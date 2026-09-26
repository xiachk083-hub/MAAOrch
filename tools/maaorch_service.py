#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAAOrch 常驻查询服务 —— 把"看屏幕/看状态"从冷启动脚本变成毫秒级 HTTP 调用。

用户 2026-09-24 的指点（照做）：
  · 不生成脚本，直接调工具；· 不冷启动，Python 常驻；· 不全量读，预建缓存；· 不串行等，并发+流式。
之前每次 `python tools/look.py` 要 8 秒（**RapidOCR 模型每次重新加载 4~6 秒**），
串三个脚本（enter_event→mt_nav→panel_gate）就是 30~60 秒。本服务把 OCR 引擎、adb 句柄、
路径都常驻在内存里，之后每次查询只有一次截屏 + 一次推理。

零依赖实现（标准库 http.server，不用装 FastAPI/uvicorn）。

端点:
  GET /health                     → {"ok":true}
  GET /look                       → 屏幕文字 + 面板字段（stars/sanity），~0.5~2s
  GET /state                      → 队列状态/待打单位/战绩统计（纯文件读，~10ms）
  GET /gate?code=PV-EX-3&want=普通 → 导航到该关并读面板（会调 mt_nav，慢一些）
  GET /shot?path=...              → 截一张图存到指定路径（默认 runtime/_svc.png）
  GET /recover?max=6              → 通用恢复：从档案/曲谱/干员页退回大厅（最多 6 次 BACK）

启动（常驻）:
  python tools/maaorch_service.py --port 8765            # 前台
  Start-Process python -ArgumentList 'tools\\maaorch_service.py' -WindowStyle Hidden   # 后台
用法:
  curl -s http://127.0.0.1:8765/look
  curl -s http://127.0.0.1:8765/state
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
LEARNED = ROOT / "runtime" / "learned"      # 运行时学习产物（不随仓库发布的游戏数据）
sys.path.insert(0, str(ROOT / "tools"))
from _dev import resolve_dev  # noqa: E402

ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
SHOT = ROOT / "runtime" / "_svc.png"
_LOCK = threading.Lock()
_OCR_LOCK = threading.Lock()     # OCR 串行化：剧情看门狗/`/look` 可能同时打进来，见 texts()
_ENG = None
_DEV = {}


def engine():
    global _ENG
    if _ENG is None:
        from rapidocr_onnxruntime import RapidOCR
        try:
            # 本机装了 onnxruntime-directml（RTX 3060），全屏 OCR 实测 1322ms → 915ms。
            # 只是"能用显卡就用"，装不上/DML 不支持就退回 CPU，不影响功能。
            _ENG = RapidOCR(use_dml=True)
        except Exception:
            _ENG = RapidOCR()
    return _ENG


def dev(emu: str = "1") -> str:
    if emu not in _DEV:
        _DEV[emu] = resolve_dev(emu)
    return _DEV[emu]


_MAA = {"client": None, "tried": False}


def maa_client():
    """⚠️ 这个返回的是 MAA 绑定里的 ``get_image``，它拿的是**缓存帧**（2026-09-24 实测：
    按一次 BACK 让画面必然变化，连续三次 get_image 的 MD5 一模一样）⇒ **不能用来做复核**。
    这里只保留给"不要求新鲜"的场景；复核/判面板一律走 `maa_core()`（强制新截）。"""
    if _MAA["tried"]:
        return _MAA["client"]
    _MAA["tried"] = True
    try:
        MAA_DIR = ROOT / "services" / "maa" / "v6.11.1"
        sys.path.insert(0, str(MAA_DIR / "Python"))
        from asst.asst import Asst
        from asst.utils import InstanceOptionType
        if not Asst.load(path=str(MAA_DIR)):
            return None
        c = Asst()
        c.set_instance_option(InstanceOptionType.touch_type, "maatouch")
        if not c.connect(ADB, dev("1"), "MuMuEmulator12"):
            return None
        _MAA["client"] = c
    except Exception:
        _MAA["client"] = None
    return _MAA["client"]


_CORE = {"c": None, "tried": False}


