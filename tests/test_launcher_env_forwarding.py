"""Tests for the local nerdctl run LEERIE_* env-var forwarding.

The orchestrator runs *inside* the container and reads its overrides from
`os.environ` — which only inherits what `nerdctl run` explicitly forwards.
The launcher forwards every `LEERIE_*` var in its environment EXCEPT a
deny-list of launcher/host-only vars (see the `nerdctl run` block in
`leerie`). Without this, host-set overrides like LEERIE_WORKER_PIDS_MAX die
at the container boundary and silently do nothing.

These tests reproduce the launcher's forwarding loop + the `-e` lines of
the `nerdctl run` invocation with a stubbed `nerdctl` that echoes its argv,
and assert:
  - an orchestrator-side var (LEERIE_WORKER_PIDS_MAX) is forwarded via `-e`
  - a dynamic per-worker var (LEERIE_MODEL_IMPLEMENTER) is forwarded
  - a deny-listed var (LEERIE_STATE_DIR) is NOT forwarded by the loop (it is
    remapped separately to /leerie-state)
  - an unset var is not forwarded
  - a launcher-side var (LEERIE_RUNTIME) is NOT forwarded

The harness extracts the forwarding loop verbatim from the launcher so the
two cannot silently diverge (coupling-guard spirit of
test_launcher_value_flags_coupling.py).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _extract_forwarding_loop() -> str:
    """Pull the deny-list + forwarding loop verbatim from the launcher so the
    test exercises the real code, not a copy. Spans from the deny-list array
    assignment through the loop that populates `_leerie_env_args`."""
    src = LAUNCHER.read_text()
    m = re.search(
        r"(_leerie_env_denylist=\" \\.*?_leerie_env_args\+=\(\"-e\" \"\$_name\"\)\n  done\n)",
        src,
        re.DOTALL,
    )
    assert m, "could not locate the LEERIE_* env-forwarding loop in the launcher"
    return m.group(1)


_HARNESS = r"""
#!/usr/bin/env bash
set -euo pipefail

# ---- forwarding loop, extracted verbatim from the launcher --------------
__FORWARDING_LOOP__

# ---- stub nerdctl: print argv one-per-line, then exit 0 -----------------
nerdctl() {
  for a in "$@"; do printf '%s\n' "$a"; done
}

# ---- reproduce the -e lines of the launcher's nerdctl run ---------------
nerdctl run \
  --rm -i \
  -e LEERIE_INSPECT_DIRS= \
  -e LEERIE_STATE_DIR=/leerie-state \
  ${_leerie_env_args[@]+"${_leerie_env_args[@]}"} \
  "leerie:test"
"""


def _run(env: dict) -> list[str]:
    """Run the harness with `env` overlaid on a minimal base; return argv
    tokens the stubbed nerdctl echoed."""
    harness = _HARNESS.replace("__FORWARDING_LOOP__", _extract_forwarding_loop())
    result = subprocess.run(
        ["bash", "-c", harness],
        env={"PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return [t for t in result.stdout.splitlines() if t]


def _forwarded_names(tokens: list[str]) -> set[str]:
    """Names passed via a bare `-e NAME` (the forwarding-loop form)."""
    names = set()
    for i, tok in enumerate(tokens):
        if tok == "-e" and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            # the loop emits bare `-e NAME`; the two remapped lines are `-e N=V`
            if "=" not in nxt:
                names.add(nxt)
    return names


def _extract_run_argv() -> str:
    """Pull the `_run_argv` array assignment verbatim from the launcher, same
    reason as _extract_forwarding_loop: assert against real code, not a copy."""
    src = LAUNCHER.read_text()
    m = re.search(r"(  _run_argv=\(\n.*?\n  \)\n)", src, re.DOTALL)
    assert m, "could not locate the _run_argv array in the launcher"
    return m.group(1)


_ARGV_HARNESS = r"""
#!/usr/bin/env bash
set -euo pipefail

# Stub every var the array interpolates; USER_REPO is the one under test.
USER_REPO=/Users/andres/src/enric/stackpulse
LEERIE_REPO=/opt/leerie
LEERIE_STATE_HOST_DIR=/tmp/state
TTY_FLAGS=""
_cidfile=/tmp/cid
REPO_IMAGE_TAG=""
IMAGE_TAG=leerie:test
_leerie_env_args=()

# ---- _run_argv, extracted verbatim from the launcher --------------------
__RUN_ARGV__

