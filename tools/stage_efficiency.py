#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""关卡效率与材料需求库 —— 给计划生成器（mas_plan_builder）一份本地可查的「刷哪里划算」数据。

产出（唯一生成者）:
  runtime/reports/stage_efficiency.json   关卡表 + 材料→推荐关反查 + 实测一致性
  runtime/state/stage_efficiency_measured.json  实测原始行缓存（--measured 扫描落盘，可离线重建）

数据源（全部只读）:
  1) 公开掉率：企鹅物流 Penguin Stats matrix(CN) —— 本地 SQLite 快照 data/penguin.db
     （services/penguin_data.py 每日 03:30 采集入库；rate = quantity/times = 每把期望掉落）
  2) MAA 资源 services/maa/source/resource/stages.json  —— 关卡→掉落声明（NORMAL_DROP/EXTRA_DROP/FURNITURE）
     services/maa/source/resource/item_index.json       —— 物品 id→名
  3) data/stage_apcost_v4.json —— 关卡计价（书写约定 unit_cost["<CODE>|普通"]）
  4) data/_stage_table.json    —— 游戏 stage_table（官方关卡表：stageId/code/zoneId/apCost/name）
  5) 实测（交叉校验用，可选）: 主机 100.79.173.69 的
     E:\AUTO-MAS\history\<date>\<账号>\*.json 里的 drop_statistics（经 SSH 只读扫描）。
     默认优先用本地缓存 runtime/state/stage_efficiency_measured.json（缓存缺失才扫）；
     --measured 强制重扫，--no-measured 完全离线。

口径（关键定义，展开见 docs/STAGE_EFFICIENCY.md）:
  rate        每把期望掉落（企鹅物流 quantity/times）
  每理智产出  = rate / ap（越大越划算）
  期望AP/个   = ap / rate
  主要掉落    = MAA 资源里该关声明为 NORMAL_DROP 的物品（未声明时回退概率最高的）
  常驻        main/tough/sub/wk_ 前缀 + *_perm（别传）；其余活动/限时关只标注，不判断开放状态
  伪关        stage_id 以 randomMaterial 开头的是「物资补给箱开箱结果」，不是关卡，剔除
  置信度      high / medium：公开样本量分档（≥5000 = high）；实测与公开不一致时降一级
  实测把数    runs = 龙门币/12/ap（主线/活动关：龙门币奖励 = 12×理智）
              资源本（CE/LS/CA/AP/SK/PR）不能用龙门币折算 → 用副本日志
              「开始行动 a~b 次, -N理智」按 GetFightStage selected 归属 + 区间去重累计
  不一致      同 (关, 物品) 实测 rate 与公开 rate 相对差 ≥ MISMATCH_REL 且实测 ≥ MIN_MEASURED_RUNS 把

用法:
  python tools/stage_efficiency.py --build [--days 30] [--measured] [--no-measured]
  python tools/stage_efficiency.py --query 固源岩组 [--json]
  python tools/stage_efficiency.py --json [--stage 1-7]
  python tools/stage_efficiency.py --selftest
  python tools/stage_efficiency.py --check              # SSH 连通性（--measured 用）

退出码: 0=成功；2=数据源不可用（无 penguin 快照 / SSH 不可达且要求实测）；1=未预期错误。
环境变量: MAAORCH_MAS_API / MAAORCH_SSH_KEY / MAAORCH_SSH_HOST（同其他 mas_* 工具）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "services" / "maa" / "source" / "resource"
DB_PATH = ROOT / "data" / "penguin.db"
APCOST_PATH = ROOT / "data" / "stage_apcost_v4.json"
STAGE_TABLE_PATH = ROOT / "data" / "_stage_table.json"
OUT_PATH = ROOT / "runtime" / "reports" / "stage_efficiency.json"
MEASURED_CACHE = ROOT / "runtime" / "state" / "stage_efficiency_measured.json"

SCHEMA = "maaorch.stage_efficiency/1"
MIN_SAMPLE = 500          # 企鹅物流样本下限（与 farm_stages.json / drop_matrix.json 同口径）
TOP_SAMPLE = 5000         # high 置信度门槛
MIN_MEASURED_RUNS = 20    # 实测判定的最小把数
MISMATCH_REL = 0.30       # 实测 vs 公开：相对偏差阈值
MIN_ITEM_RATE = 0.005     # 关卡 drops 表收录下限（每把期望掉落）
MIN_MAT_ENTRY_RATE = 0.02 # 材料反查表收录下限
LMD_ITEM = "龙门币"
LMD_PER_SANITY = 12       # 主线/活动关：龙门币奖励 = 12 × 理智
RESOURCE_CODE_RE = re.compile(r"^(CE|LS|CA|AP|SK|PR)-\d+$")
# 实测 drop_statistics 的名字归一：台/日/国际服落的是繁中/日文名，官服落简中名。
# 只登记实测缓存里实际出现过的变体，且都经「每把掉率 vs 公开」实证核对过（见 docs/STAGE_EFFICIENCY.md §局限）。
NAME_ALIASES = {
    "龍門幣": "龙门币", "聲望": "声望", "名声": "声望",
    # 日语：初級源岩=固源岩(1.240/把 ✓)、源岩鉱=源岩(0.117 ✓)、純金=赤金(0.086 ✓)
    "初級源岩": "固源岩", "源岩鉱": "源岩", "純金": "赤金",
    "入門作戦記録": "基础作战记录", "上級作戦記録": "高级作战记录", "中級作戦記録": "中级作战记录",
    "エステル原料": "酯原料", "ブドウ糖": "代糖", "アケトン試剤": "双酮",
    "異鉄の欠片": "异铁碎片", "破損装置": "破损装置",
    # 台服（繁中）
    "基礎作戰記錄": "基础作战记录", "高級作戰記錄": "高级作战记录", "中級作戰記錄": "中级作战记录",
    "破損裝置": "破损装置", "雙酮": "双酮", "異鐵碎片": "异铁碎片",
}
# 实测里出现但不算材料的名字（家具 / MAA 未解析占位），静默丢弃并计数
NAME_IGNORE = {"家具", "Unknown"}
# 非材料 id（企鹅矩阵里混入的家具/招募标签）—— 不进任何输出
NON_ITEM_IDS = {"furni"} | {f"recruit_tag_{x}" for x in (
    "aoe", "caster", "crowd_control", "debuff", "defender", "defense", "dp_recovery",
    "dps", "element", "fast_redeploy", "guard", "healing", "medic", "melee", "nuker",
    "ranged", "robot", "senior_operator", "shift", "slow", "sniper", "specialist",
    "starter", "summon", "support", "supporter", "survival", "top_operator", "vanguard")}
