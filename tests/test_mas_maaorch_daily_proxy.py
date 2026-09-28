# -*- coding: utf-8 -*-
"""task-7：MAAOrch『清日常』stdio 代理（tools/mas_maaorch_daily_proxy.py）的**完全离线**测试。

被测契约：
  1) 工具白名单：tools/list 只返回 6 只读 + mas_rerun_state/mas_rerun + daily_gap + daily_clear；
     41 个上游工具里其余一个都不出现；
  2) **默认预演绝不发车**：daily_clear(confirm 缺省/False/非布尔) 只做只读预检，零 smart_dispatch；
  3) confirm=True 才转发，且必须过硬门禁：too_many_accounts / dispatch_disabled / readonly_unavailable /
     batch_running / no_gap / unknown_account；
  4) daily_gap 只读、永不写；
  5) 脱敏：token 与敏感键名的值不进返回值/日志；
  6) 按需拉起上游：命令/日志/超时/禁用开关都有明确结构（不真起进程）；
  7) mas_rerun 原样转发（task-10）：缺省/dry_run=True 只预演，dry_run=False 才可能下发，
     单批 ≤8，MAAORCH_DAILY_NO_DISPATCH 置位时禁发车；每次调用写审计日志行。

本测试**不连任何网络**：autouse fixture 把 HTTP 传输换成"网络绊线"（一碰就炸）。
只有两条 HTTP stub 用例会把真传输换回来，它们只连 127.0.0.1 上的**本进程 HTTP stub**。

运行：python -m pytest tests/test_mas_maaorch_daily_proxy.py -q
"""
from __future__ import annotations

import asyncio
import http.server
import json
import pathlib
import re
import sys
import threading

import pytest

import mcp.types as types

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROXY = ROOT / "tools" / "mas_maaorch_daily_proxy.py"
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import mas_maaorch_daily_proxy as m  # noqa: E402

_REAL_RPC = m._rpc_sync              # 真 HTTP 传输（仅 HTTP stub 用例显式恢复）
_FAKE_TOKEN = "TEST-TOKEN-0123456789abcdef"
_TODAY = m.today_str()
_DONE_ROW = {"index": 0, "id": "A1", "name": "A1", "running": False, "queued": False,
             "sanity_pct": 5.0, "done_ts": _TODAY + " 07:10:00", "today_runs": 2}


def _row(aid="A1", name=None, *, pct=None, done_ts="", running=False, queued=False, runs=0, index=0):
    return {"index": index, "id": aid, "name": name or aid, "running": running, "queued": queued,
            "sanity_pct": pct, "done_ts": done_ts, "today_runs": runs, "last_status": ""}


def _clean_queue():
    return {"pending": [], "active": [], "pending_count": 0, "active_count": 0, "paused": False}


def _clean_mas(**kw):
    d = {"ok": True, "host": {"ok": True, "maa_processes": 0, "now": "2026-09-26 21:00:00"},
         "today": {"date": _TODAY, "accounts": 0}, "daily": [], "rerun": {"running": False},
         "waiting": 0}
    d.update(kw)
    return d


# ── 夹具 ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """网络绊线 + 固定假 token + 「上游已监听」：任何真实上游调用都会让用例炸掉。"""
    def _boom(*a, **k):
        raise AssertionError("测试期间禁止触达上游（网络绊线）")

    monkeypatch.setattr(m, "_rpc_sync", _boom)                 # 真 HTTP 一律炸
    monkeypatch.setattr(m, "port_open", lambda *a, **k: True)  # ensure_upstream 直接放行
    monkeypatch.setattr(m, "_TOOLS_CACHE", None)
    monkeypatch.setenv(m.TOKEN_ENV, _FAKE_TOKEN)               # 绝不读真实 models/config.json
    monkeypatch.setenv(m.URL_ENV, m.DEFAULT_URL)
    for env in (m.NO_DISPATCH_ENV, m.STRICT_MAS_ENV, m.NO_AUTOSTART_ENV, m.MAX_ACCOUNTS_ENV, m.DAYS_ENV):
        monkeypatch.delenv(env, raising=False)
    yield


class FakeUpstream:
    """可编程假上游：记录每次 tools/call，按工具名返回合成只读数据。"""

    def __init__(self, *, rows=None, queue=None, dashboard=None, mas=None, write=None, fail=(),
                 rerun_state=None):
        self.rows = rows if rows is not None else [_row()]
        self.queue = queue if queue is not None else _clean_queue()
        self.dashboard = dashboard if dashboard is not None else {"ok": True, "capacity": {"running": 0}}
        self.mas = mas if mas is not None else _clean_mas()
        self.write = write if write is not None else {"ok": True, "dispatched": True}
        self.rerun_state = rerun_state if rerun_state is not None else {"ok": True, "running": False,
                                                                       "accounts": []}
        self.fail = set(fail)
        self.calls = []

    async def call(self, name, arguments=None):
        self.calls.append({"name": name, "arguments": arguments})
        if name in self.fail:
            raise m.UpstreamError("simulated failure: %s" % name)
        if name == m.UPSTREAM_WRITE_TOOL:
            return _ok(self.write)
        if name == m.RERUN_STATE_TOOL:
            return _ok(dict(self.rerun_state))
        if name == m.RERUN_TOOL:
            # 复刻上游语义（network/mcp_server.py:104-124 _gate）：dry_run=True 只回显 would_call，
            # 绝不下发；dry_run=False 才"真下发"（这里只回一个 started 标记，不碰任何远端）。
            args = dict(arguments or {})
            out = {"ok": True, "action": "rerun", "dry_run": True,
                   "would_call": {"method": "POST", "path": "/api/action/mas_rerun", "body": args}}
            if args.get("dry_run") is False:
                out = {"ok": True, "action": "rerun", "dry_run": False, "started": True,
                       "accounts": list(args.get("accounts") or [])}
            return _ok(out)
        table = {"account_status": {"ok": True, "accounts": self.rows, "stats": {"total": len(self.rows)}},
                 "queue": self.queue, "dashboard": self.dashboard, "mas_status": self.mas}
        if name not in table:
            raise m.UpstreamError("假上游没定义工具 %s" % name)
        return _ok(table[name])

    @property
    def write_calls(self):
        return [c for c in self.calls if c["name"] == m.UPSTREAM_WRITE_TOOL]

    @property
    def read_names(self):
        return [c["name"] for c in self.calls]


