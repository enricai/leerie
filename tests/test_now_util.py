from datetime import datetime, timezone

from orchestrator import leerie


def test_now_returns_iso8601_utc_string():
    before = datetime.now(timezone.utc)
    result = leerie.now()
    after = datetime.now(timezone.utc)

    assert isinstance(result, str)
    parsed = datetime.fromisoformat(result)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
    assert before <= parsed <= after
