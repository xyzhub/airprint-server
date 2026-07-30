from pathlib import Path

from conftest import FakeRunner

from airprint_server.commands import CommandResult
from airprint_server.config import ManagedPrinter, State
from airprint_server.installer import ipp_usb_state, operating_system, uninstall


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