RESIDENT_PREFIX = ("main_", "tough_", "sub_", "wk_", "pro_")  # pro_ = PR 芯片本（资源本，周排期）
RESIDENT_SUFFIX = ("_perm",)          # 别传（记录修复）关：常驻可刷（企鹅快照 show_closed_zones=false 仍含）
PSEUDO_SID_PREFIX = "randomMaterial"  # 企鹅把「随机物资补给箱开箱结果」也记为 stage_id，不是关卡，剔除


def _log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- 本地数据源
def canon_code(sid: str, code: str) -> str:
    """写书约定：磨难（tough_ 前缀）独立成 "<CODE>-HARD"（与 farm_stages.json 同口径）。"""
    return f"{code}-HARD" if sid.startswith("tough_") else code


def load_resources() -> tuple[list, dict]:
    stages = json.loads((RES / "stages.json").read_text(encoding="utf-8"))
    items = json.loads((RES / "item_index.json").read_text(encoding="utf-8"))
    return stages, items


def load_apcost() -> dict:
    return json.loads(APCOST_PATH.read_text(encoding="utf-8"))


def load_stage_table() -> dict:
    return json.loads(STAGE_TABLE_PATH.read_text(encoding="utf-8"))


def resolve_ap(code: str, sid: str, ap_stages: int | None, apcost: dict) -> tuple[int | None, str]:
    """AP 解析（与 mas_plan_builder 同序）：unit_cost["<CODE>|普通"] → stages.json apCost → v4.stages[sid].ap。"""
    uc = (apcost.get("unit_cost") or {}).get(f"{code}|普通")
    if isinstance(uc, int) and uc > 0:
        return uc, "apcost_v4.unit_cost"
    if isinstance(ap_stages, int) and ap_stages > 0:
        return ap_stages, "maa_stages.json.apCost"
    meta = (apcost.get("stages") or {}).get(sid) or {}
    ap = meta.get("ap")
    if isinstance(ap, int) and ap > 0:
        return ap, "apcost_v4.stages[stageId].ap"
    return None, "未定价"


