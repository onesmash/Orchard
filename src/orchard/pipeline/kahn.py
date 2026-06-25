"""Kahn topological sort for pipeline phase ordering."""

from __future__ import annotations

from collections import deque


def kahn_sort(phases: list[tuple[str, list[str]]]) -> list[str]:
    """Return phase names in topological order via Kahn's algorithm.

    Each tuple is (name, [dep_names]).  Raises ValueError on cycle.
    """
    in_degree: dict[str, int] = {}
    adj: dict[str, list[str]] = {}

    for name, deps in phases:
        in_degree.setdefault(name, 0)
        adj.setdefault(name, [])
        for dep in deps:
            adj.setdefault(dep, []).append(name)
            in_degree[name] = in_degree.get(name, 0) + 1
            in_degree.setdefault(dep, 0)

    queue = deque([n for n, d in in_degree.items() if d == 0])
    result: list[str] = []

    while queue:
        node = queue.popleft()
        result.append(node)
        for neighbor in adj.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(result) != len(in_degree):
        raise ValueError(f"cycle detected in pipeline phases; sorted= {result}, remaining: {set(in_degree) - set(result)}")

    return result
