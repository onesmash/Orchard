import pytest


@pytest.mark.asyncio
async def test_lifespan_starts_ingest_state_watch_without_triggering_ingest(monkeypatch):
    import orchard.server as server_mod

    events: list[str] = []
    original_watch_task = server_mod._ingest_state_watch_task
    original_conn = server_mod._conn

    class FakeTask:
        def __init__(self):
            self.cancelled = False

        def done(self):
            return False

        def cancel(self):
            self.cancelled = True
            events.append("cancel")

        def __await__(self):
            async def _done():
                return None

            return _done().__await__()

    fake_watch_task = FakeTask()

    def fake_create_task(coro):
        events.append(coro.cr_code.co_name)
        coro.close()
        return fake_watch_task

    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(server_mod, "_get_conn", lambda: events.append("get_conn"))

    try:
        async with server_mod._lifespan(server_mod.app):
            assert events == ["_watch_ingest_state", "get_conn"]
    finally:
        server_mod._ingest_state_watch_task = original_watch_task
        server_mod._conn = original_conn

    assert "cancel" in events


@pytest.mark.asyncio
async def test_lifespan_skips_watch_for_filesystem_root(monkeypatch):
    import orchard.server as server_mod

    events: list[str] = []
    original_watch_task = server_mod._ingest_state_watch_task
    original_conn = server_mod._conn

    def fake_create_task(coro):
        events.append(coro.cr_code.co_name)
        coro.close()
        raise AssertionError("create_task should not be called for filesystem root")

    monkeypatch.chdir("/")
    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(server_mod, "_get_conn", lambda: events.append("get_conn"))

    try:
        async with server_mod._lifespan(server_mod.app):
            assert events == ["get_conn"]
    finally:
        server_mod._ingest_state_watch_task = original_watch_task
        server_mod._conn = original_conn


@pytest.mark.asyncio
async def test_call_tool_schedules_background_ingest_once_per_project(monkeypatch, tmp_path):
    import orchard.server as server_mod

    events: list[str] = []
    original_task = server_mod._startup_ingest_task
    original_watch_task = server_mod._ingest_state_watch_task
    original_handler = server_mod.HANDLERS["orchard_search"]
    original_projects = set(server_mod._tool_ingest_projects)

    class FakeTask:
        def done(self):
            return True

        def cancel(self):
            return None

    def fake_create_task(coro):
        events.append(coro.cr_code.co_name)
        coro.close()
        return FakeTask()

    async def fake_to_thread(func, arguments):
        return func(arguments)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(server_mod.asyncio, "to_thread", fake_to_thread)
    server_mod.HANDLERS["orchard_search"] = lambda _arguments: '{"ok": true}'

    try:
        first = await server_mod.call_tool("orchard_search", {"name": "Foo"})
        second = await server_mod.call_tool("orchard_search", {"name": "Bar"})
    finally:
        server_mod._startup_ingest_task = original_task
        server_mod._ingest_state_watch_task = original_watch_task
        server_mod.HANDLERS["orchard_search"] = original_handler
        server_mod._tool_ingest_projects.clear()
        server_mod._tool_ingest_projects.update(original_projects)

    assert [item.text for item in first] == ['{"ok": true}']
    assert [item.text for item in second] == ['{"ok": true}']
    assert events == ["_run_startup_ingest"]