def load_matrix() -> tuple[dict, dict]:
    """最新企鹅物流快照 → {(stage_id, item_id): [qty, times]} + 快照元信息。"""
    if not DB_PATH.exists():
        return {}, {}
    c = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        row = c.execute("SELECT id, ts, row_count FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return {}, {}
        agg = {}
        for sid, iid, q, t in c.execute(
                "SELECT stage_id, item_id, quantity, times FROM matrix WHERE snapshot_id=?", (row[0],)):
            agg[(sid, iid)] = [q, t]
    finally:
        c.close()
    return agg, {"snapshot_id": row[0], "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row[1])),
                 "fetched_ts": row[1], "rows": row[2],
                 "db": str(DB_PATH.relative_to(ROOT)).replace("\\", "/")}


# ---------------------------------------------------------------- 公开数据 → 关卡表
def build_stages(agg: dict, stages: list, items: dict, apcost: dict,
                 stage_table: dict) -> tuple[dict, dict]:
    """企鹅矩阵 + MAA 掉落声明 + 计价 → 关卡表。

    返回 (stages_out, meta)；stages_out 的键 = 书写用 code（磨难 = "<CODE>-HARD"）。
    """
    id2name = {k: (v.get("name") or k) for k, v in items.items()}
    id2class = {k: (v.get("classifyType") or "") for k, v in items.items()}
    stt = stage_table.get("stages") or {}
    # stageId → (canon code, ap, type, name, zone)
    meta_by_sid: dict = {}
    pseudo_skipped = 0
    for st in stages:
        sid = st["stageId"]
        if sid.startswith(PSEUDO_SID_PREFIX):
            pseudo_skipped += 1
            continue
        code = canon_code(sid, st["code"])
        ap, ap_src = resolve_ap(st["code"], sid, st.get("apCost"), apcost)
        base_sid = sid[: -len("_perm")] if sid.endswith(RESIDENT_SUFFIX) else sid
        game = stt.get(sid) or stt.get(base_sid) or {}
        meta_by_sid[sid] = {
            "stage_id": sid, "code": code, "ap": ap, "ap_source": ap_src,
            "name": game.get("name") or "",
            "zone": game.get("zoneId") or (apcost.get("stages") or {}).get(sid, {}).get("zone")
                    or (stt.get(base_sid) or {}).get("zoneId") or "",
            "stage_type": (apcost.get("stages") or {}).get(sid, {}).get("type") or ("MAIN" if sid.startswith(("main_", "tough_")) else
                          ({"wk_": "DAILY", "sub_": "SUB"}.get(sid[:3] if sid[:3] in ("wk_", "sub_") else "", "OTHER"))),
            "resident": sid.startswith(RESIDENT_PREFIX) or sid.endswith(RESIDENT_SUFFIX),
            "declared": {},   # item_id -> dropType（MAA 声明）
        }
        for d in st.get("dropInfos", []):
            iid = d.get("itemId")
            if iid and iid not in NON_ITEM_IDS:
                meta_by_sid[sid]["declared"][iid] = d.get("dropType")

    # 每关聚合矩阵
    per_stage: dict = {}
    for (sid, iid), (q, t) in agg.items():
        if iid in NON_ITEM_IDS or sid not in meta_by_sid:
            continue
        per_stage.setdefault(sid, []).append((iid, q, t))

    out: dict = {}
    dropped_closed = 0
    for sid, rows in per_stage.items():
        m = meta_by_sid[sid]
        ap = m["ap"]
        if not ap:
            dropped_closed += 1
            continue
        drops = []
        for iid, q, t in rows:
            if t < MIN_SAMPLE:
                continue
            rate = q / t
            if not (0 < rate <= 5):
                continue
            declared = m["declared"].get(iid, "")
            drops.append({
                "item_id": iid, "name": id2name.get(iid, iid),
                "item_class": id2class.get(iid, ""),
                "rate": round(rate, 4), "per_ap": round(rate / ap, 5),
                "eap": round(ap / rate, 2), "sample": t,
                "declared": declared or "UNDECLARED",
                "is_main": declared == "NORMAL_DROP",
            })
        if not drops:
            continue
        drops.sort(key=lambda d: (-d["per_ap"], d["item_id"]))
        main = [d for d in drops if d["is_main"]]
        if not main:  # MAA 未声明主掉落（58 关）→ 用概率最高的 3 个代替，标注
            main = drops[:3]
        code = m["code"]
        sample = max(d["sample"] for d in drops)
        grade = "high" if sample >= TOP_SAMPLE else "medium"
        flags = []
        if not any(d["is_main"] for d in drops):
            flags.append("MAA 资源未声明主掉落（NORMAL_DROP），主要掉落按公开概率推")
        if not m["resident"]:
            flags.append("非常驻（活动/限时）关：关闭后不可刷，本库不判断开放状态")
        kept = [d for d in drops if d["rate"] >= MIN_ITEM_RATE or d["is_main"]]
        out[code] = {
            "code": code, "stage_id": sid, "name": m["name"], "zone": m["zone"],
            "stage_type": m["stage_type"], "resident": m["resident"],
            "ap": ap, "ap_source": m["ap_source"],
            "sample": sample, "sample_grade": "high" if sample >= TOP_SAMPLE else "medium",
            "main_drops": [{"item_id": d["item_id"], "name": d["name"], "item_class": d["item_class"],
                            "rate": d["rate"],
                            "per_ap": d["per_ap"], "eap": d["eap"], "declared": d["declared"]}
                           for d in main[:5]],
            "drops": [{k: d[k] for k in ("item_id", "name", "item_class", "rate", "per_ap", "eap", "sample", "declared")}
                      for d in kept],
            "confidence": grade, "flags": flags,
        }
    meta = {"stages_with_ap": len(out), "matrix_stages_skipped_no_ap": dropped_closed,
            "pseudo_stages_skipped": pseudo_skipped}
    return out, meta


# ---------------------------------------------------------------- 实测（SSH 只读扫描）
PS_MEASURED = r"""
$root = 'E:\AUTO-MAS\history'
$days = @(Get-ChildItem $root -Directory | Sort-Object Name -Descending | Select-Object -First @@NDAYS@@)
$rows = New-Object System.Collections.ArrayList
$t0 = Get-Date
foreach ($d in $days) {
  foreach ($ad in (Get-ChildItem $d.FullName -Directory)) {
    foreach ($jf in (Get-ChildItem $ad.FullName -Filter *.json -File)) {
      $drops = @{}
      $runs = @{}
      try { $j = (Get-Content -Raw -Encoding UTF8 $jf.FullName | ConvertFrom-Json -ErrorAction Stop) } catch { continue }
      if ($null -ne $j.drop_statistics) {
        foreach ($p in $j.drop_statistics.PSObject.Properties) {
          $nm = [string]$p.Name
          $items = @{}
          foreach ($q in $p.Value.PSObject.Properties) { $items[[string]$q.Name] = [int]$q.Value }
          $drops[$nm] = $items
        }
      }
      if ($drops.Count -eq 0) { continue }
      $lf = [System.IO.Path]::ChangeExtension($jf.FullName, '.log')
      if (Test-Path $lf) {
        $cur = ''
        $seen = @{}
        $ms = @(Select-String -Path $lf -Pattern 'GetFightStage|开始行动' -Encoding UTF8 -ErrorAction SilentlyContinue)
        foreach ($m in $ms) {
          $ln = [string]$m.Line
          $mm = [regex]::Match($ln, 'selected\s+([^\s,\]]+)')
          if ($mm.Success) { $cur = $mm.Groups[1].Value; continue }
          $mm = [regex]::Match($ln, '开始行动\s+(\d+)(?:~(\d+))?\s*次,\s*[-－](\d+)\s*理智')
          if ($mm.Success) {
            $a = [int]$mm.Groups[1].Value
            $b = if ($mm.Groups[2].Success) { [int]$mm.Groups[2].Value } else { $a }
            $n = [int]$mm.Groups[3].Value
            $k = "$cur|$a|$b"
            if (-not $seen.ContainsKey($k)) {
              $seen[$k] = 1
              if (-not $runs.ContainsKey($cur)) { $runs[$cur] = @{ runs = 0; sanity = 0 } }
              $runs[$cur].runs += ($b - $a + 1)
              $runs[$cur].sanity += $n
            }
          }
        }
      }
      $rec = [ordered]@{ day = $d.Name; acct = [string]$ad.Name; file = [string]$jf.Name
                         drops = $drops; runs = $runs
                         mt = $jf.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') }
      [void]$rows.Add($rec)
    }
  }
}
Write-Output ('__JSON__' + (ConvertTo-Json -Compress -Depth 8 ([ordered]@{
  meta = [ordered]@{ hist = $root; days = @($days | ForEach-Object { $_.Name }); n = $rows.Count
                     secs = [int]((Get-Date) - $t0).TotalSeconds }
  rows = $rows })))
"""


def _ssh_ps():
    """复用 tools/stage_capability.py 定稿的 ssh/PS 管道（同目录，延迟 import）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import stage_capability as sc  # noqa: PLC0415
    return sc.ssh_ps


def scan_measured(days: int = 30, timeout: int = 1500) -> tuple[dict, str]:
    """SSH 只读扫主机 history（最近 days 个日目录）→ 原始 payload。"""
    ps = PS_MEASURED.replace("@@NDAYS@@", str(int(days)))
    out, err = _ssh_ps()(ps, timeout=timeout)
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("__JSON__"):
            return json.loads(line[len("__JSON__"):]), ""
    return {}, ("SSH 采集失败（无 __JSON__ 标记）: " + (out or "(空)")[-300:]
                + " | stderr: " + (err or "(空)")[-300:])


def aggregate_measured(rows: list, stage_ap: dict, known_names: set | None = None,
                       is_resource_code=lambda c: bool(RESOURCE_CODE_RE.match(c))) -> dict:
    """实测原始行 → {code: {runs, sanity, runs_src, items{name:qty}, attempts, days[], qc{...}}}（纯函数）。

    每把掉率 = 物品总数 / 把数；把数优先用龙门币折算（主线/活动关），资源本用副本日志累计。
    物品名先过 NAME_ALIASES 归一（台/日/国际服的繁中/日文名）；known_names 给定时，
    归一后仍不认识的物品名一律丢弃并计数（qc.unknown_names），不猜。
    """
    out: dict = {}
    qc = {"attempts_with_lmd": 0, "attempts_with_log": 0, "lmd_vs_log_compared": 0,
          "lmd_vs_log_agree": 0, "sanity_lmd": 0.0, "sanity_log": 0, "unmeasurable": 0,
          "unknown_names": {}, "dropped_unknown_rows": 0,
          "ignored_nonmaterial": 0, "denominator_log_fallback": 0}
    for r in rows:
        day = r.get("day", "")
        for code, items in (r.get("drops") or {}).items():
            ap = stage_ap.get(code)
            runs_log = int(((r.get("runs") or {}).get(code) or {}).get("runs", 0) or 0)
            sanity_log = int(((r.get("runs") or {}).get(code) or {}).get("sanity", 0) or 0)
            norm: dict = {}
            ignored = 0
            for name, qty in items.items():
                n = NAME_ALIASES.get(name, name)
                if n in NAME_IGNORE:
                    ignored += int(qty)
                    continue
                norm[n] = norm.get(n, 0) + int(qty)
            qc["ignored_nonmaterial"] += ignored
            lmd = int(norm.get(LMD_ITEM, 0) or 0)
            runs_lmd = 0
            if lmd and ap and not is_resource_code(code):
                runs_lmd = int(round(lmd / LMD_PER_SANITY / ap))
            if not ap:
                qc["unmeasurable"] += 1
                continue
            # 分母（把数）：优先龙门币折算（=实际花掉的理智/ap，最接近「出了奖励的把数」）；
            # 但 LMD 明显不完整（< 日志把数的一半）时以日志为准 —— 实测有「龙门币只落 10 而日志跑了 149 把」的坏样本。
            use_lmd = runs_lmd > 0 and (runs_log == 0 or runs_lmd >= 0.5 * runs_log)
            runs = runs_lmd if use_lmd else runs_log
            if runs <= 0:
                qc["unmeasurable"] += 1
                continue
            if lmd:
                qc["attempts_with_lmd"] += 1
            if runs_log:
                qc["attempts_with_log"] += 1
            if runs_lmd and runs_log and not is_resource_code(code):
                qc["lmd_vs_log_compared"] += 1
                qc["sanity_lmd"] += runs_lmd * ap
                qc["sanity_log"] += sanity_log
                if abs(runs_lmd * ap - sanity_log) <= max(1.0, 0.02 * runs_lmd * ap):
                    qc["lmd_vs_log_agree"] += 1
            if not use_lmd:
                qc["denominator_log_fallback"] += 1
            src = "lmd" if use_lmd else "log"
            e = out.setdefault(code, {"runs": 0, "sanity": 0, "runs_src": {}, "items": {},
                                      "attempts": 0, "days": [], "log_sanity": 0})
            e["runs"] += runs
            e["sanity"] += runs * ap
            e["log_sanity"] += sanity_log
            e["runs_src"][src] = e["runs_src"].get(src, 0) + runs
            e["attempts"] += 1
            if day and day not in e["days"]:
                e["days"].append(day)
            for name, qty in norm.items():
                if name in (LMD_ITEM, "声望"):
                    continue
                if known_names is not None and name not in known_names:
                    qc["unknown_names"][name] = qc["unknown_names"].get(name, 0) + int(qty)
                    qc["dropped_unknown_rows"] += 1
                    continue
                e["items"][name] = e["items"].get(name, 0) + int(qty)
    for code, e in out.items():
        e["days"] = sorted(e["days"])
        e["measured_rate"] = {n: (q / e["runs"] if e["runs"] else 0.0) for n, q in e["items"].items()}
        e["runs_src"] = max(e["runs_src"].items(), key=lambda kv: kv[1])[0] if e["runs_src"] else ""
    return {"stages": out, "qc": qc}


# ---------------------------------------------------------------- 交叉校验
def cross_check(stage: dict, meas: dict | None, name2id: dict, params: dict | None = None) -> dict | None:
    """实测 vs 公开（纯函数）。返回 None = 该关无实测；否则带 items/rel_dev/flags。"""
    if not meas or not meas.get("runs"):
        return None
    p = params or {}
    min_runs = p.get("min_measured_runs", MIN_MEASURED_RUNS)
    thr = p.get("mismatch_rel", MISMATCH_REL)
    ap = stage["ap"]
    pub_by_name = {d["name"]: d for d in stage["drops"]}
    items, flags = {}, []
    for name, mrate in sorted(meas["measured_rate"].items(), key=lambda kv: -kv[1]):
        qty = meas["items"][name]
        row = {"qty": qty, "rate": round(mrate, 4), "per_ap": round(mrate / ap, 5) if ap else None}
        pub = pub_by_name.get(name)
        if pub:
            row["public_rate"] = pub["rate"]
            dev = (mrate - pub["rate"]) / pub["rate"] if pub["rate"] else None
            row["rel_dev"] = round(dev, 3) if dev is not None else None
            if dev is not None and abs(dev) >= thr:
                if meas["runs"] >= min_runs:
                    row["mismatch"] = "实测与公开数据不一致"
                    flags.append(f"{name}：公开 {pub['rate']:.3f}/把 vs 实测 {mrate:.3f}/把"
                                 f"（相对差 {dev * 100:+.0f}%；实测 {meas['runs']} 把）")
                else:
                    row["note"] = f"偏差大但实测样本不足（{meas['runs']} 把 < {min_runs}），不判定"
        else:
            row["public_rate"] = None
            row["note"] = "公开数据（企鹅）无该物品，仅实测"
        items[name] = row
    return {"runs": meas["runs"], "sanity": meas["sanity"], "src": meas["runs_src"],
            "attempts": meas["attempts"], "days": [meas["days"][0], meas["days"][-1]] if meas["days"] else [],
            "items": items, "flags": flags}


def build_material_index(stages_out: dict, name2id: dict) -> dict:
    """材料 → 推荐关反查（按每理智产出降序）。"""
    idx: dict = {}
    for code, st in stages_out.items():
        for d in st["drops"]:
            if d["rate"] < MIN_MAT_ENTRY_RATE:
                continue
            note = []
            if d["declared"] == "NORMAL_DROP":
                note.append("主掉落")
            elif d["declared"] == "EXTRA_DROP":
                note.append("额外掉落")
            else:
                note.append("未声明（企鹅实测）")
            note.append("常驻" if st["resident"] else "活动/限时（关闭后不可刷）")
            mm = ((st.get("measured") or {}).get("items") or {}).get(d["name"]) or {}
            if mm.get("mismatch"):
                note.append("⚠ 实测与公开数据不一致")
            idx.setdefault(d["name"], []).append({
                "stage": code, "per_ap": d["per_ap"], "rate": d["rate"], "eap": d["eap"],
                "ap": st["ap"], "sample": d["sample"], "confidence": st["confidence"],
                "item_class": d.get("item_class", ""),
                "source": "penguin_matrix", "note": "；".join(note)})
    for name in idx:
        idx[name].sort(key=lambda r: -r["per_ap"])
    return idx


# ---------------------------------------------------------------- 构建
def build(days: int = 30, measured_mode: str = "cache", scan_timeout: int = 1500) -> tuple[dict, list]:
    stages, items = load_resources()
    apcost = load_apcost()
    stage_table = load_stage_table()
    agg, snap = load_matrix()
    problems: list = []
    if not agg:
        problems.append("penguin.db 无快照（先跑 python tools/material_stages.py --dump-farm 或 services/penguin_data.refresh）")
    stages_out, smeta = build_stages(agg, stages, items, apcost, stage_table)
    stage_ap = {c: st["ap"] for c, st in stages_out.items()}

    # ---- 实测（可选）
    meas_payload, meas_meta, scan_ts = {}, None, None
    if measured_mode != "off":
        if measured_mode == "scan" or (measured_mode == "cache" and not MEASURED_CACHE.exists()):
            _log(f"[实测] SSH 只读扫描主机 history 最近 {days} 天 …")
            payload, err = scan_measured(days=days, timeout=scan_timeout)
            if err:
                problems.append("实测扫描失败：" + err)
                _log("[实测] " + err)
            else:
                MEASURED_CACHE.parent.mkdir(parents=True, exist_ok=True)
                MEASURED_CACHE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                scan_ts = time.time()
                _log(f"[实测] 落盘 {MEASURED_CACHE.relative_to(ROOT)}（{payload['meta']['n']} 次尝试）")
        if MEASURED_CACHE.exists():
            meas_payload = json.loads(MEASURED_CACHE.read_text(encoding="utf-8"))
            if scan_ts is None:  # 用缓存的落盘时间当「扫描时间」（不谎报成构建时间）
                scan_ts = MEASURED_CACHE.stat().st_mtime
    if meas_payload:
        known_items = {v.get("name") for v in items.values()}
        agg_m = aggregate_measured(meas_payload.get("rows") or [], stage_ap, known_names=known_items)
        name2id = {v.get("name"): k for k, v in items.items()}
        n_mis = 0
        for code, meas in agg_m["stages"].items():
            st = stages_out.get(code)
            if not st:
                continue
            cc = cross_check(st, meas, name2id)
            if not cc:
                continue
            st["measured"] = cc
            if cc["flags"]:
                st["flags"].extend(cc["flags"])
                st["confidence"] = {"high": "medium", "medium": "low", "low": "low"}[st["confidence"]]
                n_mis += 1
        meas_meta = {
            "source": {"host": os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69").split("@")[-1],
                       "path": r"E:\AUTO-MAS\history\<date>\<账号>\*.json 的 drop_statistics（+ 同名 .log 的「开始行动」理智）",
                       "mode": "SSH 只读"},
            "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(scan_ts or time.time())),
            "window": {"days": days, "start": (meas_payload["meta"]["days"] or [""])[-1],
                       "end": (meas_payload["meta"]["days"] or [""])[0]},
            "attempts": meas_payload["meta"]["n"], "qc": agg_m["qc"],
            "stages_with_measured": len(agg_m["stages"]), "stages_flagged_mismatch": n_mis, "scan_secs": meas_payload["meta"].get("secs"),
        }

    mat_idx = build_material_index(stages_out, {v.get("name"): k for k, v in items.items()})
    mismatches = [{"stage": code, "item": name, "public_rate": row["public_rate"],
                   "measured_rate": row["rate"], "rel_dev": row["rel_dev"], "runs": st["measured"]["runs"]}
                  for code, st in stages_out.items() for name, row in ((st.get("measured") or {}).get("items") or {}).items()
                  if row.get("mismatch")]
    doc = {
        "schema": SCHEMA,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sources": {
            "penguin": dict(snap, name="企鹅物流 Penguin Stats matrix(CN)（本地 SQLite 快照，services/penguin_data.py 03:30 采集）")
                       if snap else {"present": False},
            "maa_res": {"stages": str((RES / "stages.json").relative_to(ROOT)).replace("\\", "/"),
                        "items": str((RES / "item_index.json").relative_to(ROOT)).replace("\\", "/"),
                        "stages_n": len(stages), "drop_decl": "dropInfos[].dropType"},
            "apcost": {"path": str(APCOST_PATH.relative_to(ROOT)).replace("\\", "/"),
                       "rule": apcost.get("_cost_rule", ""), "source": apcost.get("_source", "")},
            "stage_table": {"path": str(STAGE_TABLE_PATH.relative_to(ROOT)).replace("\\", "/"),
                            "stages_n": len(stage_table.get("stages") or {})},
            "measured": meas_meta or {"present": False,
                                      "note": "本次未做实测扫描（--no-measured 或缓存缺失）"},
        },
        "params": {"min_sample": MIN_SAMPLE, "top_sample": TOP_SAMPLE, "min_measured_runs": MIN_MEASURED_RUNS,
                   "mismatch_rel": MISMATCH_REL, "min_item_rate": MIN_ITEM_RATE,
                   "lmd_per_sanity": LMD_PER_SANITY, "resident_prefix": list(RESIDENT_PREFIX),
                   "resident_suffix": list(RESIDENT_SUFFIX), "pseudo_sid_prefix": PSEUDO_SID_PREFIX},
        "formulas": {"rate": "quantity/times（每把期望掉落）", "per_ap": "rate/ap（每理智产出）",
                     "eap": "ap/rate（期望AP/个）", "measured_runs": "龙门币/12/ap（资源本改用副本日志理智累计）"},
        "counts": {"stages": len(stages_out), "materials": len(mat_idx),
                   "stages_with_measured": (meas_meta or {}).get("stages_with_measured", 0),
                   "mismatches": len(mismatches), **smeta},
        "stages": stages_out,
        "materials": mat_idx,
        "mismatches": mismatches,
        "problems": problems,
    }
    return doc, problems


# ---------------------------------------------------------------- 渲染
def render_query(doc: dict, material: str, top: int = 15) -> str:
    mats = doc.get("materials") or {}
    names = [n for n in mats if n == material]
    if not names:
        names = sorted([n for n in mats if material in n], key=len)
    if not names:
        return f"**{material}**: 库里没有该材料（可用 --list-materials 看全部）"
    out = []
    for name in names[:5]:
        rows = mats[name]
        out.append(f"# {name} → 推荐关（每理智产出降序；数据来源=企鹅物流快照 {doc['sources']['penguin'].get('fetched_at', '?')}）")
        for i, r in enumerate(rows[:top], 1):
            star = "★" if r["stage"].endswith("-HARD") else " "
            out.append("  %2d. %-10s ap=%-3s 每理智 %-8.5f 每把 %-6.3f AP/个 %-7.2f 样本 %-8s %-6s | %s"
                       % (i, r["stage"] + star, r["ap"], r["per_ap"], r["rate"], r["eap"],
                          r["sample"], r["confidence"], r["note"]))
        if len(rows) > top:
            out.append(f"  … 其余 {len(rows) - top} 个候选（见 --json / runtime/reports/stage_efficiency.json）")
        st_meas = [(c, st) for c, st in (doc.get("stages") or {}).items()
                   if name in [d["name"] for d in st.get("drops") or []]
                   and (st.get("measured") or {}).get("items", {}).get(name)]
        if st_meas:
            out.append("  实测对照（我们自己的 drop_statistics）：")
            for code, st in sorted(st_meas, key=lambda kv: -kv[1]["measured"]["items"][name].get("rate", 0)):
                m = st["measured"]["items"][name]
                tag = " ⚠ 实测与公开数据不一致" if m.get("mismatch") else ""
                pub = f"{m['public_rate']:.3f}" if m.get("public_rate") else "—"
                out.append("    - %-10s 实测 %.3f/把（%d 把，%s）vs 公开 %s/把%s"
                           % (code, m.get("rate", 0.0), st["measured"]["runs"],
                              st["measured"]["src"], pub, tag))
    return "\n".join(out)


def render_stage(doc: dict, code: str) -> str:
    st = (doc.get("stages") or {}).get(code)
    if not st:
        return f"**{code}**: 库里没有该关"
    out = [f"# {code}（{st['name']}｜{st['stage_type']}｜{st['zone']}｜{'常驻' if st['resident'] else '活动/限时'}）",
           f"理智 {st['ap']}（{st['ap_source']}）｜样本 {st['sample']}｜置信度 {st['confidence']}"]
    if st["flags"]:
        out += ["注意："] + [f"  - {f}" for f in st["flags"]]
    out.append("主要掉落：")
    for d in st["main_drops"]:
        out.append("  - %-10s 每把 %-6.3f 每理智 %-8.5f AP/个 %-6.2f（%s）"
                   % (d["name"], d["rate"], d["per_ap"], d["eap"], d["declared"]))
    if len(st["drops"]) > len(st["main_drops"]):
        out.append("其它掉落（每把≥%.3f）：" % MIN_ITEM_RATE)
        main_ids = {d["item_id"] for d in st["main_drops"]}
        for d in st["drops"]:
            if d["item_id"] in main_ids:
                continue
            out.append("  - %-10s 每把 %-6.3f 每理智 %-8.5f（%s）"
                       % (d["name"], d["rate"], d["per_ap"], d["declared"]))
    m = st.get("measured")
    if m:
        out.append(f"实测（{m['runs']} 把，理智口径 {m['src']}，{m['days'][0]}~{m['days'][-1]}）：")
        for name, row in m["items"].items():
            tag = " ⚠ 实测与公开数据不一致" if row.get("mismatch") else ("" if row.get("public_rate") else "（公开无）")
            out.append("  - %-10s 总数 %-7d 每把 %-6.3f 每理智 %-8.5f vs 公开 %s%s"
                       % (name, row["qty"], row["rate"], row["per_ap"] or 0,
                          ("%.3f" % row["public_rate"]) if row.get("public_rate") else "—", tag))
    return "\n".join(out)


# ---------------------------------------------------------------- 自测
def selftest() -> int:
    fails = []
    n_assert = [0]

    def eq(got, want, label):
        n_assert[0] += 1
        if got != want:
            fails.append(f"{label}: got {got!r} want {want!r}")

    # 1) 书写约定
    eq(canon_code("tough_10-05", "10-6"), "10-6-HARD", "tough → -HARD")
    eq(canon_code("main_10-05", "10-6"), "10-6", "main 不变")

    # 2) AP 解析顺序
    apc = {"unit_cost": {"1-7|普通": 6}, "stages": {"main_09-16": {"ap": 21}}}
    eq(resolve_ap("1-7", "main_01-07", 6, apc)[0], 6, "unit_cost 优先")
    eq(resolve_ap("10-6", "main_10-05", 21, apc)[0], 21, "回退 stages.json")
    eq(resolve_ap("9-18", "main_09-16", None, apc), (21, "apcost_v4.stages[stageId].ap"), "回退 v4.stages")
    eq(resolve_ap("XX-1", "act_xx_01", None, {"stages": {}})[0], None, "未定价 → None")

    # 3) 实测聚合：龙门币折算把数
    rows = [
        {"day": "2026-09-01", "drops": {"1-7": {"龙门币": 72, "固源岩": 1, "基础作战记录": 1}}, "runs": {}},
        {"day": "2026-09-02", "drops": {"1-7": {"龙门币": 144, "固源岩": 3}}, "runs": {}},
    ]
    agg = aggregate_measured(rows, {"1-7": 6})
    eq(agg["stages"]["1-7"]["runs"], 1 + 2, "龙门币折算把数")
    eq(round(agg["stages"]["1-7"]["measured_rate"]["固源岩"], 4), round(4 / 3, 4), "每把掉率")
    # 台/日服繁中名归一 + 未知名丢弃
    rows_n = [{"day": "d", "drops": {"1-7": {"龍門幣": 144, "初級源岩": 8, "旧磁带": 5}}, "runs": {}}]
    agg_n = aggregate_measured(rows_n, {"1-7": 6}, known_names={"固源岩", "源岩"})
    eq(agg_n["stages"]["1-7"]["runs"], 2, "繁中「龍門幣」折算把数")
    eq(agg_n["stages"]["1-7"]["items"], {"固源岩": 8}, "繁中名归一 + 未知名丢弃")
    eq(agg_n["qc"]["dropped_unknown_rows"], 1, "未知名丢弃计数")
    # 资源本改用日志
    rows2 = [{"day": "2026-09-01", "drops": {"CE-6": {"龙门币": 10000, "基础作战记录": 10}},
              "runs": {"CE-6": {"runs": 25, "sanity": 900}}}]
    agg2 = aggregate_measured(rows2, {"CE-6": 36})
    eq(agg2["stages"]["CE-6"]["runs"], 25, "资源本用日志把数")
    eq(round(agg2["stages"]["CE-6"]["measured_rate"]["基础作战记录"], 3), 0.4, "资源本每把掉率")
    # 无把数 → 不产出（记 unmeasurable）
    agg3 = aggregate_measured([{"day": "d", "drops": {"1-7": {"固源岩": 1}}, "runs": {}}], {"1-7": 6})
    if agg3["stages"] != {}:
        fails.append("无把数应跳过")
    eq(agg3["qc"]["unmeasurable"], 1, "unmeasurable 计数")

    # 4) 交叉校验：偏差判定 + 样本不足
    st = {"ap": 6, "drops": [{"name": "固源岩", "rate": 1.245, "declared": "NORMAL_DROP"}], "flags": []}
    meas_big = {"runs": 100, "sanity": 600, "runs_src": "lmd", "attempts": 10, "days": ["a", "b"],
                "items": {"固源岩": 60}, "measured_rate": {"固源岩": 0.6}}
    cc = cross_check(st, meas_big, {}) or {}
    eq(cc["items"]["固源岩"]["mismatch"], "实测与公开数据不一致", "偏差大 → 标注")
    eq(len(cc["flags"]), 1, "flag 数")
    meas_small = {"runs": 5, "sanity": 30, "runs_src": "lmd", "attempts": 1, "days": ["a"],
                  "items": {"固源岩": 1}, "measured_rate": {"固源岩": 0.2}}
    cc2 = cross_check(st, meas_small, {}) or {}
    eq(cc2["items"]["固源岩"].get("mismatch"), None, "样本不足不判定")
    eq("不判定" in cc2["items"]["固源岩"]["note"], True, "样本不足给说明")
    eq(cross_check(st, None, {}), None, "无实测 → None")

    # 5) 材料反查顺序（每理智产出降序）
    stages_out = {
        "10-6": {"ap": 21, "resident": True, "confidence": "high",
                 "drops": [{"name": "固源岩组", "per_ap": 0.0362, "rate": 0.76, "eap": 27.6, "sample": 9999, "declared": "NORMAL_DROP"}],
                 "measured": None},
        "2-4": {"ap": 12, "resident": True, "confidence": "high",
                "drops": [{"name": "固源岩组", "per_ap": 0.0352, "rate": 0.423, "eap": 28.4, "sample": 9999, "declared": "NORMAL_DROP"}],
                "measured": {"runs": 30, "sanity": 360, "src": "lmd", "attempts": 2, "days": ["d1", "d2"],
                             "items": {"固源岩组": {"qty": 8, "rate": 0.267, "per_ap": 0.0222,
                                                   "public_rate": 0.423, "rel_dev": -0.369,
                                                   "mismatch": "实测与公开数据不一致"}},
                             "flags": ["固源岩组：公开 0.423/把 vs 实测 0.267/把（相对差 -37%；实测 30 把）"]}},
    }
    idx = build_material_index(stages_out, {})
    eq([r["stage"] for r in idx["固源岩组"]], ["10-6", "2-4"], "反查降序")
    eq("⚠ 实测与公开数据不一致" in idx["固源岩组"][1]["note"], True, "反查带不一致标注")

    # 6) 渲染
    doc = {"materials": idx, "stages": stages_out,
           "sources": {"penguin": {"fetched_at": "2026-09-28 03:30:59"}}}
    txt = render_query(doc, "固源岩组")
    if "10-6" not in txt or "每理智" not in txt:
        fails.append("render_query 内容缺项")
    txt2 = render_stage({"stages": {"2-4": {**stages_out["2-4"], "name": "图穷匕见", "stage_type": "MAIN",
                                             "zone": "main_2", "sample": 9999, "sample_grade": "high",
                                             "ap_source": "apcost_v4.unit_cost", "flags": [],
                                             "main_drops": stages_out["2-4"]["drops"]}}}, "2-4")
    if "图穷匕见" not in txt2:
        fails.append("render_stage 内容缺项")

    # 7) build_stages：伪关（randomMaterial*）剔除 + _perm 名字/常驻 + 活动关非常驻标注
    f_stages = [
        {"stageId": "randomMaterial_6", "code": "罗德岛物资补给III", "apCost": 99,
         "dropInfos": [{"dropType": "NORMAL_DROP", "itemId": "30013"}]},
        {"stageId": "act16side_04_perm", "code": "GA-4", "apCost": 12,
         "dropInfos": [{"dropType": "NORMAL_DROP", "itemId": "30013"}]},
        {"stageId": "act99side_01", "code": "XX-1", "apCost": None,
         "dropInfos": [{"dropType": "NORMAL_DROP", "itemId": "30013"}]},
    ]
    f_items = {"30013": {"name": "固源岩组", "classifyType": "MATERIAL"}}
    f_stt = {"stages": {"act16side_04": {"name": "安魂教堂", "zoneId": "act16sre_zone1"}}}
    f_agg = {("randomMaterial_6", "30013"): [600, 6000],
             ("act16side_04_perm", "30013"): [1000, 2000],
             ("act99side_01", "30013"): [3000, 6000]}
    out_s, m_s = build_stages(f_agg, f_stages, f_items, {"unit_cost": {"GA-4|普通": 12, "XX-1|普通": 15}}, f_stt)
    eq(sorted(out_s), ["GA-4", "XX-1"], "伪关剔除 + _perm/活动关入表")
    eq(out_s["GA-4"]["name"], "安魂教堂", "_perm 名字回退到基础 stageId")
    eq(out_s["GA-4"]["zone"], "act16sre_zone1", "_perm zone 回退")
    eq(out_s["GA-4"]["resident"], True, "_perm 记常驻")
    eq(out_s["XX-1"]["resident"], False, "非 _perm 活动关非常驻")
    eq(any("非常驻" in f for f in out_s["XX-1"]["flags"]), True, "活动关带非常驻标注")
    eq(m_s.get("pseudo_stages_skipped"), 1, "伪关计数")

    if fails:
        print("SELFTEST FAIL:")
        for f in fails:
            print("  ✗", f)
        return 1
    print("SELFTEST OK（7 组 / 断言 %d 条）" % n_assert[0])
    return 0


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="关卡效率与材料需求库（本地只读生成）")
    ap.add_argument("--build", action="store_true", help="重建 runtime/reports/stage_efficiency.json")
    ap.add_argument("--days", type=int, default=30, help="实测扫描窗口（天；默认 30）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--measured", dest="measured_mode", action="store_const", const="scan",
                   help="强制重新 SSH 只读扫描实测（默认：有缓存用缓存，无缓存才扫）")
    g.add_argument("--no-measured", dest="measured_mode", action="store_const", const="off",
                   help="不碰实测（纯公开数据，离线）")
    ap.add_argument("--query", metavar="材料", help="材料 → 推荐关反查")
    ap.add_argument("--stage", metavar="关卡", help="单关详情（如 1-7 / 10-6-HARD）")
    ap.add_argument("--list-materials", action="store_true", help="列出库内全部材料名")
    ap.add_argument("--json", action="store_true", help="机读输出（单行 JSON）")
    ap.add_argument("--out", help="输出路径（默认 runtime/reports/stage_efficiency.json）")
    ap.add_argument("--scan-timeout", type=int, default=1500, help="SSH 扫描超时秒")
    ap.add_argument("--selftest", action="store_true", help="离线自测（纯函数断言，零联网零写盘）")
    ap.add_argument("--check", action="store_true", help="SSH 连通性自检（--measured 用）")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.check:
        payload, err = scan_measured(days=1, timeout=min(args.scan_timeout, 600))
        if err:
            print("stage_efficiency --check: FAIL\n  " + err)
            return 2
        print("stage_efficiency --check: OK（history 日目录 %s，%d 次尝试，%ss）"
              % (payload["meta"]["days"], payload["meta"]["n"], payload["meta"]["secs"]))
        return 0

    out_path = Path(args.out) if args.out else OUT_PATH
    old = {}
    if out_path.exists():
        try:
            old = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            old = {}

    if args.build:
        doc, problems = build(days=args.days, measured_mode=args.measured_mode or "cache",
                              scan_timeout=args.scan_timeout)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            shown = out_path.resolve().relative_to(ROOT)
        except ValueError:
            shown = out_path.resolve()
        _log(f"[构建] {shown} 关卡 {doc['counts']['stages']}｜材料 {doc['counts']['materials']}"
             f"｜实测关 {doc['counts']['stages_with_measured']}｜不一致 {doc['counts']['mismatches']}")
        if problems:
            for p in problems:
                _log("  ! " + p)
        if args.json:
            print(json.dumps(doc, ensure_ascii=False))
        return 0 if doc["counts"]["stages"] else 2

    doc = old
    if not doc:
        print("尚无 runtime/reports/stage_efficiency.json —— 先跑 --build", file=sys.stderr)
        return 2

    if args.list_materials:
        names = sorted((doc.get("materials") or {}))
        print(f"材料 {len(names)} 个：" + "、".join(names))
        return 0
    if args.stage:
        txt = render_stage(doc, args.stage)
        print(json.dumps({"stage": args.stage, "text": txt}, ensure_ascii=False) if args.json else txt)
        return 0
    if args.query:
        if args.json:
            names = [n for n in (doc.get("materials") or {}) if n == args.query] \
                or [n for n in (doc.get("materials") or {}) if args.query in n]
            print(json.dumps({n: (doc["materials"][n]) for n in names}, ensure_ascii=False))
        else:
            print(render_query(doc, args.query))
        return 0
    print(json.dumps({k: doc.get(k) for k in ("schema", "built_at", "counts", "sources")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
