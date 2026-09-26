"""AI 选配质量体检：**这一关的 `v1_ai` 是"能改"还是"得重做"**（供人决定下一步）。

由来（2026-09-16 用户）："改一版之前先看 AI 生成的 v1_ai 质量 —— 能用，只是细节不对 → 改一版、
闭环转起来；不能用，缺关键位或朝向全错 → 先修生成端。这个判断只有你能做，因为你知道这关该怎么打。"

判断本身需要领域知识，但**原料可以客观化**，这个脚本把原料摆出来：

    ① 选配来源与匹配结论   哪份候选、缺不缺人/练度（匹配引擎的结论）
    ② 冲突                 位置重叠 / 装置无人（`viz_plan.conflicts` 同一实现）
    ③ 判漏                 已挡 / 可打 / 存疑（`build_payload` 的 verd，**悲观看漏**）
    ④ **必要位覆盖**        对照 `data/stage_logic/<stage>.json`：这一关必须站人的格，
                            `v1_ai` 盖住了几个（站位在位上 or 射程覆盖）；缺的列出来
    ⑤ 一句话评级            差不多能用 / 要补位 / 缺人

用法:
    python tools/v1_quality.py 9036 act54side            # 整个活动（按"最能用"排序）
    python tools/v1_quality.py 9036 act54side SR-1 --json
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

import stage_logic as SL       # noqa: E402
import versions as VSN         # noqa: E402
import viz_plan as VP          # noqa: E402
import workspace as WSP        # noqa: E402


def _v1_job(sd: pathlib.Path) -> tuple[dict | None, dict | None]:
    """这一关的 AI 根版本（v1_ai）→ (job, meta)。没有就返回 (None, None)。"""
    roots = [m for m in VSN.load_tree(sd) if not m.get("parent") and m.get("author") == "ai"]
    if not roots:
        return None, None
    p = sd / VSN.V_DIR / roots[0]["file"]
    if not p.is_file():
        return None, None
    return json.loads(p.read_text(encoding="utf-8")), roots[0]


def _deploys(job: dict) -> list[dict]:
    return [a for a in (job.get("actions") or [])
            if isinstance(a, dict) and a.get("type") in ("Deploy", "部署") and a.get("location")]


def coverage_of_logic(job: dict, logic: dict, stage: str, box_path) -> dict:
    """必要位被这份作业盖住了几个：站位在那一格 **或** 射程覆盖那一格。

    `range_cells` 与编辑器画射程是同一实现（Python 侧唯一实现），所以这里说"覆盖"就是
    页面上看到的那片绿。**不含"时间上赶不赶得上"** —— 那是判漏（③）管的事，别混。
    """
    n2t, rng, ops = VP.ranges_payload([job], box_path)
    W_, H_, key, bt, routes, waves = VP.level(stage)
    dep = _deploys(job)
    hit, miss = [], []
    for pos in logic.get("necessary_positions") or []:
        cell = tuple(pos["grid"])
        by_stand = any(tuple(a["location"]) == cell for a in dep)
        by_rng = False
        if not by_stand:
            for a in dep:
                if cell in VP.range_cells(a, n2t, rng, ops, W_, H_):
                    by_rng = True
                    break
        rec = {"grid": list(cell), "function": pos.get("function"),
               "function_label": pos.get("function_label"),
               "must_be_ready_by": pos.get("must_be_ready_by"),
               "coverage": pos.get("coverage"),
               "how": "站位" if by_stand else ("射程覆盖" if by_rng else None),
               "who": next((a.get("name") for a in dep if tuple(a["location"]) == cell), None)}
        (hit if (by_stand or by_rng) else miss).append(rec)
    return {"n": len(logic.get("necessary_positions") or []), "n_hit": len(hit),
            "hit": hit, "miss": miss, "sample_size": logic.get("sample_size"),
            "n_func_guessed": logic.get("n_func_guessed")}


def grade(job: dict, match: dict | None, verdicts: dict, warns: list, cov: dict | None) -> dict:
    """一句话评级。**只按客观信号给**，不给"能不能打过"的结论（那只有实跑能回答）。"""
    reasons = []
    n_miss = sum(verdicts.values()) and None
    if match and match.get("n_missing"):
        reasons.append("缺 %s" % "、".join(match.get("missing") or []))
    if warns:
        reasons.append("有冲突")
    if cov:
        if cov["n"] and cov["n_hit"] < cov["n"]:
            reasons.append("必要位缺 %d/%d" % (cov["n"] - cov["n_hit"], cov["n"]))
    if verdicts.get("存疑"):
        reasons.append("存疑 %d 条出怪" % verdicts["存疑"])
    nm = (match or {}).get("n_missing") or 0
    if nm >= 2:
        g = "缺人：这份不是这个号的作业（缺 %d 人）" % nm      # 硬伤：编队对不上，谈不上改
    elif nm == 1:
        g = "缺 1 人：借助战/替代能补（不一定是废弃项）"        # 1 人常靠助战解决，别一刀切说死
    elif cov and cov["n"] and cov["n_hit"] < cov["n"]:
        g = "要补位：必要位没站全"
    elif warns:
        g = "有冲突：先挪/补装置覆盖"
    elif cov and cov["n_hit"] == cov["n"] and (match is None or not match.get("n_missing")):
        g = "差不多能用：位置齐、无冲突（细节待改）"
    else:
        g = "无法判断：缺关卡逻辑（先跑 stage_logic）"
    return {"grade": g, "reasons": reasons}


def _match_of(sd: pathlib.Path, meta: dict | None) -> dict | None:
    """v1_ai 的来源候选在 manifest 里的匹配结论。

    ⚠️ manifest 的 `candidates` 是**全部排名行**（没有 `saved_as`，那是"挑进来的"才有的键）——
    按 `saved_as` 找只会得到 None（实测第一版就全列成 `?`）。按**文件名/数字 id** 认领。
    """
    mf = sd / "manifest.json"
    if not mf.is_file():
        return None
    try:
        rows = json.loads(mf.read_text(encoding="utf-8")).get("candidates") or []
    except Exception:
        return None
    base = ((meta or {}).get("base_src") or "").rsplit("/", 1)[-1]
    if not base:
        return None
    stem = base[:-5] if base.endswith(".json") else base
    import re as _re
    m = _re.search(r"(\d{4,7})$", stem)
    want_id = m.group(1) if m else None
    for r in rows:
        f = (r.get("file") or r.get("path") or "").rsplit("/", 1)[-1]
        if f == base or (want_id and str(r.get("id")) == want_id):
            return {"id": r.get("id"), "match": r.get("match"), "n_missing": r.get("n_missing"),
                    "missing": r.get("missing"), "n_detail": r.get("n_detail"),
                    "action": r.get("action")}
    return None


def check_stage(account: str, activity: str, key: str) -> dict:
    sd = WSP.find_stage_dir(account, activity, key)
    if not sd:
        return {"ok": False, "key": key, "error": "工作区里没有这一关"}
    st = json.loads((sd / "stage.json").read_text(encoding="utf-8"))
    stage = st.get("stage_id")
    job, meta = _v1_job(sd)
    if not job:
        return {"ok": False, "key": key, "stage_id": stage, "code": st.get("code"),
                "error": "没有 v1_ai（先重生成这一关）"}
    box_path = WSP.box_path(account)
    # ② 冲突 + ③ 判漏：走编辑器那套（写临时文件 → load_jobs → build_payload），不另写判定
    import tempfile, os
    prev = VP.BOX_PATH
    verdicts, warns, err = {"已挡": 0, "可打": 0, "存疑": 0}, [], None
    try:
        if box_path:
            VP.BOX_PATH = pathlib.Path(box_path)
        fd, name = tempfile.mkstemp(prefix="v1q_", suffix=".json")
        os.close(fd)
        tmp = pathlib.Path(name)
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        jobs, _ = VP.load_jobs([tmp], VP.professions(), stage)
        if jobs:
            data = VP.build_payload(stage, VP.level(stage), jobs, box_path=box_path)
            jb = data["jobs"][0]
            warns = jb.get("warns") or []
            for v in (jb.get("verd") or []):
                if v and v.get("v") in verdicts:
                    verdicts[v["v"]] += 1
        tmp.unlink()
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
    finally:
        VP.BOX_PATH = prev
    # ④ 必要位覆盖
    logic = SL.load(stage)
    cov = coverage_of_logic(job, logic, stage, box_path) if logic else None
    match = _match_of(sd, meta)
    gr = grade(job, match, verdicts, warns, cov)
    return {"ok": True, "key": key, "code": st.get("code"), "name": st.get("name"), "stage_id": stage,
            "n_actions": len(job.get("actions") or []), "n_deploy": len(_deploys(job)),
            "match": match, "warns": warns, "verdicts": verdicts, "logic": cov,
            "error": err, **gr}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI 选配质量体检（v1_ai 能不能改）")
    ap.add_argument("account")
    ap.add_argument("activity")
    ap.add_argument("stage", nargs="?", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    base = WSP.WS / a.account / a.activity
    if not base.is_dir():
        raise SystemExit("工作区里没有这个活动：%s / %s" % (a.account, a.activity))
    keys = ([a.stage] if a.stage else
            [d.name for d in sorted(base.glob("*/")) if (d / "stage.json").is_file()])
    res = [check_stage(a.account, a.activity, k) for k in keys]
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    order = {"差不多能用": 0, "缺 1 人": 1, "有冲突": 2, "要补位": 3, "无法判断": 4, "缺人": 5}
    res.sort(key=lambda r: (next((v for k, v in order.items() if (r.get("grade") or "").startswith(k)), 9),
                            r.get("key") or ""))
    print("AI 选配质量体检：%s / %s（%d 关）" % (a.account, a.activity, len(res)))
    for r in res:
        if not r.get("ok"):
            print("  %-9s %s" % (r.get("key"), r.get("error")))
            continue
        m = r.get("match") or {}
        cov = r.get("logic")
        v = r["verdicts"]
        print("  %-9s %-5s 动作%-3d 部署%-2d ｜ 匹配 %-8s 缺%s ｜ 冲突 %-2d ｜ 判漏 已挡%d/可打%d/存疑%d"
              % (r["key"], r.get("name") or "", r["n_actions"], r["n_deploy"],
                 m.get("match") or "?", m.get("n_missing") if m else "?",
                 len(r["warns"]), v["已挡"], v["可打"], v["存疑"]))
        if cov:
            print("           必要位 %d/%d%s"
                  % (cov["n_hit"], cov["n"],
                     ("　缺：" + "、".join("[%d,%d]%s" % (x["grid"][0], x["grid"][1],
                                                        x["function_label"] or "")
                                          for x in cov["miss"][:4])) if cov["miss"] else ""))
            if cov.get("n_func_guessed"):
                print("           （其中 %d 个位子的功能是按占位符名字猜的，别当硬结论）"
                      % cov["n_func_guessed"])
        else:
            print("           必要位：无关卡逻辑（先跑 python tools/stage_logic.py --stage %s）"
                  % r["stage_id"])
        print("           ⇒ %s%s" % (r["grade"], ("（" + "；".join(r["reasons"]) + "）")
                                     if r["reasons"] else ""))
    n_ok = sum(1 for r in res if r.get("ok") and r.get("grade", "").startswith("差不多能用"))
    print("\n合计：%d 关可评估 ｜ 评为「差不多能用」%d 关 ｜ 其余见上面分类" % (len(res), n_ok))
    print("下一步：挑一关「差不多能用」的先改一版（改细节才是有价值的标注）；")
    print("        「要补位」的记下来 —— 那是生成端的问题，不是标注能救的。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
