#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行时"自己发现关卡" —— 让项目**不携带任何游戏内数据**（用户 2026-09-24 的要求）。

为什么要它：游戏会更新、会加活动，仓库里那份 `data/_stage_table.json`（19.6MB 关卡表）本质上是
**某一版本的游戏数据快照**，迟早过期 ⇒ 必须维护。正确做法是**运行时从画面上读**：
    · 关卡代号（EA-1 / EP-EX-3 …）→ 地图上白框里就写着（OCR 一次；认不出就问视觉模型）
    · 理智消耗 → 关卡面板右下角"开始行动 −15"里就写着（`/panel` 顺带读；读不到就省略）
    · 节点位置 → 六边形/白框自动检出（`/nodes`），不手填坐标
    · 地图上**实际有几个节点** → 用来和学到的清单对齐（少了就告警重学，不静默用残缺清单）

产出 `runtime/learned/stages_<前缀>.json`（**学习产物，不是随仓库发布的游戏数据**）：
    {"prefix":"EA", "areas":{"main":{...}, "ex":{...}}, "codes":[...], "ap":{...},
     "hard":{...}, "white_box":{...}, "expected_from_map":N, "warnings":[...]}
队列 `runtime/_prime_queue3.py` 优先读它、读不到才退回旧表 ⇒ 新活动/新关卡**零维护**接入。

用法:
    python tools/discover_stages.py                                   # 当前屏（服务常驻 8765）
    python tools/discover_stages.py --prefix EA --area main --save     # 当前屏落库
    python tools/discover_stages.py --shot runtime/_map_EPEX.png --prefix EP --area ex --save   # 离线重放
    python tools/discover_stages.py --shots runtime/_map_LEEX_left.png,runtime/_map_LEEX_mid.png \\
        --prefix LE --area ex --save                                  # 多张平移帧取并集
    python tools/discover_stages.py --prefix CV --zones all --save     # live 切区逐个学
        #  ⚠️ 必须**站在活动地图上**：会用 data/nav_nodes.json 的 zones.<前缀> 点区域卡（唯一碰模拟器的一步）。
        #  用来解「证据只存在于一个区 ⇒ 另一个区永远排不到产 ⇒ 永远学不到」的死锁（CV/EP 主区）。

`--shot/--shots` 走的是**本地 OCR**（rapidocr，队列自己那套引擎；关卡代号是白框里的 ASCII，
正是它的舒适区），**不需要 modlens 视觉后端**、不占模拟器；本地 OCR 认不出才回落到视觉模型。
`--prefix` 是单值：**一次只学一个前缀**（逗号列表会 0 codes）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys
import time
import urllib.request

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
LEARNED = ROOT / "runtime" / "learned"
NAV_CFG = ROOT / "data" / "nav_nodes.json"
# **区域卡名 → 区的键名**：`data/nav_nodes.json` 的 `zones.<前缀>` 是「主区 / EX / S」三张卡的坐标，
#   而落库用的区区键是 main/ex（和队列 `"-EX-" in code` 的口径一致）。
ZONE_AREA = {"主区": "main", "EX": "ex", "S": "s"}
# **`TR-` 是真代号**（AD-TR-1 / PV-TR-1 / EA-TR-1：本地 OCR 实测 0.99 就读得出来），原来漏了它
# ⇒ 训练关永远进不了清单（`stages_EA.json` 的 diag 里 OCR 明明读到 EA-TR-1，落库时被丢掉）。
CODE_RE = re.compile(r"\b([A-Z]{1,3})-(EX-|ST-|SP-|S-|TR-)?(\d{1,2})\b")
SVC = "http://127.0.0.1:8765"


