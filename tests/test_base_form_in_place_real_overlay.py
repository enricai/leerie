"""The shim's `--replace` and `--remove` in a REAL held-out sandbox (DESIGN §8
*Where the base form runs*), opt-in.

Everywhere else the suite runs held-out sandboxes through its emulator,
because a developer host and CI cannot build the overlay. A `--replace` or
`--remove` the real overlay refused would be a confinement failure and stop
every run that checks an existing test, so this drives the real shim where
the overlay can be built: inside a leerie repository image, started as the
launcher starts one (`--security-opt seccomp=unconfined`, the
`leerie-container` AppArmor profile, the state root at `/leerie-state`, the
only place that profile lets an overlay be mounted), and run as `leerie` in
the nested user namespace `container-entry.sh`'s rootless branch makes
(`unshare --user --map-user=<leerie> --map-group=<leerie>`), with the image's
worker root. `tests/real_overlay_base_form_probe.sh` checks, on a real git
tree: the replacement and the removal seen inside (by `cat`, `ls` and
`git status`), the tree untouched outside afterwards, a link at the path or
on its way refused as a taken path (taken descriptor written, refusal
descriptor empty, the command not run, nothing written through), and a real
vitest running the base form in place and failing on the old assertion,
where the final form passes.

Enabled by `LEERIE_TEST_OVERLAY_IMAGE` (a leerie repository image already
present in `nerdctl --namespace leerie`; nothing is built or pulled) and
`LEERIE_TEST_VITEST_DIR` (a directory holding `node_modules/vitest`), with
`node` on PATH; skipped otherwise. Never touches another container: its own
is named `<LEERIE_TEST_OVERLAY_NAME_PREFIX or leerie-test-overlay>-<random>`
and run with `--rm`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

IMAGE = os.environ.get("LEERIE_TEST_OVERLAY_IMAGE", "")
VITEST_DIR = os.environ.get("LEERIE_TEST_VITEST_DIR", "")
NERDCTL = shutil.which("nerdctl")
NODE = shutil.which("node")
PROBE = REPO_ROOT / "tests" / "real_overlay_base_form_probe.sh"


def _image_present() -> bool:
    if not (NERDCTL and IMAGE):
        return False
    r = subprocess.run([NERDCTL, "--namespace", "leerie", "image", "inspect",
                        IMAGE], capture_output=True, timeout=60)
    return r.returncode == 0


ENABLED = bool(
    IMAGE and NERDCTL and NODE and VITEST_DIR
    and (Path(VITEST_DIR) / "node_modules" / "vitest").is_dir()
    and _image_present())


@pytest.mark.skipif(not ENABLED, reason=(
    "opt-in: LEERIE_TEST_OVERLAY_IMAGE (a present leerie repository image), "
    "LEERIE_TEST_VITEST_DIR and node on PATH"))
def test_replace_and_remove_in_a_real_held_out_sandbox(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    shutil.copy(PROBE, state / "probe.sh")
    node_prefix = Path(os.path.realpath(NODE)).parent.parent
    prefix = os.environ.get("LEERIE_TEST_OVERLAY_NAME_PREFIX",
                            "leerie-test-overlay")
    name = f"{prefix}-{uuid.uuid4().hex[:12]}"
    entry = ('exec unshare --user --map-user="$(id -u leerie)" '
             '--map-group="$(id -g leerie)" env HOME=/home/leerie '
             'USER=leerie LOGNAME=leerie /bin/bash /leerie-state/probe.sh')
    r = subprocess.run(
        [NERDCTL, "--namespace", "leerie", "run", "--rm", "--name", name,
         "--network", "none",
         "--security-opt", "seccomp=unconfined",
         "--security-opt", "apparmor=leerie-container",
         "-v", f"{REPO_ROOT / 'scripts'}:/mc/scripts:ro",
         "-v", f"{VITEST_DIR}:/mc/node:ro",
         "-v", f"{node_prefix}:/mc/nodebin:ro",
         "-v", f"{state}:/leerie-state",
         "--entrypoint", "/bin/bash", IMAGE, "-c", entry],
        capture_output=True, text=True, timeout=900)
    out = r.stdout
    fails = [ln for ln in out.splitlines() if ln.startswith("FAIL")]
    assert r.returncode == 0 and not fails, out + r.stderr[-2000:]
    passes = [ln for ln in out.splitlines() if ln.startswith("PASS")]
    assert len(passes) == 14, out
    assert "RESULT: ALL PASS" in out
