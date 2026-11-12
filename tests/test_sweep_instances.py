"""A sweep's instances are items (DESIGN §8 *A sweep's instances are items*).

Navegando 4134b78c (#501, merged): a translations task whose one required
item carried the sweep as a broad part ("the entire site's user-facing
strings are present in the messages files"). Nothing enumerated what that
part covered; the judge and three falsifiers read it whole and said met while
an independent audit found ~13 components and ~255 server messages still
hard-coding Spanish. The classifier now types such a part as a sweep; code
enumerates its instances with a parser and makes each file with instances a
required item of its own, so a half-done sweep is a set of unmet items.

Real git repositories, real tree-sitter, the real `phase_classify` /
`_delivery_judge_unmet` / `_delivery_typed_evidence`; only `claude_p` is
stubbed."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from conftest import HAS_TREESITTER, run_git_cwd_first_stdout  # noqa: E402
from stub_helpers import _d1_covered  # noqa: E402

needs_ts = pytest.mark.skipif(not HAS_TREESITTER,
                              reason="tree-sitter stack unavailable")

COMPONENT = '''"use client";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

export function OrderCard({ status }: { status: string }) {
  const t = useTranslations("OrderCard");
  const busy = status === "PENDING";
  return (
    <div className="flex items-center gap-2" title="Ver pedido">
      <h2>{t("heading")}</h2>
      <span>Estado del pedido</span>
      <button onClick={() => toast.error("No se pudo guardar")}>Guardar</button>
      <p>{busy ? "Guardando..." : t("idle")}</p>
    </div>
  );
}
'''
SERVER = '''export async function saveOrder(id: string) {
  if (!id) return { error: "El pedido no existe" };
  console.error("save failed for order", id);
  const mode = "strict";
  return { error: "Not authorized" };
}
'''
TEST_FILE = '''import { OrderCard } from "./order-card";
it("renders", () => { expect("Estado del pedido").toBeTruthy(); });
'''
PY_FILE = '''def greet(name):
    """Docstring is not shown text."""
    legacy_format(name)
    return "Hola, bienvenido"
'''


def _commit(repo, files: dict[str, str], msg: str = "c") -> str:
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    run_git_cwd_first_stdout(repo, "add", "-A")
    run_git_cwd_first_stdout(repo, "-c", "user.email=t@t", "-c",
                             "user.name=t", "commit", "-q", "-m", msg)
    return run_git_cwd_first_stdout(repo, "rev-parse", "HEAD")


def _repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    _commit(path, {"src/components/order-card.tsx": COMPONENT,
                   "src/lib/actions/orders.ts": SERVER,
                   "src/components/order-card.test.tsx": TEST_FILE,
                   "tools/greet.py": PY_FILE,
                   "messages/en.json": '{"OrderCard": {"heading": "Order"}}'})
    return path


UI_SWEEP = {"part": "No user-facing text is hard-coded in components",
            "pattern": "text_literal", "paths": ["src/**/*.tsx"],
            "exclude_paths": ["**/*.test.tsx"],
            "ignore_calls": ["t", "useTranslations"],
            "ignore_attributes": ["className"]}
SERVER_SWEEP = {"part": "Server messages are translated",
                "pattern": "text_literal", "paths": ["src/lib/actions/**"],
                "ignore_calls": ["console.error"],
                "markers": ["el", "no", "ñ"]}


def _texts(got) -> list[tuple[str, str]]:
    return [(x["node"], x["text"]) for x in got["instances"]]


# === mechanical pieces =======================================================

@pytest.mark.parametrize("glob,path,hit", [
    ("src/**/*.tsx", "src/a.tsx", True),
    ("src/**/*.tsx", "src/x/y/a.tsx", True),
    ("src/**/*.tsx", "lib/src/a.tsx", False),
    ("src/*.tsx", "src/x/a.tsx", False),
    ("**/*.test.tsx", "src/x/a.test.tsx", True),
    ("**/*.test.tsx", "a.test.tsx", True),
    ("./src/lib/**", "src/lib/actions/a.ts", True),
    ("src/app/[locale]/*.tsx", "src/app/[locale]/p.tsx", True),
    ("src/app/[locale]/*.tsx", "src/app/l/p.tsx", False),
])
def test_globs_cross_directories_only_on_double_star(leerie, glob, path, hit):
    assert bool(leerie._sweep_glob_re(glob).match(path)) is hit


@pytest.mark.parametrize("text,is_text", [
    ("Nombre", True), ("Total", True), ("Close", True),
    ("Guardando...", True), ("¿Seguro?", True), ("Reintentar", True),
    ("Estado del pedido", True), ("Configuración", True),
    ("idle", False), ("ACTIVE", False), ("UserCard", False),
    ("text-sm", False), ("flex items-center gap-2", False),
    ("Nav.dashboard", False), ("/api/orders", False),
    ("https://example.com", False), ("user_id", False), ("—", False),
    ("42", False), ("x", False),
])
def test_text_shape_is_character_classes_only(leerie, text, is_text):
    assert leerie._sweep_is_text(text) is is_text


def test_markers_are_word_equality_or_one_character_containment(leerie):
    m = ["el", "ñ"]
    assert leerie._sweep_has_marker("El pedido no existe", m)
    assert leerie._sweep_has_marker("Contraseña", m)
    # "el" inside a word is no marker: equality on words, not substrings.
    assert not leerie._sweep_has_marker("Delete element", m)
    assert leerie._sweep_has_marker("anything", [])


# === enumeration on a real tree ==============================================

@needs_ts
def test_text_literals_are_enumerated_structurally(leerie, tmp_path):
    repo = _repo(tmp_path / "r")
    got = leerie._sweep_enumerate(repo, UI_SWEEP)
    assert got["error"] == "" and got["unparsed"] == []
    assert got["files"] == ["src/components/order-card.tsx"]
    # Kept: an attribute nobody ignored, JSX text, a toast argument, a
    # ternary branch. Dropped: the directive, imports, t()/useTranslations
    # keys, the className list, the compared enum value — and the test file.
    assert _texts(got) == [
        ("string", "Ver pedido"),
        ("jsx_text", "Estado del pedido"),
        ("string", "No se pudo guardar"),
        ("jsx_text", "Guardar"),
        ("string", "Guardando..."),
    ]
    assert [x["line"] for x in got["instances"]] == [9, 11, 12, 12, 13]


@needs_ts
def test_markers_and_ignored_callees_narrow_server_literals(leerie,
                                                            tmp_path):
    repo = _repo(tmp_path / "r")
    got = leerie._sweep_enumerate(repo, SERVER_SWEEP)
    # "Not authorized" holds no marker; the console.error text is ignored.
    assert _texts(got) == [("string", "El pedido no existe")]
    no_markers = leerie._sweep_enumerate(
        repo, {**SERVER_SWEEP, "markers": []})
    assert _texts(no_markers) == [("string", "El pedido no existe"),
                                  ("string", "Not authorized")]


@needs_ts
def test_call_sweeps_and_python_files(leerie, tmp_path):
    repo = _repo(tmp_path / "r")
    calls = leerie._sweep_enumerate(
        repo, {"part": "p", "pattern": "call", "names": ["legacy_format"],
               "paths": ["tools/**"]})
    assert [(x["file"], x["line"], x["text"]) for x in calls["instances"]] \
        == [("tools/greet.py", 3, "legacy_format(name)")]
    text = leerie._sweep_enumerate(
        repo, {"part": "p", "pattern": "text_literal", "paths": ["tools/**"]})
    # The docstring is not shown text; the returned greeting is.
    assert _texts(text) == [("string", "Hola, bienvenido")]


@needs_ts
def test_the_committed_tree_is_read_never_the_working_files(leerie,
                                                            tmp_path):
    repo = _repo(tmp_path / "r")
    (repo / "src/components/order-card.tsx").write_text("export {}\n")
    assert len(leerie._sweep_enumerate(repo, UI_SWEEP)["instances"]) == 5


@needs_ts
def test_each_file_with_instances_is_a_derived_item(leerie, tmp_path):
    repo = _repo(tmp_path / "r")
    _commit(repo, {"src/components/b.tsx":
                   'export const B = () => <p>Hola mundo</p>;\n'})
    parent = {"span": "S1", "item": "UI text is translated",
              "parts": [UI_SWEEP["part"], "Messages are in parity"],
              "verifiability": "tree", "sweeps": [UI_SWEEP]}
    other = {"span": "S2", "item": "README mentions it",
             "verifiability": "tree"}
    items, rec = leerie._derive_sweep_items([parent, other], repo, {})
    # Appended after the classifier's own: their indices never move.
    assert items[:2] == [parent, other]
    derived = items[2:]
    assert [d["sweep_of"]["file"] for d in derived] == [
        "src/components/b.tsx", "src/components/order-card.tsx"]
    assert all(d["sweep_of"]["item_index"] == 0 and d["span"] == "S1"
               and d["verifiability"] == "tree" for d in derived)
    assert derived[0]["parts"] == [
        'src/components/b.tsx:1 jsx_text "Hola mundo"']
    assert len(derived[1]["parts"]) == 5
    assert rec == [{"item_index": 0, "sweep": 0, "part": UI_SWEEP["part"],
                    "pattern": "text_literal", "instances": 6, "files": 2,
                    "unparsed": [], "error": "", "derived": [2, 3]}]
    # Deriving again (a re-classify) replaces, never stacks.
    again, _ = leerie._derive_sweep_items(items, repo, {})
    assert again == items


@needs_ts
def test_the_classifier_check_sends_back_empty_broad_and_unknown_sweeps(
        leerie, tmp_path):
    repo = _repo(tmp_path / "r")

    def result(sw, parts=None):
        return {"required_items": [{"item": "x", "span": "S1",
                                    "parts": parts or [UI_SWEEP["part"]],
                                    "sweeps": [sw]}]}
    assert leerie._sweep_issues(result(UI_SWEEP), repo, {}) == []
    empty = leerie._sweep_issues(
        result({**UI_SWEEP, "paths": ["nowhere/**"]}), repo, {})
    assert [i.split(":")[0] for i in empty] == ["SWEEP_EMPTY"]
    broad = leerie._sweep_issues(result(UI_SWEEP), repo,
                                 {"sweep_max_instances": 4})
    assert [i.split(":")[0] for i in broad] == ["SWEEP_TOO_BROAD"]
    unknown = leerie._sweep_issues(result(UI_SWEEP, parts=["other"]), repo,
                                   {})
    assert [i.split(":")[0] for i in unknown] == ["SWEEP_PART_UNKNOWN"]


@needs_ts
def test_still_present_follows_text_not_line_numbers(leerie, tmp_path):
    repo = _repo(tmp_path / "r")
    parent = {"span": "S1", "item": "x", "parts": [UI_SWEEP["part"]],
              "sweeps": [UI_SWEEP]}
    items, _ = leerie._derive_sweep_items([parent], repo, {})
    # Half the sweep: two literals routed through t(), a blank line added
    # above the rest so every line number moves.
    fixed = (COMPONENT.replace('<span>Estado del pedido</span>',
                               '<span>{t("status")}</span>')
             .replace('toast.error("No se pudo guardar")',
                      'toast.error(t("saveFailed"))')
             .replace('"use client";\n', '"use client";\n\n'))
    _commit(repo, {"src/components/order-card.tsx": fixed})
    left = leerie._sweep_still_present(items, repo)
    assert [(x["node"], x["text"]) for x in left[1]] == [
        ("string", "Ver pedido"), ("jsx_text", "Guardar"),
        ("string", "Guardando...")]
    sec = leerie._sweep_evidence_section(items, left)
    assert sec.startswith("SWEEP INSTANCES STILL PRESENT")
    assert "item 1: 3 of 5 still present" in sec
    assert '"Estado del pedido"' not in sec
    # A sweep finished leaves nothing to show.
    assert leerie._sweep_evidence_section(items, {1: []}) == ""


# === wiring: the real phase_classify, the real delivery judge ================

@needs_ts
def test_phase_classify_derives_the_items_after_sending_back_an_empty_sweep(
        leerie, tmp_path, monkeypatch):
    from tests.test_phase_classify_satisfied_or_preserve import (
        EFFORTS, MODELS, _caps, _minimal_state)
    st = _minimal_state(leerie, tmp_path)
    st.data["planning_worktree"] = str(_repo(tmp_path / "plan"))
    base = _d1_covered()
    item = {**base["required_items"][0], "parts": [UI_SWEEP["part"]]}
    bad = {**item, "sweeps": [{**UI_SWEEP, "paths": ["web/**/*.tsx"]}]}
    good = {**item, "sweeps": [UI_SWEEP]}
    prompts: list[str] = []

    async def fake(**kw):
        if kw["schema_key"] != "classifier":
            return {"forbidden_commands": []}
        prompts.append(kw["user_prompt"])
        return {**base, "categories": ["bug-fixing"],
                "required_items": [bad if len(prompts) == 1 else good]}
    monkeypatch.setattr(leerie, "claude_p", fake)
    asyncio.run(leerie.phase_classify("translate the UI", st, _caps(leerie),
                                      False, MODELS, EFFORTS))
    assert len(prompts) == 2
    assert "SWEEP_EMPTY: required_items[0].sweeps[0]" in prompts[1]
    items = st.data["required_items"]
    assert len(items) == 2 and items[0]["sweeps"] == [UI_SWEEP]
    spec = {k: v for k, v in UI_SWEEP.items() if k != "part"}
    assert items[1]["sweep_of"] == {"item_index": 0, "sweep": 0,
                                    "part": UI_SWEEP["part"],
                                    "file": "src/components/order-card.tsx",
                                    "spec": spec}
    assert st.data["sweeps"][0]["derived"] == [1]
    # Requirement coverage reads the classifier's answer, not the derived
    # items: one item on S1 is no duplicate.
    assert st.data["requirement_coverage"]["holes"] == {}


@needs_ts
def test_a_task_without_a_sweep_is_unchanged(leerie, tmp_path, monkeypatch):
    from tests.test_phase_classify_satisfied_or_preserve import (
        EFFORTS, MODELS, _caps, _minimal_state)
    st = _minimal_state(leerie, tmp_path)
    st.data["planning_worktree"] = str(_repo(tmp_path / "plan"))
    base = _d1_covered()

    async def fake(**kw):
        if kw["schema_key"] != "classifier":
            return {"forbidden_commands": []}
        return {**base, "categories": ["bug-fixing"]}
    monkeypatch.setattr(leerie, "claude_p", fake)
    asyncio.run(leerie.phase_classify("fix it", st, _caps(leerie), False,
                                      MODELS, EFFORTS))
    assert st.data["required_items"] == base["required_items"]
    assert st.data["sweeps"] == []


@needs_ts
def test_the_judge_sees_the_instances_and_what_is_left(leerie, tmp_path,
                                                       monkeypatch):
    run_dir = tmp_path / ".leerie" / "runs" / "sweep-judge"
    staging = _repo(run_dir / "worktrees" / "staging")
    st = leerie.State(tmp_path / ".leerie", "sweep-judge")
    parent = {"span": "S1", "item": "UI text is translated",
              "parts": [UI_SWEEP["part"]], "verifiability": "tree",
              "sweeps": [UI_SWEEP]}
    items, _ = leerie._derive_sweep_items([parent], staging, {})
    st.data = {"task": "t", "worker_count": 0, "required_items": items}
    st.save()
    _commit(staging, {"src/components/order-card.tsx": COMPONENT.replace(
        "<span>Estado del pedido</span>", '<span>{t("status")}</span>')})
    evidence, counts = leerie._delivery_typed_evidence(
        st, run_dir, refresh_tokens=False)
    assert counts["sweep_instances_left"] == 4
    prompts: list[str] = []

    async def fake(**kw):
        prompts.append(kw["user_prompt"])
        return {"verdicts": [
            {"item_index": i, "met": False, "evidence": "left",
             "parts": [{"requirement": "r", "kind": "static",
                        "status": "unmet", "cites": []}]}
            for i in map(int, re.findall(r'"item_index": (\d+)',
                                         kw["user_prompt"]))]}
    monkeypatch.setattr(leerie, "claude_p", fake)
    asyncio.run(leerie._delivery_judge_unmet(
        "t", items, staging, st, dict(leerie.DEFAULT_CAPS),
        {"delivery_judge": "sonnet"}, {"delivery_judge": "medium"}, "pre",
        typed_evidence=evidence))
    p = prompts[0]
    entries = json.loads(p.split("ship):\n", 1)[1].split("\n\n", 1)[0]
                         .rsplit("\n(These", 1)[0])
    by_idx = {e["item_index"]: e for e in entries}
    assert "instances" not in by_idx[0]
    assert by_idx[1]["instances"] == items[1]["parts"]
    assert "SWEEP INSTANCES STILL PRESENT" in p
    assert "item 1: 4 of 5 still present" in p


def test_schema_and_prompt_carry_the_sweep_fields(leerie):
    props = (leerie.SCHEMAS["classifier"]["properties"]["required_items"]
             ["items"]["properties"]["sweeps"]["items"])
    assert props["properties"]["pattern"]["enum"] == ["text_literal", "call"]
    assert props["required"] == ["part", "pattern", "paths"]
    prompt = leerie._load_prompt("classifier")
    for field in ("sweeps", "text_literal", "ignore_calls",
                  "ignore_attributes", "markers", "exclude_paths"):
        assert f"`{field}`" in prompt or f'"{field}"' in prompt, field


# === the defect-scope audit's sites carry sweeps too =========================

SITE_SWEEP = {k: v for k, v in UI_SWEEP.items() if k != "part"}


def _audit_reply(sweep):
    return {"applicable": True, "defect_shape": "UI text bypasses messages",
            "reading": {"kind": "one_reading", "narrow": "", "broad": ""},
            "sites": [
                {"file": "messages/en.json", "symbol": "values",
                 "role": "decision_site", "disposition": "fix",
                 "extent": "one_place",
                 "resolution_kind": "recompute", "resolution": "fix values"},
                {"file": "src/components", "symbol": "hard-coded UI text",
                 "role": "bypass", "disposition": "fix", "extent": "pattern",
                 "resolution_kind": "relink",
                 "resolution": "route the text through the messages",
                 "sweep": sweep}],
            "chokepoint": {"exists": False}}


@needs_ts
def test_the_audit_sends_back_an_empty_site_sweep_and_derives_its_items(
        leerie, tmp_path, monkeypatch):
    """Issue 12/16: the directory-shaped site of navegando 4134b78c
    (`src/components :: hardcoded UI literals`), now with a sweep. The real
    `phase_defect_scope_audit`: a sweep that matches nothing goes back to the
    auditor; the good one's files become required items after the
    classifier's, each naming the site."""
    leerie_root = tmp_path / ".leerie"
    st = leerie.State(leerie_root, "sweep-audit")
    own = {"span": "S1", "item": "messages are complete",
           "verifiability": "tree"}
    st.data = {"task": "t", "worker_count": 0, "categories": ["bug-fixing"],
               "required_items": [own],
               "planning_worktree": str(_repo(tmp_path / "plan"))}
    st.save()
    prompts: list[str] = []

    async def fake(**kw):
        assert kw["schema_key"] == "defect_scope_auditor"
        prompts.append(kw["user_prompt"])
        return _audit_reply(SITE_SWEEP if len(prompts) > 1
                            else {**SITE_SWEEP, "paths": ["web/**"]})
    monkeypatch.setattr(leerie, "claude_p", fake)
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "t", st, caps, {"defect_scope_auditor": "sonnet"},
        {"defect_scope_auditor": "medium"}))
    assert len(prompts) == 2
    assert "SWEEP_EMPTY: sites[1].sweep" in prompts[1]
    assert scope["sites"][1]["sweep"] == SITE_SWEEP
    # The items are derived once the reading is settled (one reading here).
    assert st.data["required_items"] == [own]
    st.data["defect_scope"] = scope
    leerie._settle_scope_reading(st)
    items = st.data["required_items"]
    assert items[0] == own and len(items) == 2
    d = items[1]
    assert d["sweep_of"] == {"site_id": "D2",
                             "file": "src/components/order-card.tsx",
                             "spec": SITE_SWEEP}
    assert d["source_ref"] == "D2" and len(d["parts"]) == 5
    assert d["item"].startswith("Audited defect site D2 (hard-coded UI text)")
    assert st.data["sweeps"][0]["site_id"] == "D2"
    assert st.data["sweeps"][0]["derived"] == [1]


