# -*- coding: utf-8 -*-
"""闸门标准第 9 条：49 条必需路由白名单的存在性断言（task-14 / 2026-09-26）。

背景
----
前端整体退役时，network/api_fastapi.py 的路由被按「49 条白名单」收裁。
**唯一依据**：
  - docs/audits/2026-09-26_minimal_ops_gate.md      §4   （32 条：§4.1 A 组 20 + §4.2 B 组 8 + §4.3 C 组 4）
  - docs/audits/2026-09-26_minimal_ops_gate_recheck.md §4 （新增 17 条，由 task-17 的 16 个 MCP 工具驱动）
32 + 17 = 49（合并去重后仍为 49）。

为什么这条测试是硬闸门
----------------------
network/mcp_server.py 的 41 个 MCP 工具全部通过 _call_api 自调用本 app 的 REST；
而 _call_api 把 HTTPError 包成 {"error": "HTTP 404"}（mcp_server.py:60-66 附近）——
路由被误删 = 工具**静默失败**（不报错，只是不干活）。
若误用旧 32 条口径，41 个工具里 16 个会 404：
  kill_audit×3 + 模拟器/连接×7 + MAA×6。

本测试是**只读**断言：不改任何代码，不联网，不需要 Manager/配置（用 stub mw 建 app）。
运行：
    python -m pytest tests/test_routes_whitelist.py -q
"""
from __future__ import annotations

import inspect
import re
import sys
import types
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── 49 条白名单（method, path）—— 与审计文档逐行对齐，禁止随手改 ──────────────
WHITELIST: list[tuple[str, str]] = [
    # §4.1 A 组：MCP 后端（20 条）
    ("GET", "/api/status"),
    ("GET", "/api/node/dashboard"),
    ("GET", "/api/stats/dashboard"),
    ("POST", "/api/probe/stage"),
    ("GET", "/api/probe/status"),
    ("GET", "/api/depot/plan"),
    ("GET", "/api/activity/stages"),
    ("POST", "/api/activity/stages"),
    ("GET", "/api/account_status"),
    ("GET", "/api/health"),
    ("GET", "/api/events/list"),
    ("GET", "/api/queue"),
    ("POST", "/api/queue/enqueue"),
    ("POST", "/api/queue/dequeue"),
    ("POST", "/api/queue/pause"),
    ("POST", "/api/queue/resume"),
    ("POST", "/api/action/smart_selected"),
    ("GET", "/api/inspect/link_map"),
    ("GET", "/api/inspect/account/{name}"),
    ("GET", "/api/inspect/processes"),
    # §4.2 B 组：保留判据外延（8 条）
    ("GET", "/api/mas/status"),
    ("POST", "/api/mas/rerun"),
    ("POST", "/api/mas/cleanup"),
    ("POST", "/api/probe/anni_weekly"),
    ("GET", "/api/probe/results"),
    ("POST", "/api/probe/apply_plan"),
    ("GET", "/api/debug/state"),
    ("POST", "/api/queue/clear"),
    # §4.3 C 组：外部/工具/入站回调（4 条）
    ("GET", "/api/accounts"),
    ("POST", "/api/maa_remote/task"),
    ("GET", "/api/maa_remote"),
    ("POST", "/api/maa_remote/status"),
    # §4 新增 17 条（task-17 的 16 个 MCP 工具驱动，旧 32 条口径会全删）
    ("GET", "/api/kill_audit"),
    ("GET", "/api/kill_audit/pending"),
    ("POST", "/api/kill_audit/decide"),
    ("GET", "/api/emulators"),
    ("GET", "/api/emulator/{idx}"),
    ("POST", "/api/emulator/batch/{action}"),
    ("POST", "/api/emulator/{idx}/{action}"),
    ("GET", "/api/connect/status"),
    ("POST", "/api/connect/{aid}/stop"),
    ("POST", "/api/connect/{aid}/restart"),
    ("GET", "/api/maa/status"),
    ("GET", "/api/maa/instances"),
    ("POST", "/api/account/{idx}/launch"),
    ("POST", "/api/account/{idx}/stop"),
    ("POST", "/api/action/stop_all"),
    ("POST", "/api/maa/launch_source"),
    ("GET", "/api/account/{idx}/status"),
]

HTTP_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
# FastAPI 自身注册、非业务路由，不计入白名单/删除集
FRAMEWORK_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}

