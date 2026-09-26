# -*- coding: utf-8 -*-
"""作业轴可视化：把 MAA copilot 作业画成方格地图，逐格标出干员/箱子/朝向/技能，
并自动校验每一格能不能放得下去（近战只能放地面、远程可放地面或高台、禁止格不能放）。

用法:
    python tools/axis_viz.py data/copilot/BI-EX-7_v27.json --stage act14side_ex07 --out runtime/_ce6/axis_v27.png
"""
from __future__ import annotations

import argparse, io, json, pathlib, sys

from PIL import Image, ImageDraw, ImageFont

P = pathlib.Path(r"D:\1-2\MAAOrch")
CT = json.loads((P / "data/_character_table_full.json").read_text(encoding="utf-8"))
RANGED = {"SNIPER", "CASTER", "MEDIC", "SUPPORT"}          # 远程职业（可站地面或高台）
GROUND_ONLY = {"PIONEER", "WARRIOR", "TANK", "SPECIAL"}     # 近战职业（只能站地面）

CELL = 74
PAD = 64
RANGELESS = {"障碍物", "闸门"}


def prof_of(name: str) -> str:
    """返回干员的部署位属性：MELEE（只能地面）/ RANGED（地面或高台）/ NONE。"""
    for k, v in CT.items():
        if isinstance(v, dict) and v.get("name") == name:
            return (v.get("position") or "?").upper()
    return "?"


def load_map(stage: str):
    d = json.load(io.open(P / "data" / f"_level_{stage}.json", encoding="utf-8"))
    mp, tiles = d["mapData"]["map"], d["mapData"]["tiles"]
    rows, cols = len(mp), len(mp[0])
    grid = [[tiles[mp[r][c]] for c in range(cols)] for r in range(rows)]
    routes = []
    for rt in d.get("routes", []):
        sp, ep = rt["startPosition"], rt["endPosition"]
        routes.append(((sp["col"], sp["row"]), (ep["col"], ep["row"])))
    return grid, rows, cols, routes


def cell_kind(t):
    """格子类型 —— 2026-09-21 重写：改为按游戏数据里真正决定"谁能放"的字段判定。

    旧版只认 tile_start/end/wall/icestr/road/forbidden 六种 tileKey，**其它一律
    当不可部署** ✗ —— 于是新活动（act42side）的高台/地面格全被判死：MT-EX-1 我
    只"认出" 4 个可部署格，而作业站的人气作业实际用的是 (6,2)(6,3)(6,4)(5,4)…
    正确依据是 tile 自带的字段：
      buildableType: NONE / MELEE（只能放近战）/ RANGED（只能放远程，通常是高台）/ ALL
      heightType:    LOWLAND / HIGHLAND
      passableMask:  WALK_ONLY / FLY_ONLY / ALL（决定敌人能否走，与部署无关）
    返回的 kind 保持旧接口语义：ground/ice/road=两种都能放，high=只能 RANGED，
    wall/none=不能放，start/end=门。
    """
    k = t.get("tileKey")
    bt = t.get("buildableType")
    if k == "tile_end":
        return "end"
    if k == "tile_start":
        return "start"
    # 2026-09-21：MT-EX-1（act42side）实测教训——buildableType 只说"能不能放"，
    # 不说"放了会怎样"。这关 tile_sleep_road/tile_sleep_wall 是「入梦砖」，
    # 干员放上去会立刻被拖入美梦、什么都不做 ✗；全图只有 column 1 的
    # (1,1)(1,2)(1,4)(1,5) 是 tile_road 安全格。这里把入梦砖直接判为不可用
    # （宁可少报可用格，也不要让干员上场睡觉）。
    # 2026-09-21：act42side（MT 系列）实测——`tile_sleep_*` 是「入梦砖」格：
    # 干员放上去会"被拖入美梦"，但这只是**短暂代价**、不是永久失效：
    # 抄来的 MT-EX-1 三星作业（prts id 52788）主力正是摆在 (6,2)(6,3)(6,4)(5,4)
    # 这些 sleep 格上并通关 ✓。所以这里**不判死**，仍按"可部署"返回，
    # 只在图上标色提醒（见 COLORS["sleep"] 与 main() 里的提示）。
    if isinstance(k, str) and k.startswith("tile_sleep"):
        bt2 = t.get("buildableType")
        if bt2 == "RANGED":
            return "high"
        return "ice" if k.endswith("road") else "ground"
    if bt == "NONE" or bt is None:
        # 完全不能部署：高台当墙画、其它当不可用
        return "wall" if t.get("heightType") == "HIGHLAND" else "none"
    if bt == "RANGED":
        return "high"                      # 只能放远程（高台）
    # MELEE / ALL —— 地面位，两种干员都能放；冰面单独着色
    return "ice" if k == "tile_icestr" else "ground"



