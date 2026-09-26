#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""story_scan.py —— 主线章节地图**节点清点**（顺带回答"逐关星数能不能从地图读"）。

**2026-09-24 实测结论（很重要，别再走回头路）**：主线地图的**节点颜色不编码三星星数**。
证据：`runtime/_y.png` 是**第一章（46/46 全清）**的地图，视觉模型（modlens，原始结果存
`runtime/reports/_ocr_y_nodes.json`）在同一屏上给出的结论是：
    · `TUTORIAL TR-9 / TR-10` —— 蓝色六边形 + 书本图标
    · `OPERATION 1-6 / 1-7 / 1-8` —— **灰色**六边形 + 靶心图标，且"三颗小星不在可见范围内"
⇒ 事件地图那套"蓝=普通三星 / 白=全过 / 灰=未三星"（`maaorch_service.map_scan`）**不能搬到主线**；
逐关星数只能从**章节关卡列表那一屏**（完成进度→点章节卡，或逐关点进详情页）读。

所以本工具**只做清点 + 颜色观察**，默认**不做状态判定**：
    · `codes[代号].node_color`  附近六边形的颜色观察（**只是观察**，不代表星数）
    · `--color-is-state`        显式打开旧语义（**只在事件地图上成立**，主线别用）
    · `--merge`                 没有 `--color-is-state` 时**拒绝**写回 story_mines.json，
                                免得把假结论写进缺口清单

用法:
    python tools/story_scan.py --ep 1 --shot runtime/_y.png    # 离线重放（清点第一章地图）
    python tools/story_scan.py --ep 7                          # 真机：当前屏停在第 7 章地图
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "tools"))

import cv2                                                     # noqa: E402
import numpy as np                                             # noqa: E402
import main_nav as mn                                          # noqa: E402

LEARNED = PROJ / "runtime" / "learned"
MINES = PROJ / "runtime" / "plans" / "story_mines.json"
EXTRA_PAT = re.compile(r"\b(?:OPERATION|TUTORIAL|STORY)?\s*((?:[A-Z]{1,3})?\d{1,2}-\d{1,2}|TR-\d{1,2})\b")


