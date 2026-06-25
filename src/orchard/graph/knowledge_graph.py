"""In-memory KnowledgeGraph with dual indexing for pipeline phases.

Inspired by GitNexus's KnowledgeGraph pattern.  Phases write to this
accumulator; the final phase flushes everything to LadybugDB via COPY FROM.

Supports: node/rel CRUD, per-type relationship iteration (O(1) delete),
per-file node removal, and mutable→frozen lifecycle.
"""

from __future__ import annotations


class KnowledgeGraph:
    """Mutable graph accumulator with type-indexed relationships."""

    def __init__(self) -> None:
        self.node_map: dict[str, dict] = {}
        self.rel_map: dict[str, dict] = {}
        # type → rel_id → rel_dict for O(1) delete
        self.rels_by_type: dict[str, dict[str, dict]] = {}
        self.edge_ids_by_node: dict[str, set[str]] = {}
        self.node_ids_by_file: dict[str, set[str]] = {}
        self._frozen = False

    # -- mutation guard -------------------------------------------------------

    def _check_mutable(self) -> None:
        if self._frozen:
            raise RuntimeError("KnowledgeGraph is frozen")

    def freeze(self) -> None:
        """Seal the graph; further mutations raise RuntimeError."""
        self._frozen = True

    # -- nodes ----------------------------------------------------------------

    def add_node(self, node_id: str, props: dict) -> None:
        self._check_mutable()
        self.node_map[node_id] = props
        fp = props.get("file_path", "")
        if fp:
            self.node_ids_by_file.setdefault(fp, set()).add(node_id)

    def remove_node(self, node_id: str) -> None:
        self._check_mutable()
        self.node_map.pop(node_id, None)
        # Remove all edges incident to this node
        for rel_id in list(self.edge_ids_by_node.get(node_id, set())):
            self.rel_map.pop(rel_id, None)
            for tmap in self.rels_by_type.values():
                tmap.pop(rel_id, None)
        self.edge_ids_by_node.pop(node_id, None)

    def remove_nodes_by_file(self, file_path: str) -> None:
        """Remove all nodes and their edges for a given file path."""
        self._check_mutable()
        for node_id in list(self.node_ids_by_file.get(file_path, set())):
            self.remove_node(node_id)
        self.node_ids_by_file.pop(file_path, None)

    # -- relationships --------------------------------------------------------

    def add_rel(self, rel_id: str, rel_type: str,
                from_id: str, to_id: str, props: dict) -> None:
        self._check_mutable()
        props["from"] = from_id
        props["to"] = to_id
        self.rel_map[rel_id] = props
        self.rels_by_type.setdefault(rel_type, {})[rel_id] = props
        self.edge_ids_by_node.setdefault(from_id, set()).add(rel_id)
        self.edge_ids_by_node.setdefault(to_id, set()).add(rel_id)

    def iter_rels_by_type(self, rel_type: str):
        """Yield (rel_id, props) tuples for a given relationship type."""
        for rel_id, props in self.rels_by_type.get(rel_type, {}).items():
            yield rel_id, props
