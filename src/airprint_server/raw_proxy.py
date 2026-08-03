"""Raw TCP/JetDirect listeners that submit unchanged data to managed CUPS queues."""

from __future__ import annotations

import ipaddress
import logging
import os
import signal
import socket
import socketserver
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from airprint_server.commands import Runner
from airprint_server.config import CONFIG_DIR, State
from airprint_server.validation import ValidationError, port, queue_name

DEFAULT_RAW_PORT = 9100
MAX_RAW_PORT = 65535
MAX_JOB_BYTES = 32 * 1024 * 1024
IDLE_TIMEOUT_SECONDS = 60.0
SubmitRawJob = Callable[[str, Path], None]
RAW_PROXY_CONFIG_PATH = CONFIG_DIR / "raw-proxy.yaml"
RAW_PROXY_SERVICE_PATH = Path("/etc/systemd/system/airprint-server-raw.service")
RAW_PROXY_SERVICE_NAME = "airprint-server-raw.service"
RAW_PROXY_SERVICE = f"""[Unit]
Description=airprint-server raw TCP/JetDirect gateway
After=network-online.target cups.service
Wants=network-online.target
Requires=cups.service

[Service]
Type=simple
User=lp
Group=lp
ExecStart=/usr/local/bin/airprint-server serve-raw --config {RAW_PROXY_CONFIG_PATH}
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
RestrictAddressFamilies=AF_UNIX AF_INET
CapabilityBoundingSet=
LockPersonality=true
MemoryDenyWriteExecute=true
RestrictRealtime=true
RestrictSUIDSGID=true
SystemCallArchitectures=native
TasksMax=32
UMask=0077

[Install]
WantedBy=multi-user.target
"""
LOG = logging.getLogger("airprint-server.raw-proxy")


class JobTooLargeError(RuntimeError):
    """A raw client exceeded the per-connection spool limit."""


@dataclass(frozen=True, order=True)
class RawRoute:
    port: int
    queue: str


