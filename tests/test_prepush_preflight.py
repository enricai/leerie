"""`host_prepush_preflight` — catch a rejecting pre-push hook at t=0.

`host_finalize` ends in `git push`, and a repository `pre-push` hook gates
that push against the HOST CHECKOUT's working tree — which leerie never
modifies during a run (workers run in the container; the finalize rebase
uses a disposable worktree). So a hook that rejects today still rejects at
finalize, after the run has been paid for. Measured on the run that
motivated this: the host's manifests were rewritten at 18:46:10 and the run
started at 18:48:14, so the defect that rejected the push 2h19m later was
already present before the first worker spawned — as it was for all four
earlier `pnpm: not found` rejections.

These are pure git/bash tests against real repositories with real hooks — no
`jq`, no stubbed git — following `test_host_finalize_hook_probe.py` rather
than `test_host_finalize_sh.py`. The probe's whole value is that it runs the
real gate, so stubbing git would test nothing.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOST_FINALIZE_SH = REPO_ROOT / "scripts" / "host-finalize.sh"

PROBE_REF = "leerie/runs/preflight-probe"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real repo with a real `origin` (a bare repo next door), one commit,
    and `main` already pushed — i.e. the shape a leerie run starts from."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    (work / "a.txt").write_text("x\n")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-qm", "init")
    _git(work, "remote", "add", "origin", str(origin))
    assert _git(work, "push", "-q", "--no-verify", "-u", "origin", "main").returncode == 0
    return work


def _hook(repo: Path, body: str) -> None:
    h = repo / ".git" / "hooks" / "pre-push"
    h.write_text(body)
    h.chmod(0o755)


def _preflight(repo: Path, branch: str = "main") -> subprocess.CompletedProcess:
    """Call the real function under the `set -euo pipefail` its caller sets."""
    return subprocess.run(
        ["bash", "-c",
         f"set -euo pipefail; . {HOST_FINALIZE_SH}; "
         f'host_prepush_preflight "$1" "$2"', "_", str(repo), branch],
        capture_output=True, text=True, check=False,
    )


def _remote_refs(repo: Path) -> set[str]:
    out = _git(repo, "ls-remote", "--heads", "origin").stdout
    return {ln.split("\t")[-1] for ln in out.splitlines() if ln.strip()}


# --- the contract ---------------------------------------------------------

def test_no_hook_is_silent_and_free(repo):
    """Repos without a pre-push hook must pay nothing — no push, no output."""
    hook = repo / ".git" / "hooks" / "pre-push"
    assert not hook.exists()
    r = _preflight(repo)
    assert r.returncode == 0
    assert r.stdout == "" and r.stderr == ""


def test_passing_hook_is_silent(repo):
    _hook(repo, "#!/bin/sh\nexit 0\n")
    r = _preflight(repo)
    assert r.returncode == 0, r.stderr
    assert r.stderr == ""


def test_failing_hook_warns_and_returns_one(repo):
    _hook(repo, "#!/bin/sh\nexit 1\n")
    r = _preflight(repo)
    assert r.returncode == 1
    assert "pre-push` hook already fails" in r.stderr
    assert "`main`" in r.stderr, "the warning must name the tree it probed"


def test_hook_output_is_surfaced_including_stdout(repo):
    """The same split-stream reasoning as the push path: a hook's complaint
    is usually on stdout (tsc, biome), so a stderr-only report would show the
    operator nothing."""
    _hook(repo, "#!/bin/sh\n"
                "echo \"src/x.ts(1,1): error TS2307: Cannot find module 'ai'\"\n"
                "exit 1\n")
    r = _preflight(repo)
    assert r.returncode == 1
    assert "TS2307" in r.stderr


def test_oversized_hook_output_says_it_is_showing_a_tail(repo):
    """Silently cutting output and presenting it as complete is the part
    worth avoiding — the sibling push path prints a marker for the same
    reason. A hook running a test suite reaches this size trivially."""
    _hook(repo, "#!/bin/sh\n"
                'for i in $(seq 1 300); do\n'
                '  echo "src/f$i.ts(1,1): error TS2307: module $i"\n'
                "done\n"
                "exit 1\n")
    r = _preflight(repo)
    assert r.returncode == 1
    assert "Hook output (last 1500 bytes):" in r.stderr
    # Tail-anchored: the end of the report survives, the head does not.
    assert "module 300" in r.stderr
    assert "module 1)" not in r.stderr


def test_short_hook_output_is_not_labelled_as_truncated(repo):
    """Anti-vacuity partner: the marker must not appear when nothing was
    cut, or it stops meaning anything."""
    _hook(repo, "#!/bin/sh\necho 'one short line'\nexit 1\n")
    r = _preflight(repo)
    assert r.returncode == 1
    assert "Hook output:" in r.stderr
    assert "last 1500 bytes" not in r.stderr
    assert "one short line" in r.stderr


def test_missing_arguments_are_a_silent_noop(repo):
    for args in (('""', '"main"'), (f'"{repo}"', '""')):
        r = subprocess.run(
            ["bash", "-c", f"set -euo pipefail; . {HOST_FINALIZE_SH}; "
                           f"host_prepush_preflight {args[0]} {args[1]}"],
            capture_output=True, text=True, check=False)
        assert r.returncode == 0 and r.stderr == ""


# --- the two properties that make it safe and honest ----------------------

def test_probe_creates_no_ref_anywhere(repo):
    """`--dry-run` is what makes running the real gate safe. Nothing may be
    created on the remote or locally, on either outcome."""
    before = _remote_refs(repo)
    for body in ("#!/bin/sh\nexit 1\n", "#!/bin/sh\nexit 0\n"):
        _hook(repo, body)
        _preflight(repo)
        assert _remote_refs(repo) == before
        local = {ln.strip() for ln in _git(repo, "branch", "--list").stdout.splitlines()}
        assert not any(PROBE_REF in b for b in local)
    assert before == {"refs/heads/main"}


def test_probe_pushes_a_new_ref_so_the_hook_gets_real_stdin(repo):
    """THE load-bearing test.

    git hands the pre-push hook its ref updates on stdin. Probing the
    already-up-to-date working branch still RUNS the hook but hands it an
    EMPTY stdin (verified against real git), so any hook that iterates those
    refs does nothing and exits 0 — a false pass, which is the worst possible
    outcome for a probe whose entire job is to predict a rejection.

    This hook fails only when git actually fed it a ref line. It must fail.
    """
    _hook(repo, "#!/bin/sh\n"
                "if read -r line; then\n"
                '  echo "got ref update: $line"\n'
                "  exit 1\n"
                "fi\n"
                "exit 0\n")
    r = _preflight(repo)
    assert r.returncode == 1, (
        "the hook saw empty stdin — the probe is pushing an up-to-date ref "
        "instead of a new one, and will false-pass on any stdin-reading hook"
    )
    assert "got ref update:" in r.stderr
    # The ref line's shape is what finalize itself will produce: an all-zero
    # old sha, i.e. "new branch".
    assert PROBE_REF in r.stderr
    assert "0000000000000000000000000000000000000000" in r.stderr


def test_auth_or_network_failure_is_not_reported_as_a_hook_problem(repo):
    """A transport failure is not what this probe is for — the real push
    reports it properly, and a warning here would be noise on every run made
    offline. Classified with the same stderr-only rule as the push path."""
    _hook(repo, "#!/bin/sh\nexit 0\n")
    _git(repo, "remote", "set-url", "origin", str(repo.parent / "nonexistent.git"))
    r = _preflight(repo)
    assert r.returncode == 0, r.stderr
    assert "hook already fails" not in r.stderr


def test_hook_probe_short_circuits_before_any_network_call(repo):
    """Ordering guard: the hook-present check must gate the push, not the
    other way round, or every hookless repo pays a round trip to origin."""
    _git(repo, "remote", "set-url", "origin", str(repo.parent / "nonexistent.git"))
    r = _preflight(repo)   # no hook installed
    assert r.returncode == 0 and r.stderr == ""


def test_probe_disables_git_credential_prompts(repo):
    """A check meant to save the operator time must never be what hangs.

    `git push --dry-run` contacts the remote, so on an HTTPS remote with no
    cached credential git would prompt for a username at run start — where,
    before this probe existed, the user was not yet waiting on anything.

    Structural, because reproducing a real prompt needs an HTTPS server
    returning 401 (what the classifier's own corpus used) and that is out of
    scope for this file. The half that could actually be wrong — whether the
    resulting message composes with the existing classifier — is asserted
    behaviourally in the next test.
    """
    src = HOST_FINALIZE_SH.read_text()
    body = src[src.index("host_prepush_preflight() {"):]
    body = body[:body.index("\nhost_finalize() {")]
    call = next(ln for ln in body.splitlines() if "push --dry-run" in ln)
    assert "GIT_TERMINAL_PROMPT=0" in call, (
        "the probe can block run start on a credential prompt")


def test_terminal_prompts_disabled_classifies_as_auth_not_a_hook_failure():
    """The composition claim, against the real classifier.

    With prompts disabled git emits this exact line. It must land in the
    auth/network arm, so the probe returns 0 silently rather than blaming the
    repo's hook for a credential problem `--no-verify` cannot fix.
    """
    msg = ("fatal: could not read Username for 'https://github.com': "
           "terminal prompts disabled")
    r = subprocess.run(
        ["bash", "-c",
         f'set -euo pipefail; . {HOST_FINALIZE_SH}; '
         '_host_finalize_is_auth_or_network_push_error "$1"', "_", msg],
        capture_output=True, text=True, check=False)
    assert r.returncode == 0, (
        "git's prompts-disabled message is not classified as auth/network, so "
        "the probe would report it as a failing hook")


# --- launcher wiring ------------------------------------------------------

def _launcher_preflight_block() -> str:
    """Extract the launcher's preflight block verbatim rather than
    reproducing it (CLAUDE.md's no-duplicate-launcher-blocks discipline)."""
    src = (REPO_ROOT / "leerie").read_text()
    start = src.index("# 4. pre-push hook preflight")
    end = src.index("unset _skip_prepush_preflight", start)
    return src[start:end] + "unset _skip_prepush_preflight\n"


@pytest.mark.parametrize("env,expect_probe", [
    ({}, True),
    ({"_NO_PUSH": "true"}, False),
    ({"_NO_VERIFY": "true"}, False),
    ({"LEERIE_SKIP_PREPUSH_PREFLIGHT": "1"}, False),
])
def test_launcher_gates(repo, env, expect_probe):
    """The block runs the probe by default and is skipped by each of its
    three documented opt-outs."""
    block = _launcher_preflight_block()
    _hook(repo, "#!/bin/sh\nexit 1\n")
    script = (
        "set -euo pipefail\n"
        f'NO_PUSH="{env.get("_NO_PUSH", "false")}"\n'
        f'NO_VERIFY_PUSH="{env.get("_NO_VERIFY", "false")}"\n'
        f'LEERIE_REPO="{REPO_ROOT}"\n'
        f'USER_REPO="{repo}"\n'
        f'cd "{repo}"\n'
        + block
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(repo.parent),
                            **{k: v for k, v in env.items()
                               if not k.startswith("_")}},
                       check=False)
    assert r.returncode == 0, f"the probe must never refuse to start: {r.stderr}"
    assert ("hook already fails" in r.stderr) is expect_probe, r.stderr


def test_launcher_treats_the_verdict_as_advisory(repo):
    """A failing probe must warn and continue. Under `set -euo pipefail` an
    unguarded rc 1 would abort the launcher, converting a warning into a new
    way to refuse to start a run."""
    block = _launcher_preflight_block()
    _hook(repo, "#!/bin/sh\nexit 1\n")
    script = (
        "set -euo pipefail\n"
        'NO_PUSH="false"\nNO_VERIFY_PUSH="false"\n'
        f'LEERIE_REPO="{REPO_ROOT}"\nUSER_REPO="{repo}"\ncd "{repo}"\n'
        + block + '\necho "REACHED-THE-NEXT-STEP"\n'
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(repo.parent)},
                       check=False)
    assert "hook already fails" in r.stderr
    assert "REACHED-THE-NEXT-STEP" in r.stdout
    assert r.returncode == 0


