# Behavioral Regression Gate — Design Spec

- **Date:** 2026-06-18
- **Status:** Approved design; ready for implementation planning
- **Topic:** Turn the existing telemetry / judge / heal loop into a behavioral regression gate
- **Layers touched (per `CLAUDE.md` three-layer rule):** `DESIGN.md §14` →
  `IMPLEMENTATION.md §8/§10` → code. This spec is the planning artifact; the
  canonical layers are updated top-down during implementation (see §10).

---

## 1. Summary

Leerie already captures every worker call, can replay any call through a
patched prompt, and can judge a call on three dimensions. What it cannot do
today is **tell you whether a prompt edit made a worker behave worse.** That is
precisely the verification gap `DESIGN.md §16` is honest about: the
deterministic scaffolding is proven, but *behavioral quality of workers is
unverified* and the recommended first step is to "run Leerie once on a throwaway
repository with a small, fully-specified task before trusting it."

This spec closes that gap by assembling already-built primitives into a
**behavioral regression gate**:

1. A committed **golden corpus** of real captured calls (from live runs against
   a small throwaway fixture repo — the run §16 recommends anyway).
2. A committed **per-`call_type` baseline pass-rate** measured at capture time.
3. A deterministic **comparator** that re-runs the corpus through the *current*
   prompts, judges the fresh output, and **fails when judged pass-rate drops
   below baseline beyond a noise tolerance.**

It runs as a local command (`leerie regress`) plus an optional self-hosted /
`workflow_dispatch` job. It is **not** a hosted-CI PR check: `claude -p` runs on
the user's subscription with no API key, so a hosted GitHub runner cannot
execute it.

The gate is *measurement, not trust* — it sits squarely on the "code enforces"
side of `DESIGN.md §12` (see §8). The judge measures; Python decides.

## 2. Goals & non-goals

**Goals**

- Detect behavioral regressions caused by editing `prompts/*.md` (including
  `{{include:}}` partials and the judge rubric) before they ship.
- Reuse the existing capture/replay/judge primitives wholesale; add the minimum
  new surface (corpus format, capture command, env reconstruction, comparator,
  CLI/workflow).
- Cover **all** worker types — judgment workers (Tier 1) and acting workers
  (Tier 2).
- Keep the pass/fail decision deterministic and code-enforced.

**Non-goals**

- Not a correctness oracle. The judge rubric is advisory (`prompts/judge.md`);
  only the verdict *accounting* and the pass-rate *comparison* are enforced.
- Not pinning golden *output text*. Worker and judge output are
  nondeterministic, so we pin pass-*rates*, exactly as `heal_baseline` does.
- Not a replacement for `pytest tests/`. This is a separate, model-dependent
  tier that runs on demand against an authenticated `claude`.
- Not (in v1) judging the *diff* an acting worker produces — v1 judges the
  returned envelope, same as the existing judge. Diff-aware judging is a future
  extension (§11).

## 3. Background — existing machinery (precise references)

All references are to `orchestrator/leerie.py` unless noted.

