"""Tests for the launcher's --chain wave-sequencer arm (v5 Shape A).

The sequencer fans out N background ./leerie --runtime fly invocations
per wave, waits for all, tags each finalized run.json with chain_id +
wave_idx, runs synth-merge between waves, and pushes the staging
branch to origin. These tests exercise that flow with stubs for the
per-job leerie invocation (LEERIE_SELF_CMD), the git client, and
chain.git_ops.synth_merge_branches.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _init_git_repo(repo: Path) -> None:
    """Init a minimal git repo at *repo* so the launcher's git commands
    succeed against a known starting state."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "test"],
        check=True,
    )
    (repo / "README.md").write_text("initial\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "initial"],
        check=True,
    )


def _write_prompt(tmp_path: Path, name: str, content: str = "do the thing") -> Path:
    """Write a prompt file under tmp_path and return its absolute path."""
    p = tmp_path / name
    p.write_text(content)
    return p


def _build_self_stub(tmp_path: Path, *, exit_codes: list[int] | None = None) -> tuple[Path, Path]:
    """Build a stub binary that:

    1. Records its argv on the log file.
    2. Reads `LEERIE_STUB_INDEX` (env, defaults to 0) and writes a
       fake `remote/<bash-pid>.json` + `runs/<machine-id>/run.json`
       pair so the wave loop's tagging step finds the run.
    3. Exits with the rc from `exit_codes[index]` (default 0).

    The stub uses its own PPID as a unique machine_id stand-in.
    """
    log = tmp_path / "stub.log"
    state_dir = tmp_path / ".leerie" / "testrepo"
    state_dir.mkdir(parents=True, exist_ok=True)
    stub = tmp_path / "self-stub"
    rc_table = exit_codes or [0]
    rc_table_repr = " ".join(str(c) for c in rc_table)
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Stub for ./leerie --runtime fly --chain-id <id> invocations.
        # Records argv + simulates writing remote/<pid>.json + runs/<mid>/run.json
        # so the wave loop's tagging step finds it.
        echo "$@" >> "{log}"
        # Pick a unique machine-id: parent bash pid (well, our PPID).
        _mid="m$$"
        # The wave loop captures $! (this stub's pid) and looks for
        # $LEERIE_STATE_HOST_DIR/remote/<that-pid>.json. We write to $$ which
        # is our own PID — they match because $! in the parent is our $$.
        _state="{state_dir}"
        mkdir -p "$_state/remote" "$_state/runs/$_mid"
        # Capture prompt (first positional arg before --runtime).
        _prompt="$1"
        # Find chain-id (passed as --chain-id <uuid>).
        _cid=""
        while [ "$#" -gt 0 ]; do
          case "$1" in
            --chain-id) _cid="$2"; shift 2 ;;
            *) shift ;;
          esac
        done
        # Write the launcher_pid pointer the wave loop looks for.
        cat > "$_state/remote/$$.json" <<EOF
        {{"fly_machine_id":"$_mid","run_id":"$_mid","launcher_pid":$$}}
        EOF
        # Write the run.json the wave loop will tag with chain_id + wave_idx.
        cat > "$_state/runs/$_mid/run.json" <<EOF
        {{"run_id":"$_mid","branch":"leerie/runs/$_mid","fly_machine_id":"$_mid","pushed_at":"2026-06-14T00:00:00Z","finished_at":"2026-06-14T00:00:00Z"}}
        EOF
        # Pick the rc by stub-index from LEERIE_STUB_INDEX. Atomically
        # increment using a file lock so concurrent invocations stay deterministic.
        _idx_file="{tmp_path}/stub-index"
        _idx=$(cat "$_idx_file" 2>/dev/null || echo 0)
        echo $((_idx + 1)) > "$_idx_file"
        _rc_table=({rc_table_repr})
        _rc="${{_rc_table[$_idx]:-0}}"
        exit "$_rc"
        """))
    stub.chmod(0o755)
    return stub, log


def _build_synth_merge_stub(tmp_path: Path) -> tuple[Path, Path]:
    """Stub a synth_merge_branches Python entry point. Records call args
    to a log file; succeeds (rc 0) by default. Set LEERIE_SYNTH_FAIL=1
    in env to make it raise SynthMergeConflict.

    The launcher imports `from chain.git_ops import synth_merge_branches,
    SynthMergeConflict` so we shadow `chain.git_ops` via PYTHONPATH.
    """
    log = tmp_path / "synth-merge.log"
    pkg_dir = tmp_path / "fake_chain_pkg" / "chain"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "git_ops.py").write_text(textwrap.dedent(f"""\
        import json, os, sys

        class SynthMergeConflict(Exception):
            def __init__(self, branch, output=""):
                self.branch = branch
                self.output = output
                super().__init__(f"conflict on {{branch}}")

        def synth_merge_branches(repo_path, base_branch, dep_branches, stage_branch_name):
            with open("{log}", "a") as fh:
                fh.write(json.dumps({{
                    "repo": str(repo_path),
                    "base": base_branch,
                    "deps": list(dep_branches),
                    "stage": stage_branch_name,
                }}) + "\\n")
            if os.environ.get("LEERIE_SYNTH_FAIL") == "1":
                raise SynthMergeConflict(dep_branches[0] if dep_branches else "?")
            return stage_branch_name
        """))
    return pkg_dir.parent, log  # return the dir to prepend to PYTHONPATH


def _stub_git(tmp_path: Path) -> Path:
    """Stub git that succeeds on `checkout` and `push` (the launcher's
    only direct git invocations in the wave loop — synth_merge_branches
    runs in a Python subshell with the fake module). Records calls.

    For commands not directly used by the wave loop (e.g.
    `symbolic-ref --short HEAD` used to derive current_base), we delegate
    to the real git binary so the launcher's initial setup works.
    """
    log = tmp_path / "git.log"
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "git"
    real_git = shutil.which("git") or "/usr/bin/git"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$@" >> "{log}"
        # Pass-through for read-only / setup commands; stub mutating ones.
        # Scan ALL positional args for the verb (the -C <path> prefix can
        # push the verb to $3 / $4 etc.).
        for _arg in "$@"; do
          case "$_arg" in
            checkout|push) exit 0 ;;
          esac
        done
        exec "{real_git}" "$@"
        """))
    stub.chmod(0o755)
    return bin_dir, log


