#!/usr/bin/env python3
"""The delivery re-drive's self-check (DESIGN §8 *A held-out set never
enters a tree a worker commits from*): runs the shown acceptance sets
against a copy of the worker's current working tree, outside it.

Usage: selfcheck.py <config.json> [set file…] [-t PATTERN] [runner args…]

The orchestrator writes the config and a `leerie-selfcheck` wrapper that
calls this with it (`_selfcheck_prepare`). The logic is the orchestrator's
own (`_selfcheck_main`), imported from the sibling `orchestrator/`
directory as `forbidden-command-hook.py` does, so the self-check places and
runs the sets exactly as the evaluation does (`_place_set`,
`_acceptance_cmd`)."""
import os
import sys


def main() -> int:
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "orchestrator"))
    import leerie
    return leerie._selfcheck_main(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
