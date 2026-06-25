"""Parse .swiftinterface files for protocol conformance declarations.

.swiftinterface files are compiler-generated text files containing the
full API surface of a compiled Swift module, including protocol conformance
annotations (``struct Foo : ProtocolA, ProtocolB``).

This parser extracts (type_name, protocol_name) pairs and returns
SymbolRecord-like containers suitable for writing ConformsTo edges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class InterfaceConformance:
    """A single protocol conformance extracted from a .swiftinterface file."""

    type_name: str     # e.g. "BusinessEntranceTag"
    type_kind: str     # e.g. "enum"
    protocol_name: str  # e.g. "Sendable"
    module: str         # the module this declaration belongs to


# Type names that appear after ':' but are NOT protocols (raw types, super classes).
_NOT_PROTOCOLS: set[str] = {
    "Int", "UInt", "String", "Double", "Float", "Bool", "Void",
    "Int8", "Int16", "Int32", "Int64",
    "UInt8", "UInt16", "UInt32", "UInt64",
    "NSObject", "UIViewController", "UIView", "UIResponder",
    "NSDate", "NSArray", "NSDictionary", "NSSet", "NSData",
    "NSMutableArray", "NSMutableDictionary", "NSMutableSet",
    "NSMutableData", "NSMutableString",
}


def _infer_kind(decl_kind: str) -> str:
    k = decl_kind.lower()
    if k == "enum":
        return "enum"
    if k == "struct":
        return "struct"
    if k == "class":
        return "class"
    if k == "extension":
        return "extension"
    if k == "protocol":
        return "protocol"
    return "struct"


_PARSE_RE = re.compile(
    r"(?:public |private |internal |open |@\w+(?:\([^)]*\))? )*"
    r"(?P<kind>struct|class|enum|extension|protocol)\s+"
    r"(?P<name>\w+)\s*"
    r"(?::\s*(?P<inherits>[^{]+?))?"
    r"\s*\{"
)


def parse_interface_text(text: str, module_name: str = "") -> list[InterfaceConformance]:
    """Extract protocol conformances from a .swiftinterface file's text.

    Parameters
    ----------
    text
        Full text content of the .swiftinterface file.
    module_name
        The module (framework / library) name.

    Returns
    -------
    list[InterfaceConformance]
        One entry per (type, protocol) pair found.
    """
    results: list[InterfaceConformance] = []
    for m in _PARSE_RE.finditer(text):
        kind = m.group("kind")
        name = m.group("name")
        inherits_str = m.group("inherits")
        if not inherits_str:
            continue
        parts = [p.strip() for p in inherits_str.split(",")]
        for part in parts:
            # Strip generic args: `Codable` or `Identifiable where ID == Int`
            proto = part.split("<")[0].split(" where")[0].strip()
            # Remove module prefix: `Swift.Sendable` → `Sendable`
            base_name = proto.split(".")[-1]
            if not proto or base_name in _NOT_PROTOCOLS:
                continue
            # Use the unqualified name for matching against Symbol nodes.
            proto = base_name
            results.append(InterfaceConformance(
                type_name=name,
                type_kind=_infer_kind(kind),
                protocol_name=proto,
                module=module_name,
            ))
    return results


def parse_interface_file(path: str, module_name: str = "") -> list[InterfaceConformance]:
    """Read and parse a .swiftinterface file from disk.

    Parameters
    ----------
    path
        Filesystem path to the .swiftinterface file.
    module_name
        Module name override. If empty, derived from the filename.

    Returns
    -------
    list[InterfaceConformance]
    """
    if not module_name:
        import os
        base = os.path.basename(path)
        module_name = base.split(".")[0]
    with open(path, encoding="utf-8") as fh:
        return parse_interface_text(fh.read(), module_name)
