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


def _grep_files(root: str, pattern: str, window: int = 5) -> dict[str, tuple[int, str]]:
    """Run grep -rn and return {realpath: (line, block)} with context lines."""
    import subprocess
    try:
        out = subprocess.run(
            ["grep", "-rnE", f"-A{window}", "--include=*.m", "--include=*.mm",
             "--include=*.swift", pattern, os.path.realpath(root)],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}

    results: dict[str, tuple[int, str]] = {}
    current_file = None
    current_block: list[str] = []
    current_line = 0
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        m = re.match(r'^(.+?):(\d+):(.*)$', line)
        if m:
            # Save previous block (merge into existing for same file).
            if current_file and current_block:
                rp = os.path.realpath(current_file)
                if rp in results:
                    # Merge: extend existing block with new match's context.
                    _prev_ln, _prev_block = results[rp]
                    results[rp] = (_prev_ln, _prev_block + "\n" + "\n".join(current_block))
                else:
                    results[rp] = (current_line, "\n".join(current_block))
            current_file = m.group(1)
            current_line = int(m.group(2))
            current_block = [m.group(3)]
        else:
            ctx = re.match(r'^.+?-(\d+)-(.*)$', line)
            if ctx:
                current_block.append(ctx.group(2))
    if current_file and current_block:
        rp = os.path.realpath(current_file)
        if rp in results:
            _prev_ln, _prev_block = results[rp]
            results[rp] = (_prev_ln, _prev_block + "\n" + "\n".join(current_block))
        else:
            results[rp] = (current_line, "\n".join(current_block))
    return results


def build_notification_graph(
    conn, source_root: str = ""
) -> dict:
    """Extract notification publisher and observer graph."""
    from orchard.derive.objc_semantics import classify_objc_message

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

    # Pre-scan: one grep pass for all patterns (avoid 4× filesystem scan).
    raw = _grep_files(source_root, r"@selector|postNotificationName|addObserver")
    # Split results by pattern match.
    sel_files: dict[str, tuple[int, str]] = {}
    ns_files: dict[str, tuple[int, str]] = {}
    post_files: dict[str, tuple[int, str]] = {}
    obs_files: dict[str, tuple[int, str]] = {}
    for fp, (ln, block) in raw.items():
        if "@selector" in block:
            sel_files[fp] = (ln, block)
        if "NSSelectorFromString" in block:
            ns_files[fp] = (ln, block)
        if "postNotificationName" in block:
            post_files[fp] = (ln, block)
        if "addObserver" in block:
            obs_files[fp] = (ln, block)

    # Process each row using cached results.
    publishers: list[dict] = []
    observers: list[dict] = []
    target_actions: list[dict] = []
    notifications: dict[str, dict] = {}

    for row in rows:
        usr, name, module, file_path, callee_name = (
            row[0], row[1], row[2] or "", row[3] or "", row[4] or ""
        )
        role = classify_objc_message(callee_name)

        abs_path = file_path
        if source_root and not os.path.isabs(abs_path):
            abs_path = os.path.join(source_root, abs_path)

        selector = None
        noti_name = "unknown"
        line_num = 0

        if role == "notification_poster":
            # Look up from grep results.
            gi = post_files.get(os.path.realpath(abs_path))
            if gi:
                line_num = gi[0]
                noti_name = parse_post_notification_line(gi[1]) or "unknown"

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

        elif role in ("notification_observer", "target_action"):
            # Try @selector first, then NSSelectorFromString, then addObserver.
            for gset in [sel_files, ns_files, obs_files]:
                gi = gset.get(os.path.realpath(abs_path))
                if gi:
                    line_num, line_text = gi[0], gi[1]
                    sel_match = _SELECTOR_RE.search(line_text)
                    if sel_match:
                        selector = sel_match.group(1)
                    elif gset is ns_files:
                        ns_sel = re.search(r'NSSelectorFromString\(\s*@?"(\w+:?)"\)', line_text)
                        if ns_sel:
                            selector = ns_sel.group(1)
                    elif gset is obs_files:
                        parsed = parse_addobserver_line(line_text)
                        if parsed:
                            selector, noti_name = parsed[0], parsed[1] or noti_name
                    name_match = _NOTIFICATION_NAME_RE.search(line_text)
                    if name_match:
                        noti_name = _clean_name(name_match.group(1))
                    break

            # Link @selector to the callback symbol.
            callback = None
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
                "usr": usr, "name": name, "module": module,
                "file_path": file_path, "line": line_num,
                "selector": selector, "notification_name": noti_name,
                "callback": callback,
            }
            if role == "target_action":
                target_actions.append(entry)
            else:
                observers.append(entry)
                notifications.setdefault(noti_name, {"posters": [], "observers": []})
                notifications[noti_name]["observers"].append(entry)

    return {
        "publishers": publishers,
        "observers": observers,
        "target_actions": target_actions,
        "notifications": notifications,
    }


# ── Persist to graph ───────────────────────────────────────────────────


