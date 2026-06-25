"""Symbol chunker for embedding projection.

Produces ChunkRecord instances from Symbol nodes within a target.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChunkRecord:
    chunk_id: str
    owner_usr: str
    chunk_kind: str
    content: str


def chunk_symbols(conn, target_id: str) -> list[ChunkRecord]:
    """Produce one ChunkRecord per Symbol in *target_id*.

    Content is formatted as ``"{kind} {name}: {signature}"``.
    Chunk kind is ``"type"`` for struct/class/enum/protocol, ``"method"``
    for everything else.
    """
    rows = conn.execute(
        "MATCH (s:Symbol) WHERE s.target_id = $tid "
        "RETURN s.usr, s.name, s.kind, s.signature",
        {"tid": target_id},
    ).get_all()

    chunks: list[ChunkRecord] = []
    for i, row in enumerate(rows):
        usr, name, kind, sig = row[0], row[1], row[2], row[3] or ""
        content = f"{kind} {name}: {sig}".strip() if sig else f"{kind} {name}"
        chunk_kind = (
            "type" if kind in ("struct", "class", "enum", "protocol") else "method"
        )
        chunks.append(
            ChunkRecord(
                chunk_id=f"{target_id}:{usr}:chunk:{chunk_kind}:{i}",
                owner_usr=usr,
                chunk_kind=chunk_kind,
                content=content,
            )
        )
    return chunks
