"""反哺生成：把入库的规则**应用**回一份作业，产出下一版（闭环的最后一环）。

由来（2026-09-16 用户《作业系统 · 总指导》§5.4）。链子是这样闭上的：

    AI 出 v1 → 人改出 v2/v3（节点里带 changes）→ rules_db 数出规则（count）
      → 这里把规则套回最新版 → 落成 v4_ai（author=ai，parent=v3_human）
      → 人接着改 v5_human …… count 只会越来越准

**应用规则的安全性来自匹配条件**：每条规则记的是"看到 `from` 就改成 `to`"，
所以只有作业当前状态**正好等于 `from`** 才动手。这条不成立就不动 —— 宁可少改，
也不要把一套别的号的打法硬套过来（那正是"AI 生成"最容易犯的错）。

`--llm` 是可选精修：把作业摘要 + 人写过的备注当上下文交给 LLM，让它给一版改法。
**默认不开**：它有 token 成本、且输出不可复现；确定性规则跑出来的版本才是可复算的基线。
"""
from __future__ import annotations

import argparse
import copy
import json
import pathlib
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
PROJ = _HERE.parent
sys.path.insert(0, str(_HERE))
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

import rules_db as RDB         # noqa: E402
import versions as VSN         # noqa: E402
import workspace as WSP        # noqa: E402


# ── 应用规则 ──

def _deploys(job: dict) -> list[dict]:
    return [a for a in (job.get("actions") or [])
            if isinstance(a, dict) and a.get("type") in ("Deploy", "部署")]


def _same(a, b) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        return [tuple(x) if isinstance(x, list) else x for x in a] == \
               [tuple(x) if isinstance(x, list) else x for x in b]
    return a == b


def apply_rules(job: dict, rules: list[dict], box: dict | None = None) -> tuple[dict, list[dict]]:
    """把规则套到作业上 → (新作业, 应用日志)。**不满足匹配条件的规则一律不套**。

    日志逐条说明 `applied` / `skipped_reason`，所以"为什么这版是这样"永远查得回来。
    规则里 `operator`（换人）只在 box 里有那个人时才套 —— 没练过的人换上去等于写个跑不了的轴。
    """
    out = copy.deepcopy(job)
    log = []
    for r in rules:
        typ, op = r.get("type"), (r.get("operator") or "")
        frm, to = r.get("from"), r.get("to")
        hit = None
        for a in _deploys(out):
            if typ in ("location", "direction", "costs", "kills", "pre_delay"):
                if op and a.get("name") != op:
                    continue
                cur = a.get(typ if typ != "pre_delay" else "preDelay")
                if cur is None and typ == "pre_delay":
                    cur = a.get("pre_delay")
                if _same(cur, frm):
                    hit = a
                    break
            elif typ == "operator":
                # job_diff 对换人给的 operator 是**被换掉的那个名字**，to 形如 "Deploy 新名字"
                if a.get("name") == op:
                    hit = a
                    break
        if not hit:
            log.append(dict(r, applied=False, skipped_reason="作业里没有处于 from 状态的对象"))
            continue
        if typ == "operator":
            kind, _, newname = str(to or "").partition(" ")
            if kind != "Deploy" or not newname:
                log.append(dict(r, applied=False,
                                skipped_reason="规则不是换人（to=%s），不在位置/朝向/换人范围内" % to))
                continue
            if box is not None and newname not in box:
                log.append(dict(r, applied=False, skipped_reason="box 里没有 %s" % newname))
                continue
            hit["name"] = newname
        elif typ == "direction":
            hit["direction"] = to
        elif typ == "location":
            hit["location"] = list(to) if isinstance(to, (list, tuple)) else to
        elif typ == "pre_delay":
            hit["pre_delay"] = to
        else:
            hit[typ] = to
        log.append(dict(r, applied=True))
    return out, log


# ── 选基版 + 落新版 ──