# 明确必须**不存在**的已删路由（前端退役的功能面 + 安全面 + 旧静态兜底）
RETIRED_MUST_BE_GONE: list[tuple[str, str]] = [
    ("GET", "/api/config"),                 # 改配置，且 MCP 明确不暴露
    ("POST", "/api/config"),
    ("POST", "/api/system/kill_maa"),       # 直接 taskkill，MCP 明确不暴露
    ("POST", "/api/system/restart"),
    ("POST", "/api/system/close_popups"),
    ("GET", "/api/sse"),                    # Node 原生
    ("POST", "/api/sse"),
    ("GET", "/api/logs"),                   # Node 原生
    ("GET", "/api/stats"),                  # Node 原生
    ("GET", "/api/stages"),
    ("GET", "/api/oplog"),
    ("GET", "/api/accounts/export"),
    ("GET", "/api/emulators/diagnose"),
    ("POST", "/api/connect/launch"),
    ("POST", "/api/action/smart_all"),
    ("GET", "/api/orch/update_result"),
]


class _StubMW:
    """create_app 只需要 .config / ._log；MCP 挂载关掉以避免 5s 冷启动。"""

    def __init__(self) -> None:
        self.config = {"api_token": "", "mcp_mount_enabled": False}

    def _log(self, *a, **k) -> None:  # noqa: D102
        pass


def _build_app():
    from network.api_fastapi import create_app

    return create_app(_StubMW())


@pytest.fixture(scope="module")
def app():
    return _build_app()


def _business_routes(app) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for r in app.routes:
        path = getattr(r, "path", None)
        if not path or path in FRAMEWORK_PATHS or path.startswith("/mcp"):
            continue
        for m in (getattr(r, "methods", None) or set()):
            if m in HTTP_METHODS:
                out.add((m, path))
    return out


def _norm_path(p: str) -> str:
    """把 {idx} / {int(idx)} / {name} 统一成 {}，便于 MCP 路径与路由模板比对。"""
    p = p.split("?", 1)[0]
    return re.sub(r"\{[^}]*\}", "{}", p)


# ── 1. 白名单本身：必须是 49 条且无重复 ────────────────────────────────────
def test_whitelist_is_49_unique():
    assert len(WHITELIST) == 49, f"白名单必须是 49 条（32 旧 + 17 新），实际 {len(WHITELIST)}"
    assert len(set(WHITELIST)) == 49, "白名单有重复项"


# ── 2. 逐条断言：49 条全部存在（一条不落）────────────────────────────────
@pytest.mark.parametrize("method,path", WHITELIST, ids=[f"{m} {p}" for m, p in WHITELIST])
def test_whitelist_route_exists(app, method, path):
    routes = _business_routes(app)
    assert (method, path) in routes, f"白名单路由缺失（MCP 会静默 404）：{method} {path}"


# ── 3. 反向断言：业务路由恰好 = 49 条（不多不少）──────────────────────────
def test_no_extra_business_routes(app):
    routes = _business_routes(app)
    extra = sorted(routes - set(WHITELIST))
    missing = sorted(set(WHITELIST) - routes)
    assert not missing, f"缺失白名单路由：{missing}"
    assert not extra, f"出现白名单外的业务路由（应已随前端退役删除）：{extra}"
    assert len(routes) == 49, f"业务路由数应为 49，实际 {len(routes)}"


# ── 4. 已删路由确实不存在 + 旧静态 catch-all 已移除 ──────────────────────
@pytest.mark.parametrize("method,path", RETIRED_MUST_BE_GONE, ids=[f"{m} {p}" for m, p in RETIRED_MUST_BE_GONE])
def test_retired_route_is_gone(app, method, path):
    assert (method, path) not in _business_routes(app), f"已退役路由仍存在：{method} {path}"


def test_no_spa_catch_all(app):
    """旧 GET /{full_path:path}：ui/web 删除后它会 FileResponse 指向不存在文件 → 500。"""
    paths = [getattr(r, "path", "") for r in app.routes]
    assert not [p for p in paths if "full_path" in p], f"catch-all 未删除：{paths}"
    for r in app.routes:
        p = getattr(r, "path", "") or ""
        # 只允许 /api/* 与 /mcp*；不允许任何其它通配兜底
        assert p.startswith("/api/") or p.startswith("/mcp") or p in FRAMEWORK_PATHS, \
            f"存在非 /api 的兜底路由（会吞掉未知路径）：{p}"


