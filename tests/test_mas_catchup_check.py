# -*- coding: utf-8 -*-
"""受控补漏判据器的离线测试（不连真实 MAS）。

最重要的一条：assert 源码里不含任何写端点 —— 这个工具永远只能"提议"，不能"发车"。
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import mas_catchup_check as m  # noqa: E402

NOW = dt.datetime(2026, 9, 26, 12, 0, 0)
TODAY = NOW.date().isoformat()
TODAY_NAME = m.WEEKDAYS[NOW.weekday()]


def row(**kw):
    base = {"account": "acct1", "queue": "q-1", "time": "05:00",
            "days": list(m.WEEKDAYS), "enabled": True}
    base.update(kw)
    return base


def test_all_conditions_met_is_eligible():
    out = m.evaluate([row()], {}, NOW)
    assert len(out) == 1
    assert out[0]["eligible"] is True
    assert "通过全部判据" in out[0]["reason"]


def test_disabled_timeset_not_eligible():
    out = m.evaluate([row(enabled=False)], {}, NOW)
    assert out[0]["eligible"] is False
    assert "未启用" in out[0]["reason"]


def test_wrong_weekday_not_eligible():
    other = [d for d in m.WEEKDAYS if d != TODAY_NAME]
    out = m.evaluate([row(days=other)], {}, NOW)
    assert out[0]["eligible"] is False
    assert "执行日" in out[0]["reason"]


def test_grace_not_elapsed_not_eligible():
    # 把时间设成 11:30，12:00 时只过了 30 分钟 < 60
    out = m.evaluate([row(time="11:30")], {}, NOW, grace_minutes=60)
    assert out[0]["eligible"] is False
    assert "不足 60 分钟" in out[0]["reason"]


def test_done_today_blocks_candidate():
    hist = {"acct1": [{"date": TODAY + " 05:57:15", "status": "DONE"}]}
    out = m.evaluate([row()], hist, NOW)
    assert out[0]["eligible"] is False
    assert "已有 DONE 记录" in out[0]["reason"]
    assert out[0]["evidence"]["done_today"] == 1


def test_recent_activity_blocks_candidate():
    # 今天没有 DONE，但 10 分钟前有记录 => 可能仍在运行 => 不补
    stamp = (NOW - dt.timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    hist = {"acct1": [{"date": stamp, "status": "ERROR"}]}
    out = m.evaluate([row()], hist, NOW, recent_minutes=60)
    assert out[0]["eligible"] is False
    assert "可能仍在运行" in out[0]["reason"]


def test_old_error_record_does_not_block():
    # 今天 05:57 失败过，但已超过 recent 窗口 => 仍可作为候选（有证据说要补）
    hist = {"acct1": [{"date": TODAY + " 05:57:15", "status": "ERROR"}]}
    out = m.evaluate([row()], hist, NOW, recent_minutes=60)
    assert out[0]["eligible"] is True
    assert out[0]["evidence"]["done_today"] == 0


def test_upstream_error_is_structured(monkeypatch):
    out = m.evaluate([row()], {}, NOW)
    assert out[0]["evidence"]["scheduled"] == "05:00"
    with pytest.raises(m.UpstreamError):
        m.api("/api/dispatch/start", {})


def test_scrub_drops_credentials():
    raw = {"Info": {"Name": "a", "Password": "x", "SklandToken": "y"}, "n": 1}
    assert m._scrub(raw) == {"Info": {"Name": "a"}, "n": 1}


def test_source_contains_no_write_endpoints():
    """防止将来被改成会发车的执行器。"""
    src = (ROOT / "tools" / "mas_catchup_check.py").read_text(encoding="utf-8")
    bad = re.findall(
        r"/api/[\w/]*(?:add|update|delete|remove|import|export|close|start|stop|reorder|clear|cancel|set)[\w/]*",
        src, flags=re.I)
    assert bad == [], "发现写端点字符串: %s" % bad


def test_read_endpoint_whitelist_is_readonly():
    for p in m.READ_ENDPOINTS:
        assert not re.search(r"add|update|delete|remove|import|export|close|start|stop|reorder|clear|cancel",
                             p, flags=re.I), p
