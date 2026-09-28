#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""回滚演练 · 定期巡检入口（周跑 + 手动；2026-09-28，kanban t_7fa96ea5）。

一句话：把「回滚演练矩阵」（`tmp/hermes_bridge/rollback_drill.py`，卡 t_a90b0f8b / t_6fa92a0a）
从「人工复跑」变成「每周自动跑一次 + FAIL 自动告警」。本工具**不改演练本体**，只做外围三闸 + 归档 + 判据 + 告警。

判据（猫腻全在这里）：演练末行 `ROLLBACK_DRILL=PASS|FAIL`（并交叉核对退出码，矛盾按 FAIL 处理）。

三闸（任一不过 = SKIP，不产生 FAIL 噪音；`--force` 只跳过窗口闸）：
  ① 窗口闸：班次窗口 05:00–08:00 / 19:30–23:00 内不跑（演练要 SSH + 短暂占用配置快照锁）。
  ② 单飞锁：`runtime/mas_rollback_drill.lock`（pid 判活自愈，语义同 mas_config_snapshot：死锁自动清、活锁绝不抢）；
     另查 `runtime/mas_config_snapshot.lock`：有活主（快照/回滚在跑）⇒ 本轮跳过（E 组要拿同一把锁，别互相堵）。
  ③ 主机可达：SSH 不通 ⇒ 跳过（不告警——主机可达性归口 host_watchdog，本工具不该重复报「主机没了」）。

产物（全部真实落盘，路径可复核）：
  - 演练 stdout 全文归档：`docs/audits/<YYYY-MM-DD>_rollback_drill_auto.txt`（保留最近 --keep 份）
  - 演练 JSON 证据：      `docs/audits/<YYYY-MM-DD>_rollback_drill_auto.json`
                          （子集运行 `--only` 走 `…_auto_only_<组>.txt/json`，不覆盖周跑的全量证据）
  - FAIL 告警 payload：   `runtime/reports/rollback_drill_alert_<YYYYMMDD>.json`（schema maaorch.rollback_drill_alert/1）
  - FAIL 告警流水：       `runtime/rollback_drill_alerts.jsonl`
  - FAIL 看板只读卡：     活跃看板，`idempotency_key=rollback-drill:<date>`、无 assignee、status=done（人来处置）
  - 每轮运行流水：        `runtime/rollback_drill_cron_log.jsonl`（含 SKIP 轮）

stdout 末行（cron 侧看这一行）：`ROLLBACK_DRILL_AUTO=PASS|FAIL[|SKIP:<原因>]`
退出码：0=通过/跳过；4=演练 FAIL（含演练脚本缺失）；3=本工具自身异常。

用法：
  python tools/mas_rollback_drill.py                 # 全量（周跑入口；等价 cron）
  python tools/mas_rollback_drill.py --only A,H      # 子集（调试；不覆盖全量证据文件）
  python tools/mas_rollback_drill.py --force         # 忽略窗口闸（应急手跑）
  python tools/mas_rollback_drill.py --alert-send    # FAIL 时真发（watchdog.ini；**需用户批准**，默认 dry-run）
  python tools/mas_rollback_drill.py --no-card       # FAIL 时不落看板卡
  python tools/mas_rollback_drill.py --selftest      # 离线自检（不连主机、不跑演练）

纪律（与演练本体一致，见 docs/ROLLBACK_DRILL.md §4）：只碰临时副本；不重启侧车；不动模拟器；
不写生产文件；凭据不出主机；班次窗口内不跑。

环境变量：MAAORCH_SNAPSHOT_PY（覆盖 PY312 解释器）、MAAORCH_ROLLBACK_DRILL_ALERT_SEND=1（等同 --alert-send）、
MAAORCH_ROLLBACK_DRILL_NO_CARD=1、MAAORCH_ROLLBACK_DRILL_FORCE=1。
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import time

ROOT = r"D:\1-2\MAAOrch"
TOOLS = os.path.join(ROOT, "tools")
RUNTIME = os.path.join(ROOT, "runtime")
AUDITS = os.path.join(ROOT, "docs", "audits")
DRILL = os.path.join(ROOT, "tmp", "hermes_bridge", "rollback_drill.py")
PY312 = r"C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe"
LOCK = os.path.join(RUNTIME, "mas_rollback_drill.lock")
SNAP_LOCK = os.path.join(RUNTIME, "mas_config_snapshot.lock")
RUNLOG = os.path.join(RUNTIME, "rollback_drill_cron_log.jsonl")
ALERT_LOG = os.path.join(RUNTIME, "rollback_drill_alerts.jsonl")
REPORTS = os.path.join(RUNTIME, "reports")
KEEP_EVIDENCE = 10                 # 归档保留最近 N 份（周跑 ⇒ ≈ 10 周）
DRILL_TIMEOUT_S = 1500             # 演练全量实测 ~2.5 分钟；给足 SSH 抖动余量
WINDOWS = (((5, 0), (8, 0)), ((19, 30), (23, 0)))   # 班次窗口（禁跑）

