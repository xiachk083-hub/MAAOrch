# -*- coding: utf-8 -*-
"""脱敏代理的加固回归测试（2026-09-26 独立对抗性验证的产物）。

来源：docs/audits/2026-09-26_verdict_proxy.md 的 F2 / F3 / F7 / F9。
这些用例是原有 tests/test_mas_proxy_readonly.py（38 项）**没有覆盖**的形态。

运行：python -m pytest tests/test_mas_proxy_hardening.py -q
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import mas_scrub_proxy as m  # noqa: E402

PLACEHOLDER = m.PLACEHOLDER


# ── F7：键名盲区 ─────────────────────────────────────────────────────
NEW_KEY_NAMES = [
    "pass_word", "pwd", "passwd", "apiKey", "api_key", "Authorization",
    "auth_key", "authkey", "private_key", "session_id", "sessionid", "SklandToken",
]


@pytest.mark.parametrize("key", NEW_KEY_NAMES)
def test_f7_variant_key_names_are_scrubbed(key):
    """原默认集合漏掉这些写法 —— 它们曾原样外泄。"""
    out = m.scrub({"Info": {key: "SYNTH-SECRET-VALUE"}})
    assert out["Info"][key] == PLACEHOLDER, key


def test_f7_generic_password_still_scrubbed():
    assert m.scrub({"Password": "x", "nested": {"Cookie": "y"}}) == {
        "Password": PLACEHOLDER, "nested": {"Cookie": PLACEHOLDER}}


# ── F3：异常消息也必须过脱敏 ──────────────────────────────────────────
def test_f3_exception_message_is_scrubbed():
    """上游异常里带敏感键时，错误回显不能原样透出。"""
    msg = 'HTTP 500: {"Info": {"Password": "SYNTH-LEAK", "Name": "acct"}}'
    assert "SYNTH-LEAK" not in m.scrub_text(msg)
    assert PLACEHOLDER in m.scrub_text(msg)


def test_f3_handler_uses_scrub_text_on_error():
    src = (ROOT / "tools" / "mas_scrub_proxy.py").read_text(encoding="utf-8")
    assert 'scrub_text("%s: %s" % (type(e).__name__, e))' in src


# ── F2：只读模式缓存上游工具清单 ──────────────────────────────────────
def test_f2_list_tools_cache_avoids_repeat_upstream(monkeypatch):
    """只读模式下第二次 list 必须走缓存，否则 SDK 的预热水会让每次被拒调用都打上游。"""
    calls = {"n": 0}

    async def fake_list():
        calls["n"] += 1
        return [type("T", (), {"name": "get_health_api_core_health_get"})()]

    monkeypatch.setenv(m.READONLY_ENV, "1")
    monkeypatch.setattr(m, "_upstream_list_tools", fake_list)
    monkeypatch.setattr(m, "_TOOLS_CACHE", None)

    async def run():
        await m._upstream_list_tools_cached()
        await m._upstream_list_tools_cached()
        await m._upstream_list_tools_cached()

    asyncio.run(run())
    assert calls["n"] == 1, "只读模式应命中缓存（实际调用上游 %d 次）" % calls["n"]


def test_f2_full_mode_does_not_cache(monkeypatch):
    calls = {"n": 0}

    async def fake_list():
        calls["n"] += 1
        return []

    monkeypatch.delenv(m.READONLY_ENV, raising=False)
    monkeypatch.setattr(m, "_upstream_list_tools", fake_list)
    monkeypatch.setattr(m, "_TOOLS_CACHE", None)

    async def run():
        await m._upstream_list_tools_cached()
        await m._upstream_list_tools_cached()

    asyncio.run(run())
    assert calls["n"] == 2, "全量模式不应缓存（否则工具面变化看不到）"


# ── F9：开关取值语义 ─────────────────────────────────────────────────
@pytest.mark.parametrize("val,expect", [
    ("1", True), ("true", True), ("YES", True), ("on", True), (" enabled ", True),
    ("0", False), ("false", False), ("no", False), ("off", False),
    ("garbage", True), ("maybe", True),          # 非法非空 -> fail-closed 按只读
])
def test_f9_readonly_env_semantics(monkeypatch, val, expect):
    monkeypatch.setenv(m.READONLY_ENV, val)
    assert m.readonly_enabled() is expect, val


def test_f9_unset_or_empty_means_full_mode(monkeypatch):
    """未设置/空串 = 全量模式（手动全量控制条目就靠这个）。文档必须如实这么写。"""
    monkeypatch.delenv(m.READONLY_ENV, raising=False)
    assert m.readonly_enabled() is False
    monkeypatch.setenv(m.READONLY_ENV, "")
    assert m.readonly_enabled() is False


def test_f9_doc_wording_matches_code():
    """防止文档再次宣称『其它任何值 fail-closed』而不提空串。"""
    src = (ROOT / "tools" / "mas_scrub_proxy.py").read_text(encoding="utf-8")
    assert "未设置或空串" in src or "未设置或空" in src, "缺少对空串语义的说明"
