"""Tests for resolve_efforts().

Per-worker precedence (highest first):
  1. --effort-<worker> CLI flag
  2. --effort CLI flag
  3. LEERIE_EFFORT_<WORKER> env var
  4. LEERIE_EFFORT env var
  5. effort_<worker> in leerie.toml
  6. effort in leerie.toml
  7. EFFORT_DEFAULT_PER_WORKER[<worker>] (judgment workers → "high")
  8. EFFORT_DEFAULT (None — flag omitted from CLI invocation)

The judgment-workers-pinned, acting-workers-unset split was introduced
to reduce same-job subtask-count variance. Sampling stochasticity in
`claude -p` cannot be pinned (no --temperature / --seed); effort is the
strongest determinism dial available.
"""
from __future__ import annotations

import argparse

import pytest


WORKERS = ("classifier", "planner", "reconciler", "plan_overlap_judge",
           "satisfied_probe", "provision", "implementer", "integrator",
           "conformer")

# The expected default per worker, with no overrides. Judgment workers
# get "high"; acting workers (implementer, conformer) and the per-subtask
# satisfied_probe resolve to None.
DEFAULTS: dict[str, str | None] = {
    "classifier": "high",
    "planner":    "high",
    "reconciler": "high",
    "plan_overlap_judge": "high",
    "satisfied_probe": None,
    "provision":  "high",
    "integrator": "high",
    "implementer": None,
    "conformer":  None,
}


def ns(**overrides):
    """Build an argparse.Namespace with --effort and every --effort-<w>
    defaulted to None (the argparse default when the flag isn't passed)."""
    base = {"effort": None, **{f"effort_{w}": None for w in WORKERS}}
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """An empty repo-root with every LEERIE_EFFORT* env var unset."""
    monkeypatch.delenv("LEERIE_EFFORT", raising=False)
    for w in WORKERS:
        monkeypatch.delenv(f"LEERIE_EFFORT_{w.upper()}", raising=False)
    return tmp_path


def test_all_unset_defaults_per_worker(leerie, repo_root):
    """With no overrides, judgment workers default to 'high' and acting
    workers default to None (no --effort flag passed). Pins both the
    global default (None) and the per-worker override table together."""
    efforts = leerie.resolve_efforts(repo_root, ns())
    worker_slice = {w: efforts[w] for w in WORKERS}
    assert worker_slice == DEFAULTS
    assert leerie.EFFORT_DEFAULT is None
    # Six judgment workers default to high; nothing else.
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("planner") == "high"
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("classifier") == "high"
    assert "implementer" not in leerie.EFFORT_DEFAULT_PER_WORKER
    assert "conformer" not in leerie.EFFORT_DEFAULT_PER_WORKER


def test_global_env_applies_to_every_worker(leerie, repo_root, monkeypatch):
    """A global LEERIE_EFFORT overrides the per-worker defaults too, so
    acting workers get the global value rather than None."""
    monkeypatch.setenv("LEERIE_EFFORT", "max")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert {w: efforts[w] for w in WORKERS} == {w: "max" for w in WORKERS}


