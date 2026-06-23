"""调度模板池 — 任务模板与账号分离，防止竞态覆盖。
持久化到 dispatch_pool.json，重启后不丢失。"""
import json, uuid
from pathlib import Path

_pool_path = Path(__file__).parent / "dispatch_pool.json"
_dispatch_templates: dict[str, list[str]] = {}


def _save() -> None:
    """Persist templates to disk."""
    try:
        _pool_path.write_text(json.dumps(_dispatch_templates, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _load() -> None:
    """Restore templates from disk on startup."""
    global _dispatch_templates
    try:
        if _pool_path.exists():
            data = json.loads(_pool_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _dispatch_templates = data
    except Exception:
        _dispatch_templates = {}


def create_dispatch(task_list: list[str]) -> str:
    """Create a dispatch with the given task list and return its ID."""
    did = uuid.uuid4().hex[:12]
    _dispatch_templates[did] = list(task_list)
    _save()
    return did


def get_template(dispatch_id: str) -> list[str] | None:
    """Retrieve the task list for a dispatch ID, or None if not found."""
    return _dispatch_templates.get(dispatch_id)


def remove_dispatch(dispatch_id: str) -> None:
    """Remove a dispatch template (called on completion)."""
    _dispatch_templates.pop(dispatch_id, None)
    _save()


def clear_all() -> None:
    """Clear all dispatch templates (e.g. on version upgrade)."""
    _dispatch_templates.clear()
    _save()


# Auto-load on module import
_load()