| Primitive | Location | Role for this gate |
|---|---|---|
| `_capture_call(run_dir, record)` | `:6234` | Writes the fixed-envelope NDJSON row to `<run_dir>/calls.ndjson`. Source of corpus cases. |
| Fixed-envelope row | written in `claude_p` `:6484–6499` | Keys: `call_id, run_id, call_type, model, system_prompt, user_content, response_content, parsed_ok, input_tokens, output_tokens, latency_ms, success, cgroup_applied, ts`. |
| `claude_p(...)` | `:6344` | Worker invocation. `system_prompt` arg is **always** `load_prompt(<call_type>)` verbatim; all dynamic context is in `user_prompt` (captured as `user_content`). `_suppress_capture=True` skips telemetry (used by replays). |
| `load_prompt(name)` | `:75–84` | Reads `prompts/<name>.md`, expands `{{include: _foo.md}}`. Deterministic. The gate calls this fresh to get the *current* prompt. |
| `replay_capture(record, *, override_system_prompt=None, cwd=None)` | `:6594` | Reconstructs a `claude_p` call from a record; `_suppress_capture=True`. **Tier-1 replay path.** |
| `judge_capture(record, ...)` | `:6859` | Builds a judge prompt from a record's `system_prompt`/`user_content`/`response_content`, invokes `claude_p(schema_key="judge")`, returns the verdict. |
| `phase_judge(...)` | `:6910` | Reads `calls.ndjson`, runs `judge_capture` per record in parallel, writes per-call verdicts + `INDEX.json`. |
| `SCHEMAS["judge"]` | `:1058–1080` | `{passed, dimensions:{schema_ok, factual_ok, hallucination_ok}, rationale, suggested_fixes}`. Reused as-is. |
| `heal_baseline(...)` | `:7033` | Runs `n` unpatched replays per record, judges each, computes per-sample pass-rate. **The n-replay pass-rate pattern the gate reuses.** |
| `check_convergence(state, config)` | `:7243` | Pure-Python verdict incl. `REGRESSED` (`:7292`). The comparator mirrors this style. |
| `HEAL_N_REPLAYS_DEFAULT = 5`, thresholds | `:590–594` | Default replay counts / thresholds the gate's defaults track. |

**Key fact that makes the gate feasible** (verified): for every worker type the
`system_prompt` passed to `claude_p` is `load_prompt(<call_type>)` *verbatim*,
and **all** per-task context is in `user_prompt`. So holding a corpus case's
`user_content` constant while swapping in the live `load_prompt(call_type)` is a
faithful test of the edited prompt — and for judgment workers it requires no
filesystem reconstruction at all.

**Scope boundary that forces two tiers:** acting workers (`implementer`,
`conformer`, `integrator`, `provision`) build `user_content` from on-disk state
(`LEERIE_DIR`, `subtasks/<sid>.json`, the worktree CWD, BUILD/LINT/TEST
commands) and *mutate* a worktree when re-executed. They cannot be replayed as
pure functions; the environment they saw must be reconstructed and the worker
re-executed in isolation (Tier 2).

**What does not exist today:** any notion of a golden corpus, a committed
baseline, a regression comparator, or a `regress`/`corpus` command. CI
(`.github/workflows/{test,syntax,shellcheck}.yml`) has no baseline/threshold
gate.

## 4. Confirmed design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Gate runtime | Local command + optional self-hosted/`workflow_dispatch` job; **not** a hosted-CI PR check | `claude -p` needs subscription auth absent on hosted runners; output is nondeterministic and token-costly. |
| Corpus scope | **All** worker types (judgment + acting) | Behavioral quality of acting workers is exactly what §16 flags; full coverage is the goal. |
| Replay fidelity | Swap in current `load_prompt(call_type)`, hold `user_content` constant | Clean template/context separation verified in code (`:75–84`). |
| Fail rule | **Per-`call_type` pass-rate floor + tolerance**: `current_rate < baseline_rate − tolerance ⇒ REGRESSED` | Robust to model/judge noise; mirrors `heal_baseline` + `check_convergence`'s `REGRESSED`. |
| Corpus storage | **In-repo**, with Tier-2 env fixtures captured against a **small committed throwaway fixture repo** (shipped as a git bundle) | Fully reproducible, no external deps, reviewed in PRs alongside the prompts it guards. |
| Sequencing | **Staged**: Increment A = Tier 1 (judgment workers, full gate); Increment B = Tier 2 (acting workers) | Ship value early on proven primitives; build env reconstruction on a working gate. |
| Defaults | `tolerance = 0.15`, `n_text = 5`, `n_env = 3` (env tolerance `0.20`) | Track `HEAL_N_REPLAYS_DEFAULT`; env tier is noisier and costlier. |

## 5. Architecture

