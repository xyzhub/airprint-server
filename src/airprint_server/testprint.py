"""Generate test artwork and submit it through CUPS."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from airprint_server.commands import Runner
from airprint_server.cups import submit_file
from airprint_server.profiles import PrinterProfile


def _escape_ps(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def create_test_postscript(
    path: Path, printer: str, profile: PrinterProfile | None, now: datetime | None = None
) -> None:
    timestamp = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    profile_name = profile.display_name if profile else "vendor/custom driver"
    width = 226 if profile and profile.paper_width_mm and profile.paper_width_mm <= 80 else 595
    height = 500 if width == 226 else 842
    lines = [
        "%!PS-Adobe-3.0",
        f"<< /PageSize [{width} {height}] >> setpagedevice",
        "/Courier findfont 9 scalefont setfont",
        f"8 {height - 24} moveto (airprint-server test) show",
        f"8 {height - 40} moveto (Printer: {_escape_ps(printer)}) show",
        f"8 {height - 56} moveto (Profile: {_escape_ps(profile_name)}) show",
        f"8 {height - 72} moveto (Time: {_escape_ps(timestamp)}) show",
        f"8 {height - 94} moveto (|----|----|----|----|----|----|) show",
        f"8 {height - 110} moveto (Plain text: 0123456789 ABC abc) show",
        "0 setgray",
    ]
    # A small raster-like checkerboard proves halftoning/image conversion.
    origin_y = height - 180
    for row in range(8):
        for column in range(16):
            if (row + column) % 2 == 0:
                lines.append(f"{8 + column * 6} {origin_y + row * 6} 6 6 rectfill")
    lines.extend(
        [
            f"8 {origin_y - 18} moveto (Raster calibration pattern) show",
            f"newpath 8 {origin_y - 30} moveto {width - 8} {origin_y - 30} lineto stroke",
            "showpage",
            "%%EOF",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def submit_test(
    runner: Runner, printer: str, profile: PrinterProfile | None, temporary_dir: Path
) -> str:
    path = temporary_dir / "airprint-server-test.ps"
    create_test_postscript(path, printer, profile)
    options = profile.default_options() if profile else {}
    return submit_file(runner, printer, path, options)


def submit_cutter_test(runner: Runner, printer: str, temporary_dir: Path) -> str:
    """Explicit hardware action: submit only an ESC/POS partial-cut command as raw data."""
    path = temporary_dir / "airprint-server-cutter.bin"
    path.write_bytes(b"\n\n\n\x1dV\x01")
    return submit_file(runner, printer, path, {"raw": ""})

