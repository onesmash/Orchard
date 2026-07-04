"""Tests for the Symbol chunker."""

from __future__ import annotations

from orchard.graph.db import get_connection, init_schema
from orchard.search.chunker import ChunkRecord, chunk_symbols


class TestChunkSymbols:
    """Test suite for chunk_symbols()."""

    def test_chunk_symbols_returns_records_for_seeded_symbols(
        self, tmp_db_path: str,
    ) -> None:
        """Seeds 2 Symbol nodes (struct + function) and asserts correct chunks."""
        conn = get_connection(tmp_db_path)
        init_schema(conn)

        # Seed Symbol nodes
        conn.execute(
            "CREATE (:Symbol {"
            "id: 'MyApp:Foo', usr: 's:Foo', name: 'Foo', kind: 'struct',"
            "signature: 'public struct Foo'"
            "})",
        )
        conn.execute(
            "CREATE (:Symbol {"
            "id: 'MyApp:doIt', usr: 's:doIt()', name: 'doIt', kind: 'function',"
            "signature: 'func doIt()'"
            "})",
        )

        chunks = chunk_symbols(conn, "MyApp")

        assert len(chunks) == 2
        assert all(isinstance(c, ChunkRecord) for c in chunks)

        # Struct chunk
        struct_chunk = next(c for c in chunks if c.owner_usr == "s:Foo")
        assert struct_chunk.chunk_kind == "type"
        assert struct_chunk.content == "struct Foo: public struct Foo"
        assert struct_chunk.chunk_id.startswith("MyApp:s:Foo:chunk:type:")

        # Function chunk
        func_chunk = next(c for c in chunks if c.owner_usr == "s:doIt()")
        assert func_chunk.chunk_kind == "method"
        assert func_chunk.content == "function doIt: func doIt()"
        assert func_chunk.chunk_id.startswith("MyApp:s:doIt():chunk:method:")

        conn.close()

    def test_chunk_symbols_empty_for_no_symbols(self, tmp_db_path: str) -> None:
        """Returns empty list when target has no Symbol nodes."""
        conn = get_connection(tmp_db_path)
        init_schema(conn)

        chunks = chunk_symbols(conn, "EmptyTarget")
        assert chunks == []

        conn.close()

    def test_chunk_symbols_treats_missing_signature_as_empty(
        self, tmp_db_path: str,
    ) -> None:
        """Symbols with NULL signature produce content without a trailing colon."""
        conn = get_connection(tmp_db_path)
        init_schema(conn)

        conn.execute(
            "CREATE (:Symbol {"
            "id: 'MyApp:Bar', usr: 's:Bar', name: 'Bar', kind: 'class'"
            "})",
        )

        chunks = chunk_symbols(conn, "MyApp")
        assert len(chunks) == 1
        assert chunks[0].content == "class Bar"
        assert chunks[0].chunk_kind == "type"

        conn.close()

    def test_chunk_symbols_reads_whole_compiled_scope_without_target_filter(
        self, tmp_db_path: str,
    ) -> None:
        conn = get_connection(tmp_db_path)
        init_schema(conn)

        conn.execute(
            "CREATE (:Symbol {"
            "id: 's:MyAppOnly', usr: 's:MyAppOnly', name: 'MyAppOnly', kind: 'function',"
            "signature: 'func MyAppOnly()'"
            "})",
        )
        conn.execute(
            "CREATE (:Symbol {"
            "id: 's:MyPSOnly', usr: 's:MyPSOnly', name: 'MyPSOnly', kind: 'function',"
            "signature: 'func MyPSOnly()'"
            "})",
        )

        chunks = chunk_symbols(conn, "compiled-scope")

        assert {c.owner_usr for c in chunks} == {"s:MyAppOnly", "s:MyPSOnly"}
        assert all(c.chunk_id.startswith("compiled-scope:") for c in chunks)

        conn.close()
