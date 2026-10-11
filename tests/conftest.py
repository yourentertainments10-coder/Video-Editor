"""Shared pytest fixtures for the poc test suite.

Empty for now. Fixtures for a tiny sample timeline, a fake footage index, and
a fake music map are added alongside the modules that need them (Steps 2, 5, 6).
"""

# Monkeypatch Pytest to ignore PermissionError when cleaning up dead symlinks on Windows
import pytest
import _pytest.pathlib
import os

original_cleanup = _pytest.pathlib.cleanup_dead_symlinks

def safe_cleanup_dead_symlinks(root: os.PathLike):
    try:
        original_cleanup(root)
    except PermissionError:
        # Ignore permission errors (e.g., due to broken symlinks on Windows)
        pass

_pytest.pathlib.cleanup_dead_symlinks = safe_cleanup_dead_symlinks


# Socket guard fixture for offline enforcement
import socket

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

@pytest.fixture(autouse=True)
def block_non_loopback_sockets(monkeypatch):
    """Refuse any socket connect or connect_ex call to a non-loopback address."""
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex

    def guarded_connect(self, address):
        # address can be (host, port) or (host, port, flowinfo, scopeid) etc.
        if isinstance(address, tuple) and len(address) >= 1:
            host = address[0]
            # Normalize host for comparison
            if isinstance(host, bytes):
                host = host.decode('utf-8', errors='ignore')
            elif not isinstance(host, str):
                host = str(host)
            if host not in LOOPBACK_HOSTS:
                raise RuntimeError(
                    f"OFFLINE POLICY VIOLATION: Outbound socket connection to {host!r} "
                    f"blocked during test execution (loopback only allowed)."
                )
        return orig_connect(self, address)

    def guarded_connect_ex(self, address):
        if isinstance(address, tuple) and len(address) >= 1:
            host = address[0]
            if isinstance(host, bytes):
                host = host.decode('utf-8', errors='ignore')
            elif not isinstance(host, str):
                host = str(host)
            if host not in LOOPBACK_HOSTS:
                raise RuntimeError(
                    f"OFFLINE POLICY VIOLATION: Outbound socket connection to {host!r} "
                    f"blocked during test execution (loopback only allowed)."
                )
        return orig_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    # Also guard create_connection which uses socket.connect under the hood
    # No need to patch separately as it ends up calling socket.socket.connect