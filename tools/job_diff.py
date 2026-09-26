"""两份作业的结构化差异 —— 同一关不同账号的 `cleared.json` 放一起就是标注数据。

用途（2026-09-16 用户）：**差异本身就是标注**。「AI 给的那份」与「人改完能过的这份」
差在哪一步、差成什么样，就是"模型错在哪、人怎么修"的监督信号。所以差异要按动作对齐
输出成结构化条目（step/type/from/to），而不是给人看的 diff 文本。

对齐方式：先按 (动作类型, 干员名) 做 SequenceMatcher 对齐，再逐步比字段。
**不是按下标 zip** —— 作业改一行（多加一次撤退）就会让后面全部错位，那样报出来的
"第 3 步换了人"其实是"第 3 步前面插了一条"，越改越像。

    python tools/job_diff.py A.json B.json                  # 两份文件
    python tools/job_diff.py A.json B.json --json            # 结构化输出（喂训练/落库）
    python tools/job_diff.py --stage act54side_01            # 各账号工作区里这一关的 cleared.json 两两对比
    python tools/job_diff.py --stage act54side_01 --a 9036 --b emu2
"""
from __future__ import annotations

import argparse
import difflib
import json
import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).resolve().parent
PROJ = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(PROJ))

TYPES = {"部署": "Deploy", "撤退": "Retreat", "技能": "Skill", "技能开关": "Skill",
         "SpeedUp": "SpeedUp", "SkillDaemon": "SkillDaemon"}
DEPLOY = ("Deploy",)
# **框架动作**：无站位、无目标，只管节奏与技能开关。它们的**位置**不是打法内容 ——
# 池子里的作业常把 SpeedUp 插在中间（中途加速），而编辑器导出会把 SpeedUp 提到最前、
# SkillDaemon 放到最后（见 PLAN_EDITOR.md §七）。于是"只挪了一格"会被报成
# `action_added(SpeedUp) + action_removed(SpeedUp)` 三处改动 —— 忠实但没用，
# 而且会让"改 N 处"这个数失真（实测在闭环第一圈就撞上）。
# 所以：对齐时**不计框架动作**，只在数量变化时给一条低噪提示。
FRAMEWORK = ("SpeedUp", "SkillDaemon")


def _t(a: dict) -> str:
    return TYPES.get(a.get("type") or "", a.get("type") or "")


def _acts(job: dict) -> list[dict]:
    out = []
    for a in job.get("actions") or []:
        if not isinstance(a, dict):
            continue
        out.append({"type": _t(a), "name": a.get("name") or "", "location": a.get("location"),
                    "direction": a.get("direction"), "costs": a.get("costs"),
                    "kills": a.get("kills"),
                    "pre_delay": a.get("pre_delay", a.get("preDelay"))})
    return out


def _names(job: dict) -> dict:
    """opers → {名字: 技能槽}（作业里这三项是可选字段，没有就记 None，不编默认值）。"""
    out = {}
    for o in job.get("opers") or []:
        if isinstance(o, dict) and o.get("name"):
            out[o["name"]] = o.get("skill")
        elif isinstance(o, str):
            out[o] = None
    return out


