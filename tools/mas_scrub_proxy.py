# -*- coding: utf-8 -*-
"""MAS 脱敏代理 —— stdio MCP server，转发 AUTO-MAS 官方 MCP（HTTP）并在返回前递归脱敏。

## 为什么存在
AUTO-MAS 自带 MCP（缺省 http://<mas-host>:36163/mcp，**无鉴权**，tools/list = 118 个工具）。
官方实现**原样透传账号凭据**：tools/call get_user_api_scripts_user_get_post 的返回体里
/data/<uid>/Info/Password 与 Info/SklandToken 与 REST 一致地原样返回。
Hermes 直连 = 一调 script/user 家族就把账号口令写进 agent 会话/日志/记忆（送进 LLM 上下文）。
本代理在**序列化之前**把命中敏感键名的值换成占位符，让 Hermes 恢复全量控制而响应体不泄凭据。

## 挡得住什么 / 挡不住什么（务必读）
挡得住：
- tools/call 响应体里的凭据值：递归覆盖 dict/list 任意深度、content[].text 内嵌的 JSON 字符串、
  structuredContent、以及 isError 分支；**键名与结构保留**（不破坏上层解析）。
挡不住（本代理不是安全边界）：
- **调用本身**这个事实：谁能经本代理调用，谁就拿到全量控制权（含 44 个写/危险工具，
  例如 close_api_core_close_post 关后端、脚本配置 CRUD、导入导出）。
- **请求方向**：调用参数原样转发，不脱敏（例如改口令类写操作你提交的新口令）。
- 上游与网络层可见的一切：MAS 自身日志、Tailscale 段内**别人直连 36163** 的流量。
- 非键值形式的凭据：纯文本里没有引号/键名的口令、base64 载荷、工具描述里的元数据。
- 上下文之外的一切：本代理不记录任何值，但调用者的 agent 会话仍会记住它自己看到的内容。
因此仍建议：Tailscale ACL/防火墙限定源 IP；尽快给 MAS 前置带 token 的反向代理。

## 配置（环境变量优先，本文件不复写 IP）
  MAAORCH_MAS_MCP_URL      上游 /mcp 地址；缺省 = services/mas_l3.py 的 API 常量 + /mcp
  MAAORCH_MAS_MCP_TIMEOUT  单次上游调用超时（秒，缺省 30）
  MAAORCH_MAS_SCRUB_KEYS   附加敏感键名（逗号分隔的**字面**键名，大小写不敏感）
  MAAORCH_MAS_READONLY     只读巡检硬闸：1/true/yes/on = 只读；0/false/no/off = 全量；
                           **未设置 = 全量（现行为，人工会话不受影响）**；
                           其它任何值 fail-closed 到只读。

## 只读硬闸（MAAORCH_MAS_READONLY=1）—— 无人值守巡检用
- tools/list：只返回 READONLY_ALLOWED 里的工具（**精确工具名**，见下），其余全部隐藏。
- tools/call：**再校验一次**；不在 allowlist（或命中危险动词）→ 直接返回 isError=True 的结构化
  拒绝，且**在触达上游之前**返回（拒绝 = 零上游调用），所以知道被隐藏的名字也无效。
- 危险动词按**整词**黑名单（start/stop/close/update/add/delete/import/export/click/sign/dispatch...），
  同时作为 allowlist 的导入期自检：命中即从 allowlist 丢弃并打警告（fail-closed）。
- 不影响 scrub 语义：允许的只读工具返回照样脱敏（get_user_* 的 Password/SklandToken 仍被替换）。
- **默认（不设该 env）= 与以前完全一致**：透明转发 118 个工具；原 auto-mas-scrubbed 人工会话不受影响。
- 只读 allowlist（10 个，全部对应 REST 只读端点）：
  get_health_api_core_health_get / get_overview_api_info_get_overview_post /
  get_git_version_api_info_version_post / get_queues_api_queue_get_post /
  get_item_api_queue_item_get_post / get_time_set_api_queue_time_get_post /
  get_script_api_scripts_get_post / get_user_api_scripts_user_get_post /
  search_history_api_history_search_post / get_history_data_api_history_data_post
## 用法
  python tools/mas_scrub_proxy.py             # stdio server（由 Hermes 用 command+args 拉起）
  python tools/mas_scrub_proxy.py --check     # 自检：本地脱敏 + 上游工具数 + 端到端差分断言
  python tools/mas_scrub_proxy.py --check --script-id <uuid>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from datetime import timedelta

import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError

PLACEHOLDER = "<scrubbed>"
DEFAULT_SENSITIVE = ("password", "sklandtoken", "token", "secret", "credential", "cookie")


def _sensitive_re() -> re.Pattern:
    """内置敏感键名 + MAAORCH_MAS_SCRUB_KEYS 扩展（大小写不敏感）。"""
    extra = [k.strip() for k in (os.environ.get("MAAORCH_MAS_SCRUB_KEYS") or "").split(",") if k.strip()]
    parts = [re.escape(k) for k in DEFAULT_SENSITIVE] + [re.escape(k) for k in extra]
    return re.compile("|".join(parts), re.I)


SENSITIVE = _sensitive_re()


# ── 只读硬闸（MAAORCH_MAS_READONLY）─────────────────────────────────
# 用途：无人值守巡检（另一个 MCP entry auto-mas-monitor）。**代码级**只读，不靠 prompt：
#   ① tools/list 只暴露 allowlist 内的工具；
#   ② tools/call **再校验一次** —— 即使调用者知道被隐藏的名字也拒绝，
#      且**在触达上游之前**就返回（拒绝 = 零上游调用）。
# 用**精确工具名** allowlist，不用 get_* 泛匹配（泛匹配会把 click_image/check_image 之类
# 动作名也放进来；实测 118 个工具里以 get_ 开头的有 39 个，其中就有 get_screenshot_*）。
# 取值（大小写不敏感，首尾空白忽略）：
#   未设置 / 空      -> False（保持现有「人工全量控制」行为，不受影响）
#   1 true yes on    -> True
#   0 false no off   -> False
#   其它任何值        -> True 并打警告（**fail-closed**：不认识就按只读处理）
READONLY_ENV = "MAAORCH_MAS_READONLY"
_TRUTHY = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSY = {"0", "false", "no", "off", "disable", "disabled"}

# 危险动词（按**整词**匹配，避免误伤 get_time_set_* 这种含 "set" 的只读名）
_DANGER_RE = re.compile(
    r"(?:^|_)(start|stop|close|update|add|delete|del|remove|import|export|create|kill|restart"
    r"|reset|clear|write|send|click|login|logout|download|install|upload|cancel|confirm|test"
    r"|reorder|manual|operate|switch|connect|disconnect|save|qr|sign|dispatch|power|plan)(?:_|$)",
    re.I)

# 只读 allowlist：精确工具名 <-> 对应 REST 端点（名字取自 2026-09-26 实测 tools/list 真值）
READONLY_ALLOWLIST: tuple = (
    "get_health_api_core_health_get",                       # /api/core/health
    "get_overview_api_info_get_overview_post",              # /api/info/get_overview
    "get_git_version_api_info_version_post",                # /api/info/version（存活探针）
    "get_queues_api_queue_get_post",                        # /api/queue/get
    "get_item_api_queue_item_get_post",                     # /api/queue/item/get
    "get_time_set_api_queue_time_get_post",                 # /api/queue/time/get
    "get_script_api_scripts_get_post",                      # /api/scripts/get
    "get_user_api_scripts_user_get_post",                   # /api/scripts/user/get（凭据仍经 scrub）
    "search_history_api_history_search_post",               # /api/history/search
    "get_history_data_api_history_data_post",               # /api/history/data
)


def _sanitize_allowlist(names) -> tuple:
    """剔除命中危险动词的名字（fail-closed：宁可少放，也不放错）。"""
    keep = []
    for n in names:
        if _DANGER_RE.search(n):
            print("[readonly] allowlist 中的危险名已丢弃: %s" % n, file=sys.stderr, flush=True)
            continue
        keep.append(n)
    return tuple(keep)


READONLY_ALLOWED = _sanitize_allowlist(READONLY_ALLOWLIST)


def readonly_enabled() -> bool:
    """只读模式开关（每次调用都读 env；非法值 fail-closed 到 True）。"""
    raw = os.environ.get(READONLY_ENV)
    if raw is None or not str(raw).strip():
        return False                    # 未设置 = 现行为（手动全量控制）
    v = str(raw).strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    print("[readonly] %s=%r 不是已知取值 -> fail-closed 按只读处理" % (READONLY_ENV, raw),
          file=sys.stderr, flush=True)
    return True


def readonly_deny(name: str) -> str:
    """只读模式下拒绝原因；允许则返回空串。"""
    if name in READONLY_ALLOWED:
        if _DANGER_RE.search(name):     # 双保险：allowlist 被改坏也不放行
            return "命中危险动词"
        return ""
    if _DANGER_RE.search(name):
        return "命中危险动词（写/动作类）"
    return "不在只读 allowlist 内"


def _readonly_deny_result(name: str, reason: str) -> types.CallToolResult:
    payload = {"ok": False, "error": "readonly mode: tool not allowed", "reason": reason,
               "tool": name, "mode": "readonly", "env": READONLY_ENV,
               "hint": "只读巡检模式：只允许 allowlist 内的只读工具；本次调用未触达上游"}
    print("[readonly] 拒绝工具 tool=%s reason=%s" % (name, reason), file=sys.stderr, flush=True)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        isError=True)


def _kv_re() -> re.Pattern:      # 非 JSON 文本的兜底：只改「"敏感键名": 值」这种 JSON 片段形态
    keys = "|".join(re.escape(k) for k in DEFAULT_SENSITIVE)
    extra = "|".join(re.escape(k.strip()) for k in (os.environ.get("MAAORCH_MAS_SCRUB_KEYS") or "").split(",") if k.strip())
    if extra:
        keys = keys + "|" + extra
    return re.compile(r'("(?:' + keys + r')"\s*:\s*)("(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|true|false|null)', re.I)


def upstream_url() -> str:
    """上游 /mcp 地址：MAAORCH_MAS_MCP_URL > MAS_MCP_URL > 仓内常量 + /mcp。"""
    for key in ("MAAORCH_MAS_MCP_URL", "MAS_MCP_URL"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    try:
        proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if proj not in sys.path:
            sys.path.insert(0, proj)
        from services import mas_l3
        return str(mas_l3.API).rstrip("/") + "/mcp"
    except Exception:                                     # noqa: BLE001
        return "http://127.0.0.1:36163/mcp"


def rest_base() -> str:
    u = upstream_url()
    return u[:-4].rstrip("/") if u.endswith("/mcp") else u.rstrip("/")


def timeout_s() -> float:
    try:
        return max(1.0, float(os.environ.get("MAAORCH_MAS_MCP_TIMEOUT") or 30))
    except Exception:                                     # noqa: BLE001
        return 30.0


# ── 脱敏：递归 + 内嵌 JSON + 结构化 + isError ─────────────────────────

def _hit(hits: list, path: str, key: str, value) -> None:
    """记录一条命中：只留路径/键名/是否非空，**不留值**。"""
    if hits is None:
        return
    non_empty = bool(value) if not isinstance(value, str) else bool(value.strip())
    hits.append({"path": path, "key": key, "non_empty": non_empty})


def scrub(obj, hits: list | None = None, path: str = ""):
    """递归脱敏：键名命中敏感模式 -> 值整体替换为占位符（键名与容器结构保留）。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            p = "%s/%s" % (path, k)
            if SENSITIVE.search(str(k)):
                _hit(hits, p, str(k), v)
                out[k] = PLACEHOLDER
            else:
                out[k] = scrub(v, hits, p)
        return out
    if isinstance(obj, list):
        return [scrub(x, hits, "%s[%d]" % (path, i)) for i, x in enumerate(obj)]
    return obj


