"""chain.config — shared settings for the leerie-chain orchestrator app.

Reads required env vars at call time (not import time) so tests can
monkeypatch os.environ before calling load_settings(). All values come
from os.environ; no file-based precedence layer is needed for the
chain app (it runs on Fly with secrets injected as env vars).

Required env vars:
  GH_DISPATCH_PAT       GitHub PAT with repo + workflow scopes, used to
                        clone target repos and open PRs via gh.
  FLY_API_TOKEN         Fly.io API token used by fly_client to launch and
                        destroy per-run worker machines via the Machines API.
  CHAIN_WEBHOOK_SECRET  HMAC-SHA256 signing secret shared with Fly to verify
                        incoming machine-exit webhook payloads.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass


_REQUIRED: list[str] = [
    "GH_DISPATCH_PAT",
    "FLY_API_TOKEN",
    "CHAIN_WEBHOOK_SECRET",
]


def _die(msg: str, code: int = 1) -> None:
    print(f"leerie-chain: error: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


@dataclass(frozen=True)
class Settings:
    gh_dispatch_pat: str
    fly_api_token: str
    chain_webhook_secret: str


def load_settings() -> Settings:
    """Read required env vars and return a frozen Settings object.

    Exits with a clear error message if any required variable is absent or
    empty — mirrors leerie.py's die() pattern so misconfiguration is caught
    at startup rather than surfacing as an obscure AttributeError later.
    """
    missing = [k for k in _REQUIRED if not os.environ.get(k, "").strip()]
    if missing:
        _die(
            f"required environment variable(s) not set: {', '.join(missing)}. "
            f"Set them via `fly secrets set` before deploying."
        )
    return Settings(
        gh_dispatch_pat=os.environ["GH_DISPATCH_PAT"].strip(),
        fly_api_token=os.environ["FLY_API_TOKEN"].strip(),
        chain_webhook_secret=os.environ["CHAIN_WEBHOOK_SECRET"].strip(),
    )
