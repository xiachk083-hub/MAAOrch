# -*- coding: utf-8 -*-
"""修复 MAA 副本 config/gui.new.json(避免当前界面/后续读取不一致;下次任务启动会从模板覆盖)
"""
import json
import os
import shutil

BASE = r"E:\MAA-Multi"
changed = 0
for name in sorted(os.listdir(BASE), key=lambda s: (len(s), s)):
    p = os.path.join(BASE, name, "config", "gui.new.json")
    if not name.lower().startswith("maa_") or not os.path.isfile(p):
        continue
    d = json.load(open(p, encoding="utf-8"))
    tq = d.get("Configurations", {}).get("Default", {}).get("TaskQueue", [])
    n = 0
    for t in tq:
        if t.get("TaskType") == "Fight":
            t["UseExpiringMedicine"] = True
            t["MedicineExpireDays"] = 2
            t["UseExpireMedicineForActivity"] = False
            t["UseMedicine"] = False
            t["MedicineCount"] = 0
            n += 1
    if n:
        json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=4)
        changed += 1
        print(f"{name}: repaired (48h=True)")
print("copies repaired:", changed)
