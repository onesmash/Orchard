import pytest
from unittest.mock import patch, MagicMock
from orchard.pipeline.runner import PhaseResult, run_ingest_pipeline
from orchard.build.context import BuildContext, make_build_id


@pytest.fixture
def ctx():
    c = BuildContext(
        build_id="", build_system="swift_build",
        workspace_root="/tmp/pkg", scheme=None, target="MyLib",
        configuration="debug", sdk="macosx14.5",
        triple="arm64-apple-macosx14.5", toolchain_id="swift-5.10",
        derived_data_path="/tmp/dd", index_store_path="/tmp/dd/IndexStore",
        symbolgraph_output_path=None, commit_sha=None, build_config_hash="abc",
    )
    c.build_id = make_build_id(c)
    return c


def test_phase_result_fields():
    r = PhaseResult(phase="test", build_id="b1", data=None)
    assert r.phase == "test"
    assert r.stats == {}
    assert r.warnings == []


@pytest.mark.asyncio
async def test_run_ingest_pipeline_returns_results(ctx, tmp_db_path):
    with (
        patch("orchard.pipeline.runner.read_index_store") as mock_is,
        patch("orchard.pipeline.runner.parse_symbolgraph") as mock_sg,
        patch("orchard.pipeline.runner.discover_symbolgraph_paths", return_value=[]),
    ):
        from orchard.ingest.indexstore import IndexStoreResult
        from orchard.ingest.symbolgraph import SymbolGraphResult
        mock_is.return_value = (IndexStoreResult(), None)
        mock_sg.return_value = SymbolGraphResult()
        results = await run_ingest_pipeline(ctx, db_path=tmp_db_path)
    phases = [r.phase for r in results]
    assert "indexstore_ingest" in phases
    assert "identity_normalization" in phases


@pytest.mark.asyncio
async def test_pipeline_writes_calls_then_handlers_return_data(ctx, tmp_db_path):
    from unittest.mock import patch
    from orchard.ingest import indexstore as is_mod
    from orchard.ingest.symbolgraph import SymbolRecord, SymbolGraphResult
    from orchard.pipeline.runner import run_ingest_pipeline
    from orchard.handlers.callers import find_callers, CallerRequest
    from orchard.handlers.callees import find_callees, CalleeRequest

    # Synthetic IndexStore JSONL: callee is calledBy caller => caller calls callee.
    # occurrence_role "call" marks this as a source-level call-site (source_direct).
    indexstore_jsonl = (
        '{"kind": "relation", "from_usr": "c:callee()", '
        '"to_usr": "c:caller()", "role": "calledBy", "occurrence_role": "call"}\n'
    )
    # Synthetic symbolgraph: two functions exist as Symbol nodes
    sg = SymbolGraphResult(
        symbols=[
            SymbolRecord(usr="c:caller()", precise_id="", name="caller",
                         kind="function", module="M", language="swift",
                         file_path=None, signature=None,
                         access_level="public", container_usr=None),
            SymbolRecord(usr="c:callee()", precise_id="", name="callee",
                         kind="function", module="M", language="swift",
                         file_path=None, signature=None,
                         access_level="public", container_usr=None),
        ],
        relationships=[],
    )
    with (
        patch.object(is_mod, "_run_cli", side_effect=lambda *a, **kw: ([l for l in indexstore_jsonl.split("\n") if l.strip()], "")),
        patch("orchard.pipeline.runner.parse_symbolgraph", return_value=sg),
        patch("orchard.pipeline.runner.discover_symbolgraph_paths",
              return_value=["/x.json"]),
    ):
        results = await run_ingest_pipeline(ctx, db_path=tmp_db_path)

    phases = [r.phase for r in results]
    assert "call_graph_derivation" in phases
    cg = next(r for r in results if r.phase == "call_graph_derivation")
    assert cg.stats["calls_written"] == 1

    from orchard.graph.db import get_connection
    conn = get_connection(tmp_db_path)
    callers = find_callers(conn, CallerRequest(
        usr="c:callee()", target_id="MyLib", build_id=ctx.build_id))
    assert any(d["usr"] == "c:caller()" for d in callers.data)

    callees = find_callees(conn, CalleeRequest(
        usr="c:caller()", target_id="MyLib", build_id=ctx.build_id))
    assert any(d["usr"] == "c:callee()" for d in callees.data)
    conn.close()


