import pytest
from conftest import FakeRunner

from airprint_server.config import ManagedPrinter, State
from airprint_server.diagnostics import Check, diagnose


def test_diagnostic_formatting() -> None:
    assert Check(True, "CUPS running").format() == "[OK] CUPS running"
    text = Check(False, "Queue exists", "missing", "lpstat -p Queue").format()
    assert text == "[FAIL] Queue exists: missing\n       Action: lpstat -p Queue"
    assert Check(True, "ipp-usb", warning=True).format().startswith("[WARN]")


def test_diagnostics_check_configured_raw_tcp_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    printer = ManagedPrinter(
        "Receipt",
        "Receipt",
        None,
        "usb://Vendor/Receipt",
        "usb",
        raw_port=9100,
    )
    monkeypatch.setattr("airprint_server.diagnostics.operating_system", lambda: (True, "Test"))
    monkeypatch.setattr("airprint_server.diagnostics.command_exists", lambda _name: False)
    monkeypatch.setattr("airprint_server.diagnostics.cups.queue_exists", lambda *_args: False)
    monkeypatch.setattr(
        "airprint_server.diagnostics.avahi.service_active", lambda *_args: True
    )
    monkeypatch.setattr(
        "airprint_server.diagnostics.tcp_reachable", lambda *_args: (True, "connected")
    )
    monkeypatch.setattr(
        "airprint_server.diagnostics.ipp_usb_state",
        lambda _runner: {"installed": False, "active": False, "enabled": False},
    )

    checks = diagnose(FakeRunner(), State(printers={"Receipt": printer}), printer=printer)  # type: ignore[arg-type]
    by_label = {check.label: check for check in checks}

    assert by_label["Raw TCP gateway running"].ok
    assert by_label["Raw TCP listener reachable"].ok


def test_diagnostics_probe_dedicated_virtual_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    printer = ManagedPrinter(
        "Receipt",
        "Receipt",
        None,
        "usb://Vendor/Receipt",
        "usb",
        raw_port=9100,
        raw_address="192.168.1.240",
        raw_interface="wlan0",
    )
    probed: list[tuple[str, int]] = []
    monkeypatch.setattr("airprint_server.diagnostics.operating_system", lambda: (True, "Test"))
    monkeypatch.setattr("airprint_server.diagnostics.command_exists", lambda _name: False)
    monkeypatch.setattr("airprint_server.diagnostics.cups.queue_exists", lambda *_args: False)
    monkeypatch.setattr(
        "airprint_server.diagnostics.avahi.service_active", lambda *_args: True
    )
    monkeypatch.setattr(
        "airprint_server.diagnostics.tcp_reachable",
        lambda address, number: probed.append((address, number)) or (True, "connected"),
    )
    monkeypatch.setattr(
        "airprint_server.diagnostics.ipp_usb_state",
        lambda _runner: {"installed": False, "active": False, "enabled": False},
    )

    diagnose(FakeRunner(), State(printers={"Receipt": printer}), printer=printer)  # type: ignore[arg-type]

    assert probed == [("192.168.1.240", 9100)]
