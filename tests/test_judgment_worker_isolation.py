"""DESIGN §12 *Judgment-worker isolation* — the four layers that keep a
judgment worker out of the user's checkout.

The failure this exists to prevent, measured on a real run: a `classifier`
implemented an entire task in the operator's checkout on `main` — `Edit` calls
to three files, a repo-wide `lint:fix`, and a `git stash`/`pop` pair — then
died at its turn cap. `Edit` is not in `INSPECT_TOOLS`; the worker reached it
because `--dangerously-skip-permissions` bypasses `--allowedTools` entirely.

Probed live against claude 2.1.237 (filesystem-verified, four runs):

    configuration                    Write in cwd  Write outside  Bash outside
    INSPECT_TOOLS, flag ON           succeeded     succeeded      succeeded
    INSPECT_TOOLS, flag OFF          rejected      rejected       rejected

and, in the exact shape this feature ships, cwd = a detached worktree with the
flag still set: the worker overwrote the real checkout AND committed on its
branch. That is the result that makes L1 (never grant the flag) the load-bearing
layer and L2 (the worktree) worthless on its own — a worktree is not a boundary,
it is where the boundary lands once L1 restores one.

Every test here pairs a negative with a positive control, because each negative
alone is satisfied by a `claude_p` that is simply broken.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib

import pytest


MODULE = (pathlib.Path(__file__).resolve().parents[1]
          / "orchestrator" / "leerie.py")


@pytest.fixture(autouse=True)
def _clear_blt_verbs_cache(leerie):
    """`_BLT_VERBS_CACHE` is module-level and conftest's `leerie` fixture is
    session-scoped, so without this a verb list computed from one test's tmp
    repo leaks into every later file that exercises the widening — the same
    order-dependence CLAUDE.md records for `_active_admissions`."""
    leerie._BLT_VERBS_CACHE.clear()
    yield
    leerie._BLT_VERBS_CACHE.clear()

_REPO_ROOT_EXPRS = {"str(repo_root)", "os.getcwd()", "repo_root",
                    "str(os.getcwd())"}


def _claude_p_sites() -> list[tuple[int, str, str]]:
    """(lineno, schema_key, unparsed cwd expression) for every claude_p call
    with a literal schema_key. Derived from the AST rather than enumerated:
    a hand-kept list catches a regression on exactly the sites it names and
    nothing else, which is the trap PRs #180-#183 each shipped."""
    tree = ast.parse(MODULE.read_text())
    out: list[tuple[int, str, str]] = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = (f.id if isinstance(f, ast.Name)
                else getattr(f, "attr", None))
        if name != "claude_p":
            continue
        kw = {k.arg: k for k in n.keywords if k.arg}
        sk, cw = kw.get("schema_key"), kw.get("cwd")
        if sk is None or cw is None:
            continue
        if not isinstance(sk.value, ast.Constant):
            continue
        out.append((n.lineno, sk.value.value, ast.unparse(cw.value)))
    return out


# ---------------------------------------------------------------------------
# the partition itself
# ---------------------------------------------------------------------------

class TestWorkerPartition:
    def test_partition_is_exhaustive_and_disjoint(self, leerie):
        """Every worker is classified. A new worker therefore forces an
        explicit decision about which side of the boundary it sits on,
        instead of defaulting into the permissive one."""
        planning = set(leerie.PLANNING_WORKER_TYPES)
        acting = set(leerie.ACTING_WORKER_TYPES)
        assert planning & acting == set(), (
            f"a worker is in both buckets: {sorted(planning & acting)}")
        assert planning | acting == set(leerie.WORKER_TYPES), (
            "PLANNING_WORKER_TYPES | ACTING_WORKER_TYPES must cover "
            f"WORKER_TYPES exactly; unclassified: "
            f"{sorted(set(leerie.WORKER_TYPES) - planning - acting)}")

    def test_the_workers_that_actually_write_code_are_not_in_the_planning_bucket(
            self, leerie):
        """Anti-vacuity for the sweep below: if `implementer` ever landed in
        PLANNING_WORKER_TYPES the cwd sweep would still pass (implementers
        already run in a worktree) while silently stripping the permission
        flag that makes unattended execution work at all."""
        for w in ("implementer", "conformer", "integrator", "rebaser"):
            assert w not in leerie.PLANNING_WORKER_TYPES


