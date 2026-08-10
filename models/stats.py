"""Persistent run statistics per account — stored in accounts/{id}/stats.json."""
from __future__ import annotations
import json
import re as _re
from pathlib import Path
from datetime import datetime


class RunStats:
    """Read/write run history and sanity info for a single account.

    Each run is a self-contained record so the stats aggregator can rebuild
    daily / weekly / monthly / yearly / all-time views from the raw list
    without any extra state.
    """

    _MAX_RUNS = 2000

    def __init__(self, account_id: str) -> None:
        safe = _re.sub(r'[^\w.-]', '_', account_id) or "_"
        self._dir = Path(__file__).parent / "accounts" / safe
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "stats.json"
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            if self._path.exists():
                return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:
            self._warn(f"加载 stats 失败: {e}")
        return {"runs": [], "last_read_line": 0}

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        except Exception as e:
            self._warn(f"写入 stats 失败: {e}")

    def _warn(self, msg: str) -> None:
        try:
            from infrastructure.logger import Logger
            Logger("stats").warn(msg)
        except Exception:
            pass

    @property
    def last_read_line(self) -> int:
        return self._data.get("last_read_line", 0)

    def set_last_read_line(self, line: int) -> None:
        self._data["last_read_line"] = line
        self._save()

    def save_run(self, tasks: list[dict] | None = None, sanity: dict | None = None,
                 drops: dict | None = None, start_ts: str | None = None,
                 exit_code: int | None = None, elapsed: float = 0.0,
                 status: str = "", stages: list | None = None,
                 dispatch: str = "", sanity_before: dict | None = None,
                 sanity_after: dict | None = None) -> None:
        """Record a run — called on every cleanup (success, failure or timeout).

        tasks:      [{name, status}] — task list with per-task outcome.
        sanity:     backward-compatible dict (current/max/deficit/report_time).
        drops:      {itemName: qty} collected this run.
        start_ts:   run start "YYYY-MM-DD HH:MM:SS".
        exit_code:  MAA process exit code (None when unknown).
        elapsed:    run duration in seconds.
        status:     human summary: 完成 / 失败 / 超时 / 启动失败 / 已停止.
        stages:     stage ids touched this run (daily + annihilation).
        dispatch:   where the run came from (manual/schedule/smart/retry).
        """
        now = datetime.now()
        run: dict = {
            "ts": start_ts or now.strftime("%Y-%m-%d %H:%M:%S"),
            "end_ts": now.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed": round(float(elapsed or 0.0), 1),
            "status": status or ("完成" if exit_code == 0 else "未知"),
        }
        if exit_code is not None:
            run["exit_code"] = int(exit_code)
        if dispatch:
            run["dispatch"] = dispatch
        t_map: dict[str, str] = {}
        for t in (tasks or []):
            if isinstance(t, dict) and t.get("name"):
                t_map[t["name"]] = str(t.get("status", ""))
        run["tasks"] = t_map
        run["drops"] = dict(drops or {})
        if stages:
            run["stages"] = [str(s) for s in stages]
        for key, src in (("sanity_before", sanity_before),
                         ("sanity_after", sanity_after),
                         ("sanity", sanity)):
            if src and isinstance(src, dict):
                cur = src.get("current")
                if cur is None:
                    continue
                rec = {"current": cur,
                       "max": src.get("max", 0),
                       "deficit": max(0, int(src.get("max", 0) or 0) - int(cur or 0)),
                       "report_time": src.get("report_time", "")}
                run[key] = rec
        self._data.setdefault("runs", []).append(run)
        if len(self._data["runs"]) > self._MAX_RUNS:
            self._data["runs"] = self._data["runs"][-self._MAX_RUNS:]
        self._save()

    def get_daily(self, date_str: str | None = None) -> dict:
        """Get stats for a specific date (YYYY-MM-DD)."""
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")
        runs = [r for r in self._data.get("runs", []) if r.get("ts", "").startswith(date_str)]
        task_counts: dict[str, int] = {}
        drop_totals: dict[str, int] = {}
        for r in runs:
            for name, status in r.get("tasks", {}).items():
                if status == "完成":
                    task_counts[name] = task_counts.get(name, 0) + 1
            for item, qty in r.get("drops", {}).items():
                drop_totals[item] = drop_totals.get(item, 0) + qty
        return {"runs": len(runs), "tasks": task_counts, "drops": drop_totals}

    def get_last_sanity(self) -> dict | None:
        """Get sanity info from the most recent run."""
        runs = self._data.get("runs", [])
        for r in reversed(runs):
            if r.get("sanity_after") or r.get("sanity"):
                return r.get("sanity_after") or r.get("sanity")
        return None

    @property
    def total_runs(self) -> int:
        return len(self._data.get("runs", []))

    @property
    def runs(self) -> list[dict]:
        return self._data.get("runs", [])