for a in "${_run_argv[@]}"; do printf '%s\n' "$a"; done
"""


def _run_argv_tokens() -> list[str]:
    """Assemble the launcher's real _run_argv with stubbed vars; return tokens."""
    harness = _ARGV_HARNESS.replace("__RUN_ARGV__", _extract_run_argv())
    result = subprocess.run(
        ["bash", "-c", harness],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return [t for t in result.stdout.splitlines() if t]


def _env_pairs(tokens: list[str]) -> dict[str, str]:
    """Explicit `-e NAME=VALUE` pairs. The forwarding loop's bare `-e NAME`
    form is handled by _forwarded_names(); this is its NAME=VALUE sibling."""
    pairs = {}
    for i, tok in enumerate(tokens):
        if tok == "-e" and i + 1 < len(tokens) and "=" in tokens[i + 1]:
            name, _, value = tokens[i + 1].partition("=")
            pairs[name] = value
    return pairs


def test_user_repo_delivered_to_container():
    """The boundary guard this whole class of bug fell through.

    `log()` renders `[leerie] [<repo>]` from USER_REPO, falling back to cwd —
    which is /work inside the container. USER_REPO does not match `^LEERIE_`,
    so the forwarding loop cannot carry it; it needs an explicit `-e`. Without
    it every local run prints `[leerie] [work]` regardless of the repo.
    """
    pairs = _env_pairs(_run_argv_tokens())
    assert "USER_REPO" in pairs, (
        "USER_REPO is not passed to the container — log() will fall back to "
        "cwd (/work) and every line will render [leerie] [work]"
    )
    assert pairs["USER_REPO"] == "stackpulse", (
        f"expected the basename 'stackpulse', got {pairs['USER_REPO']!r} — "
        "the host path does not exist inside the container (repo is at /work), "
        "so a path value would be misleading to any future reader"
    )


def test_fly_path_also_delivers_user_repo():
    """Both runtimes must deliver the tag, by independent mechanisms.

    The Fly path has no `LEERIE_*` forwarding loop — it hand-picks keys into
    `child_env` in the detached-launch heredoc. This bug existed because the
    two mechanisms drifted: the fix landed on Fly (5f151c88) and not local.
    Pin both so a future change to one surfaces the other.
    """
    src = LAUNCHER.read_text()
    assert 'child_env["USER_REPO"] = "$(basename "$USER_REPO")"' in src, (
        "the Fly detached-launch child_env no longer injects USER_REPO — "
        "the remote orchestrator's log() will regress to [leerie] [work]"
    )


def test_worker_pids_max_forwarded():
    names = _forwarded_names(_run({"LEERIE_WORKER_PIDS_MAX": "1024"}))
    assert "LEERIE_WORKER_PIDS_MAX" in names


def test_dynamic_per_worker_var_forwarded():
    """LEERIE_MODEL_<WORKER> names are dynamic — the deny-list design must
    forward them without enumerating each one."""
    names = _forwarded_names(_run({"LEERIE_MODEL_IMPLEMENTER": "opus"}))
    assert "LEERIE_MODEL_IMPLEMENTER" in names


def test_several_orchestrator_vars_forwarded():
    names = _forwarded_names(_run({
        "LEERIE_MAX_WORKERS": "80",
        "LEERIE_SOURCE_OF_TRUTH": "codebase",
        "LEERIE_EFFORT": "high",
        "LEERIE_VERBOSITY": "stream",
    }))
    assert {"LEERIE_MAX_WORKERS", "LEERIE_SOURCE_OF_TRUTH",
            "LEERIE_EFFORT", "LEERIE_VERBOSITY"} <= names


def test_state_dir_not_forwarded_by_loop():
    """LEERIE_STATE_DIR is deny-listed — it is remapped separately to
    /leerie-state (the host value is meaningless inside the container)."""
    names = _forwarded_names(_run({"LEERIE_STATE_DIR": "/host/only/path"}))
    assert "LEERIE_STATE_DIR" not in names


def test_launcher_side_var_not_forwarded():
    """LEERIE_RUNTIME is consumed launcher-side; it must not be forwarded."""
    names = _forwarded_names(_run({"LEERIE_RUNTIME": "fly"}))
    assert "LEERIE_RUNTIME" not in names


def test_unset_var_not_forwarded():
    names = _forwarded_names(_run({}))
    assert "LEERIE_WORKER_PIDS_MAX" not in names
    assert "LEERIE_MAX_WORKERS" not in names


def test_empty_var_not_forwarded():
    """An explicitly-empty override is not forwarded (would arrive as an
    empty string that the resolver treats as unset anyway)."""
    names = _forwarded_names(_run({"LEERIE_WORKER_PIDS_MAX": ""}))
    assert "LEERIE_WORKER_PIDS_MAX" not in names


# ── coupling guard: no orchestrator override is silently deny-listed ─────────

# The four orchestrator-read LEERIE_* vars that are legitimately deny-listed
# (see the launcher's `_leerie_env_denylist` comment):
#   LEERIE_STATE_DIR / LEERIE_INSPECT_DIRS — remapped separately to
#     container-internal values.
#   LEERIE_NO_PUSH — the orchestrator is always invoked with --no-push; the
#     host does the push.
#   LEERIE_RUNTIME — the runtime is decided launcher-side before the container
#     launches; the orchestrator's read is informational.
_JUSTIFIED_DENYLISTED = {
    "LEERIE_STATE_DIR",
    "LEERIE_INSPECT_DIRS",
    "LEERIE_NO_PUSH",
    "LEERIE_RUNTIME",
}


def _orchestrator_env_vars() -> set[str]:
    """Every LEERIE_* env var the orchestrator reads (its override surface)."""
    src = (REPO_ROOT / "orchestrator" / "leerie.py").read_text()
    return set(re.findall(r'"(LEERIE_[A-Z_]+)"', src))


def _launcher_denylist() -> set[str]:
    """The LEERIE_* names in the launcher's `_leerie_env_denylist` array."""
    loop = _extract_forwarding_loop()
    denyblock = loop.split("_leerie_env_args=()")[0]
    return set(re.findall(r"(LEERIE_[A-Z_]+)", denyblock))


def test_every_orchestrator_override_reaches_the_container():
    """Coupling guard: an orchestrator-read LEERIE_* var must either reach the
    container (not deny-listed) or be one of the four justified exceptions.

    Catches a future `LEERIE_FOO` override added to the orchestrator that a
    stale deny-list would silently strand at the container boundary — the exact
    class of bug this whole change fixes.
    """
    stranded = (_orchestrator_env_vars() & _launcher_denylist()) - _JUSTIFIED_DENYLISTED
    assert not stranded, (
        "These LEERIE_* vars are read by the orchestrator but deny-listed in "
        "the launcher, so a host-set value never reaches the container:\n"
        f"  {sorted(stranded)}\n"
        "Either remove them from `_leerie_env_denylist` in `leerie` (so they "
        "forward), or add them to _JUSTIFIED_DENYLISTED here with a reason."
    )