def svc(path: str, timeout: int = 30):
    with urllib.request.urlopen(SVC + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def norm(code: str) -> str:
    return re.sub(r"\s+", "", str(code)).upper().replace("--", "-")


def rel(p) -> str:
    return str(p.relative_to(ROOT)) if ROOT in p.parents else str(p)


def keep(codes: list[str], prefix: str) -> list[str]:
    """按前缀过滤，并修 OCR 粘连（`ILE-EX-6` ⇒ `LE-EX-6`）。

    为什么不用 `startswith(prefix+"-")`：白框左边的图标/装饰会被 OCR 粘进同一个 token，
    实测 `_map_LEEX_mid.png` 的 `LE-EX-6` 就是读成 `ILE-EX-6` 的 —— 用 startswith 会把它
    **整个丢掉**，而且丢得静默（清单少一关，看起来像"图上本来就没有"）。
    """
    rx = re.compile(rf"({re.escape(prefix)}-(?:EX-|ST-|SP-|S-|TR-)?\d{{1,2}})\b")
    out = set()
    for c in codes:
        m = rx.search(str(c))
        if m:
            out.add(m.group(1))
    return sorted(out)


def zones_of(prefix: str) -> dict[str, list[int]]:
    """`data/nav_nodes.json` 的 `zones.<前缀>` ⇒ `{main: [x, y], ex: [x, y], s: [...]}`。

    为什么需要它：**只有站在活动地图上、点中区域卡，才学得到那个区的关卡清单**。而队列
    `maybe_learn_stages` 只学"当前单位所在的区"（area 由代号猜），于是"证据只存在于一个区"
    的活动，另一个区**永远学不到**（CV-1..8 / EP-1..8 实测 0 证据、全沉 tier 2）。
    区域卡坐标本来就有 ⇒ 切区只值一次点击，不需要额外占机。
    """
    try:
        cfg = json.loads(NAV_CFG.read_text(encoding="utf-8"))
    except Exception:                                     # noqa: BLE001
        return {}
    out: dict[str, list[int]] = {}
    for k, v in ((cfg.get("zones") or {}).get(prefix.upper()) or {}).items():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            out[ZONE_AREA.get(str(k), str(k).lower())] = [int(v[0]), int(v[1])]
    return out


def tap(x: int, y: int, wait: float = 2.5) -> bool:
    """走服务 `/tap` 点一下（`/tap` 不会自己等画面 ⇒ 这里本地 sleep）。

    ⚠️ 这是**唯一**会碰模拟器的一步：只有在活动地图上、且真的需要切区时才调用。
    """
    try:
        svc(f"/tap?x={int(x)}&y={int(y)}")
    except Exception:                                     # noqa: BLE001
        return False
    time.sleep(wait)
    return True


def codes_from_texts(texts: list[str]) -> list[str]:
    blob = " ".join(texts)
    return sorted({norm(f"{m.group(1)}-{m.group(2) or ''}{m.group(3)}") for m in CODE_RE.finditer(blob)})


def _modlens_json(shot: pathlib.Path, question: str) -> dict:
    """直接调 modlens CLI 拿**结构化结果**（result.ocr.full_text / semantics）。

    为什么不用 tools/vision.ask 的返回值：它按"要 JSON 就返回原文"的启发式来，而 modlens 的
    result 里并没有 `text` 字段（只有 summary/ocr/layout/semantics）⇒ 实测会返回整包 stdout，
    正则抓不到代号。这里直接解析它自己的 JSON。
    """
    skill = pathlib.Path.home() / ".agents" / "skills" / "modlens" / "scripts" / "run.ps1"
    if not skill.exists():
        return {}
    import subprocess                                     # noqa: PLC0415
    cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(skill),
           "-i", str(shot), "--prompt", question, "--timeout", "300000"]
    try:
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=420)
    except Exception:                                     # noqa: BLE001
        return {}
    out = (r.stdout or "").strip()
    i = out.find("{")
    if i < 0:
        return {}
    try:
        return json.loads(out[i:])
    except Exception:                                     # noqa: BLE001
        return {}


