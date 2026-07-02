"""Regression guard for the base image's Chromium provisioning.

feedback #64 (shipped in #23 / f31d650) bakes Chromium + chromedriver and
the rootless-container Chrome flags into the base ./Dockerfile so browser
tests (Selenium/Capybara/etc.) work without runtime setup — see
docs/IMPLEMENTATION.md "Browser-based testing". test_dockerfile_autogen.py
only covers the generated *per-repo* .leerie/Dockerfile; nothing else reads
the base image layer, so a future edit could silently drop browser-test
support. This test reads the committed ./Dockerfile directly and fails if
the chromium install or the container-flags block disappears.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _dockerfile_text() -> str:
    return (REPO_ROOT / "Dockerfile").read_text()


def test_installs_chromium():
    assert "chromium" in _dockerfile_text()


def test_installs_chromium_driver():
    assert "chromium-driver" in _dockerfile_text()


def test_bakes_container_flags_file():
    assert "/etc/chromium.d/leerie-container-flags" in _dockerfile_text()


def test_bakes_no_sandbox_flag():
    assert "--no-sandbox" in _dockerfile_text()


def test_bakes_disable_setuid_sandbox_flag():
    assert "--disable-setuid-sandbox" in _dockerfile_text()


def test_bakes_disable_dev_shm_usage_flag():
    assert "--disable-dev-shm-usage" in _dockerfile_text()


def test_container_flags_written_after_chromium_install():
    """The flags file must be baked after chromium is installed, not before."""
    text = _dockerfile_text()
    install_idx = text.index("chromium-driver")
    bake_idx = text.index("RUN echo 'CHROMIUM_FLAGS")
    assert install_idx < bake_idx