# ---------------------------------------------------------------------------
# L2 — no judgment worker is handed the real checkout (static sweep)
# ---------------------------------------------------------------------------

class TestNoJudgmentWorkerRunsInTheCheckout:
    def test_scan_finds_the_call_sites(self, leerie):
        """A scan that matches nothing certifies everything."""
        sites = _claude_p_sites()
        assert len(sites) >= 20, (
            f"only {len(sites)} claude_p sites found — the AST scan is broken")
        seen = {s for _, s, _ in sites}
        assert "implementer" in seen and "conformer" in seen, (
            "the scan did not see the acting workers; it is not reading the "
            "real call sites")
        assert seen & set(leerie.PLANNING_WORKER_TYPES), (
            "the scan saw no judgment workers at all")

    def test_no_planning_worker_is_given_the_real_repo_root(self, leerie):
        bad = [(ln, sk, cwd) for ln, sk, cwd in _claude_p_sites()
               if sk in leerie.PLANNING_WORKER_TYPES
               and cwd in _REPO_ROOT_EXPRS]
        assert not bad, (
            "judgment workers must run in the disposable worktree, never the "
            f"user's checkout (DESIGN §12): {bad}")

    def test_acting_workers_are_untouched(self, leerie):
        """The change must not have swept the acting workers along with it —
        they legitimately run in the worktree they own, named by a local."""
        acting = [(ln, sk, cwd) for ln, sk, cwd in _claude_p_sites()
                  if sk in leerie.ACTING_WORKER_TYPES]
        assert acting, "no acting-worker call sites found"
        for ln, sk, cwd in acting:
            assert cwd not in _REPO_ROOT_EXPRS, (
                f"acting worker {sk} at line {ln} runs in the real checkout")


# ---------------------------------------------------------------------------
# behavioural: drive the real claude_p and read the argv it builds
# ---------------------------------------------------------------------------

class _FakeState:
    def __init__(self, tmp_path, repo_root, *, skip_perms=False):
        self.path = tmp_path / "runs" / "r1" / "state.json"
        self.run_dir = self.path.parent
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = "r1"
        self.repo_root = repo_root
        self.data = {"verbosity": "quiet",
                     "dangerously_skip_permissions": skip_perms}

    def bump_workers(self, *a, **k):
        pass

    def add_telemetry(self, *a, **k):
        pass


_OK = {"type": "result", "subtype": "success", "is_error": False,
       "result": "{}", "structured_output": {"categories": ["testing"]}}


def _drive(leerie, monkeypatch, tmp_path, *, schema_key, cwd, repo_root,
           skip_perms=False, autonomous=False, allowed_tools="Read"):
    """Run the REAL claude_p with only `_invoke` stubbed, and return the argv
    it constructed. Stubbing claude_p itself would prove nothing — a stub
    accepts any argv, which is precisely why this class of bug survives a
    green suite (see tests/test_claude_p_call_sites.py's header)."""
    seen: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **kwargs):
        seen["cmd"] = list(cmd)
        seen["cwd"] = cwd
        return dict(_OK)

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_append_system_prompt_file_supported",
                        lambda: False)

    async def run():
        return await leerie.claude_p(
            "u", "s", schema_key=schema_key, cwd=cwd,
            allowed_tools=allowed_tools, max_turns=5, autonomous=autonomous,
            caps=dict(leerie.DEFAULT_CAPS),
            st=_FakeState(tmp_path, repo_root, skip_perms=skip_perms),
            model="sonnet", sid=schema_key)

    asyncio.run(run())
    return seen


