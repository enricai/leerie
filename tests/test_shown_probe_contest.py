"""A shown probe never forces a change that breaks what an existing test pins
(DESIGN §8 *Delivery re-drive* rule 4, *A shown probe is contested only by a
measured conflict with an existing test*).

Lane XC's closure bench (leerie 0.46.0): with a falsifier probe shown, one
fixer obeyed a probe that contradicts the report and broke two existing
tests (N3, barnacle 80e66fce item 4), another bent code against tests the
repository pins (G4-shown-2, 0d1c4033 item 29), and the contest path, a
worker's quoted say-so, was used once in 14 samples. A quote check is no
evidence (lane TB: 4 of 6 real weakenings passed it). So the conflict is
measured: after a group handed a probe commits, the probe is re-run on the
commit, and when it passes while a case an existing test pins fails, the
group is rolled back and the probe recorded as contested, never driven
again. A group whose probe still fails is zero progress, like a group that
fixed no set case. A fixer's objection is kept as its words, never an
exemption.

Every test drives the real `_run_delivery_redrive` over a real git
repository and a real pytest: the probe re-run in the held-out sandbox, the
failing-test-ID diff confirmed under B*, the base-form check. Only
`claude_p` is stubbed: it plays the implementer, committing in staging.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import run_git_out as _git

BUGGY = "def add(a, b):\n    return a - b\n"
HONEST = "def add(a, b):\n    return a + b\n"
FITTED = "def add(a, b):\n    return a + b if a != b else 0\n"
TIDIED = "def add(a, b):\n    # the difference\n    return a - b\n"
PIN_REL = "tests/test_pin.py"
PIN_SRC = ("from calc import add\n\n\n"
           "def test_add_of_equal_numbers_is_zero():\n"
           "    assert add(2, 2) == 0\n")
PIN_LOOSENED = ("from calc import add\n\n\n"
                "def test_add_of_equal_numbers_is_zero():\n"
                "    assert add(2, 2) in (0, 4)\n")
KEPT_REL = "tests/test_kept.py"
KEPT_SRC = ("from calc import add\n\n\n"
            "def test_add_returns_an_int():\n"
            "    assert isinstance(add(1, 1), int)\n")
PIN_ID = "test_add_of_equal_numbers_is_zero"
ITEM = "add(2, 3) returns 5, the sum of its arguments"
TASK = "make add return the sum of its arguments"
PROBE_REL = "falsify_probe/test_add_probe.py"
PROBE_SRC = ("from calc import add\n\n\n"
             "def test_add_returns_the_sum():\n"
             "    assert add(2, 3) == 5\n")
SET_REL = "acc/test_d.py"

# The repository's browser suite (DESIGN §8 *A repository's browser suite is a
# channel the run provisions*): a stand-in for `playwright test` that runs only
# as the suite runs it — the CI's environment (`CI=true`) and its project — and
# writes a Playwright-shaped JSON report where the runner's environment says.
# A spec's case passes when the file its `// probe:` line names holds the text
# it names. It runs only in a tree its prerequisite (`SUITE_PREP`, as a code
# generator or the runner's browser install would) set up. Shell builtins
# only: neither script calls a program on PATH.
SUITE_RUNNER_REL = "bin/playwright"
SUITE_PREP_REL = "bin/suite-prep"
SUITE_READY = ".suite-ready"
SUITE_PREP = f"#!/bin/bash\nprintf 'ready\\n' > {SUITE_READY}\n"
SUITE_RUNNER = r"""#!/bin/bash
[[ $CI == true ]] || exit 64
[[ -f .suite-ready ]] || exit 65
proj= files=() skip=
for a in "$@"; do
  if [[ $skip ]]; then skip=; continue; fi
  case $a in
    --project=chromium) proj=1 ;;
    -g|--grep) skip=1 ;;
    test|-*) ;;
    *) files+=("$a") ;;
  esac
