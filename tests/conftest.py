from __future__ import annotations

from collections.abc import Sequence

from airprint_server.commands import CommandResult


class FakeRunner:
    def __init__(
        self,
        responses: dict[tuple[str, ...], CommandResult] | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, ...]] = []
        self.dry_run = dry_run

    def run(self, args: Sequence[str], **kwargs: object) -> CommandResult:
        command = tuple(args)
        self.calls.append(command)
        return self.responses.get(
            command, CommandResult(command, 0, "", "", dry_run=self.dry_run)
        )

