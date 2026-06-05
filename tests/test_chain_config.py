"""Tests for chain.config.load_settings()."""

import importlib
import os
import sys

import pytest

import chain.config


def test_import_succeeds() -> None:
    assert chain.config is not None


def test_load_settings_reads_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_DISPATCH_PAT", "gh-pat-value")
    monkeypatch.setenv("FLY_API_TOKEN", "fly-token-value")
    monkeypatch.setenv("CHAIN_WEBHOOK_SECRET", "webhook-secret-value")
    settings = chain.config.load_settings()
    assert settings.gh_dispatch_pat == "gh-pat-value"
    assert settings.fly_api_token == "fly-token-value"
    assert settings.chain_webhook_secret == "webhook-secret-value"


def test_load_settings_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_DISPATCH_PAT", "  gh-pat-value  ")
    monkeypatch.setenv("FLY_API_TOKEN", "  fly-token-value  ")
    monkeypatch.setenv("CHAIN_WEBHOOK_SECRET", "  secret  ")
    settings = chain.config.load_settings()
    assert settings.gh_dispatch_pat == "gh-pat-value"
    assert settings.fly_api_token == "fly-token-value"
    assert settings.chain_webhook_secret == "secret"


def test_load_settings_raises_on_missing_var(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("GH_DISPATCH_PAT", "FLY_API_TOKEN", "CHAIN_WEBHOOK_SECRET"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit) as exc_info:
        chain.config.load_settings()
    assert exc_info.value.code == 1


def test_load_settings_raises_on_empty_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_DISPATCH_PAT", "")
    monkeypatch.setenv("FLY_API_TOKEN", "fly-token-value")
    monkeypatch.setenv("CHAIN_WEBHOOK_SECRET", "webhook-secret-value")
    with pytest.raises(SystemExit) as exc_info:
        chain.config.load_settings()
    assert exc_info.value.code == 1


def test_load_settings_error_message_names_missing_var(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("GH_DISPATCH_PAT", "gh-pat-value")
    monkeypatch.delenv("FLY_API_TOKEN", raising=False)
    monkeypatch.setenv("CHAIN_WEBHOOK_SECRET", "webhook-secret-value")
    with pytest.raises(SystemExit):
        chain.config.load_settings()
    captured = capsys.readouterr()
    assert "FLY_API_TOKEN" in captured.err
    assert "leerie-chain: error:" in captured.err


def test_settings_is_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_DISPATCH_PAT", "gh-pat-value")
    monkeypatch.setenv("FLY_API_TOKEN", "fly-token-value")
    monkeypatch.setenv("CHAIN_WEBHOOK_SECRET", "webhook-secret-value")
    settings = chain.config.load_settings()
    with pytest.raises((AttributeError, TypeError)):
        settings.gh_dispatch_pat = "modified"  # type: ignore[misc]
