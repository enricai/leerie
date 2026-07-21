# Contributing to Leerie

Thanks for considering a contribution. Leerie is small on purpose — a
single-file Python orchestrator, stdlib-preferred on the Python side
(runtime deps pinned in `requirements.txt` and listed in
[`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) §0), with one dev
dependency (`pytest`). The orchestrator runs inside a
container (containerd via Colima on macOS, native on Linux); see
[`docs/INSTALL.md`](docs/INSTALL.md) for the per-OS runtime setup. A good
contribution preserves that shape: a focused fix or a clearly-bounded
feature that fits inside the documented architecture, with tests and docs
updated to match.

## Before you change anything: read the three-layer rule

The repo separates *theory* (`docs/DESIGN.md`), *mechanism*
(`docs/IMPLEMENTATION.md`), and *code* (`orchestrator/leerie.py`), and
the layers are **top-down canonical**: each layer derives from and conforms
to the one above it. Precedence when they disagree: **DESIGN > IMPLEMENTATION
> code**. The lower layer is the defect.

When you change something, change the highest layer that the change touches
*first*, then propagate down. The full version of this rule, and how to
apply it in edge cases, lives in [`CLAUDE.md`](CLAUDE.md). Read it before
opening a PR that touches more than a single layer.

## Development setup

```bash
git clone https://github.com/enricai/leerie.git
cd leerie
pip install -r requirements.txt   # runtime deps — the test suite imports them
pip install pytest jsonschema     # pytest is the only dev (host-side) dependency
./leerie --version           # smoke-check; uses the launcher's fast path —
                           # does NOT require the container runtime, so
                           # it works on a fresh clone with no Colima.
```

Running leerie against a real task (`./leerie "..."` rather than `--version`)
requires the container runtime to be installed and started — see
[`docs/INSTALL.md`](docs/INSTALL.md). Iterating on
`orchestrator/leerie.py` and running `pytest tests/` is possible without
it; the test suite runs on the host Python. Because it imports the
orchestrator directly, the pinned runtime deps in `requirements.txt` must
be installed on that host Python as well — `pytest` is the only *dev*
dependency, not the only dependency the suite needs.

There is no `pyproject.toml`; contributors develop out of the checkout.
End-users get a one-command install via the Claude Code plugin marketplace
or `scripts/install.sh` — see [README *Install*](README.md#install).

A committed `leerie.toml` at the repo root pins this repo's state to
`~/.leerie/_self/` (the launcher's default would otherwise resolve to
`~/.leerie/leerie/`, which is the installer's clone location and would
fail the install-dir guard). The first time you run `./leerie "task"`
from your clone, that dir gets created with `.owner` recording your
clone's absolute path. Other repos you work on are unaffected — the
override is scoped to this checkout via the toml.

## Running the tests

```bash
pytest tests/
```

The suite covers the deterministic enforcement functions. See
[`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) §10 for what is covered
and what is deliberately out of scope (the live `claude -p` invocation path
is not unit-tested).

## The task-completion checklist

Before opening a PR, verify the same checklist that `CLAUDE.md` requires
for any change:

- [ ] `docs/IMPLEMENTATION.md` updated if the change affected code surface
      described there.
- [ ] `docs/DESIGN.md` updated only if the architecture itself changed.
- [ ] `pytest tests/` — all pass.
- [ ] `python3 -c "import ast; ast.parse(open('orchestrator/leerie.py').read())"`
      as a static check.
- [ ] `grep -rn <removed-string> .` — confirm no stragglers if the change
      renamed or removed a string used elsewhere.
- [ ] `git diff --stat` — confirm the diff is scoped to what the change
      intended; no collateral edits.
- [ ] `python3 -c 'import json; json.load(open(".claude-plugin/plugin.json")); json.load(open(".claude-plugin/marketplace.json"))'`
      — if either manifest in `.claude-plugin/` was touched, confirm both
      are valid JSON.

(Mirrors `CLAUDE.md`'s checklist — keep in sync if you change either file.)

## Commit and PR conventions

- **Conventional commit prefixes:** `chore:`, `feat:`, `fix:`, `docs:`,
  `refactor:`, `test:`, `ci:`. Match the existing git log.
- **One commit per logical change.** Resist bundling. If two changes can be
  reverted independently, they should be separate commits.
- **PR description should call out which layer(s) of the three-layer rule
  the change touches** — DESIGN, IMPLEMENTATION, code, docs, tests, CI —
  and confirm the change propagated *top-down* if it touches more than one.

## Code style

See [`CLAUDE.md`](CLAUDE.md) § Code style. There is no linter in CI;
style is enforced by review.

## Reporting bugs and requesting features

- **Bugs:** use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md).
- **Features:** use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md).
  Bear the "stays small" constraint in mind — features that pull the
  orchestrator toward generality at the cost of the single-file shape are a
  hard sell.
- **Security issues:** do not open a public issue. See
  [`SECURITY.md`](SECURITY.md).
