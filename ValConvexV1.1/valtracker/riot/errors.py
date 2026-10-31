class RiotError(Exception):
    """A Riot endpoint answered with an unexpected status."""

    def __init__(self, status: int, url: str = "", message: str = ""):
        self.status = status
        self.url = url
        super().__init__(message or f"HTTP {status} from {url}")


class ClientNotRunning(Exception):
    """The Riot Client / VALORANT is not running (no lockfile, or it refused the connection)."""


class HenrikError(Exception):
    """HenrikDev API problem, with a message that is safe to show the user."""
