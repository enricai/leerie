"""Tests for scripts/remote/fetch-branch.sh.

fetch-branch.sh is sourced by the leerie launcher after remote orchestration
exits 0.  These tests exercise the script's bash logic in isolation via
subprocess, with flyctl and git stubbed out so no real Fly.io calls or
network traffic occur.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FETCH_SH = REPO_ROOT / "scripts" / "remote" / "fetch-branch.sh"

# Python snippet that the real fetch-branch.sh sends to the machine to
# discover the completed run-id.  We replicate its logic in the stub.
# Keep this in lockstep with scripts/remote/fetch-branch.sh's discover
# script (Step 1) — the no_push fourth-line output lets Step 2 skip the
# bundle fetch for cleared-but-empty terminal-state runs (DESIGN §8).
_DISCOVER_SNIPPET = """\
import os, json, sys
runs_dir = RUNS_DIR_PLACEHOLDER
if not os.path.isdir(runs_dir):
    sys.exit(1)
best = None
best_mtime = 0
for name in os.listdir(runs_dir):
    rj = os.path.join(runs_dir, name, "run.json")
    if not os.path.isfile(rj):
        continue
    try:
        d = json.load(open(rj))
    except Exception:
        continue
    if not d.get("finished_at"):
        continue
    if d.get("pushed_at"):
        continue
    mtime = os.stat(rj).st_mtime
    if mtime > best_mtime:
        best_mtime = mtime
        best = (name, d.get("branch", ""), d.get("working_branch", ""),
                "true" if d.get("no_push") else "false")
if best is None:
    print("ERROR: no completed unpushed run found on machine")
    sys.exit(1)