def client_address_allowed(value: str) -> bool:
    """Allow loopback, link-local, and private IPv4 clients only."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        isinstance(address, ipaddress.IPv4Address)
        and (address.is_loopback or address.is_link_local or address.is_private)
    )


def _write_text(path: Path, content: str, *, mode: int = 0o644) -> None:
    if path.is_symlink():
        raise RuntimeError(f"refusing to replace symbolic link: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configured_routes(state: State) -> list[RawRoute]:
    """Return deterministic listener routes for explicitly exposed queues."""
    return sorted(
        RawRoute(printer.raw_port, printer.name)
        for printer in state.printers.values()
        if printer.raw_port is not None
    )


def next_available_port(state: State, *, start: int = DEFAULT_RAW_PORT) -> int:
    used = {route.port for route in configured_routes(state)}
    for candidate in range(start, MAX_RAW_PORT + 1):
        if candidate not in used:
            return candidate
    raise RuntimeError(f"no raw TCP port is available from {start} to {MAX_RAW_PORT}")


def _route_document(state: State) -> dict[str, object]:
    return {
        "version": 1,
        "listeners": [
            {"port": route.port, "queue": route.queue} for route in configured_routes(state)
        ],
    }


def write_routes(state: State, path: Path = RAW_PROXY_CONFIG_PATH) -> None:
    content = yaml.safe_dump(_route_document(state), sort_keys=False)
    _write_text(path, content)


def load_routes(path: Path = RAW_PROXY_CONFIG_PATH) -> list[RawRoute]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"cannot read raw proxy configuration {path}: {exc}") from exc
    listeners = document.get("listeners") if isinstance(document, dict) else None
    if not isinstance(listeners, list):
        raise ValidationError("raw proxy configuration must contain a listeners list")
    routes: list[RawRoute] = []
    used: set[int] = set()
    for listener in listeners:
        if not isinstance(listener, dict):
            raise ValidationError("raw proxy listener must be a mapping")
        raw_port = listener.get("port")
        if not isinstance(raw_port, (int, str)) or isinstance(raw_port, bool):
            raise ValidationError("raw proxy listener port must be an integer")
        number = port(raw_port)
        queue = queue_name(str(listener.get("queue", "")))
        if number in used:
            raise ValidationError(f"duplicate raw TCP port: {number}")
        used.add(number)
        routes.append(RawRoute(number, queue))
    return sorted(routes)


def reconcile_service(
    runner: Runner,
    state: State,
    *,
    service_path: Path = RAW_PROXY_SERVICE_PATH,
    config_path: Path = RAW_PROXY_CONFIG_PATH,
) -> None:
    if not state.raw_proxy_service_managed:
        raise RuntimeError("raw proxy service is not managed by airprint-server")
    write_routes(state, config_path)
    if configured_routes(state):
        runner.run(["systemctl", "enable", RAW_PROXY_SERVICE_NAME])
        runner.run(["systemctl", "restart", RAW_PROXY_SERVICE_NAME])
    else:
        runner.run(["systemctl", "disable", "--now", RAW_PROXY_SERVICE_NAME], check=False)


def install_service(
    runner: Runner,
    state: State,
    *,
    service_path: Path = RAW_PROXY_SERVICE_PATH,
    config_path: Path = RAW_PROXY_CONFIG_PATH,
) -> None:
    if (service_path.exists() or service_path.is_symlink()) and not state.raw_proxy_service_managed:
        raise RuntimeError(f"refusing to replace unmanaged system service: {service_path}")
    _write_text(service_path, RAW_PROXY_SERVICE)
    state.raw_proxy_service_managed = True
    runner.run(["systemctl", "daemon-reload"])
    reconcile_service(runner, state, service_path=service_path, config_path=config_path)


def remove_service(
    runner: Runner,
    state: State,
    *,
    service_path: Path = RAW_PROXY_SERVICE_PATH,
    config_path: Path = RAW_PROXY_CONFIG_PATH,
) -> None:
    if not state.raw_proxy_service_managed:
        return
    runner.run(["systemctl", "disable", "--now", RAW_PROXY_SERVICE_NAME], check=False)
    if runner.dry_run:
        return
    for path in (service_path, config_path):
        if path.is_symlink():
            raise RuntimeError(f"refusing to remove symbolic link: {path}")
        path.unlink(missing_ok=True)
    state.raw_proxy_service_managed = False
    runner.run(["systemctl", "daemon-reload"])


def receive_raw_job(
    source: socket.socket,
    queue: str,
    submit: SubmitRawJob,
    *,
    temporary_dir: Path | None = None,
    max_bytes: int = MAX_JOB_BYTES,
    idle_timeout: float = IDLE_TIMEOUT_SECONDS,
) -> int:
    """Treat one TCP connection as one bounded, byte-for-byte print job."""
    checked_queue = queue_name(queue)
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    source.settimeout(idle_timeout)
    fd, temporary = tempfile.mkstemp(
        prefix="airprint-server-raw-",
        dir=str(temporary_dir) if temporary_dir else None,
    )
    path = Path(temporary)
    received = 0
    try:
        with os.fdopen(fd, "wb") as output:
            while True:
                try:
                    chunk = source.recv(64 * 1024)
                except TimeoutError:
                    break
                if not chunk:
                    break
                received += len(chunk)
                if received > max_bytes:
                    raise JobTooLargeError(f"raw print job exceeds {max_bytes} bytes")
                output.write(chunk)
        if received:
            submit(checked_queue, path)
        return received
    finally:
        path.unlink(missing_ok=True)


def submit_raw_job(queue: str, path: Path) -> None:
    result = subprocess.run(
        ["lp", "-d", queue_name(queue), "-o", "raw", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "lp failed"
        raise RuntimeError(f"CUPS rejected raw job for {queue}: {detail}")


class _RawTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 8

    def __init__(
        self,
        address: tuple[str, int],
        route: RawRoute,
        job_slots: threading.BoundedSemaphore,
    ) -> None:
        self.route = route
        self.job_slots = job_slots
        super().__init__(address, _RawRequestHandler)


class _RawRequestHandler(socketserver.BaseRequestHandler):
    server: _RawTCPServer

    def handle(self) -> None:
        client = str(self.client_address[0])
        if not client_address_allowed(client):
            LOG.warning("rejecting raw connection from non-private address %s", client)
            return
        if not self.server.job_slots.acquire(blocking=False):
            LOG.warning("rejecting raw connection: concurrent job limit reached")
            return
        try:
            receive_raw_job(self.request, self.server.route.queue, submit_raw_job)
        except (JobTooLargeError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            LOG.error("raw job for %s failed: %s", self.server.route.queue, exc)
        finally:
            self.server.job_slots.release()


def serve(routes: list[RawRoute], *, bind: str = "0.0.0.0") -> None:
    if not routes:
        raise RuntimeError("raw proxy has no configured listeners")
    servers: list[_RawTCPServer] = []
    started_servers: list[_RawTCPServer] = []
    threads: list[threading.Thread] = []
    stopped = threading.Event()
    job_slots = threading.BoundedSemaphore(4)

    def stop(_signum: int, _frame: object) -> None:
        stopped.set()

    previous_term = signal.signal(signal.SIGTERM, stop)
    previous_int = signal.signal(signal.SIGINT, stop)
    try:
        for route in routes:
            server = _RawTCPServer((bind, route.port), route, job_slots)
            servers.append(server)
        for server in servers:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            started_servers.append(server)
            threads.append(thread)
            LOG.info(
                "listening on %s:%d for CUPS queue %s",
                bind,
                server.route.port,
                server.route.queue,
            )
        stopped.wait()
    finally:
        for server in started_servers:
            server.shutdown()
        for server in servers:
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


def serve_config(path: Path = RAW_PROXY_CONFIG_PATH) -> None:
    serve(load_routes(path))
