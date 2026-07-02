import pytest


@pytest.mark.asyncio
async def test_lifespan_schedules_startup_ingest_without_blocking(monkeypatch):
    import orchard.server as server_mod

    events: list[str] = []
    original_task = server_mod._startup_ingest_task
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

    fake_startup_task = FakeTask()
    fake_watch_task = FakeTask()

    def fake_create_task(coro):
        events.append(coro.cr_code.co_name)
        coro.close()
        if coro.cr_code.co_name == "_run_startup_ingest":
            return fake_startup_task
        return fake_watch_task

    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(server_mod, "_get_conn", lambda: events.append("get_conn"))

    try:
        async with server_mod._lifespan(server_mod.app):
            assert events == ["_run_startup_ingest", "_watch_ingest_state", "get_conn"]
    finally:
        server_mod._startup_ingest_task = original_task
        server_mod._ingest_state_watch_task = original_watch_task
        server_mod._conn = original_conn

    assert "cancel" in events


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
        await server_mod._run_startup_ingest()
    finally:
        server_mod._conn = original_conn

    assert fake_conn.closed is True
    assert server_mod._conn is None


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
