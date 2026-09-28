#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""audit_waterline.py —— 统一审计流（runtime/audit/）的水位判据（**只读**）

给 30 分钟只读巡检（Hermes cron job 52b88b0ffca0 第三步的收尾项）与 mas-observe MCP
`audit_waterline` 提供一条机检判据：`AUDIT_STATUS=OK|WARN|CRIT`。

取数 = `tools/mas_audit.py::status()`（同一口径；本工具**不改**它的任何写路径）。<1s、无网络。

本工具**只判不治**：永不 `rotate --apply` / `prune --apply`（清理永远是人拍板，见
`docs/MAS_AUDIT_SCHEMA.md` §9）；不写任何审计数据、不改配置、不碰远端。
（唯一文件系统副作用：轮转**已到期**时 `mas_audit.status()` 会确保 `archive/` 目录存在——
继承自 mas_audit 的 dry-run 轮转判定，幂等，不搬运/不删除任何数据。）

判据（阈值集中在下方常量；`report_hours` = 统一流当前覆盖的小时数 = 首/末事件 ts 差，
无有效 ts 时退化为 live 文件存活时长）：
  CRIT  live > 25 MiB（远过轮转阈值 = 轮转坏了/被关掉了）
        或 归档缺失（archive 0 份）而 live > 8 MiB（轮转阈值已过却没转过一次）
  WARN  live > 10 MiB（与 mas_audit.LEGACY_WARN_BYTES / §7.4 同口径）
        或 report_hours ≥ 24 且 `rotate_now.rotate=True`（该轮转而未轮转）
        或 mas_audit.status() 自身 verdict=WARN（其口径：任一旧流文件 >10 MiB；**不降级**）
  OK    其余

用法：
  python tools/audit_waterline.py             # 文本报告 + 末行判据
  python tools/audit_waterline.py --json
  python tools/audit_waterline.py --selftest  # 离线自测（假统一流/假归档，tempfile，零真实读写）
  python tools/audit_waterline.py --audit-dir DIR   # 指到别的统一流目录（演练/异机）
退出码：0=OK；2=WARN；3=CRIT；1=取数失败（fail-closed）。

环境变量：MAAORCH_AUDIT_DIR（统一流目录，默认 <repo>/runtime/audit；演练/异机用）。
末行判据（机检 grep）：`AUDIT_STATUS=OK|WARN|CRIT live=… archives=… report_hours=… [原因=…]`
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import importlib
import json
import os
import shutil
import sys
import tempfile
import time

if hasattr(sys.stdout, "reconfigure"):                          # Windows 控制台中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

# ---- 阈值（改这里就是改巡检判据；口径依据 docs/MAS_AUDIT_SCHEMA.md §7.2/§7.4）----
WARN_MB = 10.0          # live > 10 MiB ⇒ WARN（= mas_audit.LEGACY_WARN_BYTES 口径）
CRIT_MB = 25.0          # live > 25 MiB ⇒ CRIT（8 MiB 轮转阈值的 3 倍还多 = 轮转坏了）
WARN_HOURS = 24.0       # report_hours ≥ 24h 且「该轮转未轮转」⇒ WARN
ARCHIVE_STALE_DAYS = 90  # 归档超保留期（仅供报告提示，不升级判据；清理是人拍板）

MI = 1024 * 1024


def _log(msg: str) -> None:
    print(msg, flush=True)


def _audit_mod():
    """拿 tools/mas_audit.py 模块（同一口径；import 失败由调用方兜）。"""
    import mas_audit  # noqa: E402
    return importlib.reload(mas_audit)     # reload：尊重当前进程的 MAAORCH_AUDIT_DIR