print(best[0])
print(best[1])
print(best[2])
print(best[3])
"""


def _run_bash(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items()}
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        capture_output=True,
        text=True,
    )


def _make_fake_flyctl(
    tmp_path: Path,
    machine_runs_dir: Path,
    git_repo: Path,
    machine_leerie_dir: Path | None = None,
) -> Path:
    """Write a stub flyctl that routes machine exec calls locally.

    The stub handles the flyctl machine exec invocations that fetch_branch
    makes:
      1. python3 -c '...'  — run the discovery snippet, rewriting the
         hardcoded /work/.leerie/runs path to machine_runs_dir.
      2. git -C /work bundle create - <branch>  — run against git_repo.
      3. tar -cC /work/.leerie/runs <run-id>  — run against machine_runs_dir.
      4. test -f /work/.leerie/<file>  — probe against machine_leerie_dir.
      5. cat /work/.leerie/<file>  — stream from machine_leerie_dir.

    machine_leerie_dir is the local directory standing in for /work/.leerie on
    the Fly machine.  When None, Step 4/5 calls silently fail (file absent).
    All other invocations succeed silently.
    """
    # Write the discovery helper script as a separate file to avoid quoting
    # nightmares when embedding multi-line Python in a bash heredoc.
    discover_py = tmp_path / "_discover_helper.py"
    snippet = _DISCOVER_SNIPPET.replace(
        "RUNS_DIR_PLACEHOLDER", repr(str(machine_runs_dir))
    )
    discover_py.write_text(snippet)

    # Machine-side .leerie/ directory (stands in for /work/.leerie/).
    mleerie = str(machine_leerie_dir) if machine_leerie_dir else ""

    stub = tmp_path / "flyctl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        # New flyctl call shape (post-`--stdin` removal): the launcher
        # now uses `flyctl ssh console --app <app> --machine <id>
        # --pty=false -C "<cmd-string>"`. The command is a single
        # shell-quoted string; we eval it after rewriting the in-machine
        # paths to point at the test fixtures.
        f'REPO={git_repo}\n'
        f'MRUNS={machine_runs_dir}\n'
        f'MLEERIE={mleerie}\n'
        # Parse out the -C argument.
        'CMD=""\n'
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    -C) CMD="$2"; shift 2 ;;\n'
        '    auth) shift; case "$1" in status) exit 0 ;; esac ;;\n'
        '    *) shift ;;\n'
        '  esac\n'
        'done\n'
        '[ -z "$CMD" ] && exit 0\n'
        # Rewrite in-machine paths to the local fixture paths.
        '# discover_run_for_fetch python3 -c \'<script>\' — match on python3 -c\n'
        'case "$CMD" in\n'
        '  python3*-c*)\n'
        f'    exec python3 {discover_py}\n'
        '    ;;\n'
        '  git*rev-parse*--verify*)\n'
        # rewrite "git -C /work rev-parse --verify refs/heads/<branch>"
        # → "git -C $REPO rev-parse --verify refs/heads/<branch>" so the
        # probe consults the test fixture's local refs.
        '    NEWCMD="${CMD//\\/work/$REPO}"\n'
        '    eval "$NEWCMD"\n'
        '    exit $?\n'
        '    ;;\n'
        '  git*bundle*create*)\n'
        # rewrite "git -C /work bundle create - <branch>" → "git -C $REPO bundle create - <branch>"
        '    NEWCMD="${CMD//\\/work/$REPO}"\n'
        '    eval "$NEWCMD"\n'
        '    exit $?\n'
        '    ;;\n'
        '  tar*-cC*/work/.leerie/runs*)\n'
        # rewrite "tar -cC /work/.leerie/runs <run-id>" → "tar -cC $MRUNS <run-id>"
        '    NEWCMD="${CMD//\\/work\\/.leerie\\/runs/$MRUNS}"\n'
        '    eval "$NEWCMD"\n'
        '    exit $?\n'
        '    ;;\n'
        # Step 4 probe: "test -f /work/.leerie/<file>"
        '  *test*-f*/work/.leerie/*)\n'
        '    [ -z "$MLEERIE" ] && exit 1\n'
        '    NEWCMD="${CMD//\\/work\\/.leerie/$MLEERIE}"\n'
        '    eval "$NEWCMD"\n'
        '    exit $?\n'
        '    ;;\n'
        # Step 4 stream: "cat /work/.leerie/<file>"
        '  *cat*/work/.leerie/*)\n'
        '    [ -z "$MLEERIE" ] && exit 1\n'
        '    NEWCMD="${CMD//\\/work\\/.leerie/$MLEERIE}"\n'
        '    eval "$NEWCMD"\n'
        '    exit $?\n'
        '    ;;\n'
        '  *) exit 0 ;;\n'
        'esac\n'
    )
    stub.chmod(0o755)
    return stub


def _make_git_repo(tmp_path: Path, subdir: str = "myrepo") -> Path:
    """Create a minimal git repo with one commit and return its path."""
    repo = tmp_path / subdir
    repo.mkdir()
    for cmd in [
        ["git", "-C", str(repo), "init"],
        ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
        ["git", "-C", str(repo), "config", "user.name", "T"],
    ]:
        subprocess.run(cmd, check=True, capture_output=True)
    (repo / "README.md").write_text("hello")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    return repo


def test_fetch_branch_sh_exists():
    assert FETCH_SH.exists(), "scripts/remote/fetch-branch.sh is missing"


def test_fetch_branch_sh_is_executable():
    assert os.access(FETCH_SH, os.X_OK), (
        "scripts/remote/fetch-branch.sh is not executable"
    )


def test_fetch_branch_fails_without_machine_id():
    """fetch_branch returns 1 when LEERIE_MACHINE_ID is unset."""
    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={"LEERIE_MACHINE_ID": "", "USER_REPO": "/tmp"},
    )
    assert result.returncode != 0
    assert "LEERIE_MACHINE_ID" in result.stderr


def test_fetch_branch_fails_without_user_repo():
    """fetch_branch returns 1 when USER_REPO is unset."""
    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={"LEERIE_MACHINE_ID": "test-machine-001", "USER_REPO": ""},
    )
    assert result.returncode != 0
    assert "USER_REPO" in result.stderr


def test_fetch_branch_fails_when_flyctl_missing():
    """fetch_branch returns 1 with an actionable error when flyctl is absent."""
    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "USER_REPO": "/tmp",
            "PATH": "/usr/bin:/bin",  # no flyctl here
        },
    )
    assert result.returncode != 0
    assert "flyctl" in result.stderr.lower()


def test_fetch_branch_fails_when_no_completed_run(tmp_path):
    """fetch_branch returns 1 when the machine has no finished_at run.json."""
    repo = _make_git_repo(tmp_path)

    # machine_runs_dir exists but has no run with finished_at.
    machine_runs = tmp_path / "mruns"
    machine_runs.mkdir()
    stale_run = machine_runs / "some-run-id"
    stale_run.mkdir()
    (stale_run / "run.json").write_text(json.dumps({"branch": "leerie/runs/some-run-id"}))

    fake_flyctl = _make_fake_flyctl(tmp_path, machine_runs, repo)

    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode != 0
    # Should report failure to discover a completed run.
    assert "discover" in result.stderr.lower() or "completed" in result.stderr.lower()


def test_fetch_branch_streams_bundle_and_state(tmp_path):
    """fetch_branch fetches the run branch and extracts the state directory."""
    repo = _make_git_repo(tmp_path)

    run_id = "feat-test-abc123"
    run_branch = f"leerie/runs/{run_id}"

    # Create the run branch in the repo (simulates the branch existing on the
    # machine — the bundle will be created from the local repo via stub).
    subprocess.run(
        ["git", "-C", str(repo), "branch", run_branch],
        check=True, capture_output=True,
    )

    # Set up the machine-side run state.
    machine_runs = tmp_path / "machine_runs"
    run_dir = machine_runs / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "finished_at": "2026-01-01T00:00:00Z",
        "branch": run_branch,
        "working_branch": "main",
    }))
    (run_dir / "state.json").write_text(json.dumps({"task": "test task"}))

    fake_flyctl = _make_fake_flyctl(tmp_path, machine_runs, repo)

    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "test-machine-abc",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    # The run branch should be present in the host repo (it was already there
    # as we created it for the bundle, so this confirms the bundle path ran).
    ls_branches = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", run_branch],
        capture_output=True, text=True,
    )
    assert run_branch in ls_branches.stdout, (
        f"run branch {run_branch} not found in host repo after fetch"
    )

    # The state directory should be extracted on the host.
    host_run_dir = repo / ".leerie" / "runs" / run_id
    assert host_run_dir.exists(), f"host run dir not found: {host_run_dir}"
    assert (host_run_dir / "run.json").exists(), "run.json not extracted on host"
    assert (host_run_dir / "state.json").exists(), "state.json not extracted on host"

    # Completion message should be present.
    assert "fetch complete" in result.stderr or run_id in result.stderr


def test_fetch_branch_skips_bundle_when_branch_missing(tmp_path):
    """A cleared-but-empty terminal-state run on the Fly machine
    (DESIGN §8) finishes cleanly but never ran setup-run.sh, so the
    run branch was never materialized. fetch_branch must:
      - probe branch existence on the machine via
        `git rev-parse --verify refs/heads/<branch>`
      - skip the bundle step when the probe fails (exit non-zero)
      - succeed overall (return 0)
      - still stream the state directory back so `leerie --list`
        shows the run as `done`.

    Critically, fetch_branch must NOT use `run.json.no_push` as a
    proxy for "no branch was materialized" — `no_push=true` is the
    mechanism flag the launcher always forces on the in-Fly
    orchestrator (the machine can't push). Using it as the probe
    would also skip the bundle for runs the user wants pushed.

    Without the branch-existence probe, the bundle step would fail
    with "git bundle is empty — run branch may not exist on machine"
    and the no-work run would be misreported as a fetch failure."""
    repo = _make_git_repo(tmp_path)

    run_id = "bugfix-already-done-cafe42"
    run_branch = f"leerie/runs/{run_id}"

    # Critically: do NOT create the run branch in the host repo or
    # any test fixture — the stub's git rev-parse --verify probe will
    # see no such ref and fetch_branch must skip the bundle step.

    machine_runs = tmp_path / "machine_runs"
    run_dir = machine_runs / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "finished_at": "2026-05-31T20:00:00Z",
        "no_push": True,  # mechanism flag from in-Fly orchestrator; NOT the trigger
        "branch": run_branch,
        "working_branch": "main",
    }))
    (run_dir / "state.json").write_text(json.dumps({
        "task": "fix already-done thing",
        "no_work_required": True,
    }))

    _make_fake_flyctl(tmp_path, machine_runs, repo)

    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "no-work-machine",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    # Must succeed.
    assert result.returncode == 0, (
        f"fetch_branch should return 0 for no-branch runs (skip bundle, "
        f"still stream state). stderr:\n{result.stderr}"
    )
    # Bundle step is skipped — must log the skip and must NOT attempt
    # a bundle (which would fail).
    assert "not present on machine" in result.stderr or "skipping bundle" in result.stderr, (
        f"fetch_branch should log that the bundle is skipped on missing "
        f"branch. stderr:\n{result.stderr}"
    )
    assert "bundle is empty" not in result.stderr, (
        f"fetch_branch must not attempt the bundle step when the branch "
        f"is missing. stderr:\n{result.stderr}"
    )
    # The branch should NOT have been created in the host repo (no
    # bundle was fetched).
    ls_branches = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", run_branch],
        capture_output=True, text=True,
    )
    assert run_branch not in ls_branches.stdout, (
        f"run branch {run_branch} should NOT be created for a no_push run"
    )
    # But the state directory MUST be streamed back so `leerie --list`
    # can render the run as done on the host.
    host_run_dir = repo / ".leerie" / "runs" / run_id
    assert host_run_dir.exists(), (
        f"host run dir not found: {host_run_dir} — Step 3 (state-dir "
        "streaming) must still run for no_push runs"
    )
    assert (host_run_dir / "run.json").exists()
    assert (host_run_dir / "state.json").exists()
    # CRITICAL: the defense-in-depth stripper at fetch-branch.sh:291 is
    # conditional on $_branch_present="true". Since the branch was
    # absent on the machine here, the stripper MUST be skipped and
    # the in-Fly orchestrator's `no_push=true` intent (written by
    # `_finish_no_work_run` per DESIGN §8) MUST be preserved on the
    # host-side run.json. Stripping it would disarm host_finalize's
    # no_push gate and the launcher would attempt `git push` against
    # a non-existent branch.
    host_rj = json.loads((host_run_dir / "run.json").read_text())
    assert host_rj.get("no_push") is True, (
        "fetch-branch.sh must preserve no_push=true on the host-side "
        "run.json when no run branch was materialized (the "
        "cleared-but-empty terminal state). The stripper at "
        "fetch-branch.sh:291 must remain conditional on "
        "$_branch_present=\"true\"."
    )


def test_fetch_branch_strips_no_push_when_branch_present(tmp_path):
    """Positive control for Fix A's conditional stripper.

    When a run branch *was* fetched (the `--branch_present="true"`
    arm), the defense-in-depth stripper at fetch-branch.sh:291 must
    still fire and remove `no_push=true` from the host-side run.json.
    This protects against in-flight runs started against an older
    image where the in-Fly orchestrator wrote the mechanism flag
    instead of the post-split intent flag. Without this, an old-image
    run that the user *wanted* pushed would be skipped by
    host_finalize's no_push gate."""
    repo = _make_git_repo(tmp_path)
    run_id = "feat-stripper-test-abc123"
    run_branch = f"leerie/runs/{run_id}"
    subprocess.run(
        ["git", "-C", str(repo), "branch", run_branch],
        check=True, capture_output=True,
    )

    machine_runs = tmp_path / "machine_runs"
    run_dir = machine_runs / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "finished_at": "2026-01-01T00:00:00Z",
        # Stray mechanism flag — old-image shape we want stripped.
        "no_push": True,
        "branch": run_branch,
        "working_branch": "main",
    }))
    (run_dir / "state.json").write_text("{}")

    _make_fake_flyctl(tmp_path, machine_runs, repo)

    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "test-machine",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    host_run_dir = repo / ".leerie" / "runs" / run_id
    host_rj = json.loads((host_run_dir / "run.json").read_text())
    assert "no_push" not in host_rj, (
        "fetch-branch.sh's stripper must remove no_push=true on the "
        "branch-present path so host_finalize will push. Found: "
        f"{host_rj}"
    )


