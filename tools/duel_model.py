"""1v1 决斗模拟器：一个挡位 × 一个敌人，逐秒推进到一方死。

它是 `combat_model.effective_hold()` 的**严格版**：
    effective_hold 用 `min(挡位存活, 击杀耗时)` 两个静态量拼出来；
    duel 则**逐秒推进**，让"我方 DPS 随技能时间轴变化"和"敌方按攻击间隔打回来"
    同时发生，并算上**天赋自愈**（乌尔比安每次受伤回 80/120 血）。

## 数据源审计结论（**决定了这个模型能建、那个 7 层承伤模型不能建**）

| 要的东西 | 数据 |
|---|---|
| 技能持续攻击 buff（`atk`） | ✓ 在场 26 人里 12 人 |
| 技能爆发倍率（`atk_scale`） | ✓ 5 人（**单次伤害**，不是持续） |
| 群攻目标数（`attack@max_target`） | ✓ 2 人（史尔特尔/逻各斯）—— 其余按单体 |
| 天赋自愈（`value1`/`value2`/`hp_ratio`） | ✓ 乌尔比安（可解析） |
| 被动回血（`hp_recovery_per_sec`） | ✓ 桃金娘/黍 |
| 叠层（`max_stack_cnt`） | ✓ 3 人 |
| 治疗倍率（`heal_scale`） | ⚠️ 只 2 人；医疗基础治疗 = atk/间隔（可由面板推） |
| **护盾 `shield` / 庇护 `sanctuary` / 闪避 `dodge` / 易伤 `vulnerability` / 脆弱 `fragile`** | **✗ 全部不存在** |

⇒ 所以**没有**建"7 层承伤模型"（其中 4 层无输入源，等于为不存在的输入写管线）。
这里只算**有数据的那些层**：基础伤害（防御/法抗 + 5% 下限）、天赋自愈、技能时间轴。
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
PROJ = pathlib.Path(__file__).resolve().parent.parent
_cache = {}


def talent_self_heal(name, elite=None, level=None, potential=0):
    """解析"每次受到伤害时治疗自身"这类天赋 → (常驻回血量, 低血线回血量, 触发血线)。

    **从天赋描述 + 黑板读**（乌尔比安「本性的坚守」：`value1=80 / value2=120 / hp_ratio=0.5`），
    不硬编码。找不到返回 (0, 0, None) —— 不猜。
    """
    if "c" not in _cache:
        ct = json.loads((PROJ / "data" / "_char_table.json").read_text(encoding="utf-8"))
        _cache["c"] = {v["name"]: v for v in ct.values() if v.get("name")}
    v = _cache["c"].get(name)
    if not v:
        return (0.0, 0.0, None)
    phs = v.get("phases") or []
    e = min(max(int(elite or 0), 0), len(phs) - 1) if phs else 0
    for t in (v.get("talents") or []):
        for c in (t.get("candidates") or []):
            desc = c.get("description") or ""
            if "受到伤害" not in desc or "治疗自身" not in desc:
                continue
            u = c.get("unlockCondition") or {}
            up = {"PHASE_0": 0, "PHASE_1": 1, "PHASE_2": 2}.get(u.get("phase"), 0)
            if up > e:
                continue
            bb = {b.get("key"): b.get("value") for b in (c.get("blackboard") or [])}
            return (float(bb.get("value1") or 0), float(bb.get("value2") or 0),
                    bb.get("hp_ratio"))
    return (0.0, 0.0, None)


def duel(enemy_key, op_name, t_deploy=0.0, t_enter=0.0, skill_idx=None, mastery=0,
         elite=None, level=None, dt=0.25, horizon=600.0, our_hp_bonus=0.0):
    """一个挡位 × 一个敌人，逐秒打到一方死。

    返回 {result: enemy_died|blocker_died|timeout, t_death, log, ...}
    `our_hp_bonus` 用来喂技能的生命上限加成（如乌尔比安 S2 的 +60%）。
    """
    import combat_model as CB
    import operator_template as OT
    e = CB.load_enemy_combat(enemy_key)
    if not e:
        return {"reason": "enemy_not_found"}
    box = None
    try:
        import viz_plan as V
        box = V.load_box().get(op_name) or {}
    except Exception:
        box = {}
    st = CB.load_operator_stats(op_name, elite if elite is not None else box.get("elite"),
                                level if level is not None else box.get("level"))
    if not st:
        return {"reason": "operator_not_found", "op": op_name}
    # 技能的生命上限加成要一起算（乌尔比安 S2 是 +60% ⇒ 存活时间显著变长），
    # 否则"技能中"那一支会被算得比实际脆。从 skill_mods 自动取，不靠调用方记得传。
    hp_mult = 1.0 + our_hp_bonus
    if skill_idx:
        try:
            _m = OT.skill_mods(op_name, skill_idx, mastery)
            if _m.get("ok"):
                hp_mult *= _m.get("hp_mult") or 1.0
        except Exception:
            pass
    hp_max = (st.get("hp") or 0) * hp_mult
    hp = hp_max
    ehp = float(e.get("hp") or 0)
    interval = e.get("interval") or 0
    heal1, heal2, ratio = talent_self_heal(op_name, st.get("elite"), st.get("level"))
    # —— 相位级 DPS 预计算：一个相位内 DPS 不变，所以只算 2 次而不是几千次 ——
    # （逐秒调 dps_at_time 会把 792 次决斗拖到分钟级；这里等价但快约 1000 倍。
    #   ⚠️ 前提是"相位内 DPS 恒定"。若以后建模了叠层天赋（max_stack_cnt），
    #   那 DPS 会随驻场时间爬升，就不能这样预计算了 —— 这里显式记一笔。）
    _p0 = OT.current_phase(op_name, t_deploy, t_deploy, skill_idx, mastery)
    _tsk = _p0.get("skill_at")
    d_before = (OT.dps_at_time(op_name, t_deploy, t_deploy, e, skill_idx, mastery)
                .get("dps") or 0)
    d_after = None
    if _tsk is not None:
        d_after = (OT.dps_at_time(op_name, _tsk + 0.01, t_deploy, e, skill_idx, mastery)
                   .get("dps") or 0)

    def dps_now(t_local):
        if d_after is not None and _tsk is not None and t_local >= _tsk:
            return d_after
        return d_before

    t, next_hit, log = t_enter, t_enter + interval, []
    last_phase = None
    while t - t_enter < horizon and hp > 0 and ehp > 0:
        d = dps_now(t)
        ph = "技能中" if (d_after is not None and _tsk is not None and t >= _tsk) else "未开技能"
        if ph != last_phase:
            log.append({"t": round(t, 1), "phase": ph, "dps": round(d, 1),
                        "our_hp": round(hp), "enemy_hp": round(ehp)})
            last_phase = ph
        ehp -= d * dt
        if interval and t >= next_hit:
            raw = CB.damage_per_hit(e.get("atk"), st.get("def") or 0, st.get("res") or 0,
                                    e.get("damage_type"))
            h = heal2 if (ratio is not None and hp / hp_max < float(ratio)) else heal1
            net = max(0.0, (raw or 0) - h)          # 天赋自愈 = 每次受伤的等效减伤
            hp -= net
            log.append({"t": round(t, 1), "event": "enemy_hit", "raw": round(raw or 0),
                        "heal": round(h), "net": round(net), "our_hp": round(hp)})
            next_hit += interval
        t += dt
    if ehp <= 0:
        res, td = "enemy_died", t - t_enter
    elif hp <= 0:
        res, td = "blocker_died", t - t_enter
    else:
        res, td = "timeout", None
    return {"op": op_name, "enemy": e.get("name"), "result": res,
            "t_death": round(td, 1) if td is not None else None,
            "our_hp_max": round(hp_max), "our_hp_left": round(max(hp, 0)),
            "enemy_hp_left": round(max(ehp, 0)),
            "talent_heal": (heal1, heal2, ratio),
            "enemy_atk": e.get("atk"), "enemy_interval": interval,
            "our_def": st.get("def"), "our_res": st.get("res"),
            "log": log[:12]}


if __name__ == "__main__":
    import combat_model as CB
    print("=== 天赋自愈解析（数据驱动，非硬编码）===")
    for nm in ("乌尔比安", "斥罪", "山"):
        print("   %-8s -> (常驻, 低血, 血线) = %s" % (nm, talent_self_heal(nm, 2, 60)))
    print("\n=== 手算对账：乌尔比安(无技能) × 寻路者 ===")
    r = duel("enemy_1212_mtrfol_2", "乌尔比安", skill_idx=None)
    print("   结果=%s 用时=%ss 我方剩 %s/%s 敌方剩 %s"
          % (r["result"], r["t_death"], r["our_hp_left"], r["our_hp_max"], r["enemy_hp_left"]))
    print("   天赋自愈=%s 敌方 atk=%s 间隔=%s 我方 def=%s"
          % (r["talent_heal"], r["enemy_atk"], r["enemy_interval"], r["our_def"]))
    for x in r["log"][:6]:
        print("     ", x)