sys.path.insert(0, TOOLS)


def emit(line: str) -> None:
    print(line, flush=True)


def now_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def date_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 三闸

def window_block(now: dt.datetime | None = None) -> str | None:
    """班次窗口内 ⇒ 返回原因；否则 None。"""
    t = now or dt.datetime.now()
    hm = (t.hour, t.minute)
    for (lo, hi) in WINDOWS:
        if lo <= hm < hi:
            return "班次窗口 %02d:%02d–%02d:%02d" % (lo[0], lo[1], hi[0], hi[1])
    return None


def _snapshot_mod():
    import mas_config_snapshot  # noqa: PLC0415
    return mas_config_snapshot


def lock_owner(path: str) -> tuple[int | None, bool | None]:
    """(pid, alive)；读不到 pid ⇒ (None, None)。判活失败 ⇒ (pid, None)。"""
    m = _snapshot_mod()
    try:
        pid = m.read_lock_pid(path)
    except Exception:  # noqa: BLE001
        return None, None
    if pid is None:
        return None, None
    try:
        return pid, m.pid_alive(pid)
    except Exception:  # noqa: BLE001
        return pid, None


def snapshot_busy() -> str | None:
    """生产快照/回滚在跑（活主）⇒ 返回原因；死锁会被对方自愈 ⇒ 不拦。"""
    if not os.path.exists(SNAP_LOCK):
        return None
    pid, alive = lock_owner(SNAP_LOCK)
    if alive is False:
        return None
    return "SNAPSHOT_BUSY(pid=%s)" % ("?" if pid is None else pid)


class Busy(Exception):
    pass


@contextlib.contextmanager
def drill_lock(enabled: bool = True):
    """单飞锁：死锁（pid 已死）自动清重试；活锁/判不了活 ⇒ Busy（绝不抢）。"""
    if not enabled:
        yield
        return
    os.makedirs(RUNTIME, exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            pid, alive = lock_owner(LOCK)
            if alive is False:
                with contextlib.suppress(OSError):
                    os.remove(LOCK)
                    emit("锁自愈：残留锁 pid=%s 已死，清理后重试" % pid)
                continue
            raise Busy("LOCK_HELD(pid=%s)" % ("?" if pid is None else pid))
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(LOCK)


def host_reachable(timeout: int = 60) -> bool:
    try:
        import mas_patch_guard as G  # noqa: PLC0415
        out = G.ssh_ps("Write-Output 'HOST_PING_OK'", timeout=timeout)
    except Exception as e:  # noqa: BLE001
        emit("HOST 探测异常：%s: %s" % (type(e).__name__, str(e)[:160]))
        return False
    return "HOST_PING_OK" in out


# ---------------------------------------------------------------- 演练执行 / 判据

def drill_python() -> str:
    return os.environ.get("MAAORCH_SNAPSHOT_PY") or PY312


def evidence_paths(day: str, only: str | None) -> tuple[str, str]:
    tag = ("_auto_only_" + re.sub(r"[^A-Za-z]", "", only)) if only else "_auto"
    return (os.path.join(AUDITS, "%s_rollback_drill%s.txt" % (day, tag)),
            os.path.join(AUDITS, "%s_rollback_drill%s.json" % (day, tag)))


def run_drill(txt: str, js: str, only: str | None, timeout_s: int) -> dict:
    """真跑演练；stdout 全文落 txt，演练自带 JSON 证据落 js。"""
    if not os.path.exists(DRILL):
        return {"rc": None, "out": "", "seconds": 0.0, "status": "NO_SCRIPT",
                "error": "演练脚本不存在：%s（docs/ROLLBACK_DRILL.md §6 复跑方式）" % DRILL}
    cmd = [drill_python(), DRILL, "--json-out", js]
    if only:
        cmd += ["--only", only]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"      # 演练输出含中文 ⇒ 固定编码，避免管道下 cp936 乱码
    env["PYTHONUTF8"] = "1"
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=timeout_s)
        out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
        return {"rc": p.returncode, "out": out, "seconds": round(time.monotonic() - t0, 1), "status": "RAN"}
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"") + (e.stderr or b"")).decode("utf-8", "replace")
        return {"rc": None, "out": out, "seconds": round(time.monotonic() - t0, 1),
                "status": "TIMEOUT", "error": "演练超时 >%ds（进程已终止）" % timeout_s}


def archive_evidence(txt: str, out: str, banner: str = "") -> None:
    """把演练 stdout 全文归档（TIMEOUT 等异常态也归档——现场证据比"干净"重要）。"""
    os.makedirs(AUDITS, exist_ok=True)
    with open(txt, "w", encoding="utf-8") as fh:
        if banner:
            fh.write(banner.rstrip() + "\n")
        fh.write(out if (not out or out.endswith("\n")) else out + "\n")


