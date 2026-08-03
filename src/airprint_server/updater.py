"""Conservative self-update support using a root-owned managed checkout."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from airprint_server import __version__
from airprint_server.commands import Runner
from airprint_server.config import STATE_DIR, STATE_PATH, load_state, save_state

UPDATE_REMOTE = "https://github.com/xyzhub/airprint-server.git"
UPDATE_BRANCH = "main"
UPDATE_SOURCE = STATE_DIR / "source"
REVISION_RE = re.compile(r"^[0-9a-f]{40,64}$")
RELEASE_TAG_RE = re.compile(r"^refs/tags/v(?P<version>\d+\.\d+\.\d+)(?:\^\{\})?$")


@dataclass(frozen=True)
class UpdateResult:
    installed_revision: str | None
    available_revision: str
    update_available: bool
    changed: bool = False
    cancelled: bool = False
    dry_run: bool = False
    installed_version: str | None = None
    available_version: str | None = None


def remote_revision(
    runner: Runner,
    *,
    remote: str = UPDATE_REMOTE,
    branch: str = UPDATE_BRANCH,
) -> str:
    result = runner.run(
        ["git", "ls-remote", remote, f"refs/heads/{branch}"],
        check=False,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(
            f"could not check {remote}: {result.stderr.strip() or 'git ls-remote failed'}"
        )
    revision = result.stdout.partition("\t")[0].strip().lower()
    if not REVISION_RE.fullmatch(revision):
        raise RuntimeError(f"remote branch {branch!r} did not return a valid Git revision")
    return revision


def remote_release_version(
    runner: Runner,
    revision: str,
    *,
    remote: str = UPDATE_REMOTE,
) -> str | None:
    """Return the release tag attached to a revision, when one is published."""
    result = runner.run(
        ["git", "ls-remote", "--tags", remote, "refs/tags/v*"],
        check=False,
        timeout=30,
    )
    if result.returncode:
        return None
    matches: list[tuple[tuple[int, int, int], str]] = []
    for line in result.stdout.splitlines():
        raw_revision, separator, reference = line.partition("\t")
        match = RELEASE_TAG_RE.fullmatch(reference.strip())
        if not separator or raw_revision.strip().lower() != revision or not match:
            continue
        version = match.group("version")
        major, minor, patch = version.split(".")
        matches.append(((int(major), int(minor), int(patch)), version))
    return max(matches)[1] if matches else None


def revision_label(revision: str | None, version: str | None) -> str:
    short_revision = revision[:12] if revision else "not installed"
    return f"v{version} ({short_revision})" if version else f"commit {short_revision}"


def _read_git(runner: Runner, source: Path, *args: str) -> str:
    result = runner.run(["git", "-C", str(source), *args], check=False)
    if result.returncode:
        raise RuntimeError(
            f"cannot inspect managed update source {source}: "
            f"{result.stderr.strip() or 'git command failed'}"
        )
    return result.stdout.strip()


def validate_managed_source(
    source: Path,
    runner: Runner,
    *,
    remote: str = UPDATE_REMOTE,
    branch: str = UPDATE_BRANCH,
    enforce_root_owner: bool = True,
) -> str:
    if source.is_symlink():
        raise RuntimeError(f"managed update source may not be a symbolic link: {source}")
    if not source.is_dir() or not (source / ".git").is_dir():
        raise RuntimeError(f"managed update source is not a Git checkout: {source}")
    if enforce_root_owner:
        source_stat = source.stat()
        parent_stat = source.parent.stat()
        if source_stat.st_uid != 0 or parent_stat.st_uid != 0:
            raise RuntimeError(
                f"managed update source and parent must be owned by root: {source}"
            )
        writable_mask = stat.S_IWGRP | stat.S_IWOTH
        if source_stat.st_mode & writable_mask or parent_stat.st_mode & writable_mask:
            raise RuntimeError(
                f"managed update source and parent may not be group/world-writable: {source}"
            )
    configured_remote = _read_git(runner, source, "config", "--get", "remote.origin.url")
    if configured_remote != remote:
        raise RuntimeError(
            f"managed source remote is {configured_remote!r}, expected {remote!r}; refusing update"
        )
    configured_branch = _read_git(runner, source, "branch", "--show-current")
    if configured_branch != branch:
        raise RuntimeError(
            f"managed source branch is {configured_branch!r}, expected {branch!r}"
        )
    dirty = _read_git(runner, source, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise RuntimeError(
            f"managed update source contains local changes: {source}; refusing to overwrite them"
        )
    revision = _read_git(runner, source, "rev-parse", "HEAD").lower()
    if not REVISION_RE.fullmatch(revision):
        raise RuntimeError("managed update source has an invalid HEAD revision")
    return revision


def update_project(
    runner: Runner,
    *,
    confirm: Callable[[str], bool],
    check_only: bool = False,
    state_path: Path = STATE_PATH,
    source: Path = UPDATE_SOURCE,
    remote: str = UPDATE_REMOTE,
    branch: str = UPDATE_BRANCH,
    read_runner: Runner | None = None,
    enforce_root_owner: bool = True,
    current_version: str = __version__,
) -> UpdateResult:
    """Check or install a fast-forward update from the fixed upstream repository."""
    reader = read_runner or Runner()
    state = load_state(state_path)
    target = remote_revision(reader, remote=remote, branch=branch)
    target_version = remote_release_version(reader, target, remote=remote)
    installed = state.installed_revision
    if source.is_symlink():
        raise RuntimeError(f"managed update source may not be a symbolic link: {source}")
    available = installed != target or not source.exists()
    if check_only:
        return UpdateResult(
            installed,
            target,
            available,
            installed_version=current_version,
            available_version=target_version,
        )

    current_source: str | None = None
    if source.exists():
        current_source = validate_managed_source(
            source,
            reader,
            remote=remote,
            branch=branch,
            enforce_root_owner=enforce_root_owner,
        )
        available = available or current_source != target
    if not available:
        return UpdateResult(
            installed,
            target,
            False,
            installed_version=current_version,
            available_version=target_version,
        )

    current_revision = installed or current_source
    current_label = revision_label(current_revision, current_version)
    target_label = revision_label(target, target_version)
    if not confirm(f"Update airprint-server from {current_label} to {target_label}?"):
        return UpdateResult(
            installed,
            target,
            True,
            cancelled=True,
            installed_version=current_version,
            available_version=target_version,
        )
    if runner.dry_run:
        return UpdateResult(
            installed,
            target,
            True,
            dry_run=True,
            installed_version=current_version,
            available_version=target_version,
        )

    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        runner.run(
            [
                "git",
                "clone",
                "--branch",
                branch,
                "--single-branch",
                remote,
                str(source),
            ]
        )
    else:
        runner.run(["git", "-C", str(source), "fetch", "--prune", "origin", branch])
        ancestor = runner.run(
            ["git", "-C", str(source), "merge-base", "--is-ancestor", "HEAD", f"origin/{branch}"],
            check=False,
        )
        if ancestor.returncode:
            raise RuntimeError("managed checkout cannot be fast-forwarded; update was not applied")
        runner.run(["git", "-C", str(source), "merge", "--ff-only", f"origin/{branch}"])

    checked_out = validate_managed_source(
        source,
        reader,
        remote=remote,
        branch=branch,
        enforce_root_owner=enforce_root_owner,
    )
    if checked_out != target:
        raise RuntimeError(
            f"managed checkout resolved to {checked_out[:12]}, expected {target[:12]}"
        )
    install_script = source / "install.sh"
    if not install_script.is_file() or not os.access(install_script, os.X_OK):
        raise RuntimeError(f"updated repository has no executable installer: {install_script}")
    runner.run([str(install_script), "--no-wizard", "--yes"])

    refreshed = load_state(state_path)
    refreshed.update_source = str(source)
    refreshed.update_remote = remote
    refreshed.installed_revision = target
    save_state(refreshed, state_path)
    return UpdateResult(
        installed,
        target,
        True,
        changed=True,
        installed_version=current_version,
        available_version=target_version,
    )
