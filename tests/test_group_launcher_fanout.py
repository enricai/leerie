"""Tests for the --group launcher fan-out core contract (Verification §2).

Asserts that `leerie --group --repo A "pA" --repo B "pB" [--brief f]` spawns
one child per member with:
  - cwd set to the member's own repo directory,
  - --group-id <uuid> in argv,
  - --inspect-dir <sibling> for every OTHER member (not itself),
  - the shared brief text prepended to the member's prompt.

Uses the LEERIE_SELF_CMD stub-recorder pattern from
tests/test_chain_launcher_id_dispatch.py: a bash stub records both the
current working directory and the full argv to a log file, then exits 0.
The launcher's fan-out subshell `( cd <repo> && "${LEERIE_SELF_CMD}" ... ) &`
causes the stub to run in the member's cwd, making cwd an observable.
"""
from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

GROUP_ID = "cafebabe-dead-4bee-8bad-0123456789ab"
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def _make_git_repo(path: Path) -> None:
    """Initialise a bare-minimum git repo at *path*."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--allow-empty", "-m", "init"],
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
        capture_output=True,
        check=True,
    )


def _stub_recorder(tmp_path: Path) -> tuple[Path, Path]:
    """Build a stub that records 'CWD:<pwd>\\nARGV:<$@>' to a log file.

    Each invocation appends a two-line block so multiple children in the
    same fan-out accumulate in one log without racing (append is atomic for
    short writes on local filesystems).

    Returns ``(stub_path, log_path)``.
    """
    log = tmp_path / "fanout.log"
    stub = tmp_path / "fanout-stub"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        printf 'CWD:%s\\nARGV:%s\\n' "$(pwd)" "$*" >> "{log}"
        exit 0
        """))
    stub.chmod(0o755)
    return stub, log


def _run(
    tmp_path: Path,
    args: list[str],
    stub: Path,
    log: Path,
    env_extra: dict | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the launcher with *args* and the stub recorder active."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "LEERIE_REPO": str(REPO_ROOT),
        "LEERIE_SELF_CMD": str(stub),
        # Intentionally NO LEERIE_STATE_DIR so the --group guard passes.
    }
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        ["bash", str(LAUNCHER)] + args,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    result.stub_log = log.read_text() if log.exists() else ""
    return result


# ---------------------------------------------------------------------------
# Core fan-out contract
# ---------------------------------------------------------------------------


