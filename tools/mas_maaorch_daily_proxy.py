# -*- coding: utf-8 -*-
"""MAAOrch『清日常』stdio MCP 代理 —— Hermes 的受控连接器（只放行必要工具 + 默认预演）。

## 为什么存在
MAAOrch 的 MCP（http://127.0.0.1:19997/mcp/）暴露 **41 个工具**，其中一半是写/危险操作
（account_stop / maa_stop_all / queue_control / kill_audit_decide / emulator_control …）。
把 41 个工具整体交给对话 Agent，等于把"能杀进程、能关模拟器、能停全队列"的遥控器交出去；
而且核心的 **smart_dispatch 没有 dry_run 形参**（上游 network/mcp_server.py:220-228 直接
POST /api/action/smart_selected）—— **一次误调用就是真发车**。
本代理把 Hermes 看到的工具面压到 **10 个**：

    dashboard / account_status / mas_status / mas_snapshot / queue / health   ← 只读转发（响应脱敏）
    mas_rerun_state()                                                        ← 只读：后台补跑进度
    mas_rerun(accounts, dry_run=True)                                        ← 上游原生写工具：原样转发（默认预演）
    daily_gap()                                                              ← 只读：算今日缺口
    daily_clear(account_ids, confirm=False)                                   ← 受控写：默认预演，绝不发车

## 挡得住什么 / 挡不住什么（务必读）
挡得住：
- **工具面**：tools/list 只返回上表 8 个名字；白名单外的调用**在门禁层直接拒绝且零上游请求**
  （拒绝发生在 ensure/上游调用之前，见 _handle_call_tool）。
- **误发车**：daily_clear 默认 confirm=False ⇒ 只做 4 个只读工具的预检 + 原样回显将要提交的
  smart_dispatch 载荷，**一个写请求都不发**；只有显式 confirm=True（严格布尔 True）才可能发车，
  且还要过下面的硬门禁。
- **门禁（confirm=True 时，任一条不满足即拒绝且不发车）**：
  a. account_ids 非空且 ≤ MAX_ACCOUNTS_PER_CALL（缺省 3，MAAORCH_DAILY_MAX_ACCOUNTS 可覆盖）；
  b. **无正在运行的批次**：queue.active / account_status.running|queued / dashboard.capacity.running
     / mas_status.rerun.running 任一显示在跑 ⇒ refused="batch_running"；
  c. 上游账号清单里查不到的 account_id ⇒ refused="unknown_account"（**先于 no_gap** 报出，
     否则"无缺口"会把拼错的 id 掩盖成正常理由）；
  d. **今日已有 DONE 的账号剔除**（判定同 network/api_fastapi.py:852-855：sanity_pct<=20 且
     done_ts 是今天）；剔除后没有缺口 ⇒ refused="no_gap"；
  e. 只读预检本身必须成功（account_status / queue / mas_status 读不到）⇒ fail-closed
     refused="readonly_unavailable"；
  f. MAAORCH_DAILY_NO_DISPATCH 置位（硬闸/演练开关）⇒ refused="dispatch_disabled"。
- **凭据与脱敏**：鉴权 token 只在 HTTP 头里出现，**绝不打印、绝不进返回值**（_redact 兜底替换）；
  上游响应递归脱敏（敏感键名的值 → <scrubbed>），错误消息同样过脱敏。
挡不住（本代理不是安全边界）：
- **调用本身**：谁能让 Hermes 调到 daily_clear(confirm=True) 且门禁全过，谁就发了一次车 ——
  门禁只保证"不在批次在跑时插队、不重复清已完成的号、不发陌生号"。
- **上游直连**：19997 端口本身仍对任何能读 models/config.json 的本地进程开放，本代理管不到。
- **上游语义**：smart_dispatch 发出去之后 MAAOrch 怎么编排、跳过谁，由上游决定（"已排队/运行中
  会自动跳过"是它的行为，不是本代理的保证）。

## mas_rerun 转发契约（Hermes 清日常/补跑的写路径）
Hermes 直连 19997 时用的就是这个上游原生工具，本代理**不改它的语义、也不替调用方改 dry_run**：
- dry_run=True（缺省）：上游 network/mcp_server.py:104-124 的 _gate() 只回显 {"would_call": ...}，
  **绝不下发**；本代理原样转发 accounts（list[str] | null，null ⇒ 上游按主机快照的失败号解析候选）
  与 dry_run，并把上游返回体整包（脱敏后）回给调用方 —— 返回值"原样"，不额外包一层。
- dry_run=False：真下发（POST /api/action/mas_rerun → services/mas_adapter.rerun(dry_run=False)
  → tools/auto_mas_rerun.py --apply）。本代理只多做两件事：
  (1) 单批账号数 ≤ MAX_RERUN_ACCOUNTS=8（超过 ⇒ refused="too_many_accounts"，零上游请求）；
  (2) MAAORCH_DAILY_NO_DISPATCH 置位时拒绝（refused="dispatch_disabled"，零上游请求）。
- 每次转发写一行 stderr 审计日志：工具名 / accounts 数量 / dry_run 值 / 是否真下发 / 结果，
  便于事后核对"到底哪一次真的发了车"。
- **为什么不套 daily_clear 那套 batch_running/no_gap 门禁**：补跑的前提正是"批跑失败之后"，
  用"批次在跑"去拦补跑会把唯一的修复路径堵死；上游自身对已排队/运行中的账号会跳过。

## 按需拉起上游
Python 上游**按设计不常驻**（空转 ~47% CPU），由 tools/start_python_upstream.bat 手动拉、
或 Node 门面的 WMI 按需拉。本代理在任何 tools/call 之前先探 127.0.0.1:19997：
不在监听就用 Python312（或 MAAORCH_DAILY_PYTHON）后台跑 main_web.pyw、日志追加到
logs/web_ui.log，轮询等待 ≤45s；仍失败返回结构化 {"error": ...}（**明确报错，不挂死**）。
tools/list 走**上游 schema 缓存**：首次成功后本进程不再打上游，上游临时不可达时退回静态定义
（工具面恒定 8 个，绝不因上游抖动变化）。

## 配置（环境变量）
  MAAORCH_DAILY_MCP_URL        上游 /mcp 地址；缺省 http://127.0.0.1:19997/mcp/（**尾斜杠必需**）
  MAAORCH_AGENT_TOKEN          鉴权 token 覆盖；缺省读 models/config.json 的 api_token（**不打印**）
  MAAORCH_DAILY_TIMEOUT        单次上游调用超时（秒，缺省 30）
  MAAORCH_DAILY_START_TIMEOUT  拉起上游的等待上限（秒，缺省 45）
  MAAORCH_DAILY_MAX_ACCOUNTS   单次 daily_clear 的账号上限（缺省 3）
  MAAORCH_DAILY_DAYS           预检回看天数（缺省 3，钳制 1-30）
  MAAORCH_DAILY_NO_DISPATCH    发车硬闸：未设置/空串 = 允许（仍需 confirm=True）；
                               1/true/yes/on/enable/enabled = 禁止发车；
                               0/false/no/off/disable/disabled = 允许；
                               其它任何非空值 = fail-closed 按"禁止发车"处理
  MAAORCH_DAILY_STRICT_MAS     1 = 把 AUTO-MAS 主机 maa_processes>0 也算"批次在跑"（更严，缺省关）
  MAAORCH_DAILY_NO_AUTOSTART   1 = 不自动拉起上游（只报错）
  MAAORCH_DAILY_PYTHON         拉起上游用的 python.exe（缺省 Python312 绝对路径）

## 用法
  python tools/mas_maaorch_daily_proxy.py          # stdio server（Hermes 用 command+args 拉起）
  python tools/mas_maaorch_daily_proxy.py --check  # 自检：白名单/门禁/脱敏/子进程链路/降级，退出码 0/非 0
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

VERSION = "1.0.0"
PLACEHOLDER = "<scrubbed>"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "models", "config.json")
UPSTREAM_ENTRY = os.path.join(REPO_ROOT, "main_web.pyw")
UPSTREAM_LOG = os.path.join(REPO_ROOT, "logs", "web_ui.log")
DEFAULT_PYTHON = r"C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe"
_LOOPBACK = ("127.0.0.1", "localhost", "::1")

URL_ENV = "MAAORCH_DAILY_MCP_URL"
TOKEN_ENV = "MAAORCH_AGENT_TOKEN"
TIMEOUT_ENV = "MAAORCH_DAILY_TIMEOUT"
START_TIMEOUT_ENV = "MAAORCH_DAILY_START_TIMEOUT"
MAX_ACCOUNTS_ENV = "MAAORCH_DAILY_MAX_ACCOUNTS"
DAYS_ENV = "MAAORCH_DAILY_DAYS"
NO_DISPATCH_ENV = "MAAORCH_DAILY_NO_DISPATCH"
STRICT_MAS_ENV = "MAAORCH_DAILY_STRICT_MAS"
NO_AUTOSTART_ENV = "MAAORCH_DAILY_NO_AUTOSTART"
PYTHON_ENV = "MAAORCH_DAILY_PYTHON"

DEFAULT_URL = "http://127.0.0.1:19997/mcp/"
MAX_ACCOUNTS_PER_CALL = 3
DEFAULT_DAYS = 3
DONE_SANITY_PCT = 20.0

_TRUTHY = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSY = {"0", "false", "no", "off", "disable", "disabled"}

# ── 工具面：只有这 10 个名字会被 Hermes 看到 ──────────────────────────
DAILY_TOOL = "daily_clear"
GAP_TOOL = "daily_gap"
UPSTREAM_WRITE_TOOL = "smart_dispatch"          # daily_clear 内部使用的真发车工具（本体不暴露）
RERUN_TOOL = "mas_rerun"                        # 上游原生补跑工具（dry_run 语义原样转发）
RERUN_STATE_TOOL = "mas_rerun_state"            # 只读：后台补跑进度
MAX_RERUN_ACCOUNTS = 8                          # 单批补跑账号上限（上游 mas_adapter 也是 [:8]）

READONLY_WHITELIST = ("dashboard", "account_status", "mas_status", "mas_snapshot", "queue", "health")
# 原样转发的上游工具：6 个只读 + mas_rerun_state（无本地语义，直接透传）
SAFE_FORWARD_TOOLS = READONLY_WHITELIST + (RERUN_STATE_TOOL,)
# 用上游 schema 保真的工具（Hermes 看到的就是上游签名；mas_rerun 的 dry_run=True 缺省原样保留）
UPSTREAM_SCHEMA_TOOLS = SAFE_FORWARD_TOOLS + (RERUN_TOOL,)
# 本地工具（schema 由本代理定义：daily_gap 只读、daily_clear 默认预演）
LOCAL_SCHEMA_TOOLS = (DAILY_TOOL, GAP_TOOL)
EXPOSED_TOOLS = UPSTREAM_SCHEMA_TOOLS + LOCAL_SCHEMA_TOOLS

# 上游 41 个工具里，除白名单 8 个（6 只读 + mas_rerun_state + mas_rerun）之外的全部
# 33 个写/危险/无关工具 —— 导入期自检用：任何一个都不允许出现在 EXPOSED_TOOLS 里，
# 工具名取自 2026-09-26 实测 tools/list 真值。
FORBIDDEN_UPSTREAM = (
    "link_map", "inspect_account", "processes", "status", "events", "stats_dashboard",
    "depot_plan", "activity_stages", "probe_status", "enqueue", "dequeue", "queue_control",
    "probe_stage", "mas_history", "mas_ledger", "mas_cleanup",
    "kill_audit_history", "kill_audit_pending", "kill_audit_decide", "emulators",
    "emulator_status", "emulator_control", "emulator_batch", "connect_status",
    "emu_connect_stop", "emu_connect_restart", "maa_status", "maa_instances", "account_launch",
    "account_stop", "maa_stop_all", "maa_launch_source",
    # daily_clear 的真发车工具本体：**只有** daily_clear(confirm=True) 这一条受控路径能触达它
    UPSTREAM_WRITE_TOOL,
)

# 拒绝原因码（结构化、可断言）
R_BAD_ARGS = "bad_arguments"
R_TOO_MANY = "too_many_accounts"
R_DISABLED = "dispatch_disabled"
R_READONLY = "readonly_unavailable"
R_BATCH = "batch_running"
R_NO_GAP = "no_gap"
R_UNKNOWN = "unknown_account"

_REFUSAL_TEXT = {
    R_TOO_MANY: "账号数超过单次上限",
    R_DISABLED: "发车硬闸已置位（MAAORCH_DAILY_NO_DISPATCH），本次拒绝发车",
    R_READONLY: "只读预检不完整，无法确认批次状态 ⇒ fail-closed 拒绝发车",
    R_BATCH: "检测到批次正在运行（queue/account_status/dashboard/mas_status 有在跑证据）",
    R_NO_GAP: "今日目标账号均已清完（无缺口），无需发车",
    R_UNKNOWN: "存在上游账号清单里查不到的 account_id",
}


# ── 脱敏（凭据/敏感键名值）──────────────────────────────────────────
DEFAULT_SENSITIVE = (
    "password", "passwd", "pass_word", "pwd", "sklandtoken", "token", "secret",
    "credential", "cookie", "authorization", "apikey", "api_key", "auth_key",
    "authkey", "private_key", "session_id", "sessionid",
)
SENSITIVE = re.compile("|".join(re.escape(k) for k in DEFAULT_SENSITIVE), re.I)
_KV_RE = re.compile(
    r'("(?:' + "|".join(re.escape(k) for k in DEFAULT_SENSITIVE) + r')"\s*:\s*)'
    r'("(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|true|false|null)', re.I)


def api_token() -> str:
    """鉴权 token：MAAORCH_AGENT_TOKEN > models/config.json 的 api_token。

    调用方**永远不要打印返回值**；日志/返回值里要用 _redact() 兜底。
    """
    v = (os.environ.get(TOKEN_ENV) or "").strip()
    if v:
        return v
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return str(cfg.get("api_token") or "").strip()
    except Exception:                                          # noqa: BLE001
        return ""


def _redact(text: str) -> str:
    """把文本里出现的 token 真值换成占位符（最后一道兜底，防异常消息带出 token）。"""
    tok = api_token()
    if not isinstance(text, str):
        text = str(text)
    if tok and tok in text:
        return text.replace(tok, PLACEHOLDER)
    return text


def scrub(obj):
    """递归脱敏：命中敏感键名的值整体换成占位符（键名与结构保留）。"""
    if isinstance(obj, dict):
        return {k: (PLACEHOLDER if SENSITIVE.search(str(k)) else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, str):
        return _redact(obj)
    return obj


def scrub_text(text):
    """文本脱敏：JSON 对象/数组先解析再脱敏；否则按 JSON 片段正则兜底 + token 替换。"""
    if not isinstance(text, str) or not text:
        return text
    out = _redact(text)
    try:
        parsed = json.loads(out)
    except ValueError:
        parsed = None
    if isinstance(parsed, (dict, list)):
        return json.dumps(scrub(parsed), ensure_ascii=False)
    return _KV_RE.sub(lambda m: m.group(1) + '"' + PLACEHOLDER + '"', out)


# ── 环境开关 ────────────────────────────────────────────────────────
def _env_bool(name: str, *, default_when_unset: bool) -> bool:
    """三态布尔：未设置/空串 → default_when_unset；已知真/假；其它非空 → fail-closed 取"危险侧"。

    危险侧由 default_when_unset 决定：NO_DISPATCH 缺省 False ⇒ 未知值判 True（禁发车）；
    STRICT_MAS 缺省 False ⇒ 未知值判 True（更严）。写路径一律宁可多拦。
    """
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default_when_unset
    v = str(raw).strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    print("[warn] %s=%r 不是已知取值 -> fail-closed 按 %s 处理" % (name, raw, not default_when_unset),
          file=sys.stderr, flush=True)
    return not default_when_unset


def timeout_s() -> float:
    try:
        return max(1.0, float(os.environ.get(TIMEOUT_ENV) or 30))
    except Exception:                                          # noqa: BLE001
        return 30.0


def start_timeout_s() -> float:
    try:
        return max(0.0, float(os.environ.get(START_TIMEOUT_ENV) or 45))
    except Exception:                                          # noqa: BLE001
        return 45.0


def max_accounts() -> int:
    try:
        return max(1, int(os.environ.get(MAX_ACCOUNTS_ENV) or MAX_ACCOUNTS_PER_CALL))
    except Exception:                                          # noqa: BLE001
        return MAX_ACCOUNTS_PER_CALL


def clamp_days(days) -> int:
    try:
        return max(1, min(int(days or DEFAULT_DAYS), 30))
    except (TypeError, ValueError):
        return DEFAULT_DAYS


def default_days() -> int:
    return clamp_days(os.environ.get(DAYS_ENV) or DEFAULT_DAYS)


def dispatch_disabled() -> bool:
    return _env_bool(NO_DISPATCH_ENV, default_when_unset=False)


def strict_mas() -> bool:
    return _env_bool(STRICT_MAS_ENV, default_when_unset=False)


def autostart_disabled() -> bool:
    return _env_bool(NO_AUTOSTART_ENV, default_when_unset=False)


def upstream_url() -> str:
    """上游 /mcp 地址（**强制尾斜杠**：/mcp 会 307，POST 不跟随会直接失败）。"""
    v = (os.environ.get(URL_ENV) or "").strip() or DEFAULT_URL
    return v if v.endswith("/") else v + "/"


def upstream_host_port() -> tuple:
    u = urllib.parse.urlparse(upstream_url())
    host = u.hostname or "127.0.0.1"
    port = u.port or (443 if u.scheme == "https" else 80)
    return host, int(port)


def python_exe() -> str:
    v = (os.environ.get(PYTHON_ENV) or "").strip()
    if v:
        return v
    return DEFAULT_PYTHON if os.path.exists(DEFAULT_PYTHON) else sys.executable


def port_open(host: str, port: int, wait: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=wait):
            return True
    except OSError:
        return False


def _ensure_upstream_sync() -> dict | None:
    """确保上游在监听：已监听 → None；否则后台拉起并轮询 ≤start_timeout_s；失败 → 结构化错误。"""
    host, port = upstream_host_port()
    if port_open(host, port):
        return None
    if autostart_disabled():
        return {"error": "上游 %s 未监听，且已禁用自动拉起（%s=1）" % (upstream_url(), NO_AUTOSTART_ENV)}
    if host not in _LOOPBACK:
        return {"error": "上游 %s 不在本机（%s），无法自动拉起" % (upstream_url(), host)}
    if not os.path.exists(UPSTREAM_ENTRY):
        return {"error": "找不到上游入口 %s，无法拉起" % UPSTREAM_ENTRY}
    py = python_exe()
    cmd = [py, UPSTREAM_ENTRY]
    try:
        os.makedirs(os.path.dirname(UPSTREAM_LOG), exist_ok=True)
        log = open(UPSTREAM_LOG, "ab")
    except Exception as e:                                     # noqa: BLE001
        return {"error": "无法打开上游日志 %s：%s" % (UPSTREAM_LOG, e)}
    try:
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(cmd, cwd=REPO_ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         creationflags=flags, close_fds=True)
    except Exception as e:                                     # noqa: BLE001
        return {"error": "拉起上游失败（%s）：%s: %s" % (" ".join(cmd), type(e).__name__, e)}
    finally:
        try:
            log.close()
        except Exception:                                      # noqa: BLE001
            pass
    print("[proxy] 上游未监听，已后台拉起：%s main_web.pyw（日志 %s）" % (py, UPSTREAM_LOG),
          file=sys.stderr, flush=True)
    deadline = time.time() + start_timeout_s()
    while time.time() < deadline:
        if port_open(host, port):
            print("[proxy] 上游已就绪 %s" % upstream_url(), file=sys.stderr, flush=True)
            return None
        time.sleep(1.0)
    return {"error": "拉起上游后等待 %.0fs 仍不可达（%s）；命令=%s，日志=%s"
                     % (start_timeout_s(), upstream_url(), " ".join(cmd), UPSTREAM_LOG),
            "started": True, "command": cmd, "log": UPSTREAM_LOG}


async def ensure_upstream() -> dict | None:
    """异步包装：确保上游在跑（失败返回结构化错误 dict，绝不抛异常）。"""
    try:
        return await asyncio.to_thread(_ensure_upstream_sync)
    except Exception as e:                                     # noqa: BLE001
        return {"error": _redact("确保上游时异常：%s: %s" % (type(e).__name__, e))}


# ── 上游 streamable-http 客户端（每次调用独立握手）───────────────────
class UpstreamError(RuntimeError):
    """上游握手/调用失败（消息已脱敏）。"""


def _headers(session_id: str | None = None) -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    tok = api_token()
    if tok:
        h["x-agent-token"] = tok                                  # 唯一出现处：请求头，绝不打印
    if session_id:
        h["mcp-session-id"] = session_id
    return h


def parse_rpc(raw: str) -> dict:
    """解析 JSON-RPC 响应：可能是 application/json，也可能是 SSE（event: message / data: {...}）。"""
    if not raw or not raw.strip():
        return {}
    text = raw.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except ValueError:
            return {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            body = line[5:].strip()
            try:
                return json.loads(body)
            except ValueError:
                continue
    return {}


def _rpc_error_text(resp) -> str:
    if not isinstance(resp, dict):
        return _redact(json.dumps(resp, ensure_ascii=False)[:200])
    err = resp.get("error")
    if isinstance(err, dict):
        return _redact("%s: %s" % (err.get("code"), err.get("message")))
    if err:
        return _redact(str(err))
    return _redact(json.dumps(resp, ensure_ascii=False)[:200])


def _post(url: str, body: dict, session_id: str | None, timeout: float) -> tuple:
    """POST 一次 JSON-RPC；返回 (解析后的响应, 响应头里的 mcp-session-id)。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(session_id), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return parse_rpc(raw), resp.headers.get("mcp-session-id")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read()[:300].decode("utf-8", "replace")
        except Exception:                                      # noqa: BLE001
            pass
        raise UpstreamError("HTTP %s %s %s" % (e.code, e.reason, _redact(detail)).strip()) from e
    except UpstreamError:
        raise
    except Exception as e:                                     # noqa: BLE001
        raise UpstreamError(_redact("%s: %s" % (type(e).__name__, e))) from e


