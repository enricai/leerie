"""Regression pins for N19: .claude.json must never be bind-mounted as a
single file.

Root cause: the CLI rewrites `~/.claude.json` via a rename()-based atomic
write (tmp file + rename() onto the target). `rename()` onto a
bind-mounted single file returns EBUSY, forcing the CLI to fall back to
truncate-in-place — not atomic, and demonstrated to leave an empty file
under concurrent workers. The fix bind-mounts the whole per-run host
scratch dir ($STAGE) read-only at a staging path instead of mounting
.claude.json directly, and has `container-entry.sh` copy the file into
place as a real file inside the container's own filesystem, mirroring
the tar-copy pattern `scripts/remote/seed-auth.sh`/
`scripts/remote/ec2-seed-auth.sh` already use for the remote runtimes
(unaffected by this defect — they tar-copy $STAGE wholesale and never
bind-mount .claude.json at all).
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from tests.stub_helpers import _entry_sh_rootful_block

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
ENTRY_SH = REPO_ROOT / "scripts" / "container-entry.sh"


def _launcher_text() -> str:
    return LAUNCHER.read_text()


def _entry_sh_text() -> str:
    return ENTRY_SH.read_text()


# ---------------------------------------------------------------------------
# Launcher (mount construction) — static pins
# ---------------------------------------------------------------------------


def test_claude_json_is_never_bind_mounted_as_a_single_file():
    """The historical, corrupting mount form must not reappear anywhere in
    the launcher: a `-v` flag whose host side is a `.claude.json` path and
    whose container side is `/home/leerie/.claude.json`."""
    text = _launcher_text()
    assert '.claude.json:/home/leerie/.claude.json' not in text


def test_claude_json_written_in_the_cli_class():
    """.claude.json is the CLI's own (DESIGN §6 *A repository command gets
    nothing the operator handed leerie for itself*), staged under
    $STAGE_CLI — not at $STAGE's root, where the entry would copy it into
    the commands' pristine home too, and not the repository's class."""
    text = _launcher_text()
    assert '"$STAGE_CLI/.claude.json"' in text
    # Never at the stage root (the entry's cli-vs-home split keys on it).
    assert '"$STAGE/.claude.json"' not in text


def test_stage_mounted_as_a_readonly_directory_for_the_local_container():
    text = _launcher_text()
    assert 'AUTH_MOUNTS+=(-v "$STAGE:/opt/leerie-home-src:ro")' in text


def test_claude_dir_is_copied_not_mounted():
    """`.claude/` reaches the container as a copy too (DESIGN §6 *Every
    confined process has its own home*): no mount of it remains."""
    text = _launcher_text()
    assert '"$STAGE/.claude:/home/leerie/.claude"' not in text


# ---------------------------------------------------------------------------
# container-entry.sh — static pins
# ---------------------------------------------------------------------------


_CLI_COPY = "_copy_dots /opt/leerie-home-src/cli /home/leerie"
_HOME_COPY = "_copy_dots /opt/leerie-home-src/home /home/leerie"
_CMD_HOME_BUILD = '_copy_dots /home/leerie "$LEERIE_COMMAND_HOME"'


def _entry_sh_copy_region() -> str:
    """The copy region: the `_copy_dots` helper, the commands'-home build,
    and the staged-class copies."""
    text = _entry_sh_text()
    start = text.index("_copy_dots() {")
    end = text.index("_copy_dots /opt/leerie-home-src/cli /home/leerie")
    end = text.index("\nfi", end) + len("\nfi")
    return text[start:end]


def test_container_entry_copies_each_staged_class_into_place():
    """The CLI's own class (`cli/`) and the repository's (`home/`) are
    copied into the workers' home; nothing is bind-mounted beneath it."""
    text = _entry_sh_text()
    assert _CLI_COPY in text
    assert _HOME_COPY in text


