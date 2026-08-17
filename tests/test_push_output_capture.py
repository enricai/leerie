"""`git push`'s two streams are captured separately, for two consumers.

`host_finalize` used to capture the push with `2>&1 >/dev/null` — stderr
only. git forwards a pre-push hook's stdout to git's own stdout, and `tsc`
and `biome` write their diagnostics there (jest and vitest use stderr, which
is why this went unnoticed for so long), so the hook's actual complaint was
discarded. Measured on the run that motivated this file: the recorded
`push_error` was two pnpm deprecation warnings and git's generic `failed to
push some refs`, while the 13 `TS2307` lines that explained the rejection
went to `/dev/null` — an undiagnosable failure at the end of a $57 run.

The obvious fix — plain `2>&1` — is WRONG, and half of this file exists to
keep it from being reintroduced. The captured blob is also the input to
`_host_finalize_is_auth_or_network_push_error`, whose single arm matches a
qualified phrase on a line git itself prefixes (`^fatal:` / `^remote:`). A
pre-push hook that refreshes submodules or runs `git ls-remote` prints
exactly that shape ON STDOUT, so merging the streams flips a hook failure to
"auth/network" and suppresses the `--no-verify` hint the operator needed.
Measured against the real classifier: 3 of 3 adversarial hook shapes flip.

So the streams are captured apart: stderr classifies (leaving the committed
23-case corpus score unchanged BY CONSTRUCTION, not by re-measurement), and
stdout+stderr is what the operator and `run.json` get.

Harness note: this file reuses `test_host_finalize_sh.py`'s stubbed-git
runner rather than reimplementing it, matching the convention
`test_fetch_branch_leerie_streamback.py` established for
`test_fetch_branch_sh.py`. That also inherits its `HAS_JQ` gate — see
`tests/conftest.py` for why installing jq into the leerie image is the wrong
fix for a skip here.
"""
from __future__ import annotations

import json
import re
import subprocess

import pytest

from tests.conftest import HAS_JQ
from tests.test_host_finalize_sh import _make_run, _run_host_finalize

pytestmark = pytest.mark.skipif(
    not HAS_JQ,
    reason="host-only script: needs real `jq`, which the launcher guarantees "
           "on the host and the leerie image deliberately omits",
)

# A stdout line only a compiler would print, and that no part of the
# classifier or the message scaffolding could produce on its own.
TSC_LINE = "src/lib/bedrock.ts(43,3): error TS2322: Type 'LanguageModelV4'"


def _run_with_hook(tmp_path, run_id, *, push_stdout: str, push_stderr: str,
                   state_json=None, extra_env=None):
    """Drive host_finalize with a real executable pre-push hook present and a
    git stub whose `push` writes to BOTH streams and fails.

    The hook file is real (the N24 probe is structural — it stats an
    executable `pre-push`), while `config` / `rev-parse` are stubbed to route
    the probe at it, mirroring `test_host_finalize_sh.py`'s own hook test.
    """
    run_dir = _make_run(tmp_path, run_id, run_json={
        "branch": f"leerie/runs/{run_id}",
        "working_branch": "main",
        "finished_at": "2026-05-29T16:00:00+00:00",
    }, state_json=state_json)
    user_repo = tmp_path / "user-repo"
    hooks_dir = user_repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "pre-push"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    git_body = f'''
if [ "$1" = "-C" ] && [ "$3" = "config" ]; then
  exit 1
fi
if [ "$1" = "-C" ] && [ "$3" = "rev-parse" ]; then
  echo "{hooks_dir}"
  exit 0
fi
if [ "$1" = "-C" ] && [ "$3" = "push" ] || [ "$1" = "push" ]; then
  cat <<'STDOUT_EOF'
{push_stdout}
STDOUT_EOF
  cat >&2 <<'STDERR_EOF'
{push_stderr}
STDERR_EOF
  exit 1
fi
exit 0
'''
    return run_dir, _run_host_finalize(tmp_path, run_dir, git_body=git_body,
                                      extra_env=extra_env)


# --- the defect this fixes ------------------------------------------------

