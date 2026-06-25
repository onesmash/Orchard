"""Pipeline phase protocol — inspired by GitNexus's PipelinePhase pattern.

Each phase declares its name, dependencies, and an execute function.
The runner uses Kahn topological sort to determine execution order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class PipelinePhase(Protocol):
    """A single phase in the ingestion pipeline."""
    name: str
    deps: list[str]

    def execute(self, ctx: dict, deps: dict[str, Any]) -> Any:
        """Execute this phase. *ctx* is shared mutable context, *deps* is outputs of dependencies."""
        ...


@dataclass
class PhaseConfig:
    """Concrete phase configuration for registry."""
    name: str
    deps: list[str] = field(default_factory=list)
    execute: Callable[..., Any] | None = None
    enabled_when: Callable[[], bool] | None = None