def diff_jobs(job_a: dict, job_b: dict) -> list[dict]:
    """对比两份作业 → 结构化差异条目列表。

    条目字段：`step`（A 里的第几步，从 1 数；只有 B 有时取 B 的位置）、
    `type`（operator/direction/location/costs/kills/pre_delay/action_type/action_added/
    action_removed/opers_added/opers_removed/groups）、`from`/`to`、
    `name`（涉及哪个干员）、`note`（人话）。
    """
    diffs: list[dict] = []
    aa, ab = _acts(job_a), _acts(job_b)
    # 框架动作只统计数量（见 FRAMEWORK 的说明），不参与对齐 —— 否则"挪一格"会变成三处改动
    n_fw_a = sum(1 for x in aa if x["type"] in FRAMEWORK and not x["name"])
    n_fw_b = sum(1 for x in ab if x["type"] in FRAMEWORK and not x["name"])
    aa = [x for x in aa if not (x["type"] in FRAMEWORK and not x["name"])]
    ab = [x for x in ab if not (x["type"] in FRAMEWORK and not x["name"])]
    if n_fw_a != n_fw_b:
        diffs.append({"step": None, "type": "framework", "from": n_fw_a, "to": n_fw_b,
                      "note": "框架动作（SpeedUp/SkillDaemon）数量 %d → %d" % (n_fw_a, n_fw_b)})

    # 干员名单：跨账号比作业时这是最大的一类差异（两个号有什么人根本不同）
    na, nb = _names(job_a), _names(job_b)
    for n in [x for x in na if x not in nb]:
        diffs.append({"step": None, "type": "opers_added", "name": n, "from": n, "to": None,
                      "note": "只有 A 的编队里有 %s（技能%s）" % (n, na[n])})
    for n in [x for x in nb if x not in na]:
        diffs.append({"step": None, "type": "opers_removed", "name": n, "from": None, "to": n,
                      "note": "只有 B 的编队里有 %s（技能%s）" % (n, nb[n])})
    # ⚠️ 这里**不能**写成 `for n in [x for x in na if x in nb and na[x] != nb[x]]` ——
    # Python 3 的推导式有自己的作用域，`x` 出了推导式就没了（实测 UnboundLocalError：
    # 一旦两份作业有"技能槽不同"的干员，整个 diff 直接崩，而不是少报一条）。
    for n, sa in na.items():
        if n in nb and nb[n] != sa:
            diffs.append({"step": None, "type": "opers_skill", "name": n, "from": sa,
                          "to": nb[n], "note": "%s 的技能槽 %s → %s" % (n, sa, nb[n])})
    if (job_a.get("groups") or []) != (job_b.get("groups") or []):
        diffs.append({"step": None, "type": "groups", "from": job_a.get("groups"),
                      "to": job_b.get("groups"), "note": "groups 段不同"})

    # **作业级元数据也要报**（2026-09-16 用户现场抓到）：页面导出曾把 `minimum_required`
    # 从 v6.0.0 写成 v4.0.0、把 `doc.details` 换成固定文案，而 `n_changes` 仍是 0 ——
    # 于是"改 0 处"是句假话。这里把它纳入差异，**不假装没改过**。
    def _short(v):
        s = "" if v is None else str(v)
        return s if len(s) <= 60 else s[:57] + "…"

    for key in ("minimum_required", "stage_name", "version"):
        va, vb = job_a.get(key), job_b.get(key)
        if va != vb:
            diffs.append({"step": None, "type": "meta", "name": key, "from": va, "to": vb,
                          "note": "%s %s → %s" % (key, _short(va), _short(vb))})
    da, db = job_a.get("doc") or {}, job_b.get("doc") or {}
    for k in sorted(set(da) | set(db)):
        if da.get(k) != db.get(k):
            diffs.append({"step": None, "type": "meta", "name": "doc." + k,
                          "from": da.get(k), "to": db.get(k),
                          "note": "doc.%s：%s → %s" % (k, _short(da.get(k)), _short(db.get(k)))})

    keys = [(x["type"], x["name"]) for x in aa]
    keysb = [(x["type"], x["name"]) for x in ab]
    sm = difflib.SequenceMatcher(a=keys, b=keysb, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                x, y = aa[i1 + k], ab[j1 + k]
                step = i1 + k + 1
                if x["type"] in DEPLOY:
                    if (x["location"] or []) != (y["location"] or []):
                        diffs.append({"step": step, "type": "location", "name": x["name"],
                                      "from": x["location"], "to": y["location"],
                                      "note": "%s 的位置 %s → %s" % (x["name"], x["location"],
                                                                     y["location"])})
                    if (x["direction"] or "") != (y["direction"] or ""):
                        diffs.append({"step": step, "type": "direction", "name": x["name"],
                                      "from": x["direction"], "to": y["direction"],
                                      "note": "%s 的朝向 %s → %s" % (x["name"], x["direction"],
                                                                     y["direction"])})
                for f in ("costs", "kills", "pre_delay"):
                    if x[f] != y[f]:
                        diffs.append({"step": step, "type": f, "name": x["name"],
                                      "from": x[f], "to": y[f],
                                      "note": "%s 的 %s %s → %s" % (x["name"], f, x[f], y[f])})
        elif tag == "replace":
            for k in range(min(i2 - i1, j2 - j1)):
                x, y = aa[i1 + k], ab[j1 + k]
                if x["name"] != y["name"] or x["type"] != y["type"]:
                    diffs.append({"step": i1 + k + 1, "type": "operator", "name": x["name"],
                                  "from": "%s %s" % (x["type"], x["name"]),
                                  "to": "%s %s" % (y["type"], y["name"]),
                                  "note": "第 %d 步 换了操作：A=%s %s → B=%s %s"
                                          % (i1 + k + 1, x["type"], x["name"], y["type"], y["name"])})
            for k in range(i2 - i1 - min(i2 - i1, j2 - j1)):
                x = aa[i1 + k]
                diffs.append({"step": i1 + k + 1, "type": "action_removed", "name": x["name"],
                              "from": x, "to": None, "note": "A 多一步：%s %s" % (x["type"], x["name"])})
            for k in range(j2 - j1 - min(i2 - i1, j2 - j1)):
                y = ab[j1 + k]
                diffs.append({"step": None, "type": "action_added", "name": y["name"],
                              "from": None, "to": y, "note": "B 多一步：%s %s" % (y["type"], y["name"])})
        elif tag == "delete":
            for k in range(i2 - i1):
                x = aa[i1 + k]
                diffs.append({"step": i1 + k + 1, "type": "action_removed", "name": x["name"],
                              "from": x, "to": None,
                              "note": "只在 A：%s %s" % (x["type"], x["name"])})
        elif tag == "insert":
            for k in range(j2 - j1):
                y = ab[j1 + k]
                diffs.append({"step": None, "type": "action_added", "name": y["name"],
                              "from": None, "to": y,
                              "note": "只在 B：%s %s" % (y["type"], y["name"])})
    return diffs


def summarize(diffs: list[dict]) -> dict:
    from collections import Counter
    c = Counter(d["type"] for d in diffs)
    return {"n": len(diffs), "by_type": dict(c),
            "same": len(diffs) == 0}


def render(diffs: list[dict], label_a: str = "A", label_b: str = "B") -> str:
    if not diffs:
        return "两份完全一致（动作序列、位置、朝向、条件字段、编队全同）"
    lines = ["%s ←→ %s：%d 处差异" % (label_a, label_b, len(diffs))]
    for d in diffs:
        step = ("第 %-2d 步" % d["step"]) if d.get("step") else "      "
        lines.append("  %s [%-14s] %s" % (step, d["type"], d.get("note") or ""))
    return "\n".join(lines)


def _load(p) -> dict:
    d = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    if isinstance(d, list):
        d = d[0] if d and isinstance(d[0], dict) else {}
    return d


def _ws_cleared(account: str, stage_id: str) -> pathlib.Path | None:
    """在某账号的工作区里找这一关的 cleared.json（活动 id 从 stageId 前段推）。"""
    import workspace as WSP
    sd = WSP.find_stage_dir(account, activity_of(stage_id), stage_id)
    if not sd:
        return None
    f = sd / "cleared.json"
    return f if f.is_file() else None


def activity_of(stage_id: str) -> str:
    """act54side_ex01 / act54side_01 → act54side（活动 id = act<数字><侧别> 那一段）。

    不能简单 rsplit("_", 1)：`act54side_01` 只有两段，rsplit 会得到 `act54side`（对），
    但 `act54side_ex01` 也是两段、rsplit 得到 `act54side`（也对）—— 真正需要正则的是
    `act16side_08#f#` 这类带后缀的，别为了少写一行正则把它切错。
    """
    m = re.match(r"^(act\d+[a-z]+)(?:_|$)", str(stage_id))
    return m.group(1) if m else str(stage_id).rsplit("_", 1)[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="两份作业的结构化差异（同关不同账号 = 标注数据）")
    ap.add_argument("files", nargs="*", help="两份作业 JSON：A B")
    ap.add_argument("--stage", default=None, help="stageId（如 act54side_01）：在各账号工作区里找 cleared.json")
    ap.add_argument("--a", default=None, help="配 --stage：账号 A")
    ap.add_argument("--b", default=None, help="配 --stage：账号 B")
    ap.add_argument("--json", action="store_true", help="只输出结构化 JSON（给训练/落库用）")
    ap.add_argument("-o", "--out", default=None, help="把结构化差异写到文件")
    a = ap.parse_args(argv)

    if a.stage:
        import workspace as WSP
        accts = [x for x in ([a.a, a.b] if a.a and a.b else WSP.list_accounts()) if x]
        pairs = [(a.a, a.b)] if a.a and a.b else [(x, y) for i, x in enumerate(accts) for y in accts[i + 1:]]
        found, result = [], {}
        for x, y in pairs:
            fa, fb = _ws_cleared(x, a.stage), _ws_cleared(y, a.stage)
            if not fa or not fb:
                print("  %s vs %s：%s" % (x, y, "有一侧没有 cleared.json（跳过）"))
                continue
            d = diff_jobs(_load(fa), _load(fb))
            found.append((x, y, d))
            result["%s|%s" % (x, y)] = {"a": str(fa), "b": str(fb), "diffs": d, "summary": summarize(d)}
            if not a.json:
                print(render(d, "%s/%s" % (x, a.stage), "%s/%s" % (y, a.stage)))
                print()
        if not found:
            print("没有任何一对能比：先让两侧各自保存过 cleared.json（python tools/ws_server.py 起服务后点「保存为 cleared」）")
            return 1
        if a.json or a.out:
            txt = json.dumps({"stage": a.stage, "pairs": result}, ensure_ascii=False, indent=1)
            if a.out:
                pathlib.Path(a.out).write_text(txt, encoding="utf-8")
                print("→ %s（%d 对）" % (a.out, len(result)))
            else:
                print(txt)
        return 0

    if len(a.files) != 2:
        ap.print_help()
        return 2
    ja, jb = _load(a.files[0]), _load(a.files[1])
    d = diff_jobs(ja, jb)
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(
            {"a": a.files[0], "b": a.files[1], "diffs": d, "summary": summarize(d)},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print("→ %s（%d 处差异）" % (a.out, len(d)))
        return 0
    if a.json:
        print(json.dumps({"a": a.files[0], "b": a.files[1], "diffs": d, "summary": summarize(d)},
                         ensure_ascii=False, indent=1))
    else:
        print(render(d, pathlib.Path(a.files[0]).name, pathlib.Path(a.files[1]).name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
