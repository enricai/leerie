"""chain.__main__ — entry point for `python3 -m chain`.

Boots the per-chain ephemeral coordinator. Delegates to
``chain.coordinator.main`` which reads all configuration from env vars
(see that module's docstring for the full list).
"""
from __future__ import annotations

from chain.coordinator import main


if __name__ == "__main__":
    main()