# --- chain: probe once per wave, never once per job -----------------------

def _chain_fanout_block() -> str:
    """The chain arm's per-wave probe + fan-out, extracted verbatim."""
    src = (REPO_ROOT / "leerie").read_text()
    start = src.index("        # One pre-push hook probe per WAVE")
    end = src.index("_ch_wave_child_pids+=($!)", start)
    return src[start:end]


def _chain_probe_block() -> str:
    """Just the probe, bounded before the fan-out.

    Deliberately narrower than `_chain_fanout_block`: running *that* would
    background a real `./leerie`. This one is safe to execute, which is the
    point — the sibling single-run block is executed by `test_launcher_gates`,
    and a block that is only ever string-matched would pass unchanged if the
    launcher stopped evaluating it (the lesson `test_ec2_bash32_portability.py`
    records: sourcing, or scanning, is not calling).
    """
    src = (REPO_ROOT / "leerie").read_text()
    start = src.index("        # One pre-push hook probe per WAVE")
    end = src.index("unset _ch_skip_probe _ch_arg", start)
    return src[start:end] + "unset _ch_skip_probe _ch_arg\n"


def _run_chain_probe(repo: Path, *, passthrough: str = "",
                     env: dict | None = None) -> subprocess.CompletedProcess:
    script = (
        "set -euo pipefail\n"
        f'LEERIE_REPO="{REPO_ROOT}"\n'
        f'USER_REPO="{repo}"\n'
        '_ch_current_base="main"\n'
        f"_ch_passthrough=({passthrough})\n"
        + _chain_probe_block()
        + '\necho "REACHED-FANOUT"\n'
    )
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(repo.parent), **(env or {})},
        check=False)


