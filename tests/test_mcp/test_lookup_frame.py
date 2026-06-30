def test_lookup_frame_tool_is_registered():
    import orchard.server as server_mod

    names = [tool.name for tool in server_mod.TOOLS]
    assert "orchard_lookup_frame" in names
    assert "orchard_lookup_crash_thread" in names


def test_search_tool_description_mentions_next_actions_and_frame_lookup():
    import orchard.server as server_mod

    tool = next(t for t in server_mod.TOOLS if t.name == "orchard_search")
    assert "next" in tool.description.lower()
    assert "orchard_lookup_frame" in tool.description


def test_crash_thread_tool_description_mentions_dispatch_boundaries():
    import orchard.server as server_mod

    tool = next(t for t in server_mod.TOOLS if t.name == "orchard_lookup_crash_thread")
    assert "crashed thread" in tool.description.lower()
    assert "dispatch" in tool.description.lower()
