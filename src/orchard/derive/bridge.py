"""cross_language_bridge_recovery phase.

Discovers ObjC ↔ Swift bridge candidates by matching symbols across
languages within the same target and writes BridgesTo edges.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re

from orchard.normalize.identity import make_symbol_id


@dataclass
class CrossLanguageName:
    """Dual-language symbol name for ObjC/Swift interop.

    Inspired by sourcekit-lsp's CrossLanguageName.
    """
    clang_name: str | None = None   # -[Class method:] / +[Class method:]
    swift_name: str | None = None   # Class.method(_:)
    definition_language: str = ""   # "swift" | "objc" | "c"

    @property
    def definition_name(self) -> str | None:
        """Return the name in the symbol's definition language."""
        if self.definition_language == "swift":
            return self.swift_name
        if self.definition_language in ("objc", "c", "cpp"):
            return self.clang_name
        return self.swift_name or self.clang_name


@dataclass
class _BridgeProfile:
    usr: str
    language: str
    kind: str
    file_path: str
    name: str
    swift_display_name: str
    signature: str
    clang_name: str | None
    swift_name: str | None
    semantic_key: tuple[str, str, int, str] | None
    projected_swift_name: str | None


_OBJC_USR_RE = re.compile(
    r"^c:objc\(cs\)(?P<container>.+?)\((?P<scope>im|cm)\)(?P<selector>.+)$"
)
_SWIFT_USR_MEMBER_RE = re.compile(
    r"^s:(?P<container>[A-Za-z_][A-Za-z0-9_]*)[CVEO]\d+(?P<member>[A-Za-z_][A-Za-z0-9_]*)"
)


def _underscore_arity(arity: int) -> str:
    if arity <= 0:
        return "()"
    return "(" + ("_:" * arity) + ")"


def _selector_base(selector: str) -> str:
    piece = selector.split(":", 1)[0]
    return re.sub(r"With[A-Z].*$", "", piece)


def _signature_arity(signature: str) -> int:
    if not signature:
        return 0
    match = re.search(r"\((.*)\)", signature)
    if not match:
        return 0
    params = match.group(1).strip()
    if not params:
        return 0
    return params.count(",") + 1


def _parse_objc_cross_language_name(usr: str) -> CrossLanguageName:
    match = _OBJC_USR_RE.match(usr)
    if not match:
        return CrossLanguageName(definition_language="objc")
    container = match.group("container")
    selector = match.group("selector")
    prefix = "-" if match.group("scope") == "im" else "+"
    arity = selector.count(":")
    swift_base = _selector_base(selector)
    return CrossLanguageName(
        clang_name=f"{prefix}[{container} {selector}]",
        swift_name=f"{container}.{swift_base}{_underscore_arity(arity)}",
        definition_language="objc",
    )


def _swift_container_from_name(display_name: str) -> str | None:
    if "." not in display_name:
        return None
    return display_name.split(".", 1)[0] or None


def _swift_member_from_display_name(display_name: str) -> str | None:
    if not display_name:
        return None
    name_part = display_name.split(".", 1)[-1]
    return name_part.split("(", 1)[0] or None


def _parse_swift_cross_language_name(
    *,
    usr: str,
    name: str,
    swift_display_name: str,
    signature: str,
    kind: str,
) -> CrossLanguageName:
    display = swift_display_name or ""
    container = _swift_container_from_name(display)
    member = _swift_member_from_display_name(display) or name
    if not container:
        match = _SWIFT_USR_MEMBER_RE.match(usr)
        if match:
            container = match.group("container")
            member = match.group("member") or member
    arity = 0
    if display and "(" in display and ")" in display:
        params = display.rsplit("(", 1)[1].split(")", 1)[0]
        arity = 0 if not params else params.count("_:")
    elif kind in {"method", "function"}:
        arity = _signature_arity(signature)
    if display and "(" in display and "." in display:
        swift_name = display
    elif kind in {"method", "function"}:
        owner = f"{container}." if container else ""
        swift_name = f"{owner}{member}{_underscore_arity(arity)}"
    elif container:
        swift_name = f"{container}.{member}"
    else:
        swift_name = member
    return CrossLanguageName(
        swift_name=swift_name,
        definition_language="swift",
    )


def _kind_group(kind: str) -> str:
    return "callable" if kind in {"method", "function"} else kind


def _semantic_key(
    *,
    container: str | None,
    member: str | None,
    arity: int,
    kind: str,
) -> tuple[str, str, int, str] | None:
    if not member:
        return None
    return (
        (container or "").lower(),
        member.lower(),
        max(arity, 0),
        _kind_group(kind),
    )


