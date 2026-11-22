import asyncio, types, pytest
from orchestrator import leerie


def _drive(monkeypatch, rc, out, err):
    async def fake(argv, *a, **k):
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)
    monkeypatch.setattr(leerie, "_run_proc", fake)
    msgs = []
    def d(m):
        msgs.append(m); raise SystemExit(1)
    monkeypatch.setattr(leerie, "die", d)
    with pytest.raises(SystemExit):
        asyncio.run(leerie._preflight_repo())
    return msgs[0]


def test_rc128_is_unusable_not_unconfigured(monkeypatch):
    m = _drive(monkeypatch, 128, "", "fatal: not a git repository: /x")
    assert "not a usable git repository" in m
    assert "is not configured" not in m


def test_rc1_still_not_configured(monkeypatch):
    assert "is not configured" in _drive(monkeypatch, 1, "", "")
