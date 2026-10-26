"""An answer in the form Telegram accepts (section 13, RENDER).

Telegram's rich message parses GitHub-flavored Markdown itself, and the agents
write it, so rendering is a length limit, kept line breaks, plus fallbacks.
"""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InputRichMessage

MAX_RICH_CHARS = 32_768
"""The Bot API limit for a rich message."""
MAX_PLAIN_CHARS = 4_096
"""sendMessage's limit, for the plain-text fallback."""

_TRUNCATED = "\n\n_... truncated_"
_FENCE_CLOSER = "\n```"
_EMPTY_RICH = "_(no output)_"
_EMPTY_PLAIN = "(no output)"
_HARD_BREAK = "  "
"""Two trailing spaces: Markdown's hard line break, which Telegram's parser keeps (measured 2026-10-26)."""


def _clamp(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit - len(_TRUNCATED) - len(_FENCE_CLOSER)]
    if head.count("```") % 2 == 1:
        head += _FENCE_CLOSER
    return head + _TRUNCATED


def _keep_line_breaks(text: str) -> str:
    """Every line break outside a code fence stays a line break.

    In Markdown a single newline joins two lines into one, so an answer written line by line (a
    payment schedule) arrived as one run-on paragraph. A hard break at the end of a block is ignored
    and a table row or a heading trims it, so only a line inside a fence is left alone: there the
    spaces would show."""
    lines = text.split("\n")
    fenced = False
    for i, line in enumerate(lines):
        if line.lstrip(" ").startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or not line.strip() or i + 1 == len(lines) or not lines[i + 1].strip():
            continue
        if not line.endswith((_HARD_BREAK, "\\")):
            lines[i] = line + _HARD_BREAK
    return "\n".join(lines)


def to_rich_message(text: str) -> InputRichMessage:
    """The answer as a rich message, its line breaks kept. Never empty."""
    return InputRichMessage(
        markdown=_clamp(_keep_line_breaks(text), MAX_RICH_CHARS) if text.strip() else _EMPTY_RICH
    )


def to_plain_text(text: str) -> str:
    """The answer as plain text, for when a rich message is rejected. Never empty."""
    return _clamp(text, MAX_PLAIN_CHARS) if text.strip() else _EMPTY_PLAIN


def to_draft_message(text: str, status: str | None = None) -> InputRichMessage:
    """A live draft: `status` in a thinking block, which only drafts accept, then the text."""
    parts = [f"<tg-thinking>{status}</tg-thinking>"] if status else []
    if text.strip():
        parts.append(_keep_line_breaks(text))
    return InputRichMessage(markdown=_clamp("\n\n".join(parts), MAX_RICH_CHARS) if parts else _EMPTY_RICH)


def status_line(
    *,
    model: str | None = None,
    elapsed_s: float = 0.0,
    usage: Sequence[tuple[str, float]] = (),
    tool: str | None = None,
) -> str:
    """The draft's status: model, elapsed time, usage, current tool. Never empty, so the clock shows the bot is alive.

    `usage` is (label, fraction used) per limit window; a fraction past 1 shows as more than 100%."""
    parts = [_sanitize(model)] if model else []
    parts.append(_elapsed(elapsed_s))
    parts += [f"{_sanitize(label)} {round(used * 100)}%" for label, used in usage]
    if tool:
        parts.append(_sanitize(tool))
    return " · ".join(parts)


def _elapsed(seconds: float) -> str:
    hours, rest = divmod(int(max(0.0, seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _sanitize(text: str) -> str:
    """The status sits inside <tg-thinking>; a stray angle bracket would break the draft."""
    return text.replace("<", "(").replace(">", ")")