@pytest.fixture
def upstream(monkeypatch):
    def _mk(**kw):
        fake = FakeUpstream(**kw)
        monkeypatch.setattr(m, "_upstream_call_tool", fake.call)
        return fake
    return _mk


def _ok(payload: dict) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text",
                                                           text=json.dumps(payload, ensure_ascii=False))],
                                isError=False)


def _tool(name: str) -> types.Tool:
    return types.Tool(name=name, description="stub", inputSchema={"type": "object", "properties": {}})


def _payload(res: types.CallToolResult) -> dict:
    return json.loads(res.content[0].text)


def _text(res: types.CallToolResult) -> str:
    return res.content[0].text


def _clear(args):
    return asyncio.run(m._handle_call_tool(m.DAILY_TOOL, args))


def _gap(args=None):
    return asyncio.run(m._handle_call_tool(m.GAP_TOOL, args if args is not None else {}))


ALL_UPSTREAM_NAMES = m.UPSTREAM_SCHEMA_TOOLS + m.FORBIDDEN_UPSTREAM    # 上游实测 41 个


# ── 1) 白名单 ───────────────────────────────────────────────────────

def test_exposed_whitelist_is_exact():
    assert m.READONLY_WHITELIST == ("dashboard", "account_status", "mas_status", "mas_snapshot",
                                    "queue", "health")
    assert m.EXPOSED_TOOLS == m.READONLY_WHITELIST + ("mas_rerun_state", "mas_rerun",
                                                      "daily_clear", "daily_gap")
    assert len(m.EXPOSED_TOOLS) == 10
    assert m.whitelist_problems() == []
    assert not (set(m.EXPOSED_TOOLS) & set(m.FORBIDDEN_UPSTREAM))


def test_upstream_catalogue_size_matches_fact():
    """上游实测 41 个工具：暴露 8（6 只读 + mas_rerun_state/mas_rerun）+ 隐藏 33
    （含 smart_dispatch 本体；mas_cleanup 等破坏性工具仍隐藏）。"""
    assert len(ALL_UPSTREAM_NAMES) == 41
    assert len(m.UPSTREAM_SCHEMA_TOOLS) == 8
    assert len(m.FORBIDDEN_UPSTREAM) == 33
    assert m.UPSTREAM_WRITE_TOOL in m.FORBIDDEN_UPSTREAM
    assert "mas_cleanup" in m.FORBIDDEN_UPSTREAM
    assert not ({"mas_rerun", "mas_rerun_state"} & set(m.FORBIDDEN_UPSTREAM))


def test_whitelist_problems_detects_leak():
    assert m.whitelist_problems(m.EXPOSED_TOOLS + ("account_stop",))       # 混进写工具
    assert m.whitelist_problems(("dashboard",))                            # 缺项


def test_tools_list_filters_41_to_10(monkeypatch):
    async def _stub():
        return [_tool(n) for n in ALL_UPSTREAM_NAMES]
    monkeypatch.setattr(m, "_upstream_list_tools", _stub)
    got = [t.name for t in asyncio.run(m._handle_list_tools())]
    assert sorted(got) == sorted(m.EXPOSED_TOOLS)
    assert "mas_rerun" in got and "mas_rerun_state" in got          # task-10 新增的两只
    for danger in ("smart_dispatch", "account_stop", "maa_stop_all", "queue_control",
                   "kill_audit_decide", "enqueue", "mas_cleanup", "emulator_control",
                   "account_launch", "maa_launch_source"):
        assert danger not in got


def test_tools_list_falls_back_when_upstream_down(monkeypatch):
    async def _boom():
        raise m.UpstreamError("upstream down")
    monkeypatch.setattr(m, "_upstream_list_tools", _boom)
    got = asyncio.run(m._handle_list_tools())
    assert sorted(t.name for t in got) == sorted(m.EXPOSED_TOOLS)          # 工具面恒定


def test_tools_list_caches_upstream_schemas(monkeypatch):
    """上游 schema 只取一次：工具面恒定，不给上游添无谓压力（同 mas_scrub_proxy 的 F2 教训）。"""
    calls = {"n": 0}

    async def _stub():
        calls["n"] += 1
        return [_tool(n) for n in ALL_UPSTREAM_NAMES]

    monkeypatch.setattr(m, "_upstream_list_tools", _stub)
    for _ in range(3):
        assert len(asyncio.run(m._handle_list_tools())) == len(m.EXPOSED_TOOLS)
    assert calls["n"] == 1


def test_failed_tools_list_is_not_cached(monkeypatch):
    calls = {"n": 0}

    async def _stub():
        calls["n"] += 1
        if calls["n"] == 1:
            raise m.UpstreamError("upstream down")
        return [_tool(n) for n in ALL_UPSTREAM_NAMES]

    monkeypatch.setattr(m, "_upstream_list_tools", _stub)
    assert len(asyncio.run(m._handle_list_tools())) == len(m.EXPOSED_TOOLS)      # 静态兜底
    assert len(asyncio.run(m._handle_list_tools())) == len(m.EXPOSED_TOOLS)      # 下次重试拿到 schema
    assert calls["n"] == 2


def test_daily_tools_schemas(monkeypatch):
    async def _stub():
        return [_tool(n) for n in ALL_UPSTREAM_NAMES]
    monkeypatch.setattr(m, "_upstream_list_tools", _stub)
    by_name = {t.name: t for t in asyncio.run(m._handle_list_tools())}
    clear = by_name["daily_clear"].inputSchema
    assert clear["required"] == ["account_ids"]
    assert clear["properties"]["confirm"]["default"] is False
    assert clear["properties"]["account_ids"]["type"] == "array"
    assert clear["additionalProperties"] is False
    assert "daily_gap" in by_name and by_name["daily_gap"].inputSchema["additionalProperties"] is False


@pytest.mark.parametrize("name", list(m.FORBIDDEN_UPSTREAM))
def test_hidden_tool_call_denied_with_zero_upstream(upstream, name):
    fake = upstream()
    res = asyncio.run(m._handle_call_tool(name, {"account_ids": ["A1"]}))
    assert res.isError is True
    d = _payload(res)
    assert d["ok"] is False and d["upstream_calls"] == 0 and d["tool"] == name
    assert fake.calls == [], "白名单外工具必须零上游调用，实际=%r" % (fake.calls,)


