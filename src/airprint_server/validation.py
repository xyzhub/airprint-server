"""Validation for all data that reaches system commands."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

QUEUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")
PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
ALLOWED_SCHEMES = {"socket", "usb", "ipp", "ipps", "lpd", "http", "https"}
INTERFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,14}$")
PRIVATE_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


class ValidationError(ValueError):
    pass


def queue_name(value: str) -> str:
    if not QUEUE_RE.fullmatch(value) or value in {".", ".."}:
        raise ValidationError(
            "queue name must be 1-127 characters using letters, numbers, '.', '_' or '-'"
        )
    return value


def profile_id(value: str) -> str:
    if not PROFILE_RE.fullmatch(value):
        raise ValidationError("profile id must contain lowercase letters, numbers and hyphens")
    return value


def host(value: str) -> str:
    candidate = value.strip()
    if not candidate or len(candidate) > 253 or any(c in candidate for c in "/:@?#[] \t\r\n"):
        raise ValidationError(f"invalid hostname or IP address: {value!r}")
    try:
        ipaddress.ip_address(candidate)
        return candidate
    except ValueError:
        labels = candidate.rstrip(".").split(".")
        if not labels or not all(HOST_LABEL_RE.fullmatch(label) for label in labels):
            raise ValidationError(f"invalid hostname or IP address: {value!r}") from None
        return candidate


def port(value: int | str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("port must be an integer") from exc
    if not 1 <= parsed <= 65535:
        raise ValidationError("port must be between 1 and 65535")
    return parsed


def virtual_ipv4(value: str) -> str:
    candidate = value.strip()
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError as exc:
        raise ValidationError("virtual printer address must be a private IPv4 address") from exc
    if not isinstance(address, ipaddress.IPv4Address) or not any(
        address in network for network in PRIVATE_IPV4_NETWORKS
    ):
        raise ValidationError("virtual printer address must use a private IPv4 LAN range")
    return str(address)


def network_interface(value: str) -> str:
    candidate = value.strip()
    if not INTERFACE_RE.fullmatch(candidate):
        raise ValidationError(
            "network interface must be 1-15 letters, numbers, '.', '_' or '-'"
        )
    return candidate


def device_uri(value: str, *, allow_custom: bool = False) -> str:
    if not value or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise ValidationError("device URI is empty, too long, or contains control characters")
    parsed = urlsplit(value)
    if not parsed.scheme:
        raise ValidationError("device URI must include a scheme")
    if not allow_custom and parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValidationError(f"unsupported device URI scheme: {parsed.scheme}")
    if parsed.scheme.lower() != "usb" and not (parsed.hostname or parsed.path):
        raise ValidationError("device URI must include a host or path")
    return value


def readable_ppd(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValidationError(f"PPD does not exist or is not a file: {path}")
    if not (path.name.lower().endswith(".ppd") or path.name.lower().endswith(".ppd.gz")):
        raise ValidationError("custom driver file must be a .ppd or .ppd.gz file")
    return path


def socket_uri(hostname: str, number: int | str, *, disable_snmp: bool = False) -> str:
    checked_host = host(hostname)
    checked_port = port(number)
    encoded_host = f"[{checked_host}]" if ":" in checked_host else quote(checked_host, safe=".-")
    suffix = "/?snmp=false" if disable_snmp else ""
    return f"socket://{encoded_host}:{checked_port}{suffix}"
