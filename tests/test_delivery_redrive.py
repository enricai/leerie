"""The delivery re-drive (DESIGN §8 *Delivery re-drive*; bench P8d / Q3).

Confirmed-unmet work — a failing held-out set, an unmet required item, a
red merge check — goes back to the subtasks that own it, on the integrated
staging tree, in sequential groups from the shared re-drive budget. Covered:

- the typed item claims and the routing (pure): every claimer, import-graph
  routing of set files, fallbacks, shared-file groups, pending controls;
- which set failures are work, and the per-group verdict (worse, trade,
  held-back rules), with a missing report never a rollback on its own;
- the shown sets' self-check, given as an out-of-tree command (never
  staged in the tree; tests/test_redrive_selfcheck_out_of_tree.py);
- the escalation gates: the audit majority, weakened tracked tests, the
  schema gate's rules and exemptions, the TypeScript AST check (real
  `typescript` when one is available), and the escalation decision;
- `_redrive_new_failures` under B* on a real pytest repo;
- `_run_delivery_redrive` end to end on a real git repo with the
  implementer and the set evaluation stubbed: a kept fix, a rollback with
  its control note, a recurring trade declared a conflict (and the
  outcome's reason), item routing, the held-back names round, the budget,
  a resumed in-flight group, and the escalated effort.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

TS_DIR_ENV = "LEERIE_TEST_TYPESCRIPT_DIR"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _ts_dir() -> str | None:
    env = os.environ.get(TS_DIR_ENV)
    if env:
        # Set means required (CI sets it): returned even when unusable — no
        # node, no package — so a broken install fails the tests instead of
        # skipping them.
        return env
    if shutil.which("node") is None:
        return None
    r = subprocess.run(["node", "-e", "console.log(require.resolve("
                        "'typescript/package.json'))"],
                       capture_output=True, text=True, check=False)
    return str(Path(r.stdout.strip()).parent) if r.returncode == 0 else None


@pytest.fixture(autouse=True)
def _quiet_host(leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()


# === item claims ============================================================

class TestItemClaims:
    def test_every_claimer_and_deliverable_wins(self, leerie):
        subs = {
            "s1": {"claims": {"sites": [], "items": [
                {"item_index": 0, "kind": "process"},
                {"item_index": 1, "kind": "deliverable"}]}},
            "s2": {"claims": {"sites": [], "items": [
                {"item_index": 0, "kind": "deliverable"},
                {"item_index": -1, "kind": "deliverable"},
                {"item_index": True, "kind": "deliverable"},
                "junk"]}},
            "s3": {"claims": {"sites": [], "items": [
                {"item_index": 2, "kind": "process"}]}},
            "s4": {},
        }
        got = leerie._item_claims(subs)
        assert got == {0: {"claimed_by": ["s1", "s2"], "kind": "deliverable"},
                       1: {"claimed_by": ["s1"], "kind": "deliverable"},
                       2: {"claimed_by": ["s3"], "kind": "process"}}

    def test_planner_schema_carries_item_claims(self, leerie):
        claims = leerie.SCHEMAS["planner"]["$defs"]["claims"]
        items = claims["properties"]["items"]["items"]
        assert items["required"] == ["item_index", "kind"]
        assert items["properties"]["kind"]["enum"] == ["deliverable",
                                                       "process"]
        # Split children inherit item claims with the site claims.
        sub = {"change_kind": "fix", "claims": {"sites": [], "items": [
            {"item_index": 0, "kind": "deliverable"}]}}
        out = leerie._site_claim_fields(sub)
        assert out["claims"]["items"] == [{"item_index": 0,
                                           "kind": "deliverable"}]
        out["claims"]["items"].append({})
        assert len(sub["claims"]["items"]) == 1


# === routing ================================================================

def _n(kind, key, **kw):
    return {"kind": kind, "key": key, **kw}


class TestRoute:
    SUB = {"s1": ["src/a.ts"], "s2": ["src/b.ts"], "s3": ["src/a.ts",
                                                          "src/c.ts"],
           "s4": ["src/d.ts"]}

    def test_defects_go_to_exercising_subtasks_grouped_by_shared_file(
            self, leerie):
        groups = leerie._redrive_route(
            [_n("defect", "1:x.test.ts", exercised=["src/a.ts"])],
            self.SUB, fallback=["s4"], pending=[])
        # s1 and s3 share src/a.ts: one group, plan order.
        assert [g["members"] for g in groups] == [["s1", "s3"]]
        assert groups[0]["files"] == ["src/a.ts", "src/c.ts"]

    def test_defect_fallback_then_everyone(self, leerie):
        g = leerie._redrive_route(
            [_n("defect", "1:x", exercised=["elsewhere.ts"])], self.SUB,
            fallback=["s4", "nope"], pending=[])
        assert [x["members"] for x in g] == [["s4"]]
        g = leerie._redrive_route(
            [_n("defect", "1:x", exercised=[])], self.SUB, fallback=[],
            pending=[])
        assert sorted(m for x in g for m in x["members"]) == ["s1", "s2",
                                                             "s3", "s4"]

    def test_control_to_touching_subtask_else_defect_claimers(self, leerie):
        g = leerie._redrive_route(
            [_n("defect", "1:x", exercised=["src/d.ts"]),
             _n("control_regression", "1:c", exercised=["src/b.ts"]),
             _n("ci_check", "ci-lint", exercised=[])],
            self.SUB, fallback=[], pending=[])
        by = {tuple(x["members"]): [n["key"] for n in x["notes"]] for x in g}
        assert by[("s2",)] == ["1:c"]
        # The red check names no file: it rides with the defect claimer.
        assert by[("s4",)] == ["1:x", "ci-lint"]

    def test_item_to_every_claimer_and_pending_rides_along(self, leerie):
        pend = [_n("control_regression", "t::y", test="t::y")]
        g = leerie._redrive_route(
            [_n("unmet_item", "item-0", claimed_by=["s4", "s2", "gone"])],
            self.SUB, fallback=[], pending=pend)
        assert [x["members"] for x in g] == [["s2"], ["s4"]]
        for x in g:
            assert [n["key"] for n in x["notes"]] == ["item-0", "t::y"]

    def test_unclaimed_item_goes_to_everyone(self, leerie):
        g = leerie._redrive_route([_n("unmet_item", "item-0", claimed_by=[])],
                                  {"s1": ["a"], "s2": ["b"]}, fallback=[],
                                  pending=[])
        assert [x["members"] for x in g] == [["s1"], ["s2"]]


# === which failures are work; the verdict ===================================

def _set(i, **kw):
    s = {"index": i, "valid": True, "dir": "/x", "defect_files": ["d.ts"],
         "control_files": ["c.ts"], "support_files": [], "cases": {}}
    s.update(kw)
    return s


class TestFailing:
    def test_kinds_broken_variant_bounded(self, leerie):
        sets = [_set(1), _set(2, broken=True),
                _set(3, defect_files=["d.ts", "v.ts"],
                     scope={"bounded": True,
                            "counted_cases": {"d.ts": ["c1"], "v.ts": []}}),
                _set(4, scope={"bounded": True, "counted_cases": {}})]
        res = [{"index": 1, "passed": False, "failing_files": ["d.ts",
                                                               "c.ts"]},
               {"index": 2, "passed": False, "failing_files": ["d.ts"]},
               {"index": 3, "passed": False, "failing_files": ["d.ts",
                                                               "v.ts"]},
               {"index": 4, "passed": False, "failing_files": ["d.ts"]},
               {"index": 5, "passed": False, "unmeasured": True,
                "failing_files": []}]
        got, unm = leerie._redrive_failing(res, sets + [_set(5)],
                                           {1, 2, 3, 4, 5})
        assert got == {"1:d.ts": {"set_index": 1, "file": "d.ts",
                                  "kind": "defect", "failing_cases": []},
                       "1:c.ts": {"set_index": 1, "file": "c.ts",
                                  "kind": "control", "failing_cases": []},
                       "3:d.ts": {"set_index": 3, "file": "d.ts",
                                  "kind": "defect", "failing_cases": []}}
        assert unm == [5]

    def test_a_variant_case_in_a_counted_file_is_not_work(self, leerie):
        """Per case: the counted case passes, the one still failing in the
        same file is a variant — nothing to re-drive. The counted case
        failing is."""
        s3 = _set(3, scope={"bounded": True,
                            "counted_cases": {"d.ts": ["c1"]}})
        res = [{"index": 3, "passed": False, "failing_files": ["d.ts"],
                "failing_cases": {"d.ts": ["v1"]}}]
        assert leerie._redrive_failing(res, [s3], {3}) == ({}, [])
        res[0]["failing_cases"] = {"d.ts": ["v1", "c1"]}
        got, _ = leerie._redrive_failing(res, [s3], {3})
        assert got == {"3:d.ts": {"set_index": 3, "file": "d.ts",
                                  "kind": "defect",
                                  "failing_cases": ["v1", "c1"]}}

    def test_carry_keeps_unmeasured_verdicts(self, leerie):
        before = {"1:d": {"set_index": 1, "file": "d", "kind": "defect"},
                  "2:d": {"set_index": 2, "file": "d", "kind": "defect"}}
        assert leerie._redrive_carry(before, {}, [2]) == {"2:d": before["2:d"]}


def _f(*keys, kind="defect"):
    return {k: {"set_index": 1, "file": k, "kind": kind} for k in keys}


class TestVerdict:
    def test_fix_is_kept(self, leerie):
        v = leerie._redrive_verdict(_f("a", "b"), _f("b"), [])
        assert v["fixed"] == ["a"] and not v["worse"] and not v["trade"]

    def test_more_failing_or_control_or_new_test_is_worse(self, leerie):
        assert leerie._redrive_verdict(_f("a"), _f("a", "b"), [])["worse"]
        v = leerie._redrive_verdict(_f("a"), {**_f("c", kind="control")}, [])
        assert v["worse"] and v["ctl_new"] == ["c"] and v["trade"]
        assert v["pairs"] == [["a", "c"]]
        v = leerie._redrive_verdict(_f("a"), {}, ["t::x"])
        assert v["worse"] and v["pairs"] == [["a", "t::x"]]

    def test_fixed_cases_count_cases_not_files(self, leerie):
        def f(**cases):
            return {k: {"set_index": 1, "file": k, "kind": "defect",
                        "failing_cases": v} for k, v in cases.items()}
        # One of a file's two failing cases fixed: progress, no file fixed.
        v = leerie._redrive_verdict(f(a=["x", "y"]), f(a=["y"]), [])
        assert v["fixed"] == [] and v["fixed_cases"] == ["a::x"]
        # A file with no per-case record counts as one case.
        v = leerie._redrive_verdict(f(a=[]), {}, [])
        assert v["fixed_cases"] == ["a"]
        v = leerie._redrive_verdict(f(a=["x"]), {}, [])
        assert v["fixed_cases"] == ["a::x"]
        # Nothing left the list (or no record on one side): nothing fixed.
        assert leerie._redrive_verdict(f(a=["x"]), f(a=["x"]),
                                       [])["fixed_cases"] == []
        assert leerie._redrive_verdict(f(a=["x"]), f(a=[]),
                                       [])["fixed_cases"] == []
        # Held-back: fixing one case of a still-failing file is not worse.
        v = leerie._redrive_verdict({}, {}, [], held=f(h=["p", "q"]),
                                    held_after=f(h=["q"]))
        assert not v["worse"] and v["held_fixed_cases"] == ["h::p"]

    def test_worse_is_counted_in_cases(self, leerie):
        """Review round 1 M4 (the reviewer's probe): one case fixed, three
        newly failing in the same file — counted in files nothing changed,
        and the group was kept."""
        def f(**cases):
            return {k: {"set_index": 1, "file": k, "kind": "defect",
                        "failing_cases": v} for k, v in cases.items()}
        v = leerie._redrive_verdict(f(a=["c1"]), f(a=["c2", "c3", "c4"]), [])
        assert v["fixed_cases"] == ["a::c1"]
        assert v["broke_cases"] == ["a::c2", "a::c3", "a::c4"]
        assert v["worse"]
        # A set file traded for another: equal file counts, still worse.
        v = leerie._redrive_verdict(f(a=["x"]), f(b=["y"]), [])
        assert v["fixed_cases"] == ["a::x"] and v["broke_cases"] == ["b::y"]
        assert v["worse"]
        # A newly failing file with no per-case record counts as one case.
        v = leerie._redrive_verdict(f(a=["x"]), f(a=["x"], b=[]), [])
        assert v["broke_cases"] == ["b"] and v["worse"]
        # A partial fix with nothing newly failing is not worse.
        v = leerie._redrive_verdict(f(a=["x", "y"]), f(a=["y"]), [])
        assert v["broke_cases"] == [] and not v["worse"]
        # Review round 2 M4: a file that failed with named cases and now
        # fails as a whole (an import or collection error) is worse, counted
        # as its key — even when another file's case was fixed.
        v = leerie._redrive_verdict(f(a=["x"]), f(a=[]), [])
        assert v["broke_cases"] == ["a"] and v["worse"]
        v = leerie._redrive_verdict(f(a=["c1"], b=["d1"]), f(b=[]), [])
        assert v["fixed_cases"] == ["a::c1"]
        assert v["broke_cases"] == ["b"] and v["worse"]
        # Whole-file on both sides, or whole-file to named: not worse.
        assert not leerie._redrive_verdict(f(a=[]), f(a=[]), [])["worse"]
        assert not leerie._redrive_verdict(f(a=[]), f(a=["x"]), [])["worse"]
        # Held-back: a newly failing held-back case is worse even when
        # another held-back case was fixed.
        v = leerie._redrive_verdict({}, {}, [], held=f(h=["p"]),
                                    held_after=f(h=["q"]))
        assert v["held_fixed_cases"] == ["h::p"]
        assert v["held_broke_cases"] == ["h::q"] and v["worse"]

    def test_held_rules(self, leerie):
        h = _f("h1", "h2")
        v = leerie._redrive_verdict({}, {}, [], held=h, held_after=_f("h2"))
        assert not v["worse"] and v["held_fixed"] == ["h1"]
        v = leerie._redrive_verdict({}, {}, [], held=h, held_after=h)
        assert v["worse"]                       # nothing held-back fixed
        v = leerie._redrive_verdict({}, {}, [], held=h,
                                    held_after={**_f("h2"),
                                                **_f("hc", kind="control")})
        assert v["worse"] and v["trade"]
        # No held-back verdict (a missing report): the held rules are off.
        v = leerie._redrive_verdict({}, {}, [], held=h, held_after=None)
        assert not v["worse"]


# === staging ================================================================

def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    (repo / "src").mkdir()
    (repo / "src" / "a.ts").write_text("export const a = 1; // BUG\n")
    (repo / "src" / "b.ts").write_text("export const b = 2;\n")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        'test_scoped = "python3 -m pytest -q -p no:cacheprovider '
        '{test_files}"\n')
    (repo / ".gitignore").write_text(".leerie/runs/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


# === escalation gates =======================================================

class TestAuditGate:
    def test_majority_of_samples(self, leerie):
        b = leerie._redrive_audit_blocks
        assert b({}) is None
        assert b({"audit": {"samples": 3, "kinds": []}}) is None
        assert b({"audit": {"samples": 3, "sample_kinds": [
            ["contradicts_task"], ["contradicts_task", "coupling"], []]}})
        assert b({"audit": {"samples": 3, "sample_kinds": [
            ["contradicts_task"], ["coupling"], []]}}) is False
        # Only contradicts_task blocks (config (ii)).
        assert b({"audit": {"samples": 3, "sample_kinds": [
            ["overspecified_mechanism"]] * 3}}) is False


def _commit(repo: Path, files: dict[str, str | None], msg: str) -> str:
    for rel, text in files.items():
        p = repo / rel
        if text is None:
            p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


class TestWeakenedTests:
    def test_counts(self, leerie, tmp_path):
        repo, base = _repo(tmp_path)
        t = ("it('a', () => { expect(1).toBe(1); expect(2).toBe(2); });\n"
             "it('b', () => { expect(3).toBe(3); });\n")
        base = _commit(repo, {"src/x.test.ts": t, "src/y.test.ts": t,
                              "src/z.test.ts": t, "src/w.test.ts": t}, "t")
        head = _commit(repo, {
            "src/x.test.ts": t.replace("expect(2).toBe(2); ", ""),
            "src/y.test.ts": t.replace("it('b'", "it.skip('b'"),
            "src/z.test.ts": None,
            "src/w.test.ts": t + "it('c', () => { expect(4).toBe(4); });\n",
            "src/new.test.ts": "it('n', () => {});\n"}, "h")
        got = {x["file"]: x["why"] for x in
               leerie._redrive_weakened_tests(repo, base, head)}
        assert got == {"src/x.test.ts": ["fewer_expect 3->2"],
                       "src/y.test.ts": ["fewer_cases 2->1",
                                         "added_skip_only 0->1"],
                       "src/z.test.ts": ["deleted"]}


SCHEMA = """model Order {
  id       String @id
  status   String
  note     String?
  paid     Boolean
  tenantId String
  tenant   Tenant @relation(fields: [tenantId], references: [id])
}
model Tenant {
  id String @id
}
"""


class TestSchemaGate:
    def _pair(self, leerie, tmp_path, before: str, after: str):
        repo, _ = _repo(tmp_path)
        base = _commit(repo, {"prisma/schema.prisma": SCHEMA,
                              "src/order.ts": before}, "b")
        head = _commit(repo, {"src/order.ts": after}, "h")
        return leerie._redrive_schema_hits(repo, base, head)

    BEFORE = ("export function show(order: Order) {\n"
              "  return order.status;\n}\n")

    def test_new_default_read_of_required_field(self, leerie, tmp_path):
        hits = self._pair(leerie, tmp_path, self.BEFORE,
                          self.BEFORE.replace("order.status",
                                              'order.status ?? "OPEN"'))
        assert [(h["rule"], h["field"]) for h in hits] == [
            ("C1_default_read", "status")]

    def test_optional_field_boolean_or_and_empty_default_exempt(
            self, leerie, tmp_path):
        after = ("export function show(order: Order) {\n"
                 '  const n = order.note ?? "";\n'
                 "  const p = order.paid || false;\n"
                 "  const s = order.status ?? [];\n"
                 "  return order.status;\n}\n")
        assert self._pair(leerie, tmp_path, self.BEFORE, after) == []

    def test_error_branch_and_same_hunk_exempt(self, leerie, tmp_path):
        before = ("export function show(order: Order) {\n"
                  "  const s = order.status || 'x';\n"
                  "  return s;\n}\n")
        after = ("export function show(order: Order) {\n"
                 "  const s = order.status || 'y';\n"
                 "  if (!order.status || order.paid) {\n"
                 "    throw new Error('missing status');\n"
                 "  }\n"
                 "  return s;\n}\n")
        assert self._pair(leerie, tmp_path, before, after) == []

    def test_empty_string_chain_after_optional_read_exempt(
            self, leerie, tmp_path):
        after = self.BEFORE.replace(
            "order.status", 'input?.label ?? order.status ?? ""')
        assert self._pair(leerie, tmp_path, self.BEFORE, after) == []

    def test_where_key_removed(self, leerie, tmp_path):
        before = ("export async function find(db: any, id: string, t: string)"
                  " {\n  return db.order.findFirst({\n    where: {\n"
                  "      id,\n      tenantId: t,\n    },\n  });\n}\n")
        after = before.replace("      tenantId: t,\n", "")
        hits = self._pair(leerie, tmp_path, before, after)
        assert [(h["rule"], h["field"]) for h in hits] == [
            ("C5_where_key_removed", "tenantId")]


@pytest.mark.skipif(_ts_dir() is None,
                    reason="node or the typescript package unavailable")
class TestAstGate:
    BASE = ("export function load(x: Item | null) {\n"
            "  if (!x) {\n    throw new NotFoundError('missing');\n  }\n"
            "  return { status: 'ok', x };\n}\n")

    def _run(self, leerie, tmp_path, after: str, extra: dict | None = None):
        repo, _ = _repo(tmp_path)
        base = _commit(repo, {"src/load.ts": self.BASE}, "b")
        head = _commit(repo, {"src/load.ts": after, **(extra or {})}, "h")
        (repo / "node_modules").mkdir(exist_ok=True)
        os.symlink(_ts_dir(), repo / "node_modules" / "typescript")
        assert leerie._redrive_ast_available(repo)
        return leerie._redrive_ast_hits(repo, base, head)

    def test_clean_change_passes(self, leerie, tmp_path):
        hits = self._run(leerie, tmp_path,
                         self.BASE.replace("'ok'", "'done'"))
        assert hits == []

    def test_d1_removed_guard(self, leerie, tmp_path):
        after = ("export function load(x: Item | null) {\n"
                 "  return { status: 'ok', x };\n}\n")
        hits = self._run(leerie, tmp_path, after)
        assert [h["rule"] for h in hits] == ["D1_guard_removed"]

    def test_d2_early_success(self, leerie, tmp_path):
        after = ("export function load(x: Item | null, y: Item | null) {\n"
                 "  if (y === null) {\n    return { status: 'ok' };\n  }\n"
                 "  if (!x) {\n    throw new NotFoundError('missing');\n  }\n"
                 "  return { status: 'ok', x };\n}\n")
        hits = self._run(leerie, tmp_path, after)
        assert [h["rule"] for h in hits] == ["D2_early_success_return"]

    def test_ts7_tree_runs_on_the_bundled_typescript(self, leerie, tmp_path,
                                                     monkeypatch):
        """A tree on the TypeScript 7 native port (its `typescript` exports
        a version only): the gate is available through the bundled copy
        and reports the same D1 hit."""
        repo, _ = _repo(tmp_path)
        base = _commit(repo, {"src/load.ts": self.BASE}, "b")
        head = _commit(repo, {"src/load.ts": (
            "export function load(x: Item | null) {\n"
            "  return { status: 'ok', x };\n}\n")}, "h")
        pkg = repo / "node_modules" / "typescript"
        (pkg / "lib").mkdir(parents=True)
        (pkg / "package.json").write_text(json.dumps({
            "name": "typescript", "version": "7.0.2",
            "exports": {".": "./lib/version.cjs"}}))
        (pkg / "lib" / "version.cjs").write_text(
            "module.exports = { version: '7.0.2' };\n")
        monkeypatch.setattr(leerie, "BUNDLED_TYPESCRIPT",
                            tmp_path / "none")
        assert leerie._redrive_ast_available(repo) is False
        assert leerie._redrive_ast_hits(repo, base, head) is None
        monkeypatch.setattr(leerie, "BUNDLED_TYPESCRIPT", Path(_ts_dir()))
        assert leerie._redrive_ast_available(repo) is True
        hits = leerie._redrive_ast_hits(repo, base, head)
        assert [h["rule"] for h in hits] == ["D1_guard_removed"]

    def test_d3_swallowed_across_files(self, leerie, tmp_path):
        caller = ("import { load } from './load';\n"
                  "export function use(x: Item | null) {\n"
                  "  try {\n    return load(x);\n  } catch (e) {\n"
                  "    if (e instanceof NotFoundError) {\n"
                  "      return { status: 'ok' };\n    }\n    throw e;\n"
                  "  }\n}\n")
        hits = self._run(leerie, tmp_path,
                         self.BASE.replace("'ok'", "'done'"),
                         {"src/use.ts": caller})
        assert [h["rule"] for h in hits] == ["D3_guard_error_swallowed"]

def test_escalation_gate_voids_on_any_hit(leerie, tmp_path, monkeypatch):
    monkeypatch.setattr(leerie, "_redrive_weakened_tests",
                        lambda *a: [])
    monkeypatch.setattr(leerie, "_redrive_schema_hits", lambda *a: [])
    monkeypatch.setattr(leerie, "_redrive_ast_hits", lambda *a: None)
    assert leerie._redrive_escalation_gate(tmp_path, "a", "b")["void"]
    monkeypatch.setattr(leerie, "_redrive_ast_hits", lambda *a: [])
    assert not leerie._redrive_escalation_gate(tmp_path, "a",
                                               "b")["void"]


def test_ast_unavailable_without_typescript(leerie, tmp_path, monkeypatch):
    repo, _ = _repo(tmp_path)
    monkeypatch.setattr(leerie, "BUNDLED_TYPESCRIPT", tmp_path / "none")
    assert leerie._redrive_ast_available(repo) is False


class TestEscalateDecision:
    def _st(self, leerie, tmp_path, own="own", base="own", rounds=1):
        st = leerie.State(tmp_path / "state", "run-e")
        st.data = {"repo_state_before_planning": {"head": own},
                   "acceptance": {"validity_base": base},
                   "redrive_rounds": {"s1": rounds}}
        return st

    AUD_OK = {1: {"audit": {"samples": 3, "sample_kinds": [[], [], []]}}}

    def test_all_conditions(self, leerie, tmp_path):
        caps = dict(leerie.DEFAULT_CAPS)
        st = self._st(leerie, tmp_path)
        e = leerie._redrive_escalate
        assert e(st, caps, ["s1"], {1}, self.AUD_OK, True)
        assert not e(st, caps, ["s1"], {1}, self.AUD_OK, False)   # no AST
        assert not e(st, caps, ["s1"], set(), self.AUD_OK, True)  # item-only
        assert not e(st, caps, ["s1"], {1}, {1: {}}, True)        # no audit
        blocked = {1: {"audit": {"samples": 3, "sample_kinds": [
            ["contradicts_task"]] * 2 + [[]]}}}
        assert not e(st, caps, ["s1"], {1}, blocked, True)

    def test_not_last_round_or_not_own_start(self, leerie, tmp_path):
        caps = dict(leerie.DEFAULT_CAPS)
        assert not leerie._redrive_escalate(
            self._st(leerie, tmp_path, rounds=0), caps, ["s1"], {1},
            self.AUD_OK, True)
        assert not leerie._redrive_escalate(
            self._st(leerie, tmp_path, base="older"), caps, ["s1"], {1},
            self.AUD_OK, True)


# === the note ===============================================================

def test_note_typed_entries_held_names_and_selfcheck(leerie):
    note = leerie._redrive_note(
        [_n("defect", "1:src/a.test.ts", file="src/a.test.ts",
            cases=["a works"]),
         _n("control_regression", "t::x", test="t::x", detail="keep it"),
         _n("unmet_item", "item-0", item="do X", evidence="e" * 2000),
         _n("ci_check", "ci-lint", id="lint", why="red", command="pnpm lint",
            diagnostics=["src/a.ts\tno-unused"])],
        rnd=2, rounds=2, merged=[{"id": "s2", "title": "B", "intent": "b"}],
        held_names=[{"name": "held case", "kind": "defect"},
                    {"name": "kept", "kind": "control"}],
        defect_shape="the shape",
        selfcheck={"command": "/run/sc/bin/leerie-selfcheck",
                   "src": "/run/sc/sets", "run_files": ["src/a.test.ts"],
                   "name_flag": "-t"})
    head, body = note.split("\n", 1)
    assert head.startswith("DELIVERY RE-DRIVE (round 2 of 2).")
    entries = json.loads(body[:body.index("\nMERGED")])
    assert [e["kind"] for e in entries] == ["defect", "control_regression",
                                            "unmet_item", "ci_check"]
    assert entries[0]["test_file"] == "src/a.test.ts"
    assert len(entries[2]["evidence"]) == 1200
    i_merged = note.index("MERGED RE-DRIVE")
    i_held = note.index("HELD-BACK ACCEPTANCE FAILURES")
    i_self = note.index("SELF-CHECK: the already-passing part")
    assert i_merged < i_held < i_self
    sc = note[i_self:]
    assert "is NOT in this tree" in sc and "under /run/sc/sets" in sc
    assert "  - src/a.test.ts\n" in sc
    assert "  /run/sc/bin/leerie-selfcheck\n" in sc
    # The subset forms, each with its example (bench I3 OUT3 wording).
    assert "`/run/sc/bin/leerie-selfcheck a.test.ts`" in sc
    assert "`/run/sc/bin/leerie-selfcheck -t '<name pattern>'`" in sc
    assert "  - held case\n" in note
    assert "  - kept  (behaviour that passed" in note
    assert "Fitting these names will fail the recheck." in note
    assert note.rstrip().endswith("Commit your change.")
    assert "with the command above" in note


# === B* new failures, real pytest ===========================================

def test_new_failures_confirmed_under_bstar(leerie, tmp_path):
    repo = tmp_path / "py"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text("def one():\n    return 1\n")
    (repo / "conftest.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n")
    (repo / "test_calc.py").write_text(
        "from calc import one\n\n\ndef test_one():\n    assert one() == 1\n")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        'test = "python3 -m pytest -q -p no:cacheprovider"\n'
        'test_scoped = "python3 -m pytest -q -p no:cacheprovider '
        '{test_files}"\n')
    (repo / ".gitignore").write_text(".leerie/runs/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    pre = _git(repo, "rev-parse", "HEAD")
    st = leerie.State(tmp_path / "state", "run-b", repo_root=repo)
    st.data = {"verbosity": "quiet", "worker_count": 0}
    (st.run_dir / "logs").mkdir(parents=True, exist_ok=True)
    caps = {**leerie.DEFAULT_CAPS, "confirm_pairs": 2}

    async def go():
        before = await leerie._redrive_test_ids(st, caps, repo, "ids0")
        # A different size: a same-second rewrite of equal size would be
        # read from the stale bytecode cache.
        (repo / "calc.py").write_text("def one():\n    return 1 + 1\n")
        _git(repo, "commit", "-qam", "break")
        after = await leerie._redrive_test_ids(st, caps, repo, "ids1")
        return before, after, await leerie._redrive_new_failures(
            st, caps, repo, pre, before, after, "t")
    before, after, v = asyncio.run(go())
    assert before["ids"] == [] and len(after["ids"]) == 1
    assert v["new"] == after["ids"] and not v["unmeasured"]
    assert v["files"] == {after["ids"][0]: "test_calc.py"}
    # No verdict on one side is never a regression.
    v2 = asyncio.run(leerie._redrive_new_failures(st, caps, repo, pre, None,
                                                  after, "t2"))
    assert v2 == {"new": [], "pre_existing": [], "no_verdict": [],
                  "resource_affected": [], "unmeasured": True}


# === end to end =============================================================

DEFECT = "src/a.test.ts"
CONTROL = "src/c.test.ts"
HELD = "src/h.test.ts"


class World:
    """A repo whose staging carries two integrated subtasks (s1 owns
    src/a.ts, s2 owns src/b.ts), held-out sets judged by a stub that reads
    staging's committed src/a.ts: the defect file fails until it says
    FIXED, the control file fails while it says BROKE, held-back sets fail
    until it says GENERAL."""

    def __init__(self, leerie, tmp_path, *, n_sets=1, items=None,
                 claims=None, unmet=None):
        self.L = leerie
        repo, base = _repo(tmp_path)
        self.repo, self.base = repo, base
        for sid, rel in (("s1", "src/a.ts"), ("s2", "src/b.ts")):
            _git(repo, "checkout", "-q", "-b", f"sub-{sid}", base)
            (repo / rel).write_text((repo / rel).read_text() + "// work\n")
            _git(repo, "commit", "-qam", f"{sid} work")
            _git(repo, "checkout", "-q", "main")
            _git(repo, "merge", "-q", "--no-ff", "-m",
                 f"leerie: integrate {sid}", f"sub-{sid}")
        head = _git(repo, "rev-parse", "HEAD")
        head_tree = _git(repo, "rev-parse", "HEAD^{tree}")
        st = leerie.State(tmp_path / "state", "run-r", repo_root=repo)
        self.st = st
        self.dir = st.run_dir
        (self.dir / "logs").mkdir(parents=True, exist_ok=True)
        self.staging = self.dir / "worktrees" / "staging"
        self.staging.parent.mkdir(parents=True, exist_ok=True)
        _git(repo, "worktree", "add", "-q", "--detach", str(self.staging),
             head)
        sets = []
        for i in range(1, n_sets + 1):
            sdir = tmp_path / f"set{i}"
            (sdir / "src").mkdir(parents=True)
            rel = DEFECT if i <= max(1, n_sets - 2) or n_sets < 4 else HELD
            (sdir / rel).write_text("import { a } from './a';\n")
            (sdir / CONTROL).write_text("import { b } from './b';\n")
            sets.append({"index": i, "valid": True, "dir": str(sdir),
                         "defect_files": [rel], "control_files": [CONTROL],
                         "support_files": [],
                         "cases": {rel: [f"case {i}"],
                                   CONTROL: [f"control {i}"]},
                         "audit": {"samples": 3,
                                   "sample_kinds": [[], [], []]}})
        self.sets = sets
        cl = claims or {}
        plan = {"subtasks": {
            "s1": {"id": "s1", "title": "A", "intent": "fix a",
                   "change_kind": "fix", "files_likely_touched": ["src/a.ts"],
                   "claims": {"sites": [], "items": cl.get("s1", [])}},
            "s2": {"id": "s2", "title": "B", "intent": "fix b",
                   "change_kind": "fix", "files_likely_touched": ["src/b.ts"],
                   "claims": {"sites": [], "items": cl.get("s2", [])}}}}
        (self.dir / "plan.json").write_text(json.dumps(plan))
        st.data = {"task": "fix it", "verbosity": "quiet",
                   "working_branch": "main", "worker_count": 0,
                   "repo_state_before_planning": {"head": base},
                   "subtask_status": {"s1": "complete", "s2": "complete"},
                   "required_items": items or [],
                   # Final detection's verdict, of the tree it read
                   # (DESIGN §6 *A delivery verdict counts only on the tree
                   # it was measured on*).
                   "delivery_gate": {"unmet_final": unmet or [],
                                     "final_sha": head,
                                     "final_tree": head_tree},
                   "acceptance": {"validity_base": base, "sets": sets,
                                  "gate": {}}}
        st.save()
        self.calls: list[dict] = []
        self.edits: list = []          # one callable per expected drive
        self.caps = {**leerie.DEFAULT_CAPS}

    def a_text(self, rev: str) -> str:
        return _git(self.staging, "show", f"{rev}:src/a.ts")

    def install(self, monkeypatch):
        L = self.L
        world = self

        async def fake_eval(st, caps, tree, sets, label, *, rev="HEAD",
                            confirm=True):
            text = world.a_text(rev)
            out = []
            for s_ in sets:
                failing = []
                rel = s_["defect_files"][0]
                if rel == HELD and "GENERAL" not in text:
                    failing.append(rel)
                if rel == DEFECT and "FIXED" not in text:
                    failing.append(rel)
                if "BROKE" in text:
                    failing.append(CONTROL)
                out.append({"index": s_["index"], "passed": not failing,
                            "unmeasured": False, "failing_files": failing})
            return out

        async def fake_claude_p(**kw):
            staged = [p for p in (world.staging / "src").glob("*.test.ts")]
            path = ((kw.get("env_extra") or {}).get("PATH") or "")
            world.calls.append({
                "sid": kw["sid"], "effort": kw["effort"],
                "cwd": kw["cwd"], "prompt": kw["user_prompt"],
                "staged": sorted(str(p.relative_to(world.staging))
                                 for p in staged),
                "bin": path.split(os.pathsep)[0],
                "status": _git(world.staging, "status", "--porcelain",
                               "--untracked-files=all")})
            edit = world.edits.pop(0) if world.edits else None
            if edit:
                edit(world)
            return {"status": "complete", "summary": "done"}

        async def no_ids(*a, **k):
            return None

        async def no_ci(*a, **k):
            return []
        monkeypatch.setattr(L, "_evaluate_acceptance_sets", fake_eval)
        monkeypatch.setattr(L, "claude_p", fake_claude_p)
        monkeypatch.setattr(L, "_redrive_test_ids", no_ids)
        monkeypatch.setattr(L, "_run_ci_parity_final", no_ci)

    def write(self, text: str, msg: str = "fix") -> None:
        (self.staging / "src" / "a.ts").write_text(text)
        _git(self.staging, "commit", "-qam", msg)

    def run(self):
        return asyncio.run(self.L._run_delivery_redrive(
            self.dir, self.st, self.caps, {"implementer": "sonnet"},
            {"implementer": "low"}))


def test_kept_fix_updates_the_final_verdict(leerie, tmp_path, monkeypatch):
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    w.edits = [lambda w: w.write("export const a = 1; // FIXED\n")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert rec["done"] and len(w.calls) == 1
    c = w.calls[0]
    assert c["sid"] == "delivery-redrive-r1-g0" and c["effort"] == "low"
    assert c["cwd"] == str(w.staging)
    # Routed by the import graph to s1 only; nothing staged in the tree it
    # commits from, the self-check command first on its PATH.
    assert c["prompt"].startswith("Execute subtask `s1`.")
    assert c["staged"] == [] and c["status"] == ""
    assert c["bin"].endswith("/selfcheck/delivery-redrive-r1-g0/bin")
    assert f"  {c['bin']}/leerie-selfcheck\n" in c["prompt"]
    assert "SELF-CHECK:" in c["prompt"] and DEFECT in c["prompt"]
    # The group's self-check directory is gone with the group.
    assert not (w.st.run_dir / "selfcheck" / "delivery-redrive-r1-g0").exists()
    assert not list((w.staging / "src").glob("*.test.ts"))
    assert w.st.data["redrive_rounds"] == {"s1": 1}
    g = w.st.data["acceptance"]["gate"]
    assert g["final"][0]["passed"] and "residual" not in g
    assert g["final_sha"] == _git(w.staging, "rev-parse", "HEAD")
    assert w.L._decide_outcome(w.st, any_commits=True) == "done"


def test_worse_round_rolls_back_then_recurring_trade_is_a_conflict(
        leerie, tmp_path, monkeypatch):
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    start = _git(w.staging, "rev-parse", "HEAD")

    def trade(w):
        w.write("export const a = 1; // FIXED BROKE\n")
    w.edits = [trade, trade]
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert len(w.calls) == 2
    # Every attempt was reset away.
    assert _git(w.staging, "rev-parse", "HEAD") == start
    g1 = rec["rounds"][0]["groups"][0]
    assert g1["worse"] and g1["rolled_back"] and g1["trade"]
    assert g1["ctl_new"] == [f"1:{CONTROL}"]
    # Round 2 carries the broken control as a typed note.
    assert '"kind": "control_regression"' in w.calls[1]["prompt"]
    assert CONTROL in w.calls[1]["prompt"].split("SELF-CHECK")[0]
    assert rec["conflict"]["pairs"] == [[f"1:{DEFECT}", f"1:{CONTROL}"]]
    reason = rec["conflict"]["reason"]
    assert f"held-out set 1's {DEFECT}" in reason
    assert w.L._decide_outcome(w.st, any_commits=True) == "unconverged"
    assert reason in w.st.data["outcome_reasons"]


def test_items_go_to_every_claimer_process_items_never(
        leerie, tmp_path, monkeypatch):
    items = [{"item": "a does X", "span": "S1", "verifiability": "tree"},
             {"item": "run lint", "span": "S2", "verifiability": "tree"}]
    unmet = [{"item_index": 0, "item": "a does X", "evidence": "missing",
              "by": ["judge"]},
             {"item_index": 1, "item": "run lint", "evidence": "not run",
              "by": ["judge"]}]
    w = World(leerie, tmp_path, n_sets=0, items=items, unmet=unmet,
              claims={"s1": [{"item_index": 0, "kind": "deliverable"},
                             {"item_index": 1, "kind": "process"}],
                      "s2": [{"item_index": 0, "kind": "deliverable"}]})
    w.install(monkeypatch)
    w.edits = [lambda w: w.write("export const a = 1; // done\n"), None]
    judged = []

    async def fake_judge(task, its, staging, st, caps, models, efforts,
                         phase_label, **kw):
        # Handed the whole list and the indices to read (review round 14
        # M4: indices are the run's, never positions in a subset).
        assert len(its) == len(items)
        judged.append([its[i]["item"] for i in kw["only"]])
        return [], 1, None
    monkeypatch.setattr(leerie, "_delivery_judge_unmet", fake_judge)
    monkeypatch.setattr(leerie, "_delivery_record", lambda *a, **k: "")
    monkeypatch.setattr(leerie, "_delivery_typed_evidence",
                        lambda *a, **k: ([], {}))
    w.run()
    assert [c["sid"] for c in w.calls] == ["delivery-redrive-r1-g0",
                                           "delivery-redrive-r1-g1"]
    assert w.calls[0]["prompt"].startswith("Execute subtask `s1`.")
    assert w.calls[1]["prompt"].startswith("Execute subtask `s2`.")
    for c in w.calls:
        assert "a does X" in c["prompt"] and "run lint" not in c["prompt"]
    # Only the routed item is re-judged; met now, it leaves unmet_final.
    assert judged == [["a does X"]]
    left = w.st.data["delivery_gate"]["unmet_final"]
    assert [u["item_index"] for u in left] == [1]


@pytest.mark.parametrize("detected_on_start", [True, False])
def test_rechecked_items_rekey_the_detection_verdict(
        leerie, tmp_path, monkeypatch, detected_on_start):
    # DESIGN §8 *A finished verdict is reused only on the commit it was
    # measured on*: once the re-drive re-checked its routed items on the
    # commit it ends on, `delivery_gate.final_sha` names that commit, so a
    # resume's detection keeps the verdict `_decide_outcome` reads. A
    # detection of another commit than the re-drive's start is not re-keyed.
    items = [{"item": "a does X", "span": "S1", "verifiability": "tree"}]
    unmet = [{"item_index": 0, "item": "a does X", "evidence": "missing",
              "by": ["judge"]}]
    w = World(leerie, tmp_path, n_sets=0, items=items, unmet=unmet,
              claims={"s1": [{"item_index": 0, "kind": "deliverable"}]})
    start = _git(w.staging, "rev-parse", "HEAD")
    det = start if detected_on_start else "0" * 40
    w.st.data["delivery_gate"]["final_sha"] = det
    w.install(monkeypatch)
    w.edits = [lambda w: w.write("export const a = 1; // done\n")]

    async def fake_judge(task, its, staging, st, caps, models, efforts,
                         phase_label, **kw):
        return [], 1, None
    monkeypatch.setattr(leerie, "_delivery_judge_unmet", fake_judge)
    monkeypatch.setattr(leerie, "_delivery_record", lambda *a, **k: "")
    monkeypatch.setattr(leerie, "_delivery_typed_evidence",
                        lambda *a, **k: ([], {}))
    w.run()
    final = _git(w.staging, "rev-parse", "HEAD")
    assert final != start
    gate = w.st.data["delivery_gate"]
    assert gate["unmet_final"] == []
    assert w.st.data["delivery_redrive"]["final_sha"] == final
    assert gate["final_sha"] == (final if detected_on_start else det)
    on_disk = json.loads((w.dir / "state.json").read_text())
    assert on_disk["delivery_gate"]["final_sha"] == gate["final_sha"]


def test_held_back_names_round(leerie, tmp_path, monkeypatch):
    w = World(leerie, tmp_path, n_sets=4)
    w.install(monkeypatch)
    w.edits = [lambda w: w.write("export const a = 1; // FIXED\n"),
               lambda w: w.write("export const a = 1; // FIXED GENERAL\n")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert [r["held_mode"] for r in rec["rounds"]] == [False, True]
    p2 = w.calls[1]["prompt"]
    assert "HELD-BACK ACCEPTANCE FAILURES" in p2
    assert "  - case 3" in p2 and "  - case 4" in p2
    # Names only: the held-back files are never staged or named.
    assert HELD not in p2 and w.calls[1]["staged"] == []
    assert all(r["passed"] for r in
               w.st.data["acceptance"]["gate"]["final"])


def test_spent_budget_skips_and_records(leerie, tmp_path, monkeypatch):
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    w.st.data["redrive_rounds"] = {"s1": 2}
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert w.calls == []
    assert rec["rounds"][0]["groups"][0]["skipped"] == "budget"
    assert rec["done"]


def test_resumed_in_flight_group_is_rolled_back(leerie, tmp_path,
                                                monkeypatch):
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    pre = _git(w.staging, "rev-parse", "HEAD")
    w.write("export const a = 1; // half done\n", "partial")
    partial = _git(w.staging, "rev-parse", "HEAD")
    w.st.data["delivery_redrive"] = {"in_flight": {"pre_sha": pre,
                                                   "round": 1, "group": 0},
                                     "rounds": [{"round": 1, "groups": []}]}
    w.st.data["redrive_rounds"] = {"s1": 1}
    w.edits = [lambda w: w.write("export const a = 1; // FIXED\n")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert "in_flight" not in rec and rec["done"]
    # Rolled back, then the remaining round drove the fix on the clean tree:
    # the interrupted group's commit is not in the history that ships.
    assert subprocess.run(["git", "-C", str(w.staging), "merge-base",
                           "--is-ancestor", partial, "HEAD"]).returncode == 1
    assert _git(w.staging, "rev-parse", "HEAD~1") == pre
    assert "FIXED" in w.a_text("HEAD")
    assert w.st.data["redrive_rounds"] == {"s1": 2}


def test_resumed_in_flight_group_removes_its_selfcheck_dir(
        leerie, tmp_path, monkeypatch):
    """A crash mid-group leaves its self-check directory (the shown sets'
    readable copy) in the run directory, never in staging; the resume
    removes it with the rollback."""
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    pre = _git(w.staging, "rev-parse", "HEAD")
    sc = leerie._selfcheck_prepare(w.st, w.caps, w.staging, w.sets,
                                   "delivery-redrive-r1-g0", None)
    assert Path(sc["src"]).is_dir()
    w.st.data["delivery_redrive"] = {"in_flight": {
        "pre_sha": pre, "round": 1, "group": 0},
        "rounds": [{"round": 1, "groups": []}]}
    w.st.data["redrive_rounds"] = {"s1": 2}         # no budget left
    w.run()
    assert not (w.st.run_dir / "selfcheck").exists()
    assert _git(w.staging, "status", "--porcelain",
                "--untracked-files=all") == ""


def test_last_round_escalates_and_gate_voids(leerie, tmp_path, monkeypatch):
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    w.st.data["redrive_rounds"] = {"s1": 1}
    monkeypatch.setattr(leerie, "_redrive_ast_available", lambda *a: True)
    seen = []

    def gate(staging, base, head):
        seen.append((base, head))
        return {"void": True, "weakened": [{"file": "x", "why": ["deleted"]}],
                "schema": [], "ast": []}
    monkeypatch.setattr(leerie, "_redrive_escalation_gate", gate)
    start = _git(w.staging, "rev-parse", "HEAD")
    w.edits = [lambda w: w.write("export const a = 1; // FIXED\n")]
    w.run()
    assert w.calls[0]["effort"] == leerie.EFFORT_REDRIVE_ESCALATION == "high"
    ge = w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert ge["escalated"] and ge["gate"]["void"] and ge["rolled_back"]
    assert seen and seen[0][0] == start
    assert _git(w.staging, "rev-parse", "HEAD") == start


def test_committed_staged_file_is_rolled_back(leerie, tmp_path,
                                              monkeypatch):
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    start = _git(w.staging, "rev-parse", "HEAD")

    def leak(w):
        # The worker copies the set's file into its tree at the set's path
        # (its source is readable) and commits it with the fix.
        (w.staging / "src" / "a.ts").write_text("export const a = 1; // FIXED\n")
        shutil.copy(Path(w.sets[0]["dir"]) / DEFECT, w.staging / DEFECT)
        _git(w.staging, "add", "src/a.ts", DEFECT)
        _git(w.staging, "commit", "-qm", "leak")
    w.edits = [leak, leak]
    w.run()
    ge = w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert ge["committed_staged"] == [DEFECT] and ge["rolled_back"]
    assert _git(w.staging, "rev-parse", "HEAD") == start


def test_nothing_open_is_done_without_driving(leerie, tmp_path, monkeypatch):
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    w.write("export const a = 1; // FIXED\n")
    w.run()
    assert w.calls == []
    assert w.st.data["delivery_redrive"] == {
        **w.st.data["delivery_redrive"], "done": True}
    assert w.st.data["delivery_redrive"]["rounds"] == []


def test_falsifier_probe_saved_and_rechecked(leerie, tmp_path, monkeypatch):
    lane = tmp_path / "lane"
    (lane / "probe").mkdir(parents=True)
    (lane / "probe" / "p.test.ts").write_text("probe\n")
    st = leerie.State(tmp_path / "state", "run-f")
    saved = leerie._falsifier_save_probe(
        st, lane, 3, {"probe_path": "probe/p.test.ts"})
    assert saved == {"dir": str(st.run_dir / "acceptance" / "probes" / "i3"),
                     "file": "probe/p.test.ts"}
    assert Path(saved["dir"], saved["file"]).read_text() == "probe\n"
    assert leerie._falsifier_save_probe(
        st, lane, 4, {"probe_path": "../escape.ts"}) is None
    st.data = {"required_items": ["i0", "i1", "i2", "i3"],
               "delivery_gate": {
                   "unmet_final": [{"item_index": 3, "item": "i3",
                                    "evidence": "probe fails",
                                    "by": ["falsifier"]}],
                   "items": [{"item_index": 3, "verdict": "unmet",
                              "falsifier": {"samples": [
                                  {"confirmed": True,
                                   "probe_saved": saved}]}}]}}
    placed = []

    async def fake_eval(st_, caps, tree, sets, label, **kw):
        placed.append(sets[0])
        return [{"index": sets[0]["index"], "passed": True,
                 "unmeasured": False, "failing_files": []}]
    monkeypatch.setattr(leerie, "_evaluate_acceptance_sets", fake_eval)
    got = asyncio.run(leerie._redrive_recheck_items(
        tmp_path, st, dict(leerie.DEFAULT_CAPS), {}, {}, tmp_path, [3]))
    assert placed[0]["defect_files"] == ["probe/p.test.ts"]
    assert placed[0]["dir"] == saved["dir"]
    assert got == {3: {"unmet": False, "by": [], "evidence": ""}}
    leerie._redrive_apply_item_verdicts(st, got, "sha1")
    assert st.data["delivery_gate"]["unmet_final"] == []
    row = st.data["delivery_gate"]["items"][0]
    assert row["verdict"] == "met" and row["redrive_recheck"] == {"sha":
                                                                  "sha1"}

    async def unmeasured(st_, caps, tree, sets, label, **kw):
        return [{"index": sets[0]["index"], "passed": False,
                 "unmeasured": True, "failing_files": []}]
    monkeypatch.setattr(leerie, "_evaluate_acceptance_sets", unmeasured)
    st.data["delivery_gate"]["unmet_final"] = [
        {"item_index": 3, "item": "i3", "evidence": "probe fails",
         "by": ["falsifier"]}]
    got = asyncio.run(leerie._redrive_recheck_items(
        tmp_path, st, dict(leerie.DEFAULT_CAPS), {}, {}, tmp_path, [3]))
    # No verdict keeps the item unmet.
    assert got[3]["unmet"] and got[3]["by"] == ["falsifier"]


def test_run_phases_redrives_after_the_merge_checks(leerie):
    """Wiring: the re-drive runs on the tree the merge checks judged, after
    every earlier finalize step, inside its own advisory guard."""
    import inspect
    src = inspect.getsource(leerie._run_phases)
    i_ci = src.index("await _run_ci_parity_final(leerie_dir, st, caps)")
    i_rd = src.index("await _run_delivery_redrive(leerie_dir, st, caps")
    i_fin = src.index("await phase_finalize(")
    assert i_ci < i_rd < i_fin
    assert "delivery re-drive raised" in src[i_rd:i_fin]


def test_a_group_that_fixes_no_failing_case_is_rolled_back(
        leerie, tmp_path, monkeypatch):
    """Zero progress: a commit that measurably fixes none of the group's
    failing cases is reset, even though nothing got worse."""
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    start = _git(w.staging, "rev-parse", "HEAD")

    def hedge(w):
        w.write("export const a = 1; // BUG with a defensive fallback\n",
                "hedge")
    w.edits = [hedge, hedge]
    w.run()
    rec = w.st.data["delivery_redrive"]
    groups = [g for r in rec["rounds"] for g in r["groups"]]
    driven = [g for g in groups if "skipped" not in g]
    # Two rounds from the shared counter, then nothing left to spend.
    assert len(driven) == 2 and groups[-1]["skipped"] == "budget"
    for g in driven:
        assert g["zero_progress"] and not g["worse"] and g["rolled_back"]
    assert _git(w.staging, "rev-parse", "HEAD") == start
    assert "fallback" not in w.a_text("HEAD")


def test_fixing_one_case_of_a_still_failing_file_is_progress(
        leerie, tmp_path, monkeypatch):
    """Zero progress counts cases: a group that fixes one of the defect
    file's two failing cases keeps its commit although the file still
    fails."""
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    fake = leerie._evaluate_acceptance_sets

    async def per_case(st, caps, tree, sets, label, **kw):
        out = await fake(st, caps, tree, sets, label, **kw)
        text = w.a_text(kw.get("rev", "HEAD"))
        for r in out:
            if DEFECT in r["failing_files"]:
                r["failing_cases"] = {DEFECT: ["case two"] if "HALF" in text
                                      else ["case one", "case two"]}
        return out
    monkeypatch.setattr(leerie, "_evaluate_acceptance_sets", per_case)
    w.edits = [lambda w: w.write("export const a = 1; // HALF\n", "half")]
    w.run()
    g = w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert g["fixed"] == [] and g["fixed_cases"] == [f"1:{DEFECT}::case one"]
    assert g["zero_progress"] is False and not g.get("rolled_back")
    assert "HALF" in w.a_text("HEAD")


def test_no_verdict_after_the_group_is_not_zero_progress(
        leerie, tmp_path, monkeypatch):
    """A missing report is unmeasured, never a rollback on its own."""
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    measured = leerie._evaluate_acceptance_sets
    n = {"i": 0}

    async def flaky(st, caps, tree, sets, label, **kw):
        n["i"] += 1
        if "redrive-r1g0" == label:
            return [{"index": s_["index"], "passed": False,
                     "unmeasured": True, "failing_files": []} for s_ in sets]
        return await measured(st, caps, tree, sets, label, **kw)
    monkeypatch.setattr(leerie, "_evaluate_acceptance_sets", flaky)
    w.edits = [lambda w: w.write("export const a = 1; // change\n")]
    w.run()
    g = w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert g["zero_progress"] is False and not g.get("rolled_back")
    # No verdict is `unmeasured`, never the flake label.
    assert "set 1" in g["unmeasured"] and "set 1" not in g[
        "resource_affected"]
    assert "change" in w.a_text("HEAD")


def test_residual_solution_defect_goes_to_its_file_owner_once(
        leerie, tmp_path, monkeypatch):
    """Replay shape (barnacle 45fa4253 pass 1): the final conformance left one
    unfixed solution defect in src/b.ts after its last round. The re-drive
    hands it, typed, to the subtask whose integrated diff touches that file
    (s2, not s1), drives it once, and the run settles `unconverged` with the
    defect named — never a stop."""
    w = World(leerie, tmp_path, n_sets=0)
    w.st.data["conformance"] = {"_final": {
        "result": None, "warnings": [], "blocked": False,
        "residual_defects": [{
            "kind": "unhandled_path", "where": "src/b.ts:12",
            "concrete_case": "results arrive after the first read",
            "why_ships_a_defect": "Enter is sent on a partial list"}]}}
    w.st.save()
    w.install(monkeypatch)
    w.edits = [None]
    w.run()
    rec = w.st.data["delivery_redrive"]
    assert [c["sid"] for c in w.calls] == ["delivery-redrive-r1-g0"]
    p = w.calls[0]["prompt"]
    assert p.startswith("Execute subtask `s2`.")
    assert '"kind": "solution_defect"' in p
    assert "results arrive after the first read" in p
    assert rec["defects_driven"] == ["solution-defect-0"]
    assert rec["rounds"][0]["groups"][0]["no_change"] is True
    assert rec["done"]
    assert w.L._decide_outcome(w.st, any_commits=True) == "unconverged"
    reason = w.st.data["outcome_reasons"][-1]
    assert reason.startswith("1 solution defect(s) the final conformance "
                             "left unfixed")
    assert "(1 of them driven by the delivery re-drive" in reason
    assert "[unhandled_path] src/b.ts:12" in reason


def test_solution_defect_file_is_a_path_match(leerie):
    files = ["src/a.ts", "src/b.ts"]
    assert leerie._solution_defect_file("src/b.ts:12", files) == "src/b.ts"
    assert leerie._solution_defect_file("./src/a.ts", files) == "src/a.ts"
    assert leerie._solution_defect_file("handleEnter()", files) is None
    assert leerie._solution_defect_file("src/b.tsx:3", files) is None
