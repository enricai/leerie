"""Regression for the resume self-heal probe.

DESIGN §6 *Shallow seeding for heavy repos* (resume corollary). When
the initial seed dies before completing, /work is left partial/absent
and no run state exists. A dirty-only re_seed would rsync onto a
non-repo and the orchestrator would then die with "run-id does not
match any known run". The launcher's resume path probes /work validity
and, if invalid, runs the full seed_repo to self-heal — but MUST keep
the dirty-only re_seed when /work is valid (re-cloning would obliterate
the run branch + per-subtask branches).

The probe is TOKEN-BASED: the remote command always exits 0 when SSH
works and prints VALID / INVALID. This distinguishes a genuinely
unseeded /work (INVALID → safe to wipe + self-heal) from an SSH
transport failure (non-zero flyctl rc / no token). The destructive
full seed_repo runs ONLY on a confirmed round-trip returning INVALID;
an ambiguous probe (SSH blip) must NEVER wipe /work.

Three layers of coverage:
  1. The remote token command reports VALID/INVALID correctly for the
     realistic /work states.
  2. The launcher decision logic self-heals only on (rc==0 AND INVALID)
     and never wipes on an inconclusive probe.
  3. Coupling: the launcher wires the token probe before re_seed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

# The remote command the launcher runs (path localized to a test dir
# instead of /work). Mirrors leerie's `sh -c 'if [ -d /work/.git ] && ...'`.
_REMOTE_TOKEN_CMD = (
    "if [ -d {work}/.git ] && git -C {work} rev-parse --verify HEAD "
    ">/dev/null 2>&1; then echo VALID; else echo INVALID; fi"
)

# The launcher's decision logic, reproduced so a drift makes the coupling
# test fail. $1 = probe rc, $2 = token, $3 = NO_RE_SEED. Echoes the branch
# taken: SELF_HEAL (wipes /work) | RESEED | NOOP.
_DECISION = r"""
_work_probe_rc="$1"; _work_probe="$2"; NO_RE_SEED="${3:-false}"
if [ "$_work_probe_rc" -eq 0 ] && [ "$_work_probe" = "INVALID" ]; then
  echo SELF_HEAL
elif [ "$NO_RE_SEED" != "true" ]; then
  echo RESEED
else
  echo NOOP
fi
"""


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True)


def _token(work: Path) -> str:
    """Run the remote token command against a local dir; return VALID/INVALID."""
    r = subprocess.run(
        ["bash", "-c", _REMOTE_TOKEN_CMD.format(work=str(work))],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, "token command must always exit 0 when it runs"
    return r.stdout.strip()


def _decide(rc: int, token: str, no_re_seed: bool = False) -> str:
    r = subprocess.run(
        ["bash", "-c", _DECISION, "bash", str(rc), token,
         "true" if no_re_seed else "false"],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


# --- layer 1: the remote token command reports the right token ----------

def test_token_empty_work_is_invalid(tmp_path):
    work = tmp_path / "work"; work.mkdir()
    assert _token(work) == "INVALID"


def test_token_partial_junk_is_invalid(tmp_path):
    work = tmp_path / "work"; work.mkdir()
    (work / "leftover").write_text("partial")
    assert _token(work) == "INVALID"


def test_token_unborn_head_is_invalid(tmp_path):
    work = tmp_path / "work"; work.mkdir()
    _git("init", "-q", cwd=work)
    assert _token(work) == "INVALID"


def test_token_seeded_with_run_branch_is_valid(tmp_path):
    """A fully seeded repo with a run branch → VALID. The safety-critical
    case: a wipe would obliterate the run branch."""
    work = tmp_path / "work"; work.mkdir()
    _git("init", "-q", cwd=work)
    _git("config", "user.email", "t@t", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    (work / "f").write_text("x")
    _git("add", "f", cwd=work)
    _git("commit", "-qm", "init", cwd=work)
    _git("checkout", "-qb", "leerie/runs/z/run", cwd=work)
    (work / "f").write_text("y")
    _git("add", "f", cwd=work)
    _git("commit", "-qm", "worker", cwd=work)
    assert _token(work) == "VALID"
    branches = _git("branch", "--list", "leerie/runs/*", cwd=work).stdout
    assert "leerie/runs/z/run" in branches


# --- layer 2: the decision only wipes on a CONFIRMED INVALID -------------

def test_decision_invalid_self_heals():
    """Confirmed round-trip + INVALID → self-heal (the only wipe path)."""
    assert _decide(0, "INVALID") == "SELF_HEAL"


def test_decision_valid_reseeds():
    """Confirmed VALID → dirty-only re_seed, never a wipe."""
    assert _decide(0, "VALID") == "RESEED"


def test_decision_ssh_failure_does_not_wipe():
    """The load-bearing safety property: an SSH/transport failure (non-zero
    rc, no token) must NOT trigger the destructive seed_repo — it takes the
    non-wiping re_seed path so a valid /work with a run branch survives a
    transient blip."""
    for rc in (1, 2, 124, 255):
        assert _decide(rc, "") != "SELF_HEAL", (
            f"probe rc={rc} (SSH failure) must never self-heal/wipe"
        )
        assert _decide(rc, "") == "RESEED"


def test_decision_garbage_token_does_not_wipe():
    """A partial/garbled token on rc=0 (unexpected) also must not wipe."""
    assert _decide(0, "VALI") != "SELF_HEAL"
    assert _decide(0, "") != "SELF_HEAL"


def test_decision_no_re_seed_respected():
    """--no-re-seed: VALID → noop; INVALID still self-heals (no run state to
    preserve, and the machine can't proceed without a valid /work)."""
    assert _decide(0, "VALID", no_re_seed=True) == "NOOP"
    assert _decide(0, "INVALID", no_re_seed=True) == "SELF_HEAL"


# --- layer 3: coupling to the launcher ----------------------------------

def test_launcher_wires_token_probe():
    """The launcher's resume arm must use the token probe and gate the wipe
    on (rc==0 AND INVALID). Pin the structural markers so a refactor can't
    silently reintroduce the SSH-blip-wipes-/work bug."""
    src = LAUNCHER.read_text()
    # Token-based remote command.
    assert "echo VALID; else echo INVALID" in src, (
        "resume probe must emit VALID/INVALID tokens (always exit 0 on SSH "
        "success) so an SSH failure is distinguishable from an invalid /work"
    )
    # rc captured without tripping set -e.
    assert "|| _work_probe_rc=$?" in src, (
        "probe rc must be captured via `|| _work_probe_rc=$?` — a bare "
        "assignment would trip set -e and abort on SSH failure"
    )
    # Wipe gated on confirmed INVALID.
    assert '[ "$_work_probe_rc" -eq 0 ] && [ "$_work_probe" = "INVALID" ]' in src, (
        "the destructive seed_repo must run ONLY on rc==0 AND INVALID"
    )
    assert "self-heal" in src.lower()
    assert 'elif [ "$NO_RE_SEED" != "true" ]; then' in src
    # The old boolean probe must be gone (it wiped on any non-zero rc).
    assert "test -d /work/.git && git -C /work rev-parse" not in src, (
        "the old boolean probe (wipes on any non-zero flyctl rc, incl. SSH "
        "failure) must be replaced by the token probe"
    )
