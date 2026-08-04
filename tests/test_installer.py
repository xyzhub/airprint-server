from pathlib import Path

from conftest import FakeRunner

from airprint_server.commands import CommandResult
from airprint_server.config import ManagedPrinter, State
from airprint_server.installer import (
    BUILD_PACKAGES,
    INSTALL_PHASES,
    RUNTIME_PACKAGES,
    install,
    ipp_usb_state,
    migrate_managed_printer_defaults,
    operating_system,
    uninstall,
)


def test_ipp_usb_detection() -> None:
    dpkg = ("dpkg-query", "-W", "ipp-usb")
    active = ("systemctl", "is-active", "--quiet", "ipp-usb.service")
    enabled = ("systemctl", "is-enabled", "--quiet", "ipp-usb.service")
    runner = FakeRunner(
        {
            dpkg: CommandResult(dpkg, 0),
            active: CommandResult(active, 0),
            enabled: CommandResult(enabled, 1),
        }
    )
    assert ipp_usb_state(runner) == {"installed": True, "active": True, "enabled": False}  # type: ignore[arg-type]


def test_git_is_available_without_escpos_build_dependencies() -> None:
    assert "git" in RUNTIME_PACKAGES
    assert "git" not in BUILD_PACKAGES


def test_install_reports_real_phases(monkeypatch: object, tmp_path: Path) -> None:
    monkeypatch.setattr("airprint_server.installer.require_root", lambda: None)  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "airprint_server.installer.operating_system",
        lambda: (True, "Test Raspberry Pi OS"),
    )  # type: ignore[attr-defined]
    monkeypatch.setattr("airprint_server.installer.initialize_config", lambda: None)  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "airprint_server.installer.raw_proxy.install_service",
        lambda _runner, current: (
            setattr(current, "raw_proxy_service_managed", True),
            setattr(current, "raw_address_service_managed", True),
        ),
    )  # type: ignore[attr-defined]
    monkeypatch.setattr("airprint_server.installer.save_state", lambda _state: None)  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "airprint_server.installer.rastertoescpos_available", lambda _runner: True
    )  # type: ignore[attr-defined]
    runner = FakeRunner(dry_run=False)
    updates: list[tuple[int, str]] = []

    install(
        runner,  # type: ignore[arg-type]
        State(),
        script_dir=tmp_path,
        progress=lambda completed, label: updates.append((completed, label)),
    )

    assert updates[0] == (0, "Checking host compatibility")
    assert updates[-1] == (INSTALL_PHASES, "Installation complete")
    assert [completed for completed, _label in updates] == list(range(INSTALL_PHASES + 1))


def test_supported_bookworm_and_trixie(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    for identifier in ("debian", "raspbian"):
        for version in ("12", "13"):
            os_release.write_text(
                f'ID="{identifier}"\nVERSION_ID="{version}"\n'
                f'PRETTY_NAME="Test {identifier} {version}"\n',
                encoding="utf-8",
            )
            supported, label = operating_system(os_release)
            assert supported
            assert label == f"Test {identifier} {version}"


def test_unsupported_release_is_rejected(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text(
        'ID="raspbian"\nVERSION_ID="14"\nPRETTY_NAME="Future Raspberry Pi OS"\n',
        encoding="utf-8",
    )
    assert operating_system(os_release) == (False, "Future Raspberry Pi OS")


def test_migrates_legacy_bixolon_media_width(tmp_path: Path) -> None:
    ppd_path = tmp_path / "SRPE300_v1.0.3.ppd"
    ppd_path.write_text('*PPD-Adobe: "4.3"\n', encoding="ascii")
    ppd = str(ppd_path)
    printer = ManagedPrinter(
        "Receipt",
        "Receipt",
        "bixolon-srp-e300",
        "usb://BIXOLON/SRP-E300?serial=1",
        "usb",
        ppd=ppd,
        cups_options={
            "PageSize": "61X72MMY70MM",
            "PageCut": "4JobCutFeed",
        },
    )
    state = State(
        printers={"Receipt": printer},
        vendor_drivers={"bixolon-pos-cups": {"ppd_path": ppd}},
    )
    exists = ("lpstat", "-p", "Receipt")
    options = ("lpoptions", "-p", "Receipt")
    runner = FakeRunner(
        {
            exists: CommandResult(exists, 0),
            options: CommandResult(options, 0, "PageSize=61X72MMY70MM PageCut=4JobCutFeed"),
        }
    )

    assert migrate_managed_printer_defaults(runner, state) == ["Receipt"]  # type: ignore[arg-type]
    assert printer.cups_options["PageSize"] == "80X80MMY70MM"
    assert any(
        call[:3] == ("lpadmin", "-p", "Receipt")
        and "PageSize=80X80MMY70MM" in call
        for call in runner.calls
    )


def test_bixolon_media_migration_preserves_effective_user_override() -> None:
    ppd = "/var/lib/airprint-server/drivers/bixolon/SRPE300_v1.0.3.ppd"
    printer = ManagedPrinter(
        "Receipt",
        "Receipt",
        "bixolon-srp-e300",
        "usb://BIXOLON/SRP-E300?serial=1",
        "usb",
        ppd=ppd,
        cups_options={"PageSize": "61X72MMY70MM"},
    )
    state = State(
        printers={"Receipt": printer},
        vendor_drivers={"bixolon-pos-cups": {"ppd_path": ppd}},
    )
    exists = ("lpstat", "-p", "Receipt")
    options = ("lpoptions", "-p", "Receipt")
    runner = FakeRunner(
        {
            exists: CommandResult(exists, 0),
            options: CommandResult(options, 0, "PageSize=Custom.76x297mm"),
        }
    )

    assert migrate_managed_printer_defaults(runner, state) == []  # type: ignore[arg-type]
    assert printer.cups_options["PageSize"] == "61X72MMY70MM"
    assert not any(call and call[0] == "lpadmin" for call in runner.calls)


def test_safe_uninstall_only_managed_queue(monkeypatch: object, tmp_path: Path) -> None:
    monkeypatch.setattr("airprint_server.installer.require_root", lambda: None)  # type: ignore[attr-defined]
    monkeypatch.setattr("airprint_server.installer.STATE_DIR", tmp_path / "absent")  # type: ignore[attr-defined]
    monkeypatch.setattr("airprint_server.installer.CONFIG_DIR", tmp_path / "config")  # type: ignore[attr-defined]
    state = State(
        printers={
            "Managed": ManagedPrinter("Managed", "Managed", None, "ipp://host/p", "ipp")
        }
    )
    stat = ("lpstat", "-p", "Managed")
    runner = FakeRunner({stat: CommandResult(stat, 0)}, dry_run=True)
    uninstall(
        runner,  # type: ignore[arg-type]
        state,
        remove_queues=True,
        remove_config=False,
        remove_state=False,
        remove_escpos=False,
        confirm=lambda _message: True,
    )
    assert ("lpadmin", "-x", "Managed") in runner.calls
    assert not any("Unrelated" in call for call in runner.calls)