def _span_hours(live_path: str) -> tuple:
    """统一流当前覆盖的小时数（首/末事件 ts 差）。返回 (hours|None, first_ts, last_ts, rows)。

    只解析首/末两条非空行的 ts（大文件也不整读 JSON）；无有效 ts ⇒ None（调用方退化用文件年龄）。
    """
    if not os.path.exists(live_path):
        return None, None, None, 0
    first_ts = last_ts = None
    first_line = last_line = ""
    rows = 0
    with open(live_path, "r", encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            rows += 1
            if not first_line:
                first_line = ln
            last_line = ln

    def _ts(s: str):
        try:
            return json.loads(s).get("ts")
        except Exception:  # noqa: BLE001
            return None
    first_ts, last_ts = _ts(first_line), _ts(last_line)
    hours = None
    if first_ts and last_ts:
        with contextlib.suppress(ValueError):
            a = dt.datetime.strptime(str(first_ts)[:19], "%Y-%m-%d %H:%M:%S")
            b = dt.datetime.strptime(str(last_ts)[:19], "%Y-%m-%d %H:%M:%S")
            hours = round((b - a).total_seconds() / 3600.0, 2)
    return hours, first_ts, last_ts, rows


def evaluate(st: dict, span_hours, live_age_hours, max_mb: float) -> dict:
    """判据核心（纯函数，便于离线自测）：status() 的返回 ⇒ {verdict, reasons, checks}。"""
    live_bytes = int((st.get("live") or {}).get("bytes") or 0)
    live_mb = round(live_bytes / MI, 3)
    archives = st.get("archives") or []
    rot = st.get("rotate_now") or {}
    tool_verdict = str(st.get("verdict") or "OK")
    hours = span_hours if span_hours is not None else live_age_hours
    reasons: list = []
    verdict = "OK"

    if live_bytes > CRIT_MB * MI:
        verdict = "CRIT"
        reasons.append("live=%.1fMB>%.0fMB" % (live_mb, CRIT_MB))
    elif not archives and live_bytes > max_mb * MI:
        verdict = "CRIT"
        reasons.append("归档缺失（0 份）而 live=%.1fMB>轮转阈值%.0fMB" % (live_mb, max_mb))

    if verdict != "CRIT":
        if live_bytes > WARN_MB * MI:
            verdict = "WARN"
            reasons.append("live=%.1fMB>%.0fMB" % (live_mb, WARN_MB))
        elif hours is not None and hours >= WARN_HOURS and bool(rot.get("rotate")):
            verdict = "WARN"
            reasons.append("report_hours=%.1f≥%.0fh 且该轮转未轮转（%s）" % (hours, WARN_HOURS, rot.get("reason") or "rotate=true"))
        elif tool_verdict == "WARN":
            verdict = "WARN"
            reasons.append("mas_audit.status verdict=WARN（任一文件 >10MiB）")

    return {"verdict": verdict, "reasons": reasons,
            "checks": {"live_mb": live_mb, "archives_n": len(archives), "report_hours": hours,
                       "live_age_hours": live_age_hours, "rotate_now": bool(rot.get("rotate")),
                       "rotate_reason": rot.get("reason") or "", "tool_verdict": tool_verdict,
                       "thresholds": {"warn_mb": WARN_MB, "crit_mb": CRIT_MB, "warn_hours": WARN_HOURS,
                                      "rotate_mb": max_mb, "archive_stale_days": ARCHIVE_STALE_DAYS}}}


def collect() -> dict:
    """取数 + 判据。返回 {ok, elapsed_s, verdict, audit_status, reasons, checks, status, span…}。"""
    t0 = time.time()
    ma = _audit_mod()
    st = ma.status()
    live = st.get("live") or {}
    age_hours = None
    if live.get("mtime"):
        age_hours = round((time.time() - float(live["mtime"])) / 3600.0, 2)
    span, first_ts, last_ts, rows = _span_hours(str(live.get("path") or ma.LIVE))
    ev = evaluate(st, span, age_hours, float(ma.MAX_MB_DEFAULT))
    archives = st.get("archives") or []
    stale = [a for a in archives if float(a.get("age_days") or 0) > ARCHIVE_STALE_DAYS]
    line = "AUDIT_STATUS=%s live=%.1fMB archives=%d report_hours=%s rot=%s" % (
        ev["verdict"], ev["checks"]["live_mb"], len(archives),
        ("%.1f" % ev["checks"]["report_hours"]) if ev["checks"]["report_hours"] is not None else "?",
        "yes" if ev["checks"]["rotate_now"] else "no")
    if ev["reasons"]:
        line += " 原因=" + "；".join(ev["reasons"])
    return {"ok": True, "elapsed_s": round(time.time() - t0, 3), "verdict": ev["verdict"],
            "audit_status": line, "reasons": ev["reasons"], "checks": ev["checks"],
            "span": {"hours": span, "first_ts": first_ts, "last_ts": last_ts, "rows": rows},
            "archives": archives, "stale_archives_n": len(stale),
            "live": {"bytes": live.get("bytes"), "mtime": live.get("mtime"), "path": live.get("path")},
            "legacy_warn_n": len([l for l in (st.get("legacy") or []) if l.get("warn")]),
            "tool_verdict": st.get("verdict"), "limits": st.get("limits"), "total_bytes": st.get("total_bytes")}


def render(rep: dict) -> None:
    c = rep["checks"]
    _log("=" * 78)
    _log("审计水位（%s） 只读判据 · runtime/audit/" % dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    _log("=" * 78)
    _log("live: %.3f MB（%s B）/ 行 %s；归档 %d 份；覆盖 %s h；存活 %s h" % (
        c["live_mb"], rep["live"]["bytes"], rep["span"]["rows"], c["archives_n"],
        c["report_hours"] if c["report_hours"] is not None else "?", c["live_age_hours"]))
    _log("轮转判定：rotate=%s（%s）；阈值 warn>%.0fMB / crit>%.0fMB / ≥%sh 该转未转" % (
        c["rotate_now"], c["rotate_reason"], WARN_MB, CRIT_MB, WARN_HOURS))
    _log("旧流超阈：%d 个（mas_audit.status verdict=%s）；归档超保留期(%dd)：%d 份（清理是人拍板）" % (
        rep["legacy_warn_n"], rep["tool_verdict"], ARCHIVE_STALE_DAYS, rep["stale_archives_n"]))
    _log("-" * 78)
    _log(rep["audit_status"])
    _log("结论：" + {"OK": "OK（统一流水位正常）",
                     "WARN": "WARN（该看：轮转/收敛，见 docs/MAS_AUDIT_SCHEMA.md §7）",
                     "CRIT": "CRIT（轮转疑似失效；先人工确认，再 rotate --apply）"}[rep["verdict"]])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="统一审计流水位判据（只读）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--selftest", action="store_true", help="离线自测（假统一流/假归档）")
    ap.add_argument("--audit-dir", default="", help="统一流目录（默认取 MAAORCH_AUDIT_DIR 或 runtime/audit）")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.audit_dir:
        os.environ["MAAORCH_AUDIT_DIR"] = a.audit_dir
    try:
        rep = collect()
    except Exception as e:  # noqa: BLE001
        print("取数失败: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        print("AUDIT_STATUS=UNKNOWN:取数失败（%s）" % str(e)[:120])
        return 1
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        render(rep)
    return {"OK": 0, "WARN": 2, "CRIT": 3}[rep["verdict"]]


# ---------------------------------------------------------------- 离线自测
def _fake_live(path: str, mb: float, span_hours: float, rows: int = 0) -> int:
    """造一个假统一流：合法 v1 记录、体积≈mb MiB、首末 ts 跨 span_hours。返回字节数。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    target = int(mb * MI)
    now = dt.datetime.now()
    pad_len = 380
    lines = []
    n = rows or max(2, int(target / (pad_len + 120)))
    for i in range(n):
        ts = now - dt.timedelta(hours=span_hours * (1 - i / max(1, n - 1)))
        rec = {"v": 1, "ts": ts.strftime("%Y-%m-%d %H:%M:%S"), "actor": "tool:audit_waterline_selftest",
               "action": "audit.selftest", "target": "-", "result": "info", "reason": "",
               "params": {"pad": "x" * pad_len, "i": i}}
        lines.append(json.dumps(rec, ensure_ascii=False))
    blob = "\n".join(lines) + "\n"
    if len(blob) < target:                      # 补行到目标体积（保持 ts 单调：复制末行）
        need = (target - len(blob)) // (len(lines[-1]) + 1) + 1
        blob += (lines[-1] + "\n") * need
    with open(path, "w", encoding="utf-8") as f:
        f.write(blob)
    if span_hours > 24:                          # 让年龄阈值也能命中（mtime 不可造假时不依赖它）
        old = time.time() - 8 * 86400
        os.utime(path, (old, old))
    return len(blob.encode("utf-8"))


def _fake_archive(audit_dir: str, name: str = "mas_audit-20260920-000000.jsonl.gz", kb: int = 40) -> str:
    import gzip
    os.makedirs(os.path.join(audit_dir, "archive"), exist_ok=True)
    p = os.path.join(audit_dir, "archive", name)
    rec = {"v": 1, "ts": "2026-09-20 00:00:00", "actor": "tool:audit_waterline_selftest",
           "action": "audit.selftest", "target": "-", "result": "info", "reason": ""}
    with gzip.open(p, "wb") as f:
        f.write((json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8") * (kb * 4))
    return p


def selftest() -> int:
    """离线自测：四类场景（OK / WARN 体积 / WARN 该转未转 / CRIT 归档缺失 / CRIT 体积）。

    全程在 tempfile 目录里造**假统一流+假归档**，零真实 runtime 读写、零网络、零写入仓库。
    """
    cases = []

    def run(name, expect, mb, span_hours, with_archive, min_mb=8.0, rows=0, age_days=None,
            tool_verdict_ok=True):
        tmp = tempfile.mkdtemp(prefix="audit_wl_")
        try:
            d = os.path.join(tmp, "audit")
            live = os.path.join(d, "mas_audit.jsonl")
            n = _fake_live(live, mb, span_hours, rows=rows)
            if with_archive:
                _fake_archive(d)
            if age_days is not None:
                old = time.time() - age_days * 86400
                os.utime(live, (old, old))
            prev = os.environ.get("MAAORCH_AUDIT_DIR")
            os.environ["MAAORCH_AUDIT_DIR"] = d
            try:
                rep = collect()
            finally:
                if prev is None:
                    os.environ.pop("MAAORCH_AUDIT_DIR", None)
                else:
                    os.environ["MAAORCH_AUDIT_DIR"] = prev
            got = rep["verdict"]
            ok = (got == expect)
            cases.append((ok, name, expect, got, rep["audit_status"], n, rep["reasons"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    run("OK（live 0.2MiB / 覆盖 2h / 无归档）", "OK", 0.2, 2, False)
    run("WARN（live 11MiB > 10MiB）", "WARN", 11.0, 2, True)
    run("WARN（覆盖 26h 且该转未转：文件 8 天前 ⇒ age_hit）", "WARN", 0.2, 26, True, age_days=8)
    run("CRIT（归档缺失且 live 9MiB > 轮转 8MiB）", "CRIT", 9.0, 2, False)
    run("CRIT（live 26MiB > 25MiB，有归档也一样）", "CRIT", 26.0, 2, True)
    run("OK（归档在 / live 7MiB < 10MiB，不误报）", "OK", 7.0, 12, True)

    _log("audit_waterline --selftest（全程 tempfile 假目录，零真实读写）")
    for ok, name, expect, got, line, n, reasons in cases:
        _log("  [%s] %-46s 期望=%-4s 实得=%-4s bytes=%d %s" % (
            "OK" if ok else "FAIL", name, expect, got, n, ("原因=" + "；".join(reasons)) if reasons else ""))
        _log("        %s" % line)
    bad = [c for c in cases if not c[0]]
    _log("SELFTEST=%s (%d/%d)" % ("OK" if not bad else "FAIL", len(cases) - len(bad), len(cases)))
    return 0 if not bad else 3


if __name__ == "__main__":
    sys.exit(main())