```
corpus/                                  ← committed golden corpus (NEW)
  manifest.json                          ← per-call_type baseline pass-rate, n, tolerance, provenance shas
  cases/<call_type>/<case_id>.json       ← one frozen calls.ndjson envelope per case (+ optional fixture ptr)
  fixtures/<case_id>/                     ← env snapshot, Tier-2 (acting) cases only
    repo.bundle                          ← git bundle of the base repo state
    leerie_dir/                          ← frozen subtasks/<sid>.json, artifacts/, provision recipe
    env.json                             ← cwd-relative path, allowed_tools, add_dirs, BUILD/LINT/TEST, DIFF_BASE

orchestrator/leerie.py (NEW functions)
  phase_regress(corpus_dir, out_dir, caps, st, models, efforts, tier, call_types)
    ├─ Tier 1: replay_capture(rec, override_system_prompt=load_prompt(ct))  → judge_capture   [reuses heal path]
    ├─ Tier 2: replay_in_env(rec, fixture, override_system_prompt=load_prompt(ct)) → judge_capture
    └─ compare_to_baseline(results, manifest) → deterministic REGRESSED/OK verdict
  corpus_capture(run_id, call_types, ...)  ← promote calls.ndjson records → corpus/, snapshot fixtures, pin baseline
  compare_to_baseline(results, manifest)   ← pure Python; the §12 enforcement point
  replay_in_env(record, fixture, *, override_system_prompt)  ← Tier-2 only

leerie (launcher) — new verbs
  leerie regress [--tier text|env|all] [--call-type ...] [--update-baseline]
  leerie corpus capture --from <run-id> [--call-type ...] [--case <name>] [--tier ...]
  leerie corpus list

.github/workflows/regress.yml (NEW)       ← workflow_dispatch (+ optional schedule), self-hosted authed runner
```

The replay+judge+pass-rate core is **already built**. The new code is: the
corpus format, `corpus_capture`, Tier-2 `replay_in_env`, the deterministic
`compare_to_baseline`, the CLI verbs, and the workflow.

## 6. Component specifications

### 6.1 Corpus format

**`corpus/manifest.json`** (validated by a new `_validate_corpus_manifest`,
mirroring `_validate_run_json`):

```json
{
  "version": 1,
  "captured_from": [{"run_id": "…", "ts": "2026-06-18T12:00:00Z"}],
  "defaults": {"tolerance": 0.15, "n_text": 5, "n_env": 3},
  "judge_prompt_sha": "<sha256 of prompts/judge.md at baseline time>",
  "call_types": {
    "classifier": {
      "tier": "text",
      "cases": ["classifier-001", "classifier-002"],
      "baseline_pass_rate": 0.95,
      "n": 5,
      "tolerance": 0.15,
      "baseline_captured_at": "2026-06-18T12:00:00Z",
      "prompt_sha": "<sha256 of prompts/classifier.md (post-include) at baseline>"
    },
    "implementer": {
      "tier": "env",
      "cases": ["implementer-010"],
      "baseline_pass_rate": 0.80,
      "n": 3,
      "tolerance": 0.20,
      "baseline_captured_at": "2026-06-18T12:00:00Z",
      "prompt_sha": "<sha256 of prompts/implementer.md (post-include) at baseline>"
    }
  }
}
```

- `prompt_sha` / `judge_prompt_sha` give provenance. The gate **warns** when the
  current `load_prompt(ct)` sha equals the baseline sha (you ran the gate
  without changing anything — expected to pass) and **warns loudly** when
  `prompts/judge.md` changed since baseline (you moved the ruler; re-baseline is
  required, because the judge is the measuring instrument).

**`corpus/cases/<call_type>/<case_id>.json`** — the frozen envelope plus
provenance:

```json
{
  "case_id": "classifier-001",
  "call_type": "classifier",
  "captured_from_run": "…",
  "fixture": null,
  "record": { /* the verbatim calls.ndjson envelope */ }
}
```

For Tier-2 cases, `"fixture": "fixtures/implementer-010/"`.

**`corpus/fixtures/<case_id>/`** (Tier-2 only):
- `repo.bundle` — `git bundle create` of the base repo state the worktree was
  cut from (captured against the small throwaway fixture repo to stay tiny).
