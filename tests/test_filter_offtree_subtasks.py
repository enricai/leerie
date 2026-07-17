"""Tests for filter_offtree_subtasks() — the soft-drop check that removes
subtasks whose `files_likely_touched` resolves outside the run's primary
worktree (most commonly into an inspect-dir mount).

The check is a soft drop, never a hard fail: a `die()` here would leave
the run unrecoverable because the resume contract requires
`state.json["waves"]` to exist, and `waves` is only written by
`write_plan` which runs after this check.

Tests use real `tmp_path` directories so symlink resolution and
`.resolve().is_relative_to(...)` behave like the production code.
"""
from __future__ import annotations


def _capture_logs(leerie, monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lambda msg: lines.append(msg))
    return lines


def _make_state(leerie, tmp_path):
    """Build a minimal State instance with a writable state.json."""
    leerie_root = tmp_path / ".leerie"
    st = leerie.State(leerie_root=leerie_root, run_id="run-id")
    st.run_dir.mkdir(parents=True, exist_ok=True)
    st.data = {}
    return st


def test_happy_path_no_drop(leerie, tmp_path, monkeypatch):
    """All files resolve under repo_root → no drop, st.data unchanged."""
    lines = _capture_logs(leerie, monkeypatch)
    st = _make_state(leerie, tmp_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "src").mkdir()

    plans = [{"domain": "feat", "subtasks": [
        {"id": "feat-001", "files_likely_touched": ["src/a.py"]},
        {"id": "feat-002", "files_likely_touched": ["src/b.py"]},
    ]}]

    leerie.filter_offtree_subtasks(plans, repo_root, [], st)

    assert [s["id"] for s in plans[0]["subtasks"]] == ["feat-001", "feat-002"]
    assert "dropped_subtasks" not in st.data
    assert lines == []


