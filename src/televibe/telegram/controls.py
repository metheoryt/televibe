"""Controls: the one fenced block an agent ends its answer with (section 13, CONTROL)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

INFO = "control"
"""The fence's info string."""

_FENCE = re.compile(rf"^```{INFO}[ \t]*\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)


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
    if not blocks:
        return text, None
    kept = _FENCE.sub("", text).rstrip()
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
