"""Command-line interface."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from airprint_server import DESCRIPTION, __version__, cups, installer
from airprint_server.bixolon_driver import (
    BIXOLON_CUPS_OPTIONS,
    BixolonInstallation,
    download_bixolon_archive,
    install_bixolon_driver,
    installed_bixolon_ppd,
)
from airprint_server.commands import CommandError, Runner
from airprint_server.config import (
    PROFILE_DIR,
    ManagedPrinter,
    State,
    load_state,
    save_state,
)
from airprint_server.diagnostics import diagnose, recent_logs
from airprint_server.discovery import discover_network, discover_usb, select_usb
from airprint_server.profiles import PrinterProfile, load_profiles
from airprint_server.progress import ProgressBar
from airprint_server.testprint import submit_cutter_test, submit_test
from airprint_server.updater import update_project
from airprint_server.validation import (
    ValidationError,
    device_uri,
    host,
    port,
    queue_name,
    readable_ppd,
    socket_uri,
)
from airprint_server.wizard import WizardSelection, run_wizard
from airprint_server.xprinter_driver import (
    XPRINTER_CUPS_OPTIONS,
    XPrinterInstallation,
    download_xprinter_package,
    install_xprinter_driver,
    installed_xprinter_ppd,
)

LOG = logging.getLogger("airprint-server")
XPRINTER_PROFILE_MODELS = {
    "xprinter-58mm": "58",
    "xprinter-76mm": "76",
    "xprinter-80mm": "80",
}


def _root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("this command changes system configuration; rerun it with sudo")


def _confirm(message: str, yes: bool) -> bool:
    return yes or input(f"{message} [y/N] ").strip().lower() in {"y", "yes"}


def _install_bixolon_source(
    runner: Runner,
    state: State,
    archive: Path | None,
) -> BixolonInstallation | None:
    if archive is None and runner.dry_run:
        print("Would download and verify BIXOLON Linux POS CUPS driver v1.5.9.")
        return None
    if archive is not None:
        installed = install_bixolon_driver(runner, state, archive)
    else:
        print("Downloading the official BIXOLON Linux POS CUPS driver...")
        with tempfile.TemporaryDirectory(prefix="airprint-server-bixolon-") as temporary:
            downloaded = download_bixolon_archive(Path(temporary))
            installed = install_bixolon_driver(runner, state, downloaded)
    if not runner.dry_run:
        save_state(state)
    return installed


def _install_xprinter_source(
    runner: Runner,
    state: State,
    package: Path | None,
) -> XPrinterInstallation | None:
    if runner.dry_run:
        print("Would download if needed and verify XPrinter POS CUPS driver v3.13.11.")
        return None
    if package is not None:
        installed = install_xprinter_driver(runner, state, package)
    else:
        print("Downloading the official XPrinter POS CUPS driver...")
        with tempfile.TemporaryDirectory(prefix="airprint-server-xprinter-") as temporary:
            downloaded = download_xprinter_package(Path(temporary))
            installed = install_xprinter_driver(runner, state, downloaded)
    save_state(state)
    return installed


def _profile(
    args: argparse.Namespace, profiles: dict[str, PrinterProfile]
) -> PrinterProfile | None:
    identifier = getattr(args, "profile", None)
    if not identifier:
        return None
    try:
        return profiles[identifier]
    except KeyError as exc:
        raise ValidationError(
            f"unknown profile {identifier!r}; use 'airprint-server list-profiles'"
        ) from exc


def _connection_uri(args: argparse.Namespace, runner: Runner) -> tuple[str, str]:
    if args.device_uri:
        uri = device_uri(args.device_uri, allow_custom=args.connection == "custom-uri")
        connection = args.connection or urlsplit(uri).scheme.lower()
        return uri, connection
    connection = args.connection
    if connection == "socket":
        if not args.host:
            raise ValidationError("--host is required for a socket connection")
        return socket_uri(args.host, args.port, disable_snmp=args.disable_snmp), connection
    if connection == "usb":
        if not sys.stdin.isatty():
            raise ValidationError(
                "USB selection needs a terminal; use --device-uri non-interactively"
            )
        return select_usb(discover_usb(runner)).uri, connection
    if connection in {"ipp", "ipps", "lpd", "custom-uri"}:
        raise ValidationError(f"--device-uri is required for {connection}")
    raise ValidationError("--connection or --device-uri is required")


def cmd_add(
    args: argparse.Namespace,
    runner: Runner,
    state: State,
    profiles: dict[str, PrinterProfile],
) -> None:
    _root()
    name = queue_name(args.name)
    selected = _profile(args, profiles)
    uri, connection = _connection_uri(args, runner)
    if selected and connection not in selected.supported_connections:
        raise ValidationError(f"profile {selected.id} does not support {connection}")
    if name in state.printers and state.printers[name].adopted and not args.adopt:
        raise ValidationError("queue was adopted; pass --adopt to explicitly update it")
    if cups.queue_exists(runner, name) and name not in state.printers and not args.adopt:
        raise ValidationError(
            f"CUPS queue {name!r} already exists and is unmanaged; use adopt-printer first"
        )
    official_bixolon_ppd = installed_bixolon_ppd(state)
    can_use_automatic_bixolon = bool(
        selected
        and selected.id == "bixolon-srp-e300"
        and not official_bixolon_ppd
        and not args.ppd
        and (not args.driver or args.driver == selected.driver)
    )
    if can_use_automatic_bixolon and _confirm(
        "Download and install BIXOLON's official driver under its license?",
        args.yes,
    ):
        installed = _install_bixolon_source(runner, state, None)
        if installed:
            official_bixolon_ppd = installed.ppd_path
    use_official_bixolon = bool(
        selected
        and selected.id == "bixolon-srp-e300"
        and official_bixolon_ppd
        and (not args.driver or args.driver == selected.driver)
        and (not args.ppd or Path(args.ppd) == official_bixolon_ppd)
    )
    xprinter_model = XPRINTER_PROFILE_MODELS.get(selected.id) if selected else None
    official_xprinter_ppd = (
        installed_xprinter_ppd(state, xprinter_model) if xprinter_model else None
    )
    can_use_automatic_xprinter = bool(
        selected
        and xprinter_model
        and not official_xprinter_ppd
        and not args.ppd
        and (not args.driver or args.driver == selected.driver)
    )
    if can_use_automatic_xprinter and _confirm(
        "Download and install XPrinter's official driver under its license?",
        args.yes,
    ):
        installed_xprinter = _install_xprinter_source(runner, state, None)
        if installed_xprinter and xprinter_model:
            official_xprinter_ppd = installed_xprinter.ppd_paths[xprinter_model]
    use_official_xprinter = bool(
        selected
        and xprinter_model
        and official_xprinter_ppd
        and (not args.driver or args.driver == selected.driver)
        and (not args.ppd or Path(args.ppd) == official_xprinter_ppd)
    )
    ppd_source = args.ppd or (
        str(official_bixolon_ppd)
        if use_official_bixolon
        else str(official_xprinter_ppd)
        if use_official_xprinter
        else None
    )
    ppd = str(readable_ppd(ppd_source)) if ppd_source else None
    driver = (
        None
        if use_official_bixolon or use_official_xprinter
        else args.driver or (None if ppd else selected.driver if selected else None)
    )
    options = (
        dict(BIXOLON_CUPS_OPTIONS)
        if use_official_bixolon
        else dict(XPRINTER_CUPS_OPTIONS[xprinter_model])
        if use_official_xprinter and xprinter_model
        else selected.default_options()
        if selected
        else {}
    )
    description = args.description or (selected.display_name if selected else name)
    managed = ManagedPrinter(
        name=name,
        description=description,
        profile=selected.id if selected else None,
        device_uri=uri,
        connection=connection,
        driver=driver,
        ppd=ppd,
        cups_options=options,
        adopted=bool(args.adopt),
    )
    print(f"Queue: {name}\nURI: {uri}\nDriver: {ppd or driver or 'driverless'}")
    if not _confirm("Create or update this queue?", args.yes):
        print("Cancelled.")
        return
    cups.create_or_update_queue(runner, managed)
    state.printers[name] = managed
    if not runner.dry_run:
        save_state(state)
    print(f"Managed and shared CUPS queue {name}.")


def cmd_adopt(args: argparse.Namespace, runner: Runner, state: State) -> None:
    _root()
    name = queue_name(args.name)
    if name in state.printers:
        raise ValidationError(f"{name} is already managed")
    if not cups.queue_exists(runner, name):
        raise ValidationError(f"CUPS queue {name!r} does not exist")
    result = runner.run(["lpstat", "-v", name])
    prefix = f"device for {name}: "
    uri = next(
        (line[len(prefix) :] for line in result.stdout.splitlines() if line.startswith(prefix)),
        None,
    )
    if not uri:
        raise RuntimeError(f"could not inspect the device URI for {name}")
    managed = ManagedPrinter(name, name, None, uri, urlsplit(uri).scheme, adopted=True)
    state.printers[name] = managed
    if not runner.dry_run:
        save_state(state)
    print(f"Adopted {name} without changing its CUPS configuration.")


def cmd_setup(
    args: argparse.Namespace,
    runner: Runner,
    state: State,
    profiles: dict[str, PrinterProfile],
) -> None:
    _root()

    def add_selection(selection: WizardSelection) -> None:
        add_args = argparse.Namespace(
            name=selection.name,
            description=selection.description,
            profile=selection.profile,
            connection=selection.connection,
            host=None,
            port=9100,
            disable_snmp=False,
            device_uri=selection.device_uri,
            driver=selection.driver,
            ppd=selection.ppd,
            disable_ipp_usb=False,
            adopt=False,
            yes=args.yes,
        )
        cmd_add(add_args, runner, state, profiles)

    preferred_ppds: dict[str, Path] = {}
    official_ppd = installed_bixolon_ppd(state)
    if official_ppd:
        preferred_ppds["bixolon-srp-e300"] = official_ppd
    for profile_id, model in XPRINTER_PROFILE_MODELS.items():
        xprinter_ppd = installed_xprinter_ppd(state, model)
        if xprinter_ppd:
            preferred_ppds[profile_id] = xprinter_ppd
    run_wizard(runner, profiles, add_selection, preferred_ppds=preferred_ppds)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--dry-run", action="store_true", help="show commands without executing them"
    )
    common.add_argument("--yes", action="store_true", help="answer yes to confirmations")
    parser = argparse.ArgumentParser(prog="airprint-server", description=DESCRIPTION)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="count", default=0)
    sub = parser.add_subparsers(dest="command", required=True)
    install_parser = sub.add_parser("install", parents=[common])
    install_parser.add_argument(
        "--without-escpos", action="store_true"
    )
    wizard_mode = install_parser.add_mutually_exclusive_group()
    wizard_mode.add_argument(
        "--wizard",
        action="store_true",
        help="run interactive printer setup even when input is not a terminal",
    )
    wizard_mode.add_argument(
        "--no-wizard",
        action="store_false",
        dest="wizard",
        help="install components without interactive printer setup",
    )
    install_parser.set_defaults(wizard=None)
    uninstall_parser = sub.add_parser("uninstall", parents=[common])
    uninstall_parser.add_argument("--keep-queues", action="store_true")
    uninstall_parser.add_argument("--keep-config", action="store_true")
    uninstall_parser.add_argument("--keep-state", action="store_true")
    uninstall_parser.add_argument("--keep-escpos", action="store_true")
    add = sub.add_parser("add-printer", parents=[common])
    add.add_argument("--name", required=True)
    add.add_argument("--description")
    add.add_argument("--profile")
    add.add_argument(
        "--connection", choices=["socket", "usb", "ipp", "ipps", "lpd", "custom-uri"]
    )
    add.add_argument("--host", type=host)
    add.add_argument("--port", type=port, default=9100)
    add.add_argument("--disable-snmp", action="store_true")
    add.add_argument("--device-uri")
    driver = add.add_mutually_exclusive_group()
    driver.add_argument("--driver")
    driver.add_argument("--ppd")
    add.add_argument("--disable-ipp-usb", action="store_true")
    add.add_argument("--adopt", action="store_true", help=argparse.SUPPRESS)
    remove = sub.add_parser("remove-printer", parents=[common])
    remove.add_argument("name")
    sub.add_parser("list-printers", parents=[common])
    sub.add_parser("discover", parents=[common])
    sub.add_parser("discover-usb", parents=[common])
    sub.add_parser("list-profiles", parents=[common])
    sub.add_parser("setup", parents=[common], help="run the interactive printer setup wizard")
    update = sub.add_parser("update", parents=[common], help="securely update from GitHub")
    update.add_argument(
        "--check",
        action="store_true",
        help="check whether an update is available without changing the system",
    )
    bixolon = sub.add_parser(
        "install-bixolon-driver",
        parents=[common],
        help="download and install the official BIXOLON Linux CUPS driver",
    )
    bixolon.add_argument(
        "archive",
        type=Path,
        nargs="?",
        help="optional local v1.5.9 archive; otherwise download it from BIXOLON",
    )
    xprinter = sub.add_parser(
        "install-xprinter-driver",
        parents=[common],
        help="download and install the official XPrinter POS CUPS driver",
    )
    xprinter.add_argument(
        "package",
        type=Path,
        nargs="?",
        help="optional local v3.13.11 RAR or Debian package; otherwise download it",
    )
    test = sub.add_parser("test", parents=[common])
    test.add_argument("--printer", required=True)
    test.add_argument("--test-cutter", action="store_true")
    diagnostic = sub.add_parser("diagnose", parents=[common])
    diagnostic.add_argument("--printer")
    diagnostic.add_argument("--logs", action="store_true")
    sub.add_parser("status", parents=[common])
    adopt = sub.add_parser("adopt-printer", parents=[common])
    adopt.add_argument("name")
    return parser


def _dispatch(args: argparse.Namespace) -> None:
    runner = Runner(dry_run=args.dry_run)
    state = load_state()
    profiles = load_profiles(PROFILE_DIR)
    if args.command == "install":
        animated = sys.stderr.isatty() and not args.dry_run and args.verbose == 0
        with ProgressBar(installer.INSTALL_PHASES, interactive=animated) as progress:
            installer.install(
                runner,
                state,
                install_escpos=not args.without_escpos,
                script_dir=Path(
                    os.environ.get(
                        "AIRPRINT_SERVER_SOURCE_DIR", str(Path(__file__).resolve().parents[2])
                    )
                )
                / "scripts",
                progress=progress.update,
            )
        use_wizard = args.wizard is True or (args.wizard is None and sys.stdin.isatty())
        if use_wizard:
            cmd_setup(args, runner, state, profiles)
        else:
            print("Run 'sudo airprint-server setup' later to add a printer interactively.")
    elif args.command == "uninstall":
        installer.uninstall(
            runner,
            state,
            remove_queues=not args.keep_queues,
            remove_config=not args.keep_config,
            remove_state=not args.keep_state,
            remove_escpos=not args.keep_escpos,
            confirm=lambda message: _confirm(message, args.yes),
        )
        print("Uninstallation complete.")
    elif args.command == "add-printer":
        if args.disable_ipp_usb:
            _root()
            if not _confirm(
                "Disable ipp-usb and record its current state? This can affect other printers.",
                args.yes,
            ):
                raise RuntimeError("ipp-usb change declined")
            installer.disable_ipp_usb(runner, state)
            if not runner.dry_run:
                save_state(state)
        cmd_add(args, runner, state, profiles)
    elif args.command == "setup":
        cmd_setup(args, runner, state, profiles)
    elif args.command == "update":
        _root()
        result = update_project(
            runner,
            confirm=lambda message: _confirm(message, args.yes),
            check_only=args.check,
        )
        if args.check:
            if result.update_available:
                print(f"Update available: {result.available_revision[:12]}")
            else:
                print(f"airprint-server is up to date: {result.available_revision[:12]}")
        elif result.cancelled:
            print("Update cancelled.")
        elif result.dry_run:
            print(f"Would update to {result.available_revision[:12]}.")
        elif result.changed:
            print(f"Updated airprint-server to {result.available_revision[:12]}.")
        else:
            print(f"airprint-server is already up to date: {result.available_revision[:12]}")
    elif args.command == "install-bixolon-driver":
        _root()
        if not _confirm(
            "Download if needed and install BIXOLON's proprietary driver under its license?",
            args.yes,
        ):
            print("BIXOLON driver installation cancelled.")
            return
        installed = _install_bixolon_source(runner, state, args.archive)
        if installed is None:
            return
        if runner.dry_run:
            print(
                f"Would install BIXOLON {installed.version} for {installed.architecture}: "
                f"{installed.ppd_path}"
            )
            return
        managed_bixolon = [
            printer
            for printer in state.printers.values()
            if printer.profile == "bixolon-srp-e300"
        ]
        if managed_bixolon and _confirm(
            f"Apply the official driver to {len(managed_bixolon)} managed SRP-E300 queue(s)?",
            args.yes,
        ):
            for printer in managed_bixolon:
                printer.driver = None
                printer.ppd = str(installed.ppd_path)
                printer.cups_options = dict(BIXOLON_CUPS_OPTIONS)
                cups.create_or_update_queue(runner, printer)
        save_state(state)
        print(
            f"Installed BIXOLON {installed.version} for {installed.architecture}; "
            f"PPD: {installed.ppd_path}"
        )
    elif args.command == "install-xprinter-driver":
        _root()
        if not _confirm(
            "Download if needed and install XPrinter's proprietary driver under its license?",
            args.yes,
        ):
            print("XPrinter driver installation cancelled.")
            return
        installed_xprinter = _install_xprinter_source(runner, state, args.package)
        if installed_xprinter is None:
            return
        managed_xprinter = [
            (printer, XPRINTER_PROFILE_MODELS[printer.profile])
            for printer in state.printers.values()
            if printer.profile in XPRINTER_PROFILE_MODELS
        ]
        if managed_xprinter and _confirm(
            f"Apply the official driver to {len(managed_xprinter)} managed XPrinter queue(s)?",
            args.yes,
        ):
            for printer, model in managed_xprinter:
                printer.driver = None
                printer.ppd = str(installed_xprinter.ppd_paths[model])
                printer.cups_options = dict(XPRINTER_CUPS_OPTIONS[model])
                cups.create_or_update_queue(runner, printer)
            save_state(state)
        print(
            f"Installed XPrinter {installed_xprinter.version} for "
            f"{installed_xprinter.architecture}; POS-58/76/80 PPDs are ready."
        )
    elif args.command == "adopt-printer":
        cmd_adopt(args, runner, state)
    elif args.command == "remove-printer":
        _root()
        name = queue_name(args.name)
        if name not in state.printers:
            raise ValidationError(f"{name!r} is not managed; refusing to remove it")
        if _confirm(f"Remove managed CUPS queue {name}?", args.yes):
            cups.remove_queue(runner, name)
            if not runner.dry_run:
                del state.printers[name]
                save_state(state)
            print(f"Removed {name}; no other queue was changed.")
    elif args.command == "list-printers":
        actual = cups.list_queues(runner)
        if not actual and not state.printers:
            print("No CUPS queues found.")
        for name in sorted(set(actual).union(state.printers)):
            flags = ["managed" if name in state.printers else "unmanaged"]
            if name in actual:
                flags.extend(["enabled" if actual[name].enabled else "disabled"])
            else:
                flags.append("missing")
            print(f"{name}: {', '.join(flags)}")
    elif args.command == "discover-usb":
        devices = discover_usb(runner)
        if not devices:
            print("No CUPS usb:// devices found.")
        for usb_device in devices:
            print(
                f"{usb_device.manufacturer} {usb_device.model} | "
                f"serial={usb_device.serial or '-'} | {usb_device.uri}"
            )
    elif args.command == "discover":
        output = discover_network(runner)
        print(output.strip() or "No DNS-SD printer services found.")
    elif args.command == "list-profiles":
        for identifier, profile_item in sorted(profiles.items()):
            print(f"{identifier:24} {profile_item.status:18} {profile_item.display_name}")
    elif args.command == "test":
        _root()
        managed = state.printers.get(queue_name(args.printer))
        if not managed:
            raise ValidationError("test printing is restricted to managed queues")
        selected = profiles.get(managed.profile or "")
        with tempfile.TemporaryDirectory(prefix="airprint-server-") as temporary:
            print(submit_test(runner, managed.name, selected, Path(temporary)))
            if args.test_cutter:
                if not selected or selected.category != "escpos" or not selected.cutter:
                    raise ValidationError("selected profile does not declare cutter support")
                if not _confirm("Send an explicit ESC/POS cutter command?", args.yes):
                    print("Cutter test skipped.")
                else:
                    print(submit_cutter_test(runner, managed.name, Path(temporary)))
    elif args.command in {"diagnose", "status"}:
        selected_printer = getattr(args, "printer", None)
        managed = state.printers.get(selected_printer) if selected_printer else None
        if selected_printer and not managed:
            raise ValidationError(f"{selected_printer!r} is not a managed queue")
        selected_profile = profiles.get(managed.profile or "") if managed else None
        failed = False
        for check in diagnose(runner, state, printer=managed, profile=selected_profile):
            print(check.format())
            failed = failed or (not check.ok and not check.warning)
        if getattr(args, "logs", False):
            print("\nRecent relevant journal entries:")
            print(recent_logs(runner, include_ipp_usb=True).rstrip())
        if args.command == "status":
            print(f"Managed queues: {len(state.printers)}")
        if failed:
            raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    level = logging.DEBUG if args.verbose > 1 else logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s airprint-server: %(message)s")
    try:
        _dispatch(args)
        return 0
    except (ValidationError, ValueError, PermissionError, RuntimeError, CommandError) as exc:
        LOG.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        LOG.error("interrupted; completed atomic state writes remain valid")
        return 130
