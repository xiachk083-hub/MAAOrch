#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""挖矿概率排产器 —— 把"还有哪些石头 + 大概能不能拿到"算成一个期望值清单。

目标里那条"统计与概率"就落在这：**不再按固定顺序硬刷，而是按期望收益排**。

EV(单位) = P(能拿到这颗源石) × 1 颗
  P(3星) 由三块证据合成（都是现成的数据，不猜）：
    ① **本号战绩**（runtime/axis_outcomes.jsonl）：这关这轴跑过几次、几次三星 → Laplace 平滑
    ② **P(3星) 模型**（runtime/axis_rank.json）：缺口硬门槛 + 相似迁移 + 热度 + 探索
    ③ **代价**：apCost（理智）+ 已知的失败次数（惩罚反复失败的关）
  状态表 runtime/_prime_state3.json 决定"还该不该打"：done 跳过、partial 只差补三星、
  noreach 暂缓。

关卡清单来源（2026-09-24，免维护口径）：**优先 runtime/learned/stages_*.json**（运行时自己
学到的：代号 + 理智），读不到才退回版本快照 data/_stage_table.json —— 并在输出里写明用的是哪个。
主线（task-7）：额外吃 runtime/plans/story_mines.json 的章节清单（EP07~EP14 有缺口的章），
  这些单位默认 state=todo（主线逐关星数仓库里没有，见该文件的 state_reason）；
  **EV 公式与排序口径完全没动**，只是多了一批输入单位。

用法: python tools/plan_mines.py [--top 20]
产出: runtime/mine_plan.json
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
RT = ROOT / "runtime"


