#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""坐标导航（"用眼睛代替 OCR"）—— 2026-09-24

背景（用户的判断很对）：导航慢，是因为 `mt_nav` 每换一屏都要**全屏 OCR** 去找
`OPERATION PV-EX-x` 这种标签（一次 1.3~2.7 秒）。而这些地图的节点位置**是固定**的 ——
我直接看一张截图就能把每个节点的坐标读出来，脚本只要"点坐标 + 模板确认面板"，
根本不需要 OCR：
    取帧(0.16s) → 判页面(0.03s) → 点坐标(~0) → 等画面变化(轮询) → 模板确认(0.03s)
坐标表由我（模型的眼睛）读图填写；换分辨率/换地图时重新读一次即可。

用法:
    python tools/nav_map.py --stage PV-EX-3                # 点过去并确认
    python tools/nav_map.py --stage PV-EX-3 --json
    python tools/nav_map.py --list                          # 看已收录的地图
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
sys.path.insert(0, str(ROOT / "tools"))

# ── 坐标真源：data/nav_nodes.json ─────────────────────────────────────────────
# ⚠️ 这里原来有两张「我读图填的」手写坐标表（`NODES` / `ZONE_CARDS`），但本文件早就改成
#    薄壳（转发服务的 `/nav`，见下面 main()）⇒ 两张表**定义后从未被读**，纯死代码，
#    而且和服务真正读的 data/nav_nodes.json 是**两份会漂移的副本**。
#    2026-09-24 已逐条搬进 json（`NODES["PV"]` → `maps.PV_EX`、`NODES["PV_MAIN"]` →
#    `maps.PV`、`ZONE_CARDS["PV"]` → `zones.PV`），校验证据见
#    docs/audits/20260924_1515_nav_coords.md。
#    ⇒ **改坐标只改 json**；服务每次调用都重读它，别在本文件里再建第二份表。
DATA = ROOT / "data" / "nav_nodes.json"


def load_cfg() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def _is_xy(p) -> bool:
    """`[x, y]` 坐标对（`views` / `normalize` 这些长度 2 的列表不要误判成坐标）。"""
    return (isinstance(p, list) and len(p) == 2
            and all(isinstance(i, (int, float)) and not isinstance(i, bool) for i in p))




