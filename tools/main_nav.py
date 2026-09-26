#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main_nav.py —— **主线（主题曲）关卡导航 + 关卡身份核实**（2026-09-23 新写）

为什么需要它（实测，别再走回头路）:
    ① **MAA 的 Copilot 不会自动导航**：2026-09-23 实测三次，把作业 `main_16-01` 交给
       MAA Copilot，它**一次导航任务都没跑**，直接在"当前屏幕上那一关"（残留的 15-20）
       开打并失败 ⇒ 白花理智。MAA 的资源里虽然有 `Copilot@FullStageNavigation`，
       但这个构建里根本没被调用（asst.log 里 grep 不到任何 StageNavigation 任务）。
    ② 仓库里 `mt_nav.py` 是**活动关**的导航，主线用不了。
    ③ 主线唯一的入口是：**终端 → 主题曲卡片 → 章节地图**；章节由地图右下角的
       「EPISODE n」页签切换（第 14/15/16 章同属一个主题曲）。

它做什么:
    1) 认当前在哪：大厅 / 终端 / 章节地图 / 某关详情面板
    2) 不在地图上就从大厅→终端→主题曲卡片进地图（大厅/终端都靠 OCR 认，不猜坐标）
    3) 切到目标 EPISODE（OCR 找右下角那排数字，点它）
    4) 在地图上找目标关卡代号（OCR 找 `16-3` 这种标签，找不到就左右滑）
    5) 点进节点后，**用面板标题栏的 OPERATION 代号核实确实是这一关**（mt_nav 踩过的坑：
       只看"在详情页"不核代号，会把 A 关的作业拿到 B 关去跑）
    6) 输出一行 JSON： {"ok":bool,"code":..,"on_page":..,"sanity":..,"detail":..}

用法:
    python tools/main_nav.py --code 16-3           # 游戏里显示的代号
    python tools/main_nav.py --code 15-3 --raid    # 切「险地作战」那一档
    python tools/main_nav.py --code 16-3 --rounds 6
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

PROJ = pathlib.Path(__file__).resolve().parent.parent
OUT = PROJ / "runtime" / "_nav"
ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"


def resolve_dev(emu: str = "1") -> str:
    """取模拟器 adb 地址 —— **动态解析，别写死端口**。

    2026-09-23 实测踩到：实例重启后 MuMu 12 的 adb 端口从 16416 **漂到 16417**，
    写死 16416 的工具全部开始 `cannot connect (10061)` / `device not found`。
    优先用仓库里经过验证的 `detect_emu_adb`（读 MuMuManager 与 vm_config），失败才兜底 16416。
    """
    try:
        sys.path.insert(0, str(PROJ))
        from infrastructure.task_constants import detect_emu_adb
        addr = detect_emu_adb(emu)
        if addr:
            return addr
    except Exception:                                             # noqa: BLE001
        pass
    return "127.0.0.1:16416"


DEV = resolve_dev("1")           # #1（官服）

# **入口坐标改成"学一次"**（2026-09-24，task-12）：不再写死在代码里 ——
#   默认值仍是 2026-09-23 实测的那两个点，但只要 `runtime/learned/story_nav.json` 里有
#   `entry.terminal` / `entry.theme_card`（可以用 `--learn-entry` 现场学，也可手工写），
#   就以学到的为准。这样换分辨率/改版式时**不用改代码**。
LEARNED_NAV = PROJ / "runtime" / "learned" / "story_nav.json"


def _learned_entry() -> dict:
    try:
        d = json.loads(LEARNED_NAV.read_text(encoding="utf-8"))
        return d.get("entry") or {}
    except Exception:                                             # noqa: BLE001
        return {}


_ENTRY = _learned_entry()
TERMINAL_BTN = tuple(_ENTRY.get("terminal") or (967, 160))       # 大厅 → 终端（2026-09-23 实测）
THEME_CARD = tuple(_ENTRY.get("theme_card") or (723, 329))       # 终端 → 当前主题曲卡片
# **2026-09-24 Lead 真机实测**：终端页那张「MAIN THEME / 进度 / 章节名」卡片**直接开章节地图**，
#   位置 (742,545)，比"主题曲大卡 → 本章回想 → 前往章节"这条老路短，而且**绕开 EPISODE 页签 carousel**。
MAIN_THEME_CARD = tuple(_ENTRY.get("main_theme_card") or (742, 545))
_engine = None

NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def shot(tag="nav"):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"_main_{tag}.png"
    with open(p, "wb") as f:
        subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"], stdout=f, timeout=60,
                       creationflags=NOWIN)
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
    subprocess.run([ADB, "-s", DEV, "shell", "input", "tap", str(int(x)), str(int(y))],
                   timeout=30, creationflags=NOWIN)
    time.sleep(wait)


def swipe(x1, y1, x2, y2, ms=500, wait=2.0):
    subprocess.run([ADB, "-s", DEV, "shell", "input", "swipe",
                    str(x1), str(y1), str(x2), str(y2), str(ms)], timeout=30, creationflags=NOWIN)
    time.sleep(wait)


