# Contributing to Leerie

Thanks for considering a contribution. Leerie is small on purpose — a
single-file Python orchestrator, stdlib-preferred (deps pinned in
`requirements.txt`, `pytest` the only dev dependency), running inside a
container (containerd via Colima on macOS, native on Linux; see
[`docs/INSTALL.md`](docs/INSTALL.md)). A good contribution preserves that
shape: a focused fix or clearly-bounded feature that fits the documented
architecture, with tests and docs updated to match.

## Before you change anything: read the three-layer rule

See [`CLAUDE.md`](CLAUDE.md) § The three-layer rule — read it before opening
a PR that touches more than a single layer.

## Development setup

```bash
git clone https://github.com/enricai/leerie.git
cd leerie
pip install -r requirements.txt   # runtime deps — the test suite imports them
pip install pytest jsonschema     # pytest is the only dev (host-side) dependency
./leerie version           # smoke-check; uses the launcher's fast path —
                           # does NOT require the container runtime, so
                           # it works on a fresh clone with no Colima.
```

Running leerie against a real task (`./leerie "..."` rather than `version`)
requires the container runtime — see [`docs/INSTALL.md`](docs/INSTALL.md).
Iterating on `orchestrator/leerie.py` and running `pytest tests/` needs
only the host Python with `requirements.txt` installed (the suite imports
the orchestrator directly, so `pytest` alone is not enough).

There is no `pyproject.toml`; contributors develop out of the checkout.
End-users get a one-command install via the Claude Code plugin marketplace
or `scripts/install.sh` — see [README *Install*](README.md#install). A
committed `leerie.toml` at the repo root pins this repo's own state to
`~/.leerie/_self/`, avoiding a collision with the installer's
`~/.leerie/leerie/` clone location; other repos are unaffected.

## Running the tests

```bash
pytest tests/
```

The suite covers the deterministic enforcement functions. See
[`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) §10 for what is covered
and what is deliberately out of scope (the live `claude -p` invocation path
is not unit-tested).

## The task-completion checklist

Before opening a PR, run the checklist in `CLAUDE.md` § Task completion
checklist.

## Commit and PR conventions

- **Conventional commit prefixes:** `chore:`, `feat:`, `fix:`, `docs:`,
  `refactor:`, `test:`, `ci:`. Match the existing git log.
- **One commit per logical change.** Resist bundling — independently
  revertible changes get separate commits.
- **PR description should call out which layer(s) of the three-layer rule
  the change touches**, and confirm it propagated top-down if more than one.

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
