#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""让 MAA 只跑一次 StartUp（登录）以核对账号 —— 逐号实证「手机号 + 博士UID」。

背景：MAA 日志里没有账号身份；唯一可靠判据是"登录后客户端标题页/大厅显示的 玩家<手机号掩码> + ID:<UID>"。
本脚本把一个号的 Task 临时改成"只跑 StartUp"，让 AUTO-MAS 拉起 MAA 完成登录，然后**立刻还原配置**。

用法：
  python tools/mas_startup_verify.py --backup              # 备份 4 个号的 Task 到 runtime/guan1_task_backup.json
  python tools/mas_startup_verify.py --run 潮响             # 单号：改成仅 StartUp → 发车 → 等 StartUp 完成 → 还原 → 回读校验
  python tools/mas_startup_verify.py --restore --verify     # 从备份还原全部并回读校验
  python tools/mas_startup_verify.py --shot 潮响            # 拉起实例+客户端，截图标题页到 tmp/hermes_daily_check/guan1_startup/

安全：每一步都写 runtime/mas_startup_verify.log；--run 结束（含异常）都会尝试还原；还原后必须回读一致才算成功。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mas_account_identity_check as M  # noqa: E402

API = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
SCRIPT_ID = "378f7379-bea3-4178-8f91-3369a57ca19a"      # 明日方舟-官-1-1-7
MAA_DIR = r"E:\MAA-Multi\MAA_6"
EMU_INDEX = "13"
PKG = "com.hypergryph.arknights"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP = os.path.join(ROOT, "runtime", "guan1_task_backup.json")
LOG = os.path.join(ROOT, "runtime", "mas_startup_verify.log")
SHOT_DIR = os.path.join(ROOT, "tmp", "hermes_daily_check", "guan1_startup")


def post(path: str, body: dict | None = None, timeout: int = 60) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body or {}).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def log(msg: str) -> None:
    line = "%s %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def users() -> dict:
    d = post("/api/scripts/user/get", {"scriptId": SCRIPT_ID}).get("data") or {}
    return {((u or {}).get("Info") or {}).get("Name"): {"uid": uid, "task": ((u or {}).get("Task") or {})}
            for uid, u in d.items()}


def set_task(user_uid: str, task: dict) -> dict:
    """返回原始响应。注意：AUTO-MAS 在该脚本正在跑时会返回 500「配置已锁定, 无法修改」。"""
    return post("/api/scripts/user/update", {"scriptId": SCRIPT_ID, "userId": user_uid, "data": {"Task": task}})


