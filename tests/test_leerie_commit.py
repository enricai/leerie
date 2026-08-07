"""`leerie_commit` disambiguates `leerie_version` (O1).

`st.data["leerie_version"]` comes from `.claude-plugin/plugin.json`, which only
moves on a `chore(release):` commit — while `install.sh` tracks `main`
(`DEFAULT_REF="main"`). So every run between releases records the same version
whether or not it carries a given fix, and a bug report citing `v0.11.1` cannot
be placed on either side of it.

That is the same shape as the gap `dangerously_force_strict_output` closed:
enough recorded to *look* attributable, not enough to actually attribute.

The launcher computes the short sha and forwards it as `LEERIE_COMMIT`; the
orchestrator records it beside the version. Absence is a normal state (a tarball
install has no checkout to read), so it must degrade to `None` rather than fail
a run or write a misleading empty string.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import leerie  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = (REPO / "leerie").read_text()


class TestStateSurface:
    def test_declared_in_state_fields(self):
        # Guard-the-guard for the parity sweep in test_state_fields.py: that
        # file checks STATE_FIELDS against the spec table generically, so a
        # named pin here is what fails loudly if this specific key is dropped.
        assert "leerie_commit" in leerie.STATE_FIELDS

    def test_declared_next_to_leerie_version(self):
        # The two are only useful together; keeping them adjacent is what stops
        # one being added or removed without the other.
        fields = list(leerie.STATE_FIELDS)
        assert abs(fields.index("leerie_commit")
                   - fields.index("leerie_version")) == 1

    def test_documented_in_the_spec_table(self):
        impl = (REPO / "docs" / "IMPLEMENTATION.md").read_text()
        assert re.search(r"^\| `leerie_commit` \|", impl, re.M), (
            "every STATE_FIELDS entry needs an IMPLEMENTATION.md §8 row")


class TestWriteSite:
    def _write_src(self):
        import inspect
        return inspect.getsource(leerie._run_phases)

    def test_reads_the_env_var(self):
        assert 'os.environ.get("LEERIE_COMMIT")' in self._write_src()

    def test_empty_env_records_none_not_empty_string(self):
        """`or None` is load-bearing.

        An empty `LEERIE_COMMIT` reaches the orchestrator whenever leerie was
        installed from a tarball, or when a launcher predating this field runs
        against a newer orchestrator. Recording `""` would render as a real but
        blank sha in the PR footer; `None` is honest absence.
        """
        src = self._write_src()
        assert 'os.environ.get("LEERIE_COMMIT") or None' in src, (
            "an unset LEERIE_COMMIT must record None, not an empty string")

    def test_written_beside_the_version(self):
        src = self._write_src()
        assert (src.index('st.data["leerie_version"]')
                < src.index('st.data["leerie_commit"]'))


class TestRoundTrip:
    def test_survives_a_real_state_save(self, tmp_path):
        st = leerie.State(tmp_path, "run-abc", repo_root=tmp_path)
        st.data["leerie_version"] = "0.11.1"
        st.data["leerie_commit"] = "abc1234"
        st.save()
        on_disk = json.loads((tmp_path / "runs" / "run-abc"
                              / "state.json").read_text())
        assert on_disk["leerie_commit"] == "abc1234"

    def test_none_round_trips_as_null(self, tmp_path):
        st = leerie.State(tmp_path, "run-def", repo_root=tmp_path)
        st.data["leerie_commit"] = None
        st.save()
        on_disk = json.loads((tmp_path / "runs" / "run-def"
                              / "state.json").read_text())
        assert on_disk["leerie_commit"] is None


class TestRendering:
    """The commit is only useful if a reader sees it beside the version."""

    def test_suffix_includes_the_commit_when_present(self):
        import inspect
        src = inspect.getsource(leerie)
        assert 'f"{version_suffix} ({leerie_commit})"' in src

    def test_suffix_omits_it_when_absent(self):
        import inspect
        src = inspect.getsource(leerie)
        # Guarded on both: no version means no suffix at all, and no commit
        # must not render an empty pair of parens.
        assert "if version_suffix and leerie_commit:" in src


class TestLauncher:
    def test_computes_the_short_sha(self):
        assert 'LEERIE_COMMIT="$(git -C "$LEERIE_REPO" rev-parse --short HEAD' \
            in LAUNCHER

    def test_git_failure_cannot_fail_a_run(self):
        """A tarball install has no checkout; that is normal, not an error."""
        m = re.search(r'LEERIE_COMMIT="\$\([^\n]*\)"', LAUNCHER)
        assert m, "LEERIE_COMMIT assignment not found"
        line = m.group(0)
        assert "2>/dev/null" in line, "git stderr must not reach the operator"
        assert "|| true" in line, (
            "a non-checkout install must yield empty, not abort under set -e")

    def test_forwarded_into_the_container(self):
        # `${VAR:-}`, not a bare `$VAR`: the run-argv array is extracted and
        # evaluated standalone under `set -u` by
        # tests/test_launcher_env_forwarding.py, so an unbound reference there
        # aborts the harness. Production always binds it at launcher start;
        # the defensive form just keeps the array self-contained.
        assert '-e "LEERIE_COMMIT=${LEERIE_COMMIT:-}"' in LAUNCHER

    def test_forward_survives_set_u_without_the_var(self):
        """The argv array must not require LEERIE_COMMIT to be bound."""
        m = re.search(r'-e "LEERIE_COMMIT=[^"]*"', LAUNCHER)
        assert m and ":-" in m.group(0), (
            "a bare $LEERIE_COMMIT breaks any standalone evaluation of the "
            "run-argv array under `set -u`")

    def test_not_on_the_forwarding_denylist(self):
        """`LEERIE_VERSION` is denied (host-only, used for the image tag); this
        one must reach the orchestrator."""
        m = re.search(r"_leerie_env_denylist=\"[^\"]*\"", LAUNCHER, re.S)
        assert m, "denylist not found"
        assert "LEERIE_COMMIT" not in m.group(0)

    def test_launcher_still_parses(self):
        r = subprocess.run(["bash", "-n", str(REPO / "leerie")],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
