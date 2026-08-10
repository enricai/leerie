"""N14-16 preflight: the per-worker memory ceiling reconciles with a
repo's own declared `--max-old-space-size` (`_declared_node_heap_bytes`
feeding `resolve_worker_memory_max`).

Node's declared heap overrides whatever NODE_OPTIONS leerie injects for a
worker subprocess, so a resolved cgroup ceiling smaller than
heap + `_NODE_HEAP_HEADROOM_BYTES` guarantees an in-cgroup OOM regardless
of how the ceiling was sized. Exercised without a real Node install:

- An auto-derived ceiling is silently raised to heap + headroom when the
  repo declares a heap the current auto-derivation would undershoot.
- An explicit (CLI/env/file) ceiling that undershoots the declared heap is
  refused with an actionable die() naming --worker-memory-max, rather than
  silently overridden.
- A repo with no declared heap, or a repo whose control command declares a
  small heap comfortably under the resolved ceiling, is unaffected.

**Two fixture shapes, and the second is the load-bearing one.** The first
half declares the heap in `.leerie/config.toml`; the second (see "the
package-manager indirection" below) declares it in `package.json` and lets
`resolve_blt` fall through to inference. Only the config.toml shape existed
originally, and it is not how any real repo is configured: measured across
the five repos leerie manages, 2 of 5 declare a heap in `package.json` and
**0 of 5** declare one in `.leerie/config.toml`. So the reconciliation
fired on none of them — including funeralworks, whose OOMs motivated
N14-16 — while this file reported full coverage. A config.toml-only fixture
set is structurally incapable of catching that.
"""
from __future__ import annotations

import pytest


def _write_config(repo_root, *, test_cmd=None, typecheck_cmd=None):
    """Write a `.leerie/config.toml` declaring BLT axes. `typecheck_cmd`
    is folded into `build`, since resolve_blt's axes are build/lint/test
    and the repo's own typecheck step commonly rides the build axis."""
    leerie_dir = repo_root / ".leerie"
    leerie_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    if test_cmd is not None:
        lines.append(f'test = "{test_cmd}"')
    if typecheck_cmd is not None:
        lines.append(f'build = "{typecheck_cmd}"')
    (leerie_dir / "config.toml").write_text("\n".join(lines) + "\n")


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_WORKER_MEMORY_MAX", raising=False)
    return tmp_path


# ---- _declared_node_heap_bytes ---------------------------------------------

def test_declared_node_heap_bytes_extracts_max_across_axes(leerie, repo_root):
    """funeralworks' own measured shape: NODE_OPTIONS=--max-old-space-size=8192
    prefixed onto the test command."""
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=8192 pnpm test",
    )
    assert leerie._declared_node_heap_bytes(repo_root) == 8192 * 1024 * 1024


def test_declared_node_heap_bytes_takes_max_of_multiple_axes(leerie, repo_root):
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=4096 pnpm test",
        typecheck_cmd="NODE_OPTIONS=--max-old-space-size=8192 pnpm tsc",
    )
    assert leerie._declared_node_heap_bytes(repo_root) == 8192 * 1024 * 1024


def test_declared_node_heap_bytes_none_when_undeclared(leerie, repo_root):
    _write_config(repo_root, test_cmd="pnpm test")
    assert leerie._declared_node_heap_bytes(repo_root) is None


def test_declared_node_heap_bytes_none_with_no_config(leerie, repo_root):
    assert leerie._declared_node_heap_bytes(repo_root) is None


# ---- resolve_worker_memory_max: auto-derived ceiling gets raised ----------

