"""A reaction as a trigger: did this update just add an emoji (section 13, REACTED)."""

from __future__ import annotations

from collections.abc import Iterable

from aiogram.types import MessageReactionUpdated, ReactionTypeEmoji

_VS16 = "️"
"""Telegram sends some emoji with the variation selector and some without; it never changes which emoji it is."""


def _emoji(reactions: Iterable[object] | None) -> set[str]:
    return {r.emoji.replace(_VS16, "") for r in reactions or () if isinstance(r, ReactionTypeEmoji)}


def reaction_added(update: MessageReactionUpdated, emoji: str) -> bool:
    """True when `update` puts `emoji` on the message: in new_reaction, not in old_reaction (REQ-REACTED-1)."""
    wanted = emoji.replace(_VS16, "")
    return wanted in _emoji(update.new_reaction) and wanted not in _emoji(update.old_reaction)
