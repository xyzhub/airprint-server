from pathlib import Path

import pytest
import yaml

from airprint_server.profiles import PrinterProfile, load_profiles
from airprint_server.validation import ValidationError


def test_bundled_profiles_and_options() -> None:
    profiles = load_profiles()
    swiss = profiles["swisspos-t80c"]
    assert swiss.status == "tested"
    assert swiss.driver == "drv:///escpos.drv/gp80160.ppd"
    assert swiss.default_options()["PageSize"] == "w226h842"
    assert swiss.default_options()["print-color-mode"] == "monochrome"
    assert {
        "bixolon-srp-e300",
        "escpos-generic-58mm",
        "escpos-generic-80mm",
        "generic-driverless",
        "xprinter-58mm",
        "xprinter-76mm",
        "xprinter-80mm",
    } <= profiles.keys()


def test_bixolon_profile_cuts_once_per_job() -> None:
    profile = load_profiles()["bixolon-srp-e300"]
    assert profile.status == "unverified"
    assert profile.cutter
    assert profile.paper_width_mm == 80
    assert profile.printable_width_mm == 72
    assert profile.default_options()["PageSize"] == "Custom.72x297mm"
    assert profile.default_options()["Resolution"] == "180dpi"
    assert profile.default_options()["escCutter"] == "1"


def test_local_profile_overrides_bundled(tmp_path: Path) -> None:
    override = {
        "id": "swisspos-t80c",
        "display_name": "Local calibrated SwissPOS",
        "category": "escpos",
        "status": "community-tested",
        "supported_connections": ["usb"],
    }
    (tmp_path / "local.yaml").write_text(yaml.safe_dump(override), encoding="utf-8")
    assert load_profiles(tmp_path)["swisspos-t80c"].display_name.startswith("Local")


@pytest.mark.parametrize(
    "change",
    [
        {"status": "magical"},
        {"supported_connections": ["serial"]},
        {"paper_width_mm": -1},
        {"paper_width_mm": 58, "printable_width_mm": 80},
    ],
)
def test_invalid_profiles(change: dict[str, object]) -> None:
    raw: dict[str, object] = {
        "id": "example",
        "display_name": "Example",
        "category": "general",
        "status": "generic",
        "supported_connections": ["ipp"],
    }
    raw.update(change)
    with pytest.raises(ValidationError):
        PrinterProfile.from_mapping(raw)
