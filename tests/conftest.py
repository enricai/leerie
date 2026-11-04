"""Shared pytest fixtures for the leerie test suite.

leerie.py is a single script (no package), so we load it once as a
module via importlib and expose it to every test via the `leerie`
fixture.
"""
from __future__ import annotations

import ast
import asyncio
import atexit
import contextlib
import ctypes
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from _pytest import outcomes as _pytest_outcomes

REPO_ROOT = Path(__file__).resolve().parent.parent
# The launcher's logging and terminal-filter helpers (`remote_log`,
# `_terminal_relay`, `_terminal_safe`), sourced by a harness that runs an
# extracted launcher block: the block prints text a repository or a machine
# chose through the real filter (DESIGN §6 *No text leerie did not write
# reaches the operator's terminal as a control sequence*). A harness that
# stubs `remote_log` defines its stub after this line.
LOG_SH_SOURCE = f'. "{REPO_ROOT}/scripts/remote/_log.sh"\n'
# The launcher sources scripts/dockerfile-frontend.sh near its top
# (LEERIE_DOCKERFILE_FRONTEND, ensure_frontend_in_worker_ns, LEERIE_IMAGE_NS);
# a harness that runs an extracted block holding a build sources it the same
# way.
FRONTEND_SH_SOURCE = f'. "{REPO_ROOT}/scripts/dockerfile-frontend.sh"\n'
# A bash `buildctl` stub for a harness that runs a repository build, defined
# after FRONTEND_SH_SOURCE: leerie_buildkit_resolve asks BuildKit which
# containerd namespaces its workers read (DESIGN §6 *No build consumes what
# another build left in BuildKit*), and a test must not ask the host's daemon
# (CI has none). One daemon, one containerd worker reading
# `$STUB_WORKER_NS` (default `default`, the standard rootless setup's);
# `$STUB_BUILDCTL_RC` non-zero makes no daemon answer; with `$BUILDCTL_LOG`
# set each call's arguments are appended to it. Builtins only.
BUILDCTL_STUB = r"""
buildctl() {
  [ -z "${BUILDCTL_LOG:-}" ] || printf '%s\n' "$*" >> "$BUILDCTL_LOG"
  [ "${STUB_BUILDCTL_RC:-0}" = 0 ] || return "$STUB_BUILDCTL_RC"
  printf '[{"id":"w0","labels":{"org.mobyproject.buildkit.worker.containerd.namespace":"%s"}}]\n' \
    "${STUB_WORKER_NS:-default}"
}
"""

# No test reaches the user's real `~/.leerie` (or anything else under their
# home). The launcher resolves its state dir to `$HOME/.leerie/<repo>` and
# makes `logs/` there before any verb, so a test that ran it without
# `LEERIE_STATE_DIR` wrote into the user's run state (`test_re_seed`,
# `test_version_flag`: `~/.leerie/_self/logs`, `.owner`). The session gets
# its own HOME before any test module is imported, with the git identity CI
# sets globally (.github/workflows/test.yml); `REAL_HOME` is the user's, for
# a test that must read a cache there on purpose.
# The XDG base directories go with it: code under test derives its places
# from `${XDG_CACHE_HOME:-$HOME/.cache}` (leerie's private ssh-agent, the
# Fly image cache), so an inherited `XDG_CACHE_HOME` would put them back in
# the user's home, beyond the session's cleanup and its agent guard.
# Idempotent: a second import of this module (under another name) keeps the
# first one's HOME and its record of the user's.
XDG_BASE_DIRS = {"XDG_CACHE_HOME": ".cache", "XDG_CONFIG_HOME": ".config",
                 "XDG_DATA_HOME": ".local/share",
                 "XDG_STATE_HOME": ".local/state"}
if os.environ.get("PYTEST_LEERIE_TEST_HOME") and \
        os.environ.get("PYTEST_LEERIE_TEST_HOME") == os.environ.get("HOME"):
    REAL_HOME = os.environ.get("PYTEST_LEERIE_REAL_HOME", "")
else:
    REAL_HOME = os.environ.get("HOME", "")
    _TEST_HOME = Path(tempfile.mkdtemp(prefix="leerie-test-home-"))
    (_TEST_HOME / ".gitconfig").write_text(
        "[user]\n\temail = ci@leerie.invalid\n\tname = Leerie CI\n")
    os.environ["HOME"] = str(_TEST_HOME)
    for _var, _rel in XDG_BASE_DIRS.items():
        # The user's own value, so a test can be checked against it.
        os.environ[f"PYTEST_LEERIE_REAL_{_var}"] = os.environ.get(_var, "")
        os.environ[_var] = str(_TEST_HOME / _rel)
    os.environ["PYTEST_LEERIE_TEST_HOME"] = str(_TEST_HOME)
    os.environ["PYTEST_LEERIE_REAL_HOME"] = REAL_HOME
    atexit.register(shutil.rmtree, _TEST_HOME, True)


def _real_leerie_entries() -> set[str] | None:
    """The names in the user's real `~/.leerie` (read only, never written):
    the state roots of the user's own runs. None when there is no user's
    home to look at."""
    if not REAL_HOME:
        return None
    try:
        return set(os.listdir(Path(REAL_HOME) / ".leerie"))
    except FileNotFoundError:
        return set()
    except OSError:
        return None


# Taken at the first import, before any test runs.
_REAL_LEERIE_AT_START = _real_leerie_entries()


def _beneath(value: str, root: str) -> bool:
    return bool(root) and (
        value == root or value.startswith(root.rstrip("/") + "/"))


def _is_users_place(value: str) -> bool:
    """Whether `value` is the user's real home or beneath it — except the
    system temp directory, which may itself sit beneath the user's home
    (TMPDIR=~/.cache/...): the session HOME and every test's tmp_path are
    made there, and are the session's."""
    return _beneath(value, REAL_HOME) and not _beneath(
        value, os.path.realpath(tempfile.gettempdir())) and not _beneath(
        value, tempfile.gettempdir())


def _session_places_are_the_sessions() -> None:
    """HOME and the XDG base directories are never the user's: a test may
    point one at its own tmp_path (unsetting one is fine too: the code under
    test then falls back beneath the session HOME), never back at the
    user's home or at a value inherited from the user's environment."""
    assert not _is_users_place(os.environ.get("HOME", "")), "HOME"
    for var in XDG_BASE_DIRS:
        value = os.environ.get(var, "")
        if not value:
            continue
        assert not _is_users_place(value), var
        assert value != os.environ.get(f"PYTEST_LEERIE_REAL_{var}"), var


@pytest.fixture(autouse=True)
def _home_is_never_the_users():
    """A test that points HOME or an XDG base directory at the user's own
    (their home, or the value inherited from their environment) fails,
    before and after."""
    _session_places_are_the_sessions()
    yield
    _session_places_are_the_sessions()


def leerie_agent_dir() -> Path:
    """The directory `_leerie_fly_agent_ensure` (scripts/remote/lib.sh)
    puts leerie's private agent's socket in, derived as it derives it."""
    cache = os.environ.get("XDG_CACHE_HOME") or \
        os.path.join(os.environ["HOME"], ".cache")
    return Path(cache) / "leerie" / "agent"


