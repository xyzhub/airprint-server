"""Printer profile loading and validation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from airprint_server.validation import ValidationError, profile_id

PROFILE_STATUSES = {"tested", "community-tested", "unverified", "generic"}
CONNECTIONS = {"socket", "usb", "ipp", "ipps", "lpd", "custom-uri"}


@dataclass(frozen=True)
class PrinterProfile:
    id: str
    display_name: str
    category: str
    status: str
    supported_connections: tuple[str, ...]
    driver: str | None = None
    paper_width_mm: int | None = None
    printable_width_mm: int | None = None
    page_size: str | None = None
    resolution: str | None = None
    color_model: str | None = None
    monochrome: bool = False
    cutter: bool = False
    cups_options: Mapping[str, str] = field(default_factory=dict)
    source: str = ""

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str = "") -> PrinterProfile:
        required = {"id", "display_name", "category", "status", "supported_connections"}
        missing = required.difference(raw)
        if missing:
            raise ValidationError(f"{source}: missing profile fields: {', '.join(sorted(missing))}")
        identifier = profile_id(str(raw["id"]))
        display_name = str(raw["display_name"]).strip()
        category = str(raw["category"]).strip().lower()
        status = str(raw["status"]).strip()
        if not display_name or not category:
            raise ValidationError(f"{source}: display_name and category may not be empty")
        if status not in PROFILE_STATUSES:
            raise ValidationError(f"{source}: status must be one of {sorted(PROFILE_STATUSES)}")
        connections_raw = raw["supported_connections"]
        if not isinstance(connections_raw, list) or not connections_raw:
            raise ValidationError(f"{source}: supported_connections must be a non-empty list")
        connections = tuple(str(item) for item in connections_raw)
        unknown = set(connections).difference(CONNECTIONS)
        if unknown:
            raise ValidationError(f"{source}: unsupported connection types: {sorted(unknown)}")
        options_raw = raw.get("cups_options", {})
        if not isinstance(options_raw, dict):
            raise ValidationError(f"{source}: cups_options must be a mapping")
        options = {str(key): str(value) for key, value in options_raw.items()}
        widths: dict[str, int | None] = {}
        for key in ("paper_width_mm", "printable_width_mm"):
            value = raw.get(key)
            if value is not None and (not isinstance(value, int) or not 1 <= value <= 500):
                raise ValidationError(f"{source}: {key} must be an integer from 1 to 500")
            widths[key] = value
        if (
            widths["paper_width_mm"]
            and widths["printable_width_mm"]
            and widths["printable_width_mm"] > widths["paper_width_mm"]
        ):
            raise ValidationError(f"{source}: printable width cannot exceed paper width")
        driver = raw.get("driver")
        return cls(
            id=identifier,
            display_name=display_name,
            category=category,
            status=status,
            supported_connections=connections,
            driver=str(driver) if driver else None,
            paper_width_mm=widths["paper_width_mm"],
            printable_width_mm=widths["printable_width_mm"],
            page_size=str(raw["page_size"]) if raw.get("page_size") else None,
            resolution=str(raw["resolution"]) if raw.get("resolution") else None,
            color_model=str(raw["color_model"]) if raw.get("color_model") else None,
            monochrome=bool(raw.get("monochrome", False)),
            cutter=bool(raw.get("cutter", False)),
            cups_options=options,
            source=source,
        )

    def default_options(self) -> dict[str, str]:
        options = dict(self.cups_options)
        for key, value in (
            ("PageSize", self.page_size),
            ("Resolution", self.resolution),
            ("ColorModel", self.color_model),
        ):
            if value:
                options.setdefault(key, value)
        return options


def _load_file(path: Path) -> PrinterProfile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValidationError(f"cannot load profile {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValidationError(f"{path}: profile must be a YAML mapping")
    return PrinterProfile.from_mapping(raw, source=str(path))


def bundled_profile_dir() -> Path:
    return Path(str(files("airprint_server").joinpath("data/profiles")))


def load_profiles(
    system_dir: Path | None = None, extra_dirs: Iterable[Path] = ()
) -> dict[str, PrinterProfile]:
    """Load bundled profiles, then overrides. Local profile IDs intentionally win."""
    directories = [bundled_profile_dir()]
    if system_dir is not None:
        directories.append(system_dir)
    directories.extend(extra_dirs)
    loaded: dict[str, PrinterProfile] = {}
    for directory in directories:
        if not directory.exists():
            continue
        for path in sorted((*directory.glob("*.yaml"), *directory.glob("*.yml"))):
            profile = _load_file(path)
            loaded[profile.id] = profile
    return loaded

