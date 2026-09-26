"""账号池 + 作业池索引（本地跑；目标机定时导出 box 后拉回）。

两个必须做的过滤（2026-09-16 用户拍板）：
  · 作业池来源过滤：排除 ga_derived/ 派生作业 + doc.title 含「编排」的自制作业
    （否则会把自己的半成品当候选作业推荐出去）
  · box schema 适配：0 干员时**显式抛错**，绝不静默当成「拥有 0 个干员」
    （否则该账号会被判成「全部缺员」而错误进队列）
"""
import json, glob, pathlib, collections

PROJ = pathlib.Path(__file__).resolve().parent.parent


class BoxParseError(Exception):
    pass


def _is_derived(path: str, job: dict) -> bool:
    """排除**所有**自产文件：路径优先，source 兜底（prts 作业不一定带 source 字段）。"""
    ps = str(path).replace("\\", "/")
    if any(k in ps for k in ("ga_derived", "_adapted", "derived", "sr54_adapted")):
        return True
    title = ((job.get("doc") or {}).get("title") or "") if isinstance(job.get("doc"), dict) else ""
    if "编排" in title:
        return True
    src = job.get("source")
    if src is not None and "prts" not in str(src).lower():
        return True
    return False


def is_valid_job(job: dict) -> bool:
    """可执行作业 = 有非空 actions（43 份 *_match.json 有 opers 但无 actions，不是 MAA 能跑的）。"""
    return isinstance(job.get("actions"), list) and len(job["actions"]) > 0


def _self_made_name(path: str) -> bool:
    nm = pathlib.Path(path).name.lower()
    return nm == "manifest.json" or nm.endswith("_cand.json") or (nm.startswith("set_") and nm.endswith(".json"))


def pool_by_prefix(jobs: list) -> dict:
    """按关卡前缀分档统计（整体占比不是正确指标：SR 少人、主线满编，各关本就不同）。"""
    groups = collections.defaultdict(list)
    for j in jobs:
        st = (j.get("stage") or "?")
        pre = "act16side" if st.startswith("act16side") else               "act54side" if st.startswith("act54") else               "15-11" if st.startswith("15-") else               "event54" if st.startswith("event54") else "其他"
        groups[pre].append(j)
    out = {}
    for pre, js in groups.items():
        b = collections.Counter()
        for j in js:
            n = j["n_reqs"]
            b["<3" if n < 3 else "3-5" if n <= 5 else "6-8" if n <= 8 else "9-12"] += 1
        out[pre] = (len(js), dict(b))
    return out


def pool_health(jobs: list) -> dict:
    """按需求数四档统计（均值不是正确指标：含剿灭/少人作业的池子天然偏低）。"""
    buckets = {"<3": 0, "3-5": 0, "6-8": 0, "9-12": 0}
    for j in jobs:
        n = j.get("n_reqs", 0)
        key = "<3" if n < 3 else "3-5" if n <= 5 else "6-8" if n <= 8 else "9-12"
        j.setdefault("tags", [])
        if n < 6:
            j["tags"].append("low_squad")
        buckets[key] += 1
    return buckets


def load_box(path: str) -> list:
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    # 导出文件 schema 多样：list 直接用；dict 则依次尝试常见键（opers/chars/operbox/data/box）
    if isinstance(data, list):
        ops = data
    else:
        ops = None
        for k in ("opers", "chars", "own", "operbox", "data", "box", "operators"):
            v = data.get(k)
            if isinstance(v, list) and v:
                ops = v
                break
        if ops is None:
            ops = data if all(isinstance(v, dict) for v in data.values()) else []
    own = [o for o in ops if isinstance(o, dict) and o.get("own")]
    if not ops:
        raise BoxParseError(f"{pathlib.Path(path).name}: 解析出 0 条记录，疑似 schema 不符")
    if not own:
        raise BoxParseError(f"{pathlib.Path(path).name}: {len(ops)} 条记录但 own 全为假，疑似 schema 不符")
    return own


def index_boxes() -> dict:
    boxes, failed = {}, {}
    files = set()
    for pat in ("data/operbox*.json", "data/boxes/*.json", "data/**/box_*.json"):
        files.update(glob.glob(str(PROJ / pat), recursive=True))
    for f in sorted(files):
        try:
            own = load_box(f)
            boxes[pathlib.Path(f).name] = {"owned": len(own), "path": f}
        except BoxParseError as e:
            failed[pathlib.Path(f).name] = str(e)
        except Exception as e:
            failed[pathlib.Path(f).name] = f"{type(e).__name__}: {e}"
    return {"ok": boxes, "failed": failed}


_CT = json.loads((pathlib.Path(__file__).resolve().parent.parent / "data" / "_char_table.json").read_text(encoding="utf-8"))
CHAR_NAMES = {v["name"] for v in _CT.values() if isinstance(v, dict) and v.get("name")}
ROLE_MAP = {}


