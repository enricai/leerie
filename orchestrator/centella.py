#!/usr/bin/env python3
"""
Centella — deterministic task orchestrator for Claude Code.

Runs entirely on the Claude Code CLI / subscription. Every unit of LLM work is
a `claude -p` headless invocation. This script owns ALL control flow — phase
sequencing, wave scheduling, caps, retries, integration — in real Python, so
the orchestration cannot drift the way an LLM-driven controller can.

Each worker is a separate `claude -p` process, so there is no subagent nesting
anywhere. The script is the orchestrator; each `claude -p` call is a leaf.

Usage:
    centella "<task description>"
    centella --resume
    centella "<task>" --answers answers.json
    centella "<task>" --no-clarify          # skip the clarification phase

Run it from the root of the target git repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent       # centella plugin/repo root
PROMPTS = ROOT / "prompts"
SCRIPTS = ROOT / "scripts"

# --- tunable caps --------------------------------------------------------
DEFAULT_CAPS = {
    "max_total_workers": 40,        # hard ceiling on claude -p invocations
    "max_parallel": 4,              # concurrent workers within a wave
    "handoff_continuations": 3,     # fresh-context continuations per subtask
    "failed_retries": 1,            # re-spawns of a failed implementer
    "wave_revalidation_rounds": 5,  # staging re-validation attempts per wave
    "worker_timeout_sec": 5400,     # 90 minutes per worker process
}

CATEGORIES = [
    "feature-implementation", "bug-fixing", "refactoring",
    "performance-optimization", "testing", "dependency-migration",
    "configuration-build", "documentation",
]

READ_TOOLS = "Read,Grep,Glob,WebSearch,WebFetch"
ACT_TOOLS = "Read,Grep,Glob,WebSearch,WebFetch,Bash,Write,Edit"

EXIT_NEEDS_ANSWERS = 10   # emitted when clarification is needed but no TTY

VALIDATOR_SYSTEM = (
    "You verify whether an integrated set of changes satisfies a list of "
    "frozen success-criteria files. You run the criteria — execute the tests "
    "they describe, perform the documented checks — against the current "
    "working directory. You do not modify code. Your final result is delivered "
    "as structured output conforming to the JSON schema you were given: for "
    "each subtask, its id, whether all its criteria were met, and a list of "
    "any failing criteria with the reason each failed."
)

# --- worker output schemas -----------------------------------------------
# Passed to `claude -p` via --json-schema. The CLI validates the worker's
# final output against the schema AFTER the run and exposes the validated
# object as `structured_output` in the JSON envelope. NOTE: --json-schema
# only accepts an INLINE schema string; a file path is silently ignored
# (verified against Claude Code 2.1.143), so these are embedded here.
SCHEMAS: dict[str, dict] = {
    "classifier": {
        "type": "object",
        "required": ["categories"],
        "properties": {
            "categories": {"type": "array", "items": {"type": "string"}},
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "question"],
                    "properties": {
                        "id": {"type": "string"},
                        "question": {"type": "string"},
                        "why_underivable": {"type": "string"},
                    },
                },
            },
            "source_of_truth_question": {"type": "boolean"},
        },
    },
    "planner": {
        "type": "object",
        "required": ["domain", "subtasks"],
        "properties": {
            "domain": {"type": "string"},
            "source_of_truth": {"type": "string"},
            "patterns_observed": {"type": "array", "items": {"type": "string"}},
            "notes_for_orchestrator": {"type": "string"},
            "subtasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "title", "success_criteria_seed"],
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "intent": {"type": "string"},
                        "scope_note": {"type": "string"},
                        "files_likely_touched": {
                            "type": "array", "items": {"type": "string"}},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "requires": {"type": "array", "items": {"type": "string"}},
                        "provides": {"type": "array", "items": {"type": "string"}},
                        "success_criteria_seed": {"type": "string"},
                        "size": {"type": "string"},
                        "investigation_notes": {"type": "string"},
                    },
                },
            },
        },
    },
    "implementer": {
        "type": "object",
        "required": ["subtask_id", "status"],
        "properties": {
            "subtask_id": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["complete", "incomplete-handoff", "blocked", "failed"],
            },
            "branch": {"type": "string"},
            "criteria_results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion": {"type": "string"},
                        "met": {"type": "boolean"},
                        "evidence": {"type": "string"},
                    },
                },
            },
            "confidence": {
                "type": "object",
                "properties": {
                    "root_cause": {"type": "number"},
                    "solution": {"type": "number"},
                    "basis": {"type": "string"},
                },
            },
            "checkpoint_path": {"type": ["string", "null"]},
            "proposed_criteria_revision": {"type": ["string", "object", "null"]},
            "blocker": {"type": ["string", "null"]},
            "summary": {"type": "string"},
        },
    },
    "integrator": {
        "type": "object",
        "required": ["incoming_subtask", "status"],
        "properties": {
            "incoming_subtask": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["resolved", "design-conflict", "failed"],
            },
            "revalidation": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "subtask_id": {"type": "string"},
                        "all_criteria_met": {"type": "boolean"},
                        "notes": {"type": "string"},
                    },
                },
            },
            "resolution_summary": {"type": "string"},
            "diagnosis": {"type": ["string", "null"]},
        },
    },
    "validator": {
        "type": "object",
        "required": ["results"],
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["subtask_id", "all_criteria_met"],
                    "properties": {
                        "subtask_id": {"type": "string"},
                        "all_criteria_met": {"type": "boolean"},
                        "failing": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    },
}


# =========================================================================
# small utilities
# =========================================================================
def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(f"[centella {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def die(msg: str, code: int = 1):
    print(f"centella: error: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def run_script(name: str, *args: str) -> subprocess.CompletedProcess:
    """Run one of the bundled git worktree scripts in the target repo."""
    return subprocess.run(
        ["bash", str(SCRIPTS / name), *args],
        cwd=os.getcwd(), capture_output=True, text=True,
    )


# =========================================================================
# deterministic enforcement — no LLM involvement
#
# Prompts are advisory; code enforces. Every rule that can be checked
# mechanically lives here, not in a prompt. The LLM handles: understanding
# intent, writing code, decomposing tasks, resolving semantic conflicts.
# Code handles: counting, hashing, graph invariants, file existence, running
# test suites, enforcing structural rules.
# =========================================================================

def preflight(centella_dir: Path, skip_smoke: bool = False) -> None:
    """Hard checks before any LLM work. Fails fast rather than wasting workers."""

    # 1. git user identity — missing config causes implementer commits to fail
    for key in ("user.email", "user.name"):
        r = subprocess.run(["git", "config", key],
                           capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            die(f"git {key} is not configured. "
                f"Run: git config --global {key} \"<value>\"")

    # 2. working tree must be clean — a dirty tree produces ambiguous diffs
    r = subprocess.run(["git", "status", "--porcelain"],
                       capture_output=True, text=True)
    dirty = [l for l in r.stdout.splitlines() if not l.startswith("??")]
    if dirty:
        die(f"working tree has {len(dirty)} modified/staged file(s). "
            "Commit or stash before running centella.")

    # 3. no stale centella/* branches — they collide with this run's names
    r = subprocess.run(["git", "branch", "--list", "centella/*"],
                       capture_output=True, text=True)
    stale_branches = [b.strip() for b in r.stdout.strip().splitlines() if b.strip()]
    if stale_branches:
        die(f"stale centella/* branches exist: {', '.join(stale_branches)}\n"
            "Run scripts/cleanup.sh --branches to remove them first.")

    # 4. no stale worktrees
    wt_dir = centella_dir / "worktrees"
    if wt_dir.exists() and any(wt_dir.iterdir()):
        die(f"stale worktrees exist at {wt_dir}\n"
            "Run scripts/cleanup.sh to remove them first.")

    # 5. live smoke-test: auth + --output-format json + --json-schema inline
    #    Catches auth failures and version mismatches before a 40-worker run starts.
    if not skip_smoke:
        log("preflight: smoke-testing claude -p…")
        try:
            proc = subprocess.run(
                ["claude", "-p", "respond with the single word ok",
                 "--output-format", "json",
                 "--json-schema", '{"type":"object"}',
                 "--max-turns", "1"],
                capture_output=True, text=True, timeout=90,
            )
            if proc.returncode != 0:
                die(f"claude -p smoke test failed (exit {proc.returncode}):\n"
                    f"{proc.stderr or proc.stdout}")
            outer = json.loads(proc.stdout)
            if outer.get("is_error"):
                die(f"claude -p smoke test returned an error: "
                    f"{outer.get('api_error_status') or outer.get('result')}")
        except subprocess.TimeoutExpired:
            die("claude -p smoke test timed out — auth issue or network problem")
        except json.JSONDecodeError:
            die("claude -p smoke test returned non-JSON — "
                "check your Claude Code version and login status")
        log("preflight: ok")


_ID_PREFIXES = frozenset({
    "bugfix-", "feat-", "refactor-", "perf-",
    "test-", "deps-", "config-", "docs-",
})


def validate_plan(subtasks: dict) -> None:
    """Structural validation of the merged plan — pure Python set operations."""
    errors: list[str] = []

    # duplicate ids cause silent filesystem collisions
    seen: set[str] = set()
    for sid in subtasks:
        if sid in seen:
            errors.append(f"duplicate subtask id: {sid!r}")
        seen.add(sid)

    # all provides tags across every subtask — used for requires resolution
    all_provides: set[str] = set()
    for s in subtasks.values():
        all_provides.update(s.get("provides", []))

    all_ids = set(subtasks.keys())
    for sid, s in subtasks.items():
        if not any(sid.startswith(p) for p in _ID_PREFIXES):
            errors.append(f"{sid}: id must start with one of "
                          f"{sorted(_ID_PREFIXES)} — cross-domain collisions "
                          "and audit-trail ambiguity otherwise")
        if s.get("size", "").lower() == "large":
            errors.append(f"{sid}: size='large' — planner must split it further")
        if not (s.get("success_criteria_seed") or "").strip():
            errors.append(f"{sid}: success_criteria_seed is empty — "
                          "implementer has no starting point for criteria")
        for dep in s.get("depends_on", []):
            if dep not in all_ids:
                errors.append(f"{sid}: depends_on '{dep}' which does not exist "
                              "— scheduler will silently drop this edge")
        for cap in s.get("requires", []):
            if cap not in all_provides:
                errors.append(f"{sid}: requires '{cap}' but nothing provides it — "
                              "dependency is unresolvable and will be silently dropped")

    if errors:
        bullet = "\n".join(f"  • {e}" for e in errors)
        die(f"plan validation failed ({len(errors)} issue(s)):\n{bullet}")
    log(f"plan validation: {len(subtasks)} subtasks ok")


def detect_test_runner() -> list[str] | None:
    """Scan cwd for a known deterministic test harness.
    Returns the command as a list, or None if nothing is recognisable."""
    cwd = Path.cwd()

    # Python: pytest
    pt = cwd / "pyproject.toml"
    if (cwd / "pytest.ini").exists() or (cwd / "setup.cfg").exists():
        return ["python", "-m", "pytest", "--tb=short", "-q"]
    if pt.exists() and "pytest" in pt.read_text():
        return ["python", "-m", "pytest", "--tb=short", "-q"]

    # JavaScript / TypeScript: npm test
    pkg = cwd / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text()).get("scripts", {})
            if "test" in scripts:
                return ["npm", "test"]
        except (json.JSONDecodeError, OSError):
            pass

    # Go
    if (cwd / "go.mod").exists():
        return ["go", "test", "./..."]

    # Rust
    if (cwd / "Cargo.toml").exists():
        return ["cargo", "test", "--quiet"]

    # Make: test target
    mk = cwd / "Makefile"
    if mk.exists():
        content = mk.read_text()
        if "\ntest:" in content or content.startswith("test:"):
            return ["make", "test"]

    return None


# --- criteria locking --------------------------------------------------------

def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def lock_criteria(sid: str, centella_dir: Path, st: State) -> None:
    """Hash the criteria file and store it — called after first write.
    Idempotent: does nothing if no file exists or a lock is already stored."""
    path = centella_dir / "criteria" / f"{sid}.md"
    if not path.exists():
        return
    with st._lock:
        locks = st.data.setdefault("criteria_locks", {})
        if sid not in locks:
            locks[sid] = _hash_file(path)
            st.save()


def verify_criteria_lock(sid: str, centella_dir: Path, st: State) -> None:
    """Raise WorkerError if the criteria file changed after locking.
    A changed hash means the implementer silently lowered its own bar."""
    path = centella_dir / "criteria" / f"{sid}.md"
    locks = st.data.get("criteria_locks", {})
    if sid not in locks or not path.exists():
        return
    current = _hash_file(path)
    if current != locks[sid]:
        raise WorkerError(
            f"{sid}: criteria file was modified after being locked "
            f"(stored={locks[sid][:8]}, current={current[:8]}). "
            "Implementer may have lowered its own bar — escalating.")


# --- checkpoint validation ---------------------------------------------------

_CHECKPOINT_SECTIONS = [
    "## Frozen success criteria",
    "## Current status",
    "## Files touched",
    "## Decisions made",
    "## Evidence gate status",
    "## Next action",
    "## Open unknowns",
]


def validate_checkpoint(path: str) -> str | None:
    """Return an error description if the checkpoint is structurally incomplete,
    None if it looks good. A missing section produces a confused successor."""
    p = Path(path)
    if not p.exists():
        return f"checkpoint file does not exist: {path}"
    content = p.read_text()
    missing = [s for s in _CHECKPOINT_SECTIONS if s not in content]
    if missing:
        return (f"missing {len(missing)} required section(s): "
                f"{', '.join(missing)}")
    return None


# --- result cross-field validation -------------------------------------------

def validate_result(result: dict,
                    centella_dir: Path | None = None) -> str | None:
    """Cross-field invariant checks that JSON Schema cannot express.
    Returns an error string if the result is self-contradictory, None if ok."""
    status = result.get("status")
    if status == "complete":
        cr = result.get("criteria_results") or []
        if not cr:
            return ("status='complete' but criteria_results is empty — "
                    "no verification evidence provided")
        failing = [c.get("criterion", "?") for c in cr
                   if not c.get("met", False)]
        if failing:
            n = len(failing)
            sample = failing[:3]
            return (f"status='complete' but {n} criterion/criteria unmet: "
                    f"{sample}{'…' if n > 3 else ''}")
        # criteria file must exist — a missing file means criteria_results
        # was fabricated without ever writing the lock-able file
        if centella_dir is not None:
            sid = result.get("subtask_id")
            if sid:
                cf = centella_dir / "criteria" / f"{sid}.md"
                if not cf.exists():
                    return (f"status='complete' but criteria file does not exist: "
                            f"{cf} — criteria_results may have been fabricated")
    elif status == "incomplete-handoff":
        cp = result.get("checkpoint_path")
        if not cp:
            return "status='incomplete-handoff' but checkpoint_path is null"
        if not Path(cp).exists():
            return f"checkpoint_path '{cp}' does not exist on disk"
    elif status == "blocked":
        if not (result.get("blocker") or "").strip():
            return "status='blocked' but blocker field is empty"
    return None


# --- post-implementation diff scope check ------------------------------------

def check_diff_scope(sid: str, worktree: str, subtask: dict,
                     st: State) -> str | None:
    """Check the implementer's diff for violations.
    Returns a fatal error string if protected paths were touched.
    Logs a non-fatal warning for unexpected scope. Returns None when clean."""
    r = subprocess.run(
        ["git", "diff", "--name-only", "centella/staging..HEAD"],
        cwd=worktree, capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    touched = [f for f in r.stdout.strip().splitlines() if f]
    if not touched:
        return None

    # fatal: any changes to protected meta-directories are out of bounds
    _PROTECTED = (".centella/", ".git/", ".claude/")
    protected = [f for f in touched
                 if any(f.startswith(p) for p in _PROTECTED)]
    if protected:
        return (f"{sid}: diff touches protected path(s): {protected} — "
                "implementers must not modify meta-directories")

    # non-fatal: log a warning for radically unexpected scope
    expected = subtask.get("files_likely_touched", [])
    over_ratio = bool(expected) and len(touched) > max(len(expected) * 3, 5)
    over_volume = len(touched) > 15
    if over_ratio or over_volume:
        reason = f"touched {len(touched)} files, expected ~{len(expected)}"
        log(f"  ⚠  scope warning {sid}: {reason}")
        with st._lock:
            st.data.setdefault("scope_warnings", {})[sid] = {
                "touched": touched, "expected": expected, "reason": reason,
            }
            st.save()

    return None


# --- post-integrator commit check --------------------------------------------

def check_merge_committed(staging: Path) -> str | None:
    """Return an error if the staging worktree is still mid-merge.

    An integrator that returns status 'resolved' must have completed the merge
    commit. If `MERGE_HEAD` still exists, the merge was never concluded — the
    worker claimed success while leaving the worktree in a broken mid-merge
    state. This is the integrator-side analogue of `check_branch_has_commits`:
    it catches a worker lying about having finished."""
    r = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        cwd=str(staging), capture_output=True, text=True,
    )
    if r.returncode == 0:
        return ("the staging worktree is still mid-merge (MERGE_HEAD exists) — "
                "the integrator did not complete the merge commit")
    # also reject a worktree left with staged-but-uncommitted conflict edits
    s = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=str(staging), capture_output=True, text=True,
    )
    if s.returncode == 0 and s.stdout.strip():
        return ("the staging worktree has staged but uncommitted changes — "
                "the integrator did not complete the merge commit")
    return None


def check_integrator_commit(staging: Path) -> str | None:
    """Return an error if the integrator's merge commit touched .centella/ files.
    The integrator should only touch project files, never coordination artifacts."""
    r = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=str(staging), capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    bad = [f for f in r.stdout.strip().splitlines()
           if f and f.startswith(".centella/")]
    if bad:
        return f"integrator commit touched coordination files: {bad}"
    return None


# --- branch-has-commits verification -----------------------------------------

def check_branch_has_commits(sid: str, worktree: str) -> str | None:
    """Return error if the implementer's branch has no commits ahead of staging.
    An empty diff means the worker produced schema-valid JSON claiming success
    while doing nothing — a silent no-op that wastes an integration attempt."""
    if not Path(worktree).exists():
        return None  # worktree gone — can't determine, don't block
    try:
        r = subprocess.run(
            ["git", "log", "centella/staging..HEAD", "--oneline"],
            cwd=worktree, capture_output=True, text=True,
        )
    except OSError:
        return None
    if r.returncode != 0:
        return None
    if not r.stdout.strip():
        return (f"branch centella/{sid} has no commits ahead of staging — "
                "implementer claimed complete without making any changes")
    return None


# --- conflict marker scan post-integration -----------------------------------

def scan_conflict_markers(staging: Path) -> str | None:
    """Return error if unresolved conflict markers remain in the staging tree.
    git grep exit 0 = matches found (bad); exit 1 = clean (good)."""
    if not staging.exists():
        return None
    try:
        r = subprocess.run(
            ["git", "grep", "-l", "^<<<<<<< ", "HEAD"],
            cwd=str(staging), capture_output=True, text=True,
        )
    except OSError:
        return None
    if r.returncode == 0:
        files = [f for f in r.stdout.strip().splitlines() if f]
        sample = files[:5]
        tail = "…" if len(files) > 5 else ""
        return (f"conflict markers in {len(files)} file(s) after integration: "
                f"{sample}{tail}")
    return None


# --- pre-validator criteria existence check ----------------------------------

def check_criteria_files_exist(wave: list[str],
                                centella_dir: Path) -> str | None:
    """Return error if any subtask in the wave is missing its criteria file.
    A missing file means validation would fail with no useful diagnosis —
    catch it before spending a worker invocation."""
    missing = [sid for sid in wave
               if not (centella_dir / "criteria" / f"{sid}.md").exists()]
    if missing:
        return (f"criteria files missing for: {', '.join(missing)} — "
                "validation cannot proceed without them")
    return None


# --- resume state integrity check --------------------------------------------

def validate_resume_state(data: dict) -> None:
    """Assert the structure of a loaded state.json before resuming. A corrupt
    or hand-edited file produces wrong behavior; fail fast rather than run
    silently. Only `task` is strictly required here — a run interrupted before
    scheduling has no `waves` yet, and main() handles that case separately with
    a clearer message."""
    if "task" not in data or not str(data.get("task", "")).strip():
        die("state.json has no usable 'task' — cannot resume. "
            "Inspect .centella/state.json manually.")

    # waves is optional (absent if interrupted before scheduling); if present
    # it must be well-formed, and completed_waves must be in range.
    if "waves" in data:
        waves = data["waves"]
        if not isinstance(waves, list) or not all(isinstance(w, list)
                                                  for w in waves):
            die("state.json: 'waves' must be a list of lists")
        completed = data.get("completed_waves", 0)
        if not isinstance(completed, int) or not (0 <= completed <= len(waves)):
            die(f"state.json: 'completed_waves' ({completed!r}) is out of range "
                f"(expected 0..{len(waves)})")

    # subtask_status, if present, must be a dict
    if "subtask_status" in data and not isinstance(data["subtask_status"], dict):
        die("state.json: 'subtask_status' must be an object")


# =========================================================================
# the single point where LLM work happens: a `claude -p` invocation
# =========================================================================
class WorkerError(RuntimeError):
    pass


def _invoke(cmd: list[str], cwd: str, timeout: int) -> dict:
    """Run a `claude -p` command once; return the parsed JSON envelope."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise WorkerError(f"worker timed out after {timeout}s")
    if proc.returncode != 0:
        raise WorkerError((proc.stderr or proc.stdout or "claude -p failed").strip())
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise WorkerError("claude -p did not return a valid JSON envelope")