def scrub_text(text, hits: list | None = None, path: str = "") -> str:
    """content[].text：是 JSON 对象/数组 -> 解析脱敏后回填；否则按 JSON 片段正则兜底。"""
    if not isinstance(text, str) or not text.strip():
        return text
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, (dict, list)):
        return json.dumps(scrub(parsed, hits, path), ensure_ascii=False)
    kv = _kv_re()

    def _sub(m):
        if hits is not None:
            km = re.search(r'"([^"]+)"', m.group(1))
            hits.append({"path": path + "/<json-fragment>",
                         "key": km.group(1) if km else m.group(1),
                         "non_empty": m.group(2) not in ('""', "null", "false")})
        return m.group(1) + '"' + PLACEHOLDER + '"'

    return kv.sub(_sub, text)


def scrub_result(res: types.CallToolResult, hits: list | None = None) -> types.CallToolResult:
    """对整个 CallToolResult 脱敏（序列化之前）。isError 分支同样覆盖。"""
    content = []
    for i, c in enumerate(res.content or []):
        p = "/content[%d]" % i
        if getattr(c, "type", "") == "text":
            content.append(c.model_copy(update={"text": scrub_text(getattr(c, "text", ""), hits, p + "/text")}))
        else:
            # 非文本 content（image/audio/resource/...）：走 model_dump -> 脱敏 -> 还原
            content.append(type(c).model_validate(scrub(c.model_dump(), hits, p)))
    sc = getattr(res, "structuredContent", None)
    return types.CallToolResult(
        content=content,
        structuredContent=(scrub(sc, hits, "/structuredContent") if sc is not None else None),
        isError=bool(res.isError),
        meta=res.meta,
    )


