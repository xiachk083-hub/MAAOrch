"""敌方攻击机制：生存时间 / 阻挡延后 → 把到门时刻从「无阻挡下界」往真实推一档。

⚠️ 机制澄清（指导里那条"敌人进干员攻击范围就停下打"**不成立**）：
    阿米娅的近战敌人**只在被阻挡时停下**（地面干员站在它路径格上），
    不会因为"进了干员的射程"而停。决定"停不停"的是：
      敌人 `motion`（FLY 挡不住）+ `applyWay`（NONE 不攻击）+ 干员 `blockCnt`（0 挡不住）
    ⇒ `applyWay=NONE` / `motion=FLY` 的敌人（如御4：攻 0、NO_DAMAGE、FLY）
      **延后恒为 0** —— 挡位对它毫无影响，它直接飞到门。

数据源（逐项核实过，键名与指导里写的不同）：
    敌人   data/_enemy_db.json -> <Key>.Value[lv].enemyData
             attributes.maxHp / atk / def / magicResistance
             attributes.baseAttackTime   ← 指导里叫 attackInterval，真键是 baseAttackTime
             attributes.attackSpeed      ← 实际间隔 = baseAttackTime × 100 / attackSpeed
             motion（WALK/FLY）、applyWay（MELEE/RANGED/ALL/NONE）、rangeRadius
           data/_enemy_handbook.json -> enemyData[<Key>].damageType   ← 敌人库里是 **null**
             （['PHYSIC'] / ['MAGIC'] / ['NO_DAMAGE']）
    干员   data/_char_table.json -> <char>.phases[<E>].attributesKeyFrames[].data
             maxHp / def / magicResistance / blockCnt
           ⚠️ **只有 2 帧（首级 + 满级）**，不是逐级曲线（541/541 阶段如此）⇒ 中间等级只能
              **线性插值**，而**信赖加成完全不在数据里**。生存时间因此是近似值，一律标注。

伤害公式（Arknights 标准，物理/法术都有 5% 下限）：
    物理: max(atk - def, atk × 0.05)
    法术: max(atk × (1 - res/100), atk × 0.05)
    真伤: atk
"""
import json
import pathlib

PROJ = pathlib.Path(__file__).resolve().parent.parent
ENEMY_DB = PROJ / "data" / "_enemy_db.json"
HANDBOOK = PROJ / "data" / "_enemy_handbook.json"
CHAR_TBL = PROJ / "data" / "_char_table.json"

_cache = {}

UNKNOWN = [
    "仇恨机制：敌人被多个干员围住时打谁，文件里没有 ⇒ 本模型假设它打**阻挡它的那个**",
    "溅射/范围伤害：只有 rangeRadius，没有溅射半径字段 ⇒ 本模型按**单体**算",
    "治疗覆盖：本模型是**无治疗下界**（挡位不会回血）",
    "信赖加成：属性表里没有 ⇒ 干员血量/防御可能被低估",
]


def load_enemy_combat(key, level=0):
    """敌人的攻击面参数。查不到返回 None（**不猜**）。"""
    if "e" not in _cache:
        _cache["e"] = {e["Key"]: e for e in
                       (json.loads(ENEMY_DB.read_text(encoding="utf-8")).get("enemies") or [])
                       if e.get("Key")}
    if "h" not in _cache:
        _cache["h"] = json.loads(HANDBOOK.read_text(encoding="utf-8")).get("enemyData") or {}
    e = _cache["e"].get(key)
    if not e:
        return None
    ent = next((v for v in (e.get("Value") or []) if (v.get("level") or 0) == level), None) \
        or (e.get("Value") or [None])[0]
    if not ent:
        return None
    ed = ent.get("enemyData") or {}
    a = ed.get("attributes") or {}
    g = lambda k: (a.get(k) or {}).get("m_value")
    dt = (_cache["h"].get(key) or {}).get("damageType") or []
    dt = dt[0] if isinstance(dt, list) and dt else dt
    bat, aspd = g("baseAttackTime") or 0, g("attackSpeed") or 100
    motion = (ed.get("motion") or {}).get("m_value")
    apply_way = (ed.get("applyWay") or {}).get("m_value")
    interval = (bat * 100.0 / aspd) if (bat and aspd) else None
    return {"key": key, "name": (ed.get("name") or {}).get("m_value"), "level": ent.get("level"),
            "hp": g("maxHp"), "atk": g("atk"), "def": g("def"), "res": g("magicResistance"),
            "base_attack_time": bat, "attack_speed": aspd, "interval": interval,
            "motion": motion, "apply_way": apply_way,
            "range_radius": (ed.get("rangeRadius") or {}).get("m_value"),
            "damage_type": dt,
            # 能不能被挡 / 会不会打人 —— 这两个开关决定"阻挡延后"是否成立
            "blockable": (motion or "").upper() != "FLY",
            "can_damage": bool(g("atk")) and (str(dt).upper() not in ("NO_DAMAGE", "NONE")),
            "immune": {k: g(k) for k in ("stunImmune", "silenceImmune", "frozenImmune")
                       if (a.get(k) or {}).get("m_defined")}}


