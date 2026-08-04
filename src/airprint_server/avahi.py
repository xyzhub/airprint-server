"""Avahi and standard CUPS DNS-SD integration."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from airprint_server.commands import Runner
from airprint_server.config import State
from airprint_server.validation import ValidationError, queue_name

AVAHI_SERVICE_DIR = Path("/etc/avahi/services")
AVAHI_HOSTS_PATH = Path("/etc/avahi/hosts")
HOSTS_BEGIN = "# BEGIN airprint-server managed printer hosts"
HOSTS_END = "# END airprint-server managed printer hosts"
SERVICE_PREFIX = "airprint-server-"
XML_INVALID = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\uFFFE\uFFFF]")


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


def _printer_label(name: str) -> str:
    checked = queue_name(name)
    label = re.sub(r"[^a-z0-9-]+", "-", checked.lower()).strip("-") or "printer"
    return label


def raw_printer_hostnames(state: State) -> dict[str, str]:
    """Return stable, human-readable mDNS host names for dedicated raw endpoints."""
    printers = [
        printer
        for printer in state.printers.values()
        if printer.raw_address is not None and printer.raw_port is not None
    ]
    bases = {printer.name: _printer_label(printer.name) for printer in printers}
    counts: dict[str, int] = {}
    for base in bases.values():
        counts[base] = counts.get(base, 0) + 1
    hostnames: dict[str, str] = {}
    for printer in printers:
        base = bases[printer.name]
        if counts[base] > 1:
            digest = hashlib.sha256(printer.name.encode("utf-8")).hexdigest()[:8]
            label = f"{base[:46]}-{digest}-printer"
        else:
            label = f"{base[:55]}-printer"
        hostnames[printer.name] = f"{label}.local"
    return hostnames


def _truncate_utf8(value: str, limit: int) -> str:
    return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore").rstrip()


def _raw_printer_service_names(state: State) -> dict[str, str]:
    printers = [
        printer
        for printer in state.printers.values()
        if printer.raw_address is not None and printer.raw_port is not None
    ]
    candidates: dict[str, str] = {}
    for printer in printers:
        description = printer.description
        cleaned = printer.name if XML_INVALID.search(description) else " ".join(description.split())
        candidates[printer.name] = _truncate_utf8(cleaned or printer.name, 63)
    counts: dict[str, int] = {}
    for candidate in candidates.values():
        key = candidate.casefold()
        counts[key] = counts.get(key, 0) + 1
    names: dict[str, str] = {}
    for printer in printers:
        candidate = candidates[printer.name]
        if counts[candidate.casefold()] > 1:
            suffix = f" ({_truncate_utf8(printer.name, 30)})"
            candidate = f"{_truncate_utf8(candidate, 63 - len(suffix.encode('utf-8')))}{suffix}"
        names[printer.name] = candidate
    return names


def _service_path(service_dir: Path, name: str) -> Path:
    base = _printer_label(name)[:40]
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    return service_dir / f"{SERVICE_PREFIX}{base}-{digest}.service"


def _xml_text(value: str) -> str:
    if XML_INVALID.search(value):
        raise ValidationError("printer display name contains characters that XML cannot represent")
    return escape(value)


def _service_document(name: str, hostname: str, port: int) -> str:
    escaped_name = _xml_text(name)
    escaped_hostname = _xml_text(hostname)
    return f"""<?xml version="1.0" standalone="no"?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name>{escaped_name}</name>
  <service protocol="ipv4">
    <type>_pdl-datastream._tcp</type>
    <host-name>{escaped_hostname}</host-name>
    <port>{port}</port>
    <txt-record>txtvers=1</txt-record>
    <txt-record>ty={escaped_name}</txt-record>
  </service>
