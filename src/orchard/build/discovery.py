import os


def discover_index_store_path(derived_data: str) -> str | None:
    for root, dirs, _ in os.walk(derived_data):
        if os.path.basename(root) == "IndexStore":
            return root
    return None


def discover_symbolgraph_paths(derived_data: str) -> list[str]:
    result = []
    for root, _, files in os.walk(derived_data):
        for f in files:
            if f.endswith(".symbols.json"):
                result.append(os.path.join(root, f))
    return result


def discover_swiftinterface_paths(derived_data: str) -> list[str]:
    """Find .swiftinterface files produced by a Swift compilation."""
    result = []
    for root, _, files in os.walk(derived_data):
        for f in files:
            if f.endswith(".swiftinterface") and ".private" not in f:
                result.append(os.path.join(root, f))
    return result
