"""DESIGN §6 *The host never imports the repository's code* (review round 25,
isolation I1).

The launcher and the scripts it runs on the host call `python3` from the
operator's cwd, which is the user's checkout. A program read from `-c` or
stdin puts the cwd first on `sys.path`, so a `json.py` / `contextlib.py` at
the checkout's root ran on the host, unconfined, with the operator's
credentials: the reviewer planted `contextlib.py` and the bare-resume pick
seam ran it. The invariant is that no host-side Python resolves a module from
the checkout, held by one spelled convention, `python3 -I`, at every call.

Two halves:
- a RULE over every host-run shell file: any `python3` invocation without
  `-I` fails, including commands sent to a Fly/EC2 machine and shell heredoc
  bodies, with a planted-file control proving the scan can fail;
- the reviewer's proof INVERTED, on the shipped code: the bare-resume seam,
  the `config --recapture` seam and the launcher's `-c` push-choice helper,
  run from a checkout holding planted stdlib-named modules, never run them —
  and still do their job.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "leerie"


def _host_files() -> list[Path]:
    """Every shell file the host runs: the launcher and what it runs or
    sources on the host (`host-finalize.sh`, `install.sh`,
    `runtime-install.sh`, the Fly/EC2 lifecycle under `scripts/remote/`)."""
    return ([LAUNCHER, REPO / "scripts" / "host-finalize.sh",
             REPO / "scripts" / "install.sh",
             REPO / "scripts" / "runtime-install.sh"]
            + sorted((REPO / "scripts" / "remote").glob("*.sh")))


_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
# `python3` as a word (not `/usr/bin/python3`, `python3.12`, `python3-pip`),
# with the next word captured: an invocation is followed by a flag, `-`, a
# quoted or variable script path, or an absolute path.
_PY3 = re.compile(r"(?<![\w/.-])python3(?![\w.-])(?=\s+(\S+))")
_INVOKES = re.compile(r"""^(-|["'$/])""")
_ISOLATED = re.compile(r"^-[A-Za-z]*I")


def _unisolated(path: Path) -> list[tuple[int, str]]:
    """(line number, line) of every `python3` invocation in `path` whose
    first flag cluster lacks `I`. Comments and Python heredoc bodies (their
    terminator names PY) are prose or Python, not invocations; other heredoc
    bodies are shell — a script sent to a machine — and are scanned."""
    out: list[tuple[int, str]] = []
    term: str | None = None
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if term is not None:
            if line.strip() == term:
                term = None
                continue
            if "PY" in term:
                continue
        if line.lstrip().startswith("#"):
            continue
        for m in _PY3.finditer(line):
            nxt = m.group(1)
            if _INVOKES.match(nxt) and not _ISOLATED.match(nxt):
                out.append((n, line.strip()))
        h = _HEREDOC.search(line)
        if h and "<<<" not in line:
            term = h.group(2)
    return out


def _invocations(path: Path) -> int:
    n = 0
    for line in path.read_text().splitlines():
        if line.lstrip().startswith("#"):
            continue
        n += sum(1 for m in _PY3.finditer(line) if _INVOKES.match(m.group(1)))
    return n


def test_every_host_python3_is_isolated() -> None:
    offenders = [f"{p.relative_to(REPO)}:{n}: {line[:100]}"
                 for p in _host_files() for n, line in _unisolated(p)]
    assert not offenders, (
        "host-side python3 must run isolated (`python3 -I`): the cwd is the "
        "user's checkout, and without -I a same-named module there runs on "
        "the host (DESIGN §6 *The host never imports the repository's "
        "code*):\n  " + "\n  ".join(offenders))


def test_the_scan_sees_the_known_calls() -> None:
    """Anti-vacuity: the launcher alone makes dozens of host python3 calls."""
    assert _invocations(LAUNCHER) >= 70
    assert all(p.is_file() for p in _host_files()[:4])


def test_the_rule_catches_an_unisolated_call(tmp_path: Path) -> None:
    f = tmp_path / "planted.sh"
    f.write_text(
        "v=\"$(python3 -c 'import json')\"\n"          # 1: caught
        "python3 - \"$x\" <<'PY'\n"                    # 2: caught
        "print('python3 -c inside Python is a string')\n"
        "PY\n"
        "ssh host \"python3 - '$id'\"\n"               # 5: caught
        "cat <<'REMOTE_SH'\n"
        "python3 -c 'pass'\n"                          # 7: caught (shell body)
        "REMOTE_SH\n"
        "# python3 -c in a comment\n"
        "python3 -I -B - <<'PY'\n"                     # 10: isolated
        "PY\n"
        "echo \"python3 not found\"\n"                 # 12: not an invocation
        "/usr/bin/python3 -IS x\n")
    assert [n for n, _ in _unisolated(f)] == [1, 2, 5, 7]


# --------------------------------------------------------------------------
# The reviewer's proof, inverted: planted modules at the checkout never run.
# --------------------------------------------------------------------------

def _plant(checkout: Path, marker: Path) -> None:
    """Stdlib names every seam imports (directly or through leerie.py)."""
    checkout.mkdir(parents=True, exist_ok=True)
    for name in ("contextlib", "json", "pathlib", "io"):
        (checkout / f"{name}.py").write_text(
            f"open({str(marker)!r}, 'a').write({name!r} + '\\n')\n"
            "raise SystemExit(0)\n")


def _seam(opener: str) -> tuple[list[str], str]:
    """The flags and body of the launcher's seam whose `python3` line
    contains `opener`, as shipped."""
    lines = LAUNCHER.read_text().splitlines()
    i = next(k for k, line in enumerate(lines)
             if "python3" in line and opener in line)
    flags = re.search(r"python3((?: -\w+)*) -(?:\s|$)", lines[i]).group(1)
    j = next(k for k in range(i, len(lines)) if "<<'PY'" in lines[k])
    end = next(k for k in range(j + 1, len(lines)) if lines[k] == "PY")
    return flags.split(), "\n".join(lines[j + 1:end]) + "\n"


def _resumable_run(state: Path, run_id: str, *, no_push: bool) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "leerie_for_layout", REPO / "orchestrator" / "leerie.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    d = state / "runs" / run_id
    d.mkdir(parents=True)
    (d / "state.json").write_text(json.dumps(
        {"task": "t", "started_at": "2026-01-01T00:00:00",
         "run_layout": m.RUN_LAYOUT_VERSION}))
    (d / "run.json").write_text(json.dumps({"no_push": no_push}))


def test_the_bare_resume_seam_never_runs_a_checkout_module(
        tmp_path: Path) -> None:
    flags, body = _seam('"$LEERIE_REPO/orchestrator/leerie.py" \\')
    assert "-I" in flags
    checkout, state, marker = (tmp_path / "checkout", tmp_path / "state",
                               tmp_path / "RAN_ON_HOST")
    _plant(checkout, marker)
    _resumable_run(state, "picked", no_push=True)
    out = tmp_path / "pick"
    subprocess.run(["python3", *flags, "-",
                    str(REPO / "orchestrator" / "leerie.py"), str(state),
                    str(out)],
                   input=body, text=True, cwd=checkout, timeout=120,
                   capture_output=True, check=False)
    assert not marker.exists(), marker.read_text()
    # And it still works: the pick is the orchestrator's own.
    assert out.read_text() == "picked"


def test_the_recapture_seam_never_runs_a_checkout_module(
        tmp_path: Path) -> None:
    flags, body = _seam('"$_user_repo" "$_leerie_state_dir"')
    assert "-I" in flags
    checkout, state, marker = (tmp_path / "checkout", tmp_path / "state",
                               tmp_path / "RAN_ON_HOST")
    _plant(checkout, marker)
    (state / "runs").mkdir(parents=True)
    r = subprocess.run(["python3", *flags, "-", str(checkout), str(state),
                        "false", str(REPO / "orchestrator" / "leerie.py")],
                       input=body, text=True, cwd=checkout, timeout=120,
                       capture_output=True, check=False)
    assert not marker.exists(), marker.read_text()
    # It reached leerie.py's own code (no runs to recapture), not a plant.
    assert "Traceback" not in r.stderr, r.stderr


def _launcher_no_push_block() -> str:
    text = LAUNCHER.read_text()
    start = text.index("\nNO_PUSH=false\n")
    tail = "unset _NO_PUSH_CLI _resume_run_id\n"
    return text[start:text.index(tail, start) + len(tail)]


def test_the_launcher_push_choice_helpers_never_run_a_checkout_module(
        tmp_path: Path) -> None:
    """The launcher's real NO_PUSH block — the bare-resume seam and the
    `python3 -I -c 'import json …'` record read — run from the checkout."""
    checkout, state, marker = (tmp_path / "checkout", tmp_path / "state",
                               tmp_path / "RAN_ON_HOST")
    _plant(checkout, marker)
    _resumable_run(state, "r1", no_push=True)
    script = ("set -euo pipefail\n"
              "remote_log() { echo \"$*\" >&2; }\n"
              f"USER_REPO={checkout}\nLEERIE_STATE_HOST_DIR={state}\n"
              f"LEERIE_REPO={REPO}\nRUNTIME=local\nIS_RESUME=true\n")
    for run_id in ("r1", ""):  # `resume r1`, then a bare `resume`
        r = subprocess.run(
            ["bash", "-c", script + f"LEERIE_RUN_ID={run_id}\n"
             + _launcher_no_push_block() + 'echo "$NO_PUSH"\n',
             "--", "resume", *([run_id] if run_id else [])],
            cwd=checkout, env={"PATH": "/usr/bin:/bin"},
            capture_output=True, text=True, timeout=120, check=False)
        assert r.returncode == 0, r.stderr
        assert not marker.exists(), marker.read_text()
        assert r.stdout.strip() == "true", (run_id, r.stdout, r.stderr)


# --------------------------------------------------------------------------
# The seams that reach claude_p find the operator's tenacity, never the
# checkout's (review round 26, F1). `-I` drops the user site and PYTHONPATH
# along with the cwd; `admit_operator_import_paths` takes back the first two.
# --------------------------------------------------------------------------

def _rebaser_seam() -> str:
    """The host rebaser's script, as host-finalize.sh writes it."""
    text = (REPO / "scripts" / "host-finalize.sh").read_text()
    start = text.index("cat > \"$_rebaser_py\" <<'PY'\n")
    start = text.index("\n", start) + 1
    return text[start:text.index("\nPY\n", start) + 1]


def _orchestrator_shim(tmp_path: Path) -> Path:
    """The real leerie.py, with the seams' worker entrypoints replaced by a
    `claude_p`-style lazy `import tenacity` that reports which one it got.
    The real `admit_operator_import_paths` is what the seams call."""
    shim = tmp_path / "orch_shim.py"
    shim.write_text(
        "import importlib.util, json, sys\n"
        "_s = importlib.util.spec_from_file_location(\n"
        f"    'leerie_real', {str(REPO / 'orchestrator' / 'leerie.py')!r})\n"
        "_m = importlib.util.module_from_spec(_s)\n"
        "_s.loader.exec_module(_m)\n"
        "admit_operator_import_paths = _m.admit_operator_import_paths\n"
        "def _which():\n"
        "    try:\n"
        "        import tenacity\n"
        "    except ImportError:\n"
        "        return 'none'\n"
        "    return getattr(tenacity, 'WHO', 'interpreter')\n"
        "def run_rebaser(*a):\n"
        "    return {'status': _which()}\n"
        "def run_recapture_deps(leerie_root, user_repo, force=False):\n"
        "    (leerie_root / 'who').write_text(_which())\n")
    return shim


def _operator_tenacity(site_dir: Path) -> None:
    pkg = site_dir / "tenacity"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("WHO = 'operator'\n")


def _host_env(tmp_path: Path, where: str) -> dict:
    """An environment whose operator tenacity is on PYTHONPATH (behind a
    relative entry, which names the cwd) or in the user site. The two
    `*_into_checkout` shapes are an operator's own paths that name the
    checkout (review round 27, isolation I2): an absolute PYTHONPATH entry
    (direnv's `PYTHONPATH=$PWD/src`) ahead of the operator's tenacity, and
    an editable install's `.pth` in the user site, with no tenacity of the
    operator's anywhere."""
    import os
    import sysconfig
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home")}
    user_site = Path(sysconfig.get_path(
        "purelib", f"{os.name}_user",
        vars={"userbase": str(tmp_path / "userbase")}))
    if where == "pythonpath":
        _operator_tenacity(tmp_path / "op")
        env["PYTHONPATH"] = f".:tenacity_dir:{tmp_path / 'op'}"
    elif where == "pythonpath_into_checkout":
        _operator_tenacity(tmp_path / "op")
        env["PYTHONPATH"] = (f"{tmp_path / 'checkout' / 'tenacity_dir'}:"
                             f"{tmp_path / 'op'}")
        env["PYTHONNOUSERSITE"] = "1"
    elif where == "pth_into_checkout":
        env["PYTHONUSERBASE"] = str(tmp_path / "userbase")
        user_site.mkdir(parents=True)
        (user_site / "__editable__.proj-0.1.pth").write_text(
            f"{tmp_path / 'checkout' / 'tenacity_dir'}\n")
    else:
        env["PYTHONUSERBASE"] = str(tmp_path / "userbase")
        _operator_tenacity(user_site)
    return env


# Each `where` and what the seam's tenacity must be. With no tenacity of the
# operator's, the interpreter's own (if the host has one) or none at all.
_TENACITY_CASES = [("pythonpath", {"operator"}), ("user_site", {"operator"}),
                   ("pythonpath_into_checkout", {"operator"}),
                   ("pth_into_checkout", {"interpreter", "none"})]


def _plant_tenacity(checkout: Path, marker: Path) -> None:
    _plant(checkout, marker)
    for d in (checkout, checkout / "tenacity_dir"):
        d.mkdir(exist_ok=True)
        (d / "tenacity.py").write_text(
            f"open({str(marker)!r}, 'a').write('tenacity\\n')\n"
            "WHO = 'checkout'\n")


@pytest.mark.parametrize("where,expect", _TENACITY_CASES)
def test_the_rebaser_seam_imports_the_operators_tenacity(
        tmp_path: Path, where: str, expect: set) -> None:
    checkout, marker = tmp_path / "checkout", tmp_path / "RAN_ON_HOST"
    _plant_tenacity(checkout, marker)
    script = tmp_path / "rebaser.py"
    script.write_text(_rebaser_seam())
    out = tmp_path / "rebaser.json"
    r = subprocess.run(
        ["python3", "-I", str(script), str(tmp_path), str(checkout), "rid",
         str(checkout), "b", "w", "main", str(_orchestrator_shim(tmp_path)),
         str(out)],
        cwd=checkout, env=_host_env(tmp_path, where), text=True,
        capture_output=True, timeout=120, check=False)
    assert not marker.exists(), marker.read_text()
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text())["status"] in expect