def test_unknown_tool_denied():
    res = asyncio.run(m._handle_call_tool("totally_unknown_tool", {}))
    assert res.isError is True and "白名单" in _text(res)


@pytest.mark.parametrize("name", list(m.READONLY_WHITELIST))
def test_each_readonly_tool_passes_the_gate(monkeypatch, name):
    calls = []

    async def _stub(tool, arguments):
        calls.append((tool, arguments))
        return _ok({"ok": True, "tool": tool})

    monkeypatch.setattr(m, "_upstream_call_tool", _stub)
    res = asyncio.run(m._handle_call_tool(name, {}))
    assert res.isError is False and _payload(res)["tool"] == name     # 直接返回上游 JSON 对象
    assert calls == [(name, None)]


def test_readonly_non_json_result_is_wrapped(monkeypatch):
    async def _stub(tool, arguments):
        return types.CallToolResult(content=[types.TextContent(type="text", text="plain text: OK")],
                                    isError=False)
    monkeypatch.setattr(m, "_upstream_call_tool", _stub)
    d = _payload(asyncio.run(m._handle_call_tool("health", {})))
    assert d["content"][0]["text"] == "plain text: OK" and d["isError"] is False


# ── 2) 默认预演：绝不发车（核心证据）────────────────────────────────

def test_preview_default_sends_zero_write_requests(upstream):
    """默认路径：无写请求、只走只读白名单、回显将要提交的载荷。"""
    fake = upstream(rows=[_row("A1")])
    res = _clear({"account_ids": ["A1"]})
    d = _payload(res)
    assert res.isError is False
    assert d["confirm"] is False and d["dry_run"] is True
    assert d["dispatched"] is False and d["account_ids"] == []
    assert d["will_call"] == {"tool": "smart_dispatch",
                              "arguments": {"account_ids": ["A1"], "include_anni": True, "only_anni": False}}
    assert fake.write_calls == [], "默认预演绝不能调用 smart_dispatch，实际=%r" % (fake.write_calls,)
    assert sorted(fake.read_names) == ["account_status", "dashboard", "mas_status", "queue"]
    assert "upstream" not in d, "预演不得产生上游写结果"
    assert d["candidate_account_ids"] == ["A1"] and d["will_call"]["arguments"]["account_ids"] == ["A1"]
    assert '"dispatched": true' not in _text(res)


def test_preview_missing_confirm_key_is_preview(upstream):
    fake = upstream()
    d = _payload(_clear({"account_ids": ["A1"]}))
    assert d["confirm"] is False and d["dispatched"] is False and fake.write_calls == []


@pytest.mark.parametrize("bad", ["true", "1", 1, None, {}, "True"])
def test_non_bool_confirm_stays_preview(upstream, bad):
    """只有真正的布尔 true 才算确认；字符串/数字一律按预演（fail-closed）。"""
    fake = upstream()
    d = _payload(_clear({"account_ids": ["A1"], "confirm": bad}))
    assert d["confirm"] is False and d["dispatched"] is False
    assert fake.write_calls == []
    assert any("confirm" in w for w in d["warnings"])


def test_preview_reports_would_refuse(upstream):
    """预演时把"若确认会被什么门禁拦住"提前说清楚。"""
    upstream(queue={"pending": [], "active": [{"id": "x"}], "pending_count": 0,
                    "active_count": 1, "paused": False})
    d = _payload(_clear({"account_ids": ["A1"]}))
    assert d["dispatched"] is False and d["would_refuse"] == m.R_BATCH


def test_preview_payload_has_no_token(upstream):
    upstream(mas=_clean_mas(today={"date": _TODAY, "token": _FAKE_TOKEN, "api_token": _FAKE_TOKEN}))
    res = _clear({"account_ids": ["A1"]})
    assert _FAKE_TOKEN not in _text(res)
    assert m.PLACEHOLDER in _text(res)


def test_readonly_forward_is_scrubbed(upstream):
    fake = upstream()
    async def _res(n, a):
        fake.calls.append({"name": n, "arguments": a})
        return _ok({"ok": True, "Info": {"Password": "REAL-PW", "SklandToken": "REAL-TK", "Name": "n"}})
    monkeypatch_ok = asyncio.run(m._handle_call_tool("dashboard", {}))
    d = _payload(monkeypatch_ok)
    info = d["structuredContent"] if "structuredContent" in d else d
    assert "REAL-PW" not in _text(monkeypatch_ok) and "REAL-TK" not in _text(monkeypatch_ok)


# ── 3) confirm=True：门禁 ───────────────────────────────────────────

def test_confirm_true_forwards_exactly_once_with_gap_only(upstream):
    fake = upstream(rows=[_row("A1", "gap-acct"), _row("A2", "done-acct", pct=5.0, done_ts=_TODAY + " 06:00:00")])
    res = _clear({"account_ids": ["A1", "A2"], "confirm": True})
    d = _payload(res)
    assert res.isError is False and d["dispatched"] is True and d["dry_run"] is False
    assert d["account_ids"] == ["A1"]                                # 今日已清的 A2 被剔除
    assert d["precheck"]["skipped_done"] == ["A2"]
    assert [c["arguments"] for c in fake.write_calls] == [
        {"account_ids": ["A1"], "include_anni": True, "only_anni": False}]
    assert len(fake.write_calls) == 1
    assert d["upstream"]["tool"] == m.UPSTREAM_WRITE_TOOL and d["upstream"]["result"]["isError"] is False


def test_confirm_true_upstream_result_is_scrubbed(upstream):
    fake = upstream(rows=[_row("A1")],
                    write={"ok": True, "Info": {"Password": "REAL-PW", "SklandToken": "REAL-TK"}})
    res = _clear({"account_ids": ["A1"], "confirm": True})
    assert "REAL-PW" not in _text(res) and "REAL-TK" not in _text(res)
    assert m.PLACEHOLDER in _text(res)
    up = _payload(res)["upstream"]["result"]
    inner = json.loads(up["content"][0]["text"])
    assert inner["Info"]["Password"] == m.PLACEHOLDER and inner["Info"]["SklandToken"] == m.PLACEHOLDER


def test_confirm_true_matches_by_account_name_and_submits_id(upstream):
    fake = upstream(rows=[_row("A1", "深海猎人")])
    d = _payload(_clear({"account_ids": ["深海猎人"], "confirm": True}))
    assert d["dispatched"] is True
    assert fake.write_calls[0]["arguments"]["account_ids"] == ["A1"]


