#!/usr/bin/env python3
"""下载敌人属性库（levels/enemydata/enemy_database.json）与敌人名表，存到 data/。

为什么要自建（2026-09-15）: 想算"这号干员能不能站住/打得死"，必须有敌人数值
（HP/攻击/防御/移速/攻间隔/技能）。作业站作业里没有，关卡文件只给波次与路线。
镜像上 enemy_database.json 有几十 MB，前台下载容易超时 → 单独跑这个脚本，可后台执行。
"""
from __future__ import annotations
import json
import pathlib
import sys
import urllib.request

PROJ = pathlib.Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0"}
BASES = [
    "https://fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/",
    "https://gcore.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/",
    "https://cdn.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/",
]
WANT = [
    ("levels/enemydata/enemy_database.json", PROJ / "data" / "_enemy_db.json"),
    ("excel/enemy_handbook_table.json", PROJ / "data" / "_enemy_handbook.json"),
]


def main() -> None:
    for path, out in WANT:
        if out.exists() and out.stat().st_size > 1000:
            print(f"已存在，跳过：{out.name} ({out.stat().st_size} B)")
            continue
        for base in BASES:
            try:
                raw = urllib.request.urlopen(
                    urllib.request.Request(base + path, headers=UA), timeout=600).read()
                out.write_bytes(raw)
                print(f"OK {path} ← {base.split('/')[2]} ({len(raw)} B) → {out}")
                break
            except Exception as e:
                print(f"  × {base.split('/')[2]}: {type(e).__name__} {str(e)[:80]}")
        else:
            print(f"!! 全部镜像失败：{path}", file=sys.stderr)


if __name__ == "__main__":
    main()
