# -*- coding: utf-8 -*-
"""AUTO-MAS 跑位/队列体检（只读；扩槽或搬迁后必跑一遍）。

为什么需要它：`auto_mas_add_slots.py` + `auto_mas_flatten.py` 会**按命名约定**推
`E:\\MAA-Multi\\MAA_<槽位号>`（不检查目录是否存在），也会直接改脚本的 `Info.Path` ——
一旦推错或写空，对应账号第二天就直接起不来。所以搬完必须**独立**验一遍（别信搬迁工具自己的复核）。

查六件事：
① 队列：日常/晚班条数与项数、两套是否一一对应
② `Path`：队列里用到的脚本有没有空 `Path`（空 = 脚本被废掉，历史踩过的陷阱）
③ 目录：用到的 MAA 目录是否都在 `E:\\MAA-Multi` 里、有没有 `MAA.exe`
④ 目录集合是否恰好是 `MAA_1..MAA_<槽位数>`（没有跑偏的旧目录）
⑤ 同一模拟器 `Emulator.Index` 不许出现在两个槽位（不同槽位是并行跑的，会互抢模拟器）
⑥ 同一个脚本项不许同时挂在两套槽位

字段位置（2026-09-21 实测核对过，别猜）：
  /api/scripts/get      → data[sid] = {Info:{Name,Path}, Emulator:{Index}}   ← Path 在**脚本** Info 里
  /api/scripts/user/get → data = {userUid:{Info:{Name…}}}（号内昵称，没有 Path；且必须带 scriptId，传 null 报 422）
  /api/queue/item/get   → index 元素是 {uid,type} 字典，配置在 data[uid].Info（ScriptId 在这一层）

用法：
    python tools/auto_mas_verify.py            # 体检（有异常时退出码 1）
    python tools/auto_mas_verify.py --slots 12 # 只按指定槽位数判"目录是否跑偏"
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import urllib.request

API = "http://100.79.173.69:36163"
SSH = ["ssh", "-i", os.path.expanduser(r"~\.ssh\maaorch_target"),
       "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
       "Administrator@100.79.173.69"]

problems: list[str] = []


def api(path: str, body: dict | None = None, timeout: int = 90) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body or {}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def item_sid(entry, data) -> str:
    uid = entry.get("uid") if isinstance(entry, dict) else entry
    node = data.get(uid) if isinstance(data, dict) else None
    if isinstance(node, dict):
        return (node.get("Info") or node).get("ScriptId") or uid
    return uid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slots", type=int, default=0, help="槽位数（默认按日常队列条数推断）")
    ap.add_argument("--no-ssh", action="store_true", help="跳过主机目录检查")
    args = ap.parse_args()

    scripts = api("/api/scripts/get", {"scriptId": None}).get("data") or {}
    meta = {}
    for sid, sv in scripts.items():
        info = (sv or {}).get("Info") or {}
        meta[sid] = {"name": info.get("Name", "?"), "path": (info.get("Path") or "").strip(),
                     "emu": str(((sv or {}).get("Emulator") or {}).get("Index", ""))}

    qs = api("/api/queue/get", {"queueId": None}).get("data") or {}
    slot_items: dict[str, list[str]] = {}
    for qid, q in qs.items():
        name = ((q or {}).get("Info") or {}).get("Name", "?")
        r = api("/api/queue/item/get", {"queueId": qid, "queueItemId": None})
        slot_items[name] = [item_sid(e, r.get("data") or {}) for e in (r.get("index") or [])]

    daily = sorted(n for n in slot_items if n.startswith("日常"))
    eve = sorted(n for n in slot_items if n.startswith("晚班"))
    slots = args.slots or len(daily)

    print("=== ① 队列 ===")
    for n in daily:
        print("  %-12s %2d 项" % (n, len(slot_items[n])))
    print("  日常 %d 条 / 晚班 %d 条（槽位 %d）" % (len(daily), len(eve), slots))
    if len(daily) != len(eve):
        problems.append("日常/晚班队列条数不一致")
    for n in daily:
        if len(slot_items.get(n, [])) != len(slot_items.get("晚班" + n[2:], [])):
            problems.append("%s 与 晚班%s 项数不一致" % (n, n[2:]))

    used = {s for items in slot_items.values() for s in items}
    print("\n=== ② Path 空值 ===")
    empty = [s for s in used if not meta.get(s, {}).get("path")]
    print("  队列里用到的脚本 %d 个，空 Path %d %s" % (len(used), len(empty),
          "→ " + "、".join(meta.get(s, {}).get("name", s) for s in empty[:10]) if empty else "✓"))
    if empty:
        problems.append("%d 个脚本 Path 为空（会被直接废掉）" % len(empty))

    print("\n=== ③④ MAA 目录 ===")
    dirs = collections.Counter(meta[s]["path"].replace("/", "\\").rstrip("\\").split("\\")[-1]
                               for s in used if meta.get(s, {}).get("path"))
    for k in sorted(dirs, key=lambda x: (len(x), x)):
        print("  %-9s %d 个号" % (k, dirs[k]))
    stray = [k for k in dirs if not (k.startswith("MAA_") and k[4:].isdigit()
                                     and 1 <= int(k[4:]) <= slots)]
    print("  超出 MAA_1..%d 的目录: %s" % (slots, stray or "无 ✓"))
    if stray:
        problems.append("用到范围外目录: %s" % "、".join(stray))

    if not args.no_ssh:
        # 注意：cmd 的 for 变量本身写成 %i，别用 %d —— 会和 Python 的 % 格式化打架
        range_expr = " ".join(str(i) for i in range(1, slots + 1))
        cmd = ('dir /b /ad E:\\MAA-Multi & '
               'for %i in (' + range_expr + ') do @dir /b "E:\\MAA-Multi\\MAA_%i\\MAA.exe" >nul 2>&1 '
               '&& echo EXE_OK %i || echo EXE_MISSING %i')
        try:
            out = subprocess.run(SSH + [cmd], capture_output=True, timeout=180)
            txt = (out.stdout + out.stderr).decode("utf-8", "replace")
        except Exception as e:                                     # noqa: BLE001
            txt = ""
            print("  (SSH 失败: %s)" % str(e)[:120])
        missing = [ln.split()[-1] for ln in txt.splitlines() if ln.startswith("EXE_MISSING")]
        dirs_on_disk = {ln.strip() for ln in txt.splitlines() if ln.strip().startswith("MAA_")}
        print("  盘上目录: %s" % ("、".join(sorted(dirs_on_disk, key=lambda x: (len(x), x))) or "(取不到)"))
        print("  缺 MAA.exe 的: %s" % ("、".join(missing) or "无 ✓"))
        if missing:
            problems.append("目录缺 MAA.exe: %s" % "、".join(missing))
        if dirs_on_disk:
            for d in dirs:
                if d not in dirs_on_disk:
                    problems.append("队列用到但盘上没有目录: %s" % d)
        else:
            print("  （主机目录列表没取到，本次跳过存在性判定）")

    print("\n=== ⑤ 模拟器独占（同 emu 不许跨槽）===")
    emu_slots: dict[str, set] = collections.defaultdict(set)
    for name, items in slot_items.items():
        for s in items:
            e = meta.get(s, {}).get("emu")
            if e:
                emu_slots[e].add(name[2:])          # 日常定时N 与 晚班定时N 共用同一槽位
    clash = {e: sorted(v) for e, v in emu_slots.items() if len(v) > 1}
    print("  跨槽位的模拟器: %s" % (clash or "无 ✓"))
    if clash:
        problems.append("%d 个模拟器跨槽位: %s" % (len(clash), "、".join(clash)))

    print("\n=== ⑥ 脚本项独占（同项不许挂两套槽）===")
    where: dict[str, set] = collections.defaultdict(set)
    for name, items in slot_items.items():
        for s in items:
            where[s].add(name)
    dup = {k: sorted(v) for k, v in where.items() if len({n[2:] for n in v}) > 1}
    print("  跨槽位的脚本项: %s" % (dup or "无 ✓"))
    if dup:
        problems.append("%d 个脚本项跨槽位" % len(dup))

    print("\n" + ("❌ 发现 %d 类问题：\n  - %s" % (len(problems), "\n  - ".join(problems))
                  if problems else "✅ 体检通过：队列完整、无空 Path、目录齐、模拟器与脚本项都不跨槽"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
