import json

import pytest


@pytest.fixture(autouse=True)
def reset_server_state(monkeypatch):
    import orchard.server as server_mod

    monkeypatch.setattr(server_mod, "_DB_PATH", "")
    monkeypatch.setattr(server_mod, "_conn", None)
    monkeypatch.setattr(server_mod, "_conn_db_path", "")
    monkeypatch.setattr(server_mod, "_conn_by_db_path", {})
    monkeypatch.setattr(server_mod, "_startup_ingest_task", None)
    monkeypatch.setattr(server_mod, "_ingest_state_watch_task", None)
    monkeypatch.setattr(server_mod, "_tool_ingest_projects", set())
    monkeypatch.delenv("ORCHARD_DB_PATH", raising=False)


@pytest.mark.asyncio
async def test_lifespan_does_not_start_watcher_or_open_connection(monkeypatch):
    import orchard.server as server_mod

    events: list[str] = []

    def fake_create_task(coro):
        events.append(coro.cr_code.co_name)
        coro.close()
        raise AssertionError("lifespan should not start background tasks")

    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(server_mod, "_get_conn", lambda *args, **kwargs: events.append("get_conn"))

    async with server_mod._lifespan(server_mod.app):
        events.append("entered")

    assert events == ["entered"]


@pytest.mark.asyncio
async def test_call_tool_schedules_background_ingest_once_per_project(monkeypatch, tmp_path):
    import orchard.server as server_mod

    events: list[str] = []

    class FakeTask:
        def done(self):
            return True

        def cancel(self):
            return None

    def fake_create_task(coro):
        events.append(coro.cr_frame.f_locals["project_dir"])
        coro.close()
        return FakeTask()

    async def fake_to_thread(func, arguments):
        return func(arguments)

    repo_root = tmp_path / "repo"
    db_path = repo_root / ".orchard" / "graph.db"
    db_path.parent.mkdir(parents=True)
    db_path.touch()

    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(server_mod.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(server_mod, "_get_conn", lambda project_dir=None: object())
    monkeypatch.setitem(server_mod.HANDLERS, "orchard_search", lambda _arguments: '{"ok": true}')

    async def resolve_target():
        return server_mod.ResolvedTarget(
            project_dir=str(repo_root),
            db_path=str(db_path),
            source="request_root",
            watcher_eligible=True,
        )

    monkeypatch.setattr(server_mod, "_resolve_request_target", resolve_target)

    first = await server_mod.call_tool("orchard_search", {"name": "Foo"})
    second = await server_mod.call_tool("orchard_search", {"name": "Bar"})

    assert [item.text for item in first] == ['{"ok": true}']
    assert [item.text for item in second] == ['{"ok": true}']
    assert events == [str(repo_root)]


@pytest.mark.asyncio
async def test_call_tool_prefers_mcp_root_for_background_ingest(monkeypatch, tmp_path):
    import mcp.types as mcp_types
    import orchard.server as server_mod

    scheduled_projects: list[str] = []

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
    db_path = tmp_path / ".orchard" / "graph.db"
    db_path.parent.mkdir(parents=True)
    db_path.touch()

    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(server_mod.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(type(server_mod.app), "request_context", property(lambda _self: fake_ctx))
    monkeypatch.setattr(server_mod, "_get_conn", lambda project_dir=None: object())
    monkeypatch.setitem(server_mod.HANDLERS, "orchard_search", lambda _arguments: '{"ok": true}')

    payload = await server_mod.call_tool("orchard_search", {"name": "Foo"})

    assert [item.text for item in payload] == ['{"ok": true}']
    assert scheduled_projects == [str(tmp_path)]


@pytest.mark.asyncio
async def test_call_tool_skips_background_ingest_for_explicit_db(monkeypatch, tmp_path):
    import orchard.server as server_mod

    events: list[str] = []
    db_path = tmp_path / "external.db"
    db_path.touch()

    def fake_create_task(coro):
        events.append(coro.cr_code.co_name)
        coro.close()
        raise AssertionError("background ingest should not be scheduled for explicit db targets")

    async def fake_to_thread(func, arguments):
        return func(arguments)

    async def resolve_target():
        return server_mod.ResolvedTarget(
            project_dir=None,
            db_path=str(db_path),
            source="cli_db",
            watcher_eligible=False,
        )

    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(server_mod.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(server_mod, "_resolve_request_target", resolve_target)
    monkeypatch.setattr(server_mod, "_get_conn", lambda project_dir=None: object())
    monkeypatch.setitem(server_mod.HANDLERS, "orchard_search", lambda _arguments: '{"ok": true}')

    payload = await server_mod.call_tool("orchard_search", {"name": "Foo"})

    assert [item.text for item in payload] == ['{"ok": true}']
    assert events == []


@pytest.mark.asyncio
async def test_call_tool_returns_clear_error_when_no_target_resolves(monkeypatch):
    import orchard.server as server_mod

    async def fake_to_thread(func, arguments):
        return func(arguments)

    monkeypatch.setattr(server_mod.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setitem(
        server_mod.HANDLERS,
        "orchard_search",
        lambda _arguments: (_ for _ in ()).throw(
            RuntimeError("Cannot create an empty database under READ ONLY mode.")
        ),
    )

    payload = await server_mod.call_tool("orchard_search", {"name": "Foo"})

    body = json.loads(payload[0].text)
    assert "No Orchard graph database configured" in body["error"]


@pytest.mark.asyncio
async def test_call_tool_schedules_background_ingest_for_each_project(monkeypatch, tmp_path):
    import orchard.server as server_mod

    scheduled_projects: list[str] = []

    class FakeTask:
        def done(self):
            return True

        def cancel(self):
            return None

    def fake_create_task(coro):
        scheduled_projects.append(coro.cr_frame.f_locals["project_dir"])
        coro.close()
        return FakeTask()

    async def fake_to_thread(func, arguments):
        return func(arguments)

    project_a = tmp_path / "ProjectA"
    project_b = tmp_path / "ProjectB"
    db_a = project_a / ".orchard" / "graph.db"
    db_b = project_b / ".orchard" / "graph.db"
    db_a.parent.mkdir(parents=True)
    db_b.parent.mkdir(parents=True)
    db_a.touch()
    db_b.touch()

    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(server_mod.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(server_mod, "_get_conn", lambda project_dir=None: object())
    monkeypatch.setitem(server_mod.HANDLERS, "orchard_search", lambda _arguments: '{"ok": true}')

    async def resolve_project_a():
        return server_mod.ResolvedTarget(
            project_dir=str(project_a),
            db_path=str(db_a),
            source="request_root",
            watcher_eligible=True,
        )

    async def resolve_project_b():
        return server_mod.ResolvedTarget(
            project_dir=str(project_b),
            db_path=str(db_b),
            source="request_root",
            watcher_eligible=True,
        )

    monkeypatch.setattr(server_mod, "_resolve_request_target", resolve_project_a)
    await server_mod.call_tool("orchard_search", {"name": "Foo"})
    monkeypatch.setattr(server_mod, "_resolve_request_target", resolve_project_b)
    await server_mod.call_tool("orchard_search", {"name": "Bar"})

    assert scheduled_projects == [str(project_a), str(project_b)]


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
    server_mod._conn = fake_conn

    monkeypatch.setattr(
        "orchard.ingest.indexstore._orchard_cli_path",
        lambda: "/fake/orchard",
    )

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(server_mod.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await server_mod._run_startup_ingest(str(tmp_path))

    assert fake_conn.closed is True
    assert server_mod._conn is None


@pytest.mark.asyncio
async def test_watch_ingest_state_resets_connection(monkeypatch, tmp_path):
    import orchard.server as server_mod

    state_path = (tmp_path / ".orchard" / "ingest-state.json").resolve()

    class FakeConn:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    async def fake_awatch(*args, **kwargs):
        yield {(1, str(state_path))}

    fake_conn = FakeConn()
    server_mod._conn = fake_conn
    monkeypatch.setattr(server_mod, "awatch", fake_awatch)

    await server_mod._watch_ingest_state(str(tmp_path))

    assert fake_conn.closed is True
    assert server_mod._conn is None
