from pathlib import Path

import yaml

from airprint_server.config import ManagedPrinter, State, load_state, save_state


def test_state_round_trip_and_atomic_replacement(tmp_path: Path) -> None:
    path = tmp_path / "state.yaml"
    state = State(
        printers={
            "Counter": ManagedPrinter(
                "Counter", "Counter", "escpos-generic-80mm", "usb://V/M?serial=1", "usb"
            )
        }
    )
    save_state(state, path)
    loaded = load_state(path)
    assert loaded.printers["Counter"].device_uri.endswith("serial=1")
    state.printers["Counter"].description = "Updated"
    save_state(state, path)
    assert yaml.safe_load(path.read_text())["printers"]["Counter"]["description"] == "Updated"
    assert not list(tmp_path.glob(".state.yaml.*"))


def test_missing_state_is_empty(tmp_path: Path) -> None:
    assert load_state(tmp_path / "missing.yaml").printers == {}

