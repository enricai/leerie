"""The re-drive routing filter admits any language, never tests or vendored paths."""
from orchestrator import leerie


def test_redrive_routable_language_agnostic():
    for p in ["a.go", "src/x.rs", "app/Main.java", "lib/a.rb"]:
        assert leerie._redrive_routable(p)
    for p in ["", "  ", "node_modules/x.js", "tests/test_a.py", ".venv/lib/a.py"]:
        assert not leerie._redrive_routable(p)
