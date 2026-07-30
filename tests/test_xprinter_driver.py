from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from airprint_server.validation import ValidationError
from airprint_server.xprinter_driver import (
    ELF_MACHINES,
    XPRINTER_ARCHITECTURE_SUFFIXES,
    XPRINTER_MODEL_FILTERS,
    XPRINTER_PPD_MEMBERS,
    download_xprinter_package,
    load_xprinter_payload,
)


def _tar_xz(members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:xz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def _ar(members: dict[str, bytes]) -> bytes:
    output = bytearray(b"!<arch>\n")
    for name, content in members.items():
        header = (
            f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{0o100644:<8o}{len(content):<10}`\n"
        ).encode("ascii")
        assert len(header) == 60
        output.extend(header)
        output.extend(content)
        if len(content) % 2:
            output.extend(b"\n")
    return bytes(output)


def _elf(machine: int) -> bytes:
    binary = bytearray(64)
    binary[:6] = b"\x7fELF\x02\x01"
    binary[18:20] = machine.to_bytes(2, "little")
    return bytes(binary)


def _package(tmp_path: Path, *, machine: str = "aarch64") -> tuple[Path, str]:
    suffix = XPRINTER_ARCHITECTURE_SUFFIXES[machine]
    data_members = {
        member: f'*ModelName:             "POS-{model}"\n'.encode()
        for model, member in XPRINTER_PPD_MEMBERS.items()
    }
    for filter_name in set(XPRINTER_MODEL_FILTERS.values()):
        data_members[
            f"./opt/pos/printer-driver-pos/bin/{filter_name}-{suffix}"
        ] = _elf(ELF_MACHINES[machine])
    package = _ar(
        {
            "debian-binary": b"2.0\n",
            "control.tar.xz": _tar_xz(
                {"./control": b"Package: printer-driver-pos\nVersion: 3.13.11\n"}
            ),
            "data.tar.xz": _tar_xz(data_members),
        }
    )
    path = tmp_path / "driver.deb"
    path.write_bytes(package)
    return path, hashlib.sha256(package).hexdigest()


def test_loads_xprinter_ppds_and_matching_arm_filters(tmp_path: Path) -> None:
    package, checksum = _package(tmp_path)

    payload = load_xprinter_payload(
        package,
        machine="arm64",
        expected_sha256=checksum,
    )

    assert payload.architecture == "aarch64"
    assert set(payload.ppds) == {"58", "76", "80"}
    assert set(payload.filters) == {"rastertosnailep", "rastertosnailep2"}
    assert all(binary.startswith(b"\x7fELF") for binary in payload.filters.values())


def test_rejects_changed_xprinter_debian_package(tmp_path: Path) -> None:
    package, _checksum = _package(tmp_path)
    with pytest.raises(ValidationError, match="checksum mismatch"):
        load_xprinter_payload(package, machine="aarch64", expected_sha256="0" * 64)


def test_rejects_legacy_intel_only_xprinter_installer(tmp_path: Path) -> None:
    package = tmp_path / "XP-80"
    package.write_bytes(b"line=`wc -l $0|awk '{print $1}'`\n")
    checksum = hashlib.sha256(package.read_bytes()).hexdigest()
    with pytest.raises(ValidationError, match="Intel Linux filters"):
        load_xprinter_payload(package, machine="aarch64", expected_sha256=checksum)


def test_downloads_pinned_xprinter_collection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = b"official-rar"
    monkeypatch.setattr(
        "airprint_server.xprinter_driver._fetch_xprinter_download",
        lambda: content,
    )

    downloaded = download_xprinter_package(
        tmp_path / "download",
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )

    assert downloaded.read_bytes() == content
    assert downloaded.stat().st_mode & 0o777 == 0o600
