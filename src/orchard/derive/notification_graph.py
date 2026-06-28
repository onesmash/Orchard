"""Notification publisher-subscriber graph extraction.

Detects NSNotificationCenter post/observe patterns by analysing callee
symbol names on Calls edges.  Reads source files at registration sites
to extract ``@selector(xxx)`` and notification name strings — data that
IndexStore does not record.
"""

from __future__ import annotations

import os
import re

from orchard.normalize.identity import make_symbol_id

# ── Line-level parsing ───────────────────────────────────────────────

_SELECTOR_RE = re.compile(r"@selector\(\s*([\w:]+)\s*\)")
_NOTIFICATION_NAME_RE = re.compile(r"name:\s*(\S+)")
_POST_NAME_RE = re.compile(r"postNotificationName:\s*(\S+)")
_FOR_NAME_RE = re.compile(r"addObserverForName:\s*(\S+)")


def parse_addobserver_line(line: str) -> tuple[str | None, str | None] | None:
    """Extract (@selector, notification_name) from an addObserver: line.

    Returns None if this is not an addObserver call.
    """
    if "addObserver" not in line:
        return None

    sel_match = _SELECTOR_RE.search(line)
    name_match = _NOTIFICATION_NAME_RE.search(line)
    if name_match is None:
        # Block-based: addObserverForName:xxx — notification is first arg.
        for_match = _FOR_NAME_RE.search(line)
        if for_match:
            return (None, _clean_name(for_match.group(1)))
        return None  # can't classify without a notification name

    selector = sel_match.group(1) if sel_match else None
    notification_name = _clean_name(name_match.group(1))
    return (selector, notification_name)


def parse_post_notification_line(line: str) -> str | None:
    """Extract notification name from a postNotificationName: line.

    Returns None if this is not a postNotification call.
    """
    if "postNotificationName" not in line:
        return None

    # Try postNotificationName:xxx first
    post_match = _POST_NAME_RE.search(line)
    if post_match:
        return _clean_name(post_match.group(1))

    # Fall back to name: pattern
    name_match = _NOTIFICATION_NAME_RE.search(line)
    if name_match:
        return _clean_name(name_match.group(1))

    return None


def _clean_name(raw: str) -> str:
    """Strip trailing punctuation from a notification name token."""
    return raw.strip().rstrip("];")


# ── Source file reading ──────────────────────────────────────────────


def _find_block_in_file(
    file_path: str, pattern: str, window: int = 5
) -> tuple[int, str] | None:
    """Return (line_number, block_text) for the first match of *pattern*.

    *block_text* includes *window* lines starting from the match line
    to handle multi-line message-send expressions.
    """
    if not file_path or not os.path.isfile(file_path):
        return None
    with open(file_path, "r") as fh:
        lines = fh.readlines()
    for i, line in enumerate(lines):
        if pattern in line:
            block = "".join(lines[i : i + window])
            return (i + 1, block)
    return None


# ── Graph builder ─────────────────────────────────────────────────────