def claude_p(user_prompt: str, system_prompt: str, *, schema_key: str,
             cwd: str, allowed_tools: str, max_turns: int, autonomous: bool,
             caps: dict, st: "State", model: str | None = None) -> dict:
    """Run one headless Claude Code worker and return its validated
    structured output.

    The worker's result is constrained with `--json-schema` (inline — a file
    path is silently ignored by the CLI). The CLI validates the worker's final
    output against the schema and exposes it as `structured_output` in the
    envelope. If that field is missing or the run reports an error, the worker
    is retried once with the failure noted, then declared failed.

    `autonomous` workers skip permission prompts (they act on files inside an
    isolated worktree); non-autonomous workers get only read tools.
    """
    schema = json.dumps(SCHEMAS[schema_key], separators=(",", ":"))

    def build(extra_user: str = "") -> list[str]:
        cmd = [
            "claude", "-p", user_prompt + extra_user,
            "--append-system-prompt", system_prompt,
            "--output-format", "json",
            "--json-schema", schema,
            "--allowedTools", allowed_tools,
            "--max-turns", str(max_turns),
        ]
        if autonomous:
            # acting workers run inside an isolated worktree; skipping prompts
            # is what makes the run unattended. Blast radius is the worktree.
            cmd.append("--dangerously-skip-permissions")
        if model:
            cmd += ["--model", model]
        return cmd

    timeout = caps["worker_timeout_sec"]
    last_problem = ""
    for attempt in (1, 2):
        retry_note = ("" if attempt == 1 else
                      f"\n\nYOUR PREVIOUS ATTEMPT FAILED: {last_problem} "
                      "Return output that conforms exactly to the required schema.")
        envelope = _invoke(build(retry_note), cwd, timeout)

        # record run-weight telemetry
        st.add_telemetry(envelope)

        # surface non-clean exits — a worker that hit --max-turns exits 0 and
        # can still produce structured_output, but stopped mid-work
        term = envelope.get("terminal_reason", "")
        turns = envelope.get("num_turns", -1)
        if term and term != "completed":
            log(f"  ⚠  worker exited with terminal_reason='{term}' "
                f"(num_turns={turns}) — output may be incomplete")

        if envelope.get("is_error"):
            last_problem = str(envelope.get("api_error_status")
                               or envelope.get("result") or "worker reported an error")
            continue
        structured = envelope.get("structured_output")
        if structured is None:
            last_problem = ("the run produced no structured_output — the final "
                            "output did not satisfy the JSON schema")
            continue
        return structured

    raise WorkerError(f"worker failed schema-valid output twice: {last_problem}")