def back(wait=2.0):
    subprocess.run([ADB, "-s", DEV, "shell", "input", "keyevent", "4"], timeout=30,
                   creationflags=NOWIN)
    time.sleep(wait)


def learn_entry(kind: str, shot_path: pathlib.Path | None = None, xy: tuple[int, int] | None = None) -> dict:
    """**学一个入口坐标**（task-12 要求 2：坐标只在必要时学一次，落 runtime/learned/）。

    kind ∈ {terminal, theme_card}：
      · terminal   —— 大厅里「终端」按钮（默认 (967,160)，2026-09-23 实测）
      · theme_card —— 终端页里当前「主题曲」大卡（默认 (723,329)，实测：直接进章节地图）
    两种给法：`xy=(x,y)` 直接落库；或给一张截图让视觉模型看一眼报坐标（离线可用 `--shot`）。
    prompt 里**不能出现双引号**（经 PowerShell 传参会把参数截断 —— task-6 踩过）。
    """
    if xy is None:
        sys.path.insert(0, str(PROJ / "tools"))
        from discover_stages import _modlens_json                       # noqa: PLC0415
        q = {
            "terminal": "这是明日方舟大厅截图。请给出右侧【终端】按钮中心的像素坐标（1280x720 整数）。"
                        "只输出 JSON：{'x':123,'y':456}",
            "theme_card": "这是明日方舟【终端】页截图。请给出当前【主题曲】大卡（能直接点进去的那张）"
                          "中心的像素坐标（1280x720 整数）。只输出 JSON：{'x':123,'y':456}",
            "main_theme_card": "这是明日方舟【终端】页截图。请给出写着 MAIN THEME 与章节名（例如 怒号光明）"
                               "的那张卡片**可点区域中心**的像素坐标（1280x720 整数）。"
                               "只输出 JSON：{'x':123,'y':456}",
        }[kind]
        img = shot_path
        if img is None:
            OUT.mkdir(parents=True, exist_ok=True)
            img = OUT / f"_learn_{kind}.png"
            with open(img, "wb") as f:
                subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"], stdout=f,
                               timeout=60, creationflags=NOWIN)
        d = _modlens_json(img, q)
        res = d.get("result") or {}
        txt = "\n".join(str(t) for t in ((res.get("ocr") or {}).get("full_text"), res.get("summary"),
                                         json.dumps(res.get("semantics") or {}, ensure_ascii=False)) if t)
        cands = [(int(a), int(b)) for a, b in re.findall(r"(\d{2,4})\s*[,，]\s*(\d{2,4})", txt)]
        cands = [(x, y) for x, y in cands if 0 < x < 1280 and 0 < y < 720]
        if not cands:
            return {"ok": False, "kind": kind, "detail": "视觉模型没给出可用坐标", "raw": txt[:300]}
        xy = cands[0]
    doc = {}
    if LEARNED_NAV.exists():
        try:
            doc = json.loads(LEARNED_NAV.read_text(encoding="utf-8"))
        except Exception:                                               # noqa: BLE001
            doc = {}
    entry = dict(doc.get("entry") or {})
    entry[kind] = [int(xy[0]), int(xy[1])]
    doc["entry"] = entry
    doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    doc["_note"] = ("主题曲入口坐标（学一次；main_nav.py 启动时读它，读不到才回退代码里的默认值）。"
                    "terminal=大厅里终端按钮；theme_card=终端页主题曲大卡。")
    LEARNED_NAV.parent.mkdir(parents=True, exist_ok=True)
    LEARNED_NAV.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "kind": kind, "xy": [int(xy[0]), int(xy[1])], "saved": str(LEARNED_NAV)}


def main_theme_card(hits) -> tuple[int, int] | None:
    """终端页那张「MAIN THEME」卡片的可点位置（OCR 找文字，找不到用实测常量兜底）。

    Lead 2026-09-24 真机实测：终端页出现「MAIN THEME / 进度 / JT8-3 / 怒号光明」卡片，
    点它 (742,545) **直接进 EP08 章节地图**（看到 R8-11/JT8-1/M8-8）⇒ 比"主题曲大卡→本章回想→
    前往章节"短，而且**绕开 EPISODE 页签 carousel**（那个 carousel 在窗口里把队列卡死过）。
    """
    for x, y, t in hits:
        s = t.upper().replace(" ", "")
        if "MAINTHEME" in s or ("MAIN" in s and "THEME" in s):
            return (x, y + 26)          # 文字在卡片上部 ⇒ 往下偏一点落到卡片可点区
    for x, y, t in hits:
        if t.strip() in ("进度", "Chapter", "CHAPTER") and y > 400:
            return (x, y + 26)
    return None


def read(tag="nav"):
    img = shot(tag)
    if img is None:
        return [], "", None
    h = hits_of(img)
    return h, " ".join(t for _, _, t in h), img


# ── 页面判读 ────────────────────────────────────────────────────────────
def is_lobby(hits, txt):
    return ("终端" in txt) and ("仓库" in txt)


def is_terminal(hits, txt):
    return ("MAIN THEME" in txt.upper()) or ("ACTIVITY" in txt.upper())