def _close_session(url: str, session_id: str | None, timeout: float) -> None:
    """尽力关闭上游会话（DELETE），失败静默 —— 只为不把会话堆在上游内存里。"""
    if not session_id:
        return
    try:
        req = urllib.request.Request(url, headers=_headers(session_id), method="DELETE")
        urllib.request.urlopen(req, timeout=min(timeout, 5.0)).close()
    except Exception:                                          # noqa: BLE001
        pass


def _rpc_sync(method: str, params: dict | None = None, timeout: float | None = None) -> dict:
    """一次完整握手（initialize → notifications/initialized → method）并返回 result。

    上游是简化版 streamable-http：不缓存会话、每次独立握手最不易卡死（同 mas_scrub_proxy 的取舍）。
    """
    to = float(timeout or timeout_s())
    url = upstream_url()
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "maaorch-daily-proxy", "version": VERSION}}}
    resp, sid = _post(url, init, None, to)
    if not isinstance(resp, dict) or "result" not in resp:
        raise UpstreamError("initialize 失败：%s" % _rpc_error_text(resp))
    _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid, to)
    try:
        if method == "initialize":
            return resp["result"]
        resp2, _ = _post(url, {"jsonrpc": "2.0", "id": 2, "method": method, "params": params or {}},
                        sid, to)
        if isinstance(resp2, dict) and "error" in resp2:
            raise UpstreamError("%s 返回错误：%s" % (method, _rpc_error_text(resp2)))
        if not isinstance(resp2, dict) or "result" not in resp2:
            raise UpstreamError("%s 无 result：%s" % (method, _rpc_error_text(resp2)))
        return resp2["result"]
    finally:
        _close_session(url, sid, to)


