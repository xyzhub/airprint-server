"""Human-readable, actionable diagnostics."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from airprint_server import avahi, cups, raw_proxy
from airprint_server.commands import Runner, command_exists
from airprint_server.config import ManagedPrinter, State
from airprint_server.discovery import discover_usb, tcp_reachable
from airprint_server.installer import ipp_usb_state, operating_system, rastertoescpos_available
from airprint_server.profiles import PrinterProfile


@dataclass(frozen=True)
class Check:
    ok: bool
    label: str
    detail: str = ""
    action: str = ""
    warning: bool = False

    def format(self) -> str:
        marker = "WARN" if self.warning else ("OK" if self.ok else "FAIL")
        text = f"[{marker}] {self.label}"
        if self.detail:
            text += f": {self.detail}"
        if not self.ok and self.action:
            text += f"\n       Action: {self.action}"
        return text


def _service(runner: Runner, service: str, label: str) -> Check:
    ok = avahi.service_active(runner, service)
    return Check(ok, f"{label} running", action=f"sudo systemctl status {service}")


def diagnose(
    runner: Runner,
    state: State,
    *,
    printer: ManagedPrinter | None = None,
    profile: PrinterProfile | None = None,
) -> list[Check]:
    checks: list[Check] = []
    supported, os_label = operating_system()
    checks.append(
        Check(
            supported,
            "Supported operating system",
            os_label,
            "Use Debian/Raspberry Pi OS 12 (Bookworm) or 13 (Trixie); "
            "other releases are not validated.",
        )
    )
    cups_installed = command_exists("lpstat")
    checks.append(
        Check(cups_installed, "CUPS installed", action="sudo apt install cups cups-client")
    )
    if cups_installed:
        checks.append(_service(runner, "cups.service", "CUPS"))
        valid, detail = cups.validate_cups(runner)
        checks.append(Check(valid, "CUPS configuration valid", detail, "sudo cupsd -t"))
    avahi_installed = command_exists("avahi-browse")
    checks.append(
        Check(
            avahi_installed,
            "Avahi installed",
            action="sudo apt install avahi-daemon avahi-utils",
        )
    )
    if avahi_installed:
        checks.append(_service(runner, "avahi-daemon.service", "Avahi"))
    ipp = ipp_usb_state(runner)
    if ipp["installed"]:
        checks.append(
            Check(
                True,
                "ipp-usb detected",
                "active; it may claim some USB printers" if ipp["active"] else "not active",
                warning=ipp["active"],
            )
        )
    if printer is None:
        return checks
    exists = cups.queue_exists(runner, printer.name)
    checks.append(
        Check(exists, "Queue exists", printer.name, f"lpstat -p {printer.name} -l")
    )
    statuses = cups.list_queues(runner) if exists else {}
    queue = statuses.get(printer.name)
    checks.append(
        Check(
            bool(queue and queue.enabled),
            "Queue enabled",
            action=f"sudo cupsenable {printer.name}",
        )
    )
    checks.append(
        Check(
            bool(queue and queue.accepting),
            "Queue accepting jobs",
            action=f"sudo cupsaccept {printer.name}",
        )
    )
    attrs = cups.printer_attributes(runner, printer.name) if exists else {}
    shared = attrs.get("printer-is-shared", "").lower() in {"true", "yes", "1"}
    checks.append(
        Check(
            shared,
            "Queue shared",
            action=f"sudo lpadmin -p {printer.name} -o printer-is-shared=true",
        )
    )
    if printer.driver:
        checks.append(
            Check(
                cups.driver_available(runner, printer.driver),
                "Driver available",
                printer.driver,
                "lpinfo -m | less",
            )
        )
    checks.append(Check(True, "Device URI valid", printer.device_uri))
    if printer.raw_port is not None:
        service_running = avahi.service_active(runner, raw_proxy.RAW_PROXY_SERVICE_NAME)
        checks.append(
            Check(
                service_running,
                "Raw TCP gateway running",
                f"port {printer.raw_port}",
                f"sudo systemctl status {raw_proxy.RAW_PROXY_SERVICE_NAME}",
            )
        )
        reachable, detail = tcp_reachable("127.0.0.1", printer.raw_port)
        checks.append(
            Check(
                reachable,
                "Raw TCP listener reachable",
                detail,
                f"nc -vz 127.0.0.1 {printer.raw_port}",
            )
        )
    if avahi_installed and exists:
        checks.append(
            Check(
                avahi.advertised(runner, printer.name, printer.description),
                "AirPrint service advertised",
                action="avahi-browse -rt _ipp._tcp",
            )
        )
    if profile and profile.category == "escpos":
        escpos = rastertoescpos_available(runner)
        checks.extend(
            [
                Check(
                    escpos,
                    "rastertoescpos installed",
                    action="sudo airprint-server install",
                ),
                Check(
                    bool(profile.driver and cups.driver_available(runner, profile.driver)),
                    "ESC/POS PPD available",
                    profile.driver or "",
                    "lpinfo -m | grep escpos",
                ),
            ]
        )
    parsed = urlsplit(printer.device_uri)
    if parsed.scheme == "socket" and parsed.hostname and parsed.port:
        try:
            socket.getaddrinfo(parsed.hostname, parsed.port)
            checks.append(Check(True, "Printer host resolved", parsed.hostname))
        except socket.gaierror as exc:
            checks.append(
                Check(False, "Printer host resolved", str(exc), f"getent hosts {parsed.hostname}")
            )
        reachable, detail = tcp_reachable(parsed.hostname, parsed.port)
        checks.append(
            Check(
                reachable,
                "TCP port reachable",
                detail,
                f"nc -vz {parsed.hostname} {parsed.port}",
            )
        )
    if parsed.scheme == "usb":
        backend = Path("/usr/lib/cups/backend/usb")
        if not backend.exists():
            backend = Path("/usr/libexec/cups/backend/usb")
        checks.append(
            Check(backend.exists(), "USB backend available", str(backend), "sudo apt install cups")
        )
        try:
            devices = discover_usb(runner)
            checks.append(Check(bool(devices), "USB printer detected", f"{len(devices)} device(s)"))
            current = any(item.uri == printer.device_uri for item in devices)
            checks.append(
                Check(
                    current,
                    "Configured USB URI currently present",
                    printer.device_uri,
                    "lpinfo -v | grep '^direct usb://'",
                )
            )
        except RuntimeError as exc:
            checks.append(Check(False, "USB printer detected", str(exc), "lpinfo -v"))
    return checks


def recent_logs(runner: Runner, *, include_ipp_usb: bool = False) -> str:
    services = ["cups.service", "avahi-daemon.service", raw_proxy.RAW_PROXY_SERVICE_NAME]
    if include_ipp_usb:
        services.append("ipp-usb.service")
    service_args = [value for service in services for value in ("-u", service)]
    result = runner.run(
        ["journalctl", "--no-pager", "-n", "20", "--output=short", *service_args],
        check=False,
    )
    return result.stdout[-8000:]
