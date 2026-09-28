#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermes mas 巡检的【只读】agent-time 量化器。

定位：把「Hermes 定时巡检 AUTO-MAS」这件事折算成可核算的 **agent time**：
每次 fire 的存活时长、LLM 调用数、工具调用数、token（含 cache/reasoning）、估算成本，
再按节拍投影到 24 小时 / 30 天。它是**记账器**，不是执行器：
只读 Hermes 自己的运行产物，绝不写 Hermes home，绝不触发任何巡检/补跑。

数据来源（全部只读）：
  <home>/profiles/<profile>/cron/executions.db      durable 执行尝试：status/source/pid/起止/错误
  <home>/profiles/<profile>/cron/usage_audit.jsonl  每次 fire 的 token 与 duration_ms
  <home>/profiles/<profile>/state.db                sessions 表：api_call_count/tool_call_count/token/成本

用法：
    python tools/mas_agent_time.py                       # 打印摘要 + 写 docs/audits/<date>_mas_agent_time.{md,json}
    python tools/mas_agent_time.py --json                # 只打印 JSON
    python tools/mas_agent_time.py --no-write            # 只打印，不落盘
    python tools/mas_agent_time.py --profile mas --limit 50 --cadence-minutes 30

口径（写死在代码里，报告里同步声明）：
  agent_time  = finished_at - started_at（该次 fire 的进程存活时长，秒）
  llm_calls   = state.db sessions.api_call_count
  tool_calls  = state.db sessions.tool_call_count
  失败 fire 的 agent_time 极短（秒级）且没有 session，因此投影只用 completed 样本。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sqlite3
import statistics
import sys

SENSITIVE_KEYS = ("password", "token", "secret", "credential", "cookie", "auth")
DEFAULT_HERMES_HOME = pathlib.Path(os.environ.get("MAAORCH_HERMES_HOME")
                                   or os.environ.get("HERMES_HOME")
                                   or (pathlib.Path.home() / "AppData" / "Local" / "hermes"))
ROOT = pathlib.Path(__file__).resolve().parents[1]

# 已知故障签名 → 可读标签（用于健康判定与告警）
ERROR_SIGNATURES = (
    ("pydantic_core", "运行时环境错配：gateway 的 sys.path 挂着不匹配的 pydantic_core（3.11 venv vs 3.14 解释器）"),
    ("No module named", "依赖缺失/环境错配"),
    ("Failed to initialize OpenAI client", "模型客户端初始化失败"),
    ("lost its durable fire claim", "fire claim 所有权丢失（上一轮未收敛）"),
)


def classify_error(err):
    """把自由文本错误归类，便于统计与告警。"""
    if not err:
        return ""
    for needle, label in ERROR_SIGNATURES:
        if needle in err:
            return label
    return err.strip().splitlines()[0][:120]


def _scrub(obj):
    """读入即丢弃凭据类键（沿用 tools/mas_catchup_check.py 的约定）。"""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()
                if not any(s in str(k).lower() for s in SENSITIVE_KEYS)}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


# ── 读取（只读） ────────────────────────────────────────────────────────

