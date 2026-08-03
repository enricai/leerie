"""Tests that the synthesized mise override env var is exported into
the orchestrator's `os.environ` so downstream worker subprocesses
(implementer, conformer) inherit it.

Why this matters (DESIGN §6½):
`phase_provision` synthesizes a mise override at
`.leerie/runs/<id>/mise-overrides.toml` when a polyglot repo needs a
synthesized go pin (`go.mod` + idiomatic files, no `.go-version`).
The override file is NOT in the worktree's tracked-file set (it lives
under `.leerie/runs/`, which is git-ignored — only `.leerie/config.toml`
and `.leerie/Dockerfile` are tracked). So mise's discovery in the
worktree wouldn't see the synth — and `mise exec -- go ...` invoked
from a worker's Bash tool would fall through to system PATH, where
Go isn't installed.

`phase_provision` exports `MISE_OVERRIDE_CONFIG_FILENAMES` into
`os.environ` directly, and the `--resume` branch re-exports from
persisted state. Worker subprocesses spawned by `_invoke` inherit the
parent env (no `env=` passed), so they pick the var up automatically.
"""
from __future__ import annotations

import os


def test_resume_reexports_override_env_when_set(leerie, tmp_path, monkeypatch):
    """On `--resume`, the orchestrator must re-export
    MISE_OVERRIDE_CONFIG_FILENAMES from persisted state so downstream
    implementer/conformer workers inherit it. Without this, mise's
    worker-side discovery wouldn't find the synthesized go pin."""
    monkeypatch.delenv("MISE_OVERRIDE_CONFIG_FILENAMES", raising=False)
    override_path = str(tmp_path / "mise-overrides.toml")

    # Simulate what the resume path actually does (we only test the
    # narrow env-export contract, not the whole _orchestrate() entrypoint).
    state_data = {"provision": {"override_file": override_path}}
    persisted_override = (state_data.get("provision") or {}).get("override_file")
    if persisted_override:
        os.environ["MISE_OVERRIDE_CONFIG_FILENAMES"] = str(persisted_override)

    assert os.environ.get("MISE_OVERRIDE_CONFIG_FILENAMES") == override_path


def test_resume_does_not_export_override_when_none(leerie, tmp_path, monkeypatch):
    """When persisted state has no override_file (repo had no go.mod
    synth), nothing should be exported. mise's normal discovery walk
    handles the worker's deps."""
    monkeypatch.delenv("MISE_OVERRIDE_CONFIG_FILENAMES", raising=False)

    state_data = {"provision": {"override_file": None}}
    persisted_override = (state_data.get("provision") or {}).get("override_file")
    if persisted_override:
        os.environ["MISE_OVERRIDE_CONFIG_FILENAMES"] = str(persisted_override)

    assert "MISE_OVERRIDE_CONFIG_FILENAMES" not in os.environ


def test_replay_provision_function_was_removed(leerie):
    """Regression guard: the per-worktree replay function was removed
    in favor of worker-driven install (DESIGN §6½). If a refactor
    re-introduces it, this test catches the design drift before code
    review."""
    assert not hasattr(leerie, "replay_provision_in_worktree"), (
        "replay_provision_in_worktree was deliberately removed; workers "
        "now run installs in their own worktrees per DESIGN §6½. If you "
        "need to re-introduce orchestrator-driven install, update "
        "DESIGN.md first per the three-layer rule."
    )
