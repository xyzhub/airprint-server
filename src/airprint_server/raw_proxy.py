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
from airprint_server.config import CONFIG_DIR, STATE_DIR, State, atomic_write_yaml
from airprint_server.validation import (
    ValidationError,
    network_interface,
    port,
    queue_name,
    virtual_ipv4,
)

DEFAULT_RAW_PORT = 9100
MAX_RAW_PORT = 65535
MAX_JOB_BYTES = 32 * 1024 * 1024
IDLE_TIMEOUT_SECONDS = 60.0
SubmitRawJob = Callable[[str, Path], None]
RAW_PROXY_CONFIG_PATH = CONFIG_DIR / "raw-proxy.yaml"
RAW_PROXY_SERVICE_PATH = Path("/etc/systemd/system/airprint-server-raw.service")
RAW_PROXY_SERVICE_NAME = "airprint-server-raw.service"
RAW_ADDRESSES_APPLIED_PATH = STATE_DIR / "raw-addresses.yaml"
RAW_ADDRESS_SERVICE_PATH = Path("/etc/systemd/system/airprint-server-addresses.service")
RAW_ADDRESS_SERVICE_NAME = "airprint-server-addresses.service"
RAW_ADDRESS_EXEC = (
    "/usr/local/bin/airprint-server apply-raw-addresses "
    f"--config {RAW_PROXY_CONFIG_PATH} --applied-state {RAW_ADDRESSES_APPLIED_PATH}"
)
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
RAW_ADDRESS_SERVICE = f"""[Unit]
Description=airprint-server managed virtual printer addresses
After=network-online.target
Wants=network-online.target
Before={RAW_PROXY_SERVICE_NAME}

[Service]
Type=oneshot
ExecStart={RAW_ADDRESS_EXEC}
RemainAfterExit=yes
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
ReadWritePaths={STATE_DIR}
RestrictAddressFamilies=AF_UNIX AF_NETLINK AF_PACKET
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW
LockPersonality=true
MemoryDenyWriteExecute=true
RestrictRealtime=true
RestrictSUIDSGID=true
SystemCallArchitectures=native
TasksMax=16
UMask=0077

[Install]
WantedBy=multi-user.target
"""
LOG = logging.getLogger("airprint-server.raw-proxy")


class JobTooLargeError(RuntimeError):
    """A raw client exceeded the per-connection spool limit."""


@dataclass(frozen=True)
class RawRoute:
    port: int
    queue: str
    address: str = "0.0.0.0"
    interface: str | None = None


@dataclass(frozen=True, order=True)
class VirtualAddress:
    address: str
    interface: str


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
    routes = [
        RawRoute(
            printer.raw_port,
            printer.name,
            printer.raw_address or "0.0.0.0",
            printer.raw_interface,
        )
        for printer in state.printers.values()
        if printer.raw_port is not None
    ]
    return sorted(routes, key=lambda route: (route.address, route.port, route.queue))


def next_available_port(state: State, *, start: int = DEFAULT_RAW_PORT) -> int:
    used = {route.port for route in configured_routes(state)}
    for candidate in range(start, MAX_RAW_PORT + 1):
        if candidate not in used:
            return candidate
    raise RuntimeError(f"no raw TCP port is available from {start} to {MAX_RAW_PORT}")


