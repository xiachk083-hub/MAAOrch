"""干员输出模板：输入「干员 + 时刻」→ 输出这一刻的 DPS。

数据驱动（不硬编码"某某暖机 70 秒"）：
    面板   data/_char_table.json   属性帧（atk/baseAttackTime/attackSpeed/maxHp/blockCnt）
    倍率   data/_skill_table.json  技能黑板（按 level = 专精档取）
    时机   作业 JSON 的 actions（Skill 动作）or skill_usage 自动

## 黑板两种倍率形态 —— **不能混用**（实测口径，血泪）

| 形态 | 出现处 | 键 | 读法 |
|---|---|---|---|
| **持续型** | 描述写「攻击力+{atk:0%}」 | `atk` | **倍率 = 1 + 值**（乌尔比安 S2 `atk:1.6` ⇒ ×2.6） |
| **爆发型** | 描述写「造成相当于攻击力{atk_scale:0%}的伤害」 | `atk_scale` | 是**单次伤害倍率**，**不能当持续 DPS 倍率** |

⚠️ 我曾按"任何以 `_scale` 结尾的键取最大"实现，结果抓到 **`talent_scale: 2.0`**（天赋倍率，
与攻击力无关）⇒ 乌尔比安被算成 ×2.0（真值 2.6）。**按名字模糊匹配键必翻车。**

## 攻速：**别假设有**

乌尔比安三个技能的黑板里**都没有攻速键**，天赋也没有 ⇒ 不存在"暖机后攻速 ×1.2"。
有攻速加成的技能会出现在 `attack_speed` / `aspd` 键里；**读不到就是没有，不要套默认值。**

## 未建模（一律标注）
    天赋（乌尔比安「本性的坚守」受伤自愈 80~120 血 ⇒ 实际存活比模型长；
          「血脉的哺养」击杀叠攻 +30×9 ⇒ 实际 DPS 比模型高）
    专精（只看得到倍数区间；用 min/max 两档跑）
    信赖（不在数据里，面板偏低）
"""
import json
import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
_cache = {}

# 持续型倍率的键（描述里是「攻击力+X%」）
SUSTAINED_KEYS = ("atk",)
# 爆发型（描述里是「造成相当于攻击力 X% 的伤害」）——只作参考，不当持续倍率
BURST_KEYS = ("atk_scale",)


def _char_table():
    if "c" not in _cache:
        ct = json.loads((PROJ / "data" / "_char_table.json").read_text(encoding="utf-8"))
        _cache["c"] = {v["name"]: v for v in ct.values() if v.get("name")}
    if "s" not in _cache:
        _cache["s"] = json.loads((PROJ / "data" / "_skill_table.json").read_text(encoding="utf-8"))
    return _cache["c"], _cache["s"]


def skill_mods(name, skill_idx, mastery=0):
    """该干员该技能在指定专精档下的加成。**按键名精确取**，不模糊匹配。

    返回 {ok, held(持续), burst(爆发,仅供参考), hp_mult, block_add, duration,
          sp_cost, init_sp, aspd, note}
    """
    ct, st = _char_table()
    v = ct.get(name)
    if not v:
        return {"ok": False, "reason": "operator_not_found"}
    sk = v.get("skills") or []
    if not (skill_idx and 1 <= int(skill_idx) <= len(sk)):
        return {"ok": False, "reason": "no_skill_declared"}
    sid = sk[int(skill_idx) - 1].get("skillId")
    s = st.get(sid) or {}
    lv = s.get("levels") or []
    if not lv:
        return {"ok": False, "reason": "no_levels", "skill_id": sid}
    l = lv[min(max(int(mastery), 0), len(lv) - 1)]
    bb = {b.get("key"): b.get("value") for b in (l.get("blackboard") or [])}
    atk_add = bb.get("atk")                       # 「攻击力+X%」⇒ 加算，倍率 = 1+X
    held = (1.0 + float(atk_add)) if isinstance(atk_add, (int, float)) else 1.0
    burst = bb.get("atk_scale") if "atk_scale" in bb else None
    aspd = None
    for k in ("attack_speed", "aspd"):
        if isinstance(bb.get(k), (int, float)):
            aspd = 1.0 + float(bb[k])
    hp_add = bb.get("max_hp")
    note = []
    if burst is not None:
        note.append("黑板有 atk_scale=%s：那是**单次伤害**倍率，未计入持续 DPS" % burst)
    if aspd is None:
        note.append("黑板无攻速键 ⇒ 按无攻速加成算（**不套默认值**）")
    return {"ok": True, "skill_id": sid, "skill_name": l.get("name"), "mastery": mastery,
            "held_mult": round(held, 3), "burst_scale": burst,
            "hp_mult": (1.0 + float(hp_add)) if isinstance(hp_add, (int, float)) else 1.0,
            "block_add": int(bb.get("block_cnt") or 0), "aspd_mult": aspd or 1.0,
            "duration": l.get("duration"), "sp_cost": (l.get("spData") or {}).get("spCost"),
            "init_sp": (l.get("spData") or {}).get("initSp"),
            "increment": (l.get("spData") or {}).get("increment"),
            "note": "；".join(note)}