def codes_from_local_ocr(shot: pathlib.Path, prefix: str) -> tuple[list[str], dict, dict]:
    """**本地 OCR**（`infrastructure.ocr` → rapidocr_onnxruntime，队列自己 OCR 用的同一套引擎）。

    为什么要有这条路：`--shot` 离线重放原来**只走 modlens 视觉模型**，那条链一挂（provider 返回
    schema 不匹配 / 400 MissingSessionID）离线学习就全废 —— 而关卡代号是**白框里的 ASCII**
    （MT-EX-3 / LE-EX-7 / AD-TR-1），正是本地 OCR 的舒适区。零网络、零占机、可复现、快。
    实测置信度 0.99+（见 `docs/audits/` 的验证记录）。

    返回 `(codes, box, diag)`；解释器缺 cv2/rapidocr 时返回空 + 原因（不抛）。
    """
    diag: dict = {"local_ocr": 0}
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from infrastructure.ocr import ocr_boxes              # noqa: PLC0415
    except Exception as e:                                    # noqa: BLE001
        diag["local_ocr_error"] = f"{type(e).__name__}: {e}"[:160]
        return [], {}, diag
    try:
        boxes = ocr_boxes(str(shot))
    except Exception as e:                                    # noqa: BLE001
        diag["local_ocr_error"] = f"{type(e).__name__}: {e}"[:160]
        return [], {}, diag
    diag["local_ocr_boxes"] = len(boxes)
    codes = keep(codes_from_texts([str(b.get("text") or "") for b in boxes]), prefix)
    box: dict[str, list[int]] = {}
    for b in boxes:
        k = keep([norm(str(b.get("text") or ""))], prefix)
        if k and b.get("cx") is not None:
            box.setdefault(k[0], [int(b["cx"]), int(b["cy"])])
    box = {k: v for k, v in box.items() if v[0] > 20 and v[1] > 20}
    diag["local_ocr"] = len(codes)
    return codes, box, diag


def codes_from_vision(shot: pathlib.Path, prefix: str) -> tuple[list[str], dict]:
    """问外部视觉模型要关卡代号 + 白框中心（认不出 OCR 时的兜底；也是"白框代号"判据）。"""
    q = (f"这是明日方舟的活动地图截图。请列出图上**全部**关卡节点：每个白色方框里的关卡代号"
         f"（形如 {prefix}-1 / {prefix}-EX-3 / {prefix}-ST-1），以及该方框中心在 1280x720 下的整数坐标。"
         "只输出 JSON：{'stages':[{'code':'…','x':123,'y':456}]}"
         "（注意：prompt 里不能出现双引号 —— 经 PowerShell 传参会把参数截断）")
    d = _modlens_json(shot, q)
    res = d.get("result") or {}
    texts = [str(shot)]
    if res:
        texts = [(res.get("ocr") or {}).get("full_text") or "", res.get("summary") or "",
                 json.dumps(res.get("semantics") or {}, ensure_ascii=False)]
    ans = "\n".join(t for t in texts if t)
    codes: list[str] = []
    box: dict[str, list[int]] = {}
    if "{" in ans:
        for s in re.findall(r"\{[^{}]*\"code\"[^{}]*\}", ans):
            try:
                st = json.loads(s)
            except Exception:                             # noqa: BLE001
                continue
            c = norm(st.get("code") or "")
            if c:
                codes.append(c)
                if st.get("x") is not None and st.get("y") is not None:
                    box[c] = [int(st["x"]), int(st["y"])]
    if not codes:                                         # 正则抓 OCR 全文 / 语义证据
        codes = codes_from_texts([ans])
    codes = keep(codes, prefix)
    for m in re.finditer(r"([A-Z]{1,3}-(?:EX-|ST-|SP-|S-|TR-)?\d{1,2})[^0-9]{0,40}?\(?\s*(\d{2,4})\s*[,，]\s*(\d{2,4})",
                         ans.upper()):
        k = keep([norm(m.group(1))], prefix)
        if k:
            box.setdefault(k[0], [int(m.group(2)), int(m.group(3))])
    box = {k: v for k, v in box.items() if v[0] > 20 and v[1] > 20}
    return codes, box