def _route_document(state: State) -> dict[str, object]:
    listeners: list[dict[str, object]] = []
    for route in configured_routes(state):
        listener: dict[str, object] = {"port": route.port, "queue": route.queue}
        if route.address != "0.0.0.0":
            listener["address"] = route.address
            listener["interface"] = route.interface
        listeners.append(listener)
    return {
        "version": 2,
        "listeners": listeners,
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
    used: list[tuple[str, int]] = []
    for listener in listeners:
        if not isinstance(listener, dict):
            raise ValidationError("raw proxy listener must be a mapping")
        raw_port = listener.get("port")
        if not isinstance(raw_port, (int, str)) or isinstance(raw_port, bool):
            raise ValidationError("raw proxy listener port must be an integer")
        number = port(raw_port)
        queue = queue_name(str(listener.get("queue", "")))
        raw_address = listener.get("address")
        address = virtual_ipv4(str(raw_address)) if raw_address else "0.0.0.0"
        raw_interface = listener.get("interface")
        interface = network_interface(str(raw_interface)) if raw_interface else None
        if address != "0.0.0.0" and interface is None:
            raise ValidationError(f"raw TCP listener {address}:{number} needs an interface")
        if any(
            other_port == number
            and (address == "0.0.0.0" or other == "0.0.0.0" or address == other)
            for other, other_port in used
        ):
            raise ValidationError(f"duplicate raw TCP listener: {address}:{number}")
        used.append((address, number))
        routes.append(RawRoute(number, queue, address, interface))
    return sorted(routes, key=lambda route: (route.address, route.port, route.queue))


def resolve_virtual_interface(runner: Runner, address: str) -> str:
    target = ipaddress.ip_address(virtual_ipv4(address))
    result = runner.run(
        ["ip", "-o", "-4", "address", "show", "scope", "global"],
        check=False,
    )
    candidates: list[tuple[int, str]] = []
    if not result.returncode:
        for line in result.stdout.splitlines():
            fields = line.split()
            if "inet" not in fields:
                continue
            inet_index = fields.index("inet")
            if inet_index < 2 or inet_index + 1 >= len(fields):
                continue
            interface = fields[1].split("@", 1)[0]
            try:
                checked_interface = network_interface(interface)
                local = ipaddress.ip_interface(fields[inet_index + 1])
            except (ValidationError, ValueError):
                continue
            reserved = {
                local.ip,
                local.network.network_address,
                local.network.broadcast_address,
            }
            if (
                target in local.network
                and target not in reserved
                and local.network.prefixlen < 32
            ):
                candidates.append((local.network.prefixlen, checked_interface))
    if not candidates:
        raise ValidationError(
            f"{address} is not on a connected private IPv4 LAN; choose an unused address "
            "from the Pi's current subnet"
        )
    return max(candidates, key=lambda item: (item[0], item[1]))[1]


def virtual_addresses(routes: list[RawRoute]) -> list[VirtualAddress]:
    return sorted(
        {
            VirtualAddress(route.address, network_interface(route.interface or ""))
            for route in routes
            if route.address != "0.0.0.0"
        }
    )


def _load_applied_addresses(path: Path) -> list[VirtualAddress]:
    if not path.exists():
        return []
    if path.is_symlink():
        raise RuntimeError(f"applied virtual-address state may not be a symbolic link: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"cannot read applied virtual-address state {path}: {exc}") from exc
    addresses = document.get("addresses") if isinstance(document, dict) else None
    if not isinstance(addresses, list):
        raise ValidationError("applied virtual-address state must contain an addresses list")
    return sorted(
        VirtualAddress(
            virtual_ipv4(str(item.get("address", ""))),
            network_interface(str(item.get("interface", ""))),
        )
        for item in addresses
        if isinstance(item, dict)
    )


def _interface_addresses(runner: Runner, interface: str) -> set[str]:
    result = runner.run(
        ["ip", "-o", "-4", "address", "show", "dev", network_interface(interface)],
        check=False,
    )
    addresses: set[str] = set()
    if result.returncode:
        return addresses
    for line in result.stdout.splitlines():
        fields = line.split()
        if "inet" not in fields:
            continue
        index = fields.index("inet")
        if index + 1 < len(fields):
            try:
                addresses.add(str(ipaddress.ip_interface(fields[index + 1]).ip))
            except ValueError:
                continue
    return addresses


def validate_virtual_address_available(
    runner: Runner,
    address: str,
    interface: str,
) -> None:
    checked_address = virtual_ipv4(address)
    checked_interface = network_interface(interface)
    if checked_address in _interface_addresses(runner, checked_interface):
        raise RuntimeError(
            f"refusing to claim {checked_address}: it is already configured on "
            f"{checked_interface}"
        )
    probe = runner.run(
        [
            "arping",
            "-D",
            "-c",
            "2",
            "-w",
            "3",
            "-I",
            checked_interface,
            checked_address,
        ],
        check=False,
    )
    if probe.returncode:
        raise RuntimeError(
            f"refusing to claim {checked_address}: another LAN device may already use it"
        )


def apply_virtual_addresses(
    runner: Runner,
    routes: list[RawRoute],
    *,
    applied_path: Path = RAW_ADDRESSES_APPLIED_PATH,
) -> None:
    desired = virtual_addresses(routes)
    applied = _load_applied_addresses(applied_path)
    for old in sorted(set(applied) - set(desired)):
        runner.run(
            ["ip", "address", "del", f"{old.address}/32", "dev", old.interface],
            check=False,
        )
    for item in desired:
        if item.address in _interface_addresses(runner, item.interface):
            if item in applied:
                continue
            raise RuntimeError(
                f"refusing to claim {item.address}: it is already configured on {item.interface}"
            )
        validate_virtual_address_available(runner, item.address, item.interface)
        runner.run(["ip", "address", "add", f"{item.address}/32", "dev", item.interface])
    if not runner.dry_run:
        atomic_write_yaml(
            applied_path,
            {
                "version": 1,
                "addresses": [
                    {"address": item.address, "interface": item.interface} for item in desired
                ],
            },
            mode=0o600,
        )


def apply_configured_virtual_addresses(
    runner: Runner,
    *,
    config_path: Path = RAW_PROXY_CONFIG_PATH,
    applied_path: Path = RAW_ADDRESSES_APPLIED_PATH,
) -> None:
    apply_virtual_addresses(runner, load_routes(config_path), applied_path=applied_path)


def _companion_path(selected: Path, default: Path, filename: str) -> Path:
    return default if selected == RAW_PROXY_SERVICE_PATH else selected.with_name(filename)


def _managed_unit(path: Path, content: str, *, already_managed: bool) -> None:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"refusing to replace unmanaged system service: {path}")
        if not already_managed:
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise RuntimeError(f"cannot inspect existing system service: {path}") from exc
            if existing != content:
                raise RuntimeError(f"refusing to replace unmanaged system service: {path}")
    _write_text(path, content)


def reconcile_service(
    runner: Runner,
    state: State,
    *,
    service_path: Path = RAW_PROXY_SERVICE_PATH,
    config_path: Path = RAW_PROXY_CONFIG_PATH,
) -> None:
    if not state.raw_proxy_service_managed or not state.raw_address_service_managed:
        raise RuntimeError("raw proxy services are not fully managed by airprint-server")
    write_routes(state, config_path)
    runner.run(["systemctl", "enable", RAW_ADDRESS_SERVICE_NAME])
    runner.run(["systemctl", "restart", RAW_ADDRESS_SERVICE_NAME])
    if not virtual_addresses(configured_routes(state)):
        runner.run(["systemctl", "disable", "--now", RAW_ADDRESS_SERVICE_NAME], check=False)
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
    address_service_path: Path | None = None,
    config_path: Path = RAW_PROXY_CONFIG_PATH,
) -> None:
    selected_address_service = address_service_path or _companion_path(
        service_path,
        RAW_ADDRESS_SERVICE_PATH,
        RAW_ADDRESS_SERVICE_PATH.name,
    )
    if selected_address_service == RAW_ADDRESS_SERVICE_PATH:
        RAW_ADDRESSES_APPLIED_PATH.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(RAW_ADDRESSES_APPLIED_PATH.parent, 0o750)
    _managed_unit(
        service_path,
        RAW_PROXY_SERVICE,
        already_managed=state.raw_proxy_service_managed,
    )
    _managed_unit(
        selected_address_service,
        RAW_ADDRESS_SERVICE,
        already_managed=state.raw_address_service_managed,
    )
    state.raw_proxy_service_managed = True
    state.raw_address_service_managed = True
    runner.run(["systemctl", "daemon-reload"])
    reconcile_service(runner, state, service_path=service_path, config_path=config_path)


