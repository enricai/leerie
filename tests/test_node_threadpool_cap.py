"""Tests for `_normalize_node_threadpool` (N22, mirroring
`tests/test_normalize_pip_installs.py`'s shape): Node/pnpm build tooling's
Rust/Tokio (SWC/Turbopack) and libuv thread pools size to the host's full
core count by default, which can exhaust the per-worker `pids.max` cgroup
cap and get recorded as a `build-failed` defect of the diff rather than a
resource-limit collision. Thread-pool-capping env vars must be injected
into every Node/pnpm/npm/yarn install-or-build recipe entry.
"""
from __future__ import annotations


def _entry(cmd: list[str], kind: str = "install") -> dict:
    return {"kind": kind, "command": cmd, "working_dir": ".",
            "timeout_s": 600}


class TestNormalizeNodeThreadpool:
    def test_pnpm_install_gets_thread_pool_caps(self, leerie):
        r = leerie._normalize_node_threadpool([_entry(["pnpm", "install"])])
        env = r[0]["env"]
        assert env["UV_THREADPOOL_SIZE"] == "4"
        assert env["RAYON_NUM_THREADS"] == "4"
        # Command itself is unchanged — the cap travels via env, not argv.
        assert r[0]["command"] == ["pnpm", "install"]

    def test_npm_and_yarn_also_capped(self, leerie):
        for pm, cmd in (("npm", ["npm", "ci"]), ("yarn", ["yarn", "install"])):
            r = leerie._normalize_node_threadpool([_entry(cmd)])
            assert r[0]["env"]["UV_THREADPOOL_SIZE"] == "4", pm
            assert r[0]["env"]["RAYON_NUM_THREADS"] == "4", pm

    def test_build_kind_also_capped(self, leerie):
        r = leerie._normalize_node_threadpool(
            [_entry(["pnpm", "run", "build"], kind="build")])
        assert r[0]["env"]["UV_THREADPOOL_SIZE"] == "4"

    def test_existing_env_value_not_overwritten(self, leerie):
        entry = _entry(["pnpm", "install"])
        entry["env"] = {"UV_THREADPOOL_SIZE": "8"}
        r = leerie._normalize_node_threadpool([entry])
        # Entry-supplied value wins; the missing var is still filled in.
        assert r[0]["env"]["UV_THREADPOOL_SIZE"] == "8"
        assert r[0]["env"]["RAYON_NUM_THREADS"] == "4"

    def test_non_node_command_untouched(self, leerie):
        entry = _entry(["pip", "install", "-r", "requirements.txt"])
        r = leerie._normalize_node_threadpool([entry])
        assert r[0] is entry  # unchanged, not even shallow-copied
        assert "env" not in r[0]

    def test_none_kind_entry_untouched(self, leerie):
        entry = {"kind": "none", "command": []}
        r = leerie._normalize_node_threadpool([entry])
        assert r[0] is entry

    def test_empty_recipe(self, leerie):
        assert leerie._normalize_node_threadpool([]) == []


class TestPhaseProvisionRecipeSectionRendersEnv:
    def test_env_rendered_as_var_prefix(self, leerie):
        recipe = leerie._normalize_node_threadpool(
            [_entry(["pnpm", "install"])])
        section = leerie._format_provision_recipe_section(
            recipe, audience="implementer")
        assert "UV_THREADPOOL_SIZE=4" in section
        assert "RAYON_NUM_THREADS=4" in section
        assert "pnpm install" in section

    def test_no_env_no_prefix(self, leerie):
        recipe = [_entry(["make", "build"], kind="build")]
        section = leerie._format_provision_recipe_section(
            recipe, audience="implementer")
        assert section is not None
        last_line = section.splitlines()[-1]
        assert last_line.strip().startswith("1. make build")