def test_auto_derived_ceiling_raised_above_declared_heap(leerie, repo_root, monkeypatch):
    """A repo declaring an 8192 MiB heap must resolve to a ceiling of at
    least 8192 MiB + headroom, even though nothing (CLI/env/file) pins the
    ceiling explicitly. Auto-derivation itself is pinned to a known-low
    6 GiB value (below the 8192 MiB + `_NODE_HEAP_HEADROOM_BYTES` this repo
    needs)
    so the assertion is independent of the test host's real memory —
    only the reconciliation logic under test is exercised."""
    monkeypatch.setattr(leerie, "_auto_worker_memory_max", lambda max_parallel: 6 * 1024**3)
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=8192 pnpm test",
    )
    result = leerie.resolve_worker_memory_max(repo_root, max_parallel=4)
    needed = 8192 * 1024 * 1024 + leerie._NODE_HEAP_HEADROOM_BYTES
    assert result >= needed, (
        f"resolved ceiling {result} did not reconcile with the declared "
        f"8192 MiB heap (needed >= {needed})"
    )
    assert result == needed


def test_auto_derived_ceiling_unaffected_without_declared_heap(leerie, repo_root, monkeypatch):
    """Control: no declared heap -> auto-derivation is untouched by the
    reconciliation logic."""
    monkeypatch.setattr(leerie, "_auto_worker_memory_max", lambda max_parallel: 6 * 1024**3)
    baseline = leerie.resolve_worker_memory_max(repo_root, max_parallel=4)

    _write_config(repo_root, test_cmd="pnpm test")
    result = leerie.resolve_worker_memory_max(repo_root, max_parallel=4)
    assert result == baseline


def test_auto_derived_ceiling_unaffected_by_small_declared_heap(leerie, repo_root, monkeypatch):
    """Control: a repo whose command declares a heap comfortably under the
    existing auto-derived ceiling is unaffected — reconciliation only ever
    raises, never lowers, and only raises when undershooting."""
    monkeypatch.setattr(leerie, "_auto_worker_memory_max", lambda max_parallel: 6 * 1024**3)
    baseline = leerie.resolve_worker_memory_max(repo_root, max_parallel=4)

    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=1024 pnpm test",
    )
    result = leerie.resolve_worker_memory_max(repo_root, max_parallel=4)
    assert result == baseline


# ---- resolve_worker_memory_max: explicit ceilings die() instead ----------

def test_explicit_cli_value_dies_when_undershooting_declared_heap(leerie, repo_root):
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=8192 pnpm test",
    )
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(repo_root, max_parallel=4, cli_value="4G")


def test_die_message_names_worker_memory_max_flag(leerie, repo_root, capsys):
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=8192 pnpm test",
    )
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(repo_root, max_parallel=4, cli_value="4G")
    captured = capsys.readouterr()
    assert "--worker-memory-max" in (captured.out + captured.err)


def test_explicit_env_value_dies_when_undershooting_declared_heap(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_MEMORY_MAX", "4G")
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=8192 pnpm test",
    )
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(repo_root, max_parallel=4)


def test_explicit_file_value_dies_when_undershooting_declared_heap(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("worker_memory_max = 4G\n")
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=8192 pnpm test",
    )
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(repo_root, max_parallel=4)


def test_explicit_cli_value_unaffected_without_declared_heap(leerie, repo_root):
    """Control: an explicit value with no declared heap resolves exactly
    as before — no reconciliation logic engaged at all."""
    assert leerie.resolve_worker_memory_max(
        repo_root, max_parallel=4, cli_value="4G") == 4 * 1024**3


def test_explicit_cli_value_passes_when_it_covers_declared_heap(leerie, repo_root):
    """Control: an explicit ceiling that already comfortably covers the
    declared heap + headroom is left exactly as given."""
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=1024 pnpm test",
    )
    assert leerie.resolve_worker_memory_max(
        repo_root, max_parallel=4, cli_value="4G") == 4 * 1024**3


# ---- the package-manager indirection (the case that made this inert) -------
#
# Every test above declares the heap in `.leerie/config.toml`. No real repo
# does that: measured across the five repos leerie manages, 2 of 5 declare a
# heap in `package.json` and 0 of 5 declare one in `.leerie/config.toml`. So
# the whole reconciliation fired on NONE of them — including funeralworks,
# whose OOMs motivated N14-16 — while this file reported full coverage. The
# fixtures below are the shape that actually ships.

