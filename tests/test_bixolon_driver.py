from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest
from conftest import FakeRunner

from airprint_server.bixolon_driver import (
    BIXOLON_CUPS_OPTIONS,
    BIXOLON_FILTER_MEMBERS,
    BIXOLON_PPD_MEMBER,
    install_bixolon_driver,
    load_bixolon_payload,
)
from airprint_server.commands import CommandResult
from airprint_server.config import State
from airprint_server.validation import ValidationError


def _archive(tmp_path: Path, *, unsafe: bool = False) -> tuple[Path, str]:
    path = tmp_path / "driver.tgz"
    members = {
        BIXOLON_PPD_MEMBER: b'*ModelName:             "BIXOLON SRP-E300"\n',
        BIXOLON_FILTER_MEMBERS["aarch64"]: b"\x7fELF-arm64",
        BIXOLON_FILTER_MEMBERS["armv7l"]: b"\x7fELF-armhf",
    }
    if unsafe:
        members["../outside"] = b"unsafe"
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_loads_only_matching_architecture_and_ppd(tmp_path: Path) -> None:
    archive, checksum = _archive(tmp_path)
    payload = load_bixolon_payload(
        archive,
        machine="arm64",
        expected_sha256=checksum,
    )
    assert payload.architecture == "aarch64"
    assert payload.filter_binary == b"\x7fELF-arm64"
    assert b"SRP-E300" in payload.ppd


def test_rejects_checksum_mismatch(tmp_path: Path) -> None:
    archive, _checksum = _archive(tmp_path)
    with pytest.raises(ValidationError, match="checksum mismatch"):
        load_bixolon_payload(archive, machine="aarch64", expected_sha256="0" * 64)


def test_rejects_unsafe_archive_member(tmp_path: Path) -> None:
    archive, checksum = _archive(tmp_path, unsafe=True)
    with pytest.raises(ValidationError, match="unsafe member"):
        load_bixolon_payload(archive, machine="aarch64", expected_sha256=checksum)


def test_rejects_unsupported_architecture(tmp_path: Path) -> None:
    archive, checksum = _archive(tmp_path)
    with pytest.raises(ValidationError, match="unsupported.*architecture"):
        load_bixolon_payload(archive, machine="riscv64", expected_sha256=checksum)


def test_installs_only_validated_filter_and_ppd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive, checksum = _archive(tmp_path)
    monkeypatch.setattr("airprint_server.bixolon_driver.require_root", lambda: None)
    package = ("dpkg-query", "-W", "-f=${Status}", "libcupsimage2t64")
    runner = FakeRunner(
        {
            package: CommandResult(package, 0, "install ok installed"),
        }
    )
    state = State()
    filter_path = tmp_path / "cups" / "filter" / "rastertoBixolon"
    data_dir = tmp_path / "data"

    installed = install_bixolon_driver(
        runner,  # type: ignore[arg-type]
        state,
        archive,
        machine="aarch64",
        expected_sha256=checksum,
        data_dir=data_dir,
        filter_path=filter_path,
    )

    assert installed.ppd_path.read_bytes().startswith(b"*ModelName")
    assert installed.filter_path.read_bytes() == b"\x7fELF-arm64"
    assert installed.filter_path.stat().st_mode & 0o777 == 0o755
    assert state.vendor_drivers["bixolon-pos-cups"]["version"] == "1.5.9"
    assert ("systemctl", "reload-or-restart", "cups.service") in runner.calls
    assert BIXOLON_CUPS_OPTIONS["PageCut"] == "4JobCutFeed"
