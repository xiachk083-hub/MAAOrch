# -*- coding: utf-8 -*-
"""mas-ondemand MCP 的离线回归（kanban t_1aa55dac；t_4ddb3728 起账本与 CLI 版共用）。

三层证据里最便宜的一层：
- 本文件（pytest，离线）：server 自带 `--selftest` 18 项 + 工具面契约（6 名 / 写动作默认预演）+
  共享账本口径（与 CLI `tools/mas_ondemand.py` 同一路径、同一把 `mas_dispatch.lock`）。
- `python tools/mas_ondemand_mcp.py --check`：真 REST + 真 ssh 的只读闸门自检。
- `python tests/ondemand_mcp_probe.py`：真 stdio MCP 协议 + 只读/dry-run 现场抽查（13 项）。

本文件不碰网络、不碰真机（selftest 内部 monkeypatch 掉 REST/账本）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"

# 冻结契约（与 docs/MCP_TOOL_ROADMAP.md §10、config.yaml tools.include 三处必须一致）
EXPECTED_TOOLS = [
    "list_running",
    "dispatch_queue",
    "dispatch_script",
    "dispatch_account",
    "stop_task",
    "ondemand_preflight",
]
WRITE_TOOLS = ["dispatch_queue", "dispatch_script", "dispatch_account", "stop_task"]


def _load():
    name = "mas_ondemand_mcp"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _schemas(mod):
    return {t.name: t for t in mod._SCHEMAS}


def test_tools_are_the_six_contracted_names():
    assert list(_schemas(_load())) == EXPECTED_TOOLS


def test_write_tools_default_to_dry_run():
    schemas = _schemas(_load())
    for name in WRITE_TOOLS:
        props = schemas[name].inputSchema["properties"]
        assert props["apply"]["default"] is False, f"{name}: apply 必须默认 False（预演）"
    for name in ("list_running", "ondemand_preflight"):
        assert "apply" not in schemas[name].inputSchema["properties"], f"{name} 应为只读"


def test_selftest_all_green():
    rep = _load().selftest()
    assert rep["failed"] == 0 and rep["ok"]
    assert rep["passed"] >= 18, rep["passed"]
    assert all(c["ok"] for c in rep["cases"])


def _load_ledger():
    name = "mas_ondemand_ledger"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_ledger_is_shared_with_cli_end():
    """t_4ddb3728：MCP 与 CLI 必须读写同一份账本、共用同一把发车锁。"""
    mod = _load()
    lg = _load_ledger()
    assert mod.ledger is lg, "两端必须 import 同一个账本模块"
    assert lg.ledger_path().endswith("mas_ondemand_ledger.json"), lg.ledger_path()
    assert mod.STATE == lg.ledger_path(), "STATE 旧引用应指向共享账本"
    assert mod.LOG_EVENTS.endswith("mas_dispatch_log.jsonl"), "真动作事件应进班次同源事件流"
    assert set(mod.EVENT_VERDICTS) >= {"dispatched", "failed", "stopped"}, "真动作 verdict 映射缺失"
    # 锁：MCP 用的就是 mas_dispatch 的锁（同文件），不再自持一把
    assert mod.md.LOCK.endswith("mas_dispatch.lock"), mod.md.LOCK