def test_hook_stdout_reaches_the_operator(tmp_path):
    """The reported failure, reproduced: the cause is on stdout and the
    stderr carries only cosmetic warnings. The operator must see the cause."""
    _, r = _run_with_hook(
        tmp_path, "feat-a-aaaaaa",
        push_stdout=TSC_LINE,
        push_stderr='[WARN] The "pnpm" field in package.json is no longer read.\n'
                    "error: failed to push some refs to 'origin'",
    )
    assert r.returncode == 1
    assert TSC_LINE in r.stderr, (
        "the hook's stdout was discarded — this is the exact reported defect"
    )


def test_hook_stdout_is_persisted_to_run_json(tmp_path):
    """…and survives past the terminal, since the terminal is where the
    previous four occurrences of this class were lost."""
    run_dir, r = _run_with_hook(
        tmp_path, "feat-b-aaaaaa",
        push_stdout=TSC_LINE,
        push_stderr="error: failed to push some refs to 'origin'",
    )
    assert r.returncode == 1
    recorded = json.loads((run_dir / "run.json").read_text())["push_error"]
    assert TSC_LINE in recorded
    # stderr is not dropped in the process of adding stdout.
    assert "failed to push some refs" in recorded


def test_stdout_section_is_labelled(tmp_path):
    """Two streams in one blob need a boundary, or the reader cannot tell
    git's own words from the hook's."""
    _, r = _run_with_hook(
        tmp_path, "feat-c-aaaaaa",
        push_stdout=TSC_LINE,
        push_stderr="error: failed to push some refs to 'origin'",
    )
    assert "--- pre-push hook output (stdout) ---" in r.stderr


# --- why the one-character fix is wrong -----------------------------------

@pytest.mark.parametrize("stdout_shape", [
    # A hook that refreshes submodules.
    "fatal: could not read from remote repository.",
    # A hook that checks whether it is behind upstream.
    "fatal: unable to access 'https://github.com/acme/private.git/': "
    "The requested URL returned error: 403",
    # A hook echoing a git error it captured.
    "remote: Repository not found.",
])
def test_git_framed_hook_stdout_does_not_suppress_the_hook_hint(
        tmp_path, stdout_shape):
    """THE load-bearing test.

    Each of these is ordinary pre-push hook output that happens to carry
    git's own framing, on stdout. Merging the streams before classification
    makes `_host_finalize_is_auth_or_network_push_error` match, the
    `if … && ! …` guard fail, and the `--no-verify` hint vanish — telling the
    operator to fix credentials that are fine. Splitting the capture is what
    prevents it; reverting to `2>&1` fails this test three times.
    """
    _, r = _run_with_hook(
        tmp_path, "feat-d-aaaaaa",
        push_stdout=stdout_shape,
        push_stderr="error: failed to push some refs to 'origin'",
    )
    assert r.returncode == 1
    assert "--no-verify" in r.stderr, (
        "hook hint suppressed: git-framed text on STDOUT reached the "
        "classifier, so the streams are no longer being kept apart"
    )
    # Positive pin for the sentence the negative assertions elsewhere rely
    # on (here and in test_host_finalize_sh.py). Reword it and those turn
    # vacuously true; this fails instead.
    assert "This looks like a failing git hook" in r.stderr
    # And the operator still sees it — split, not dropped.
    assert stdout_shape.splitlines()[0] in r.stderr


def test_real_auth_failure_on_stderr_still_classifies_as_auth(tmp_path):
    """Anti-vacuity control. If the test above passed because the classifier
    stopped working altogether, this fails: a genuine credential failure —
    on stderr, where git puts it — must still suppress the hook hint, since
    `--no-verify` cannot fix credentials."""
    _, r = _run_with_hook(
        tmp_path, "feat-e-aaaaaa",
        push_stdout="running pre-push checks…",
        push_stderr="fatal: Authentication failed for "
                    "'https://github.com/o/r.git/'",
    )
    assert r.returncode == 1
    assert "This looks like a failing git hook" not in r.stderr


# --- presentation ---------------------------------------------------------

