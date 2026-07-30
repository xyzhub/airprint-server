"""Validated access to the user-supplied BIXOLON POS CUPS driver."""

from __future__ import annotations

import hashlib
import platform
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from airprint_server.validation import ValidationError

BIXOLON_VERSION = "1.5.9"
BIXOLON_ARCHIVE = f"Software_BxlPOSCupsDrv_Linux_v{BIXOLON_VERSION}.tgz"
BIXOLON_SHA256 = "6a081d40dfb62cd5a42a816b7e52f9628c20e3aca65c61564e9ebb0862a18d1e"
BIXOLON_ROOT = f"Software_BxlPOSCupsDrv_Linux_v{BIXOLON_VERSION}"
BIXOLON_PPD_MEMBER = f"{BIXOLON_ROOT}/Bixolon/SRPE300_v1.0.3.ppd"
BIXOLON_FILTER_MEMBERS = {
    "aarch64": f"{BIXOLON_ROOT}/filters/rastertoBixolon_v1.5.9_RaspberryPi_x64",
    "armv7l": f"{BIXOLON_ROOT}/filters/rastertoBixolon_v1.5.9_RaspberryPi_x86",
    "x86_64": f"{BIXOLON_ROOT}/filters/rastertoBixolon_v1.5.9_x64",
    "i686": f"{BIXOLON_ROOT}/filters/rastertoBixolon_v1.5.9_x86",
}
ARCHITECTURE_ALIASES = {
    "arm64": "aarch64",
    "armhf": "armv7l",
    "armv6l": "armv7l",
    "amd64": "x86_64",
    "i386": "i686",
}
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class BixolonPayload:
    version: str
    architecture: str
    archive_sha256: str
    ppd: bytes
    filter_binary: bytes


def _archive_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_archive_member(member: tarfile.TarInfo) -> bool:
    path = PurePosixPath(member.name)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and (member.isdir() or member.isreg())
        and member.size <= MAX_MEMBER_BYTES
    )


def _read_member(archive: tarfile.TarFile, name: str) -> bytes:
    try:
        member = archive.getmember(name)
    except KeyError as exc:
        raise ValidationError(f"BIXOLON archive is missing required file: {name}") from exc
    if not member.isreg() or member.size > MAX_MEMBER_BYTES:
        raise ValidationError(f"BIXOLON archive member is not a safe regular file: {name}")
    extracted = archive.extractfile(member)
    if extracted is None:
        raise ValidationError(f"cannot read BIXOLON archive member: {name}")
    return extracted.read(MAX_MEMBER_BYTES + 1)


def load_bixolon_payload(
    archive_path: Path,
    *,
    machine: str | None = None,
    expected_sha256: str = BIXOLON_SHA256,
) -> BixolonPayload:
    """Validate the complete vendor archive and return only required in-memory files."""
    archive_path = archive_path.expanduser()
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ValidationError(f"BIXOLON driver archive is not a regular file: {archive_path}")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValidationError("BIXOLON driver archive exceeds the 64 MiB safety limit")
    actual_sha256 = _archive_sha256(archive_path)
    if actual_sha256 != expected_sha256:
        raise ValidationError(
            "BIXOLON driver archive checksum mismatch; expected the official v1.5.9 archive"
        )
    architecture = (machine or platform.machine()).lower()
    architecture = ARCHITECTURE_ALIASES.get(architecture, architecture)
    try:
        filter_member = BIXOLON_FILTER_MEMBERS[architecture]
    except KeyError as exc:
        raise ValidationError(f"unsupported BIXOLON driver architecture: {architecture}") from exc
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            if any(not _safe_archive_member(member) for member in archive.getmembers()):
                raise ValidationError("BIXOLON driver archive contains an unsafe member")
            ppd = _read_member(archive, BIXOLON_PPD_MEMBER)
            filter_binary = _read_member(archive, filter_member)
    except (tarfile.TarError, OSError) as exc:
        raise ValidationError(f"cannot read BIXOLON driver archive: {exc}") from exc
    if not filter_binary.startswith(b"\x7fELF"):
        raise ValidationError("BIXOLON filter is not an ELF executable")
    if b'*ModelName:             "BIXOLON SRP-E300"' not in ppd:
        raise ValidationError("BIXOLON PPD does not identify the SRP-E300")
    return BixolonPayload(BIXOLON_VERSION, architecture, actual_sha256, ppd, filter_binary)