def build_notification_graph(
    conn, source_root: str = ""
) -> dict:
    """Extract notification publisher and observer graph.

    Finds all Calls edges where the callee is an NSNotificationCenter
    method, then reads source files at observer registration sites to
    extract ``@selector(xxx)`` and notification name strings.

    Groups results by notification name, linking each observer to its
    callback symbol in the graph.

    Args:
        conn: Ladybug connection.
        source_root: Optional prefix to resolve relative paths.

    Returns:
        Dict with keys:
          - ``publishers``: flat list of publisher entries
          - ``observers``: flat list of observer entries
          - ``notifications``: dict keyed by notification name, each
            containing ``posters`` and ``observers`` lists.
    """
    from orchard.derive.objc_semantics import classify_objc_message

    # Exact selector matching avoids false positives from KVO
    # (addObserver:forKeyPath:...) and custom addObserver wrappers.
    # Also finds target-action patterns (addTarget:action:forControlEvents:).
    rows = conn.execute(
        "MATCH (caller:Symbol)-[r:Calls]->(callee:Symbol) "
        "WHERE callee.name IN ["
        "  'addObserver:selector:name:object:', "
        "  'addObserverForName:object:queue:usingBlock:', "
        "  'postNotificationName:object:', "
        "  'postNotificationName:object:userInfo:', "
        "  'removeObserver:name:object:', "
        "  'removeObserver:', "
        "  'addTarget:action:forControlEvents:' "
        "] "
        "RETURN DISTINCT caller.usr, caller.name, caller.module, "
        "caller.file_path, callee.name",
    ).get_all()

    publishers: list[dict] = []
    observers: list[dict] = []
    target_actions: list[dict] = []
    notifications: dict[str, dict] = {}

    for row in rows:
        usr, name, module, file_path, callee_name = (
            row[0], row[1], row[2] or "", row[3] or "", row[4] or ""
        )
        role = classify_objc_message(callee_name)

        # Resolve absolute path.
        abs_path = file_path
        if source_root and not os.path.isabs(abs_path):
            abs_path = os.path.join(source_root, abs_path)

        if role == "notification_poster":
            # Read source to extract notification name.
            line_info = _find_block_in_file(abs_path, "postNotificationName")
            noti_name = "unknown"
            line_num = 0
            if line_info:
                line_num, line_text = line_info
                noti_name = parse_post_notification_line(line_text) or "unknown"

            entry = {
                "usr": usr,
                "name": name,
                "module": module,
                "file_path": file_path,
                "line": line_num,
                "notification_name": noti_name,
            }
            publishers.append(entry)
            notifications.setdefault(noti_name, {"posters": [], "observers": []})
            notifications[noti_name]["posters"].append(entry)

        elif role == "notification_observer":
            # Search directly for @selector — handles direct calls, macros,
            # and wrapper methods uniformly.  Falls back to NSSelectorFromString
            # for dynamic selectors, then addObserver for block-based patterns.
            line_info = (_find_block_in_file(abs_path, "@selector")
                         or _find_block_in_file(abs_path, "NSSelectorFromString")
                         or _find_block_in_file(abs_path, "addObserver"))
            selector = None
            noti_name = "unknown"
            line_num = 0
            callback: dict | None = None

            if line_info:
                line_num, line_text = line_info
                sel_match = _SELECTOR_RE.search(line_text)
                if sel_match:
                    selector = sel_match.group(1)
                else:
                    ns_sel = re.search(r'NSSelectorFromString\(\s*@?"(\w+:?)"\)', line_text)
                    if ns_sel:
                        selector = ns_sel.group(1)
                    else:
                        parsed = parse_addobserver_line(line_text)
                        if parsed:
                            selector, noti_name = parsed[0], parsed[1] or noti_name
                name_match = _NOTIFICATION_NAME_RE.search(line_text)
                if name_match:
                    noti_name = _clean_name(name_match.group(1))

            # Link @selector to the callback symbol.
            if selector:
                cb_rows = conn.execute(
                    "MATCH (s:Symbol) WHERE s.name = $sel "
                    "AND s.file_path = $fp "
                    "RETURN s.usr, s.name, s.kind, s.module LIMIT 1",
                    {"sel": selector, "fp": file_path},
                ).get_all()
                if cb_rows:
                    callback = {
                        "usr": cb_rows[0][0],
                        "name": cb_rows[0][1],
                        "kind": cb_rows[0][2],
                        "module": cb_rows[0][3] or "",
                    }

            entry = {
                "usr": usr,
                "name": name,
                "module": module,
                "file_path": file_path,
                "line": line_num,
                "selector": selector,
                "notification_name": noti_name,
                "callback": callback,
            }
            observers.append(entry)
            notifications.setdefault(noti_name, {"posters": [], "observers": []})
            notifications[noti_name]["observers"].append(entry)

        elif role == "target_action":
            # Read source to extract @selector from addTarget:action:...
            line_info = _find_block_in_file(abs_path, "addTarget:")
            selector = None
            line_num = 0
            callback = None

            if line_info:
                line_num, line_text = line_info
                sel_match = _SELECTOR_RE.search(line_text)
                if sel_match:
                    selector = sel_match.group(1)

            # Link @selector to the callback symbol.
            if selector:
                cb_rows = conn.execute(
                    "MATCH (s:Symbol) WHERE s.name = $sel "
                    "AND s.file_path = $fp "
                    "RETURN s.usr, s.name, s.kind, s.module LIMIT 1",
                    {"sel": selector, "fp": file_path},
                ).get_all()
                if cb_rows:
                    callback = {
                        "usr": cb_rows[0][0],
                        "name": cb_rows[0][1],
                        "kind": cb_rows[0][2],
                        "module": cb_rows[0][3] or "",
                    }

            target_actions.append({
                "usr": usr,
                "name": name,
                "module": module,
                "file_path": file_path,
                "line": line_num,
                "selector": selector,
                "callback": callback,
            })

    return {
        "publishers": publishers,
        "observers": observers,
        "target_actions": target_actions,
        "notifications": notifications,
    }
