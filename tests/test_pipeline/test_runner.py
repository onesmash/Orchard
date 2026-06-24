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
        mock_is.return_value = IndexStoreResult()
        mock_sg.return_value = SymbolGraphResult()
        results = await run_ingest_pipeline(ctx, db_path=tmp_db_path)
    phases = [r.phase for r in results]
    assert "indexstore_ingest" in phases
    assert "identity_normalization" in phases