def load_operator_stats(name, elite=None, level=None, potential=None):
    """干员的血量/防御/法抗/阻挡数。属性表只有首满两帧 ⇒ 中间等级线性插值，并标注。"""
    if "c" not in _cache:
        ct = json.loads(CHAR_TBL.read_text(encoding="utf-8"))
        _cache["c"] = {v["name"]: v for v in ct.values() if v.get("name")}
    v = _cache["c"].get(name)
    if not v:
        return None
    phs = v.get("phases") or []
    e = min(max(int(elite or 0), 0), len(phs) - 1) if phs else 0
    kf = (phs[e].get("attributesKeyFrames") if phs else None) or []
    if not kf:
        return None
    lv = level if level is not None else kf[0].get("level")
    lo, hi = kf[0], kf[-1]
    l0, l1 = lo.get("level") or 1, hi.get("level") or 1
    f = 0.0 if l1 == l0 else max(0.0, min(1.0, (float(lv) - l0) / (l1 - l0)))
    d0, d1 = lo.get("data") or {}, hi.get("data") or {}

    def lerp(k):
        a, b = d0.get(k), d1.get(k)
        if a is None and b is None:
            return None
        if a is None or b is None:
            return a if a is not None else b
        return a + (b - a) * f
    return {"name": name, "elite": e, "level": lv, "interpolated": l1 != l0 and 0 < f < 1,
            "frames": len(kf), "hp": lerp("maxHp"), "def": lerp("def") or 0,
            "res": lerp("magicResistance") or 0, "block_cnt": d0.get("blockCnt") or 0,
            # 攻击面（我方 DPS 要用；**伤害类型不在这一帧里**，见 damage_kind()）
            "atk": lerp("atk"), "base_attack_time": lerp("baseAttackTime"),
            "attack_speed": lerp("attackSpeed"),
            "note": "属性表只有首满两帧 ⇒ 中间等级为线性插值；信赖加成不在数据里"}


def damage_per_hit(atk, defense=0, res=0, damage_type="PHYSIC"):
    """单次伤害（按 Arknights 公式，物理/法术都有 5% 下限）。"""
    dt = str(damage_type or "PHYSIC").upper()
    if atk is None:
        return None
    if dt in ("NO_DAMAGE", "NONE"):
        return 0.0
    if dt in ("TRUE", "TRUE_DAMAGE", "TRUE_DAMAGE_UNBALANCED"):
        return float(atk)
    if dt.startswith("MAGIC"):
        return max(float(atk) * (1 - float(res or 0) / 100.0), float(atk) * 0.05)
    return max(float(atk) - float(defense or 0), float(atk) * 0.05)      # PHYSIC


def survive_time(op, enemy):
    """干员面对该敌人能扛多久（秒）。无治疗 ⇒ **下界**。

    op/enemy 都接受"已解析好的 dict"或原始属性 dict；键名兼容两套写法
    （`baseAttackTime`/`attackInterval`、`HP`/`hp`、`PHYSIC`/`PHYSICAL`），
    这样指导里手算的那个例子能直接跑。
    """
    g = lambda d, *ks: next((d[k] for k in ks if d.get(k) is not None), None)
    hp = g(op, "hp", "HP", "maxHp")
    df = g(op, "def", "DEF") or 0
    rs = g(op, "res", "RES", "magicResistance") or 0
    atk = g(enemy, "atk", "ATK")
    dt = g(enemy, "damage_type", "damageType") or "PHYSIC"
    itv = g(enemy, "interval", "attackInterval", "baseAttackTime")
    if hp is None or atk is None or not itv:
        return None
    dmg = damage_per_hit(atk, df, rs, dt)
    if not dmg:
        return None                                    # 打不动 ⇒ 无限（返回 None 让调用方标注）
    return (float(hp) / dmg) * float(itv)


