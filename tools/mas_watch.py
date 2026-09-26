# -*- coding: utf-8 -*-
"""MAAOrch L3 守护进程：日报 + 告警 + 失败自动补跑（工作流层第一个闭环）。

设计（对应 RUNBOOK 第 10 节质量基线）：
- **定时**：由计划任务每 20 分钟调一次；**只在班次窗口内干活**，窗口外静默退出。
- **判定"班次收工"**：连续两次（本进程两次调用之间）检测到 0 个 MAA 进程 —— 队列换项空隙里也会是 0，单次判断会误判。
- **一个班只出一份报告**（state 里记 `reported[日期] = [班次]`），避免重复刷屏。
- **失败自动补跑**：报告里若有 `|X|` 的号 → 调 `tools/auto_mas_rerun.py` 建临时队列补跑；
  **同号每天最多补 1 次**（state 里记 `rerun[日期]`），单批最多 8 个。
- **通知**：平时静默；有异常才弹系统通知 + 写报告文件。
- **留痕**：`runtime/mas_ledger.jsonl` 追加每条事件（报告/补跑/结果）。

用法：
    python tools/mas_watch.py              # 正常守护（计划任务用这个）
    python tools/mas_watch.py --force      # 无视窗口/去重，立刻出报告（调试用）
    python tools/mas_watch.py --no-rerun   # 只报告不补跑
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(ROOT, "runtime")
REPORTS = os.path.join(RUNTIME, "reports")
STATE = os.path.join(RUNTIME, "mas_watch_state.json")
LEDGER = os.path.join(RUNTIME, "mas_ledger.jsonl")
SSH = ["ssh", "-i", os.path.expanduser(r"~\.ssh\maaorch_target"),
       "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
       "Administrator@100.79.173.69"]
REMOTE = "chcp 65001 >nul && set PYTHONIOENCODING=utf-8 && python E:/_mas_watch_data.py"

DISK_WARN_GB = {"C": 60, "E": 150}          # 低于这个值告警（当前 C:238/E:1863 的总量基准）


def log_event(kind: str, **kw) -> None:
    os.makedirs(RUNTIME, exist_ok=True)
    rec = {"ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "kind": kind}
    rec.update(kw)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_state() -> dict:
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {"maa_counts": [], "reported": {}, "rerun": {}}


def save_state(st: dict) -> None:
    os.makedirs(RUNTIME, exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def fetch() -> dict | None:
    try:
        r = subprocess.run(SSH + [REMOTE], capture_output=True, timeout=600)
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
        for ln in out.splitlines():
            ln = ln.strip()
            if ln.startswith("{") and ln.endswith("}"):
                return json.loads(ln)
    except Exception as e:                                     # noqa: BLE001
        log_event("fetch_error", error=str(e)[:200])
    return None


def local_conf() -> dict:
    """本地私有配置（不进版本库）：runtime/mas_watch.local.json
    例：{"discord_webhook": "https://discord.com/api/webhooks/xxx/yyy", "notify_on": "abnormal"}
    """
    for p in (os.path.join(RUNTIME, "mas_watch.local.json"),):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:                                      # noqa: BLE001
            continue
    return {}


def _post_multipart(url: str, headers: dict, content: str, report_path: str | None) -> bool:
    """给 Discord 发一条消息（正文 + 可选 .md 附件）。webhook 与 Bot 走同一个函数，只差鉴权头。"""
    boundary = "----MAAOrch" + uuid.uuid4().hex
    parts: list[bytes] = []

    def add(name: str, value, filename: str | None = None, ctype: str | None = None) -> None:
        head = '--%s\r\nContent-Disposition: form-data; name="%s"' % (boundary, name)
        if filename:
            head += '; filename="%s"' % filename
        head += "\r\n"
        if ctype:
            head += "Content-Type: %s\r\n" % ctype
        head += "\r\n"
        raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
        parts.append(head.encode("utf-8") + raw + b"\r\n")

    add("payload_json", json.dumps({"content": content[:1900]}, ensure_ascii=False),
        ctype="application/json")
    if report_path and os.path.exists(report_path):
        add("files[0]", open(report_path, encoding="utf-8").read().encode("utf-8"),
            os.path.basename(report_path), "text/markdown")
    body = b"".join(parts) + ("--%s--\r\n" % boundary).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={**headers, "Content-Type": "multipart/form-data; boundary=%s" % boundary,
                 "User-Agent": "MAAOrch/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return 200 <= r.status < 300


def notify_discord(title: str, summary: str, report_path: str | None = None) -> bool:
    """推送到 Discord：**优先 Bot**（bot_token + channel_id），没有则回退 webhook。
    配置都在 runtime/mas_watch.local.json（已在 .gitignore）。
    """
    conf = local_conf()
    content = "**%s**\n%s" % (title, summary)
    token = conf.get("bot_token") or os.environ.get("MAS_DISCORD_BOT_TOKEN", "")
    channel = str(conf.get("channel_id") or os.environ.get("MAS_DISCORD_CHANNEL_ID", "") or "")
    webhook = conf.get("discord_webhook") or os.environ.get("MAS_DISCORD_WEBHOOK", "")
    try:
        if token and channel:
            ok = _post_multipart("https://discord.com/api/v10/channels/%s/messages" % channel,
                                 {"Authorization": "Bot %s" % token}, content, report_path)
            log_event("notify_discord", via="bot", ok=ok)
            return ok
        if webhook:
            ok = _post_multipart(webhook, {}, content, report_path)
            log_event("notify_discord", via="webhook", ok=ok)
            return ok
    except Exception as e:                                     # noqa: BLE001
        log_event("notify_discord_error", error=str(e)[:200])
    return False


def notify(title: str, body: str) -> None:
    """Windows 气泡通知（不装任何外部模块）"""
    ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
          "$n = New-Object System.Windows.Forms.NotifyIcon; "
          "$n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; "
          f"$n.ShowBalloonTip(15000, '{title}', '{body}', 'Info'); "
          "Start-Sleep -Seconds 8; $n.Dispose()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60)
    except Exception:                                          # noqa: BLE001
        pass


def build_report(d: dict, rerun_done: list, rerun_planned: list) -> str:
    now, shift = d["now"], d["shift"]
    L = [f"# MAAOrch 日报 · {now} · {'早班' if shift == 'daily' else '晚班'}",
         "",
         f"- 账号记录：**{len(d['accounts'])}** 个｜失败 **{len(d['failures'])}**｜未跑 **{len(d['never_ran'])}**",
         f"- 盘位：" + "、".join(f"{k}: 剩 {v['free_gb']}G / {v['total_gb']}G"
                                + (" ⚠️" if v["free_gb"] < DISK_WARN_GB.get(k, 0) else "")
                                for k, v in sorted(d["disk"].items())),
         ""]
    if d["queues"]:
        L += ["## 各队列", "", "| 队列 | 项 | 完成 | 首个 | 最新 | 未完成 |", "|---|---|---|---|---|---|"]
        for qn, q in sorted(d["queues"].items()):
            L.append("| %s | %d | %d | %s | %s | %s |" % (
                qn, q["items"], q["done"], q["first"] or "-", q["last"] or "-",
                "、".join(p[:10] for p in q["pending"]) or "—"))
        L.append("")
    if d["failures"]:
        L += ["## ⚠️ 失败（最后一条不是 Success）", "", "| 账号 | 原因 | 时间 |", "|---|---|---|"]
        for f in d["failures"]:
            L.append(f"| {f['name']} | {f['reason']} | {f['ts']} |")
        L.append("")
    if d["never_ran"]:
        L += ["## 未跑（今天没有记录）", "", "、".join(d["never_ran"]), ""]
    if rerun_planned or rerun_done:
        L += ["## 自动补跑", ""]
        if rerun_planned:
            L.append("- 本轮补跑：**%s**" % "、".join(rerun_planned))
        if rerun_done:
            L.append("- 今天已补跑过（不再重复）：%s" % "、".join(rerun_done))
        L.append("")
    if not d["failures"] and not d["never_ran"]:
        L.append("✅ 本班全部成功，无异常。")
        L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="无视窗口与去重，立刻出报告")
    ap.add_argument("--no-rerun", action="store_true", help="只报告，不自动补跑")
    ap.add_argument("--quiet", action="store_true", help="不弹通知")
    ap.add_argument("--test-notify", action="store_true", help="只测 Discord webhook 连通性")
    args = ap.parse_args()

    st = load_state()

    # --test-notify 必须最先处理：否则会被下面的"班次窗口/在跑"静默 return 拦住
    if args.test_notify:
        ok = notify_discord("MAAOrch 连通性测试", "这是一条测试消息（`--test-notify`）。", None)
        print("Discord 推送:", "成功 ✓" if ok else
              "失败 ✗（runtime/mas_watch.local.json 里填 bot_token+channel_id，或 discord_webhook）")
        return 0 if ok else 3

    d = fetch()
    if not d:
        print("采集失败（主机不可达或脚本报错）")
        return 2

    today = d["now"][:10]
    shift = d["shift"]
    running = int(d.get("maa_processes") or 0)

    # 空闲判定：连续两次（本次与上次）都为 0
    counts = (st.get("maa_counts") or [])[-1:]
    st["maa_counts"] = counts + [running]
    idle_twice = len(st["maa_counts"]) >= 2 and all(c == 0 for c in st["maa_counts"])

    if not args.force:
        if shift is None:
            save_state(st)
            return 0
        if running > 0:
            save_state(st)
            return 0
        if not idle_twice:
            save_state(st)
            return 0
        if shift in (st.get("reported") or {}).get(today, []):
            save_state(st)
            return 0

    # ---- 出报告 ----
    rerun_done = (st.get("rerun") or {}).get(today, [])
    planned = []
    if d["failures"] and not args.no_rerun:
        todo = [f["name"] for f in d["failures"] if f["name"] not in rerun_done][:8]
        if todo:
            r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "auto_mas_rerun.py"),
                                "--accounts", ",".join(todo), "--apply", "--force"],
                               capture_output=True, timeout=1200)
            out = (r.stdout + r.stderr).decode("utf-8", "replace")
            planned = todo if "发车" in out else []
            log_event("rerun", accounts=todo, ok=bool(planned), detail=out[-400:])

    report = build_report(d, rerun_done, planned)
    os.makedirs(REPORTS, exist_ok=True)
    path = os.path.join(REPORTS, "%s_%s.md" % (today, shift or "manual"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    with open(os.path.join(REPORTS, "latest.md"), "w", encoding="utf-8") as f:
        f.write(report)

    # 只有"正式出报告"才占用本班名额；--force 调试不占，否则真报告会被跳过
    if shift and not args.force:
        st.setdefault("reported", {}).setdefault(today, []).append(shift)
    if planned:
        st.setdefault("rerun", {}).setdefault(today, []).extend(planned)
    save_state(st)
    log_event("report", shift=shift, failures=len(d["failures"]), never_ran=len(d["never_ran"]),
              rerun=planned, path=path)

    # 通知：平时静默；有异常才推（Discord 优先，其次本机气泡）
    abnormal = bool(d["failures"] or d["never_ran"])
    if abnormal and not args.quiet:
        msg = "失败 %d、未跑 %d" % (len(d["failures"]), len(d["never_ran"]))
        if planned:
            msg += "；已自动补跑 %d 个" % len(planned)
        if not notify_discord("MAAOrch 日报 · %s · 有异常" % (shift or "manual"), msg, path):
            notify("MAAOrch 日报（有异常）", msg)
    elif local_conf().get("notify_on") == "always" and not args.quiet:
        notify_discord("MAAOrch 日报 · %s · 全绿" % (shift or "manual"),
                       "全部成功：账号 %d 个" % len(d["accounts"]), path)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