def node_code_in_panel(hits):
    """（已废弃，保留给老调用）见 `panel_code`。"""
    cands = [(x, y, t) for x, y, t in hits
             if re.fullmatch(r"\d{1,2}-\d{1,2}", t.replace(" ", "")) and x > 650]
    if not cands:
        return ""
    cands.sort(key=lambda c: c[1])
    return cands[0][2].replace(" ", "")


# ── 「报酬里有没有源石」判据（用户 2026-09-23 口径）────────────────────────
#    原话：「**报酬里面没有源石就是没有就不用打了**」
#    判据＝报酬区里那颗**未领取的至纯源石徽章**：金色六边形+菱形，下面印「三星获得」。
#    实测分界（模板 `data/templates/prime_badge.png`，报酬 ROI y∈[430,620] x∈[860,1280]）：
#      · 第 15 章 15-20 普通（未通关，有首通源石）= **1.000**
#      · 第 15 章 15-4 / 15-3（已通关，源石已领）    = 0.577
#      · 主线地图页（没有报酬区）                     = 0.386
#    ⇒ 阈值 0.75。
BADGE_TPL = PROJ / "data" / "templates" / "prime_badge.png"
REWARD_ROI = (430, 620, 860, 1280)
BADGE_THRESHOLD = 0.75


def reward_has_prime(img) -> tuple[bool, float]:
    """报酬区里有没有**未领的至纯源石**。返回 (有没有, 最高匹配分)。"""
    if img is None or not BADGE_TPL.exists():
        return False, -1.0
    tpl = cv2.imread(str(BADGE_TPL))
    if tpl is None:
        return False, -1.0
    y0, y1, x0, x1 = REWARD_ROI
    roi = img[y0:y1, x0:x1]
    if roi.size == 0:
        return False, -1.0
    best = 0.0
    for s in (0.9, 1.0, 1.1):
        t = cv2.resize(tpl, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
        if t.shape[0] >= roi.shape[0] or t.shape[1] >= roi.shape[1]:
            continue
        best = max(best, float(cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED).max()))
    return best >= BADGE_THRESHOLD, round(best, 3)


def is_map(hits, txt):
    """地图页特征：右下角有「EPISODE」页签 / 屏上有关卡代号标签 / 有「开始行动」。"""
    if "EPISODE" in txt.upper():
        return True
    if codes_on_map(hits):
        return True
    return "开始行动" in txt or "开始突袭" in txt or "开始险地" in txt


CODE_TOKEN = re.compile(r"(?:[A-Z]{1,2}\d{1,2}|\d{1,2})-S?\d{1,2}")   # 8-1 / R8-1 / M8-1 / JT8-3 / S7-1


def codes_on_map(hits) -> list[str]:
    """屏上的**关卡代号**（带字母前缀的也算：R8-1 / M8-1 / JT8-3 / H8-1 / S7-1）。

    用 `search` 而不是 `fullmatch`：OCR 常把标签和代号并成一个 token（实测 `OPERATION9-1`、
    `OPERATION15-20`）⇒ fullmatch 会漏掉整屏的代号（第 1/9 章的单测就是这么失败的）。
    """
    out = []
    for _x, _y, t in hits:
        s = t.replace(" ", "").upper()
        m = CODE_TOKEN.search(s)
        if not m:
            continue
        c = m.group(0)
        mm = re.match(r"^(?:[A-Z]{1,2})?(\d{1,2})-", c)
        if mm and int(mm.group(1)) > 20:            # 甩掉 `26-09` 这种时间戳碎片
            continue
        out.append(c)
    return out


def episode_tabs(hits):
    """右下角那排 EPISODE 页签 → {章节号: (x, y)}。

    实测（2026-09-23，第 14/15/16 章同属主题曲「离解复合」）：
        `EPISODE`(983,660) `EPISODE`(1086,661) ＋ 数字 `14`(984,685) `15`(1085,685) `16`(1187,685)
        ⇒ 数字在 **y≈660~705、x>900**（上面一行是 EPISODE/当前进度 字样）。
    只认这一带，别把关卡代号当页签。
    """
    out = {}
    for x, y, t in hits:
        s = t.replace(" ", "")
        if y < 620 or y > 715 or x < 900:
            continue
        if re.fullmatch(r"\d{1,2}", s):
            out[int(s)] = (x, y)
    return out