done
[[ $proj ]] || exit 64
specs= rc=0
for f in "${files[@]}"; do
  title= src= want= at=0 n=0
  while IFS= read -r line; do
    n=$((n + 1))
    [[ $line =~ ^test\(\"([^\"]*)\" ]] && title=${BASH_REMATCH[1]}
    [[ $line =~ ^//\ probe:\ ([^ ]+)\ (.*)$ ]] && \
      { src=${BASH_REMATCH[1]}; want=${BASH_REMATCH[2]}; }
    [[ $line == *expect\(* ]] && at=$n
  done < "$f"
  ok=
  while IFS= read -r line; do
    [[ $line == *"$want"* ]] && ok=1
  done < "$src"
  if [[ $ok ]]; then
    t='{"projectName":"chromium","status":"expected","results":[{"status":"passed"}]}'
  else
    rc=1
    t='{"projectName":"chromium","status":"unexpected","results":[{"status":"failed","error":{"message":"Error: expect(received).toBe(expected)\n    at '"$PWD/$f:$at"':3"}}]}'
  fi
  specs+="${specs:+,}"'{"title":"'"$f"'","file":"'"$f"'","specs":[{"title":"'"$title"'","file":"'"$f"'","tests":['"$t"']}]}'
done
[[ $PLAYWRIGHT_JSON_OUTPUT_FILE ]] && \
  printf '{"config":{"rootDir":"%s"},"errors":[],"suites":[%s]}' \
    "$PWD" "$specs" > "$PLAYWRIGHT_JSON_OUTPUT_FILE"
exit $rc
"""
SPEC_REL = "e2e/probe-falsify/add.spec.ts"
SPEC_SRC = ('import { expect, test } from "@playwright/test";\n'
            "// probe: calc.py return a + b\n"
            'test("add returns the sum on screen", async ({ page }) => {\n'
            "  expect(await page.locator('#sum').innerText()).toBe('5');\n"
            "});\n")
SPEC_CASE = f"{SPEC_REL} :: add returns the sum on screen"
SUITE = {"check_id": "e2e", "argv": [SUITE_RUNNER_REL, "test",
                                     "--project=chromium"],
         "runner": "playwright",
         "template": f"{SUITE_RUNNER_REL} test --project=chromium "
                     "{test_files}",
         "env": [], "prerequisites": [{"argv": [SUITE_PREP_REL], "cwd": "."}],
         "requires": ["browser"],
         "source": {}, "viable": True}


@pytest.fixture(autouse=True)
def _quiet_host(leerie, monkeypatch):
    # B*'s gate reads the host's PSI; pin it quiet so a loaded host cannot
    # stretch these tests by the gate's wait.
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()


class World:
    """A base whose `calc.add` subtracts and whose existing test pins that
    `add(2, 2) == 0`; a run whose staging carries one integrated subtask
    `s1` owning `calc.py`; one shown held-out set, passing; required item
    0 unmet on a confirmed falsifier probe from shown-only leads,
    `add(2, 3) == 5`. With `pin_added_by_run`, the pinning test is not on
    the base: the run's own subtask added it. With `via_suite`, the probe
    is a spec the falsifier confirmed through the repository's browser
    suite (`SUITE_RUNNER`), which the plain test command cannot run; with
    `channel` false, the run has no browser channel."""

    def __init__(self, leerie, tmp_path: Path, *,
                 pin_added_by_run: bool = False, via_suite: bool = False,
                 channel: bool = True):
        self.L = leerie
        repo = tmp_path / "repo"
        (repo / "tests").mkdir(parents=True)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "t@x")
        _git(repo, "config", "user.name", "t")
        (repo / "calc.py").write_text(BUGGY)
        (repo / "conftest.py").write_text(
            "import sys, pathlib\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n")
        (repo / KEPT_REL).write_text(KEPT_SRC)
        if not pin_added_by_run:
            (repo / PIN_REL).write_text(PIN_SRC)
        (repo / "bin").mkdir()
        for rel, text in ((SUITE_RUNNER_REL, SUITE_RUNNER),
                          (SUITE_PREP_REL, SUITE_PREP)):
            (repo / rel).write_text(text)
            (repo / rel).chmod(0o755)
        (repo / ".leerie").mkdir()
        (repo / ".leerie" / "config.toml").write_text(
            'test_scoped = "python3 -m pytest -q -p no:cacheprovider '
            '{test_files}"\n'
            'test = "python3 -m pytest -q -p no:cacheprovider tests"\n')
        (repo / ".gitignore").write_text(
            f"__pycache__/\n.leerie/\n{SUITE_READY}\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        base = _git(repo, "rev-parse", "HEAD")
        # The run's subtask: a commit on its branch, integrated.
        _git(repo, "checkout", "-q", "-b", "sub-s1", base)
        (repo / "calc.py").write_text(BUGGY + "# s1 work\n")
        if pin_added_by_run:
            (repo / PIN_REL).write_text(PIN_SRC)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "s1 work")
        _git(repo, "checkout", "-q", "main")
        _git(repo, "merge", "-q", "--no-ff", "-m", "leerie: integrate s1",
             "sub-s1")
        head = _git(repo, "rev-parse", "HEAD")
        root = tmp_path / "state"
        st = leerie.State(root, "run-c", repo_root=repo)
        self.st = st
        (st.run_dir / "logs").mkdir(parents=True, exist_ok=True)
        acc = tmp_path / "accset"
        (acc / "acc").mkdir(parents=True)
        (acc / SET_REL).write_text("def test_d():\n    pass\n")
        self.sets = [{"index": 1, "valid": True, "dir": str(acc),
                      "defect_files": [SET_REL], "control_files": [],
                      "support_files": [], "cases": {SET_REL: ["test_d"]}}]
        probe_dir = st.run_dir / "acceptance" / "probes" / "i0"
        self.probe_rel = SPEC_REL if via_suite else PROBE_REL
        (probe_dir / self.probe_rel).parent.mkdir(parents=True)
        (probe_dir / self.probe_rel).write_text(
            SPEC_SRC if via_suite else PROBE_SRC)
        evidence = (f"a falsifier probe of the item's words \"{ITEM}\" "
                    "fails under B* on the final tree")
        row = {"item_index": 0, "verdict": "unmet", "by": ["falsifier"],
               "falsifier": {"item_index": 0, "confirmed": True, "samples": [
                   {"sample": 0, "confirmed": True, "lead_sets": [1],
                    "item_span_quote": ITEM,
                    "case_name": "test_add_returns_the_sum",
                    "assertion_quote": "    assert add(2, 3) == 5",
                    "reason": "add subtracts",
                    "probe_saved": {"dir": str(probe_dir),
                                    "file": self.probe_rel},
                    **({"via_browser_suite": True} if via_suite else {})}]}}
        plan = {"subtasks": {"s1": {
            "id": "s1", "title": "Fix add", "intent": "make add sum",
            "change_kind": "fix", "files_likely_touched": ["calc.py"],
            "claims": {"sites": [], "items": [
                {"item_index": 0, "kind": "deliverable"}]}}}}
        (st.run_dir / "plan.json").write_text(json.dumps(plan))
        st.data = {
            "task": TASK, "verbosity": "quiet", "working_branch": "main",
            "worker_count": 0, "base_sha": base,
            "repo_state_before_planning": {"head": base},
            "subtask_status": {"s1": "complete"},
            "required_items": [{"item": ITEM, "span": "S1",
                                "verifiability": "tree"}],
            "delivery_gate": {
                "unmet_final": [{"item_index": 0, "item": ITEM,
                                 "evidence": evidence, "by": ["falsifier"],
                                 "evidence_by": {"falsifier": evidence}}],
                "items": [row], "final_sha": head},
            "acceptance": {"validity_base": base, "sets": self.sets,
                           "gate": {}}}
        if via_suite and channel:
            st.data["ci_parity"] = {"browser_suite": dict(SUITE)}
        st.save()
        # The run's own repository (DESIGN §6 *The run's own repository*),
        # with staging on the run branch, as setup-run.sh leaves it.
        leerie._ensure_run_repo(st)
        run_repo = st.run_dir / "repo"
        _git(run_repo, "fetch", "-q", str(repo), f"+{head}:refs/leerie/t")
        self.staging = st.run_dir / "worktrees" / "staging"
        self.staging.parent.mkdir(parents=True, exist_ok=True)
        _git(run_repo, "worktree", "add", "-q", "-B",
             leerie._compute_run_branch(st.run_id), str(self.staging), head)
        _git(self.staging, "config", "user.email", "t@x")
        _git(self.staging, "config", "user.name", "t")
        final = asyncio.run(leerie._evaluate_acceptance_sets(
            st, self._caps(), str(self.staging), self.sets, "start"))
        st.data["delivery_gate"]["final_tree"] = _git(
            self.staging, "rev-parse", "HEAD^{tree}")
        st.data["acceptance"]["gate"] = {"final_sha": head, "final": final}
        st.save()
        self.head = head
        self.calls: list[dict] = []
        self.edits: list = []
        self.answers: list[dict] = []

    def _caps(self) -> dict:
        caps = dict(self.L.DEFAULT_CAPS)
        caps.update(worker_timeout_sec=120, confirm_pairs=2,
                    confirm_intermittent_repeats=0, max_parallel=1,
                    subtask_redrive_rounds=2)
        return caps

    def commit(self, files: dict[str, str], msg: str = "fix") -> None:
        for rel, text in files.items():
            (self.staging / rel).write_text(text)
        _git(self.staging, "add", "-A")
        _git(self.staging, "commit", "-qm", msg)

    def run(self, monkeypatch) -> dict:
        world = self

        async def fake_claude_p(**kw):
            assert kw["schema_key"] == "implementer", kw["schema_key"]
            world.calls.append({"prompt": kw["user_prompt"],
                                "cwd": kw["cwd"]})
            world.kw = kw
            edit = world.edits.pop(0) if world.edits else None
            if edit:
                edit(world)
            ans = world.answers.pop(0) if world.answers else {}
            return {"status": "complete", "summary": "done", **ans}
        monkeypatch.setattr(self.L, "claude_p", fake_claude_p)
        asyncio.run(self.L._run_delivery_redrive(
            self.st.run_dir, self.st, self._caps(),
            {"implementer": "sonnet"}, {"implementer": "low"}))
        return self.st.data["delivery_redrive"]


def _groups(rec: dict) -> list[dict]:
    """The groups an implementer drove (a member out of rounds is recorded
    `skipped`)."""
    return [g for r in rec["rounds"] for g in r["groups"]
            if not g.get("skipped")]


def test_a_probe_that_conflicts_with_an_existing_test_is_contested(
        leerie, tmp_path, monkeypatch):
    """N3's shape: the fixer obeys the probe and breaks a case a test the
    base had pins, in a file the run never changed."""
    w = World(leerie, tmp_path)
    w.edits = [lambda w: w.commit({"calc.py": HONEST})]
    rec = w.run(monkeypatch)
    [g] = _groups(rec)
    assert g["probes_after"] == {"0": "pass"}
    assert g["rolled_back"] is True
    assert _git(w.staging, "rev-parse", "HEAD") == w.head
    [c] = rec["contested_probes"]
    assert c["item_index"] == 0 and c["probe_test_file"] == PROBE_REL
    assert (c["round"], c["group"]) == (1, 0)
    assert [t["file"] for t in c["tests"]] == [PIN_REL]
    assert PIN_ID in c["tests"][0]["id"]
    assert g["probe_conflict"]["items"] == [0]
    assert ("making the falsifier probe(s) of item(s) [0] pass broke "
            "case(s) existing tests pin: ") in g["rollback_reason"]
    # Never retried: two rounds were open, one implementer ran.
    assert len(w.calls) == 1
    assert "unrouted_items" not in rec
    # Never dropped: the item stays unmet, the run is unconverged, and the
    # conflict is named for a person.
    assert [u["item_index"] for u in
            w.st.data["delivery_gate"]["unmet_final"]] == [0]
    assert leerie._decide_outcome(w.st, any_commits=True) == "unconverged"
    [line] = [r for r in w.st.data["outcome_reasons"]
              if r.startswith("item 0: its falsifier probe")]
    assert line.startswith(
        f"item 0: its falsifier probe {PROBE_REL} conflicts with existing "
        "tests — the re-drive change that made it pass (round 1, group 0) "
        "broke 1 case(s) they pin: ")
    assert PIN_ID in line and line.endswith(
        "the change was rolled back and the probe is not driven again — a "
        "person decides; the item stays unmet")


def test_loosening_the_pinning_test_is_a_conflict_too(
        leerie, tmp_path, monkeypatch):
    """G4-shown-2's shape, made worse: the fixer also edits the existing
    test so its final form passes. Its base form, run against the commit,
    still fails, so the conflict stands."""
    w = World(leerie, tmp_path)
    w.edits = [lambda w: w.commit({"calc.py": HONEST,
                                   PIN_REL: PIN_LOOSENED})]
    rec = w.run(monkeypatch)
    [g] = _groups(rec)
    assert g["probes_after"] == {"0": "pass"}
    assert any(PIN_ID in x for x in g["base_form_changed"])
    assert g["rolled_back"] is True
    [c] = rec["contested_probes"]
    assert [t["file"] for t in c["tests"]] == [PIN_REL]
    assert len(w.calls) == 1


def test_a_probe_that_still_fails_is_zero_progress_and_stays_shown(
        leerie, tmp_path, monkeypatch):
    """The commit breaks nothing and fixes nothing: before, a group handed
    only a probe could never be zero progress, and this was kept."""
    w = World(leerie, tmp_path)
    w.edits = [lambda w: w.commit({"calc.py": TIDIED}),
               lambda w: w.commit({"calc.py": TIDIED + "# again\n"})]
    rec = w.run(monkeypatch)
    g1, g2 = _groups(rec)
    for g in (g1, g2):
        assert g["probes_after"] == {"0": "fail"}
        assert g["zero_progress"] is True and g["rolled_back"] is True
        assert g["rollback_reason"] == (
            "it fixed no failing case it could be measured on (a shown "
            "falsifier probe counts as one)")
        assert not g.get("new_failures")
    assert rec["contested_probes"] == []
    # Still shown: the second round was handed the same probe.
    assert len(w.calls) == 2
    assert all(PROBE_REL in c["prompt"] for c in w.calls)
    assert _git(w.staging, "rev-parse", "HEAD") == w.head


def test_a_probe_still_failing_is_no_conflict_whatever_broke(
        leerie, tmp_path, monkeypatch):
    """The commit breaks the existing test and leaves the probe failing:
    nothing measured says satisfying the probe breaks the test. Rolled back
    as worse, and the probe goes to the next round."""
    w = World(leerie, tmp_path)
    product = "def add(a, b):\n    return a * b\n"
    w.edits = [lambda w: w.commit({"calc.py": product}),
               lambda w: w.commit({"calc.py": product + "# again\n"})]
    rec = w.run(monkeypatch)
    g1 = _groups(rec)[0]
    assert g1["probes_after"] == {"0": "fail"}
    assert any(PIN_ID in x for x in g1["new_failures"])
    assert g1["rolled_back"] is True and "probe_conflict" not in g1
    assert rec["contested_probes"] == []
    assert len(w.calls) == 2


def test_breaking_a_test_the_run_added_is_no_contest(
        leerie, tmp_path, monkeypatch):
    """A test the run itself added pins what the run wrote, not the
    repository's behaviour: the group is rolled back as worse, and the
    probe goes to the next round."""
    w = World(leerie, tmp_path, pin_added_by_run=True)
    w.edits = [lambda w: w.commit({"calc.py": HONEST}),
               lambda w: w.commit({"calc.py": HONEST + "# again\n"})]
    rec = w.run(monkeypatch)
    g1 = _groups(rec)[0]
    assert g1["probes_after"] == {"0": "pass"}
    assert any(PIN_ID in x for x in g1["new_failures"])
    assert g1["rolled_back"] is True and "probe_conflict" not in g1
    assert rec["contested_probes"] == []
    assert len(w.calls) == 2


def test_a_probe_made_to_pass_without_a_conflict_is_kept(
        leerie, tmp_path, monkeypatch):
    w = World(leerie, tmp_path)
    w.edits = [lambda w: w.commit({"calc.py": FITTED})]
    rec = w.run(monkeypatch)
    [g] = _groups(rec)
    assert g["probes_after"] == {"0": "pass"}
    assert not g.get("rolled_back") and not g["zero_progress"]
    assert rec["contested_probes"] == []
    assert _git(w.staging, "show", "HEAD:calc.py") == FITTED.rstrip("\n")
    # The re-check re-ran the probe on the kept commit: the item is met.
    assert w.st.data["delivery_gate"]["unmet_final"] == []


def test_a_fixer_objection_is_its_words_and_exempts_nothing(
        leerie, tmp_path, monkeypatch):
    """A `contradicts_task` objection quoting the task verbatim, the
    contest the first form of this rule accepted: kept as the fixer's
    words, and the probe is driven again."""
    w = World(leerie, tmp_path)
    objection = {"probe_test_file": "/x/selfcheck/sets/probe-i0/"
                 + PROBE_REL, "kind": "contradicts_task",
                 "reason": f"the task says \"{TASK}\" and this is wrong"}
    stray = {"probe_test_file": "src/other.test.ts",
             "kind": "input_outside_domain", "reason": "not a probe"}
    w.answers = [{"probe_objections": [objection, stray]},
                 {"probe_objections": [objection]}]
    rec = w.run(monkeypatch)
    assert len(w.calls) == 2, "an objection exempted the probe"
    assert rec["contested_probes"] == []
    assert [(o["item_index"], o["probe_test_file"], o["kind"], o["round"])
            for o in rec["probe_objections"]] == [
        (0, PROBE_REL, "contradicts_task", 1),
        (0, PROBE_REL, "contradicts_task", 2)]
    assert leerie._decide_outcome(w.st, any_commits=True) == "unconverged"
    lines = [r for r in w.st.data["outcome_reasons"]
             if "objected to its falsifier probe" in r]
    assert len(lines) == 2 and lines[0].endswith(
        "its own words, not verified by leerie; it exempts nothing")


def test_the_pr_shows_the_conflict_and_the_objection(
        leerie, tmp_path, monkeypatch):
    """The consumer: the run ends, `_record_unmet_evidence` records what is
    still unmet, and the PR's end-state section is rendered from it."""
    w = World(leerie, tmp_path)
    objection = {"probe_test_file": PROBE_REL,
                 "kind": "breaks_existing_test",
                 "reason": "add(2, 2) is pinned to 0"}
    w.edits = [lambda w: w.commit({"calc.py": HONEST})]
    w.answers = [{"probe_objections": [objection]}]
    w.run(monkeypatch)
    leerie._decide_outcome(w.st, any_commits=True)
    leerie._record_unmet_evidence(w.st)
    end = leerie._format_pr_end_state_section(w.st.data)
    section = end.split("## Still failing when the run ended", 1)[1]
    section = section.split("\n## ", 1)[0]
    head = ("- The probe conflicts with existing tests: a re-drive change "
            "that made it pass broke 1 case(s) they pin, so the change was "
            "rolled back and the probe was not driven again. Decide which "
            "is right: `")
    assert head in section
    tail = section.split(head, 1)[1].split("\n", 1)[0]
    assert PIN_REL in tail and PIN_ID in tail
    assert ("- A fixer's objection, in its own words (not verified by "
            "leerie; it exempts nothing): breaks_existing_test: add(2, 2) "
            "is pinned to 0") in section
    # In the item's own entry, after its probe line.
    assert section.index(f"### Item 0: `{PROBE_REL}`") < section.index(head)


def _selfcheck(w, *args: str) -> subprocess.CompletedProcess:
    """The fixer's self-check, as its shell runs it: the command the note
    names, with the PATH and environment the drive gave the worker."""
    kw = w.kw
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    env.update(kw.get("env_extra") or {})
    cmd = str(Path(kw["selfcheck_dir"]) / "bin" / "leerie-selfcheck")
    assert f"  {cmd}\n" in kw["user_prompt"]
    return subprocess.run([cmd, *args], cwd=kw["cwd"], capture_output=True,
                          text=True, env=env, timeout=300)


SUITE_LINE = f"SELF-CHECK: {SUITE_RUNNER_REL} test --project=chromium {SPEC_REL}"


def test_a_suite_probe_is_shown_and_every_rerun_goes_through_the_suite(
        leerie, tmp_path, monkeypatch):
    """A probe the falsifier confirmed through the repository's browser
    suite (DESIGN §8 *A probe is re-run through the channel that confirmed
    it*): handed to the fixer, run through the suite by its self-check
    (failing on the shipped tree, passing on its uncommitted fix), re-run
    through the suite on its commit (the progress read), and by the
    re-check, which makes the item met. The test command cannot run the
    spec at all: through it, none of these could be read."""
    w = World(leerie, tmp_path, via_suite=True)
    out: list = []

    def fixer(w):
        out.append(_selfcheck(w))
        (w.staging / "calc.py").write_text(FITTED)
        out.append(_selfcheck(w))
        out.append(_selfcheck(w, "add.spec.ts"))
        w.commit({"calc.py": FITTED})
    w.edits = [fixer]
    rec = w.run(monkeypatch)
    assert len(w.calls) == 1
    # Shown: the note names the spec as a probe in the self-check.
    assert SPEC_REL in w.calls[0]["prompt"]
    before, after, alone = out
    # Both channels, each with its own command: the shown set through the
    # test command, the probe through the suite.
    assert "python3 -m pytest" in before.stdout and SUITE_LINE in before.stdout
    assert before.returncode == 1, before.stdout
    assert "SELF-CHECK FAILED" in before.stdout
    assert after.returncode == 0 and "SELF-CHECK PASSED" in after.stdout, (
        after.stdout)
    # Naming the probe runs it alone, through the suite.
    assert alone.returncode == 0 and SUITE_LINE in alone.stdout
    assert "pytest" not in alone.stdout
    [g] = _groups(rec)
    assert g["probes_after"] == {"0": "pass"}
    assert not g.get("rolled_back") and not g["zero_progress"]
    assert _git(w.staging, "show", "HEAD:calc.py") == FITTED.rstrip("\n")
    # The re-check re-ran the probe through the suite: the item is met.
    assert w.st.data["delivery_gate"]["unmet_final"] == []


def test_a_suite_probe_still_failing_is_measured_failing(
        leerie, tmp_path, monkeypatch):
    """A commit that leaves the spec failing is read so through the suite:
    `fail`, a measured verdict, so the group is zero progress — never
    `unmeasured`, which is what the test command gives a spec."""
    w = World(leerie, tmp_path, via_suite=True)
    w.edits = [lambda w: w.commit({"calc.py": TIDIED}),
               lambda w: w.commit({"calc.py": TIDIED + "# again\n"})]
    rec = w.run(monkeypatch)
    for g in _groups(rec):
        assert g["probes_after"] == {"0": "fail"}
        assert g["zero_progress"] is True and g["rolled_back"] is True
    assert len(w.calls) == 2
    # The `fail` is the suite's: every run of the probe on a commit went
    # through its command (the test command fails a spec it cannot load on
    # every tree, which reads the same).
    logs = leerie._held_out_logs(w.st.run_dir)
    for rnd in (1, 2):
        text = (logs / f"acceptance-redrive-r{rnd}g0-probes.log").read_text()
        assert f"{SUITE_RUNNER_REL} test --project=chromium {SPEC_REL}" in text
        assert "-m pytest" not in text
    assert [u["item_index"] for u in
            w.st.data["delivery_gate"]["unmet_final"]] == [0]


def test_a_suite_probe_that_conflicts_with_an_existing_test_is_contested(
        leerie, tmp_path, monkeypatch):
    """The conflict is measured through the suite too: the spec passes on
    the commit while the existing test it breaks fails, so the group is
    rolled back and the probe recorded contested; the PR names the suite's
    own command for it."""
    w = World(leerie, tmp_path, via_suite=True)
    w.edits = [lambda w: w.commit({"calc.py": HONEST})]
    rec = w.run(monkeypatch)
    [g] = _groups(rec)
    assert g["probes_after"] == {"0": "pass"}
    assert g["rolled_back"] is True
    [c] = rec["contested_probes"]
    assert c["probe_test_file"] == SPEC_REL
    assert [t["file"] for t in c["tests"]] == [PIN_REL]
    assert len(w.calls) == 1
    leerie._decide_outcome(w.st, any_commits=True)
    leerie._record_unmet_evidence(w.st)
    [e] = [x for x in w.st.data["unmet_evidence"]["entries"]
           if x.get("kind") == "falsifier"]
    assert e["cmd"] == f"{SUITE_RUNNER_REL} test --project=chromium {SPEC_REL}"


def test_a_suite_command_the_task_forbids_never_launches(
        leerie, tmp_path, monkeypatch):
    """DESIGN §9 *A command the task forbids*: the suite's own command goes
    through the one gate wherever a probe is re-run. With the task
    forbidding it, the fixer's self-check runs the test-command channel and
    never launches the suite's, saying so, with no pass for the probe and a
    typed record of why; the commit's re-run and the re-check launch it
    neither, and give no verdict, so the item stays unmet."""
    w = World(leerie, tmp_path, via_suite=True)
    w.st.data["process_constraints"] = {
        "forbidden_commands": [SUITE_RUNNER_REL]}
    w.st.save()
    out: list = []

    def fixer(w):
        (w.staging / "calc.py").write_text(FITTED)
        out.append(_selfcheck(w))
        w.commit({"calc.py": FITTED})
    w.edits = [fixer]
    rec = w.run(monkeypatch)
    [r] = out
    assert r.returncode == 2, r.stdout
    assert "SELF-CHECK PASSED" not in r.stdout
    assert SUITE_LINE not in r.stdout
    assert "python3 -m pytest" in r.stdout          # the other channel ran
    assert (f"SELF-CHECK UNMEASURED: {SPEC_REL} not run — the task forbids "
            f"`{SUITE_RUNNER_REL}`") in r.stdout
    g = _groups(rec)[0]
    [call] = g["selfcheck_calls"]
    assert call["rc"] == 2
    assert call["unmeasured"] == {SPEC_REL: "forbidden_by_task"}
    assert call["forbidden"] == f"the task forbids `{SUITE_RUNNER_REL}`"
    # Leerie's own re-runs: gated too, no verdict, never launched.
    assert g["probes_after"] == {"0": "unmeasured"}
    assert [u["item_index"] for u in
            w.st.data["delivery_gate"]["unmet_final"]] == [0]
    for log in leerie._held_out_logs(w.st.run_dir).glob("*.log"):
        assert f"{SUITE_RUNNER_REL} test" not in log.read_text(), log


def test_a_suite_probe_in_a_run_without_the_channel_is_not_shown(
        leerie, tmp_path, monkeypatch):
    """With no browser channel in the run, nothing can run the probe as it
    was confirmed: it is not shown, never driven or contested, and a
    re-run of it gives no verdict rather than the test command's."""
    w = World(leerie, tmp_path, via_suite=True, channel=False)
    w.edits = [lambda w: w.commit({"calc.py": HONEST})]
    rec = w.run(monkeypatch)
    assert w.calls == []
    assert rec["contested_probes"] == []
    assert [(u["item_index"], u["reason"]) for u in
            rec["unrouted_items"]] == [(0, "unreproducible")]
    assert _git(w.staging, "rev-parse", "HEAD") == w.head
    probe = leerie._falsifier_probe_set(
        0, str(w.st.run_dir / "acceptance" / "probes" / "i0"), SPEC_REL,
        via_browser_suite=True)
    [r] = asyncio.run(leerie._evaluate_acceptance_sets(
        w.st, w._caps(), str(w.staging), [probe], "nochannel"))
    assert r["unmeasured"] is True
    assert r["unmeasured_reason"] == "no_browser_suite"


def test_the_implementer_schema_types_the_objection(leerie):
    p_ = leerie.SCHEMAS["implementer"]["properties"]
    assert "contested_probes" not in p_
    item = p_["probe_objections"]["items"]
    assert item["required"] == ["probe_test_file", "kind"]
    assert set(item["properties"]) == {"probe_test_file", "kind", "reason"}
    assert item["properties"]["kind"]["enum"] == [
        "input_outside_domain", "contradicts_task", "breaks_existing_test"]
