"""DESIGN §6 *The host never runs a program the checkout chose*, part 2
(review round 27, isolation I5).

`_repo_git_push` kept `git -C "$repo" push` for the whole push whenever the
operator had a pre-push hook (husky, pre-commit and `git lfs install` all
install one), so git started the transport (`ssh`) and the credential helper
in the checkout too, where a version manager's shim chooses them from the
checkout's version files. Only the hook needs the work tree.

The reviewer's proof, inverted: with a hook, `ssh` starts in `/`. And the
hook keeps its semantics: it starts at the top of the work tree, gets git's
arguments, its ref lines on stdin and the operator's `GIT_CONFIG_PARAMETERS`,
and its exit status still vetoes the push.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HF = REPO / "scripts" / "host-finalize.sh"

# Builtins only (no program found through PATH, so no shim can recurse).
_SSH = """#!/bin/sh
printf '%s\\n' "$PWD" >> "$SSH_STARTS"
exit 255
"""

_HOOK = """#!/bin/sh
{{
  printf 'pwd=%s\\n' "$PWD"
  printf 'args=%s|%s\\n' "$1" "$2"
  printf 'gcp=%s\\n' "${{GIT_CONFIG_PARAMETERS-unset}}"
  printf 'leerie=%s\\n' "${{LEERIE_HOOKS_DIR-unset}}"
  while read -r l r; do printf 'in=%s %s\\n' "$l" "$r"; done
}} >> {log}
exit {rc}
"""


def _g(*a: str, cwd: str | None = None) -> str:
    return subprocess.run(["git", "-c", "user.email=x@x.invalid", "-c",
                           "user.name=x", *a], cwd=cwd, check=True,
                          capture_output=True, text=True,
                          timeout=60).stdout


def _checkout(tmp_path: Path, origin: str) -> Path:
    co = tmp_path / "checkout"
    _g("init", "-q", "-b", "main", str(co))
    (co / "sub").mkdir()
    _g("-C", str(co), "commit", "-q", "--allow-empty", "-m", "c")
    _g("-C", str(co), "remote", "add", "origin", origin)
    return co


def _hook(co: Path, log: Path, rc: int, where: str = ".git/hooks") -> None:
    d = co / where
    d.mkdir(parents=True, exist_ok=True)
    (d / "pre-push").write_text(_HOOK.format(log=log, rc=rc))
    (d / "pre-push").chmod(0o755)


def _push(tmp_path: Path, repo: Path, *args: str,
          env_extra: dict | None = None) -> subprocess.CompletedProcess:
    shims = tmp_path / "shims"
    shims.mkdir(exist_ok=True)
    (shims / "ssh").write_text(_SSH)
    (shims / "ssh").chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_", "LEERIE_"))}
    env.update(PATH=f"{shims}:{os.environ['PATH']}", GIT_TERMINAL_PROMPT="0",
               SSH_STARTS=str(tmp_path / "ssh-starts"), **(env_extra or {}))
    return subprocess.run(
        ["/bin/bash", "-c", f'. "{HF}"; _repo_git_push "$@"', "x", str(repo),
         *args], cwd="/", env=env, capture_output=True, text=True,
        timeout=90)


def test_a_hooked_push_starts_ssh_outside_the_checkout(tmp_path: Path
                                                       ) -> None:
    co = _checkout(tmp_path, "ssh://git@leerie.invalid/r.git")
    _hook(co, tmp_path / "hook.log", 0)
    _push(tmp_path, co, "origin", "HEAD:refs/heads/x")
    assert (tmp_path / "ssh-starts").read_text().split() == ["/"]
    # Control: the `-C` form the push used to keep starts it in the checkout.
    (tmp_path / "ssh-starts").unlink()
    subprocess.run(["/bin/bash", "-c", 'git -C "$1" push origin HEAD:x', "x",
                    str(co)], cwd="/", capture_output=True, timeout=90,
                   env={**os.environ, "GIT_TERMINAL_PROMPT": "0",
                        "PATH": f"{tmp_path / 'shims'}:{os.environ['PATH']}",
                        "SSH_STARTS": str(tmp_path / "ssh-starts")})
    assert (tmp_path / "ssh-starts").read_text().split() == [str(co)]


def _local_remote(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    _g("init", "-q", "--bare", str(bare))
    return bare


def _remote_has(bare: Path, ref: str) -> bool:
    return subprocess.run(["git", "--git-dir", str(bare), "rev-parse", "-q",
                           "--verify", ref], capture_output=True,
                          timeout=30).returncode == 0


def test_the_hook_runs_as_git_would_run_it(tmp_path: Path) -> None:
    bare = _local_remote(tmp_path)
    co = _checkout(tmp_path, str(bare))
    log = tmp_path / "hook.log"
    _hook(co, log, 0)
    head = _g("-C", str(co), "rev-parse", "HEAD").strip()
    # Named by a subdirectory: the hook still starts at the top.
    r = _push(tmp_path, co / "sub", "origin", "HEAD:refs/heads/ok",
              env_extra={"GIT_CONFIG_PARAMETERS": "'op.key'='v'"})
    assert r.returncode == 0, r.stderr
    assert _remote_has(bare, "refs/heads/ok")
    lines = log.read_text().splitlines()
    assert f"pwd={co}" in lines, lines
    assert f"args=origin|{bare}" in lines, lines
    assert "gcp='op.key'='v'" in lines, lines
    assert "leerie=unset" in lines, lines
    assert any(ln.startswith(f"in=HEAD {head} refs/heads/ok")
               for ln in lines), lines


def test_no_operator_parameters_leave_none(tmp_path: Path) -> None:
    bare = _local_remote(tmp_path)
    co = _checkout(tmp_path, str(bare))
    _hook(co, tmp_path / "hook.log", 0)
    assert _push(tmp_path, co, "origin", "HEAD:refs/heads/ok").returncode == 0
    assert "gcp=unset" in (tmp_path / "hook.log").read_text().splitlines()


def test_the_hook_still_vetoes_the_push(tmp_path: Path) -> None:
    bare = _local_remote(tmp_path)
    co = _checkout(tmp_path, str(bare))
    _hook(co, tmp_path / "hook.log", 1)
    r = _push(tmp_path, co, "origin", "HEAD:refs/heads/no")
    assert r.returncode != 0
    assert not _remote_has(bare, "refs/heads/no")
    assert "in=HEAD" in (tmp_path / "hook.log").read_text()
    # --no-verify skips it, as before.
    r = _push(tmp_path, co, "--no-verify", "origin", "HEAD:refs/heads/nv")
    assert r.returncode == 0 and _remote_has(bare, "refs/heads/nv")


def test_a_relative_core_hookspath_is_honoured(tmp_path: Path) -> None:
    """husky's shape: `core.hooksPath=.husky/_`, relative to the top."""
    bare = _local_remote(tmp_path)
    co = _checkout(tmp_path, str(bare))
    _g("-C", str(co), "config", "core.hooksPath", ".husky/_")
    _hook(co, tmp_path / "hook.log", 1, where=".husky/_")
    r = _push(tmp_path, co / "sub", "origin", "HEAD:refs/heads/no")
    assert r.returncode != 0 and not _remote_has(bare, "refs/heads/no")
    assert f"pwd={co}" in (tmp_path / "hook.log").read_text().splitlines()


def test_without_a_hook_the_push_succeeds(tmp_path: Path) -> None:
    bare = _local_remote(tmp_path)
    co = _checkout(tmp_path, str(bare))
    assert _push(tmp_path, co, "origin", "HEAD:refs/heads/ok").returncode == 0
    assert _remote_has(bare, "refs/heads/ok")


def test_leeries_hooks_are_one_script() -> None:
    d = REPO / "scripts" / "push-hooks"
    assert sorted(p.name for p in d.iterdir()) == ["pre-push",
                                                    "reference-transaction"]
    assert (d / "pre-push").read_bytes() == \
        (d / "reference-transaction").read_bytes()
    assert all(os.access(p, os.X_OK) for p in d.iterdir())