@pytest.mark.asyncio
async def test_pipeline_includes_bridge_recovery_phase(ctx, tmp_db_path):
    from unittest.mock import patch
    from orchard.ingest.indexstore import IndexStoreResult
    from orchard.ingest.symbolgraph import SymbolGraphResult
    with (
        patch("orchard.pipeline.runner.read_index_store", return_value=(IndexStoreResult(), None)),
        patch("orchard.pipeline.runner.parse_symbolgraph", return_value=SymbolGraphResult()),
        patch("orchard.pipeline.runner.discover_symbolgraph_paths", return_value=[]),
    ):
        results = await run_ingest_pipeline(ctx, db_path=tmp_db_path)
    phases = [r.phase for r in results]
    assert "cross_language_bridge_recovery" in phases


@pytest.mark.asyncio
async def test_pipeline_embedding_projection_handles_embedder_down(ctx, tmp_db_path):
    """When the embedder is unreachable, embedding_projection phase still appears
    with embedded=0 and a warning."""
    from unittest.mock import patch
    from orchard.ingest.indexstore import IndexStoreResult
    from orchard.ingest.symbolgraph import SymbolGraphResult
    from orchard.search.embedder import EmbeddingError

    with (
        patch("orchard.pipeline.runner.read_index_store", return_value=(IndexStoreResult(), None)),
        patch("orchard.pipeline.runner.parse_symbolgraph", return_value=SymbolGraphResult()),
        patch("orchard.pipeline.runner.discover_symbolgraph_paths", return_value=[]),
        patch("orchard.pipeline.runner.Embedder.__init__",
              side_effect=EmbeddingError("Connection refused")),
    ):
        results = await run_ingest_pipeline(ctx, db_path=tmp_db_path)

    phases = [r.phase for r in results]
    assert "embedding_projection" in phases

    embed_phase = next(r for r in results if r.phase == "embedding_projection")
    assert embed_phase.stats["embedded"] == 0
    assert len(embed_phase.warnings) > 0
    assert any("Embedding unavailable" in w for w in embed_phase.warnings)


@pytest.mark.asyncio
async def test_pipeline_merge_prefers_indexstore_path_and_name(ctx, tmp_db_path):
    from orchard.ingest.indexstore import IndexStoreResult, SymbolLineRecord
    from orchard.ingest.symbolgraph import SymbolRecord, SymbolGraphResult
    from orchard.graph.db import get_connection

    sg = SymbolGraphResult(
        symbols=[
            SymbolRecord(
                usr="c:objc(cs)Demo(im)doThing:",
                precise_id="c:objc(cs)Demo(im)doThing:",
                name="doThing(_:)",
                kind="method",
                module="M",
                language="objc",
                file_path="/wrong/Reference.swift",
                signature="func doThing(_ value: Int)",
                access_level="public",
                container_usr=None,
            )
        ],
        relationships=[],
    )
    is_result = IndexStoreResult(
        symbols=[
            SymbolLineRecord(
                usr="c:objc(cs)Demo(im)doThing:",
                name="doThing:",
                symbol_kind="InstanceMethod",
                language="objc",
                module="M",
                file_path="/right/Demo.m",
            )
        ]
    )

    with (
        patch("orchard.pipeline.runner.read_index_store", return_value=(is_result, None)),
        patch("orchard.pipeline.runner.parse_symbolgraph", return_value=sg),
        patch("orchard.pipeline.runner.discover_symbolgraph_paths", return_value=["/x.json"]),
    ):
        await run_ingest_pipeline(ctx, db_path=tmp_db_path)

    conn = get_connection(tmp_db_path)
    rows = conn.execute(
        "MATCH (s:Symbol {id: $id}) "
        "RETURN s.name, s.file_path, s.swift_display_name",
        {"id": "c:objc(cs)Demo(im)doThing:"},
    ).get_all()
    conn.close()

    assert rows == [["doThing:", "/right/Demo.m", "doThing(_:)"]]
