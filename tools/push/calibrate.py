"""以「游戏原话」校准状态文件：地图节点右侧的实心黄点 = 该关已通关。

为什么需要（2026-09-17）: 状态文件会被「没看到结算页 → 记三星」污染（V 的 EX-3 就是这么假的），
于是推图循环跳过没通关的关、去追不存在的下一关，一关白烧 3~5 分钟。**黄点是唯一权威。**

判据与动作:
  · 有黄点、记录不是通关档        → 改成 三星（游戏说通了）
  · 无黄点、记录却是三星/二星/通关 → 改成 未确认 + reason（游戏说没通）
  · 图上没有这个节点              → **不动**（地图只画到「已通关的最后一关 + 1」，看不到 ≠ 没通）

⚠️ **黄点判据只对「殡仪堂」(EX 区) 有效**（2026-09-17 实测）: 通学路(SR 区)那张图是**横排路线**，
它的通关标记是节点左侧的**青色六边形**、**没有黄点** —— 拿黄点去判 SR 会把 V 已通的
SR-1/2/3 全翻成"未确认"，**凭空造一条假记录**（正是这套修复要消灭的那类 bug）。
所以 SR 区默认**不校准**，等它的判据也验证过再加。

用法:
    python calibrate.py --all                 # 三个号都校准（只 calibration 已验过的 EX 区）
    python calibrate.py --emu 1 --account 394162955 --pool event54_394162955
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("auto_push", HERE / "auto_push.py")
AP = importlib.util.module_from_spec(_spec)
sys.modules["auto_push"] = AP
_spec.loader.exec_module(AP)

ACCOUNTS = pathlib.Path(r"D:\1-2\MAAOrch\data\accounts.json")
DONE = ("三星", "二星", "通关")


def calibrate_area(area: str, seed_stage: str, shot_key: str) -> dict:
    """进到某个区（用它能找到的第一个节点当锚），返回 {stage: True/False}（黄点判据）。"""
    print(f"\n--- 校准区 {area}（锚点 {seed_stage}）---")
    AP.handle_ui(15)
    ok = AP.navigate_to(seed_stage)
    img = AP.shot(f"cal_{shot_key}")            # **文件名只用 ASCII**：cv2.imwrite 遇到中文名会静默失败
    if img is None:
        print("   抓不到帧，跳过这个区")
        return {}
    nodes = AP.read_nodes(img)
    if not nodes:
        print(f"   这个区读不到节点（navigate_to={ok}）⇒ 跳过")
        return {}
    # 只在**节点排布与本判据验证过的区**上判定（EX = 竖排 + 黄点）
    if not any(k.startswith("EX-") for k in nodes):
        print(f"   这个区读到的节点是 {sorted(nodes)}，不是 EX 区 ⇒ 不用黄点判据（样式不同）")
        return {}
    out = {}
    for code, (x, y) in sorted(nodes.items(), key=lambda kv: int(kv[0].split("-")[-1])):
        out[code] = AP.has_yellow_dot(img, x, y)
        print(f"   {code:8s} @({x:4d},{y:4d})  {'✅ 有黄点' if out[code] else '❌ 无黄点'}")
    return out


def apply_to_state(verdicts: dict, dry: bool) -> list[str]:
    """按黄点结果回写状态文件。返回变更说明列表。"""
    st = AP.load_state()
    stages = st.setdefault("stages", {})
    changes = []
    for code, cleared in verdicts.items():
        rec = stages.setdefault(code, {"status": "未打"})
        cur = rec.get("status")
        if cleared and cur not in DONE:
            changes.append(f"{code}: {cur} → 三星（游戏说有黄点）")
            rec["status"] = "三星"
            rec.pop("reason", None)
            rec["verified_by"] = "地图黄点 2026-09-17"
        elif (not cleared) and cur in DONE:
            changes.append(f"{code}: {cur} → 未确认（游戏说无黄点）")
            rec["status"] = "未确认"
            rec["reason"] = "地图无黄点（2026-09-17 校准：实际未通关）"
            rec["verified_by"] = "地图黄点 2026-09-17"
    if changes and not dry:
        st["calibrated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = AP.STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, AP.STATE_PATH)
    return changes


def run(emu: str, account: str, pool: str, dry: bool) -> None:
    AP.apply_target(emu, account, pool, allow_shared=False)
    # **校准也要抢按实例的锁**（2026-09-17）: 校准同样要开着游戏画面点，和推图同时跑会互相
    # 抢同一台模拟器 —— 这正是用户看到"一直战斗中却没在战斗"的成因之一。
    AP.take_emu_lock()
    AP.SCALE = AP.detect_scale()
    print(f"\n{'=' * 70}\n校准 {account}（实例 {emu} @ {AP.SERIAL}）｜ 状态 {AP.STATE_PATH.name}")

    verdicts: dict[str, bool] = {}
    for area, seed, key in (("殡仪堂", "EX-1", "ex"),):
        verdicts.update(calibrate_area(area, seed, key))
    print("   （通学路/SR 区不校准：它的通关标记是青色六边形、没有黄点，判据未验证 —— 见文件头说明）")

    # **交棒前统一收尾**（2026-09-17）: 校准也在动游戏，结束时把游戏放回大厅，
    # 否则紧接着起来的推图会从"没人管的界面"起步（box 读取就是这么坑过一次的）。
    AP.cleanup_to_lobby()

    changes = apply_to_state(verdicts, dry)
    if not changes:
        print("   状态与游戏一致，无需改动 ✓")
    for c in changes:
        print(f"   [改] {c}")
    if dry and changes:
        print("   （dry-run：没写盘）")


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--emu", default="")
    a.add_argument("--account", default="")
    a.add_argument("--pool", default="")
    a.add_argument("--all", action="store_true", help="按 data/accounts.json 校准全部账号")
    a.add_argument("--dry", action="store_true", help="只看差异，不写盘")
    args = a.parse_args()

    if args.all:
        accs = json.loads(ACCOUNTS.read_text(encoding="utf-8"))["accounts"]
        for acc in accs:
            try:
                run(str(acc["emu_index"]), acc["uid"], acc["pool"], args.dry)
            except SystemExit as e:
                print(f"   {acc['uid']} 跳过：{e}")
    else:
        if not args.account:
            raise SystemExit("要么 --all，要么给 --emu/--account/--pool")
        run(args.emu, args.account, args.pool, args.dry)


if __name__ == "__main__":
    main()