def _write_node_repo(repo_root, scripts, *, lockfile="pnpm-lock.yaml"):
    """A Node repo as `_infer_build_lint_test` sees one: a package.json with
    scripts, and a lockfile picking the package manager. Deliberately NO
    `.leerie/config.toml`, so `resolve_blt` falls through to inference and
    yields `<pm> run <script>` — the indirection under test."""
    import json as _json
    (repo_root / "package.json").write_text(
        _json.dumps({"name": "fixture", "scripts": scripts}))
    (repo_root / lockfile).write_text("")


def test_heap_declared_in_package_json_is_found(leerie, repo_root):
    """The funeralworks shape verbatim. `resolve_blt` returns `pnpm run
    test`; the heap is in the script body it indirects to. Scanning only the
    resolved command returns None and the reconciliation no-ops."""
    _write_node_repo(repo_root, {
        "build": "next build",
        "test": "NODE_ENV=test NODE_OPTIONS=--max-old-space-size=8192 vitest run",
    })
    assert leerie._declared_node_heap_bytes(repo_root) == 8192 * 1024 * 1024


def test_package_json_heap_raises_the_ceiling(leerie, repo_root, monkeypatch):
    """End to end: the declared heap must actually move the resolved cap."""
    _write_node_repo(repo_root, {
        "test": "NODE_OPTIONS=--max-old-space-size=8192 vitest run",
    })
    monkeypatch.setattr(leerie, "_auto_worker_memory_max",
                        lambda *_a, **_k: 6 * 1024**3)
    monkeypatch.setattr(leerie, "_cgroup_slice_info", lambda: None)
    needed = 8192 * 1024 * 1024 + leerie._NODE_HEAP_HEADROOM_BYTES
    assert leerie.resolve_worker_memory_max(repo_root, max_parallel=4) == needed


def test_package_json_max_across_scripts(leerie, repo_root):
    """Max across every script the BLT axes reach, not the first hit."""
    _write_node_repo(repo_root, {
        "build": "NODE_OPTIONS=--max-old-space-size=4096 next build",
        "test": "NODE_OPTIONS=--max-old-space-size=8192 vitest run",
    })
    assert leerie._declared_node_heap_bytes(repo_root) == 8192 * 1024 * 1024


def test_package_json_without_heap_stays_none(leerie, repo_root):
    """Control — a Node repo declaring no heap must not acquire one, or the
    positive cases above would prove nothing."""
    _write_node_repo(repo_root, {"build": "next build", "test": "vitest run"})
    assert leerie._declared_node_heap_bytes(repo_root) is None


def test_only_scripts_the_blt_axes_reach_are_scanned(leerie, repo_root):
    """A heap on an unrelated script (`start`) is not leerie's problem: the
    conformer never runs it, so it must not inflate the ceiling."""
    _write_node_repo(repo_root, {
        "build": "next build",
        "test": "vitest run",
        "start": "NODE_OPTIONS=--max-old-space-size=16384 next start",
    })
    assert leerie._declared_node_heap_bytes(repo_root) is None


@pytest.mark.parametrize("flag", [
    "--max-old-space-size=8192",
    "--max-old-space-size 8192",
    "--max_old_space_size=8192",
    "--max_old_space_size 8192",
])
def test_all_four_flag_spellings_are_matched(leerie, repo_root, flag):
    """V8 normalises `-`/`_` and accepts `=` or whitespace. The original
    pattern matched only the `=`-with-dashes form; the other three were
    silently invisible."""
    _write_node_repo(repo_root, {"test": f"node {flag} ./run.js"})
    assert leerie._declared_node_heap_bytes(repo_root) == 8192 * 1024 * 1024


def test_one_level_of_script_chaining_is_followed(leerie, repo_root):
    """`test` delegating to another script is common (`pretest`, `test:ci`)."""
    _write_node_repo(repo_root, {
        "test": "pnpm run test:ci",
        "test:ci": "NODE_OPTIONS=--max-old-space-size=8192 vitest run",
    })
    assert leerie._declared_node_heap_bytes(repo_root) == 8192 * 1024 * 1024


