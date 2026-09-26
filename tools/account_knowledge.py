"""跨账号知识库：这关需要哪些干员、哪个号打过、最好的作业是哪份 —— A 号的经验给 B 号用。

用法:
    python tools/account_knowledge.py                     # 重建 data/cross_account_knowledge.json 并打印
    python tools/account_knowledge.py --stage act54side_05  # 查某一关：谁能跑、谁缺谁
    python tools/account_knowledge.py --share act54side_05  # 把"打通过的那份作业"抄进别的号的池子

数据来源（全是本地文件，**不花理智**）:
    · 各号的作业池 data/copilot/<pool>/*.json  → 作业需要哪些干员
    · 各号的干员箱 data/workspaces/<uid>/box.json → 缺谁
    · 各号的状态 push_state_<uid>.json → 打过没、结果、用的哪份作业
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
SR54 = pathlib.Path(r"C:\Users\xiach\.codex\visualizations\2026\09\16"
                    r"\01a0aaf9-9a07-7931-b068-f38e4064ea0f\sr54")
ACCOUNTS = PROJ / "data" / "accounts.json"
OUT = PROJ / "data" / "cross_account_knowledge.json"


def load(p: pathlib.Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def box_of(acc: dict) -> dict:
    """这个号的干员箱（谁有）。uid 目录优先，退回按名字的老目录。"""
    for cand in (PROJ / "data" / "workspaces" / acc["uid"] / "box.json",
                 PROJ / "data" / "workspaces" / acc["name"] / "box.json"):
        d = load(cand, None)
        if d:
            return {o["name"]: o for o in (d.get("own") or [])}
    return {}


def pool_of(acc: dict) -> pathlib.Path:
    return PROJ / "data" / "copilot" / acc.get("pool", "event54_c")


def build() -> dict:
    accs = load(ACCOUNTS, {}).get("accounts", [])
    know: dict = {}
    job_opers: dict[str, list[str]] = {}      # 作业文件名 → 它需要哪些干员（后面给"最好作业"取用）
    for acc in accs:
        owned = box_of(acc)
        state = load(SR54 / acc.get("state", ""), {}).get("stages", {})
        # 这个号池子里每关的作业（一份/关），记下需要的人和它自己的缺口
        for f in sorted(pool_of(acc).glob("act54side_*.json")):
            job = load(f, {})
            stage = job.get("stage_name")
            if not stage:
                continue
            opers = [o.get("name") for o in (job.get("opers") or []) if o.get("name")]
            job_opers[f.name] = opers
            slot = know.setdefault(stage, {"stage": stage, "required_operators": [],
                                           "tried_by": {}, "best_job": "", "best_account": ""})
            slot["required_operators"] = sorted(set(slot["required_operators"]) | set(opers))
            missing = [o for o in opers if o not in owned]
            slot["tried_by"].setdefault(acc["uid"], {
                "name": acc["name"], "job": f.name, "missing": missing,
                "status": "未打", "reason": "",
            })
        # 状态：这关到底打成什么样（key 是 SR-8/EX-1 这种显示名）
        for key, rec in (state or {}).items():
            sn = None
            try:
                kind, num = key.split("-")
                sn = (f"act54side_ex{int(num):02d}" if kind.upper() == "EX"
                      else f"act54side_{int(num):02d}")
            except Exception:
                continue
            slot = know.setdefault(sn, {"stage": sn, "required_operators": [],
                                        "tried_by": {}, "best_job": "", "best_account": ""})
            who = slot["tried_by"].setdefault(acc["uid"], {"name": acc["name"], "job": "",
                                                           "missing": []})
            who["status"] = rec.get("status", "")
            who["reason"] = rec.get("reason", "")
            if rec.get("job_used"):
                who["job"] = rec["job_used"]
            if rec.get("status") in ("三星", "二星") and not slot["best_job"]:
                slot["best_job"] = rec.get("job_used") or ""
                slot["best_account"] = acc["uid"]
    # "需要干员"以**打通过的那份作业**为准（没有就打通过的就用候选并集，并标注）
    for slot in know.values():                 # 这里 know 还是"按 stage_name 直接索引"的
        best = slot.get("best_job")
        if best and job_opers.get(best):
            slot["required_operators"] = sorted(set(job_opers[best]))
            slot["required_source"] = "best_job"
        else:
            slot["required_source"] = "candidates(union)"
    return {"accounts": {a["uid"]: {"name": a["name"], "client": a["client"],
                                    "pool": a.get("pool")} for a in accs},
            "stages": know}


def main() -> None:
    ap = argparse.ArgumentParser(description="跨账号知识库（不花理智）")
    ap.add_argument("--stage", default="", help="只查这一关（stage_name，如 act54side_05）")
    ap.add_argument("--share", default="", help="把这一关「最好的作业」抄给还没过的号")
    a = ap.parse_args()

    know = build()
    OUT.write_text(json.dumps(know, ensure_ascii=False, indent=1), encoding="utf-8")
    stages = know["stages"]
    accs = load(ACCOUNTS, {}).get("accounts", [])

    if a.stage:
        stages = {k: v for k, v in stages.items() if k == a.stage}
    print(f"知识库 {OUT.name}：{len(know['stages'])} 关（{len(accs)} 个号）")
    for stage, info in sorted(stages.items()):
        print(f"\n{stage} ｜ 需要干员：{'、'.join(info['required_operators']) or '—'}"
              f" ｜ 最好作业：{info['best_job'] or '—'}"
              f"（{info['best_account'] or '—'}）")
        for uid, t in sorted(info["tried_by"].items()):
            miss = f" 缺：{'、'.join(t['missing'])}" if t.get("missing") else ""
            print(f"    {t.get('name', uid):6s} {t.get('status', '未打'):4s}"
                  f" {t.get('reason', '')} {t.get('job', '')}{miss}")

    if a.share:
        info = know["stages"].get(a.share, {})
        best = info.get("best_job")
        if not best:
            sys.exit(f"{a.share} 还没有'打通过'的作业可共享")
        src = SR54  # 作业文件名就是 job_used；在各自的池子里找
        for acc in accs:
            if acc["uid"] == info.get("best_account"):
                continue
            pool = pool_of(acc)
            src_file = None
            for cand in (pool / best, *[pool / f"{a.share}_{best.split('_')[-1]}"]):
                if cand.is_file():
                    src_file = cand
                    break
            if src_file is None:                      # 从"最好的号"的池子里拿
                for other in accs:
                    cand = pool_of(other) / best
                    if cand.is_file():
                        src_file = cand
                        break
            if src_file is None:
                print(f"   ⚠️ 找不到 {best} 的正文，跳过 {acc['name']}")
                continue
            dst = pool / best
            shutil.copyfile(src_file, dst)
            print(f"   抄给 {acc['name']}（{acc['client']}）: {dst.name}")


if __name__ == "__main__":
    main()
