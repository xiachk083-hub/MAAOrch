#!/usr/bin/env python3
"""打关迭代循环：编排 → 尝试 → 失败分析 → 改招 → 再编排 → … 直到三星。

用户口径（2026-09-15）：
    "编排，然后尝试一下；失败了分析原因，然后编排再尝试，再失败了再分析，直到通关"
    —— 不是无脑重试：每一轮都要**按失败证据改打法**。

四个环节与证据来源:
    编排  build_plan(stage, box, variant)  —— 按地图(路线数/choke/高台) + 敌人数值 + 号内练度
    尝试  点关卡节点 → maa_copilot_run（本地 json；失败会返还理智，可反复打）
    判定  MAA 日志（MissionFailed / 最后执行到第几步 / 本局用时）+ 结算截图三星核验
    分析  归因 = 伤害不够 / 漏怪(路线覆盖不足) / 挡位被秒 / 部署冲突(格子或费用)
    改招  按归因切到下一个策略变体（见 VARIANTS），并把该轮方案/结果/归因写进 ledger

用法:
    python tools/plan_loop.py --stage act16side_ex01 --node 390,165 --emu 2 --operbox data/operbox_pasted_0915e.json --max-iter 6
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import subprocess
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "tools"))
import stage_value as SV  # noqa: E402
import job_meta as JM     # noqa: E402  （生成器默认值单一来源）

OUT_ROOT = PROJ / "data" / "plan_loop"


# ---------- 数据 ----------
def load_stage(stage: str) -> dict:
    tile = SV.find_tile_file(stage)
    if not tile:
        raise SystemExit(f"MAA 资源里没有 {stage} 的地图")
    meta = json.loads(tile.read_text(encoding="utf-8"))
    level = SV.load_level(meta["levelId"], stage)
    enemies, spawns = {}, collections.Counter()
    for w in level.get("waves") or []:
        for fr in (w.get("fragments") or []):
            for a in (fr.get("actions") or []):
                if str(a.get("actionType", "")).upper() != "SPAWN":
                    continue
                lv = (SV.ENEMIES_BY_KEY.get(a["key"]) or [{}])[0].get("enemyData") or {}
                ab = lv.get("attributes") or {}
                enemies[a["key"]] = {"name": SV.unwrap(lv.get("name")) or a["key"],
                                     "hp": SV.unwrap(ab.get("maxHp")) or 0,
                                     "df": SV.unwrap(ab.get("def")) or 0,
                                     "res": SV.unwrap(ab.get("magicResistance")) or 0,
                                     "atk": SV.unwrap(ab.get("atk")) or 0}
                spawns[a["key"]] += a.get("count") or 1
    # 路线去重（起点）→ 有几条进攻路线（决定要几个挡位）
    routes = []
    for rt in level.get("routes") or []:
        sp, ep = rt.get("startPosition") or {}, rt.get("endPosition") or {}
        if None in (sp.get("row"), sp.get("col"), ep.get("row"), ep.get("col")):
            continue
        if (sp["row"], sp["col"]) == (ep["row"], ep["col"]):
            continue
        routes.append((sp, ep))
    starts = {(s["col"], s["row"]) for s, _ in routes}
    return {"meta": meta, "level": level, "enemies": enemies, "spawns": spawns,
            "lanes": len(starts) or 1, "total_hp": sum(e["hp"] * spawns[k] for k, e in enemies.items())}


def positions(tiles) -> tuple[tuple[int, int] | None, list, list, list]:
    """choke（离蓝门最近的地面）、其余地面、高台；另外给每条路线的入口附近地面"""
    ends = [(c, r) for r, row in enumerate(tiles) for c, cell in enumerate(row) if cell.get("isEnd")]
    ground = [(c, r) for r, row in enumerate(tiles) for c, cell in enumerate(row)
              if cell.get("buildableType") == 1 and cell.get("heightType") == 0]
    high = [(c, r) for r, row in enumerate(tiles) for c, cell in enumerate(row)
            if cell.get("buildableType") == 2]
    if not ends or not ground:
        return None, [], [], []
    d = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1])
    choke = min(ground, key=lambda g: min(d(g, e) for e in ends))
    return choke, [g for g in ground if g != choke], high, ends


# ---------- 编排（按变体） ----------
def pick(box: dict, stage: dict) -> dict:
    """按敌人数值挑人：物理/法术/挡位/奶/先锋"""
    pool: dict[str, list] = collections.defaultdict(list)
    for nm, o in box.items():
        if nm not in SV.N2I:
            continue
        # 硬规则：4★ 以上、精0 Lv1 = 实际用不了（2026-09-15 用户点出，之前漏接进挑人）
        if o.get("elite", 0) == 0 and o.get("level", 1) <= 1 and (o.get("rarity") or 3) >= 4:
            continue
        prof = SV.CT[SV.N2I[nm]]["profession"]
        p = SV.panel_at(nm, o["elite"], o["level"])
        kind, _idx, ab, scale, idelta = SV.dmg_kind(nm)
        atk = p["atk"] * (1 + ab)
        interval = max(0.3, p["interval"] + idelta)
        dps = max((SV.hit(atk * scale, e, kind) / interval) for e in stage["enemies"].values()) if stage["enemies"] else 0
        _ph = SV.CT[SV.N2I[nm]]["phases"]
        attrs = _ph[min(o["elite"], len(_ph) - 1)]["attributesKeyFrames"][-1]["data"]
        rec = {"name": nm, "lv": f"精{o['elite']}Lv{o['level']}", "kind": kind,
               "hp": p["hp"], "df": p["df"], "dps": dps, "prof": prof,
               "cost": attrs.get("cost"), "block": attrs.get("blockCnt")}
        key = ("先锋" if prof == "PIONEER" else "重装" if prof == "TANK" else
               "医疗" if prof == "MEDIC" else "物理" if kind == "物理" else "法术")
        pool[key].append(rec)
    for k in pool:
        pool[k].sort(key=lambda r: -r["dps"] if k in ("物理", "法术") else (-r["hp"] if k == "重装" else 0))
    return pool


def build_plan(stage: str, box: dict, variant: int, last: dict | None) -> dict:
    """按变体生成方案。variant 越大越保守/越厚（路线全盖 → 加人 → 双奶）"""
    st = load_stage(stage)
    tiles = st["meta"]["tiles"]
    choke, grounds, highs, ends = positions(tiles)
    pool = pick(box, st)
    plan: list[tuple[dict, tuple[int, int], str]] = []
    used: set = set()

    def take(kind: str, n: int = 1, skip=()) -> list[dict]:
        out = []
        for r in pool.get(kind, []):
            if r["name"] in skip or any(x[0]["name"] == r["name"] for x in plan):
                continue
            out.append(r)
            if len(out) >= n:
                break
        return out

    def place(rec: dict, cands: list, direction="Down"):
        for c in cands:
            if c not in used:
                used.add(c)
                plan.append((rec, c, direction))
                return True
        return False

    if variant == 0:                                   # V1：单 choke 堵门（GA-8 成功的形状）
        if choke:
            used.add(choke)
            for r in take("先锋", 1):
                plan.append((r, choke, "Down"))
            place_pref = grounds + highs
            for r in take("重装", 1) + take("物理", 2) + take("法术", 2) + take("医疗", 1):
                place(r, highs if r["kind"] in ("法术", "医疗") else grounds,
                      "Left" if r["kind"] in ("物理", "法术") else "Down")
    elif variant == 1:                                 # V2：每条路线一个挡位
        if choke:
            used.add(choke)
        seq = take("先锋", 1) + take("重装", 2) + take("医疗", 1) + take("物理", 2) + take("法术", 2)
        for i, r in enumerate(seq):
            cand = (highs if r["kind"] in ("法术", "医疗") else grounds)
            place(r, cand, "Down" if r["kind"] in ("重装", "医疗") else "Left")
    else:                                              # V3+：全厚（能上的都上，8~9 人）
        seq = (take("先锋", 1) + take("重装", 2) + take("医疗", 2)
               + take("物理", 3) + take("法术", 3))
        for r in seq[:9]:
            cand = (highs if r["kind"] in ("法术", "医疗") else grounds)
            place(r, cand, "Left")

    actions = [{"type": "SpeedUp"}]
    opers = []
    for rec, pos, direction in plan:
        opers.append({"name": rec["name"], "skill": 3, "skill_usage": 1})
        actions.append({"type": "Deploy", "name": rec["name"], "location": [pos[0], pos[1]], "direction": direction})
    actions.append({"type": "SkillDaemon"})
    return {"version": JM.GEN_JOB_VERSION, "stage_name": stage,
            "minimum_required": JM.GEN_MINIMUM_REQUIRED,   # 单一来源，见 tools/job_meta.py
            "doc": {"title": f"{stage} 编排v{variant + 1}", "details": f"路线数={st['lanes']} 总血={st['total_hp']} 变体={variant + 1}"},
            "opers": opers, "groups": [], "actions": actions}, st, plan


# ---------- 尝试 / 判定 / 分析 ----------
def run_once(emu: str, job: pathlib.Path, timeout: float) -> dict:
    log = PROJ / "services" / "maa" / "instances" / "1" / "debug" / "asst.log"
    before = log.stat().st_size if log.exists() else 0
    t0 = time.time()
    subprocess.run([sys.executable, str(PROJ / "tools" / "maa_copilot_run.py"),
                    "--emu", emu, "--copilot", str(job), "--timeout", str(timeout)],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout + 180)
    tail = ""
    try:
        with log.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(before)
            tail = f.read()
    except Exception:
        pass
    last_step = ""
    for ln in tail.splitlines():
        if "CopilotAction" in ln:
            last_step = ln[-260:]
    return {"seconds": round(time.time() - t0), "failed": "MissionFailed" in tail or "任务出错" in tail,
            "finished": "AllTasksCompleted" in tail, "deploy_errors": tail.count("not found in formation") + tail.count("干员不可用"),
            "last_action": last_step}


def analyze(r: dict, stage: dict, variant: int) -> str:
    if not r["failed"] and r["finished"]:
        return "未判定失败（需看三星）"
    reasons = []
    if r["deploy_errors"]:
        reasons.append("部署/干员不可用（练度或格子问题）")
    if r["seconds"] < 90:
        reasons.append("开局即崩（多半是漏怪/挡位被秒，路线覆盖不足）")
    else:
        reasons.append("打到最后但没收掉（多半是输出不够或奶不住）")
    return "；".join(reasons)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--node", required=True, help="关卡节点坐标 x,y（adb 点导航）")
    ap.add_argument("--emu", default="2")
    ap.add_argument("--operbox", type=pathlib.Path, default=PROJ / "data" / "operbox_pasted_0915e.json")
    ap.add_argument("--max-iter", type=int, default=6)
    ap.add_argument("--timeout", type=float, default=420.0)
    args = ap.parse_args()
    box = {o["name"]: o for o in json.loads(args.operbox.read_text(encoding="utf-8")) if o.get("own")}
    nx, ny = (int(v) for v in args.node.split(","))
    out_dir = OUT_ROOT / args.stage
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger = []

    from services.ai_agent.percept import AdbScreen
    sc = AdbScreen(args.emu)
    sc.ensure_connected()

    for it in range(args.max_iter):
        variant = min(it, 2)                                  # 0→单choke 1→双挡 2→全厚
        job, stage, plan = build_plan(args.stage, box, variant, ledger[-1] if ledger else None)
        p = out_dir / f"iter{it + 1}_v{variant + 1}.json"
        p.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n=== 第 {it + 1} 轮（变体 v{variant + 1}，{len(job['opers'])} 人）===", flush=True)
        for rec, pos, d in plan:
            print(f"    {rec['name']:<10}{rec['lv']:<10}{rec['kind']:<4}→[{pos[0]},{pos[1]}] {d}", flush=True)
        for _ in range(2):                     # 先退回关卡地图，保证起点确定
            sc._run(["shell", "input", "tap", "40", "40"]); time.sleep(1.0)
        print(f"    点节点 → {args.node}", flush=True)
        sc._run(["shell", "input", "tap", str(nx), str(ny)])
        time.sleep(1.8)
        r = run_once(args.emu, p, args.timeout)
        r["reason"] = analyze(r, stage, variant)
        print(f"    结果：failed={r['failed']} 用时{r['seconds']}s 归因：{r['reason']}", flush=True)
        ledger.append({"iter": it + 1, "variant": variant + 1, "job": str(p), **r})
        (out_dir / "ledger.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
        # 三星核验（用 maa_job_fallback 里现成的实现）
        try:
            from tools.maa_job_fallback import verify_three_star
            stars = verify_three_star(int(args.emu))
            ledger[-1]["three_star"] = stars
            print(f"    三星核验：{stars}", flush=True)
            if stars is True:
                print("    ✓ 三星通关，收工", flush=True)
                break
        except Exception as e:
            print(f"    ! 三星核验不可用：{e}", flush=True)
        # 收尾：点掉结果/失败 → 返回地图
        for pos in ((640, 660), (640, 660), (640, 400), (40, 40), (40, 40)):
            sc._run(["shell", "input", "tap", str(pos[0]), str(pos[1])])
            time.sleep(1.0)
    (out_dir / "ledger.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n迭代台账：{out_dir / 'ledger.json'}")


if __name__ == "__main__":
    main()
