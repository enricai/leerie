"""Tests for the Stage-3a state-dir guard: two group members land in distinct
per-basename state dirs even when the parent env carries LEERIE_STATE_HOST_DIR,
and a shared LEERIE_STATE_DIR / --state-dir is rejected before any child spawns.

Background (from the plan's finding #1):
  The launcher exports LEERIE_STATE_HOST_DIR after resolution (leerie:561), so
  every child process inherits the *parent's* already-resolved state dir. This
  is safe because children re-resolve from cwd basename (leerie:502-508) and
  LEERIE_STATE_HOST_DIR is the *output* var — it does NOT feed back into
  resolution (the *input* var is LEERIE_STATE_DIR, leerie:531-534). So two
  members cd-ing into grp-api and grp-web each get their own
  ~/.leerie/<basename> regardless of the inherited output var.

  The trap: if LEERIE_STATE_DIR (input) or --state-dir (CLI) is forwarded into
  the child's env, all members resolve to the same shared dir and the .owner
  sidecar refuses the second member. The --group arm therefore rejects both
  before fan-out (leerie:2808-2813, leerie:2790-2793).

The harness below mirrors _state_dir_default (leerie:502-506) — the exact
resolution that each group-member subshell runs after cd-ing into its repo.
We test:
  A) With LEERIE_STATE_HOST_DIR inherited from the "parent", two members in
     repos with distinct basenames still resolve to distinct dirs.
  B) The --group arm itself rejects LEERIE_STATE_DIR / --state-dir (exercised
     via the real launcher subprocess tests below).

Model: tests/test_resolve_state_dir.py (_HARNESS pattern).
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

# ---------------------------------------------------------------------------
# Bash harness: simulates one group member's state-dir resolution.
#
# Each member receives USER_REPO (its repo path), HOME, and optionally
# inherits LEERIE_STATE_HOST_DIR (the parent's already-resolved output var)
# and/or LEERIE_STATE_DIR (the dangerous input var).  The harness reproduces
# leerie:502-561 — the block that runs in the child subshell after `cd <repo>`.
#
# Args: <user_repo> <fake_home>; remaining args are treated as CLI flags.
# Env: set LEERIE_STATE_HOST_DIR and/or LEERIE_STATE_DIR as needed.
# Output: the resolved LEERIE_STATE_HOST_DIR value.
# ---------------------------------------------------------------------------
_MEMBER_RESOLUTION_HARNESS = r"""
#!/usr/bin/env bash
set -euo pipefail
USER_REPO="$1"
HOME="$2"
export HOME
shift 2   # remaining args are simulated CLI flags

_state_dir_default() {
  local basename
  basename="$(python3 -c "import os,sys; print(os.path.basename(sys.argv[1].rstrip('/')))" "$USER_REPO")"
  echo "$HOME/.leerie/$basename"
}

LEERIE_STATE_HOST_DIR="$(_state_dir_default)"

if [ -f "$USER_REPO/leerie.toml" ]; then
  _toml_state_dir="$( { grep -E '^[[:space:]]*state_dir[[:space:]]*=' \
                            "$USER_REPO/leerie.toml" 2>/dev/null \
                        || true; } \
                      | head -1 \
                      | sed -E 's/^[[:space:]]*state_dir[[:space:]]*=[[:space:]]*//;
                                s/[[:space:]]*$//;
                                s/^"(.*)"$/\1/;
                                s/^'"'"'(.*)'"'"'$/\1/')"
  if [ -n "$_toml_state_dir" ]; then
    case "$_toml_state_dir" in
      "~")   _toml_state_dir="$HOME" ;;
      "~/"*) _toml_state_dir="$HOME/${_toml_state_dir#"~/"}" ;;
    esac
    LEERIE_STATE_HOST_DIR="$_toml_state_dir"
  fi
  unset _toml_state_dir
fi

if [ -n "${LEERIE_STATE_DIR:-}" ]; then
  LEERIE_STATE_HOST_DIR="$LEERIE_STATE_DIR"
fi

