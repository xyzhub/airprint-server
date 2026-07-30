from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

import pytest
from conftest import FakeRunner

from airprint_server.bixolon_driver import (
    BIXOLON_ARCHIVE,
    BIXOLON_CUPS_OPTIONS,
    BIXOLON_FILTER_MEMBERS,
    BIXOLON_PPD_MEMBER,
    download_bixolon_archive,
    install_bixolon_driver,
    load_bixolon_payload,
    remove_bixolon_driver,
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


def _download_package(archive: bytes, *, unsafe: bool = False) -> bytes:
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as output:
        output.writestr(BIXOLON_ARCHIVE, archive)
        if unsafe:
            output.writestr("../outside", b"unsafe")
    return package.getvalue()


def test_downloads_and_extracts_pinned_vendor_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive_path, archive_checksum = _archive(tmp_path)
    package = _download_package(archive_path.read_bytes())
    monkeypatch.setattr(
        "airprint_server.bixolon_driver._fetch_bixolon_download",
        lambda: package,
    )

    downloaded = download_bixolon_archive(
        tmp_path / "download",
        expected_download_sha256=hashlib.sha256(package).hexdigest(),
        expected_archive_sha256=archive_checksum,
    )

    assert downloaded.name == BIXOLON_ARCHIVE
    assert downloaded.read_bytes() == archive_path.read_bytes()
    assert downloaded.stat().st_mode & 0o777 == 0o600


def test_rejects_changed_vendor_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "airprint_server.bixolon_driver._fetch_bixolon_download",
        lambda: b"changed",
    )
    with pytest.raises(ValidationError, match="download checksum mismatch"):
        download_bixolon_archive(tmp_path, expected_download_sha256="0" * 64)


def test_rejects_unsafe_vendor_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive_path, archive_checksum = _archive(tmp_path)
    package = _download_package(archive_path.read_bytes(), unsafe=True)
    monkeypatch.setattr(
        "airprint_server.bixolon_driver._fetch_bixolon_download",
        lambda: package,
    )
    with pytest.raises(ValidationError, match="unsafe member"):
        download_bixolon_archive(
            tmp_path / "download",
            expected_download_sha256=hashlib.sha256(package).hexdigest(),
            expected_archive_sha256=archive_checksum,
        )


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
    monkeypatch.setattr("airprint_server.bixolon_driver._require_root", lambda: None)
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
        os_version="13",
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


def test_removes_only_recorded_driver_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("airprint_server.bixolon_driver._require_root", lambda: None)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    ppd = data_dir / "SRPE300_v1.0.3.ppd"
    ppd.write_bytes(b"ppd")
    filter_path = tmp_path / "cups" / "filter" / "rastertoBixolon"
    filter_path.parent.mkdir(parents=True)
    filter_path.write_bytes(b"filter")
    state = State(
        vendor_drivers={
            "bixolon-pos-cups": {
                "ppd_path": str(ppd),
                "filter_path": str(filter_path),
            }
        }
    )

    remove_bixolon_driver(state, data_dir=data_dir, filter_path=filter_path)

    assert not ppd.exists()
    assert not filter_path.exists()
    assert "bixolon-pos-cups" not in state.vendor_drivers
