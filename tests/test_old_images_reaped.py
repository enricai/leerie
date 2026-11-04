"""An image no launch can use any more is removed by leerie (DESIGN §6 *An
image no launch can use any more is removed by leerie*).

The operator's host, 2026-11-04: the disk filled (191 of 193 GB), a
repository image build died with `no space left on device` and a run was
stopped by leerie's own disk check. The `leerie` namespace held every
version's images (`leerie:0.44.0` … `0.44.8` at 2.4 GB each, repository
images at 4.76 and 5.9 GB each) and nothing ever removed one.

The real launcher runs against a stub `nerdctl` whose image store is a file
in the world (`images`: `name|size` lines, the `leerie` namespace's) and
whose `image rm` refuses each name listed in `inuse`, as containerd refuses
an image a container uses. Each launch takes a shared hold on its version's
file under `<host dir>/image-holds/` before its first image check and keeps
it to its end; a reaper removes a version's images only while it holds that
version's file exclusively. Here the test itself plays the other launch,
taking the hold a launch of another version, or a newer launch's reaper,
would take, so each side of the window is exercised deterministically."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from tests.conftest import FRONTEND_SH_SOURCE
from tests.launcher_stub_run import SLEEP, _STUB, launch, make_world, reap, run_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
CUR = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json")
                 .read_text())["version"]

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="reads /proc")

# The image store's commands, put in front of the harness stub's `run`,
# `kill`, `info` and `version`. Builtins and absolute paths only.
_STORE_CASES = r'''
  images)
    fmt="" prev=""
    for a in "$@"; do [ "$prev" = --format ] && fmt=$a; prev=$a; done
    [ "$ns" = leerie ] || exit 0
    while IFS='|' read -r n s; do
      [ -n "$n" ] || continue
      o="${fmt//\{\{.Repository\}\}/${n%:*}}"
      o="${o//\{\{.Tag\}\}/${n##*:}}"
      o="${o//\{\{.Size\}\}/$s}"
      printf '%s\n' "$o"
    done < "$w/images"
    exit 0 ;;
  image)
    name="${!#}"
    case "$2" in
      rm)
        if [ -n "${STUB_RM_FAIL:-}" ]; then
          printf 'nerdctl: %s\n' "$STUB_RM_FAIL" >&2; exit 1
        fi
        while IFS= read -r l; do
          if [ "$l" = "$name" ]; then
            printf 'Error: conflict: unable to remove %s: in use by a container\n' "$name" >&2
            exit 1
          fi
        done < "$w/inuse"
        keep=() found=""
        while IFS= read -r l; do
          if [ "${l%%|*}" = "$name" ]; then found=1; else keep+=("$l"); fi
        done < "$w/images"
        [ -n "$found" ] || { printf 'Error: %s: not found\n' "$name" >&2; exit 1; }
        printf '%s\n' ${keep[@]+"${keep[@]}"} > "$w/images"
        exit 0 ;;
      inspect)
        [ "$ns" = leerie ] || exit 1
        while IFS= read -r l; do [ "${l%%|*}" = "$name" ] && exit 0; done < "$w/images"
        exit 1 ;;
    esac
    exit 0 ;;
  build)
    tag="" prev=""
    for a in "$@"; do [ "$prev" = -t ] && tag=$a; prev=$a; done
    [ -z "$tag" ] || printf '%s|2.4GB\n' "$tag" >> "$w/images"
    exit 0 ;;
'''

_FAIL_STUB = "#!/bin/bash\nexit 1\n"

OWN_OLD = [  # images of leerie's own, of versions older than any release
    "leerie:0.0.1", "leerie:0.0.2",
    "leerie-repo/acme-web-0123456789ab:0.0.1",
    "leerie-repo/acme-web-0123456789ab:0.0.2",
]
OWN_KEPT = [
    f"leerie:{CUR}", f"leerie-repo/acme-web-0123456789ab:{CUR}",
    "leerie:999.0.0",               # a newer version than this launch's
    "leerie:dev",                   # not a version: never older
]
FOREIGN = [
    "postgres:0.0.1", "registry.fly.io/acme:0.0.1", "leerie-other:0.0.1",
    "acme/leerie:0.0.1", "leerie-repo:0.0.1", "<none>:<none>",
]


def _world(tmp_path: Path, images: list[str], inuse: list[str] = ()) -> dict:
    w = make_world(tmp_path)
    stub = _STUB.replace('case "$1" in\n', 'case "$1" in\n' + _STORE_CASES, 1)
    assert stub != _STUB
    (w["bin"] / "nerdctl").write_text(stub.replace("__WORLD__", str(w["world"]))
                                      .replace("__SLEEP__", SLEEP))
    # No real buildctl, flyctl or ssh-agent is ever reached.
    for name in ("buildctl", "flyctl", "fly", "ssh-agent", "ssh-add"):
        (w["bin"] / name).write_text(_FAIL_STUB)
        (w["bin"] / name).chmod(0o755)
    (w["world"] / "images").write_text(
        "".join(f"{n}|{i + 1}.5GB\n" for i, n in enumerate(images)))
    (w["world"] / "inuse").write_text("".join(f"{n}\n" for n in inuse))
    return w


def _calls(w: dict) -> list[str]:
    f = w["world"] / "nerdctl.calls"
    return f.read_text().splitlines() if f.exists() else []


def _store(w: dict) -> list[str]:
    return [l.split("|")[0] for l in
            (w["world"] / "images").read_text().splitlines() if l]


def _rms(w: dict) -> list[str]:
    return [c for c in _calls(w) if " image rm " in f" {c} "]


def _holds(w: dict) -> Path:
    return w["world"] / "host" / "image-holds"


def _run(w: dict, args: list[str], env: dict | None = None,
         before=None) -> tuple[int, str]:
    log = w["world"] / f"launch-{uuid.uuid4().hex}.log"
    try:
        with open(log, "wb") as f:
            p = launch(w, args, env_extra=env or {}, stdout=f,
                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            if before:
                before(p, log)
            rc = p.wait(timeout=120)
    finally:
        reap(w["mark"])
    return rc, log.read_text(errors="replace")


def _hold(path: Path, mode: int) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, mode)
    return fd


def _wait_for(pred, timeout: float = 30) -> None:
    end = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.05)


def test_older_versions_unused_images_are_removed_and_nothing_else(tmp_path):
    """Every image of leerie's own of an older version is removed, through
    `nerdctl --namespace leerie image rm <name>`, never forced; this
    version's, a newer version's, a tag that is no version, and every name
    leerie does not make stay, and are never even asked to go. The listing
    comes before this launch's first image check, so the disk is freed
    before its build. Falsified: the `leerie_reap_old_images` call removed
    from the launcher (no image removed)."""
    w = _world(tmp_path, OWN_OLD + OWN_KEPT + FOREIGN)
    rc, out = _run(w, ["--no-push", "a task"])
    assert rc == 0, out
    assert run_argv(w), out
    assert sorted(_store(w)) == sorted(OWN_KEPT + FOREIGN)
    assert sorted(_rms(w)) == sorted(
        f"--namespace leerie image rm {n}" for n in OWN_OLD)
    assert "removed images of older leerie versions: " in out, out
    for i, n in enumerate(OWN_OLD):
        assert f"{n} ({i + 1}.5GB)" in out, out
    calls = _calls(w)
    first_inspect = next(i for i, c in enumerate(calls)
                         if c == f"--namespace leerie image inspect leerie:{CUR}")
    listing = calls.index(
        "--namespace leerie images --format {{.Repository}}:{{.Tag}}|{{.Size}}")
    assert listing < first_inspect, calls


def test_an_image_a_container_uses_is_kept_and_the_launch_runs(tmp_path):
    """containerd's refusal of an image a container uses is "in use": the
    image stays, is named as kept, the other old images still go, and the
    launch runs. Falsified: the refusal made fatal (the launch exited 1
    before its container)."""
    used = "leerie-repo/acme-web-0123456789ab:0.0.1"
    w = _world(tmp_path, OWN_OLD + [f"leerie:{CUR}"], inuse=[used])
    rc, out = _run(w, ["--no-push", "a task"])
    assert rc == 0, out
    assert run_argv(w), out
    assert sorted(_store(w)) == sorted([used, f"leerie:{CUR}"])
    assert f"kept images of older leerie versions a container still uses: {used}" in out, out


def test_a_failing_image_rm_is_tolerated(tmp_path):
    """Any refusal, not only a container's: every image stays and the
    launch still runs."""
    w = _world(tmp_path, OWN_OLD + [f"leerie:{CUR}"])
    rc, out = _run(w, ["--no-push", "a task"],
                   env={"STUB_RM_FAIL": "something else"})
    assert rc == 0, out
    assert run_argv(w), out
    assert sorted(_store(w)) == sorted(OWN_OLD + [f"leerie:{CUR}"])
    assert "removed images" not in out, out


def test_a_version_another_launch_holds_is_left_alone(tmp_path):
    """A launch of 0.0.1 holds its version's file shared (here, the test);
    this launch removes 0.0.2's images and none of 0.0.1's, not even asking.
    Falsified: the reaper's `try` lock's refusal ignored (0.0.1's images
    removed)."""
    w = _world(tmp_path, OWN_OLD + [f"leerie:{CUR}"])
    fd = _hold(_holds(w) / "0.0.1.lock", fcntl.LOCK_SH)
    try:
        rc, out = _run(w, ["--no-push", "a task"])
    finally:
        os.close(fd)
    assert rc == 0, out
    held = [n for n in OWN_OLD if n.endswith(":0.0.1")]
    assert sorted(_store(w)) == sorted(held + [f"leerie:{CUR}"])
    assert not [c for c in _rms(w) if c.endswith(":0.0.1")], _rms(w)
    assert "kept leerie 0.0.1's images: a launch of leerie 0.0.1 is using them" in out, out


def test_a_launch_waits_for_a_removal_of_its_versions_images(tmp_path):
    """The window, from the launch's side: a newer launch's reaper holds this
    version's file exclusively (the test) and removes this version's base
    while it does. This launch makes no image call until the reaper lets go,
    then finds the base gone and builds it, and runs on it: it never checked
    an image that was then removed under it. Falsified: the launch's shared
    hold removed (its image check ran while the reaper held the file, found
    the base, and its container would run on a removed image)."""
    w = _world(tmp_path, [f"leerie:{CUR}"])
    fd = _hold(_holds(w) / f"{CUR}.lock", fcntl.LOCK_EX)
    marker = "--test-- the reaper lets go"

    def reaper(p, log):
        try:
            _wait_for(lambda: f"waiting for another launch's removal of leerie {CUR}'s images"
                      in log.read_text(errors="replace") or p.poll() is not None)
            assert p.poll() is None, log.read_text(errors="replace")
            assert not [c for c in _calls(w) if " image" in f" {c}"], _calls(w)
            (w["world"] / "images").write_text("")
            with open(w["world"] / "nerdctl.calls", "a") as f:
                f.write(marker + "\n")
        finally:
            os.close(fd)

    rc, out = _run(w, ["--no-push", "a task"], before=reaper)
    assert rc == 0, out
    calls = _calls(w)
    assert marker in calls, calls
    before = calls[:calls.index(marker)]
    assert not [c for c in before if " image" in f" {c}"], calls
    assert any(c.startswith("--namespace leerie build ") and f"-t leerie:{CUR}" in c
               for c in calls[calls.index(marker):]), calls
    assert f"leerie:{CUR}" in run_argv(w)


def test_a_launch_holds_its_version_until_it_ends(tmp_path):
    """The window, from the reaper's side: while a launch's container runs,
    its version's file cannot be taken exclusively; it can the moment the
    launcher has exited (its EXIT trap lets the hold go, so no holder
    outlives the launch). Falsified: the shared hold removed (taken at
    once); the EXIT traps' release removed (still held after the exit)."""
    w = _world(tmp_path, [f"leerie:{CUR}"])
    path = _holds(w) / f"{CUR}.lock"
    seen = {}

    def probe(p, log):
        _wait_for(lambda: bool(run_argv(w)) or p.poll() is not None)
        assert p.poll() is None, log.read_text(errors="replace")
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen["held"] = True
        finally:
            os.close(fd)

    rc, out = _run(w, ["--no-push", "a task"],
                   env={"STUB_RUN_SECONDS": "3"}, before=probe)
    assert rc == 0, out
    assert seen == {"held": True}
    fd = os.open(path, os.O_RDWR)
    try:
        assert _try_ex(fd)
    finally:
        os.close(fd)