def detect_hexes(img) -> list[dict]:
    """通用蓝/白六边形检出（口径同 `maaorch_service.nodes_scan`，2026-09-24 验证过）。

    注意：这套参数是给**事件地图**调的；主线地图上 OPERATION 节点本身是灰的，
    所以这里检出的多半是 TUTORIAL 之类的蓝节点 —— 这也是"不能拿它判主线星数"的原因。
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = (hsv[:, :, 0].astype(np.int16), hsv[:, :, 1].astype(np.int16),
               hsv[:, :, 2].astype(np.int16))
    out = []
    for mask, kind in ((((h >= 95) & (h <= 125) & (s >= 140) & (v >= 150)), "蓝"),
                       (((s <= 40) & (v >= 235)), "白")):
        m8 = (mask.astype(np.uint8) * 255)
        m8 = cv2.morphologyEx(m8, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        n, _lab, stats, cents = cv2.connectedComponentsWithStats(m8, 8)
        for i in range(1, n):
            x, y, w, hh, area = stats[i]
            if not (22 <= w <= 70 and 20 <= hh <= 70):
                continue
            if area < 0.45 * w * hh or area > 1.2 * w * hh:
                continue
            if min(x, y) < 30 or y > 700:
                continue
            out.append({"x": int(cents[i][0]), "y": int(cents[i][1]), "kind": kind,
                        "w": int(w), "h": int(hh)})
    return out


def nearest_hex(hexes: list[dict], x: int, y: int, max_dx: int = 130, max_dy: int = 40) -> dict | None:
    best, bd = None, 1e9
    for hx in hexes:
        dx, dy = hx["x"] - x, abs(hx["y"] - y)
        if -max_dx <= dx <= max_dx and dy <= max_dy:
            d = abs(dx) + dy * 2
            if d < bd:
                best, bd = hx, d
    return best


def expected_codes(ep: int) -> list[dict]:
    """该章有哪些关卡。两级来源：① story_mines.json（有缺口时已列好）② 旧表 zoneId=main_<ep>。"""
    if MINES.exists():
        try:
            d = json.loads(MINES.read_text(encoding="utf-8"))
            for ch in d.get("chapters") or []:
                if int(ch.get("ep", -1)) == ep:
                    return ch.get("units") or []
        except Exception:                                        # noqa: BLE001
            pass
    tbl = PROJ / "data" / "_stage_table.json"
    if not tbl.exists():
        return []
    try:
        st = json.loads(tbl.read_text(encoding="utf-8"))["stages"]
    except Exception:                                            # noqa: BLE001
        return []
    out, seen = [], set()
    for sid, rec in sorted(st.items()):
        if rec.get("zoneId") != f"main_{ep}":
            continue
        if str(sid).startswith(("easy_", "st_", "tr_", "spst_", "act")) or not (rec.get("apCost") or 0) > 0:
            continue
        code = str(rec.get("code") or "").upper()
        if not code or code in seen:
            continue
        seen.add(code)
        kind = ("突袭" if rec.get("difficulty") == "FOUR_STAR"
                else "磨难" if (str(sid).startswith("tough_") or rec.get("difficulty") == "SIX_STAR")
                else "H关" if str(sid).startswith("hard_")
                else "S关" if str(sid).startswith("sub_") else "普通")
        out.append({"code": code, "kind": kind, "stage_id": sid, "ap": rec.get("apCost")})
    return out


def scan(ep: int, shot: pathlib.Path | None, color_is_state: bool = False,
         verbose: bool = True) -> dict:
    t0 = time.time()
    img = cv2.imread(str(shot)) if shot else mn.shot("scan")
    if img is None:
        return {"ok": False, "detail": f"取帧失败：{shot}"}
    hits = mn.hits_of(img)
    txt = " ".join(t for _, _, t in hits)
    tabs = mn.episode_tabs(hits)
    if not tabs and not shot:                    # 校准纪律：不在主线地图上就拒绝解读
        return {"ok": False, "detail": f"当前屏不是主线章节地图（没读到 EPISODE 页签）：{txt[:120]}"}
    by_num: dict[str, dict] = {}
    for u in expected_codes(ep):                 # 同代号可能有 普通/突袭 两条 ⇒ 去重
        c = str(u["code"]).upper()
        if c not in by_num or u.get("kind") != "突袭":
            by_num[c] = u
    hexes = detect_hexes(img)
    rows: dict[str, dict] = {}

    def look(code: str, meta: dict) -> dict:
        pos = (mn.find_node(hits, code, x_lo=0, x_hi=1280, y_lo=100, y_hi=700)
               or mn.find_node_any(hits, code))
        if not pos:
            return {"state": "未在屏上", "kind": meta.get("kind"), "stage_id": meta.get("stage_id")}
        hx = nearest_hex(hexes, int(pos[0]), int(pos[1]))
        node_color = (hx or {}).get("kind") or "灰/其它（未检出蓝/白六边形）"
        row = {"node_color": node_color, "hex": ([hx["x"], hx["y"]] if hx else None),
               "label": [int(pos[0]), int(pos[1])], "kind": meta.get("kind"),
               "stage_id": meta.get("stage_id")}
        if color_is_state:                       # 只有显式要求时才给"星数"语义
            row["state"] = (f"{node_color}（普通三星）" if node_color == "蓝"
                            else "白（普通+磨难/突袭都过）" if node_color == "白"
                            else "灰/缺角（未三星）")
        return row

    for code, u in by_num.items():
        rows[code] = look(code, u)
    extra = []
    for m in EXTRA_PAT.finditer(txt.upper()):
        c = m.group(1).strip()
        if c and c not in rows and c not in extra:
            extra.append(c)
    for code in extra:                           # 预期表没有的（或已清章没人列）也记下来
        rows[code] = look(code, {"kind": "屏上读到", "stage_id": None})

    out = {"ok": True, "ep": ep, "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "elapsed_s": round(time.time() - t0, 1), "episode_tabs": sorted(tabs),
           "codes": rows, "屏上其它代号": extra,
           "on_screen": [c for c, r in rows.items() if r.get("state") != "未在屏上"],
           "off_screen": [c for c, r in rows.items() if r.get("state") == "未在屏上"],
           "state_basis": ("map-node-color（**仅在事件地图上成立**）" if color_is_state else
                           "none —— 主线地图节点颜色不代表星数；本工具只做清点"),
           "_note": "node_color 只是颜色观察；证据 runtime/reports/_ocr_y_nodes.json。"}
    if color_is_state:
        out["未三星"] = [c for c, r in rows.items() if str(r.get("state", "")).startswith("灰")]
        out["已三星"] = [c for c, r in rows.items() if str(r.get("state", "")).startswith(("蓝", "白"))]
    if verbose:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def merge_into_mines(rep: dict) -> dict:
    """把扫描结论写回 story_mines.json 的 chapters[].missing_stages（**需要颜色=星数成立**）。"""
    if "未三星" not in rep:
        return {"ok": False, "detail": "拒绝写回：主线地图节点颜色不代表星数（需 --color-is-state，"
                                       "而那只在事件地图上成立）⇒ 逐关星数请看章节关卡列表页"}
    if not MINES.exists():
        return {"ok": False, "detail": "story_mines.json 不存在"}
    d = json.loads(MINES.read_text(encoding="utf-8"))
    n = 0
    for ch in d.get("chapters") or []:
        if int(ch.get("ep", -1)) != rep["ep"]:
            continue
        ch["missing_stages"] = sorted(rep["未三星"])
        ch["missing_stages_source"] = f"tools/story_scan.py @ {rep['scanned_at']}（事件地图口径）"
        ch["state"] = "scanned"
        n += 1
    d.setdefault("_scans", {})[str(rep["ep"])] = {"at": rep["scanned_at"], "未三星": rep["未三星"]}
    MINES.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "chapters_updated": n}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", type=int, required=True, help="第几章（如 7）")
    ap.add_argument("--shot", default="", help="离线重放：用这张截图代替现场取帧")
    ap.add_argument("--color-is-state", action="store_true",
                    help="把节点颜色当星数（**只在事件地图上成立**；主线用了会误判，别用）")
    ap.add_argument("--merge", action="store_true", help="把结论写回 runtime/plans/story_mines.json")
    ap.add_argument("--save", action="store_true", default=True,
                    help="落 runtime/learned/story_state_<ep>.json（清点产物）")
    a = ap.parse_args()
    shot = pathlib.Path(a.shot) if a.shot else None
    if shot and not shot.is_absolute():
        shot = PROJ / shot
    rep = scan(a.ep, shot, color_is_state=a.color_is_state)
    if not rep.get("ok"):
        print(json.dumps(rep, ensure_ascii=False))
        return 1
    if a.save:
        LEARNED.mkdir(parents=True, exist_ok=True)
        p = LEARNED / f"story_state_{a.ep}.json"
        p.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n落库：{p.relative_to(PROJ)}")
    if a.merge:
        print("写回 story_mines.json：" + json.dumps(merge_into_mines(rep), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