def pick_base(sd, want: str | None = None, prefer: str = "root") -> tuple[dict, str]:
    """反哺的基版。**默认 = AI 的根版本（`v1_ai`）**，不是最新的人改版。

    为什么（2026-09-16 沙盒实测撞出来的）：规则是人**改前**的状态 → 改后；把它套到
    "人已经改好的最新版"上，`from` 必然不匹配 —— 于是"反哺套上 0 条"，看起来像闭环没合上，
    其实只是基版选错了对象。反哺的用途是**下次生成自动修正**（用户验收口径）：
    拿 AI 的基线（或一个新账号的 AI 选配）套规则，产出的版本才是"AI 学到后的下一次出手"。

    没有根版本时才退：最新人改版 → cleared.json → 第一份候选；**返回的 id 一并给出**，
    不让"基版是哪一份"变成隐含假设。`prefer="latest"` 可显式要旧行为。
    """
    if want:
        p = VSN.version_path(sd, want)
        return json.loads(p.read_text(encoding="utf-8")), want
    tree = VSN.load_tree(sd)
    roots = [m for m in tree if not m.get("parent")]
    if prefer == "latest":
        humans = [m for m in tree if m.get("author") == "human"]
        pick = (humans[-1] if humans else (tree[-1] if tree else None))
    else:
        ai_roots = [m for m in roots if m.get("author") == "ai"]
        pick = (ai_roots[0] if ai_roots else (roots[0] if roots else (tree[0] if tree else None)))
    if pick:
        return json.loads(VSN.version_path(sd, pick["id"]).read_text(encoding="utf-8")), pick["id"]
    cl = sd / "cleared.json"
    if cl.is_file():
        return json.loads(cl.read_text(encoding="utf-8")), "cleared.json"
    js = sorted((sd / "jobs").glob("*.json"))
    if not js:
        raise SystemExit("这一关没有任何基版可用（versions/ cleared.json jobs/ 都是空的）")
    return json.loads(js[0].read_text(encoding="utf-8")), "jobs/%s" % js[0].name


def evaluate(job: dict, stage: str, box_path) -> dict:
    """复用编辑器那套判据（冲突 + 每条出怪的判漏）评一版作业 —— **不另写一份判定逻辑**。

    做法是把作业写成一个临时文件，走 `viz_plan.load_jobs → build_payload`，读它算出来的
    `warns` 与 `verd`（已挡/可打/存疑）。这样"编辑器里看到的"和"反哺报告的"永远同一个口径。
    """
    import viz_plan as V
    prev = V.BOX_PATH
    f = None
    try:
        V.BOX_PATH = pathlib.Path(box_path) if box_path else prev
        # 临时文件放系统临时目录：**不在工作区里留 scratch**（那儿只该有版本与人的产出）
        fd, name = tempfile.mkstemp(prefix="ws_eval_", suffix=".json")
        import os
        os.close(fd)
        f = pathlib.Path(name)
        f.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        jobs, skipped = V.load_jobs([f], V.professions(), stage)
        if not jobs:
            return {"ok": False, "error": "作业没有可渲染的动作"}
        data = V.build_payload(stage, V.level(stage), jobs, box_path=box_path)
        jb = data["jobs"][0]
        verd = jb.get("verd") or []
        cnt = {"已挡": 0, "可打": 0, "存疑": 0}
        for v in verd:
            if v and v.get("v") in cnt:
                cnt[v["v"]] += 1
        return {"ok": True, "warns": jb.get("warns") or [], "verdicts": cnt,
                "waves": len(verd), "dts": jb.get("dts") or [],
                "duels": [d.get("duel") for d in (jb.get("dts") or []) if d.get("duel")]}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    finally:
        V.BOX_PATH = prev
        try:
            if f:
                f.unlink()
        except Exception:
            pass