def _tools_from_rpc(result) -> list:
    out = []
    for t in (result or {}).get("tools") or []:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        try:
            out.append(types.Tool.model_validate(t))
        except Exception:                                      # noqa: BLE001
            out.append(types.Tool(name=str(t["name"]), description=str(t.get("description") or ""),
                                  inputSchema={"type": "object", "properties": {}}))
    return out


async def _upstream_list_tools() -> list:
    """上游 tools/list（只读）。"""
    result = await asyncio.to_thread(_rpc_sync, "tools/list", {})
    return _tools_from_rpc(result)


async def _upstream_call_tool(name: str, arguments: dict | None = None) -> types.CallToolResult:
    """上游 tools/call（调用方必须先过白名单门禁）。"""
    params: dict = {"name": name}
    if arguments:
        params["arguments"] = arguments
    print("[upstream] tools/call name=%s" % name, file=sys.stderr, flush=True)
    result = await asyncio.to_thread(_rpc_sync, "tools/call", params)
    if not isinstance(result, dict):
        raise UpstreamError("tools/call 返回不是对象：%s" % _redact(str(result)[:200]))
    try:
        return types.CallToolResult.model_validate(result)
    except Exception:                                          # noqa: BLE001
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
            isError=bool(result.get("isError")))


# ── 结果载荷（供返回值嵌入上游原始结果）────────────────────────────
def _result_json(res: types.CallToolResult) -> dict | None:
    """取返回体的 JSON 对象（structuredContent 优先，其次 content[].text）。"""
    sc = getattr(res, "structuredContent", None)
    if isinstance(sc, dict) and sc:
        return sc
    for c in res.content or []:
        if getattr(c, "type", "") == "text":
            try:
                d = json.loads(c.text)
            except ValueError:
                continue
            if isinstance(d, dict):
                return d
    return None


def _result_payload(res: types.CallToolResult) -> dict:
    """上游 CallToolResult → 可嵌入的 dict（脱敏后）。"""
    content = []
    for c in res.content or []:
        if getattr(c, "type", "") == "text":
            content.append({"type": "text", "text": scrub_text(getattr(c, "text", ""))})
        else:
            try:
                content.append(scrub(c.model_dump()))
            except Exception:                                  # noqa: BLE001
                content.append({"type": str(getattr(c, "type", "unknown"))})
    sc = getattr(res, "structuredContent", None)
    out = {"isError": bool(res.isError), "content": content,
           "structuredContent": scrub(sc) if sc is not None else None}
    return scrub(out)


async def _read_source(name: str, arguments: dict | None = None) -> dict:
    """只读源读取：返回 JSON 对象；ok=false/非 JSON 一律当读取失败抛 UpstreamError。"""
    res = await _upstream_call_tool(name, arguments)
    payload = _result_json(res)
    if payload is None:
        raise UpstreamError("%s 返回不是 JSON 对象" % name)
    if payload.get("ok") is False:
        raise UpstreamError("%s 返回 ok=false：%s" % (name, payload.get("error") or payload.get("detail") or ""))
    return payload


# ── 纯函数：预检判定与门禁（可离线单测）─────────────────────────────
def today_str() -> str:
    return time.strftime("%Y-%m-%d")


def _extract_account_rows(payload) -> list:
    rows = (payload or {}).get("accounts")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _mas_host_accounts(payload) -> dict:
    host = (payload or {}).get("host")
    recs = (host or {}).get("accounts") if isinstance(host, dict) else None
    return {str(k): v for k, v in recs.items() if isinstance(v, dict)} if isinstance(recs, dict) else {}


def is_today_done(row: dict, today: str) -> bool:
    """今日是否已清完 —— 判定同 network/api_fastapi.py:852-855（sanity_pct<=20 且 done_ts 是今天）。"""
    if not isinstance(row, dict):
        return False
    pct = row.get("sanity_pct")
    if pct is None:
        return False
    try:
        if float(pct) > DONE_SANITY_PCT:
            return False
    except (TypeError, ValueError):
        return False
    return str(row.get("done_ts") or "").startswith(today)


def account_verdict(row, today: str) -> str:
    """账号判定：unknown / running / queued / already_done / gap。"""
    if not isinstance(row, dict):
        return "unknown"
    if row.get("running"):
        return "running"
    if row.get("queued"):
        return "queued"
    if is_today_done(row, today):
        return "already_done"
    return "gap"


def _batch_evidence(reads: dict, *, strict: bool) -> dict:
    """汇总"批次是否在跑"的只读证据（纯函数，输入是 4 个只读工具的返回体）。"""
    ev: list = []
    unknown: list = []
    q = reads.get("queue")
    q = q if isinstance(q, dict) else {}
    active = q.get("active_count")
    try:
        active = int(active) if active is not None else len(q.get("active") or [])
    except (TypeError, ValueError):
        active = len(q.get("active") or [])
    try:
        pending = int(q.get("pending_count") if q.get("pending_count") is not None
                      else len(q.get("pending") or []))
    except (TypeError, ValueError):
        pending = len(q.get("pending") or [])
    if reads.get("queue") is None:
        unknown.append("queue")
    elif active > 0:
        ev.append("queue.active_count=%d" % active)

    rows = _extract_account_rows(reads.get("account_status"))
    if reads.get("account_status") is None:
        unknown.append("account_status")
    running = [str(r.get("id") or r.get("name")) for r in rows if r.get("running")]
    queued = [str(r.get("id") or r.get("name")) for r in rows if r.get("queued")]
    if running:
        ev.append("account_status.running=%d(%s)" % (len(running), ",".join(running[:5])))
    if queued:
        ev.append("account_status.queued=%d(%s)" % (len(queued), ",".join(queued[:5])))

    dash = reads.get("dashboard") if isinstance(reads.get("dashboard"), dict) else {}
    cap = dash.get("capacity") if isinstance(dash.get("capacity"), dict) else {}
    dash_running = cap.get("running")
    try:
        dash_running = int(dash_running) if dash_running is not None else None
    except (TypeError, ValueError):
        dash_running = None
    if reads.get("dashboard") is None:
        unknown.append("dashboard")
    elif dash_running:
        ev.append("dashboard.capacity.running=%d" % dash_running)

    mas = reads.get("mas_status") if isinstance(reads.get("mas_status"), dict) else {}
    if reads.get("mas_status") is None:
        unknown.append("mas_status")
    rerun = mas.get("rerun") if isinstance(mas.get("rerun"), dict) else {}
    rerun_running = bool(rerun.get("running")) if "running" in rerun else None
    if rerun_running:
        ev.append("mas_status.rerun.running=true")
    host = mas.get("host") if isinstance(mas.get("host"), dict) else {}
    maa_proc = host.get("maa_processes")
    try:
        maa_proc = int(maa_proc) if maa_proc is not None else None
    except (TypeError, ValueError):
        maa_proc = None
    if strict and maa_proc:
        ev.append("mas_status.host.maa_processes=%d（strict）" % maa_proc)

    return {"running": bool(ev), "evidence": ev, "unknown": unknown,
            "local_queue_active": active, "local_queue_pending": pending,
            "local_accounts_running": running, "local_accounts_queued": queued,
            "dashboard_running": dash_running, "mas_rerun_running": rerun_running,
            "mas_host_maa_processes": maa_proc, "strict_mas": bool(strict)}


def _mas_usable(mas) -> tuple:
    """mas_status 是否可信（ok=false 或主机快照不可用 → 不可信，无法确认 AUTO-MAS 侧状态）。"""
    if not isinstance(mas, dict):
        return False, "无返回"
    if mas.get("ok") is False:
        return False, str(mas.get("error") or mas.get("detail") or "ok=false")[:200]
    host = mas.get("host")
    if isinstance(host, dict) and host.get("ok") is False:
        return False, str(host.get("error") or "主机快照不可用")[:200]
    return True, ""


# 预检必须成功的数据源（本地 ground truth + MAS 侧在跑记录）；dashboard 是冗余信号，允许缺失
REQUIRED_READS = ("account_status", "queue", "mas_status")