def test_unknown_path_is_clean_404(app):
    httpx = pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        for probe in ("/", "/index.html", "/ui/web/index.html", "/unknown/xyz", "/api/nope"):
            r = c.get(probe)
            assert r.status_code == 404, f"{probe} 应返回 404，实际 {r.status_code}"
            assert r.status_code < 500


# ── 5. MCP 41 个工具引用的路径全部可达 ───────────────────────────────────
_CALL_RE = re.compile(r"_call_api\(\s*mw\s*,\s*f?[\"']([^\"']+)[\"']")
_GATE_RE = re.compile(r"_gate\(\s*f?[\"']([^\"']+)[\"']")


# 记录型假 FastMCP：模块级共享 dict（不要用闭包局部变量 —— 若 network.mcp_server
# 已在 sys.modules 里被缓存过，二次调用会复用旧模块对象，局部 dict 收不到东西）。
_RECORDED: dict[str, object] = {}


class _FakeMCP:
    def __init__(self, *a, **k) -> None:
        pass

    def tool(self):
        def deco(fn):
            _RECORDED[fn.__name__] = fn
            return fn

        return deco

    def http_app(self, *a, **k):
        return object()


def _load_real_mcp_tools() -> dict[str, object]:
    """用记录型假 FastMCP 走一遍 build_mcp_app，拿到真实注册的 41 个工具函数。"""
    import importlib
    import network

    _RECORDED.clear()
    saved = sys.modules.get("fastmcp")
    sys.modules.pop("network.mcp_server", None)
    if hasattr(network, "mcp_server"):           # 关键：清掉包属性，否则 from-import 会拿到旧模块
        delattr(network, "mcp_server")
    fake = types.ModuleType("fastmcp")
    fake.FastMCP = _FakeMCP  # type: ignore[attr-defined]
    try:
        sys.modules["fastmcp"] = fake
        ms = importlib.import_module("network.mcp_server")
        ms.build_mcp_app(_StubMW())
    finally:
        sys.modules.pop("network.mcp_server", None)
        if hasattr(network, "mcp_server"):
            delattr(network, "mcp_server")
        if saved is not None:
            sys.modules["fastmcp"] = saved
        else:
            sys.modules.pop("fastmcp", None)
    return dict(_RECORDED)


def test_mcp_tool_count_is_41():
    tools = _load_real_mcp_tools()
    assert len(tools) == 41, f"MCP 工具数应为 41，实际 {len(tools)}：{sorted(tools)}"


# mcp_server.py 的 queue_control 用 f"/api/queue/{action}"（action=pause|resume）；
# 审计文档 §4 已注明「/api/queue/{action} 展开为 pause+resume 两条路由」——显式展开，
# 不允许用「通配等于存在」糊过去（否则删掉 pause 或 resume 一条，工具会静默 404）。
TEMPLATE_EXPANSIONS: dict[str, list[str]] = {
    "/api/queue/{action}": ["/api/queue/pause", "/api/queue/resume"],
}


def test_mcp_referenced_paths_all_reachable(app):
    tools = _load_real_mcp_tools()
    if not tools:
        pytest.skip("fastmcp 不可用，跳过 MCP 路径可达性断言")

    route_set = {_norm_path(p) for _, p in _business_routes(app)}
    src = (ROOT / "network" / "mcp_server.py").read_text(encoding="utf-8")

    referenced: set[str] = set()
    for fn in tools.values():
        try:
            body = inspect.getsource(fn)
        except (OSError, TypeError):
            body = ""
        for rx in (_CALL_RE, _GATE_RE):
            referenced.update(rx.findall(body))
    # 兜底：整模块扫描（含工具体内的嵌套 preview 闭包）
    for rx in (_CALL_RE, _GATE_RE):
        referenced.update(rx.findall(src))

    assert referenced, "没能从 mcp_server.py 提取到任何 _call_api/_gate 路径"

    unreachable: list[str] = []
    for p in sorted(referenced):
        if p in TEMPLATE_EXPANSIONS:
            unreachable += [e for e in TEMPLATE_EXPANSIONS[p] if e not in route_set]
        elif _norm_path(p) not in route_set:
            unreachable.append(p)
    assert not unreachable, f"MCP 引用了不存在的路由（会静默 {{{{error: HTTP 404}}}}）：{unreachable}"