class TestL1PermissionFlag:
    """The load-bearing layer: the flag is unreachable for judgment workers."""

    def test_judgment_worker_never_gets_the_flag_even_when_set(
            self, leerie, tmp_path, monkeypatch):
        seen = _drive(leerie, monkeypatch, tmp_path, schema_key="planner",
                      cwd=str(tmp_path / "wt"), repo_root=tmp_path / "repo",
                      skip_perms=True, autonomous=False)
        assert "--dangerously-skip-permissions" not in seen["cmd"], (
            "a judgment worker was handed the permission bypass; measured, "
            "that lets it Write outside its cwd and commit on the user's "
            "branch")

    def test_acting_worker_still_gets_the_flag(
            self, leerie, tmp_path, monkeypatch):
        """The positive control. Without it, this file would pass against a
        claude_p that never emits the flag for anyone — which would break
        unattended execution entirely."""
        seen = _drive(leerie, monkeypatch, tmp_path, schema_key="implementer",
                      cwd=str(tmp_path / "wt"), repo_root=tmp_path / "repo",
                      skip_perms=False, autonomous=True)
        assert "--dangerously-skip-permissions" in seen["cmd"]

    def test_flag_state_does_not_change_the_acting_worker_argv(
            self, leerie, tmp_path, monkeypatch):
        """`autonomous` alone decides. Pinning this stops a future edit from
        re-introducing the OR that caused the incident."""
        on = _drive(leerie, monkeypatch, tmp_path, schema_key="implementer",
                    cwd=str(tmp_path / "wt"), repo_root=tmp_path / "repo",
                    skip_perms=True, autonomous=True)
        off = _drive(leerie, monkeypatch, tmp_path, schema_key="implementer",
                     cwd=str(tmp_path / "wt"), repo_root=tmp_path / "repo",
                     skip_perms=False, autonomous=True)
        assert on["cmd"] == off["cmd"]


