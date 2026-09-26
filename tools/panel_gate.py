#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""panel_gate.py —— **面板判据的唯一实现**（2026-09-24 审计后重构）

用户口径（原话）:
  ① 「**报酬里面没有源石就是没有就不用打了**」 ⇒ 判据 A：报酬区有没有**未领的至纯源石徽章**
  ② 「判据是**是否已通关**，不是是否三星」        ⇒ 判据 B：面板标题栏三个六边形亮了几颗（0=未通关）

⚠️ 教训（2026-09-23 实测）：拿"未三星"当闸会**打已经通关的关**（代理跑一局、拿不到源石、白花理智）。
   MT-5/MT-6/MT-7 都早就通关了，但"未三星"探针说它们"未三星" ⇒ 我白跑了 MT-6/MT-7 两局（源石仍是 7）。

⚠️ 2026-09-24 独立审计又抓到**两套判据并存导致的读数分裂**（本项目反复踩的坑，别再分裂）：
   · `tools/maaorch_service.py::panel()` 自己抄了一份，且徽章 ROI **x/y 写反**
     （写成 `img[620:1280, 430:860]`，正确是 y430:620 / x860:1280）⇒ 站在**有徽章**的 EA-1 面板上
     只报 0.644 / `reward_primes=false`（真值 0.999 / true）⇒ 全是假 ⊘，白白漏矿；
   · 星星除数写成 490（应为 1470/颗）且不封顶 ⇒ 3 星面板报 **9 星**、地图页报 3 星。
   ⇒ 现在**只有这一个文件实现判据**：`read_panel()` / `stars_from_result_page()`，
     服务端 `/panel`、`/all` 与 CLI 全部调它（`maaorch_service.panel()` 已改成薄壳）。

字段语义（调用方按这个写，别自己再猜）:
    stars            0..3；**页面不是「关卡详情」时一律 null**（不再拿默认 0 冒充读数）
    stars_conf       'high'（页面=关卡详情，读数可信）/ 'low'（不可信 ⇒ stars/cleared = null）
    stars_raw        不论置信度都给出原始档位（只用于排查，**不许用来判 done**）
    stars_blue_px    星区亮蓝像素（0=三颗全灰；≈1470/颗，实测 3 星 ≈4400）
    cleared          = stars>0，低置信时 null
    reward_primes    徽章判据；**页面不是「关卡详情」时 null**（报酬区只在面板上存在）
    reward_primes_raw 不论页面都给原始布尔（排查用）
    reward_score     模板最高分（0.999=徽章在；≤0.72=不在。阈值 0.75）
                     ⚠️ 阈值**单一来源** = `runtime/config/reward_gate.json`（present 0.75 / absent 0.72）；
                     本文件与 `tools/event_sweep.mjs` 都读它，**别在两处各写一份常量**。
    reward_verdict   'present'(有矿)｜'absent'(无矿)｜'ambiguous'（缺读数或落 (absent, present)）
                     —— 上位读数的**唯一判决函数**（`reward_verdict()`）
    page             页面指纹名（'关卡详情' / '结算页' / '未知' …）
    result_stars     结算页（行动结束）星数 0..3；非结算页 null
    result_lit       结算页三个槽位是否亮 [b,b,b]；非结算页 null
    result_conf      'high' / 'low'

用法（配合任意导航工具；它只截图+判读，**不点任何东西**）:
    python tools/mt_nav.py --stage MT-6 --want 普通 && python tools/panel_gate.py
输出一行 JSON: {"ok":..,"stars":..,"cleared":..,"reward_primes":..,"reward_score":..,"page":..}
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

import cv2

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "tools"))

from main_nav import reward_has_prime, sanity  # noqa: E402
from rapidocr_onnxruntime import RapidOCR      # noqa: E402

ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"   # 实测路径；文件不在时退回 PATH 里的 adb

# ── 判据常量（**实测标定，改动前先看注释**）───────────────────────────────
# 面板星区：标题栏右侧三个六边形。实测（15-3 三星面板 = 蓝像素 4400，15-20 未通关 = 0），
# 单个亮六边形 ≈1470 px ⇒ 除数 1470、封顶 3；<300 记 0（噪声底）。
STAR_ROI = (60, 150, 980, 1260)        # y0, y1, x0, x1
STAR_BLUE_MIN = 300
STAR_BLUE_PER = 1470
STAR_CAP = 3

