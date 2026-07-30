from collections.abc import Callable, Iterator

from conftest import FakeRunner

from airprint_server.cli import build_parser
from airprint_server.commands import CommandResult
from airprint_server.discovery import USBDevice, parse_lpinfo_ipp_uris
from airprint_server.profiles import load_profiles
from airprint_server.wizard import (
    WizardSelection,
    collect_printer,
    find_driver_models,
    parse_driver_models,
    run_wizard,
)


def answers(*values: str) -> tuple[Callable[[str], str], Iterator[str]]:
    iterator = iter(values)
    return lambda _prompt: next(iterator), iterator


def test_parse_ipp_devices_and_driver_matches() -> None:
    assert parse_lpinfo_ipp_uris(
        "network ipp\nnetwork ipp://localhost:60000/ipp/print\n"
        "network ipps://office.local/ipp/print\n"
    ) == [
        "ipp://localhost:60000/ipp/print",
        "ipps://office.local/ipp/print",
    ]
    models = parse_driver_models(
        "drv:///acme.drv/laser.ppd Acme Laser 1000\n"
        "drv:///other.drv/generic.ppd Generic Printer\n"
    )
    matches = find_driver_models(
        models, USBDevice("usb://Acme/Laser%201000", "Acme", "Laser 1000", None)
    )
    assert [model.uri for model in matches] == ["drv:///acme.drv/laser.ppd"]


def test_collect_xprinter_usb_uses_suggested_profile() -> None:
    command = ("lpinfo", "-v")
    runner = FakeRunner(
        {
            command: CommandResult(
                command,
                0,
                "direct usb://XPrinter/XP-80C?serial=123456\n",
            )
        }
    )
    input_fn, _ = answers("1", "1", "", "")
    selection = collect_printer(
        runner,  # type: ignore[arg-type]
        load_profiles(),
        input_fn=input_fn,  # type: ignore[arg-type]
        output=lambda _message: None,
    )
    assert selection == WizardSelection(
        name="XPrinter-XP-80C",
        description="Generic Xprinter-compatible 80 mm",
        profile="xprinter-80mm",
        connection="usb",
        device_uri="usb://XPrinter/XP-80C?serial=123456",
        driver="drv:///escpos.drv/gp80160.ppd",
        ppd=None,
    )


def test_collect_bixolon_usb_uses_job_cut_profile() -> None:
    command = ("lpinfo", "-v")
    runner = FakeRunner(
        {
            command: CommandResult(
                command,
                0,
                "direct usb://BIXOLON/SRP-E300?serial=00000001\n",
            )
        }
    )
    input_fn, _ = answers("1", "1", "", "")
    selection = collect_printer(
        runner,  # type: ignore[arg-type]
        load_profiles(),
        input_fn=input_fn,  # type: ignore[arg-type]
        output=lambda _message: None,
    )
    assert selection is not None
    assert selection.profile == "bixolon-srp-e300"
    assert selection.driver == "drv:///escpos.drv/gp80160.ppd"
    assert selection.name == "BIXOLON-SRP-E300"


def test_collect_generic_usb_suggests_installed_driver() -> None:
    devices = ("lpinfo", "-v")
    models = ("lpinfo", "-m")
    runner = FakeRunner(
        {
            devices: CommandResult(devices, 0, "direct usb://Acme/Laser%201000?serial=A\n"),
            models: CommandResult(
                models,
                0,
                "drv:///acme.drv/laser.ppd Acme Laser 1000\n"
                "drv:///sample.drv/generic.ppd Generic Printer\n",
            ),
        }
    )
    input_fn, _ = answers("1", "1", "1", "", "")
    selection = collect_printer(
        runner,  # type: ignore[arg-type]
        load_profiles(),
        input_fn=input_fn,  # type: ignore[arg-type]
        output=lambda _message: None,
    )
    assert selection is not None
    assert selection.profile == "generic-driverless"
    assert selection.driver == "drv:///acme.drv/laser.ppd"
    assert selection.name == "Acme-Laser-1000"


def test_wizard_can_finish_without_adding_printer() -> None:
    command = ("lpinfo", "-v")
    runner = FakeRunner({command: CommandResult(command, 0, "")})
    input_fn, _ = answers("4")
    added: list[WizardSelection] = []
    run_wizard(
        runner,  # type: ignore[arg-type]
        load_profiles(),
        added.append,
        input_fn=input_fn,  # type: ignore[arg-type]
        output=lambda _message: None,
    )
    assert added == []


def test_install_wizard_mode_defaults() -> None:
    parser = build_parser()
    assert parser.parse_args(["install"]).wizard is None
    assert parser.parse_args(["install", "--wizard"]).wizard is True
    assert parser.parse_args(["install", "--no-wizard"]).wizard is False
