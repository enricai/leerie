import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "leerie", Path(__file__).resolve().parent.parent / "orchestrator" / "leerie.py"
)
leerie = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(leerie)


def test_sentinel_lines_resolve_to_no_path():
    for line in ["- None.", "- none.", "- N/A", "- (none)", "- na", "- -", "- nothing"]:
        path, is_deleted = leerie._parse_touched_file_line(line)
        assert path is None, f"{line!r} should not resolve to a path, got {path!r}"


def test_real_paths_still_resolve():
    cases = {
        "- ./src/app.ts": "./src/app.ts",
        "- ../lib/x.ts": "../lib/x.ts",
        "- .github/workflows/ci.yml": ".github/workflows/ci.yml",
        "- README.md": "README.md",
    }
    for line, expected in cases.items():
        path, _ = leerie._parse_touched_file_line(line)
        assert path == expected


def test_narration_without_path_token_is_skipped():
    path, _ = leerie._parse_touched_file_line("- refactored the module")
    assert path is None
