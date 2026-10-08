import asyncio

from televibe.lines import LINE_LIMIT, json_records


async def _records(*chunks: bytes, limit: int = LINE_LIMIT) -> list[dict]:
    reader = asyncio.StreamReader()
    for chunk in chunks:
        reader.feed_data(chunk)
    reader.feed_eof()
    return [record async for record in json_records(reader, limit=limit)]


async def test_bad_lines_are_skipped():
    """REQ-RUN-3: non-JSON, non-object and undecodable lines are skipped; the stream goes on."""
    data = b'{"a": 1}\nnot json\n[1, 2]\n"text"\n\xff\xfe\n\n{"b": 2}\n'
    assert await _records(data) == [{"a": 1}, {"b": 2}]


async def test_lines_split_across_chunks_and_no_final_newline():
    """REQ-RUN-3: a record split over reads is joined; the last line needs no newline."""
    assert await _records(b'{"a"', b": 1}\n", b'{"b": 2}') == [{"a": 1}, {"b": 2}]


async def test_oversize_line_is_skipped():
    """REQ-RUN-3: a line longer than the limit is skipped, whole, however it is chunked."""
    data = b'{"a": 1}\n{"long": "' + b"x" * 100 + b'"}\n{"b": 2}\n'
    assert await _records(data, limit=16) == [{"a": 1}, {"b": 2}]
    chunks = [data[i : i + 5] for i in range(0, len(data), 5)]
    assert await _records(*chunks, limit=16) == [{"a": 1}, {"b": 2}]


def test_default_limit_is_8_mib():
    """REQ-RUN-3: the read limit is 8 MiB."""
    assert LINE_LIMIT == 8 * 1024 * 1024
