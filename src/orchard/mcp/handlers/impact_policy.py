"""Pure data dataclass for impact analysis traversal policy.

Defines which edge types are traversed, depth limits, and whether to
include low-confidence bridges. This separates traversal configuration
from the impact analysis handler logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImpactTraversalPolicy:
    """Configuration for impact analysis graph traversal.

    Attributes:
        relation_types: Edge types to traverse during impact analysis.
        include_low_confidence: Whether to include edges with confidence < 1.0.
        include_bridge_edges: Whether to append "BridgesTo" to the effective
            relation types for cross-module traversal.
        stop_at_target_boundary: Stop traversal when leaving the target symbol's
            owning module/file boundary.
        stop_at_module_boundary: Stop traversal when crossing a module boundary.
        max_depth: Maximum traversal depth from the starting symbol.
    """

    relation_types: list[str] = field(default_factory=lambda: [
        "Calls",
        "References",
        "Implements",
    ])
    include_low_confidence: bool = False
    include_bridge_edges: bool = True
    stop_at_target_boundary: bool = False
    stop_at_module_boundary: bool = False
    max_depth: int = 5

    def effective_relation_types(self) -> list[str]:
        """Return the relation types to use, optionally including BridgesTo."""
        types = list(self.relation_types)
        if self.include_bridge_edges and "BridgesTo" not in types:
            types.append("BridgesTo")
        return types