def _build_bridge_profile(row: tuple) -> _BridgeProfile:
    usr, language, kind, name, file_path, signature, swift_display_name = row
    file_path = file_path or ""
    signature = signature or ""
    swift_display_name = swift_display_name or ""
    if language == "objc":
        cross_name = _parse_objc_cross_language_name(usr)
        container = None
        member = name
        arity = 0
        if cross_name.swift_name and "." in cross_name.swift_name:
            container = cross_name.swift_name.split(".", 1)[0]
            member = cross_name.swift_name.split(".", 1)[1].split("(", 1)[0]
            params = cross_name.swift_name.rsplit("(", 1)[1].split(")", 1)[0]
            arity = 0 if not params else params.count("_:")
        return _BridgeProfile(
            usr=usr,
            language=language,
            kind=kind,
            file_path=file_path,
            name=name,
            swift_display_name=swift_display_name,
            signature=signature,
            clang_name=cross_name.clang_name,
            swift_name=cross_name.swift_name,
            semantic_key=_semantic_key(container=container, member=member, arity=arity, kind=kind),
            projected_swift_name=cross_name.swift_name,
        )
    cross_name = _parse_swift_cross_language_name(
        usr=usr,
        name=name,
        swift_display_name=swift_display_name,
        signature=signature,
        kind=kind,
    )
    container = _swift_container_from_name(cross_name.swift_name or "")
    member = _swift_member_from_display_name(cross_name.swift_name or "") or name
    arity = _signature_arity(signature) if kind in {"method", "function"} else 0
    if cross_name.swift_name and "(" in cross_name.swift_name:
        params = cross_name.swift_name.rsplit("(", 1)[1].split(")", 1)[0]
        arity = 0 if not params else params.count("_:")
    return _BridgeProfile(
        usr=usr,
        language=language,
        kind=kind,
        file_path=file_path,
        name=name,
        swift_display_name=swift_display_name,
        signature=signature,
        clang_name=None,
        swift_name=cross_name.swift_name,
        semantic_key=_semantic_key(container=container, member=member, arity=arity, kind=kind),
        projected_swift_name=None,
    )


def _record_pair(
    pairs: dict[tuple[str, str], tuple[str, float]],
    usr_a: str,
    usr_b: str,
    *,
    kind: str,
    confidence: float,
) -> None:
    if usr_a == usr_b:
        return
    pair_key = tuple(sorted([usr_a, usr_b]))
    existing = pairs.get(pair_key)
    if existing is None or confidence > existing[1]:
        pairs[pair_key] = (kind, confidence)