def stats_at(name, elite=None, level=None):
    """面板（线性插值 + 标注）。复用 combat_model，避免第二份实现。

    ⚠️ elite/level 为 None 时**从 box 取**（`operbox_batch/emu2.json` 的 elite/level）。
    这里曾直接传 None ⇒ `load_operator_stats` 回落成 **E0 lv1** ⇒ 面板攻击力被砍到一半以下
    （乌尔比安 E0 739 vs E2 lv38 1421.8）。**和 `elite`/`e` 取错键是同一类坑。**
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import combat_model as CB
    if elite is None or level is None:
        try:
            import viz_plan as V
            b = V.load_box().get(name) or {}
        except Exception:
            b = {}
        elite = b.get("elite") if elite is None else elite
        level = b.get("level") if level is None else level
    return CB.load_operator_stats(name, elite, level)


def skill_activate_time(op_stats, mods, t_deploy, regen=1.0):
    """技能**首次**可用时刻（仅 spType=INCREASE_WITH_TIME）。

    = 部署 + (spCost - initSp) / (increment × spRecoveryPerSec)。
    注意 `spRecoveryPerSec` 取干员属性帧里的值（多数 1.0）。
    """
    if not mods.get("ok"):
        return None
    cost, init = mods.get("sp_cost"), mods.get("init_sp") or 0
    inc = mods.get("increment") or 1.0
    spr = (op_stats or {}).get("sp_recovery") or 1.0
    if cost is None:
        return None
    return t_deploy + max(0.0, (cost - init)) / (inc * spr)


def current_phase(name, t, t_deploy, skill_idx=None, mastery=0, sp_recovery=1.0,
                  elite=None, level=None):
    """t 时刻该干员处于哪个阶段。返回 {phase, mods, hold_mult, hp_mult, aspd_mult}"""
    mods = skill_mods(name, skill_idx, mastery) if skill_idx else {"ok": False}
    st = stats_at(name, elite, level) or {}
    out = {"t": t, "phase": "未开技能", "mods": mods,
           "hold_mult": 1.0, "hp_mult": 1.0, "aspd_mult": 1.0, "skill_at": None}
    if not mods.get("ok"):
        out["note"] = mods.get("reason")
        return out
    st = dict(st, sp_recovery=sp_recovery)
    t_sk = skill_activate_time(st, mods, t_deploy, regen=sp_recovery)
    out["skill_at"] = round(t_sk, 2) if t_sk is not None else None
    if t_sk is None or t < t_sk:
        return out
    out["phase"] = "技能中（%s）" % mods.get("skill_name")
    out["hold_mult"] = mods["held_mult"]
    out["hp_mult"] = mods["hp_mult"]
    out["aspd_mult"] = mods["aspd_mult"]
    return out


def dps_at_time(name, t, t_deploy, enemy, skill_idx=None, mastery=0, damage_kind=None,
                elite=None, level=None):
    """**这一刻**的 DPS。"""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import combat_model as CB
    ph = current_phase(name, t, t_deploy, skill_idx, mastery, elite=elite, level=level)
    st = stats_at(name, elite, level)
    if not st:
        return {"dps": None, "reason": "operator_not_found"}
    kind = damage_kind or (CB.damage_kind(name, skill_idx)[0] if skill_idx else
                           CB.damage_kind(name)[0])
    eff = dict(st, damage_kind=kind,
               atk=(st.get("atk") or 0) * ph["hold_mult"],
               hp=(st.get("hp") or 0) * ph["hp_mult"],
               base_attack_time=(st.get("base_attack_time") or 0) / ph["aspd_mult"])
    d = CB.dps(eff, enemy)
    return {"dps": round(d, 2) if d else d, "phase": ph["phase"], "damage_kind": kind,
            "atk_eff": round(eff["atk"], 1), "skill_at": ph["skill_at"],
            "hold_mult": ph["hold_mult"], "hp_mult": ph["hp_mult"], "aspd_mult": ph["aspd_mult"],
            "mods_note": (ph["mods"] or {}).get("note") if ph["mods"].get("ok") else ph.get("note")}


def fire_network_timeline(enemy_key, shooter, t_enter, t_deploy, skill_idx=None,
                          mastery=0, dt=0.5, horizon=600.0):
    """敌人从 t_enter 进入某干员的火力网，逐秒累加到打死为止。"""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import combat_model as CB
    e = CB.load_enemy_combat(enemy_key)
    if not e:
        return {"reason": "enemy_not_found"}
    hp, t, dmg, log = (e.get("hp") or 0), t_enter, 0.0, []
    while dmg < hp and t - t_enter < horizon:
        cur = dps_at_time(shooter, t, t_deploy, e, skill_idx, mastery)
        d = cur.get("dps") or 0
        if t == t_enter or (log and log[-1]["phase"] != cur["phase"]):
            log.append({"t": round(t, 1), "phase": cur["phase"], "dps": round(d, 1)})
        dmg += d * dt
        t += dt
    killed = dmg >= hp
    return {"enemy": e.get("name"), "hp": hp, "killed": killed,
            "kill_time": round(t - t_enter, 1) if killed else None,
            "reason": None if killed else "timeout（%ds 内没打死）" % horizon,
            "phases": log}


if __name__ == "__main__":
    import combat_model as CB
    E = CB.load_enemy_combat("enemy_1216_cansld_2")          # 撕裂者
    print("=== 乌尔比安 S2 的倍率（对账）===")
    m = skill_mods("乌尔比安", 2, mastery=9)
    for k in ("skill_name", "held_mult", "burst_scale", "aspd_mult", "hp_mult",
              "block_add", "sp_cost", "init_sp", "duration"):
        print("   %-12s = %s" % (k, m.get(k)))
    print("   note:", m.get("note"))
    print("\n=== 未开技能 vs 技能中 DPS ===")
    d0 = dps_at_time("乌尔比安", t=0, t_deploy=0, enemy=E, skill_idx=2, mastery=9)
    d1 = dps_at_time("乌尔比安", t=200, t_deploy=0, enemy=E, skill_idx=2, mastery=9)
    print("   未开技能 T+0   : DPS=%s 相位=%s" % (d0["dps"], d0["phase"]))
    print("   技能中   T+200 : DPS=%s 相位=%s" % (d1["dps"], d1["phase"]))
    ratio = d1["dps"] / d0["dps"]
    print("   比值 = %.3f  ← **不等于** held_mult=%s，这是对的：撕裂者 DEF 1500 让未开技能落在"
          % (ratio, m["held_mult"]))
    print("             5%% 下限区、技能中打穿进入真实伤害区，跨区是阶跃 ⇒ 比值远大于倍率")

    print()
    print("=== 对账（判据只在 DEF=0 时成立）===")
    Z = CB.load_enemy_combat("enemy_1140_merhd_2")       # 战术猎犬pro：DEF=0
    z0 = dps_at_time("乌尔比安", t=0, t_deploy=0, enemy=Z, skill_idx=2, mastery=9)
    z1 = dps_at_time("乌尔比安", t=200, t_deploy=0, enemy=Z, skill_idx=2, mastery=9)
    zr = z1["dps"] / z0["dps"]
    print("   打 DEF=0 的猎犬：DPS %.2f → %.2f，比值 = %.3f" % (z0["dps"], z1["dps"], zr))
    assert abs(zr - m["held_mult"]) < 0.02, (zr, m["held_mult"])
    print("   ⇒ 比值 %.3f == held_mult %s ✓  smoke OK" % (zr, m["held_mult"]))
    print("   ⚠️ DEF>0 时比值必然 > 倍率（减防在加攻之后），DEF 高到跨区时是阶跃 ——")
    print("      所以拿'比值≈倍率'当判据，只在 DEF=0 的目标上成立。")
    print("\n=== 火力网：暖机前后击杀耗时 ===")
    r = fire_network_timeline("enemy_1216_cansld_2", "乌尔比安", 0, 0, skill_idx=2, mastery=9)
    if r.get("killed"):
        print("   击杀耗时 %.1fs" % r["kill_time"])
    else:
        print("   %s" % r.get("reason"))
    for p in r["phases"]:
        print("     T+%-6s %-16s DPS=%s" % (p["t"], p["phase"], p["dps"]))
    print("   击杀耗时 = %s s" % r["kill_time"])
