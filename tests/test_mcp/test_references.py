from orchard.graph.db import get_connection, init_schema
from orchard.handlers.references import find_references, ReferencesRequest


def test_find_references_includes_target_action_bridges(tmp_db_path, tmp_path):
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
            f"CREATE (:Symbol {{id: '{sym_data[0]}', usr: '{sym_data[0]}', "
            f"precise_id: '', name: '{sym_data[1]}', language: '{sym_data[2]}', "
            f"kind: '{sym_data[3]}', module: '{sym_data[5]}', "
            f"file_path: '{sym_data[4]}', signature: '', container_usr: '', "
            f"access_level: 'internal', origin: 'derived', is_generated: false}})"
        )

    conn.execute(
        "MATCH (c:Symbol {id:'s:setupToggle'}), (t:Symbol {id:'s:addTarget'}) "
        "CREATE (c)-[:Calls {source:'derived', confidence:1.0, provenance:'indexstore', "
        "build_id:'b1', reason:'source_direct'}]->(t)"
    )

    resp = find_references(conn, ReferencesRequest(usr="s:setupToggle", build_id="b1"))
    callee = next(item for item in resp.data["outgoing"] if item["name"] == "addTarget:action:forControlEvents:")

    assert callee["semantic_role"] == "target_action"
    bridge = callee["target_action_bridges"][0]
    assert bridge["selector"] == "onToggle:"
    assert bridge["control_event"] == "UIControlEventValueChanged"
    assert bridge["callback"]["name"] == "onToggle:"

    conn.close()