def test_fetch_branch_picks_normal_run_over_no_push(tmp_path):
    """When the machine has both a normal completed run AND a no_push
    run, discovery must prefer the normal run — its branch needs to
    be fetched to enable push+PR. The no_push run is still on the
    machine's filesystem (audit), it just isn't selected for this
    fetch invocation. The discovery uses the most-recent mtime, so
    we use a normal run with a newer mtime to ensure it wins
    unambiguously."""
    repo = _make_git_repo(tmp_path)

    normal_run_id = "feat-do-something-real-123abc"
    normal_run_branch = f"leerie/runs/{normal_run_id}"
    no_push_run_id = "bugfix-already-done-456def"

    # Create the normal run branch in the host repo (so the bundle
    # step succeeds against the stub).
    subprocess.run(
        ["git", "-C", str(repo), "branch", normal_run_branch],
        check=True, capture_output=True,
    )

    machine_runs = tmp_path / "machine_runs"

    # No-push run (older mtime — written first).
    no_push_dir = machine_runs / no_push_run_id
    no_push_dir.mkdir(parents=True)
    (no_push_dir / "run.json").write_text(json.dumps({
        "finished_at": "2026-05-31T19:00:00Z",
        "no_push": True,
        "branch": f"leerie/runs/{no_push_run_id}",
        "working_branch": "main",
    }))
    (no_push_dir / "state.json").write_text("{}")

    # Sleep a beat so the normal run gets a strictly newer mtime.
    import time
    time.sleep(0.05)

    # Normal run (newer mtime).
    normal_dir = machine_runs / normal_run_id
    normal_dir.mkdir(parents=True)
    (normal_dir / "run.json").write_text(json.dumps({
        "finished_at": "2026-05-31T20:00:00Z",
        "branch": normal_run_branch,
        "working_branch": "main",
    }))
    (normal_dir / "state.json").write_text("{}")

    _make_fake_flyctl(tmp_path, machine_runs, repo)

    result = _run_bash(
        f'source {FETCH_SH}; fetch_branch && echo "RUN_ID=$LEERIE_REMOTE_RUN_ID"',
        env={
            "LEERIE_MACHINE_ID": "mixed-machine",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    # Discovery picked the normal run.
    assert f"RUN_ID={normal_run_id}" in result.stdout, (
        f"discovery should select the normal run over the no_push run "
        f"(newer mtime + non-no_push). stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    # The bundle step ran for the normal run (no skip message).
    assert "skipping branch bundle" not in result.stderr


def test_fetch_branch_exports_run_id(tmp_path):
    """fetch_branch exports LEERIE_REMOTE_RUN_ID on success."""
    repo = _make_git_repo(tmp_path, subdir="repo")

    run_id = "fix-export-test-deadbeef"
    run_branch = f"leerie/runs/{run_id}"
    subprocess.run(
        ["git", "-C", str(repo), "branch", run_branch],
        check=True, capture_output=True,
    )

    machine_runs = tmp_path / "mruns"
    run_dir = machine_runs / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "finished_at": "2026-01-01T00:00:00Z",
        "branch": run_branch,
        "working_branch": "main",
    }))
    (run_dir / "state.json").write_text("{}")

    fake_flyctl = _make_fake_flyctl(tmp_path, machine_runs, repo)

    result = _run_bash(
        f'source {FETCH_SH}; fetch_branch && echo "RUN_ID=$LEERIE_REMOTE_RUN_ID"',
        env={
            "LEERIE_MACHINE_ID": "m1",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    assert f"RUN_ID={run_id}" in result.stdout, (
        f"LEERIE_REMOTE_RUN_ID not exported correctly. stdout: {result.stdout}"
    )


# ---------------------------------------------------------------------------
# Step 4: .leerie/ stream-back tests
# ---------------------------------------------------------------------------

def _make_run_fixture(
    tmp_path: Path, run_id: str
) -> tuple[Path, Path, Path]:
    """Return (repo, machine_runs, run_branch) for a basic completed run."""
    repo = _make_git_repo(tmp_path)
    run_branch = f"leerie/runs/{run_id}"
    subprocess.run(
        ["git", "-C", str(repo), "branch", run_branch],
        check=True, capture_output=True,
    )
    machine_runs = tmp_path / "machine_runs"
    run_dir = machine_runs / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "finished_at": "2026-01-01T00:00:00Z",
        "branch": run_branch,
        "working_branch": "main",
    }))
    (run_dir / "state.json").write_text("{}")
    return repo, machine_runs, run_branch