- `leerie_dir/` — frozen `subtasks/<sid>.json`, `artifacts/`, provision recipe —
  everything the worker's `user_content` references under `LEERIE_DIR`.
- `env.json` — `{ "cwd_rel": "...", "allowed_tools": "...", "add_dirs_rel": [...],
  "build_cmd": "...", "lint_cmd": "...", "test_cmd": "...", "diff_base": "...",
  "autonomous": true }`.

### 6.2 `corpus_capture` / `leerie corpus capture`

`leerie corpus capture --from <run-id> [--call-type ...] [--case <name>] [--tier text|env|all]`:

1. Read `<state-root>/runs/<run-id>/calls.ndjson`; select records by
   `call_type`, defaulting to a `success && parsed_ok` filter so the corpus
   starts from known-good behavior.
2. Write each selected record as `cases/<call_type>/<case_id>.json`.
3. For acting-worker (`tier=env`) cases, snapshot the environment into
   `fixtures/<case_id>/`: `git bundle` the base SHA, copy the referenced
   `LEERIE_DIR` subtree, write `env.json`.
4. Run `phase_regress` once against the *current* prompts to measure and pin
   `baseline_pass_rate`, `prompt_sha`, and `judge_prompt_sha` into
   `manifest.json`.

`leerie corpus list` prints the manifest summary (per-call_type tier, case
count, baseline pass-rate, tolerance).

### 6.3 `phase_regress`

```
async def phase_regress(corpus_dir, out_dir, caps, st, models, efforts,
                        tier="all", call_types=None) -> dict
```

For each selected case:
- **Tier 1 (text):** `n_text` × `replay_capture(record,
  override_system_prompt=load_prompt(call_type))` → `judge_capture` → record
  `passed`.
- **Tier 2 (env):** `n_env` × `replay_in_env(record, fixture,
  override_system_prompt=load_prompt(call_type))` → `judge_capture` → record
  `passed`.

Runs replays in parallel under `asyncio.Semaphore(caps["max_parallel"])` (same
pattern as `phase_judge`). Writes per-case verdicts to
`<out_dir>/<case_id>/…` and a `REPORT.json`. Returns the structured comparison
report from `compare_to_baseline`.

### 6.4 `replay_in_env` (Tier-2)

```
async def replay_in_env(record, fixture, *, override_system_prompt) -> tuple[dict, dict]
```

1. Materialize `fixture/repo.bundle` into a temp clone; cut a fresh git worktree
   at the bundled base (reuse `scripts/*.sh` worktree mechanics).
2. Restore `fixture/leerie_dir/` to a temp `LEERIE_DIR`; rewrite the absolute
   `LEERIE_DIR` path in the record's `user_content` to the temp path.
3. Invoke `claude_p` directly (not `replay_capture`, which is text-only) with:
   `override_system_prompt`, the rewritten `user_content`, the fixture's
   `cwd`/`allowed_tools`/`add_dirs`/`autonomous`, `schema_key=call_type`, and
   `_suppress_capture=True`.
4. Return `(envelope, structured_output)`. The worktree is disposable and
   removed after the replay (each replay is isolated).

Tier-2 replays run real builds/tests → slow and token-costly. Hence lower
`n_env`, higher tolerance, and env tier is opt-in (`--tier env|all`).

### 6.5 `compare_to_baseline` — the deterministic enforcement point

```
def compare_to_baseline(results: dict, manifest: dict) -> dict
```

Pure Python, **no model judgment**:

```
for ct, cfg in manifest["call_types"].items():
    total   = len(cfg["cases"]) * cfg["n"]
    passes  = count of replay verdicts with verdict["passed"] is True for ct
    current = passes / total                       # guard total == 0
    cfg_verdict = "REGRESSED" if current < cfg["baseline_pass_rate"] - cfg["tolerance"] else "OK"
overall = "REGRESSED" if any per-ct verdict == "REGRESSED" else "OK"
return {"overall": overall, "per_call_type": {ct: {"current": …, "baseline": …,
        "tolerance": …, "verdict": …}}, "warnings": [judge_sha_changed, …]}
```