@pytest.mark.asyncio
async def test_call_tool_prefers_mcp_root_for_background_ingest(monkeypatch, tmp_path):
    import mcp.types as mcp_types
    import orchard.server as server_mod

    scheduled_projects: list[str] = []
    original_task = server_mod._startup_ingest_task
    original_watch_task = server_mod._ingest_state_watch_task
    original_handler = server_mod.HANDLERS["orchard_search"]
    original_projects = set(server_mod._tool_ingest_projects)

    class FakeTask:
        def done(self):
            return True

        def cancel(self):
            return None

    class FakeSession:
        async def list_roots(self):
            return mcp_types.ListRootsResult(
                roots=[mcp_types.Root(uri=tmp_path.as_uri(), name="orchard2")]
            )

    def fake_create_task(coro):
        scheduled_projects.append(coro.cr_frame.f_locals["project_dir"])
        coro.close()
        return FakeTask()

    async def fake_to_thread(func, arguments):
        return func(arguments)

    fake_ctx = type("FakeCtx", (), {"session": FakeSession()})()

    monkeypatch.chdir("/")
    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(server_mod.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(type(server_mod.app), "request_context", property(lambda _self: fake_ctx))
    server_mod.HANDLERS["orchard_search"] = lambda _arguments: '{"ok": true}'

    try:
        payload = await server_mod.call_tool("orchard_search", {"name": "Foo"})
    finally:
        server_mod._startup_ingest_task = original_task
        server_mod._ingest_state_watch_task = original_watch_task
        server_mod.HANDLERS["orchard_search"] = original_handler
        server_mod._tool_ingest_projects.clear()
        server_mod._tool_ingest_projects.update(original_projects)

    assert [item.text for item in payload] == ['{"ok": true}']
    assert scheduled_projects == [str(tmp_path)]


@pytest.mark.asyncio
async def test_call_tool_skips_background_ingest_for_filesystem_root(monkeypatch):
    import orchard.server as server_mod

    events: list[str] = []
    original_task = server_mod._startup_ingest_task
    original_watch_task = server_mod._ingest_state_watch_task
    original_handler = server_mod.HANDLERS["orchard_search"]
    original_projects = set(server_mod._tool_ingest_projects)

    def fake_create_task(coro):
        events.append(coro.cr_code.co_name)
        coro.close()
        raise AssertionError("background ingest should not be scheduled for filesystem root")

    async def fake_to_thread(func, arguments):
        return func(arguments)

    monkeypatch.chdir("/")
    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(server_mod.asyncio, "to_thread", fake_to_thread)
    server_mod.HANDLERS["orchard_search"] = lambda _arguments: '{"ok": true}'

    try:
        payload = await server_mod.call_tool("orchard_search", {"name": "Foo"})
    finally:
        server_mod._startup_ingest_task = original_task
        server_mod._ingest_state_watch_task = original_watch_task
        server_mod.HANDLERS["orchard_search"] = original_handler
        server_mod._tool_ingest_projects.clear()
        server_mod._tool_ingest_projects.update(original_projects)

    assert [item.text for item in payload] == ['{"ok": true}']
    assert events == []


@pytest.mark.asyncio
async def test_call_tool_returns_helpful_missing_database_error(monkeypatch, tmp_path):
    import json
    import orchard.server as server_mod

    original_task = server_mod._startup_ingest_task
    original_watch_task = server_mod._ingest_state_watch_task
    original_handler = server_mod.HANDLERS["orchard_search"]
    original_projects = set(server_mod._tool_ingest_projects)

    class FakeTask:
        def done(self):
            return True

        def cancel(self):
            return None

    def fake_create_task(coro):
        coro.close()
        return FakeTask()

    async def fake_to_thread(func, arguments):
        return func(arguments)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(server_mod.asyncio, "to_thread", fake_to_thread)
    server_mod.HANDLERS["orchard_search"] = (
        lambda _arguments: (_ for _ in ()).throw(
            RuntimeError("Cannot create an empty database under READ ONLY mode.")
        )
    )

    try:
        payload = await server_mod.call_tool("orchard_search", {"name": "Foo"})
    finally:
        server_mod._startup_ingest_task = original_task
        server_mod._ingest_state_watch_task = original_watch_task
        server_mod.HANDLERS["orchard_search"] = original_handler
        server_mod._tool_ingest_projects.clear()
        server_mod._tool_ingest_projects.update(original_projects)

    body = json.loads(payload[0].text)
    assert "database not found:" in body["error"]
    assert "orchard ingest --project-dir ." in body["error"]


@pytest.mark.asyncio
async def test_call_tool_schedules_background_ingest_for_each_project(monkeypatch, tmp_path):
    import orchard.server as server_mod

    events: list[str] = []
    original_task = server_mod._startup_ingest_task
    original_watch_task = server_mod._ingest_state_watch_task
    original_handler = server_mod.HANDLERS["orchard_search"]
    original_projects = set(server_mod._tool_ingest_projects)

    class FakeTask:
        def done(self):
            return True

        def cancel(self):
            return None

    def fake_create_task(coro):
        events.append(coro.cr_code.co_name)
        coro.close()
        return FakeTask()

    async def fake_to_thread(func, arguments):
        return func(arguments)

    project_a = tmp_path / "ProjectA"
    project_b = tmp_path / "ProjectB"
    project_a.mkdir()
    project_b.mkdir()

    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(server_mod.asyncio, "to_thread", fake_to_thread)
    server_mod.HANDLERS["orchard_search"] = lambda _arguments: '{"ok": true}'

    try:
        monkeypatch.chdir(project_a)
        await server_mod.call_tool("orchard_search", {"name": "Foo"})
        monkeypatch.chdir(project_b)
        await server_mod.call_tool("orchard_search", {"name": "Bar"})
    finally:
        server_mod._startup_ingest_task = original_task
        server_mod._ingest_state_watch_task = original_watch_task
        server_mod.HANDLERS["orchard_search"] = original_handler
        server_mod._tool_ingest_projects.clear()
        server_mod._tool_ingest_projects.update(original_projects)

    assert events == ["_run_startup_ingest", "_run_startup_ingest"]


@pytest.mark.asyncio
async def test_run_startup_ingest_resets_connection_after_success(monkeypatch, tmp_path):
    import orchard.server as server_mod

    class FakeConn:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    fake_conn = FakeConn()
    original_conn = server_mod._conn

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "orchard.ingest.indexstore._orchard_cli_path",
        lambda: "/fake/orchard",
    )
    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(server_mod.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    server_mod._conn = fake_conn

    try:
        await server_mod._run_startup_ingest(str(tmp_path))
    finally:
        server_mod._conn = original_conn

    assert fake_conn.closed is True
    assert server_mod._conn is None


@pytest.mark.asyncio
async def test_lifespan_logs_helpful_missing_database_error(monkeypatch):
    import orchard.server as server_mod

    events: list[str] = []
    original_watch_task = server_mod._ingest_state_watch_task
    original_conn = server_mod._conn

    class FakeTask:
        def done(self):
            return False

        def cancel(self):
            return None

        def __await__(self):
            async def _done():
                return None

            return _done().__await__()

    def fake_create_task(coro):
        coro.close()
        return FakeTask()

    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(
        server_mod,
        "_get_conn",
        lambda: (_ for _ in ()).throw(
            RuntimeError("Cannot create an empty database under READ ONLY mode.")
        ),
    )
    monkeypatch.setattr(server_mod._SERVER_LOGGER, "error", lambda msg, *args: events.append(msg % args))

    try:
        async with server_mod._lifespan(server_mod.app):
            pass
    finally:
        server_mod._ingest_state_watch_task = original_watch_task
        server_mod._conn = original_conn

    assert any("database not found:" in event for event in events)


@pytest.mark.asyncio
async def test_watch_ingest_state_resets_connection(monkeypatch, tmp_path):
    import orchard.server as server_mod

    project_dir = tmp_path
    state_path = (project_dir / ".orchard" / "ingest-state.json").resolve()

    class FakeConn:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    async def fake_awatch(*args, **kwargs):
        yield {(1, str(state_path))}

    fake_conn = FakeConn()
    original_conn = server_mod._conn
    monkeypatch.setattr(server_mod, "awatch", fake_awatch)
    server_mod._conn = fake_conn

    try:
        await server_mod._watch_ingest_state(str(project_dir))
    finally:
        server_mod._conn = original_conn

    assert fake_conn.closed is True
    assert server_mod._conn is None
