"""Tests for RunStats — persistent run history."""
import json, tempfile, shutil
from pathlib import Path
from datetime import datetime


class TestRunStats:
    @staticmethod
    def _make_stats(account_id="test_id"):
        from stats import RunStats
        # Point to temp dir
        import os
        tmp = tempfile.mkdtemp()
        st = RunStats(account_id)
        st._dir = Path(tmp)
        st._path = Path(tmp) / "stats.json"
        st._data = {"runs": [], "last_read_line": 0}
        return st, tmp

    def test_save_and_load(self):
        st, tmp = self._make_stats()
        st.save_run(
            tasks=[{"name": "刷关作战", "status": "完成"}, {"name": "公开招募", "status": "完成"}],
            sanity={"current": 5, "max": 210, "deficit": 205},
            drops={"固源岩": 21, "赤金": 12},
        )
        # Reload
        data = st._load()
        assert len(data["runs"]) == 1
        r = data["runs"][0]
        assert r["tasks"] == {"刷关作战": "完成", "公开招募": "完成"}
        assert r["sanity"]["current"] == 5
        assert r["sanity"]["max"] == 210
        assert r["sanity"]["deficit"] == 205
        assert r["drops"] == {"固源岩": 21, "赤金": 12}
        shutil.rmtree(tmp)

    def test_last_sanity(self):
        st, tmp = self._make_stats()
        st.save_run(tasks=[], sanity={"current": 5, "max": 210, "deficit": 205})
        st.save_run(tasks=[], sanity={"current": 87, "max": 135, "deficit": 48})
        s = st.get_last_sanity()
        assert s["current"] == 87
        assert s["max"] == 135
        shutil.rmtree(tmp)

    def test_daily_aggregation(self):
        st, tmp = self._make_stats()
        today = datetime.now().strftime("%Y-%m-%d")
        st._data["runs"] = [
            {"ts": f"{today} 08:00:00", "tasks": {"刷关作战": "完成", "基建换班": "完成"}, "drops": {"固源岩": 10}},
            {"ts": f"{today} 12:00:00", "tasks": {"刷关作战": "完成"}, "drops": {"固源岩": 5}},
            {"ts": "2020-01-01 08:00:00", "tasks": {"刷关作战": "完成"}, "drops": {"固源岩": 100}},
        ]
        daily = st.get_daily(today)
        assert daily["runs"] == 2
        assert daily["tasks"]["刷关作战"] == 2
        assert daily["drops"]["固源岩"] == 15
        daily_old = st.get_daily("2020-01-01")
        assert daily_old["runs"] == 1
        shutil.rmtree(tmp)

    def test_max_200_runs(self):
        st, tmp = self._make_stats()
        for i in range(250):
            st._data["runs"].append({"ts": f"2026-01-{i:02d}", "tasks": {}, "drops": {}})
        st.save_run(tasks=[{"name": "test", "status": "完成"}])
        assert len(st._data["runs"]) <= 201  # 200 old + 1 new
        shutil.rmtree(tmp)

    def test_total_runs(self):
        st, tmp = self._make_stats()
        st._data["runs"] = [{"ts": "a", "tasks": {}}, {"ts": "b", "tasks": {}}]
        assert st.total_runs == 2
        shutil.rmtree(tmp)