def maa_core():
    """**真·取新帧**：ctypes 直调 MaaCore 的 `AsstAsyncScreencap(block=True)` + `AsstGetImageBgr`
    （BGR 原始像素，省掉 PNG 编解码），并带 MuMu 的连接附加信息走它的私有通道。
    实测中位 **156~169ms**（adb raw 是 180~220ms，绑定 get_image 虽然 42ms 但是**陈帧**）。"""
    if _CORE["tried"]:
        return _CORE["c"]
    _CORE["tried"] = True
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("maa_frame_mod", ROOT / "tools" / "maa_frame.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)                        # type: ignore
        extras = '{"mumu": {"path": "D:\\\\Xiach\\\\MuMuPlayer\\\\nx_main", "index": 1}}'
        _CORE["c"] = mod.Core("1", "MuMuEmulator12", extras)
    except Exception:
        _CORE["c"] = None
    return _CORE["c"]


def shot_fresh(path: pathlib.Path = SHOT):
    """取一张**新鲜**帧：MaaCore BGR(~160ms) → adb raw(~200ms)。"""
    c = maa_core()
    if c is not None:
        try:
            img = c.fresh()
            if img is not None:
                if path != SHOT:
                    import cv2
                    cv2.imwrite(str(path), img)
                return img
        except Exception:
            pass
    return shot_adb(path)


def shot(path: pathlib.Path = SHOT, emu: str = "1"):
    """对外统一入口：**新鲜帧**（历史原因保留旧签名）。"""
    return shot_fresh(path)


def shot_adb(path: pathlib.Path = SHOT, emu: str = "1"):
    """adb 兜底：raw RGBA（180~220ms）；raw 拿不到再退 PNG。"""
    import cv2
    import numpy as np
    with _LOCK:
        r = subprocess.run([ADB, "-s", dev(emu), "exec-out", "screencap"],
                           stdout=subprocess.PIPE, timeout=60)
    raw = r.stdout
    h, w = 720, 1280
    if len(raw) >= 16 + h * w * 4:
        arr = np.frombuffer(raw, dtype=np.uint8)[16:16 + h * w * 4].reshape((h, w, 4))
        img = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    else:
        with open(path, "wb") as f:
            subprocess.run([ADB, "-s", dev(emu), "exec-out", "screencap", "-p"],
                           stdout=f, timeout=60)
        img = cv2.imread(str(path))
    if path != SHOT and img is not None:
        cv2.imwrite(str(path), img)
    return img


# ── 拖拽/滑动：**motionevent**，不是 `input swipe` ──────────────────────
# Lead 2026-09-24 实测（`docs/INPUT_GESTURES.md`）：`input swipe` 在游戏里**经常不生效**
#   （滑「完成进度」页 3 次、逐像素比对证明零位移；拖干员"拖起来放不下"），
#   而 `DOWN → 若干 MOVE → UP`（每步 200~300ms、DOWN 后先停一下）游戏才认。
def motionevent_cmds(x1: int, y1: int, x2: int, y2: int, ms: int = 700, steps: int = 4) -> list:
    """拖拽的 adb 命令序列（**纯函数**，可离线自测）：DOWN 起点 → steps 次等距 MOVE → UP 终点。
    `ms` 是整段手势的总时长（用来算每步间隔），`steps` 是中间 MOVE 的次数。"""
    steps = max(1, min(12, int(steps)))
    ms = max(50, min(4000, int(ms)))            # noqa: F841 —— 时长只用于调用方的 sleep 节奏
    cmds = [["shell", "input", "motionevent", "DOWN", str(int(x1)), str(int(y1))]]
    for i in range(1, steps + 1):
        xi = round(x1 + (x2 - x1) * i / steps)
        yi = round(y1 + (y2 - y1) * i / steps)
        cmds.append(["shell", "input", "motionevent", "MOVE", str(int(xi)), str(int(yi))])
    cmds.append(["shell", "input", "motionevent", "UP", str(int(x2)), str(int(y2))])
    return cmds


def drag(x1: int, y1: int, x2: int, y2: int, ms: int = 700, steps: int = 4, emu: str = "1") -> dict:
    """按 `motionevent_cmds` 发一次拖拽。DOWN 之后先停 0.2s（给游戏识别"长按"的时间）。"""
    t0 = time.time()
    cmds = motionevent_cmds(x1, y1, x2, y2, ms, steps)
    per = max(0.05, min(0.35, (max(50, int(ms)) / 1000.0) / (len(cmds) + 1)))
    for i, cmd in enumerate(cmds):
        try:
            subprocess.run([ADB, "-s", dev(emu), *cmd], capture_output=True, timeout=20)
        except Exception:                                 # noqa: BLE001 —— 单步失败不该抛给调用方
            pass
        time.sleep(0.2 if i == 0 else per)
    return {"ok": True, "x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2),
            "ms": int(ms), "steps": int(steps), "n_cmds": len(cmds),
            "cost_ms": int((time.time() - t0) * 1000)}


def _drag_step(st) -> dict | None:
    """把坐标表里的 `["swipe", x1, y1, x2, y2]` 用 **motionevent** 执行。
    为什么换掉 `fastnav.Shell.swipe`：Lead 实测 `input swipe` 在游戏里经常零位移
    （`docs/INPUT_GESTURES.md`），地图翻页/归一化会静默失败 ⇒ 统一走 motionevent。"""
    try:
        args = [int(v) for v in st[1:5]]
    except Exception:                                     # noqa: BLE001
        return None
    if len(args) < 4:
        return None
    return drag(*args, steps=5, ms=800)


def texts(img):
    # **OCR 串行化**（2026-09-24，task-3）：新加的剧情看门狗会高频调 `/skipstory`（也要 OCR
    #   右上角那一小块），和 sweep 的 `/look`、`/all?text=1` 可能同时打进来；RapidOCR 实例
    #   不是为并发调用设计的 ⇒ 用一把锁排成队。单次小块 OCR 只有一百多毫秒，排队不会更慢，
    #   并发才会互相抢显存/互相踩状态。
    with _OCR_LOCK:
        res, _ = engine()(img)
    out = []
    for box, t, *_ in (res or []):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append((int(sum(xs) / len(xs)), int(sum(ys) / len(ys)), t.strip()))
    return out


def panel(img, page: str | None = None) -> dict:
    """面板判据 —— **薄壳**，唯一实现是 `tools/panel_gate.py::read_panel()`。

    2026-09-24 独立审计抓到的两处硬 bug（都在这条旧实现里，别再抄回来）：
      ① 徽章 ROI 的 x/y **写反**：旧代码 `img[620:1280, 430:860]`，
         正确是 `REWARD_ROI=(y 430:620, x 860:1280)` ⇒ 站在**有徽章**的 EA-1 面板上
         只报 0.644/`reward_primes=false`（真值 **0.999/true**）⇒ 全是假 ⊘，白漏矿；
      ② 星星除数写成 490（应为 1470/颗）且不封顶 ⇒ 3 星面板报 **9 星**、地图页报 3 星。
    现在星星由 main_nav 标定口径给出（封顶 3、<300 记 0），且**页面不是「关卡详情」时
    stars/cleared/reward_primes 一律 null**（低置信读数不许拿去判 done/⊘）。
    """
    import panel_gate
    return panel_gate.read_panel(img, page=page)


# ── 页面指纹（模板常驻内存，一次加载）────────────────────────────────────
_PAGES: list[tuple[str, "object", tuple | None]] = []
# **结构性判据的闸门**（2026-09-24 实测标定，见 `page_of` docstring）：
#   地图模板最高分 ≥ 0.70 且页面判不准时，才去付"名条 OCR"的钱。
#   实测（466 张语料）：闸门 0.70 只触发 9 张（2%），覆盖 3/3 真地图；
#   闸门 0.78 会漏掉 `_pv.png`(0.772)；闸门 0.60 触发数一样但余量更小。
#   非地图页的地图模板最高分：关卡详情 0.646 / 乐章详情 0.568 / 结算页 0.549 /
#   曲谱 0.517 / 大厅 0.478 ⇒ 0.70 这条线把它们全挡在外面（不会引入假阳性）。
STRUCT_GATE = 0.70


def pages_load():
    """把 data/templates/pages/*.png 读进内存一次（原来每次判别都要重新读盘 + 重新 import）。"""
    if _PAGES:
        return _PAGES
    import cv2
    specs = [("大厅", "lobby.png", (1000, 0, 1280, 90)),
             ("曲谱", "book.png", (1000, 0, 1280, 100)),
             # **曲谱页的第二张模板 `book_alt.png`**（2026-09-24 查"两天 207 行 blocked=报告的 51%"
             #   时定位）：曲谱页有**两种形态**，`book.png` 只覆盖其一（右上角「乐章收录」大胶囊，自匹配
             #   1.000）；另一种右上角是「日历｜乐章」分段控件，`book.png` 只给 **0.747** ⇒ 被 0.80
             #   门槛压成「未知」⇒ `enter_by_template.py:361/375` 的 `page_now() != "曲谱"` 立刻收手、
             #   `mt_nav` 危险闸报「不在活动图里」⇒ 那 207 行就是这么来的（AD/PV 有模板也照样中招）。
             #   book_alt.png 裁自 20:26 真帧 (1058,26)-(1252,84)：在 MT/EP/AD/CV 五张 0.747 帧上
             #   **1.000**，在「乐章收录」屏 0.73（与 book.png 取 max ⇒ 仍 1.000），大厅 0.34、
             #   乐章详情 0.37 ⇒ 分离干净。同名模板在 `scores` 里按最大值合并（见 `:322`）。
             ("曲谱", "book_alt.png", (1000, 0, 1280, 100)),
             ("乐章详情", "chapter.png", (900, 520, 1280, 700)),
             ("活动地图", "map.png", (850, 520, 1280, 719)),
             ("关卡详情", "stage.png", (900, 560, 1280, 719)),
             ("结算页", "result.png", (0, 120, 700, 380)),
             # **活动地图模板**（2026-09-24 修，auditor-1 发现）：这四张原来叫
             #   `鍦板浘_EP.png`（"地图_EP" 用错编码写出来的乱码名），而且**没列进 specs**
             #   ⇒ page_of 永远认不出任何活动地图（EA 地图 0.391「未知」）⇒ "进图守卫"永远看到「未知」、
             #   把成功判成失败。文件名已改成 ASCII 并在此登记（整屏匹配，不带 ROI）。
             ("活动地图", "map_EP.png", (0, 0, 1280, 720)),
             ("活动地图", "map_LE.png", (0, 0, 1280, 720)),
             ("活动地图", "map_MT.png", (0, 0, 1280, 720)),
             ("活动地图", "map_PV.png", (0, 0, 1280, 720)),
             ("活动地图", "map_EA.png", (0, 0, 1280, 720)),
             ("活动地图", "map_CV.png", (0, 0, 1280, 720))]
    for name, f, search in specs:
        p = ROOT / "data" / "templates" / "pages" / f
        if not p.exists():
            continue
        t = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if t is not None:
            _PAGES.append((name, t, search))
    return _PAGES


def page_of(img, struct: bool | None = None, prefix: str = "", ocr_text: str | None = None) -> dict:
    """页面指纹 + **阈值与余量规则** + **活动地图的结构性判据**（2026-09-24 task-14）。

    2026-09-24 踩坑：EP EX 地图被误判成「曲谱」——因为旧的曲谱模板是右上角一块浅色区域，
    在地图背景上也能匹配到 0.715。现在：① 模板换成结构性特征（蓝色按钮/区域卡/胶囊）；
    ② 要求最佳 ≥0.80 且比第二名高 ≥0.06，否则报 `未知`（调用方据此退回 OCR 导航）。

    **结构性判据**（`struct`）：模板**只覆盖自己那一屏** ⇒ 新活动/新视图退化成「未知」：
    实测真地图 `_map_mt` 0.794 / `_mt_act` 0.791 / `_pv` 0.772 全被 0.80 压成未知，而同一分数带
    里混着 `_stone_dlg` 0.782（用源石恢复理智弹窗）/`_dlg*` 0.783（剧情对话）⇒ **单阈值分不开**。
    于是补一条与活动无关的结构判据：`stage_boxes.is_activity_map()`（白名条 + 条内读得出 `XX-n`）。
    `struct=None`（默认）= **只在"模板说不准但接近"时才算**（判未知 且 地图模板最高分 ≥0.70）：
    实测在 466 张语料上只触发 **9 张（2%）**，而那 9 张正是"原本只能拿到未知"的屏；
    无条件跑会命中 82% 的屏（几何预筛不挑食）⇒ 会冲破"单步 <0.3s"预算。
    需要"绝对确信"的一次性核验（进活动落地）可以传 `struct=True` 无条件跑。
    """
    import cv2
    # **黑帧守卫**（2026-09-24 独立审计实测）：取帧失败/游戏切场时会出现**全黑帧**
    #   （实测 `runtime/_mapcheck.png` 有一版是 mean=0.0）。这种帧送去匹配，10 个模板
    #   全是 0.000、margin 也是 0 ⇒ 返回「未知(page_score=0)」，调用方**分不清**
    #   "这是张废图"和"这是一屏我没见过的页面"，容易接着做无用导航。
    #   这里显式报 `page="黑帧"`，让调用方一眼看出是**取帧**的问题、去重截而不是去导航。
    #   阈值 8.0 取得很松：真机任何一屏（连最暗的加载画面）实测 mean 都 >60。
    if float(img.mean()) < 8.0:
        return {"page": "黑帧", "page_score": 0, "page_scores": {},
                "detail": "画面近乎全黑（取帧失败/切场中）⇒ 请重截，别据此导航"}
    scores = {}
    for name, t, search in pages_load():
        roi = img[search[1]:search[3], search[0]:min(search[2], img.shape[1])] if search else img
        if roi.size == 0:
            continue
        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # **2026-09-24 修的自身 bug**：原来写的是 `t.shape >= g.shape ⇒ continue`，
        #   而"整屏地图模板"（720x1280）恰好等于整屏 ROI（720x1280）⇒ **永远被跳过**
        #   ⇒ 活动地图永远认不出（EA 地图 0.391「未知」，而模板自匹配其实是 1.000）。
        #   现在只跳过"严格大于 ROI"的模板；等大时 matchTemplate 返回 1x1，正好是自匹配。
        if t.shape[0] > g.shape[0] or t.shape[1] > g.shape[1]:
            continue
        sc = round(float(cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED).max()), 3)
        # 同名多模板（EP/LE/MT/PV/EA 都叫「活动地图」）要取**最大值**，不能互相覆盖。
        if sc > scores.get(name, -1):
            scores[name] = sc
    if not scores:
        return {"page": "未知", "page_score": 0, "page_scores": {}}
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, second = ranked[0], (ranked[1] if len(ranked) > 1 else ("", 0))
    ok = best[1] >= 0.80 and (best[1] - second[1]) >= 0.06
    out = {"page": best[0] if ok else "未知", "page_score": best[1],
           "page_margin": round(best[1] - second[1], 3), "page_scores": scores}

    # ── 活动地图的结构性判据 ────────────────────────────────────────────────
    # 三条路径，按"付不付得起"排序：
    #   ① `ocr_text` 由调用方给（**免费**）：调用方本来就要 OCR（`/all?text=1`、`/look`、
    #      `nav_to` 的宽松前置、enter_by_template…）⇒ 白拿一次结构判据；
    #   ② `struct=True`：无条件跑（1.2~6s，一次性核验流程用，别放进每步循环）；
    #   ③ 默认：`page==未知 且 地图模板最高分≥0.70` 才跑（快，但**认不出全新地图**——
    #      真机首验 AD 新地图时 6 帧的 map_max 只有 0.367~0.585 ⇒ 一次都没触发，这就是断点）。
    #      ⇒ 全新活动请走 ①/②；这是**已知边界**，写在注释里而不是假装没有。
    import stage_boxes
    # **结构判据只允许把「未知」改成「活动地图」，不许推翻一个有把握的判定**
    #   （2026-09-24 真机首验的第二个教训）：文本路径第一版是无条件生效的，结果
    #   `_verify.png`（EA-1 面板，关卡详情 0.98）与 `_c3.png`（结算页 0.982）**都被翻成活动地图** ——
    #   因为面板标题/结算页上本来就写着 `EA-1` / `MT-EX-8`。那会把 `read_panel()`/`event_sweep`
    #   的置信门槛（`page=='关卡详情'`）直接毁掉。
    #   规则：只有在"模板给不出结论（未知）"时才让结构判据说话。
    def _apply_struct(codes: list[str], src: str, min_codes: int) -> bool:
        uniq = []
        for c in codes:
            if c not in uniq:
                uniq.append(c)
        want = f"{prefix.upper()}-" if prefix else ""
        # 前缀比对用 **包含**：OCR 会把前一个词的尾字母粘上来（实测 `WAD-3`/`IAD-TR-1`/`UEA-ST-1`），
        #   严格 startswith 会把"其实认对了"的屏判成不是本活动。
        pref_ok = any(want in c for c in uniq) if want else True
        # **门槛分两条路**（2026-09-24 真机首验的第三个教训）：
        #   · 文本路径（没有任何"名条"锚点）要求 **≥2 个不同代号** —— 地图的特征是"一排名条"；
        #     只要求"出现过 1 个像代号的东西"会被随手一段文字骗到（实测 `_refill.png` 里有个
        #     `ESS-47` 就被判成活动地图）。
        #   · 图像路径**有白名条当锚**（框内 OCR 读出来的），1 个就足够 —— 实测 `_pv.png` 屏上
        #     只有一个 `PV-S-1` 名条，要求 ≥2 会把这张真地图打回未知。
        hit = len(uniq) >= min_codes and pref_ok
        out.update({"struct_map": hit, "struct_codes": uniq, "struct_src": src})
        if hit and out["page"] == "未知":
            out["page"] = "活动地图"
            out["page_src"] = f"结构判据（{src}）"
            return True
        return False

    map_max = scores.get("活动地图", 0.0)
    if ocr_text is not None and out["page"] == "未知" and _apply_struct(
            stage_boxes.codes_in_text(ocr_text), "调用方 OCR 文本里的关卡代号", 2):
        return out

    need = struct if struct is not None else (out["page"] == "未知" and map_max >= STRUCT_GATE)
    if need and (out["page"] == "未知" or struct is True):
        try:
            r = stage_boxes.is_activity_map(img, prefix=prefix, ocr=engine())
            out.update({"struct_bars": r["bars"], "struct_ms": r["ocr_ms"]})
            _apply_struct(r["codes"], "白名条+框内/全屏 OCR", 1)
        except Exception as e:                       # noqa: BLE001
            out["struct_err"] = f"{type(e).__name__}: {e}"
    return out