@needs_ts
def test_site_derived_items_are_re_enumerated_from_their_own_spec(
        leerie, tmp_path):
    repo = _repo(tmp_path / "r")
    scope = leerie._scope_sites(_audit_reply(SITE_SWEEP))
    items, rec = leerie._derive_site_sweep_items(
        [{"item": "own"}], {"sites": scope}, repo)
    assert [r["site_id"] for r in rec] == ["D2"] and len(items) == 2
    # A ruled-out site's sweep derives nothing; deriving again replaces.
    again, _ = leerie._derive_site_sweep_items(items, {"sites": scope}, repo)
    assert again == items
    _commit(repo, {"src/components/order-card.tsx": COMPONENT.replace(
        "<span>Estado del pedido</span>", '<span>{t("status")}</span>')})
    left = leerie._sweep_still_present(items, repo)
    assert len(left[1]) == 4


def test_auditor_schema_and_prompt_carry_the_site_sweep(leerie):
    site = (leerie.SCHEMAS["defect_scope_auditor"]["properties"]["sites"]
            ["items"]["properties"])
    assert site["sweep"]["required"] == ["pattern", "paths"]
    prompt = leerie._load_prompt("defect_scope_auditor")
    assert "`sweep`" in prompt and "`ignore_calls`" in prompt


@needs_ts
@pytest.mark.parametrize("site,issue", [
    # A pattern needs its sweep.
    ({"file": "src/components/order-card.tsx", "extent": "pattern"},
     "SWEEP_MISSING: sites[0]"),
    # A directory is a pattern, whatever the auditor typed (the 4134b78c
    # site `src/components`).
    ({"file": "src/components", "extent": "one_place"},
     "SITE_IS_A_DIRECTORY: sites[0]"),
    ({"file": "src/components/", "extent": "one_place"},
     "SITE_IS_A_DIRECTORY: sites[0]"),
    # One file, one place: nothing to send back.
    ({"file": "src/components/order-card.tsx", "extent": "one_place"}, None),
    # A ruled-out site is never checked.
    ({"file": "src/components", "extent": "one_place",
      "disposition": "ruled_out"}, None),
])
def test_every_fix_site_types_its_extent_and_a_pattern_is_swept(
        leerie, tmp_path, site, issue):
    repo = _repo(tmp_path / "r")
    s = {"symbol": "s", "disposition": "fix", **site}
    got = leerie._site_sweep_issues({"sites": [s]}, repo, {})
    if issue is None:
        assert got == []
    else:
        assert len(got) == 1 and got[0].startswith(issue)
