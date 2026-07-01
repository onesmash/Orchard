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
_CONTROL_EVENT_RE = re.compile(r"forControlEvents:\s*([A-Za-z0-9_]+)")
_OBJC_METHOD_RE = re.compile(r"^\s*[+-]\s*\([^)]*\)\s*(.+?)\s*(?:\{|$)")


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


def parse_target_action_line(line: str) -> tuple[str | None, str | None]:
    """Extract (@selector, control_event) from an addTarget:action: line."""
    if "addTarget" not in line:
        return (None, None)

    sel_match = _SELECTOR_RE.search(line)
    event_match = _CONTROL_EVENT_RE.search(line)
    selector = sel_match.group(1) if sel_match else None
    control_event = event_match.group(1) if event_match else None
    return selector, control_event


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


def _selector_from_objc_signature(signature: str) -> str | None:
    """Extract an ObjC selector name from a method signature fragment."""
    match = _OBJC_METHOD_RE.match(signature)
    if not match:
        return None

    body = match.group(1).strip()
    labels = re.findall(r"([A-Za-z_]\w*)\s*:", body)
    if labels:
        return "".join(f"{label}:" for label in labels)

    name_match = re.match(r"([A-Za-z_]\w*)", body)
    if name_match:
        return name_match.group(1)
    return None


def _extract_objc_method_blocks(
    file_path: str,
    source_cache: dict[str, list[str]],
    method_cache: dict[str, dict[str, tuple[int, str]]],
) -> dict[str, tuple[int, str]]:
    """Return {selector_or_name: (line_number, block_text)} for one source file."""
    if file_path in method_cache:
        return method_cache[file_path]

    if not file_path or not os.path.isfile(file_path):
        method_cache[file_path] = {}
        return method_cache[file_path]

    lines = source_cache.get(file_path)
    if lines is None:
        with open(file_path, "r") as fh:
            lines = fh.readlines()
        source_cache[file_path] = lines

    methods: dict[str, tuple[int, str]] = {}
    i = 0
    total = len(lines)
    while i < total:
        line = lines[i]
        if not re.match(r"^\s*[+-]\s*\(", line):
            i += 1
            continue

        start = i
        signature_lines = [line]
        while "{" not in "".join(signature_lines) and i + 1 < total:
            if "".join(signature_lines).strip().endswith(";"):
                break
            i += 1
            signature_lines.append(lines[i])

        signature_text = "".join(signature_lines).strip()
        if "{" not in signature_text:
            i += 1
            continue

        signature = " ".join(part.strip() for part in signature_lines)
        selector = _selector_from_objc_signature(signature)
        brace_depth = sum(part.count("{") - part.count("}") for part in signature_lines)
        block_lines = list(signature_lines)
        while brace_depth > 0 and i + 1 < total:
            i += 1
            block_lines.append(lines[i])
            brace_depth += lines[i].count("{") - lines[i].count("}")

        if selector:
            methods[selector] = (start + 1, "".join(block_lines))
        i += 1

    method_cache[file_path] = methods
    return methods


def _find_named_code_block(
    file_path: str,
    symbol_name: str,
    source_cache: dict[str, list[str]],
) -> tuple[int, str] | None:
    """Find a C/C++/ObjC++-style code block by symbol name."""
    if not file_path or not os.path.isfile(file_path) or not symbol_name:
        return None

    lines = source_cache.get(file_path)
    if lines is None:
        with open(file_path, "r") as fh:
            lines = fh.readlines()
        source_cache[file_path] = lines

    pattern = re.compile(rf"\b{re.escape(symbol_name)}\s*\(")
    total = len(lines)
    i = 0
    while i < total:
        line = lines[i]
        if not pattern.search(line):
            i += 1
            continue

        start = i
        header_lines = [line]
        header_text = "".join(header_lines)
        while "{" not in header_text and i + 1 < total:
            if header_text.strip().endswith(";"):
                break
            i += 1
            header_lines.append(lines[i])
            header_text = "".join(header_lines)

        if "{" not in header_text:
            i += 1
            continue

        brace_depth = header_text.count("{") - header_text.count("}")
        block_lines = list(header_lines)
        while brace_depth > 0 and i + 1 < total:
            i += 1
            block_lines.append(lines[i])
            brace_depth += lines[i].count("{") - lines[i].count("}")
        return (start + 1, "".join(block_lines))

    return None


