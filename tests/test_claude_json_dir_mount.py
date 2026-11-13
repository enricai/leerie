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


def test_claude_json_still_written_at_the_stage_root():
    """$STAGE is also the exact tree seed-auth.sh/ec2-seed-auth.sh
    tar-pipe wholesale to Fly/EC2; .claude.json must stay at $STAGE's
    root (not move into a subdirectory) or the remote seeding paths would
    silently relocate it too."""
    text = _launcher_text()
    assert '"$STAGE/.claude.json"' in text
    assert 'mkdir "$STAGE/claude-json-src"' not in text


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


def _entry_sh_claude_json_copy_block() -> str:
    text = _entry_sh_text()
    marker = "if [ -d /opt/leerie-home-src ]; then"
    start = text.index(marker)
    end = text.index("\nfi", start)
    return text[start:end]


def test_container_entry_copies_the_staged_home_into_place():
    block = _entry_sh_claude_json_copy_block()
    assert 'cp -a "$_e" /home/leerie/' in block


def test_container_entry_copy_step_does_not_itself_chown():
    """The copy runs unconditionally (regardless of ROOTLESS); ownership
    is handled entirely by the shared rootful guard below it, not by a
    second, separate ROOTLESS check duplicated here."""
    block = _entry_sh_claude_json_copy_block()
    assert "chown" not in block
    assert "ROOTLESS" not in block


def test_container_entry_chowns_the_home_in_the_shared_rootful_guard():
    block = _entry_sh_rootful_block()
    assert "chown -R leerie: /home/leerie " in block


def test_container_entry_claude_json_copy_precedes_privilege_drop():
    text = _entry_sh_text()
    copy_idx = text.index('cp -a "$_e" /home/leerie/')
    unshare_idx = text.index("unshare --user --map-user=")
    orchestrator_idx = text.rindex(
        "python3 /opt/leerie-image/orchestrator/leerie.py"
    )
    assert copy_idx < unshare_idx
    assert copy_idx < orchestrator_idx


def test_container_entry_claude_json_copy_precedes_the_rootful_chown():
    text = _entry_sh_text()
    copy_idx = text.index('cp -a "$_e" /home/leerie/')
    chown_idx = text.index("chown -R leerie: /home/leerie ")
    assert copy_idx < chown_idx


# ---------------------------------------------------------------------------
# container-entry.sh — behavioral: run the extracted blocks for real, under
# simulated rootless and rootful environments.
# ---------------------------------------------------------------------------


def _run_copy_and_ownership(
    rootless: bool, has_leerie_user: bool
) -> tuple[bool, str | None, bool]:
    """Execute the real copy block plus the real shared rootful-guard
    block from container-entry.sh, back to back (as they run in the
    script), in a throwaway sh with `cp`/`chown`/`getent` faked onto PATH
    so no real privilege changes happen. Returns (dest_exists,
    dest_content, chown_invoked_on_claude_json) — read out *inside* the
    tempdir's lifetime, since the tempdir (and everything under it,
    including the dest file) is removed the moment the `with` block
    exits."""
    copy_block = _entry_sh_claude_json_copy_block() + "\nfi\n"
    rootful_block = _entry_sh_rootful_block() + "\nfi\n"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        src_dir = tmp_path / "opt" / "leerie-home-src"
        src_dir.mkdir(parents=True)
        (src_dir / ".claude.json").write_text('{"ok": true}')
        (src_dir / "leerie-code").mkdir()
        dest_dir = tmp_path / "home" / "leerie"
        dest_dir.mkdir(parents=True)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        chown_log = tmp_path / "chown.log"

        # Fake getent: succeeds (user "leerie" exists) iff has_leerie_user.
        getent_rc = 0 if has_leerie_user else 1
        (bin_dir / "getent").write_text(f"#!/bin/sh\nexit {getent_rc}\n")
        (bin_dir / "getent").chmod(0o755)

        (bin_dir / "chown").write_text(
            f'#!/bin/sh\necho "$@" >> "{chown_log}"\nexit 0\n'
        )
        (bin_dir / "chown").chmod(0o755)

        def _rewrite(text: str) -> str:
            return text.replace(
                "/opt/leerie-home-src", str(src_dir),
            ).replace(
                "/home/leerie/", str(dest_dir) + "/"
            ).replace(
                "/home/leerie ", str(dest_dir) + " "
            ).replace(
                "/work ", str(tmp_path / "work") + " "
            ).replace(
                "/tmp/.cache", str(tmp_path / "tmpcache")
            )

        script = (
            "#!/bin/sh\nset -e\n"
            f'ROOTLESS={"true" if rootless else "false"}\n'
            f'export PATH="{bin_dir}:$PATH"\n'
            + _rewrite(copy_block)
            + "\n"
            + _rewrite(rootful_block)
        )
        subprocess.run(
            ["sh", "-c", script], check=True, cwd=tmp_path, timeout=10
        )
        dest_file = dest_dir / ".claude.json"
        dest_exists = dest_file.exists()
        dest_content = dest_file.read_text() if dest_exists else None
        # The home is chowned whole (`chown -R leerie: <home>`).
        chown_invoked = chown_log.exists() and any(
            line.split()[-1] == str(dest_dir)
            for line in chown_log.read_text().splitlines()
            if line.startswith("-R "))
        assert not (dest_dir / "leerie-code").exists()
        return dest_exists, dest_content, chown_invoked


def test_copy_happens_and_content_matches_under_rootless():
    exists, content, chowned = _run_copy_and_ownership(
        rootless=True, has_leerie_user=True
    )
    assert exists
    assert content == '{"ok": true}'
    # Rootless: must NOT chown to leerie — a root-owned file is what maps
    # correctly through the single-entry unshare --map-user remap; an
    # explicit chown to the image's literal leerie UID would make it
    # appear as nobody/65534 inside the remapped namespace.
    assert chowned is False


def test_copy_happens_and_chowns_under_rootful():
    exists, content, chowned = _run_copy_and_ownership(
        rootless=False, has_leerie_user=True
    )
    assert exists
    assert content == '{"ok": true}'
    # Rootful: `runuser -u leerie` is a real uid switch with no remap, so
    # the copied file must be explicitly chowned to leerie.
    assert chowned is True


def test_copy_is_a_noop_when_source_mount_absent():
    """Fly/EC2 seed .claude.json directly; no such mount exists there, so
    the copy step must not error when the source is missing."""
    block = _entry_sh_claude_json_copy_block() + "\nfi\n"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dest_dir = tmp_path / "home" / "leerie"
        dest_dir.mkdir(parents=True)
        script = (
            "#!/bin/sh\nset -e\nROOTLESS=false\n"
            + block.replace(
                "/opt/leerie-home-src", str(tmp_path / "nonexistent"),
            ).replace(
                "/home/leerie/", str(dest_dir) + "/"
            )
        )
        subprocess.run(["sh", "-c", script], check=True, cwd=tmp_path, timeout=10)
        assert not (dest_dir / ".claude.json").exists()
