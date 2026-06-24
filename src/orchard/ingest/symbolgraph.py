import json
from dataclasses import dataclass, field


@dataclass
class SymbolRecord:
    usr: str
    precise_id: str
    name: str
    kind: str
    module: str
    language: str
    file_path: str | None
    signature: str | None
    access_level: str
    container_usr: str | None = None


@dataclass
class SymbolRelRecord:
    source_usr: str
    target_usr: str
    rel_kind: str


@dataclass
class SymbolGraphResult:
    symbols: list[SymbolRecord] = field(default_factory=list)
    relationships: list[SymbolRelRecord] = field(default_factory=list)


def parse_symbolgraph(path: str, target_id: str) -> SymbolGraphResult:
    with open(path) as f:
        data = json.load(f)
    module_name = data.get("module", {}).get("name", "")
    result = SymbolGraphResult()
    for sym in data.get("symbols", []):
        ident = sym.get("identifier", {})
        loc = sym.get("location", {})
        uri = loc.get("uri", "")
        file_path = uri.removeprefix("file://") if uri.startswith("file://") else None
        frags = sym.get("declarationFragments", [])
        sig = "".join(f.get("spelling", "") for f in frags) if frags else None
        result.symbols.append(SymbolRecord(
            usr=ident.get("precise", ""),
            precise_id=ident.get("precise", ""),
            name=sym.get("names", {}).get("title", ""),
            kind=sym.get("kind", {}).get("identifier", ""),
            module=module_name,
            language=ident.get("interfaceLanguage", "swift"),
            file_path=file_path,
            signature=sig,
            access_level=sym.get("accessLevel", "internal"),
        ))
    for rel in data.get("relationships", []):
        result.relationships.append(SymbolRelRecord(
            source_usr=rel["source"],
            target_usr=rel["target"],
            rel_kind=rel["kind"],
        ))
    return result
