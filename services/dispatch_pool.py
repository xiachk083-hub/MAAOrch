"""调度模板池 — 任务模板与账号分离，防止竞态覆盖。
持久化到 dispatch_pool.json，重启后不丢失。

2026-08-13 任务参数化: 条目从纯任务名列表扩展为带参结构（向后兼容）:
    旧: {"did": ["StartUp","Fight"]}
    新: {"did": {"tasks": ["StartUp","Fight"], "fight": {"stage": "TO-7", "times": 66}}}
"""
import json, uuid
from pathlib import Path

_pool_path = Path(__file__).parent / "dispatch_pool.json"
_dispatch_templates: dict = {}


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


def create_dispatch(task_list: list[str], params: dict | None = None) -> str:
    """Create a dispatch with the given task list (+可选参数) and return its ID.

    params 例: {"fight": {"stage": "TO-7", "times": 66}}
    无参数时保持旧形态（纯任务名列表），完全向后兼容。
    """
    did = uuid.uuid4().hex[:12]
    if params:
        _dispatch_templates[did] = {"tasks": list(task_list), **params}
    else:
        _dispatch_templates[did] = list(task_list)
    _save()
    return did


def get_template_info(dispatch_id: str) -> tuple[list[str], dict]:
    """返回 (task_list, params)。旧形态/缺失 → ([], {})。"""
    tpl = _dispatch_templates.get(dispatch_id)
    if isinstance(tpl, dict):
        tasks = tpl.get("tasks", [])
        params = {k: v for k, v in tpl.items() if k != "tasks"}
        return (list(tasks) if isinstance(tasks, list) else []), params
    if isinstance(tpl, list):
        return list(tpl), {}
    return [], {}


def get_template(dispatch_id: str) -> list[str] | None:
    """Retrieve the task list for a dispatch ID, or None if not found."""
    tasks, _p = get_template_info(dispatch_id)
    return tasks or None


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
