# -*- coding: utf-8 -*-
"""task-31：MAS 脱敏代理的**只读硬闸**（MAAORCH_MAS_READONLY）离线测试。

被测对象：tools/mas_scrub_proxy.py
硬性要求（task-31）：
  1) 只读模式必须**同时**过滤 tools/list，并在 tools/call **再校验一次**
     （即使调用者知道被隐藏的工具名也拒绝）；
  2) 用**精确工具名** allowlist，不用 get_* 泛匹配；
  3) 拒绝必须发生在**触达上游之前**（拒绝 = 零上游调用）；
  4) 默认（不设 env）行为与以前完全一致（透明转发，人工会话不受影响）；
  5) 非法 env 取值 **fail-closed** 到只读；
  6) 不改 scrub 语义（允许的只读工具返回照样脱敏）。

本测试**完全离线**：autouse fixture 把两个上游函数换成"网络绊线"（一碰就爆），
只有显式打桩的用例才能通过，因此任何意外的真实请求都会立刻让用例失败。

运行：
    python -m pytest tests/test_mas_proxy_readonly.py -q
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib
import sys

import pytest

import mcp.types as types

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROXY = ROOT / "tools" / "mas_scrub_proxy.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def proxy():
    """按路径加载被测模块（tools/ 不是包）。"""
    spec = importlib.util.spec_from_file_location("mas_scrub_proxy_under_test", PROXY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _no_network(proxy, monkeypatch):
    """网络绊线：任何未被用例显式打桩的上游调用都会让用例炸掉（而不是真的连 MAS）。"""
    async def _boom(*a, **k):
        raise AssertionError("测试期间禁止触达上游（网络绊线）")
    monkeypatch.setattr(proxy, "_upstream_list_tools", _boom)
    monkeypatch.setattr(proxy, "_upstream_call_tool", _boom)
    monkeypatch.delenv(proxy.READONLY_ENV, raising=False)
    yield


def _mk_tool(name: str) -> types.Tool:
    return types.Tool(name=name, description="stub", inputSchema={"type": "object", "properties": {}})


# 只读 allowlist（10）+ 显式写/危险（6）+ get_ 前缀但**非** allowlist 的动作/无关工具（2）
FAKE_TOOLS = (
    list([
        "get_health_api_core_health_get",
        "get_overview_api_info_get_overview_post",
        "get_git_version_api_info_version_post",
        "get_queues_api_queue_get_post",
        "get_item_api_queue_item_get_post",
        "get_time_set_api_queue_time_get_post",
        "get_script_api_scripts_get_post",
        "get_user_api_scripts_user_get_post",
        "search_history_api_history_search_post",
        "get_history_data_api_history_data_post",
    ])
    + [
        "close_api_core_close_post",
        "add_task_api_dispatch_start_post",
        "update_user_api_scripts_user_update_post",
        "delete_game_sign_account_api_tools_sign_account_delete_post",
        "export_script_to_file_api_scripts_export_file_post",
        "import_script_from_file_api_scripts_import_file_post",
        "get_screenshot_api_ocr_screenshot_post",          # get_ 前缀但不在 allowlist
        "get_web_config_api_info_webconfig_post",          # get_ 前缀但不在 allowlist
    ]
)

WRITE_NAMES = [
    "close_api_core_close_post",
    "add_task_api_dispatch_start_post",
    "update_user_api_scripts_user_update_post",
    "export_script_to_file_api_scripts_export_file_post",
]


def _ok_result(text_obj: dict) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(text_obj, ensure_ascii=False))],
        isError=False)


def _payload(res: types.CallToolResult) -> dict:
    return json.loads(res.content[0].text)


# ── 1) 开关取值与 fail-closed ────────────────────────────────────────

def test_default_env_unset_means_full_control(proxy):
    assert proxy.readonly_enabled() is False


@pytest.mark.parametrize("value,expect", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True), (" enabled ", True),
    ("0", False), ("false", False), ("no", False), ("off", False), ("", False), ("   ", False),
    ("maybe", True), ("2", True), ("readonly", True),      # 非法值 -> fail-closed 到只读
])
def test_env_values(proxy, monkeypatch, value, expect):
    monkeypatch.setenv(proxy.READONLY_ENV, value)
    assert proxy.readonly_enabled() is expect


def test_invalid_value_is_fail_closed_even_for_weird_input(proxy, monkeypatch):
    monkeypatch.setenv(proxy.READONLY_ENV, "FALSE ")      # 注意：带空格但仍是合法 falsy
    assert proxy.readonly_enabled() is False
    monkeypatch.setenv(proxy.READONLY_ENV, "flase")       # 拼错 -> 只读
    assert proxy.readonly_enabled() is True


# ── 2) allowlist 完整性（精确名 + 危险动词自检）──────────────────────

def test_allowlist_is_exact_and_danger_free(proxy):
    assert len(proxy.READONLY_ALLOWED) == len(proxy.READONLY_ALLOWLIST) == 10
    for n in proxy.READONLY_ALLOWED:
        assert not proxy._DANGER_RE.search(n), "allowlist 混进危险名: %s" % n
        assert n.startswith(("get_", "search_")), "只读名应以 get_/search_ 开头: %s" % n


def test_sanitize_allowlist_drops_dangerous_names(proxy):
    got = proxy._sanitize_allowlist(["close_api_core_close_post",
                                     "update_user_api_scripts_user_update_post",
                                     "get_health_api_core_health_get"])
    assert got == ("get_health_api_core_health_get",)


def test_danger_regex_matches_write_verbs_but_not_time_set(proxy):
    for n in ("close_api_core_close_post", "add_task_api_dispatch_start_post",
              "update_queue_api_queue_update_post", "import_script_from_file_api_scripts_import_file_post",
              "export_script_to_file_api_scripts_export_file_post", "click_image_api_ocr_click_image_post",
              "stop_task_api_dispatch_stop_post", "send_json_message_api_ws_debug_message_send_json_post"):
        assert proxy._DANGER_RE.search(n), "应判为危险: %s" % n
    # 含 "set" 但整词安全的只读名不能误伤
    assert not proxy._DANGER_RE.search("get_time_set_api_queue_time_get_post")


# ── 3) tools/list 过滤 ──────────────────────────────────────────────

def test_list_is_filtered_in_readonly(proxy, monkeypatch):
    monkeypatch.setenv(proxy.READONLY_ENV, "1")
    async def _stub():
        return [_mk_tool(n) for n in FAKE_TOOLS]
    monkeypatch.setattr(proxy, "_upstream_list_tools", _stub)
    got = [t.name for t in asyncio.run(proxy._handle_list_tools())]
    assert sorted(got) == sorted(proxy.READONLY_ALLOWED)
    assert not (set(got) & set(WRITE_NAMES))
    assert "get_screenshot_api_ocr_screenshot_post" not in got      # 精确名，不是 get_* 泛匹配
    assert "get_web_config_api_info_webconfig_post" not in got


def test_list_is_transparent_by_default(proxy, monkeypatch):
    async def _stub():
        return [_mk_tool(n) for n in FAKE_TOOLS]
    monkeypatch.setattr(proxy, "_upstream_list_tools", _stub)
    got = [t.name for t in asyncio.run(proxy._handle_list_tools())]
    assert got == FAKE_TOOLS          # 默认模式：118/全部原样，现行为不变


# ── 4) 隐藏写调用：拒绝 + 零上游调用 ─────────────────────────────────

@pytest.mark.parametrize("name", WRITE_NAMES + ["get_screenshot_api_ocr_screenshot_post",
                                                "totally_unknown_tool"])
def test_hidden_call_denied_with_zero_upstream(proxy, monkeypatch, name):
    monkeypatch.setenv(proxy.READONLY_ENV, "1")
    calls = []
    async def _spy(n, a):
        calls.append((n, a))
        return _ok_result({"code": 200, "should": "never be reached"})
    monkeypatch.setattr(proxy, "_upstream_call_tool", _spy)
    res = asyncio.run(proxy._handle_call_tool(name, {"scriptId": "S"}))
    assert res.isError is True
    d = _payload(res)
    assert d["ok"] is False and d["mode"] == "readonly" and d["tool"] == name and d["reason"]
    assert calls == [], "只读模式拒绝时必须零上游调用，实际=%r" % (calls,)
    assert "never be reached" not in res.content[0].text


def test_danger_guard_applies_even_if_allowlist_corrupted(proxy, monkeypatch):
    monkeypatch.setenv(proxy.READONLY_ENV, "1")
    monkeypatch.setattr(proxy, "READONLY_ALLOWED",
                        tuple(proxy.READONLY_ALLOWED) + ("close_api_core_close_post",))
    calls = []
    async def _spy(n, a):
        calls.append(n)
        return _ok_result({"code": 200})
    monkeypatch.setattr(proxy, "_upstream_call_tool", _spy)
    res = asyncio.run(proxy._handle_call_tool("close_api_core_close_post", {}))
    assert res.isError is True and "危险动词" in _payload(res)["reason"] and calls == []


# ── 5) 允许的只读工具：正常执行 + 仍脱敏 ────────────────────────────

def test_allowed_read_succeeds_and_still_scrubbed(proxy, monkeypatch):
    monkeypatch.setenv(proxy.READONLY_ENV, "1")
    calls = []
    async def _spy(n, a):
        calls.append((n, a))
        return _ok_result({"code": 200, "data": {"u1": {"Info": {
            "Name": "acct", "Password": "REAL-PW", "SklandToken": "REAL-TK"}}}})
    monkeypatch.setattr(proxy, "_upstream_call_tool", _spy)
    res = asyncio.run(proxy._handle_call_tool("get_user_api_scripts_user_get_post", {"scriptId": "S"}))
    assert res.isError is False
    assert calls == [("get_user_api_scripts_user_get_post", {"scriptId": "S"})]
    info = _payload(res)["data"]["u1"]["Info"]
    assert info["Password"] == proxy.PLACEHOLDER and info["SklandToken"] == proxy.PLACEHOLDER
    assert info["Name"] == "acct" and "REAL-PW" not in res.content[0].text


@pytest.mark.parametrize("name", [
    "get_health_api_core_health_get",
    "get_overview_api_info_get_overview_post",
    "get_queues_api_queue_get_post",
    "search_history_api_history_search_post",
])
def test_each_allowlisted_tool_passes_the_gate(proxy, monkeypatch, name):
    monkeypatch.setenv(proxy.READONLY_ENV, "1")
    calls = []
    async def _spy(n, a):
        calls.append(n)
        return _ok_result({"code": 200})
    monkeypatch.setattr(proxy, "_upstream_call_tool", _spy)
    res = asyncio.run(proxy._handle_call_tool(name, {}))
    assert res.isError is False and calls == [name]


# ── 6) 默认模式兼容（人工会话不受影响）──────────────────────────────

@pytest.mark.parametrize("name", WRITE_NAMES)
def test_default_mode_forwards_writes_untouched(proxy, monkeypatch, name):
    calls = []
    async def _spy(n, a):
        calls.append(n)
        return _ok_result({"code": 200, "forwarded": True})
    monkeypatch.setattr(proxy, "_upstream_call_tool", _spy)
    res = asyncio.run(proxy._handle_call_tool(name, {"x": 1}))
    assert res.isError is False and calls == [name]
    assert _payload(res)["forwarded"] is True
