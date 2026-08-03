from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest
from conftest import FakeRunner

from airprint_server.bixolon_driver import BIXOLON_CUPS_OPTIONS, BixolonInstallation
from airprint_server.cli import build_parser, cmd_add, main
from airprint_server.config import ManagedPrinter, State
from airprint_server.profiles import load_profiles
from airprint_server.xprinter_driver import XPRINTER_CUPS_OPTIONS, XPrinterInstallation


class _InteractiveInput:
    def isatty(self) -> bool:
        return True


def test_add_bixolon_uses_installed_official_driver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ppd = tmp_path / "SRPE300.ppd"
    ppd.write_text("*PPD-Adobe: \"4.3\"\n", encoding="utf-8")
    filter_path = tmp_path / "rastertoBixolon"
    filter_path.write_bytes(b"\x7fELF")
    state = State(
        vendor_drivers={
            "bixolon-pos-cups": {
                "version": "1.5.9",
                "ppd_path": str(ppd),
                "filter_path": str(filter_path),
            }
        }
    )
    configured: list[ManagedPrinter] = []
    monkeypatch.setattr("airprint_server.cli._root", lambda: None)
    monkeypatch.setattr("airprint_server.cli.cups.queue_exists", lambda *_args: False)
    monkeypatch.setattr(
        "airprint_server.cli.cups.create_or_update_queue",
        lambda _runner, printer: configured.append(printer),
    )
    monkeypatch.setattr("airprint_server.cli.save_state", lambda _state: None)
    args = argparse.Namespace(
        name="BIXOLON-SRP-E300",
        description=None,
        profile="bixolon-srp-e300",
        connection="usb",
        device_uri="usb://BIXOLON/SRP-E300?serial=00000001",
        host=None,
        port=9100,
        disable_snmp=False,
        driver=None,
        ppd=None,
        adopt=False,
        yes=True,
    )

    cmd_add(args, FakeRunner(), state, load_profiles())  # type: ignore[arg-type]

    assert configured[0].driver is None
    assert configured[0].ppd == str(ppd)
    assert configured[0].cups_options == BIXOLON_CUPS_OPTIONS


def test_install_bixolon_downloads_when_archive_is_omitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    downloaded_to: list[Path] = []
    archive = tmp_path / "driver.tgz"
    archive.write_bytes(b"archive")
    ppd = tmp_path / "SRPE300.ppd"
    filter_path = tmp_path / "rastertoBixolon"
    state = State()

    def download(destination: Path) -> Path:
        downloaded_to.append(destination)
        return archive

    monkeypatch.setattr("airprint_server.cli._root", lambda: None)
    monkeypatch.setattr("airprint_server.cli.load_state", lambda: state)
    monkeypatch.setattr("airprint_server.cli.load_profiles", lambda _path: {})
    monkeypatch.setattr("airprint_server.cli.download_bixolon_archive", download)
    monkeypatch.setattr(
        "airprint_server.cli.install_bixolon_driver",
        lambda _runner, _state, selected: BixolonInstallation(
            "1.5.9", "aarch64", ppd, filter_path
        )
        if selected == archive
        else None,
    )
    monkeypatch.setattr("airprint_server.cli.save_state", lambda _state: None)

    assert main(["install-bixolon-driver", "--yes"]) == 0
    assert len(downloaded_to) == 1
    assert downloaded_to[0].name.startswith("airprint-server-bixolon-")


def test_add_bixolon_downloads_official_driver_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ppd = tmp_path / "SRPE300.ppd"
    ppd.write_text("*PPD-Adobe: \"4.3\"\n", encoding="utf-8")
    filter_path = tmp_path / "rastertoBixolon"
    archive = tmp_path / "driver.tgz"
    configured: list[ManagedPrinter] = []
    downloads: list[Path] = []
    state = State()

    def download(destination: Path) -> Path:
        downloads.append(destination)
        return archive

    monkeypatch.setattr("airprint_server.cli._root", lambda: None)
    monkeypatch.setattr("airprint_server.cli.cups.queue_exists", lambda *_args: False)
    monkeypatch.setattr(
        "airprint_server.cli.cups.create_or_update_queue",
        lambda _runner, printer: configured.append(printer),
    )
    monkeypatch.setattr("airprint_server.cli.download_bixolon_archive", download)
    monkeypatch.setattr(
        "airprint_server.cli.install_bixolon_driver",
        lambda _runner, _state, selected: BixolonInstallation(
            "1.5.9", "aarch64", ppd, filter_path
        )
        if selected == archive
        else None,
    )
    monkeypatch.setattr("airprint_server.cli.save_state", lambda _state: None)
    args = argparse.Namespace(
        name="BIXOLON-SRP-E300",
        description=None,
        profile="bixolon-srp-e300",
        connection="usb",
        device_uri="usb://BIXOLON/SRP-E300?serial=00000001",
        host=None,
        port=9100,
        disable_snmp=False,
        driver="drv:///escpos.drv/gp80160.ppd",
        ppd=None,
        adopt=False,
        yes=True,
    )

    cmd_add(args, FakeRunner(), state, load_profiles())  # type: ignore[arg-type]

    assert len(downloads) == 1
    assert configured[0].driver is None
    assert configured[0].ppd == str(ppd)
    assert configured[0].cups_options == BIXOLON_CUPS_OPTIONS