def _connect_ro(path):
    """以只读 URI 打开 sqlite：写操作在连接层就失败。"""
    if not path.is_file():
        raise FileNotFoundError("找不到 %s" % path)
    conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_ts(value):
    """executions.db 用 ISO 字符串，state.db 用 epoch 秒。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(float(value)).astimezone()
    text = str(value).strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def load_executions(db, job_id=None, limit=200):
    """durable 执行尝试（含失败），按时间倒序。"""
    conn = _connect_ro(db)
    try:
        sql = ("SELECT id, job_id, source, pid, status, started_at, finished_at, claimed_at,"
               " delivery_outcome, error FROM executions")
        args = []
        if job_id:
            sql += " WHERE job_id = ?"
            args.append(job_id)
        sql += " ORDER BY rowid DESC LIMIT ?"
        args.append(int(limit))
        rows = []
        for row in conn.execute(sql, args):
            started, finished = _parse_ts(row["started_at"]), _parse_ts(row["finished_at"])
            wall = (finished - started).total_seconds() if (started and finished) else None
            rows.append({
                "execution_id": row["id"], "job_id": row["job_id"], "source": row["source"],
                "pid": row["pid"], "status": row["status"],
                "started_at": started.isoformat() if started else None,
                "finished_at": finished.isoformat() if finished else None,
                "agent_time_s": wall,
                "error": row["error"] or "",
                "error_kind": classify_error(row["error"]),
                "delivery_outcome": row["delivery_outcome"],
            })
        return rows
    finally:
        conn.close()


def load_usage_audit(path):
    """usage_audit.jsonl：每次 fire 的 token / duration_ms / model。"""
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = _scrub(json.loads(line))
        except json.JSONDecodeError:
            continue
        rec["ts_dt"] = _parse_ts(rec.get("ts"))
        out.append(rec)
    return out


def load_cron_sessions(db, limit=200):
    """state.db 里 source='cron' 的会话 = 一次巡检的 agent 侧账本。"""
    conn = _connect_ro(db)
    try:
        rows = []
        for row in conn.execute(
            "SELECT id, model, started_at, ended_at, end_reason, message_count, tool_call_count,"
            " api_call_count, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,"
            " reasoning_tokens, estimated_cost_usd FROM sessions"
            " WHERE substr(id, 1, 5) = 'cron_' ORDER BY started_at DESC LIMIT ?", (int(limit),)):
            started, ended = _parse_ts(row["started_at"]), _parse_ts(row["ended_at"])
            rows.append({
                "session_id": row["id"], "model": row["model"],
                "started_at": started.isoformat() if started else None,
                "ended_at": ended.isoformat() if ended else None,
                "agent_time_s": (ended - started).total_seconds() if (started and ended) else None,
                "end_reason": row["end_reason"],
                "message_count": row["message_count"], "tool_calls": row["tool_call_count"],
                "llm_calls": row["api_call_count"],
                "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
                "cache_read_tokens": row["cache_read_tokens"],
                "cache_write_tokens": row["cache_write_tokens"],
                "reasoning_tokens": row["reasoning_tokens"],
                "estimated_cost_usd": row["estimated_cost_usd"],
                "started_dt": started,
            })
        return rows
    finally:
        conn.close()


# ── 归并（纯函数，便于离线测试） ────────────────────────────────────────

def _nearest(records, when, key, tolerance_s=600.0, used=None, lead_s=None):
    """按时间就近匹配，且一对一。

    lead_s 不为空时要求候选时间戳不早于 when - lead_s（一次 fire 的 session 只可能
    在该 fire 开始之后建立；否则 18:12 的失败 fire 会去认领 18:16 那次成功运行的账本）。
    """
    best, best_delta = None, None
    for rec in records:
        stamp = rec.get(key)
        if stamp is None:
            continue
        if used is not None and id(rec) in used:
            continue
        delta = (stamp - when).total_seconds()
        if lead_s is not None and delta < -abs(lead_s):
            continue
        if abs(delta) <= tolerance_s and (best_delta is None or abs(delta) < abs(best_delta)):
            best, best_delta = rec, delta
    return best


def join_runs(executions, sessions, usage):
    """把「执行尝试（含失败）」与「agent 侧账本」按时间对齐成一条条 run。"""
    runs = []
    used_sessions, used_usage = set(), set()
    for ex in executions:
        started = dt.datetime.fromisoformat(ex["started_at"]) if ex["started_at"] else None
        finished = dt.datetime.fromisoformat(ex["finished_at"]) if ex["finished_at"] else None
        sess = _nearest(sessions, started, "started_dt", tolerance_s=180.0,
                        used=used_sessions, lead_s=5.0) if started else None
        if sess is not None:
            used_sessions.add(id(sess))
        use = _nearest([u for u in usage if u.get("job_id") in (None, ex["job_id"])],
                       finished, "ts_dt", used=used_usage, lead_s=180.0) if finished else None
        if use is not None:
            used_usage.add(id(use))
        tokens = sum(int(sess.get(k) or 0) for k in
                     ("input_tokens", "output_tokens", "cache_read_tokens",
                      "cache_write_tokens", "reasoning_tokens")) if sess else None
        runs.append({
            **ex,
            "session_id": sess["session_id"] if sess else None,
            "model": (sess or {}).get("model") or (use or {}).get("model"),
            "end_reason": (sess or {}).get("end_reason"),
            "llm_calls": (sess or {}).get("llm_calls"),
            "tool_calls": (sess or {}).get("tool_calls"),
            "message_count": (sess or {}).get("message_count"),
            "tokens_total": tokens,
            "tokens_detail": {k: (sess or {}).get(k) for k in
                              ("input_tokens", "output_tokens", "cache_read_tokens",
                               "reasoning_tokens")} if sess else None,
            "estimated_cost_usd": (sess or {}).get("estimated_cost_usd"),
            "audit_duration_ms": (use or {}).get("duration_ms"),
            "audit_total_tokens": (use or {}).get("total_tokens"),
            "audit_error": (use or {}).get("error"),
        })
    return runs


def summarize(runs, cadence_minutes=30, health_window=3):
    """汇总 + 投影。投影只用 completed 样本（失败样本是秒级空转，会把均值带偏）。"""
    done = [r for r in runs if r.get("status") == "completed" and r.get("agent_time_s")]
    running = [r for r in runs if r.get("status") == "running"]
    # 只有 completed 才算成功；running 既不是成功也不是失败（巡检可能正在飞）。
    failed = [r for r in runs if r.get("status") not in ("completed", "running")]
    wall = [r["agent_time_s"] for r in done]
    per_fire = statistics.median(wall) if wall else None
    fires_per_day = 1440.0 / cadence_minutes if cadence_minutes else None
    unattended_all = [r for r in runs if r.get("source") == "builtin"]  # 无人值守路径（builtin）样本
    last = runs[0] if runs else None
    window = list(runs[:health_window])
    auto_window = [r for r in window if r.get("source") == "builtin"]
    # 窗口内的旧失败不该永远压着新成功：最近一次自动 fire 成功、失败只出现在更早位置 = 已恢复。
    latest_auto = auto_window[0] if auto_window else None
    auto_latest_ok = bool(latest_auto) and latest_auto.get("status") in ("completed", "running")
    stale_failures = [r for r in auto_window[1:] if r.get("status") not in ("completed", "running")]
    kinds = {}
    for r in failed:
        if r["error_kind"]:
            kinds[r["error_kind"]] = kinds.get(r["error_kind"], 0) + 1
    med_tokens = statistics.median([r["tokens_total"] for r in done if r["tokens_total"]]) if done else None
    return {
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "runs_total": len(runs),
        "runs_completed": len(done),
        "runs_running": len(running),
        "runs_failed": len(failed),
        "runs_by_source": {s: len([r for r in runs if r.get("source") == s]) for s in
                           sorted({r.get("source") or "?" for r in runs})},
        "success_rate": (len(done) / len(runs)) if runs else None,
        "agent_time_median_s": per_fire,
        "agent_time_mean_s": (statistics.fmean(wall) if wall else None),
        "agent_time_max_s": (max(wall) if wall else None),
        "llm_calls_median": (statistics.median([r["llm_calls"] for r in done if r["llm_calls"]]) if done else None),
        "tool_calls_median": (statistics.median([r["tool_calls"] for r in done if r["tool_calls"]]) if done else None),
        "tokens_median": med_tokens,
        "cadence_minutes": cadence_minutes,
        "fires_per_day": fires_per_day,
        "agent_time_per_day_h": (per_fire * fires_per_day / 3600.0) if (per_fire and fires_per_day) else None,
        "utilization_pct": ((per_fire * fires_per_day / 86400.0) * 100.0) if (per_fire and fires_per_day) else None,
        "tokens_per_day": (med_tokens * fires_per_day) if (med_tokens and fires_per_day) else None,
        "unattended_runs": len(unattended_all),
        "unattended_completed": len([r for r in unattended_all if r.get("status") == "completed"]),
        "failure_kinds": kinds,
        "health_window": health_window,
        "recent_fires": [{"started_at": r.get("started_at"), "source": r.get("source"),
                          "status": r.get("status"), "agent_time_s": r.get("agent_time_s"),
                          "error_kind": r.get("error_kind")} for r in window],
        # running 不算故障：正在飞的巡检不能在探针里被判死。
        "auto_path_healthy": (all(r.get("status") in ("completed", "running") for r in auto_window)
                              if auto_window else None),
        "auto_recovered": bool(latest_auto and auto_latest_ok and stale_failures),
        "auto_recovered_since": (latest_auto.get("started_at") if latest_auto and auto_latest_ok else None),
        "auto_stale_failures": len(stale_failures),
        "last_run": last,
    }


# ── 渲染 ────────────────────────────────────────────────────────────────

def _fmt(value, digits=1, suffix=""):
    if value is None:
        return "—"
    if isinstance(value, float):
        return ("%.*f" % (int(digits), value)) + suffix
    return "%s%s" % (value, suffix)

def _fmt_time(value):
    """ISO 时间戳裁成 “YYYY-MM-DD HH:MM:SS”（解析不了就原样回显）。"""
    parsed = _parse_ts(value)
    if parsed is None:
        return value or "—"
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def render_markdown(summary, runs, profile="mas", job_id=None):
    s = summary
    lines = []
    lines.append("# Hermes 巡检 agent-time 量化（profile=%s%s）"
                 % (profile, "，job=%s" % job_id if job_id else ""))
    lines.append("")
    lines.append("生成时间：%s ｜ 数据来源：cron/executions.db + cron/usage_audit.jsonl + state.db（只读）"
                 % s["generated_at"])
    lines.append("")
    lines.append("## 1. 结论")
    lines.append("")
    if s["auto_path_healthy"] is True:
        verdict = "**无人值守路径健康**（最近 %d 次自动 fire 全部完成）" % s["health_window"]
    elif s["auto_path_healthy"] is None:
        verdict = "**无人值守路径无样本**（窗口内没有 builtin fire）"
    elif s["auto_recovered"]:
        verdict = ("**无人值守路径已恢复**（最近一次自动 fire %s 成功；窗口内 %d 次失败为修复前遗留）"
                   % (_fmt_time(s["auto_recovered_since"]), s["auto_stale_failures"]))
    else:
        verdict = "**无人值守路径故障**（最近 %d 次自动 fire 未全部完成）" % s["health_window"]
    lines.append("- 自动巡检健康：%s" % verdict)
    lines.append("- 单次巡检 agent time（中位数，completed 样本）：**%s**" % _fmt(s["agent_time_median_s"], 0, " s"))
    lines.append("- 节拍：每 %s 分钟一次 ⇒ %s 次/天；据此投影 **%s h/天**（占全天 %s）"
                 % (s["cadence_minutes"], _fmt(s["fires_per_day"], 0),
                    _fmt(s["agent_time_per_day_h"], 2), _fmt(s["utilization_pct"], 1, "%")))
    lines.append("- 单次 LLM/工具调用（中位数）：%s / %s；单次 token（中位数）：%s"
                 % (_fmt(s["llm_calls_median"], 0), _fmt(s["tool_calls_median"], 0), _fmt(s["tokens_median"], 0)))
    lines.append("- token 投影：**%s / 天**" % _fmt(s["tokens_per_day"], 0))
    lines.append("- 样本：共 %d 次尝试（完成 %d / 进行中 %d / 失败 %d），按来源 %s"
                 % (s["runs_total"], s["runs_completed"], s.get("runs_running", 0), s["runs_failed"],
                    json.dumps(s["runs_by_source"])))
    if s["failure_kinds"]:
        lines.append("- 失败分类：" + "；".join("%s ×%d" % (k, v) for k, v in s["failure_kinds"].items()))
    lines.append("")
    lines.append("## 2. 逐次明细")
    lines.append("")
    lines.append("| 开始时间 | 来源 | 结果 | agent time | LLM | 工具 | token | 失败分类 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in runs:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            _fmt_time(r["started_at"]), r["source"], r["status"],
            _fmt(r["agent_time_s"], 1, " s"), _fmt(r["llm_calls"], 0), _fmt(r["tool_calls"], 0),
            _fmt(r["tokens_total"], 0), r["error_kind"] or ""))
    lines.append("")
    lines.append("## 3. 口径与边界")
    lines.append("")
    lines.append("- agent_time = executions.finished_at - started_at（该次 fire 的进程存活时长）。")
    lines.append("- llm_calls / tool_calls / token 取自 state.db.sessions（id LIKE 'cron_%'），按开始时间就近对齐。")
    lines.append("- 失败 fire 的 agent_time 是秒级空转、且没有 session，因此投影只用 completed 样本，避免均值被稀释。")
    lines.append("- 估算成本字段（estimated_cost_usd）在当前 provider 下多为 0，只作占位，不作结论。")
    lines.append("- 本工具只读：以 mode=ro 打开 Hermes 的 sqlite，不写入 Hermes home。")
    lines.append("")
    lines.append("关联：docs/HERMES_MAS_HANDOVER.md（交接契约）、tools/mas_catchup_check.py（补漏判据，只提议不发车）。")
    lines.append("")
    return "\n".join(lines)


def build_report(hermes_home, profile, job_id=None, limit=50, cadence_minutes=30, health_window=3):
    base = pathlib.Path(hermes_home) / "profiles" / profile
    executions = load_executions(base / "cron" / "executions.db", job_id=job_id, limit=limit)
    sessions = load_cron_sessions(base / "state.db", limit=limit)
    usage = load_usage_audit(base / "cron" / "usage_audit.jsonl")
    runs = join_runs(executions, sessions, usage)
    return summarize(runs, cadence_minutes=cadence_minutes, health_window=health_window), runs


def main(argv=None):
    ap = argparse.ArgumentParser(description="Hermes mas 巡检的只读 agent-time 量化器")
    ap.add_argument("--hermes-home", default=str(DEFAULT_HERMES_HOME))
    ap.add_argument("--profile", default="mas")
    ap.add_argument("--job", default="52b88b0ffca0", help="cron job id；传空串表示全部 job")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--cadence-minutes", type=int, default=30)
    ap.add_argument("--health-window", type=int, default=3)
    ap.add_argument("--check", action="store_true",
                    help="健康探针：无人值守路径（builtin）不健康时以退出码 3 结束；UNKNOWN 以 4 结束")
    ap.add_argument("--json", action="store_true", help="只打印 JSON")
    ap.add_argument("--no-write", action="store_true", help="不写 docs/audits 报告")
    ap.add_argument("--out-dir", default=str(ROOT / "docs" / "audits"))
    args = ap.parse_args(argv)

    job_id = args.job or None
    try:
        summary, runs = build_report(args.hermes_home, args.profile, job_id=job_id, limit=args.limit,
                                     cadence_minutes=args.cadence_minutes,
                                     health_window=args.health_window)
    except FileNotFoundError as e:
        print("读取失败（Hermes 产物缺失）：%s" % e, file=sys.stderr)
        return 2

    if args.check:
        # 探针语义：只有"最近窗口内的 builtin fire 全部完成"才算健康；
        # 没有样本 = UNKNOWN（不代表没问题，也不代表有问题）。
        healthy = summary["auto_path_healthy"]
        if healthy:
            print("AUTO_PATH=HEALTHY （最近 %d 次自动 fire 无失败；进行中的不算失败）" % summary["health_window"])
            return 0
        if healthy is None:
            print("AUTO_PATH=UNKNOWN （窗口内没有 builtin fire，无法判断无人值守路径）")
            return 4
        if summary.get("auto_recovered"):
            print("AUTO_PATH=RECOVERED （最近一次自动 fire %s 成功；窗口内 %d 次失败为修复前遗留）"
                  % (summary.get("auto_recovered_since") or "?", summary.get("auto_stale_failures", 0)))
            for f in summary["recent_fires"]:
                print("  - %s source=%s status=%s agent_time=%s %s"
                      % (f["started_at"], f["source"], f["status"], f["agent_time_s"], f["error_kind"] or ""))
            return 0
        print("AUTO_PATH=FAILING （最近 %d 次自动 fire 未全部完成）" % summary["health_window"])
        for f in summary["recent_fires"]:
            print("  - %s source=%s status=%s agent_time=%s %s"
                  % (f["started_at"], f["source"], f["status"], f["agent_time_s"], f["error_kind"] or ""))
        return 3

    payload = {"summary": summary, "runs": runs}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(summary, runs, profile=args.profile, job_id=job_id))

    if not args.no_write:
        out_dir = pathlib.Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.date.today().isoformat()
        md_path = out_dir / ("%s_mas_agent_time.md" % stamp)
        json_path = out_dir / ("%s_mas_agent_time.json" % stamp)
        md_path.write_text(render_markdown(summary, runs, profile=args.profile, job_id=job_id), encoding="utf-8")
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.json:
            print("\n已写出：%s\n         %s" % (md_path, json_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