def test_every_line_of_the_push_output_is_indented(tmp_path):
    """`printf '    %s\\n' "$multi_line"` indents only the FIRST line, which
    ran git's error onto the tail of a hook's last line and read as one
    corrupted sentence in the reported failure."""
    _, r = _run_with_hook(
        tmp_path, "feat-f-aaaaaa",
        push_stdout="alpha-marker\nbeta-marker",
        push_stderr="gamma-marker\nerror: failed to push some refs to 'origin'",
    )
    for marker in ("alpha-marker", "beta-marker", "gamma-marker"):
        line = next(ln for ln in r.stderr.splitlines() if marker in ln)
        assert line.startswith("    "), f"{marker!r} not indented: {line!r}"


def test_printed_copy_is_tail_truncated_far_tighter_than_run_json(tmp_path):
    """A hook running a test suite emits megabytes (one recorded push_error
    is 104 KB). The terminal gets 4 KB of tail — where a compiler or test
    runner puts its summary — while run.json keeps a much larger 32 KB."""
    filler = "\n".join(f"noise line {i}" for i in range(2000))  # ~30 KB
    run_dir, r = _run_with_hook(
        tmp_path, "feat-g-aaaaaa",
        push_stdout=filler + "\nFINAL-TAIL-MARKER",
        push_stderr="error: failed to push some refs to 'origin'",
    )
    assert "FINAL-TAIL-MARKER" in r.stderr
    assert "truncated" in r.stderr
    assert "noise line 0" not in r.stderr, "head was printed; truncation is not tail-anchored"
    recorded = json.loads((run_dir / "run.json").read_text())["push_error"]
    # Under the 32 KB persist bound, so this payload survives whole.
    assert "noise line 0" in recorded and "FINAL-TAIL-MARKER" in recorded


def test_oversized_push_output_still_writes_the_sidecar(tmp_path):
    """The regression that matters most, because it silently defeats
    everything else in this file.

    `push_error` reaches run.json as a single `jq --arg` value, and one argv
    element cannot exceed MAX_ARG_STRLEN (131,072 bytes). One real recorded
    push_error is already 104,520 bytes on stderr alone, so folding hook
    stdout into the same value is what makes the ceiling reachable — and past
    it `jq` cannot be exec'd, which under `set -euo pipefail` aborts
    host_finalize BEFORE the diagnostic prints. The operator would lose
    exactly the output this capture exists to preserve.

    Remove the 32 KB persist bound and this fails.
    """
    huge = "\n".join(f"error TS2307: line {i} of a very long compiler report"
                     for i in range(4000))          # ~200 KB, well past 128 KB
    assert len(huge) > 131_072, "fixture no longer exceeds MAX_ARG_STRLEN"
    run_dir, r = _run_with_hook(
        tmp_path, "feat-l-aaaaaa",
        push_stdout=huge + "\nFINAL-TAIL-MARKER",
        push_stderr="error: failed to push some refs to 'origin'",
    )
    assert r.returncode == 1
    # The diagnostic still reached the operator…
    assert "git push failed" in r.stderr
    assert "--no-verify" in r.stderr
    # …and the sidecar is still valid JSON carrying the informative tail.
    recorded = json.loads((run_dir / "run.json").read_text())
    assert "FINAL-TAIL-MARKER" in recorded["push_error"]
    assert len(recorded["push_error"]) < 131_072, (
        "persisted push_error can reach MAX_ARG_STRLEN; the next jq --arg "
        "write on this path will fail to exec"
    )
    assert recorded.get("pushed_at") is None


# --- the hint's new facts -------------------------------------------------

def test_hint_states_the_hook_measured_the_working_tree(tmp_path):
    """The fact that ends the confusion. git runs pre-push against whatever
    is CHECKED OUT; leerie never checks out the run branch, so a hook that
    lints the working tree is reporting on host state."""
    _, r = _run_with_hook(
        tmp_path, "feat-h-aaaaaa",
        push_stdout=TSC_LINE,
        push_stderr="error: failed to push some refs to 'origin'",
    )
    assert "working tree" in r.stderr
    assert "NOT the" in r.stderr
    assert "leerie never checks out" in r.stderr