@pytest.mark.parametrize("where,expect", _TENACITY_CASES)
def test_the_recapture_seam_imports_the_operators_tenacity(
        tmp_path: Path, where: str, expect: set) -> None:
    flags, body = _seam('"$_user_repo" "$_leerie_state_dir"')
    checkout, state, marker = (tmp_path / "checkout", tmp_path / "state",
                               tmp_path / "RAN_ON_HOST")
    _plant_tenacity(checkout, marker)
    state.mkdir()
    r = subprocess.run(
        ["python3", *flags, "-", str(checkout), str(state), "false",
         str(_orchestrator_shim(tmp_path))],
        input=body, cwd=checkout, env=_host_env(tmp_path, where), text=True,
        capture_output=True, timeout=120, check=False)
    assert not marker.exists(), marker.read_text()
    assert r.returncode == 0, r.stderr
    assert (state / "who").read_text() in expect


def test_the_chain_synth_merge_helper_drops_a_relative_pythonpath_entry(
        tmp_path: Path) -> None:
    """The chain helper hands the operator's PYTHONPATH to sys.path itself;
    `.` there names the cwd, and an absolute entry may name the checkout
    itself (review round 27, isolation I2): the checkout's
    `chain/git_ops.py` must not run either way, while an absolute operator
    entry elsewhere is still honoured."""
    text = LAUNCHER.read_text()
    start = text.index("if ! python3 -I -c '\n") + len("if ! python3 -I -c '\n")
    source = text[start:text.index("\n' \"$USER_REPO\"", start) + 1]
    checkout, marker = tmp_path / "checkout", tmp_path / "RAN_ON_HOST"
    _plant(checkout, marker)
    for root, who in ((checkout, "checkout"), (tmp_path / "op", "operator")):
        pkg = root / "chain"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "git_ops.py").write_text(
            "class SynthMergeConflict(Exception): pass\n"
            "def synth_merge_branches(repo, base, branches, stage):\n"
            f"    open({str(marker if who == 'checkout' else tmp_path / 'who')!r},"
            f" 'w').write({who!r})\n")
    r = subprocess.run(
        ["python3", "-I", "-c", source, str(checkout), "main", "[]", "stage",
         f".:{checkout}:{tmp_path / 'op'}", str(REPO)],
        cwd=checkout, text=True, capture_output=True, timeout=120,
        check=False)
    assert not marker.exists(), marker.read_text()
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "who").read_text() == "operator"