def rotate_evidence(keep: int = KEEP_EVIDENCE) -> list:
    """保留最近 keep 份**全量**自动证据（子集运行 `_auto_only_*` 不参与轮转、不删）。"""
    removed = []
    for ext in ("txt", "json"):
        files = [f for f in sorted(glob.glob(os.path.join(AUDITS, "*_rollback_drill_auto.%s" % ext)))
                 if "_auto_only_" not in os.path.basename(f)]
        for f in (files[:-keep] if keep > 0 else files):
            with contextlib.suppress(OSError):
                os.remove(f)
                removed.append(f)
    return removed


def parse_drill(out: str) -> dict:
    """从演练 stdout 提取：末行判据 / 失败步骤 / 合计（JSON 证据优先，缺失时退回文本解析）。"""
    verdict = None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("ROLLBACK_DRILL="):
            verdict = s.split("=", 1)[1].strip()
    fails = [l.strip()[5:].strip() for l in out.splitlines() if l.startswith("FAIL ")]
    ok_n = total = None
    m = re.search(r"==\s*合计\s*(\d+)/(\d+)\s*PASS", out)
    if m:
        ok_n, total = int(m.group(1)), int(m.group(2))
    return {"verdict": verdict, "fail_steps": fails, "ok_count": ok_n, "total": total}


def read_drill_json(js: str) -> dict | None:
    try:
        with open(js, encoding="utf-8") as fh:
            ev = json.load(fh)
    except (OSError, ValueError):
        return None
    steps = [s for s in (ev.get("steps") or []) if "ok" in s]
    ev["fail_steps"] = ["%s %s" % (s.get("step"), s.get("detail", "")) for s in steps if not s.get("ok")]
    ev["ok_count"] = sum(1 for s in steps if s.get("ok"))
    ev["total"] = len(steps)
    return ev


def judge(res: dict, ev: dict | None) -> dict:
    """判据合成（fail-closed）：末行 + 退出码 + JSON 计数三者取最严。"""
    p = parse_drill(res["out"])
    verdict, fails = p["verdict"], p["fail_steps"]
    ok_n, total = p["ok_count"], p["total"]
    if ev:
        fails = ev["fail_steps"] or fails
        ok_n, total = ev["ok_count"], ev["total"]
    if res["status"] == "NO_SCRIPT":
        return {"outcome": "FAIL", "reason": "NO_SCRIPT", "ok_count": ok_n, "total": total,
                "fail_steps": fails or [res.get("error", "")]}
    if res["status"] == "TIMEOUT":
        return {"outcome": "FAIL", "reason": "TIMEOUT", "ok_count": ok_n, "total": total,
                "fail_steps": fails or [res.get("error", "")]}
    if verdict not in ("PASS", "FAIL"):
        return {"outcome": "FAIL", "reason": "NO_VERDICT", "ok_count": ok_n, "total": total,
                "fail_steps": fails or ["演练未打印 ROLLBACK_DRILL= 末行（见证据 txt 尾部）"]}
    if verdict == "PASS" and res["rc"] != 0:
        return {"outcome": "FAIL", "reason": "RC_MISMATCH(rc=%s)" % res["rc"], "ok_count": ok_n,
                "total": total, "fail_steps": fails or ["末行判 PASS 但退出码非 0"]}
    if verdict == "PASS" and total is not None and ok_n != total:
        return {"outcome": "FAIL", "reason": "COUNT_MISMATCH(%s/%s)" % (ok_n, total), "ok_count": ok_n,
                "total": total, "fail_steps": fails or ["末行判 PASS 但计数不符"]}
    return {"outcome": verdict, "reason": None, "ok_count": ok_n, "total": total, "fail_steps": fails}


# ---------------------------------------------------------------- 告警

def build_alert(day: str, j: dict, res: dict, txt: str, js: str) -> dict:
    n_fail = len(j["fail_steps"])
    title = "[MAAOrch] 回滚演练 FAIL · %s" % day
    summary = ("%s\n步骤 %s/%s PASS，耗时 %ss，判据原因=%s\n失败步骤（%d）：\n%s\n证据：%s\n"
               "定因提示：先分辨「真回归」还是「基线已变」（补丁面/manifest/快照 ts 变了要在 §3/§7 同步）；"
               "处置见 docs/ROLLBACK_DRILL.md §4/§5。" % (
                   title, j["ok_count"], j["total"], res["seconds"], j["reason"] or "-", n_fail,
                   "\n".join("  - " + s[:300] for s in j["fail_steps"][:12]) or "  - （无步骤明细，见证据尾部）",
                   "docs/audits/" + os.path.basename(txt)))
    return {"schema": "maaorch.rollback_drill_alert/1", "generated_at": now_str(), "date": day,
            "outcome": "FAIL", "reason": j["reason"], "ok_count": j["ok_count"], "total": j["total"],
            "fail_n": n_fail, "fail_steps": j["fail_steps"][:20], "seconds": res["seconds"],
            "drill_rc": res["rc"], "send": False, "channel": None,
            "title": title, "summary": summary,
            "evidence": {"txt": txt, "json": js},
            "runbook": "docs/ROLLBACK_DRILL.md §4 回滚卡片 / §5 残留与后续"}