`leerie regress` exits **non-zero** iff `overall == "REGRESSED"`. Empty corpus →
`OK` with a warning. This mirrors `check_convergence`'s `REGRESSED` arm and is
unit-tested deterministically (§9).

### 6.6 CLI surface (`leerie` launcher + arg parsing in `leerie.py`)

- `leerie regress [--tier text|env|all] [--call-type CT ...] [--update-baseline]`
  - Runs `phase_regress`, prints the report, exits non-zero on regression.
  - `--update-baseline` re-pins `manifest.json` (baseline pass-rate + shas) after
    an *intentional* prompt change. Without it, the baseline is read-only.
- `leerie corpus capture …` and `leerie corpus list` (§6.2).

Precedence and config follow existing conventions (CLI > env > `leerie.toml`)
where a knob makes sense (e.g. `LEERIE_REGRESS_TOLERANCE`); defaults live in
`DEFAULT_CAPS`-adjacent module constants
(`REGRESS_TOLERANCE_DEFAULT`, `REGRESS_N_TEXT_DEFAULT`, `REGRESS_N_ENV_DEFAULT`).

### 6.7 `.github/workflows/regress.yml`

- Triggers: `workflow_dispatch` (with a `tier` input, default `text`) and an
  optional nightly `schedule`.
- Runs on a **self-hosted runner with `claude` authenticated**.
- Default job: `leerie regress --tier text`; env tier opt-in via the
  `workflow_dispatch` input (cost).
- Writes the `REPORT.json` summary to `$GITHUB_STEP_SUMMARY`. Can be configured
  to fail the job on regression (self-hosted only). Does **not** gate hosted PR
  checks.

## 7. Edge cases & correctness notes

- **`{{include:}}` partials:** because replay calls `load_prompt(ct)` fresh,
  editing `prompts/_clarification_filter.md` (included by classifier/implementer)
  is caught automatically.
- **Editing the judge rubric:** `prompts/judge.md` is the measuring instrument.
  Changing it invalidates baselines. The gate compares `judge_prompt_sha` and
  emits a loud warning requiring `--update-baseline` (re-baseline). This is the
  one case where a "regression" is really "you changed the ruler."
- **Nondeterminism:** absorbed by `n` replays per case + per-call_type
  tolerance. Env tier uses higher tolerance / it is the noisier path.
- **Corpus is a sample, not exhaustive** (stated honestly, §16 tone): a green
  gate means "no regression on the captured cases," not "no regression
  anywhere."

## 8. §12 compliance ("prompts are advisory, code enforces")

The gate is *measurement, not trust*:

- The judge rubric (`prompts/judge.md`) is advisory — a prompt. ✔ allowed.
- The verdict *accounting* (counting `passed`), the pass-rate computation, the
  baseline comparison, and the exit code are **real Python** in
  `compare_to_baseline` — no model judgment in the decision. ✔ enforced.
- The gate never trusts a worker's self-report; it independently re-executes and
  re-judges. ✔ outcome-checked, like the confidence-gate pattern in §12.

