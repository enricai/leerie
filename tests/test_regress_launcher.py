import subprocess
import sys
from pathlib import Path

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
