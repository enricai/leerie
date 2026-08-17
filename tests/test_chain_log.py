"""Unit tests for chain/_log.py's package-isolated log()/die() helpers."""
from datetime import datetime

import pytest

from chain import _log


def test_log_writes_prefixed_message_to_stdout(capsys):
    _log.log("hello world")
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "[chain] hello world" in captured.out
    assert captured.out.rstrip("\n").endswith("hello world")


def test_die_writes_prefixed_message_to_stderr_and_exits_default_code(capsys):
    with pytest.raises(SystemExit) as exc_info:
        _log.die("bad input")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "leerie-chain: error: bad input" in captured.err


def test_die_exits_with_given_code(capsys):
    with pytest.raises(SystemExit) as exc_info:
        _log.die("bad", code=2)
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "leerie-chain: error: bad" in captured.err


def test_iso_now_returns_parseable_iso8601_timestamp():
    ts = _log._iso_now()
    assert isinstance(ts, str)
    assert ts.endswith("Z")
    parsed = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    assert parsed.year >= 2024


def test_log_message_prefixed_with_iso_now_timestamp(capsys):
    _log.log("timestamped")
    captured = capsys.readouterr()
    line = captured.out.rstrip("\n")
    ts_part, rest = line.split(" ", 1)
    datetime.strptime(ts_part, "%Y-%m-%dT%H:%M:%SZ")
    assert rest == "[chain] timestamped"
