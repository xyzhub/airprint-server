"""Validated installation of XPrinter's official POS CUPS driver."""

from __future__ import annotations

import hashlib
import io
import os
import platform
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from airprint_server.validation import ValidationError

XPRINTER_VERSION = "3.13.11"
XPRINTER_DOWNLOAD_URL = "https://www.xprintertech.com/bill-product-driver-1"
XPRINTER_DOWNLOAD_SHA256 = (
    "39afd0b209c5ac5cb589318174f312830978e27a8a63ae59f6f1650fddc51a6a"
)
XPRINTER_DOWNLOAD_NAME = "xprinter-bill-driver-3.13.11.rar"
XPRINTER_DEB_NAME = f"printer-driver-pos_{XPRINTER_VERSION}_all.deb"
XPRINTER_DEB_MEMBER = f"票据产品驱动/Linux驱动/{XPRINTER_DEB_NAME}"
XPRINTER_DEB_SHA256 = "292061819d381541f1fd2318386382f3cc63484b36e85d9c5603be1b4b68853c"
XPRINTER_MODELS = ("58", "76", "80")
XPRINTER_PPD_MEMBERS = {
    model: f"./usr/share/cups/model/pos/POS-{model}.ppd" for model in XPRINTER_MODELS
}
XPRINTER_MODEL_FILTERS = {
    "58": "rastertosnailep",
    "76": "rastertosnailep2",
    "80": "rastertosnailep",
}
XPRINTER_ARCHITECTURE_SUFFIXES = {
    "aarch64": "aarch64",
    "armv7l": "armv7l",
    "x86_64": "x64",
    "i686": "x86",
}
XPRINTER_ARCHITECTURE_ALIASES = {
    "arm64": "aarch64",
    "armhf": "armv7l",
    "armv6l": "armv7l",
    "amd64": "x86_64",
    "i386": "i686",
}
ELF_MACHINES = {"aarch64": 183, "armv7l": 40, "x86_64": 62, "i686": 3}
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_DEB_BYTES = 8 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class XPrinterPayload:
    version: str
    architecture: str
    package_sha256: str
    ppds: dict[str, bytes]
    filters: dict[str, bytes]


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _stage_file(destination: Path, content: bytes, mode: int) -> str:
    if destination.is_symlink():
        raise RuntimeError(f"refusing to replace symbolic link: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        return temporary
    except BaseException:
        os.unlink(temporary)
        raise


def _fetch_xprinter_download() -> bytes:
    request = Request(
        XPRINTER_DOWNLOAD_URL,
        headers={"User-Agent": "airprint-server/0.1 XPrinter-driver-installer"},
    )
    try:
        with urlopen(request, timeout=120) as response:
            final_url = urlsplit(response.geturl())
            if final_url.scheme != "https" or final_url.hostname not in {
                "www.xprintertech.com",
                "img5541.weyesimg.com",
            }:
                raise ValidationError("XPrinter download redirected to an unexpected host")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
                raise ValidationError("XPrinter driver download exceeds the 64 MiB safety limit")
            chunks: list[bytes] = []
            received = 0
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > MAX_DOWNLOAD_BYTES:
                    raise ValidationError(
                        "XPrinter driver download exceeds the 64 MiB safety limit"
                    )
                chunks.append(chunk)
    except (OSError, URLError, ValueError) as exc:
        raise ValidationError(f"cannot download XPrinter driver: {exc}") from exc
    return b"".join(chunks)


def download_xprinter_package(
    destination_dir: Path,
    *,
    expected_sha256: str = XPRINTER_DOWNLOAD_SHA256,
) -> Path:
    """Download the pinned official XPrinter driver collection."""
    content = _fetch_xprinter_download()
    if _sha256_bytes(content) != expected_sha256:
        raise ValidationError(
            "XPrinter driver download checksum mismatch; the vendor package may have changed"
        )
    if destination_dir.is_symlink():
        raise ValidationError(f"download destination is a symbolic link: {destination_dir}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / XPRINTER_DOWNLOAD_NAME
    staged = _stage_file(destination, content, 0o600)
    os.replace(staged, destination)
    return destination


def _read_ar_members(content: bytes) -> dict[str, bytes]:
    if not content.startswith(b"!<arch>\n"):
        if content.startswith(b"line=`wc -l $0"):
            raise ValidationError(
                "legacy XPrinter 2.4.0 installers contain only Intel Linux filters; "
                "use the automatic v3.13.11 download on Raspberry Pi"
            )
        raise ValidationError("XPrinter package is not a Debian archive")
    members: dict[str, bytes] = {}
    offset = 8
    while offset < len(content):
        header = content[offset : offset + 60]
        if len(header) != 60 or header[58:60] != b"`\n":
            raise ValidationError("XPrinter Debian archive has an invalid member header")
        try:
            size = int(header[48:58].decode("ascii").strip())
            name = header[:16].decode("ascii").strip().removesuffix("/")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValidationError("XPrinter Debian archive has invalid metadata") from exc
        offset += 60
        end = offset + size
        if size > MAX_MEMBER_BYTES or end > len(content):
            raise ValidationError("XPrinter Debian archive member exceeds safety limits")
        members[name] = content[offset:end]
        offset = end + (size % 2)
    return members


def _safe_tar_member(member: tarfile.TarInfo) -> bool:
    path = PurePosixPath(member.name)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and (member.isdir() or member.isreg())
        and member.size <= MAX_MEMBER_BYTES
    )


def _read_tar_member(archive: tarfile.TarFile, name: str) -> bytes:
    try:
        member = archive.getmember(name)
    except KeyError as exc:
        raise ValidationError(f"XPrinter package is missing required file: {name}") from exc
    if not member.isreg() or member.size > MAX_MEMBER_BYTES:
        raise ValidationError(f"XPrinter package member is not a safe regular file: {name}")
    extracted = archive.extractfile(member)
    if extracted is None:
        raise ValidationError(f"cannot read XPrinter package member: {name}")
    return extracted.read(MAX_MEMBER_BYTES + 1)


def _validate_elf(binary: bytes, architecture: str, name: str) -> None:
    if (
        len(binary) < 20
        or not binary.startswith(b"\x7fELF")
        or binary[5] != 1
        or int.from_bytes(binary[18:20], "little") != ELF_MACHINES[architecture]
    ):
        raise ValidationError(f"XPrinter filter has the wrong ELF architecture: {name}")


def load_xprinter_payload(
    package_path: Path,
    *,
    machine: str | None = None,
    expected_sha256: str = XPRINTER_DEB_SHA256,
) -> XPrinterPayload:
    """Validate the vendor Debian package without executing its maintainer scripts."""
    package_path = package_path.expanduser()
    if package_path.is_symlink() or not package_path.is_file():
        raise ValidationError(f"XPrinter driver package is not a regular file: {package_path}")
    if package_path.stat().st_size > MAX_DEB_BYTES:
        raise ValidationError("XPrinter Debian package exceeds the 8 MiB safety limit")
    content = package_path.read_bytes()
    actual_sha256 = _sha256_bytes(content)
    if actual_sha256 != expected_sha256:
        raise ValidationError(
            "XPrinter Debian package checksum mismatch; expected official v3.13.11"
        )
    architecture = (machine or platform.machine()).lower()
    architecture = XPRINTER_ARCHITECTURE_ALIASES.get(architecture, architecture)
    try:
        suffix = XPRINTER_ARCHITECTURE_SUFFIXES[architecture]
    except KeyError as exc:
        raise ValidationError(f"unsupported XPrinter driver architecture: {architecture}") from exc
    members = _read_ar_members(content)
    try:
        control_data = members["control.tar.xz"]
        payload_data = members["data.tar.xz"]
    except KeyError as exc:
        raise ValidationError("XPrinter Debian package is missing control or data") from exc
    try:
        with tarfile.open(fileobj=io.BytesIO(control_data), mode="r:xz") as control:
            metadata = _read_tar_member(control, "./control").decode("utf-8")
        with tarfile.open(fileobj=io.BytesIO(payload_data), mode="r:xz") as archive:
            if any(not _safe_tar_member(member) for member in archive.getmembers()):
                raise ValidationError("XPrinter Debian package contains an unsafe member")
            ppds = {
                model: _read_tar_member(archive, member)
                for model, member in XPRINTER_PPD_MEMBERS.items()
            }
            filters = {
                filter_name: _read_tar_member(
                    archive,
                    (
                        "./opt/pos/printer-driver-pos/bin/"
                        f"{filter_name}-{suffix}"
                    ),
                )
                for filter_name in set(XPRINTER_MODEL_FILTERS.values())
            }
    except (tarfile.TarError, OSError, UnicodeDecodeError) as exc:
        raise ValidationError(f"cannot read XPrinter Debian package: {exc}") from exc
    if (
        "Package: printer-driver-pos" not in metadata
        or f"Version: {XPRINTER_VERSION}" not in metadata
    ):
        raise ValidationError("XPrinter Debian package metadata does not match v3.13.11")
    for model, ppd in ppds.items():
        if f'*ModelName:             "POS-{model}"'.encode() not in ppd:
            raise ValidationError(f"XPrinter PPD does not identify POS-{model}")
    for filter_name, binary in filters.items():
        _validate_elf(binary, architecture, filter_name)
    return XPrinterPayload(XPRINTER_VERSION, architecture, actual_sha256, ppds, filters)
