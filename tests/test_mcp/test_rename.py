"""Tests for USR-precise rename — build_rename_plan, rename_diff, rename_symbol.

AC-R1: build_rename_plan returns correct file/line/col entries
AC-R2: rename_diff generates human-readable diff output
AC-R3: USR not found → graceful error response
AC-R4: no references → empty plan
AC-R5: dry_run mode returns diff without filesystem writes
"""
import pytest
import json
from orchard.graph.db import get_connection, init_schema
from orchard.handlers.rename import (
    RenameRequest, build_rename_plan, rename_diff, rename_symbol,
)


@pytest.fixture
def conn_with_rename_data(tmp_db_path):
    """DB with symbols and occurrences for rename testing."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    # Declared symbol
    conn.execute(
        "CREATE (:Symbol {id: 's:oldFunc', usr: 's:oldFunc', precise_id: '', "
        "name: 'oldFunc', language: 'swift', kind: 'swift.func', module: 'M', "
        "target_id: 'T1', file_path: '/src/main.swift', signature: 'func oldFunc()', "
        "container_usr: '', access_level: 'internal', origin: 'derived', "
        "is_generated: false})"
    )
    # Caller symbol
    conn.execute(
        "CREATE (:Symbol {id: 's:caller', usr: 's:caller', precise_id: '', "
        "name: 'callerFunc', language: 'swift', kind: 'swift.func', module: 'M', "
        "target_id: 'T1', file_path: '/src/main.swift', signature: 'func callerFunc()', "
        "container_usr: '', access_level: 'internal', origin: 'derived', "
        "is_generated: false})"
    )
    # Second caller in different file
    conn.execute(
        "CREATE (:Symbol {id: 's:caller2', usr: 's:caller2', precise_id: '', "
        "name: 'anotherCaller', language: 'swift', kind: 'swift.func', module: 'M', "
        "target_id: 'T1', file_path: '/src/utils.swift', signature: '', "
        "container_usr: '', access_level: 'internal', origin: 'derived', "
        "is_generated: false})"
    )
    # File nodes
    for path in ["/src/main.swift", "/src/utils.swift"]:
        conn.execute(
            f"CREATE (:File {{path: '{path}', module: 'M', language: 'swift', "
            f"target_id: 'T1', is_generated: false}})"
        )
    # Occurrences: definition of oldFunc at main.swift:10:5
    conn.execute(
        "MATCH (f:File {path: '/src/main.swift'}) "
        "CREATE (f)-[:ContainsOccurrence]->"
        "(:Occurrence {id: 'occ-def', usr: 's:oldFunc', file_path: '/src/main.swift', "
        "line: 10, col: 5, role: 'definition'})"
    )
    # Occurrences: reference from callerFunc at main.swift:25:9
    conn.execute(
        "MATCH (f:File {path: '/src/main.swift'}) "
        "CREATE (f)-[:ContainsOccurrence]->"
        "(:Occurrence {id: 'occ-ref1', usr: 's:oldFunc', file_path: '/src/main.swift', "
        "line: 25, col: 9, role: 'reference'})"
    )
    # Occurrences: reference from utils.swift:42:12
    conn.execute(
        "MATCH (f:File {path: '/src/utils.swift'}) "
        "CREATE (f)-[:ContainsOccurrence]->"
        "(:Occurrence {id: 'occ-ref2', usr: 's:oldFunc', file_path: '/src/utils.swift', "
        "line: 42, col: 12, role: 'reference'})"
    )
    # Call edges
    conn.execute(
        "MATCH (c:Symbol {id:'s:caller'}), (t:Symbol {id:'s:oldFunc'}) "
        "CREATE (c)-[:Calls {source:'derived', confidence:1.0, provenance:'indexstore', "
        "build_id:'b1', reason:'source_direct'}]->(t)"
    )
    conn.execute(
        "MATCH (c:Symbol {id:'s:caller2'}), (t:Symbol {id:'s:oldFunc'}) "
        "CREATE (c)-[:Calls {source:'derived', confidence:1.0, provenance:'indexstore', "
        "build_id:'b1', reason:'source_direct'}]->(t)"
    )
    yield conn
    conn.close()


# ── AC-R1: build_rename_plan ──────────────────────────────────────
def test_build_rename_plan_finds_definition_and_references(conn_with_rename_data):
    """AC-R1: Plan contains 1 definition + 2 reference entries."""
    plan = build_rename_plan(conn_with_rename_data, "s:oldFunc", "newFunc")

    assert len(plan) == 3, f"Expected 3 entries (1 def + 2 refs), got {len(plan)}"

    definition = [e for e in plan if e["edit_type"] == "declaration"]
    references = [e for e in plan if e["edit_type"] == "reference"]

    assert len(definition) == 1
    assert definition[0]["file_path"] == "/src/main.swift"
    assert definition[0]["line"] == 10
    assert definition[0]["col"] == 5
    assert definition[0]["old_name"] == "oldFunc"
    assert definition[0]["new_name"] == "newFunc"

    assert len(references) == 2
    ref_files = {r["file_path"] for r in references}
    assert "/src/main.swift" in ref_files
    assert "/src/utils.swift" in ref_files


# ── AC-R2: rename_diff ────────────────────────────────────────────
def test_rename_diff_generates_human_readable_output(conn_with_rename_data):
    """AC-R2: rename_diff produces file-grouped, line-numbered output."""
    plan = build_rename_plan(conn_with_rename_data, "s:oldFunc", "newFunc")
    diff_text = rename_diff(plan)

    assert "oldFunc" in diff_text
    assert "newFunc" in diff_text
    assert "/src/main.swift" in diff_text
    assert "/src/utils.swift" in diff_text
    assert "10" in diff_text and "declaration" in diff_text  # line 10 declaration
    assert "25" in diff_text and "reference" in diff_text   # line 25 reference
    assert "42" in diff_text and "reference" in diff_text   # line 42 reference


# ── AC-R3: USR not found ──────────────────────────────────────────
def test_build_rename_plan_usr_not_found(conn_with_rename_data):
    """AC-R3: Unknown USR returns None (distinct from empty plan)."""
    plan = build_rename_plan(conn_with_rename_data, "s:doesNotExist", "newFunc")
    assert plan is None


# ── AC-R3.1: fallback without occurrence data ──────────────────────
def test_build_rename_plan_fallback_without_occurrences(tmp_db_path):
    """Symbol exists, no Occurrence nodes → uses file_path from Symbol table."""
    from orchard.graph.db import get_connection
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:Symbol {id: 's:noOcc', usr: 's:noOcc', precise_id: '', "
        "name: 'noOccurrenceSymbol', language: 'swift', kind: 'swift.func', "
        "module: 'M', target_id: 'T1', file_path: '/src/none.swift', "
        "signature: '', container_usr: '', access_level: 'internal', "
        "origin: 'derived', is_generated: false})"
    )
    plan = build_rename_plan(conn, "s:noOcc", "newFunc")
    assert len(plan) == 1  # declaration from file_path fallback
    assert plan[0]["file_path"] == "/src/none.swift"
    assert plan[0]["line"] == 0  # no precise location
    conn.close()


def test_rename_symbol_dry_run_works_without_occurrences(tmp_db_path):
    """Symbol with file_path but no occurrence → plan + diff via fallback."""
    from orchard.graph.db import get_connection
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:Symbol {id: 's:noOcc', usr: 's:noOcc', precise_id: '', "
        "name: 'noOccurrenceSymbol', language: 'swift', kind: 'swift.func', "
        "module: 'M', target_id: 'T1', file_path: '/src/none.swift', "
        "signature: '', container_usr: '', access_level: 'internal', "
        "origin: 'derived', is_generated: false})"
    )
    req = RenameRequest(usr="s:noOcc", new_name="newFunc", dry_run=True)
    resp = rename_symbol(conn, req)
    assert resp.data is not None
    assert "search" in resp.data["diff"]  # fallback location label
    assert len(resp.data["plan"]) == 1
    conn.close()


# ── AC-R4: no references ──────────────────────────────────────────
def test_build_rename_plan_no_references(conn_with_rename_data):
    """AC-R4: Symbol with only definition, no references → 1 entry."""
    conn_with_rename_data.execute(
        "CREATE (:Symbol {id: 's:unused', usr: 's:unused', precise_id: '', "
        "name: 'unusedFunc', language: 'swift', kind: 'swift.func', module: 'M', "
        "target_id: 'T1', file_path: '/src/unused.swift', signature: '', "
        "container_usr: '', access_level: 'internal', origin: 'derived', "
        "is_generated: false})"
    )
    conn_with_rename_data.execute(
        "CREATE (:File {path: '/src/unused.swift', module: 'M', language: 'swift', "
        "target_id: 'T1', is_generated: false})"
    )
    conn_with_rename_data.execute(
        "MATCH (f:File {path: '/src/unused.swift'}) "
        "CREATE (f)-[:ContainsOccurrence]->"
        "(:Occurrence {id: 'occ-unused', usr: 's:unused', file_path: '/src/unused.swift', "
        "line: 1, col: 5, role: 'definition'})"
    )
    plan = build_rename_plan(conn_with_rename_data, "s:unused", "usedFunc")
    assert len(plan) == 1
    assert plan[0]["edit_type"] == "declaration"


# ── AC-R5: dry_run returns diff, no writes ────────────────────────
def test_rename_symbol_dry_run_returns_diff_without_writes(conn_with_rename_data, tmp_path):
    """AC-R5: dry_run=True returns plan and diff, does not write files."""
    req = RenameRequest(usr="s:oldFunc", new_name="newFunc", dry_run=True)
    resp = rename_symbol(conn_with_rename_data, req)

    assert resp.data is not None
    assert "diff" in resp.data
    assert "plan" in resp.data
    assert len(resp.data["plan"]) == 3
    assert resp.data["dry_run"] is True
    # Since dry_run, the evidence should mention it
    assert any("dry" in s.lower() for s in resp.evidence_sources)


def test_rename_symbol_no_dry_run_attempts_write(conn_with_rename_data, tmp_path):
    """Non-dry-run with real file paths succeeds on writeable temp files."""
    # Create actual files at the expected paths so write succeeds
    import os
    main_path = tmp_path / "main.swift"
    utils_path = tmp_path / "utils.swift"
    main_path.write_text("func oldFunc() {}\nfunc callerFunc() {\n    oldFunc()\n}\n")
    utils_path.write_text("oldFunc()\n")

    # Use a DB where file_path points to real temp files
    from orchard.graph.db import get_connection
    conn2 = get_connection(str(tmp_path / "rename.db"))
    init_schema(conn2)
    conn2.execute(
        f"CREATE (:Symbol {{id: 's:oldFunc', usr: 's:oldFunc', precise_id: '', "
        f"name: 'oldFunc', language: 'swift', kind: 'swift.func', module: 'M', "
        f"target_id: 'T1', file_path: '{main_path}', signature: 'func oldFunc()', "
        f"container_usr: '', access_level: 'internal', origin: 'derived', "
        f"is_generated: false}})"
    )
    conn2.execute(
        f"CREATE (:Symbol {{id: 's:caller', usr: 's:caller', precise_id: '', "
        f"name: 'callerFunc', language: 'swift', kind: 'swift.func', module: 'M', "
        f"target_id: 'T1', file_path: '{main_path}', signature: '', "
        f"container_usr: '', access_level: 'internal', origin: 'derived', "
        f"is_generated: false}})"
    )
    for path in [str(main_path), str(utils_path)]:
        conn2.execute(
            f"CREATE (:File {{path: '{path}', module: 'M', language: 'swift', "
            f"target_id: 'T1', is_generated: false}})"
        )
    conn2.execute(
        f"MATCH (f:File {{path: '{main_path}'}}) "
        f"CREATE (f)-[:ContainsOccurrence]->"
        f"(:Occurrence {{id: 'occ-def', usr: 's:oldFunc', file_path: '{main_path}', "
        f"line: 1, col: 6, role: 'definition'}})"
    )
    conn2.execute(
        f"MATCH (f:File {{path: '{main_path}'}}) "
        f"CREATE (f)-[:ContainsOccurrence]->"
        f"(:Occurrence {{id: 'occ-ref1', usr: 's:oldFunc', file_path: '{main_path}', "
        f"line: 3, col: 5, role: 'reference'}})"
    )
    conn2.execute(
        f"MATCH (f:File {{path: '{utils_path}'}}) "
        f"CREATE (f)-[:ContainsOccurrence]->"
        f"(:Occurrence {{id: 'occ-ref2', usr: 's:oldFunc', file_path: '{utils_path}', "
        f"line: 1, col: 1, role: 'reference'}})"
    )
    conn2.execute(
        f"MATCH (c:Symbol {{id:'s:caller'}}), (t:Symbol {{id:'s:oldFunc'}}) "
        f"CREATE (c)-[:Calls {{source:'derived', confidence:1.0, provenance:'indexstore', "
        f"build_id:'b1', reason:'source_direct'}}]->(t)"
    )

    req = RenameRequest(usr="s:oldFunc", new_name="newFunc", dry_run=False)
    resp = rename_symbol(conn2, req)

    assert resp.data is not None
    assert resp.data["dry_run"] is False
    assert resp.data["files_modified"] > 0

    # Verify files were actually modified
    main_content = main_path.read_text()
    assert "oldFunc" not in main_content
    assert "newFunc" in main_content

    utils_content = utils_path.read_text()
    assert "oldFunc" not in utils_content
    assert "newFunc" in utils_content

    conn2.close()


def test_rename_symbol_empty_new_name_rejected(conn_with_rename_data):
    """Empty new_name returns an error response."""
    req = RenameRequest(usr="s:oldFunc", new_name="")
    resp = rename_symbol(conn_with_rename_data, req)
    assert resp.data is None
    assert any("new_name" in gap.lower() for gap in resp.open_gaps)
