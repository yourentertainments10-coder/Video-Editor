"""PyInstaller entry point for the installed Offline Video Editor."""
import multiprocessing
import sys

from poc.server.launcher import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
