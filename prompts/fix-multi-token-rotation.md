# Smart multi-token rotation: CLAUDE_CODE_OAUTH_TOKENS with runway-based selection and mid-run failover

## Context and intent

Add support for `CLAUDE_CODE_OAUTH_TOKENS` — a comma-separated list of
`CLAUDE_CODE_OAUTH_TOKEN` values — that **supersedes** the singular
`CLAUDE_CODE_OAUTH_TOKEN` when present. The goal is to make a run far less likely to
run out of quota:

1. **Start-of-run smart selection (not round-robin):** when `CLAUDE_CODE_OAUTH_TOKENS`
   is set, probe each token to determine which has the **most runway** (lowest
   session/weekly utilization, furthest-off reset) and pick that one for the run.
2. **Mid-run failover:** if the active token gets rate-limited during the run, rotate
   to another token that still has runway and continue — without restarting the
   container. If ALL tokens are currently limited, pick the one whose window resets
   **soonest**, wait until then, and resume on it (minimize downtime, don't just pause).

This is a leerie orchestrator change (this repo). Read `CLAUDE.md`, `docs/DESIGN.md`
§6 (*Credential strategy* / *Finalization*), and `docs/IMPLEMENTATION.md` first.
Follow the three-layer rule (DESIGN → IMPLEMENTATION → code) and, critically, §12
"prompts advisory, code enforces" + "Python operates only on structured data" — the
probe outputs are structured (JSON fields / typed headers), so ALL ranking and
failover logic is **deterministic Python, with NO LLM worker** (see below).

## Categorization and file ownership (required — read before planning)

This task requires BOTH the `feature-implementation` and `testing` categories.
The `## Tests` section below is substantial new test-authorship work (new bash
harnesses, new stub-based Python test modules), not incidental verification —
it needs its own planner.

To avoid the cross-planner file-collision failure mode (two blind planners
authoring incompatible contracts for the same test file — a documented prior
incident; see CLAUDE.md's `TEST_OWNERSHIP_RISK`), ownership is split **by file**,
not by concept, and is non-overlapping:

- **`feature-implementation` planner owns:** `leerie` (launcher),
  `orchestrator/leerie.py`, `scripts/remote/seed-auth.sh`,
  `scripts/remote/ec2-seed-auth.sh`, `docs/DESIGN.md`, `docs/IMPLEMENTATION.md`.
  It must NOT create or edit any `tests/test_*.py` file or test bash-harness file.
- **`testing` planner owns:** only new/expanded test files — new
  `tests/test_*.py` modules covering probe/ranking, failover, `_invoke` env
  threading, and secrets-hygiene, plus the launcher bash-harness test file for
  `CLAUDE_CODE_OAUTH_TOKENS` forwarding (mirroring `tests/test_launcher_env_forwarding.py`).
  It must NOT touch `leerie`, `orchestrator/leerie.py`, or either doc file — it
  reads them for context only.

### Required `requires` edges for test subtasks (do not omit)

Every `testing`-planner subtask consumes a capability a `feature-implementation`
subtask provides. Declare the edge explicitly — do not leave it to be inferred —
so the plan-wiring gate doesn't have to guess it:

- Any test asserting env-threading behavior (`_invoke` spawning with the active
  token, `st.data["active_oauth_token"]`) → `requires: invoke-token-env` (the
  capability the per-invocation env-threading feature subtask provides).
- Any test exercising probe/ranking output, or asserting a consumer of that
  output uses the real probe/ranking helpers (not a hand-rolled stand-in) →
  `requires: token-probe-ranking` (the capability the probe/ranking feature
  subtask provides).
- Any test covering the mid-run failover/rotation code path in `claude_p`'s
  backoff loop → `requires: token-failover` (the capability the mid-run
  failover feature subtask provides).
- The secrets-hygiene test needs BOTH `token-probe-ranking` (it audits the
  start-of-run selection path) AND `token-failover` (it must also audit the
  mid-run rotation path — a separate code path that can leak tokens
  independently of the first).
- The launcher bash-harness forwarding test needs `requires: launcher-multi-token`
  (the launcher-parsing feature subtask, since it tests the forwarding logic
  directly) AND `requires: multi-token-design-docs` (the feature subtask that
  updates `docs/DESIGN.md`/`docs/IMPLEMENTATION.md` for this feature, so the
  test verifies against the documented contract, not an assumed one).

Whichever `feature-implementation` subtask updates `docs/DESIGN.md` §6 and
`docs/IMPLEMENTATION.md` to describe multi-token selection + failover (see
"Definition of done" below — docs must land BEFORE the code) must declare
`provides: multi-token-design-docs`. This is the producer side of the
`requires: multi-token-design-docs` edge above.

## Feasibility (researched — build the prompt on these facts, not assumptions)

Runway IS probeable for a Claude subscription token, cheaply, via one of two
mechanisms (try A, fall back to B):

- **Probe A — the OAuth usage endpoint (zero inference cost):**
  `GET https://api.anthropic.com/api/oauth/usage` with
  `Authorization: Bearer <token>`, `anthropic-beta: oauth-2025-04-20`, and a
  `User-Agent: claude-code/<version>` (CRITICAL — omitting the User-Agent yields
  instant persistent 429s). Returns `five_hour.{utilization, resets_at}`,
  `seven_day.{utilization, resets_at}`, and per-model `seven_day_opus` /
  `seven_day_sonnet`. `utilization` is 0–100; `resets_at` is ISO-8601 UTC. Requires
  `user:profile` scope — a `claude setup-token` (scope `user:inference`, which is what
  leerie uses) gets **403** here.
- **Probe B — a minimal inference call, read the unified rate-limit headers** (works
  for setup-tokens): `POST /v1/messages` with `model=<any>`, `max_tokens=1`,
  `messages=[{role:"user", content:"."}]`; read
  `anthropic-ratelimit-unified-5h-utilization` / `-5h-reset`,
  `-7d-utilization` / `-7d-reset`, `-5h-status`. Costs ~1 output token. (These
  headers are NOT on `count_tokens` — a real messages call is required.)
- **Robust rule: try Probe A; on 403 fall back to Probe B.**

**Rank tokens by runway:** `min(1 − five_hour_util, 1 − seven_day_util)` (higher =
more runway), tie-break by furthest-off `resets_at`. Account for per-model weekly
sublimits (`seven_day_opus`/`seven_day_sonnet`) — leerie's judgment workers default to
Opus, so a token near its Opus weekly cap has less usable runway than its aggregate
suggests.

**HARD CAVEATS (shape the design — do not ignore):**
- Both endpoints are **UNDOCUMENTED and UNSTABLE** — they can change/break without
  notice. Treat every probe as **best-effort telemetry, NEVER a hard gate.** A probe
  failure must degrade gracefully, never fail the run (see the fallback rule below).
- **Probe A rate-limits aggressively, per access token.** CACHE each token's probe
  result for ≥180 s; do not re-probe per worker spawn.
- Access tokens expire ~60 min — the probe naturally surfaces a dead token (it errors),
  which is itself useful selection signal.

## How leerie handles the token today (the surface you're changing)

- **Launcher (`leerie`):** `_extract_claude_credentials_json` (`leerie:145–172`)
  synthesizes `{"claudeAiOauth":{"accessToken":<token>,"scopes":["user:inference"]}}`
  from `CLAUDE_CODE_OAUTH_TOKEN` (env wins over Keychain over on-disk file). The token
  is forwarded into the container UNCONDITIONALLY as `-e CLAUDE_CODE_OAUTH_TOKEN`
  (`leerie:5520–5522`) AND written to a mounted `.credentials.json` (`leerie:5525–5528`,
  mount at `5746`). `_check_claude_credential_ttl` (`leerie:201–253`) is a launcher
  preflight (no-op for the synthesized blob, which has no `expiresAt`).
- **Orchestrator:** `claude -p` gets the token PURELY by process-env inheritance from
  the container. `_invoke` (`orchestrator/leerie.py:10803`) spawns each worker with
  `env=worker_env`, and `worker_env` is currently **`None`** except under
  `LEERIE_WORKER_DEBUG` (`10849–10860`, `worker_env = None` at `10856`) — so workers
  inherit the container env untouched. **No token is passed per-invocation today.**
- **Rate-limit machinery already exists (reuse it, don't reinvent):**
  `RateLimitedExit` (`2290`), `TerminalAuthFailure` (`2329`); classifiers
  `_api_error_category` (`9944`, `{401:auth,429:quota,529:overload}`),
  `_is_auth_or_quota_failure` (`9999`), `_is_terminal_auth_failure` (`9959`);
  `detect_session_limit` (`2379`); the `rate_limit_event` stream carrying
  `utilization`/`surpassedThreshold`/`resetsAt`/`overageDisabledReason` (latched at
  `10940–10945`, protocol rate-limit raise at `10416–10445`); the `claude_p` tenacity
  backoff loop (`12006–12078`, `auth_retry_max_sec` default 300 at `214`); and the
  `main()` handlers (`23934` TerminalAuthFailure→EXIT_LOCKED, `23970` RateLimitedExit→
  auto-resume-after-reset or EXIT_LOCKED pause).

## The single load-bearing prerequisite

**Per-invocation token switching requires threading a per-call env through `_invoke`.**
Today `_invoke` passes `env=None` (`orchestrator/leerie.py:10856`). Change it so each
`claude -p` spawn runs with the CURRENTLY-SELECTED token:
- Maintain the active token in orchestrator state (a module-level/`State` field, e.g.
  `st.data["active_oauth_token"]`).
- In `_invoke`, build `worker_env = os.environ.copy()` and set
  `worker_env["CLAUDE_CODE_OAUTH_TOKEN"] = <active token>` before
  `create_subprocess_exec(..., env=worker_env)`. (The CLI re-reads env per spawn, so no
  container restart is needed. If leerie also relies on the mounted
  `.credentials.json`, either rewrite that file on switch OR ensure the env var wins —
  verify which the CLI prefers and document it.)
This one change is what makes both the start-selection and the mid-run failover real.

## Required implementation

### 1. Launcher: parse and forward the list

- `CLAUDE_CODE_OAUTH_TOKENS` (comma-separated) SUPERSEDES `CLAUDE_CODE_OAUTH_TOKEN`
  when set. Forward the whole list into the container as a new
  `-e CLAUDE_CODE_OAUTH_TOKENS=...` alongside the existing single-token `-e`
  (`leerie:5520–5522`). Keep the single-token path fully working when the plural is
  unset (no regression). For the mounted `.credentials.json` file-write
  (`5525–5528`), seed it from the FIRST token of the list (or the probe-selected one if
  you probe host-side) — the orchestrator will override per-invocation anyway.
  Implement this by setting `CLAUDE_CODE_OAUTH_TOKEN` to the first list element (or
  the probe-selected one) before the existing `_extract_claude_credentials_json`
  call at `leerie:5523` runs — do NOT construct `.credentials.json` via a
  separate/parallel code path. That function's env-var branch is what already gets
  the mcpOAuth-only rejection guard, the die()-fast diagnosis, and
  `_check_claude_credential_ttl` for free; a parallel path would silently bypass
  all three.
- Trim whitespace around each comma-separated entry; ignore empty entries; a
  single-element list behaves exactly like the singular var.
- Also forward `CLAUDE_CODE_OAUTH_TOKENS` on the Fly/EC2 seed paths
  (`scripts/remote/seed-auth.sh`, `ec2-seed-auth.sh`) so remote runtimes get the list
  too — mirror the existing single-token fallback blocks.

### 2. Start-of-run probe + selection (pure Python, best-effort)

- Hook location: in/after `preflight()` (def at `orchestrator/leerie.py:5231`, called
  at `22773`) and BEFORE `phase_classify` (def at `13587`) — `preflight` already does
  an auth smoke test, so this is the natural "which token" spot. Only runs when
  `CLAUDE_CODE_OAUTH_TOKENS` is present (else the singular path is unchanged).
- For each token: run Probe A (`/api/oauth/usage`), fall back to Probe B
  (`max_tokens=1` messages call + unified headers) on 403. Parse the structured result.
  Cache per token ≥180 s (a `State`/module dict keyed by a token fingerprint — NEVER log
  or persist the raw token; use a hash/last-6 for identity).
- Rank by runway (`min(1−5h_util, 1−7d_util)`, per-model-aware, `resets_at` tie-break);
  set the winner as the active token (§ prerequisite).
- **Best-effort:** if probing fails for ALL tokens, DO NOT fail the run — pick the
  first token and proceed, relying on mid-run 429-failover. Never `die()` on a probe
  failure. The reliable backbone is react-on-429; the probe is an advisory layer.
- **Two-tier logging — distinguish transient flakiness from an endpoint that CHANGED
  SHAPE (do NOT lump them together).** These are different failures and must log
  differently, because a silent shape-drift would leave the whole probe feature dead
  (degraded to first-token-always) for weeks with nobody noticing:
  - **Transient** (timeout, connection error, 5xx, a 429 on the probe itself): expected;
    log QUIETLY (low verbosity / debug). Retry is optional; just fall back.
  - **Shape/contract drift** (a 2xx response whose body/headers no longer contain the
    expected fields — missing `five_hour.utilization`/`resets_at`, or the
    `anthropic-ratelimit-unified-*` headers absent): the undocumented endpoint has
    CHANGED. Log LOUDLY at WARNING (an unmissable, distinct message naming which probe
    and which field went missing) so the operator learns the probe needs updating —
    NOT a quiet degrade. This is the signal that the probe code must be revised.
  - Treat a 401/expired-token as neither of the above — it is a real per-token signal
    (that token is dead / unusable), useful for selection, and logged as such.
  - Emit a distinct, greppable marker for the shape-drift case (e.g. a stable string
    like `token-probe: endpoint contract drift` + the missing field) so it is easy to
    alert on and to find in logs.
- Use stdlib HTTP (`urllib.request`) — no new runtime dep (leerie is stdlib-preferred).

### 3. Mid-run failover (pure Python, in the claude_p backoff loop)

- Hook location: `claude_p`'s backoff branch (`orchestrator/leerie.py:12006–12078`,
  inside `claude_p` def at `11755`), where `_is_auth_or_quota_failure` / the
  `rate_limit_event` latch already detect a
  429/quota condition. Terminal-auth (`_is_terminal_auth_failure`) is NOT a rate-limit —
  leave that path (→ TerminalAuthFailure) unchanged.
- On a rate-limit for the active token: probe/rank the OTHER tokens (respect the ≥180 s
  cache). If one has runway, **switch the active token and retry the invocation** (no
  container restart) BEFORE spending the `auth_retry_max_sec` backoff budget on the same
  dead token. This is a new rotate arm ADDED before the existing backoff/pause — nothing
  existing regresses.
- **If ALL tokens are currently rate-limited:** pick the one whose window resets
  **soonest** (min `resets_at` across the exhausted set, from the probe/headers or the
  `rate_limit_event.resetsAt`), switch to it, and fall through to leerie's EXISTING
  reset-wait auto-resume path (`RateLimitedExit(reset_at=<soonest>)` →
  `_sleep_then_reexec`). This reuses the existing wait/re-exec machinery — you're just
  choosing the soonest-reset token instead of pausing on the one that happened to fail.
- Record which token is active in run state so `--resume` picks up sensibly.
- **Never** hard-fail on a probe error mid-run — if you can't probe the others, fall
  through to today's behavior (backoff/pause) exactly as it works now.

### 4. Config knobs (follow existing precedence patterns)

- No new judgment worker, no schema (structured data → pure Python, §12).
- Add caps to `DEFAULT_CAPS` where relevant (e.g. `token_probe_cache_sec` default 180)
  with the standard CLI/env/leerie.toml precedence if a knob is warranted; keep it
  minimal.
- Secrets hygiene: NEVER log a raw token; identify tokens by a short fingerprint. Do
  not write tokens to `state.json`/`run.json`/`calls.ndjson` (audit the telemetry
  writer `_capture_call` if you touch the env plumbing).

## Constraints

- **Best-effort everywhere:** the undocumented endpoints may break; every probe path
  must degrade to "use current/first token + react-on-429", never `die()`.
- **No regression for the singular var:** `CLAUDE_CODE_OAUTH_TOKEN` alone (no plural)
  must behave exactly as today — same forwarding, same TTL preflight, same handlers.
- Evaluate the env-plumbing change across all three runtimes (local rootless
  containerd, rootful Colima, Fly/EC2) per CLAUDE.md — the token must reach the CLI on
  each. Verify whether the CLI prefers env or the mounted `.credentials.json` and make
  the switch authoritative on the winning channel.
- stdlib-preferred (no new runtime deps for the probe — `urllib.request` + `json`).
- bash 3.2 portability for any launcher changes (no `local -n`/`declare -n`).

## Tests

All bullets below are owned by the `testing` planner (new test files only — see
"Categorization and file ownership" above). The `feature-implementation` planner
does not author these; it only needs to keep the surfaces below testable (e.g.
don't hide logic where it can't be stubbed).

- Launcher (bash-harness, stubbed; new test file): `CLAUDE_CODE_OAUTH_TOKENS`
  supersedes the singular; forwarded as `-e`; single-element list == singular
  behavior; whitespace/empty-entry handling; singular-only path unchanged;
  env-forwarding denylist not leaking tokens where it shouldn't (mirror
  `tests/test_launcher_env_forwarding.py`).
- Probe/ranking (pure Python, stub `urllib` responses; new
  `tests/test_token_probe.py`-style file): Probe A parsed correctly; 403 →
  Probe B fallback; ranking picks the lowest-utilization/furthest-reset token;
  per-model Opus sublimit respected; cache honored (no re-probe within 180 s);
  ALL-probes-fail → first token chosen, no exception.
- Failover (stub `_invoke` envelopes, mirror `tests/test_no_result_event_retry.py` /
  `test_terminal_auth_routing.py`; new test file): a 429 on the active token rotates
  to a token with runway and retries; all-limited → soonest-reset token chosen +
  existing reset-wait path taken; terminal-auth still routes to TerminalAuthFailure
  (NOT rotated); a probe failure mid-run falls through to today's backoff/pause with
  no exception.
- `_invoke` env threading (new test file, or a new test class if one of the above
  files is the natural home): each spawn carries the active token in its env;
  switching the active token changes the env of the NEXT spawn (the prerequisite is
  actually wired).
- Secrets (new test file): a test asserting no raw token appears in
  `state.json`/`run.json`/`calls.ndjson` or logs (fingerprint only).

## Definition of done (verify behavior end-to-end — do not self-certify on "tests pass")

- With two real tokens in `CLAUDE_CODE_OAUTH_TOKENS`, a run probes both at start, logs
  the chosen one by fingerprint + its runway, and uses it — demonstrate the probe
  actually ran and selection happened (not just a unit test).
- Simulate a mid-run 429 on the active token (stub the envelope) and show the run
  rotates to the other token and CONTINUES in the same container (no re-exec) — and that
  when both are limited it waits for the soonest reset rather than pausing immediately.
- A probe-endpoint failure (stub a 500/changed shape) degrades to first-token +
  react-on-429 with no run failure — demonstrate the best-effort fallback.
- The singular `CLAUDE_CODE_OAUTH_TOKEN` path (no plural set) is byte-for-byte unchanged
  in behavior.
- No raw token in any persisted file or log.
- DESIGN §6 *Credential strategy* + IMPLEMENTATION.md updated to describe multi-token
  selection + failover BEFORE the code; `pytest tests/` passes; `ast.parse` clean;
  `git diff --stat` scoped to `leerie`, `orchestrator/leerie.py`,
  `scripts/remote/*seed-auth.sh`, docs, and tests.