def showing_episode(hits, ep: int) -> bool:
    """屏上是不是**正在展示第 ep 章**的地图（看节点标签前缀）。

    为什么不能只看 `ep in episode_tabs(hits)`：页签那一排**永远**是 14/15/16，
    在不在里面跟"当前选中的是哪一章"无关（2026-09-23 实测踩到：站在 15 章的地图上问
    16-3，代码里 `16 in tabs` 为真 ⇒ 不切页签 ⇒ 白滚 10 次也找不到 16-3）。

    **2026-09-24 窗口实测的严重 bug（已在线上把队列卡成死循环）**：老写法只认裸代号
    `\\d{1,2}-\\d{1,2}`，而**第 8 章的节点是 `R8-1 / M8-1 / JT8-3` 这种带字母前缀的** ⇒
    `showing_episode(8)` 恒为 False ⇒ `switch_episode` 点完 8 之后永远"没切过去"，
    于是**在同一个坐标上连点十几次**（日志 `[导航] 页签 [7, 8, 9] ⇒ 点 EPISODE 8 (1085,684)`
    刷屏），白白烧掉一个窗口。现在改成**带前缀也认**（`R8-1`/`M8-1`/`JT8-3`/`H8-1` 都算第 8 章）。
    """
    for c in codes_on_map(hits):
        m = re.match(r"^(?:[A-Z]{1,2})?(\d{1,2})-", c)
        if m and int(m.group(1)) == ep:
            return True
    return False


def chapter_of_code(code: str) -> int:
    """关卡代号 → 章节号（EPISODE 页签用）。

    数字开头的直接取（`16-3`→16）；**非数字开头的（TR-26 / H15-1 / JT8-3…）查
    `data/prime_plan.json`**（那份表里 code↔chapter 都有）——2026-09-23 实测踩到：
    旧写法 `int(code.split('-')[0])` 遇到 `TR-26` 直接 ValueError 崩掉 ✗。
    """
    m = re.match(r"^(\d+)-", code)
    if m:
        return int(m.group(1))
    try:
        plan = json.loads((PROJ / "data" / "prime_plan.json").read_text(encoding="utf-8"))
        for r in plan.get("rows") or []:
            if str(r.get("code", "")).upper() == code.upper():
                return int(r["chapter"])
    except Exception:                                             # noqa: BLE001
        pass
    m2 = re.search(r"(\d+)", code)
    return int(m2.group(1)) if m2 else 1


def norm_code(c: str) -> str:
    """代号归一：**去掉章号的零填充**（面板 OCR 会给 `07-2`，而目标是 `7-2`）。

    2026-09-24 窗口实测（第三个坑）：`panel_code()` 读出 `07-2`，而 `code` 是 `7-2` ⇒
    `on == code` 恒为 False ⇒ main_nav 明明已经站在**正确的详情页**上（stars=3/徽章=false
    全都读到了），却继续重复点节点、最后报 `ok=false, detail="切不到 EPISODE 7"`。
    这类"数据读对了但比较不等"的假失败最浪费窗口，必须在比较前归一。
    """
    c = str(c or "").replace(" ", "").upper()
    m = re.match(r"^(?:([A-Z]{1,2}))?0*(\d+)-(.+)$", c)
    if m:
        return f"{m.group(1) or ''}{m.group(2)}-{m.group(3)}"
    return c


def detail_confirmed(hits, txt, code: str) -> tuple[bool, str]:
    """**不依赖 `page_of` 的详情页确认**（Lead 2026-09-24 实测：主线关卡详情页 `page_of` 给「未知」、
    stars=None ⇒ 任何"`page=='关卡详情'` 才算成功"的判据在主线永远失败）。

    两级判据（都用 main_nav 自己的 OCR，不查服务）：
      ① 屏上读到关卡代号（`panel_code`）且与本关一致；
      ② 退一步：屏上有本关代号 + 有开始按钮（开始行动/开始突袭/开始险地/开始作战/演习/代理指挥）。
    返回 (是否确认, 读到的代号)。
    """
    on = panel_code(hits) if is_detail(hits, txt) else ""
    if on and norm_code(on) == norm_code(code):
        return True, on
    on2 = on or panel_code(hits)
    if on2 and norm_code(on2) == norm_code(code) and any(
            k in txt for k in ("开始行动", "开始突袭", "开始险地", "开始作战", "演习", "代理指挥")):
        return True, on2
    # ③ `panel_code()` 只拼**右半屏**（x>600）的文字；万一主线面板的代号落在左半屏，
    #    这里用"屏上任何位置的关卡代号 + 有开始按钮"兜底（宁可不漏判）。
    for c in codes_on_map(hits):
        if norm_code(c) == norm_code(code) and any(
                k in txt for k in ("开始行动", "开始突袭", "开始险地", "开始作战", "演习", "代理指挥")):
            return True, c
    return False, on2


def is_detail(hits, txt):
    return any(k in txt for k in ("推荐平均等级", "报酬", "开始行动", "开始突袭", "开始险地",
                                  "代理指挥", "演习", "附加条件"))


def panel_code(hits):
    """详情面板里的关卡代号。

    ⚠️ OCR 会把 `OPERATION` 和代号**粘成一个元素**：实测 `oPenATion15-20`(931,98)
    ⇒ 不能要求元素文本"恰好等于代号"（旧写法就是这么漏判的），要把面板那一侧（x>600）
    的文字拼起来再正则。地图上的节点标签在左半边（x<650），不会混进来。
    """
    s = "".join(t for x, _y, t in hits if x > 600).upper().replace(" ", "")
    m = re.search(r"(\d{1,2}-\d{1,2})", s)
    return m.group(1) if m else ""


