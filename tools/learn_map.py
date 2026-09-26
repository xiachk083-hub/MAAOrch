#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**一次问视觉模型，拿全一张地图的关卡清单 + 点击坐标** —— 泛化/免维护的收口。

用户 2026-09-24 的判据：地图上"白色方框 + 里面写着关卡代号"就是关卡按钮。
实测（EP 地图）：一次提问就拿到 **8 个节点代号 + 各自白框中心坐标**：
    EP-EX-1(216,158) EP-EX-2(306,282) … EP-EX-8(986,158)

于是：**不用模板、不用 OCR 调参、不用手填坐标、不用版本快照关卡表** ——
对一张从没见过的地图，问一次（~17s）建库，之后按代号查坐标秒点（本地毫秒级）。

产出：`runtime/learned/nodes_<前缀>.json` = {"EP-EX-1": [216,158], …}（学习产物，可重建）
用法:
    python tools/learn_map.py --shot runtime/_map_EPEX.png     # 离线建库
    python tools/learn_map.py                                  # 抓当前屏建库
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
LEARNED = ROOT / "runtime" / "learned"
CODE_RE = re.compile(r"\b([A-Z]{1,3})-(EX-|ST-|SP-|S-)?(\d{1,2})\b")
COORD_RE = re.compile(r"([A-Z]{1,3}-(?:EX-|ST-|SP-|S-)?\d{1,2})[^0-9]{0,120}?\(?\s*(\d{2,4})\s*[,，]\s*(\d{2,4})")

# **踩过的坑**：强制"只输出 JSON"时，模型只肯给代号、不给坐标（实测两次都是 [0,0]）；
#   反而**放开让它描述**时，它会在证据里写出 "Level code: EP-EX-1; white box center coordinates: (216, 158)"
#   —— 那种自由文本才能被正则抓到坐标（EP 八关就是这么拿全的）。所以这里保留"要求坐标"但不禁自由文本。
PROMPT = ("这是 1280x720 的明日方舟活动地图截图。图上有若干关卡节点，每个节点是一个**白色方框**，"
          "框里写着关卡代号（形如 EP-EX-1 / EA-1 / MT-S-2），方框下面还有小字 OPERATION。"
          "请逐个列出**全部**节点，每个都要写成这种形式："
          "“关卡代号 X，白色方框中心坐标 (x, y)”（例如：EP-EX-1，白色方框中心坐标 (216, 158)）。"
          "坐标必须是 1280x720 像素下的整数，不要省略，不要只给代号。")


def ask_vision(shot: pathlib.Path) -> dict:
    """先试结构化字段（semantics.entities 里常带坐标），再退回正则抓原始文本。"""
    sys.path.insert(0, str(ROOT / "tools"))
    import vision as v
    raw = v.ask(shot, PROMPT)
    out: dict[str, list[int]] = {}
    # ① 直接从返回文本里正则抓 "EP-EX-1 ... (216, 158)" 这种
    for m in COORD_RE.finditer(raw):
        out[m.group(1).upper()] = [int(m.group(2)), int(m.group(3))]
    # ② 标准 JSON {"stages":[...]}
    if "{" in raw:
        try:
            s = raw[raw.find("{"): raw.rfind("}") + 1]
            d = json.loads(s)
            for st in (d.get("stages") or []):
                if st.get("code") and st.get("x") is not None:
                    out[str(st["code"]).upper()] = [int(st["x"]), int(st["y"])]
        except Exception:
            pass
    # ③ 退一步：只要代号（坐标拿不到也有用：至少知道这图有哪几关）
    if not out:
        for m in CODE_RE.finditer(raw.upper().replace(" ", "")):
            out.setdefault(f"{m.group(1)}-{m.group(2) or ''}{m.group(3)}", [0, 0])
    return out


def normalize_prefix(stages: dict, prefix: str) -> tuple[dict, list[str]]:
    """**统一补前缀 + 按前缀校验**（2026-09-24 实测：落库出现过 `ST-1` 而不是 `EA-ST-1`）。

    规则：key 已经是 `<prefix>-` 开头 → 原样；否则（如 `ST-1` / `EX-3`）一律补成 `<prefix>-<key>`，
    并回报 fixed 列表。补完再校验一遍，任何不符合 `代号-数字` 形状的丢掉并报告。
    """
    out: dict[str, list[int]] = {}
    fixed: list[str] = []
    for k, v in stages.items():
        k2 = str(k).upper().strip().replace(" ", "")
        if not k2.startswith(prefix + "-"):
            fixed.append(f"{k2}→{prefix}-{k2}")
            k2 = f"{prefix}-{k2}"
        if not re.fullmatch(r"[A-Z]{1,3}-(?:EX-|ST-|SP-|S-)?\d{1,2}", k2):
            fixed.append(f"丢弃(形状不对):{k2}")
            continue
        out[k2] = v
    return out, fixed


