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
        "    import tenacity\n"
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
    relative entry, which names the cwd) or in the user site."""
    import os
    import sysconfig
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home")}
    if where == "pythonpath":
        _operator_tenacity(tmp_path / "op")
        env["PYTHONPATH"] = f".:tenacity_dir:{tmp_path / 'op'}"
    else:
        base = tmp_path / "userbase"
        env["PYTHONUSERBASE"] = str(base)
        _operator_tenacity(Path(sysconfig.get_path(
            "purelib", f"{os.name}_user", vars={"userbase": str(base)})))
    return env


def _plant_tenacity(checkout: Path, marker: Path) -> None:
    _plant(checkout, marker)
    for d in (checkout, checkout / "tenacity_dir"):
        d.mkdir(exist_ok=True)
        (d / "tenacity.py").write_text(
            f"open({str(marker)!r}, 'a').write('tenacity\\n')\n"
            "WHO = 'checkout'\n")


@pytest.mark.parametrize("where", ["pythonpath", "user_site"])
def test_the_rebaser_seam_imports_the_operators_tenacity(
        tmp_path: Path, where: str) -> None:
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
    assert json.loads(out.read_text()) == {"status": "operator"}


@pytest.mark.parametrize("where", ["pythonpath", "user_site"])
def test_the_recapture_seam_imports_the_operators_tenacity(
        tmp_path: Path, where: str) -> None:
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
    assert (state / "who").read_text() == "operator"


def test_the_chain_synth_merge_helper_drops_a_relative_pythonpath_entry(
        tmp_path: Path) -> None:
    """The chain helper hands the operator's PYTHONPATH to sys.path itself;
    `.` there names the cwd, the checkout, whose `chain/git_ops.py` must not
    run, while an absolute operator entry is still honoured."""
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
         f".:{tmp_path / 'op'}", str(REPO)],
        cwd=checkout, text=True, capture_output=True, timeout=120,
        check=False)
    assert not marker.exists(), marker.read_text()
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "who").read_text() == "operator"
