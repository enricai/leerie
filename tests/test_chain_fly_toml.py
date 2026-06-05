"""Structural tests for chain/fly.toml and chain/Dockerfile.

Ensures chain/fly.toml is valid TOML, declares the required sections for a
persistent HTTP service (distinguishing it from the machine-per-run root
fly.toml), and that chain/Dockerfile references the chain server entrypoint
and omits leerie-worker-only layers.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHAIN_FLY_TOML = REPO_ROOT / "chain" / "fly.toml"
CHAIN_DOCKERFILE = REPO_ROOT / "chain" / "Dockerfile"


# ---------------------------------------------------------------------------
# TOML loader helper (mirrors tests/test_fly_toml.py)
# ---------------------------------------------------------------------------

def _load_chain_fly_toml() -> dict:
    if sys.version_info >= (3, 11):
        import tomllib
        with CHAIN_FLY_TOML.open("rb") as f:
            return tomllib.load(f)
    else:
        try:
            import tomli  # type: ignore[import]
            with CHAIN_FLY_TOML.open("rb") as f:
                return tomli.load(f)
        except ImportError:
            pytest.skip("tomli not installed and Python < 3.11")


# ---------------------------------------------------------------------------
# chain/fly.toml tests
# ---------------------------------------------------------------------------

def test_chain_fly_toml_exists():
    assert CHAIN_FLY_TOML.exists(), "chain/fly.toml is missing"


def test_chain_fly_toml_is_valid_toml():
    data = _load_chain_fly_toml()
    assert isinstance(data, dict)


def test_chain_fly_toml_declares_app():
    data = _load_chain_fly_toml()
    assert "app" in data, "chain/fly.toml must declare 'app'"
    assert data["app"], "chain/fly.toml 'app' must be non-empty"


def test_chain_fly_toml_declares_primary_region():
    data = _load_chain_fly_toml()
    assert "primary_region" in data, "chain/fly.toml must declare 'primary_region'"


def test_chain_fly_toml_declares_build():
    data = _load_chain_fly_toml()
    build = data.get("build", {})
    assert build, "chain/fly.toml must have a [build] section"
    # Either a dockerfile reference or a pre-built registry image.
    has_ref = "dockerfile" in build or "image" in build
    assert has_ref, "chain/fly.toml [build] must declare 'dockerfile' or 'image'"


def test_chain_fly_toml_has_http_service():
    data = _load_chain_fly_toml()
    # Fly accepts either [http_service] (modern) or [[services]] (legacy).
    has_http = "http_service" in data or "services" in data
    assert has_http, (
        "chain/fly.toml must declare [http_service] — the chain app is a "
        "persistent HTTP service unlike the root fly.toml"
    )


def test_chain_fly_toml_internal_port():
    data = _load_chain_fly_toml()
    svc = data.get("http_service") or {}
    port = svc.get("internal_port")
    assert port == 8080, (
        f"chain/fly.toml [http_service].internal_port must be 8080 "
        f"(chain.server default); got {port!r}"
    )


def test_chain_fly_toml_has_mounts():
    data = _load_chain_fly_toml()
    assert "mounts" in data, (
        "chain/fly.toml must declare [mounts] for the SQLite persistent volume"
    )
    mounts = data["mounts"]
    # mounts may be a dict (single mount) or a list of dicts.
    if isinstance(mounts, list):
        assert mounts, "chain/fly.toml [mounts] must be non-empty"
        mounts = mounts[0]
    assert "source" in mounts and mounts["source"], (
        "chain/fly.toml [mounts] must declare a non-empty 'source' volume name"
    )
    assert "destination" in mounts and mounts["destination"], (
        "chain/fly.toml [mounts] must declare a non-empty 'destination' path"
    )


def test_chain_fly_toml_persistent_min_machines():
    data = _load_chain_fly_toml()
    # The chain app must keep at least one machine alive to receive webhooks.
    # Check both [deploy] and [http_service] for the setting.
    deploy_min = data.get("deploy", {}).get("min_machines_running", 0)
    svc_min = data.get("http_service", {}).get("min_machines_running", 0)
    effective_min = max(deploy_min, svc_min)
    assert effective_min >= 1, (
        f"chain/fly.toml min_machines_running must be >= 1 (persistent service); "
        f"got deploy={deploy_min}, http_service={svc_min}"
    )


# ---------------------------------------------------------------------------
# chain/Dockerfile tests
# ---------------------------------------------------------------------------

def test_chain_dockerfile_exists():
    assert CHAIN_DOCKERFILE.exists(), "chain/Dockerfile is missing"


def test_chain_dockerfile_has_chain_entrypoint():
    text = CHAIN_DOCKERFILE.read_text()
    assert "chain" in text, (
        "chain/Dockerfile must reference the chain package (e.g. python3 -m chain)"
    )
    # Must have a CMD or ENTRYPOINT that launches the chain server.
    has_cmd = re.search(r'^(CMD|ENTRYPOINT)\s', text, re.MULTILINE)
    assert has_cmd, "chain/Dockerfile must declare CMD or ENTRYPOINT"


def test_chain_dockerfile_omits_worker_toolchain():
    text = CHAIN_DOCKERFILE.read_text()
    # Strip comments so we only match actual RUN/ENV/COPY/ENTRYPOINT lines.
    active_lines = "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )
    # mise and claude-code are worker-only — the chain image must not bake them.
    assert "mise" not in active_lines, (
        "chain/Dockerfile must not install mise (worker-only toolchain)"
    )
    assert "claude-code" not in active_lines and "claude_code" not in active_lines, (
        "chain/Dockerfile must not install claude-code (worker-only toolchain)"
    )


def test_chain_dockerfile_installs_git_and_gh():
    text = CHAIN_DOCKERFILE.read_text()
    assert "git" in text, "chain/Dockerfile must install git"
    assert "gh" in text, "chain/Dockerfile must install gh"