def test_add_xprinter_uses_installed_official_driver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ppds = {}
    details = {"version": "3.13.11", "architecture": "aarch64"}
    for model in ("58", "76", "80"):
        ppd = tmp_path / f"POS-{model}.ppd"
        ppd.write_text("*PPD-Adobe: \"4.3\"\n", encoding="utf-8")
        ppds[model] = ppd
        details[f"ppd_{model}"] = str(ppd)
    for name in ("rastertosnailep", "rastertosnailep2"):
        filter_path = tmp_path / f"{name}-pos"
        filter_path.write_bytes(b"\x7fELF")
        details[f"filter_{name}"] = str(filter_path)
    state = State(vendor_drivers={"xprinter-pos-cups": details})
    configured: list[ManagedPrinter] = []
    monkeypatch.setattr("airprint_server.cli._root", lambda: None)
    monkeypatch.setattr("airprint_server.cli.cups.queue_exists", lambda *_args: False)
    monkeypatch.setattr(
        "airprint_server.cli.cups.create_or_update_queue",
        lambda _runner, printer: configured.append(printer),
    )
    monkeypatch.setattr("airprint_server.cli.save_state", lambda _state: None)
    args = argparse.Namespace(
        name="XPrinter-XP-80C",
        description=None,
        profile="xprinter-80mm",
        connection="usb",
        device_uri="usb://XPrinter/XP-80C?serial=123456",
        host=None,
        port=9100,
        disable_snmp=False,
        driver=None,
        ppd=None,
        adopt=False,
        yes=True,
    )

    cmd_add(args, FakeRunner(), state, load_profiles())  # type: ignore[arg-type]

    assert configured[0].driver is None
    assert configured[0].ppd == str(ppds["80"])
    assert configured[0].cups_options == XPRINTER_CUPS_OPTIONS["80"]
    assert configured[0].cups_options["PageCutType"] == "0NoCutPage"
    assert configured[0].cups_options["DocCutType"] == "1PartialCutDoc"


def test_install_xprinter_downloads_when_package_is_omitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    downloaded_to: list[Path] = []
    archive = tmp_path / "driver.rar"
    archive.write_bytes(b"archive")
    ppd_paths = {model: tmp_path / f"POS-{model}.ppd" for model in ("58", "76", "80")}
    filter_paths = {
        name: tmp_path / f"{name}-pos"
        for name in ("rastertosnailep", "rastertosnailep2")
    }
    state = State()

    def download(destination: Path) -> Path:
        downloaded_to.append(destination)
        return archive

    monkeypatch.setattr("airprint_server.cli._root", lambda: None)
    monkeypatch.setattr("airprint_server.cli.load_state", lambda: state)
    monkeypatch.setattr("airprint_server.cli.load_profiles", lambda _path: {})
    monkeypatch.setattr("airprint_server.cli.download_xprinter_package", download)
    monkeypatch.setattr(
        "airprint_server.cli.install_xprinter_driver",
        lambda _runner, _state, selected: XPrinterInstallation(
            "3.13.11", "aarch64", ppd_paths, filter_paths
        )
        if selected == archive
        else None,
    )
    monkeypatch.setattr("airprint_server.cli.save_state", lambda _state: None)

    assert main(["install-xprinter-driver", "--yes"]) == 0
    assert len(downloaded_to) == 1
    assert downloaded_to[0].name.startswith("airprint-server-xprinter-")