# 面板标题右侧三个六边形（星级记录）的中心像素 —— 实测 (2026-09-23, chapter 15 面板)
HEXES = ((1046, 110), (1105, 110), (1164, 110))
# 困难档的"开始"按钮文字（各章叫法不同）——「开始作战」是第 15 章险地档的写法
RAID_START = ("开始险地", "开始突袭", "开始磨难", "开始作战")
# 困难档面板上的其它特征（第 15 章险地档会印「沙盘推演」+ 附加条件，切回普通的那颗按钮叫「常规作战」）
RAID_MARKS = ("沙盘推演", "常规作战")


def stars_from_panel(img) -> int:
    """面板上亮着几个六边形 = 几星通关（0 = 未通关）。

    **用"亮蓝像素面积"判**（2026-09-23 实测标定，分界极干净）:
        · 第 15 章 15-3（三星）: 蓝像素 4400（单个六边形 ≈1470 px）
        · 第 15 章 15-20（未通关）: 蓝像素 **0**
        · 而"代理指挥"上的锁**跟通没通关无关**（15-3 三星但代理也是锁的）⇒ 不能拿它当判据。
    """
    if img is None:
        return -1
    import numpy as np
    sub = img[60:150, 980:1260]
    if sub.size == 0:
        return -1
    b = sub[:, :, 0].astype(int)
    r = sub[:, :, 2].astype(int)
    n = int(((b > 150) & (b > r + 40)).sum())
    if n < 300:
        return 0
    return max(1, min(3, round(n / 1470)))


def find_node(hits, code, x_lo=150, x_hi=900, y_lo=140, y_hi=640):
    """地图上的节点标签。

    实测（2026-09-23）节点标签的 y 在 **360~530** 之间跳（链子是斜的），旧写法卡 `y<470`
    会把 15-4(534,530) 这种直接滤掉 ⇒ 表现成"地图上找不到这一关"，白滚十几屏。
    右半屏 x>900 会被**详情面板**盖住，点它可能按到面板按钮 ⇒ 只认 x∈[150,900]；
    真在视野外就先横拖把它挪到中间（见 main 里的 re-center）。
    """
    want = code.replace(" ", "")
    for x, y, t in hits:
        if x < x_lo or x > x_hi or y < y_lo or y > y_hi:
            continue
        # 2026-09-25 修复（达芬奇 A2）：比较必须过 `norm_code` —— 面板/地图 OCR 会给零填充
        #   `07-2` 而目标是 `7-2`，字面量相等恒 False ⇒ 假「地图上找不到这一关」（主线 13 条）。
        #   与 :379 detail_confirmed 同规（那里早已归一，这里漏了）。
        if norm_code(t) == norm_code(want):
            return x, y
    return None


def find_node_any(hits, code):
    """放宽到整屏（含被面板压住的区域）—— 只用来判断"它在不在这一屏"，不直接点。"""
    return find_node(hits, code, x_lo=30, x_hi=1250, y_lo=140, y_hi=640)


def sanity(txt):
    m = re.search(r"(\d{1,3})\s*/\s*(\d{2,3})", txt)
    return int(m.group(1)) if m else None


def is_result(hits, txt):
    return any(k in txt for k in ("行动结束", "MISSION RESULTS", "任务失败", "MISSION FAILED",
                                  "点击画面", "以继续"))


def result_ok(txt):
    return ("行动结束" in txt or "MISSION RESULTS" in txt) and ("任务失败" not in txt)


# ── 进地图 ──────────────────────────────────────────────────────────────
def is_battle(hits, txt) -> bool:
    """**战斗内**判据（2026-09-24 窗口实测加）：关卡内 HUD 的特征文本。

    为什么要它：`ensure_map()` 原来遇到"认不出的页面"就**连按 BACK（最多 8 次）**，
    而战斗内 BACK 完全无效（退战要在暂停菜单里点"退出行动"）⇒ 白按 8 次、每次还 sleep 2s。
    实测证据：窗口里模拟器停在战斗内，OCR 全文只有 `0/10  52 剩余可放置角色：8 21`，
    main_nav 两趟各花 ~40s 全部无效（截图 runtime/reports/_win_state.png）。
    """
    if "剩余可放置角色" in txt or "可放置角色" in txt:
        return True
    if "倍速" in txt and "暂停" in txt:
        return True
    return bool(re.search(r"\b\d{1,2}/\d{1,3}\b", txt)) and bool(re.search(r"^\s*\d{1,3}\s", txt))