def test_hint_surfaces_what_passed_in_container(tmp_path):
    """The counter-evidence: the same check the hook just failed passed
    in-container, on the tree that actually holds the run's changes."""
    _, r = _run_with_hook(
        tmp_path, "feat-i-aaaaaa",
        push_stdout=TSC_LINE,
        push_stderr="error: failed to push some refs to 'origin'",
        state_json={"blt_results": {
            "h1": {"command": "npx tsc --noEmit", "passed": True},
            "h2": {"command": "npx tsc --noEmit", "passed": True},
            "h3": {"command": "pnpm run test", "passed": False},
        }},
    )
    assert "verified the integrated tree in-container" in r.stderr
    assert "npx tsc --noEmit" in r.stderr
    # Deduplicated, and a FAILING axis is never presented as verification.
    hint = next(ln for ln in r.stderr.splitlines() if "npx tsc --noEmit" in ln)
    assert hint.count("npx tsc --noEmit") == 1
    assert "pnpm run test" not in hint


def _multibyte_locale() -> str | None:
    """A locale value under which bash's `${#var}` counts CHARACTERS.

    Without one this test cannot discriminate at all, and would pass against
    the very bug it targets — see its docstring.
    """
    for loc in ("C.UTF-8", "en_US.UTF-8", "C.utf8"):
        r = subprocess.run(
            ["bash", "-c", 'v="日本"; echo ${#v}'],
            env={"PATH": "/usr/bin:/bin", "LC_ALL": loc},
            capture_output=True, text=True, check=False,
        )
        if r.stdout.strip() == "2":      # 2 characters, not 6 bytes
            return loc
    return None


def test_persist_bound_is_measured_in_bytes_not_characters(tmp_path):
    """The bound guards a BYTE ceiling, so it must be measured in bytes.

    `${#var}` counts characters under a UTF-8 locale — a 7-character Japanese
    string reports 7 and occupies 21 bytes — while `tail -c` and
    MAX_ARG_STRLEN both count bytes. A char-based guard therefore
    under-measures by up to 4x, and 32,768 four-byte characters is exactly
    131,072 bytes with the guard not firing.

    THE LOCALE IS THE WHOLE TEST. In the C locale bash's `${#var}` counts
    bytes, so a char-based and a byte-based implementation are
    indistinguishable — and `test_host_finalize_sh.py`'s runner builds a
    minimal env with no LANG/LC_ALL, i.e. exactly that locale. The first
    version of this test therefore passed against the bug (verified by
    reverting the fix: 35 passed). It must run under a multibyte locale, and
    skip loudly rather than silently prove nothing when none is available.
    """
    loc = _multibyte_locale()
    if loc is None:
        pytest.skip("no multibyte locale available; the byte-vs-character "
                    "distinction this test exists for is unobservable here")
    ch = "日"                                   # 1 character, 3 bytes
    payload = ch * 20000                        # 20,000 chars / 60,000 bytes
    assert len(payload) < 32768                 # a char-based guard stays quiet
    assert len(payload.encode()) > 32768        # a byte-based guard fires
    run_dir, r = _run_with_hook(
        tmp_path, "feat-n-aaaaaa",
        push_stdout=payload + "\nFINAL-TAIL-MARKER",
        push_stderr="error: failed to push some refs to 'origin'",
        extra_env={"LC_ALL": loc},
    )
    assert r.returncode == 1
    recorded = json.loads((run_dir / "run.json").read_text())["push_error"]
    assert len(recorded.encode()) <= 32768 + 200, (
        "persisted push_error was bounded by character count, not bytes"
    )
    assert "FINAL-TAIL-MARKER" in recorded, "truncation is not tail-anchored"


def test_short_payload_is_persisted_without_a_truncation_marker(tmp_path):
    """Anti-vacuity partner: piping every payload through `tail -c` must not
    make an untruncated one *claim* it was truncated."""
    _, r = _run_with_hook(
        tmp_path, "feat-o-aaaaaa",
        push_stdout=TSC_LINE,
        push_stderr="error: failed to push some refs to 'origin'",
    )
    run_dir = tmp_path / "user-repo" / ".leerie" / "runs" / "feat-o-aaaaaa"
    recorded = json.loads((run_dir / "run.json").read_text())["push_error"]
    assert "truncated to the last" not in recorded
    assert TSC_LINE in recorded
    assert r.returncode == 1