def _build_precheck(reads: dict, errors: list, inputs, days: int, *, strict: bool | None = None) -> dict:
    """由 4 个只读返回体算出：账号判定 + 批次证据 + 汇总（纯函数，不发任何请求）。"""
    strict = strict_mas() if strict is None else bool(strict)
    today = today_str()
    errors = list(errors or [])
    required_failed = sorted({str(e.get("tool")) for e in errors
                              if str(e.get("tool")) in REQUIRED_READS})
    mas = reads.get("mas_status")
    mas_ok, mas_why = _mas_usable(mas)
    if not mas_ok and "mas_status" not in required_failed:
        required_failed.append("mas_status")
        errors.append({"tool": "mas_status", "error": _redact("不可用：%s" % mas_why)})
    batch = _batch_evidence(reads, strict=strict)

    rows = _extract_account_rows(reads.get("account_status"))
    by_id = {str(r.get("id")): r for r in rows if r.get("id") is not None}
    by_name = {str(r.get("name")): r for r in rows if r.get("name")}

    accounts = []
    submit_ids: list = []
    unknown_ids: list = []
    skipped_done: list = []
    for raw in inputs or []:
        token = str(raw)
        row = by_id.get(token) or by_name.get(token)
        verdict = account_verdict(row, today)
        item = {"input": token, "id": None, "name": None, "verdict": verdict,
                "today_done": bool(verdict == "already_done"),
                "running": bool(row.get("running")) if isinstance(row, dict) else None,
                "queued": bool(row.get("queued")) if isinstance(row, dict) else None,
                "sanity_pct": row.get("sanity_pct") if isinstance(row, dict) else None,
                "done_ts": row.get("done_ts") if isinstance(row, dict) else None,
                "today_runs": row.get("today_runs") if isinstance(row, dict) else None}
        if isinstance(row, dict):
            item["id"] = row.get("id")
            item["name"] = row.get("name")
        if verdict == "unknown":
            unknown_ids.append(token)
        elif verdict == "already_done":
            skipped_done.append(token)
        elif verdict in ("running", "queued"):
            pass                                     # 在跑的号不算缺口，也不当"今日已清"
        else:
            submit_ids.append(str(row.get("id")))    # 统一用上游账号 id 提交
        accounts.append(item)

    all_accounts = []
    for r in rows:
        v = account_verdict(r, today)
        if v == "already_done":
            why = "今日已清完（sanity_pct<=%g 且 done_ts=%s）" % (DONE_SANITY_PCT, r.get("done_ts"))
        elif v == "unknown":
            why = "不在上游账号清单里"
        else:
            why = "今日无 DONE 记录（done_ts=%s, sanity_pct=%s）" % (r.get("done_ts") or "空",
                                                                    r.get("sanity_pct"))
        all_accounts.append({"id": r.get("id"), "name": r.get("name"), "index": r.get("index"),
                             "verdict": v, "today_done": v == "already_done",
                             "running": bool(r.get("running")), "queued": bool(r.get("queued")),
                             "sanity_pct": r.get("sanity_pct"), "done_ts": r.get("done_ts"),
                             "today_runs": r.get("today_runs"), "evidence": why})
    warnings = []
    if batch.get("unknown"):
        warnings.append("只读源缺失：%s" % ",".join(batch["unknown"]))
    if batch.get("mas_host_maa_processes"):
        warnings.append("AUTO-MAS 主机当前有 %d 个 MAA 进程（%s；如要更严可置 %s=1）"
                        % (batch["mas_host_maa_processes"],
                           "已计入批次在跑" if strict else "未计入批次判定", STRICT_MAS_ENV))
    if batch.get("local_queue_pending"):
        warnings.append("本机队列有 %d 项排队中（未计入 batch_running）" % batch["local_queue_pending"])

    mas_q = mas if isinstance(mas, dict) else {}
    q = reads.get("queue") if isinstance(reads.get("queue"), dict) else {}
    acct = reads.get("account_status") if isinstance(reads.get("account_status"), dict) else {}
    return {
        "date": today,
        "days": clamp_days(days),
        "errors": errors,
        "required_failed": required_failed,
        "batch_state_known": not required_failed,
        "batch": batch,
        "accounts": accounts,
        "all_accounts": all_accounts,
        "submit_ids": submit_ids,
        "unknown_ids": unknown_ids,
        "skipped_done": skipped_done,
        "summary": {
            "today": mas_q.get("today"),
            "daily": mas_q.get("daily"),
            "rerun": mas_q.get("rerun"),
            "mas_host": {"now": (mas_q.get("host") or {}).get("now") if isinstance(mas_q.get("host"), dict) else None,
                         "shift": (mas_q.get("host") or {}).get("shift") if isinstance(mas_q.get("host"), dict) else None,
                         "maa_processes": batch.get("mas_host_maa_processes"),
                         # 仅信息展示，不参与"今日已清"判定：本地账号与 AUTO-MAS 账号是两套命名空间
                         "today_records": len(_mas_host_accounts(mas_q)),
                         "waiting": mas_q.get("waiting")},
            "queue": {"pending_count": q.get("pending_count"), "active_count": q.get("active_count"),
                      "paused": q.get("paused")},
            "account_stats": acct.get("stats"),
        },
        "warnings": warnings,
    }


def _refusal_code(precheck: dict, ids, confirm: bool) -> tuple:
    """confirm=True 时的硬门禁（纯函数）：返回 (原因码, 中文原因)；全部通过 → ("", "")。

    顺序：数量 → 发车硬闸 → 只读预检完整性 → 批次在跑 → 陌生账号 → 无缺口。
    陌生账号放在 no_gap 之前，是因为"拼错的 id"必须先暴露出来，
    否则会被"无缺口"这个看起来正常的理由掩盖（预演时 would_refuse 同理）。
    """
    if confirm is not True:
        return "", ""
    if len(ids) > max_accounts():
        return R_TOO_MANY, "%s（本批 %d 个 > 上限 %d 个）" % (_REFUSAL_TEXT[R_TOO_MANY], len(ids), max_accounts())
    if dispatch_disabled():
        return R_DISABLED, _REFUSAL_TEXT[R_DISABLED]
    if not precheck.get("batch_state_known"):
        return R_READONLY, "%s（读取失败：%s）" % (_REFUSAL_TEXT[R_READONLY],
                                                ",".join(precheck.get("required_failed") or ["未知"]))
    if (precheck.get("batch") or {}).get("running"):
        return R_BATCH, "%s：%s" % (_REFUSAL_TEXT[R_BATCH],
                                    "; ".join((precheck.get("batch") or {}).get("evidence") or []))
    if precheck.get("unknown_ids"):
        return R_UNKNOWN, "%s：%s" % (_REFUSAL_TEXT[R_UNKNOWN], ",".join(precheck["unknown_ids"]))
    if not precheck.get("submit_ids"):
        return R_NO_GAP, "%s（%d 个目标均已清完）" % (_REFUSAL_TEXT[R_NO_GAP], len(precheck.get("skipped_done") or []))
    return "", ""


def _parse_daily_args(arguments) -> tuple:
    """daily_clear 参数解析（严格；confirm 必须是真正的布尔 True 才可能发车）。"""
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return {}, "arguments 必须是对象"
    unknown = sorted(set(arguments) - {"account_ids", "confirm", "days"})
    if unknown:
        return {}, "未知参数 %s（只接受 account_ids / confirm / days）" % unknown
    raw = arguments.get("account_ids")
    if isinstance(raw, str):
        return {}, "account_ids 必须是字符串列表（不接受逗号分隔字符串）"
    if not isinstance(raw, (list, tuple)):
        return {}, "account_ids 必须是字符串列表"
    ids: list = []
    for x in raw:
        if not isinstance(x, str) or not x.strip():
            return {}, "account_ids 的元素必须是非空字符串"
        x = x.strip()
        if x not in ids:
            ids.append(x)
    if not ids:
        return {}, "account_ids 不能为空"
    confirm = arguments.get("confirm", False)
    warn = ""
    if not isinstance(confirm, bool):
        warn = "confirm 收到非布尔值（%s），已按 False（预演）处理 —— 只有布尔 true 才算确认" % type(confirm).__name__
        confirm = False
    return {"account_ids": ids, "confirm": confirm, "days": clamp_days(arguments.get("days")), "warning": warn}, ""


def _parse_gap_args(arguments) -> tuple:
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return {}, "arguments 必须是对象"
    unknown = sorted(set(arguments) - {"days"})
    if unknown:
        return {}, "未知参数 %s（只接受 days）" % unknown
    return {"days": clamp_days(arguments.get("days"))}, ""


def _parse_rerun_args(arguments) -> tuple:
    """mas_rerun 参数解析：accounts(list[str] | null) / dry_run(bool) **原样转发**。

    只有非布尔 dry_run 会被 fail-closed 按 True（预演）处理并记 warning；
    布尔 False 一律原样保留（不替调用方改值 —— 是否真下发由上游 dry_run 语义决定）。
    """
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return {}, "arguments 必须是对象"
    unknown = sorted(set(arguments) - {"accounts", "dry_run"})
    if unknown:
        return {}, "未知参数 %s（只接受 accounts / dry_run）" % unknown
    raw = arguments.get("accounts")
    accounts = None
    if raw is not None:
        if isinstance(raw, str):
            return {}, "accounts 必须是字符串列表或 null（不接受逗号分隔字符串）"
        if not isinstance(raw, (list, tuple)):
            return {}, "accounts 必须是字符串列表或 null"
        accounts = []
        for x in raw:
            if not isinstance(x, str) or not x.strip():
                return {}, "accounts 的元素必须是非空字符串"
            x = x.strip()
            if x not in accounts:
                accounts.append(x)                             # 去重但保持调用方顺序
    dry_run = arguments.get("dry_run", True)
    warn = ""
    if not isinstance(dry_run, bool):
        warn = "dry_run 收到非布尔值（%s），已按 True（预演）处理" % type(dry_run).__name__
        dry_run = True
    return {"accounts": accounts, "dry_run": dry_run, "warning": warn}, ""


def _audit_rerun(accounts, dry_run, dispatched: bool, extra: str = "") -> None:
    """mas_rerun 审计日志（stderr 一行）：工具名 / accounts 数量 / dry_run 值 / 是否真下发 / 结果。"""
    print("[audit] tool=%s accounts=%s dry_run=%s dispatched=%s%s"
          % (RERUN_TOOL, "null" if accounts is None else len(accounts), dry_run, bool(dispatched),
             (" " + extra) if extra else ""), file=sys.stderr, flush=True)