def test_too_many_accounts_refused(upstream):
    fake = upstream(rows=[_row("A%d" % i, index=i) for i in range(1, 5)])
    d = _payload(_clear({"account_ids": ["A1", "A2", "A3", "A4"], "confirm": True}))
    assert d["refused"] == m.R_TOO_MANY and d["dispatched"] is False and d["account_ids"] == []
    assert fake.write_calls == []


def test_max_accounts_env_override(upstream, monkeypatch):
    monkeypatch.setenv(m.MAX_ACCOUNTS_ENV, "1")
    fake = upstream(rows=[_row("A1"), _row("A2", index=1)])
    d = _payload(_clear({"account_ids": ["A1", "A2"], "confirm": True}))
    assert d["refused"] == m.R_TOO_MANY and fake.write_calls == []


def test_three_accounts_allowed_by_default(upstream):
    fake = upstream(rows=[_row("A%d" % i, index=i) for i in (1, 2, 3)])
    d = _payload(_clear({"account_ids": ["A1", "A2", "A3"], "confirm": True}))
    assert d["dispatched"] is True and d["account_ids"] == ["A1", "A2", "A3"]
    assert len(fake.write_calls) == 1


@pytest.mark.parametrize("kw", [
    {"queue": {"pending": [], "active": [{"id": "x"}], "active_count": 1, "pending_count": 0, "paused": False}},
    {"rows": [_row("A1", running=True)]},
    {"rows": [_row("A1", queued=True)]},
    {"dashboard": {"ok": True, "capacity": {"running": 2}}},
    {"mas": _clean_mas(rerun={"running": True, "accounts": ["x"]})},
])
def test_batch_running_refused(upstream, kw):
    fake = upstream(**kw)
    d = _payload(_clear({"account_ids": ["A1"], "confirm": True}))
    assert d["refused"] == m.R_BATCH and d["dispatched"] is False
    assert d["precheck"]["batch"]["running"] is True and d["precheck"]["batch"]["evidence"]
    assert fake.write_calls == [], "批次在跑时绝不能发车"


def test_preview_still_reports_batch_evidence(upstream):
    upstream(queue={"pending": [], "active": [{"id": "x"}], "active_count": 1, "pending_count": 0,
                    "paused": False})
    d = _payload(_clear({"account_ids": ["A1"]}))
    assert d["precheck"]["batch"]["evidence"] and d["dispatched"] is False


def test_no_gap_refused(upstream):
    fake = upstream(rows=[_row("A1", "a", pct=5.0, done_ts=_TODAY + " 07:10:00"),
                          _row("A2", "b", index=1, pct=None, done_ts=_TODAY + " 07:20:00")])
    d = _payload(_clear({"account_ids": ["A1"], "confirm": True}))
    assert d["refused"] == m.R_NO_GAP and d["dispatched"] is False and fake.write_calls == []
    assert d["precheck"]["skipped_done"] == ["A1"]


def test_yesterday_done_is_still_a_gap(upstream):
    """昨晚 23:59 清完不算今天 —— 判定必须带"今天"条件（同 api_fastapi.py:852-855）。"""
    fake = upstream(rows=[_row("A1", pct=5.0, done_ts="2020-01-01 23:59:00")])
    d = _payload(_clear({"account_ids": ["A1"], "confirm": True}))
    assert d["dispatched"] is True and len(fake.write_calls) == 1
    assert d["precheck"]["accounts"][0]["verdict"] == "gap"


def test_unknown_account_refused(upstream):
    fake = upstream(rows=[_row("A1")])
    d = _payload(_clear({"account_ids": ["NOT-AN-ACCOUNT"], "confirm": True}))
    assert d["refused"] == m.R_UNKNOWN and d["dispatched"] is False and fake.write_calls == []
    assert d["precheck"]["unknown_account_ids"] == ["NOT-AN-ACCOUNT"]


def test_empty_account_list_is_error(upstream):
    fake = upstream()
    d = _payload(_clear({"account_ids": []}))
    assert d["refused"] == m.R_BAD_ARGS and d["dispatched"] is False
    assert fake.calls == [], "参数错误应在触达上游之前返回"


@pytest.mark.parametrize("args", [
    {"account_ids": "A1,A2"},
    {"account_ids": [1]},
    {"account_ids": ["  "]},
    {"account_ids": ["A1"], "unknown_key": 1},
    "not-a-dict",
])
def test_bad_arguments_error(upstream, args):
    fake = upstream()
    d = _payload(_clear(args))
    assert d["refused"] == m.R_BAD_ARGS and fake.calls == []


@pytest.mark.parametrize("fail", [("account_status",), ("queue",), ("mas_status",)])
def test_readonly_unavailable_fails_closed(upstream, fail):
    fake = upstream(fail=fail)
    d = _payload(_clear({"account_ids": ["A1"], "confirm": True}))
    assert d["refused"] == m.R_READONLY and d["dispatched"] is False and fake.write_calls == []
    assert d["precheck"]["batch_state_known"] is False


def test_mas_degraded_fails_closed(upstream):
    """MAS 主机快照不可用 ⇒ 无法确认 AUTO-MAS 侧在跑记录 ⇒ fail-closed。"""
    fake = upstream(mas={"ok": True, "host": {"ok": False, "error": "ssh unreachable"},
                         "rerun": {"running": False}, "today": {}})
    d = _payload(_clear({"account_ids": ["A1"], "confirm": True}))
    assert d["refused"] == m.R_READONLY and fake.write_calls == []


def test_preview_works_even_if_reads_fail(upstream):
    upstream(fail=("account_status", "queue", "mas_status", "dashboard"))
    res = _clear({"account_ids": ["A1"]})
    d = _payload(res)
    assert res.isError is False and d["dispatched"] is False
    assert d["precheck"]["errors"] and d["would_refuse"] == m.R_READONLY


def test_dispatch_kill_switch(upstream, monkeypatch):
    fake = upstream()
    monkeypatch.setenv(m.NO_DISPATCH_ENV, "1")
    d = _payload(_clear({"account_ids": ["A1"], "confirm": True}))
    assert d["refused"] == m.R_DISABLED and d["dispatched"] is False and fake.write_calls == []