# =========================================================================
# run state — persisted so a run is observable and resumable
# =========================================================================
class State:
    def __init__(self, centella_dir: Path):
        self.path = centella_dir / "state.json"
        self.data: dict = {}
        # RLock (reentrant) so callers that already hold the lock can call
        # save() without deadlocking.
        self._lock = __import__("threading").RLock()

    def load(self) -> bool:
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            return True
        return False

    def save(self) -> None:
        """Atomic write via temp-file rename — safe to call from any thread."""
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2))
            tmp.replace(self.path)   # atomic on POSIX; best-effort on Windows

    def update(self, key: str, value) -> None:
        """Thread-safe single-key update + save."""
        with self._lock:
            self.data[key] = value
            self.save()

    def bump_workers(self, caps: dict) -> None:
        with self._lock:
            self.data["worker_count"] = self.data.get("worker_count", 0) + 1
            count = self.data["worker_count"]
            self.save()
        if count > caps["max_total_workers"]:
            raise WorkerError(
                f"worker budget exhausted ({caps['max_total_workers']}). "
                "State saved; re-run with --resume after raising --max-workers."
            )

    def add_telemetry(self, envelope: dict) -> None:
        """Accumulate run-weight signals from a worker envelope. On a
        subscription the dollar figure is not billed, but it and the token
        counts are a useful proxy for how heavy the run is."""
        with self._lock:
            t = self.data.setdefault("telemetry", {"calls": 0, "cost_usd": 0.0,
                                                   "input_tokens": 0,
                                                   "output_tokens": 0})
            t["calls"] += 1
            t["cost_usd"] += float(envelope.get("total_cost_usd") or 0.0)
            usage = envelope.get("usage") or {}
            t["input_tokens"] += int(usage.get("input_tokens") or 0)
            t["output_tokens"] += int(usage.get("output_tokens") or 0)
            self.save()


