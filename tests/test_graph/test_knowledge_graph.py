"""Tests for in-memory KnowledgeGraph with dual indexing."""
from orchard.graph.knowledge_graph import KnowledgeGraph


class TestKnowledgeGraph:
    def test_add_node(self):
        kg = KnowledgeGraph()
        kg.add_node("n1", {"name": "A", "kind": "class"})
        assert "n1" in kg.node_map
        assert kg.node_map["n1"]["name"] == "A"

    def test_add_rel(self):
        kg = KnowledgeGraph()
        kg.add_node("n1", {"name": "A"})
        kg.add_node("n2", {"name": "B"})
        kg.add_rel("r1", "Calls", "n1", "n2", {"confidence": 0.9})
        assert "r1" in kg.rel_map
        assert "r1" in kg.rels_by_type["Calls"]

    def test_iter_rels_by_type(self):
        kg = KnowledgeGraph()
        kg.add_node("n1", {})
        kg.add_node("n2", {})
        kg.add_rel("r1", "Calls", "n1", "n2", {})
        kg.add_rel("r2", "Calls", "n2", "n1", {})
        kg.add_rel("r3", "Contains", "n1", "n2", {})
        calls = list(kg.iter_rels_by_type("Calls"))
        assert len(calls) == 2
        contains = list(kg.iter_rels_by_type("Contains"))
        assert len(contains) == 1

    def test_remove_node_cleans_edges(self):
        kg = KnowledgeGraph()
        kg.add_node("n1", {})
        kg.add_node("n2", {})
        kg.add_rel("r1", "Calls", "n1", "n2", {})
        kg.remove_node("n1")
        assert "n1" not in kg.node_map
        assert "r1" not in kg.rel_map

    def test_remove_nodes_by_file(self):
        kg = KnowledgeGraph()
        kg.add_node("n1", {"file_path": "/a.swift"})
        kg.add_node("n2", {"file_path": "/a.swift"})
        kg.add_node("n3", {"file_path": "/b.swift"})
        kg.remove_nodes_by_file("/a.swift")
        assert "n1" not in kg.node_map
        assert "n2" not in kg.node_map
        assert "n3" in kg.node_map

    def test_freeze_prevents_mutation(self):
        kg = KnowledgeGraph()
        kg.add_node("n1", {})
        kg.freeze()
        try:
            kg.add_node("n2", {})
            assert False, "should have raised"
        except RuntimeError:
            pass

    def test_node_ids_by_file_tracks_paths(self):
        kg = KnowledgeGraph()
        kg.add_node("n1", {"file_path": "/x.swift"})
        kg.add_node("n2", {"file_path": "/x.swift"})
        assert kg.node_ids_by_file["/x.swift"] == {"n1", "n2"}