@pytest.mark.parametrize("val,expect_disabled", [
    ("1", True), ("true", True), ("YES", True), ("on", True), ("enabled", True),
    ("0", False), ("false", False), ("no", False), ("off", False), ("", False),
    ("maybe", True), ("flase", True),               # 非法非空 -> fail-closed 禁发车
])
def test_disabled_switch_semantics(monkeypatch, val, expect_disabled):
    monkeypatch.setenv(m.NO_DISPATCH_ENV, val)
    assert m.dispatch_disabled() is expect_disabled, val


def test_strict_mas_switch(upstream, monkeypatch):
    fake = upstream(mas=_clean_mas(host={"ok": True, "maa_processes": 7}))
    d1 = _payload(_clear({"account_ids": ["A1"], "confirm": True}))
    assert d1["refused"] is None and d1["dispatched"] is True          # 缺省：远端 MAA 进程不算硬闸
    assert any("MAA 进程" in w for w in d1["warnings"])
    fake2 = upstream(mas=_clean_mas(host={"ok": True, "maa_processes": 7}))
    monkeypatch.setenv(m.STRICT_MAS_ENV, "1")
    d2 = _payload(_clear({"account_ids": ["A1"], "confirm": True}))
    assert d2["refused"] == m.R_BATCH and fake2.write_calls == []


# ── 4) daily_gap：只读、永不写 ──────────────────────────────────────

def test_daily_gap_lists_gaps_and_never_writes(upstream):
    fake = upstream(rows=[_row("A1", "未清"), _row("A2", "已清", index=1, pct=5.0,
                                                   done_ts=_TODAY + " 06:00:00")])
    d = _payload(_gap())
    assert d["tool"] == m.GAP_TOOL
    assert [g["id"] for g in d["gaps"]] == ["A1"]
    assert [g["id"] for g in d["done_today"]] == ["A2"]
    assert d["batch"]["running"] is False
    assert d["dispatchable_account_ids"] == ["A1"]
    assert d["gaps"][0]["evidence"] and d["gaps"][0]["verdict"] == "gap"   # 含判定依据字段
    assert fake.write_calls == [], "daily_gap 永不写"
    assert sorted(fake.read_names) == ["account_status", "dashboard", "mas_status", "queue"]


def test_daily_gap_running_account_is_busy_not_gap(upstream):
    fake = upstream(rows=[_row("A1", "在跑", running=True), _row("A2", "缺口", index=1)])
    d = _payload(_gap())
    assert [b["id"] for b in d["busy"]] == ["A1"]
    assert [g["id"] for g in d["gaps"]] == ["A2"]
    assert d["batch"]["running"] is True and d["dispatchable_account_ids"] == []
    assert fake.write_calls == []


def test_daily_gap_has_no_dispatchable_when_batch_running(upstream):
    fake = upstream(rows=[_row("A1")],
                    queue={"pending": [], "active": [{"id": "x"}], "active_count": 1,
                           "pending_count": 0, "paused": False})
    d = _payload(_gap())
    assert d["batch"]["running"] is True
    assert d["gaps"] and d["dispatchable_account_ids"] == []
    assert fake.write_calls == []


def test_daily_gap_degraded_when_reads_fail(upstream):
    upstream(fail=("account_status",))
    d = _payload(_gap())
    assert d["ok"] is False and d["degraded"] is True and d["dispatchable_account_ids"] == []


def test_daily_gap_rejects_unknown_args(upstream):
    fake = upstream()
    d = _payload(_gap({"nope": 1}))
    assert d.get("ok") is False and fake.calls == []


# ── 5) mas_rerun：原样转发（task-10）────────────────────────────────

def test_mas_rerun_default_is_preview_and_forwards_dry_run_true(upstream):
    """缺省 dry_run ⇒ 上游语义 True ⇒ 只预演：转发原样、零副作用、返回原样。"""
    fake = upstream()
    res = asyncio.run(m._handle_call_tool(m.RERUN_TOOL, {"accounts": ["A1", "A2"]}))
    assert res.isError is False
    d = _payload(res)
    assert fake.calls == [{"name": m.RERUN_TOOL, "arguments": {"dry_run": True, "accounts": ["A1", "A2"]}}]
    assert d["dry_run"] is True
    assert d["would_call"]["body"] == {"accounts": ["A1", "A2"], "dry_run": True}
    assert "started" not in d                       # 预演不产生任何下发标记
    assert fake.write_calls == []                   # 绝不落到 smart_dispatch


def test_mas_rerun_accounts_null_is_omitted_so_upstream_resolves(upstream):
    """accounts 缺省/null 原样透传（由上游按主机快照的失败号解析候选），不塞空列表。"""
    fake = upstream()
    for args in ({}, {"accounts": None}, {"accounts": []}):
        d = _payload(asyncio.run(m._handle_call_tool(m.RERUN_TOOL, args)))
        assert d["dry_run"] is True
    assert [c["arguments"] for c in fake.calls] == [{"dry_run": True}, {"dry_run": True},
                                                    {"dry_run": True, "accounts": []}]


def test_mas_rerun_state_is_readonly_forward(upstream):
    fake = upstream(rerun_state={"ok": True, "running": True, "accounts": ["X"], "started_at": "t"})
    res = asyncio.run(m._handle_call_tool(m.RERUN_STATE_TOOL, {}))
    d = _payload(res)
    assert res.isError is False and d["running"] is True
    assert [c["name"] for c in fake.calls] == [m.RERUN_STATE_TOOL]
    assert fake.write_calls == [] and not ({"mas_rerun"} & {c["name"] for c in fake.calls})


def test_mas_rerun_dry_run_false_is_forwarded_verbatim(upstream):
    """不替调用方改 dry_run：False 原样转发（上游才会真下发）。"""
    fake = upstream()
    res = asyncio.run(m._handle_call_tool(m.RERUN_TOOL, {"accounts": ["A1"], "dry_run": False}))
    d = _payload(res)
    assert res.isError is False
    assert fake.calls[0]["arguments"] == {"dry_run": False, "accounts": ["A1"]}
    assert d["dry_run"] is False and d["started"] is True