def test_chain_probe_block_actually_runs(repo):
    """Executable coverage for the block, not just a string match.

    `bash -n` catches syntax; it does not catch an unbound variable or a
    quoting bug under `set -euo pipefail`, which is what this runs into if
    the block is wrong. The empty `_ch_passthrough` here is the common case
    and the one that breaks a bare `"${arr[@]}"` on bash 3.2.
    """
    _hook(repo, "#!/bin/sh\nexit 1\n")
    r = _run_chain_probe(repo)
    assert r.returncode == 0, f"the chain probe must not abort the wave: {r.stderr}"
    assert "hook already fails" in r.stderr
    assert "REACHED-FANOUT" in r.stdout, "the block aborted before the fan-out"
    assert r.stderr.count("hook already fails") == 1, "probed more than once"


def test_chain_probe_is_skipped_for_a_no_push_chain(repo):
    """`chain --no-push` pushes nothing, so probing the hook is pure noise.

    The gate has to read `_ch_passthrough`: `NO_PUSH` is first assigned
    further down the launcher, after this arm, so it does not exist here.
    """
    _hook(repo, "#!/bin/sh\nexit 1\n")
    r = _run_chain_probe(repo, passthrough='"--no-push"')
    assert r.returncode == 0
    assert "hook already fails" not in r.stderr
    assert "REACHED-FANOUT" in r.stdout


