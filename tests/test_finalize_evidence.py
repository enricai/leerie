"""Finalize evidence (stage 6b): the advisory whole-PR review (N2f, DESIGN §9
*An advisory whole-PR review feeds the final conformer*) and the contract-
literal check (N7d v7e, DESIGN §8 *Typed evidence for the delivery judge*).
The site-token check (N7a) is in tests/test_inline_examples.py.

Discipline: the pure verdict is executed on values; the wiring is driven
through the real functions against real git trees with only `claude_p`
stubbed — what the delivery judge and the final conformer actually receive,
what State records, and that nothing here changes the run's outcome.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.test_run_final_conformance import _clean_result, env  # noqa: F401

MODELS = {"pr_reviewer": "opus", "contract_extractor": "sonnet",
          "delivery_judge": "sonnet", "conformer": "sonnet"}
EFFORTS = {"pr_reviewer": "high", "contract_extractor": "medium",
           "delivery_judge": "medium", "conformer": "low"}


def _git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                       text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout


# === contract literals: the pure verdict (bench q10 check7e) ==============

def _ext(lit, kind="string_value", cp="feat-002", sym=None, **kw):
    return {"counterpart_sid": cp, "literal": lit, "literal_kind": kind,
            "symbol": sym, **kw}


def _verdict(leerie, ext, *, tree=(), grep=(), own=(), cp_added=()):
    return leerie._contract_literal_verdict(
        ext, tree_files=list(tree), grep=list(grep), own_files=set(own),
        cp_added=None if cp_added is None else list(cp_added))


@pytest.mark.parametrize("ext,why", [
    (_ext("a b"), "not a token literal"),
    (_ext("x/y", kind="identifier"), "not a token literal"),
    (_ext("auto", cp=None), "no check"),
    (_ext("auto", literal_unverified=True), "no check"),
    (_ext("42", kind="other"), "no check"),
])
def test_unchecked_contracts_never_flag(leerie, ext, why):
    assert _verdict(leerie, ext) == (False, why)


def test_counterpart_added_line_honours(leerie):
    ext = _ext("auto")
    assert _verdict(leerie, ext, cp_added=[
        ("src/pay.ts", "mode: 'auto',")]) == (
        False, "on a counterpart added line")


def test_only_non_test_code_outside_the_owner_honours(leerie):
    ext = _ext("auto")
    cp = [("src/pay.ts", "mode: 'manual',")]
    grep_doc = [("docs/modes.md", "the 'auto' mode"),
                ("messages/en.json", '"auto": "Automatic"'),
                ("src/pay.test.ts", "expect(mode).toBe('auto')"),
                ("src/owner.ts", "if (mode === 'auto')")]
    # v7e rule 2: a doc, an i18n file, a test and the owner's own file
    # honour nothing.
    assert _verdict(leerie, ext, grep=grep_doc, own={"src/owner.ts"},
                    cp_added=cp) == (True, "counterpart diff missing")
    grep_code = grep_doc + [("src/billing/plan.ts", "const m = 'auto';")]
    assert _verdict(leerie, ext, grep=grep_code, own={"src/owner.ts"},
                    cp_added=cp) == (False, "honoured in non-test code")
    # The extension list goes past the bench's JS/TS one.
    assert _verdict(leerie, ext, grep=[("app/plan.py", "m = 'auto'")],
                    cp_added=cp)[0] is False


def test_a_near_variant_on_the_counterpart_flags_even_when_honoured(leerie):
    ext = _ext("auto")
    grep = [("src/billing/plan.ts", "const m = 'auto';")]
    flag, why = _verdict(leerie, ext, grep=grep,
                         cp_added=[("src/pay.ts", "mode: 'autopay',")])
    assert flag and why == "counterpart emits a near-variant 'autopay'"
    # In a test file the variant does not count.
    assert _verdict(leerie, ext, grep=grep, cp_added=[
        ("src/pay.test.ts", "mode: 'autopay',")])[0] is False


@pytest.mark.parametrize("lit,tok,near", [
    ("auto", "autopay", True), ("item", "items", True),
    ("user_id", "userId", True), ("auto", "auto", False),
    ("settings", "settings:read", False), ("x", "x_v2", False),
    ("abc", "abcdef", False),  # shorter than 4
    ("cart", "cartographers", False),  # under half the length
])
def test_near_variant_table(leerie, lit, tok, near):
    assert leerie._contract_near_variant(lit, tok) is near


def test_a_counterpart_symbol_honours(leerie):
    ext = _ext("auto", sym="PAY_MODE")
    assert _verdict(leerie, ext, cp_added=[
        ("src/pay.ts", "mode: PAY_MODE,")]) == (
        False, "symbol used by the counterpart")


def test_counterpart_not_integrated_reads_the_tree(leerie):
    ext = _ext("auto")
    assert _verdict(leerie, ext, cp_added=None, own={"src/o.ts"},
                    grep=[("src/o.ts", "'auto'")])[0] is True
    assert _verdict(leerie, ext, cp_added=None, own={"src/o.ts"},
                    grep=[("src/p.ts", "'auto'")])[0] is False


def test_routes(leerie):
    ext = _ext("/api/ads/click", kind="route_path")
    assert _verdict(leerie, ext, tree=["src/lib/a.ts"]) == (
        False, "no app router")
    router = ["src/app/page.tsx", "src/app/api/ads/clicks/route.ts"]
    assert _verdict(leerie, ext, tree=router, cp_added=[]) == (
        True, "route not served")
    served = router + ["src/app/(site)/api/ads/click/route.ts"]
    assert _verdict(leerie, ext, tree=served, cp_added=[])[0] is False
    assert _verdict(leerie, ext, tree=router, cp_added=[
        ("src/lib/client.ts", "fetch('/api/ads/click')")])[0] is False
    assert _verdict(leerie, ext, tree=router, cp_added=[
        ("src/lib/client.ts", "fetch('/api/ads/clicks')")])[0] is True


# === contract literals: extraction and the check on a real tree ============

def _integrated_repo(tmp_path, cp_text):
    """A staging worktree whose first-parent history integrates feat-001
    (the owner, consuming a mode) and feat-002 (the counterpart)."""
    leerie_dir = tmp_path / "run"
    staging = leerie_dir / "worktrees" / "staging"
    staging.mkdir(parents=True)
    _git(staging, "init", "-q", "-b", "main")
    _git(staging, "config", "user.email", "t@t")
    _git(staging, "config", "user.name", "t")
    (staging / "README.md").write_text("x\n")
    _git(staging, "add", ".")
    _git(staging, "commit", "-q", "-m", "base")
    _git(staging, "checkout", "-q", "-b", "leerie/runs/r")
    for sid, path, text in (
            ("feat-001", "src/owner.ts", "if (mode === 'auto') go();\n"),
            ("feat-002", "src/pay.ts", cp_text)):
        _git(staging, "checkout", "-q", "-b", sid, "main")
        (staging / path).parent.mkdir(parents=True, exist_ok=True)
        (staging / path).write_text(text)
        _git(staging, "add", ".")
        _git(staging, "commit", "-q", "-m", sid)
        _git(staging, "checkout", "-q", "leerie/runs/r")
        _git(staging, "merge", "-q", "--no-ff", "-m",
             f"leerie: integrate {sid}", sid)
    (leerie_dir / "plan.json").write_text(json.dumps({"subtasks": {
        "feat-001": {"title": "Consume the payment mode"},
        "feat-002": {"title": "Emit the payment mode"}}}))
    return leerie_dir, staging


def _risk_state(leerie, tmp_path, detail):
    st = leerie.State(tmp_path / ".leerie", "r")
    st.data = {"working_branch": "main", "risk_register": {
        "feat-001": [
            {"kind": "untested_change", "detail": "no test",
             "source": "implementer"},
            {"kind": "external_contract_assumption", "detail": detail,
             "where": "src/owner.ts", "source": "implementer"}]}}
    st.save()
    return st


def _stub_extractor(leerie, monkeypatch, replies):
    calls: list[dict] = []

    async def fake(**kw):
        assert kw["schema_key"] == "contract_extractor"
        calls.append(kw)
        r = replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r
    monkeypatch.setattr(leerie, "claude_p", fake)
    return calls


def test_extract_validates_caches_and_the_check_flags(
        leerie, tmp_path, monkeypatch):
    leerie_dir, _ = _integrated_repo(tmp_path, "export const MODE = "
                                     "'autopay';\n")
    st = _risk_state(leerie, tmp_path,
                     "assumes feat-002 emits mode 'auto'")
    calls = _stub_extractor(leerie, monkeypatch, [{
        "counterpart_sid": "feat-002", "literal": "auto",
        "literal_kind": "string_value", "symbol": None}])
    asyncio.run(leerie._extract_contract_literals(
        leerie_dir, st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS))
    assert len(calls) == 1
    payload = calls[0]["user_prompt"]
    assert '"id": "feat-002"' in payload and "Emit the payment mode" in payload
    assert calls[0]["autonomous"] is False
    rec = st.data["contract_literal_checks"]["feat-001#1"]
    assert rec["extracted"]["counterpart_sid"] == "feat-002"
    assert "literal_unverified" not in rec["extracted"]
    # Cached: a resume never re-asks.
    asyncio.run(leerie._extract_contract_literals(
        leerie_dir, st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS))
    assert len(calls) == 1

    flagged = leerie._check_contract_literals(st, leerie_dir)
    assert [r["owner_sid"] for r in flagged] == ["feat-001"]
    assert rec["flag"] is True and rec["why"] == "counterpart diff missing"
    sec = leerie._contract_literal_evidence_section(flagged)
    assert sec.startswith("CONTRACT LITERAL FLAGS")
    row = json.loads(sec.split("\n", 1)[1])[0]
    assert (row["owner_sid"], row["counterpart_sid"], row["literal"]) == (
        "feat-001", "feat-002", "auto")


def test_an_honoured_contract_is_not_flagged(leerie, tmp_path, monkeypatch):
    leerie_dir, _ = _integrated_repo(tmp_path, "export const MODE = "
                                     "'auto';\n")
    st = _risk_state(leerie, tmp_path, "assumes feat-002 emits mode 'auto'")
    _stub_extractor(leerie, monkeypatch, [{
        "counterpart_sid": "feat-002", "literal": "auto",
        "literal_kind": "string_value", "symbol": None}])
    asyncio.run(leerie._extract_contract_literals(
        leerie_dir, st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS))
    assert leerie._check_contract_literals(st, leerie_dir) == []
    rec = st.data["contract_literal_checks"]["feat-001#1"]
    assert rec["flag"] is False and rec["why"] == "on a counterpart added line"


@pytest.mark.parametrize("reply,expect", [
    # not a subtask of this run
    ({"counterpart_sid": "feat-999", "literal": "auto"},
     {"counterpart_sid": None}),
    # the owner itself
    ({"counterpart_sid": "feat-001", "literal": "auto"},
     {"counterpart_sid": None}),
    # a literal the risk never says
    ({"counterpart_sid": "feat-002", "literal": "manual"},
     {"counterpart_sid": "feat-002", "literal_unverified": True}),
])
def test_extraction_is_validated_by_code(leerie, tmp_path, monkeypatch,
                                         reply, expect):
    leerie_dir, _ = _integrated_repo(tmp_path, "x\n")
    st = _risk_state(leerie, tmp_path, "assumes feat-002 emits mode 'auto'")
    _stub_extractor(leerie, monkeypatch, [{
        "literal_kind": "string_value", "symbol": None, **reply}])
    asyncio.run(leerie._extract_contract_literals(
        leerie_dir, st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS))
    ext = st.data["contract_literal_checks"]["feat-001#1"]["extracted"]
    for k, v in expect.items():
        assert ext.get(k) == v
    assert leerie._check_contract_literals(st, leerie_dir) == []


def test_an_extractor_failure_is_recorded_not_raised(leerie, tmp_path,
                                                    monkeypatch):
    leerie_dir, _ = _integrated_repo(tmp_path, "x\n")
    st = _risk_state(leerie, tmp_path, "assumes 'auto'")
    _stub_extractor(leerie, monkeypatch, [leerie.WorkerError("boom")])
    asyncio.run(leerie._extract_contract_literals(
        leerie_dir, st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS))
    rec = st.data["contract_literal_checks"]["feat-001#1"]
    assert "error" in rec and "extracted" not in rec


# === the delivery judge receives the typed evidence ========================

def _judge_capture(leerie, monkeypatch):
    prompts: list[str] = []

    async def fake(**kw):
        assert kw["schema_key"] == "delivery_judge"
        prompts.append(kw["user_prompt"])
        # A met the citation screen verifies (DESIGN §8 *Delivery
        # detection*): an absence claim `git grep` confirms on any tree.
        return {"verdicts": [{"item_index": 0, "met": True, "evidence": "e",
                              "parts": [{"requirement": "never name it",
                                         "kind": "static", "status": "met",
                                         "cites": [{"path": "ABSENT",
                                                    "line_start": 0,
                                                    "line_end": 0,
                                                    "quote": "zz-never-zz"}]
                                         }]}]}
    monkeypatch.setattr(leerie, "claude_p", fake)
    return prompts


def test_prejudge_sends_the_sections_and_records_counts(
        leerie, tmp_path, monkeypatch):
    leerie_dir, staging = _integrated_repo(
        tmp_path, "export const MODE = 'autopay';\n")
    st = _risk_state(leerie, tmp_path, "assumes feat-002 emits mode 'auto'")
    st.data.update({
        "task": "t", "required_items": [{"item": "never name the site"}],
        "contract_literal_checks": {"feat-001#1": {
            "owner_sid": "feat-001", "risk_index": 1,
            "detail": "assumes feat-002 emits mode 'auto'", "where": "",
            "extracted": {"counterpart_sid": "feat-002", "literal": "auto",
                          "literal_kind": "string_value", "symbol": None}}},
        "site_token_warnings": [{"token": "acmeshop", "file": "src/a.ts",
                                 "added_line": "acmeshop", "report_example":
                                 "https://acmeshop.example"}]})
    prompts = _judge_capture(leerie, monkeypatch)
    asyncio.run(leerie._run_delivery_prejudge(
        leerie_dir, st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS))
    (p,) = prompts
    assert "SITE-TOKEN HITS" in p and "CONTRACT LITERAL FLAGS" in p
    # Evidence precedes the closing instruction.
    assert p.index("CONTRACT LITERAL FLAGS") < p.index(
        "Return one verdict per item_index")
    assert st.data["delivery_gate"]["evidence_before"] == {
        "site_token_hits": 1, "contract_flags": 1}


def test_without_evidence_the_judge_prompt_is_unchanged(
        leerie, tmp_path, monkeypatch):
    leerie_dir, _ = _integrated_repo(tmp_path, "x\n")
    st = _risk_state(leerie, tmp_path, "d")
    st.data.update({"task": "t",
                    "required_items": [{"item": "never name the site"}]})
    prompts = _judge_capture(leerie, monkeypatch)
    staging = leerie_dir / "worktrees" / "staging"
    for ev in (None, []):
        asyncio.run(leerie._delivery_judge_unmet(
            "t", st.data["required_items"], staging, st,
            dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS, phase_label="pre",
            typed_evidence=ev))
    assert prompts[0] == prompts[1]
    assert "SITE-TOKEN" not in prompts[0] and "CONTRACT LITERAL" not in \
        prompts[0]


# === the advisory whole-PR review =========================================

def _review_env(env, required=True):  # noqa: F811
    st = env["st"]
    st.data["task"] = "Make f return 1 and keep the API stable"
    if required:
        st.data["required_items"] = [{"item": "f returns 1"},
                                     "the public API is unchanged"]
    st.save()
    return env


def _stub_reviewer(leerie, monkeypatch, findings, *, mutate=None):
    calls: list[dict] = []

    async def fake(**kw):
        calls.append(kw)
        if kw["schema_key"] == "pr_reviewer":
            if mutate:
                mutate(Path(kw["cwd"]))
            return {"findings": findings}
        return _clean_result()
    monkeypatch.setattr(leerie, "claude_p", fake)
    return calls


GOOD = {"item_id": "item 0", "kind": "unmet_requirement", "path": "src.py",
        "line_start": 1, "line_end": 2, "quote": "def f():\n    pass",
        "item_span_quote": "returns 1", "explanation": "f returns None"}
BAD_QUOTE = {**GOOD, "item_id": None, "kind": "bug",
             "quote": "def g(): return 2", "item_span_quote": None}


def test_review_payload_verification_and_record(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    _review_env(env)
    calls = _stub_reviewer(leerie, monkeypatch, [GOOD, BAD_QUOTE])
    rec = asyncio.run(leerie._run_pr_review(
        env["run_dir"], env["st"], env["caps"], MODELS, EFFORTS))
    (call,) = calls
    assert call["model"] == "opus" and call["effort"] == "high"
    assert call["autonomous"] is False
    assert call["max_turns"] == leerie.PR_REVIEW_MAX_TURNS == 150
    up = call["user_prompt"]
    assert '"item_id": "0"' in up and '"item_id": "1"' in up
    assert "the public API is unchanged" in up
    assert "+def f():" in up and "src.py" in up
    assert up.index("REQUIRED ITEMS") < up.index("FULL DIFF")
    f0, f1 = rec["findings"]
    assert f0["verified"] and f0["item_id_norm"] == "0" and f0["span_ok"]
    assert f0["line_ok"] is True
    assert not f1["verified"]
    assert (rec["n_findings"], rec["n_verified"]) == (2, 1)
    assert env["st"].data["pr_review"] is rec


def test_only_verified_findings_reach_the_final_conformer(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    _review_env(env)
    calls = _stub_reviewer(leerie, monkeypatch, [GOOD, BAD_QUOTE])
    asyncio.run(leerie._run_pr_review(
        env["run_dir"], env["st"], env["caps"], MODELS, EFFORTS))
    asyncio.run(leerie._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))
    conf = [c for c in calls if c["schema_key"] == "conformer"]
    assert conf
    up = conf[0]["user_prompt"]
    assert "ADVISORY WHOLE-PR REVIEW" in up
    shown = json.loads(up.split("ADVISORY WHOLE-PR REVIEW", 1)[1]
                       .split("\n", 1)[1].split("\n]\n", 1)[0] + "\n]")
    assert [f["quote"] for f in shown] == [GOOD["quote"]]


def test_no_verified_finding_means_no_section(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    _review_env(env)
    _stub_reviewer(leerie, monkeypatch, [BAD_QUOTE])
    asyncio.run(leerie._run_pr_review(
        env["run_dir"], env["st"], env["caps"], MODELS, EFFORTS))
    assert leerie._format_pr_review_section(env["st"]) is None


def test_reviewer_changes_never_reach_staging(env, monkeypatch):  # noqa: F811
    """Rewritten from `test_reviewer_changes_are_reverted` and the
    non-UTF-8 leftover test: the reviewer runs in its own checkout of HEAD
    (DESIGN §8 *A worker that does not commit never runs in a tree leerie
    measures*), so nothing it writes, deletes or commits is ever in
    staging to restore. tests/test_pr_review_runs_outside_staging.py has
    review round 15 M1's shapes."""
    leerie = env["leerie"]
    _review_env(env)
    st_ = env["staging"]
    head = _git(st_, "rev-parse", "HEAD").strip()
    with open(bytes(st_) + b"/before\xe9.txt", "wb") as f:
        f.write(b"there before the review\n")
    seen: list[Path] = []

    def mutate(cwd: Path):
        seen.append(cwd)
        (cwd / "src.py").write_text("changed\n")
        (cwd / "coverage.out").write_text("x\n")
        with open(bytes(cwd) + b"/caf\xe9.out", "wb") as f:
            f.write(b"x\n")
        _git(cwd, "commit", "-qam", "reviewer")
    _stub_reviewer(leerie, monkeypatch, [], mutate=mutate)
    rec = asyncio.run(leerie._run_pr_review(
        env["run_dir"], env["st"], env["caps"], MODELS, EFFORTS))
    assert seen and seen[0] != st_ and not seen[0].exists()
    assert "tree_restored" not in rec and "error" not in rec
    assert (st_ / "src.py").read_text() == "def f():\n    pass\n"
    assert not (st_ / "coverage.out").exists()
    assert not os.path.exists(bytes(st_) + b"/caf\xe9.out")
    assert os.path.exists(bytes(st_) + b"/before\xe9.txt")
    assert _git(st_, "rev-parse", "HEAD").strip() == head


