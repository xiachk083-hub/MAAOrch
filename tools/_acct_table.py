# -*- coding: utf-8 -*-
"""按模拟器编号排账号表"""
import json

d = json.load(open(r"D:/1-2/MAAOrch/tools/_scriptconfig_dump.json", encoding="utf-8"))
rows = []
for sid, v in d.items():
    if sid == "instances":
        continue
    info = v.get("Info") or {}
    emu = v.get("Emulator") or {}
    idx = emu.get("Index", "?")
    path = (info.get("Path") or "?").replace("E:\\MAA-Multi\\", "").replace("E:/MAA-Multi/", "")
    ud = (v.get("SubConfigsInfo") or {}).get("UserData") or {}
    for uid, entry in ud.items():
        if uid == "instances":
            continue
        uinfo = entry.get("Info") or {}
        rows.append(
            {
                "idx": idx,
                "name": uinfo.get("Name", "?"),
                "server": uinfo.get("Server"),
                "status": uinfo.get("Status"),
                "stage": uinfo.get("Stage"),
                "anni": uinfo.get("Annihilation"),
                "maa": path,
            }
        )
rows.sort(key=lambda r: (int(r["idx"]) if str(r["idx"]).isdigit() else 999, r["name"]))
print("%-4s %-24s %-10s %-6s %-10s %-12s %-8s" % ("emu", "账号", "服务器", "状态", "关卡", "剿灭", "MAA副本"))
print("-" * 92)
for r in rows:
    print(
        "%-4s %-24s %-10s %-6s %-10s %-12s %-8s"
        % (
            r["idx"],
            r["name"],
            r["server"],
            "启用" if r["status"] else "停用",
            r["stage"] or "-",
            r["anni"] or "-",
            r["maa"],
        )
    )
print()
print("总账号:", len(rows))
