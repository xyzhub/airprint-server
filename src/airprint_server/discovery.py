"""Network and USB discovery helpers."""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

from airprint_server.commands import Runner
from airprint_server.validation import host, port

USB_LINE_RE = re.compile(r"^(?:direct|serial)\s+(usb://\S+)")


@dataclass(frozen=True)
class USBDevice:
    uri: str
    manufacturer: str
    model: str
    serial: str | None

    @property
    def stable(self) -> bool:
        return bool(self.serial)


def parse_usb_uri(uri: str) -> USBDevice:
    parsed = urlsplit(uri)
    if parsed.scheme.lower() != "usb":
        raise ValueError(f"not a USB device URI: {uri}")
    manufacturer = unquote(parsed.netloc) or "Unknown"
    model = unquote(parsed.path.lstrip("/")) or "Unknown"
    query = parse_qs(parsed.query)
    serial_values = query.get("serial", [])
    return USBDevice(uri, manufacturer, model, serial_values[0] if serial_values else None)


def parse_lpinfo_devices(output: str) -> list[USBDevice]:
    devices: list[USBDevice] = []
    seen: set[str] = set()
    for line in output.splitlines():
        match = USB_LINE_RE.match(line.strip())
        if match and match.group(1) not in seen:
            seen.add(match.group(1))
            devices.append(parse_usb_uri(match.group(1)))
    return devices


def discover_usb(runner: Runner) -> list[USBDevice]:
    result = runner.run(["lpinfo", "-v"], check=False)
    if result.returncode:
        raise RuntimeError(
            "CUPS USB discovery failed. Ensure CUPS is running and the USB backend is executable.\n"
            + result.stderr.strip()
        )
    return parse_lpinfo_devices(result.stdout)


def select_usb(devices: list[USBDevice]) -> USBDevice:
    if not devices:
        raise RuntimeError(
            "No CUPS usb:// devices found. Check power, cable, CUPS USB backend, and ipp-usb."
        )
    print("Detected USB printers:")
    for index, item in enumerate(devices, 1):
        serial = item.serial or "no serial (URI may be unstable)"
        print(f"  {index}. {item.manufacturer} {item.model}; {serial}\n     {item.uri}")
    duplicates: dict[tuple[str, str], int] = {}
    for item in devices:
        if not item.serial:
            key = (item.manufacturer, item.model)
            duplicates[key] = duplicates.get(key, 0) + 1
    if any(count > 1 for count in duplicates.values()):
        print("WARNING: identical devices without serial numbers cannot be reliably distinguished.")
    while True:
        answer = input("Select a printer number: ").strip()
        try:
            return devices[int(answer) - 1]
        except (ValueError, IndexError):
            print(f"Enter a number from 1 to {len(devices)}.")


def tcp_reachable(hostname: str, number: int, timeout: float = 2.0) -> tuple[bool, str]:
    checked_host, checked_port = host(hostname), port(number)
    try:
        with socket.create_connection((checked_host, checked_port), timeout=timeout):
            return True, f"TCP connection to {checked_host}:{checked_port} succeeded"
    except OSError as exc:
        return False, f"TCP connection to {checked_host}:{checked_port} failed: {exc}"


def discover_network(runner: Runner) -> str:
    """Return raw DNS-SD printer discovery; parsing vendor data would lose useful fields."""
    result = runner.run(["avahi-browse", "-rt", "_printer._tcp"], check=False, timeout=8)
    if result.returncode not in {0, 124}:
        raise RuntimeError(f"Avahi discovery failed: {result.stderr.strip()}")
    return result.stdout

