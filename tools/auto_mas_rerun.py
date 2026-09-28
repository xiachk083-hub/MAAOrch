# -*- coding: utf-8 -*-
"""临时起队列补跑（AUTO-MAS）：把指定账号塞进临时队列并发车，跑完即弃。

三条硬约束（踩过）：
1. **同一 MAA 目录的号必须放同一条临时队列**——不然两条队列会抢同一个 MAA_N 目录。
2. **目标 MAA 目录必须当前空闲**——正在跑的产线队列还占着它的话，会互相踩。
3. **模拟器起不来的号补跑也是白跑**——先查 MuMuManager，实例不 boot 的直接拦下。

用法：
    python tools/auto_mas_rerun.py --accounts "欧哈呦学妹"            # dry-run
    python tools/auto_mas_rerun.py --accounts "欧哈呦学妹" --apply    # 建临时队列并发车
    python tools/auto_mas_rerun.py --accounts "A,B" --apply --force   # 模拟器没起来也硬试
    python tools/auto_mas_rerun.py --cleanup                          # 删掉所有「临时-补跑-*」队列
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import subprocess
import sys
import time
import urllib.request

API = "http://100.79.173.69:36163"
SSH = ["ssh", "-i", r"C:\Users\xiach\.ssh\maaorch_target", "-o", "StrictHostKeyChecking=no",
       "-o", "ConnectTimeout=20", "Administrator@100.79.173.69"]
MGR = r"E:\MuMu Player 12\nx_main\MuMuManager.exe"
TAG = "临时-补跑"


def api(path: str, body: dict | None = None, timeout: int = 90) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body if body is not None else {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def ssh_ps(script: str, timeout: int = 180) -> str:
    r = subprocess.run(SSH + ["chcp 65001 >nul && powershell -NoProfile -Command " + '"' + script + '"'],
                       capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    return (r.stdout + r.stderr).decode("utf-8", "replace")


def load_scripts() -> dict:
    out = {}
    for sid, sv in (api("/api/scripts/get", {"scriptId": None}).get("data") or {}).items():
        info = sv.get("Info") or {}
        ud = api("/api/scripts/user/get", {"scriptId": sid, "userId": None}).get("data") or {}
        users = [uv.get("Info") or {} for k, uv in ud.items()
                 if k != "instances" and isinstance(uv, dict)]
        out[sid] = {"name": info.get("Name", "?"), "path": (info.get("Path") or "").replace("/", "\\"),
                    "emu": str((sv.get("Emulator") or {}).get("Index", "")),
                    "enabled": [u.get("Name") for u in users if u.get("Status")],
                    "all_users": [u.get("Name") for u in users]}
    return out


def busy_paths() -> set:
    """正在被占用的 MAA **目录**（有 MAA.exe 在跑 ⇒ 取 exe 的父目录）"""
    out = ssh_ps("Get-Process MAA -ErrorAction SilentlyContinue | ForEach-Object { $_.Path }")
    res = set()
    for ln in out.splitlines():
        p = ln.strip().replace("/", "\\")
        if p.endswith("MAA.exe"):
            res.add(p.rsplit("\\", 1)[0])
    return res


BOOT_PS = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'MuMuVMMHeadless.exe' } | "
           "ForEach-Object { $_.CommandLine }")


def vmm_running() -> dict:
    """{实例号: pid} —— 按 MuMuVMMHeadless 命令行里的 MuMuPlayer-12.0-<N> 匹配。

    ⚠️ 本机实测：`MuMuManager info -v N` / `adb -v N` **完全不可信**（会对着正在被
    AUTO-MAS 使用的实例回 "vm not running"），所以体检一律看进程列表，不看它。
    """
    out = ssh_ps(BOOT_PS)
    res = {}
    for ln in out.splitlines():
        if "MuMuPlayer-12.0-" in ln:
            idx = ln.split("MuMuPlayer-12.0-")[-1].split()[0].strip('"')
            res[idx] = idx
    return res


def emu_ready(indices: set, wait: int = 180) -> dict:
    """{emu: 'up' | 'launched' | 'dead'}：没开的就地拉起，等它出现 VMM 进程。"""
    if not indices:
        return {}
    want = {str(i) for i in indices}
    have = vmm_running()
    todo = sorted(want - set(have))
    res = {i: "up" for i in want if i in have}
    if todo:
        ssh_ps("& '" + MGR + "' control -v " + " launch; & '" + MGR + "' control -v ".join(todo)
               + " launch")
        t0 = time.time()
        while time.time() - t0 < wait and todo:
            time.sleep(15)
            have = vmm_running()
            for i in list(todo):
                if i in have:
                    res[i] = "launched"
                    todo.remove(i)
        for i in todo:
            res[i] = "dead"
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", default="", help="账号名，逗号分隔")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true", help="无视体检结果（现在默认就不拦，保留兼容）")
    ap.add_argument("--skip-dead", action="store_true", help="体检没等到进程的号直接剔除不发车")
    ap.add_argument("--cleanup", action="store_true", help="删掉所有临时补跑队列")
    args = ap.parse_args()

    qs = api("/api/queue/get", {"queueId": None}).get("data") or {}

    if args.cleanup:
        n = 0
        for qid, qv in qs.items():
            if (qv.get("Info") or {}).get("Name", "").startswith(TAG):
                print("  删除", qv["Info"]["Name"], api("/api/queue/delete", {"queueId": qid}))
                n += 1
        print(f"清掉 {n} 条临时队列")
        return

    want = [x.strip() for x in args.accounts.split(",") if x.strip()]
    if not want:
        sys.exit("用法：--accounts \"账号1,账号2\"")
    scripts = load_scripts()

    # 账号 -> 脚本
    hit, miss = {}, [w for w in want]
    for sid, sv in scripts.items():
        for u in sv["enabled"]:
            if u in want:
                hit[u] = sid
                miss = [m for m in miss if m != u]
    for m in miss:
        print(f"  [!] 找不到启用中的账号：{m}")

    # 每台模拟器单独体检（一台坏了不能连累同组的号）
    emus = emu_ready({scripts[hit[a]]["emu"] for a in hit if scripts[hit[a]]["emu"]})
    print("模拟器体检（按 VMM 进程判断；MuMuManager 的状态查询本机不可信）:")
    dead = []
    for a in sorted(hit, key=lambda x: scripts[hit[x]]["emu"]):
        e = scripts[hit[a]]["emu"]
        st = emus.get(e, "up")
        print("  emu %-3s %-22s %s" % (
            e, a, {"up": "已在跑 ✓", "launched": "本次拉起 ✓",
                   "dead": "没等到 VMM 进程 ⚠"}.get(st, st)))
        if st == "dead":
            dead.append(a)
    # 体检偏保守、误判率不低（AUTO-MAS 自己拉起的实例有时 180 秒内不出现在进程表里，
    # 实测 emu 32 就被冤枉过两次）⇒ 默认只告警不拦；要按体检结果剔除得显式加 --skip-dead。
    if dead and args.skip_dead and not args.force:
        print("  ⇒ 按体检结果剔除：%s" % "、".join(dead))
        alive = {a: s for a, s in hit.items() if a not in dead}
    else:
        if dead:
            print("  ⇒ 以上只是「没等到进程」，仍照发（AUTO-MAS 会自己拉起并重试 3 次）；"
                  "要剔除加 --skip-dead")
        alive = dict(hit)

    # 按 MAA 目录分组（同目录必须同队列）；组内按用户输入顺序排，保证"打印的顺序 = 实际入队顺序"
    order = {name: i for i, name in enumerate(want)}
    groups: dict[str, list[str]] = {}
    for acct, sid in alive.items():
        groups.setdefault(scripts[sid]["path"], []).append(acct)

    busy = busy_paths()
    print("\n正在跑的 MAA 目录:", ", ".join(sorted(p.split("\\")[-1] for p in busy)) or "（无）")
    print()
    plan = []
    for path, accts in sorted(groups.items()):
        accts.sort(key=lambda a: order.get(a, 10 ** 6))       # 组内按用户给的顺序
        sids = []
        for a in accts:
            if alive[a] not in sids:                          # 同脚本只加一次
                sids.append(alive[a])
        ok_busy = path not in busy
        seq = " → ".join("%d.%s[%s]" % (i, a, scripts[alive[a]]["name"])
                         for i, a in enumerate(accts, 1))
        print(f"  临时队列 ← {path.split(chr(92))[-1]:8s} {seq}")
        if not ok_busy:
            print("      ✗ 目录被占用（有产线 MAA 在跑），跳过")
            continue
        plan.append((accts, sids))
    if dead:
        print(f"\n  剔除（模拟器起不来）：{', '.join(dead)}")

    if not plan:
        print("\n没有可发车的组")
        return
    if not args.apply:
        print(f"\n[dry-run] 将建 {len(plan)} 条临时队列并发车。加 --apply 执行。")
        return

    ts = dt.datetime.now().strftime("%m%d_%H%M")
    for i, (accts, sids) in enumerate(plan, 1):
        qid = api("/api/queue/add", {}).get("queueId")
        api("/api/queue/update", {"queueId": qid,
                                  "data": {"Info": {"Name": f"{TAG}-{ts}-{i}", "TimeEnabled": False,
                                                    "StartUpEnabled": False, "AfterAccomplish": "NoAction"}}})
        for sid in sids:
            r1 = api("/api/queue/item/add", {"queueId": qid})
            iid = r1.get("queueItemId") or (r1.get("data") or {}).get("uid")
            api("/api/queue/item/update", {"queueId": qid, "queueItemId": iid,
                                           "data": {"Info": {"ScriptId": sid}}})
        r = api("/api/dispatch/start", {"taskId": qid, "mode": "AutoProxy"})
        print(f"  [{TAG}-{ts}-{i}] 发车 {', '.join(accts)}: {json.dumps(r, ensure_ascii=False)[:120]}")


if __name__ == "__main__":
    sys.exit(main())
