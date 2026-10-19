import pytest
from tgkit import FORM, POLL

from televibe.telegram.controls import (
    ControlTexts,
    Form,
    Offer,
    Poll,
    Press,
    answered_keyboard,
    control_block,
    form_answer,
    keyboard,
    offer_answer,
    parse_press,
    poll_answer,
    poll_fallback_text,
    split_control,
    ticks_after,
)

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


def test_offer_keyboard_is_one_button():
    """REQ-CONTROL-3: one button, the offer's label or the default, data tv:o."""
    [[b]] = keyboard(Offer(None), ControlTexts(offer_label="Да")).inline_keyboard
    assert (b.text, b.callback_data) == ("Да", "tv:o")
    [[b]] = keyboard(Offer("Слить"), ControlTexts()).inline_keyboard
    assert b.text == "Слить"


def test_form_keyboard_marks_ticks_and_ends_with_done():
    """REQ-CONTROL-3: rows as given, ✓ on the ticked option, [Done] last."""
    kb = keyboard(FORM, ControlTexts(done_label="Готово"), ticks=(1, 0)).inline_keyboard
    assert [[b.text for b in row] for row in kb] == [["ты", "✓ вы"], ["✓ он", "она"], ["Готово"]]
    assert kb[0][1].callback_data == "tv:t:0:1" and kb[2][0].callback_data == "tv:d"


def test_form_keyboard_without_ticks_uses_the_advance_ticks():
    """REQ-CONTROL-3: ticks=None is the form's own advance ticks."""
    kb = keyboard(FORM, ControlTexts()).inline_keyboard
    assert [[b.text for b in row] for row in kb[:2]] == [["ты", "✓ вы"], ["он", "она"]]


def test_answered_keyboard_is_one_inert_button():
    """REQ-CONTROL-3: the mark on one button that does nothing, data tv:x."""
    [[b]] = answered_keyboard("✓ Да").inline_keyboard
    assert (b.text, b.callback_data) == ("✓ Да", "tv:x")


def test_callback_data_fits_64_bytes_for_a_big_form():
    """REQ-CONTROL-3: indices only."""
    big = Form(tuple(tuple("ж" * 60 for _ in range(8)) for _ in range(10)), (None,) * 10)
    for row in keyboard(big, ControlTexts()).inline_keyboard:
        assert all(len((b.callback_data or "").encode()) <= 64 for b in row)


def test_parse_press():
    """REQ-CONTROL-4: televibe's data parses, anything else is None."""
    assert parse_press("tv:t:2:1") == Press("tick", 2, 1)
    assert parse_press("tv:o") == Press("offer") and parse_press("tv:d") == Press("done")
    assert parse_press("tv:x") == Press("inert")
    assert parse_press("menu:5") is None and parse_press(None) is None and parse_press("tv:t:a") is None


def test_ticks_replay_and_toggle():
    """REQ-CONTROL-5: one tick per row, from the advance ticks; pressing the ticked option unticks it."""
    form = Form((("ты", "вы"), ("он", "она")), (1, None))
    assert ticks_after(form, []) == (1, None)
    assert ticks_after(form, [Press("tick", 0, 0), Press("tick", 1, 1)]) == (0, 1)
    assert ticks_after(form, [Press("tick", 1, 1), Press("tick", 1, 1)]) == (1, None)
    assert ticks_after(form, [Press("tick", 5, 0), Press("offer")]) == (1, None)
    assert ticks_after(form, [Press("tick", 0, 9)]) == (1, None)


def test_answer_texts():
    """REQ-CONTROL-6: the person's answer as text, from the bot's templates."""
    t = ControlTexts()
    assert offer_answer(Offer("Слить"), t) == "Pressed: Слить"
    assert offer_answer(Offer(None), t) == "Pressed: Yes"
    assert poll_answer(POLL, [0, 2], t) == "Chose in the poll: Арман 20 000, ничего"
    assert poll_answer(POLL, [], t) == "Chose in the poll: nothing"
    assert form_answer(FORM, (1, None), t, typed="Максим") == "Form: вы; not chosen\nTyped: Максим"
    assert form_answer(FORM, (1, 0), t) == "Form: вы; он"


def test_poll_fallback_text_numbers_the_options():
    """REQ-CONTROL-6: the poll as plain text, for a chat that cannot show one."""
    assert poll_fallback_text(POLL) == (
        "Что из этого займы?\nПереводы людям из выписок за июль–октябрь\n\n1) Арман 20 000\n2) Лолита 50 000\n3) ничего"
    )
    assert poll_fallback_text(Poll("Q?", ("a", "b"))) == "Q?\n\n1) a\n2) b"
