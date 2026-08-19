"""The single derivation of the launcher's real `config)` case arm.

`test_config_verb.py` and `test_config_recapture.py` both extract this arm
verbatim from the shipped launcher (rather than hand-reproducing its logic,
which would be body-blind by construction — see `test_config_verb.py`'s
module docstring for the falsification proving this). Both copies were
byte-identical, which is the same drift risk `tests/launcher_blocks.py`
exists to close for the launch-env-block extraction: two copies of a rule
drift exactly the way two copies of a list do, so there is one copy, here.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def extract_config_arm() -> str:
    """Return the real `config)` case-arm body (including the `config)`
    pattern and trailing `;;`) verbatim from the shipped launcher."""
    launcher_text = (REPO_ROOT / "leerie").read_text()
    start_marker = "  config)\n"
    end_marker = "\n  list)"
    s = launcher_text.index(start_marker)
    e = launcher_text.index(end_marker, s)
    return launcher_text[s:e]