# 结算页（「行动结束」）星区：位于左中，**与面板星区不是同一块**（面板在右上角）。
# 几何来自 fixer-1 实测 + auditor-1 独立复核（真值集 7/7 张一致，±16px 外扩不变）：
#   3★ _le6_res/_le5_res/_le5_res2；2★ _after_skip/_pvr2；0★ _c3/_term2
#   亮六边形单槽蓝像素 ≈2117~2343 ⇒ 阈值 600 有 3 倍余量；灰六边形 = 0。
RESULT_STAR_SLOTS = ((74, 126), (147, 199), (220, 272))
RESULT_STAR_Y = (292, 352)
RESULT_HSV_LO = (85, 90, 130)
RESULT_HSV_HI = (135, 255, 255)
RESULT_LIT_MIN = 600

# ── 报酬栏阈值：**单一来源** `runtime/config/reward_gate.json` ─────────────────
#   为什么（2026-09-24 Lead 定案）：本文件上面 28 行的原文就是语义（0.999=徽章在；≤0.72=不在；
#   阈值 0.75）。而横扫器曾把「从 ⊘ 观测簇反推出来的 0.46–0.64 带」当成门槛 ⇒ 把 0.46~0.70
#   这批**本来就很确信的「不在」**降级成 unreliable ⇒ ⊘ 永远无法终结。阈值只许从这里读；
#   读不到才回退内置值，并**在 stderr 警告**（不许静默用假值）。
GATE_CFG = PROJ / "runtime" / "config" / "reward_gate.json"
_FALLBACK_PRESENT, _FALLBACK_ABSENT = 0.75, 0.72


def _load_reward_gate() -> tuple[float, float]:
    """(present, absent)：present = 判「徽章在」的分数下限；absent = 判「徽章不在」的分数上限。"""
    try:
        j = json.loads(GATE_CFG.read_text(encoding="utf-8"))
        p, a = float(j["present"]), float(j["absent"])
        if not p > a:
            raise ValueError(f"present({p}) 必须 > absent({a})")
        return p, a
    except Exception as e:                       # noqa: BLE001
        print(f"[panel_gate] ⚠️ 读不到 {GATE_CFG}（{e}）⇒ 回退内置 present={_FALLBACK_PRESENT}"
              f"/absent={_FALLBACK_ABSENT}（本文件 28 行原文）", file=sys.stderr)
        return _FALLBACK_PRESENT, _FALLBACK_ABSENT


REWARD_PRESENT, REWARD_ABSENT = _load_reward_gate()


def _check_algo_threshold() -> None:
    """配置里的 present 必须就是**模板匹配真正用的**那个常量（`main_nav.BADGE_THRESHOLD`）。

    单一来源的另一种漂移：配置写成 0.8、算法里还是 0.75 ⇒ 判决与读数自相矛盾。不一致就警告。
    """
    try:
        import main_nav as _mn
        t = float(getattr(_mn, "BADGE_THRESHOLD"))
        if abs(t - REWARD_PRESENT) > 1e-9:
            print(f"[panel_gate] ⚠️ 阈值不一致：config present={REWARD_PRESENT} ≠ "
                  f"main_nav.BADGE_THRESHOLD={t}（reward_has_prime 用的是后者）", file=sys.stderr)
    except Exception:                            # noqa: BLE001
        pass


_check_algo_threshold()


def reward_verdict(score) -> str:
    """**报酬栏判决——唯一实现**（横扫器的 ⊘ 分支与结算复核都按它；阈值只从配置文件来）。

    score >= present            ⇒ 'present'（有矿：徽章还在）
    score <= absent             ⇒ 'absent' （无矿：徽章不在了 = 该关首通源石已领）
    absent < score < present    ⇒ 'ambiguous'（窄带，说不准）
    分数缺失/非数（None/''/NaN）⇒ 'ambiguous'（**读不到 ≠ 没有**）
    """
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "ambiguous"
    if s != s:                                   # NaN
        return "ambiguous"
    if s < 0:                                    # -1.0 = 模板/ROI 根本读不到（reward_has_prime 的哨兵）
        return "ambiguous"
    if s >= REWARD_PRESENT:
        return "present"
    if s <= REWARD_ABSENT:
        return "absent"
    return "ambiguous"


