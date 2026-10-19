import pytest

from televibe.telegram.controls import Form, Offer, Poll, control_block, split_control

POLL_TEXT = """Что из этого займы?

```control
poll multiple
? Что из этого займы?
> Переводы людям из выписок за июль–октябрь
- Арман 20 000
- Лолита 50 000
- ничего
```
"""

FORM_TEXT = """Уточни:

```control
form
- ты | *вы
- он | она
```
"""

POLL = Poll(
    "Что из этого займы?",
    ("Арман 20 000", "Лолита 50 000", "ничего"),
    True,
    "Переводы людям из выписок за июль–октябрь",
)
FORM = Form((("ты", "вы"), ("он", "она")), (1, None))


def test_offer_block_is_split_off():
    """REQ-CONTROL-1: the block at the end becomes a control; the text loses it."""
    text, control = split_control("Слила 31 пару\n\n```control\noffer Да\n```\n")
    assert text == "Слила 31 пару" and control == Offer("Да")


def test_bare_offer_has_no_label():
    """REQ-CONTROL-1: `offer` alone is an offer without a label."""
    assert split_control("x\n```control\noffer\n```")[1] == Offer(None)


def test_poll_and_form_parse():
    """REQ-CONTROL-1: poll question, description, options, multiple; form rows and advance ticks."""
    _, poll = split_control(POLL_TEXT)
    assert poll == POLL
    _, form = split_control(FORM_TEXT)
    assert form == FORM


def test_poll_description_lines_are_joined():
    """REQ-CONTROL-1: every `>` line is a line of the description."""
    _, poll = split_control("t\n```control\npoll\n? q\n> a\n> b\n- x\n- y\n```")
    assert poll == Poll("q", ("x", "y"), False, "a\nb")


@pytest.mark.parametrize(
    "bad",
    [
        "```control\npoll\n? q\n- one\n```",  # one option
        "```control\npoll\n- one\n- two\n```",  # no question
        "```control\nform\n- ты\n```",  # a row of one
        "```control\nform\n- *ты | *вы\n```",  # two ticks in a row
        "```control\nform\n```",  # no rows
        "```control\noffer Да\n```\n\nmore text",  # not at the end
        "```control\noffer\n```\n```control\noffer\n```",  # two blocks
        "```control\nbutton\n```",  # unknown kind
        "```control\npoll\n? q\n- a",  # cut off mid-block
        "```control",  # a bare opening line
        "```control\noffer\n```\n```control\npoll\n? q",  # the second block is cut off
    ],
)
def test_malformed_block_is_dropped_and_logged(bad, caplog):
    """REQ-CONTROL-1: the text is kept without any control fence, no control, a warning."""
    text, control = split_control("answer\n\n" + bad)
    assert control is None and "```control" not in text and text.startswith("answer")
    assert "malformed control block" in caplog.text


def test_text_after_the_block_is_preserved():
    """REQ-CONTROL-1: a block not at the end is dropped, the text after it stays."""
    text, control = split_control("answer\n\n```control\noffer Да\n```\n\nmore text")
    assert control is None and "more text" in text and "```control" not in text


def test_text_without_a_block_is_untouched():
    """REQ-CONTROL-1: a text with no control fence comes back as it was."""
    assert split_control("plain ```py\nx\n```") == ("plain ```py\nx\n```", None)


@pytest.mark.parametrize("control", [Offer(None), Offer("Да"), POLL, FORM])
def test_block_round_trips(control):
    """REQ-CONTROL-2: control_block(c) splits back to c."""
    assert split_control("t\n\n" + control_block(control)) == ("t", control)
