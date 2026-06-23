"""Telegram notification — send task failure alerts to your phone."""
from __future__ import annotations
import json, urllib.request, urllib.error, logging

_LOG = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"


def send(tg_token: str, chat_id: str, text: str) -> bool:
    """Send a plain text message to a Telegram chat. Returns True on success."""
    if not tg_token or not chat_id:
        return False
    try:
        url = _API.format(token=tg_token, method="sendMessage")
        data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        _LOG.warning(f"TG send failed: {e}")
        return False


def format_alert(account_name: str, exit_code: int, failed_tasks: list[str],
                 consecutive_failures: int, game_client: str) -> str:
    """Build a formatted alert message."""
    lines = [
        "<b>⚠️ MAAOrch 任务失败</b>",
        f"账号: {account_name}",
        f"客户端: {game_client}",
        f"退出码: {exit_code}",
    ]
    if failed_tasks:
        lines.append(f"失败任务: {', '.join(failed_tasks)}")
    if consecutive_failures > 0:
        lines.append(f"连续失败: {consecutive_failures} 次")
    lines.append(f"时间: {__import__('datetime').datetime.now().strftime('%H:%M:%S')}")
    return "\n".join(lines)