def read_live(prefix: str) -> dict:
    """当前屏：/look 全屏 OCR → 代号；/nodes 六边形；/panel 理智（读不到就算了）。"""
    got, out = {}, {"ocr_texts": [], "page": None, "reward_primes": None}
    try:
        got = svc("/look")
        out["ocr_texts"] = got.get("texts") or []
        out["page"] = got.get("page")
        out["reward_primes"] = got.get("reward_primes")
    except Exception as e:                                # noqa: BLE001
        out["ocr_error"] = str(e)[:120]
    codes = keep(codes_from_texts(out["ocr_texts"]), prefix)
    box: dict[str, list[int]] = {}
    try:
        nd = svc("/nodes")
        for n in nd.get("nodes") or []:
            k = keep([norm(n.get("code") or "")], prefix)
            if k and n.get("x") is not None:
                box[k[0]] = [int(n["x"]), int(n["y"])]
        out["nodes"] = len(nd.get("nodes") or [])
    except Exception as e:                                # noqa: BLE001
        out["nodes_error"] = str(e)[:120]
    return {"codes": codes, "box": box, "diag": out}


def learn(shots: list[pathlib.Path] | None, prefix: str, area: str | None,
          expect: int = 0, expect_from: str = "", partial: str = "") -> dict:
    """学一个前缀的一个区。`shots` 给**同一区**的多张截图 ⇒ 取并集。

    为什么支持多张：地图是要平移才看得全的。实测 `_map_LEEX_left.png` 只有 LE-EX-1/2/3、
    `_map_LEEX_mid.png` 只有 LE-EX-4~7 ⇒ 单张永远学不到"8/8"，而且**看起来像图上就这么多**
    （比"缺文件"更危险：残缺清单会静默把单位判成 tier 2）。
    """
    if shots:
        for s in shots:
            if not s.exists():
                return {"ok": False, "detail": f"截图不存在：{s}"}
        codes: list[str] = []
        box: dict[str, list[int]] = {}
        frames, diags = [], []
        n_ocr = n_vis = 0
        for s in shots:
            lcodes, lbox, diag = codes_from_local_ocr(s, prefix)   # 本地 OCR 优先
            if lcodes:
                n_ocr += len(lcodes)
            else:                                                  # 本地认不出 ⇒ 才问视觉模型
                vcodes, vbox = codes_from_vision(s, prefix)
                n_vis += len(vcodes)
                lcodes, lbox = vcodes, vbox
                diag = dict(diag, vision=len(vcodes))
            frames.append({"shot": rel(s), "n": len(lcodes), "codes": lcodes})
            codes = sorted(set(codes) | set(lcodes))
            for k, v in (lbox or {}).items():
                box.setdefault(k, v)
            diags.append(diag)
        src: dict = {"ocr": n_ocr, "vision": n_vis, "shots": frames,
                     "diag": diags[0] if len(diags) == 1 else diags}
        if len(shots) == 1:
            src["shot"] = rel(shots[0])
        return {"ok": bool(codes), "codes": codes, "box": box, "ap": {}, "source": src,
                "expect": expect, "expect_from": expect_from, "partial_note": partial}
    live = read_live(prefix)
    codes, box = live["codes"], live["box"]
    src = {"ocr": len(codes), "diag": live["diag"]}
    if not codes:                                         # OCR 没读到 ⇒ 截图 + 本地 OCR / 视觉兜底
        shot = ROOT / "runtime" / "_disc.png"
        try:
            svc(f"/shot?path={shot}", timeout=40)
            lcodes, lbox, diag = codes_from_local_ocr(shot, prefix)
            if lcodes:
                codes, box = lcodes, lbox
                src["local_ocr"] = len(lcodes)
            else:
                codes, box = codes_from_vision(shot, prefix)
                src["vision_fallback"] = len(codes)
            src["diag"] = dict(src.get("diag") or {}, **diag)
        except Exception as e:                            # noqa: BLE001
            src["vision_error"] = str(e)[:120]
    # 理智：从 /panel 读（离线重放时没有面板，就省略）
    ap: dict[str, int] = {}
    try:
        pn = svc("/panel")
        c = norm(pn.get("code") or "")
        if keep([c], prefix) and pn.get("ap") is not None:
            ap[keep([c], prefix)[0]] = int(pn["ap"])
    except Exception:                                     # noqa: BLE001
        pass
    return {"ok": bool(codes), "codes": codes, "box": box, "ap": ap, "source": src,
            "expect": expect, "expect_from": expect_from, "partial_note": partial}


