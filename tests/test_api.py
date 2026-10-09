import televibe


def test_public_surface_is_pinned():
    """REQ-API-1: the public surface is exactly these names."""
    assert sorted(televibe.__all__) == sorted([
        "Engine", "Turn", "Session", "Account", "Access", "ClaudeCode", "Codex", "FailReason", "Stranded",
        "Queued", "Started", "Message", "ToolUse", "Warning", "Limits", "LimitWindow", "Done", "Failed",
        "TelevibeError",
    ])
    for name in televibe.__all__:
        assert getattr(televibe, name) is not None
