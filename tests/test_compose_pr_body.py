"""Tests for `compose_pr_body()` — deterministic PR-body generation from
state.json + run_id. Used by finalize.sh (commit 4).

Critical properties:
- Deterministic: same state → same body, every time.
- Renders all required sections (Task, Classification, Run summary).
- Missing optional fields render as 'n/a' rather than the literal 'None'
  (Python's str(None) → 'None' is unhelpful in a rendered PR body).
- No KeyError or AttributeError on partially-populated state.
"""
from __future__ import annotations


def _full_state() -> dict:
    return {
        "task": "Add telemetry and self-heal skills",
        "started_at": "2026-05-26T14:31:22.847291+00:00",
        "finished_at": "2026-05-26T15:47:09.123456+00:00",
        "categories": ["feature-implementation", "testing"],
        "answers": {"source_of_truth": "both"},
        "waves": [["feat-001", "feat-002"], ["test-001"]],
        "worker_count": 17,
        "working_branch": "main",
    }


def test_compose_pr_body_deterministic(leerie):
    """Same inputs → byte-identical output. Foundational property."""
    state = _full_state()
    rid = "feat-add-telemetry-and-self-heal-skills-a3f7c2"
    a = leerie.compose_pr_body(state, rid)
    b = leerie.compose_pr_body(state, rid)
    assert a == b


def test_compose_pr_body_contains_all_sections(leerie):
    """The three top-level headings must all render so reviewers know
    what to expect."""
    body = leerie.compose_pr_body(_full_state(), "feat-foo-abc123")
    assert "## Task" in body
    assert "## Classification" in body
    assert "## Run summary" in body


def test_compose_pr_body_renders_task_verbatim(leerie):
    """The task description appears as-is — important for review context."""
    state = _full_state()
    body = leerie.compose_pr_body(state, "feat-foo-abc123")
    assert state["task"] in body


def test_compose_pr_body_uses_first_category(leerie):
    """When multiple categories were assigned, the body shows the primary
    one (consistent with how the run-id prefix is derived)."""
    body = leerie.compose_pr_body(_full_state(), "feat-foo-abc123")
    assert "feature-implementation" in body


def test_compose_pr_body_renders_run_id(leerie):
    """The run_id appears in the body for traceability — a reviewer can
    grep their `.leerie/runs/` for the directory."""
    rid = "feat-add-telemetry-and-self-heal-skills-a3f7c2"
    body = leerie.compose_pr_body(_full_state(), rid)
    assert rid in body


def test_compose_pr_body_includes_wave_and_subtask_counts(leerie):
    """`Waves: N, subtasks: M` — derived from `waves` list shape."""
    body = leerie.compose_pr_body(_full_state(), "feat-foo-abc123")
    # _full_state has 2 waves, 3 subtasks total.
    assert "Waves: 2" in body
    assert "subtasks: 3" in body


def test_compose_pr_body_includes_worker_count(leerie):
    body = leerie.compose_pr_body(_full_state(), "feat-foo-abc123")
    assert "17" in body  # the worker_count


def test_compose_pr_body_includes_working_branch(leerie):
    body = leerie.compose_pr_body(_full_state(), "feat-foo-abc123")
    assert "main" in body  # the working branch


def test_compose_pr_body_includes_state_json_pointer(leerie):
    """The body should point reviewers at the on-disk state.json for full
    detail beyond what the PR summary shows."""
    rid = "feat-foo-abc123"
    body = leerie.compose_pr_body(_full_state(), rid)
    assert "leerie --list" in body
    assert rid in body
    assert "state.json" in body


# --- missing / partial state handling --------------------------------------

def test_compose_pr_body_missing_finished_at_renders_na(leerie):
    """An unfinished run (no `finished_at`) should not render 'None' in
    the PR body — 'n/a' is the convention."""
    state = _full_state()
    del state["finished_at"]
    body = leerie.compose_pr_body(state, "feat-foo-abc123")
    assert "None" not in body
    assert "n/a" in body


def test_compose_pr_body_missing_categories_renders_na(leerie):
    """No categories at all → primary category renders as 'n/a'."""
    state = _full_state()
    del state["categories"]
    body = leerie.compose_pr_body(state, "feat-foo-abc123")
    assert "Category: n/a" in body


def test_compose_pr_body_empty_categories_renders_na(leerie):
    """Empty list → 'n/a' (not 'None' or a crash)."""
    state = _full_state()
    state["categories"] = []
    body = leerie.compose_pr_body(state, "feat-foo-abc123")
    assert "Category: n/a" in body


