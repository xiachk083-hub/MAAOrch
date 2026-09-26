#!/usr/bin/env python3
"""关卡数值顾问 —— 给「关卡 + 账号」算承受账/输出账。

由来（2026-09-15 用户）："作业只能算打法，还有干员的数值、敌方的机制数值会怎么走"。作业站
给的是站位与时机；能不能真过，得把三样对齐：地图（拦在哪）、敌方波次与数值（谁来、多硬、
怎么走）、我方干员数值（打不打得动、扛不扛得住）。这个脚本把后两样算成一张表。

数据来源（全部本地/镜像，无需登录）:
    地图   MAA resource/Arknights-Tile-Pos/<stageId>*.json      （格子类型/红门蓝门/高台）
    波次   levels/activities/<act>/level_<stageId>.json          （5 波的敌人、数量、路线）
    敌人   data/_enemy_db.json                                  （tools/fetch_enemy_db.py 下）
    我方   data/_char_table.json / _skill_table.json             （面板与技能倍率）
    账号   --operbox 指定的干员盒

用法:
    python tools/stage_value.py --stage act16side_08 --operbox data/operbox_pasted_0915e.json
    python tools/stage_value.py --stage act54side_ex05 --operbox data/operbox_pasted_0915f.json
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import pathlib
import re
import urllib.request

import cv2
import numpy as np

PROJ = pathlib.Path(__file__).resolve().parent.parent
CT = json.loads((PROJ / "data" / "_char_table.json").read_text(encoding="utf-8"))
SK = json.loads((PROJ / "data" / "_skill_table.json").read_text(encoding="utf-8"))
N2I = {v["name"]: k for k, v in CT.items() if v.get("name")}
ENEMY_DB = json.loads((PROJ / "data" / "_enemy_db.json").read_text(encoding="utf-8"))["enemies"]
ENEMIES_BY_KEY = {e["Key"]: e["Value"] for e in ENEMY_DB}
UA = {"User-Agent": "Mozilla/5.0"}
BASES = ["https://ghproxy.net/https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/",
         "https://fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/",
         "https://gcore.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/"]
MAA_TILE_DIRS = [
    "D:/Xiach/M-A/MAA/*/resource/Arknights-Tile-Pos",
    "C:/Users/*/M-A/MAA/*/resource/Arknights-Tile-Pos",
    str(PROJ / "services" / "maa" / "*" / "resource" / "Arknights-Tile-Pos"),
]


def unwrap(v):
    return v.get("m_value") if isinstance(v, dict) and "m_value" in v else v


# ---------- 地图 / 关卡 ----------
def find_tile_file(stage: str) -> pathlib.Path | None:
    for pat in MAA_TILE_DIRS:
        for d in glob.glob(pat):
            for f in glob.glob(str(pathlib.Path(d) / f"{stage}*.json")):
                if "#f#" not in f:                       # 优先普通难度
                    return pathlib.Path(f)
    return None


def load_level(level_id: str, stage: str) -> dict:
    """按地图文件里的 levelId 取关卡文件（波次/路线）。镜像会限流，逐个试。"""
    cache = PROJ / "data" / f"_level_{stage}.json"
    if cache.exists() and cache.stat().st_size > 1000:
        return json.loads(cache.read_text(encoding="utf-8"))
    for base in BASES:
        try:
            d = json.loads(urllib.request.urlopen(
                urllib.request.Request(f"{base}levels/{level_id}.json", headers=UA), timeout=90).read())
            cache.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            return d
        except Exception as e:
            print(f"  ! {base.split('/')[2]}: {type(e).__name__}{getattr(e, 'code', '')}")
    raise SystemExit(f"取不到关卡文件：{level_id}")


def render_map(tiles: list, level: dict, stage: str, plan: dict[str, tuple[int, int]] | None = None) -> str:
    H, W = len(tiles), len(tiles[0])
    S, PAD = 78, 44
    img = np.full((H * S + PAD, W * S + PAD, 3), 28, np.uint8)
    for r in range(H):
        for c in range(W):
            cell = tiles[r][c]
            key = (cell.get("heightType", 0), cell.get("buildableType", 0))
            color = {(0, 1): (90, 110, 90), (1, 2): (150, 110, 60), (0, 0): (58, 58, 58),
                     (1, 0): (45, 45, 45), (1, 1): (120, 120, 120)}.get(key, (60, 60, 60))
            x0, y0 = PAD + c * S, PAD + r * S
            cv2.rectangle(img, (x0, y0), (x0 + S - 2, y0 + S - 2), color, -1)
            if cell.get("isStart"):
                cv2.circle(img, (x0 + S // 2, y0 + S // 2), 16, (60, 60, 220), -1)
            if cell.get("isEnd"):
                cv2.circle(img, (x0 + S // 2, y0 + S // 2), 16, (220, 180, 60), -1)
    for r in level.get("routes") or []:
        sp, ep = r.get("startPosition") or {}, r.get("endPosition") or {}
        if None in (sp.get("row"), sp.get("col"), ep.get("row"), ep.get("col")):
            continue
        y1, y2 = H - 1 - sp["row"], H - 1 - ep["row"]
        if (sp["row"], sp["col"]) == (ep["row"], ep["col"]):
            continue
        cv2.arrowedLine(img, (PAD + sp["col"] * S + S // 2, PAD + y1 * S + S // 2),
                        (PAD + ep["col"] * S + S // 2, PAD + y2 * S + S // 2), (200, 120, 255), 2, tipLength=0.08)
    for nm, (c, r) in (plan or {}).items():
        x0, y0 = PAD + c * S, PAD + r * S
        cv2.rectangle(img, (x0 + 4, y0 + 4), (x0 + S - 6, y0 + S - 6), (0, 255, 255), 2)
        cv2.putText(img, nm[:6], (x0 + 6, y0 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
    cv2.putText(img, f"{stage} {W}x{H} | green=deployable blue=highground orange=wall red=start yellow=end purple=route",
                (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (230, 230, 230), 1)
    out = PROJ / "data" / "mastery_scan" / f"{stage}_map.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), img)
    return str(out)


# ---------- 数值 ----------
def panel_at(name: str, elite: int, level: int) -> dict:
    c = CT[N2I[name]]
    ph = c["phases"][min(elite, len(c["phases"]) - 1)]
    f = ph["attributesKeyFrames"]
    lo, hi = f[0]["data"], f[-1]["data"]
    t = 0.0 if f[-1]["level"] == f[0]["level"] else (level - f[0]["level"]) / (f[-1]["level"] - f[0]["level"])
    return {"hp": lo["maxHp"] + (hi["maxHp"] - lo["maxHp"]) * t,
            "atk": lo["atk"] + (hi["atk"] - lo["atk"]) * t,
            "df": lo["def"] + (hi["def"] - lo["def"]) * t,
            "interval": lo.get("baseAttackTime") or 1.3,
            "prof": c["profession"], "sub": c.get("subProfessionId")}


def dmg_kind(name: str) -> tuple[str, int, float, float, float]:
    """返回 (伤害类型, 用哪个技能, 攻击力加成, 攻击倍率, 间隔增量) —— 取倍率最高的技能"""
    c = CT[N2I[name]]
    best = ("物理", 0, 0.0, 1.0, 0.0)
    for i, s in enumerate(c.get("skills") or []):
        levels = SK.get(s["skillId"], {}).get("levels") or []
        if not levels:
            continue
        row = levels[-1]                                    # 专三
        desc = re.sub(r"<[^>]+>", "", row.get("description") or "")
        kind = "元素" if ("元素" in desc or "损伤" in desc) else ("法术" if "法术" in desc else "物理")
        bl = {b["key"]: b["value"] for b in (row.get("blackboard") or [])}
        atk_buff = float(bl.get("atk") or 0)
        scale = max([float(v) for k, v in bl.items() if "atk_scale" in k and "display" not in k] or [1.0])
        idelta = float(bl.get("base_attack_time") or 0)
        if atk_buff * scale > best[2] * best[3]:
            best = (kind, i, atk_buff, scale, idelta)
    if best[0] == "物理" and CT[N2I[name]]["profession"] == "CASTER":
        best = ("法术",) + best[1:]
    return best


def hit(atk: float, e: dict, kind: str) -> float:
    if kind in ("法术", "元素"):
        return atk * (1 - (e.get("res") or 0) / 100.0)
    return max(atk - (e.get("df") or 0), atk * 0.05)


def main() -> None:
    ap = argparse.ArgumentParser(description="关卡数值顾问")
    ap.add_argument("--stage", required=True, help="MAA stageId，如 act16side_08 / act54side_ex05")
    ap.add_argument("--operbox", required=True, type=pathlib.Path)
    ap.add_argument("--top", type=int, default=10, help="报告前 N 个输出位")
    ap.add_argument("--plan", default="", help="可选的 干员=col,row 列表（画在地图上）")
    args = ap.parse_args()

    box = {o["name"]: o for o in json.loads(args.operbox.read_text(encoding="utf-8")) if o.get("own")}
    tile_file = find_tile_file(args.stage)
    if not tile_file:
        raise SystemExit(f"MAA 资源里没有 {args.stage} 的地图文件")
    tile_meta = json.loads(tile_file.read_text(encoding="utf-8"))
    level = load_level(tile_meta["levelId"], args.stage)
    plan = {}
    for kv in filter(None, (x.strip() for x in args.plan.split(","))):
        if "=" in kv:
            nm, rc = kv.split("=", 1)
            c, r = (int(x) for x in rc.split(","))
            plan[nm] = (c, r)
    tiles = tile_meta["tiles"]
    print(f"地图：{tile_file.name} 关卡「{tile_meta.get('name')}」（{len(tiles[0])}x{len(tiles)}）"
          f" → {render_map(tiles, level, args.stage, plan)}")

    # 波次
    spawns = collections.defaultdict(int)
    print("\n=== 波次 ===")
    for wi, w in enumerate(level.get("waves") or []):
        acts = [a for fr in (w.get("fragments") or []) for a in (fr.get("actions") or [])
                if str(a.get("actionType", "")).upper() == "SPAWN"]
        if not acts:
            continue
        line = []
        for a in acts:
            line.append(f"+{a.get('preDelay')}s x{a.get('count')} {a.get('key')} 路线{a.get('routeIndex')}")
            spawns[a.get("key")] += a.get("count") or 1
        print(f"  波{wi}: " + "; ".join(line))

    # 敌人属性
    ekey = {}
    print("\n=== 敌人 ===")
    for k, n in spawns.items():
        lv = (ENEMIES_BY_KEY.get(k) or [{}])[0].get("enemyData") or {}
        ab = lv.get("attributes") or {}
        e = {"name": unwrap(lv.get("name")) or k, "hp": unwrap(ab.get("maxHp")), "atk": unwrap(ab.get("atk")),
             "df": unwrap(ab.get("def")), "res": unwrap(ab.get("magicResistance")),
             "spd": unwrap(ab.get("moveSpeed")), "bat": unwrap(ab.get("baseAttackTime")),
             "wt": unwrap(ab.get("massLevel")), "desc": (unwrap(lv.get("description")) or "")[:80], "n": n}
        ekey[k] = e
        print(f"  {e['name']:<10} HP{e['hp']:<7} 攻{e['atk']:<5} 防{e['df']:<6} 法抗{e['res']:<5} "
              f"移速{e['spd']} 攻间隔{e['bat']}s 重量{e['wt']} ×{n}")
        if e["desc"]:
            print(f"      描述: {e['desc']}")

    # 我方输出
    rows = []
    for nm, o in box.items():
        if nm not in N2I:
            continue
        p = panel_at(nm, o["elite"], o["level"])
        kind, idx, atk_buff, scale, idelta = dmg_kind(nm)
        atk = p["atk"] * (1 + atk_buff)
        interval = max(0.3, p["interval"] + idelta)
        dps = {}
        for k, e in ekey.items():
            if e["hp"] is None:
                continue
            dps[e["name"]] = hit(atk * scale, e, kind) / interval
        rows.append({"op": nm, "elite": o["elite"], "level": o["level"], "kind": kind, "atk": atk,
                     "interval": interval, "dps": dps})
    rows.sort(key=lambda r: max(r["dps"].values() or [0]), reverse=True)
    print(f"\n=== 本号输出位（技能倍率按专三，未含信赖/模组；前 {args.top} 名）===")
    names = list(ekey.values()) and [e["name"] for e in ekey.values()]
    print(f"    {'干员':<10}{'练度':<10}{'类型':<6}" + "".join(f"{n[:6]:>10}" for n in names))
    for r in rows[:args.top]:
        print(f"    {r['op']:<10}精{r['elite']}Lv{r['level']:<5}{r['kind']:<6}"
              + "".join(f"{r['dps'].get(n, 0):>10.0f}" for n in names))
    out = PROJ / "data" / f"value_{args.stage}.json"
    out.write_text(json.dumps({"stage": args.stage, "enemies": ekey, "rows": rows},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n明细：{out}")


if __name__ == "__main__":
    main()