def parse_from_modlens(d: dict) -> dict:
    """离线重放：从一份已存的 modlens 结果 JSON 里抽代号+坐标（不再调模型）。

    为什么：视觉调用 ~20-40s 且偶发失败（实测一次返回空）；有了这个入口，落库逻辑
    （补前缀/前缀校验/合并/清理旧 key）就能**确定性复测**。
    """
    res = d.get("result") or {}
    text = "\n".join(str(t) for t in (
        (res.get("ocr") or {}).get("full_text") or "",
        res.get("summary") or "",
        json.dumps(res.get("semantics") or {}, ensure_ascii=False),
    ) if t)
    out: dict[str, list[int]] = {}
    for m in COORD_RE.finditer(text):
        out[m.group(1).upper()] = [int(m.group(2)), int(m.group(3))]
    for m in CODE_RE.finditer(text.upper().replace(" ", "")):
        out.setdefault(f"{m.group(1)}-{m.group(2) or ''}{m.group(3)}", [0, 0])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", default="")
    ap.add_argument("--from-json", default="", help="离线重放：用一份已存的 modlens 结果 JSON 代替现场识别")
    ap.add_argument("--save", action="store_true", default=True)
    ap.add_argument("--prefix", default="", help="活动前缀（如 EA/EP）；不给就从学到的代号里取多数派")
    ap.add_argument("--replace", action="store_true", help="覆盖旧库（默认是**合并**，多区域可累积）")
    ap.add_argument("--fix-prefixes", action="store_true",
                    help="只修已有库：把 nodes_<前缀>.json 里少前缀的 key 补成 <前缀>-<key> 并清掉重复")
    a = ap.parse_args()
    if a.fix_prefixes:
        pre = (a.prefix or "").upper()
        if not pre:
            print(json.dumps({"ok": False, "detail": "--fix-prefixes 需要 --prefix"}, ensure_ascii=False))
            return 1
        p = LEARNED / f"nodes_{pre}.json"
        if not p.exists():
            print(json.dumps({"ok": False, "detail": f"{p} 不存在"}, ensure_ascii=False))
            return 1
        old = json.loads(p.read_text(encoding="utf-8"))
        pruned = [k for k in old if not str(k).startswith(pre + "-")]
        new, fixed = normalize_prefix(old, pre)
        p.write_text(json.dumps(dict(sorted(new.items())), ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"ok": True, "prefix": pre, "before": len(old), "after": len(new),
                          "prefix_fixed": fixed, "pruned_stale_keys": pruned, "saved": str(p),
                          "stages": new}, ensure_ascii=False, indent=1))
        return 0
    if a.from_json:
        p = pathlib.Path(a.from_json)
        if not p.is_absolute():
            p = ROOT / p
        stages = parse_from_modlens(json.loads(p.read_text(encoding="utf-8")))
        shot = p
        a.shot = str(p)
    elif a.shot:
        shot = pathlib.Path(a.shot)
        if not shot.is_absolute():
            shot = ROOT / shot
    else:
        shot = ROOT / "runtime" / "_map.png"
        urllib.request.urlopen(f"http://127.0.0.1:8765/shot?path={shot}", timeout=25).read()

    if not a.from_json:
        stages = ask_vision(shot)
    # **过滤垃圾**（2026-09-24 实测教训）：如果在「乐章详情」这种非地图页上调用，
    #   模型会硬凑出代号但给不出坐标 ⇒ 落库成 [0,0]，把库污染成没用的东西。
    #   规则：坐标必须像真的（x>20 且 y>20）；剩下不到 1/3 就判定"这不是地图"。
    good = {k: v for k, v in stages.items() if v and v[0] > 20 and v[1] > 20}
    if len(good) < max(1, len(stages) // 3) or not good:
        print(json.dumps({"ok": False, "detail": "当前屏不是活动地图（坐标全无效）⇒ 不落库，先导航到地图",
                          "raw": stages}, ensure_ascii=False)[:400])
        return 1
    stages = good
    counts = collections.Counter(k.split("-")[0] for k in stages)
    pre = (a.prefix or (counts.most_common(1)[0][0] if counts else "")).upper()
    if not pre:
        print(json.dumps({"ok": False, "detail": "认不出前缀，请显式 --prefix"}, ensure_ascii=False))
        return 1
    stages, fixed = normalize_prefix(stages, pre)
    if not stages:
        print(json.dumps({"ok": False, "detail": "补前缀后没有合法代号", "fixed": fixed}, ensure_ascii=False))
        return 1
    LEARNED.mkdir(parents=True, exist_ok=True)
    p = LEARNED / f"nodes_{pre}.json"
    merged = {}
    pruned = []
    if p.exists() and not a.replace:
        try:
            merged = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                     # noqa: BLE001
            merged = {}
        # **按前缀校验旧库**（2026-09-24）：老 bug 留下的 `ST-1` 这类少前缀 key 直接清掉，
        #   否则它会和新写进去的 `EA-ST-1` 并存，把同一个节点算成两个。
        for k in list(merged):
            if not str(k).startswith(pre + "-"):
                pruned.append(k)
                merged.pop(k)
    before = len(merged)
    merged.update(stages)
    p.write_text(json.dumps(dict(sorted(merged.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "prefix": pre, "count": len(stages), "merged_total": len(merged),
                      "replaced": bool(a.replace), "prefix_fixed": fixed, "pruned_stale_keys": pruned,
                      "before": before, "saved": str(p),
                      "stages": stages, "_note": "key 一律带前缀，落库前已按前缀校验并清理旧的无前缀 key"},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