def test_mas_rerun_kill_switch_blocks_only_real_dispatch(upstream, monkeypatch):
    """MAAORCH_DAILY_NO_DISPATCH：只拦 dry_run=False 的真下发，不拦预演。"""
    monkeypatch.setenv(m.NO_DISPATCH_ENV, "1")
    fake = upstream()
    prev = asyncio.run(m._handle_call_tool(m.RERUN_TOOL, {"accounts": ["A1"], "dry_run": True}))
    assert prev.isError is False and _payload(prev)["dry_run"] is True
    blocked = asyncio.run(m._handle_call_tool(m.RERUN_TOOL, {"accounts": ["A1"], "dry_run": False}))
    d = _payload(blocked)
    assert blocked.isError is True and d["refused"] == m.R_DISABLED
    assert d["dispatched"] is False and d["upstream_calls"] == 0
    assert [c["name"] for c in fake.calls] == [m.RERUN_TOOL]      # 只有预演那次到了上游


def test_mas_rerun_rejects_more_than_8_accounts(upstream):
    """单批上限 8（上游 mas_adapter 同样只取前 8 个）：超限本地拒绝，零上游请求。"""
    fake = upstream()
    assert m.MAX_RERUN_ACCOUNTS == 8
    nine = ["A%d" % i for i in range(9)]
    res = asyncio.run(m._handle_call_tool(m.RERUN_TOOL, {"accounts": nine, "dry_run": True}))
    d = _payload(res)
    assert res.isError is True and d["refused"] == m.R_TOO_MANY
    assert d["upstream_calls"] == 0 and d["accounts_count"] == 9
    assert fake.calls == [], "超上限必须在本地拒绝，零上游请求"
    eight = ["A%d" % i for i in range(8)]
    ok = asyncio.run(m._handle_call_tool(m.RERUN_TOOL, {"accounts": eight, "dry_run": True}))
    assert ok.isError is False and fake.calls[0]["arguments"]["accounts"] == eight


def test_mas_rerun_fail_closed_when_upstream_unreachable(monkeypatch):
    """上游不可达（端口关 + 禁自启）：结构化报错，不挂死、不假装已发车。"""
    monkeypatch.setattr(m, "port_open", lambda *a, **k: False)
    monkeypatch.setenv(m.NO_AUTOSTART_ENV, "1")
    res = asyncio.run(m._handle_call_tool(m.RERUN_TOOL, {"accounts": ["A1"], "dry_run": True}))
    d = _payload(res)
    assert res.isError is True and d["ok"] is False
    assert d["dispatched"] is False and d["upstream_calls"] == 0 and d["error"]


def test_mas_rerun_upstream_call_failure_is_structured(upstream):
    fake = upstream(fail=(m.RERUN_TOOL,))
    res = asyncio.run(m._handle_call_tool(m.RERUN_TOOL, {"accounts": ["A1"], "dry_run": False}))
    d = _payload(res)
    assert res.isError is True and d["ok"] is False and d["dispatched"] is False
    assert d["dispatch_unknown"] is True          # 真下发意图 + 调用失败 ⇒ 明确说"是否已下发未知"
    assert "TEST-TOKEN" not in _text(res)
    assert len(fake.calls) == 1


def test_mas_rerun_bad_arguments_are_rejected_with_zero_upstream(upstream):
    fake = upstream()
    for args in ({"nope": 1}, {"accounts": "A1,A2"}, {"accounts": [1]}, {"accounts": [""]},
                 {"accounts": ["A1"], "dry_run": True, "confirm": True}, ["not-a-dict"]):
        res = asyncio.run(m._handle_call_tool(m.RERUN_TOOL, args))
        d = _payload(res)
        assert res.isError is True and d["refused"] == m.R_BAD_ARGS, args
        assert d["upstream_calls"] == 0
    assert fake.calls == []


def test_mas_rerun_non_bool_dry_run_stays_preview(upstream):
    """非布尔 dry_run 一律 fail-closed 按预演处理（绝不把 "false"/0 当成真下发意图）。"""
    fake = upstream()
    for bad in ("false", "true", 0, 1, None, {}):
        d = _payload(asyncio.run(m._handle_call_tool(m.RERUN_TOOL, {"accounts": ["A1"], "dry_run": bad})))
        assert d["dry_run"] is True, bad
    assert [c["arguments"]["dry_run"] for c in fake.calls] == [True] * 6


def test_mas_rerun_accounts_order_and_dedupe(upstream):
    fake = upstream()
    asyncio.run(m._handle_call_tool(m.RERUN_TOOL, {"accounts": ["B", " A ", "B"], "dry_run": True}))
    assert fake.calls[0]["arguments"]["accounts"] == ["B", "A"]   # 去重 + 保序 + strip


def test_mas_rerun_audit_log_line(upstream, capsys):
    """审计日志：工具名 + accounts 数量 + dry_run 值 + 是否真下发（含拒绝）。"""
    upstream()
    asyncio.run(m._handle_call_tool(m.RERUN_TOOL, {"accounts": ["A1", "A2"]}))       # 缺省预演
    err = capsys.readouterr().err
    assert "[audit] tool=mas_rerun accounts=2 dry_run=True dispatched=False" in err
    asyncio.run(m._handle_call_tool(m.RERUN_TOOL, {"accounts": ["A1"], "dry_run": False}))
    err = capsys.readouterr().err
    assert "[audit] tool=mas_rerun accounts=1 dry_run=False dispatched=True" in err
    asyncio.run(m._handle_call_tool(m.RERUN_TOOL, {"accounts": ["A%d" % i for i in range(9)]}))
    err = capsys.readouterr().err
    assert "[audit] tool=mas_rerun accounts=9 dry_run=True dispatched=False refused=too_many_accounts" in err


def test_mas_rerun_result_is_scrubbed(upstream):
    fake = upstream(rerun_state={})
    fake.rerun_state = {"ok": True, "running": False, "Token": "SYNTH-RERUN-TOKEN", "detail": "x"}
    d = _payload(asyncio.run(m._handle_call_tool(m.RERUN_STATE_TOOL, {})))
    assert d["Token"] == m.PLACEHOLDER and "SYNTH-RERUN-TOKEN" not in json.dumps(d)


