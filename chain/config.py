"""chain.config — shared settings for the leerie-chain orchestrator app."""
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
