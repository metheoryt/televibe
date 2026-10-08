"""The one exception televibe raises at a call site."""


class TelevibeError(Exception):
    """A misuse caught before any agent process starts (REQ-API-2)."""
