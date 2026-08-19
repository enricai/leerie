"""The single derivation of the launcher's detect_bedrock_mode()/bedrock_preflight()
functions, extracted verbatim for test harnesses that need real implementations
rather than stubs.

Previously duplicated byte-for-byte in tests/test_bedrock_bearer_token.py and
tests/test_bedrock_mode.py — same single-owner discipline as
tests/launcher_blocks.py and tests/ec2_stub.py.
"""
from __future__ import annotations

from pathlib import Path

LAUNCHER_PATH = Path(__file__).resolve().parents[1] / "leerie"


def extract_bedrock_functions() -> str:
    """Pull detect_bedrock_mode() and bedrock_preflight() verbatim from the
    launcher so the harness has real implementations, not stubs, for the
    SSO/profile control-flow paths."""
    src = LAUNCHER_PATH.read_text()
    start = src.index("detect_bedrock_mode() {")
    end = src.index("\n}\n", src.index("bedrock_preflight() {")) + len("\n}\n")
    block = src[start:end]
    assert "detect_bedrock_mode() {" in block
    assert "bedrock_preflight() {" in block
    return block