def test_inspect_dir_leak_drops_subtask(leerie, tmp_path, monkeypatch):
    """A file under an inspect-dir is read-only; drop with the specific
    'resolves under inspect-dir' reason."""
    lines = _capture_logs(leerie, monkeypatch)
    st = _make_state(leerie, tmp_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    inspect = tmp_path / "inspect" / "api"
    inspect.mkdir(parents=True)

    plans = [{"domain": "bugfix", "subtasks": [
        {"id": "bugfix-005",
         "files_likely_touched": [str(inspect / "src/controllers/contacts.ts")]},
        {"id": "bugfix-006",
         "files_likely_touched": ["src/lib/messages.ts"]},
    ]}]

    leerie.filter_offtree_subtasks(plans, repo_root, [str(inspect)], st)

    assert [s["id"] for s in plans[0]["subtasks"]] == ["bugfix-006"]
    assert "bugfix-005" in st.data["dropped_subtasks"]
    reasons = st.data["dropped_subtasks"]["bugfix-005"]["reasons"]
    assert any("resolves under inspect-dir" in r for r in reasons)
    assert any("filter_offtree_subtasks: dropped 1 subtask(s)" in l for l in lines)


def test_generic_offtree_path_drops_with_generic_reason(leerie, tmp_path, monkeypatch):
    """A path under neither repo_root nor any inspect-dir gets the generic
    'does not resolve under repo root' reason."""
    lines = _capture_logs(leerie, monkeypatch)
    st = _make_state(leerie, tmp_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    plans = [{"domain": "feat", "subtasks": [
        {"id": "feat-001", "files_likely_touched": ["/tmp/foo.py"]},
    ]}]

    leerie.filter_offtree_subtasks(plans, repo_root, [], st)

    assert plans[0]["subtasks"] == []
    reasons = st.data["dropped_subtasks"]["feat-001"]["reasons"]
    assert any("does not resolve under repo root" in r for r in reasons)


def test_empty_files_likely_touched_is_ok(leerie, tmp_path, monkeypatch):
    """A subtask with no files_likely_touched survives untouched (the field
    is allowed to be omitted)."""
    lines = _capture_logs(leerie, monkeypatch)
    st = _make_state(leerie, tmp_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    plans = [{"domain": "config", "subtasks": [
        {"id": "config-001"},
        {"id": "config-002", "files_likely_touched": []},
    ]}]

    leerie.filter_offtree_subtasks(plans, repo_root, [], st)

    assert [s["id"] for s in plans[0]["subtasks"]] == ["config-001", "config-002"]
    assert "dropped_subtasks" not in st.data
    assert lines == []


def test_mixed_plans_filter_independently(leerie, tmp_path, monkeypatch):
    """Each plan's subtasks list is filtered independently; survivors stay
    in their original plan."""
    _capture_logs(leerie, monkeypatch)
    st = _make_state(leerie, tmp_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    inspect = tmp_path / "inspect" / "api"
    inspect.mkdir(parents=True)

    plans = [
        {"domain": "feat", "subtasks": [
            {"id": "feat-001", "files_likely_touched": ["src/a.py"]},
            {"id": "feat-002",
             "files_likely_touched": [str(inspect / "src/x.py")]},
        ]},
        {"domain": "bugfix", "subtasks": [
            {"id": "bugfix-001",
             "files_likely_touched": [str(inspect / "src/y.py")]},
            {"id": "bugfix-002", "files_likely_touched": ["src/b.py"]},
        ]},
    ]

    leerie.filter_offtree_subtasks(plans, repo_root, [str(inspect)], st)

    assert [s["id"] for s in plans[0]["subtasks"]] == ["feat-001"]
    assert [s["id"] for s in plans[1]["subtasks"]] == ["bugfix-002"]
    assert set(st.data["dropped_subtasks"].keys()) == {"feat-002", "bugfix-001"}


def test_schedule_after_filter_has_no_dropped_sid(leerie, tmp_path, monkeypatch):
    """Integration with schedule(): after filter drops a sid, the schedule
    waves do not reference it. This is the load-bearing spot-check from
    the plan."""
    _capture_logs(leerie, monkeypatch)
    st = _make_state(leerie, tmp_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    inspect = tmp_path / "inspect"
    inspect.mkdir()

    plans = [{"domain": "feat", "subtasks": [
        {"id": "feat-001", "files_likely_touched": ["src/a.py"],
         "depends_on": [], "requires": [], "provides": []},
        {"id": "feat-002",
         "files_likely_touched": [str(inspect / "src/x.py")],
         "depends_on": [], "requires": [], "provides": []},
    ]}]

    leerie.filter_offtree_subtasks(plans, repo_root, [str(inspect)], st)
    subtasks, waves = leerie.schedule(plans)

    assert "feat-002" not in subtasks
    flat = [sid for w in waves for sid in w]
    assert "feat-002" not in flat
    assert "feat-001" in flat


def test_dropped_subtask_provides_tag_survivor_requires(leerie, tmp_path, monkeypatch):
    """Cross-subtask interaction: when a dropped subtask provides a tag a
    survivor requires, the drop must prune that inbound `requires` (the tag
    channel), so the survivor does NOT dangle and validate_plan survives.

    Previously this die()d with 'requires X but nothing provides it' — the
    id channel was pruned but the tag channel was not (DESIGN §5 *Id-vanishing
    operations*, the drop half). The tag prune closes that gap: a tag whose
    only provider was dropped is removed from every survivor's `requires`."""
    _capture_logs(leerie, monkeypatch)
    st = _make_state(leerie, tmp_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    inspect = tmp_path / "inspect"
    inspect.mkdir()

    plans = [{"domain": "feat", "subtasks": [
        {"id": "feat-001",
         "files_likely_touched": [str(inspect / "x.py")],
         "depends_on": [], "requires": [],
         "provides": ["api-shim-extracted"],
         "success_criteria_seed": "tag is produced",
         "size": "small"},
        {"id": "feat-002",
         "files_likely_touched": ["src/b.py"],
         "depends_on": [],
         "requires": [{"tag": "api-shim-extracted", "extent": "in_plan"}],
         "provides": [],
         "success_criteria_seed": "uses the shim",
         "size": "small"},
    ]}]

    leerie.filter_offtree_subtasks(plans, repo_root, [str(inspect)], st)
    # the orphaned requires-tag is pruned from the survivor
    surv = {s["id"]: s for s in plans[0]["subtasks"]}
    assert surv["feat-002"]["requires"] == []
    subtasks, _ = leerie.schedule(plans)
    leerie.validate_plan(subtasks)   # must NOT die() now


# ---------------------------------------------------------------------------
# dropped ids must not leave dangling depends_on
# (DESIGN §5 *Id-vanishing operations*)
# ---------------------------------------------------------------------------

def test_dropped_subtask_dep_is_pruned(leerie, tmp_path, monkeypatch):
    """A dropped id can no longer satisfy any dependent, so inbound
    `depends_on` references to it must be pruned — otherwise schedule()
    drops the edge silently and validate_plan die()s the run."""
    _capture_logs(leerie, monkeypatch)
    st = _make_state(leerie, tmp_path)
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)

    plans = [{"domain": "feat", "subtasks": [
        {"id": "feat-001", "files_likely_touched": ["/etc/passwd"],
         "depends_on": []},
        {"id": "feat-002", "files_likely_touched": ["src/b.py"],
         "depends_on": ["feat-001"]},
        {"id": "feat-003", "files_likely_touched": ["src/c.py"],
         "depends_on": ["feat-001", "feat-002"]},
    ]}]

    leerie.filter_offtree_subtasks(plans, repo_root, [], st)

    surv = plans[0]["subtasks"]
    ids = {s["id"] for s in surv}
    assert ids == {"feat-002", "feat-003"}

    by_id = {s["id"]: s for s in surv}
    assert by_id["feat-002"]["depends_on"] == []
    assert by_id["feat-003"]["depends_on"] == ["feat-002"]
    assert not [d for s in surv
                for d in (s.get("depends_on") or []) if d not in ids]


def test_no_drop_leaves_deps_untouched(leerie, tmp_path, monkeypatch):
    """Nothing dropped → no mapping → depends_on byte-identical."""
    _capture_logs(leerie, monkeypatch)
    st = _make_state(leerie, tmp_path)
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)

    plans = [{"domain": "feat", "subtasks": [
        {"id": "feat-001", "files_likely_touched": ["src/a.py"],
         "depends_on": []},
        {"id": "feat-002", "files_likely_touched": ["src/b.py"],
         "depends_on": ["feat-001"]},
    ]}]

    leerie.filter_offtree_subtasks(plans, repo_root, [], st)

    surv = plans[0]["subtasks"]
    assert [s["id"] for s in surv] == ["feat-001", "feat-002"]
    assert surv[1]["depends_on"] == ["feat-001"]