def page_name(img) -> str | None:
    """页面指纹名（**复用服务里唯一那份 page_of**，不另写一套）。

    服务没起/导入失败 ⇒ 返回 None ⇒ stars_conf 自动降为 'low'（宁可不判，也不误判）。
    """
    try:
        import maaorch_service as _svc
        return _svc.page_of(img).get("page")
    except Exception:                       # noqa: BLE001
        return None


def star_blue_px(img) -> int:
    """面板星区的"亮蓝"像素数（与 main_nav.stars_from_panel 同口径：B 通道高且明显大于 R）。"""
    sub = img[STAR_ROI[0]:STAR_ROI[1], STAR_ROI[2]:STAR_ROI[3]]
    if sub.size == 0:
        return -1
    b = sub[:, :, 0].astype(int)
    r = sub[:, :, 2].astype(int)
    return int(((b > 150) & (b > r + 40)).sum())


def stars_calibrated(img) -> tuple[int, int]:
    """(星数 0..3, 蓝像素)。**封顶 3、噪声底归 0** —— 旧服务版 `round(n/490)` 是错的。"""
    n = star_blue_px(img)
    if n < STAR_BLUE_MIN:
        return 0, max(n, 0)
    return max(1, min(STAR_CAP, round(n / STAR_BLUE_PER))), n


