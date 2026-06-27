"""Notification publisher-subscriber graph extraction.

Detects NSNotificationCenter post/observe patterns by analysing callee
symbol names on Calls edges.  ObjC selector strings are stored as the
callee symbol's name (e.g. ``addObserver:selector:name:object:``).
"""

from __future__ import annotations

from orchard.derive.objc_semantics import classify_objc_message


def build_notification_graph(conn) -> dict:
    """Extract notification publisher and observer sets.

    Finds all Calls edges where the callee's name matches a known
    NSNotificationCenter selector pattern (addObserver:... /
    postNotificationName:...), then classifies each caller as a
    publisher or observer.

    Returns:
        Dict with ``publishers`` and ``observers`` lists, each entry
        containing ``usr``, ``name``, ``selector`` (the callee name),
        ``selector_role``, and ``module``.
    """
    rows = conn.execute(
        "MATCH (caller:Symbol)-[r:Calls]->(callee:Symbol) "
        "WHERE callee.name = 'addObserver:selector:name:object:' "
        "   OR callee.name STARTS WITH 'addObserver:' "
        "   OR callee.name = 'postNotificationName:object:' "
        "   OR callee.name STARTS WITH 'postNotificationName:' "
        "RETURN DISTINCT caller.usr, caller.name, caller.module, callee.name",
    ).get_all()

    publishers: list[dict] = []
    observers: list[dict] = []

    for row in rows:
        usr, name, module, selector = row[0], row[1], row[2], row[3] or ""
        role = classify_objc_message(selector)
        entry = {
            "usr": usr,
            "name": name,
            "module": module or "",
            "selector": selector,
            "selector_role": role,
        }
        if role == "notification_poster":
            publishers.append(entry)
        elif role == "notification_observer":
            observers.append(entry)

    return {"publishers": publishers, "observers": observers}