def ssh_agents_under(directory: str | os.PathLike) -> list[int]:
    """The PIDs of every `ssh-agent` whose argv names a path under
    `directory`. leerie's private agent is `ssh-agent -a
    <cache>/leerie/agent/ssh-agent.sock`; it daemonizes, so the test that
    started it is not its parent and must stop it by PID (`SSH_AUTH_SOCK=…
    ssh-agent -k` stops nothing without `SSH_AGENT_PID`)."""
    prefix = os.fspath(directory).rstrip("/") + "/"
    pids = []
    if os.path.isdir("/proc"):
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as f:
                    argv = f.read().split(b"\0")
            except OSError:
                continue
            if os.path.basename(argv[0]) == b"ssh-agent" and any(
                    prefix.encode() in a for a in argv[1:]):
                pids.append(int(entry))
        return pids
    out = subprocess.run(["/bin/ps", "-A", "-o", "pid=", "-o", "command="],
                         capture_output=True, text=True, check=False).stdout
    for line in out.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        argv = cmd.split()
        if argv and os.path.basename(argv[0]) == "ssh-agent" and \
                prefix in cmd:
            pids.append(int(pid))
    return pids


def stop_ssh_agents_under(directory: str | os.PathLike) -> list[int]:
    """Stop, by PID, every agent `ssh_agents_under(directory)` finds; the
    PIDs it stopped."""
    pids = ssh_agents_under(directory)
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 15)
    return pids


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """No leerie ssh-agent the session started outlives the session, and
    the user's real ~/.leerie gains no entry.

    Code on the Fly path (`_leerie_fly_agent_ensure`) starts leerie's private
    agent in `leerie_agent_dir()`, under the session HOME; nothing stopped
    it, so every run leaked one agent per pytest process, on CI too. Only the
    controlling process checks: an xdist worker that finishes early shares
    the HOME with workers still running. A leak fails the session, and the
    agent is stopped either way, so a failing run leaks nothing."""
    if hasattr(session.config, "workerinput"):
        return
    if not os.environ.get("PYTEST_LEERIE_TEST_HOME"):
        return
    # No test writes the user's real ~/.leerie: a launcher run with the
    # user's HOME and no pinned state root made `<repo>/logs` there for every
    # repository name a test used. Names only; nothing there is removed.
    now = _real_leerie_entries()
    if _REAL_LEERIE_AT_START is not None and now is not None:
        gained = sorted(now - _REAL_LEERIE_AT_START)
        if gained:
            sys.stderr.write(
                f"\nconftest: {Path(REAL_HOME) / '.leerie'} gained {gained} "
                "during the session. A test that runs the launcher pins HOME "
                "or LEERIE_STATE_DIR beneath its own tmp_path; nothing was "
                "removed (if you made these yourself meanwhile, ignore "
                "this).\n")
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
    # A test may give the code an XDG_CACHE_HOME beneath its own tmp_path,
    # so an agent it starts lives under the session's base temp directory
    # (xdist workers' are beneath the controller's).
    agent_dir = leerie_agent_dir()
    leaked = stop_ssh_agents_under(agent_dir)
    factory = getattr(session.config, "_tmp_path_factory", None)
    if factory is not None:
        leaked += stop_ssh_agents_under(factory.getbasetemp())
    if leaked:
        sys.stderr.write(
            f"\nconftest: ssh-agent(s) {leaked} started in the session's "
            f"{agent_dir} outlived the session (stopped now). A test that "
            "starts an agent stops it by PID; a test whose subject is not "
            "the agent must not reach `_leerie_fly_agent_ensure`'s spawn.\n")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


def run_git_repo_first(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """`git -C <repo> <args>`, repo-first signature (`_git(repo, *args)`)."""
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True)


def run_git_cwd_kw(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """`git <args>` run in `cwd`, keyword-only signature (`_git(*args, cwd=)`)."""
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True)


def run_git_cwd_first_stdout(cwd: Path, *args: str) -> str:
    """`git <args>` run in `cwd`, cwd-first signature, returns stripped stdout."""
    out = subprocess.run(["git", *args], cwd=str(cwd), check=True,
                         capture_output=True, text=True)
    return out.stdout.strip()


def run_git_out(cwd: Path, *args: str, check: bool = True,
                env: dict[str, str] | None = None, strip: bool = True,
                errors: str | None = None,
                config: tuple[str, ...] = ()) -> str:
    """`git [-c <k=v> ...] <args>` run in `cwd`, returning its stdout
    (stripped unless `strip=False`). `check=False` returns the stdout of a
    failing command instead of raising; `env` replaces the environment;
    `errors` decodes the output as UTF-8 with that error handler (for git's
    raw byte paths); `config` is `-c` settings laid before `args`. The one
    git runner every test with a local `_git` aliases, so its variants live
    here once (`tests/test_no_duplicate_git_test_helpers.py`)."""
    cfg = [x for kv in config for x in ("-c", kv)]
    kw: dict = ({"encoding": "utf-8", "errors": errors} if errors
                else {"text": True})
    out = subprocess.run(["git", *cfg, *args], cwd=str(cwd), check=check,
                         env=env, capture_output=True, **kw).stdout
    return out.strip() if strip else out


def run_git_at(path: Path, *args: str, check: bool = True) -> str:
    """`git <args>` at `path`, as leerie names a repository: a bare one (the
    run's own) with `--git-dir`, a work tree with `-C`. Returns stripped
    stdout."""
    path = Path(path)
    at = (["--git-dir", str(path)]
          if (path / "HEAD").is_file() and (path / "objects").is_dir()
          else ["-C", str(path)])
    return subprocess.run(["git", *at, *args], check=check,
                          capture_output=True, text=True).stdout.strip()


def run_git_cwd_first_unchecked(
    cwd: Path, *args: str, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """`git <args>` run in `cwd`, cwd-first signature, unchecked (`check=False`)
    so a nonzero rc is returned rather than raised. Several call sites expect
    a nonzero rc (a refused `-d`, a missing branch) as a normal outcome."""
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=False, env=env)


def init_git_repo(path: Path) -> Path:
    """Create a minimal git repo at `path` with one commit on `main`."""
    path.mkdir(parents=True, exist_ok=True)
    run_git_repo_first(path, "init", "-q", "-b", "main")
    run_git_repo_first(path, "config", "user.email", "test@x")
    run_git_repo_first(path, "config", "user.name", "test")
    (path / "a.txt").write_text("a\n")
    run_git_repo_first(path, "add", ".")
    run_git_repo_first(path, "commit", "-q", "-m", "a")
    return path


def _run(coro):
    """Run an async coroutine synchronously (this repo does not use
    pytest-asyncio). Single owner for the byte-identical helper that was
    previously redefined in fifteen test files."""
    return asyncio.run(coro)


def run_bash(
    script: str,
    env: dict | None = None,
    *,
    cwd: Path | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess:
    """Run a bash script via `bash -c`, merging `env` onto the parent
    process environment. Single owner for the byte-identical `_run_bash`
    helper previously redefined across ~22 test files."""
    base_env = {k: v for k, v in os.environ.items()}
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        cwd=str(cwd) if cwd is not None else None,
        input=input,
        capture_output=True,
        text=True,
    )


try:
    import jsonschema  # type: ignore
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


def validate_or_fallback_required(schema: dict, instance: dict) -> bool:
    """Validate `instance` against `schema`, or fall back to a required-field
    check when `jsonschema` isn't installed.

    Single owner for the idiom duplicated across the schema-test files
    (e.g. tests/test_fit_judge_schema.py:22-30): when jsonschema is
    available, use it directly (raises on an invalid instance) and return
    True; otherwise assert every `schema["required"]` key is present in
    `instance` and return False, so the caller runs its own type-specific
    fallback assertions on top.
    """
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return True
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    return False