# --------------------------------------------------------------------------
# DESIGN §6 *The host never runs a program the checkout chose* (review round
# 26, isolation I2). `-I` decides what an interpreter imports, not WHICH
# interpreter runs: a version manager's shim on PATH (asdf, mise, pyenv)
# chooses the program from the version files of its working directory and
# every directory above it. The launcher leaves the checkout before it runs
# anything, its own bash included, and points git at it from `/`.
#
# The shims below are built from shell builtins and absolute paths only, for
# a bounded list of names, each exec-ing the real program by the absolute
# path found before the shim directory went on PATH: no shim can start
# another shim, so none can recurse (the 2026-11-02 host crash was a shim in
# front of every program that called sed and head through PATH).
# --------------------------------------------------------------------------

import os  # noqa: E402
import shutil  # noqa: E402

_SHIMMED = ("python3", "bash", "git", "gh", "jq", "nerdctl", "flyctl", "aws",
            "ssh", "ssh-agent", "ssh-add", "sed", "awk", "grep", "cat",
            "head", "tail", "tr", "cut", "sort", "dirname", "basename",
            "uname", "mktemp", "readlink", "find", "id", "date", "timeout")

_SHIM = """#!/bin/sh
# A version manager's shim, as asdf behaves: builtins and absolute paths only.
d=$(pwd -P)
case "$d/" in "$SHIM_CHECKOUT"/*) printf '%s %s\\n' {name} "$d" >> "$SHIM_LOG" ;; esac
if [ {name} = python3 ]; then
  while :; do
    if [ -f "$d/.tool-versions" ]; then
      while IFS= read -r l || [ -n "$l" ]; do
        case "$l" in
          "python path:"*) exec "${{l#python path:}}/bin/python3" "$@" ;;
        esac
      done < "$d/.tool-versions"
    fi
    [ "$d" = / ] && break
    d=${{d%/*}}
    [ -n "$d" ] || d=/
  done
fi
{tail}
"""


