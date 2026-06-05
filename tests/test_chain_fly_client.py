"""Tests for chain.fly_client — Fly Machines API client.

HTTP transport is stubbed at the urllib.request.urlopen boundary so no live
Fly API calls are made.
"""
from __future__ import annotations

import json
import os
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import chain.fly_client as fly_client
from chain.fly_client import FlyClientError, destroy_machine, get_machine_state, launch_machine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_response(data: Any, status: int = 200) -> MagicMock:
    body = json.dumps(data).encode()
    mock = MagicMock()
    mock.read.return_value = body
    mock.status = status
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def _http_error(code: int, message: str = "error") -> "urllib.error.HTTPError":
    import urllib.error
    return urllib.error.HTTPError(
        url="https://api.machines.dev/v1/...",
        code=code,
        msg=message,
        hdrs=None,  # type: ignore[arg-type]
        fp=BytesIO(message.encode()),
    )


# ---------------------------------------------------------------------------
# Missing token
# ---------------------------------------------------------------------------

class TestMissingToken:
    def test_launch_machine_raises_on_missing_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FLY_API_TOKEN", raising=False)
        with pytest.raises(FlyClientError, match="FLY_API_TOKEN"):
            launch_machine("registry.fly.io/leerie:0.1.0", {}, "iad")

    def test_get_machine_state_raises_on_missing_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FLY_API_TOKEN", raising=False)
        with pytest.raises(FlyClientError, match="FLY_API_TOKEN"):
            get_machine_state("abc123")

    def test_destroy_machine_raises_on_missing_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FLY_API_TOKEN", raising=False)
        with pytest.raises(FlyClientError, match="FLY_API_TOKEN"):
            destroy_machine("abc123")

    def test_empty_token_raises_clear_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "   ")
        with pytest.raises(FlyClientError, match="FLY_API_TOKEN"):
            launch_machine("registry.fly.io/leerie:0.1.0", {}, "iad")


# ---------------------------------------------------------------------------
# launch_machine
# ---------------------------------------------------------------------------

class TestLaunchMachine:
    def test_issues_post_to_correct_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "test-token")
        monkeypatch.setenv("FLY_APP_NAME", "leerie-test")

        captured: list[Any] = []

        def fake_urlopen(req: Any) -> Any:
            captured.append(req)
            return _fake_response({"id": "machine-abc", "state": "created"})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            machine_id = launch_machine(
                "registry.fly.io/leerie:0.2.0",
                {"LEERIE_RUN_ID": "run-123"},
                "iad",
            )

        assert machine_id == "machine-abc"
        assert len(captured) == 1
        req = captured[0]
        assert req.get_method() == "POST"
        assert "/apps/leerie-test/machines" in req.full_url

    def test_sends_image_env_region_in_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "test-token")
        monkeypatch.setenv("FLY_APP_NAME", "leerie")

        captured_body: list[dict[str, Any]] = []

        def fake_urlopen(req: Any) -> Any:
            captured_body.append(json.loads(req.data))
            return _fake_response({"id": "m1", "state": "created"})

        env = {"KEY": "val", "RUN": "x"}
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            launch_machine("registry.fly.io/leerie:1.0.0", env, "lhr")

        body = captured_body[0]
        assert body["config"]["image"] == "registry.fly.io/leerie:1.0.0"
        assert body["config"]["env"] == env
        assert body["region"] == "lhr"

    def test_sends_guest_config_in_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "test-token")
        monkeypatch.setenv("FLY_APP_NAME", "leerie")

        captured_body: list[dict[str, Any]] = []

        def fake_urlopen(req: Any) -> Any:
            captured_body.append(json.loads(req.data))
            return _fake_response({"id": "m1", "state": "created"})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            launch_machine(
                "registry.fly.io/leerie:1.0.0",
                {},
                "iad",
                vm_cpus=2,
                vm_memory_mb=4096,
            )

        guest = captured_body[0]["config"]["guest"]
        assert guest["cpus"] == 2
        assert guest["memory_mb"] == 4096

    def test_sends_auth_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "secret-fly-token")
        monkeypatch.setenv("FLY_APP_NAME", "leerie")

        captured: list[Any] = []

        def fake_urlopen(req: Any) -> Any:
            captured.append(req)
            return _fake_response({"id": "m2", "state": "created"})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            launch_machine("registry.fly.io/leerie:1.0.0", {}, "iad")

        req = captured[0]
        assert req.get_header("Authorization") == "Bearer secret-fly-token"

    def test_returns_machine_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "tok")
        monkeypatch.setenv("FLY_APP_NAME", "leerie")

        with patch(
            "urllib.request.urlopen",
            return_value=_fake_response({"id": "xyz-machine-id", "state": "created"}),
        ):
            result = launch_machine("registry.fly.io/leerie:0.1.0", {}, "iad")

        assert result == "xyz-machine-id"

    def test_raises_on_api_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "tok")
        monkeypatch.setenv("FLY_APP_NAME", "leerie")

        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error(422, "invalid image"),
        ):
            with pytest.raises(FlyClientError, match="422"):
                launch_machine("bad-image", {}, "iad")

    def test_raises_when_response_missing_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "tok")
        monkeypatch.setenv("FLY_APP_NAME", "leerie")

        with patch(
            "urllib.request.urlopen",
            return_value=_fake_response({"state": "created"}),  # no 'id'
        ):
            with pytest.raises(FlyClientError, match="no 'id' key"):
                launch_machine("registry.fly.io/leerie:0.1.0", {}, "iad")

    def test_default_app_name_is_leerie(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "tok")
        monkeypatch.delenv("FLY_APP_NAME", raising=False)

        captured: list[Any] = []

        def fake_urlopen(req: Any) -> Any:
            captured.append(req)
            return _fake_response({"id": "m3", "state": "created"})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            launch_machine("registry.fly.io/leerie:0.1.0", {}, "iad")

        assert "/apps/leerie/machines" in captured[0].full_url