# ── 关卡身份"校准块"（像千分尺归零：位置归一 + 读数比对）─────────────────
STAGE_TPL = ROOT / "data" / "templates" / "stages"
CODE_ROI = (860, 80, 1010, 120)          # 面板右上角那行 "PV-EX-3"


def code_crop(img):
    x0, y0, x1, y1 = CODE_ROI
    return img[y0:y1, x0:x1]


def verify_stage(img, stage: str) -> dict:
    """拿面板上的关卡代号与目标比对。

    有模板 ⇒ 模板匹配（~5ms，**这是"归零"后的读数**）；
    没模板 ⇒ 只报 `unknown`，由调用方决定要不要 OCR 学一次（`learn_stage`）。
    """
    import cv2
    tpl = STAGE_TPL / f"{stage}.png"
    if not tpl.exists():
        return {"stage_match": None, "stage_score": None, "have_template": False}
    t = cv2.imread(str(tpl), cv2.IMREAD_GRAYSCALE)
    c = cv2.cvtColor(code_crop(img), cv2.COLOR_BGR2GRAY)
    # 注意：模板就是同一块 ROI 裁的 ⇒ **尺寸相等是正常的**，只有严格更大才算不匹配
    # （2026-09-24 踩过：写成 >= 会把"模板已存在"判成"没有模板"，于是每次都白跑一次 OCR）
    if t is None or t.shape[0] > c.shape[0] or t.shape[1] > c.shape[1]:
        return {"stage_match": None, "stage_score": None, "have_template": False}
    s = float(cv2.matchTemplate(c, t, cv2.TM_CCOEFF_NORMED).max())
    return {"stage_match": s >= 0.75, "stage_score": round(s, 3), "have_template": True}


def learn_stage(img, stage: str) -> bool:
    """把当前面板的代号区域存为该关模板（只在**确认过身份**后调用）。"""
    import cv2
    STAGE_TPL.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(STAGE_TPL / f"{stage}.png"),
                            cv2.cvtColor(code_crop(img), cv2.COLOR_BGR2GRAY)))


def code_matches(stage: str, ocr_text: str) -> bool:
    """宽松比对：OCR 常出小毛病（实测把 `EP-EX-8` 读成 `EP-EEX-8`），
    所以只核**前缀 + EX/S/SP 类型 + 关卡号**，不要求逐字符相等。"""
    import re
    t = re.sub(r"[^A-Z0-9]", "", stage.upper())
    o = re.sub(r"[^A-Z0-9]", "", (ocr_text or "").upper())
    if not o:
        return False
    pre = t[:2]                       # 只取前两个字母（"EP"）；取全部字母会把 "EPEX" 当前缀，反而对不上
    if pre and pre not in o:
        return False
    kind = "EX" if "EX" in t else ("SP" if "SP" in t else "")
    if kind and kind not in o:
        return False
    num = re.search(r"(\d+)$", t)
    if num and not re.search(rf"{num.group(1)}(?!\d)", o):
        return False
    return True


def ocr_code(img) -> str:
    """只 OCR 代号那小块（~1.1s），用于**首次确认**某关、随后就把模板学下来。"""
    with _OCR_LOCK:                      # 与 texts() 同一把锁（别和剧情看门狗抢引擎）
        res, _ = engine()(code_crop(img))
    return "".join(t for _b, t, *_ in (res or []))