_cli_state_dir=""
_prev_was_state_dir=false
for arg in "$@"; do
  if $_prev_was_state_dir; then
    _cli_state_dir="$arg"
    _prev_was_state_dir=false
    continue
  fi
  case "$arg" in
    --state-dir=*) _cli_state_dir="${arg#--state-dir=}" ;;
    --state-dir)   _prev_was_state_dir=true ;;
  esac
done
if [ -n "$_cli_state_dir" ]; then
  LEERIE_STATE_HOST_DIR="$_cli_state_dir"
fi
unset _cli_state_dir _prev_was_state_dir

case "$LEERIE_STATE_HOST_DIR" in
  "~")   LEERIE_STATE_HOST_DIR="$HOME" ;;
  "~/"*) LEERIE_STATE_HOST_DIR="$HOME/${LEERIE_STATE_HOST_DIR#"~/"}" ;;
esac

echo "$LEERIE_STATE_HOST_DIR"
"""


def _resolve_member_dir(
    user_repo: Path,
    fake_home: Path,
    env: dict,
    cli_args: list[str] | None = None,
) -> str:
    """Run the member-resolution harness and return the resolved state dir."""
    result = subprocess.run(
        ["bash", "-c", _MEMBER_RESOLUTION_HARNESS, "--", str(user_repo), str(fake_home)]
        + (cli_args or []),
        env={**{"PATH": "/usr/bin:/bin"}, **env},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _make_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--allow-empty", "-m", "init"],
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
        capture_output=True, check=True,
    )


# ---------------------------------------------------------------------------
# A: Distinct-basename isolation when parent env carries LEERIE_STATE_HOST_DIR
# ---------------------------------------------------------------------------


class TestDistinctStateDirsUnderInheritedOutputVar:
    """Two group members in repos with distinct basenames must resolve to
    separate ~/.leerie/<basename> dirs even when the parent process has
    already exported LEERIE_STATE_HOST_DIR (for the first member's repo).

    This tests the exact subshell-sim finding in the plan: the *output* var
    LEERIE_STATE_HOST_DIR does not feed back into resolution (only the *input*
    var LEERIE_STATE_DIR would), so each child independently re-derives from
    its own cwd basename.
    """

    def test_two_members_distinct_basenames_resolve_to_distinct_dirs(
        self, tmp_path: Path
    ) -> None:
        """Core invariant: grp-api → ~/.leerie/grp-api, grp-web → ~/.leerie/grp-web."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        repo_api = tmp_path / "grp-api"
        repo_api.mkdir()
        repo_web = tmp_path / "grp-web"
        repo_web.mkdir()

        dir_api = _resolve_member_dir(repo_api, fake_home, {})
        dir_web = _resolve_member_dir(repo_web, fake_home, {})

        assert dir_api == str(fake_home) + "/.leerie/grp-api"
        assert dir_web == str(fake_home) + "/.leerie/grp-web"
        assert dir_api != dir_web

    def test_inherited_leerie_state_host_dir_does_not_pin_second_member(
        self, tmp_path: Path
    ) -> None:
        """When a child inherits LEERIE_STATE_HOST_DIR=~/.leerie/grp-api (the
        parent's already-resolved output var), the second member (grp-web) must
        still resolve to ~/.leerie/grp-web — NOT to ~/.leerie/grp-api.

        LEERIE_STATE_HOST_DIR is the *output* var (leerie:561 `export`), not
        the *input* var (LEERIE_STATE_DIR).  The resolution block re-derives
        from basename unconditionally (leerie:502-508), so the inherited output
        var is immediately overwritten and has no effect.
        """
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        repo_api = tmp_path / "grp-api"
        repo_api.mkdir()
        repo_web = tmp_path / "grp-web"
        repo_web.mkdir()

        # Simulate: parent already resolved api, exports LEERIE_STATE_HOST_DIR.
        parent_resolved_api = str(fake_home) + "/.leerie/grp-api"

        # Member 1 (grp-api): inherits the same value it would resolve to anyway.
        dir_api = _resolve_member_dir(
            repo_api, fake_home,
            {"LEERIE_STATE_HOST_DIR": parent_resolved_api},
        )
        assert dir_api == parent_resolved_api

        # Member 2 (grp-web): inherits LEERIE_STATE_HOST_DIR pointing at api,
        # but must resolve to its OWN basename.
        dir_web = _resolve_member_dir(
            repo_web, fake_home,
            {"LEERIE_STATE_HOST_DIR": parent_resolved_api},
        )
        assert dir_web == str(fake_home) + "/.leerie/grp-web"
        assert dir_web != dir_api

    def test_no_owner_collision_because_dirs_are_distinct(
        self, tmp_path: Path
    ) -> None:
        """Distinct dirs mean the .owner sidecar check never sees a collision.

        If both members resolved to the same dir, the second would fail the
        .owner check ('state-dir collision' in stderr). This test confirms they
        are distinct even under the inherited-output-var scenario.
        """
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        repo_api = tmp_path / "grp-api"
        repo_api.mkdir()
        repo_web = tmp_path / "grp-web"
        repo_web.mkdir()

        parent_resolved_api = str(fake_home) + "/.leerie/grp-api"

        dir_api = _resolve_member_dir(
            repo_api, fake_home,
            {"LEERIE_STATE_HOST_DIR": parent_resolved_api},
        )
        dir_web = _resolve_member_dir(
            repo_web, fake_home,
            {"LEERIE_STATE_HOST_DIR": parent_resolved_api},
        )

        # Distinct dirs means each member would create/claim its OWN .owner file.
        assert dir_api != dir_web
        # No shared prefix beyond ~/.leerie/.
        assert Path(dir_api).name != Path(dir_web).name

    def test_three_members_all_distinct(self, tmp_path: Path) -> None:
        """N > 2 group members each with distinct basenames all resolve distinctly."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        basenames = ["alpha-svc", "beta-svc", "gamma-svc"]
        repos = [tmp_path / bn for bn in basenames]
        for r in repos:
            r.mkdir()

        # Parent has already exported the first repo's resolved dir.
        parent_exported = str(fake_home) + "/.leerie/alpha-svc"

        dirs = [
            _resolve_member_dir(r, fake_home, {"LEERIE_STATE_HOST_DIR": parent_exported})
            for r in repos
        ]

        assert len(set(dirs)) == len(basenames), "All members must get distinct state dirs"
        for basename, resolved in zip(basenames, dirs):
            assert resolved == str(fake_home) + f"/.leerie/{basename}"

    def test_leerie_state_dir_input_var_does_pin_members(self, tmp_path: Path) -> None:
        """Confirm that LEERIE_STATE_DIR (the *input* var) DOES pin members to
        a shared dir. This is the trap the --group guard prevents by rejecting
        LEERIE_STATE_DIR before fan-out.

        This test documents the dangerous behavior: if LEERIE_STATE_DIR were
        forwarded to children (which --group must NOT do), both members would
        resolve to the same dir and the second .owner check would fail.
        """
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        repo_api = tmp_path / "grp-api"
        repo_api.mkdir()
        repo_web = tmp_path / "grp-web"
        repo_web.mkdir()

        shared_dir = str(tmp_path / "shared-state")

        # With LEERIE_STATE_DIR set, both members pin to the same dir.
        dir_api = _resolve_member_dir(
            repo_api, fake_home, {"LEERIE_STATE_DIR": shared_dir}
        )
        dir_web = _resolve_member_dir(
            repo_web, fake_home, {"LEERIE_STATE_DIR": shared_dir}
        )

        # Both resolve to the shared dir — this is why --group must reject it.
        assert dir_api == shared_dir
        assert dir_web == shared_dir
        assert dir_api == dir_web  # collision: .owner would refuse the 2nd


# ---------------------------------------------------------------------------
# B: --group arm rejects LEERIE_STATE_DIR and --state-dir (launcher level)
# ---------------------------------------------------------------------------


def _stub_recorder(tmp_path: Path) -> tuple[Path, Path]:
    """Minimal stub that records its argv and exits 0."""
    log = tmp_path / "stub.log"
    stub = tmp_path / "stub"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$@" >> "{log}"
        exit 0
        """))
    stub.chmod(0o755)
    return stub, log