This is the same shape §14 already documents for judge ("verdict accounting is
real Python") and heal ("convergence check … is real Python"). The gate extends
that pattern from a single run to a committed, versioned baseline.

## 9. Testing plan

Deterministic, stub-driven — no live `claude` in the unit suite (consistent with
`claude_p` being out of the unit tier):

- `test_compare_to_baseline.py` — regression / no-regression / tolerance-edge /
  empty-corpus / total==0 guard / multi-call_type (one regresses, one improves).
- `test_corpus_manifest_validator.py` — `_validate_corpus_manifest`
  accept/reject, mirroring `_validate_run_json` tests.
- `test_phase_regress_e2e.py` — stubbed worker + stubbed judge, end-to-end
  capture→replay→compare for a Tier-1 case (mirrors `test_heal_pipeline_e2e.py`).
- `test_replay_in_env.py` — Tier-2 worktree reconstruction from a tiny committed
  `repo.bundle` fixture, asserting the worker is invoked with the reconstructed
  `cwd`/`LEERIE_DIR` and the current prompt (bash-harness style for the worktree
  bits).
- A coupling test (like the existing retry-policy marker test) asserting the
  comparator's `REGRESSED` semantics stay consistent with `check_convergence`.

## 10. Three-layer doc propagation (per `CLAUDE.md`)

Implementation changes the highest layer first, then propagates down:

1. **`DESIGN.md §14`** ("Telemetry, judging, self-healing") gains a
   "Behavioral regression gate / golden corpus" subsection: the corpus concept,
   the two-tier replay, the committed baseline, and the §12 framing. This is an
   *architecture addition* — it must land first.
2. **`IMPLEMENTATION.md §8`** (phase overview table) gains the `phase_regress`
   row; **§10** (telemetry) gains the corpus format, `corpus_capture`,
   `replay_in_env`, `compare_to_baseline`, and the CLI/workflow surface;
   the install/quick-start surface gains the `regress`/`corpus` verbs.
3. **Code** in `orchestrator/leerie.py`, the `leerie` launcher, and
   `.github/workflows/regress.yml`, conforming to the updated spec.
4. **`CLAUDE.md`** task-completion checklist gains a corpus-manifest validity
   check, and the Testing section lists the new deterministic tests.

No new `SCHEMAS` entry is required (the gate reuses `SCHEMAS["judge"]`).

## 11. Future extensions (explicitly deferred)

- **Diff-aware acting-worker judging:** feed the git diff an acting worker
  produced into the judge's `user_content` for a richer behavioral signal.
  v1 judges the returned envelope only.
- **Path-filtered self-hosted PR gate:** once a self-hosted authed runner is
  trusted, trigger on `prompts/**` / `SCHEMAS` changes and block the PR.
- **Auto-rebaseline PRs:** a `--update-baseline` run that opens a PR with the new
  manifest when a prompt change is intentional and improves pass-rate.

## 12. Acceptance criteria

**Increment A — Tier 1 (judgment workers), full gate end-to-end:**
- [ ] `corpus/` format + `_validate_corpus_manifest` + validator tests.
- [ ] `leerie corpus capture` promotes Tier-1 records and pins a baseline.
- [ ] `phase_regress` text path + `compare_to_baseline` + deterministic tests.
- [ ] `leerie regress` exits non-zero on a seeded regression, zero otherwise.
- [ ] `.github/workflows/regress.yml` (text default) runs on a self-hosted authed
      runner and reports to the step summary.
- [ ] DESIGN §14 / IMPLEMENTATION §8/§10 updated first; `pytest tests/` green;
      AST check passes.

**Increment B — Tier 2 (acting workers):**
- [ ] `corpus capture --tier env` snapshots `repo.bundle` + `leerie_dir/` +
      `env.json` against the committed throwaway fixture repo.
- [ ] `replay_in_env` reconstructs an isolated worktree + `LEERIE_DIR` and
      re-executes the worker with the current prompt.
- [ ] `leerie regress --tier all` covers acting workers; env-tier defaults
      (`n_env=3`, tolerance `0.20`) applied.
- [ ] Tier-2 reconstruction test green; docs propagated.

## 13. References

- `orchestrator/leerie.py`: `_capture_call:6234`, `claude_p:6344` (envelope
  `:6484–6499`, `load_prompt:75`), `replay_capture:6594`, `judge_capture:6859`,
  `phase_judge:6910`, `SCHEMAS["judge"]:1058`, `heal_baseline:7033`,
  `check_convergence:7243` (`REGRESSED:7292`), heal constants `:590–594`.
- `docs/DESIGN.md`: §12 (`:2307–2383`, deterministic enforcement), §14
  (`:2544–2623`, telemetry/judge/heal), §16 (`:2703–2800`, verification status).
- `docs/IMPLEMENTATION.md`: §8 (phase overview), §10 (telemetry; `calls.ndjson`,
  `replay_capture`).
- CI: `.github/workflows/{test,syntax,shellcheck}.yml` (no baseline gate today).
