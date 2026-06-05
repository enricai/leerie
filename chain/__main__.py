"""chain.__main__ — entry point for `python3 -m chain`.

Initialises the ChainState DB and starts the HTTP server.  The /data
mount point is the Fly persistent volume path declared in chain/fly.toml.
"""
from __future__ import annotations

import os

from chain.config import load_settings
from chain.server import make_server
from chain.state import ChainState

_DB_PATH = os.environ.get("CHAIN_DB_PATH", "/data/chain.db")
_HOST = os.environ.get("CHAIN_HOST", "0.0.0.0")
_PORT = int(os.environ.get("CHAIN_PORT", "8080"))


def main() -> None:
    cs = ChainState.init_db(_DB_PATH)
    settings = load_settings()
    httpd = make_server(cs, settings, host=_HOST, port=_PORT)
    print(f"leerie-chain: listening on {_HOST}:{_PORT}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