def transcript_path(log_dir: Path, name: str) -> Path:
    """Where `_invoke` writes worker transcript `name` of log directory
    `log_dir` (`logs/` or `logs/held-out/`): its transcripts directory,
    apart from leerie's command logs (DESIGN §8 *What a worker ran is read
    from its own transcript*). The directory is made."""
    d = Path(log_dir) / "transcripts"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def register_worker_transcript(log_path: Path) -> Path:
    """Register a hand-written fixture log as a worker's transcript, the
    line `_invoke` appends when it spawns one (`TRANSCRIPTS_REGISTRY`): the
    readers of what a worker ran take registered transcripts only (DESIGN §8
    *What a worker ran is read from its own transcript*). A fixture meant
    to be found by a reader is written at `transcript_path`."""
    with (log_path.parent / "transcripts.ndjson").open("a") as f:
        f.write(json.dumps({"sid": log_path.stem, "log": log_path.name})
                + "\n")
    return log_path


@pytest.fixture(scope="session")
def leerie():
    """The leerie module loaded from orchestrator/leerie.py."""
    spec = importlib.util.spec_from_file_location("leerie", LEERIE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The line breaks `ast` counts (`ast._splitlines_no_ff`): \r\n, \r and \n
# only, so a form feed or U+2028 inside a line does not shift line numbers
# the way `str.splitlines` would. A fixed format, matched mechanically.
_AST_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")


def source_segments(source: str) -> Callable[[ast.AST], str | None]:
    """`ast.get_source_segment(source, node)`, with `source` split ONCE.

    `ast.get_source_segment` re-splits the whole source on every call, and
    on Python 3.10 and 3.11 that split is a pure-Python loop over every
    character. Over leerie.py (about 1,700 functions in 3.5 MB) a loop of
    calls cost about 26 minutes on 3.11 and more on 3.10, at COLLECTION
    (tests/test_needs_human_sources_render.py derives its parameters at
    import): the CI legs sat that long before their first test. Same result
    for every node (tests/test_source_segments.py); the only one tests use.
    """
    lines = _AST_LINE.findall(source)

    def segment(node: ast.AST) -> str | None:
        try:
            a, b = node.lineno, node.end_lineno
            start, end = node.col_offset, node.end_col_offset
        except AttributeError:
            return None
        if b is None or end is None:
            return None
        a, b = a - 1, b - 1
        if a == b:
            return lines[a].encode()[start:end].decode()
        first = lines[a].encode()[start:].decode()
        last = lines[b].encode()[:end].decode()
        return "".join([first, *lines[a + 1:b], last])

    return segment


def _has_treesitter() -> bool:
    """True only if the installed tree-sitter stack can actually extract a
    symbol. Delegates to leerie's own `_tree_sitter_extraction_works()`
    functional probe (mere importability is insufficient — an
    installed-but-incompatible language-pack version imports fine yet extracts
    nothing). Shared here so extraction-dependent repo-map test modules gate
    on it without duplication."""
    spec = importlib.util.spec_from_file_location("_leerie_ts_probe", LEERIE_PY)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod._tree_sitter_extraction_works()
    except Exception:
        return False


# Evaluated once at collection; extraction-dependent repo-map test modules
# import this to skip cleanly on hosts without a working parser.
HAS_TREESITTER = _has_treesitter()


# Evaluated once at collection; test modules that exercise *host-side* bash
# import this to skip cleanly where `jq` is absent.
#
# Why a gate rather than adding jq to the image: the host/container split is
# deliberate. Host bash uses `jq` — the launcher hard-fails at preflight
# without it (`leerie`'s "jq not found on PATH" check, which tells you to
# `brew install jq`) — while code that runs *inside* the container uses
# python3, exactly as `scripts/remote/seed-auth.sh` documents: "python3 over
# jq because jq isn't in the leerie image (see Dockerfile)".
#
# The gated modules source scripts the host owns (`host-finalize.sh`,
# `provision.sh`'s `decide_teardown`, the launcher's finalize path) and stub
# `git`/`gh` on PATH but not `jq`, so they silently inherit it from whichever
# machine runs pytest. They pass on a dev host and in CI (both ship jq) and
# fail only inside `leerie:<version>` — where the scripts under test could
# never succeed anyway, since gh auth, ssh-agent, and Keychain are all
# host-side (DESIGN §6 *Finalization*).
#
# Do NOT "fix" a skip here by installing jq into the image: that buys a green
# tick, not working code, and erodes the boundary.
HAS_JQ = shutil.which("jq") is not None


def _landlock_abi() -> int:
    """The host kernel's Landlock ABI, 0 where it has none (not Linux, the
    LSM off, a seccomp profile refusing the syscall). The shim's own `abi()`,
    loaded by path so this needs no package import."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "lk_conftest", REPO_ROOT / "scripts" / "landlock_exec.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return int(m.abi())


# Evaluated once at collection; test modules whose SUBJECT is the kernel's
# confinement import this.
#
# Why a gate and not an emulation: Landlock is a Linux LSM with no macOS
# equivalent (`sandbox-exec` is deprecated by Apple and its profiles do not
# express leerie's grants), and faking it away would make a confinement test
# pass on a kernel that cannot confine — a green tick over the one guarantee
# the shim exists to provide. leerie runs in a Linux container, so these
# tests' subject is always on Linux in production; CI (ubuntu-latest) has
# ABI >= 3 and runs every one of them for real.
#
# Where the host has no Landlock, the test runs where leerie runs: with
# LEERIE_TEST_CONTAINER_IMAGE naming a leerie image and `nerdctl` on PATH,
# `_landlock_container_runner` below re-runs it inside the container, which
# has the real LSM (a macOS Colima VM reports ABI 4). Gated on the variable
# as `tests/test_pinned_runtime_runs.py` and `tests/test_work_read_only.py`
# gate theirs, so the reason never names a remedy that does nothing.
LANDLOCK_ABI = _landlock_abi()
MIN_LANDLOCK_ABI = 3
LANDLOCK_IMAGE = os.environ.get("LEERIE_TEST_CONTAINER_IMAGE", "")
# Under CI's per-test cap (`--timeout=600`, .github/workflows/test.yml), and
# matching `tests/test_work_read_only.py`'s own container test. Measured need
# is about 10 s a test, so this is 30x headroom: a longer ceiling than the cap
# it runs under would be killed by pytest-timeout mid-`nerdctl`, and `--rm`
# does not run when the client is SIGKILLed.
LANDLOCK_CONTAINER_TIMEOUT_SEC = 300


def _landlock_users_env() -> dict[str, str]:
    """The user's own HOME and XDG directories: rootless nerdctl finds its
    network configuration there, and the session's test home has none. Same
    reason as `tests/test_pinned_runtime_runs.py`'s `_users_env`."""
    env = {**os.environ, "HOME": REAL_HOME}
    for var in XDG_BASE_DIRS:
        real = os.environ.get(f"PYTEST_LEERIE_REAL_{var}", "")
        if real:
            env[var] = real
        else:
            env.pop(var, None)
    # The child pytest must not recurse into this hook.
    env.pop("LEERIE_TEST_CONTAINER_IMAGE", None)
    return env

def _container_runtime_usable() -> bool:
    """True when `nerdctl` is there AND its daemon answers.

    `shutil.which` alone is not enough: with Colima installed but stopped --
    an ordinary state on a laptop -- every `nerdctl run` exits fatal on the
    containerd socket, which would turn each gated test into a failure whose
    text says nothing about its subject. A test that cannot be run here must
    skip, so the runtime is probed once.
    """
    if not shutil.which("nerdctl"):
        return False
    try:
        # `_landlock_users_env`, not this process's: conftest has already
        # pointed HOME at a test directory, where rootless nerdctl finds no
        # configuration and reports "colima is not running" on a host where
        # it is running. The runner itself uses the same environment.
        return subprocess.run(["nerdctl", "version"], capture_output=True,
                              timeout=60,
                              env=_landlock_users_env()).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# Public: a module with its own kernel precondition (a `/proc` read, say)
# composes its skipif with this, so its reason stays true too. The probe runs
# only where an image is configured, so it costs a host without one nothing
# (and CI, which sets no image, never pays it).
CAN_RUN_IN_CONTAINER = bool(LANDLOCK_IMAGE) and _container_runtime_usable()
# TWO marks, applied as a list -- never `pytest.mark.needs_landlock(skipif)`,
# which makes the skipif an ARGUMENT of the outer mark instead of a mark of
# its own, so nothing skips and every confined test runs against a kernel
# that cannot confine (measured: 15 such failures).
#   - `needs_landlock` is the name `pytest_collection_modifyitems` looks for;
#     a bare skipif mark is named "skipif", so a name lookup finds nothing.
#   - the skipif stops a test this host can neither run nor hand to a
#     container.
# A list is what pytest applies as a stack of marks, for both a decorator
# (`@needs_landlock`) and a module `pytestmark`.
needs_landlock = pytest.mark.needs_landlock
skip_without_landlock = pytest.mark.skipif(
    LANDLOCK_ABI < MIN_LANDLOCK_ABI and not CAN_RUN_IN_CONTAINER,
    reason=f"the host kernel has no Landlock ABI {MIN_LANDLOCK_ABI} "
           f"(it reports {LANDLOCK_ABI}) and this test's subject is the "
           "kernel's confinement; leerie runs in a Linux container, so "
           "set LEERIE_TEST_CONTAINER_IMAGE to a leerie image and start "
           "the container runtime (`nerdctl version` must answer) and it "
           "runs there instead. CI runs it directly.")


def _landlock_container_argv(node_id: str) -> list[str]:
    """`nerdctl run` argv that runs one test, by node id, inside a leerie
    image. The repo is copied in, never used in place: the suite writes.

    The security options mirror `tests/test_pinned_runtime_runs.py`'s real
    container test — rootless containerd needs `seccomp=unconfined` for the
    shim's own seccomp filter, and the sandbox's mounts need the
    `leerie-container` AppArmor profile where it is loaded.
    """
    rootless = Path(f"/run/user/{os.getuid()}/containerd-rootless/child_pid")
    sec = (["--security-opt", "seccomp=unconfined"]
           if rootless.is_file() else [])
    if Path("/etc/apparmor.d/leerie-container").is_file():
        sec += ["--security-opt", "apparmor=leerie-container"]
    # pytest is not in the image (it is a runtime image, not a test one), so
    # it is installed per run: ~5 s, measured. `safe.directory` is needed
    # because the copy is not owned by the user git sees.
    script = (
        "set -e\n"
        "cp -a /src /w\n"
        "cd /w\n"
        # Not `>/dev/null 2>&1`: `--quiet` already drops the normal chatter,
        # and under `set -e` a swallowed failure (no egress, no index --
        # jsonschema is in neither requirements.txt nor the image) left the
        # operator a verdict with an empty body.
        "/usr/bin/python3 -m pip install --quiet --break-system-packages "
        "  --root-user-action=ignore pytest jsonschema\n"
        "git config --global --add safe.directory /w\n"
        f"exec /usr/bin/python3 -m pytest -q -p no:cacheprovider {node_id!r}\n")
    return ["nerdctl", "run", "--rm", *sec, "--entrypoint", "bash",
            "-v", f"{REPO_ROOT}:/src:ro", LANDLOCK_IMAGE, "-c", script]




def _runs_in_container(item) -> bool:
    """This test's verdict comes from the container, not from this kernel."""
    return (LANDLOCK_ABI < MIN_LANDLOCK_ABI and CAN_RUN_IN_CONTAINER
            and item.get_closest_marker("needs_landlock") is not None)


def pytest_collection_modifyitems(config, items):
    """Hand every `needs_landlock` test to the container, where this kernel
    has no Landlock and an image is configured.

    The item's own `setup` is replaced, so none of its fixtures is built on
    a kernel that cannot confine: `tests/test_commands_keep_default_signals`'s
    module-scoped `outside` reads `/proc/self/status` and raised
    StopIteration at setup, long before any body ran. The container's verdict
    becomes the test's. Inside the container the ABI is >= 3 and
    LEERIE_TEST_CONTAINER_IMAGE is cleared from the child's environment, so
    the marker is inert there and the re-run executes the body normally —
    there is no recursion.
    """
    for item in items:
        if _runs_in_container(item):
            item.setup = _landlock_container_setup(item)      # type: ignore[method-assign]


def _landlock_container_setup(item):
    """`item.setup` that runs the test in the container instead.

    The verdict is only recorded here, never raised: a `pytest.fail` at setup
    reports as an ERROR, which reads as "the harness broke" and which `-ra`
    groups away from real failures. `pytest_runtest_call` raises it in the
    call phase instead, where a failure is a failure.
    """
    def setup() -> None:
        r = subprocess.run(_landlock_container_argv(item.nodeid),
                           capture_output=True, text=True,
                           timeout=LANDLOCK_CONTAINER_TIMEOUT_SEC,
                           env=_landlock_users_env())
        item._landlock_container = (
            r.returncode,
            f"in {LANDLOCK_IMAGE} (this host has no Landlock ABI "
            f"{MIN_LANDLOCK_ABI}):\n{r.stdout[-4000:]}{r.stderr[-4000:]}")
    return setup


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    """The container already decided this test; run no local body."""
    got = getattr(item, "_landlock_container", None)
    if got is None:
        return
    rc, said = got
    if rc != 0:
        pytest.fail(said, pytrace=False)
    raise _RanInContainer("the container passed this test")


class _RanInContainer(_pytest_outcomes.Skipped):
    """Carries "the container passed this" out of the call phase, where
    `pytest_runtest_makereport` turns it into the pass it was.

    A `Skipped` subclass, not a plain Exception: `check_interactive_exception`
    exempts `Skipped` by name, so `--pdb` does not open a debugger on a test
    that PASSED in the container. Everything else about the outcome is set by
    the report hook below.
    """


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """A test the container passed reports as passed here."""
    out = yield
    rep = out.get_result()
    exc = call.excinfo.value if call.excinfo else None
    if call.when == "call" and isinstance(exc, _RanInContainer):
        rep.outcome = "passed"
        rep.longrepr = None


def _make_caps(leerie, **overrides) -> dict:
    """Shared DEFAULT_CAPS-derived caps builder for the recursive-decompose
    test family (§5½ (P1)). Callers layer on extra keys (e.g.
    `subfile_split_max_span`) via **overrides rather than every file forcing
    an identical cap set."""
    caps = {
        "max_total_workers": 200,
        "decompose_max_depth": leerie.DEFAULT_CAPS["decompose_max_depth"],
        "decompose_fit_threshold": leerie.DEFAULT_CAPS["decompose_fit_threshold"],
        "decompose_noprogress_rounds": leerie.DEFAULT_CAPS["decompose_noprogress_rounds"],
    }
    caps.update(overrides)
    return caps


def _fit_response(score: float) -> dict:
    """Shared stubbed fit_judge worker response for the recursive-decompose
    test family (§5½ (P1))."""
    return {
        "score": score,
        "rationale": f"score={score}",
        "diffuse": "" if score >= 0.70 else "too broad",
        "confidence": {
            "fit": 8.5, "basis": "test",
            "falsifiers_tested": ["x"], "contradictions_reconciled": [],
            "gap_to_close": {},
        },
    }


def _split_response(parent_id: str, n: int) -> dict:
    """Shared stubbed splitter worker response for the recursive-decompose
    test family (§5½ (P1))."""
    return {
        "children": [
            {
                "id": f"{parent_id}-{i + 1}",
                "title": f"child {i + 1}",
                "success_criteria_seed": f"criterion {i + 1}",
                "files_likely_touched": [f"f{i}.py"],
            }
            for i in range(n)
        ],
    }


def fake_claude_on_path(tmp_path: Path, monkeypatch) -> Path:
    """Put a stub `claude` binary on PATH and return it.

    `main()` gates on `shutil.which("claude")` and `die()`s (exit 1) long
    before anything a `main()`-driving test asserts on -- 87 lines before
    `State(...)` mints the run dir, and well before the top-level
    try/except. The binary is on a developer's PATH and NOT on the CI
    runner's, so a suite that drives the real `main()` without this passes
    locally and fails on CI with every expected exit code collapsed to
    `die()`'s 1. That is the trap CLAUDE.md records ("A local pass is not
    evidence until the host lacks `claude`"), and it is how 14 tests in
    `test_main_exception_arms.py` shipped red.

    Single owner on purpose: this lived in `test_main_cli_wiring.py` while
    `test_main_exception_arms.py` -- the same harness minus this one call --
    had no copy at all. Anything driving the real `main()` imports it from
    here rather than reproducing it.
    """
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "claude"
    stub.write_text("#!/bin/sh\necho '{}'\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return stub


# prctl option numbers, mirrored from leerie's own `_PR_SET_CHILD_SUBREAPER` /
# `_PR_GET_CHILD_SUBREAPER`. Duplicated as literals rather than imported
# because this fixture is autouse and must not force the session-scoped
# `leerie` module load onto every test in the suite;
# `tests/test_subreaper.py` carries a coupling guard that the two agree.
_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37


@contextlib.contextmanager
def child_subreaper_restored():
    """Restore this process's `PR_SET_CHILD_SUBREAPER` flag on exit.

    The mechanism behind the autouse fixture below, exposed as a plain
    context manager so a test can drive it without reaching into pytest's
    private fixture internals (which differ across pytest versions).

    Linux-only (`prctl` is a Linux syscall); a no-op everywhere else, and a
    no-op if the flag is unreadable or comes back unchanged.
    """
    if not sys.platform.startswith("linux"):
        yield
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        before = ctypes.c_int(0)
        if libc.prctl(_PR_GET_CHILD_SUBREAPER, ctypes.byref(before),
                      0, 0, 0) != 0:
            yield
            return
    except OSError:
        yield
        return
    try:
        yield
    finally:
        after = ctypes.c_int(0)
        if (libc.prctl(_PR_GET_CHILD_SUBREAPER, ctypes.byref(after),
                       0, 0, 0) == 0
                and after.value != before.value):
            libc.prctl(_PR_SET_CHILD_SUBREAPER, before.value, 0, 0, 0)


@pytest.fixture(autouse=True)
def _default_aws_region(monkeypatch):
    """EC2 preflight (`require_aws`) dies without a region, and a dev host
    may have none configured; subprocess tests inherit os.environ. Tests
    that assert the missing-region error set their own env explicitly."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")


@pytest.fixture(autouse=True)
def _ec2_tests_isolated_home(request, tmp_path_factory, monkeypatch):
    """EC2 tests get a throwaway HOME. `run_bash` merges os.environ, and
    ec2_default_key_file writes leerie's key under $HOME/.leerie/.ec2-keys:
    any test that leaves LEERIE_EC2_KEY_NAME blank would otherwise drop a
    stub key into the developer's real home (it did, once)."""
    if "ec2" in request.module.__name__:
        monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))


