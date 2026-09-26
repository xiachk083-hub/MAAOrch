# -*- coding: utf-8 -*-
"""AUTO-MAS 关卡映射工具 — 「墟」复刻活动切换。

映射规则（2026-08-21 用户拍板）：
  - 搓玉号（原 TO-5，现 AT-4）      ：Stage=AT-4,  Stage_1=1-7（活动搓玉 + 日常搓玉兜底）
  - 材料号（现 AT-6 / 遗留 TO-8）    ：Stage=AT-8, Stage_1=AT-7, Stage_2=AT-6, Stage_3=1-7
                                     （类凝结核 → 酮凝集组 → 固源岩组 → 1-7）
  - 已配 1-7 的号 / 计划模式号 / 停用号：不动

用法：
  python tools/auto_mas_stage_map.py            # dry-run：打印分配方案，不改任何配置
  python tools/auto_mas_stage_map.py --apply    # 实际写入（POST /api/scripts/user/update）
  python tools/auto_mas_stage_map.py --file <path>  # 指定 ScriptConfig.json 本地副本
"""
import argparse
import json
import sys
import urllib.request

API = "http://100.79.173.69:36163"

# 当前关卡 -> (Stage, Stage_1, Stage_2, Stage_3)；"-" 表示不动该键
PLAN = {
    "AT-4": {"Stage": "AT-4", "Stage_1": "1-7"},
    "AT-6": {"Stage": "AT-8", "Stage_1": "AT-7", "Stage_2": "AT-6", "Stage_3": "1-7"},
    "TO-8": {"Stage": "AT-8", "Stage_1": "AT-7", "Stage_2": "AT-6", "Stage_3": "1-7"},
}


def load_accounts(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    rows = []
    for sid, v in d.items():
        if sid == "instances":
            continue
        ud = (v.get("SubConfigsInfo") or {}).get("UserData") or {}
        for uid, entry in ud.items():
            if uid == "instances":
                continue
            info = entry.get("Info") or {}
            rows.append(
                {
                    "sid": sid,
                    "uid": uid,
                    "name": info.get("Name", "?"),
                    "server": info.get("Server"),
                    "status": info.get("Status"),
                    "stagemode": info.get("StageMode"),
                    "stage": info.get("Stage"),
                    "stage_1": info.get("Stage_1"),
                }
            )
    return rows


def api(path: str, body: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def plan_changes(acc: dict) -> dict | None:
    """返回要写入的 Info 字段（仅变更键）；不需要改的返回 None。"""
    if acc["status"] is False or acc["stagemode"] != "Fixed":
        return None
    p = PLAN.get(acc["stage"])
    if not p:
        return None
    diff = {k: val for k, val in p.items() if acc.get(k.lower()) != val}
    return diff or None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入（默认只预览）")
    ap.add_argument("--file", default="tools/_scriptconfig_dump.json")
    args = ap.parse_args()

    accs = load_accounts(args.file)
    targets, skipped = [], []
    for a in accs:
        ch = plan_changes(a)
        if ch:
            targets.append((a, ch))
        else:
            skipped.append(a)

    print(f"总账号 {len(accs)} | 待改 {len(targets)} | 不动 {len(skipped)}")
    print("-" * 100)
    for a, ch in targets:
        tag = f"{a['stage']} → " + ", ".join(f"{k}={v}" for k, v in ch.items())
        print(f"  {a['name']:<24} [{a['server']:<9}] {tag}")
    print("-" * 100)
    print("不动:", ", ".join(a["name"] for a in skipped))

    if not args.apply:
        print("\n[dry-run] 未写入。加 --apply 生效。")
        return

    ok = fail = 0
    for a, ch in targets:
        r = api(
            "/api/scripts/user/update",
            {"scriptId": a["sid"], "userId": a["uid"], "data": {"Info": ch}},
        )
        if r.get("code") == 200:
            ok += 1
            print(f"  ✓ {a['name']}: {ch}")
        else:
            fail += 1
            print(f"  ✗ {a['name']}: {r.get('message')}")
    print(f"\n完成：成功 {ok}，失败 {fail}")


if __name__ == "__main__":
    sys.exit(main())
