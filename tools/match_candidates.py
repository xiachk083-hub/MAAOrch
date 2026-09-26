"""box × 作业池 → candidates（缺 0 直跑 / 缺 1-2 半自动替代 / 缺 3+ 进队列）。

box 侧：本地过去 box 无 profession 字段 → 从 data/_char_table.json 按 id join（英文枚举，与 ROLE_MAP 一致）。
作业侧：需求四来源（opers ∪ actions[].name ∪ groups）+ low_squad 分离，见 tools/batch_index.py。
目标机导出（data/operbox_batch/*.json）到位后，本模块直接对它跑；现在先用本地 box 验证链路。
"""
import json, glob, pathlib, sys, collections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools.batch_index import parse_requirements, ROLE_MAP

PROJ = pathlib.Path(__file__).resolve().parent.parent
CT = json.loads((PROJ / "data" / "_char_table.json").read_text(encoding="utf-8"))
PROF = {v["name"]: v.get("profession") for v in CT.values()}


def build_box_index(box_json):
    """容错：调用方可能传原始 dict（导出工具是 {'own':[...],'all_count':N}）。

    迭代 dict 只会拿到键名 → 静默 0 人 → 所有候选都被判「全员缺失」。
    2026-09-16 实测：spot_check 就这么把真实账号的匹配结果算成了 0 人假象。
    """
    if isinstance(box_json, dict):
        for k in ("own", "opers", "chars", "operbox", "data", "box", "operators"):
            v = box_json.get(k)
            if isinstance(v, list) and v:
                box_json = v
                break
    owned_names, owned_roles, operators = set(), {}, {}
    for op in box_json:
        if not isinstance(op, dict) or not op.get("own"):
            continue
        nm = op.get("name")
        if not nm:
            continue
        owned_names.add(nm)
        operators[nm] = {"elite": op.get("elite"), "level": op.get("level"),
                         "rarity": op.get("rarity")}
        prof = op.get("profession") or PROF.get(nm)
        if prof:
            owned_roles.setdefault(prof, []).append(nm)
    return {"owned_names": owned_names, "owned_roles": owned_roles, "operators": operators}


def compute_missing(job, box):
    """硬需求只统计**可验证**的两类：operator（没这个人）+ role（该职业一个都没有）。

    placeholder（非真实干员名，如「奶盾」「先锋」）与 group（组名）→ **软需求**：
    数据层无法验证，是作者留给打手的灵活位，**不能否决作业**。
    """
    missing, soft = [], []
    for req in parse_requirements(job):
        if req["type"] == "operator":
            if req["name"] not in box["owned_names"]:
                missing.append(req["name"])
        elif req["type"] == "role":
            if not box["owned_roles"].get(req["role"]):
                missing.append(f"角色:{req['role']}")
        elif req["type"] == "placeholder":
            soft.append(req.get("display", "?"))
        elif req["type"] == "group":
            soft.append("/".join(req["members"]))
    return missing, soft


def rank_candidates(box, jobs, allow_low_squad=False):
    """exact = **硬需求全满足**（软需求是加分项/待确认项，不是否决项——2026-09-16 用户定）。"""
    out = []
    for j in jobs:
        reqs = parse_requirements(j)
        if len(reqs) < 6 and not allow_low_squad:
            continue
        miss, soft = compute_missing(j, box)
        n = len(miss)
        if n == 0:
            match, ptype = ("exact", None) if not soft else ("partial", "partial_soft")
        elif n <= 2:
            match, ptype = "partial", "partial_hard"
        else:
            match, ptype = "no", None
        out.append({"job_id": pathlib.Path(j["_file"]).stem, "n_missing": n, "n_soft": len(soft),
                    "n_actions": len(j.get("actions") or []),
                    "match": match, "partial_type": ptype, "missing": miss, "soft": soft,
                    "n_reqs": len(reqs),
                    "action": "直跑" if match == "exact" else
                              ("半自动替代" if ptype == "partial_hard" else
                               "半自动确认(占位/组名)" if ptype == "partial_soft" else "进队列")})
    out.sort(key=lambda x: (x["n_missing"], x["n_soft"]))
    return out