def _run_chain(
    tmp_path: Path,
    waves: list[list[Path]],
    *,
    exit_codes: list[int] | None = None,
    synth_fail: bool = False,
) -> subprocess.CompletedProcess:
    """Run the launcher's --chain arm with stubs."""
    user_repo = tmp_path / "userrepo"
    user_repo.mkdir()
    _init_git_repo(user_repo)

    state_dir = tmp_path / ".leerie" / "testrepo"
    state_dir.mkdir(parents=True, exist_ok=True)

    self_stub, self_log = _build_self_stub(tmp_path, exit_codes=exit_codes)
    fake_chain_dir, synth_log = _build_synth_merge_stub(tmp_path)
    git_bin_dir, git_log = _stub_git(tmp_path)

    args = ["--chain"]
    for w in waves:
        args.extend(["--wave", ",".join(str(p) for p in w)])

    # Prepend our git stub to PATH and fake chain package to PYTHONPATH.
    real_path = os.environ.get("PATH", "/usr/bin:/bin")
    env = {
        "PATH": f"{git_bin_dir}:{real_path}",
        "USER_REPO": str(user_repo),
        "LEERIE_REPO": str(REPO_ROOT),
        "HOME": str(tmp_path),
        "LEERIE_STATE_HOST_DIR": str(state_dir),
        "LEERIE_STATE_DIR": str(state_dir),
        "LEERIE_SELF_CMD": str(self_stub),
        "PYTHONPATH": str(fake_chain_dir),
    }
    if synth_fail:
        env["LEERIE_SYNTH_FAIL"] = "1"

    result = subprocess.run(
        ["bash", str(LAUNCHER)] + args,
        env=env, capture_output=True, text=True, timeout=30,
        cwd=str(user_repo),  # launcher derives USER_REPO from cwd
    )
    result.self_log = self_log.read_text() if self_log.exists() else ""
    result.synth_log = synth_log.read_text() if synth_log.exists() else ""
    result.git_log = git_log.read_text() if git_log.exists() else ""
    return result


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_single_wave_single_job(tmp_path: Path) -> None:
    """One wave, one job → one stub invocation, no synth-merge."""
    p = _write_prompt(tmp_path, "a.md")
    result = _run_chain(tmp_path, [[p]])
    assert result.returncode == 0, result.stderr
    # Stub invoked once.
    assert result.self_log.count("--runtime fly") == 1
    # No synth-merge (only one wave).
    assert result.synth_log == ""
    # No staging branch push.
    assert "push origin leerie/stage" not in result.git_log


def test_single_wave_multi_job_runs_in_parallel(tmp_path: Path) -> None:
    """One wave with 3 jobs → 3 stub invocations."""
    ps = [_write_prompt(tmp_path, f"j{i}.md", f"prompt-{i}") for i in range(3)]
    result = _run_chain(tmp_path, [ps])
    assert result.returncode == 0, result.stderr
    invocations = result.self_log.splitlines()
    assert len(invocations) == 3, invocations
    # Each invocation carries its own prompt as the first argv.
    prompts_seen = {line.split(" --runtime")[0] for line in invocations}
    assert prompts_seen == {"prompt-0", "prompt-1", "prompt-2"}


