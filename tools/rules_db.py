"""规则入库：把**修改树里人改出来的变化**聚合成"这一关该怎么打"的规则，供反哺生成消费。

由来（2026-09-16 用户《作业系统 · 总指导》§5.3）：差异就是标注，同一处修正在多少个版本里出现过
（`count`）就是这条规则有多可靠。所以入库只做一件事：**数**。

    knowledge/edits.json        ← 关卡级规则（**不含账号标识与文件路径**；note 是关卡特有打法，按知识保留）
    data/rules/edits_full.json  ← 同一批规则 + sources（哪几个账号的哪一版贡献的），本地留档、可审计

两条口径（别改，改了就出脏数据）：
  1. **只收 `author = human` 的节点**。AI 自己的选配不是"修正"（v1_ai 永远 0 条规则），
     把 AI 的原始输出也当规则数进去，等于把 AI 的偏好当成人的意见。
  2. **按 (stage, type, operator, from, to) 聚合，不按 step**。步数在版本之间会漂移
     （前面插一次撤退，后面全错位），而"把 [5,2] 的凛御银灰 从 Up 改成 Down"这件事
     在哪一版都是同一件事。step 只作为参考值收集（`steps`）。

`from`/`to` 是应用规则的**匹配条件**：反哺时只有作业当前状态等于 `from` 才改 ——
这正是规则的含义（"看到 X 就改成 Y"），也是它安全的唯一理由。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
PROJ = _HERE.parent
sys.path.insert(0, str(_HERE))
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

import workspace as WSP        # noqa: E402

PUBLIC = PROJ / "knowledge" / "edits.json"       # 关卡级、无标识
FULL = PROJ / "data" / "rules" / "edits_full.json"
# 可当规则应用的差异类型（其余只留档：`groups` 不是位置规则，`opers_*` 是编队信号不是打法规则）
RULE_TYPES = ("location", "direction", "costs", "kills", "pre_delay", "operator")
LINEUP_TYPES = ("opers_added", "opers_removed", "opers_skill")


def iter_metas(accounts: list[str] | None = None):
    """遍历所有工作区的版本 meta → (account, activity, code, stage_dir, meta)。"""
    for ac in (accounts or WSP.list_accounts()):
        for act in WSP.list_activities(ac):
            base = WSP.WS / ac / act
            for d in sorted(base.glob("*/")):
                mdir = d / "versions"
                if not mdir.is_dir():
                    continue
                for f in sorted(mdir.glob("*.meta.json")):
                    try:
                        m = json.loads(f.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    yield ac, act, d.name, d, m


def _key(stage: str, c: dict) -> str:
    return json.dumps([stage, c.get("type"), c.get("name") or "",
                       c.get("from"), c.get("to")], ensure_ascii=False, sort_keys=True)


def collect(accounts: list[str] | None = None) -> dict:
    """聚合 → {"rules": {stage: [rule…]}, "lineup": {stage: [信号…]}, "stats": {...}}。

    只有 `type ∈ RULE_TYPES` 的变化进 `rules`；编队类（`opers_added/removed`）进 `lineup`
    （它是有用的信号："这份作业用的人，人给删了"），但不该被当成位置规则去套。
    """
    rules: dict[str, dict] = {}
    lineup: dict[str, dict] = {}
    n_nodes = n_human = n_changes = 0
    for ac, act, code, sd, m in iter_metas(accounts):
        n_nodes += 1
        if m.get("author") != "human":
            continue
        n_human += 1
        stage = m.get("stage_id") or ""
        for c in (m.get("changes") or []):
            n_changes += 1
            typ = c.get("type")
            if typ not in RULE_TYPES and typ not in LINEUP_TYPES:
                continue
            bucket, store = ("rules", rules) if typ in RULE_TYPES else ("lineup", lineup)
            k = _key(stage, c)
            r = store.setdefault(stage, {}).setdefault(k, {
                "stage": stage, "type": typ, "operator": c.get("name") or "",
                "from": c.get("from"), "to": c.get("to"),
                "count": 0, "steps": [], "locations": [], "notes": [], "sources": []})
            r["count"] += 1
            if c.get("step"):
                r["steps"].append(c["step"])
            # 位置上下文：这条规则发生在哪个格上（from 是位置时就是它自己）
            loc = c.get("from") if typ == "location" else None
            if isinstance(loc, list) and loc not in r["locations"]:
                r["locations"].append(loc)
            note = (m.get("note") or "").strip()
            if note and note not in r["notes"]:
                r["notes"].append(note)
            src = "%s/%s/%s#%s" % (ac, act, code, m.get("id"))
            if src not in r["sources"]:
                r["sources"].append(src)

    def _finish(store):
        out = {}
        for stage, items in store.items():
            rows = sorted(items.values(), key=lambda x: (-x["count"], x["type"], x["operator"]))
            for r in rows:                                  # steps 汇总成参考区间
                st = sorted(set(r["steps"]))
                r["steps"] = st
                r["step_median"] = st[len(st) // 2] if st else None
            out[stage] = rows
        return out

    return {"rules": _finish(rules), "lineup": _finish(lineup),
            "stats": {"nodes": n_nodes, "human_nodes": n_human, "changes": n_changes,
                      "stages": len(rules)}}


def save(db: dict) -> dict:
    """写两份：公开档（剥掉 sources/notes 之外的标识）+ 完整档（留档）。

    ⚠️ 公开档**必须**不含账号 id 与文件路径 —— 它是准备进公开仓库的（见记忆「公开仓库隐私原则」）。
    note 是用户写的关卡特有打法描述（"右路敌人从下往上爬"），按知识保留；`sources` 一律剥掉。
    """
    pub = {"_note": "由 tools/rules_db.py 从各账号工作区的修改树聚合；不含账号标识。"
                    "应用口径：作业当前状态 == from 才改成 to。",
           "stats": db["stats"], "rules": {}, "lineup": {}}
    for sec in ("rules", "lineup"):
        for stage, rows in db[sec].items():
            pub[sec][stage] = [{k: v for k, v in r.items() if k != "sources"} for r in rows]
    PUBLIC.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC.write_text(json.dumps(pub, ensure_ascii=False, indent=1), encoding="utf-8")
    FULL.parent.mkdir(parents=True, exist_ok=True)
    FULL.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"public": str(PUBLIC), "full": str(FULL)}


def load(full: bool = False) -> dict:
    p = FULL if full else PUBLIC
    if not p.is_file():
        return {"rules": {}, "lineup": {}, "stats": {}}
    d = json.loads(p.read_text(encoding="utf-8"))
    d.setdefault("rules", {})
    d.setdefault("lineup", {})
    d.setdefault("stats", {})
    return d


def rules_for(stage: str, min_count: int = 2, db: dict | None = None) -> list[dict]:
    """取这一关够格应用的规则（count >= min_count）。**默认 2：只出现一次可能是偶然。**"""
    db = db or load()
    return [r for r in (db.get("rules") or {}).get(stage, []) if r.get("count", 0) >= min_count]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="规则入库：修改树 → knowledge/edits.json")
    ap.add_argument("--account", action="append", default=None, help="只统计这些账号（可重复）")
    ap.add_argument("--stage", default=None, help="只看这一关的规则")
    ap.add_argument("--min-count", type=int, default=1, help="打印时只列 count ≥ N 的规则")
    ap.add_argument("--build", action="store_true", help="聚合并写盘")
    a = ap.parse_args(argv)

    db = collect(a.account)
    st = db["stats"]
    print("修改树：%d 个版本节点（其中人改 %d 个）｜可入库变化 %d 处 ｜ 关卡 %d"
          % (st["nodes"], st["human_nodes"], st["changes"], st["stages"]))
    if a.build:
        out = save(db)
        print("→ 公开档 %s（无账号标识）" % pathlib.Path(out["public"]).relative_to(PROJ))
        print("→ 完整档 %s（含 sources，本地留档）" % pathlib.Path(out["full"]).relative_to(PROJ))
    stages = [a.stage] if a.stage else sorted(db["rules"], key=lambda s: -sum(
        r["count"] for r in db["rules"][s]))
    for stage in stages:
        rows = [r for r in (db["rules"].get(stage) or []) if r["count"] >= a.min_count]
        lin = db["lineup"].get(stage) or []
        if not rows and not lin:
            print("\n%s：还没有可入库的人改（先让人在编辑器里改几版）" % stage)
            continue
        print("\n%s（%d 条规则，≥%d 次）" % (stage, len(rows), a.min_count))
        for r in rows:
            print("   ×%-3d %-10s %-8s %s → %s%s"
                  % (r["count"], r["type"], r["operator"] or "-",
                     json.dumps(r["from"], ensure_ascii=False),
                     json.dumps(r["to"], ensure_ascii=False),
                     ("　# " + r["notes"][0][:40]) if r["notes"] else ""))
        if lin:
            print("   编队信号：" + "；".join(
                "%s %s（×%d）" % (r["type"].replace("opers_", ""), r["operator"], r["count"])
                for r in lin[:6]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
