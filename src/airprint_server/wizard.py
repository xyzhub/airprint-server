"""Interactive printer setup wizard."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from airprint_server.commands import Runner
from airprint_server.discovery import USBDevice, discover_usb, parse_lpinfo_ipp_uris
from airprint_server.profiles import PrinterProfile
from airprint_server.validation import (
    ValidationError,
    device_uri,
    host,
    port,
    queue_name,
    readable_ppd,
    socket_uri,
)

Input = Callable[[str], str]
Output = Callable[[str], None]


class WizardCancelled(RuntimeError):
    """The user exited the wizard without an error."""


@dataclass(frozen=True)
class DriverModel:
    uri: str
    description: str


@dataclass(frozen=True)
class ConnectionChoice:
    connection: str
    uri: str
    device: USBDevice | None = None


@dataclass(frozen=True)
class WizardSelection:
    name: str
    description: str
    profile: str
    connection: str
    device_uri: str
    driver: str | None
    ppd: str | None
    raw_port: int | None = None


def parse_driver_models(output: str) -> list[DriverModel]:
    models: list[DriverModel] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        uri, separator, description = stripped.partition(" ")
        if separator and ":///" in uri:
            models.append(DriverModel(uri, description.strip()))
    return models


def find_driver_models(models: list[DriverModel], device: USBDevice) -> list[DriverModel]:
    words = {
        word.lower()
        for word in re.findall(r"[A-Za-z0-9]+", f"{device.manufacturer} {device.model}")
        if len(word) >= 3 and word.lower() not in {"printer", "series", "thermal"}
    }
    if not words:
        return []

    def score(model: DriverModel) -> int:
        haystack = f"{model.uri} {model.description}".lower()
        return sum(1 for word in words if word in haystack)

    matches = [model for model in models if score(model)]
    return sorted(matches, key=lambda model: (-score(model), model.description.lower()))[:12]


def _input(prompt: str, input_fn: Input) -> str:
    try:
        return input_fn(prompt)
    except EOFError as exc:
        raise WizardCancelled("setup wizard ended because input was closed") from exc


def _choice(
    prompt: str,
    labels: list[str],
    *,
    input_fn: Input,
    output: Output,
) -> int:
    output(prompt)
    for number, label in enumerate(labels, 1):
        output(f"  {number}. {label}")
    while True:
        answer = _input(f"Select 1-{len(labels)}: ", input_fn).strip()
        try:
            selected = int(answer)
        except ValueError:
            selected = 0
        if 1 <= selected <= len(labels):
            return selected - 1
        output(f"Please enter a number from 1 to {len(labels)}.")


def _yes_no(prompt: str, *, default: bool, input_fn: Input) -> bool:
    marker = "[Y/n]" if default else "[y/N]"
    answer = _input(f"{prompt} {marker} ", input_fn).strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def _validated(
    prompt: str,
    validator: Callable[[str], object],
    *,
    default: str | None,
    input_fn: Input,
    output: Output,
) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        answer = _input(f"{prompt}{suffix}: ", input_fn).strip() or (default or "")
        try:
            validator(answer)
            return answer
        except (ValidationError, ValueError) as exc:
            output(f"Invalid value: {exc}")


def choose_connection(
    runner: Runner,
    *,
    input_fn: Input = input,
    output: Output = print,
) -> ConnectionChoice | None:
    try:
        devices = discover_usb(runner)
    except RuntimeError as exc:
        devices = []
        output(f"USB discovery warning: {exc}")
    ipp_result = runner.run(["lpinfo", "-v"], check=False)
    ipp_uris = parse_lpinfo_ipp_uris(ipp_result.stdout) if not ipp_result.returncode else []
    duplicates: dict[tuple[str, str], int] = {}
    for device in devices:
        if not device.serial:
            key = (device.manufacturer, device.model)
            duplicates[key] = duplicates.get(key, 0) + 1
    if any(count > 1 for count in duplicates.values()):
        output(
            "WARNING: Multiple identical USB printers have no serial number; "
            "they cannot be reliably distinguished."
        )
    if (
        not devices
        and runner.run(
            ["systemctl", "is-active", "--quiet", "ipp-usb.service"], check=False
        ).returncode
        == 0
    ):
        output(
            "No direct usb:// printer was found and ipp-usb is active. "
            "Choose a detected IPP URI below, or inspect 'lpinfo -v'."
        )

    labels = [
        (
            f"USB: {device.manufacturer} {device.model}"
            f" — serial {device.serial} — {device.uri}"
            if device.serial
            else (
                f"USB: {device.manufacturer} {device.model} — no serial; "
                f"URI may be unstable — {device.uri}"
            )
        )
        for device in devices
    ]
    labels.extend(f"Detected IPP/IPPS printer: {uri}" for uri in ipp_uris)
    labels.extend(
        [
            "Network printer using raw TCP socket (usually port 9100)",
            "IPP or IPPS printer",
            "LPD or another complete CUPS device URI",
            "Finish without adding another printer",
        ]
    )
    selected = _choice(
        "Choose how the printer is connected:",
        labels,
        input_fn=input_fn,
        output=output,
    )
    if selected < len(devices):
        device = devices[selected]
        return ConnectionChoice("usb", device.uri, device)
    ipp_index = selected - len(devices)
    if 0 <= ipp_index < len(ipp_uris):
        uri = ipp_uris[ipp_index]
        return ConnectionChoice(urlsplit(uri).scheme.lower(), uri)
    action = selected - len(devices) - len(ipp_uris)
    if action == 0:
        hostname = _validated(
            "Printer hostname or IP address",
            host,
            default=None,
            input_fn=input_fn,
            output=output,
        )
        number = _validated(
            "Raw socket port",
            port,
            default="9100",
            input_fn=input_fn,
            output=output,
        )
        disable_snmp = _yes_no(
            "Disable SNMP for this queue?", default=False, input_fn=input_fn
        )
        return ConnectionChoice(
            "socket", socket_uri(hostname, number, disable_snmp=disable_snmp)
        )
    if action == 1:
        uri = _validated(
            "Complete ipp:// or ipps:// device URI",
            lambda value: _validate_ipp_uri(value),
            default=None,
            input_fn=input_fn,
            output=output,
        )
        return ConnectionChoice(urlsplit(uri).scheme.lower(), uri)
    if action == 2:
        uri = _validated(
            "Complete CUPS device URI",
            lambda value: device_uri(value, allow_custom=True),
            default=None,
            input_fn=input_fn,
            output=output,
        )
        scheme = urlsplit(uri).scheme.lower()
        connection = scheme if scheme in {"lpd", "ipp", "ipps", "usb", "socket"} else "custom-uri"
        return ConnectionChoice(connection, uri)
    return None


def _validate_ipp_uri(value: str) -> str:
    checked = device_uri(value)
    if urlsplit(checked).scheme.lower() not in {"ipp", "ipps"}:
        raise ValidationError("URI must begin with ipp:// or ipps://")
    return checked


def _recommended_profile(
    profiles: list[PrinterProfile], choice: ConnectionChoice
) -> PrinterProfile | None:
    device_text = ""
    if choice.device:
        device_text = f"{choice.device.manufacturer} {choice.device.model}".lower()
    preferred = "generic-driverless"
    if "bixolon" in device_text or "srp-e300" in device_text:
        preferred = "bixolon-srp-e300"
    elif "swisspos" in device_text or "spst80" in device_text:
        preferred = "swisspos-t80c"
    elif "xprinter" in device_text or "xp-" in device_text:
        if "58" in device_text:
            preferred = "xprinter-58mm"
        elif "76" in device_text:
            preferred = "xprinter-76mm"
        else:
            preferred = "xprinter-80mm"
    elif "58" in device_text:
        preferred = "escpos-generic-58mm"
    elif any(word in device_text for word in ("pos", "receipt", "thermal", "80c")):
        preferred = "escpos-generic-80mm"
    return next((profile for profile in profiles if profile.id == preferred), None)


def choose_profile(
    profiles: dict[str, PrinterProfile],
    connection: ConnectionChoice,
    *,
    input_fn: Input = input,
    output: Output = print,
) -> PrinterProfile:
    supported = sorted(
        (
            profile
            for profile in profiles.values()
            if connection.connection in profile.supported_connections
        ),
        key=lambda profile: profile.display_name.lower(),
    )
    if not supported:
        raise RuntimeError(f"no profiles support the {connection.connection} connection")
    recommended = _recommended_profile(supported, connection)
    if recommended:
        supported.remove(recommended)
        supported.insert(0, recommended)
    labels = [
        f"{profile.display_name} [{profile.status}]"
        + (" — suggested" if profile is recommended else "")
        for profile in supported
    ]
    selected = _choice(
        "Choose the closest printer profile. Generic ESC/POS profiles require testing:",
        labels,
        input_fn=input_fn,
        output=output,
    )
    return supported[selected]


def choose_driver(
    runner: Runner,
    profile: PrinterProfile,
    connection: ConnectionChoice,
    *,
    preferred_ppd: Path | None = None,
    input_fn: Input = input,
    output: Output = print,
) -> tuple[str | None, str | None]:
    if preferred_ppd is not None:
        selected_ppd = str(readable_ppd(str(preferred_ppd)))
        output(f"Using installed vendor PPD: {selected_ppd}")
        return None, selected_ppd
    if profile.driver:
        output(f"Using profile driver: {profile.driver}")
        return profile.driver, None
    if connection.connection in {"ipp", "ipps"}:
        output("Using CUPS driverless IPP Everywhere mode.")
        return None, None

    result = runner.run(["lpinfo", "-m"], check=False)
    matches = (
        find_driver_models(parse_driver_models(result.stdout), connection.device)
        if connection.device
        else []
    )
    labels = [f"{model.description} ({model.uri})" for model in matches]
    labels.extend(["Enter an installed CUPS model URI", "Use a vendor PPD file"])
    selected = _choice(
        "This profile needs a printer driver. Choose one:",
        labels,
        input_fn=input_fn,
        output=output,
    )
    if selected < len(matches):
        return matches[selected].uri, None
    if selected == len(matches):
        driver = _validated(
            "Installed CUPS model URI from 'lpinfo -m'",
            lambda value: value if ":///" in value else _invalid_driver(),
            default=None,
            input_fn=input_fn,
            output=output,
        )
        return driver, None
    ppd = _validated(
        "Path to vendor .ppd or .ppd.gz file",
        readable_ppd,
        default=None,
        input_fn=input_fn,
        output=output,
    )
    return None, ppd


def _invalid_driver() -> str:
    raise ValidationError("expected a CUPS model URI such as drv:///vendor/model.ppd")


def _suggested_queue_name(choice: ConnectionChoice) -> str:
    source = (
        f"{choice.device.manufacturer}-{choice.device.model}"
        if choice.device
        else "AirPrint-Printer"
    )
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", source).strip(".-_")[:60]
    return cleaned or "AirPrint-Printer"


def collect_printer(
    runner: Runner,
    profiles: dict[str, PrinterProfile],
    *,
    preferred_ppds: Mapping[str, Path] | None = None,
    used_raw_ports: set[int] | None = None,
    raw_port_owners: Mapping[int, str] | None = None,
    input_fn: Input = input,
    output: Output = print,
) -> WizardSelection | None:
    connection = choose_connection(runner, input_fn=input_fn, output=output)
    if connection is None:
        return None
    profile = choose_profile(profiles, connection, input_fn=input_fn, output=output)
    driver, ppd = choose_driver(
        runner,
        profile,
        connection,
        preferred_ppd=(preferred_ppds or {}).get(profile.id),
        input_fn=input_fn,
        output=output,
    )
    name = _validated(
        "CUPS queue name",
        queue_name,
        default=_suggested_queue_name(connection),
        input_fn=input_fn,
        output=output,
    )
    description = _validated(
        "AirPrint display name",
        lambda value: value.strip() or _invalid_description(),
        default=profile.display_name,
        input_fn=input_fn,
        output=output,
    )
    raw_port: int | None = None
    occupied = set(used_raw_ports or ())
    occupied.update(
        number for number, owner in (raw_port_owners or {}).items() if owner != name
    )
    if _yes_no(
        "Expose this queue as a raw TCP/JetDirect network printer?",
        default=False,
        input_fn=input_fn,
    ):
        suggested = next(
            candidate for candidate in range(9100, 65536) if candidate not in occupied
        )

        def available_raw_port(value: str) -> int:
            selected = port(value)
            if selected in occupied:
                raise ValidationError(f"raw TCP port {selected} is already assigned")
            return selected

        raw_port = int(
            _validated(
                "Raw TCP listen port",
                available_raw_port,
                default=str(suggested),
                input_fn=input_fn,
                output=output,
            )
        )
    return WizardSelection(
        name=name,
        description=description,
        profile=profile.id,
        connection=connection.connection,
        device_uri=connection.uri,
        driver=driver,
        ppd=ppd,
        raw_port=raw_port,
    )


def _invalid_description() -> str:
    raise ValidationError("description may not be empty")


def configure_existing_raw_exposure(
    managed_raw_ports: Mapping[str, int | None],
    set_raw_exposure: Callable[[str, int | None], None],
    *,
    input_fn: Input = input,
    output: Output = print,
) -> None:
    if not managed_raw_ports or not _yes_no(
        "Configure standard Ethernet-printer access for an existing queue?",
        default=False,
        input_fn=input_fn,
    ):
        return
    names = sorted(managed_raw_ports)
    labels = [
        f"{name} — raw TCP port {managed_raw_ports[name]}"
        if managed_raw_ports[name] is not None
        else f"{name} — AirPrint only"
        for name in names
    ]
    name = names[
        _choice(
            "Choose an existing managed printer:",
            labels,
            input_fn=input_fn,
            output=output,
        )
    ]
    current = managed_raw_ports[name]
    if not _yes_no(
        f"Make {name} available as a standard raw TCP/JetDirect printer?",
        default=True,
        input_fn=input_fn,
    ):
        set_raw_exposure(name, None)
        return
    occupied = {
        number
        for queue, number in managed_raw_ports.items()
        if queue != name and number is not None
    }
    suggested = current or next(
        candidate for candidate in range(9100, 65536) if candidate not in occupied
    )

    def available_raw_port(value: str) -> int:
        selected = port(value)
        if selected in occupied:
            raise ValidationError(f"raw TCP port {selected} is already assigned")
        return selected

    selected_port = int(
        _validated(
            "Raw TCP listen port",
            available_raw_port,
            default=str(suggested),
            input_fn=input_fn,
            output=output,
        )
    )
    set_raw_exposure(name, selected_port)


def run_wizard(
    runner: Runner,
    profiles: dict[str, PrinterProfile],
    add_printer: Callable[[WizardSelection], None],
    *,
    preferred_ppds: Mapping[str, Path] | None = None,
    used_raw_ports: set[int] | None = None,
    raw_port_owners: Mapping[int, str] | None = None,
    managed_raw_ports: Mapping[str, int | None] | None = None,
    set_raw_exposure: Callable[[str, int | None], None] | None = None,
    input_fn: Input = input,
    output: Output = print,
) -> None:
    occupied_raw_port_owners = dict(raw_port_owners or {})
    for number in used_raw_ports or ():
        occupied_raw_port_owners.setdefault(number, "")
    output("")
    output("airprint-server setup wizard")
    output("============================")
    output("The wizard will discover printers and create only the queues you confirm.")
    output("Supported vendor drivers will be offered for download when they are needed.")
    if managed_raw_ports and set_raw_exposure:
        def record_raw_exposure(name: str, number: int | None) -> None:
            set_raw_exposure(name, number)
            for assigned, owner in list(occupied_raw_port_owners.items()):
                if owner == name:
                    del occupied_raw_port_owners[assigned]
            if number is not None:
                occupied_raw_port_owners[number] = name

        configure_existing_raw_exposure(
            managed_raw_ports,
            record_raw_exposure,
            input_fn=input_fn,
            output=output,
        )
    while True:
        selection = collect_printer(
            runner,
            profiles,
            preferred_ppds=preferred_ppds,
            raw_port_owners=occupied_raw_port_owners,
            input_fn=input_fn,
            output=output,
        )
        if selection is None:
            output("Setup wizard finished.")
            return
        add_printer(selection)
        occupied_raw_port_owners = {
            number: owner
            for number, owner in occupied_raw_port_owners.items()
            if owner != selection.name
        }
        if selection.raw_port is not None:
            occupied_raw_port_owners[selection.raw_port] = selection.name
        if not _yes_no("Add another printer?", default=False, input_fn=input_fn):
            output("Setup wizard finished.")
            return