def save(prefix: str, area: str | None, got: dict) -> pathlib.Path:
    LEARNED.mkdir(parents=True, exist_ok=True)
    p = LEARNED / f"stages_{prefix}.json"
    old = {}
    if p.exists():
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                 # noqa: BLE001
            old = {}
    areas = dict(old.get("areas") or {})
    key = area or "default"
    areas[key] = {"codes": got["codes"], "updated": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                  "white_box": got["box"]}
    if got.get("partial_note"):        # 局部帧 ⇒ 自报残缺（按区记；完整学过会自动清掉）
        areas[key]["partial"] = got["partial_note"]
    codes = sorted({c for a in areas.values() for c in (a.get("codes") or [])})
    ap = dict(old.get("ap") or {})
    ap.update(got.get("ap") or {})
    nodes_file = LEARNED / f"nodes_{prefix}.json"
    n_nodes = 0
    if nodes_file.exists():
        try:
            n_nodes = len(json.loads(nodes_file.read_text(encoding="utf-8")))
        except Exception:                                 # noqa: BLE001
            n_nodes = 0
    expect = int(got.get("expect") or old.get("expected_from_map") or 0)
    expected = max(len(codes), n_nodes, len(got["box"]), expect)
    warnings = list(old.get("warnings") or [])
    if expect and len(codes) < expect:
        warnings = [f"学到的关卡数 {len(codes)} < 应有 {expect}"
                    f"（{got.get('expect_from') or old.get('expect_from') or '外部给定'}）"
                    f"⇒ 清单残缺，需要重学（换屏/补 area，如 --area ex）"]
    elif n_nodes and len(codes) < n_nodes:
        warnings = [f"学到的关卡数 {len(codes)} < 地图节点数 {n_nodes} ⇒ 清单残缺，需要重学（换屏/补 area）"]
    elif got["source"].get("ocr", 0) == 0 and got["source"].get("vision"):
        warnings = ["OCR 没读到代号，这批是视觉模型给的 ⇒ 建议下次再跑一次交叉验证"]
    else:
        warnings = []
    # **局部帧必须自报残缺**：`maybe_learn_stages` 只在「太旧(12h) 或 warnings 非空(2h)」时才重学
    # ⇒ 用平移帧学出来的**局部**清单如果不带告警，就会**静默锁死 live 重学 12 小时**，
    #   而它恰恰是最需要 live 补全的那种（例：`_ad_map.png` 只覆盖 AD 主区左半 ⇒ 6/10）。
    #   按**区**记（`areas[key].partial`）⇒ 多个区的残缺同时可见，且某区后来被完整学过就自动消失。
    partials = [str(v["partial"]) for v in areas.values()
                if isinstance(v, dict) and v.get("partial")]
    if partials:
        warnings = partials + [w for w in warnings if w not in partials]
    doc = {"prefix": prefix, "updated": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
           "areas": areas, "codes": codes, "ap": ap, "hard": dict(old.get("hard") or {}),
           "white_box": {c: v for a in areas.values() for c, v in (a.get("white_box") or {}).items()},
           "expected_from_map": expected,
           "expect_from": got.get("expect_from") or old.get("expect_from") or "",
           "node_count_from_nodes": n_nodes,
           "source": got["source"], "warnings": warnings,
           "_note": "学习产物（运行时从画面读出来），不是随仓库发布的游戏数据；可随时重学覆盖。"}
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def _learn_zones(a, prefix: str) -> int:
    """**按区域卡逐个区学**（live）：点 `zones.<前缀>` 的卡 → 读当前屏 → 按区落库。

    为什么需要这个模式（而不是让调用方自己点卡）：队列 `maybe_learn_stages` 只学"当前单位
    所在的区"，而 area 是由**单位代号**猜的（`-EX-` ⇒ ex）。于是"证据只存在于一个区"的活动
    会**自己把自愈环路关上**：另一个区的唯一入口（那些单位）正因为"图上无证据"沉在 tier 2、
    永远排不到产 ⇒ 永远没人进那个区 ⇒ 永远学不到（实测 CV/EP：主区各 12 个代号 0 证据，
    `nav_nodes.json` 的 `maps` 里也没有它们的主区坐标 ⇒ 想靠"手工补 areas.main"救，
    只会让那批单位白跑一次导航失败=noreach）。
    区域卡坐标本来就在 `zones` 里 ⇒ 切区只值一次点击，在**已经付过的那次进图**里就能学。

    ⚠️ 只读当前屏（本地 OCR），不额外占机；但会点区域卡 ⇒ 调用方必须确保**现在就站在活动地图上**。
    """
    cards = zones_of(prefix)
    if not cards:
        print(json.dumps({"ok": False, "prefix": prefix,
                          "detail": f"data/nav_nodes.json 的 zones 里没有 {prefix} 的区域卡坐标 ⇒ 切不了区"
                                    f"（EA 就是这样：没有条目，只能站在哪个区学哪个区）"},
                         ensure_ascii=False, indent=1))
        return 2
    want = [z.strip().lower() for z in re.split(r"[,;]", a.zones) if z.strip()]
    names = list(cards) if (not want or want == ["all"]) else [z for z in want if z in cards]
    if not names:
        print(json.dumps({"ok": False, "prefix": prefix, "want": want, "have": sorted(cards),
                          "detail": "要学的区和 zones 里的区域卡对不上"}, ensure_ascii=False, indent=1))
        return 2
    out_zones, saved = [], []
    for i, nm in enumerate(names):
        x, y = cards[nm]
        tapped = tap(x, y, wait=3.0 if i == 0 else 2.5)
        got = learn(None, prefix, nm, expect=a.expect, expect_from=a.expect_from, partial=a.partial)
        codes = got.get("codes") or []
        rec = {"zone": nm, "card": [x, y], "tapped": tapped, "count": len(codes), "codes": codes,
               "page": (got.get("source", {}).get("diag") or {}).get("page")}
        # **区不符就绝不落库**（2026-09-24）：区域卡坐标是"读图填的"，点偏了会**静默把另一个区的
        #   清单写进这个区**（AD 那次误键就是这么发生的：屏在 EX 区、键却写成 main）。判据很硬：
        #   `ex` 区的代号必须带 `-EX-`，`main` 区的代号必须不带 —— 不符就只报不写。
        bad = [c for c in codes if ("-EX-" in c) != (nm == "ex")]
        if codes and bad:
            rec["skipped_save"] = (f"读到的代号与目标区不符（{bad[:5]}）⇒ 区域卡点错了/屏不在该区，"
                                   f"**不落库**（免得污染这个区）")
            out_zones.append(rec)
            continue
        # **空结果绝不落库**：`save()` 是"整区覆盖"，用一张空帧盖掉一个学好的区
        #   = 把那个区的证据静默抹掉（顶层并集当场少一批代号 ⇒ 单位沉 tier 2）。
        if a.save and rec["count"]:
            p = save(prefix, nm, got)
            rec["saved"] = rel(p)
            saved.append(nm)
        elif a.save:
            rec["skipped_save"] = "这个区一张代号都没读到 ⇒ 不落库（免得空帧盖掉学好的区）"
        out_zones.append(rec)
    print(json.dumps({"ok": any(r["count"] for r in out_zones), "prefix": prefix,
                      "zones": out_zones, "saved": saved,
                      "source": "live（服务 /look + 本地 OCR）"}, ensure_ascii=False, indent=1))
    return 0 if any(r["count"] for r in out_zones) else 1


