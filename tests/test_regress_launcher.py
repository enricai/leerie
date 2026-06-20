import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "leerie"


def test_launcher_mounts_corpus_writable():
    """The launcher must add a writable /corpus bind-mount and set
    LEERIE_CORPUS_DIR so --corpus-capture can write back to the repo."""
    src = LAUNCHER.read_text()
    assert "/opt/leerie-image/corpus" not in src or "corpus:/corpus" in src
    assert "corpus:/corpus" in src, "writable /corpus mount missing"
    assert "LEERIE_CORPUS_DIR" in src


def test_launcher_skips_finalize_for_corpus_verbs():
    """--regress / --corpus-* must not trigger the host-side push+PR
    finalize block."""
    src = LAUNCHER.read_text()
    assert "LEERIE_CORPUS_VERB" in src


def test_argparse_accepts_regress_flags():
    """The orchestrator argparse must accept the new flags without error."""
    code = (
        "import sys; sys.path.insert(0,'orchestrator'); import leerie, argparse;"
        "ap=leerie._build_arg_parser();"
        "a=ap.parse_args(['--regress','--tier','all',"
        "'--call-type','classifier','--update-baseline']);"
        "assert a.regress and a.tier=='all' and a.regress_call_types==['classifier']"
        " and a.update_baseline;"
        "b=ap.parse_args(['--corpus-capture','r1','--case','smoke']);"
        "assert b.corpus_capture_from=='r1' and b.corpus_case_name=='smoke';"
        "c=ap.parse_args(['--corpus-list']); assert c.corpus_list;"
        "print('ok')"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


# --- REGR-05: resource hygiene of the --regress / --corpus-capture arms ---

def _drive_main(leerie, tmp_path, monkeypatch, argv):
    """Drive leerie.main() through the --regress / --corpus-capture
    dispatch with all live boundaries (claude lookup, corpus dir, state
    root) redirected at a throwaway tmp tree. Returns the resolved
    state root (leerie_root) so callers can inspect leerie_root/runs."""
    state_root = tmp_path / "state"
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state_root))
    monkeypatch.setenv("LEERIE_CORPUS_DIR", str(corpus_dir))
    monkeypatch.chdir(tmp_path)
    # main() refuses to proceed without a `claude` on PATH; the workers
    # are monkeypatched out below so the binary is never actually run.
    monkeypatch.setattr(leerie.shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(sys, "argv", ["leerie", *argv])
    return state_root


def test_regress_leaves_no_throwaway_dir(leerie, tmp_path, monkeypatch):
    """REGR-05: a --regress invocation must not leak its throwaway
    regress-<uuid> run dir on the state root."""
    state_root = _drive_main(
        leerie, tmp_path, monkeypatch,
        ["--run-id", "container-xyz", "--regress"])

    async def fake_phase_regress(*a, **k):
        return {"overall": "OK", "per_call_type": {}, "warnings": []}

    monkeypatch.setattr(leerie, "phase_regress", fake_phase_regress)

    leerie.main()

    runs = state_root / "runs"
    leftovers = list(runs.glob("regress-*"))
    assert leftovers == [], f"throwaway regress dir leaked: {leftovers}"


def test_regress_regressed_still_cleans_up_and_exits_12(leerie, tmp_path,
                                                        monkeypatch):
    """REGR-05: the EXIT_REGRESSED path still removes the throwaway dir
    AND preserves the exit code (cleanup runs before the sys.exit)."""
    state_root = _drive_main(
        leerie, tmp_path, monkeypatch,
        ["--run-id", "container-abc", "--regress"])

    async def fake_phase_regress(*a, **k):
        return {"overall": "REGRESSED", "per_call_type": {}, "warnings": []}

    monkeypatch.setattr(leerie, "phase_regress", fake_phase_regress)

    with pytest.raises(SystemExit) as exc:
        leerie.main()
    assert exc.value.code == leerie.EXIT_REGRESSED

    leftovers = list((state_root / "runs").glob("regress-*"))
    assert leftovers == [], f"throwaway regress dir leaked: {leftovers}"


def test_corpus_capture_locked_target_exits_locked(leerie, tmp_path,
                                                    monkeypatch):
    """REGR-05: --corpus-capture against a run dir whose flock is held by
    a live State must exit via EXIT_LOCKED, not raise StateLockedError."""
    target_run = "locked-run-1"
    state_root = _drive_main(
        leerie, tmp_path, monkeypatch,
        ["--run-id", "container-cap", "--corpus-capture", target_run])

    # corpus_capture must never run — the lock fires first. Guard against
    # a regression where the guard is missing and capture is attempted.
    async def fail_capture(*a, **k):
        raise AssertionError("corpus_capture ran despite a locked target")

    monkeypatch.setattr(leerie, "corpus_capture", fail_capture)

    # Hold the target run dir's exclusive flock for the duration of the
    # call by keeping a real State alive (its __init__ takes the flock).
    holder = leerie.State(state_root, target_run)
    try:
        with pytest.raises(SystemExit) as exc:
            leerie.main()
        assert exc.value.code == leerie.EXIT_LOCKED
    finally:
        holder.release_lock()
