"""Base dataclasses for MCP tool requests and responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar

T = TypeVar("T")

Freshness = Literal["fresh", "stale", "partially_stale", "build_mismatch", "toolchain_mismatch"]


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
