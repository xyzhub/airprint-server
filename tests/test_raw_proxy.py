from __future__ import annotations

import socket
import threading
from pathlib import Path

import pytest
import yaml
from conftest import FakeRunner

from airprint_server import raw_proxy
from airprint_server.commands import CommandResult
from airprint_server.config import ManagedPrinter, State
from airprint_server.raw_proxy import (
    JobTooLargeError,
    RawRoute,
    apply_virtual_addresses,
    client_address_allowed,
    configured_routes,
    find_available_virtual_address,
    install_service,
    load_routes,
    next_available_port,
    receive_raw_job,
    reconcile_service,
    remove_service,
    resolve_virtual_interface,
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


def test_routes_can_share_port_9100_on_distinct_virtual_addresses(tmp_path: Path) -> None:
    state = State(
        printers={
            "Receipt": ManagedPrinter(
                "Receipt", "Receipt", None, "usb://V/R", "usb", raw_port=9100,
                raw_address="192.168.1.240", raw_interface="wlan0",
            ),
            "Labels": ManagedPrinter(
                "Labels", "Labels", None, "usb://V/L", "usb", raw_port=9100,
                raw_address="192.168.1.241", raw_interface="wlan0",
            ),
        }
    )
    config_path = tmp_path / "raw-proxy.yaml"

    raw_proxy.write_routes(state, config_path)

    assert load_routes(config_path) == [
        RawRoute(9100, "Receipt", "192.168.1.240", "wlan0"),
        RawRoute(9100, "Labels", "192.168.1.241", "wlan0"),
    ]


def test_resolves_virtual_address_to_connected_lan_interface() -> None:
    command = ("ip", "-o", "-4", "address", "show", "scope", "global")
    runner = FakeRunner(
        {
            command: CommandResult(
                command,
                0,
                "2: wlan0    inet 192.168.1.20/24 brd 192.168.1.255 scope global wlan0\n",
            )
        }
    )

    assert resolve_virtual_interface(runner, "192.168.1.240") == "wlan0"  # type: ignore[arg-type]


def test_finds_an_available_virtual_address_on_the_connected_lan() -> None:
    addresses = ("ip", "-o", "-4", "address", "show", "scope", "global")
    show = ("ip", "-o", "-4", "address", "show", "dev", "wlan0")
    first_probe = (
        "arping",
        "-D",
        "-c",
        "2",
        "-w",
        "3",
        "-I",
        "wlan0",
        "192.168.1.240",
    )
    second_probe = (*first_probe[:-1], "192.168.1.241")
    runner = FakeRunner(
        {
            addresses: CommandResult(
                addresses,
                0,
                "2: wlan0 inet 192.168.1.20/24 brd 192.168.1.255 scope global wlan0\n",
            ),
            show: CommandResult(
                show,
                0,
                "2: wlan0 inet 192.168.1.20/24 scope global wlan0\n",
            ),
            first_probe: CommandResult(first_probe, 1, "", "duplicate detected"),
            second_probe: CommandResult(second_probe, 0),
        }
    )

    selected = find_available_virtual_address(runner)  # type: ignore[arg-type]

    assert selected == raw_proxy.VirtualAddress("192.168.1.241", "wlan0")
    assert first_probe in runner.calls
    assert second_probe in runner.calls


def test_available_virtual_address_skips_addresses_managed_by_another_queue() -> None:
    addresses = ("ip", "-o", "-4", "address", "show", "scope", "global")
    show = ("ip", "-o", "-4", "address", "show", "dev", "wlan0")
    probe = (
        "arping",
        "-D",
        "-c",
        "2",
        "-w",
        "3",
        "-I",
        "wlan0",
        "192.168.1.241",
    )
    runner = FakeRunner(
        {
            addresses: CommandResult(
                addresses,
                0,
                "2: wlan0 inet 192.168.1.20/24 scope global wlan0\n",
            ),
            show: CommandResult(show, 0, "2: wlan0 inet 192.168.1.20/24 scope global\n"),
            probe: CommandResult(probe, 0),
        }
    )

    selected = find_available_virtual_address(  # type: ignore[arg-type]
        runner,
        excluded={"192.168.1.240"},
    )

    assert selected.address == "192.168.1.241"
    assert not any(call[-1] == "192.168.1.240" for call in runner.calls if call[0] == "arping")


def test_available_virtual_address_avoids_dot_zero_and_dot_255_on_wide_subnets() -> None:
    addresses = ("ip", "-o", "-4", "address", "show", "scope", "global")
    show = ("ip", "-o", "-4", "address", "show", "dev", "wlan0")
    responses = {
        addresses: CommandResult(
            addresses,
            0,
            "2: wlan0 inet 10.20.30.20/16 scope global wlan0\n",
        ),
        show: CommandResult(show, 0, "2: wlan0 inet 10.20.30.20/16 scope global\n"),
    }
    for suffix in range(240, 255):
        probe = (
            "arping",
            "-D",
            "-c",
            "2",
            "-w",
            "3",
            "-I",
            "wlan0",
            f"10.20.30.{suffix}",
        )
        responses[probe] = CommandResult(probe, 1, "", "duplicate detected")
    runner = FakeRunner(responses)

    selected = find_available_virtual_address(runner)  # type: ignore[arg-type]

    assert selected.address == "10.20.30.239"


def test_reconciles_only_tracked_virtual_addresses(tmp_path: Path) -> None:
    applied_path = tmp_path / "raw-addresses.yaml"
    applied_path.write_text(
        "version: 1\naddresses:\n  - address: 192.168.1.239\n    interface: wlan0\n",
        encoding="utf-8",
    )
    show = ("ip", "-o", "-4", "address", "show", "dev", "wlan0")
    probe = ("arping", "-D", "-c", "2", "-w", "3", "-I", "wlan0", "192.168.1.240")
    runner = FakeRunner(
        {
            show: CommandResult(
                show,
                0,
                "2: wlan0 inet 192.168.1.20/24 scope global wlan0\n",
            ),
            probe: CommandResult(probe, 0),
        }
    )

    apply_virtual_addresses(
        runner,  # type: ignore[arg-type]
        [RawRoute(9100, "Receipt", "192.168.1.240", "wlan0")],
        applied_path=applied_path,
    )

    assert ("ip", "address", "del", "192.168.1.239/32", "dev", "wlan0") in runner.calls
    assert ("ip", "address", "add", "192.168.1.240/32", "dev", "wlan0") in runner.calls
    assert yaml.safe_load(applied_path.read_text())["addresses"] == [
        {"address": "192.168.1.240", "interface": "wlan0"}
    ]


def test_refuses_virtual_address_when_arp_duplicate_detection_fails(
    tmp_path: Path,
) -> None:
    show = ("ip", "-o", "-4", "address", "show", "dev", "wlan0")
    probe = ("arping", "-D", "-c", "2", "-w", "3", "-I", "wlan0", "192.168.1.240")
    runner = FakeRunner(
        {
            show: CommandResult(show, 0, "2: wlan0 inet 192.168.1.20/24 scope global\n"),
            probe: CommandResult(probe, 1, "", "duplicate detected"),
        }
    )

    with pytest.raises(RuntimeError, match="another LAN device"):
        apply_virtual_addresses(
            runner,  # type: ignore[arg-type]
            [RawRoute(9100, "Receipt", "192.168.1.240", "wlan0")],
            applied_path=tmp_path / "applied.yaml",
        )

    assert not any(call[:3] == ("ip", "address", "add") for call in runner.calls)


def test_reapplies_tracked_virtual_address_after_reboot(tmp_path: Path) -> None:
    applied_path = tmp_path / "raw-addresses.yaml"
    applied_path.write_text(
        "version: 1\naddresses:\n  - address: 192.168.1.240\n    interface: wlan0\n",
        encoding="utf-8",
    )
    show = ("ip", "-o", "-4", "address", "show", "dev", "wlan0")
    probe = ("arping", "-D", "-c", "2", "-w", "3", "-I", "wlan0", "192.168.1.240")
    runner = FakeRunner(
        {
            show: CommandResult(show, 0, "2: wlan0 inet 192.168.1.20/24 scope global\n"),
            probe: CommandResult(probe, 0),
        }
    )

    apply_virtual_addresses(
        runner,  # type: ignore[arg-type]
        [RawRoute(9100, "Receipt", "192.168.1.240", "wlan0")],
        applied_path=applied_path,
    )

    assert ("ip", "address", "add", "192.168.1.240/32", "dev", "wlan0") in runner.calls


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
    assert state.raw_address_service_managed
    assert ("systemctl", "enable", "airprint-server-raw.service") in runner.calls
    assert ("systemctl", "restart", "airprint-server-raw.service") in runner.calls


def test_installs_hardened_root_address_service(tmp_path: Path) -> None:
    service_path = tmp_path / "airprint-server-raw.service"
    address_service_path = tmp_path / "airprint-server-addresses.service"
    config_path = tmp_path / "raw-proxy.yaml"
    state = State()

    install_service(
        FakeRunner(),  # type: ignore[arg-type]
        state,
        service_path=service_path,
        address_service_path=address_service_path,
        config_path=config_path,
    )

    unit = address_service_path.read_text()
    assert "apply-raw-addresses" in unit
    assert "CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW" in unit
    assert "RestrictAddressFamilies=AF_UNIX AF_NETLINK AF_PACKET" in unit
    assert "NoNewPrivileges=true" in unit


def test_install_adopts_an_identical_service_when_management_state_was_lost(
    tmp_path: Path,
) -> None:
    service_path = tmp_path / "airprint-server-raw.service"
    config_path = tmp_path / "raw-proxy.yaml"
    service_path.write_text(raw_proxy.RAW_PROXY_SERVICE, encoding="utf-8")
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

    assert state.raw_proxy_service_managed
    assert load_routes(config_path) == configured_routes(state)


def test_install_refuses_to_adopt_a_different_unmanaged_service(tmp_path: Path) -> None:
    service_path = tmp_path / "airprint-server-raw.service"
    service_path.write_text("[Service]\nExecStart=/unknown\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unmanaged system service"):
        install_service(
            FakeRunner(),  # type: ignore[arg-type]
            State(),
            service_path=service_path,
            config_path=tmp_path / "raw-proxy.yaml",
        )


def test_upgrade_does_not_overwrite_unmanaged_address_service(tmp_path: Path) -> None:
    service_path = tmp_path / "airprint-server-raw.service"
    address_service_path = tmp_path / "airprint-server-addresses.service"
    service_path.write_text(raw_proxy.RAW_PROXY_SERVICE, encoding="utf-8")
    address_service_path.write_text("[Service]\nExecStart=/unknown\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unmanaged system service"):
        install_service(
            FakeRunner(),  # type: ignore[arg-type]
            State(raw_proxy_service_managed=True),
            service_path=service_path,
            address_service_path=address_service_path,
            config_path=tmp_path / "raw-proxy.yaml",
        )


def test_reconcile_stops_listener_when_no_ports_remain(tmp_path: Path) -> None:
    service_path = tmp_path / "airprint-server-raw.service"
    config_path = tmp_path / "raw-proxy.yaml"
    state = State(raw_proxy_service_managed=True, raw_address_service_managed=True)
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
