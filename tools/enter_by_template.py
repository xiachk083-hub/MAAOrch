#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按**卡片模板**进活动 —— 替掉"在网格里 OCR 找乐章名"那条又慢又不可靠的路。

用户 2026-09-24 的两条关键提醒：
  · 完成进度网格**进来会停在上次那个插曲的位置** ⇒ 每次必须先归零（滑到顶）再往下扫；
  · 卡片封面**不会变** ⇒ 做成模板，匹配与滚动位置无关。

流程：
  ① 打开「乐章收录」→「完成进度」（坐标点，不用 OCR）
  ② 归零：往上/下用力滑若干次到顶（**不做"判到头"这种花活** —— 那正是之前坏掉的地方：
     我原来靠"帧差变小=到头"，结果动画一直在动、判据永不成立，于是一路滑满上限）
  ③ 逐屏扫描：截屏 → 模板匹配目标活动卡 → 命中就点它
  ④ 落地核验：页面指纹变成「乐章详情」→ 点「进入活动」→ 再核验页面（活动地图）

用法:
    python tools/enter_by_template.py --event buyizhicai            # 进「不义之财」
    python tools/enter_by_template.py --event buyizhicai --max-screens 6
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
sys.path.insert(0, str(ROOT / "tools"))
ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
SVC = "http://127.0.0.1:8765"
EV_TPL = ROOT / "data" / "templates" / "events"

BOOK_COLLECT = (1174, 49)     # 曲谱页右上「乐章收录」
TAB_PROGRESS = (959, 42)      # 「完成进度」页签
ENTER_BTN = (1132, 616)       # 乐章详情页「进入活动」
OFFSET_UNIT = "zero:slow1200/2.0"   # offsets「屏」的单位：归零原点起算 + 慢拖(1200ms)+停稳(2.0s)
EXIT_CANCEL = (448, 497)            # 「是否确认退出游戏?」弹窗左侧 ✗（实测帧 _ce6/_mt_book0.png）
DIALOG_ROI = (340, 295, 940, 520)   # 退出确认弹窗的「文字条 + 两个按钮」一带（便宜 OCR 用）

# ── 归零/扫描加固参数（2026-09-25，只在本文件里，改这里即可调）──────────────
PAGE_NAMESPACE = "progress_grid"  # offsets.json **双键**的第 1 键：屏号只对「完成进度」网格页有意义
SCAN_MS = 1200                    # 慢拖一屏的**默认时长**（= OFFSET_UNIT 的 slow1200；改它必须同步改 OFFSET_UNIT）
ZERO_MS = 450                     # 归零快滑时长（默认仍 450ms；连续零位移时升级见 ZERO_STALL_LIMIT）
STEPWISE_MS = 1600                # motionevent 分步拖总时长（比 1200 更慢更长 = 升级档）
BAND_ROI = (110, 660, 60, 620)    # 位移核验 ROI[y0,y1,x0,x1]：只取**左半屏卡片列** —— 旧 ROI 横跨
                                  #   x60~1220 被右半屏常驻动画主导（帧差/NCC 全是它的信号）
MIN_MOVE_PX = 8.0                 # 真位移阈值①：行位移折算回原 ROI（550px 高）≥8px
NCC_STILL = 0.97                  # 真位移阈值②：整块归一化互相关 < 0.97（两条满足其一 = 真动了）
# ⚠️ 2026-09-25 01:42 回归修复：初值 0.995 太松 —— 静止页面的帧间噪声/动画 NCC 常态 0.994-0.999，
#   每滑都 0.994<0.995 ⇒ 判"在动" ⇒ stall 永不累积 ⇒ 归零滑满 10 滑判不了到顶 ⇒ **所有 enter 全灭**
#   （37 分钟全灭实证）。阈值必须显著低于静止噪声（0.97），真滚动 NCC 会掉到 0.8-0.9 量级。
ZERO_MAX_SLIDES = 10              # 归零滑动上限（沿用旧的 range(1,11)）
ZERO_STALL_LIMIT = 2              # 连续 N 滑零位移 ⇒ 换更慢/更长的滑法；归零里再连续 N 次才认到顶


class _Tee:
    """把 stdout 同时写进文件（审计：队列只留最后一行，失败路径否则查无对证）。"""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, s):
        for st in self._streams:
            try:
                st.write(s)
                st.flush()
            except Exception:                    # noqa: BLE001
                pass
        return len(s)

    def flush(self):
        for st in self._streams:
            try:
                st.flush()
            except Exception:                    # noqa: BLE001
                pass

    def __getattr__(self, k):
        return getattr(self._streams[0], k)