# =========================================================================
# phases
# =========================================================================
def phase_classify(task: str, st: State, caps: dict, no_clarify: bool) -> dict:
    """Phase 1 (classify), which also produces the Phase 0 clarification
    questions: classify the task and surface only genuinely underivable
    (intent-level) questions."""
    log("phase 1: classifying task")
    sys_prompt = (PROMPTS / "classifier.md").read_text()
    st.bump_workers(caps)
    result = claude_p(
        user_prompt=f"TASK:\n{task}\n\nClassify it and apply the clarification filter.",
        system_prompt=sys_prompt, schema_key="classifier", cwd=os.getcwd(),
        allowed_tools=READ_TOOLS, max_turns=20, autonomous=False,
        caps=caps, st=st,
    )
    cats = [c for c in result.get("categories", []) if c in CATEGORIES]
    if not cats:
        die("classifier returned no recognized categories")
    questions = [] if no_clarify else result.get("questions", [])
    st.data["categories"] = cats
    st.data["classifier_questions"] = questions
    st.data["needs_source_of_truth"] = bool(result.get("source_of_truth_question"))
    st.save()
    log(f"categories: {', '.join(cats)}")
    return result


def gather_answers(st: State, supplied: dict | None) -> dict:
    """Collect clarification answers — from --answers, from a TTY prompt, or
    (no TTY, no answers) defer by writing pending-questions.json and exiting."""
    questions = st.data.get("classifier_questions", [])
    need_sot = st.data.get("needs_source_of_truth", False)
    answers: dict = dict(supplied or {})

    pending = [q for q in questions if q.get("id") not in answers]
    sot_missing = need_sot and "source_of_truth" not in answers

    if not pending and not sot_missing:
        st.data["answers"] = answers
        st.save()
        return answers

    if not sys.stdin.isatty():
        # launched non-interactively (e.g. via the plugin skill): defer.
        centella_dir = st.path.parent
        (centella_dir / "pending-questions.json").write_text(json.dumps({
            "questions": pending,
            "source_of_truth": sot_missing,
        }, indent=2))
        log("clarification needed; wrote .centella/pending-questions.json")
        sys.exit(EXIT_NEEDS_ANSWERS)

    for q in pending:
        print(f"\n? {q['question']}")
        if q.get("why_underivable"):
            print(f"  (underivable: {q['why_underivable']})")
        answers[q["id"]] = input("  > ").strip()
    if sot_missing:
        print("\n? Build feature work from existing codebase patterns, "
              "or from researched best-practice standards?")
        choice = input("  [patterns/research] > ").strip().lower()
        answers["source_of_truth"] = (
            "researched-standards" if choice.startswith("r") else "existing-patterns")

    st.data["answers"] = answers
    st.save()
    return answers


