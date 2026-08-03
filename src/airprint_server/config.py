"""Atomic configuration and managed-state persistence."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from airprint_server.validation import (
    ValidationError,
    device_uri,
    port,
    profile_id,
    queue_name,
)

CONFIG_DIR = Path("/etc/airprint-server")
CONFIG_PATH = CONFIG_DIR / "config.yaml"
PROFILE_DIR = CONFIG_DIR / "profiles.d"
STATE_DIR = Path("/var/lib/airprint-server")
STATE_PATH = STATE_DIR / "state.yaml"


@dataclass
class ManagedPrinter:
    name: str
    description: str
    profile: str | None
    device_uri: str
    connection: str
    driver: str | None = None
    ppd: str | None = None
    cups_options: dict[str, str] = field(default_factory=dict)
    adopted: bool = False
    raw_port: int | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> ManagedPrinter:
        name = queue_name(str(raw["name"]))
        uri = device_uri(str(raw["device_uri"]), allow_custom=True)
        selected = raw.get("profile")
        if selected is not None:
            profile_id(str(selected))
        options = raw.get("cups_options", {})
        if not isinstance(options, dict):
            raise ValidationError(f"printer {name}: cups_options must be a mapping")
        return cls(
            name=name,
            description=str(raw.get("description", name)),
            profile=str(selected) if selected else None,
            device_uri=uri,
            connection=str(raw.get("connection", "custom-uri")),
            driver=str(raw["driver"]) if raw.get("driver") else None,
            ppd=str(raw["ppd"]) if raw.get("ppd") else None,
            cups_options={str(k): str(v) for k, v in options.items()},
            adopted=bool(raw.get("adopted", False)),
            raw_port=port(raw["raw_port"]) if raw.get("raw_port") is not None else None,
        )


@dataclass
class State:
    version: int = 1
    printers: dict[str, ManagedPrinter] = field(default_factory=dict)
    rastertoescpos_managed: bool = False
    rastertoescpos_source: str | None = None
    cups_backups: list[str] = field(default_factory=list)
    avahi_services: list[str] = field(default_factory=list)
    ipp_usb_previous: dict[str, Any] | None = None
    installed_packages: list[str] = field(default_factory=list)
    update_source: str | None = None
    update_remote: str | None = None
    installed_revision: str | None = None
    vendor_drivers: dict[str, dict[str, str]] = field(default_factory=dict)
    raw_proxy_service_managed: bool = False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> State:
        printers_raw = raw.get("printers", {})
        if not isinstance(printers_raw, dict):
            raise ValidationError("state printers must be a mapping")
        printers = {
            name: ManagedPrinter.from_mapping(value) for name, value in printers_raw.items()
        }
        raw_ports: dict[int, str] = {}
        for printer in printers.values():
            if printer.raw_port is None:
                continue
            previous = raw_ports.get(printer.raw_port)
            if previous:
                raise ValidationError(
                    f"raw TCP port {printer.raw_port} is assigned to both "
                    f"{previous!r} and {printer.name!r}"
                )
            raw_ports[printer.raw_port] = printer.name
        vendor_drivers_raw = raw.get("vendor_drivers", {})
        if not isinstance(vendor_drivers_raw, dict) or any(
            not isinstance(value, dict) for value in vendor_drivers_raw.values()
        ):
            raise ValidationError("state vendor_drivers must be a mapping of mappings")
        vendor_drivers = {
            str(name): {str(key): str(value) for key, value in details.items()}
            for name, details in vendor_drivers_raw.items()
        }
        return cls(
            version=int(raw.get("version", 1)),
            printers=printers,
            rastertoescpos_managed=bool(raw.get("rastertoescpos_managed", False)),
            rastertoescpos_source=raw.get("rastertoescpos_source"),
            cups_backups=[str(v) for v in raw.get("cups_backups", [])],
            avahi_services=[str(v) for v in raw.get("avahi_services", [])],
            ipp_usb_previous=raw.get("ipp_usb_previous"),
            installed_packages=[str(v) for v in raw.get("installed_packages", [])],
            update_source=str(raw["update_source"]) if raw.get("update_source") else None,
            update_remote=str(raw["update_remote"]) if raw.get("update_remote") else None,
            installed_revision=(
                str(raw["installed_revision"]) if raw.get("installed_revision") else None
            ),
            vendor_drivers=vendor_drivers,
            raw_proxy_service_managed=bool(raw.get("raw_proxy_service_managed", False)),
        )


def atomic_write_yaml(path: Path, data: object, *, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    yaml.safe_load(serialized)  # Validate before replacing the target.
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_state(path: Path = STATE_PATH) -> State:
    if not path.exists():
        return State()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValidationError(f"cannot read state file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValidationError(f"state file {path} must contain a YAML mapping")
    return State.from_mapping(raw)


def save_state(state: State, path: Path = STATE_PATH) -> None:
    data = asdict(state)
    data["printers"] = {name: asdict(printer) for name, printer in state.printers.items()}
    atomic_write_yaml(path, data)


def initialize_config(path: Path = CONFIG_PATH) -> None:
    if not path.exists():
        atomic_write_yaml(path, {"version": 1, "airprint": {"remote_admin": False}})
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