def block_delay(enemy, op, our_dps=None):
    """敌人被这个挡位拖住多少秒。

    = min(挡位能扛多久, 敌人多久被打死)。**我们的 DPS 不在数据里** ⇒ our_dps 为 None 时
    只给"挡位能扛多久"这一项，并标 `capped_by_hp=True`（即真实延后 ≤ 此值）。
    """
    e = enemy if isinstance(enemy, dict) else {}
    if not e.get("blockable", True):
        return {"delay": 0.0, "reason": "不可阻挡（FLY）—— 挡位对它无影响"}
    if not e.get("can_damage", True):
        return {"delay": 0.0, "reason": "不造成伤害（NO_DAMAGE/atk=0）—— 挡它也不拖延"}
    if not (op.get("block_cnt") or 0):
        return {"delay": 0.0, "reason": "干员阻挡数为 0，挡不住"}
    st = survive_time(op, e)
    if st is None:
        return {"delay": None, "reason": "生存时间算不出（缺攻/血/间隔）"}
    if our_dps:
        kill = float(e.get("hp") or 0) / float(our_dps)
        return {"delay": round(min(st, kill), 2), "reason": None,
                "by_hp": round(st, 2), "by_kill": round(kill, 2)}
    return {"delay": round(st, 2), "capped_by_hp": True,
            "reason": "只算到'挡位能扛多久'（我方 DPS 无数据）⇒ 真实延后 ≤ 此值"}


def estimate_arrival(enemy_key, op_name, path_steps, speed, elite=None, level=None,
                     our_dps=None, enemy_level=0):
    """到门时刻的**区间**（不是单值）。

    arrival_min = 一路不被打断（= 现有 enemy_speed 的口径）
    arrival_est / arrival_max = 加上"被这个挡位拖住"的延后
    """
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import viz_plan as V
    e = load_enemy_combat(enemy_key, enemy_level)
    if not e:
        return {"reason": "enemy_not_found", "enemy_key": enemy_key}
    box = V.load_box()
    b = box.get(op_name) or {}
    op = load_operator_stats(op_name, elite if elite is not None else b.get("elite"),
                             level if level is not None else b.get("level"))
    walk = path_steps / speed if speed else None
    if walk is None:
        return {"reason": "speed_missing or path_missing"}
    out = {"enemy": e.get("name"), "op": op_name, "walk_time": round(walk, 2),
           "arrival_min": round(walk, 2),
           "assumptions": ["敌人只打阻挡它的那个", "无治疗", "单体伤害", "单次阻挡"],
           "unknowns": UNKNOWN}
    if not op:
        out["reason"] = "operator_not_found"
        return out
    bd = block_delay(e, op, our_dps)
    if bd.get("delay") is None:
        out["reason"] = bd.get("reason")
        return out
    out["block_delay"] = bd["delay"]
    out["arrival_est"] = round(walk + bd["delay"], 2)
    out["arrival_max"] = round(walk + bd["delay"], 2)
    out["block_note"] = bd.get("reason")
    out["op_stats"] = {k: op.get(k) for k in ("elite", "level", "hp", "def", "res", "block_cnt")}
    out["note"] = op.get("note")
    return out


if __name__ == "__main__":
    print("=== 数据源盘点（ex02 四种敌人）===")
    for k in ("enemy_1212_mtrfol_2", "enemy_1216_cansld_2", "enemy_1140_merhd_2",
              "enemy_1017_defdrn"):
        e = load_enemy_combat(k)
        print("  %-22s atk=%-6s 间隔=%-5s 血=%-6s 防=%-6s 抗=%-5s %-8s %-6s 可挡=%-5s 会打人=%s"
              % (e["name"], e["atk"], e["interval"], e["hp"], e["def"], e["res"],
                 e["apply_way"], e["motion"], e["blockable"], e["can_damage"]))
    print("\n=== 手算对账（指导里的例子）===")
    t = survive_time({"hp": 2000, "def": 300, "res": 0},
                     {"atk": 400, "attackInterval": 1.5, "damageType": "PHYSICAL"})
    print("  生存时间 = %.1f 秒（手算：400-300=100 伤害 → 2000/100=20 次 → 20×1.5=30s）" % t)
    assert 29 < t < 31, "对账失败: %s" % t
    print("  smoke OK")