def phase_plan(task: str, st: State, caps: dict) -> list[dict]:
    """Phase 2: one planner per category, run in parallel. Each returns a
    JSON plan of granular subtasks."""
    log("phase 2: planning")
    cats = st.data["categories"]
    answers = st.data.get("answers", {})
    sot = answers.get("source_of_truth", "existing-patterns")
    sys_prompt = (PROMPTS / "planner.md").read_text()
    ctx = json.dumps({"task": task, "source_of_truth": sot,
                      "clarification_answers": answers}, indent=2)

    def plan_one(category: str) -> dict:
        st.bump_workers(caps)
        up = (f"DOMAIN: {category}\n\nCONTEXT:\n{ctx}\n\n"
              f"Decompose the {category} aspect of this task into a JSON plan "
              "per your instructions.")
        return claude_p(user_prompt=up, system_prompt=sys_prompt,
                        schema_key="planner", cwd=os.getcwd(),
                        allowed_tools=READ_TOOLS, max_turns=40,
                        autonomous=False, caps=caps, st=st)

    plans = []
    with ThreadPoolExecutor(max_workers=caps["max_parallel"]) as ex:
        for category, plan in zip(cats, ex.map(plan_one, cats)):
            n = len(plan.get("subtasks", []))
            log(f"  {category}: {n} subtask(s)")
            plans.append(plan)
    return plans


def schedule(plans: list[dict]) -> tuple[dict, list[list[str]]]:
    """Phase 3 (pure Python): merge plans, resolve intra- and cross-domain
    dependencies, topologically sort into waves. Deterministic."""
    log("phase 3: scheduling")
    subtasks: dict[str, dict] = {}
    for plan in plans:
        for s in plan.get("subtasks", []):
            subtasks[s["id"]] = s
    if not subtasks:
        die("planners produced no subtasks")

    # provides -> [subtask ids] for cross-domain edge resolution
    providers: dict[str, list[str]] = {}
    for sid, s in subtasks.items():
        for cap in s.get("provides", []):
            providers.setdefault(cap, []).append(sid)

    # build edges: predecessors of each subtask
    preds: dict[str, set[str]] = {sid: set() for sid in subtasks}
    for sid, s in subtasks.items():
        for dep in s.get("depends_on", []):
            if dep in subtasks:
                preds[sid].add(dep)
        for cap in s.get("requires", []):
            for provider in providers.get(cap, []):
                if provider != sid:
                    preds[sid].add(provider)

    # Kahn's algorithm -> waves
    waves: list[list[str]] = []
    done: set[str] = set()
    remaining = set(subtasks)
    while remaining:
        wave = sorted(sid for sid in remaining if preds[sid] <= done)
        if not wave:
            cyc = ", ".join(sorted(remaining))
            die(f"dependency cycle among subtasks: {cyc}")
        waves.append(wave)
        done |= set(wave)
        remaining -= set(wave)

    log(f"  {len(subtasks)} subtasks across {len(waves)} wave(s)")
    return subtasks, waves


def write_plan(centella_dir: Path, task: str, st: State,
               subtasks: dict, waves: list[list[str]]) -> None:
    """Persist the merged plan and per-subtask spec files the implementers read."""
    answers = st.data.get("answers", {})
    sot = answers.get("source_of_truth", "existing-patterns")
    (centella_dir / "plan.json").write_text(json.dumps(
        {"task": task, "waves": waves, "subtasks": subtasks}, indent=2))
    sub_dir = centella_dir / "subtasks"
    for sid, s in subtasks.items():
        spec = dict(s)
        spec["_task"] = task
        spec["_source_of_truth"] = sot
        spec["_clarification_answers"] = answers
        (sub_dir / f"{sid}.json").write_text(json.dumps(spec, indent=2))
    st.data["waves"] = waves
    st.data["completed_waves"] = st.data.get("completed_waves", 0)
    st.data["subtask_status"] = st.data.get("subtask_status", {})
    st.save()


