"""Tests for Kahn topological sort."""
from orchard.pipeline.kahn import kahn_sort


def test_kahn_linear():
    phases = [
        ("c", ["b"]),
        ("b", ["a"]),
        ("a", []),
    ]
    order = kahn_sort(phases)
    assert order.index("a") < order.index("b") < order.index("c")


def test_kahn_diamond():
    phases = [
        ("d", ["b", "c"]),
        ("b", ["a"]),
        ("c", ["a"]),
        ("a", []),
    ]
    order = kahn_sort(phases)
    assert order.index("a") < order.index("b")
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_kahn_parallel():
    phases = [
        ("b", []),
        ("a", []),
    ]
    order = kahn_sort(phases)
    assert set(order) == {"a", "b"}


def test_kahn_cycle_detection():
    phases = [
        ("a", ["b"]),
        ("b", ["a"]),
    ]
    try:
        kahn_sort(phases)
        assert False, "should have raised"
    except ValueError as e:
        assert "cycle" in str(e)


def test_kahn_empty():
    assert kahn_sort([]) == []