# ── 剧情跳过（"从没打过的关会先播剧情" —— 2026-09-24 实测，task-3）──────────
# 实测配方（坐标都在 1280x720 的截图里定的）：
#   ① 剧情界面右上角有「**跳过 ▶**」→ 点 (1200,53)（左边还有「自动」，点 x≈1000~1080 会点到自动）
#   ② 弹出「**是否跳过剧情?**」→ 右侧确认 ⊙ 在 (796,462)（左侧 ⊗ 是取消，≈(476,462)）
#   ③ 跳过之后才会进战斗/结算
# 做成**服务端一次调用**的原因：看门狗要高频问（MAA 打关的那几分钟），
#   每问一次就 spawn 一个 Python / 做一次全屏 OCR 都是白烧（那条目标是"极致快"）。
SKIP_ROI = (1050, 20, 1279, 90)      # 只看右上角这一小块来判断"有没有剧情"
SKIP_BTN = (1200, 53)                # 「跳过 ▶」
SKIP_CONFIRM = (796, 462)            # 「是否跳过剧情?」右侧确认 ⊙
SKIP_DLG_ROI = (380, 405, 920, 505)  # 确认弹窗的文字条（"是否跳过剧情?"）
_SKIP = {"seen": 0}                  # 连续看到"跳过"的次数（防 OCR 漏认弹窗）


def skip_story(img=None, src: str = "maa") -> dict:
    """看右上角有没有「跳过」；有就把整套动作做完：点「跳过 ▶」→ 认弹窗 → 点确认 ⊙。

    返回 `{"ok","found","taps","dialog","text","cost_ms","src"}`。
    `src="adb"` ⇒ 取帧走 **adb screencap**，不碰 MaaCore/MuMu 私有通道 —— MAA 正在同一台
    模拟器上打关时（它自己也用那条通道取帧）用这个更稳：两条链路互不排队。
    纪律（别退回老路）：① 只 OCR `SKIP_ROI` 这一小块，**不全屏**；② 点确认前先在
    `SKIP_DLG_ROI` 里认一次弹窗 —— 如果"跳过"是直接生效的（没弹窗），那一下点击会点到
    别的东西上；③ 若连续 3 次还看见「跳过」（说明弹窗在、但 OCR 没认出来），按实测配方
    硬点一次确认。
    """
    t0 = time.time()
    if img is None:
        img = shot_adb(SHOT) if src == "adb" else shot()
    if img is None:
        return {"ok": False, "found": False, "err": "截图失败", "src": src}
    roi = img[SKIP_ROI[1]:SKIP_ROI[3], SKIP_ROI[0]:SKIP_ROI[2]]
    txt = " ".join(t for _, _, t in texts(roi))
    found = "跳过" in txt
    out = {"ok": True, "found": found, "text": txt[:80], "taps": [], "src": src}
    if not found:
        _SKIP["seen"] = 0
        out["cost_ms"] = int((time.time() - t0) * 1000)
        return out

    fn = _shell()
    fn.Shell.get().tap(*SKIP_BTN)
    out["taps"].append("skip")
    time.sleep(0.7)                                   # 弹窗出现/剧情推进的固定短等
    img2 = shot_adb(SHOT) if src == "adb" else shot()
    dlg = ""
    if img2 is not None:
        dlg = " ".join(t for _, _, t in texts(img2[SKIP_DLG_ROI[1]:SKIP_DLG_ROI[3],
                                                  SKIP_DLG_ROI[0]:SKIP_DLG_ROI[2]]))
    _SKIP["seen"] += 1
    if "跳过" in dlg or "是否" in dlg or _SKIP["seen"] >= 3:
        fn.Shell.get().tap(*SKIP_CONFIRM)
        out["taps"].append("confirm")
        out["dialog"] = (dlg[:60] or "forced-3rd")    # 认不出弹窗时的兜底（照实测配方点）
        _SKIP["seen"] = 0
    else:
        out["dialog"] = ""
    out["cost_ms"] = int((time.time() - t0) * 1000)
    return out


def map_scan(prefix: str) -> dict:
    """**不用进详情页**，直接从地图读每关的完成状态（用户 2026-09-24 提供的游戏细节）：

        · 蓝色六边形   = 普通三星（源石已领）
        · 白色六边形   = 连突袭也过了（这颗石头彻底没了）
        · 灰色/缺一角  = 没到三星（普通源石还可能挂着）

    一次取帧 + 每个节点采样几毫秒尺度的像素 ⇒ 整张图 ~0.25s 就有结论，
    而"逐关点进面板再退出来"要 1.8s + 0.9s / 关。
    """
    import cv2
    import numpy as np
    cfg = nav_nodes()
    hex_map = cfg.get("hex") or {}
    hexes = hex_map.get(prefix) or hex_map.get(f"{prefix}_EX") or {}   # "EP" 或 "EP_EX" 都认
    img = shot()
    if img is None or not hexes:
        return {"ok": False, "detail": f"取帧失败或没有 {prefix} 的六边形坐标表"}
    pg = page_of(img)
    if pg.get("page") != "活动地图":
        # **校准纪律**：不在活动地图上就别报状态（否则会拿别的画面读出垃圾结论）
        return {"ok": False, "detail": f"当前页面是「{pg.get('page')}」（置信 {pg.get('page_score')}），不是活动地图 ⇒ 拒绝解读", "page": pg}
    rows = {}
    import numpy as _np
    for code, (x, y) in hexes.items():
        # **在节点附近滑窗找六边形**（比死记六边形中心稳）：六边形紧贴标签左侧，
        #   所以在 (x-110..x+25, y-28..y+28) 里找蓝/白占比最高的 26x26 小块。
        x0, x1 = max(0, x - 110), min(img.shape[1], x + 26)
        y0, y1 = max(0, y - 28), min(img.shape[0], y + 28)
        reg = img[y0:y1, x0:x1]
        if reg.size == 0:
            continue
        hsv = cv2.cvtColor(reg, cv2.COLOR_BGR2HSV)
        h, s, v = hsv[:, :, 0].astype(_np.int16), hsv[:, :, 1].astype(_np.int16), hsv[:, :, 2].astype(_np.int16)
        blue_m = ((h >= 90) & (h <= 130) & (s > 110) & (v > 130)).astype(_np.float32)
        white_m = ((s < 45) & (v > 205)).astype(_np.float32)
        k = 26
        best_blue = best_white = 0.0
        for yy in range(0, max(1, reg.shape[0] - k), 6):
            for xx in range(0, max(1, reg.shape[1] - k), 6):
                best_blue = max(best_blue, float(blue_m[yy:yy + k, xx:xx + k].mean()))
                best_white = max(best_white, float(white_m[yy:yy + k, xx:xx + k].mean()))
        if best_blue >= 0.35:
            state = "蓝（普通三星）"
        elif best_white >= 0.45:
            state = "白（普通+突袭都过）"
        else:
            state = "灰/缺角（未三星）"
        rows[code] = {"state": state, "blue": round(best_blue, 2), "white": round(best_white, 2)}
    todo_normal = [k for k, v in rows.items() if v["state"].startswith("灰")]
    todo_raid = [k for k, v in rows.items() if v["state"].startswith("蓝")]
    return {"ok": True, "prefix": prefix, "nodes": rows,
            "普通待补三星": todo_normal, "突袭可打": todo_raid,
            "已全清": [k for k, v in rows.items() if v["state"].startswith("白")]}


