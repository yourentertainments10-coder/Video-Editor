"""CLI entry point: `python -m poc <subcommand>`.

Deliberately empty of real subcommands until each component exists. Subcommands
are added one per implementation step:

  python -m poc.timeline <file.json>   (Step 2/3: schema + validator, run directly as a module for now)
  python -m poc run ...                (Step 8: full end-to-end pipeline, added last)

This stub only proves the package is importable and runnable as `python -m poc`.
"""
from __future__ import annotations

import sys

from poc import __version__


def main(argv: list[str]) -> int:
    print(f"poc {__version__}: no subcommands implemented yet (Step 1 scaffold only).")
    print("See docs/MVP.md for the implementation sequence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
