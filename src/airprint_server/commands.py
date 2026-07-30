"""Safe external command execution."""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

LOG = logging.getLogger("airprint-server")


class CommandError(RuntimeError):
    """An external command failed."""

    def __init__(self, result: CommandResult) -> None:
        super().__init__(
            f"command failed ({result.returncode}): {shlex.join(result.args)}"
            + (f"\n{result.stderr.strip()}" if result.stderr.strip() else "")
        )
        self.result = result


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    dry_run: bool = False


class Runner:
    """Run commands without invoking a shell; optionally report a dry run."""

    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    def run(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
        input_text: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        command = tuple(str(arg) for arg in args)
        if not command or any("\x00" in arg for arg in command):
            raise ValueError("invalid command argument")
        LOG.info("%s%s", "[dry-run] " if self.dry_run else "", shlex.join(command))
        if self.dry_run:
            return CommandResult(command, 0, dry_run=True)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            input=input_text,
            timeout=timeout,
            env=env,
        )
        result = CommandResult(command, completed.returncode, completed.stdout, completed.stderr)
        if check and completed.returncode:
            raise CommandError(result)
        return result


def command_exists(name: str, runner: Runner | None = None) -> bool:
    del runner
    return shutil.which(name) is not None
