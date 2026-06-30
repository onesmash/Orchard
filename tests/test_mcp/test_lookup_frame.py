def test_lookup_frame_tool_is_registered_without_crash_thread_tool():
    import orchard.server as server_mod

    names = [tool.name for tool in server_mod.TOOLS]
    assert "orchard_lookup_frame" in names
    assert "orchard_lookup_crash_thread" not in names
    assert "orchard_lookup_crash_thread" not in server_mod.HANDLERS


def test_search_tool_description_mentions_next_actions_and_frame_lookup():
    import orchard.server as server_mod

    tool = next(t for t in server_mod.TOOLS if t.name == "orchard_search")
    assert "next" in tool.description.lower()
    assert "orchard_lookup_frame" in tool.description


def test_lookup_frame_tool_description_is_single_frame_only():
    import orchard.server as server_mod

    tool = next(t for t in server_mod.TOOLS if t.name == "orchard_lookup_frame")
    description = tool.description.lower()
    assert "single" in description
    assert "frame" in description
    assert "crashed thread" not in description
    assert "business symbol" not in description
    assert "root cause" not in description
