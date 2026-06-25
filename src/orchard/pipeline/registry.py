"""Phase registry with enabledWhen predicates.

Inspired by GitNexus's phase registry pattern.  Supports conditional phases
that are only included when their enabledWhen predicate returns True.
"""

from __future__ import annotations

from orchard.pipeline.phase import PhaseConfig


_registry: dict[str, PhaseConfig] = {}


def register(phase: PhaseConfig) -> None:
    """Register a pipeline phase.  Replaces any existing phase with the same name."""
    _registry[phase.name] = phase


def get_enabled_phases() -> list[PhaseConfig]:
    """Return all registered phases whose enabledWhen predicate is True (or absent)."""
    return [
        p for p in _registry.values()
        if p.enabled_when is None or p.enabled_when()
    ]


def clear() -> None:
    """Clear the registry (for tests)."""
    _registry.clear()