def grid_scan(ocr: bool = False) -> dict:
    """读「完成进度 / 乐章收录」网格页 —— 用户 2026-09-24 给的规则：

        · 卡片**左上角六边形亮蓝（无勾）** ⇒ 这个活动**还有东西可领**（有源石可挖）
        · 六边形**灰色 + 白色勾、颜色变淡**   ⇒ 已经拿完了
        · 六边形右边是 `xx/yy` 星数进度：普通 3 星 + 突袭 1 星 = 单关满星 4

    网格布局固定（5 列 × 3 行可见），所以按格子采样即可，**不用 OCR 认活动名**；
    活动名由我在坐标表里按"第几格 = 哪个活动"填一次即可。
    """
    import cv2
    import numpy as np
    img = shot()
    if img is None:
        return {"ok": False, "detail": "取帧失败"}
    cols = [96, 302, 505, 702, 906]          # 六边形中心的 x（1280 宽）——只作初值
    rows = [118, 333, 548]                   # 六边形中心的 y（720 高）
    cells = []

    # **动态找标记**（2026-09-24 修正）：卡片间距不是均匀的，而且"已拿完"的 ✓ 是**亮白圆**
    # 不是灰圆 —— 固定格子会漏卡。改成在每行的高度带上扫"蓝六边形 / 亮白圆"的横向位置，
    # 自己把列位置找出来。
    for r, cy in enumerate(rows):
        band = img[max(0, cy - 16):cy + 16, :]
        if band.size == 0:
            continue
        hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
        h, s, v = hsv[:, :, 0].astype(np.int16), hsv[:, :, 1].astype(np.int16), hsv[:, :, 2].astype(np.int16)
        blue_m = ((h >= 90) & (h <= 130) & (s > 110) & (v > 130))
        white_m = ((s < 45) & (v >= 228))
        prof = blue_m.mean(axis=0) * 2.0 + white_m.mean(axis=0)   # 标记所在列会更"亮"
        # 找局部极大（间隔 ≥120px 算不同卡片）
        centers, last_x = [], -999
        for x in range(20, img.shape[1] - 20):
            if prof[x] >= 0.35 and x - last_x >= 120:
                if not centers or prof[x] > prof[centers[-1]]:
                    centers.append(x)
                last_x = x
        for c, cx in enumerate(centers):
            patch = img[max(0, cy - 16):cy + 16, max(0, cx - 16):cx + 16]
            if patch.size == 0:
                continue
            ph = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
            phh, phs, phv = ph[:, :, 0].astype(np.int16), ph[:, :, 1].astype(np.int16), ph[:, :, 2].astype(np.int16)
            blue = float(((phh >= 90) & (phh <= 130) & (phs > 110) & (phv > 130)).mean())
            white = float(((phs < 45) & (phv >= 228)).mean())
            if blue >= 0.30:
                state = "有可拿"          # 亮蓝六边形（无勾）
            elif white >= 0.30:
                state = "已拿完"          # 亮白圆 + 勾
            else:
                state = "空/无卡"
            item = {"row": r, "col": c, "x": cx, "y": cy, "state": state,
                    "blue": round(blue, 2), "white": round(white, 2)}
            if ocr and state != "空/无卡":
                crop = img[max(0, cy - 14):cy + 14, cx + 26:cx + 150]
                res, _ = engine()(crop)
                item["stars"] = "".join(t for _b, t, *_ in (res or [])).replace(" ", "")
            cells.append(item)
    blue_cells = [c for c in cells if c["state"] == "有可拿"]
    return {"ok": True, "cells": cells, "有可拿": len(blue_cells), "已拿完": len([c for c in cells if c["state"] == "已拿完"])}