def run_implementer(sid: str, centella_dir: Path, caps: dict, st: State,
                    continuation: bool = False, note: str = "") -> dict:
    """Spawn one implementer for one subtask in its own worktree. Handles
    handoff continuations up to the cap."""
    sys_prompt = (PROMPTS / "implementer.md").read_text()
    proc = run_script("new-worktree.sh", sid)
    if proc.returncode != 0:
        raise WorkerError(f"worktree creation failed for {sid}: {proc.stderr.strip()}")
    worktree = proc.stdout.strip().splitlines()[-1]

    up = [f"Execute subtask `{sid}`.",
          f"CENTELLA_DIR is {centella_dir} (absolute).",
          f"Read your spec at {centella_dir}/subtasks/{sid}.json.",
          "Your current working directory IS your isolated worktree — make and "
          "commit all code changes here."]
    if continuation:
        up.append(f"This is a CONTINUATION. Read the checkpoint at "
                  f"{centella_dir}/checkpoints/{sid}.md, validate it against the "
                  f"actual repo state, then continue.")
    if note:
        up.append(f"NOTE FROM ORCHESTRATOR: {note}")

    st.bump_workers(caps)
    try:
        return claude_p(user_prompt="\n".join(up), system_prompt=sys_prompt,
                        schema_key="implementer", cwd=worktree,
                        allowed_tools=ACT_TOOLS, max_turns=120,
                        autonomous=True, caps=caps, st=st)
    except WorkerError as e:
        # worker could not return schema-valid output even after a retry
        # (e.g. it hit --max-turns mid-task) -> treat as a handoff so a fresh
        # implementer can continue from whatever checkpoint exists.
        return {"subtask_id": sid, "status": "incomplete-handoff",
                "checkpoint_path": str(centella_dir / "checkpoints" / f"{sid}.md"),
                "summary": f"worker produced no schema-valid result: {e}"}


def _retryable_failure(reason: str) -> bool:
    """The retry policy, in one place.

    A failure is retried only if a corrective note to a fresh worker can
    plausibly fix it — e.g. "you forgot to commit" or "your worktree was
    dirty." A failure that means the worker is broken or dishonest is NOT
    retried: re-running it burns a worker invocation against the budget for no
    expected gain, and (for a bad-handoff case) a cold restart discards the
    partial work the checkpoint pointed at.

    Retryable (corrective note can fix it):
      - branch had no commits ahead of staging
      - worktree left dirty (uncommitted changes)

    Terminal (worker is broken/dishonest — terminate immediately, no retry):
      - cross-field invariant violation (worker lied about its own status)
      - diff touched a protected path (.centella/, .git/, .claude/)
      - any worker-level error surfaced as a failure
    """
    retryable_markers = ("no commits ahead of staging",
                         "uncommitted change")
    return any(m in reason for m in retryable_markers)


def settle_subtask(sid: str, centella_dir: Path, caps: dict, st: State) -> dict:
    """Drive one subtask to a terminal state.

    Two bounded escalation paths, both code-enforced:
      - handoff continuations (cap: caps['handoff_continuations'])
      - corrective retries of a retryable failure (cap: caps['failed_retries'])

    A non-retryable failure (see `_retryable_failure`) terminates the subtask
    immediately with status 'failed' — no retry is attempted. Returns the final
    result."""
    continuations = 0
    retries = 0
    note = ""
    continuation = False
    worktree = str(centella_dir / "worktrees" / sid)
    subtask_path = centella_dir / "subtasks" / f"{sid}.json"
    subtask = json.loads(subtask_path.read_text()) if subtask_path.exists() else {}

    def fail(reason: str) -> dict | None:
        """Record a failed attempt. Returns a terminal result dict if the
        subtask is done (non-retryable, or retry cap exhausted), or None if the
        caller should loop for one more corrective attempt."""
        nonlocal retries, continuation, note
        res = {"subtask_id": sid, "status": "failed", "summary": reason}
        with st._lock:
            st.data.setdefault("subtask_status", {})[sid] = "failed"
            st.save()
        lock_criteria(sid, centella_dir, st)
        if not _retryable_failure(reason):
            log(f"  {sid}: non-retryable failure — terminating: {reason}")
            return res
        retries += 1
        if retries > caps["failed_retries"]:
            log(f"  {sid}: retry cap reached — terminating")
            return res
        continuation = False
        note = f"Previous attempt failed: {reason}"
        return None

    while True:
        # Before re-invoking the implementer (whether this is a corrective
        # retry or a handoff continuation), verify the criteria file has not
        # been altered since it was locked. A retried implementer is a stuck
        # model — exactly the case the lock guards against. No-op on the first
        # iteration, when no lock exists yet.
        verify_criteria_lock(sid, centella_dir, st)

        res = run_implementer(sid, centella_dir, caps, st,
                              continuation=continuation, note=note)

        # cross-field invariant check — catches a worker that lied about
        # status. A self-contradictory result means the worker is malfunctioning
        # or dishonest: non-retryable by `_retryable_failure`.
        problem = validate_result(res, centella_dir)
        if problem:
            log(f"  result invariant violated for {sid}: {problem}")
            done = fail(problem)
            if done is not None:
                return done
            continue

        status = res.get("status")
        with st._lock:
            st.data.setdefault("subtask_status", {})[sid] = status
            st.save()

        if status == "complete":
            # a 'complete' claim with no commits is a retryable mistake —
            # the worker may genuinely have work to commit and just forgot
            commit_err = check_branch_has_commits(sid, worktree)
            if commit_err:
                log(f"  branch check failed for {sid}: {commit_err}")
                done = fail(commit_err)
                if done is not None:
                    return done
                continue
            # uncommitted changes — retryable, same reasoning
            wt_status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=worktree, capture_output=True, text=True,
            )
            dirty = [l for l in wt_status.stdout.splitlines()
                     if l and not l.startswith("??")]
            if dirty:
                done = fail(f"{sid}: worktree has {len(dirty)} uncommitted "
                            f"change(s) — changes will be lost on integration")
                if done is not None:
                    return done
                continue
            lock_criteria(sid, centella_dir, st)
            # protected-path violation — the worker wrote to .git/ etc.: it is
            # broken, not merely careless. Non-retryable by `_retryable_failure`.
            scope_err = check_diff_scope(sid, worktree, subtask, st)
            if scope_err:
                done = fail(scope_err)
                if done is not None:
                    return done
                continue
            return res

        if status == "incomplete-handoff":
            cp_err = validate_checkpoint(res.get("checkpoint_path") or "")
            if cp_err:
                log(f"  bad checkpoint for {sid}: {cp_err}")
                return {"subtask_id": sid, "status": "blocked",
                        "blocker": f"checkpoint invalid: {cp_err}",
                        "summary": cp_err}
            lock_criteria(sid, centella_dir, st)
            continuations += 1
            if continuations > caps["handoff_continuations"]:
                return {"subtask_id": sid, "status": "blocked",
                        "blocker": ("exceeded handoff cap — subtask is mis-scoped "
                                    "and needs re-decomposition"),
                        "summary": "handoff cap exceeded"}
            continuation, note = True, ""
            continue

        if status == "failed":
            # a worker that reported failure itself — treat its summary as the
            # reason and run it through the same retry policy
            done = fail(res.get("summary") or "worker reported failure")
            if done is not None:
                return done
            continue

        # blocked, or anything unexpected
        return res