def remove_service(
    runner: Runner,
    state: State,
    *,
    service_path: Path = RAW_PROXY_SERVICE_PATH,
    address_service_path: Path | None = None,
    config_path: Path = RAW_PROXY_CONFIG_PATH,
    applied_path: Path | None = None,
) -> None:
    if not state.raw_proxy_service_managed and not state.raw_address_service_managed:
        return
    selected_address_service = address_service_path or _companion_path(
        service_path,
        RAW_ADDRESS_SERVICE_PATH,
        RAW_ADDRESS_SERVICE_PATH.name,
    )
    selected_applied_path = applied_path or (
        RAW_ADDRESSES_APPLIED_PATH
        if config_path == RAW_PROXY_CONFIG_PATH
        else config_path.with_name("raw-addresses-applied.yaml")
    )
    if state.raw_proxy_service_managed:
        runner.run(["systemctl", "disable", "--now", RAW_PROXY_SERVICE_NAME], check=False)
    if state.raw_address_service_managed:
        apply_virtual_addresses(runner, [], applied_path=selected_applied_path)
        runner.run(["systemctl", "disable", "--now", RAW_ADDRESS_SERVICE_NAME], check=False)
    if runner.dry_run:
        return
    managed_paths = [config_path]
    if state.raw_proxy_service_managed:
        managed_paths.append(service_path)
    if state.raw_address_service_managed:
        managed_paths.extend((selected_address_service, selected_applied_path))
    for path in managed_paths:
        if path.is_symlink():
            raise RuntimeError(f"refusing to remove symbolic link: {path}")
        path.unlink(missing_ok=True)
    state.raw_proxy_service_managed = False
    state.raw_address_service_managed = False
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


def serve(routes: list[RawRoute], *, bind: str | None = None) -> None:
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
            server = _RawTCPServer((bind or route.address, route.port), route, job_slots)
            servers.append(server)
        for server in servers:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            started_servers.append(server)
            threads.append(thread)
            LOG.info(
                "listening on %s:%d for CUPS queue %s",
                server.server_address[0],
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