def write_alert_files(payload: dict) -> dict:
    """落 FAIL 告警 payload + 流水；返回 {"payload": path, "log": path|None}。"""
    os.makedirs(REPORTS, exist_ok=True)
    path = os.path.join(REPORTS, "rollback_drill_alert_%s.json" % payload["date"].replace("-", ""))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    logp = None
    try:
        with open(ALERT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": payload["generated_at"], "date": payload["date"],
                                 "outcome": payload["outcome"], "reason": payload["reason"],
                                 "ok": payload["ok_count"], "total": payload["total"],
                                 "fail_n": payload["fail_n"], "send": payload["send"],
                                 "title": payload["title"]}, ensure_ascii=False) + "\n")
        logp = ALERT_LOG
    except OSError:
        pass
    return {"payload": path, "log": logp}


def alert_send(payload: dict) -> dict:
    """真发（--alert-send）：复用 host_watchdog 通知配置（runtime/watchdog.ini，与 AUTO-MAS 通知同源）。"""
    try:
        import host_watchdog  # noqa: PLC0415
        c = host_watchdog.load_conf()
        host_watchdog.notify(c, payload["title"], payload["summary"], send=True)
        return {"ok": True, "channel": c["notify"].get("channel") or "email"}
    except SystemExit as e:  # watchdog.ini 缺失时 load_conf 会 sys.exit
        return {"ok": False, "error": "watchdog.ini 缺失: %s" % e}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


def card_key(day: str) -> str:
    return "rollback-drill:%s" % day


def card_upsert(day: str, payload: dict) -> dict:
    """看板只读卡（status=done、无 assignee ⇒ 不参与派工；人来处置）。复用班次日报的 upsert。"""
    try:
        import mas_shift_report  # noqa: PLC0415
        body = ("**回滚演练定期巡检 FAIL（自动）**\n\n%s\n\n"
                "- 判据：末行 `ROLLBACK_DRILL=FAIL`（步骤 %s/%s PASS）\n"
                "- 证据：`%s`\n- 处置卡片：`docs/ROLLBACK_DRILL.md` §4（回滚卡片）/ §5（残留与后续）\n"
                "- 本卡由 `tools/mas_rollback_drill.py` 自动生成（周跑，cron `mas-rollback-drill`）；只读、无 assignee。" % (
                    payload["summary"], payload["ok_count"], payload["total"],
                    "docs/audits/" + os.path.basename(payload["evidence"]["txt"])))
        return mas_shift_report.board_upsert(
            payload["title"], body,
            "回滚演练 FAIL：%s/%s PASS（原因=%s）— 需人工定因：真回归 or 基线已变" % (
                payload["ok_count"], payload["total"], payload["reason"] or "-"),
            card_key(day))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