def run_bridge_recovery(conn, target_id: str, build_id: str) -> dict[str, int]:
    """Find cross-language bridge candidates and write BridgesTo edges.

    Strategies (in priority order):
      1. Name match: same base name + different language → confidence 0.70.
      2. USR correlation (deferred to M4).

    Uses MERGE for idempotency — repeated runs with the same data produce
    no new edges. Counts reflect only *new* edges written in this call.

    Parameters
    ----------
    conn
        Open Ladybug connection.
    target_id
        The build target identifier.
    build_id
        The build snapshot identifier.

    Returns
    -------
    dict
        Counters: ``bridges_by_name``, ``total``.
    """
    # Count existing edges to report only *new* ones (delta-based idempotency).
    before = conn.execute(
        "MATCH ()-[r:BridgesTo {provenance: 'derive/bridge', build_id: $bid}]->() "
        "RETURN count(r)",
        {"bid": build_id},
    ).get_all()
    before_count = int(before[0][0]) if before else 0

    rows = conn.execute(
        "MATCH (s:Symbol {target_id: $tid}) "
        "WHERE s.language IN ['swift','objc'] "
        "RETURN s.usr, s.language, s.kind, s.name, s.file_path, "
        "s.signature, s.swift_display_name",
        {"tid": target_id},
    ).get_all()
    profiles = [_build_bridge_profile(row) for row in rows]
    swift_profiles = [p for p in profiles if p.language == "swift"]
    objc_profiles = [p for p in profiles if p.language == "objc"]

    pairs: dict[tuple[str, str], tuple[str, float]] = {}

    swift_by_name: dict[str, list[_BridgeProfile]] = {}
    objc_by_projection: dict[str, list[_BridgeProfile]] = {}
    swift_by_key: dict[tuple[str, str, int, str], list[_BridgeProfile]] = {}
    objc_by_key: dict[tuple[str, str, int, str], list[_BridgeProfile]] = {}
    swift_by_name_kind: dict[tuple[str, str], list[_BridgeProfile]] = {}
    objc_by_name_kind: dict[tuple[str, str], list[_BridgeProfile]] = {}

    for profile in swift_profiles:
        if profile.swift_name:
            swift_by_name.setdefault(profile.swift_name, []).append(profile)
        if profile.semantic_key:
            swift_by_key.setdefault(profile.semantic_key, []).append(profile)
        swift_by_name_kind.setdefault((profile.name, profile.kind), []).append(profile)
    for profile in objc_profiles:
        if profile.projected_swift_name:
            objc_by_projection.setdefault(profile.projected_swift_name, []).append(profile)
        if profile.semantic_key:
            objc_by_key.setdefault(profile.semantic_key, []).append(profile)
        objc_by_name_kind.setdefault((profile.name, profile.kind), []).append(profile)

    for swift_name, swifts in swift_by_name.items():
        for objc in objc_by_projection.get(swift_name, []):
            for swift in swifts:
                _record_pair(
                    pairs, swift.usr, objc.usr, kind="usr_correlate", confidence=0.95
                )

    for key, swifts in swift_by_key.items():
        for objc in objc_by_key.get(key, []):
            for swift in swifts:
                same_dir = bool(
                    swift.file_path
                    and objc.file_path
                    and PurePosixPath(swift.file_path).parent
                    == PurePosixPath(objc.file_path).parent
                )
                confidence = 0.85 if same_dir else 0.82
                _record_pair(
                    pairs, swift.usr, objc.usr, kind="usr_correlate", confidence=confidence
                )

    for key, swifts in swift_by_name_kind.items():
        for objc in objc_by_name_kind.get(key, []):
            same_dir = bool(
                swifts[0].file_path
                and objc.file_path
                and PurePosixPath(swifts[0].file_path).parent
                == PurePosixPath(objc.file_path).parent
            )
            for swift in swifts:
                if same_dir:
                    _record_pair(
                        pairs, swift.usr, objc.usr, kind="usr_correlate", confidence=0.85
                    )
                _record_pair(
                    pairs, swift.usr, objc.usr, kind="name_match", confidence=0.70
                )

    # Write bidirectional BridgesTo edges via MERGE.
    counts: dict[str, int] = {"bridges_by_name": 0, "bridges_by_usr": 0, "total": 0}
    profile_by_usr = {profile.usr: profile for profile in profiles}
    for (usr_a, usr_b), (kind, conf) in pairs.items():
        if kind == "usr_correlate":
            counts["bridges_by_usr"] += 2  # bidirectional
        else:
            counts["bridges_by_name"] += 2
        counts["total"] += 2
        for src_usr, tgt_usr in [(usr_a, usr_b), (usr_b, usr_a)]:
            src_meta = profile_by_usr.get(src_usr)
            tgt_meta = profile_by_usr.get(tgt_usr)
            src_lang = src_meta.language if src_meta else ""
            tgt_lang = tgt_meta.language if tgt_meta else ""
            clang_name = (
                src_meta.clang_name if src_meta and src_lang == "objc"
                else tgt_meta.clang_name if tgt_meta and tgt_lang == "objc"
                else None
            )
            swift_name = (
                src_meta.swift_name if src_meta and src_lang == "swift"
                else tgt_meta.swift_name if tgt_meta and tgt_lang == "swift"
                else None
            )
            def_lang = src_lang or tgt_lang
            conn.execute(
                "MATCH (a:Symbol {id: $src}), (b:Symbol {id: $dst}) "
                "MERGE (a)-[r:BridgesTo {provenance: $prov, build_id: $bid}]->(b) "
                "SET r.bridge_kind = $kind, r.confidence = $conf, r.reason = $reason, "
                "r.clang_name = $clang, r.swift_name = $swift, "
                "r.definition_language = $deflang",
                {
                    "src": make_symbol_id(target_id, src_usr),
                    "dst": make_symbol_id(target_id, tgt_usr),
                    "kind": kind,
                    "prov": "derive/bridge",
                    "conf": conf,
                    "bid": build_id,
                    "reason": "derive/bridge",
                    "clang": clang_name,
                    "swift": swift_name,
                    "deflang": def_lang,
                },
            )

    # Report delta (new edges only) for idempotency.
    after = conn.execute(
        "MATCH ()-[r:BridgesTo {provenance: 'derive/bridge', build_id: $bid}]->() "
        "RETURN count(r)",
        {"bid": build_id},
    ).get_all()
    after_count = int(after[0][0]) if after else 0
    new_total = max(0, after_count - before_count)
    return {
        "bridges_by_name": counts["bridges_by_name"],
        "bridges_by_usr": counts["bridges_by_usr"],
        "total": new_total,
    }