def _shims(tmp_path: Path, checkout: Path) -> dict:
    """PATH with a shim in front of each of `_SHIMMED` present on this host;
    each logs a start whose working directory is in `checkout`. `ssh` never
    connects anywhere: it logs and fails."""
    shims = tmp_path / "shims"
    shims.mkdir()
    for name in _SHIMMED:
        real = shutil.which(name)
        if real is None:
            continue
        tail = ('printf "%s\\n" "$d" >> "$SHIM_LOG.ssh"; exit 255'
                if name == "ssh" else f'exec {real} "$@"')
        (shims / name).write_text(_SHIM.format(name=name, tail=tail))
        (shims / name).chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("LEERIE_", "GIT_"))}
    return {**env, "PATH": f"{shims}:{os.environ['PATH']}",
            "SHIM_LOG": str(tmp_path / "shim.log"),
            "SHIM_CHECKOUT": str(checkout.resolve()),
            "LEERIE_STATE_DIR": str(tmp_path / "state"),
            "HOME": str(home)}


def _hostile_checkout(tmp_path: Path, marker: Path) -> Path:
    """A checkout as a merged run branch could leave it: a `.tool-versions`
    naming the checkout's own program, which records that it ran."""
    co = tmp_path / "checkout"
    evil = co / ".evil" / "bin"
    evil.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(co)], check=True, timeout=60)
    (co / ".tool-versions").write_text("python path:/proc/self/cwd/.evil\n")
    (evil / "python3").write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> {marker}\n'
        f'exec {shutil.which("python3")} "$@"\n')
    (evil / "python3").chmod(0o755)
    return co


