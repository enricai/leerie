"""The EC2 launch wrapper merges the baked Dockerfile ENV set
(/opt/leerie-image/leerie.env, written by ec2-bake.sh) into the
orchestrator's environment — an instance does not inherit an image's ENV the
way a container does. Executes the launcher's own rendered heredoc."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = (ROOT / "leerie").read_text()


def _child_env_heredoc() -> str:
    # The EC2 branch's child_env block: from `cat <<PY` up to its `PY`.
    start = LAUNCHER.index('_render_launch_prefix ec2 "$_ec2_launch_argv_json"')
    body = LAUNCHER[start:]
    m = re.search(r"\n\s*cat <<PY\n(.*?)\nPY\n", body, re.S)
    return "cat <<PY\n" + m.group(1) + "\nPY\n"


def _render_and_run(tmp_path: Path, env_file_text: str | None, session_env: dict) -> dict:
    env_file = tmp_path / "leerie.env"
    if env_file_text is not None:
        env_file.write_text(env_file_text)
    shell = (
        'USER_REPO=/repos/demo; _ec2_host_tz=UTC; _ec2_leerie_commit_json=\'""\'; '
        "_ec2_oauth_token_json='\"\"'; _ec2_oauth_tokens_json='\"\"'\n" + _child_env_heredoc()
    )
    rendered = subprocess.run(["bash", "-c", shell], capture_output=True, text=True, check=True).stdout
    rendered = rendered.replace("/opt/leerie-image/leerie.env", str(env_file))
    program = "import os, json\n" + rendered + "\nprint(json.dumps(child_env))\n"
    out = subprocess.run(["python3", "-c", program], capture_output=True, text=True,
                         env=session_env, check=True).stdout
    return json.loads(out)


def test_baked_env_overrides_the_session_env_and_session_specifics_win_after(tmp_path):
    session = {"PATH": "/usr/bin:/bin", "HOME": "/root", "KEEP_ME": "session"}
    env = _render_and_run(tmp_path, (
        "MISE_RUBY_COMPILE=false\n"
        "PATH=/usr/local/share/mise/shims:/usr/bin:/bin\n"
        "HOME=/not-used\n"
        "WITH_EQUALS=a=b\n"
    ), session)
    assert env["MISE_RUBY_COMPILE"] == "false"
    assert env["PATH"] == "/usr/local/share/mise/shims:/usr/bin:/bin"
    assert env["WITH_EQUALS"] == "a=b"          # split on the first '=' only
    assert env["KEEP_ME"] == "session"          # untouched keys survive
    assert env["HOME"] == "/home/leerie"         # the wrapper's own settings win
    assert env["USER_REPO"] == "demo" and env["TZ"] == "UTC"


def test_operator_ami_without_the_file_keeps_the_session_env(tmp_path):
    session = {"PATH": "/usr/bin:/bin", "HOME": "/root"}
    env = _render_and_run(tmp_path, None, session)
    assert env["PATH"] == "/usr/bin:/bin"
    assert "MISE_RUBY_COMPILE" not in env


def _orch_argv(orch_args: list[str]) -> list[str]:
    """Execute the shared launch suffix's own argv-assembly lines."""
    suffix = subprocess.run(
        ["bash", "-c", f". {ROOT}/scripts/remote/seed-common.sh; _render_launch_suffix"],
        capture_output=True, text=True, check=True).stdout
    lines = [l for l in suffix.splitlines() if l.startswith(("_split = ", "orch_argv = "))]
    assert len(lines) == 2, suffix
    ns = {"orch_args": orch_args}
    exec("\n".join(lines), ns)
    return ns["orch_argv"]


def test_resume_keeps_its_verb_and_run_id_first():
    # A leading --no-push made the orchestrator miss `resume` (it is
    # recognized only as argv[0]) and reject the run id — on a real resume.
    assert _orch_argv(["resume", "i-0abc", "--runtime", "ec2", "--host-no-push", "false"]) == [
        "resume", "i-0abc", "--runtime", "ec2", "--host-no-push", "false", "--no-push"]


def test_fresh_task_gets_no_push_after_its_args():
    assert _orch_argv(["Fix the bug", "--run-id", "i-0abc"]) == [
        "Fix the bug", "--run-id", "i-0abc", "--no-push"]


def test_no_push_stays_before_a_literal_separator():
    # After "--" argparse would read --no-push as a positional.
    assert _orch_argv(["--run-id", "r1", "--", "-task starting with a dash"]) == [
        "--run-id", "r1", "--no-push", "--", "-task starting with a dash"]


def test_launch_argv_parses_as_resume_in_the_orchestrator(leerie):
    # The orchestrator's own verb handling, applied to what the wrapper builds.
    argv = _orch_argv(["resume", "i-0abc", "--host-no-push", "false"])
    assert argv[0] == "resume"
    run_id, rest = leerie._extract_resume_run_id(argv[1:])
    assert run_id == "i-0abc" and "--no-push" in rest


def test_remote_launcher_exit_cleanup_kills_the_keepalive_and_removes_stage(tmp_path):
    # Executes the launcher's own hook: the staged credentials must go.
    body = re.search(r"\n_remote_launcher_exit_cleanup\(\) \{\n.*?\n\}\n", LAUNCHER, re.S).group(0)
    stage = tmp_path / "cfg-x"
    (stage / ".claude").mkdir(parents=True)
    (stage / ".claude" / ".credentials.json").write_text("{}")
    script = (f"( while :; do sleep 1; done ) & _stage_keepalive_pid=$!; STAGE={stage}\n"
              + body + "\n_remote_launcher_exit_cleanup; sleep 0.2; "
              "kill -0 $_stage_keepalive_pid 2>/dev/null && echo alive || echo gone")
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == "gone"
    assert not stage.exists()


def test_remote_launcher_exit_cleanup_is_safe_with_nothing_staged():
    # Under the launcher's `set -u`, an unset keepalive pid or STAGE must not
    # turn a clean teardown into an unbound-variable failure.
    body = re.search(r"\n_remote_launcher_exit_cleanup\(\) \{\n.*?\n\}\n", LAUNCHER, re.S).group(0)
    r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + body + "\n_remote_launcher_exit_cleanup; echo ok"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr
