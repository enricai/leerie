"""scripts/remote/registry_image.py: the digest a tag names, and the
repository image's layout, read from a registry (review round 29, isolation
F1). Driven against tests/fake_registry.py on loopback, through the
registry's Bearer token challenge.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fake_registry import FakeRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "remote" / "registry_image.py"

BASE_LAYERS = ["sha256:" + "1" * 64, "sha256:" + "2" * 64]
WORKER_LAYER = "sha256:" + "3" * 64
CONFIG = {"Entrypoint": ["/opt/leerie-image/scripts/container-entry.sh"],
          "User": "root", "Env": ["PATH=/usr/bin"]}


def _run(*args: str, password: str = "") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args],
                          input=password, capture_output=True, text=True,
                          timeout=60)


@pytest.fixture
def registry():
    with FakeRegistry() as r:
        yield r


def _base(registry: FakeRegistry) -> str:
    d = registry.put_image("testapp", "1.0", BASE_LAYERS, CONFIG)
    return f"{registry.host}/testapp@{d}"


def test_digest_is_the_manifest_the_tag_names(registry):
    d = registry.put_image("testapp", "1.0", BASE_LAYERS, CONFIG)
    r = _run("digest", f"{registry.host}/testapp:1.0")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == d
    # Through the token realm the challenge named, scoped to the repository.
    assert registry.token_requests
    assert "repository%3Atestapp%3Apull" in registry.token_requests[0]


def test_a_registry_whose_digest_disagrees_with_the_body_is_refused(registry):
    registry.put_image("testapp", "1.0", BASE_LAYERS, CONFIG)
    registry.lie_about.add("1.0")
    r = _run("digest", f"{registry.host}/testapp:1.0")
    assert r.returncode == 2
    assert "registry says" in r.stderr


def test_a_missing_tag_is_an_error(registry):
    r = _run("digest", f"{registry.host}/testapp:nope")
    assert r.returncode == 2
    assert "HTTP 404" in r.stderr


def test_the_base_plus_one_layer_with_its_config_is_leeries_layout(registry):
    base = _base(registry)
    d = registry.put_image("testapp", "1.0-r", BASE_LAYERS + [WORKER_LAYER],
                           CONFIG)
    r = _run("layout", base, f"{registry.host}/testapp@{d}")
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("layers, config, why", [
    (BASE_LAYERS + [WORKER_LAYER, "sha256:" + "4" * 64], CONFIG,
     "not the base's 2 plus one"),
    (BASE_LAYERS, CONFIG, "not the base's 2 plus one"),
    (["sha256:" + "9" * 64, BASE_LAYERS[1], WORKER_LAYER], CONFIG,
     "not the base's 2 plus one"),
    (BASE_LAYERS + [WORKER_LAYER], {**CONFIG, "Entrypoint": ["/bin/sh"]},
     "configuration is not the base's"),
    (BASE_LAYERS + [WORKER_LAYER], {**CONFIG, "User": "1000"},
     "configuration is not the base's"),
])
def test_anything_else_is_not(registry, layers, config, why):
    base = _base(registry)
    d = registry.put_image("testapp", "1.0-r", layers, config)
    r = _run("layout", base, f"{registry.host}/testapp@{d}")
    assert r.returncode == 1, r.stderr
    assert why in r.stderr


def test_labels_and_image_are_not_compared(registry):
    """A builder may add its own labels, and `Image` names the image
    inspected; neither changes what runs."""
    base = _base(registry)
    d = registry.put_image("testapp", "1.0-r", BASE_LAYERS + [WORKER_LAYER],
                           {**CONFIG, "Labels": {"builder": "x"},
                            "Image": "sha256:" + "8" * 64})
    r = _run("layout", base, f"{registry.host}/testapp@{d}")
    assert r.returncode == 0, r.stderr


def test_an_index_is_read_for_linux_amd64(registry):
    base = _base(registry)
    good = registry.put_image("testapp", "amd", BASE_LAYERS + [WORKER_LAYER],
                              CONFIG)
    bad = registry.put_image("testapp", "arm", ["sha256:" + "7" * 64],
                             CONFIG)
    registry.put_index("testapp", "1.0-r", {("linux", "amd64"): good,
                                            ("linux", "arm64"): bad})
    r = _run("layout", base, f"{registry.host}/testapp:1.0-r")
    assert r.returncode == 0, r.stderr


def test_only_loopback_registries_are_spoken_to_over_http():
    spec = importlib.util.spec_from_file_location("registry_image", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._scheme("registry.fly.io") == "https"
    assert mod._scheme("example.com:5000") == "https"
    assert mod._scheme("127.0.0.1:5000") == "http"
    assert mod._scheme("localhost:5000") == "http"
    assert mod.parse_ref("registry.fly.io/app:1.0-abc") == (
        "registry.fly.io", "app", "1.0-abc")
    with pytest.raises(mod.RegistryError):
        mod.parse_ref("app:1.0")