def test_step4_streams_config_and_dockerfile_to_host(tmp_path):
    """Step 4 copies config.toml and Dockerfile from the machine to the
    host .leerie/ dir when neither exists on the host yet."""
    run_id = "feat-streamback-001"
    repo, machine_runs, _ = _make_run_fixture(tmp_path, run_id)

    # Create machine-side .leerie/ files.
    machine_leerie = tmp_path / "machine_leerie"
    machine_leerie.mkdir()
    (machine_leerie / "config.toml").write_text("[config]\nsetup_packages = []\n")
    (machine_leerie / "Dockerfile").write_text("FROM debian:13\n")

    _make_fake_flyctl(tmp_path, machine_runs, repo, machine_leerie_dir=machine_leerie)

    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "m-streamback",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    host_leerie = repo / ".leerie"
    assert (host_leerie / "config.toml").exists(), "config.toml not streamed to host"
    assert (host_leerie / "Dockerfile").exists(), "Dockerfile not streamed to host"
    assert (host_leerie / "config.toml").read_text() == "[config]\nsetup_packages = []\n"
    assert (host_leerie / "Dockerfile").read_text() == "FROM debian:13\n"


def test_step4_never_clobbers_existing_host_file(tmp_path):
    """Step 4 skips a file when the host target already exists."""
    run_id = "feat-noclobber-002"
    repo, machine_runs, _ = _make_run_fixture(tmp_path, run_id)

    # Pre-create host .leerie/config.toml with user-edited content.
    host_leerie = repo / ".leerie"
    host_leerie.mkdir()
    original_content = "# hand-edited\n[config]\nsetup_packages = ['git']\n"
    (host_leerie / "config.toml").write_text(original_content)

    # Machine has a different version.
    machine_leerie = tmp_path / "machine_leerie"
    machine_leerie.mkdir()
    (machine_leerie / "config.toml").write_text("[config]\nsetup_packages = []\n")
    (machine_leerie / "Dockerfile").write_text("FROM debian:13\n")

    _make_fake_flyctl(tmp_path, machine_runs, repo, machine_leerie_dir=machine_leerie)

    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "m-noclobber",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    # config.toml must be unchanged.
    assert (host_leerie / "config.toml").read_text() == original_content, (
        "Step 4 must not clobber an existing host-side config.toml"
    )
    # Dockerfile was absent on the host — should have been written.
    assert (host_leerie / "Dockerfile").exists(), "Dockerfile should be written when absent on host"


