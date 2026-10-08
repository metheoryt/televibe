"""Telegram layer: show turns in Telegram and follow reply chains (SPEC.md section 12)."""

try:
    import aiogram  # noqa: F401
except ImportError as exc:
    raise ImportError("televibe.telegram needs aiogram; install it with `pip install 'televibe[telegram]'`") from exc

__all__: list[str] = []