def stars_from_result_page(img) -> dict:
    """结算页（「行动结束」）三个六边形的得星数 —— 唯一实现。

    ⚠️ **0★ 也是有效读数**（实测 `_c3.png` MT-EX-8、`_term2.png` 15-20 都是
    "行动结束 + 三灰六边形"）⇒ 调用方别把 result_stars==0 当成"不是结算页"。
    """
    null = {"result_stars": None, "result_lit": None, "result_blue": None, "result_conf": "low"}
    if img is None:
        return null
    y0, y1 = RESULT_STAR_Y
    import numpy as np
    lo, hi = np.array(RESULT_HSV_LO), np.array(RESULT_HSV_HI)
    blues: list[int] = []
    for x0, x1 in RESULT_STAR_SLOTS:
        sub = img[y0:y1, x0:x1]
        if sub.size == 0:
            return null
        m = cv2.inRange(cv2.cvtColor(sub, cv2.COLOR_BGR2HSV), lo, hi)
        blues.append(int(m.sum() // 255))
    lit = [bool(b >= RESULT_LIT_MIN) for b in blues]
    return {"result_stars": int(sum(lit)), "result_lit": lit, "result_blue": blues,
            "result_conf": "high"}


def read_panel(img, page: str | None = None, want_result: bool | None = None) -> dict:
    """**唯一判据入口**：一次取帧 → 星级 + 徽章 + 页面（+ 结算页星数）。

    page 由调用方传入（服务里有现成的 page_of，避免重复算）；不传就自己查。
    want_result: 是否顺便算结算页星数；默认"页面是结算页才算"。
    """
    if img is None:
        return {"ok": False, "err": "无画面"}
    if page is None:
        page = page_name(img)
    stars, n = stars_calibrated(img)
    conf = "high" if page == "关卡详情" else "low"
    has_p, score = reward_has_prime(img)
    out = {
        "ok": True,
        "stars": stars if conf == "high" else None,
        "stars_raw": stars,
        "stars_blue_px": n,
        "stars_conf": conf,
        "cleared": (stars > 0) if conf == "high" else None,
        "reward_primes": bool(has_p) if conf == "high" else None,
        "reward_primes_raw": bool(has_p),
        "reward_score": round(float(score), 3),
        # 判决（单一来源阈值）：'present' 有矿 / 'absent' 无矿 / 'ambiguous' 说不准。
        # ⚠️ **低分不是"读数不可信"**：0.46~0.70 是"确信不在"（Lead 实测 no_prime max=0.702 与
        #    done min=0.950 在 (0.702,0.950] 完全无重叠）。
        "reward_verdict": reward_verdict(score),
        "reward_thresholds": {"present": REWARD_PRESENT, "absent": REWARD_ABSENT},
        "page": page,
    }
    if want_result is None:
        want_result = page == "结算页"
    out.update(stars_from_result_page(img) if want_result
               else {"result_stars": None, "result_lit": None, "result_blue": None,
                     "result_conf": "low" if page != "结算页" else "high"})
    return out


def _adb_bin() -> str:
    """adb 可执行文件：MuMu 自带路径优先（实测最快），不在就退回 PATH 里的 adb。"""
    return ADB if pathlib.Path(ADB).exists() else "adb"


def grab(dev: str, path=None):
    """**raw RGBA 截屏** —— 2026-09-24 实测：`screencap -p`（PNG）810~950ms，
    `screencap`（raw）180~220ms。设备端 PNG 编码是整条链路的大头，raw 只搬 3.5MB 像素。"""
    import numpy as np
    adb = _adb_bin()
    r = subprocess.run([adb, "-s", dev, "exec-out", "screencap"], stdout=subprocess.PIPE, timeout=60)
    b = r.stdout
    if len(b) >= 16 + 720 * 1280 * 4:
        arr = np.frombuffer(b, dtype=np.uint8)[16:16 + 720 * 1280 * 4].reshape((720, 1280, 4))
        img = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    else:                                   # 兜底：老路径（PNG）
        with open(path or (PROJ / "runtime" / "_gate.png"), "wb") as f:
            subprocess.run([adb, "-s", dev, "exec-out", "screencap", "-p"], stdout=f, timeout=60)
        img = cv2.imread(str(path or (PROJ / "runtime" / "_gate.png")))
    if path is not None and img is not None:
        cv2.imwrite(str(path), img)
    return img


def _resolve_dev(emu: str) -> str:
    """按实例号解析 adb 地址（端口会漂 16416→16417，别写死）。"""
    from _dev import resolve_dev
    return resolve_dev(emu)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", default="1")
    ap.add_argument("--shot", default="runtime/_gate.png")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()

    dev = _resolve_dev(a.emu)
    p = PROJ / a.shot
    p.parent.mkdir(parents=True, exist_ok=True)
    img = grab(dev, p)          # raw RGBA：实测 180~220ms（PNG 那条要 810~950ms）
    if img is None:
        print(json.dumps({"ok": False, "err": "截图失败"}, ensure_ascii=False))
        return 1

    res, _ = RapidOCR()(img)
    blob = " ".join(t for _b, t, *_ in (res or []))
    out = {"tag": a.tag, **read_panel(img), "sanity": sanity(blob), "text": blob[:120]}
    print(json.dumps(out, ensure_ascii=False))
    return 0


def main_fast(a) -> int:
    """**快路径**：不做全屏 OCR（实测全屏 5960ms、局部 684ms、像素判据 0.2ms）。

    队列每关都要"看一眼面板"，星星/徽章/页面全是模板+像素（合计 <100ms），
    理智那串数字只 OCR 一小块 ROI（约 0.3~0.6s）—— 整轮从 5s 降到 <1s。
    """
    dev = _resolve_dev(a.emu)
    p = PROJ / a.shot
    p.parent.mkdir(parents=True, exist_ok=True)
    img = grab(dev, p)
    if img is None:
        print(json.dumps({"ok": False, "err": "截图失败"}, ensure_ascii=False))
        return 1
    out = {"tag": a.tag, **read_panel(img)}
    san = None
    try:                                    # 理智数字在左上角一小块里（1280x720: x 90..270, y 320..390）
        roi = img[320:390, 90:270]
        res, _ = RapidOCR()(roi)
        san = sanity("".join(t for _b, t, *_ in (res or [])))
        if san is None:
            res, _ = RapidOCR()(img[0:60, 60:300])
            san = sanity("".join(t for _b, t, *_ in (res or [])))
    except Exception:
        san = None
    out.update({"sanity": san, "fast": True, "text": ""})
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import argparse as _ap
    _p = _ap.ArgumentParser()
    _p.add_argument("--emu", default="1")
    _p.add_argument("--tag", default="")
    _p.add_argument("--shot", default="runtime/_gate.png")
    _p.add_argument("--fast", action="store_true", help="跳过全屏 OCR（只算星星/徽章+理智小块）")
    _a = _p.parse_args()
    raise SystemExit(main_fast(_a) if _a.fast else main())
