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

例外与写闸门（2026-08-16 前端退役，人工入口整体迁到 MCP）：
- 守护审计（Kill Audit）的**放行/拦截本身**是例外 —— 它是所有破坏性动作
  （杀 MAA / 关模拟器 / 回收 / 重启）的确认闸门（models/kill_audit.py），
  前端退役后若不给 MCP 入口，人工将完全无法放行，观察窗口一到反而变成
  "超时默认放行"（models/kill_audit.py:129-146），比人工把关更危险。
- 放行不直接杀进程：kill_audit_decide 只改条目状态，真正动手由守护方在
  观察到 approved 后执行（models/kill_audit.py:118-126）。
- 仍然**不暴露**：/api/system/kill_maa（直接 taskkill MAA.exe）、
  /api/system/restart、/api/system/close_popups、/api/config（改配置）。
- 写工具契约（kill_audit_decide / emulator_control / emulator_batch /
  emu_connect_stop / emu_connect_restart / account_launch / account_stop /
  maa_stop_all / maa_launch_source）：一律带 dry_run 形参且**默认 True** ——
  True 时只回显将要提交的请求与目标现状，绝不下发；只有显式 dry_run=False
  才真正执行。
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

    def _gate(path: str, body: dict, dry_run: bool, preview: Any = None,
              timeout: int = 30) -> dict:
        """写操作统一闸门：dry_run=True（默认）只预演，绝不下发请求。

        dry_run=False 时才 POST path（body 一律给 {} 以保证 _call_api 用 POST）。
        preview 是可选的只读回调，用于在预演时回显目标现状。
        """
        if not dry_run:
            return _call_api(mw, path, body, timeout=timeout)
        out: dict = {
            "ok": True,
            "dry_run": True,
            "would_call": {"method": "POST", "path": path, "body": body},
            "hint": "确认无误后显式传 dry_run=False 才会真正执行",
        }
        if preview is not None:
            try:
                out["preview"] = preview()
            except Exception as e:  # 预演信息失败不影响闸门判断
                out["preview_error"] = str(e)
        return out

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

    # ── 守护审计放行（Kill Audit）────────────────────────────────────
    # 前端退役后这是唯一人工放行口（原 Web「守护审计」页）。代理 api_fastapi.py
    # 的 /api/kill_audit*（不新增 REST 路由）。放行本身不杀进程：decide 只改条目
    # 状态，破坏性动作由守护方观察到 approved 后执行（models/kill_audit.py:118-126）。

    @mcp.tool()
    def kill_audit_history(limit: int = 100) -> dict:
        """【只读】守护审计历史（倒序）+ 各状态计数概览。

        每条含：动作类型 action(kill_maa/close_emu/reclaim_emu/restart_emu)、
        申请原因 reason、判据快照 evidence、状态 status(pending/approved/
        rejected/expired/executed)、决定人 decided_by、备注 note/复盘 fix。
        "谁在什么时候放行了哪次杀、依据是什么"用这个；limit 默认 100。
        代理 GET /api/kill_audit?limit=（api_fastapi.py:2157-2163）。
        """
        return _call_api(mw, f"/api/kill_audit?limit={int(limit)}")

    @mcp.tool()
    def kill_audit_pending() -> dict:
        """【只读】列出**待放行**的破坏性动作（先清算超时项再返回，过期项不在此列）。

        条目字段：id、aid、name、action、reason、evidence（真实日志/进程状态/
        心跳年龄快照）、expire_ts。放行前必须先用它核对 evidence 判据。
        代理 GET /api/kill_audit/pending（api_fastapi.py:2165-2172）。
        """
        return _call_api(mw, "/api/kill_audit/pending")

    @mcp.tool()
    def kill_audit_decide(entry_id: str, approve: bool, by: str = "mcp-agent",
                          note: str = "", fix: str = "", dry_run: bool = True) -> dict:
        """【写操作／会放行破坏性动作】对守护审计条目放行或拦截 —— 唯一人工放行口。

        approve=True → 条目置 approved = **放行**该破坏性动作（kill_maa 杀 MAA /
        close_emu 关模拟器 / reclaim_emu 回收 / restart_emu 重启），守护方随后执行；
        approve=False → 拦截。放行前务必先 kill_audit_pending 核对 evidence：
        误杀代价是重启风暴连锁，漏杀只是多等几分钟，宁可多等不可错杀
        （models/kill_audit.py:5-10）。

        默认 dry_run=True：只回显将要提交的决策 + 当前待放行项与目标条目，
        绝不写入；只有显式 dry_run=False 才真正落决策（留痕 by/note/fix）。
        fix 用于误杀复盘（根因+修复）。
        代理 POST /api/kill_audit/decide（api_fastapi.py:2174-2191）。
        """
        body = {"id": entry_id, "approve": bool(approve), "by": by,
                "note": note, "fix": fix}

        def _prev() -> dict:
            data = _call_api(mw, "/api/kill_audit/pending")
            items = data.get("pending") if isinstance(data, dict) else None
            if isinstance(items, list):
                hit = [e for e in items if str(e.get("id")) == str(entry_id)]
                return {"pending_total": len(items),
                        "target_found": bool(hit),
                        "target": hit[0] if hit else None}
            return data

        return _gate("/api/kill_audit/decide", body, dry_run, _prev)

    # ── 模拟器启停/重启（mumu-cli）────────────────────────────────────
    # 对应原 Web「模拟器」按钮，代理 api_fastapi.py 的 /api/emulator* 与
    # /api/connect*（不新增 REST 路由）。索引 idx 即 /api/emulators 里的 index。

    @mcp.tool()
    def emulators() -> dict:
        """【只读】全部模拟器实例：index、名称、running（是否在跑）、adb_port、
        pid 等。启停/重启前先用它确认 index 与当前运行状态。
        代理 GET /api/emulators（api_fastapi.py:1766-1769）。
        """
        return _call_api(mw, "/api/emulators")

    @mcp.tool()
    def emulator_status(idx: str) -> dict:
        """【只读】单台模拟器实例详情（按 index）。不存在时返回
        {"error": "HTTP 404"} 而非抛异常。
        代理 GET /api/emulator/{idx}（api_fastapi.py:1771-1777）。
        """
        return _call_api(mw, f"/api/emulator/{idx}")

    @mcp.tool()
    def emulator_control(idx: str, action: str, dry_run: bool = True) -> dict:
        """【写操作】单台模拟器启停重启：action="start" | "stop" | "restart"（按 index）。

        - start：mumu-cli launch + 等 Android 就绪（40s），并写手动启动保护期；
        - stop / restart：先停该模拟器上的 MAA，再优雅关闭（adb reboot -p →
          等退出 → shutdown 兜底），restart 随后重新 launch 并等就绪。

        默认 dry_run=True：只回显将要执行的动作与该模拟器现状，绝不下发；
        只有显式 dry_run=False 才真正启停。stop/restart 会中断该机上正在跑的任务。
        代理 POST /api/emulator/{idx}/{action}（api_fastapi.py:2380-2431）。
        """
        if action not in ("start", "stop", "restart"):
            return {"error": "action must be 'start', 'stop' or 'restart'"}

        def _prev() -> dict:
            data = _call_api(mw, "/api/emulators")
            items = data.get("emulators") if isinstance(data, dict) else None
            if isinstance(items, list):
                hit = [e for e in items if str(e.get("index")) == str(idx)]
                return {"target": hit[0] if hit else None, "target_found": bool(hit)}
            return data

        return _gate(f"/api/emulator/{idx}/{action}", {}, dry_run, _prev, timeout=180)

    @mcp.tool()
    def emulator_batch(action: str, indexes: list[str] | None = None,
                       dry_run: bool = True) -> dict:
        """【写操作】批量模拟器启停重启：action="start" | "stop" | "restart"。

        indexes 省略/为空时按 REST 默认：start → 全部未运行实例，
        stop/restart → 全部运行实例。串行执行（每台间隔 1.5s）避免 mumu-cli
        并发冲突，批量大时耗时较长（单次调用最长等 15 分钟）。

        默认 dry_run=True：只列出**将要受影响**的 index 清单，绝不下发；
        只有显式 dry_run=False 才真正执行（会中断这些机器上的任务）。
        代理 POST /api/emulator/batch/{action}（api_fastapi.py:2318-2378）。
        """
        if action not in ("start", "stop", "restart"):
            return {"error": "action must be 'start', 'stop' or 'restart'"}
        body = {"indexes": [str(x) for x in (indexes or [])]}

        def _prev() -> dict:
            data = _call_api(mw, "/api/emulators")
            items = data.get("emulators") if isinstance(data, dict) else None
            if not isinstance(items, list):
                return data
            want = set(body["indexes"])
            targets = []
            for e in items:
                i = str(e.get("index", ""))
                if want and i not in want:
                    continue
                running = bool(e.get("running"))
                if action == "start" and running:
                    continue
                if action in ("stop", "restart") and not running:
                    continue
                targets.append(i)
            return {"would_target": targets, "target_count": len(targets)}

        return _gate(f"/api/emulator/batch/{action}", body, dry_run, _prev, timeout=900)

    @mcp.tool()
    def connect_status() -> dict:
        """【只读】连接模式（connect/daily 选配作业）现状：每个在跑模拟器的
        index、名称、adb_port、maa_running（该机 MAA 是否真在跑）、maa_queued、
        mode（该机选配模式）。停/重启连接模式 MAA 前先用它确认 aid=emu{index}。
        代理 GET /api/connect/status（api_fastapi.py:2766-2789）。
        """
        return _call_api(mw, "/api/connect/status")

    @mcp.tool()
    def emu_connect_stop(aid: str, dry_run: bool = True) -> dict:
        """【写操作】停止某台模拟器的**连接模式 MAA**（aid 形如 "emu0"，即
        "emu" + 模拟器 index）。只动 connect_accounts，不影响调度账号。

        默认 dry_run=True：只回显该 aid 现状与将要调用的路径，绝不下发；
        只有显式 dry_run=False 才真正 stop。
        代理 POST /api/connect/{aid}/stop（api_fastapi.py:2825-2829）。
        """
        def _prev() -> dict:
            data = _call_api(mw, "/api/connect/status")
            items = data.get("emulators") if isinstance(data, dict) else None
            if isinstance(items, list):
                hit = [e for e in items if f"emu{e.get('index')}" == aid]
                return {"target": hit[0] if hit else None, "target_found": bool(hit)}
            return data

        return _gate(f"/api/connect/{aid}/stop", {}, dry_run, _prev, timeout=60)

    @mcp.tool()
    def emu_connect_restart(aid: str, mode: str = "connect", dry_run: bool = True) -> dict:
        """【写操作】重启某台模拟器的连接模式 MAA：先 stop，再把 aid 重新入队
        （aid 形如 "emu0"；mode="connect" 空闲待命 | "daily" 跑日常）。
        模拟器必须处于运行中，否则返回 {"ok": False, "error": "模拟器 #x 未运行"}。

        默认 dry_run=True：只回显该 aid 现状与将要提交的 mode，绝不下发；
        只有显式 dry_run=False 才真正重启（会中断该机当前连接会话）。
        代理 POST /api/connect/{aid}/restart（api_fastapi.py:2831-2851）。
        """
        if mode not in ("connect", "daily"):
            return {"error": "mode must be 'connect' or 'daily'"}
        body = {"mode": mode}

        def _prev() -> dict:
            data = _call_api(mw, "/api/connect/status")
            items = data.get("emulators") if isinstance(data, dict) else None
            if isinstance(items, list):
                hit = [e for e in items if f"emu{e.get('index')}" == aid]
                return {"target": hit[0] if hit else None, "target_found": bool(hit)}
            return data

        return _gate(f"/api/connect/{aid}/restart", body, dry_run, _prev, timeout=60)

    # ── MAA 启停 ─────────────────────────────────────────────────────
    # 对应原 Web「启动/停止」按钮与 system 面板，代理 api_fastapi.py 的
    # /api/account/{idx}/launch|stop、/api/action/stop_all、/api/maa/*。
    # 注意：/api/system/kill_maa（直接 taskkill MAA.exe）**故意不暴露**。

    @mcp.tool()
    def maa_status() -> dict:
        """【只读】MAA 运行总览：源 MAA（services/maa/source）是否就绪/版本/是否
        在跑、实例池（services/maa/instances）总数与存活数、更新包下载状态。
        代理 GET /api/maa/status（api_fastapi.py:3161-3197）。
        """
        return _call_api(mw, "/api/maa/status")

    @mcp.tool()
    def maa_instances() -> dict:
        """【只读】MAA 实例池明细（services/maa/instances/<n>/）：每个实例的
        exe 路径、配置、.pid 与进程是否存活。
        代理 GET /api/maa/instances（api_fastapi.py:3232-3262）。
        """
        return _call_api(mw, "/api/maa/instances")

    @mcp.tool()
    def account_launch(idx: int, dry_run: bool = True) -> dict:
        """【写操作】启动指定账号（按 index，0 起）的 MAA 实例（runner.launch）。

        默认 dry_run=True：只回显该账号名与当前是否在跑，绝不下发；
        只有显式 dry_run=False 才真正启动。
        代理 POST /api/account/{idx}/launch（api_fastapi.py:1867-1874）。
        """
        def _prev() -> dict:
            return _call_api(mw, f"/api/account/{int(idx)}/status")

        return _gate(f"/api/account/{int(idx)}/launch", {}, dry_run, _prev, timeout=120)

    @mcp.tool()
    def account_stop(idx: int, dry_run: bool = True) -> dict:
        """【写操作】停止指定账号（按 index，0 起）正在跑的 MAA 实例（runner.stop）。

        默认 dry_run=True：只回显该账号名与当前是否在跑，绝不下发；
        只有显式 dry_run=False 才真正停止（会中断该账号当前任务）。
        代理 POST /api/account/{idx}/stop（api_fastapi.py:1876-1882）。
        """
        def _prev() -> dict:
            return _call_api(mw, f"/api/account/{int(idx)}/status")

        return _gate(f"/api/account/{int(idx)}/stop", {}, dry_run, _prev, timeout=60)

    @mcp.tool()
    def maa_stop_all(dry_run: bool = True) -> dict:
        """【写操作】停止全部在跑的 MAA（调度队列 stop_all）—— 相当于调度台
        "全部停止"按钮，会中断当前所有运行中的账号任务。

        默认 dry_run=True：只回显将被打断的 active 账号清单（读 /api/queue），
        绝不下发；只有显式 dry_run=False 才真正 stop_all。
        代理 POST /api/action/stop_all（api_fastapi.py:2149-2153）。
        """
        def _prev() -> dict:
            data = _call_api(mw, "/api/queue")
            if isinstance(data, dict):
                return {"active": data.get("active", []),
                        "active_count": data.get("active_count"),
                        "pending_count": data.get("pending_count")}
            return data

        return _gate("/api/action/stop_all", {}, dry_run, _prev, timeout=120)

    @mcp.tool()
    def maa_launch_source(dry_run: bool = True) -> dict:
        """【写操作】启动**源 MAA.exe**（services/maa/source/MAA.exe，正常窗口）：
        用于人工完成首次初始化（协议/热更新）后再关闭窗口。MAA 已在运行或源未
        安装时 REST 会返回 {"ok": False, "error": ...}。

        默认 dry_run=True：只回显源 MAA 就绪/运行现状（读 /api/maa/status），
        绝不下发；只有显式 dry_run=False 才真正拉起窗口。
        代理 POST /api/maa/launch_source（api_fastapi.py:3199-3216）。
        """
        def _prev() -> dict:
            data = _call_api(mw, "/api/maa/status")
            if isinstance(data, dict):
                return {"source_ready": data.get("source_ready"),
                        "source_version": data.get("source_version"),
                        "source_running": data.get("source_running")}
            return data

        return _gate("/api/maa/launch_source", {}, dry_run, _prev, timeout=30)

    # ── 挂载 ─────────────────────────────────────────────────────────

    # http_app(path="/")：内部路由注册在根，挂载前缀（/mcp）由 FastAPI mount 决定
    return _TokenAuth(mcp.http_app(path="/", transport="streamable-http"), mw)
