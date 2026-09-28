#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AUTO-MAS 失败定因（triage）—— 把 "MAA 在完成任务前退出" 这种笼统结论，
落到「哪条任务链出错 / 什么原因 / 要不要管 / 要不要补跑」。

背景（2026-09-27 实测）：hbznn119-日 连续 6 次被判 "MAA 在完成任务前退出"，真实根因是
**公招链(Recruit)被放弃** —— 该号招聘许可=0，开不了新招募；而唤醒/作战/基建/信用购物/领奖
全部完成 ⇒ 属**资源类可忽略**失败，不该按"实例/网络故障"去补跑。

数据源（全部只读）：
  * AUTO-MAS REST：/api/scripts/get、/api/scripts/user/get（账号 -> 脚本 -> MAA 目录）
  * 主机 SSH：读该 MAA 目录的 debug/asst.log，抓 TaskChainError / SubTaskError
    （这两个标记是纯 ASCII，不受 GBK/UTF-8 编码差异影响）

用法：
  python tools/mas_error_triage.py --check
  python tools/mas_error_triage.py --account hbznn119-日 [--json]
  python tools/mas_error_triage.py --deadline-lines 400
  python tools/mas_error_triage.py --maa-dir "E:\\MAA-Multi\\MAA_7"

分类表（KIND 可扩展）：
  recruit_abandoned  公招链被放弃（招聘许可不足 / 招募位状态）  severity=info  不补跑
  fight_failed       作战链失败（理智/关卡/代理）               severity=warn
  infrast_failed     基建链失败                                 severity=warn
  startup_failed     唤醒/登录失败（PRTS/网络）                 severity=warn
  unknown_chain      其它/未识别链失败                          severity=warn
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

API = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")

KINDS = {
    "Recruit": ("recruit_abandoned", "info", "公招链被放弃（常见：招聘许可不足 / 招募位状态异常）",
                "不影响其它任务链；不触发补跑"),
    "Fight": ("fight_failed", "warn", "作战链失败（理智不足 / 关卡不可打 / 代理问题）", "按需人工确认"),
    "Infrast": ("infrast_failed", "warn", "基建链失败（换班/干员占用/界面异常）", "按需人工确认"),
    "StartUp": ("startup_failed", "warn", "唤醒/登录失败（PRTS 登录、网络、客户端）", "可重试"),
    "Mall": ("mall_failed", "warn", "信用购物链失败", "按需人工确认"),
    "Award": ("award_failed", "warn", "领取奖励链失败", "按需人工确认"),
}


def post(path: str, body: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body or {}).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def resolve_maa_dir(account: str) -> tuple[str, str]:
    """账号 -> (脚本名, MAA 目录)。走 AUTO-MAS REST（只读）。"""
    scripts = (post("/api/scripts/get", {"scriptId": None}).get("data") or {})
    for sid, s in scripts.items():
        try:
            users = (post("/api/scripts/user/get", {"scriptId": sid}).get("data") or {})
        except Exception:  # noqa: BLE001
            continue
        names = [((u or {}).get("Info") or {}).get("Name") for u in users.values()]
        if account in names:
            info = (post("/api/scripts/get", {"scriptId": sid}).get("data") or {}).get(sid, {}).get("Info") or {}
            return str(info.get("Name", sid)), str(info.get("Path", ""))
    raise SystemExit("找不到账号 %s 所属脚本（名字要与 AUTO-MAS 里一致）" % account)


def ssh_lines(maa_dir: str, limit: int = 40) -> list[str]:
    """读该 MAA 目录 asst.log 里最近的 TaskChainError / SubTaskError 行（纯 ASCII 匹配）。"""
    log = maa_dir.rstrip("\\/") + r"\debug\asst.log"
    ps = ("$p='%s'; if (Test-Path $p) { Select-String -Path $p -Pattern 'TaskChainError|SubTaskError' "
          "-Encoding UTF8 | Select-Object -Last %d | ForEach-Object { $_.Line } } else { 'NO_LOG' }") % (log, limit)
    cmd = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
           SSH_HOST, "powershell -NoProfile -Command \"%s\"" % ps]
    # stdin=DEVNULL 必须：本函数在 MCP stdio server（mas_triage_mcp）里被调用时，
    # ssh 子进程若继承永不 EOF 的 MCP stdin 管道会挂死到超时（实测 180s；DEVNULL 后 ~2s）。
    r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    return [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]


def triage(account: str | None, maa_dir: str | None) -> dict:
    script = ""
    if not maa_dir:
        script, maa_dir = resolve_maa_dir(account or "")
    if not maa_dir:
        raise SystemExit("无法确定 MAA 目录")
    lines = ssh_lines(maa_dir)
    chain_errs, sub_errs = [], []
    for ln in lines:
        m = re.search(r'TaskChainError\s*\{?\s*"taskchain"\s*:\s*"([^"]+)"', ln)
        if m:
            chain_errs.append(m.group(1))
        m2 = re.search(r'"subtask"\s*:\s*"([^"]+)"', ln) or re.search(r'"class"\s*:\s*"(asst::\w+)"', ln)
        if "SubTaskError" in ln and m2:
            sub_errs.append(m2.group(1))
    chains = []
    for c in chain_errs:
        if c not in chains:
            chains.append(c)
    chain = chains[-1] if chains else ""
    kind, sev, why, action = KINDS.get(chain, ("unknown_chain", "warn", "未识别的任务链错误", "看日志人工判断"))
    # 细分类：公招链走到「招募确认(RecruitConfirm)」才失败 —— 实测该号招聘许可=0（资源类，可忽略）
    if chain == "Recruit" and any("RecruitConfirm" in ln for ln in lines):
        kind, sev = "recruit_confirm_failed", "info"
        why = "公招「招募确认」步骤失败 —— 实测原因：该号招聘许可=0，开不了新招募（资源类）"
        action = "不影响其它任务链；不补跑、不告警升级；等券到位后自愈"
    return {
        "account": account or "",
        "script": script,
        "maa_dir": maa_dir,
        "chains_with_error": chains,
        "subtask_errors": sub_errs[-3:],
        "kind": kind,
        "severity": sev,
        "reason": why,
        "action": action,
        "evidence": lines[-4:],
        "log_grep": "TaskChainError|SubTaskError",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="AUTO-MAS 失败定因（只读）")
    ap.add_argument("--account")
    ap.add_argument("--maa-dir")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        try:
            s = post("/api/scripts/get", {"scriptId": None})
            ok = s.get("code") == 200
        except Exception as e:  # noqa: BLE001
            print("mas_error_triage --check: FAIL (REST %s)" % e)
            return 2
        print("mas_error_triage --check: OK (REST reachable, %d scripts)" % len(s.get("data") or {}))
        return 0
    res = triage(a.account, a.maa_dir)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("账号 %s（脚本 %s / %s）" % (res["account"], res["script"], res["maa_dir"]))
        print("出错链: %s ｜ 子任务: %s" % (", ".join(res["chains_with_error"]) or "无", ", ".join(res["subtask_errors"]) or "无"))
        print("判定: %s（severity=%s）—— %s ⇒ %s" % (res["kind"], res["severity"], res["reason"], res["action"]))
        for e in res["evidence"]:
            print("  证据: " + e[:170])
    return 0


if __name__ == "__main__":
    sys.exit(main())