def test_cyclic_scripts_terminate(leerie, repo_root):
    """A cyclic pair must not hang or recurse forever."""
    _write_node_repo(repo_root, {
        "test": "pnpm run other",
        "other": "pnpm run test",
    })
    assert leerie._declared_node_heap_bytes(repo_root) is None


def test_malformed_package_json_degrades_to_none(leerie, repo_root):
    """Unparseable package.json must not crash the run at startup."""
    (repo_root / "package.json").write_text("{ not json")
    (repo_root / "pnpm-lock.yaml").write_text("")
    assert leerie._declared_node_heap_bytes(repo_root) is None


def test_config_toml_still_wins_when_it_declares_blt(leerie, repo_root):
    """Regression control for the additive change: an explicit config.toml
    BLT command is still scanned directly, and a package.json script the
    config does not invoke is not consulted."""
    _write_node_repo(repo_root, {
        "test": "NODE_OPTIONS=--max-old-space-size=16384 vitest run",
    })
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=2048 vitest run",
    )
    assert leerie._declared_node_heap_bytes(repo_root) == 2048 * 1024 * 1024


# ---- package-manager shorthands (B7) ---------------------------------------
#
# `_infer_build_lint_test` emits `<pm> run <script>`, but a repo's own
# `.leerie/config.toml` may declare any idiomatic shorthand. Matching only
# the literal `run` silently disabled the whole reconciliation for those —
# and since the heap lives in the script body, a missed indirection means
# no heap found at all.

@pytest.mark.parametrize("cmd", [
    "yarn test",                     # yarn's `run` is optional
    "npm test",                      # npm lifecycle shorthand
    "npm run-script test",           # documented npm alias
    "pnpm run test",                 # the inferred form (control)
])
def test_pm_shorthands_reach_the_script(leerie, repo_root, cmd):
    _write_node_repo(repo_root, {
        "test": "NODE_OPTIONS=--max-old-space-size=8192 vitest run"})
    _write_config(repo_root, test_cmd=cmd)
    assert leerie._declared_node_heap_bytes(repo_root) == 8192 * 1024 * 1024


@pytest.mark.parametrize("cmd", [
    "pnpm -r run build",
    "pnpm --filter web run build",
    "yarn --cwd app build",
])
def test_workspace_flag_forms_reach_the_script(leerie, repo_root, cmd):
    """A flag's VALUE may survive as a spurious candidate (`web`, `app`) —
    harmless, because a candidate that is not a script name is only a
    `scripts.get()` miss. What matters is that `build` is still reached."""
    _write_node_repo(repo_root, {
        "build": "NODE_OPTIONS=--max-old-space-size=8192 next build"})
    _write_config(repo_root, typecheck_cmd=cmd)   # typecheck_cmd writes `build`
    assert leerie._declared_node_heap_bytes(repo_root) == 8192 * 1024 * 1024


@pytest.mark.parametrize("cmd", [
    "pnpm run build&&node x.js start",     # no spaces around &&
    "pnpm run build;node x.js start",      # no spaces around ;
    "pnpm run build|tee log start",        # no spaces around |
    "pnpm run build && node x.js start",   # spaced (already worked)
])
def test_separator_without_spaces_still_finds_the_real_script(
        leerie, repo_root, cmd):
    """The script the command actually runs must be found whichever way the
    chain is punctuated — and the *other* command's argument must not be.

    This is one control for two failures, because the pre-fix code produced
    both at once. `_SHELL_SEP` was tested per whitespace-split token, so a
    separator written without surrounding spaces was invisible:
    `"build&&node".split()` is a single token that equals no separator. On
    `pnpm run build&&node x.js start` the shipped matcher therefore returned
    `['x.js', 'start']` — it LOST `build`, the script being run, and offered
    `start`, an argument to a different command.

    The miss is the dangerous half. A resolved heap raises the worker's
    memory cage, so losing `build`'s declared 8192 under-sizes the cage and
    the worker OOMs — the exact failure this reconciliation exists to
    prevent. Asserting 8192 (not 16384, not None) pins both halves: `build`
    was found, and `start` was not.
    """
    _write_node_repo(repo_root, {
        "build": "NODE_OPTIONS=--max-old-space-size=8192 next build",
        "start": "NODE_OPTIONS=--max-old-space-size=16384 next start",
    })
    _write_config(repo_root, test_cmd=cmd)
    assert leerie._declared_node_heap_bytes(repo_root) == 8192 * 1024**2


