from __future__ import annotations

from io import StringIO

import pytest

from airprint_server.progress import ProgressBar


def test_noninteractive_progress_uses_stable_log_lines() -> None:
    output = StringIO()
    with ProgressBar(2, stream=output, interactive=False) as progress:
        progress.update(0, "Checking host")
        progress.update(1, "Installing packages")
        progress.update(2, "Installation complete")

    assert output.getvalue().splitlines() == [
        "[0/2] Checking host",
        "[1/2] Installing packages",
        "[2/2] Installation complete",
    ]


def test_progress_rejects_backwards_updates() -> None:
    progress = ProgressBar(2, stream=StringIO(), interactive=False)
    progress.update(1, "Installing")
    with pytest.raises(ValueError, match="backwards"):
        progress.update(0, "Checking")


def test_failed_progress_names_current_phase() -> None:
    output = StringIO()
    with (
        pytest.raises(RuntimeError, match="boom"),
        ProgressBar(2, stream=output, interactive=False) as progress,
    ):
        progress.update(1, "Installing packages")
        raise RuntimeError("boom")

    assert output.getvalue().splitlines()[-1] == "[failed] Installing packages"
