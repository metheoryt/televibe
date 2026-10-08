"""Telegram layer: show turns in Telegram and follow reply chains (SPEC.md section 12)."""

try:
    import aiogram  # noqa: F401
except ImportError as exc:
    raise ImportError("televibe.telegram needs aiogram; install it with `pip install 'televibe[telegram]'`") from exc

from televibe.telegram import render
from televibe.telegram.chains import Chain, Chains
from televibe.telegram.presenter import Presenter, Reactions, Texts
from televibe.telegram.store import ChainStore, MemoryChainStore

__all__ = ["Chain", "ChainStore", "Chains", "MemoryChainStore", "Presenter", "Reactions", "Texts", "render"]
