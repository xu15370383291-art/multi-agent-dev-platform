"""Operator maintenance starts must not accidentally trigger paid paused work."""

import json
import sys
from types import SimpleNamespace

import pytest

from app.config import settings
from app.platform_runtime import require_resume_mode


@pytest.mark.parametrize("held", [False, True])
def test_running_worker_mode_is_explicit_and_cannot_silently_change(monkeypatch, held):
    monkeypatch.setattr(settings(), "auto_correction_enabled", True)
    rows = [{"component": "worker", "automatic_paused_run_recovery": not held}]
    require_resume_mode(rows, held)
    with pytest.raises(ValueError, match="resume_mode_differs"):
        require_resume_mode(rows, not held)


def test_start_cli_propagates_maintenance_choice_without_changing_normal_default(monkeypatch, tmp_path):
    from scripts import platform

    calls = []
    monkeypatch.setattr(platform.runtime, "start", lambda *a, **kw: calls.append(kw) or {})
    for flag in [[], ["--hold-auto-resume"]]:
        monkeypatch.setattr(sys, "argv", ["platform", "start", "--state", str(tmp_path), *flag])
        platform.main()
    assert calls == [{"hold_auto_resume": False}, {"hold_auto_resume": True}]


def test_worker_maintenance_identity_and_coordinator_are_both_held(monkeypatch, tmp_path):
    import psutil

    from app import auto_correction, console_output, worker
    from scripts import platform

    monkeypatch.setattr(settings(), "auto_correction_enabled", True)
    monkeypatch.setattr(
        psutil,
        "Process",
        lambda: SimpleNamespace(
            pid=12345,
            create_time=lambda: 42,
            cmdline=lambda: ["python", "-m", "scripts.platform", *sys.argv[1:]],
        ),
    )  # Explicit process fixture; pytest itself was not spawned with serve args.
    monkeypatch.setattr(console_output, "LimitedConsole", lambda stream: stream)
    seen = []

    def run():
        assert settings().auto_correction_enabled is True
        assert settings().auto_resume_paused_runs is False
        # Disabled tick must return before touching a DB or model.
        assert auto_correction.tick(None, None) is False
        seen.append(True)

    monkeypatch.setattr(worker, "main", run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "platform",
            "serve",
            "--component",
            "worker",
            "--instance",
            "fixture",
            "--state",
            str(tmp_path),
            "--hold-auto-resume",
        ],
    )
    platform.main()
    identity = json.loads((tmp_path / "worker-fixture.identity.json").read_text())
    assert identity["automatic_paused_run_recovery"] is False
    assert "--hold-auto-resume" in identity["command"]
    assert seen == [True]


def test_read_only_commands_cannot_apply_a_hidden_maintenance_flag(monkeypatch):
    from scripts import platform

    monkeypatch.setattr(sys, "argv", ["platform", "status", "--hold-auto-resume"])
    with pytest.raises(SystemExit) as exc:
        platform.main()
    assert exc.value.code == 2
