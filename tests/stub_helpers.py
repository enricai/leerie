"""Shared test helpers reused across tests/*.py.

Single-owner discipline (see CLAUDE.md's `launcher_blocks.py`/`ec2_stub.py`
precedent): each helper here previously existed as byte-identical copies in
every consuming test file, which is the drift risk this module exists to
remove.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY_SH = REPO_ROOT / "scripts" / "container-entry.sh"


def _entry_sh_rootful_block() -> str:
    text = ENTRY_SH.read_text()
    marker = 'if [ "$ROOTLESS" != "true" ] && getent passwd leerie'
    start = text.index(marker)
    end = text.index("\nfi", start)
    return text[start:end]


def _make_stub_timeout(stub_dir: Path) -> None:
    """No-op `timeout`: run the child, ignore the cap.

    Adequate for tests that merely need the binary to exist on the
    stubbed PATH. A test that asserts the cap actually *fires* must
    use `_stub_timeout` (below) instead.

    Six test files each defined a byte-identical `_make_stub_timeout(stub_dir)`,
    differing only in docstring wording: it writes a `timeout` shim that skips
    any leading `--flag`/duration args and execs the wrapped command, ignoring
    the time cap. Adequate for tests that merely need the `timeout` binary to
    exist on the stubbed PATH (macOS ships no `/usr/bin/timeout`; it's coreutils,
    via Homebrew). A single-owner module keeps future changes to the stub's
    semantics from silently desyncing across call sites — same precedent as
    `tests/ec2_stub.py` and `tests/launcher_blocks.py`.

    This is deliberately NOT the stub for tests that assert the cap actually
    *fires*: `_stub_timeout` (below) is a distinct, intentionally different
    killing variant for that case.
    """
    stub = stub_dir / "timeout"
    stub.write_text(
        """#!/usr/bin/env bash
while [[ "$1" == --* ]]; do shift; done
shift
exec "$@"
"""
    )
    stub.chmod(0o755)


def _stub_timeout(bin_dir: Path) -> None:
    """Real, group-killing `timeout` stub.

    These tests pin PATH to `{bin_dir}:/usr/bin:/bin` so a fake binary is
    found and the host's Homebrew layout can't leak in. But macOS ships no
    `timeout` in /usr/bin (it's coreutils, via Homebrew), so
    `_seed_timeout_prefix` correctly no-ops — and a stall test's `sleep 600`
    then runs unbounded, hanging the suite for 10 minutes rather than
    failing.

    Unlike `_make_stub_timeout` above, this one must honour the cap: the
    tests it serves assert the timeout actually *fires* (rc 124), so an
    `exec "$@"` stub that ignores the duration would hang exactly like no
    stub at all.

    Runs the child in its own process GROUP and signals the whole group on
    expiry — killing only the direct child is not enough: its grandchildren
    (a stalled stub's own `sleep`) inherit the captured stdout, and a
    `$(...)` capture blocks until every writer closes the pipe, so the
    caller would hang even though the child is dead. Real GNU timeout kills
    the group for exactly this reason.
    """
    stub = bin_dir / "timeout"
    stub.write_text(
        """#!/usr/bin/env bash
kill_after=""
while [[ "$1" == --* ]]; do
  case "$1" in
    --kill-after=*) kill_after="${1#--kill-after=}" ;;
    --kill-after)   kill_after="$2"; shift ;;
    --foreground)   ;;
  esac
  shift
done
secs="$1"; shift

# `set -m` puts the child in its own process group (pgid == its pid), so
# `kill -- -$pgid` reaches the whole subtree.
set -m
"$@" &
child=$!
set +m

(
  sleep "$secs"
  kill -TERM -- "-$child" 2>/dev/null || kill -TERM "$child" 2>/dev/null
  if [ -n "$kill_after" ]; then
    sleep "$kill_after"
    kill -KILL -- "-$child" 2>/dev/null || kill -KILL "$child" 2>/dev/null
  fi
) &
waiter=$!

wait "$child" 2>/dev/null; rc=$?
kill -TERM "$waiter" 2>/dev/null
# GNU timeout reports 124 when it had to kill the child.
[ "$rc" -eq 143 ] && rc=124
exit "$rc"
"""
    )
    stub.chmod(0o755)


def _stub_self_cmd(tmp_path: Path) -> tuple[Path, Path]:
    """Build a stub binary that records its argv to a log and exits 0.

    Used to intercept ``LEERIE_SELF_CMD`` dispatch in chain/group launcher
    tests so they don't actually shell out to a real launcher recursion.

    Returns ``(stub_path, log_path)``.
    """
    log = tmp_path / "stub-self.log"
    stub = tmp_path / "self-stub"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$@" >> "{log}"
        exit 0
        """))
    stub.chmod(0o755)
    return stub, log


def _d1_covered(n_spans: int = 1) -> dict:
    """Classifier requirement-coverage fields that leave no hole for a task
    that `_d1_split_spans` cuts into `n_spans` plain spans and that points
    at no report (DESIGN §8 *Requirement coverage*): every span `items`,
    one tree-verifiable item each. Merge into a stub classifier result so a
    test about something else does not spend check rounds on coverage."""
    ids = [f"S{i + 1}" for i in range(n_spans)]
    return {
        "span_dispositions": [{"span": i, "disposition": "items",
                               "kind": "", "reason": "asks for work"}
                              for i in ids],
        "section_roles": [],
        "required_items": [{"span": i, "item": f"requirement of {i}",
                            "parts": [], "verifiability": "tree",
                            "untouched_paths": []} for i in ids],
    }


# A delivery_judge citation code can verify (DESIGN §8 *Delivery detection*):
# `_delivery_tree` commits this line at this path, and `_judge_verdict` cites
# it, so a stubbed `met` is a VERIFIED met rather than one the screen
# rejects.
DELIVERY_CITED_FILE = "src/delivered_feature.py"
DELIVERY_CITED_LINE = "def delivered_feature(value):"


def _delivery_tree(path: Path) -> Path:
    """Make `path` a git repository (or reuse it) whose HEAD carries
    `DELIVERY_CITED_FILE`; returns `path`."""
    import subprocess
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        subprocess.run(["git", "init", "-q", str(path)], check=True)
    f = path / DELIVERY_CITED_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(DELIVERY_CITED_LINE + "\n    return value\n")
    subprocess.run(["git", "-C", str(path), "add", DELIVERY_CITED_FILE],
                   check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-q", "-m",
                    "cited file"], check=True)
    return path


def _judge_parts(met: bool) -> list[dict]:
    """One part citing the committed line — `met` or `unmet`."""
    return [{"requirement": "the delivered feature exists",
             "kind": "static", "status": "met" if met else "unmet",
             "cites": [{"path": DELIVERY_CITED_FILE, "line_start": 1,
                        "line_end": 1, "quote": DELIVERY_CITED_LINE}]}]


def _judge_verdict(index: int, met: bool, evidence: str = "e") -> dict:
    """A schema-shaped delivery_judge verdict whose citation verifies on a
    `_delivery_tree`."""
    return {"item_index": index, "met": met, "parts": _judge_parts(met),
            "evidence": evidence}