def backup() -> dict:
    snap = {name: v["task"] for name, v in users().items()}
    os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
    with open(BACKUP, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    log("backup -> %s : %s" % (BACKUP, json.dumps(snap, ensure_ascii=False)))
    return snap


def load_backup() -> dict:
    with open(BACKUP, encoding="utf-8") as f:
        return json.load(f)


def restore(verify: bool = True, attempts: int = 40, gap: int = 15) -> int:
    """从备份还原 4 个号的 Task。**运行期配置被锁 ⇒ 会重试到解锁为止**（默认 40×15s=10 分钟）。

    返回 0=全部还原且回读一致；1=仍未一致（危险，需人工介入）。
    """
    snap = load_backup()
    for i in range(1, attempts + 1):
        cur = users()
        changed, bad = [], []
        for name, task in snap.items():
            if name not in cur:
                bad.append(name + "(缺失)")
                continue
            if cur[name]["task"] != task:
                r = set_task(cur[name]["uid"], task)
                if r.get("code") == 200:
                    changed.append(name)
                else:
                    bad.append("%s(%s)" % (name, str(r.get("message"))[:40]))
        cur2 = users()
        still = [n for n, t in snap.items() if cur2.get(n, {}).get("task") != t]
        if not still:
            log("verify-after-restore: OK（第 %d 次尝试；本次改动=%s）" % (i, ",".join(changed) or "无"))
            return 0
        log("restore 第 %d 次未成功，仍不一致=%s%s；%ds 后重试" % (i, ",".join(still), (" 拒绝原因=" + ",".join(bad)) if bad else "", gap))
        time.sleep(gap)
    log("restore FAILED：10 分钟内没能还原 %s —— 必须人工处理（05:15 该号只会跑 StartUp）" % ",".join(still))
    return 1


def tail_gui(n: int = 40) -> list:
    ps = ("$log = '" + MAA_DIR + r"\debug\gui.log'; Get-Content -Tail " + str(n) + " -Encoding UTF8 $log")
    return M.remote_ps(ps).splitlines()


def run_one(name: str, timeout: int) -> int:
    snap = load_backup()
    if name not in snap:
        log("ERROR: %s 不在备份里（先 --backup）" % name)
        return 2
    cur = users()
    if name not in cur:
        log("ERROR: 找不到用户 %s" % name)
        return 2
    uid = cur[name]["uid"]
    only_startup = {"IfStartUp": True, "IfRecruit": False, "IfInfrast": False, "IfFight": False,
                    "IfMall": False, "IfAward": False, "IfRoguelike": False, "IfReclamation": False,
                    "IfDepotMaintain": False, "IfActivityFirst": False}
    try:
        set_task(uid, only_startup)
        log("[%s] Task -> 仅 StartUp" % name)
        r = post("/api/dispatch/start", {"taskId": uid, "mode": "ScriptConfig"})
        log("[%s] dispatch: %s" % (name, json.dumps(r, ensure_ascii=False)[:200]))
        started = dt.datetime.now()
        done = False
        while (dt.datetime.now() - started).total_seconds() < timeout:
            lines = tail_gui(60)
            if any("Completed Task Chain: StartUp" in x for x in lines):
                done = True
                break
            time.sleep(10)
        log("[%s] StartUp completed=%s（耗时 %.0fs）" % (name, done, (dt.datetime.now() - started).total_seconds()))
        # 等 MAA 退出（AUTO-MAS 运行期锁配置，不退出就改不回去）
        for _ in range(30):
            ps = ("(Get-CimInstance Win32_Process -Filter \"Name='MAA.exe'\" | "
                  "Where-Object { $_.CommandLine -like '*MAA_6*' } | Measure-Object).Count")
            if "0" in (M.remote_ps(ps, timeout=120).strip().splitlines() or ["1"])[-1]:
                break
            time.sleep(10)
        return 0 if done else 3
    finally:
        rc = restore(verify=True)
        if rc:
            log("[%s] 警告：还原失败，需要人工处理" % name)


def shot(name: str, wait_boot: int = 240) -> int:
    """拉起实例 13 + 客户端，截一张图回来（用于读「玩家<掩码>」与 ID:<UID>）。

    设备发现不写死端口：先记录 adb devices，再 MuMuManager adb --vmindex 13 connect，
    取**新增**的那个 serial（重启后端口会漂）。
    """
    os.makedirs(SHOT_DIR, exist_ok=True)
    adb = r"E:\MuMu Player 12\shell\adb.exe"
    remote_png = "E:\\_guan1_" + name + ".png"
    ps = ("$mumu = 'E:\\MuMu Player 12\\nx_main\\MuMuManager.exe'; $adb = '" + adb + "'; "
          "$before = @(& $adb devices | Select-String 'device$' | ForEach-Object { ($_ -split '\\s+')[0] }); "
          "& $mumu control -v " + EMU_INDEX + " launch | Out-Null; "
          "for ($i=0; $i -lt " + str(max(1, wait_boot // 15)) + "; $i++) { Start-Sleep -Seconds 15; "
          "  $o = (& $mumu info -v " + EMU_INDEX + ") -join ' '; if ($o -match 'is_android_started\":\s*true') { break } }; "
          "& $mumu adb --vmindex " + EMU_INDEX + " connect | Out-Null; Start-Sleep -Seconds 3; "
          "$after = @(& $adb devices | Select-String 'device$' | ForEach-Object { ($_ -split '\\s+')[0] }); "
          "$new = $after | Where-Object { $before -notcontains $_ }; "
          "$ser = if ($new) { $new[0] } else { $after[0] }; "
          "Write-Output ('SERIAL=' + $ser); "
          "& $adb -s $ser shell getprop sys.boot_completed; "
          "& $adb -s $ser shell monkey -p " + PKG + " -c android.intent.category.LAUNCHER 1 | Out-Null; "
          "for ($j=0; $j -lt 8; $j++) { Start-Sleep -Seconds 15; & $adb -s $ser shell screencap -p /sdcard/v.png; "
          "  $bytes = (& $adb -s $ser shell 'stat -c %s /sdcard/v.png') -join ''; "
          "  if ([int]$bytes -gt 300000) { break } }; "
          "& $adb -s $ser pull /sdcard/v.png '" + remote_png + "' | Out-Null; "
          "Write-Output ('SHOT_OK ' + $remote_png + ' ' + ((Get-Item '" + remote_png + "').Length))")
    out = M.remote_ps(ps, timeout=900)
    log("shot remote: " + out.replace("\n", " ")[-260:])
    scp = subprocess.run(["scp", "-O", "-i", M.KEY, "-o", "StrictHostKeyChecking=no",
                          M.SSH_HOST + ":" + remote_png.replace("\\", "/"),
                          os.path.join(SHOT_DIR, name + ".png")],
                         capture_output=True, stdin=subprocess.DEVNULL, text=True,
                         encoding="utf-8", errors="replace", timeout=300)
    local = os.path.join(SHOT_DIR, name + ".png")
    ok = os.path.exists(local)
    log("shot local %s -> %s (%s bytes)" % (name, local, os.path.getsize(local) if ok else "MISSING"))
    return 0 if ok else 4


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", action="store_true")
    ap.add_argument("--run")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--shot")
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()
    if a.backup:
        backup()
        return 0
    if a.restore:
        return restore(verify=True)
    if a.run:
        return run_one(a.run, a.timeout)
    if a.shot:
        return shot(a.shot)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