def _run_launcher(
    tmp_path: Path,
    args: list[str],
    env_extra: dict | None = None,
    stub: Path | None = None,
    stub_log: Path | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the real launcher with the given args, returning a CompletedProcess
    with an extra `.stub_log` attribute."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "LEERIE_REPO": str(REPO_ROOT),
        "USER_REPO": str(tmp_path),
        # Intentionally omit LEERIE_STATE_DIR so the default guard tests
        # can inject it explicitly.
    }
    if env_extra:
        env.update(env_extra)
    if stub:
        env["LEERIE_SELF_CMD"] = str(stub)
    result = subprocess.run(
        ["bash", str(LAUNCHER)] + args,
        env=env, capture_output=True, text=True, timeout=30,
    )
    result.stub_log = stub_log.read_text() if stub_log and stub_log.exists() else ""
    return result


class TestGroupLauncherStateDirGuard:
    """The --group arm rejects LEERIE_STATE_DIR / --state-dir before fan-out.

    This is the REQUIRED Stage-3a guard: the --group arm must not forward
    these to children (they would pin all members to one dir and trip .owner
    on the second member).  Modeled on TestStateDirGuard in test_group_launcher.py
    but with a different fixture: no LEERIE_STATE_DIR in the default env, so
    we test the rejection explicitly by injecting it.
    """

    def test_rejects_leerie_state_dir_env(self, tmp_path: Path) -> None:
        """--group exits non-zero when LEERIE_STATE_DIR is set in env."""
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        _make_git_repo(repo_a)
        _make_git_repo(repo_b)
        stub, stub_log = _stub_recorder(tmp_path)
        result = _run_launcher(
            tmp_path,
            ["--group", "--repo", str(repo_a), "task a",
             "--repo", str(repo_b), "task b"],
            env_extra={"LEERIE_STATE_DIR": str(tmp_path / "shared-state")},
            stub=stub, stub_log=stub_log,
        )
        assert result.returncode != 0
        out = result.stdout + result.stderr
        assert "LEERIE_STATE_DIR" in out or "state-dir" in out.lower()
        assert result.stub_log == ""  # no children spawned

    def test_rejects_state_dir_cli_arg(self, tmp_path: Path) -> None:
        """--group exits non-zero when --state-dir is passed as a CLI arg."""
        repo_a = tmp_path / "repo-a"
        _make_git_repo(repo_a)
        stub, stub_log = _stub_recorder(tmp_path)
        result = _run_launcher(
            tmp_path,
            ["--group",
             "--state-dir", str(tmp_path / "custom"),
             "--repo", str(repo_a), "task"],
            stub=stub, stub_log=stub_log,
        )
        assert result.returncode != 0
        out = result.stdout + result.stderr
        assert "--state-dir" in out or "state-dir" in out.lower()
        assert result.stub_log == ""  # no children spawned

    def test_group_succeeds_without_state_dir_env(self, tmp_path: Path) -> None:
        """--group proceeds normally when LEERIE_STATE_DIR is NOT set."""
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        _make_git_repo(repo_a)
        _make_git_repo(repo_b)
        stub, stub_log = _stub_recorder(tmp_path)
        result = _run_launcher(
            tmp_path,
            ["--group",
             "--group-id", "deadbeef-1234-4abc-8def-0123456789ab",
             "--repo", str(repo_a), "task a",
             "--repo", str(repo_b), "task b"],
            stub=stub, stub_log=stub_log,
        )
        assert result.returncode == 0, result.stderr
        # Both children were spawned.
        assert stub_log.exists()
        log = result.stub_log
        assert log.count("--group-id") >= 2

    def test_error_message_names_leerie_state_dir(self, tmp_path: Path) -> None:
        """Rejection message names LEERIE_STATE_DIR so the user knows what to fix."""
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        _make_git_repo(repo_a)
        _make_git_repo(repo_b)
        stub, _ = _stub_recorder(tmp_path)
        result = _run_launcher(
            tmp_path,
            ["--group", "--repo", str(repo_a), "task a",
             "--repo", str(repo_b), "task b"],
            env_extra={"LEERIE_STATE_DIR": str(tmp_path / "shared-state")},
            stub=stub,
        )
        assert result.returncode != 0
        assert "LEERIE_STATE_DIR" in (result.stdout + result.stderr)
