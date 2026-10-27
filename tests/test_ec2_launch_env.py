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