def svc(path: str, timeout: float = 30.0):
    """调常驻服务。**带重试**（2026-09-24 真机实测）：服务在别的进程重启/被高频调用时，
    会出现短暂的 `ConnectionRefusedError [WinError 10061]`，旧代码直接抛出去把整条进活动流程打死
    （实测 10:48 那次就是 `tap()` 里崩的，前面 138s 全白干）。
    """
    if "?" in path:
        base, q = path.split("?", 1)
        pairs = [(kv.split("=", 1) + [""])[:2] for kv in q.split("&") if kv]   # 值可以是空串
        path = base + "?" + urllib.parse.urlencode([(k, v) for k, v in pairs])
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(SVC + path, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:                       # noqa: BLE001
            last = e
            if attempt < 2:
                time.sleep(0.5)
    raise RuntimeError(f"服务不可用（试了 3 次）：{type(last).__name__}: {last}") from last


def tap(x, y, wait=1.2):
    svc(f"/tap?x={int(x)}&y={int(y)}")
    time.sleep(wait)


def swipe(x1, y1, x2, y2, ms=900, wait=1.4):
    """滑动 —— 走 **常驻 adb shell**（`fastnav.Shell`）。

    2026-09-24 审计：旧版每次 `subprocess.run([adb, ...])` 都新起一个 adb 客户端
    （实测 53ms 握手 + 进程创建），而一次进活动要滑 10~40 次 ⇒ 白花 1~6s。
    Shell 常驻一条 `adb shell`，写一行就是一次滑动。失败退回一次性 adb。
    """
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        from fastnav import Shell                       # noqa: PLC0415
        Shell.get().swipe(int(x1), int(y1), int(x2), int(y2), int(ms))
    except Exception:                                   # noqa: BLE001
        subprocess.run([ADB, "-s", "127.0.0.1:16416", "shell", "input", "swipe",
                        str(x1), str(y1), str(x2), str(y2), str(ms)], timeout=30)
    time.sleep(wait)


def swipe_stepwise(x1: int, y1: int, x2: int, y2: int, ms: int = STEPWISE_MS, wait: float = 2.0) -> bool:
    """motionevent **分步拖**（docs/INPUT_GESTURES.md 实测口径）：`input swipe` 在「完成进度」页
    经常整段不生效（逐像素比对三滑位移仅 0.13%~0.27% ⇒ 根本没滚），`DOWN → N×MOVE → UP`、
    每步 200~300ms 才可靠。总时长 `ms` 摊到每步；`DOWN` 后先歇一拍再 `MOVE`（给游戏识别长按的
    时间）；起手点遵守文档**不从 y≥650 起手**（本文件两条滑轨起点 y=90/420，安全）。
    走常驻 `fastnav.Shell`（不新起 adb 客户端）；失败退回一次性 `input swipe`。返回是否走通分步拖。
    """
    steps = max(4, int(round(ms / 300)))
    step_s = max(0.2, int(ms / steps) / 1000.0)
    pts = [(int(round(x1 + (x2 - x1) * i / steps)), int(round(y1 + (y2 - y1) * i / steps)))
           for i in range(1, steps + 1)]
    seq = [["motionevent", "DOWN", str(int(x1)), str(int(y1))]]
    seq += [["motionevent", "MOVE", str(px), str(py)] for px, py in pts[:-1]]
    seq += [["motionevent", "UP", str(pts[-1][0]), str(pts[-1][1])]]
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        from fastnav import Shell                       # noqa: PLC0415
        sh = Shell.get()
        for c in seq:
            sh.cmd("input " + " ".join(c))
            time.sleep(step_s)
        time.sleep(wait)
        return True
    except Exception as e:                              # noqa: BLE001
        print("  motionevent 分步拖失败 ⇒ 退回一次性 swipe：", str(e)[:60])
        try:
            subprocess.run([ADB, "-s", "127.0.0.1:16416", "shell", "input", "swipe",
                            str(x1), str(y1), str(x2), str(y2), str(max(int(ms), 1500))], timeout=30)
        except Exception:                               # noqa: BLE001
            pass
        time.sleep(wait)
        return False


def grid_swipe(y1: int, y2: int, ms: int = 1200, wait: float = 2.0, stepwise: bool = False) -> None:
    """网格页竖直滑一屏（手指 640 直线上下）。**滑动时长提到函数签名上，默认仍 1200ms**
    （= OFFSET_UNIT `zero:slow1200/2.0` 的单位；改默认值必须同步改 OFFSET_UNIT 和已存屏号）。
    `stepwise=True` 走 motionevent 分步拖（连续零位移时的升级档，见 `swipe_stepwise`）。
    """
    if stepwise:
        swipe_stepwise(640, int(y1), 640, int(y2), ms=ms, wait=wait)
    else:
        swipe(640, int(y1), 640, int(y2), int(ms), wait)


def true_displacement(prev_band, cur_band) -> tuple[bool, float, float]:
    """相邻两帧 ROI 的**真位移**核验 → (这一滑是否真动了, 行位移px, NCC)。

    两条判据**满足其一**才算这一滑有效（旧判据=裸帧差均值：没滚动时它≈0 会假到顶，
    右半屏动画又会把它抬高 ⇒ 两个方向都骗人）：
      · 行位移：ROI 每行均值剖面做归一化互相关，取 |最优整数移位| × 原图缩放比 ≥ MIN_MOVE_PX；
      · NCC：整块归一化互相关 < NCC_STILL（掉得够多 ⇒ 画面真的变了）。
    band 为 None / 形状不一致（截屏失败）⇒ (False, 0.0, 1.0)：**核验不了就不算到顶、不算到底**。
    """
    if prev_band is None or cur_band is None or getattr(prev_band, "shape", None) != getattr(cur_band, "shape", None):
        return False, 0.0, 1.0
    import numpy as np
    a = prev_band.astype(np.float32)
    b = cur_band.astype(np.float32)
    scale = (BAND_ROI[1] - BAND_ROI[0]) / float(a.shape[0])
    ra = a.mean(axis=1)
    rb = b.mean(axis=1)
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    m = min(12, a.shape[0] // 4)
    best_s, best_k = -2.0, 0
    for k in range(-m, m + 1):
        x = ra[: a.shape[0] - k] if k >= 0 else ra[-k:]
        y = rb[k:] if k >= 0 else rb[: a.shape[0] + k]
        if x.size < 8:
            continue
        den = float(np.linalg.norm(x) * np.linalg.norm(y))
        if den <= 0:
            continue
        s = float(np.dot(x, y) / den)
        if s > best_s:
            best_s, best_k = s, k
    row_px = abs(best_k) * scale
    fa = a.ravel()
    fb = b.ravel()
    fa = fa - fa.mean()
    fb = fb - fb.mean()
    den = float(np.linalg.norm(fa) * np.linalg.norm(fb))
    ncc = float(np.dot(fa, fb) / den) if den > 0 else 1.0
    return bool((row_px >= MIN_MOVE_PX) or (ncc < NCC_STILL)), float(row_px), float(ncc)


def page_now() -> str:
    """当前页名。**必须带 `?text=1`**（2026-09-24 实测的假阴性根因）。

    服务里 `/all` 不带 `text` 时**不跑结构判据**（`page_of(ocr_text=None)`）⇒ 新活动的地图没有
    整屏模板 ⇒ 永远「未知」。AD 进活动的真机现场：工具的 `page_now()` 读到「未知」并据此判
    "点了「进入活动」但仍不在活动地图"⇒ 队列把 AD 记成"进不去"；而**同一秒**带 `text=1` 的
    `/all` 读到 `page=活动地图 0.585`（屏上 `AD-EX-1/2/3` + `OPERATION` + `为戏剧，为永恒`）。
    ⇒ 一次假阴性会让**最大的矿（AD 红丝绒 32/56）**被整体判成到不了。
    `?text=1` 的代价 = 一次整屏 OCR（1~6s）；这个工具是**进活动的一次性流程**，不是每步循环。
    """
    return svc("/all?text=1").get("page")


def find_event(slug: str) -> dict | None:
    """在当前屏匹配目标活动卡（模板与滚动位置无关）→ 返回卡片中心坐标。"""
    import cv2
    tpl = EV_TPL / f"{slug}.png"
    if not tpl.exists():
        raise SystemExit(f"没有模板 {tpl}（先用 tools/event_templates.py --learn 学一张）")
    p = ROOT / "runtime" / "_scan.png"
    svc(f"/shot?path={p}")
    img = cv2.imread(str(p))
    t = cv2.imread(str(tpl), cv2.IMREAD_GRAYSCALE)
    if img is None or t is None:
        return None
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if t.shape[0] >= g.shape[0] or t.shape[1] >= g.shape[1]:
        return None
    r = cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)
    _mn, mx, _ml, loc = cv2.minMaxLoc(r)
    if mx < 0.80:
        return None
    return {"x": int(loc[0] + t.shape[1] / 2), "y": int(loc[1] + t.shape[0] / 2), "score": round(float(mx), 3)}


def find_enter_button() -> tuple[int, int] | None:
    """「进入活动」按钮位置。

    **2026-09-24 用户直接指出**：在乐章详情页上就该**直接点「进入活动」进去**，别在导航上绕。
    我用眼睛在真机上量过：按钮白底、右下，中心 **(1122,617)**（我最早硬编码的 (1132,616) 本来就对）。
    所以顺序改成：**先用这个实测坐标**；点了没用才去猜（白框/视觉模型）。
    """
    return (1122, 617)


def _guess_enter_button() -> tuple[int, int] | None:
    """兜底：先按"白色长方形"找，再问视觉模型，再不行用旧坐标。"""
    import cv2
    import numpy as np
    import urllib.request as _u
    p = ROOT / "runtime" / "_detail.png"
    try:
        _u.urlopen(f"http://127.0.0.1:8765/shot?path={p}", timeout=20).read()
        im = cv2.imread(str(p))
        if im is not None:
            hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
            h, s, v = hsv[:, :, 0].astype(np.int16), hsv[:, :, 1].astype(np.int16), hsv[:, :, 2].astype(np.int16)
            mask = (((s < 55) & (v > 215))).astype(np.uint8) * 255
            # **只在下半屏找**（2026-09-24 实测教训）：不限制区域时，它把顶部卡片/标题那块
            #   大白斑当成按钮（选中 (975,169)），点了当然没反应。按钮在下半屏。
            mask[:400, :] = 0
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
            cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            best = None
            for c in cnts:
                x, y, w, hh = cv2.boundingRect(c)
                if w < 150 or not (30 <= hh <= 110) or w / max(hh, 1) < 2.0:
                    continue
                if best is None or w * hh > best[0]:
                    best = (w * hh, x + w // 2, y + hh // 2)
            if best:
                print(f"  白色长方形=进入活动：({best[1]},{best[2]}) 面积 {best[0]}")
                return (best[1], best[2])
    except Exception as e:                       # noqa: BLE001
        print("  找白条失败：", str(e)[:60])
    # —— 兜底：问视觉模型 ——
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import vision as _vision                     # noqa: PLC0415
        q = ('1280x720 手游截图。找**白色长方形的「进入活动」按钮**，'
             '只输出 JSON：{"x":<中心x>,"y":<中心y>}，找不到输出 {"x":null,"y":null}。')
        ans = _vision.ask(p, q)
        s = ans[ans.find("{"): ans.rfind("}") + 1] if "{" in ans else ""
        import json as _json
        d = _json.loads(s) if s else {}
        if d.get("x") is not None:
            print(f"  视觉模型给的按钮位置：({int(d['x'])},{int(d['y'])})")
            return (int(d["x"]), int(d["y"]))
    except Exception as e:                       # noqa: BLE001
        print("  视觉找按钮失败：", str(e)[:60])
    return (1132, 616)


_MAPCHECK_CACHE = {"t": 0.0, "prefix": None, "val": False}


def looks_like_map(prefix: str = "") -> bool:
    """**"到位了吗"判据**（2026-09-24 第四次修正：审计实测后换成"白框里读得出本活动代号"）。

    踩过的坑（一次比一次隐蔽）：
      ① 只认那 6 张页面模板 ⇒ 新活动地图报"未知"，把成功判成失败；
      ② 改成"检出六边形节点就算" ⇒ **完成进度网格页的卡片上也画着六边形**：审计实测
         `nodes_scan` 在网格页 `_scan.png`/`_sig.png` 上各报 **4 个节点**，而真地图
         `_map_now.png` 上只报 **1 个** ⇒ 这条判据的实际区分方向是**反的**；
      ③ 再改成"OCR 文本里出现本活动前缀（EA-）" ⇒ 那只能说明"屏上有一串字"，
         跟"图上有可点的关卡节点"无关（乐章详情页的关卡列表、曲谱页的章节号都能骗过它）。

    现在的判据用**用户给的那条**（也是唯一实测能分开的）：
        图上必须有**白框，且框里读得出本活动的关卡代号**（形如 `EA-1`）。
    实测（auditor-1）：
        · 真地图 `runtime/_map_now.png` → **1 个**（白框 OCR 出 `EA-1OPERATION`）
        · 乐章详情 `_detail_now.png`、网格 `_scan.png`、`_sig.png`、`_vision.png` → **0 个**

    开销（真机实测，MAA 同时在跑时）：`/shot` 0.5s、`/look?roi=` 小块 **1.6s/次** ⇒ 所以
    **先用几何筛框**再 OCR：网格页那 4 个框（85x19 / 70x16 / 146x39）全被筛掉 ⇒ **0 次 OCR**；
    真地图只有 2 个候选（130x43 名条、121x51 区域卡）⇒ 最多 2 次 OCR。再加 1.5s 结果缓存
    （同一轮 poll 里这个函数会被连问两次）。没给 prefix 时退化为"找到像名条的白框就算"。
    """
    import re as _re
    now = time.time()
    if _MAPCHECK_CACHE["prefix"] == prefix and now - _MAPCHECK_CACHE["t"] < 1.5:
        return bool(_MAPCHECK_CACHE["val"])
    res = False
    p = ROOT / "runtime" / "_mapcheck.png"
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        svc(f"/shot?path={p}")
        import cv2
        img = cv2.imread(str(p))
        if img is not None:
            import stage_boxes
            cand = []
            for x0, y0, x1, y1 in stage_boxes.white_boxes(img):
                w, h = x1 - x0, y1 - y0
                # 只对"像关卡名条"的白框 OCR（真地图实测 130x43；网格页的 85x19/70x16 直接排除）
                if 100 <= w <= 340 and 26 <= h <= 84:
                    cand.append((x0, y0, x1, y1))
                if len(cand) >= 2:
                    break
            if not prefix:
                res = bool(cand)
            else:
                want = f"{prefix.upper()}-"
                for x0, y0, x1, y1 in cand:
                    try:
                        txt = " ".join(svc(f"/look?roi={x0},{y0},{x1},{y1}").get("texts") or [])
                    except Exception:                # noqa: BLE001
                        continue
                    m = _re.match(r"([A-Z]{1,3})-(?:EX-|S-|SP-)?\d{1,2}",
                                  txt.upper().replace(" ", ""))
                    if m and f"{m.group(1)}-" == want:
                        res = True
                        break
    except Exception:                                # noqa: BLE001
        res = False
    _MAPCHECK_CACHE.update(t=now, prefix=prefix, val=res)
    return res


def _exit_dialog_up() -> bool:
    """屏上是不是「是否确认退出游戏?」弹窗 —— **只在要按 BACK 前**花一次便宜 OCR。

    为什么需要它（2026-09-24 真机）：终端页的 BACK 会拉起退出确认弹窗，弹窗一盖屏，
    后面所有坐标点击全落空（帧 `_ce6/_mt_book0.png`）。宁可多一次 /look，也别按出它。
    """
    x0, y0, x1, y1 = DIALOG_ROI
    try:
        txt = "".join(svc(f"/look?roi={x0},{y0},{x1},{y1}").get("texts") or [])
    except Exception:                                # noqa: BLE001
        return False
    return ("退出" in txt) or ("是否确认" in txt)


def _offset_get(offsets: dict, slug: str, namespace: str = PAGE_NAMESPACE) -> dict:
    """offsets.json **双键读取（迁移兼容）**：先读新 `offsets[page_namespace][slug]`，
    没有再读旧 `offsets[slug]` 单键（旧记录读到后照旧走 unit/zeroed 可信度判定）。
    """
    ns = offsets.get(namespace)
    if isinstance(ns, dict) and isinstance(ns.get(slug), dict):
        return ns[slug]
    rec = offsets.get(slug)
    return rec if isinstance(rec, dict) else {}


def _offset_put(offsets: dict, slug: str, rec: dict, namespace: str = PAGE_NAMESPACE) -> dict:
    """offsets.json **双键写入**：记到 `offsets[namespace][slug]`，并删掉旧 `offsets[slug]` 单键
    （迁移：全仓已核对只有本文件读写 offsets.json，留着旧键会变成两个真值源）。
    """
    ns = offsets.get(namespace)
    if not isinstance(ns, dict):
        ns = {}
    ns[slug] = rec
    offsets[namespace] = ns
    offsets.pop(slug, None)
    return offsets


def trusted_offset(slug: str, namespace: str = PAGE_NAMESPACE) -> tuple[int, int, str]:
    """offsets.json 里这个 slug 的**可信**屏号 + 诊断 → (屏号, 页面实测长度, 说明)。
    键序：双键 `offsets[page_namespace][slug]`（新）→ 单键 `offsets[slug]`（旧，迁移读取，见 `_offset_get`）。

    屏号 0 = 不可信/没有 ⇒ 不跳快路，直接归零后逐屏扫。
    **可信条件**（2026-09-24 定性）：写的时候带 `unit == OFFSET_UNIT` 且 `zeroed: true`
    —— 也就是"归零原点起算 + 慢拖 1200/2.0 的屏数"。旧记录（没有 unit 字段，实测
    buyizhicai=12 / hongsirong=16 是没归零时采的假坐标）**一律只忽略、不删**：等下一次真扫
    把它改对，正好当回归判据。另外用 `runtime/_grid_len.json` 的页面实测长度钳位：
    记录值 > 页面长度 ⇒ 物理上不可能是绝对屏号 ⇒ 同样忽略。
    """
    off_file = EV_TPL / "offsets.json"
    try:
        rec = _offset_get(json.loads(off_file.read_text(encoding="utf-8")) or {}, slug, namespace)
    except Exception:                                # noqa: BLE001
        rec = {}
    try:
        glen = int(json.loads((ROOT / "runtime" / "_grid_len.json").read_text(encoding="utf-8"))
                   .get("screens") or 0)
    except Exception:                                # noqa: BLE001
        glen = 0
    n = int(rec.get("screen") or 0)
    if not n:
        return 0, glen, "没有记录"
    if not (rec.get("unit") == OFFSET_UNIT and rec.get("zeroed")):
        return 0, glen, f"脏值 {n}（缺 unit/zeroed 标记 = 不是从归零点量的）⇒ 忽略快路"
    if glen and n > glen:
        return 0, glen, f"脏值 {n} > 页面实测 {glen} 屏 ⇒ 物理上不成立 ⇒ 忽略快路"
    return n, glen, f"可信 {n} 屏（{OFFSET_UNIT}）"



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True, help="模板名（ASCII slug）")
    ap.add_argument("--prefix", default="", help="活动关卡前缀（如 EA）；用于确认『真的在地图上』")
    ap.add_argument("--max-screens", type=int, default=30, help="逐屏扫描的**真上限**（旧版只把它拼进失败文案，2026-09-24 修）")
    ap.add_argument("--zone-ex", action="store_true", help="进 EX 区（点右下区域卡）")
    a = ap.parse_args()

    # **先验模板存在，再碰设备**（2026-09-24 我自己踩的坑）：模板不存在时旧版会一路走到
    #   BACK 清页 + to_book 才报错 —— 也就是说一次「参数写错」的调用也会真的去按键/滑屏
    #   （14:59 我就这么误按了 8 次 BACK）。判在设备动作之前。
    if not (EV_TPL / f"{a.event}.png").exists():
        print(f"没有模板 {EV_TPL / (a.event + '.png')}（先用 tools/event_templates.py --learn 学一张）")
        return 2

    # **审计**（2026-09-24）：队列 capture_output 只用最后一行 ⇒ 失败的中间路径查无对证
    #   （那次「16 屏」是假的，谁都复不出来）。这里把整份 stdout 同时落盘。
    try:
        _logf = open(ROOT / "runtime" / f"_enter_{a.event}.log", "a", encoding="utf-8")
        _logf.write("\n==== " + time.strftime("%Y-%m-%d %H:%M:%S")
                    + f" enter_by_template --event {a.event} --prefix {a.prefix}"
                    + f" --max-screens {a.max_screens} zone_ex={a.zone_ex}\n")
        sys.stdout = _Tee(sys.stdout, _logf)
    except Exception:                            # noqa: BLE001
        pass

    # ① 先保证站在**曲谱页**（不然那两下坐标点等于乱点）。
    #    ⓪ **清干扰页，但不许盲按 BACK**（2026-09-24 真机抓包的教训）：终端/大厅页上按 BACK
    #       拉起的是「是否确认退出游戏?」弹窗（帧 `runtime/_ce6/_mt_book0.png` 14:40:41 就是它），
    #       弹窗一盖住屏，后面每一次坐标点击都落空、to_book 白绕 30 秒。规则改成：
    #         · 页面=曲谱 ⇒ 结束
    #         · 页面=大厅/终端 ⇒ **一次 BACK 都不按**（那里 BACK 只会弹退出确认），交给 to_book()
    #         · 页面未知 ⇒ 最多按 2 次；但按之前先花 1 次 /look 认一下退出确认弹窗，
    #           认出来就直接点「取消 ✗」，别用 BACK 去关它
    for _i in range(2):
        pg0 = page_now()
        if pg0 == "曲谱":
            break
        if pg0 == "大厅":
            print("页面=大厅/终端 ⇒ 不按 BACK（那里 BACK 会弹退出确认），直接走 to_book()")
            break
        if _exit_dialog_up():
            print("  屏上是「是否确认退出游戏?」弹窗 ⇒ 点取消（✗ 448,497）")
            tap(*EXIT_CANCEL, wait=1.2)
            continue
        # BACK 键：走常驻服务，别 spawn adb（那正是被禁掉的慢法）
        try:
            urllib.request.urlopen(f"{SVC}/key?code=4", timeout=10).read()
        except Exception:                            # noqa: BLE001
            pass
        time.sleep(1.0)
    if page_now() != "曲谱":
        print("当前不在曲谱页 → 用 to_book() 回曲谱")
        _ok_book = False
        try:
            sys.path.insert(0, str(ROOT / "tools"))
            from enter_event import to_book          # noqa: PLC0415
            _ok_book = bool(to_book(a.prefix or "CV", print))
        except Exception as e:                       # noqa: BLE001
            print("to_book 失败：", e)
        # ⚠️ 这个返回值以前被丢掉（2026-09-24 实测的代价）：`to_book` 6 步全认不出页面时
        #    （游戏卡在「暂停的演习」/战斗 HUD），旧版照样按 (1174,49)/(959,42) 盲点，
        #    接着去扫一个**冻结**的页面（帧差 0.0 ⇒ 立刻"已滑到底"），每个单位白烧 89~100s，
        #    还把原因写成"两遍都没找到"—— 看起来像模板坏了，把审计带偏。
        #    现在到不了曲谱就**当场收手**，并给出能直指现场的失败原因。
        if not _ok_book or page_now() != "曲谱":
            print(json.dumps({"ok": False, "event": a.event,
                              "detail": f"没能回到曲谱页（to_book 结束于「{page_now()}」）——"
                                        "现场多半卡在战斗 HUD/暂停演习或别的死屏，不是模板问题"},
                             ensure_ascii=False))
            return 1

    # ①b 打开「乐章收录」→「完成进度」
    tap(*BOOK_COLLECT, wait=4.0)
    tap(*TAB_PROGRESS, wait=3.0)

    hit = None
    # ②b **先直接在当前屏找**（零滚动）：2026-09-24 两次成功都走这条（第一屏 0.990 命中）。
    #    ⚠️ 这条**不写 offsets**：此刻没归零，视口位置未知，写下去就是自污染。
    for _try in range(6):
        hit = find_event(a.event)
        if hit:
            print(f"当前屏命中（零滚动 ⇒ 不记 offsets）：{a.event} 在 ({hit['x']},{hit['y']}) "
                  f"score={hit['score']}（第 {_try+1} 次尝试）")
            break
        time.sleep(1.5)
    if not hit:
        print("当前屏没命中（页面可能还没就绪）⇒ 归零后按「绝对屏号」扫")

    def _band():
        import urllib.request
        import numpy as np
        import cv2
        p = ROOT / "runtime" / "_zero2.png"
        try:
            urllib.request.urlopen(f"{SVC}/shot?path={p}", timeout=15).read()
            im = cv2.imread(str(p))
            if im is None:
                return None
            y0, y1, x0, x1 = BAND_ROI
            return cv2.resize(cv2.cvtColor(im[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY), (96, 44),
                              interpolation=cv2.INTER_AREA)
        except Exception:                            # noqa: BLE001
            return None

    def zero_to_top(why: str, ms: int = ZERO_MS) -> bool:
        """归零到顶 —— **绝对屏号的原点**（用户 2026-09-24 两次提醒：这页竖直很长，必须先滑到顶）。

        方向：手指**向下拖** (90→660) = 内容下移 = 露出上面的内容 ⇒ 回到顶部。

        **2026-09-25 加固**（提案①③）：
          · 旧判据「裸帧差 < 1.0 = 到顶」在这页是假的 —— 裸 swipe 450ms 三滑位移仅
            0.13%~0.27%（根本没滚 ⇒ 帧差≈0 ⇒ **假到顶**）。现在每滑一次先过
            `true_displacement()`：相邻两帧 ROI **行位移 ≥ MIN_MOVE_PX 或 NCC < NCC_STILL**
            才算"这一滑真动了"、才计一次滑；
          · 连续 ZERO_STALL_LIMIT 滑零位移 ⇒ 换**更慢/更长**的 motionevent 分步拖
            （docs/INPUT_GESTURES.md 实测：`input swipe` 这页不生效，DOWN→MOVE×N→UP 才可靠）；
          · 分步拖后仍连续零位移才认「到顶」；
          · 返回 bool，**调用方必须检查**：失败走"重开页面"兜底，不再在未知起点上扫。
        """
        print(f"归零（{why}）：手指**向下拖**直到滑不动（ms={ms}，真位移核验开）")
        prev_band = _band()
        if prev_band is None:
            print("  归零失败：起手截屏就失败（_band 为 None）⇒ 无法核验 ⇒ return False")
            return False
        stall = 0                       # 连续零位移滑数
        stepwise = False                # 是否已升级为 motionevent 分步拖
        for i in range(1, ZERO_MAX_SLIDES + 1):
            if stall >= ZERO_STALL_LIMIT and not stepwise:
                print(f"  连续 {stall} 滑零位移 ⇒ 升级为 motionevent 分步拖（更慢/更长，{STEPWISE_MS}ms）")
                stepwise = True
            grid_swipe(90, 660, ms=(STEPWISE_MS if stepwise else ms),
                       wait=(2.0 if stepwise else 1.5), stepwise=stepwise)
            cur = _band()
            if cur is None:
                print(f"  归零第{i}滑：截屏失败（_band 为 None）⇒ 无法核验 ⇒ return False")
                return False
            moved, row_px, ncc = true_displacement(prev_band, cur)
            print(f"  归零第{i}滑 真位移核验：moved={moved} 行位移≈{row_px:.0f}px NCC={ncc:.3f}"
                  + ("（分步拖）" if stepwise else ""))
            prev_band = cur
            if moved:
                stall = 0
                continue
            stall += 1
            if stall >= ZERO_STALL_LIMIT and stepwise:
                print("  ⇒ 分步拖后仍连续零位移 ⇒ 已经到顶（再滑也不动）")
                return True
        print(f"  归零滑满上限 {ZERO_MAX_SLIDES} 滑仍在动（没通过到顶核验）⇒ return False")
        return False

    if not hit:
        # **调用方必须检查归零返回值**（提案③：旧版把返回值丢掉 ⇒ 归零失败也照扫，起点是假的）
        if not zero_to_top("扫之前先到顶"):
            print("⚠️ 归零失败（真位移核验不过 / 滑满上限仍在动）⇒ 兜底：重开「完成进度」页签后再归零一次")
            tap(*TAB_PROGRESS, wait=3.0)
            if not zero_to_top("兜底：重开页签后"):
                print(json.dumps({"ok": False, "event": a.event,
                                  "detail": "归零两遍都失败（真位移核验不过）——不知道视口在哪，"
                                            "拒绝在未知滚动位置上扫描；现场多半卡在死屏/弹窗/页面没就绪"},
                                 ensure_ascii=False))
                return 1

    # ③ offsets 只在「归零原点」上记；旧值先判可信度（见 trusted_offset 的注释）。
    off_file = EV_TPL / "offsets.json"
    try:
        offsets = json.loads(off_file.read_text(encoding="utf-8"))
    except Exception:                                # noqa: BLE001
        offsets = {}
    _n, _glen, _why = trusted_offset(a.event)
    print(f"offsets[{PAGE_NAMESPACE}][{a.event}]：{_why}（页面实测长度 {_glen} 屏）")

    scan_cap = int(a.max_screens) if int(a.max_screens) > 0 else 30
    scanned = 0
    _r = 0

    # ②c 快路：按**与记录同一个滑动单位**滑到位再匹配。
    #    ⚠️ 2026-09-24 审计：旧版用 swipe(...,1100,0.35) 复制 1200/2.0 记下的屏号 —— 单位不一致，
    #       而它自己的教训就是「快滑会飞过好几屏」⇒ 位置根本复现不出来。
    if not hit and _n:
        print(f"快路：按记录滑 {_n} 屏直达（慢拖 {OFFSET_UNIT}，与记录同单位）")
        for _ in range(_n):
            grid_swipe(420, 150)                 # ms=1200 默认值提到 grid_swipe 签名上
        hit = find_event(a.event)
        if hit:
            print(f"快路命中：{a.event} 在 ({hit['x']},{hit['y']}) score={hit['score']}")
        else:
            # ⚠️ **快路没中必须重新归零再扫**（2026-09-24 根因）：旧版接着往下扫，卡片被留在
            #    视口上方 ⇒ 越扫越远，一路到底也看不到；而把参数值当成实扫屏数写进日志，
            #    把"其实没扫几屏"伪装成"扫了 16 屏"。
            print("快路没中 ⇒ **重新归零**再从头扫（不接着往下扫）")
            if not zero_to_top("快路落点不可信"):
                print("⚠️ 快路后归零失败 ⇒ 兜底：重开「完成进度」页签后再归零一次")
                tap(*TAB_PROGRESS, wait=3.0)
                if not zero_to_top("兜底：快路失败、重开页签后"):
                    print(json.dumps({"ok": False, "event": a.event,
                                      "detail": "快路没中且两遍归零都失败（真位移核验不过）——"
                                                "拒绝在未知滚动位置上扫描"}, ensure_ascii=False))
                    return 1
            hit = None

    if not hit:
        # **边滑边检，一直滑到底**（用户 2026-09-24 口径）：先到顶，再一屏一屏往下慢拖、
        #   停稳再截图、每屏都检一次，直到「再滑也不动」= 到底。不用固定屏数（页长会变）。
        # **2026-09-25 加固**：到底判据从"裸帧差<1.0"换成 `true_displacement()` 真位移核验
        #   （提案①：没滚动时帧差也≈0，动画又会把帧差抬高 ⇒ 旧判据两头都会被骗）；
        #   连续 ZERO_STALL_LIMIT 滑零位移才认到底，其间先升级 motionevent 分步拖再试一次。
        prev = _band()
        i = 0
        stall = 0
        use_step = False
        unit_used = OFFSET_UNIT       # 诚实记账：真用了分步拖的那一屏不冒充 slow1200 单位
        while i < scan_cap:
            hit = find_event(a.event)
            if hit:
                print(f"第 {i} 屏命中（绝对屏号 = 归零原点起算的慢拖屏数）：{a.event} 在 "
                      f"({hit['x']},{hit['y']}) score={hit['score']}")
                try:
                    _offset_put(offsets, a.event,
                                {"screen": i, "unit": unit_used, "zeroed": True,
                                 "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
                    off_file.write_text(json.dumps(offsets, ensure_ascii=False, indent=1), encoding="utf-8")
                except Exception:                    # noqa: BLE001
                    pass
                break
            print(f"第 {i} 屏没有 ⇒ 往下翻一屏（慢拖 + 停稳再截）")
            if use_step:
                grid_swipe(420, 150, ms=STEPWISE_MS, stepwise=True)
                unit_used = OFFSET_UNIT + "+step1600"
            else:
                grid_swipe(420, 150)                 # ms=1200（默认值就在签名上）
            cur = _band()
            scanned += 1
            i += 1
            if prev is None or cur is None:
                print("  ⇒ 截屏失败（band 为 None）⇒ 无法核验位移 ⇒ 停止扫描（不在未知位置上继续）")
                break
            moved, row_px, ncc = true_displacement(prev, cur)
            print(f"    真位移核验：moved={moved} 行位移≈{row_px:.0f}px NCC={ncc:.3f}")
            prev = cur
            if moved:
                stall = 0
                use_step = False
                continue
            stall += 1
            if stall >= ZERO_STALL_LIMIT:
                print(f"  ⇒ 连续 {stall} 滑零位移（真位移核验不过）⇒ 已经滑到底，停止扫描")
                break
            print("    这一滑没产生真位移 ⇒ 下一滑升级为 motionevent 分步拖（更慢/更长）")
            use_step = True
        else:
            print(f"  ⇒ 已扫到上限 {scan_cap} 屏仍未命中")

    if not hit:
        # **就地重试，不重走流程**（2026-09-24 审计重写）：旧版失败时把整个脚本再跑一遍
        #   （重新 to_book → 开网格 → 归零 → 扫 16 屏），一次失败就翻倍耗时；而且递归那遍
        #   漏传 --prefix ⇒ looks_like_map("") 退化，会把"其实还在网格页"判成成功。
        #   实测失败多是"页面还没就绪/停在别处" ⇒ 重开页签 + 多等一会儿 + 再扫几屏足够。
        print("第一遍没找到 ⇒ 就地重试：重开「完成进度」→ 等就绪 → 再扫最多 3 屏（不重走前面的流程）")
        tap(*TAB_PROGRESS, wait=3.0)
        for _try in range(6):
            hit = find_event(a.event)
            if hit:
                break
            time.sleep(1.5)
        while not hit and _r < 3:
            grid_swipe(420, 150)                 # ms=1200 默认值提到 grid_swipe 签名上
            _r += 1
            hit = find_event(a.event)
        if hit:
            print(f"  就地重试命中（未归零 ⇒ 不记 offsets）：{a.event} 在 ({hit['x']},{hit['y']}) "
                  f"score={hit['score']}")
    if not hit:
        print(json.dumps({"ok": False,
                          "detail": f"两遍都没找到 {a.event}（第一遍**实扫 {scanned} 屏**/上限 {scan_cap}；"
                                    f"就地重试扫 {_r} 屏；offsets 判读：{_why}）"},
                         ensure_ascii=False))
        return 1

    # ④ 点卡 → 核验详情页 → 进活动
    tap(hit["x"], hit["y"], wait=3.0)
    pg = page_now()
    print("点卡后页面：", pg)
    if pg != "乐章详情":
        print(json.dumps({"ok": False, "detail": f"点了卡但页面是「{pg}」，不是乐章详情"}, ensure_ascii=False))
        return 1
    tap(*find_enter_button(), wait=2.0)
    # **进图确认**（2026-09-24）：点了「进入活动」不等于进去了 —— 实测会被弹回「完成进度」网格
    #   （页面指纹报"未知"，分数 0.747），于是后面所有导航都必然失败。
    #   这里轮询确认真的到了地图；没到就再点一次「进入活动」，仍没到就如实报 ok:false，
    #   交给队列的重试逻辑（而不是假装成功、让导航白跑几分钟）。
    pg2 = ""
    for _try in range(3):
        deadline = time.time() + 5.0
        while time.time() < deadline:
            pg2 = page_now()
            if pg2 in ("活动地图", "关卡详情") or looks_like_map(a.prefix):
                break
            time.sleep(0.4)
        if pg2 in ("活动地图", "关卡详情") or looks_like_map(a.prefix):
            break
        if _try == 0:
            # 实测坐标点不进去 ⇒ 退到"白色长方形"检测（审计：`_guess_enter_button` 之前是死代码）
            _btn = _guess_enter_button()
            print(f"  第 1 次（实测坐标）没进 ⇒ 退到白框检测的按钮 {_btn} 再点一次")
            tap(*_btn, wait=2.5)
            continue
        print(f"  第 {_try+1} 次点「进入活动」后仍不在活动地图（当前「{pg2}」）⇒ 再点一次")
        tap(*find_enter_button(), wait=2.5)
    if pg2 not in ("活动地图", "关卡详情") and not looks_like_map(a.prefix):
        print(json.dumps({"ok": False, "detail": f"点了「进入活动」但仍不在活动地图（当前「{pg2}」）",
                          "event": a.event, "card": hit}, ensure_ascii=False))
        return 1
    # **进 EX 区**：进图后默认在"主区"，EX 关在另一张区域卡上（CV 实测：mt_nav 在主区
    #   找不到 CV-EX-1）。区域卡坐标写在 data/nav_nodes.json 的 zones 里（我读图填的）。
    if a.zone_ex:
        try:
            cfg = json.loads((ROOT / "data" / "nav_nodes.json").read_text(encoding="utf-8"))
            slug_prefix = (a.prefix or "").upper() or {"buyizhicai": "CV"}.get(a.event, a.event)
            cards = (cfg.get("zones") or {}).get(slug_prefix, {})
            pt = cards.get("EX")
            if pt:
                print(f"切到 EX 区：点区域卡 {pt}")
                tap(*pt, wait=3.0)
        except Exception as e:                       # noqa: BLE001
            print("切区失败（忽略）：", e)
        pg2 = page_now()
    # 页面指纹只认识少数几张图（PV/MT/EP/LE 的地图）；新活动（如 CV）会报"未知"。
    # 判成功的口径放宽成"**已经不在乐章详情页**"（说明「进入活动」生效了），
    # 这比死等一个我还没学过的页面模板实用。
    # **成功判据**（2026-09-24 审计收紧）：必须是「活动地图/关卡详情」页面指纹，**或**
    #   `looks_like_map(prefix)`（图上有带本活动代号的白框）。旧判据 `pg2 != "乐章详情"` 太松：
    #   停在曲谱/网格/任何别页都会判成功（实测 09:57:34 就是这么报出 `ok:false` 却走到最后的）。
    in_map = pg2 in ("活动地图", "关卡详情") or looks_like_map(a.prefix)
    ok = bool(in_map)
    print(json.dumps({"ok": ok, "page": pg2, "event": a.event, "card": hit}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