# ── 上游：每次请求独立握手（无鉴权 + 无会话缓存，最不易卡死）──────────
async def _upstream_list_tools() -> list:
    to = timeout_s()
    async with streamablehttp_client(upstream_url(), timeout=to) as (r, w, _):
        async with ClientSession(r, w, read_timeout_seconds=timedelta(seconds=to)) as s:
            await s.initialize()
            tl = await s.list_tools()
            return list(tl.tools)


async def _upstream_call_tool(name: str, arguments: dict | None) -> types.CallToolResult:
    to = timeout_s()
    async with streamablehttp_client(upstream_url(), timeout=to) as (r, w, _):
        async with ClientSession(r, w, read_timeout_seconds=timedelta(seconds=to)) as s:
            await s.initialize()
            # 关于空 arguments：曾试过把 {} 原样下发（而非 or None），实测对本用例无效 ——
            # MAS 自己的 MCP 服务把空 arguments 当作「无 body」，get_queues 仍 422；
            # 传任何非 UUID 值又会 500 badly formed hexadecimal UUID string。
            # 这是 MAS 侧 schema 限制（REST 可传 queueId=null，MCP 生成的 schema 不接受 null），
            # 不在本代理职责内，故保持原样透传。详见 docs/HERMES_MAS_HANDOVER.md §10。
            return await s.call_tool(name, arguments or None)