def feedback_generate(account: str, activity: str, key: str, min_count: int = 2,
                      base: str | None = None, from_rules: str | None = None,
                      dry_run: bool = False, llm: bool = False,
                      base_prefer: str = "root") -> dict:
    """反哺生成一版：规则套基版 → 落 `vN_ai`（author=ai）+ 评估报告。"""
    sd = WSP.find_stage_dir(account, activity, key)
    if not sd:
        raise SystemExit("工作区里没有这一关：%s / %s / %s" % (account, activity, key))
    st = json.loads((sd / "stage.json").read_text(encoding="utf-8"))
    stage = st.get("stage_id")
    box_file = WSP.box_path(account)
    box = WSP.load_account_box(account)[0]["owned_names"] if box_file else None

    db = RDB.load(full=True)
    if from_rules:
        rules = [r for r in ((json.loads(pathlib.Path(from_rules).read_text(encoding="utf-8"))
                              .get("rules") or {}).get(stage) or [])]
    else:
        rules = RDB.rules_for(stage, min_count, db)
    base_job, base_id = pick_base(sd, base, base_prefer)
    if not rules:
        return {"ok": False, "error": "这一关没有 count>=%d 的规则（先让人改几版）" % min_count,
                "base": base_id, "stage": stage}

    new_job, log = apply_rules(base_job, rules, box)
    applied = [x for x in log if x["applied"]]
    rep = {"stage": stage, "base": base_id, "n_rules": len(rules), "n_applied": len(applied),
           "log": log}
    if not applied:
        rep.update({"ok": False, "written": None,
                    "error": "规则都匹配不上这一版（作业当前状态与规则的 from 不符）——不改，避免乱套"})
        return rep

    note = "反哺：套用 %d 条规则（count≥%d，基版 %s）" % (len(applied), min_count, base_id)
    if llm:
        refined, llm_info = llm_refine(new_job, st, rules)
        rep["llm"] = llm_info
        if refined is not None:
            new_job = refined
            note += " + LLM 精修"
    # 规则应用后位置变了，冲突要重算一份（否则带进去的还是基版的结论）
    ev = evaluate(new_job, stage, box_file)
    rep["eval"] = ev
    if dry_run:
        rep["ok"] = True
        rep["written"] = None
        return rep
    # 基版可能是版本 id（→ parent）、也可能是 cleared.json / jobs/x.json（→ base_src）
    is_ver = bool(base_id) and not base_id.endswith(".json")
    node = VSN.add_version(sd, new_job, author="ai",
                           parent=base_id if is_ver else None,
                           base_src="" if is_ver else base_id,
                           note=note, stage_id=stage, account=account,
                           activity=activity, code=st.get("code") or "", quiet=True)
    WSP.write_activity_index(account, activity)
    rep.update({"ok": True, "written": node["id"], "file": node["file"],
                "changes": node["changes"], "n_changes": node["n_changes"]})
    return rep


# ── LLM 精修（可选，默认关）──

