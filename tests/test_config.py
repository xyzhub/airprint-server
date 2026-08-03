from pathlib import Path

import pytest
import yaml

from airprint_server.config import ManagedPrinter, State, load_state, save_state
from airprint_server.validation import ValidationError


def test_state_round_trip_and_atomic_replacement(tmp_path: Path) -> None:
    path = tmp_path / "state.yaml"
    state = State(
        printers={
            "Counter": ManagedPrinter(
                "Counter", "Counter", "escpos-generic-80mm", "usb://V/M?serial=1", "usb"
            )
        },
        update_source="/var/lib/airprint-server/source",
        update_remote="https://github.com/xyzhub/airprint-server.git",
        installed_revision="a" * 40,
        vendor_drivers={"bixolon-pos-cups": {"version": "1.5.9"}},
    )
    save_state(state, path)
    loaded = load_state(path)
    assert loaded.printers["Counter"].device_uri.endswith("serial=1")
    assert loaded.installed_revision == "a" * 40
    assert loaded.vendor_drivers["bixolon-pos-cups"]["version"] == "1.5.9"
    state.printers["Counter"].description = "Updated"
    save_state(state, path)
    assert yaml.safe_load(path.read_text())["printers"]["Counter"]["description"] == "Updated"
    assert not list(tmp_path.glob(".state.yaml.*"))


def test_missing_state_is_empty(tmp_path: Path) -> None:
    assert load_state(tmp_path / "missing.yaml").printers == {}


def test_raw_tcp_port_round_trips_and_duplicate_ports_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "state.yaml"
    first = ManagedPrinter(
        "First", "First", None, "usb://Vendor/First", "usb", raw_port=9100
    )
    save_state(State(printers={"First": first}), path)
    assert load_state(path).printers["First"].raw_port == 9100

    duplicate = yaml.safe_load(path.read_text())
    duplicate["printers"]["Second"] = {
        "name": "Second",
        "description": "Second",
        "profile": None,
        "device_uri": "usb://Vendor/Second",
        "connection": "usb",
        "raw_port": 9100,
    }
    path.write_text(yaml.safe_dump(duplicate), encoding="utf-8")
    with pytest.raises(ValidationError, match="raw TCP port 9100"):
        load_state(path)
