from __future__ import annotations

import socket
import threading
from pathlib import Path

import pytest
import yaml
from conftest import FakeRunner

from airprint_server import raw_proxy
from airprint_server.config import ManagedPrinter, State
from airprint_server.raw_proxy import (
    JobTooLargeError,
    client_address_allowed,
    configured_routes,
    install_service,
    load_routes,
    next_available_port,
    receive_raw_job,
    reconcile_service,
    remove_service,
)


@pytest.mark.parametrize("address", ["127.0.0.1", "192.168.1.20", "10.0.0.4", "169.254.1.2"])
def test_raw_proxy_accepts_only_local_or_private_ipv4_clients(address: str) -> None:
    assert client_address_allowed(address)


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "not-an-address"])
def test_raw_proxy_rejects_public_or_invalid_client_addresses(address: str) -> None:
    assert not client_address_allowed(address)


def test_receives_one_tcp_connection_as_one_unchanged_raw_job(tmp_path: Path) -> None:
    receiver, client = socket.socketpair()
    payload = b"\x1b@receipt text\n\x1dV\x01"
    submitted: list[tuple[str, bytes]] = []

    client.sendall(payload)
    client.shutdown(socket.SHUT_WR)
    try:
        size = receive_raw_job(
            receiver,
            "Receipt",
            lambda queue, path: submitted.append((queue, path.read_bytes())),
            temporary_dir=tmp_path,
            max_bytes=1024,
        )
    finally:
        receiver.close()
        client.close()

    assert size == len(payload)
    assert submitted == [("Receipt", payload)]
    assert list(tmp_path.iterdir()) == []


def test_rejects_raw_job_before_it_can_fill_storage(tmp_path: Path) -> None:
    receiver, client = socket.socketpair()
    client.sendall(b"123456789")
    client.shutdown(socket.SHUT_WR)
    try:
        with pytest.raises(JobTooLargeError, match="8 bytes"):
            receive_raw_job(
                receiver,
                "Receipt",
                lambda _queue, _path: None,
                temporary_dir=tmp_path,
                max_bytes=8,
            )
    finally:
        receiver.close()
        client.close()

    assert list(tmp_path.iterdir()) == []


def test_tcp_listener_routes_private_client_bytes_to_selected_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted: list[tuple[str, bytes]] = []
    completed = threading.Event()

    def submit(queue: str, path: Path) -> None:
        submitted.append((queue, path.read_bytes()))
        completed.set()

    monkeypatch.setattr(raw_proxy, "submit_raw_job", submit)
    route = raw_proxy.RawRoute(0, "Receipt")
    server = raw_proxy._RawTCPServer(  # type: ignore[attr-defined]
        ("127.0.0.1", 0), route, threading.BoundedSemaphore(4)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(server.server_address, timeout=2) as client:
            client.sendall(b"printer-ready-data")
        assert completed.wait(2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert submitted == [("Receipt", b"printer-ready-data")]


def test_routes_only_managed_printers_with_explicit_unique_ports() -> None:
    state = State(
        printers={
            "Receipt": ManagedPrinter(
                "Receipt",
                "Receipt",
                None,
                "usb://Vendor/Receipt",
                "usb",
                raw_port=9100,
            ),
            "Labels": ManagedPrinter(
                "Labels",
                "Labels",
                None,
                "usb://Vendor/Labels",
                "usb",
                raw_port=9101,
            ),
            "AirPrintOnly": ManagedPrinter(
                "AirPrintOnly",
                "AirPrint Only",
                None,
                "ipp://printer/ipp/print",
                "ipp",
            ),
        }
    )

    assert [(route.port, route.queue) for route in configured_routes(state)] == [
        (9100, "Receipt"),
        (9101, "Labels"),
    ]
    assert next_available_port(state) == 9102


def test_managed_service_is_hardened_and_activated_for_configured_routes(
    tmp_path: Path,
) -> None:
    service_path = tmp_path / "airprint-server-raw.service"
    config_path = tmp_path / "raw-proxy.yaml"
    state = State(
        printers={
            "Receipt": ManagedPrinter(
                "Receipt",
                "Receipt",
                None,
                "usb://Vendor/Receipt",
                "usb",
                raw_port=9100,
            )
        }
    )
    runner = FakeRunner()

    install_service(
        runner,  # type: ignore[arg-type]
        state,
        service_path=service_path,
        config_path=config_path,
    )

    unit = service_path.read_text()
    assert "User=lp" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "airprint-server serve-raw" in unit
    assert yaml.safe_load(config_path.read_text())["listeners"] == [
        {"port": 9100, "queue": "Receipt"}
    ]
    assert load_routes(config_path) == configured_routes(state)
    assert state.raw_proxy_service_managed
    assert ("systemctl", "enable", "airprint-server-raw.service") in runner.calls
    assert ("systemctl", "restart", "airprint-server-raw.service") in runner.calls


def test_reconcile_stops_listener_when_no_ports_remain(tmp_path: Path) -> None:
    service_path = tmp_path / "airprint-server-raw.service"
    config_path = tmp_path / "raw-proxy.yaml"
    state = State(raw_proxy_service_managed=True)
    runner = FakeRunner()

    reconcile_service(
        runner,  # type: ignore[arg-type]
        state,
        service_path=service_path,
        config_path=config_path,
    )

    assert ("systemctl", "disable", "--now", "airprint-server-raw.service") in runner.calls
    assert yaml.safe_load(config_path.read_text())["listeners"] == []


def test_uninstall_removes_only_service_recorded_as_managed(tmp_path: Path) -> None:
    service_path = tmp_path / "airprint-server-raw.service"
    config_path = tmp_path / "raw-proxy.yaml"
    service_path.write_text("managed", encoding="utf-8")
    config_path.write_text("listeners: []\n", encoding="utf-8")
    state = State(raw_proxy_service_managed=True)
    runner = FakeRunner()

    remove_service(
        runner,  # type: ignore[arg-type]
        state,
        service_path=service_path,
        config_path=config_path,
    )

    assert not service_path.exists()
    assert not config_path.exists()
    assert not state.raw_proxy_service_managed
    assert ("systemctl", "daemon-reload") in runner.calls


def test_dry_run_uninstall_keeps_raw_proxy_files_and_state(tmp_path: Path) -> None:
    service_path = tmp_path / "airprint-server-raw.service"
    config_path = tmp_path / "raw-proxy.yaml"
    service_path.write_text("managed", encoding="utf-8")
    config_path.write_text("listeners: []\n", encoding="utf-8")
    state = State(raw_proxy_service_managed=True)
    runner = FakeRunner(dry_run=True)

    remove_service(
        runner,  # type: ignore[arg-type]
        state,
        service_path=service_path,
        config_path=config_path,
    )

    assert service_path.exists()
    assert config_path.exists()
    assert state.raw_proxy_service_managed