def _launcher(argv: list[str], co: Path, env: dict
              ) -> subprocess.CompletedProcess:
    return subprocess.run(["/usr/bin/timeout", "120", str(LAUNCHER), *argv],
                          cwd=co, env=env, capture_output=True, text=True,
                          timeout=150)


def test_the_checkouts_tool_versions_never_picks_the_launchers_python3(
        tmp_path: Path) -> None:
    """Review round 26 I2's proof, inverted and runnable without asdf: a
    shim that reads `.tool-versions` from its working directory up, as
    asdf's does, and a checkout naming `python path:/proc/self/cwd/.evil`.
    `leerie --help` from the checkout runs leerie's python3, not the
    checkout's — and still prints the help."""
    marker = tmp_path / "RAN_ON_HOST"
    co = _hostile_checkout(tmp_path, marker)
    env = _shims(tmp_path, co)
    r = _launcher(["--help"], co, env)
    assert not marker.exists(), marker.read_text()
    assert r.returncode == 0 and "usage: leerie" in r.stdout, r.stderr[-800:]
    # Control: the same shim started in the checkout does pick its program,
    # so the assertion above can fail.
    subprocess.run(["python3", "-c", "pass"], cwd=co, env=env, timeout=60,
                   check=True)
    assert marker.exists()


def test_no_program_the_launcher_starts_runs_in_the_checkout(
        tmp_path: Path) -> None:
    """The class, not the one interpreter: with each of `_SHIMMED` behind a
    shim that logs a start whose working directory is the checkout, the
    launcher's host-only paths start none there — its own `bash` included."""
    co = _hostile_checkout(tmp_path, tmp_path / "RAN_ON_HOST")
    env = _shims(tmp_path, co)
    for argv in (["--help"], ["version"], ["config"], ["list"]):
        r = _launcher(argv, co, env)
        assert r.returncode == 0, (argv, r.stdout[-800:], r.stderr[-800:])
    log = tmp_path / "shim.log"
    assert not log.exists(), log.read_text()
    # Control: a program started in the checkout is logged.
    subprocess.run(["cat", "/dev/null"], cwd=co, env=env, timeout=60,
                   check=True)
    assert log.read_text().startswith("cat ")


def test_a_relative_path_the_operator_gave_is_still_the_checkouts(
        tmp_path: Path) -> None:
    """Leaving the checkout keeps the operator's relative paths meaning
    what they meant: a missing relative task file is reported as missing,
    and a relative `--state-dir` is anchored at the checkout (beside it
    here: one inside it is refused, DESIGN §6 *No host path comes from a
    repository*)."""
    co = _hostile_checkout(tmp_path, tmp_path / "RAN_ON_HOST")
    env = _shims(tmp_path, co)
    env.pop("LEERIE_STATE_DIR")
    r = _launcher(["list", "--state-dir", "../rel-state"], co, env)
    assert r.returncode == 0, r.stderr[-800:]
    assert (tmp_path / "rel-state").is_dir()
    (co / "task.md").write_text("do it\n")
    r = _launcher(["missing.md"], co, env)
    assert r.returncode == 1 and "task file not found: missing.md" in \
        r.stderr, r.stderr[-800:]