def svc(path: str, timeout: float = 10.0):
    import urllib.parse
    import urllib.request
    # ⚠️ 路径里带中文（want=普通）必须 URL 编码，否则 urllib 抛
    #    UnicodeEncodeError: 'ascii' codec can't encode ...（2026-09-24 踩过）
    if "?" in path:
        base, q = path.split("?", 1)
        pairs = [kv.split("=", 1) for kv in q.split("&") if "=" in kv]
        path = base + "?" + urllib.parse.urlencode([(k, v) for k, v in pairs])
    with urllib.request.urlopen("http://127.0.0.1:8765" + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def shot(p: pathlib.Path):
    svc(f"/shot?path={p}")
    return p


def what() -> dict:
    """**一次调用拿全**：页面指纹 + 星星 + 徽章（服务端一次取帧算完，实测 ~230ms）。

    之前是 `/shot` 落盘(1.5MB) + cv2.imread + 每次重新读模板 ⇒ 单次判页面 ~450~540ms。
    """
    return svc("/all")


def page(p: pathlib.Path | None = None):
    """兼容旧签名：返回 (页面信息, None)。现在直接走服务，不落盘。"""
    return what(), None


SRC_LABEL = {"table": "坐标表命中", "learned": "模型坐标库", "auto_scan": "自动试点",
             "none": "无坐标", "unknown": "未知"}


def coord_src_of(stage: str, r: dict) -> tuple[str, str]:
    """`(coord_src, 来源)` —— 区分「坐标表命中」与「自动试点/学习」。

    服务端**目前不返回** `coord_src`（加字段要重启 8765 ⇒ 本次先不动服务，diff 建议见
    docs/audits/20260924_1515_nav_coords.md）：所以这里按 `/nav` 的返回推断；
    服务端将来给了这个字段就**原样透传**（`r["coord_src"]` 优先）。
    """
    if r.get("coord_src"):
        return str(r["coord_src"]), "server"
    if r.get("from_learned_nodes"):
        return "learned", "cli"          # runtime/learned/nodes_<前缀>.json 命中
    if r.get("learned_node"):
        return "auto_scan", "cli"        # 自动试点命中，并已写进 learned
    if not r.get("point"):
        return "none", "cli"
    try:
        maps = (load_cfg().get("maps") or {})
    except Exception:                    # noqa: BLE001
        return "unknown", "cli"
    prefix = stage.split("-")[0]
    table = maps.get(f"{prefix}_EX", {}) if "-EX-" in stage else maps.get(prefix, {})
    if isinstance(table, dict):
        if stage in table or any(stage in (v.get("nodes") or {}) for v in (table.get("views") or [])):
            return "table", "cli"
    return "auto_scan", "cli"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="")
    ap.add_argument("--want", default="普通")
    ap.add_argument("--zone", default="", help="切到哪个区域卡（EX / 主区 / S）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verify", action="store_true", help="（校准已内置，保留兼容）")
    a = ap.parse_args()
    if a.list:
        cfg = load_cfg()
        total = 0
        for k, v in (cfg.get("maps") or {}).items():
            if not isinstance(v, dict):
                continue
            nodes = dict(v.get("nodes") or {})
            for view in v.get("views") or []:
                for code, pt in (view.get("nodes") or {}).items():
                    nodes.setdefault(code, pt)
            total += len(nodes)
            warn = ""
            if any(not (isinstance(p, list) and len(p) == 2
                        and 0 <= p[0] < 1280 and 0 <= p[1] < 720) for p in nodes.values()):
                warn += "  ⚠ 有坏点/越界坐标"
            # 服务当前读**平铺**（maaorch_service.py:776 `table.get(stage)`），
            # 只有带 views 的图读 `.nodes` ⇒ 两者必须一致，否则服务点不到。
            flat = {c: p for c, p in v.items() if _is_xy(p)}
            if flat and {c: [int(p[0]), int(p[1])] for c, p in flat.items()} != \
                    {c: [int(p[0]), int(p[1])] for c, p in nodes.items()}:
                warn += "  ⚠ nodes 与同层平铺不一致"
            print(k, "->", list(nodes), f"（{len(nodes)} 个）" + warn)
        zs = cfg.get("zones") or {}
        print(f"合计 {total} 个节点坐标；zones: "
              + ", ".join(f"{k}({len(v)} 张卡)" for k, v in zs.items() if isinstance(v, dict)))
        return 0

    if not a.stage:
        print(json.dumps({"ok": False, "detail": "没给 --stage"}, ensure_ascii=False))
        return 1

    # **直接调常驻服务**（2026-09-24）：帧/模板/adb shell/校准库全在服务里常驻，
    #   省掉每次新起 Python 的 0.4~0.8s；CLI 只当薄壳，用于手工排查。
    try:
        q = f"/nav?stage={a.stage}&want={a.want}" + (f"&zone={a.zone}" if a.zone else "")
        r = svc(q, timeout=90)
    except Exception as e:                       # noqa: BLE001
        r = {"ok": False, "detail": f"服务不可用：{type(e).__name__}: {e}"}
    # 坐标来源：服务端给了就透传，没给就按返回值推断（「坐标表命中」还是「自动试点」）
    r = dict(r)
    src, src_origin = coord_src_of(a.stage, r)
    r.setdefault("coord_src", src)
    r.setdefault("coord_src_from", src_origin)
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        chk = r.get("stage_check") or {}
        extra = ""
        if chk:
            extra = ("｜身份核对 ✓" + (f"（模板 {chk.get('stage_score')}）" if chk.get("stage_score") else
                                      (f"（OCR {chk.get('stage_ocr')}，已学模板）" if chk.get("learned") else "")))
        label = SRC_LABEL.get(str(r.get("coord_src")), str(r.get("coord_src")))
        print(f"{'✓' if r.get('ok') else '✗'} {a.stage}｜页面 {r.get('page_before')} → {r.get('page_after')}"
              f"｜点 {r.get('point')}{extra}｜来源 {label}"
              f"{'' if r.get('coord_src_from') == 'server' else '（CLI 推断）'}"
              f"｜合计 {r.get('total_ms')}ms"
              + (f"｜{r.get('detail')}" if r.get("detail") else ""))
    return 0 if r.get("ok") else 1

if __name__ == "__main__":
    raise SystemExit(main())