def test_chain_probe_honours_the_env_opt_outs(repo):
    _hook(repo, "#!/bin/sh\nexit 1\n")
    for var in ("LEERIE_SKIP_PREPUSH_PREFLIGHT", "LEERIE_NO_PUSH"):
        r = _run_chain_probe(repo, env={var: "1"})
        assert r.returncode == 0
        assert "hook already fails" not in r.stderr, var
        assert "REACHED-FANOUT" in r.stdout


def test_chain_probe_runs_with_unrelated_passthrough_flags(repo):
    """Anti-vacuity for the skip tests above: an array that is non-empty but
    contains no `--no-push` must still probe."""
    _hook(repo, "#!/bin/sh\nexit 1\n")
    r = _run_chain_probe(repo, passthrough='"--effort" "high"')
    assert "hook already fails" in r.stderr
    assert "REACHED-FANOUT" in r.stdout


def test_chain_children_are_told_to_skip_the_probe():
    """A chain backgrounds one `./leerie` per job against the SAME checkout,
    so without this every job re-runs the hook — N concurrent lint/typecheck
    runs computing one answer, and N identical warnings."""
    block = _chain_fanout_block()
    launch = next(ln for ln in block.splitlines()
                  if "LEERIE_SELF_CMD" in ln and "--runtime fly" in ln)
    assert "LEERIE_SKIP_PREPUSH_PREFLIGHT=1" in launch, (
        "chain children are not told to skip the probe; a wave of N jobs "
        "will run N concurrent hook probes on one working tree"
    )
    # And it must be an env prefix on the child command, not an export that
    # would also suppress the parent's own probe below.
    assert launch.index("LEERIE_SKIP_PREPUSH_PREFLIGHT=1") < launch.index("$0")


def test_chain_probes_once_itself_after_checking_out_the_wave_base():
    """Skipping in the children is only half the fix — dropping the check
    from the most expensive kind of run is the opposite of the point. The
    parent must probe, and it must do so against the tree the wave will push
    from, which the checkout immediately above establishes."""
    src = (REPO_ROOT / "leerie").read_text()
    checkout = src.index('git -C "$USER_REPO" checkout "$_ch_current_base"')
    probe = src.index('host_prepush_preflight "$USER_REPO" "$_ch_current_base"')
    fanout = src.index("LEERIE_SELF_CMD", probe)
    assert checkout < probe < fanout, (
        "the per-wave probe must sit between the wave checkout and the fan-out"
    )
    block = _chain_fanout_block()
    assert "|| true" in block, "the chain probe must stay advisory under set -e"
    assert "LEERIE_SKIP_PREPUSH_PREFLIGHT" in block, (
        "the chain probe must honour its own opt-out")


def test_launcher_skips_a_detached_head(repo):
    """A detached HEAD has no branch to push from; guessing one would probe
    a ref the run will never push."""
    block = _launcher_preflight_block()
    _hook(repo, "#!/bin/sh\nexit 1\n")
    _git(repo, "checkout", "-q", "--detach", "HEAD")
    script = (
        "set -euo pipefail\n"
        'NO_PUSH="false"\nNO_VERIFY_PUSH="false"\n'
        f'LEERIE_REPO="{REPO_ROOT}"\nUSER_REPO="{repo}"\ncd "{repo}"\n'
        + block
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(repo.parent)},
                       check=False)
    assert r.returncode == 0
    assert "hook already fails" not in r.stderr