def log_run(rec: dict) -> None:
    os.makedirs(RUNTIME, exist_ok=True)
    with contextlib.suppress(OSError):
        with open(RUNLOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 主流程

def run_once(only: str | None = None, force: bool = False, alert_send_flag: bool = False,
             no_card: bool = False, keep: int = KEEP_EVIDENCE, timeout_s: int = DRILL_TIMEOUT_S,
             skip_host_check: bool = False) -> tuple[int, str]:
    """返回 (退出码, 末行判据)。所有输出走 emit()。"""
    day = date_str()
    emit("== 回滚演练 · 定期巡检入口 %s ==（来源卡 t_7fa96ea5；演练本体 %s）" % (now_str(), DRILL))
    base = {"ts": now_str(), "date": day, "only": only, "force": bool(force)}

    def bail(reason: str, code: int = 0, extra: dict | None = None) -> tuple[int, str]:
        rec = dict(base); rec.update({"outcome": "SKIP", "reason": reason})
        rec.update(extra or {})
        log_run(rec)
        line = "ROLLBACK_DRILL_AUTO=SKIP:%s" % reason
        emit(line)
        return code, line

    reason = None if force else window_block()
    if reason:
        emit("窗口闸：跳过（%s；演练要 SSH + 短暂占用配置快照锁）。--force 可强制。" % reason)
        return bail("WINDOW")

    busy = snapshot_busy()
    if busy:
        emit("快照闸：跳过（%s——E 组要拿同一把锁，避免互相堵）。" % busy)
        return bail(busy.split("(")[0])

    if not skip_host_check and not host_reachable():
        emit("主机闸：跳过（SSH 不可达；主机可达性归口 host_watchdog，本轮不告警）。")
        return bail("HOST_UNREACHABLE")

    txt, js = evidence_paths(day, only)
    os.makedirs(AUDITS, exist_ok=True)   # 必须先建目录：演练自己会往 --json-out 写证据（不存在 ⇒ 演练末尾崩）
    try:
        with drill_lock():
            emit("单飞锁：acquired（%s）" % LOCK)
            res = run_drill(txt, js, only, timeout_s)
            ev = read_drill_json(js) if res["status"] == "RAN" else None
            j = judge(res, ev)
            banner = ""
            if res["status"] == "TIMEOUT":
                banner = "!! 演练超时打断（>%ds）——下面是已产出的部分 stdout !!" % timeout_s
                cleanup_remote_scratch(res["out"])
            elif res["status"] == "NO_SCRIPT":
                banner = "!! 演练脚本不存在：%s !!" % DRILL
            archive_evidence(txt, res["out"], banner)
            emit("归档：%s%s" % (txt, "" if only is None else "（子集运行，独立命名、不参与轮转）"))
            if only is None:
                for f in rotate_evidence(keep):
                    emit("归档轮转：删除 %s" % f)
    except Busy as e:
        emit("单飞锁：跳过（%s——上一轮还在跑）。" % e)
        return bail("LOCK_HELD")

    emit(short_report(res["out"], j))
    emit("DRILL_STEPS=%s/%s  DRILL_SECONDS=%s  DRILL_RC=%s" % (
        j["ok_count"], j["total"], res["seconds"], res["rc"]))
    emit("EVIDENCE=%s" % ("docs/audits/" + os.path.basename(txt)))

    rec = dict(base)
    rec.update({"outcome": j["outcome"], "reason": j["reason"], "ok": j["ok_count"],
                "total": j["total"], "seconds": res["seconds"], "rc": res["rc"],
                "evidence": txt})

    if j["outcome"] == "FAIL":
        payload = build_alert(day, j, res, txt, js)
        paths = write_alert_files(payload)
        emit("DRILL_FAIL_REASON=%s" % (j["reason"] or "-"))
        emit("DRILL_FAIL_STEPS=" + ("；".join(s[:120] for s in j["fail_steps"][:6]) or "（见证据尾部）"))
        emit("告警 payload=%s%s" % ("runtime/reports/" + os.path.basename(paths["payload"]),
                                    "；流水=runtime/" + os.path.basename(paths["log"]) if paths["log"] else ""))
        want_send = alert_send_flag or os.environ.get("MAAORCH_ROLLBACK_DRILL_ALERT_SEND") == "1"
        if want_send:
            sent = alert_send(payload)
            payload["send"] = bool(sent.get("ok"))
            payload["channel"] = sent.get("channel")
            with open(paths["payload"], "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            rec["send"] = payload["send"]
            emit("ALERT=%s(%s)" % ("sent" if sent.get("ok") else "send-failed",
                                   sent.get("channel") or sent.get("error")))
        else:
            emit("ALERT=dry-run（未发外部通知；真发需批准：--alert-send 或 MAAORCH_ROLLBACK_DRILL_ALERT_SEND=1）")
        if not no_card and os.environ.get("MAAORCH_ROLLBACK_DRILL_NO_CARD") != "1":
            c = card_upsert(day, payload)
            rec["card"] = c.get("id") if c.get("ok") else None
            emit("CARD=%s" % (("看板卡 %s（%s）" % (c.get("id"), c.get("how"))) if c.get("ok")
                              else "未落卡：%s" % c.get("error")))
    log_run(rec)
    line = "ROLLBACK_DRILL_AUTO=%s" % j["outcome"]
    emit(line)
    return (0 if j["outcome"] == "PASS" else 4), line


def short_report(out: str, j: dict) -> str:
    """演练 stdout 太长 ⇒ 只留判据相关行（PASS 留合计行；FAIL 留失败步骤 + 合计行）。"""
    lines = []
    for l in out.splitlines():
        if l.startswith("FAIL ") or l.startswith("== "):
            lines.append(l.rstrip())
    if not lines:
        lines = [l.rstrip() for l in out.splitlines()[-5:]]
    if j["outcome"] == "PASS":
        lines = [l for l in lines if l.startswith("== ")] or lines[-1:]
    return "演练摘要：\n" + "\n".join(lines[:40])


def cleanup_remote_scratch(out: str) -> None:
    """演练被超时打断 ⇒ 清它自己的主机 scratch（ts 取自 stdout 头行；严格白名单格式）。"""
    m = re.search(r"回滚演练矩阵\s+(\d{8}-\d{6})", out)
    if not m:
        emit("超时兜底：未能从 stdout 取到演练 ts，主机 scratch 需人工复核（E:\\AUTO-MAS\\_rbdrill*）")
        return
    ts = m.group(1)
    if not re.fullmatch(r"\d{8}-\d{6}", ts):
        return
    ps = ("foreach ($p in @('E:\\AUTO-MAS\\_rbdrill_%s','E:\\AUTO-MAS\\_rbdrill_sc_%s')) { "
          "if (Test-Path $p) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue } }; "
          "Write-Output ('RB_CLEAN=' + (-not (Test-Path 'E:\\AUTO-MAS\\_rbdrill_%s')) + '/' + "
          "(-not (Test-Path 'E:\\AUTO-MAS\\_rbdrill_sc_%s')))" % (ts, ts, ts, ts))
    try:
        import mas_patch_guard as G  # noqa: PLC0415
        emit("超时兜底：主机 scratch 清理 → %s" % G.ssh_ps(ps, timeout=120).strip()[:120])
    except Exception as e:  # noqa: BLE001
        emit("超时兜底：主机 scratch 清理失败（%s: %s）——请人工复核 E:\\AUTO-MAS\\_rbdrill*" % (
            type(e).__name__, str(e)[:120]))


# ---------------------------------------------------------------- 自检（离线）

def _fake_drill(dir_: str, body: str) -> str:
    p = os.path.join(dir_, "fake_drill.py")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(body)
    return p


def selftest() -> int:
    """离线自检（25 例）。会临时改写模块级路径常量（演练脚本/锁/归档目录指向临时目录），
    结束前原样恢复 —— 因此可在 pytest / 回归套件里**进程内**调用（见 tools/tests/test_regression.py F1）。"""
    global DRILL, LOCK, SNAP_LOCK, AUDITS, RUNLOG, ALERT_LOG, REPORTS, ROOT, TOOLS, RUNTIME, emit
    import tempfile
    _saved = {k: globals()[k] for k in ("DRILL", "LOCK", "SNAP_LOCK", "AUDITS", "RUNLOG",
                                        "ALERT_LOG", "REPORTS", "ROOT", "TOOLS", "RUNTIME", "emit")}
    checks, fails = [], []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))
        if not ok:
            fails.append(name)
        print("%s %-56s %s" % ("PASS" if ok else "FAIL", name, detail[:110]))

    # 1) 窗口闸
    cases = [((4, 59), True), ((5, 0), False), ((7, 59), False), ((8, 0), True), ((12, 0), True),
             ((19, 29), True), ((19, 30), False), ((22, 59), False), ((23, 0), True)]
    bad = [("%02d:%02d" % hm) for hm, want_ok in cases
           if (window_block(dt.datetime(2026, 9, 28, hm[0], hm[1])) is None) != want_ok]
    check("窗口闸边界（05:00–08:00 / 19:30–23:00）", not bad, "异常：%s" % bad if bad else "9 例")

    # 2) 判据解析 + 合成
    sample = ("PASS A0 ok\nFAIL B2 闸门未翻转\n== 合计 1/2 PASS，总耗时 3.0s ==\nROLLBACK_DRILL=FAIL\n")
    p = parse_drill(sample)
    check("parse：末行/失败步骤/合计", p["verdict"] == "FAIL" and p["fail_steps"] == ["B2 闸门未翻转"]
          and (p["ok_count"], p["total"]) == (1, 2), json.dumps(p, ensure_ascii=False)[:110])
    jd = judge({"rc": 0, "out": sample, "seconds": 1.0, "status": "RAN"}, None)
    check("judge：FAIL 末行 ⇒ FAIL", jd["outcome"] == "FAIL" and len(jd["fail_steps"]) == 1, jd["reason"] or "-")
    jd = judge({"rc": 0, "out": "PASS A\nROLLBACK_DRILL=PASS\n", "seconds": 1.0, "status": "RAN"},
               {"ok_count": 1, "total": 1, "fail_steps": []})
    check("judge：PASS 一致 ⇒ PASS", jd["outcome"] == "PASS", jd["reason"] or "-")
    jd = judge({"rc": 4, "out": "PASS A\nROLLBACK_DRILL=PASS\n", "seconds": 1.0, "status": "RAN"}, None)
    check("judge：末行 PASS 但 rc=4 ⇒ FAIL（矛盾从 FAIL）", jd["outcome"] == "FAIL" and "RC_MISMATCH" in jd["reason"], jd["reason"])
    jd = judge({"rc": 0, "out": "PASS A\n", "seconds": 1.0, "status": "RAN"}, None)
    check("judge：无末行 ⇒ FAIL:NO_VERDICT（fail-closed）", jd["outcome"] == "FAIL" and jd["reason"] == "NO_VERDICT", jd["reason"])
    jd = judge({"rc": None, "out": "", "seconds": 9.0, "status": "TIMEOUT"}, None)
    check("judge：超时 ⇒ FAIL:TIMEOUT", jd["outcome"] == "FAIL" and jd["reason"] == "TIMEOUT", jd["reason"])

    # 3) 锁：单飞 / 死锁自愈 / 活锁不抢
    with tempfile.TemporaryDirectory() as td:
        LOCK = os.path.join(td, "drill.lock")
        with drill_lock():
            check("锁：首轮 acquired", os.path.exists(LOCK), LOCK)
            try:
                with drill_lock():
                    check("锁：二轮被拒（单飞）", False)
            except Busy as e:
                check("锁：二轮被拒（单飞）", "LOCK_HELD" in str(e), str(e))
        check("锁：退出即释放", not os.path.exists(LOCK))
        with open(LOCK, "w") as fh:
            fh.write("999999")
        with drill_lock():
            check("锁：死锁自愈（pid 999999 不存在 ⇒ 清理重试）", True)
        with open(LOCK, "w") as fh:
            fh.write(str(os.getpid()))
        try:
            with drill_lock():
                check("锁：活锁不抢（本进程 pid 活着 ⇒ Busy）", False)
        except Busy as e:
            check("锁：活锁不抢（本进程 pid 活着 ⇒ Busy）", "LOCK_HELD" in str(e), str(e))
        with open(LOCK, "w") as fh:
            fh.write("not-a-pid")
        try:
            with drill_lock():
                check("锁：判不了活 ⇒ 拒绝（不赌）", False)
        except Busy:
            check("锁：判不了活 ⇒ 拒绝（不赌）", True)
        os.remove(LOCK)

    # 4) 快照闸
    with tempfile.TemporaryDirectory() as td:
        SNAP_LOCK = os.path.join(td, "snap.lock")
        check("快照闸：无锁 ⇒ 放行", snapshot_busy() is None)
        with open(SNAP_LOCK, "w") as fh:
            fh.write(str(os.getpid()))
        check("快照闸：活主 ⇒ 跳过", "SNAPSHOT_BUSY" in (snapshot_busy() or ""), snapshot_busy() or "")
        with open(SNAP_LOCK, "w") as fh:
            fh.write("999999")
        check("快照闸：死锁 ⇒ 放行（对方会自愈）", snapshot_busy() is None)
        os.remove(SNAP_LOCK)

    # 5) 证据命名 / 轮转
    check("证据命名：全量 / 子集", os.path.basename(evidence_paths("2026-09-28", None)[0]) == "2026-09-28_rollback_drill_auto.txt"
          and os.path.basename(evidence_paths("2026-09-28", "A,H")[0]) == "2026-09-28_rollback_drill_auto_only_AH.txt",
          os.path.basename(evidence_paths("2026-09-28", "A,H")[0]))
    with tempfile.TemporaryDirectory() as td:
        AUDITS = td
        for i in range(1, 14):
            with open(os.path.join(td, "2026-01-%02d_rollback_drill_auto.txt" % i), "w") as fh:
                fh.write("x")
            with open(os.path.join(td, "2026-01-%02d_rollback_drill_auto.json" % i), "w") as fh:
                fh.write("{}")
        rm = rotate_evidence(10)
        left = sorted(os.listdir(td))
        check("轮转：13 对 → 保留最近 10 对", len(rm) == 6 and len(left) == 20
              and "2026-01-01_rollback_drill_auto.txt" not in left
              and "2026-01-13_rollback_drill_auto.txt" in left, "删 %d / 剩 %d" % (len(rm), len(left)))

    # 6) 端到端（假演练，不连主机）
    with tempfile.TemporaryDirectory() as td:
        ROOT, TOOLS = td, os.path.join(td, "tools")
        os.makedirs(TOOLS)
        AUDITS = os.path.join(td, "audits")
        RUNTIME = os.path.join(td, "runtime")
        RUNLOG = os.path.join(RUNTIME, "rollback_drill_cron_log.jsonl")
        ALERT_LOG = os.path.join(RUNTIME, "rollback_drill_alerts.jsonl")
        REPORTS = os.path.join(RUNTIME, "reports")
        LOCK = os.path.join(RUNTIME, "d.lock")
        SNAP_LOCK = os.path.join(RUNTIME, "s.lock")
        DRILL = _fake_drill(td, "import json,sys,argparse\n"
                                "ap=argparse.ArgumentParser(); ap.add_argument('--json-out'); ap.add_argument('--only')\n"
                                "a=ap.parse_args()\n"
                                "print('PASS A0 fake')\nprint('== 合计 1/1 PASS，总耗时 0.1s ==')\n"
                                "print('ROLLBACK_DRILL=PASS')\n"
                                "json.dump({'steps': [{'step':'A0','ok':True,'detail':'fake'}]}, open(a.json_out,'w'))\n")
        captured = []
        emit = (lambda line, _c=captured: _c.append(line))
        code, line = run_once(force=True, skip_host_check=True, no_card=True)
        ok_e2e = (code == 0 and line == "ROLLBACK_DRILL_AUTO=PASS"
                  and os.path.exists(os.path.join(AUDITS, "%s_rollback_drill_auto.txt" % date_str())))
        check("端到端（假演练 PASS）：退出码/末行/归档", ok_e2e, "code=%s %s" % (code, line))
        log = os.path.join(RUNTIME, "rollback_drill_cron_log.jsonl")
        check("端到端：运行流水 1 行", os.path.exists(log) and len(open(log, encoding="utf-8").read().strip().splitlines()) == 1)

        # 6b) 子集运行：独立命名、不动全量证据
        captured[:] = []
        code, line = run_once(only="H", force=True, skip_host_check=True, no_card=True)
        sub_txt, _ = evidence_paths(date_str(), "H")
        full_txt, _ = evidence_paths(date_str(), None)
        check("端到端（子集 --only H）：独立命名 + 全量证据仍在",
              code == 0 and os.path.exists(sub_txt) and os.path.exists(full_txt)
              and "不参与轮转" in "".join(captured), os.path.basename(sub_txt))

        # 6c) 假演练 FAIL ⇒ 告警三件套
        DRILL = _fake_drill(td, "import json,argparse,sys\n"
                                "ap=argparse.ArgumentParser(); ap.add_argument('--json-out'); ap.add_argument('--only')\n"
                                "a=ap.parse_args()\n"
                                "print('PASS A0 fake')\nprint('FAIL B2 闸门未翻转')\n"
                                "print('== 合计 1/2 PASS，总耗时 0.1s ==')\nprint('ROLLBACK_DRILL=FAIL')\n"
                                "sys.exit(4)\n")
        captured[:] = []
        code, line = run_once(force=True, skip_host_check=True, no_card=True)
        payload_p = os.path.join(REPORTS, "rollback_drill_alert_%s.json" % date_str().replace("-", ""))
        alerted = (code == 4 and line == "ROLLBACK_DRILL_AUTO=FAIL"
                   and os.path.exists(payload_p)
                   and any("ALERT=dry-run" in l for l in captured)
                   and "B2" in open(payload_p, encoding="utf-8").read()
                   and os.path.exists(ALERT_LOG)
                   and len(open(ALERT_LOG, encoding="utf-8").read().strip().splitlines()) == 1)
        check("端到端（假演练 FAIL）：rc=4 / payload / 流水 / dry-run 不真发", alerted, line)

        # 6d) 演练脚本缺失 ⇒ FAIL:NO_SCRIPT（不是静默跳过）
        DRILL = os.path.join(td, "nope.py")
        captured[:] = []
        code, line = run_once(force=True, skip_host_check=True, no_card=True)
        check("端到端：演练脚本缺失 ⇒ FAIL（不静默）",
              code == 4 and line == "ROLLBACK_DRILL_AUTO=FAIL"
              and any("DRILL_FAIL_REASON=NO_SCRIPT" in l for l in captured), line)

        # 6e) 窗口闸（未 --force）
        captured[:] = []
        saved = globals()["window_block"]
        globals()["window_block"] = lambda now=None: "班次窗口 05:00–08:00"
        code, line = run_once(skip_host_check=True, no_card=True)
        globals()["window_block"] = saved
        check("端到端：窗口内 SKIP:WINDOW（rc=0，不告警）", code == 0 and line == "ROLLBACK_DRILL_AUTO=SKIP:WINDOW", line)

        # 6f) 单飞锁在跑 ⇒ SKIP:LOCK_HELD
        captured[:] = []
        with open(LOCK, "w") as fh:
            fh.write(str(os.getpid()))
        code, line = run_once(force=True, skip_host_check=True, no_card=True)
        os.remove(LOCK)
        check("端到端：单飞锁被活主占 ⇒ SKIP:LOCK_HELD", code == 0 and line == "ROLLBACK_DRILL_AUTO=SKIP:LOCK_HELD", line)

    globals().update(_saved)          # 恢复模块常量（进程内调用安全）
    print("\n== selftest %d/%d OK ==" % (len(checks) - len(fails), len(checks)))
    if fails:
        print("失败：%s" % "、".join(fails))
    return 0 if not fails else 3


