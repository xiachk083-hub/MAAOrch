#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**审计"标了 done 的单位"到底有没有凭据** —— 判错的代价不对称，所以判据要分层。

为什么要有它（用户口径）：`done` 意味着"这颗源石已经到手（或确认没矿）"，一旦判错就把矿**永久漏掉**；
而反过来把"真的已领"拉回来重验，代价只是**一次导航 + 一次面板读**（不花理智：⊘ 就不打）。
⇒ 所以本工具的立场是：**没有凭据就算没凭据**，但要把"凭据强弱"分清楚，别把强证据也扔进重验队列。

## 判据分层（2026-09-24 由 auditor-1 重写；旧版有三个硬伤，见文末"旧版问题"）

**最强（面板实测，正确 ROI，逐关）** —— `runtime/reports/map_panels.json`、
`runtime/learned/panels_<前缀>_<区>.json`、`runtime/reports/panels_*.json`：
  · `page=='关卡详情' and stars>=1 and reward_primes is False` ⇒ `proven_done`（矿已领）
  · `reward_primes is True` 或 `(stars==0 and cleared is False)` ⇒ `proven_mine`（矿还在！必须回 todo）

**强（账本自证，CLI 正确 ROI 口径）**：
  · `verdict` 含「徽章已消失」且 `stars_after>=3` ⇒ `proven_done_ledger`
    （跑前徽章在 `reward_score≈1.0`、跑后复核 3★ 且徽章消失 —— 一条完整的到账链）
  · `action=='no_prime'` 且 `stars in (1,2,3)` 且 `reward_score < 0.75` ⇒ `legit_no_prime`
    （三星已通关 + 正确 ROI 上徽章确实不在 ⇒ 合理的 ⊘）

**弱（要重验）**：
  · `action=='no_prime'` 但 `stars` 缺失/为 0/>3 ⇒ `suspect_broken_roi`
    （旧服务徽章 ROI 写反 + 星星除数 490：`stars=11` 物理上不可能 ⇒ 这类 ⊘ 不可信）
  · 只有 `tried[].three_star is True` / `result` 以"成功"开头，但**没有任何面板/徽章证据**
    ⇒ `maa_self_report_only`（MAA 自报不可信，用户明确说过）
  · 什么都查不到 ⇒ `no_evidence`

**全局交叉核对**：`runtime/plans/stone_counter_timeline.json`（人工看大厅源石计数器的读数 + 截图出处）
—— 它只能给"总数涨了几颗"的界，不能归属到某一关，所以只作为 sanity 行打印。

用法:
    python tools/audit_done_units.py            # 只报告（写 runtime/plans/reverify_list.json）
    python tools/audit_done_units.py --apply    # 把 reverify 集合写回 _prime_state3.json（有备份）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys
import time

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
OUT = ROOT / "runtime" / "plans" / "reverify_list.json"
STATE = ROOT / "runtime" / "_prime_state3.json"


def jlines(p: pathlib.Path):
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("{"):
                try:
                    yield json.loads(line)
                except Exception:                        # noqa: BLE001
                    pass
    except FileNotFoundError:
        return


# ── 内部关卡名 → 游戏代号（**复用队列里的 EVENTS 表**，不另抄一份）──────────
def stage_to_code(stage: str, ev_prefix: dict[str, str]) -> str:
    """`act39side_ex04` → `EP-EX-4`；`act42side_07` → `MT-7`；`act18side_s03` → `LE-S-3`。"""
    m = re.match(r"^(act\d+side)_(ex|s|sp)?0*(\d+)$", stage, re.I)
    if not m:
        return stage
    ev, kind, num = m.group(1), (m.group(2) or "").lower(), int(m.group(3))
    pre = ev_prefix.get(ev, "")
    if not pre:
        return stage
    return f"{pre}-{kind.upper() + '-' if kind else ''}{num}"