def llm_refine(job: dict, st: dict, rules: list[dict]):
    """把作业 + 规则 + 人写过的备注交给 LLM，要一版改法（JSON patch）。

    **返回 (新作业 | None, 说明)** —— 任何一步不成立都返回 None 并说明原因，绝不半途改一半。
    校验只认"动作数组里的 Deploy 字段"（location/direction/name），别的键一律忽略：
    让模型直接改整份作业 JSON，它会在无关字段上编东西（我们只信这几列）。
    """
    try:
        from services.ai_agent.llm import LLM, _extract_json
    except Exception as e:
        return None, {"ok": False, "error": "LLM 模块不可用：%s" % e}
    try:
        deploys = [{"i": i, "name": a.get("name"), "loc": a.get("location"),
                    "dir": a.get("direction")}
                   for i, a in enumerate(_deploys(job))]
        ctx = [r.get("notes", [""])[0] for r in rules if r.get("notes")][:12]
        user = ("关卡「%s %s」当前部署：\n%s\n\n人改过的地方（原话）：\n%s\n\n"
                "只输出 JSON：{\"deploys\":[{\"i\":序号,\"loc\":[x,y],\"dir\":\"Up|Down|Left|Right\",\"why\":\"一句\"}]}"
                "，顶多改 4 条；不需要改就输出 {\"deploys\":[]}。"
                % (st.get("code"), st.get("name"), json.dumps(deploys, ensure_ascii=False),
                   "\n".join("- " + c for c in ctx)))
        r = LLM().chat("你是方舟排轴助手，只做小步修正，不重排整份轴。", user, temperature=0.2)
        if not r.get("ok"):
            return None, {"ok": False, "error": r.get("error")}
        patch = _extract_json(r.get("text") or "")
        if not isinstance(patch, dict) or "deploys" not in patch:
            return None, {"ok": False, "error": "返回不是约定 JSON", "raw": (r.get("text") or "")[:200]}
        out = copy.deepcopy(job)
        dep = _deploys(out)
        n = 0
        for ch in (patch.get("deploys") or [])[:4]:
            try:
                a = dep[int(ch["i"])]
            except Exception:
                continue
            if isinstance(ch.get("loc"), list) and len(ch["loc"]) == 2:
                a["location"] = ch["loc"]
            if ch.get("dir") in ("Up", "Down", "Left", "Right"):
                a["direction"] = ch["dir"]
            n += 1
        return (out if n else None), {"ok": True, "n_changes": n, "why": patch.get("deploys")}
    except Exception as e:
        return None, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="反哺生成：规则套回最新版 → 落 vN_ai")
    ap.add_argument("account")
    ap.add_argument("activity")
    ap.add_argument("stage", nargs="?", default=None, help="关卡（代号或 stageId）；不给=整个活动")
    ap.add_argument("--min-count", type=int, default=2, help="只用出现次数 ≥ N 的规则（默认 2）")
    ap.add_argument("--base", default=None, help="基版（版本 id，默认 AI 根版本 v1_ai）")
    ap.add_argument("--from-latest", action="store_true",
                    help="改用最新的人改版当基版（默认用 AI 根版本：规则就是来纠正它的）")
    ap.add_argument("--dry-run", action="store_true", help="只打印会改什么，不落盘")
    ap.add_argument("--llm", action="store_true", help="额外让 LLM 精修一版（有 token 成本）")
    a = ap.parse_args(argv)

    stages = ([a.stage] if a.stage else
              [d.name for d in sorted((WSP.WS / a.account / a.activity).glob("*/"))])
    if not stages:
        raise SystemExit("工作区里没有这个活动：%s / %s" % (a.account, a.activity))
    if a.min_count < 2:
        print("! --min-count %d：只出现一次的规则很可能只是某一次尝试（不同分支/账号的看法也可能"
              "互相冲突）。默认是 2；这里按你指定的来，后果自负。" % a.min_count)
    n_ok = 0
    for key in stages:
        try:
            rep = feedback_generate(a.account, a.activity, key, a.min_count, a.base,
                                    dry_run=a.dry_run, llm=a.llm,
                                    base_prefer="latest" if a.from_latest else "root")
        except SystemExit as e:
            print("  %-10s %s" % (key, e))
            continue
        tag = "落成 %s" % rep.get("written") if rep.get("written") else (
            "（dry-run）" if a.dry_run and rep.get("ok") else "跳过")
        print("  %-10s 规则 %2d ｜ 套上 %2d ｜ %s" % (key, rep.get("n_rules", 0),
                                                    rep.get("n_applied", 0), tag))
        for x in (rep.get("log") or []):
            if x["applied"]:
                print("       ✓ %s %s → %s（count %d，来源 %d）"
                      % (x["type"], json.dumps(x.get("from"), ensure_ascii=False),
                         json.dumps(x.get("to"), ensure_ascii=False), x["count"],
                         len(x.get("sources") or [])))
        if rep.get("llm"):
            print("       LLM: %s" % json.dumps(rep["llm"], ensure_ascii=False)[:140])
        ev = rep.get("eval") or {}
        if ev.get("ok"):
            v = ev["verdicts"]
            print("       评估：判漏 已挡%d 可打%d 存疑%d ｜ 冲突 %s"
                  % (v["已挡"], v["可打"], v["存疑"], "；".join(ev["warns"]) or "无"))
        elif ev.get("error"):
            print("       评估失败：%s" % ev["error"])
        if rep.get("error"):
            print("       %s" % rep["error"])
        n_ok += 1 if rep.get("written") else 0
    print("合计落版 %d/%d" % (n_ok, len(stages)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
