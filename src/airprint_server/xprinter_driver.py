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

from airprint_server.commands import Runner
from airprint_server.config import State
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
XPRINTER_DATA_DIR = Path("/var/lib/airprint-server/drivers/xprinter")
XPRINTER_CUPS_OPTIONS = {
    "58": {
        "PageSize": "X48MMY297MM",
        "PageCutType": "0NoCutPage",
        "DocCutType": "0NoCutDoc",
        "print-scaling-default": "fit",
    },
    "76": {
        "PageSize": "X63MMY70MM",
        "PageCutType": "0NoCutPage",
        "DocCutType": "0NoCutDoc",
        "print-scaling-default": "fit",
    },
    "80": {
        "PageSize": "X72MMY297MM",
        "PageCutType": "0NoCutPage",
        "DocCutType": "1PartialCutDoc",
        "FeedCutAfterJobEnd": "4Line",
        "print-scaling-default": "fit",
    },
}


@dataclass(frozen=True)
class XPrinterPayload:
    version: str
    architecture: str
    package_sha256: str
    ppds: dict[str, bytes]
    filters: dict[str, bytes]


@dataclass(frozen=True)
class XPrinterInstallation:
    version: str
    architecture: str
    ppd_paths: dict[str, Path]
    filter_paths: dict[str, Path]


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
    if content.startswith(b"line=`wc -l $0"):
        raise ValidationError(
            "legacy XPrinter 2.4.0 installers contain only Intel Linux filters; "
            "use the automatic v3.13.11 download on Raspberry Pi"
        )
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


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("XPrinter driver installation requires root; rerun with sudo")


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


def _install_dependencies(runner: Runner, state: State, packages: list[str]) -> None:
    missing = [
        package
        for package in packages
        if runner.run(
            ["dpkg-query", "-W", "-f=${Status}", package],
            check=False,
        ).stdout.strip()
        != "install ok installed"
    ]
    if not missing:
        return
    runner.run(["apt-get", "update"])
    runner.run(["apt-get", "install", "-y", "--no-install-recommends", *missing])
    state.installed_packages = sorted(set(state.installed_packages).union(missing))


def _filter_directory(runner: Runner) -> Path:
    if runner.dry_run:
        return Path("/usr/lib/cups/filter")
    server_bin = Path(runner.run(["cups-config", "--serverbin"]).stdout.strip())
    if not server_bin.is_absolute() or ".." in server_bin.parts:
        raise RuntimeError(f"cups-config returned an unsafe serverbin path: {server_bin}")
    return server_bin / "filter"


def _archive_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_xprinter_deb(
    runner: Runner,
    archive_path: Path,
    destination_dir: Path,
    *,
    expected_archive_sha256: str,
) -> Path:
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ValidationError(f"XPrinter driver archive is not a regular file: {archive_path}")
    if archive_path.stat().st_size > MAX_DOWNLOAD_BYTES:
        raise ValidationError("XPrinter driver archive exceeds the 64 MiB safety limit")
    if _archive_sha256(archive_path) != expected_archive_sha256:
        raise ValidationError(
            "XPrinter driver archive checksum mismatch; expected the official collection"
        )
    runner.run(
        [
            "unar",
            "-quiet",
            "-output-directory",
            str(destination_dir),
            str(archive_path),
            XPRINTER_DEB_MEMBER,
        ],
        timeout=120,
    )
    package = destination_dir / XPRINTER_DEB_MEMBER
    if package.is_symlink() or not package.is_file():
        raise ValidationError(f"XPrinter archive did not produce {XPRINTER_DEB_NAME}")
    if package.stat().st_size > MAX_DEB_BYTES:
        raise ValidationError("extracted XPrinter Debian package exceeds the 8 MiB safety limit")
    return package


def installed_xprinter_ppd(state: State, model: str) -> Path | None:
    details = state.vendor_drivers.get("xprinter-pos-cups")
    if model not in XPRINTER_MODELS or not details:
        return None
    ppd_path = Path(details.get(f"ppd_{model}", ""))
    filter_name = XPRINTER_MODEL_FILTERS[model]
    filter_path = Path(details.get(f"filter_{filter_name}", ""))
    if (
        details.get("version") == XPRINTER_VERSION
        and ppd_path.is_file()
        and filter_path.is_file()
    ):
        return ppd_path
    return None