# ————————————————— 我方 DPS（第二步：补 min 的另一半） —————————————————
SKILL_TBL2 = PROJ / "data" / "_skill_table.json"
ART_KINDS = ("法术", "物理", "元素", "真实")           # 描述里可能声明的伤害类型


def damage_kind(name, skill_idx=None):
    """该干员（该技能）造成的伤害类型。返回 (kind, source)。

    分层规则（**描述声明优先**）：
      1) 技能描述里声明了「法术/物理/元素/真实」→ 用它。实测这一步会给出**反直觉但正确**
         的结果：德克萨斯（先锋）S2 → 法术、斥罪（重装）S1 → 法术、凯尔希（医疗）S3 → 真实。
      2) 未声明 → 职业默认：CASTER→法术、MEDIC→治疗(无伤害)、其余→物理。**这是猜测**，
         故 source 标 `default`，调用方要能看出"这条是按职业猜的"。
    干员侧**没有** damageType 字段（属性帧里只有 atk/attackSpeed/baseAttackTime），
    别去找它 —— 这就是为什么要分两层并标注来源。
    """
    if "c2" not in _cache:
        ct = json.loads(CHAR_TBL.read_text(encoding="utf-8"))
        _cache["c2"] = {v["name"]: v for v in ct.values() if v.get("name")}
    if "s2" not in _cache:
        _cache["s2"] = json.loads(SKILL_TBL2.read_text(encoding="utf-8"))
    v = _cache["c2"].get(name)
    if not v:
        return None, "operator_not_found"
    sk = v.get("skills") or []
    if skill_idx and 1 <= int(skill_idx) <= len(sk):
        sid = sk[int(skill_idx) - 1].get("skillId")
        lv = ((_cache["s2"].get(sid) or {}).get("levels") or [])
        desc = (lv[0].get("description") or "") if lv else ""
        for k in ART_KINDS:
            if k in desc:
                return k, "declared"
    prof = v.get("profession")
    if prof == "MEDIC":
        return "治疗", "default"                   # 医疗只治疗（description 实证过）
    return ("法术" if prof == "CASTER" else "物理"), "default"


def skill_atk_scale(name, skill_idx):
    """技能的**持续攻击力倍率**。读不到返回 None（调用方按 1.0 处理）。

    ⚠️ 这里曾按"任何以 `_scale` 结尾的键取最大"实现 —— 结果抓到 **`talent_scale`**
    （天赋倍率，与攻击力无关），把乌尔比安算成 ×2.0（真值 ×2.6）。**按名字模糊匹配键必翻车**，
    现在改成：只认 `atk` 键，且按描述的「攻击力+{atk:0%}」约定 ⇒ **倍率 = 1 + 值**。
    `atk_scale`（「造成相当于攻击力 X% 的伤害」）是**单次伤害**倍率，不是持续倍率，不在此返回。
    """
    if "c2" not in _cache:
        ct = json.loads(CHAR_TBL.read_text(encoding="utf-8"))
        _cache["c2"] = {x["name"]: x for x in ct.values() if x.get("name")}
    if "s2" not in _cache:
        _cache["s2"] = json.loads(SKILL_TBL2.read_text(encoding="utf-8"))
    v = _cache["c2"].get(name)
    if not v:
        return None
    sk = v.get("skills") or []
    if not (skill_idx and 1 <= int(skill_idx) <= len(sk)):
        return None
    sid = sk[int(skill_idx) - 1].get("skillId")
    lv = ((_cache["s2"].get(sid) or {}).get("levels") or [])
    if not lv:
        return None
    bb = {b.get("key"): b.get("value") for b in (lv[-1].get("blackboard") or [])}
    a = bb.get("atk")
    return (1.0 + float(a)) if isinstance(a, (int, float)) else None


def dps(op, enemy, skill_mult=1.0, kind=None):
    """我方对单个敌人的 DPS（伤害/秒）。op/enemy 接受已解析 dict 或原始属性 dict。"""
    g = lambda d, *ks: next((d[k] for k in ks if d.get(k) is not None), None)
    atk = g(op, "atk", "ATK")
    bat, aspd = g(op, "base_attack_time", "baseAttackTime"), g(op, "attack_speed", "attackSpeed")
    if atk is None or bat is None:
        return None
    interval = (bat * 100.0 / (aspd or 100)) or None
    if not interval:
        return None
    kind = kind or g(op, "damage_kind") or "物理"
    if kind == "治疗":
        return 0.0
    a = float(atk) * float(skill_mult or 1.0)
    if kind == "元素":
        dmg = a                                   # 元素不吃防御也不吃法抗
    elif kind == "真实":
        dmg = a
    else:
        dmg = damage_per_hit(a, g(enemy, "def") or 0, g(enemy, "res") or 0,
                             "MAGIC" if kind == "法术" else "PHYSIC")
    return dmg / interval


