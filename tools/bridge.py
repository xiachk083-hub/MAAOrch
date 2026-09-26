"""ZCode Stop Hook → bridge：任务结束后自动收集状态 + 调 LLM + 写建议。

链路（阶段一：只出建议，不自动执行任何东西）::

    ZCode Stop hook（stdin 收事件 JSON）
      → 采集本地状态（运行记录 / 日志尾 / trace 尾 / 账号状态）
      → 调 LLM（system prompt 来自 strategy/bridge_prompt.md）
      → 写 data/bridge_last.json（下一轮读）+ data/bridge_notify.log（留痕）
      → stdout 回 hook 输出（ZCode 解析并注入下一轮上下文）

为什么自包含、不复用 services/ai_agent/llm.py（2026-09-16 实测）:
    llm.py 依赖 percept.log，而 percept.log **既落盘又 print 到 stdout**。hook 的
    stdout 是要交给 ZCode 解析的（严格 schema，多一个键整条作废），且 llm.py 在
    「content 为空（reasoning 吃光 max_tokens）」时会打印重试日志 —— 那条日志会
    直接污染 hook 输出，属于偶发但必然复现的坑。本文件把它那两处被验证过的行为
    学过来（空内容加倍预算重试、剥围栏取平衡 {} 的 JSON 提取），但自己发 HTTP，
    保证 stdout 只有一个出口。

硬约束（对应需求文档，逐条落地）:
    1. 无凭据不外发 —— 环境变量与 models/config.json 都没有 key 时，只写
       bridge_notify.log，绝不发请求。
    2. 不污染生产数据 —— 只读运行记录/日志/trace；只写 bridge_last.json 与
       bridge_notify.log（两者都在已 gitignore 的 data/ 下）。不动任何作业 JSON、
       不动 strategy、不动 accounts_state。
    3. 幂等 —— 以 sessionId+timestamp 为去重键，同键重复触发不重复写、不重复调 API。
    4. 超时保护 —— 单次 API 调用 60s 上限，超时只记日志，不抛异常。
    5. 原子写 —— .tmp + os.replace。
    6. stdout 保持干净 —— 日志一律走 stderr / notify 文件；默认**什么都不输出**。

为什么默认静默（2026-09-16 扫 app.asar 实证，推翻了需求文档的假设）::

    需求设想「stdout 回 {"advice": {...}} 让 ZCode 捕获并注入」。把 ZCode 的 Electron 包
    （app.asar, 307MB）逐串扫过：hookSpecificOutput / additionalContext / suppressOutput
    **出现次数均为 0**，而同族的 hookEventName=10、hookEvent=28、statusMessage=115、
    以及完整的 hookConfig schema 都在。也就是说这个构建的配置 hook 只实现了
    「执行命令 + 记录 outcome（success/blocked/failed/cancelled/timed_out）」的契约，
    **没有把 stdout 注入下一轮上下文的输出协议**。
    既然如此，我原先照 Claude Code 惯例发的 {"hookSpecificOutput": ...} 只是个无意义的
    多余键 —— 而多余键会让整条 hook 被记成 failed（本机已有 1.7 万条同类失败可佐证代价）。
    所以默认静默；建议的正式通道是 data/bridge_last.json（需求文档里「供下一轮读」那一路）。

    同批查到的两条硬事实，一并留档：
      · hook 配置 schema 是 .strict() 的，根只有 enabled/timeoutMs/maxOutputBytes/events，
        events 只认那 7 个事件名，组对象只认 matcher/hooks —— 本文件写出的形状与之逐字段吻合。
      · `timeout`（秒）只对 type=command 生效；process 只认 `timeoutMs`（毫秒）——
        本文件用 timeoutMs 是对的。
      · 官方 UI 文案 settings.hooks.sessionSnapshot =「Hook 配置变更将在新会话中生效」
        印证了「改完 hook 要开新会话」；另有 settings.hooks.review.* 信任审核流程，
        首次启用可能需要在 GUI 里点「信任」。

用法::

    echo '{"event":"Stop","sessionId":"s1","timestamp":"t1","responseText":"..."}' \
        | python tools/bridge.py            # 默认：静默，只写文件（hook 就用这个形态）
    python tools/bridge.py --advice-json    # 额外把 {"advice": {...}} 写到 stdout（手工排查/管道）

退出码恒为 0 —— hook 非 0 会被 ZCode 记成错误，本脚本的任何失败都不该影响会话。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
import re
import socket
import sys
import time
import urllib.error
import urllib.request

PROJ = pathlib.Path(__file__).resolve().parent.parent
DATA = PROJ / "data"
AGENT_RUN = DATA / "agent_run"
PROMPT_FILE = PROJ / "strategy" / "bridge_prompt.md"
CONFIG_FILE = PROJ / "models" / "config.json"
LAST_FILE = DATA / "bridge_last.json"
NOTIFY_FILE = DATA / "bridge_notify.log"

API_TIMEOUT = 60.0          # 需求硬约束 4：单次调用 60s
TOTAL_BUDGET = 110.0        # 含重试的墙钟上限，留出写盘余量（hook timeoutMs 150s）
TAIL_LOG = 2000             # 日志尾字符数（需求指定）
TAIL_TRACE = 1000           # trace 尾字符数（需求指定）
MAX_RESPONSE = 4000         # zcode 回复截断，防 token 失控
TAIL_JSONL = 20000          # 取 jsonl 最后一条时的读入量

DEFAULT_ENDPOINT = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"

DEFAULT_SYSTEM = (
    "你是方舟调试助手。看下面的日志状态，判断下一步该做什么。\n"
    "只输出 JSON：{\"diagnosis\": \"当前卡在哪\", \"next_action\": \"具体下一步命令或改动\","
    " \"reason\": \"为什么\", \"confidence\": 0.0~1.0}\n"
    "判据：缺干员→替换 X 为 Y；漏路→加挡位/调顺序；成功（三星）→记录并下一个账号；"
    "卡住→查 SubTaskError，逐层定位。"
)


# ── 基础 IO ──────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_text(path) -> str:
    try:
        p = pathlib.Path(path)
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    except Exception:
        return ""


def _read_tail(path, limit: int) -> str:
    """读文件末尾 limit 个字符；中文按 3 字节估所以按 4 倍字节预读，不存在则空串。"""
    try:
        p = pathlib.Path(path)
        if not p.exists() or not p.is_file():
            return ""
        size = p.stat().st_size
        with open(p, "rb") as f:
            if size > limit * 4:
                f.seek(size - limit * 4)
            data = f.read()
        return data.decode("utf-8", "replace")[-limit:]
    except Exception:
        return ""


def _read_json(path):
    try:
        p = pathlib.Path(path)
        if not p.exists():
            return None
        obj = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _last_jsonl(path):
    """最后一条可解析的 JSON 对象；尾行被截断/写坏就往前找。"""
    for raw in reversed(_read_tail(path, TAIL_JSONL).splitlines()):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _newest_log(folder):
    try:
        logs = [p for p in pathlib.Path(folder).glob("*.log") if p.is_file()]
        return max(logs, key=lambda p: p.stat().st_mtime) if logs else None
    except Exception:
        return None


def _write_atomic(path, obj) -> None:
    """硬约束 5：先写 .tmp 再 os.replace —— 写到一半崩不会留下半截文件。"""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _notify(msg: str, to_file: bool = True) -> None:
    """留痕：stderr + bridge_notify.log（绝不写 stdout）。"""
    line = f"[{_now()}] {msg}"
    print(line, file=sys.stderr, flush=True)
    if not to_file:
        return
    try:
        NOTIFY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(NOTIFY_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── 状态采集（只读）─────────────────────────────────────────────────────

def _summarize_accounts(obj):
    """账号状态压成「阶段分布 + 问题账号」——原始文件可能有几十个账号，喂不动。"""
    if not isinstance(obj, dict) or not obj:
        return None
    dist: dict = {}
    problems = []
    for acc, val in obj.items():
        stage = val.get("阶段", "未开始") if isinstance(val, dict) else "未开始"
        dist[stage] = dist.get(stage, 0) + 1
        if stage in ("失败", "异常") and len(problems) < 10:
            problems.append({
                "账号": acc,
                "阶段": stage,
                "结果": val.get("结果") if isinstance(val, dict) else None,
                "attempts": val.get("attempts") if isinstance(val, dict) else None,
            })
    return {"总数": len(obj), "阶段分布": dist, "问题账号": problems}


def collect_state() -> dict:
    """采集本地状态。全程只读，任何一项缺失都降级成空值而不是抛异常。"""
    st: dict = {}

    for cand in (DATA / "runs.jsonl", AGENT_RUN / "iterations.jsonl"):
        run = _last_jsonl(cand)
        if run is not None:
            st["last_run"] = run
            st["last_run_from"] = str(cand.relative_to(PROJ))
            break
    else:
        st["last_run"] = None
        st["last_run_from"] = None

    log_path = AGENT_RUN / "_launch.log"          # 需求指定的路径
    if not log_path.exists():                      # 不存在则退到最近写过的日志
        log_path = _newest_log(AGENT_RUN)
    st["log_tail"] = _read_tail(log_path, TAIL_LOG) if log_path else ""
    st["log_from"] = str(log_path.relative_to(PROJ)) if log_path else None

    st["trace_tail"] = _read_tail(AGENT_RUN / "trace.jsonl", TAIL_TRACE)
    st["agents_health"] = _read_json(AGENT_RUN / "agents_health.json")
    st["state"] = _summarize_accounts(_read_json(DATA / "accounts_state.json"))
    return st


# ── 事件归一化与去重 ────────────────────────────────────────────────────

def _normalize_event(raw: dict) -> dict:
    """ZCode 实际下发的是 snake_case（session_id 等），文档样例是 camelCase —— 两种都收。"""
    def pick(*names) -> str:
        for n in names:
            v = raw.get(n)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    return {
        "event": pick("event", "hook_event_name") or "Stop",
        "sessionId": pick("sessionId", "session_id"),
        "timestamp": pick("timestamp", "ts"),
        "responseText": pick("responseText", "response", "assistant_response",
                             "last_assistant_message"),
        "cwd": pick("cwd", "project_dir"),
    }


def _dedup_key(ev: dict) -> str:
    """硬约束 3 的去重键。没有 timestamp 时用回复内容哈希兜底，保证同样输入不重复处理。"""
    ts = ev.get("timestamp")
    if not ts:
        digest = hashlib.sha1((ev.get("responseText") or "").encode("utf-8")).hexdigest()
        ts = "sha1:" + digest[:12]
    return f"{ev.get('sessionId') or 'nosession'}|{ts}"


# ── 凭据与通道 ──────────────────────────────────────────────────────────

def _resolve_channel() -> dict:
    """环境变量优先，其次 models/config.json。

    需求硬约束 1 的口径是「环境变量不存在就不外发」；这里额外接受 config 里的
    ai_api_key —— 本项目现役凭据就在那儿（实测两个环境变量均未设置），只认环境
    变量会让本脚本永远不发请求。两者都没有时才算无凭据。
    """
    cfg = _read_json(CONFIG_FILE) or {}
    env_ds = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    env_an = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    cfg_key = str(cfg.get("ai_api_key") or "").strip()
    protocol = str(cfg.get("ai_protocol") or "openai").strip().lower()
    endpoint = str(cfg.get("ai_endpoint") or "").strip()
    model = str(cfg.get("ai_model") or "").strip()

    if env_ds:
        key, source = env_ds, "env:DEEPSEEK_API_KEY"
    elif env_an:
        key, source = env_an, "env:ANTHROPIC_API_KEY"
        protocol = "anthropic"          # 拿 anthropic key 就按 anthropic 线格式发
    elif cfg_key:
        key, source = cfg_key, "config:ai_api_key"
    else:
        return {"ok": False, "reason": "无凭据：环境变量 DEEPSEEK_API_KEY / "
                                       "ANTHROPIC_API_KEY 与 models/config.json 的 "
                                       "ai_api_key 都未配置"}

    if protocol == "anthropic":
        if not model:
            return {"ok": False, "reason": "协议为 anthropic 但 models/config.json 未配 ai_model"}
        if "deepseek" in endpoint:
            return {"ok": False, "reason": f"协议 anthropic 但 ai_endpoint 仍指向 deepseek"
                                           f"（{endpoint}）—— 先改 ai_endpoint"}
    else:
        endpoint = endpoint or DEFAULT_ENDPOINT
        model = model or DEFAULT_MODEL

    return {"ok": True, "key": key, "endpoint": endpoint, "model": model,
            "protocol": protocol, "source": source}


def _pick_text(data: dict, protocol: str):
    """从两种协议的响应里取正文；空正文不算成功（reasoning 型模型会吃光预算）。"""
    try:
        if protocol == "anthropic":
            blocks = data.get("content") or []
            text = "".join(b.get("text", "") for b in blocks
                           if isinstance(b, dict) and b.get("type") == "text")
            return text.strip(), ""
        msg = data["choices"][0]["message"]
        return (msg.get("content") or "").strip(), (msg.get("reasoning_content") or "").strip()
    except Exception:
        return "", ""


def _http_chat(ch: dict, system: str, user: str, max_tokens: int, timeout: float) -> dict:
    if ch["protocol"] == "anthropic":
        body = {"model": ch["model"], "max_tokens": max_tokens, "temperature": 0.2,
                "system": system, "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": ch["key"], "anthropic-version": "2023-06-01",
                   "Content-Type": "application/json"}
    else:
        body = {"model": ch["model"], "temperature": 0.2, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        headers = {"Authorization": f"Bearer {ch['key']}", "Content-Type": "application/json"}

    req = urllib.request.Request(ch["endpoint"],
                                 data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read()[:300].decode("utf-8", "replace")
        except Exception:
            pass
        return {"ok": False, "error": f"HTTP {e.code}: {detail}"}
    except (TimeoutError, socket.timeout):
        return {"ok": False, "error": f"API 超时（{timeout:.0f}s）"}
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        if "timed out" in msg.lower():
            return {"ok": False, "error": f"API 超时（{timeout:.0f}s）"}
        return {"ok": False, "error": msg}

    text, reasoning = _pick_text(data, ch["protocol"])
    if not text:
        return {"ok": False, "error": "模型返回空内容（max_tokens 可能被 reasoning 吃光）",
                "reasoning": reasoning[:400]}
    return {"ok": True, "text": text}


def _extract_json(text: str):
    """剥 ``` 围栏 + 取第一个平衡的 {...}（模型常带前后说明）。逻辑同 llm.py 的被验证实现。"""
    t = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(t[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except Exception:
                    return None
    return None


def _call_llm(ch: dict, system: str, user: str, deadline: float) -> dict:
    def left() -> float:
        return max(1.0, min(API_TIMEOUT, deadline - time.monotonic()))

    r = _http_chat(ch, system, user, 4000, left())
    if not r.get("ok") and "空内容" in (r.get("error") or "") and left() > 5:
        _notify("模型空内容（reasoning 吃光 4000 token），加预算重试一次")
        r = _http_chat(ch, system, user, 16000, left())
    if not r.get("ok"):
        return r

    obj = _extract_json(r["text"])
    if obj is None:
        return {"ok": False, "error": f"返回内容不是合法 JSON（前 200 字符：{r['text'][:200]!r}）"}
    return {"ok": True, "advice": obj}


def _normalize_advice(obj: dict) -> dict:
    """锁死四个字段；confidence 容错（缺失/百分比/越界都收敛到 0~1）。"""
    def field(name: str, default: str) -> str:
        v = obj.get(name)
        return v.strip() if isinstance(v, str) and v.strip() else default

    try:
        conf = float(obj.get("confidence"))
    except Exception:
        conf = -1.0
    if conf < 0:
        conf = 0.5
    elif conf > 100.0:
        conf = 1.0
    elif conf > 1.0:
        conf = conf / 100.0          # 模型写成 85 这种百分比
    return {
        "diagnosis": field("diagnosis", "(未给出)"),
        "next_action": field("next_action", "(未给出)"),
        "reason": field("reason", "(未给出)"),
        "confidence": round(conf, 2),
    }


# ── 提示词渲染 ──────────────────────────────────────────────────────────

def _render_prompt(tpl: str, mapping: dict):
    """按已知占位符逐个 str.replace —— prompt 里有字面量 {}（JSON 契约），不能用 .format。"""
    out, hits = tpl, 0
    for token, value in mapping.items():
        if token in out:
            hits += 1
            out = out.replace(token, value)
    return out, hits


def _emit(payload: dict) -> None:
    """stdout 的唯一出口。只在 --advice-json 时被调用（默认静默，理由见模块 docstring）。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


# ── 主流程 ──────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    want_advice_json = "--advice-json" in args

    start = time.monotonic()
    deadline = start + TOTAL_BUDGET

    # 1) 读 stdin 事件（TTY 下手动跑不阻塞）
    text = ""
    try:
        if not sys.stdin.isatty():
            text = sys.stdin.read()
    except Exception:
        text = ""
    raw = {}
    if text.strip():
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                raw = parsed
            else:
                _notify(f"stdin JSON 顶层不是对象（{type(parsed).__name__}），按空事件处理")
        except Exception:
            _notify(f"stdin 不是合法 JSON，按空事件处理（前 200 字符：{text[:200]!r}）")

    ev = _normalize_event(raw)

    # 2) 项目守卫：装在 user 级配置里 → 对所有项目都会触发，非本项目静默退出
    proj_dir = (os.environ.get("ZCODE_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR")
                or ev.get("cwd"))
    if proj_dir:
        try:
            if pathlib.Path(proj_dir).resolve() != PROJ:
                _notify(f"跳过：当前项目 {proj_dir} 不是 {PROJ}", to_file=False)
                return 0
        except Exception:
            pass

    # 3) 幂等（硬约束 3）
    key = _dedup_key(ev)
    prev = _read_json(LAST_FILE) or {}
    if prev.get("event_key") == key:
        _notify(f"幂等跳过：同一事件已处理过（key={key}）")
        return 0

    # 4) 采集状态
    st = collect_state()

    # 5) 凭据（硬约束 1）
    ch = _resolve_channel()
    if not ch.get("ok"):
        _notify(f"无凭据，跳过 API 调用 —— {ch.get('reason')}")
        return 0

    # 6) 渲染 prompt 并调用
    state_json = json.dumps(st.get("state"), ensure_ascii=False) if st.get("state") else "(无 accounts_state.json)"
    mapping = {
        "{responseText}": (ev.get("responseText") or "(空)")[:MAX_RESPONSE],
        "{last_run}": json.dumps(st.get("last_run"), ensure_ascii=False) if st.get("last_run") else "(无 runs.jsonl / iterations.jsonl 记录)",
        "{log_tail}": st.get("log_tail") or "(无)",
        "{trace_tail}": st.get("trace_tail") or "(无)",
        "{state}": state_json,
    }
    tpl = _read_text(PROMPT_FILE)
    if tpl:
        system, hits = _render_prompt(tpl, mapping)
    else:
        system, hits = DEFAULT_SYSTEM, 0
        _notify(f"未找到 {PROMPT_FILE.name}，改用内置 system prompt")
    user = "以上是完整输入，请输出 JSON 契约里的对象。"
    if hits == 0:
        user = ("状态快照（JSON）：\n"
                + json.dumps({k.strip("{}"): v for k, v in mapping.items()}, ensure_ascii=False)
                + "\n\n请只输出 JSON 契约里的对象。")

    _notify(f"调用 {ch['protocol']} 通道（{ch['source']} / {ch['model']}），"
            f"状态键 {[k for k in ('last_run', 'log_tail', 'trace_tail', 'state') if st.get(k)]}")
    r = _call_llm(ch, system, user, deadline)
    if not r.get("ok"):
        err = r.get("error") or "未知错误"
        if "超时" in err:
            _notify("API 超时，未写建议（bridge_last.json 保持上一轮内容）")
        else:
            _notify(f"API 调用失败，未写建议 —— {err}")
        if r.get("reasoning"):
            _notify(f"reasoning 片段：{r['reasoning'][:200]}")
        return 0

    advice = _normalize_advice(r["advice"])
    record = {
        "time": _now(),
        "event_key": key,
        "event": {k: (v[:300] if isinstance(v, str) else v) for k, v in ev.items()},
        "state_keys": [k for k in ("last_run", "log_tail", "trace_tail", "state") if st.get(k)],
        "channel": {"source": ch["source"], "model": ch["model"]},
        "advice": advice,
    }
    try:
        _write_atomic(LAST_FILE, record)                    # 硬约束 5
    except Exception as e:
        _notify(f"写 {LAST_FILE.name} 失败：{type(e).__name__}: {e}")
        return 0

    _notify(f"建议已写入 {LAST_FILE.name}（{time.monotonic() - start:.1f}s）："
            f"{advice['diagnosis']} → {advice['next_action']} (conf={advice['confidence']})")

    if want_advice_json:      # 默认不写 stdout —— 本构建没有 stdout 注入协议（见 docstring）
        _emit({"advice": advice})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                  # 兜底：hook 必须恒 rc=0，绝不能因本脚本报错
        try:
            _notify(f"未捕获异常（已吞，rc=0）：{type(e).__name__}: {e}")
        except Exception:
            pass
        sys.exit(0)
