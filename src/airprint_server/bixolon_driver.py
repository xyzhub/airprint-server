"""Validated access to the user-supplied BIXOLON POS CUPS driver."""

from __future__ import annotations

import hashlib
import io
import os
import platform
import stat
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from airprint_server.commands import Runner
from airprint_server.config import State
from airprint_server.validation import ValidationError

BIXOLON_VERSION = "1.5.9"
BIXOLON_ARCHIVE = f"Software_BxlPOSCupsDrv_Linux_v{BIXOLON_VERSION}.tgz"
BIXOLON_SHA256 = "6a081d40dfb62cd5a42a816b7e52f9628c20e3aca65c61564e9ebb0862a18d1e"
BIXOLON_DOWNLOAD_URL = (
    "https://www.bixolon.com/_lib/download_single.php?"
    "FILE_INFO=driver%7Cdriver_file%7Cdriver_idx%7C118%7Cdriver"
)
BIXOLON_DOWNLOAD_SHA256 = (
    "363245c3e7f0a0db343f05e15852a57532eea78b42054e97899f402774841dd1"
)
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
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024
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


def _fetch_bixolon_download() -> bytes:
    request = Request(
        BIXOLON_DOWNLOAD_URL,
        headers={"User-Agent": "airprint-server/0.1 BIXOLON-driver-installer"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            final_url = urlsplit(response.geturl())
            if final_url.scheme != "https" or final_url.hostname != "www.bixolon.com":
                raise ValidationError("BIXOLON download redirected to an unexpected host")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
                raise ValidationError("BIXOLON driver download exceeds the 16 MiB safety limit")
            chunks: list[bytes] = []
            received = 0
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > MAX_DOWNLOAD_BYTES:
                    raise ValidationError(
                        "BIXOLON driver download exceeds the 16 MiB safety limit"
                    )
                chunks.append(chunk)
    except (OSError, URLError, ValueError) as exc:
        raise ValidationError(f"cannot download BIXOLON driver: {exc}") from exc
    return b"".join(chunks)


def _safe_zip_member(member: zipfile.ZipInfo) -> bool:
    path = PurePosixPath(member.filename)
    unix_mode = member.external_attr >> 16
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and not stat.S_ISLNK(unix_mode)
        and member.file_size <= MAX_ARCHIVE_BYTES
    )


def download_bixolon_archive(
    destination_dir: Path,
    *,
    expected_download_sha256: str = BIXOLON_DOWNLOAD_SHA256,
    expected_archive_sha256: str = BIXOLON_SHA256,
) -> Path:
    """Download BIXOLON's official ZIP and safely extract the pinned driver archive."""
    download = _fetch_bixolon_download()
    if hashlib.sha256(download).hexdigest() != expected_download_sha256:
        raise ValidationError(
            "BIXOLON driver download checksum mismatch; the vendor package may have changed"
        )
    try:
        with zipfile.ZipFile(io.BytesIO(download)) as package:
            if any(not _safe_zip_member(member) for member in package.infolist()):
                raise ValidationError("BIXOLON driver download contains an unsafe member")
            try:
                archive_info = package.getinfo(BIXOLON_ARCHIVE)
            except KeyError as exc:
                raise ValidationError(
                    f"BIXOLON driver download is missing {BIXOLON_ARCHIVE}"
                ) from exc
            archive = package.read(archive_info)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValidationError(f"cannot read BIXOLON driver download: {exc}") from exc
    if hashlib.sha256(archive).hexdigest() != expected_archive_sha256:
        raise ValidationError("downloaded BIXOLON driver archive checksum mismatch")
    if destination_dir.is_symlink():
        raise ValidationError(f"download destination is a symbolic link: {destination_dir}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / BIXOLON_ARCHIVE
    staged = _stage_file(destination, archive, 0o600)
    os.replace(staged, destination)
    return destination


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


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("BIXOLON driver installation requires root; rerun with sudo")


def _install_dependency(runner: Runner, state: State, package: str) -> None:
    status = runner.run(
        ["dpkg-query", "-W", "-f=${Status}", package],
        check=False,
    )
    if status.stdout.strip() == "install ok installed":
        return
    runner.run(["apt-get", "update"])
    runner.run(["apt-get", "install", "-y", "--no-install-recommends", package])
    state.installed_packages = sorted(set(state.installed_packages).union({package}))


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
    _require_root()
    payload = load_bixolon_payload(
        archive_path,
        machine=machine,
        expected_sha256=expected_sha256,
    )
    host_version = os_version or _host_version()
    dependency = "libcupsimage2t64" if host_version == "13" else "libcupsimage2"
    _install_dependency(runner, state, dependency)
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


def remove_bixolon_driver(
    state: State,
    *,
    data_dir: Path = BIXOLON_DATA_DIR,
    filter_path: Path | None = None,
) -> None:
    """Remove only paths previously managed by this integration."""
    _require_root()
    details = state.vendor_drivers.get("bixolon-pos-cups")
    if not details:
        return
    recorded_ppd = Path(details.get("ppd_path", ""))
    recorded_filter = Path(details.get("filter_path", ""))
    expected_ppd = data_dir / BIXOLON_PPD_PATH.name
    selected_filter = filter_path or recorded_filter
    if recorded_ppd != expected_ppd or recorded_filter != selected_filter:
        raise RuntimeError("refusing to remove unexpected BIXOLON driver paths")
    if filter_path is None and not (
        selected_filter.is_absolute()
        and selected_filter.name == BIXOLON_FILTER_NAME
        and selected_filter.parent.name == "filter"
        and "cups" in selected_filter.parts
        and selected_filter.parts[:2] == ("/", "usr")
    ):
        raise RuntimeError("refusing to remove BIXOLON filter outside the CUPS server directory")
    for path in (recorded_ppd, selected_filter):
        if path.is_symlink():
            raise RuntimeError(f"refusing to remove symbolic link: {path}")
        if path.exists():
            path.unlink()
    if data_dir.exists() and not any(data_dir.iterdir()):
        data_dir.rmdir()
    del state.vendor_drivers["bixolon-pos-cups"]