def test_mas_rerun_schema_is_forwarded_verbatim(monkeypatch):
    """Hermes 看到的是**上游真签名**（dry_run 缺省 true），本代理不改 schema。"""
    schema = {"type": "object",
              "properties": {"accounts": {"anyOf": [{"items": {"type": "string"}, "type": "array"},
                                                   {"type": "null"}], "default": None},
                             "dry_run": {"default": True, "type": "boolean"}},
              "title": "mas_rerun"}
    async def _stub():
        return [types.Tool(name=n, description="d", inputSchema=dict(schema)) for n in ALL_UPSTREAM_NAMES]
    monkeypatch.setattr(m, "_upstream_list_tools", _stub)
    by_name = {t.name: t for t in asyncio.run(m._handle_list_tools())}
    assert by_name["mas_rerun"].inputSchema["properties"]["dry_run"]["default"] is True
    assert "anyOf" in by_name["mas_rerun"].inputSchema["properties"]["accounts"]
    assert by_name["mas_rerun_state"].inputSchema["properties"] == schema["properties"]


def test_exposed_tools_have_no_output_schema(monkeypatch):
    """上游工具带 outputSchema 时也必须剥掉（保留入参契约）。

    本代理是脱敏/重排层，声明了上游出参 schema 会让 MCP 客户端强校验 structuredContent
    并直接 RuntimeError（mcp/client/session.py:429-439）；实测 mas_rerun_state 曾因此炸掉。
    """
    out_schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

    async def _stub():
        return [types.Tool(name=n, description="d", inputSchema={"type": "object"},
                           outputSchema=dict(out_schema)) for n in ALL_UPSTREAM_NAMES]
    monkeypatch.setattr(m, "_upstream_list_tools", _stub)
    got = asyncio.run(m._handle_list_tools())
    assert len(got) == len(m.EXPOSED_TOOLS)
    assert all(getattr(t, "outputSchema", None) is None for t in got)
    assert all(t.inputSchema is not None for t in got)          # 入参契约保留
    assert all(t.description for t in got)


def test_upstream_mas_rerun_contract_pin():
    """钉住上游契约（本代理依赖它）：缺省 dry_run=True，且 dry_run 经 _gate() 只预演。"""
    src = (ROOT / "network" / "mcp_server.py").read_text(encoding="utf-8")
    assert re.search(r"def mas_rerun\(accounts: list\[str\] \| None = None, dry_run: bool = True\)", src)
    assert "mas_adapter.rerun(accounts=accounts, dry_run=bool(dry_run))" in src
    gate = re.search(r"def _gate\(.*?\n    def ", src, re.S)
    assert gate and "if not dry_run:" in gate.group(0) and '"dry_run": True' in gate.group(0)


# ── 6) 脱敏与 token ─────────────────────────────────────────────────

def test_scrub_recursive_and_text(monkeypatch):
    monkeypatch.setenv(m.TOKEN_ENV, _FAKE_TOKEN)
    out = m.scrub({"Info": {"Password": "P", "SklandToken": "T", "deep": [{"api_token": "X"}], "Name": "n"}})
    assert out["Info"]["Password"] == m.PLACEHOLDER
    assert out["Info"]["SklandToken"] == m.PLACEHOLDER
    assert out["Info"]["deep"][0]["api_token"] == m.PLACEHOLDER
    assert out["Info"]["Name"] == "n"
    assert _FAKE_TOKEN not in m.scrub_text("prefix " + _FAKE_TOKEN + " suffix")
    assert m.PLACEHOLDER in m.scrub_text('HTTP 500 {"Password":"SYNTH-PW"}')
    assert "SYNTH-PW" not in m.scrub_text('HTTP 500 {"Password":"SYNTH-PW"}')


def test_redact_hides_token_value(monkeypatch):
    monkeypatch.setenv(m.TOKEN_ENV, _FAKE_TOKEN)
    assert _FAKE_TOKEN not in m._redact("boom " + _FAKE_TOKEN)
    assert m.PLACEHOLDER in m._redact("boom " + _FAKE_TOKEN)


# ── 7) 按需拉起上游（不真起进程）─────────────────────────────────────

def test_autostart_disabled_reports_structured_error(monkeypatch):
    monkeypatch.setattr(m, "port_open", lambda *a, **k: False)
    monkeypatch.setenv(m.NO_AUTOSTART_ENV, "1")
    def _no_popen(*a, **k):
        raise AssertionError("禁用自动拉起时不得 Popen")
    monkeypatch.setattr(m.subprocess, "Popen", _no_popen)
    err = m._ensure_upstream_sync()
    assert isinstance(err, dict) and m.NO_AUTOSTART_ENV in err["error"]


