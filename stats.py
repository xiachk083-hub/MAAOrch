"""Persistent run statistics per account — stored in accounts/{id}/stats.json."""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime


class RunStats:
    """Read/write run history and sanity info for a single account."""

    def __init__(self, account_id: str) -> None:
        self._dir = Path(__file__).parent / "accounts" / account_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "stats.json"
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            if self._path.exists():
                return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {"runs": [], "last_read_line": 0}

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    @property
    def last_read_line(self) -> int:
        return self._data.get("last_read_line", 0)

    def set_last_read_line(self, line: int) -> None:
        self._data["last_read_line"] = line
        self._save()

    def save_run(self, tasks: list[dict], sanity: dict | None = None,
                 drops: dict | None = None, start_ts: str | None = None) -> None:
        """Record a completed MAA run."""
        now = datetime.now()
        run = {
            "ts": start_ts or now.strftime("%Y-%m-%d %H:%M:%S"),
            "tasks": {t["name"]: t["status"] for t in tasks},
            "drops": drops or {},
        }
        if sanity:
            cur = sanity["current"]
            mx = sanity["max"]
            deficit = mx - cur
            run["sanity"] = {
                "current": cur,
                "max": mx,
                "deficit": deficit,
                "report_time": sanity.get("report_time", ""),
            }
        self._data.setdefault("runs", []).append(run)
        # Keep last 200 runs
        if len(self._data["runs"]) > 200:
            self._data["runs"] = self._data["runs"][-200:]
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
            if "sanity" in r:
                return r["sanity"]
        return None

    @property
    def total_runs(self) -> int:
        return len(self._data.get("runs", []))
