"""ObjC selector / message-send semantic classification.

Maps ObjC selectors to human-readable semantic roles (notification observer,
delegate setter, target-action, etc.) to provide higher-level interpretation
of low-level ``objc_msgSend`` call edges.
"""

import re

# ── Semantic role patterns ───────────────────────────────────────────

_ROLE_PATTERNS: list[tuple[str, str]] = [
    # (compiled regex pattern, semantic role)
    (r"^addObserver:", "notification_observer"),
    (r"^postNotificationName:", "notification_poster"),
    (r"^removeObserver:", "notification_observer"),
    (r"^addTarget:action:", "target_action"),
    (r"^sendAction:to:", "action_sender"),
    (r"^set\w*Delegate:$", "delegate_setter"),
    (r"^set\w*DataSource:$", "data_source"),
    # Common Apple framework callback selectors
    (r"^viewDidLoad$", "framework_callback"),
    (r"^viewWillAppear:$", "framework_callback"),
    (r"^viewDidAppear:$", "framework_callback"),
    (r"^viewWillDisappear:$", "framework_callback"),
    (r"^viewDidDisappear:$", "framework_callback"),
    (r"^application:", "framework_callback"),
    (r"^scene:", "framework_callback"),
    (r"^windowScene:", "framework_callback"),
    (r"^tableView:numberOfRows", "framework_callback"),
    (r"^tableView:cellForRow", "framework_callback"),
    (r"^numberOfSectionsIn", "framework_callback"),
    (r"^collectionView:numberOfItems", "framework_callback"),
    (r"^collectionView:cellForItem", "framework_callback"),
    (r"^didReceiveMemoryWarning$", "framework_callback"),
    (r"^awakeFromNib$", "framework_callback"),
    (r"^prepareForSegue:sender:$", "framework_callback"),
    (r"^loadView$", "framework_callback"),
]

_compiled: list[tuple[re.Pattern, str]] = [
    (re.compile(p), role) for p, role in _ROLE_PATTERNS
]


def classify_objc_message(selector: str) -> str:
    """Classify an ObjC selector into a semantic role.

    Args:
        selector: The full ObjC selector string (e.g.
            ``"addObserver:selector:name:object:"``).

    Returns:
        One of: ``notification_observer``, ``notification_poster``,
        ``target_action``, ``action_sender``, ``delegate_setter``,
        ``data_source``, ``framework_callback``, or ``unknown``.
    """
    if not selector:
        return "unknown"
    for pattern, role in _compiled:
        if pattern.match(selector):
            return role
    return "unknown"
