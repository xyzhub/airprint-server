import subprocess

from airprint_server.commands import Runner


def test_dry_run_never_invokes_subprocess(monkeypatch: object) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess was invoked")

    monkeypatch.setattr(subprocess, "run", fail)  # type: ignore[attr-defined]
    result = Runner(dry_run=True).run(["lpadmin", "-x", "Queue"])
    assert result.dry_run
    assert result.args == ("lpadmin", "-x", "Queue")