def test_compose_pr_body_missing_answers_renders_na(leerie):
    """No clarification was done → source-of-truth renders as 'n/a'."""
    state = _full_state()
    del state["answers"]
    body = leerie.compose_pr_body(state, "feat-foo-abc123")
    assert "Source of truth: n/a" in body


def test_compose_pr_body_empty_state(leerie):
    """Defensive: an empty state still renders without raising. The body
    will be mostly 'n/a' but every section header is still present."""
    body = leerie.compose_pr_body({}, "feat-foo-abc123")
    assert "## Task" in body
    assert "## Classification" in body
    assert "## Run summary" in body
    assert "None" not in body  # no literal 'None' leaked through


def test_compose_pr_body_footer_link_with_version(leerie):
    """`leerie_version` present → footer links to the repo with a
    ` v<version>` suffix (leerie.py:2062-2063)."""
    state = _full_state()
    state["leerie_version"] = "1.4.0"
    body = leerie.compose_pr_body(state, "feat-foo-abc123")
    assert "[leerie v1.4.0](https://github.com/enricai/leerie)" in body


def test_compose_pr_body_footer_link_without_version(leerie):
    """No `leerie_version` in state → footer links to the repo with no
    version suffix at all (not `[leerie ]`, not `[leerie vNone]`)."""
    state = _full_state()
    body = leerie.compose_pr_body(state, "feat-foo-abc123")
    assert "[leerie](https://github.com/enricai/leerie)" in body
    assert "[leerie v" not in body


def test_compose_pr_body_no_literal_none(leerie):
    """Sweep guard: under no realistic state shape should the literal
    string 'None' appear in the body."""
    # Various partial states
    states = [
        {},
        {"task": "x"},
        {"task": "x", "started_at": None, "finished_at": None},
        {"task": "x", "waves": []},
        {"task": "x", "answers": {}},
        {"task": "x", "categories": [None]},
    ]
    for state in states:
        body = leerie.compose_pr_body(state, "feat-foo-abc123")
        assert "None" not in body, f"literal 'None' leaked for state={state}"


# --- deploy-ordering notes (DESIGN §20 run groups) -------------------------

def test_compose_pr_body_no_deploy_section_when_absent(leerie):
    """No external_preconditions → no deploy-ordering section rendered."""
    body = leerie.compose_pr_body(_full_state(), "feat-foo-abc123")
    assert "Deploy-ordering" not in body
    assert "⚠" not in body


def test_compose_pr_body_deploy_section_when_preconditions_present(leerie):
    """external_preconditions present → deploy-ordering section rendered."""
    state = _full_state()
    state["external_preconditions"] = [
        {
            "tag": "storage-volumes-api",
            "reasons": [
                {"sid": "feat-001", "reason": "adds /volumes endpoint consumed here"}
            ],
            "originating_subtasks": ["feat-001"],
        }
    ]
    body = leerie.compose_pr_body(state, "feat-foo-abc123")
    assert "⚠ Deploy-ordering" in body
    assert "storage-volumes-api" in body
    assert "adds /volumes endpoint consumed here" in body


def test_compose_pr_body_deploy_section_empty_list_no_section(leerie):
    """Empty external_preconditions list → no deploy-ordering section."""
    state = _full_state()
    state["external_preconditions"] = []
    body = leerie.compose_pr_body(state, "feat-foo-abc123")
    assert "Deploy-ordering" not in body


def test_compose_pr_body_deploy_section_multiple_entries(leerie):
    """Multiple preconditions each render as a separate bullet."""
    state = _full_state()
    state["external_preconditions"] = [
        {
            "tag": "auth-service",
            "reasons": [{"sid": "feat-001", "reason": "auth token API required"}],
            "originating_subtasks": ["feat-001"],
        },
        {
            "tag": "billing-api",
            "reasons": [{"sid": "feat-002", "reason": "billing plan check needed"}],
            "originating_subtasks": ["feat-002"],
        },
    ]
    body = leerie.compose_pr_body(state, "feat-foo-abc123")
    assert "auth-service" in body
    assert "auth token API required" in body
    assert "billing-api" in body
    assert "billing plan check needed" in body


def test_compose_pr_body_deploy_section_no_reasons(leerie):
    """A precondition with no reason text still renders the tag."""
    state = _full_state()
    state["external_preconditions"] = [
        {
            "tag": "some-external-dep",
            "reasons": [],
            "originating_subtasks": [],
        }
    ]
    body = leerie.compose_pr_body(state, "feat-foo-abc123")
    assert "some-external-dep" in body
    assert "None" not in body