def main() -> int:
    with contextlib.suppress(Exception):
        getattr(sys.stdout, "reconfigure")(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="回滚演练 · 定期巡检入口（周跑；判据末行 ROLLBACK_DRILL_AUTO=）")
    ap.add_argument("--only", default=None, help="演练子集 A/B/C/D/E/F/G/H 逗号分隔（调试；不覆盖全量证据）")
    ap.add_argument("--force", action="store_true", help="忽略窗口闸（应急手跑；纪律优先）")
    ap.add_argument("--alert-send", action="store_true", help="FAIL 时真发通知（watchdog.ini；需用户批准）")
    ap.add_argument("--no-card", action="store_true", help="FAIL 时不落看板只读卡")
    ap.add_argument("--keep", type=int, default=KEEP_EVIDENCE, help="归档保留份数（默认 %d）" % KEEP_EVIDENCE)
    ap.add_argument("--timeout", type=int, default=DRILL_TIMEOUT_S, help="演练超时秒（默认 %d）" % DRILL_TIMEOUT_S)
    ap.add_argument("--drill-script", default=None, help="演练脚本路径覆盖（默认 %s）" % DRILL)
    ap.add_argument("--skip-host-check", action="store_true", help="跳过 SSH 可达闸（仅调试）")
    ap.add_argument("--selftest", action="store_true", help="离线自检（不连主机、不跑演练）")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.drill_script:
        globals()["DRILL"] = a.drill_script
    force = a.force or os.environ.get("MAAORCH_ROLLBACK_DRILL_FORCE") == "1"
    try:
        code, _ = run_once(only=a.only, force=force, alert_send_flag=a.alert_send, no_card=a.no_card,
                           keep=a.keep, timeout_s=a.timeout, skip_host_check=a.skip_host_check)
        return code
    except Exception as e:  # noqa: BLE001
        emit("ROLLBACK_DRILL_AUTO=ERROR:%s:%s" % (type(e).__name__, str(e)[:200]))
        return 3


if __name__ == "__main__":
    sys.exit(main())