def effective_hold(enemy_key, op_name, skill_idx=None, elite=None, level=None, enemy_level=0):
    """**有效拖住时间 = min(挡位存活, 击杀耗时)** —— 判漏真正要的那个数。

    返回区间（无技能 ↔ 满技能倍率）+ `limiting_factor` 说明**死于哪个**。
    """
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import viz_plan as V
    e = load_enemy_combat(enemy_key, enemy_level)
    if not e:
        return {"reason": "enemy_not_found"}
    b = (V.load_box().get(op_name) or {})
    op = load_operator_stats(op_name, elite if elite is not None else b.get("elite"),
                             level if level is not None else b.get("level"))
    if not op:
        return {"reason": "operator_not_found", "op": op_name}
    kind, ksrc = damage_kind(op_name, skill_idx)
    op = dict(op, damage_kind=kind)
    out = {"enemy": e.get("name"), "op": op_name, "damage_kind": kind,
           "kind_source": ksrc, "unknowns": UNKNOWN,
           "assumptions": ["敌人只打阻挡它的那个", "无治疗", "单体伤害", "单目标（多目标分摊未建模）"]}
    if kind == "治疗":
        out.update(hold_min=0.0, hold_max=0.0, hold_est=0.0, limiting_factor="不打伤害（治疗）")
        return out
    st = survive_time(op, e)
    out["survive_time"] = round(st, 2) if st is not None else None
    scale = skill_atk_scale(op_name, skill_idx)
    branches = {}
    for lab, mult in (("min", 1.0), ("max", scale if (scale and scale > 1) else 1.0)):
        d = dps(op, e, skill_mult=mult)
        if not d:
            continue
        kill = (e.get("hp") or 0) / d
        hold = min(st, kill) if st is not None else None
        branches[lab] = {"skill_mult": mult, "dps": round(d, 1), "kill_time": round(kill, 2),
                         "hold": round(hold, 2) if hold is not None else None,
                         "limiting": ("挡位先死" if (st is not None and st <= kill) else "敌人先死")}
    if not branches:
        out["reason"] = "dps 算不出（缺 atk/间隔）"
        return out
    out["hold_min"] = branches.get("min", {}).get("hold")
    out["hold_max"] = branches.get("max", {}).get("hold")
    out["hold_est"] = out["hold_min"]
    out["limiting_factor"] = branches.get("min", {}).get("limiting")
    out["detail"] = branches
    out["note"] = ("技能倍率上界 = %s（黑板的 *_scale；**专精读不到**）" % scale) if scale else \
                  "该技能黑板里没有倍率键 ⇒ 上下界相同"
    return out


if __name__ == "__main__":
    print("\n=== 伤害类型分层（描述声明 vs 职业默认）===")
    for nm, si in (("德克萨斯", 2), ("斥罪", 1), ("凯尔希", 3), ("逻各斯", 3), ("白面鸮", 3)):
        k, s = damage_kind(nm, si)
        print("  %-8s 技能%d -> %-4s (%s)" % (nm, si, k, s))
    print("\n=== 手算对账：dps ===")
    # 攻击 800 / 间隔 1.0s / 物理，打防 300 → (800-300)/1.0 = 500
    d = dps({"atk": 800, "baseAttackTime": 1.0, "attackSpeed": 100, "damage_kind": "物理"},
            {"def": 300, "res": 0, "hp": 5000})
    print("  DPS = %.1f（手算：(800-300)/1.0 = 500）" % d)
    assert abs(d - 500) < 1e-6, d
    # 法术：800 × (1-30/100) = 560
    d2 = dps({"atk": 800, "baseAttackTime": 1.0, "attackSpeed": 100, "damage_kind": "法术"},
             {"def": 300, "res": 30, "hp": 5000})
    print("  法术 DPS = %.1f（手算：800×0.7 = 560）" % d2)
    assert abs(d2 - 560) < 1e-6, d2
    print("  smoke OK")