def ensure_map(max_backs=6, verbose=True):
    """把界面弄到章节地图上。返回 (ok, last_txt)"""
    for i in range(max_backs + 2):
        hits, txt, _img = read(f"m{i}")
        # **战斗内 ⇒ 什么都不按、立刻返回**：退战只能人来点（暂停 → 退出行动），
        #   盲按 BACK 无效还会把时间耗光（窗口实测两趟各 40s 白跑）。
        if is_battle(hits, txt):
            if verbose:
                print("   [导航] 识别到**战斗内**（关卡 HUD）⇒ 不按任何键，先退出战斗再跑", flush=True)
            return False, f"在战斗内（OCR: {txt[:60]}）"
        # 结算页 → 点掉
        if is_result(hits, txt):
            if verbose:
                print(f"   [导航] 结算页 ⇒ 点继续", flush=True)
            tap(640, 620, wait=3.0)
            tap(640, 620, wait=2.5)
            continue
        if is_map(hits, txt) and episode_tabs(hits):
            if verbose:
                print(f"   [导航] 已在地图（页签 {sorted(episode_tabs(hits))}）", flush=True)
            return True, txt
        if is_lobby(hits, txt):
            if verbose:
                print("   [导航] 在大厅 ⇒ 点终端", flush=True)
            tap(*TERMINAL_BTN, wait=4.0)
            continue
        if is_terminal(hits, txt):
            # **优先走 MAIN THEME 卡片**（Lead 真机实测：直接进章节地图、绕开 EPISODE carousel）；
            #   找不到那张卡再退回"主题曲大卡 → 本章回想 → 前往章节"的老路。
            mt = main_theme_card(hits)
            if mt:
                if verbose:
                    print(f"   [导航] 在终端 ⇒ 点 MAIN THEME 卡片 {mt}（直达章节地图）", flush=True)
                tap(*mt, wait=5.0)
                h2, t2, _ = read("mt")
                if is_map(h2, t2) or episode_tabs(h2):
                    continue
                if verbose:
                    print("   [导航] MAIN THEME 点了没进地图 ⇒ 退回主题曲大卡", flush=True)
            if verbose:
                print("   [导航] 在终端 ⇒ 点主题曲卡片", flush=True)
            tap(*THEME_CARD, wait=5.0)
            continue
        # 主题曲总览页（本章回想 / 前往章节）⇒ 点「前往章节」进地图
        if "前往章节" in txt:
            for x, y, t in hits:
                if "前往章节" in t:
                    if verbose:
                        print(f"   [导航] 主题曲总览页 ⇒ 点「前往章节」({x},{y})", flush=True)
                    tap(x, y, wait=5.0)
                    break
            continue
        # 主题曲目录/乐章收录（Story Line 目录、燎原、人们我们…）⇒ **不认识就别乱点**：
        #  2026-09-23 实测：这里点卡片会点到「乌萨斯的孩子们」（带锁），越点越远 ⇒ 一律返回。
        #  进地图的正路只有一条：**终端 → 主题曲卡片 → 本章回想页 → 前往章节**。
        if "乐章收录" in txt or "Story Line" in txt or "曲谱" in txt:
            if verbose:
                print("   [导航] 在主题曲目录 ⇒ 返回（不乱点卡片）", flush=True)
            back(2.0)
            continue
        # 既不认识、又有「确认退出游戏」→ 点取消（439,475）
        if "是否确认退出游戏" in txt:
            tap(439, 475, wait=2.0)
            continue
        # 「暂离行动？」之类的确认框（实测：反复切难度后会弹）⇒ 点「取消」，别把进度丢了
        if "暂离" in txt or ("取消" in txt and "确认" in txt):
            for x, y, t in hits:
                if "取消" in t:
                    if verbose:
                        print("   [导航] 确认框 ⇒ 点「取消」", flush=True)
                    tap(x, y, wait=2.5)
                    break
            continue
        if verbose:
            print(f"   [导航] 认不出（按返回再试）：{txt[:80]}", flush=True)
        back(2.0)
    hits, txt, _ = read("mlast")
    return (is_map(hits, txt) and bool(episode_tabs(hits))), txt