def persist_notification_graph(
    conn, source_root: str = "", build_id: str = ""
) -> int:
    """Persist Notification nodes, Posts and Observes edges to the graph.

    Builds the notification graph from Calls edges + source grep, then
    writes Notification nodes and edges via COPY FROM for bulk speed.
    Idempotent — repeated calls with the same data produce no new edges.

    Returns:
        Total number of Posts + Observes edges written.
    """
    import csv, tempfile, os

    graph = build_notification_graph(conn, source_root=source_root)

    # Build Posts edges: poster symbol → Notification node.
    posts_rows: list[list[str]] = []
    # Build Observes edges: Notification node → callback symbol.
    observes_rows: list[list[str]] = []

    for noti_name, data in graph["notifications"].items():
        if noti_name == "unknown":
            continue
        if not data["posters"] or not data["observers"]:
            continue

        for obs in data["observers"]:
            callback = obs.get("callback")
            if not callback:
                continue
            cb_id = make_symbol_id(callback["usr"])
            for poster in data["posters"]:
                posts_rows.append([
                    make_symbol_id(poster["usr"]), noti_name,
                    "0.70", "derive/notification", build_id,
                ])
                observes_rows.append([
                    noti_name, cb_id,
                    obs.get("selector") or "", "0.70",
                    "derive/notification", build_id,
                ])

    # Write Notification nodes via COPY FROM (skip existing).
    all_noti_names = {r[1] for r in posts_rows} | {r[0] for r in observes_rows}
    if all_noti_names:
        existing = conn.execute(
            "MATCH (n:Notification) RETURN n.name"
        ).get_all()
        existing_names = {r[0] for r in existing}
        new_names = sorted(all_noti_names - existing_names)
        if new_names:
            nf = tempfile.NamedTemporaryFile(
                mode="w", suffix=".csv", delete=False, newline="")
            try:
                w = csv.writer(nf)
                for name in new_names:
                    w.writerow([name])
                nf.close()
                conn.execute(
                    f"COPY Notification FROM '{nf.name}' "
                    "(HEADER false, DELIM ',')"
                )
            finally:
                os.unlink(nf.name)

    # Write Posts edges.
    count = 0
    if posts_rows:
        pf = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, newline="")
        try:
            w = csv.writer(pf, quoting=csv.QUOTE_ALL)
            for r in posts_rows:
                w.writerow(r)
            pf.close()
            conn.execute(
                f"COPY Posts FROM '{pf.name}' (HEADER false, DELIM ',')"
            )
            count += len(posts_rows)
        finally:
            os.unlink(pf.name)

    # Write Observes edges.
    if observes_rows:
        of = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, newline="")
        try:
            w = csv.writer(of, quoting=csv.QUOTE_ALL)
            for r in observes_rows:
                w.writerow(r)
            of.close()
            conn.execute(
                f"COPY Observes FROM '{of.name}' (HEADER false, DELIM ',')"
            )
            count += len(observes_rows)
        finally:
            os.unlink(of.name)

    return count


def _query_persisted_graph(conn, notification_name: str = "") -> dict:
    """Query persisted Notification nodes and Posts/Observes edges.

    Returns the same shape as ``build_notification_graph`` so the CLI
    can use either source transparently.
    """
    noti_filter = ""
    params: dict = {}
    if notification_name:
        noti_filter = "WHERE n.name CONTAINS $name"
        params["name"] = notification_name

    notifications: dict[str, dict] = {}
    rows = conn.execute(
        f"MATCH (p:Symbol)-[ps:Posts]->(n:Notification) {noti_filter} "
        "OPTIONAL MATCH (n)-[ob:Observes]->(cb:Symbol) "
        "RETURN n.name, p.usr, p.name, p.module, p.file_path, "
        "cb.usr, cb.name, cb.module, ob.selector",
        params,
    ).get_all()

    for r in rows:
        noti_name = r[0]
        posters = notifications.setdefault(noti_name, {"posters": [], "observers": []})
        # Poster
        posters["posters"].append({
            "usr": r[1], "name": r[2], "module": r[3] or "",
            "file_path": r[4] or "", "line": 0, "notification_name": noti_name,
        })
        # Observer callback
        if r[5]:
            posters["observers"].append({
                "usr": "", "name": "", "module": "", "file_path": "",
                "line": 0, "selector": r[8] or "",
                "notification_name": noti_name,
                "callback": {"usr": r[5], "name": r[6], "module": r[7] or ""},
            })

    # Also collect notifications that only have observers (no posters).
    obs_rows = conn.execute(
        f"MATCH (n:Notification) {noti_filter} "
        "WHERE NOT EXISTS { MATCH (:Symbol)-[:Posts]->(n) } "
        "OPTIONAL MATCH (n)-[ob:Observes]->(cb:Symbol) "
        "RETURN n.name, cb.usr, cb.name, cb.module, ob.selector",
        params,
    ).get_all()
    for r in obs_rows:
        noti_name = r[0]
        data = notifications.setdefault(noti_name, {"posters": [], "observers": []})
        if r[1]:
            data["observers"].append({
                "usr": "", "name": "", "module": "", "file_path": "",
                "line": 0, "selector": r[4] or "",
                "notification_name": noti_name,
                "callback": {"usr": r[1], "name": r[2], "module": r[3] or ""},
            })

    return {
        "publishers": [],
        "observers": [],
        "target_actions": [],
        "notifications": notifications,
    }
