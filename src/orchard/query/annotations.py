"""Small response annotations for triage-oriented graph results."""

from __future__ import annotations

import os


def execution_boundary_for(symbol: dict) -> dict[str, str] | None:
    """Return a heuristic execution-boundary label for a symbol-like dict."""
    name = (symbol.get("name") or "").lower()
    kind = (symbol.get("kind") or "").lower()
    semantic_role = (symbol.get("semantic_role") or "").lower()

    if semantic_role == "notification_observer":
        return _boundary("notification_callback_sink", "objc notification observer call")
    if semantic_role == "framework_callback" or _looks_like_sdk_callback(name, kind):
        return _boundary("sdk_callback", "framework callback-shaped symbol")
    if _looks_like_worker_dispatch(name):
        return _boundary("worker_thread_dispatch", "worker/thread dispatch-shaped symbol")
    if _looks_like_main_thread_task(name):
        return _boundary("main_thread_task", "main-thread dispatch-shaped symbol")
    if _looks_like_lifecycle_path(name):
        return _boundary("lifecycle_uninit_path", "lifecycle teardown-shaped symbol")
    if _looks_like_callback_sink(name):
        return _boundary("notification_callback_sink", "callback/notification-shaped symbol")
    return None


def source_scope_for(file_path: str, workspace_root: str | None = None) -> dict[str, str]:
    """Classify whether *file_path* is inside the active workspace root."""
    if not file_path:
        return {"status": "unknown", "reason": "missing_file_path"}
    root = os.path.abspath(workspace_root or os.getcwd())
    path = os.path.abspath(file_path)
    try:
        inside = os.path.commonpath([root, path]) == root
    except ValueError:
        inside = False
    if inside:
        return {"status": "inside_workspace_root", "workspace_root": root}
    return {
        "status": "outside_workspace_root",
        "workspace_root": root,
        "hint": "symbol source is outside current workspace root",
    }


def annotate_symbol_source_scope(symbol: dict, workspace_root: str | None = None) -> dict:
    """Return *symbol* with ``source_scope`` when a file path is available."""
    file_path = symbol.get("file_path") or ""
    if not file_path:
        return symbol
    return {**symbol, "source_scope": source_scope_for(file_path, workspace_root)}


def _boundary(role: str, reason: str) -> dict[str, str]:
    return {"role": role, "confidence": "heuristic", "reason": reason}


def _looks_like_sdk_callback(name: str, kind: str) -> bool:
    callback_names = (
        "viewdidload",
        "viewwillappear",
        "viewdidappear",
        "viewwilldisappear",
        "viewdiddisappear",
        "application:",
        "scene:",
        "tableview:",
        "collectionview:",
    )
    return "callback" in kind or any(token in name for token in callback_names)


def _looks_like_worker_dispatch(name: str) -> bool:
    tokens = (
        "process_msg",
        "worker",
        "thread",
        "dispatch",
        "queue",
        "async",
        "runloop",
    )
    return any(token in name for token in tokens)


def _looks_like_main_thread_task(name: str) -> bool:
    tokens = ("mainthread", "main_thread", "mainqueue", "main_queue", "dispatch_get_main_queue")
    return any(token in name for token in tokens)


def _looks_like_lifecycle_path(name: str) -> bool:
    tokens = ("dealloc", "destroy", "dispose", "cleanup", "uninit", "tear_down", "teardown")
    return any(token in name for token in tokens) or name.startswith("~")


def _looks_like_callback_sink(name: str) -> bool:
    tokens = ("notification", "notify", "observer", "callback", "selector")
    return any(token in name for token in tokens)
