"""Start the offline editor: ``python -m poc.server`` (see poc/server/launcher.py)."""
from __future__ import annotations

import sys

from poc.server.launcher import main

if __name__ == "__main__":
    sys.exit(main())