def _try_ex(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    fcntl.flock(fd, fcntl.LOCK_UN)
    return True


def test_a_fly_launch_reaps_nothing_and_holds_nothing(tmp_path):
    """`--runtime fly`: the launch reaches the base image check (so it is
    past where a local launch reaps) and lists, removes and holds nothing.
    Falsified: the block's `RUNTIME=local` guard removed (the old images
    were removed)."""
    w = _world(tmp_path, OWN_OLD + [f"leerie:{CUR}"])
    _run(w, ["--runtime", "fly", "--no-push", "a task"],
         env={"LEERIE_FLY_APP": "acme"})
    calls = _calls(w)
    assert f"--namespace leerie image inspect leerie:{CUR}" in calls, calls
    assert not [c for c in calls if " images " in f" {c} "], calls
    assert not _rms(w), calls
    assert sorted(_store(w)) == sorted(OWN_OLD + [f"leerie:{CUR}"])
    assert not _holds(w).exists()


@pytest.mark.parametrize("a,b,older", [
    ("0.44.0", "0.44.8", True), ("0.44.8", "0.44.8", False),
    ("0.44.10", "0.44.9", False), ("0.9", "0.10", True),
    ("0.44", "0.44.0", False), ("0.44", "0.44.1", True),
    ("1.0.0", "0.99.99", False), ("010", "9", False),
    ("1.0.0-rc1", "2", False), ("<none>", "1", False), ("dev", "1", False),
    ("0.1", "dev", False), ("", "1", False), ("1..2", "3", False),
    ("1234567890", "1", False),
])
def test_version_order(a, b, older):
    r = subprocess.run(
        ["bash", "-c", FRONTEND_SH_SOURCE + '_leerie_version_older "$1" "$2"',
         "x", a, b], capture_output=True, text=True, timeout=30)
    assert (r.returncode == 0) is older, (a, b, r.stderr)