def skill_slot_available(elite, skill_num) -> bool:
    """方舟技能槽位解锁：E0→槽1，E1→槽1/2，E2→槽1/2/3。

    2026-09-16 实测：作业要求 skill:2 而账号该干员只有 E0 → MAA 报 Unavailable，
    但那不是「缺干员」——是缺**技能槽位**。不提前判它就只能在 MAA 编队阶段炸。
    """
    return int(skill_num or 1) <= {0: 1, 1: 2, 2: 3}.get(int(elite or 0), 0)


def compute_missing_detailed(job, box):
    """区分「真没有」与「有但练度不够」（2026-09-16 用户定：练度不够算缺）。

    返回 [{name, reason, have, need}]，reason ∈ not_owned / elite_low / level_low / skill_low。
    作业未声明 elite/level/skill 时按无要求处理（MAA copilot 里这三项可选）。
    """
    out = []
    for req in parse_requirements(job):
        if req["type"] != "operator":
            continue
        nm = req["name"]
        if nm not in box["owned_names"]:
            out.append({"name": nm, "reason": "not_owned"})
            continue
        op = (box.get("operators") or {}).get(nm) or {}
        if not skill_slot_available(op.get("elite"), req.get("skill", 1)):
            out.append({"name": nm, "reason": "skill_slot_unavailable",
                        "have_elite": op.get("elite"), "need_skill": req.get("skill", 1)})
            continue
        for key, why in (("elite", "elite_low"), ("level", "level_low"), ("skill", "skill_low")):
            need = req.get(key)
            have = op.get(key)
            if need is not None and have is not None and have < need:
                out.append({"name": nm, "reason": why, "have": have, "need": need})
                break
    return out


def load_jobs():
    jobs = []
    for f in sorted((PROJ / "data" / "copilot").glob("act16side_ex02*/*.json")):
        j = json.loads(f.read_text(encoding="utf-8"))
        j["_file"] = str(f)
        jobs.append(j)
    return jobs


if __name__ == "__main__":
    jobs = load_jobs()
    boxes = {}
    for f in glob.glob(str(PROJ / "data" / "operbox_batch" / "*.json")) or []:
        boxes[pathlib.Path(f).stem] = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
    if not boxes:      # 目标机导出未到位 → 用本地历史 box 先验证链路
        for f in sorted(glob.glob(str(PROJ / "data" / "operbox_pasted_0915*.json"))):
            boxes[pathlib.Path(f).stem] = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
        print("(目标机 box 未导出 → 用本地历史 box 验证链路)")
    print(f"作业池 {len(jobs)} 份 | box {len(boxes)} 份\n")
    for name, raw in boxes.items():
        box = build_box_index(raw)
        cands = rank_candidates(box, jobs)
        dist = collections.Counter(c["match"] for c in cands)
        top = [c for c in cands if c["match"] in ("exact", "partial")][:2]
        if not top and cands:
            c0 = cands[0]
            print(f"    (仍无 exact/partial) 最佳 {c0['job_id']} 缺{c0['n_missing']} → {c0['missing']}")
        print(f"{name}: {len(box['owned_names'])} 人 | exact={dist['exact']} "
              f"partial_soft={sum(1 for c in cands if c['partial_type']=='partial_soft')} "
              f"partial_hard={sum(1 for c in cands if c['partial_type']=='partial_hard')} "
              f"no={dist['no']} | soft合计={sum(c['n_soft'] for c in cands)}")
        for c in top:
            print(f"    → {c['job_id']} [{c['match']}] 需求{c['n_reqs']} 缺{c['n_missing']} "
                  f"{c['missing'][:3]} {c['action']}")