def main() -> int:
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--shot", default="", help="离线重放：用一张已存的地图截图建库（不占模拟器）")
    ap_.add_argument("--shots", default="",
                     help="离线重放（多张，逗号分隔）：**同一区**要几张平移帧才学得全（并集落库）")
    ap_.add_argument("--prefix", default="", help="活动前缀，如 EA / EP（不给就从代号里猜第一个）")
    ap_.add_argument("--area", default="", help="区域名：main / ex（同一前缀可多次学、自动合并）")
    ap_.add_argument("--expect", type=int, default=0,
                     help="该活动**应有**的关卡数（如活动卡 0/56 ⇒ 16 关）——用来告警'学到的比实际少'")
    ap_.add_argument("--expect-from", default="", help="expect 的来源说明（写进文件，便于追）")
    ap_.add_argument("--partial", default="",
                     help="自报'这份清单是局部帧学来的、可能残缺'（写进 warnings ⇒ 保住 2h 后的 live 重学）")
    ap_.add_argument("--zones", default="",
                     help="**live 按区学**：点 data/nav_nodes.json 的 zones.<前缀> 区域卡，逐个区学并落库"
                          "（all=该前缀所有区域卡，或 main,ex）。用来解"
                          "'证据只存在于一个区 ⇒ 另一个区的单位永远排不到产 ⇒ 永远学不到'的死锁")
    ap_.add_argument("--save", action="store_true")
    a = ap_.parse_args()

    shot_paths = [x.strip() for x in re.split(r"[,;]", f"{a.shots},{a.shot}") if x.strip()]
    shots = []
    for x in shot_paths:
        p = pathlib.Path(x)
        shots.append(p if p.is_absolute() else ROOT / p)
    if "," in a.prefix.strip():
        # **说清楚，别让人以为"跑过了"**：`--prefix AD,EA` 会 0 codes（单值 + `f"stages_{prefix}.json"`）。
        print(json.dumps({"ok": False, "detail": "--prefix 只接受一个前缀（落库文件名是 f\"stages_{prefix}.json\"）"
                                               "⇒ 逗号列表必然 0 codes；要批量请在**外面**循环，一次一个"},
                         ensure_ascii=False, indent=1))
        return 2
    prefix = a.prefix.upper()
    if a.zones:
        return _learn_zones(a, prefix)
    got = (learn(shots or None, prefix, a.area or None, expect=a.expect,
                 expect_from=a.expect_from, partial=a.partial)
           if prefix else {"ok": False, "detail": "需要 --prefix"})
    if not got.get("ok"):
        print(json.dumps({"ok": False, "detail": got.get("detail") or "没发现任何关卡代号",
                          "source": got.get("source")}, ensure_ascii=False, indent=1))
        return 1
    out = {"ok": True, "prefix": prefix, "area": a.area or "default",
           "count": len(got["codes"]), "codes": got["codes"], "ap": got["ap"], "source": got["source"],
           "white_box": len(got["box"]), "expect": a.expect or None,
           "warning": (f"学到的 {len(got['codes'])} < 应有 {a.expect} ⇒ 残缺，需要重学"
                       if a.expect and len(got["codes"]) < a.expect else (a.partial or None))}
    if a.save:
        p = save(prefix, a.area or None, got)
        out["saved"] = str(p.relative_to(ROOT))
        out["file"] = json.loads(p.read_text(encoding="utf-8"))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