def switch_episode(ep: int, verbose=True, tries: int = 16) -> bool:
    """把地图切到第 ep 章。

    **页签是个滑动窗口**（2026-09-23 实测）：站在第 14 章时那一排是 `13 14 15`，
    点一下 15 就变成 `14 15 16` ⇒ 不能指望"目标章一定在屏上"，
    得**沿窗口一步步走过去**：每次挑一个离目标最近、且偏右的页签点。

    **2026-09-24 窗口实测的坑（原来 tries=6，且没有"点了没变"的保护）**：从 `[14,15,16]`
    切到第 7 章，每次点击窗口只挪一格 ⇒ 需要 9 次点击才轮到 7；tries=6 会在点到 `[9,10,11]`
    时耗尽并报"切不到 EPISODE 7"（**导航链本身完全正常**）。另外若"点了没变化"却没察觉，
    就会在同一个坐标上连点（实测卡死一个窗口）⇒ 这里加：同一坐标连点 3 次无效就换下一个候选，
    再无效就返回 False 并说明。现在默认 16。
    """
    last_pos = None
    same_pos_hits = 0
    tried: set = set()
    for _ in range(max(tries, 2 * abs(ep) + 4)):
        hits, txt, _img = read("ep")
        if showing_episode(hits, ep):
            return True
        tabs = episode_tabs(hits)
        if not tabs:
            # **2026-09-24 窗口实测**：点页签有时会把地图带到一个**没有页签的界面**（点过头/落到别的元素）
            #   ⇒ 原来直接 return False，把一次本可挽回的切章判成失败。现在先 BACK 一次回地图再继续。
            if verbose:
                print("   [导航] 屏上没有 EPISODE 页签 ⇒ BACK 一次再试", flush=True)
            back(2.0)
            hits, txt, _img = read("epbk")
            if episode_tabs(hits) or showing_episode(hits, ep):
                continue
            if verbose:
                print("   [导航] BACK 后仍没有页签 ⇒ 放弃切章", flush=True)
            return False
        # 候选按"离目标最近"排；同一坐标连点 3 次无效 ⇒ 换下一个候选
        cands = sorted(tabs.items(), key=lambda kv: (abs(kv[0] - ep), -kv[0]))
        best = None
        for num, pos in cands:
            if (num, pos) in tried and same_pos_hits >= 3:
                continue
            best = (num, pos)
            break
        if best is None:
            if verbose:
                print(f"   [导航] 页签 {sorted(tabs)} 都点过且无效 ⇒ 放弃切第 {ep} 章", flush=True)
            return False
        num, (x, y) = best
        if (x, y) == last_pos:
            same_pos_hits += 1
        else:
            same_pos_hits = 0
        last_pos = (x, y)
        if verbose:
            print(f"   [导航] 页签 {sorted(tabs)} ⇒ 点 EPISODE {num} ({x},{y})"
                  + (f"（同点第 {same_pos_hits + 1} 次）" if same_pos_hits else ""), flush=True)
        tap(x, y, wait=4.5)
        if same_pos_hits >= 3:
            tried.add((num, (x, y)))
            last_pos, same_pos_hits = None, 0
    hits, _txt, _img = read("eplast")
    return showing_episode(hits, ep)


