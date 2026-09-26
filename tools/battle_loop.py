#!/usr/bin/env python3
"""最小战斗循环 — 暂停→截图→提取状态→(规则)决策→执行→步进→再暂停。

零件全部**已有实测出处**（别在这里另发明一套）：
    截图/黑帧重试       services/ai_agent/percept.py
    暂停·恢复 (1813,81)  docs/SKILL_EMULATOR_OPS.md §3b
    拖拽放人（两段慢滑） docs/SKILL_EMULATOR_OPS.md §4 第 6-9 条
    影格投影 ×1.5       services/ai_agent/tile_map.py
    费用 OCR / 卡排职业图标  §8 分工表（这两件 LLM 稳、本地也稳，循环里用本地省 30s/帧）
    我方干员（蓝绿血条） docs/CLEARING_AGENT.md §8c（暂停压暗帧 ONNX 失效）
    敌人位置/数量        tools/enemy_timeline.enemies_at(stage, T)
    T = 费用钟（T = 费用 − initialCost + **已花费用**）  §8d-2 ④
         ⚠️ 2026-09-19 实测修正：只用 `费用−initialCost` 会**系统性偏小**。
         证据：一帧里费用 19、旧式推 T=9，但按敌人位置反查（时间表里只有 T=30/31
         同时出现 (3,0)+(5,0) 两个撬棍巡，画面正是这两只）⇒ 真 T≈30，
         差的 21 就是那只**手动先放**的煌的部署费。所以 spent 必须计进去。
    规则来源            从跑得通的作业提 (name/location/direction)；作业里的"谁"若不在牌堆，
                        按职业替换（落格与朝向照用作业的）

设计取舍（v1，为"能转"不为"打好"）：
  · **决策 = 规则**（先验证循环本身）；LLM 只留钩子（`--llm-check`），失败才问
  · 放人**在暂停态做**（已验证可行）⇒ 一步 = 暂停中操作完 → 恢复 step 秒 → 再暂停
  · 每圈用**费用钟重锚 T**（不靠累加，避免漂移）

用法：
    python tools/battle_loop.py --stage act51side_01 --job data/copilot/act51side_01/act51side_01_90096.json --dry
    python tools/battle_loop.py --stage act51side_01 --job <...> --cycles 8 --step 1.5
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "tools"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import enemy_timeline as ET  # noqa: E402
import cost_model as CM  # noqa: E402
from services.ai_agent.percept import Perception, ResourcePaths  # noqa: E402
from services.ai_agent.tile_map import load_level  # noqa: E402

ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
DEV = "emulator-5554"
PAUSE_BTN = (1813, 81)          # SKILL_EMULATOR_OPS §3b
COST_ROI_1280 = (1199, 494, 80, 44)   # BattleCostData
MELEE_ROLES = {"Pioneer", "Warrior", "Tank", "Special"}


# ── 底层：截图 / 点击 ─────────────────────────────────────────────

# ⚠️ Windows：不带 CREATE_NO_WINDOW，每次调 adb 都会**闪一个 cmd 黑窗** ✗
# （用户点名"那个弹窗问题，是 win 的 cmd" —— 可视化界面每 4 秒截一帧 ⇒ 每 4 秒闪一次）
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def sh(*args: str) -> str:
    return subprocess.run([ADB, "-s", DEV, "shell", *args],
                          capture_output=True, text=True, creationflags=NOWIN).stdout


def shot(tries: int = 4) -> np.ndarray | None:
    """截一帧（黑帧重试 —— 实测间歇黑帧 ~2/10）。"""
    for _ in range(tries):
        b = subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"],
                           capture_output=True, creationflags=NOWIN).stdout
        if len(b) > 1000:
            im = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)
            if im is not None and im.mean() > 1:
                return im
    return None


def shot_fast(tries: int = 3) -> np.ndarray | None:
    """快取图：`exec-out screencap`（**不带 -p**）→ 原始 RGBA，实测 **0.33s vs PNG 1.1s（3.4×）**。

    三个必须按实测来的点（2026-09-19 全验过）：
      ① 原始流**带 16 字节头**（宽、高、format、colorspace 各 4 字节小端）；
         不去掉的话整幅图会**均匀错位 4px**（我拿齿轮模板量出来的：(74,40) vs 真值 (70,40)），
         坐标全废 ✗；
      ② 通道序是 **RGBA**，要 `[:, :, 2::-1]` 转 BGR；
      ③ 仍然走 `exec-out` 这条安全通道 —— **不许碰 `nc_capture`**（`tools/pit_check.py` 坑②：
         它的 timeout 是假的，会冻死整条推图）。
      ④ **黑帧闸要查均值 + 方差**：原来只查 `mean() > 1`，但实测间断返回的"黑帧"**不是纯 0**
         （2026-09-19 采样器把一整格纯黑存进了产物 ✗）⇒ 现在 `mean > 4 and std > 8` 才收。
    """
    for _ in range(tries):
        b = subprocess.run([ADB, "-s", DEV, "exec-out", "screencap"],
                           capture_output=True, creationflags=NOWIN).stdout
        if len(b) < 1920 * 1080 * 4:
            continue
        off = len(b) - 1920 * 1080 * 4           # 头有多大自己算（正常是 16）
        if off > 64:                             # 头大得离谱 ⇒ 不是正常的 screencap 输出
            continue
        a = np.frombuffer(b[off:off + 1920 * 1080 * 4], np.uint8).reshape(1080, 1920, 4)
        im = a[:, :, 2::-1].copy()
        if im.mean() > 4 and im.std() > 8:       # 黑帧重试（同 shot()）
            return im
    return None


def tap(x: int, y: int) -> None:
    sh("input", "tap", str(int(x)), str(int(y)))


# ── 报幕/台词弹窗（用户说的"那个弹窗问题"） ─────────────────────────
def popup_cover(img) -> tuple[int, int, int, int] | None:
    """报幕弹窗的遮挡区 (x0,y0,x1,y1)；没弹窗返回 None。

    2026-09-19 实测：战斗里左上出现**干员头像 + 近黑横条 (309,87)-(1440,236)**，
    连头像一起盖住 ≈ **(49,87)-(1440,356)** ⇒ 一口气盖掉 31 个指纹格里的 **16 个** ✗。
    关键性质：**暂停时游戏时间不走 ⇒ 它不会自己淡出**（恢复几秒才会消失）。
    ⇒ 两处受害：① 画面指纹会变（误触"指纹不符 ⇒ 停手"）② 盖住左上角（齿轮在下面）。
    """
    g = img.max(2)
    m = np.zeros(g.shape, np.uint8)
    m[40:340, 240:1560] = np.uint8(g[40:340, 240:1560] < 45) * 255   # 只在顶部中区找黑条
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((9, 25), np.uint8))
    n, _lab, st, _ce = cv2.connectedComponentsWithStats(m, 8)
    best = None
    for bx, by, bw, bh, ar in st[1:]:
        if ar > 8000 and bw > 300 and (best is None or ar > best[4]):
            best = (int(bx), int(by), int(bw), int(bh), int(ar))
    if best is None:
        return None
    bx, by, bw, bh, _ = best
    return (max(0, bx - 260), max(0, by - 10), bx + bw, min(img.shape[0], by + bh + 120))


# ── 状态提取 ────────────────────────────────────────────────────

class Loop:
    def __init__(self, stage: str, job: pathlib.Path | None, *, dry: bool = False,
                 auto: bool = False, instance: int = 1, client: str = "Official",
                 spent: float = 0.0) -> None:
        self.stage = stage
        self.dry = dry
        self.paths = ResourcePaths(instance, client)
        self.p = Perception.offline("data/test_battle_pa3.png", instance=instance, client=client)
        self.lv = load_level(stage, self.paths)
        self.stage_px = 1.5                      # 实机帧 1920 宽 / 1280（投影→设备像素；离线回放用图宽自适应）
        self.done: set[int] = set()        # 关卡身份：SPAWN 展开后的总敌数（记录用；顶部计数 OCR 在暂停/演习态不可靠，见 in_battle 注释）
        self.expected_total = len(ET._spawns(stage))
        self.walk_cells = [(c, r) for r in range(self.lv.height) for c in range(self.lv.width)
                           if self.lv.at(c, r) and self.lv.at(c, r).walkable]
        # 指纹格的投影点（预存：比指纹时要按弹窗遮挡筛格，见 in_battle）
        self.cell_px = []
        for (c, r) in self.walk_cells:
            q = self.lv.project(c, r)
            self.cell_px.append((q[0] * self.stage_px, q[1] * self.stage_px) if q else None)
        self.ref_fp: np.ndarray | None = None    # 参照指纹（--expect-fp 载入；没有就拒绝跑）
        self.fp_tol = 26.0                       # 实测：同局差 ~2、换关差 ~42，取中间
        # 费用钟：initialCost/costIncreaseTime 从关卡文件 options 读（读不到就**不出 T**，不套默认 10）
        self.opt = CM.load_level_options(stage)
        self.spent = float(spent)                # 累计已花费用（含 --spent 声明的手动先放）
        self.spent_unknown = False               # 放过但费用读不到的牌 ⇒ T 偏小，如实标注
        # 费用读数的来源：暂停帧 OCR 优先（就在决策那一刻，最准）；读不出就用
        # **运行帧那次读到的值 + 到暂停为止经过的秒数 × regen**（2026-09-19 实测：
        # 暂停蒙层把画面压到 0.61 倍，OCR 在暂停帧读 `19` 失败、运行帧读 `20` 成功）。
        self.cost_hint: tuple[int, float] | None = None
        print(f"[关卡] {stage} 预期总敌数 {self.expected_total} 指纹格 {len(self.walk_cells)}")
        # 规则要在 walk_cells/expected_total 之后建（_auto_rule 用得到）
        self.rules = self._rules_from_job(job) if job else (self._auto_rule() if auto else [])

    # 规则（无作业时的兜底）：按**出怪表**派生一条最小规则
    def _auto_rule(self) -> list[dict]:
        """没有作业时的兜底：挑**最早出怪那条路线**，在它旁边找一格地面可部署格，朝向指向路线来向。
        只为"循环能转"，不追求打得好（正路是从跑得通的作业提规则，见 §9）。"""
        sp = [x for x in ET._spawns(self.stage) if x.get("cells")]
        if not sp:
            return []
        r = min(sp, key=lambda x: x["spawn_t"])
        cells = [tuple(c["grid"]) for c in r["cells"]]
        mid = cells[min(3, len(cells) - 1)]
        for (c, rr) in self.walk_cells:
            t = self.lv.at(c, rr)
            if t and t.buildable == 1 and max(abs(c - mid[0]), abs(rr - mid[1])) <= 1:
                d = "Left" if cells[0][0] > mid[0] else "Right"
                print(f"[规则] auto：{r['enemy_name']} 走 {cells[0]}→{cells[-1]}，落格 ({c},{rr}) 朝 {d}")
                return [{"name": None, "cell": (c, rr), "dir": d, "rule": "auto"}]
        return []

    # 规则：作业动作 → [(name, cell, dir)]
    def _rules_from_job(self, job: pathlib.Path) -> list[dict]:
        d = json.loads(pathlib.Path(job).read_text(encoding="utf-8"))
        out = []
        for a in d.get("actions") or []:
            if a.get("type") == "Deploy" and a.get("location"):
                out.append({"name": a.get("name"),
                            "cell": tuple(a["location"]),
                            "dir": (a.get("direction") or "Left"),
                            "rule": "job"})
        print(f"[规则] 从 {pathlib.Path(job).name} 提出 {len(out)} 条：",
              [(r["name"], r["cell"], r["dir"]) for r in out])
        return out

    # 读数
    def read_cost(self, img) -> int | None:
        s = img.shape[1] / 1280.0      # 按图算比例：实机帧 1920 宽 ⇒ 1.5；离线回放的图是 1280 宽 ⇒ 1.0
        x, y, w, h = COST_ROI_1280
        roi = (int(x * s), int(y * s), int(w * s), int(h * s))
        hit = self.p.ocr.read_region(img, roi, upscale=3, min_score=0.2)
        s = (hit.label or "").strip() if hit else ""
        return int(s) if s.isdigit() else None

    def read_deck(self, img) -> list[dict]:
        """卡排：职业图标 → [{role, x1080, cost_ocr}]（从左到右）。

        ⚠️ 阈值不能写死 0.85：**实测同一界面在不同状态下分数会掉**（这次的帧 0.85/0.7 全 0 命中、
        0.55 命中 24 —— 其中含大量伪命中，因为那张根本不是战斗界面）。
        所以走**阶梯 + 合理性校验**：取第一个命中数落在 [4, 14] 的阈值；都不落 ⇒ 返回 []（调用方据此判"不在战斗"）。
        """
        small = cv2.resize(img, (1280, 720), interpolation=cv2.INTER_AREA)
        for th in (0.85, 0.78, 0.70, 0.62, 0.55):
            found = {}
            for role in ("Pioneer", "Warrior", "Sniper", "Caster", "Tank",
                         "Medic", "Support", "Special"):
                for hh in self.p.templates.find_all(small, "BattleOperRole" + role,
                                                    roi=(0, 576, 1280, 144), threshold=th):
                    found[round(hh.center[0] * 1.5)] = role
            # **等距校验**（fail-closed）：卡排是等距的（1080p 下步距 ~164px）。
            # 实测踩到：真卡排被收起时，低阈值会冒出 6 个**间距毫无规律**的伪命中，
            # 只数个数会"假通过" ⇒ 必须要求等距，否则继续往下试阈值、都不行就返回 []。
            xs = sorted(found)
            gaps = [b - a for a, b in zip(xs, xs[1:])]
            even = bool(gaps) and (max(gaps) - min(gaps)) <= 40 and 60 <= (sum(gaps) / len(gaps)) <= 260
            # **右锚校验**：手牌行是**右对齐**的（右端固定 ~1880）⇒ 最右那张必须靠近右边。
            # 实测踩到（2026-09-19）：换关后低阈值在地图左半边刷出 10 个等距伪命中 ✗，
            # 只查"等距 + 个数"会**假通过**，于是手牌框整体画在地图上 ✗。
            right_anchored = bool(xs) and xs[-1] >= 1600
            if 4 <= len(found) <= 14 and even and right_anchored:
                out = []
                for x in sorted(found):
                    hit = self.p.ocr.read_region(img, (x - 15, 890, 130, 60), upscale=2, min_score=0.2)
                    s = (hit.label or "").strip() if hit else ""
                    out.append({"role": found[x], "x1080": x,
                                "cost": int(s) if s.isdigit() else None,
                                "th": th})
                return out
        return []

    def in_battle(self, img, cost, deck) -> tuple[bool, str]:
        """开打闸（**fail-closed**）：读数 + **参照指纹**校验。

        ① 费用读得到 ② 卡排张数在合理区间 ③ 画面指纹 == 参照指纹（`--expect-fp` 给的那份）。

        ⚠️ **默认没有参照指纹就拒绝跑**：不许"贴到屏幕上的任意一局"上 ——
        实测这台机上正跑着**别的关**（12 敌，而 PA-1 是 19），贴上去就成了"两只手抢一台模拟器"。
        参照指纹用 `--save-fp <file>` 在**你确认过的那一刻**存（例如刚进完演习、还没动手）。
        指纹 = 本关可走格的投影点上的一小块颜色均值（与敌我无关，只反映地图与镜头）。
        """
        if cost is None:
            return False, "费用读不到"
        if len(deck) < 4:
            return False, f"卡排只认出 {len(deck)} 张（阈值阶梯都不过）"
        if self.dry:
            # 离线回放：一帧固定图、不驱动游戏 ⇒ 指纹闸不适用（它的用途是"别贴到屏幕上的任意一局"）
            return True, "dry 离线回放（跳过指纹闸）"
        cov = popup_cover(img)
        fp = self.map_fingerprint(img)
        if self.ref_fp is None:
            return False, ("**没有参照指纹 ⇒ 拒绝跑**（不许贴到屏幕上的任意一局上）："
                           "先在确认过的时刻存一份 `--save-fp data/_battle_fp.npy`，"
                           "之后用 `--expect-fp data/_battle_fp.npy` 跑")
        per = np.abs(fp - self.ref_fp).mean(1)
        note = ""
        if cov:
            keep = np.array([px is not None and not (cov[0] - 20 <= px[0] <= cov[2] + 20
                                                      and cov[1] - 20 <= px[1] <= cov[3] + 20)
                             for px in self.cell_px])
            note = (f"（报幕弹窗盖住 {int((~keep).sum())} 格，已排除）" if keep.any() else "")
            per = per[keep] if keep.any() else per
        d = float(per.mean())
        if d > self.fp_tol:
            return False, (f"**画面指纹不符**（差 {d:.1f} > {self.fp_tol}）{note}：这不是参照那一局了"
                           f"（换关/被人接管/回大厅）⇒ 停手")
        return True, f"指纹一致（差 {d:.1f}）{note}"

    def map_fingerprint(self, img) -> np.ndarray:
        """本关可走格的投影点 → 小块颜色均值（画面指纹）。投影点是**固定**的，与敌我无关。"""
        vals = []
        for (c, r) in self.walk_cells:
            q = self.lv.project(c, r)
            if not q:
                continue
            X, Y = int(q[0] * self.stage_px), int(q[1] * self.stage_px)
            patch = img[max(0, Y - 8):Y + 8, max(0, X - 8):X + 8]
            vals.append(patch.reshape(-1, 3).mean(0) if patch.size else np.zeros(3))
        return np.array(vals)

    def our_operators(self, img) -> list[tuple[int, int]]:
        """我方干员 → [(cell)]：蓝绿血条颜色检测（暂停压暗帧 ONNX 失效，见 §8c）。"""
        b, g, r = (img[:, :, i].astype(int) for i in range(3))
        m = np.uint8(((b - r) > 25) & ((g - r) > 10) & (b > 90)) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 9), np.uint8))
        n, _lab, st, ce = cv2.connectedComponentsWithStats(m, 8)
        cells = []
        for (x, y, w, h, a), (cx, cy) in zip(st[1:], ce[1:]):
            if w >= 70 and 6 <= h <= 26 and a >= 400:
                cells.append(self.cell_of_px(cx, cy + 6))
        return [c for c in cells if c]

    def cell_of_px(self, px: float, py: float) -> tuple[int, int] | None:
        best = None
        for r in range(self.lv.height):
            for c in range(self.lv.width):
                t = self.lv.at(c, r)
                if not t or not t.walkable:
                    continue
                q = self.lv.project(c, r)
                if not q:
                    continue
                d = (q[0] * self.stage_px - px) ** 2 + (q[1] * self.stage_px - py) ** 2
                if best is None or d < best[0]:
                    best = (d, (c, r))
        return best[1] if best and best[0] < 90 ** 2 else None

    # 组装
    def clock(self, cost: int | None) -> tuple[float | None, str]:
        """费用钟 → (T, 来源说明)。**T 算不出就给 None**（fail-closed：宁可不报敌人位置）。

        `initialCost`/`costIncreaseTime` 从关卡 options 读（每个活动/主线都不一样，别写死 10）；
        `spent` = 循环自己放掉的 + `--spent` 声明的（手动先放的那些必须是**真费用**，
        否则 T 会偏小 —— 2026-09-19 实测：只算 `cost−initial` 时 T 报 9、真值 ≈30）。
        """
        if cost is None:
            return None, "费用读不到 ⇒ 不出 T"
        if not self.opt:
            return None, "关卡 options 缺失（data/_level_<stage>.json 没有 initialCost）⇒ 不出 T"
        T = round(cost - self.opt["initial"] + self.spent, 2)
        note = f"cost-{self.opt['initial']}+spent{self.spent:g}"
        if self.spent_unknown:
            note += " ⚠️ 有派出的牌费用读不到 ⇒ T 偏小"
        return T, note

    def state(self) -> dict:
        img = self.p.shot() if self.dry else shot()   # dry = 离线回放（读参照帧，不碰模拟器）
        if img is None:
            return {"ok": False, "why": "截图失败"}
        cost = self.read_cost(img)
        cost_src = "暂停帧 OCR"
        if cost is None and self.cost_hint:
            v, t0 = self.cost_hint
            regen = (self.opt or {}).get("regen", 1.0)
            dt = time.monotonic() - t0
            if regen <= 5:                        # regen=999 的训练关不折算（会炸成天文数字）
                cost = int(round(v + dt * regen))
                cost_src = f"运行帧 OCR {v} + {dt:.1f}s×{regen:g}"
            else:
                cost_src = f"暂停帧读不出，且本关 regen={regen} 不能折算"
        deck = self.read_deck(img)
        ok, why = self.in_battle(img, cost, deck)
        if not ok:
            return {"ok": False, "why": why}
        T, t_src = self.clock(cost)
        return {"ok": True, "img": img, "cost": cost, "cost_src": cost_src, "T": T, "T_src": t_src,
                "popup": popup_cover(img),
                "gate": why, "deck": deck, "ours": self.our_operators(img),
                "enemies": ET.enemies_at(self.stage, T) if T is not None and T >= 0 else []}

    # ── 决策（规则） ──
    def decide(self, s: dict) -> dict | None:
        for i, rule in enumerate(self.rules):
            if i in self.done:
                continue
            cell = rule["cell"]
            if any(tuple(o) == tuple(cell) for o in s["ours"]):
                self.done.add(i)          # 已经有人站那格 ⇒ 这条算完成
                continue
            card = self.pick_card(s["deck"], rule, s["cost"])
            if not card:
                return None               # 费用不够 / 牌堆没有能站的 ⇒ 等
            return {"kind": "deploy", "idx": i, "card": card, "cell": cell, "dir": rule["dir"]}
        return None

    def pick_card(self, deck: list[dict], rule: dict, cost: int | None) -> dict | None:
        """作业写的是具体干员；不在牌堆时**按职业替换**（落格/朝向仍照用作业的）。"""
        if cost is None:
            return None
        named = [c for c in deck if c.get("role") and c["role"].lower() in
                 (rule.get("name") or "").lower()]
        pool = named or [c for c in deck if c["role"] in MELEE_ROLES]
        affordable = [c for c in pool if c["cost"] is not None and c["cost"] <= cost]
        if affordable:
            return affordable[0]
        # 卡上费用 OCR 实测很不可靠（11 张只读出 1 张）⇒ **退回最左**：
        # 卡片按费用升序排列 ⇒ 最左的能站类卡就是最便宜的那张（先按升序假定，与 MAA 口径一致）
        return pool[0] if pool else None

    # ── 执行 ──
    def deploy(self, card: dict, cell: tuple[int, int], direction: str) -> bool:
        """落点必须走 `project()` 算出的**格心**，不许按画面目测。

        为什么（用户 2026-09-19 点出的"有时候放不精准"）：地图是**透视投影**（非仿射），
        (0,0) 处局部列基 **(+126,+33)**、行基 **(−19.5,+91.5)**（设备像素）——两条基都不与屏幕轴平行，
        所以"看着在格子上"和"坐标是格心"是两回事：**沿列偏 >65px 或沿行偏 >47px 就串到隔壁格**。
        格心之间的屏幕距离只有 130/94px ⇒ 目视误差轻松吃掉半个格子。
        ⚠️ 那两条基是**局部值**：`project(2,2)` 与"基向量×2"差 20px ⇒ 只能按"就近半格容差"理解，
        **不许拿它外推别格坐标**（要坐标就逐格调 project）。
        """
        cx, cy = int(card["x1080"] + 55), 939          # 卡内取点（图标在卡左上；y=939 是手动部署实测命中的值）
        q = self.lv.project(*cell)
        if not q:
            return False
        tx, ty = int(q[0] * self.stage_px), int(q[1] * self.stage_px)
        d = {"Left": (-1, 0), "Right": (1, 0), "Up": (0, -1), "Down": (0, 1)}.get(direction, (-1, 0))
        ex, ey = tx + d[0] * 120, ty + d[1] * 120     # 朝向**短滑**（600px 会把干员平移一格，见 §4.7）
        if self.dry:
            print(f"   [dry] 拖 {cx},{cy} → 格{cell}({tx},{ty}) 朝向{direction}")
            return True
        sh("input", "swipe", str(cx), str(cy), str(tx), str(ty), "500")
        time.sleep(0.6)
        sh("input", "swipe", str(tx), str(ty), str(ex), str(ey), "300")
        time.sleep(0.7)
        return True

    # ── 主循环 ──
    def deploy_allowed(self) -> tuple[bool, str]:
        """部署闸（三态 ✓）：只有几何体检 TRUSTED 的关卡才允许部署。

        外援 2026-09-19 §三态："FALLBACK / UNKNOWN 一律不画官方格网、**不部署**" ✓。
        为什么必须挡在**部署**这一层：格网画错最多是看错 ✗，**按错坐标拖干员**是实打实的错操作 ✗✗。
        """
        try:
            import tilepos_audit as _TA                              # noqa: PLC0415
            return _TA.trusted(self.stage)
        except Exception as e:                                       # noqa: BLE001
            return False, f"体检登记不可用（{str(e)[:50]}）⇒ 拒部署"

    def run(self, cycles: int, step: float, paused_ok: bool = True) -> None:
        for k in range(1, cycles + 1):
            s = self.state()
            for _try in range(3):                     # 报幕弹窗：暂停不淡出 ⇒ 恢复几秒再暂停
                if self.dry or not s.get("ok") or not s.get("popup"):
                    break
                print(f"    台词弹窗挡着 {s['popup']}（暂停时不会淡出）⇒ 恢复 {step}s 再暂停（第 {_try+1}/3 次）")
                tap(*PAUSE_BTN)
                time.sleep(step)
                tap(*PAUSE_BTN)
                time.sleep(0.5)
                s = self.state()
            if not s.get("ok"):
                print(f"[{k}] ✗ {s['why']}")
                return
            print(f"[{k}] T≈{s['T']}s({s['T_src']}) 费用={s['cost']}({s.get('cost_src')}) "
                  f"卡排={len(s['deck'])}张 "
                  f"我方={len(s['ours'])} 敌人={len(s['enemies'])} "
                  f"({[e['enemy_name'][:4] for e in s['enemies']]})")
            act = self.decide(s)
            if act and not self.dry:
                _ok, _why = self.deploy_allowed()
                if not _ok:
                    print(f"    ✗ 拒部署（几何未验证）：{_why} ⇒ 停手")
                    return
            if act and self.dry:
                print(f"    [dry] 会下 {act['card']['role']}(费{act['card']['cost']}) "
                      f"到 {act['cell']} 朝 {act['dir']} —— 离线回放，不拖不点")
            elif act:
                print(f"    → 下 {act['card']['role']}(费{act['card']['cost']}) "
                      f"到 {act['cell']} 朝 {act['dir']}")
                c0 = s["cost"]
                self.deploy(act["card"], act["cell"], act["dir"])
                # **部署校验：看目标格上有没有我方血条**（直接信号）。
                # ⚠️ 别用"费用有没有扣"当初判据 —— 实测踩到：两次读数之间战斗在跑，
                # 费用自然回涨（+38）盖过扣费（-7）⇒ 误报"未生效"（那一拖其实成功了）。
                time.sleep(1.0)
                chk = self.state()
                hit = bool(chk.get("ok")) and any(
                    tuple(o) == tuple(act["cell"]) for o in chk.get("ours", []))
                if not hit:
                    # 没落到目标格时**报出它落到哪了**：斜投影下"差一格"和"根本没放下去"
                    # 是两种完全不同的故障（前者是拖拽终点没落在投影格心，后者是手势没生效），
                    # 光说"未生效"分不出来（用户 2026-09-19 点出的"有时候放不精准"）。
                    ours = [tuple(o) for o in chk.get("ours", [])] if chk.get("ok") else []
                    if ours:
                        tgt = tuple(act["cell"])
                        o = min(ours, key=lambda z: max(abs(z[0] - tgt[0]), abs(z[1] - tgt[1])))
                        d = max(abs(o[0] - tgt[0]), abs(o[1] - tgt[1]))
                        q1, q2 = self.lv.project(*tgt), self.lv.project(*o)
                        px = float(np.hypot(q1[0] - q2[0], q1[1] - q2[1]) * self.stage_px) if q1 and q2 else 0.0
                        print(f"    ✗ 没落到 {tgt}：最近的自己人在 {o}（差 {d} 格、屏幕 {px:.0f}px）"
                              f" —— 落点一律用 project() 的格心，别按画面目测（斜投影 ±65px/±47px 就串格）⇒ 停手")
                    else:
                        print(f"    ✗ 目标格 {act['cell']} 上没检出我方血条，场上也没有任何我方干员"
                              f"（拖动没生效？）⇒ 停手，不重复空转")
                    return
                # 花掉多少费用 = 暂停态里前后两次读数之差（暂停不regen ⇒ 这个差就是牌价，
                # 比 OCR 卡面数字稳）。测不到就标 unknown，让 T 自己承认偏小。
                c1 = chk.get("cost")
                if c0 is not None and c1 is not None and 0 <= (c0 - c1) <= 60:
                    self.spent += (c0 - c1)
                    print(f"    ✓ 部署生效（{act['cell']} 检出我方干员；本次花 {c0 - c1} 费 "
                          f"⇒ 累计 spent={self.spent:g}）")
                else:
                    self.spent_unknown = True
                    print(f"    ✓ 部署生效（{act['cell']} 检出我方干员）"
                          f"⚠️ 费用读不到（{c0}→{c1}）⇒ spent 记不了，T 会偏小")
                self.done.add(act["idx"])
            else:
                print("    → 等待（无可用规则/费用不够）")
            if k < cycles and not self.dry:
                tap(*PAUSE_BTN)                       # 恢复
                time.sleep(step)
                # 运行态抓一帧读费用（**暂停蒙层压暗 ⇒ 暂停帧读不出**，实测 2026-09-19）
                im_run = shot()
                if im_run is not None:
                    c_run = self.read_cost(im_run)
                    if c_run is not None:
                        self.cost_hint = (c_run, time.monotonic())
                tap(*PAUSE_BTN)                       # 再暂停
                time.sleep(0.5)


def main() -> None:
    ap = argparse.ArgumentParser(description="最小战斗循环（规则版）")
    ap.add_argument("--stage", default="act51side_01")
    ap.add_argument("--job", default="", help="作业 json（规则来源）")
    ap.add_argument("--auto-rule", action="store_true", help="无作业时按出怪表派生最小规则")
    ap.add_argument("--cycles", type=int, default=5)
    ap.add_argument("--step", type=float, default=1.5, help="每步恢复时长（秒）")
    ap.add_argument("--dry", action="store_true", help="不点游戏，只跑提取+决策")
    ap.add_argument("--save-fp", default="", help="把当前帧的画面指纹存到该文件（确认过的时刻）")
    ap.add_argument("--expect-fp", default="", help="参照指纹文件（跑之前必须给，否则拒绝）")
    ap.add_argument("--spent", type=float, default=0.0,
                    help="开局前已经花掉的费用（手动先放过的干员，按真实费用填）"
                         "—— 费用钟 T = 费用 − initialCost + spent，不填会偏小")
    args = ap.parse_args()
    L = Loop(args.stage, pathlib.Path(args.job) if args.job else None,
             dry=args.dry, auto=args.auto_rule, spent=args.spent)
    if args.save_fp:
        im = shot()
        if im is None:
            raise SystemExit("截图失败，存不了指纹")
        fp = L.map_fingerprint(im)
        np.save(args.save_fp, fp)
        print(f"已存参照指纹 {args.save_fp}（{len(fp)} 格）")
        return
    if args.expect_fp:
        L.ref_fp = np.load(args.expect_fp)
        print(f"已载参照指纹 {args.expect_fp}（{len(L.ref_fp)} 格）")
    L.run(args.cycles, args.step)


if __name__ == "__main__":
    main()
