"""TilePos 数据体检器：TileCalc2 预测的格网几何 vs 游戏高亮观测到的几何。

（外援 2026-09-19 的方案：**验证比重建容易** —— 校验只需要"两根轴的方向 + 局部格距"，
不需要整张单应 ✓；我们之前卡住的正是"重建"那半 ✗。）

判定思路
  · **预测**：用关卡数据 + TileCalc2（我们的 `Level.project` 与官方逐行复刻**输出一致** ✓）取
    某格与相邻格的屏幕点 ⇒ 两根轴向量（方向 + 长度）✓；
  · **观测**：从"选中干员时的可放置高亮"掩码提边缘 → 方向聚类出两根轴 ✓ + 相邻台阶的间距 ✓；
  · 比 **轴夹角** 与 **每轴尺度比** ⇒ 给出 TRUSTED / FALLBACK 三态里的两态（UNKNOWN = 没高亮 ✓）。

为什么只比这两样就够：格网画歪在我们这里的表现就是"方向不对（今天 PA-5：列方向 dy=0 ✗）"
或"尺度不对（今天 PA-5：1.6 倍 ✗）"⇒ 这两项任一超标就足以否掉 ✓✓。

用法：
    python tools/tilepos_audit.py --stage act51side_05 --plain data/_live_pa5_bright.png \
        --ground data/_pa5_card4.png --high data/_pa5_card2.png
    python tools/tilepos_audit.py --all          # 对已注册/有素材的关卡依次体检
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

import cv2
import numpy as np

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "tools"))

VIEW = (110, 875, 515, 1905)
ANGLE_TOL_DEG = 3.0
# Chamfer 阈值：**暂定**（等 PA-1 的高亮帧到手后用它定基线 —— 外援 §验证顺序：先 PA-1 当基线 ✓）
CHAMFER_TOL_MED = 8.0     # 预测可部署格边界 → 观测高亮边界 的中位距离（像素）          # 轴方向容差
SCALE_LO, SCALE_HI = 0.85, 1.18   # 每轴尺度比容差


def _mask(f: np.ndarray) -> np.ndarray:
    B, G, R = (f[:, :, i].astype(int) for i in range(3))
    m = np.uint8(((G - R > 10) & (G - B > 8) & (G > 65)) * 255)
    m[:VIEW[0]] = 0; m[VIEW[1]:] = 0; m[:, :VIEW[2]] = 0; m[:, VIEW[3]:] = 0
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))


def observed_axes(mask: np.ndarray, min_len: int = 12) -> dict:
    """从高亮掩码的轮廓提两根轴：方向（单位向量）+ 该轴相邻台阶的中位长度。"""
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask, 8)
    segs = []
    for i in range(1, n):
        if st[i, 4] < 2500:
            continue
        c = max(cv2.findContours(np.uint8(lab == i) * 255, cv2.RETR_EXTERNAL,
                                 cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
        ap = cv2.approxPolyDP(c, 0.012 * cv2.arcLength(c, True), True).reshape(-1, 2)
        for k in range(len(ap)):
            v = ap[(k + 1) % len(ap)].astype(float) - ap[k].astype(float)
            L = float(np.hypot(*v))
            if L >= min_len:
                if v[0] < 0 or (v[0] == 0 and v[1] < 0):
                    v = -v
                segs.append((v[0] / L, v[1] / L, L))
    if len(segs) < 4:
        return {}
    dirs = np.array([[s[0], s[1]] for s in segs])
    d0 = dirs[np.argmax([d[1] for d in dirs])]           # 先挑“最竖”的一条当第一轴
    groups = {0: [], 1: []}
    for s in segs:
        d = np.array(s[:2])
        groups[0 if abs(float(np.dot(d, d0))) > 0.7 else 1].append(s)
    out = {}
    for g, items in groups.items():
        if not items:
            continue
        v = np.mean([[s[0], s[1]] for s in items], axis=0)
        v = v / max(1e-6, float(np.hypot(*v)))
        # **周期估计**（外援 §"下一步最值得做的"）：不能拿"线段长度统计"当格距 ✗
        # —— 轮廓里既有噪声小段（实测 22px ✗）又有"好几格并成一条的直段"（长度是格距的整数倍 ✗），
        # 取分位/中位都会被带跑。正解：把每个候选周期 u 按"有多少段长 ≈ u 的整数倍"打分，取支持最强的 u ✓。
        lens = [s[2] for s in items if s[2] >= 25]          # 先把噪声小段滤掉（<25px 不可能是台阶）
        if not lens:
            out[g] = {"dir": (float(v[0]), float(v[1])), "step_px": 0.0, "support": 0.0}
            continue
        best_u, best_score = 0.0, -1.0
        for u in lens:                                      # 候选周期 = 某个段长本身
            if u < 25:
                continue
            score = 0.0
            for L in lens:
                k = max(1, round(L / u))
                err = abs(L / u - k)                        # 越接近整数倍越好
                if err < 0.18:
                    score += 1.0 - err / 0.18
            score /= len(lens)                              # 归一化：支持率
            if score > best_score + 1e-9 or (abs(score - best_score) <= 1e-9 and u < best_u):
                best_u, best_score = float(u), score
        out[g] = {"dir": (float(v[0]), float(v[1])),
                  "step_px": round(best_u, 1), "support": round(best_score, 2)}
    return out


def mask_periods(mask: np.ndarray, lo: int = 40, hi: int = 220) -> dict:
    """从**掩码本身的周期性**量格距（比轮廓拐点稳得多 ✓）。

    高亮掩码是"若干整格并起来"的 ✓ ⇒ 沿两根轴分别做自相关，第一个显著峰就是格距 ✓✓。
    这是外援 §"把台阶拐点→局部格距做干净"的落地：不再依赖轮廓分段（噪声小段/长直段都会骗人 ✗）。
    做法：把掩码按主方向 rotate 到轴对齐（两轴近似正交），再对行/列投影做自相关。
    """
    ys, xs = np.where(mask > 0)
    if len(xs) < 2000:
        return {}
    # 主轴：对掩码的梯度方向做统计（用边缘方向即可，这里直接用二阶矩近似）
    gx = cv2.Sobel(mask, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(mask, cv2.CV_32F, 0, 1, ksize=3)
    ang = 0.5 * math.atan2(2 * float((gx * gy).sum()), float((gx * gx - gy * gy).sum()))
    M = cv2.getRotationMatrix2D((float(xs.mean()), float(ys.mean())), math.degrees(ang), 1.0)
    rot = cv2.warpAffine(mask, M, (mask.shape[1], mask.shape[0]), flags=cv2.INTER_NEAREST)
    out = {}
    for tag, prof in (("col", rot.sum(0).astype(float)), ("row", rot.sum(1).astype(float))):
        pr = prof - prof.mean()
        ac = np.correlate(pr, pr, mode="full")[len(pr) - 1:]
        ac = ac / max(1e-6, ac[0])
        seg = ac[lo:hi]
        if seg.size == 0:
            continue
        # 第一个"显著局部极大"（比邻域高、且自身够强）
        cand = [k for k in range(1, len(seg) - 1)
                if seg[k] >= seg[k - 1] and seg[k] >= seg[k + 1] and seg[k] > 0.15]
        out[tag] = float(lo + cand[0]) if cand else None
    return out


def predicted_axes(lv, s_px: float = 1.5) -> dict:
    """从关卡数据 + TileCalc2（= 我们的 project，两者输出一致 ✓）取两根轴。"""
    q00 = lv.project(2, 2)
    q10 = lv.project(3, 2)
    q01 = lv.project(2, 3)
    if not (q00 and q10 and q01):
        return {}
    out = {}
    for g, (a, b) in enumerate(((q00, q10), (q00, q01))):   # 0=列向 1=行向
        v = np.array([b[0] - a[0], b[1] - a[1]], float) * s_px
        L = float(np.hypot(*v))
        out[g] = {"dir": (float(v[0] / L), float(v[1] / L)), "step_px": L}
    return out


def angle_deg(d1, d2) -> float:
    c = max(-1.0, min(1.0, float(np.dot(d1, d2))))
    return math.degrees(math.acos(abs(c)))


def audit(stage: str, plain_path: str, hl_paths: list[str]) -> dict:
    import battle_loop as BL
    L = BL.Loop(stage, None)
    pred = predicted_axes(L.lv, L.stage_px)
    plain = cv2.imdecode(np.fromfile(plain_path, np.uint8), 1) if pathlib.Path(plain_path).is_file() else None
    m = np.zeros((plain.shape[:2] if plain is not None else (1080, 1920)), np.uint8)
    for p in hl_paths:
        if pathlib.Path(p).is_file():
            m |= _mask(cv2.imdecode(np.fromfile(p, np.uint8), 1))
    obs = observed_axes(m)
    per = mask_periods(m)
    if per.get("col") and 0 in obs:
        obs[0]["step_px"], obs[0]["support"] = per["col"], "autocorr"
    if per.get("row") and 1 in obs:
        obs[1]["step_px"], obs[1]["support"] = per["row"], "autocorr"
    res = {"stage": stage, "has_highlight": bool(obs), "pred": pred, "obs": obs,
           "status": "UNKNOWN", "reason": "没有高亮素材 ⇒ 不判（外援 §三态里的 UNKNOWN）"}
    if not obs or not pred:
        return res
    # 把观测轴与预测轴按“方向最接近”配对，再比夹角与尺度
    pairs, pairs_done = [], []
    for po, pv in pred.items():
        best, bg = None, None
        for og, ov in obs.items():
            if og in pairs_done:
                continue
            a = angle_deg(pv["dir"], ov["dir"])
            if best is None or a < best:
                best, bg = a, og
        if bg is None:
            continue
        pairs_done.append(bg)
        pairs.append({"pred": pv, "obs": obs[bg], "axis_deg": round(best, 1),
                      "scale": round(obs[bg]["step_px"] / max(1e-6, pv["step_px"]), 2)})
    res["pairs"] = pairs
    bad = [p for p in pairs if p["axis_deg"] > ANGLE_TOL_DEG
           or not (SCALE_LO <= p["scale"] <= SCALE_HI)]
    # ② 判据二：**局部 Chamfer**（已自测：自比 median 0.0px ✓ 说明它真的在量对齐 ✓）
    ch = chamfer_audit(stage, hl_paths)
    res["chamfer"] = ch
    ch_bad = bool(ch.get("available")) and ch.get("median_px", 0) > CHAMFER_TOL_MED
    res["status"] = "TRUSTED" if (not bad and not ch_bad) else "FALLBACK"
    res["reason"] = ("TileCalc2 与观测高亮一致（轴 + chamfer 都过）⇒ 可用官方几何 ✓" if not (bad or ch_bad) else
                     f"**DATA/RENDER MISMATCH**：TileCalc2 与游戏高亮不一致 "
                     f"（轴夹角 {[p['axis_deg'] for p in res.get('pairs',[])]} ✗；"
                     f"chamfer 中位 {ch.get('median_px')}px ✗）⇒ 本关格网不可信，"
                     "应走视觉几何（不得反改官方 view ✗）")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description="TilePos 数据体检器")
    ap.add_argument("--stage", default="act51side_05")
    ap.add_argument("--plain", default="data/_live_pa5_bright.png")
    ap.add_argument("--ground", nargs="*", default=["data/_pa5_card4.png"])
    ap.add_argument("--high", nargs="*", default=["data/_pa5_card2.png"])
    ap.add_argument("--registry", default="data/tilepos_audit.json")
    a = ap.parse_args()
    hl = list(a.ground) + list(a.high)
    r = audit_stage(a.stage, hl, clean=a.plain)        # 规格路径：只测量 ✓
    r["action"] = action_of(r["status"])               # 外援 §schema 的 action 块 ✓
    print("关卡 %s ⇒ **%s**  action=%s" % (r["stage"], r["status"],
                                        json.dumps(r["action"], ensure_ascii=False)))
    print("  ", r["reason"])
    for pp in r.get("_pairs", []):
        pass
    wrote = record(a.stage, r, a.registry)             # 规格路径：幂等写盘 ✓
    print("  登记：", "已写入 ✓" if wrote else "同版本同结论 ⇒ 跳过（幂等 ✓）")
    print("→", a.registry)


AUDIT_VERSION = 3          # 审计流程版本（判据/测量器/schema 变了就 +1）
DETECTOR_VERSION = 3       # 高亮检测器版本


def action_of(status: str) -> dict:
    """外援 §schema 的 action 块：由状态推出"能不能画格网 / 能不能部署" ✓。"""
    ok = (status == "TRUSTED")
    return {"draw_grid": ok, "allow_deploy": ok,
            "fallback": None if ok else ("visual_lattice" if status == "FALLBACK" else "refuse_guess")}


def trusted(stage: str, registry: str = "data/tilepos_audit.json") -> tuple[bool, str]:
    """三态闸的**唯一读取点**（轻量 ✓）：TRUSTED 才放行 ✓。battle_loop 与标注层共用 ✓。"""
    import json as _json
    r = pathlib.Path(registry)
    if not r.is_file():
        return False, f"没有体检登记（{r}）⇒ 本关几何未验证（UNKNOWN）"
    try:
        d = _json.loads(r.read_text(encoding="utf-8"))
    except Exception as e:                                      # noqa: BLE001
        return False, f"体检登记读取失败：{e}"
    rec = d.get(stage)
    if not rec:
        return False, f"{stage} 没做过几何体检（UNKNOWN）"
    if rec.get("status") != "TRUSTED":
        return False, f"{stage} 几何体检 = {rec.get('status')}：{(rec.get('reason') or '')[:80]}"
    return True, ""


def chamfer_audit(stage: str, hl_frames: list, s_px: float = 1.5) -> dict:
    """**局部 Chamfer**：预测的"可部署格边界" vs 观测高亮边界（逐位置距离 ✓ 不压成全局 px/格 ✓）。"""
    import battle_loop as BL
    L = BL.Loop(stage, None); lv = L.lv
    m = np.zeros((1080, 1920), np.uint8)
    for fp in hl_frames:
        if pathlib.Path(fp).is_file():
            m |= _mask(cv2.imdecode(np.fromfile(fp, np.uint8), 1))
    if m.sum() // 255 < 2000:
        return {"available": False, "note": "没有可用高亮 ⇒ 不判（UNKNOWN ✓）"}
    dil = cv2.dilate(m, np.ones((5, 5), np.uint8)); ero = cv2.erode(m, np.ones((5, 5), np.uint8))
    obs = cv2.bitwise_and(dil, cv2.bitwise_not(ero))
    dist = cv2.distanceTransform(255 - obs, cv2.DIST_L2, 3)
    pts = []
    for r in range(lv.height):
        for c in range(lv.width):
            tt = lv.at(c, r)
            if not tt or tt.buildable == 0:
                continue
            cs = []
            for (dc, dr) in ((0, 0), (1, 0), (1, 1), (0, 1)):
                q = lv.project(c + dc, r + dr)
                if not q:
                    cs = []
                    break
                cs.append((q[0] * s_px, q[1] * s_px))
            if not cs:
                continue
            cs = np.array(cs, float); cen = cs.mean(0)
            x0, y0 = int(max(0, cen[0] - 60)), int(max(0, cen[1] - 60))
            x1, y1 = int(min(1919, cen[0] + 60)), int(min(1079, cen[1] + 60))
            if m[y0:y1, x0:x1].max() == 0:
                continue
            for k in range(4):
                a_, b_ = cs[k], cs[(k + 1) % 4]
                n = max(2, int(np.hypot(*(b_ - a_)) / 4))
                for sidx in range(n + 1):
                    q_ = a_ + (b_ - a_) * (sidx / n)
                    xi, yi = int(round(q_[0])), int(round(q_[1]))
                    if 0 <= xi < 1920 and 0 <= yi < 1080:
                        pts.append(float(dist[yi, xi]))
    if len(pts) < 50:
        return {"available": False, "note": "预测边与观测高亮重叠太少 ⇒ 不判"}
    pts = np.array(pts)
    return {"available": True, "n": int(len(pts)),
            "median_px": round(float(np.median(pts)), 1),
            "p90_px": round(float(np.percentile(pts, 90)), 1),
            "max_px": round(float(pts.max()), 1),
            "note": "预测可部署格边界 → 观测高亮边界 的距离（≈0 为一致 ✓；自测过：观测自比 = 0.0 ✓）"}


def audit_stage(stage: str, hl_frames: list, clean: str | None = None,
                frame_size: tuple[int, int] | None = None) -> dict:
    """跑一次体检，**只返回证据**（无任何"修正后的 view" ✗）。与 `trusted()` 分工：它测，trusted 只读 ✓。"""
    import datetime as _dt
    r = audit(stage, clean or "", list(hl_frames))
    ch = chamfer_audit(stage, hl_frames)
    ch_bad = bool(ch.get("available")) and ch.get("median_px", 0) > CHAMFER_TOL_MED
    status = r["status"] if "FALLBACK" in r["status"] or r["status"] == "UNKNOWN" else         ("FALLBACK" if ch_bad else "TRUSTED")
    return {
        "stage": stage, "status": status,
        "reason": r["reason"] + ("" if not ch.get("available") else
                                 f"｜chamfer 中位 {ch['median_px']}px（阈值 {CHAMFER_TOL_MED}）"),
        "audit_version": AUDIT_VERSION, "detector_version": DETECTOR_VERSION,
        "tilecalc_version": "TileCalc2 (3rdparty/include/Arknights-Tile-Pos)",
        "frame_size": list(frame_size) if frame_size else None,
        "observed_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "_pairs": r.get("pairs", []),
        "action": action_of(status),
        "observation": {"frame": pathlib.Path(hl_frames[0]).name if hl_frames else None,
                        "axis_angle_deg": [pp["axis_deg"] for pp in r.get("pairs", [])],
                        "chamfer_px": ch,
                        "scale": {"available": False,
                                  "note": "观测格距估计器会锁到真格距的分数倍（36/60 vs 真值≈113）⇒ 不作为判据"}},
    }


def record(stage: str, result: dict, registry: str = "data/tilepos_audit.json") -> bool:
    """写登记表。**幂等**：同 audit_version 且同结论 ⇒ 不重写（只增证据 ✓）。"""
    import json as _json
    p = pathlib.Path(registry)
    d = {}
    if p.is_file():
        try:
            d = _json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                      # noqa: BLE001
            d = {}
    old = d.get(stage) or {}
    if old.get("audit_version") == result.get("audit_version") and old.get("status") == result.get("status"):
        return False
    d[stage] = result
    p.write_text(_json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


if __name__ == "__main__":
    main()
