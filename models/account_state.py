"""Per-account operational state — reliable usage/login/completion/sanity record.

Unlike stats.json (run history, lossy), this keeps ONE live state file per
account that is updated on every lifecycle event and survives restarts:

    models/accounts/{safe_id}/state.json

Fields:
    last_use        — 最近一次使用（启动/停止/处理）
    last_login      — 最近一次登录游戏（MAA 启动）
    last_complete   — 最近一次完成（exit=0）
    last_status     — 最近状态: 完成/失败/卡死/已停止
    last_exit_code  — 最近退出码
    sanity          — 最近体力 {current, max, report_time}
    today_runs      — 今日运行次数（自然日）
    last_stage      — 最近关卡
    last_drops      — 最近掉落
"""
from __future__ import annotations
import json
import re as _re
from pathlib import Path
from datetime import datetime

_STATE_DIR = Path(__file__).parent / "accounts"


class AccountState:
    def __init__(self, account_id: str) -> None:
        safe = _re.sub(r'[^\w.-]', '_', account_id) or "_"
        self._dir = _STATE_DIR / safe
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "state.json"
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            if self._path.exists():
                return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        except Exception:
            pass

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _roll_day(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self._data.get("_day") != today:
            self._data["_day"] = today
            self._data["today_runs"] = 0

    # ── 生命周期写入 ──
    def on_login(self) -> None:
        self._roll_day()
        self._data["last_login"] = self._now()
        self._data["last_use"] = self._now()
        self._save()

    def on_use(self, note: str = "") -> None:
        self._roll_day()
        self._data["last_use"] = self._now()
        if note:
            self._data["last_note"] = note
        self._save()

    def on_complete(self, exit_code: int | None, status: str,
                    sanity: dict | None = None, drops: dict | None = None,
                    stage: str = "") -> None:
        self._roll_day()
        now = self._now()
        self._data["last_complete"] = now
        self._data["last_status"] = status
        self._data["last_exit_code"] = exit_code
        self._data["today_runs"] = int(self._data.get("today_runs", 0)) + 1
        if sanity and isinstance(sanity, dict):
            cur = sanity.get("current")
            if cur is not None:
                self._data["sanity"] = {
                    "current": cur,
                    "max": sanity.get("max", 0),
                    # log_parser 的 report_time 默认 ""（asst.log 无此字段）—
                    # 空字符串会覆盖默认值导致体力记录无时间戳（无法区分新旧）。
                    "report_time": sanity.get("report_time") or now,
                }
        if drops:
            self._data["last_drops"] = dict(drops)
        if stage:
            self._data["last_stage"] = stage
        self._save()

    def on_stuck(self, reason: str = "") -> None:
        self._roll_day()
        self._data["last_status"] = "卡死"
        self._data["last_note"] = reason or "任务级卡死"
        self._data["last_use"] = self._now()
        self._save()

    def on_stopped(self, reason: str = "") -> None:
        self._roll_day()
        self._data["last_status"] = "已停止"
        if reason:
            self._data["last_note"] = reason
        self._data["last_use"] = self._now()
        self._save()

    # ── 读取 ──
    @property
    def data(self) -> dict:
        return dict(self._data)

    @property
    def sanity(self) -> dict | None:
        return self._data.get("sanity")

    @property
    def last_status(self) -> str:
        return self._data.get("last_status", "")

    @property
    def today_runs(self) -> int:
        return int(self._data.get("today_runs", 0))