def test_the_launcher_leaves_the_checkout_before_its_interpreter_is_chosen(
        ) -> None:
    """`#!/usr/bin/env bash` resolved bash through PATH from the checkout;
    the launcher starts under /bin/sh, whose path no shim chooses, and
    changes to `/` before it execs the bash PATH names there."""
    lines = LAUNCHER.read_text().splitlines()
    assert lines[0] == "#!/bin/sh"
    end = next(i for i, line in enumerate(lines)
               if line.strip().startswith('exec "$_leerie_bash"'))
    prologue = "\n".join(lines[:end])
    assert "cd / || exit 1" in prologue
    assert prologue.index("cd / || exit 1") < prologue.index(
        "_leerie_bash=$(command -v bash)")


def _git_remote_cwds(tmp_path: Path, script: str) -> str:
    """Run `script` (bash, host-finalize.sh sourced) from `/` against a
    checkout whose origin is an ssh URL, with `ssh` behind a logging shim;
    the shim log ("" when nothing ran in the checkout). Asserts `ssh` was
    started at all, so an empty log is never a command that did not run."""
    co = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", str(co)], check=True, timeout=60)
    subprocess.run(["git", "-C", str(co), "remote", "add", "origin",
                    "ssh://git@leerie.invalid/x/y.git"], check=True)
    env = _shims(tmp_path, co)
    subprocess.run(["/usr/bin/timeout", "60", "/bin/bash", "-c",
                    f'. "{REPO}/scripts/host-finalize.sh"; USER_REPO="{co}"; '
                    + script],
                   cwd="/", env=env, capture_output=True, text=True,
                   timeout=90)
    assert (tmp_path / "shim.log.ssh").is_file(), "git never started ssh"
    log = tmp_path / "shim.log"
    return log.read_text() if log.exists() else ""


def test_a_git_command_that_reaches_a_remote_starts_nothing_in_the_checkout(
        tmp_path: Path) -> None:
    """`git -C` changes into the work tree and starts `ssh` (and the
    credential helper) there; `_repo_git_from_root` names the repository by
    its git directory and work tree, so they start in `/`."""
    assert _git_remote_cwds(
        tmp_path, '_repo_git_from_root "$USER_REPO" ls-remote origin') == ""


def test_the_remote_git_control_starts_ssh_in_the_checkout(
        tmp_path: Path) -> None:
    """Control for the test above: the `-C` form it replaced is caught."""
    assert _git_remote_cwds(
        tmp_path, 'git -C "$USER_REPO" ls-remote origin').startswith("ssh ")


def test_host_finalize_reaches_remotes_only_from_root() -> None:
    """Every git command in host-finalize.sh and the launcher that reaches
    a remote goes through `_repo_git_from_root` or `_repo_git_push`, a push
    with the operator's pre-push hook included (review round 27, isolation
    I5: that push kept `git -C`). `gh` is pointed at the repository with
    `GIT_DIR` and `GIT_WORK_TREE`."""
    text = (REPO / "scripts" / "host-finalize.sh").read_text()
    bad = []
    for f in (REPO / "scripts" / "host-finalize.sh", LAUNCHER):
        for ln in f.read_text().splitlines():
            if ln.lstrip().startswith(("#", "echo", "printf")):
                continue
            # `$LEERIE_REPO` is leerie's own install, not a checkout.
            if re.search(r'git -C "\$\{?\w+\}?" (push|fetch|ls-remote|pull|'
                         r'remote show)\b', ln) and "$LEERIE_REPO" not in ln:
                bad.append(ln.strip())
    assert bad == [], bad
    assert re.search(r'GIT_DIR="\$_pr_gd" GIT_WORK_TREE="\$USER_REPO" gh pr '
                     r'create', text)