@pytest.fixture(autouse=True)
def _no_iam_propagation_wait(monkeypatch):
    """`provision_instance` sleeps between run-instances retries while a new
    IAM profile propagates; a test that exercises the retry would otherwise
    spend real seconds per attempt, and the sleeps add up across the suite."""
    monkeypatch.setenv("LEERIE_EC2_IAM_RETRY_S", "0")


def measure_run_dir(tmp_path: Path, *, beside: bool = False) -> Path:
    """A run directory for a test double of the state whose repository
    commands leerie runs. Every such command is confined to its measurement
    grant (DESIGN §6 *Leerie's runs of the repository's commands are
    confined too*), which lives in the run directory; its state root,
    `<tmp_path>/state`, holds nothing else, so the test's own files beside
    it (a tree, a fake binary) stay readable and the tree stays outside.
    `beside`: for a test whose tree is `tmp_path` itself, the run directory
    goes beside it rather than inside it."""
    base = (tmp_path.parent / f"{tmp_path.name}-run") if beside else tmp_path
    d = base / "state" / "runs" / "r1"
    d.mkdir(parents=True, exist_ok=True)
    return d


def measured_tmp_files(run_dir: Path, name: str) -> list[Path]:
    """Every `<private temp dir>/<name>` of the run's measured trees: where
    a confined command's `$TMPDIR` put a file a test reads back (a run
    counter, a marker). Each tree's private temp directory (`/tmp/l/<id>/`)
    is named in its measurement directory's sidecar."""
    out: list[Path] = []
    for side in (Path(run_dir) / "measure").glob("*.tree"):
        try:
            tmp = json.loads(side.read_text()).get("tmp")
        except (OSError, ValueError):
            continue
        if tmp:
            out += Path(tmp).glob(name)
    return sorted(out)