def integrate_wave(wave: list[str], results: dict[str, dict],
                   centella_dir: Path, caps: dict, st: State) -> list[str]:
    """Merge each completed subtask branch into staging (git merge, not
    cherry-pick); resolve conflicts with an integrator worker. Returns the
    list of integrated ids.

    If an integrator cannot resolve a conflict (status other than 'resolved'),
    the in-progress merge is aborted so the staging worktree is left clean, and
    the run is terminated with the integrator's diagnosis — an unresolved
    conflict must not silently proceed onto a corrupt staging tree."""
    integrated, integrated_so_far = [], []
    staging = (centella_dir / "worktrees" / "staging").resolve()
    for sid in wave:
        if results.get(sid, {}).get("status") != "complete":
            continue
        proc = run_script("integrate.sh", sid)
        if proc.returncode == 0:
            integrated.append(sid)
            integrated_so_far.append(sid)
            continue
        # conflict: staging worktree is mid-merge — hand to an integrator
        log(f"  conflict integrating {sid}; spawning integrator")
        sys_prompt = (PROMPTS / "integrator.md").read_text()
        up = (f"Resolve the in-progress merge conflict in this worktree.\n"
              f"CENTELLA_DIR is {centella_dir}.\n"
              f"Incoming subtask: {sid}\n"
              f"Already-integrated subtasks it may conflict with: "
              f"{', '.join(integrated_so_far) or 'none'}")
        st.bump_workers(caps)
        ires = claude_p(user_prompt=up, system_prompt=sys_prompt,
                        schema_key="integrator", cwd=str(staging),
                        allowed_tools=ACT_TOOLS, max_turns=60,
                        autonomous=True, caps=caps, st=st)
        if ires.get("status") == "resolved":
            # the integrator must have actually committed the merge — a
            # 'resolved' claim with the worktree still mid-merge is a lie,
            # the integrator-side analogue of check_branch_has_commits.
            merge_err = check_merge_committed(staging)
            if merge_err:
                subprocess.run(["git", "merge", "--abort"],
                               cwd=str(staging), capture_output=True)
                with st._lock:
                    st.data["integrator_failure"] = {
                        "subtask": sid,
                        "reason": f"integrator claimed 'resolved' but {merge_err}"}
                    st.save()
                die(f"integrator for {sid} returned 'resolved' but {merge_err}. "
                    f"The merge was aborted; centella/staging is clean. "
                    f"State saved — resolve and re-run with --resume.")
            commit_err = check_integrator_commit(staging)
            if commit_err:
                # non-fatal: log and record, but don't undo the integration
                log(f"  ⚠  integrator commit warning for {sid}: {commit_err}")
                with st._lock:
                    st.data.setdefault("integrator_warnings", {})[sid] = commit_err
                    st.save()
            integrated.append(sid)
            integrated_so_far.append(sid)
        else:
            # design-conflict or failed: the integrator could not produce a
            # correct merge. Abort the in-progress merge so staging is left
            # clean, then terminate — this must not proceed silently.
            diagnosis = (ires.get("diagnosis")
                         or ires.get("resolution_summary")
                         or "no diagnosis provided")
            log(f"  integrator could not resolve {sid}: "
                f"{ires.get('status')} — {diagnosis}")
            subprocess.run(["git", "merge", "--abort"],
                           cwd=str(staging), capture_output=True)
            with st._lock:
                st.data["integrator_failure"] = {
                    "subtask": sid, "status": ires.get("status"),
                    "diagnosis": diagnosis}
                st.save()
            die(f"integrator could not integrate {sid} "
                f"({ires.get('status')}): {diagnosis}\n"
                f"The in-progress merge was aborted; centella/staging is intact "
                f"at the last good wave. Resolve the conflict between {sid} and "
                f"the already-integrated subtasks manually, then re-run with "
                f"--resume.")
    return integrated


def validate_wave(wave: list[str], centella_dir: Path, caps: dict,
                  st: State) -> dict:
    """Re-run every wave subtask's frozen criteria against integrated staging.
    Tries the deterministic test runner first; falls back to LLM only on
    failure or when no runner was detected."""
    staging = (centella_dir / "worktrees" / "staging").resolve()

    # criteria files must exist before we spend any validation workers
    missing_err = check_criteria_files_exist(wave, centella_dir)
    if missing_err:
        die(f"pre-validation check failed: {missing_err}")

    # fast path: deterministic test suite — no worker invocation, no quota
    runner = st.data.get("test_runner")
    if runner:
        log(f"  running deterministic test suite: {' '.join(runner)}")
        r = subprocess.run(runner, cwd=str(staging), capture_output=True,
                           text=True, timeout=600)
        if r.returncode == 0:
            log("  staging tests pass — skipping LLM validator")
            return {"results": [
                {"subtask_id": sid, "all_criteria_met": True, "failing": []}
                for sid in wave
            ]}
        log(f"  tests failed (exit {r.returncode}) — "
            "falling through to LLM validator for diagnosis")

    # LLM validator: runs criteria that aren't captured by the test suite,
    # or diagnoses why the test suite failed
    criteria = [f"{centella_dir}/criteria/{sid}.md" for sid in wave]
    up = ("Verify the current working directory against these frozen "
          "success-criteria files. Run every criterion.\n" +
          "\n".join(f"- subtask {sid}: {path}"
                    for sid, path in zip(wave, criteria)))
    st.bump_workers(caps)
    return claude_p(user_prompt=up, system_prompt=VALIDATOR_SYSTEM,
                    schema_key="validator", cwd=str(staging),
                    allowed_tools=ACT_TOOLS, max_turns=40,
                    autonomous=True, caps=caps, st=st)


