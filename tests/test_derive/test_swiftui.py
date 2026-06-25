"""Tests for swiftui_derivation phase."""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import make_symbol_id, upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.derive.swiftui import run_swiftui_derivation


def _seed_structs(conn, target_id, structs: list[tuple[str, str, str]]):
    """Seed Symbol nodes. Each tuple is (usr, name, module)."""
    records = [
        SymbolRecord(
            usr=usr, precise_id="", name=name, kind="struct",
            module=mod, language="swift", file_path="", signature="",
            access_level="public", container_usr=None,
        )
        for usr, name, mod in structs
    ]
    upsert_symbols(conn, records, target_id)


def test_swiftui_derivation_creates_view_tree_edges(tmp_db_path):
    """Structs in the same module get ViewTree edges from the first to others."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "MyApp"

    _seed_structs(conn, target_id, [
        ("s:ContentView", "ContentView", "MyApp"),
        ("s:HeaderView", "HeaderView", "MyApp"),
        ("s:FooterView", "FooterView", "MyApp"),
    ])

    stats = run_swiftui_derivation(conn, target_id, build_id="b1")
    # ContentView -> HeaderView, ContentView -> FooterView = 2 ViewTree edges
    assert stats["view_tree_edges"] == 2
    assert stats["nav_flow_edges"] == 0

    rows = conn.execute(
        "MATCH (a:Symbol)-[r:ViewTree]->(b:Symbol) "
        "RETURN a.name, b.name, r.confidence, r.derived_from, r.build_id"
    ).get_all()
    assert len(rows) == 2
    sources = {r[0] for r in rows}
    targets = {r[1] for r in rows}
    assert sources == {"ContentView"}
    assert targets == {"HeaderView", "FooterView"}
    for r in rows:
        assert float(r[2]) == 0.70
        assert r[3] == "derive/swiftui"
        assert r[4] == "b1"

    conn.close()


def test_swiftui_derivation_creates_navigation_flow_edges(tmp_db_path):
    """Structs with navigation-like names get NavigationFlow edges."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "MyApp"

    _seed_structs(conn, target_id, [
        ("s:HomeView", "HomeView", "MyApp"),
        ("s:SettingsLink", "SettingsLink", "MyApp"),
        ("s:ProfileNav", "ProfileNav", "MyApp"),
    ])

    stats = run_swiftui_derivation(conn, target_id, build_id="b1")
    # ViewTree: HomeView -> SettingsLink, HomeView -> ProfileNav = 2
    # NavigationFlow: SettingsLink -> HomeView, ProfileNav -> HomeView = 2
    assert stats["view_tree_edges"] == 2
    assert stats["nav_flow_edges"] == 2

    nf_rows = conn.execute(
        "MATCH (a:Symbol)-[r:NavigationFlow]->(b:Symbol) "
        "RETURN a.name, b.name, r.confidence, r.derived_from"
    ).get_all()
    assert len(nf_rows) == 2
    for r in nf_rows:
        assert r[1] == "HomeView"  # destination is HomeView
        assert r[0] in ("SettingsLink", "ProfileNav")
        assert float(r[2]) == 0.70
        assert r[3] == "derive/swiftui"

    conn.close()


def test_swiftui_derivation_idempotent(tmp_db_path):
    """Second run with same data reports zero new edges (idempotent via MERGE)."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "MyApp"

    _seed_structs(conn, target_id, [
        ("s:RootView", "RootView", "MyApp"),
        ("s:ChildView", "ChildView", "MyApp"),
    ])

    stats1 = run_swiftui_derivation(conn, target_id, build_id="b1")
    assert stats1["view_tree_edges"] == 1

    stats2 = run_swiftui_derivation(conn, target_id, build_id="b1")
    assert stats2["view_tree_edges"] == 0
    assert stats2["nav_flow_edges"] == 0

    # Only 1 edge total (no duplicates).
    rows = conn.execute("MATCH ()-[r:ViewTree]->() RETURN count(r)").get_all()
    assert rows[0][0] == 1

    conn.close()


def test_swiftui_derivation_single_struct_no_edges(tmp_db_path):
    """A single struct in a module produces no edges (need at least 2)."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "Solo"

    _seed_structs(conn, target_id, [
        ("s:LoneView", "LoneView", "Solo"),
    ])

    stats = run_swiftui_derivation(conn, target_id, build_id="b1")
    assert stats["view_tree_edges"] == 0
    assert stats["nav_flow_edges"] == 0

    conn.close()


def test_swiftui_derivation_no_structs(tmp_db_path):
    """No struct Symbols means zero edges produced."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "Empty"

    stats = run_swiftui_derivation(conn, target_id, build_id="b1")
    assert stats["view_tree_edges"] == 0
    assert stats["nav_flow_edges"] == 0

    conn.close()


def test_swiftui_derivation_multiple_modules(tmp_db_path):
    """Structs in different modules get separate ViewTree edges per module."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "T"

    _seed_structs(conn, target_id, [
        ("s:AV", "AView", "ModA"),
        ("s:AB", "AButton", "ModA"),
        ("s:BV", "BView", "ModB"),
        ("s:BL", "BLabel", "ModB"),
    ])

    stats = run_swiftui_derivation(conn, target_id, build_id="b1")
    # ModA: AView -> AButton = 1
    # ModB: BView -> BLabel = 1
    assert stats["view_tree_edges"] == 2
    assert stats["nav_flow_edges"] == 0

    conn.close()