# ---------------------------------------------------------------------------
# get_machine_state
# ---------------------------------------------------------------------------

class TestGetMachineState:
    def test_issues_get_to_correct_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "tok")
        monkeypatch.setenv("FLY_APP_NAME", "leerie")

        captured: list[Any] = []

        def fake_urlopen(req: Any) -> Any:
            captured.append(req)
            return _fake_response({"id": "m1", "state": "started"})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            state = get_machine_state("m1")

        assert state == "started"
        req = captured[0]
        assert req.get_method() == "GET"
        assert "/apps/leerie/machines/m1" in req.full_url

    def test_returns_stopped_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "tok")
        monkeypatch.setenv("FLY_APP_NAME", "leerie")

        with patch(
            "urllib.request.urlopen",
            return_value=_fake_response({"id": "m2", "state": "stopped"}),
        ):
            assert get_machine_state("m2") == "stopped"

    def test_raises_on_api_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "tok")
        monkeypatch.setenv("FLY_APP_NAME", "leerie")

        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error(500, "internal error"),
        ):
            with pytest.raises(FlyClientError, match="500"):
                get_machine_state("m99")

    def test_raises_when_response_missing_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "tok")
        monkeypatch.setenv("FLY_APP_NAME", "leerie")

        with patch(
            "urllib.request.urlopen",
            return_value=_fake_response({"id": "m3"}),  # no 'state'
        ):
            with pytest.raises(FlyClientError, match="no 'state' key"):
                get_machine_state("m3")


# ---------------------------------------------------------------------------
# destroy_machine
# ---------------------------------------------------------------------------

class TestDestroyMachine:
    def test_issues_delete_with_force_param(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "tok")
        monkeypatch.setenv("FLY_APP_NAME", "leerie")

        captured: list[Any] = []

        def fake_urlopen(req: Any) -> Any:
            captured.append(req)
            return _fake_response(None)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            destroy_machine("m-del")

        req = captured[0]
        assert req.get_method() == "DELETE"
        assert "/apps/leerie/machines/m-del" in req.full_url
        assert "force=true" in req.full_url

    def test_does_not_raise_on_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "tok")
        monkeypatch.setenv("FLY_APP_NAME", "leerie")

        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error(404, "not found"),
        ):
            # Should not raise — machine already gone is success
            destroy_machine("already-gone")

    def test_raises_on_non_404_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "tok")
        monkeypatch.setenv("FLY_APP_NAME", "leerie")

        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error(500, "server error"),
        ):
            with pytest.raises(FlyClientError, match="500"):
                destroy_machine("bad-machine")

    def test_returns_none_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLY_API_TOKEN", "tok")
        monkeypatch.setenv("FLY_APP_NAME", "leerie")

        with patch("urllib.request.urlopen", return_value=_fake_response(None)):
            result = destroy_machine("m-ok")

        assert result is None