</service-group>
"""


def _write_text(path: Path, content: str, *, mode: int = 0o644) -> bool:
    if path.is_symlink():
        raise RuntimeError(f"refusing to replace symbolic link: {path}")
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def _render_hosts(existing: str, mappings: list[tuple[str, str]]) -> str:
    lines = existing.splitlines()
    begins = [index for index, line in enumerate(lines) if line == HOSTS_BEGIN]
    ends = [index for index, line in enumerate(lines) if line == HOSTS_END]
    if len(begins) != len(ends) or len(begins) > 1 or (begins and begins[0] >= ends[0]):
        raise RuntimeError(f"malformed {HOSTS_BEGIN!r} block in {AVAHI_HOSTS_PATH}")
    if begins:
        lines = lines[: begins[0]] + lines[ends[0] + 1 :]
    if mappings:
        lines.extend([HOSTS_BEGIN, *(f"{address} {host}" for address, host in mappings), HOSTS_END])
    return "\n".join(lines) + ("\n" if lines else "")


def _validate_managed_service_path(path: Path, service_dir: Path) -> None:
    if path.parent != service_dir or not (
        path.name.startswith(SERVICE_PREFIX) and path.name.endswith(".service")
    ):
        raise RuntimeError(f"refusing to manage unexpected Avahi service path: {path}")


def reconcile_raw_printer_services(
    runner: Runner,
    state: State,
    *,
    service_dir: Path = AVAHI_SERVICE_DIR,
    hosts_path: Path = AVAHI_HOSTS_PATH,
) -> None:
    """Publish dedicated raw printer endpoints with names through mDNS/DNS-SD."""
    if runner.dry_run:
        return
    if service_dir.is_symlink():
        raise RuntimeError(f"Avahi service directory may not be a symbolic link: {service_dir}")
    if hosts_path.is_symlink():
        raise RuntimeError(f"Avahi hosts file may not be a symbolic link: {hosts_path}")

    hostnames = raw_printer_hostnames(state)
    service_names = _raw_printer_service_names(state)
    desired: dict[Path, str] = {}
    mappings: list[tuple[str, str]] = []
    for name, hostname in sorted(hostnames.items()):
        printer = state.printers[name]
        if printer.raw_address is None or printer.raw_port is None:
            continue
        path = _service_path(service_dir, name)
        desired[path] = _service_document(service_names[name], hostname, printer.raw_port)
        mappings.append((printer.raw_address, hostname))

    tracked = {Path(value) for value in state.avahi_services}
    for path in tracked.union(desired):
        _validate_managed_service_path(path, service_dir)
    for path, content in desired.items():
        if path.exists() and path not in tracked and path.read_text(encoding="utf-8") != content:
            raise RuntimeError(f"refusing to overwrite unmanaged Avahi service: {path}")

    existing_hosts = hosts_path.read_text(encoding="utf-8") if hosts_path.exists() else ""
    rendered_hosts = _render_hosts(existing_hosts, sorted(mappings))
    hosts_mode = stat.S_IMODE(hosts_path.stat().st_mode) if hosts_path.exists() else 0o644
    changed = (
        _write_text(hosts_path, rendered_hosts, mode=hosts_mode)
        if rendered_hosts or hosts_path.exists()
        else False
    )
    for path, content in desired.items():
        changed = _write_text(path, content) or changed
    for path in sorted(tracked - set(desired)):
        if path.is_symlink():
            raise RuntimeError(f"refusing to remove symbolic link: {path}")
        if path.exists():
            path.unlink()
            changed = True
    state.avahi_services = [str(path) for path in sorted(desired)]
    if changed:
        runner.run(["systemctl", "restart", "avahi-daemon.service"])


def remove_managed_printer_services(
    runner: Runner,
    state: State,
    *,
    service_dir: Path = AVAHI_SERVICE_DIR,
    hosts_path: Path = AVAHI_HOSTS_PATH,
) -> None:
    """Remove only DNS-SD files and host mappings recorded as project-managed."""
    if runner.dry_run:
        return
    if service_dir.is_symlink() or hosts_path.is_symlink():
        raise RuntimeError("refusing to remove Avahi configuration through a symbolic link")
    tracked = {Path(value) for value in state.avahi_services}
    for path in tracked:
        _validate_managed_service_path(path, service_dir)
        if path.is_symlink():
            raise RuntimeError(f"refusing to remove symbolic link: {path}")
    existing_hosts = hosts_path.read_text(encoding="utf-8") if hosts_path.exists() else ""
    rendered_hosts = _render_hosts(existing_hosts, [])
    hosts_mode = stat.S_IMODE(hosts_path.stat().st_mode) if hosts_path.exists() else 0o644
    changed = (
        _write_text(hosts_path, rendered_hosts, mode=hosts_mode)
        if rendered_hosts or hosts_path.exists()
        else False
    )
    for path in sorted(tracked):
        if path.exists():
            path.unlink()
            changed = True
    state.avahi_services = []
    if changed:
        runner.run(["systemctl", "restart", "avahi-daemon.service"])