def nodes_scan() -> dict:
    """**自动找地图节点**（泛化的关键一环，2026-09-24）：不依赖我手工填的坐标表。

    原理：节点六边形是画面上"颜色最纯"的东西 —— 亮蓝（=普通三星）/ 亮白（=连突袭都过）。
    于是：取帧 → 颜色掩膜 → 连通域 → 按"宽高 25~60px、近似正方"筛掉文字和装饰 → 得到节点中心。
    有了中心就能直接点，**任何新活动/新地图进来都能用**（零代码、零手工填表）。
    """
    import cv2
    import numpy as np
    img = shot()
    if img is None:
        return {"ok": False, "detail": "取帧失败"}
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0].astype(np.int16), hsv[:, :, 1].astype(np.int16), hsv[:, :, 2].astype(np.int16)
    blue = ((h >= 95) & (h <= 125) & (s >= 140) & (v >= 150)).astype(np.uint8) * 255
    white = ((s <= 40) & (v >= 235)).astype(np.uint8) * 255
    out = []
    for mask, kind in ((blue, "蓝（普通三星）"), (white, "白（含突袭已过）")):
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        n, _lab, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
        for i in range(1, n):
            x, y, w, hh, area = stats[i]
            if not (24 <= w <= 70 and 22 <= hh <= 70):
                continue
            if area < 0.55 * w * hh or area > 1.2 * w * hh:      # 六边形≈0.75 填充
                continue
            if min(x, y) < 30 or y > 700:                        # 甩掉边缘的 UI
                continue
            out.append({"x": int(cents[i][0]), "y": int(cents[i][1]), "kind": kind,
                        "w": int(w), "h": int(hh)})
    # 按"从上到下、从左到右"排序，便于和第几关对应
    out.sort(key=lambda d: (d["y"] // 40, d["x"]))
    return {"ok": True, "nodes": out, "count": len(out),
            "蓝": len([d for d in out if d["kind"].startswith("蓝")]),
            "白": len([d for d in out if d["kind"].startswith("白")])}


def scroll_state() -> dict:
    """读**滚动条**判断"滚到哪儿了"（2026-09-24，用户提醒：完成进度页竖直很长）。

    页面右侧有一条细亮滚动条 ⇒ 它的上端位置就是"当前滚动位置"，这是最便宜可靠的
    "归零"判据（比"帧差变小=没在动"稳得多 —— 那个判据被页面动画骗过好几次）。
    """
    import cv2
    import numpy as np
    img = shot()
    if img is None:
        return {"ok": False, "detail": "取帧失败"}
    strip = img[90:719, 1245:1275]                 # 右侧边栏（**跳过顶部 UI**：按钮/页签也会亮）
    g = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
    col = (g > 150).mean(axis=1)
    rows = np.where(col > 0.25)[0]
    if len(rows) < 8:
        return {"ok": True, "found": False, "detail": "没找到滚动条（这页可能不需要滚）"}
    # 取**最长连续段**当滑块（避免把零散的亮点当成滑块）
    runs, start = [], rows[0]
    for i in range(1, len(rows)):
        if rows[i] != rows[i - 1] + 1:
            runs.append((start, rows[i - 1]))
            start = rows[i]
    runs.append((start, rows[-1]))
    top, bottom = max(runs, key=lambda r: r[1] - r[0])
    top += 90
    return {"ok": True, "found": True, "thumb_top": int(top), "thumb_bottom": int(bottom + 90),
            "at_top": top <= 110, "runs": len(runs)}


def state() -> dict:
    st = {}
    try:
        st = json.load(open(ROOT / "runtime" / "_prime_state3.json", encoding="utf-8"))
    except Exception:
        pass
    todo = [k for k, v in st.items() if v in ("todo", "partial")]
    outcomes = []
    try:
        outcomes = [json.loads(l) for l in (ROOT / "runtime" / "axis_outcomes.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        pass
    return {
        "units_total": len(st),
        "todo": len(todo),
        "todo_list": todo[:40],
        "ledger": len(outcomes),
        "ledger_3star": sum(1 for o in outcomes if (o.get("stars") or 0) >= 3),
        "rank_stages": len(json.load(open(ROOT / "runtime" / "axis_rank.json", encoding="utf-8"))) if (ROOT / "runtime" / "axis_rank.json").exists() else 0,
    }


# ── 导航（搬进服务：帧/模板/adb shell 全部常驻，省掉每次新起 Python 的 0.4~0.8s）──
def nav_nodes() -> dict:
    try:
        return json.loads((ROOT / "data" / "nav_nodes.json").read_text(encoding="utf-8"))
    except Exception:
        return {"maps": {}, "zones": {}, "panel": {}}


def _shell():
    sys.path.insert(0, str(ROOT / "tools"))
    import fastnav
    return fastnav


# ── 通用恢复：被留在「不可导航页」时先退回大厅 ────────────────────────────────
# 2026-09-24 20:2x 实测事故（Lead 亲手验证的手法，这里固化成代码）：
#   单位导航失败会把游戏留在「档案 / 乐章收录 / 完成进度(曲谱)」或「干员详情」页 ——
#   这类页面上本文件的页面守卫直接收手（`不在活动地图上`），而**下一个单位再导航一次
#   又把人带回档案页** ⇒ 整轮单位全废（20:23 起 0 判决）。实测回退路径（1280x720）：
#     完成进度网格（未知 0.747）→ BACK → 曲谱（1.0）→ BACK → 大厅（0.899），共 2 次。
#   硬规则：
#     · **只在** 曲谱 / 乐章详情 / 未知（且文本含档案或干员页标记）时按 BACK；
#       大厅 / 活动地图 / 关卡详情 / 结算页 **一次都不按** —— 那会把已经在正确位置的
#       游戏退出去，而且**在大厅上按 BACK 会拉起「是否确认退出游戏」弹窗**（本机实测：
#       第 3 次 BACK ⇒ page=未知 0.652 文本「是否确认退出游戏？」）。弹窗只点「取消」，
#       绝不按 BACK。
#     · 疑似战斗态（含 倍速/暂停/下一波…）⇒ 不动手，交给上层。
#     · 最多 6 次，回到 大厅 / 活动地图 即停；
#     · 全过程写进返回 JSON（`recover_pages` / `recover`）并落 runtime/_nav_recover.log。
RECOVER_MAX_BACK = 6                  # 上限（Lead 要求：最多 6 次）
RECOVER_WAIT = 2.0                    # 按完 BACK 等 2 秒再读页面（与人工实测手法一致）
RECOVER_STOP = ("大厅", "活动地图", "关卡详情", "结算页")     # 落在这些页 ⇒ 停手、一次 BACK 都不按
RECOVER_BACK_PAGES = ("曲谱", "乐章详情")                    # 睁眼就能认出的不可导航页
# 2026-09-25 达芬奇实测：旧表覆盖不到**终端页**（MT 一族 36 单位在结构上永远触发不了恢复；
#   "完成进度"154 命中 vs "ATTENTION"/"TO-DO LIST"/"前往上一次作战" 0 命中）。
#   匹配用归一化（去空格）：blob 是 " ".join(token) 拼的，OCR 把多字标签切成两 token 时
#   "乐章收录" in "乐章 收录" 恒假 —— 与 norm_code 同一族（字面量 vs OCR 文本不先归一化）。
RECOVER_MARKERS = ("乐章收录", "Story Line", "完成进度", "默认进度", "奖励进度",
                   "信赖值", "精英化", "收起", "干员详情", "档案",
                   "终端", "ATTENTION", "TO-DO LIST", "前往上一次作战")  # 「未知」页要命中其一才敢按 BACK
RECOVER_BATTLE = ("倍速", "暂停", "下一波", "自动部署", "已部署")    # 疑似战斗态 ⇒ 永不按 BACK
RECOVER_EXIT_DIALOG = ("是否确认退出游戏", "确认退出游戏")
RECOVER_CANCEL_XY = (439, 475)        # 退出确认弹窗的「取消」（同 tools/main_nav.py 的既有口径）
RECOVER_LOG = ROOT / "runtime" / "_nav_recover.log"


def _rlog(line: str) -> None:
    """恢复过程的审计日志（失败绝不影响导航）。"""
    try:
        RECOVER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(RECOVER_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%H:%M:%S] ") + line + "\n")
    except Exception:                                     # noqa: BLE001
        pass


def recover_to_lobby(max_back: int = RECOVER_MAX_BACK, emu: str = "1") -> dict:
    """把游戏从「档案/乐章收录/完成进度(曲谱)/干员详情」这类**不可导航页**退回大厅。

    判页用**便宜的模板指纹**（`page_of`，~0.03s）；只有判成「未知」时才付一次全屏 OCR，
    用它去认档案页/干员页的标记（`RECOVER_MARKERS`）—— 认不出就**一次 BACK 都不按**。

    返回（`_img` 是最后一帧，调用方 pop 掉复用，别外泄到 JSON）::

        {"attempted": bool, "ok": bool, "back_count": int,
         "pages": ["完成进度页?未知", "曲谱", "大厅"], "actions": [...],
         "page_after": "大厅", "detail": "...", "total_ms": int, "_img": ndarray}
    """
    t0 = time.time()
    fn = _shell()
    mx = max(0, int(max_back))
    res = {"attempted": False, "ok": False, "back_count": 0, "pages": [], "actions": [],
           "page_after": None, "detail": "", "max_back": mx}
    img = None
    for _ in range(mx):
        img = shot(emu=emu)
        if img is None:
            res["detail"] = "截图失败 ⇒ 恢复放弃"
            break
        page = page_of(img).get("page")
        res["pages"].append(page)
        if page in RECOVER_STOP:
            res["ok"] = True
            res["detail"] = ((f"已回到「{page}」（按了 {res['back_count']} 次 BACK）"
                              if res["back_count"] else f"已在「{page}」") + "｜可导航页 ⇒ 恢复停手")
            break
        if page == "黑帧":                       # 取帧失败/切场中 ⇒ 重截，绝不按 BACK
            time.sleep(0.6)
            continue
        blob, why = "", ("曲谱/乐章详情（不可导航页）" if page in RECOVER_BACK_PAGES else "")
        if page == "未知":                       # 只有这时才付 OCR 的钱
            try:
                blob = " ".join(t for _, _, t in texts(img))
            except Exception:                    # noqa: BLE001
                blob = ""
            if any(m in blob for m in RECOVER_EXIT_DIALOG):
                fn.Shell.get().tap(*RECOVER_CANCEL_XY)          # 点「取消」，**不按 BACK**
                res["actions"].append({"page": page, "act": "dismiss-exit-dialog",
                                       "xy": list(RECOVER_CANCEL_XY), "text": blob[:160]})
                time.sleep(1.2)
                continue
            if any(m in blob for m in RECOVER_BATTLE):
                res["actions"].append({"page": page, "act": "noop",
                                       "why": "疑似战斗态 ⇒ 不按 BACK", "text": blob[:160]})
                res["detail"] = "疑似战斗态（屏上含 倍速/暂停/下一波…）⇒ 恢复不动手"
                break
            _blob_flat = blob.replace(" ", "")
            if not any(m.replace(" ", "") in _blob_flat for m in RECOVER_MARKERS):
                res["actions"].append({"page": page, "act": "noop",
                                       "why": "认不出页面且无档案/干员页标记 ⇒ 不按 BACK",
                                       "text": blob[:160]})
                res["detail"] = "认不出页面且没有档案/干员页标记 ⇒ 恢复不动手（不乱按 BACK）"
                break
            why = "未知 + 含档案/干员页标记"
        res["attempted"] = True
        fn.Shell.get().key(4)                                   # BACK
        res["back_count"] += 1
        act = {"page": page, "act": "back", "why": why}
        if blob:
            act["text"] = blob[:160]
        res["actions"].append(act)
        time.sleep(RECOVER_WAIT)
    else:
        # 循环用满（一次都没落在停手页）⇒ 再读一帧，如实报告最后在哪
        img2 = shot(emu=emu)
        if img2 is not None:
            img = img2
            page = page_of(img2).get("page")
            res["pages"].append(page)
            res["ok"] = page in RECOVER_STOP
        res["detail"] = (f"按了 {res['back_count']} 次 BACK 仍未回到大厅/活动地图"
                         f"（上限 {mx}）")
    res["page_after"] = res["pages"][-1] if res["pages"] else None
    res["total_ms"] = round((time.time() - t0) * 1000)
    res["_img"] = img
    if res["back_count"] or any(a.get("act") != "back" for a in res["actions"]):
        # 2026-09-25 审计要求：把 blob[:160] 落进日志，否则下次失败仍分不清
        # (a)分词致多字标记匹配失败 vs (b)屏上真没那些字（actions[].text 本来就有，这里只是让日志行自带）
        _last_txt = ""
        for _a in reversed(res["actions"]):
            if _a.get("text"):
                _last_txt = str(_a["text"])[:160]
                break
        _rlog(f"recover backs={res['back_count']} ok={res['ok']} pages={'→'.join(res['pages'])} "
              f"after={res['page_after']} detail={res['detail']} blob={_last_txt}")
    return res


def nav_to(stage: str, want: str = "普通", zone: str = "") -> dict:
    """坐标导航：判页面 →（必要时 BACK 回地图）→ 点节点 → 轮询确认面板；突袭则再点一次开关。"""
    t0 = time.time()
    fn = _shell()
    cfg = nav_nodes()
    out = {"ok": False, "stage": stage, "want": want}

    # **通用恢复（2026-09-24）**：被留在「档案/乐章收录/完成进度(曲谱)/干员详情」这类
    #   不可导航页时，先按 BACK 退回大厅（最多 6 次、回到大厅/活动地图即停），再走下面的
    #   坐标导航。少了这一步**一个单位导航失败会把整轮带崩**（20:23 起 0 判决）：
    #   失败会把人留在曲谱页，而下一个单位再导航一次又是同样的「不在活动地图上」。
    rec = recover_to_lobby()
    first_img = rec.pop("_img", None)
    out["recover"] = rec                            # 全过程可审计（含 recover_pages / back_count）
    out["recover_pages"] = list(rec.get("pages") or [])
    out["recover_backs"] = rec.get("back_count")

    if zone:
        prefix = stage.split("-")[0]
        pt = (cfg.get("zones") or {}).get(prefix, {}).get(zone)
        if pt:
            fn.Shell.get().tap(*pt)
            time.sleep(0.4)

    # 恢复阶段刚取过一帧 ⇒ 直接复用（除非上面点了区域卡，那时页面会变，必须重截）
    img = shot() if (zone or first_img is None) else first_img
    if img is None:
        out["detail"] = "截图失败"
        return out
    cur = page_of(img)
    out["page_before"] = cur.get("page")

    # 坐标表：优先 "<前缀>_EX"；带 views 的地图（一屏放不下，如 LE）先归一化位置再点
    prefix = stage.split("-")[0]
    maps = cfg.get("maps") or {}
    table = maps.get(f"{prefix}_EX", {}) if "-EX-" in stage else maps.get(prefix, {})
    view_steps = []
    if isinstance(table, dict) and "views" in table:
        chosen = None
        for v in table.get("views") or []:
            if stage in (v.get("nodes") or {}):
                chosen = v
                break
        if chosen is None:
            out["detail"] = f"{stage} 不在任何已记录视图里"
            out["total_ms"] = round((time.time() - t0) * 1000)
            return out
        # 归一化：先把地图拖到最左端（确定性），再执行该视图的 steps
        for st in table.get("normalize") or []:
            if st and st[0] == "swipe":
                _drag_step(st)                        # motionevent（swipe 实测不生效）
                time.sleep(0.5)
        view_steps = chosen.get("steps") or []
        table = chosen.get("nodes") or {}
    pt = table.get(stage) if isinstance(table, dict) else None
    # **先用"模型建库"的坐标**（2026-09-24）：tools/learn_map.py 问一次视觉模型就把
    #   整张地图的"关卡代号 → 白框中心坐标"落进 runtime/learned/nodes_<前缀>.json，
    #   这里优先查它 ⇒ 新活动**不用我手填坐标表**（泛化 + 免维护）。
    if pt is None:
        try:
            lp = LEARNED / f"nodes_{prefix}.json"
            if lp.exists():
                learned = json.loads(lp.read_text(encoding="utf-8"))
                if stage in learned and learned[stage] and learned[stage][0]:
                    pt = learned[stage]
                    out["from_learned_nodes"] = True
        except Exception:
            pass
    if pt is None:
        # **零手填坐标**（用户 2026-09-24 要求）：坐标表里没有这关时，
        # 用 `/nodes` 的六边形自动检出**逐个试**：点一个 → 看是不是关卡详情 → OCR 代号比对，
        # 命中就把这个坐标**学下来**（写进 runtime/learned/nodes_<前缀>.json），下次直接命中。
        # 这一步让"新活动/新关卡"完全不依赖我读图填表（泛化 + 免维护）。
        nodes = (nodes_scan().get("nodes") or [])[:8]
        for nd in nodes:
            fn.Shell.get().tap(nd["x"], nd["y"])
            deadline = time.time() + 2.0
            im = None
            while time.time() < deadline:
                time.sleep(0.12)
                im = shot()
                if im is not None and page_of(im).get("page") == "关卡详情":
                    break
            if im is None or page_of(im).get("page") != "关卡详情":
                continue
            code = ocr_code(im)
            if code_matches(stage, code):
                LEARNED.mkdir(parents=True, exist_ok=True)
                lp = LEARNED / f"nodes_{prefix}.json"
                try:
                    d = json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else {}
                except Exception:
                    d = {}
                d[stage] = [nd["x"], nd["y"]]
                lp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
                out.update({"ok": True, "point": [nd["x"], nd["y"]], "learned_node": True,
                            "stage_check": {"stage_ocr": code, "learned": True},
                            "page_after": "关卡详情", "total_ms": round((time.time() - t0) * 1000)})
                return out
            fn.Shell.get().key(4)          # 不是目标关 ⇒ 退回地图再试下一个节点
            time.sleep(0.6)
        out["detail"] = f"坐标表里没有 {stage}，自动试了 {len(nodes)} 个检出的节点都没命中"
        out["total_ms"] = round((time.time() - t0) * 1000)
        return out

    # 已在目标面板（且不是突袭请求）→ 直接返回
    last = None
    try:
        last = json.loads((ROOT / "runtime" / "_nav_last.json").read_text(encoding="utf-8"))
    except Exception:
        last = {}
    if cur.get("page") == "关卡详情" and last.get("stage") == stage and last.get("want") == want:
        out.update({"ok": True, "cached": True, "page_after": "关卡详情",
                    "total_ms": round((time.time() - t0) * 1000)})
        return out

    if cur.get("page") not in ("活动地图", "关卡详情"):
        # **放宽"必须在活动地图"**（2026-09-24 实测教训）：新活动的活动地图不在我那几张页面
        #   模板里 ⇒ 被判「未知」⇒ 明明在图里也直接失败（EA 就这么被卡了几小时）。
        #   够用判据：认不出页面时，只要**检出六边形节点**、或**这关已有坐标**（模型/我读图建的库），
        #   就继续往下走 —— 点完还会用关卡代号核对身份，点错会被拦，所以放宽是安全的。
        lenient = False
        # ⚠️ 别用"检出六边形"当判据：**完成进度网格页的卡片上也画着六边形**，会误判成地图
        #    （实测：人在网格页却被放行，后面导航全废）。用**关卡代号/OPERATION 标签**才准。
        try:
            import re as _re2
            ts2 = texts(im) or []
            txt2 = " ".join(t for _, _, t in ts2).upper().replace(" ", "")
            # **必须看到"本活动的代号"**（如 EA-1 / EP-EX-3）才算在图里。
            #   2026-09-24 实测教训：只判"有 OPERATION 或有任意代号"会被**完成进度网格页**骗过
            #   （网格页上也有各种标签/编号），于是在网格页上导航、全线失败。用前缀卡死最稳。
            lenient = (f"{prefix}-".upper() in txt2)
        except Exception:
            lenient = False
        if not lenient:
            try:
                lp = LEARNED / f"nodes_{prefix}.json"
                lenient = lp.exists() and stage in json.loads(lp.read_text(encoding="utf-8"))
            except Exception:
                lenient = False
        if not lenient:
            out["detail"] = f"当前在「{cur.get('page')}」屏，不在活动地图上（也没检出节点/无坐标）"
            out["total_ms"] = round((time.time() - t0) * 1000)
            return out
        out["page_before_lenient"] = True

    if cur.get("page") == "关卡详情":
        fn.Shell.get().key(4)                       # BACK 回地图（确定性重置）
        deadline = time.time() + 3.0
        while time.time() < deadline:
            time.sleep(0.1)
            im2 = shot()
            if im2 is not None and page_of(im2).get("page") == "活动地图":
                break
        fn.wait_settle(max_wait=0.25)

    after = {}
    for _ in range(3):
        for st in view_steps:                      # 视图自身的位移（如 LE 从最左端再左拖一格）
            if st and st[0] == "swipe":
                _drag_step(st)                        # motionevent（swipe 实测不生效）
                time.sleep(0.4)
        view_steps = []
        fn.Shell.get().tap(*pt)
        deadline = time.time() + 2.5
        while time.time() < deadline:
            time.sleep(0.12)
            im3 = shot()
            after = page_of(im3) if im3 is not None else {}
            if after.get("page") == "关卡详情":
                break
        if after.get("page") == "关卡详情":
            break
        fn.wait_settle(max_wait=0.6)

    if after.get("page") == "关卡详情":
        # ★ 校准（像千分尺读数）：落地后再核一次"面板上的关卡是不是我要的那一关"
        #   有模板 → 5ms 模板比对；没模板 → 只 OCR 一次确认，确认过就把模板学下来，
        #   以后同一关都是 5ms。核对不通过就报 ok=false，让调用方退回 OCR 导航。
        img_now = shot()
        chk = verify_stage(img_now, stage) if img_now is not None else {"stage_match": None}
        if chk.get("stage_match") is None and img_now is not None:
            code = ocr_code(img_now)
            ok_code = code_matches(stage, code)
            chk = {"stage_match": ok_code, "stage_ocr": code, "learned": False}
            if ok_code:
                chk["learned"] = learn_stage(img_now, stage)
        out["stage_check"] = chk
        if chk.get("stage_match") is False:
            out["ok"] = False
            out["detail"] = f"校准失败：面板上的关卡与 {stage} 不符（{chk}）"
            out["total_ms"] = round((time.time() - t0) * 1000)
            return out
        # 突袭：面板固定位置点一下「突袭模式」开关
        if want and want != "普通":
            btn = (cfg.get("panel") or {}).get("突袭模式")
            if btn:
                fn.Shell.get().tap(*btn)
                time.sleep(0.4)
        try:
            (ROOT / "runtime" / "_nav_last.json").write_text(
                json.dumps({"stage": stage, "want": want}), encoding="utf-8")
        except Exception:
            pass

    out.update({"ok": after.get("page") == "关卡详情", "page_after": after.get("page"),
                "point": pt, "total_ms": round((time.time() - t0) * 1000)})
    return out


class H(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):        # 静音
        pass

    def do_GET(self):                  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        emu = (q.get("emu") or ["1"])[0]
        t0 = time.time()
        try:
            if u.path == "/health":
                return self._send({"ok": True, "dev": dev(emu), "cost_ms": 0})
            if u.path == "/skipstory":
                # 一次调用 = 检测 + 点「跳过 ▶」+ 点确认 ⊙（状态机在服务里，调用方不用起进程、
                # 不用自己拼两次 tap）。剧情看门狗每 ~1.5s 调它一次，单次只 OCR 右上角小块。
                # `?src=adb` ⇒ 取帧走 adb screencap（MAA 正在同一台模拟器打关时用它避开私有通道）。
                return self._send(skip_story(src=(q.get("src") or ["maa"])[0]))
            if u.path == "/state":
                return self._send({"ok": True, **state(), "cost_ms": int((time.time() - t0) * 1000)})
            if u.path == "/look":
                img = shot(emu=emu)
                if img is None:
                    return self._send({"ok": False, "err": "截图失败"})
                roi = (q.get("roi") or [""])[0]
                if roi:
                    try:
                        x0, y0, x1, y1 = (int(v) for v in roi.split(","))
                        sub = img[y0:y1, x0:x1]
                        ts = texts(sub)
                        ts = [(x + x0, y + y0, t) for x, y, t in ts]
                    except Exception as e:                      # noqa: BLE001
                        return self._send({"ok": False, "err": f"roi 解析失败: {e}"}, 400)
                else:
                    ts = texts(img)
                # **把已经付过钱的 OCR 文本喂给 page_of**（task-14 真机 AD 首验的修复）：
                #   调用方既然已经 OCR 了整屏，就顺手白拿一次"结构判据" —— 这样**全新活动地图
                #   （没有整屏模板、模板闸门不触发）也能在这次调用里被认出来**，不再多花一毫秒。
                blob = " ".join(t for _, _, t in ts)
                return self._send({"ok": True, "texts": [t for _, _, t in ts],
                                   **panel(img, page=page_of(img, ocr_text=blob).get("page")),
                                   "cost_ms": int((time.time() - t0) * 1000)})
            if u.path == "/nav":
                stage = (q.get("stage") or [""])[0]
                if not stage:
                    return self._send({"ok": False, "err": "缺 stage"}, 400)
                r = nav_to(stage, (q.get("want") or ["普通"])[0], (q.get("zone") or [""])[0])
                return self._send(r)
            if u.path == "/recover":
                # 只做「退回大厅」这一步（不导航）：审计 / 人工验证用。
                #   curl -s 'http://127.0.0.1:8765/recover?max=6'
                try:
                    mx = int((q.get("max") or [RECOVER_MAX_BACK])[0])
                except Exception:                                     # noqa: BLE001
                    mx = RECOVER_MAX_BACK
                r = recover_to_lobby(mx, emu)
                r.pop("_img", None)
                r["cost_ms"] = int((time.time() - t0) * 1000)
                return self._send(r)
            if u.path == "/tap":
                # 通用点击（坐标法用）：/tap?x=756&y=660
                fn = _shell()
                fn.Shell.get().tap(int((q.get("x") or ["0"])[0]), int((q.get("y") or ["0"])[0]))
                return self._send({"ok": True})
            if u.path == "/key":
                fn = _shell()
                fn.Shell.get().key(int((q.get("code") or ["4"])[0]))
                return self._send({"ok": True})
            if u.path == "/drag":
                # **motionevent 拖拽**（`docs/INPUT_GESTURES.md`：`input swipe` 在游戏里经常不生效，
                #   拖干员"拖起来放不下"、滑列表零位移）⇒ 统一用 DOWN→MOVE…→UP。
                #   /drag?x1=1164&y1=662&x2=700&y2=480&ms=700&steps=4
                return self._send(drag(int((q.get("x1") or ["0"])[0]), int((q.get("y1") or ["0"])[0]),
                                       int((q.get("x2") or ["0"])[0]), int((q.get("y2") or ["0"])[0]),
                                       int((q.get("ms") or ["700"])[0]), int((q.get("steps") or ["4"])[0]),
                                       emu))
            if u.path == "/scroll":
                return self._send(scroll_state())
            if u.path == "/nodes":
                return self._send(nodes_scan())
            if u.path == "/grid":
                return self._send(grid_scan(ocr=bool((q.get("ocr") or [""])[0])))
            if u.path == "/map":
                pf = (q.get("prefix") or [""])[0]
                if not pf:
                    return self._send({"ok": False, "err": "缺 prefix"}, 400)
                return self._send(map_scan(pf))
            if u.path == "/all":
                # **一次取帧，把所有判据都算完**（省掉"N 个问题 = N 次截屏"）
                # `?shot=<path>` 可选：顺手把这一帧存成文件当**证据**（结算页星数读数的凭据）。
                img = shot(emu=emu)
                if img is None:
                    return self._send({"ok": False, "err": "截图失败"})
                sp = (q.get("shot") or [""])[0]
                if sp:
                    try:
                        import cv2
                        pathlib.Path(sp).parent.mkdir(parents=True, exist_ok=True)
                        cv2.imwrite(sp, img)
                    except Exception:                 # noqa: BLE001 —— 存证失败不该影响判据
                        pass
                # **`?text=1` 时把这份 OCR 白给结构判据用**（task-14 真机 AD 首验的修复）：
                #   全新活动的地图没有整屏模板 ⇒ 默认闸门不触发 ⇒ 旧写法永远「未知」。
                #   调用方既然已经付了整屏 OCR 的钱，就让 `page_of(ocr_text=…)` 用它判一次：
                #   代价 0ms，换来"新地图也能认出来"。
                want_text = bool((q.get("text") or [""])[0])
                blob = " ".join(t for _, _, t in texts(img)) if want_text else None
                pg = page_of(img, ocr_text=blob)   # 只算一次，喂给 panel()（原来算了两次）
                out = {"ok": True, **panel(img, page=pg.get("page")), **pg,
                       "cost_ms": int((time.time() - t0) * 1000)}
                if want_text:
                    out["text"] = (blob or "")[:1200]
                return self._send(out)
            if u.path == "/page":
                img = shot(emu=emu)
                if img is None:
                    return self._send({"ok": False, "err": "截图失败"})
                blob = " ".join(t for _, _, t in texts(img)) if (q.get("text") or [""])[0] else None
                return self._send({"ok": True, **page_of(img, ocr_text=blob),
                                   "cost_ms": int((time.time() - t0) * 1000)})
            if u.path == "/panel":
                # **不做 OCR**（除非 ?text=1），只算面板字段：页面 + 星级 + 徽章 + 结算页星数
                # ⇒ 一次 ~0.4s，用于高频轮询。字段语义见 tools/panel_gate.py::read_panel()
                img = shot(emu=emu)
                if img is None:
                    return self._send({"ok": False, "err": "截图失败"})
                want_text = bool((q.get("text") or [""])[0])
                blob = " ".join(t for _, _, t in texts(img)) if want_text else None
                pg = page_of(img, ocr_text=blob)
                out = {"ok": True, **panel(img, page=pg.get("page")),
                       "page_score": pg.get("page_score"), "page_margin": pg.get("page_margin"),
                       "page_src": pg.get("page_src"), "struct_codes": pg.get("struct_codes"),
                       "cost_ms": int((time.time() - t0) * 1000)}
                if want_text:
                    out["text"] = (blob or "")[:1200]
                return self._send(out)
            if u.path == "/shot":
                p = pathlib.Path((q.get("path") or [str(ROOT / "runtime" / "_svc_shot.png")])[0])
                img = shot(p, emu=emu)
                return self._send({"ok": img is not None, "path": str(p),
                                   "cost_ms": int((time.time() - t0) * 1000)})
            if u.path == "/gate":
                code = (q.get("code") or [""])[0]
                want = (q.get("want") or ["普通"])[0]
                if not code:
                    return self._send({"ok": False, "err": "缺 code"}, 400)
                prefix = code.split("-")[0]
                r = subprocess.run(["python", "tools/mt_nav.py", "--stage", code, "--want", want,
                                    "--prefix", prefix], cwd=ROOT, capture_output=True,
                                   encoding="utf-8", errors="replace", timeout=600)
                nav = None
                for line in (r.stdout or "").splitlines():
                    if line.strip().startswith("{"):
                        try:
                            nav = json.loads(line)
                        except Exception:
                            pass
                if not (nav or {}).get("ok"):
                    return self._send({"ok": False, "stage": code, "nav": nav,
                                       "cost_ms": int((time.time() - t0) * 1000)})
                img = shot(emu=emu)
                return self._send({"ok": True, "stage": code, "nav": nav,
                                   **panel(img, page=page_of(img).get("page")),
                                   "cost_ms": int((time.time() - t0) * 1000)})
            return self._send({"ok": False, "err": "未知端点", "paths": ["/health", "/look", "/state", "/gate", "/shot", "/nav", "/recover"]}, 404)
        except Exception as e:                             # noqa: BLE001
            return self._send({"ok": False, "err": f"{type(e).__name__}: {e}"}, 500)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    engine()                                            # 预热：把 4~6s 的模型加载挪到启动时
    dev("1")
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"maaorch 查询服务就绪：http://127.0.0.1:{a.port}  (dev={dev('1')})", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
