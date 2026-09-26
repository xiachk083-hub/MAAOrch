#!/usr/bin/env python3
"""实时画面状态判别 —— 给"盯屏→立刻反应"用。

为什么（2026-09-15 用户）:
    "你每次都是执行脚本…反应慢在不实时" —— 批处理 + 盲等不是实时。
    要实时，先要能**每帧立刻判定当前是什么界面**（不靠人看图、不靠 LLM），
    这样状态一变（战斗结束/结算出现/回到地图）就能在 1 秒内动作。

判别方式: 用几张从实机截图里裁出来的小模板做匹配（灰度 + 归一化相关）:
    战斗中 / 三星结算 / 失败结算 / 关卡地图 / 加载中 / 其它
模板来源: data/mastery_scan/ 下今天抓的实机帧；裁出的模板存 data/state_tpl/。

用法:
    python -c "from tools.screen_state import state_of; ..."     # 作为库
    python tools/screen_state.py --emu 2 --interval 2 --seconds 60   # 盯着看状态变化
"""
from __future__ import annotations

import argparse
import pathlib
import time

import cv2
import numpy as np

PROJ = pathlib.Path(__file__).resolve().parent.parent
TPL_DIR = PROJ / "data" / "state_tpl"
SHOTS = PROJ / "data" / "mastery_scan"

# (模板名, 来源图, 裁剪框 x1,y1,x2,y2, 阈值) —— 裁剪框按 1280x720 实机帧
SPECS = [
    ("win",   "_after_confirm2.png", (60, 250, 600, 330), 0.75),   # 「行动结束 MISSION RESULTS」+ 三个六边形
    ("fail",  "_ex1_result.png",     (100, 300, 500, 420), 0.75),  # 「任务失败 MISSION FAILED」
    ("battle", "_now_in_stage.png",  (1080, 20, 1280, 90), 0.80),  # 战斗中右上角 2X/▶
    ("exmap", "_truth.png",          (900, 540, 1230, 680), 0.75),
    ("form",  "_stuck.png",          (900, 648, 1100, 706), 0.80),   # 编队页的「确认」按钮
    ("dialog", "_skip_b.png",       (400, 420, 880, 500), 0.80),   # 通用弹窗（确认框版式）
    ("detail", "_now4.png",         (940, 175, 1250, 220), 0.80),   # 关卡详情页（推荐平均等级 文字区，避开与编队页重叠的按钮行） # 圣殿之路/万国峰会 徽记
]


def build_templates() -> dict[str, np.ndarray]:
    TPL_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, src, box, _thr in SPECS:
        p = TPL_DIR / f"{name}.png"
        if not p.exists():
            img = cv2.imread(str(SHOTS / src))
            if img is None:
                continue
            x1, y1, x2, y2 = box
            cv2.imwrite(str(p), img[y1:y2, x1:x2])
        t = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if t is not None:
            out[name] = t
    return out


_TPLS = build_templates()


def state_of(frame: np.ndarray) -> tuple[str, float]:
    """返回 (状态, 置信度)。状态 ∈ win/fail/battle/exmap/other"""
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    best, score = "other", 0.0
    for name, tpl in _TPLS.items():
        thr = next(s[3] for s in SPECS if s[0] == name)
        if tpl.shape[0] > g.shape[0] or tpl.shape[1] > g.shape[1]:
            continue
        r = cv2.matchTemplate(g, tpl, cv2.TM_CCOEFF_NORMED)
        v = float(r.max())
        if v >= thr and v > score:
            best, score = name, v
    if best == "other" and match_main(frame):
        return "main", 0.80      # 主界面（模板缺失 → 指纹兜底；实测自相似 0.84~0.93）
    return best, round(score, 3)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", default="2")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--image", type=pathlib.Path)
    args = ap.parse_args()
    if args.image:
        print(state_of(cv2.imread(str(args.image))))
        return
    import sys
    sys.path.insert(0, str(PROJ))
    from services.ai_agent.percept import AdbScreen
    sc = AdbScreen(args.emu)
    sc.ensure_connected()
    t0 = time.time()
    last = None
    while time.time() - t0 < args.seconds:
        f = sc.capture()
        if f is not None:
            st, conf = state_of(f)
            if st != last:
                print(f"[{time.strftime('%H:%M:%S')}] 状态 → {st}（{conf}）", flush=True)
                last = st
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

_MAIN_FP = None


def match_main(frame, thr: float = 0.80) -> bool:
    """主界面指纹判定（零模型延迟）。

    2026-09-16：判别器一直没有「主界面」模板，导致大厅被读成 other，
    导航链因此在真正的大厅上判不出状态、误入读图分支（模型 5~15s/次 × 2）而超时。
    """
    global _MAIN_FP
    try:
        import numpy as _np
        if _MAIN_FP is None:
            fp_path = pathlib.Path(__file__).resolve().parent.parent / "data" / "refs" / "main_fp.npy"
            if not fp_path.exists():
                return False
            _MAIN_FP = _np.load(fp_path)
        v = cv2.resize(frame, (64, 64)).flatten().astype(float)
        v = v / (_np.linalg.norm(v) + 1e-6)
        return float(_np.dot(v, _MAIN_FP)) > thr
    except Exception:
        return False

def state_of_stable(frames: int = 2, gap: float = 0.6):
    """跨帧一致性：连续 frames 帧同状态才算数，否则返回 ("changing", 0)。

    为什么必须在调用层（2026-09-16 实测）：state_of 是单帧判别且各页置信度都很高
    （main 0.80 / exmap 1.00 / detail 1.00 / form 1.00）——当游戏**真的在动**（MAA 在跑）
    时，逐帧会给出轮流不同的高置信结果，单帧无法区分「画面在变」与「判别不稳」。
    导航/依赖当前状态的地方一律用它；画面在变就等。
    """
    import time as _t
    prev = None
    for _ in range(max(2, frames)):
        f = _capture_for_stable()
        st, conf = state_of(f) if f is not None else ("other", 0.0)
        if prev is not None and st != prev:
            return "changing", 0.0
        prev = st
        _t.sleep(gap)
    return prev or "other", conf if prev else 0.0


def _capture_for_stable():
    """借用感知层的 AdbScreen 抓帧（惰性导入，避免模块级依赖）。"""
    try:
        from services.ai_agent.percept import AdbScreen
        sc = globals().setdefault("_STABLE_SC", AdbScreen("2"))
        return sc.capture()
    except Exception:
        return None
