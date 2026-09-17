#!/usr/bin/env python3
"""干员仓库读取（MAA OperBox 任务）—— 判断账号能跑哪份作业的前提。

为什么必须做: 作业里的 `opers`/`groups` 必须是账号**真的拥有**的干员，否则 MAA
的自动编队会报 `get_char_allocation_for_each_group has no feasible solution`，
然后带着不匹配的阵容开打 → 必输（2026-09-14 实测 15-11 第一份作业就是这么输的）。

复用 services/depot_reader.py 的成熟模式：子进程 + MaaCore 回调
（SubTaskExtraInfo=20003，what 以 OperBox 开头，details.done 时 data 是 JSON）。

产出: models/accounts/<id>/operbox.json 或 data/operbox/<tag>_<ts>.json
字段: own_opers[] 里每项有 name/rarity/elite/level/potential（无技能等级、无模组）

用法:
    python tools/maa_operbox.py --emu 1                       # 读并落盘
    python tools/maa_operbox.py --emu 1 --check "棘刺,桃金娘"   # 顺便查有没有这些干员

⚠️ **前置条件：游戏必须停在能被 MAA 接管的界面（大厅/首页）—— 不是"有空机就行"**
（2026-09-17 实测）: 游戏停在活动地图里直接读，MAA 会返回 `own: []` —— **看着像"这个号
一个干员都没有"，其实是没读到**（同一台机器同一个号，回到大厅再读就是 338/391 人）。
带"先回大厅 + 抢实例锁"的自动版：`python tools/box_read_when_free.py --emu N --uid <uid>`。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from infrastructure.task_constants import detect_emu_adb, find_adb  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="读账号干员仓库（MAA OperBox）")
    ap.add_argument("--emu", default="1")
    ap.add_argument("--instance", default="1")
    ap.add_argument("--client", default="Official")
    ap.add_argument("--startup", action="store_true", default=True,
                    help="先 StartUp 确保进入游戏（默认开）")
    ap.add_argument("--timeout", type=float, default=420.0)
    ap.add_argument("--check", default="", help="逗号分隔的干员名，检查是否拥有")
    ap.add_argument("--tag", default="emu" + time.strftime("%m%d_%H%M"))
    args = ap.parse_args()

    inst = (PROJ / "services" / "maa" / "instances" / str(args.instance)).resolve()
    adb = find_adb() or "adb"
    addr = detect_emu_adb(args.emu)
    if not addr:
        sys.exit("取不到模拟器 adb 端口")
    # 子进程内收回调 → 把结果打成一行哨兵输出
    child = f'''
import json, sys, time
sys.path.insert(0, {str(inst / "Python")!r})
from asst.asst import Asst
from ctypes import CFUNCTYPE, c_int, c_char_p, c_void_p

captured = {{}}
@Asst.CallBackType          # 必须装饰成 CFUNCTYPE —— 传普通函数会 ctypes.ArgumentError
def cb(msg, details, arg):
    d = json.loads(details.decode("utf-8", "replace"))
    if msg == 20003 and str(d.get("what", "")).startswith("OperBox"):
        det = d.get("details") or {{}}
        if det.get("done"):
            captured["all"] = det.get("all_opers") or []
            captured["own"] = det.get("own_opers") or []
    elif msg == 10001:      # AllTasksCompleted
        captured["done"] = True

Asst.load(path={str(inst)!r})
asst = Asst(cb)
ok = asst.connect({adb!r}, {addr!r})
if not ok:
    print("CONNECT_FAIL"); sys.exit(1)
asst.append_task("StartUp", {{"client_type": {args.client!r}, "start_game_enabled": True}})
asst.append_task("OperBox", {{}})
asst.start()
dl = time.time() + {args.timeout}
while time.time() < dl and asst.running():
    time.sleep(3)
asst.stop()
print("OPERBOX_RESULT " + json.dumps(captured, ensure_ascii=False))
'''
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True,
                           timeout=args.timeout + 120, encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = r.stdout or ""
    except subprocess.TimeoutExpired:
        sys.exit("子进程超时")
    if r.stderr and r.stderr.strip():
        print("   stderr:", r.stderr.strip()[-300:])
    print(f"--- 子进程 {time.time() - t0:.0f}s，输出尾部: {out.strip()[-200:]}")
    payload = None
    for line in out.splitlines():
        if line.startswith("OPERBOX_RESULT "):
            payload = json.loads(line[len("OPERBOX_RESULT "):])
    if not payload or not payload.get("own"):
        print("没收到干员数据（看 services/maa/instances/*/debug/asst.log）")
        # 兜底取证
        log = (inst / "debug" / "asst.log")
        if log.exists():
            hits = [l for l in log.read_text(encoding="utf-8", errors="replace").splitlines()[-3000:]
                    if "OperBox" in l or "analyze failed" in l or "not find oper" in l]
            for l in hits[-8:]:
                print("   ", l.split("] ", 1)[-1][:150])
        return
    own = payload["own"]
    allops = payload.get("all") or []
    path = PROJ / "data" / f"operbox_{args.tag}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"own": own, "all_count": len(allops)}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(f"\n拥有干员 {len(own)} 名（静态名单 {len(allops)}）→ 已写 {path}")
    by_rarity = {}
    for o in own:
        by_rarity.setdefault(o.get("rarity"), []).append(o.get("name"))
    for rar in sorted(by_rarity, reverse=True):
        names = by_rarity[rar]
        print(f"  {rar}★ ×{len(names)}: {', '.join(names[:18])}{' …' if len(names) > 18 else ''}")
    if args.check:
        want = [x.strip() for x in args.check.split(",") if x.strip()]
        owned = {o.get("name") for o in own}
        for w in want:
            hit = w in owned
            lv = next((f"{o.get('elite')}精{o.get('level')}级" for o in own if o.get("name") == w), "")
            print(f"  [{'有' if hit else '无'}] {w} {lv}")


if __name__ == "__main__":
    main()