def test_review_runs_once_and_a_crash_is_recorded(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    _review_env(env)

    async def crash(**kw):
        raise leerie.WorkerError("boom")
    monkeypatch.setattr(leerie, "claude_p", crash)
    rec = asyncio.run(leerie._run_pr_review(
        env["run_dir"], env["st"], env["caps"], MODELS, EFFORTS))
    assert "boom" in rec["error"] and rec["findings"] == []
    calls = _stub_reviewer(leerie, monkeypatch, [GOOD])
    again = asyncio.run(leerie._run_pr_review(
        env["run_dir"], env["st"], env["caps"], MODELS, EFFORTS))
    assert calls == [] and again is env["st"].data["pr_review"]


def test_skipped_once_the_final_conformance_ran(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    _review_env(env)
    env["st"].data["conformance"] = {"_final": {"result": {}}}
    calls = _stub_reviewer(leerie, monkeypatch, [GOOD])
    assert asyncio.run(leerie._run_pr_review(
        env["run_dir"], env["st"], env["caps"], MODELS, EFFORTS)) is None
    assert calls == [] and "pr_review" not in env["st"].data


def test_the_contract_is_a_reviewable_item(leerie, tmp_path):
    st = leerie.State(tmp_path / ".leerie", "r")
    st.data = {"required_items": ["a"], "defect_scope": {
        "applicable": True, "defect_shape": "shape",
        "sites": [{"file": "f.py", "symbol": "g"}]}}
    items = leerie._pr_review_items(st)
    assert [i["item_id"] for i in items] == ["0", "contract"]
    assert json.loads(items[1]["text"])["defect_shape"] == "shape"


def test_findings_never_change_the_outcome(leerie, tmp_path):
    st = leerie.State(tmp_path / ".leerie", "r")
    st.data = {"subtask_status": {"feat-001": "complete"}}
    before = leerie._decide_outcome(st, any_commits=True)
    st.data["pr_review"] = {"findings": [{**GOOD, "verified": True}] * 5,
                            "n_verified": 5}
    st.data["contract_literal_checks"] = {"a#0": {"flag": True}}
    st.data["site_token_warnings"] = [{"token": "x", "file": "y"}]
    assert leerie._decide_outcome(st, any_commits=True) == before == "done"


def test_run_phases_order(leerie):
    """After integration: site tokens, contract extraction, the delivery
    gate's first half, the review, then the final conformer it feeds —
    each wrapped so it cannot abort the run."""
    src = inspect.getsource(leerie._run_phases)
    order = [src.index(s) for s in (
        "await phase_execute(", "_warn_site_token_leaks(st, leerie_dir)",
        "await _extract_contract_literals(", "await _run_delivery_prejudge(",
        "await _run_pr_review(", "await _run_final_conformance(")]
    assert order == sorted(order)
    window = src[order[0]:order[-1]]
    assert window.count("except Exception") >= 4


def test_schemas_and_tiers(leerie):
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate({"findings": [GOOD]}, leerie.SCHEMAS["pr_reviewer"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"findings": [{**GOOD, "kind": "style"}]},
                            leerie.SCHEMAS["pr_reviewer"])
    jsonschema.validate({"counterpart_sid": None, "literal": None,
                         "literal_kind": "other", "symbol": None},
                        leerie.SCHEMAS["contract_extractor"])
    assert leerie.MODEL_DEFAULT_PER_WORKER["pr_reviewer"] == "opus"
    assert "contract_extractor" not in leerie.MODEL_DEFAULT_PER_WORKER
    assert {"pr_reviewer", "contract_extractor"} <= \
        leerie.PLANNING_WORKER_TYPES