def phase_execute(centella_dir: Path, st: State, caps: dict) -> None:
    """Phase 5: run waves sequentially; within a wave, subtasks in parallel."""
    log("phase 4: creating staging worktree")
    proc = run_script("setup-staging.sh")
    if proc.returncode != 0:
        die(f"staging setup failed: {proc.stderr.strip()}")

    waves = st.data["waves"]
    start = st.data.get("completed_waves", 0)
    for wi in range(start, len(waves)):
        wave = waves[wi]
        log(f"phase 5: wave {wi + 1}/{len(waves)} — {len(wave)} subtask(s)")

        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=caps["max_parallel"]) as ex:
            futs = {ex.submit(settle_subtask, sid, centella_dir, caps, st): sid
                    for sid in wave}
            for fut in futs:
                sid = futs[fut]
                results[sid] = fut.result()
                log(f"  {sid}: {results[sid].get('status')}")

        blocked = [s for s, r in results.items()
                   if r.get("status") in ("blocked", "failed")]
        if blocked:
            st.data["blocked"] = {s: results[s].get("blocker")
                                  or results[s].get("summary") for s in blocked}
            st.save()
            die(f"wave {wi + 1} has unresolved subtasks: {', '.join(blocked)}. "
                f"See .centella/state.json; resolve and re-run with --resume.")

        integrate_wave(wave, results, centella_dir, caps, st)

        # deterministic: scan staging for unresolved conflict markers before
        # spending any validation workers — a marker means integration is broken
        staging_path = centella_dir / "worktrees" / "staging"
        marker_err = scan_conflict_markers(staging_path)
        if marker_err:
            die(f"wave {wi + 1}: {marker_err}\n"
                "Resolve manually in .centella/worktrees/staging, commit, "
                "then re-run with --resume.")

        # re-validate integrated staging; re-spawn failing implementers
        for attempt in range(caps["wave_revalidation_rounds"]):
            v = validate_wave(wave, centella_dir, caps, st)
            failing = [r["subtask_id"] for r in v.get("results", [])
                       if not r.get("all_criteria_met", False)]
            if not failing:
                break
            log(f"  staging re-validation failed for: {', '.join(failing)} "
                f"(round {attempt + 1})")
            if attempt == caps["wave_revalidation_rounds"] - 1:
                die(f"wave {wi + 1} fails staging validation after "
                    f"{caps['wave_revalidation_rounds']} rounds: {failing}")
            for sid in failing:
                settle_subtask(sid, centella_dir, caps, st)  # add fixing commits
                run_script("integrate.sh", sid)             # re-merge the delta

        st.data["completed_waves"] = wi + 1
        st.save()


def phase_finalize(centella_dir: Path, st: State) -> None:
    log("phase 6: finalizing")
    proc = run_script("finalize.sh")
    if proc.returncode != 0:
        die(f"finalize failed (staging is intact): {proc.stderr.strip()}")
    run_script("cleanup.sh")

    # verify the merge commit actually landed on the working branch
    r = subprocess.run(
        ["git", "log", "--merges", "-1", "--format=%s", "HEAD"],
        capture_output=True, text=True,
    )
    if r.returncode == 0 and "centella:" not in r.stdout:
        log("  ⚠  finalize warning: centella merge commit not found at HEAD — "
            "verify the working branch manually")

    # verify staging and the working branch are now identical — a non-empty
    # diff here means the merge silently dropped changes (data loss)
    r = subprocess.run(
        ["git", "diff", "--stat", "centella/staging..HEAD"],
        capture_output=True, text=True,
    )
    if r.returncode == 0 and r.stdout.strip():
        log(f"  ⚠  finalize warning: working branch diverges from staging after merge:\n"
            f"    {r.stdout.strip()}\n"
            "    Some changes may not have merged. Inspect manually.")
    wc = st.data.get("worker_count", 0)
    nsub = len(st.data.get("subtask_status", {}))
    tel = st.data.get("telemetry", {})
    st.data["finished_at"] = now()
    st.save()
    log(f"done — {nsub} subtasks, {len(st.data['waves'])} waves, "
        f"{wc} worker invocations. Merged into the working branch.")
    if tel:
        log(f"run weight: {tel.get('calls', 0)} claude -p calls, "
            f"{tel.get('input_tokens', 0):,} in / "
            f"{tel.get('output_tokens', 0):,} out tokens "
            f"(see .centella/state.json)")


# =========================================================================
# entry point
# =========================================================================
def main() -> None:
    ap = argparse.ArgumentParser(prog="centella", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", nargs="?", help="the task to execute")
    ap.add_argument("--resume", action="store_true",
                    help="resume an interrupted run from .centella/state.json")
    ap.add_argument("--answers", metavar="FILE",
                    help="JSON file of pre-supplied clarification answers")
    ap.add_argument("--no-clarify", action="store_true",
                    help="skip clarification; derive everything autonomously")
    ap.add_argument("--max-workers", type=int,
                    help="override the total worker-invocation budget")
    ap.add_argument("--max-parallel", type=int,
                    help="override concurrent workers per wave")
    ap.add_argument("--skip-smoke", action="store_true",
                    help="skip the live claude -p smoke test during preflight")
    args = ap.parse_args()

    if not shutil.which("claude"):
        die("the `claude` CLI is not on PATH — install Claude Code first")
    if subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                      capture_output=True).returncode != 0:
        die("not inside a git repository")

    caps = dict(DEFAULT_CAPS)
    if args.max_workers:
        caps["max_total_workers"] = args.max_workers
    if args.max_parallel:
        caps["max_parallel"] = args.max_parallel

    centella_dir = Path(".centella").resolve()
    for sub in ("", "subtasks", "criteria", "checkpoints"):
        (centella_dir / sub).mkdir(parents=True, exist_ok=True)
    st = State(centella_dir)

    try:
        if args.resume:
            if not st.load():
                die("nothing to resume — no .centella/state.json")
            validate_resume_state(st.data)
            task = st.data["task"]
            log(f"resuming: {task!r} (worker count {st.data.get('worker_count', 0)})")
            if "waves" not in st.data:
                die("cannot resume — run did not reach the scheduling phase")
        else:
            if not args.task:
                die("a task description is required (or use --resume)")
            task = args.task
            st.data = {"task": task, "started_at": now(), "worker_count": 0}
            st.save()
            preflight(centella_dir, skip_smoke=args.skip_smoke)
            supplied = (json.loads(Path(args.answers).read_text())
                        if args.answers else None)
            phase_classify(task, st, caps, args.no_clarify)
            gather_answers(st, supplied)
            plans = phase_plan(task, st, caps)
            subtasks, waves = schedule(plans)
            validate_plan(subtasks)
            runner = detect_test_runner()
            if runner:
                log(f"detected test runner: {' '.join(runner)}")
            st.data["test_runner"] = runner
            write_plan(centella_dir, task, st, subtasks, waves)

        phase_execute(centella_dir, st, caps)
        phase_finalize(centella_dir, st)
    except WorkerError as e:
        st.save()
        die(str(e))
    except KeyboardInterrupt:
        st.save()
        die("interrupted — state saved; re-run with --resume", code=130)


if __name__ == "__main__":
    main()
