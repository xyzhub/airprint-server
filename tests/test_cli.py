from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from conftest import FakeRunner

from airprint_server.bixolon_driver import BIXOLON_CUPS_OPTIONS, BixolonInstallation
from airprint_server.cli import cmd_add, main
from airprint_server.config import ManagedPrinter, State
from airprint_server.profiles import load_profiles


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
