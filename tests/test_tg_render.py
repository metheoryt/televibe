from televibe.telegram.render import (
    MAX_PLAIN_CHARS,
    MAX_RICH_CHARS,
    status_line,
    to_draft_message,
    to_plain_text,
    to_rich_message,
)


def test_rich_message_short_text_is_unchanged():
    """REQ-RENDER-1: a text within the limit is sent as is."""
    assert to_rich_message("**hi**\n\n```py\nx = 1\n```").markdown == "**hi**\n\n```py\nx = 1\n```"


def test_rich_message_is_clamped_with_a_note():
    """REQ-RENDER-1: a longer text is cut to 32768 characters and a truncation note is appended."""
    body = to_rich_message("word " * 20_000).markdown
    assert body is not None
    assert len(body) <= MAX_RICH_CHARS
    assert body.endswith("_... truncated_")


def test_rich_message_closes_a_cut_code_fence():
    """REQ-RENDER-1: when the cut lands inside a code fence, the fence is closed."""
    body = to_rich_message("intro\n```python\n" + "x = 1\n" * 10_000).markdown
    assert body is not None
    assert len(body) <= MAX_RICH_CHARS
    assert body.count("```") % 2 == 0


def test_rich_message_is_never_empty():
    """REQ-RENDER-1: an empty or blank text becomes a short note."""
    assert to_rich_message("").markdown == "_(no output)_"
    assert to_rich_message("  \n\t").markdown == "_(no output)_"


def test_plain_text_limit_and_empty():
    """REQ-RENDER-2: plain text is at most 4096 characters, closes a cut fence, and is never empty."""
    assert to_plain_text("hello") == "hello"
    long = to_plain_text("```\n" + "y\n" * 5_000)
    assert len(long) <= MAX_PLAIN_CHARS and long.count("```") % 2 == 0 and long.endswith("_... truncated_")
    assert to_plain_text(" ") == "(no output)"


def test_draft_puts_status_in_a_thinking_block_above_the_text():
    """REQ-RENDER-3: the status goes in a thinking block, then the text."""
    assert to_draft_message("partial answer", "0:05 · Bash").markdown == (
        "<tg-thinking>0:05 · Bash</tg-thinking>\n\npartial answer"
    )
    assert to_draft_message("", "0:01").markdown == "<tg-thinking>0:01</tg-thinking>"


def test_status_line_always_has_the_elapsed_time():
    """REQ-RENDER-3: the status line is never empty; it holds at least the elapsed time."""
    assert status_line() == "0:00"
    assert status_line(elapsed_s=3725.9) == "1:02:05"
    assert status_line(model="sonnet", elapsed_s=65, tool="Bash") == "sonnet · 1:05 · Bash"


def test_status_line_shows_usage_windows():
    """REQ-RENDER-3: each usage window as label and percent, between the clock and the tool; past 100% stays as is."""
    usage = [("5h", 0.354), ("7d", 0.13)]
    assert status_line(model="sonnet", elapsed_s=65, usage=usage, tool="Bash") == "sonnet · 1:05 · 5h 35% · 7d 13% · Bash"
    assert status_line(usage=[("<5ч>", 1.04)]) == "0:00 · (5ч) 104%"


def test_status_line_replaces_angle_brackets():
    """REQ-RENDER-3: angle brackets from a tool or model name cannot break the thinking block."""
    line = status_line(model="<m>", tool="mcp__x</tg-thinking><b>")
    assert "<" not in line and ">" not in line
