"""Controls: the one fenced block an agent ends its answer with (section 13, CONTROL)."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)

INFO = "control"
"""The fence's info string."""

_FENCE = re.compile(rf"^```{INFO}[ \t]*\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)
_OPEN = re.compile(rf"^```{INFO}[ \t]*(?:\n|$)", re.MULTILINE)
"""An opening line that no closing fence answered: a turn cut off mid-block."""


@dataclass(frozen=True, slots=True)
class Offer:
    """One button that offers the agent's next step."""

    label: str | None = None
    """None: the bot's `ControlTexts.offer_label`."""


@dataclass(frozen=True, slots=True)
class Poll:
    question: str
    options: tuple[str, ...]
    """Two or more."""
    multiple: bool = False
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Form:
    """Rows of tick buttons: the person ticks one option per row, then sends."""

    rows: tuple[tuple[str, ...], ...]
    """One or more rows, each of two or more options."""
    ticked: tuple[int | None, ...]
    """Per row, the option ticked in advance."""


type Control = Offer | Poll | Form


class _Malformed(Exception):
    pass


def split_control(text: str) -> tuple[str, Control | None]:
    """The answer without its control block, and the control (REQ-CONTROL-1).

    A text with no control fence comes back as it was. A malformed block is removed
    all the same and logged: the person still reads the answer.
    """
    blocks = list(_FENCE.finditer(text))
    kept = _FENCE.sub("", text)
    if unclosed := _OPEN.search(kept):
        # A truncated block: nothing from its opening line on is the person's to read.
        log.warning("televibe: malformed control block: %s", "unclosed fence")
        return kept[: unclosed.start()].rstrip(), None
    if not blocks:
        return text, None
    kept = kept.rstrip()
    try:
        if len(blocks) > 1:
            raise _Malformed("more than one block")
        if text[blocks[0].end() :].strip():
            raise _Malformed("text after the block")
        return kept, _parse(blocks[0].group(1))
    except _Malformed as why:
        log.warning("televibe: malformed control block: %s", why)
        return kept, None


def _parse(body: str) -> Control:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        raise _Malformed("empty")
    head, rest = lines[0].split(maxsplit=1) if " " in lines[0] else (lines[0], ""), lines[1:]
    kind, arg = head
    if kind == "offer":
        if rest:
            raise _Malformed("offer takes one line")
        return Offer(arg or None)
    if kind == "poll":
        return _poll(arg, rest)
    if kind == "form":
        if arg:
            raise _Malformed("form takes no argument")
        return _form(rest)
    raise _Malformed(f"unknown kind {kind!r}")


def _poll(arg: str, lines: list[str]) -> Poll:
    if arg not in ("", "multiple"):
        raise _Malformed(f"unknown poll argument {arg!r}")
    question: list[str] = []
    description: list[str] = []
    options: list[str] = []
    for line in lines:
        mark, value = line[0], line[1:].strip()
        if mark not in "?>-" or not value:
            raise _Malformed(f"bad poll line {line!r}")
        {"?": question, ">": description, "-": options}[mark].append(value)
    if len(question) != 1:
        raise _Malformed("a poll needs exactly one question")
    if len(options) < 2:
        raise _Malformed("a poll needs two or more options")
    return Poll(question[0], tuple(options), arg == "multiple", "\n".join(description) or None)


def _form(lines: list[str]) -> Form:
    rows: list[tuple[str, ...]] = []
    ticked: list[int | None] = []
    for line in lines:
        if not line.startswith("-"):
            raise _Malformed(f"bad form line {line!r}")
        row: list[str] = []
        ticks: list[int] = []
        for i, option in enumerate(part.strip() for part in line[1:].split("|")):
            if option.startswith("*"):
                ticks.append(i)
                option = option[1:].strip()
            if not option:
                raise _Malformed(f"empty option in {line!r}")
            row.append(option)
        if len(row) < 2:
            raise _Malformed(f"a row needs two or more options: {line!r}")
        if len(ticks) > 1:
            raise _Malformed(f"more than one tick in a row: {line!r}")
        rows.append(tuple(row))
        ticked.append(ticks[0] if ticks else None)
    if not rows:
        raise _Malformed("a form needs one or more rows")
    return Form(tuple(rows), tuple(ticked))


def control_block(control: Control) -> str:
    """The fenced block for `control`; `split_control` reads it back (REQ-CONTROL-2)."""
    match control:
        case Offer(label):
            lines = [f"offer {label}" if label else "offer"]
        case Poll(question, options, multiple, description):
            lines = ["poll multiple" if multiple else "poll", f"? {question}"]
            lines += [f"> {line}" for line in description.split("\n")] if description else []
            lines += [f"- {option}" for option in options]
        case Form(rows, ticked):
            lines = ["form"]
            for row, tick in zip(rows, ticked, strict=True):
                lines.append("- " + " | ".join(f"*{o}" if i == tick else o for i, o in enumerate(row)))
    return "\n".join([f"```{INFO}", *lines, "```"]) + "\n"


@dataclass(frozen=True, slots=True)
class ControlTexts:
    """The words a bot puts on and around its controls (REQ-CONTROL-6)."""

    offer_label: str = "Yes"
    done_label: str = "Done"
    pressed: str = "Pressed: {label}"
    voted: str = "Chose in the poll: {options}"
    nothing: str = "nothing"
    form: str = "Form: {rows}"
    unset: str = "not chosen"
    typed: str = "Typed: {text}"
    answered: str = "✓ {label}"
    answered_in_text: str = "✓ answered in text"


@dataclass(frozen=True, slots=True)
class Press:
    """What a callback's data says was pressed."""

    kind: Literal["offer", "tick", "done", "inert"]
    row: int | None = None
    option: int | None = None


_OFFER, _DONE, _INERT = "tv:o", "tv:d", "tv:x"
_TICK = re.compile(r"tv:t:(\d+):(\d+)")


def keyboard(
    control: Offer | Form, texts: ControlTexts, ticks: Sequence[int | None] | None = None
) -> InlineKeyboardMarkup:
    """The inline keyboard for an offer or a form (REQ-CONTROL-3).

    `ticks` is the option ticked per row; None is the form's own advance ticks.
    """
    if isinstance(control, Offer):
        button = InlineKeyboardButton(text=control.label or texts.offer_label, callback_data=_OFFER)
        return InlineKeyboardMarkup(inline_keyboard=[[button]])
    ticks = control.ticked if ticks is None else ticks
    rows = [
        [
            InlineKeyboardButton(text=f"✓ {option}" if j == ticks[i] else option, callback_data=f"tv:t:{i}:{j}")
            for j, option in enumerate(row)
        ]
        for i, row in enumerate(control.rows)
    ]
    rows.append([InlineKeyboardButton(text=texts.done_label, callback_data=_DONE)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def answered_keyboard(mark: str) -> InlineKeyboardMarkup:
    """One button that does nothing, left under an answered control (REQ-CONTROL-3)."""
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=mark, callback_data=_INERT)]])