# ── MCP server（stdio 侧）───────────────────────────────────────────
_DAILY_CLEAR_SCHEMA = {
    "type": "object",
    "properties": {
        "account_ids": {"type": "array", "items": {"type": "string"},
                        "description": "要清日常的账号（上游账号 id 或账号名，最多 %d 个）" % MAX_ACCOUNTS_PER_CALL},
        "confirm": {"type": "boolean", "default": False,
                    "description": "默认 false：只做只读预检 + 回显将要提交的 smart_dispatch 载荷，绝不发车；"
                                   "显式 true 才可能发车（还要过 batch_running / no_gap / unknown_account 等硬门禁）"},
        "days": {"type": "integer", "default": DEFAULT_DAYS,
                 "description": "预检汇总回看的自然日数（1-30，缺省 %d）" % DEFAULT_DAYS},
    },
    "required": ["account_ids"],
    "additionalProperties": False,
}
_DAILY_GAP_SCHEMA = {
    "type": "object",
    "properties": {"days": {"type": "integer", "default": DEFAULT_DAYS,
                            "description": "回看的自然日数（1-30，缺省 %d）" % DEFAULT_DAYS}},
    "additionalProperties": False,
}

_STATIC_READONLY_DESC = {
    "dashboard": "【只读】节点/账号总览看板（上游未返回 schema 时的兜底定义）",
    "account_status": "【只读】全部账号状态：连接/运行/体力/今日运行与完成情况",
    "mas_status": "【只读】AUTO-MAS 产线总览：主机快照 + 今日/近 N 天发车聚合 + 补跑状态",
    "mas_snapshot": "【只读】AUTO-MAS 主机实时快照（在飞 MAA / 盘位 / 队列进度）",
    "queue": "【只读】本机调度队列状态（pending / active / paused）",
    "health": "【只读】服务健康检查（进程/看门狗/关键服务）",
    "mas_rerun_state": "【只读】后台补跑进度：running / accounts / started_at / ok / detail",
    "mas_rerun": "【写：dry_run 缺省 true=只预演】上游原生补跑工具，原样转发 accounts/dry_run（单批 ≤%d）"
                 % MAX_RERUN_ACCOUNTS,
}


def _static_tool(name: str, description: str = "", schema: dict | None = None) -> types.Tool:
    return types.Tool(name=name, description=description or _STATIC_READONLY_DESC.get(name, name),
                      inputSchema=schema or {"type": "object", "properties": {}})


def _daily_clear_tool() -> types.Tool:
    return _static_tool(DAILY_TOOL,
                        "【受控写：默认预演】清日常：只读预检（今日完成情况 + 批次是否在跑 + 账号是否认识）"
                        "后，把将要提交的 smart_dispatch 载荷原样回显；confirm=False（默认）**绝不发车**。"
                        "confirm=True 时才发车，且 account_ids 非空且 ≤%d、无批次在跑、剔除今日已清完的号、"
                        "无陌生号，否则返回 {ok:false, refused:<原因>} 不发车。"
                        "发车后返回上游原始结果（脱敏）。" % MAX_ACCOUNTS_PER_CALL,
                        _DAILY_CLEAR_SCHEMA)


def _daily_gap_tool() -> types.Tool:
    return _static_tool(GAP_TOOL,
                        "【只读，永不写】今日清日常缺口：用只读工具算出今天还没清完、且当前没有批次在跑的账号清单，"
                        "含每个账号的判定依据（done_ts / sanity_pct / today_runs）与批次在跑证据。",
                        _DAILY_GAP_SCHEMA)


def _exposed_definitions(upstream_tools: list) -> list:
    """白名单工具的对外定义：入参 schema 用上游真值，**出参 schema 一律剥掉**。

    上游 FastMCP 工具带 outputSchema，而本代理是"脱敏 + 重排"层（daily_clear 返回自造结构、
    敏感字段值会换成占位符），一旦声明 outputSchema，MCP 客户端会强校验 structuredContent
    （mcp/client/session.py:429-439），命中即 RuntimeError —— 实测 mas_rerun_state 就会炸。
    剥掉它既保住入参契约（Hermes 需要的是怎么调），又不承诺我们满足不了的出参结构。
    """
    """暴露面恒定为 EXPOSED_TOOLS：只读 6 个保真用上游 schema，两个本地工具用本地 schema。"""
    by_name = {t.name: t for t in upstream_tools or []}
    out = []
    for n in UPSTREAM_SCHEMA_TOOLS:
        tool = by_name.get(n)
        if tool is None:
            out.append(_static_tool(n))
            continue
        if getattr(tool, "outputSchema", None) is None or not hasattr(tool, "model_copy"):
            out.append(tool)
        else:
            out.append(tool.model_copy(update={"outputSchema": None}))
    out.append(_daily_clear_tool())
    out.append(_daily_gap_tool())
    return out


def deny_reason(name: str) -> str:
    """白名单门禁：允许返回空串；否则返回拒绝原因（在触达上游之前用）。"""
    if name in EXPOSED_TOOLS:
        return ""
    if name == UPSTREAM_WRITE_TOOL or name in FORBIDDEN_UPSTREAM:
        return ("上游写/危险工具，不在本代理白名单内（本代理只放行只读转发 + %s 原样转发 + "
                "%s 受控发车）" % (RERUN_TOOL, DAILY_TOOL))
    return "不在本代理白名单内"


def whitelist_problems(exposed=None) -> list:
    """导入期/自检用：暴露面里混进危险名、或缺项都算问题。"""
    exposed = tuple(exposed if exposed is not None else EXPOSED_TOOLS)
    problems = []
    for n in exposed:
        if n in FORBIDDEN_UPSTREAM:
            problems.append("暴露面混进上游写/危险工具: %s" % n)
    for n in EXPOSED_TOOLS:
        if n not in exposed:
            problems.append("暴露面缺少必需工具: %s" % n)
    return problems


_PROBLEMS = whitelist_problems()
if _PROBLEMS:                                                   # fail-closed：白名单坏了就别启动
    raise RuntimeError("工具白名单自检失败：" + "; ".join(_PROBLEMS))

server = Server(
    "maaorch-daily-proxy",
    version=VERSION,
    instructions="MAAOrch 的『清日常』受控代理：只暴露 %d 个工具（只读 %s + %s/%s + %s/%s）。"
                 "daily_clear 默认 confirm=False **只预演不发车**；confirm=True 才受控发车，"
                 "且必须过 batch_running / no_gap / unknown_account / readonly_unavailable 硬门禁。"
                 "%s(accounts, dry_run=True) 原样转发上游语义：dry_run=True 只预演、dry_run=False 真下发"
                 "（单批 ≤%d 个账号；MAAORCH_DAILY_NO_DISPATCH 置位时禁发车）。"
                 % (len(EXPOSED_TOOLS), "/".join(READONLY_WHITELIST), RERUN_STATE_TOOL, RERUN_TOOL,
                    DAILY_TOOL, GAP_TOOL, RERUN_TOOL, MAX_RERUN_ACCOUNTS),
)

_TOOLS_CACHE: list | None = None


def _json_result(payload: dict, is_error: bool = False) -> types.CallToolResult:
    text = _redact(json.dumps(scrub(payload), ensure_ascii=False))
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                isError=bool(is_error))


def _error_result(message: str, **extra) -> types.CallToolResult:
    payload = {"ok": False, "error": _redact(message)}
    payload.update(extra)
    print("[proxy] 错误：%s" % payload["error"], file=sys.stderr, flush=True)
    return _json_result(payload, is_error=True)


def _deny_result(name: str, reason: str) -> types.CallToolResult:
    print("[gate] 拒绝工具 name=%s reason=%s（零上游请求）" % (name, reason), file=sys.stderr, flush=True)
    return _json_result({"ok": False, "error": "工具不在白名单内，已拒绝", "reason": reason,
                         "tool": name, "upstream_calls": 0,
                         "hint": "本代理只放行 %s；清日常请用 %s(account_ids, confirm=False)"
                                 % ("/".join(EXPOSED_TOOLS), DAILY_TOOL)}, is_error=True)


@server.list_tools()
async def _handle_list_tools() -> list:
    """只返回白名单工具；上游 schema 首次成功后缓存（工具面恒定，不随上游抖动）。"""
    global _TOOLS_CACHE
    if _TOOLS_CACHE is None:
        try:
            _TOOLS_CACHE = await _upstream_list_tools()
        except Exception as e:                                 # noqa: BLE001
            print("[proxy] 上游 tools/list 不可用（退回静态定义）：%s: %s"
                  % (type(e).__name__, _redact(str(e))), file=sys.stderr, flush=True)
    tools = _exposed_definitions(_TOOLS_CACHE or [])
    names = [t.name for t in tools]
    hidden = len(_TOOLS_CACHE or [])
    print("[list] tools/list -> %d 个（白名单；上游 %d 个中隐藏 %d 个）"
          % (len(names), hidden, max(0, hidden - len(UPSTREAM_SCHEMA_TOOLS))), file=sys.stderr, flush=True)
    return tools


async def _readonly_precheck(inputs, days: int) -> dict:
    """拉 4 个只读工具并算出预检结果（任何读失败都记进 errors，不抛异常）。"""
    reads: dict = {n: None for n in ("account_status", "queue", "dashboard", "mas_status")}
    errors: list = []
    ensure_err = await ensure_upstream()
    if ensure_err:
        errors.append({"tool": "upstream", "error": _redact(str(ensure_err.get("error") or "上游不可用"))})
        for n in reads:
            errors.append({"tool": n, "error": "跳过（上游不可用）"})
        return _build_precheck(reads, errors, inputs, days)
    for name, args in (("account_status", {}), ("queue", {}), ("dashboard", {}),
                       ("mas_status", {"days": clamp_days(days)})):
        try:
            reads[name] = await _read_source(name, args)
        except Exception as e:                                 # noqa: BLE001
            errors.append({"tool": name, "error": _redact("%s: %s" % (type(e).__name__, e))})
    return _build_precheck(reads, errors, inputs, days)