def test_step4_nonfatal_when_machine_file_absent(tmp_path):
    """Step 4 is non-fatal: fetch_branch still returns 0 when no .leerie/
    files exist on the machine (machine_leerie_dir is absent)."""
    run_id = "feat-nonfatal-003"
    repo, machine_runs, _ = _make_run_fixture(tmp_path, run_id)

    # Stub with no machine .leerie/ dir — test -f will fail for all files.
    _make_fake_flyctl(tmp_path, machine_runs, repo, machine_leerie_dir=None)

    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "m-nofail",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode == 0, (
        "fetch_branch must return 0 even when no machine .leerie/ files exist; "
        f"stderr:\n{result.stderr}"
    )


def test_step4_uses_leerie_state_host_dir(tmp_path):
    """Step 4 writes to LEERIE_STATE_HOST_DIR when the env var is set."""
    run_id = "feat-statehostdir-004"
    repo, machine_runs, _ = _make_run_fixture(tmp_path, run_id)

    machine_leerie = tmp_path / "machine_leerie"
    machine_leerie.mkdir()
    (machine_leerie / "config.toml").write_text("[config]\nsetup_packages = ['curl']\n")

    _make_fake_flyctl(tmp_path, machine_runs, repo, machine_leerie_dir=machine_leerie)

    custom_host_dir = tmp_path / "custom_leerie_state"
    custom_host_dir.mkdir()

    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "m-hostdir",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "LEERIE_STATE_HOST_DIR": str(custom_host_dir),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    # File must land in custom_host_dir, not repo/.leerie/.
    assert (custom_host_dir / "config.toml").exists(), (
        "config.toml must be written to LEERIE_STATE_HOST_DIR when set"
    )
    assert not (repo / ".leerie" / "config.toml").exists(), (
        "config.toml must NOT be written to USER_REPO/.leerie when "
        "LEERIE_STATE_HOST_DIR is set"
    )