def test_two_children_spawned(tmp_path: Path) -> None:
    """Exactly two stub invocations appear in the log for a two-repo group."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _make_git_repo(repo_a)
    _make_git_repo(repo_b)
    stub, log = _stub_recorder(tmp_path)

    result = _run(
        tmp_path,
        ["--group", "--group-id", GROUP_ID,
         "--repo", str(repo_a), "prompt for a",
         "--repo", str(repo_b), "prompt for b"],
        stub=stub, log=log,
    )
    assert result.returncode == 0, result.stderr
    # Two "CWD:" lines → two child invocations.
    assert result.stub_log.count("CWD:") == 2


def test_each_child_cds_into_its_repo(tmp_path: Path) -> None:
    """Each child runs in its own repo directory (not the parent's cwd)."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _make_git_repo(repo_a)
    _make_git_repo(repo_b)
    stub, log = _stub_recorder(tmp_path)

    result = _run(
        tmp_path,
        ["--group", "--group-id", GROUP_ID,
         "--repo", str(repo_a), "prompt for a",
         "--repo", str(repo_b), "prompt for b"],
        stub=stub, log=log,
    )
    assert result.returncode == 0, result.stderr
    log_text = result.stub_log
    # Both repo paths must appear as CWD entries.
    assert f"CWD:{repo_a.resolve()}" in log_text
    assert f"CWD:{repo_b.resolve()}" in log_text


def test_each_child_receives_group_id(tmp_path: Path) -> None:
    """Both children receive --group-id <uuid> in their argv."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _make_git_repo(repo_a)
    _make_git_repo(repo_b)
    stub, log = _stub_recorder(tmp_path)

    result = _run(
        tmp_path,
        ["--group", "--group-id", GROUP_ID,
         "--repo", str(repo_a), "task a",
         "--repo", str(repo_b), "task b"],
        stub=stub, log=log,
    )
    assert result.returncode == 0, result.stderr
    log_text = result.stub_log
    # The group-id flag appears at least twice (once per child).
    assert log_text.count(f"--group-id {GROUP_ID}") >= 2


def test_each_child_receives_group_id_minted_when_absent(tmp_path: Path) -> None:
    """Without --group-id a fresh UUID is minted and passed to each child."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _make_git_repo(repo_a)
    _make_git_repo(repo_b)
    stub, log = _stub_recorder(tmp_path)

    result = _run(
        tmp_path,
        ["--group",
         "--repo", str(repo_a), "task a",
         "--repo", str(repo_b), "task b"],
        stub=stub, log=log,
    )
    assert result.returncode == 0, result.stderr
    uuids = UUID_RE.findall(result.stub_log)
    # At least two appearances (one per child) and they must be identical.
    assert len(uuids) >= 2
    assert len(set(uuids)) == 1, "all children should share the same minted group_id"


def test_child_a_receives_inspect_dir_for_b(tmp_path: Path) -> None:
    """The child for repo-a receives --inspect-dir pointing at repo-b."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _make_git_repo(repo_a)
    _make_git_repo(repo_b)
    stub, log = _stub_recorder(tmp_path)

    result = _run(
        tmp_path,
        ["--group", "--group-id", GROUP_ID,
         "--repo", str(repo_a), "task a",
         "--repo", str(repo_b), "task b"],
        stub=stub, log=log,
    )
    assert result.returncode == 0, result.stderr
    log_text = result.stub_log

    # Parse per-child blocks: "CWD:<cwd>\nARGV:<argv>"
    blocks = _parse_blocks(log_text)

    block_a = _find_block(blocks, cwd=str(repo_a.resolve()))
    assert block_a is not None, "no child block found with CWD=repo-a"
    assert f"--inspect-dir {repo_b.resolve()}" in block_a["argv"], (
        f"child for repo-a should carry --inspect-dir repo-b; got: {block_a['argv']}"
    )
    # Must NOT carry an inspect-dir pointing at itself.
    assert f"--inspect-dir {repo_a.resolve()}" not in block_a["argv"]


def test_child_b_receives_inspect_dir_for_a(tmp_path: Path) -> None:
    """The child for repo-b receives --inspect-dir pointing at repo-a."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _make_git_repo(repo_a)
    _make_git_repo(repo_b)
    stub, log = _stub_recorder(tmp_path)

    result = _run(
        tmp_path,
        ["--group", "--group-id", GROUP_ID,
         "--repo", str(repo_a), "task a",
         "--repo", str(repo_b), "task b"],
        stub=stub, log=log,
    )
    assert result.returncode == 0, result.stderr
    log_text = result.stub_log

    blocks = _parse_blocks(log_text)
    block_b = _find_block(blocks, cwd=str(repo_b.resolve()))
    assert block_b is not None, "no child block found with CWD=repo-b"
    assert f"--inspect-dir {repo_a.resolve()}" in block_b["argv"], (
        f"child for repo-b should carry --inspect-dir repo-a; got: {block_b['argv']}"
    )
    assert f"--inspect-dir {repo_b.resolve()}" not in block_b["argv"]


def test_three_members_each_get_two_inspect_dirs(tmp_path: Path) -> None:
    """With three repos each child gets exactly two --inspect-dir flags."""
    repos = [tmp_path / f"repo-{n}" for n in ("a", "b", "c")]
    for r in repos:
        _make_git_repo(r)
    stub, log = _stub_recorder(tmp_path)

    cli = ["--group", "--group-id", GROUP_ID]
    for r in repos:
        cli += ["--repo", str(r), f"task {r.name}"]

    result = _run(tmp_path, cli, stub=stub, log=log)
    assert result.returncode == 0, result.stderr
    log_text = result.stub_log

    blocks = _parse_blocks(log_text)
    assert len(blocks) == 3, f"expected 3 child invocations, got {len(blocks)}"
    for blk in blocks:
        assert blk["argv"].count("--inspect-dir") == 2, (
            f"each of 3 members should get 2 --inspect-dir flags; "
            f"cwd={blk['cwd']} argv={blk['argv']}"
        )


def test_brief_prepended_to_each_childs_prompt(tmp_path: Path) -> None:
    """--brief <file> content is prepended to every member's prompt."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _make_git_repo(repo_a)
    _make_git_repo(repo_b)
    brief = tmp_path / "brief.md"
    brief.write_text("shared cross-repo context\n")
    stub, log = _stub_recorder(tmp_path)

    result = _run(
        tmp_path,
        ["--group", "--group-id", GROUP_ID,
         "--brief", str(brief),
         "--repo", str(repo_a), "task a",
         "--repo", str(repo_b), "task b"],
        stub=stub, log=log,
    )
    assert result.returncode == 0, result.stderr
    log_text = result.stub_log
    # Brief text appears in both children's ARGV lines.
    assert log_text.count("shared cross-repo context") >= 2


def test_brief_prepended_before_member_prompt(tmp_path: Path) -> None:
    """The brief comes before the member-specific prompt in the full log."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _make_git_repo(repo_a)
    _make_git_repo(repo_b)
    brief = tmp_path / "brief.md"
    brief.write_text("BRIEF_MARKER")
    stub, log = _stub_recorder(tmp_path)

    result = _run(
        tmp_path,
        ["--group", "--group-id", GROUP_ID,
         "--brief", str(brief),
         "--repo", str(repo_a), "PROMPT_A",
         "--repo", str(repo_b), "PROMPT_B"],
        stub=stub, log=log,
    )
    assert result.returncode == 0, result.stderr
    log_text = result.stub_log
    # The brief marker appears before each member's own prompt token in
    # the log. Because the prompt is passed as a single argv word
    # ("BRIEF_MARKER PROMPT_A"), both tokens live in the same ARGV
    # segment — BRIEF_MARKER at a lower string offset than PROMPT_A/B.
    assert log_text.index("BRIEF_MARKER") < log_text.index("PROMPT_A"), (
        "brief must precede member prompt A in log"
    )
    assert log_text.index("BRIEF_MARKER") < log_text.index("PROMPT_B"), (
        "brief must precede member prompt B in log"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_blocks(log_text: str) -> list[dict]:
    """Parse the stub log into per-child dicts with 'cwd' and 'argv' keys.

    Each child appends:
        CWD:<path>
        ARGV:<space-separated args>
    """
    blocks: list[dict] = []
    lines = log_text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith("CWD:"):
            cwd = lines[i][4:]
            argv = lines[i + 1][5:] if (i + 1 < len(lines) and lines[i + 1].startswith("ARGV:")) else ""
            blocks.append({"cwd": cwd, "argv": argv})
            i += 2
        else:
            i += 1
    return blocks


def _find_block(blocks: list[dict], cwd: str) -> dict | None:
    """Return the block whose cwd matches (exact or resolves to the same path)."""
    for blk in blocks:
        if blk["cwd"] == cwd or Path(blk["cwd"]).resolve() == Path(cwd).resolve():
            return blk
    return None