def _find_method_block(
    file_path: str,
    symbol_name: str,
    source_cache: dict[str, list[str]],
    method_cache: dict[str, dict[str, tuple[int, str]]],
) -> tuple[int, str] | None:
    """Return the method block for one symbol name, if the source can be parsed."""
    methods = _extract_objc_method_blocks(file_path, source_cache, method_cache)
    block = methods.get(symbol_name)
    if block is not None:
        return block
    return _find_named_code_block(file_path, symbol_name, source_cache)


# ── Graph builder ─────────────────────────────────────────────────────


def _grep_files(root: str, pattern: str, window: int = 5,
                file_list: list[str] | None = None) -> dict[str, tuple[int, str]]:
    """Run grep -rn and return {realpath: (line, block)} with context lines.

    When *file_list* is provided, only those files are scanned
    (incremental mode).  Otherwise the entire *root* is scanned."""
    import subprocess
    if file_list is not None:
        # Filter to files that exist and are .m/.mm/.swift.
        srcs = [f for f in file_list
                if os.path.isfile(f) and f.endswith(('.m', '.mm', '.swift'))]
        if not srcs:
            return {}
        cmd = ["grep", "-rnE", f"-A{window}", pattern] + srcs
    else:
        cmd = ["grep", "-rnE", f"-A{window}", "--include=*.m", "--include=*.mm",
               "--include=*.swift", pattern, os.path.realpath(root)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
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
    conn, source_root: str = "", changed_files: list[str] | None = None,
) -> dict:
    """Extract notification publisher and observer graph.

    When *changed_files* is provided (incremental ingest), only those
    files are re-scanned; unchanged files keep their existing edges.
    """
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

    caller_files: list[str] = []
    seen_files: set[str] = set()
    for row in rows:
        file_path = row[3] or ""
        if not file_path:
            continue
        abs_path = file_path
        if source_root and not os.path.isabs(abs_path):
            abs_path = os.path.join(source_root, abs_path)
        real_path = os.path.realpath(abs_path)
        if real_path not in seen_files:
            seen_files.add(real_path)
            caller_files.append(real_path)

    grep_files = caller_files
    if changed_files is not None:
        allowed = {os.path.realpath(path) for path in changed_files}
        grep_files = [path for path in caller_files if path in allowed]

    raw = _grep_files(
        source_root,
        r"@selector|postNotificationName|addObserver",
        file_list=grep_files if grep_files else [],
    )
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
    callback_requests: list[tuple[str, str]] = []
    callback_cache: dict[tuple[str, str], dict] = {}
    source_cache: dict[str, list[str]] = {}
    method_cache: dict[str, dict[str, tuple[int, str]]] = {}

    pending_observers: list[dict] = []
    pending_target_actions: list[dict] = []

    for row in rows:
        usr, name, module, file_path, callee_name = (
            row[0], row[1], row[2] or "", row[3] or "", row[4] or ""
        )
        role = classify_objc_message(callee_name)

        abs_path = file_path
        if source_root and not os.path.isabs(abs_path):
            abs_path = os.path.join(source_root, abs_path)

        selector = None
        control_event = None
        noti_name = "unknown"
        line_num = 0

        if role == "notification_poster":
            method_block = _find_method_block(
                os.path.realpath(abs_path), name, source_cache, method_cache
            )
            gi = method_block
            if gi and "postNotificationName" not in gi[1]:
                gi = None
            if gi is None and method_block is not None:
                continue
            if gi is None and method_block is None:
                # Fall back to file-level grep when method parsing misses.
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
            if callee_name.startswith("removeObserver:"):
                continue
            # Try @selector first, then NSSelectorFromString, then addObserver.
            method_block = _find_method_block(
                os.path.realpath(abs_path), name, source_cache, method_cache
            )
            method_sets = []
            if method_block:
                line_num, line_text = method_block
                method_sets.append(("method", (line_num, line_text)))
            fallback_sets = [
                ("selector", sel_files.get(os.path.realpath(abs_path))),
                ("ns_selector", ns_files.get(os.path.realpath(abs_path))),
                ("observer", obs_files.get(os.path.realpath(abs_path))),
            ]
            for kind, gi in method_sets + fallback_sets:
                if gi:
                    line_num, line_text = gi[0], gi[1]
                    parsed_selector, parsed_control_event = parse_target_action_line(line_text)
                    if parsed_selector:
                        selector = parsed_selector
                    if parsed_control_event:
                        control_event = parsed_control_event
                    sel_match = _SELECTOR_RE.search(line_text)
                    if sel_match and not selector:
                        selector = sel_match.group(1)
                    elif kind == "ns_selector":
                        ns_sel = re.search(r'NSSelectorFromString\(\s*@?"(\w+:?)"\)', line_text)
                        if ns_sel:
                            selector = ns_sel.group(1)
                    elif kind in ("method", "observer"):
                        parsed = parse_addobserver_line(line_text)
                        if parsed:
                            selector, noti_name = parsed[0], parsed[1] or noti_name
                    name_match = _NOTIFICATION_NAME_RE.search(line_text)
                    if name_match:
                        noti_name = _clean_name(name_match.group(1))
                    break

            entry = {
                "usr": usr, "name": name, "module": module,
                "file_path": file_path, "line": line_num,
                "selector": selector, "control_event": control_event,
                "notification_name": noti_name,
                "callback": None,
            }
            if role == "target_action":
                pending_target_actions.append(entry)
            else:
                pending_observers.append(entry)
                notifications.setdefault(noti_name, {"posters": [], "observers": []})
                notifications[noti_name]["observers"].append(entry)
            if selector:
                callback_requests.append((file_path, selector))

    if callback_requests:
        file_paths = sorted({fp for fp, _ in callback_requests if fp})
        selectors = sorted({sel for _, sel in callback_requests if sel})
        if file_paths and selectors:
            cb_rows = conn.execute(
                "UNWIND $fps AS fp "
                "UNWIND $sels AS sel "
                "MATCH (s:Symbol) "
                "WHERE s.file_path = fp AND s.name = sel "
                "RETURN s.file_path, s.name, s.usr, s.kind, s.module",
                {"fps": file_paths, "sels": selectors},
            ).get_all()
            for row in cb_rows:
                callback_cache[(row[0], row[1])] = {
                    "usr": row[2],
                    "name": row[1],
                    "kind": row[3],
                    "module": row[4] or "",
                }

    for entry in pending_observers:
        selector = entry.get("selector")
        if selector:
            entry["callback"] = callback_cache.get((entry["file_path"], selector))
        observers.append(entry)

    for entry in pending_target_actions:
        selector = entry.get("selector")
        if selector:
            entry["callback"] = callback_cache.get((entry["file_path"], selector))
        target_actions.append(entry)

    return {
        "publishers": publishers,
        "observers": observers,
        "target_actions": target_actions,
        "notifications": notifications,
    }


