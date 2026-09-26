#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""训练结果报告 —— 把"这个号的打关学习成果"汇成一页 HTML。

用户问：「我要在什么地方才能看见这个账号的训练结果」
⇒ 产出 `runtime/reports/training_report.html`（自带样式、双击就能看）+ 同名 .md。

看什么：
  ① 账号快照（精英化/等级分布；专精/模组标注为"未采集"）
  ② 战绩账本统计：总条数、整体三星率、按关卡 / 按作业的明细
  ③ 关卡画像：同关成功组 vs 失败组算出的特征区分度
  ④ 当前 P(3星) 排序 TOP（含分数分解）
用法:
    python tools/training_report.py
    python tools/training_report.py --open     # 生成后用默认浏览器打开
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
RT = ROOT / "runtime"
OUT_HTML = RT / "reports" / "training_report.html"
OUT_MD = RT / "reports" / "training_report.md"

sys.path.insert(0, str(ROOT / "tools"))


def load_json(p: pathlib.Path, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def load_jsonl(p: pathlib.Path) -> list[dict]:
    out = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def esc(x) -> str:
    return (str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()

    snap = load_json(RT / "account_snapshot.json", {})
    outcomes = load_jsonl(RT / "axis_outcomes.jsonl")
    profiles = load_json(RT / "stage_profiles.json", {})
    rank = load_json(RT / "axis_rank.json", {})
    recipes = load_jsonl(RT / "op_recipes.jsonl")

    opers = snap.get("opers") or {}
    elite = collections.Counter(v.get("elite") for v in opers.values())
    lvl = [v.get("level") or 0 for v in opers.values()]

    # 账本统计
    by_stage = collections.defaultdict(lambda: {"n": 0, "good": 0, "axes": []})
    by_axis = collections.defaultdict(lambda: {"n": 0, "good": 0})
    for o in outcomes:
        # 口径（2026-09-24 修）：演习/无效行不进战绩 —— 胜绩 = 3★ 或 stone=true
        if o.get("mode") == "drill" or o.get("valid") is False:
            continue
        st = o.get("stage") or "?"
        by_stage[st]["n"] += 1
        by_axis[o.get("op_id") or f"{st}_{o.get('id')}"]["n"] += 1
        if (o.get("stars") or 0) >= 3 or o.get("stone") is True:
            by_stage[st]["good"] += 1
            by_axis[o.get("op_id") or f"{st}_{o.get('id')}"]["good"] += 1
        by_stage[st]["axes"].append(o)

    counted = [o for o in outcomes if not (o.get("mode") == "drill" or o.get("valid") is False)]
    total = len(counted)
    good = sum(1 for o in counted if (o.get("stars") or 0) >= 3 or o.get("stone") is True)

    # ── HTML ────────────────────────────────────────────────────────────
    def table(headers, rows) -> str:
        h = "".join(f"<th>{esc(x)}</th>" for x in headers)
        body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
        return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"

    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts: list[str] = []
    parts.append(f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>MAAOrch 打关训练报告</title>
<style>
 body{{background:#14161a;color:#e8e8ea;font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif;margin:0;padding:28px 34px}}
 h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:26px 0 8px;color:#7fd1ff;border-bottom:1px solid #2a2f37;padding-bottom:4px}}
 .sub{{color:#8b93a1;font-size:12px;margin-bottom:18px}}
 .cards{{display:flex;gap:12px;flex-wrap:wrap;margin:10px 0 4px}}
 .card{{background:#1b1f26;border:1px solid #2a2f37;border-radius:10px;padding:12px 16px;min-width:150px}}
 .card b{{display:block;font-size:22px;color:#9ee6a0}} .card span{{color:#8b93a1;font-size:12px}}
 table{{border-collapse:collapse;width:100%;margin:6px 0 12px;font-size:13px}}
 th,td{{border-bottom:1px solid #262b33;padding:5px 9px;text-align:left;vertical-align:top}}
 th{{color:#9fb0c4;font-weight:600;background:#1b1f26}}
 tr:hover td{{background:#191d24}}
 code{{background:#20242c;padding:1px 5px;border-radius:4px;color:#ffd479}}
 .warn{{color:#ffb46b}} .ok{{color:#9ee6a0}} .bad{{color:#ff8f8f}}
 .note{{background:#1b1f26;border-left:3px solid #4b8bd6;padding:9px 13px;color:#b9c2cf;border-radius:0 8px 8px 0;margin:10px 0}}
</style></head><body>
<h1>MAAOrch 打关训练报告</h1>
<div class="sub">生成时间 {stamp} ｜ 数据源：<code>runtime/account_snapshot.json</code> <code>runtime/axis_outcomes.jsonl</code> <code>runtime/op_recipes.jsonl</code> <code>runtime/stage_profiles.json</code> <code>runtime/axis_rank.json</code></div>
""")

    parts.append('<div class="cards">')
    parts.append(f'<div class="card"><b>{len(opers)}</b><span>账号干员（快照）</span></div>')
    parts.append(f'<div class="card"><b>{len(recipes)}</b><span>已解析作业配方</span></div>')
    parts.append(f'<div class="card"><b>{total}</b><span>战绩账本条数</span></div>')
    rate = f"{(good / total * 100):.0f}%" if total else "—"
    parts.append(f'<div class="card"><b>{good} / {total} ({rate})</b><span>面板核实的三星</span></div>')
    parts.append(f'<div class="card"><b>{len(profiles)}</b><span>关卡画像（≥3 样本）</span></div>')
    parts.append("</div>")

    parts.append("<h2>① 账号快照</h2>")
    parts.append(table(["精英化", "人数"], [(f"{k if k is not None else '?'}", v) for k, v in sorted(elite.items(), key=lambda kv: -(kv[0] or 0))]))
    parts.append(f"<div class='note'>等级：最高 {max(lvl) if lvl else 0} ／最低 {min(lvl) if lvl else 0}；"
                 f"来源 <code>{esc(snap.get('source', '?'))}</code>。"
                 f"<span class='warn'>专精 / 模组：{'已采集' if snap.get('mastery_known') else '未采集'}</span>"
                 "（MAA OperBox 读不到，需要跑 <code>tools/mastery_scan.py</code> 扫界面图标）。</div>")

    parts.append("<h2>② 战绩账本</h2>")
    rows = []
    for st, v in sorted(by_stage.items(), key=lambda kv: -kv[1]["n"]):
        best = sorted(v["axes"], key=lambda o: -(o.get("stars") or 0))[:2]
        rows.append([st, v["n"], v["good"],
                     f"{v['good'] / v['n'] * 100:.0f}%",
                     " ".join(f"{o.get('id')}({(o.get('stars') or 0)}★)" for o in best)])
    parts.append(table(["关卡", "试过", "三星", "三星率", "最好结果"], rows) if rows else "<div class='note'>还没有战绩（跑一关就会自动记）。</div>")

    parts.append("<h2>③ 关卡画像（成功组 vs 失败组 → 特征区分度）</h2>")
    if profiles:
        rows = []
        for st, p in profiles.items():
            feats = list((p.get("特征权重") or {}).items())[:6]
            rows.append([st, f"{p.get('样本')}（成功 {p.get('成功')} / 失败 {p.get('失败')}）",
                         "、".join(f"{k} {v:+.2f}" for k, v in feats)])
        parts.append(table(["关卡", "样本", "区分度最高的干员"], rows))
    else:
        parts.append("<div class='note'>暂无：同一关需要 <b>≥3 条战绩且成功/失败组都有</b>才算画像。"
                     "现在样本还不够，再跑几关就会自己出现。</div>")

    parts.append("<h2>④ 当前 P(3星) 排序 TOP</h2>")
    if rank:
        rows = []
        for k, ids in list(rank.items())[:40]:
            rows.append([k, " → ".join(str(i).split("_")[-1] for i in ids[:5])])
        parts.append(table(["关卡|难度", "推荐出场顺序（作业号）"], rows))
    else:
        parts.append("<div class='note'>还没有排序文件（跑一次 <code>python tools/axis_rank.py --rank</code>）。</div>")

    parts.append("</body></html>")
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text("".join(parts), encoding="utf-8")

    # Markdown 版（方便在聊天里贴）
    md = [f"# MAAOrch 打关训练报告（{stamp}）", "",
          f"- 账号干员：**{len(opers)}**｜作业配方：**{len(recipes)}**｜战绩账本：**{total}** 条｜"
          f"面板核实三星：**{good}/{total}**（{rate}）｜关卡画像：**{len(profiles)}** 关", "",
          "## 战绩（按关卡）", "", "| 关卡 | 试过 | 三星 | 三星率 | 最好结果 |", "|---|---|---|---|---|"]
    for st, v in sorted(by_stage.items(), key=lambda kv: -kv[1]["n"]):
        best = sorted(v["axes"], key=lambda o: -(o.get("stars") or 0))[:2]
        md.append(f"| {st} | {v['n']} | {v['good']} | {v['good']/v['n']*100:.0f}% | "
                  + " ".join(f"{o.get('id')}({(o.get('stars') or 0)}★)" for o in best) + " |")
    md += ["", "## 关卡画像", ""]
    if profiles:
        for st, p in profiles.items():
            md.append(f"- **{st}**（{p.get('样本')} 条：成功 {p.get('成功')} / 失败 {p.get('失败')}）："
                      + "、".join(f"{k} {v:+.2f}" for k, v in list((p.get('特征权重') or {}).items())[:6]))
    else:
        md.append("- 暂无（同关需 ≥3 条且成功/失败组都有）")
    OUT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"已生成：{OUT_HTML}")
    print(f"    以及：{OUT_MD}")
    print(f"战绩 {total} 条（三星 {good}）｜画像 {len(profiles)} 关｜排序 {len(rank)} 关")
    if a.open:
        subprocess.run(["cmd", "/c", "start", "", str(OUT_HTML)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