@pytest.mark.parametrize("cmd,expected", [
    # A PM subcommand that is not a script resolves to nothing...
    ("pnpm install --frozen-lockfile", ["install"]),
    # ...but trailing tokens are still offered, and that is deliberate:
    # `pnpm exec turbo run build` finds `build` ONLY because of it.
    ("pnpm exec turbo run build", ["exec", "turbo", "build"]),
    ("pnpm --filter web run build", ["web", "build"]),
    ("yarn --cwd app build", ["app", "build"]),
    ("npm run-script test", ["test"]),
    ("yarn test", ["test"]),
    # A newline is a separator too. Covered here rather than end-to-end
    # because it cannot survive the trip: `_write_config` interpolates into
    # a TOML basic string, and a literal newline makes that invalid TOML, so
    # the whole config is dropped and the assertion would be answered by the
    # inference fallback instead. That is how the newline case in the
    # end-to-end parametrization above came to pass pre-fix while proving
    # nothing — it never tested a newline at all.
    ("pnpm run build\nnode x.js start", ["build"]),
])
def test_candidate_extraction_stays_over_inclusive(leerie, cmd, expected):
    """Asserted at the unit, because the end-to-end form cannot fail.

    Its predecessor (`test_pm_subcommands_are_not_treated_as_scripts`) wrote
    a fixture with no `--max-old-space-size` anywhere and asserted `None` —
    which every possible implementation returns, including one that scans
    every script in the file. It could not distinguish correct behaviour
    from any other.

    Over-inclusion is the intended design, not a tolerated flaw: narrowing
    to `exec`/`dlx`-abandon or stop-at-`--` was prototyped and rejected,
    because `pnpm exec turbo run build` and `pnpm exec turbo -- run build`
    both become misses under those rules.
    """
    assert leerie._pm_script_candidates(cmd) == expected


# ---- the fleet-fit forecast (B4/B5) ----------------------------------------

def test_fleet_fit_forecast_uses_the_same_signal_as_the_degrade(
        leerie, repo_root, monkeypatch, capsys):
    """The forecast predicts `_degrade_max_parallel_for_wave`, so it must
    read the same signal — `slice_max MINUS unreclaimable`, never the raw
    budget.

    Dividing the raw budget over-reports how many workers will actually be
    admitted, in a message whose only purpose is to tell the operator what
    the degrade is about to do. Numbers chosen so the two disagree: a
    40 GiB slice with 20 GiB unreclaimable fits 3 workers raw and 1 after
    the subtraction."""
    _write_node_repo(repo_root, {
        "test": "NODE_OPTIONS=--max-old-space-size=8192 vitest run"})
    monkeypatch.setattr(leerie, "_auto_worker_memory_max",
                        lambda *_a, **_k: 6 * 1024**3)
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                        lambda: (40 * 1024**3, 0, 20 * 1024**3))

    leerie.resolve_worker_memory_max(repo_root, max_parallel=5)
    out = capsys.readouterr()
    msg = out.out + out.err

    assert "fits ~1 concurrent workers" in msg, (
        f"forecast must divide headroom, not the raw slice budget: {msg}")
    assert "fits ~3" not in msg


def test_degrade_log_names_the_demand_estimate_not_the_build_peak(
        leerie, monkeypatch, capsys):
    """`build_peak_bytes` is injectable now, so labelling it the
    "build-peak floor" is wrong precisely on the runs N14-16 targets — it
    prints a repo's declared-heap demand under the build-peak name."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                        lambda: (40 * 1024**3, 0, 30 * 1024**3))
    leerie._degrade_max_parallel_for_wave(5, 8 * 1024**3)
    msg = "".join(capsys.readouterr())
    assert "per-worker demand estimate" in msg
    assert "build-peak floor" not in msg
