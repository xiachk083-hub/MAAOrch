# -*- coding: utf-8 -*-
"""MT（act42side 众生行记）关卡导航 + **身份核实** + 难度切换。

为什么不用现成的:
    `bi_state.py` 的「已经在 X 详情页」只看页面特征（开始行动/推荐平均等级/附加条件），
    **不核对关卡代号** ⇒ 站在 MT-EX-7 的详情页上问 MT-EX-8，它会说"已经在 MT-EX-8 的详情页"
    并返回 0（2026-09-22 实测，连问 MT-1 / MT-S-1 / MT-EX-8 全中招）。横扫时这会把 A 关的
    作业拿到 B 关去跑，白花理智。所以这里自己做三件事：
      ① 认章节页签（息止安所=主线 / 瞻望圣堂=S / 谒见神祇=EX），必要时点它 + 左右滑动找节点
      ② 点进节点后，**用详情页标题栏（y<200 的 OPERATION+代号）核实确实是这一关**
      ③ 切难度（普通↔突袭），切不过去就如实说"突袭未解锁"

用法:
    python tools/mt_nav.py --stage MT-EX-7 --want 普通
    python tools/mt_nav.py --stage MT-S-1 --want 突袭
输出（stdout 最后一行）: JSON {"ok":bool,"stage":..,"on_page":..,"difficulty":..,"sanity":..,"detail":..}
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

import cv2
from rapidocr_onnxruntime import RapidOCR

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
OUT = PROJ / "runtime" / "_ce6"
ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
# **端口动态解析**（2026-09-23 修）：实例重启后 adb 端口会从 16416 漂到 16417，
# 写死端口的表现是"截图空文件 → rapidocr LoadImageError"，看不出根因。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _dev import resolve_dev                                       # noqa: E402

DEV = resolve_dev("1")           # #1（官服）
TABS = {"EX": (1080, 666), "MAIN": (938, 659), "S": (1168, 659)}
_engine = None


def shot(tag="nav"):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"_mt_{tag}.png"
    with open(p, "wb") as f:
        subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"], stdout=f, timeout=60)
    return cv2.imread(str(p))


def hits_of(img):
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    res, _ = _engine(img)
    out = []
    for box, text, *_ in (res or []):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append((int(sum(xs) / len(xs)), int(sum(ys) / len(ys)), text))
    return out


def tap(x, y, wait=3.0):
    """点一下 —— **走快导航内核**：常驻 adb shell（省掉每次 adb 客户端 46ms 握手），
    并且不再无脑 sleep，而是**轮询到画面真的变了**就走（实测等待从 3s → 0.2~0.5s）。"""
    try:
        import fastnav
        fastnav.tap_wait(int(x), int(y), max_wait=wait)
        return
    except Exception:
        pass
    subprocess.run([ADB, "-s", DEV, "shell", "input", "tap", str(int(x)), str(int(y))], timeout=30)
    time.sleep(wait)


def swipe(x1, y1, x2, y2, ms=500, wait=2.0):
    try:
        import fastnav
        fastnav.swipe_wait(int(x1), int(y1), int(x2), int(y2), int(ms), max_wait=wait)
        return
    except Exception:
        pass
    subprocess.run([ADB, "-s", DEV, "shell", "input", "swipe",
                    str(x1), str(y1), str(x2), str(y2), str(ms)], timeout=30)
    time.sleep(wait)


def read(tag="nav"):
    img = shot(tag)
    h = hits_of(img)
    return h, " ".join(t for _, _, t in h)


# ── 页面判读 ────────────────────────────────────────────────────────────
def is_detail(hits, txt):
    return any(k in txt for k in ("开始行动", "开始突袭", "推荐平均等级", "附加条件"))


def header_code(hits):
    """详情页**标题栏**里的关卡代号。

    实测（2026-09-22，act42side）：
      · EX 页：OCR 会把标签和代号粘成一个元素 —— `OPERATIoNMT-EX-6`（y≈98）
      · 主线页：会拆成两个 —— `OPERATIONI`(y≈104) + `MT-1`(y≈96)，中间夹着关卡名「圣秩」(y≈132)
      · 而**背后的地图节点**文字在 y≈150 起（EX-1 节点 y=169、主线节点 MT-ST-1 y=207）
    ⇒ 取 y<145 的所有文字拼成一串再正则，两种排版都吃得到，且切掉了地图节点。
    （旧版按元素逐个匹配，主线页的拆分排版认不出来 ⇒ 误判成"认不出这是哪一关"。）
    """
    top = [t for x, y, t in sorted(hits, key=lambda h: h[1]) if y < 145]
    s = "".join(top).upper().replace(" ", "")
    pat = re.escape(PREFIX.upper()) + r"-(?:EX-|ST-|TR-|S-)?\d+"
    m = re.search("(" + pat + ")", s)
    if m:
        return m.group(1)
    # **兜底：整屏找**（2026-09-23 实测必需）——PV/其它活动的面板，代号不一定落在 y<145：
    # 出现过"详情页明明是对的、却因为标题被 OCR 拆碎/错位而判成 `?`，然后自己按返回退出去" ✗。
    # 只在 is_detail（已经在详情页）时才会走到这里，地图上的节点标签不会造成误判。
    s2 = "".join(t for _x, _y, t in hits).upper().replace(" ", "")
    m2 = re.search("(" + pat + ")", s2)
    return m2.group(1) if m2 else ""


def difficulty(hits, txt):
    if ("开始突袭" in txt) or ("普通难度" in txt):
        return "突袭"
    if ("开始行动" in txt) or ("突袭模式" in txt):
        return "普通"
    return "?"


def sanity(txt):
    m = re.search(r"(\d{1,3})\s*/\s*210", txt) or re.search(r"理智\D{0,6}(\d{1,3})", txt)
    return int(m.group(1)) if m else None


PREFIX = "MT"          # 活动关卡代号前缀（--prefix 可改；如空想花庭 HE / 风雪过境 BI）


def chapter_of(code):
    c = code.upper()
    p = PREFIX + "-"
    if p + "EX-" in c or "-EX-" in c:
        return "EX"
    if p + "S-" in c or "-S-" in c:
        return "S"
    return "MAIN"


def chapter_ok(hits, kind):
    """当前这一屏是不是目标区域的关卡列表（代号前缀 = PREFIX）。

    2026-09-23 泛化：原来这里写死 `MT-`，只能用于众生行记；改成用模块级 `PREFIX`
    （`--prefix HE` 就能给空想花庭用），其余逻辑（找节点、核代号、切难度）本来就是通用的。
    """
    p = PREFIX.upper() + "-"
    ts = [t.strip().upper().replace(" ", "") for _, _, t in hits]
    if kind == "EX":
        return any(t.startswith(p + "EX-") for t in ts)
    if kind == "S":
        return any(t.startswith(p + "S-") for t in ts)
    return any(re.fullmatch(re.escape(p) + r"\d+", t) or t.startswith(p + "ST-")
               or t.startswith(p + "TR-") for t in ts)


def norm_code(c: str) -> str:
    """代号归一（与 tools/main_nav.norm_code 同语义的**本地副本**，避免跨模块依赖）：
    去掉章号的零填充（OCR 会给 `07-2`，目标是 `7-2`）。2026-09-25 达芬奇 A2：两导航器
    find_node 都只做字面相等 ⇒ 假「地图上找不到节点」；比较前必须归一。"""
    c = str(c or "").replace(" ", "").upper()
    m = re.match(r"^(?:([A-Z]{1,2}))?0*(\d+)-(.+)$", c)
    if m:
        return f"{m.group(1) or ''}{m.group(2)}-{m.group(3)}"
    return c


def find_node(hits, code):
    for x, y, t in hits:
        if norm_code(t) == norm_code(code) and y > 130:
            return x, y
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, help="关卡代号，如 MT-EX-7 / MT-S-1 / MT-5 / HE-1")
    ap.add_argument("--want", choices=["普通", "突袭"], default=None)
    ap.add_argument("--prefix", default="MT", help="关卡代号前缀（默认 MT；空想花庭=HE，风雪过境=BI）")
    ap.add_argument("--rounds", type=int, default=5, help="整体重试轮数")
    a = ap.parse_args()
    global PREFIX
    PREFIX = a.prefix.upper()
    code = a.stage.upper()
    kind = chapter_of(code)
    res = {"ok": False, "stage": code, "on_page": "", "difficulty": "", "sanity": None, "detail": ""}

    for rnd in range(a.rounds):
        hits, txt = read(f"r{rnd}")
        # 结算页/弹层先点掉
        if any(k in txt for k in ("行动结束", "MISSION RESULTS", "点击画面", "以继续")):
            tap(640, 640)
            hits, txt = read(f"r{rnd}b")

        # ① 在详情页：**只认标题栏的关卡代号**（这就是 bi_state 缺的那一步）
        if is_detail(hits, txt):
            on = header_code(hits)
            if not on:
                # 认不出标题多半是"刚打完/正在切页面"的过渡帧 —— 再拍一张，别据此就按返回
                hits, txt = read(f"r{rnd}re")
                on = header_code(hits)
            res["on_page"] = on or res["on_page"]
            res["sanity"] = sanity(txt) or res["sanity"]
            if on != code:
                # 站在别的关的详情页 / 或认不出标题 —— **绝不许在这时候点页面上的任何东西**
                # （2026-09-22 实测：S 章页签 (1168,659) 和「开始行动」(1123,642) 几乎重合，
                #   在这里失手就会直接把别的关推上编队页）
                res["detail"] = f"这一屏是 {on or '?'} 的详情页，不是 {code} ⇒ 先退出"
                subprocess.run([ADB, "-s", DEV, "shell", "input", "keyevent", "4"], timeout=30)
                time.sleep(2.0)
                continue
            cur = difficulty(hits, txt)
            res["difficulty"] = cur
            if a.want is None or a.want == cur:
                res.update(ok=True, detail=f"已在 {code} 详情页（{cur}）")
                print(json.dumps(res, ensure_ascii=False))
                return 0
            # 切难度：**只在真的 OCR 到那颗开关时才点**（2026-09-23 实测踩坑）
            #   原来有个盲兜底 `tx,ty = 677,643`：在"面板没认出来/不在面板上"的时候会点到
            #   别的界面的按钮上，横扫跑着跑着把游戏带进了**干员页**（日志里出现"稀有度 信赖值 收起"）✗
            #   ⇒ 找不到开关就如实报"切不了"，什么都不点。
            hit = None
            for x, y, t in hits:
                if ("突袭模式" in t) or ("普通难度" in t):
                    hit = (x, y)
                    break
            if hit is None:
                res["detail"] = f"面板上没有『突袭模式/普通难度』开关（现在 {cur}）⇒ 不动手"
                print(json.dumps(res, ensure_ascii=False))
                return 6
            tap(*hit, wait=3.5)
            hits2, txt2 = read(f"r{rnd}sw")
            cur2 = difficulty(hits2, txt2)
            res["difficulty"] = cur2
            if cur2 == a.want:
                res.update(ok=True, detail=f"已切到 {code} 的{a.want}")
                print(json.dumps(res, ensure_ascii=False))
                return 0
            res["detail"] = f"切不过去（现在还是 {cur2}）⇒ 普通多半没通关 / 突袭未解锁"
            print(json.dumps(res, ensure_ascii=False))
            return 5

        # ①b **加载页要等，不要当"不在活动图"**（2026-09-23 补）
        #     每次点完节点/切完难度，游戏都会先出一屏"RHODESISLAND + 一句 tip"的加载页；
        #     旧写法把它当"认不出"⇒ 防护闸直接收手（rc=7）⇒ 横扫在 AD/其它活动上原地卡死 ✗。
        if ("RHODESISLAND" in txt.upper().replace(" ", "")
                or "加载" in txt or "LOADING" in txt.upper()) and not chapter_ok(hits, kind):
            time.sleep(2.5)
            continue

        # ② 编队页（误点「开始行动」的后果）→ 退回去，绝不在这里点任何按钮
        if any(k in txt for k in ("快捷编队", "助战干员", "SUPPORTUNIT", "OPERATIONSTART")):
            res["detail"] = "误入编队页 ⇒ 按返回退出"
            subprocess.run([ADB, "-s", DEV, "shell", "input", "keyevent", "4"], timeout=30)
            time.sleep(2.0)
            continue

        # ②b **危险闸：不在活动图里就一步都别点**（2026-09-23 实测踩过）
        #     大厅/基建/终端等界面上，`TABS` 那三个坐标（右下角）会落在**基建/任务**等按钮上
        #     ⇒ 横扫点着点着就进了基建（日志里出现"进驻总览 / 235/235 810/810"）⇒ 后面全是废动作。
        #     判据：屏上要么有本活动前缀的关卡代号，要么有 「OPERATION」；都没有 ⇒ 收手报错，别乱点。
        if not chapter_ok(hits, kind):
            has_op = "OPERATION" in txt.upper() or any(
                t.strip().upper().startswith(PREFIX.upper() + "-") for _, _, t in hits)
            if not has_op:
                res["detail"] = f"不在活动图里（屏上：{txt[:60]}）⇒ 不点任何东西，先人工导航到活动页"
                print(json.dumps(res, ensure_ascii=False))
                return 7
            continue

        # ③ 地图：认章节 → 找节点
        #    页签坐标 `TABS` 是**众生行记(MT)** 实测值；换别的活动（--prefix HE 等）时
        #    页签位置不同 ⇒ 不去瞎点，直接在当前屏找节点（多数别传的普通关都在同一屏/同一条链上）。
        if not chapter_ok(hits, kind) and PREFIX == "MT":
            tap(*TABS[kind], wait=3.0)
            hits, txt = read(f"r{rnd}c")
            if not chapter_ok(hits, kind):
                # 三个页签都试一遍（页签会随活动进度左右挪）
                for k2 in ("EX", "MAIN", "S"):
                    if k2 == kind:
                        continue
                    tap(*TABS[k2], wait=2.5)
                    hits, txt = read(f"r{rnd}d{k2}")
                    if chapter_ok(hits, kind):
                        break
        node = find_node(hits, code)
        if node is None:                      # 屏上没有 → 左右滑找
            for i in range(3):
                swipe(1100, 400, 150, 400)
                hits, txt = read(f"r{rnd}s{i}l")
                node = find_node(hits, code)
                if node:
                    break
            if node is None:
                for i in range(6):
                    swipe(150, 400, 1100, 400)
                    hits, txt = read(f"r{rnd}s{i}r")
                    node = find_node(hits, code)
                    if node:
                        break
        if node is None:
            res["detail"] = f"地图上找不到 {code} 的节点（章节={kind}）｜屏上文字：{txt[:150]}"
            continue
        tap(node[0], node[1] - 20, wait=4.0)

    if not res["detail"]:
        res["detail"] = f"{a.rounds} 轮内没能进到 {code} 的详情页"
    print(json.dumps(res, ensure_ascii=False))
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