# ── Persist to graph ───────────────────────────────────────────────────


def _delete_notification_edges_for_files(conn, changed_files: list[str]) -> None:
    """Delete persisted notification edges tied to the given source files."""
    if not changed_files:
        return
    files = [os.path.realpath(path) for path in changed_files]
    conn.execute(
        "MATCH (p:Symbol)-[r:Posts]->(:Notification) "
        "WHERE p.file_path IN $files "
        "DELETE r",
        {"files": files},
    )
    conn.execute(
        "MATCH (:Notification)-[r:Observes]->(:Symbol) "
        "WHERE r.observer_file_path IN $files "
        "DELETE r",
        {"files": files},
    )


def persist_notification_graph(
    conn, source_root: str = "", build_id: str = "",
    changed_files: list[str] | None = None,
) -> int:
    """Persist Notification nodes, Posts and Observes edges to the graph.

    When *changed_files* is provided (incremental ingest), only those
    files are re-scanned for @selector/notification patterns.
    Idempotent — repeated calls with the same data produce no new edges.

    Returns:
        Total number of Posts + Observes edges written.
    """
    import csv, tempfile, os

    graph = build_notification_graph(conn, source_root=source_root,
                                     changed_files=changed_files)
    if changed_files is not None:
        _delete_notification_edges_for_files(conn, changed_files)

    # Build Posts edges: poster symbol → Notification node.
    posts_rows: list[list[str]] = []
    # Build Observes edges: Notification node → callback symbol.
    observes_rows: list[list[str]] = []

    for noti_name, data in graph["notifications"].items():
        if noti_name == "unknown":
            continue

        if data["posters"]:
            for poster in data["posters"]:
                posts_rows.append([
                    make_symbol_id(poster["usr"]), noti_name,
                    "0.70", "derive/notification", build_id,
                ])
        for obs in data["observers"]:
            callback = obs.get("callback")
            if not callback:
                continue
            observes_rows.append([
                noti_name, make_symbol_id(callback["usr"]),
                obs.get("selector") or "",
                obs.get("usr") or "",
                obs.get("name") or "",
                obs.get("file_path") or "",
                "0.70",
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


def _query_persisted_graph(
    conn, notification_name: str = "", build_id: str = "",
) -> dict:
    """Query persisted Notification nodes and Posts/Observes edges.

    Returns the same shape as ``build_notification_graph`` so the CLI
    can use either source transparently.
    """
    params: dict = {}
    clauses: list[str] = []
    if notification_name:
        clauses.append("n.name CONTAINS $name")
        params["name"] = notification_name
    posts_where = ["($build_id = '' OR ps.build_id = $build_id)"]
    if clauses:
        posts_where.extend(clauses)
    notification_where = "WHERE " + " AND ".join(clauses) if clauses else ""
    observer_only_where = (
        "WHERE NOT EXISTS { MATCH (:Symbol)-[ps:Posts]->(n) "
        "WHERE $build_id = '' OR ps.build_id = $build_id }"
    )
    if clauses:
        observer_only_where = (
            "WHERE "
            + " AND ".join(clauses)
            + " AND NOT EXISTS { MATCH (:Symbol)-[ps:Posts]->(n) "
            "WHERE $build_id = '' OR ps.build_id = $build_id }"
        )
    params["build_id"] = build_id or ""

    notifications: dict[str, dict] = {}
    rows = conn.execute(
        "MATCH (p:Symbol)-[ps:Posts]->(n:Notification) "
        f"WHERE {' AND '.join(posts_where)} "
        "OPTIONAL MATCH (n)-[ob:Observes]->(cb:Symbol) "
        "WHERE $build_id = '' OR ob.build_id = $build_id "
        "RETURN DISTINCT n.name, p.usr, p.name, p.module, p.file_path, "
        "cb.usr, cb.name, cb.module, ob.selector, "
        "ob.observer_usr, ob.observer_name, ob.observer_file_path",
        params,
    ).get_all()

    seen_posters: dict[str, set[tuple[str, str, str, str]]] = {}
    seen_observers: dict[str, set[tuple[str, str, str, str, str, str]]] = {}
    for r in rows:
        noti_name = r[0]
        data = notifications.setdefault(noti_name, {"posters": [], "observers": []})
        poster_key = (r[1] or "", r[2] or "", r[3] or "", r[4] or "")
        if r[1] and poster_key not in seen_posters.setdefault(noti_name, set()):
            seen_posters[noti_name].add(poster_key)
            data["posters"].append({
                "usr": r[1], "name": r[2], "module": r[3] or "",
                "file_path": r[4] or "", "line": 0, "notification_name": noti_name,
            })
        if r[5]:
            observer_key = (
                r[9] or "",
                r[10] or "",
                r[11] or "",
                r[8] or "",
                r[5] or "",
                r[6] or "",
            )
            if observer_key not in seen_observers.setdefault(noti_name, set()):
                seen_observers[noti_name].add(observer_key)
                data["observers"].append({
                    "usr": r[9] or "",
                    "name": r[10] or "",
                    "file_path": r[11] or "",
                    "module": "", "line": 0,
                    "selector": r[8] or "",
                    "notification_name": noti_name,
                    "callback": {"usr": r[5], "name": r[6], "module": r[7] or ""},
                })

    # Also collect notifications that only have observers (no posters).
    obs_rows = conn.execute(
        f"MATCH (n:Notification) {observer_only_where} "
        "OPTIONAL MATCH (n)-[ob:Observes]->(cb:Symbol) "
        "WHERE $build_id = '' OR ob.build_id = $build_id "
        "RETURN n.name, cb.usr, cb.name, cb.module, ob.selector, "
        "ob.observer_usr, ob.observer_name, ob.observer_file_path",
        params,
    ).get_all()
    for r in obs_rows:
        noti_name = r[0]
        data = notifications.setdefault(noti_name, {"posters": [], "observers": []})
        if r[1]:
            observer_key = (
                r[5] or "",
                r[6] or "",
                r[7] or "",
                r[4] or "",
                r[1] or "",
                r[2] or "",
            )
            if observer_key not in seen_observers.setdefault(noti_name, set()):
                seen_observers[noti_name].add(observer_key)
                data["observers"].append({
                    "usr": r[5] or "",
                    "name": r[6] or "",
                    "file_path": r[7] or "",
                    "module": "", "line": 0,
                    "selector": r[4] or "",
                    "notification_name": noti_name,
                    "callback": {"usr": r[1], "name": r[2], "module": r[3] or ""},
                })

    return {
        "publishers": [],
        "observers": [],
        "target_actions": [],
        "notifications": notifications,
    }
