"""MAAOrch MCP server — 把 MAAOrch 调度平台能力暴露为 MCP 工具。

架构（2026-08-14 立项，DSH Agent 工具插件）：
- 挂载：main_web 的 FastAPI app 在 /mcp 路径挂载本模块返回的 ASGI app（streamable-http）。
- 鉴权：挂载点校验 x-agent-token / Authorization: Bearer，值须与 api_token 一致
  （api_token 留空时不鉴权，兼容本地开发）。不新增裸暴露面。
- 工具内部通过 127.0.0.1 自调用 MAAOrch HTTP API（复用现有 api_token 鉴权层和
  参数校验，无需重构端点逻辑）。
- 定位：DSH Agent = 遥控器（传令/报信），MAAOrch = 唯一执行中枢，生命周期互不绑架。

安全原则：只暴露"调度台也有的操作"，Agent 经对话指令触发；危险操作
（杀进程/改配置/部署）一律不暴露。
"""
from __future__ import annotations

import hmac
import json
import urllib.error
import urllib.request
from typing import Any

from fastmcp import FastMCP


def _call_api(mw: Any, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    """调用本机 MAAOrch HTTP API（复用 api_token 鉴权层）。"""
    port = int(mw.config.get("api_port", 19999) or 19999) if hasattr(mw, "config") else 19999
    token = mw.config.get("api_token", "") if hasattr(mw, "config") else ""
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    if token:
        req.add_header("x-agent-token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except ValueError:
                return {"text": raw[:2000]}
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8", errors="replace"))
        except Exception:
            detail = e.reason
        return {"error": f"HTTP {e.code}", "detail": detail}
    except Exception as e:  # 工具返回错误而非抛出
        return {"error": str(e)}


class _TokenAuth:
    """MCP 挂载点鉴权：x-agent-token 或 Authorization: Bearer，值须与 api_token 一致。"""

    def __init__(self, app: Any, mw: Any) -> None:
        self.app = app
        self.token = mw.config.get("api_token", "") if hasattr(mw, "config") else ""
        # 透传 lifespan：FastAPI mount 子 app 不会传播，父 app 需显式组合
        self.lifespan = getattr(app, "lifespan", None)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if not self.token or scope.get("type") != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        got = headers.get("x-agent-token", "")
        if not got:
            auth = headers.get("authorization", "")
            if auth.startswith("Bearer "):
                got = auth[7:]
        if not got or not hmac.compare_digest(got, self.token):
            body = json.dumps({"error": "unauthorized"}).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": body})
            return
        return await self.app(scope, receive, send)


def build_mcp_app(mw: Any) -> Any:
    """构造挂载到 main_web /mcp 的 ASGI app（streamable-http + token 鉴权）。"""
    mcp = FastMCP("maaorch")

    # ── 只读：状态/总览 ──────────────────────────────────────────────

    @mcp.tool()
    def link_map() -> dict:
        """模拟器↔MAA↔ADB 端口对应关系：每个官服账号的模拟器真实端口 vs MAA
        配置端口是否一致、模拟器/MAA 是否活着、任务链。查对应/连接错乱用这个。"""
        return _call_api(mw, "/api/inspect/link_map")

    @mcp.tool()
    def inspect_account(account_name: str) -> dict:
        """勘察单个账号现场：配置、模拟器状态、MAA 实例、当前任务、asst.log
        最近错误。账号卡住/动作异常时用它定位卡在哪。"""
        return _call_api(mw, f"/api/inspect/account/{account_name}")

    @mcp.tool()
    def processes() -> dict:
        """枚举可疑进程（MAA/python/adb/MuMu 含父子关系）— 查挂着的子代/
        残留/多余进程。"""
        return _call_api(mw, "/api/inspect/processes")

    @mcp.tool()
    def status() -> dict:
        """MAAOrch 总体运行状态（等效 Web 首页）：模拟器、账号、队列、任务、统计总览。"""
        return _call_api(mw, "/api/status")

    @mcp.tool()
    def dashboard() -> dict:
        """节点/账号总览看板：每台模拟器的账号、运行状态、最近任务信息。"""
        return _call_api(mw, "/api/node/dashboard")

    @mcp.tool()
    def queue() -> dict:
        """调度队列状态：pending（排队中，含位置/ETA/任务内容）、active（运行中）、paused。"""
        return _call_api(mw, "/api/queue")

    @mcp.tool()
    def account_status() -> dict:
        """全部账号状态：连接、挂起、运行、体力/理智、任务信息。"""
        return _call_api(mw, "/api/account_status")

    @mcp.tool()
    def events(limit: int = 50) -> dict:
        """事件流（EventBus）：模拟器启停、任务完成/失败、账号事件等（按时间倒序）。"""
        return _call_api(mw, f"/api/events/list?limit={int(limit)}")

    @mcp.tool()
    def stats_dashboard() -> dict:
        """刷取统计总览：今日/累计运行次数、时长、成功率。"""
        return _call_api(mw, "/api/stats/dashboard")

    @mcp.tool()
    def health() -> dict:
        """服务健康检查：进程、看门狗、关键服务存活状态。"""
        return _call_api(mw, "/api/health")

    @mcp.tool()
    def depot_plan() -> dict:
        """材料刷取计划（Depot 计划）：当前水位、待刷材料、目标关卡与次数。"""
        return _call_api(mw, "/api/depot/plan")

    @mcp.tool()
    def activity_stages() -> dict:
        """当前活动关卡列表（企鹅物流分类），用于选择刷取目标。"""
        return _call_api(mw, "/api/activity/stages")

    @mcp.tool()
    def probe_status() -> dict:
        """关卡检测（Probe）状态：进行中/历史结果、账号关卡能力记录。"""
        return _call_api(mw, "/api/probe/status")

    # ── 写：经对话指令触发（与调度台同等操作）────────────────────────

    @mcp.tool()
    def enqueue(account_index: int, priority: int = 0) -> dict:
        """将账号（按 index，0 起）加入调度队列。priority 越大越靠前（默认 0）。

        注意：只入队，具体跑什么任务由该账号的调度任务/槽位决定；
        要指定任务请用 smart_dispatch。
        """
        return _call_api(mw, "/api/queue/enqueue", {"account_index": account_index, "priority": priority})

    @mcp.tool()
    def dequeue(account_index: int) -> dict:
        """将账号（按 index）移出调度队列（已在运行的不会中断）。"""
        return _call_api(mw, "/api/queue/dequeue", {"account_index": account_index})

    @mcp.tool()
    def queue_control(action: str) -> dict:
        """队列控制：action="pause" 暂停队列（不再启动新任务），"resume" 恢复。"""
        if action not in ("pause", "resume"):
            return {"error": "action must be 'pause' or 'resume'"}
        return _call_api(mw, f"/api/queue/{action}")

    @mcp.tool()
    def smart_dispatch(account_ids: list[str], include_anni: bool = True, only_anni: bool = False) -> dict:
        """调度台下发任务（核心）：对指定账号（account_ids，账号 id 列表）执行日常任务编排。

        - include_anni=True: 账号配置了剿灭则并入剿灭；only_anni=True: 只跑剿灭。
        - 账号已排队/运行中会自动跳过。
        - 任务内容由账号的调度配置决定（智能调度开启时按智能编排）。
        """
        return _call_api(mw, "/api/action/smart_selected",
                         {"account_ids": account_ids, "include_anni": include_anni, "only_anni": only_anni})

    @mcp.tool()
    def probe_stage(account_id: str, stage: str) -> dict:
        """检测某账号能否刷指定关卡（异步，结果落 probe_status；可配 probe_stages 批量）。"""
        return _call_api(mw, "/api/probe/stage", {"account_id": account_id, "stage": stage})

    # ── MAS（AUTO-MAS 远端多账号调度器）──────────────────────────────
    # 进程内直连 services.mas_adapter（MAS 唯一门面），不新增 REST 路由
    # （/api/mas/* 由另一处维护）。adapter 惰性加载 services.mas_l3，主机不可达/
    # 服务层异常时统一返回 {ok: False, error, degraded: True}，不抛异常。
    # 只读/写标注见各 docstring；写工具一律 dry_run=True 默认预演。

    @mcp.tool()
    def mas_snapshot(refresh: bool = False) -> dict:
        """【只读】MAS 远端主机实时快照（SSH 采集）：在飞 MAA、盘位、队列进度、
        失败号、未跑账号、磁盘余量。refresh=True 绕过 45 秒缓存强制重采（较慢）。
        主机不可达时返回 {ok: False, degraded: True} 而非抛异常。"""
        from services import mas_adapter
        return mas_adapter.snapshot(refresh=bool(refresh))

    @mcp.tool()
    def mas_status(days: int = 7, refresh: bool = False) -> dict:
        """【只读】MAS 产线总览（与 Web 工作台同源）：主机实时快照 + 今日/近 N 天发车
        聚合（一次过/补跑成功/全败）+ 失败原因 Top + 钉子户 + 本地留痕 + 补跑状态。
        "昨晚谁失败了、为什么"用这个；days 范围 1-30（默认 7）。"""
        from services import mas_adapter
        return mas_adapter.status(days=int(days), refresh=bool(refresh))

    @mcp.tool()
    def mas_history(days: int = 7) -> dict:
        """【只读】MAS（AUTO-MAS）近 N 天原始历史（未聚合）：
        {日期: {账号: {index: 逐次尝试(状态/时间), error_info: 失败原因}}}。
        需要逐次尝试明细时用它；要看结论用 mas_status。"""
        from services import mas_adapter
        return mas_adapter.history(days=int(days))

    @mcp.tool()
    def mas_ledger(n: int = 12) -> dict:
        """【只读】MAS 本地留痕（runtime/mas_ledger.jsonl 末尾 n 条）：
        日报/补跑/清理等事件流水，用于"刚才那轮到底做了什么"的追溯。"""
        from services import mas_adapter
        return mas_adapter.ledger(int(n))

    @mcp.tool()
    def mas_rerun_state() -> dict:
        """【只读】MAS 补跑进度：后台补跑是否在跑、本批账号、开始/完成时间、结果明细
        （含 ok=补跑是否发车成功）。"""
        from services import mas_adapter
        return mas_adapter.rerun_state()

    @mcp.tool()
    def mas_rerun(accounts: list[str] | None = None, dry_run: bool = True) -> dict:
        """【写操作】触发 MAS 补跑发车：给远端 AUTO-MAS 建临时队列补跑失败账号（最多 8 个）。

        会改动远端 MAS 队列。默认 dry_run=True：只预演，不碰远端，返回将要发的号
        （accounts 为空时按主机快照的失败号解析候选）；只有显式 dry_run=False 才真正提交。
        """
        from services import mas_adapter
        return mas_adapter.rerun(accounts=accounts, dry_run=bool(dry_run))

    @mcp.tool()
    def mas_cleanup(dry_run: bool = True) -> dict:
        """【写操作】清理远端 MAS 上的临时补跑队列（tools/auto_mas_rerun.py --cleanup）。

        会改动远端 MAS 队列。默认 dry_run=True：只预演，不删除任何东西；
        只有显式 dry_run=False 才真正执行清理。
        """
        from services import mas_adapter
        return mas_adapter.cleanup(dry_run=bool(dry_run))

    # ── 挂载 ─────────────────────────────────────────────────────────

    # http_app(path="/")：内部路由注册在根，挂载前缀（/mcp）由 FastAPI mount 决定
    return _TokenAuth(mcp.http_app(path="/", transport="streamable-http"), mw)