def ev_prefix_map() -> dict[str, str]:
    sys.path.insert(0, str(ROOT / "runtime"))
    try:
        from _prime_queue3 import EVENTS                  # noqa: PLC0415
        return {ev: prefix for ev, _n, prefix in EVENTS}
    except Exception:                                     # noqa: BLE001
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="把 reverify 集合写回状态文件（先备份）")
    ap.add_argument("--state-file", default="", help="要审的状态文件（默认 runtime/_prime_state3.json）")
    ap.add_argument("--units", default="", help="逗号分隔的单位清单（审历史快照用）")
    ap.add_argument("--units-file", default="", help="单位清单 json（[..] 或 reverify_list.json 形态）")
    a = ap.parse_args()

    if a.units or a.units_file:
        if a.units_file:
            d = json.loads((ROOT / a.units_file).read_text(encoding="utf-8"))
            if isinstance(d, dict):
                done = [x["unit"] if isinstance(x, dict) else x
                        for b in ("weak_only", "no_evidence") for x in (d.get(b) or [])]
                done += list(d.get("keep_done") or [])
            else:
                done = list(d)
        else:
            done = [u.strip() for u in a.units.split(",") if u.strip()]
        done = sorted(set(done))
    else:
        st = json.loads((ROOT / (a.state_file or "runtime/_prime_state3.json")).read_text(encoding="utf-8"))
        done = [k for k, v in st.items() if v == "done"]
        st = st
    evp = ev_prefix_map()

    proven_done: dict[str, str] = {}
    proven_mine: dict[str, str] = {}

    # ① 面板实测（最强）—— **带时间戳**，并识别"扫描早于状态写入"（陈旧，不能据此回滚）
    panel_ts: dict[str, float] = {}

    def ts_of(node, fallback: float) -> float:
        for k in ("scanned_at", "ts"):
            v = node.get(k) if isinstance(node, dict) else None
            if v:
                try:
                    return time.mktime(time.strptime(str(v)[:19], "%Y-%m-%dT%H:%M:%S"))
                except Exception:                          # noqa: BLE001
                    pass
        return fallback

    def take_panel(unit_key: str, rec: dict, src: str, scan_ts: float) -> None:
        if not isinstance(rec, dict) or rec.get("page") != "关卡详情":
            return
        stars, prime = rec.get("stars"), rec.get("reward_primes")
        panel_ts[unit_key] = max(panel_ts.get(unit_key, 0.0), scan_ts)
        if prime is True:
            proven_mine[unit_key] = f"{src} stars={stars} 徽章={prime} score={rec.get('reward_score')}"
        elif isinstance(stars, int) and stars >= 1 and prime is False:
            proven_done[unit_key] = f"{src} stars={stars} 徽章={prime} score={rec.get('reward_score')}"

    p = ROOT / "runtime" / "reports" / "map_panels.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        nodes = d.get("nodes") if isinstance(d.get("nodes"), dict) else d
        t = ts_of(d, p.stat().st_mtime)
        stamp = time.strftime("%H:%M", time.localtime(t))
        for code, rec in (nodes.items() if isinstance(nodes, dict) else []):
            if str(code).startswith("_"):
                continue
            for want in ("普通", "突袭"):
                take_panel(f"{code}|{want}", rec, f"{p.name}@{stamp}", t)
    for p in list((ROOT / "runtime" / "learned").glob("panels_*.json")) + \
            list((ROOT / "runtime" / "reports").glob("panels_*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                 # noqa: BLE001
            continue
        nodes = d.get("nodes") or d
        t = ts_of(d, p.stat().st_mtime)
        for code, rec in (nodes.items() if isinstance(nodes, dict) else []):
            if str(code).startswith("_"):
                continue
            for want in ("普通", "突袭"):
                take_panel(f"{code}|{want}", rec, p.name, t)

    # ② 账本（强/弱都要按字段细分）
    proven_ledger: dict[str, str] = {}
    legit_noprime: dict[str, str] = {}
    suspect: dict[str, str] = {}
    maa_only: dict[str, str] = {}
    for p in sorted((ROOT / "runtime" / "reports").glob("event_sweep_*.jsonl")):
        for r in jlines(p):
            key = f"{r.get('code')}|{r.get('want')}"
            act, verdict, score = r.get("action"), str(r.get("verdict") or ""), r.get("reward_score")
            stars, stars_after = r.get("stars"), r.get("stars_after")
            # `star` 字段永远是 null（老版工具读的就是它 ⇒ 全是 None）——这里读 stars/stars_after
            if act == "done" and "徽章已消失" in verdict and isinstance(stars_after, int) \
                    and stars_after >= 3:
                proven_ledger.setdefault(key, f"{p.name} verdict=…徽章已消失 stars_after={stars_after} "
                                             f"score={score}")
            elif act == "no_prime":
                if isinstance(stars, int) and 1 <= stars <= 3 and isinstance(score, (int, float)) \
                        and score < 0.75:
                    legit_noprime.setdefault(key, f"{p.name} ⊘ stars={stars} score={score}")
                else:
                    suspect.setdefault(key, f"{p.name} ⊘ **stars={stars} score={score}**（坏 ROI/坏除数）")
            elif act in ("done", "partial"):
                if isinstance(stars_after, int) and stars_after >= 3:
                    proven_ledger.setdefault(key, f"{p.name} action={act} stars_after={stars_after} "
                                                 f"score={score}")
                else:
                    maa_only.setdefault(key, f"{p.name} action={act} verdict={verdict[:40]} score={score}")

    # ③ axis_outcomes（**Key 是内部名，必须映射成代号**：旧版就是漏了这一步，强证据通道全是空）
    axis: dict[str, str] = {}
    for r in jlines(ROOT / "runtime" / "axis_outcomes.jsonl"):
        code = stage_to_code(str(r.get("stage") or ""), evp)
        want = r.get("want") or ""
        if not code or not want:
            continue
        # **只认「真打且有效」的行**（2026-09-24 口径修，与 plan_mines.py / axis_rank.py /
        #   rank_service.mjs / training_report.py 同源）：演习（mode=="drill"）只烧演习券、
        #   不产收益 ⇒ 演习 3★ 不是通关凭据；无效局（valid is False）连星数都没有。
        #   （旧版只看 stars>=3：`act43side_ex02_59767` 的 drill-3star 会给 AD-EX-2 当凭据。）
        #   胜绩/通关凭据两个来源：3★ 读数 **或** `stone: true`（源石到账 = 终局证据，
        #   结算页星数判据曾把亮勋章读成 0★，真通关只留下 stone=true + stars=null）。
        if r.get("mode") == "drill" or r.get("valid") is False:
            continue
        if (isinstance(r.get("stars"), int) and r["stars"] >= 3) or r.get("stone") is True:
            axis.setdefault(f"{code}|{want}",
                            f"axis_outcomes stars={r.get('stars')} stone={r.get('stone')} "
                            f"note={r.get('note')}")

    # ④ 分档
    state_mtime = (ROOT / "runtime" / "_prime_state3.json").stat().st_mtime
    rows = {"proven_done": [], "proven_mine": [], "proven_ledger": [], "legit_no_prime": [],
            "stale_panel": [], "maa_self_report_only": [], "suspect_broken_roi": [], "no_evidence": []}
    for k in done:
        # **陈旧面板证据**：面板扫描早于"状态被写成 done" ⇒ 中间可能发生过本工具看不到的事
        #   （实测 CV-EX-8：11:27 扫到"徽章仍在"，11:42 那颗矿已经被打掉、源石 33→34，但**没有账本**）。
        #   ⇒ 这种不能凭旧扫描回滚，只能"重读一次面板"定性。
        if k in proven_mine and panel_ts.get(k, 0) < state_mtime:
            rows["stale_panel"].append({
                "unit": k,
                "evidence": f"{proven_mine[k]}；但扫描时间 {time.strftime('%H:%M', time.localtime(panel_ts[k]))}"
                            f" 早于状态写入 {time.strftime('%H:%M', time.localtime(state_mtime))} ⇒ "
                            f"需重读面板（别凭旧扫描回滚）"})
            continue
        for bucket, table in (("proven_mine", proven_mine), ("proven_done", proven_done),
                              ("proven_ledger", proven_ledger), ("proven_ledger", axis),
                              ("legit_no_prime", legit_noprime),
                              ("suspect_broken_roi", suspect),
                              ("maa_self_report_only", maa_only)):
            if k in table:
                rows[bucket].append({"unit": k, "evidence": table[k]})
                break
        else:
            rows["no_evidence"].append({"unit": k, "evidence": "账本/面板/战绩里都查不到凭据"})

    keep = [r["unit"] for b in ("proven_done", "proven_ledger", "legit_no_prime") for r in rows[b]]
    revert = [r["unit"] for b in ("proven_mine", "suspect_broken_roi", "maa_self_report_only",
                                 "no_evidence") for r in rows[b]]

    # ⑤ 全局交叉核对（只能给"总数涨了几颗"的界，不归属到单关）
    tl = ROOT / "runtime" / "plans" / "stone_counter_timeline.json"
    timeline = json.loads(tl.read_text(encoding="utf-8")) if tl.exists() else None

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "done_total": len(done),
        "keep_done": sorted(keep),
        "reverify": sorted(revert),
        **{k: sorted(v, key=lambda x: x["unit"]) for k, v in rows.items()},
        "stone_counter": timeline,
        "_note": "proven_* = 有凭据（面板实测最强）；legit_no_prime = 合理 ⊘；"
                 "suspect_broken_roi = 坏 ROI/坏除数时代的 ⊘（不可信）；"
                 "maa_self_report_only = 只有 MAA 自报；no_evidence = 什么都没有。"
                 "reverify = 后四类；重验只花导航+面板读（⊘ 不打，不花理智）。",
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"done 总数 {len(done)}")
    for k in ("proven_done", "proven_ledger", "legit_no_prime", "proven_mine", "stale_panel",
              "suspect_broken_roi", "maa_self_report_only", "no_evidence"):
        print(f"  {k:22s} {len(rows[k])}")
    print(f"\n保留 done（有凭据）{len(keep)}：{sorted(keep)}")
    print(f"要重验 {len(revert)}：{sorted(revert)}")
    if timeline:
        print(f"\n全局交叉核对（大厅源石计数器，人工看图）：{timeline.get('readings')}")
    print("落盘:", OUT)

    if a.apply:
        shutil.copy2(STATE, STATE.with_suffix(f".json.bak-{time.strftime('%Y%m%d_%H%M%S')}"))
        for k in revert:
            st[k] = "todo"
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"✓ 已把 {len(revert)} 个单位写回 todo（有备份）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
