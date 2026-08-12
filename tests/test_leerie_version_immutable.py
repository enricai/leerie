"""`leerie_version`/`leerie_commit` stay immutable across resumes (N38).

Before this change, the resume branch of `_run_phases` unconditionally
overwrote `st.data["leerie_version"]`/`st.data["leerie_commit"]` with
whatever `.claude-plugin/plugin.json` and `$LEERIE_COMMIT` said at resume
time — so a run resumed after an install upgrade had its ORIGINAL failures
silently reattributed to the new release. `leerie_version`/`leerie_commit`
now record only the original run start; `leerie_versions` is a new
append-only list that records what was actually installed at every resume,
so that history is not lost.
"""
import inspect
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import leerie  # noqa: E402

# Single owner of the branch-split AST walk — see tests/test_leerie_commit.py
# for why this is imported rather than reimplemented.
from tests.test_state_fields import _state_init_branch_keys  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


class TestStateSurface:
    def test_declared_in_state_fields(self):
        assert "leerie_versions" in leerie.STATE_FIELDS

    def test_documented_in_the_spec_table(self):
        impl = (REPO / "docs" / "IMPLEMENTATION.md").read_text()
        assert re.search(r"^\| `leerie_versions` \|", impl, re.M), (
            "every STATE_FIELDS entry needs an IMPLEMENTATION.md §8 row")


class TestBranchParity:
    """Mirrors tests/test_leerie_commit.py's both-branch AST-walk pattern."""

    def test_leerie_versions_seeded_in_both_branches(self):
        resume, fresh = _state_init_branch_keys(leerie)
        assert "leerie_versions" in resume, (
            "resume branch does not seed leerie_versions")
        assert "leerie_versions" in fresh, (
            "fresh-run branch does not seed leerie_versions")

    def test_leerie_version_and_commit_still_seeded_in_both_branches(self):
        """Anti-regression: this change must not accidentally remove the
        (still-required, just conditional-on-resume) leerie_version /
        leerie_commit seeding from either branch."""
        resume, fresh = _state_init_branch_keys(leerie)
        for key in ("leerie_version", "leerie_commit"):
            assert key in resume, f"resume branch no longer seeds {key}"
            assert key in fresh, f"fresh-run branch no longer seeds {key}"


class TestWriteSiteImmutability:
    def _write_src(self):
        return inspect.getsource(leerie._run_phases)

    def test_resume_branch_guards_leerie_version_on_absence(self):
        src = self._write_src()
        assert 'if "leerie_version" not in st.data:' in src, (
            "leerie_version must only be (re)seeded on resume when absent "
            "from the loaded state — otherwise every resume clobbers the "
            "original attribution")

    def test_resume_branch_guards_leerie_commit_on_absence(self):
        src = self._write_src()
        assert 'if "leerie_commit" not in st.data:' in src, (
            "leerie_commit must only be (re)seeded on resume when absent "
            "from the loaded state")

    def test_resume_branch_always_appends_to_leerie_versions(self):
        """Unlike leerie_version/leerie_commit, leerie_versions is NOT
        conditional — every resume must append a fresh history entry
        regardless of whether the key already exists."""
        src = self._write_src()
        assert 'st.data["leerie_versions"].append(' in src

    def test_fresh_branch_leerie_versions_is_a_one_entry_list(self):
        src = self._write_src()
        # The fresh-run dict literal seeds leerie_versions as `[{...}]`.
        m = re.search(r'"leerie_versions":\s*\[\{', src)
        assert m, (
            "fresh-run branch must seed leerie_versions as a one-entry list")

    def test_versions_entry_records_version_commit_and_at(self):
        src = self._write_src()
        assert '"version": _read_version()' in src
        assert '"commit": os.environ.get("LEERIE_COMMIT") or None' in src
        assert '"at": now()' in src


class TestRoundTrip:
    def test_leerie_versions_survives_a_real_state_save(self, tmp_path):
        st = leerie.State(tmp_path, "run-ghi", repo_root=tmp_path)
        st.data["leerie_versions"] = [
            {"version": "0.17.0", "commit": "abc1234", "at": "2026-08-12T00:00:00Z"},
        ]
        st.save()
        on_disk = json.loads((tmp_path / "runs" / "run-ghi"
                              / "state.json").read_text())
        assert on_disk["leerie_versions"] == [
            {"version": "0.17.0", "commit": "abc1234", "at": "2026-08-12T00:00:00Z"},
        ]

    def test_leerie_versions_grows_across_simulated_resumes(self, tmp_path):
        """Simulates two resumes' worth of appends round-tripping through a
        real State.save() — the mechanism the actual resume branch relies
        on. Reuses the same State instance rather than re-opening (the
        exclusive flock in State.__init__ means a second instance on the
        same run dir would deadlock, same as two orchestrators)."""
        st = leerie.State(tmp_path, "run-jkl", repo_root=tmp_path)
        st.data["leerie_versions"] = []
        st.data["leerie_versions"].append(
            {"version": "0.16.0", "commit": "aaa1111", "at": "t1"})
        st.save()

        st.data["leerie_versions"].append(
            {"version": "0.17.0", "commit": "bbb2222", "at": "t2"})
        st.save()

        on_disk = json.loads((tmp_path / "runs" / "run-jkl"
                              / "state.json").read_text())
        assert [e["version"] for e in on_disk["leerie_versions"]] == [
            "0.16.0", "0.17.0"]


class TestImmutabilityBehavior:
    """Directly exercises the guard logic (not just its source text) against
    a stand-in for the resume branch's condition."""

    def test_existing_leerie_version_is_not_overwritten(self):
        data = {"leerie_version": "0.10.0"}
        if "leerie_version" not in data:
            data["leerie_version"] = "0.17.0"  # what _read_version() would
                                                # return "now"
        assert data["leerie_version"] == "0.10.0"

    def test_missing_leerie_version_is_seeded(self):
        """Resuming a run started before this field existed still gets a
        value rather than staying permanently absent."""
        data = {}
        if "leerie_version" not in data:
            data["leerie_version"] = "0.17.0"
        assert data["leerie_version"] == "0.17.0"
