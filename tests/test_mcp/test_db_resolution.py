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


def test_resolve_target_prefers_request_root_graph_db(monkeypatch, tmp_path):
    import orchard.server as server_mod

    repo_root = tmp_path / "repo"
    db_path = repo_root / ".orchard" / "graph.db"
    db_path.parent.mkdir(parents=True)
    db_path.touch()

    monkeypatch.setattr(server_mod, "_DB_PATH", "/configured/graph.db")
    monkeypatch.setenv("ORCHARD_DB_PATH", "/env/graph.db")

    target = server_mod._resolve_target(str(repo_root))

    assert target.project_dir == str(repo_root.resolve())
    assert target.db_path == str(db_path.resolve())
    assert target.source == "request_root"
    assert target.watcher_eligible is True


def test_resolve_target_uses_cli_db_without_watcher(monkeypatch, tmp_path):
    import orchard.server as server_mod

    db_path = tmp_path / "external.db"
    db_path.touch()

    monkeypatch.setattr(server_mod, "_DB_PATH", str(db_path))

    target = server_mod._resolve_target(None)

    assert target.project_dir is None
    assert target.db_path == str(db_path.resolve())
    assert target.source == "cli_db"
    assert target.watcher_eligible is False


def test_get_conn_raises_clear_error_without_request_root_or_explicit_db():
    import orchard.server as server_mod

    with pytest.raises(RuntimeError, match="No Orchard graph database configured"):
        server_mod._get_conn()


@pytest.mark.asyncio
async def test_call_tool_only_schedules_ingest_for_watcher_eligible_target(monkeypatch):
    import orchard.server as server_mod

    scheduled_projects: list[str] = []
    get_conn_args: list[str | None] = []

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

    watcher_target = server_mod.ResolvedTarget(
        project_dir="/tmp/repo",
        db_path="/tmp/repo/.orchard/graph.db",
        source="request_root",
        watcher_eligible=True,
    )
    explicit_target = server_mod.ResolvedTarget(
        project_dir=None,
        db_path="/tmp/external.db",
        source="cli_db",
        watcher_eligible=False,
    )

    monkeypatch.setattr(server_mod.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(server_mod.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setitem(server_mod.HANDLERS, "fake_tool", lambda _args: "ok")
    monkeypatch.setattr(
        server_mod,
        "_get_conn",
        lambda project_dir=None: get_conn_args.append(project_dir) or object(),
    )

    async def resolve_watcher_target():
        return watcher_target

    async def resolve_explicit_target():
        return explicit_target

    monkeypatch.setattr(server_mod, "_resolve_request_target", resolve_watcher_target)
    first = await server_mod.call_tool("fake_tool", {})

    monkeypatch.setattr(server_mod, "_resolve_request_target", resolve_explicit_target)
    second = await server_mod.call_tool("fake_tool", {})

    assert [item.text for item in first] == ["ok"]
    assert [item.text for item in second] == ["ok"]
    assert scheduled_projects == ["/tmp/repo"]
    assert get_conn_args == ["/tmp/repo", None]


@pytest.mark.asyncio
async def test_lifespan_does_not_open_connection_or_start_watcher(monkeypatch):
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


def test_get_conn_caches_per_db_path(monkeypatch, tmp_path):
    import orchard.server as server_mod

    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    db_a = repo_a / ".orchard" / "graph.db"
    db_b = repo_b / ".orchard" / "graph.db"
    db_a.parent.mkdir(parents=True)
    db_b.parent.mkdir(parents=True)
    db_a.touch()
    db_b.touch()

    conn_a = object()
    conn_b = object()

    calls: list[str] = []

    def fake_get_connection(path, read_only=True):
        calls.append(path)
        if path == str(db_a.resolve()):
            return conn_a
        if path == str(db_b.resolve()):
            return conn_b
        raise AssertionError(f"unexpected db path: {path}")

    monkeypatch.setattr("orchard.graph.db.get_connection", fake_get_connection)

    first_a = server_mod._get_conn(str(repo_a))
    second_a = server_mod._get_conn(str(repo_a))
    first_b = server_mod._get_conn(str(repo_b))

    assert first_a is conn_a
    assert second_a is conn_a
    assert first_b is conn_b
    assert calls == [str(db_a.resolve()), str(db_b.resolve())]
