#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_map_panels.py —— 地图**面板预扫**：一次判清"这张图上哪几关真有矿"。

为什么（CV-EX 的实测教训）：原以为"8 个 EX 关 = 8 个矿"，逐关读面板后发现**只有 EX-8 有徽章**。
老流程"进活动 → 逐关导航 → 闸门"会给每关都花一次导航+闸门（还可能有失败重试）⇒ 白跑。
正确顺序：**进图后一次性预扫**（点节点 → `/all` 读面板 → BACK），把结果按 TTL 缓存，
队列只对"真有矿"的关发起导航，其余直接标 done。

判据（对齐 task-13）：
    · `reward_primes=True`（徽章在）                ⇒ **mine**（打）
    · `stars==0 且 cleared==False`（从没打过）      ⇒ **mine**（打）
    · `stars>=1 且 reward_primes=False`（矿已领）   ⇒ **done**（省掉导航+闸门）
    · 点不进详情页 / page≠关卡详情 / 读数为空       ⇒ **unknown**（绝不误判成没矿）

产出：`runtime/learned/panels_<前缀>_<区>.json`（带 `scanned_at` / `ttl_h` / 每关 `verdict`）
用法:
    python tools/scan_map_panels.py --prefix CV --area ex            # 真机：当前停在该地图上
    python tools/scan_map_panels.py --prefix CV --area ex --force-rescan
    python tools/scan_map_panels.py --from runtime/learned/nodes_CV.json       # 老用法
    python tools/scan_map_panels.py --simulate test.json --prefix CV --area ex # 离线：把结果写进缓存
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import time
import urllib.request

SVC = "http://127.0.0.1:8765"
ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
LEARNED = ROOT / "runtime" / "learned"


