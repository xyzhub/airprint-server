"""Avahi and standard CUPS DNS-SD integration."""

from __future__ import annotations

from airprint_server.commands import Runner
from airprint_server.validation import queue_name


def advertised(runner: Runner, printer: str, description: str | None = None) -> bool:
    """Check both modern IPP and AirPrint subtype advertisements."""
    names = {queue_name(printer).lower()}
    if description:
        names.add(description.lower())
    result = runner.run(["avahi-browse", "-rt", "_ipp._tcp"], check=False, timeout=8)
    if any(name in result.stdout.lower() for name in names):
        return True
    fallback = runner.run(["avahi-browse", "-rt", "_airprint._tcp"], check=False, timeout=8)
    return any(name in fallback.stdout.lower() for name in names)


def service_active(runner: Runner, service: str) -> bool:
    return runner.run(["systemctl", "is-active", "--quiet", service], check=False).returncode == 0


def service_enabled(runner: Runner, service: str) -> bool:
    return runner.run(["systemctl", "is-enabled", "--quiet", service], check=False).returncode == 0


def ensure_services(runner: Runner) -> None:
    runner.run(["systemctl", "enable", "--now", "cups.service"])
    runner.run(["systemctl", "enable", "--now", "avahi-daemon.service"])
