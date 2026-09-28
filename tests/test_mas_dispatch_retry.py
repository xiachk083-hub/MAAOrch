# -*- coding: utf-8 -*-
"""mas_dispatch 发车「失败分类 / 退避重试 / 告警台账」的离线测试。

硬约束：
  1. 不连 Hermes、不连 AUTO-MAS —— post()/reachable() 全部被替换成脚本桩；
  2. 不写真实 runtime/ —— RUNTIME/STATE/LOG/ALERTS/LOCK 全部指到 tmp_path；
  3. 不真等 —— time.sleep 被替换。

覆盖：失败分类表（返回体/异常）、退避重试与预算 defer、告警写入/去重/自愈/test 隔离、
并发锁（被占拒绝 / 陈旧锁回收）、CLI 面（--selftest 全绿、全发过时 --reconcile 零动作）。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import mas_dispatch as md  # noqa: E402

OKR = {"code": 200, "status": "success", "message": "操作成功", "taskId": "task-ok"}


@pytest.fixture()
def tmp_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "RUNTIME", str(tmp_path))
    monkeypatch.setattr(md, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(md, "LOG", str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(md, "ALERTS", str(tmp_path / "alerts.json"))
    monkeypatch.setattr(md, "LOCK", str(tmp_path / "lock"))
    monkeypatch.setattr(md, "NO_GO", False)
    return tmp_path


def _script(monkeypatch, reach=(), posts=(), calls=None):
    """给 reachable()/post() 灌脚本；reach=[(ok,why)...] post=[dict|Exception...]。"""
    reach, posts = list(reach), list(posts)

    def _reach():
        return reach.pop(0) if reach else (True, "code=200")

    def _post(path, body=None, timeout=30):
        if calls is not None:
            calls.append((path, body))
        item = posts.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(md, "reachable", _reach)
    monkeypatch.setattr(md, "post", _post)


def _no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(md.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


# --------------------------------------------------------------------------
# 1. 离线回归套件（模块内 selftest）+ 白名单
# --------------------------------------------------------------------------

def test_selftest_all_green():
    res = md.selftest()
    assert res["ok"], res
    assert res["passed"] >= 17 and res["failed"] == 0


def test_whitelist_blocks_before_any_io(tmp_runtime, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("白名单不过时不应有任何 REST 调用")

    monkeypatch.setattr(md, "post", _boom)
    monkeypatch.setattr(md, "reachable", _boom)
    r = md.dispatch("打杂任务", "uid-1", True)
    assert r["refused"] == "not_whitelisted"


# --------------------------------------------------------------------------
# 2. 失败分类表
# --------------------------------------------------------------------------

def test_classify_resp_table():
    r = md._classify_resp({"code": 500, "message": "RuntimeError: 任务 X 已在运行"})
    assert r["reason"] == "target_running" and r["retryable"]
    r = md._classify_resp({"code": 500, "message": "任务 官-1-1-7 已被其他任务调度器锁定"})
    assert r["reason"] == "slot_occupied" and r["retryable"]
    r = md._classify_resp({"code": 500, "message": "ValueError: badly formed hexadecimal UUID string"})
    assert r["reason"] == "target_not_found" and not r["retryable"]
    r = md._classify_resp({"code": 200, "taskId": ""})
    assert r["reason"] == "no_taskid" and not r["retryable"]
    r = md._classify_resp({"code": 500, "message": "KeyError: 'Foo'"})
    assert r["reason"] == "bad_response" and not r["retryable"]
    r = md._classify_resp({"code": 200, "taskId": "T-9"})
    assert r["ok"] and r["taskId"] == "T-9"


def test_classify_exc_table():
    reason, _detail, retryable = md._classify_exc(urllib.error.URLError("refused"))
    assert reason == "rest_unreachable" and retryable
    reason, _detail, retryable = md._classify_exc(
        urllib.error.HTTPError("u", 500, "boom", {}, None))
    assert reason == "http_error" and retryable
    reason, _detail, retryable = md._classify_exc(
        urllib.error.HTTPError("u", 400, "bad request", {}, None))
    assert reason == "http_error" and not retryable
    reason, _detail, retryable = md._classify_exc(json.JSONDecodeError("x", "<html>", 0))
    assert reason == "bad_response" and not retryable


# --------------------------------------------------------------------------
# 3. 退避重试 / 预算 defer
# --------------------------------------------------------------------------

def test_retry_delays_then_success(tmp_runtime, monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    _script(monkeypatch, reach=[(True, "code=200")] * 3,
            posts=[urllib.error.URLError("x"), urllib.error.URLError("x"), OKR])
    r = md.dispatch("日常定时1", "uid-1", True, retries=2, retry_delays=[60, 180])
    assert r["dispatched"] and r["retries"] == 2
    assert sleeps == [60, 180]
    assert md.active_alerts() == []


def test_retry_exhausted_writes_terminal_alert(tmp_runtime, monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    _script(monkeypatch, reach=[(True, "code=200")] * 3,
            posts=[urllib.error.URLError("x")] * 3)
    r = md.dispatch("日常定时1", "uid-1", True, retries=2, retry_delays=[7, 7])
    assert r["failed"] and r["reason"] == "rest_unreachable" and r["attempts"] == 3
    assert sleeps == [7, 7]
    alerts = md.active_alerts()
    assert len(alerts) == 1 and alerts[0]["attempts"] == 3


def test_budget_defers_without_alert_or_sleep(tmp_runtime, monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    _script(monkeypatch, reach=[(True, "code=200")], posts=[urllib.error.URLError("x")])
    r = md.dispatch("日常定时1", "uid-1", True, retries=5, retry_delays=[300], retry_budget=0)
    assert r.get("deferred") and not r.get("alert")
    assert sleeps == [] and md.active_alerts() == []


# --------------------------------------------------------------------------
# 4. 告警台账：自愈 / test 隔离 / 去重
# --------------------------------------------------------------------------

def test_alert_selfheal_on_next_success(tmp_runtime, monkeypatch):
    _no_sleep(monkeypatch)
    _script(monkeypatch, reach=[(True, "code=200")], posts=[urllib.error.URLError("x")])
    r = md.dispatch("日常定时1", "uid-1", True, retries=0)
    assert r["alert"] == "日常定时1:rest_unreachable"
    assert len(md.active_alerts()) == 1
    _script(monkeypatch, reach=[(True, "code=200")], posts=[OKR])
    r2 = md.dispatch("日常定时1", "uid-1", True, retries=0)
    assert r2["dispatched"]
    assert r2["alert_cleared"] == "日常定时1:rest_unreachable"
    assert md.active_alerts() == []


def test_alert_dedupe_same_reason_same_day(tmp_runtime, monkeypatch):
    _no_sleep(monkeypatch)
    _script(monkeypatch, reach=[(True, "code=200")] * 2, posts=[urllib.error.URLError("x")] * 2)
    md.dispatch("日常定时1", "uid-1", True, retries=0)
    md.dispatch("日常定时1", "uid-1", True, retries=0)
    assert len(md.active_alerts()) == 1


def test_test_flagged_isolated_then_clearable(tmp_runtime, monkeypatch):
    _no_sleep(monkeypatch)
    _script(monkeypatch, reach=[(True, "code=200")], posts=[urllib.error.URLError("x")])
    md.dispatch("日常定时1", "bad-id", True, post_id="retryalert-selftest", test=True, retries=0)
    assert md.active_alerts() == []           # 巡检视图看不到
    assert len(md.active_alerts(exclude_test=False)) == 1
    assert md.clear_test_alerts() == 1
    assert md.active_alerts(exclude_test=False) == []


# --------------------------------------------------------------------------
# 5. 并发锁
# --------------------------------------------------------------------------

def test_lock_busy_refused_then_stale_reclaim(tmp_runtime, monkeypatch):
    _no_sleep(monkeypatch)
    lock = pathlib.Path(md.LOCK)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("pid=999 ts=fake", encoding="utf-8")
    _script(monkeypatch, reach=[(True, "code=200")], posts=[OKR])
    r = md.dispatch("日常定时1", "uid-1", True, retries=0)
    assert r["refused"] == "concurrent_dispatch"
    assert lock.exists()
    old = time.time() - (md.LOCK_STALE_S + 60)
    os.utime(lock, (old, old))
    _script(monkeypatch, reach=[(True, "code=200")], posts=[OKR])
    r2 = md.dispatch("日常定时1", "uid-1", True, retries=0)
    assert r2["dispatched"]
    assert not lock.exists()


# --------------------------------------------------------------------------
# 6. CLI 面（子进程，离线）
# --------------------------------------------------------------------------

def test_cli_selftest_ok():
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "mas_dispatch.py"), "--selftest"],
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0
    assert "SELFTEST=OK" in out.stdout


def test_cli_reconcile_all_sent_is_noop(tmp_path):
    today = md.dt.date.today().isoformat()
    state = {today: {n: {"shift": md.shift_of(n), "at": "x"} for n in md.schedule()}}
    (tmp_path / "mas_dispatch_state.json").write_text(json.dumps(state), encoding="utf-8")
    env = dict(os.environ, MAAORCH_DISPATCH_RUNTIME=str(tmp_path))
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "mas_dispatch.py"), "--reconcile", "--json"],
                         capture_output=True, text=True, timeout=180, env=env)
    assert out.returncode == 0
    payload = json.loads(out.stdout.strip().splitlines()[-1])
    assert payload["count"] == 0 and payload["alerts"] == []
