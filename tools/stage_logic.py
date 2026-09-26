"""关卡逻辑：**多作业 → 单逻辑**（位置/功能/时间三路对齐 → 共性 + 灵活性 + 时机窗）。

由来（2026-09-16 用户《作业系统 · 总指导》§六）：一份作业是"这个号的这一手"，多看几份才能看出
**这一关本身要什么** —— 哪些格子是必须站的（多数作业都站）、那些格子要什么功能、几点之前必须落地。
有了它，一个新账号不必再从"抄某一份"开始，可以**按逻辑生成**（`generate_from_logic`）。

三路对齐（都要，少一路就只剩"抄作业"）：
  · **位置**：格 → 多少份作业在这里放了人（`coverage`）、放过谁（`operators`）、朝哪（`directions`）
  · **功能**：职业 → 功能类（block/output/heal/support/dp/special）；**医疗一律 `heal`，绝不进伤害类**
  · **时间**：该格的 `ordinal_median`（通常第几个下） + **`must_be_ready_by`**（这条路上最早到这一格的
    敌人几点到 —— 这就是"为什么必须第 1 个下"的量）

产物：`data/stage_logic/<stage>.json`（**关卡级、跨账号**，所以不放在某个账号的工作区里）。

⚠️ 时间窗依赖移速系数（`enemy_speed.MULT` 目前 1.0，是**有争议的结论**，见 tools/enemy_speed.py）。
输出里带 `speed_mult` 字段标明用的是哪套；换系数窗口会整体变化，且**判漏对系数敏感**，
所以别把它当成精确值——要看的是**先后关系**（谁必须在谁之前），那部分不受系数影响。
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
PROJ = _HERE.parent
sys.path.insert(0, str(_HERE))
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

import enemy_speed as ES       # noqa: E402
import job_meta as JM          # noqa: E402  （生成器默认值单一来源）
import viz_plan as VP          # noqa: E402
import workspace as WSP        # noqa: E402

LOGIC_DIR = PROJ / "data" / "stage_logic"

# 职业 → 功能类。**医疗恒为 heal**（实证过 char_table.description 只治疗、battle_data 无"能否攻击"字段，
# 见记忆 [[job-covers-mechanic-check]]）—— 把医疗算进伤害类会让"必要功能"整片失真。
ROLE_FUNC = {"PIONEER": "dp", "WARRIOR": "block", "TANK": "block", "SPECIALIST": "special",
             "SNIPER": "output", "CASTER": "output", "MEDIC": "heal", "SUPPORT": "support"}
FUNC_LABEL = {"block": "阻挡", "output": "输出", "heal": "治疗", "support": "辅助",
              "dp": "产费", "special": "特种"}
# 占位符（prts 作业里的「奶盾」「打人近卫」「高台核」…）没有 profession，只能按名字里的关键词归类。
# **这是猜测，不是数据** —— 所以每个位置都带 `func_source` 标出这一格的功能是"读职业"还是"猜的"，
# 汇总里也分开计数。不标出来就等于把猜测混进"必要功能"，那种结论不能当硬结论用。
PLACEHOLDER_FUNC = (("奶", "heal"), ("医", "heal"), ("盾", "block"), ("坦", "block"),
                    ("阻挡", "block"), ("拦", "block"), ("近卫", "block"), ("打人", "block"),
                    ("狙", "output"), ("术", "output"), ("法", "output"), ("高台", "output"),
                    ("输出", "output"), ("核", "output"), ("先锋", "dp"), ("产费", "dp"),
                    ("特", "special"))
# 各功能在 box 里能用哪些职业顶（生成时按这个筛人）
FUNC_PROFS = {"block": ("TANK", "WARRIOR", "PIONEER", "SPECIALIST"),
              "output": ("SNIPER", "CASTER"), "heal": ("MEDIC",),
              "support": ("SUPPORT",), "dp": ("PIONEER",),
              "special": ("SPECIALIST", "WARRIOR")}


def classify(name: str | None, prof: dict) -> tuple[str, str]:
    """(功能, 来源)。来源 ∈ profession / placeholder_guess / unknown —— 让人知道哪一格是猜的。"""
    p = prof.get(name) if name else None
    if p:
        return ROLE_FUNC.get(p) or "special", "profession"
    for kw, fn in PLACEHOLDER_FUNC:
        if name and kw in name:
            return fn, "placeholder_guess"
    return "special", "unknown"


def stage_samples(stage: str, include_derived: bool = False,
                  extra_files=()) -> list[dict]:
    """样本作业：`data/copilot` 池里声明了本关的作业（默认排除自产件），可另加现成文件。

    自产件（我们自己的编排）**默认不算样本**：它们是"试过的轴"，不是"能过的轴"，
    混进来会把"必要位置"算偏（一个没打过关的尝试站的位置凭什么叫必要）。
    """
    out = []
    seen = set()
    want_raid = stage.endswith("#f#")
    for e in WSP.pool_index():
        if e["stage"] != stage or (e["derived"] and not include_derived):
            continue
        # **样本不能混模式**：突袭/普通声明同一个 stage_name，混着对齐算出来的"必要位"
        # 是两种地图/数值的混合体（实测 SR-EX-4 的 7/7 就是这么算出来的）。
        if not want_raid and WSP.raid_only(e["id"]):
            continue
        if (stage, e["id"]) in seen:
            continue
        seen.add((stage, e["id"]))
        out.append({"src": e["file"], "job": e["job"], "derived": e["derived"]})
    for f in extra_files:
        p = pathlib.Path(f)
        if not p.is_file():
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(j, list):
            j = j[0] if j and isinstance(j[0], dict) else {}
        out.append({"src": str(p), "job": j, "derived": None})
    return out


def _lane_index(routes, cell):
    """格 → 它在哪几条路上、第几格（用于算"最早几点到这一格"）。"""
    hits = []
    for a, b, path in routes:
        if not path or cell not in [tuple(c) for c in path]:
            continue
        hits.append((a, [tuple(c) for c in path].index(cell)))
    return hits


def _deadline_at(waves, routes, cell, start=None):
    """这一格的**占用窗末刻**：敌人**还在这格**的最后一刻（按 `enemy_speed.MULT` 换算）。

    口径（2026-09-16 用户拍定）**占用窗**：`WAIT_FOR_SECONDS` 是**原地罚站** ——
    敌人在那 30 秒里一直站在那格，挡位随时能拦 ⇒ 判漏的 deadline 用**离开时刻**，不是到达时刻。

        · 到达 = 出怪时刻 + 走到第 i 格步数/移速 + **这一格之前**的累计停留
          （`waits_seq` 逐次给，见 `viz_plan.RoutePath`）
        · 末刻 = 到达 + **这一格自己**的停留（同格多次 WAIT 累加）

    ⚠️ 改动前这里只算到达（`t + i/移速`），`WAIT` 完全没进公式 ⇒ 对**停留点之后**的格一律偏早，
    判"必要位覆盖"偏松（以为来得及）。缺移速/缺路径时返回 None——不猜。
    """
    best = None
    for w in waves:
        if start is not None and tuple(w.get("start") or []) != tuple(start):
            continue
        sp = w.get("speed")
        if not sp:
            continue
        path, seq = None, ()
        for a, b, p in routes:
            if tuple(a) == tuple(w.get("start") or []):
                path = [tuple(c) for c in (p or [])]
                seq = tuple(getattr(p, "waits_seq", ()) or ())
                break
        if not path or cell not in path:
            continue
        idx = path.index(cell)
        cum = dwell = 0.0
        for c, secs in seq:
            try:
                j = path.index(tuple(c))
            except ValueError:
                continue                      # 停留点不在路径上 → 不算，也不猜
            if j < idx:
                cum += secs
            elif j == idx:
                dwell += secs
        t = round((w.get("t") or 0) + idx / sp + cum + dwell, 2)
        best = t if best is None else min(best, t)
    return best


def extract(stage: str, samples: list[dict], threshold: float = 0.6,
            min_jobs: int = 2, box_path=None) -> dict:
    """三路对齐 → 关卡逻辑 dict。`threshold` = 多少份作业用过才算"必要"。"""
    W, H, key, bt, routes, waves = VP.level(stage)
    n2t, rng, ops = VP.ranges_payload([s["job"] for s in samples], box_path)
    prof = VP.professions()
    path_tiles = set()
    for a, b, p in routes:
        for c in (p or []):
            path_tiles.add(tuple(c))

    by_grid: dict[tuple, dict] = {}
    func_jobs = collections.defaultdict(set)          # 功能 → 用过它的作业下标
    n_used = 0
    for si, s in enumerate(samples):
        dep = [a for a in (s["job"].get("actions") or [])
               if isinstance(a, dict) and a.get("type") in ("Deploy", "部署") and a.get("location")]
        if not dep:
            continue
        n_used += 1
        for k, a in enumerate(dep):
            cell = tuple(a["location"])
            fn, src = classify(a.get("name"), prof)
            func_jobs[fn].add(si)
            g = by_grid.setdefault(cell, {"cell": list(cell), "jobs": set(), "operators": collections.Counter(),
                                          "directions": collections.Counter(),
                                          "funcs": collections.Counter(), "func_src": collections.Counter(),
                                          "ordinals": [],
                                          "on_path": cell in path_tiles,
                                          "buildable": bt(*cell) if 0 <= cell[0] < W and 0 <= cell[1] < H else None})
            g["jobs"].add(si)
            g["operators"][a.get("name") or "?"] += 1
            if a.get("direction"):
                g["directions"][VP.norm_dir(a["direction"])] += 1
            g["funcs"][fn] += 1
            g["func_src"][src] += 1
            g["ordinals"].append(k + 1)

    total = max(n_used, 1)
    positions = []
    for cell, g in by_grid.items():
        cov = len(g["jobs"]) / total
        if cov < threshold or len(g["jobs"]) < min_jobs:
            continue
        fn = (g["funcs"].most_common(1) or [("special", 0)])[0][0]
        fsrc = (g["func_src"].most_common(1) or [("unknown", 0)])[0][0]
        lanes = _lane_index(routes, cell)
        must = _deadline_at(waves, routes, cell)      # **占用窗末刻**（不是到达时刻，见 docstring）
        positions.append({
            "grid": list(cell), "function": fn, "function_label": FUNC_LABEL.get(fn, fn),
            "func_source": fsrc,
            "coverage": round(cov, 3), "n_jobs": len(g["jobs"]),
            "on_path": g["on_path"], "buildable": g["buildable"],
            "operators": [{"name": n, "n": c} for n, c in g["operators"].most_common(6)],
            "direction": ((g["directions"].most_common(1) or [(None, 0)])[0][0]),
            "ordinal_median": sorted(g["ordinals"])[len(g["ordinals"]) // 2] if g["ordinals"] else None,
            "lanes": [list(a) for a, _ in lanes],
            "must_be_ready_by": must,
        })
    positions.sort(key=lambda p: ((p["must_be_ready_by"] if p["must_be_ready_by"] is not None else 9999),
                                  p["ordinal_median"] or 9))

    needed, optional = [], []
    for fn, s in sorted(func_jobs.items(), key=lambda kv: -len(kv[1])):
        (needed if len(s) / total >= threshold else optional).append(
            {"function": fn, "label": FUNC_LABEL.get(fn, fn), "n_jobs": len(s),
             "coverage": round(len(s) / total, 3)})
    n_guessed = sum(1 for p in positions if p["func_source"] != "profession")

    # 时机窗：按 ordinal 把"第几个下"翻译成"出怪后的哪段时间"（用 must_be_ready_by 的分布）
    timing = []
    for k in (1, 2, 3):
        grp = [p for p in positions if p["ordinal_median"] == k and p["must_be_ready_by"] is not None]
        if grp:
            lo = min(p["must_be_ready_by"] for p in grp)
            hi = max(p["must_be_ready_by"] for p in grp)
            timing.append({"window": "T+0~%s" % hi if k == 1 else "T+%s~%s" % (lo, hi),
                           "required": "第 %d 个部署要落地" % k,
                           "grids": [p["grid"] for p in grp]})

    return {"stage": stage, "sample_size": n_used, "threshold": threshold,
            "speed_mult": ES.MULT,
            "necessary_positions": positions,
            "necessary_functions": needed, "optional_functions": optional,
            "timing": timing,
            "n_func_guessed": n_guessed,
            "note": "位置/功能来自多作业对齐（阈值 %.2f，其中 %d 个位置的功能是按占位符名字猜的）；"
                    "时间窗依赖移速系数 %s（**有争议**，看先后关系别看绝对值）"
                    % (threshold, n_guessed, ES.MULT)}


def save(logic: dict) -> pathlib.Path:
    LOGIC_DIR.mkdir(parents=True, exist_ok=True)
    p = LOGIC_DIR / ("%s.json" % logic["stage"])
    p.write_text(json.dumps(logic, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load(stage: str) -> dict | None:
    p = LOGIC_DIR / ("%s.json" % stage)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


# ── 按逻辑生成 ──

def _box_pool(box: dict, prof: dict | None = None) -> dict:
    """box → {profession: [name…]}（只留练得动的：4★+ 精0 Lv1 实际用不了）。"""
    prof = prof or VP.professions()
    out = collections.defaultdict(list)
    for nm, o in box.items():
        if o.get("elite", 0) == 0 and (o.get("level") or 1) <= 1 and (o.get("rarity") or 3) >= 4:
            continue
        p = prof.get(nm)
        if p:
            out[p].append(nm)
    for k in out:
        out[k].sort(key=lambda n: (-(box[n].get("rarity") or 0), n))
    return out


def _lane_cells_for(routes, waves, cell):
    for a, b, p in routes:
        if p and cell in [tuple(c) for c in p]:
            return set(tuple(c) for c in p)
    return set()


def generate_from_logic(logic: dict, box: dict, account: str = "", level: str = "E2",
                        box_path=None, samples: list | None = None) -> tuple[dict, dict]:
    """按关卡逻辑 + 这个号的 box 生成一份作业 → (job, report)。

    挑人顺序（每一步都留理由，report 里逐条可查）：
      1. **逻辑里证明过的人**里有且 box 也有的（同一格上别人用过、且这个号有）；
      2. 否则按功能在该格的**几何/数值**表现挑：阻挡位跑一次 1v1 决斗（能打死/能扛住优先），
         输出位比"射程能盖到这条路几格"，治疗/辅助按职业取练度最好的。
    方向取该格多数作业的朝向；顺序按 `must_be_ready_by`（谁先到谁先下）。
    """
    import duel_model as DM
    import combat_model as CB
    stage = logic["stage"]
    W, H, key, bt, routes, waves = VP.level(stage)
    # 射程三件套**只算一次**：`ranges_payload` 每次调用都会重新读 battle_data.json（10MB 级），
    # 放进挑人循环里会让一次生成慢到不可用（这是"内层循环里重复加载"的第二次教训，第一次在 load_box）
    n2t, rng, ops = VP.ranges_payload([], box_path or VP.BOX_PATH)
    prof_map = VP.professions()
    # 技能槽从样本里取（多数作业给这个干员带几技能）——写死 1 会让生成的轴在 MAA 里技能不匹配，
    # 而"带哪个技能"是作业里唯一另一个必填字段（location/direction/skill 三件套）
    skill_vote = collections.Counter()
    for smp in (samples or []):
        for o in (smp.get("job") or {}).get("opers") or []:
            if isinstance(o, dict) and o.get("name") and o.get("skill"):
                skill_vote[(o["name"], int(o["skill"]))] += 1
    def skill_of(nm):
        cands = [(c, k) for (x, k), c in skill_vote.items() if x == nm]
        if not cands:
            return 1
        cands.sort(key=lambda t: -t[0])
        return cands[0][1]
    box_idx = {n: o for n, o in box.items()}
    by_prof = _box_pool(box, prof_map)
    used, actions, report = set(), [], []
    prev_box_path = VP.BOX_PATH
    try:
        if box_path:
            VP.BOX_PATH = pathlib.Path(box_path)
        # 逐格挑人（顺序 = 逻辑里给的顺序 = 时间上必须的先后）
        for pos in logic.get("necessary_positions") or []:
            cell = tuple(pos["grid"])
            fn = pos["function"]
            lane_cells = _lane_cells_for(routes, waves, cell)
            pick, why = None, ""
            proven = [o["name"] for o in (pos.get("operators") or [])]
            skipped_proven = []
            for nm in proven:                       # ① 别人证明过、这个号也有
                if nm not in box_idx or nm in used:
                    continue
                # **功能要对得上**：样本里可能是"另一个功能的人恰好站这格"（实测 [8,4] 既有
                # 奶盾也有一名近卫）——照抄名字会把近卫派到治疗位，生成的轴自相矛盾。
                f2, _src2 = classify(nm, prof_map)
                if fn != "special" and f2 != fn:
                    skipped_proven.append("%s(%s)" % (nm, FUNC_LABEL.get(f2, f2)))
                    continue
                pick, why = nm, "逻辑里这一格用过、本号也有（%s 对得上）" % FUNC_LABEL.get(fn, fn)
                break
            if not pick:                            # ② 按功能 + 几何/数值挑
                cands = []
                for p in FUNC_PROFS.get(fn, ()):
                    cands += [n for n in by_prof.get(p, []) if n not in used]
                if fn == "block" and cands:
                    first = min((w for w in waves if w.get("arrival") is not None),
                                key=lambda w: w["arrival"], default=None)
                    best = None
                    for nm in cands[:14]:
                        o = box_idx.get(nm) or {}
                        du = None
                        if first:
                            try:
                                du = DM.duel(first["key"], nm, t_deploy=0, t_enter=0,
                                             elite=o.get("elite"), level=o.get("level"))
                            except Exception:
                                du = None
                        score = (1 if (du or {}).get("result") == "enemy_died" else 0,
                                 (du or {}).get("our_hp_left") or 0)
                        if best is None or score > best[0]:
                            best = (score, nm, du)
                    if best:
                        pick, why = best[1], "阻挡位决斗：%s%s" % (
                            "敌人死" if best[0][0] else "挡位死",
                            ("（敌 %s）" % best[2].get("enemy")) if best[2] else "")
                elif fn == "output" and lane_cells and cands:
                    best = None
                    for nm in cands[:20]:
                        act = {"name": nm, "location": list(cell),
                               "direction": pos.get("direction") or "Right"}
                        cov = len(VP.range_cells(act, n2t, rng, ops, W, H) & lane_cells)
                        if best is None or cov > best[0]:
                            best = (cov, nm)
                    if best:
                        pick, why = best[1], "射程盖到这条路 %d 格" % best[0]
                if not pick and cands:
                    pick, why = cands[0], "该功能里练度最好的（没有几何/数值区分）"
            if not pick:
                report.append({"grid": list(cell), "function": fn, "picked": None,
                               "why": "box 里没有能顶这个功能的干员（跳过）"})
                continue
            used.add(pick)
            actions.append({"type": "Deploy", "name": pick, "location": list(cell),
                            "direction": pos.get("direction") or "Right"})
            report.append({"grid": list(cell), "function": fn, "picked": pick, "why": why,
                           "must_be_ready_by": pos.get("must_be_ready_by"),
                           "coverage": pos.get("coverage"),
                           "skipped_proven": skipped_proven})
    finally:
        VP.BOX_PATH = prev_box_path

    opers = [{"name": a["name"], "skill": skill_of(a["name"]), "skill_usage": 1}
             for a in actions]
    job = {"version": JM.GEN_JOB_VERSION, "stage_name": stage,
           "minimum_required": JM.GEN_MINIMUM_REQUIRED,   # 单一来源，见 tools/job_meta.py
           "doc": {"title": "%s 按关卡逻辑生成（%d 份样本）" % (stage, logic.get("sample_size") or 0),
                   "details": "由 tools/stage_logic.py 按位置/功能/时间对齐全自动生成；逐格挑人理由见 report"},
           "opers": opers, "groups": [],
           "actions": [{"type": "SpeedUp"}] + actions + [{"type": "SkillDaemon"}]}
    rep = {"stage": stage, "account": account, "n_positions": len(logic.get("necessary_positions") or []),
           "n_deployed": len(actions), "report": report,
           "functions": logic.get("necessary_functions")}
    return job, rep


def _cover_count(name, cell, direction, lane_cells, box_path) -> int:
    """这个干员站在 cell 朝 direction 时，射程盖到这条路上几格（含自身格）。

    ⚠️ 每次调用都会重读 `battle_data.json` ⇒ **不要放进循环里**（生成时的挑人循环已改成
    预先取一次三件套）。留这个函数只是为了单点验证/外部调用方便。
    """
    n2t, rng, ops = VP.ranges_payload([], box_path or VP.BOX_PATH)
    act = {"name": name, "location": list(cell), "direction": direction}
    return len(VP.range_cells(act, n2t, rng, ops, 99, 99) & lane_cells)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="关卡逻辑：多作业对齐 → 单逻辑；也可按逻辑生成")
    ap.add_argument("--stage", required=True, help="stageId，如 act54side_01")
    ap.add_argument("--threshold", type=float, default=0.6, help="多少份作业用过才算必要（默认 0.6）")
    ap.add_argument("--min-jobs", type=int, default=2, help="至少几份作业（默认 2）")
    ap.add_argument("--include-derived", action="store_true", help="把自己的编排也当样本")
    ap.add_argument("--extra", action="append", default=None, help="额外样本文件（可重复）")
    ap.add_argument("--generate", action="store_true", help="顺带按逻辑生成一份作业")
    ap.add_argument("--account", default=None, help="配 --generate：用哪个账号的 box")
    ap.add_argument("--activity", default=None, help="配 --generate：落到哪个工作区（给版本树）")
    ap.add_argument("--code", default=None, help="配 --generate：关卡代号（默认从工作区找）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不落盘")
    a = ap.parse_args(argv)

    samples = stage_samples(a.stage, a.include_derived, a.extra or [])
    if not samples:
        raise SystemExit("池子里没有 %s 的样本作业（先用 --extra 指定几份）" % a.stage)
    box_path = WSP.box_path(a.account) if a.account else None
    logic = extract(a.stage, samples, a.threshold, a.min_jobs, box_path)
    print("关卡逻辑 %s：样本 %d 份 ｜ 必要位 %d 个 ｜ 必要功能 %s ｜ 可选 %s ｜ 系数 %s"
          % (a.stage, logic["sample_size"], len(logic["necessary_positions"]),
             "/".join(x["label"] for x in logic["necessary_functions"]) or "-",
             "/".join(x["label"] for x in logic["optional_functions"]) or "-", logic["speed_mult"]))
    for p in logic["necessary_positions"]:
        ready = ("T+%-5s" % p["must_be_ready_by"]) if p["must_be_ready_by"] is not None \
            else "—（不在出怪路径上，时间不约束）"
        print("   [%d,%d] %-4s 覆盖%3d%% ×%-2d 序%-2s 需在 %s 前 ｜ 用过：%s ｜ 朝向 %s%s"
              % (p["grid"][0], p["grid"][1], p["function_label"], round(p["coverage"] * 100),
                 p["n_jobs"], p["ordinal_median"], ready,
                 "、".join(o["name"] for o in p["operators"][:4]),
                 (p["direction"] or ["?"])[0],
                 "（功能是猜的）" if p["func_source"] != "profession" else ""))
    if not a.dry_run:
        print("→", save(logic).relative_to(PROJ))
    if not a.generate:
        return 0
    if not a.account:
        raise SystemExit("--generate 需要 --account（按哪个号的 box 挑人）")
    box = WSP.load_account_box(a.account)[0]["operators"]
    job, rep = generate_from_logic(logic, box, a.account, box_path=box_path, samples=samples)
    print("\n按逻辑生成：必要位 %d → 落地 %d 个" % (rep["n_positions"], rep["n_deployed"]))
    for r in rep["report"]:
        print("   [%d,%d] %-4s → %-10s %s" % (r["grid"][0], r["grid"][1], r["function"],
                                             r["picked"] or "（缺人）", r["why"]))
        if r.get("skipped_proven"):
            print("        （跳过逻辑里用过但功能对不上的：%s）" % "、".join(r["skipped_proven"]))
    # 评一版：复用反馈模块的判定（冲突 + 每条出怪判漏），**不另写判定逻辑**
    try:
        import feedback as FB
        ev = FB.evaluate(job, a.stage, box_path)
        if ev.get("ok"):
            v = ev["verdicts"]
            print("   评估：判漏 已挡%d 可打%d 存疑%d ｜ 冲突 %s"
                  % (v["已挡"], v["可打"], v["存疑"], "；".join(ev["warns"]) or "无"))
            rep["eval"] = ev
        else:
            print("   评估失败：%s" % ev.get("error"))
    except Exception as e:
        print("   评估跳过：%s: %s" % (type(e).__name__, e))
    if a.dry_run:
        print(json.dumps(job, ensure_ascii=False)[:400])
        return 0
    sd = WSP.find_stage_dir(a.account, a.activity, a.code or a.stage) if a.activity else None
    if sd:
        import versions as VSN
        parent = None
        tree = VSN.load_tree(sd)
        roots = [m for m in tree if not m.get("parent") and m.get("author") == "ai"]
        node = VSN.add_version(sd, job, author="ai", parent=(roots[0]["id"] if roots else None),
                               note="按关卡逻辑生成（%d 份样本，必要位 %d 个）"
                                    % (logic["sample_size"], rep["n_positions"]),
                               stage_id=a.stage, account=a.account, activity=a.activity,
                               code=sd.name)
        print("→ 版本 %s（%s）" % (node["id"], (sd / "versions" / node["file"]).relative_to(PROJ)))
        WSP.write_activity_index(a.account, a.activity)
    else:
        out = PROJ / "data" / "plan_loop" / ("%s_logic_gen.json" % a.stage)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        print("→ %s（没给 --activity，落到这里）" % out.relative_to(PROJ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