def test_no_host_script_makes_a_checkout_its_cwd() -> None:
    """The scripts the host runs address a checkout with `-C` or by path:
    none `cd`s into it, and no `git submodule foreach` body starts a program
    inside a submodule's checkout (only the shell's own printf)."""
    bad = []
    for p in _host_files() + [REPO / "scripts" / "remote" / "seed-common.sh"]:
        lines = p.read_text().splitlines()
        for n, line in enumerate(lines, 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            # `config --chat` too (review round 27, isolation I1): its CLI
            # starts in a directory of its own and names the checkout by
            # `--add-dir`.
            if re.search(r'\bcd "\$(USER_REPO|host|_user_repo|repo)"', s):
                bad.append(f"{p.name}:{n}: {s}")
            body = s + " " + (lines[n].strip() if n < len(lines) else "")
            if re.search(r"submodule\b.*\bforeach\b", s) and not re.search(
                    r"""foreach --recursive \\?\s*'printf "[^']*"[^']*'$""",
                    " ".join(body.split())):
                bad.append(f"{p.name}:{n}: {s}")
    assert not bad, bad


def test_chain_git_runs_from_root_and_names_the_repo(tmp_path: Path,
                                                     monkeypatch) -> None:
    """chain/git_ops.py starts git from `/`: `-C` for a local command, the
    git directory and work tree for one that reaches a remote."""
    import sys
    sys.path.insert(0, str(REPO))
    try:
        from chain import git_ops
    finally:
        sys.path.remove(str(REPO))
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    seen = []
    real = subprocess.run

    def fake_run(args, cwd=None, **kw):
        if "rev-parse" in args and "--absolute-git-dir" in args:
            return real(args, cwd=cwd, **kw)
        seen.append((args, cwd))
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(git_ops.subprocess, "run", fake_run)
    git_ops._run(["git", "status"], cwd=tmp_path)
    git_ops._run(["git", "fetch", "origin"], cwd=tmp_path)
    assert seen == [
        (["git", "-C", str(tmp_path), "status"], "/"),
        (["git", f"--git-dir={tmp_path / '.git'}",
          f"--work-tree={tmp_path}", "fetch", "origin"], "/")]


# --------------------------------------------------------------------------
# Review round 27, isolation I5: a fetch that brings in a commit moving a
# submodule runs `git fetch` inside that submodule's git directory, in the
# checkout (`<checkout>/.git/modules/<sub>`), and that starts the
# submodule's transport there. Every host fetch and pull says
# `--no-recurse-submodules`.
# --------------------------------------------------------------------------

_GIT_WORD = re.compile(
    r"(?:^|[\s;(&|`])(?:git|_repo_git_from_root|_repo_git_push|leerie_\w*git)"
    r"(?=\s)(?P<rest>[^|;`]*)")
_REMOTE_WORD = re.compile(r"\s(fetch|pull)(?=\s|$)")


def _logical_lines(path: Path) -> list[tuple[int, str]]:
    """`path`'s lines with backslash continuations joined, comments and
    lines that only print dropped."""
    out: list[tuple[int, str]] = []
    buf, start = "", 0
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not buf:
            start = n
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        buf += line
        s = buf.strip()
        buf = ""
        if s.startswith(("#", "echo", "printf", "remote_log", "log ", "err ")):
            continue
        out.append((start, s))
    return out


def _fetches_recursing(path: Path) -> list[str]:
    bad = []
    if path.suffix == ".py":
        text = path.read_text()
        for m in re.finditer(r'\[\s*"git",\s*"(fetch|pull)"[^\]]*\]', text):
            if '"--no-recurse-submodules"' not in m.group(0):
                bad.append(f"{path.name}: {m.group(0)}")
        return bad
    for n, s in _logical_lines(path):
        for g in _GIT_WORD.finditer(s):
            if _REMOTE_WORD.search(g.group("rest")) and \
                    "--no-recurse-submodules" not in g.group("rest"):
                bad.append(f"{path.name}:{n}: {s}")
    return bad


def test_every_host_fetch_skips_submodule_recursion() -> None:
    bad = []
    for p in _host_files() + sorted((REPO / "chain").glob("*.py")):
        bad += _fetches_recursing(p)
    assert not bad, bad


def test_the_fetch_rule_sees_the_known_fetches() -> None:
    """The scan reaches what it must: the scratch-ref fetch, the bundle
    fetches of both remote runtimes and the chain's fetch are among the
    fetches it reads (a scan that matched nothing would pass)."""
    seen = []
    for p in _host_files() + sorted((REPO / "chain").glob("*.py")):
        if p.suffix == ".py":
            seen += [p.name for _ in re.finditer(
                r'\[\s*"git",\s*"fetch"', p.read_text())]
            continue
        for n, s in _logical_lines(p):
            if any(_REMOTE_WORD.search(g.group("rest"))
                   for g in _GIT_WORD.finditer(s)):
                seen.append(p.name)
    for name in ("host-finalize.sh", "fetch-branch.sh",
                 "ec2-fetch-branch.sh", "git_ops.py", "leerie"):
        assert name in seen, (name, seen)
    assert seen.count("host-finalize.sh") >= 5, seen


def test_the_fetch_rule_catches_a_recursing_fetch(tmp_path: Path) -> None:
    sh = tmp_path / "x.sh"
    sh.write_text('git -C "$USER_REPO" -c core.hooksPath=/dev/null fetch \\\n'
                  '  --no-write-fetch-head "$tree" HEAD:refs/x\n'
                  'echo "fetch it"\n')
    assert len(_fetches_recursing(sh)) == 1
    py = tmp_path / "x.py"
    py.write_text('_run(["git", "fetch", "origin"], cwd=r)\n')
    assert len(_fetches_recursing(py)) == 1


def _submodule_bump(tmp_path: Path) -> tuple[Path, Path]:
    """The user's checkout with a populated submodule whose remote is ssh,
    and a run tree whose commit moves that submodule's gitlink."""
    def g(*a: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.email=x@x.invalid", "-c", "user.name=x",
             "-c", "protocol.file.allow=always", *a], check=True,
            capture_output=True, text=True, timeout=60).stdout
    sub, user, run = (tmp_path / n for n in ("sub", "user", "run-tree"))
    g("init", "-q", str(sub))
    g("-C", str(sub), "commit", "-q", "--allow-empty", "-m", "s1")
    g("init", "-q", str(user))
    g("-C", str(user), "submodule", "add", "-q", str(sub), "lib")
    g("-C", str(user), "commit", "-q", "-m", "with lib")
    g("-C", str(user / "lib"), "remote", "set-url", "origin",
      "ssh://git@leerie.invalid/lib.git")
    g("-C", str(sub), "commit", "-q", "--allow-empty", "-m", "s2")
    s2 = g("-C", str(sub), "rev-parse", "HEAD").strip()
    g("clone", "-q", str(user), str(run))
    g("-C", str(run), "update-index", "--cacheinfo", f"160000,{s2},lib")
    g("-C", str(run), "commit", "-q", "-m", "bump lib")
    return user, run


