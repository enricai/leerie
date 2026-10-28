"""Requirement coverage v6.1 and the grouped delivery judge (DESIGN §8
*Requirement coverage* / *Grouped judging at coverage item counts*, §12
*Delimiter-only cutting is not the deleted heading harvest*).

Python cuts the task at markdown delimiters and assigns IDs; the classifier
types every span and section; the gate is set arithmetic over those IDs. The
delivery gate routes external items to `needs_human_verification`, settles
untouched-paths items with `git diff`, and judges the rest in root-ancestor
packs.

Substance discipline: every consumer is executed (phase_classify,
_delivery_judge_unmet, phase_planning_coverage_gate) and asserted on values
and order — never on key presence alone.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from tests.stub_helpers import _d1_covered, _delivery_tree, _judge_verdict

MODELS = {"classifier": "sonnet", "delivery_judge": "sonnet",
          "task_coverage_judge": "sonnet"}
EFFORTS = {"classifier": "medium", "delivery_judge": "medium",
           "task_coverage_judge": "medium"}

TASK = """# Fix the export

The export drops rows.

- Restore every row
  - keep the header order
    - including the trailing column
  - never write an empty file
- Add a regression test

```
- a fenced line, not an item
# not a heading
```
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "data").mkdir()
    (repo / "data" / "a.csv").write_text("x\n")
    (repo / "src.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _state(leerie, tmp_path, **data):
    root = tmp_path / ".leerie"
    run_id = "test-d1"
    (root / "runs" / run_id / "worktrees" / "staging").mkdir(parents=True)
    st = leerie.State(root, run_id)
    st.data = {"task": "t", "worker_count": 0, **data}
    st.save()
    return st


# === the cut: delimiters only, IDs only ====================================

class TestSplitSpans:
    def test_delimiters_and_parent_links(self, leerie):
        spans = leerie._d1_split_spans(TASK)
        assert [(s["id"], s["parent"], s["heading"]) for s in spans] == [
            ("S1", None, True),     # heading
            ("S2", None, False),    # paragraph
            ("S3", None, False),    # - Restore every row
            ("S4", "S3", False),    # nested
            ("S5", "S4", False),    # nested twice
            ("S6", "S3", False),    # sibling of S4
            ("S7", None, False),    # back to top level
            ("S8", None, False),    # the fence, whole
        ]
        assert spans[4]["text"] == "    - including the trailing column"

    def test_fence_stays_whole_and_hides_delimiters(self, leerie):
        fence = leerie._d1_split_spans(TASK)[-1]["text"]
        assert fence.startswith("```") and fence.endswith("```")
        assert "# not a heading" in fence and "- a fenced line" in fence

    def test_cut_loses_no_text(self, leerie):
        """IDs only: every non-blank line of the task is in exactly one span."""
        spans = leerie._d1_split_spans(TASK)
        lines = [ln for s in spans for ln in s["text"].splitlines()]
        assert lines == [ln for ln in TASK.splitlines() if ln.strip()]

    def test_blank_line_resets_nesting(self, leerie):
        spans = leerie._d1_split_spans("- a\n\nnot indented\n  - b\n")
        assert [(s["id"], s["parent"]) for s in spans] == [
            ("S1", None), ("S2", None), ("S3", None)]


class TestSplitSections:
    def test_headings_cut_fence_aware_with_preamble(self, leerie):
        secs = leerie._d1_split_sections(
            "preamble\n# A\nx\n```\n# inside\n```\n## B\ny\n", "r.md", 3)
        assert [(s["id"], s["text"].splitlines()[0]) for s in secs] == [
            ("R3", "preamble"), ("R4", "# A"), ("R5", "## B")]
        assert all(s["source"] == "r.md" for s in secs)


class TestReportSources:
    def test_only_untracked_markdown_within_the_size_cap(
            self, leerie, tmp_path):
        repo, _ = _repo(tmp_path)
        (repo / "TRACKED.md").write_text("# tracked\n")
        _git(repo, "add", "TRACKED.md")
        _git(repo, "commit", "-qm", "tracked doc")
        (repo / "REPORT.md").write_text("# finding\nbad\n")
        (repo / "notes.txt").write_text("plain\n")
        (repo / "BIG.md").write_text("x" * (leerie.D1_REPORT_MAX_BYTES + 1))
        task = "Fix REPORT.md and follow TRACKED.md; see notes.txt and BIG.md"
        assert leerie._d1_report_sources(task, repo) == {
            "REPORT.md": "# finding\nbad\n"}


# === the gate: set arithmetic over IDs =====================================

def _units(leerie, task=TASK, reports=None):
    spans = leerie._d1_split_spans(task)
    sections, rep = [], {}
    for src, body in (reports or {}).items():
        secs = leerie._d1_split_sections(body, src, len(sections) + 1)
        sections += secs
        rep[src] = [s["id"] for s in secs]
    return {"spans": spans, "sections": sections, "reports": rep}


def _clean(leerie, units):
    """A hole-free answer: S1 heading, S2 background, S3..S7 items, S8
    background, R1 requirement, R2 evidence supporting it."""
    disp = []
    for s in units["spans"]:
        if s["id"] in ("S1",):
            disp.append({"span": s["id"], "disposition": "no_requirement",
                         "kind": "heading", "reason": "r"})
        elif s["id"] in ("S2", "S8"):
            disp.append({"span": s["id"], "disposition": "no_requirement",
                         "kind": "background", "reason": "r"})
        else:
            disp.append({"span": s["id"], "disposition": "items",
                         "kind": "", "reason": "r"})
    roles = []
    for s in units["sections"]:
        first = s["id"] == units["sections"][0]["id"]
        roles.append({"section": s["id"],
                      "role": "requirement" if first else "evidence",
                      "supports": "" if first else units["sections"][0]["id"],
                      "reason": "r"})
    items = [{"span": d["span"], "item": f"do {d['span']}",
              "verifiability": "tree"}
             for d in disp if d["disposition"] == "items"]
    items += [{"span": r["section"], "item": "fix the finding",
               "verifiability": "tree"}
              for r in roles if r["role"] == "requirement"]
    return {"categories": ["bug-fixing"], "span_dispositions": disp,
            "section_roles": roles, "required_items": items}


REPORT = {"BUG.md": "# The finding\nrows vanish\n## Repro\nrun it\n"}


class TestHoles:
    def test_clean_answer_has_no_holes(self, leerie):
        u = _units(leerie, reports=REPORT)
        assert leerie._d1_holes(_clean(leerie, u), u) == {}

    @pytest.mark.parametrize("mutate,label,ids", [
        # S4's item is then anchored on a span that is not `items` too.
        (lambda r: r["span_dispositions"].pop(3), "D1_UNDISPOSED", ["S4"]),
        (lambda r: r["section_roles"].pop(1), "D1_UNROLED", ["R2"]),
        (lambda r: r["required_items"].pop(0), "D1_UNCOVERED", ["S3"]),
        (lambda r: r["required_items"].append(
            {"span": "S3", "item": "again"}), "D1_DUPLICATE", ["S3"]),
        (lambda r: r["required_items"].append(
            {"span": "S2", "item": "x"}), "D1_MISANCHORED", ["S2"]),
        (lambda r: r["span_dispositions"][1].update(kind="vibes"),
         "D1_BAD_KIND", ["S2"]),
        (lambda r: r["span_dispositions"][1].update(
            kind="restatement", restates="S99"), "D1_BAD_RESTATES", ["S2"]),
        (lambda r: r["section_roles"][1].update(supports="R2"),
         "D1_BAD_SUPPORTS", ["R2"]),
    ])
    def test_each_hole_names_its_ids(self, leerie, mutate, label, ids):
        u = _units(leerie, reports=REPORT)
        r = _clean(leerie, u)
        mutate(r)
        expected = {label: ids}
        if label == "D1_UNDISPOSED":
            expected["D1_MISANCHORED"] = ids
        assert leerie._d1_holes(r, u) == expected

    def test_restating_an_ancestor_is_a_hole(self, leerie):
        u = _units(leerie)
        r = _clean(leerie, u)
        r["span_dispositions"][4] = {"span": "S5",
                                     "disposition": "no_requirement",
                                     "kind": "restatement", "restates": "S3",
                                     "reason": "r"}
        r["required_items"] = [i for i in r["required_items"]
                               if i["span"] != "S5"]
        assert leerie._d1_holes(r, u) == {"D1_RESTATES_ANCESTOR": ["S5"]}

    def test_report_without_requirement_only_when_asked(self, leerie):
        u = _units(leerie, reports=REPORT)
        r = _clean(leerie, u)
        r["section_roles"][0].update(role="context")
        r["section_roles"][1].update(supports="")
        r["required_items"] = [i for i in r["required_items"]
                               if i["span"] != "R1"]
        assert leerie._d1_holes(r, u, ask_report=True) == {
            "D1_REPORT_WITHOUT_REQUIREMENT": ["BUG.md"]}
        assert leerie._d1_holes(r, u, ask_report=False) == {}

    def test_table_rows_exempt_from_duplicate_and_quote_checked(self, leerie):
        task = ("Fix every row:\n\n| id | problem |\n|---|---|\n"
                "| F1 | slow |\n| F2 | wrong |\n")
        u = _units(leerie, task=task)
        base = {"span_dispositions": [
            {"span": "S1", "disposition": "no_requirement",
             "kind": "heading", "reason": "r"},
            {"span": "S2", "disposition": "items", "kind": "", "reason": "r"}],
            "section_roles": []}
        rows = [{"span": "S2", "item": "F1 fast", "table_row": "F1"},
                {"span": "S2", "item": "F2 right", "table_row": "F2"}]
        assert leerie._d1_holes({**base, "required_items": rows}, u) == {}
        bad = rows + [{"span": "S2", "item": "F9", "table_row": "F9"},
                      {"span": "S2", "item": "again", "table_row": "F1"}]
        holes = leerie._d1_holes({**base, "required_items": bad}, u)
        assert list(holes) == ["D1_BAD_TABLE_ROW"]
        assert any("'F9' not in the span" in h
                   for h in holes["D1_BAD_TABLE_ROW"])
        assert any("'F1' repeated" in h for h in holes["D1_BAD_TABLE_ROW"])

    def test_issue_strings_carry_the_label_and_ids_before_the_dash(
            self, leerie):
        """`_issue_signature` keys oscillation detection on the text before
        the em dash; it must hold the IDs, so a different hole set is a
        different signature."""
        u = _units(leerie)
        r = _clean(leerie, u)
        r["span_dispositions"] = r["span_dispositions"][:2]
        issues = leerie._d1_coverage_holes(r, u)
        assert issues[0].startswith("D1_UNDISPOSED: S3, S4, S5, S6, S7, S8 —")


class TestClosure:
    def test_restatement_chain_heading_block_and_supports(self, leerie):
        u = _units(leerie, reports=REPORT)
        r = _clean(leerie, u)
        # S2 restates S1 (a heading whose block holds items): covered.
        r["span_dispositions"][1] = {"span": "S2",
                                     "disposition": "no_requirement",
                                     "kind": "restatement", "restates": "S1",
                                     "reason": "r"}
        closure = leerie._d1_closure(r, u)
        assert closure == ["S2", "S3", "S4", "S5", "S6", "S7", "R1", "R2"]


# === phase_classify wiring =================================================

def _run_classify(leerie, tmp_path, monkeypatch, answers, task=TASK,
                  review_note=""):
    st = _state(leerie, tmp_path)
    calls: list[dict] = []

    async def fake_claude_p(**kw):
        calls.append(kw)
        return answers[min(len(calls), len(answers)) - 1]

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.chdir(tmp_path)
    caps = dict(leerie.DEFAULT_CAPS)
    asyncio.run(leerie.phase_classify(task, st, caps, False, MODELS, EFFORTS,
                                      review_note=review_note))
    return st, calls


class TestPhaseClassify:
    def test_prompt_shows_the_cut_and_a_hole_is_sent_back(
            self, leerie, tmp_path, monkeypatch):
        u = _units(leerie)
        clean = _clean(leerie, u)
        clean["required_items"][1]["verifiability"] = "external"
        holey = {**clean, "span_dispositions": clean["span_dispositions"][1:]}
        st, calls = _run_classify(leerie, tmp_path, monkeypatch,
                                  [holey, clean])
        assert len(calls) == 2
        first = calls[0]["user_prompt"]
        assert "[S4 < S3]   - keep the header order" in first
        assert "[S5 < S4]     - including the trailing column" in first
        assert "D1_UNDISPOSED: S1 —" in calls[1]["user_prompt"]
        # Values persisted from the clean round, in order.
        assert [i["span"] for i in st.data["required_items"]] == [
            "S3", "S4", "S5", "S6", "S7"]
        cov = st.data["requirement_coverage"]
        assert cov["spans"][3] == {"id": "S4", "parent": "S3"}
        assert cov["closure"] == ["S3", "S4", "S5", "S6", "S7"]
        assert cov["holes"] == {}
        assert st.data["needs_human_verification"] == [{
            "source": "required_item", "item_index": 1, "item": "do S4",
            "span": "S4", "source_ref": ""}]

    def test_report_without_requirement_is_asked_once(
            self, leerie, tmp_path, monkeypatch):
        """Round 0 is told about the report; round 1 leaves an unrelated
        hole and still names no requirement section — round 2's feedback
        must carry only the unrelated hole, since the report was already
        asked about once."""
        (tmp_path / "BUG.md").write_text(REPORT["BUG.md"])
        task = TASK + "\nSee BUG.md\n"
        u = _units(leerie, task=task, reports=REPORT)
        r = _clean(leerie, u)
        for role in r["section_roles"]:
            role.update(role="context", supports="")
        r["required_items"] = [i for i in r["required_items"]
                               if not i["span"].startswith("R")]
        other_hole = {**r, "span_dispositions": r["span_dispositions"][1:]}
        st, calls = _run_classify(leerie, tmp_path, monkeypatch,
                                  [r, other_hole, r], task=task)
        assert len(calls) == 3
        assert "=== REFERENCED FILE BUG.md" in calls[0]["user_prompt"]
        assert ("D1_REPORT_WITHOUT_REQUIREMENT: BUG.md"
                in calls[1]["user_prompt"])
        assert "D1_UNDISPOSED: S1" in calls[2]["user_prompt"]
        assert ("D1_REPORT_WITHOUT_REQUIREMENT"
                not in calls[2]["user_prompt"])
        assert st.data["requirement_coverage"]["reports"] == ["BUG.md"]
        assert st.data["requirement_coverage"]["holes"] == {}

    def test_residual_holes_are_recorded_not_fatal(
            self, leerie, tmp_path, monkeypatch):
        u = _units(leerie)
        r = _clean(leerie, u)
        r["required_items"] = r["required_items"][1:]
        st, calls = _run_classify(leerie, tmp_path, monkeypatch, [r])
        assert st.data["requirement_coverage"]["holes"] == {
            "D1_UNCOVERED": ["S3"]}
        assert st.data["categories"] == ["bug-fixing"]

    def test_review_note_is_shown_but_never_cut(
            self, leerie, tmp_path, monkeypatch):
        u = _units(leerie)
        note = "IMPORTANT — review says:\n\n- add testing"
        st, calls = _run_classify(leerie, tmp_path, monkeypatch,
                                  [_clean(leerie, u)], review_note=note)
        prompt = calls[0]["user_prompt"]
        assert note in prompt
        assert prompt.index("[S8]") < prompt.index(note)
        assert [s["id"] for s in
                st.data["requirement_coverage"]["spans"]][-1] == "S8"

    def test_gate_passes_the_original_task_and_the_note_apart(self, leerie):
        import inspect
        src = inspect.getsource(leerie.phase_classification_gate)
        assert "await phase_classify(task, st," in src
        assert "review_note=review_note" in src


class TestSchema:
    def test_coverage_fields_are_required(self, leerie):
        sch = leerie.SCHEMAS["classifier"]
        assert {"span_dispositions", "section_roles",
                "required_items"} <= set(sch["required"])
        item = sch["properties"]["required_items"]["items"]
        assert set(item["required"]) == {"item", "span", "verifiability"}
        assert item["properties"]["verifiability"]["enum"] == [
            "tree", "external"]

    def test_helper_output_has_no_holes(self, leerie):
        u = {"spans": leerie._d1_split_spans("task"), "sections": [],
             "reports": {}}
        assert leerie._d1_holes(_d1_covered(), u) == {}


# === grouped delivery judge ================================================

class TestJudgePacks:
    def _spans(self, n_roots, kids):
        spans = []
        for r in range(n_roots):
            rid = f"S{len(spans) + 1}"
            spans.append({"id": rid, "parent": None})
            for _ in range(kids):
                spans.append({"id": f"S{len(spans) + 1}", "parent": rid})
        return spans

    def test_root_groups_packed_to_the_limit_in_index_order(self, leerie):
        spans = self._spans(3, 7)          # 3 roots x 8 spans
        items = [{"span": s["id"]} for s in spans]
        packs = leerie._d1_judge_packs(items, list(range(24)), spans, 20)
        assert packs == [list(range(0, 16)), list(range(16, 24))]

    def test_a_group_over_30_is_chunked_at_30(self, leerie):
        spans = self._spans(1, 44)         # one root, 45 spans
        items = [{"span": s["id"]} for s in spans] + [{"span": "R1"}]
        packs = leerie._d1_judge_packs(items, list(range(46)), spans, 20)
        assert [len(p) for p in packs] == [30, 15, 1]

    def test_sections_share_a_group_unknown_anchors_stand_alone(self, leerie):
        items = [{"span": "R2"}, {"span": "S9"}, {"span": "R2"}, {}]
        packs = leerie._d1_judge_packs(items, [0, 1, 2, 3], [], 2)
        assert packs == [[0, 2], [1, 3]]


class TestUntouchedVerdict:
    def test_met_unmet_glob_and_refusals(self, leerie, tmp_path):
        repo, base = _repo(tmp_path)
        assert leerie._d1_untouched_verdict(repo, base, ["data/"]) == {
            "met": True, "files": []}
        (repo / "data" / "a.csv").write_text("changed\n")
        _git(repo, "commit", "-qam", "touch data")
        assert leerie._d1_untouched_verdict(repo, base, ["data/"]) == {
            "met": False, "files": ["data/a.csv"]}
        assert leerie._d1_untouched_verdict(repo, base, ["**/*.csv"]) == {
            "met": False, "files": ["data/a.csv"]}
        # Glob semantics: `*` does not cross `/`, so a root-level glob
        # leaves data/a.csv out (a plain git pathspec would match it).
        assert leerie._d1_untouched_verdict(repo, base, ["*.csv"]) == {
            "met": True, "files": []}
        assert leerie._d1_untouched_verdict(repo, base, ["src.py"]) == {
            "met": True, "files": []}
        for bad in (["/etc"], ["../x"], ["a b"], [""]):
            assert leerie._d1_untouched_verdict(repo, base, bad) is None
        assert leerie._d1_untouched_verdict(repo, None, ["data/"]) is None
        assert leerie._d1_untouched_verdict(repo, "0" * 40, ["data/"]) is None


class TestGroupedDeliveryJudge:
    def _setup(self, leerie, tmp_path, monkeypatch, verdict_for):
        repo, base = _repo(tmp_path)
        (repo / "data" / "a.csv").write_text("changed\n")
        _git(repo, "commit", "-qam", "touch data")
        # The judges' stubbed citations quote this file (DESIGN §8
        # *Delivery detection*: a met must carry a verified citation).
        _delivery_tree(repo)
        spans = [{"id": "S1", "parent": None}]
        spans += [{"id": f"S{i}", "parent": "S1"} for i in range(2, 26)]
        spans += [{"id": "S26", "parent": None}]
        items = [{"item": f"req {i}", "span": f"S{i + 1}",
                  "verifiability": "tree"} for i in range(26)]
        items.append({"item": "note it in the PR", "span": "S26",
                      "verifiability": "external"})
        items.append({"item": "leave data/ alone", "span": "S26",
                      "verifiability": "tree", "untouched_paths": ["data/"]})
        st = _state(leerie, tmp_path,
                    repo_state_before_planning={"head": base},
                    requirement_coverage={"spans": spans})
        calls: list[dict] = []

        async def fake_claude_p(**kw):
            calls.append(kw)
            return verdict_for(kw)

        monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
        return st, items, repo, calls

    def test_routes_packs_and_merges_by_global_index(
            self, leerie, tmp_path, monkeypatch):
        def verdict_for(kw):
            payload = kw["user_prompt"]
            arr = json.loads(payload.split("):\n", 1)[1].split("\n(These")[0])
            idx = [e["item_index"] for e in arr]
            # Item 3 always unmet; a stray verdict for item 0 sent to the
            # second pack must be ignored.
            vs = [_judge_verdict(i, i != 3, f"e{i}") for i in idx]
            if 0 not in idx:
                vs.append(_judge_verdict(0, False, "stray"))
            return {"verdicts": vs}

        st, items, repo, calls = self._setup(leerie, tmp_path, monkeypatch,
                                             verdict_for)
        routing: dict = {}
        unmet, n, contract = asyncio.run(leerie._delivery_judge_unmet(
            "task", items, repo, st, dict(leerie.DEFAULT_CAPS), MODELS,
            EFFORTS, phase_label="pre", routing=routing))
        prompts = [c["user_prompt"] for c in calls]
        assert not any("note it in the PR" in p for p in prompts)
        assert not any("leave data/ alone" in p for p in prompts)
        # 25 items under S1 (one group, 21..30 -> one pack), S26 alone.
        assert "(These are 25 of the task's 28 required items" in prompts[0]
        assert [c["max_turns"] for c in calls[:1]] == [90]
        assert routing["external"] == [26]
        assert routing["untouched"] == [
            {"item_index": 27, "met": False, "files": ["data/a.csv"]}]
        assert routing["packs"] == 2
        assert [u["item_index"] for u in unmet] == [3, 27]
        assert unmet[1]["settled_by"] == "untouched_paths"
        assert "data/a.csv" in unmet[1]["evidence"]
        # Pack 0 flagged item 3 -> 3 samples; pack 1 (item 25) clean -> 1.
        assert n == 4
        assert sorted({c["sid"] for c in calls}) == [
            "delivery_judge-pre-p0-s0", "delivery_judge-pre-p0-s1",
            "delivery_judge-pre-p0-s2", "delivery_judge-pre-p1-s0"]
        assert contract is None

    def test_contract_is_asked_of_the_first_pack_only(
            self, leerie, tmp_path, monkeypatch):
        def verdict_for(kw):
            payload = kw["user_prompt"]
            arr = json.loads(payload.split("):\n", 1)[1].split("\n(These")[0])
            out = {"verdicts": [_judge_verdict(e["item_index"], True, "ok")
                                for e in arr]}
            if "DEFECT CONTRACT" in payload:
                out["contract"] = {"verdict": "met", "evidence": "holds"}
            return out

        st, items, repo, calls = self._setup(leerie, tmp_path, monkeypatch,
                                             verdict_for)
        unmet, n, contract = asyncio.run(leerie._delivery_judge_unmet(
            "task", items, repo, st, dict(leerie.DEFAULT_CAPS), MODELS,
            EFFORTS, phase_label="pre",
            contract_ctx={"defect_shape": "rows vanish", "sites": ["a"]}))
        with_contract = [c for c in calls
                         if "DEFECT CONTRACT" in c["user_prompt"]]
        assert len(with_contract) == 1
        assert with_contract[0]["sid"] == "delivery_judge-pre-p0-s0"
        assert contract["verdict"] == "met"
        assert [u["item_index"] for u in unmet] == [27]

    def test_single_pack_keeps_the_old_sid_and_no_slice_note(
            self, leerie, tmp_path, monkeypatch):
        st = _state(leerie, tmp_path)
        calls: list[dict] = []

        async def fake_claude_p(**kw):
            calls.append(kw)
            return {"verdicts": [_judge_verdict(0, True, "ok")]}

        monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
        tree = _delivery_tree(tmp_path / "tree")
        asyncio.run(leerie._delivery_judge_unmet(
            "task", [{"item": "one"}], tree, st,
            dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS, phase_label="pre"))
        assert [c["sid"] for c in calls] == ["delivery_judge-pre-s0"]
        assert "(These are" not in calls[0]["user_prompt"]


class TestCoverageJudgePayload:
    def test_required_items_are_numbered_in_the_payload(
            self, leerie, tmp_path, monkeypatch):
        st = _state(leerie, tmp_path, required_items=[
            {"item": "a", "span": "S3"}, {"item": "b", "source_ref": "x"}])
        seen: dict = {}

        async def fake_claude_p(**kw):
            seen.update(kw)
            return {"task_covered": True, "coverage_gaps": [],
                    "rationale": "ok"}

        monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
        asyncio.run(leerie.phase_planning_coverage_gate(
            [{"subtasks": []}], "task", st, dict(leerie.DEFAULT_CAPS),
            MODELS, EFFORTS))
        payload = json.loads(seen["user_prompt"].split("\n\n", 1)[1])
        assert payload["required_items"] == [
            {"item_index": 0, "item": "a", "span": "S3"},
            {"item_index": 1, "item": "b", "span": "x"}]


def test_design_states_the_delimiter_only_rule():
    text = (Path(__file__).resolve().parent.parent / "docs"
            / "DESIGN.md").read_text()
    i = text.index("### Language-to-JSON")
    j = text.index("\n### ", i + 1)
    section = text[i:j]
    assert "Delimiter-only cutting is not the deleted heading harvest" in section
    assert "assigns IDs and nothing else" in section