def test_per_worker_env_overrides_global_env(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_EFFORT", "low")
    monkeypatch.setenv("LEERIE_EFFORT_PLANNER", "max")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["planner"] == "max"
    for w in WORKERS:
        if w != "planner":
            assert efforts[w] == "low"


def test_global_toml_applies_to_every_worker(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("effort = high\n")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert {w: efforts[w] for w in WORKERS} == {w: "high" for w in WORKERS}


def test_per_worker_toml_overrides_global_toml(leerie, repo_root):
    (repo_root / "leerie.toml").write_text(
        "effort = low\neffort_integrator = max\n")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["integrator"] == "max"
    for w in WORKERS:
        if w != "integrator":
            assert efforts[w] == "low"


def test_env_beats_toml(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("effort = low\n")
    monkeypatch.setenv("LEERIE_EFFORT", "max")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert {w: efforts[w] for w in WORKERS} == {w: "max" for w in WORKERS}


def test_global_cli_beats_global_env_and_toml(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("effort = low\n")
    monkeypatch.setenv("LEERIE_EFFORT", "low")
    efforts = leerie.resolve_efforts(repo_root, ns(effort="max"))
    assert {w: efforts[w] for w in WORKERS} == {w: "max" for w in WORKERS}


def test_per_worker_cli_beats_everything(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text(
        "effort = low\neffort_planner = low\n")
    monkeypatch.setenv("LEERIE_EFFORT", "low")
    monkeypatch.setenv("LEERIE_EFFORT_PLANNER", "low")
    efforts = leerie.resolve_efforts(repo_root,
                                   ns(effort="low", effort_planner="max"))
    assert efforts["planner"] == "max"
    for w in WORKERS:
        if w != "planner":
            assert efforts[w] == "low"


def test_full_precedence_per_worker(leerie, repo_root, monkeypatch):
    # Per-worker CLI > global CLI > per-worker env > global env >
    # per-worker TOML > global TOML > per-worker default > EFFORT_DEFAULT.
    # Exercise one rung at a time on the planner (which has a per-worker
    # default of "high"). Implementer has no per-worker default — we
    # check both fallthrough behaviors at the bottom.
    cfg = repo_root / "leerie.toml"

    # rung 7 (per-worker default → "high" for planner)
    assert leerie.resolve_efforts(repo_root, ns())["planner"] == "high"
    # And rung 8 for implementer (no per-worker default, EFFORT_DEFAULT is None)
    assert leerie.resolve_efforts(repo_root, ns())["implementer"] is None

    # rung 6: global TOML beats default
    cfg.write_text("effort = low\n")
    assert leerie.resolve_efforts(repo_root, ns())["planner"] == "low"
    assert leerie.resolve_efforts(repo_root, ns())["implementer"] == "low"

    # rung 5: per-worker TOML beats global TOML
    cfg.write_text("effort = low\neffort_planner = medium\n")
    assert leerie.resolve_efforts(repo_root, ns())["planner"] == "medium"

    # rung 4: global env beats both TOML rungs
    monkeypatch.setenv("LEERIE_EFFORT", "max")
    assert leerie.resolve_efforts(repo_root, ns())["planner"] == "max"

    # rung 3: per-worker env beats global env
    monkeypatch.setenv("LEERIE_EFFORT_PLANNER", "low")
    assert leerie.resolve_efforts(repo_root, ns())["planner"] == "low"

    # rung 2: global CLI beats env (per-worker CLI still unset)
    assert leerie.resolve_efforts(repo_root, ns(effort="xhigh"))["planner"] == "xhigh"

    # rung 1: per-worker CLI beats global CLI
    assert leerie.resolve_efforts(
        repo_root, ns(effort="xhigh", effort_planner="max"))["planner"] == "max"


def test_bad_global_env_dies(leerie, repo_root, monkeypatch, capsys):
    monkeypatch.setenv("LEERIE_EFFORT", "extreme")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_efforts(repo_root, ns())
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "LEERIE_EFFORT" in err
    assert "extreme" in err
    assert "is not one of" in err


def test_bad_per_worker_env_dies(leerie, repo_root, monkeypatch, capsys):
    monkeypatch.setenv("LEERIE_EFFORT_INTEGRATOR", "ludicrous")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_efforts(repo_root, ns())
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "LEERIE_EFFORT_INTEGRATOR" in err
    assert "ludicrous" in err


def test_bad_global_toml_dies(leerie, repo_root, capsys):
    (repo_root / "leerie.toml").write_text("effort = bogus\n")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_efforts(repo_root, ns())
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "leerie.toml" in err
    assert "bogus" in err
    assert "is not one of" in err


def test_bad_per_worker_toml_dies(leerie, repo_root, capsys):
    (repo_root / "leerie.toml").write_text("effort_classifier = nope\n")
    with pytest.raises(SystemExit) as exc:
        leerie.resolve_efforts(repo_root, ns())
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "effort_classifier" in err
    assert "nope" in err


def test_empty_env_treated_as_unset(leerie, repo_root, monkeypatch):
    """Empty / whitespace-only env vars fall through as if unset, so the
    per-worker default table (DEFAULTS) wins — not "" for all. Pins that
    the strip-then-falsy check in resolve_efforts hasn't been replaced
    with a default-value substitution."""
    monkeypatch.setenv("LEERIE_EFFORT", "")
    monkeypatch.setenv("LEERIE_EFFORT_PLANNER", "   ")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert {w: efforts[w] for w in WORKERS} == DEFAULTS


def test_every_value_accepted_in_global_env(leerie, repo_root, monkeypatch):
    for level in leerie.EFFORT_VALUES:
        monkeypatch.setenv("LEERIE_EFFORT", level)
        efforts = leerie.resolve_efforts(repo_root, ns())
        assert {w: efforts[w] for w in WORKERS} == {w: level for w in WORKERS}


def test_post_run_workers_resolved(leerie, repo_root, monkeypatch):
    """Judge and heal are not in WORKER_TYPES so they don't get per-worker
    --effort-<W> flags, but they still honor the global LEERIE_EFFORT. With
    no global override they fall through to None (no per-worker default)."""
    # Default: no override → None
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["judge"] is None
    assert efforts["heal"] is None

    # Global env propagates to post-run workers
    monkeypatch.setenv("LEERIE_EFFORT", "max")
    efforts = leerie.resolve_efforts(repo_root, ns())
    assert efforts["judge"] == "max"
    assert efforts["heal"] == "max"


def test_judgment_workers_pinned_set(leerie):
    """Pins which workers default to 'high'. Adding a worker to the
    judgment set should be a deliberate decision, not a silent drift.
    Acting workers (implementer, conformer) must stay absent — their
    reasoning depth is bounded by the DESIGN §8 evidence gate."""
    assert set(leerie.EFFORT_DEFAULT_PER_WORKER) == {
        "classifier", "planner", "reconciler", "plan_overlap_judge",
        "provision", "integrator", "pr_writer", "dep_capture",
    }
    assert "implementer" not in leerie.EFFORT_DEFAULT_PER_WORKER
    assert "conformer" not in leerie.EFFORT_DEFAULT_PER_WORKER


def test_effort_values_set(leerie):
    """Pins the supported levels exposed by `claude -p --effort`. If the
    CLI adds a level (or drops one), this test fails and the caller is
    forced to revisit EFFORT_VALUES rather than silently accepting it."""
    assert leerie.EFFORT_VALUES == ("low", "medium", "high", "xhigh", "max")
