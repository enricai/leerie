"""N8: the launcher's `finalize: skipped` line must announce the run id
when it's known — the launcher-side half of the die()/run-id fix in
orchestrator/leerie.py.

Extracts the real `if [ "$container_rc" -ne 0 ] ...` block verbatim from
`leerie` (the same source-slicing discipline used throughout this suite —
see e.g. tests/test_config_verb.py's `_extract_config_arm`) rather than
reproducing it, so a future edit to the launcher can't silently drift from
what these tests pin. Sources scripts/remote/_log.sh first so the real
`remote_log` helper is in scope, mirroring tests/test_remote_log.py's
prefix-shape assertions.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
LOG_SH = REPO_ROOT / "scripts" / "remote" / "_log.sh"

START_MARKER = 'if [ "$container_rc" -ne 0 ] || [ "$NO_PUSH" = "true" ]; then'

PREFIX_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2} "
    r"\[leerie\] \[(?P<repo>[^\]]+)\] (?P<body>.*)$"
)


def _extract_finalize_skipped_block() -> str:
    """Pull the `if [ "$container_rc" -ne 0 ] ... fi` block out of the real
    launcher by brace/if-fi depth counting on the shell keywords, starting
    at the first line equal to START_MARKER."""
    lines = LAUNCHER.read_text().splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == START_MARKER:
            start = i
            break
    assert start is not None, "finalize:skipped block not found in launcher"

    depth = 0
    end = None
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if re.match(r"^(if\b.*then|for\b.*do|while\b.*do)$", stripped) or stripped.endswith("; then") or stripped.endswith("; do"):
            depth += 1
        elif stripped in ("fi", "done"):
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None, "unterminated finalize:skipped block"
    return "\n".join(lines[start:end + 1])


BLOCK = _extract_finalize_skipped_block()


def test_block_uses_remote_log_not_bare_echo():
    # A bare `echo "[leerie] ..."` carries no timestamp and can't carry a
    # run id consistently with the rest of the launcher's log lines.
    assert "remote_log " in BLOCK
    assert 'echo "[leerie] finalize: skipped' not in BLOCK


def test_block_reads_run_id_from_leerie_run_id_or_cidfile():
    assert "LEERIE_RUN_ID" in BLOCK
    assert "$_cidfile" in BLOCK


def _run(container_rc: str, no_push: str = "false", leerie_run_id: str = "",
          cidfile_content: str | None = None, tmp_path: Path | None = None) -> str:
    env = {"PATH": "/usr/bin:/bin"}
    cidfile_path = ""
    if cidfile_content is not None:
        assert tmp_path is not None
        cidfile = tmp_path / "cidfile"
        cidfile.write_text(cidfile_content)
        cidfile_path = str(cidfile)

    script = (
        f"source {LOG_SH}; "
        f'container_rc="{container_rc}"; '
        f'NO_PUSH="{no_push}"; '
        f'LEERIE_RUN_ID="{leerie_run_id}"; '
        f'_cidfile="{cidfile_path}"; '
        f"{BLOCK}"
    )
    r = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
    )
    return r.stderr.rstrip("\n")


def test_includes_run_id_from_leerie_run_id_when_set(tmp_path):
    out = _run(container_rc="1", leerie_run_id="run-explicit-123", tmp_path=tmp_path)
    m = PREFIX_RE.match(out)
    assert m, f"no prefix match: {out!r}"
    assert "run-explicit-123" in m.group("body")
    assert "finalize: skipped" in m.group("body")


def test_falls_back_to_cidfile_when_leerie_run_id_unset(tmp_path):
    out = _run(container_rc="1", leerie_run_id="", cidfile_content="abc123containerid",
               tmp_path=tmp_path)
    m = PREFIX_RE.match(out)
    assert m, f"no prefix match: {out!r}"
    assert "abc123containerid" in m.group("body")


def test_leerie_run_id_wins_over_cidfile(tmp_path):
    out = _run(container_rc="1", leerie_run_id="run-explicit-123",
               cidfile_content="abc123containerid", tmp_path=tmp_path)
    m = PREFIX_RE.match(out)
    assert m
    assert "run-explicit-123" in m.group("body")
    assert "abc123containerid" not in m.group("body")


def test_no_run_id_available_still_emits_plain_message(tmp_path):
    out = _run(container_rc="1", leerie_run_id="", cidfile_content=None,
               tmp_path=tmp_path)
    m = PREFIX_RE.match(out)
    assert m, f"no prefix match: {out!r}"
    assert m.group("body") == "finalize: skipped — container exited with code 1"


def test_no_message_when_container_rc_is_zero(tmp_path):
    out = _run(container_rc="0", no_push="true", leerie_run_id="run-x", tmp_path=tmp_path)
    assert out == ""
