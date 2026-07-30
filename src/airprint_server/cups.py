"""CUPS queue operations and parsers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from airprint_server.commands import CommandError, Runner
from airprint_server.config import ManagedPrinter
from airprint_server.validation import device_uri, queue_name, readable_ppd


@dataclass(frozen=True)
class QueueStatus:
    name: str
    enabled: bool
    accepting: bool
    shared: bool | None = None
    device_uri: str | None = None
    description: str | None = None


def create_queue_args(printer: ManagedPrinter) -> list[str]:
    queue_name(printer.name)
    device_uri(printer.device_uri, allow_custom=printer.connection == "custom-uri")
    args = [
        "lpadmin",
        "-p",
        printer.name,
        "-v",
        printer.device_uri,
        "-D",
        printer.description,
    ]
    if printer.ppd:
        args.extend(["-P", str(readable_ppd(printer.ppd))])
    elif printer.driver:
        args.extend(["-m", printer.driver])
    else:
        scheme = urlsplit(printer.device_uri).scheme.lower()
        if scheme in {"ipp", "ipps"}:
            args.extend(["-m", "everywhere"])
        else:
            raise ValueError("a driver, custom PPD, or IPP/IPPS driverless URI is required")
    args.extend(["-E", "-o", "printer-is-shared=true"])
    for key, value in sorted(printer.cups_options.items()):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", key) or "\x00" in value:
            raise ValueError(f"unsafe CUPS option: {key!r}")
        args.extend(["-o", f"{key}={value}"])
    return args


def create_or_update_queue(runner: Runner, printer: ManagedPrinter) -> None:
    runner.run(create_queue_args(printer))
    runner.run(["cupsenable", printer.name])
    runner.run(["cupsaccept", printer.name])


def remove_queue(runner: Runner, name: str) -> None:
    runner.run(["lpadmin", "-x", queue_name(name)])


def queue_exists(runner: Runner, name: str) -> bool:
    return runner.run(["lpstat", "-p", queue_name(name)], check=False).returncode == 0


def list_queues(runner: Runner) -> dict[str, QueueStatus]:
    printers = runner.run(["lpstat", "-p"], check=False)
    accepting = runner.run(["lpstat", "-a"], check=False)
    enabled: dict[str, bool] = {}
    for line in printers.stdout.splitlines():
        match = re.match(r"printer\s+(\S+)\s+(is idle|now printing|disabled)", line)
        if match:
            enabled[match.group(1)] = match.group(2) != "disabled"
    accepted = {
        line.split()[0]
        for line in accepting.stdout.splitlines()
        if "accepting requests" in line and line.split()
    }
    return {
        name: QueueStatus(name, is_enabled, name in accepted)
        for name, is_enabled in enabled.items()
    }


def printer_attributes(runner: Runner, name: str) -> dict[str, str]:
    result = runner.run(["lpoptions", "-p", queue_name(name)], check=False)
    attrs: dict[str, str] = {}
    for token in result.stdout.split():
        if "=" in token:
            key, value = token.split("=", 1)
            attrs[key] = value
    return attrs


def driver_available(runner: Runner, driver: str) -> bool:
    return driver in runner.run(["lpinfo", "-m"], check=False).stdout


def validate_cups(runner: Runner) -> tuple[bool, str]:
    result = runner.run(["cupsd", "-t"], check=False)
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def submit_file(runner: Runner, name: str, path: Path, options: dict[str, str]) -> str:
    args = ["lp", "-d", queue_name(name)]
    for key, value in sorted(options.items()):
        args.extend(["-o", f"{key}={value}" if value else key])
    args.append(str(path))
    try:
        result = runner.run(args)
    except CommandError as exc:
        raise RuntimeError(f"CUPS rejected the test job: {exc}") from exc
    return result.stdout.strip()