# ── MCP server（stdio 侧）───────────────────────────────────────────
server = Server(
    "mas-scrub-proxy",
    version="1.0.0",
    instructions="AUTO-MAS 的脱敏代理：工具面与上游完全一致，响应体里敏感键名的值已替换为 "
                 + PLACEHOLDER + "。",
)


@server.list_tools()
async def _handle_list_tools() -> list:
    try:
        tools = await _upstream_list_tools()
    except Exception as e:                                # noqa: BLE001
        msg = "MAS MCP 上游不可达（%s）: %s: %s" % (upstream_url(), type(e).__name__, e)
        print("[proxy] " + msg, file=sys.stderr, flush=True)
        raise McpError(types.ErrorData(code=types.INTERNAL_ERROR, message=msg)) from e
    if readonly_enabled():
        allowed = [t for t in tools if t.name in READONLY_ALLOWED]
        print("[readonly] tools/list -> %d/%d（只读模式过滤掉 %d 个非只读工具）" % (
            len(allowed), len(tools), len(tools) - len(allowed)), file=sys.stderr, flush=True)
        return allowed
    print("[proxy] tools/list -> %d 个工具（上游 %s）" % (len(tools), upstream_url()),
          file=sys.stderr, flush=True)
    return tools


@server.call_tool(validate_input=False)     # 透明代理：参数合法性交给上游判定
async def _handle_call_tool(name: str, arguments: dict | None) -> types.CallToolResult:
    if readonly_enabled():
        _reason = readonly_deny(name)
        if _reason:
            # 关键：直接返回，**不触达上游**（隐藏名字也挡得住）
            return _readonly_deny_result(name, _reason)
    try:
        res = await _upstream_call_tool(name, arguments)
    except Exception as e:                                # noqa: BLE001
        payload = {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                   "upstream": upstream_url(), "tool": name}
        print("[proxy] 上游调用失败 tool=%s %s" % (name, payload["error"]), file=sys.stderr, flush=True)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            isError=True)
    hits: list = []
    out = scrub_result(res, hits)
    if hits:
        # 只记字段名与路径，绝不记值
        print("[scrub] tool=%s redacted=%d keys=%s" % (
            name, len(hits), sorted({h["key"] for h in hits})), file=sys.stderr, flush=True)
    return out


