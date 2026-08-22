# Security Policy

## Supported versions

Leerie is pre-1.0; only the latest minor release line receives fixes.

| Version | Supported |
|---------|-----------|
| 0.2.x   | yes       |
| < 0.2   | no        |

The public surface (CLI flags, `.leerie/` layout, worker schemas,
`leerie.toml` keys) may change between minor versions. Pin a commit if
you need stability.

## Reporting a vulnerability

Email **andres@enricai.com** with the subject prefix **`[leerie-security]`**.
Do not open a public GitHub issue or pull request for a suspected
vulnerability. Include a description of the issue and its impact, a
minimal reproduction (task, repo state, invocation), the commit you
reproduced on, and your contact info for follow-up.

What to expect: acknowledgment within 7 days, a coordinated disclosure
timeline negotiated with you (typically 30–90 days depending on severity
and fix complexity), and credit in the
[GitHub Release](https://github.com/enricai/leerie/releases) notes unless
you ask to remain anonymous.

## Threat model context

Acting workers run `claude -p --dangerously-skip-permissions` — intentional,
since that's what makes a run unattended. The mitigation is not removing
the flag; it is the worktree isolation and staging-branch review
([`docs/DESIGN.md`](docs/DESIGN.md) §6), the container PID-namespace and
cgroups boundary containing every worker subprocess (also DESIGN §6), and
the deterministic enforcement boundary in
[`docs/DESIGN.md`](docs/DESIGN.md) §12. See also the
[README "Safety" section](README.md#safety).

### Vulnerabilities (please report)

Any defect that violates the documented isolation or enforcement boundary:

- **Worktree escape** — a script in `scripts/*.sh` resolving `..` or a
  symlink into the main checkout, letting a worker write outside its
  worktree
- **State-write vulnerabilities** — `validate_resume_state()` or
  `State.save()` exploitable via a poisoned `.leerie/` directory (e.g.,
  a poisoned `.leerie/state.json` making `resume` do something unintended)
- **Command injection** — unsanitized expansion in `scripts/*.sh` that
  lets a task description, repo name, or filename inject shell commands
- **Auto-merge bypass** — a subtask branch landing on the run branch
  (`leerie/runs/<id>`) without the documented integrator gates, or the
  run-branch validation step being skipped. (Phase 6 pushes the run
  branch and opens a PR — the human review on that PR is the
  user-facing safety boundary, not the merge into staging.)
- **Schema bypass** — a worker's output consumed without passing
  through its `SCHEMAS` entry (see CLAUDE.md "Mandatory requirements")

### Not vulnerabilities

Accepted risks of running Leerie as designed; please don't report these:

- **A worker doing something destructive inside its own worktree** —
  expected under `--dangerously-skip-permissions`, bounded by worktree
  isolation. Review staging before merging.
- **A worker's commit being merged into staging by the integrator** — by
  design; the safety boundary is the user's review of the phase-6 PR, not
  staging.
- **Running on a repository whose `claude` CLI is misconfigured** —
  Leerie does not validate the user's `claude` credentials or
  permissions; this is upstream of the orchestrator.
- **High disk usage from worktrees** — each subtask gets its own
  worktree; resource consumption is operational, not adversarial.
