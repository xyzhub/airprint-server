"""Host installation, CUPS configuration, and safe uninstall operations."""

from __future__ import annotations

import os
import platform
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from airprint_server import avahi, cups
from airprint_server.bixolon_driver import BIXOLON_CUPS_OPTIONS, remove_bixolon_driver
from airprint_server.commands import Runner
from airprint_server.config import (
    CONFIG_DIR,
    STATE_DIR,
    State,
    initialize_config,
    save_state,
)
from airprint_server.xprinter_driver import remove_xprinter_driver

RUNTIME_PACKAGES = [
    "cups",
    "cups-client",
    "cups-bsd",
    "cups-filters",
    "cups-filters-core-drivers",
    "avahi-daemon",
    "avahi-utils",
    "git",
    "python3",
    "python3-venv",
    "python3-yaml",
]
BUILD_PACKAGES = ["build-essential", "libcups2-dev", "cups-ppdc"]
SUPPORTED_IDS = {"debian", "raspbian"}
SUPPORTED_VERSIONS = {"12", "13"}
INSTALL_PHASES = 7
InstallProgress = Callable[[int, str], None]
LEGACY_BIXOLON_PAGE_SIZE = "61X72MMY70MM"


def _report(progress: InstallProgress | None, completed: int, label: str) -> None:
    if progress is not None:
        progress(completed, label)


def require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("this operation requires root; rerun with sudo")


def operating_system(os_release: Path = Path("/etc/os-release")) -> tuple[bool, str]:
    values: dict[str, str] = {}
    if os_release.exists():
        for line in os_release.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip('"')
    identifier = values.get("ID", "").lower()
    version = values.get("VERSION_ID", "")
    supported = identifier in SUPPORTED_IDS and version in SUPPORTED_VERSIONS
    label = values.get("PRETTY_NAME", platform.platform())
    return supported, label


def install_package_list(runner: Runner, state: State, packages: list[str]) -> None:
    missing = [
        package
        for package in packages
        if runner.run(["dpkg-query", "-W", "-f=${Status}", package], check=False).stdout.strip()
        != "install ok installed"
    ]
    if not missing:
        return
    runner.run(["apt-get", "update"])
    runner.run(["apt-get", "install", "-y", "--no-install-recommends", *missing])
    state.installed_packages = sorted(set(state.installed_packages).union(missing))


def install_packages(runner: Runner, state: State, *, with_build: bool = True) -> None:
    packages = RUNTIME_PACKAGES + (BUILD_PACKAGES if with_build else [])
    install_package_list(runner, state, packages)


def configure_cups(runner: Runner) -> None:
    """Use supported cupsctl settings; never enable remote administration."""
    runner.run(
        [
            "cupsctl",
            "--share-printers",
            "--remote-any",
            "--no-remote-admin",
            "--no-user-cancel-any",
        ]
    )
    valid, detail = cups.validate_cups(runner)
    if not valid:
        raise RuntimeError(f"CUPS configuration validation failed: {detail}")
    runner.run(["systemctl", "reload-or-restart", "cups.service"])


def ipp_usb_state(runner: Runner) -> dict[str, bool]:
    installed = runner.run(["dpkg-query", "-W", "ipp-usb"], check=False).returncode == 0
    active = installed and avahi.service_active(runner, "ipp-usb.service")
    enabled = installed and avahi.service_enabled(runner, "ipp-usb.service")
    return {"installed": installed, "active": active, "enabled": enabled}


def disable_ipp_usb(runner: Runner, state: State) -> None:
    previous = ipp_usb_state(runner)
    if not previous["installed"]:
        return
    state.ipp_usb_previous = previous
    runner.run(["systemctl", "disable", "--now", "ipp-usb.service"])


def restore_ipp_usb(runner: Runner, state: State) -> None:
    previous = state.ipp_usb_previous
    if not previous:
        return
    if previous.get("enabled") and previous.get("active"):
        runner.run(["systemctl", "enable", "--now", "ipp-usb.service"])
    elif previous.get("enabled"):
        runner.run(["systemctl", "enable", "ipp-usb.service"])
    elif previous.get("active"):
        runner.run(["systemctl", "start", "ipp-usb.service"])
    state.ipp_usb_previous = None


def migrate_managed_printer_defaults(runner: Runner, state: State) -> list[str]:
    """Apply narrowly scoped profile corrections without replacing user overrides."""
    migrated: list[str] = []
    bixolon = state.vendor_drivers.get("bixolon-pos-cups", {})
    official_ppd = bixolon.get("ppd_path")
    if not official_ppd:
        return migrated
    for printer in state.printers.values():
        if (
            printer.profile != "bixolon-srp-e300"
            or printer.ppd != official_ppd
            or printer.cups_options.get("PageSize") != LEGACY_BIXOLON_PAGE_SIZE
            or not cups.queue_exists(runner, printer.name)
        ):
            continue
        effective = cups.printer_attributes(runner, printer.name)
        if effective.get("PageSize") != LEGACY_BIXOLON_PAGE_SIZE:
            continue
        printer.cups_options["PageSize"] = BIXOLON_CUPS_OPTIONS["PageSize"]
        cups.create_or_update_queue(runner, printer)
        migrated.append(printer.name)
    return migrated


