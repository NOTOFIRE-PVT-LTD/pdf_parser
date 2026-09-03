"""Suppress harmless Windows asyncio noise when browser tabs disconnect."""

from __future__ import annotations

import socket
import sys


def apply_windows_asyncio_fix() -> None:
    """Avoid WinError 10054 tracebacks when Streamlit/WebSocket clients disconnect."""
    if sys.platform != "win32":
        return

    from asyncio.proactor_events import _ProactorBasePipeTransport

    if getattr(_ProactorBasePipeTransport, "_tp_patched", False):
        return

    _orig = _ProactorBasePipeTransport._call_connection_lost

    def _call_connection_lost(self, exc):  # type: ignore[no-untyped-def]
        if self._called_connection_lost:
            return
        try:
            self._protocol.connection_lost(exc)
        finally:
            sock = self._sock
            if sock is not None and hasattr(sock, "shutdown") and sock.fileno() != -1:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except (ConnectionResetError, OSError):
                    pass
                try:
                    sock.close()
                except OSError:
                    pass
            self._sock = None
            server = self._server
            if server is not None:
                try:
                    server._detach(self)
                except TypeError:
                    server._detach()
                self._server = None
            self._called_connection_lost = True

    _ProactorBasePipeTransport._call_connection_lost = _call_connection_lost  # type: ignore[method-assign]
    _ProactorBasePipeTransport._tp_patched = True  # type: ignore[attr-defined]
