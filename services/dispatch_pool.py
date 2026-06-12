"""调度模板池 — 任务模板与账号分离，防止竞态覆盖。"""
import uuid

_dispatch_templates: dict[str, list[str]] = {}


def create_dispatch(task_list: list[str]) -> str:
    """Create a dispatch with the given task list and return its ID."""
    did = uuid.uuid4().hex[:12]
    _dispatch_templates[did] = list(task_list)
    return did


def get_template(dispatch_id: str) -> list[str] | None:
    """Retrieve the task list for a dispatch ID, or None if not found."""
    return _dispatch_templates.get(dispatch_id)


def remove_dispatch(dispatch_id: str) -> None:
    """Remove a dispatch template (called on normal completion)."""
    _dispatch_templates.pop(dispatch_id, None)
