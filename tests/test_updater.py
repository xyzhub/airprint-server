from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import FakeRunner

from airprint_server.commands import CommandResult
from airprint_server.config import State, load_state, save_state
from airprint_server.updater import (
    UPDATE_BRANCH,
    UPDATE_REMOTE,
    remote_release_version,
    remote_revision,
    update_project,
    validate_managed_source,
)

OLD_REVISION = "1" * 40
NEW_REVISION = "2" * 40


def test_remote_revision_parsing() -> None:
    command = ("git", "ls-remote", UPDATE_REMOTE, f"refs/heads/{UPDATE_BRANCH}")
    runner = FakeRunner(
        {command: CommandResult(command, 0, f"{NEW_REVISION}\trefs/heads/main\n")}
    )
    assert remote_revision(runner) == NEW_REVISION  # type: ignore[arg-type]


def test_remote_release_version_finds_tag_for_target_revision() -> None:
    command = ("git", "ls-remote", "--tags", UPDATE_REMOTE, "refs/tags/v*")
    runner = FakeRunner(
        {
            command: CommandResult(
                command,
                0,
                f"{'3' * 40}\trefs/tags/v0.1.0\n"
                f"{NEW_REVISION}\trefs/tags/v0.2.0\n",
            )
        }
    )

    assert remote_release_version(runner, NEW_REVISION) == "0.2.0"  # type: ignore[arg-type]


def test_update_confirmation_shows_versions_and_short_revisions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_path = tmp_path / "state.yaml"
    save_state(State(installed_revision=OLD_REVISION), state_path)
    monkeypatch.setattr(
        "airprint_server.updater.remote_revision",
        lambda *_args, **_kwargs: NEW_REVISION,
    )
    monkeypatch.setattr(
        "airprint_server.updater.remote_release_version",
        lambda *_args, **_kwargs: "0.2.0",
    )
    prompts: list[str] = []

    result = update_project(
        FakeRunner(),  # type: ignore[arg-type]
        confirm=lambda message: prompts.append(message) or False,
        state_path=state_path,
        source=tmp_path / "source",
        read_runner=FakeRunner(),  # type: ignore[arg-type]
        current_version="0.1.0",
    )

    assert result.cancelled
    assert prompts == [
        "Update airprint-server from v0.1.0 (111111111111) "
        "to v0.2.0 (222222222222)?"
    ]


def test_update_check_does_not_change_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.yaml"
    source = tmp_path / "source"
    source.mkdir()
    save_state(State(installed_revision=OLD_REVISION), state_path)
    command = ("git", "ls-remote", UPDATE_REMOTE, f"refs/heads/{UPDATE_BRANCH}")
    reader = FakeRunner(
        {command: CommandResult(command, 0, f"{NEW_REVISION}\trefs/heads/main\n")}
    )
    result = update_project(
        FakeRunner(),  # type: ignore[arg-type]
        confirm=lambda _message: True,
        check_only=True,
        state_path=state_path,
        source=source,
        read_runner=reader,  # type: ignore[arg-type]
    )
    assert result.update_available
    assert result.available_revision == NEW_REVISION
    assert load_state(state_path).installed_revision == OLD_REVISION


def test_update_dry_run_does_not_create_source(tmp_path: Path) -> None:
    state_path = tmp_path / "state.yaml"
    source = tmp_path / "source"
    save_state(State(), state_path)
    command = ("git", "ls-remote", UPDATE_REMOTE, f"refs/heads/{UPDATE_BRANCH}")
    reader = FakeRunner(
        {command: CommandResult(command, 0, f"{NEW_REVISION}\trefs/heads/main\n")}
    )
    result = update_project(
        FakeRunner(dry_run=True),  # type: ignore[arg-type]
        confirm=lambda _message: True,
        state_path=state_path,
        source=source,
        read_runner=reader,  # type: ignore[arg-type]
    )
    assert result.dry_run
    assert not source.exists()
    assert load_state(state_path).installed_revision is None


def test_managed_source_rejects_wrong_remote(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / ".git").mkdir(parents=True)
    command = ("git", "-C", str(source), "config", "--get", "remote.origin.url")
    reader = FakeRunner(
        {command: CommandResult(command, 0, "https://example.invalid/repository.git\n")}
    )
    with pytest.raises(RuntimeError, match="expected"):
        validate_managed_source(
            source,
            reader,  # type: ignore[arg-type]
            enforce_root_owner=False,
        )


def test_fast_forward_update_reinstalls_and_records_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_path = tmp_path / "state.yaml"
    source = tmp_path / "source"
    (source / ".git").mkdir(parents=True)
    installer = source / "install.sh"
    installer.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    installer.chmod(0o755)
    save_state(State(installed_revision=OLD_REVISION), state_path)

    revisions: Iterator[str] = iter((OLD_REVISION, NEW_REVISION))
    monkeypatch.setattr(
        "airprint_server.updater.remote_revision",
        lambda *_args, **_kwargs: NEW_REVISION,
    )
    monkeypatch.setattr(
        "airprint_server.updater.validate_managed_source",
        lambda *_args, **_kwargs: next(revisions),
    )
    runner = FakeRunner()
    result = update_project(
        runner,  # type: ignore[arg-type]
        confirm=lambda _message: True,
        state_path=state_path,
        source=source,
        read_runner=FakeRunner(),  # type: ignore[arg-type]
        enforce_root_owner=False,
    )

    assert result.changed
    assert ("git", "-C", str(source), "fetch", "--prune", "origin", "main") in runner.calls
    assert (
        "git",
        "-C",
        str(source),
        "merge",
        "--ff-only",
        "origin/main",
    ) in runner.calls
    assert (str(installer), "--no-wizard", "--yes") in runner.calls
    updated = load_state(state_path)
    assert updated.installed_revision == NEW_REVISION
    assert updated.update_remote == UPDATE_REMOTE
    assert updated.update_source == str(source)