def test_the_rebase_scratch_fetch_starts_nothing_in_a_submodule(
        tmp_path: Path) -> None:
    """Review round 27 I5's proof, inverted, on the shipped command line:
    host-finalize's scratch-ref fetch of the rebaser's worktree, whose
    commit moves a gitlink, starts no `ssh` (git's on-demand recursion
    would start the submodule's transport in `<checkout>/.git/modules`).
    The control drops the flag from the same command and is caught."""
    text = (REPO / "scripts" / "host-finalize.sh").read_text()
    m = re.search(r'git -C "\$USER_REPO" -c core\.hooksPath=/dev/null fetch'
                  r' \\\n(?:.*\\\n)*?.*"HEAD:\$_rebase_scratch_ref" --force',
                  text)
    assert m, "the scratch-ref fetch moved"
    cmd = " ".join(m.group(0).replace("\\\n", " ").split())

    def ssh_started(line: str, where: Path) -> bool:
        """`line` over a fresh pair of repositories (git recurses only for
        a commit it has just fetched)."""
        where.mkdir()
        user, run = _submodule_bump(where)
        env = _shims(where, user)
        subprocess.run(["/bin/bash", "-c", f'USER_REPO="$1"; _rebase_worktree'
                        f'="$2"; _rebase_scratch_ref=refs/leerie-scratch/x; '
                        f'{line}', "x", str(user), str(run)],
                       cwd="/", env=env, capture_output=True, timeout=90)
        return (where / "shim.log.ssh").exists()
    assert not ssh_started(cmd, tmp_path / "shipped")
    # Control: the same command without the flag is caught.
    assert ssh_started(cmd.replace(" --no-recurse-submodules", ""),
                       tmp_path / "control")


# --------------------------------------------------------------------------
# Review round 27, isolation I1: `config --chat` ran `claude` from inside the
# checkout (`cd "$_user_repo" && exec claude`), so a shim, or a relative PATH
# entry such as `node_modules/.bin`, chose it from the checkout.
# --------------------------------------------------------------------------

_CLAUDE_STUB = """#!/bin/sh
printf 'pwd=%s\\n' "$PWD" >> "$CHAT_LOG"
for a in "$@"; do printf 'arg=%s\\n' "$a" >> "$CHAT_LOG"; done
exit 0
"""


def test_config_chat_starts_its_cli_outside_the_checkout(
        tmp_path: Path) -> None:
    marker = tmp_path / "RAN_ON_HOST"
    co = _hostile_checkout(tmp_path, marker)
    # The checkout's own `claude`, reachable through a relative PATH entry.
    (co / "node_modules" / ".bin").mkdir(parents=True)
    (co / "node_modules" / ".bin" / "claude").write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$PWD" >> {marker}\n')
    (co / "node_modules" / ".bin" / "claude").chmod(0o755)
    env = _shims(tmp_path, co)
    stub = tmp_path / "cli"
    stub.mkdir()
    (stub / "claude").write_text(_CLAUDE_STUB)
    (stub / "claude").chmod(0o755)
    env["PATH"] = f"node_modules/.bin:{env['PATH']}:{stub}"
    env["CHAT_LOG"] = str(tmp_path / "chat.log")
    r = _launcher(["config", "--chat"], co, env)
    assert r.returncode == 0, r.stderr[-800:]
    assert not marker.exists(), marker.read_text()
    log = (tmp_path / "chat.log").read_text().splitlines()
    pwd = log[0].removeprefix("pwd=")
    assert not (pwd + "/").startswith(str(co.resolve()) + "/"), pwd
    assert not Path(pwd).exists(), "the chat's directory outlived it"
    args = [ln.removeprefix("arg=") for ln in log[1:]]
    assert args[args.index("--add-dir") + 1] == str(co.resolve())
    assert str(co.resolve()) in args[-1]
    shim_log = tmp_path / "shim.log"
    assert not shim_log.exists(), shim_log.read_text()
    # Control: from the checkout, the relative entry finds the checkout's.
    subprocess.run(["claude"], cwd=co, env=env, timeout=60)
    assert marker.exists()
