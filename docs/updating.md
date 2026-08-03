# Updating airprint-server

Check the fixed upstream `main` branch without changing the host:

```sh
sudo airprint-server update --check
```

Install an available revision:

```sh
sudo airprint-server update
```

The command displays the installed and target release versions, with abbreviated
Git revisions for precise identification, and asks for confirmation. For
example:

```text
Update airprint-server from v0.1.0 (3ba4142e543b) to v0.2.0 (a1b2c3d4e5f6)? [y/N]
```

If a target commit does not have a release tag, the updater falls back to its
abbreviated Git revision. It then performs a fast-forward update and invokes the
idempotent installer with `--no-wizard`, so existing queues remain managed and
the interactive setup does not run.

## First update

The updater does not execute the user's original clone as root. On first use,
it creates `/var/lib/airprint-server/source` as a dedicated root-owned checkout
of:

```text
https://github.com/xyzhub/airprint-server.git
```

Later updates reuse only that checkout. Its source path, remote, and installed
revision are recorded in `/var/lib/airprint-server/state.yaml`.

An installation older than the updater must bootstrap it once:

```sh
cd ~/airprint-server
git pull --ff-only
sudo ./install.sh --no-wizard
```

All subsequent updates can use the CLI.

## Safety checks

Before executing downloaded code as root, the updater verifies:

- the managed path is not a symbolic link;
- the source and parent directory are root-owned and not group/world-writable;
- `origin` is the fixed HTTPS repository;
- the active branch is `main`;
- the checkout has no tracked or untracked changes;
- the fetched revision is a fast-forward descendant;
- the checked-out commit exactly matches the revision observed remotely;
- `install.sh` exists and is executable.

The update is intentionally restricted to one repository and branch. HTTPS
and Git commit identity do not protect against compromise of the upstream
GitHub account. Administrators needing stronger release provenance should
review the target commit before confirmation and pin deployments through
their own configuration-management workflow.

## Non-interactive use

```sh
sudo airprint-server update --yes
```

`--dry-run` checks the target revision and reports the planned action without
creating or changing the managed checkout:

```sh
sudo airprint-server update --dry-run
```

If an installation attempt fails, managed queues and state remain in place.
Resolve the reported error and rerun the update. The updater never performs a
hard reset or overwrites local changes.