def rastertoescpos_presence(runner: Runner) -> tuple[bool, bool]:
    filter_result = runner.run(
        ["find", "/usr/lib/cups/filter", "/usr/libexec/cups/filter", "-name", "rastertoescpos"],
        check=False,
    )
    models = runner.run(["lpinfo", "-m"], check=False).stdout
    has_filter = bool(filter_result.stdout.strip())
    has_models = all(
        model in models
        for model in ("drv:///escpos.drv/gp58130.ppd", "drv:///escpos.drv/gp80160.ppd")
    )
    return has_filter, has_models


def rastertoescpos_available(runner: Runner) -> bool:
    has_filter, has_models = rastertoescpos_presence(runner)
    return has_filter and has_models


def install(
    runner: Runner,
    state: State,
    *,
    install_escpos: bool = True,
    script_dir: Path | None = None,
    progress: InstallProgress | None = None,
) -> None:
    require_root()
    _report(progress, 0, "Checking host compatibility")
    supported, label = operating_system()
    if not supported:
        raise RuntimeError(
            f"unsupported operating system: {label}; "
            "expected Debian/Raspberry Pi OS 12 (Bookworm) or 13 (Trixie)"
        )
    _report(progress, 1, "Installing system packages")
    install_packages(runner, state, with_build=False)
    _report(progress, 2, "Preparing airprint-server configuration")
    if not runner.dry_run:
        initialize_config()
    _report(progress, 3, "Starting CUPS and Avahi services")
    avahi.ensure_services(runner)
    _report(progress, 4, "Configuring CUPS printer sharing")
    configure_cups(runner)
    _report(
        progress,
        5,
        "Checking ESC/POS printer driver"
        if install_escpos
        else "Skipping optional ESC/POS printer driver",
    )
    if install_escpos and not rastertoescpos_available(runner):
        _report(progress, 5, "Building ESC/POS printer driver")
        install_package_list(runner, state, BUILD_PACKAGES)
        had_filter, had_models = rastertoescpos_presence(runner)
        independently_present = had_filter or had_models
        build_script = script_dir / "build-rastertoescpos.sh" if script_dir else None
        if build_script and build_script.is_file():
            runner.run([str(build_script)])
        else:
            with tempfile.TemporaryDirectory(prefix="airprint-server-build-") as temporary:
                source = Path(temporary) / "rastertoescpos"
                runner.run(
                    [
                        "git",
                        "clone",
                        "--depth",
                        "1",
                        "https://github.com/chunlinyao/rastertoescpos.git",
                        str(source),
                    ]
                )
                runner.run(["make", "-C", str(source)])
                runner.run(["make", "-C", str(source), "install"])
        if not runner.dry_run and not rastertoescpos_available(runner):
            raise RuntimeError(
                "rastertoescpos build completed but the filter or expected PPD models are missing"
            )
        if not independently_present:
            state.rastertoescpos_managed = True
            state.rastertoescpos_source = "https://github.com/chunlinyao/rastertoescpos"
    _report(progress, 6, "Updating managed printer defaults")
    if not runner.dry_run:
        migrate_managed_printer_defaults(runner, state)
        save_state(state)
    _report(progress, INSTALL_PHASES, "Installation complete")


def uninstall(
    runner: Runner,
    state: State,
    *,
    remove_queues: bool,
    remove_config: bool,
    remove_state: bool,
    remove_escpos: bool,
    confirm: Callable[[str], bool],
) -> None:
    require_root()
    if remove_queues and state.printers and confirm(
        f"Remove {len(state.printers)} managed CUPS queue(s)?"
    ):
        for name in list(state.printers):
            if cups.queue_exists(runner, name):
                cups.remove_queue(runner, name)
            del state.printers[name]
    if remove_escpos and state.rastertoescpos_managed and confirm(
        "Remove rastertoescpos files installed by this project?"
    ):
        for path in (
            "/usr/lib/cups/filter/rastertoescpos",
            "/usr/libexec/cups/filter/rastertoescpos",
            "/usr/share/cups/drv/escpos.drv",
            "/usr/share/cups/ppdc/escposmedia.h",
        ):
            candidate = Path(path)
            if candidate.exists() and not runner.dry_run:
                candidate.unlink()
        state.rastertoescpos_managed = False
    if remove_escpos and "bixolon-pos-cups" in state.vendor_drivers and confirm(
        "Remove the BIXOLON CUPS driver installed by this project?"
    ):
        remove_bixolon_driver(state)
    if remove_escpos and "xprinter-pos-cups" in state.vendor_drivers and confirm(
        "Remove the XPrinter CUPS driver installed by this project?"
    ):
        remove_xprinter_driver(state)
    restore_ipp_usb(runner, state)
    if (
        remove_config
        and confirm(f"Remove configuration directory {CONFIG_DIR}?")
        and CONFIG_DIR.exists()
        and not runner.dry_run
    ):
        shutil.rmtree(CONFIG_DIR)
    if remove_state and confirm(f"Remove state directory {STATE_DIR}?"):
        if STATE_DIR.exists() and not runner.dry_run:
            shutil.rmtree(STATE_DIR)
    elif STATE_DIR.exists() and not runner.dry_run:
        save_state(state)