class TestCwdGuard:
    def test_judgment_worker_in_the_real_checkout_is_refused(
            self, leerie, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        with pytest.raises(ValueError, match="disposable worktree"):
            _drive(leerie, monkeypatch, tmp_path, schema_key="planner",
                   cwd=str(repo), repo_root=repo)

    def test_the_same_worker_elsewhere_reaches_invoke(
            self, leerie, tmp_path, monkeypatch):
        """Positive control: the negative above is also satisfied by a
        claude_p that raises unconditionally."""
        repo, wt = tmp_path / "repo", tmp_path / "wt"
        repo.mkdir(), wt.mkdir()
        seen = _drive(leerie, monkeypatch, tmp_path, schema_key="planner",
                      cwd=str(wt), repo_root=repo)
        assert seen["cwd"] == str(wt)

    def test_guard_is_path_based_not_string_based(
            self, leerie, tmp_path, monkeypatch):
        """`realpath`, not string equality — a trailing slash or a symlinked
        state root would otherwise walk straight through it."""
        repo = tmp_path / "repo"
        repo.mkdir()
        with pytest.raises(ValueError):
            _drive(leerie, monkeypatch, tmp_path, schema_key="classifier",
                   cwd=str(repo) + "/", repo_root=repo)

    def test_post_execution_satisfied_probe_rescue_is_not_refused(
            self, leerie, tmp_path, monkeypatch):
        """`satisfied_probe` runs twice: pre-schedule in the judgment
        worktree, and again after execution against a SUBTASK worktree. The
        guard is phrased "not the real checkout" rather than "equals the
        planning worktree" precisely so the second call needs no special
        case — pin that, or a future simplification breaks it."""
        repo, sub = tmp_path / "repo", tmp_path / "sub-wt"
        repo.mkdir(), sub.mkdir()
        seen = _drive(leerie, monkeypatch, tmp_path,
                      schema_key="satisfied_probe", cwd=str(sub),
                      repo_root=repo)
        assert seen["cwd"] == str(sub)


class TestL3AllowlistWidening:
    """The escape hatch, re-expressed: more tools, same boundary."""

    @staticmethod
    def _repo_with_blt(tmp_path):
        repo = tmp_path / "repo"
        (repo / ".leerie").mkdir(parents=True)
        (repo / ".leerie" / "config.toml").write_text(
            'build = "pnpm run build"\n'
            'lint = "biome check ."\n'
            'test = "vitest run"\n')
        return repo

    def _allowed(self, seen) -> str:
        cmd = seen["cmd"]
        return cmd[cmd.index("--allowedTools") + 1]

    def test_flag_widens_the_allowlist_with_the_repo_build_verbs(
            self, leerie, tmp_path, monkeypatch):
        repo = self._repo_with_blt(tmp_path)
        seen = _drive(leerie, monkeypatch, tmp_path, schema_key="planner",
                      cwd=str(tmp_path / "wt"), repo_root=repo,
                      skip_perms=True, allowed_tools=leerie.INSPECT_TOOLS)
        allowed = self._allowed(seen)
        for verb in ("pnpm", "biome", "vitest"):
            assert f"Bash({verb}:*)" in allowed, (
                f"{verb} missing from the widened allowlist: {allowed}")

    def test_without_the_flag_the_allowlist_is_untouched(
            self, leerie, tmp_path, monkeypatch):
        """The anti-vacuity partner: if widening happened unconditionally the
        test above would pass while the flag meant nothing."""
        repo = self._repo_with_blt(tmp_path)
        seen = _drive(leerie, monkeypatch, tmp_path, schema_key="planner",
                      cwd=str(tmp_path / "wt"), repo_root=repo,
                      skip_perms=False, allowed_tools=leerie.INSPECT_TOOLS)
        assert self._allowed(seen) == leerie.INSPECT_TOOLS

    def test_widening_never_adds_a_writer(self, leerie, tmp_path, monkeypatch):
        """Widening grants build verbs, not file tools. `Write`/`Edit`
        reaching a judgment worker is the original incident."""
        repo = self._repo_with_blt(tmp_path)
        seen = _drive(leerie, monkeypatch, tmp_path, schema_key="planner",
                      cwd=str(tmp_path / "wt"), repo_root=repo,
                      skip_perms=True, allowed_tools=leerie.INSPECT_TOOLS)
        entries = self._allowed(seen).split(",")
        assert "Write" not in entries and "Edit" not in entries
        assert "Bash" not in entries, "a bare Bash wildcard defeats the point"

    def test_the_satisfied_probe_bucket_is_never_widened(
            self, leerie, tmp_path, monkeypatch):
        """`SATISFIED_PROBE_TOOLS` is deliberately narrower than
        `INSPECT_TOOLS`, and its narrowness is calibrated rather than
        incidental: 12/12 false positives with full INSPECT_TOOLS latitude
        against 0 when scoped to the base tree, where a false positive
        silently deletes real work. An earlier revision of the widening
        applied to every judgment worker and handed the probe
        `Bash(pytest:*)` on this very repo."""
        repo = self._repo_with_blt(tmp_path)
        seen = _drive(leerie, monkeypatch, tmp_path,
                      schema_key="satisfied_probe",
                      cwd=str(tmp_path / "wt"), repo_root=repo,
                      skip_perms=True,
                      allowed_tools=leerie.SATISFIED_PROBE_TOOLS)
        assert self._allowed(seen) == leerie.SATISFIED_PROBE_TOOLS

    def test_the_planner_bucket_IS_widened_under_the_same_conditions(
            self, leerie, tmp_path, monkeypatch):
        """Anti-vacuity partner: without it the test above passes against a
        widening that was accidentally disabled everywhere."""
        repo = self._repo_with_blt(tmp_path)
        seen = _drive(leerie, monkeypatch, tmp_path, schema_key="planner",
                      cwd=str(tmp_path / "wt"), repo_root=repo,
                      skip_perms=True, allowed_tools=leerie.INSPECT_TOOLS)
        assert self._allowed(seen) != leerie.INSPECT_TOOLS

    def test_env_assignments_are_not_mistaken_for_the_executable(
            self, leerie, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".leerie").mkdir(parents=True)
        (repo / ".leerie" / "config.toml").write_text(
            'build = "CI=1 NODE_ENV=production pnpm build"\n'
            'lint = ""\n'
            'test = ""\n')
        assert leerie._blt_verbs(repo) == ["pnpm"]

    def test_non_invoking_prefixes_are_stepped_over(self, leerie, tmp_path):
        """`timeout 90 …`, `cd x && …` and `VAR=v …` do not change what a
        command invokes. Granting `Bash(timeout:*)` instead of the real verb
        would leave the planner unable to run the build it was just granted
        — the exact degradation the widening exists to prevent."""
        repo = tmp_path / "repo"
        (repo / ".leerie").mkdir(parents=True)
        (repo / ".leerie" / "config.toml").write_text(
            'build = "timeout 90 pnpm build"\n'
            'lint = "CI=1 NODE_ENV=production biome check ."\n'
            'test = "cd packages/api && vitest run"\n')
        assert leerie._blt_verbs(repo) == ["pnpm", "biome", "vitest"]

    def test_verbs_are_memoized_per_repo(self, leerie, tmp_path, monkeypatch):
        """`resolve_blt` reads config, runs inference and LOGS. Called once
        per judgment worker that is dozens of identical log lines and dozens
        of redundant reads, so the result is cached per repo."""
        repo = self._repo_with_blt(tmp_path)
        calls = {"n": 0}
        real = leerie.resolve_blt

        def counting(root):
            calls["n"] += 1
            return real(root)

        monkeypatch.setattr(leerie, "resolve_blt", counting)
        leerie._blt_verbs(repo)
        leerie._blt_verbs(repo)
        leerie._blt_verbs(repo)
        assert calls["n"] == 1

    def test_unparseable_command_is_skipped_not_guessed(
            self, leerie, tmp_path):
        """A wrong verb widens the wrong thing, so an axis that cannot be
        tokenised is dropped rather than approximated.

        Note the payload has to be genuinely unbalanced: a backslash-escaped
        quote (`pnpm run \\"x`) parses fine, so an earlier version of this
        test exercised the happy path and asserted nothing."""
        repo = tmp_path / "repo"
        (repo / ".leerie").mkdir(parents=True)
        (repo / ".leerie" / "config.toml").write_text(
            "build = \"pnpm run 'unbalanced\"\n" 'lint = ""\n' 'test = ""\n')
        with pytest.raises(ValueError):
            __import__("shlex").split("pnpm run 'unbalanced")
        assert "Bash(pnpm:*)" not in leerie._widen_inspect_tools(
            leerie.INSPECT_TOOLS, repo)


class TestJudgmentCwdFallback:
    """`_judgment_cwd` is a lookup, not the enforcement — so it falls back
    rather than raising when the key is absent, and the fallback is chosen so
    it CANNOT be the thing the guarantee forbids."""

    def test_fallback_is_under_the_run_dir_never_the_checkout(
            self, leerie, tmp_path):
        st = _FakeState(tmp_path, tmp_path / "repo")
        st.data.pop("planning_worktree", None)
        got = leerie._judgment_cwd(st)
        assert str(st.run_dir) in got
        assert got != str(st.repo_root)

    def test_the_fallback_still_trips_the_claude_p_guard_if_it_ever_matched(
            self, leerie, tmp_path, monkeypatch):
        """The property that makes the fallback safe: enforcement lives in
        claude_p, not here. Drive a judgment worker at the checkout directly
        and confirm it is still refused — that is what the fallback relies on
        being true."""
        repo = tmp_path / "repo"
        repo.mkdir()
        with pytest.raises(ValueError):
            _drive(leerie, monkeypatch, tmp_path, schema_key="reconciler",
                   cwd=str(repo), repo_root=repo)

    def test_an_explicit_path_wins_over_the_fallback(self, leerie, tmp_path):
        st = _FakeState(tmp_path, tmp_path / "repo")
        st.data["planning_worktree"] = "/somewhere/explicit"
        assert leerie._judgment_cwd(st) == "/somewhere/explicit"


class TestWiring:
    def test_claude_p_docstring_states_the_flag_is_unreachable(self, leerie):
        """The docstring is what the next reader trusts; it said the opposite
        for the whole life of the bug."""
        src = inspect.getdoc(leerie.claude_p) or ""
        assert "never" in src.lower() and "PLANNING_WORKER_TYPES" in src

    def test_skip_perms_is_not_an_or_over_state(self, leerie):
        """Source-coupling on the exact shape that caused the incident:
        `autonomous or st.data.get("dangerously_skip_permissions")`."""
        src = inspect.getsource(leerie.claude_p)
        assert 'autonomous or bool(' not in src, (
            "the OR that granted judgment workers the permission bypass is "
            "back")