def svc(path: str, timeout: int = 25, tries: int = 3):
    """调常驻服务，**带重试**（2026-09-24 审计）：服务重启/并发高峰时会短暂
    `ConnectionRefusedError [WinError 10061]`，旧版一次失败就把整轮扫描打断，
    而结果只在最后落盘 ⇒ 前面几十秒全丢。"""
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(SVC + path, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:                                   # noqa: BLE001
            last = e
            if i < tries - 1:
                time.sleep(0.5)
    raise RuntimeError(f"服务不可用（{tries} 次）：{type(last).__name__}: {last}")


def verdict_of(rec: dict) -> str:
    """**纯函数**（队列与测试共用）：一条面板读数 → mine / done / unknown。"""
    if not isinstance(rec, dict):
        return "unknown"
    page = str(rec.get("page") or "")
    if page and page != "关卡详情":
        return "unknown"                       # 没进详情页 ⇒ 不判
    stars, cleared, prime = rec.get("stars"), rec.get("cleared"), rec.get("reward_primes")
    if prime is True:
        return "mine"
    if isinstance(stars, int) and stars >= 1 and prime is False:
        return "done"
    if (stars == 0) and (cleared is False) and prime is not False:
        # 「面板空星 = 从没通关 ⇒ 首通源石还在」（用户口径）。
        # ⚠️ 2026-09-24 审计加的条件 `prime is not False`：**0 星 + 徽章明确不在**是
        #   **自相矛盾**的读数（从没通关的关，正确 ROI 上徽章一定在：EA-1 实测 0.999）
        #   ⇒ 有一项读错了（徽章模板没匹配上 / 面板被遮）。这时必须回到 `unknown` 重扫：
        #   判 mine 会白花理智，判 done 会把矿**永远漏掉**（最坏的一种错）。
        return "mine"
    if prime is False and (stars in (0, None)) and cleared is None:
        return "unknown"
    return "unknown"


def cache_path(prefix: str, area: str) -> pathlib.Path:
    return LEARNED / f"panels_{prefix.upper()}_{area.lower()}.json"


def load_cache(prefix: str, area: str, ttl_h: float = 6.0) -> dict | None:
    """读缓存；过期/损坏返回 None（调用方决定要不要重扫）。"""
    p = cache_path(prefix, area)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                             # noqa: BLE001
        return None
    try:
        ts = time.mktime(time.strptime(str(d.get("scanned_at"))[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:                                             # noqa: BLE001
        ts = p.stat().st_mtime
    # TTL 以**调用方给的为准**（`--panel-ttl`），文件里记的 ttl_h 只作展示/追溯：
    # 之前"文件里的 TTL 覆盖调用方"会让 `--panel-ttl 0` 这类强制重扫失效（自证脚本抓到过）。
    ttl = float(ttl_h if ttl_h is not None else (d.get("ttl_h") or 6.0))
    raw_age_h = (time.time() - ts) / 3600
    d["age_h"] = round(raw_age_h, 2)
    d["ttl_used_h"] = ttl
    d["stale"] = raw_age_h > ttl
    return d


def nodes_from_learned(prefix: str, area: str) -> dict[str, tuple[int, int]]:
    """从 runtime/learned/nodes_<前缀>*.json 取节点坐标（按区过滤：ex 取含 -EX- 的，main 取其余）。"""
    out: dict[str, tuple[int, int]] = {}
    for f in sorted(LEARNED.glob(f"nodes_{prefix.upper()}*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                                         # noqa: BLE001
            continue
        if not isinstance(d, dict):
            continue
        for code, xy in d.items():
            c = str(code).upper()
            if not c.startswith(prefix.upper() + "-"):
                continue
            is_ex = "-EX-" in c
            if (area.lower() == "ex") != is_ex:
                continue
            if isinstance(xy, list) and len(xy) == 2 and int(xy[0]) > 0:
                out[c] = (int(xy[0]), int(xy[1]))
    return out


def panel_code_seen() -> str:
    """读面板标题栏的关卡代号（ROI 与 `maaorch_service.CODE_ROI` 同口径）。"""
    try:
        r = svc("/look?roi=860,80,1010,120")
        return " ".join(r.get("texts") or [])[:40]
    except Exception as e:                                       # noqa: BLE001
        return f"(OCR 失败 {type(e).__name__})"


def code_matches(stage: str, ocr_text: str) -> bool:
    """**复用 service 里唯一那份**（不再抄一份判据）。"""
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        from maaorch_service import code_matches as _cm
        return bool(_cm(stage, ocr_text))
    except Exception:                                            # noqa: BLE001
        t = "".join(c for c in stage.upper() if c.isalnum())
        o = "".join(c for c in (ocr_text or "").upper() if c.isalnum())
        return bool(o) and t[:2] in o


def wait_page(want: str, timeout: float = 3.0) -> dict:
    """轮询等页面变成 `want`（不再用固定 sleep）。返回最后一次 /all。"""
    deadline = time.time() + timeout
    st: dict = {}
    while time.time() < deadline:
        st = svc("/all")
        if st.get("page") == want:
            return st
        time.sleep(0.25)
    return st


def _looks_like_map(prefixes: set[str] | None = None, timeout: float = 15.0) -> bool:
    """兜底判据：**主线/新活动地图**的 `page_of` 是「未知」（不在活动地图模板里），
    所以直接看**屏上有没有关卡代号**（白名条 OCR 的结果）。

    ⚠️ 2026-09-24 独立审计修：这个函数**原来根本不存在** —— `tools/_patch_scan.py` 里的补丁
    没落到本文件，而第 183 行已经在调它 ⇒ 每一关都 `NameError: name '_looks_like_map' is not defined`
    ⇒ **面板预扫（就是"找矿"那个工具）整个失效**：
    `runtime/learned/panels_AD_main.json` / `panels_R8_main.json` 里全是 `unknown` +
    同样的 NameError，就是这么来的。锚点（`page == 活动地图`）只对活动地图成立，
    主线章节地图永远走不到下一行。

    实现：走服务 `/look` 的 OCR（不在此进程冷启动 OCR），用 `stage_boxes` 里**唯一那份**
    代号正则取所有代号；给了 `prefixes` 就要求代号属于其中之一
    （避免"关卡列表里写着代号"的页面被当成地图 —— 那边的身份核对会兜住，但少点一次更省）。
    """
    try:
        txt = " ".join(svc("/look", timeout=timeout).get("texts") or [])
    except Exception:                                            # noqa: BLE001
        return False
    import re as _re
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import stage_boxes as SB                                 # noqa: PLC0415
        pat = SB.CODE_RE_FREE
    except Exception:                                            # noqa: BLE001
        pat = _re.compile(r"([A-Z]{1,3})-(EX-|S-|SP-)?(\d{1,2})")
    found = {f"{m.group(1)}-{m.group(2) or ''}{m.group(3)}"
             for m in _re.finditer(pat, txt.upper().replace(" ", "").replace("OPERATION", ""))}
    if not found:
        return False
    if prefixes:
        return any(c.startswith(p.upper() + "-") for c in found for p in prefixes)
    return True


def scan(nodes: dict[str, tuple[int, int]], per_node_s: float = 0.0) -> dict:
    """真机预扫：**条件** BACK → 确认在地图 → 点节点 → 轮询到详情页 → **核对代号** → 读面板。

    2026-09-24 审计改了四处（都是"把读数记到错关卡头上"的风险源）：
      · BACK **只在"当前还停在关卡详情"时才发**（旧版连第一关都 BACK，会把已进好的活动退出去）；
      · 点之前**必须确认 page==活动地图**，否则这一关记 unknown（绝不乱点乱读）；
      · 点之后**轮询**等详情页（≤3s，替代固定 sleep），并且**核对面板代号 == 这一关**；
      · 身份不符 ⇒ `identity_ok=False` + verdict=unknown（**不许**当成"这关没矿"）。
    """
    out: dict[str, dict] = {}
    t0 = time.time()
    prefixes = {c.split("-")[0] for c in nodes if c}
    for code, (x, y) in nodes.items():
        tn = time.time()
        rec: dict = {}
        try:
            cur = svc("/all")
            if cur.get("page") == "关卡详情":       # 还停在上一个面板 ⇒ 才需要回地图
                svc("/key?code=4")
                cur = wait_page("活动地图", timeout=3.0)
            # **主线地图也要能扫**（2026-09-24 修）：主线章节地图的页面指纹是「未知」
            #   （它不在活动地图模板里），原判据直接跳过 ⇒ 主线全变 unknown。
            #   放宽：只要屏上能看到"关卡代号"（白名条 OCR）就当成地图，继续点节点。
            if cur.get("page") != "活动地图" and not _looks_like_map(prefixes):
                rec = {"page": cur.get("page"), "skipped": True,
                       "detail": f"不在活动地图（{cur.get('page')}）⇒ 跳过，不点不读"}
            else:
                if cur.get("page") != "活动地图":
                    rec["map_check"] = "靠屏上代号放行（page_of=未知）"
                svc(f"/tap?x={x}&y={y}")
                st = wait_page("关卡详情", timeout=max(3.0, per_node_s))
                rec = {k: st.get(k) for k in ("page", "stars", "cleared", "reward_primes",
                                              "reward_score")}
                if st.get("page") == "关卡详情":
                    seen = panel_code_seen()
                    rec["code_on_panel"] = seen
                    rec["identity_ok"] = code_matches(code, seen)
                    if not rec["identity_ok"]:
                        rec["detail"] = f"身份不符：面板读到「{seen}」，不是 {code} ⇒ 不采信"
        except Exception as e:                                     # noqa: BLE001
            rec = {"page": None, "err": str(e)[:120]}
        # 身份不符/没进详情页/跳过 ⇒ 一律 unknown（verdict_of 会把 page!=关卡详情 判 unknown；
        # 身份不符时手动压成 unknown，防止"读到了别人的面板却说这关没矿"）
        rec["verdict"] = "unknown" if rec.get("identity_ok") is False else verdict_of(rec)
        rec["elapsed_s"] = round(time.time() - tn, 1)
        out[code] = rec
        print(f"  {code:10s} page={str(rec.get('page')):6s} stars={rec.get('stars')} "
              f"cleared={rec.get('cleared')} 徽章={rec.get('reward_primes')} "
              f"代号={rec.get('code_on_panel', '-')} ⇒ {rec['verdict']}（{rec['elapsed_s']}s）",
              flush=True)
    return {"nodes": out, "elapsed_s": round(time.time() - t0, 1)}


def save_cache(prefix: str, area: str, nodes: dict, ttl_h: float, elapsed_s: float,
               source: str) -> pathlib.Path:
    p = cache_path(prefix, area)
    doc = {
        "prefix": prefix.upper(), "area": area.lower(),
        "scanned_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "ttl_h": ttl_h, "source": source, "elapsed_s": elapsed_s,
        "nodes": nodes,
        "verdicts": {c: (r.get("verdict") or "unknown") for c, r in nodes.items()},
        "_note": "面板预扫缓存（task-13）：mine=还有矿，done=矿已领，unknown=没读清（绝不误判成没矿）。",
    }
    LEARNED.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="", help="活动前缀（如 CV）；给了就从 runtime/learned/nodes_<前缀>.json 取节点")
    ap.add_argument("--area", default="main", help="区域：main / ex")
    ap.add_argument("--from", dest="from_json", default="", help="直接给节点文件（{代号: [x,y]}）")
    ap.add_argument("--nodes", nargs="*", default=[], help="形如 CV-EX-1=227,262")
    ap.add_argument("--out", default="", help="另外把结果写一份到这个路径（可选）")
    ap.add_argument("--ttl-h", type=float, default=6.0, help="缓存有效期（小时）")
    ap.add_argument("--force-rescan", action="store_true", help="忽略 TTL 强制重扫")
    ap.add_argument("--simulate", default="", help="离线：把这份 {代号:{...}} 结果当扫描结果写进缓存（不碰模拟器）")
    ap.add_argument("--dry-run", action="store_true", help="只打印会扫哪些节点，不点")
    a = ap.parse_args()

    # 离线：直接把结果写进缓存（做证据/复现用）
    if a.simulate:
        if not (a.prefix and a.area):
            print(json.dumps({"ok": False, "detail": "--simulate 需要 --prefix 与 --area"}, ensure_ascii=False))
            return 2
        raw = json.loads((ROOT / a.simulate).read_text(encoding="utf-8"))
        nodes = {}
        for code, rec in raw.items():
            if code.startswith("_"):
                continue
            r = dict(rec)
            r.setdefault("verdict", verdict_of(r))
            nodes[code.upper()] = r
        p = save_cache(a.prefix, a.area, nodes, a.ttl_h, 0.0, f"simulate:{a.simulate}")
        print(json.dumps({"ok": True, "saved": str(p.relative_to(ROOT)),
                          "verdicts": {c: r["verdict"] for c, r in nodes.items()}},
                         ensure_ascii=False, indent=1))
        return 0

    nodes: dict[str, tuple[int, int]] = {}
    if a.prefix:
        cached = load_cache(a.prefix, a.area, a.ttl_h)
        if cached and not cached["stale"] and not a.force_rescan:
            print(json.dumps({"ok": True, "skipped": True,
                              "detail": f"缓存还新鲜（{cached['age_h']}h ≤ TTL {cached['ttl_h']}h）⇒ 不重扫",
                              "cache": str(cache_path(a.prefix, a.area).relative_to(ROOT)),
                              "verdicts": cached.get("verdicts")}, ensure_ascii=False, indent=1))
            return 0
        nodes = nodes_from_learned(a.prefix, a.area)
    if a.from_json:
        d = json.loads((ROOT / a.from_json).read_text(encoding="utf-8"))
        for k, v in d.items():
            if isinstance(v, list) and len(v) == 2 and v[0]:
                nodes[str(k).upper()] = (int(v[0]), int(v[1]))
    for spec in a.nodes:
        k, _, v = spec.partition("=")
        x, y = v.split(",")
        nodes[k.upper()] = (int(x), int(y))
    if not nodes:
        print(json.dumps({"ok": False, "detail": "没有节点可扫（给 --prefix / --from / --nodes，"
                                                 "或先用 tools/learn_map.py 学节点坐标）"},
                         ensure_ascii=False))
        return 1
    if a.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "nodes": sorted(nodes)}, ensure_ascii=False, indent=1))
        return 0

    r = scan(nodes)
    pfx = a.prefix.upper() or (sorted(nodes)[0].split("-")[0] if nodes else "MAP")
    p = save_cache(pfx, a.area, r["nodes"], a.ttl_h, r["elapsed_s"], "live-scan")
    miss = [c for c, v in r["nodes"].items() if v.get("verdict") == "unknown"]
    print(json.dumps({"ok": True, "saved": str(p.relative_to(ROOT)), "count": len(r["nodes"]),
                      "mine": [c for c, v in r["nodes"].items() if v["verdict"] == "mine"],
                      "done": [c for c, v in r["nodes"].items() if v["verdict"] == "done"],
                      "unknown": miss, "elapsed_s": r["elapsed_s"],
                      "per_node_avg_s": round(r["elapsed_s"] / max(len(r["nodes"]), 1), 1)},
                     ensure_ascii=False, indent=1))
    if a.out:
        op = ROOT / a.out
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(r["nodes"], ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