async def _handle_daily_clear(arguments) -> types.CallToolResult:
    """daily_clear：默认预演（零写请求）；confirm=True 才过门禁发车。"""
    args, err = _parse_daily_args(arguments)
    if err:
        return _json_result({"ok": False, "tool": DAILY_TOOL, "error": err, "refused": R_BAD_ARGS,
                             "dispatched": False, "account_ids": [],
                             "hint": "account_ids 必填（字符串列表，最多 %d 个）；confirm 缺省 False" % max_accounts()},
                            is_error=True)
    ids = args["account_ids"]
    confirm = args["confirm"]
    days = args["days"]
    pre = await _readonly_precheck(ids, days)
    refused, reason = _refusal_code(pre, ids, confirm)
    would_refuse, would_reason = _refusal_code(pre, ids, True)
    will_ids = list(pre["submit_ids"])
    will_call_args = {"account_ids": will_ids, "include_anni": True, "only_anni": False}

    payload = {
        "ok": not refused,
        "tool": DAILY_TOOL,
        "date": pre["date"],
        "confirm": confirm,
        "dry_run": True,
        "dispatched": False,
        "refused": refused or None,
        "refused_reason": reason or None,
        "would_refuse": None if confirm else (would_refuse or None),
        "would_refuse_reason": None if confirm else (would_reason or None),
        "candidate_account_ids": will_ids,
        "account_ids": [],
        "will_call": {"tool": UPSTREAM_WRITE_TOOL, "arguments": will_call_args},
        "precheck": {
            "date": pre["date"], "days": pre["days"], "accounts": pre["accounts"], "batch": pre["batch"],
            "batch_state_known": pre["batch_state_known"], "required_failed": pre["required_failed"],
            "errors": pre["errors"], "skipped_done": pre["skipped_done"],
            "unknown_account_ids": pre["unknown_ids"], "summary": pre["summary"],
        },
        "warnings": list(pre["warnings"]),
        "hint": "默认预演：本次没有向 smart_dispatch 发送任何内容；确认无误后显式传 confirm=True 才发车。",
    }
    if args.get("warning"):
        payload["warnings"].append(args["warning"])

    if confirm is not True:          # 布尔身份判断：非 True（含字符串 "true"）一律预演，绝不发车
        print("[daily_clear] 预演 account_ids=%s 候选=%s would_refuse=%s（零上游写）"
              % (ids, will_ids, payload["would_refuse"]), file=sys.stderr, flush=True)
        return _json_result(payload)

    if refused:
        payload["hint"] = "门禁拒绝：%s —— 未向 smart_dispatch 发送任何内容。" % (reason or refused)
        print("[daily_clear] 门禁拒绝 refused=%s ids=%s（零上游写）" % (refused, ids),
              file=sys.stderr, flush=True)
        return _json_result(payload, is_error=True)

    commit = {"account_ids": will_ids, "include_anni": True, "only_anni": False}
    payload["will_call"] = {"tool": UPSTREAM_WRITE_TOOL, "arguments": commit}
    try:
        res = await _upstream_call_tool(UPSTREAM_WRITE_TOOL, commit)
    except Exception as e:                                     # noqa: BLE001
        msg = _redact("%s: %s" % (type(e).__name__, e))
        print("[daily_clear] 发车调用失败：%s" % msg, file=sys.stderr, flush=True)
        payload.update({"ok": False, "error": msg, "dispatched": False, "dispatch_unknown": True,
                        "hint": "发车请求失败（是否已在上游执行未知，请用 queue/account_status 复核）"})
        return _json_result(payload, is_error=True)
    upstream = _result_payload(res)
    payload.update({"ok": not bool(res.isError), "dispatched": True, "dry_run": False,
                    "account_ids": list(will_ids), "upstream": {"tool": UPSTREAM_WRITE_TOOL,
                                                                "arguments": commit, "result": upstream},
                    "hint": "已调用上游 %s（结果见 upstream.result，敏感字段已脱敏）" % UPSTREAM_WRITE_TOOL})
    print("[daily_clear] 已发车 account_ids=%s 上游 isError=%s" % (will_ids, res.isError),
          file=sys.stderr, flush=True)
    return _json_result(payload, is_error=bool(res.isError))


async def _handle_daily_gap(arguments) -> types.CallToolResult:
    """daily_gap：只读缺口（今日没清完 + 当前没有批次在跑），**永不写**。"""
    args, err = _parse_gap_args(arguments)
    if err:
        return _json_result({"ok": False, "tool": GAP_TOOL, "error": err}, is_error=True)
    pre = await _readonly_precheck([], args["days"])
    gaps = [a for a in pre["all_accounts"] if a["verdict"] == "gap"]
    done = [a for a in pre["all_accounts"] if a["verdict"] == "already_done"]
    busy = [a for a in pre["all_accounts"] if a["verdict"] in ("running", "queued")]
    batch_running = bool(pre["batch"].get("running"))
    dispatchable = [a["id"] for a in gaps] if (pre["batch_state_known"] and not batch_running) else []
    payload = {
        "ok": pre["batch_state_known"],
        "degraded": not pre["batch_state_known"],
        "tool": GAP_TOOL,
        "date": pre["date"],
        "days": pre["days"],
        "batch": pre["batch"],
        "accounts_total": len(pre["all_accounts"]),
        "gap_count": len(gaps),
        "gaps": gaps,
        "done_today": done,
        "busy": busy,
        "dispatchable_account_ids": dispatchable,
        "readonly_errors": pre["errors"],
        "summary": pre["summary"],
        "warnings": pre["warnings"],
        "hint": "本工具只读、永不发车。dispatchable_account_ids 仅在没有批次在跑且预检完整时给出；"
                "要发车请用 %s(account_ids=<上面的 id>, confirm=True) 并接受同一套硬门禁。" % DAILY_TOOL,
    }
    print("[daily_gap] 缺口=%d 已清=%d 忙碌=%d batch_running=%s（零上游写）"
          % (len(gaps), len(done), len(busy), batch_running), file=sys.stderr, flush=True)
    return _json_result(payload)


async def _handle_mas_rerun(arguments) -> types.CallToolResult:
    """mas_rerun：上游原生补跑工具，**原样转发** accounts/dry_run（不改调用方的 dry_run）。

    - dry_run 缺省 True ⇒ 上游 network/mcp_server.py:104-124 的 _gate() 只回显、**绝不下发**；
      只有显式 dry_run=False 才真下发。本代理只加两道本地闸：单批 ≤ MAX_RERUN_ACCOUNTS、
      MAAORCH_DAILY_NO_DISPATCH 置位时拒绝（两道闸都是零上游请求）。
    - accounts: list[str] | null；null/缺省 ⇒ 由上游按主机快照的失败号解析候选。
    - 每次调用写一行审计日志；返回值 = 上游返回体（脱敏后**原样**，不额外包一层）。
    """
    args, err = _parse_rerun_args(arguments)
    if err:
        _audit_rerun(None, None, False, "refused=" + R_BAD_ARGS)
        return _json_result({"ok": False, "tool": RERUN_TOOL, "error": err, "refused": R_BAD_ARGS,
                             "dispatched": False, "dry_run": None, "upstream_calls": 0,
                             "hint": "mas_rerun 只接受 accounts（字符串列表或 null）与 dry_run"
                                     "（布尔，缺省 true=只预演）"}, is_error=True)
    accounts = args["accounts"]
    dry_run = args["dry_run"]
    warn = " warning=non_bool_dry_run" if args.get("warning") else ""
    if accounts is not None and len(accounts) > MAX_RERUN_ACCOUNTS:
        _audit_rerun(accounts, dry_run, False, "refused=" + R_TOO_MANY + warn)
        return _json_result({"ok": False, "tool": RERUN_TOOL, "refused": R_TOO_MANY,
                             "error": "%s（本批 %d 个 > 上限 %d 个）"
                                      % (_REFUSAL_TEXT[R_TOO_MANY], len(accounts), MAX_RERUN_ACCOUNTS),
                             "dispatched": False, "dry_run": dry_run, "accounts_count": len(accounts),
                             "upstream_calls": 0,
                             "hint": "单批补跑最多 %d 个账号（上游 mas_adapter 同样只取前 8 个）"
                                     % MAX_RERUN_ACCOUNTS}, is_error=True)
    if dry_run is not True and dispatch_disabled():
        _audit_rerun(accounts, dry_run, False, "refused=" + R_DISABLED + warn)
        return _json_result({"ok": False, "tool": RERUN_TOOL, "refused": R_DISABLED,
                             "error": _REFUSAL_TEXT[R_DISABLED], "dispatched": False,
                             "dry_run": dry_run, "upstream_calls": 0,
                             "hint": "发车硬闸置位；改用 dry_run=True 预演，或去掉 %s" % NO_DISPATCH_ENV},
                            is_error=True)
    ensure_err = await ensure_upstream()
    if ensure_err:
        msg = _redact(str(ensure_err.get("error") or "上游不可用"))
        _audit_rerun(accounts, dry_run, False, "error=upstream_unavailable" + warn)
        return _json_result({"ok": False, "tool": RERUN_TOOL, "error": msg, "dry_run": dry_run,
                             "dispatched": False, "upstream_calls": 0, "upstream": upstream_url(),
                             "hint": "可手动跑 tools/start_python_upstream.bat 后重试"},
                            is_error=True)
    forward: dict = {"dry_run": dry_run}                        # dry_run 原样（不改调用方的值）
    if accounts is not None:
        forward["accounts"] = accounts
    try:
        res = await _upstream_call_tool(RERUN_TOOL, forward)
    except Exception as e:                                     # noqa: BLE001
        msg = _redact("%s: %s" % (type(e).__name__, e))
        _audit_rerun(accounts, dry_run, False, "error=upstream_call_failed" + warn)
        return _json_result({"ok": False, "tool": RERUN_TOOL, "error": msg, "dry_run": dry_run,
                             "dispatched": False, "dispatch_unknown": dry_run is not True,
                             "hint": "上游调用失败；dry_run=False 时是否已下发未知，请用 %s 复核"
                                     % RERUN_STATE_TOOL}, is_error=True)
    _audit_rerun(accounts, dry_run, dry_run is not True,
                 "upstream_isError=%s" % bool(res.isError) + warn)
    payload = _result_json(res)
    if payload is None:
        return _json_result(_result_payload(res), is_error=bool(res.isError))
    return _json_result(payload, is_error=bool(res.isError))    # 原样（脱敏）返回上游返回体


@server.call_tool(validate_input=False)
async def _handle_call_tool(name: str, arguments: dict | None) -> types.CallToolResult:
    """工具体入口：白名单外**先拒绝**（零上游请求），再谈转发。"""
    if name == RERUN_TOOL:
        return await _handle_mas_rerun(arguments)
    if name == DAILY_TOOL:
        return await _handle_daily_clear(arguments)
    if name == GAP_TOOL:
        return await _handle_daily_gap(arguments)
    reason = deny_reason(name)
    if reason:
        return _deny_result(name, reason)
    ensure_err = await ensure_upstream()
    if ensure_err:
        return _error_result(str(ensure_err.get("error") or "上游不可用"), tool=name,
                             upstream=upstream_url(), hint="可手动跑 tools/start_python_upstream.bat 后重试")
    try:
        res = await _upstream_call_tool(name, arguments or None)
    except Exception as e:                                     # noqa: BLE001
        return _error_result("%s: %s" % (type(e).__name__, e), tool=name, upstream=upstream_url())
    # 上游结果是 JSON 对象 → 原样（脱敏后）返回，Hermes 不必再解析 content[].text；
    # 非 JSON（纯文本/图片）→ 退回 {"isError","content","structuredContent"} 包装。
    payload = _result_json(res)
    if payload is None:
        return _json_result(_result_payload(res), is_error=bool(res.isError))
    return _json_result(payload, is_error=bool(res.isError))


