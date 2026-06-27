"""Base dataclasses for MCP tool requests and responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar

T = TypeVar("T")

Freshness = Literal["fresh", "stale", "partially_stale", "build_mismatch", "toolchain_mismatch"]


def reason_to_confidence(reason: str | None) -> str:
    """Map Calls edge reason to user-facing confidence label.

    IndexStore tags every CALLS edge with a reason:
      - ``source_direct`` — observed at a source-level call-site (compiler-verified)
      - ``indexstore_relation_only`` — compiler type-inference edge (inferred)
      - ``None`` — edge from symbolgraph (semantically equivalent to source-level)

    Returns:
        ``"compiler-verified"`` for source-level evidence.
        ``"inferred"`` for compiler-inferred edges.
    """
    if reason == "source_direct" or reason is None:
        return "compiler-verified"
    if reason == "indexstore_relation_only":
        return "inferred"
    return "compiler-verified"


@dataclass
class BaseToolRequest:
    """Base request dataclass for MCP tool handlers."""

    repo_root: str | None = None
    build_id: str | None = None
    target: str | None = None
    module: str | None = None
    include_derived: bool = True
    max_depth: int = 5
    depth: int = 1
    relation_types: list[str] = field(default_factory=lambda: ["Calls"])
    include_inferred: bool = False


@dataclass
class BaseToolResponse:
    """Base response dataclass for MCP tool handlers."""

    data: object
    freshness: Freshness
    build_id: str | None = None
    target: str | None = None
    module: str | None = None
    evidence_sources: list[str] = field(default_factory=list)
    confidence: float | None = None
    open_gaps: list[str] = field(default_factory=list)
    noise_removed: int = 0