def test_container_entry_builds_the_commands_home_from_image_plus_home_class():
    """The commands' pristine home is the image's home and `home/` only —
    never `cli/`, so the operator's login (`.claude/.credentials.json`) is
    not in it (DESIGN §6 *A repository command gets nothing the operator
    handed leerie for itself*)."""
    text = _entry_sh_text()
    build_idx = text.index(_CMD_HOME_BUILD)
    home_into_cmd = text.index(
        '_copy_dots /opt/leerie-home-src/home "$LEERIE_COMMAND_HOME"')
    assert build_idx < home_into_cmd
    # cli/ goes into /home/leerie only, never the commands' home.
    assert '_copy_dots /opt/leerie-home-src/cli "$LEERIE_COMMAND_HOME"' \
        not in text


def test_container_entry_copy_step_does_not_itself_chown():
    """The copy runs unconditionally (regardless of ROOTLESS); ownership
    is handled by the shared rootful guard below it."""
    block = _entry_sh_copy_region()
    assert "chown" not in block
    assert "ROOTLESS" not in block


def test_container_entry_chowns_both_homes_in_the_shared_rootful_guard():
    block = _entry_sh_rootful_block()
    assert "chown -R leerie: /home/leerie " in block
    assert 'chown -R leerie: "$LEERIE_COMMAND_HOME"' in block


def test_container_entry_copy_precedes_privilege_drop():
    text = _entry_sh_text()
    copy_idx = text.index(_CLI_COPY)
    unshare_idx = text.index("unshare --user --map-user=")
    orchestrator_idx = text.rindex(
        "python3 /opt/leerie-image/orchestrator/leerie.py"
    )
    assert copy_idx < unshare_idx
    assert copy_idx < orchestrator_idx


def test_container_entry_copy_precedes_the_rootful_chown():
    text = _entry_sh_text()
    copy_idx = text.index(_CLI_COPY)
    chown_idx = text.index("chown -R leerie: /home/leerie ")
    assert copy_idx < chown_idx


# ---------------------------------------------------------------------------
# container-entry.sh — behavioral: run the extracted blocks for real, under
# simulated rootless and rootful environments.
# ---------------------------------------------------------------------------


def _run_entry_copies(rootless: bool) -> dict:
    """Run the real entry copy region (the `_copy_dots` helper, the
    commands'-home build, the staged-class copies) plus the shared rootful
    guard, back to back as the entry runs them, in a throwaway sh with
    `cp`/`chown`/`getent` faked onto PATH. The staged directory holds the
    CLI's login (`cli/.claude/.credentials.json`) and a repository file
    (`home/.gitconfig`). Returns what landed in each home and which homes
    were chowned. Read out inside the tempdir's lifetime."""
    region = _entry_sh_copy_region()
    rootful_block = _entry_sh_rootful_block() + "\nfi\n"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "opt" / "leerie-home-src"
        (src / "cli" / ".claude").mkdir(parents=True)
        (src / "cli" / ".claude" / ".credentials.json").write_text("LOGIN")
        (src / "cli" / ".claude.json").write_text('{"ok": true}')
        (src / "home").mkdir()
        (src / "home" / ".gitconfig").write_text("[user]\n")
        (src / "leerie-code").mkdir()           # neither class: not copied
        home = tmp_path / "home" / "leerie"
        home.mkdir(parents=True)
        (home / ".image-file").write_text("img")   # the image's own home
        cmd_home = tmp_path / "leerie-command-home"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        chown_log = tmp_path / "chown.log"
        (bin_dir / "getent").write_text("#!/bin/sh\nexit 0\n")
        (bin_dir / "getent").chmod(0o755)
        (bin_dir / "chown").write_text(
            f'#!/bin/sh\necho "$@" >> "{chown_log}"\nexit 0\n')
        (bin_dir / "chown").chmod(0o755)

        def _rw(text: str) -> str:
            return (text.replace("/opt/leerie-home-src", str(src))
                        .replace("/home/leerie", str(home))
                        .replace("/work ", str(tmp_path / "work") + " ")
                        .replace("/tmp/.cache", str(tmp_path / "tmpcache")))

        script = (
            "#!/bin/sh\nset -e\n"
            f'ROOTLESS={"true" if rootless else "false"}\n'
            f'export LEERIE_COMMAND_HOME="{cmd_home}"\n'
            f'export PATH="{bin_dir}:$PATH"\n'
            + _rw(region) + "\n" + _rw(rootful_block))
        subprocess.run(["sh", "-c", script], check=True, cwd=tmp_path,
                       timeout=10)

        def _chowned(path: Path) -> bool:
            return chown_log.exists() and any(
                line.split()[-1] == str(path)
                for line in chown_log.read_text().splitlines()
                if line.startswith("-R "))
        return {
            "worker_login": (home / ".claude" / ".credentials.json").exists(),
            "worker_claude_json": (home / ".claude.json").exists(),
            "worker_gitconfig": (home / ".gitconfig").exists(),
            "cmd_login": (cmd_home / ".claude" / ".credentials.json").exists(),
            "cmd_claude_json": (cmd_home / ".claude.json").exists(),
            "cmd_gitconfig": (cmd_home / ".gitconfig").exists(),
            "cmd_image_file": (cmd_home / ".image-file").exists(),
            "worker_leerie_code": (home / "leerie-code").exists(),
            "worker_chowned": _chowned(home),
            "cmd_chowned": _chowned(cmd_home),
        }