def test_add_xprinter_downloads_official_driver_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = tmp_path / "driver.rar"
    ppd_paths = {model: tmp_path / f"POS-{model}.ppd" for model in ("58", "76", "80")}
    for ppd in ppd_paths.values():
        ppd.write_text("*PPD-Adobe: \"4.3\"\n", encoding="utf-8")
    filter_paths = {
        name: tmp_path / f"{name}-pos"
        for name in ("rastertosnailep", "rastertosnailep2")
    }
    configured: list[ManagedPrinter] = []
    downloads: list[Path] = []
    state = State()

    def download(destination: Path) -> Path:
        downloads.append(destination)
        return archive

    monkeypatch.setattr("airprint_server.cli._root", lambda: None)
    monkeypatch.setattr("airprint_server.cli.cups.queue_exists", lambda *_args: False)
    monkeypatch.setattr(
        "airprint_server.cli.cups.create_or_update_queue",
        lambda _runner, printer: configured.append(printer),
    )
    monkeypatch.setattr("airprint_server.cli.download_xprinter_package", download)
    monkeypatch.setattr(
        "airprint_server.cli.install_xprinter_driver",
        lambda _runner, _state, selected: XPrinterInstallation(
            "3.13.11", "aarch64", ppd_paths, filter_paths
        )
        if selected == archive
        else None,
    )
    monkeypatch.setattr("airprint_server.cli.save_state", lambda _state: None)
    args = argparse.Namespace(
        name="XPrinter-XP-58",
        description=None,
        profile="xprinter-58mm",
        connection="usb",
        device_uri="usb://XPrinter/XP-58?serial=123456",
        host=None,
        port=9100,
        disable_snmp=False,
        driver="drv:///escpos.drv/gp58130.ppd",
        ppd=None,
        adopt=False,
        yes=True,
    )

    cmd_add(args, FakeRunner(), state, load_profiles())  # type: ignore[arg-type]

    assert len(downloads) == 1
    assert configured[0].driver is None
    assert configured[0].ppd == str(ppd_paths["58"])
    assert configured[0].cups_options == XPRINTER_CUPS_OPTIONS["58"]


def test_running_utility_without_command_starts_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[argparse.Namespace] = []
    monkeypatch.setattr(sys, "stdin", _InteractiveInput())
    monkeypatch.setattr("airprint_server.cli._dispatch", dispatched.append)

    assert main([]) == 0
    assert dispatched[0].command == "setup"


def test_add_printer_records_requested_raw_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    state = State(raw_proxy_service_managed=True)
    configured: list[ManagedPrinter] = []
    reconciled: list[int | None] = []
    monkeypatch.setattr("airprint_server.cli._root", lambda: None)
    monkeypatch.setattr("airprint_server.cli.cups.queue_exists", lambda *_args: False)
    monkeypatch.setattr(
        "airprint_server.cli.cups.create_or_update_queue",
        lambda _runner, printer: configured.append(printer),
    )
    monkeypatch.setattr("airprint_server.cli.save_state", lambda _state: None)
    monkeypatch.setattr(
        "airprint_server.cli.raw_proxy.reconcile_service",
        lambda _runner, current: reconciled.append(current.printers["Receipt"].raw_port),
    )
    args = argparse.Namespace(
        name="Receipt",
        description=None,
        profile="escpos-generic-80mm",
        connection="usb",
        device_uri="usb://Vendor/Receipt?serial=1",
        host=None,
        port=9100,
        raw_port=9100,
        disable_snmp=False,
        driver=None,
        ppd=None,
        adopt=False,
        yes=True,
    )

    cmd_add(args, FakeRunner(), state, load_profiles())  # type: ignore[arg-type]

    assert configured[0].raw_port == 9100
    assert reconciled == [9100]


def test_raw_exposure_commands_parse_valid_ports() -> None:
    parser = build_parser()
    assert parser.parse_args(["expose-raw", "Receipt"]).port is None
    assert parser.parse_args(["expose-raw", "Receipt", "--port", "9101"]).port == 9101
    assert parser.parse_args(["unexpose-raw", "Receipt"]).printer == "Receipt"


def test_raw_service_command_does_not_read_root_only_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "raw-proxy.yaml"
    config.write_text("version: 1\nlisteners: []\n", encoding="utf-8")
    served: list[Path] = []
    monkeypatch.setattr(
        "airprint_server.cli.load_state",
        lambda: pytest.fail("serve-raw must not read the root-only state file"),
    )
    monkeypatch.setattr("airprint_server.cli.raw_proxy.serve_config", served.append)

    assert main(["serve-raw", "--config", str(config)]) == 0
    assert served == [config]
