"""Validated access to the user-supplied BIXOLON POS CUPS driver."""

from __future__ import annotations

import hashlib
import os
import platform
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from airprint_server.commands import Runner
from airprint_server.config import State
from airprint_server.installer import install_package_list, require_root
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
BIXOLON_DATA_DIR = Path("/var/lib/airprint-server/drivers/bixolon")
BIXOLON_PPD_PATH = BIXOLON_DATA_DIR / "SRPE300_v1.0.3.ppd"
BIXOLON_FILTER_NAME = "rastertoBixolon"
BIXOLON_CUPS_OPTIONS = {
    "PageSize": "61X72MMY70MM",
    "Resolution": "180dpi",
    "ColorModel": "1Gray",
    "PageType": "0Variable",
    "Dithering": "1True",
    "PageCut": "4JobCutFeed",
    "print-scaling-default": "fit",
}


@dataclass(frozen=True)
class BixolonPayload:
    version: str
    architecture: str
    archive_sha256: str
    ppd: bytes
    filter_binary: bytes


@dataclass(frozen=True)
class BixolonInstallation:
    version: str
    architecture: str
    ppd_path: Path
    filter_path: Path


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


def _filter_destination(runner: Runner) -> Path:
    if runner.dry_run:
        return Path("/usr/lib/cups/filter") / BIXOLON_FILTER_NAME
    server_bin = Path(runner.run(["cups-config", "--serverbin"]).stdout.strip())
    if not server_bin.is_absolute() or ".." in server_bin.parts:
        raise RuntimeError(f"cups-config returned an unsafe serverbin path: {server_bin}")
    return server_bin / "filter" / BIXOLON_FILTER_NAME


def _host_version(os_release: Path = Path("/etc/os-release")) -> str:
    try:
        lines = os_release.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError(f"cannot read host release information: {exc}") from exc
    values = dict(line.split("=", 1) for line in lines if "=" in line)
    version = values.get("VERSION_ID", "").strip('"')
    if version not in {"12", "13"}:
        raise RuntimeError(f"unsupported Debian/Raspberry Pi OS version: {version or 'unknown'}")
    return version


def installed_bixolon_ppd(state: State) -> Path | None:
    details = state.vendor_drivers.get("bixolon-pos-cups")
    if not details:
        return None
    ppd_path = Path(details.get("ppd_path", ""))
    filter_path = Path(details.get("filter_path", ""))
    if (
        details.get("version") == BIXOLON_VERSION
        and ppd_path.is_file()
        and filter_path.is_file()
    ):
        return ppd_path
    return None


def install_bixolon_driver(
    runner: Runner,
    state: State,
    archive_path: Path,
    *,
    machine: str | None = None,
    os_version: str | None = None,
    expected_sha256: str = BIXOLON_SHA256,
    data_dir: Path = BIXOLON_DATA_DIR,
    filter_path: Path | None = None,
) -> BixolonInstallation:
    """Install only the validated SRP-E300 PPD and matching CUPS filter."""
    require_root()
    payload = load_bixolon_payload(
        archive_path,
        machine=machine,
        expected_sha256=expected_sha256,
    )
    host_version = os_version or _host_version()
    dependency = "libcupsimage2t64" if host_version == "13" else "libcupsimage2"
    install_package_list(runner, state, [dependency])
    selected_filter_path = filter_path or _filter_destination(runner)
    selected_ppd_path = data_dir / BIXOLON_PPD_PATH.name
    installation = BixolonInstallation(
        payload.version,
        payload.architecture,
        selected_ppd_path,
        selected_filter_path,
    )
    if runner.dry_run:
        return installation

    staged_filter = _stage_file(selected_filter_path, payload.filter_binary, 0o755)
    staged_ppd = _stage_file(selected_ppd_path, payload.ppd, 0o644)
    try:
        linkage = runner.run(["ldd", staged_filter], check=False)
        if linkage.returncode or "not found" in f"{linkage.stdout}\n{linkage.stderr}":
            raise RuntimeError("BIXOLON filter has unresolved runtime library dependencies")
        os.replace(staged_filter, selected_filter_path)
        os.replace(staged_ppd, selected_ppd_path)
    finally:
        for temporary in (staged_filter, staged_ppd):
            if os.path.exists(temporary):
                os.unlink(temporary)
    runner.run(["systemctl", "reload-or-restart", "cups.service"])
    state.vendor_drivers["bixolon-pos-cups"] = {
        "version": payload.version,
        "architecture": payload.architecture,
        "archive_sha256": payload.archive_sha256,
        "ppd_path": str(selected_ppd_path),
        "filter_path": str(selected_filter_path),
    }
    return installation