_FAKE_RUN_DIR: Path | None = None


def fake_run_dir() -> Path:
    """This test's own run directory, for a test double of the state that
    has no real one. `claude_p` makes per-worker directories and a
    configuration template in the run directory (DESIGN §6 *Each worker's
    own configuration and temporary directories*), so one fixed path shared
    by every test (and every suite on the host) would carry one test's
    template and workers into the next."""
    assert _FAKE_RUN_DIR is not None
    return _FAKE_RUN_DIR


@pytest.fixture(autouse=True)
def _own_temp_dir_restored():
    """Put the process's temp directory back after a test that ran
    `_run_phases`, which makes the run directory's `tmp/` leerie's own
    (`_use_run_tmp`: `tempfile.tempdir` and `TMPDIR`)."""
    saved, env = tempfile.tempdir, os.environ.get("TMPDIR")
    yield
    tempfile.tempdir = saved
    if env is None:
        os.environ.pop("TMPDIR", None)
    else:
        os.environ["TMPDIR"] = env


@pytest.fixture(autouse=True)
def _fresh_fake_run_dir(tmp_path_factory):
    global _FAKE_RUN_DIR
    _FAKE_RUN_DIR = tmp_path_factory.mktemp("fake-run")
    yield
    _FAKE_RUN_DIR = None