def install_xprinter_driver(
    runner: Runner,
    state: State,
    source_path: Path,
    *,
    machine: str | None = None,
    os_version: str | None = None,
    expected_download_sha256: str = XPRINTER_DOWNLOAD_SHA256,
    expected_deb_sha256: str = XPRINTER_DEB_SHA256,
    data_dir: Path = XPRINTER_DATA_DIR,
    filter_dir: Path | None = None,
) -> XPrinterInstallation:
    """Install only the validated POS-58/76/80 PPDs and matching filters."""
    _require_root()
    source_path = source_path.expanduser()
    if source_path.is_symlink() or not source_path.is_file():
        raise ValidationError(f"XPrinter driver source is not a regular file: {source_path}")
    with source_path.open("rb") as source:
        signature = source.read(32)
    if signature.startswith(b"line=`wc -l $0"):
        raise ValidationError(
            "legacy XPrinter 2.4.0 installers contain only Intel Linux filters; "
            "use the automatic v3.13.11 download on Raspberry Pi"
        )
    is_deb = signature.startswith(b"!<arch>\n")
    if not is_deb and not signature.startswith(b"Rar!\x1a\x07"):
        raise ValidationError(
            "XPrinter driver source is neither the official RAR nor Debian package"
        )
    host_version = os_version or _host_version()
    dependency = "libcupsimage2t64" if host_version == "13" else "libcupsimage2"
    _install_dependencies(runner, state, [*(["unar"] if not is_deb else []), dependency])
    if is_deb:
        payload = load_xprinter_payload(
            source_path,
            machine=machine,
            expected_sha256=expected_deb_sha256,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="airprint-server-xprinter-deb-") as temporary:
            package = _extract_xprinter_deb(
                runner,
                source_path,
                Path(temporary),
                expected_archive_sha256=expected_download_sha256,
            )
            payload = load_xprinter_payload(
                package,
                machine=machine,
                expected_sha256=expected_deb_sha256,
            )
    selected_filter_dir = filter_dir or _filter_directory(runner)
    ppd_paths = {
        model: data_dir / f"POS-{model}.ppd" for model in XPRINTER_MODELS
    }
    filter_paths = {
        name: selected_filter_dir / f"{name}-pos" for name in payload.filters
    }
    installation = XPrinterInstallation(
        payload.version,
        payload.architecture,
        ppd_paths,
        filter_paths,
    )
    if runner.dry_run:
        return installation

    staged: dict[Path, str] = {}
    try:
        for model, content in payload.ppds.items():
            staged[ppd_paths[model]] = _stage_file(ppd_paths[model], content, 0o644)
        for name, content in payload.filters.items():
            destination = filter_paths[name]
            staged[destination] = _stage_file(destination, content, 0o755)
        for destination in filter_paths.values():
            linkage = runner.run(["ldd", staged[destination]], check=False)
            if linkage.returncode or "not found" in f"{linkage.stdout}\n{linkage.stderr}":
                raise RuntimeError(
                    f"XPrinter filter has unresolved runtime dependencies: {destination.name}"
                )
        for destination, temporary in staged.items():
            os.replace(temporary, destination)
    finally:
        for temporary in staged.values():
            if os.path.exists(temporary):
                os.unlink(temporary)
    runner.run(["systemctl", "reload-or-restart", "cups.service"])
    details = {
        "version": payload.version,
        "architecture": payload.architecture,
        "package_sha256": payload.package_sha256,
    }
    details.update({f"ppd_{model}": str(path) for model, path in ppd_paths.items()})
    details.update({f"filter_{name}": str(path) for name, path in filter_paths.items()})
    state.vendor_drivers["xprinter-pos-cups"] = details
    return installation


def remove_xprinter_driver(
    state: State,
    *,
    data_dir: Path = XPRINTER_DATA_DIR,
    filter_dir: Path | None = None,
) -> None:
    """Remove only XPrinter paths previously recorded by this project."""
    _require_root()
    details = state.vendor_drivers.get("xprinter-pos-cups")
    if not details:
        return
    expected_ppds = {
        model: data_dir / f"POS-{model}.ppd" for model in XPRINTER_MODELS
    }
    recorded_ppds = {
        model: Path(details.get(f"ppd_{model}", "")) for model in XPRINTER_MODELS
    }
    filter_names = set(XPRINTER_MODEL_FILTERS.values())
    recorded_filters = {
        name: Path(details.get(f"filter_{name}", "")) for name in filter_names
    }
    selected_filter_dir = filter_dir or next(iter(recorded_filters.values())).parent
    expected_filters = {
        name: selected_filter_dir / f"{name}-pos" for name in filter_names
    }
    if recorded_ppds != expected_ppds or recorded_filters != expected_filters:
        raise RuntimeError("refusing to remove unexpected XPrinter driver paths")
    if filter_dir is None and not (
        selected_filter_dir.is_absolute()
        and selected_filter_dir.name == "filter"
        and "cups" in selected_filter_dir.parts
        and selected_filter_dir.parts[:2] == ("/", "usr")
    ):
        raise RuntimeError("refusing to remove XPrinter filters outside the CUPS directory")
    for path in [*recorded_ppds.values(), *recorded_filters.values()]:
        if path.is_symlink():
            raise RuntimeError(f"refusing to remove symbolic link: {path}")
        if path.exists():
            path.unlink()
    if data_dir.exists() and not any(data_dir.iterdir()):
        data_dir.rmdir()
    del state.vendor_drivers["xprinter-pos-cups"]