async def _serve() -> None:
    tok = api_token()
    print("[proxy] 启动：上游=%s token=%s 暴露=%d 个（只读 %d + %s/%s）默认预演=是 发车硬闸=%s"
          % (upstream_url(), "已配置" if tok else "未配置", len(EXPOSED_TOOLS), len(READONLY_WHITELIST),
             DAILY_TOOL, GAP_TOOL, "已置位(禁发车)" if dispatch_disabled() else "未置位"),
          file=sys.stderr, flush=True)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


# ── 自检 ────────────────────────────────────────────────────────────
def _synth_precheck(*, inputs=("A1",), rows=None, queue=None, dashboard=None, mas=None,
                    errors=None, strict: bool = False) -> dict:
    """合成预检（--check 与测试共用）；rows/queue/... 缺省给"干净"数据。"""
    rows = rows if rows is not None else [{"index": 0, "id": "A1", "name": "acct1", "running": False,
                                           "queued": False, "sanity_pct": None, "done_ts": "",
                                           "today_runs": 0}]
    reads = {
        "account_status": {"ok": True, "accounts": rows, "stats": {"total": len(rows)}},
        "queue": queue if queue is not None else {"pending": [], "active": [], "pending_count": 0,
                                                 "active_count": 0, "paused": False},
        "dashboard": dashboard if dashboard is not None else {"ok": True, "capacity": {"running": 0}},
        "mas_status": mas if mas is not None else {"ok": True, "host": {"ok": True, "maa_processes": 0},
                                                   "today": {"date": today_str(), "accounts": 0},
                                                   "daily": [], "rerun": {"running": False}},
    }
    return _build_precheck(reads, errors or [], list(inputs), DEFAULT_DAYS, strict=strict)


def _local_checks() -> dict:
    """离线自检（不连网）：白名单、参数、门禁、脱敏。"""
    checks: dict = {}
    checks["暴露面=6 只读 + mas_rerun_state/mas_rerun + daily_gap/daily_clear"] = (
        tuple(EXPOSED_TOOLS) == READONLY_WHITELIST + (RERUN_STATE_TOOL, RERUN_TOOL, DAILY_TOOL, GAP_TOOL)
        and len(EXPOSED_TOOLS) == 10)
    checks["暴露面不含任何上游写工具"] = not (set(EXPOSED_TOOLS) & set(FORBIDDEN_UPSTREAM))
    checks["上游 URL 带尾斜杠（/mcp 会 307）"] = upstream_url().endswith("/")
    checks["白名单只读名一律放行"] = all(deny_reason(n) == "" for n in SAFE_FORWARD_TOOLS)
    checks["mas_rerun 放行、写/危险名一律拒绝"] = (
        deny_reason(RERUN_TOOL) == ""
        and all(deny_reason(n) for n in (UPSTREAM_WRITE_TOOL, "account_stop", "maa_stop_all",
                                         "queue_control", "kill_audit_decide", "enqueue",
                                         "mas_cleanup", "emulator_control")))
    r_def, r_err = _parse_rerun_args({"accounts": ["a", "b"]})
    checks["mas_rerun 缺省 dry_run=True（预演）"] = (not r_err) and r_def.get("dry_run") is True
    checks["mas_rerun 不改调用方的 dry_run=False"] = _parse_rerun_args({"dry_run": False})[0].get("dry_run") is False
    checks["mas_rerun 非布尔 dry_run 按预演"] = _parse_rerun_args({"dry_run": "false"})[0].get("dry_run") is True
    checks["mas_rerun accounts 原样保序去重"] = (
        _parse_rerun_args({"accounts": ["b", "a", "b"]})[0].get("accounts") == ["b", "a"])
    checks["mas_rerun 单批上限 8（超出交门禁拒绝）"] = (
        MAX_RERUN_ACCOUNTS == 8
        and len(_parse_rerun_args({"accounts": ["a%d" % i for i in range(9)]})[0].get("accounts") or []) == 9)
    checks["mas_rerun 未知参数报错"] = bool(_parse_rerun_args({"nope": 1})[1])
    checks["空 account_ids 报错"] = bool(_parse_daily_args({"account_ids": []})[1])
    checks["非列表 account_ids 报错"] = bool(_parse_daily_args({"account_ids": "a,b"})[1])
    a2, e2 = _parse_daily_args({"account_ids": ["A1"], "confirm": "true"})
    checks["字符串 \"true\" 不算确认"] = (not e2) and a2.get("confirm") is False
    clean = _synth_precheck()
    checks["预检干净时 confirm=True 无拒绝"] = _refusal_code(clean, ["A1"], True)[0] == ""
    too_many = _synth_precheck(inputs=("A1", "A2", "A3", "A4"),
                               rows=[dict(r, id="A%d" % i) for i, r in
                                     enumerate([{"index": 0, "name": "a", "running": False, "queued": False,
                                                 "sanity_pct": None, "done_ts": "", "today_runs": 0}] * 4)])
    checks["超上限拒绝"] = _refusal_code(too_many, ["A1", "A2", "A3", "A4"], True)[0] == R_TOO_MANY
    running = _synth_precheck(queue={"pending": [], "active": [{"id": "x"}], "pending_count": 0,
                                     "active_count": 1, "paused": False})
    checks["批次在跑拒绝"] = _refusal_code(running, ["A1"], True)[0] == R_BATCH
    done = _synth_precheck(rows=[{"index": 0, "id": "A1", "name": "a", "running": False, "queued": False,
                                  "sanity_pct": 5.0, "done_ts": today_str() + " 07:10:00", "today_runs": 2}])
    checks["今日已清完拒绝"] = _refusal_code(done, ["A1"], True)[0] == R_NO_GAP
    unknown = _synth_precheck(inputs=("NOPE",))
    checks["陌生账号拒绝"] = _refusal_code(unknown, ["NOPE"], True)[0] == R_UNKNOWN
    broken = _synth_precheck(errors=[{"tool": "queue", "error": "boom"}])
    checks["只读源缺失 fail-closed"] = (_refusal_code(broken, ["A1"], True)[0] == R_READONLY
                                        and _refusal_code(broken, ["A1"], False)[0] == "")
    scrubbed = scrub({"Info": {"Password": "SYNTH-PW-1", "SklandToken": "SYNTH-TK-2", "Name": "n"}})
    checks["敏感键名脱敏"] = (scrubbed["Info"]["Password"] == PLACEHOLDER
                              and scrubbed["Info"]["SklandToken"] == PLACEHOLDER
                              and scrubbed["Info"]["Name"] == "n")
    checks["异常文本脱敏"] = ("SYNTH-PW-1" not in scrub_text('HTTP 500 {"Password":"SYNTH-PW-1"}')
                              and PLACEHOLDER in scrub_text('HTTP 500 {"Password":"SYNTH-PW-1"}'))
    checks["自启命令可解析（python 解释器 + main_web.pyw 都在）"] = (
        os.path.isfile(python_exe()) and os.path.isfile(UPSTREAM_ENTRY))
    tok = api_token()
    checks["token 不进返回值（_redact 兜底）"] = (not tok) or (
        tok not in _redact("prefix " + tok) and PLACEHOLDER in _redact("prefix " + tok))
    return checks


