"""A host-wide image step runs once, under a host lock (DESIGN §6 *A
host-wide image step runs once, under a host lock*).

The operator's live 0.44.0 launches: five launchers started within about two
minutes on one checkout each exported leerie's base to a tarball in /tmp and
loaded it into BuildKit's worker namespace at the same moment, about 2.4 GB
each. Nothing serialised them. Here N launches of the launcher's own text
run at once against one stub image store whose state is shared on disk, and
each step that should happen once per host (the base build, the base's copy,
the frontend's pull) or once per checkout (the repository image's build)
happens once; every launch still ends with the image.

`leerie_lock` itself: a second taker waits for the first, the lock is not
held by any descriptor the shell's children inherit, and it is let go when
the launch that took it dies."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from tests.committed_tree import commit_tree
from tests.conftest import BUILDCTL_STUB, FRONTEND_SH_SOURCE, LOG_SH_SOURCE
from tests.repo_image_block_extract import extract_block

REPO_ROOT = Path(__file__).resolve().parent.parent
N = 5
BASE_BLOCK = ("# --- build image if not present",
              "# --- per-repo derived image (local nerdctl)")
REPO_BLOCK = ("# --- per-repo derived image (local nerdctl) ",
              "\n# --- translate --inspect-dir paths")


def _launcher() -> str:
    return (REPO_ROOT / "leerie").read_text()


# An image store shared by every launch, kept as files in $STUB_DIR:
# `base` (leerie's base in `leerie`), `worker-base` (its copy in the
# worker's namespace `default`), `frontend` (the pinned frontend there) and
# `repo` (the repository image). Each step that changes it logs one line to
# $STUB_DIR/steps and takes a moment, as the real one does, so launches
# that are not serialised overlap. Builtins and absolute paths only.
_STORE_NERDCTL = r'''
nerdctl() {
  local _ns="" _a _o="" _t="" _prev=""
  if [ "${1:-}" = "--namespace" ]; then _ns="$2"; shift 2; fi
  local _c="${1:-}"; shift || true
  case "$_c:$_ns" in
    image:leerie)
      case "$*" in
        *RepoDigests*) [ -e "$STUB_DIR/repo" ] || return 1
                       echo "leerie-repo/x@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" ;;
        *RootFS.Layers*) case "$*" in *"$IMAGE_TAG") echo '["sha256:b"]' ;;
                         *) echo '["sha256:b","sha256:w"]' ;; esac ;;
        *Config*) echo '{"Entrypoint":["/e"],"Image":"x"}' ;;
        *.Id*) [ -e "$STUB_DIR/base" ] || return 1; echo sha256:base-id ;;
        *) [ -e "$STUB_DIR/base" ] || return 1 ;;
      esac ;;
    image:default)
      case "$*" in
        *.Id*) [ -e "$STUB_DIR/worker-base" ] || return 1; echo sha256:base-id ;;
        *dockerfile*) [ -e "$STUB_DIR/frontend" ] || return 1 ;;
        *) return 1 ;;
      esac ;;
    images:*) return 0 ;;
    pull:default)
      echo pull >> "$STUB_DIR/steps"; /usr/bin/sleep 0.5
      : > "$STUB_DIR/frontend" ;;
    save:leerie) echo image-bytes ;;
    load:default)
      while IFS= read -r _a; do :; done
      echo load >> "$STUB_DIR/steps"; /usr/bin/sleep 0.5
      : > "$STUB_DIR/worker-base" ;;
    build:leerie)
      for _a in "$@"; do
        [ "$_prev" = -o ] && _o="$_a"
        [ "$_prev" = -t ] && _t="$_a"
        _prev="$_a"
      done
      /usr/bin/sleep 0.5
      if [ "$_t" = "$IMAGE_TAG" ]; then
        echo base-build >> "$STUB_DIR/steps"; : > "$STUB_DIR/base"
      elif [ -n "$_t" ]; then
        echo repo-build >> "$STUB_DIR/steps"; : > "$STUB_DIR/repo"
      else
        _o="${_o#*dest=}"; _o="${_o%%\",tar=false}"
        /usr/bin/mkdir -p "$_o"
        printf '{"manifests":[{"digest":"sha256:%s"}]}' \
          cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc \
          > "$_o/index.json"
      fi ;;
    *) return 0 ;;
  esac
}
'''


def _launch(tmp_path: Path, body: str, env: dict[str, str]) -> subprocess.Popen:
    script = ("set -euo pipefail\n" + LOG_SH_SOURCE + FRONTEND_SH_SOURCE
              + BUILDCTL_STUB + 'remote_log() { echo "$*" >&2; }\n'
              + _STORE_NERDCTL + body)
    return subprocess.Popen(
        ["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"),
             "TMPDIR": str(tmp_path / "tmp"), "STUB_DIR": str(tmp_path / "store"),
             "IMAGE_TAG": "leerie:9.9.9", "LEERIE_VERSION": "9.9.9", **env})


def _all(procs: list[subprocess.Popen]) -> list[str]:
    errs = []
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err[-3000:]
        errs.append(err)
    return errs


def _setup(tmp_path: Path) -> None:
    for d in ("home", "tmp", "store", "host"):
        (tmp_path / d).mkdir()


def _steps(tmp_path: Path) -> list[str]:
    f = tmp_path / "store" / "steps"
    return f.read_text().split() if f.exists() else []


def test_concurrent_launches_build_leeries_base_once(tmp_path):
    """N launches at once, no base on the host: one builds it and pulls the
    frontend, the others wait under the host's image lock and find it.
    Falsified live (2026-11-03): the lock dropped from the base block (5
    base builds)."""
    _setup(tmp_path)
    body = ("_repo_build_done() { :; }\n"
            f'_leerie_image_lock="{tmp_path}/host/image.lock"\n'
            + extract_block(_launcher(), *BASE_BLOCK))
    procs = [_launch(tmp_path, body, {"LEERIE_REPO": str(tmp_path),
                                      "LEERIE_STATE_HOST_DIR": str(tmp_path)})
             for _ in range(N)]
    _all(procs)
    steps = _steps(tmp_path)
    assert steps.count("base-build") == 1, steps
    assert steps.count("pull") == 1, steps
    assert not list((tmp_path / "tmp").iterdir())


def test_concurrent_launches_on_one_checkout_copy_and_build_once(tmp_path):
    """N launches at once on one checkout whose repository image is not
    built, the base present only in leerie's namespace: the base is copied
    into the worker's namespace once, streamed with no file in TMPDIR, the
    frontend pulled once, the repository image built once (its two builds),
    and every launch ends with the image recorded. Falsified live
    (2026-11-03): the checkout's lock dropped (several repository builds,
    loads and pulls). The host lock alone is falsified by the next test:
    here the checkout's lock already serialises."""
    _setup(tmp_path)
    (tmp_path / "store" / "base").touch()
    repo = tmp_path / "repo"
    (repo / ".leerie").mkdir(parents=True)
    (repo / ".leerie" / "Dockerfile").write_text(
        "ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    commit_tree(repo)
    state = tmp_path / "state"
    state.mkdir()
    body = (f'_leerie_image_lock="{tmp_path}/host/image.lock"\n'
            'USER_REPO="$STUB_REPO"\n'
            + extract_block(_launcher(), *REPO_BLOCK)
            + '\necho "REF=$REPO_IMAGE_REF"\n')
    procs = [_launch(tmp_path, body, {"STUB_REPO": str(repo),
                                      "LEERIE_STATE_HOST_DIR": str(state)})
             for _ in range(N)]
    outs = [p.communicate(timeout=120) for p in procs]
    for p, (out, err) in zip(procs, outs):
        assert p.returncode == 0, err[-3000:]
        assert out.strip().endswith("REF=sha256:" + "a" * 64), \
            (out, err[-2000:])
    steps = _steps(tmp_path)
    assert steps.count("load") == 1, steps
    assert steps.count("pull") == 1, steps
    assert steps.count("repo-build") == 1, steps
    assert not list((tmp_path / "tmp").iterdir())
    # No containerd config under this HOME: each launch that decided to
    # build warned that the content store is shared, and built on (review
    # round 38, isolation X2; DESIGN §6 *The content store*).
    warned = [err for _, err in outs
              if "warning: containerd shares stored content" in err]
    assert len(warned) == 1, [err[-800:] for _, err in outs]
    assert "systemctl --user restart containerd" in warned[0]
    waited = sum("waiting for another launch" in err for _, err in outs)
    assert waited >= 1, [err[-500:] for _, err in outs]


_LOCK = FRONTEND_SH_SOURCE + 'remote_log() { echo "$*" >&2; }\n'


def test_a_second_taker_waits_until_the_first_lets_go(tmp_path):
    """The first launch holds the lock for 3 s; a second, started while it
    does, gets it only after it lets go, and says once that it waits."""
    lock = tmp_path / "l" / "image.lock"
    first = subprocess.Popen(
        ["bash", "-c", _LOCK + f'leerie_lock "{lock}" x || exit 3\n'
         'echo HELD; /usr/bin/sleep 3\n'
         'echo "RELEASED $(/usr/bin/date +%s.%N)"\n'
         'leerie_unlock "$LEERIE_LOCK_HOLDER"\n'],
        stdout=subprocess.PIPE, text=True, env={"PATH": "/usr/bin:/bin"})
    assert first.stdout.readline() == "HELD\n"
    second = subprocess.run(
        ["bash", "-c", _LOCK + f'leerie_lock "{lock}" "the first" || exit 3\n'
         'echo "GOT $(/usr/bin/date +%s.%N)"\n'
         'leerie_unlock "$LEERIE_LOCK_HOLDER"\n'],
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin"})
    rest, _ = first.communicate(timeout=30)
    released = float(rest.split()[1])
    got = float(second.stdout.split()[1])
    assert got >= released, (rest, second.stdout)
    assert second.stderr.count("waiting for the first...") == 1, second.stderr


def test_the_lock_is_let_go_when_its_launch_dies(tmp_path):
    """A launch that took the lock and started a child that outlives it is
    SIGKILLed: the lock is free within about a second, though the child is
    still running (it holds no descriptor of the lock), and no ready file
    is left. Falsified live (2026-11-03): the holder's liveness check of the
    launch removed (the second taker times out)."""
    lock = tmp_path / "l" / "image.lock"
    first = subprocess.Popen(
        ["bash", "-c", _LOCK + f'leerie_lock "{lock}" x || exit 3\n'
         '/usr/bin/sleep 30 </dev/null >/dev/null 2>&1 &\n'
         'echo "CHILD $!"; echo HELD; /usr/bin/sleep 30\n'],
        stdout=subprocess.PIPE, text=True, env={"PATH": "/usr/bin:/bin"},
        start_new_session=True)
    child = int(first.stdout.readline().split()[1])
    assert first.stdout.readline() == "HELD\n"
    os.kill(first.pid, signal.SIGKILL)
    first.wait(timeout=10)
    try:
        t = time.monotonic()
        second = subprocess.run(
            ["bash", "-c", _LOCK + f'leerie_lock "{lock}" x || exit 3\n'
             'leerie_unlock "$LEERIE_LOCK_HOLDER"; echo GOT\n'],
            capture_output=True, text=True, timeout=10,
            env={"PATH": "/usr/bin:/bin"})
        assert second.stdout == "GOT\n", second.stderr
        assert time.monotonic() - t < 5
        os.kill(child, 0)  # the child still runs
    finally:
        os.killpg(first.pid, signal.SIGKILL)
    assert sorted(p.name for p in lock.parent.iterdir()) == ["image.lock"]


def test_concurrent_launches_on_different_checkouts_copy_the_base_once(
        tmp_path):
    """N launches at once on N checkouts (each its own state root), the base
    present only in leerie's namespace: the base is copied into the worker's
    namespace once and the frontend pulled once, under the host's image
    lock, while each checkout builds its own image. Falsified live
    (2026-11-03): the host lock dropped from the per-repo block (several
    loads and pulls)."""
    _setup(tmp_path)
    (tmp_path / "store" / "base").touch()
    body = (f'_leerie_image_lock="{tmp_path}/host/image.lock"\n'
            'USER_REPO="$STUB_REPO"\n'
            + extract_block(_launcher(), *REPO_BLOCK))
    procs = []
    for i in range(N):
        repo = tmp_path / f"repo{i}"
        (repo / ".leerie").mkdir(parents=True)
        (repo / ".leerie" / "Dockerfile").write_text(
            f"ARG BASE_IMAGE\nFROM $BASE_IMAGE\n# {i}\n")
        commit_tree(repo)
        state = tmp_path / f"state{i}"
        state.mkdir()
        procs.append(_launch(tmp_path, body, {
            "STUB_REPO": str(repo), "LEERIE_STATE_HOST_DIR": str(state)}))
    _all(procs)
    steps = _steps(tmp_path)
    assert steps.count("load") == 1, steps
    assert steps.count("pull") == 1, steps
    assert steps.count("repo-build") >= 1, steps