def test_autostart_command_shape(monkeypatch, tmp_path):
    """上游没在监听 ⇒ 用 Python312 后台跑 main_web.pyw，日志追加 web_ui.log，超时结构化报错。"""
    calls = {}
    class _FakePopen:
        def __init__(self, cmd, **kw):
            calls["cmd"] = cmd
            calls["kw"] = kw
    monkeypatch.setattr(m, "port_open", lambda *a, **k: False)
    monkeypatch.setattr(m.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(m, "UPSTREAM_LOG", str(tmp_path / "web_ui.log"))
    monkeypatch.setenv(m.START_TIMEOUT_ENV, "0")
    err = m._ensure_upstream_sync()
    assert isinstance(err, dict) and "不可达" in err["error"]
    assert calls["cmd"][1] == m.UPSTREAM_ENTRY
    assert calls["cmd"][0].endswith(("python.exe", "python", "python3")) or calls["cmd"][0] == sys.executable
    assert calls["kw"]["cwd"] == m.REPO_ROOT
    assert str(tmp_path) in m.UPSTREAM_LOG


def test_autostart_refused_for_non_loopback(monkeypatch):
    monkeypatch.setattr(m, "port_open", lambda *a, **k: False)
    monkeypatch.setenv(m.URL_ENV, "http://10.1.2.3:19997/mcp/")
    def _no_popen(*a, **k):
        raise AssertionError("非本机上游不得 Popen")
    monkeypatch.setattr(m.subprocess, "Popen", _no_popen)
    err = m._ensure_upstream_sync()
    assert isinstance(err, dict) and "不在本机" in err["error"]


def test_upstream_url_forces_trailing_slash(monkeypatch):
    monkeypatch.setenv(m.URL_ENV, "http://127.0.0.1:19997/mcp")
    assert m.upstream_url() == "http://127.0.0.1:19997/mcp/"
    assert m.upstream_host_port() == ("127.0.0.1", 19997)


def test_parse_rpc_handles_sse_and_json():
    sse = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
    assert m.parse_rpc(sse)["result"]["ok"] is True
    assert m.parse_rpc('{"jsonrpc":"2.0","id":2,"result":{"ok":false}}')["id"] == 2
    assert m.parse_rpc("") == {}


# ── 8) 真 HTTP 传输 × 本机 stub（尾斜杠 / 头 / SSE 解析）──────────────

class _StubHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list = []

    def log_message(self, *a):                                  # 静音
        pass

    def _sse(self, obj, extra=None):
        raw = ("event: message\ndata: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def do_DELETE(self):
        type(self).seen.append({"method": "DELETE", "path": self.path})
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        body = json.loads(raw or b"{}")
        type(self).seen.append({"method": "POST", "path": self.path,
                                "headers": {k.lower(): v for k, v in self.headers.items()},
                                "body": body})
        if self.path != "/mcp/":                                # 尾斜杠写错必须暴露出来
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        method = body.get("method")
        if method == "initialize":
            self._sse({"jsonrpc": "2.0", "id": body.get("id"),
                       "result": {"protocolVersion": "2024-11-05", "capabilities": {},
                                  "serverInfo": {"name": "stub", "version": "0"}}},
                      extra={"mcp-session-id": "stub-session"})
        elif method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif method == "tools/list":
            tools = [{"name": n, "description": "stub", "inputSchema": {"type": "object", "properties": {}}}
                     for n in ALL_UPSTREAM_NAMES]
            self._sse({"jsonrpc": "2.0", "id": body.get("id"), "result": {"tools": tools}})
        elif method == "tools/call":
            params = body.get("params") or {}
            res = {"content": [{"type": "text",
                                "text": json.dumps({"ok": True, "tool": params.get("name"),
                                                    "arguments": params.get("arguments")},
                                                   ensure_ascii=False)}],
                   "isError": False}
            self._sse({"jsonrpc": "2.0", "id": body.get("id"), "result": res})
        else:
            self._sse({"jsonrpc": "2.0", "id": body.get("id"),
                       "error": {"code": -32601, "message": "method not found"}})


@pytest.fixture
def stub_upstream(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    _StubHandler.seen = []
    monkeypatch.setattr(m, "_rpc_sync", _REAL_RPC)              # 恢复真 HTTP 客户端
    monkeypatch.setenv(m.URL_ENV, "http://127.0.0.1:%d/mcp/" % srv.server_address[1])
    yield srv
    srv.shutdown()
    srv.server_close()


def test_http_wire_protocol_trailing_slash_and_headers(stub_upstream):
    tools_ = asyncio.run(m._upstream_list_tools())
    assert {t.name for t in tools_} == set(ALL_UPSTREAM_NAMES)
    posts = [s for s in _StubHandler.seen if s["method"] == "POST"]
    assert posts and all(s["path"] == "/mcp/" for s in posts)
    init = [s for s in posts if s["body"].get("method") == "initialize"][0]
    assert init["headers"].get("x-agent-token") == _FAKE_TOKEN          # 鉴权头带上了
    assert "text/event-stream" in init["headers"].get("accept", "")     # SSE 协商
    assert init["headers"].get("content-type", "").startswith("application/json")
    called = asyncio.run(m._upstream_call_tool("dashboard", {}))
    assert m._result_json(called)["tool"] == "dashboard"
    names = [s["body"].get("params", {}).get("name") for s in _StubHandler.seen
             if s["method"] == "POST" and s["body"].get("method") == "tools/call"]
    assert names == ["dashboard"]
    assert all(s["path"] == "/mcp/" for s in _StubHandler.seen if s["method"] == "POST")


def test_http_stub_preview_sends_no_write_over_the_wire(stub_upstream):
    res = asyncio.run(m._handle_call_tool(m.DAILY_TOOL, {"account_ids": ["acc-1"]}))
    d = _payload(res)
    wire_calls = [s["body"].get("params", {}).get("name") for s in _StubHandler.seen
                  if s["method"] == "POST" and s["body"].get("method") == "tools/call"]
    assert m.UPSTREAM_WRITE_TOOL not in wire_calls, "默认预演在传输层就不能出现写调用"
    assert sorted(wire_calls) == ["account_status", "dashboard", "mas_status", "queue"]
    assert d["dispatched"] is False and d["account_ids"] == []
    assert d["will_call"]["arguments"]["account_ids"] == []      # 假上游账号清单为空 ⇒ 无候选


def test_http_stub_confirm_blocked_by_kill_switch(stub_upstream, monkeypatch):
    monkeypatch.setenv(m.NO_DISPATCH_ENV, "1")
    res = asyncio.run(m._handle_call_tool(m.DAILY_TOOL, {"account_ids": ["acc-1"], "confirm": True}))
    d = _payload(res)
    assert res.isError is True and d["refused"] == m.R_DISABLED and d["dispatched"] is False
    wire_calls = [s["body"].get("params", {}).get("name") for s in _StubHandler.seen
                  if s["method"] == "POST" and s["body"].get("method") == "tools/call"]
    assert m.UPSTREAM_WRITE_TOOL not in wire_calls


# ── 9) 源码静态断言（防回归）────────────────────────────────────────

def test_source_has_single_guarded_write_path():
    src = PROXY.read_text(encoding="utf-8")
    assert src.count("_upstream_call_tool(UPSTREAM_WRITE_TOOL") == 1, "写调用只能有一处（daily_clear 门禁之后）"
    assert re.search(r"confirm is not True", src), "确认必须用布尔身份判断（confirm is not True）"
    assert not re.search(r"if not confirm\b", src), "禁止用真值判断决定是否发车"
    assert not re.search(r"if confirm\s*:", src), "禁止 if confirm: 这种真值判断"
    assert "MAX_ACCOUNTS_PER_CALL = 3" in src
    assert "NO_DISPATCH_ENV = \"MAAORCH_DAILY_NO_DISPATCH\"" in src
    assert "未设置/空串" in src, "NO_DISPATCH 的空串语义必须写清楚"


def test_source_never_prints_token():
    src = PROXY.read_text(encoding="utf-8")
    assert not re.search(r"print\([^)]*api_token\(\)", src), "禁止把 token 打进日志"
    assert "x-agent-token" in src
    assert _FAKE_TOKEN not in src and "api_token" in src      # 只出现键名，不出现真值


def test_source_refusal_codes_present():
    src = PROXY.read_text(encoding="utf-8")
    for code in ('"too_many_accounts"', '"dispatch_disabled"', '"readonly_unavailable"',
                 '"batch_running"', '"no_gap"', '"unknown_account"'):
        assert code in src, code