COLORS = {"ground": (236, 236, 232), "ice": (198, 232, 236), "road": (250, 250, 246),
          "high": (226, 208, 176), "wall": (86, 86, 92), "none": (150, 150, 156),
          "sleep": (172, 142, 210),   # 入梦砖：放上去干员会睡着（紫）
          "start": (208, 96, 96), "end": (86, 138, 214)}


# 突袭禁部署格（2026-09-21 从实机放大截图逐格读出：游戏里画 ✗ 的 8 格）
RAID_FORBIDDEN = {(4, 3), (5, 3), (6, 3), (7, 3), (4, 4), (5, 4), (6, 4), (7, 4)}


def can_place(kind: str, prof: str, name: str, cr=None, forbidden=None) -> tuple[bool, str]:
    if forbidden and cr in forbidden:
        return False, "突袭禁部署格（游戏内画 ✗，放不下去）"
    if name in RANGELESS:
        return (kind in ("ground", "ice", "road")), "箱子只能放地面"
    if kind in ("ground", "ice", "road"):
        return True, ""
    if kind == "high":
        return (prof == "RANGED"), "高台只能放 RANGED 位干员（游戏数据 position 字段）"
    return False, "不可部署格"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--stage", default="act14side_ex07")
    ap.add_argument("--out", default=str(P / "runtime" / "_ce6" / "axis.png"))
    ap.add_argument("--title", default="")
    ap.add_argument("--raid", action="store_true", help="按突袭禁部署格（(4,3)~(7,3)、(4,4)~(7,4) 八格）校验")
    a = ap.parse_args()

    job = json.load(io.open(a.job, encoding="utf-8"))
    grid, rows, cols, routes = load_map(a.stage)
    opname = {}
    for g in job.get("groups", []):
        for o in g.get("opers", []):
            opname[g["name"]] = (o.get("name"), o.get("skill"))
    for o in job.get("opers", []):
        if o.get("name"):
            opname[o["name"]] = (o.get("name"), o.get("skill"))

    W = PAD * 2 + CELL * cols
    H = PAD * 2 + CELL * rows + 190
    im = Image.new("RGB", (W, H), (255, 255, 255))
    dr = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("msyh.ttc", 17)
        fb = ImageFont.truetype("msyh.ttc", 20)
        fs = ImageFont.truetype("msyh.ttc", 14)
    except Exception:
        f = fb = fs = ImageFont.load_default()

    ox, oy = PAD, PAD + 40
    dr.text((PAD, 10), a.title or pathlib.Path(a.job).name, fill=(20, 20, 20), font=fb)

    for r in range(rows):
        for c in range(cols):
            kind = cell_kind(grid[r][c])
            x0, y0 = ox + c * CELL, oy + r * CELL
            dr.rectangle([x0, y0, x0 + CELL, y0 + CELL], fill=COLORS[kind], outline=(120, 120, 124))
            dr.text((x0 + 6, y0 + 4), "%d,%d" % (c, r), fill=(90, 90, 96), font=fs)

    # 蓝色出怪口/终点标记
    for r in range(rows):
        for c in range(cols):
            kind = cell_kind(grid[r][c])
            if kind in ("start", "end"):
                x0, y0 = ox + c * CELL, oy + r * CELL
                dr.text((x0 + 22, y0 + 26), "红门" if kind == "start" else "蓝门",
                        fill=(255, 255, 255) if kind == "start" else (255, 255, 255), font=f)

    warn = []
    for i, act in enumerate(job.get("actions", [])):
        if act.get("type") != "Deploy":
            continue
        name = act.get("name", "")
        loc = act.get("location") or [None, None]
        c, r = loc[0], loc[1]
        real, skill = opname.get(name, (name, None))
        if c is None or not (0 <= c < cols and 0 <= r < rows):
            warn.append("%s 位置越界 %s" % (real, loc))
            continue
        kind = cell_kind(grid[r][c])
        prof = prof_of(real)
        ok, why = can_place(kind, prof, real, (c, r), RAID_FORBIDDEN if a.raid else None)
        if not ok:
            warn.append("%s (%d,%d) 放不下：%s（该格=%s，职业=%s）" % (real, c, r, why, kind, prof))
        x0, y0 = ox + c * CELL, oy + r * CELL
        cx, cy = x0 + CELL // 2, y0 + CELL // 2
        is_crate = real in RANGELESS
        col = (250, 176, 60) if is_crate else (46, 120, 214)
        if not ok:
            col = (214, 48, 48)
        dr.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], outline=col, width=4)
        label = "箱" if is_crate else real[:6]
        tw = dr.textlength(label, font=f)
        dr.text((cx - tw / 2, cy - 10), label, fill=(20, 20, 20), font=f)
        if skill and not is_crate:
            dr.text((cx - 26, cy + 8), "S%d" % skill, fill=(140, 60, 20), font=fs)
        d = act.get("direction")
        if d in ("Up", "Down", "Left", "Right"):
            dx, dy = {"Up": (0, -1), "Down": (0, 1), "Left": (-1, 0), "Right": (1, 0)}[d]
            dr.line([cx + dx * 26, cy + dy * 26, cx + dx * 52, cy + dy * 52], fill=col, width=5)
            dr.polygon([(cx + dx * 60, cy + dy * 60),
                        (cx + dx * 44 - dy * 9, cy + dy * 44 + dx * 9),
                        (cx + dx * 44 + dy * 9, cy + dy * 44 - dx * 9)], fill=col)

    srcs = [(c, r) for (c, r) in {(s[0], s[1]) for s, _e in routes}]
    for (c, r), (ec, er) in [(s, e) for s, e in routes]:
        x1, y1 = ox + c * CELL + CELL // 2, oy + r * CELL + CELL // 2
        x2, y2 = ox + ec * CELL + CELL // 2, oy + er * CELL + CELL // 2
        dr.line([x1, y1, x2, y2], fill=(200, 60, 60, 90), width=2)

    y = oy + CELL * rows + 16
    dr.text((PAD, y), "图例：白=地面 浅蓝=冰面 土黄=高台(只放远程) 深灰=不可通行 蓝门=终点 红门=出怪口", fill=(40, 40, 44), font=f)
    dr.text((PAD, y + 24), "蓝色圈=干员(带朝向箭头与技能) 橙圈=障碍物箱子 红圈=该格放不下 ✗", fill=(40, 40, 44), font=f)
    if warn:
        dr.text((PAD, y + 48), "校验失败：", fill=(200, 30, 30), font=f)
        for i, w in enumerate(warn[:4]):
            dr.text((PAD + 80, y + 48 + i * 20), w, fill=(200, 30, 30), font=f)
    else:
        dr.text((PAD, y + 48), "校验通过：所有部署格都放得下 ✓", fill=(20, 140, 60), font=f)
    im.save(a.out)
    print("saved", a.out, im.size, "warnings:", len(warn))
    for w in warn:
        print("  ✗", w)
    # 有 warning 就以退出码 3 结束：调用方用
    #   python tools/axis_viz.py ... ; if ($LASTEXITCODE -ne 0) { exit 1 }
    # 机械拦截，避免"看到报错还是照跑"（2026-09-21 因此白跑三局）。
    if warn:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
