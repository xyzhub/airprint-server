from pathlib import Path

from conftest import FakeRunner

from airprint_server.commands import CommandResult
from airprint_server.config import ManagedPrinter
from airprint_server.cups import create_queue_args, list_queues


def test_cups_command_generation() -> None:
    printer = ManagedPrinter(
        name="SwissPOS",
        description="SwissPOS AirPrint ESC-POS",
        profile="swisspos-t80c",
        device_uri="socket://192.168.1.123:9100",
        connection="socket",
        driver="drv:///escpos.drv/gp80160.ppd",
        cups_options={"PageSize": "w226h842", "ColorModel": "Gray"},
    )
    args = create_queue_args(printer)
    assert args[:6] == [
        "lpadmin",
        "-p",
        "SwissPOS",
        "-v",
        "socket://192.168.1.123:9100",
        "-D",
    ]
    assert args[7:9] == ["-m", "drv:///escpos.drv/gp80160.ppd"]
    assert "printer-is-shared=true" in args
    assert "PageSize=w226h842" in args


def test_custom_ppd_command(tmp_path: Path) -> None:
    ppd = tmp_path / "vendor.ppd"
    ppd.write_text("*PPD-Adobe: \"4.3\"\n", encoding="ascii")
    printer = ManagedPrinter("Office", "Office", None, "usb://V/M", "usb", ppd=str(ppd))
    args = create_queue_args(printer)
    assert "-P" in args
    assert str(ppd) in args


def test_parse_cups_status() -> None:
    p = ("lpstat", "-p")
    a = ("lpstat", "-a")
    runner = FakeRunner(
        {
            p: CommandResult(p, 0, "printer One is idle. enabled since now\nprinter Two disabled"),
            a: CommandResult(a, 0, "One accepting requests since now\n"),
        }
    )
    queues = list_queues(runner)  # type: ignore[arg-type]
    assert queues["One"].enabled and queues["One"].accepting
    assert not queues["Two"].enabled and not queues["Two"].accepting
