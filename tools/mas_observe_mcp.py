# -*- coding: utf-8 -*-
r"""mas-observe MCP —— 巡检用「只读观察面」：9 个只读工具，全部包只读 CLI。

背景（2026-09-27，kanban t_6cfe7865 / docs/MCP_TOOL_ROADMAP.md §3.2 冻结契约）：
巡检 agent（cron job 52b88b0ffca0）看得到"谁失败、有没有漏发"，但看不到"现场什么样、
该不该补、是不是串号"。本 server 暴露 5 个**只读**工具补上这块：

  mas_preflight(queue|script, skip_screen=True)  班次前 GO/NO-GO（6 项检查，~30s/全量 40-60s）
  mas_report(date?, summary_only?, include_shift?, write_report?)  每日错误聚合 +（可选）班次块
  mas_idle_emulators(aggressive?, with_fg?)      空转实例收敛**清单**（只出建议，不执行关闭）
  mas_client_state(serial|idx|image|list|无参数) 客户端状态（停在哪/有没有挡路弹窗；不点击）
  mas_identity_trail(since?, until?, targets?, …) 每轮身份留痕对照（配置期望 vs 实测掩码/UID）
  mas_interrupt_guard(idx?, max?, …)            打断源治理（客户端版本变化 / 系统重启窗口）
  event_calendar(days?, window_hours?)          周期性事件到点提醒（UPCOMING=：周切/活动/商店截止/宿主重启）
  mas_kanban_health(board?, json?)              看板治理体检一行（KANBAN_HEALTH=：重复/僵尸/长期 blocked/崩溃超限/孤儿产物）
  audit_waterline()                             统一审计流（runtime/audit/）水位判据一行（AUDIT_STATUS=：OK|WARN|CRIT；
                                                等价 tools/audit_waterline.py，卡 t_537fbff2）

写操作：**无**（不发车、不动实例、不改配置、不 kill；远端全只读）。
本机可写的东西只有：runtime/observe/ 下的临时产物（截图/json）、mas_report(write_report=true)
时的 runtime/reports/maa_errors_<date>.{md,json}、mas_identity_trail(out=…)。默认都不落盘。
audit_waterline 只判不治：**永不** rotate/prune（清理永远是人拍板，见 docs/MAS_AUDIT_SCHEMA.md §9）。

包装契约（与 mas_dispatch_mcp / mas_triage_mcp 同规）：
  1. 每个工具 = 一次 `subprocess.run([python, <CLI>, …], stdin=DEVNULL, capture_output=True, timeout=…)`；
     优先 `--json`；解析失败 ⇒ {ok:false, error:{kind:"parse_error", raw_head}}。
  2. **stdin=DEVNULL 是硬要求**（教训在案 t_ae836e86：ssh 继承 MCP stdin 管道会挂死到超时）。
  3. 统一错误鞘 {ok:false, error:{kind: upstream_unreachable|timeout|parse_error|bad_args|unknown, detail}}；
     **不重试**（幂等；重试交给巡检下一跳）。
  4. 每工具显式超时：preflight 120s(skip)/300s(全量)、report 420s、idle 240s、
     client_state 240s、identity 420s；server 侧 timeout:900 兜底（config.yaml）。
  5. 输出账号一律掩码（掩码口径：前 2 + *** + 后 2）；不落原始 uuid / 完整昵称。

用法：
  python tools/mas_observe_mcp.py            # stdio MCP server（Hermes 用 command+args 拉起）
  python tools/mas_observe_mcp.py --check    # 自检：CLI 存在性 + selftest + 只读断言，退出码 0/2
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS_DIR)
RUNTIME = os.path.join(ROOT, "runtime")
OBSERVE_DIR = os.path.join(RUNTIME, "observe")
REPORTS_DIR = os.path.join(RUNTIME, "reports")

CLI_PREFLIGHT = os.path.join(TOOLS_DIR, "mas_preflight.py")
CLI_ERRDAILY = os.path.join(TOOLS_DIR, "maa_error_daily.py")
CLI_IDLE = os.path.join(TOOLS_DIR, "mas_idle_emulators.py")
CLI_CLIENT = os.path.join(TOOLS_DIR, "client_state_check.py")
CLI_IDENTITY = os.path.join(TOOLS_DIR, "mas_identity_trace.py")
CLI_INTERRUPT = os.path.join(TOOLS_DIR, "interrupt_guard.py")
CLI_EVENTCAL = os.path.join(TOOLS_DIR, "event_calendar.py")
CLI_AUDIT_WL = os.path.join(TOOLS_DIR, "audit_waterline.py")
CLI_KANBAN_HEALTH = os.path.join(TOOLS_DIR, "kanban_health.py")

PY = sys.executable  # MCP server 由 Python312 拉起（config: command=Python312\python.exe）
SERVER_NAME = "mas-observe"
_TS_FMT = "%Y-%m-%d %H:%M:%S"

_SCHEMAS = [
    types.Tool(
        name="mas_preflight",
        description=("【只读】班次前 GO/NO-GO 自检（等价 tools/mas_preflight.py）：6 项检查 = REST 可达 / 目标槽位空闲 / "
                     "目标实例可连 / 客户端无残留模态（截图+OCR，可跳过）/ 注入闸门 / 磁盘水位。"
                     "入参 queue（日常定时N/晚班定时N）与 script（如 明日方舟-官-1-1-7）二选一必填。"
                     "skip_screen=true（MCP 默认）跳过第 4 项截图+OCR（约 30s），false=全量（+20~40s）。"
                     "返回 {ok, preflight:\"GO\"|\"NO-GO\"|\"UNKNOWN\", checks{}, reasons[], failing_checks[]}。"
                     "NGO/NO-GO 都是有效结论（ok=true）；目标无法解析或超时 ⇒ ok=false 且 preflight=\"UNKNOWN\"（等同 NO-GO，fail-closed）。"
                     "补位发车前调用；PREFLIGHT≠GO 时不发车、只告警。只读：不启动/不关闭实例、不改配置、不发车。"),
        inputSchema={"type": "object",
                     "properties": {
                         "queue": {"type": "string", "description": "队列名（如 晚班定时1）"},
                         "script": {"type": "string", "description": "脚本名（如 明日方舟-官-1-1-7）"},
                         "skip_screen": {"type": "boolean", "default": True,
                                         "description": "true=跳过截图+OCR 检查（默认，快）；false=全量"},
                         "min_free_gb": {"type": "number", "default": 10, "description": "磁盘 FAIL 阈值(GB)"},
                         "warn_free_gb": {"type": "number", "default": 30, "description": "磁盘 WARN 阈值(GB)"},
                         "timeout": {"type": "integer", "description": "工具超时预算(秒)；缺省 skip=120 / 全量=300"},
                     },
                     "additionalProperties": False},
    ),
    types.Tool(
        name="mas_report",
        description=("【只读】每日错误聚合 + 班次摘要（等价 tools/maa_error_daily.py + 本机发车台账）。"
                     "四源（history maa_result/同名.log + MAA gui/asst 日志 + AUTO-MAS app.log + 只读 REST）"
                     "聚合当日错误，返回 `err_summary=\"ERR_SUMMARY=<n> 条，Top=<类型>\"` + counts（致命/告警/可忽略、分类计数）"
                     "+ items（≤30 条，账号掩码）+ shift 块（今日已发/到点未发/告警台账）。"
                     "date 缺省=今天（本地 <05:00 自动取昨天，覆盖夜班尾巴）；summary_only=true 只回摘要行与计数。"
                     "耗时约 30-90s；数据源不可达 ⇒ ok=false + err_summary=\"ERR_SUMMARY=UNKNOWN:<原因>\"（fail-closed）。"
                     "巡检每轮应调用（替代只看失败名单）；write_report=true 时落盘 runtime/reports（默认 false）。"),
        inputSchema={"type": "object",
                     "properties": {
                         "date": {"type": "string", "description": "YYYY-MM-DD；缺省=今天（本地 <05:00 取昨天）"},
                         "summary_only": {"type": "boolean", "default": False,
                                          "description": "true=只回 ERR_SUMMARY+counts（省上下文）"},
                         "include_shift": {"type": "boolean", "default": True,
                                           "description": "附班次/发车摘要（已发/到点未发/告警台账）"},
                         "write_report": {"type": "boolean", "default": False,
                                          "description": "true=落盘 runtime/reports/maa_errors_<date>.{md,json}"},
                         "timeout": {"type": "integer", "description": "远端扫描超时(秒)，默认 420"},
                     },
                     "additionalProperties": False},
    ),
    types.Tool(
        name="mas_idle_emulators",
        description=("【只读】空转实例收敛清单（等价 tools/mas_idle_emulators.py）：扫出\"跑着、没有 MAA 在用、"
                     "也不属于下一班/补跑复用\"的 MuMu 实例，只出建议关闭清单 + 建议命令，**不执行关闭**。"
                     "返回 {ok, counts:{running,in_use,needed,candidates}, next_shift, candidates[], "
                     "suggested_commands[], rows[…]}。aggressive=true 扩展口径（空闲即候选，代价=下班冷启）；"
                     "with_fg=true 附前台包（adb 只读，与其它 adb 任务错开时再用）。耗时约 60-120s；"
                     "上游不可达 ⇒ ok=false 且不给关闭建议。巡检写 POWER= 行时：空转数=counts.candidates，建议=suggested_commands。"),
        inputSchema={"type": "object",
                     "properties": {
                         "aggressive": {"type": "boolean", "default": False,
                                        "description": "扩展口径：空闲即候选（代价=下班冷启）"},
                         "with_fg": {"type": "boolean", "default": False, "description": "附前台包（adb 只读）"},
                         "timeout": {"type": "integer", "description": "超时(秒)，默认 240"},
                     },
                     "additionalProperties": False},
    ),
    types.Tool(
        name="mas_client_state",
        description=("【只读】客户端状态检测（等价 tools/client_state_check.py）：判定实例停在哪个界面/有没有挡路弹窗"
                     "（模态:登录被取消/更新提示/公告/活动页/退出确认/账号管理残留/世界观光卡/桌面/正常/未知）+ ACTION 安全处置建议。"
                     "只截图/读图 + OCR，不点击、不按键。四种用法：① serial=远端 adb 序列号（默认经 ssh 在产线主机截图；local=true 走本机 adb）；"
                     "② idx=实例下标（1..N，可逗号分隔 ≤6，自动从 /api/emulator/status 解析 adb 地址）；"
                     "③ image/list=离线图片路径；④ 不给参数=抽查产线在跑实例（MuMuVMMHeadless 进程口径，默认最多 4 台）。"
                     "返回 {ok, mode, counts, instances:[{idx?,serial,state,action,evidence,shot}], abnormal[], summary}。"
                     "读不到/判不了 ⇒ state=\"未知\"（fail-closed，绝不返回\"正常\"）。巡检写 CLIENT_STATE= 行时用 summary（异常台数+状态）。"),
        inputSchema={"type": "object",
                     "properties": {
                         "serial": {"type": "string", "description": "adb 序列号（如 127.0.0.1:7555）"},
                         "idx": {"type": "string", "description": "实例下标（单个或逗号分隔，如 \"13\" 或 \"13,14\"，≤6）"},
                         "image": {"type": "string", "description": "离线判定单张截图路径"},
                         "list": {"type": "string", "description": "批量清单文件（每行一个图片路径）"},
                         "local": {"type": "boolean", "default": False,
                                   "description": "true=本机 adb 直连（默认 false=经 ssh 在产线主机截图）"},
                         "max": {"type": "integer", "default": 4, "description": "无参数抽查时的台数上限（≤6）"},
                         "timeout": {"type": "integer", "description": "超时(秒)，默认 240"},
                     },
                     "additionalProperties": False},
    ),
    types.Tool(
        name="mas_identity_trail",
        description=("【只读】每轮身份留痕对照（等价 tools/mas_identity_trace.py report）：把 gui.log 的「账号切换 -->> <掩码>」行"
                     "与每轮大厅/标题帧 OCR（ID:<UID>/玩家掩码）对照\"期望账号 vs 实测账号\"，任何一轮错配=红色。"
                     "返回 {ok, red_rounds, scripts:{<脚本>:{maa_dir, rounds:[{user(mask), start, done, switch, observed_mask, "
                     "observed_uid, verdict, color}]}}, orphans_n, report_path?}。red_rounds>0 仍是 ok=true（检测成功），"
                     "调用方按其处置（写 IDENTITY= 行：red_rounds=0⇒一致，>0⇒不一致，无轮次/无证据⇒无法判定）。"
                     "缺省 targets=两个多号脚本（官-1-1-7=MAA_6:13;官-2-4-47=MAA_2:14），since 缺省=今天。"
                     "耗时 30-120s（ssh 拉日志；有帧 OCR 索引时更准）。只读：不写远端、不发车、不改配置。"),
        inputSchema={"type": "object",
                     "properties": {
                         "since": {"type": "string", "description": "YYYY-MM-DD（缺省=今天）"},
                         "until": {"type": "string", "description": "YYYY-MM-DD（缺省=since+1）"},
                         "targets": {"type": "string", "description": "脚本=MAA目录:实例，分号分隔"},
                         "ocr_index": {"type": "string", "description": "帧 OCR 索引 json 路径（可选）"},
                         "frames_manifest": {"type": "string", "description": "帧清单（可选）"},
                         "out": {"type": "string", "description": "报告 md 落盘路径（可选）"},
                         "timeout": {"type": "integer", "description": "超时(秒)，默认 420"},
                     },
                     "additionalProperties": False},
    ),
    types.Tool(
        name="mas_interrupt_guard",
        description=("【只读】打断源治理（等价 tools/interrupt_guard.py --json）：检测两类会打断跑批的事件 —— "
                     "① 客户端版本：运行中实例 ARK 包 versionName/lastUpdateTime 与基线（runtime/interrupt_guard_state.json）比对，"
                     "变化 ⇒ 告警 + 建议暂停该实例发车（避免半途更新打断）；"
                     "② 系统重启：宿主/本机的重启类计划任务（执行 /r 或 Restart-Computer）、待重启标记（PFR/CBS/WU）、在飞 MAA 数 —— "
                     "≤15 分钟即刻重启、≤45 分钟且宿主在飞 MAA>0 ⇒ 高风险；待重启标记、重启落班次窗口 ⇒ 提示级。"
                     "返回 {ok, interrupt_risk:\"无|客户端更新|系统重启|客户端更新+系统重启|UNKNOWN:<原因>\", risk_line, "
                     "reasons[], version{running_n,rows[],changed[]}, restart{machines{},next[],signals[]}}。"
                     "巡检写 INTERRUPT_RISK= 行时直接引用 risk_line（或 interrupt_risk）；非「无」⇒ 只告警、不强行发车。"
                     "只读：不改计划任务、不动客户端、不发车、不落盘（版本基线只由 CLI --record 更新）。"
                     "耗时约 15-60s（ssh+adb，取决于运行中实例数）；宿主不可达 ⇒ ok=false 且 interrupt_risk=UNKNOWN:…（fail-closed）。"),
        inputSchema={"type": "object",
                     "properties": {
                         "idx": {"type": "string", "description": "版本面实例下标（逗号分隔；缺省=所有运行中实例）"},
                         "max": {"type": "integer", "default": 6, "description": "版本面最多查几台运行中实例"},
                         "scope": {"type": "string", "default": "host,local",
                                   "description": "重启面范围：host / local / host,local"},
                         "version_only": {"type": "boolean", "default": False, "description": "只查客户端版本面"},
                         "restart_only": {"type": "boolean", "default": False, "description": "只查重启面"},
                         "timeout": {"type": "integer", "description": "超时(秒)，默认 300"},
                     },
                     "additionalProperties": False},
    ),
    types.Tool(
        name="event_calendar",
        description=("【只读】周期性事件到点提醒（等价 tools/event_calendar.py --check）：未来 days 天内的"
                     "周切（剿灭周额度重置/周任务）、活动开/关、商店与奖励截止（快照 note 抽取，BJT）、"
                     "资源关轮换、宿主重启窗口 —— 按 window_hours 窗口升序给 `UPCOMING=`（最多 6 项 + 「等另有 N 项」）。"
                     "返回 {ok, event_cal, upcoming, event_advice, exit_code, elapsed_s}；巡检写 `UPCOMING=…` 行时"
                     "直接用返回的 upcoming（值不含 `UPCOMING=` 前缀）。数据不可用（快照缺失/损坏/工具缺）⇒ ok=false 且 "
                     "upcoming=\"UNKNOWN:<原因>\"（fail-closed，**不许当「无事件」用**）。"
                     "只读：`--check` 零写盘、不发车、不改配置。耗时约 2-10s。"),
        inputSchema={"type": "object",
                     "properties": {
                         "days": {"type": "integer", "default": 14,
                                  "description": "日历天数（1..60，默认 14）"},
                         "window_hours": {"type": "integer", "default": 48,
                                          "description": "UPCOMING 提醒窗口小时数（1..336，默认 48）"},
                         "timeout": {"type": "integer", "description": "超时(秒)，默认 60"},
                     },
                     "additionalProperties": False},
    ),
    types.Tool(
        name="mas_kanban_health",
        description=("【只读】看板治理体检（等价 tools/kanban_health.py，t_67915887 产物）：扫两块板 kanban.db，把"
                     "「重复卡 / 僵尸卡 / 长期 blocked / 崩溃超限 / 孤儿产物 / 板级并发」压成一行 `KANBAN_HEALTH=`"
                     "（status=OK/WARN/CRIT；行内每类计数=P1·P2 需动作项，P3 只进 `P3=` 汇总）。"
                     "默认返回该行；json=true 时附机读对象（boards[].findings[] 每条含 cat/code/sev/title/detail/action/cmd，"
                     "体量大，仅在需要 P1/P2 标题清单时再用）。只读硬闸：只跑 --line/--json/--no-write，不写盘、不改卡、"
                     "不归档、不重派（建议动作归 lead，见 docs/KANBAN_DISPATCH_POLICY.md §4/§6）。耗时约 3-20s"
                     "（纯 sqlite + 本机产物路径核验，不连远端）。工具面未提供 ⇒ 写 UNKNOWN，不许顶替。"),
        inputSchema={"type": "object",
                     "properties": {
                         "board": {"type": "string",
                                   "description": "只扫指定板 slug（可逗号分隔多个，如 maa-parallel-benchmark-20260926；缺省=全部板）"},
                         "json": {"type": "boolean", "default": False,
                                  "description": "true=附机读对象（含 findings 明细：P1/P2 标题清单等）"},
                     },
                     "additionalProperties": False},
    ),
    types.Tool(
        name="audit_waterline",
        description=("【只读】统一审计流（runtime/audit/）水位判据（等价 tools/audit_waterline.py，卡 t_537fbff2）："
                     "把 `live 体积 / 归档份数 / 覆盖小时数 / 轮转状态` 压成一行 `AUDIT_STATUS=OK|WARN|CRIT`。"
                     "判据（与 §7.4 同口径）：live>10MiB ⇒ WARN；live>25MiB 或 归档缺失而 live>8MiB ⇒ CRIT；"
                     "覆盖 ≥24h 且该轮转未轮转 ⇒ WARN（mas_audit.status 自身 WARN 不降级）。"
                     "返回 {ok, audit_status, verdict, reasons[], live_mb, archives_n, report_hours, rotate_now, elapsed_s}。"
                     "巡检写 `AUDIT_STATUS=…` 行时直接用 audit_status；**只判不治**：本工具永不 rotate/prune"
                     "（清理永远是人拍板）；<1s、无网络、零业务写入。"), 
        inputSchema={"type": "object",
                     "properties": {"timeout": {"type": "integer", "description": "超时(秒)，默认 60"}},
                     "additionalProperties": False},
    ),
]

_KNOWN_TOOLS = ["mas_preflight", "mas_report", "mas_idle_emulators", "mas_client_state", "mas_identity_trail",
                "mas_interrupt_guard", "event_calendar", "mas_kanban_health", "audit_waterline"]


# ---------------------------------------------------------------- 基础工具

def _text(payload) -> list:
    return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=1))]


def _err(kind: str, detail, **extra) -> dict:
    d = {"ok": False, "error": {"kind": kind, "detail": str(detail)[:400]}}
    d.update(extra)
    return d


_WIN_ENV_DEFAULTS = {
    "ComSpec": r"C:\WINDOWS\System32\cmd.exe",
    "windir": r"C:\WINDOWS",
    "OS": "Windows_NT",
    "ProgramData": r"C:\ProgramData",
    "ProgramFiles": r"C:\Program Files",
    "ProgramFiles(x86)": r"C:\Program Files (x86)",
    "ProgramW6432": r"C:\Program Files",
    "CommonProgramFiles": r"C:\Program Files\Common Files",
    "ALLUSERSPROFILE": r"C:\ProgramData",
    "SystemDrive": "C:",
    "SESSIONNAME": "Console",
    "NUMBER_OF_PROCESSORS": "16",
    "PROCESSOR_IDENTIFIER": "AMD64",
    "USERDOMAIN": "DESKTOP",
}


def _registry_env() -> dict:
    """机器/用户级环境变量（HKLM Session Manager\\Environment + HKCU\\Environment）。"""
    out = {}
    if os.name != "nt":
        return out
    try:
        import winreg
    except ImportError:
        return out
    for hive, path in ((winreg.HKEY_LOCAL_MACHINE,
                        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                       (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(hive, path) as k:
                for i in range(winreg.QueryInfoKey(k)[1]):
                    try:
                        name, val, _ = winreg.EnumValue(k, i)
                    except OSError:
                        continue
                    if isinstance(val, str):
                        # nt 环境块大小写不敏感：统一大写键名，避免 'Path' 与 'PATH' 双键
                        out[name.upper()] = os.path.expandvars(val)
        except OSError:
            pass
    return out


def _env() -> dict:
    """子进程环境。**必须自给自足**：MCP stdio 服务器被 SDK 用极简 env 拉起
    （mcp 的 get_default_environment() 只给 12 个键：APPDATA/HOMEDRIVE/HOMEPATH/
    LOCALAPPDATA/PATH/PATHEXT/PROCESSOR_ARCHITECTURE/SYSTEMDRIVE/SYSTEMROOT/TEMP/
    USERNAME/USERPROFILE）。此 env 下 **ssh.exe 会 0.1s 内静默 rc=255、stdout/stderr
    全空、连 `-v` 都没有输出**——这就是「SSH 采集失败（无 __JSON__ 标记）: (空)」
    「SSH 上传未确认: 」这类空错误的根因（t_6cfe7865 实测；同一命令在 bash 全量 env 下
    5.7s rc=0）。实测补齐 {ComSpec, windir, OS, ProgramData, ProgramFiles(+x86),
    ProgramW6432, CommonProgramFiles, ALLUSERSPROFILE, SystemDrive, SESSIONNAME,
    NUMBER_OF_PROCESSORS, PROCESSOR_IDENTIFIER, USERDOMAIN, TMP} 后恢复正常
    （单补 ComSpec / windir / SystemDrive / HOME 均不够，必须是这一组）。
    另合并 registry 环境（用户真实环境，含 PATH/TEMP 等权威值），最后兜底 PATH。

    2026-09-28 二次修复（t_6cfe7865 实测）：registry 键名若保留原大小写（'Path'），会与
    os.environ 的 'PATH' 形成大小写双键；Windows 环境块大小写不敏感，子进程实际生效的是
    “后写键”=registry 用户版 Path（不含 System32/OpenSSH）⇒ 裸名 `ssh` 直接 WinError 2
    （mas_idle 0.2s 空失败的根因）。现统一大写键名、PATH 两段显式合并（继承+registry），
    并把 System32\\OpenSSH 前置，确保裸名 `ssh` 解析到 Windows 自带 OpenSSH。
    """
    env = dict(os.environ)
    reg = _registry_env()
    if os.name == "nt":
        # Windows 环境块大小写不敏感：'PATH'(继承) 与 'Path'(registry) 双写时子进程实际
        # 生效“后写键”，会拿到无 System32/OpenSSH 的那份（t_6cfe7865 实测：ssh WinError 2）。
        # 统一大写键；PATH 两段显式合并（继承在前、registry 去重后追加）。
        env = {k.upper(): v for k, v in env.items()}
        if "PATH" in reg:
            merged, seen = [], set()
            for seg in (env.get("PATH") or "", reg["PATH"]):
                for p in seg.split(";"):
                    low = p.rstrip("\\").lower()
                    if p and low not in seen:
                        seen.add(low)
                        merged.append(p)
            env["PATH"] = ";".join(merged)
            reg = {k: v for k, v in reg.items() if k != "PATH"}
    env.update(reg)
    env.update({k.upper(): v for k, v in _WIN_ENV_DEFAULTS.items()} if os.name == "nt" else _WIN_ENV_DEFAULTS)
    C = (lambda s: s.upper()) if os.name == "nt" else (lambda s: s)
    win = env.get(C("SystemRoot")) or env.get(C("windir")) or r"C:\Windows"
    env[C("SystemRoot")] = win
    env.setdefault(C("windir"), win)
    env.setdefault(C("TEMP"), os.path.join(win, "Temp"))
    env.setdefault(C("TMP"), env[C("TEMP")])
    env.setdefault(C("ComSpec"), os.path.join(win, "System32", "cmd.exe"))
    env.setdefault(C("PATHEXT"), ".COM;.EXE;.BAT;.CMD")
    home = env.get(C("USERPROFILE")) or os.path.expanduser("~")
    if not home or home == "~":
        home = r"C:\Users\xiach"
    env.setdefault(C("USERPROFILE"), home)
    env.setdefault(C("HOME"), home)
    env.setdefault(C("USERNAME"), "xiach")
    must = [os.path.join(win, "System32"), os.path.join(win, "System32", "OpenSSH"),
            os.path.join(win, "System32", "Wbem"), win,
            r"C:\platform-tools-latest-windows\platform-tools"]
    parts = [p for p in (env.get(C("PATH")) or "").split(";") if p and os.path.isdir(p)]
    lowered = [p.rstrip("\\").lower() for p in parts]
    for d in must:
        if os.path.isdir(d) and d.rstrip("\\").lower() not in lowered:
            parts.append(d)
    # 前置 System32\OpenSSH：确保裸名 `ssh` = Windows 自带 OpenSSH（而非 PATH 里可能更靠前的
    # git-bash ssh；maa_error_daily 的实测教训：git ssh 对 `-i C:\…` 路径参数不可靠）。
    oss = os.path.join(win, "System32", "OpenSSH")
    if os.path.isdir(oss):
        low_oss = oss.rstrip("\\").lower()
        parts = [oss] + [p for p in parts if p.rstrip("\\").lower() != low_oss]
    env[C("PATH")] = ";".join(parts)
    env[C("PYTHONUTF8")] = "1"
    env[C("PYTHONIOENCODING")] = "utf-8"
    return env


def _run(argv: list, timeout: int) -> tuple:
    """一次 CLI 调用。stdin=DEVNULL 是硬要求（禁止继承 MCP 管道）。返回 (rc, stdout, stderr)。"""
    r = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL,
                       timeout=timeout, cwd=ROOT, env=_env())
    return (r.returncode, (r.stdout or b"").decode("utf-8", "replace"),
            (r.stderr or b"").decode("utf-8", "replace"))


def _mask(name: str) -> str:
    """账号掩码：前 2 + *** + 后 2（短名只留首字符）。"""
    s = str(name or "")
    if not s or s == "?":
        return s or "?"
    if len(s) <= 4:
        return s[0] + "***"
    return s[:2] + "***" + s[-2:]


def _eff_date(date_s: str | None) -> str:
    """与 maa_error_daily.resolve_date 同规则：缺省=今天；本地 <05:00 取昨天。"""
    if date_s:
        return date_s
    now = dt.datetime.now()
    d = now.date() if now.hour >= 5 else now.date() - dt.timedelta(days=1)
    return d.isoformat()


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------- mas_preflight

def tool_preflight(a: dict) -> dict:
    queue, script = str(a.get("queue") or "").strip(), str(a.get("script") or "").strip()
    if bool(queue) == bool(script):
        return _err("bad_args", "queue 与 script 必须二选一（且只能给一个）", preflight="UNKNOWN")
    skip = bool(a.get("skip_screen", True))
    try:
        min_gb = float(a.get("min_free_gb", 10) or 10)
        warn_gb = float(a.get("warn_free_gb", 30) or 30)
    except (TypeError, ValueError):
        return _err("bad_args", "min_free_gb/warn_free_gb 必须是数字", preflight="UNKNOWN")
    try:
        tmo = int(a.get("timeout") or (120 if skip else 300))
    except (TypeError, ValueError):
        tmo = 120 if skip else 300
    cmd = [PY, CLI_PREFLIGHT, "--json", "--min-free-gb", str(min_gb), "--warn-free-gb", str(warn_gb),
           "--timeout", str(max(30, tmo))]
    cmd += ["--queue", queue] if queue else ["--script", script]
    if skip:
        cmd += ["--skip-screen"]
    t0 = time.time()
    try:
        rc, out, err = _run(cmd, timeout=tmo + 30)
    except subprocess.TimeoutExpired:
        return _err("timeout", "mas_preflight 超时 %ss（未返回 GO）" % (tmo + 30), preflight="UNKNOWN",
                    elapsed_s=round(time.time() - t0, 1))
    except Exception as e:  # noqa: BLE001
        return _err("unknown", "%s: %s" % (type(e).__name__, e), preflight="UNKNOWN")
    elapsed = round(time.time() - t0, 1)
    try:
        res = json.loads(out)
    except Exception:  # noqa: BLE001
        # rc=2（无法判定：目标解析失败/远端不可达）时 CLI 无 JSON，未输出以 stderr 说明
        return _err("upstream_unreachable" if rc == 2 else "parse_error",
                    (err or out)[-400:], preflight="UNKNOWN", exit_code=rc, elapsed_s=elapsed)
    res["ok"] = rc in (0, 1)  # 1=NO-GO 是有效结论
    res["exit_code"] = rc
    res["elapsed_s"] = elapsed
    if not res["ok"]:
        res.setdefault("error", {"kind": "unknown", "detail": "exit=%s" % rc})
    return res


# ---------------------------------------------------------------- mas_report

def _shift_block() -> dict:
    """班次/发车摘要（本机 runtime + tools/mas_dispatch.py 只读口径；不触发任何发车）。"""
    out = {}
    try:
        sys.path.insert(0, TOOLS_DIR)
        import mas_dispatch as md  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": "%s: %s" % (type(e).__name__, e)}
    today = dt.date.today().isoformat()
    try:
        reach, why = md.reachable()
        out["reachable"] = reach
        out["reach_detail"] = why
    except Exception as e:  # noqa: BLE001
        out["reachable"], out["reach_detail"] = False, "%s: %s" % (type(e).__name__, e)
    try:
        state = md.load_state()
        sent = sorted((state.get(today) or {}).keys())
        out["sent_today_n"], out["sent_today"] = len(sent), sent
        table = md.schedule()
        now = dt.datetime.now()
        due, late = [], []
        for name, hhmm in sorted(table.items()):
            if name in sent:
                continue
            hh, mm = hhmm.split(":")
            due_at = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            if now < due_at:
                continue
            (late if (now - due_at).total_seconds() > md.GRACE_MIN * 60 else due).append("%s@%s" % (name, hhmm))
        out["due_not_sent"], out["late_beyond_grace"] = due, late
    except Exception as e:  # noqa: BLE001
        out["state_error"] = "%s: %s" % (type(e).__name__, e)
    try:
        alerts = []
        for a in md.active_alerts():
            alerts.append({"queue": a.get("queue"), "reason": a.get("reason"), "ts": a.get("ts"),
                           "attempts": a.get("attempts"), "detail": (a.get("detail") or "")[:160]})
        out["alerts"], out["alerts_n"] = alerts, len(alerts)
    except Exception as e:  # noqa: BLE001
        out["alerts"], out["alerts_n"] = [], None
        out["alerts_error"] = "%s: %s" % (type(e).__name__, e)
    return out


def _trim_item(it: dict) -> dict:
    keep = ("slot", "ts", "src", "cat", "sev", "chain", "note", "script")
    row = {k: it.get(k) for k in keep if it.get(k) not in (None, "")}
    if it.get("account"):
        row["account"] = _mask(it["account"])
    if it.get("raw"):
        row["raw"] = str(it["raw"])[:160]
    return row


def tool_report(a: dict) -> dict:
    date_s = str(a.get("date") or "").strip() or None
    if date_s and not _DATE_RE.match(date_s):
        return _err("bad_args", "date 必须是 YYYY-MM-DD：%r" % date_s)
    summary_only = bool(a.get("summary_only", False))
    include_shift = bool(a.get("include_shift", True))
    write_report = bool(a.get("write_report", False))
    try:
        tmo = int(a.get("timeout") or 420)
    except (TypeError, ValueError):
        tmo = 420
    cmd = [PY, CLI_ERRDAILY, "--json", "--timeout", str(tmo)]
    if date_s:
        cmd += ["--date", date_s]
    if not write_report:
        cmd += ["--no-write"]
    else:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        cmd += ["--out", os.path.join(REPORTS_DIR, "maa_errors_%s.md" % _eff_date(date_s))]
    t0 = time.time()
    try:
        rc, out, err = _run(cmd, timeout=tmo + 90)
    except subprocess.TimeoutExpired:
        return _err("timeout", "mas_report 超时 %ss" % (tmo + 90),
                    err_summary="ERR_SUMMARY=UNKNOWN:timeout(%ss)" % (tmo + 90),
                    elapsed_s=round(time.time() - t0, 1))
    except Exception as e:  # noqa: BLE001
        return _err("unknown", "%s: %s" % (type(e).__name__, e),
                    err_summary="ERR_SUMMARY=UNKNOWN:%s" % type(e).__name__)
    elapsed = round(time.time() - t0, 1)
    try:
        data = json.loads(out)
    except Exception:  # noqa: BLE001
        # rc=2：数据源不可达，stdout 只有 ERR_SUMMARY=UNKNOWN:<原因> 行
        line = ""
        for ln in (out or "").splitlines():
            if ln.startswith("ERR_SUMMARY="):
                line = ln.strip()
        if line:
            return {"ok": False, "err_summary": line, "exit_code": rc, "elapsed_s": elapsed,
                    "error": {"kind": "upstream_unreachable", "detail": (err or out)[-300:]}}
        return _err("parse_error", (err or out)[:400], err_summary="ERR_SUMMARY=UNKNOWN:parse_error",
                    exit_code=rc, elapsed_s=elapsed)
    c = data.get("counts") or {}
    payload = {"ok": rc == 0, "tool": "mas_report", "date": data.get("date"), "window": data.get("window"),
               "generated_at": data.get("generated_at"),
               "err_summary": "ERR_SUMMARY=%d 条，Top=%s" % (c.get("n", -1), c.get("top", "?")),
               "counts": c, "elapsed_s": elapsed}
    if not payload["ok"]:
        payload["error"] = {"kind": "upstream_partial", "detail": (err or out)[-300:]}
    if summary_only:
        if include_shift:
            payload["shift"] = _shift_block()
        return payload
    payload["items"] = [_trim_item(it) for it in (data.get("items") or [])[:30]]
    h = data.get("hist") or {}
    payload["hist"] = {"accounts_n": len(h.get("accounts") or []), "attempts": h.get("attempts"),
                       "ok": h.get("ok"), "fail": h.get("fail"), "rows_n": len(h.get("rows") or [])}
    payload["slots_n"] = len(data.get("slots") or [])
    payload["app"] = data.get("app")
    payload["degraded"] = {"rest_ok": (data.get("meta") or {}).get("rest_ok"),
                           "ssh": bool((data.get("meta") or {}).get("ssh_host"))}
    if write_report:
        base = os.path.join(REPORTS_DIR, "maa_errors_%s" % (data.get("date") or _eff_date(date_s)))
        payload["report"] = {"md": base + ".md", "json": base + ".json"}
    if include_shift:
        payload["shift"] = _shift_block()
    return payload


# ---------------------------------------------------------------- mas_idle_emulators

def tool_idle(a: dict) -> dict:
    try:
        tmo = int(a.get("timeout") or 240)
    except (TypeError, ValueError):
        tmo = 240
    cmd = [PY, CLI_IDLE, "--json", "--timeout", str(tmo)]
    if a.get("aggressive"):
        cmd += ["--aggressive"]
    if a.get("with_fg"):
        cmd += ["--with-fg"]
    t0 = time.time()
    try:
        rc, out, err = _run(cmd, timeout=tmo + 60)
    except subprocess.TimeoutExpired:
        return _err("timeout", "mas_idle_emulators 超时 %ss（不得据此建议关闭）" % (tmo + 60),
                    ok_hint="UNKNOWN", elapsed_s=round(time.time() - t0, 1))
    except Exception as e:  # noqa: BLE001
        return _err("unknown", "%s: %s" % (type(e).__name__, e))
    elapsed = round(time.time() - t0, 1)
    try:
        data = json.loads(out)
    except Exception:  # noqa: BLE001
        return _err("upstream_unreachable" if rc == 2 else "parse_error", (err or out)[-400:],
                    exit_code=rc, elapsed_s=elapsed)
    if not isinstance(data, dict) or not data.get("ok"):
        return _err("upstream_unreachable", (err or out)[-300:], exit_code=rc, elapsed_s=elapsed,
                    candidates=[], counts={})
    data["tool"] = "mas_idle_emulators"
    data["elapsed_s"] = elapsed
    return data


# ---------------------------------------------------------------- mas_client_state

def _import_client_check():
    sys.path.insert(0, TOOLS_DIR)
    import client_state_check as cs  # noqa: PLC0415
    return cs


def _run_client(png_paths: list, timeout: int) -> tuple:
    """一组图片 → client_state_check --list --jsonl（一次 OCR 初始化，省时）。"""
    os.makedirs(OBSERVE_DIR, exist_ok=True)
    lst = os.path.join(OBSERVE_DIR, "client_state_list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        f.write("\n".join(png_paths) + "\n")
    rc, out, err = _run([PY, CLI_CLIENT, "--list", lst, "--jsonl"], timeout=timeout)
    rows, total = [], None
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("#"):
            m = re.search(r"total=(\d+)", ln)
            if m:
                total = int(m.group(1))
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:  # noqa: BLE001
            continue
    if not rows and rc != 0:
        raise RuntimeError((err or out)[:300] or "client_state_check exit=%s" % rc)
    return rows, total


def _emu_map(timeout: int = 60) -> dict:
    """REST /api/emulator/status（只读）→ {idx(str): {"title","status","adb"}}。"""
    sys.path.insert(0, TOOLS_DIR)
    import mas_preflight as pf  # noqa: PLC0415
    es = pf.post("/api/emulator/status", {}, timeout=timeout).get("data") or {}
    out = {}
    for _eid, m in es.items():
        if isinstance(m, dict):
            for idx, v in m.items():
                if isinstance(v, dict):
                    out[str(idx)] = {"title": (v.get("title") or "?"), "status": v.get("status"),
                                     "adb": v.get("adb_address") or ""}
    return out


def _screencap(idx: str, tag: str) -> tuple:
    """远端截图（ssh + adb，只读）→ (png_path, serial)；失败 (\"\", \"\")。"""
    sys.path.insert(0, TOOLS_DIR)
    import mas_preflight as pf  # noqa: PLC0415
    serial = ""
    try:
        serial = (_emu_map().get(str(idx)) or {}).get("adb") or ""
    except Exception:  # noqa: BLE001
        serial = ""
    if not serial:
        try:  # 退化：VMM pid → 监听端口 → adb 探测（与 preflight 第 3 项同一链路）
            survey = pf.remote_survey(timeout=120)
            pid = survey.get("vmm", {}).get(str(idx))
            if pid:
                ports = pf.netstat_ports().get(int(pid), [])
                got = pf.instance_adb_probe(ports)
                serial = got.get("serial", "")
        except Exception:  # noqa: BLE001
            serial = ""
    if not serial:
        return "", ""
    shot = pf.screencap(serial, OBSERVE_DIR, tag)
    return shot or "", serial


def _resolve_running(timeout: int = 120) -> list:
    """产线主机在跑实例（MuMuVMMHeadless 口径）→ [idx(str), …] 升序。"""
    sys.path.insert(0, TOOLS_DIR)
    import mas_preflight as pf  # noqa: PLC0415
    survey = pf.remote_survey(timeout=timeout)
    return sorted((survey.get("vmm") or {}).keys(), key=lambda s: int(s))


def _client_summary(instances: list) -> tuple:
    counts: dict = {}
    for it in instances:
        st = it.get("state") or "未知"
        counts[st] = counts.get(st, 0) + 1
    abnormal = [it for it in instances if it.get("state") != "正常" and not str(it.get("state", "")).startswith("正常")]
    parts = "、".join("%s×%d" % (k, v) for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
    if abnormal:
        det = "；".join("idx%s=%s" % (it.get("idx") or it.get("serial") or "?", it.get("state")) for it in abnormal[:4])
        summary = "%s（异常 %d：%s）" % (parts, len(abnormal), det)
    else:
        summary = parts or "无"
    return counts, abnormal, summary


def tool_client_state(a: dict) -> dict:
    try:
        cs = _import_client_check()
    except Exception as e:  # noqa: BLE001
        return _err("unknown", "client_state_check 导入失败: %s" % e)
    try:
        tmo = int(a.get("timeout") or 240)
    except (TypeError, ValueError):
        tmo = 240
    deadline = time.time() + tmo
    image = str(a.get("image") or "").strip()
    lst = str(a.get("list") or "").strip()
    serial = str(a.get("serial") or "").strip()
    idx_arg = str(a.get("idx") or "").strip()
    local = bool(a.get("local", False))
    try:
        maxn = min(6, max(1, int(a.get("max", 4))))
    except (TypeError, ValueError):
        maxn = 4

    # ① 离线单图
    if image:
        try:
            rc, out, err = _run([PY, CLI_CLIENT, "--image", image, "--json"], timeout=min(120, tmo))
            res = json.loads(out)
        except subprocess.TimeoutExpired:
            return _err("timeout", "client_state_check --image 超时", mode="image")
        except Exception as e:  # noqa: BLE001
            return _err("parse_error", "%s: %s" % (type(e).__name__, e), mode="image")
        res.update({"ok": True, "mode": "image", "idx": None, "serial": None, "shot": image})
        counts, abnormal, summary = _client_summary([res])
        res.update({"counts": counts, "abnormal": abnormal, "summary": summary})
        return res

    # ② 批量清单文件（离线）
    if lst:
        try:
            rows, total = _run_client_lines(lst, tmo)
        except subprocess.TimeoutExpired:
            return _err("timeout", "client_state_check --list 超时", mode="list")
        except Exception as e:  # noqa: BLE001
            return _err("parse_error", str(e)[:300], mode="list")
        inst = []
        for r in rows:
            st = r.get("state") or "未知"
            inst.append({"file": r.get("file"), "state": st,
                         "action": cs.ACTIONS.get(st) or cs.ACTIONS.get("未知"),
                         "evidence": r.get("evidence"), "size": r.get("size")})
        counts, abnormal, summary = _client_summary(inst)
        return {"ok": True, "mode": "list", "total": total, "counts": counts,
                "instances": inst, "abnormal": abnormal, "summary": summary}

    # ③ 单 serial（默认经 ssh 在产线主机截图；local=true 用本机 adb）
    if serial:
        shot = serial
        if not local:
            try:
                sys.path.insert(0, TOOLS_DIR)
                import mas_preflight as pf  # noqa: PLC0415
                shot = pf.screencap(serial, OBSERVE_DIR, "client_" + serial.replace(":", "_").replace(".", "_"))
            except Exception:  # noqa: BLE001
                shot = ""
        if not shot:
            return _err("upstream_unreachable", "截图失败（ssh/adb 不可用或画面无有效 PNG）", mode="serial",
                        serial=serial, state="未知")
        try:
            args = [PY, CLI_CLIENT, "--image", shot, "--json"] if not local else \
                   [PY, CLI_CLIENT, "--serial", serial, "--json"]
            rc, out, err = _run(args, timeout=min(150, int(max(30, deadline - time.time()))))
            res = json.loads(out)
        except subprocess.TimeoutExpired:
            return _err("timeout", "client_state_check 超时", mode="serial", serial=serial, state="未知")
        except Exception as e:  # noqa: BLE001
            return _err("parse_error", "%s: %s / %s" % (type(e).__name__, e, (out or "")[:200]),
                        mode="serial", serial=serial, state="未知")
        res.update({"ok": True, "mode": "serial", "idx": None, "serial": serial, "shot": shot})
        counts, abnormal, summary = _client_summary([res])
        res.update({"counts": counts, "abnormal": abnormal, "summary": summary})
        return res

    # ④ + ⑤ idx 列表 / 无参数抽查（截图 → 批量 OCR）
    targets = []
    if idx_arg:
        targets = [s.strip() for s in idx_arg.split(",") if s.strip()]
        targets = [t for t in targets if t.isdigit()][:maxn]
        if not targets:
            return _err("bad_args", "idx 必须是下标（1..N，逗号分隔 ≤6）：%r" % idx_arg, mode="idx")
    try:
        if not targets:
            targets = _resolve_running(timeout=min(120, int(max(30, deadline - time.time()))))[:maxn]
    except subprocess.TimeoutExpired:
        return _err("timeout", "远端巡检超时（ssh）", mode="sweep", state="未知")
    except Exception as e:  # noqa: BLE001
        return _err("upstream_unreachable", "%s: %s" % (type(e).__name__, e), mode="sweep", state="未知")
    if not targets:
        return {"ok": True, "mode": "sweep", "instances": [], "counts": {}, "abnormal": [],
                "summary": "无在跑实例（MuMuVMMHeadless=0）", "note": "只读口径：不启动实例"}

    shots, meta = [], []
    for idx in targets:
        if deadline - time.time() < 20:
            meta.append({"idx": idx, "state": "未知", "note": "超时预算耗尽未截"})
            continue
        try:
            png, ser = _screencap(idx, "client_i%s" % idx)
        except Exception as e:  # noqa: BLE001
            png, ser = "", ""
            meta.append({"idx": idx, "serial": "", "state": "未知", "note": "截图异常 %s" % type(e).__name__})
            continue
        if not png:
            meta.append({"idx": idx, "serial": ser, "state": "未知", "shot": "",
                         "note": "截图失败（实例不可连或未开机；不猜测）"})
            continue
        shots.append(png)
        meta.append({"idx": idx, "serial": ser, "shot": png})
    if shots:
        try:
            rows, _total = _run_client(shots, timeout=int(max(30, deadline - time.time())))
        except subprocess.TimeoutExpired:
            rows = []
            for m in meta:
                if m.get("shot"):
                    m.update({"state": "未知", "note": "OCR 超时"})
        except Exception as e:  # noqa: BLE001
            rows = []
            for m in meta:
                if m.get("shot"):
                    m.update({"state": "未知", "note": "OCR 失败: %s" % str(e)[:80]})
        by_file = {r.get("file"): r for r in rows}
        for m in meta:
            if not m.get("shot"):
                continue
            r = by_file.get(m["shot"]) or {}
            st = r.get("state") or "未知"
            m.update({"state": st,
                      "action": cs.ACTIONS.get(st) or cs.ACTIONS.get("未知"),
                      "evidence": r.get("evidence"), "size": r.get("size")})
    counts, abnormal, summary = _client_summary(meta)
    return {"ok": True, "mode": "idx" if idx_arg else "sweep", "targets": targets,
            "instances": meta, "counts": counts, "abnormal": abnormal, "summary": summary}


def _run_client_lines(list_file: str, timeout: int) -> tuple:
    """给定清单文件（离线批量）→ (rows, total)。"""
    rc, out, err = _run([PY, CLI_CLIENT, "--list", list_file, "--jsonl"], timeout=timeout)
    rows, total = [], None
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("#"):
            m = re.search(r"total=(\d+)", ln)
            if m:
                total = int(m.group(1))
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:  # noqa: BLE001
            continue
    if not rows and rc != 0:
        raise RuntimeError((err or out)[:300] or "client_state_check exit=%s" % rc)
    return rows, total


# ---------------------------------------------------------------- mas_identity_trail

def tool_identity(a: dict) -> dict:
    try:
        tmo = int(a.get("timeout") or 420)
    except (TypeError, ValueError):
        tmo = 420
    since = str(a.get("since") or "").strip() or dt.date.today().isoformat()
    if not _DATE_RE.match(since):
        return _err("bad_args", "since 必须是 YYYY-MM-DD：%r" % since)
    until = str(a.get("until") or "").strip()
    if until and not _DATE_RE.match(until):
        return _err("bad_args", "until 必须是 YYYY-MM-DD：%r" % until)
    os.makedirs(OBSERVE_DIR, exist_ok=True)
    outdir = os.path.join(RUNTIME, "identity_trace")
    os.makedirs(outdir, exist_ok=True)
    jpath = os.path.join(OBSERVE_DIR, "identity_%s.json" % dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    cmd = [PY, CLI_IDENTITY, "report", "--since", since, "--json", jpath, "--outdir", outdir]
    if until:
        cmd += ["--until", until]
    for src, flag in (("targets", "--targets"), ("ocr_index", "--ocr-index"),
                      ("frames_manifest", "--frames-manifest"), ("out", "--out")):
        v = str(a.get(src) or "").strip()
        if v:
            cmd += [flag, v]
    t0 = time.time()
    try:
        rc, out, err = _run(cmd, timeout=tmo)
    except subprocess.TimeoutExpired:
        return _err("timeout", "mas_identity_trail 超时 %ss" % tmo, elapsed_s=round(time.time() - t0, 1))
    except Exception as e:  # noqa: BLE001
        return _err("unknown", "%s: %s" % (type(e).__name__, e))
    elapsed = round(time.time() - t0, 1)
    if rc == 2:
        return _err("upstream_unreachable", (err or out)[-400:], exit_code=rc, elapsed_s=elapsed)
    try:
        with open(jpath, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        return _err("parse_error", "json 读回失败: %s: %s / %s" % (type(e).__name__, e, (out or "")[-200:]),
                    exit_code=rc, elapsed_s=elapsed)
    scripts = {}
    reds = 0
    for name, s in (data.get("scripts") or {}).items():
        rounds = []
        for r in (s.get("rounds") or []):
            if r.get("color") == "red":
                reds += 1
            rounds.append({"user": r.get("user"), "start": r.get("start"), "done": r.get("done"),
                           "app_completed": r.get("app_completed"), "switch": r.get("switch"),
                           "observed_mask": r.get("observed_mask"), "observed_uid": r.get("observed_uid"),
                           "verdict": r.get("verdict"), "color": r.get("color")})
        scripts[name] = {"maa_dir": s.get("maa_dir"), "rounds": rounds[:20],
                         "rounds_n": len(s.get("rounds") or []),
                         "users": [{"mask": u.get("mask")} for u in (s.get("users") or [])]}
    payload = {"ok": True, "tool": "mas_identity_trail", "since": data.get("since") or since,
               "until": data.get("until") or until, "generated": data.get("generated"),
               "red_rounds": reds, "scripts": scripts, "elapsed_s": elapsed,
               "exit_code": rc, "json_path": jpath}
    # 判定口径（fail-closed）：有红=不一致；有轮次且无红=一致；无轮次/无证据=无法判定
    total_rounds = sum(v["rounds_n"] for v in scripts.values())
    if reds > 0:
        payload["identity"] = "不一致"
    elif total_rounds > 0:
        payload["identity"] = "一致"
    else:
        payload["identity"] = "无法判定（窗口内无轮次或无身份证据）"
    return payload


# ---------------------------------------------------------------- mas_interrupt_guard

def tool_interrupt(a: dict) -> dict:
    """打断源治理（只读）：客户端版本 + 宿主/本机重启 ⇒ 一行 interrupt_risk。"""
    try:
        tmo = int(a.get("timeout") or 300)
    except (TypeError, ValueError):
        tmo = 300
    try:
        mx = int(a.get("max") or 6)
    except (TypeError, ValueError):
        mx = 6
    cmd = [PY, CLI_INTERRUPT, "--json", "--max", str(mx), "--timeout", str(tmo)]
    idx = str(a.get("idx") or "").strip()
    if idx:
        cmd += ["--idx", idx]
    scope = str(a.get("scope") or "").strip()
    if scope:
        cmd += ["--scope", scope]
    if a.get("version_only"):
        cmd += ["--version-only"]
    if a.get("restart_only"):
        cmd += ["--restart-only"]
    t0 = time.time()
    try:
        rc, out, err = _run(cmd, timeout=tmo + 30)
    except subprocess.TimeoutExpired:
        return _err("timeout", "mas_interrupt_guard 超时 %ss" % tmo, elapsed_s=round(time.time() - t0, 1))
    except Exception as e:  # noqa: BLE001
        return _err("unknown", "%s: %s" % (type(e).__name__, e))
    elapsed = round(time.time() - t0, 1)
    try:
        data = json.loads(out)
    except Exception as e:  # noqa: BLE001
        return _err("parse_error", "%s: %s / out=%s / err=%s" % (type(e).__name__, e, (out or "")[-200:], (err or "")[-200:]),
                    exit_code=rc, elapsed_s=elapsed)
    risk = data.get("interrupt_risk")
    # 版本面压缩
    v = data.get("version") or {}
    cmp_ = v.get("compare") or {}
    rows = [{"idx": r.get("idx"), "pkg": r.get("pkg"), "versionName": r.get("versionName"),
             "lastUpdateTime": r.get("lastUpdateTime"), "status": r.get("status"),
             "baseline_versionName": (r.get("baseline") or {}).get("versionName")}
            for r in (cmp_.get("rows") or [])]
    version = {"running_n": len((v.get("survey") or {}).get("running") or []),
               "rows": rows,
               "changed_n": len(cmp_.get("changed") or []),
               "new_n": len(cmp_.get("new") or []),
               "unreadable_n": len(cmp_.get("unreadable") or []),
               "state_file": v.get("state_file"),
               "error": v.get("error")}
    # 重启面压缩
    r = data.get("restart") or {}
    machines = {}
    for m, s in (r.get("surveys") or {}).items():
        if not s:
            continue
        pend = {k: val for k, val in (s.get("pending") or {}).items() if val}
        machines[m] = {"in_flight": s.get("in_flight"), "boot": s.get("boot"), "pending": pend,
                       "tasks": [{"name": t.get("name"), "state": t.get("state"), "next": t.get("next")}
                                 for t in (s.get("tasks") or []) if t.get("state") != "Disabled"]}
    restart = {"machines": machines, "next": r.get("next") or [],
               "signals": [{"kind": s.get("kind"), "machine": s.get("machine"), "detail": s.get("detail")}
                           for s in (r.get("signals") or [])],
               "errors": r.get("errors") or []}
    payload = {"ok": not bool(data.get("hard_error")), "tool": "mas_interrupt_guard",
               "generated_at": data.get("generated_at"), "interrupt_risk": risk,
               "risk_line": "INTERRUPT_RISK=%s" % risk, "reasons": data.get("reasons") or [],
               "version": version, "restart": restart, "exit_code": rc, "elapsed_s": elapsed}
    if data.get("hard_error"):
        payload["error"] = {"kind": "upstream_unreachable" if any(
            x in str(data.get("hard_error")) for x in ("ssh", "Timeout", "IG-END", "TimeoutExpired"))
            else "unknown", "detail": str(data.get("hard_error"))[:400]}
    return payload


_ECAL_KEYS = ("EVENT_CAL", "UPCOMING", "EVENT_ADVICE")


def _event_cal_lines(out: str) -> dict:
    """取 `event_calendar.py --check` 的 EVENT_CAL=/UPCOMING=/EVENT_ADVICE= 三行（去掉 `KEY=` 前缀）。"""
    vals: dict = {}
    for raw in (out or "").splitlines():
        ln = raw.strip()
        for k in _ECAL_KEYS:
            pre = k + "="
            if ln.startswith(pre) and k not in vals:
                vals[k] = ln[len(pre):]
                break
    return vals


def tool_event_calendar(a: dict) -> dict:
    """周期性事件到点提醒（只读）：--check 三行 ⇒ upcoming（写 `UPCOMING=…` 用）；fail-closed 给 UNKNOWN:<原因>。"""
    try:
        days = int(a.get("days") or 14)
    except (TypeError, ValueError):
        return _err("bad_args", "days 必须是整数", upcoming="UNKNOWN:bad_args days")
    try:
        wh = int(a.get("window_hours") or 48)
    except (TypeError, ValueError):
        return _err("bad_args", "window_hours 必须是整数", upcoming="UNKNOWN:bad_args window_hours")
    if not 1 <= days <= 60:
        return _err("bad_args", "days 越界（1..60）", upcoming="UNKNOWN:bad_args days 越界（1..60）")
    if not 1 <= wh <= 336:
        return _err("bad_args", "window_hours 越界（1..336）", upcoming="UNKNOWN:bad_args window_hours 越界（1..336）")
    try:
        tmo = int(a.get("timeout") or 60)
    except (TypeError, ValueError):
        tmo = 60
    if not os.path.isfile(CLI_EVENTCAL):
        return _err("unknown", "缺 tools/event_calendar.py",
                    upcoming="UNKNOWN:缺 tools/event_calendar.py（工具面未提供）")
    t0 = time.time()
    try:
        rc, out, err = _run([PY, CLI_EVENTCAL, "--check", "--days", str(days), "--window-hours", str(wh)], timeout=tmo)
    except subprocess.TimeoutExpired:
        return _err("timeout", "event_calendar 超时 %ss" % tmo,
                    upcoming="UNKNOWN:超时（event_calendar --check >%ss）" % tmo,
                    elapsed_s=round(time.time() - t0, 1))
    except Exception as e:  # noqa: BLE001
        return _err("unknown", "%s: %s" % (type(e).__name__, e),
                    upcoming="UNKNOWN:%s" % str(e)[:120], elapsed_s=round(time.time() - t0, 1))
    elapsed = round(time.time() - t0, 1)
    vals = _event_cal_lines(out)
    payload = {"ok": rc == 0, "event_cal": vals.get("EVENT_CAL"), "upcoming": vals.get("UPCOMING"),
               "event_advice": vals.get("EVENT_ADVICE"), "exit_code": rc, "elapsed_s": elapsed}
    if rc == 2:            # 工具自身 fail-closed（快照缺失/损坏）⇒ 三行已是 UNKNOWN
        payload["error"] = {"kind": "unavailable", "detail": str(vals.get("UPCOMING") or "数据不可用")[:400]}
    elif rc != 0:
        payload["error"] = {"kind": "unknown", "detail": ("rc=%s %s" % (rc, (err or out or "").strip()))[:400]}
        payload["upcoming"] = "UNKNOWN:rc=%s（event_calendar 未产出）" % rc
    if not payload.get("upcoming"):
        payload["ok"] = False
        payload["upcoming"] = "UNKNOWN:无输出（event_calendar --check 未产出 UPCOMING 行；rc=%s）" % rc
        payload.setdefault("error", {"kind": "parse_error", "detail": (out or err or "")[-300:]})
    if str(payload["upcoming"]).startswith("UNKNOWN:"):
        payload["ok"] = False
    return payload


# ---------------------------------------------------------------- mas_kanban_health

_KH_KV_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*)=([^,]*)")
_KH_COUNT_KEYS = ("boards", "cards", "running", "dup", "zombie", "blocked_stale", "fail", "orphan",
                  "P1", "P2", "P3")


def _kh_parse_line(line: str) -> tuple:
    """`KANBAN_HEALTH=ts=…,boards=2,…,P2=6,P3=18,status=WARN` → (counts, status)。只做搬运，不重算。"""
    body = line[len("KANBAN_HEALTH="):] if line.startswith("KANBAN_HEALTH=") else line
    kv = dict(_KH_KV_RE.findall(body))
    counts: dict = {}
    for k in _KH_COUNT_KEYS:
        if k in kv:
            try:
                counts[k] = int(kv[k])
            except ValueError:
                counts[k] = kv[k]
    return counts, kv.get("status")


def tool_kanban_health(a: dict) -> dict:
    """看板治理体检（只读；单一真源 = tools/kanban_health.py，本层只搬运）。

    只读硬闸：子进程只允许 `--line` / `--json` / `--no-write` / `--board <slug>`；
    零写盘、不触发任何看板写动作（不归档/不重派/不改卡）。
    """
    board = str(a.get("board") or "").strip()
    as_json = bool(a.get("json", False))
    if not os.path.isfile(CLI_KANBAN_HEALTH):
        return _err("unknown", "缺 tools/kanban_health.py（工具面未提供）")
    cmd = [PY, CLI_KANBAN_HEALTH, "--json" if as_json else "--line", "--no-write"]
    for slug in [s.strip() for s in board.split(",") if s.strip()]:
        cmd += ["--board", slug]
    t0 = time.time()
    try:
        rc, out, err = _run(cmd, timeout=60)
    except subprocess.TimeoutExpired:
        return _err("timeout", "mas_kanban_health 超时 60s（看板扫描未返回）",
                    elapsed_s=round(time.time() - t0, 1))
    except Exception as e:  # noqa: BLE001
        return _err("unknown", "%s: %s" % (type(e).__name__, e))
    elapsed = round(time.time() - t0, 1)
    if as_json:
        try:
            doc = json.loads(out)
        except Exception:  # noqa: BLE001
            return _err("bad_args" if (rc == 4 and board) else ("unknown" if rc == 4 else "parse_error"),
                        ("rc=%s %s" % (rc, (err or out or "").strip()))[:400], exit_code=rc, elapsed_s=elapsed)
        line = str(doc.get("line") or "")
        counts, status = _kh_parse_line(line)
        return {"ok": rc == 0, "tool": "mas_kanban_health", "line": line,
                "status": doc.get("status") or status, "counts": counts,
                "totals": doc.get("totals"), "json": doc, "exit_code": rc, "elapsed_s": elapsed}
    line = ""
    for ln in (out or "").splitlines():
        if ln.strip().startswith("KANBAN_HEALTH="):
            line = ln.strip()
    if not line:
        # rc=4：一块板都没扫到（board slug 打错 ⇒ bad_args；缺 boards 根 ⇒ unknown）
        return _err("bad_args" if (rc == 4 and board) else ("unknown" if rc == 4 else "parse_error"),
                    ("rc=%s %s" % (rc, (err or out or "").strip()))[:400], exit_code=rc, elapsed_s=elapsed)
    counts, status = _kh_parse_line(line)
    return {"ok": rc == 0, "tool": "mas_kanban_health", "line": line, "status": status,
            "counts": counts, "exit_code": rc, "elapsed_s": elapsed}


# ---------------------------------------------------------------- audit_waterline

def tool_audit_waterline(a: dict) -> dict:
    """统一审计流水位判据（只读；单一真源 = tools/audit_waterline.py，本层只搬运）。

    巡检写 `AUDIT_STATUS=…` 行时直接用返回的 audit_status。
    只读硬闸：子进程只允许 `--json`；本层永不 rotate/prune、零写入。
    rc 语义（**0=OK / 2=WARN / 3=CRIT 都是有效结论**，ok=true；1=取数失败 ⇒ ok=false）。
    """
    try:
        tmo = int(a.get("timeout") or 60)
    except (TypeError, ValueError):
        tmo = 60
    if not os.path.isfile(CLI_AUDIT_WL):
        return _err("unknown", "缺 tools/audit_waterline.py（工具面未提供）",
                    audit_status="UNKNOWN:缺 tools/audit_waterline.py（工具面未提供）")
    t0 = time.time()
    try:
        rc, out, err = _run([PY, CLI_AUDIT_WL, "--json"], timeout=tmo)
    except subprocess.TimeoutExpired:
        return _err("timeout", "audit_waterline 超时 %ss（水位判据未返回）" % tmo,
                    audit_status="UNKNOWN:超时（audit_waterline >%ss）" % tmo,
                    elapsed_s=round(time.time() - t0, 1))
    except Exception as e:  # noqa: BLE001
        return _err("unknown", "%s: %s" % (type(e).__name__, e),
                    audit_status="UNKNOWN:%s" % str(e)[:120], elapsed_s=round(time.time() - t0, 1))
    elapsed = round(time.time() - t0, 1)
    try:
        data = json.loads(out.strip() or "{}")
    except ValueError:
        line = ""
        for ln in reversed((out or "").splitlines()):
            if ln.strip().startswith("AUDIT_STATUS="):
                line = ln.strip()
                break
        return _err("parse_error", ("rc=%s %s" % (rc, (err or out or "").strip()))[:400],
                    audit_status=line or "UNKNOWN:parse_error（audit_waterline 未产出判据行）",
                    exit_code=rc, elapsed_s=elapsed)
    checks = data.get("checks") or {}
    payload = {"ok": rc in (0, 2, 3), "tool": "audit_waterline",
               "audit_status": data.get("audit_status"), "verdict": data.get("verdict"),
               "reasons": data.get("reasons") or [], "live_mb": checks.get("live_mb"),
               "archives_n": checks.get("archives_n"), "report_hours": checks.get("report_hours"),
               "live_age_hours": checks.get("live_age_hours"), "rotate_now": checks.get("rotate_now"),
               "stale_archives_n": data.get("stale_archives_n"), "exit_code": rc, "elapsed_s": elapsed}
    if rc not in (0, 2, 3):
        payload["ok"] = False
        payload["error"] = {"kind": "unknown", "detail": ("rc=%s %s" % (rc, (err or out or "").strip()))[:400]}
        if not str(payload.get("audit_status") or "").startswith("UNKNOWN:"):
            payload["audit_status"] = "UNKNOWN:rc=%s（audit_waterline 未产出有效判据）" % rc
    if not payload.get("ok") and not payload.get("audit_status"):
        payload["audit_status"] = "UNKNOWN:无输出（audit_waterline 未产出判据行）"
    return payload


# ---------------------------------------------------------------- MCP 装配

async def _call(name: str, args: dict):
    args = args or {}
    try:
        if name == "mas_preflight":
            return _text(tool_preflight(args))
        if name == "mas_report":
            return _text(tool_report(args))
        if name == "mas_idle_emulators":
            return _text(tool_idle(args))
        if name == "mas_client_state":
            return _text(tool_client_state(args))
        if name == "mas_identity_trail":
            return _text(tool_identity(args))
        if name == "mas_interrupt_guard":
            return _text(tool_interrupt(args))
        if name == "event_calendar":
            return _text(tool_event_calendar(args))
        if name == "mas_kanban_health":
            return _text(tool_kanban_health(args))
        if name == "audit_waterline":
            return _text(tool_audit_waterline(args))
        return _text({"ok": False, "error": {"kind": "bad_args", "detail": "unknown tool: %s" % name}})
    except Exception as e:  # noqa: BLE001
        return _text(_err("unknown", "%s: %s" % (type(e).__name__, e)))


async def _main() -> None:
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list() -> list:
        return _SCHEMAS

    @server.call_tool()
    async def _call_tool(tool_name: str, arguments: dict | None):
        return await _call(tool_name, arguments or {})

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def _check() -> int:
    problems = []
    names = [t.name for t in _SCHEMAS]
    if names != _KNOWN_TOOLS:
        problems.append("工具名与冻结契约不一致: %s" % names)
    for p in (CLI_PREFLIGHT, CLI_ERRDAILY, CLI_IDLE, CLI_CLIENT, CLI_IDENTITY, CLI_INTERRUPT, CLI_EVENTCAL,
              CLI_KANBAN_HEALTH, CLI_AUDIT_WL):
        if not os.path.isfile(p):
            problems.append("CLI 缺失: %s" % p)
    # 只读姿态自审：本文件不得出现任何写端点/写动作（字样拼接避免自匹配）
    try:
        with open(os.path.abspath(__file__), encoding="utf-8") as f:
            me = f.read()
        bad_words = ["/api/emulator/" + "operate", "shut" + "down", "maa_" + "stop",
                     "queue_" + "control", "/api/dispatch/" + "start", "hermes " + "kanban"]
        for bad in bad_words:
            if bad in me:
                problems.append("只读断言失败：源码出现写操作字样 %r" % bad)
    except Exception as e:  # noqa: BLE001
        problems.append("只读自审读取失败: %s" % e)
    selftests = [(CLI_PREFLIGHT, ["--selftest"], "selftest: OK"),
                 (CLI_ERRDAILY, ["--selftest"], "--selftest: OK"),
                 (CLI_IDENTITY, ["selftest"], "selftest"),
                 (CLI_INTERRUPT, ["--selftest"], "selftest: OK"),
                 (CLI_EVENTCAL, ["--selftest"], "SELFTEST=OK"),
                 (CLI_KANBAN_HEALTH, ["--selftest"], "SELFTEST=PASS"),
                 (CLI_AUDIT_WL, ["--selftest"], "SELFTEST=OK"),
                 (CLI_CLIENT, ["--help"], "usage"),
                 (CLI_IDLE, ["--help"], "usage")]
    results = []
    for cli, argv, expect in selftests:
        try:
            rc, out, err = _run([PY, cli] + argv, timeout=180)
            ok = rc == 0 and expect in out
            results.append("%s=%s" % (os.path.basename(cli), "OK" if ok else "FAIL"))
            if not ok:
                problems.append("%s %s rc=%s（期望含 %r）" % (os.path.basename(cli), argv, rc, expect))
        except Exception as e:  # noqa: BLE001
            results.append("%s=ERR" % os.path.basename(cli))
            problems.append("%s 运行失败: %s" % (os.path.basename(cli), e))
    print("mas_observe_mcp --check: %s" % ("FAIL" if problems else "OK"))
    print("  tools(%d)=%s" % (len(names), ",".join(names)))
    print("  cli: %s" % " | ".join(results))
    print("  readonly_assert=PASS（源码无写端点字样）" if not any("只读断言" in p for p in problems) else "  readonly_assert=FAIL")
    for p in problems:
        print("  - " + p)
    return 2 if problems else 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(_check())
    asyncio.run(_main())