def test_husky_v9_banner_on_stdout_names_the_hook(tmp_path):
    """Husky is the commonest hook runner there is, and its banner is on
    STDOUT — measured: a repo with `core.hooksPath=.husky/_` runs
    `.husky/_/h`, whose line 20 is a bare `echo "husky - $n script failed
    (code $c)"` with no `>&2`. So under the historical stderr-only capture
    the "which hook" naming grep could never match it, and the hint always
    fell back to its generic default. The existing stderr-stub test in
    test_host_finalize_sh.py is why that looked covered.

    Naming only — classification stays on stderr, which the parametrized
    test above pins.
    """
    _, r = _run_with_hook(
        tmp_path, "feat-m-aaaaaa",
        push_stdout="src/x.ts(1,1): error TS2307: Cannot find module 'ai'\n"
                    "husky - pre-push script failed (code 1)",
        push_stderr="error: failed to push some refs to 'origin'",
    )
    assert r.returncode == 1
    assert "failing git hook (pre-push script failed)" in r.stderr, (
        "the naming grep is still reading stderr only, where husky v9 never "
        "writes its banner"
    )


def test_absent_state_json_does_not_break_the_hint(tmp_path):
    """`state.json` is optional on this path (the fail-open completion gate
    reaches here without one). A missing file must degrade to silence, not
    abort the hint under `set -euo pipefail`."""
    _, r = _run_with_hook(
        tmp_path, "feat-j-aaaaaa",
        push_stdout=TSC_LINE,
        push_stderr="error: failed to push some refs to 'origin'",
        state_json=None,
    )
    assert r.returncode == 1
    assert "verified the integrated tree in-container" not in r.stderr
    assert "--no-verify" in r.stderr          # the rest of the hint survives
    assert TSC_LINE in r.stderr


# --- degradation ----------------------------------------------------------

def test_degrades_to_stderr_only_when_no_temp_dir(tmp_path):
    """The split capture needs two temp files, and this file's own classifier
    documents a real full-`/tmp` incident (N30). With `mktemp` failing, the
    push must still be attempted, still fail cleanly, and still record its
    stderr — i.e. degrade to exactly the historical behaviour rather than
    losing the push."""
    # Fail only the plain-file form the capture uses. `mktemp -d` is the
    # rebase scratch worktree, several steps earlier and unrelated — failing
    # that too would abort the function before the push and make this test
    # pass for the wrong reason (verified: it did).
    stub = tmp_path / "mktemp"
    stub.write_text('#!/usr/bin/env bash\n'
                    '[ "${1:-}" = "-d" ] && exec /usr/bin/mktemp "$@"\n'
                    'exit 1\n')
    stub.chmod(0o755)
    run_dir, r = _run_with_hook(
        tmp_path, "feat-k-aaaaaa",
        push_stdout=TSC_LINE,
        push_stderr="error: failed to push some refs to 'origin'",
    )
    assert r.returncode == 1
    assert "git push failed" in r.stderr
    recorded = json.loads((run_dir / "run.json").read_text())["push_error"]
    assert "failed to push some refs" in recorded
    assert json.loads((run_dir / "run.json").read_text()).get("pushed_at") is None


# --- guard the guard ------------------------------------------------------

def test_source_no_longer_merges_the_streams_into_the_classifier():
    """Structural backstop for the parametrized test above.

    Comments in this region necessarily quote `2>&1 >/dev/null` while
    explaining it, so the scan strips comment lines first — the same trap
    the zombie-reaper guard documents in CLAUDE.md.
    """
    from tests.test_host_finalize_sh import HOST_FINALIZE_SH
    src = HOST_FINALIZE_SH.read_text()
    body = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    # The classifier is called with the stderr-only variable, never the blob.
    calls = re.findall(r"_host_finalize_is_auth_or_network_push_error \"\$(\w+)\"",
                       body)
    assert calls, "classifier call site not found — has it been renamed?"
    assert "push_all" not in calls, (
        "the combined blob is being classified; that is the regression this "
        "file exists to prevent"
    )
