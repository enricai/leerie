#!/usr/bin/env python3
"""
Leerie — deterministic task orchestrator for Claude Code.

Runs entirely on the Claude Code CLI / subscription. Every unit of LLM work is
a `claude -p` headless invocation. This script owns ALL control flow — phase
sequencing, wave scheduling, caps, retries, integration — in real Python, so
the orchestration cannot drift the way an LLM-driven controller can.

Each worker is a separate `claude -p` process, so there is no subagent nesting
anywhere. The script is the orchestrator; each `claude -p` call is a leaf.

Usage:
    leerie "<task description>"
    leerie --resume
    leerie "<task>" --answers answers.json
    leerie "<task>" --clarify             # opt into surfacing intent questions

Run it from the root of the target git repository.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import ctypes
import fcntl
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# tenacity is imported lazily inside claude_p() (its sole use site), not at
# module scope, so orchestrator/leerie.py loads on a bare host python3 that
# lacks requirements.txt deps. The `config --recapture` host seam
# (leerie:864) exec_module()s this file on the host, where python3 is not
# guaranteed to have tenacity; a module-scope import there would crash before
# run_recapture_deps()'s pathlib guards can print their diagnostic. Do not
# hoist it back to the top.

ROOT = Path(__file__).resolve().parent.parent       # leerie plugin/repo root
PROMPTS = ROOT / "prompts"
SCRIPTS = ROOT / "scripts"


def _read_version() -> str:
    """Single source of truth: `.claude-plugin/plugin.json`'s `version`
    field. Read at every `main()` invocation (the f-string in the
    `--version` action evaluates eagerly), not at import time. The
    manifest is part of the distributed plugin, so a missing /
    malformed file means the install is broken and a clear runtime
    error is the right outcome."""
    return json.loads(
        (ROOT / ".claude-plugin" / "plugin.json").read_text()
    )["version"]

# `{{include: _foo.md}}` placeholder pattern used by load_prompt() to embed
# a shared prompt fragment into a worker prompt. Only files prefixed with
# `_` are eligible — that prefix marks an internal include, never a
# standalone worker prompt. One level deep; no recursion needed today.
_PROMPT_INCLUDE_RE = re.compile(r"\{\{\s*include:\s*(_[a-z0-9_]+\.md)\s*\}\}")


def load_prompt(name: str) -> str:
    """Read prompts/<name>.md and expand any {{include: _foo.md}}
    placeholders by inlining the named fragment. Replaces the prior
    `(PROMPTS / f"{name}.md").read_text()` pattern so the
    clarification-filter wording can live in one place
    (prompts/_clarification_filter.md, included by both the classifier
    and the implementer prompts). See DESIGN.md §11."""
    raw = (PROMPTS / f"{name}.md").read_text()
    return _PROMPT_INCLUDE_RE.sub(
        lambda m: (PROMPTS / m.group(1)).read_text(), raw)

# Minimum `claude` CLI version that supports `--json-schema` in `claude -p`
# mode. Anthropic CHANGELOG v2.1.22 (2026-01-28): "Fixed structured outputs
# for non-interactive (-p) mode." Earlier 2.1.x point releases may work but
# have no positive evidence in the release notes; v1.x and v2.0.x do not
# have the flag at all. Enforced at preflight by _check_claude_cli_version().
MIN_CLAUDE_CLI = (2, 1, 22)

# --- tunable caps --------------------------------------------------------
DEFAULT_CAPS = {
    "max_total_workers": 200,       # hard ceiling on claude -p invocations
    # Concurrent workers within a wave. Per-worker cgroup containment
    # (DESIGN §6 *Memory containment*) keeps an OOM inside one worker's
    # cgroup, so wave-level parallelism can be high without cascading to
    # sshd / lima-guestagent. Users on smaller VMs can opt down via
    # --max-parallel.
    "max_parallel": 5,              # concurrent workers within a wave
    # Per-subtask re-spawn budget. Consumed by BOTH context-exhaustion
    # handoffs and DESIGN §11 mid-execution clarifications — a subtask
    # that mixes the two is still bounded by this single cap, so "ask
    # instead of research" cannot win extra budget. See DESIGN §11
    # mid-execution clarification subsection.
    "subtask_continuations": 3,
    "failed_retries": 1,            # re-spawns of a failed implementer
    # Orchestrator-level conformer re-runs per subtask (DESIGN §9 *Post-
    # work conformance*). Bounds the loop in `settle_subtask` that re-spawns
    # the conformer when its output is malformed or residuals remain.
    # Exhausting this cap is a *warning*, not a failure — the phase is
    # advisory and never produces a `failed` / `blocked` subtask status.
    "conformance_rounds": 3,
    # CRITIC-pattern mechanical-feedback re-invocation caps. Each worker
    # type gets code-enforced structural checks (file existence, graph
    # cycles, lockfile consistency, etc.) and the orchestrator re-invokes
    # with the check results as external feedback if issues are found.
    # Separate from conformance_rounds (which loops on observable
    # build/lint/test signals) and from confidence_rounds (which is
    # the worker-internal evidence-gate iteration budget).
    "judgment_check_rounds": 3,     # classifier, reconciler, provision,
                                    # overlap judge, integrator
    "planner_check_rounds": 3,      # planner (richer checks justify more)
    "implementer_confidence_retries": 2,  # separate from subtask_continuations
    "planner_samples": 3,           # multi-sample; set to 1 to disable
    "worker_timeout_sec": 5400,     # 90 minutes per worker process
    # If a worker emits no stdout events for this many seconds, log a
    # warning naming the worker, its PID, the elapsed silence, and any
    # stderr tail. Observation-only — does not kill the worker. The
    # 90-min `worker_timeout_sec` remains the only kill. Surfaces the
    # silent-hang failure class that otherwise gives the user zero
    # feedback between phase start and the 90-min hard kill.
    "worker_idle_warn_sec": 300,
    # Worker-internal evidence-gate iterations for planner and implementer
    # (DESIGN §8 + §13). User-tunable via --confidence-rounds /
    # LEERIE_CONFIDENCE_ROUNDS / leerie.toml; see IMPLEMENTATION.md §2
    # "Confidence rounds". The orchestrator does not count these iterations
    # — the cap is passed into each worker's prompt and the worker bounds
    # itself. Surfacing the knob is for tuning persistence, not for
    # promoting a prompt-governed limit to a code guarantee.
    "confidence_rounds": 8,
    # Per-worker cgroup memory cap (bytes). Each `claude -p` worker is
    # enrolled (by the cgroup broker — see scripts/cgroup-broker.py and
    # DESIGN §6, because the dropped-privilege orchestrator can't enroll
    # workers or set controller limits itself) in its own child
    # cgroup `leerie-w-<sid>` under leerie.slice on both runtimes, and the
    # broker sets the cgroup's memory.max to this value. When a worker's tool
    # subtree (vitest, tsc, webpack workers, etc.) tries to allocate past
    # the cap, the kernel OOM-kills inside the cgroup — sshd / pid 1 /
    # other workers in the container are unaffected. This is the fix for
    # the OOM cascade from the finalmemoriam run (kernel ring on the
    # Colima VM showed agetty → journald → sshd → lima-guestagent killed
    # because a vitest worker blew past 1.85 GB RSS inside the
    # container's single memcg). Resolved at runtime by
    # resolve_worker_memory_max — CLI > env > leerie.toml > default. The
    # default value of None means "auto-derive from /proc/meminfo at run
    # start" (see _auto_worker_memory_max).
    "worker_memory_max_bytes": None,
    # Per-worker cgroup v2 PID cap. Catches runaway fork-bomb behavior
    # from a worker's tool subtree while still admitting a legitimate
    # heavy conformance run. The prior default of 256 was too low for a
    # real workload: a repo whose full test suite fans out across many
    # subprocesses (e.g. leerie's own ~193-module suite, ~70 of which
    # shell out to bash harnesses / git / the launcher, run un-scoped by
    # the conformer per prompts/conformer.md) bursts past 256 in seconds
    # and wedges every subsequent fork with EAGAIN — the exact failure
    # the PID-exhaustion detector then reports. 1024 clears that burst
    # while a runaway shell loop (in the thousands) still trips the cap.
    # Overridable per-repo via resolve_worker_pids_max (CLI > env >
    # leerie.toml > this default).
    "worker_pids_max": 1024,
    # Auth/quota backoff budget. When `claude -p` returns an envelope
    # whose `api_error_status` is 401/429/529 or whose result message
    # names an auth/rate-limit failure, `claude_p()` retries with tenacity's
    # `wait_exponential_jitter(initial=15, max=120, jitter=5)` until
    # this many cumulative seconds have elapsed, then bails with a
    # WorkerError that names the Claude Code subscription cap. 300 s
    # (~4 retries: 15 + 30 + 60 + 120 = 225 s plus jitter) is enough
    # to ride out a brief gateway hiccup but small enough that a real
    # 5-hour subscription cap surfaces to the user quickly rather than
    # tying the run up overnight. See IMPLEMENTATION.md §3 *Auth/quota
    # backoff* and §6 caps row.
    "auth_retry_max_sec": 300,
    # Per-subtask `claude -p` call multiplier consumed by
    # `check_budget_feasibility()` (DESIGN §13 *Budget feasibility —
    # fail fast at the cheapest moment*). Default 2.5 calibrated from
    # six on-disk runs as of 2026-06-03: three completed runs cluster
    # at 2.0–2.31 calls/subtask (1 implementer + ~1.3 conformer rounds);
    # one died-mid-execution run logged 2.59 with prompts/{implementer,
    # conformer}.md §"Environmental issues are out of scope" not yet
    # in effect (lint-fighting inflator). 2.5 covers the worst observed
    # real ratio. NOT a runtime gate — consumed by the planner-output
    # feasibility preflight only, never by `bump_workers()`.
    "subtask_call_estimate": 2.5,
    # Multiplier applied to `total_estimate` inside
    # `check_budget_feasibility()` before comparison to
    # `max_total_workers`. With the default `subtask_call_estimate=2.5`,
    # the guaranteed cap headroom is ~1.44× — comfortably absorbs the
    # observed worst case while still flagging the truly-unwinnable
    # 29-subtask runs that motivated the preflight.
    "budget_safety_margin": 1.15,
    # Repo-map token budget for the personalized-PageRank-ranked subgraph
    # injected into the planner and splitter (DESIGN §5½ (P6) *Codebase structural
    # map*). The subgraph is binary-searched to fit within this many tokens;
    # ranked so the most task-relevant symbols appear at the prompt extremes.
    # ~1000 tokens keeps the injection below 5% of typical planner context.
    "repo_map_tokens": 1000,
    # Maximum recursion depth for recursive_decompose() (DESIGN §5½ (P1) *P1
    # recursive judge + splitter*). A depth-5 tree can represent up to 2^5=32
    # leaves from a single subtask, more than enough for the observed 64-file
    # migration sweeps (target ~8 leaves). Terminates recursion at depth ≥ 5
    # even if fit_judge still scores below decompose_fit_threshold.
    "decompose_max_depth": 5,
    # P1 fit-judge pass threshold for recursive_decompose() (DESIGN §5½ (P1)).
    # A subtask scoring ≥ this value is accepted as a leaf (well-fit).
    # MEASURED on n=24 telemetry-labeled subtasks: oversized mean 0.26 vs
    # well-fit mean 0.84 — 0.57 separation, 88% accuracy at 0.70. The
    # originally-planned 0.95 threshold over-splits 100% of well-fit subtasks
    # (their scores cluster at 0.82–0.93); 0.70 is the empirically-derived
    # optimum (see F1-build-measure.md).
    "decompose_fit_threshold": 0.70,
    # No-progress guard for recursive_decompose(). If this many consecutive
    # recursion rounds produce no child whose fit score exceeds the parent's,
    # the subtask is accepted as a leaf with a warning rather than recursing
    # indefinitely. Prevents a degenerate splitter output from looping to
    # decompose_max_depth.
    "decompose_noprogress_rounds": 2,
}

# Every key the orchestrator writes to `st.data`. Canonical alongside the
# `state.json` field table in IMPLEMENTATION.md §8 — drift in either
# direction is caught by tests/test_state_fields.py.
STATE_FIELDS = (
    "task", "started_at", "finished_at",
    "waves", "completed_waves", "subtask_status",
    "blocked",
    "worker_count", "telemetry",
    "categories", "classifier_questions", "answers",
    "needs_source_of_truth", "source_of_truth_pref", "clarify",
    "dangerously_skip_permissions",
    "skip_overlap_judge",
    "skip_satisfied_check",
    "skip_budget_check",
    "strict_conformer",
    "skip_base_baseline",
    "skip_repo_map",
    # cgroup_containment: recorded by the fail-closed gate
    # (enforce_and_record_cgroup_containment, in _run_phases just before the
    # first worker spawns) (DESIGN §6 *Memory containment*). {enforced: bool, hierarchy:
    # "v2"|"v1"|null}. `enforced=false` means workers ran without memory/PID
    # limits (only reachable via --dangerously-allow-uncapped). Persisted
    # because the crash that motivated the broker left NO artifact of the
    # silent containment failure — this makes it visible in state.json.
    "cgroup_containment",
    "verbosity", "inspect_dirs",
    "integrator_warnings", "scope_warnings",
    "conformance",
    "provision",
    # external_preconditions: planner-declared `extent: external` requires
    # entries collected during phase_reconcile (DESIGN §5
    # `requires.extent`). Persisted so write_plan() can surface them in
    # plan.json's `preconditions` section. Empty list when no planner
    # declared any external requirement — the common case.
    "external_preconditions",
    # current_phase carries the orchestrator's active phase string so the
    # `_memory_sampler` (telemetry sidecar at memory.ndjson) can correlate
    # RSS growth with the code path that produced it. Updated at each
    # phase_* entry. Empty string before phase 1.
    "current_phase",
    # dropped_subtasks: subtasks soft-dropped by filter_offtree_subtasks
    # because their files_likely_touched resolved off-tree (most commonly
    # into an inspect-dir mount). Map of sid → {reasons: [str], files:
    # [str]}. Empty/absent when no drop fired. Audit trail only — the
    # run proceeds with the surviving subtasks.
    "dropped_subtasks",
    # conditional_drops: planner-emitted consumer subtasks dropped by the
    # reconciler's `conditional_drops` resolution op (DESIGN §5) — i.e.
    # the planner authored the subtask as "no-op if X" and X turned out
    # to be unresolvable. Map of sid → {reason: str, from_unresolved_tag:
    # str}. Empty/absent when no conditional_drop fired. Distinct audit
    # field from `dropped_subtasks` (off-tree soft drops, phase 3) so the
    # two causes stay separately auditable.
    "conditional_drops",
    # speculative_collapse_drops: subtask sids mechanically pruned by
    # dead-subtask elimination (DESIGN §5) — fully-speculative subtasks
    # whose every in_plan requires was unresolvable because the provider
    # domain returned 0 subtasks. List of sid strings.
    "speculative_collapse_drops",
    # plan_overlap_judge / plan_overlap_applied: set by phase_overlap_judge
    # (DESIGN §5 *Cross-domain surface overlap*). plan_overlap_judge stores
    # the full judge worker output (list of collisions with a_sid/b_sid/
    # artifact/resolution/reason/merge_feasibility) for audit / replay
    # debugging. plan_overlap_applied stores the post-apply mutation summary
    # (each entry: {action: merge|drop_a|drop_b, surviving_sid, dropped_sid,
    # artifact}). Both empty/absent when the phase short-circuits
    # (single-planner runs, < 2 subtasks, --skip-overlap-judge) or when the
    # judge returned `{collisions: []}`.
    "plan_overlap_judge",
    "plan_overlap_applied",
    # no_work_required / no_work_reasons: set by _finish_no_work_run when
    # every planner returns status="ready" with empty subtasks (DESIGN §8
    # *The cleared-but-empty terminal state*). The task is already
    # satisfied on HEAD; the orchestrator records finished_at, skips
    # phases 3-6, and exits 0. Absent on every normal run.
    "no_work_required",
    "no_work_reasons",
    # working_branch: the user's branch at the moment phase_classify
    # runs (`git rev-parse --abbrev-ref HEAD`). Mirrored to run.json
    # and to `<state-root>/runs/<id>/working-branch` (setup-run.sh writes
    # the on-disk copy later). Persisted into st.data so downstream
    # readers (pr_writer payload, run_final_conformance's DIFF_BASE)
    # do not have to re-query git or re-read run.json.
    "working_branch",
    # leerie_version: the version string from .claude-plugin/plugin.json at
    # the time the run started (or resumed). Persisted so the PR footer can
    # show the exact version that produced the run, which aids debugging.
    "leerie_version",
    # dep_capture_done: set to True in state.json and written as a sentinel
    # file (<run_dir>/dep_capture.done) after capture_repo_deps completes a
    # successful write. The run-start backstop checks the sentinel file to
    # skip runs already captured (idempotency).
    "dep_capture_done",
)

CATEGORIES = [
    "feature-implementation", "bug-fixing", "refactoring",
    "performance-optimization", "testing", "dependency-migration",
    "configuration-build", "infrastructure", "documentation",
]

# Short abbreviations used in the run_id branch-name prefix (DESIGN §6
# "The run identifier"). Every entry in CATEGORIES must have an abbrev —
# enforced by tests/test_run_id.py::test_category_abbrev_coverage.
CATEGORY_ABBREV = {
    "feature-implementation": "feat",
    "bug-fixing": "bugfix",
    "refactoring": "refactor",
    "performance-optimization": "perf",
    "testing": "test",
    "dependency-migration": "deps",
    "configuration-build": "config",
    "infrastructure": "infra",
    "documentation": "docs",
}

# Paths an implementer (or conformer) may never write to. `.leerie/`
# inside a worktree and `.git/` are coordination-only — leerie's own
# in-repo coordination subtree (e.g., `.leerie-setup.sh` lives at the
# repo root, but the directory `.leerie/` is reserved) and the git
# plumbing dir, respectively; neither is the implementer's surface.
# (Run state itself lives at `<state-root>` outside the repo, mounted at
# `/leerie-state` — see resolve_leerie_root.) Inside `.claude/`, the
# three documented user-deliverable subtrees are exempt
# (`agents/`, `commands/`, `skills/`) because they ARE legitimate
# deliverables — leerie's own self-healing skill, for instance, instructs
# consumers to write a subagent file at `.claude/agents/<name>.md`.
# Top-level `.claude/` files (`settings.json`, `settings.local.json`,
# any future per-session state) stay protected — they are coordination
# and config, not deliverable customizations. See DESIGN §9.
_PROTECTED_PREFIXES = (".leerie/", ".git/")
_CLAUDE_DELIVERABLE_PREFIXES = (
    ".claude/agents/", ".claude/commands/", ".claude/skills/",
)


def is_protected_path(path: str) -> bool:
    """Return True if `path` is a meta-directory the implementer must not
    write to. See `_PROTECTED_PREFIXES` and `_CLAUDE_DELIVERABLE_PREFIXES`
    for the rule."""
    if any(path.startswith(p) for p in _PROTECTED_PREFIXES):
        return True
    if path.startswith(".claude/"):
        return not any(path.startswith(p) for p in _CLAUDE_DELIVERABLE_PREFIXES)
    return False

_READ_BASE = "Read,Grep,Glob,WebSearch,WebFetch"
# INSPECT_TOOLS is the read-only-with-shell bucket for classifier, planner,
# and reconciler. These workers run in the real repo cwd (not a worktree),
# so the default is that they cannot use --dangerously-skip-permissions.
# Without pre-approval, Bash calls in -p mode are gated by the permission
# system, return is_error=true, and surface as "tool-fail" — even for
# benign commands like `ls foo 2>&1` whose redirection trips the
# multiple-operations splitter. The Bash(<verb>:*) prefix patterns
# pre-approve specific read-only verbs (verified against claude 2.1.150:
# the pattern matcher handles trailing redirection like `2>&1`).
# Write/Edit are deliberately omitted: by default, the §12 "read-only
# worker" contract stays mechanically enforced — anything outside this
# allowlist falls through and is rejected in non-interactive mode. The
# top-level `leerie --dangerously-skip-permissions` flag (DESIGN §12 last
# paragraph) is the documented escape hatch: when set, claude_p passes
# --dangerously-skip-permissions to every worker, including the inspect
# bucket; the allowlist still names what the worker can call without
# prompting, but the gate that rejects everything else is lifted.
#
# Tool restriction is two-layered:
#   --allowedTools  (soft) — pre-approves tools for auto-execution;
#       bypassed entirely by --dangerously-skip-permissions.
#   --disallowedTools (hard, DISALLOWED_TOOLS) — removes tools from the
#       model's context; survives --dangerously-skip-permissions.
INSPECT_TOOLS = (
    f"{_READ_BASE},"
    "Bash(ls:*),Bash(find:*),Bash(cat:*),Bash(head:*),Bash(tail:*),"
    "Bash(wc:*),Bash(grep:*),Bash(rg:*),Bash(file:*),Bash(stat:*),"
    "Bash(tree:*),Bash(pwd),Bash(echo:*),"
    "Bash(git log:*),Bash(git show:*),Bash(git diff:*),"
    "Bash(git status),Bash(git branch:*),Bash(git ls-files:*)"
)
ACT_TOOLS = f"{_READ_BASE},Bash,Write,Edit"

# SATISFIED_PROBE_TOOLS — a deliberately narrow, BASE-TREE-ONLY subset of
# INSPECT_TOOLS for the phase-3 satisfied-probe (DESIGN §8 *Already-
# satisfied subtask elimination*). It must NOT include history-spanning
# git verbs (`git log:*`, `git show:*` with an arbitrary ref, `git
# branch`): a git worktree shares the main repo's full ref/object DB, so
# a probe that can reach other branches / later commits will "find" a
# deliverable that exists only on some OTHER branch and false-positive —
# silently dropping real work. Calibration measured 12/12 false-positives
# with full INSPECT_TOOLS latitude and 0 when scoped to the base tree.
# Only `git show HEAD:<path>`, `git diff`, and `git status` on the current
# checkout are allowed; the prompt reinforces the same rule (§12: the code
# constraint is the guarantee, the prompt is documentation).
SATISFIED_PROBE_TOOLS = (
    f"{_READ_BASE},"
    "Bash(ls:*),Bash(cat:*),Bash(head:*),Bash(wc:*),Bash(grep:*),"
    "Bash(rg:*),Bash(file:*),Bash(stat:*),Bash(pwd),Bash(echo:*),"
    "Bash(git show HEAD:*),Bash(git diff:*),Bash(git status)"
)

# DISALLOWED_TOOLS is the hard-deny list passed via --disallowedTools to
# every worker.  Unlike --allowedTools (permission-tier only, bypassed by
# --dangerously-skip-permissions), --disallowedTools with bare tool names
# removes tools from the model's context entirely — the model cannot see
# or call them regardless of permission mode.  This prevents workers from
# spawning subagents, setting timers, or sending messages that the
# orchestrator cannot track.
DISALLOWED_TOOLS = (
    "Agent,SendMessage,"
    "ScheduleWakeup,"
    "CronCreate,CronDelete,CronList,"
    "RemoteTrigger,PushNotification"
)

# --inspect-dir preference: extra directories to grant the inspect-bucket
# workers (classifier, planner, reconciler, plan_overlap_judge, provision) read access to via the
# Claude Code CLI's --add-dir flag. Without this, Read/Grep/Glob and the
# allowlisted Bash verbs in INSPECT_TOOLS are sandboxed to the repo cwd,
# so cross-repo references like "~/src/enric/beacon" fail with "blocked,
# outside allowed working directories". Repeatable on the CLI; env var is
# colon-separated; TOML key is a comma-separated string. Empty by default.
INSPECT_DIRS_ENV = "LEERIE_INSPECT_DIRS"
INSPECT_DIRS_FILE = "leerie.toml"

EXIT_NEEDS_ANSWERS = 10   # emitted when clarification is needed but no TTY
# Emitted by `check_budget_feasibility()` (DESIGN §13 *Budget feasibility —
# fail fast at the cheapest moment*) when the estimated remaining `claude -p`
# calls plus the already-spent count exceeds `max_total_workers`. Distinct
# from EXIT_NEEDS_ANSWERS=10 (deferred clarification) and from generic
# `die()` exit code 1 so the Fly runtime's decide_teardown trap and
# automation around it can route this case specifically. The error message
# names a recommended `--max-workers` value. Not resumable: `--resume`
# re-enters past schedule(), so the preflight has nothing to gate; a run
# that died here re-runs from scratch with the recommended cap or a split
# task.
EXIT_BUDGET_INFEASIBLE = 11

# Emitted when State.__init__ cannot acquire the run-directory flock
# because another orchestrator already owns this run. Lets the launcher
# refuse `--resume` cleanly when an orchestrator is still alive,
# instead of silently spawning a second one that would race on
# state.json (DESIGN §6 *Single owner per run dir*). 75 aligns with
# the BSD sysexits.h EX_TEMPFAIL convention.
EXIT_LOCKED = 75

# Fixed backoff before auto-resuming a rate-limit that carried NO parseable
# reset time — chiefly the out-of-credits mid-stream kill, where the kill left
# no `resetsAt` to compute a wake time from (DESIGN §6 *Rate-limited →
# auto-resume*). We can't know when credits/limits refresh, so we poll: sleep a
# short fixed interval and re-exec `--resume`. A premature retry (still limited)
# just re-hits the same clean pause and sleeps again — cheap, and bounded by the
# persisted `max_total_workers` budget so a stuck run never runs away.
RATE_LIMIT_RETRY_BACKOFF_SEC = 300  # 5 minutes

# Source-of-truth preference — see DESIGN.md §11. Resolution order:
# --source-of-truth CLI flag → LEERIE_SOURCE_OF_TRUTH env var →
# per-repo leerie.toml → 'both'. CLI/env are session knobs, so they
# outrank the committed file default. The preference is never surfaced
# as an interactive question: any explicit setting overrides the
# default, and unset means the caller implicitly accepted 'both'.
SOURCE_OF_TRUTH_VALUES = ("codebase", "research", "both")
SOURCE_OF_TRUTH_ENV = "LEERIE_SOURCE_OF_TRUTH"
SOURCE_OF_TRUTH_FILE = "leerie.toml"

# Runtime mode — see IMPLEMENTATION.md §2 "Runtime mode". Resolution order:
# --runtime CLI flag → LEERIE_RUNTIME env var → per-repo leerie.toml → 'local'.
# CLI/env are session knobs and outrank the committed file default.
RUNTIME_VALUES = ("local", "fly")
RUNTIME_ENV = "LEERIE_RUNTIME"
RUNTIME_FILE = SOURCE_OF_TRUTH_FILE

# Confidence-rounds preference — see IMPLEMENTATION.md §2 "Confidence
# rounds". Resolution order: --confidence-rounds CLI flag →
# LEERIE_CONFIDENCE_ROUNDS env var → leerie.toml → DEFAULT_CAPS
# fallback. The TOML file is shared with source-of-truth and model
# resolution.
CONFIDENCE_ROUNDS_ENV = "LEERIE_CONFIDENCE_ROUNDS"
CONFIDENCE_ROUNDS_FILE = SOURCE_OF_TRUTH_FILE

# CRITIC-pattern cap env vars. Same resolution shape as confidence_rounds.
JUDGMENT_CHECK_ROUNDS_ENV = "LEERIE_JUDGMENT_CHECK_ROUNDS"
PLANNER_CHECK_ROUNDS_ENV = "LEERIE_PLANNER_CHECK_ROUNDS"
IMPLEMENTER_CONFIDENCE_RETRIES_ENV = "LEERIE_IMPLEMENTER_CONFIDENCE_RETRIES"
PLANNER_SAMPLES_ENV = "LEERIE_PLANNER_SAMPLES"

# max-workers preference. Same resolution shape as confidence_rounds.
# CLI --max-workers wins; then LEERIE_MAX_WORKERS env; then max_workers
# in leerie.toml; then DEFAULT_CAPS fallback.
MAX_WORKERS_ENV = "LEERIE_MAX_WORKERS"
MAX_WORKERS_FILE = SOURCE_OF_TRUTH_FILE

# max-parallel preference. Same resolution shape as max_workers.
# CLI --max-parallel wins; then LEERIE_MAX_PARALLEL env; then
# max_parallel in leerie.toml; then DEFAULT_CAPS fallback.
MAX_PARALLEL_ENV = "LEERIE_MAX_PARALLEL"
MAX_PARALLEL_FILE = SOURCE_OF_TRUTH_FILE

# Per-worker memory cap (cgroup v2 memory.max). Same resolution shape:
# CLI --worker-memory-max wins; then LEERIE_WORKER_MEMORY_MAX env; then
# worker_memory_max in leerie.toml; then auto-derive from /proc/meminfo
# at startup. Accepted suffixes: K, M, G, T (case-insensitive, IEC
# binary — 1G == 1024**3 bytes). See _parse_memory_size.
WORKER_MEMORY_MAX_ENV = "LEERIE_WORKER_MEMORY_MAX"
WORKER_MEMORY_MAX_FILE = SOURCE_OF_TRUTH_FILE

# Per-worker cgroup PID cap (cgroup v2 pids.max). Same resolution shape:
# CLI --worker-pids-max wins; then LEERIE_WORKER_PIDS_MAX env; then
# worker_pids_max in leerie.toml; then DEFAULT_CAPS["worker_pids_max"].
# A positive integer — see resolve_worker_pids_max.
WORKER_PIDS_MAX_ENV = "LEERIE_WORKER_PIDS_MAX"
WORKER_PIDS_MAX_FILE = SOURCE_OF_TRUTH_FILE

# --no-push preference (DESIGN §6 "Push + PR"): skip the push + open-PR
# step at finalize. Resolution order: --no-push CLI flag → LEERIE_NO_PUSH
# env → no_push in leerie.toml → default False.
# --no-verify is CLI-only (no env/TOML mirror) to match CLAUDE.md's
# "never skip hooks unless asked" principle — env/TOML defaults for
# hook-skipping would dilute the "user explicitly asked" semantics.
NO_PUSH_ENV = "LEERIE_NO_PUSH"
NO_PUSH_FILE = SOURCE_OF_TRUTH_FILE

# --clarify preference (DESIGN §11): opt into surfacing intent questions
# to the user. Resolution order: --clarify CLI flag → LEERIE_CLARIFY
# env → clarify in leerie.toml → default False. Same precedence and
# parse rules as --no-push; mirrored env+TOML because "ask me questions"
# is a stable per-user preference, unlike --no-verify (a per-invocation
# safety override).
CLARIFY_ENV = "LEERIE_CLARIFY"
CLARIFY_FILE = SOURCE_OF_TRUTH_FILE

# --dangerously-skip-permissions escape hatch (DESIGN §12). Forces
# every claude -p worker — including the judgment workers that run in
# the real repo cwd — to pass --dangerously-skip-permissions, waiving
# the mechanical §12 read-only enforcement on classifier / planner /
# reconciler / provision. Named identically to the underlying CLI flag
# on purpose: choosing it means the user understands they are removing
# a guardrail. Resolution order: --dangerously-skip-permissions CLI
# flag → LEERIE_DANGEROUSLY_SKIP_PERMISSIONS env → leerie.toml → False.
DANGEROUS_SKIP_PERMS_ENV = "LEERIE_DANGEROUSLY_SKIP_PERMISSIONS"
DANGEROUS_SKIP_PERMS_FILE = SOURCE_OF_TRUTH_FILE

# --dangerously-allow-uncapped bypass (DESIGN §6 *Memory containment*).
# By default, if the cgroup broker probe fails (broker down, no usable cgroup
# hierarchy — neither a v2 unified mount nor v1 pids+memory controller mounts —
# read-only cgroupfs, or a rootless host whose systemd doesn't delegate
# pids+memory into the per-session user slice — non-systemd init, or an
# older/overridden delegation config; DESIGN §6 *Rootless exception*),
# leerie die()s before the first worker rather than run workers uncapped — a
# silently-uncapped run is what
# let a conformer's runaway subtree exhaust the VM thread table (the Bun
# EAGAIN crash). This flag downgrades that fatal gate to a loud warning and
# continues uncapped, for operators on hosts that genuinely cannot delegate.
# Resolution order: --dangerously-allow-uncapped CLI flag →
# LEERIE_DANGEROUSLY_ALLOW_UNCAPPED env → dangerously_allow_uncapped in
# leerie.toml → False.
DANGEROUS_ALLOW_UNCAPPED_ENV = "LEERIE_DANGEROUSLY_ALLOW_UNCAPPED"
DANGEROUS_ALLOW_UNCAPPED_FILE = SOURCE_OF_TRUTH_FILE

# --skip-overlap-judge bypass (DESIGN §5 *Cross-domain surface overlap*).
# Skips the phase 2¾ `plan_overlap_judge` worker even on multi-planner
# runs. The cheap-skip on single-planner runs is automatic and not
# bypassable by this flag; this flag only suppresses the worker spawn
# on the runs where it would otherwise fire. Resolution order:
# --skip-overlap-judge CLI flag → LEERIE_SKIP_OVERLAP_JUDGE env →
# skip_overlap_judge in leerie.toml → False.
SKIP_OVERLAP_JUDGE_ENV = "LEERIE_SKIP_OVERLAP_JUDGE"
SKIP_OVERLAP_JUDGE_FILE = SOURCE_OF_TRUTH_FILE

# --skip-budget-check bypass (DESIGN §13 *Budget feasibility — fail
# fast at the cheapest moment*). Suppresses `check_budget_feasibility()`
# in `_run_phases()` after `schedule()` returns. The runtime backstop in
# `State.bump_workers()` still fires if the run actually exceeds
# `max_total_workers` during execution; this flag only suppresses the
# *early* die() that catches mathematically-unwinnable runs at the
# planner/execute boundary. Use when the operator knows the conformer
# phase will degrade heavily to advisory warnings or otherwise come in
# under the estimate. Resolution order: --skip-budget-check CLI flag →
# LEERIE_SKIP_BUDGET_CHECK env → skip_budget_check in leerie.toml → False.
SKIP_BUDGET_CHECK_ENV = "LEERIE_SKIP_BUDGET_CHECK"
SKIP_BUDGET_CHECK_FILE = SOURCE_OF_TRUTH_FILE

STRICT_CONFORMER_ENV = "LEERIE_STRICT_CONFORMER"
STRICT_CONFORMER_FILE = SOURCE_OF_TRUTH_FILE

# --skip-base-baseline bypass (DESIGN §9 *Base-tree health baseline*).
# Suppresses `capture_conformance_baseline()` at the start of
# `phase_execute` — the once-per-run install-into-staging + BLT pass that
# records whether the base tree was green before any subtask mutated it.
# The pass runs the full test suite once (tens of seconds to a few
# minutes on heavy repos); this flag lets an operator who knows the base
# is green skip that up-front cost. When skipped, the conformer receives
# no BASELINE context and falls back to its prior self-judgment of
# "pre-existing" failures. Resolution order: --skip-base-baseline CLI
# flag → LEERIE_SKIP_BASE_BASELINE env → skip_base_baseline in
# leerie.toml → False.
SKIP_BASE_BASELINE_ENV = "LEERIE_SKIP_BASE_BASELINE"
SKIP_BASE_BASELINE_FILE = SOURCE_OF_TRUTH_FILE

# --skip-repo-map bypass (DESIGN §5½ (P6) *Codebase structural map*). Suppresses
# `build_repo_map()` and the ranked-subgraph injection into the planner
# context. Use on repos where tree-sitter cannot parse the primary language,
# or where the user wants the prior grep/glob-only planning path. When
# skipped, the planner receives no repo-map context and degrades gracefully
# to today's behavior. Resolution order: --skip-repo-map CLI flag →
# LEERIE_SKIP_REPO_MAP env → skip_repo_map in leerie.toml → False.
SKIP_REPO_MAP_ENV = "LEERIE_SKIP_REPO_MAP"
SKIP_REPO_MAP_FILE = SOURCE_OF_TRUTH_FILE

# <state-root>/repo-map-cache/ directory (relative to leerie_root). Stores
# the mtime-keyed per-file parse results produced by build_repo_map() so
# only changed files are re-parsed on subsequent runs (Aider diskcache
# pattern). Created on first use by build_repo_map().
REPO_MAP_CACHE_DIR = "repo-map-cache"

# capture_deps preference (DESIGN §6½). Controls whether phase_finalize
# scans logs and writes setup_packages / triggers the language-dep bake.
# Default True. Resolution order: LEERIE_CAPTURE_DEPS env →
# capture_deps in .leerie/config.toml → True.
CAPTURE_DEPS_ENV = "LEERIE_CAPTURE_DEPS"
CAPTURE_DEPS_CONFIG = ".leerie/config.toml"

# --skip-satisfied-check bypass (DESIGN §8 *Already-satisfied subtask
# elimination*). Suppresses the phase-3 `filter_satisfied_subtasks()`
# gate that spawns a per-subtask `satisfied_probe` worker to drop
# subtasks already met on the base tree. When set, every subtask
# proceeds to schedule(); the mechanical `check_branch_has_commits`
# backstop still catches an already-satisfied subtask post-execution
# (as a retryable no-op). Resolution order: --skip-satisfied-check CLI
# flag → LEERIE_SKIP_SATISFIED_CHECK env → skip_satisfied_check in
# leerie.toml → False.
SKIP_SATISFIED_CHECK_ENV = "LEERIE_SKIP_SATISFIED_CHECK"
SKIP_SATISFIED_CHECK_FILE = SOURCE_OF_TRUTH_FILE

# --pr-template selector. When the target repo has multiple PR templates
# in a PULL_REQUEST_TEMPLATE/ directory, pick this one by name (the
# basename, with or without .md). When unset, the alphabetically first
# .md in the directory wins. Has no effect when the repo uses a single
# top-level template (pull_request_template.md / .github/...) or when
# no template exists at all. Resolution order: --pr-template CLI flag →
# LEERIE_PR_TEMPLATE env → pr_template in leerie.toml → None.
PR_TEMPLATE_ENV = "LEERIE_PR_TEMPLATE"
PR_TEMPLATE_FILE = SOURCE_OF_TRUTH_FILE

# Verbosity — see IMPLEMENTATION.md §2 "Verbosity". Four levels with
# stackable -v/-q shortcuts following the clig.dev / cargo / kubectl
# convention. Default is `stream` because the user invoking leerie
# is opening to watch; -q drops to leerie's pre-streaming behavior;
# -qq goes fully quiet (errors still emit per clig.dev "errors emit at
# every level" anti-pattern guard).
VERBOSITY_VALUES = ("quiet", "normal", "stream", "debug")
VERBOSITY_DEFAULT = "stream"
VERBOSITY_ENV = "LEERIE_VERBOSITY"
VERBOSITY_FILE = SOURCE_OF_TRUTH_FILE

# Subtask statuses that count as "done" for the progress counter.
_TERMINAL_STATUSES = frozenset({"complete", "failed", "blocked"})

# Model selection — see IMPLEMENTATION.md §2 "Model selection". Aliases
# are passed straight to `claude --model`; the CLI resolves them to the
# current version. Each worker type has independent CLI/env/TOML
# overrides; falls back through global CLI/env/TOML/MODEL_DEFAULT.
MODEL_VALUES = ("sonnet", "opus", "haiku")
# Global default. Used when no per-worker default applies. DESIGN §5 +
# IMPLEMENTATION.md §2: judgment workers (everything except implementer)
# run on Opus by default; implementer's per-worker default is sonnet.
# Users can override globally with --model / LEERIE_MODEL / `model =`
# in leerie.toml, or per-worker with --model-<worker> /
# LEERIE_MODEL_<WORKER> / `model_<worker> =`.
MODEL_DEFAULT = "opus"
# Per-worker defaults applied *after* user overrides (CLI/env/TOML) but
# *before* the global MODEL_DEFAULT fallback. Only workers that need a
# different default from MODEL_DEFAULT appear here.
MODEL_DEFAULT_PER_WORKER = {
    "implementer": "sonnet",
    "conformer": "sonnet",
    "judge": "sonnet",
    "heal": "sonnet",
    "pr_writer": "sonnet",
    # satisfied_probe runs once per subtask (DESIGN §8 *Already-satisfied
    # subtask elimination*); throughput/cost dominates. Its correctness
    # rests on the base-tree-only tool scope + conservative default, not
    # the model tier.
    "satisfied_probe": "sonnet",
}
MODEL_ENV = "LEERIE_MODEL"
MODEL_FILE = "leerie.toml"
# Effort selection — see IMPLEMENTATION.md §2 "Effort selection". The
# `claude -p` CLI exposes `--effort {low,medium,high,xhigh,max}` to dial
# reasoning depth. The CLI exposes no --temperature and no --seed, so
# effort is the strongest determinism dial available; pinning it removes
# the "this run thought harder than that one" axis on judgment workers.
# A worker that resolves to None gets no --effort flag (inherits Claude's
# default) — that is the intended behavior for acting workers.
EFFORT_VALUES = ("low", "medium", "high", "xhigh", "max")
EFFORT_DEFAULT: str | None = None
EFFORT_DEFAULT_PER_WORKER: dict[str, str] = {
    "classifier": "high",
    "planner": "high",
    "reconciler": "high",
    "plan_overlap_judge": "high",
    "provision": "high",
    "integrator": "high",
    "pr_writer": "high",
    "dep_capture": "high",
    "fit_judge": "high",
    "splitter": "high",
}
EFFORT_ENV = "LEERIE_EFFORT"
WORKER_TYPES = ("classifier", "planner", "reconciler", "plan_overlap_judge",
                "satisfied_probe", "provision", "implementer", "integrator",
                "conformer", "fit_judge", "splitter")
# Post-run skill workers — not in WORKER_TYPES because they don't run inside
# the main orchestrate loop, but they do get dedicated model resolution via
# --judge-model / --heal-model (and their env / TOML mirrors).
MODEL_JUDGE_ENV = "LEERIE_MODEL_JUDGE"
MODEL_HEAL_ENV = "LEERIE_MODEL_HEAL"
MODEL_PR_WRITER_ENV = "LEERIE_MODEL_PR_WRITER"
MODEL_DEP_CAPTURE_ENV = "LEERIE_MODEL_DEP_CAPTURE"

# Judge output directory name — relative to <run-dir>. Holds LLM judge
# output files. Resolution order: --judge-dir CLI → LEERIE_JUDGE_DIR env →
# judge_dir in leerie.toml → "judge-out".
JUDGE_DIR_DEFAULT = "judge-out"
JUDGE_DIR_ENV = "LEERIE_JUDGE_DIR"
JUDGE_DIR_FILE = "leerie.toml"

# Heal output directory name — relative to <run-dir>. Holds LLM self-heal
# loop output files. Resolution order: --heal-dir CLI → LEERIE_HEAL_DIR env →
# heal_dir in leerie.toml → "heal-out".
HEAL_DIR_DEFAULT = "heal-out"
HEAL_DIR_ENV = "LEERIE_HEAL_DIR"
HEAL_DIR_FILE = "leerie.toml"

# Heal-loop convergence knobs — see IMPLEMENTATION.md §2 "Heal-loop convergence
# parameters". User-tunable knobs use the standard CLI/env/TOML/default
# resolution; non-user-tunable constants (window, delta, n) are fixed here.
HEAL_MAX_ROUNDS_DEFAULT = 10        # max iterations per call_type
HEAL_SUCCESS_THRESHOLD_DEFAULT = 0.9  # pass-rate bar for SUCCESS verdict
HEAL_PLATEAU_WINDOW_DEFAULT = 3     # look-back window for plateau detection
HEAL_PLATEAU_DELTA_DEFAULT = 0.03   # minimum improvement to avoid plateau
HEAL_N_REPLAYS_DEFAULT = 5          # replays per sample per iteration
HEAL_MAX_ROUNDS_ENV = "LEERIE_HEAL_MAX_ROUNDS"
HEAL_SUCCESS_THRESHOLD_ENV = "LEERIE_HEAL_SUCCESS_THRESHOLD"
HEAL_MAX_ROUNDS_FILE = "leerie.toml"
HEAL_SUCCESS_THRESHOLD_FILE = "leerie.toml"

# State directory override — see IMPLEMENTATION.md §2 "State directory".
# When set, leerie writes all run state (state.json, runs/, logs/) under
# this path instead of the default repo-relative `.leerie/`. This decouples
# the persisted state location from the repo mount so users can keep a single
# ~/.leerie-style directory across all repos rather than one per repo.
# Unset → falls back to `<repo_root>/.leerie` (today's behavior preserved).
STATE_DIR_ENV = "LEERIE_STATE_DIR"




def resolve_prompt(call_type: str) -> tuple[str, str, str]:
    """Return (source_kind, content, location_hint) for a worker call_type.

    source_kind is always 'file' — every worker's system prompt lives at
    `prompts/<call_type>.md`. location_hint is the stable relative path the
    heal loop uses to describe where to apply a patch.

    Raises ValueError for an unknown call_type.
    """
    if call_type not in WORKER_TYPES:
        raise ValueError(
            f"unknown call_type {call_type!r}; valid types: {WORKER_TYPES}"
        )
    hint = f"prompts/{call_type}.md"
    content = (PROMPTS / f"{call_type}.md").read_text()
    return ("file", content, hint)


# --- worker output schemas -----------------------------------------------
# Passed to `claude -p` via --json-schema. The CLI validates the worker's
# final output against the schema AFTER the run and exposes the validated
# object as `structured_output` in the JSON envelope. NOTE: --json-schema
# only accepts an INLINE schema string; a file path is silently ignored
# (verified against Claude Code 2.1.143), so these are embedded here.

# Shared shape for the conformer's build/lint/tests fields — three objects
# with the same {ran, passed, command, summary} schema. Pulled out to keep
# the conformer schema readable.
_CONFORMER_BLT_PROP = {
    "type": "object",
    "required": ["ran", "passed", "command", "summary"],
    "properties": {
        "ran": {"type": "boolean"},
        "passed": {"type": "boolean"},
        "command": {"type": "string"},
        "summary": {"type": "string"},
    },
}

# Shared shape for a single `requires` entry on a planner or reconciler
# subtask. The structural part is in the JSON schema — `tag` + `extent`
# must be present and `extent` is restricted to two values. The
# *conditional* invariant ("`reason` is required and non-empty when
# `extent == 'external'`") is not expressible in vanilla JSON Schema
# without `if/then`, so it is enforced in `validate_plan` instead, per
# CLAUDE.md "prompts are advisory, code enforces." See DESIGN §5
# `requires.extent` for the architectural contract.
_REQUIRES_ITEM = {
    "type": "object",
    "required": ["tag", "extent"],
    "properties": {
        "tag": {"type": "string"},
        "extent": {"type": "string", "enum": ["in_plan", "external"]},
        "reason": {"type": "string"},
    },
}


def _confidence_schema(axes: list[str]) -> dict:
    """Build the §8 confidence sub-schema for the given score axes.

    Every worker that self-gates on confidence uses the same structural
    discipline (DESIGN §8 / §12): numeric score axes, basis, falsifiers,
    contradictions, gap-to-close.  This helper DRYs the seven occurrences
    across SCHEMAS."""
    return {
        "type": "object",
        "required": [*axes, "basis", "falsifiers_tested",
                     "contradictions_reconciled", "gap_to_close"],
        "properties": {
            **{ax: {"type": "number"} for ax in axes},
            "basis": {"type": "string"},
            "falsifiers_tested": {
                "type": "array", "items": {"type": "string"}},
            "contradictions_reconciled": {
                "type": "array", "items": {"type": "string"}},
            "gap_to_close": {
                "type": "object",
                "properties": {ax: {"type": "string"} for ax in axes},
            },
        },
    }

SCHEMAS: dict[str, dict] = {
    "classifier": {
        "type": "object",
        "required": ["categories", "confidence"],
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
            "confidence": _confidence_schema(["classification"]),
        },
    },
    "planner": {
        "type": "object",
        "required": ["domain", "subtasks", "status", "confidence"],
        "properties": {
            "domain": {"type": "string"},
            # DESIGN §8 planner gate: a planner whose evidence gate cannot
            # clear within confidence_rounds emits status="blocked" with an
            # empty subtasks list and the gap analysis in
            # confidence.gap_to_close. The orchestrator surfaces a blocked
            # planner as a fatal run condition (the run cannot proceed with
            # no plan); confidence itself remains worker-internal.
            "status": {
                "type": "string",
                "enum": ["ready", "blocked"],
            },
            # §8 + §12 structural enforcement via _confidence_schema.
            "confidence": _confidence_schema(
                ["task_understanding", "decomposition_quality"]),
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
                        "requires": {"type": "array", "items": _REQUIRES_ITEM},
                        "provides": {"type": "array", "items": {"type": "string"}},
                        "success_criteria_seed": {"type": "string"},
                        "size": {"type": "string"},
                        "investigation_notes": {"type": "string"},
                    },
                },
            },
        },
    },
    "reconciler": {
        # Output of the reconciler worker (DESIGN §5). Spawned by
        # phase_reconcile after phase_plan when the merged planner output
        # has `requires` capability tags with no matching `provides`. The
        # worker reasons over the full task + merged subtasks (with their
        # provides, requires, depends_on, files_likely_touched) and the
        # list of unresolved tags, then emits eight arrays:
        #   - 5 resolution actions: renames, added_provides, added_subtasks,
        #     conditional_drops, dropped_requires (close unresolved-requires
        #     gaps; the common case). conditional_drops handles planner-
        #     emitted consumers whose own `intent` declares them conditional
        #     on an unresolvable precondition. dropped_requires handles
        #     consumers whose `requires` entry is over-specified — an
        #     aggregate, coarser synonym, or authoring-time decision the
        #     same subtask itself records (the consumer stays; only the
        #     bad edge goes).
        #   - 2 cycle-breaking-only actions: dependency_edges, merged_subtasks
        #     (used only in retry mode, when the gate detected the first
        #     attempt's mutations closed a cycle). dropped_requires also
        #     plays a cycle-breaking role in retry mode (over-specified
        #     requires entries that close cycles), but its primary home
        #     is now resolution.
        #   - 1 escape hatch: unresolvable (genuine gap with no plausible
        #     resolution; dies the run with the worker's stated reason).
        # Each array is independently optional (any can be empty). The
        # orchestrator applies the seven action arrays mechanically and
        # runs Tarjan's SCC + a must-include validator over the post-
        # mutation graph; `unresolvable` aborts before any mutation.
        "type": "object",
        "required": ["renames", "added_provides", "added_subtasks",
                     "conditional_drops",
                     "dropped_requires", "dependency_edges",
                     "merged_subtasks", "unresolvable", "confidence"],
        "properties": {
            "renames": {
                # Rewrite a `requires` tag on one subtask to match an
                # existing `provides` tag on another. The single most
                # common case (planners picked different words for the
                # same thing).
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["sid", "from", "to"],
                    "properties": {
                        "sid": {"type": "string"},
                        "from": {"type": "string"},
                        "to": {"type": "string"},
                    },
                },
            },
            "added_provides": {
                # A subtask actually produces the needed capability but
                # didn't declare the tag. Add it to that subtask's
                # `provides`.
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["sid", "tag"],
                    "properties": {
                        "sid": {"type": "string"},
                        "tag": {"type": "string"},
                    },
                },
            },
            "added_subtasks": {
                # Genuine gap — propose a new subtask to fill it. Shape
                # mirrors planner-output subtasks (same required fields).
                # Leerie stamps `_added_by_reconciler: true` on every entry
                # in `_apply_reconciler_output` — the model has no business
                # setting it (any guarantee that matters lives in code,
                # not in the model's response).
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
                        "requires": {"type": "array", "items": _REQUIRES_ITEM},
                        "provides": {"type": "array", "items": {"type": "string"}},
                        "success_criteria_seed": {"type": "string"},
                        "size": {"type": "string"},
                        "investigation_notes": {"type": "string"},
                    },
                },
            },
            "conditional_drops": {
                # Resolution op #4: drop a planner-emitted consumer subtask
                # whose own `intent` declares it conditional on an
                # unresolvable in_plan precondition (DESIGN §5). Used when
                # the planner authored a subtask as "no-op if X" and no
                # subtask in any domain produces X. The apply step removes
                # the named sid from its plan and prunes downstream
                # depends_on references; the drop is recorded in
                # state.data["conditional_drops"] for audit (distinct from
                # state.data["dropped_subtasks"] which records off-tree
                # soft-drops from filter_offtree_subtasks). Restricted to
                # planner-authored consumers — the apply step die()s if
                # the target sid carries _added_by_reconciler: true
                # (reconciler-added subtasks have no planner prose to
                # convert into a structured drop). Silent no-op on missing
                # sid (mirrors `renames` / `dropped_requires`).
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["sid", "reason"],
                    "properties": {
                        "sid": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "dropped_requires": {
                # Resolution op #5 AND Cycle-breaking op #1: remove an
                # over-specified `extent: in_plan` requires entry.
                # Resolution mode: the requires entry is an aggregate,
                # coarser synonym, or authoring-time decision the same
                # subtask itself records (rather than a code artifact
                # another subtask produces) — the consumer stays in the
                # plan, only the bad edge goes.
                # Cycle-breaking mode: an over-specified requires entry
                # was what closed a dependency cycle; dropping it breaks
                # the cycle without removing real subtask coupling.
                # Apply mechanics are identical in either mode and live in
                # `_apply_reconciler_output`. Silent no-op on missing
                # sid/entry (mirrors `renames`).
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["sid", "tag", "reason"],
                    "properties": {
                        "sid": {"type": "string"},
                        "tag": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "dependency_edges": {
                # Cycle-breaking op #2: assert an explicit `depends_on`
                # ordering between two existing subtasks. Used when both
                # sides legitimately need each other and one ordering is
                # the right answer. Both ids must exist — apply step
                # `die()`s on a missing id (fail-loud, mirrors the
                # added_subtasks collision check).
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["from", "to", "reason"],
                    "properties": {
                        "from": {"type": "string"},
                        "to": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "merged_subtasks": {
                # Cycle-breaking op #3: collapse two subtasks into one when
                # the cycle reflects a genuine authoring overlap (signal:
                # shared `files_likely_touched` between SCC members). The
                # surviving subtask (`into`) inherits the union of both
                # halves' provides/requires/depends_on/files_likely_touched,
                # with self-references dropped (a requires whose tag is now
                # in provides is removed; `from` is removed from depends_on
                # entries). Downstream subtasks' depends_on references to
                # `from` are rewritten to `into`. Tag-based requires need
                # no rewriting (they match by tag, and `into` carries the
                # union of provides). Telemetry: surviving subtask gets
                # `_merged_from: ["<absorbed-id>", ...]`. Optional override
                # fields let the reconciler restate the merged unit's
                # contract; absent overrides default to concatenation
                # (success_criteria_seed) or `into`'s values.
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["into", "from", "reason"],
                    "properties": {
                        "into": {"type": "string"},
                        "from": {"type": "string"},
                        "reason": {"type": "string"},
                        "title": {"type": "string"},
                        "intent": {"type": "string"},
                        "success_criteria_seed": {"type": "string"},
                    },
                },
            },
            "unresolvable": {
                # Gap with no plausible resolution. The orchestrator dies
                # with the worker's `reason` shown verbatim. NOT a valid
                # response to a cycle — cycle resolution must use one of
                # the cycle-breaking ops above; the retry prompt enforces
                # this.
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["sid", "tag", "reason"],
                    "properties": {
                        "sid": {"type": "string"},
                        "tag": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "confidence": _confidence_schema(["reconciliation"]),
        },
    },
    "implementer": {
        "type": "object",
        "required": ["subtask_id", "status", "confidence"],
        "properties": {
            "subtask_id": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["complete", "incomplete-handoff", "blocked",
                         "failed", "needs-clarification"],
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
            # §8 + §12 structural enforcement via _confidence_schema.
            "confidence": _confidence_schema(["root_cause", "solution"]),
            "checkpoint_path": {"type": ["string", "null"]},
            "blocker": {"type": ["string", "null"]},
            "summary": {"type": "string"},
            # DESIGN §11 mid-execution clarification exception. An
            # implementer that hits a genuine intent-question it cannot
            # derive from the codebase or research returns
            # status='needs-clarification' with this object set AND a
            # checkpoint of the work-in-progress; the orchestrator
            # surfaces the question to the user through the same
            # interactive / EXIT_NEEDS_ANSWERS paths used by the
            # Phase-1 classifier. The `why_underivable` field is
            # required for the same reason it is at Phase 1: to keep
            # the worker from drifting toward asking rather than
            # researching.
            "clarification_question": {
                "type": ["object", "null"],
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "why_underivable": {"type": "string"},
                },
                "required": ["id", "question", "why_underivable"],
            },
            # DESIGN §5 *Artifact passing between subtasks*. Structured
            # deliverables that downstream subtasks (named by the
            # predecessor graph) consume — research specs, design
            # summaries, generated parameters. The orchestrator
            # materializes the array to
            # `<state-root>/runs/<run-id>/artifacts/<sid>.json` on a
            # successful `complete` result and injects upstream entries
            # into the prompts of dependent subtasks. Absent or empty for
            # pure code-implementation subtasks. A non-empty artifacts
            # array is a substitute deliverable that lets
            # `check_branch_has_commits` accept a 'complete' with no
            # commits (DESIGN §5).
            "artifacts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "kind", "content"],
                    "properties": {
                        "name": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": ["markdown", "json", "text"],
                        },
                        "content": {"type": "string"},
                        "summary": {"type": "string"},
                    },
                },
            },
        },
    },
    "integrator": {
        "type": "object",
        "required": ["incoming_subtask", "status", "confidence"],
        "properties": {
            "incoming_subtask": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["resolved", "design-conflict", "failed"],
            },
            "resolution_summary": {"type": "string"},
            "diagnosis": {"type": ["string", "null"]},
            "confidence": _confidence_schema(["resolution"]),
        },
    },
    "judge": {
        # Output of a judge worker invocation. Three dimensions mirror the
        # beacon scorer rubric but as an LLM judgment (not a hard-coded rule):
        # schema adherence, factual accuracy, hallucination-freeness. The
        # `passed` field is the aggregate verdict; the caller decides what
        # to do with a failing verdict (log, heal, or both).
        "type": "object",
        "required": ["passed", "dimensions", "rationale", "suggested_fixes"],
        "properties": {
            "passed": {"type": "boolean"},
            "dimensions": {
                "type": "object",
                "required": ["schema_ok", "factual_ok", "hallucination_ok"],
                "properties": {
                    "schema_ok": {"type": "boolean"},
                    "factual_ok": {"type": "boolean"},
                    "hallucination_ok": {"type": "boolean"},
                },
            },
            "rationale": {"type": "string"},
            "suggested_fixes": {"type": "array", "items": {"type": "string"}},
        },
    },
    "conformer": {
        # DESIGN §9 *Post-work conformance*: an advisory worker that runs
        # after the implementer's success path. Schema requires the
        # build/lint/tests objects so a worker that skipped the honesty
        # discipline fails its own JSON gate before the orchestrator reads
        # it; cross-field invariants (residuals require non-empty
        # rules_files_read, fixed-violations cite a rule, updates cite a
        # path) are enforced by validate_conformance_result().
        "type": "object",
        "required": [
            "subtask_id", "rules_files_read",
            "rule_violations_fixed", "rule_violations_residual",
            "docs_updates", "tests_updates",
            "build", "lint", "tests", "summary", "confidence",
        ],
        "properties": {
            "subtask_id": {"type": "string"},
            "rules_files_read": {
                "type": "array", "items": {"type": "string"}},
            "rule_violations_fixed": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["rule", "fix", "evidence"],
                    "properties": {
                        "rule": {"type": "string"},
                        "fix": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                },
            },
            "rule_violations_residual": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["rule", "why_not_fixed"],
                    "properties": {
                        "rule": {"type": "string"},
                        "why_not_fixed": {"type": "string"},
                    },
                },
            },
            "docs_updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["path", "reason"],
                    "properties": {
                        "path": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "tests_updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["path", "reason"],
                    "properties": {
                        "path": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "build": _CONFORMER_BLT_PROP,
            "lint": _CONFORMER_BLT_PROP,
            "tests": _CONFORMER_BLT_PROP,
            "summary": {"type": "string"},
            # §8 + §12 structural enforcement via _confidence_schema.
            # The orchestrator loops on observable signals (residuals,
            # build/lint/test), not the score — but requiring the
            # discipline fields ensures a worker that skipped self-gating
            # fails its own JSON schema.
            "confidence": _confidence_schema(["conformance"]),
        },
    },
    "patch_generator": {
        # Output of the patch-generator worker. The worker proposes a
        # minimal edit to the system prompt that addresses the observed
        # failure mode. `anchor` and `replacement` are the only required
        # fields; the heal loop validates that `anchor` is a literal
        # substring of the current prompt body before applying the patch
        # (per the prompts-are-advisory-code-enforces principle — the
        # check is in request_patch, not in the prompt).
        "type": "object",
        "required": ["anchor", "replacement"],
        "properties": {
            "anchor": {"type": "string"},
            "replacement": {"type": "string"},
            "strategy": {"type": "string"},
            "pivot_reason": {"type": ["string", "null"]},
        },
    },
    "pr_writer": {
        # DESIGN §6 *Finalization*: LLM-written PR title + body that
        # respects the target repo's PR template when one exists. The
        # launcher prepends "leerie: " to the title (the worker must NOT)
        # so leerie-opened PRs stay easy to spot in lists. `used_template`
        # is the repo-relative path of the template that was filled out,
        # or null when no template was found.
        "type": "object",
        "required": ["title", "body", "used_template"],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "body": {"type": "string", "minLength": 1},
            "used_template": {"type": ["string", "null"]},
        },
    },
    "dep_capture": {
        # DESIGN §6½: LLM worker that reads the shell commands workers
        # ran (extracted from logs/*.log via _iter_log_tool_use) and
        # decides what the repo genuinely needs across all languages and
        # frameworks. Code enforces via this schema; the worker decides
        # content; _write_config_toml_keys writes it deterministically.
        "type": "object",
        "required": ["setup_packages", "language_installs"],
        "properties": {
            "setup_packages": {
                # apt packages for the warm apt layer in .leerie/Dockerfile.
                # minLength enforces "no empty package names" at the schema layer
                # (DESIGN §12: guarantees that can be checked mechanically live in
                # code) so an empty-item capture can't render to "" and blank the
                # persisted config.
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "language_installs": {
                # Per-manager install commands the Dockerfile bakes in via
                # COPY + RUN layers. `copy_inputs` are repo-relative paths
                # COPYed in before the RUN (e.g. requirements.txt,
                # package.json). Hallucinated paths are validated against
                # the repo and skipped; the RUN is still emitted.
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["manager", "command"],
                    "properties": {
                        "manager": {"type": "string", "minLength": 1},
                        "command": {"type": "string", "minLength": 1},
                        "copy_inputs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            },
            "dockerfile_notes": {"type": ["string", "null"]},
        },
    },
    "provision": {
        # LLM fallback for per-repo dependency provisioning (DESIGN §6½).
        # Fires only when detect_recipe_from_lockfiles() returns an empty
        # list — Java/Gradle, bare pyproject.toml, polyglot Makefile setups.
        # The recipe is structurally bounded here, then mechanically
        # validated by validate_provision_recipe() (§12 carve-out).
        "type": "object",
        "required": ["recipe", "confidence"],
        "properties": {
            "recipe": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["kind", "command", "working_dir"],
                    "properties": {
                        # `none` means no install step is needed (pure docs
                        # repo). `install` is the dep-fetch step. `build`
                        # is a follow-on that prepares the workspace
                        # (e.g. `pnpm run build` for an app that only
                        # functions after a build pass). Both kinds are
                        # rendered into the implementer/conformer prompts
                        # by `_format_provision_recipe_section`; the
                        # worker decides whether and when to run each.
                        "kind": {"enum": ["install", "build", "none"]},
                        # argv list (NOT a shell string). argv[0] must be
                        # in the allowlist enforced by
                        # validate_provision_recipe; no shell metacharacters
                        # anywhere in the argv.
                        "command": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                        # `.` or a relative path inside the repo. No
                        # absolute paths, no `..` segments — enforced by
                        # validate_provision_recipe.
                        "working_dir": {"type": "string"},
                        "timeout_s": {"type": "integer", "minimum": 1},
                    },
                },
            },
            "confidence": _confidence_schema(["recipe_correctness"]),
        },
    },
    "plan_overlap_judge": {
        # Output of the plan-overlap judge worker (DESIGN §5
        # *Cross-domain surface overlap*). Spawned by `phase_overlap_judge`
        # after `phase_reconcile` when 2+ planners contributed subtasks.
        # The worker reads the full reconciled subtask list (title,
        # intent, files_likely_touched, provides, requires, depends_on)
        # and emits zero or more `collisions` — pairs of subtasks that
        # would produce the same exported artifact with incompatible
        # designs.
        #
        # Each collision carries one of four `resolution` values:
        #   - merge: a single implementation can satisfy both subtasks'
        #     success criteria. MUST carry a non-empty `merge_feasibility`
        #     statement describing the unified API (enforced in code by
        #     _validate_overlap_judge_output, per DESIGN §12 — the prompt
        #     asks for it on every merge, and Python rejects a merge
        #     without it). The orchestrator collapses the two subtasks
        #     into one and uses `merge_feasibility` as the merged
        #     subtask's unified intent.
        #   - drop_a / drop_b: one subtask is strictly superseded by the
        #     other. The orchestrator removes the dropped sid and
        #     rewrites downstream `depends_on` references.
        #   - unresolvable: the two intents are structurally
        #     contradictory and no single artifact satisfies both. The
        #     orchestrator dies at plan time with both sids + artifact +
        #     reason; user revises the task or manually picks a side.
        "type": "object",
        "additionalProperties": False,
        "required": ["collisions", "confidence"],
        "properties": {
            "confidence": _confidence_schema(["judgment"]),
            "collisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["a_sid", "b_sid", "artifact",
                                 "resolution", "reason"],
                    "properties": {
                        "a_sid": {"type": "string"},
                        "b_sid": {"type": "string"},
                        "artifact": {"type": "string"},
                        "resolution": {
                            "type": "string",
                            "enum": ["merge", "drop_a", "drop_b",
                                     "unresolvable"],
                        },
                        "reason": {"type": "string"},
                        # Required-when-merge is enforced in code (see
                        # _validate_overlap_judge_output) rather than via
                        # JSON Schema conditionals — keeps the schema
                        # surface flat and the error message specific.
                        "merge_feasibility": {"type": "string"},
                    },
                },
            },
        },
    },
    "satisfied_probe": {
        # Output of the per-subtask satisfied-probe worker (DESIGN §8
        # *Already-satisfied subtask elimination*). Spawned once per
        # surviving subtask by `filter_satisfied_subtasks` in phase 3.
        # The worker inspects ONLY the base tree (working tree + `git
        # show HEAD:` — never other refs) and decides whether the
        # subtask's success criteria are already fully met, such that an
        # implementer would have nothing to commit.
        #
        # `satisfied` is the load-bearing field: True → the subtask is
        # soft-dropped before scheduling. The prompt instructs the
        # worker to default to False on any uncertainty — a false "already
        # done" silently deletes real work, which is strictly worse than
        # a false "still needed" (the latter only costs one implementer
        # round the mechanical no-commits backstop already tolerates).
        # No confidence block: this is an advisory prune subordinate to
        # `check_branch_has_commits` (§12), not a run-gating judgment.
        "type": "object",
        "additionalProperties": False,
        "required": ["satisfied", "evidence"],
        "properties": {
            "satisfied": {"type": "boolean"},
            "evidence": {"type": "string"},
            "checked": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    },
    "fit_judge": {
        # Output of the P1 fit-judge worker (DESIGN §5½ (P1) *P1 recursive
        # judge + splitter*). Spawned by recursive_decompose() for each
        # subtask candidate. The worker scores P1 Task-Context Fit as a
        # 0–1 confidence value: a subtask scores high when its scope and
        # context are co-minimized (minimum necessary complexity, maximum
        # relevance) and forms a single verifiable conceptual unit.
        #
        # MEASURED on n=24 telemetry-labeled subtasks: oversized mean 0.26
        # vs well-fit mean 0.84 — 0.57 separation, 88% accuracy at the
        # 0.70 threshold (DEFAULT_CAPS["decompose_fit_threshold"]).
        #
        # Read-only (INSPECT_TOOLS), fed the subtask spec and its P6
        # ranked subgraph. Reuses _confidence_schema for the score axis
        # so the same evidence-gate discipline (falsifiers, contradictions,
        # gap_to_close) applies.
        "type": "object",
        "required": ["score", "rationale", "diffuse", "confidence"],
        "properties": {
            # 0–1 Task-Context Fit score. >= decompose_fit_threshold (0.70)
            # means the subtask is a leaf (well-fit; no further splitting).
            "score": {"type": "number", "minimum": 0, "maximum": 1},
            # Human-readable explanation of the score.
            "rationale": {"type": "string"},
            # What is diffuse or over-scoped about the subtask (empty
            # string when score >= 0.70).
            "diffuse": {"type": "string"},
            "confidence": _confidence_schema(["fit"]),
        },
    },
    "splitter": {
        # Output of the P1 splitter worker (DESIGN §5½ (P1) *P1 recursive
        # judge + splitter*). Spawned by recursive_decompose() when
        # fit_judge scores below decompose_fit_threshold. The worker
        # receives a pre-computed file partition (from partition_files()
        # for migration sweeps) or the P6 repo-map subgraph for coupled
        # cases, and emits child subtasks with titles + success_criteria_seed.
        #
        # The worker only LABELS pre-partitioned chunks (it never decides
        # which files go where for the migration case — that is guaranteed
        # 100%-coverage by partition_files() by construction). For the
        # coupled-minority case it emits structural seams backed by the
        # repo-map, backstopped by _check_migration_surface.
        "type": "object",
        "required": ["children"],
        "properties": {
            "children": {
                "type": "array",
                "minItems": 1,
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
                        "depends_on": {
                            "type": "array", "items": {"type": "string"}},
                        "requires": {
                            "type": "array", "items": _REQUIRES_ITEM},
                        "provides": {
                            "type": "array", "items": {"type": "string"}},
                        "success_criteria_seed": {"type": "string"},
                        "size": {"type": "string"},
                        "investigation_notes": {"type": "string"},
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
    repo = Path(os.environ.get("USER_REPO") or os.getcwd()).name or "?"
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"{ts} [leerie] [{repo}] {msg}", flush=True)


def die(msg: str, code: int = 1):
    print(f"leerie: error: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


class InterruptedBySignal(BaseException):
    """Raised by signal handlers (SIGTERM, SIGHUP) installed in main().
    Inherits BaseException (not Exception) so the broad `except Exception`
    handlers inside orchestrate() don't swallow it. Caught only at
    main()'s top-level try/except, where it triggers worktree-only
    cleanup with state and branches preserved (DESIGN §6).

    SIGINT keeps Python's default KeyboardInterrupt — caught separately
    but follows the same worktree-only-cleanup contract (DESIGN §6
    *Cleanup on abnormal exit*). The explicit "throw this away"
    gesture is `scripts/cleanup.sh --run-id <id> --branches`, not
    Ctrl-C."""
    pass


class RateLimitedExit(BaseException):
    """Raised when claude -p reports the Claude Code subscription
    session-limit / rate-limit has been hit. Inherits BaseException so
    it propagates through asyncio's gather and the broad
    `except Exception` handlers without being swallowed — same pattern
    as InterruptedBySignal.

    Carries:
      - reset_at: datetime | None — parsed from the literal Claude Code
        message format when present. None means "could not compute a
        wake time"; main()'s handler then sleeps a fixed backoff and
        auto-resumes (rate-limits reset on a clock, so a premature retry
        just re-hits the same clean pause).
      - out_of_credits: bool — True only for the out-of-credits
        mid-stream kill (an exhaustion `overageDisabledReason`). This
        case does NOT auto-resume: out-of-credits has no reset clock (it
        clears on a top-up / billing cycle, not by waiting), so main()
        does worktree-only cleanup, logs a --resume hint, and exits
        EXIT_LOCKED. Looping a fixed backoff against it would only spin
        against the wall and burn the worker budget.
      - raw_message: str — the verbatim message text (or a synthesized
        envelope for the protocol-level rate_limit_event path),
        surfaced to the user on exit.

    Three main() outcomes, keyed on (out_of_credits, reset_at):
      - out_of_credits=True → pause-and-surface (EXIT_LOCKED, --resume).
      - reset_at set → sleep to the reset moment, then auto-resume.
      - reset_at None (unparseable rate-limit) → fixed-backoff auto-resume.

    See DESIGN §6 *Cleanup on abnormal exit* for the auto-resume
    contract."""
    def __init__(self, reset_at: datetime | None, raw_message: str,
                 out_of_credits: bool = False):
        super().__init__(raw_message)
        self.reset_at = reset_at
        self.raw_message = raw_message
        self.out_of_credits = out_of_credits


# Literal Claude Code subscription rate-limit message format, observed
# verbatim across three independent runs (barnacle/stackpulse/substack)
# on 2026-05-27. Format:
#   "You've hit your session limit · resets <h>:<mm><am|pm> (<IANA TZ>)"
# Match case-insensitively but require the literal prefix — broader
# patterns false-match legitimate assistant text discussing rate-
# limiting code (a worker iterating on rate-limit handling could
# legitimately write "the hot path is rate-limited"). The prefix is
# Claude-Code-specific marketing copy; no other text plausibly
# contains it.
_SESSION_LIMIT_PREFIX = re.compile(
    r"you've hit your session limit", re.IGNORECASE)
_SESSION_LIMIT_RESET = re.compile(
    r"resets?\s+(\d{1,2}):(\d{2})\s*([ap]m)\s*\(([^)]+)\)",
    re.IGNORECASE)

# Known `status` values for a `rate_limit_event` payload that mean the
# limit has NOT been hit. Anything outside this set is treated as a
# terminal rate-limit signal — defensive against future Anthropic
# status strings ("exceeded", "denied", "blocked", etc.) without
# hardcoding a guess at the terminal value.
_RATE_LIMIT_ALLOWED_STATUSES = ("allowed", "allowed_warning")


def detect_session_limit(text: str) -> RateLimitedExit | None:
    """Return a RateLimitedExit if `text` matches the Claude Code
    session-limit message format, else None. Parse failures of the
    reset clause produce an exit with reset_at=None — the run still
    exits cleanly, just without auto-resume.

    Deliberately strict on the time-parse path: a wrong sleep is worse
    than no sleep, so we only return a reset_at when every step of the
    parse succeeds (regex match, integer conversion of hour and minute,
    range checks on each, AM/PM normalization, ZoneInfo lookup).
    Anything else → reset_at=None and the user gets a manual --resume
    instruction."""
    if not _SESSION_LIMIT_PREFIX.search(text):
        return None
    reset_at: datetime | None = None
    m = _SESSION_LIMIT_RESET.search(text)
    if m:
        hour_s, minute_s, ampm, tz_name = m.group(1), m.group(2), m.group(3), m.group(4)
        try:
            tz = ZoneInfo(tz_name)
            h = int(hour_s)
            mn = int(minute_s)
            if not (0 <= mn < 60):
                raise ValueError(f"minute out of range: {mn}")
            if ampm.lower() == "pm" and h != 12:
                h += 12
            elif ampm.lower() == "am" and h == 12:
                h = 0
            if not (0 <= h < 24):
                raise ValueError(f"hour out of range: {h}")
            now = datetime.now(tz)
            candidate = now.replace(hour=h, minute=mn, second=0, microsecond=0)
            # Reset is always in the future; if the parsed time is
            # earlier than now (or equal), it's tomorrow.
            if candidate <= now:
                candidate = candidate + timedelta(days=1)
            reset_at = candidate
        except (ValueError, ZoneInfoNotFoundError):
            reset_at = None
    return RateLimitedExit(reset_at=reset_at, raw_message=text)


def _install_signal_handlers() -> None:
    """Install SIGTERM/SIGHUP handlers that raise InterruptedBySignal.
    SIGINT is left to Python's default (KeyboardInterrupt). On Windows,
    SIGHUP doesn't exist and SIGTERM behaves differently — best-effort,
    guard with hasattr."""
    def _raise_intr(signum, frame):
        raise InterruptedBySignal(signal.Signals(signum).name)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _raise_intr)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _raise_intr)


_PROC_TREE_GRACE_SEC = 2.0


def _enumerate_descendants(root_pid: int) -> set[int]:
    """Return every PID reachable from `root_pid` via PPID links.

    A flat list of (pid, ppid) is enough because PPID points to a process's
    *current* parent — even after one fork-and-detach. POSIX guarantees a
    `ps -eo pid,ppid` snapshot is consistent enough for this; the snapshot
    races a process's reparenting to init, but `_terminate_proc_tree`'s
    SIGTERM-then-SIGKILL-after-grace pattern is robust to that race (any
    process we miss in pass 1 we catch in pass 2 because PPID-walk re-runs
    after the grace window, OR — if it was already reaped — it's gone).

    Returns the set of descendant PIDs *not* including root_pid itself.
    `ps` failures (e.g. no permission) return an empty set: callers fall
    back to the leader-only kill path."""
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,ppid"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return set()
    children_of: dict[int, list[int]] = {}
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        children_of.setdefault(ppid, []).append(pid)
    seen: set[int] = set()
    stack = [root_pid]
    while stack:
        p = stack.pop()
        for c in children_of.get(p, []):
            if c not in seen:
                seen.add(c)
                stack.append(c)
    return seen


def _signal_pids(pids: set[int], sig: int) -> None:
    """Best-effort signal delivery to a set of PIDs. Drops ProcessLookupError
    (already dead) and PermissionError (not ours / already reaped). All other
    OSError variants are also swallowed — this is a cleanup path; we cannot
    let signal-delivery failure abort the teardown.

    Deliberately does NOT `waitpid` the signalled PIDs: a SIGKILLed orphan
    becomes a `<defunct>` zombie until reaped, and the central `_zombie_reaper`
    (armed by `_become_subreaper`) is the single place that reaps orphaned
    descendants — see DESIGN §6 *Zombie reaping*."""
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _reparented_orphans(seen: set[int]) -> list[int]:
    """Return PIDs from `seen` that are currently alive, reparented to
    init (ppid==1), and older than _PID_REAP_MIN_AGE_SEC seconds —
    sorted oldest-first (longest-running first, safest to kill first).

    These are the leaked background subprocesses that have finished their
    immediate work and been orphaned; unlike a recently-launched background
    test (also ppid==1 but *young*), they have been silent long enough
    that the age floor distinguishes them. Uses `ps -eo pid,ppid,etimes`
    where `etimes` is a bare elapsed-seconds integer (POSIX extension,
    verified present in the container image).

    Returns an empty list on any `ps` failure — same empty-set fallback
    as `_enumerate_descendants`."""
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,ppid,etimes"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    candidates: list[tuple[int, int]] = []  # (etimes, pid), for sorting
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            pid, ppid, etimes = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        # ppid in (1, our pid): a leaked orphan reparents to PID 1 on a plain
        # init, but after `_become_subreaper` it reparents to the orchestrator
        # itself — accept both so the mid-run reaper still recognizes orphans.
        if (pid in seen and ppid in (1, os.getpid())
                and etimes >= _PID_REAP_MIN_AGE_SEC):
            candidates.append((etimes, pid))
    # Oldest-first: killing the longest-running orphans first frees the most
    # slots while touching the fewest recently-launched background processes.
    candidates.sort(reverse=True)
    return [pid for _, pid in candidates]


_DESCENDANT_POLL_SEC = 0.5

# Mid-run reaping thresholds (DESIGN §6 *Mid-run PID reaping*).
# High-water: arm reaping when pids.current/pids.max reaches this ratio.
# Low-water: stop killing once pressure drops below this ratio (hysteresis).
# Min-age: only orphans older than this many seconds are eligible — protects
# recently-launched background tasks the worker may still be waiting on.
_PID_REAP_HIGH_WATER = 0.90
_PID_REAP_LOW_WATER = 0.75
_PID_REAP_MIN_AGE_SEC = 60

# `_invoke`'s PID-exhaustion detector probes the cgroup once the last
# _PID_EXHAUSTION_WINDOW tool-results hold ≥_PID_EXHAUSTION_ERROR_THRESHOLD
# errors (DESIGN §6 *Detecting PID exhaustion*). A *window*, not a run of
# consecutive errors: tool-results are never adjacent in the stream (the
# model's assistant turn always sits between them), so a consecutive
# counter could never exceed one. A single ordinary failing command leaves
# ≤1 error in the window and — with the confirming cgroup read — never
# trips detection; a subtree that can no longer fork fills the window fast.
# Window = double the error threshold: wide enough that an exhausted
# worker's back-to-back failures land together, narrow enough that a few
# unrelated failures scattered across a long healthy run don't accumulate
# to the threshold. Validated on the captured config-003 exhaustion trace,
# where window sizes 5 / 6 / 8 all first probe at the same point.
_PID_EXHAUSTION_WINDOW = 6
_PID_EXHAUSTION_ERROR_THRESHOLD = 3


class _DescendantTracker:
    """Background poller that accumulates every PID ever observed as a
    descendant of `leader_pid` during the leader's lifetime.

    Why this is needed (DESIGN §6): Claude Code's Bash tool uses
    `run_in_background: true` to fire-and-forget long-running commands
    (test runners, builds, dev servers). Each such command is spawned in
    its own POSIX session (detached). The bash wrapper writes the
    background task ID to the worker's stream and exits. When that
    wrapper exits, its children (the actual long-running command) are
    immediately reparented to PID 1 by the kernel.

    Result: by the time `claude -p` itself exits and leerie's `_invoke`
    can call a post-hoc `_enumerate_descendants(claude_p.pid)`, the
    backgrounded subprocesses are no longer descendants — they're
    orphans of init. A snapshot taken at exit-time finds nothing.

    Fix: take snapshots THROUGHOUT the worker's life, while the PPID
    chain is still intact, and remember every PID we ever saw. At exit
    time, SIGKILL the accumulated set. Anything that died naturally
    yields ProcessLookupError (swallowed); anything still alive gets
    reaped.

    Polling cost is negligible: ~10ms per `ps` call every 500ms ≈ 2%
    CPU during a worker's run. There is one tracker instance per
    worker; all of them share leerie's single asyncio event loop, so
    even with `max_parallel` concurrent workers the polling stays on
    one CPU."""

    def __init__(self, leader_pid: int,
                 cgroup_sid: str | None = None):
        self._leader_pid = leader_pid
        self._cgroup_sid = cgroup_sid
        self._seen: set[int] = set()
        self._task: asyncio.Task | None = None
        self._stopped = False

    def start(self) -> None:
        """Spawn the polling task on the current event loop."""
        if self._task is None:
            self._task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        try:
            while not self._stopped:
                descendants = _enumerate_descendants(self._leader_pid)
                self._seen.update(descendants)
                # Pressure-gated mid-run reaping (DESIGN §6 *Mid-run PID
                # reaping*). Only active when this tracker was constructed
                # with a cgroup_sid; otherwise the branch is a no-op and
                # behavior is byte-identical to before.
                if self._cgroup_sid is not None:
                    stat = _cgroup_stat(self._cgroup_sid)
                    if stat is not None:
                        cur, mx, _ev = stat
                        if mx > 0 and cur / mx >= _PID_REAP_HIGH_WATER:
                            # Armed: pressure is high — reap oldest reparented
                            # orphans first, stopping once pressure drops.
                            candidates = _reparented_orphans(self._seen)
                            killed: set[int] = set()
                            for pid in candidates:
                                recheck = _cgroup_stat(self._cgroup_sid)
                                if recheck is None:
                                    break
                                rc, rm, _ = recheck
                                if rm <= 0 or rc / rm < _PID_REAP_LOW_WATER:
                                    break
                                _signal_pids({pid}, signal.SIGKILL)
                                killed.add(pid)
                            # Prune killed PIDs from _seen so stop_and_reap
                            # doesn't double-signal them (harmless but noisy).
                            self._seen -= killed
                if not descendants and self._seen:
                    # Worker has been alive long enough to spawn at
                    # least one descendant AND that descendant is now
                    # gone (reparented to init, or naturally exited).
                    # Slow down to save ps calls during the worker's
                    # winding-down phase — our accumulated `_seen` set
                    # already holds the orphan PIDs we'll SIGKILL at
                    # stop_and_reap.
                    await asyncio.sleep(_DESCENDANT_POLL_SEC * 2)
                else:
                    # Either descendants ARE present (keep watching at
                    # full rate), or we have NEVER seen one yet (the
                    # leader may not have spawned its first child yet —
                    # slowing down now would miss the first batch as
                    # they appear). Stay at the fast poll rate.
                    await asyncio.sleep(_DESCENDANT_POLL_SEC)
        except asyncio.CancelledError:
            return

    async def stop_and_reap(self) -> int:
        """Stop polling, SIGKILL every accumulated PID, return the
        count signaled. Safe to call multiple times. Always runs at
        worker exit, success-path AND failure-path."""
        self._stopped = True
        if self._task is not None:
            # One final snapshot to catch anything spawned since the
            # last poll cycle (still cheap — one `ps` call).
            self._seen.update(_enumerate_descendants(self._leader_pid))
            # Fire-and-forget cancel: the poll loop notices `_stopped`
            # at its next iteration and exits cleanly. We don't `await`
            # the cancelled task here because doing so would block at
            # an `await` point, and a `CancelledError` propagating from
            # the caller would be silently caught by any local
            # exception handler — breaking asyncio's cancellation
            # contract. The orphaned task is harmless; the event loop
            # reaps it on shutdown.
            self._task.cancel()
            self._task = None
        if self._seen:
            _signal_pids(self._seen, signal.SIGKILL)
        return len(self._seen)


async def _terminate_proc_tree(proc: asyncio.subprocess.Process) -> None:
    """Terminate a subprocess AND every descendant process, then reap.

    Why a PPID-walk (not just `killpg`): Claude Code's Bash tool runs each
    command via `bash -c` started in a *new POSIX session* (own PGID).
    `os.killpg(claude_p_pgid)` does NOT reach those detached subprocesses
    because they no longer share `claude -p`'s process group. The PPID chain
    however stays intact while the parent lives, so a recursive walk through
    `ps -eo pid,ppid` reaches every descendant regardless of how many session
    layers separate them.

    Algorithm: SIGTERM the leader's process group AND every descendant we can
    enumerate; wait the grace window so well-behaved children flush; re-snapshot
    (catches anything spawned mid-teardown OR not visible in pass 1);
    SIGKILL the remainder; reap the leader. Init reaps any descendants we
    cannot, once they exit.

    Idempotent and exception-safe: this runs only from `_invoke`'s and
    `run_proc`'s `except` blocks (abnormal-exit paths). Success-path
    cleanup of detached backgrounded subprocesses (Claude Code's Bash
    tool with `run_in_background: true`) is handled separately by
    `_DescendantTracker`, because by the time a clean `claude -p` exit
    is observable to leerie those subprocesses have already reparented to
    PID 1 and are no longer reachable from this helper's PPID walk.
    All signal-delivery races (process already gone, PGID recycled)
    are swallowed; the helper never raises.

    `asyncio.CancelledError` propagates out unhandled, AFTER the SIGKILL pass
    has fired in `finally`. Swallowing cancellation here would silently break
    asyncio teardown — callers' outer `raise` would still fire, but the
    event loop shutdown path expects `CancelledError` to surface."""
    leader_pid = proc.pid
    leader_pgid = proc.pid  # PGID == PID when spawned with start_new_session=True
    # Pass 1: enumerate descendants while parent is still alive (PPID chain
    # intact), then signal everything we found AND the leader's PG.
    descendants = _enumerate_descendants(leader_pid)
    try:
        os.killpg(leader_pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    _signal_pids(descendants, signal.SIGTERM)

    exited_cleanly = False
    try:
        await asyncio.wait_for(proc.wait(), timeout=_PROC_TREE_GRACE_SEC)
        exited_cleanly = True
    except asyncio.TimeoutError:
        pass
    finally:
        # Re-enumerate. Anything we missed in pass 1 (spawned in the gap,
        # or reparented before we read /proc), AND anything that ignored
        # SIGTERM, gets SIGKILLed here. We always run this pass, even on
        # `exited_cleanly` — the leader may be reaped but its detached
        # grandchildren are not in its PGID, so its exit doesn't take them
        # with it.
        survivors = _enumerate_descendants(leader_pid)
        try:
            os.killpg(leader_pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        _signal_pids(survivors, signal.SIGKILL)
        if not exited_cleanly:
            # Reap the leader so the OS doesn't accumulate a zombie.
            # `shield` keeps the reap running if the caller's task gets
            # cancelled mid-wait; the cancellation still surfaces.
            try:
                await asyncio.shield(proc.wait())
            except (ProcessLookupError, PermissionError, OSError):
                pass


def _cleanup_on_abnormal_exit(st: "State", *, full_purge: bool) -> None:
    """Clean up after an abnormal exit (signal, exception, WorkerError).

    Always: remove every git worktree under `st.run_dir / "worktrees"`,
    then `git worktree prune` to clear stale metadata. Per-worktree
    failures are caught — one bad worktree shouldn't block the others.

    If `full_purge` is True (the user's explicit Ctrl-C gesture):
    additionally delete the run branch (`leerie/runs/<run-id>`) and
    every subtask branch (`leerie/subtasks/<run-id>/*`), and
    recursively remove `st.run_dir`. The run is gone; `--resume` can't
    recover it.

    If `full_purge` is False (SIGTERM/SIGHUP/exception): state.json and
    the run branch are left intact so `--resume <id>` can
    continue the run. This is the conservative default for "external
    process killed me, user probably wants to recover.\""""
    if st is None or st.run_id is None:
        return
    worktrees_dir = st.run_dir / "worktrees"
    has_worktrees = worktrees_dir.is_dir() and any(worktrees_dir.iterdir())
    # full_purge requires a log line (it's removing the whole run dir);
    # worktrees-only with nothing to do is silent (e.g., preflight died
    # before setup-run.sh — no worktrees ever existed).
    if full_purge or has_worktrees:
        log(f"cleanup: {'full purge' if full_purge else 'worktrees only'} "
            f"for run {st.run_id}")
    # Remove worktrees. The 240s timeout is calibrated for realistic
    # worker workloads: a 868 MB / 41k-file worktree (npm install +
    # Next.js build) takes ~45-90s uncontested; under N-way concurrent
    # cleanup (e.g. 6 worktrees from a multi-subtask wave), per-worktree
    # time grows several-fold via disk contention. 240s covers the
    # observed worst-case + room for a 2-3 GB monorepo. Still bounded
    # so a genuinely hung git command (not just a slow rm-rf) doesn't
    # block cleanup indefinitely. Per-worktree failures are non-fatal —
    # the loop logs and continues — and a closing recovery-hint line
    # tells the user how to finish manually.
    worktree_remove_timeout = 240
    failed_removals = 0
    worktrees_dir_resolved = worktrees_dir.resolve() if worktrees_dir.is_dir() else None
    if worktrees_dir.is_dir():
        for entry in worktrees_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(entry)],
                    capture_output=True, check=False,
                    timeout=worktree_remove_timeout,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                log(f"  cleanup: git worktree remove failed for {entry}: {e}")
            # Fall back to direct removal if the directory still exists.
            # Two real cases this catches:
            #   1) `git worktree remove` succeeded administratively
            #      (deregistered from git) but timed out mid-rmtree —
            #      directory survives with partial contents.
            #   2) git no longer tracks the worktree (already pruned)
            #      so `git worktree remove` returns nonzero without
            #      raising — directory survives untouched.
            # The user hit case 2 after an overnight run that crashed
            # while the old 30s timeout was still in place: cleanup
            # logged "failed", git later pruned the entry on its own
            # bookkeeping pass, and the surviving directory blocked
            # `--resume`'s new-worktree.sh from re-creating the
            # worktree at the same path. Safe to rm because the path
            # is sandboxed under <state-root>/runs/<run-id>/worktrees/<sid>;
            # we re-check via .resolve() to make sure a symlink or
            # refactor hasn't escaped the sandbox.
            if entry.exists():
                try:
                    resolved = entry.resolve()
                    if (worktrees_dir_resolved is not None
                            and resolved.parent == worktrees_dir_resolved):
                        shutil.rmtree(entry, ignore_errors=True)
                except OSError as e:
                    log(f"  cleanup: fallback rm failed for {entry}: {e}")
            if entry.exists():
                failed_removals += 1
                log(f"  cleanup: worktree {entry} survived removal")
    if failed_removals:
        log(f"  cleanup: {failed_removals} worktree(s) not removed within "
            f"{worktree_remove_timeout}s — run "
            f"`scripts/cleanup.sh --run-id {st.run_id}` to finish manually")
    try:
        subprocess.run(["git", "worktree", "prune"],
                       capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not full_purge:
        return
    # Full purge: delete branches and the run dir. The run branch lives
    # at leerie/runs/<run-id> and subtask branches under
    # leerie/subtasks/<run-id>/<sid> — see compute_run_branch for the
    # namespace-disjointness rationale.
    branch_globs = [
        f"refs/heads/leerie/runs/{st.run_id}",
        f"refs/heads/leerie/subtasks/{st.run_id}/",
    ]
    for glob in branch_globs:
        r = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname:short)", glob],
            capture_output=True, text=True, check=False, timeout=10,
        )
        if r.returncode != 0:
            continue
        for ref in r.stdout.splitlines():
            ref = ref.strip()
            if not ref:
                continue
            subprocess.run(
                ["git", "branch", "-D", ref],
                capture_output=True, check=False, timeout=10,
            )
    if st.run_dir.exists():
        shutil.rmtree(st.run_dir, ignore_errors=True)


async def _reset_subtask_worktree(sid: str, leerie_dir: Path, run_id: str) -> None:
    """Remove the per-subtask worktree directory and branch so a corrective
    retry can start clean from `new-worktree.sh`'s "fresh subtask" path.
    Without this, retrying after a `complete`-with-no-commits failure
    re-runs the script against a still-registered worktree and an existing
    branch — the second `git worktree add -b` fails with
    `fatal: a branch ... already exists`, the WorkerError escapes
    settle_subtask, and gather_or_cancel takes down the whole wave.

    Tolerates either being absent: both `git worktree remove --force`
    and `git branch -D` return nonzero when their target is missing,
    and that is the expected idempotent case. Mirrors the rmtree
    fallback in `_cleanup_on_abnormal_exit` for the case where git
    administratively succeeded but left the directory behind."""
    worktree = leerie_dir / "worktrees" / sid
    branch = f"leerie/subtasks/{run_id}/{sid}"
    await run_proc(["git", "worktree", "remove", "--force", str(worktree)])
    await run_proc(["git", "branch", "-D", branch])
    if worktree.exists():
        try:
            shutil.rmtree(worktree, ignore_errors=True)
        except OSError:
            pass
    await run_proc(["git", "worktree", "prune"])


def _sleep_then_reexec(st: "State", wait_seconds: int, reason: str) -> int | None:
    """Shared tail of the rate-limit auto-resume path (DESIGN §6 *Rate-limited
    → auto-resume*): worktree-only cleanup (state + run branch preserved),
    sleep `wait_seconds`, then `os.execv` the orchestrator into a fresh
    `--resume` process.

    Returns None when it does not return (the `os.execv` succeeded and replaced
    the process — unreachable code after it). Returns an **exit code** when the
    sleep or re-exec was interrupted/failed instead of resuming, so the caller
    sets `exit_code` and returns through main()'s normal flow:
      - Ctrl-C (SIGINT) during the sleep → 130
      - SIGTERM/SIGHUP during the sleep → 128 + signum (143 / 129)
      - `os.execv` itself failed (should-never-happen; e.g. ENOENT on the
        interpreter) → 75 (EX_TEMPFAIL) — state is preserved, so a manual
        `--resume` recovers.
    In every interrupted/failed case cleanup has already run, so state and the
    run branch are intact for a manual `--resume`; the caller must NOT re-run
    cleanup (it leaves `abnormal=False`).

    Cleanup runs BEFORE the sleep so the re-exec'd `--resume` finds a clean
    slate — `_cleanup_on_abnormal_exit` removes every worktree (git-registered
    AND orphaned dirs, then `git worktree prune`), so `setup-run.sh`'s staging
    worktree re-creation can't hit a stale-dir conflict. A consequence: the
    sleep is measured from AFTER cleanup, so for a parsed reset time the wait
    under-counts by the cleanup duration. That is harmless and self-correcting:
    if it wakes early the re-exec'd run immediately re-hits `RateLimitedExit`,
    re-computes the (now-shorter) wait, and sleeps again — bounded by the
    persisted `max_total_workers` budget.

    We re-exec the orchestrator, not the launcher: the launcher is not baked
    into the container image and would try to spawn a new container. Re-execing
    `$python $__file__ --resume --run-id <id>` gives a fresh worker budget and a
    fresh asyncio loop; `worker_count` persists in state.json so the
    `max_total_workers` cap survives across re-exec."""
    try:
        _cleanup_on_abnormal_exit(st, full_purge=False)
    except BaseException as ce:
        log(f"  cleanup before sleep failed (non-fatal): {ce}")
    log(f"  {reason} — sleeping {wait_seconds}s then auto-resuming; "
        f"Ctrl-C to stop and resume manually (leerie --resume {st.run_id})")
    try:
        time.sleep(wait_seconds)
    except KeyboardInterrupt:
        # User bailed on the wait — state + branches already preserved (cleanup
        # ran above), so a manual --resume picks up cleanly.
        log("interrupted by user (SIGINT) during rate-limit sleep — state "
            f"preserved (resume with leerie --resume {st.run_id})")
        return 130
    except InterruptedBySignal as e:
        # SIGTERM/SIGHUP during the wait (CI cancel, systemd stop, terminal
        # close). Same preserved-state guarantee; map to 128+signum the way
        # main()'s top-level InterruptedBySignal arm does, instead of letting it
        # escape uncaught (a sibling except can't catch it) as a bare traceback.
        log(f"  interrupted by signal ({e}) during rate-limit sleep — state "
            f"preserved (resume with leerie --resume {st.run_id})")
        signum = getattr(signal, str(e), None)
        return (128 + int(signum)) if signum else 1
    log(f"  auto-resuming: exec orchestrator --resume {st.run_id}")
    try:
        os.execv(sys.executable,
                 [sys.executable, __file__, "--resume", "--run-id", st.run_id])
    except OSError as ex:
        # execv essentially never fails (the interpreter that's running us
        # exists), but if it does the exception would otherwise escape past the
        # sibling except arms as a bare traceback. Cleanup already ran, so fall
        # back to the manual-resume path with EX_TEMPFAIL.
        log(f"  auto-resume exec failed ({ex}); resume manually: "
            f"leerie --resume {st.run_id}")
        return EXIT_LOCKED  # 75, EX_TEMPFAIL
    # Unreachable: a successful execv replaces the process.
    return None


def _parse_claude_version(version_output: str | None) -> tuple[int, int, int] | None:
    """Pull MAJOR.MINOR.PATCH out of `claude --version` output.
    Returns None if the format is unrecognized — caller falls through to
    the live smoke test rather than failing closed on a regex."""
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", (version_output or "").strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def _check_claude_cli_version() -> None:
    """die() if `claude` is too old for --json-schema. Without this, a
    stale CLI surfaces as a cryptic 'unknown option' wrapped in the
    smoke-test error path — actionable for nobody. Existence on PATH is
    already enforced earlier in main() via shutil.which().

    Timeout is 30 s (not 10 s) because on a freshly-provisioned Fly
    machine the first `claude --version` invocation can take ~17 s —
    the Node runtime warms up, statsig fetches feature flags, etc.
    Subsequent calls return in <0.2 s. 30 s is a generous ceiling
    above the observed cold-start time."""
    try:
        out = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except subprocess.TimeoutExpired:
        die("`claude --version` timed out — investigate the CLI install.")
    found = _parse_claude_version(out.stdout)
    if found is None:
        return  # unrecognized format — defer to smoke test
    if found < MIN_CLAUDE_CLI:
        die(
            f"claude CLI {'.'.join(map(str, found))} is too old; leerie "
            f"requires >= {'.'.join(map(str, MIN_CLAUDE_CLI))} for "
            "--json-schema (introduced for `claude -p` in v2.1.22). "
            "Upgrade with the native installer: "
            "`curl -fsSL https://claude.ai/install.sh | bash`. "
            "(npm/pnpm installs are now an advanced/legacy option per the "
            "Claude Code docs.)"
        )



# --- run identifier (DESIGN §6 "The run identifier") --------------------
#
# A run_id is the container/machine ID assigned by the container runtime:
# Fly machine ID for --runtime fly, nerdctl container ID for local runs.
# The launcher passes it to the orchestrator via --run-id; the orchestrator
# never generates its own. The same string appears in three places: branch
# name (`leerie/runs/<run-id>`), state dir (`<state-root>/runs/<run-id>/`),
# and PR body.


def compute_run_branch(run_id: str) -> str:
    """The git branch name carrying a run's integrated work.

    The `leerie/runs/` prefix is **mandatory**, not cosmetic. Subtask
    branches live under the sibling prefix `leerie/subtasks/<run-id>/<sid>`
    (see `compute_subtask_branch`). Git's loose ref store represents each
    ref as a file inside `refs/heads/…/`, so a ref AT a path and a ref
    UNDER that same path cannot coexist. If both lived under
    `leerie/<run-id>` the first `git worktree add` for a subtask would
    fail with `cannot lock ref …`. The disjoint `runs/` and `subtasks/`
    sub-namespaces make that collision structurally impossible."""
    return f"leerie/runs/{run_id}"


def compute_subtask_branch(run_id: str, sid: str) -> str:
    """The git branch name for one subtask's worktree.

    Paired with `compute_run_branch` — see that function for the
    namespace-disjointness rationale. The bash side
    (`scripts/new-worktree.sh`, `scripts/integrate.sh`) constructs the
    same string; this helper exists so the shape is grep-able from
    Python and any future Python call site that needs a subtask branch
    name goes through one function."""
    return f"leerie/subtasks/{run_id}/{sid}"


# --- run.json sidecar invariants (IMPLEMENTATION.md §8) -----------------

def _validate_run_json(data: dict) -> None:
    """Enforce the logical invariants on a `run.json` sidecar.

    1. `pushed_at` and `push_error` are mutually exclusive (at most one
       is non-null).
    2. `pr_url` and `pr_error` are mutually exclusive.
    3. If `pr_url` is set, `pushed_at` must be set (cannot have a PR
       without a successful push).
    4. `paused_at`, `pushed_at`, and `killed_at` are mutually exclusive
       (a run cannot be in more than one terminal-or-paused state).
    5. If `paused_at` is set, `fly_machine_id` must also be set — you
       cannot pause a run without knowing where to resume it.
    6. If `killed_at` is set, `fly_machine_id` must also be set — you
       cannot have destroyed a machine you don't have a pointer to.
    7. If `volume_id` is set, `fly_machine_id` must also be set — a Fly
       volume without a machine to attach it to is a corrupt sidecar
       (provision.sh writes the two together; the only way to violate
       this is external mutation).

    Raises ValueError on any violation. Caller (e.g., `leerie --list`)
    decides whether to die, warn, or render as `status=corrupt-sidecar`."""
    if not isinstance(data, dict):
        raise ValueError("run.json must be a JSON object")
    pushed_at = data.get("pushed_at")
    push_error = data.get("push_error")
    pr_url = data.get("pr_url")
    pr_error = data.get("pr_error")
    paused_at = data.get("paused_at")
    killed_at = data.get("killed_at")
    fly_machine_id = data.get("fly_machine_id")
    if pushed_at is not None and push_error is not None:
        raise ValueError(
            "run.json invariant: pushed_at and push_error are both set; "
            "exactly one must be null"
        )
    if pr_url is not None and pr_error is not None:
        raise ValueError(
            "run.json invariant: pr_url and pr_error are both set; "
            "exactly one must be null"
        )
    if pr_url is not None and pushed_at is None:
        raise ValueError(
            "run.json invariant: pr_url is set but pushed_at is null; "
            "PR cannot succeed without a successful push"
        )
    # Mutual-exclusion across the three terminal/paused markers.
    _set_states = sum(1 for v in (paused_at, pushed_at, killed_at) if v is not None)
    if _set_states > 1:
        raise ValueError(
            "run.json invariant: paused_at / pushed_at / killed_at are mutually "
            "exclusive; at most one may be non-null"
        )
    if paused_at is not None and fly_machine_id is None:
        raise ValueError(
            "run.json invariant: paused_at is set but fly_machine_id is null; "
            "you cannot pause a run without knowing where to resume it"
        )
    if killed_at is not None and fly_machine_id is None:
        raise ValueError(
            "run.json invariant: killed_at is set but fly_machine_id is null; "
            "you cannot have destroyed a machine you don't have a pointer to"
        )
    # `sync_failed_at`: set by decide_teardown's clean-exit branch when
    # fetch_branch fails. The machine is left RUNNING (work-preserving)
    # and the user is told to recover manually + then `leerie --kill`.
    # Orthogonal to paused/pushed/killed — the machine isn't paused or
    # killed, just has un-synced work — so no mutual exclusion with the
    # other terminal states. Mutex'd against pushed/killed (a synced+
    # pushed run cannot also be sync-failed; a destroyed machine cannot
    # be sync-failed).
    sync_failed_at = data.get("sync_failed_at")
    if sync_failed_at is not None and pushed_at is not None:
        raise ValueError(
            "run.json invariant: sync_failed_at and pushed_at are both set; "
            "a successfully pushed run cannot also be sync-failed"
        )
    if sync_failed_at is not None and killed_at is not None:
        raise ValueError(
            "run.json invariant: sync_failed_at and killed_at are both set; "
            "a destroyed machine cannot also be sync-failed"
        )
    if sync_failed_at is not None and fly_machine_id is None:
        raise ValueError(
            "run.json invariant: sync_failed_at is set but fly_machine_id is null; "
            "the running machine needs a pointer for the user to recover via --finalize/--kill"
        )
    # `volume_id`: written by provision.sh when FLY_VM_DISK_GB is set.
    # The provision path always writes volume_id and fly_machine_id
    # together (provision.sh:574-580), so this invariant cannot be
    # violated by anything leerie ships — it's a defense against
    # external mutation or a corrupted run dir.
    volume_id = data.get("volume_id")
    if volume_id is not None and fly_machine_id is None:
        raise ValueError(
            "run.json invariant: volume_id is set but fly_machine_id is null; "
            "a Fly volume without a machine to attach it to is invalid"
        )


# --- PR body composition (DESIGN §6 "Finalization") ---------------------

def _format_run_duration(started_at: str | None, finished_at: str | None) -> str | None:
    """Return a human-readable elapsed duration like '3m 42s' or '1h 12m'.

    Returns None when either timestamp is absent or unparseable so callers
    can fall back to 'n/a' without a crash."""
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(finished_at)
        total = int((end - start).total_seconds())
        if total < 0:
            return None
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"
    except (ValueError, TypeError):
        return None


def compose_pr_body(state: dict, run_id: str) -> str:
    """Generate the deterministic fallback PR body from run state +
    run_id. No I/O.

    DESIGN §6 *Finalization*: this is the **fail-open fallback**. The
    primary PR body is now written by the `pr_writer` LLM worker (see
    `_compose_pr_via_llm`) and lives in `run.json` under `pr_body`; the
    host launcher uses that when present and only falls back to this
    deterministic shape when the worker errored or returned nothing.
    The bash launcher carries a structurally equivalent fallback inline
    so the launcher does not need to call back into Python; this
    function remains the canonical reference for the fallback's shape.

    Missing optional fields render as 'n/a' rather than the literal
    string 'None' — Python's f-string default would produce 'None' for
    a missing `finished_at`, which is unhelpful in a PR body."""
    def _or_na(value) -> str:
        return "n/a" if value in (None, "") else str(value)

    task = state.get("task", "")
    categories = state.get("categories") or []
    first_cat = categories[0] if categories else None
    answers = state.get("answers") or {}
    source_of_truth = answers.get("source_of_truth")
    started_at = state.get("started_at")
    finished_at = state.get("finished_at")
    waves = state.get("waves") or []
    wave_count = len(waves)
    subtask_count = sum(len(w) for w in waves)
    worker_count = state.get("worker_count")
    working_branch = state.get("working_branch")
    leerie_version = state.get("leerie_version")
    version_suffix = f" v{leerie_version}" if leerie_version else ""
    duration = _format_run_duration(started_at, finished_at)
    # Cost line — rendered only when the telemetry aggregate is present (it is
    # absent on pre-classify orphans). Keep the bash fallback in
    # scripts/host-finalize.sh structurally equivalent.
    tel = state.get("telemetry") or {}
    cost_line = (
        f"- Cost: ${tel.get('cost_usd', 0.0):,.2f} "
        f"({tel.get('calls', 0)} calls, "
        f"{tel.get('input_tokens', 0):,} in / "
        f"{tel.get('output_tokens', 0):,} out tokens)\n"
    ) if tel else ""
    body = (
        "## Task\n"
        "\n"
        f"{task}\n"
        "\n"
        "## Classification\n"
        "\n"
        f"- Category: {_or_na(first_cat)}\n"
        f"- Source of truth: {_or_na(source_of_truth)}\n"
        "\n"
        "## Run summary\n"
        "\n"
        f"- Run ID: {run_id}\n"
        f"- Started: {_or_na(started_at)}\n"
        f"- Finished: {_or_na(finished_at)}\n"
        f"- Duration: {_or_na(duration)}\n"
        f"- Waves: {wave_count}, subtasks: {subtask_count}\n"
        f"- Workers: {_or_na(worker_count)}\n"
        f"{cost_line}"
        f"- Generated by [leerie{version_suffix}](https://github.com/enricai/leerie) on `{_or_na(working_branch)}`.\n"
        "\n"
        f"Run `leerie --list` on the host that produced this PR to "
        f"locate full run state (state.json) for run ID `{run_id}`.\n"
    )
    preconditions = state.get("external_preconditions") or []
    if preconditions:
        body += "\n## ⚠ Deploy-ordering\n\n"
        body += ("One or more cross-repo prerequisites were declared by the "
                 "planner. Merge and deploy the following before merging this PR:\n\n")
        for entry in preconditions:
            tag = entry.get("tag") or "(unknown)"
            reasons = entry.get("reasons") or []
            reason_texts = [r.get("reason", "") for r in reasons if r.get("reason")]
            if reason_texts:
                combined = "; ".join(reason_texts)
                body += f"- **{tag}** — {combined}\n"
            else:
                body += f"- **{tag}**\n"
    return body


def _write_run_json(run_dir: Path, **fields) -> None:
    """Merge fields into the run.json sidecar at `run_dir/run.json`,
    validate the result, and write atomically.

    Reads existing sidecar (if any), applies `fields` on top, validates
    via `_validate_run_json`, then writes via temp-file rename. Same
    atomicity pattern as `State.save()`. Fields with value `None` are
    written through as null (used to clear a previous error / status).

    Designed to be called at every push/PR state transition: run start,
    finalize success, push success, push failure, PR success, PR
    failure. Each call is idempotent given the same inputs."""
    sidecar = run_dir / "run.json"
    data: dict = {}
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text())
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
    data.update(fields)
    _validate_run_json(data)
    tmp = sidecar.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(sidecar)


# --- run discovery and resolution (DESIGN §6 multi-run resume) ----------

def discover_runs(leerie_root: Path) -> list[dict]:
    """Enumerate `<state-root>/runs/*/state.json`, returning one summary
    dict per discovered run. Malformed state.json files are skipped with
    a logged warning, never raising.

    Also enumerate **orphan** run dirs: directories that have a
    `fly-machine.json` (written by the launcher the moment Fly machine
    provision succeeds, before any orchestrator code runs) but no
    `state.json` (the orchestrator never wrote one — typically because
    `seed_auth` failed before `phase_classify` completed). These are
    returned with `_orphan=True` and `started_at` synthesized from
    `fly-machine.json`, so `--list` shows them and `resolve_run_id`
    accepts them for `--resume`. Without this, runs that died during
    the initial seed are invisible to both commands and the user has no
    way to recover them other than `ls <state-root>/runs/` + manual machine
    cleanup. (See plan file 2026-06-04 incident: 3 of 4 hung runs hid
    here.)

    Returned dicts have one of two shapes:

    - **Normal (state.json present):** at least `run_id` (directory
      name), `path` (state.json path), `task`, `started_at`,
      `finished_at`, `categories`. Other state.json fields are passed
      through unchanged.
    - **Orphan (`fly-machine.json` only, `_orphan=True`):** exactly
      `run_id` (directory name), `path` (fly-machine.json path),
      `started_at` (copied from fly-machine.json; may be the empty
      string), `_orphan: True`. Fields like `task`, `finished_at`,
      `categories` are NOT present — they were never written. Callers
      that consume both shapes must branch on `state.get("_orphan")`
      before accessing state-only fields, or use `state.get(<key>)`
      with a default.

    Both shapes sort together by `started_at` descending (newest
    first) for stable display in `leerie --list`.

    Pure read; no writes. Returns [] if `leerie_root/runs` doesn't
    exist."""
    runs_dir = leerie_root / "runs"
    if not runs_dir.is_dir():
        return []
    out: list[dict] = []
    for entry in runs_dir.iterdir():
        if not entry.is_dir():
            continue
        state_path = entry / "state.json"
        if not state_path.is_file():
            # Orphan check: dir with fly-machine.json but no state.json
            # is a pre-classify failure (seed_auth aborted before the
            # orchestrator wrote state.json). Surface it so `--list`
            # and `--resume` can reach it. Skip if neither sidecar
            # exists — empty dirs are not runs.
            fly_path = entry / "fly-machine.json"
            if not fly_path.is_file():
                continue
            try:
                fly_data = json.loads(fly_path.read_text())
            except (OSError, ValueError) as e:
                log(f"warning: skipping malformed fly-machine.json at "
                    f"{fly_path}: {e}")
                continue
            if not isinstance(fly_data, dict):
                log(f"warning: fly-machine.json at {fly_path} is not a "
                    f"JSON object")
                continue
            summary = {
                "run_id": entry.name,
                "path": str(fly_path),
                "started_at": fly_data.get("started_at") or "",
                "_orphan": True,
            }
            out.append(summary)
            continue
        try:
            data = json.loads(state_path.read_text())
        except (OSError, ValueError) as e:
            log(f"warning: skipping malformed state.json at {state_path}: {e}")
            continue
        if not isinstance(data, dict):
            log(f"warning: state.json at {state_path} is not a JSON object")
            continue
        summary = dict(data)
        summary["run_id"] = entry.name
        summary["path"] = str(state_path)
        out.append(summary)
    # Newest first. Empty / missing `started_at` sorts last.
    out.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return out


def resolve_run_id(leerie_root: Path, cli_run_id: str | None) -> str:
    """Pick the run_id to operate on. Used by `--resume`.

    Policy (DESIGN §6 "the run branch is the resume contract"):
    - If `cli_run_id` is given, it must exactly match an existing run.
      Otherwise die with the available list (fails closed).
    - Elif exactly one run exists, use it. Preserves the common case
      where there's only one run in flight.
    - Else die: multiple runs and no `--run-id` is ambiguous.

    Never guesses across multiple runs. `--resume` against an ambiguous
    repo is a hard error, not a heuristic."""
    runs = discover_runs(leerie_root)
    if cli_run_id is not None:
        for r in runs:
            if r["run_id"] == cli_run_id:
                return cli_run_id
        available = ", ".join(r["run_id"] for r in runs) or "(none)"
        die(
            f"run-id {cli_run_id!r} does not match any known run. "
            f"Available: {available}. Use `leerie --list` to enumerate."
        )
    if not runs:
        die(
            f"no runs found under {leerie_root}/runs/. Start a new run with "
            "`./leerie \"<task>\"`."
        )
    if len(runs) == 1:
        return runs[0]["run_id"]
    available = "\n  ".join(_format_run_for_disambiguation(r, leerie_root)
                            for r in runs)
    die(
        "multiple runs present; pass the run-id to disambiguate:\n  "
        f"{available}\nUse `leerie --list` to see full details."
    )


def _format_run_for_disambiguation(run: dict, leerie_root: Path) -> str:
    """Build the per-row hint string for `resolve_run_id`'s
    multiple-runs error message. Combines run_id, derived status,
    started_at, and a last-activity time so the user can tell which
    run is live without an extra `leerie --list` invocation.

    Reads run.json from disk for `_derive_run_status` (same source
    `leerie --list` consults). Falls back gracefully when sidecar or
    state.json is unreadable — disambiguation is best-effort UX, not
    a correctness boundary."""
    run_id = run["run_id"]
    started = run.get("started_at") or "?"
    # Derived status — uses run.json sidecar if present, falls back to
    # state.json fields. Same pattern as list_runs().
    run_dir = leerie_root / "runs" / run_id
    run_json: dict | None = None
    sidecar = run_dir / "run.json"
    if sidecar.is_file():
        try:
            parsed = json.loads(sidecar.read_text())
            if isinstance(parsed, dict):
                run_json = parsed
        except (OSError, ValueError):
            pass
    status = _derive_run_status(run_json, run)
    # Last-activity: mtime of the run's sidecar (state.json for normal
    # runs, fly-machine.json for `seed-failed` orphans), formatted as
    # the elapsed duration from now. A live run shows seconds-to-
    # minutes; a hung or abandoned run shows hours-to-days.
    last_activity = "?"
    sidecar_path = run.get("path")
    if sidecar_path:
        try:
            mtime = os.path.getmtime(sidecar_path)
            last_activity = _format_age(datetime.now(timezone.utc).timestamp()
                                        - mtime)
        except (OSError, ValueError, OverflowError):
            # OSError: the sidecar (state.json or fly-machine.json) was
            # deleted between discover_runs and now.
            # ValueError/OverflowError: pathological mtime (NaN, inf) that
            # _format_age's int() would reject. Both are extremely unlikely
            # in practice; this is defense-in-depth so a one-in-a-million
            # filesystem quirk can't crash --resume startup.
            pass
    return (f"{run_id}  status={status}  started={started}  "
            f"last-activity={last_activity}")


def _format_age(seconds: float) -> str:
    """Render a duration in seconds as a short human-friendly age:
    "5s", "3m", "47m", "2h12m", "1d4h", "5d". Used by the --resume
    disambiguation hint to show how stale each in-flight run is."""
    if seconds < 0:
        seconds = 0
    s = int(seconds)
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        h, m = divmod(s, 3600)
        m //= 60
        return f"{h}h{m:02d}m ago" if m else f"{h}h ago"
    d, h = divmod(s, 86400)
    h //= 3600
    return f"{d}d{h}h ago" if h else f"{d}d ago"


# --- run status (consumed by `leerie --list`) -------------------------

# The derived statuses returned by `_derive_run_status`. Status is
# *derived* from run.json + state.json fields, not stored, so the value
# rendered by --list is always consistent with the actual on-disk state.
#
# `seed-failed` is special: it covers run dirs that have a
# fly-machine.json (machine was provisioned) but no state.json (the
# orchestrator never wrote one — typically seed_auth aborted before
# phase_classify). Surfaced by `discover_runs` as `_orphan=True`,
# matched by the earliest check in `_derive_run_status`. See plan file
# for the 2026-06-04 incident where this status would have rescued
# three runs that hid behind the prior "no state.json → skip" rule.
RUN_STATUSES = (
    "seed-failed",
    "corrupt-sidecar",
    "in-progress",
    "incomplete",
    "done",
    "done-pushed-no-pr",
    "done-pushed-pr",
    "push-failed",
    "pr-failed",
    "paused",
    "killed",
    "sync-failed",
)


def _derive_run_status(run_json: dict | None, state_json: dict | None) -> str:
    """Pure function: derive a run's status from run.json + state.json.

    Status is the run's lifecycle dimension; runtime (local vs fly) is a
    separate axis surfaced via `fly_machine_id` and filtered with
    `--list --runtime <local|fly>`.

    Order of checks matters — earlier checks fire first:
      0. state_json["_orphan"] set → `seed-failed` (synthesized by
                                     `discover_runs` for run dirs that
                                     have fly-machine.json but no
                                     state.json; seed_auth aborted
                                     before phase_classify).
      1. run.json invariant-invalid → `corrupt-sidecar`.
      2. push_error set            → `push-failed`.
      3. pr_error set              → `pr-failed`.
      4. pr_url set                → `done-pushed-pr`.
      5. pushed_at set             → `done-pushed-no-pr`.
      6. sync_failed_at set        → `sync-failed` (machine still up, work
                                     not on host yet — recover via
                                     `leerie --finalize` then `--kill`).
      6½. finished_at set but
          completed_waves <
          len(waves), and no
          killed_at/paused_at      → `incomplete` (run died mid-wave; the
                                     die-path handler stamped finished_at
                                     for discovery but the waves are not
                                     all integrated — resumable). See
                                     DESIGN §6 *finished_at is a discovery
                                     sentinel, not a completion signal*.
      7. finished_at set           → `done` (run completed, --no-push).
      8. killed_at set             → `killed` (explicit --kill).
      9. paused_at set             → `paused` (pause-on-failure or --stop).
     10. otherwise                 → `in-progress`.

    Precedence note: push/PR errors fire before the paused/killed checks
    because a finalize that failed mid-write should surface as the error
    it actually is. sync_failed_at fires before finished_at because the
    user must address the failed sync before treating the run as locally
    complete. killed_at fires before paused_at because the kill verb
    supersedes any prior pause state (the machine was destroyed).
    seed-failed fires earliest because orphan dirs have no run.json at
    all; running the corrupt-sidecar check on an empty dict would
    misclassify them.

    state_json is consulted for the `_orphan` marker (synthesized by
    `discover_runs`); other state.json fields remain reserved for
    forward-compat (future statuses like 'blocked' may consult
    state.json["blocked"])."""
    if (state_json or {}).get("_orphan"):
        return "seed-failed"
    rj = run_json or {}
    if rj:
        try:
            _validate_run_json(rj)
        except ValueError:
            return "corrupt-sidecar"
    if rj.get("push_error"):
        return "push-failed"
    if rj.get("pr_error"):
        return "pr-failed"
    if rj.get("pr_url"):
        return "done-pushed-pr"
    if rj.get("pushed_at"):
        return "done-pushed-no-pr"
    if rj.get("sync_failed_at"):
        return "sync-failed"
    # 6½. finished_at can be a *discovery* stamp written by the die-path
    # SystemExit handler on a mid-wave abort, NOT a real completion (DESIGN
    # §6 *finished_at is a discovery sentinel, not a completion signal*). A
    # run whose waves are not all integrated is `incomplete`, not `done`,
    # so --list doesn't mislabel it and finalize doesn't push a partial
    # branch. killed_at/paused_at take precedence (an explicitly
    # stopped/killed run is not "incomplete" in the resumable sense).
    if rj.get("finished_at") and not rj.get("killed_at") and not rj.get("paused_at"):
        sj = state_json or {}
        waves = sj.get("waves")
        if (isinstance(waves, list)
                and not sj.get("no_work_required")
                and sj.get("completed_waves", 0) < len(waves)):
            return "incomplete"
    if rj.get("finished_at"):
        return "done"
    if rj.get("killed_at"):
        return "killed"
    if rj.get("paused_at"):
        return "paused"
    return "in-progress"


def _collect_run_rows(
    leerie_root: Path,
) -> list[tuple[str, str, str, str, bool, str]]:
    """Build (run_id, started_at, status, branch, is_fly, cost) rows for
    every run under `leerie_root/runs/`. Pure data-gathering; rendering is
    the caller's concern. `is_fly` is True when the run has Fly runtime
    artifacts (fly_machine_id in run.json or fly-machine.json present).
    `cost` is the run's aggregate `$X.XX` from `state.json`'s telemetry
    block, or `—` when telemetry is absent (e.g. pre-classify orphans).
    `is_fly` stays second-to-last as the filter-only field so the existing
    `r[2]` status / `r[4]` runtime filters in `list_runs` are unaffected."""
    runs = discover_runs(leerie_root)
    rows: list[tuple[str, str, str, str, bool, str]] = []
    for state in runs:
        run_id = state["run_id"]
        run_dir = leerie_root / "runs" / run_id
        run_json: dict | None = None
        sidecar = run_dir / "run.json"
        if sidecar.is_file():
            try:
                parsed = json.loads(sidecar.read_text())
                if isinstance(parsed, dict):
                    run_json = parsed
            except (OSError, ValueError):
                run_json = None
        status = _derive_run_status(run_json, state)
        started_at = state.get("started_at") or "—"
        branch = (run_json or {}).get("branch") or compute_run_branch(run_id)
        is_fly = bool((run_json or {}).get("fly_machine_id")
                      or (run_dir / "fly-machine.json").is_file())
        # Telemetry rides along in the state summary (discover_runs passes the
        # whole state.json through), so no extra disk read. Orphans have no
        # state.json and thus no telemetry → render "—".
        tel = state.get("telemetry") or {}
        cost = f"${tel['cost_usd']:,.2f}" if tel.get("cost_usd") is not None \
            else "—"
        rows.append((run_id, started_at, status, branch, is_fly, cost))
    return rows


def _render_run_table(
    rows: list[tuple[str, str, str, str, bool, str]],
) -> None:
    """Print rows as a columnar table with auto-sized columns. Column
    order is run_id, started_at, status, cost, branch (the filter-only
    `is_fly` at r[4] is not rendered; cost is at r[5])."""
    w_id = max(len("run_id"), *(len(r[0]) for r in rows))
    w_st = max(len("started_at"), *(len(r[1]) for r in rows))
    w_status = max(len("status"), *(len(r[2]) for r in rows))
    w_cost = max(len("cost"), *(len(r[5]) for r in rows))
    w_br = max(len("branch"), *(len(r[3]) for r in rows))
    fmt = (f"{{:<{w_id}}}  {{:<{w_st}}}  {{:<{w_status}}}  "
           f"{{:>{w_cost}}}  {{:<{w_br}}}")
    print(fmt.format("run_id", "started_at", "status", "cost", "branch"))
    print(fmt.format("-" * w_id, "-" * w_st, "-" * w_status,
                     "-" * w_cost, "-" * w_br))
    for r in rows:
        print(fmt.format(r[0], r[1], r[2], r[5], r[3]))


def list_runs(
    leerie_root: Path,
    status_filter: str | None = None,
    runtime_filter: str | None = None,
) -> None:
    """Render a sortable columnar table of runs to stdout. Used by
    `leerie --list`. Reads run.json sidecar (commit 4) for status
    derivation; falls back to state.json fields for runs without a
    sidecar.

    Filters compose:
      `status_filter` restricts to rows whose derived status matches.
      `runtime_filter` restricts to rows by execution backend: 'fly'
      means rows with Fly runtime artifacts (fly_machine_id in run.json
      or fly-machine.json present); 'local' means rows without. Both
      are validated against argparse `choices=` before
      this is called; unknown values render an empty table."""
    rows = _collect_run_rows(leerie_root)
    if status_filter is not None:
        rows = [r for r in rows if r[2] == status_filter]
    if runtime_filter == "fly":
        rows = [r for r in rows if r[4]]
    elif runtime_filter == "local":
        rows = [r for r in rows if not r[4]]
    if not rows:
        msg = "no runs"
        if status_filter is not None:
            msg += f" with status={status_filter}"
        if runtime_filter is not None:
            msg += f" with runtime={runtime_filter}"
        if status_filter is None and runtime_filter is None:
            msg = f"no runs under {leerie_root}/runs/"
        print(msg)
        return
    _render_run_table(rows)


def _aggregate_calls(calls_path: Path) -> dict[str, dict]:
    """Group a run's calls.ndjson by call_type, summing per-type counts,
    tokens, latency, and failures (by failure_kind). Malformed lines are
    skipped. Returns {call_type: {calls, input_tokens, output_tokens,
    latency_ms_sum, failures, failure_kinds}}."""
    agg: dict[str, dict] = {}
    try:
        text = calls_path.read_text()
    except OSError:
        return agg
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        ct = rec.get("call_type") or "(unknown)"
        row = agg.setdefault(ct, {
            "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "latency_ms_sum": 0, "failures": 0, "failure_kinds": {},
        })
        row["calls"] += 1
        row["input_tokens"] += int(rec.get("input_tokens") or 0)
        row["output_tokens"] += int(rec.get("output_tokens") or 0)
        row["latency_ms_sum"] += int(rec.get("latency_ms") or 0)
        if not rec.get("success"):
            row["failures"] += 1
            fk = rec.get("failure_kind") or "(unclassified)"
            row["failure_kinds"][fk] = row["failure_kinds"].get(fk, 0) + 1
    return agg


def _memory_peak(mem_path: Path) -> dict | None:
    """Read a run's memory.ndjson and return peak rss_kb + max open_fds /
    thread_count, or None if the file is absent/empty/unreadable."""
    try:
        text = mem_path.read_text()
    except OSError:
        return None
    peak_rss = max_fds = max_threads = 0
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            s = json.loads(line)
        except ValueError:
            continue
        n += 1
        peak_rss = max(peak_rss, int(s.get("rss_kb") or 0))
        max_fds = max(max_fds, int(s.get("open_fds") or 0))
        max_threads = max(max_threads, int(s.get("thread_count") or 0))
    if n == 0:
        return None
    return {"samples": n, "peak_rss_kb": peak_rss,
            "max_open_fds": max_fds, "max_thread_count": max_threads}


def report_run(leerie_root: Path, cli_run_id: str | None) -> None:
    """Print a telemetry report for one run: header (status, duration,
    aggregate calls/$/tokens from state.json), a per-call_type breakdown
    (count, in/out tokens, avg latency, failures) from calls.ndjson, and a
    memory-peak line from memory.ndjson. Read-only. Used by `--report`.

    Run selection reuses `resolve_run_id` (exact match, or the sole run,
    else die with the available list)."""
    run_id = resolve_run_id(leerie_root, cli_run_id or None)
    run_dir = leerie_root / "runs" / run_id
    state: dict = {}
    try:
        parsed = json.loads((run_dir / "state.json").read_text())
        if isinstance(parsed, dict):
            state = parsed
    except (OSError, ValueError):
        state = {}
    run_json: dict | None = None
    try:
        parsed = json.loads((run_dir / "run.json").read_text())
        if isinstance(parsed, dict):
            run_json = parsed
    except (OSError, ValueError):
        run_json = None

    status = _derive_run_status(run_json, state or None)
    started = state.get("started_at")
    finished = state.get("finished_at")
    duration = _format_run_duration(started, finished) or "n/a"
    tel = state.get("telemetry") or {}

    print(f"run:      {run_id}")
    print(f"status:   {status}")
    print(f"started:  {started or 'n/a'}")
    print(f"duration: {duration}")
    if tel:
        print(f"weight:   {tel.get('calls', 0)} calls, "
              f"${tel.get('cost_usd', 0.0):,.2f}, "
              f"{tel.get('input_tokens', 0):,} in / "
              f"{tel.get('output_tokens', 0):,} out tokens")
    else:
        print("weight:   (no telemetry recorded)")

    agg = _aggregate_calls(run_dir / "calls.ndjson")
    if agg:
        print("\nby call_type:")
        w_ct = max(len("call_type"), *(len(k) for k in agg))
        hdr = (f"  {{:<{w_ct}}}  {{:>5}}  {{:>10}}  {{:>10}}  "
               f"{{:>9}}  {{:>4}}")
        print(hdr.format("call_type", "calls", "in_tok", "out_tok",
                         "avg_ms", "fail"))
        print(hdr.format("-" * w_ct, "-" * 5, "-" * 10, "-" * 10,
                         "-" * 9, "-" * 4))
        # Newest-heaviest first: sort by call count descending.
        for ct, r in sorted(agg.items(),
                            key=lambda kv: kv[1]["calls"], reverse=True):
            avg_ms = r["latency_ms_sum"] // r["calls"] if r["calls"] else 0
            print(hdr.format(ct, r["calls"], f"{r['input_tokens']:,}",
                             f"{r['output_tokens']:,}", f"{avg_ms:,}",
                             r["failures"]))
        # Failure-kind rollup — only when there were failures.
        fk_totals: dict[str, int] = {}
        for r in agg.values():
            for k, v in r["failure_kinds"].items():
                fk_totals[k] = fk_totals.get(k, 0) + v
        if fk_totals:
            print("\nfailures by kind:")
            for k, v in sorted(fk_totals.items(),
                               key=lambda kv: kv[1], reverse=True):
                print(f"  {v:>4}  {k}")
    else:
        print("\nby call_type: (no calls.ndjson found)")

    mem = _memory_peak(run_dir / "memory.ndjson")
    if mem:
        print(f"\nmemory:   peak RSS {mem['peak_rss_kb']:,} KiB, "
              f"max {mem['max_open_fds']} fds / "
              f"{mem['max_thread_count']} threads "
              f"({mem['samples']} samples)")


def _read_toml_key(path: Path, key: str) -> str | None:
    """Read a single `key = value` from a flat leerie.toml. Returns
    None when the file does not exist or the key is absent. Strips
    matched surrounding double or single quotes from the value. Used
    by both source-of-truth and model resolvers — keeping one parser
    means a fix benefits both."""
    if not path.exists():
        return None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() != key:
            continue
        return v.strip().strip('"').strip("'")
    return None


TASK_FILE_SUFFIXES = (".txt", ".md")


def resolve_task_argument(raw: str) -> str:
    """Resolve the positional `task` argument to the task string.

    If `raw` points at an existing .txt or .md file, return its contents
    (stripped). If `raw` has a .txt/.md suffix but the file doesn't exist,
    die — the user meant to reference a file. Otherwise return `raw`
    unchanged.
    """
    p = Path(raw)
    # A long literal task is one path component over NAME_MAX (255 bytes
    # on macOS/Linux), which makes stat() raise ENAMETOOLONG instead of
    # returning a "not found" result that is_file() would surface as
    # False. Any stat failure means we cannot confirm a file, so treat
    # `raw` as the literal task.
    stat_ok = True
    try:
        is_task_file = (p.is_file()
                        and p.suffix.lower() in TASK_FILE_SUFFIXES)
    except OSError:
        is_task_file = False
        stat_ok = False
    if is_task_file:
        contents = p.read_text().strip()
        if not contents:
            die(f"task file {raw!r} is empty")
        return contents
    if stat_ok and p.suffix.lower() in TASK_FILE_SUFFIXES:
        die(f"task file not found: {raw}")
    return raw


def resolve_leerie_root(repo_root: Path) -> Path:
    """Resolve the leerie state root directory.

    Resolution order: LEERIE_STATE_DIR env var → repo_root / '.leerie'.

    Under the standard launcher path the container is started with
    LEERIE_STATE_DIR=/leerie-state, which is the bind-mount target of
    $LEERIE_STATE_HOST_DIR (default $HOME/.leerie/<basename>/) — so all
    run state lives outside the repo. The unset branch only fires for
    direct (non-launcher) Python callers like the test suite, where it
    falls back to the in-repo `.leerie/` layout (used by tests and direct invocations).
    """
    env = os.environ.get(STATE_DIR_ENV, "").strip()
    if env:
        return Path(env).resolve()
    return (repo_root / ".leerie").resolve()


def _resolve_enum_pref(repo_root: Path, cli_value: str | None, *,
                       env_var: str, file_key: str, file_name: str,
                       allowed: frozenset[str] | tuple[str, ...],
                       default: str) -> str:
    """Shared resolution for enum-valued prefs. CLI > env > file > default.
    Bad env or file values die() at startup. Mirrors _resolve_bool_pref."""
    if cli_value:
        return cli_value
    env = os.environ.get(env_var, "").strip()
    if env:
        if env not in allowed:
            die(f"{env_var}={env!r} is not one of {allowed}")
        return env
    cfg = repo_root / file_name
    file_val = _read_toml_key(cfg, file_key)
    if file_val is not None:
        if file_val not in allowed:
            die(f"{cfg}: {file_key}={file_val!r} is not one of {allowed}")
        return file_val
    return default


def resolve_source_of_truth(repo_root: Path,
                            cli_value: str | None = None) -> str:
    """Resolve the source-of-truth preference. Order:
    --source-of-truth CLI flag → LEERIE_SOURCE_OF_TRUTH env var →
    leerie.toml → default 'both'. argparse validates `cli_value` via
    choices=, so it is trusted when set. env and file values are
    rejected via die() if not in SOURCE_OF_TRUTH_VALUES — a bad
    config is caught at startup, not during a planner run."""
    return _resolve_enum_pref(
        repo_root, cli_value,
        env_var=SOURCE_OF_TRUTH_ENV, file_key="source_of_truth",
        file_name=SOURCE_OF_TRUTH_FILE,
        allowed=SOURCE_OF_TRUTH_VALUES, default="both")


def resolve_runtime(repo_root: Path,
                    cli_value: str | None = None) -> str:
    """Resolve the runtime mode. Order:
    --runtime CLI flag → LEERIE_RUNTIME env var → leerie.toml → default 'local'.
    argparse validates `cli_value` via choices=, so it is trusted when set.
    env and file values are rejected via die() if not in RUNTIME_VALUES — a
    bad config is caught at startup, not during a worker run."""
    return _resolve_enum_pref(
        repo_root, cli_value,
        env_var=RUNTIME_ENV, file_key="runtime",
        file_name=RUNTIME_FILE,
        allowed=RUNTIME_VALUES, default="local")


def _resolve_str_pref(repo_root: Path, cli_value: str | None, *,
                      env_var: str, file_key: str, file_name: str,
                      default: str | None) -> str | None:
    """Shared resolution for unvalidated string prefs. CLI > env > file >
    default. No enum check — value is free-form. Mirrors _resolve_bool_pref."""
    if cli_value and cli_value.strip():
        return cli_value.strip()
    env = os.environ.get(env_var, "").strip()
    if env:
        return env
    cfg = repo_root / file_name
    file_val = _read_toml_key(cfg, file_key)
    if file_val is not None and file_val.strip():
        return file_val.strip()
    return default


def resolve_pr_template(repo_root: Path,
                        cli_value: str | None = None) -> str | None:
    """Resolve the --pr-template selector. Order:
    --pr-template CLI flag → LEERIE_PR_TEMPLATE env → leerie.toml → None.
    Returns the basename of the desired template inside a
    PULL_REQUEST_TEMPLATE/ directory (case preserved, .md optional).
    No validation against MODEL_VALUES-style enum since the choice is
    free-form (depends on the target repo's directory contents); the
    template-discovery helper validates existence later."""
    return _resolve_str_pref(
        repo_root, cli_value,
        env_var=PR_TEMPLATE_ENV, file_key="pr_template",
        file_name=PR_TEMPLATE_FILE, default=None)


def _resolve_positive_int_pref(repo_root: Path, cli_value: int | None, *,
                               env_var: str, file_key: str, file_name: str,
                               default: int) -> int:
    """Shared resolution for positive-int prefs. CLI > env > file > default.
    Bad env or file values die() at startup. Mirrors _resolve_bool_pref."""
    if cli_value is not None:
        return cli_value
    env = os.environ.get(env_var, "").strip()
    if env:
        try:
            n = int(env)
        except ValueError:
            die(f"{env_var}={env!r} is not a positive integer")
        if n < 1:
            die(f"{env_var}={env!r} is not a positive integer")
        return n
    cfg = repo_root / file_name
    file_val = _read_toml_key(cfg, file_key)
    if file_val is not None:
        try:
            n = int(file_val)
        except ValueError:
            die(f"{cfg}: {file_key}={file_val!r} is not a positive integer")
        if n < 1:
            die(f"{cfg}: {file_key}={file_val!r} is not a positive integer")
        return n
    return default


def resolve_confidence_rounds(repo_root: Path,
                              cli_value: int | None = None) -> int:
    """Resolve the confidence-rounds cap. Order:
    --confidence-rounds CLI flag → LEERIE_CONFIDENCE_ROUNDS env var →
    leerie.toml → DEFAULT_CAPS["confidence_rounds"]. argparse validates
    `cli_value` is a positive int via `type=`, so it is trusted when set.
    env and file values are rejected via die() when not a positive int —
    bad config caught at startup, not during a planner run."""
    return _resolve_positive_int_pref(
        repo_root, cli_value,
        env_var=CONFIDENCE_ROUNDS_ENV, file_key="confidence_rounds",
        file_name=CONFIDENCE_ROUNDS_FILE,
        default=DEFAULT_CAPS["confidence_rounds"])


def resolve_max_workers(repo_root: Path,
                        cli_value: int | None = None) -> int:
    """Resolve the max-workers cap. Order:
    --max-workers CLI flag → LEERIE_MAX_WORKERS env var → leerie.toml →
    DEFAULT_CAPS["max_total_workers"]. argparse validates `cli_value` is an
    int via `type=int` so it is trusted when set. env and file values are
    rejected via die() when not a positive int — bad config caught at
    startup, not mid-run."""
    return _resolve_positive_int_pref(
        repo_root, cli_value,
        env_var=MAX_WORKERS_ENV, file_key="max_workers",
        file_name=MAX_WORKERS_FILE,
        default=DEFAULT_CAPS["max_total_workers"])


def resolve_max_parallel(repo_root: Path,
                         cli_value: int | None = None) -> int:
    """Resolve the max-parallel cap. Order:
    --max-parallel CLI flag → LEERIE_MAX_PARALLEL env var → leerie.toml →
    DEFAULT_CAPS["max_parallel"]. argparse validates `cli_value` is a
    positive int via `type=_positive_int` so it is trusted when set.
    env and file values are rejected via die() when not a positive int —
    bad config caught at startup, not mid-run."""
    return _resolve_positive_int_pref(
        repo_root, cli_value,
        env_var=MAX_PARALLEL_ENV, file_key="max_parallel",
        file_name=MAX_PARALLEL_FILE,
        default=DEFAULT_CAPS["max_parallel"])


def resolve_judgment_check_rounds(repo_root: Path,
                                   cli_value: int | None = None) -> int:
    """Resolve the judgment-check-rounds cap (CRITIC-pattern re-invocations
    for classifier, reconciler, provision, overlap judge, integrator)."""
    return _resolve_positive_int_pref(
        repo_root, cli_value,
        env_var=JUDGMENT_CHECK_ROUNDS_ENV,
        file_key="judgment_check_rounds",
        file_name=SOURCE_OF_TRUTH_FILE,
        default=DEFAULT_CAPS["judgment_check_rounds"])


def resolve_planner_check_rounds(repo_root: Path,
                                  cli_value: int | None = None) -> int:
    """Resolve the planner-check-rounds cap (CRITIC-pattern re-invocations
    for planner — higher default because checks are richer)."""
    return _resolve_positive_int_pref(
        repo_root, cli_value,
        env_var=PLANNER_CHECK_ROUNDS_ENV,
        file_key="planner_check_rounds",
        file_name=SOURCE_OF_TRUTH_FILE,
        default=DEFAULT_CAPS["planner_check_rounds"])


def resolve_implementer_confidence_retries(
        repo_root: Path, cli_value: int | None = None) -> int:
    """Resolve the implementer-confidence-retries cap (separate from
    subtask_continuations so confidence retries don't consume the
    handoff/clarification budget)."""
    return _resolve_positive_int_pref(
        repo_root, cli_value,
        env_var=IMPLEMENTER_CONFIDENCE_RETRIES_ENV,
        file_key="implementer_confidence_retries",
        file_name=SOURCE_OF_TRUTH_FILE,
        default=DEFAULT_CAPS["implementer_confidence_retries"])


def resolve_planner_samples(repo_root: Path,
                             cli_value: int | None = None) -> int:
    """Resolve the planner-samples cap (multi-sample; 1 disables)."""
    return _resolve_positive_int_pref(
        repo_root, cli_value,
        env_var=PLANNER_SAMPLES_ENV,
        file_key="planner_samples",
        file_name=SOURCE_OF_TRUTH_FILE,
        default=DEFAULT_CAPS["planner_samples"])


_MEMORY_SUFFIX_MULTIPLIER = {
    "": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4,
}


def _parse_memory_size(value: str, context: str) -> int:
    """Parse a memory size string like "4G", "512M", "1024" into bytes.

    Accepts an optional case-insensitive IEC binary suffix (K/M/G/T).
    No suffix means bytes. Rejects negative, zero, fractional, and
    garbage values via die() with `context` in the error message so the
    user knows which knob produced the bad value."""
    v = value.strip()
    if not v:
        die(f"{context}: memory size cannot be empty")
    suffix = v[-1:].upper()
    if suffix in _MEMORY_SUFFIX_MULTIPLIER and suffix != "":
        numeric = v[:-1]
        mult = _MEMORY_SUFFIX_MULTIPLIER[suffix]
    else:
        numeric = v
        mult = 1
    try:
        n = int(numeric)
    except ValueError:
        die(f"{context}: {value!r} is not a valid memory size "
            f"(expected like '4G', '512M', '1024')")
    if n <= 0:
        die(f"{context}: {value!r} must be a positive memory size")
    return n * mult


def _auto_worker_memory_max(max_parallel: int) -> int:
    """Auto-derive a per-worker memory cap from /proc/meminfo.

    The goal: distribute the VM's RAM across `max_parallel + 1` slots
    so one slot remains for the orchestrator + system processes
    (sshd, lima-guestagent, etc.) outside any worker cgroup. Capped at
    4 GiB per worker — beyond that, a single tool subtree shouldn't
    legitimately need more, and an uncapped 8+ GiB cgroup defeats
    the containment purpose.

    Falls back to 2 GiB if /proc/meminfo is unreadable (non-Linux,
    sandboxed test, etc.). The cgroup write itself will detect a
    nonsensical limit and the probe will skip wrapping."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    total = kb * 1024
                    break
            else:
                return 2 * 1024**3
    except (FileNotFoundError, PermissionError, ValueError):
        return 2 * 1024**3
    per_worker = total // (max_parallel + 1)
    return min(per_worker, 4 * 1024**3)


def resolve_worker_memory_max(repo_root: Path,
                              max_parallel: int,
                              cli_value: str | None = None) -> int:
    """Resolve the per-worker cgroup memory cap (bytes). Order:
    --worker-memory-max CLI flag → LEERIE_WORKER_MEMORY_MAX env →
    leerie.toml `worker_memory_max` → auto-derive from /proc/meminfo.

    All sources accept the same format ("4G", "512M", "1024") and are
    validated by _parse_memory_size, which die()s on bad input — bad
    config is caught at startup, not during a worker spawn."""
    if cli_value is not None:
        return _parse_memory_size(cli_value, "--worker-memory-max")
    env = os.environ.get(WORKER_MEMORY_MAX_ENV, "").strip()
    if env:
        return _parse_memory_size(env, WORKER_MEMORY_MAX_ENV)
    cfg = repo_root / WORKER_MEMORY_MAX_FILE
    file_val = _read_toml_key(cfg, "worker_memory_max")
    if file_val is not None:
        return _parse_memory_size(file_val,
                                  f"{cfg}: worker_memory_max")
    return _auto_worker_memory_max(max_parallel)


def resolve_worker_pids_max(repo_root: Path,
                            cli_value: int | None = None) -> int:
    """Resolve the per-worker cgroup PID cap (pids.max). Order:
    --worker-pids-max CLI flag → LEERIE_WORKER_PIDS_MAX env →
    leerie.toml `worker_pids_max` → DEFAULT_CAPS["worker_pids_max"].
    argparse validates `cli_value` is a positive int via
    `type=_positive_int` so it is trusted when set; env and file values
    die() when not a positive int — bad config caught at startup."""
    return _resolve_positive_int_pref(
        repo_root, cli_value,
        env_var=WORKER_PIDS_MAX_ENV, file_key="worker_pids_max",
        file_name=WORKER_PIDS_MAX_FILE,
        default=DEFAULT_CAPS["worker_pids_max"])


def resolve_inspect_dirs(repo_root: Path,
                         cli_values: list[str] | None = None) -> list[str]:
    """Resolve the extra inspection directories for classifier/planner/
    reconciler/provision. Order: --inspect-dir CLI flags (one or more, repeatable) →
    LEERIE_INSPECT_DIRS env var (colon-separated) → inspect_dirs in
    leerie.toml (comma-separated string) → []. Paths are expanded
    (~ → $HOME) and resolved to absolute form so a relative path in TOML
    still works after the orchestrator changes cwd. Non-existent paths
    are accepted at resolve time — the CLI surfaces a clearer error if
    --add-dir gets a bad path, and we want startup to fail fast at the
    use site rather than rejecting a typo before classify even runs."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        p = raw.strip()
        if not p:
            return
        abs_p = str(Path(p).expanduser().resolve())
        if abs_p not in seen:
            seen.add(abs_p)
            out.append(abs_p)

    if cli_values:
        for p in cli_values:
            _add(p)
        return out
    env = os.environ.get(INSPECT_DIRS_ENV, "").strip()
    if env:
        for p in env.split(":"):
            _add(p)
        return out
    cfg = repo_root / INSPECT_DIRS_FILE
    file_val = _read_toml_key(cfg, "inspect_dirs")
    if file_val is not None:
        for p in file_val.split(","):
            _add(p)
        return out
    return out


def _parse_bool_envtoml(value: str) -> bool | None:
    """Parse a boolean from an env var or TOML scalar. Returns True/False
    for the conventional spellings; None for the empty string / unset.
    Raises ValueError on any other input so the caller can die() with a
    helpful message rather than silently treating typos as False."""
    v = value.strip().lower()
    if v == "":
        return None
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise ValueError(value)


def _resolve_bool_pref(repo_root: Path, cli_value: bool, *,
                       env_var: str, file_key: str, file_name: str) -> bool:
    """Shared resolution for `store_true` CLI flags that also have an
    env-var and per-repo TOML mirror (see DESIGN §11 / §6 patterns).
    Order: CLI True wins → env → file → False. Bad env or file values
    `die()` at startup, not mid-run. Used by `resolve_no_push` and
    `resolve_clarify`; keep one shape so they cannot drift."""
    if cli_value:
        return True
    env = os.environ.get(env_var, "").strip()
    if env:
        try:
            parsed = _parse_bool_envtoml(env)
        except ValueError:
            die(f"{env_var}={env!r} is not a boolean "
                "(use 1/0, true/false, yes/no, on/off)")
        if parsed is not None:
            return parsed
    cfg = repo_root / file_name
    file_val = _read_toml_key(cfg, file_key)
    if file_val is not None:
        try:
            parsed = _parse_bool_envtoml(file_val)
        except ValueError:
            die(f"{cfg}: {file_key}={file_val!r} is not a boolean")
        if parsed is not None:
            return parsed
    return False


def resolve_no_push(repo_root: Path, cli_value: bool) -> bool:
    """Resolve the --no-push preference. Order:
    --no-push CLI flag (action='store_true', so True if passed) →
    LEERIE_NO_PUSH env var → no_push in leerie.toml → False.
    `--no-verify` has no env/TOML mirror (see NO_PUSH_ENV comment)."""
    return _resolve_bool_pref(
        repo_root, cli_value,
        env_var=NO_PUSH_ENV, file_key="no_push", file_name=NO_PUSH_FILE)


def push_will_happen(no_push: bool, host_no_push: bool | None) -> bool:
    """Whether the host will push after this orchestrator exits.

    DESIGN §6 *Finalization*: `--no-push` on the orchestrator's argv is
    a **mechanism flag** on Fly (the Machine has no GitHub auth and
    cannot push regardless of user preference); the launcher always
    passes it on the remote path. The user's actual launch-time intent
    is a separate signal, propagated via `--host-no-push true|false`
    (None when unset = local runtime).

    The function returns the answer to one question: will a push
    happen on the host? `pr_writer` runs iff this is True (otherwise it
    burns budget composing a PR that will never open), and
    `phase_finalize` writes `not push_will_happen(...)` to
    `run.json.no_push` so `host_finalize` reads the user's intent, not
    the mechanism flag.

    Local runtime: `host_no_push is None`; the launcher and the pusher
    are the same shell, so `no_push` alone reflects intent.

    Fly runtime: `host_no_push` is the user's intent; `no_push` is
    always True (mechanism). Intent wins."""
    if host_no_push is None:
        return not no_push
    return not host_no_push


def resolve_clarify(repo_root: Path, cli_value: bool) -> bool:
    """Resolve the --clarify preference. Order:
    --clarify CLI flag (action='store_true', so True if passed) →
    LEERIE_CLARIFY env var → clarify in leerie.toml → False.
    See DESIGN §11 for the clarification semantics."""
    return _resolve_bool_pref(
        repo_root, cli_value,
        env_var=CLARIFY_ENV, file_key="clarify", file_name=CLARIFY_FILE)


def resolve_dangerously_skip_permissions(
        repo_root: Path, cli_value: bool) -> bool:
    """Resolve the --dangerously-skip-permissions preference. Order:
    --dangerously-skip-permissions CLI flag (action='store_true') →
    LEERIE_DANGEROUSLY_SKIP_PERMISSIONS env var →
    dangerously_skip_permissions in leerie.toml → False.

    When True, EVERY claude -p worker — including the judgment workers
    (classifier, planner, reconciler, plan_overlap_judge, provision) that run
    in the real repo cwd, not an isolated worktree — is invoked with
    --dangerously-skip-permissions. This waives the DESIGN §12
    mechanical enforcement that planners stay read-only; trust shifts
    onto the prompts. Off by default; users opting in are making one
    all-or-nothing trust decision. See DESIGN §12 (last paragraph) and
    IMPLEMENTATION.md §2 "Permission override (dangerous)"."""
    return _resolve_bool_pref(
        repo_root, cli_value,
        env_var=DANGEROUS_SKIP_PERMS_ENV,
        file_key="dangerously_skip_permissions",
        file_name=DANGEROUS_SKIP_PERMS_FILE)


def resolve_dangerously_allow_uncapped(
        repo_root: Path, cli_value: bool) -> bool:
    """Resolve the --dangerously-allow-uncapped preference. Order:
    --dangerously-allow-uncapped CLI flag (action='store_true') →
    LEERIE_DANGEROUSLY_ALLOW_UNCAPPED env var →
    dangerously_allow_uncapped in leerie.toml → False.

    When True, a failed cgroup broker probe (workers would run without
    memory/PID containment) is a loud warning instead of a fatal error.
    Off by default: silently-uncapped workers are what let a runaway
    subtree exhaust the VM thread table (DESIGN §6 *Memory containment*)."""
    return _resolve_bool_pref(
        repo_root, cli_value,
        env_var=DANGEROUS_ALLOW_UNCAPPED_ENV,
        file_key="dangerously_allow_uncapped",
        file_name=DANGEROUS_ALLOW_UNCAPPED_FILE)


def resolve_skip_overlap_judge(repo_root: Path, cli_value: bool) -> bool:
    """Resolve the --skip-overlap-judge preference. Order:
    --skip-overlap-judge CLI flag (action='store_true') →
    LEERIE_SKIP_OVERLAP_JUDGE env var →
    skip_overlap_judge in leerie.toml → False.

    When True, `phase_overlap_judge` (DESIGN §5 *Cross-domain surface
    overlap*) skips the worker spawn even on multi-planner runs that
    would otherwise trigger it. The cheap-skip on single-planner / <2-
    subtask runs is automatic and not gated by this flag — this flag
    only affects the runs where the worker would actually fire. Off by
    default; use only when you know the surface overlap is intentional
    and you want to bypass the discipline."""
    return _resolve_bool_pref(
        repo_root, cli_value,
        env_var=SKIP_OVERLAP_JUDGE_ENV,
        file_key="skip_overlap_judge",
        file_name=SKIP_OVERLAP_JUDGE_FILE)


def resolve_skip_budget_check(repo_root: Path, cli_value: bool) -> bool:
    """Resolve the --skip-budget-check preference. Order:
    --skip-budget-check CLI flag (action='store_true') →
    LEERIE_SKIP_BUDGET_CHECK env var →
    skip_budget_check in leerie.toml → False.

    When True, `check_budget_feasibility()` (DESIGN §13 *Budget
    feasibility — fail fast at the cheapest moment*) is suppressed.
    The runtime backstop in `State.bump_workers()` still fires if the
    run exceeds `max_total_workers` during execution; this flag only
    suppresses the *early* die() that catches mathematically-unwinnable
    runs at the plan/execute boundary. Off by default; use only when
    the operator knows the conformer phase will degrade heavily to
    advisory warnings or the per-subtask ratio will come in well
    under the default estimate."""
    return _resolve_bool_pref(
        repo_root, cli_value,
        env_var=SKIP_BUDGET_CHECK_ENV,
        file_key="skip_budget_check",
        file_name=SKIP_BUDGET_CHECK_FILE)


def resolve_skip_satisfied_check(repo_root: Path, cli_value: bool) -> bool:
    """Resolve the --skip-satisfied-check preference. Order:
    --skip-satisfied-check CLI flag (action='store_true') →
    LEERIE_SKIP_SATISFIED_CHECK env var →
    skip_satisfied_check in leerie.toml → False.

    When True, `filter_satisfied_subtasks()` (DESIGN §8 *Already-
    satisfied subtask elimination*) is suppressed: no `satisfied_probe`
    worker spawns and every subtask proceeds to `schedule()`. The
    mechanical `check_branch_has_commits` backstop still catches an
    already-satisfied subtask post-execution (as a retryable no-op), so
    this flag trades the cheap plan-time skip for the more expensive
    post-execution one. Off by default; use when the operator knows the
    base tree contains no already-merged overlap and wants to save the
    per-subtask probe cost."""
    return _resolve_bool_pref(
        repo_root, cli_value,
        env_var=SKIP_SATISFIED_CHECK_ENV,
        file_key="skip_satisfied_check",
        file_name=SKIP_SATISFIED_CHECK_FILE)


def resolve_strict_conformer(repo_root: Path, cli_value: bool) -> bool:
    """Resolve the --strict-conformer preference. Order:
    --strict-conformer CLI flag (action='store_true') →
    LEERIE_STRICT_CONFORMER env var →
    strict_conformer in leerie.toml → False.

    When True, conformer residuals (failed build/lint/test axes or
    unresolved rule violations) cause the subtask to return status
    'blocked' instead of surfacing as advisory warnings. The run can
    be resumed with --resume after the user fixes the residuals."""
    return _resolve_bool_pref(
        repo_root, cli_value,
        env_var=STRICT_CONFORMER_ENV,
        file_key="strict_conformer",
        file_name=STRICT_CONFORMER_FILE)


def resolve_skip_base_baseline(repo_root: Path, cli_value: bool) -> bool:
    """Resolve the --skip-base-baseline preference. Order:
    --skip-base-baseline CLI flag (action='store_true') →
    LEERIE_SKIP_BASE_BASELINE env var →
    skip_base_baseline in leerie.toml → False.

    When True, `capture_conformance_baseline` is not run — the once-per-run
    install-into-staging + build/lint/test pass that records base-tree
    health (DESIGN §9 *Base-tree health baseline*) is skipped, and the
    conformer gets no BASELINE context. Use on repos whose base is known
    green, or to avoid the up-front full-suite-run cost."""
    return _resolve_bool_pref(
        repo_root, cli_value,
        env_var=SKIP_BASE_BASELINE_ENV,
        file_key="skip_base_baseline",
        file_name=SKIP_BASE_BASELINE_FILE)


def resolve_skip_repo_map(repo_root: Path, cli_value: bool) -> bool:
    """Resolve the --skip-repo-map preference. Order:
    --skip-repo-map CLI flag (action='store_true') →
    LEERIE_SKIP_REPO_MAP env var →
    skip_repo_map in leerie.toml → False.

    When True, `build_repo_map()` is not called — the ranked P6 subgraph
    injection into the planner/splitter context is skipped and the planner
    degrades gracefully to the prior grep/glob-only path. Use on repos where
    tree-sitter cannot parse the primary language, or where the user wants to
    opt out of the structural context."""
    return _resolve_bool_pref(
        repo_root, cli_value,
        env_var=SKIP_REPO_MAP_ENV,
        file_key="skip_repo_map",
        file_name=SKIP_REPO_MAP_FILE)


def _positive_int(s: str) -> int:
    """argparse `type=` helper. Rejects non-positive integers with the
    standard argparse error message. Used by --confidence-rounds."""
    try:
        n = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{s!r} is not an integer")
    if n < 1:
        raise argparse.ArgumentTypeError(f"{s!r} is not a positive integer")
    return n


def resolve_verbosity(repo_root: Path,
                      cli_value: str | None = None) -> str:
    """Resolve the verbosity level. Order:
    --verbosity CLI flag → LEERIE_VERBOSITY env var → leerie.toml →
    VERBOSITY_DEFAULT. argparse validates `cli_value` via choices=, so
    it is trusted when set. env and file values are rejected via die()
    if not in VERBOSITY_VALUES — a bad config is caught at startup,
    not during a worker run.

    The -v/-vv/-q/-qq shortcuts are resolved separately in main()
    BEFORE this function is called — they map to one of VERBOSITY_VALUES
    and pass through as cli_value. The shortcut→level mapping is anchored
    to `normal` (the pre-streaming behavior), not to VERBOSITY_DEFAULT,
    so -v means "show me the streaming feature" rather than "bump above
    the default by one"."""
    return _resolve_enum_pref(
        repo_root, cli_value,
        env_var=VERBOSITY_ENV, file_key="verbosity",
        file_name=VERBOSITY_FILE,
        allowed=VERBOSITY_VALUES, default=VERBOSITY_DEFAULT)


def verbosity_from_shortcuts(verbose: int, quiet: int) -> str | None:
    """Map argparse -v/-vv/-q/-qq counts to a verbosity level.

    Anchors to `normal` (NOT to VERBOSITY_DEFAULT), so -v always means
    "show me the streaming feature" and -q always means "back to the
    pre-streaming terse output", independent of what env-var / TOML
    defaults are set to. Matches the cargo / kubectl idiom of treating
    shortcuts as relative-to-baseline rather than relative-to-resolved.

    Returns None when neither shortcut was used (caller falls through
    to resolve_verbosity / env / TOML / default). Returns a value from
    VERBOSITY_VALUES when a shortcut was used. Stacking past -vv / -qq
    saturates at the endpoints rather than wrapping or raising — a
    user typing -vvvv gets debug, not an error."""
    if quiet:
        return "quiet" if quiet > 1 else "normal"
    if verbose:
        return "debug" if verbose > 1 else "stream"
    return None


def resolve_models(repo_root: Path, args) -> dict[str, str]:
    """Resolve the model alias for each worker type. Per-worker
    precedence (highest first):
      1. --model-<worker> CLI flag
      2. --model CLI flag (global default for this run)
      3. LEERIE_MODEL_<WORKER> env var
      4. LEERIE_MODEL env var
      5. model_<worker> in leerie.toml
      6. model in leerie.toml
      7. MODEL_DEFAULT_PER_WORKER[<worker>] (e.g., implementer → sonnet)
      8. MODEL_DEFAULT (opus)
    `args` is the parsed argparse.Namespace (CLI values are already
    validated by argparse choices=). env and file values are rejected
    via die() when not in MODEL_VALUES."""
    cfg = repo_root / MODEL_FILE

    def from_env(name: str) -> str | None:
        v = os.environ.get(name, "").strip()
        if not v:
            return None
        if v not in MODEL_VALUES:
            die(f"{name}={v!r} is not one of {MODEL_VALUES}")
        return v

    def from_file(key: str) -> str | None:
        v = _read_toml_key(cfg, key)
        if v is None:
            return None
        if v not in MODEL_VALUES:
            die(f"{cfg}: {key}={v!r} is not one of {MODEL_VALUES}")
        return v

    global_cli = getattr(args, "model", None)
    global_env = from_env(MODEL_ENV)
    global_file = from_file("model")

    models: dict[str, str] = {}
    for worker in WORKER_TYPES:
        # argparse converts --model-foo to args.model_foo
        per_cli = getattr(args, f"model_{worker}", None)
        per_env = from_env(f"{MODEL_ENV}_{worker.upper()}")
        per_file = from_file(f"model_{worker}")
        # Per-worker default kicks in only when no user override applies.
        # Implementer falls through to "sonnet"; everything else falls
        # through to MODEL_DEFAULT ("opus").
        per_worker_default = MODEL_DEFAULT_PER_WORKER.get(worker, MODEL_DEFAULT)
        models[worker] = (per_cli or global_cli or per_env or global_env
                          or per_file or global_file or per_worker_default)
    # Judge and heal use dedicated flags (--judge-model / --heal-model) and
    # dedicated env vars (LEERIE_MODEL_JUDGE / LEERIE_MODEL_HEAL) rather
    # than the --model-<W> pattern — they're post-run skill workers that don't
    # participate in the --model global-default resolution path. They still
    # fall back to the global override so `--model sonnet` applies everywhere.
    judge_cli = getattr(args, "judge_model", None)
    judge_env = from_env(MODEL_JUDGE_ENV)
    judge_file = from_file("model_judge")
    models["judge"] = (judge_cli or judge_env or global_cli or global_env
                       or judge_file or global_file
                       or MODEL_DEFAULT_PER_WORKER.get("judge", MODEL_DEFAULT))
    heal_cli = getattr(args, "heal_model", None)
    heal_env = from_env(MODEL_HEAL_ENV)
    heal_file = from_file("model_heal")
    models["heal"] = (heal_cli or heal_env or global_cli or global_env
                      or heal_file or global_file
                      or MODEL_DEFAULT_PER_WORKER.get("heal", MODEL_DEFAULT))
    pr_writer_cli = getattr(args, "pr_writer_model", None)
    pr_writer_env = from_env(MODEL_PR_WRITER_ENV)
    pr_writer_file = from_file("model_pr_writer")
    models["pr_writer"] = (pr_writer_cli or pr_writer_env
                           or global_cli or global_env
                           or pr_writer_file or global_file
                           or MODEL_DEFAULT_PER_WORKER.get(
                               "pr_writer", MODEL_DEFAULT))
    # dep_capture is env-var-only (no CLI flag, no leerie.toml key) — it is a
    # post-run worker with no argparse registration. Its opus default comes from
    # the global MODEL_DEFAULT fallback (it is intentionally absent from
    # MODEL_DEFAULT_PER_WORKER).
    dep_capture_env = from_env(MODEL_DEP_CAPTURE_ENV)
    models["dep_capture"] = (dep_capture_env
                             or global_cli or global_env or global_file
                             or MODEL_DEFAULT)
    return models


def resolve_efforts(repo_root: Path, args) -> dict[str, str | None]:
    """Resolve the --effort value for each worker type. Mirrors
    resolve_models() rung-for-rung. Per-worker precedence (highest first):
      1. --effort-<worker> CLI flag
      2. --effort CLI flag (global default for this run)
      3. LEERIE_EFFORT_<WORKER> env var
      4. LEERIE_EFFORT env var
      5. effort_<worker> in leerie.toml
      6. effort in leerie.toml
      7. EFFORT_DEFAULT_PER_WORKER[<worker>] (e.g., planner → "high")
      8. EFFORT_DEFAULT (None — flag omitted from CLI invocation)
    A None value means "do not pass --effort"; claude_p's build() omits
    the flag entirely so the worker inherits Claude's default. CLI values
    are pre-validated by argparse choices=; env and file values are
    rejected via die() when not in EFFORT_VALUES."""
    cfg = repo_root / MODEL_FILE

    def from_env(name: str) -> str | None:
        v = os.environ.get(name, "").strip()
        if not v:
            return None
        if v not in EFFORT_VALUES:
            die(f"{name}={v!r} is not one of {EFFORT_VALUES}")
        return v

    def from_file(key: str) -> str | None:
        v = _read_toml_key(cfg, key)
        if v is None:
            return None
        if v not in EFFORT_VALUES:
            die(f"{cfg}: {key}={v!r} is not one of {EFFORT_VALUES}")
        return v

    global_cli = getattr(args, "effort", None)
    global_env = from_env(EFFORT_ENV)
    global_file = from_file("effort")

    efforts: dict[str, str | None] = {}
    for worker in WORKER_TYPES:
        per_cli = getattr(args, f"effort_{worker}", None)
        per_env = from_env(f"{EFFORT_ENV}_{worker.upper()}")
        per_file = from_file(f"effort_{worker}")
        per_worker_default = EFFORT_DEFAULT_PER_WORKER.get(worker, EFFORT_DEFAULT)
        # Explicit-None chain: every rung is either str or None, so we can
        # collapse with `or` — None falls through to the next rung; the
        # final fallback is EFFORT_DEFAULT (None), meaning "omit --effort".
        efforts[worker] = (per_cli or global_cli or per_env or global_env
                           or per_file or global_file or per_worker_default)
    # Post-run skill workers (judge, heal) are not in WORKER_TYPES so they
    # don't get per-worker --effort-<W> flags, but they still honor the
    # global override so `--effort high` applies everywhere.
    efforts["judge"] = (global_cli or global_env or global_file
                        or EFFORT_DEFAULT_PER_WORKER.get("judge", EFFORT_DEFAULT))
    efforts["heal"] = (global_cli or global_env or global_file
                       or EFFORT_DEFAULT_PER_WORKER.get("heal", EFFORT_DEFAULT))
    efforts["pr_writer"] = (global_cli or global_env or global_file
                            or EFFORT_DEFAULT_PER_WORKER.get(
                                "pr_writer", EFFORT_DEFAULT))
    efforts["dep_capture"] = (global_cli or global_env or global_file
                              or EFFORT_DEFAULT_PER_WORKER.get(
                                  "dep_capture", EFFORT_DEFAULT))
    return efforts


def resolve_judge_dir(repo_root: Path, cli_value: str | None = None) -> str:
    """Resolve the judge output directory name. Order:
    --judge-dir CLI flag → LEERIE_JUDGE_DIR env var →
    judge_dir in leerie.toml → JUDGE_DIR_DEFAULT ("judge-out").
    The value is a plain directory name (or relative path) appended to
    the run dir — not validated against the filesystem at resolve time."""
    return _resolve_str_pref(
        repo_root, cli_value,
        env_var=JUDGE_DIR_ENV, file_key="judge_dir",
        file_name=JUDGE_DIR_FILE, default=JUDGE_DIR_DEFAULT)


def resolve_heal_dir(repo_root: Path, cli_value: str | None = None) -> str:
    """Resolve the heal output directory name. Order:
    --heal-dir CLI flag → LEERIE_HEAL_DIR env var →
    heal_dir in leerie.toml → HEAL_DIR_DEFAULT ("heal-out").
    The value is a plain directory name (or relative path) appended to
    the run dir — not validated against the filesystem at resolve time."""
    return _resolve_str_pref(
        repo_root, cli_value,
        env_var=HEAL_DIR_ENV, file_key="heal_dir",
        file_name=HEAL_DIR_FILE, default=HEAL_DIR_DEFAULT)


def resolve_heal_max_rounds(repo_root: Path, cli_value: int | None = None) -> int:
    """Resolve the heal-loop max-iterations cap. Order:
    --heal-max-rounds CLI flag → LEERIE_HEAL_MAX_ROUNDS env var →
    heal_max_rounds in leerie.toml → HEAL_MAX_ROUNDS_DEFAULT (10).
    An invalid (non-positive) value in env or file is rejected via die()."""
    return _resolve_positive_int_pref(
        repo_root, cli_value,
        env_var=HEAL_MAX_ROUNDS_ENV, file_key="heal_max_rounds",
        file_name=HEAL_MAX_ROUNDS_FILE,
        default=HEAL_MAX_ROUNDS_DEFAULT)


def resolve_heal_success_threshold(repo_root: Path,
                                   cli_value: float | None = None) -> float:
    """Resolve the heal-loop success pass-rate threshold. Order:
    --heal-success-threshold CLI flag → LEERIE_HEAL_SUCCESS_THRESHOLD env var →
    heal_success_threshold in leerie.toml → HEAL_SUCCESS_THRESHOLD_DEFAULT (0.9).
    Value must be in (0, 1]; invalid values in env or file are rejected via die()."""
    if cli_value is not None:
        return cli_value
    env = os.environ.get(HEAL_SUCCESS_THRESHOLD_ENV, "").strip()
    if env:
        try:
            v = float(env)
        except ValueError:
            die(f"{HEAL_SUCCESS_THRESHOLD_ENV}={env!r} is not a float")
        if not (0.0 < v <= 1.0):
            die(f"{HEAL_SUCCESS_THRESHOLD_ENV}={env!r} must be in (0, 1]")
        return v
    cfg = repo_root / HEAL_SUCCESS_THRESHOLD_FILE
    file_val = _read_toml_key(cfg, "heal_success_threshold")
    if file_val is not None:
        try:
            v = float(file_val)
        except ValueError:
            die(f"{cfg}: heal_success_threshold={file_val!r} is not a float")
        if not (0.0 < v <= 1.0):
            die(f"{cfg}: heal_success_threshold={file_val!r} must be in (0, 1]")
        return v
    return HEAL_SUCCESS_THRESHOLD_DEFAULT


async def run_proc(cmd: list[str], *, cwd: str | None = None,
                   timeout: float | None = None) -> subprocess.CompletedProcess:
    """Async equivalent of `subprocess.run(cmd, capture_output=True, text=True)`.
    On timeout, kills the process and raises `subprocess.TimeoutExpired` — same
    semantics callers already handle. One helper everywhere keeps the asyncio
    boilerplate out of the call sites.

    `start_new_session=True` isolates the child into its own POSIX
    session/process group, distinct from leerie's own. This is what lets
    `_terminate_proc_tree` send `os.killpg(proc.pid, ...)` on the
    cleanup path without accidentally signaling the orchestrator's own
    group. The flag is a no-op on Windows."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        if timeout is None:
            stdout, stderr = await proc.communicate()
        else:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _terminate_proc_tree(proc)
        raise subprocess.TimeoutExpired(cmd, timeout)
    except BaseException:
        # Any other exception (CancelledError from a parent abort, an unexpected
        # OSError/BrokenPipeError from the PIPE, etc.) must still leave no
        # orphan subtree. Terminate the process group then re-raise.
        await _terminate_proc_tree(proc)
        raise
    # Success path needs no descendant sweep: `run_proc` is used for short
    # synchronous commands (git, smoke tests, cleanup helpers) that do not
    # background tool calls the way `claude -p` workers do via Claude Code's
    # Bash tool. The detached-session leak class addressed by
    # `_DescendantTracker` is specific to `_invoke`, not here.
    return subprocess.CompletedProcess(
        cmd,
        proc.returncode if proc.returncode is not None else 0,
        stdout.decode(errors="replace") if stdout else "",
        stderr.decode(errors="replace") if stderr else "",
    )


async def run_streaming(
    cmd: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    log_path: Path | None = None,
    label: str | None = None,
    verbosity: str = "stream",
    line_prefix: str = "  | ",
    tail_lines: int = 40,
) -> tuple[int, str]:
    """Run a subprocess with stdout+stderr streamed live, persisted to a
    log file, and tailed for error reporting. Use this instead of
    `run_proc` for *long-running* commands where:

      - the user should see progress in real time (no silent multi-minute
        hangs while a buffered pipe fills),
      - the full output should land on disk regardless of verbosity, and
      - the last N lines should be available in any exception we raise.

    Returns `(returncode, tail)`. On timeout raises
    `subprocess.TimeoutExpired` (same shape `run_proc` raises) with
    `output` populated with the captured tail so callers can include it
    in their error message. On any other exception, terminates the
    process tree via `_terminate_proc_tree` (same exception-safety
    contract as `run_proc`).

    `verbosity`:
      - "quiet": no stdout echo; log file still gets every line.
      - anything else ("normal", "stream", "debug"): echo each line
        through `log()` with `line_prefix`.

    `label` is appended to the persistent log's section header — useful
    when multiple commands write to the same log file (provision.log
    accumulates `mise install`, `.leerie-setup.sh`, etc.).

    The DRY counterpart to `run_proc`: identical contract for the
    process-group/exception-safety story, different I/O shape. Pick
    `run_proc` for short captures (git plumbing, smoke tests) where
    the synchronous-collect shape is what the caller wants; pick
    `run_streaming` for anything that might run long enough that a
    silent terminal would mislead the user.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )

    tail: deque[str] = deque(maxlen=tail_lines)
    log_fh = None
    if log_path is not None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fh = log_path.open("a", buffering=1)  # line-buffered
            header = f"=== {label or ' '.join(cmd)} (cwd={cwd or '.'}) ==="
            log_fh.write(header + "\n")
        except OSError:
            log_fh = None

    echo = verbosity != "quiet"

    async def read_loop() -> None:
        # proc.stdout is guaranteed non-None because we passed
        # stdout=asyncio.subprocess.PIPE above; no runtime check needed.
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip("\r\n")
            tail.append(line)
            if log_fh is not None:
                try:
                    log_fh.write(line + "\n")
                except OSError:
                    pass
            if echo:
                log(f"{line_prefix}{line}")

    try:
        if timeout is None:
            await asyncio.gather(read_loop(), proc.wait())
        else:
            await asyncio.wait_for(
                asyncio.gather(read_loop(), proc.wait()),
                timeout=timeout,
            )
    except asyncio.TimeoutError:
        if log_fh is not None:
            try:
                log_fh.write(f"=== TIMEOUT after {timeout}s ===\n")
            except OSError:
                pass
        await _terminate_proc_tree(proc)
        captured = "\n".join(tail)
        exc = subprocess.TimeoutExpired(cmd, timeout)
        # Standard TimeoutExpired exposes `output` and `stderr`; populate
        # `output` with the merged tail so callers can include it in
        # their die() message without re-reading the log file.
        exc.output = captured
        if log_fh is not None:
            try:
                log_fh.close()
            except OSError:
                pass
        raise exc
    except BaseException:
        await _terminate_proc_tree(proc)
        if log_fh is not None:
            try:
                log_fh.close()
            except OSError:
                pass
        raise

    if log_fh is not None:
        try:
            log_fh.close()
        except OSError:
            pass

    rc = proc.returncode if proc.returncode is not None else 0
    return rc, "\n".join(tail)


async def gather_or_cancel(*aws):
    """Like asyncio.gather, but on the first exception cancel every other
    in-flight task and await its finalization before re-raising. Paired with
    run_proc's child-killing exception handler, this terminates in-flight
    `claude -p` subprocesses immediately on a failed-run abort instead of
    letting them burn the worker budget for up to worker_timeout_sec."""
    tasks = [asyncio.ensure_future(a) for a in aws]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            if not t.done():
                t.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except BaseException:
            # If the cleanup itself is cancelled or errors, drop that
            # secondary exception so the bare `raise` below re-raises the
            # original — the user wants the real failure cause, not noise
            # from the cleanup phase.
            pass
        raise


async def run_script(name: str, *args: str) -> subprocess.CompletedProcess:
    """Run one of the bundled git worktree scripts in the target repo."""
    return await run_proc(["bash", str(SCRIPTS / name), *args], cwd=os.getcwd())


# =========================================================================
# deterministic enforcement — no LLM involvement
#
# Prompts are advisory; code enforces. Every rule that can be checked
# mechanically lives here, not in a prompt. The LLM handles: understanding
# intent, writing code, decomposing tasks, resolving semantic conflicts.
# Code handles: counting, hashing, graph invariants, file existence, running
# test suites, enforcing structural rules.
# =========================================================================

async def preflight(leerie_dir: Path, verbosity: str = VERBOSITY_DEFAULT,
                    skip_smoke: bool = False, no_push: bool = False) -> None:
    """Hard checks before any LLM work. Fails fast rather than wasting workers."""

    # 1. git user identity — missing config causes implementer commits to fail
    for key in ("user.email", "user.name"):
        r = await run_proc(["git", "config", key])
        if r.returncode != 0 or not r.stdout.strip():
            die(f"git {key} is not configured. "
                f"Run: git config --global {key} \"<value>\"")

    # 2. working tree must be clean — a dirty tree produces ambiguous diffs
    r = await run_proc(["git", "status", "--porcelain"])
    dirty = [l for l in r.stdout.splitlines() if not l.startswith("??")]
    if dirty:
        die(f"working tree has {len(dirty)} modified/staged file(s). "
            "Commit or stash before running leerie.")

    # 3. external 'leerie' branch — a bare branch named 'leerie' occupies
    #    the ref path that leerie's namespaced branches (leerie/runs/*,
    #    leerie/subtasks/*) need as a directory in git's loose ref store.
    r = await run_proc(["git", "show-ref", "--verify", "--quiet",
                        "refs/heads/leerie"])
    if r.returncode == 0:
        die("a branch named 'leerie' exists in this repository. "
            "This conflicts with leerie's internal branch namespace "
            "(leerie/runs/*, leerie/subtasks/*) — git cannot create "
            "branches under 'leerie/' while 'leerie' exists as a branch.\n"
            "Rename or delete it:\n"
            "  git branch -m leerie leerie-old    # rename (preserves commits)\n"
            "  git branch -D leerie               # delete (if fully merged)")

    # 4. claude CLI version is recent enough for `--json-schema` in -p mode.
    #    Runs even when --skip-smoke is set: --skip-smoke is for skipping the
    #    *live* model call (auth + a turn), not for skipping local CLI sanity
    #    checks. Without this, a stale CLI fails the smoke test with a cryptic
    #    'unknown option' that tells the user nothing actionable.
    _check_claude_cli_version()

    # 5. gh CLI preflight moved to the host launcher (DESIGN §6
    #    *Finalization*). The launcher checks `gh auth status` + origin
    #    remote presence before spinning up this container; if they
    #    fail, the container never starts.

    # 6. live smoke-test: auth + --output-format stream-json + --json-schema inline.
    #    Catches auth failures before a 40-worker run starts. Streams so a slow
    #    Opus / heavy-context startup is visible — the previous max_turns=1
    #    failure was invisible in the non-streaming mode until exit.
    if not skip_smoke:
        log("preflight: smoke-testing claude -p…")
        cmd = ["claude", "-p", "respond with the single word ok",
               "--output-format", "stream-json",
               "--verbose",
               "--json-schema", '{"type":"object"}']
        try:
            envelope = await _invoke(cmd, cwd=os.getcwd(), timeout=90,
                                     sid="smoke",
                                     leerie_dir=leerie_dir,
                                     verbosity=verbosity)
        except subprocess.TimeoutExpired:
            die("claude -p smoke test timed out — auth issue or network problem")
        except WorkerError as e:
            die(f"claude -p smoke test failed: {e}")
        if envelope.get("is_error"):
            die(f"claude -p smoke test returned an error: "
                f"{envelope.get('api_error_status') or envelope.get('result')}")
        log("preflight: ok")


_ID_PREFIXES = frozenset(f"{v}-" for v in CATEGORY_ABBREV.values())


_VALID_EXTENTS = frozenset({"in_plan", "external"})


# ---- Mechanical-check functions (CRITIC-pattern feedback) -------------- #
# Each returns a list[str] of issue descriptions.  Empty = clean.
# Pure Python, no LLM — the orchestrator injects these as external
# feedback on re-invocation.  See _run_checked_loop.


def _confidence_issues(
    conf: dict, axes: list[str], threshold: float = 9.0,
) -> list[str]:
    """Return one LOW_CONFIDENCE issue per axis below *threshold*.

    Returns empty when *conf* has no numeric axes at all — the schema
    enforces their presence in real runs; an empty dict here means the
    caller passed ``result.get("confidence") or {}`` on a dict that
    lacked the field entirely (e.g. a test stub)."""
    if not any(isinstance(conf.get(ax), (int, float)) for ax in axes):
        return []
    out: list[str] = []
    for ax in axes:
        val = conf.get(ax)
        if not isinstance(val, (int, float)) or val < threshold:
            out.append(
                f"LOW_CONFIDENCE: axis {ax!r} is {val} (threshold "
                f"{threshold})")
    return out


def check_classifier_output(result: dict, repo_root: Path) -> list[str]:
    """Thin mechanical checks on the classifier's category selection."""
    issues: list[str] = []
    cats = result.get("categories", [])

    _DIR_SIGNALS: dict[str, list[str]] = {
        "infrastructure": ["infra", "cdk", "terraform", "pulumi"],
        "documentation": ["docs", "doc"],
    }
    for cat, dirs in _DIR_SIGNALS.items():
        if cat in cats and not any(
                (repo_root / d).exists() for d in dirs):
            issues.append(
                f"CATEGORY_NO_DIR: classified as {cat!r} but no "
                f"{'/'.join(dirs)} directory found at repo root")

    for q in result.get("questions", []):
        if not (q.get("why_underivable") or "").strip():
            issues.append(
                f"EMPTY_WHY: question {q.get('id', '?')!r} has "
                "empty why_underivable")

    if len(cats) > 4:
        issues.append(
            f"MANY_CATEGORIES: {len(cats)} categories — typical "
            "tasks span 1–3")
    _SAME_WORK_RISK_PAIRS = [
        ("bug-fixing", "feature-implementation"),
        ("bug-fixing", "refactoring"),
        ("feature-implementation", "refactoring"),
    ]
    cats_set = set(cats)
    for a, b in _SAME_WORK_RISK_PAIRS:
        if a in cats_set and b in cats_set:
            issues.append(
                f"SAME_WORK_RISK: {a!r} and {b!r} both selected — "
                "these categories often describe the same intent "
                "under different labels (e.g. 'complete translations' "
                "as both bug-fix and feature). Apply the same-work "
                "test: would planners in each category modify the same "
                "files for the same reason? If yes, keep only the "
                "best-fitting category. If they produce genuinely "
                "different deliverables, keep both."
            )
    issues.extend(_confidence_issues(
        result.get("confidence") or {}, ["classification"]))
    return issues


# ---------------------------------------------------------------------------
# Migration-surface completeness (DESIGN §5)
# ---------------------------------------------------------------------------

_MIGRATION_SIGNAL_RE = re.compile(
    r"replac(?:es?|ing)\s+(?:direct\s+)?[`'\"]?([a-zA-Z_][a-zA-Z0-9_.]*)[`'\"]?"
    r"|migrat(?:es?|ing)\s+from\s+[`'\"]?([a-zA-Z_][a-zA-Z0-9_.]*)[`'\"]?"
    r"|extract(?:s|ing)\s+[`'\"]?([a-zA-Z_][a-zA-Z0-9_.]*)[`'\"]?\s+(?:from|replacing|as\b)"
    r"|new\s+(?:accessor|seam|helper|abstraction)\s+(?:for|replacing)\s+[`'\"]?([a-zA-Z_][a-zA-Z0-9_.]*)[`'\"]?",
    re.IGNORECASE,
)

_MIGRATION_SURFACE_THRESHOLD = 5


def _grep_old_pattern(pattern: str, repo_root: Path) -> set[str]:
    """Grep *repo_root* for *pattern*, return set of relative file paths."""
    search_dir = repo_root / "src" if (repo_root / "src").is_dir() else repo_root
    try:
        cp = subprocess.run(
            ["grep", "-rl", "--include=*.ts", "--include=*.tsx",
             "--include=*.js", "--include=*.jsx", "--include=*.py",
             "--include=*.rb", "--include=*.go", "--include=*.java",
             "--include=*.rs", "--include=*.cs",
             pattern, str(search_dir)],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return set()
    files: set[str] = set()
    for line in cp.stdout.splitlines():
        line = line.strip()
        if line:
            try:
                files.add(str(Path(line).relative_to(repo_root)))
            except ValueError:
                files.add(line)
    return files


def _check_migration_surface(
    subtasks: list[dict], repo_root: Path,
) -> list[str]:
    """UNCOVERED_MIGRATION_SURFACE check (DESIGN §5)."""
    issues: list[str] = []
    all_touched: set[str] = set()
    for s in subtasks:
        all_touched.update(s.get("files_likely_touched") or [])

    for s in subtasks:
        sid = s.get("id", "?")
        text = (s.get("intent") or "") + " " + (s.get("investigation_notes") or "")
        for m in _MIGRATION_SIGNAL_RE.finditer(text):
            old_pattern = next((g for g in m.groups() if g), None)
            if not old_pattern or len(old_pattern) < 4:
                continue
            grep_hits = _grep_old_pattern(old_pattern, repo_root)
            if not grep_hits:
                continue
            uncovered = grep_hits - all_touched
            if len(uncovered) > _MIGRATION_SURFACE_THRESHOLD:
                sample = sorted(uncovered)[:10]
                issues.append(
                    f"UNCOVERED_MIGRATION_SURFACE: {sid} introduces "
                    f"replacement for {old_pattern!r} but {len(uncovered)} "
                    f"of {len(grep_hits)} files containing the old pattern "
                    f"are not in any subtask's files_likely_touched. "
                    f"Uncovered sample: {sample}")
    return issues


# ---------------------------------------------------------------------------
# P1 recursive decomposition (DESIGN §5½ (P1))
# ---------------------------------------------------------------------------

def partition_files(files: list[str], chunk_size: int) -> list[list[str]]:
    """Partition *files* into non-overlapping chunks of at most *chunk_size*.

    100% coverage + 0 overlap guaranteed by construction: every element of
    *files* appears in exactly one chunk, and no element appears in more than
    one chunk. The last chunk may be smaller than *chunk_size*.

    Used by recursive_decompose() for migration sweeps — the exhaustive file
    list comes from P6 / _grep_old_pattern; the splitter LLM only labels the
    pre-computed chunks rather than deciding which files go where (the
    measured LLM-drops-14/29 correction from F1-build-measure.md)."""
    if not files or chunk_size < 1:
        return [list(files)] if files else []
    chunks: list[list[str]] = []
    for i in range(0, len(files), chunk_size):
        chunks.append(files[i: i + chunk_size])
    return chunks


def _migration_child(subtask: dict, chunk: list[str], cid: str,
                     title: str, criteria: str) -> dict:
    """Build one migration-chunk child subtask from its (code-fixed) file
    partition plus a title + success_criteria_seed. Inherits the parent's
    graph edges/intent; the files are the pre-computed chunk, never re-decided."""
    return {
        "id": cid,
        "title": title,
        "success_criteria_seed": criteria,
        "files_likely_touched": chunk,
        "intent": subtask.get("intent", ""),
        "scope_note": subtask.get("scope_note", ""),
        "depends_on": subtask.get("depends_on", []),
        "requires": subtask.get("requires", []),
        "provides": subtask.get("provides", []),
        "size": "medium",
        "investigation_notes": subtask.get("investigation_notes", ""),
    }


def _deterministic_chunk_label(subtask: dict, chunk: list[str],
                               idx: int, total: int) -> tuple[str, str]:
    """Fallback title + criteria for a migration chunk when the splitter
    label-only worker is unavailable or returns a mismatched set. Distinct
    per chunk BY CONSTRUCTION (idx + file list) so children never collide."""
    parent_title = subtask.get("title", "migration")
    title = f"{parent_title} (part {idx + 1}/{total})"
    files_txt = ", ".join(chunk)
    criteria = (
        f"{subtask.get('success_criteria_seed', '')}\n"
        f"Scope: only these files — {files_txt}."
    ).strip()
    return title, criteria


async def _label_migration_chunks(
    subtask: dict, chunks: list[list[str]], base_id: str, depth: int,
    st: "State", caps: dict, models: dict[str, str],
    efforts: dict[str, str | None], repo_root: Path,
    wrap_repo_map: Callable[[str], str],
) -> list[dict]:
    """LABEL-ONLY splitter pass for migration chunks (DESIGN §5½ — "the LLM
    only labels"). The file→chunk partition is fixed by partition_files();
    this asks the splitter to write a distinct title + success_criteria_seed
    per chunk (keyed by its pre-assigned id) so children are not identical
    parent-copies. §12 code-enforces coverage: if the worker returns a
    mismatched or incomplete label set, every chunk falls back to a distinct
    deterministic label rather than crashing or shipping identical titles."""
    ids = [f"{base_id}-{i + 1}" for i in range(len(chunks))]
    # Deterministic labels are the guaranteed-distinct baseline; the worker
    # only upgrades them.
    labels: dict[str, tuple[str, str]] = {
        cid: _deterministic_chunk_label(subtask, chunk, i, len(chunks))
        for i, (cid, chunk) in enumerate(zip(ids, chunks))
    }

    chunk_spec = [{"id": cid, "files_likely_touched": chunk}
                  for cid, chunk in zip(ids, chunks)]
    st.bump_workers(caps)
    sys_prompt = load_prompt("splitter")
    user_prompt = wrap_repo_map(
        "LABEL PRE-PARTITIONED MIGRATION CHUNKS (label-only mode).\n"
        "The file partition below is FIXED — do NOT move, add, or drop files. "
        "For each chunk id, write a concise distinct `title` and a "
        "`success_criteria_seed`. Return one child per id, preserving ids and "
        "files_likely_touched exactly.\n\n"
        "PARENT SUBTASK:\n"
        f"{json.dumps(subtask, indent=2)}\n\n"
        "CHUNKS TO LABEL:\n"
        f"{json.dumps(chunk_spec, indent=2)}"
    )
    try:
        result = await claude_p(
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            schema_key="splitter",
            cwd=str(repo_root),
            allowed_tools=INSPECT_TOOLS,
            max_turns=30,
            autonomous=False,
            caps=caps,
            st=st,
            model=models.get("splitter", MODEL_DEFAULT),
            effort=efforts.get("splitter"),
            sid=f"splitter-label-{base_id}-d{depth}",
        )
        for child in (result.get("children") or []):
            cid = child.get("id")
            title = (child.get("title") or "").strip()
            crit = (child.get("success_criteria_seed") or "").strip()
            if cid in labels and title and crit:
                labels[cid] = (title, crit)
    except WorkerError:
        # Worker crashed — keep the distinct deterministic labels (§12: a
        # split must never silently produce identical children).
        log(f"recursive_decompose: label-only splitter failed for "
            f"{base_id}; using deterministic chunk labels")

    return [
        _migration_child(subtask, chunk, cid, *labels[cid])
        for cid, chunk in zip(ids, chunks)
    ]


async def recursive_decompose(
    subtask: dict,
    depth: int,
    st: "State",
    caps: dict,
    models: dict[str, str],
    efforts: dict[str, str | None],
    repo_root: Path,
    *,
    repo_map: dict | None = None,
    _parent_score: float | None = None,
    _noprogress_count: int = 0,
) -> list[dict]:
    """Recursively decompose *subtask* until leaves pass the P1 fit threshold.

    Algorithm (DESIGN §5½ (P1)):
      1. Judge the subtask's Task-Context Fit via the fit_judge worker.
      2. If score >= decompose_fit_threshold or depth >= decompose_max_depth:
         return [subtask] (leaf).
      3. Split using partition_files (migration) or the splitter worker
         (coupled minority). Every judge/split call goes through st.bump_workers.
      4. No-progress guard: if decompose_noprogress_rounds consecutive rounds
         produce no child whose score exceeds the parent's, accept as leaf with
         a warning.
      5. Recurse into each child at depth+1; return flattened leaves.

    *repo_map* is the pre-built (once, in phase_plan) global symbol graph;
    when present, each fit_judge/splitter call is grounded with a per-node
    personalized-PageRank subgraph ranked to *this* subtask's files (DESIGN
    §5½ P6 — "feed the same to the splitter, re-ranked to each node's files").
    ``None`` when skip_repo_map is set or the map could not be built — the
    workers then run on the raw subtask spec (graceful degrade).

    Returns a flat list of leaf subtasks ready for schedule()."""
    max_depth = caps.get("decompose_max_depth",
                         DEFAULT_CAPS["decompose_max_depth"])
    threshold = caps.get("decompose_fit_threshold",
                         DEFAULT_CAPS["decompose_fit_threshold"])
    noprogress_max = caps.get("decompose_noprogress_rounds",
                              DEFAULT_CAPS["decompose_noprogress_rounds"])

    # Per-node P6 grounding: re-rank the global repo-map to this subtask's
    # files so the fit_judge/splitter see the local structural neighborhood
    # (DESIGN §5½). Empty seed_symbols — subtasks carry files, not symbols;
    # mirrors phase_plan's planner-ctx rank_repo_map(rm, seeds, []) call.
    node_map_text = ""
    if repo_map is not None:
        try:
            node_files = [str(Path(f)) for f in
                          (subtask.get("files_likely_touched") or [])]
            node_map_text = rank_repo_map(repo_map, node_files, [])
        except Exception:
            node_map_text = ""  # degrade silently; worker runs without it

    def _with_repo_map(prompt: str) -> str:
        if not node_map_text:
            return prompt
        return (
            f"{prompt}\n\nRANKED REPO-MAP SUBGRAPH (structural context for "
            f"this subtask's files):\n{node_map_text}"
        )

    # --- judge step ----------------------------------------------------------
    st.bump_workers(caps)
    sys_prompt = load_prompt("fit_judge")
    user_prompt = _with_repo_map(
        "SUBTASK TO JUDGE:\n"
        f"{json.dumps(subtask, indent=2)}\n\n"
        "Score this subtask's P1 Task-Context Fit (0–1) and return your verdict."
    )
    judge_result = await claude_p(
        system_prompt=sys_prompt,
        user_prompt=user_prompt,
        schema_key="fit_judge",
        cwd=str(repo_root),
        allowed_tools=INSPECT_TOOLS,
        max_turns=30,
        autonomous=False,
        caps=caps,
        st=st,
        model=models.get("fit_judge", MODEL_DEFAULT),
        effort=efforts.get("fit_judge"),
        sid=f"fit-judge-{subtask.get('id', 'x')}-d{depth}",
    )
    score: float = judge_result.get("score", 0.0)

    # --- leaf check ----------------------------------------------------------
    if score >= threshold or depth >= max_depth:
        if depth >= max_depth and score < threshold:
            log(
                f"recursive_decompose: depth cap ({max_depth}) reached for "
                f"{subtask.get('id', '?')} (score={score:.2f}); accepting as leaf"
            )
        return [subtask]

    # --- no-progress guard ---------------------------------------------------
    if _parent_score is not None and score <= _parent_score:
        new_noprogress = _noprogress_count + 1
    else:
        new_noprogress = 0

    if new_noprogress >= noprogress_max:
        log(
            f"recursive_decompose: no-progress guard triggered for "
            f"{subtask.get('id', '?')} after {noprogress_max} rounds "
            f"(score={score:.2f}); accepting as leaf with warning"
        )
        return [subtask]

    # --- split step ----------------------------------------------------------
    files = subtask.get("files_likely_touched") or []
    # Migration path (dominant case, ~84%): code-partitions, LLM only labels.
    # Coupled-minority path: LLM-splitter decides the partition.
    chunk_size = 8
    if len(files) > chunk_size:
        # Migration sweep: partition_files guarantees 100% coverage + 0 overlap
        # BY CONSTRUCTION (the code owns the partition — DESIGN §5½). The
        # splitter worker is then invoked in LABEL-ONLY mode: it titles and
        # writes success criteria per pre-computed chunk; it must NOT move
        # files. This is the plan's "LLM only labels" rule — a bare parent-copy
        # gives every chunk an identical, useless title.
        chunks = partition_files(files, chunk_size)
        base_id = subtask.get("id", "split")
        children = await _label_migration_chunks(
            subtask, chunks, base_id, depth, st, caps, models, efforts,
            repo_root, _with_repo_map)
    else:
        # Coupled minority: LLM splitter decides the partition using
        # structural seams from the repo-map; backstopped by
        # _check_migration_surface at the plan level.
        st.bump_workers(caps)
        sys_prompt_s = load_prompt("splitter")
        user_prompt_s = _with_repo_map(
            "SUBTASK TO SPLIT:\n"
            f"{json.dumps(subtask, indent=2)}\n\n"
            "Split this subtask along real structural seams. Return child subtasks."
        )
        split_result = await claude_p(
            system_prompt=sys_prompt_s,
            user_prompt=user_prompt_s,
            schema_key="splitter",
            cwd=str(repo_root),
            allowed_tools=INSPECT_TOOLS,
            max_turns=30,
            autonomous=False,
            caps=caps,
            st=st,
            model=models.get("splitter", MODEL_DEFAULT),
            effort=efforts.get("splitter"),
            sid=f"splitter-{subtask.get('id', 'x')}-d{depth}",
        )
        children = split_result.get("children") or []
        if not children:
            # Splitter produced no children; accept the subtask as a leaf.
            log(
                f"recursive_decompose: splitter returned no children for "
                f"{subtask.get('id', '?')}; accepting as leaf"
            )
            return [subtask]

    # --- recurse into children -----------------------------------------------
    leaves: list[dict] = []
    for child in children:
        child_leaves = await recursive_decompose(
            child, depth + 1, st, caps, models, efforts, repo_root,
            repo_map=repo_map,
            _parent_score=score,
            _noprogress_count=new_noprogress,
        )
        leaves.extend(child_leaves)
    return leaves


def check_planner_output(
    result: dict, repo_root: Path, domain: str,
) -> list[str]:
    """Rich mechanical checks on a single planner domain's output."""
    issues: list[str] = []
    subtasks = result.get("subtasks", [])
    prefix = CATEGORY_ABBREV.get(domain, "") + "-"

    for s in subtasks:
        sid = s.get("id", "?")
        for f in s.get("files_likely_touched", []):
            full = repo_root / f
            if not full.exists() and not full.parent.exists():
                has_ancestor = False
                ancestor = full.parent.parent
                while ancestor != repo_root and ancestor != ancestor.parent:
                    if ancestor.exists():
                        has_ancestor = True
                        break
                    ancestor = ancestor.parent
                if not has_ancestor:
                    issues.append(
                        f"PHANTOM_PATH: {sid} lists {f!r} in "
                        "files_likely_touched but no ancestor "
                        "directory exists under the repo root")

    all_ids = {s["id"] for s in subtasks}
    for s in subtasks:
        sid = s.get("id", "?")
        for dep in s.get("depends_on", []) or []:
            if dep.startswith(prefix) and dep not in all_ids:
                issues.append(
                    f"DANGLING_DEP: {sid} depends_on {dep!r} which "
                    "does not exist in this plan")

    for s in subtasks:
        if not (s.get("success_criteria_seed") or "").strip():
            issues.append(
                f"EMPTY_CRITERIA: {s.get('id', '?')} has empty "
                "success_criteria_seed")

    for s in subtasks:
        if (s.get("size") or "").lower() == "large":
            issues.append(
                f"OVERSIZED: {s.get('id', '?')} has size='large' "
                "— split it")

    file_owners: dict[str, list[str]] = {}
    for s in subtasks:
        for f in s.get("files_likely_touched", []):
            file_owners.setdefault(f, []).append(s.get("id", "?"))
    for f, owners in file_owners.items():
        if len(owners) > 1:
            issues.append(
                f"INTRA_DOMAIN_OVERLAP: {f!r} touched by "
                f"{owners} — consider merging or splitting")

    for s in subtasks:
        bad = [f for f in (s.get("files_likely_touched") or [])
               if isinstance(f, str) and is_protected_path(f)]
        if bad:
            issues.append(
                f"PROTECTED_PATH: {s.get('id', '?')} lists "
                f"protected path(s) {bad}")

    # Intra-domain cycle via simple DFS.
    deps: dict[str, list[str]] = {}
    for s in subtasks:
        sid = s.get("id", "?")
        deps[sid] = [
            d for d in (s.get("depends_on") or [])
            if d.startswith(prefix) and d in all_ids]
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {sid: WHITE for sid in all_ids}
    cycle_found = False
    for start in all_ids:
        if color[start] != WHITE:
            continue
        stack = [start]
        while stack and not cycle_found:
            node = stack[-1]
            if color[node] == WHITE:
                color[node] = GRAY
                for dep in deps.get(node, []):
                    if color.get(dep) == GRAY:
                        cycle_found = True
                        break
                    if color.get(dep) == WHITE:
                        stack.append(dep)
            else:
                color[node] = BLACK
                stack.pop()
    if cycle_found:
        issues.append(
            "INTRA_DOMAIN_CYCLE: dependency cycle detected within "
            f"this domain ({domain})")

    issues.extend(_check_migration_surface(subtasks, repo_root))

    # `decomposition_quality` is retained in the planner schema as an advisory
    # self-report, but is NO LONGER a gating axis (DESIGN §5½): the independent
    # `fit_judge` in recursive_decompose is the authoritative decomposition
    # gate, which removes the self-grading bias of letting the planner grade
    # its own decomposition. Only `task_understanding` gates here.
    issues.extend(_confidence_issues(
        result.get("confidence") or {},
        ["task_understanding"]))
    return issues


def check_reconciler_output(
    output: dict, plans: list[dict],
) -> list[str]:
    """Mechanical checks on the reconciler's output beyond the existing
    acyclicity / size / unresolved gates."""
    issues: list[str] = []

    all_provides: set[str] = set()
    for plan in plans:
        for s in plan.get("subtasks", []):
            all_provides.update(s.get("provides", []) or [])

    for r in output.get("renames", []) or []:
        if r.get("to") and r["to"] not in all_provides:
            issues.append(
                f"RENAME_TO_NOWHERE: rename on {r.get('sid', '?')} "
                f"from {r.get('from', '?')!r} to {r['to']!r} but "
                "no subtask provides that tag")

    for s in output.get("added_subtasks", []) or []:
        sid = s.get("id", "")
        if sid and not any(sid.startswith(p) for p in _ID_PREFIXES):
            issues.append(
                f"BAD_PREFIX: added subtask {sid!r} does not start "
                "with a valid prefix")
        if sid and sid in (s.get("depends_on") or []):
            issues.append(
                f"SELF_DEP: added subtask {sid!r} depends on itself")

    issues.extend(_confidence_issues(
        output.get("confidence") or {}, ["reconciliation"]))
    return issues


def check_overlap_judge_output(
    output: dict, plans: list[dict], repo_root: Path,
) -> list[str]:
    """Mechanical checks on the overlap judge's collision list."""
    issues: list[str] = []
    collisions = output.get("collisions", []) or []

    by_id: dict[str, dict] = {}
    for plan in plans:
        for s in plan.get("subtasks", []):
            by_id[s["id"]] = s

    _CODE_EXTS = frozenset(
        ".ts .tsx .py .go .rs .java .rb .js .jsx .css .scss".split())
    for c in collisions:
        artifact = c.get("artifact", "")
        if "/" in artifact or any(
                artifact.endswith(ext) for ext in _CODE_EXTS):
            if not (repo_root / artifact).exists():
                issues.append(
                    f"PHANTOM_ARTIFACT: collision "
                    f"{c.get('a_sid', '?')} <-> {c.get('b_sid', '?')} "
                    f"names artifact {artifact!r} which does not "
                    "exist in the repo")

    for c in collisions:
        a = by_id.get(c.get("a_sid", ""), {})
        b = by_id.get(c.get("b_sid", ""), {})
        a_files = set(a.get("files_likely_touched", []) or [])
        b_files = set(b.get("files_likely_touched", []) or [])
        if a_files and b_files and not (a_files & b_files):
            issues.append(
                f"NO_FILE_OVERLAP: collision "
                f"{c.get('a_sid', '?')} <-> {c.get('b_sid', '?')} "
                "but they share no files_likely_touched")

    all_requires_tags: set[str] = set()
    for plan in plans:
        for s in plan.get("subtasks", []):
            for r in s.get("requires", []) or []:
                tag = r.get("tag", "") if isinstance(r, dict) else ""
                if tag:
                    all_requires_tags.add(tag)
    for c in collisions:
        dropped_sid = None
        if c.get("resolution") == "drop_a":
            dropped_sid = c.get("a_sid")
        elif c.get("resolution") == "drop_b":
            dropped_sid = c.get("b_sid")
        if dropped_sid and dropped_sid in by_id:
            dropped_provides = set(
                by_id[dropped_sid].get("provides", []) or [])
            orphaned = dropped_provides & all_requires_tags
            if orphaned:
                issues.append(
                    f"DROP_BREAKS_GRAPH: dropping {dropped_sid!r} "
                    f"would remove provides tags {orphaned} that "
                    "other subtasks require")

    issues.extend(_confidence_issues(
        output.get("confidence") or {}, ["judgment"]))
    return issues


def check_provision_output(
    result: dict, repo_root: Path,
) -> list[str]:
    """Mechanical checks on the provision LLM fallback's recipe."""
    issues: list[str] = []
    recipe = result.get("recipe", []) or []

    _LOCKFILE_TO_PM: dict[str, str] = {
        "pnpm-lock.yaml": "pnpm", "yarn.lock": "yarn",
        "package-lock.json": "npm", "bun.lockb": "bun",
        "bun.lock": "bun", "uv.lock": "uv",
        "poetry.lock": "poetry", "Pipfile.lock": "pipenv",
    }
    detected_pms: set[str] = set()
    for lf, pm in _LOCKFILE_TO_PM.items():
        if (repo_root / lf).exists():
            detected_pms.add(pm)

    for i, entry in enumerate(recipe):
        wd = entry.get("working_dir", ".")
        if wd != "." and not (repo_root / wd).is_dir():
            issues.append(
                f"MISSING_WORKDIR: recipe[{i}] working_dir="
                f"{wd!r} does not exist")
        cmd = entry.get("command", [])
        if cmd and cmd[0] in ("npm", "yarn", "pnpm", "bun"):
            if detected_pms and cmd[0] not in detected_pms:
                issues.append(
                    f"WRONG_PM: recipe uses {cmd[0]!r} but repo "
                    f"has lockfile(s) for {detected_pms}")

    has_lockfiles = any(
        (repo_root / lf).exists() for lf in _LOCKFILE_TO_PM)
    if not recipe and has_lockfiles:
        issues.append(
            "EMPTY_RECIPE: recipe is empty but repo has lockfile(s)")

    issues.extend(_confidence_issues(
        result.get("confidence") or {}, ["recipe_correctness"]))
    return issues


def check_integrator_output(result: dict) -> list[str]:
    """Confidence gate for the integrator."""
    return _confidence_issues(
        result.get("confidence") or {}, ["resolution"])


def check_implementer_output(
    result: dict, subtask: dict, actual_files: set[str],
) -> list[str]:
    """Mechanical checks on an implementer's complete result."""
    issues: list[str] = []
    planned = set(subtask.get("files_likely_touched", []) or [])

    if planned and actual_files:
        if not (actual_files & planned):
            issues.append(
                f"NO_PLANNED_FILES_TOUCHED: none of the planned "
                f"files were modified — planned: "
                f"{sorted(planned)[:5]}")

    if result.get("status") == "complete":
        for cr in result.get("criteria_results", []) or []:
            if cr.get("met") is False:
                issues.append(
                    f"UNMET_CRITERION: claims complete but "
                    f"criterion {cr.get('criterion', '?')!r} "
                    "is not met")

    return issues


# ---- Task-referenced file extraction (CRITIC correlated-error breaker) - #
# When the task string references files (glob patterns or explicit paths),
# the orchestrator mechanically extracts structural elements (headings,
# YAML keys, numbered items) and injects them as an external coverage
# reference into the planner's prompt.  This breaks the correlated-error
# ceiling identified by "The Specification as Quality Gate" (Mar 2026,
# arxiv 2603.25773).

_GLOB_CHARS = frozenset("*?[{")
_BRACE_RE = re.compile(r'\{([^}]+)\}')


def _expand_braces(pattern: str) -> list[str]:
    """Expand shell-style ``{a,b}`` brace groups into multiple patterns.

    Python's ``glob.glob`` does not support brace expansion, but task
    strings commonly use it (e.g. ``spec-*.{md,yaml}``).  Recursive
    so nested braces work."""
    m = _BRACE_RE.search(pattern)
    if not m:
        return [pattern]
    prefix, suffix = pattern[:m.start()], pattern[m.end():]
    expanded: list[str] = []
    for alt in m.group(1).split(","):
        expanded.extend(_expand_braces(prefix + alt.strip() + suffix))
    return expanded


def glob_task_references(task: str, repo_root: Path) -> list[Path]:
    """Find file references in the task string via glob expansion.

    Scans for tokens that look like file paths or glob patterns (contain
    a dot + extension, or glob characters).  Brace groups like
    ``{md,yaml}`` are pre-expanded before globbing since Python's
    ``glob`` module does not handle them.  Returns deduplicated matched
    paths sorted alphabetically.  Returns empty list when nothing
    matches — the feature is a no-op for tasks that don't reference
    files."""
    import glob as _glob
    candidates: list[str] = []
    for token in task.split():
        token = token.strip("\"'(),;:")
        if not token:
            continue
        has_ext = "." in token and not token.startswith(".")
        has_glob = any(c in token for c in _GLOB_CHARS)
        if has_ext or has_glob:
            candidates.append(token)
    matched: list[Path] = []
    seen: set[str] = set()
    for cand in candidates:
        for expanded in _expand_braces(cand):
            for m in sorted(_glob.glob(str(repo_root / expanded))):
                p = Path(m)
                if p.is_file() and str(p) not in seen:
                    seen.add(str(p))
                    matched.append(p)
    return matched


def extract_task_file_structure(
    task: str, repo_root: Path,
) -> list[str] | None:
    """Extract structural elements from files referenced in the task.

    Returns a list of ``"filename: heading/key"`` strings, or ``None``
    if no files matched or no structure could be extracted."""
    matched = glob_task_references(task, repo_root)
    if not matched:
        return None
    items: list[str] = []
    for f in matched:
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        ext = f.suffix.lower()
        name = f.name
        if ext in (".md", ".txt"):
            for m in re.finditer(r'^#{3,6}\s+(.+)', text, re.MULTILINE):
                items.append(f"{name}: {m.group(1).strip()}")
            for m in re.finditer(r'^\d+\.\s+(.+)', text, re.MULTILINE):
                raw = m.group(1).strip()
                if not re.match(r'^\[.+\]\(#', raw):
                    items.append(f"{name}: {raw[:80]}")
        elif ext in (".yaml", ".yml"):
            # Stdlib-only: extract list-item IDs and top-level mapping
            # keys via regex.  Full YAML parsing would require PyYAML
            # which is not a runtime dep (stdlib-preferred per CLAUDE.md).
            for m in re.finditer(r'^- id:\s*(.+)', text, re.MULTILINE):
                items.append(f"{name}: {m.group(1).strip()}")
            for m in re.finditer(
                    r'^([a-zA-Z_][\w-]*):', text, re.MULTILINE):
                items.append(f"{name}: {m.group(1)}")
    return items if items else None


_MAX_COVERAGE_ITEMS = 50


def check_task_file_coverage(
    extracted: list[str], subtasks: list[dict],
) -> list[str]:
    """Check which extracted items are NOT referenced by any subtask.

    Returns a LOW_COVERAGE issue when >50% of items are uncovered AND the
    item count is ≤ ``_MAX_COVERAGE_ITEMS``.  Above the cap the signal is
    too dilute for meaningful gating — a planner with 5–15 subtasks cannot
    realistically cover half of 200+ spec items.  The prompt injection
    (``_format_task_file_structure``) is unconditional regardless of this
    cap."""
    if not extracted:
        return []
    if len(extracted) > _MAX_COVERAGE_ITEMS:
        return []
    plan_text = " ".join(
        (s.get("intent", "") + " " +
         s.get("investigation_notes", "") + " " +
         s.get("title", ""))
        for s in subtasks
    ).lower()
    uncovered = []
    for item in extracted:
        key = item.split(": ", 1)[1] if ": " in item else item
        if key.lower() not in plan_text:
            uncovered.append(item)
    if uncovered and len(uncovered) > len(extracted) * 0.5:
        return [
            f"LOW_COVERAGE: {len(uncovered)}/{len(extracted)} items "
            f"from task-referenced files not mentioned in plan. "
            f"Sample: {uncovered[:5]}"]
    return []


def _format_task_file_structure(items: list[str]) -> str:
    """Format extracted structure as an external coverage reference
    for the planner prompt."""
    lines = "\n".join(f"- {item}" for item in items[:100])
    return (
        "TASK-REFERENCED FILE STRUCTURE (mechanically extracted by the "
        "orchestrator — not generated by an LLM):\n\n"
        f"{lines}\n\n"
        "Use this as a coverage checklist. Verify your plan addresses "
        "each item or explicitly notes why it's out of scope for your "
        "domain."
    )


# ---------------------------------------------------------------------------
# P6 repo-map — build_repo_map + rank_repo_map (DESIGN §5½ (P6))
# ---------------------------------------------------------------------------
# tree_sitter and tree_sitter_language_pack are lazy-imported inside each
# function (same pattern as tenacity inside claude_p) so that orchestrator/
# leerie.py loads on a bare host python3 that lacks requirements.txt deps.
# The config --recapture host seam exec_module()s this file on the host where
# neither tree-sitter package is guaranteed; a module-scope import would crash
# before the fast-path guards can print their diagnostic.


def _repo_map_cache_key(path: Path) -> str:
    """Return a stable cache key: '<abs_path>@<mtime_ns>'.

    The mtime_ns component means that touching a file produces a new key
    and forces a re-parse, while leaving it untouched hits the cached
    result — the Aider diskcache pattern."""
    return f"{path}@{path.stat().st_mtime_ns}"


def _walk_calls(node: "object") -> list[str]:
    """Collect identifier names from call-expression function positions.

    Walks the tree-sitter CST recursively.  For each `call` node, extracts
    the function's identifier if the callee is a bare name or the first
    component of an attribute access (e.g. ``foo(...)`` → ``"foo"``,
    ``obj.method(...)`` → ``"obj"`` is skipped, only bare-name callees become
    reference edges).  This approximates cross-file ref edges without requiring
    a scope resolver."""
    results: list[str] = []

    def _visit(n: "object") -> None:
        if n.type == "call":  # type: ignore[attr-defined]
            func = n.child_by_field_name("function")  # type: ignore[attr-defined]
            if func and func.type == "identifier":  # type: ignore[attr-defined]
                text = func.text  # type: ignore[attr-defined]
                if text:
                    results.append(
                        text.decode() if isinstance(text, bytes) else text)
        for child in n.children:  # type: ignore[attr-defined]
            _visit(child)

    _visit(node)
    return results


def _parse_repo_file(path: Path) -> tuple[list[str], list[str]]:
    """Parse one source file and return ``(defs, refs)``.

    *defs* — list of defined symbol names (functions, classes, methods).
    *refs* — list of symbol names called/referenced in the file body.

    Uses ``tree_sitter_language_pack.process()`` for definitions (structure)
    and tree-sitter's CST for call-site references.  Returns ``([], [])``
    when the language is unsupported or an error occurs (graceful degrade —
    the file is simply absent from the graph)."""
    try:
        import tree_sitter_language_pack as tslp  # noqa: PLC0415
        from tree_sitter import Parser  # noqa: PLC0415
    except ImportError:
        return [], []
    try:
        lang = tslp.detect_language(str(path))
        if not lang:
            return [], []
        source = path.read_text(errors="replace")
        proc_result = tslp.process(
            source,
            tslp.ProcessConfig(language=lang, structure=True, imports=False),
        )
        defs: list[str] = []

        def _collect_defs(items: list) -> None:
            for item in items:
                if item.name:
                    defs.append(item.name)
                _collect_defs(item.children)

        _collect_defs(proc_result.structure)
        py_lang = tslp.get_language(lang)
        parser = Parser(py_lang)
        tree = parser.parse(source.encode())
        refs = _walk_calls(tree.root_node)
        return defs, refs
    except Exception:  # graceful degrade; tree-sitter parse errors, IO, etc.
        return [], []


# Source-code extensions used only to detect the G6 "repo has code but the
# symbol graph is empty" condition (tree-sitter unavailable/incompatible).
# Not an allow-list for parsing — _parse_repo_file/detect_language own that.
_SOURCE_EXTS = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".rb", ".java", ".kt", ".c", ".h", ".cc", ".cpp",
    ".hpp", ".cs", ".php", ".swift", ".scala", ".sh", ".lua",
})

_repo_map_empty_warned = False


def _tree_sitter_extraction_works() -> bool:
    """True only if the tree-sitter stack can actually extract a symbol.
    Parses a trivial snippet through _parse_repo_file so an installed-but-
    incompatible parser (imports fine, extracts nothing) is caught. Used to
    distinguish a broken parser (warn) from a legitimately symbol-less repo
    (stay quiet) in build_repo_map's empty-graph check."""
    import tempfile  # noqa: PLC0415

    try:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "_probe.py"
            f.write_text("def _probe_sym():\n    return 1\n")
            defs, _refs = _parse_repo_file(f)
        return "_probe_sym" in defs
    except Exception:
        return False


def _warn_repo_map_empty_once(source_candidates: int) -> None:
    """Emit at most one warning per process when build_repo_map produces an
    empty graph despite the repo containing source files AND a functional
    probe confirms tree-sitter cannot extract symbols — the signal that
    tree-sitter is unavailable or its API is incompatible and P6 has silently
    become a no-op (DESIGN §12). If the probe *works* (so the empty graph is a
    legitimately symbol-less repo), no warning is emitted."""
    global _repo_map_empty_warned
    if _repo_map_empty_warned:
        return
    if _tree_sitter_extraction_works():
        return  # parser is fine; the repo just has no extractable symbols
    _repo_map_empty_warned = True
    log(
        f"WARNING: repo-map is empty despite {source_candidates} source "
        "file(s) — tree-sitter is unavailable or incompatible; P6 structural "
        "context is disabled and planning degrades to grep/glob only. Check "
        "the tree-sitter-language-pack install (see requirements.txt pin)."
    )


def build_repo_map(
    repo_root: Path,
    leerie_root: Path,
) -> dict:
    """Build (or update) the repo-map symbol/reference graph for *repo_root*.

    Walks all source files under *repo_root* (skipping ``node_modules``,
    ``.git``, ``__pycache__``, virtualenvs, and build outputs), parses each
    with tree-sitter, extracts definition and call-site symbols, and builds a
    two-sided graph:

    - ``files``   : ``{file_path: [def_symbol, ...]}``
    - ``refs``    : ``{def_symbol: {file_path, ...}}``

    Parse results are mtime-cached under
    ``<leerie_root>/repo-map-cache/<sha256(path)>.pkl`` so that only files
    whose mtime changed since the last call are re-parsed (the Aider diskcache
    pattern).  The cache directory is created on first use.

    Returns the ``RepoMap`` dict (always; never raises)."""
    import pickle  # noqa: PLC0415
    import hashlib  # noqa: PLC0415

    cache_dir = leerie_root / REPO_MAP_CACHE_DIR
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        cache_dir = None  # cache unavailable; proceed without

    _SKIP_DIRS = frozenset({
        ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
        ".tox", "dist", "build", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", "target", "vendor", ".bundle",
    })

    def _cache_path(abs_path: Path) -> Path | None:
        if cache_dir is None:
            return None
        digest = hashlib.sha256(str(abs_path).encode()).hexdigest()
        return cache_dir / f"{digest}.pkl"

    def _load_cache(abs_path: Path) -> tuple[list[str], list[str]] | None:
        cp = _cache_path(abs_path)
        if cp is None or not cp.exists():
            return None
        try:
            with open(cp, "rb") as fh:
                entry = pickle.load(fh)  # noqa: S301
            if entry.get("mtime_ns") == abs_path.stat().st_mtime_ns:
                return entry["defs"], entry["refs"]
        except Exception:
            pass
        return None

    def _save_cache(
        abs_path: Path,
        defs: list[str],
        refs: list[str],
    ) -> None:
        cp = _cache_path(abs_path)
        if cp is None:
            return
        try:
            with open(cp, "wb") as fh:
                pickle.dump({
                    "mtime_ns": abs_path.stat().st_mtime_ns,
                    "defs": defs,
                    "refs": refs,
                }, fh, protocol=4)
        except Exception:
            pass

    files_map: dict[str, list[str]] = {}
    refs_map: dict[str, set[str]] = {}
    source_candidates = 0  # files with a source-code extension we tried to parse

    for dirpath, dirnames, filenames in os.walk(repo_root):
        # Prune skip dirs in-place so os.walk doesn't descend into them
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            abs_path = Path(dirpath) / fname
            if abs_path.suffix.lower() in _SOURCE_EXTS:
                source_candidates += 1
            rel = str(abs_path.relative_to(repo_root))
            cached = _load_cache(abs_path)
            if cached is not None:
                defs, file_refs = cached
            else:
                defs, file_refs = _parse_repo_file(abs_path)
                _save_cache(abs_path, defs, file_refs)
            if not defs and not file_refs:
                continue
            files_map[rel] = defs
            for sym in file_refs:
                if sym not in refs_map:
                    refs_map[sym] = set()
                refs_map[sym].add(rel)

    # G6 — make silent P6 degradation visible (DESIGN §12 "no silent
    # under-coverage"): if the repo has source files but the graph came back
    # empty, tree-sitter is unavailable/incompatible (e.g. a language-pack
    # version without the process() API) and the whole P6 layer just became a
    # no-op the planner cannot detect. Warn ONCE per process so an operator
    # sees it instead of a silently degraded plan. A genuinely empty/non-code
    # repo (source_candidates == 0) stays quiet — that degrade is legitimate.
    if source_candidates and not files_map:
        _warn_repo_map_empty_once(source_candidates)

    return {"files": files_map, "refs": refs_map}


def _pagerank(
    graph: dict[str, set[str]],
    personalization: dict[str, float],
    damping: float = 0.85,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> dict[str, float]:
    """Personalized PageRank on a directed graph (stdlib-only, no networkx).

    *graph*           : node → set of outgoing neighbors.
    *personalization* : node → preference weight (unnormalized).

    Returns node → rank score (higher = more task-relevant)."""
    # Collect all nodes (including those only appearing as targets)
    nodes: set[str] = set(graph.keys())
    for neighbors in graph.values():
        nodes.update(neighbors)
    if not nodes:
        return {}

    n = len(nodes)
    total_pref = sum(personalization.values()) or 1.0
    pref: dict[str, float] = {
        nd: personalization.get(nd, 0.0) / total_pref for nd in nodes
    }
    rank: dict[str, float] = {nd: 1.0 / n for nd in nodes}
    dangling: set[str] = {
        nd for nd in nodes if nd not in graph or not graph[nd]
    }

    for _ in range(max_iter):
        dangling_sum = damping * sum(rank[nd] for nd in dangling) / n
        new_rank: dict[str, float] = {}
        for nd in nodes:
            in_score = sum(
                rank[src] / len(graph[src])
                for src in nodes
                if src in graph and nd in graph[src] and graph[src]
            )
            new_rank[nd] = (
                damping * in_score
                + dangling_sum
                + (1.0 - damping) * pref.get(nd, 0.0)
            )
        err = sum(abs(new_rank[nd] - rank[nd]) for nd in nodes)
        rank = new_rank
        if err < tol:
            break
    return rank


def _render_repo_map_subgraph(
    repo_map: dict,
    ranked_files: list[tuple[str, float]],
    max_files: int,
) -> str:
    """Render the top *max_files* files from *ranked_files* as a compact
    text block: one line per file listing its defined symbols.

    Symbols are shown as a comma-separated list.  The format is:

        path/to/file.py: SymA, SymB, SymC

    Files with no defs are omitted.  Top-ranked files appear first (highest
    score → most task-relevant); per the recency-bias principle, the most
    relevant entries appear at the beginning of the block."""
    files_map: dict[str, list[str]] = repo_map["files"]
    lines: list[str] = []
    for rel, _score in ranked_files[:max_files]:
        syms = files_map.get(rel)
        if not syms:
            continue
        lines.append(f"{rel}: {', '.join(syms[:30])}")
    return "\n".join(lines)


def _count_tokens_approx(text: str) -> int:
    """Approximate token count: ~4 bytes per token (GPT/Claude typical)."""
    return max(1, len(text.encode()) // 4)


def rank_repo_map(
    repo_map: dict,
    seed_files: list[str],
    seed_symbols: list[str],
    token_budget: int | None = None,
) -> str:
    """Run personalized PageRank on *repo_map* biased toward *seed_files* and
    *seed_symbols*, and return a compact ranked-subgraph text string that fits
    within *token_budget* tokens.

    The subgraph is binary-searched to fit the budget: start with all ranked
    files, halve max_files until the rendered text fits, then return the
    largest budget-fitting slice.

    When *token_budget* is ``None``, uses ``DEFAULT_CAPS["repo_map_tokens"]``.

    Returns an empty string when the repo-map is empty or no seed is given
    and the whole map is empty."""
    if token_budget is None:
        token_budget = DEFAULT_CAPS["repo_map_tokens"]

    files_map: dict[str, list[str]] = repo_map.get("files", {})
    refs_map: dict[str, set[str]] = repo_map.get("refs", {})

    if not files_map:
        return ""

    # Build a file→file edge graph via shared symbols:
    # file A → file B when a symbol defined in A is referenced in B
    # (A is a callee; B is a caller).  This direction means high-in-degree
    # nodes are widely-used utilities — biasing toward them surfaces the
    # structural backbone of the task neighborhood.
    graph: dict[str, set[str]] = {}
    def_to_file: dict[str, str] = {}
    for rel, syms in files_map.items():
        for sym in syms:
            def_to_file[sym] = rel

    for sym, referencing_files in refs_map.items():
        definer = def_to_file.get(sym)
        if definer is None:
            continue
        if definer not in graph:
            graph[definer] = set()
        for ref_file in referencing_files:
            if ref_file != definer:
                graph[definer].add(ref_file)

    for rel in files_map:
        if rel not in graph:
            graph[rel] = set()

    pref: dict[str, float] = {}
    seed_set = set(seed_files)
    for rel in files_map:
        if rel in seed_set:
            pref[rel] = pref.get(rel, 0.0) + 1.0
    for sym in seed_symbols:
        definer = def_to_file.get(sym)
        if definer and definer in files_map:
            pref[definer] = pref.get(definer, 0.0) + 1.0
        for ref_file in refs_map.get(sym, set()):
            if ref_file in files_map:
                pref[ref_file] = pref.get(ref_file, 0.0) + 0.5

    if not pref:
        pref = {rel: 1.0 for rel in files_map}

    ranks = _pagerank(graph, pref)

    ranked_files: list[tuple[str, float]] = sorted(
        ((rel, ranks.get(rel, 0.0)) for rel in files_map),
        key=lambda x: -x[1],
    )

    total = len(ranked_files)
    if total == 0:
        return ""

    lo, hi = 1, total
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = _render_repo_map_subgraph(repo_map, ranked_files, mid)
        if _count_tokens_approx(candidate) <= token_budget:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1

    return best


def validate_plan(subtasks: dict) -> None:
    """Structural validation of the merged plan — pure Python set operations.

    `requires` entries are objects `{tag, extent, reason?}` per DESIGN §5
    `requires.extent`. The JSON schema (`_REQUIRES_ITEM`) enforces the
    shape; this function enforces the conditional invariants that
    vanilla JSON Schema cannot express, and verifies the in-plan
    producer-side of cross-domain dependencies. `extent: external`
    entries are deliberately *not* checked for a provider — they are
    declared out-of-graph by the planner and surface in `plan.json`'s
    `preconditions` section."""
    errors: list[str] = []

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
            # Name the actual author. Reconciler-added subtasks carry
            # `_added_by_reconciler: true` (stamped in
            # `_apply_reconciler_output`). The phase 2½ size gate catches
            # reconciler-authored `large` upstream with a structured
            # retry; if one reaches this validator, the size-retry
            # exhausted and the message should say so. For planner-
            # authored `large`, the planner prompt already states the
            # rule (`prompts/planner.md`: "Never emit `size: large`") so
            # the message blames the planner directly.
            if s.get("_added_by_reconciler"):
                errors.append(
                    f"{sid}: size='large' — reconciler must split it "
                    "further (size-retry exhausted)")
            else:
                errors.append(
                    f"{sid}: size='large' — planner must split it further")
        if not (s.get("success_criteria_seed") or "").strip():
            errors.append(f"{sid}: success_criteria_seed is empty — "
                          "implementer has no starting point for criteria")
        for dep in s.get("depends_on", []):
            if dep not in all_ids:
                errors.append(f"{sid}: depends_on '{dep}' which does not exist "
                              "— scheduler will silently drop this edge")
        for entry in s.get("requires", []):
            # Defensive: the JSON schema rejects bare strings before this
            # function runs, but the planner output gets mutated downstream
            # (rename / promotion) so re-check shape here.
            if not isinstance(entry, dict):
                errors.append(f"{sid}: requires entry must be an object "
                              f"{{tag, extent, reason?}}, got {entry!r}")
                continue
            tag = entry.get("tag", "")
            extent = entry.get("extent", "")
            reason = (entry.get("reason") or "").strip()
            if not tag or not isinstance(tag, str):
                errors.append(f"{sid}: requires entry has empty or non-string "
                              f"tag: {entry!r}")
                continue
            if extent not in _VALID_EXTENTS:
                errors.append(f"{sid}: requires '{tag}' has unknown extent "
                              f"{extent!r} — must be one of "
                              f"{sorted(_VALID_EXTENTS)}")
                continue
            if extent == "external" and not reason:
                errors.append(f"{sid}: requires '{tag}' with extent=external "
                              "must include a non-empty `reason` naming the "
                              "owner (other repo, ops runbook, manual step) "
                              "and why no in-repo subtask could produce it")
                continue
            if extent == "in_plan" and tag not in all_provides:
                errors.append(f"{sid}: requires '{tag}' but nothing provides it — "
                              "dependency is unresolvable and will be silently dropped")
        # DESIGN §5 *Artifact passing between subtasks*: the planner
        # must not name protected meta-directories (.leerie/, .git/, or
        # top-level .claude/) in files_likely_touched. The implementer's
        # check_diff_scope would reject any commit that touched them,
        # and the worker can't even `git add` such paths (the worktree
        # gitignore excludes .leerie/). Catching this at plan-
        # validation time avoids burning an implementer invocation on
        # an impossible deliverable and gives the planner a corrective
        # retry round. For coordination artifacts (research specs,
        # design summaries) the right channel is the implementer's
        # `artifacts` result field routed via provides/depends_on.
        bad_paths = [
            f for f in (s.get("files_likely_touched") or [])
            if isinstance(f, str) and is_protected_path(f)
        ]
        if bad_paths:
            errors.append(
                f"{sid}: files_likely_touched names protected meta-"
                f"directory path(s) {bad_paths} — implementers cannot "
                "commit there (.leerie/, .git/, and top-level .claude/ "
                "are off-limits). For coordination artifacts (research "
                "specs, design summaries) use the implementer's "
                "`artifacts` result field with provides/depends_on "
                "instead of files_likely_touched (DESIGN §5 *Artifact "
                "passing between subtasks*).")

    if errors:
        bullet = "\n".join(f"  • {e}" for e in errors)
        die(f"plan validation failed ({len(errors)} issue(s)):\n{bullet}")
    log(f"plan validation: {len(subtasks)} subtasks ok")


def warn_cross_planner_file_overlap(plans: list[dict]) -> None:
    """Log a warning when subtasks from different planner outputs both list
    the same path in `files_likely_touched`. Two planners decomposing the
    same surface produces contradictory criteria the integrator can't
    reconcile — surface that risk at plan-validation time so the user can
    re-frame the task before workers start.

    Empirically (n=3 historical runs in May 2026): a successful run had 0
    cross-planner overlaps; two failed runs had 9 and 10 respectively. The
    naive cross-prefix overlap signal had zero false positives in that
    data. This is a warning, not a hard fail — same-file overlap is
    sometimes legitimate (one planner adds scaffolding the other consumes)
    and the integrator is still the backstop. The future-work item is to
    extend the reconciler to resolve overlaps automatically (DESIGN §5)."""
    file_owners: dict[str, list[tuple[str, str]]] = {}
    for plan in plans:
        domain = plan.get("domain") or "?"
        for s in plan.get("subtasks", []):
            sid = s.get("id", "?")
            for f in s.get("files_likely_touched", []):
                file_owners.setdefault(f, []).append((domain, sid))
    overlaps = {f: owners for f, owners in file_owners.items()
                if len({d for d, _ in owners}) > 1}
    if not overlaps:
        return
    log(f"⚠  cross-planner file overlap: {len(overlaps)} file(s) claimed by "
        "multiple planners. Two planners decomposing the same surface "
        "produces contradictory subtask criteria the integrator may not "
        "be able to reconcile. Review the plan before proceeding.")
    for f, owners in sorted(overlaps.items()):
        per = ", ".join(f"{d}({sid})" for d, sid in sorted(owners))
        log(f"     {f}: {per}")


_ENV_TAG_KEYWORDS = frozenset({"env", "bootstrap", "secret", "config-key",
                               "credential"})


def warn_layer_gaps(plans: list[dict]) -> None:
    """Advisory cross-domain layer-gap warnings (DESIGN §5).

    Runs on the reconciled plan before scheduling. Two heuristics:
    1. schema.prisma touched but no seed/migration files in any subtask.
    2. provides tags with env/bootstrap/secret keywords but no .env.example
       touched."""
    all_files: set[str] = set()
    all_provides: list[tuple[str, str]] = []
    for plan in plans:
        for s in plan.get("subtasks", []):
            sid = s.get("id", "?")
            all_files.update(s.get("files_likely_touched") or [])
            for tag in s.get("provides") or []:
                all_provides.append((sid, tag))

    # Heuristic 1: DB schema without seed/migration
    touches_schema = any(
        "schema.prisma" in f for f in all_files)
    touches_seed = any(
        "seed.ts" in f or "seed.js" in f or "seed.py" in f
        for f in all_files)
    touches_migration = any("migrations/" in f for f in all_files)
    if touches_schema and not (touches_seed or touches_migration):
        log("⚠  LAYER_GAP: schema.prisma modified but no subtask "
            "touches seed or migration files — database initialization "
            "may be incomplete")

    # Heuristic 2: env-contract provider without env docs
    env_provider_sids = [
        sid for sid, tag in all_provides
        if any(kw in tag.lower() for kw in _ENV_TAG_KEYWORDS)]
    touches_env_template = any(
        ".env.example" in f or ".env.local.example" in f
        or ".env.template" in f for f in all_files)
    if env_provider_sids and not touches_env_template:
        log(f"⚠  LAYER_GAP: subtasks {env_provider_sids} provide "
            "env/bootstrap/secret capabilities but no subtask updates "
            ".env.example or env documentation")


def _resolves_under(path_str: str, root: Path) -> bool:
    """True iff `path_str` (relative or absolute) resolves under `root`.
    Resolves symlinks so a planner cannot sneak a path through with a
    symlinked decoy. Returns False on any OSError or ValueError —
    treated by the caller as "not under root," which fails the check
    and triggers a drop (the safe direction)."""
    try:
        candidate = Path(path_str)
        if not candidate.is_absolute():
            candidate = root / candidate
        return candidate.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def filter_offtree_subtasks(plans: list[dict], repo_root: Path,
                            inspect_dirs: list[str], st: "State") -> None:
    """Mutate `plans` in place: drop any subtask whose `files_likely_touched`
    contains a path that does not resolve under `repo_root`. Record drops
    in `st.data["dropped_subtasks"]` and log a per-subtask warning. Soft
    drop — the run continues with the surviving subtasks, `schedule()`
    runs after this and sees a clean plan.

    Motivation: cross-repo `--inspect-dir` runs let the planner read
    files in mounts like `/inspect/api/...`, and the planner sometimes
    names those paths in `files_likely_touched` for an implementer that
    can only modify the run's primary worktree. The implementer either
    fails outright or clones the inspected repo into `/tmp` and edits
    there — those edits never reach the subtask branch, and
    `check_branch_has_commits` correctly fails the subtask.

    Why a soft drop and not `die()`: a hard fail here is unrecoverable
    via `--resume`. The resume branch in `_run_phases` does not re-run
    `phase_plan` or this filter, and `state.json["waves"]` is only
    written by `write_plan` which runs after `schedule()`. Soft drop
    matches the existing `warn_cross_planner_file_overlap` pattern at
    the same pre-schedule layer."""
    inspect_roots = [Path(d).resolve() for d in (inspect_dirs or [])]
    dropped: dict[str, dict] = {}
    for plan in plans:
        survivors = []
        for s in plan.get("subtasks", []):
            sid = s.get("id", "?")
            offtree_paths = [
                f for f in (s.get("files_likely_touched") or [])
                if not _resolves_under(f, repo_root)
            ]
            if not offtree_paths:
                survivors.append(s)
                continue
            reasons = []
            for f in offtree_paths:
                leaked = next((str(r) for r in inspect_roots
                               if _resolves_under(f, r)), None)
                if leaked:
                    reasons.append(
                        f"{f!r} resolves under inspect-dir {leaked!r} "
                        "(read-only; implementer cannot modify)")
                else:
                    reasons.append(
                        f"{f!r} does not resolve under repo root "
                        f"{str(repo_root)!r}")
            dropped[sid] = {"reasons": reasons, "files": offtree_paths}
        plan["subtasks"] = survivors
    if not dropped:
        return
    log(f"⚠  filter_offtree_subtasks: dropped {len(dropped)} subtask(s) "
        "with off-tree files_likely_touched:")
    for sid, info in sorted(dropped.items()):
        for r in info["reasons"]:
            log(f"     {sid}: {r}")
    st.data.setdefault("dropped_subtasks", {}).update(dropped)
    st.save()


async def filter_satisfied_subtasks(
    plans: list[dict], repo_root: Path, st: "State", caps: dict,
    models: dict[str, str], efforts: dict[str, str | None],
) -> dict[str, str] | None:
    """Mutate `plans` in place: drop any subtask whose success criteria
    are already met on the base tree (DESIGN §8 *Already-satisfied
    subtask elimination*). Spawns one read-only `satisfied_probe` worker
    per surviving subtask (bounded by `max_parallel`), each judging that
    subtask's `success_criteria_seed` against the current checkout, and
    soft-drops the ones the probe marks `satisfied`. Recorded in
    `st.data["dropped_subtasks"]` with `reason: "already_satisfied"` plus
    the probe's evidence — the same audit shape as
    `filter_offtree_subtasks`.

    Returns a `no_work_map` (`domain → basis`) IFF the drop empties every
    `status == "ready"` plan (so the caller routes to
    `_finish_no_work_run`, the same terminal state as the native
    cleared-but-empty case, DESIGN §8). Returns None otherwise — the run
    proceeds to `schedule()` with the surviving subtasks. A `status ==
    "blocked"` plan with zero subtasks does NOT trigger the no-work route
    (it must still fall to `schedule()`'s all-blocked `die`), mirroring
    `detect_no_work`'s ready-only guard.

    Soft drop, not `die()`: same resume-safety reasoning as
    `filter_offtree_subtasks` — the drop happens on the plan→schedule
    path that `--resume` does not re-run, and `state.json["waves"]` is
    only written by `write_plan` after `schedule()`, so a surviving-only
    plan is what gets persisted. The gate is advisory and subordinate to
    the mechanical `check_branch_has_commits` backstop (§12): a
    false-negative (probe says "still needed" when it was done) costs one
    implementer round the backstop already tolerates; the probe is tuned
    to prefer that over a false-positive that would silently delete real
    work. The probe runs with `SATISFIED_PROBE_TOOLS` (a base-tree-only
    subset of INSPECT_TOOLS — no history-spanning git, see that
    constant's rationale)."""
    if st.data.get("skip_satisfied_check"):
        log("phase 3: satisfied-check skipped (--skip-satisfied-check / "
            "LEERIE_SKIP_SATISFIED_CHECK / skip_satisfied_check=true)")
        return None

    # Flatten to (plan, subtask) pairs so the probe results can be applied
    # back to the owning plan. Only subtasks carrying a non-empty success
    # criterion are probeable — without a criterion there is nothing to
    # judge "already met" against, so such a subtask always survives.
    probeable: list[dict] = []
    total = 0
    for plan in plans:
        for s in plan.get("subtasks", []) or []:
            total += 1
            if (s.get("success_criteria_seed") or "").strip():
                probeable.append(s)
    if not probeable:
        return None

    log(f"phase 3: satisfied-probe over {len(probeable)} subtask(s) "
        f"(base-tree already-satisfied check, DESIGN §8)")
    st.data["current_phase"] = "phase 3: satisfied-probe"
    st.save()

    sys_prompt = load_prompt("satisfied_probe")
    sem = asyncio.Semaphore(caps["max_parallel"])
    # sid → drop record; only satisfied subtasks land here.
    dropped: dict[str, dict] = {}

    async def probe_one(s: dict) -> None:
        sid = s.get("id", "?")
        payload = {
            "id": sid,
            "title": s.get("title", ""),
            "intent": s.get("intent", ""),
            "success_criteria_seed": s.get("success_criteria_seed", ""),
            "files_likely_touched": list(
                s.get("files_likely_touched", []) or []),
        }
        user_prompt = (
            "SUBTASK:\n" + json.dumps(payload, indent=2) +
            "\n\nReturn only the JSON object per your schema. Judge the "
            "CURRENT working tree / HEAD only — never other branches or "
            "history. Default satisfied=false on any uncertainty."
        )
        async with sem:
            # bump_workers is OUTSIDE the try: its WorkerError signals
            # budget exhaustion (worker_count > max_total_workers), which
            # is the hard backstop — it must propagate so gather_or_cancel
            # aborts the run, not be swallowed into a silent subtask-keep.
            # Only a claude_p failure is caught below as the fail-safe.
            st.bump_workers(caps)
            try:
                out = await claude_p(
                    user_prompt=user_prompt, system_prompt=sys_prompt,
                    schema_key="satisfied_probe", cwd=str(repo_root),
                    allowed_tools=SATISFIED_PROBE_TOOLS, max_turns=20,
                    autonomous=False, caps=caps, st=st,
                    model=models["satisfied_probe"],
                    effort=efforts["satisfied_probe"],
                    sid=f"satisfied_probe-{sid}",
                )
            except WorkerError as e:
                # A probe crash (e.g. claude_p schema failure twice) must
                # NOT drop the subtask — fail safe toward keeping the work.
                # Log and let the subtask survive.
                log(f"  satisfied-probe {sid}: crashed ({e}); keeping "
                    "subtask (fail-safe — no drop on probe failure)")
                return
        if out.get("satisfied") is True:
            dropped[sid] = {
                "reason": "already_satisfied",
                "evidence": out.get("evidence", ""),
                "checked": list(out.get("checked", []) or []),
            }

    await gather_or_cancel(*(probe_one(s) for s in probeable))

    if not dropped:
        return None

    # Apply the drops: rewrite each plan's subtasks to survivors only.
    for plan in plans:
        plan["subtasks"] = [
            s for s in (plan.get("subtasks", []) or [])
            if s.get("id") not in dropped
        ]

    log(f"phase 3: satisfied-probe dropped {len(dropped)}/{total} "
        "already-satisfied subtask(s):")
    for sid, info in sorted(dropped.items()):
        log(f"     {sid}: {info['evidence'][:160]}")
    st.data.setdefault("dropped_subtasks", {}).update(dropped)
    st.save()

    # If every ready plan is now empty, this is the per-subtask analogue
    # of the cleared-but-empty terminal state. Build a no_work_map from
    # the drop evidence (NOT from plan confidence.basis, which is the
    # planner's original "I found work" rationale — misleading here) and
    # signal the caller to route to _finish_no_work_run. A blocked plan
    # with 0 subtasks must still fall through to schedule()'s all-blocked
    # die, so guard on ready-only exactly like detect_no_work.
    for plan in plans:
        if plan.get("status") != "ready":
            return None
        if plan.get("subtasks"):
            return None
    no_work_map: dict[str, str] = {}
    for plan in plans:
        domain = plan.get("domain") or "<unknown>"
        no_work_map[domain] = (
            "all subtasks already satisfied on HEAD "
            "(satisfied-probe, DESIGN §8)")
    return no_work_map


# --- per-repo dependency provisioning ----------------------------------------
# See DESIGN.md §6½ "Per-repo dependency provisioning" and IMPLEMENTATION.md
# §6½ for the layered design (.leerie-setup.sh → mise install → table → LLM
# fallback → worktree replay).

# argv[0] allowlist for any provision command — both table-emitted commands
# and the LLM-fallback recipe. Validated by validate_provision_recipe().
# Anything outside this set is rejected; the §12 carve-out (the LLM
# fallback worker) is mechanically contained by this list.
_PROVISION_ARGV0_ALLOW = frozenset({
    "pnpm", "npm", "yarn", "pip", "pip3", "uv", "poetry", "pipenv",
    "go", "cargo", "bundle", "gem", "mvn", "gradle", "gradlew", "make",
    "composer", "dotnet",
})

# Shell metacharacters that must not appear anywhere in a command argv.
# A command emitted as a true argv list cannot legitimately need any of
# these — the executor invokes it directly with no shell, and any
# metacharacter is a sign the recipe was malformed or smuggling shell
# semantics through the validator.
_PROVISION_SHELL_METACHARS = frozenset(set("|&;$`><\n\r"))


def _lockfile_table_entries(repo_root: Path) -> list[dict]:
    """The deterministic lockfile → install-command table. Returns a list of
    recipe entries (possibly empty) — polyglot repos like Rails-with-frontend
    emit ALL matching commands, not first-match-wins. See IMPLEMENTATION.md
    §6½ for the full table.

    Each entry is the minimal recipe shape: {kind, command, working_dir,
    timeout_s}. Callers compose them into a full recipe and persist to
    st.data["provision"]["recipe"].
    """
    entries: list[dict] = []

    # --- Node.js: pnpm > yarn > npm precedence ---
    # The precedence is documented at the pnpm and yarn sites: a repo that
    # commits multiple lockfiles is rare, but when it happens the most-
    # specific one wins. pnpm-lock.yaml means the team has chosen pnpm even
    # if package-lock.json was left behind from a prior tool.
    has_pnpm = (repo_root / "pnpm-lock.yaml").is_file()
    has_yarn = (repo_root / "yarn.lock").is_file()
    has_npm = (repo_root / "package-lock.json").is_file()
    if has_pnpm:
        entries.append({
            "kind": "install",
            "command": ["pnpm", "install", "--frozen-lockfile"],
            "working_dir": ".",
            "timeout_s": 1800,
        })
    elif has_yarn:
        entries.append({
            "kind": "install",
            "command": ["yarn", "install", "--frozen-lockfile"],
            "working_dir": ".",
            "timeout_s": 1800,
        })
    elif has_npm:
        entries.append({
            "kind": "install",
            "command": ["npm", "ci"],
            "working_dir": ".",
            "timeout_s": 1800,
        })

    # --- Python: uv > poetry > pipenv. Bare requirements.txt and bare
    # pyproject.toml (without a lockfile) deliberately do NOT match — they
    # are the ambiguous tail that goes to the LLM fallback (verified
    # against Django, which uses `pip install -e .`).
    if (repo_root / "uv.lock").is_file():
        entries.append({
            "kind": "install",
            "command": ["uv", "sync"],
            "working_dir": ".",
            "timeout_s": 1800,
        })
    elif (repo_root / "poetry.lock").is_file():
        entries.append({
            "kind": "install",
            "command": ["poetry", "install"],
            "working_dir": ".",
            "timeout_s": 1800,
        })
    elif (repo_root / "Pipfile.lock").is_file():
        entries.append({
            "kind": "install",
            "command": ["pipenv", "install"],
            "working_dir": ".",
            "timeout_s": 1800,
        })

    # --- Go ---
    if (repo_root / "go.mod").is_file() and (repo_root / "go.sum").is_file():
        entries.append({
            "kind": "install",
            "command": ["go", "mod", "download"],
            "working_dir": ".",
            "timeout_s": 1800,
        })

    # --- Rust ---
    if (repo_root / "Cargo.lock").is_file():
        entries.append({
            "kind": "install",
            "command": ["cargo", "fetch"],
            "working_dir": ".",
            "timeout_s": 1800,
        })

    # --- Ruby ---
    if (repo_root / "Gemfile.lock").is_file():
        entries.append({
            "kind": "install",
            "command": ["bundle", "install"],
            "working_dir": ".",
            "timeout_s": 1800,
        })

    # --- PHP ---
    if (repo_root / "composer.lock").is_file():
        entries.append({
            "kind": "install",
            "command": ["composer", "install", "--no-interaction"],
            "working_dir": ".",
            "timeout_s": 1800,
        })

    # --- C# / .NET ---
    if (repo_root / "packages.lock.json").is_file():
        entries.append({
            "kind": "install",
            "command": ["dotnet", "restore"],
            "working_dir": ".",
            "timeout_s": 1800,
        })

    return entries


def detect_recipe_from_lockfiles(repo_root: Path) -> list[dict]:
    """Public entry point for the deterministic detection layer. Returns
    a list of recipe entries (possibly empty). An empty list means the
    table abstained and the caller should fall back to the LLM worker.
    """
    return _lockfile_table_entries(repo_root)


def _normalize_pip_installs(recipe: list[dict]) -> list[dict]:
    """Add `--break-system-packages` to every `pip`/`pip3`/`python -m pip`
    *install* command that lacks it. Returns a new recipe list (entries
    are shallow-copied only when rewritten).

    Why this is code, not a prompt rule: the container's system Python is
    Debian-13 externally-managed (PEP 668) — a bare `pip install` exits
    non-zero with "externally-managed-environment" (the same failure the
    Dockerfile already works around at its own `pip3 install
    --break-system-packages`). The LLM provision worker generates recipes
    by mirroring the repo's CI, which runs in a venv/runner and needs no
    such flag, so it never emits one. That gap silently breaks every
    recipe consumer — most visibly `capture_conformance_baseline`, whose
    `pip install` then fails and leaves the base-tree test axis recording
    `command not found` instead of a real pass/fail. Normalizing here, at
    the single point every consumer reads the recipe as data, fixes all
    of them at once (§12: prompts advisory, code enforces).

    The flag is safe unconditionally: on a non-externally-managed
    interpreter (e.g. a mise-managed Python) it is a harmless no-op; on
    the apt system Python it is required.
    """
    out: list[dict] = []
    for entry in recipe:
        cmd = entry.get("command") or []
        if _is_pip_install(cmd) and "--break-system-packages" not in cmd:
            i = cmd.index("install")
            new_cmd = cmd[:i + 1] + ["--break-system-packages"] + cmd[i + 1:]
            out.append({**entry, "command": new_cmd})
        else:
            out.append(entry)
    return out


def _is_pip_install(cmd: list[str]) -> bool:
    """True iff `cmd` (an argv list) is a pip *install* invocation — bare
    `pip`/`pip3 …` or `python[3] -m pip …` — where the pip subcommand is
    `install`. The subcommand is the first token after the pip prefix that
    is not a global option (`pip -v install …` → install), so a leading
    global flag doesn't hide it. A non-install subcommand (`pip list`)
    returns False."""
    if not cmd:
        return False
    if cmd[0] in ("pip", "pip3"):
        rest = cmd[1:]
    elif cmd[0] in ("python", "python3") and cmd[1:3] == ["-m", "pip"]:
        rest = cmd[3:]
    else:
        return False
    for tok in rest:
        if tok.startswith("-"):
            continue  # global option before the subcommand
        return tok == "install"  # first non-option token is the subcommand
    return False


def validate_provision_recipe(recipe: list[dict]) -> None:
    """Mechanically bound the provision recipe. Raises ValueError on any
    violation. Called for BOTH the table-emitted recipe and the LLM-
    fallback recipe — the §12 carve-out for the LLM worker is contained
    here, not in the prompt.

    Invariants enforced:
      - command is a non-empty argv list (no shell strings).
      - command[0] is in _PROVISION_ARGV0_ALLOW (or the entry is kind: none).
      - No shell metacharacters anywhere in the argv (no piping, no
        redirection, no command substitution).
      - No `sudo` anywhere.
      - working_dir is "." or a relative path with no ".." segments and
        no leading "/" (worker cannot reach outside the repo).
      - kind is one of {install, build, none}.
    """
    if not isinstance(recipe, list):
        raise ValueError(f"recipe must be a list, got {type(recipe).__name__}")
    for i, entry in enumerate(recipe):
        if not isinstance(entry, dict):
            raise ValueError(f"recipe[{i}] is not a dict: {entry!r}")
        kind = entry.get("kind")
        if kind not in ("install", "build", "none"):
            raise ValueError(
                f"recipe[{i}].kind={kind!r} must be one of install|build|none")
        if kind == "none":
            # `none` entries are bypass markers; no command required.
            continue
        cmd = entry.get("command")
        if not isinstance(cmd, list) or not cmd:
            raise ValueError(
                f"recipe[{i}].command must be a non-empty argv list")
        if any(not isinstance(a, str) for a in cmd):
            raise ValueError(
                f"recipe[{i}].command must be a list of strings")
        if cmd[0] not in _PROVISION_ARGV0_ALLOW:
            raise ValueError(
                f"recipe[{i}].command[0]={cmd[0]!r} is not in the allowed "
                f"package-manager set {sorted(_PROVISION_ARGV0_ALLOW)}")
        for j, arg in enumerate(cmd):
            if arg == "sudo":
                raise ValueError(
                    f"recipe[{i}].command contains 'sudo' at position {j}")
            bad = _PROVISION_SHELL_METACHARS & set(arg)
            if bad:
                raise ValueError(
                    f"recipe[{i}].command[{j}]={arg!r} contains shell "
                    f"metacharacters {sorted(bad)}")
        wd = entry.get("working_dir")
        if not isinstance(wd, str) or not wd:
            raise ValueError(
                f"recipe[{i}].working_dir must be a non-empty string")
        if wd.startswith("/"):
            raise ValueError(
                f"recipe[{i}].working_dir={wd!r} must be relative, not absolute")
        # ".." anywhere in the path — split on both `/` and `\` so a
        # Windows-style smuggling attempt is also caught.
        parts = wd.replace("\\", "/").split("/")
        if ".." in parts:
            raise ValueError(
                f"recipe[{i}].working_dir={wd!r} contains '..' "
                "(must not traverse outside the repo)")


# Section-header regex for the README extractor. Matches install/setup-
# adjacent words. Verified against 15 real OSS READMEs (DESIGN §6½ + the
# verification corpus in tests/test_readme_extractor.py): catches 13/15.
# The two known misses (Supabase, esbuild) are marketing-style READMEs
# that delegate install to external docs — those repos route through
# .leerie-setup.sh.
_README_SECTION_RE = re.compile(
    r"(?i)\b("
    r"install"
    r"|getting[\s-]?started"
    r"|quick[\s-]?start"
    r"|setup"
    r"|usage"
    r"|\brun\b"
    r"|develop"
    r"|build(ing)?( from source| instructions)?"
    r"|compil(e|ing)( from source)?"
    r"|download"
    r"|from source"
    r"|requirements"
    r"|prerequisites"
    r"|dependenc(y|ies)"
    r")\b"
)

# Strip leading markdown-decoration glyphs (emoji, bullets, punctuation)
# from a header line before keyword matching. Handles `## 🚀 Getting
# Started` and `## • Install` without losing the keyword. The character
# class is intentionally permissive — emoji span several Unicode blocks,
# so we whitelist ASCII word characters / spaces instead and strip
# everything else from the left.
_HEADER_DECOR_RE = re.compile(r"^[^\w]+", flags=re.UNICODE)

# Code-fence content heuristics for the fallback layer. Used when no
# header matches: keep code fences that contain recognizable install
# commands so the LLM still sees the project's documented invocation.
_INSTALL_CMD_HINT_RE = re.compile(
    r"\b(pip|pip3|npm|pnpm|yarn|uv|poetry|cargo|brew|apt|apt-get|dnf|"
    r"yum|pacman|go install|make|bundle install|gem install|mise install)\b"
)

# Byte budget for the dep_capture command extraction. Extracted Bash
# commands from logs/*.log (deduped, newest-first) are admitted until this
# ceiling; any truncation is noted in the worker prompt. Sized to admit
# the full command set for essentially every run (~50–80k tokens undeduped,
# ~66k worst-case) while bounding the LLM input. Mirrors the
# _FIXTURE_TOTAL_BUDGET idiom in gather_provision_fixtures.
_DEPCAP_TOTAL_BUDGET = 307200  # 300KB ≈ 75k tokens at 4 bytes/token

# Manifests-first dep_capture (DESIGN §6½). The worker's PRIMARY corpus is the
# repo's dependency-manifest files; the install-filtered command list is only a
# SECONDARY hint for system/native (apt) deps that appear in no manifest. This
# replaced an earlier design that fed the worker the *complete* command corpus —
# overwhelmingly noise (git/grep/pytest/`python3 -c`), which let the model
# degenerate into emitting prose as package names.

# Dependency manifests to gather, primary corpus. Bounded per file and in total
# so a giant lockfile can't blow the budget. Order is display order.
_DEP_MANIFEST_NAMES = (
    "requirements.txt", "requirements-dev.txt", "requirements-test.txt",
    "pyproject.toml", "Pipfile", "Pipfile.lock", "setup.py", "setup.cfg",
    "package.json", "pnpm-lock.yaml", "package-lock.json", "yarn.lock",
    "go.mod", "Cargo.toml", "Cargo.lock", "Gemfile", "Gemfile.lock",
    "composer.json", "composer.lock",
)
_DEPCAP_MANIFEST_FILE_BUDGET = 16384    # 16 KB per manifest file
_DEPCAP_MANIFEST_TOTAL_BUDGET = 131072  # 128 KB across all manifests

# Install-verb matcher for the SECONDARY command hint. A command is kept only
# when it invokes a package-manager install verb at a command boundary (start of
# string, or after ; & | ( or `sudo`). Commands whose leading word is a
# text-scanning tool (grep/git/…) are dropped even if the verb appears inside a
# quoted pattern — that is how prose ("grep 'apt-get install intents'") leaked in.
_DEPCAP_INSTALL_RE = re.compile(
    r"(?:^|[\s;&|(])(?:sudo\s+)?(?:"
    r"apt-get\s+install|apt\s+install|yum\s+install|dnf\s+install|apk\s+add|"
    r"pip3?\s+install|pipx\s+install|poetry\s+add|uv\s+(?:add|pip\s+install)|"
    r"npm\s+(?:install|i|ci|add)|pnpm\s+(?:add|install|i)|yarn\s+(?:add|install)|"
    r"cargo\s+(?:install|add)|go\s+(?:install|get)|"
    r"gem\s+install|bundle\s+(?:add|install)|composer\s+require|dotnet\s+add"
    r")(?:\s|$)"
)
# Leading command words that scan text: their args routinely contain install
# verbs inside quoted patterns, so drop them from the hint corpus.
_DEPCAP_TEXT_TOOLS = frozenset(
    {"grep", "rg", "git", "sed", "awk", "echo", "cat", "printf", "ag", "ack"}
)
# Shell separators between commands. A single logged Bash call is often several
# commands (newlines from a `python3 -c` heredoc, `&&`/`|` chains). Splitting on
# these lets the text-tool gate apply per-command, so `echo hi\npip install x`
# keeps the install segment instead of being dropped by the leading `echo`.
_DEPCAP_SEGMENT_RE = re.compile(r"[\n;]|&&|\|\||[|&]")


def _is_install_command(cmd: str) -> bool:
    """True if `cmd` invokes a package-manager install verb (SECONDARY hint).

    Evaluates each shell segment independently: keep the command if ANY segment
    whose leading word is NOT a text-scanning tool (grep/git/…) matches an
    install verb. Per-segment evaluation matters because a single logged Bash
    call may chain commands (`echo hi; pip install x`) — the whole-string first
    word would otherwise let a leading `echo`/`grep` drop a genuine install on a
    later segment. The text-tool gate still suppresses the leak that motivated it
    (`grep "apt-get install …"`), because there the verb lives *inside* the text
    tool's own segment."""
    for seg in _DEPCAP_SEGMENT_RE.split(cmd):
        words = seg.split()
        if not words:
            continue
        first = words[0]
        # Strip a leading sudo so `sudo grep …` is still treated as a grep.
        if first == "sudo":
            first = words[1] if len(words) > 1 else ""
        if first in _DEPCAP_TEXT_TOOLS:
            continue
        if _DEPCAP_INSTALL_RE.search(seg):
            return True
    return False


def _gather_dep_manifests(repo_root: Path) -> str:
    """Read the repo's dependency-manifest files (PRIMARY dep_capture corpus).

    Returns a labeled text block (one section per manifest found), bounded per
    file and in total. Absent manifests are skipped. This is deterministic
    corpus selection (DESIGN §6½ / §12): the model decides content, code decides
    what it is shown."""
    blocks: list[str] = []
    total = 0
    for name in _DEP_MANIFEST_NAMES:
        p = repo_root / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        if len(text.encode()) > _DEPCAP_MANIFEST_FILE_BUDGET:
            text = text.encode()[:_DEPCAP_MANIFEST_FILE_BUDGET].decode(
                errors="ignore") + "\n… (truncated)"
        block = f"### {name}\n```\n{text}\n```"
        encoded = len(block.encode())
        if total + encoded > _DEPCAP_MANIFEST_TOTAL_BUDGET:
            break
        blocks.append(block)
        total += encoded
    return "\n\n".join(blocks)


def _normalize_setup_packages(pkgs: list[str]) -> str:
    """Render a package list in the canonical persisted form: order-preserving
    dedup, space-joined. Shared by _merge_setup_packages (union) and the
    replace path so both emit byte-identical TOML values."""
    return " ".join(dict.fromkeys(p for p in pkgs if p))


def _merge_setup_packages(existing: str, captured: list[str]) -> str | None:
    """Union existing setup_packages with newly-captured packages.

    Returns the merged string only when the set grew (new packages
    discovered); returns None when captured is a subset of existing.
    Preserves user-narrowed lists — never removes packages.
    """
    def _parse(s: str) -> list[str]:
        return [p for p in re.split(r"[,\s]+", s.strip()) if p]

    existing_list = _parse(existing) if existing else []
    existing_set = dict.fromkeys(existing_list)  # preserves first-seen order
    new_pkgs = [p for p in captured if p not in existing_set]
    if not new_pkgs:
        return None
    merged = list(existing_set) + new_pkgs
    # Deduplicate while preserving order (existing_set already dedups existing).
    return _normalize_setup_packages(merged)


def _dump_language_installs(entries: list[dict]) -> str:
    """JSON-encode `language_installs` for TOML persistence, single-quote-safe.

    The value is stored as a JSON string inside `.leerie/config.toml`.
    `_toml_value` wraps a `"`-containing value in a TOML *literal* (`'...'`)
    string, which cannot itself contain a `'`. A captured install command may
    carry single quotes (e.g. `pip install 'requests[security]'`,
    `gem install -v '~> 7.0'`), which would otherwise break the literal wrapper
    and yield invalid TOML. Escaping them as the JSON escape `\\u0027` keeps the
    value free of literal `'` while `json.loads` (both readers) recovers the
    original quote — so the round-trip is lossless and the file is valid TOML."""
    return json.dumps(entries, separators=(",", ":")).replace("'", "\\u0027")


def _toml_value(val: str) -> str:
    """Render `val` as a TOML string literal.

    A value containing `"` — notably the JSON-encoded `language_installs`
    (`[{"manager":"pip",...}]`) — would terminate a basic (`"..."`) string
    early and produce invalid TOML. TOML *literal* strings (`'...'`) process no
    escapes, so wrap such values in single quotes. This requires the value to
    contain no literal `'` — guaranteed for `language_installs` by
    `_dump_language_installs` (which escapes `'`→`\\u0027`) and trivially true
    for `setup_packages` (apt names have no quotes). Both readers (`_read_toml_key`
    and the launcher's flat-TOML regex) already `.strip("'")`, so this
    round-trips cleanly with no unescaping. Plain values keep the `"..."` form."""
    if '"' in val and "'" not in val:
        return f"'{val}'"
    return f'"{val}"'


def _write_config_toml_keys(cfg_path: Path, updates: dict[str, str]) -> None:
    """Minimal deterministic TOML upsert for a set of key-value pairs.

    Creates cfg_path with a standard header if absent. For each key in
    updates: replaces the first uncommented `key = ...` line if present,
    otherwise appends. Never touches commented lines. Uses os.replace
    atomicity (mirrors State.save discipline).
    """
    if cfg_path.exists():
        lines = cfg_path.read_text().splitlines(keepends=True)
    else:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# leerie per-repo configuration — commit this file to version-control.\n",
            "# Generated by: leerie capture\n",
            "# See: https://leerie.enric.ai/docs/config\n",
        ]
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            out.append(line)
            continue
        matched_key = None
        for k in list(remaining):
            if re.match(rf"^\s*{re.escape(k)}\s*=", line):
                matched_key = k
                break
        if matched_key is not None:
            val = remaining.pop(matched_key)
            out.append(f'{matched_key} = {_toml_value(val)}\n')
        else:
            out.append(line)
    # Append any keys that were not found in the file.
    for k, v in remaining.items():
        out.append(f'{k} = {_toml_value(v)}\n')
    tmp = Path(str(cfg_path) + f".tmp.{os.getpid()}")
    tmp.write_text("".join(out))
    os.replace(tmp, cfg_path)


def _extract_depcap_commands(log_dir: Path) -> tuple[str, bool]:
    """Install-shaped Bash commands from worker logs (SECONDARY dep_capture hint).

    Dedup, newest-first, bounded to _DEPCAP_TOTAL_BUDGET bytes. Only commands that
    invoke a package-manager install verb are kept (`_is_install_command`); the
    other ~96% (git/grep/pytest/`python3 -c`) is noise that let the worker
    degenerate into echoing prose as packages. This is the SECONDARY hint —
    manifests (`_gather_dep_manifests`) are the primary corpus (DESIGN §6½)."""
    seen: dict[str, None] = {}  # ordered set, dedup
    for log_path in sorted(log_dir.glob("*.log"), reverse=True):
        for kind, inp, _result in _iter_log_tool_use(log_path):
            if kind != "Bash":
                continue
            cmd = inp.get("command", "")
            if cmd and _is_install_command(cmd):
                seen[cmd] = None
    total_bytes = 0
    hit_ceiling = False
    lines: list[str] = []
    for cmd in seen:
        encoded = (cmd + "\n---\n").encode()
        if total_bytes + len(encoded) > _DEPCAP_TOTAL_BUDGET:
            hit_ceiling = True
            break
        lines.append(cmd)
        total_bytes += len(encoded)
    return "\n---\n".join(lines), hit_ceiling


def resolve_capture_deps(repo_root: Path) -> bool:
    """Resolve the capture_deps preference (DESIGN §6½).

    Order: LEERIE_CAPTURE_DEPS env → capture_deps in .leerie/config.toml → True.
    Default is True (capture enabled); set to 0/false to opt out.
    """
    env = os.environ.get(CAPTURE_DEPS_ENV, "").strip()
    if env:
        try:
            parsed = _parse_bool_envtoml(env)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed
    cfg = repo_root / CAPTURE_DEPS_CONFIG
    val = _read_toml_key(cfg, "capture_deps")
    if val is not None:
        try:
            parsed = _parse_bool_envtoml(val)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed
    return True  # default: enabled


async def capture_repo_deps(
        repo_root: Path,
        st: object,
        caps: dict | None = None,
        models: dict[str, str] | None = None,
        efforts: dict[str, str | None] | None = None,
        replace: bool = False,
) -> None:
    """Invoke the dep_capture LLM worker and write deps to .leerie/config.toml (non-fatal).

    replace=False (default, every automatic seam): union / never-clobber —
    only new packages/managers are added, existing entries are preserved.
    replace=True (only the operator-driven `--recapture --force` path):
    wholesale-replace the persisted setup_packages + language_installs from the
    fresh capture. A capture that yields nothing under replace leaves the
    existing values untouched (never blanks a good config with an empty run).
    """
    if not resolve_capture_deps(repo_root):
        return
    if caps is None or models is None or efforts is None:
        log("capture: skipped dep_capture worker (caps/models/efforts not "
            "available at finalize time)")
        return
    log_dir = getattr(st, "run_dir", None)
    if log_dir is None:
        return
    log_dir = Path(log_dir) / "logs"
    if not log_dir.is_dir():
        return
    # Skip when a committed .leerie/Dockerfile exists; it is authoritative
    # (DESIGN §6½) and setup_packages / language_installs are ignored.
    dockerfile = repo_root / ".leerie" / "Dockerfile"
    if dockerfile.is_file():
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_root), "ls-files", "--error-unmatch",
                 ".leerie/Dockerfile"],
                capture_output=True)
            if result.returncode == 0:
                log("capture: .leerie/Dockerfile is committed — skipping "
                    "dep_capture (Dockerfile is authoritative)")
                return
        except Exception:
            pass
    # Manifests-first corpus (DESIGN §6½): manifest files are primary ground
    # truth; install-filtered commands are only a hint for system/native deps.
    manifests_text = _gather_dep_manifests(repo_root)
    commands_text, hit_ceiling = _extract_depcap_commands(log_dir)
    if not manifests_text.strip() and not commands_text.strip():
        log("capture: no dependency manifests and no install commands found — "
            "skipping dep_capture")
        return
    wc = st.data.get("worker_count", 0) if hasattr(st, "data") else 0
    if wc >= caps["max_total_workers"]:
        log(f"capture: skipped dep_capture worker (worker budget exhausted at "
            f"{wc}/{caps['max_total_workers']})")
        return
    sys_prompt = load_prompt("dep_capture")
    model = models.get("dep_capture", MODEL_DEFAULT)
    effort = efforts.get("dep_capture")
    truncation_note = (
        "\n\n[NOTE: command list was truncated at the byte budget ceiling — "
        "the most recent commands are shown first]"
        if hit_ceiling else ""
    )
    manifests_section = (
        manifests_text if manifests_text.strip()
        else "(No dependency manifest files found in the repo.)"
    )
    commands_section = (
        f"{commands_text}{truncation_note}" if commands_text.strip()
        else "(No package-manager install commands observed during the run.)"
    )
    user_prompt = (
        "## Dependency manifest files found in the repo (PRIMARY):\n\n"
        f"{manifests_section}\n\n"
        "## Install commands observed during the run "
        "(SECONDARY hint, filtered):\n\n"
        f"{commands_section}"
    )
    if hasattr(st, "bump_workers"):
        st.bump_workers(caps)
    result = await claude_p(
        user_prompt=user_prompt,
        system_prompt=sys_prompt,
        schema_key="dep_capture",
        cwd=str(repo_root),
        allowed_tools=INSPECT_TOOLS,
        max_turns=10,
        autonomous=False,
        caps=caps,
        st=st,
        model=model,
        effort=effort,
        sid="dep-capture",
    )
    setup_packages: list[str] = result.get("setup_packages") or []
    language_installs: list[dict] = result.get("language_installs") or []
    cfg_path = repo_root / ".leerie" / "config.toml"
    updates: dict[str, str] = {}
    if setup_packages:
        if replace:
            # Wholesale replace: drop packages no longer captured. Gate on the
            # *rendered* value, not list truthiness — a schema-valid empty-item
            # capture ([""]) is a truthy list but renders to "", which would
            # blank a good config. Only write a non-empty value.
            norm = _normalize_setup_packages(setup_packages)
            if norm:
                updates["setup_packages"] = norm
        else:
            existing = _read_toml_key(cfg_path, "setup_packages") or ""
            merged = _merge_setup_packages(existing, setup_packages)
            if merged is not None:
                updates["setup_packages"] = merged
    if language_installs:
        # JSON-in-TOML because TOML has no inline array type compatible with
        # the flat _read_toml_key/_write_config_toml_keys surface.
        if replace:
            # Wholesale replace: the fresh capture is authoritative — drop
            # managers no longer present. Filter empty-manager junk (mirrors the
            # union path below) and gate on the filtered list being non-empty so
            # an empty-item capture never blanks a good config.
            kept = [e for e in language_installs if e.get("manager")]
            if kept:
                updates["language_installs"] = _dump_language_installs(kept)
        else:
            existing_raw = _read_toml_key(cfg_path, "language_installs") or ""
            try:
                existing_list: list[dict] = json.loads(existing_raw) if existing_raw else []
            except (json.JSONDecodeError, ValueError):
                existing_list = []
            existing_managers = {e.get("manager") for e in existing_list if e.get("manager")}
            new_entries = [e for e in language_installs
                           if e.get("manager") and e["manager"] not in existing_managers]
            if new_entries:
                merged_list = existing_list + new_entries
                updates["language_installs"] = _dump_language_installs(merged_list)
    if updates:
        _write_config_toml_keys(cfg_path, updates)
        pkg_note = f" setup_packages={updates['setup_packages']!r}" if "setup_packages" in updates else ""
        li_note = f" language_installs({len(language_installs)} entries)" if "language_installs" in updates else ""
        log(f"capture: dep_capture wrote{pkg_note}{li_note} to {cfg_path}; "
            "run `git add .leerie/ && git commit` to bake next run")
    elif replace:
        # Under --force, an empty capture must not blank a good config.
        log("capture: dep_capture captured no deps — leaving existing config unchanged")
    else:
        log("capture: dep_capture found no new deps to write")
    # Write idempotency sentinel. Both a state field (for in-run readers)
    # and a lightweight file (for the next-run backstop, which reads past
    # run dirs without constructing a full State).
    run_dir_path = getattr(st, "run_dir", None)
    if run_dir_path is not None:
        try:
            (Path(run_dir_path) / "dep_capture.done").write_text("1\n")
        except Exception:
            pass
    if hasattr(st, "data"):
        try:
            st.data["dep_capture_done"] = True
        except Exception:
            pass


async def _backstop_capture_prior_runs(
        leerie_root: Path,
        repo_root: Path,
        caps: dict,
        models: dict[str, str],
        efforts: dict[str, str | None],
) -> None:
    """Cover SIGKILL/crash: run dep_capture over prior runs with logs/ but no sentinel."""
    runs_dir = leerie_root / "runs"
    if not runs_dir.is_dir():
        return
    for run_dir in sorted(runs_dir.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        log_dir = run_dir / "logs"
        if not log_dir.is_dir():
            continue
        sentinel = run_dir / "dep_capture.done"
        if sentinel.is_file():
            continue
        log(f"backstop: running dep_capture for prior run {run_dir.name}")
        try:

            class _BackstopState:
                pass

            bst = _BackstopState()
            bst.run_dir = run_dir  # type: ignore[attr-defined]
            bst.data = {}  # type: ignore[attr-defined]
            bst.bump_workers = lambda _caps: None  # type: ignore[attr-defined]
            await capture_repo_deps(
                repo_root, bst,
                caps=caps, models=models, efforts=efforts,
            )
        except Exception as exc:
            log(f"backstop: non-fatal error capturing {run_dir.name}: {exc}")


def run_recapture_deps(
        leerie_root: Path,
        repo_root: Path,
        force: bool = False,
        run_id: str | None = None,
) -> None:
    """Host-side recapture entrypoint (DESIGN §6½) — consolidates dep_capture across runs."""
    caps = dict(DEFAULT_CAPS)

    # Minimal args namespace so resolve_models/efforts can read env vars and
    # TOML but have no CLI overrides (this is a host-side non-interactive call).
    class _MinimalArgs:
        model = None
        pr_writer_model = None
        effort = None

    _args = _MinimalArgs()
    models = resolve_models(repo_root, _args)
    efforts = resolve_efforts(repo_root, _args)

    if run_id is not None:
        target_run_dir = leerie_root / "runs" / run_id
        if not target_run_dir.is_dir():
            die(f"recapture: run {run_id!r} not found under {leerie_root / 'runs'}")
        target_dirs: list[Path] = [target_run_dir]
    else:
        runs_dir = leerie_root / "runs"
        if not runs_dir.is_dir():
            die(f"recapture: no runs directory at {runs_dir}; nothing to recapture")
        finished: list[Path] = []
        for d in runs_dir.iterdir():
            if not d.is_dir():
                continue
            rj = d / "run.json"
            if not rj.is_file():
                continue
            try:
                rdata = json.loads(rj.read_text())
            except (OSError, ValueError):
                continue
            if rdata.get("finished_at") and (d / "logs").is_dir():
                finished.append(d)
        if not finished:
            die("recapture: no completed run with logs found; nothing to recapture")
        # Process newest-first so the most recent installs inform the decision.
        target_dirs = sorted(finished, reverse=True)

    ran_any = False
    for target_run_dir in target_dirs:
        sentinel = target_run_dir / "dep_capture.done"
        if force:
            # Drop sentinel so the worker fires unconditionally on this run.
            try:
                sentinel.unlink(missing_ok=True)
            except Exception:
                pass
        elif sentinel.is_file():
            continue

        # Flock the run dir (refuse to race a live orchestrator). --phase judge
        # pattern: construct State → catch StateLockedError → skip (not fatal
        # for multi-run consolidation; we still capture the others).
        try:
            target_st = State(leerie_root, target_run_dir.name, repo_root=repo_root)
        except StateLockedError as e:
            log(f"recapture: skipping {target_run_dir.name!r}: another "
                f"orchestrator owns the run (holding flock on {e.run_dir}).")
            continue
        target_st.load()  # non-fatal if state.json is missing (older runs)

        log(f"recapture: scanning {target_run_dir / 'logs'} ...")
        try:
            asyncio.run(capture_repo_deps(
                repo_root, target_st,
                caps=caps, models=models, efforts=efforts,
                replace=force,
            ))
            ran_any = True
        except Exception as exc:
            log(f"recapture: error during dep_capture for {target_run_dir.name}: {exc}")

    if not ran_any and not force:
        log("recapture: all eligible runs already captured (use --force to re-run)")


def _split_readme_headers(text: str) -> list[tuple[int, str, str]]:
    """Return [(line_index, header_text, body_until_next_header), ...] for
    text. Supports three header styles:
      - ATX: lines starting with `#`, `##`, etc.
      - Setext: a line followed by `===` or `---` of equal length.
      - Asciidoc: lines starting with `==`, `===`, etc. (no `#`).

    Returns sections in document order. The first section's header text
    is "" if the file does not start with a header (the "intro").
    """
    lines = text.split("\n")
    n = len(lines)
    # Find header line indices first.
    headers: list[tuple[int, str]] = []  # (line_index, header_text)
    i = 0
    while i < n:
        line = lines[i]
        stripped = line.strip()
        # ATX (`# Foo`, `## Foo`, etc.). Asciidoc level markers (`==
        # Foo`) are picked up too — the leading `=` group reads as
        # header decoration once we strip it for keyword matching.
        if stripped.startswith("#") or stripped.startswith("=="):
            headers.append((i, stripped))
            i += 1
            continue
        # Setext: `Foo\n=====` (h1) or `Foo\n-----` (h2). The underline
        # must be at least 3 chars of `=` or `-` and roughly the length
        # of the line above (RST conventions are looser than Markdown's
        # but we accept any underline ≥3 chars).
        if i + 1 < n and stripped:
            nxt = lines[i + 1].strip()
            if len(nxt) >= 3 and (set(nxt) == {"="} or set(nxt) == {"-"}):
                headers.append((i, stripped))
                i += 2
                continue
        i += 1

    if not headers:
        return [(0, "", text)]

    sections: list[tuple[int, str, str]] = []
    # Intro before the first header.
    if headers[0][0] > 0:
        intro_body = "\n".join(lines[: headers[0][0]])
        sections.append((0, "", intro_body))
    for k, (start, hdr) in enumerate(headers):
        end = headers[k + 1][0] if k + 1 < len(headers) else n
        body = "\n".join(lines[start: end])
        sections.append((start, hdr, body))
    return sections


def _is_install_section(header: str) -> bool:
    """True if a header (after decoration-strip) matches the section
    regex. Empty header (the intro) is not an install section by
    definition."""
    if not header:
        return False
    cleaned = _HEADER_DECOR_RE.sub("", header)
    return bool(_README_SECTION_RE.search(cleaned))


def _slice_code_fences_with_install_hints(text: str, ctx_lines: int = 10) -> str:
    """Fallback layer: scan for fenced code blocks containing recognized
    install commands and return them with ±ctx_lines of surrounding
    context. Used when the header-aware extractor finds no install
    section."""
    lines = text.split("\n")
    n = len(lines)
    in_fence = False
    fence_start = -1
    fence_marker = ""
    kept_ranges: list[tuple[int, int]] = []  # inclusive [start, end] line indices
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not in_fence:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = True
                fence_start = i
                fence_marker = stripped[:3]
        else:
            if stripped.startswith(fence_marker):
                # Fence closed at line i.
                fence_text = "\n".join(lines[fence_start: i + 1])
                if _INSTALL_CMD_HINT_RE.search(fence_text):
                    lo = max(0, fence_start - ctx_lines)
                    hi = min(n - 1, i + ctx_lines)
                    kept_ranges.append((lo, hi))
                in_fence = False
                fence_start = -1
                fence_marker = ""
    if not kept_ranges:
        return ""
    # Merge overlapping ranges in order.
    merged: list[tuple[int, int]] = []
    for lo, hi in sorted(kept_ranges):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    pieces = ["\n".join(lines[lo: hi + 1]) for lo, hi in merged]
    return "\n\n…\n\n".join(pieces)


# Per-extract budgets, in bytes. README ≤1KB intro + matched sections
# under an 8KB total cap. The fixture set as a whole is capped at 24KB
# by gather_provision_fixtures().
_README_INTRO_BUDGET = 1024
_README_EXTRACT_BUDGET = 8192
_README_FALLBACK_BUDGET = 6144  # final top-of-file fallback
_FIXTURE_TOTAL_BUDGET = 24576   # 24KB hard ceiling per repo


def extract_readme_sections(text: str) -> str:
    """Extract the install/setup-relevant slice of a README.

    Fallback chain (DESIGN §6½):
      1. Header-aware: ≤1KB intro + sections whose header matches
         _README_SECTION_RE, under an 8KB total cap.
      2. Code-fence hint: if no section header matches, scan for code
         fences containing install commands (pip/npm/cargo/etc.) and
         keep them with ±10 lines of surrounding context.
      3. Final: top-6KB of the README verbatim.

    Returns the extracted text (≤8KB). Empty input → empty output.
    """
    if not text:
        return ""
    sections = _split_readme_headers(text)
    out_parts: list[str] = []
    used = 0

    # Intro budget: first section body, whether labeled or not. A README
    # that starts with `# Project\n\nElevator pitch.\n\n## Install` has
    # its first section named "Project" (not ""), but the elevator pitch
    # is still the intro from the user's point of view. Including the
    # first section's body (up to the intro budget) keeps that signal
    # without making the whole top-level section count as an install
    # section.
    if sections:
        first_body = sections[0][2][:_README_INTRO_BUDGET]
        if first_body.strip():
            out_parts.append(first_body)
            used += len(first_body)

    matched_any = False
    for _idx, hdr, body in sections:
        if not _is_install_section(hdr):
            continue
        matched_any = True
        if used >= _README_EXTRACT_BUDGET:
            break
        room = _README_EXTRACT_BUDGET - used
        out_parts.append(body[:room])
        used += min(len(body), room)

    if matched_any:
        return "\n\n".join(out_parts)

    # Fallback 2: code-fence install-hint slicer.
    fence_slice = _slice_code_fences_with_install_hints(text)
    if fence_slice:
        intro_part = out_parts[0] if out_parts else ""
        fence_room = max(0, _README_EXTRACT_BUDGET - len(intro_part))
        fence_part = fence_slice[:fence_room]
        if intro_part:
            return intro_part + "\n\n" + fence_part
        return fence_part

    # Fallback 3: top-6KB.
    return text[:_README_FALLBACK_BUDGET]


def _read_file_safely(path: Path, budget: int) -> str:
    """Read a file with a byte ceiling, swallowing missing-file and
    decode errors. Used by gather_provision_fixtures to assemble the
    fixture dict from optional repo files."""
    try:
        return path.read_text(errors="replace")[:budget]
    except (OSError, UnicodeError):
        return ""


# Manifest file groups for the fixture gatherer.
_PROVISION_ROOT_MANIFESTS = (
    "package.json", "pyproject.toml", "go.mod", "Cargo.toml",
    "Gemfile", "Makefile", "pom.xml",
    "build.gradle", "build.gradle.kts",
)
_PROVISION_WORKFLOW_PREFERRED_RE = re.compile(r"(?i)\b(ci|test|build|release)\b")
_PROVISION_WORKFLOW_SKIP_RE = re.compile(r"(?i)\b(codeql|stale|dependabot)\b")


def _sample_workspace_manifests(repo_root: Path, pkg_json_text: str,
                                 per_file_budget: int,
                                 max_files: int) -> list[tuple[str, str]]:
    """For a monorepo whose root package.json declares `workspaces`,
    return up to `max_files` sampled child manifests as (rel_path, text)
    pairs. Returns [] if no workspaces are declared or no children are
    found."""
    try:
        pkg = json.loads(pkg_json_text)
    except (ValueError, TypeError):
        return []
    workspaces = pkg.get("workspaces")
    if isinstance(workspaces, dict):
        # npm/yarn shape: {"packages": [...]}
        workspaces = workspaces.get("packages")
    if not isinstance(workspaces, list) or not workspaces:
        return []

    sampled: list[tuple[str, str]] = []
    seen: set[Path] = set()
    for pattern in workspaces:
        if len(sampled) >= max_files:
            break
        if not isinstance(pattern, str):
            continue
        # glob via Path.glob (handles `packages/*` style).
        try:
            for child in sorted(repo_root.glob(pattern + "/package.json")):
                if len(sampled) >= max_files:
                    break
                if child in seen:
                    continue
                seen.add(child)
                rel = child.relative_to(repo_root).as_posix()
                sampled.append((rel, _read_file_safely(child, per_file_budget)))
        except (OSError, ValueError):
            continue
    return sampled


def gather_provision_fixtures(repo_root: Path) -> dict:
    """Assemble the LLM-fallback worker's input set. Returns a dict with
    keys:
      - readme: header-aware extract (≤8KB)
      - manifests: dict[rel_path -> text] of root manifest files present
      - workspace_manifests: list[(rel_path, text)] sampled child
        manifests for monorepos (≤3 files, 1KB each)
      - workflows: list[(filename, text)] up to 2 GitHub Actions files
        preferring ci/test/build/release names
      - contributing: text of CONTRIBUTING.md or docs/DEVELOPMENT.md
        (≤4KB) or empty
      - total_bytes: int — actual size after assembly
      - hit_ceiling: bool — True if any section was truncated by the
        24KB total budget

    See DESIGN.md §6½ "Provision-worker input fixtures."
    """
    out: dict = {
        "readme": "",
        "manifests": {},
        "workspace_manifests": [],
        "workflows": [],
        "contributing": "",
        "total_bytes": 0,
        "hit_ceiling": False,
    }

    def add_bytes(n: int) -> bool:
        """Return True if we have budget for `n` more bytes; flip
        hit_ceiling otherwise."""
        if out["total_bytes"] + n > _FIXTURE_TOTAL_BUDGET:
            out["hit_ceiling"] = True
            return False
        out["total_bytes"] += n
        return True

    # --- README ---
    readme_paths = [
        repo_root / "README.md",
        repo_root / "README.rst",
        repo_root / "README",
        repo_root / "README.txt",
        repo_root / "README.adoc",
    ]
    for rp in readme_paths:
        if rp.is_file():
            raw = _read_file_safely(rp, _README_EXTRACT_BUDGET * 4)
            extract = extract_readme_sections(raw)
            if add_bytes(len(extract)):
                out["readme"] = extract
            break

    # --- Root manifests ---
    pkg_json_text = ""
    for name in _PROVISION_ROOT_MANIFESTS:
        if out["hit_ceiling"]:
            break
        p = repo_root / name
        if not p.is_file():
            continue
        text = _read_file_safely(p, 8192)
        if not add_bytes(len(text)):
            break
        out["manifests"][name] = text
        if name == "package.json":
            pkg_json_text = text

    # --- Workspace child manifests (monorepo) ---
    if pkg_json_text and not out["hit_ceiling"]:
        children = _sample_workspace_manifests(
            repo_root, pkg_json_text, per_file_budget=1024, max_files=3)
        for rel, text in children:
            if not add_bytes(len(text)):
                break
            out["workspace_manifests"].append((rel, text))

    # --- Workflows ---
    if not out["hit_ceiling"]:
        wf_dir = repo_root / ".github" / "workflows"
        if wf_dir.is_dir():
            try:
                candidates = [p for p in sorted(wf_dir.iterdir())
                              if p.suffix in (".yml", ".yaml") and p.is_file()
                              and not _PROVISION_WORKFLOW_SKIP_RE.search(p.name)]
            except OSError:
                candidates = []
            # Prefer files whose names match ci/test/build/release.
            preferred = [p for p in candidates
                         if _PROVISION_WORKFLOW_PREFERRED_RE.search(p.name)]
            others = [p for p in candidates if p not in preferred]
            ordered = preferred + others
            for p in ordered[:2]:
                text = _read_file_safely(p, 4096)
                if not add_bytes(len(text)):
                    break
                out["workflows"].append((p.name, text))

    # --- CONTRIBUTING / DEVELOPMENT ---
    if not out["hit_ceiling"]:
        for cand in (repo_root / "CONTRIBUTING.md",
                     repo_root / "docs" / "DEVELOPMENT.md",
                     repo_root / "DEVELOPMENT.md"):
            if cand.is_file():
                text = _read_file_safely(cand, 4096)
                if add_bytes(len(text)):
                    out["contributing"] = text
                break

    return out


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

# Sections where "nothing to report" is a legitimate answer — a worker that
# made no decisions yet, or has no open unknowns, should be able to say so.
# Every other section must carry real content for the successor to pick up.
_CHECKPOINT_SECTIONS_ALLOW_NONE = {"## Decisions made", "## Open unknowns"}

# Single-token substitutes for content. A required section that contains
# only one of these is not a checkpoint, it's a placeholder — the
# successor would learn nothing from it. `_normalize_for_noise()` strips
# trailing punctuation and collapses repeated `?` before the membership
# check, so `None.`, `TBD!`, and `???` are caught alongside the bare
# tokens.
_NOISE_TOKENS = {
    "none", "n/a", "na", "tbd",
    "nothing", "unknown", "todo", "pending",
    "—", "--", "-", "?",
}


def _split_checkpoint_sections(content: str) -> dict[str, list[str]]:
    """Split a checkpoint file by `## ` headers into {header: lines}.
    Lines are stripped; blanks dropped. Returns one bucket per header
    found, in the order they appeared."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in content.splitlines():
        if raw.startswith("## "):
            current = raw.rstrip()
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        stripped = raw.strip()
        if stripped:
            sections[current].append(stripped)
    return sections


def validate_checkpoint(path: str,
                        worktree_root: Path | None = None) -> str | None:
    """Return an error description if the checkpoint is structurally incomplete,
    None if it looks good. A missing section produces a confused successor;
    so does a section that contains only a placeholder.

    `worktree_root`, when supplied, enables the freshness check on
    `## Files touched`: every path listed there must either exist in the
    worktree or carry a `[deleted]` annotation in its bullet line. Skip the
    freshness check when the worktree is gone (e.g. cleaned up already)."""
    p = Path(path)
    if not p.exists():
        return f"checkpoint file does not exist: {path}"
    content = p.read_text()

    missing = [s for s in _CHECKPOINT_SECTIONS if s not in content]
    if missing:
        return (f"missing {len(missing)} required section(s): "
                f"{', '.join(missing)}")

    sections = _split_checkpoint_sections(content)
    for header in _CHECKPOINT_SECTIONS:
        lines = sections.get(header, [])
        if not lines:
            return f"section '{header}' has no content"
        # Reject single-token noise placeholders in the sections that MUST
        # carry real handoff context. Allow them only in the two sections
        # where "nothing to report" is a legitimate answer.
        if header in _CHECKPOINT_SECTIONS_ALLOW_NONE:
            continue
        if all(_normalize_for_noise(l) in _NOISE_TOKENS for l in lines):
            return (f"section '{header}' contains only placeholder tokens "
                    f"({lines!r}) — successor cannot resume from this")

    # Freshness check: paths under `## Files touched` must still exist in
    # the worktree (or be explicitly marked [deleted]). A stale checkpoint
    # naming a file the successor cannot find produces wasted re-discovery.
    if worktree_root is not None and worktree_root.exists():
        for line in sections.get("## Files touched", []):
            path_str, is_deleted = _parse_touched_file_line(line)
            if path_str is None:
                continue  # not a path-shaped line; skip narration
            if is_deleted:
                continue
            if not (worktree_root / path_str).exists():
                return (f"`## Files touched` lists '{path_str}' but the file "
                        "does not exist in the worktree and is not flagged "
                        "[deleted] — checkpoint is stale")

    return None


def _normalize_for_noise(line: str) -> str:
    """Reduce a checkpoint line to its comparison key for `_NOISE_TOKENS`.
    Strips the bullet marker, lowercases, collapses a pure run of `?` to a
    single `?`, then peels off trailing `.`/`!`/`…` — so `None.`, `TBD!`,
    and `???` all match their bare-token forms. The `?`-collapse runs
    before the trailing-punctuation strip; otherwise `???` would be
    eaten down to the empty string and miss the `?` token entirely."""
    s = _strip_bullet(line).lower().strip()
    if s and set(s) == {"?"}:
        s = "?"
    while s and s[-1] in ".!…":
        s = s[:-1].rstrip()
    return s


def _strip_bullet(line: str) -> str:
    """Strip leading markdown bullet markers (`-`, `*`, `1.`) before noise
    comparison. `- none` should be rejected the same as bare `none`."""
    stripped = line.lstrip()
    for prefix in ("- ", "* ", "+ "):
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip()
    # numbered list: `1. `, `2. `, …
    if len(stripped) >= 3 and stripped[0].isdigit():
        i = 1
        while i < len(stripped) and stripped[i].isdigit():
            i += 1
        if stripped[i:i+2] == ". ":
            return stripped[i+2:].strip()
    return stripped


def _parse_touched_file_line(line: str) -> tuple[str | None, bool]:
    """Extract a file path from a `## Files touched` line and detect the
    `[deleted]` annotation. Returns (path, is_deleted) or (None, False)
    if the line doesn't look like a path entry. Conservative: only treats
    a line as path-shaped if its first whitespace-delimited token looks
    like a relative path (contains `/`, `.`, or ends with a common code
    extension). Narration lines without a path token are skipped."""
    body = _strip_bullet(line)
    if not body:
        return (None, False)
    is_deleted = "[deleted]" in body.lower()
    # The first token is the candidate path; strip backticks and trailing
    # punctuation that often surrounds paths in markdown.
    first = body.split()[0].strip("`,:;()[]")
    if not first or first.startswith("#"):
        return (None, False)
    # Only treat as a path if it has a separator or a dot — a bare word
    # like "refactored" is narration, not a path.
    if "/" in first or "." in first:
        return (first, is_deleted)
    return (None, False)


# --- result cross-field validation -------------------------------------------

def validate_result(result: dict) -> tuple[str, str] | None:
    """Cross-field invariant checks that JSON Schema cannot express.
    Returns `(failure_kind, message)` if the result is self-contradictory,
    None if ok. `failure_kind` is the structured discriminator
    `_retryable_failure` dispatches on; `message` is the human-readable
    diagnostic surfaced to the user.

    Per DESIGN §8, the §8 confidence gate is the only load-bearing
    discipline; the criteria file is informational (DESIGN §9). A
    `complete` status is accepted regardless of what `criteria_results`
    carries — empty, missing, or with `met:false` entries are all
    valid. The unmet entries are recorded on the result for telemetry
    and surface as conformance warnings, but do not affect terminal
    status. The other branches (handoff, blocked, failed, clarification)
    still enforce the mechanical-precondition fields their next-step
    consumers require."""
    status = result.get("status")
    if status == "incomplete-handoff":
        cp = result.get("checkpoint_path")
        if not cp:
            return ("broken",
                    "status='incomplete-handoff' but checkpoint_path is null")
        if not Path(cp).exists():
            # "empty_handoff" — the Claude Code session-limit / rate-limit no-op
            # case and the --max-turns-with-no-checkpoint case both land here.
            # Both are corrective-note retryable: a fresh worker can plausibly
            # do better. See _RETRYABLE_FAILURE_KINDS.
            return ("empty_handoff",
                    f"checkpoint_path '{cp}' does not exist on disk")
    elif status == "blocked":
        if not (result.get("blocker") or "").strip():
            return ("broken", "status='blocked' but blocker field is empty")
    elif status == "failed":
        # A `failed` result without a summary is a worker contract violation:
        # the prompt requires a diagnosis. Without it, the user sees a canned
        # placeholder rather than a real explanation of what went wrong.
        if not (result.get("summary") or "").strip():
            return ("broken",
                    "status='failed' but summary is empty — no diagnosis provided")
    elif status == "needs-clarification":
        # DESIGN §11 mid-execution clarification: the question and the
        # work-in-progress checkpoint MUST both be present. The question
        # is what gets surfaced to the user; the checkpoint is what
        # carries the partial work forward to the re-spawned implementer.
        # The why_underivable field inside the question is required by
        # the schema and re-checked here as a content (not just shape)
        # gate against the worker drifting toward "ask instead of
        # research."
        cq = result.get("clarification_question")
        if not cq:
            return ("broken",
                    "status='needs-clarification' but clarification_question "
                    "is null — see DESIGN §11")
        for field in ("id", "question", "why_underivable"):
            if not (cq.get(field) or "").strip():
                return ("broken",
                        f"status='needs-clarification' but "
                        f"clarification_question.{field} is empty — "
                        "see DESIGN §11")
        cp = result.get("checkpoint_path")
        if not cp:
            return ("broken",
                    "status='needs-clarification' but checkpoint_path is "
                    "null — the work-in-progress must survive the question")
        if not Path(cp).exists():
            return ("broken",
                    f"status='needs-clarification' but checkpoint_path "
                    f"'{cp}' does not exist on disk")
    return None


# --- post-implementation diff scope check ------------------------------------

async def check_diff_scope(sid: str, worktree: str, subtask: dict,
                           st: State) -> str | None:
    """Check the implementer's diff for violations.
    Returns a fatal error string if protected paths were touched.
    Logs a non-fatal warning for unexpected scope. Returns None when clean.

    The diff is computed against the run branch (`leerie/runs/<run-id>`)
    — the base every subtask branched off of. Hardcoding `leerie/staging`
    here used to silently disable the check after the per-run refactor
    (the branch doesn't exist), so the protected-path enforcement was off."""
    run_branch = compute_run_branch(st.run_id)
    r = await run_proc(
        ["git", "diff", "--name-only", f"{run_branch}..HEAD"],
        cwd=worktree,
    )
    if r.returncode != 0:
        return None
    touched = [f for f in r.stdout.strip().splitlines() if f]
    if not touched:
        return None

    # fatal: any changes to protected meta-directories are out of bounds.
    # `.claude/{agents,commands,skills}/` are exempt (documented Claude
    # Code user-deliverable locations); top-level `.claude/` files
    # (settings.json, settings.local.json) stay protected. See
    # is_protected_path() for the rule.
    protected = [f for f in touched if is_protected_path(f)]
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
        st.data.setdefault("scope_warnings", {})[sid] = {
            "touched": touched, "expected": expected, "reason": reason,
        }
        st.save()

    return None


# --- post-integrator commit check --------------------------------------------

async def check_merge_committed(staging: Path) -> str | None:
    """Return an error if the staging worktree is still mid-merge.

    An integrator that returns status 'resolved' must have completed the merge
    commit. If `MERGE_HEAD` still exists, the merge was never concluded — the
    worker claimed success while leaving the worktree in a broken mid-merge
    state. This is the integrator-side analogue of `check_branch_has_commits`:
    it catches a worker lying about having finished."""
    r = await run_proc(
        ["git", "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        cwd=str(staging),
    )
    if r.returncode == 0:
        return ("the staging worktree is still mid-merge (MERGE_HEAD exists) — "
                "the integrator did not complete the merge commit")
    # also reject a worktree left with staged-but-uncommitted conflict edits
    s = await run_proc(
        ["git", "diff", "--cached", "--name-only"],
        cwd=str(staging),
    )
    if s.returncode == 0 and s.stdout.strip():
        return ("the staging worktree has staged but uncommitted changes — "
                "the integrator did not complete the merge commit")
    return None


async def check_integrator_commit(staging: Path) -> str | None:
    """Return an error if the integrator's merge commit touched .leerie/ files.
    The integrator should only touch project files, never coordination artifacts."""
    r = await run_proc(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=str(staging),
    )
    if r.returncode != 0:
        return None
    bad = [f for f in r.stdout.strip().splitlines()
           if f and f.startswith(".leerie/")]
    if bad:
        return f"integrator commit touched coordination files: {bad}"
    return None


# --- branch-has-commits verification -----------------------------------------

async def branch_has_commits_ahead(worktree: str,
                                   parent_branch: str) -> bool:
    """True iff the subtask branch has ≥1 commit ahead of `parent_branch`.

    Positive-polarity, unambiguous: returns True ONLY when the worktree
    exists, `git log parent..HEAD` succeeds, AND its output is non-empty.
    A missing worktree or a failed git command returns False — "can't
    prove commits exist" is treated as "no commits," so no caller mistakes
    an indeterminate state for real committed work. `check_branch_has_commits`
    (the no-op gate) and the `empty_handoff` rescue in `settle_subtask` both
    key on this: the rescue keeps committed work only when it can *prove* the
    commit exists, never on a bare `None`/indeterminate result."""
    if not Path(worktree).exists():
        return False  # worktree gone — can't determine, treat as no commits
    try:
        r = await run_proc(
            ["git", "log", f"{parent_branch}..HEAD", "--oneline"],
            cwd=worktree,
        )
    except OSError:
        return False
    if r.returncode != 0:
        return False
    return bool(r.stdout.strip())


async def check_branch_has_commits(sid: str, worktree: str,
                                   parent_branch: str
                                   ) -> tuple[str, str] | None:
    """Return `(failure_kind, message)` if the implementer's subtask
    branch has no commits ahead of the run branch (`parent_branch` —
    typically `leerie/runs/<run-id>`), else None. An empty diff means the
    worker produced schema-valid JSON claiming success while doing
    nothing — a silent no-op that wastes an integration attempt. The
    `"no_commits"` kind is retryable per `_RETRYABLE_FAILURE_KINDS`.

    Note the deliberate asymmetry with `branch_has_commits_ahead`: an
    indeterminate state (worktree gone / git failed) returns None here —
    "don't block" — because this is a *gate* on a `complete` claim, where
    the safe default is to let it through rather than fail a subtask on an
    unverifiable git error. The rescue path uses `branch_has_commits_ahead`
    instead, whose safe default is the opposite (don't rescue unless proven)."""
    if not Path(worktree).exists():
        return None  # worktree gone — can't determine, don't block
    try:
        r = await run_proc(
            ["git", "log", f"{parent_branch}..HEAD", "--oneline"],
            cwd=worktree,
        )
    except OSError:
        return None
    if r.returncode != 0:
        return None
    if not r.stdout.strip():
        return ("no_commits",
                f"subtask branch for {sid} has no commits ahead of the run "
                f"branch ({parent_branch}) — implementer claimed complete "
                "without making any changes")
    return None


# --- conflict marker scan post-integration -----------------------------------

async def scan_conflict_markers(staging: Path) -> str | None:
    """Return error if unresolved conflict markers remain in the staging tree.
    git grep exit 0 = matches found (bad); exit 1 = clean (good)."""
    if not staging.exists():
        return None
    try:
        r = await run_proc(
            ["git", "grep", "-l", "^<<<<<<< ", "HEAD"],
            cwd=str(staging),
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


# --- resume state integrity check --------------------------------------------

def validate_resume_state(data: dict) -> None:
    """Assert the structure of a loaded state.json before resuming. A corrupt
    or hand-edited file produces wrong behavior; fail fast rather than run
    silently. Only `task` is strictly required here — a run interrupted before
    scheduling has no `waves` yet, and main() handles that case separately with
    a clearer message."""
    if "task" not in data or not str(data.get("task", "")).strip():
        die("state.json has no usable 'task' — cannot resume. "
            "Inspect the run's state.json manually "
            "(under <state-root>/runs/<run-id>/).")

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


def _api_error_category(status: int | None) -> str | None:
    """Map a `claude -p` `api_error_status` to a coarse failure category.

    401→auth (subscription / credential rejection), 429→quota (rate limit),
    529→overload (transient gateway). None for any other or absent status.
    `api_error_status` on the result envelope is always a JSON number or null
    (SDK type `api_error_status?: number | null`), so this numeric set is
    exhaustive — no string forms ever reach here.

    Single source of truth for the status→category mapping, shared by
    `_is_auth_or_quota_failure` (which needs the bool) and
    `_classify_failure_kind` (which needs the category string)."""
    return {401: "auth", 429: "quota", 529: "overload"}.get(status)


def _is_auth_or_quota_failure(envelope: dict) -> bool:
    """True if the `claude -p` envelope looks like a 401/429/529/
    auth-message rejection from the Anthropic gateway.

    These need backoff, not the immediate corrective retry that the
    schema-error path uses — the request was rejected before reaching
    a model and a fresh request will be rejected too until the user's
    Claude Code subscription window clears (401/429) or the gateway's
    transient overload (529) subsides. The auth/quota retry loop in
    claude_p() consults this classifier; non-matching envelopes fall
    through to the existing 2-attempt schema loop unchanged.

    Gated on `is_error`: a successful, schema-valid envelope must never
    match, no matter what its `result` text says. Without this gate, a
    worker whose task legitimately talks about API rate limiting or auth
    (e.g. planning a rate-limited endpoint) trips the text markers below
    on its own correct output — the orchestrator then burns the full
    backoff budget re-running an already-successful worker and can
    eventually raise a false "subscription capped" WorkerError.
    """
    if not envelope.get("is_error"):
        return False
    if _api_error_category(envelope.get("api_error_status")) is not None:
        return True
    # No numeric status matched — fall back to text markers on the result
    # body. This path is specific to the retry classifier (not shared with
    # `_classify_failure_kind`, which reads only the numeric status).
    msg = str(envelope.get("result") or "").lower()
    return ("invalid authentication" in msg
            or "rate limit" in msg
            or "rate-limit" in msg)


def _classify_failure_kind(envelope: dict, parsed_ok: bool) -> str | None:
    """Categorize *why* a captured `claude -p` call failed, for the
    `failure_kind` field on the calls.ndjson record. Returns None on
    success (the caller writes null).

    Reads only fields present on the returned result envelope — `is_error`,
    `api_error_status` (numeric 401/429/529 or null, per the SDK type),
    `terminal_reason`, and the caller's already-computed `parsed_ok`:

    - "api_error"        — gateway rejected the request. Split by
      `_api_error_category` (401→auth, 429→quota, 529→overload — the same
      map the auth/quota retry path uses); a bare is_error with no matching
      numeric status stays "api_error".
    - "incomplete"       — the worker stopped mid-work (e.g. --max-turns);
      `terminal_reason` set and not "completed". leerie already warns on
      this at the capture site.
    - "schema_parse_failed" — the worker returned but its output did not
      validate against the call_type schema (`not parsed_ok`, no is_error).
      This is the dominant real-world failure mode.
    - None               — success (is_error false and parsed_ok true).

    KNOWN GAP (see the capture-site comment and DESIGN §14): the richest
    failures — RateLimitedExit (out-of-credits / rate-limit) and WorkerError
    (nonzero exit, no-result-event, 10 MiB overlong line) — are raised as
    exceptions inside `_read_stream` and propagate *past* the capture block,
    so no record is written for them at all and `failure_kind` cannot cover
    them. Tagging those would require threading a capture call into the raise
    sites; deferred."""
    if not envelope.get("is_error") and parsed_ok:
        return None
    if envelope.get("is_error"):
        # Same status→category map the auth/quota retry path keys on
        # (`_api_error_category`); a bare is_error with no matching numeric
        # status stays "api_error".
        cat = _api_error_category(envelope.get("api_error_status"))
        return f"api_error:{cat}" if cat else "api_error"
    term = envelope.get("terminal_reason") or ""
    if term and term != "completed":
        return "incomplete"
    # Reaching here means is_error is false and (is_error false, parsed_ok true)
    # already returned None above — so parsed_ok is necessarily false here.
    return "schema_parse_failed"


def _extract_tool_result_text(block: dict) -> str:
    """Tool-result `content` is either a string or a list of content
    blocks (`{type: "text", text: "..."}`). Normalize to a plain
    string so summaries / file output don't have to branch."""
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text") or "")
        return " ".join(parts)
    return ""


# libc's EAGAIN ("Resource temporarily unavailable") / shell fork-failure
# strings. When a worker has exhausted its cgroup pids.max, further forks
# are denied and shells cannot launch. This text SURVIVES into the
# tool-result only for commands that emit output before the shell dies;
# trivial commands (`echo`, `true`) surface a bare "Exit code 1" instead.
# So this is a cheap *fast-path* confirmation — the authoritative signal is
# the cgroup `pids.events` read (DESIGN §6 *Detecting PID exhaustion*).
_FORK_EAGAIN_MARKERS = (
    "resource temporarily unavailable",
    "cannot fork",
    "cannot allocate memory",
    "fork: retry",
)


def _is_fork_exhaustion(text: str) -> bool:
    """True if `text` carries a shell fork-failure signature. Advisory —
    absence does NOT rule out PID exhaustion (see marker list above)."""
    low = text.lower()
    return any(m in low for m in _FORK_EAGAIN_MARKERS)


def _tool_result_outcome(event: dict) -> bool | None:
    """Classify a stream event for `_read_stream`'s PID-exhaustion window:

    - `True`  — the event is a tool_result and it errored,
    - `False` — the event is a tool_result and it succeeded,
    - `None`  — the event is NOT a tool_result (assistant / system /
      rate_limit / result).

    The detector appends only non-None outcomes to its window, so the
    events *between* tool-results (which are the majority of the stream,
    and which always separate one tool-result from the next) neither count
    as errors nor reset the window. This is the crux of the window design:
    a *consecutive* tool-result counter could never exceed one, because two
    tool-results are never adjacent in the stream (the model's assistant
    turn always sits between them). Not tool-specific — under PID
    exhaustion every shell-spawning tool fails, so the fraction of recent
    tool-results that errored, plus the authoritative cgroup probe, is what
    separates exhaustion from an ordinary single failing command."""
    if event.get("type") != "user":
        return None
    for b in (event.get("message", {}) or {}).get("content", []) or []:
        if b.get("type") == "tool_result":
            return bool(b.get("is_error"))
    return None


def _tag_each_line(prefix: str, content: str) -> str:
    """Prefix the first non-empty line of `content` with `prefix`;
    subsequent lines get a width-matched continuation prefix that
    preserves the `[<sid>` worker-attribution segment but drops the
    event-kind token.

    Used for tool_result summaries whose content can be multi-line
    (a Read of a source file, a Grep result, a stack trace, a
    compound command's stdout). Lines 2+ must stay attributable to
    this worker — in a parallel run with max_parallel=4, untagged
    continuation lines would be indistinguishable from another
    worker's output. The kind token (`tool-fail`, `tool-ok`)
    repeated on every line, however, is per-tool-call information
    that obscures the actual content when repeated.

    For single-line content the result is `f'{prefix} {content}'`.
    For empty content it returns the empty string so the caller's
    truthiness check naturally drops it. If `prefix` doesn't match
    the expected `<indent>[<sid> <kind>]` shape (defensive — every
    current caller does), the helper falls back to repeating
    `prefix` on every line."""
    lines = [ln for ln in content.splitlines() if ln]
    if not lines:
        return ""
    open_b = prefix.find("[")
    close_b = prefix.rfind("]")
    cont = prefix
    if open_b >= 0 and close_b > open_b:
        inside = prefix[open_b + 1 : close_b]
        sid, _, kind = inside.partition(" ")
        if sid and kind:
            keep = prefix[: open_b + 1] + sid
            pad = " " * (close_b - len(keep))
            cont = keep + pad + "]"
    return "\n".join([f"{prefix} {lines[0]}",
                      *(f"{cont} {ln}" for ln in lines[1:])])


def _summarize_tool_use(sid: str, block: dict, verbosity: str) -> str:
    """Map one `tool_use` content block to a one-line inline summary.
    `verbosity` is "stream" or "debug" by the time this is called;
    debug allows wider truncation limits."""
    name = block.get("name", "?")
    inp = block.get("input", {}) or {}
    if name == "Read":
        return f"  [{sid} read] {inp.get('file_path', '?')}"
    if name == "Grep":
        path = inp.get("path", "")
        suffix = f" in {path}" if path else ""
        return f"  [{sid} grep] {inp.get('pattern', '?')}{suffix}"
    if name == "Glob":
        return f"  [{sid} glob] {inp.get('pattern', '?')}"
    if name == "Bash":
        cmd_lines = (inp.get("command") or "").splitlines()
        cmd = cmd_lines[0] if cmd_lines else ""
        # No truncation: a mid-cut shell command loses the part you
        # actually need to read (the operands at the end of a pipeline).
        # Multi-line scripts still show only the first line; the
        # per-worker .log file has the full command.
        return f"  [{sid} bash] {cmd}"
    if name in ("Write", "Edit", "NotebookEdit"):
        return f"  [{sid} {name.lower()}] {inp.get('file_path', '?')}"
    if name == "WebFetch":
        return f"  [{sid} fetch] {inp.get('url', '?')}"
    if name == "WebSearch":
        return f"  [{sid} search] {inp.get('query', '?')}"
    if name == "StructuredOutput":
        # `input` is the worker's full structured payload. Only surface
        # at debug — at stream this is noise since the `done` line
        # follows immediately. Per-worker file has it whole regardless.
        if verbosity == "debug":
            return f"  [{sid}] finalizing output {str(inp)}"
        return None
    # Unknown / MCP tool — dump the full repr of the input. The
    # tail of an MCP-tool input (a Supabase query operand, a Stripe
    # API parameter) is where the useful detail lives; mid-cut
    # loses it. Per-worker .log file matches.
    return f"  [{sid} {name}] {str(inp)}"


def _summarize_stream_event(sid: str, event: dict, verbosity: str) -> str | None:
    """Return the one-line inline-log summary for one stream event, or
    None to drop the event from the inline log. The per-worker file
    always gets the raw event regardless of verbosity — this function
    only governs what surfaces inline.

    Levels in increasing detail: quiet, normal, stream, debug. At
    quiet/normal, individual events are dropped (leerie's existing
    phase / subtask-status log lines stand alone), with the one
    exception of result-with-error which surfaces at every level
    (clig.dev "errors emit at every level")."""
    t = event.get("type")
    sub = event.get("subtype")

    # quiet/normal: drop everything except worker-level errors.
    if verbosity in ("quiet", "normal"):
        if t == "result" and event.get("is_error"):
            n = event.get("num_turns", "?")
            return f"  [{sid}] worker failed ({sub}, turns={n})"
        return None

    # stream and debug: per-event summaries.
    if t == "system":
        if sub == "init":
            model = event.get("model", "?")
            return f"  [{sid}] starting (model={model})"
        # hook_started / hook_response are noisy (every SessionStart
        # hook fires once each); surface only at debug.
        if verbosity == "debug" and sub in ("hook_started", "hook_response"):
            hook_name = event.get("hook_name", "?")
            return f"  [{sid} hook] {sub} {hook_name}"
        return None

    if t == "assistant":
        msg = event.get("message", {})
        blocks = msg.get("content", []) or []
        lines = []
        for b in blocks:
            bt = b.get("type")
            if bt == "text":
                # First, inspect the text for a Claude Code session-limit /
                # rate-limit message. Detection here is load-bearing for
                # the text path of the limit: when the subscription limit
                # is hit, claude -p returns the session-limit string as
                # assistant text and then closes the session with
                # subtype="success" — without this check, validate_result
                # would see a synthesized incomplete-handoff and the
                # `_retryable_failure` safety net would be the only
                # remaining defense. (The protocol-level path is handled
                # below in the rate_limit_event branch.) See DESIGN §6
                # *Cleanup on abnormal exit* for the auto-resume contract.
                text = b.get("text") or ""
                if (exc := detect_session_limit(text)):
                    raise exc
                # Emit every non-empty line of the assistant's text as
                # its own [<sid> text] entry, full-width (no
                # truncation). Mid-cut sentences in earlier versions
                # ate the part the user actually wanted to read. The
                # per-worker .log file has the same content; this just
                # surfaces it inline too.
                for ln in text.splitlines():
                    ln = ln.strip()
                    if ln:
                        lines.append(f"  [{sid} text] {ln}")
            elif bt == "tool_use":
                tool_summary = _summarize_tool_use(sid, b, verbosity)
                if tool_summary is not None:
                    lines.append(tool_summary)
        return "\n".join(lines) if lines else None

    if t == "user":
        msg = event.get("message", {})
        for b in msg.get("content", []) or []:
            if b.get("type") != "tool_result":
                continue
            content_txt = _extract_tool_result_text(b).strip()
            if b.get("is_error"):
                # No truncation: a schema-validation failure or other
                # tool error names exactly the missing fields / the
                # rejection reason — the diagnostic information a user
                # needs to act. Mid-cut error messages drop the useful
                # detail. Multi-line errors (rare but possible) get the
                # `tool-fail` tag on line 1 and a width-matched
                # continuation prefix (keeping the sid) on lines 2+
                # so attribution survives parallel runs without
                # repeating the kind token on every line — see
                # _tag_each_line.
                #
                # When the failure carries an EAGAIN/fork signature, name
                # the real cause inline so a human reading the stream isn't
                # misled by a bare "Exit code 1" (DESIGN §6 *Detecting PID
                # exhaustion*). The authoritative kill decision is made by
                # `_read_stream`'s cgroup probe; this only relabels.
                if _is_fork_exhaustion(content_txt):
                    return _tag_each_line(
                        f"  [{sid} tool-fail: PID cap reached — worker "
                        f"subtree cannot fork]", content_txt)
                return _tag_each_line(f"  [{sid} tool-fail]", content_txt)
            # Successful tool results are file-only at stream; debug
            # gets the FULL content. The user opting into debug is
            # explicitly asking for raw worker output; truncating
            # defeats the level. A worker reading a large file will
            # flood the orchestrator log at debug — that's the
            # accepted trade-off. Multi-line content (a Read of a
            # source file, a Grep of code) gets the `tool-ok` tag
            # on line 1 and a width-matched continuation prefix
            # (keeping the sid) on lines 2+ so attribution survives
            # parallel runs without repeating the kind token on
            # every line.
            if verbosity == "debug":
                return _tag_each_line(f"  [{sid} tool-ok]", content_txt)
        return None

    if t == "rate_limit_event":
        info = event.get("rate_limit_info", {}) or {}
        # The actual Claude Code stream-json schema (verified from
        # captured worker logs 2026-05-27): the payload carries
        # `status` (observed values: "allowed", "allowed_warning"),
        # `resetsAt` (Unix timestamp seconds), `rateLimitType`,
        # `utilization` (float 0..1, present on warning events),
        # `surpassedThreshold` (the *threshold value crossed*, e.g.
        # 0.9 — NOT a boolean flag), `overageStatus`,
        # `overageDisabledReason`, `isUsingOverage`. The terminal
        # status value (when the limit is actually hit) is
        # Anthropic-internal and unobserved by us; we treat any
        # status not in the known-allowed set as terminal —
        # defensive against future status strings ("exceeded",
        # "denied", "blocked", etc.) without hardcoding a guess.
        status = info.get("status")
        if status is not None and status not in _RATE_LIMIT_ALLOWED_STATUSES:
            reset_at: datetime | None = None
            resets_at_ts = info.get("resetsAt")
            rate_limit_type = info.get("rateLimitType", "?")
            if isinstance(resets_at_ts, (int, float)):
                try:
                    reset_at = datetime.fromtimestamp(
                        resets_at_ts, tz=timezone.utc)
                except (OSError, ValueError, OverflowError):
                    reset_at = None
            raw = (f"rate_limit_event status={status} "
                   f"rateLimitType={rate_limit_type} "
                   f"resetsAt={resets_at_ts}")
            raise RateLimitedExit(reset_at=reset_at, raw_message=raw)
        # Surface threshold-crossings at stream; everything at debug.
        # The boolean-or-numeric `surpassedThreshold` field is a
        # threshold value (e.g. 0.9), not a boolean flag — truthy when
        # present and non-zero, which is the right "this is a
        # threshold-crossing event" signal for surfacing the warning.
        if info.get("surpassedThreshold") or verbosity == "debug":
            util_frac = float(info.get("utilization") or 0)
            util = int(util_frac * 100)
            return f"  [{sid}] rate-limit {status or '?'} (util={util}%)"
        return None

    if t == "result":
        n = event.get("num_turns", "?")
        if sub == "success":
            cost = float(event.get("total_cost_usd") or 0)
            return f"  [{sid}] done (turns={n}, cost=${cost:.4f})"
        return f"  [{sid}] failed ({sub}, turns={n})"

    # Unknown event type — surface only at debug; otherwise drop.
    if verbosity == "debug":
        return f"  [{sid} ?] {t}/{sub}"
    return None


def _format_progress_prefix(
        prog: tuple[int, int, int, int, int] | None) -> str:
    """Render the activity prefix from a `_get_progress` tuple, or "" when
    progress is None (pre-Phase-5 workers).

    Each non-zero counter becomes its own ` · `-separated segment; zero
    counters are omitted so the prefix never reads `0 subtasks anything`.
    `done` is always last so the eye follows rising progress on the right.
    See IMPLEMENTATION.md §Verbosity & log prefix for the rendering rules."""
    if prog is None:
        return ""
    running, in_conformer, done, wave_idx, wave_total = prog

    def plural(n: int) -> str:
        return "subtask" if n == 1 else "subtasks"

    segs = [f"wave {wave_idx} of {wave_total}"]
    if running:
        segs.append(f"running {running} {plural(running)}")
    if in_conformer:
        segs.append(f"{in_conformer} {plural(in_conformer)} in conformer")
    if done:
        segs.append(f"{done} {plural(done)} done")
    return f"[{' · '.join(segs)}] "


def _get_progress(st: "State") -> tuple[int, int, int, int, int] | None:
    """Return (running, in_conformer, done, wave_idx, wave_total) for the
    inline activity prefix (see IMPLEMENTATION.md §Verbosity & log prefix).

    Only meaningful once waves are scheduled — returns None before that so
    classifier/planner workers emit no prefix. Counts are restricted to the
    *current* wave's membership (`waves[completed_waves]`) so that "running 5
    subtasks" reads as "5 of this wave's implementers are in flight," not
    "5 of all subtasks across all waves." The wave index is 1-based for
    display (`completed_waves + 1`) so that the in-flight wave reads as
    `1 of 3` while wave 1 is running, not `0 of 3`.

    A subtask is:
      - running:      `subtask_status[sid]` absent or not in _TERMINAL_STATUSES
      - in_conformer: status == "complete" but `conformance[sid]` absent
                      (settle_subtask writes that key exactly when the
                      advisory conformer phase finishes, so absence is a
                      precise live signal — DESIGN §9)
      - done:         status in _TERMINAL_STATUSES, and (if status ==
                      "complete") `conformance[sid]` is present.
                      `failed` / `blocked` are terminal regardless of
                      conformance — the conformer only runs on the success
                      path."""
    waves = st.data.get("waves")
    if not waves:
        return None
    completed = st.data.get("completed_waves", 0)
    if completed >= len(waves):
        return None
    wave = waves[completed]
    if not wave:
        return None
    statuses = st.data.get("subtask_status", {})
    conformance = st.data.get("conformance", {})
    running = in_conformer = done = 0
    for sid in wave:
        status = statuses.get(sid)
        if status not in _TERMINAL_STATUSES:
            running += 1
        elif status == "complete" and sid not in conformance:
            in_conformer += 1
        else:
            done += 1
    return running, in_conformer, done, completed + 1, len(waves)


# --- cgroup containment for worker subtrees (via the cgroup broker) ------
# Each `claude -p` worker (and every descendant it forks: bash children,
# vitest pools, webpack workers, tsc, etc.) is enrolled in its own child
# cgroup `leerie-w-<sid>`. The cgroup's memory.max and pids.max bound how
# much RAM / how many PIDs the worker subtree may consume. When it exceeds
# memory.max the kernel OOM-kills inside that cgroup; when it exceeds
# pids.max further fork/clone in the subtree gets EAGAIN — sshd / pid 1 /
# sibling workers are unaffected. This is the fix for the cascade in
# DESIGN §6 Worker subtree termination, AND for the thread/PID-table
# exhaustion crash (Bun EAGAIN) that motivated the broker.
#
# THE BROKER, AND WHY (reproduced live — see DESIGN §6). Cgroup enforcement
# must be done by an identity that owns (or was delegated) the relevant
# subtree, not by the dropped-privilege orchestrator (non-root leerie
# rootful; a nested-userns-remapped identity rootless):
#   1. Migrating a task into a cgroup needs write on `cgroup.procs` of the
#      destination, the source, AND their common ancestor. Workers are born
#      in the container-runtime-owned scope; moving them into `leerie.slice`
#      crosses a cgroup the leerie user doesn't own → EACCES/EIO.
#   2. In the rootful case, even inside a delegated subtree the kernel keeps
#      controller limit files (`pids.max`, `memory.max`) owned by root when
#      the subtree was merely chowned rather than created by the delegatee —
#      so the leerie user can organize processes but not set limits.
# So `scripts/cgroup-broker.py` runs at that owning identity — real root
# rootful, the rootlesskit-mapped host UID rootless (which owns the
# systemd-delegated user slice; DESIGN §6 *Rootless exception*) — launched by
# container-entry.sh at PID 1 before the privilege drop, and performs
# create/enroll/destroy on the orchestrator's behalf over a Unix socket. It
# handles cgroup v1
# (Fly Firecracker VMs are v1/hybrid) vs v2 (Colima) transparently. The
# functions below are thin socket clients; the public names/signatures are
# unchanged so `_invoke`'s call sites are untouched, except `_cgroup_create`
# now returns the sid (str) rather than a filesystem Path.

_CGROUP_BROKER_SOCK = "/run/leerie-cgroup.sock"
_CGROUP_PROBE_RESULT: bool | None = None
_CGROUP_HIERARCHY: str | None = None  # "v2" / "v1" — set by a passing probe


def _cgroup_request(payload: str, timeout: float = 5.0) -> str:
    """Send one request to the cgroup broker and return its response line
    (without trailing newline). Raises OSError if the broker is
    unreachable or the round-trip fails."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(_CGROUP_BROKER_SOCK)
        s.sendall((payload + "\n").encode())
        return s.recv(4096).decode(errors="replace").strip()


def _cgroup_probe() -> bool:
    """Once-per-run probe, memoized in `_CGROUP_PROBE_RESULT`: does the
    cgroup broker exist AND can it round-trip a create+enroll+destroy of a
    throwaway cgroup? This is the true test of the path workers use — the
    old direct-write probe passed on hosts where non-root enrollment
    actually fails (the bug this replaced). A passing probe records the
    hierarchy (`v2`/`v1`) in `_CGROUP_HIERARCHY` for telemetry."""
    global _CGROUP_PROBE_RESULT, _CGROUP_HIERARCHY
    if _CGROUP_PROBE_RESULT is not None:
        return _CGROUP_PROBE_RESULT
    try:
        resp = _cgroup_request("probe")
    except OSError as e:
        log(f"  cgroup broker unreachable at {_CGROUP_BROKER_SOCK} "
            f"({e.strerror or e}); worker containment is OFF for this "
            f"run. Check that scripts/container-entry.sh launched "
            f"scripts/cgroup-broker.py at PID 1.")
        _CGROUP_PROBE_RESULT = False
        return False
    if resp.startswith("OK"):
        parts = resp.split()
        _CGROUP_HIERARCHY = parts[1] if len(parts) > 1 else "unknown"
        _CGROUP_PROBE_RESULT = True
        return True
    log(f"  cgroup broker probe failed ({resp}); worker containment is "
        f"OFF for this run. The broker found no usable cgroup hierarchy "
        f"(no cgroup-v2 unified mount, no v1 pids+memory controller "
        f"mounts, or a read-only cgroupfs).")
    _CGROUP_PROBE_RESULT = False
    return False


def _cgroup_create(sid: str, memory_max_bytes: int,
                   pids_max: int) -> str | None:
    """Ask the broker to create a worker cgroup and set its caps. Returns
    the sid on success (the handle passed to `_cgroup_enroll`/
    `_cgroup_destroy`), None on any failure. Idempotent — re-spawning a
    worker with the same sid re-writes the caps."""
    if not _cgroup_probe():
        return None
    try:
        resp = _cgroup_request(
            f"create {sid} {memory_max_bytes} {pids_max}")
    except OSError as e:
        log(f"  [{sid}] cgroup create failed ({e.strerror or e}); "
            f"worker runs uncapped")
        return None
    if resp == "OK":
        return sid
    log(f"  [{sid}] cgroup create rejected by broker ({resp}); "
        f"worker runs uncapped")
    return None


def _cgroup_enroll(sid: str, pid: int) -> bool:
    """Ask the broker to migrate `pid` into the worker cgroup. Called
    immediately after the worker subprocess spawns. Returns True on
    success. Failure logs but does not abort the worker — it simply runs
    in the parent cgroup (uncapped)."""
    try:
        resp = _cgroup_request(f"enroll {sid} {pid}")
    except OSError as e:
        log(f"  cgroup enroll failed for pid={pid}: {e.strerror or e}")
        return False
    if resp == "OK":
        return True
    log(f"  cgroup enroll rejected by broker for pid={pid}: {resp}")
    return False


def _cgroup_destroy(sid: str | None) -> None:
    """Ask the broker to tear down the worker cgroup (kill any survivors,
    rmdir). Best-effort — broker/socket errors are swallowed. Called from
    `_invoke`'s cleanup path on every exit (success, timeout, abort)."""
    if sid is None:
        return
    with contextlib.suppress(OSError):
        _cgroup_request(f"destroy {sid}")


def _cgroup_stat(sid: str | None) -> tuple[int, int, int] | None:
    """Read-only probe of a worker cgroup's PID counters via the broker's
    `stat` verb. Returns `(pids.current, pids.max, pids.events.max)`, or
    None when the sid is None, containment is off (no broker / uncapped
    run), or the broker errors — callers must treat None as "cannot tell"
    and NOT infer exhaustion. `pids.max` is -1 when the cgroup is
    uncapped/unlimited; `pids.events.max` counts kernel fork denials (the
    broker reports 0 on v1 — where it is not read — so v1 detection falls
    back to current >= max).
    Used by `_read_stream` to distinguish a PID-exhausted worker (whose
    every Bash call fails with EAGAIN) from a worker whose commands merely
    fail (DESIGN §6 *Detecting PID exhaustion*)."""
    if sid is None:
        return None
    try:
        resp = _cgroup_request(f"stat {sid}")
    except OSError:
        return None
    parts = resp.split()
    if len(parts) == 4 and parts[0] == "OK":
        try:
            return (int(parts[1]), int(parts[2]), int(parts[3]))
        except ValueError:
            return None
    return None


def enforce_and_record_cgroup_containment(st: "State",
                                          allow_uncapped: bool) -> None:
    """Fail-closed containment gate + state recording, run once per run
    just before the first worker spawns (DESIGN §6 *Memory containment*).

    Probes the cgroup broker end-to-end (create+enroll+destroy round-trip),
    records `{enforced, hierarchy}` into `st.data` (merges — `st.data` is
    already loaded/seeded by the time this runs), then `die()`s if
    containment can't be enabled — unless the operator explicitly waived
    it with `--dangerously-allow-uncapped`, which downgrades to a loud
    warning. A silently-uncapped run is what let a runaway conformer
    subtree exhaust the VM thread table (the Bun EAGAIN crash), and the
    direct-write probe this replaced gave false positives on exactly the
    hosts where non-root enrollment fails.

    **Why here and not in `main()`:** this must run only on paths that
    actually spawn workers. `_run_phases`' resume branch short-circuits
    (returns) before this on already-completed / no-work runs — those
    spawn zero workers (the "host launcher pushes + opens the PR" flow),
    so gating them would `die()` spuriously on a containment-incapable
    host. Called at the two worker-guaranteed points (fresh + resumable
    resume) after `st.data` is populated and before the first worker
    (`phase_classify` on a fresh run, `phase_execute` on resume).

    Recording the outcome is deliberate: the crash that motivated the
    broker left NO artifact of the silent containment failure — persisting
    it in state.json makes it visible for post-mortems."""
    enforced = _cgroup_probe()
    st.data["cgroup_containment"] = {
        "enforced": enforced,
        "hierarchy": _CGROUP_HIERARCHY,
    }
    st.save()
    if enforced:
        return
    if allow_uncapped:
        log("  ⚠  WARNING: worker cgroup containment is OFF "
            "(--dangerously-allow-uncapped). Workers run without "
            "memory/PID limits; a runaway subtree can exhaust the VM "
            "thread/PID table. See DESIGN §6 Memory containment.")
        return
    die("worker cgroup containment could not be enabled — workers would "
        "run uncapped and a runaway subtree can exhaust the VM thread/PID "
        "table (the Bun EAGAIN crash). The cgroup broker "
        "(scripts/cgroup-broker.py, launched by container-entry.sh at "
        "PID 1) is down, or this host has no usable cgroup hierarchy / "
        "read-only cgroupfs, or — rootless — a host whose systemd doesn't "
        "delegate pids+memory into the per-session user slice (non-systemd "
        "init, or an older/overridden delegation config). See "
        "docs/INSTALL.md (cgroup delegation). "
        "To run anyway without containment, pass --dangerously-allow-uncapped "
        f"(or set {DANGEROUS_ALLOW_UNCAPPED_ENV}=1).")


async def _invoke(cmd: list[str], cwd: str, timeout: int,
                  sid: str, leerie_dir: Path, verbosity: str,
                  progress: Callable[[], tuple[int, int, int, int] | None]
                  | None = None,
                  idle_warn_sec: float | None = None,
                  worker_memory_max_bytes: int | None = None,
                  worker_pids_max: int | None = None) -> dict:
    """Run a `claude -p` command, streaming events as they arrive.

    The CLI is invoked with `--output-format stream-json --verbose`; each
    line of stdout is one JSON event. The final `type: "result"` event
    is the envelope (same shape as the non-streaming `--output-format
    json` path produces). All events are appended to
    `<state-root>/logs/<sid>.log` regardless of verbosity. Inline summaries
    surface to the orchestrator log according to `verbosity` (see
    `_summarize_stream_event`).

    `cmd` must already contain `--output-format stream-json --verbose`
    — `claude_p` adds those.

    Errors / cancellation follow `run_proc`'s contract: timeout raises
    `subprocess.TimeoutExpired`, cancellation kills the child and
    re-raises. A worker that exits without emitting any `result` event
    raises `WorkerError` — same error class callers already handle."""
    log_path = leerie_dir / "logs" / f"{sid}.log"
    # `limit=10MB` overrides asyncio's StreamReader 64KB-per-line default.
    # A single `claude -p` event can plausibly exceed 64KB: the
    # implementer's `structured_output` tool_use carries the full
    # worker payload, and a long assistant text block is one event
    # too. Without this, a large event would crash `_read_stream`:
    # readline() (under the `async for proc.stdout` iterator) calls
    # readuntil() which raises LimitOverrunError, which readline()
    # wraps and re-raises as ValueError("Separator is not found,
    # and chunk exceed the limit"). Either name in `except` works —
    # but the user-visible exception is ValueError.
    # LEERIE_WORKER_DEBUG=1 injects DEBUG=* and ANTHROPIC_LOG=debug into the
    # worker's environment. The point: if a worker hangs before emitting
    # any stdout event, rerunning with this env var makes the CLI emit its
    # internal state to stderr (which the watchdog below surfaces), so an
    # otherwise-invisible silent stall becomes diagnosable without
    # redeploying leerie. The variable is opt-in because verbose CLI logging
    # is noisy on healthy runs.
    worker_env = None
    if os.environ.get("LEERIE_WORKER_DEBUG"):
        worker_env = os.environ.copy()
        worker_env["DEBUG"] = "*"
        worker_env["ANTHROPIC_LOG"] = "debug"

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        # stdin=DEVNULL: workers receive their full prompt + schema via
        # argv and never read terminal input. Without this the worker
        # inherits the orchestrator's stdin, which inside a `nerdctl run
        # -it` container is /dev/pts/0 — a real TTY. A CLI that branches
        # on isatty() (e.g. to prompt for permission) would hang
        # invisibly waiting for input that never arrives. Closing stdin
        # eliminates that whole class of hang.
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=10 * 1024 * 1024,
        # Session/PG leader so `_terminate_proc_tree` can reap the tool-call
        # grandchildren `claude -p` spawns (vitest, dev servers, etc.).
        start_new_session=True,
        env=worker_env,
    )
    # Spawn heartbeat: surfaces *that* the worker was launched before the
    # await blocks on its first event. Without this line, the user sees
    # the phase header and then silence until the first stream-json event
    # arrives (or the 90-min `worker_timeout_sec` fires) — a silent worker
    # was the failure class that motivated the watchdog below.
    #
    # Suppressed at `quiet`: per the verbosity contract, quiet emits
    # phase boundaries + errors only. The watchdog warning below IS
    # error-class and fires at every verbosity; this spawn line is
    # operational chatter and stays gated.
    if verbosity != "quiet":
        log(f"  [{sid}] spawned (pid={proc.pid})")
    # cgroup containment: ask the cgroup broker to create the worker cgroup
    # and enroll the worker (and every descendant it forks — the kernel
    # propagates cgroup membership down the process tree). These are
    # synchronous socket round-trips to the broker made directly in this
    # coroutine (not via asyncio.to_thread): they briefly block the event
    # loop, but the broker's replies are tiny/fast and bounded by
    # `_cgroup_request`'s 5 s timeout, so the stall is negligible and no
    # deadlock is possible (the broker never calls back into leerie).
    # Containment enforcement itself was already gated at run start
    # (enforce_and_record_cgroup_containment); here a per-worker failure
    # returns None / False and the worker runs without its own sub-cgroup
    # — the cgroup path NEVER aborts the worker (telemetry that crashes its
    # host is worse than no telemetry, same principle as _memory_sampler).
    cgroup_sid: str | None = None
    if (worker_memory_max_bytes is not None
            and worker_pids_max is not None):
        cgroup_sid = _cgroup_create(sid, worker_memory_max_bytes,
                                    worker_pids_max)
        if cgroup_sid is not None:
            _cgroup_enroll(cgroup_sid, proc.pid)
    # Track every descendant PID that ever appears under this worker. Claude
    # Code's Bash tool uses `run_in_background: true` to fire-and-forget
    # long-running commands (test runners, builds, dev servers); those
    # subprocesses outlive `claude -p`'s exit and are orphaned to init by the
    # time leerie could PPID-walk post-exit. The tracker observes them while
    # the chain is still intact, then SIGKILLs the accumulated set at the
    # end. See DESIGN §6 *Cleanup on abnormal exit*.
    descendant_tracker = _DescendantTracker(proc.pid, cgroup_sid)
    descendant_tracker.start()
    envelope: dict | None = None
    # Latch actual credit exhaustion from `rate_limit_event`s as they
    # stream. The discriminator is `overageDisabledReason` being an
    # *exhaustion* reason (`out_of_credits` / `out_of_overage`), NOT
    # `overageStatus:"rejected"`. That distinction is load-bearing:
    # `overageStatus:"rejected"` is a *standing config state* emitted by
    # every `rate_limit_event` from an org that has overage disabled
    # (`overageDisabledReason:"org_level_disabled"`, `status:"allowed"`)
    # — it does NOT mean credits ran out, and plenty of base subscription
    # quota may remain. Keying the latch on it caused a false positive:
    # any unrelated mid-stream truncation inherited the permanently-set
    # flag and was misreported as out-of-credits. Exhaustion is fatal
    # only when it coincides with a stream that truncates *without* a
    # result event (the CLI was killed mid-turn the moment credits ran
    # out); the no-envelope branch below consults this latch to route
    # that specific case into the pause-and-surface path instead of a
    # bare WorkerError → die().
    overage_blocked = False
    stderr_chunks: list[bytes] = []
    # Watchdog state: last_event_at is updated by _read_stream on every
    # successfully-parsed stream-json event. The _idle_watchdog coroutine
    # below observes this clock and warns when no events arrive for
    # `worker_idle_warn_sec` seconds.
    last_event_at = time.monotonic()
    # PID-exhaustion detector state (DESIGN §6 *Detecting PID exhaustion*).
    # A sliding window of recent tool-result outcomes (True=errored); the
    # cgroup is probed once the window holds ≥threshold errors. A window
    # (not a consecutive counter) is required because tool-results are never
    # adjacent in the stream — see `_tool_result_outcome`.
    tool_result_window: deque[bool] = deque(maxlen=_PID_EXHAUSTION_WINDOW)
    # Baseline pids.events.max at the first probe, so we can tell "the
    # counter is climbing" (fresh denials) from a stale nonzero value.
    pids_events_baseline: int | None = None

    async def _read_stream():
        nonlocal envelope, last_event_at, overage_blocked
        nonlocal pids_events_baseline
        # `buffering=1` is line-buffered: every newline flushes to disk.
        # Without this Python text-mode files are fully buffered when not
        # connected to a TTY, so `tail -f <state-root>/logs/<sid>.log` would
        # show nothing until the file closed at worker end — defeating
        # the entire live-progress property of the streaming feature.
        with log_path.open("a", buffering=1) as log_file:
            try:
                async for raw in proc.stdout:
                    if not raw:
                        continue
                    # Any bytes from the worker count as liveness — refresh
                    # the watchdog clock before parsing, so a stream of
                    # non-JSON lines (which are logged and skipped below)
                    # still counts as activity.
                    last_event_at = time.monotonic()
                    line = raw.decode(errors="replace").rstrip("\n")
                    # File: always record the raw event with a timestamp
                    # header. The header lets `tail -f` users see
                    # structure without parsing JSON.
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        log_file.write(
                            f"[{now()}] non-json-line\n{line}\n\n")
                        continue
                    t = event.get("type", "?")
                    sub = event.get("subtype")
                    header = f"{t}/{sub}" if sub else t
                    log_file.write(f"[{now()}] {header}\n{line}\n\n")
                    # Latch credit-exhaustion state (see `overage_blocked`
                    # declaration above for why this keys on
                    # `overageDisabledReason`, NOT `overageStatus`).
                    # Tracked here rather than in `_summarize_stream_event`
                    # because it must survive to the post-stream
                    # no-envelope check even at quiet verbosity, where the
                    # summarizer returns None.
                    if t == "rate_limit_event":
                        _rli = event.get("rate_limit_info") or {}
                        if (_rli.get("overageDisabledReason")
                                in ("out_of_credits", "out_of_overage")):
                            overage_blocked = True
                    # PID-exhaustion detection (DESIGN §6 *Detecting PID
                    # exhaustion*). A worker that has exhausted its cgroup
                    # pids.max fails EVERY shell-spawning tool call with
                    # EAGAIN — including trivial ones whose bare "Exit code
                    # 1" gives the model no way to diagnose it, so it
                    # spirals to the end of the run. Track the last N
                    # tool-result outcomes in a sliding window; once it
                    # holds ≥threshold errors, probe the cgroup. If it
                    # confirms exhaustion (at cap, or the fork-denial counter
                    # is climbing), raise WorkerError — _invoke's `except
                    # BaseException` terminates the subtree + reaps, and the
                    # callers route it (implementer → retryable handoff;
                    # conformer → advisory). A window, not a consecutive
                    # counter: tool-results are never adjacent in the stream
                    # (the model's assistant turn always sits between them),
                    # so a consecutive count could never exceed one. The
                    # window still leaves an ordinary failing test (≤1 error)
                    # below the threshold — and the cgroup read is the final
                    # authority regardless.
                    _outcome = _tool_result_outcome(event)
                    if _outcome is not None:
                        tool_result_window.append(_outcome)
                        # Probe only when THIS result errored (and the
                        # window is error-heavy): the probe is a synchronous
                        # broker round-trip, and gating on the error avoids
                        # re-probing on the interleaved successes of a
                        # healthy-but-failing worker. An exhausted worker —
                        # whose every call errors — still probes on its
                        # first qualifying error, so detection is unchanged.
                        if (_outcome is True
                                and sum(tool_result_window)
                                >= _PID_EXHAUSTION_ERROR_THRESHOLD):
                            stat = _cgroup_stat(cgroup_sid)
                            if stat is not None:
                                cur, mx, ev_max = stat
                                if pids_events_baseline is None:
                                    pids_events_baseline = ev_max
                                at_cap = mx > 0 and cur >= mx
                                denials_climbing = (
                                    ev_max > pids_events_baseline)
                                # Fast-path text confirmation strengthens
                                # the signal but is not required.
                                if at_cap or denials_climbing:
                                    log(f"  [{sid}] PID cap reached: cgroup "
                                        f"pids.current={cur}/{mx}, fork "
                                        f"denials={ev_max} — the worker "
                                        f"leaked background processes "
                                        f"(likely run_in_background test / "
                                        f"build / dev-server subprocesses) "
                                        f"until it could no longer fork a "
                                        f"shell; every Bash call now fails. "
                                        f"Terminating early so a fresh worker "
                                        f"retries with a clean PID table.")
                                    raise WorkerError(
                                        f"worker {sid} exhausted its PID "
                                        f"cgroup (pids.current={cur}/{mx}, "
                                        f"fork denials={ev_max}); every "
                                        f"shell-spawning tool call fails "
                                        f"with EAGAIN")
                    # No else-reset: successes age out of the bounded window
                    # naturally as newer outcomes push them past maxlen.
                    # Inline summary (verbosity-gated). Multi-line
                    # summaries (multi-block events, multi-line text)
                    # are emitted one log() call per line so each
                    # line gets its own ISO-8601 [leerie] prefix —
                    # otherwise the timestamp only renders on line 1
                    # and lines 2+ visually disconnect from the
                    # orchestrator's timestamped log stream.
                    summary = _summarize_stream_event(sid, event, verbosity)
                    if summary:
                        # Recompute per event so siblings finishing
                        # mid-run bump the count for still-running
                        # workers. Cached for this event's splitlines
                        # loop so a multi-line summary doesn't
                        # straddle two different counts.
                        prog = progress() if progress else None
                        prog_prefix = _format_progress_prefix(prog)
                        for ln in summary.splitlines():
                            if ln:
                                log(prog_prefix + ln)
                    # Capture the final result envelope. Skip
                    # `task-notification` result events: claude -p keeps
                    # the session alive after the worker's real result if
                    # a Bash run_in_background:true subprocess is still
                    # pending, and emits a second `result` event when the
                    # background task finishes. That event has
                    # `origin: {"kind": "task-notification"}` and no
                    # `structured_output`; capturing it overwrites the
                    # genuine envelope and cascades into a fake
                    # `empty_handoff` invariant violation downstream
                    # (envelope.get("structured_output") would be None →
                    # schema-retry → WorkerError → synthesized
                    # incomplete-handoff with a checkpoint_path that does
                    # not exist on disk).
                    if t == "result":
                        if (event.get("origin") or {}).get("kind") \
                                == "task-notification":
                            continue
                        envelope = event
            except ValueError as e:
                # asyncio's StreamReader raises ValueError("Separator
                # is not found, and chunk exceed the limit") when a
                # single line exceeds the 10 MiB limit (see
                # create_subprocess_exec above). Without this catch
                # the ValueError would propagate through claude_p's
                # retry loop unhandled and surface as a Python
                # traceback. Convert to WorkerError so callers see a
                # leerie-shaped error and the retry path treats it
                # as a worker fault.
                raise WorkerError(
                    "claude -p emitted a line exceeding the 10 MiB "
                    "buffer limit — likely a runaway structured_output "
                    f"or text block: {e}") from e

    async def _drain_stderr():
        # Stream stderr live to the per-sid log file with a `[ts] stderr`
        # header so it's distinguishable from stream-json events, and
        # echo selectively to the orchestrator log at stream/debug
        # verbosity. Continues to buffer raw bytes into stderr_chunks
        # so the existing exit-time error path (line ~4195) and the
        # idle-watchdog stderr-tail flush still work unchanged.
        #
        # Solves the failure class where a worker emits a fatal message
        # to stderr (e.g. "Claude configuration file not found" from
        # the claude-code recovery-loop bug) but leerie doesn't surface
        # it until the 300s watchdog fires (or never, if the recovery
        # loop spins indefinitely with no exit).
        nonlocal last_event_at  # stderr activity counts as liveness
        with log_path.open("a", buffering=1) as log_file:
            try:
                async for raw in proc.stderr:
                    if not raw:
                        continue
                    last_event_at = time.monotonic()
                    stderr_chunks.append(raw)
                    line = raw.decode(errors="replace").rstrip("\n")
                    log_file.write(f"[{now()}] stderr\n{line}\n\n")
                    if verbosity in ("stream", "debug"):
                        log(f"  [{sid}] stderr: {line}")
            except ValueError as e:
                # Mirror _read_stream's overlong-line protection
                # (line ~4082): asyncio's StreamReader raises
                # ValueError when a single line exceeds the 10 MiB
                # buffer limit. Convert to WorkerError so callers see
                # a leerie-shaped error consistent with the stdout path.
                raise WorkerError(
                    "claude -p stderr emitted a line exceeding the "
                    f"10 MiB buffer limit: {e}") from e

    async def _idle_watchdog():
        # Observation-only stall detector. Wakes every `warn_sec` seconds
        # and warns if the worker has emitted no stdout bytes for that
        # long. Never kills the worker — the 90-min `worker_timeout_sec`
        # remains the only kill. Surfaces the silent-hang failure class
        # that motivated this watchdog: a `claude -p` worker that gets
        # stuck before its first `system/init` event would otherwise
        # leave the user with zero feedback for up to 90 minutes.
        #
        # When the worker exits normally (success or timeout), the
        # surrounding try/finally cancels this task; CancelledError is
        # suppressed by the awaiting caller.
        # `idle_warn_sec` carries the resolved per-run cap from
        # `claude_p` (which built `caps = dict(DEFAULT_CAPS)` and then
        # honored any CLI / env / TOML override). Direct `_invoke`
        # callers — preflight smoke-test, replay paths, tests — don't
        # plumb caps and pass `None`; we fall back to `DEFAULT_CAPS`
        # so the watchdog still functions for them without forcing
        # every call site to thread the full caps dict.
        warn_sec = (idle_warn_sec if idle_warn_sec is not None
                    else DEFAULT_CAPS["worker_idle_warn_sec"])
        while True:
            await asyncio.sleep(warn_sec)
            gap = time.monotonic() - last_event_at
            # Compare in floats — truncating to int here would make a
            # sub-second `warn_sec` (e.g. in tests) compare against 0
            # and never warn.
            if gap < warn_sec:
                continue
            # Stderr tail: if the CLI is logging to stderr (e.g. when
            # LEERIE_WORKER_DEBUG=1), surface the most recent bytes
            # alongside the silence warning so the user has something
            # actionable. Truncated to the last 400 chars to keep the
            # orchestrator log readable.
            tail = b"".join(stderr_chunks[-10:]).decode(
                errors="replace").strip()
            tail_note = (f" — stderr tail: {tail[-400:]!r}"
                         if tail else "")
            log(f"  [{sid}] no stdout events in {int(gap)}s "
                f"(pid={proc.pid}, hard kill at "
                f"{timeout}s){tail_note}")

    watchdog_task = asyncio.create_task(_idle_watchdog())
    try:
        # Mark this worker as asyncio-managed so `_zombie_reaper` never
        # `waitpid`s it and steals its exit status from asyncio's child watcher.
        # Placed as the FIRST statement of this try so the `finally` discard
        # below genuinely covers it on every exit path (DESIGN §6 *Zombie
        # reaping*).
        _ASYNCIO_MANAGED_PIDS.add(proc.pid)
        try:
            await asyncio.wait_for(
                asyncio.gather(_read_stream(), _drain_stderr(),
                               proc.wait()),
                timeout=timeout)
        except asyncio.TimeoutError:
            # Cancel the watchdog BEFORE the termination awaits so it
            # cannot wake during them and log a stale "no stdout events"
            # warning against a worker that's already being killed. The
            # `finally:` below still collects the task; cancel() is
            # idempotent.
            watchdog_task.cancel()
            await _terminate_proc_tree(proc)
            await descendant_tracker.stop_and_reap()
            raise subprocess.TimeoutExpired(cmd, timeout)
        except BaseException:
            # Same race-closing cancel as above. Then terminate the
            # worker's whole subtree (claude -p + its tool-call
            # grandchildren via PPID walk) and reap any backgrounded
            # subprocesses the tracker observed during the run. Then
            # re-raise. Leerie's gather_or_cancel relies on this for clean
            # aborts.
            watchdog_task.cancel()
            await _terminate_proc_tree(proc)
            await descendant_tracker.stop_and_reap()
            raise
    finally:
        # Un-register the worker PID: asyncio has now finished awaiting it
        # (proc.wait() resolved inside the gather, or we terminated it), so the
        # zombie reaper may safely reap it if it lingers as a <defunct>.
        _ASYNCIO_MANAGED_PIDS.discard(proc.pid)
        # The watchdog runs for the whole worker lifetime and must be
        # cancelled on every exit path (success, timeout, abort) so it
        # doesn't outlive the worker and fire spuriously against a stale
        # `last_event_at`. The contextlib.suppress is the standard
        # asyncio pattern for awaiting a cancelled task without
        # propagating the CancelledError.
        watchdog_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog_task
        # cgroup teardown via the broker. The broker's destroy atomically
        # reaps any worker-tree process that survived _terminate_proc_tree /
        # descendant_tracker.stop_and_reap above (cgroup.kill on v2, move-to-
        # parent on v1) — a backstop for the backgrounded grandchild class —
        # then rmdirs the cgroup so we don't accumulate leerie-w-* entries
        # across a long-running orchestrator. Best-effort: socket errors are
        # swallowed inside _cgroup_destroy.
        _cgroup_destroy(cgroup_sid)
    # Success path: reap any backgrounded subprocesses the worker left
    # behind. `claude -p` workers use Claude Code's Bash tool with
    # `run_in_background: true` for long-running tasks (test runners,
    # builds, dev servers — DESIGN §6). Those subprocesses
    # are spawned in detached POSIX sessions, exit-reparent to PID 1, and
    # would otherwise outlive `claude -p`'s clean exit. The tracker has
    # accumulated their PIDs throughout the worker's life; stop_and_reap
    # SIGKILLs the union.
    leaked = await descendant_tracker.stop_and_reap()
    if leaked:
        log(f"  [{sid}] reaped {leaked} backgrounded subprocess(es) "
            f"that survived `claude -p` exit")

    if envelope is None:
        stderr_txt = b"".join(stderr_chunks).decode(errors="replace").strip()
        # Out-of-credits mid-stream kill: an exhaustion `rate_limit_event`
        # was seen (overage_blocked) and the CLI was terminated before
        # emitting a result event. This is a credit condition, not a worker
        # fault — raise RateLimitedExit(out_of_credits=True) so main()'s
        # handler pauses-and-surfaces (worktree cleanup + --resume hint +
        # EXIT_LOCKED) rather than the auto-resume path: out-of-credits has
        # no reset clock, so looping a fixed backoff would spin against the
        # wall. A bare WorkerError would instead bypass the auth/quota
        # backoff (it needs an envelope to classify) and die() the run
        # non-resumably. Checked before the returncode arm because the kill
        # may exit nonzero.
        if overage_blocked:
            raise RateLimitedExit(
                reset_at=None,
                out_of_credits=True,
                raw_message=(
                    f"out of credits — claude -p ({sid}) terminated "
                    "mid-stream with no result event"))
        if proc.returncode and proc.returncode != 0:
            raise WorkerError(
                f"claude -p exited {proc.returncode}: "
                f"{stderr_txt or '(no stderr)'}")
        raise WorkerError(
            "claude -p produced no result event "
            f"(stderr: {stderr_txt or '(empty)'})")
    return envelope


def _capture_call(run_dir: Path, record: dict) -> None:
    """Append one NDJSON record to calls.ndjson with fsync-per-line durability.

    fsync ensures a hard-killed run leaves a clean, fully-written last line
    rather than a partial line that would break NDJSON parsers."""
    capture_path = run_dir / "calls.ndjson"
    line = json.dumps(record, separators=(",", ":")) + "\n"
    with capture_path.open("a") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def _collect_memory_sample(st: "State") -> dict:
    """Snapshot orchestrator RSS / current phase / worker count / open FDs /
    thread count. Stdlib only (no `psutil` dependency).

    The four axes give enough signal to distinguish "natural heavy run" from
    leak shape:
      - rss_kb grows linearly with no GC drops → RSS leak, escalate to
        `tracemalloc`.
      - open_fds grows with no decline → subprocess pipe / log handle leak,
        audit `_invoke`'s cleanup paths.
      - thread_count grows → leaked `_DescendantTracker` background threads
        not being joined.
      - phase / worker_count contextualize the other axes.

    `/proc/self/fd` is the canonical Linux FD source; leerie's orchestrator
    runs as PID 1 inside the container (Linux), so the proc-fs path is
    valid. ru_maxrss is in KB on Linux (in bytes on macOS, but the
    orchestrator never runs on bare macOS — the launcher does, and we
    don't sample it). All probes are individually exception-guarded so a
    container without /proc still produces a partial sample rather than
    crashing the orchestrator over telemetry."""
    import resource
    import threading
    rss_kb = 0
    try:
        ru = resource.getrusage(resource.RUSAGE_SELF)
        rss_kb = int(ru.ru_maxrss)
    except Exception:
        pass

    open_fds = -1
    try:
        open_fds = len(os.listdir("/proc/self/fd"))
    except Exception:
        pass

    thread_count = -1
    try:
        thread_count = threading.active_count()
    except Exception:
        pass

    return {
        "ts": now(),
        "rss_kb": rss_kb,
        "phase": st.data.get("current_phase", "<unknown>"),
        "worker_count": st.data.get("worker_count", 0),
        "open_fds": open_fds,
        "thread_count": thread_count,
    }


# Linux prctl option numbers (from <linux/prctl.h>). Set: make this process a
# "child subreaper" so orphaned descendants reparent to it instead of climbing
# to PID 1. Get: read the current flag back (used only by tests to verify Set).
_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37

# PIDs of the `claude -p` workers the orchestrator spawns via
# `asyncio.create_subprocess_exec` and is actively awaiting (`proc.wait()` inside
# a gather). asyncio's own child watcher owns these PIDs' exit statuses; the
# zombie reaper MUST NOT `waitpid` them or it would steal the status out from
# under asyncio (which then reports returncode 255 and logs a spurious warning).
# `_invoke` registers a worker here at spawn and discards it after the gather.
# This is LOAD-BEARING, not just belt-and-suspenders: an asyncio child that has
# exited but not yet been watcher-reaped is briefly a `state==Z, ppid==getpid()`
# zombie — indistinguishable by the reaper's /proc filter from a true orphan.
# The registration set is what tells them apart, so the reaper must consult it.
_ASYNCIO_MANAGED_PIDS: set[int] = set()


def _become_subreaper() -> bool:
    """Install this process as a child-subreaper so orphaned descendants
    reparent to it (DESIGN §6 *Zombie reaping*). Returns True on success.

    Why this is load-bearing: the leerie container's PID 1 is `runuser`/the
    entrypoint (or an idle `sleep infinity` on Fly), NOT a real init — it never
    `wait()`s arbitrary orphans. A worker's tool subtree routinely orphans
    short-lived subprocesses (git, ssh-agent, and their children); without a
    subreaper those reparent to PID 1 and rot as zombies, each still counting
    against the worker cgroup's `pids.max` until it fills and every `fork()`
    EAGAINs. Claiming the subreaper role routes those orphans to the
    orchestrator, where `_zombie_reaper` reaps them.

    Linux-only (`prctl` is a Linux syscall). On any other platform, or if the
    libc call fails, this is a logged no-op — the orchestrator only runs for
    real inside the Linux container, so a dev-machine no-op is harmless.
    Called once, early in `main()`, before any worker is spawned so every
    descendant inherits the reparent-to-us behavior."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        rc = libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
        if rc != 0:
            err = ctypes.get_errno()
            log(f"subreaper: prctl(PR_SET_CHILD_SUBREAPER) failed (errno "
                f"{err}); orphaned worker subprocesses may accumulate as "
                f"zombies against the cgroup PID cap")
            return False
        return True
    except OSError as e:
        log(f"subreaper: could not install child-subreaper ({e}); orphaned "
            f"worker subprocesses may accumulate as zombies")
        return False


def _orphan_zombie_children() -> list[int]:
    """Return PIDs of processes that are (a) zombies (`<defunct>`, state `Z`),
    (b) direct children of this process (`PPid == os.getpid()`), and (c) NOT an
    asyncio-managed worker. These are the orphaned descendants that reparented
    to us after `_become_subreaper` and need `wait()`ing.

    Reads `/proc/<pid>/stat` — field 3 is the state char, field 4 is PPid. The
    comm field (field 2, parenthesized) can contain spaces/parens, so parse
    from the LAST `)` to avoid mis-splitting. Non-Linux / no-`/proc` → empty
    list (the reaper is a no-op there, matching `_become_subreaper`)."""
    try:
        pids = [int(e) for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return []
    me = os.getpid()
    out: list[int] = []
    for pid in pids:
        if pid in _ASYNCIO_MANAGED_PIDS:
            continue  # asyncio owns this worker's exit status — never touch it
        try:
            with open(f"/proc/{pid}/stat") as f:
                data = f.read()
        except OSError:
            continue  # exited between listdir and open, or not readable
        rparen = data.rfind(")")
        if rparen == -1:
            continue
        fields = data[rparen + 2:].split()  # skip ") " → state is fields[0]
        if len(fields) < 2:
            continue
        state, ppid_s = fields[0], fields[1]
        try:
            ppid = int(ppid_s)
        except ValueError:
            continue
        if state == "Z" and ppid == me:
            out.append(pid)
    return out


async def _zombie_reaper(interval_sec: float = 1.0) -> None:
    """Periodically `wait()` orphaned descendants that reparented to this
    process (after `_become_subreaper`), clearing them before they pile up as
    zombies against the worker cgroup's `pids.max` (DESIGN §6 *Zombie reaping*).

    Complements `_DescendantTracker`: the tracker SIGKILLs *live* leaked PIDs
    to relieve pressure, but a SIGKILLed orphan becomes a `<defunct>` zombie
    that still occupies a task slot until someone `wait()`s it — that someone
    is this loop. Distinct concerns: the tracker bounds live processes, this
    reaps dead ones.

    Targeted, NOT `waitpid(-1)`: a blanket `waitpid(-1)` would race asyncio's
    own child watcher and steal the exit status of a live `claude -p` worker
    (asyncio then reports returncode 255 and logs a spurious warning). Instead
    we `waitpid` only the specific PIDs `_orphan_zombie_children()` reports —
    zombies that are our children and are not asyncio-managed workers — so we
    never touch a PID asyncio is awaiting. Spawned as a background task by
    `orchestrate()` and cancelled in its `finally`, mirroring `_memory_sampler`."""
    while True:
        try:
            for pid in _orphan_zombie_children():
                try:
                    os.waitpid(pid, os.WNOHANG)
                except (ChildProcessError, OSError):
                    pass  # asyncio already reaped it, or it vanished — fine
        except Exception:
            pass  # reaping must never crash the orchestrator
        await asyncio.sleep(interval_sec)


async def _memory_sampler(st: "State",
                          interval_sec: float = 30.0) -> None:
    """Periodic orchestrator-memory sample for leak detection.

    Writes one ndjson line per `interval_sec` to `memory.ndjson` alongside
    `state.json`. Each line records RSS, current phase, worker count, open
    FDs, and thread count — enough to correlate growth with the phase and
    the worker concurrency in flight at that moment.

    Lifecycle: spawned as a fire-and-forget task by `orchestrate()`, cancelled
    in the `finally` block. On cancellation, one final sample is written
    before re-raising so the on-disk trail captures the orchestrator's
    end-of-run state.

    Never crashes the orchestrator: every probe is exception-guarded, the
    sample-write is exception-guarded, and an exception thrown anywhere
    inside the loop body is swallowed (telemetry that crashes the
    orchestrator is worse than no telemetry)."""
    # Re-resolve `st.run_dir` every tick — defensive against any future
    # mutation of st.run_dir.
    while True:
        try:
            out = st.run_dir / "memory.ndjson"
            sample = _collect_memory_sample(st)
            with out.open("a", buffering=1) as f:
                f.write(json.dumps(sample, separators=(",", ":")) + "\n")
        except Exception:
            pass
        try:
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            # Final sample before exit so the trail captures the
            # last-moment state. Best-effort: if the write fails (disk
            # full, run_dir gone), the existing samples on disk are
            # still useful; don't mask the CancelledError.
            try:
                out = st.run_dir / "memory.ndjson"
                sample = _collect_memory_sample(st)
                with out.open("a", buffering=1) as f:
                    f.write(json.dumps(sample, separators=(",", ":")) + "\n")
            except Exception:
                pass
            raise


async def claude_p(user_prompt: str, system_prompt: str, *, schema_key: str,
                   cwd: str, allowed_tools: str, max_turns: int, autonomous: bool,
                   caps: dict, st: "State", model: str, sid: str,
                   add_dirs: list[str] | None = None,
                   effort: str | None = None,
                   _suppress_capture: bool = False) -> dict:
    """Run one headless Claude Code worker and return its validated
    structured output.

    The worker's result is constrained with `--json-schema` (inline — a file
    path is silently ignored by the CLI). The CLI validates the worker's final
    output against the schema and exposes it as `structured_output` in the
    envelope. If that field is missing or the run reports an error, the worker
    is retried once with the failure noted, then declared failed.

    Worker activity streams as one JSON event per stdout line
    (`--output-format stream-json --verbose`). `_invoke` writes the raw
    events to `<state-root>/logs/<sid>.log` and emits per-event inline
    summaries gated by `st.data["verbosity"]`. The final `result` event
    is returned as the envelope — same shape as the pre-streaming
    single-result mode (`structured_output` present on schema success).

    `autonomous` workers skip permission prompts (they act on files inside an
    isolated worktree); non-autonomous workers get only read tools — unless
    `state.dangerously_skip_permissions` is set, in which case every worker
    is invoked with `--dangerously-skip-permissions`, waiving the §12
    mechanical read-only enforcement on judgment workers. See DESIGN §12
    and IMPLEMENTATION.md §2 "Permission override (dangerous)".

    `model` is a `claude --model` alias (`sonnet` / `opus` / `haiku`);
    resolved per worker-type by `resolve_models()` at startup.

    `effort` is a `claude --effort` level (`low` / `medium` / `high` /
    `xhigh` / `max`) or `None` to omit the flag entirely (worker
    inherits Claude's default). Resolved per worker-type by
    `resolve_efforts()` at startup. The CLI exposes no `--temperature`
    or `--seed`, so effort is the strongest determinism dial available
    — pinning it on judgment workers reduces cross-run variance in
    their structured output (IMPLEMENTATION.md §2 "Effort selection").

    `sid` is the worker identifier used in inline log tags and the
    per-worker log filename (e.g. `bugfix-001`, `classifier`,
    `planner-bug-fixing`, `integrator-feat-001`, `conformer-feat-003`).

    `add_dirs` are extra paths forwarded to the CLI as `--add-dir` entries.
    Used by the inspect bucket (classifier, planner, reconciler, plan_overlap_judge, provision)
    so the `Read`/`Grep`/`Glob` sandbox and the allowlisted `Bash` verbs can
    reach sibling repos referenced in the task description. Resolved by
    `resolve_inspect_dirs()` and persisted under `st.data["inspect_dirs"]`
    so `--resume` honors the original choice.
    """
    # Drift guard: typos in `schema_key` would write orphan rows into
    # calls.ndjson (judge/heal filter by call_type, so an orphan is
    # silently dropped). Fail fast at the call site instead. The
    # allowed set is WORKER_TYPES plus the two post-run skill schemas
    # (`judge`, `patch_generator`) that are not main-loop workers but
    # do invoke claude_p with their own schema.
    _allowed_schema_keys = set(WORKER_TYPES) | {
        "judge", "patch_generator", "pr_writer", "dep_capture"}
    if schema_key not in _allowed_schema_keys:
        raise ValueError(
            f"claude_p called with unknown schema_key {schema_key!r}; "
            f"expected one of {sorted(_allowed_schema_keys)}"
        )
    schema = json.dumps(SCHEMAS[schema_key], separators=(",", ":"))
    leerie_dir = st.path.parent
    verbosity = st.data.get("verbosity", VERBOSITY_DEFAULT)

    def build(extra_user: str = "") -> list[str]:
        cmd = [
            "claude", "-p", user_prompt + extra_user,
            "--append-system-prompt", system_prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--json-schema", schema,
            "--allowedTools", allowed_tools,
            "--disallowedTools", DISALLOWED_TOOLS,
            "--max-turns", str(max_turns),
            "--model", model,
        ]
        # IMPLEMENTATION.md §2 "Effort selection". When effort is None
        # (unset for this worker, the default for acting workers) the
        # CLI invocation is byte-identical to the pre-feature behavior;
        # only opted-in workers carry the flag.
        if effort is not None:
            cmd.extend(["--effort", effort])
        for d in (add_dirs or ()):
            cmd.extend(["--add-dir", d])
        skip_perms = autonomous or bool(
            st.data.get("dangerously_skip_permissions", False))
        if skip_perms:
            # Acting workers (autonomous=True) run inside an isolated
            # worktree; skipping prompts is what makes the run unattended,
            # blast radius bounded by the worktree. When the user passes
            # the top-level --dangerously-skip-permissions escape hatch
            # (DESIGN §12 last paragraph), judgment workers in the real
            # repo cwd also get the flag — §12 mechanical enforcement
            # waived, trust shifts onto the prompts.
            cmd.append("--dangerously-skip-permissions")
        return cmd

    timeout = caps["worker_timeout_sec"]

    async def _spawn(retry_note: str) -> dict:
        """One `_invoke` + telemetry + NDJSON capture + non-clean-exit
        warnings. Factored out so the auth/quota backoff loop below can
        re-invoke the worker without duplicating the capture/telemetry
        bookkeeping — every retry, success or failure, still produces
        one calls.ndjson row so the audit trail is complete."""
        _t0 = time.monotonic()
        envelope = await _invoke(build(retry_note), cwd, timeout,
                                 sid, leerie_dir, verbosity,
                                 progress=lambda: _get_progress(st),
                                 idle_warn_sec=caps.get(
                                     "worker_idle_warn_sec",
                                     DEFAULT_CAPS["worker_idle_warn_sec"]),
                                 worker_memory_max_bytes=caps.get(
                                     "worker_memory_max_bytes"),
                                 worker_pids_max=caps.get(
                                     "worker_pids_max",
                                     DEFAULT_CAPS["worker_pids_max"]))
        _latency_ms = int((time.monotonic() - _t0) * 1000)

        # record run-weight telemetry
        st.add_telemetry(envelope)

        # capture NDJSON record — written on every attempt (success and failure)
        # so a hard-killed run leaves a complete audit trail.
        # Skipped when _suppress_capture=True (replay mode) so replays
        # never pollute the captures stream.
        if not _suppress_capture:
            _usage = envelope.get("usage") or {}
            _parsed_ok = envelope.get("structured_output") is not None
            _success = not envelope.get("is_error") and _parsed_ok
            # cgroup_applied: whether the per-worker cgroup containment
            # was active for this spawn. Useful when post-mortem
            # inspecting a calls.ndjson — a run with cgroup_applied
            # consistently False means the launcher's writable
            # /sys/fs/cgroup mount didn't propagate, and the OOM-
            # cascade safety net was off.
            _cgroup_applied = _CGROUP_PROBE_RESULT is True
            # failure_kind: why this call failed (null on success). Covers
            # only envelope-returning failures — RateLimitedExit / WorkerError
            # raise past this block and are never captured (see
            # _classify_failure_kind's KNOWN GAP).
            _failure_kind = _classify_failure_kind(envelope, _parsed_ok)
            _capture_call(st.run_dir, {
                "call_id": str(uuid.uuid4()),
                "run_id": st.run_id,
                "call_type": schema_key,
                "model": model,
                "system_prompt": system_prompt,
                "user_content": user_prompt + retry_note,
                "response_content": str(envelope.get("result") or ""),
                "parsed_ok": _parsed_ok,
                "input_tokens": int(_usage.get("input_tokens") or 0),
                "output_tokens": int(_usage.get("output_tokens") or 0),
                "latency_ms": _latency_ms,
                "success": _success,
                "failure_kind": _failure_kind,
                "cgroup_applied": _cgroup_applied,
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            })

        # surface non-clean exits — a worker that hit --max-turns exits 0 and
        # can still produce structured_output, but stopped mid-work
        term = envelope.get("terminal_reason", "")
        turns = envelope.get("num_turns", -1)
        if term and term != "completed":
            log(f"  ⚠  worker exited with terminal_reason='{term}' "
                f"(num_turns={turns}) — output may be incomplete")
        # Context-decay proxy: a worker that returned at or above 80% of its
        # turn budget likely produced its final result against a degraded
        # context window. The schema only checks structure, not the quality
        # of reasoning underneath it. Surface the proxy so a 9.x confidence
        # score from a near-cap worker is read with the right scepticism.
        # `elif`: this branch only fires when the worker stopped cleanly —
        # if terminal_reason was set, the warning above already named
        # num_turns, so we avoid double-warning the same condition.
        elif turns >= 0 and turns >= int(0.8 * max_turns):
            log(f"  ⚠  worker returned at {turns}/{max_turns} turns "
                f"(≥80% of cap) — output may have been produced against a "
                "degraded context window")

        return envelope

    auth_retry_max_sec = caps.get(
        "auth_retry_max_sec", DEFAULT_CAPS["auth_retry_max_sec"])
    last_problem = ""
    for attempt in (1, 2):
        retry_note = ("" if attempt == 1 else
                      f"\n\nYOUR PREVIOUS ATTEMPT FAILED: {last_problem} "
                      "Return output that conforms exactly to the required schema.")
        envelope = await _spawn(retry_note)

        # Auth/quota backoff: 401/429/529/auth-message envelopes need
        # waiting, not the immediate corrective retry below. The gateway
        # has already rejected the request and a fresh request will be
        # rejected too until the user's Claude Code subscription window
        # clears (401/429) or the transient overload (529) subsides. Run
        # tenacity's exponential-backoff-with-jitter loop, capped at
        # `auth_retry_max_sec` cumulative seconds. The loop exits when an
        # invocation returns a non-auth envelope (success or a different
        # error class) or when the budget is exhausted.
        if _is_auth_or_quota_failure(envelope):
            # Lazy import (not module-scope) so this file loads on a bare host
            # python3 lacking requirements.txt deps — see the module-top note.
            from tenacity import (
                AsyncRetrying,
                RetryCallState,
                RetryError,
                retry_if_result,
                stop_after_delay,
                wait_exponential_jitter,
            )

            def _log_before_sleep(rs: RetryCallState) -> None:
                env = rs.outcome.result()
                marker = (env.get("api_error_status") or "auth/quota")
                log(f"  worker hit {marker} — retrying in "
                    f"{rs.next_action.sleep:.0f}s "
                    f"(elapsed {rs.seconds_since_start:.0f}s of "
                    f"{auth_retry_max_sec}s budget)")

            # Use tenacity's __call__ (decorator) form rather than the
            # iterator form: the iterator form's AttemptManager.__exit__
            # unconditionally overwrites retry_state's result with None
            # on clean exit, defeating retry_if_result. __call__ sets
            # the result correctly inside its own loop and surfaces the
            # last attempt via RetryError.last_attempt on stop-fire.
            try:
                envelope = await AsyncRetrying(
                    wait=wait_exponential_jitter(
                        initial=15, max=120, jitter=5),
                    stop=stop_after_delay(auth_retry_max_sec),
                    retry=retry_if_result(_is_auth_or_quota_failure),
                    reraise=False,
                    before_sleep=_log_before_sleep,
                )(_spawn, retry_note)
            except RetryError as e:
                # Budget exhausted with the envelope still auth/quota.
                # Surface the last attempt's envelope so the
                # subscription-cap WorkerError below fires with
                # accurate context. retry_if_result only filters
                # results (not exceptions), so last_attempt holds a
                # result Future — .result() returns the envelope.
                envelope = e.last_attempt.result()

            if _is_auth_or_quota_failure(envelope):
                # A 529 is transient gateway overload, not a subscription
                # cap — don't misattribute it. 401/429 (and the text-marker
                # path) stay on the rolling-usage-cap message.
                if envelope.get("api_error_status") == 529:
                    raise WorkerError(
                        "Claude API returned an overloaded (529) error "
                        f"after ~{auth_retry_max_sec}s of retries — the "
                        "Anthropic gateway is under transient load. Run "
                        "--resume to retry.")
                raise WorkerError(
                    "Claude API returned auth/quota error after "
                    f"~{auth_retry_max_sec}s of retries — your Claude "
                    "Code subscription likely hit its rolling usage "
                    "cap. Run --resume once the window clears.")

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


async def replay_capture(record: dict, *,
                         override_system_prompt: str | None = None,
                         cwd: str | None = None) -> tuple[dict, dict]:
    """Replay one captured call from a calls.ndjson record.

    Given a single NDJSON record (as a dict), reconstructs the `claude_p()`
    invocation with the captured `system_prompt`, `user_content`, `call_type`
    (mapped to `schema_key`), `model`, and any other reproducible parameters.
    Returns `(envelope, structured_output)` from the new invocation.

    `override_system_prompt` lets the heal loop replay with a patched prompt
    instead of the originally captured one.

    Replays use a throw-away in-memory state and `_suppress_capture=True` so
    they never pollute the original run's calls.ndjson — the capture stream is
    the ground truth; replay is ephemeral analysis.

    `cwd` defaults to the current working directory. The replay worker runs
    non-autonomous (read-only tools) by default, matching the behaviour most
    call types actually use; callers may not need write access for scoring.

    The returned structured_output is the parsed object from the new envelope.
    A WorkerError is raised if the replay call fails schema validation twice,
    same as a live call.
    """
    call_type = record["call_type"]
    system_prompt = override_system_prompt or record["system_prompt"]
    user_prompt = record["user_content"]
    model = record.get("model", MODEL_DEFAULT)

    # Minimal in-memory state: no run dir needed because capture is suppressed.
    # _suppress_capture=True prevents _capture_call from writing anywhere;
    # add_telemetry is called but state.save() writes to a tempdir that is
    # discarded after replay.
    import tempfile
    with tempfile.TemporaryDirectory() as _tmpdir:
        tmp_run_dir = Path(_tmpdir) / "replay-run"
        tmp_run_dir.mkdir()
        tmp_state_path = tmp_run_dir / "state.json"
        tmp_state_path.write_text("{}")

        replay_st = _ReplayState(tmp_run_dir, tmp_state_path)
        caps = dict(DEFAULT_CAPS)

        # Replay deliberately omits `effort=`: captured records don't
        # store the original `--effort` level, so a faithful replay
        # would have to guess. Falling through to claude_p's None
        # default keeps replays shaped like every other
        # "no-effort-pinned" call.
        structured = await claude_p(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            schema_key=call_type,
            cwd=cwd or os.getcwd(),
            allowed_tools=INSPECT_TOOLS,
            max_turns=40,
            autonomous=False,
            caps=caps,
            st=replay_st,
            model=model,
            sid=f"replay-{call_type}",
            _suppress_capture=True,
        )
    envelope = replay_st.last_envelope
    return (envelope, structured)


def _accumulate_telemetry(data: dict, envelope: dict) -> None:
    """Accumulate run-weight signals from a worker envelope into `data`.
    Shared between State and _ReplayState."""
    t = data.setdefault("telemetry", {"calls": 0, "cost_usd": 0.0,
                                       "input_tokens": 0,
                                       "output_tokens": 0})
    t["calls"] += 1
    t["cost_usd"] += float(envelope.get("total_cost_usd") or 0.0)
    usage = envelope.get("usage") or {}
    t["input_tokens"] += int(usage.get("input_tokens") or 0)
    t["output_tokens"] += int(usage.get("output_tokens") or 0)


class _ReplayState:
    """Minimal State-alike for replay_capture: no persistent writes.

    Satisfies the interface claude_p() calls on the state object (bump_workers,
    add_telemetry, .data, .run_id, .run_dir, .path) without touching the
    state root on disk. All save() calls are no-ops. last_envelope captures the envelope returned
    by _invoke so replay_capture can return (envelope, structured_output).
    """

    def __init__(self, run_dir: Path, state_path: Path) -> None:
        self.run_dir = run_dir
        self.path = state_path
        self.run_id = "replay"
        self.data: dict = {
            "telemetry": {"calls": 0, "cost_usd": 0.0,
                          "input_tokens": 0, "output_tokens": 0},
            "verbosity": "quiet",
        }
        self.last_envelope: dict = {}

    def save(self) -> None:
        pass  # replay writes nothing

    def bump_workers(self, caps: dict) -> None:
        pass  # no budget tracking during replay

    def add_telemetry(self, envelope: dict) -> None:
        self.last_envelope = envelope
        _accumulate_telemetry(self.data, envelope)


class StateLockedError(Exception):
    """Raised when State.__init__ cannot acquire the per-run-directory
    flock because another orchestrator already owns this run.

    The handler at the orchestrator entry point converts this into an
    EXIT_LOCKED process exit; the launcher's rc=75 pivot then attaches
    the user to the live orchestrator's log stream via the smart
    `--resume` router (DESIGN §6 *Single owner per run dir*, *Smart
    resume in remote mode*)."""

    def __init__(self, run_dir: Path):
        super().__init__(f"another orchestrator already owns {run_dir}")
        self.run_dir = run_dir


# =========================================================================
# run state — persisted so a run is observable and resumable
# =========================================================================
class State:
    """In-memory run state with atomic on-disk persistence.

    Single-owner-per-run-dir: `__init__` acquires an exclusive advisory
    flock on the run directory and holds it for the life of the
    process (released by the kernel on exit, including SIGKILL). A
    second orchestrator that tries to construct State against the same
    run_dir gets `StateLockedError`. This is the load-bearing defense
    against the dual-orchestrator race — see DESIGN §6 *Single owner
    per run dir* for the architecture and the failure mode it
    prevents.

    The lock is on the *directory*, not state.json: `save()`'s atomic
    `tmp.replace(self.path)` would orphan a state.json-bound fd from
    the new inode, opening a multi-second window where a racer could
    acquire on the unlocked replacement. Directory inodes are never
    replaced, so the lock fd stays valid for the process lifetime.

    No async-side lock: every mutator runs on the single asyncio event
    loop, so reads and writes are not preempted mid-statement.
    Concurrent `claude -p` workers spawned via `asyncio.gather`
    interleave only at `await` points, which never fall inside a
    `st.data[k] = v; st.save()` pair.

    Per-run scope: every State instance is anchored at
    `leerie_root / "runs" / run_id / state.json`. Two State instances
    with different run_ids share no on-disk state. See DESIGN.md §6
    and §10."""

    def __init__(
        self,
        leerie_root: Path,
        run_id: str,
        repo_root: Path | None = None,
    ):
        self.leerie_root = leerie_root
        self.run_id = run_id
        # leerie_root may now live outside the repo (LEERIE_STATE_DIR), so
        # repo_root cannot be derived from it as leerie_root.parent in general.
        self.repo_root: Path = repo_root if repo_root is not None else leerie_root.parent
        self.run_dir = leerie_root / "runs" / run_id
        self.path = self.run_dir / "state.json"
        self.data: dict = {}
        # Ensure run_dir exists so we can open it for flock. The
        # orchestrator entry point also mkdir's the standard subdirs
        # right after construction; this just guarantees `run_dir`
        # itself is present before we try to flock its inode.
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._lock_fd: int | None = None
        self._acquire_lock()

    def _acquire_lock(self) -> None:
        """Open run_dir for flock and acquire EX|NB. Raises
        StateLockedError if held by another process.

        `from None` on the re-raise suppresses Python's automatic
        `__context__` chain — the caller dies cleanly with just
        StateLockedError in the traceback, no "During handling of
        the above exception, another exception occurred..." noise."""
        fd = os.open(self.run_dir, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise StateLockedError(self.run_dir) from None
        self._lock_fd = fd

    def release_lock(self) -> None:
        """Explicit release. Idempotent. The kernel also releases on
        process exit; this exists for tests and for callers that want
        deterministic teardown."""
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)
            except Exception:
                # Catch-all is intentional: __del__ → release_lock can
                # fire at interpreter shutdown when the `os` module
                # global has been set to None, raising NameError
                # instead of OSError. The fd is closed by the kernel
                # on process exit regardless; the catch is to suppress
                # a noisy traceback, not to recover.
                pass
            self._lock_fd = None

    def __del__(self) -> None:
        # Best-effort cleanup. Python's GC may not call this on
        # interpreter shutdown; the kernel's process-exit cleanup is
        # the real guarantee.
        #
        # Use getattr because __del__ can run on a partially-constructed
        # instance — if `__init__` raised mid-way (e.g. bad path types
        # before `_lock_fd` was set), self.release_lock() would raise
        # AttributeError that Python then "ignores" with a noisy
        # traceback. The conditional sidesteps that.
        if getattr(self, "_lock_fd", None) is not None:
            self.release_lock()

    def load(self) -> bool:
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            return True
        return False

    def save(self) -> None:
        """Atomic write via temp-file rename.

        The flock is on `self.run_dir`, not `self.path`, so the
        `tmp.replace(self.path)` inode swap below does not affect lock
        ownership. The directory inode is stable for the run's
        lifetime."""
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        tmp.replace(self.path)   # atomic on POSIX; best-effort on Windows

    def bump_workers(self, caps: dict) -> None:
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
        _accumulate_telemetry(self.data, envelope)
        self.save()


# =========================================================================
# judge phase — LLM-scored review of captured call records
# =========================================================================

async def judge_capture(record: dict, models: dict[str, str],
                        efforts: dict[str, str | None],
                        caps: dict, st: "State") -> dict:
    """Run a judge worker against one captured call record.

    The judge evaluates the record's response_content on three dimensions:
    schema adherence, factual accuracy, and hallucination-freeness. Uses
    claude_p() with schema_key="judge" and a deterministic sid derived from
    the call_type and call_id so the per-worker log file is locatable.

    Returns the structured judge output dict (validated against
    SCHEMAS["judge"]).
    """
    call_type = record.get("call_type", "unknown")
    call_id = record.get("call_id", "unknown")
    sys_prompt = load_prompt("judge")
    user_prompt = (
        "CALL RECORD TO JUDGE:\n"
        f"call_type: {call_type}\n"
        f"call_id: {call_id}\n"
        f"model: {record.get('model', '')}\n\n"
        "SYSTEM PROMPT (the instructions the worker was given):\n"
        f"{record.get('system_prompt', '')}\n\n"
        "USER CONTENT (the input the worker received):\n"
        f"{record.get('user_content', '')}\n\n"
        "RESPONSE CONTENT (what the worker produced):\n"
        f"{record.get('response_content', '')}\n\n"
        f"parsed_ok: {record.get('parsed_ok', False)}\n"
        f"success: {record.get('success', False)}\n\n"
        "Judge this call on the three dimensions and return your verdict."
    )
    # Judge workers are stateless observers — read-only tools only.
    model = models.get("judge", MODEL_DEFAULT)
    effort = efforts.get("judge")
    st.bump_workers(caps)
    return await claude_p(
        user_prompt=user_prompt,
        system_prompt=sys_prompt,
        schema_key="judge",
        cwd=os.getcwd(),
        allowed_tools=INSPECT_TOOLS,
        max_turns=40,
        autonomous=False,
        caps=caps,
        st=st,
        model=model,
        effort=effort,
        sid=f"judge-{call_type}-{call_id[:8]}",
    )


async def phase_judge(run_dir: Path, judge_out_dir: Path,
                      caps: dict, st: "State",
                      models: dict[str, str],
                      efforts: dict[str, str | None],
                      judge_call_types: list[str] | None = None) -> dict:
    """Judge all captured call records in run_dir/calls.ndjson.

    Reads each line of calls.ndjson, optionally filters by call_type when
    `judge_call_types` is provided, then runs judge_capture() in parallel
    under the existing asyncio.Semaphore(max_parallel) bound.

    Each verdict is written to judge_out_dir/<call_id>.json. After all
    judgments complete, an INDEX.json is written to judge_out_dir/ listing
    every judged call with its call_id, call_type, and passed status.

    Returns a dict with keys "judged" (count) and "index" (list of index
    entries).
    """
    capture_path = run_dir / "calls.ndjson"
    if not capture_path.exists():
        log("phase_judge: no calls.ndjson found — nothing to judge")
        return {"judged": 0, "index": []}

    records: list[dict] = []
    for line in capture_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            log(f"  phase_judge: skipping malformed NDJSON line: {line[:80]!r}")
            continue
        if judge_call_types and rec.get("call_type") not in judge_call_types:
            continue
        records.append(rec)

    if not records:
        log("phase_judge: no records to judge after filtering")
        return {"judged": 0, "index": []}

    judge_out_dir.mkdir(parents=True, exist_ok=True)
    log(f"phase_judge: judging {len(records)} record(s)")

    sem = asyncio.Semaphore(caps["max_parallel"])
    index: list[dict] = []

    async def judge_one(rec: dict) -> None:
        async with sem:
            call_id = rec.get("call_id", "unknown")
            call_type = rec.get("call_type", "unknown")
            verdict = await judge_capture(rec, models, efforts, caps, st)
            verdict_path = judge_out_dir / f"{call_id}.json"
            verdict_path.write_text(json.dumps(verdict, indent=2))
            index.append({
                "call_id": call_id,
                "call_type": call_type,
                "passed": verdict.get("passed", False),
            })
            status = "pass" if verdict.get("passed") else "FAIL"
            log(f"  judge-{call_type}-{call_id[:8]}: {status}")

    await gather_or_cancel(*(judge_one(r) for r in records))

    # Sort index by call_id for stable output across parallel orderings.
    index.sort(key=lambda e: e["call_id"])
    (judge_out_dir / "INDEX.json").write_text(json.dumps(index, indent=2))
    log(f"phase_judge: wrote {len(index)} verdict(s) to {judge_out_dir}")
    return {"judged": len(index), "index": index}


# =========================================================================
# heal-loop — persistent state and three phase functions
# =========================================================================

class HealState:
    """Persistent state for one heal-loop run scoped to a single call_type.

    Layout on disk: <heal_dir>/<call_type>/state.json

    Fields written to state.json:
      failing_samples  — list of capture records the heal loop is working on
      baseline         — {call_id: {"pass_rate": float, "verdicts": list}}
                         noise-floor measured by heal_baseline
      history          — list of iteration records appended by heal_replay_patched
      best_so_far      — {pass_rate: float, iter_n: int} tracking the best arm
    """

    def __init__(self, heal_dir: Path, call_type: str) -> None:
        self.heal_dir = heal_dir
        self.call_type = call_type
        self.state_dir = heal_dir / call_type
        self.path = self.state_dir / "state.json"
        self.failing_samples: list[dict] = []
        self.baseline: dict = {}
        self.history: list[dict] = []
        self.best_so_far: dict = {}

    def save(self) -> None:
        """Atomic write via temp-file rename."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "failing_samples": self.failing_samples,
            "baseline": self.baseline,
            "history": self.history,
            "best_so_far": self.best_so_far,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.path)

    def load(self) -> bool:
        """Load state from disk. Returns True if file existed and was loaded."""
        if not self.path.exists():
            return False
        data = json.loads(self.path.read_text())
        self.failing_samples = data.get("failing_samples", [])
        self.baseline = data.get("baseline", {})
        self.history = data.get("history", [])
        self.best_so_far = data.get("best_so_far", {})
        return True


async def heal_baseline(call_type: str, failing_records: list[dict], n: int,
                        heal_dir: Path, caps: dict, st: "State",
                        models: dict[str, str],
                        efforts: dict[str, str | None]) -> HealState:
    """Run n unpatched replays per failing capture to establish a noise-floor.

    For each record in failing_records, runs n replay_capture() calls with the
    original system prompt (no override), judges each replay via judge_capture(),
    and persists the per-sample pass rates + verdict list to
    <heal_dir>/<call_type>/baseline/verdicts/.

    Returns a HealState with failing_samples, baseline, and best_so_far set.
    Replays run in parallel under asyncio.Semaphore(max_parallel).
    """
    hs = HealState(heal_dir, call_type)
    hs.failing_samples = list(failing_records)

    verdicts_dir = heal_dir / call_type / "baseline" / "verdicts"
    verdicts_dir.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(caps["max_parallel"])
    baseline: dict = {}

    async def _run_one(record: dict, replay_idx: int) -> dict:
        """Run one replay+judge pair; return verdict dict."""
        async with sem:
            call_id = record["call_id"]
            # Replay with original system prompt (no patch).
            try:
                envelope, _ = await replay_capture(record)
            except Exception:
                envelope = {}
            # Build a synthetic record for the judge using the replayed output.
            judge_record = dict(record)
            judge_record["response_content"] = (
                envelope.get("result") or record.get("response_content", "")
            )
            judge_record["parsed_ok"] = not envelope.get("is_error", True)
            judge_record["success"] = not envelope.get("is_error", True)
            verdict = await judge_capture(judge_record, models, efforts, caps, st)
            # Write verdict file.
            call_id = record["call_id"]
            verdict_path = verdicts_dir / f"{call_id}-{replay_idx}.json"
            verdict_path.write_text(json.dumps(verdict, indent=2))
            return verdict

    # Gather all (record, replay_idx) pairs.
    tasks = []
    for record in failing_records:
        for idx in range(n):
            tasks.append((record, idx))

    results: list[tuple[dict, dict]] = []
    coros = [_run_one(rec, idx) for rec, idx in tasks]
    verdicts_flat = await gather_or_cancel(*coros)

    # Aggregate per-sample pass rates.
    task_idx = 0
    for record in failing_records:
        call_id = record["call_id"]
        sample_verdicts = []
        for idx in range(n):
            sample_verdicts.append(verdicts_flat[task_idx])
            task_idx += 1
        passes = sum(1 for v in sample_verdicts if v.get("passed", False))
        baseline[call_id] = {
            "pass_rate": passes / n if n > 0 else 0.0,
            "verdicts": sample_verdicts,
        }

    hs.baseline = baseline
    overall_pass_rate = (
        sum(v["pass_rate"] for v in baseline.values()) / len(baseline)
        if baseline else 0.0
    )
    hs.best_so_far = {"pass_rate": overall_pass_rate, "iter_n": 0}
    hs.save()
    log(f"heal_baseline: {call_type}: {len(failing_records)} sample(s), "
        f"n={n}, baseline pass_rate={overall_pass_rate:.2%}")
    return hs


def heal_apply_patch(call_type: str, iter_n: int, patch_text: str,
                     anchor_match: str, heal_dir: Path,
                     failing_records: list[dict]) -> list[Path]:
    """Materialise per-sample patched prompts under iter-<N>/patched-prompts/.

    For each record in failing_records, replaces the first occurrence of
    `anchor_match` in the original system_prompt with `patch_text`, and writes
    the result to <heal_dir>/<call_type>/iter-<N>/patched-prompts/<call_id>.txt.

    Returns the list of written paths.
    """
    out_dir = heal_dir / call_type / f"iter-{iter_n}" / "patched-prompts"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for record in failing_records:
        call_id = record["call_id"]
        original = record.get("system_prompt", "")
        patched = original.replace(anchor_match, patch_text, 1)
        dest = out_dir / f"{call_id}.txt"
        dest.write_text(patched)
        written.append(dest)
    log(f"heal_apply_patch: {call_type} iter-{iter_n}: "
        f"wrote {len(written)} patched prompt(s)")
    return written


async def heal_replay_patched(call_type: str, iter_n: int, n: int,
                              heal_dir: Path, caps: dict, st: "State",
                              models: dict[str, str],
                              efforts: dict[str, str | None]) -> HealState:
    """Run n patched replays per failing capture and append an iteration record.

    Reads HealState from disk. For each failing sample, loads the patched
    prompt from iter-<iter_n>/patched-prompts/<call_id>.txt, runs n
    replay_capture() calls with that prompt override, judges each via
    judge_capture(), computes the pass rate, and appends an iteration record
    to hs.history. Updates hs.best_so_far if the pass rate improved.

    Replays run in parallel under asyncio.Semaphore(max_parallel).
    Returns the updated HealState.
    """
    hs = HealState(heal_dir, call_type)
    if not hs.load():
        raise FileNotFoundError(
            f"HealState not found at {hs.path} — run heal_baseline first"
        )

    patched_dir = heal_dir / call_type / f"iter-{iter_n}" / "patched-prompts"
    verdicts_dir = heal_dir / call_type / f"iter-{iter_n}" / "verdicts"
    verdicts_dir.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(caps["max_parallel"])

    async def _run_one(record: dict, replay_idx: int,
                       patched_prompt: str) -> dict:
        async with sem:
            try:
                envelope, _ = await replay_capture(
                    record, override_system_prompt=patched_prompt
                )
            except Exception:
                envelope = {}
            judge_record = dict(record)
            judge_record["response_content"] = (
                envelope.get("result") or record.get("response_content", "")
            )
            judge_record["parsed_ok"] = not envelope.get("is_error", True)
            judge_record["success"] = not envelope.get("is_error", True)
            verdict = await judge_capture(judge_record, models, efforts, caps, st)
            call_id = record["call_id"]
            verdict_path = verdicts_dir / f"{call_id}-{replay_idx}.json"
            verdict_path.write_text(json.dumps(verdict, indent=2))
            return verdict

    # Build tasks: (record, patched_prompt, replay_idx).
    tasks: list[tuple[dict, str, int]] = []
    for record in hs.failing_samples:
        call_id = record["call_id"]
        prompt_path = patched_dir / f"{call_id}.txt"
        if not prompt_path.exists():
            log(f"  heal_replay_patched: missing patched prompt for {call_id}, "
                f"skipping")
            continue
        patched_prompt = prompt_path.read_text()
        for idx in range(n):
            tasks.append((record, patched_prompt, idx))

    coros = [_run_one(rec, idx, prompt) for rec, prompt, idx in tasks]
    verdicts_flat: list[dict] = await gather_or_cancel(*coros)

    # Aggregate per-sample pass rates for this iteration.
    iter_scores: dict = {}
    task_offset = 0
    records_with_prompts = [
        r for r in hs.failing_samples
        if (patched_dir / f"{r['call_id']}.txt").exists()
    ]
    for record in records_with_prompts:
        call_id = record["call_id"]
        sample_verdicts = verdicts_flat[task_offset:task_offset + n]
        task_offset += n
        passes = sum(1 for v in sample_verdicts if v.get("passed", False))
        iter_scores[call_id] = {
            "pass_rate": passes / n if n > 0 else 0.0,
            "verdicts": sample_verdicts,
        }

    overall_pass_rate = (
        sum(v["pass_rate"] for v in iter_scores.values()) / len(iter_scores)
        if iter_scores else 0.0
    )

    iter_record = {
        "iter_n": iter_n,
        "pass_rate": overall_pass_rate,
        "scores": iter_scores,
    }
    hs.history.append(iter_record)

    if overall_pass_rate > hs.best_so_far.get("pass_rate", 0.0):
        hs.best_so_far = {"pass_rate": overall_pass_rate, "iter_n": iter_n}

    hs.save()
    log(f"heal_replay_patched: {call_type} iter-{iter_n}: "
        f"pass_rate={overall_pass_rate:.2%}")
    return hs


def check_convergence(state: HealState, config: dict) -> str:
    """Evaluate whether the heal loop has converged.

    Returns one of:
      SUCCESS          — best pass_rate >= config["success_threshold"]
      TIMEOUT          — iterations exhausted (len(history) >= max_iterations)
      BUDGET_EXHAUSTED — worker_count reached max_total_workers
      PLATEAUED        — last plateau_window iterations all have |delta| < plateau_delta
      REGRESSED        — every history entry's pass_rate is below the baseline
      CONTINUE         — none of the above; keep iterating

    `config` keys (all required):
      success_threshold   float  — e.g. 0.9
      max_iterations      int    — e.g. 10
      plateau_window      int    — e.g. 3
      plateau_delta       float  — e.g. 0.03
      worker_count        int    — current worker invocation count
      max_total_workers   int    — cap from caps dict

    The convergence check is deterministic (DESIGN §12): it operates entirely
    on measurements in HealState.history and best_so_far, with no model judgment.
    """
    history = state.history
    best_pass_rate = state.best_so_far.get("pass_rate", 0.0)
    success_threshold = config["success_threshold"]
    max_iterations = config["max_iterations"]
    plateau_window = config["plateau_window"]
    plateau_delta = config["plateau_delta"]
    worker_count = config.get("worker_count", 0)
    max_total_workers = config.get("max_total_workers", DEFAULT_CAPS["max_total_workers"])

    # SUCCESS: best arm already meets the target.
    if best_pass_rate >= success_threshold:
        return "SUCCESS"

    # BUDGET_EXHAUSTED: worker cap reached before convergence.
    if worker_count >= max_total_workers:
        return "BUDGET_EXHAUSTED"

    # TIMEOUT: iteration cap hit.
    if len(history) >= max_iterations:
        return "TIMEOUT"

    # REGRESSED: every iteration was worse than baseline.
    if history:
        baseline_rate = (
            sum(v["pass_rate"] for v in state.baseline.values()) / len(state.baseline)
            if state.baseline else 0.0
        )
        if all(entry.get("pass_rate", 0.0) < baseline_rate for entry in history):
            return "REGRESSED"

    # PLATEAUED: the last plateau_window iterations all changed by less than plateau_delta.
    if len(history) >= plateau_window:
        recent = history[-plateau_window:]
        rates = [entry.get("pass_rate", 0.0) for entry in recent]
        deltas = [abs(rates[i] - rates[i - 1]) for i in range(1, len(rates))]
        if all(d < plateau_delta for d in deltas):
            return "PLATEAUED"

    return "CONTINUE"


def write_heal_report(call_type: str, state: HealState,
                      best_patch_text: str = "") -> Path:
    """Render a markdown heal report to <heal_dir>/<call_type>/healing-<call_type>.md.

    The report includes:
    - The best patch text (or 'none' when no patch improved on baseline)
    - The number of iterations run
    - A per-iteration history table with pass rates
    - The baseline pass rate

    Returns the path of the written file.
    """
    report_dir = state.state_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"healing-{call_type}.md"

    baseline_rate = (
        sum(v["pass_rate"] for v in state.baseline.values()) / len(state.baseline)
        if state.baseline else 0.0
    )
    best = state.best_so_far
    best_rate = best.get("pass_rate", 0.0)
    best_iter = best.get("iter_n", 0)
    n_iterations = len(state.history)

    lines = [
        f"# Heal report: {call_type}",
        "",
        f"**Iterations run:** {n_iterations}  ",
        f"**Baseline pass rate:** {baseline_rate:.1%}  ",
        f"**Best pass rate:** {best_rate:.1%} (iter {best_iter})  ",
        "",
        "## Best patch",
        "",
        "```",
        best_patch_text if best_patch_text else "(no patch improved on baseline)",
        "```",
        "",
        "## Iteration history",
        "",
        "| iter | pass_rate |",
        "|------|-----------|",
    ]
    for entry in state.history:
        lines.append(f"| {entry.get('iter_n', '?')} | {entry.get('pass_rate', 0.0):.1%} |")

    if not state.history:
        lines.append("| — | — |")

    lines.append("")
    report_path.write_text("\n".join(lines))
    log(f"write_heal_report: {call_type}: wrote {report_path}")
    return report_path


async def request_patch(state: HealState, iter_n: int,
                        st: "State", caps: dict,
                        models: dict[str, str],
                        efforts: dict[str, str | None]) -> tuple[str, str]:
    """Invoke the patch-generator worker to propose a minimal prompt edit.

    Builds a user_prompt containing:
    - The current prompt body (resolved via resolve_prompt)
    - The failing samples (response_content from each)
    - The prior iteration history for context

    Calls claude_p() with schema_key="patch_generator" and sid
    `heal-patch-<call_type>-iter<N>`.

    After the worker responds, validates that the returned `anchor` is a
    literal substring of the resolved prompt body. If not, raises ValueError
    — the heal loop must not apply a patch that cannot be cleanly located in
    the prompt (per the prompts-are-advisory-code-enforces principle: this
    check lives in code, not in the prompt).

    Returns (anchor_match, patch_text) on success.
    """
    call_type = state.call_type
    _, prompt_body, _ = resolve_prompt(call_type)
    sys_prompt = load_prompt("patch_generator")

    # Build the failing samples section: only response_content is needed
    # for the patch-generator to understand what went wrong.
    sample_lines = []
    for rec in state.failing_samples:
        cid = rec.get("call_id", "?")
        resp = rec.get("response_content", "")
        sample_lines.append(f"call_id: {cid}\nresponse_content:\n{resp}")
    samples_block = "\n---\n".join(sample_lines) if sample_lines else "(none)"

    # Prior history: anchor/replacement/strategy/pass_rate for each iteration.
    history_lines = []
    for entry in state.history:
        n = entry.get("iter_n", "?")
        pr = entry.get("pass_rate", 0.0)
        # patch text is not stored in history; only pass_rate and scores are.
        history_lines.append(f"iter {n}: pass_rate={pr:.2%}")
    history_block = "\n".join(history_lines) if history_lines else "(no prior iterations)"

    user_prompt = (
        f"CALL TYPE: {call_type}\n"
        f"ITERATION: {iter_n}\n\n"
        "CURRENT SYSTEM PROMPT:\n"
        f"{prompt_body}\n\n"
        "FAILING SAMPLES:\n"
        f"{samples_block}\n\n"
        "PRIOR ITERATION HISTORY:\n"
        f"{history_block}\n\n"
        "Propose a minimal patch to the system prompt that addresses the "
        "failure mode. Return anchor, replacement, strategy, and pivot_reason."
    )

    model = models.get("heal", MODEL_DEFAULT_PER_WORKER.get("heal", MODEL_DEFAULT))
    effort = efforts.get("heal")
    st.bump_workers(caps)
    result = await claude_p(
        user_prompt=user_prompt,
        system_prompt=sys_prompt,
        schema_key="patch_generator",
        cwd=os.getcwd(),
        allowed_tools=INSPECT_TOOLS,
        max_turns=40,
        autonomous=False,
        caps=caps,
        st=st,
        model=model,
        effort=effort,
        sid=f"heal-patch-{call_type}-iter{iter_n}",
    )

    anchor = result.get("anchor", "")
    replacement = result.get("replacement", "")

    # Code-enforced: anchor must be a literal substring of the prompt body.
    # A patch that cannot be located would corrupt the prompt silently —
    # the prompt is advisory but this application check is mechanical.
    if anchor not in prompt_body:
        raise ValueError(
            f"request_patch: anchor {anchor!r} not found in resolved prompt "
            f"for call_type={call_type!r} — cannot apply patch safely"
        )

    return anchor, replacement


async def phase_heal(call_type: str, failing_records: list[dict],
                     heal_dir: Path, caps: dict,
                     st: "State", models: dict[str, str],
                     efforts: dict[str, str | None],
                     request_patch_fn=None,
                     n: int = HEAL_N_REPLAYS_DEFAULT,
                     config: dict | None = None) -> str:
    """Drive the full heal loop for one call_type.

    Phases (per iteration):
      1. Baseline (once): run n unpatched replays per record to measure noise-floor.
      2. Loop:
         a. request_patch_fn(state, iter_n) → (anchor_match, patch_text)
         b. heal_apply_patch — materialise patched prompts
         c. heal_replay_patched — run n replays with the patched prompt + judge
         d. check_convergence — returns SUCCESS/PLATEAUED/TIMEOUT/BUDGET_EXHAUSTED/
            REGRESSED/CONTINUE
      3. write_heal_report — always written, even if the loop terminates early.

    `request_patch_fn` is a callable taking (state: HealState, iter_n: int) and
    returning (anchor_match: str, patch_text: str). When None (the default), the
    real `request_patch` worker is used. Injecting a stub keeps this function
    independently testable.

    Note: the injected callable may be sync (for tests) or async. If it is a
    sync stub with 2 arguments (state, iter_n), it is called directly. If it is
    None, the real async `request_patch(state, iter_n, st, caps, models)` is
    awaited — this is the production path.

    Returns the terminal verdict string.
    """
    converge_config = dict({
        "success_threshold": HEAL_SUCCESS_THRESHOLD_DEFAULT,
        "max_iterations": HEAL_MAX_ROUNDS_DEFAULT,
        "plateau_window": HEAL_PLATEAU_WINDOW_DEFAULT,
        "plateau_delta": HEAL_PLATEAU_DELTA_DEFAULT,
    }, **(config or {}))

    # Merge in caps-derived fields so check_convergence has budget visibility.
    converge_config.setdefault("worker_count", st.data.get("worker_count", 0))
    converge_config.setdefault("max_total_workers",
                               caps.get("max_total_workers",
                                        DEFAULT_CAPS["max_total_workers"]))

    log(f"phase_heal: {call_type}: starting heal loop "
        f"(max_iter={converge_config['max_iterations']}, "
        f"threshold={converge_config['success_threshold']:.0%}, "
        f"n={n})")

    hs = await heal_baseline(call_type, failing_records, n, heal_dir, caps, st,
                             models, efforts)

    best_patch_text: str = ""
    verdict = "CONTINUE"
    iter_n = 0

    while verdict == "CONTINUE":
        iter_n += 1
        # Update worker_count snapshot before convergence check each iteration.
        converge_config["worker_count"] = st.data.get("worker_count", 0)

        # Invoke the patch generator: real worker (default) or injected stub.
        if request_patch_fn is None:
            anchor_match, patch_text = await request_patch(
                hs, iter_n, st, caps, models, efforts)
        elif asyncio.iscoroutinefunction(request_patch_fn):
            anchor_match, patch_text = await request_patch_fn(hs, iter_n)
        else:
            anchor_match, patch_text = request_patch_fn(hs, iter_n)

        heal_apply_patch(call_type, iter_n, patch_text, anchor_match,
                         heal_dir, hs.failing_samples)
        hs = await heal_replay_patched(call_type, iter_n, n, heal_dir,
                                       caps, st, models, efforts)
        converge_config["worker_count"] = st.data.get("worker_count", 0)
        verdict = check_convergence(hs, converge_config)

        # Track the patch text that produced the best result so far.
        if hs.best_so_far.get("iter_n", 0) == iter_n:
            best_patch_text = patch_text

        log(f"phase_heal: {call_type} iter-{iter_n}: verdict={verdict}")

    write_heal_report(call_type, hs, best_patch_text)
    log(f"phase_heal: {call_type}: terminated with {verdict}")
    return verdict


# =========================================================================
# per-repo dependency provisioning helpers (DESIGN §6½)
# =========================================================================

# Categories that touch only documentation / non-code surfaces. If classify
# returned *only* these, phase_provision short-circuits to kind:none — no
# point detecting an install recipe for a docs-only run (workers wouldn't
# need it anyway, and skipping the lockfile-table / LLM-fallback work
# trims the run time). The check is "are all returned categories in this
# set?" so a feature+docs task still produces a recipe.
_DOCS_ONLY_CATEGORIES = frozenset({"documentation"})


async def run_setup_hook(repo_root: Path, log_dir: Path,
                          st: "State") -> None:
    """Execute `<repo>/.leerie-setup.sh` if present. Idempotent via
    `st.data["provision"]["sh_hook_ran"]` — re-entering this function
    after the hook has already run is a no-op.

    The script runs as the `leerie` container user (non-root), in the
    repo root, with the same environment workers will see. Output
    streams to `<log_dir>/setup-hook.log`. Nonzero exit → `die()`.

    **What the hook CAN do** (runs unprivileged):
    - `mise install <lang>@<version>` to add a language runtime mise
      supports beyond the image-baked LTS bake (Ruby, Java, Rust, etc.).
    - Install user-space CLI tools into `~/.local/bin` or any other
      user-writable location.
    - Pre-populate fixtures the workers need (sample data, config).
    - Set per-run environment variables via `~/.bashrc` (note: the
      orchestrator does not source bashrc by default; the hook would
      need to write its own activation).

    **What the hook CANNOT do** (no root, no sudo):
    - `apt-get install` or any package-manager invocation requiring
      root. The container intentionally does NOT ship sudo.
    - Write to `/usr/*`, `/etc/*`, or any other system directory.
    - Install system services.

    If a repo needs a system package the language layer can't provide,
    the documented workaround is to maintain a fork of the leerie
    Dockerfile that installs it at image-build time and override
    `IMAGE_TAG`. Out of scope for leerie to automate.
    """
    prov = st.data.setdefault("provision", {})
    if prov.get("sh_hook_ran"):
        return
    hook = repo_root / ".leerie-setup.sh"
    if not hook.exists():
        return
    # A path at .leerie-setup.sh that isn't a regular file (most likely a
    # directory committed by mistake) is silent-failure-shaped: workers
    # would later die with confusing "command not found" messages from
    # the unrun setup. Surface the misshape here with a clear message.
    if not hook.is_file():
        die(
            f".leerie-setup.sh at {hook} exists but is not a regular "
            "file (it's a directory or special file). Remove the "
            "misnamed entry or replace it with an executable script."
        )

    log("phase 1½: running .leerie-setup.sh")
    st.data["current_phase"] = "phase 1½: setup-hook"
    st.save()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "setup-hook.log"
    verbosity = st.data.get("verbosity", VERBOSITY_DEFAULT)
    try:
        rc, tail = await run_streaming(
            ["bash", str(hook)],
            cwd=str(repo_root),
            timeout=600,  # 10 minutes
            log_path=log_path,
            label=".leerie-setup.sh",
            verbosity=verbosity,
        )
    except subprocess.TimeoutExpired as exc:
        die(f".leerie-setup.sh did not complete within 10 minutes\n"
            f"(see {log_path})\n{exc.output or ''}")
    if rc != 0:
        die(f".leerie-setup.sh exited {rc}\n(see {log_path})\n{tail}")
    prov["sh_hook_ran"] = True
    st.save()


# Regex for extracting the `go 1.X[.Y]` directive from a go.mod file.
# Matches the canonical form documented at https://go.dev/ref/mod#go-mod-file-go .
_GO_MOD_VERSION_RE = re.compile(r"^\s*go\s+(\d+(?:\.\d+){0,2})\s*$", re.MULTILINE)


def _existing_mise_toml_path(repo_root: Path) -> Path | None:
    """Return the path to whichever of `mise.toml` or `.mise.toml`
    exists in the repo root, or None if neither does. Prefers the
    non-dotted form when both exist — matches mise's documented
    discovery precedence
    (https://mise.jdx.dev/configuration.html: "Paths which start
    with `mise` can be dotfiles, e.g.: `.mise.toml`").
    """
    for name in ("mise.toml", ".mise.toml"):
        p = repo_root / name
        if p.is_file():
            return p
    return None


def _go_already_pinned(repo_root: Path) -> bool:
    """Return True if the repo already specifies a Go version mise would
    pick up — via `.go-version`, a `go` entry in `.tool-versions`, or a
    `[tools] go = "..."` in `mise.toml`/`.mise.toml`. In any of these
    cases leerie should NOT synthesize an override; the existing pin wins.
    """
    if (repo_root / ".go-version").is_file():
        return True
    tv = repo_root / ".tool-versions"
    if tv.is_file():
        try:
            for line in tv.read_text(errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or not stripped:
                    continue
                # `.tool-versions` is whitespace-separated: `tool version`.
                parts = stripped.split()
                if parts and parts[0].lower() == "go":
                    return True
        except OSError:
            pass
    mt = _existing_mise_toml_path(repo_root)
    if mt is not None:
        try:
            content = mt.read_text(errors="replace")
            # Cheap text-level check — TOML parsing here would pull in
            # tomllib but the heuristic is sufficient: any `go =` line
            # under a [tools] section indicates a pin.
            if re.search(r"(?m)^\s*go\s*=", content):
                return True
        except OSError:
            pass
    return False


# Strip leading `v` or `V` (any number) from `.nvmrc`/`.node-version`
# values; mise expects bare semver. Compiled once at module load.
_LEADING_V_RE = re.compile(r"^[vV]+")


# Idiomatic version files mise reads natively *when discovery walks
# the repo* — but NOT when `MISE_OVERRIDE_CONFIG_FILENAMES` is set
# (verified against mise discussions #6598 / #7058). When the override
# fires (because leerie synthesized a go pin), every idiomatic file the
# repo committed for some OTHER language is silently dropped — workers
# end up running on the image-baked LTS instead of the pinned version.
# So when the override fires, leerie scans these files and injects their
# pins into the override's `[tools]` section.
_IDIOMATIC_VERSION_FILES = (
    # (filename, mise tool key, value transformer)
    (".nvmrc", "node", lambda s: _LEADING_V_RE.sub("", s)),
    (".node-version", "node", lambda s: _LEADING_V_RE.sub("", s)),
    (".python-version", "python", lambda s: s),
    (".ruby-version", "ruby", lambda s: s),
)


# asdf-compatible names (used by `.tool-versions`) that mise treats as
# aliases for its canonical tool names. Without this map, a repo with
# `.nvmrc` (injects `node`) plus `.tool-versions` carrying
# `nodejs 20.11.0` would end up with both `node` and `nodejs` in the
# override — mise treats both as the same tool, producing ambiguous
# resolution. Normalize asdf names BEFORE checking already_pinned.
_ASDF_TOOL_ALIASES = {
    "nodejs": "node",
    "python3": "python",
}


def _existing_mise_toml_tool_keys(text: str | None) -> set[str]:
    """Return the set of tool keys pinned by a `[tools]` section in the
    given mise.toml text. Used by `synth_mise_go_override` to avoid
    re-pinning a tool the repo already wired up explicitly. Heuristic
    line-level scan — full TOML parsing would pull in tomllib and the
    set of forms we care about (top-level `[tools]` table, simple
    `<key> =` lines) is tiny.
    """
    if not text:
        return set()
    keys: set[str] = set()
    in_tools = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_tools = (stripped == "[tools]")
            continue
        if not in_tools:
            continue
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_-]*)\s*=", line)
        if m:
            keys.add(m.group(1))
    return keys


def _read_idiomatic_pins(repo_root: Path,
                          already_pinned: set[str]) -> list[tuple[str, str]]:
    """Return [(tool, version), ...] for every idiomatic version file in
    `repo_root` whose tool is NOT already in `already_pinned`.

    Used to bridge the `MISE_OVERRIDE_CONFIG_FILENAMES` semantic: when
    the override is set, mise reads ONLY the listed files; idiomatic
    discovery is suppressed. Leerie must therefore copy the pins forward
    explicitly. See DESIGN §6½ "Per-repo dependency provisioning."

    `.tool-versions` is parsed line-by-line (asdf-compatible format:
    `<tool> <version>` per line, comments with `#`).

    `rust-toolchain.toml` is intentionally out of scope — the file's
    `[toolchain] channel = "..."` shape needs more care than a regex
    sweep, and the rare repo that committed only rust-toolchain.toml
    can add a `mise.toml` or commit `.tool-versions` instead.
    """
    pins: list[tuple[str, str]] = []
    for filename, tool, transform in _IDIOMATIC_VERSION_FILES:
        if tool in already_pinned:
            continue
        path = repo_root / filename
        if not path.is_file():
            continue
        try:
            raw = path.read_text(errors="replace").strip()
        except OSError:
            continue
        if not raw:
            continue
        version = transform(raw.splitlines()[0].strip())
        if not version:
            continue
        pins.append((tool, version))
        already_pinned.add(tool)
    # .tool-versions: asdf-compatible, multiple tools per file.
    # asdf and mise sometimes disagree on tool names (asdf calls Node
    # `nodejs`, mise calls it `node`). Normalize via _ASDF_TOOL_ALIASES
    # before the dedup check so we don't pin both `node` AND `nodejs`
    # (which mise treats as the same tool — ambiguous resolution).
    tv = repo_root / ".tool-versions"
    if tv.is_file():
        try:
            content = tv.read_text(errors="replace")
        except OSError:
            content = ""
        for line in content.splitlines():
            stripped = line.split("#", 1)[0].strip()
            if not stripped:
                continue
            parts = stripped.split()
            if len(parts) < 2:
                continue
            raw_tool, version = parts[0], parts[1]
            tool = _ASDF_TOOL_ALIASES.get(raw_tool, raw_tool)
            if tool in already_pinned:
                continue
            pins.append((tool, version))
            already_pinned.add(tool)
    return pins


def synth_mise_go_override(repo_root: Path, run_dir: Path) -> Path | None:
    """If `go.mod` exists and no other Go pin is in place, write a mise
    override file that pins the Go version mise should install. Returns
    the absolute path to the override file (so the caller can export
    `MISE_OVERRIDE_CONFIG_FILENAMES` before invoking `mise install`),
    or None if no synthesis was needed.

    `MISE_OVERRIDE_CONFIG_FILENAMES` REPLACES the default config
    discovery rather than merging with it (verified against mise docs
    and discussions #4136 / #8510 / #6598 / #7058). When the override
    is set, mise reads ONLY the listed files — both `mise.toml` AND
    idiomatic files (`.nvmrc`, `.python-version`, etc.) are otherwise
    silently dropped. This helper preserves both:

      - Existing `mise.toml` content is read and the `go = "X"` pin
        is inserted into its `[tools]` section (no duplicate header).
      - Idiomatic version files in the repo root (`.nvmrc`,
        `.node-version`, `.python-version`, `.ruby-version`,
        `.tool-versions`) are read; any tool NOT already pinned in
        `mise.toml` gets its pin copied into the override's `[tools]`
        section alongside the synthesized go pin.

    Without the idiomatic-file copy, a polyglot Go+Node repo with
    `go.mod` + `.nvmrc: 20.11.0` and no `mise.toml` would silently
    install only Go; the Node version pinned in `.nvmrc` would drop
    to the image-baked LTS, defeating the runtime-version guarantee
    the entire mise layer was built to provide.

    **Known limits of the existing-mise-config scanner:**
    `_existing_mise_toml_tool_keys` reads tool pins from the canonical
    `[tools]` section with bare keys (`node = "20"`). Two valid TOML
    forms are NOT detected — when present, leerie will re-inject pins
    from idiomatic files alongside the existing ones, producing
    duplicate keys that mise rejects mid-run:

      - Inline-table form: `tools = { node = "20.11.0" }`
      - Quoted keys: `[tools]\n"node" = "20.11.0"`

    Both are rare in practice (the canonical form is what mise's
    docs and `mise use` write). Repos that hit these can switch to
    the canonical form. A proper fix would need `tomllib` (3.11+
    stdlib) — leerie's minimum is 3.10 — or a hand-written inline-
    table parser; neither is justified by current usage.

    See DESIGN §6½ and IMPLEMENTATION §6½ step 3.
    """
    gomod = repo_root / "go.mod"
    if not gomod.is_file():
        return None
    if _go_already_pinned(repo_root):
        return None
    try:
        text = gomod.read_text(errors="replace")
    except OSError:
        return None
    m = _GO_MOD_VERSION_RE.search(text)
    if not m:
        return None
    version = m.group(1)

    run_dir.mkdir(parents=True, exist_ok=True)
    override_path = run_dir / "mise-overrides.toml"

    header_comment = (
        "# Synthesized by leerie from go.mod (DESIGN §6½).\n"
        "# mise's go plugin does not parse go.mod itself; idiomatic\n"
        "# version files (.nvmrc, .python-version, etc.) are copied in\n"
        "# because MISE_OVERRIDE_CONFIG_FILENAMES suppresses discovery.\n"
    )

    existing = _existing_mise_toml_path(repo_root)
    existing_text: str | None = None
    if existing is not None:
        try:
            existing_text = existing.read_text(errors="replace")
        except OSError:
            existing_text = None

    # Build the full set of new pins (go + every idiomatic-file tool
    # that the existing mise.toml doesn't already pin).
    already_pinned = _existing_mise_toml_tool_keys(existing_text)
    already_pinned.add("go")  # we're adding it ourselves below
    idiomatic_pins = _read_idiomatic_pins(repo_root, already_pinned)
    new_pin_lines = [f'go = "{version}"'] + [
        f'{tool} = "{ver}"' for tool, ver in idiomatic_pins
    ]

    if existing_text is None:
        # No existing mise.toml — emit a minimal override carrying just
        # the new pins.
        body = "[tools]\n" + "\n".join(new_pin_lines) + "\n"
        override_path.write_text(header_comment + body)
        return override_path

    # Insert new pin lines into the existing [tools] section if one
    # exists; otherwise append a fresh [tools] section. We avoid
    # emitting a duplicate [tools] header (TOML 1.0 §6.5 — "Defining a
    # table more than once is invalid").
    lines = existing_text.rstrip("\n").split("\n")
    out_lines: list[str] = []
    inserted = False
    in_tools = False
    for line in lines:
        out_lines.append(line)
        stripped = line.strip()
        # Detect entering the [tools] table. A subsequent table header
        # (`[other]`) exits it. Subtables (`[tools.something]`) are also
        # valid TOML but are out of scope for this synthesis — leerie only
        # cares about adding scalar keys to the top-level [tools].
        if not in_tools and stripped == "[tools]":
            in_tools = True
            # Insert immediately after the header, before any existing
            # keys. This is the safe minimal change.
            out_lines.extend(new_pin_lines)
            inserted = True
            in_tools = False  # done — keys after are preserved as-is
            continue
    if not inserted:
        # No [tools] section in the existing file — append one.
        if out_lines and out_lines[-1].strip() != "":
            out_lines.append("")
        out_lines.append("[tools]")
        out_lines.extend(new_pin_lines)

    override_path.write_text(
        header_comment + "\n".join(out_lines) + "\n")
    return override_path


# Filenames that signal "this repo pins a runtime version mise should
# install." Used by `_repo_has_version_signal` to decide whether to
# invoke `mise install` at all — an unversioned repo runs on the
# image-baked LTS without bothering mise.
_MISE_SIGNAL_FILES = (
    "mise.toml", ".mise.toml",
    ".tool-versions",
    ".nvmrc", ".node-version",
    ".python-version",
    ".ruby-version",
    "rust-toolchain.toml",
    ".go-version",
)


def _repo_has_version_signal(repo_root: Path,
                              override_file: Path | None) -> bool:
    """Return True if the repo declares any runtime version pin mise
    can act on, OR if leerie already synthesized an override file. False
    means there's nothing for `mise install` to do; the LTS fallback
    in the image is the right answer."""
    if override_file is not None:
        return True
    for name in _MISE_SIGNAL_FILES:
        if (repo_root / name).is_file():
            return True
    return False


async def run_mise_install(repo_root: Path, log_dir: Path,
                            st: "State",
                            override_file: Path | None = None) -> None:
    """Invoke `mise install` at the repo root. If `override_file` is
    provided, exports `MISE_OVERRIDE_CONFIG_FILENAMES` so mise reads
    leerie's synthesized config instead of the default discovery walk.

    Captures resolved versions via `mise ls --current --json` and stores
    the raw blob at `st.data["provision"]["mise_versions"]` — callers can
    reduce `tools[name][0].version` for display.

    Failures propagate to `die()` with the failing tool/version and the
    last 40 lines of mise output.

    No-signals short-circuit: if the repo has zero version pins (no
    `mise.toml`, `.tool-versions`, idiomatic file, or leerie-synthesized
    override), this function is a logged no-op. The image-baked LTS
    Node and Python on PATH then become the workers' runtime. Without
    this guard, mise's exact behavior for `mise install` with no
    declared tools is implementation-dependent and could `die()` the
    whole run with a confusing "no tools to install" message — exactly
    the case the LTS-fallback story was supposed to handle smoothly.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "provision.log"

    if not _repo_has_version_signal(repo_root, override_file):
        log("  no version pins detected — workers run on image-baked LTS")
        prov = st.data.setdefault("provision", {})
        prov["mise_versions"] = {}
        st.save()
        return

    env = os.environ.copy()
    if override_file is not None:
        env["MISE_OVERRIDE_CONFIG_FILENAMES"] = str(override_file)

    # `mise install` with no tool args reads the active config and
    # installs every declared tool. We let the resolver figure out the
    # set from the repo's .tool-versions / .nvmrc / .python-version /
    # rust-toolchain.toml / .go-version (the last either committed or
    # synthesized by synth_mise_go_override).
    #
    # Stream output: a first-run install of Python 3.12 / Ruby 3.2 /
    # Rust can take minutes; without streaming the user sees a silent
    # `mise install` line and nothing else until it finishes (or hits
    # whatever container-level wall-clock the user gives up at).
    verbosity = st.data.get("verbosity", VERBOSITY_DEFAULT)
    try:
        rc, tail = await run_streaming(
            ["mise", "install"],
            cwd=str(repo_root),
            env=env,
            log_path=log_path,
            label="mise install",
            verbosity=verbosity,
        )
    except subprocess.TimeoutExpired as exc:
        die(f"mise install timed out\n(see {log_path})\n{exc.output or ''}")
    if rc != 0:
        die(f"mise install failed (exit {rc})\n(see {log_path})\n{tail}")

    # Capture resolved versions. `mise ls --current --json` is the
    # documented machine-readable view; `mise current --json` does NOT
    # exist (verified against mise.usage.kdl).
    proc = await asyncio.create_subprocess_exec(
        "mise", "ls", "--current", "--json",
        cwd=str(repo_root),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        # Not fatal — workers run their own install commands via prompt
        # injection (DESIGN §6½ "Worker-driven install"); they don't
        # need the resolved-versions blob to do so. Log and move on.
        log(f"mise ls --current --json failed (exit {proc.returncode}); "
            "skipping version capture")
        return
    try:
        versions = json.loads(stdout.decode(errors="replace") if stdout else "{}")
    except (ValueError, TypeError):
        versions = {}
    prov = st.data.setdefault("provision", {})
    prov["mise_versions"] = versions
    st.save()


# =========================================================================
# phases
# =========================================================================
async def phase_classify(task: str, st: State, caps: dict, clarify: bool,
                         models: dict[str, str],
                         efforts: dict[str, str | None]) -> dict:
    """Phase 1 (classify), which also produces the Clarify sub-step's
    intent questions: classify the task and surface only genuinely
    underivable (intent-level) questions."""
    log("phase 1: classifying task")
    st.data["current_phase"] = "phase 1: classify"
    st.save()
    sys_prompt = load_prompt("classifier")
    repo_root = Path(os.getcwd())
    user_prompt_parts: list[str] = [
        f"TASK:\n{task}\n\nClassify it and apply the clarification filter."]

    async def _invoke() -> dict:
        st.bump_workers(caps)
        return await claude_p(
            user_prompt="\n\n".join(user_prompt_parts),
            system_prompt=sys_prompt, schema_key="classifier",
            cwd=str(repo_root),
            allowed_tools=INSPECT_TOOLS, max_turns=60, autonomous=False,
            caps=caps, st=st, model=models["classifier"],
            effort=efforts["classifier"], sid="classifier",
            add_dirs=st.data.get("inspect_dirs") or None,
        )

    async def _on_feedback(fb: str) -> dict:
        if len(user_prompt_parts) > 1:
            user_prompt_parts[-1] = fb
        else:
            user_prompt_parts.append(fb)
        return {}

    result, gate_warnings = await _run_checked_loop(
        invoke=_invoke,
        check=lambda r: check_classifier_output(r, repo_root),
        name="classifier",
        max_rounds=caps["judgment_check_rounds"],
        make_feedback_prompt=_on_feedback,
    )
    if result is None:
        die("classifier crashed and produced no result")
    for w in gate_warnings:
        log(f"  classifier: {w}")
    cats = [c for c in result.get("categories", []) if c in CATEGORIES]
    if not cats:
        die("classifier returned no recognized categories")
    questions = result.get("questions", []) if clarify else []
    st.data["categories"] = cats
    st.data["classifier_questions"] = questions
    st.data["needs_source_of_truth"] = bool(result.get("source_of_truth_question"))
    st.save()
    log(f"categories: {', '.join(cats)}")
    return result


def gather_answers(st: State, supplied: dict | None) -> dict:
    """Collect clarification answers — from --answers, from the resolved
    source-of-truth preference, from a TTY prompt, or (no TTY, no answers)
    defer by writing pending-questions.json and exiting."""
    questions = st.data.get("classifier_questions", [])
    need_sot = st.data.get("needs_source_of_truth", False)
    sot_pref = st.data.get("source_of_truth_pref", "both")
    answers: dict = dict(supplied or {})

    provided_sot = answers.get("source_of_truth")
    if provided_sot is not None and provided_sot not in SOURCE_OF_TRUTH_VALUES:
        die(f"source_of_truth={provided_sot!r} is not one of "
            f"{SOURCE_OF_TRUTH_VALUES}")

    # Satisfy source_of_truth non-interactively from the resolved
    # preference (DESIGN §11). The preference always holds a real value
    # — `codebase`, `research`, or `both` (default) — so this never
    # blocks for an interactive answer.
    if need_sot and "source_of_truth" not in answers:
        answers["source_of_truth"] = sot_pref

    pending = [q for q in questions if q.get("id") not in answers]

    if not pending:
        st.data["answers"] = answers
        st.save()
        return answers

    if not sys.stdin.isatty():
        # launched non-interactively (e.g. via the plugin skill): defer.
        leerie_dir = st.path.parent
        (leerie_dir / "pending-questions.json").write_text(json.dumps({
            "questions": pending,
        }, indent=2))
        log(f"clarification needed; wrote {leerie_dir}/pending-questions.json")
        sys.exit(EXIT_NEEDS_ANSWERS)

    for q in pending:
        print(f"\n? {q['question']}")
        if q.get("why_underivable"):
            print(f"  (underivable: {q['why_underivable']})")
        answers[q["id"]] = input("  > ").strip()

    st.data["answers"] = answers
    st.save()
    return answers


def absorb_supplied_answers(args, st: State, leerie_dir: Path) -> None:
    """Merge --answers FILE into st.data['answers'] and propagate the
    update to existing subtask spec files. Safe to call on both initial
    runs and on --resume; a no-op when --answers is not set.

    The reason this is its own helper, separate from `gather_answers`,
    is that the latter runs the classifier-question collection flow
    (asking the user / writing pending-questions.json / exiting non-zero)
    which is appropriate on the initial run but not on resume. On
    resume we just want the merge half — the user has already produced
    an answers file in response to a prior EXIT_NEEDS_ANSWERS exit
    (either pending-questions.json from gather_answers, or
    pending-clarifications.json from surface_clarification), and the
    job here is to get those answers into state and onto disk so the
    next worker invocation sees them.

    The subtask-spec rewrite mirrors leerie.py around the
    needs-clarification branch of settle_subtask: every existing spec
    file gets its `_clarification_answers` field overwritten with the
    current st.data['answers']. This is intentionally aggressive — a
    subtask that doesn't read the new keys ignores them; a subtask
    that does, sees them on its next invocation."""
    if not args.answers:
        return
    supplied_path = Path(args.answers)
    if not supplied_path.exists():
        die(f"--answers file does not exist: {args.answers}")
    try:
        supplied = json.loads(supplied_path.read_text())
    except json.JSONDecodeError as e:
        die(f"--answers file is not valid JSON: {args.answers}: {e}")
    if not isinstance(supplied, dict):
        die(f"--answers file must contain a JSON object, got "
            f"{type(supplied).__name__}")

    # Validate source_of_truth if present — same validation gate as
    # gather_answers uses, so a bad value fails at startup not mid-run.
    provided_sot = supplied.get("source_of_truth")
    if provided_sot is not None and provided_sot not in SOURCE_OF_TRUTH_VALUES:
        die(f"source_of_truth={provided_sot!r} is not one of "
            f"{SOURCE_OF_TRUTH_VALUES}")

    answers = st.data.setdefault("answers", {})
    # Supplied keys override anything already in state — a re-run with
    # an answer to a previously-deferred question is the whole point.
    answers.update(supplied)
    st.data["answers"] = answers
    st.save()

    # Propagate the new answers to every existing subtask spec file so
    # implementers spawned (or re-spawned) after this point see them in
    # their `_clarification_answers`. Specs are written once at
    # phase_plan time with the then-current answers; later answers must
    # be flushed through.
    sub_dir = leerie_dir / "subtasks"
    if sub_dir.exists():
        for spec_path in sub_dir.glob("*.json"):
            try:
                spec = json.loads(spec_path.read_text())
            except json.JSONDecodeError:
                continue  # corrupted spec; let the implementer surface it
            spec["_clarification_answers"] = answers
            spec_path.write_text(json.dumps(spec, indent=2))


def surface_clarification(sid: str, question: dict, checkpoint_path: str,
                          st: State) -> bool:
    """Surface a mid-execution clarification question to the user
    (DESIGN §11). Mirrors `gather_answers`'s TTY-vs-non-TTY split:

      - Interactive (TTY): prompt right here, store the answer in
        st.data['answers'][question.id], and return True so the caller
        re-spawns the implementer as a CONTINUATION.
      - Non-interactive: write <state-root>/pending-clarifications.json
        with the question, the subtask id, and the checkpoint path,
        then sys.exit(EXIT_NEEDS_ANSWERS) so the calling layer can
        collect the answer and resume.

    Returning True signals "answer captured, re-spawn the worker."
    Non-interactive callers never reach the return — sys.exit fires
    first. The caller is responsible for bumping the
    subtask_continuations counter before treating this as the
    continuation step."""
    leerie_dir = st.path.parent
    answers = st.data.setdefault("answers", {})

    if not sys.stdin.isatty():
        # Persist enough state for the surrounding layer to resume.
        # The question id keys the answer; the checkpoint path is
        # what the re-spawned worker will read.
        (leerie_dir / "pending-clarifications.json").write_text(
            json.dumps({
                "subtask_id": sid,
                "question": question,
                "checkpoint_path": checkpoint_path,
            }, indent=2))
        log(f"  {sid}: clarification needed; wrote "
            f"{leerie_dir}/pending-clarifications.json")
        # Save state so the answer the user supplies on the re-run
        # lands in a state.json that already knows about this subtask's
        # progress so far.
        st.save()
        sys.exit(EXIT_NEEDS_ANSWERS)

    qid = question["id"]
    print(f"\n? [{sid}] {question['question']}")
    print(f"  (underivable: {question.get('why_underivable', '')})")
    answers[qid] = input("  > ").strip()
    st.data["answers"] = answers
    st.save()
    return True


def _format_provision_user_prompt(fixtures: dict, task: str) -> str:
    """Compose the LLM-fallback user prompt from the assembled fixture
    set. Mirrors the layout the worker prompt expects."""
    parts: list[str] = [
        f"TASK CONTEXT:\n{task}",
        "",
        "Below are the repo signals you have to decide how to install "
        "its dependencies. Emit a recipe that uses the project's own "
        "documented commands. Reject any command outside the allowlist."
        " Emit `kind: none` if no install is needed (pure docs repo).",
        "",
    ]
    if fixtures["readme"]:
        parts += ["=== README (install-relevant slice) ===",
                  fixtures["readme"], ""]
    for name, text in fixtures["manifests"].items():
        parts += [f"=== {name} ===", text, ""]
    for rel, text in fixtures["workspace_manifests"]:
        parts += [f"=== {rel} (workspace child) ===", text, ""]
    for name, text in fixtures["workflows"]:
        parts += [f"=== .github/workflows/{name} ===", text, ""]
    if fixtures["contributing"]:
        parts += ["=== CONTRIBUTING / DEVELOPMENT docs ===",
                  fixtures["contributing"], ""]
    if fixtures["hit_ceiling"]:
        parts.append(
            "[fixture set was truncated to the 24KB budget — some "
            "files may be incomplete]")
    return "\n".join(parts)


async def phase_provision(repo_root: Path, st: State, caps: dict,
                           models: dict[str, str],
                           efforts: dict[str, str | None]) -> None:
    """Phase 1½: per-repo dependency *detection*.

    Runs after classify so a docs-only run can short-circuit. Five
    ordered steps (DESIGN §6½):

      1. Docs-only short-circuit. If classify returned only
         documentation categories, persist `kind: none` and return.
      2. `.leerie-setup.sh` hook if present.
      3. Synthesize a mise go-override from `go.mod` if needed.
      4. `mise install` at the repo root (reads .tool-versions natively
         and .nvmrc / .python-version / .ruby-version /
         rust-toolchain.toml via the image-set
         MISE_IDIOMATIC_VERSION_FILE_ENABLE_TOOLS env). Capture
         resolved versions via `mise ls --current --json`.
      5. Detect install commands: deterministic lockfile table first
         (emits all matches for polyglot repos), LLM fallback if the
         table abstains. Validate the recipe and persist it to
         st.data["provision"]["recipe"] for downstream workers to
         consult via prompt injection.

    Phase 1½ deliberately does NOT execute the install recipe at
    repo_root. The repo is bind-mounted from the host; writing
    `node_modules/` / `.venv/` / `target/` into it would clobber the
    host's checkout with linux-built artifacts on darwin hosts. Each
    worker runs installs in its own worktree against the shared
    cache instead (DESIGN §6½ "Worker-driven install").

    Naturally skipped on `--resume` because the entire fresh-run
    else-branch in `orchestrate()` is skipped.
    """
    log("phase 1½: detecting per-repo deps")
    st.data["current_phase"] = "phase 1½: provision"
    st.save()
    prov = st.data.setdefault("provision", {})
    log_dir = st.run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 1. Docs-only short-circuit.
    cats = set(st.data.get("categories") or [])
    if cats and cats <= _DOCS_ONLY_CATEGORIES:
        log("  docs-only task: skipping dep detection")
        prov["source"] = "skipped-docs-only"
        prov["recipe"] = [{"kind": "none", "command": [],
                           "working_dir": ".", "timeout_s": 0}]
        st.save()
        return

    # 2. Setup hook.
    await run_setup_hook(repo_root, log_dir, st)

    # 3. Synthesize a mise go override if go.mod lacks a sibling pin.
    override = synth_mise_go_override(repo_root, st.run_dir)
    if override is not None:
        log(f"  synthesized mise override at {override.name} "
            f"(go.mod → .go-version equivalent)")
    # Persist so a `--resume` after this point can re-export the env
    # var (orchestrate() re-reads provision state on resume).
    prov["override_file"] = str(override) if override is not None else None
    st.save()
    # Export MISE_OVERRIDE_CONFIG_FILENAMES into the orchestrator's
    # os.environ now so every downstream subprocess — `mise install`
    # below, the implementer/conformer `claude -p` workers (which
    # inherit os.environ via _invoke), and any `mise exec --` they
    # invoke from their worktrees — sees the synthesized go pin.
    # Without this, mise's discovery in the worktree wouldn't find the
    # synth (the override file lives under .leerie/, which isn't in the
    # worktree's tracked-file set) and `mise exec -- go ...` would
    # fall through to system PATH where Go isn't installed.
    if override is not None:
        os.environ["MISE_OVERRIDE_CONFIG_FILENAMES"] = str(override)

    # 4. mise install + version capture.
    await run_mise_install(repo_root, log_dir, st, override_file=override)
    versions = prov.get("mise_versions") or {}
    if versions:
        version_summary = ", ".join(
            f"{tool} {entries[0].get('version', '?')}"
            for tool, entries in sorted(versions.items())
            if isinstance(entries, list) and entries
        )
        if version_summary:
            log(f"  resolved versions: {version_summary}")

    # 5a. Detect install commands — table first.
    recipe = detect_recipe_from_lockfiles(repo_root)
    if recipe:
        prov["source"] = "table"
        log(f"  table emitted {len(recipe)} install command(s)")
    else:
        # 5b. LLM fallback with CRITIC-pattern mechanical feedback loop.
        log("  table abstained — invoking provision worker")
        fixtures = gather_provision_fixtures(repo_root)
        sys_prompt = load_prompt("provision")
        prov_up_parts: list[str] = [
            _format_provision_user_prompt(fixtures, st.data["task"])]

        async def _invoke_provision() -> dict:
            st.bump_workers(caps)
            return await claude_p(
                user_prompt="\n\n".join(prov_up_parts),
                system_prompt=sys_prompt,
                schema_key="provision",
                cwd=str(repo_root),
                allowed_tools=INSPECT_TOOLS,
                max_turns=30,
                autonomous=False,
                caps=caps, st=st,
                model=models.get("provision", MODEL_DEFAULT),
                effort=efforts.get("provision"),
                sid="provision",
                add_dirs=st.data.get("inspect_dirs") or None,
            )

        async def _on_prov_fb(fb: str) -> dict:
            if len(prov_up_parts) > 1:
                prov_up_parts[-1] = fb
            else:
                prov_up_parts.append(fb)
            return {}

        result, prov_warnings = await _run_checked_loop(
            invoke=_invoke_provision,
            check=lambda r: check_provision_output(r, repo_root),
            name="provision",
            max_rounds=caps["judgment_check_rounds"],
            make_feedback_prompt=_on_prov_fb,
        )
        if result is None:
            die("provision worker crashed and produced no result")
        for w in prov_warnings:
            log(f"  provision: {w}")
        recipe = result.get("recipe") or []
        prov["source"] = "llm"

    # Validate the recipe shape. §12 carve-out: any drift from the
    # schema or allowlist is rejected here, not in the prompt — even
    # though we no longer execute the recipe ourselves, workers will
    # see it via prompt injection and we don't want to ship them
    # malformed entries.
    try:
        validate_provision_recipe(recipe)
    except ValueError as e:
        die(f"provision recipe failed validation: {e}")

    # Add --break-system-packages to pip installs before persisting, so
    # every downstream consumer (baseline capture, prompt injection into
    # implementer/conformer workers) gets a recipe that actually runs on
    # the Debian-13 externally-managed system Python. See
    # _normalize_pip_installs.
    recipe = _normalize_pip_installs(recipe)

    prov["recipe"] = recipe
    st.save()

    log(f"  recipe detected ({len(recipe)} command(s), "
        f"source={prov['source']}) — workers will run installs in their worktrees")


def _select_best_planner_sample(
    samples: list[dict], repo_root: Path, domain: str,
) -> dict:
    """Mechanically select the best planner sample for a domain.

    Selection criteria (no LLM — avoids self-bias per ACL 2024):
      1. Fewest mechanical-check issues
      2. Tiebreak: most subtasks (more investigation = better coverage)
      3. Tiebreak: first sample (determinism device)
    """
    scored: list[tuple[int, int, int, dict]] = []
    for i, sample in enumerate(samples):
        issues = check_planner_output(sample, repo_root, domain)
        n_subtasks = len(sample.get("subtasks", []))
        # Lower issue count is better; higher subtask count is better.
        scored.append((len(issues), -n_subtasks, i, sample))
    scored.sort()
    winner = scored[0]
    if len(samples) > 1:
        log(f"  {domain}: multi-sample selected sample {winner[2]} "
            f"({winner[0]} issues, {-winner[1]} subtasks) from "
            f"{len(samples)} samples")
    return winner[3]


async def phase_plan(task: str, st: State, caps: dict,
                     models: dict[str, str],
                     efforts: dict[str, str | None]) -> list[dict]:
    """Phase 2: one planner per category, run in parallel (bounded by
    max_parallel). Each returns a JSON plan of granular subtasks."""
    log("phase 2: planning")
    st.data["current_phase"] = "phase 2: planning"
    st.save()
    cats = st.data["categories"]
    answers = st.data.get("answers", {})
    sot = answers.get("source_of_truth", "codebase")
    sys_prompt = load_prompt("planner")

    sem = asyncio.Semaphore(caps["max_parallel"])

    repo_root = Path(os.getcwd())

    # Task-referenced file extraction (CRITIC correlated-error breaker).
    # Injects a mechanically-extracted coverage checklist into the planner
    # prompt when the task string references files.  No-op otherwise.
    task_file_items = extract_task_file_structure(task, repo_root)
    task_file_section = (_format_task_file_structure(task_file_items)
                         if task_file_items else None)
    if task_file_section:
        log(f"  extracted {len(task_file_items)} structural items "
            "from task-referenced files")

    # P6 repo-map injection (DESIGN §5½ (P6)). Build the ranked subgraph seeded
    # from the task-referenced files identified above, and inject it into the
    # planner context.  When skip_repo_map is True the planner degrades
    # gracefully to the pre-existing grep/glob-only path.
    ctx_dict: dict = {
        "task": task,
        "source_of_truth": sot,
        "clarification_answers": answers,
        # confidence_rounds is the worker-internal evidence-gate bound (DESIGN
        # §8 planner gate). The orchestrator does not enforce it — the planner
        # bounds itself — but passing it in the context blob is what makes the
        # user-visible knob real.
        "confidence_rounds": caps["confidence_rounds"],
    }
    # The built global symbol graph is reused (once) for BOTH the planner ctx
    # (ranked to the task seeds) and the P1 recursion (re-ranked per node —
    # DESIGN §5½). None when skipped or the build fails → graceful degrade.
    repo_map: dict | None = None
    if not st.data.get("skip_repo_map"):
        try:
            repo_map = build_repo_map(repo_root, st.leerie_root)
            seed_files = (
                [str(Path(item.split(": ", 1)[0])) for item in task_file_items]
                if task_file_items else []
            )
            ranked = rank_repo_map(repo_map, seed_files, [])
            if ranked:
                ctx_dict["repo_map"] = ranked
                log(f"  injected ranked repo-map subgraph "
                    f"({len(ranked.splitlines())} files) into planner ctx")
        except Exception:
            repo_map = None  # degrade silently; planner runs without repo-map
    ctx = json.dumps(ctx_dict, indent=2)

    async def plan_one(category: str, sample_idx: int = 0) -> dict | None:
        async with sem:
            prefix = f"{CATEGORY_ABBREV[category]}-"
            sid = (f"planner-{category}-s{sample_idx}" if n_samples > 1
                   else f"planner-{category}")
            base_prompt = (
                f"DOMAIN: {category}\nID_PREFIX: {prefix}\n\n"
                f"CONTEXT:\n{ctx}\n\n"
                f"Decompose the {category} aspect of this task into a JSON plan "
                "per your instructions. Every subtask id MUST start with "
                f"`{prefix}` (e.g., `{prefix}001`).")
            # Mutable list: [base, task_file_section?, feedback?].
            # task_file_section persists across rounds (external
            # reference); feedback replaces on each round.
            up_parts: list[str] = [base_prompt]
            if task_file_section:
                up_parts.append(task_file_section)
            feedback_slot: list[str] = []

            async def _invoke() -> dict:
                st.bump_workers(caps)
                return await claude_p(
                    user_prompt="\n\n".join(up_parts + feedback_slot),
                    system_prompt=sys_prompt,
                    schema_key="planner", cwd=str(repo_root),
                    allowed_tools=INSPECT_TOOLS, max_turns=100,
                    autonomous=False, caps=caps, st=st,
                    model=models["planner"],
                    effort=efforts["planner"],
                    sid=sid,
                    add_dirs=st.data.get("inspect_dirs") or None)

            async def _on_feedback(fb: str) -> dict:
                feedback_slot.clear()
                feedback_slot.append(fb)
                return {}

            def _check_planner(r: dict) -> list[str]:
                issues = check_planner_output(r, repo_root, category)
                if task_file_items:
                    issues.extend(check_task_file_coverage(
                        task_file_items, r.get("subtasks", [])))
                return issues

            result, gate_warnings = await _run_checked_loop(
                invoke=_invoke,
                check=_check_planner,
                name=f"planner-{category}",
                max_rounds=caps["planner_check_rounds"],
                make_feedback_prompt=_on_feedback,
            )
            if result is None:
                for w in gate_warnings:
                    log(f"  planner-{category}: {w}")
                if n_samples > 1:
                    log(f"  planner-{category}: crashed and produced "
                        "no result")
                    return None
                die(f"planner for {category} crashed and produced "
                    "no result")
            for w in gate_warnings:
                log(f"  planner-{category}: {w}")
            return result

    n_samples = caps.get("planner_samples", 1)

    if n_samples <= 1:
        # Fast path: identical to single-sample behavior.
        plans = await gather_or_cancel(*(plan_one(c) for c in cats))
    else:
        # Multi-sample: run N independent invocations per category,
        # then mechanically select the cleanest sample per category.
        log(f"  ({n_samples} samples per domain — multi-sample mode)")
        all_coros = []
        coro_keys: list[tuple[str, int]] = []
        for c in cats:
            for s_idx in range(n_samples):
                all_coros.append(plan_one(c, s_idx))
                coro_keys.append((c, s_idx))
        all_results = await gather_or_cancel(*all_coros)

        by_category: dict[str, list[dict]] = {}
        for (c, _s_idx), result in zip(coro_keys, all_results):
            if result is not None:
                by_category.setdefault(c, []).append(result)

        plans = []
        for c in cats:
            samples = by_category.get(c, [])
            if not samples:
                die(f"all {n_samples} planner samples for {c} crashed "
                    "— no plan produced")
            best = _select_best_planner_sample(
                samples, repo_root, c)
            plans.append(best)

    # DESIGN §5½ *Wire-in to phase_plan*: depth=0 entry so the depth cap
    # counts from the planner level, not from inside recursive_decompose itself.
    log("  expanding subtasks via recursive_decompose (P1 Layer C)")
    for plan in plans:
        first_pass = plan.get("subtasks", [])
        if not first_pass:
            continue
        leaves: list[dict] = []
        for subtask in first_pass:
            expanded = await recursive_decompose(
                subtask, 0, st, caps, models, efforts, repo_root,
                repo_map=repo_map)
            leaves.extend(expanded)
        plan["subtasks"] = leaves

    for category, plan in zip(cats, plans):
        n = len(plan.get("subtasks", []))
        status = plan.get("status", "ready")
        if status == "blocked":
            gap = (plan.get("confidence", {}) or {}).get("gap_to_close", {})
            log(f"  {category}: BLOCKED (planner gate) — {n} subtask(s); "
                f"gap: {gap}")
        else:
            log(f"  {category}: {n} subtask(s)")
    return list(plans)


def _promote_external_collisions(plans: list[dict]) -> int:
    """In-place: for every `requires` entry with `extent: external` whose
    tag is in some plan's `provides`, rewrite the entry to `extent:
    in_plan`. The in-plan producer wins so a planner cannot unilaterally
    bypass a real producer in another domain — DESIGN §5 `requires.extent`
    collision rule.

    Returns the count of promoted entries (for logging). Mutates the
    plans list in place; `reason` is preserved on the promoted entry
    for telemetry but is no longer load-bearing once `extent` is
    `in_plan`."""
    all_provides: set[str] = set()
    for plan in plans:
        for s in plan.get("subtasks", []):
            all_provides.update(s.get("provides", []))
    promoted = 0
    for plan in plans:
        for s in plan.get("subtasks", []):
            for entry in s.get("requires", []):
                if (isinstance(entry, dict)
                        and entry.get("extent") == "external"
                        and entry.get("tag") in all_provides):
                    entry["extent"] = "in_plan"
                    promoted += 1
    return promoted


def _collect_external_preconditions(plans: list[dict]) -> list[dict]:
    """Walk plans and return the deduped list of planner-declared
    `extent: external` requires entries — the `preconditions` surface
    persisted in `plan.json` (DESIGN §5 `requires.extent`).

    Run AFTER `_promote_external_collisions` so any entry that had an
    in-plan producer has already been demoted out of the external set.
    Each output entry is `{tag, reasons: [{sid, reason}, …],
    originating_subtasks: [sid, …]}`, deduped by tag and stable-sorted
    for deterministic output."""
    by_tag: dict[str, dict] = {}
    for plan in plans:
        for s in plan.get("subtasks", []):
            sid = s.get("id", "")
            for entry in s.get("requires", []):
                if (not isinstance(entry, dict)
                        or entry.get("extent") != "external"):
                    continue
                tag = entry.get("tag", "")
                reason = (entry.get("reason") or "").strip()
                if not tag:
                    continue
                bucket = by_tag.setdefault(tag, {
                    "tag": tag,
                    "reasons": [],
                    "originating_subtasks": [],
                })
                if sid not in bucket["originating_subtasks"]:
                    bucket["originating_subtasks"].append(sid)
                bucket["reasons"].append({"sid": sid, "reason": reason})
    return [by_tag[t] for t in sorted(by_tag)]


def _compute_unresolved_requires(plans: list[dict]) -> list[dict]:
    """Pure-Python lookup: every (sid, tag, domain) where a subtask
    `requires` a capability tag that no subtask in the merged plan
    `provides`. Mirrors the set logic in validate_plan() but emits the
    data rather than raising. Used by phase_reconcile to assemble the
    reconciler worker's input and (after the worker applies its
    resolutions) to verify the output actually closed every gap.

    Only `extent: in_plan` entries are checked — `extent: external` is
    a planner-declared out-of-graph prerequisite (DESIGN §5
    `requires.extent`) and is collected separately by
    `_collect_external_preconditions`. Caller is expected to have run
    `_promote_external_collisions` first so any external entry with an
    in-plan producer has already been demoted.

    `domain` names the producing planner-domain of `sid` — surfaced in
    the abort message so the user can see which planner held the
    dangling dependency. Reconciler input is read-or-ignore on the
    field; it's there for the orchestrator's own rendering."""
    all_provides: set[str] = set()
    sid_domain: dict[str, str] = {}
    for plan in plans:
        for s in plan.get("subtasks", []):
            all_provides.update(s.get("provides", []))
            sid_domain[s["id"]] = plan.get("domain", "<unknown>")
    unresolved: list[dict] = []
    for plan in plans:
        for s in plan.get("subtasks", []):
            for entry in s.get("requires", []):
                if not isinstance(entry, dict):
                    continue
                if entry.get("extent") != "in_plan":
                    continue
                tag = entry.get("tag", "")
                if tag and tag not in all_provides:
                    unresolved.append({
                        "sid": s["id"], "tag": tag,
                        "domain": sid_domain[s["id"]],
                    })
    return unresolved


def _prune_dead_subtasks(
    plans: list[dict],
    unresolvable_entries: list[dict],
) -> list[str]:
    """Dead-subtask elimination: remove subtasks whose EVERY in_plan
    requires tag is in the reconciler's unresolvable set (DESIGN §5).

    Fires only when at least one plan has 0 subtasks (the "constant
    fold" that makes the dead subtasks detectable). Mirrors the
    conditional_drops cleanup pattern: prunes depends_on references
    from surviving subtasks.

    Returns sorted list of pruned sids (empty if nothing pruned).
    Mutates `plans` in place."""
    if not any(not p.get("subtasks") for p in plans):
        return []

    unresolvable_pairs: set[tuple[str, str]] = {
        (u["sid"], u["tag"]) for u in unresolvable_entries
    }
    unresolvable_sids: set[str] = {u["sid"] for u in unresolvable_entries}

    dead_sids: set[str] = set()
    for plan in plans:
        for s in plan.get("subtasks", []):
            sid = s.get("id", "")
            if sid not in unresolvable_sids:
                continue
            in_plan_tags = [
                e.get("tag", "")
                for e in (s.get("requires") or [])
                if isinstance(e, dict) and e.get("extent") == "in_plan"
                and e.get("tag")
            ]
            if not in_plan_tags:
                continue
            if all((sid, tag) in unresolvable_pairs for tag in in_plan_tags):
                dead_sids.add(sid)

    if not dead_sids:
        return []

    for plan in plans:
        plan["subtasks"] = [
            s for s in plan.get("subtasks", [])
            if s.get("id") not in dead_sids
        ]

    for plan in plans:
        for s in plan.get("subtasks", []):
            deps = s.get("depends_on") or []
            pruned_deps = [d for d in deps if d not in dead_sids]
            if len(pruned_deps) != len(deps):
                s["depends_on"] = pruned_deps

    return sorted(dead_sids)


def _find_oversized_added_subtasks(plans: list[dict]) -> list[dict]:
    """Pure-Python lookup: every reconciler-added subtask (carrying
    `_added_by_reconciler: true`, stamped by `_apply_reconciler_output`)
    whose `size` is `"large"`. Mirrors the planner-side prohibition the
    planner prompt already states ("Never emit `size: large`") and the
    final `validate_plan` backstop — but fires earlier so the size
    gate in `phase_reconcile` can respawn the reconciler with structured
    feedback before the post-merge validator gets a chance to die().

    Returns subtask dicts as-is (not copies); the size gate only needs
    them for prompt rendering, not mutation. Empty list means the size
    gate short-circuits — no extra reconciler spawn."""
    oversized: list[dict] = []
    for plan in plans:
        for s in plan.get("subtasks", []):
            if not s.get("_added_by_reconciler"):
                continue
            if (s.get("size") or "").lower() == "large":
                oversized.append(s)
    return oversized


def _apply_reconciler_output(
    plans: list[dict],
    output: dict,
    attempt_1_renames: list[dict] | None = None,
) -> list[dict]:
    """Mutate `plans` per the reconciler's output. On success, returns
    the same `plans` list (with in-place edits on existing subtasks
    plus an appended `_reconciler` pseudo-plan for any added_subtasks).
    On an id-collision in `added_subtasks`, a missing-id reference in
    `dependency_edges` / `merged_subtasks`, or a `conditional_drops`
    target that carries `_added_by_reconciler: true`, calls `die()` —
    the pseudo-plan is never appended and `plans` is left in an
    undefined state (callers must deep-copy before applying if they
    need clean reversion on failure; the cycle-resolution retry loop
    does this).

    `attempt_1_renames` is the attempt-1 reconciler output's `renames`
    list, used by the `dropped_requires` apply step (only) to accept
    either form of an unresolved tag — the post-mutation tag that
    attempt 1's rename produced, or the pre-revert tag the consumer's
    `requires` actually holds after the retry's revert restored the
    pre-mutation plans. Mirrors the dual-tag acceptance in
    `_validate_unresolved_must_include` so the validator and the apply
    step cannot disagree — the same symmetry repair commit cd244cf
    applied to renames/added_provides/added_subtasks. `None` on
    attempt 1 (no revert in scope, strict match is correct).

    Seven action arrays consumed here, in order:

    1. `renames` rewrite a single `requires` entry on the named subtask.
    2. `added_provides` append a tag to the named subtask's `provides`.
    3. `added_subtasks` become a new domain="_reconciler" plan appended
       to the list — schedule() flattens by id, so domain only affects
       the per-domain log line. Each added subtask is stamped with
       `_added_by_reconciler: true` for downstream traceability
       (size gate + validate_plan error wording + conditional_drops'
       planner-only guard rely on it).
    4. `conditional_drops` remove a planner-emitted consumer subtask
       whose own `intent` declared it conditional on an unresolvable
       in_plan precondition (resolution op: converts the planner's
       prose "no-op if X" into a structured drop the capability graph
       can express). Runs after `added_subtasks` so the
       `_added_by_reconciler` guard catches any attempt to drop a
       reconciler-invented subtask — that op is restricted to
       planner-authored consumers. Runs before the cycle-breaking ops
       so they see the post-drop graph. The orchestrator removes the
       sid from its plan and prunes downstream `depends_on` references
       to it; the audit write (sid → reason + originating tag) happens
       in `phase_reconcile` after this function returns. Silent no-op
       on missing sid (mirrors `renames` / `dropped_requires`).
    5. `dropped_requires` remove an `extent: in_plan` requires entry
       (resolution op AND cycle-breaking op: used when the requirement
       was over-specified — an aggregate, coarser synonym, or
       authoring-time decision the same subtask itself records, not a
       code artifact another subtask produces. The consumer stays in
       the plan; only the bad edge is removed). Apply mechanics are
       identical in either mode — phase_reconcile's must-include
       validator accepts it in unresolved-tag retry mode too. Silent
       no-op on missing sid/entry (mirrors `renames`).
    6. `dependency_edges` append a planner-declared `depends_on` edge
       between two existing subtasks (cycle-breaking op: used when both
       sides legitimately need each other and one ordering is right).
       Both ids must exist — `die()` on a missing id. Also dies on
       `from == to` (self-loop — a subtask cannot depend on itself).
    7. `merged_subtasks` collapse two existing subtasks into one
       (cycle-breaking op: used when the cycle reflects genuine
       authoring overlap — signal: shared `files_likely_touched` between
       SCC members). The surviving subtask (`into`) inherits the union
       of both halves' provides/requires/depends_on/files_likely_touched
       with self-references dropped, and stamps
       `_merged_from: [<absorbed-id>, ...]`. Downstream subtasks'
       `depends_on` references to `from` are rewritten to `into`.
       Tag-based `requires` need no rewriting (`into` carries the union
       of provides). Both ids must exist and differ — `die()` on
       violation.

    The `unresolvable` array is not consumed here — phase_reconcile()
    inspects it directly before calling this helper."""
    # Index subtasks by id for O(1) mutation. Modifying the subtask
    # dict mutates the underlying plan because dicts are shared by
    # reference; no need to write the plan back.
    by_id: dict[str, dict] = {}
    for plan in plans:
        for s in plan.get("subtasks", []):
            by_id[s["id"]] = s

    for r in output.get("renames", []):
        s = by_id.get(r["sid"])
        if s is None:
            continue  # reconciler named a sid that doesn't exist; ignore
        # `requires` entries are objects `{tag, extent, reason?}`
        # (DESIGN §5 `requires.extent`); rewrite the `tag` field on the
        # entry whose tag matches `from`, preserve extent/reason. The
        # `extent: in_plan` guard makes the architectural invariant
        # load-bearing: the reconciler only ever reasons about in_plan
        # tags (externals are filtered out before its input is built),
        # so a rename must not mutate an external entry even if its tag
        # happens to collide.
        for entry in s.get("requires", []) or []:
            if (isinstance(entry, dict)
                    and entry.get("extent") == "in_plan"
                    and entry.get("tag") == r["from"]):
                entry["tag"] = r["to"]

    for ap in output.get("added_provides", []):
        s = by_id.get(ap["sid"])
        if s is None:
            continue
        provs = s.setdefault("provides", [])
        if ap["tag"] not in provs:
            provs.append(ap["tag"])

    added = output.get("added_subtasks", [])
    if added:
        # Fail loud on id collisions. schedule() merges all subtasks
        # into a single dict keyed by id (leerie.py: see `schedule`),
        # so a duplicate id would silently overwrite a real subtask and
        # vanish its requires/provides/depends_on from the DAG. The
        # reconciler's prompt warns against this, but prompts are
        # advisory per CLAUDE.md "The central principle" — the
        # mechanical guarantee lives here. Two failure modes to cover:
        #   1. existing-vs-added: an added_subtask id collides with a
        #      subtask the planners already produced.
        #   2. added-vs-added: the reconciler emitted the same id twice
        #      within added_subtasks itself. Both halves get silently
        #      collapsed by schedule()'s dict-flatten if not caught here.
        existing_ids = {s["id"] for s in by_id.values()}
        ext_collisions = sorted({s["id"] for s in added if s["id"] in existing_ids})
        seen: set[str] = set()
        self_collisions: list[str] = []
        for s in added:
            sid = s["id"]
            if sid in seen and sid not in self_collisions:
                self_collisions.append(sid)
            seen.add(sid)
        if ext_collisions or self_collisions:
            parts = []
            if ext_collisions:
                parts.append("collide with existing subtasks: "
                             + ", ".join(ext_collisions))
            if self_collisions:
                parts.append("are duplicated within added_subtasks: "
                             + ", ".join(sorted(self_collisions)))
            die(
                "reconciler proposed added_subtasks whose id(s) "
                + "; ".join(parts)
                + ". The scheduler merges by id; an unchecked collision "
                "would silently drop one of the subtasks from the DAG. "
                "Refine the task or re-run."
            )
        # Stamp `_added_by_reconciler: true` on every added subtask.
        # The size gate + validate_plan's "planner vs reconciler" error
        # wording rely on this flag; stamping it here (rather than
        # trusting the model to set it in its response) makes the
        # provenance signal a mechanical guarantee that a defective
        # model cannot bypass by emitting `false`.
        for s in added:
            s["_added_by_reconciler"] = True
        plans.append({
            "domain": "_reconciler",
            "status": "ready",
            "subtasks": added,
        })
        # Re-index so the conditional_drops + cycle-breaking ops below
        # can find added_subtasks by id (e.g. a dependency_edges entry
        # could legitimately reference an added_subtask). conditional_drops
        # also reads `by_id` to enforce its `_added_by_reconciler` guard.
        for s in added:
            by_id[s["id"]] = s

    # --- Resolution op #4: conditional_drops (DESIGN §5) ---
    # Runs after added_subtasks (so the `_added_by_reconciler` guard
    # below catches any attempt to drop a reconciler-invented subtask;
    # this op is restricted to planner-authored consumers — a reconciler-
    # added subtask has no planner prose to convert into a structured
    # drop). Runs before the cycle-breaking ops (so they see the post-
    # drop graph; a depends_on/merge targeting a dropped sid would be
    # incoherent and the missing-id guards on those ops would catch it).
    for cd in output.get("conditional_drops", []):
        sid = cd["sid"]
        s = by_id.get(sid)
        if s is None:
            continue  # silent no-op, mirrors `renames` / `dropped_requires`
        if s.get("_added_by_reconciler"):
            die(
                f"reconciler proposed conditional_drops on {sid!r} which "
                "was added by the reconciler itself; this op is restricted "
                "to planner-authored consumers (the planner's prose "
                "conditionality is what conditional_drops converts into a "
                "structured drop — reconciler-added subtasks have no such "
                "prose). Refine the task or re-run."
            )
        # Remove from its plan (dicts are shared by reference with
        # `by_id`, so we filter every plan's subtasks list — mirrors
        # the merged_subtasks removal below).
        for plan in plans:
            plan["subtasks"] = [
                t for t in plan.get("subtasks", []) if t.get("id") != sid
            ]
        del by_id[sid]
        # Prune downstream `depends_on` references — a dropped subtask
        # can no longer satisfy any dependent. Tag-based `requires`
        # need no rewriting here: if the dropped subtask's `provides`
        # tag was depended on by another subtask, that becomes a fresh
        # unresolved entry and the unresolved-retry loop handles it
        # (or surfaces as `unresolvable` on the second pass).
        for other_sid, other_s in by_id.items():
            deps = other_s.get("depends_on") or []
            if sid in deps:
                other_s["depends_on"] = [d for d in deps if d != sid]

    # --- Cycle-breaking ops (apply after the resolution ops above) ---

    # Build a (sid, post_tag) -> pre_tag map from attempt-1's renames
    # so dropped_requires applied in a retry context can match either
    # form of the unresolved tag. Empty dict in attempt 1
    # (`attempt_1_renames is None`) → the comprehension over `.get()`
    # never returns a pre_tag, so the strict-equality branch is the
    # only one that fires — preserves attempt-1 behavior.
    pre_revert_tag_by_sid_tag: dict[tuple[str, str], str] = {}
    if attempt_1_renames:
        for r in attempt_1_renames:
            sid_r = r.get("sid")
            from_tag = r.get("from")
            to_tag = r.get("to")
            if sid_r and from_tag and to_tag:
                pre_revert_tag_by_sid_tag[(sid_r, to_tag)] = from_tag

    for dr in output.get("dropped_requires", []):
        s = by_id.get(dr["sid"])
        if s is None:
            continue  # silent no-op, mirrors `renames`
        # Accept either the post-mutation tag (dr["tag"]) or the
        # pre-revert tag (looked up via attempt_1_renames). After the
        # retry's revert, the consumer's `requires` holds the
        # pre-revert form; if the model emits dropped_requires keyed
        # on the post-mutation form, fall back to the pre-revert tag.
        # Mirror of the validator's dual-tag acceptance — keeps the
        # apply step and the must-include validator from disagreeing.
        candidate_tags = {dr["tag"]}
        pre_tag = pre_revert_tag_by_sid_tag.get((dr["sid"], dr["tag"]))
        if pre_tag is not None:
            candidate_tags.add(pre_tag)
        reqs = s.get("requires") or []
        s["requires"] = [
            entry for entry in reqs
            if not (isinstance(entry, dict)
                    and entry.get("extent") == "in_plan"
                    and entry.get("tag") in candidate_tags)
        ]

    for de in output.get("dependency_edges", []):
        frm, to = de["from"], de["to"]
        if frm == to:
            die(
                "reconciler proposed dependency_edges with from == to "
                f"({frm!r}); a subtask cannot depend on itself. Refine "
                "the task or re-run."
            )
        if frm not in by_id or to not in by_id:
            missing = [x for x in (frm, to) if x not in by_id]
            die(
                "reconciler proposed dependency_edges referencing "
                f"non-existent subtask id(s): {', '.join(sorted(missing))}. "
                "Both endpoints must be existing subtasks. Refine the task "
                "or re-run."
            )
        target = by_id[to]
        deps = target.setdefault("depends_on", [])
        if frm not in deps:
            deps.append(frm)

    for ms in output.get("merged_subtasks", []):
        into_id, from_id = ms["into"], ms["from"]
        if into_id == from_id:
            die(
                "reconciler proposed merged_subtasks with into == from "
                f"({into_id!r}); merge endpoints must differ."
            )
        if into_id not in by_id or from_id not in by_id:
            missing = [x for x in (into_id, from_id) if x not in by_id]
            die(
                "reconciler proposed merged_subtasks referencing "
                f"non-existent subtask id(s): {', '.join(sorted(missing))}. "
                "Both into and from must be existing subtasks. Refine the "
                "task or re-run."
            )
        into_s = by_id[into_id]
        from_s = by_id[from_id]

        # provides: union (dedup, order-preserving).
        merged_provides = list(into_s.get("provides", []) or [])
        for tag in (from_s.get("provides") or []):
            if tag not in merged_provides:
                merged_provides.append(tag)
        into_s["provides"] = merged_provides

        # requires: union, then drop self-references (an entry whose tag
        # is now in the merged provides is satisfied by the merged unit
        # itself — would be a self-loop in the graph).
        seen_req: set[tuple[str, str]] = set()
        merged_requires = []
        for entry in (list(into_s.get("requires", []) or [])
                      + list(from_s.get("requires", []) or [])):
            if not isinstance(entry, dict):
                continue
            tag = entry.get("tag", "")
            extent = entry.get("extent", "")
            key = (tag, extent)
            if key in seen_req:
                continue
            seen_req.add(key)
            # Self-reference cleanup: only drop in_plan entries whose
            # tag is now produced by the merged unit. external entries
            # are out-of-graph and stay regardless.
            if extent == "in_plan" and tag in merged_provides:
                continue
            merged_requires.append(entry)
        into_s["requires"] = merged_requires

        # depends_on: union, minus `from` itself (would be a self-loop),
        # dedup, order-preserving.
        merged_deps: list[str] = []
        for dep in (list(into_s.get("depends_on", []) or [])
                    + list(from_s.get("depends_on", []) or [])):
            if dep == from_id or dep == into_id:
                continue
            if dep not in merged_deps:
                merged_deps.append(dep)
        into_s["depends_on"] = merged_deps

        # files_likely_touched: union, order-preserving dedup.
        merged_files: list[str] = []
        for f in (list(into_s.get("files_likely_touched", []) or [])
                  + list(from_s.get("files_likely_touched", []) or [])):
            if f not in merged_files:
                merged_files.append(f)
        into_s["files_likely_touched"] = merged_files

        # success_criteria_seed: optional override; default to
        # concatenation so both halves' criteria survive into the
        # implementer's spec.
        override_scs = ms.get("success_criteria_seed")
        if override_scs:
            into_s["success_criteria_seed"] = override_scs
        else:
            into_scs = into_s.get("success_criteria_seed", "") or ""
            from_scs = from_s.get("success_criteria_seed", "") or ""
            if into_scs and from_scs:
                into_s["success_criteria_seed"] = (
                    f"{into_scs} AND {from_scs}")
            elif from_scs:
                into_s["success_criteria_seed"] = from_scs
            # else: keep into's (possibly empty) SCS

        # title / intent: optional overrides; default to keep into's.
        if ms.get("title"):
            into_s["title"] = ms["title"]
        if ms.get("intent"):
            into_s["intent"] = ms["intent"]

        # _merged_from telemetry — append so a chain of merges is
        # traceable (merge A into B, then B into C → C carries [A, B]).
        merged_from = into_s.setdefault("_merged_from", [])
        if from_id not in merged_from:
            merged_from.append(from_id)
        # If `from` itself absorbed others earlier, carry their ids too.
        for prior in (from_s.get("_merged_from") or []):
            if prior not in merged_from:
                merged_from.append(prior)

        # Remove `from` from its plan. Dicts are shared by reference
        # with `by_id`, so we filter every plan's subtasks list.
        for plan in plans:
            plan["subtasks"] = [
                s for s in plan.get("subtasks", []) if s.get("id") != from_id
            ]
        del by_id[from_id]

        # Rewrite downstream depends_on references to `from` → `into`.
        # Tag-based requires need no rewriting: they match by tag, and
        # `into` now carries the union of provides. Dedup after the
        # rewrite in case a subtask already depended on `into`.
        for sid, s in by_id.items():
            deps = s.get("depends_on") or []
            if from_id in deps:
                new_deps: list[str] = []
                for dep in deps:
                    dep = into_id if dep == from_id else dep
                    if dep not in new_deps and dep != sid:
                        new_deps.append(dep)
                s["depends_on"] = new_deps

    return plans


async def phase_reconcile(plans: list[dict], task: str, st: State,
                          caps: dict, models: dict[str, str],
                          efforts: dict[str, str | None]) -> list[dict]:
    """Phase 2½: reconcile cross-domain capability-tag drift between
    parallel planners (DESIGN §5, §14). Short-circuits when planners
    agreed; otherwise runs one reconciler worker whose output is applied
    mechanically. Genuinely unresolvable gaps die.

    Returns the (possibly mutated) `plans` list, ready for `schedule()`."""
    # Pre-condition: subtask ids are globally unique across plans. The
    # planner prompt tells each domain to scope ids to itself with a
    # domain-prefix, and the 8 CATEGORIES map to distinct prefixes
    # (leerie.py: CATEGORIES / _ID_PREFIXES), so in practice this
    # invariant holds. But prompts are advisory per CLAUDE.md; if a
    # planner ignores the rule, schedule()'s dict-flatten (line ~2997:
    # `subtasks[s["id"]] = s`) would silently overwrite, vanishing the
    # loser's requires/provides/depends_on from the DAG — the same
    # silent-data-loss failure class as the reconciler-output collisions
    # caught downstream. Catch it here, before any reconciler mutation
    # and before the short-circuit (a collision that doesn't manifest as
    # an unresolved `requires` would otherwise slip through).
    id_owners: dict[str, list[str]] = {}
    for plan in plans:
        domain = plan.get("domain", "<unknown>")
        for s in plan.get("subtasks", []):
            id_owners.setdefault(s["id"], []).append(domain)
    cross_collisions = {sid: owners for sid, owners in id_owners.items()
                        if len(owners) > 1}
    if cross_collisions:
        bullets = "\n".join(
            f"  • {sid!r} emitted by: {', '.join(owners)}"
            for sid, owners in sorted(cross_collisions.items())
        )
        die(
            "planner-vs-planner subtask id collision(s):\n"
            f"{bullets}\n"
            "Planners must emit globally unique subtask ids — by "
            "convention, each domain prefixes its ids with the domain "
            "(feat-, test-, bugfix-, …). schedule()'s by-id merge "
            "would otherwise silently drop one of the subtasks from "
            "the DAG. Refine the task or re-run."
        )

    # Apply the DESIGN §5 `requires.extent` mechanical passes BEFORE
    # computing the unresolved set:
    #   1. Promote `external` entries whose tag is in some plan's
    #      `provides` to `in_plan` — the real producer wins.
    #   2. Collect remaining `external` entries into the preconditions
    #      list, persisted via st so write_plan can surface it in
    #      plan.json. Externals never enter the reconciler's queue.
    promoted = _promote_external_collisions(plans)
    if promoted:
        log(f"phase 2½: promoted {promoted} external requires entry/entries "
            "to in_plan (an in-plan provider exists)")
    preconditions = _collect_external_preconditions(plans)
    st.data["external_preconditions"] = preconditions
    st.save()
    if preconditions:
        log(f"phase 2½: collected {len(preconditions)} external precondition(s) "
            "(planner-declared out-of-graph requirements — will surface in "
            "plan.json's `preconditions` section)")

    unresolved = _compute_unresolved_requires(plans)
    if not unresolved:
        # Common-case short-circuit: every `requires` already has a
        # producer. No worker call needed.
        return plans

    log(f"phase 2½: reconciling {len(unresolved)} cross-domain "
        f"capability-tag mismatch(es)")
    st.data["current_phase"] = "phase 2½: reconcile"
    st.save()

    # Build the reconciler's input. The worker sees the task, the
    # categories that contributed subtasks, every subtask's id/title/
    # intent/provides/requires (omit other fields to keep context small),
    # and the precomputed unresolved set.
    #
    # `requires` is flattened to bare tag strings here, dropping any
    # `extent: external` entries entirely. The reconciler reasons
    # purely about graph edges (DESIGN §5); externals are out-of-graph
    # by planner declaration and surface via `preconditions` in
    # plan.json, not through the reconciler. Keeping the view simple
    # also matches the worked example in prompts/reconciler.md (bare
    # strings).
    categories: list[str] = []
    subtask_views: list[dict] = []
    for plan in plans:
        domain = plan.get("domain")
        if domain and domain not in categories and domain != "_reconciler":
            categories.append(domain)
        for s in plan.get("subtasks", []):
            in_plan_tags = [
                e.get("tag", "") for e in (s.get("requires") or [])
                if isinstance(e, dict) and e.get("extent") == "in_plan"
                and e.get("tag")
            ]
            subtask_views.append({
                "id": s.get("id", ""),
                "title": s.get("title", ""),
                "intent": s.get("intent", ""),
                # `depends_on` and `files_likely_touched` are surfaced so the
                # reconciler can reason about ordering and file-overlap
                # signals when its first attempt closes a cycle and the
                # retry prompt asks it to revise. Without them, the model
                # has no structural input for picking between
                # dropped_requires / dependency_edges / merged_subtasks.
                "depends_on": list(s.get("depends_on", []) or []),
                "files_likely_touched": list(
                    s.get("files_likely_touched", []) or []),
                "provides": list(s.get("provides", []) or []),
                "requires": in_plan_tags,
            })
    payload = {
        "task": task,
        "categories": categories,
        "subtasks": subtask_views,
        "unresolved_requires": unresolved,
    }

    sys_prompt = load_prompt("reconciler")
    user_prompt = (
        "RECONCILER INPUT:\n" + json.dumps(payload, indent=2) +
        "\n\nResolve every unresolved_requires entry per your "
        "instructions and emit the eight-array JSON output."
    )

    # Snapshot the pre-mutation plans + pre-mutation providers map. The
    # snapshot is used both for clean reversion between retry attempts
    # and for the recommendation heuristic's "speculative rename"
    # detection (case 3): a rename's `from` tag is speculative if it
    # had no pre-reconcile producer.
    pre_plans_snapshot = copy.deepcopy(plans)
    pre_subtasks_snapshot: dict[str, dict] = {}
    for plan in pre_plans_snapshot:
        for s in plan.get("subtasks", []):
            pre_subtasks_snapshot[s["id"]] = s
    pre_providers: dict[str, list[str]] = {}
    for sid, s in pre_subtasks_snapshot.items():
        for cap in s.get("provides", []) or []:
            pre_providers.setdefault(cap, []).append(sid)

    async def _spawn_reconciler(up: str) -> dict:
        st.bump_workers(caps)
        return await claude_p(
            user_prompt=up, system_prompt=sys_prompt,
            schema_key="reconciler", cwd=os.getcwd(),
            allowed_tools=INSPECT_TOOLS, max_turns=30,
            autonomous=False, caps=caps, st=st,
            model=models["reconciler"], effort=efforts["reconciler"],
            sid="reconciler",
            add_dirs=st.data.get("inspect_dirs") or None,
        )

    def _check_unresolvable(out: dict) -> None:
        """Fail closed on unresolvable BEFORE mutating anything — the
        user gets the worker's diagnosis without phantom mutations on
        disk. Used in both attempts."""
        unresolvable = out.get("unresolvable", []) or []
        if not unresolvable:
            return
        sid_domain = {u["sid"]: u["domain"] for u in unresolved}
        bullets = "\n".join(
            f"  • {sid_domain.get(u['sid'], '<unknown>')}/{u['sid']} "
            f"requires '{u['tag']}': {u['reason']}"
            for u in unresolvable
        )
        die(
            f"reconciler could not resolve {len(unresolvable)} "
            f"capability-tag dependency/dependencies:\n{bullets}\n"
            "Each dependency is a planner-coverage gap: the consuming "
            "planner-domain emitted `requires` for a capability no "
            "other planner's domain produced. A common cause is a "
            "scope disagreement — two planners reading the task "
            "differently. To unblock:\n"
            "  • Refine the task description to make the disputed "
            "scope explicit (e.g., name the missing capability or the "
            "surface it lives on), and re-run.\n"
            "  • Or narrow scope with `--source-of-truth codebase` so "
            "planners reading repo docs stop treating them as a "
            "feature checklist."
        )

    def _record_conditional_drops(out: dict) -> None:
        """Persist each conditional_drops entry to
        st.data["conditional_drops"] (keyed by sid → reason + the tag
        whose resolution motivated the drop). Called after each
        successful _apply_reconciler_output. Distinct from
        st.data["dropped_subtasks"] which records off-tree soft-drops
        from filter_offtree_subtasks (phase 3) — same audit shape,
        different cause, separately auditable.

        The from_unresolved_tag lookup uses the closure-scoped
        `unresolved` set computed upstream: every consumer that ends
        up in conditional_drops had at least one unresolved tag in
        that set, and the lookup remembers which tag's resolution
        the drop addressed. If a sid has multiple unresolved tags
        (rare — most planner consumers have one unmet precondition
        each), the first is recorded; the rest are implied by the
        sid removal.

        Wholesale-replaces st.data["conditional_drops"] on every
        call (mirrors how _collect_external_preconditions wholesale-
        replaces st.data["external_preconditions"] at the adjacent
        retry sites — see this file's "...keeps the re-run idempotent"
        docstring on that helper). Per-sid overwrite would leak stale
        attempt-1 entries when a retry chain (size, cycle, unresolved)
        reverts `plans` but the audit field isn't reverted. An empty
        drops list correctly clears any prior attempt's entries."""
        drops = out.get("conditional_drops") or []
        sid_first_tag = {}
        for u in unresolved:
            sid_first_tag.setdefault(u["sid"], u["tag"])
        st.data["conditional_drops"] = {
            cd["sid"]: {
                "reason": cd.get("reason", ""),
                "from_unresolved_tag": sid_first_tag.get(cd["sid"], ""),
            }
            for cd in drops
            if cd.get("sid")
        }
        st.save()

    # === Attempt 1: spawn (with CRITIC-pattern check loop), apply,
    # check size, check acyclic ===
    recon_up_parts: list[str] = [user_prompt]

    async def _invoke_recon() -> dict:
        return await _spawn_reconciler("\n\n".join(recon_up_parts))

    async def _on_recon_fb(fb: str) -> dict:
        if len(recon_up_parts) > 1:
            recon_up_parts[-1] = fb
        else:
            recon_up_parts.append(fb)
        return {}

    output, recon_warnings = await _run_checked_loop(
        invoke=_invoke_recon,
        check=lambda r: check_reconciler_output(r, plans),
        name="reconciler",
        max_rounds=caps["judgment_check_rounds"],
        make_feedback_prompt=_on_recon_fb,
    )
    if output is None:
        die("reconciler crashed and produced no result")
    for w in recon_warnings:
        log(f"  reconciler: {w}")
    # Dead-subtask elimination (DESIGN §5): prune fully-speculative
    # subtasks before _check_unresolvable can die(). A subtask is
    # fully speculative when every one of its in_plan requires tags
    # is in the reconciler's unresolvable set AND at least one domain
    # has 0 subtasks (the "constant fold" that makes the dead subtasks
    # detectable).
    _unresolvable_entries = output.get("unresolvable", []) or []
    if _unresolvable_entries:
        _pruned = _prune_dead_subtasks(plans, _unresolvable_entries)
        if _pruned:
            log(f"phase 2½: dead-subtask elimination — pruned "
                f"{len(_pruned)} fully-speculative subtask(s): "
                f"{', '.join(_pruned)}")
            st.data["speculative_collapse_drops"] = _pruned
            st.save()
            output["unresolvable"] = [
                u for u in _unresolvable_entries
                if u["sid"] not in set(_pruned)
            ]
    _check_unresolvable(output)
    _apply_reconciler_output(plans, output)
    _record_conditional_drops(output)

    # Size gate: any reconciler-added subtask with `size: large` is an
    # authoring defect. Runs *before* the acyclicity gate because
    # oversize bundling is upstream — a `large` subtask that bundles
    # four capabilities is also more likely to produce a cycle than
    # four small single-capability subtasks. Splitting first lets the
    # cycle gate evaluate a cleaner graph. Mirror of the cycle-retry
    # control flow below: revert, respawn with structured prompt, re-
    # apply, re-check; die() on exhaustion.
    oversized = _find_oversized_added_subtasks(plans)
    if oversized:
        offenders = ", ".join(s.get("id", "<unknown>") for s in oversized)
        log(f"phase 2½: size gate fired on attempt 1 — "
            f"{len(oversized)} oversized added_subtask(s): {offenders}")
        size_retry_prompt = _build_size_retry_prompt(oversized, user_prompt)

        # Revert: deep-copy the pre-mutation snapshot back into `plans`.
        plans.clear()
        plans.extend(copy.deepcopy(pre_plans_snapshot))

        log("phase 2½: respawning reconciler with size-resolution "
            "retry prompt")
        output2 = await _spawn_reconciler(size_retry_prompt)
        _check_unresolvable(output2)
        for w in check_reconciler_output(output2, plans):
            log(f"  reconciler size-retry: {w}")
        _apply_reconciler_output(
            plans, output2, attempt_1_renames=output.get("renames"))
        _record_conditional_drops(output2)

        # Re-run the size gate on attempt 2's output.
        oversized2 = _find_oversized_added_subtasks(plans)
        if oversized2:
            bullets = "\n".join(
                f"  • {s.get('id', '<unknown>')} "
                f"(provides={list(s.get('provides', []) or [])})"
                for s in oversized2
            )
            die(
                "phase 2½ size gate fired on attempt 2 — the reconciler's "
                f"revised output still emits {len(oversized2)} `added_subtask`(s) "
                f"with `size: large`:\n{bullets}\n"
                "Both retry attempts exhausted. The model bundled work that "
                "exceeds one worker's context twice in a row. Refine the "
                "task description so the foundation capabilities are named "
                "as separate concerns, or split the task into smaller runs."
            )
        # Attempt 2 succeeded — adopt its output for downstream logging.
        output = output2
        # Refresh the snapshot so any later retry (cycle, unresolved)
        # reverts to the post-size-retry state, not the original
        # pre-mutation state. Without this refresh, a subsequent cycle
        # retry's revert would undo the size split and the oversized
        # subtask would return.
        pre_plans_snapshot = copy.deepcopy(plans)

    # Build the post-mutation subtasks dict for the cycle gate.
    post_subtasks: dict[str, dict] = {}
    for plan in plans:
        for s in plan.get("subtasks", []):
            post_subtasks[s["id"]] = s
    preds, _provs, edge_sources = _build_predecessor_graph(post_subtasks)
    succ: dict[str, set[str]] = {sid: set() for sid in post_subtasks}
    for tgt, src_set in preds.items():
        for src in src_set:
            succ[src].add(tgt)
    sccs = _tarjan_sccs(set(post_subtasks), succ)

    if sccs:
        # Cycle detected on attempt 1 — log the diagnostic, compute
        # recommendations, revert mutations, and retry once with the
        # structured retry prompt.
        diag = _format_cycle_diagnostic(
            sccs, succ, edge_sources, output, post_subtasks)
        log(f"phase 2½: acyclicity gate fired on attempt 1 — "
            f"{len(sccs)} cycle(s) detected:\n{diag}")
        recommendations = [
            _recommend_cycle_resolution(
                scc, succ, edge_sources, post_subtasks, output, pre_providers)
            for scc in sccs
        ]
        retry_prompt = _build_cycle_retry_prompt(
            sccs, succ, edge_sources, output, post_subtasks,
            recommendations, user_prompt)

        # Revert: deep-copy the pre-mutation snapshot back into `plans`.
        # The list is mutated in place so callers' references stay valid.
        plans.clear()
        plans.extend(copy.deepcopy(pre_plans_snapshot))

        # === Attempt 2: spawn with retry prompt, validate must-include,
        # apply, check acyclic. If still cyclic → die. ===
        log("phase 2½: respawning reconciler with cycle-resolution "
            "retry prompt")
        output2 = await _spawn_reconciler(retry_prompt)
        _check_unresolvable(output2)

        # Must-include validation: did the revised output address every
        # named cycle? Uses the cycles named in the retry prompt (from
        # the attempt-1 graph). If any cycle was ignored, die cleanly.
        unaddressed = _validate_must_include(output2, sccs)
        if unaddressed:
            bullets = "\n".join(f"  • {u}" for u in unaddressed)
            die(
                f"reconciler's revised output ignored "
                f"{len(unaddressed)} named cycle(s):\n{bullets}\n"
                "Leerie requires every named cycle to be addressed by at "
                "least one of dropped_requires / dependency_edges / "
                "merged_subtasks. The retry prompt listed the legal "
                "operations per cycle; the model defied the structural "
                "constraint. Refine the task or re-run."
            )

        for w in check_reconciler_output(output2, plans):
            log(f"  reconciler cycle-retry: {w}")
        _apply_reconciler_output(
            plans, output2, attempt_1_renames=output.get("renames"))
        _record_conditional_drops(output2)

        # Re-run the gate on attempt 2's output.
        post2_subtasks: dict[str, dict] = {}
        for plan in plans:
            for s in plan.get("subtasks", []):
                post2_subtasks[s["id"]] = s
        preds2, _p2, edge_sources2 = _build_predecessor_graph(post2_subtasks)
        succ2: dict[str, set[str]] = {sid: set() for sid in post2_subtasks}
        for tgt, src_set in preds2.items():
            for src in src_set:
                succ2[src].add(tgt)
        sccs2 = _tarjan_sccs(set(post2_subtasks), succ2)
        if sccs2:
            diag2 = _format_cycle_diagnostic(
                sccs2, succ2, edge_sources2, output2, post2_subtasks)
            die(
                "phase 2½ acyclicity gate fired on attempt 2 — the "
                "reconciler's revised output still produces "
                f"{len(sccs2)} cycle(s):\n{diag2}\n"
                "Both retry attempts exhausted. Refine the task "
                "description or split it into smaller runs that produce "
                "fewer cross-domain capability-tag mismatches; runs with "
                "<15 renames historically never cycle."
            )
        # Attempt 2 succeeded — adopt its output for downstream logging.
        output = output2
        # Refresh the snapshot so the unresolved retry (if it fires
        # next) reverts to the post-cycle-retry state, not the
        # original pre-mutation state. Without this refresh, the
        # unresolved retry would undo the cycle break.
        pre_plans_snapshot = copy.deepcopy(plans)

    # Re-run the DESIGN §5 `requires.extent` mechanical passes against the
    # post-reconciler plan tree so any `extent: external` entries on
    # reconciler-added connector subtasks flow through the same machinery
    # as planner-declared externals. Without this, an added_subtask
    # carrying an external prerequisite would be silently dropped: not
    # collected as a precondition, not surfaced in plan.json, not
    # promoted even if an in-plan producer exists in another plan. The
    # collector returns the full deduped set, so replacing (not
    # appending to) st.data["external_preconditions"] keeps the
    # re-run idempotent.
    #
    # The count can move in either direction:
    #   - GROWS when a reconciler added_subtask declares a new external
    #     requirement (the common forward case).
    #   - SHRINKS when a reconciler `added_provides` (or an added_subtask
    #     that provides a tag) absorbs a planner-declared external —
    #     the second-pass `_promote_external_collisions` demotes the
    #     external entry to in_plan because a provider now exists. This
    #     is correct behavior: the reconciler discovered that the
    #     external prerequisite is actually in-plan after all.
    promoted_after = _promote_external_collisions(plans)
    if promoted_after:
        log(f"phase 2½: promoted {promoted_after} external requires "
            "entry/entries from reconciler added_subtasks to in_plan")
    preconditions_after = _collect_external_preconditions(plans)
    if len(preconditions_after) != len(preconditions):
        log(f"phase 2½: preconditions count changed from "
            f"{len(preconditions)} to {len(preconditions_after)} "
            "after reconciler output")
    st.data["external_preconditions"] = preconditions_after
    st.save()

    # Second-pass check: an `added_subtask` may itself have an unresolved
    # `requires`, OR the reconciler may have invented a new tag in its
    # added_subtasks/added_provides without renaming the original
    # consumer's tag to match (the captured run 075210 failure shape:
    # `cdk-stacks-authored` left unresolved because the model created a
    # connector providing `infra-stacks-authored` and forgot the rename
    # on deps-008).
    #
    # On first detection: spawn the reconciler once more with a structured
    # retry prompt that surfaces the unresolved tags + string-similarity
    # hints from the post-mutation provides namespace. Mirror of the
    # cycle-resolution retry loop above. Same 2-attempt budget; the
    # second attempt's failure dies cleanly with the full report.
    still_unresolved = _compute_unresolved_requires(plans)
    if still_unresolved:
        log(f"phase 2½: unresolved-requires gate fired on attempt 1 — "
            f"{len(still_unresolved)} tag(s) without producers")

        # Build the post-mutation providers map for the recommendation
        # heuristic + retry-prompt builder.
        post_subtasks_for_unresolved: dict[str, dict] = {}
        for plan in plans:
            for s in plan.get("subtasks", []):
                post_subtasks_for_unresolved[s["id"]] = s
        post_providers: dict[str, list[str]] = {}
        for sid, s in post_subtasks_for_unresolved.items():
            for cap in (s.get("provides") or []):
                post_providers.setdefault(cap, []).append(sid)

        # Compute recommendations per unresolved entry (may be None for
        # entries with no strong-similarity candidate).
        recommendations: dict[tuple[str, str], dict | None] = {}
        for u in still_unresolved:
            recommendations[(u["sid"], u["tag"])] = (
                _recommend_unresolved_resolution(
                    u["sid"], u["tag"], post_providers, output))
        n_recommended = sum(1 for r in recommendations.values()
                            if r is not None)
        log(f"  computed {n_recommended} string-similarity hint(s) "
            f"({len(still_unresolved) - n_recommended} unresolved "
            "entry/entries left to model judgment)")

        retry_prompt = _build_unresolved_retry_prompt(
            still_unresolved, post_providers, recommendations, output,
            user_prompt)

        # Revert: deep-copy snapshot back into `plans` (same pattern
        # as the cycle retry). `pre_plans_snapshot` is still in scope
        # from the cycle-gate block above.
        plans.clear()
        plans.extend(copy.deepcopy(pre_plans_snapshot))

        log("phase 2½: respawning reconciler with unresolved-tags "
            "retry prompt")
        output3 = await _spawn_reconciler(retry_prompt)
        _check_unresolvable(output3)

        # Must-include validation: did the revised output address every
        # named unresolved entry? Same fail-loud discipline as the
        # cycle-gate's must-include check. Pass `output` (attempt-1's
        # output) so the validator can accept renames whose `from` is
        # the consumer's pre-revert tag — matches what leerie's own
        # recommendation produces.
        unaddressed = _validate_unresolved_must_include(
            output3, still_unresolved, output)
        if unaddressed:
            bullets = "\n".join(f"  • {u}" for u in unaddressed)
            die(
                f"reconciler's revised output ignored "
                f"{len(unaddressed)} named unresolved-requires entry/"
                f"entries:\n{bullets}\n"
                "Leerie requires every named unresolved entry to be "
                "addressed by at least one of renames / added_provides "
                "/ added_subtasks / conditional_drops / dropped_requires "
                "/ unresolvable. "
                "The retry prompt listed the legal operations per entry; "
                "the model defied the structural constraint. Refine the "
                "task or re-run."
            )

        for w in check_reconciler_output(output3, plans):
            log(f"  reconciler unresolved-retry: {w}")
        _apply_reconciler_output(
            plans, output3, attempt_1_renames=output.get("renames"))
        _record_conditional_drops(output3)

        # Re-run the external-extent passes against attempt-2's plans
        # (mirror of the post-cycle-retry re-run; attempt-2's
        # added_subtasks may carry external requires entries that need
        # the promotion-and-collection passes).
        _promote_external_collisions(plans)
        preconditions_after = _collect_external_preconditions(plans)
        st.data["external_preconditions"] = preconditions_after
        st.save()

        # Re-run the cycle gate on attempt-2's output — the revised
        # output could plausibly introduce a new cycle (e.g., a rename
        # that closes a loop with an existing edge).
        post3_subtasks: dict[str, dict] = {}
        for plan in plans:
            for s in plan.get("subtasks", []):
                post3_subtasks[s["id"]] = s
        preds3, _p3, edge_sources3 = _build_predecessor_graph(post3_subtasks)
        succ3: dict[str, set[str]] = {sid: set() for sid in post3_subtasks}
        for tgt, src_set in preds3.items():
            for src in src_set:
                succ3[src].add(tgt)
        sccs3 = _tarjan_sccs(set(post3_subtasks), succ3)
        if sccs3:
            diag3 = _format_cycle_diagnostic(
                sccs3, succ3, edge_sources3, output3, post3_subtasks)
            die(
                "phase 2½ acyclicity gate fired on the unresolved-"
                "retry attempt — the reconciler's revised output "
                f"introduced {len(sccs3)} cycle(s):\n{diag3}\n"
                "The unresolved-tags retry produced a graph the cycle "
                "gate rejects. Refine the task or re-run."
            )

        # Re-check unresolved on the final state.
        final_unresolved = _compute_unresolved_requires(plans)
        if final_unresolved:
            bullets = "\n".join(
                f"  • {u['domain']}/{u['sid']} requires '{u['tag']}'"
                for u in final_unresolved
            )
            die(
                "reconciler output left "
                f"{len(final_unresolved)} cross-domain dependency/"
                f"dependencies still unresolved after the unresolved-"
                f"tags retry:\n{bullets}\n"
                "Both retry attempts exhausted. This usually means "
                "the reconciler can't find a real producer for the "
                "tag and didn't emit `unresolvable`. Refine the task "
                "description and re-run."
            )
        # Attempt-2 succeeded — adopt its output for downstream logging.
        output = output3

    log(f"phase 2½: reconciled "
        f"({len(output.get('renames', []))} rename(s), "
        f"{len(output.get('added_provides', []))} added_provides, "
        f"{len(output.get('added_subtasks', []))} new subtask(s))")
    return plans


def _tarjan_sccs(
    nodes: set[str],
    succ: dict[str, set[str]],
) -> list[list[str]]:
    """Tarjan's strongly-connected-components algorithm.

    Returns a list of SCCs (each a list of node ids), filtered to
    *non-trivial* components — size ≥ 2 or a self-loop. Trivial
    singletons (a node with no edge to itself) are not returned because
    they are by definition acyclic.

    Iterative implementation (no recursion) so very deep graphs cannot
    blow the stack. Deterministic: nodes are visited in sorted order and
    each component is returned in discovery order, so the same input
    always produces byte-identical output.
    """
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sccs: list[list[str]] = []
    counter = 0

    # Sorted-roots ensures determinism across runs.
    for root in sorted(nodes):
        if root in index_of:
            continue
        work: list[tuple[str, Iterator[str]]] = []
        index_of[root] = counter
        lowlink[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work.append((root, iter(sorted(succ.get(root, set())))))

        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index_of:
                    index_of[w] = counter
                    lowlink[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append(
                        (w, iter(sorted(succ.get(w, set())))))
                    advanced = True
                    break
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], index_of[w])
            if advanced:
                continue
            if lowlink[v] == index_of[v]:
                comp: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                # Filter to non-trivial: size ≥ 2 OR self-loop on size 1.
                if len(comp) > 1 or v in succ.get(v, set()):
                    sccs.append(sorted(comp))
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[v])
    return sccs


def _attribute_cycle_edges(
    scc: list[str],
    succ: dict[str, set[str]],
    edge_sources: dict[tuple[str, str], str],
    output: dict,
    subtasks: dict[str, dict],
) -> list[dict]:
    """For each edge inside an SCC, attribute it back to the reconciler
    mutation (if any) that closed it. Returns a list of edge dicts:
    `{from, to, source, mutation}` where `source` is the raw
    edge_sources label (`"depends_on"` or `"requires:<tag>"`) and
    `mutation` is a short string describing the reconciler mutation that
    produced the edge: `"rename: <sid>'s '<from>' -> '<to>'"`,
    `"added_subtask: <id>"`, `"dependency_edge: <from> -> <to>"`, or
    `"planner-declared"` when no reconciler mutation closed the edge
    (the planner already had this depends_on, so the rename/edge that
    formed the cycle is on the OTHER side of the SCC).
    """
    scc_set = set(scc)
    # Index reconciler renames by (sid, new-tag) for fast lookup.
    # After a rename `feat-001: foo -> bar`, the edge created is
    # feat-001 -> <provider-of-bar>, where the new requires entry has
    # tag=bar.
    rename_by_target: dict[tuple[str, str], dict] = {}
    for r in output.get("renames", []):
        rename_by_target[(r["sid"], r["to"])] = r
    added_ids = {s["id"] for s in output.get("added_subtasks", [])}
    dep_edge_set = {
        (de["from"], de["to"]) for de in output.get("dependency_edges", [])
    }

    edges_out: list[dict] = []
    for src in scc:
        for dst in sorted(succ.get(src, set())):
            if dst not in scc_set:
                continue
            label = edge_sources.get((src, dst), "?")
            mutation = "planner-declared"
            # If this edge's source subtask is a reconciler-added one,
            # the whole subtask is the "mutation."
            if src in added_ids:
                mutation = f"added_subtask: {src}"
            elif dst in added_ids:
                # The added subtask's provides matched a requires on src.
                mutation = f"added_subtask: {dst}"
            elif (src, dst) in dep_edge_set:
                mutation = f"dependency_edge: {src} -> {dst}"
            elif label.startswith("requires:"):
                # The edge `src -> dst` means src provides what dst
                # requires. So the renamed requires entry is on the
                # CONSUMER (dst), not the producer (src). Look up the
                # rename by (dst, tag).
                tag = label.split(":", 1)[1]
                r = rename_by_target.get((dst, tag))
                if r is not None:
                    mutation = (
                        f"rename: {dst}'s '{r['from']}' -> '{r['to']}' "
                        f"(provided by {src})"
                    )
            edges_out.append({
                "from": src, "to": dst,
                "source": label, "mutation": mutation,
            })
    return edges_out


def _format_cycle_diagnostic(
    sccs: list[list[str]],
    succ: dict[str, set[str]],
    edge_sources: dict[tuple[str, str], str],
    output: dict,
    subtasks: dict[str, dict],
) -> str:
    """Render an SCC list + edge attributions into the multi-line
    diagnostic string used by both the gate's `die()` and the retry
    prompt's cycle section. Deterministic — same input, same output.
    """
    lines: list[str] = []
    for i, scc in enumerate(sccs, 1):
        edges = _attribute_cycle_edges(
            scc, succ, edge_sources, output, subtasks)
        lines.append(f"CYCLE {i}: {' <-> '.join(scc)} "
                     f"({len(scc)}-node SCC)")
        lines.append("  Edges in the cycle:")
        for e in edges:
            lines.append(
                f"    {e['from']} -> {e['to']}   "
                f"[{e['source']}; {e['mutation']}]")
        # Structural signals for merge/edge/drop choice in the retry prompt.
        shared_files = _shared_files_in_scc(scc, subtasks)
        planner_depends = [
            (e['from'], e['to']) for e in edges
            if e['mutation'] == 'planner-declared'
            and e['source'] == 'depends_on'
        ]
        if shared_files:
            lines.append(f"  Shared files_likely_touched: "
                         f"{shared_files}   ← merge signal")
        else:
            lines.append("  Shared files_likely_touched: none")
        if planner_depends:
            descs = [f"{f} -> {t}" for f, t in planner_depends]
            lines.append(f"  Planner-declared depends_on in SCC: "
                         f"{', '.join(descs)}")
        else:
            lines.append("  Planner-declared depends_on in SCC: none")
    return "\n".join(lines)


def _shared_files_in_scc(
    scc: list[str], subtasks: dict[str, dict],
) -> list[str]:
    """Files that appear in `files_likely_touched` of ≥ 2 SCC members.
    Empirically (commit 836a9d8, n=3 historical runs) shared-files had
    zero false positives as a "these subtasks really overlap" signal;
    here it's the merge-vs-edge tiebreaker the recommendation heuristic
    consumes."""
    if len(scc) < 2:
        return []
    file_owners: dict[str, list[str]] = {}
    for sid in scc:
        s = subtasks.get(sid) or {}
        for f in (s.get("files_likely_touched") or []):
            file_owners.setdefault(f, []).append(sid)
    return sorted(f for f, owners in file_owners.items() if len(owners) >= 2)


def _original_tag_for_rename_edge(edge: dict, output: dict) -> str:
    """Given a cycle-edge dict (from `_attribute_cycle_edges`) and the
    reconciler's output, return the ORIGINAL pre-rename tag if the
    edge was created by a rename in the output, else the post-rename
    tag (which equals the edge's source-label tag).

    Used by the cycle-retry recommendation + must-include builders so
    `dropped_requires` ops target the tag that ACTUALLY exists on the
    consumer's requires entry after the retry's revert. The retry
    deep-copies the pre-mutation plans back into `plans` before
    applying attempt 2's output — meaning the consumer's requires
    entry holds the ORIGINAL pre-rename tag at apply time. A drop
    targeting the post-rename tag silently no-ops (no matching entry
    to remove), leaving the cycle in place and confusing the model.

    Returns the empty string if the edge has no `requires:<tag>`
    source label (e.g. depends_on edges).
    """
    src_label = edge.get("source", "")
    if not src_label.startswith("requires:"):
        return ""
    post_rename_tag = src_label.split(":", 1)[1]
    consumer_sid = edge["to"]
    for r in output.get("renames", []):
        if r["sid"] == consumer_sid and r["to"] == post_rename_tag:
            return r["from"]
    # Edge wasn't created by a rename in this output (could be
    # planner-declared or from an added_subtask). The post-rename
    # tag IS the current tag in the consumer's requires — safe to
    # drop.
    return post_rename_tag


def _recommend_cycle_resolution(
    scc: list[str],
    succ: dict[str, set[str]],
    edge_sources: dict[tuple[str, str], str],
    subtasks: dict[str, dict],
    output: dict,
    pre_providers: dict[str, list[str]],
) -> dict:
    """Deterministic recommendation for breaking one SCC.

    Returns a dict shaped like one entry of the reconciler's cycle-
    breaking arrays, with an extra `op` key naming the action (always
    either `"dropped_requires"` or `"merged_subtasks"` — the heuristic
    never recommends `dependency_edges`; that op is reachable only when
    the model overrides the recommendation) and a `rationale`
    explaining why this op was picked.

    Heuristic (in order, first match wins):

    1. **Exactly one edge in the SCC is a planner-declared depends_on** →
       `dropped_requires` on the rename that closes the reverse direction.
       Planner ordering wins; the reconciler's rename is the drift.
       [Verifies on summarizer run 1: feat-009 -> feat-008 is planner-
        declared; recommend drop of feat-008's prisma-data-access-ready
        requires.]

    2. **Else SCC members share files_likely_touched (and SCC has size 2 —
       merge is pairwise)** →
       `merged_subtasks(into=smaller_by_scs, from=larger)`. The subtasks
       are authoring the same file; one commit will do both pieces of
       work; the shorter-criterion subtask becomes the canonical home
       (tie-break: lexicographic sid).
       [Verifies on summarizer run 2: feat-001 and config-005 share
        package.json; feat-001 has shorter SCS; recommend merge into
        feat-001.]

    3. **Else** → `dropped_requires` on the rename whose `from` tag had
       no planner-declared producer in the pre-reconcile graph. The
       rename was speculative — the tag was never going to resolve to
       a real producer; dropping the requirement is structurally
       honest.

    4. **Tie-breaker of last resort** → drop the lexicographically later
       rename in the SCC.

    `pre_providers` is the providers map from BEFORE renames were
    applied (used by case 3 to identify speculative renames). The
    caller computes it from the pre-mutation deep copy of the plans.
    """
    edges = _attribute_cycle_edges(
        scc, succ, edge_sources, output, subtasks)

    # --- Case 1: planner-declared depends_on edge present ---
    planner_edges = [
        e for e in edges if e["mutation"] == "planner-declared"
        and e["source"] == "depends_on"
    ]
    if len(planner_edges) == 1:
        planner_e = planner_edges[0]
        # The rename to drop is the one that closes the reverse
        # direction (from planner-e.to back to planner-e.from). In the
        # graph: edge `src -> dst` means src provides what dst requires
        # — so the renamed requires entry lives on the CONSUMER (dst).
        for e in edges:
            if e["from"] == planner_e["to"] and e["to"] == planner_e["from"]:
                if e["mutation"].startswith("rename:"):
                    src_label = e["source"]
                    if src_label.startswith("requires:"):
                        # Use the ORIGINAL pre-rename tag so the drop
                        # matches the consumer's requires entry after
                        # the retry's revert (which restores the pre-
                        # mutation state). A post-rename tag would
                        # silently no-op.
                        tag = _original_tag_for_rename_edge(e, output)
                        return {
                            "op": "dropped_requires",
                            "sid": e["to"],
                            "tag": tag,
                            "reason": (
                                f"{planner_e['from']} -> {planner_e['to']} "
                                "is planner-declared via depends_on; the "
                                f"reverse rename on {e['to']} is the "
                                "drift that closed the cycle. Drop the "
                                "rename's original requirement so the "
                                "planner-declared ordering wins."
                            ),
                            "rationale": "case-1: planner-edge keeper",
                        }

    # --- Case 2: shared files_likely_touched ---
    shared = _shared_files_in_scc(scc, subtasks)
    if shared and len(scc) == 2:
        a, b = scc[0], scc[1]
        a_scs = (subtasks[a].get("success_criteria_seed") or "")
        b_scs = (subtasks[b].get("success_criteria_seed") or "")
        # Shorter SCS becomes the canonical home (`into`); tie-break by
        # lexicographic sid for determinism.
        if len(a_scs) < len(b_scs):
            into, from_ = a, b
        elif len(b_scs) < len(a_scs):
            into, from_ = b, a
        else:
            into, from_ = sorted([a, b])
        return {
            "op": "merged_subtasks",
            "into": into,
            "from": from_,
            "reason": (
                f"Both subtasks edit the same file(s) ({', '.join(shared)}) "
                "and the cycle reflects a genuine authoring overlap, not "
                "a code-artifact dependency. Collapse them into one unit "
                f"with {into} as the canonical home (shorter "
                "success_criteria_seed)."
            ),
            "rationale": "case-2: shared-files merge",
        }

    # --- Case 3: speculative rename (no pre-reconcile producer) ---
    # Edge `src -> dst` carries the rename on the CONSUMER (dst). The
    # rename rewrote some `from`-tag on dst into `tag`; the speculative
    # test asks whether the ORIGINAL `from`-tag had any producer in
    # pre_providers.
    for e in edges:
        if not e["mutation"].startswith("rename:"):
            continue
        src_label = e["source"]
        if not src_label.startswith("requires:"):
            continue
        post_rename_tag = src_label.split(":", 1)[1]
        for r in output.get("renames", []):
            if r["sid"] == e["to"] and r["to"] == post_rename_tag:
                original = r["from"]
                if not pre_providers.get(original):
                    # Drop the ORIGINAL tag — the consumer's requires
                    # entry holds it (not the post-rename tag) at retry
                    # apply time, after the retry's revert.
                    return {
                        "op": "dropped_requires",
                        "sid": e["to"],
                        "tag": original,
                        "reason": (
                            f"Rename of {e['to']}'s '{original}' was "
                            "speculative — no planner-declared producer "
                            "existed for the original tag in the pre-"
                            "reconcile graph. The renamed requirement is "
                            "structurally honest to drop rather than "
                            "preserve via merge/edge."
                        ),
                        "rationale": "case-3: speculative-rename drop",
                    }

    # --- Case 4: lexicographic last-resort tiebreaker ---
    # Drop the lexicographically later rename in the SCC (consumer side).
    # Always returns something; this is the final guarantee that the
    # recommendation function is total.
    rename_edges = [
        e for e in edges
        if e["mutation"].startswith("rename:")
        and e["source"].startswith("requires:")
    ]
    if rename_edges:
        rename_edges.sort(
            key=lambda e: (e["to"], e["source"]), reverse=True)
        e = rename_edges[0]
        # Drop the ORIGINAL pre-rename tag (matches the consumer's
        # requires entry after the retry's revert).
        tag = _original_tag_for_rename_edge(e, output)
        return {
            "op": "dropped_requires",
            "sid": e["to"],
            "tag": tag,
            "reason": (
                "No structural signal preferred a specific resolution; "
                "dropping the lexicographically later rename's original "
                "requirement as a deterministic tiebreaker. Override with "
                "a different operation if you have a structural reason to."
            ),
            "rationale": "case-4: lexicographic tiebreaker",
        }

    # --- Degenerate: no rename edges in the SCC (all depends_on / added) ---
    # This shape shouldn't occur in practice (the gate fires when the
    # reconciler's mutations closed a cycle), but if it does, drop a
    # placeholder for the apply step to ignore. The model still gets the
    # SCC and is free to propose any of the cycle-breaking ops.
    return {
        "op": "dropped_requires",
        "sid": scc[0],
        "tag": "",
        "reason": (
            "No reconciler-introduced rename was found in this SCC; the "
            "cycle may have been formed purely by planner-declared edges "
            "(very rare). Propose any cycle-breaking op."
        ),
        "rationale": "case-degenerate",
    }


def _format_recommendation(rec: dict) -> str:
    """Render a recommendation dict (from `_recommend_cycle_resolution`)
    as a single-line operation literal the retry prompt presents as
    "the answer." The `reason` is inlined verbatim (repr-escaped) so a
    literal-minded model can copy the entire line directly into its
    output without having to interpolate a placeholder. Deterministic.

    The recommendation heuristic only ever emits `dropped_requires`
    (cases 1/3/4) or `merged_subtasks` (case 2). It never emits
    `dependency_edges` — that op is reachable only when the model
    overrides the recommendation, in which case `_format_must_include`
    (not this function) renders the option string."""
    op = rec.get("op", "")
    reason = repr(rec.get("reason", ""))
    if op == "dropped_requires":
        return (f"dropped_requires(sid={rec['sid']!r}, "
                f"tag={rec['tag']!r}, reason={reason})")
    if op == "merged_subtasks":
        return (f"merged_subtasks(into={rec['into']!r}, "
                f"from={rec['from']!r}, reason={reason})")
    return f"<unknown op: {op}>"


def _format_must_include(
    scc: list[str], edges: list[dict], output: dict,
) -> list[str]:
    """For one SCC, list the bounded set of legal cycle-breaking
    operations the retry's apply step will accept. The retry prompt
    surfaces this set so the model knows the legal answer space. The
    apply step's must-include validation rejects outputs that pick
    none of them.

    `output` is the failing attempt-1 reconciler output — used to
    look up each rename's ORIGINAL pre-rename tag so the rendered
    `dropped_requires` options target the tag the consumer's requires
    entry actually holds at retry apply time (after the revert
    restores the pre-mutation state).
    """
    options: list[str] = []
    # Each rename in the SCC can be dropped. Edge `src -> dst` carries
    # the rename on the consumer (dst), so the drop targets dst's sid
    # and the ORIGINAL pre-rename tag.
    for e in edges:
        if (e["mutation"].startswith("rename:")
                and e["source"].startswith("requires:")):
            tag = _original_tag_for_rename_edge(e, output)
            options.append(
                f"dropped_requires(sid={e['to']!r}, tag={tag!r}, ...)")
    # For 2-node SCCs, dependency_edges in either direction and
    # merged_subtasks in either direction are also legal answers.
    if len(scc) == 2:
        a, b = scc[0], scc[1]
        options.append(
            f"dependency_edges(from={a!r}, to={b!r}, ...)")
        options.append(
            f"dependency_edges(from={b!r}, to={a!r}, ...)")
        options.append(
            f"merged_subtasks(into={a!r}, from={b!r}, ...)")
        options.append(
            f"merged_subtasks(into={b!r}, from={a!r}, ...)")
    return options


def _build_cycle_retry_prompt(
    sccs: list[list[str]],
    succ: dict[str, set[str]],
    edge_sources: dict[tuple[str, str], str],
    output: dict,
    subtasks: dict[str, dict],
    recommendations: list[dict],
    original_user_prompt: str,
) -> str:
    """Build the retry prompt sent to the reconciler when the
    acyclicity gate fires on attempt 1.

    Structure:
      - One section per SCC with: edges + structural signals +
        recommendation + bounded "must-include" set.
      - Instructions on the legal answer space (no `unresolvable` for
        cycles).
      - The original user prompt re-appended verbatim so the worker
        has the full input + the resolution context.
    """
    parts: list[str] = []
    parts.append(
        "Your previous reconciler output created "
        f"{len(sccs)} dependency cycle(s) in the merged plan. Leerie has "
        "analyzed each cycle and computed a recommended resolution using "
        "structural signals. You must either emit the recommendation "
        "verbatim or propose an alternative from the bounded set below. "
        "`unresolvable` is NOT a valid response to a cycle — cycles must "
        "be broken with one of dropped_requires / dependency_edges / "
        "merged_subtasks.\n"
    )
    for i, (scc, rec) in enumerate(zip(sccs, recommendations), 1):
        edges = _attribute_cycle_edges(
            scc, succ, edge_sources, output, subtasks)
        shared = _shared_files_in_scc(scc, subtasks)
        planner_edges = [
            (e['from'], e['to']) for e in edges
            if e['mutation'] == 'planner-declared'
            and e['source'] == 'depends_on'
        ]
        parts.append(
            f"\nCYCLE {i}: {' <-> '.join(scc)} ({len(scc)}-node SCC)")
        parts.append("  Edges:")
        for e in edges:
            parts.append(
                f"    {e['from']} -> {e['to']}   "
                f"[{e['source']}; {e['mutation']}]")
        parts.append("  Structural signals:")
        if shared:
            parts.append(
                f"    - Shared files_likely_touched: {shared}   "
                "← merge signal")
        else:
            parts.append("    - Shared files_likely_touched: none")
        if planner_edges:
            descs = [f"{f} -> {t}" for f, t in planner_edges]
            parts.append(
                f"    - Planner-declared depends_on in SCC: "
                f"{', '.join(descs)}")
        else:
            parts.append(
                "    - Planner-declared depends_on in SCC: none")
        # SCS lengths help the model understand the merge tiebreak.
        if len(scc) == 2:
            scs_lens = [
                f"{sid}={len(subtasks.get(sid, {}).get('success_criteria_seed') or '')}"
                for sid in scc
            ]
            parts.append(
                f"    - success_criteria_seed lengths (chars): "
                f"{', '.join(scs_lens)}")
        parts.append(
            f"\n  RECOMMENDED: {_format_recommendation(rec)}")
        parts.append(f"    Why: {rec.get('reason', '')}")
        parts.append("\n  Your output for this cycle MUST include at "
                     "least one of:")
        for opt in _format_must_include(scc, edges, output):
            marker = ("    ← recommended"
                      if _matches_recommendation(opt, rec) else "")
            parts.append(f"    - {opt}{marker}")
    parts.append(
        "\nEmit the same eight-array output as before, with the "
        "additions necessary to break every cycle. Leerie will re-run "
        "cycle detection on your revised output and reject any response "
        "that still has cycles — including new cycles your revisions "
        "introduce.\n\n--- ORIGINAL INPUT ---\n"
    )
    parts.append(original_user_prompt)
    return "\n".join(parts)


def _build_size_retry_prompt(
    oversized: list[dict],
    original_user_prompt: str,
) -> str:
    """Build the retry prompt sent to the reconciler when the size gate
    fires on attempt 1 (any `added_subtask` emitted with `size: large`).

    Structure:
      - One section per offending subtask with: title, intent, the
        `provides` tags it bundled, its `requires`, its `depends_on`.
      - The explicit decomposition rule (one subtask per `provides` tag,
        or smaller groupings that share state).
      - The original user prompt re-appended verbatim so the worker has
        the full input + the resolution context.

    No recommendation heuristic — unlike the cycle and unresolved-requires
    retries, the answer here is mechanical: split into N subtasks. The
    model just needs to know which subtasks are oversized and what the
    rule is."""
    parts: list[str] = []
    parts.append(
        f"Your previous reconciler output emitted {len(oversized)} "
        "`added_subtask`(s) with `size: large`. Leerie enforces "
        "`size ∈ {small, medium}` on all reconciler-added subtasks — "
        "the same constraint planners are bound to. A `large` subtask "
        "bundles work that must be implementable in one worker context; "
        "if it cannot, it must be split.\n"
    )
    for i, s in enumerate(oversized, 1):
        sid = s.get("id", "<unknown>")
        title = s.get("title", "")
        intent = s.get("intent", "")
        provides = list(s.get("provides", []) or [])
        in_plan_reqs = [
            e.get("tag", "") for e in (s.get("requires") or [])
            if isinstance(e, dict) and e.get("extent") == "in_plan"
            and e.get("tag")
        ]
        depends_on = list(s.get("depends_on", []) or [])
        parts.append(f"\nOVERSIZED {i}: {sid}")
        if title:
            parts.append(f"  Title: {title}")
        if intent:
            parts.append(f"  Intent: {intent}")
        parts.append(
            f"  Provides ({len(provides)} tag(s)): "
            f"{provides if provides else '(none — this is itself a smell)'}")
        parts.append(
            f"  Requires (in_plan): "
            f"{in_plan_reqs if in_plan_reqs else '(none)'}")
        parts.append(
            f"  Depends_on: {depends_on if depends_on else '(none)'}")
    parts.append(
        "\nDecomposition rule: emit one subtask per `provides` tag, or "
        "smaller groupings of tags that genuinely share state (e.g., a "
        "DB client and its DAL belong together; an env-config module and "
        "an object-storage helper do not). Partition the `provides` tags "
        "across the new subtasks so every original tag is still produced "
        "by exactly one subtask. Inherit `requires` and `depends_on` "
        "onto the subtask(s) that need them; do not duplicate. Each new "
        "subtask must have `size: small` or `size: medium`."
        "\n\nEmit the same eight-array output as before, with the "
        "oversized subtask(s) replaced by the split subtasks in "
        "`added_subtasks`. Leerie will re-run the size gate on your "
        "revised output and reject any response that still emits "
        "`size: large`.\n\n--- ORIGINAL INPUT ---\n"
    )
    parts.append(original_user_prompt)
    return "\n".join(parts)


def _matches_recommendation(option_str: str, rec: dict) -> bool:
    """Whether a must-include option string matches the recommendation
    (so the retry prompt can mark it with ← recommended).

    The recommendation is always either `dropped_requires` or
    `merged_subtasks` (see `_format_recommendation` docstring); no
    `dependency_edges` branch is reachable here."""
    op = rec.get("op", "")
    if op == "dropped_requires":
        return option_str.startswith(
            f"dropped_requires(sid={rec['sid']!r}, tag={rec['tag']!r}")
    if op == "merged_subtasks":
        return option_str.startswith(
            f"merged_subtasks(into={rec['into']!r}, from={rec['from']!r}")
    return False


def _validate_must_include(
    output: dict,
    sccs: list[list[str]],
) -> list[str]:
    """For each SCC, check that the reconciler's revised output includes
    at least one operation from its must-include set. Returns the list
    of SCCs (each rendered with ' <-> ' between sids) that were NOT
    addressed — empty list means every cycle was addressed.

    Called from the retry loop after attempt 2 emits, before the
    apply-step runs. A non-empty result means the model defied the
    structural constraint and the run aborts cleanly.
    """
    drops = {(e["sid"], e["tag"]) for e in output.get("dropped_requires", [])}
    edges = {(e["from"], e["to"]) for e in output.get("dependency_edges", [])}
    merges = {frozenset([m["into"], m["from"]])
              for m in output.get("merged_subtasks", [])}
    unaddressed: list[str] = []
    for scc in sccs:
        scc_set = set(scc)
        addressed = False
        # dropped_requires against an SCC member (any tag).
        for sid, tag in drops:
            if sid in scc_set:
                addressed = True
                break
        if addressed:
            continue
        # dependency_edges with both endpoints in the SCC.
        for f, t in edges:
            if f in scc_set and t in scc_set:
                addressed = True
                break
        if addressed:
            continue
        # merged_subtasks where both ids are in the SCC.
        for pair in merges:
            if pair <= scc_set:
                addressed = True
                break
        if not addressed:
            unaddressed.append(" <-> ".join(scc))
    return unaddressed


def _tag_jaccard(a: str, b: str) -> float:
    """Token-set Jaccard similarity over hyphen-split tokens.

    Leerie's capability-tag namespace tends toward hyphenated phrases
    where true synonyms share root tokens (e.g. `cdk-stacks-authored`
    ≈ `infra-stacks-authored` share `stacks`+`authored` → 2/4 = 0.5).
    The unresolved-tags retry heuristic uses this to rank candidate
    `provides` against an unresolved `requires` tag.

    Limitation: cannot detect synonyms that share no tokens (e.g.
    `aws-services-selected` vs `infra-stacks-authored`). In those
    cases the heuristic abstains and the model picks unaided.
    """
    ta = set(a.split('-')) if a else set()
    tb = set(b.split('-')) if b else set()
    if not ta and not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _recommend_unresolved_resolution(
    consumer_sid: str,
    unresolved_tag: str,
    providers: dict[str, list[str]],
    output: dict,
) -> dict | None:
    """Deterministic recommendation for resolving one unresolved
    `(consumer_sid, unresolved_tag)` requires entry. Mirror of
    `_recommend_cycle_resolution` for the unresolved-tags retry.

    Returns a dict shaped like a rename op (with `op` + `rationale`),
    or `None` if no candidate is confident enough to recommend
    (model picks unaided).

    The recommendation's `from` field is the consumer's PRE-REVERT
    requires-entry tag. If `output["renames"]` contains a rename
    matching `(sid=consumer_sid, to=unresolved_tag)`, attempt-1 had
    rewritten the consumer's tag to `unresolved_tag`; after the
    retry's revert, the consumer's entry holds `r["from"]` (the
    original pre-rename tag). Else `unresolved_tag` was never touched
    by a rename and IS the consumer's pre-revert tag. Analogous to
    `_original_tag_for_rename_edge`'s role for the cycle-retry's
    `dropped_requires` recommendations.

    Self-loop guard: skips candidates whose `providers[candidate]`
    includes `consumer_sid` — a rename TO a tag the consumer itself
    provides would create a self-edge in the dependency graph.
    Caught the historical `deps-011 'supabase-client-imports-removed'`
    case in run feat-let-s-add-telemetry-051809.

    Heuristic (after the self-loop guard filters candidates):
      1. Unique top match with Jaccard ≥ 0.5 → recommend rename.
      2. Top match with Jaccard ≥ 0.7 (even if not unique) → recommend
         rename (very high similarity is high confidence).
      3. Else → return None.

    The recommendation framing in the retry prompt treats this output
    as a *hint* (string-similarity prior), NOT as the answer the model
    must emit verbatim. Historical scan showed leerie renames usually
    span vocabularies with low textual overlap; the heuristic is
    calibrated for synonym-asymmetric cases (like captured run
    075210), not general renames.
    """
    # Determine the consumer's pre-revert tag (what attempt-2's apply
    # will see). If attempt-1 renamed the consumer's tag to
    # `unresolved_tag`, the pre-revert tag is `r["from"]`. Else it's
    # `unresolved_tag` (no rename touched it).
    pre_revert_tag = unresolved_tag
    for r in output.get("renames", []):
        if r.get("sid") == consumer_sid and r.get("to") == unresolved_tag:
            pre_revert_tag = r["from"]
            break

    # Score candidates, applying the self-loop guard.
    scored: list[tuple[float, str]] = []
    for candidate_tag, candidate_providers in providers.items():
        if consumer_sid in candidate_providers:
            # Self-loop trap: skip.
            continue
        j = _tag_jaccard(unresolved_tag, candidate_tag)
        scored.append((j, candidate_tag))
    scored.sort(reverse=True)

    if not scored:
        return None

    # Case 1: unique top match with j >= 0.5.
    strong = [s for s in scored if s[0] >= 0.5]
    if len(strong) == 1:
        return {
            "op": "rename",
            "sid": consumer_sid,
            "from": pre_revert_tag,
            "to": strong[0][1],
            "reason": (
                f"Top string-similarity match (Jaccard {strong[0][0]:.3f}, "
                "shared hyphen-tokens) — unique above the 0.5 threshold."
            ),
            "rationale": "case-1: unique-strong-similarity",
        }

    # Case 2: very high similarity (j >= 0.7) even if not unique.
    very_strong = [s for s in scored if s[0] >= 0.7]
    if very_strong:
        return {
            "op": "rename",
            "sid": consumer_sid,
            "from": pre_revert_tag,
            "to": very_strong[0][1],
            "reason": (
                f"Very high string-similarity match (Jaccard "
                f"{very_strong[0][0]:.3f}, shared hyphen-tokens)."
            ),
            "rationale": "case-2: very-high-similarity",
        }

    # No confident match — model picks unaided.
    return None


def _build_unresolved_retry_prompt(
    unresolved: list[dict],
    providers: dict[str, list[str]],
    recommendations: dict[tuple[str, str], dict | None],
    output: dict,
    original_user_prompt: str,
) -> str:
    """Build the retry prompt sent to the reconciler when the
    unresolved-requires gate fires on attempt 1.

    Per unresolved entry, render:
      - The (consumer_sid, unresolved_tag) pair.
      - The top-3 string-similar `provides` tags (always shown).
      - The recommendation (if computed) framed as a HINT — string
        similarity can produce false friends (e.g. narrow synonym for
        a broader concept), so the model is told to emit verbatim only
        if semantically correct.
      - The bounded must-include set.

    `recommendations` maps (sid, tag) → recommendation-dict-or-None.
    `output` is attempt 1's reconciler output — used to look up the
    pre-revert tag for any unresolved entry that attempt 1 renamed.
    All must-include examples (renames `from`, added_provides tag,
    added_subtasks provides) use the pre-revert tag so a model copying
    them verbatim hits the consumer's actual requires entry after the
    retry's revert restores the pre-mutation plans. When `pre_revert_tag
    != tag`, a NOTE is also rendered explaining the revert semantic so
    a literal-minded model doesn't override the examples with the
    post-mutation tag (which silently no-ops at apply time).
    """
    parts: list[str] = []
    parts.append(
        "Your previous reconciler output left "
        f"{len(unresolved)} cross-domain `requires` tag(s) still "
        "unresolved after applying your renames / added_provides / "
        "added_subtasks. Leerie has computed string-similarity hints "
        "from the post-mutation `provides` namespace. Use the hints "
        "if they're semantically correct; if a hint is only "
        "textually close (a 'false friend' — e.g. a narrow synonym "
        "for a broader concept), pick a different option from the "
        "bounded set below. `unresolvable` IS valid here if no real "
        "producer exists. `dropped_requires` IS also valid: when the "
        "consumer's own `provides` already covers the work the requires "
        "tag names (an over-specified self-reference rather than a real "
        "cross-subtask dependency), drop the requires entry — the "
        "consumer stays, only the bad edge goes.\n"
    )

    for i, u in enumerate(unresolved, 1):
        sid = u["sid"]
        tag = u["tag"]
        rec = recommendations.get((sid, tag))
        parts.append(
            f"\nUNRESOLVED {i}: {u.get('domain', '<unknown>')}/{sid} "
            f"requires '{tag}'")

        # Top-3 similarity ranking (always shown — context for the model).
        scored = sorted(
            ((_tag_jaccard(tag, p), p)
             for p in providers if sid not in providers[p]),
            reverse=True,
        )
        if scored:
            parts.append("  Top string-similar provides (consumer's own "
                         "provides excluded — self-loop guard):")
            for j, p in scored[:3]:
                parts.append(
                    f"    j={j:.3f}  '{p}'  (provided by {providers[p]})")
        else:
            parts.append("  No provides in the plan to rank against.")

        # Recommendation, if computed.
        if rec is not None:
            parts.append(
                f"\n  HINT (string-similarity prior — verify "
                f"semantically before emitting):")
            parts.append(
                f"    rename(sid='{rec['sid']}', "
                f"from='{rec['from']}', to='{rec['to']}', "
                f"reason='{rec['reason']}')")
        else:
            parts.append(
                "\n  No high-confidence hint computed — pick from the "
                "bounded set below using your judgment.")

        # Must-include set. The rename example must use the
        # pre-revert tag as `from`: attempt 2 applies against the
        # reverted (pre-mutation) plans, so the consumer's requires
        # entry holds the pre-revert tag, not the post-mutation one.
        pre_revert_tag = tag
        for r in output.get("renames", []):
            if r.get("sid") == sid and r.get("to") == tag:
                pre_revert_tag = r["from"]
                break
        # When attempt 1 renamed the consumer's tag, the unresolved
        # header shows the POST-mutation tag but the must-include
        # examples reference the PRE-revert tag. Explain the revert
        # so a literal-minded model doesn't override the examples
        # with the post-mutation form (which silently no-ops after
        # apply).
        if pre_revert_tag != tag:
            parts.append(
                f"\n  NOTE: Your attempt 1 renamed '{pre_revert_tag}' → "
                f"'{tag}' on this consumer. Leerie reverts your attempt-1 "
                f"output before re-applying attempt 2 against the "
                f"pre-mutation plans — so the consumer's requires entry "
                f"holds the ORIGINAL '{pre_revert_tag}' at apply time. "
                f"Address '{pre_revert_tag}' (the examples below use it "
                f"correctly); don't emit '{tag}' as the tag/from field.")
        parts.append(
            "\n  Your output for this unresolved entry MUST include "
            "at least one of:")
        if scored:
            top = scored[0][1]
            parts.append(
                f"    - renames: rewrite this entry to a real producer "
                f"(e.g. rename(sid={sid!r}, from={pre_revert_tag!r}, "
                f"to={top!r}))")
        parts.append(
            f"    - added_provides: declare an existing subtask "
            f"actually produces '{pre_revert_tag}' (add it to that "
            f"subtask's provides)")
        parts.append(
            f"    - added_subtasks: add a new connector subtask whose "
            f"provides includes '{pre_revert_tag}'")
        parts.append(
            f"    - conditional_drops: drop the consumer subtask "
            f"({sid!r}) wholesale — ONLY if its own `intent` declares "
            "it conditional on this precondition (e.g. 'no-op if X', "
            "'conditionally add', 'otherwise this subtask is dropped'). "
            "Reserved for planner-authored consumers.")
        parts.append(
            f"    - dropped_requires: drop just this `requires` entry "
            f"from {sid!r} (the consumer stays in the plan) — ONLY if "
            f"the consumer's own `provides` already covers the work "
            f"'{pre_revert_tag}' names, at a different granularity. "
            "I.e. the requires entry is an aggregate, a coarser synonym, "
            "or an authoring-time decision the same subtask itself "
            "records, rather than a code artifact another subtask "
            "produces. Distinct from `conditional_drops` (which removes "
            "the whole subtask) — use this when the consumer should "
            "stay but the over-specified self-reference should go.")
        parts.append(
            f"    - unresolvable: name this as a genuine gap with a "
            "one-sentence reason (aborts the run cleanly).")

    parts.append(
        "\nEmit the same eight-array output as before. Leerie will "
        "re-check unresolved-requires AND re-run the cycle gate on "
        "your revised output; an attempt that still has unresolved "
        "tags will abort the run with the structured report.\n\n"
        "--- ORIGINAL INPUT ---\n")
    parts.append(original_user_prompt)
    return "\n".join(parts)


def _validate_unresolved_must_include(
    output: dict,
    unresolved: list[dict],
    attempt_1_output: dict | None,
) -> list[str]:
    """For each unresolved (sid, tag), check the reconciler's revised
    output addresses it via one of: rename on that sid+tag, added_provides
    covering that tag (any sid), added_subtask whose provides includes
    that tag, conditional_drops on the consumer sid (drops the
    planner-emitted conditional consumer wholesale — DESIGN §5),
    dropped_requires on that sid+tag (consumer's requires is
    over-specified — an aggregate or coarser synonym of the consumer's
    own provides; the requires entry is the defect, not the plan), OR
    unresolvable on that sid+tag.

    Returns the list of unaddressed entries (rendered as "domain/sid
    requires 'tag'") — empty list means every unresolved entry was
    addressed. Mirror of `_validate_must_include` for the cycle gate.

    For rename / added_provides / added_subtasks ops, accept a match
    if the op covers EITHER the unresolved (post-mutation) tag OR the
    consumer's pre-revert tag (looked up via `attempt_1_output`'s
    renames). This matches what leerie's own recommendation + must-include
    examples produce: the pre-revert tag is what the consumer's
    requires entry holds at apply time after the retry's revert
    restores the pre-mutation state. Without this dual-tag
    acceptance leerie would reject its own recommendations as not
    addressing the unresolved entry; without ALSO accepting the
    post-mutation form a model that legitimately re-emits the rename
    + addresses the resulting post-mutation entry would be rejected.

    Called from the unresolved-retry loop after attempt 2 emits, before
    the apply-step runs. A non-empty result means the model defied the
    structural constraint and the run aborts cleanly.

    `attempt_1_output` is the failing first-attempt reconciler output
    (in scope as `output` at the call site). Pass `None` when no
    attempt-1 output is available (no pre-revert tag lookup occurs).
    """
    # Build a (consumer_sid → pre_revert_tag) lookup from attempt-1's
    # renames so we can accept the pre-revert tag alongside the
    # unresolved (post-mutation) tag in validation (all three branches:
    # rename, added_provides, added_subtasks).
    pre_revert_tag_by_sid_tag: dict[tuple[str, str], str] = {}
    if attempt_1_output:
        for r in attempt_1_output.get("renames", []):
            sid = r.get("sid")
            to_tag = r.get("to")
            from_tag = r.get("from")
            if sid and to_tag and from_tag:
                pre_revert_tag_by_sid_tag[(sid, to_tag)] = from_tag

    # Index the revised output's operations for fast lookup.
    rename_sid_tags = {
        (r["sid"], r["from"]) for r in output.get("renames", [])
    }
    added_provides_tags = {
        ap["tag"] for ap in output.get("added_provides", [])
    }
    added_subtask_provides: set[str] = set()
    for s in output.get("added_subtasks", []):
        for tag in (s.get("provides") or []):
            added_subtask_provides.add(tag)
    unresolvable_sid_tags = {
        (u["sid"], u["tag"]) for u in output.get("unresolvable", [])
    }
    # conditional_drops addresses an unresolved entry by removing the
    # *consumer* sid wholesale — the unresolved tag becomes moot because
    # the subtask requiring it ceases to exist. Matched on sid alone
    # (not sid+tag), mirroring the apply step's keying. The
    # `_added_by_reconciler` guard in the apply step ensures the drop is
    # safe — it die()s if the model targets a reconciler-added subtask.
    conditional_drop_sids = {
        cd["sid"] for cd in output.get("conditional_drops", [])
    }
    # dropped_requires addresses an unresolved entry by removing the
    # over-specified `requires` entry from the consumer's `requires`
    # list. Matched on (sid, tag); the apply step at the existing
    # `dropped_requires` site (see `_apply_reconciler_output`) keys the
    # same way. The consumer stays in the plan — only the over-specified
    # edge is removed.
    dropped_requires_sid_tags = {
        (dr["sid"], dr["tag"]) for dr in output.get("dropped_requires", [])
    }

    unaddressed: list[str] = []
    for u in unresolved:
        sid, tag = u["sid"], u["tag"]
        # Accept rename on either the post-mutation tag or the pre-revert tag.
        pre_revert_tag = pre_revert_tag_by_sid_tag.get((sid, tag))
        rename_addressed = (
            (sid, tag) in rename_sid_tags
            or (pre_revert_tag is not None
                and (sid, pre_revert_tag) in rename_sid_tags)
        )
        # added_provides/added_subtasks must accept BOTH the post-mutation
        # tag (legal when attempt 2 re-emits the rename so apply produces
        # consumer.requires=[post-mutation-tag]) AND the pre-revert tag
        # (legal when attempt 2 omits the rename and the producer covers
        # the consumer's pre-revert entry directly). Symmetric to the
        # dual-tag acceptance for the rename branch above.
        added_provides_addressed = (
            tag in added_provides_tags
            or (pre_revert_tag is not None
                and pre_revert_tag in added_provides_tags)
        )
        added_subtask_addressed = (
            tag in added_subtask_provides
            or (pre_revert_tag is not None
                and pre_revert_tag in added_subtask_provides)
        )
        # dropped_requires: same dual-tag acceptance as the other ops.
        # If attempt 2 re-emits the rename, the apply step sees
        # consumer.requires=[post-mutation-tag]; if attempt 2 omits
        # the rename, the requires entry holds the pre-revert tag.
        # Either form should count as addressing this unresolved entry.
        dropped_requires_addressed = (
            (sid, tag) in dropped_requires_sid_tags
            or (pre_revert_tag is not None
                and (sid, pre_revert_tag) in dropped_requires_sid_tags)
        )
        addressed = (
            rename_addressed
            or added_provides_addressed
            or added_subtask_addressed
            or sid in conditional_drop_sids
            or dropped_requires_addressed
            or (sid, tag) in unresolvable_sid_tags
        )
        if not addressed:
            unaddressed.append(
                f"{u.get('domain', '<unknown>')}/{sid} requires '{tag}'")
    return unaddressed


# =========================================================================
# phase 2¾: plan-overlap judge (DESIGN §5 *Cross-domain surface overlap*)
# =========================================================================
#
# Parallel planners can independently produce subtasks that target the
# same exported artifact (component / function / primitive) with
# incompatible APIs. The reconciler doesn't catch this — its mandate is
# `requires`-tag vocabulary drift, and the two colliding subtasks can
# legitimately use different `provides` tags for the same artifact. The
# collision then surfaces as an integrator merge-conflict mid-run, with
# worker budget already spent across earlier waves.
#
# `phase_overlap_judge` runs one `plan_overlap_judge` worker between
# reconcile and schedule to detect these collisions at plan time. The
# judge emits zero or more `collisions`, each with one of four
# resolutions: `merge` (one component satisfies both intents),
# `drop_a` / `drop_b` (one intent supersedes), or `unresolvable`
# (structural API contradiction; die at plan time).
#
# Per DESIGN §12, the Python apply step is load-bearing: the prompt
# describes the discipline, the code enforces it. The merge-feasibility
# backstop (`_validate_overlap_judge_output`) rejects any `merge`
# without a non-empty `merge_feasibility` statement — that statement IS
# the merged subtask's unified intent, so a missing one would produce
# a frankenstein spec.


def _compute_overlap_anchors(collisions: list[dict]) -> set[str]:
    """An *anchor* is a sid that appears in two or more non-
    `unresolvable` collisions. By construction, the anchor is the
    subtask whose surface overlaps with multiple sibling subtasks
    on different artifacts (or with structurally compatible intents
    on related artifacts) — i.e., it is the broader scope that
    absorbs each partner. Returning the anchor set lets the apply
    loop survive the anchor through every merge it appears in,
    overriding the default lex-smaller survivor rule (which is a
    determinism device with no semantic content — see
    `_apply_overlap_merge`'s docstring on the lex rule).

    Pure helper. `unresolvable` collisions are excluded because they
    are surfaced separately and never mutate the plan, so they don't
    establish an overlap claim."""
    counts: dict[str, int] = {}
    for c in collisions:
        if c.get("resolution") == "unresolvable":
            continue
        for sid in (c.get("a_sid"), c.get("b_sid")):
            if sid:
                counts[sid] = counts.get(sid, 0) + 1
    return {sid for sid, n in counts.items() if n >= 2}


def _validate_overlap_judge_output(output: dict, subtasks_by_id: dict[str, dict]) -> None:
    """Apply the merge-feasibility backstop and structural sanity checks
    on the judge's output, before any mutation. die()s on violation —
    callers are expected to have deep-copied `plans` if they need clean
    reversion. Per DESIGN §12 (prompts advisory, code enforces): the
    prompt asks for a concrete merge_feasibility statement whenever
    `merge` is emitted; Python rejects a `merge` without one rather
    than trusting the prompt was followed."""
    seen_pairs: set[tuple[str, str]] = set()
    for c in output.get("collisions", []) or []:
        a_sid = c.get("a_sid")
        b_sid = c.get("b_sid")
        resolution = c.get("resolution")
        if not a_sid or not b_sid:
            die(f"plan-overlap judge emitted a collision with missing "
                f"a_sid/b_sid: {c!r}. Schema should have caught this; "
                "refine the task or re-run.")
        if a_sid == b_sid:
            die(f"plan-overlap judge emitted a collision where a_sid == "
                f"b_sid == {a_sid!r}; a subtask cannot collide with "
                "itself. Refine the task or re-run.")
        if a_sid not in subtasks_by_id:
            die(f"plan-overlap judge referenced unknown subtask "
                f"{a_sid!r} (collision with {b_sid!r}); the judge sees "
                "the reconciled plan, so an unknown id is a model "
                "defect. Refine the task or re-run.")
        if b_sid not in subtasks_by_id:
            die(f"plan-overlap judge referenced unknown subtask "
                f"{b_sid!r} (collision with {a_sid!r}); refine the task "
                "or re-run.")
        # Order-independent pair dedup — two collisions on the same pair
        # with different resolutions would be incoherent.
        pair = tuple(sorted([a_sid, b_sid]))
        if pair in seen_pairs:
            die(f"plan-overlap judge emitted two collisions for the "
                f"same pair {pair!r}; the model must pick one "
                "resolution. Refine the task or re-run.")
        seen_pairs.add(pair)
        if resolution == "merge":
            mf = (c.get("merge_feasibility") or "").strip()
            if not mf:
                die(f"plan-overlap judge emitted resolution=merge for "
                    f"{a_sid!r} ↔ {b_sid!r} (artifact: "
                    f"{c.get('artifact', '<unspecified>')!r}) without a "
                    "merge_feasibility statement. The prompt requires a "
                    "concrete merge-feasibility check before merging; "
                    "merging without it would produce a frankenstein "
                    "implementer spec. The right answer in this case is "
                    "resolution=unresolvable. Refine the task or re-run.")

    # Anchor-set consistency check (DESIGN §5 anchor rule).
    # An *anchor* is a sid that appears in two or more non-
    # `unresolvable` collisions — the structural broader-scope subtask
    # the apply loop will keep as the survivor of each merge it
    # appears in. One judge emission involving anchors is pathological
    # and must die() before any mutation:
    #
    # - drop-of-anchor: a `drop_*` whose `dropped_sid` is an anchor.
    #   The judge would be asking to delete the same subtask other
    #   collisions claim absorbs them — directly contradictory.
    #
    # (Earlier iterations of this audit also gated `merge`-between-
    # two-anchors, but the apply loop's natural semantics — fall
    # through to lex-smaller within the unified cluster, with
    # `_apply_overlap_merge`'s `from_intent` preservation per the
    # DESIGN §5 carry-forward invariant — handles every observed
    # multi-anchor shape cleanly. The earlier check was over-
    # aggressive and is removed.)
    collisions = output.get("collisions", []) or []
    anchors = _compute_overlap_anchors(collisions)
    if anchors:
        for c in collisions:
            resolution = c.get("resolution")
            a_sid = c.get("a_sid")
            b_sid = c.get("b_sid")
            artifact = c.get("artifact", "<unspecified>")
            if resolution == "drop_a" and a_sid in anchors:
                die(f"plan-overlap judge contradicts itself: it asks to "
                    f"drop {a_sid!r} (collision with {b_sid!r}, artifact "
                    f"{artifact!r}) but {a_sid!r} also anchors other "
                    "merge/drop collisions in the same output. A drop "
                    "of an anchor would delete the subtask other "
                    "collisions claim absorbs them. Refine the task or "
                    "re-run; if the cluster is genuine, the judge "
                    "should emit `merge` (not `drop`) against the "
                    "anchor and `unresolvable` if the cluster cannot "
                    "be auto-resolved.")
            if resolution == "drop_b" and b_sid in anchors:
                die(f"plan-overlap judge contradicts itself: it asks to "
                    f"drop {b_sid!r} (collision with {a_sid!r}, artifact "
                    f"{artifact!r}) but {b_sid!r} also anchors other "
                    "merge/drop collisions in the same output. A drop "
                    "of an anchor would delete the subtask other "
                    "collisions claim absorbs them. Refine the task or "
                    "re-run; if the cluster is genuine, the judge "
                    "should emit `merge` (not `drop`) against the "
                    "anchor and `unresolvable` if the cluster cannot "
                    "be auto-resolved.")


def _apply_overlap_drop(plans: list[dict], dropped_sid: str,
                        surviving_sid: str) -> None:
    """Remove `dropped_sid` from its plan, union its `provides` tags
    into `surviving_sid`, and rewrite downstream `depends_on`
    references to point at `surviving_sid`. Mirrors the
    `conditional_drops` apply step's removal logic in
    `_apply_reconciler_output` — same in-place plan mutation pattern,
    same downstream `depends_on` rewrite semantics.

    Unlike conditional_drops (which prunes references rather than
    rewriting them, because the dropped subtask's `provides` becomes a
    fresh unresolved entry the reconciler retry handles), an
    overlap-judge drop has a surviving partner producing the same
    artifact. We union the dropped subtask's `provides` into the
    survivor so downstream `requires` that matched the dropped
    subtask's tags resolve cleanly against the survivor — without this
    union, dropping `feat-008` (provides `auth-shell-adopted`) in
    favor of `refactor-001` (provides `auth-shell-component`) would
    orphan every `feat-011 requires auth-shell-adopted` edge into a
    `validate_plan` error that doesn't trace back to the judge's drop.
    `phase_overlap_judge` runs after the reconciler's unresolved-retry
    loop, so leaving orphans for "the next pass" is not an option.

    After the union, any `extent: in_plan` requires entry on the
    survivor whose tag is now in its own provides becomes a graph
    self-loop and is removed — mirrors the same self-loop cleanup in
    `_apply_overlap_merge`. The dropped subtask's title / intent /
    success_criteria_seed are NOT carried over: the judge said one
    intent supersedes the other, and the survivor's intent is the
    intent that wins. Only the capability-graph wiring is unioned."""
    # Self-loop guard: if the caller asked to drop `X` in favor of `X`
    # (e.g. an apply-loop rewrite collapsed both endpoints onto the
    # same survivor), there is nothing to do. Continuing past this
    # would happily filter the only copy out of `plans` in the removal
    # loop below — the same silent-data-loss class the apply-loop
    # rewrite is designed to prevent.
    if dropped_sid == surviving_sid:
        return

    by_id: dict[str, dict] = {}
    for plan in plans:
        for s in plan.get("subtasks", []):
            by_id[s["id"]] = s

    # Silent no-op if either sid is missing — mirrors `renames` /
    # `conditional_drops`. The validator catches truly unknown sids
    # upstream; this guard makes the helper robust against being
    # called twice (e.g., a retry path) without dying.
    dropped = by_id.get(dropped_sid)
    surviving = by_id.get(surviving_sid)
    if dropped is None or surviving is None:
        # Still rewrite depends_on references — they may point at the
        # dropped sid even if the subtask itself is already gone.
        for plan in plans:
            for s in plan.get("subtasks", []):
                deps = s.get("depends_on") or []
                if dropped_sid in deps:
                    new_deps: list[str] = []
                    for dep in deps:
                        dep = surviving_sid if dep == dropped_sid else dep
                        if dep not in new_deps and dep != s.get("id"):
                            new_deps.append(dep)
                    s["depends_on"] = new_deps
        # Still remove any stale subtask entry with the dropped id.
        for plan in plans:
            plan["subtasks"] = [
                t for t in plan.get("subtasks", []) if t.get("id") != dropped_sid
            ]
        return

    # Union dropped.provides into surviving.provides (dedup, order-
    # preserving). This is the load-bearing fix: downstream `requires`
    # entries that matched the dropped subtask's tags now resolve
    # against the survivor.
    surv_provides = surviving.setdefault("provides", [])
    for tag in (dropped.get("provides") or []):
        if tag not in surv_provides:
            surv_provides.append(tag)

    # Drop survivor's in_plan requires that are now self-loops (tag
    # produced by the survivor's own provides post-union). External
    # entries stay regardless (out-of-graph, not graph edges).
    surviving["requires"] = [
        entry for entry in (surviving.get("requires") or [])
        if not (isinstance(entry, dict)
                and entry.get("extent") == "in_plan"
                and entry.get("tag") in surv_provides)
    ]

    # Remove the dropped subtask from its plan.
    for plan in plans:
        plan["subtasks"] = [
            t for t in plan.get("subtasks", []) if t.get("id") != dropped_sid
        ]

    # Rewrite downstream depends_on references and drop the dropped sid
    # from anywhere it appears as a predecessor.
    for plan in plans:
        for s in plan.get("subtasks", []):
            deps = s.get("depends_on") or []
            if dropped_sid in deps:
                new_deps: list[str] = []
                for dep in deps:
                    dep = surviving_sid if dep == dropped_sid else dep
                    if dep not in new_deps and dep != s.get("id"):
                        new_deps.append(dep)
                s["depends_on"] = new_deps


def _apply_overlap_merge(plans: list[dict], a_sid: str, b_sid: str,
                         artifact: str, merge_feasibility: str,
                         survivor_hint: str | None = None) -> str:
    """Collapse `a_sid` and `b_sid` into one subtask. Returns the
    surviving sid.

    Survivor selection:
    - Default (no `survivor_hint`): the lexicographically smaller sid
      wins. This is a *determinism* device — it ensures two judge
      outputs that differ only in pair ordering produce identical
      merged plans. It carries no semantic content.
    - With `survivor_hint`: the named sid wins, overriding the lex
      rule. Used by `_apply_overlap_collisions` for the anchor case:
      when one sid appears in multiple non-`unresolvable` collisions,
      it is the structural anchor of the cluster (by construction the
      subtask that overlaps with every partner) and must survive each
      merge so its partners are absorbed into it. The hint must equal
      either `a_sid` or `b_sid`; passing any other value die()s.

    Field semantics — mirrors `_apply_reconciler_output`'s
    `merged_subtasks` apply step but uses the judge's
    `merge_feasibility` as the canonical unified intent rather than
    a free-form concatenation. The result is one subtask that:

    - keeps the surviving sid as `id` (stable across re-runs).
    - `title` becomes `"{survivor.title} + {dropped.title}"`.
    - `intent` becomes the concatenation of the survivor's full
      existing intent, the absorbed subtask's full existing intent
      (under a `--- Absorbed intent from {dropped.id} ---` marker),
      and a trailing `"Merged with {dropped.id} by plan-overlap-judge:
      {merge_feasibility}"` note. Including the absorbed subtask's
      intent is required by the DESIGN §5 *merge_feasibility
      carry-forward* invariant: any `merge_feasibility` previously
      appended to the absorbed subtask's intent (from an earlier
      merge where it was the survivor) must survive into the new
      merged subtask. The trailing note is the load-bearing record
      of the current pair's unified spec, the same as before.
    - `success_criteria_seed` becomes `"{survivor.criteria} AND "
      "{dropped.criteria}"` (same shape as the reconciler's merge
      for symmetry).
    - `files_likely_touched`, `provides`, `requires`, `depends_on`
      become the union (dedup, order-preserving), with self-references
      to either sid removed.
    - tag-based `requires` that the merged subtask itself now provides
      are dropped (would be a graph self-loop).
    - `_merged_from` stamps `[dropped_sid, ...]` for traceability
      (mirrors the reconciler).

    Downstream subtasks whose `depends_on` referenced the dropped sid
    are rewritten to point at the survivor."""
    by_id: dict[str, dict] = {}
    for plan in plans:
        for s in plan.get("subtasks", []):
            by_id[s["id"]] = s

    if a_sid not in by_id or b_sid not in by_id:
        # _validate_overlap_judge_output should have caught this.
        # Defensive die() in case the validator is bypassed somehow.
        missing = [x for x in (a_sid, b_sid) if x not in by_id]
        die(
            "plan-overlap merge references non-existent subtask id(s): "
            f"{', '.join(sorted(missing))}. Both endpoints must exist "
            "in the reconciled plan."
        )

    if survivor_hint is not None and survivor_hint not in (a_sid, b_sid):
        # Defensive die — the only legitimate callers pass an anchor
        # sid that they themselves resolved from the collision pair.
        die(
            f"plan-overlap merge survivor_hint {survivor_hint!r} is "
            f"neither endpoint of the pair ({a_sid!r}, {b_sid!r}). "
            "This is an orchestrator logic bug; refine the task or "
            "re-run."
        )

    # Survivor selection. With a hint, the named sid wins; otherwise
    # the stable lex-smaller rule (determinism device).
    if survivor_hint is not None:
        into_id = survivor_hint
        from_id = b_sid if survivor_hint == a_sid else a_sid
    else:
        into_id, from_id = sorted([a_sid, b_sid])
    into_s = by_id[into_id]
    from_s = by_id[from_id]

    # title: concatenation.
    into_title = into_s.get("title") or ""
    from_title = from_s.get("title") or ""
    if into_title and from_title:
        into_s["title"] = f"{into_title} + {from_title}"
    elif from_title:
        into_s["title"] = from_title

    # intent: anchor on into's intent, then append the absorbed
    # subtask's full intent (which may already contain prior
    # `Merged with …` notes from earlier merges where `from_s` was
    # itself a survivor — see DESIGN §5 "merge_feasibility
    # carry-forward" invariant), then append the current pair's
    # merge_feasibility as the unified-spec note. Without the
    # from_intent block, any merge_feasibility from a prior
    # absorption would be silently lost when this subtask is
    # itself absorbed — the silent-data-loss class the
    # merge-feasibility discipline exists to prevent, applied
    # across an absorption chain rather than within a single pair.
    into_intent = into_s.get("intent") or ""
    from_intent = from_s.get("intent") or ""
    from_block = (f"\n\n--- Absorbed intent from {from_id} ---\n"
                  f"{from_intent}") if from_intent else ""
    note = (f"\n\nMerged with {from_id} by plan-overlap-judge "
            f"(artifact: {artifact}):\n{merge_feasibility}")
    into_s["intent"] = into_intent + from_block + note

    # success_criteria_seed: concatenation with AND (matches reconciler).
    into_scs = into_s.get("success_criteria_seed", "") or ""
    from_scs = from_s.get("success_criteria_seed", "") or ""
    if into_scs and from_scs:
        into_s["success_criteria_seed"] = f"{into_scs} AND {from_scs}"
    elif from_scs:
        into_s["success_criteria_seed"] = from_scs

    # provides: union, dedup, order-preserving.
    merged_provides = list(into_s.get("provides", []) or [])
    for tag in (from_s.get("provides") or []):
        if tag not in merged_provides:
            merged_provides.append(tag)
    into_s["provides"] = merged_provides

    # requires: union, drop self-references (entries whose tag is now in
    # the merged provides — would be a graph self-loop). External
    # entries stay regardless (out-of-graph, surface as preconditions).
    seen_req: set[tuple[str, str]] = set()
    merged_requires = []
    for entry in (list(into_s.get("requires", []) or [])
                  + list(from_s.get("requires", []) or [])):
        if not isinstance(entry, dict):
            continue
        tag = entry.get("tag", "")
        extent = entry.get("extent", "")
        key = (tag, extent)
        if key in seen_req:
            continue
        seen_req.add(key)
        if extent == "in_plan" and tag in merged_provides:
            continue
        merged_requires.append(entry)
    into_s["requires"] = merged_requires

    # depends_on: union minus self-references (would be a self-loop),
    # dedup, order-preserving.
    merged_deps: list[str] = []
    for dep in (list(into_s.get("depends_on", []) or [])
                + list(from_s.get("depends_on", []) or [])):
        if dep == from_id or dep == into_id:
            continue
        if dep not in merged_deps:
            merged_deps.append(dep)
    into_s["depends_on"] = merged_deps

    # files_likely_touched: union, order-preserving dedup.
    merged_files: list[str] = []
    for f in (list(into_s.get("files_likely_touched", []) or [])
              + list(from_s.get("files_likely_touched", []) or [])):
        if f not in merged_files:
            merged_files.append(f)
    into_s["files_likely_touched"] = merged_files

    # _merged_from telemetry — append so a chain of merges is traceable.
    merged_from = into_s.setdefault("_merged_from", [])
    if from_id not in merged_from:
        merged_from.append(from_id)
    for prior in (from_s.get("_merged_from") or []):
        if prior not in merged_from:
            merged_from.append(prior)

    # Remove `from` from its plan.
    for plan in plans:
        plan["subtasks"] = [
            s for s in plan.get("subtasks", []) if s.get("id") != from_id
        ]

    # Rewrite downstream depends_on references: from → into.
    for plan in plans:
        for s in plan.get("subtasks", []):
            deps = s.get("depends_on") or []
            if from_id in deps:
                new_deps: list[str] = []
                for dep in deps:
                    dep = into_id if dep == from_id else dep
                    if dep not in new_deps and dep != s.get("id"):
                        new_deps.append(dep)
                s["depends_on"] = new_deps

    return into_id


def _would_cycle_after(
    plans: list[dict], apply_fn: Callable[[list[dict]], None]
) -> bool:
    """Return True iff applying `apply_fn` to `plans` would leave the
    subtask dependency graph cyclic.

    Side-effect-free: `apply_fn` runs against a deep copy, so the passed
    `plans` is never mutated. Uses the same `_build_predecessor_graph` +
    `_tarjan_sccs` pair as the phase 2½ acyclicity gate (`phase_reconcile`)
    and the phase 2¾ post-merge backstop, so "cycle" means the same thing
    at every site.

    Used by `_apply_overlap_collisions` to tentatively test each `merge` /
    `drop_*` resolution *before* applying it: a resolution whose
    dependency-union would close a cycle is skipped rather than applied
    (DESIGN §5 *Post-merge acyclicity* — per-resolution cycle avoidance).
    The deep copy is the honest way to model the check: `_apply_overlap_merge`
    / `_apply_overlap_drop` mutate in place across all plans (remove the
    absorbed sid, rewrite downstream references), so re-deriving the union
    semantics without them would risk drifting from the real apply."""
    trial = copy.deepcopy(plans)
    apply_fn(trial)
    trial_subtasks = {
        s["id"]: s for plan in trial for s in plan.get("subtasks", [])
    }
    preds, _provs, _esrc = _build_predecessor_graph(trial_subtasks)
    succ: dict[str, set[str]] = {sid: set() for sid in trial_subtasks}
    for tgt, src_set in preds.items():
        for src in src_set:
            succ[src].add(tgt)
    return bool(_tarjan_sccs(set(trial_subtasks), succ))


def _apply_overlap_collisions(plans: list[dict],
                              collisions: list[dict]) -> list[dict]:
    """Apply a validated list of overlap-judge collisions to `plans`
    in input order, returning the per-resolution audit trail.

    Anchor-aware semantics: when a single sid appears in multiple
    non-`unresolvable` collisions (an *anchor*, computed by
    `_compute_overlap_anchors`), every merge it participates in
    keeps it as the survivor. This overrides `_apply_overlap_merge`'s
    default lex-smaller survivor rule for the cluster case. The
    rationale is structural: the anchor is by construction the
    subtask that overlaps with each of its partners, so absorbing
    each partner *into* the anchor matches what the judge described
    (every individual `merge_feasibility` statement is appended to
    the anchor's intent). The pairwise judge output is the simple
    protocol; Python walks the pairs into a coherent cluster
    resolution (DESIGN §12 — logic enforced in code, not in the
    prompt).

    Callers must run `_validate_overlap_judge_output` first, which
    catches the one pathological anchor case the apply loop cannot
    resolve safely: a `drop_*` whose `dropped_sid` is an anchor —
    the judge is contradicting itself (asking to delete the same
    subtask other collisions claim absorbs them).

    Merges between two anchors (legitimate within a single connected
    cluster — e.g. a triangle of `merge(A,B), merge(A,C), merge(B,C)`)
    fall through to the lex-smaller rule and are absorbed correctly
    because `_apply_overlap_merge`'s intent assembly preserves the
    absorbed subtask's full intent (DESIGN §5 merge_feasibility
    carry-forward invariant). Pairs whose endpoints have already
    collapsed to the same survivor (the redundant closing edge of
    such a cluster) are recorded as `skipped_redundant` entries in
    the returned audit trail.

    No state writes — the apply semantics can be unit-tested without
    standing up a worker or a State instance; the caller
    (`phase_overlap_judge`) handles state persistence around it.
    `log()` lines are emitted per resolution so a real run still
    surfaces what changed. `unresolvable` entries are treated as
    silent no-ops here — the caller is expected to have surfaced
    and die()d on them before invoking this helper.
    """
    anchors = _compute_overlap_anchors(collisions)
    applied: list[dict] = []
    # Transitive-rewrite map: sid → its current survivor in `plans`.
    # Populated as merges/drops execute. Used to rewrite an endpoint
    # that an earlier resolution already absorbed (e.g. anchor A merged
    # with B in pair 1, then a later collision references B — resolve
    # B → A so the helper sees a live endpoint).
    survivor_of: dict[str, str] = {}

    def _resolve(sid: str) -> str:
        """Transitively chase the survivor pointer until a fixed point
        (or unmapped sid). Path-compresses as it walks."""
        seen: list[str] = []
        cur = sid
        while cur in survivor_of and survivor_of[cur] != cur:
            seen.append(cur)
            cur = survivor_of[cur]
        for s in seen:
            survivor_of[s] = cur
        return cur

    for c in collisions:
        raw_a = c["a_sid"]
        raw_b = c["b_sid"]
        a_sid = _resolve(raw_a)
        b_sid = _resolve(raw_b)
        artifact = c.get("artifact", "")
        resolution = c["resolution"]

        # Both endpoints rewrote to the same survivor — an earlier
        # op already collapsed this exact pair (legitimately, in
        # connected-cluster shapes like triangles and 4-cycles where
        # the judge enumerates every pair of an artifact-overlap
        # cluster). Re-applying would no-op the helpers' defensive
        # die() or double-stamp `_merged_from`. Record the skip so
        # the audit trail (`state.data["plan_overlap_applied"]`)
        # reflects every collision the judge emitted, including the
        # redundant ones.
        if a_sid == b_sid:
            applied.append({
                "action": "skipped_redundant",
                "artifact": artifact,
                "collapsed_to": a_sid,
                "original_a_sid": raw_a,
                "original_b_sid": raw_b,
                "merge_feasibility": c.get("merge_feasibility", ""),
                "reason": c.get("reason", ""),
            })
            log(f"phase 2¾: collision {raw_a} ↔ {raw_b} "
                f"already collapsed to {a_sid} via earlier "
                f"resolution — skipped_redundant "
                f"(artifact: {artifact})")
            continue

        if resolution == "merge":
            mf = (c.get("merge_feasibility") or "").strip()
            # _validate_overlap_judge_output enforces non-empty; the
            # `or "<unset>"` is belt-and-suspenders against a logic bug.
            # Anchor-survivor rule: if exactly one endpoint is in the
            # anchor set, that endpoint wins regardless of lex order.
            # If both are anchors (legitimate when the judge emits
            # connecting pairs within a single broader cluster, e.g. a
            # triangle), we fall through to lex-smaller within the
            # cluster — semantics is preserved because the absorbed
            # subtask's intent carries forward (DESIGN §5
            # merge_feasibility carry-forward invariant). If neither
            # is, hint stays None → lex-smaller rule applies.
            survivor_hint: str | None = None
            if a_sid in anchors and b_sid not in anchors:
                survivor_hint = a_sid
            elif b_sid in anchors and a_sid not in anchors:
                survivor_hint = b_sid
            # Per-resolution cycle avoidance (DESIGN §5 *Post-merge
            # acyclicity*): a merge's dependency-union can close a
            # transitive cycle through a third subtask absent from the
            # merged pair. Test the merge against a copy first; if it
            # would cycle, skip it and keep both subtasks separate for
            # the integrator rather than die()ing the run. `survivor_of`
            # is deliberately NOT updated — both endpoints stay live so a
            # later collision referencing either resolves to a present sid.
            if _would_cycle_after(
                plans,
                lambda tr, a=a_sid, b=b_sid, art=artifact, m=mf,
                sh=survivor_hint: _apply_overlap_merge(
                    tr, a, b, art, m or "<unset>", survivor_hint=sh),
            ):
                applied.append({
                    "action": "skipped_would_cycle", "resolution": "merge",
                    "artifact": artifact,
                    "a_sid": a_sid, "b_sid": b_sid,
                    "original_a_sid": raw_a, "original_b_sid": raw_b,
                    "merge_feasibility": mf,
                    "reason": c.get("reason", ""),
                })
                log(f"phase 2¾: merge {a_sid} ↔ {b_sid} would introduce a "
                    f"dependency cycle (artifact: {artifact}) — "
                    f"skipped_would_cycle; both subtasks kept separate for "
                    f"the integrator to resolve at integration time")
                continue
            surviving = _apply_overlap_merge(
                plans, a_sid, b_sid, artifact, mf or "<unset>",
                survivor_hint=survivor_hint)
            dropped = b_sid if surviving == a_sid else a_sid
            survivor_of[dropped] = surviving
            applied.append({
                "action": "merge", "artifact": artifact,
                "surviving_sid": surviving, "dropped_sid": dropped,
                "reason": c.get("reason", ""),
            })
            log(f"phase 2¾: merged {dropped} → {surviving} "
                f"(artifact: {artifact})")
        elif resolution == "drop_a":
            # Cycle avoidance (same rationale as `merge` above): a drop
            # also unions the dropped subtask's `provides` into the
            # survivor and rewrites downstream `depends_on`, so it can
            # close a cycle too. Skip rather than die() if it would.
            if _would_cycle_after(
                plans,
                lambda tr, d=a_sid, s=b_sid: _apply_overlap_drop(
                    tr, dropped_sid=d, surviving_sid=s),
            ):
                applied.append({
                    "action": "skipped_would_cycle", "resolution": "drop_a",
                    "artifact": artifact,
                    "a_sid": a_sid, "b_sid": b_sid,
                    "original_a_sid": raw_a, "original_b_sid": raw_b,
                    "reason": c.get("reason", ""),
                })
                log(f"phase 2¾: drop_a {a_sid} (keeping {b_sid}) would "
                    f"introduce a dependency cycle (artifact: {artifact}) — "
                    f"skipped_would_cycle; both subtasks kept separate for "
                    f"the integrator to resolve at integration time")
                continue
            _apply_overlap_drop(plans, dropped_sid=a_sid,
                                surviving_sid=b_sid)
            survivor_of[a_sid] = b_sid
            applied.append({
                "action": "drop_a", "artifact": artifact,
                "surviving_sid": b_sid, "dropped_sid": a_sid,
                "reason": c.get("reason", ""),
            })
            log(f"phase 2¾: dropped {a_sid}, kept {b_sid} "
                f"(artifact: {artifact})")
        elif resolution == "drop_b":
            if _would_cycle_after(
                plans,
                lambda tr, d=b_sid, s=a_sid: _apply_overlap_drop(
                    tr, dropped_sid=d, surviving_sid=s),
            ):
                applied.append({
                    "action": "skipped_would_cycle", "resolution": "drop_b",
                    "artifact": artifact,
                    "a_sid": a_sid, "b_sid": b_sid,
                    "original_a_sid": raw_a, "original_b_sid": raw_b,
                    "reason": c.get("reason", ""),
                })
                log(f"phase 2¾: drop_b {b_sid} (keeping {a_sid}) would "
                    f"introduce a dependency cycle (artifact: {artifact}) — "
                    f"skipped_would_cycle; both subtasks kept separate for "
                    f"the integrator to resolve at integration time")
                continue
            _apply_overlap_drop(plans, dropped_sid=b_sid,
                                surviving_sid=a_sid)
            survivor_of[b_sid] = a_sid
            applied.append({
                "action": "drop_b", "artifact": artifact,
                "surviving_sid": a_sid, "dropped_sid": b_sid,
                "reason": c.get("reason", ""),
            })
            log(f"phase 2¾: dropped {b_sid}, kept {a_sid} "
                f"(artifact: {artifact})")
        # resolution == "unresolvable" treated as silent no-op — the
        # caller is expected to have surfaced and die()d on these
        # before invoking this helper.

    return applied


async def phase_overlap_judge(plans: list[dict], task: str, st: State,
                              caps: dict, models: dict[str, str],
                              efforts: dict[str, str | None]) -> list[dict]:
    """Phase 2¾: detect cross-planner surface-overlap collisions
    (DESIGN §5 *Cross-domain surface overlap*).

    Short-circuits when fewer than 2 planners contributed subtasks, or
    when the total subtask count is < 2 — single-planner / trivial runs
    cannot produce cross-planner surface collisions. Also short-circuits
    when `--skip-overlap-judge` (or its env / TOML mirror) is set; that
    knob lives on `st.data["skip_overlap_judge"]` and is checked here
    rather than in the caller so the skip is uniformly auditable.

    On a non-skipped run, spawns one `plan_overlap_judge` worker with
    the full reconciled subtask list, validates the output via
    `_validate_overlap_judge_output` (DESIGN §12 backstop on the
    merge-feasibility discipline), then applies each collision
    mechanically: `merge` collapses two subtasks into one via
    `_apply_overlap_merge`; `drop_a` / `drop_b` removes the dropped
    subtask via `_apply_overlap_drop`; `unresolvable` `die()`s at plan
    time with both sids + artifact + reason. Persists the full judge
    output to `st.data["plan_overlap_judge"]` and the mutation summary
    to `st.data["plan_overlap_applied"]` for audit.

    Returns the (possibly mutated) `plans` list, ready for `schedule()`.
    """
    # Cheap-skip conditions, in order of cost.
    if st.data.get("skip_overlap_judge"):
        log("phase 2¾: overlap-judge skipped (--skip-overlap-judge / "
            "LEERIE_SKIP_OVERLAP_JUDGE / skip_overlap_judge=true)")
        return plans

    # Count planners that actually contributed subtasks, and total
    # subtask count. The reconciler may have added a synthetic
    # `_reconciler` plan; count it as a contributor since its subtasks
    # can still collide with planner-authored ones.
    contributing_domains: set[str] = set()
    total_subtasks = 0
    for plan in plans:
        sts = plan.get("subtasks", []) or []
        if sts:
            contributing_domains.add(plan.get("domain", "<unknown>"))
            total_subtasks += len(sts)
    if len(contributing_domains) < 2:
        log(f"phase 2¾: overlap-judge skipped "
            f"(single contributing planner: "
            f"{sorted(contributing_domains)!r})")
        return plans
    if total_subtasks < 2:
        log(f"phase 2¾: overlap-judge skipped "
            f"(< 2 subtasks total: {total_subtasks})")
        return plans

    log(f"phase 2¾: plan-overlap judge over "
        f"{len(contributing_domains)} planner(s), {total_subtasks} subtask(s)")
    st.data["current_phase"] = "phase 2¾: overlap-judge"
    st.save()

    # Build the judge's input. The worker sees every subtask's
    # id/title/intent/scope_note/files_likely_touched/provides/requires/
    # depends_on. `requires` is left as the planner-emitted object form
    # since the judge may want to read `reason` text in addition to
    # tag/extent.
    subtask_views: list[dict] = []
    for plan in plans:
        for s in plan.get("subtasks", []) or []:
            subtask_views.append({
                "id": s.get("id", ""),
                "title": s.get("title", ""),
                "intent": s.get("intent", ""),
                "scope_note": s.get("scope_note", ""),
                "files_likely_touched": list(
                    s.get("files_likely_touched", []) or []),
                "provides": list(s.get("provides", []) or []),
                "requires": list(s.get("requires", []) or []),
                "depends_on": list(s.get("depends_on", []) or []),
            })
    payload = {"task": task, "subtasks": subtask_views}

    sys_prompt = load_prompt("plan_overlap_judge")
    user_prompt = (
        "JUDGE INPUT:\n" + json.dumps(payload, indent=2) +
        "\n\nReturn only the JSON object per your schema. If no surface "
        "collisions exist, return {\"collisions\": []}."
    )

    repo_root = Path(os.getcwd())
    oj_up_parts: list[str] = [user_prompt]

    async def _invoke_oj() -> dict:
        st.bump_workers(caps)
        return await claude_p(
            user_prompt="\n\n".join(oj_up_parts),
            system_prompt=sys_prompt,
            schema_key="plan_overlap_judge", cwd=str(repo_root),
            allowed_tools=INSPECT_TOOLS, max_turns=30,
            autonomous=False, caps=caps, st=st,
            model=models["plan_overlap_judge"],
            effort=efforts["plan_overlap_judge"],
            sid="plan_overlap_judge",
            add_dirs=st.data.get("inspect_dirs") or None,
        )

    async def _on_oj_fb(fb: str) -> dict:
        if len(oj_up_parts) > 1:
            oj_up_parts[-1] = fb
        else:
            oj_up_parts.append(fb)
        return {}

    output, oj_warnings = await _run_checked_loop(
        invoke=_invoke_oj,
        check=lambda r: check_overlap_judge_output(r, plans, repo_root),
        name="plan_overlap_judge",
        max_rounds=caps["judgment_check_rounds"],
        make_feedback_prompt=_on_oj_fb,
    )
    if output is None:
        die("plan overlap judge crashed and produced no result")
    for w in oj_warnings:
        log(f"  overlap-judge: {w}")

    # Persist the raw judge output for audit, even before applying —
    # if a later die() fires (unresolvable, or _validate_overlap_judge_output),
    # the user can inspect what the judge said.
    st.data["plan_overlap_judge"] = output
    st.save()

    collisions = (output.get("collisions") or [])
    if not collisions:
        log("phase 2¾: no surface collisions found")
        return plans

    # Index subtasks by id once for the validator's id-existence check.
    by_id: dict[str, dict] = {}
    for plan in plans:
        for s in plan.get("subtasks", []) or []:
            by_id[s["id"]] = s
    _validate_overlap_judge_output(output, by_id)

    # Surface unresolvable BEFORE mutating anything — same fail-closed
    # discipline as phase_reconcile._check_unresolvable. The user gets
    # the judge's diagnosis without phantom mutations on disk.
    unresolvable = [c for c in collisions
                    if c.get("resolution") == "unresolvable"]
    if unresolvable:
        bullets = "\n".join(
            f"  • {c['a_sid']} ↔ {c['b_sid']} "
            f"(artifact: {c.get('artifact', '<unspecified>')}): "
            f"{c.get('reason', '<no reason>')}"
            for c in unresolvable
        )
        die(
            f"plan-overlap judge found {len(unresolvable)} unresolvable "
            "surface collision(s) in the reconciled plan:\n"
            f"{bullets}\n"
            "Each collision is two planners producing the same exported "
            "artifact with structurally incompatible APIs (DESIGN §5 "
            "*Cross-domain surface overlap*). Auto-merging would produce "
            "a frankenstein implementer spec the judge correctly "
            "refused. To unblock:\n"
            "  • Refine the task description to disambiguate the "
            "disputed surface (name which API survives, or split it into "
            "two distinct artifacts), and re-run.\n"
            "  • Or manually delete one of the colliding subtask specs "
            "in <state-root>/runs/<run-id>/subtasks/ and `--resume`."
        )

    # Apply merges and drops in input order via the pure helper.
    applied = _apply_overlap_collisions(plans, collisions)

    # Post-merge acyclicity backstop. `_apply_overlap_collisions` already
    # skips (skipped_would_cycle) any resolution whose dependency-union
    # would close a cycle — per-resolution cycle avoidance (DESIGN §5
    # *Post-merge acyclicity*). This final Tarjan pass must therefore never
    # find a cycle; if it does, `_would_cycle_after` and the real apply path
    # disagreed, which is an orchestrator logic bug, not a user-recoverable
    # task-shape problem. Retained as defense-in-depth against future drift,
    # mirroring `_apply_overlap_merge`'s defensive missing-sid die().
    post_merge_subtasks: dict[str, dict] = {}
    for plan in plans:
        for s in plan.get("subtasks", []):
            post_merge_subtasks[s["id"]] = s
    pm_preds, _pm_provs, pm_edge_sources = _build_predecessor_graph(
        post_merge_subtasks)
    pm_succ: dict[str, set[str]] = {sid: set() for sid in post_merge_subtasks}
    for tgt, src_set in pm_preds.items():
        for src in src_set:
            pm_succ[src].add(tgt)
    pm_sccs = _tarjan_sccs(set(post_merge_subtasks), pm_succ)
    if pm_sccs:
        diag = _format_cycle_diagnostic(
            pm_sccs, pm_succ, pm_edge_sources, {}, post_merge_subtasks)
        die(
            f"phase 2¾: post-merge acyclicity backstop found "
            f"{len(pm_sccs)} dependency cycle(s) that per-resolution cycle "
            f"avoidance should have skipped:\n{diag}\n"
            "This is an orchestrator logic bug — `_would_cycle_after` and "
            "the real `_apply_overlap_collisions` apply path disagreed. "
            "Re-run with --skip-overlap-judge to bypass phase 2¾ entirely, "
            "and please report this run (the tentative cycle check missed a "
            "cycle the real apply produced)."
        )

    st.data["plan_overlap_applied"] = applied
    st.save()
    n_merge = sum(1 for a in applied if a['action'] == 'merge')
    n_drop = sum(1 for a in applied if a['action'].startswith('drop'))
    n_skip = sum(1 for a in applied if a['action'] == 'skipped_redundant')
    n_cycle_skip = sum(1 for a in applied
                       if a['action'] == 'skipped_would_cycle')
    parts = [f"{n_merge} merge", f"{n_drop} drop"]
    if n_skip:
        parts.append(f"{n_skip} skipped_redundant")
    if n_cycle_skip:
        parts.append(f"{n_cycle_skip} skipped_would_cycle")
    log(f"phase 2¾: applied {len(applied)} resolution(s) "
        f"({', '.join(parts)})")
    return plans


def _build_predecessor_graph(
    subtasks: dict[str, dict],
) -> tuple[dict[str, set[str]], dict[str, list[str]],
           dict[tuple[str, str], str]]:
    """Build the predecessor graph from a merged subtasks dict.

    Returns `(preds, providers, edge_sources)`:
    - `preds[sid]` is the set of subtask ids that must complete before `sid`.
    - `providers[tag]` is the list of subtask ids that declare `tag` in
      their `provides`.
    - `edge_sources[(pred, succ)]` is a diagnostic label describing what
      created the edge: `"depends_on"` for a planner-declared
      `depends_on`, or `"requires:<tag>"` for a cross-domain capability-
      tag match. Used by the phase 2½ cycle gate's edge-attribution
      message and by future scheduler diagnostics.

    Shared between the phase 2½ acyclicity gate (`phase_reconcile`) and
    the phase 3 scheduler (`schedule`) so the two cannot drift in what
    counts as an edge. Pure function — no logging, no side effects, no
    `die()`. Callers handle empties and cycles themselves.

    `requires` entries are objects `{tag, extent, reason?}` (DESIGN §5
    `requires.extent`); only `extent: in_plan` entries become graph
    edges — `external` entries are out-of-graph by planner declaration
    and surface as preconditions in `plan.json` instead.
    """
    providers: dict[str, list[str]] = {}
    for sid, s in subtasks.items():
        for cap in s.get("provides", []):
            providers.setdefault(cap, []).append(sid)

    preds: dict[str, set[str]] = {sid: set() for sid in subtasks}
    edge_sources: dict[tuple[str, str], str] = {}
    for sid, s in subtasks.items():
        for dep in s.get("depends_on", []):
            if dep in subtasks:
                preds[sid].add(dep)
                # `depends_on` wins over a `requires`-tag attribution for
                # the same edge — the planner explicitly declared it, so
                # diagnostics should name that as the source.
                edge_sources[(dep, sid)] = "depends_on"
        for entry in s.get("requires", []):
            if not isinstance(entry, dict) or entry.get("extent") != "in_plan":
                continue
            cap = entry.get("tag", "")
            for provider in providers.get(cap, []):
                if provider != sid:
                    preds[sid].add(provider)
                    # Don't overwrite a `depends_on` attribution.
                    edge_sources.setdefault(
                        (provider, sid), f"requires:{cap}")
    return preds, providers, edge_sources


def detect_no_work(plans: list[dict]) -> dict[str, str] | None:
    """Return a `{domain: basis}` map iff *every* plan satisfies
    `status == "ready"` and `subtasks == []` — the cleared-but-empty
    terminal state (DESIGN §8): the planner gate cleared and confirmed
    the task is already satisfied on HEAD. Else return None.

    The basis is read from `plan["confidence"]["basis"]` (a required
    string in the planner schema, `leerie.py:587-608`). Falls back to a
    placeholder string if the field is missing or non-string, rather
    than raising — the orchestrator's job here is to surface the
    planner's reasoning, not to re-validate the schema.

    Returns None on the mixed and all-blocked cases so the normal
    `schedule()` path (and its all-blocked die) still fires."""
    if not plans:
        return None
    out: dict[str, str] = {}
    for plan in plans:
        if plan.get("status") != "ready":
            return None
        if plan.get("subtasks"):
            return None
        domain = plan.get("domain") or "<unknown>"
        basis = ((plan.get("confidence") or {}).get("basis")
                 if isinstance(plan.get("confidence"), dict) else None)
        if not isinstance(basis, str) or not basis.strip():
            basis = "<no basis given by planner>"
        out[domain] = basis
    return out


def _finish_no_work_run(st: State, no_work_map: dict[str, str]) -> None:
    """Terminal-state handler for the cleared-but-empty case
    (DESIGN §8 *The cleared-but-empty terminal state*). Records
    `no_work_required=true` in state.json, writes `finished_at` to
    state.json + run.json, logs the no-work summary, and returns.

    `no_push=True` on the run.json write is load-bearing: the host
    launcher polls `finished_at` as the "ready to push" sentinel, and
    there is no run branch to push (none was materialized). `_derive_
    run_status` reads `finished_at` + the missing `pushed_at` / `pr_url`
    and renders the run as `done` in `leerie --list`.

    Does NOT invoke `finalize.sh` / `cleanup.sh` — `finalize.sh` would
    fail its non-empty-branch check and `cleanup.sh` has no subtask
    branches to drop."""
    log("phase 3: nothing to schedule — every planner returned "
        "status=ready with zero subtasks")
    for domain, basis in no_work_map.items():
        log(f"  {domain}: no work (basis: {basis!r})")
    st.data["no_work_required"] = True
    st.data["no_work_reasons"] = dict(no_work_map)
    st.data["finished_at"] = now()
    st.data["waves"] = []
    st.data["subtask_status"] = {}
    st.data["current_phase"] = "done: no work required"
    st.save()
    _write_run_json(
        st.run_dir,
        finished_at=st.data["finished_at"],
        no_push=True,
        no_verify=False,
    )
    log("done — task already satisfied on HEAD; no commits made, "
        "working branch unchanged")
    # Surface what the run cost — the classifier + one planner per
    # category still ran. Same shape as phase_finalize's run-weight
    # line so the no-work and normal paths report cost identically.
    tel = st.data.get("telemetry")
    if tel:
        log(f"run weight: {tel.get('calls', 0)} claude -p calls, "
            f"${tel.get('cost_usd', 0.0):,.2f}, "
            f"{tel.get('input_tokens', 0):,} in / "
            f"{tel.get('output_tokens', 0):,} out tokens "
            f"(see {st.path})")


def schedule(plans: list[dict]) -> tuple[dict, list[list[str]]]:
    """Phase 3 (pure Python): merge plans, resolve intra- and cross-domain
    dependencies, topologically sort into waves. Deterministic."""
    log("phase 3: scheduling")
    subtasks: dict[str, dict] = {}
    blocked_domains: list[str] = []
    for plan in plans:
        for s in plan.get("subtasks", []):
            subtasks[s["id"]] = s
        if plan.get("status") == "blocked":
            blocked_domains.append(plan.get("domain", "<unknown>"))
    if not subtasks:
        if blocked_domains:
            die("planners produced no subtasks — all relevant domains exited "
                f"blocked at the evidence gate: {', '.join(blocked_domains)}. "
                "See each planner's confidence.gap_to_close for what evidence "
                "would unblock; raise --confidence-rounds or supply the "
                "missing information and re-run.")
        die("planners produced no subtasks")
    if blocked_domains:
        # Partial block: some domains succeeded, others exited blocked.
        # The earlier phase_plan log line carried each blocked domain's
        # gap, but by the time the user is reading scheduling output that
        # signal is several phases back. Surface it again here so a
        # silently-dropped domain is not invisible in the run summary.
        log(f"WARNING: {len(blocked_domains)} domain(s) exited blocked at "
            f"the planner evidence gate and contributed no subtasks: "
            f"{', '.join(blocked_domains)}. Proceeding with the ready "
            "domains; see the per-category log lines above for each "
            "blocked planner's gap_to_close.")

    preds, _providers, _edge_sources = _build_predecessor_graph(subtasks)

    # Kahn's algorithm -> waves. Cycles are caught upstream by phase 2½'s
    # acyclicity gate (reconciler output) and phase 2¾'s post-merge gate
    # (overlap-judge merges). This fallback stays as defense-in-depth.
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


def check_budget_feasibility(st: State, caps: dict,
                             subtasks: dict,
                             waves: list[list[str]]) -> None:
    """Phase 3 pure-Python gate (DESIGN §13 *Budget feasibility — fail
    fast at the cheapest moment*). Called once in `_run_phases()`
    immediately after `schedule()` returns and before `write_plan()`
    persists anything. Estimates the `claude -p` calls the run will
    consume from here to finalize and `die()`s with
    EXIT_BUDGET_INFEASIBLE when the estimate exceeds
    `caps["max_total_workers"]`.

    Skipped when `st.data["skip_budget_check"]` is True (the
    `--skip-budget-check` opt-out). The runtime backstop in
    `State.bump_workers()` remains as the load-bearing ultimate
    enforcement either way.

    The estimate adds the *remaining* call count to the
    *already-spent* `worker_count` (which reflects every upstream
    phase: classifier, provision, planners, reconciler, overlap
    judge), so the only free variable is the per-subtask multiplier
    — calibrated empirically and documented at
    `DEFAULT_CAPS["subtask_call_estimate"]`."""
    if st.data.get("skip_budget_check"):
        return
    cap = caps["max_total_workers"]
    already_spent = st.data.get("worker_count", 0)
    n_subtasks = len(subtasks)
    n_waves = len(waves)
    # 1 fixed: `pr_writer` (the only post-execute LLM call —
    # finalize itself is shell scripts: `git push` + `gh pr create`).
    # Plus up to `conformance_rounds` calls for the final-tree
    # conformance pass (DESIGN §6 *Worktree and integration model*,
    # final-tree pass) which runs once on the integrated staging
    # worktree after the last wave. Everything else (classifier,
    # planners, reconciler, overlap_judge, provision) has already
    # spawned and been counted into `worker_count` by the time we get
    # here.
    remaining_estimate = (
        n_subtasks * caps["subtask_call_estimate"]
        + n_waves
        + caps["conformance_rounds"]
        + 1
    )
    total_estimate = already_spent + remaining_estimate
    margin = caps["budget_safety_margin"]
    if total_estimate * margin > cap:
        recommended = int(total_estimate * margin) + 5
        die(
            f"budget infeasible: planner produced {n_subtasks} subtask(s) "
            f"across {n_waves} wave(s); {already_spent} `claude -p` "
            f"call(s) already spent on upstream phases (classifier / "
            f"planner / reconciler / overlap-judge / provision); "
            f"estimated {remaining_estimate:g} more needed "
            f"(implementers + conformers + integrators + pr_writer). "
            f"Total estimate {total_estimate:g} × safety margin {margin} "
            f"= {total_estimate * margin:g} vs --max-workers {cap}. "
            f"Re-run with --max-workers {recommended}, split the task "
            f"into smaller scopes, or use --skip-budget-check to push "
            f"through (the runtime backstop in State.bump_workers() will "
            f"still fire if the estimate was correct).",
            code=EXIT_BUDGET_INFEASIBLE,
        )


def _write_subtask_artifacts(leerie_dir: Path, sid: str,
                              artifacts: list) -> None:
    """Persist a subtask's `artifacts` result field to
    `<state-root>/runs/<run-id>/artifacts/<sid>.json` so downstream subtasks
    can read it. Atomic temp + rename, matching `State.save()`. See
    DESIGN §5 *Artifact passing between subtasks*.

    The orchestrator owns this directory — workers do not write here
    directly; the artifact payload travels through the implementer
    result JSON and the orchestrator materializes the file. Callers
    must check the artifacts array is non-empty before calling: an
    empty array is the common code-implementation case and no file
    should be written for it (a present-but-empty file would
    misrepresent the subtask as having produced something).
    """
    art_dir = leerie_dir / "artifacts"
    art_dir.mkdir(exist_ok=True)
    path = art_dir / f"{sid}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"subtask_id": sid, "artifacts": artifacts},
                              indent=2))
    tmp.replace(path)


def _read_upstream_artifacts(leerie_dir: Path,
                              predecessor_ids: list[str]) -> list[dict]:
    """Read the artifacts files for `predecessor_ids` and return them
    in input order. Missing files are skipped silently — the common
    code-implementation predecessor produces no artifacts file and
    that is not an error. Returns a list of `{subtask_id, artifacts}`
    dicts, one per predecessor that produced any.
    """
    out: list[dict] = []
    art_dir = leerie_dir / "artifacts"
    for pid in predecessor_ids:
        path = art_dir / f"{pid}.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("artifacts"):
            out.append(payload)
    return out


def _format_upstream_artifacts_for_sid(leerie_dir: Path,
                                        sid: str) -> str | None:
    """End-to-end helper: load the persisted plan, recompute the
    predecessor graph, read the artifacts files for `sid`'s
    predecessors, and render the prompt section. Returns None when
    there is nothing to inject (no predecessors, no predecessor
    produced artifacts, plan.json missing or unreadable).

    Re-deriving the graph from disk on every implementer spawn keeps
    the resume path identical to the fresh-run path — both load the
    same `plan.json` and run the same `_build_predecessor_graph`.
    The cost is one small JSON read per implementer; negligible at
    leerie's worker cadence.
    """
    plan_path = leerie_dir / "plan.json"
    if not plan_path.exists():
        return None
    try:
        plan = json.loads(plan_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    subtasks = plan.get("subtasks") or {}
    if not isinstance(subtasks, dict) or sid not in subtasks:
        return None
    preds, _, _ = _build_predecessor_graph(subtasks)
    predecessor_ids = sorted(preds.get(sid, set()))
    if not predecessor_ids:
        return None
    payloads = _read_upstream_artifacts(leerie_dir, predecessor_ids)
    return _format_upstream_artifacts_section(payloads)


def _format_upstream_artifacts_section(payloads: list[dict]) -> str | None:
    """Render upstream artifact payloads as a prompt section, or None
    if there is nothing to render. Inlines `content` verbatim — see
    DESIGN §5 *Artifact passing between subtasks* on tight-context
    discipline: a subtask only ever sees artifacts from its declared
    predecessors, never the run-wide artifact set.
    """
    if not payloads:
        return None
    lines = ["## Artifacts from upstream subtasks",
             "",
             "The subtasks listed below produced structured deliverables "
             "you are expected to consume. Treat each artifact's content "
             "as part of your specification for this subtask."]
    for payload in payloads:
        pid = payload.get("subtask_id", "?")
        for art in payload.get("artifacts", []):
            name = art.get("name", "")
            kind = art.get("kind", "text")
            summary = art.get("summary", "").strip()
            content = art.get("content", "")
            lines.append("")
            lines.append(f"### {pid} — {name} ({kind})")
            if summary:
                lines.append(summary)
                lines.append("")
            lines.append(content)
    return "\n".join(lines)


def write_plan(leerie_dir: Path, task: str, st: State,
               subtasks: dict, waves: list[list[str]]) -> None:
    """Persist the merged plan and per-subtask spec files the implementers read."""
    answers = st.data.get("answers", {})
    sot = answers.get("source_of_truth", "codebase")
    # External preconditions are the planner-declared out-of-graph
    # requires entries collected during phase_reconcile (DESIGN §5
    # `requires.extent`). Surfacing them in plan.json gives the
    # launcher / integrator / human a deploy-notes section without
    # treating them as build-graph edges. Empty list when no planner
    # declared any `extent: external` entry — common case.
    preconditions = st.data.get("external_preconditions", []) or []
    (leerie_dir / "plan.json").write_text(json.dumps(
        {"task": task, "waves": waves, "subtasks": subtasks,
         "preconditions": preconditions}, indent=2))
    sub_dir = leerie_dir / "subtasks"
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


def _format_provision_recipe_section(recipe: list[dict],
                                      *, audience: str) -> str | None:
    """Render the persisted provision recipe as a prompt section, or
    return None if the recipe is empty / all-`none`.

    `audience` controls the framing:
      - "implementer": "decide whether your subtask needs them"
      - "conformer": "ensure deps are installed before BUILD/LINT/TEST"

    The recipe is detected in phase_provision but executed by workers
    in their own worktrees (DESIGN §6½ "Worker-driven install"). This
    function is the prompt-injection helper that hands the recipe to
    workers verbatim — no per-worker variation, same string in every
    prompt.
    """
    install_entries = [e for e in recipe
                       if e.get("kind") in ("install", "build")
                       and e.get("command")]
    if not install_entries:
        return None

    lines = ["", "PROVISION_RECIPE:"]
    if audience == "implementer":
        lines.append(
            "  The orchestrator detected the following install (and "
            "follow-on build) commands for this repo. Your worktree "
            "starts with NO installed dependencies and no build "
            "outputs. Decide whether your subtask needs them — if yes, "
            "run them via Bash in the order shown. The package-manager "
            "caches (pnpm store, pip wheel cache, go module cache, cargo "
            "registry) are warm and shared across worktrees, so "
            "re-running these is fast. These are advisory: skip them if "
            "your subtask is purely documentation, config, or otherwise "
            "doesn't touch buildable code."
        )
    elif audience == "conformer":
        lines.append(
            "  Your worktree starts with NO installed dependencies "
            "(or only those the implementer chose to install) and no "
            "build outputs. Before running BUILD_CMD / LINT_CMD / "
            "TEST_CMD, ensure deps and any required build artifacts are "
            "present — either run the install (and follow-on build) "
            "command(s) yourself first, in the order shown, or react to "
            "a failing test/build that diagnoses missing deps and run "
            "them then. The caches are warm so re-running is fast."
        )
    else:
        raise ValueError(f"unknown audience {audience!r}")
    for i, e in enumerate(install_entries, 1):
        cmd_str = " ".join(e["command"])
        wd = e.get("working_dir", ".")
        timeout = e.get("timeout_s") or 1800
        lines.append(f"  {i}. {cmd_str}   (cwd: {wd}, timeout: {timeout}s)")
    return "\n".join(lines)


async def run_implementer(sid: str, leerie_dir: Path, caps: dict, st: State,
                          models: dict[str, str],
                          efforts: dict[str, str | None],
                          continuation: bool = False, note: str = "") -> dict:
    """Spawn one implementer for one subtask in its own worktree. Handles
    both kinds of continuation up to the shared `subtask_continuations`
    cap: context-exhaustion handoffs and DESIGN §11 mid-execution
    clarifications."""
    sys_prompt = load_prompt("implementer")
    proc = await run_script("new-worktree.sh", sid, st.run_id)
    if proc.returncode != 0:
        raise WorkerError(f"worktree creation failed for {sid}: {proc.stderr.strip()}")
    worktree = proc.stdout.strip().splitlines()[-1]
    # The fresh worktree has NO installed deps. The implementer runs
    # installs itself in its own worktree against the shared
    # package-manager caches (DESIGN §6½ "Worker-driven install"); the
    # recipe to follow is injected into its prompt below. We don't
    # pre-install here because (a) it would clobber the host's
    # repo_root checkout that this worktree shares the package cache
    # with, and (b) workers correctly skip install when their subtask
    # is config-only / docs-only.

    # DESIGN §11 mid-execution clarification: the worker may exit with
    # `needs-clarification` only when --clarify is in effect. Without
    # --clarify (the default) the user has not opted into questions, so
    # the worker must run the same codebase→research probe and make a
    # documented best-effort decision instead of interrupting.
    can_ask_user = st.data.get("clarify", False)

    up = [f"Execute subtask `{sid}`.",
          f"LEERIE_DIR is {leerie_dir} (absolute).",
          f"Read your spec at {leerie_dir}/subtasks/{sid}.json.",
          "Your current working directory IS your isolated worktree — make and "
          "commit all code changes here.",
          # DESIGN §8 + §13: evidence-gate bound, prompt-governed.
          f"CONFIDENCE_ROUNDS: {caps['confidence_rounds']} (the maximum "
          "number of evidence-gate iterations before you exit blocked).",
          # DESIGN §11 mid-execution clarification gate.
          f"CAN_ASK_USER: {str(can_ask_user).lower()} (when true, you may "
          "exit `needs-clarification` for a genuine intent question that "
          "neither the codebase nor research can resolve; when false, you "
          "must make a best-effort decision and proceed)."]
    recipe_section = _format_provision_recipe_section(
        (st.data.get("provision") or {}).get("recipe") or [],
        audience="implementer")
    if recipe_section is not None:
        up.append(recipe_section)
    # DESIGN §5 *Artifact passing between subtasks*: inject the
    # artifacts produced by this subtask's predecessors. The
    # predecessor graph is the same one the scheduler uses for wave
    # ordering — `depends_on` + `requires`-derived edges. The plan
    # lives on disk in `plan.json`; we re-derive the graph there
    # rather than rely on in-memory state so resume works the same as
    # a fresh run.
    artifacts_section = _format_upstream_artifacts_for_sid(leerie_dir, sid)
    if artifacts_section is not None:
        up.append(artifacts_section)
    # DESIGN §9: surface the repo's authoritative convention docs to the
    # implementer at write time (not only to the post-hoc conformer), so a
    # new component matches the repo's design conventions on the first try.
    # st.repo_root is populated at State construction — already in scope.
    convention_docs_section = _format_convention_docs_section(st.repo_root)
    if convention_docs_section is not None:
        up.append(convention_docs_section)
    if continuation:
        up.append(f"This is a CONTINUATION. Read the checkpoint at "
                  f"{leerie_dir}/checkpoints/{sid}.md, validate it against the "
                  f"actual repo state, then continue.")
    if note:
        up.append(f"NOTE FROM ORCHESTRATOR: {note}")

    st.bump_workers(caps)
    try:
        return await claude_p(user_prompt="\n".join(up), system_prompt=sys_prompt,
                              schema_key="implementer", cwd=worktree,
                              allowed_tools=ACT_TOOLS, max_turns=120,
                              autonomous=True, caps=caps, st=st,
                              model=models["implementer"],
                              effort=efforts["implementer"], sid=sid)
    except WorkerError as e:
        # worker could not return schema-valid output even after a retry
        # (e.g. it hit --max-turns mid-task) -> treat as a handoff so a fresh
        # implementer can continue from whatever checkpoint exists. If no
        # checkpoint was written, validate_result tags it
        # `failure_kind="empty_handoff"` (retryable); see the TimeoutExpired
        # arm below for the full retry-classification rationale.
        return {"subtask_id": sid, "status": "incomplete-handoff",
                "checkpoint_path": str(leerie_dir / "checkpoints" / f"{sid}.md"),
                "summary": f"worker produced no schema-valid result: {e}"}
    except subprocess.TimeoutExpired:
        # worker hit the per-process wall-clock cap (`worker_timeout_sec`,
        # default 5400s / 90 min). _invoke killed the claude -p child
        # and re-raised TimeoutExpired. Without this catch the
        # exception would escape settle_subtask → gather_or_cancel →
        # phase_execute → orchestrate → main()'s catch-all and dump a
        # 50KB traceback (with the entire claude -p command line) to
        # the user's terminal. Same treatment as the WorkerError
        # arm — a fresh implementer can continue from any partial
        # checkpoint. If no checkpoint was written, validate_result
        # tags the missing-checkpoint case as `failure_kind="empty_handoff"`
        # which `_retryable_failure` accepts as retryable; the
        # failed_retries cap then bounds the chain.
        #
        # Why retry rather than terminal: leerie's typical usage is
        # unattended (overnight runs), so a transient hang has real
        # value in recovering on a fresh process. The worst case —
        # one extra 90-min worker invocation bounded by failed_retries
        # — is an acceptable trade for that recovery chance. An
        # operator-supervised mode that wanted fail-fast semantics
        # would need a separate cap (not currently in scope).
        timeout = caps.get("worker_timeout_sec", "?")
        return {"subtask_id": sid, "status": "incomplete-handoff",
                "checkpoint_path": str(leerie_dir / "checkpoints" / f"{sid}.md"),
                "summary": (f"worker timed out after {timeout}s "
                            "(worker_timeout_sec cap) — fresh implementer "
                            "can continue from any partial checkpoint")}


# Per DESIGN §12 "prompts are advisory, code enforces" (and its inverse:
# deterministic code must not make judgment calls on prose), the retry
# classifier dispatches on a structured `failure_kind` tagged at the
# producer rather than substring-matching a free-form `reason` string.
# Adding a new retryable failure mode: extend this set AND have the
# producer return the new kind. The coupling test in
# tests/test_retryable_failure.py asserts every producer's retryable-path
# return uses a kind in this set.
_RETRYABLE_FAILURE_KINDS = frozenset({
    "no_commits",      # check_branch_has_commits — implementer claimed
                       # complete with nothing committed
    "dirty_worktree",  # inline status-porcelain check in settle_subtask —
                       # uncommitted changes would be lost on integration
    "empty_handoff",   # validate_result — `incomplete-handoff` envelope
                       # with a checkpoint_path that does not exist on
                       # disk. Two known triggers: (a) the Claude Code
                       # session-limit / rate-limit no-op case (primarily
                       # caught by detect_session_limit() upstream; this
                       # is the safety net for a message-format change),
                       # and (b) a worker that hit --max-turns with no
                       # checkpoint written, synthesized into the same
                       # envelope by run_implementer's WorkerError /
                       # TimeoutExpired catches. Both are corrective-note
                       # cases — a fresh worker can plausibly do better.
})


def _retryable_failure(kind: str) -> bool:
    """The retry policy, in one place. Dispatches on a structured
    `failure_kind` produced by the failure source — never on prose.

    Retryable kinds (`_RETRYABLE_FAILURE_KINDS`): a corrective note to a
    fresh worker can plausibly fix the failure.

    Everything else is terminal: the worker is broken or dishonest, and
    re-running it burns a worker invocation against the budget for no
    expected gain. The canonical terminal kind emitted by `validate_result`
    and `settle_subtask` is `"broken"` (cross-field invariant violation,
    diff touched a protected path, worker-level error). Any unknown kind
    is also terminal — adding a new retryable mode requires extending
    `_RETRYABLE_FAILURE_KINDS`."""
    return kind in _RETRYABLE_FAILURE_KINDS


# --- post-work conformance phase (DESIGN §9 *Post-work conformance*) -------
# Runs after the implementer's success-path settlement checks pass, before
# `settle_subtask` returns. The phase is advisory: nothing it does or fails
# to do can produce a `failed` / `blocked` subtask status. The code-enforced
# guarantees are narrow — rule-file discovery is deterministic, the worker's
# output is schema-validated, and the same diff-scope check that gates the
# implementer is re-applied to the conformer's commits. Everything else
# (which rule was violated, whether build/lint/tests passed, whether docs
# are actually stale) is the worker's judgment, surfaced as warnings.

# Fixed, capped allowlist of rule-file paths the discovery function checks.
# Order is the priority order the conformer reads in. Adding to this list
# is a design change (DESIGN §9) — the worker is told these are
# authoritative and only these.
_RULES_FILE_CANDIDATES = (
    "CLAUDE.md", "AGENTS.md", ".agent.md",
    ".cursorrules", ".windsurfrules",
    "docs/CLAUDE.md", "docs/AGENTS.md",
    "docs/CONVENTIONS.md", "docs/STYLE.md",
    # Design-system docs: a repo's component/color/banner conventions live
    # here, not in CLAUDE.md. Discovering them lets the conformer enforce
    # them and (DESIGN §9) the implementer follow them at write time, so a
    # new UI component matches the design on the first try instead of
    # drifting and relying on a post-hoc catch.
    "docs/DESIGN-SYSTEM.md", "docs/DESIGN_SYSTEM.md", "docs/UI.md",
    "README.md", "CONTRIBUTING.md",
    "docs/DESIGN.md", "docs/IMPLEMENTATION.md",
)


def discover_rules_files(repo_root: Path) -> list[Path]:
    """Return existing rule-file paths from `_RULES_FILE_CANDIDATES`, in
    declaration order, capped at the candidate-list length. Never raises;
    never recurses; returns [] cleanly when nothing matches."""
    out: list[Path] = []
    for rel in _RULES_FILE_CANDIDATES:
        p = repo_root / rel
        try:
            if p.is_file():
                out.append(p)
        except OSError:
            continue
    return out


def _format_rules_paths(rules_files: list[Path], repo_root: Path) -> str:
    """Render discovered rule/convention paths relative to `repo_root` for
    prompt injection. Shared by the conformer's `RULES_FILES:` line and the
    implementer's `CONVENTION_DOCS:` line so the two formats never diverge.
    Returns `(none)` for an empty list."""
    return ", ".join(
        str(p.relative_to(repo_root)) if str(p).startswith(str(repo_root))
        else str(p)
        for p in rules_files
    ) or "(none)"


def _format_convention_docs_section(repo_root: Path) -> str | None:
    """Build the implementer's `CONVENTION_DOCS:` prompt block from the same
    discovery the conformer uses (DESIGN §9). Returns None when the repo has
    no discoverable convention docs, so no empty block is injected. Paths
    only — the implementer opens the ones its subtask needs from its own
    worktree checkout, avoiding inlining a large design-system doc."""
    rules_files = discover_rules_files(repo_root)
    if not rules_files:
        return None
    return ("CONVENTION_DOCS (authoritative repo conventions — read the ones "
            "relevant to your subtask and follow their patterns, especially "
            "design-system / component conventions for any UI work): "
            f"{_format_rules_paths(rules_files, repo_root)}")


def _is_rails_repo(repo_root: Path) -> bool:
    """True when the repo is a Rails application. Requires both a
    Gemfile.lock (rules out plain scripts) and bin/rails (the canonical
    file generated by `rails new`, absent in Sinatra/Grape/etc.)."""
    return ((repo_root / "Gemfile.lock").is_file()
            and (repo_root / "bin" / "rails").is_file())


def _infer_build_lint_test(repo_root: Path) -> dict[str, str]:
    """Best-effort guess at the repo's build / lint / test commands. Returns
    a dict with keys 'build', 'lint', 'test' — empty string when no command
    could be inferred for that axis. The conformer is told an empty string
    means "not applicable; report ran=false." This is a *suggestion* the
    worker may override based on what it sees in the repo."""
    out = {"build": "", "lint": "", "test": ""}
    if (repo_root / "Makefile").is_file():
        # Don't assume specific targets — the conformer reads the Makefile
        # and picks. We just signal "a Makefile exists."
        out["build"] = "make"
    if (repo_root / "package.json").is_file():
        # Lockfile-aware PM detection — precedence mirrors
        # detect_recipe_from_lockfiles (pnpm > yarn > bun > npm).
        # `<pm> run build` / `<pm> run test` uniformly: bun's bare
        # `bun test` / `bun build` invoke built-in tools, not
        # package.json scripts.
        if (repo_root / "pnpm-lock.yaml").is_file():
            pm = "pnpm"
        elif (repo_root / "yarn.lock").is_file():
            pm = "yarn"
        elif (repo_root / "bun.lockb").is_file() or \
             (repo_root / "bun.lock").is_file():
            pm = "bun"
        else:
            pm = "npm"
        out["build"] = out["build"] or f"{pm} run build"
        out["test"] = out["test"] or f"{pm} run test"
    if (repo_root / "pyproject.toml").is_file() or \
       (repo_root / "pytest.ini").is_file() or \
       (repo_root / "setup.cfg").is_file():
        out["test"] = out["test"] or "pytest"
    if (repo_root / "Cargo.toml").is_file():
        out["build"] = out["build"] or "cargo build"
        out["test"] = out["test"] or "cargo test"
    if (repo_root / "go.mod").is_file():
        out["build"] = out["build"] or "go build ./..."
        out["test"] = out["test"] or "go test ./..."
    if (repo_root / "pom.xml").is_file():
        out["build"] = out["build"] or "mvn package"
        out["test"] = out["test"] or "mvn test"
    if (repo_root / "build.gradle").is_file() or \
       (repo_root / "build.gradle.kts").is_file():
        if (repo_root / "gradlew").is_file():
            out["build"] = out["build"] or "./gradlew build"
            out["test"] = out["test"] or "./gradlew test"
        else:
            out["build"] = out["build"] or "gradle build"
            out["test"] = out["test"] or "gradle test"
    if (repo_root / ".eslintrc").is_file() or \
       (repo_root / ".eslintrc.json").is_file() or \
       (repo_root / ".eslintrc.js").is_file() or \
       (repo_root / ".eslintrc.cjs").is_file() or \
       (repo_root / ".eslintrc.yaml").is_file() or \
       (repo_root / ".eslintrc.yml").is_file():
        out["lint"] = out["lint"] or "npx eslint ."
    if (repo_root / ".ruff.toml").is_file() or \
       (repo_root / "ruff.toml").is_file():
        out["lint"] = out["lint"] or "ruff check ."
    if (repo_root / ".rubocop.yml").is_file() or \
       (repo_root / ".rubocop.yaml").is_file():
        out["lint"] = out["lint"] or "bundle exec rubocop"
    if (repo_root / "detekt.yml").is_file() or \
       (repo_root / "detekt.yaml").is_file():
        out["lint"] = out["lint"] or "detekt"
    if next(repo_root.glob("*.sln"), None) is not None:
        out["build"] = out["build"] or "dotnet build"
        out["test"] = out["test"] or "dotnet test"
    elif next(repo_root.glob("*.csproj"), None) is not None:
        out["build"] = out["build"] or "dotnet build"
        out["test"] = out["test"] or "dotnet test"
    if (repo_root / "phpunit.xml").is_file() or \
       (repo_root / "phpunit.xml.dist").is_file():
        out["test"] = out["test"] or "vendor/bin/phpunit"
    if (repo_root / "phpstan.neon").is_file() or \
       (repo_root / "phpstan.neon.dist").is_file():
        out["lint"] = out["lint"] or "vendor/bin/phpstan analyse"
    if _is_rails_repo(repo_root):
        out["test"] = out["test"] or "bin/rails test"
    return out


def _load_blt_config(repo_root: Path) -> dict[str, str] | None:
    """Read BLT-related keys from .leerie/config.toml.

    Returns None when the file does not exist. Otherwise returns a dict
    containing only the keys that are present in the file (build, lint,
    test, setup_packages). Missing keys are not defaulted — the caller
    (resolve_blt) decides what to do with absent axes.

    Uses _read_toml_key for each key so the flat-parser semantics
    (first-match-wins, stripped quotes, skip comments/blanks) are shared."""
    cfg = repo_root / ".leerie" / "config.toml"
    if not cfg.exists():
        return None
    out: dict[str, str] = {}
    for key in ("build", "lint", "test", "setup_packages"):
        val = _read_toml_key(cfg, key)
        if val is not None:
            out[key] = val
    return out


def resolve_blt(repo_root: Path) -> dict[str, str]:
    """Return the effective build/lint/test commands for the repo.

    Resolution order (per axis):
    1. Declared value in .leerie/config.toml — wins unconditionally,
       including an empty string (which means "not applicable").
    2. Falls through to _infer_build_lint_test() for any axis not declared.

    Logs which axes came from config vs inference so the conformer's worker
    log makes the source visible."""
    declared = _load_blt_config(repo_root)
    inferred = _infer_build_lint_test(repo_root)

    if declared is None:
        log("BLT: no .leerie/config.toml — using inference for all axes")
        return inferred

    blt_axes = ("build", "lint", "test")
    config_axes = [ax for ax in blt_axes if ax in declared]
    infer_axes = [ax for ax in blt_axes if ax not in declared]
    if config_axes:
        log(f"BLT: config axes={config_axes} infer axes={infer_axes}")

    out: dict[str, str] = {}
    for ax in blt_axes:
        if ax in declared:
            out[ax] = declared[ax]
        else:
            out[ax] = inferred[ax]
    return out


def validate_conformance_result(result: dict, worktree: str) -> str | None:
    """Cross-field invariants for the conformer's structured output.
    Returns None when valid, else a one-line error string.

    The JSON schema already enforces the *shape* (required fields, their
    types). This function enforces the cross-field rules the schema can't:
    residuals require a non-empty `rules_files_read`, every fixed violation
    cites a non-empty `rule`, every docs/tests update cites a path that
    actually exists in the worktree.

    Per DESIGN §12 this is the code-enforced honesty check; the worker's
    own judgment is not second-guessed beyond these structural minimums."""
    if not isinstance(result, dict):
        return "conformer result is not an object"

    files_read = result.get("rules_files_read") or []
    residuals = result.get("rule_violations_residual") or []
    if residuals and not files_read:
        return ("rule_violations_residual non-empty but rules_files_read "
                "is empty — a violation cannot exist without a rule")

    fixed = result.get("rule_violations_fixed") or []
    for i, item in enumerate(fixed):
        if not (item.get("rule") or "").strip():
            return f"rule_violations_fixed[{i}] has empty 'rule'"

    # Resolve the worktree once for path-traversal checking. Paths must
    # both exist AND resolve inside the worktree — a `path` like
    # "../../etc/passwd" or "/etc/passwd" is an honesty failure (the
    # conformer claims to have updated a doc inside the subtask, but the
    # path it cites escapes the worktree).
    try:
        wt_resolved = Path(worktree).resolve()
    except OSError:
        return f"worktree path {worktree!r} could not be resolved"
    for kind in ("docs_updates", "tests_updates"):
        for i, item in enumerate(result.get(kind) or []):
            rel = (item.get("path") or "").strip()
            if not rel:
                return f"{kind}[{i}] has empty 'path'"
            try:
                resolved = (wt_resolved / rel).resolve()
            except OSError:
                return (f"{kind}[{i}] path {rel!r} could not be resolved")
            # Path.is_relative_to was added in 3.9; we target 3.10+ (see
            # CLAUDE.md tech-stack note) so this is safe.
            if not resolved.is_relative_to(wt_resolved):
                return (f"{kind}[{i}] path {rel!r} escapes the worktree "
                        f"(resolves to {resolved}); paths must stay inside "
                        f"the subtask's worktree")
            if not resolved.exists():
                return (f"{kind}[{i}] cites path {rel!r} which does not "
                        f"exist in the worktree")
    return None


async def _branch_head_sha(worktree: str) -> str:
    """HEAD sha in the worktree, or empty string on failure. Used as the
    rollback target before the conformer adds commits."""
    r = await run_proc(["git", "rev-parse", "HEAD"], cwd=worktree)
    if r.returncode != 0:
        return ""
    return r.stdout.strip()


async def _protected_paths_since(worktree: str,
                                 before_sha: str) -> list[str]:
    """Return the list of protected paths the diff `before_sha..HEAD`
    touched in `worktree`, or [] when clean / empty / git failure.

    The per-subtask conformer reuses `check_diff_scope` which is
    hardcoded to diff against the run branch — that comparison is
    correct for a subtask's own worktree but produces an empty diff
    on the staging worktree (which sits at the run-branch HEAD).
    This helper does the analogous protected-path check for the
    final-tree conformance pass, scoped to "what did this round
    add to staging."""
    if not before_sha:
        return []
    r = await run_proc(
        ["git", "diff", "--name-only", f"{before_sha}..HEAD"],
        cwd=worktree,
    )
    if r.returncode != 0:
        return []
    touched = [f for f in r.stdout.strip().splitlines() if f]
    return [f for f in touched if is_protected_path(f)]


async def _blob_sha(worktree: str, ref: str, path: str) -> str | None:
    """Blob SHA of `path` at `ref` in `worktree`, or None if the path is
    absent at that ref.

    Uses `git rev-parse --verify -q <ref>:<path>` deliberately: plain
    `git rev-parse <ref>:<path>` on a missing path prints the literal
    `<ref>:<path>` string to stdout *and* exits non-zero, so naive
    callers capture a bogus value; `--verify -q` exits cleanly with empty
    stdout instead."""
    r = await run_proc(
        ["git", "rev-parse", "--verify", "-q", f"{ref}:{path}"],
        cwd=worktree,
    )
    out = r.stdout.strip()
    return out if (r.returncode == 0 and out) else None


async def clobbered_owned_files(worktree: str, base_ref: str,
                                impl_head: str) -> list[str]:
    """Return implementer-owned files the conformer reverted-to-base or
    deleted, i.e. the data-loss signature (DESIGN §9 *No clobbering the
    implementer's work*). Empty list when clean or on git failure.

    - Owned set = files changed in `base_ref..impl_head` (the reliable
      owned set; `files_likely_touched` is advisory and NOT used because
      the implementer may commit outside it).
    - `impl_head` MUST be the implementer's committed HEAD snapshotted
      *before the first conformer round* — a per-round HEAD already folds
      in prior conformer commits, and a round-0 clobber would then read as
      "implementer never changed it" and be missed.
    - A file is a clobber iff the implementer changed it (blob@impl !=
      blob@base) AND at HEAD it is absent (deleted) OR its blob equals
      base (reverted). A legitimate conformer edit yields a distinct third
      blob and is not flagged."""
    if not base_ref or not impl_head:
        return []
    r = await run_proc(
        ["git", "diff", "--name-only", f"{base_ref}..{impl_head}"],
        cwd=worktree,
    )
    if r.returncode != 0:
        return []
    owned = [f for f in r.stdout.splitlines() if f]
    clobbered: list[str] = []
    for f in owned:
        b_base = await _blob_sha(worktree, base_ref, f)
        b_impl = await _blob_sha(worktree, impl_head, f)
        b_head = await _blob_sha(worktree, "HEAD", f)
        if b_impl == b_base:
            continue  # implementer didn't actually change it — not owned
        if b_head is None:
            clobbered.append(f"{f} (deleted)")
        elif b_head == b_base:
            clobbered.append(f"{f} (reverted-to-base)")
    return clobbered


async def rollback_conformer_commits(worktree: str, before_sha: str) -> None:
    """Hard-reset the subtask branch back to `before_sha`. Used when the
    conformer wrote to a protected path — the implementer's commits
    are preserved, the conformer's are dropped. Safe to call when no
    new commits were made: it's a no-op reset.

    Note: `git reset --hard` also discards uncommitted changes. Callers
    that want to warn about discarded scribbles should call
    `_uncommitted_paths` first."""
    if not before_sha:
        return
    await run_proc(["git", "reset", "--hard", before_sha], cwd=worktree)


async def _uncommitted_paths(worktree: str) -> list[str]:
    """Return tracked-file paths with uncommitted changes in the worktree,
    or [] if the check fails. Untracked files are excluded — the rollback
    only touches tracked state. Used as a pre-rollback observability
    helper: when the conformer leaves uncommitted scribbles alongside a
    commit that triggers rollback, those scribbles get silently discarded
    by `git reset --hard`. This lets the caller surface what was lost."""
    try:
        r = await run_proc(["git", "status", "--porcelain"], cwd=worktree)
    except OSError:
        return []
    if r.returncode != 0:
        return []
    return [line for line in r.stdout.splitlines()
            if line and not line.startswith("??")]


async def _unprefixed_conformer_commits(worktree: str, before_sha: str,
                                        prefix: str = "conformer:"
                                        ) -> list[str]:
    """Return subject lines of commits between before_sha..HEAD whose
    subjects do not start with `prefix`. Empty list when there are no new
    commits, when every new commit is correctly prefixed, or when the git
    invocation fails (the caller treats a missing answer as no warning).

    This is the code-side honesty check for the prompt-level rule
    "conformer commits must start with `conformer:`" (DESIGN §9
    Post-work conformance + §12 prompts-are-advisory). The check is
    *observability*, not enforcement — unprefixed commits surface as
    `conformance_warnings`, never trigger rollback."""
    if not before_sha:
        return []
    r = await run_proc(
        ["git", "log", "--format=%s", f"{before_sha}..HEAD"],
        cwd=worktree,
    )
    if r.returncode != 0:
        return []
    return [line for line in r.stdout.splitlines()
            if line and not line.startswith(prefix)]


async def run_conformer(sid: str, leerie_dir: Path, worktree: str,
                        caps: dict, st: State, models: dict[str, str],
                        efforts: dict[str, str | None],
                        rules_files: list[Path],
                        blt_commands: dict[str, str],
                        diff_base: str,
                        extra_feedback: str | None = None) -> dict | None:
    """Spawn one conformer for one subtask in its existing worktree.
    Returns the worker's structured output, or None on WorkerError (which
    is recorded as a warning by the caller — DESIGN §9: the phase is
    advisory)."""
    sys_prompt = load_prompt("conformer")
    repo_root = st.repo_root
    rules_paths_str = _format_rules_paths(rules_files, repo_root)
    up = [f"Run the post-work conformance phase for subtask `{sid}`.",
          f"LEERIE_DIR is {leerie_dir} (absolute). Your subtask spec "
          f"is at {leerie_dir}/subtasks/{sid}.json and the implementer's "
          f"success-criteria notes are at {leerie_dir}/criteria/{sid}.md "
          "— both read-only inputs.",
          "Your current working directory IS the subtask's worktree. Make "
          "and commit any fixes here. Every commit subject must start "
          "with `conformer:`.",
          f"RULES_FILES: {rules_paths_str}",
          f"BUILD_CMD: {blt_commands.get('build') or '(none)'}",
          f"LINT_CMD: {blt_commands.get('lint') or '(none)'}",
          f"TEST_CMD: {blt_commands.get('test') or '(none)'}",
          f"DIFF_BASE: {diff_base} (compare with `git diff {diff_base}..HEAD`)"]
    baseline_section = _format_baseline_section(
        (st.data.get("conformance") or {}).get("_baseline"))
    if baseline_section is not None:
        up.append(baseline_section)
    recipe_section = _format_provision_recipe_section(
        (st.data.get("provision") or {}).get("recipe") or [],
        audience="conformer")
    if recipe_section is not None:
        up.append(recipe_section)
    if extra_feedback is not None:
        up.append(extra_feedback)

    # bump_workers is inside the try block on purpose: it raises
    # WorkerError when max_total_workers is exhausted, and the conformance
    # phase must NEVER escalate that into a failed/blocked subtask
    # (DESIGN §9 Post-work conformance — the phase is advisory only). The
    # implementer at run_implementer() places bump_workers outside its try
    # because for the implementer the budget-exhausted error IS meant to
    # abort the run.
    try:
        st.bump_workers(caps)
        return await claude_p(user_prompt="\n".join(up),
                              system_prompt=sys_prompt,
                              schema_key="conformer", cwd=worktree,
                              allowed_tools=ACT_TOOLS, max_turns=60,
                              autonomous=True, caps=caps, st=st,
                              model=models["conformer"],
                              effort=efforts["conformer"],
                              sid=f"{sid}-conformer")
    except WorkerError as e:
        log(f"  {sid}: conformer crashed: {e}")
        return None
    except subprocess.TimeoutExpired:
        # Same rationale as run_implementer's TimeoutExpired catch —
        # don't let the worker-timeout traceback escape. The conformer
        # phase is advisory; a timed-out conformer becomes one more
        # warning, not a run-killer.
        timeout = caps.get("worker_timeout_sec", "?")
        log(f"  {sid}: conformer timed out after {timeout}s")
        return None


def _summarize_residuals(conf_res: dict) -> list[str]:
    """One advisory string per residual / failing build-lint-test axis.
    Empty list when the conformer reports a fully clean pass."""
    out: list[str] = []
    for item in conf_res.get("rule_violations_residual") or []:
        rule = (item.get("rule") or "").strip()
        why = (item.get("why_not_fixed") or "").strip()
        out.append(f"rule-residual: {rule!r} not fixed — {why}")
    for axis in ("build", "lint", "tests"):
        a = conf_res.get(axis) or {}
        if a.get("ran") and not a.get("passed"):
            summary = (a.get("summary") or "").strip() or "(no summary)"
            out.append(f"{axis}-failed: {a.get('command', '')!r}: {summary}")
    return out


def _confidence_axes_clear(
    conf: dict, axes: list[str], threshold: float = 9.0,
) -> bool:
    """True when every named axis in *conf* is a number >= *threshold*.

    Used by ``_run_checked_loop`` and the implementer's in-settle_subtask
    confidence check.  Pure — no I/O, no state mutation."""
    for ax in axes:
        val = conf.get(ax)
        if not isinstance(val, (int, float)) or val < threshold:
            return False
    return True


def _format_check_feedback(
    issues: list[str], rnd: int, max_rounds: int,
) -> str:
    """Format a structured feedback block for a re-invocation.

    The block is deterministic text the orchestrator computed without an
    LLM — file-existence checks, graph-cycle detection, lockfile
    matching, etc.  Injected into the worker's user prompt on
    re-invocation so the CRITIC pattern applies (external tool-verified
    signal, not prior-pass output)."""
    header = (
        f"ORCHESTRATOR MECHANICAL CHECK (round {rnd + 1} of {max_rounds}):\n"
        f"{len(issues)} issue(s) found by deterministic checks on your "
        "output:\n"
    )
    body = "\n".join(f"- {issue}" for issue in issues)
    footer = (
        "\n\nAddress these issues. The orchestrator provides only these "
        "mechanically-derived signals — not your previous output."
    )
    return header + body + footer


async def _run_checked_loop(
    *,
    invoke: Callable[..., Awaitable[dict]],
    check: Callable[[dict], list[str]],
    name: str,
    max_rounds: int,
    make_feedback_prompt: Callable[[str], Awaitable[dict]] | None = None,
) -> tuple[dict | None, list[str]]:
    """Generic mechanical-feedback retry loop (CRITIC pattern).

    Calls *invoke* up to *max_rounds* times.  After each call, runs
    *check* (a pure-Python function) on the result.  If the check
    returns an empty list the loop breaks (output is clean).  Otherwise
    the issue list is formatted as external feedback via
    ``_format_check_feedback`` and the next round's *invoke* is expected
    to receive it (the caller is responsible for closing over a mutable
    prompt variable that ``make_feedback_prompt`` updates, or ignoring
    feedback for workers that don't need prompt mutation).

    Returns ``(last_result, all_warnings)``.  The caller decides
    escalation (die, block, warn).

    *make_feedback_prompt* is optional.  When provided, it receives the
    formatted feedback string and should update whatever closed-over
    state the next ``invoke()`` call will read (typically a user-prompt
    variable).  When ``None``, feedback is logged but not injected (the
    loop still retries — useful when the re-invocation alone, as a fresh
    ``claude -p`` session, is the value)."""
    warnings: list[str] = []
    last_res: dict | None = None

    for rnd in range(max_rounds):
        try:
            last_res = await invoke()
        except Exception as exc:
            warnings.append(f"{name} round {rnd}: worker crashed: {exc}")
            break

        if last_res is None:
            warnings.append(f"{name} round {rnd}: worker returned None")
            break

        issues = check(last_res)
        if not issues:
            break

        for issue in issues:
            warnings.append(f"{name} round {rnd}: {issue}")

        if rnd < max_rounds - 1 and make_feedback_prompt is not None:
            feedback = _format_check_feedback(issues, rnd, max_rounds)
            await make_feedback_prompt(feedback)

    return last_res, warnings


def _conformance_clean(conf_res: dict) -> bool:
    """True when the conformer reports no residuals and every axis is
    either passed or not applicable. Used to short-circuit the
    orchestrator-level conformer loop."""
    if conf_res.get("rule_violations_residual"):
        return False
    for axis in ("build", "lint", "tests"):
        a = conf_res.get(axis) or {}
        if a.get("ran") and not a.get("passed"):
            return False
    return True


# Per-axis regexes matching the shapes of build/lint/test commands the
# typical Node/JS and Ruby/Rails conformer worker reaches for. Used by
# `_count_bash_axis_invocations` and `_count_orphaned_bg_axis` to surface
# advisory warnings when a worker overrun an axis in one round, or fired
# a fresh BLT command in response to an auto-backgrounded prior one
# (the retry-instead-of-recover antipattern; see conformer.md §4).
_BLT_AXIS_RES: dict[str, re.Pattern[str]] = {
    "test":  re.compile(r"\b(?:pnpm|npm|yarn|bun|npx)\s+(?:run\s+)?(?:test|vitest)\b"
                        r"|\bvitest\s+run\b"
                        r"|\bbin/rails\s+test\b"
                        r"|\bmvn\s+test\b"
                        r"|\b\.?/?gradlew\s+test\b|\bgradle\s+test\b"
                        r"|\bdotnet\s+test\b"
                        r"|\bvendor/bin/phpunit\b"),
    "build": re.compile(r"\b(?:pnpm|npm|yarn|bun)\s+(?:run\s+)?build\b"
                        r"|\btsc(?:\s|$)|\bnext\s+build\b"
                        r"|\bmvn\s+(?:package|compile)\b"
                        r"|\b\.?/?gradlew\s+build\b|\bgradle\s+build\b"
                        r"|\bdotnet\s+build\b"),
    "lint":  re.compile(r"\b(?:pnpm|npm|yarn|bun)\s+(?:run\s+)?lint\b"
                        r"|\bbiome\s+check\b|\beslint(?:\s|$)"
                        r"|\brubocop\b"
                        r"|\bvendor/bin/phpstan\b|\bvendor/bin/phpcs\b"),
}
# Exact wording the Bash tool returns when it auto-backgrounds a
# command that exceeds the worker-supplied `timeout`. The bash_id
# (a `b`-prefixed token) appears after the colon. Coupled with the
# Bash tool's behavior; if Claude Code changes the wording, the
# orphan-detection helper silently underreports, so this string
# lives in one place and is covered by a regression test.
_BG_RESULT_PREFIX = "Command running in background with ID:"
_BG_ID_RE = re.compile(r"Command running in background with ID:\s*(\w+)")


def _iter_log_tool_use(
        log_path: Path) -> Iterator[tuple[str, dict, str]]:
    """Yield each Bash/BashOutput/KillBash/Read `tool_use` block from a
    per-worker JSONL log, paired with its `tool_result` content when
    one is present. Yields tuples of (kind, input_dict, result_text)
    where `result_text` is "" if no result was paired.

    Tolerates malformed lines: any line that isn't valid JSON, or any
    JSON event that doesn't carry the expected message/content shape,
    is skipped silently. The JSONL log format is produced by the Claude
    Code SDK, not by leerie itself; a parsing failure on a single line
    must not break the advisory warning emission for the whole round."""
    if not log_path.is_file():
        return
    lines = log_path.read_text().splitlines()
    # Two passes keep the API simple — log files are at most a few MB.
    # First collects tool_use blocks (id, kind, input) in order; second
    # keys tool_result texts by tool_use_id and yields the joined pairs.
    uses: list[tuple[str, str, dict]] = []  # (tool_use_id, name, input)
    results: dict[str, str] = {}
    for line in lines:
        if not line.startswith("{"):
            continue
        try:
            body = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = body.get("message") or {}
        # A non-dict `message` value (e.g. a top-level event with
        # `{"message": "string"}`) would crash `msg.get("content")` —
        # the docstring promises malformed-line tolerance, so skip.
        if not isinstance(msg, dict):
            continue
        content = msg.get("content") or []
        if not isinstance(content, list):
            continue
        for blk in content:
            if not isinstance(blk, dict):
                continue
            t = blk.get("type")
            if t == "tool_use":
                name = blk.get("name", "")
                if name in ("Bash", "BashOutput", "KillBash", "Read"):
                    uses.append((blk.get("id", ""), name,
                                 blk.get("input") or {}))
            elif t == "tool_result":
                tid = blk.get("tool_use_id", "")
                c = blk.get("content", "")
                if isinstance(c, list):
                    c = " ".join(
                        x.get("text", "") if isinstance(x, dict) else str(x)
                        for x in c)
                results[tid] = c or ""
    for tid, kind, inp in uses:
        yield kind, inp, results.get(tid, "")


def _count_bash_axis_invocations(log_path: Path,
                                 axis_re: re.Pattern[str]) -> int:
    """Count distinct Bash `tool_use` invocations in `log_path` whose
    command matches `axis_re`. Returns 0 when the log is missing or
    contains no matching invocations. Tolerates malformed log lines."""
    n = 0
    for kind, inp, _result in _iter_log_tool_use(log_path):
        if kind != "Bash":
            continue
        if axis_re.search(inp.get("command", "")):
            n += 1
    return n


def _count_orphaned_bg_axis(log_path: Path,
                            axis_re: re.Pattern[str]) -> list[str]:
    """Return the bash_ids of BLT commands matching `axis_re` that
    auto-backgrounded (tool_result starts with `_BG_RESULT_PREFIX`) and
    were followed in the same log by **another** Bash invocation matching
    the same axis_re — i.e., the worker fired a fresh test/build command
    in response to a backgrounded one instead of recovering the result
    via `BashOutput shell_id=<id>` or `Read file_path=<temp>`.

    Bash invocations that auto-backgrounded and were followed by a
    BashOutput-poll on the same shell_id, a KillBash on it, or any Read
    of the temp output file path are *not* orphans — the model recovered
    cleanly. Foreground commands (no auto-background result) are never
    orphans."""
    # Build a flat ordered list of all relevant tool_use events so we
    # can ask "what was the next BLT-axis Bash after this backgrounded
    # one?" with a simple index walk.
    events: list[tuple[str, dict, str]] = list(_iter_log_tool_use(log_path))
    orphans: list[str] = []
    for i, (kind, inp, result) in enumerate(events):
        if kind != "Bash":
            continue
        if not axis_re.search(inp.get("command", "")):
            continue
        if not result.startswith(_BG_RESULT_PREFIX):
            continue
        m = _BG_ID_RE.search(result)
        bg_id = m.group(1) if m else ""
        # Walk forward to the next BLT-axis Bash in this log. If the
        # very next BLT-axis Bash matches axis_re, it's a retry. If we
        # find a BashOutput/KillBash with this bg_id, or a Read of the
        # temp file (the tool reports the path right after the bg id;
        # we use a permissive substring check on the result text), it's
        # a clean recovery and we stop searching.
        for j in range(i + 1, len(events)):
            kind_j, inp_j, _ = events[j]
            if kind_j == "BashOutput" and inp_j.get("shell_id") == bg_id:
                break  # polled — clean recovery
            if kind_j == "KillBash" and inp_j.get("shell_id") == bg_id:
                break  # killed — clean termination
            if kind_j == "Read":
                p = inp_j.get("file_path", "")
                if bg_id and bg_id in p:
                    break  # read the temp output file — clean recovery
                # A different Read: keep scanning; recovery might come
                # later or the model might still retry.
                continue
            if kind_j == "Bash":
                # If the next Bash matches the same axis, it's a retry.
                # If it's an unrelated command (e.g. `cat /tmp/...`,
                # `git log`), keep scanning — recovery might still come.
                cmd_j = inp_j.get("command", "")
                if axis_re.search(cmd_j):
                    orphans.append(bg_id)
                    break
                if bg_id and bg_id in cmd_j:
                    break  # shell-inspected the bg job output — recovery
                continue
        # If we ran out of events with no recovery and no retry, the
        # worker simply stopped doing things related to this axis —
        # not an orphan-by-retry, so we do not append.
    return orphans


def _emit_bash_axis_warnings(log_path: Path, round_label: str,
                             warnings: list[str]) -> None:
    """Helper called once per conformer round: append advisory warnings
    to `warnings` for axes that were invoked more than once in the
    round, or whose auto-backgrounded invocations were followed by a
    retry instead of a temp-file read or `BashOutput` poll (see
    conformer.md §4 for the discipline)."""
    if not log_path.is_file():
        return
    for axis, axis_re in _BLT_AXIS_RES.items():
        n = _count_bash_axis_invocations(log_path, axis_re)
        if n > 1:
            warnings.append(
                f"{round_label}: ran {axis.upper()}_CMD {n} times in one "
                f"round (see {log_path}) — `run each axis exactly once "
                "per round` per conformer.md §4; surfaced as advisory.")
        for bg_id in _count_orphaned_bg_axis(log_path, axis_re):
            warnings.append(
                f"{round_label}: {axis.upper()}_CMD auto-backgrounded "
                f"(bash_id={bg_id}) and was followed by another "
                f"{axis.upper()}_CMD invocation — that is the "
                "retry-instead-of-recover pattern. Set `timeout: 600000` "
                "on the original invocation to prevent the background "
                "trap (conformer.md §4); if it still backgrounds, "
                "recover by reading the temp output file the Bash tool "
                "reports (`Read file_path=<path>`).")


async def _run_conformance_phase(sid: str, leerie_dir: Path,
                                 worktree: str, subtask: dict, caps: dict,
                                 st: State, models: dict[str, str],
                                 efforts: dict[str, str | None]
                                 ) -> tuple[dict | None, list[str], str | None]:
    """Drive the orchestrator-level conformer loop for one subtask.
    Returns `(last_conformer_result, warnings, blocked_reason)`. Never
    raises a workflow error: all failure modes — malformed output,
    WorkerError, gate violations on conformer commits, exhausted
    rounds — surface as entries in `warnings`. When
    `caps["strict_conformer"]` is True and residuals remain after the
    loop, `blocked_reason` is a non-None summary string; the caller
    uses it to block the subtask instead of completing with advisory
    warnings."""
    warnings: list[str] = []
    repo_root = st.repo_root
    rules_files = discover_rules_files(repo_root)
    blt = resolve_blt(repo_root)
    run_branch = compute_run_branch(st.run_id)
    last_res: dict | None = None
    blt_feedback: str | None = None
    # Snapshot the implementer's committed HEAD ONCE, before any conformer
    # round runs, for the clobber-survival check (DESIGN §9 *No clobbering
    # the implementer's work*). Must be captured here, not per-round: a
    # per-round HEAD folds in prior conformer commits, so a round-0
    # revert-to-base would read as "implementer never changed it" and slip
    # past the check.
    impl_head_sha = await _branch_head_sha(worktree)
    # Set when the conformer reverted/deleted an implementer-owned file.
    # Under --strict-conformer this blocks the subtask even if the
    # conformer's own BLT/residuals came back clean (a clobber is the
    # severest residual — it tried to destroy the implementer's work).
    clobbered_files: list[str] = []

    for c_round in range(caps["conformance_rounds"]):
        before_sha = await _branch_head_sha(worktree)
        last_res = await run_conformer(
            sid, leerie_dir, worktree, caps, st, models, efforts,
            rules_files=rules_files, blt_commands=blt,
            diff_base=run_branch, extra_feedback=blt_feedback)

        if last_res is None:
            warnings.append(f"conformer round {c_round}: worker crashed; "
                            "phase surfaced as advisory")
            break

        err = validate_conformance_result(last_res, worktree)
        if err:
            warnings.append(f"conformer round {c_round}: malformed result: {err}")
            break

        # Re-apply the implementer gates against any new conformer commits.
        # Empty diff (worker added no commits) is fine and common: a
        # well-formed result with no fixes is a legitimate "nothing to do."
        # check_diff_scope returns a string ONLY for a protected-path
        # violation — .leerie/, .git/, or top-level .claude/ files;
        # .claude/{agents,commands,skills}/ are exempt per
        # is_protected_path(). The scope-volume warning is logged
        # side-channel and does not surface here.
        scope_err = await check_diff_scope(sid, worktree, subtask, st)
        if scope_err:
            discarded = await _uncommitted_paths(worktree)
            if discarded:
                warnings.append(
                    f"conformer round {c_round}: discarding "
                    f"{len(discarded)} uncommitted file(s) during rollback: "
                    f"{[line[3:] for line in discarded]}")
            await rollback_conformer_commits(worktree, before_sha)
            warnings.append(f"conformer round {c_round}: protected-path "
                            f"violation reverted ({scope_err})")
            break

        # Dirty-worktree check: the conformer should commit, not leave
        # uncommitted changes that integration would lose.
        dirty = await _uncommitted_paths(worktree)
        if dirty:
            warnings.append(f"conformer round {c_round}: left "
                            f"{len(dirty)} uncommitted change(s) — not "
                            "rolled back, but surfaced as advisory")

        # Clobber-survival check (DESIGN §9 *No clobbering the
        # implementer's work*): did the conformer revert-to-base or delete
        # a file the implementer committed? Warn always; under strict mode
        # roll the conformer's commits back to the implementer HEAD. Not
        # auto-rolled-back in advisory mode — a legitimate revert-to-base
        # is git-indistinguishable from a clobber and the phase is advisory.
        clobbered = await clobbered_owned_files(
            worktree, run_branch, impl_head_sha)
        if clobbered:
            clobbered_files = clobbered
            warnings.append(
                f"conformer round {c_round}: reverted/deleted "
                f"{len(clobbered)} implementer-owned file(s): {clobbered}")
            if caps.get("strict_conformer"):
                discarded = await _uncommitted_paths(worktree)
                if discarded:
                    warnings.append(
                        f"conformer round {c_round}: discarding "
                        f"{len(discarded)} uncommitted file(s) during "
                        f"clobber rollback: {[line[3:] for line in discarded]}")
                await rollback_conformer_commits(worktree, impl_head_sha)
                warnings.append(
                    f"conformer round {c_round}: strict mode — rolled "
                    "conformer commits back to implementer HEAD to restore "
                    "clobbered work")
                break

        # Commit-prefix observability: surface (but don't roll back) any
        # conformer commits whose subject doesn't start with `conformer:`.
        # The prefix lets reviewers identify conformer commits in git log;
        # missing prefixes are a discipline lapse, not a correctness issue.
        unprefixed = await _unprefixed_conformer_commits(worktree, before_sha)
        for subject in unprefixed:
            warnings.append(f"conformer round {c_round}: commit subject "
                            f"missing `conformer:` prefix: {subject!r}")

        # BLT-axis observability: surface advisory warnings when the
        # worker invoked an axis more than once in one round, or fired a
        # fresh BLT command in response to an auto-backgrounded prior
        # one. The conformer's sid is f"{sid}-conformer" — see
        # `run_conformer` where claude_p is called.
        _emit_bash_axis_warnings(
            leerie_dir / "logs" / f"{sid}-conformer.log",
            f"conformer round {c_round}", warnings)

        bg_retry_warnings = [
            w for w in warnings
            if w.startswith(f"conformer round {c_round}:")
            and "auto-backgrounded" in w]
        blt_feedback = (
            _format_check_feedback(bg_retry_warnings, c_round,
                                   caps["conformance_rounds"])
            if bg_retry_warnings else None)

        if _conformance_clean(last_res):
            break

    if last_res is not None:
        warnings.extend(_summarize_residuals(last_res))

    blocked_reason: str | None = None
    if caps.get("strict_conformer"):
        # A clobber blocks even when the conformer's own BLT/residuals are
        # clean: it was rolled back above, but strict mode must surface it
        # for the operator (fix + --resume), not silently complete.
        if clobbered_files:
            blocked_reason = (
                "strict-conformer: conformer reverted/deleted "
                f"implementer-owned file(s): {clobbered_files}")
        elif last_res is not None and not _conformance_clean(last_res):
            residuals = _summarize_residuals(last_res)
            blocked_reason = (
                "strict-conformer: " + "; ".join(residuals[:3])
                if residuals else "strict-conformer: conformance not clean")

    return last_res, warnings, blocked_reason


def _runner_missing(summary: str) -> bool:
    """True if a failed baseline command failed because its runner is not
    on PATH (rather than a real test/build/lint failure). The canonical
    signature is a shell `command not found`; also treat a bare
    `No such file or directory` on the command as runner-missing. Used to
    distinguish "could not measure" from "base is RED"."""
    s = (summary or "").lower()
    return "command not found" in s or "no such file or directory" in s


def _format_baseline_section(baseline: dict | None) -> str | None:
    """Render the base-tree health baseline as a conformer prompt section,
    or None when there is no baseline (skipped, or not yet captured).

    The section tells the conformer which build/lint/test axes were
    already RED on the unmodified base tree so it scopes its own
    build/lint/test judgment to the *delta* — failures the change
    introduced — instead of re-deriving "these are pre-existing" from
    scratch (DESIGN §9 *Base-tree health baseline*). Advisory: the
    mechanical part (which axes were red on base) is code-computed here;
    the judgment (is a given post-change failure the same one) stays with
    the worker.

    An axis whose baseline command could not be measured (`measured:
    False` — its runner was missing) is surfaced honestly as "could not
    measure," NOT folded into GREEN or RED: a false GREEN would tell the
    conformer to treat every failure as new; a false RED gives it a
    delta it can't use. Every axis dict carries `measured` (set by
    `capture_conformance_baseline`), so a missing/false value means the
    axis is not a measured pass."""
    if not baseline:
        return None
    axes = baseline.get("axes") or {}

    def _measured(a: str) -> bool:
        ax = axes.get(a) or {}
        return bool(ax.get("ran") and ax.get("measured"))

    red = [a for a in ("build", "lint", "tests")
           if _measured(a) and not (axes.get(a) or {}).get("passed")]
    green = [a for a in ("build", "lint", "tests")
             if _measured(a) and (axes.get(a) or {}).get("passed")]
    unmeasured = [a for a in ("build", "lint", "tests")
                  if (axes.get(a) or {}).get("ran")
                  and not (axes.get(a) or {}).get("measured")]

    lines = ["", "BASELINE:"]
    # Only claim GREEN when an axis was *actually* measured and passed —
    # never when every axis was unmeasurable (e.g. the runner was absent),
    # which would be a false all-clear (the very framing this baseline
    # exists to avoid). The `unmeasured` block below carries the honest
    # "could not measure — attribute failures yourself" guidance in that case.
    if not red and green:
        lines.append(
            "  The base tree (before any subtask's change) was GREEN on "
            "every axis that could be measured — those passed (or were "
            "not applicable). So any build/lint/test failure you observe "
            "on a measured axis was introduced by this run's diff: report "
            "it as a residual and try to fix it.")
    elif red:
        lines.append(
            "  The base tree (before any subtask's change) was already RED "
            "on the following axis/axes: " + ", ".join(red) + ". These "
            "failures PRE-EXIST on the base and are NOT this run's "
            "responsibility — do NOT report them as residuals and do NOT "
            "try to fix them. Scope your build/lint/test judgment to the "
            "DELTA: only report a build/lint/test failure as a residual if "
            "it is NEW relative to this base state (i.e. introduced by the "
            "diff). Compare with `git diff DIFF_BASE..HEAD` to attribute "
            "failures.")
        for a in red:
            summ = ((axes.get(a) or {}).get("summary") or "").strip()
            if summ:
                lines.append(f"  - {a} (pre-existing): {summ[:200]}")
    if unmeasured:
        lines.append(
            "  The following axis/axes COULD NOT be measured on the base "
            "tree (the runner was not available): " + ", ".join(unmeasured)
            + ". There is no baseline for them — attribute any failure on "
            "these axes yourself, honestly, by whether the failing files "
            "are in `git diff DIFF_BASE..HEAD`. Do NOT check out or reset "
            "the tree to another ref to re-derive the base — that destroys "
            "the implementer's committed work.")
    return "\n".join(lines)


async def capture_conformance_baseline(
        leerie_dir: Path, st: State, caps: dict) -> None:
    """Record base-tree build/lint/test health once per run (DESIGN §9
    *Base-tree health baseline*).

    Runs at the start of `phase_execute`, after `setup-run.sh` has created
    the staging worktree off the base HEAD but before any wave mutates it,
    so the tree is an unmodified snapshot of the base. Installs the
    provision recipe into staging (deps live only in worktrees — §6½ — so
    the suite cannot run without this), then runs each resolved
    build/lint/test command directly via `run_streaming` and records the
    **exit code** per axis (non-zero ⇒ RED). Exit-code-based on purpose:
    100% reliable, no per-framework output parsing.

    Deterministic (no LLM). Advisory — never raises; any failure to
    capture is logged and the run proceeds with no baseline (the conformer
    then falls back to its prior self-judgment). Idempotent: the presence
    of `st.data["conformance"]["_baseline"]` is the completion sentinel, so
    `--resume` does not re-run it.

    A RED base is surfaced loudly (a `log()` warning + a
    `run.json.health.base_suite` record) because it usually means leerie's
    container/provisioning could not make the repo green before starting —
    the operator's signal to suspect provisioning / memory / missing deps,
    distinct from a genuinely red base branch."""
    conf = st.data.get("conformance") or {}
    if conf.get("_baseline") is not None:
        log("phase 4: base-health baseline already captured — skipping (resume)")
        return
    staging = (leerie_dir / "worktrees" / "staging").resolve()
    if not staging.is_dir():
        log("phase 4: base-health baseline skipped — staging worktree absent")
        return

    repo_root = st.repo_root
    blt = resolve_blt(repo_root)
    # resolve_blt keys the test axis "test" (singular); the conformer's
    # structured-output result keys it "tests" (plural). Map the axis name
    # we store/report ("tests", matching the conformer result + baseline
    # consumers) to the resolve_blt command key ("test").
    _AXIS_CMD_KEY = {"build": "build", "lint": "lint", "tests": "test"}
    if not any(blt.get(_AXIS_CMD_KEY[a]) for a in ("build", "lint", "tests")):
        log("phase 4: base-health baseline skipped — no build/lint/test "
            "commands resolved for this repo")
        return

    log("phase 4: capturing base-tree health baseline on staging")
    verbosity = st.data.get("verbosity", VERBOSITY_DEFAULT)
    log_path = st.run_dir / "logs" / "base-baseline.log"
    timeout = float(caps.get("worker_timeout_sec") or 5400)

    # Install the provision recipe into staging so the suite can run.
    # Deps live only in worktrees (§6½); staging starts bare. Failure to
    # install is non-fatal — a subsequent BLT command that needs deps will
    # simply exit non-zero, which is itself recorded as a RED axis.
    recipe = (st.data.get("provision") or {}).get("recipe") or []
    for e in recipe:
        if e.get("kind") not in ("install", "build") or not e.get("command"):
            continue
        wd = staging / (e.get("working_dir") or ".")
        try:
            await run_streaming(
                e["command"], cwd=str(wd),
                timeout=float(e.get("timeout_s") or 1800),
                log_path=log_path, label=f"baseline-install: {' '.join(e['command'])}",
                verbosity=verbosity)
        except subprocess.TimeoutExpired:
            log(f"  base-baseline: install timed out: {' '.join(e['command'])}")
        except Exception as ex:  # non-fatal: BLT below will show the effect
            log(f"  base-baseline: install error "
                f"({type(ex).__name__}): {' '.join(e['command'])}")

    axes: dict[str, dict] = {}
    for axis in ("build", "lint", "tests"):
        cmd = (blt.get(_AXIS_CMD_KEY[axis]) or "").strip()
        if not cmd:
            # Every axis dict carries `measured` so no consumer needs a
            # default; a not-applicable axis is unmeasured (and, being
            # ran=False, is neither red nor green).
            axes[axis] = {"ran": False, "measured": False, "passed": None,
                          "summary": "", "command": ""}
            continue
        try:
            rc, tail = await run_streaming(
                ["bash", "-lc", cmd], cwd=str(staging), timeout=timeout,
                log_path=log_path, label=f"baseline-{axis}: {cmd}",
                verbosity=verbosity)
            summary = (tail or "").strip()[-400:]
            if rc != 0 and _runner_missing(summary):
                # The command didn't run — its runner isn't on PATH (e.g.
                # the recipe's `pip install` failed, so pytest is absent).
                # This is "could not measure," NOT "base is RED": recording
                # it as red-with-`command not found` gives the conformer a
                # useless delta and provokes it to re-derive the baseline
                # destructively (git stash / checkout <base> -- .). Mark it
                # unmeasurable so red-axis logic and the conformer prompt
                # both skip it.
                axes[axis] = {"ran": True, "measured": False, "passed": None,
                              "command": cmd, "summary": summary}
            else:
                axes[axis] = {
                    "ran": True, "measured": True, "passed": rc == 0,
                    "command": cmd,
                    # Keep a short tail for the RED warning / conformer context.
                    "summary": summary,
                }
        except subprocess.TimeoutExpired:
            axes[axis] = {"ran": True, "measured": True, "passed": False,
                          "command": cmd,
                          "summary": f"timed out after {int(timeout)}s"}
        except Exception as ex:
            axes[axis] = {"ran": False, "measured": False, "passed": None,
                          "command": cmd,
                          "summary": f"{type(ex).__name__}: {ex}"}

    # An axis is RED only if it actually ran AND was measurable AND failed.
    # Unmeasurable axes (runner missing) are neither red nor green — they
    # carry no delta and are surfaced separately to the conformer.
    red = [a for a in ("build", "lint", "tests")
           if axes[a].get("ran") and axes[a].get("measured")
           and not axes[a].get("passed")]
    baseline = {"axes": axes, "red_axes": red}
    conf = st.data.setdefault("conformance", {})
    conf["_baseline"] = baseline
    st.save()

    if red:
        log(f"phase 4: ⚠ base tree is RED on {', '.join(red)} — leerie could "
            "not confirm this repo is green before starting. Suspect "
            "provisioning / missing deps / memory limits, or a genuinely "
            "red base branch. Conformer residuals for these axes will be "
            "treated as pre-existing (delta-scoped).")
        _write_run_json(st.run_dir,
                        health={"base_suite": {"status": "red",
                                               "red_axes": red}})
    else:
        log("phase 4: base tree is GREEN (build/lint/tests) — new "
            "build/lint/test failures will be attributed to this run.")
        _write_run_json(st.run_dir,
                        health={"base_suite": {"status": "green",
                                               "red_axes": []}})


async def run_final_conformance(leerie_dir: Path, st: State, caps: dict,
                                models: dict[str, str],
                                efforts: dict[str, str | None]) -> None:
    """Whole-tree conformance pass on the integrated staging worktree
    (DESIGN §6 *Worktree and integration model*, final-tree pass).

    Runs once after every wave has integrated, before phase_finalize.
    Same conformer worker, same prompt, same `conformance_rounds` cap,
    same protected-path rollback as the per-subtask phase — the only
    differences are cwd (staging worktree), DIFF_BASE (working_branch
    rather than the run branch), and the absence of a subtask spec /
    criteria file. Advisory: any failure mode (missing staging
    worktree, WorkerError, malformed result, exhausted rounds) is
    recorded under `st.data["conformance"]["_final"]["warnings"]`,
    never raised."""
    staging = (leerie_dir / "worktrees" / "staging").resolve()
    if not staging.is_dir():
        log("phase 5: final conformance skipped — staging worktree absent")
        return
    working_branch = st.data.get("working_branch")
    if not working_branch:
        log("phase 5: final conformance skipped — working_branch not in state")
        return
    # Resume idempotence: phase_execute is gated on `completed_waves`
    # but the final pass leaves no per-round sentinel of its own. On
    # `--resume` after this pass already recorded a result, re-running
    # would burn worker budget and could overwrite a clean result with
    # a different one. The presence of `_final` in `st.data["conformance"]`
    # is the completion sentinel.
    if (st.data.get("conformance") or {}).get("_final") is not None:
        log("phase 5: final conformance already complete — skipping (resume)")
        return

    log("phase 5: final-tree conformance on staging")
    st.data["current_phase"] = "phase 5: final conformance"
    st.save()

    repo_root = st.repo_root
    rules_files = discover_rules_files(repo_root)
    blt = resolve_blt(repo_root)

    warnings: list[str] = []
    last_res: dict | None = None
    blt_feedback: str | None = None

    sys_prompt = load_prompt("conformer")
    rules_paths_str = _format_rules_paths(rules_files, repo_root)

    # Base ref and pre-pass snapshot for the clobber-survival check
    # (DESIGN §9 *No clobbering the implementer's work*). Staging is cut
    # from the run branch (setup-run.sh), so `run_branch..staging_before`
    # is the union of all integrated implementer work; the run branch is
    # the base version to compare against.
    run_branch = compute_run_branch(st.run_id)
    staging_before_sha = await _branch_head_sha(str(staging))
    clobbered_files: list[str] = []

    for c_round in range(caps["conformance_rounds"]):
        before_sha = await _branch_head_sha(str(staging))

        # Build the per-round user prompt. Mirrors run_conformer's shape
        # but the spec / criteria lines are replaced with one sentence
        # framing this as the post-integration whole-tree pass — there
        # is no per-subtask spec file to point at.
        up = [
            "Run the post-integration whole-tree conformance phase on "
            "the merged run branch. This is the final conformer pass "
            "before the PR is opened; you are reviewing the *combined* "
            "diff of every subtask in this run, not any one subtask.",
            f"LEERIE_DIR is {leerie_dir} (absolute). There is no "
            "subtask spec or criteria file for this pass — the unit "
            "of work is the whole run.",
            "Your current working directory IS the integrated staging "
            "worktree. Make and commit any fixes here. Every commit "
            "subject must start with `conformer:`.",
            f"RULES_FILES: {rules_paths_str}",
            f"BUILD_CMD: {blt.get('build') or '(none)'}",
            f"LINT_CMD: {blt.get('lint') or '(none)'}",
            f"TEST_CMD: {blt.get('test') or '(none)'}",
            f"DIFF_BASE: {working_branch} (compare with "
            f"`git diff {working_branch}..HEAD`)",
        ]
        baseline_section = _format_baseline_section(
            (st.data.get("conformance") or {}).get("_baseline"))
        if baseline_section is not None:
            up.append(baseline_section)
        recipe_section = _format_provision_recipe_section(
            (st.data.get("provision") or {}).get("recipe") or [],
            audience="conformer")
        if recipe_section is not None:
            up.append(recipe_section)
        if blt_feedback is not None:
            up.append(blt_feedback)

        try:
            st.bump_workers(caps)
            res = await claude_p(
                user_prompt="\n".join(up),
                system_prompt=sys_prompt,
                schema_key="conformer", cwd=str(staging),
                allowed_tools=ACT_TOOLS, max_turns=60,
                autonomous=True, caps=caps, st=st,
                model=models["conformer"],
                effort=efforts["conformer"],
                sid=f"final-conformer-r{c_round}")
        except WorkerError as e:
            warnings.append(f"final conformer round {c_round}: "
                            f"WorkerError: {e}")
            break
        except subprocess.TimeoutExpired:
            timeout = caps.get("worker_timeout_sec", "?")
            warnings.append(f"final conformer round {c_round}: timed out "
                            f"after {timeout}s")
            break

        last_res = res
        # validate_conformance_result enforces shape rules
        # (residuals-imply-rules-files-read, every fixed violation
        # cites a rule) and path-traversal safety for docs/tests
        # update entries. The worktree it resolves paths against is
        # the staging worktree here, mirroring how the per-subtask
        # call passes the subtask worktree.
        err = validate_conformance_result(res, str(staging))
        if err:
            warnings.append(f"final conformer round {c_round}: "
                            f"malformed result: {err}")
            break

        # Protected-path rollback: same discipline as the per-subtask
        # loop, but using `_protected_paths_since(before_sha)` instead
        # of `check_diff_scope` — the latter is hardcoded to diff
        # against the run branch, which on the staging worktree (at
        # the run-branch HEAD) would produce an empty diff and
        # silently no-op. Scoping the check to this round's added
        # commits is the correct semantics for the final pass: the
        # protected-path discipline is about the conformer's own
        # commits, not about anything implementers may have done
        # (those were caught by the per-subtask gates).
        protected = await _protected_paths_since(str(staging), before_sha)
        if protected:
            discarded = await _uncommitted_paths(str(staging))
            if discarded:
                warnings.append(
                    f"final conformer round {c_round}: discarding "
                    f"{len(discarded)} uncommitted file(s) during "
                    f"rollback: {[line[3:] for line in discarded]}")
            await rollback_conformer_commits(str(staging), before_sha)
            warnings.append(f"final conformer round {c_round}: "
                            f"protected-path violation reverted "
                            f"(touched {protected})")
            break

        dirty = await _uncommitted_paths(str(staging))
        if dirty:
            warnings.append(f"final conformer round {c_round}: left "
                            f"{len(dirty)} uncommitted change(s) — not "
                            "rolled back, but surfaced as advisory")

        # Clobber-survival check (DESIGN §9 *No clobbering the
        # implementer's work*): same guard as the per-subtask phase,
        # scoped to the integrated staging tree. base=run_branch,
        # impl_head=staging HEAD captured before this pass.
        clobbered = await clobbered_owned_files(
            str(staging), run_branch, staging_before_sha)
        if clobbered:
            clobbered_files = clobbered
            warnings.append(
                f"final conformer round {c_round}: reverted/deleted "
                f"{len(clobbered)} integrated file(s): {clobbered}")
            if caps.get("strict_conformer"):
                discarded = await _uncommitted_paths(str(staging))
                if discarded:
                    warnings.append(
                        f"final conformer round {c_round}: discarding "
                        f"{len(discarded)} uncommitted file(s) during "
                        f"clobber rollback: {[line[3:] for line in discarded]}")
                await rollback_conformer_commits(
                    str(staging), staging_before_sha)
                warnings.append(
                    f"final conformer round {c_round}: strict mode — "
                    "rolled conformer commits back to restore clobbered work")
                break

        unprefixed = await _unprefixed_conformer_commits(
            str(staging), before_sha)
        for subject in unprefixed:
            warnings.append(f"final conformer round {c_round}: commit "
                            f"subject missing `conformer:` prefix: "
                            f"{subject!r}")

        # BLT-axis observability: same per-round warnings as the
        # per-subtask conformance phase. Final-conformer's sid is
        # f"final-conformer-r{c_round}" — see the claude_p call above.
        _emit_bash_axis_warnings(
            leerie_dir / "logs" / f"final-conformer-r{c_round}.log",
            f"final conformer round {c_round}", warnings)

        bg_retry_warnings = [
            w for w in warnings
            if w.startswith(f"final conformer round {c_round}:")
            and "auto-backgrounded" in w]
        blt_feedback = (
            _format_check_feedback(bg_retry_warnings, c_round,
                                   caps["conformance_rounds"])
            if bg_retry_warnings else None)

        if _conformance_clean(res):
            break

    if last_res is not None:
        warnings.extend(_summarize_residuals(last_res))

    final_blocked = bool(
        caps.get("strict_conformer")
        and (clobbered_files
             or (last_res is not None and not _conformance_clean(last_res))))

    st.data.setdefault("conformance", {})["_final"] = {
        "result": last_res,
        "warnings": warnings,
        "blocked": bool(final_blocked),
    }
    st.save()
    for w in warnings:
        log(f"  final conformance: {w}")
    if final_blocked:
        why = (f"conformer reverted/deleted integrated file(s): "
               f"{clobbered_files}" if clobbered_files else "has residuals")
        die(f"strict-conformer: final-tree conformance {why}; "
            "run blocked. Fix and --resume.")


async def settle_subtask(sid: str, leerie_dir: Path, caps: dict, st: State,
                         models: dict[str, str],
                         efforts: dict[str, str | None]) -> dict:
    """Drive one subtask to a terminal state.

    Three bounded escalation paths, all code-enforced:
      - subtask continuations (cap: caps['subtask_continuations']) —
        consumed by both context-exhaustion handoffs and DESIGN §11
        mid-execution clarifications, sharing a single budget so a
        subtask cannot get extra re-spawns by mixing the two
      - corrective retries of a retryable failure (cap: caps['failed_retries'])

    A non-retryable failure (see `_retryable_failure`) terminates the subtask
    immediately with status 'failed' — no retry is attempted. Returns the final
    result."""
    continuations = 0
    retries = 0
    confidence_retries = 0
    note = ""
    continuation = False
    worktree = str(leerie_dir / "worktrees" / sid)
    subtask_path = leerie_dir / "subtasks" / f"{sid}.json"
    subtask = json.loads(subtask_path.read_text()) if subtask_path.exists() else {}

    async def fail(kind: str, reason: str) -> dict | None:
        """Record a failed attempt. `kind` is the structured discriminator
        `_retryable_failure` dispatches on (see `_RETRYABLE_FAILURE_KINDS`);
        `reason` is the human-readable diagnostic stored on the result and
        echoed to the log. Returns a terminal result dict if the subtask
        is done (non-retryable, or retry cap exhausted), or None if the
        caller should loop for one more corrective attempt.

        On a retryable failure that will loop, `_reset_subtask_worktree`
        clears the leftover worktree + branch so `new-worktree.sh`
        reaches its "fresh subtask" path on the next iteration. Without
        this reset, the retry hits `fatal: a branch ... already exists`
        and the WorkerError escapes to `gather_or_cancel`, killing the
        whole wave."""
        nonlocal retries, continuation, note
        res = {"subtask_id": sid, "status": "failed", "summary": reason}
        st.data.setdefault("subtask_status", {})[sid] = "failed"
        st.save()
        if not _retryable_failure(kind):
            log(f"  {sid}: non-retryable failure ({kind}) — terminating: {reason}")
            return res
        retries += 1
        if retries > caps["failed_retries"]:
            log(f"  {sid}: retry cap reached — terminating")
            return res
        await _reset_subtask_worktree(sid, leerie_dir, st.run_id)
        continuation = False
        note = f"Previous attempt failed: {reason}"
        return None

    while True:
        res = await run_implementer(sid, leerie_dir, caps, st, models, efforts,
                                    continuation=continuation, note=note)

        # cross-field invariant check — catches a worker that lied about
        # status. A self-contradictory result means the worker is malfunctioning
        # or dishonest: non-retryable by `_retryable_failure` (kind="broken"),
        # except for the empty-handoff case which validate_result tags
        # "empty_handoff" — retryable because a fresh worker can plausibly
        # do better.
        problem = validate_result(res)
        rescued_from_empty_handoff = False
        if problem:
            kind, message = problem
            # An `empty_handoff` (worker ended its turn with no checkpoint —
            # typically because it backgrounded an expensive final step like a
            # build that OOM-died, so `claude -p` was reaped mid-turn) must NOT
            # discard a green, committed diff. `fail()` would `_reset_subtask_
            # worktree`, destroying the commits, then burn the retry cap. If the
            # worktree already holds committed work, the worker DID produce a
            # deliverable — settle it as `complete` and let the advisory
            # conformance phase (below) record whatever verification step didn't
            # finish. This is the outcome subtasks whose criteria didn't gate on
            # the build got by luck (they returned before dying); made
            # deterministic here. See DESIGN §9.
            if kind == "empty_handoff" and \
                    await branch_has_commits_ahead(
                        worktree, compute_run_branch(st.run_id)):
                log(f"  {sid}: {message} — but the worktree has committed "
                    "work; the worker likely ended its turn on an incomplete "
                    "background task (e.g. an OOM-killed build). Keeping the "
                    "committed diff and settling via advisory conformance "
                    "instead of discarding it.")
                res = {"subtask_id": sid, "status": "complete",
                       "summary": (res.get("summary")
                                   or "worker ended its turn with committed "
                                   "work but no checkpoint (incomplete "
                                   "background step); committed diff kept"),
                       "criteria_results": res.get("criteria_results") or []}
                rescued_from_empty_handoff = True
            else:
                log(f"  result invariant violated for {sid}: {message}")
                done = await fail(kind, message)
                if done is not None:
                    return done
                continue

        status = res.get("status")

        # CRITIC-pattern confidence + mechanical check on complete results.
        # Separate budget from subtask_continuations so confidence retries
        # don't consume the handoff/clarification budget. Skipped for a result
        # rescued from `empty_handoff`: the worker never returned a confidence
        # envelope (it was reaped mid-turn), so there is nothing to gate on —
        # re-spawning it would just repeat the doomed background step.
        if status == "complete" and not rescued_from_empty_handoff and \
                confidence_retries < caps.get("implementer_confidence_retries", 2):
            conf = (res.get("confidence") or {}) \
                if isinstance(res.get("confidence"), dict) else {}
            if not _confidence_axes_clear(conf, ["root_cause", "solution"]):
                below = {ax: conf.get(ax) for ax in ["root_cause", "solution"]
                         if not isinstance(conf.get(ax), (int, float))
                         or conf[ax] < 9.0}
                log(f"  {sid}: confidence gate not cleared: {below}")
                confidence_retries += 1
                continuation = True
                note = (
                    f"Previous attempt returned complete but confidence "
                    f"gate did not clear: {below}. Re-examine and either "
                    f"raise your confidence with evidence or report blocked.")
                continue
            # Mechanical checks on the implementer's output.
            run_branch = compute_run_branch(st.run_id)
            diff_proc = await run_proc(
                ["git", "diff", "--name-only", run_branch],
                cwd=worktree)
            actual_files = set(diff_proc.stdout.strip().splitlines()
                               ) if diff_proc.returncode == 0 else set()
            impl_issues = check_implementer_output(res, subtask, actual_files)
            if impl_issues and confidence_retries < caps.get(
                    "implementer_confidence_retries", 2):
                log(f"  {sid}: mechanical check issues: {impl_issues}")
                confidence_retries += 1
                continuation = True
                note = _format_check_feedback(
                    impl_issues, confidence_retries - 1,
                    caps.get("implementer_confidence_retries", 2))
                continue

        st.data.setdefault("subtask_status", {})[sid] = status
        if status == "complete":
            # A prior failed attempt may have written this sid into the
            # blocked dict (wave-failure :14902 / integrate precondition).
            # A resume that completes the subtask must clear the stale entry
            # so state.json doesn't carry a contradictory blocked record.
            st.data.get("blocked", {}).pop(sid, None)
        st.save()

        if status == "complete":
            # DESIGN §5 *Artifact passing between subtasks*: a non-empty
            # `artifacts` array is a substitute deliverable that lets a
            # research-style subtask pass the commit-presence gate
            # without committing code. The orchestrator persists the
            # artifacts on the success path below; the
            # commit/dirty/scope checks below stay as written for the
            # code-implementation case (empty artifacts) and for the
            # mixed case (some commits and some artifacts).
            has_artifacts = bool(res.get("artifacts"))
            # a 'complete' claim with no commits is a retryable mistake —
            # the worker may genuinely have work to commit and just forgot
            commit_err = await check_branch_has_commits(
                sid, worktree, compute_run_branch(st.run_id))
            if commit_err and not has_artifacts:
                kind, message = commit_err
                log(f"  branch check failed for {sid}: {message}")
                done = await fail(kind, message)
                if done is not None:
                    return done
                continue
            # uncommitted changes — retryable for a normal `complete`. For a
            # result rescued from `empty_handoff`, tracked uncommitted changes
            # are debris a reaped worker left mid-edit; the deliverable is the
            # already-committed diff, not the debris.
            wt_status = await run_proc(
                ["git", "status", "--porcelain"], cwd=worktree)
            dirty = [l for l in wt_status.stdout.splitlines()
                     if l and not l.startswith("??")]
            if dirty:
                if rescued_from_empty_handoff:
                    # Discard the debris rather than fail: a `dirty_worktree`
                    # fail would `_reset_subtask_worktree` and destroy the very
                    # commits we are keeping. Best-effort — run_proc returns
                    # (doesn't raise) on nonzero, so a failed checkout leaves
                    # the debris for the dirty-worktree guard on the next
                    # integration step; not fatal here.
                    await run_proc(["git", "checkout", "--", "."],
                                   cwd=worktree)
                else:
                    done = await fail(
                        "dirty_worktree",
                        f"{sid}: worktree has {len(dirty)} uncommitted "
                        f"change(s) — changes will be lost on integration")
                    if done is not None:
                        return done
                    continue
            # protected-path violation — the worker wrote to .git/ etc.: it is
            # broken, not merely careless. Non-retryable by `_retryable_failure`.
            scope_err = await check_diff_scope(sid, worktree, subtask, st)
            if scope_err:
                done = await fail("broken", scope_err)
                if done is not None:
                    return done
                continue

            # DESIGN §5 *Artifact passing between subtasks*: persist
            # structured deliverables for downstream subtasks. The
            # orchestrator owns the artifacts directory — workers do
            # not write there directly. Atomic write (temp +
            # os.replace) keeps a partial file from ever appearing if
            # the orchestrator dies mid-flush.
            if has_artifacts:
                _write_subtask_artifacts(leerie_dir, sid, res["artifacts"])

            # DESIGN §9 *Post-work conformance*: advisory by default,
            # blocking when --strict-conformer is on. Runs only on the
            # success path (every check above has passed). In advisory
            # mode, attaches warnings to `res`; in strict mode, returns
            # `blocked` if residuals remain.
            #
            # The broad try/except is load-bearing: `_run_conformance_phase`
            # calls `run_proc` → `asyncio.create_subprocess_exec`, which
            # raises `FileNotFoundError` when `cwd` is missing. Catching
            # everything here preserves the advisory framing for exceptions;
            # the strict-conformer check runs AFTER this block on the
            # return value, not via exception.
            conf_res: dict | None = None
            conf_warnings: list[str] = []
            blocked_reason: str | None = None
            try:
                conf_res, conf_warnings, blocked_reason = \
                    await _run_conformance_phase(
                        sid, leerie_dir, worktree, subtask, caps, st,
                        models, efforts)
            except Exception as e:
                conf_warnings.append(
                    f"conformance phase raised {type(e).__name__}: {e} — "
                    "surfaced as advisory, subtask still complete")
            if conf_res is not None:
                res["conformance"] = conf_res
            if conf_warnings:
                res["conformance_warnings"] = conf_warnings
                for w in conf_warnings:
                    log(f"  {sid}: conformance: {w}")
            st.data.setdefault("conformance", {})[sid] = {
                "result": conf_res,
                "warnings": conf_warnings,
            }
            st.save()
            if blocked_reason:
                return {"subtask_id": sid, "status": "blocked",
                        "blocker": blocked_reason,
                        "summary": blocked_reason}
            return res

        if status == "incomplete-handoff":
            # Worktree convention from scripts/new-worktree.sh:
            # .leerie/worktrees/<subtask-id>. The freshness check on
            # `## Files touched` validates paths against this directory;
            # if it no longer exists (e.g. cleanup ran early), the check
            # is skipped gracefully.
            wt_root = leerie_dir / "worktrees" / sid
            cp_err = validate_checkpoint(res.get("checkpoint_path") or "",
                                         worktree_root=wt_root)
            if cp_err:
                log(f"  bad checkpoint for {sid}: {cp_err}")
                return {"subtask_id": sid, "status": "blocked",
                        "blocker": f"checkpoint invalid: {cp_err}",
                        "summary": cp_err}
            continuations += 1
            if continuations > caps["subtask_continuations"]:
                return {"subtask_id": sid, "status": "blocked",
                        "blocker": ("exceeded subtask continuation cap — "
                                    "subtask is mis-scoped and needs "
                                    "re-decomposition"),
                        "summary": "subtask continuation cap exceeded"}
            continuation, note = True, ""
            continue

        if status == "needs-clarification":
            # DESIGN §11 mid-execution clarification: same continuation
            # mechanism as `incomplete-handoff` (worker wrote a
            # checkpoint, orchestrator re-spawns with CONTINUATION),
            # plus a side trip through surface_clarification to capture
            # the user's answer. Consumes from the same
            # subtask_continuations budget — there is no extra "ask the
            # user" allowance.
            wt_root = leerie_dir / "worktrees" / sid
            cp_err = validate_checkpoint(res.get("checkpoint_path") or "",
                                         worktree_root=wt_root)
            if cp_err:
                log(f"  bad checkpoint for {sid}: {cp_err}")
                return {"subtask_id": sid, "status": "blocked",
                        "blocker": f"checkpoint invalid: {cp_err}",
                        "summary": cp_err}
            continuations += 1
            if continuations > caps["subtask_continuations"]:
                return {"subtask_id": sid, "status": "blocked",
                        "blocker": ("exceeded subtask continuation cap — "
                                    "subtask is mis-scoped and needs "
                                    "re-decomposition"),
                        "summary": "subtask continuation cap exceeded"}
            # Surface the question; interactive prompt or non-interactive
            # exit with EXIT_NEEDS_ANSWERS. On interactive return, the
            # answer is already in st.data['answers'] so the re-spawned
            # worker reads it via _clarification_answers in its spec.
            surface_clarification(sid, res["clarification_question"],
                                  res.get("checkpoint_path") or "", st)
            # Rewrite this subtask's spec so the new answer is visible
            # to the next implementer — the spec was written once at
            # phase_plan time with the then-current answers; clarifications
            # captured later must be propagated.
            spec_path = leerie_dir / "subtasks" / f"{sid}.json"
            if spec_path.exists():
                spec = json.loads(spec_path.read_text())
                spec["_clarification_answers"] = st.data.get("answers", {})
                spec_path.write_text(json.dumps(spec, indent=2))
            continuation, note = True, ""
            continue

        if status == "failed":
            # A worker that reported failure itself — terminal by default.
            # The worker (not an orchestrator-side check) decided to fail,
            # so there is no producer-tagged structured kind. "broken" is
            # the right default: a worker that ran to completion and then
            # self-reported `failed` is broken-worker territory unless we
            # have evidence otherwise. Matches the row in IMPLEMENTATION.md's
            # "Per-subtask checks" table in §5 ("Deterministic enforcement
            # points").
            done = await fail(
                "broken", res.get("summary") or "worker reported failure")
            if done is not None:
                return done
            continue

        # blocked, or anything unexpected
        return res


async def integrate_wave(wave: list[str], results: dict[str, dict],
                         leerie_dir: Path, caps: dict, st: State,
                         models: dict[str, str],
                         efforts: dict[str, str | None]) -> list[str]:
    """Merge each completed subtask branch into staging (git merge, not
    cherry-pick); resolve conflicts with an integrator worker. Returns the
    list of integrated ids.

    If an integrator cannot resolve a conflict (status other than 'resolved'),
    the in-progress merge is aborted so the staging worktree is left clean, and
    the run is terminated with the integrator's diagnosis — an unresolved
    conflict must not silently proceed onto a corrupt staging tree."""
    integrated, integrated_so_far = [], []
    staging = (leerie_dir / "worktrees" / "staging").resolve()
    for sid in wave:
        if results.get(sid, {}).get("status") != "complete":
            continue
        proc = await run_script("integrate.sh", sid, st.run_id)
        if proc.returncode == 0:
            integrated.append(sid)
            integrated_so_far.append(sid)
            continue
        if proc.returncode == 2:
            # exit 2 from integrate.sh is a precondition failure (staging
            # worktree or subtask branch missing) — not a merge conflict.
            # Spawning an integrator against a missing worktree fails in
            # confusing ways, so abort here with the script's own message.
            # Save state first (local convention — see the two neighboring
            # die() sites below) so `--resume` can pick up what was done.
            reason = (f"integrate.sh precondition failure: "
                      f"{proc.stderr.strip() or proc.stdout.strip() or 'no message'}")
            st.data.setdefault("blocked", {})[sid] = reason
            st.save()
            die(f"integrate.sh precondition failure for {sid}: "
                f"{proc.stderr.strip() or proc.stdout.strip() or 'no message'}")
        # exit 1 (conflict): staging worktree is mid-merge — hand to an integrator
        log(f"  conflict integrating {sid}; spawning integrator")
        sys_prompt = load_prompt("integrator")
        up_parts: list[str] = [
            f"Resolve the in-progress merge conflict in this worktree.\n"
            f"LEERIE_DIR is {leerie_dir}.\n"
            f"Incoming subtask: {sid}\n"
            f"Already-integrated subtasks it may conflict with: "
            f"{', '.join(integrated_so_far) or 'none'}"]

        async def _invoke_integrator() -> dict:
            st.bump_workers(caps)
            return await claude_p(
                user_prompt="\n\n".join(up_parts),
                system_prompt=sys_prompt,
                schema_key="integrator", cwd=str(staging),
                allowed_tools=ACT_TOOLS, max_turns=60,
                autonomous=True, caps=caps, st=st,
                model=models["integrator"],
                effort=efforts["integrator"],
                sid=f"integrator-{sid}")

        async def _on_integrator_fb(fb: str) -> dict:
            if len(up_parts) > 1:
                up_parts[-1] = fb
            else:
                up_parts.append(fb)
            return {}

        # The integrator's async mechanical checks (conflict markers,
        # merge committed) run in the resolved-status path below.
        ires, int_warnings = await _run_checked_loop(
            invoke=_invoke_integrator,
            check=lambda r: check_integrator_output(r),
            name=f"integrator-{sid}",
            max_rounds=caps["judgment_check_rounds"],
            make_feedback_prompt=_on_integrator_fb,
        )
        if ires is None:
            await run_proc(["git", "merge", "--abort"], cwd=str(staging))
            die(f"integrator for {sid} crashed; merge aborted")
        for w in int_warnings:
            log(f"  integrator-{sid}: {w}")
        if ires.get("status") == "resolved":
            # the integrator must have actually committed the merge — a
            # 'resolved' claim with the worktree still mid-merge is a lie,
            # the integrator-side analogue of check_branch_has_commits.
            merge_err = await check_merge_committed(staging)
            if merge_err:
                await run_proc(["git", "merge", "--abort"], cwd=str(staging))
                die(f"integrator for {sid} returned 'resolved' but {merge_err}. "
                    f"The merge was aborted; {compute_run_branch(st.run_id)} "
                    "is clean. Resolve and re-run with --resume.")
            commit_err = await check_integrator_commit(staging)
            if commit_err:
                # non-fatal: log and record, but don't undo the integration
                log(f"  ⚠  integrator commit warning for {sid}: {commit_err}")
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
            await run_proc(["git", "merge", "--abort"], cwd=str(staging))
            die(f"integrator could not integrate {sid} "
                f"({ires.get('status')}): {diagnosis}\n"
                f"The in-progress merge was aborted; "
                f"{compute_run_branch(st.run_id)} is intact at the last "
                f"good wave. Resolve the conflict between {sid} and "
                f"the already-integrated subtasks manually, then re-run with "
                f"--resume.")
    return integrated


async def phase_execute(leerie_dir: Path, st: State, caps: dict,
                        models: dict[str, str],
                        efforts: dict[str, str | None]) -> None:
    """Phases 4-5: create staging, then run waves sequentially; within a wave,
    subtasks in parallel (bounded by max_parallel)."""
    log("phase 4: creating run-branch worktree")
    st.data["current_phase"] = "phase 4-5: implementing"
    st.save()
    proc = await run_script("setup-run.sh", st.run_id)
    if proc.returncode != 0:
        die(f"run setup failed: {proc.stderr.strip()}")

    # On Fly, machine stop SIGKILLs the orchestrator (PID 1 is sleep
    # infinity; SIGTERM reaps it, kernel SIGKILLs children) — the
    # finally-block cleanup never runs and .git/worktrees/ metadata
    # from the prior invocation persists on the volume. Prune before
    # any wave creates new worktrees so stale entries don't crash
    # `git worktree list --porcelain` in new-worktree.sh.
    await run_proc(["git", "worktree", "prune"])

    # Base-tree health baseline (DESIGN §9): staging now exists off the
    # base HEAD and no wave has mutated it yet, so this is the earliest
    # accurate snapshot. Advisory + idempotent (sentinel-guarded), so it
    # runs once on the fresh path and is skipped on --resume. Opt-out via
    # skip_base_baseline (the full-suite-run cost).
    if not st.data.get("skip_base_baseline"):
        try:
            await capture_conformance_baseline(leerie_dir, st, caps)
        except Exception as e:
            # Defense-in-depth: capture_conformance_baseline is documented
            # to never raise, but a bug in its glue must not block the run.
            log(f"phase 4: base-health baseline errored "
                f"({type(e).__name__}: {e}); proceeding with no baseline")

    sem = asyncio.Semaphore(caps["max_parallel"])

    async def settle_one(sid: str) -> tuple[str, dict]:
        async with sem:
            r = await settle_subtask(sid, leerie_dir, caps, st, models, efforts)
            log(f"  {sid}: {r.get('status')}")
            return sid, r

    waves = st.data["waves"]
    start = st.data.get("completed_waves", 0)
    for wi in range(start, len(waves)):
        wave = waves[wi]
        # On --resume, skip subtasks already completed in a prior
        # invocation.  Failed/blocked subtasks are retried — that is
        # the point of --resume.
        prior = st.data.get("subtask_status", {})
        remaining = [sid for sid in wave if prior.get(sid) != "complete"]
        if not remaining:
            log(f"phase 5: wave {wi + 1} of {len(waves)} — "
                f"all {len(wave)} subtask(s) already complete, skipping")
            st.data["completed_waves"] = wi + 1
            st.save()
            continue
        skipped = len(wave) - len(remaining)
        if skipped:
            log(f"phase 5: wave {wi + 1} of {len(waves)} — "
                f"{len(remaining)} subtask(s) to run "
                f"({skipped} already complete)")
        else:
            log(f"phase 5: wave {wi + 1} of {len(waves)} — "
                f"{len(wave)} subtask{'s' if len(wave) != 1 else ''}")

        # Clear stale terminal status so _get_progress counts retried
        # subtasks as "running" (absent = running per _get_progress).
        ss = st.data.get("subtask_status", {})
        cleared = [sid for sid in remaining if ss.pop(sid, None) is not None]
        if cleared:
            st.save()

        pairs = await gather_or_cancel(*(settle_one(sid) for sid in remaining))
        results: dict[str, dict] = dict(pairs)

        blocked = [s for s, r in results.items()
                   if r.get("status") in ("blocked", "failed")]

        # Integrate successful subtasks BEFORE dying on failures
        # (DESIGN §3 *Partial-wave integration*). integrate_wave already
        # filters for status=="complete", so passing the full wave is safe.
        integrated = await integrate_wave(
            wave, results, leerie_dir, caps, st, models, efforts)

        # Deterministic post-integration safety net: an unresolved
        # conflict marker means integration broke the tree. Per-subtask
        # quality is the implementer's §8 confidence gate — there is no
        # LLM wave-level re-validation (see DESIGN §8, §9).
        staging_path = leerie_dir / "worktrees" / "staging"
        marker_err = await scan_conflict_markers(staging_path)
        if marker_err:
            die(f"wave {wi + 1}: {marker_err}\n"
                f"Resolve manually in {staging_path}, commit, "
                "then re-run with --resume.")

        if blocked:
            if integrated:
                log(f"  integrated {len(integrated)} successful subtask(s) "
                    f"before reporting {len(blocked)} failure(s)")
            st.data["blocked"] = {s: results[s].get("blocker")
                                  or results[s].get("summary") for s in blocked}
            st.save()
            die(f"wave {wi + 1} has unresolved subtasks: {', '.join(blocked)}. "
                f"See {st.path}; resolve and re-run with --resume.")

        st.data["completed_waves"] = wi + 1
        st.save()


# `push_and_open_pr` was removed when finalize moved to the host
# launcher (DESIGN §6 *Finalization*). The launcher does `git push` +
# `gh pr create` in bash + jq after this container exits — auth state
# lives on the host where it works without forwarding.
#
# `compose_pr_body` is kept (above) as the canonical reference for the
# PR body shape; the launcher's bash composition is structurally
# equivalent. Keeping the Python version makes future audits cheap
# (one file to read).


# --- PR template discovery + LLM body composition -----------------------
# DESIGN §6 *Finalization* hands the PR title/body to a `claude -p`
# worker (pr_writer) so the body respects the target repo's PR template
# when one is present. The deterministic bash composition in the host
# launcher (and `compose_pr_body` above) remains the fail-open fallback.

# GitHub's canonical search order for a single top-level PR template,
# in the priority the GitHub web UI uses.
_PR_TEMPLATE_SINGLE_LOCATIONS = (
    ".github/pull_request_template.md",
    "pull_request_template.md",
    "docs/pull_request_template.md",
)
# Directories where GitHub looks for *multiple* templates. Any .md
# inside any of these counts; leerie defaults to the alphabetically
# first basename, with --pr-template overriding the choice.
_PR_TEMPLATE_MULTI_DIRS = (
    ".github/PULL_REQUEST_TEMPLATE",
    "PULL_REQUEST_TEMPLATE",
    "docs/PULL_REQUEST_TEMPLATE",
)


def find_pr_template(repo_root: Path,
                     override: str | None = None) -> tuple[Path, str] | None:
    """Locate the PR template the worker should fill out, or None when
    the repo has no template.

    Returns `(absolute_path, relative_path_from_repo_root)` so the caller
    can both read the file and report which template was used (the
    relative path goes into run.json under `pr_template_used`).

    Discovery order:
      1. The four single-template locations in
         `_PR_TEMPLATE_SINGLE_LOCATIONS` (GitHub's canonical order).
      2. Any `PULL_REQUEST_TEMPLATE/` directory in
         `_PR_TEMPLATE_MULTI_DIRS`. When `override` matches a basename
         inside one of these directories (with or without `.md`), that
         template wins; otherwise the alphabetically first `.md` wins.

    Case sensitivity: file lookups use the literal paths above
    (lowercase `pull_request_template.md`, uppercase
    `PULL_REQUEST_TEMPLATE/`) — GitHub itself accepts both cases but
    leerie normalizes on the canonical casing rather than scanning every
    case-variant.
    """
    for rel in _PR_TEMPLATE_SINGLE_LOCATIONS:
        candidate = repo_root / rel
        if candidate.is_file():
            return (candidate, rel)
    for rel_dir in _PR_TEMPLATE_MULTI_DIRS:
        d = repo_root / rel_dir
        if not d.is_dir():
            continue
        mds = sorted(p for p in d.iterdir()
                     if p.is_file() and p.suffix == ".md")
        if not mds:
            continue
        if override:
            wanted = override if override.endswith(".md") else f"{override}.md"
            for p in mds:
                if p.name == wanted:
                    return (p, f"{rel_dir}/{p.name}")
            # Override named, no match — fall through to the default rather
            # than die(), since a bad pr_template setting should not block
            # finalize. The fail-open path in _compose_pr_via_llm logs a
            # warning if pr_template was set but didn't resolve.
        return (mds[0], f"{rel_dir}/{mds[0].name}")
    return None


# Byte budgets for the pr_writer payload. The launcher passes the whole
# JSON-encoded payload as a single argv element to `claude -p`; Linux
# ARG_MAX in the leerie container (Debian 12) is ~128 KB. These caps keep
# the largest fields well under that ceiling. The diff sample is line-
# capped instead of byte-capped because individual diff lines can be
# long but the worker reads them as hunks.
PR_WRITER_COMMIT_LOG_MAX_BYTES = 80_000
PR_WRITER_TEMPLATE_MAX_BYTES = 32_000
PR_WRITER_DIFF_SAMPLE_MAX_LINES = 500
# Bound on the `final_conformance` payload field (DESIGN §6 final-tree
# pass paragraph). Defends against a pathological run that produces
# many residuals / many warnings; typical runs are well under this.
# The combined argv-bound payload + system prompt must stay under
# Debian's ~128 KB ARG_MAX.
PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES = 8_000


def _cap_text(s: str, max_bytes: int, label: str) -> tuple[str, bool]:
    """Return (capped_text, was_truncated). Cap `s` at `max_bytes` of
    its UTF-8 encoding without splitting a multi-byte codepoint, then
    append a single-line sentinel marker so the worker sees in-band
    that the field was truncated. `label` names the field in the
    sentinel so the worker can attribute the truncation correctly.
    Empty / short strings pass through unchanged."""
    if not s:
        return (s, False)
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return (s, False)
    # Trim to max_bytes and back off until the trailing bytes form a
    # complete UTF-8 codepoint. errors="ignore" on the final decode
    # makes this defensive against the rare case where the back-off
    # logic still lands inside a continuation.
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    sentinel = (f"\n... [{label} truncated at ~{max_bytes // 1000} KB; "
                "remainder omitted — rely on the commit log] ...")
    return (truncated + sentinel, True)


# Matches "leerie:" at the very start of a string, case-insensitive, with
# any whitespace that follows. Anchored so it can't fire mid-string
# (does not false-positive on "leerietes", "leerie is great", etc.).
_LEERIE_PREFIX_RE = re.compile(r"^leerie:\s*", re.IGNORECASE)


def _strip_leerie_prefix(title: str) -> str:
    """Strip a leading `leerie:` from a worker-emitted PR title so the
    launcher's unconditional `leerie: ` prepend cannot produce
    `leerie: leerie: ...`.

    The pr_writer prompt tells the worker not to emit the prefix, but
    DESIGN §12 *prompts are advisory, code enforces* — a guarantee
    that matters and can be checked mechanically must live in code.
    Without this guard, a single drift produces a user-visible defect
    on every PR until the prompt is patched."""
    return _LEERIE_PREFIX_RE.sub("", title)


def _truncate_diff_sample(diff_text: str, max_lines: int) -> tuple[str, bool]:
    """Return (truncated_text, was_truncated). Splits on newlines and
    keeps the first `max_lines`, appending a sentinel line when truncated
    so the worker can see in-band that the sample is incomplete.

    Line-based (not byte-based) because individual diff lines can be
    long and breaking one mid-line would render the surrounding hunk
    unreadable. Byte budgets for other fields go through `_cap_text`."""
    lines = diff_text.splitlines()
    if len(lines) <= max_lines:
        return (diff_text, False)
    kept = lines[:max_lines]
    kept.append(f"... [diff sample truncated at {max_lines} lines; "
                "remaining hunks omitted — rely on the commit log] ...")
    return ("\n".join(kept), True)


def _base_health_payload(st: "State") -> dict | None:
    """Compact view of the base-tree health baseline (DESIGN §9) for the
    pr_writer payload. Returns None when no baseline was captured (skipped
    or a repo with no BLT commands) so the field is simply absent.

    Reports which build/lint/test axes were RED on the unmodified base
    tree, so the PR body can state whether the assembled change builds and
    passes its tests *relative to the base* — the reviewer's signal for
    whether a red suite is this run's fault or inherited, without having to
    check out the branch. Advisory: informational, never a gate."""
    baseline = (st.data.get("conformance") or {}).get("_baseline")
    if not baseline:
        return None
    axes = baseline.get("axes") or {}
    ran = {a: (axes.get(a) or {}) for a in ("build", "lint", "tests")}
    # An axis is RED only if it ran, was measurable, and failed — same
    # rule as capture_conformance_baseline's red_axes and
    # _format_baseline_section. An unmeasurable axis (runner missing)
    # carries no verdict and must not colour base_status red.
    red = [a for a in ("build", "lint", "tests")
           if ran[a].get("ran") and ran[a].get("measured")
           and not ran[a].get("passed")]
    return {
        "base_status": "red" if red else "green",
        "base_red_axes": red,
        # Per-axis ran/measured/passed so the pr_writer can phrase "net of base".
        "axes": {a: {"ran": bool(ran[a].get("ran")),
                     "measured": bool(ran[a].get("measured")),
                     "passed": ran[a].get("passed")}
                 for a in ("build", "lint", "tests")},
    }


def _record_run_health(st: "State") -> None:
    """Compute and persist run-health signals into `run.json.health`
    (DESIGN §9 / F4). Pure surfacing of data already captured in the
    per-worker JSONL logs — no new instrumentation:

      - `slowest_worker_sid` / `slowest_worker_min`: the worker whose
        summed result `duration_ms` was largest, keyed by log basename
        (the sid). `calls.ndjson` carries `latency_ms` but no sid, so the
        per-worker log is the authoritative source for both.
      - `truncated_worker_count`: worker logs that ended a result with
        `terminal_reason":"max_turns"` — a worker cut off at the turn cap
        (a mis-sized-subtask signal; see the deferred decomposition work).

    Merges into any existing `health` object (e.g. the `base_suite`
    record the baseline wrote) rather than clobbering it. Best-effort:
    malformed log lines are skipped; a missing logs dir is a no-op."""
    logs_dir = st.run_dir / "logs"
    if not logs_dir.is_dir():
        return
    best_sid: str | None = None
    best_ms = 0.0
    truncated = 0
    for lg in sorted(logs_dir.glob("*.log")):
        total_ms = 0.0
        hit_cap = False
        try:
            with lg.open() as f:
                for line in f:
                    # Cheap pre-filter: skip lines that can't be a result
                    # record. Tolerant of both compact (`"type":"result"`,
                    # the live claude -p shape) and spaced JSON.
                    if '"result"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("type") != "result":
                        continue
                    total_ms += rec.get("duration_ms") or 0
                    if rec.get("terminal_reason") == "max_turns":
                        hit_cap = True
        except OSError:
            continue
        if hit_cap:
            truncated += 1
        if total_ms > best_ms:
            best_ms = total_ms
            best_sid = lg.stem
    health = {
        "slowest_worker_sid": best_sid,
        "slowest_worker_min": round(best_ms / 60000.0, 1) if best_ms else 0.0,
        "truncated_worker_count": truncated,
    }
    # Preserve the baseline's base_suite record if present.
    existing = {}
    sidecar = st.run_dir / "run.json"
    if sidecar.exists():
        try:
            existing = (json.loads(sidecar.read_text()) or {}).get("health") or {}
        except (OSError, ValueError):
            existing = {}
    if isinstance(existing, dict) and existing.get("base_suite") is not None:
        health["base_suite"] = existing["base_suite"]
    _write_run_json(st.run_dir, health=health)


def _final_conformance_payload(st: "State") -> dict | None:
    """Compact view of the final-tree conformer pass for the pr_writer
    payload. Returns None when there is nothing advisory to say —
    pass was skipped, crashed without recording, or returned a fully
    clean result with no warnings — so the field is simply absent
    from the payload (the prompt treats absence as "nothing to add").

    The serialized JSON is bounded by
    `PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES`. A pathological run that
    produces many residuals / many warnings would otherwise add an
    unbounded field to a payload already sized close to ARG_MAX."""
    block = (st.data.get("conformance") or {}).get("_final")
    if not block:
        return None
    res = block.get("result") or {}
    residuals = [
        {"rule": (r.get("rule") or "").strip(),
         "why_not_fixed": (r.get("why_not_fixed") or "").strip()}
        for r in (res.get("rule_violations_residual") or [])
        if (r.get("rule") or "").strip()
    ]
    failed_axes: list[dict] = []
    for axis in ("build", "lint", "tests"):
        a = res.get(axis) or {}
        if a.get("ran") and not a.get("passed"):
            failed_axes.append({
                "axis": axis,
                "command": (a.get("command") or "").strip(),
                "summary": (a.get("summary") or "").strip() or "(no summary)",
            })
    warnings = [w for w in (block.get("warnings") or []) if w]
    if not residuals and not failed_axes and not warnings:
        return None
    out = {"residuals": residuals,
           "failed_axes": failed_axes,
           "warnings": warnings}
    # Truncation: failed_axes is bounded by 3 (build/lint/tests) and
    # tiny; residuals and warnings are the only fields that can grow.
    # Trim each list from the tail until the JSON byte length fits;
    # leave at least one element each so the worker still sees that
    # there *was* drift. Append a `truncated` marker so the prompt's
    # advisory section can mention it honestly.
    cap = PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES
    if len(json.dumps(out, separators=(",", ":")).encode("utf-8")) <= cap:
        return out
    truncated = False
    while (len(json.dumps(out, separators=(",", ":")).encode("utf-8")) > cap
           and (len(out["residuals"]) > 1 or len(out["warnings"]) > 1)):
        if len(out["warnings"]) > 1:
            out["warnings"].pop()
            truncated = True
        elif len(out["residuals"]) > 1:
            out["residuals"].pop()
            truncated = True
    if truncated:
        out["truncated"] = True
    return out


async def _compose_pr_via_llm(st: "State",
                              caps: dict,
                              models: dict[str, str],
                              efforts: dict[str, str | None],
                              repo_root: Path,
                              pr_template_override: str | None) -> None:
    """Run the pr_writer worker and persist its title/body to run.json.

    DESIGN §6 *Finalization*: the worker runs *inside* the orchestrator
    container (where `claude -p` is available) and writes its output to
    run.json — the existing container→host handoff channel. The host
    launcher then reads `pr_title` and `pr_body` from run.json and
    passes them to `gh pr create`.

    **Fail-open contract**: any error (subprocess failure, schema
    mismatch, timeout, git errors collecting context) is logged as a
    warning and swallowed. The launcher's bash fallback composition
    runs in that case, so a PR will still open — generating a richer
    body must never block finalize success.
    """
    try:
        # 1. Locate the template (may be None).
        tpl = find_pr_template(repo_root, pr_template_override)
        tpl_content = ""
        tpl_rel: str | None = None
        tpl_truncated = False
        if tpl is not None:
            tpl_path, tpl_rel = tpl
            try:
                raw = tpl_path.read_text()
                tpl_content, tpl_truncated = _cap_text(
                    raw, PR_WRITER_TEMPLATE_MAX_BYTES, "PR template")
            except OSError as e:
                log(f"pr_writer: failed to read template {tpl_rel}: {e} "
                    "(falling back to no-template mode)")
                tpl = None
                tpl_rel = None
        if pr_template_override and tpl_rel is None:
            log(f"pr_writer: --pr-template={pr_template_override!r} did "
                f"not match any template; using default discovery")

        # 2. Collect git context. Commits are the spine; diff is sampled.
        working_branch = st.data.get("working_branch") or "HEAD"
        run_branch = compute_run_branch(st.run_id)
        rev_range = f"{working_branch}..{run_branch}"

        async def _git(args: list[str]) -> str:
            # start_new_session=True per DESIGN §6 "Worker subtree
            # termination" — every subprocess in this module isolates
            # into its own POSIX session so cleanup can killpg without
            # signalling the orchestrator's own group. Static-enforced
            # by tests/test_signal_cleanup.py.
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(repo_root), *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            out, _err = await proc.communicate()
            return out.decode("utf-8", errors="replace")

        commit_log_raw = await _git([
            "log", "--no-merges", "--format=%h %s%n%b%n---", rev_range])
        commit_log, commit_log_truncated = _cap_text(
            commit_log_raw, PR_WRITER_COMMIT_LOG_MAX_BYTES, "commit log")
        diff_stat = await _git(["diff", "--stat", rev_range])
        dirstat = await _git(["diff", "--dirstat=files,5", rev_range])
        # Sample diff: cap at ~500 lines. A full diff of a 32-file run
        # easily blows the prompt budget; the commit log is the canonical
        # record. We pull the head of `git diff` (deterministic order:
        # alphabetical by path) so the sample at least covers some
        # concrete hunks instead of being a no-op stat.
        full_diff = await _git(["diff", rev_range])
        diff_sample, diff_truncated = _truncate_diff_sample(
            full_diff, PR_WRITER_DIFF_SAMPLE_MAX_LINES)

        # 3. Pull planner-written subtask titles from plan.json. The
        # planner writes its full plan there (write_plan above), and the
        # titles are the cleanest human-readable summary of each subtask's
        # intent. Empty list if plan.json is missing or malformed.
        subtask_titles: list[str] = []
        try:
            plan = json.loads(
                (st.run_dir / "plan.json").read_text())
            for sid, spec in (plan.get("subtasks") or {}).items():
                title = (spec or {}).get("title")
                if title:
                    subtask_titles.append(title)
        except (OSError, json.JSONDecodeError):
            pass

        categories = st.data.get("categories") or []
        answers = st.data.get("answers") or {}

        payload = {
            "task": st.data.get("task", ""),
            "categories": categories,
            "source_of_truth": answers.get("source_of_truth"),
            "working_branch": working_branch,
            "run_branch": run_branch,
            "wave_count": len(st.data.get("waves") or []),
            "subtask_count": sum(
                len(w) for w in (st.data.get("waves") or [])),
            "worker_count": st.data.get("worker_count"),
            "elapsed_time": _format_run_duration(
                st.data.get("started_at"), st.data.get("finished_at")),
            "leerie_version": st.data.get("leerie_version"),
            "subtask_titles": subtask_titles,
            "template": (
                {"path": tpl_rel, "content": tpl_content,
                 "truncated": tpl_truncated}
                if tpl_rel else None
            ),
            "commit_log": commit_log,
            "commit_log_truncated": commit_log_truncated,
            "diff_stat": diff_stat,
            "dirstat": dirstat,
            "diff_sample": diff_sample,
            "diff_sample_truncated": diff_truncated,
        }
        fc = _final_conformance_payload(st)
        if fc is not None:
            payload["final_conformance"] = fc
        bh = _base_health_payload(st)
        if bh is not None:
            payload["base_health"] = bh
        preconditions = st.data.get("external_preconditions") or []
        if preconditions:
            payload["external_preconditions"] = preconditions

        sys_prompt = load_prompt("pr_writer")
        model = models.get("pr_writer", MODEL_DEFAULT_PER_WORKER.get(
            "pr_writer", MODEL_DEFAULT))
        effort = efforts.get("pr_writer")
        # Pre-check the worker budget. If the run already saturated
        # max_total_workers in execution, bump_workers() would raise
        # WorkerError — which the fail-open except below would catch
        # and log as "composition failed (WorkerError: ...)", which is
        # misleading (the worker never ran; the budget said no). Skip
        # cleanly and let the launcher's deterministic fallback run.
        wc = st.data.get("worker_count", 0)
        if wc >= caps["max_total_workers"]:
            log(f"pr_writer: skipped (worker budget exhausted at "
                f"{wc}/{caps['max_total_workers']}); deterministic "
                "fallback will run")
            return
        st.bump_workers(caps)
        result = await claude_p(
            user_prompt=json.dumps(payload, separators=(",", ":")),
            system_prompt=sys_prompt,
            schema_key="pr_writer",
            cwd=str(repo_root),
            allowed_tools=INSPECT_TOOLS,
            max_turns=20,
            autonomous=False,
            caps=caps,
            st=st,
            model=model,
            effort=effort,
            sid="pr-writer",
        )

        # Strip whitespace, then strip any leading `leerie:` the worker
        # may have emitted despite the prompt telling it not to —
        # DESIGN §12 *prompts are advisory, code enforces*. The
        # launcher unconditionally prepends `leerie: `, so leaving a
        # worker-emitted prefix in place would render `leerie: leerie: …`.
        title = _strip_leerie_prefix((result.get("title") or "").strip()).strip()
        body = (result.get("body") or "").strip()
        if not title or not body:
            log("pr_writer: worker returned empty title or body; "
                "launcher will use deterministic fallback")
            return
        used = result.get("used_template")
        _write_run_json(
            st.run_dir,
            pr_title=title,
            pr_body=body,
            pr_template_used=used,
        )
        log(f"pr_writer: composed PR via {model}"
            + (f" (filled template {used})" if used else ""))
    except Exception as e:
        # Fail-open: any failure means the launcher uses its bash
        # fallback. Surface enough to debug but never raise.
        log(f"pr_writer: composition failed ({type(e).__name__}: {e}); "
            "launcher will use deterministic fallback")


async def phase_finalize(leerie_dir: Path, st: State, no_push: bool,
                         no_verify: bool,
                         caps: dict | None = None,
                         models: dict[str, str] | None = None,
                         efforts: dict[str, str | None] | None = None,
                         pr_template_override: str | None = None,
                         host_no_push: bool | None = None) -> None:
    """Phase 6: verify the run branch and record finalize state.

    The push + PR step has moved to the host launcher (DESIGN §6
    *Finalization*); this phase no longer makes network calls. It runs
    `finalize.sh` to verify the run branch is non-empty, runs
    `cleanup.sh` to drop subtask branches, writes `finished_at` to
    state.json + run.json, and exits. The launcher polls run.json's
    `finished_at` sentinel and does `git push` + `gh pr create` on the
    host using the host's own auth state.

    `no_verify` is passed through into the run.json sidecar so the
    launcher knows whether to add `--no-verify` to its `git push`.

    `pr_writer` runs after `finished_at` is recorded when
    `push_will_happen(no_push, host_no_push)` is True AND the caller
    threaded caps/models/efforts through. Gating on **intent** (not
    the `no_push` mechanism flag) matters on Fly: the launcher always
    passes `--no-push` to the in-Machine orchestrator (it can't
    reach origin), but the user may still want the PR. `host_no_push`
    is None on local runtime and falls back to `not no_push` for the
    same condition. Output lands in run.json's `pr_title` / `pr_body`
    / `pr_template_used` fields; `host_finalize` reads these and falls
    back to the deterministic `compose_pr_body` shape if they are
    missing.

    `run.json.no_push` is written from **intent**
    (`not push_will_happen(...)`), not the mechanism flag, so
    `host_finalize`'s skip check honors the user's preference cleanly
    on both runtimes.
    """
    log("phase 6: finalizing")
    # Completion gate (DESIGN §6 *finished_at is a discovery sentinel, not a
    # completion signal*). The normal wave loop only reaches finalize after
    # every wave integrates, but a stray finalize-only invocation (or a
    # future control-flow bug) must never push a partial run branch or open
    # a premature PR. Refuse if the waves are not all integrated. The
    # cleared-but-empty terminal state routes through _finish_no_work_run,
    # not here, so waves==[] never reaches this guard.
    waves = st.data.get("waves", [])
    completed = st.data.get("completed_waves", 0)
    if isinstance(waves, list) and completed < len(waves):
        die(f"refusing to finalize: only {completed} of {len(waves)} waves "
            f"complete. Resume to finish: leerie --resume {st.run_id}")
    st.data["current_phase"] = "phase 6: finalize"
    st.save()
    proc = await run_script("finalize.sh", st.run_id)
    if proc.returncode != 0:
        die(f"finalize failed (run branch is intact): {proc.stderr.strip()}")
    await run_script("cleanup.sh", "--run-id", st.run_id, "--subtask-branches")
    # Post-cleanup verification: the run branch must survive cleanup.
    # finalize.sh verified it existed moments ago; if it's gone after
    # cleanup.sh, something deleted it (a concurrent process, a git
    # corruption, or a cleanup.sh bug). die() here routes to the pause
    # branch via decide_teardown, preserving the machine for recovery.
    branch_ref = f"refs/heads/leerie/runs/{st.run_id}"
    verify = await run_proc(
        ["git", "show-ref", "--verify", "--quiet", branch_ref])
    if verify.returncode != 0:
        die(f"CRITICAL: run branch leerie/runs/{st.run_id} disappeared "
            f"after cleanup — branch existed before cleanup (finalize.sh "
            f"passed) but is gone after")

    wc = st.data.get("worker_count", 0)
    nsub = len(st.data.get("subtask_status", {}))
    tel = st.data.get("telemetry", {})
    st.data["finished_at"] = now()
    st.save()
    will_push = push_will_happen(no_push, host_no_push)
    # Record finalize success in the run.json sidecar. The launcher
    # uses `finished_at` as the "ready for push" sentinel. `no_push`
    # here is **intent** — host_finalize reads it as "the user opted
    # out of pushing" and short-circuits accordingly. This is the
    # critical distinction from the orchestrator's --no-push argv
    # flag, which on Fly is a mechanism flag (the Machine can't
    # push). See push_will_happen / DESIGN §6.
    _write_run_json(
        st.run_dir,
        finished_at=st.data["finished_at"],
        no_push=not will_push,
        no_verify=no_verify,
    )

    # Capture-and-bake hook (DESIGN §6½). Non-fatal: any error is
    # swallowed so capture failure never blocks a run from completing.
    try:
        await capture_repo_deps(
            Path(os.getcwd()), st,
            caps=caps, models=models, efforts=efforts,
        )
    except Exception as _cap_exc:
        log(f"capture: non-fatal error during dep capture ({_cap_exc}); "
            "continuing")

    # Run-health surfacing (DESIGN §9 / F4): fold the slowest worker and
    # the turn-cap-truncation count into run.json.health, merging with any
    # base_suite record the baseline already wrote. Pure surfacing of
    # data already captured in the per-worker logs; never gates, never
    # raises (best-effort — a bad log line must not block finalize).
    try:
        _record_run_health(st)
    except Exception as _h_exc:
        log(f"run-health: non-fatal error ({_h_exc}); continuing")

    # LLM-composed PR title/body. Runs only when push will happen and
    # the caller threaded models/efforts/caps through. Fail-open: any
    # error is swallowed and the launcher uses its bash fallback.
    if will_push and caps is not None and models is not None and efforts is not None:
        await _compose_pr_via_llm(
            st, caps, models, efforts,
            repo_root=Path(os.getcwd()),
            pr_template_override=pr_template_override,
        )

    if not will_push:
        log(f"skipped push and PR (--no-push); the run branch "
            f"{compute_run_branch(st.run_id)} is local-only; "
            "your working branch is unchanged")
    else:
        log(f"work is on {compute_run_branch(st.run_id)}; the host "
            "launcher will push and open the PR after this container exits")

    pr_url = None  # the launcher writes pr_url to run.json after gh pr create
    pr_suffix = ""
    log(f"done — {nsub} subtasks, {len(st.data['waves'])} waves, "
        f"{wc} worker invocations.{pr_suffix} Work is on "
        f"{compute_run_branch(st.run_id)}; working branch unchanged.")
    if tel:
        log(f"run weight: {tel.get('calls', 0)} claude -p calls, "
            f"${tel.get('cost_usd', 0.0):,.2f}, "
            f"{tel.get('input_tokens', 0):,} in / "
            f"{tel.get('output_tokens', 0):,} out tokens "
            f"(see {st.path})")


# =========================================================================
# entry point
# =========================================================================
async def orchestrate(args, caps: dict, leerie_dir: Path, st: State,
                      sot_pref: str, verbosity: str,
                      models: dict[str, str],
                      efforts: dict[str, str | None]) -> None:
    """The async portion of a run: every phase that spawns a `claude -p`
    worker. main() handles sync setup, then drives this with `asyncio.run`."""
    # Memory telemetry: a long-running coroutine that snapshots RSS / phase /
    # worker count / open FDs / thread count into memory.ndjson every 30s
    # so we can distinguish "natural heavy run" from "real orchestrator leak"
    # after the fact. Lifecycle is bounded by this function — cancelled in
    # the finally so it never outlives the run.
    sampler_task = asyncio.create_task(_memory_sampler(st))
    # Zombie reaper: `_become_subreaper()` (called in main()) routes orphaned
    # worker descendants to us; this task `wait()`s them so they don't rot as
    # zombies against the worker cgroup's pids.max (DESIGN §6 *Zombie reaping*).
    # Same lifecycle as the sampler — cancelled in the finally so it never
    # outlives the run.
    reaper_task = asyncio.create_task(_zombie_reaper())
    try:
        await _run_phases(args, caps, leerie_dir, st, sot_pref, verbosity,
                          models, efforts)
    finally:
        sampler_task.cancel()
        reaper_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sampler_task
        with contextlib.suppress(asyncio.CancelledError):
            await reaper_task


async def _run_phases(args, caps: dict, leerie_dir: Path, st: State,
                      sot_pref: str, verbosity: str,
                      models: dict[str, str],
                      efforts: dict[str, str | None]) -> None:
    """The phase sequence of one run. Split out from `orchestrate()`
    so the latter can wrap it with the memory-sampler try/finally
    without burying the phase calls behind extra indentation. Source-
    text coupling tests for the orchestrate call-sites parse this
    function's body — keep all phase calls here."""
    if args.resume:
        if not st.load():
            die(f"nothing to resume — no state.json at {st.path}")
        validate_resume_state(st.data)
        task = st.data["task"]
        log(f"resuming: {task!r} (worker count {st.data.get('worker_count', 0)})")
        log(f"per-worker logs: {st.run_dir / 'logs'}/")
        # A successfully finalized run must not re-execute phases 4→5→6.
        # Without this guard, `--resume` on a completed run re-runs
        # setup-run.sh + finalize.sh + cleanup.sh, creating a window
        # where a concurrent decide_teardown (from the prior exit's
        # launcher child) can race with the second orchestrator and
        # destroy the machine mid-cycle.
        # Guard: finished_at + current_phase == finalize. The die()
        # handler also sets finished_at (for fetch_branch discovery)
        # but leaves current_phase at whatever phase died — those runs
        # ARE resumable and must fall through.
        if (st.data.get("finished_at")
                and st.data.get("current_phase") == "phase 6: finalize"
                and not st.data.get("no_work_required")):
            log(f"run already completed at {st.data['finished_at']} — "
                "nothing to resume (host launcher will push + open PR)")
            return
        # A no-work run (DESIGN §8 *The cleared-but-empty terminal state*)
        # is already complete — finished_at is set, no run branch was
        # materialized, no commits exist. Falling through to phase_execute
        # would call setup-run.sh (creating a fresh empty branch), iterate
        # zero waves, then finalize.sh would fail its non-empty-branch
        # check and abort with "nothing to finalize". Short-circuit here.
        if st.data.get("no_work_required"):
            log(f"run already completed with no work required at "
                f"{st.data.get('finished_at', '<unknown>')} — nothing to "
                "resume")
            return
        if "waves" not in st.data:
            phase = st.data.get("current_phase", "")
            if phase == "phase 3: scheduling":
                # Budget-feasibility check fired after schedule() but before
                # write_plan() — plans are not persisted, so this run cannot
                # be resumed. User must re-run fresh with a higher budget.
                die(
                    "cannot resume — run stopped at the budget-feasibility "
                    "check before any work was scheduled. Plans are not "
                    "persisted. Re-run (without --resume) with "
                    "--skip-budget-check or a higher --max-workers."
                )
            die("cannot resume — run did not reach the scheduling phase")
        # Refresh the preferences in case env vars or leerie.toml
        # changed since the original run started. Verbosity is
        # resolved fresh every run — the user can dial up or down on
        # resume without editing state.json.
        st.data["source_of_truth_pref"] = sot_pref
        st.data["verbosity"] = verbosity
        st.data["inspect_dirs"] = list(getattr(args, "inspect_dirs", []) or [])
        st.data["clarify"] = bool(args.clarify)
        st.data["dangerously_skip_permissions"] = bool(
            args.dangerously_skip_permissions)
        st.data["skip_overlap_judge"] = bool(args.skip_overlap_judge)
        st.data["skip_satisfied_check"] = bool(
            getattr(args, "skip_satisfied_check", False))
        st.data["skip_budget_check"] = bool(args.skip_budget_check)
        st.data["strict_conformer"] = bool(args.strict_conformer)
        st.data["skip_base_baseline"] = bool(args.skip_base_baseline)
        st.data["skip_repo_map"] = bool(args.skip_repo_map)
        st.data["leerie_version"] = _read_version()
        st.save()
        # Fail-closed containment gate + recording, now that st.data is
        # loaded and this resume is past the completed/no-work short-
        # circuits (i.e. it WILL spawn workers). Merges into the resumed
        # state rather than clobbering it.
        enforce_and_record_cgroup_containment(
            st, args.dangerously_allow_uncapped)
        # Absorb --answers on resume too. The documented user flow for
        # a non-interactive deferred-question exit (Phase-1 or §11
        # mid-execution) is: get a pending-*.json, write an answers
        # file, re-run with --resume --answers <file>. Without this
        # call the answers file was silently dropped — the re-spawned
        # worker would re-ask the same question forever. See P5-1.
        absorb_supplied_answers(args, st, leerie_dir)
        # Re-export the mise override env var if the original run
        # synthesized one. phase_provision (which set it on os.environ
        # the first time) is skipped on resume, but downstream
        # implementer/conformer subprocesses still need it to find the
        # synthesized go pin.
        override = (st.data.get("provision") or {}).get("override_file")
        if override:
            os.environ["MISE_OVERRIDE_CONFIG_FILENAMES"] = str(override)
    else:
        if not args.task:
            die("a task description is required (or use --resume)")
        task = resolve_task_argument(args.task)
        st.data = {"task": task, "started_at": now(), "worker_count": 0,
                   "source_of_truth_pref": sot_pref,
                   "verbosity": verbosity,
                   "inspect_dirs": list(getattr(args, "inspect_dirs", []) or []),
                   "clarify": bool(args.clarify),
                   "dangerously_skip_permissions": bool(
                       args.dangerously_skip_permissions),
                   "skip_overlap_judge": bool(args.skip_overlap_judge),
                   "skip_satisfied_check": bool(
                       getattr(args, "skip_satisfied_check", False)),
                   "skip_budget_check": bool(args.skip_budget_check),
                   "strict_conformer": bool(args.strict_conformer),
                   "skip_base_baseline": bool(args.skip_base_baseline),
                   "skip_repo_map": bool(args.skip_repo_map),
                   "leerie_version": _read_version()}
        st.save()
        # Fail-closed containment gate + recording, before the first
        # worker (phase_classify below). Must come after the
        # `st.data = {...}` seed above, which would otherwise discard the
        # recorded key.
        enforce_and_record_cgroup_containment(
            st, args.dangerously_allow_uncapped)
        await preflight(leerie_dir, verbosity=verbosity,
                        skip_smoke=args.skip_smoke,
                        no_push=getattr(args, "no_push", False))
        supplied = (json.loads(Path(args.answers).read_text())
                    if args.answers else None)
        # Run-start backstop (DESIGN §6½): before phase_classify, scan prior
        # runs that produced logs but whose dep_capture.done sentinel is absent.
        # Covers the SIGKILL / crash case where the cancel-arm capture couldn't
        # run. Non-fatal: any error is logged and ignored.
        await _backstop_capture_prior_runs(
            leerie_dir, Path(os.getcwd()), caps, models, efforts)
        await phase_classify(task, st, caps, args.clarify, models, efforts)
        log(f"run id: {st.run_id}")
        # Initialize run.json with the immutable run-identity fields
        # (run_id, branch, working_branch, started_at, task) so
        # `leerie --list` can enumerate this run from the moment
        # it has a stable identity — not only after finalize.
        head_proc = await run_proc(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"])
        working_branch = (head_proc.stdout.strip()
                          if head_proc.returncode == 0 else "")
        st.data["working_branch"] = working_branch
        st.save()
        _write_run_json(
            st.run_dir,
            run_id=st.run_id,
            branch=compute_run_branch(st.run_id),
            working_branch=working_branch,
            started_at=st.data["started_at"],
            task=task,
            **({"group_id": args.group_id} if args.group_id else {}),
        )
        # Provision per-repo deps (DESIGN §6½). Runs after classify (so a
        # docs-only run can short-circuit). On `--resume` the
        # entire else-branch is skipped, so phase_provision never re-fires.
        await phase_provision(Path(os.getcwd()), st, caps, models, efforts)
        # gather_answers blocks on input(). That's fine here: no concurrent
        # tasks are scheduled yet, so blocking the loop blocks nothing. Kept
        # on the event loop deliberately — every State mutation runs on the
        # loop, which is why the lock-free State works.
        gather_answers(st, supplied)
        plans = await phase_plan(task, st, caps, models, efforts)
        # Bridge cross-domain capability-tag mismatches before the
        # scheduler builds its DAG. Short-circuits with no worker call
        # when planners agreed on vocabulary (the common case).
        plans = await phase_reconcile(plans, task, st, caps, models, efforts)
        # Cleared-but-empty terminal state (DESIGN §8): every planner
        # cleared its gate and confirmed the task is already satisfied
        # on HEAD. Nothing to schedule, nothing to execute, no run
        # branch to push. _finish_no_work_run records the outcome and
        # we return — skipping phase_execute (which would call
        # setup-run.sh) and phase_finalize (which would try to push a
        # non-existent branch). Runs after phase_reconcile so a planner
        # that emits an empty plan but the reconciler later adds
        # subtasks is not misclassified as no-work.
        no_work_map = detect_no_work(plans)
        if no_work_map is not None:
            _finish_no_work_run(st, no_work_map)
            return
        # Phase 2¾: detect cross-planner surface-overlap collisions
        # (DESIGN §5 *Cross-domain surface overlap*). Two planners can
        # independently produce subtasks for the same exported artifact
        # with incompatible APIs; the reconciler doesn't look for this
        # (its mandate is `requires`-tag vocabulary drift). The judge
        # short-circuits on single-planner / <2-subtask runs; on multi-
        # planner runs it emits zero or more collisions resolved as
        # merge / drop_* (applied mechanically) or unresolvable (die at
        # plan time, strictly better than the integrator design-
        # conflict crash this exists to prevent).
        plans = await phase_overlap_judge(
            plans, task, st, caps, models, efforts)
        # Surface cross-planner file-claim overlaps. Warning only — the
        # reconciler handles capability-tag drift but not file-claim
        # conflicts (yet); empirically these correlate strongly with
        # integrator design-conflict crashes downstream.
        warn_cross_planner_file_overlap(plans)
        warn_layer_gaps(plans)
        # Drop subtasks whose files_likely_touched leak into inspect-dir
        # mounts (read-only) or other off-tree paths. Soft drop so the
        # surviving subtasks proceed; the drop is recorded in
        # state.data["dropped_subtasks"] for audit. Must run BEFORE
        # schedule() so the resulting waves do not reference dropped sids.
        filter_offtree_subtasks(plans, Path(os.getcwd()),
                                st.data.get("inspect_dirs") or [], st)
        # Already-satisfied subtask elimination (DESIGN §8). Per-subtask
        # probe of the base tree; drops subtasks already met (e.g. a
        # sibling run merged the deliverable to the base). Soft drop,
        # recorded in state.data["dropped_subtasks"]. If it empties every
        # ready plan, route to the same cleared-but-empty terminal state
        # as detect_no_work — using the drop-derived no_work_map, not the
        # planner's original confidence.basis. Must run BEFORE schedule().
        satisfied_no_work = await filter_satisfied_subtasks(
            plans, Path(os.getcwd()), st, caps, models, efforts)
        if satisfied_no_work is not None:
            _finish_no_work_run(st, satisfied_no_work)
            return
        st.data["current_phase"] = "phase 3: scheduling"
        st.save()
        subtasks, waves = schedule(plans)
        # Budget-feasibility preflight (DESIGN §13 *Budget feasibility —
        # fail fast at the cheapest moment*). Runs after schedule() so we
        # have the real wave count, before validate_plan / write_plan so
        # no plan.json or subtask spec files get written for a run that
        # is mathematically unwinnable. die()s with EXIT_BUDGET_INFEASIBLE
        # and a recommended --max-workers; opt-out via --skip-budget-check.
        check_budget_feasibility(st, caps, subtasks, waves)
        validate_plan(subtasks)
        write_plan(leerie_dir, task, st, subtasks, waves)

    await phase_execute(leerie_dir, st, caps, models, efforts)
    # Final-tree conformance pass on the integrated staging worktree
    # (DESIGN §6 *Worktree and integration model*, final-tree pass).
    # Advisory — never raises; failure modes surface in
    # st.data["conformance"]["_final"].
    try:
        await run_final_conformance(leerie_dir, st, caps, models, efforts)
    except Exception as e:
        # Defense-in-depth: run_final_conformance is documented to
        # never raise, but a bug in its glue (e.g. a future state
        # mutation that throws) must not block phase_finalize. Record
        # and move on.
        log(f"final conformance phase raised {type(e).__name__}: {e} — "
            "surfaced as advisory, finalize proceeds")
        st.data.setdefault("conformance", {}).setdefault(
            "_final", {"result": None, "warnings": []}
        )["warnings"].append(
            f"orchestrator-side exception: {type(e).__name__}: {e}")
        st.save()
    await phase_finalize(leerie_dir, st,
                        no_push=getattr(args, "no_push", False),
                        no_verify=getattr(args, "no_verify", False),
                        caps=caps, models=models, efforts=efforts,
                        pr_template_override=getattr(
                            args, "pr_template", None),
                        host_no_push=getattr(args, "host_no_push", None))


def main() -> None:
    # Install the child-subreaper role first thing, before any worker (or any
    # subprocess this process spawns) exists, so every descendant inherits the
    # reparent-to-us behavior. This is what makes `_zombie_reaper` able to reap
    # orphaned git/ssh-agent subprocesses that would otherwise pile up as
    # zombies against the worker cgroup's pids.max (DESIGN §6 *Zombie reaping*).
    _become_subreaper()
    ap = argparse.ArgumentParser(
        prog="leerie", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
launcher verbs (handled before the container starts):
  --stop <run-id>       pause a remote Fly machine (resumable via --resume)
  --kill <run-id>       destroy a remote machine permanently (--force skips prompt)
  --finalize <run-id>   post-detach finalization (--force/--no-verify/--no-push)
  --re-seed <run-id>    mid-run host-to-machine re-rsync (--force bypasses safety)
  --shell               drop into bash on resume instead of tailing the log
  --auto-finalize       auto-finalize on clean orchestrator exit during resume
  --no-re-seed          skip auto-reseed on resume
  --fly-disk-gb N       Fly volume size in GB (default 8 on --runtime fly)
  --state-dir PATH      override per-repo state directory
  --no-runtime-install  skip container runtime auto-install
  --no-auto-publish     skip image publish probe
  --local-build         force local image build (not Fly remote builder)

chain verbs (launcher fast-paths — no container started):
  --chain-submit        submit a multi-run chain (--wave <files> ...)
  --chain-status <id>   print a chain snapshot
  --list-chains         list all chains
  --chain-kill <id>     cancel a chain and destroy its machines
  --chain-attach <id>   fetch a chain's event log

See README.md "Launcher verbs" for full details and sub-flags.""")
    ap.add_argument("--version", action="version",
                    version=f"leerie {_read_version()}",
                    help="print the leerie version and exit")
    ap.add_argument("task", nargs="?",
                    help="the task to execute (literal string, or path to "
                         "a .txt/.md file whose contents are the task)")
    ap.add_argument("--resume", action="store_true",
                    help="resume an interrupted run (auto-picks if exactly "
                         "one run exists under <state-root>/runs/). "
                         "Default: off (start a new run)")
    ap.add_argument("--run-id", metavar="ID",
                    help="select a specific run by id (for --resume when "
                         "multiple runs are in flight). See `--list` to "
                         "enumerate.")
    ap.add_argument("--list", action="store_true", dest="list_runs",
                    help="enumerate in-flight and completed runs in this "
                         "repository (run id, started, status, branch). "
                         "Exits without running orchestrate. Default: off")
    ap.add_argument("--status", metavar="STATE", dest="status_filter",
                    choices=RUN_STATUSES,
                    help=f"with --list, restrict the table to runs whose "
                         f"derived status matches STATE. One of: "
                         f"{', '.join(RUN_STATUSES)}.")
    ap.add_argument("--report", metavar="RUN_ID", nargs="?",
                    const="", dest="report",
                    help="print a telemetry report for a run: per-call-type "
                         "token/cost/latency/failure breakdown plus memory "
                         "peak. Pass a run id, or omit it to auto-pick when "
                         "exactly one run exists. Exits without running "
                         "orchestrate. See `--list` to enumerate.")
    ap.add_argument("--answers", metavar="FILE",
                    help="JSON file of pre-supplied clarification answers")
    ap.add_argument("--clarify", action="store_true",
                    help="opt into surfacing intent questions to the user "
                         "(DESIGN §11). Without this flag (the default), the "
                         "classifier's filter still runs but surviving "
                         "questions are dropped and the implementer makes a "
                         f"best-effort decision. Also {CLARIFY_ENV} env var "
                         "or clarify=true in leerie.toml.")
    ap.add_argument("--no-push", action="store_true",
                    help="skip the push and PR step at finalize. The run "
                         "completes with the run branch local-only; your "
                         "working branch is unchanged. Default: off (push "
                         f"and PR happen). Also {NO_PUSH_ENV} env var or "
                         "no_push in leerie.toml.")
    # Hidden: the Fly launcher uses this to communicate the user's
    # launch-time push intent into the in-Fly orchestrator. The
    # orchestrator's --no-push is a mechanism flag on Fly (the Machine
    # can't reach origin); --host-no-push carries intent. None (unset
    # on the local-runtime path) means "no separate intent channel,
    # --no-push is intent." See DESIGN §6 *Finalization*.
    ap.add_argument("--host-no-push", choices=["true", "false"],
                    default=None, help=argparse.SUPPRESS)
    ap.add_argument("--no-verify", action="store_true",
                    help="pass --no-verify to the finalize `git push` "
                         "(skips pre-push hooks). Worker commits inside "
                         "worktrees still run all hooks. Default: off "
                         "(hooks run). CLI flag only (no env/TOML mirror — "
                         "matches CLAUDE.md's explicit-user-request "
                         "principle for hook-skipping).")
    ap.add_argument("--pr-template", metavar="NAME",
                    help="when the target repo has multiple PR templates "
                         "in PULL_REQUEST_TEMPLATE/, pick this one by "
                         "basename (with or without .md). No effect "
                         "when the repo has a single top-level template "
                         "or none at all. Default: alphabetically first "
                         ".md. Also "
                         f"{PR_TEMPLATE_ENV} env var or pr_template in "
                         "leerie.toml.")
    ap.add_argument("--dangerously-skip-permissions", action="store_true",
                    help="DANGEROUS: pass --dangerously-skip-permissions "
                         "to EVERY claude -p worker — including the "
                         "judgment workers (classifier, planner, "
                         "reconciler, plan_overlap_judge, provision) "
                         "that run in the real "
                         "repo cwd, not an isolated worktree. Waives "
                         "the DESIGN §12 mechanical enforcement that "
                         "they stay read-only. Use only on repos you "
                         "would run `claude --dangerously-skip-permissions` "
                         "against directly. Default: off (judgment "
                         "workers narrow-allowlisted). Also "
                         f"{DANGEROUS_SKIP_PERMS_ENV} env var or "
                         "dangerously_skip_permissions=true in leerie.toml.")
    ap.add_argument("--dangerously-allow-uncapped", action="store_true",
                    help="DANGEROUS: continue even if worker cgroup "
                         "containment could not be enabled (cgroup broker "
                         "down, no usable cgroup hierarchy — neither a v2 "
                         "unified mount nor v1 pids+memory controller mounts "
                         "— read-only cgroupfs, or a rootless host whose "
                         "systemd doesn't delegate pids+memory into the "
                         "per-session user slice). Default is to die() "
                         "before the first worker — silently-uncapped workers "
                         "can exhaust the VM thread/PID table (DESIGN §6 "
                         "Memory containment). This downgrades that fatal gate "
                         "to a loud warning and runs workers uncapped. Also "
                         f"{DANGEROUS_ALLOW_UNCAPPED_ENV} env var or "
                         "dangerously_allow_uncapped=true in leerie.toml.")
    ap.add_argument("--max-workers", type=_positive_int, metavar="N",
                    help=f"total worker-invocation budget "
                         f"(default {DEFAULT_CAPS['max_total_workers']}); "
                         f"also {MAX_WORKERS_ENV} and max_workers in "
                         "leerie.toml")
    ap.add_argument("--max-parallel", type=_positive_int, metavar="N",
                    help=f"concurrent workers per wave "
                         f"(default {DEFAULT_CAPS['max_parallel']}); "
                         f"also {MAX_PARALLEL_ENV} and max_parallel in "
                         "leerie.toml")
    ap.add_argument("--confidence-rounds", type=_positive_int, metavar="N",
                    help=f"how many evidence-gate rounds each planner / "
                         f"implementer may run before exiting blocked "
                         f"(default {DEFAULT_CAPS['confidence_rounds']}); "
                         f"also {CONFIDENCE_ROUNDS_ENV} and "
                         f"confidence_rounds in leerie.toml")
    ap.add_argument("--planner-samples", type=_positive_int, metavar="N",
                    help=f"independent planner invocations per domain; "
                         f"the cleanest sample is selected mechanically "
                         f"(default {DEFAULT_CAPS['planner_samples']}; "
                         f"set to 2–3 for recall). Also "
                         f"{PLANNER_SAMPLES_ENV} and "
                         "planner_samples in leerie.toml")
    ap.add_argument("--worker-memory-max", metavar="SIZE",
                    help="per-worker cgroup memory cap (e.g. '4G', "
                         "'512M', '1024'). Bounds RAM available to each "
                         "claude -p worker subtree; an OOM stays inside "
                         "the worker cgroup rather than cascading to "
                         "sshd / orchestrator. Auto-derived from "
                         "/proc/meminfo when unset. Also "
                         f"{WORKER_MEMORY_MAX_ENV} env var or "
                         "worker_memory_max in leerie.toml")
    ap.add_argument("--worker-pids-max", type=_positive_int, metavar="N",
                    help="per-worker cgroup PID cap (positive integer; "
                         f"default {DEFAULT_CAPS['worker_pids_max']}). "
                         "Bounds fork/clone in each claude -p worker "
                         "subtree; a runaway fork-bomb stays inside the "
                         "worker cgroup rather than exhausting the VM's "
                         "PID table. Raise it for repos whose conformance "
                         "step runs a subprocess-heavy full test suite. "
                         f"Also {WORKER_PIDS_MAX_ENV} env var or "
                         "worker_pids_max in leerie.toml")
    ap.add_argument("--skip-smoke", action="store_true",
                    help="skip the live claude -p smoke test during preflight. "
                         "Default: off (smoke test runs)")
    ap.add_argument("--skip-overlap-judge", action="store_true",
                    help="skip the phase 2¾ plan-overlap judge worker even on "
                         "multi-planner runs (DESIGN §5 Cross-domain surface "
                         "overlap). The cheap-skip on single-planner / "
                         "<2-subtask runs is automatic; this flag only "
                         "affects runs where the worker would otherwise fire. "
                         f"Also {SKIP_OVERLAP_JUDGE_ENV} env or "
                         "skip_overlap_judge in leerie.toml. Default: off.")
    ap.add_argument("--skip-satisfied-check", action="store_true",
                    help="skip the phase 3 per-subtask satisfied-probe that "
                         "drops subtasks already met on the base tree (DESIGN "
                         "§8 Already-satisfied subtask elimination). When set, "
                         "every subtask proceeds to scheduling; the mechanical "
                         "no-commits backstop still catches already-satisfied "
                         "work post-execution (as a retryable no-op). "
                         f"Also {SKIP_SATISFIED_CHECK_ENV} env or "
                         "skip_satisfied_check in leerie.toml. Default: off.")
    ap.add_argument("--skip-budget-check", action="store_true",
                    help="skip the post-schedule budget-feasibility preflight "
                         "(DESIGN §13 Budget feasibility — fail fast at the "
                         "cheapest moment). The runtime backstop in "
                         "State.bump_workers() still fires if the run exceeds "
                         "--max-workers during execution; this flag only "
                         "suppresses the early die() that catches "
                         "mathematically-unwinnable runs at the plan/execute "
                         "boundary. "
                         f"Also {SKIP_BUDGET_CHECK_ENV} env or "
                         "skip_budget_check in leerie.toml. Default: off.")
    ap.add_argument("--strict-conformer", action="store_true",
                    help="make the conformer phase blocking: conformer "
                         "residuals (failed build/lint/test, unresolved rule "
                         "violations) cause the subtask to return 'blocked' "
                         "instead of surfacing as advisory warnings. Resume "
                         "with --resume after fixing. "
                         f"Also {STRICT_CONFORMER_ENV} env or "
                         "strict_conformer in leerie.toml. Default: off.")
    ap.add_argument("--skip-base-baseline", action="store_true",
                    help="skip the base-tree health baseline (DESIGN §9): the "
                         "once-per-run install-into-staging + build/lint/test "
                         "pass that records whether the base was green before "
                         "any subtask ran. The pass runs the full suite once "
                         "(tens of seconds to a few minutes); skip it when the "
                         "base is known green or the up-front cost is unwanted. "
                         f"Also {SKIP_BASE_BASELINE_ENV} env or "
                         "skip_base_baseline in leerie.toml. Default: off.")
    ap.add_argument("--skip-repo-map", action="store_true",
                    help="skip the P6 repo-map structural context (DESIGN §5½ (P6)): "
                         "suppresses build_repo_map() and the ranked subgraph "
                         "injection into planner/splitter context. The planner "
                         "degrades gracefully to the prior grep/glob-only path. "
                         "Use on repos where tree-sitter cannot parse the "
                         "primary language, or to opt out of structural context. "
                         f"Also {SKIP_REPO_MAP_ENV} env or "
                         "skip_repo_map in leerie.toml. Default: off.")
    ap.add_argument("--source-of-truth", choices=SOURCE_OF_TRUTH_VALUES,
                    metavar="VALUE",
                    help=f"source-of-truth preference "
                         f"({'|'.join(SOURCE_OF_TRUTH_VALUES)}, default both); "
                         f"overrides {SOURCE_OF_TRUTH_ENV} and leerie.toml")
    ap.add_argument("--runtime", choices=RUNTIME_VALUES,
                    metavar="MODE",
                    help=f"execution runtime "
                         f"({'|'.join(RUNTIME_VALUES)}, default local); "
                         f"overrides {RUNTIME_ENV} and leerie.toml")
    ap.add_argument("--inspect-dir", action="append", metavar="PATH",
                    dest="inspect_dir",
                    help="extra directory the inspect-bucket workers "
                         "(classifier, planner, reconciler, plan_overlap_judge, provision) may read. "
                         "Forwarded to `claude -p` as --add-dir. Repeatable. "
                         "Use for sibling repos referenced in the task that "
                         "live outside the current repo cwd. Default: none. "
                         f"Also {INSPECT_DIRS_ENV} (colon-separated) or "
                         "inspect_dirs in leerie.toml (comma-separated).")
    ap.add_argument("--model", choices=MODEL_VALUES, metavar="ALIAS",
                    help=f"model alias for all workers "
                         f"({'|'.join(MODEL_VALUES)}); no global default — "
                         f"without an override, judgment workers default to "
                         f"{MODEL_DEFAULT} and the acting workers "
                         f"(implementer, conformer) default to "
                         f"{MODEL_DEFAULT_PER_WORKER['implementer']} "
                         "(IMPLEMENTATION.md §2). Per-worker "
                         "--model-<worker> flags override this, as do "
                         "LEERIE_MODEL[_*] env vars and leerie.toml")
    for _w in WORKER_TYPES:
        _w_default = MODEL_DEFAULT_PER_WORKER.get(_w, MODEL_DEFAULT)
        ap.add_argument(f"--model-{_w}", choices=MODEL_VALUES, metavar="ALIAS",
                        help=f"model alias for the {_w} worker "
                             f"(default {_w_default}) — overrides "
                             f"--model, LEERIE_MODEL, and leerie.toml")
    # Effort selection — see IMPLEMENTATION.md §2 "Effort selection".
    # Same shape as --model: a global --effort plus per-worker --effort-<W>
    # overrides. Acting workers (implementer, conformer) have no per-worker
    # default, so without an override they get no --effort flag at all and
    # inherit Claude's default — the previous behavior.
    _judgment_workers = ", ".join(sorted(EFFORT_DEFAULT_PER_WORKER))
    ap.add_argument("--effort", choices=EFFORT_VALUES, metavar="LEVEL",
                    help=f"reasoning-depth dial for all workers "
                         f"({'|'.join(EFFORT_VALUES)}); judgment workers "
                         f"({_judgment_workers}) default to "
                         f"{EFFORT_DEFAULT_PER_WORKER['planner']}, acting "
                         "workers (implementer, conformer) default to unset "
                         "(IMPLEMENTATION.md §2). Per-worker --effort-<worker> "
                         "flags override this, as do LEERIE_EFFORT[_*] env vars "
                         "and leerie.toml")
    for _w in WORKER_TYPES:
        _e_default = EFFORT_DEFAULT_PER_WORKER.get(_w, "unset")
        ap.add_argument(f"--effort-{_w}", choices=EFFORT_VALUES, metavar="LEVEL",
                        help=f"reasoning depth for the {_w} worker "
                             f"(default {_e_default}) — overrides "
                             f"--effort, LEERIE_EFFORT, and leerie.toml")
    ap.add_argument("--judge-model", choices=MODEL_VALUES, metavar="ALIAS",
                    help=f"model alias for the judge post-run worker "
                         f"(default {MODEL_DEFAULT_PER_WORKER['judge']}); "
                         f"also {MODEL_JUDGE_ENV} or model_judge in leerie.toml")
    ap.add_argument("--heal-model", choices=MODEL_VALUES, metavar="ALIAS",
                    help=f"model alias for the heal post-run worker "
                         f"(default {MODEL_DEFAULT_PER_WORKER['heal']}); "
                         f"also {MODEL_HEAL_ENV} or model_heal in leerie.toml")
    ap.add_argument("--pr-writer-model", choices=MODEL_VALUES, metavar="ALIAS",
                    help=f"model alias for the pr_writer finalize worker "
                         f"(default {MODEL_DEFAULT_PER_WORKER['pr_writer']}); "
                         f"also {MODEL_PR_WRITER_ENV} or model_pr_writer "
                         f"in leerie.toml")
    ap.add_argument("--heal-max-rounds", type=int, metavar="N",
                    help=f"maximum heal-loop iterations per call_type "
                         f"(default {HEAL_MAX_ROUNDS_DEFAULT}); "
                         f"also {HEAL_MAX_ROUNDS_ENV} or heal_max_rounds in leerie.toml")
    ap.add_argument("--heal-success-threshold", type=float, metavar="RATE",
                    help=f"pass-rate threshold for heal-loop SUCCESS verdict "
                         f"(default {HEAL_SUCCESS_THRESHOLD_DEFAULT}); "
                         f"also {HEAL_SUCCESS_THRESHOLD_ENV} or "
                         "heal_success_threshold in leerie.toml")
    # Verbosity: explicit --verbosity wins; -v/-q stackable shortcuts
    # anchor to `normal` (the pre-streaming behavior). So `-v` = stream,
    # `-vv` = debug, `-q` = normal, `-qq` = quiet. See IMPLEMENTATION.md
    # §2 "Verbosity". When none are given, resolve_verbosity falls
    # through to env / TOML / VERBOSITY_DEFAULT.
    ap.add_argument("--verbosity", choices=VERBOSITY_VALUES, metavar="LEVEL",
                    help=f"output verbosity ({'/'.join(VERBOSITY_VALUES)}, "
                         f"default {VERBOSITY_DEFAULT}); overrides "
                         f"{VERBOSITY_ENV} and leerie.toml")
    ap.add_argument("-v", "--verbose", action="count", default=0,
                    help="shortcut: -v=stream, -vv=debug. Default: 0 "
                         "(no -v; falls through to --verbosity)")
    ap.add_argument("-q", "--quiet", action="count", default=0,
                    help="shortcut: -q=normal (pre-streaming behavior), "
                         "-qq=quiet (errors and phase boundaries only). "
                         "Default: 0 (no -q; falls through to --verbosity)")
    ap.add_argument("--judge-dir", metavar="DIR",
                    help=f"subdirectory name under the run dir for LLM judge "
                         f"output (default '{JUDGE_DIR_DEFAULT}'); also "
                         f"{JUDGE_DIR_ENV} or judge_dir in leerie.toml")
    ap.add_argument("--heal-dir", metavar="DIR",
                    help=f"subdirectory name under the run dir for LLM self-heal "
                         f"output (default '{HEAL_DIR_DEFAULT}'); also "
                         f"{HEAL_DIR_ENV} or heal_dir in leerie.toml")
    ap.add_argument("--phase", choices=["judge", "heal"], metavar="PHASE",
                    help="run a post-run skill phase against an existing run's "
                         "captured LLM calls instead of starting a new run. "
                         "PHASE must be 'judge' or 'heal'. Requires an existing "
                         "run (use --run-id to select one when multiple runs "
                         "exist, or omit when exactly one run is in flight). "
                         "'judge' scores every captured call in calls.ndjson "
                         "using the 3-dimensional LLM judge rubric and writes "
                         "verdict files to <run-dir>/<judge-dir>/. "
                         "'heal' reads the judge index for failing call_types "
                         "and runs the self-heal loop for each, writing healing "
                         "reports to <run-dir>/<heal-dir>/.")
    ap.add_argument("--group-id", metavar="UUID",
                    help="run-group identifier (UUID). Written into run.json "
                         "alongside chain_id. Set by the --group launcher arm "
                         "when fanning out N member runs; a member with a "
                         "group_id behaves identically to a standalone run "
                         "otherwise. See DESIGN.md §20 and IMPLEMENTATION.md "
                         "'Run-group verbs'.")
    args = ap.parse_args()

    # --list short-circuits everything else: read <state-root>/runs/* and
    # exit. No git/CLI checks needed; the user might be inspecting runs
    # from outside a git repo.
    if args.list_runs:
        leerie_root = resolve_leerie_root(Path(os.getcwd()))
        list_runs(
            leerie_root,
            status_filter=args.status_filter,
            runtime_filter=args.runtime,
        )
        return

    # --report is a read-only telemetry verb; like --list it exits without
    # running orchestrate. `const=""` (nargs="?") means the flag was passed
    # with no inline id → resolve_run_id auto-picks the sole run.
    if getattr(args, "report", None) is not None:
        leerie_root = resolve_leerie_root(Path(os.getcwd()))
        report_run(leerie_root, args.report or args.run_id)
        return

    if not shutil.which("claude"):
        die("`claude` CLI not found on PATH. Install Claude Code (native, "
            "recommended): `curl -fsSL https://claude.ai/install.sh | bash`. "
            "Docs: https://docs.claude.com/en/docs/claude-code/setup")
    # The cwd-is-git-repo check moved to the host launcher (DESIGN §6).
    # If the launcher started us, we're already in a git repo by then.

    caps = dict(DEFAULT_CAPS)
    # Resolve max_total_workers across CLI / env / TOML / default. The
    # resolver die()s on a bad env or TOML value; argparse already rejected
    # a bad --max-workers via _positive_int.
    caps["max_total_workers"] = resolve_max_workers(
        Path(os.getcwd()), args.max_workers)
    caps["max_parallel"] = resolve_max_parallel(
        Path(os.getcwd()), args.max_parallel)
    # Resolve confidence_rounds across CLI / env / TOML / default. The
    # resolver die()s on a bad env or TOML value; argparse already rejected
    # a bad --confidence-rounds via _positive_int.
    caps["confidence_rounds"] = resolve_confidence_rounds(
        Path(os.getcwd()), args.confidence_rounds)
    cwd = Path(os.getcwd())
    caps["judgment_check_rounds"] = resolve_judgment_check_rounds(
        cwd, getattr(args, "judgment_check_rounds", None))
    caps["planner_check_rounds"] = resolve_planner_check_rounds(
        cwd, getattr(args, "planner_check_rounds", None))
    caps["implementer_confidence_retries"] = \
        resolve_implementer_confidence_retries(
            cwd, getattr(args, "implementer_confidence_retries", None))
    caps["planner_samples"] = resolve_planner_samples(
        cwd, getattr(args, "planner_samples", None))
    # Resolve per-worker cgroup memory cap. Auto-derives from
    # /proc/meminfo when unset; resolver die()s on a bad size string.
    # Reads `caps["max_parallel"]` already resolved above so the auto-
    # derived value is "VM ram split N+1 ways, capped at 4 GiB".
    caps["worker_memory_max_bytes"] = resolve_worker_memory_max(
        Path(os.getcwd()), caps["max_parallel"], args.worker_memory_max)
    # Per-worker cgroup PID cap. CLI > env > leerie.toml > default; the
    # resolver die()s on a non-positive-integer value. Threaded into
    # _cgroup_create via the caps.get("worker_pids_max", …) site so the
    # broker writes it to the worker cgroup's pids.max.
    caps["worker_pids_max"] = resolve_worker_pids_max(
        Path(os.getcwd()), args.worker_pids_max)
    caps["strict_conformer"] = args.strict_conformer

    # Resolve verbosity. Explicit --verbosity wins; else -v/-q
    # shortcuts (anchored to `normal`); else env / TOML / default.
    # See verbosity_from_shortcuts() for the shortcut-mapping rationale.
    verbosity = (args.verbosity
                 or verbosity_from_shortcuts(args.verbose, args.quiet)
                 or resolve_verbosity(Path(os.getcwd()), None))

    # The on-disk layout is per-run: every run gets its own subdirectory
    # `leerie_root/runs/<run-id>/` (see DESIGN.md §6, §10). The run_id
    # is the container/machine ID — the launcher always passes it via
    # --run-id.
    repo_root = Path(os.getcwd())
    leerie_root = resolve_leerie_root(repo_root)
    leerie_root.mkdir(parents=True, exist_ok=True)
    (leerie_root / "runs").mkdir(parents=True, exist_ok=True)
    if args.resume:
        run_id = resolve_run_id(leerie_root, args.run_id)
    elif args.run_id:
        run_id = args.run_id
    else:
        die("--run-id is required (the launcher passes the container/machine ID)")
    try:
        st = State(leerie_root, run_id, repo_root=repo_root)
    except StateLockedError as e:
        # Another orchestrator already owns this run dir (likely the
        # user ran `--resume` while the original orchestrator was still
        # alive — see DESIGN §6 *Single owner per run dir*). The
        # launcher's flock probe should normally catch this earlier;
        # the check here is the load-bearing one for any code path
        # that bypasses the launcher (manual `python3 leerie.py
        # --resume`, future verbs, etc.).
        log(f"another orchestrator already owns run {run_id!r} "
            f"(holding flock on {e.run_dir}). "
            f"Tail it without spawning a duplicate: "
            f"`leerie --resume {run_id}`. "
            f"If the holder is wedged, kill it and retry.")
        sys.exit(EXIT_LOCKED)
    for sub in ("", "subtasks", "criteria", "checkpoints", "logs"):
        (st.run_dir / sub).mkdir(parents=True, exist_ok=True)

    # Resolve source-of-truth and per-worker model preferences once per run.
    # Both die() on a bad value so typos in leerie.toml or env vars are
    # caught at startup, not mid-planner. argparse already rejected any bad
    # --source-of-truth / --model[-*] before we got here.
    sot_pref = resolve_source_of_truth(repo_root, args.source_of_truth)
    args.runtime = resolve_runtime(repo_root, args.runtime)
    models = resolve_models(repo_root, args)
    log(f"models: " + ", ".join(f"{w}={models[w]}" for w in WORKER_TYPES))
    efforts = resolve_efforts(repo_root, args)
    # Log only workers with a resolved effort — an "unset" worker is
    # explicitly opting out of the --effort flag and showing it as
    # "effort=None" in the log would be noise.
    _e_pairs = [f"{w}={efforts[w]}" for w in WORKER_TYPES
                if efforts[w] is not None]
    if _e_pairs:
        log("efforts: " + ", ".join(_e_pairs))

    # Resolve --no-push: CLI flag → LEERIE_NO_PUSH env → no_push in
    # leerie.toml → False. Re-attach to args so orchestrate() /
    # preflight() / phase_finalize() see the resolved value uniformly via
    # `args.no_push` regardless of where the choice came from.
    args.no_push = resolve_no_push(repo_root, args.no_push)

    # Coerce --host-no-push from "true"/"false"/None into bool | None.
    # None on the local-runtime path (launcher doesn't pass it) means
    # push_will_happen() falls back to `not no_push`. On Fly the
    # launcher always passes "true" or "false" reflecting the user's
    # launch-time intent (host_no_push in fly-machine.json).
    args.host_no_push = (
        None if args.host_no_push is None
        else args.host_no_push == "true"
    )

    # Resolve --clarify with the same shape as --no-push (DESIGN §11).
    # Re-attach to args so orchestrate() folds it into state.json under
    # the canonical "clarify" key.
    args.clarify = resolve_clarify(repo_root, args.clarify)

    # Resolve --dangerously-skip-permissions (DESIGN §12 escape hatch).
    # Same precedence shape as --no-push / --clarify. Re-attach to args
    # so orchestrate() folds it into state.json under the canonical
    # "dangerously_skip_permissions" key; claude_p reads it from there
    # on every invocation instead of threading another parameter.
    args.dangerously_skip_permissions = resolve_dangerously_skip_permissions(
        repo_root, args.dangerously_skip_permissions)
    if args.dangerously_skip_permissions:
        log("dangerously-skip-permissions: ON "
            "(judgment workers run with prompts disabled — "
            "§12 enforcement waived)")

    # Resolve --skip-overlap-judge (DESIGN §5 *Cross-domain surface
    # overlap*). Same precedence shape as --clarify /
    # --dangerously-skip-permissions. Re-attach to args so orchestrate()
    # folds it into state.json under the canonical "skip_overlap_judge"
    # key; phase_overlap_judge reads it from there on entry.
    args.skip_overlap_judge = resolve_skip_overlap_judge(
        repo_root, args.skip_overlap_judge)

    # Resolve --skip-satisfied-check (DESIGN §8 *Already-satisfied subtask
    # elimination*). Same precedence shape as the other skip flags.
    # orchestrate() folds it into state.json under "skip_satisfied_check";
    # filter_satisfied_subtasks reads it from there on entry.
    args.skip_satisfied_check = resolve_skip_satisfied_check(
        repo_root, args.skip_satisfied_check)

    # Resolve --skip-budget-check (DESIGN §13 *Budget feasibility — fail
    # fast at the cheapest moment*). Same precedence shape as the other
    # skip flags. Re-attach to args so orchestrate() folds it into
    # state.json under the canonical "skip_budget_check" key;
    # check_budget_feasibility() reads it from there.
    args.skip_budget_check = resolve_skip_budget_check(
        repo_root, args.skip_budget_check)

    args.strict_conformer = resolve_strict_conformer(
        repo_root, args.strict_conformer)

    args.skip_base_baseline = resolve_skip_base_baseline(
        repo_root, args.skip_base_baseline)

    args.skip_repo_map = resolve_skip_repo_map(
        repo_root, args.skip_repo_map)

    args.dangerously_allow_uncapped = resolve_dangerously_allow_uncapped(
        repo_root, args.dangerously_allow_uncapped)

    # Resolve --pr-template: free-form string (no enum). Re-attach to
    # args so phase_finalize sees the resolved value via
    # `args.pr_template`. None means "alphabetically first .md in
    # PULL_REQUEST_TEMPLATE/" (the discovery helper's default).
    args.pr_template = resolve_pr_template(
        repo_root, getattr(args, "pr_template", None))

    # Resolve --inspect-dir: CLI flags (repeatable) → LEERIE_INSPECT_DIRS
    # env (colon-separated) → inspect_dirs in leerie.toml (comma-separated)
    # → []. Re-attached to args so orchestrate() can fold it into state.
    args.inspect_dirs = resolve_inspect_dirs(
        repo_root, getattr(args, "inspect_dir", None))

    args.judge_dir = resolve_judge_dir(repo_root, args.judge_dir)
    args.heal_dir = resolve_heal_dir(repo_root, args.heal_dir)
    args.heal_max_rounds = resolve_heal_max_rounds(
        repo_root, getattr(args, "heal_max_rounds", None))
    args.heal_success_threshold = resolve_heal_success_threshold(
        repo_root, getattr(args, "heal_success_threshold", None))

    # --phase judge|heal: post-run skill phases. Short-circuit the normal
    # orchestrate() flow — just pick an existing run and run the skill.
    if args.phase:
        # `--phase judge|heal` spawns claude -p workers (judge /
        # patch_generator) but is deliberately NOT gated by the cgroup
        # containment check: it is an opt-in post-run analysis tool
        # operating on an already-finished run with read-only / patch
        # workers, not the conformer-heavy main run that motivated the
        # gate. Intentional non-coverage — do not "fix" as an oversight.
        phase_run_id = resolve_run_id(leerie_root, args.run_id)
        try:
            phase_st = State(leerie_root, phase_run_id, repo_root=repo_root)
        except StateLockedError as e:
            # `--phase judge|heal` mutates state.json (writes
            # `dangerously_skip_permissions` below, then runs workers
            # that update telemetry). Running concurrently with the
            # original orchestrator would race the same way `--resume`
            # would. Refuse here.
            log(f"cannot run --phase on {phase_run_id!r}: another "
                f"orchestrator owns the run (holding flock on "
                f"{e.run_dir}). Wait for it to finish, or kill it "
                f"first if wedged.")
            sys.exit(EXIT_LOCKED)
        if not phase_st.load():
            die(f"no state.json found for run {phase_run_id!r}; "
                f"the run may not have reached the execute phase yet")
        # Refresh the escape-hatch preference so a user invoking
        # `--phase judge|heal --dangerously-skip-permissions` gets the
        # flag flowed into the judge/patch_generator workers — without
        # this, claude_p reads the value the original run persisted and
        # the visible startup log would lie about whether the workers
        # actually see the override.
        phase_st.data["dangerously_skip_permissions"] = bool(
            args.dangerously_skip_permissions)
        phase_st.save()
        phase_run_dir = phase_st.run_dir
        judge_out_dir = phase_run_dir / args.judge_dir
        heal_out_dir = phase_run_dir / args.heal_dir
        if args.phase == "judge":
            asyncio.run(phase_judge(phase_run_dir, judge_out_dir, caps,
                                    phase_st, models, efforts))
        else:  # heal
            # Read judge INDEX.json to find failing call_types; if no index
            # exists yet, run phase_judge first so heal has verdicts to
            # act on.
            index_path = judge_out_dir / "INDEX.json"
            if not index_path.exists():
                log("--phase heal: no judge INDEX.json found; running judge first")
                asyncio.run(phase_judge(phase_run_dir, judge_out_dir, caps,
                                        phase_st, models))
            if index_path.exists():
                try:
                    index = json.loads(index_path.read_text())
                except (OSError, ValueError) as e:
                    die(f"--phase heal: could not read {index_path}: {e}")
            else:
                index = []
            # Collect failing call_ids grouped by call_type.
            failing_by_type: dict[str, list[str]] = {}
            for entry in index:
                if not entry.get("passed", True):
                    ct = entry.get("call_type", "unknown")
                    failing_by_type.setdefault(ct, []).append(
                        entry.get("call_id", ""))
            if not failing_by_type:
                log("--phase heal: no failing captures in judge index; nothing to heal")
                return
            # Load the original capture records from calls.ndjson.
            capture_path = phase_run_dir / "calls.ndjson"
            all_captures: dict[str, dict] = {}
            if capture_path.exists():
                for line in capture_path.read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        all_captures[rec.get("call_id", "")] = rec
                    except (ValueError, AttributeError):
                        pass
            for call_type, failing_ids in sorted(failing_by_type.items()):
                failing_records = [all_captures[cid] for cid in failing_ids
                                   if cid in all_captures]
                if not failing_records:
                    log(f"--phase heal: {call_type}: no capture records found; skipping")
                    continue
                config = {
                    "max_iterations": args.heal_max_rounds,
                    "success_threshold": args.heal_success_threshold,
                }
                asyncio.run(phase_heal(call_type, failing_records, heal_out_dir,
                                       caps, phase_st, models, efforts,
                                       config=config))
        return

    # The fail-closed cgroup containment gate (DESIGN §6 *Memory
    # containment*) runs inside `_run_phases`, at the two points that are
    # guaranteed to spawn workers (after the resume short-circuits for
    # already-completed / no-work runs, which spawn none). Gating here in
    # `main()` — before `orchestrate()` — would die() spuriously on those
    # zero-worker resume paths on a containment-incapable host.

    # Signal handlers (DESIGN §6 / DESIGN §14): SIGTERM and SIGHUP raise
    # InterruptedBySignal so the same try/except machinery that catches
    # KeyboardInterrupt also handles process-level termination. The
    # cleanup logic chooses full purge vs. worktrees-only based on which
    # signal/exception fired.
    _install_signal_handlers()

    abnormal = False
    full_purge = False
    exit_code = 0
    exit_message: str | None = None
    try:
        asyncio.run(orchestrate(args, caps, st.run_dir, st,
                                sot_pref, verbosity, models, efforts))
    except WorkerError as e:
        abnormal = True
        full_purge = False
        st.save()
        exit_message = str(e)
        exit_code = 1
    except RateLimitedExit as e:
        # Claude Code session-limit / rate-limit (or out-of-credits mid-stream
        # kill) hit mid-worker. See DESIGN §6 *Rate-limited → auto-resume*.
        # Two dispositions:
        #   1. out_of_credits=True → PAUSE-AND-SURFACE. Out-of-credits has no
        #      reset clock (it clears on a top-up / billing cycle, not by
        #      waiting), so auto-resuming would only spin a fixed backoff
        #      against the wall and burn the worker budget. Instead: worktree
        #      cleanup, a --resume hint, and EXIT_LOCKED. Checked first.
        #   2. otherwise (rate-limit) → AUTO-RESUME via `_sleep_then_reexec`
        #      (worktree-only cleanup → sleep → os.execv the orchestrator into
        #      a fresh `--resume --run-id` process). The wait differs:
        #        - reset_at parsed cleanly → sleep until that moment + 30s.
        #        - reset_at is None (parse failed) → sleep a fixed
        #          RATE_LIMIT_RETRY_BACKOFF_SEC and poll; we can't compute a
        #          wake time, and an early retry just re-hits the same clean
        #          pause. (This replaced the old "exit 75, resume manually"
        #          behavior — a fixed backoff guesses no wrong time.)
        # We re-exec the orchestrator, not the launcher: the launcher isn't baked
        # into the image and would spawn a new container. `worker_count` persists
        # in state.json so `--max-workers` survives the re-exec — a run that
        # repeatedly hits the limit still respects the cap. Subprocess cleanup is
        # handled by the asyncio cancellation chain (IMPLEMENTATION §5): _invoke's
        # BaseException guard kills the rate-limited claude -p child; sibling
        # wave-tasks cancel through gather.
        full_purge = False
        st.save()
        if e.out_of_credits:
            # Pause-and-surface. Run worktree-only cleanup directly here (state
            # + run branch preserved), then leave abnormal=False so the finally
            # doesn't re-run it. EXIT_LOCKED is the launcher's preserve-state
            # pause pivot — reuse it rather than minting a new code.
            log(f"out of credits — {e.raw_message}")
            log(f"  add credits, then resume with: leerie --resume {st.run_id}")
            abnormal = False
            try:
                _cleanup_on_abnormal_exit(st, full_purge=False)
            except BaseException as cleanup_err:
                log(f"cleanup failed (non-fatal): {cleanup_err}")
            # Best-effort dep_capture in its own asyncio.run (DESIGN §6½).
            # Non-fatal. Mirrors the cancel-arm pattern.
            try:
                asyncio.run(capture_repo_deps(
                    repo_root, st,
                    caps=caps, models=models, efforts=efforts,
                ))
            except Exception as _cap_exc:
                log(f"capture: non-fatal error during out-of-credits pause "
                    f"({_cap_exc})")
            exit_code = EXIT_LOCKED
        else:
            log(f"rate-limited: {e.raw_message}")
            # `_sleep_then_reexec` runs cleanup itself and either os.execv's
            # (never returns) or returns an exit code on interrupt/failure — so
            # leave abnormal=False (the finally must NOT re-run cleanup).
            abnormal = False
            if e.reset_at is not None:
                # `_sleep_then_reexec` runs cleanup first, so the wait is measured
                # from after cleanup; the +30s margin absorbs the drift, and a
                # premature wake self-corrects (the re-exec'd run re-hits the limit
                # and re-sleeps). See the helper's docstring.
                wait_seconds = max(
                    0,
                    int((e.reset_at - datetime.now(e.reset_at.tzinfo))
                        .total_seconds())) + 30
                reason = f"rate limit resets {e.reset_at.isoformat()}"
            else:
                wait_seconds = RATE_LIMIT_RETRY_BACKOFF_SEC
                reason = "rate limited / no reset time"
            rc = _sleep_then_reexec(st, wait_seconds, reason)
            if rc is not None:
                # Interrupted (SIGINT→130, SIGTERM/SIGHUP→128+signum) or execv
                # failed (→75). Cleanup already ran; state preserved for --resume.
                exit_code = rc
            # Otherwise _sleep_then_reexec os.execv'd and never returned.
    except KeyboardInterrupt:
        # Ctrl-C → worktree cleanup only; state and branches preserved
        # so the user can --resume. The explicit "throw this away"
        # gesture is `scripts/cleanup.sh --run-id <id> --branches`,
        # not Ctrl-C. asyncio.run already cancelled pending tasks and
        # _invoke's / run_proc's BaseException handlers killed
        # in-flight child processes (DESIGN §6).
        abnormal = True
        full_purge = False
        st.save()
        log("interrupted by user (SIGINT) — worktree cleanup; "
            f"state preserved (resume with leerie --resume {st.run_id})")
        # Best-effort dep_capture in its own asyncio.run (DESIGN §6½).
        # Mirrors the RateLimitedExit post-loop pattern. Non-fatal.
        try:
            asyncio.run(capture_repo_deps(
                repo_root, st,
                caps=caps, models=models, efforts=efforts,
            ))
        except Exception as _cap_exc:
            log(f"capture: non-fatal error during cancel-arm capture "
                f"({_cap_exc})")
        exit_code = 130
    except InterruptedBySignal as e:
        # SIGTERM / SIGHUP → external orchestration (CI cancel, systemd
        # stop, terminal close). User likely wants to recover; preserve
        # state and run branch for --resume.
        abnormal = True
        full_purge = False
        st.save()
        log(f"interrupted by signal ({e}) — worktree cleanup; "
            f"state preserved (resume with leerie --resume {st.run_id})")
        # Best-effort dep_capture in its own asyncio.run (DESIGN §6½).
        # Non-fatal.
        try:
            asyncio.run(capture_repo_deps(
                repo_root, st,
                caps=caps, models=models, efforts=efforts,
            ))
        except Exception as _cap_exc:
            log(f"capture: non-fatal error during signal-arm capture "
                f"({_cap_exc})")
        # 128 + signal number; SIGTERM=15 → 143, SIGHUP=1 → 129.
        signum = getattr(signal, str(e), None)
        exit_code = (128 + int(signum)) if signum else 1
    except SystemExit as e:
        # `die()` raises SystemExit. It's the *clean* exit mechanism for
        # known failure modes (preflight gh missing, classifier produced
        # no categories, integrator design-conflict, ...). Don't treat it
        # as an unhandled exception — die() already printed the right
        # message. Mark abnormal so the finally block can clean up any
        # worktrees the run did create (no-op when none exist, e.g.
        # preflight die() before setup-run.sh ran).
        abnormal = True
        full_purge = False
        # fetch_branch's discovery script requires finished_at in
        # run.json. Without this write, every post-setup die() leaves
        # the run undiscoverable and the sync fails with "no completed
        # unpushed run found on machine."
        # The exit code file lets the tail wrapper propagate the
        # orchestrator's exit code to decide_teardown so failed runs
        # reach the pause branch (DESIGN §6 teardown disposition table).
        if st is not None and st.run_dir is not None:
            try:
                st.data["finished_at"] = now()
                # Only persist state.json when it carries meaningful
                # content.  A failed --resume leaves st.data as a bare
                # stub (no "task"); writing that poisons the host-side
                # file and blocks subsequent resumes with "no usable
                # task" instead of the clearer "no state.json".
                if st.data.get("task"):
                    st.save()
                _write_run_json(st.run_dir, finished_at=st.data["finished_at"])
                _ec = e.code if e.code is not None else 1
                (st.run_dir / "orchestrator.exit_code").write_text(
                    str(_ec) + "\n")
            except Exception:
                pass
        raise
    except BaseException as e:
        # Anything else (genuinely unhandled exception in orchestrate,
        # asyncio cancellation chain, etc.). Save state, mark abnormal
        # so the finally block runs cleanup, then re-raise so the user
        # sees the traceback.
        abnormal = True
        full_purge = False
        st.save()
        log(f"unhandled exception: {type(e).__name__}: {e}")
        raise
    finally:
        if abnormal:
            try:
                _cleanup_on_abnormal_exit(st, full_purge=full_purge)
            except BaseException as cleanup_err:
                # Cleanup failure is non-fatal; the user can re-run
                # `scripts/cleanup.sh --run-id <id>` manually.
                log(f"cleanup failed (non-fatal): {cleanup_err}")
    # Write exit code for the tail wrapper (DESIGN §6 teardown
    # disposition table). The SystemExit handler writes it eagerly
    # (before re-raise); this covers the non-raise paths
    # (KeyboardInterrupt, InterruptedBySignal, normal exit).
    if st is not None and st.run_dir is not None:
        try:
            (st.run_dir / "orchestrator.exit_code").write_text(
                str(exit_code) + "\n")
        except Exception:
            pass
    if exit_message is not None:
        die(exit_message, code=exit_code)
    if exit_code != 0:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
