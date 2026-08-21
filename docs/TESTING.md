# Testing — per-feature coverage inventory

This file holds the exhaustive, per-feature/per-incident test-coverage
narrative that used to live inline in `CLAUDE.md`'s `## Testing` section.
`CLAUDE.md` keeps only the operational essentials a session needs before
running the suite (concurrency hazards, environment gating, the
subreaper/source-mutation traps, and the reusable authoring lessons); this
file is the detailed record of what each test module covers and why, for
when you need to know whether a given behavior already has a test guarding
it. See CLAUDE.md's "Commit messages are the permanent record" section for
why this kind of per-incident detail belongs in a durable reference (or in
git history) rather than perpetually accreting in the file every session
loads.

## Handler robustness, timeout classification, derivation-guard scope, and pattern ablation

**A handler must SURVIVE its own exception, not merely catch it.**
`main()`'s `except DiskLowSpace` arm opened with an unguarded `st.save()`
— and `State.save()`'s own out-of-space conversion is one of the three
raise sites, so on the disk-full path that call re-entered the failure and
raised again *from inside the handler*. A sibling `except` of the same
`try` does not see an exception raised in another arm's body, so it
escaped `main()`, skipping the cleanup, the dep capture and the
`EXIT_LOCKED` assignment: an exit-1 traceback where the whole arm exists
to produce a resumable pause. Three lessons worth keeping. First,
the arm's own comment already documented this hazard for the
`dep_capture` call ten lines below and guarded that one — the likelier
re-raiser went unguarded because the comment named only one of the raise
sites, which is why `test_survives_a_save_that_is_still_failing`
in `tests/test_disk_preflight.py` asserts against *every* save in the arm
rather than pinning one call. Second, **fixing one arm was not the fix**:
eight other handlers in `main()` carried the identical bare `st.save()`,
including the catch-all `except BaseException`, where a raise REPLACES the
unhandled exception and leaves the real bug reachable only as
`__context__`, which nothing prints. They all now route through
`_save_state_best_effort`, which logs and never raises — deliberately
broader than `except DiskLowSpace`, because a read-only run dir raises
`PermissionError` (measured) and no conversion touches it.
Third, the test it replaced asserted only
`issubclass(DiskLowSpace, BaseException)` and concluded "no separate
save()-specific handler is required" — a tautology that *reasoned* its
way to a false conclusion. Reaching a handler was never the question.

**A timeout is infrastructure, not a leerie bug.** `_invoke` converts
`asyncio.TimeoutError` into `subprocess.TimeoutExpired`, which is an
`Exception` but **not** a `WorkerError` — so every `_run_checked_loop`
caller took its `except Exception: … break` arm ("anything else is a bug
in leerie itself") and `die()`d instead of retrying, and five bare
`except WorkerError` sites let it escape to `main()`'s terminal handler
entirely. Both pre-date the per-worker timeout table; the table made them
~4x more reachable for the 18 worker types whose ceiling it lowered,
while the stated motivation was a hung `classifier`. The retry arm and
those five sites now name `subprocess.TimeoutExpired` alongside
`WorkerError`. **Never interpolate one into a message**: `str()` on a
`TimeoutExpired` renders `cmd`, i.e. the entire `claude -p` argv with an
inlined system prompt — the 50 KB terminal dump `_run_implementer`'s
handler documents. `_brief_worker_exc` exists for exactly this and names
`exc.timeout` instead; `tests/test_checked_loop.py` pins both the retry
and that the argv never reaches a warning line.

**Derivation guards are one-directional unless you write the converse.**
Both `TIMEOUT_DEFAULT_PER_WORKER` tests iterated the *table* ("every
shipped entry is reproducible"), so deleting five entries passed the whole
suite while those workers silently reverted to the 5400 s global.
`test_every_measured_worker_below_the_cap_is_IN_the_table` iterates the
measured summary instead. The same shape bit `main()`'s caps wiring: its
guard asserted the value line plus `"args.worker_timeout"` — which appears
*on* that value line — so deleting the explicitness assignment left it
green and restored the silent-no-op defect. The three minimal entrypoints
had both halves asserted; `main()`, the primary path, did not.

**Ablate a pattern against its corpus instead of adding alternatives.**
`_host_finalize_is_auth_or_network_push_error` carried 11 alternatives;
removing each in turn showed only three were load-bearing for the 9 real
git cases, and four of the rest (`could not resolve host`, `connection
refused|timed out`, `operation timed out`, `no route to host`) were pure
false-positive surface — unreachable for real git behind the
`^(fatal|remote):` anchor, which emits them on an unprefixed `ssh:` line
or inside an `unable to access '<url>':` line already matched. Dropping
them kept 9/9 and fixed three hook misclassifications. Separately,
`_host_finalize_ssh_transport_failure` was **provably dead**: its
companion condition was a line the first arm already matched, and the
first arm ran first. A second arm that cannot change an answer is worse
than no arm, because three places documented it as the discriminator.