async def _serve() -> None:
    _ro = readonly_enabled()
    if _ro:
        server.instructions = (str(server.instructions or "") +
                               " **本进程处于只读巡检模式（%s）：tools/list 只暴露只读工具，"
                               "tools/call 对非 allowlist 工具直接拒绝且不触达上游。**" % READONLY_ENV)
    print("[proxy] 启动：只读模式=%s allowlist=%d 个 上游=%s" % (
        _ro, len(READONLY_ALLOWED), upstream_url()), file=sys.stderr, flush=True)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


# ── 自检 ────────────────────────────────────────────────────────────

def _walk_sensitive(obj, path="", out=None):
    """收集敏感键名的路径 + 值是否为占位符（**不返回真值**）。"""
    out = out if out is not None else []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s/%s" % (path, k)
            if SENSITIVE.search(str(k)):
                out.append({"path": p, "key": str(k),
                            "is_placeholder": v == PLACEHOLDER,
                            "non_empty": bool(v) if not isinstance(v, str) else bool(v.strip())})
            else:
                _walk_sensitive(v, p, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _walk_sensitive(v, "%s[%d]" % (path, i), out)
    return out


def _result_json(res: types.CallToolResult):
    """取返回体的第一个可解析 JSON text（脱敏后应仍是合法 JSON）。"""
    for c in res.content or []:
        if getattr(c, "type", "") == "text":
            try:
                return json.loads(c.text)
            except ValueError:
                continue
    return None


def _synthetic_case():
    """桩：递归 + 内嵌 JSON + 非 JSON 正则 + structuredContent + isError。"""
    secrets = {"pw": "SYNTH-PW-0001", "tk": "SYNTH-TK-0002", "ck": "SYNTH-CK-0003",
               "secret2": "SYNTH-SECRET-0004"}
    inner = {"data": {"u1": {"Info": {"Name": "acct", "Password": secrets["pw"],
                                      "SklandToken": secrets["tk"],
                                      "Nested": {"cookie": secrets["ck"], "deep": [{"Token": secrets["secret2"]}]}}}},
             "index": [{"uid": "x"}]}
    res = types.CallToolResult(
        content=[
            types.TextContent(type="text", text=json.dumps(inner, ensure_ascii=False)),
            types.TextContent(type="text", text='prefix {"secret": "' + secrets["secret2"] + '"} suffix'),
            types.TextContent(type="text", text="plain text without any keys"),
        ],
        structuredContent={"a": {"credential": secrets["ck"]}},
        isError=True,
    )
    hits: list = []
    out = scrub_result(res, hits)
    blob = json.dumps([c.model_dump() for c in out.content], ensure_ascii=False) + \
        json.dumps(out.structuredContent, ensure_ascii=False)
    leaked = [k for k, v in secrets.items() if v in blob]
    parsed0 = json.loads(out.content[0].text)
    checks = {
        "no_secret_leaked": not leaked,
        "embedded_json_scrubbed": parsed0["data"]["u1"]["Info"]["Password"] == PLACEHOLDER
                                  and parsed0["data"]["u1"]["Info"]["SklandToken"] == PLACEHOLDER,
        "deep_nested_scrubbed": parsed0["data"]["u1"]["Info"]["Nested"]["cookie"] == PLACEHOLDER
                                and parsed0["data"]["u1"]["Info"]["Nested"]["deep"][0]["Token"] == PLACEHOLDER,
        "structure_preserved": sorted(parsed0["data"]["u1"]["Info"].keys()) ==
                               ["Name", "Nested", "Password", "SklandToken"],
        "regex_fragment_scrubbed": PLACEHOLDER in out.content[1].text
                                   and '"secret"' in out.content[1].text,
        "plain_text_untouched": out.content[2].text == "plain text without any keys",
        "structured_content_scrubbed": out.structuredContent["a"]["credential"] == PLACEHOLDER,
        "iserror_preserved": out.isError is True,
        "hits_recorded": len(hits) >= 5,
    }
    print("-- 桩用例（合成值，仅打印字段名/布尔）--")
    for h in sorted({h["key"] for h in hits}):
        print("   hit key=%s redacted=True" % h)
    return checks


async def _offline_error_case() -> dict:
    """桩：上游抛错时 call_tool 处理器自己返回结构化错误（不抛异常、不卡 transport）。"""
    async def run():
        orig = globals()["_upstream_call_tool"]

        async def boom(name, arguments):
            raise ConnectionError("simulated: all connection attempts failed")

        globals()["_upstream_call_tool"] = boom
        try:
            res = await _handle_call_tool("get_script_api_scripts_get_post", {"scriptId": "x"})
        finally:
            globals()["_upstream_call_tool"] = orig
        d = json.loads((res.content or [types.TextContent(type="text", text="{}")])[0].text)
        return {"offline_error_is_structured": bool(res.isError) and d.get("ok") is False
                                               and "simulated" in str(d.get("error"))
                                               and d.get("tool") == "get_script_api_scripts_get_post",
                "offline_error_names_upstream": str(d.get("upstream", "")).endswith("/mcp")}
    return await run()


async def _check(script_id: str = "") -> int:
    print("== MAS 脱敏代理 --check ==")
    print("upstream =", upstream_url(), "| timeout =", timeout_s(), "s")
    print("readonly =", readonly_enabled(), "| allowlist =", len(READONLY_ALLOWED), "个")
    fails = []

    def report(name, ok, extra=""):
        print("   %s %s%s" % ("PASS" if ok else "FAIL", name, (" | " + extra) if extra else ""))
        if not ok:
            fails.append(name)

    print("[A] 本地脱敏桩（不连网）")
    for k, v in _synthetic_case().items():
        report(k, v)
    for k, v in (await _offline_error_case()).items():
        report(k, v)

    print("[B] stdio 握手 + tools/list（经代理子进程）")
    from mcp.client.stdio import StdioServerParameters, stdio_client
    env = dict(os.environ)
    env["MAAORCH_MAS_MCP_URL"] = upstream_url()
    errlog = open(os.path.join(tempfile.gettempdir(), "mas_scrub_proxy_child.log"), "w+", encoding="utf-8")
    params = StdioServerParameters(command=sys.executable, args=[os.path.abspath(__file__)], env=env)
    tools_n = 0
    try:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                report("initialize 成功", bool(getattr(init, "serverInfo", None)),
                       "serverInfo=%s" % getattr(getattr(init, "serverInfo", None), "name", None))
                tl = await session.list_tools()
                tools_n = len(tl.tools)
                report("tools/list 有工具", tools_n > 0, "工具数=%d" % tools_n)
                names = {t.name for t in tl.tools}
                report("目标工具在清单里", "get_user_api_scripts_user_get_post" in names)

                sid = script_id.strip()
                if not sid:
                    try:
                        import urllib.request
                        req = urllib.request.Request(
                            rest_base() + "/api/scripts/get",
                            data=json.dumps({"scriptId": None}).encode(),
                            headers={"Content-Type": "application/json"}, method="POST")
                        with urllib.request.urlopen(req, timeout=timeout_s()) as resp:
                            d = scrub(json.loads(resp.read().decode("utf-8", "replace")))
                        sid = ((d.get("index") or [{}])[0] or {}).get("uid", "")
                        print("   scriptId 由 REST /api/scripts/get 发现（只取 uid，未打印其它内容）")
                    except Exception as e:                # noqa: BLE001
                        print("   scriptId 发现失败:", type(e).__name__, e)
                if not sid:
                    report("scriptId 可得", False, "用 --script-id 显式指定")
                    print("[C] 端到端脱敏断言: SKIP（无 scriptId）")
                else:
                    print("[C] 端到端差分断言（同一 scriptId：上游直连 vs 经代理）")
                    report("scriptId 可得", True, "长度=%d" % len(sid))
                    raw = await _upstream_call_tool("get_user_api_scripts_user_get_post", {"scriptId": sid})
                    raw_hits = _walk_sensitive(_result_json(raw) or {})
                    n_raw = len(raw_hits)
                    n_raw_nonempty = sum(1 for h in raw_hits if h["non_empty"])
                    report("上游直连确实带敏感字段（检查非空转）", n_raw > 0,
                           "命中=%d（非空=%d）" % (n_raw, n_raw_nonempty))
                    raw_json = _result_json(raw) or {}
                    prox = await session.call_tool("get_user_api_scripts_user_get_post", {"scriptId": sid})
                    prox_json = _result_json(prox)
                    report("代理返回仍是合法 JSON", prox_json is not None)
                    prox_hits = _walk_sensitive(prox_json or {})
                    n_prox = len(prox_hits)
                    n_ph = sum(1 for h in prox_hits if h["is_placeholder"])
                    report("代理侧敏感字段全部为占位符", n_prox > 0 and n_ph == n_prox,
                           "命中=%d 已脱敏=%d" % (n_prox, n_ph))
                    print("   -- 敏感字段（只打印路径与是否已脱敏）--")
                    for h in prox_hits:
                        print("      %s  key=%s  is_placeholder=%s"
                              % (h["path"], h["key"], h["is_placeholder"]))
                    rawsets = {h["path"].replace("/<json", "") for h in raw_hits}
                    proxsets = {h["path"] for h in prox_hits}
                    report("敏感字段集合一致（键名/结构未丢）", rawsets == proxsets,
                           "raw=%d prox=%d" % (len(rawsets), len(proxsets)))
                    rj = raw_json.get("data") or {}
                    pj = (prox_json or {}).get("data") or {}
                    rinfo = sorted(((list(rj.values()) or [{}])[0].get("Info") or {}).keys())
                    pinfo = sorted(((list(pj.values()) or [{}])[0].get("Info") or {}).keys())
                    report("非敏感字段未丢（Info 键集合一致）", rinfo == pinfo and len(rinfo) > 0,
                           "keys=%d" % len(pinfo))
                    report("isError 未被代理改写", prox.isError == raw.isError,
                           "raw=%s prox=%s" % (raw.isError, prox.isError))
    except Exception as e:                                # noqa: BLE001
        report("上游可达（stdio 代理链路）", False, "%s: %s" % (type(e).__name__, e))
    errlog.flush()
    errlog.seek(0)
    log = errlog.read()
    errlog.close()
    if log:
        for line in log.splitlines():
            if line.startswith("[scrub]") or line.startswith("[proxy]"):
                print("   子进程日志:", line)

    print("[D] 上游不可达降级（URL 指向死端口：断言握手不垮、错误结构化、transport 不死）")
    from mcp.client.stdio import StdioServerParameters, stdio_client
    dead = "http://127.0.0.1:9/mcp"
    env2 = dict(os.environ)
    env2["MAAORCH_MAS_MCP_URL"] = dead
    errlog2 = open(os.path.join(tempfile.gettempdir(), "mas_scrub_proxy_child_dead.log"), "w+", encoding="utf-8")
    try:
        params2 = StdioServerParameters(command=sys.executable, args=[os.path.abspath(__file__)], env=env2)
        async with stdio_client(params2, errlog=errlog2) as (read2, write2):
            async with ClientSession(read2, write2) as s2:
                init2 = await s2.initialize()
                report("上游死时 initialize 仍成功（不拖垮握手）", bool(getattr(init2, "serverInfo", None)))
                try:
                    await s2.list_tools()
                    report("上游死时 tools/list 显式报错（不静默返回空表）", False, "竟然成功")
                except Exception as e:                    # noqa: BLE001
                    report("上游死时 tools/list 显式报错（不静默返回空表）", True, type(e).__name__)
                r1 = await s2.call_tool("get_user_api_scripts_user_get_post", {"scriptId": "x"})
                txt = ((r1.content or [types.TextContent(type="text", text="")])[0].text or "")
                # 两条路径都算「结构化错误」：① 本代理处理器自己的 JSON 载荷；
                # ② SDK 在 tools/call 前预热工具缓存（_get_cached_tool_definition -> tools/list）
                #    失败时给的 isError 结果（mcp/server/lowlevel/server.py:527-541、473-480）。
                structured = False
                try:
                    d1 = json.loads(txt)
                    structured = d1.get("ok") is False and bool(d1.get("error"))
                except Exception:                         # noqa: BLE001
                    structured = (dead in txt or "不可达" in txt or "unreachable" in txt.lower())
                report("上游死时 tools/call 返回结构化错误（isError=True）",
                       bool(r1.isError) and bool(txt) and structured, txt[:70])
                r2 = await s2.call_tool("get_user_api_scripts_user_get_post", {"scriptId": "x"})
                report("失败后 transport 仍可用（连打两次不卡死）", bool(r2.isError))
    except Exception as e:                                # noqa: BLE001
        report("上游不可达降级链路", False, "%s: %s" % (type(e).__name__, e))
    finally:
        errlog2.close()

    print("[summary] 失败项=%d %s" % (len(fails), fails or ""))
    if fails:
        print("RESULT: FAIL (assertion)")
        return 1
    print("RESULT: PASS (tools=%d, 上游 %s)" % (tools_n, upstream_url()))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="AUTO-MAS 脱敏 MCP 代理（stdio）")
    ap.add_argument("--check", action="store_true", help="自检：本地脱敏 + 上游工具数 + 端到端差分断言")
    ap.add_argument("--script-id", default="", help="--check 用的 scriptId（缺省自动发现）")
    args = ap.parse_args()
    if args.check:
        return asyncio.run(_check(args.script_id))
    asyncio.run(_serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