def parse_press(data: str | None) -> Press | None:
    """What `data` says was pressed; None when it is not televibe's (REQ-CONTROL-4)."""
    if data == _OFFER:
        return Press("offer")
    if data == _DONE:
        return Press("done")
    if data == _INERT:
        return Press("inert")
    if data and (m := _TICK.fullmatch(data)):
        return Press("tick", int(m[1]), int(m[2]))
    return None


def ticks_after(form: Form, presses: Iterable[Press]) -> tuple[int | None, ...]:
    """The ticks after `presses`, from the form's advance ticks (REQ-CONTROL-5).

    Pressing the ticked option unticks it. Other kinds and out-of-range ticks are ignored.
    """
    ticks = list(form.ticked)
    for press in presses:
        if press.kind != "tick" or press.row is None or press.option is None:
            continue
        if not (0 <= press.row < len(form.rows) and 0 <= press.option < len(form.rows[press.row])):
            continue
        ticks[press.row] = None if ticks[press.row] == press.option else press.option
    return tuple(ticks)


def offer_answer(offer: Offer, texts: ControlTexts) -> str:
    """The person's press as text (REQ-CONTROL-6)."""
    return texts.pressed.format(label=offer.label or texts.offer_label)


def poll_answer(poll: Poll, option_ids: Sequence[int], texts: ControlTexts) -> str:
    """The person's vote as text (REQ-CONTROL-6)."""
    options = ", ".join(poll.options[i] for i in option_ids) or texts.nothing
    return texts.voted.format(options=options)


def form_answer(form: Form, ticks: Sequence[int | None], texts: ControlTexts, typed: str | None = None) -> str:
    """The person's form as text, and what they typed besides (REQ-CONTROL-6)."""
    rows = "; ".join(texts.unset if t is None else row[t] for row, t in zip(form.rows, ticks, strict=True))
    text = texts.form.format(rows=rows)
    return f"{text}\n{texts.typed.format(text=typed)}" if typed else text


def poll_fallback_text(poll: Poll) -> str:
    """The poll as plain numbered text, for a chat that cannot show one (REQ-CONTROL-6)."""
    head = poll.question + (f"\n{poll.description}" if poll.description else "")
    options = "\n".join(f"{i}) {option}" for i, option in enumerate(poll.options, 1))
    return f"{head}\n\n{options}"