@pytest.fixture(autouse=True)
def _no_operator_claude_config(tmp_path_factory, monkeypatch):
    """Point the operator's CLI configuration at an empty directory: every
    `claude_p` snapshots it into the run's template (DESIGN §6 *Each
    worker's own configuration and temporary directories*), and the real
    `~/.claude` holds the developer's credentials and tens of MB of skills
    and plugins, which would otherwise land in every test's run directory
    under /tmp. A test of that snapshot sets its own."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR",
                       str(tmp_path_factory.mktemp("operator-claude")))


@pytest.fixture(autouse=True)
def _fresh_head_acceptance_results(leerie, monkeypatch):
    """Give every test an empty `_HEAD_ACCEPTANCE_RESULTS`: the `leerie`
    module is session-scoped, so a measurement cached by one test would
    otherwise be readable by the next one that reuses its (run dir, sha)
    key."""
    monkeypatch.setattr(leerie, "_HEAD_ACCEPTANCE_RESULTS", {})


@pytest.fixture(autouse=True)
def _fresh_cgroup_probe(leerie, monkeypatch):
    """Give every test an unprobed broker: `_cgroup_probe` memoizes into
    module globals, and the base-health baseline's peak measurement now
    probes (DESIGN §6 *The repo's own build peak*) — on a host with no
    broker one test's `False` would otherwise answer every later test's
    probe."""
    monkeypatch.setattr(leerie, "_CGROUP_PROBE_RESULT", None)
    monkeypatch.setattr(leerie, "_CGROUP_HIERARCHY", None)


@pytest.fixture(autouse=True)
def _no_real_planning_worktree(request, monkeypatch):
    """Keep `_ensure_planning_worktree` from shelling out to real git.

    It runs `scripts/planning-worktree.sh`, which does a real
    `git worktree add` rooted at `resolve_leerie_root()`. With
    `LEERIE_STATE_DIR` unset -- the default for a direct Python caller -- that
    resolves to `<repo>/.leerie`, so any test driving the real `_run_phases`
    created a FULL CHECKOUT OF THIS REPO inside this repo (registered, since
    per-run repositories, in that run's `<run-dir>/repo`; before them, in
    this repo's own `.git/worktrees/`). The run's repository itself is
    `_no_run_repo_without_a_git_repo`'s concern, below.

    The failure mode is silent in three ways at once, which is why this is a
    conftest-level guard rather than a stub in each driving test: `.leerie/*`
    is gitignored so `git status` stays clean; the directories survive the
    session; and the damage surfaces in an unrelated file, because a nested
    `.leerie/.../worktrees/planning/tests/...` copy does not match the
    `rel.startswith("tests/")` exclusion that scanners like
    `test_helper_naming_convention` use. Measured: two worktrees, 25 MB, and
    one red test with no visible connection to the change that caused it.

    Opt out with `@pytest.mark.real_planning_worktree` when the worktree
    mechanics are themselves the subject.
    """
    if request.node.get_closest_marker("real_planning_worktree"):
        return
    leerie = request.getfixturevalue("leerie")

    async def _stub(st):
        run_dir = Path(getattr(st, "run_dir", "."))
        path = str(run_dir / "worktrees"
                   / leerie._worktree_dirname(run_dir.name, "planning"))
        try:
            st.data["planning_worktree"] = path
            st.save()
        except Exception:
            pass
        return path

    monkeypatch.setattr(leerie, "_ensure_planning_worktree", _stub)


@pytest.fixture(autouse=True)
def _test_tmp_readable_by_measurements(request, monkeypatch):
    """Let a measured command read the test's own `tmp_path`, outside its
    state root. pytest's `tmp_path` is under the host's `/tmp`, which no
    grant names — an isolated command's `/tmp` is its own (DESIGN §6 *Every
    confined process has its own `/tmp`, `/dev/shm` and IPC*) — so a fake
    runner or installer a test puts there could not even be executed. In
    production no tool lives in `/tmp`. Never the state root or the host
    directory (`_apart`), never the rest of `/tmp`."""
    if "tmp_path" not in request.fixturenames:
        return
    leerie = request.getfixturevalue("leerie")
    tmp = request.getfixturevalue("tmp_path")
    real = leerie._measurement_grants

    def grants(st, tree, kind=leerie.MEASURE_KIND):
        g = real(st, tree, kind)
        root = leerie._run_dir_of(st).parent.parent
        extra = tuple(p for p in leerie._apart([str(tmp)], root)
                      if p not in g.rw + g.ro)
        return g._replace(ro=g.ro + extra)
    monkeypatch.setattr(leerie, "_measurement_grants", grants)


HELDOUT_SANDBOX_EMULATOR = REPO_ROOT / "tests" / "heldout_sandbox_emulator.py"


@pytest.fixture(autouse=True)
def _no_image_worker_root(request, monkeypatch):
    """Point `WORKER_ROOT` at a path that does not exist, so the suite runs
    the same in leerie's base image and in a repository image, which has a
    worker root at `/opt/leerie-worker-root` (DESIGN §6 *The orchestrator's
    root is leerie's; a repository's root is only a confined process's*).
    `tests/test_worker_root_view.py` drives the shim's `--worker-root`
    itself; a test that needs the image's own opts out with
    `@pytest.mark.real_worker_root`."""
    if request.node.get_closest_marker("real_worker_root"):
        return
    leerie = request.getfixturevalue("leerie")
    monkeypatch.setattr(leerie, "WORKER_ROOT",
                        REPO_ROOT / "tests" / "no-worker-root-in-tests")


@pytest.fixture(autouse=True)
def _emulated_heldout_sandbox(request, monkeypatch):
    """Run every held-out sandbox a test asks for through
    `tests/heldout_sandbox_emulator.py`. Leerie runs a held-out file only in
    a sandbox (`_confined_argv(…, sandbox=)` always emits `--sandbox`), which
    needs an unprivileged user namespace with mounts — refused on a
    developer host and CI (Ubuntu's
    `kernel.apparmor_restrict_unprivileged_userns`), where every real
    held-out run would exit 126. The emulator keeps the sandbox's contract
    with leerie (files placed for the run and gone after it, the report
    back through its descriptor) without its isolation; a test whose
    subject is the isolation opts out with `@pytest.mark.real_heldout_sandbox`
    and skips where `isolation_error()` reports the host cannot build one."""
    if request.node.get_closest_marker("real_heldout_sandbox"):
        return
    leerie = request.getfixturevalue("leerie")
    real = leerie._confined_argv

    def _argv(grants, cmd, *, sandbox=None, report_fd=None, taken_fd=None,
              **kw):
        if sandbox is None:
            return real(grants, cmd, **kw)
        out = [sys.executable, "-I", str(HELDOUT_SANDBOX_EMULATOR),
               "--view", sandbox.view]
        for src, rel in sandbox.place:
            out += ["--place", f"{src}={rel}"]
        if taken_fd is not None:
            out += ["--taken-fd", str(taken_fd)]
        if sandbox.report is not None and report_fd is not None:
            out += ["--report", sandbox.report, "--report-fd",
                    str(report_fd)]
        return out + ["--", *real(grants, cmd)]
    monkeypatch.setattr(leerie, "_confined_argv", _argv)


NO_REAL_HELDOUT_SANDBOX_SITE = REPO_ROOT / "tests" / "no_real_heldout_sandbox_site"


@pytest.fixture(autouse=True)
def _no_real_heldout_sandbox(request, monkeypatch):
    """Keep the entry's `_require_heldout_sandbox` (`_require_confinement`)
    from building a real sandbox. It needs an unprivileged user namespace
    with mounts, which a developer host and CI restrict (Ubuntu's
    `kernel.apparmor_restrict_unprivileged_userns`): every test driving real
    `main()` would die there. The stub also leaves `ISOLATE_ENV` unset,
    so nothing a test spawns is isolated. A Python child a test starts gets
    the same stub through `tests/no_real_heldout_sandbox_site/` put first on
    `PYTHONPATH` (its `sitecustomize`): a test that drives the real `main()`
    in a process of its own would otherwise die at `_require_confinement`
    on that host before reaching its subject. A test whose subject is the
    gate opts out with `@pytest.mark.real_heldout_sandbox` (and skips where
    `isolation_error()` reports the host cannot build one)."""
    # The real gate sets the variable in `os.environ` itself, which
    # monkeypatch would not undo: cleared before and after every test.
    os.environ.pop("LEERIE_ISOLATED", None)
    if not request.node.get_closest_marker("real_heldout_sandbox"):
        # Every loaded copy of the orchestrator: the session fixture's, and
        # one a test imports itself (`import leerie` off sys.path, or its own
        # spec), so a test driving `preflight()` through any of them is
        # covered.
        mods = [request.getfixturevalue("leerie")] + [
            m for m in list(sys.modules.values())
            if getattr(m, "__file__", None)
            and os.path.realpath(m.__file__) == os.path.realpath(LEERIE_PY)]
        for mod in {id(m): m for m in mods}.values():
            if hasattr(mod, "_require_heldout_sandbox"):
                monkeypatch.setattr(mod, "_require_heldout_sandbox",
                                    lambda run_dir: None)
        prior = os.environ.get("PYTHONPATH")
        monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
            [str(NO_REAL_HELDOUT_SANDBOX_SITE)] + ([prior] if prior else [])))
    yield
    os.environ.pop("LEERIE_ISOLATED", None)


_WORKER_EXIT_MODULE = None


def worker_exit(tree) -> None:
    """What a worker's exit does in production, for a test that stands in
    for a worker by committing in its tree itself: leerie takes the tree's
    branch from its tree repository into the run repository
    (`_invoke_admitted` → `_take_after_worker`; DESIGN §6 *A repository per
    committing tree (I6)*). A no-op for a tree of no tree repository. The
    real function, from the module loaded once for helpers."""
    global _WORKER_EXIT_MODULE
    if _WORKER_EXIT_MODULE is None:
        spec = importlib.util.spec_from_file_location("_leerie_worker_exit",
                                                      LEERIE_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _WORKER_EXIT_MODULE = mod
    _WORKER_EXIT_MODULE._take_tree_branch(tree)


def relocate_into_run_repo(leerie, st) -> Path:
    """Turn a hand-built pre-change layout into the run's own repository
    (DESIGN §6 *The run's own repository*): for a test that committed the
    run's branches in its "user" repository (`st.repo_root`) and checked
    staging out there, make `<run-dir>/repo` (`_ensure_run_repo`), move
    every `leerie/*` branch into it (deleted from the user's repository),
    re-create every worktree of the user's repository that lies under the
    run directory as a worktree of the run's repository on the same branch
    or commit, and record `base_sha` — the cut point, the merge-base of the
    run branch and the working branch — when the state names a working
    branch and has none. Afterwards the user's repository holds no leerie
    ref or worktree, as in a real run. Returns the run's repository."""
    user = Path(st.repo_root)
    run_dir = Path(st.run_dir).resolve()

    def git(cwd, *a, check=True):
        return subprocess.run(["git", "-C", str(cwd), *a], check=check,
                              capture_output=True, text=True).stdout.strip()
    trees = []
    cur: dict = {}
    for line in git(user, "worktree", "list", "--porcelain").splitlines() + [""]:
        if not line:
            if cur.get("path") and Path(cur["path"]).resolve().is_relative_to(
                    run_dir):
                trees.append(cur)
            cur = {}
        elif line.startswith("worktree "):
            cur["path"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch "):]
    for t in trees:
        git(user, "worktree", "remove", "--force", t["path"])
    repo = leerie._ensure_run_repo(st)
    refs = git(user, "for-each-ref", "--format=%(refname)",
               "refs/heads/leerie").split()
    if refs:
        git(repo, "fetch", "-q", str(user), *[f"+{r}:{r}" for r in refs])
        for r in refs:
            git(user, "update-ref", "-d", r)
    for t in trees:
        if t.get("branch"):
            git(repo, "worktree", "add", "-q", t["path"],
                t["branch"][len("refs/heads/"):])
        else:
            git(repo, "worktree", "add", "-q", "--detach", t["path"],
                t["head"])
    wb = st.data.get("working_branch")
    run_branch = f"refs/heads/leerie/runs/{st.run_id}"
    if wb and not st.data.get("base_sha") and git(
            repo, "rev-parse", "-q", "--verify", run_branch, check=False):
        st.data["base_sha"] = git(repo, "merge-base", run_branch,
                                  f"refs/heads/{wb}")
        st.save()
    return repo


@pytest.fixture(autouse=True)
def _no_run_repo_without_a_git_repo(request, monkeypatch):
    """Let a test drive `_run_phases` / `phase_execute` / the planning
    worktree over a state whose `repo_root` is no git repository.

    Every run makes its own repository from the user's at start
    (`_ensure_run_repo`, DESIGN §6 *The run's own repository*), and dies
    when it cannot — `_preflight_repo` guarantees a repository in
    production. Many tests drive those phases with the scripts and git
    stubbed, over a temporary directory or a `Mock` state; for them the
    creation and the refresh are no-ops here. A test whose `repo_root` is a
    real repository gets the real thing, unstubbed."""
    leerie = request.getfixturevalue("leerie")
    real_ensure = leerie._ensure_run_repo_or_die
    real_refresh = leerie._refresh_run_repo_base_or_die

    def _has_repo(st) -> bool:
        root = getattr(st, "repo_root", None)
        run_dir = getattr(st, "run_dir", None)
        if not isinstance(root, (str, os.PathLike)) or not isinstance(
                run_dir, (str, os.PathLike)):
            return False
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-dir"],
            capture_output=True).returncode == 0

    async def ensure(st):
        if _has_repo(st):
            # A real run repository is a full copy of the user's branch
            # (measured on this repository: 31 MB, ~11 s). Under this
            # checkout — the default state root `<repo>/.leerie` when
            # LEERIE_STATE_DIR is unset — it would outlive the session,
            # gitignored and unseen, the planning worktree's old leak
            # (above). Fail the test that would make it, by name.
            run_dir = Path(str(st.run_dir)).resolve()
            if run_dir.is_relative_to(REPO_ROOT):
                pytest.fail(
                    f"this test drives the real run repository under this "
                    f"checkout ({run_dir}): pin the state root to tmp_path "
                    "(State(tmp_path / ..., ...) or LEERIE_STATE_DIR)")
            return await real_ensure(st)
        return Path(str(getattr(st, "run_dir", "."))) / "repo"

    async def refresh(st, where):
        if _has_repo(st):
            return await real_refresh(st, where)
        return ""
    monkeypatch.setattr(leerie, "_ensure_run_repo_or_die", ensure)
    monkeypatch.setattr(leerie, "_refresh_run_repo_base_or_die", refresh)


@pytest.fixture(autouse=True)
def _restore_git_hooks_env():
    """Put the git config environment back after every test.

    `main()` turns leerie's hooks off by appending `core.hooksPath` to
    `os.environ`'s `GIT_CONFIG_*` entries (`_git_hooks_off`, DESIGN §6½
    *Hooks off*). A test that drives the real `main()` would leave that on
    the pytest process, and every later test's git would then run no hook
    — silently, depending on collection order (the same shape as the
    subreaper flag below)."""
    keys = lambda: {k for k in os.environ  # noqa: E731
                    if k.startswith(("GIT_CONFIG_", "LEERIE_GIT_HOOKS"))}
    before = {k: os.environ[k] for k in keys()}
    yield
    for k in keys() - set(before):
        os.environ.pop(k, None)
    os.environ.update(before)


@pytest.fixture(autouse=True)
def _restore_child_subreaper():
    """Put `PR_SET_CHILD_SUBREAPER` back after every test.

    `main()` calls `_become_subreaper()` among its first statements, before
    argparse -- so every test that drives the real `main()` sets
    `prctl(PR_SET_CHILD_SUBREAPER, 1)` on the *pytest* process, and nothing
    ever cleared it. The flag then silently changes the meaning of process
    liveness for every later test in the same session: an orphaned
    grandchild that would normally reparent to PID 1 (which `wait()`s it, so
    the PID vanishes) instead reparents to pytest, which never reaps it. It
    lingers as a zombie -- and a zombie still owns its PID slot, so
    `os.kill(pid, 0)` succeeds and a liveness probe reports a process that
    was in fact killed.

    Measured: this turned three `tests/test_signal_cleanup.py` orphan-reaping
    assertions red on CI while `orchestrator/leerie.py` and that file were
    both byte-identical to main. Collection is alphabetical, so `test_main_*`
    poisons `test_signal_cleanup`; `tests/test_subreaper.py` sets the same
    flag and escaped only by sorting *after* it -- an accident of filename,
    not a fix. Hence a suite-wide autouse restore rather than a per-file one.
    """
    with child_subreaper_restored():
        yield


# Every process a test starts carries this variable (its value is the test's
# own), so it can be found after the test however it was started: in its own
# process group or session, or reparented to init once its parent died.
TEST_PROCESS_ENV = "PYTEST_LEERIE_TEST_ID"


# What the teardown scan calls, bound when this module is imported. The
# scan runs at `_no_process_outlives_its_test`'s teardown, and that is
# before the test's monkeypatches are undone: `monkeypatch` is one instance
# per test, set up by the first autouse fixture that requests it (several
# above this one do), so it is torn down after every fixture set up later,
# this one included. Looked up at call time, `open` was the stub of a test
# that forbids reading files (tests/test_repo_change_warn_default.py's
# `test_fingerprint_never_reads_contents`, which then errored at teardown
# on every run), and any test patching `os.listdir`, `os.stat` or `os.kill`
# would have blinded or broken the scan the same way.
_open = open
_os_getpid, _os_getuid = os.getpid, os.getuid
_os_isdir, _os_listdir, _os_stat = os.path.isdir, os.listdir, os.stat
_os_kill, _os_waitpid = os.kill, os.waitpid


def processes_marked(marker: str) -> list[int]:
    """The PIDs, other than this process, whose environment carries
    `TEST_PROCESS_ENV=<marker>`. /proc is a fixed kernel format; a process
    that is gone or not ours is skipped.

    Empty where there is no /proc. The guarantee this backs is CI's (Linux),
    and a bare os.listdir("/proc") here raised FileNotFoundError in the
    autouse teardown that calls it, erroring the teardown of every test on
    macOS -- a dev host reads as "nothing left behind", the same answer it
    gave before this scan existed."""
    if not _os_isdir("/proc"):
        return []
    needle = f"{TEST_PROCESS_ENV}={marker}".encode()
    me, uid, out = _os_getpid(), _os_getuid(), []
    for name in _os_listdir("/proc"):
        if not name.isdigit() or int(name) == me:
            continue
        try:
            if _os_stat(f"/proc/{name}").st_uid != uid:
                continue
            with _open(f"/proc/{name}/environ", "rb") as f:
                env = f.read()
        except OSError:
            continue
        if needle + b"\0" in env or env.endswith(needle):
            out.append(int(name))
    return out


def kill_marked(marker: str, rounds: int = 5) -> list[int]:
    """SIGKILL every process carrying `marker`, again while new ones appear
    (a fork between a scan and its kill), and reap the ones that are this
    process's own children. Returns what it killed."""
    killed: list[int] = []
    for _ in range(rounds):
        pids = processes_marked(marker)
        if not pids:
            break
        for pid in pids:
            with contextlib.suppress(OSError):
                _os_kill(pid, signal.SIGKILL)
                killed.append(pid)
        for pid in pids:
            with contextlib.suppress(ChildProcessError, OSError):
                _os_waitpid(pid, os.WNOHANG)
    return killed


@pytest.fixture(autouse=True)
def _no_process_outlives_its_test(monkeypatch):
    """Kill, at teardown, every process the test started that is still
    alive.

    A per-test timeout (pytest-timeout's signal method, CI) raises inside
    the test, and `subprocess.run`'s own timeout kills only its direct
    child: what that child started (the launcher's relay pipeline: `bash -c`
    over `_log.sh`, its `bash` subshells, `terminal-text.py`) was left
    running, reparented to init, for hours. Those children change process
    group (`_build_job` puts a build in one of its own) and lose their
    parent, so neither is a handle; the environment is, since every child
    inherits it. Started after the session's and module's fixtures, so a
    process one of those keeps across tests is not marked.
    """
    marker = uuid.uuid4().hex
    monkeypatch.setenv(TEST_PROCESS_ENV, marker)
    yield
    kill_marked(marker)


@pytest.fixture(autouse=True)
def _quiet_host_pressure(request, monkeypatch):
    """The B* confirm gate (`_confirm_gate`) waits, up to
    `confirm_gate_wait_sec` (300 s) per confirm, for the HOST's CPU pressure
    to fall below `confirm_psi_cpu_max`. A test whose subject is not that
    gate then measured the host: on a loaded host (CI's 4 vCPUs under
    `-n 4`, or a developer's) tests/test_clean_tree_gate.py's repair tests
    took 559 s and 600 s, the second cut by the per-test timeout, against
    39 s on a quieter one. A read of the default file is a quiet host here;
    a read of a named file is real, so the reader's own tests still parse
    one. Opt out with `@pytest.mark.real_host_pressure`.
    """
    if (request.node.get_closest_marker("real_host_pressure")
            or "leerie" not in request.fixturenames):
        yield
        return
    mod = request.getfixturevalue("leerie")
    real = getattr(mod, "_host_cpu_psi", None)
    if real is None:
        yield
        return

    def quiet(path: str | None = None) -> float | None:
        return 0.0 if path is None else real(path)

    monkeypatch.setattr(mod, "_host_cpu_psi", quiet)
    yield


def judge_checkout_in_place(leerie, monkeypatch) -> None:
    """For a test that fakes git itself (`run_proc` stubbed, a made-up
    commit): `_judge_checkout` yields the given tree instead of adding a
    real checkout of a commit that does not exist. Where the judge reads is
    not such a test's subject; tests/test_pr_review_runs_outside_staging.py
    drives the real one."""
    import contextlib

    @contextlib.asynccontextmanager
    async def in_place(st, staging, name, *, rev=None, share_ignored=False):
        yield staging
    monkeypatch.setattr(leerie, "_judge_checkout", in_place)