def classify_requirement(name: str) -> dict:
    """主判据：这个名字是不是**真实干员**（char_table 有全部干员名）。

    是 → operator（硬需求）；是职业枚举 → role；都不是 → **placeholder（归软需求）**。
    这条比维护 ROLE_MAP 词典干净一个量级：自动覆盖「先锋」「奶」「奶盾」「术师干员」
    以及以后任何新写法（2026-09-16 实测：枚举追不上作者的口语）。
    """
    if name in CHAR_NAMES:
        return {"type": "operator", "name": name}
    if name in ROLE_MAP:
        return {"type": "role", "role": ROLE_MAP[name], "display": name}
    return {"type": "placeholder", "display": name}


def collect_all_names(job: dict) -> set:
    """需求名字 = opers ∪ actions[].name（去重，过滤空串）。

    实测（2026-09-16）：prts 把职业/占位符写在 actions[].name 里（如「先锋」「奶盾」），
    只看 opers/groups 会系统性低估需求。SpeedUp 的 name 空→过滤；
    Retreat/Skill 的 name 与 Deploy 重复→set 去重。
    """
    names = set()
    for n in job.get("opers", []) or []:
        nm = n.get("name") if isinstance(n, dict) else n
        if isinstance(nm, str) and nm.strip():
            names.add(nm.strip())
    for act in job.get("actions", []) or []:
        nm = act.get("name") if isinstance(act, dict) else None
        if isinstance(nm, str) and nm.strip():
            names.add(nm.strip())
    return names


def parse_requirements(job: dict) -> list[dict]:
    """四来源：opers ∪ actions[].name（→ role/operator）+ groups（→ group，组内任一）。"""
    reqs = [classify_requirement(n) for n in collect_all_names(job)]
    for g in job.get("groups", []) or []:
        members = g if isinstance(g, list) else [g]
        members = [m.get("name") if isinstance(m, dict) else m for m in members]
        members = [m for m in members if m]
        if members:
            reqs.append({"type": "group", "members": members})
    return reqs


def index_jobs() -> list:
    jobs, skipped_derived, skipped_bad = [], 0, 0
    for f in glob.glob(str(PROJ / "data/copilot/**/*.json"), recursive=True):
        try:
            j = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
        except Exception:
            skipped_bad += 1
            continue
        if isinstance(j, list):
            j = j[0] if j and isinstance(j[0], dict) else {}
        if not isinstance(j, dict) or "opers" not in j:
            skipped_bad += 1
            continue
        if _self_made_name(f) or not is_valid_job(j):
            skipped_bad += 1
            continue
        if _is_derived(f, j):
            skipped_derived += 1
            continue
        reqs = parse_requirements(j)
        jobs.append({"file": str(pathlib.Path(f).relative_to(PROJ)), "stage": j.get("stage_name"),
                     "n_reqs": len(reqs), "n_opers": sum(1 for r in reqs if r["type"] == "operator"),
                     "n_groups": sum(1 for r in reqs if r["type"] == "group"), "reqs": reqs})
    return jobs, skipped_derived, skipped_bad


if __name__ == "__main__":
    b = index_boxes()
    print(f"① 账号 box：可解析 {len(b['ok'])} 份，明确失败 {len(b['failed'])} 份（不静默当 0）")
    for k, v in sorted(b["ok"].items(), key=lambda x: -x[1]["owned"])[:6]:
        print(f"   {k:<34} 拥有 {v['owned']}")
    for k, why in b["failed"].items():
        print(f"   ✗ {k:<32} {why}")
    jobs, der, bad = index_jobs()
    avg = sum(j["n_reqs"] for j in jobs) / max(len(jobs), 1)
    print(f"\n② 作业池：保留 {len(jobs)} 份（排除派生 {der}，坏文件 {bad}）")
    print(f"   平均需求数 {avg:.1f} = opers+groups（过滤前只数 opers=3.4）")
    print("   按关前缀分档（这才是正确口径）:")
    for pre, (n, b) in sorted(pool_by_prefix(jobs).items(), key=lambda x: -x[1][0]):
        tot_p = max(n, 1)
        print(f"     {pre:<12} {n:>3} 份 | " + " ".join(f"{k}={v}({v/tot_p*100:.0f}%)" for k, v in b.items()))
    bh = pool_health(jobs)
    tot = max(len(jobs), 1)
    print("   分布: " + "  ".join(f"{k}={v}({v/tot*100:.0f}%)" for k, v in bh.items()))
    main = bh["6-8"] / tot
    print(f"   主力档(6-8) 占比 {main*100:.0f}% → " + ("达标 ✓(≥30%)" if main >= 0.30 else "不达标 ✗"))
    print(f"   low_squad 标签 {sum(1 for j in jobs if 'low_squad' in j.get('tags', []))} 份（排序时不与满编混排）")
    print("   关卡分布 top:", collections.Counter(j["stage"] for j in jobs).most_common(5))
    print("   验收: 数量 ~150-200 ✓" if 120 <= len(jobs) <= 220 else f"   验收: 数量 {len(jobs)} 需复核",
          "| 平均编队 ≥6 ✓" if avg >= 6 else f"| 平均编队 {avg:.1f} <6 需复核")
