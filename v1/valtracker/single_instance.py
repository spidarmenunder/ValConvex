"""Only one ValConvex at a time. Starting it again just brings the running one to the front."""
from __future__ import annotations

import socket
import threading
from typing import Callable, Optional

PORT = 47831
DEMO_PORT = 47832
HELLO = b"SHOW\n"
REPLY = b"VALCONVEX\n"


class SingleInstance:
    def __init__(self, port: int = PORT):
        self.port = port
        self.server: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._on_show: Optional[Callable[[], None]] = None

    def acquire(self, on_show: Callable[[], None]) -> bool:
        """True: carry on (we are the first, or could not tell). False: another ValConvex was told to show itself."""
        self._on_show = on_show
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", self.port))
            srv.listen(2)
        except OSError:
            srv.close()
            return not self._poke_existing()
        self.server = srv
        self._thread = threading.Thread(target=self._serve, daemon=True, name="single-instance")
        self._thread.start()
        return True

    def _poke_existing(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as c:
                c.sendall(HELLO)
                return c.recv(32).startswith(REPLY.strip())
        except OSError:
            return False

    def _serve(self) -> None:
        while self.server is not None:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            try:
                conn.settimeout(1.0)
                if conn.recv(16).startswith(HELLO.strip()):
                    conn.sendall(REPLY)
                    if self._on_show:
                        self._on_show()
            except OSError:
                pass
            finally:
                conn.close()

    def release(self) -> None:
        srv, self.server = self.server, None
        thread = self._thread
        self._thread = None
        if srv:
            try:
                srv.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                srv.close()
            except OSError:
                pass
        if thread and thread is not threading.current_thread():
            thread.join(timeout=0.25)