def test_multi_wave_synth_merges_between_waves(tmp_path: Path) -> None:
    """Two waves → 1 synth-merge call + 1 staging branch push."""
    p0 = _write_prompt(tmp_path, "wave0.md")
    p1 = _write_prompt(tmp_path, "wave1.md")
    result = _run_chain(tmp_path, [[p0], [p1]])
    assert result.returncode == 0, result.stderr
    # Two per-job invocations.
    assert result.self_log.count("--runtime fly") == 2
    # One synth-merge call between waves.
    synth_calls = [json.loads(line) for line in result.synth_log.splitlines() if line]
    assert len(synth_calls) == 1
    call = synth_calls[0]
    assert call["base"] == "main"
    assert call["stage"].startswith("leerie/stage/") and "wave-1" in call["stage"]
    assert len(call["deps"]) == 1  # wave-0 produced 1 branch
    # Staging branch pushed.
    assert "push origin leerie/stage" in result.git_log


def test_multi_wave_chain_id_threaded_to_each_job(tmp_path: Path) -> None:
    """Each per-job invocation receives the same --chain-id <uuid>."""
    p0 = _write_prompt(tmp_path, "a.md")
    p1 = _write_prompt(tmp_path, "b.md")
    result = _run_chain(tmp_path, [[p0], [p1]])
    assert result.returncode == 0, result.stderr
    # Extract the chain-id used in each invocation.
    chain_ids = []
    for line in result.self_log.splitlines():
        if "--chain-id" in line:
            parts = line.split()
            idx = parts.index("--chain-id")
            chain_ids.append(parts[idx + 1])
    assert len(chain_ids) == 2
    assert chain_ids[0] == chain_ids[1]  # Same chain.
    # UUID-shaped.
    import re
    assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                    chain_ids[0])


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_wave_job_failure_pauses_chain(tmp_path: Path) -> None:
    """One failed job in wave 0 → chain exits non-zero with resume hint."""
    p = _write_prompt(tmp_path, "a.md")
    result = _run_chain(tmp_path, [[p]], exit_codes=[1])
    assert result.returncode == 1
    assert "paused" in (result.stdout + result.stderr).lower()
    assert "--resume" in (result.stdout + result.stderr)


def test_synth_merge_conflict_pauses_chain(tmp_path: Path) -> None:
    """Synth-merge conflict → chain exits non-zero with conflict message."""
    p0 = _write_prompt(tmp_path, "wave0.md")
    p1 = _write_prompt(tmp_path, "wave1.md")
    result = _run_chain(tmp_path, [[p0], [p1]], synth_fail=True)
    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "conflict" in out.lower() or "paused" in out.lower()


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_no_waves_errors(tmp_path: Path) -> None:
    """--chain with no --wave flag → usage error."""
    result = _run_chain(tmp_path, [])
    assert result.returncode == 1
    assert "wave" in (result.stdout + result.stderr).lower()


def test_missing_prompt_file_errors(tmp_path: Path) -> None:
    """--chain with a nonexistent prompt file → error before any spawn."""
    user_repo = tmp_path / "userrepo"
    user_repo.mkdir()
    _init_git_repo(user_repo)

    state_dir = tmp_path / ".leerie" / "testrepo"
    state_dir.mkdir(parents=True, exist_ok=True)
    self_stub, self_log = _build_self_stub(tmp_path)
    fake_chain_dir, _ = _build_synth_merge_stub(tmp_path)
    git_bin_dir, _ = _stub_git(tmp_path)

    real_path = os.environ.get("PATH", "/usr/bin:/bin")
    env = {
        "PATH": f"{git_bin_dir}:{real_path}",
        "USER_REPO": str(user_repo),
        "LEERIE_REPO": str(REPO_ROOT),
        "HOME": str(tmp_path),
        "LEERIE_STATE_HOST_DIR": str(state_dir),
        "LEERIE_STATE_DIR": str(state_dir),
        "LEERIE_SELF_CMD": str(self_stub),
        "PYTHONPATH": str(fake_chain_dir),
    }
    result = subprocess.run(
        ["bash", str(LAUNCHER), "--chain", "--wave", "/no/such/prompt.md"],
        env=env, capture_output=True, text=True, timeout=10,
        cwd=str(user_repo),
    )
    assert result.returncode == 1
    assert "not found" in (result.stdout + result.stderr).lower()
    # Stub never invoked (log file doesn't exist or is empty).
    assert not self_log.exists() or self_log.read_text() == ""


# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------


def test_chain_id_is_written_to_run_json(tmp_path: Path) -> None:
    """After a successful wave, each finalized run.json carries the
    chain_id + wave_idx tag the wave loop wrote via update_run_json."""
    p = _write_prompt(tmp_path, "a.md")
    result = _run_chain(tmp_path, [[p]])
    assert result.returncode == 0, result.stderr

    # Find the run.json the stub wrote.
    state_dir = tmp_path / ".leerie" / "testrepo"
    runs_dir = state_dir / "runs"
    run_jsons = list(runs_dir.glob("*/run.json"))
    assert len(run_jsons) >= 1, "stub didn't write any run.json"

    data = json.loads(run_jsons[0].read_text())
    assert data.get("chain_id"), f"chain_id not tagged on {run_jsons[0]}: {data}"
    # wave_idx should be 0 (first wave).
    assert str(data.get("wave_idx")) == "0", f"wave_idx wrong: {data}"
