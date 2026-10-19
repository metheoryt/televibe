"""Telegram layer: show turns in Telegram, follow reply chains, start turns from reactions (SPEC.md section 13)."""

try:
    import aiogram  # noqa: F401
except ImportError as exc:
    raise ImportError("televibe.telegram needs aiogram; install it with `pip install 'televibe[telegram]'`") from exc

from televibe.telegram import controls, render
from televibe.telegram.chains import Chain, Chains
from televibe.telegram.presenter import Presenter, Reactions, Texts
from televibe.telegram.reacted import reaction_added
from televibe.telegram.store import ChainStore, MemoryChainStore

__all__ = [
    "Chain",
    "ChainStore",
    "Chains",
    "MemoryChainStore",
    "Presenter",
    "Reactions",
    "Texts",
    "controls",
    "reaction_added",
    "render",
]
