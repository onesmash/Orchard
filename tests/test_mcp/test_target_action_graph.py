from orchard.graph.db import get_connection, init_schema
from orchard.handlers.target_action_graph import (
    get_target_action_graph,
    TargetActionGraphRequest,
)


def test_target_action_graph_filters_by_callback_usr(tmp_db_path, tmp_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)

    source_file = tmp_path / "ToggleCell.mm"
    source_file.write_text("""\
@implementation ToggleCell
- (void)setupToggle {
    [self.toggle addTarget:self action:@selector(onToggle:) forControlEvents:UIControlEventValueChanged];
}
- (void)onToggle:(id)sender {
}
@end
""")

    conn.execute(
        "CREATE (:BuildSnapshot {id: 'b1', build_system: 'xcodebuild', "
        f"workspace_root: '{tmp_path}', created_at: '2026-07-01T00:00:00+00:00'}})"
    )

    for sym_data in [
        ("s:setupToggle", "setupToggle", "objc", "method", str(source_file), "MyModule"),
        ("s:onToggle", "onToggle:", "objc", "method", str(source_file), "MyModule"),
        ("s:addTarget", "addTarget:action:forControlEvents:", "objc", "method",
         "/System/Library/Frameworks/UIKit.framework/Headers/UIControl.h", "UIKit"),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sym_data[0]}', usr: '{sym_data[0]}', precise_id: '', "
            f"name: '{sym_data[1]}', language: '{sym_data[2]}', kind: '{sym_data[3]}', "
            f"module: '{sym_data[5]}', file_path: '{sym_data[4]}', signature: '', "
            f"container_usr: '', access_level: 'internal', origin: 'derived', is_generated: false}})"
        )

    conn.execute(
        "MATCH (c:Symbol {id:'s:setupToggle'}), (t:Symbol {id:'s:addTarget'}) "
        "CREATE (c)-[:Calls {source:'derived', confidence:1.0, provenance:'indexstore', "
        "build_id:'b1', reason:'source_direct'}]->(t)"
    )

    resp = get_target_action_graph(
        conn,
        TargetActionGraphRequest(callback_usr="s:onToggle", build_id="b1"),
    )

    assert len(resp.data["callbacks"]) == 1
    callback_group = next(iter(resp.data["callbacks"].values()))
    assert callback_group["callback"]["name"] == "onToggle:"
    assert callback_group["bindings"][0]["selector"] == "onToggle:"

    conn.close()


def test_target_action_graph_groups_by_registrar(tmp_db_path, tmp_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)

    source_file = tmp_path / "ToggleCell.mm"
    source_file.write_text("""\
@implementation ToggleCell
- (void)setupToggle {
    [self.toggle addTarget:self action:@selector(onToggle:) forControlEvents:UIControlEventValueChanged];
}
- (void)onToggle:(id)sender {
}
@end
""")

    conn.execute(
        "CREATE (:BuildSnapshot {id: 'b1', build_system: 'xcodebuild', "
        f"workspace_root: '{tmp_path}', created_at: '2026-07-01T00:00:00+00:00'}})"
    )

    for sym_data in [
        ("s:setupToggle", "setupToggle", "objc", "method", str(source_file), "MyModule"),
        ("s:onToggle", "onToggle:", "objc", "method", str(source_file), "MyModule"),
        ("s:addTarget", "addTarget:action:forControlEvents:", "objc", "method",
         "/System/Library/Frameworks/UIKit.framework/Headers/UIControl.h", "UIKit"),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sym_data[0]}', usr: '{sym_data[0]}', precise_id: '', "
            f"name: '{sym_data[1]}', language: '{sym_data[2]}', kind: '{sym_data[3]}', "
            f"module: '{sym_data[5]}', file_path: '{sym_data[4]}', signature: '', "
            f"container_usr: '', access_level: 'internal', origin: 'derived', is_generated: false}})"
        )

    conn.execute(
        "MATCH (c:Symbol {id:'s:setupToggle'}), (t:Symbol {id:'s:addTarget'}) "
        "CREATE (c)-[:Calls {source:'derived', confidence:1.0, provenance:'indexstore', "
        "build_id:'b1', reason:'source_direct'}]->(t)"
    )

    resp = get_target_action_graph(
        conn,
        TargetActionGraphRequest(group_by="registrar", build_id="b1"),
    )

    assert len(resp.data["registrars"]) == 1
    registrar_group = next(iter(resp.data["registrars"].values()))
    assert registrar_group["registrar"]["name"] == "setupToggle"
    assert registrar_group["bindings"][0]["control_event"] == "UIControlEventValueChanged"

    conn.close()