async def _check() -> int:
    print("== MAAOrch 清日常代理 --check ==")
    tok = api_token()
    print("upstream = %s | 鉴权 token = %s | 超时 = %.0fs | MAX_ACCOUNTS = %d | 默认天数 = %d"
          % (upstream_url(), "已配置" if tok else "未配置", timeout_s(), max_accounts(), default_days()))
    print("暴露工具 = %d（只读 %d + %s/%s + %s/%s，上游 41 个里隐藏 %d 个）｜发车硬闸 = %s"
          % (len(EXPOSED_TOOLS), len(READONLY_WHITELIST), RERUN_STATE_TOOL, RERUN_TOOL,
             DAILY_TOOL, GAP_TOOL, len(FORBIDDEN_UPSTREAM),
             "置位" if dispatch_disabled() else "未置位（仍需 confirm=True）"))
    fails: list = []

    def report(name, ok, extra=""):
        print("   %s %s%s" % ("PASS" if ok else "FAIL", name, (" | " + extra) if extra else ""))
        if not ok:
            fails.append(name)

    print("[A] 本地规则桩（白名单/参数/门禁/脱敏，不连网）")
    for k, v in _local_checks().items():
        report(k, bool(v))

    print("[B] 上游可达 + 直连只读清点")
    sample_ids: list = []
    ensure_err = await ensure_upstream()
    if ensure_err:
        report("上游可达", False, str(ensure_err.get("error") or "")[:160])
    else:
        report("上游可达", True, upstream_url())
        try:
            ups = await _upstream_list_tools()
            names = {t.name for t in ups}
            report("上游 tools/list 可读", len(names) > 0, "%d 个工具" % len(names))
            report("上游含全部白名单只读工具", set(READONLY_WHITELIST) <= names)
            report("上游含 %s / %s（原样转发的补跑链路）" % (RERUN_TOOL, RERUN_STATE_TOOL),
                   {RERUN_TOOL, RERUN_STATE_TOOL} <= names)
            report("上游含 %s（写工具本体，只经门禁触达）" % UPSTREAM_WRITE_TOOL, UPSTREAM_WRITE_TOOL in names)
            acct = await _read_source("account_status", {})
            rows = _extract_account_rows(acct)
            sample_ids = [str(r.get("id")) for r in rows if r.get("id")][:1]
            report("上游只读可调用（account_status）", bool(rows), "账号数=%d 判定=%s"
                   % (len(rows), [account_verdict(r, today_str()) for r in rows][:5]))
            report("样例账号 id 可得（供预演桩用）", bool(sample_ids),
                   "取 1 个，不打印其它内容" if sample_ids else "账号清单为空")
        except Exception as e:                                 # noqa: BLE001
            report("上游只读清点", False, "%s: %s" % (type(e).__name__, _redact(str(e))[:160]))

    print("[C] 代理链路（子进程 stdio）：白名单过滤 + 默认预演零写 + 硬闸")
    from mcp.client.stdio import StdioServerParameters, stdio_client
    env = dict(os.environ)
    env[URL_ENV] = upstream_url()
    env[NO_DISPATCH_ENV] = "1"                                  # 演练：即使 confirm=True 也禁止发车
    env[NO_AUTOSTART_ENV] = "1"
    errlog = open(os.path.join(tempfile.gettempdir(), "maaorch_daily_proxy_child.log"), "w+",
                  encoding="utf-8")
    params = StdioServerParameters(command=sys.executable, args=[os.path.abspath(__file__)], env=env)
    try:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with _client_session(read, write) as sess:
                init = await sess.initialize()
                report("子进程 initialize 成功", bool(getattr(init, "serverInfo", None)),
                       "serverInfo=%s" % getattr(getattr(init, "serverInfo", None), "name", None))
                tl = await sess.list_tools()
                names = sorted(t.name for t in tl.tools)
                report("tools/list 只暴露白名单", names == sorted(EXPOSED_TOOLS), "实际=%s" % names)
                report("危险写工具不在 tools/list", not (set(names) & set(FORBIDDEN_UPSTREAM)))
                report("暴露工具已剥掉 outputSchema（客户端不会强校验 structuredContent）",
                       all(getattr(t, "outputSchema", None) is None for t in tl.tools))
                hl = await sess.call_tool("health", {})
                report("只读转发 health 可用（上游 outputSchema 不再拖垮调用）",
                       (not hl.isError) and isinstance(_payload_of(hl), dict))
                denied = await sess.call_tool(UPSTREAM_WRITE_TOOL, {"account_ids": ["x"]})
                d0 = _payload_of(denied)
                report("直呼 %s 被门禁拒绝" % UPSTREAM_WRITE_TOOL,
                       bool(denied.isError) and d0.get("ok") is False
                       and "白名单" in json.dumps(d0, ensure_ascii=False), str(d0.get("reason"))[:80])
                ids = sample_ids or ["probe-not-a-real-id"]
                prev = await sess.call_tool(DAILY_TOOL, {"account_ids": ids})
                d1 = _payload_of(prev)
                pc = d1.get("precheck") or {}
                report("daily_clear 默认预演 dispatched=false", d1.get("dispatched") is False
                       and d1.get("dry_run") is True and d1.get("account_ids") == [])
                report("daily_clear 预演回显 smart_dispatch 载荷",
                       (d1.get("will_call") or {}).get("tool") == UPSTREAM_WRITE_TOOL
                       and sorted(((d1.get("will_call") or {}).get("arguments") or {}))
                       == ["account_ids", "include_anni", "only_anni"],
                       json.dumps(((d1.get("will_call") or {}).get("arguments") or {}), ensure_ascii=False)[:120])
                report("daily_clear 预演含每账号判定 + 批次证据",
                       isinstance(pc.get("accounts"), list) and len(pc.get("accounts") or []) == len(ids)
                       and all("verdict" in a for a in pc.get("accounts") or [])
                       and isinstance((pc.get("batch") or {}).get("evidence"), list),
                       "would_refuse=%s" % d1.get("would_refuse"))
                report("daily_clear 预演未泄漏 token", (not tok) or (tok not in _text_of(prev)))
                conf = await sess.call_tool(DAILY_TOOL, {"account_ids": ids, "confirm": True})
                d2 = _payload_of(conf)
                report("confirm=True 但 %s=1 时拒绝发车" % NO_DISPATCH_ENV,
                       bool(conf.isError) and d2.get("dispatched") is False
                       and d2.get("refused") == R_DISABLED, "refused=%s" % d2.get("refused"))
                gap = await sess.call_tool(GAP_TOOL, {})
                d3 = _payload_of(gap)
                report("daily_gap 只读可用且永不写",
                       d3.get("tool") == GAP_TOOL and isinstance(d3.get("gaps"), list)
                       and isinstance(d3.get("dispatchable_account_ids"), list),
                       "gap=%s batch_running=%s" % (d3.get("gap_count"),
                                                    (d3.get("batch") or {}).get("running")))
                st = await sess.call_tool(RERUN_STATE_TOOL, {})
                report("%s 只读转发可用" % RERUN_STATE_TOOL,
                       (not st.isError) and isinstance(_payload_of(st), dict))
                rr = await sess.call_tool(RERUN_TOOL, {"accounts": ids, "dry_run": True})
                d4 = _payload_of(rr)
                report("mas_rerun 预演（dry_run=True）原样转发且绝不下发",
                       d4.get("dry_run") is True and (d4.get("ok") is True or "would_call" in d4),
                       "上游返回 keys=%s" % sorted(d4)[:8])
                rr2 = await sess.call_tool(RERUN_TOOL, {"accounts": ids, "dry_run": False})
                d5 = _payload_of(rr2)
                report("mas_rerun dry_run=False 被 %s 拦下（零上游）" % NO_DISPATCH_ENV,
                       bool(rr2.isError) and d5.get("refused") == R_DISABLED
                       and d5.get("upstream_calls") == 0, "refused=%s" % d5.get("refused"))
                rr3 = await sess.call_tool(RERUN_TOOL, {"accounts": ["a%d" % i for i in range(9)], "dry_run": True})
                d6 = _payload_of(rr3)
                report("mas_rerun 超 8 个账号被拒绝（零上游）",
                       bool(rr3.isError) and d6.get("refused") == R_TOO_MANY
                       and d6.get("upstream_calls") == 0, "refused=%s" % d6.get("refused"))
    except Exception as e:                                     # noqa: BLE001
        report("子进程 stdio 链路", False, _redact(_exc_text(e))[:400])
    errlog.flush()
    errlog.seek(0)
    log = errlog.read()
    errlog.close()
    rerun_lines = sum(1 for ln in log.splitlines()
                      if ln.strip() == "[upstream] tools/call name=%s" % RERUN_TOOL)
    report("mas_rerun 只有 dry_run=True 那次真的到了上游", rerun_lines == 1,
           "上游 %s 调用行=%d（dry_run=False 那次应在本地被拦）" % (RERUN_TOOL, rerun_lines))
    report("mas_rerun 审计日志行（工具名/账号数/dry_run/是否真下发）",
           "[audit] tool=%s accounts=" % RERUN_TOOL in log)
    if "[upstream] tools/call name=%s" % UPSTREAM_WRITE_TOOL in log:
        report("子进程日志里没有 smart_dispatch 调用（默认预演零写）", False, "日志出现写调用")
    else:
        report("子进程日志里没有 smart_dispatch 调用（默认预演零写）", True,
               "上游调用行=%d" % log.count("[upstream] tools/call name="))
    for line in log.splitlines():
        if line.startswith(("[gate]", "[daily_clear]", "[daily_gap]", "[list]", "[audit]")):
            print("   子进程日志:", line)

    print("[D] 上游不可达：白名单拒绝仍不依赖上游 + 写路径 fail-closed")
    dead_env = dict(os.environ)
    dead_env[URL_ENV] = "http://127.0.0.1:9/mcp/"
    dead_env[NO_AUTOSTART_ENV] = "1"
    dead_log = open(os.path.join(tempfile.gettempdir(), "maaorch_daily_proxy_child_dead.log"), "w+",
                    encoding="utf-8")
    try:
        params2 = StdioServerParameters(command=sys.executable, args=[os.path.abspath(__file__)],
                                        env=dead_env)
        async with stdio_client(params2, errlog=dead_log) as (r2, w2):
            async with _client_session(r2, w2) as s2:
                init2 = await s2.initialize()
                report("上游死时 initialize 仍成功（不拖垮握手）", bool(getattr(init2, "serverInfo", None)))
                tl2 = await s2.list_tools()
                report("上游死时工具面仍是白名单（静态兜底）",
                       sorted(t.name for t in tl2.tools) == sorted(EXPOSED_TOOLS))
                r1 = await s2.call_tool("account_stop", {"idx": 0})
                d1 = _payload_of(r1)
                report("上游死时白名单外工具照样拒绝（零上游依赖）",
                       bool(r1.isError) and d1.get("upstream_calls") == 0 and "白名单" in json.dumps(d1, ensure_ascii=False))
                r2 = await s2.call_tool(DAILY_TOOL, {"account_ids": ["x"], "confirm": True})
                d2 = _payload_of(r2)
                report("上游不可读时 confirm=True fail-closed",
                       bool(r2.isError) and d2.get("refused") == R_READONLY and d2.get("dispatched") is False,
                       "refused=%s" % d2.get("refused"))
                r3 = await s2.call_tool(DAILY_TOOL, {"account_ids": ["x"]})
                d3 = _payload_of(r3)
                report("上游不可读时预演仍结构化（不发车）",
                       d3.get("dispatched") is False and bool(d3.get("precheck", {}).get("errors")))
                r4 = await s2.call_tool(RERUN_TOOL, {"accounts": ["x"], "dry_run": True})
                d4 = _payload_of(r4)
                report("上游不可读时 mas_rerun 结构化报错（不挂死）",
                       bool(r4.isError) and d4.get("ok") is False and d4.get("upstream_calls") == 0,
                       str(d4.get("error"))[:80])
    except Exception as e:                                     # noqa: BLE001
        report("上游不可达降级链路", False, _redact(_exc_text(e))[:400])
    finally:
        dead_log.close()

    print("[summary] 失败项=%d %s" % (len(fails), fails or ""))
    if fails:
        print("RESULT: FAIL (assertion)")
        return 1
    print("RESULT: PASS（暴露 %d 个工具，上游 %s）" % (len(EXPOSED_TOOLS), upstream_url()))
    return 0


def _client_session(read, write):
    """延迟 import：只有 --check 需要 mcp 客户端（避免 stdio server 路径上的无谓依赖）。"""
    from mcp import ClientSession
    return ClientSession(read, write)


def _exc_text(e: BaseException, depth: int = 0) -> str:
    """把 ExceptionGroup/异常链摊平成一行（--check 直接指出根因，而不是只说 TaskGroup 挂了）。"""
    parts = ["%s: %s" % (type(e).__name__, str(e)[:200])]
    if depth < 3:
        for sub in getattr(e, "exceptions", None) or []:
            parts.append(_exc_text(sub, depth + 1))
        cause = getattr(e, "__cause__", None)
        if cause is not None and cause is not e:
            parts.append("<- " + _exc_text(cause, depth + 1))
    return " | ".join(parts)


def _payload_of(res) -> dict:
    for c in res.content or []:
        if getattr(c, "type", "") == "text":
            try:
                d = json.loads(c.text)
                if isinstance(d, dict):
                    return d
            except ValueError:
                continue
    return {}


def _text_of(res) -> str:
    return "".join(getattr(c, "text", "") or "" for c in (res.content or []))


def main() -> int:
    ap = argparse.ArgumentParser(description="MAAOrch 清日常 stdio MCP 代理（默认预演，只放行白名单工具）")
    ap.add_argument("--check", action="store_true", help="自检：白名单/门禁/脱敏/子进程链路/降级")
    args = ap.parse_args()
    if args.check:
        return asyncio.run(_check())
    asyncio.run(_serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