def load_jsonl(p: pathlib.Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def load_stage_source() -> tuple[dict, dict, str]:
    """关卡代号 → (stage_id, 理智)。**learned 优先、旧表兜底**（2026-09-24 免维护口径）。

    返回 (by_code, learned, 来源说明)；by_code[(code, "普通"|"突袭")] = (stage_id, ap)
    """
    by_code: dict = {}
    learned: dict[str, set] = {}
    ldir = RT / "learned"
    for f in sorted(ldir.glob("stages_*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        pre = str(d.get("prefix") or f.stem.split("_", 1)[-1]).upper()
        aps = d.get("ap") or {}
        sids = d.get("stage_id") or {}
        for c in d.get("codes") or []:
            c = str(c).upper()
            learned.setdefault(pre, set()).add(c)
            if c in aps:
                by_code[(c, "普通")] = (sids.get(c), int(aps[c]))
    # 旧表兜底：既补 learned 没有的代号，也补突袭档（learned 目前只学普通档）
    try:
        table = json.load(open(ROOT / "data" / "_stage_table.json", encoding="utf-8"))["stages"]
    except Exception:
        table = {}
    n_table = 0
    for sid, s in table.items():
        code = (s.get("code") or "").upper()
        if not code:
            continue
        want = "突袭" if s.get("difficulty") == "FOUR_STAR" else "普通"
        if (code, want) not in by_code:
            by_code[(code, want)] = (sid, int(s.get("apCost") or 15))
            n_table += 1
    src = (f"learned({len(learned)} 个前缀/{sum(len(v) for v in learned.values())} 个代号)"
           + (f" + 旧表补 {n_table} 条" if n_table else "") if learned else "旧表(_stage_table.json)")
    return by_code, learned, src


def load_story_units() -> tuple[dict, dict]:
    """主线缺口清单（task-7）→ {unit_key: {stage_id, ap, chapter, chapter_name}}。

    只在 runtime/plans/story_mines.json 存在时生效；不存在的章/关一律不动。
    """
    p = RT / "plans" / "story_mines.json"
    if not p.exists():
        return {}, {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}, {}
    units: dict[str, dict] = {}
    cmap: dict[str, str] = {}
    for ch in d.get("chapters") or []:
        ep = ch.get("ep")
        label = f"EP{int(ep):02d} {ch.get('name')}"
        # 每关两档：普通 + （有 突袭/磨难 版本时）突袭
        normal: dict[str, dict] = {}
        hard: dict[str, dict] = {}
        for u in ch.get("units") or []:
            kd = u.get("kind")
            if kd == "普通":
                normal.setdefault(u["code"], u)
            elif kd in ("突袭", "磨难"):
                hard.setdefault(u["code"], u)
        for code, u in normal.items():
            key = f"{code}|普通"
            units[key] = {"stage_id": u.get("stage_id"), "ap": u.get("ap"),
                          "chapter": str(ep), "chapter_name": label}
            cmap[key] = label
        for code, u in hard.items():
            key = f"{code}|突袭"
            units[key] = {"stage_id": u.get("stage_id"), "ap": u.get("ap"),
                          "chapter": str(ep), "chapter_name": label}
            cmap[key] = label
    return units, cmap


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()

    state = json.loads((RT / "_prime_state3.json").read_text(encoding="utf-8"))
    ledger = load_jsonl(RT / "axis_outcomes.jsonl")
    rank = json.loads((RT / "axis_rank.json").read_text(encoding="utf-8")) if (RT / "axis_rank.json").exists() else {}
    by_code, learned, src = load_stage_source()
    story, story_label = load_story_units()

    # 主线单位并进来：状态表里有就尊重状态表，没有就按 todo（主线逐关星数仓库里没有）
    units_state = dict(state)
    n_story_new = 0
    for k in story:
        if k not in units_state:
            units_state[k] = "todo"
            n_story_new += 1

    # 本号战绩：stage → [三星次数, 总次数]
    #   **只统计「真打且有效」的行**（2026-09-24 口径修：Mas 审计 → Hermes 复核）。
    #   旧代码只看 `stars` 一列，于是两个方向都违反「演习读数只用于挑轴，绝不入账」：
    #     · `mode == "drill"`（演习）：只烧演习券、**不产收益** ⇒ 演习 3★ 不是胜绩
    #       （`act43side_ex02_59767` 的 `drill-3star` 白给 AD-EX-2 加了一个 g）、
    #       演习 0★/无效也不是败绩（会把 p_hist 往下压）。
    #     · `valid is False`（无效局：没拿到结算页结论/没进战斗）：连星数都没有，
    #       不该算作「试过一次」。
    #   胜绩口径两个来源：3★ 读数 **或** `stone: true` —— 源石到账是终局证据，
    #   结算页星数判据曾把亮勋章读成 0★（真通关只留下 stone=true + stars=null）。
    hist = collections.defaultdict(lambda: [0, 0])
    n_skip = 0
    for o in ledger:
        st = o.get("stage")
        if not st:
            continue
        if o.get("mode") == "drill" or o.get("valid") is False:
            n_skip += 1
            continue
        hist[st][1] += 1
        if (o.get("stars") or 0) >= 3 or o.get("stone") is True:
            hist[st][0] += 1

    rows = []
    for key, st in units_state.items():
        if st in ("done", "noreach"):
            continue                       # done 跳过；noreach 暂缓（进不去）
        code, want = key.split("|")
        meta = story.get(key) or {}
        sid, ap = meta.get("stage_id"), meta.get("ap")
        if sid is None:
            sid, ap = by_code.get((code, want), (None, 15))
        g, n = hist.get(sid or "", [0, 0])
        p_hist = (g + 1) / (n + 2)         # Laplace 平滑：没跑过 = 0.5
        axes = rank.get(f"{sid}|{want}") or []
        p_model = None
        if axes:
            # axis_rank 里排第一的那份轴，它的 score 已在模型里算过；这里只取"有没有可用轴"
            p_model = 0.55 if len(axes) >= 3 else (0.45 if axes else 0.25)
        p = p_hist if p_model is None else round(0.6 * p_hist + 0.4 * p_model, 3)
        if st == "partial":
            p = min(1.0, p + 0.15)         # 只差补三星：已有基础
        ev = p * 1.0                       # 一颗源石
        rows.append({"unit": key, "stage_id": sid, "want": want, "state": st, "ap": ap,
                     "p_3star": p, "p_hist": round(p_hist, 3), "hist": f"{g}/{n}",
                     "axes": len(axes), "ev": round(ev, 3), "ev_per_ap": round(ev / max(ap, 1), 3),
                     "src": "story" if key in story else "event",
                     "chapter_name": story_label.get(key)})

    # **整章期望**（2026-09-24 追加）：单颗 EV 都在 0.5 上下时，"哪一章还有一大堆没打"
    #   才是真正的排序依据 —— 用户口径：缺星数 ÷ 4 ≈ 这一章还剩几颗源石。
    #   这里用"这一章待打单位数 × 平均 P(3星)"当整章期望收益，**先按章排、章内按单颗 EV 排**。
    #   主线单位（task-7）的章键用数字前缀（7/8/…）—— 和队列的 code.split("-")[0] 口径一致。
    chap = collections.defaultdict(lambda: [0.0, 0])
    for r in rows:
        pre = (r["unit"].split("-")[0] or "?").upper()
        chap[pre][0] += r["ev"]
        chap[pre][1] += 1
    for r in rows:
        pre = (r["unit"].split("-")[0] or "?").upper()
        r["chapter"] = pre
        r["chapter_ev"] = round(chap[pre][0], 2)
        r["chapter_pending"] = chap[pre][1]
        if r.get("chapter_name"):
            r["chapter_display"] = r["chapter_name"]
        elif r["src"] == "story":
            r["chapter_display"] = f"EP{pre}"

    rows.sort(key=lambda r: (-r["chapter_ev"], -r["ev"], -r["ev_per_ap"]))
    (RT / "mine_plan.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"关卡清单来源：{src}")
    print(f"战绩口径：axis_outcomes.jsonl {len(ledger)} 行 → 采纳 {len(ledger) - n_skip} 行"
          f"（跳过演习/无效 {n_skip} 行｜胜绩 = 3★ 或 stone=true）")
    if story:
        print(f"主线缺口清单：{len(story)} 个单位（新登记 todo {n_story_new} 个）"
              f"｜章节 {sorted({v for v in story_label.values()})}")
    print(f"待打 {len(rows)} 个单位｜按**整章期望收益**排序（章内再按单颗 EV）：")
    print(f"{'单位':22s} {'状态':8s} {'P(3星)':>7s} {'EV':>5s} {'EV/理智':>8s} {'战绩':>6s} {'轴':>3s} {'理智':>4s} {'章EV':>6s} {'章待打':>6s} {'来源':>5s}")
    for r in rows[:a.top]:
        print(f"{r['unit']:22s} {r['state']:8s} {r['p_3star']:7.3f} {r['ev']:5.3f} "
              f"{r['ev_per_ap']:8.3f} {r['hist']:>6s} {r['axes']:3d} {r['ap']:4d} "
              f"{r['chapter_ev']:6.2f} {r['chapter_pending']:6d} {r['src']:>5s}")
    # **主线章节单独列一遍**（验收口径：plan_mines 输出里能看到这些章节）
    story_rows = [r for r in rows if r["src"] == "story"]
    if story_rows:
        print("\n主线章节（来自 runtime/plans/story_mines.json，缺星来自 runtime/_x.png）：")
        def _epnum(r):
            try:
                return int(r["chapter"])
            except Exception:
                return 99
        by_ch = collections.defaultdict(list)
        for r in story_rows:
            by_ch[r["chapter"]].append(r)
        for ch in sorted(by_ch, key=lambda c: _epnum(by_ch[c][0])):
            rs = by_ch[ch]
            nm = rs[0].get("chapter_name") or f"EP{ch}"
            print(f"  {nm:<20s} 待打 {len(rs):3d} 个单位 章EV {rs[0]['chapter_ev']:6.2f} "
                  f"（普通 {sum(1 for r in rs if r['want'] == '普通')}｜突袭 {sum(1 for r in rs if r['want'] == '突袭')}）"
                  f"  例：{rs[0]['unit']} ap={rs[0]['ap']}")
    print(f"\n明细写 {RT / 'mine_plan.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