def test_the_login_reaches_the_worker_home_but_not_the_commands_home():
    """The two-sided security property at the entry (DESIGN §6 *A
    repository command gets nothing the operator handed leerie for
    itself*): the CLI's login is in the workers' home and absent from the
    commands' pristine home, while a repository file (.gitconfig) is in
    both. This is the regression anchor against a future change leaking the
    credential back into the command home."""
    for rootless in (True, False):
        r = _run_entry_copies(rootless)
        # Worker home: both the login (cli class) and the repo file.
        assert r["worker_login"] and r["worker_claude_json"], r
        assert r["worker_gitconfig"], r
        # Commands' home: the repo file and the image's own, never the login.
        assert r["cmd_gitconfig"] and r["cmd_image_file"], r
        assert not r["cmd_login"], r
        assert not r["cmd_claude_json"], r
        # `leerie-code` is neither class and is copied nowhere.
        assert not r["worker_leerie_code"], r


def test_both_homes_chowned_under_rootful_not_rootless():
    """Rootless: a root-owned copy maps to leerie through the single-entry
    unshare remap, so NO chown (CLAUDE.md's Dockerfile:235-241 trap).
    Rootful: `runuser -u leerie` is a real uid switch, so BOTH the workers'
    home and the commands' home are chowned to leerie."""
    rootless = _run_entry_copies(True)
    assert rootless["worker_chowned"] is False
    assert rootless["cmd_chowned"] is False
    rootful = _run_entry_copies(False)
    assert rootful["worker_chowned"] is True
    assert rootful["cmd_chowned"] is True


def test_copy_is_a_noop_when_source_mount_absent():
    """Fly/EC2 seed the stage differently; no `/opt/leerie-home-src` mount
    exists there, so the staged-class copy must not error when the source
    is missing (the commands'-home build still runs from the image home)."""
    region = _entry_sh_copy_region()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        home = tmp_path / "home" / "leerie"
        home.mkdir(parents=True)
        cmd_home = tmp_path / "leerie-command-home"
        script = (
            "#!/bin/sh\nset -e\n"
            f'export LEERIE_COMMAND_HOME="{cmd_home}"\n'
            + region.replace(
                "/opt/leerie-home-src", str(tmp_path / "nonexistent"))
                    .replace("/home/leerie", str(home)))
        subprocess.run(["sh", "-c", script], check=True, cwd=tmp_path,
                       timeout=10)
        assert not (home / ".claude.json").exists()
        assert cmd_home.is_dir()