# ── 主流程 ──────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="", help="游戏里显示的关卡代号，如 16-3 / 15-20 / 0-1")
    ap.add_argument("--raid", action="store_true", help="切「险地作战 / 突袭」那一档")
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--quiet", action="store_true")
    # task-12：入口坐标"学一次"（落 runtime/learned/story_nav.json），不再只能改代码
    ap.add_argument("--learn-entry", choices=["terminal", "theme_card", "main_theme_card"], default="",
                    help="学一个入口坐标（默认从当前屏让视觉模型看一眼；配 --shot 可离线重放）")
    ap.add_argument("--set-entry", default="", help="直接写入口坐标，如 --set-entry terminal=967,160")
    ap.add_argument("--shot", default="", help="离线：用这张图代替现场截图（配 --learn-entry）")
    a = ap.parse_args()

    if a.learn_entry or a.set_entry:
        if a.set_entry:
            k, _, v = a.set_entry.partition("=")
            x, _, y = v.partition(",")
            out = learn_entry(k.strip(), xy=(int(x), int(y)))
        else:
            sp = pathlib.Path(a.shot) if a.shot else None
            if sp and not sp.is_absolute():
                sp = PROJ / sp
            out = learn_entry(a.learn_entry, shot_path=sp)
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 1

    if not a.code:
        print(json.dumps({"ok": False, "detail": "需要 --code（或 --learn-entry/--set-entry）"},
                         ensure_ascii=False))
        return 2
    code = a.code.replace(" ", "")
    ep = chapter_of_code(code)
    res = {"ok": False, "code": code, "on_page": "", "sanity": None, "detail": ""}
    V = not a.quiet

    ok, txt = ensure_map(verbose=V)
    if not ok:
        res["detail"] = f"进不去章节地图：{txt[:120]}"
        print(json.dumps(res, ensure_ascii=False))
        return 3

    for rnd in range(a.rounds):
        # 每轮先看是不是已经站在目标关的面板上
        hits, txt, img = read(f"r{rnd}")
        res["sanity"] = sanity(txt) or res["sanity"]
        hit_ok, on = detail_confirmed(hits, txt, code)
        if hit_ok:
            res["on_page"] = on
            res["detail_via"] = "ocr（不依赖 page_of：主线详情页 page_of 给未知）"
            res["stars"] = stars_from_panel(img)
            res["cleared"] = res["stars"] > 0
            has_p, score = reward_has_prime(img)
            res["reward_primes"] = has_p
            res["reward_score"] = score
            res["mode"] = ("险地/磨难" if (any(k in txt for k in RAID_START)
                                          or any(k in txt for k in RAID_MARKS))
                           else "普通" if "开始行动" in txt else "?")
            in_raid = res["mode"] == "险地/磨难"
            if norm_code(on) == norm_code(code):
                if a.raid != in_raid:
                    # 面板底部那颗按钮的**名字随档位与章不同**：普通档印「险地作战」/「隐秘战线」/「突袭模式」，
                    # 险地档印「常规作战」。判"现在是哪一档"只看**开始按钮上的字**
                    # （`开始行动` = 普通；`开始作战`/`开始险地`/`开始突袭` = 困难）。
                    # ⚠️ 只扫**底部按钮条**（y∈[600,700]、x∈[560,900]）——面板正文里也会出现"险地/磨难"字样，
                    #    扫全屏会点到正文上；而且**只能点一次**（2026-09-23 实测：代码里重复了一段
                    #    toggle 循环 ⇒ 点两次 = 切过去又切回来 ⇒ 表现成"难度永远切不动"）。
                    want = ("常规作战",) if in_raid else ("险地作战", "突袭模式", "隐秘战线", "磨难", "突袭", "险地")
                    hit = None
                    for x, y, t in hits:
                        if 560 <= x <= 900 and 600 <= y <= 700 and any(k in t for k in want):
                            hit = (x, y)
                            break
                    if hit is None:                                  # 兜底：实测按钮就在 (754,660) 一带
                        hit = (754, 660)
                    if V:
                        print(f"   [导航] 切档位：{'险地→普通' if in_raid else '普通→险地'} @ {hit}", flush=True)
                    tap(*hit, wait=3.5)
                    hits2, txt2, img2 = read(f"r{rnd}raid")
                    res["sanity"] = sanity(txt2) or res["sanity"]
                    if img2 is not None:
                        res["stars"] = stars_from_panel(img2)
                        res["cleared"] = res["stars"] > 0
                        has_p2, score2 = reward_has_prime(img2)
                        res["reward_primes"] = has_p2
                        res["reward_score"] = score2
                    res["mode"] = ("险地/磨难" if (any(k in txt2 for k in RAID_START)
                                                  or any(k in txt2 for k in RAID_MARKS))
                                   else "普通" if "开始行动" in txt2 else "?")
                res.update(ok=True, detail=(f"已在 {code} 的详情面板（{res.get('stars')} 星 / {res.get('mode')}"
                                            f" / 报酬源石={'有' if res.get('reward_primes') else '无'}"
                                            f"[{res.get('reward_score')}]）"))
                print(json.dumps(res, ensure_ascii=False))
                return 0
            # 站在别的关的面板上 ⇒ 先切章节/找节点（不点面板上的任何按钮）
        if not showing_episode(hits, ep):
            if not switch_episode(ep, verbose=V):
                res["detail"] = f"切不到 EPISODE {ep}"
                print(json.dumps(res, ensure_ascii=False))
                return 4
            hits, txt, _img = read(f"r{rnd}ep")
        node = find_node(hits, code)
        if node is None:
            # 地图是**横向长链**：实测必须 `swipe(1150,380 → 250,380, 900ms)` 才滚得动
            # （起点不能落在右下角面板上，y=330 那条带也拖不动 —— 2026-09-23 实测）
            for i in range(6):
                swipe(1150, 380, 250, 380, ms=900, wait=2.0)      # 往前（章节后面的关）
                hits, txt, _img = read(f"r{rnd}s{i}l")
                node = find_node(hits, code)
                if node:
                    break
            if node is None:
                for i in range(10):
                    swipe(250, 380, 1150, 380, ms=900, wait=2.0)  # 回头（章节前面的关）
                    hits, txt, _img = read(f"r{rnd}s{i}r")
                    node = find_node(hits, code)
                    if node:
                        break
        if node is None and find_node_any(hits, code):
            # 目标在这一屏、但落在面板下面（x>900 或 x<150）⇒ 横拖把它挪到中间再点
            for _ in range(3):
                x0, y0 = find_node_any(hits, code)
                swipe(x0, 380, 640, 380, ms=700, wait=1.5)
                hits, txt, _img = read(f"r{rnd}c")
                node = find_node(hits, code)
                if node:
                    break
        if node is None:
            res["detail"] = f"地图上找不到 {code} 的节点｜屏上：{txt[:120]}"
            print(json.dumps(res, ensure_ascii=False))
            return 5
        if V:
            print(f"   [导航] 找到 {code} @ {node} ⇒ 点它", flush=True)
        # **点节点会先把地图平移**（Lead 2026-09-24 实测：点 H7-1 的坐标结果地图滚到 7-1..7-4）
        #   ⇒ 点完必须**确认是否进了详情页**；没进就重算节点坐标（不能拿旧坐标连点）。
        _last_node = res.get("_last_node")
        res["_last_node"] = (int(node[0]), int(node[1]))
        if _last_node and abs(_last_node[0] - node[0]) <= 3 and abs(_last_node[1] - node[1]) <= 3:
            # 同一个位置连点两次都没进详情页 ⇒ 说明点偏了：改点标签自己（不 -22）并稍微放大偏移
            if V:
                print("   [导航] 同点两次没进详情页 ⇒ 改点标签本身", flush=True)
            tap(node[0], node[1], wait=3.5)
        else:
            tap(node[0], node[1] - 22, wait=3.5)
        # 点完立刻回读一次：进了详情页就交给下一轮的判定（这里只用于观测）
        _h2, _t2, _i2 = read(f"r{rnd}tap")
        if V and not (is_detail(_h2, _t2) or panel_code(_h2)):
            print(f"   [导航] 点完没进详情页（地图被平移了？）⇒ 下一轮重算坐标", flush=True)

    res["detail"] = f"{a.rounds} 轮内没进到 {code} 的面板"
    print(json.dumps(res, ensure_ascii=False))
    return 6


if __name__ == "__main__":
    raise SystemExit(main())
