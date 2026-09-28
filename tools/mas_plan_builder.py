#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""刷取计划生成器：干员 + 仓库 + 能力台账 + 活动  ->  计划草案（可转 run profile 执行）。

输入（默认只读本地产物；只有解析脚本时才碰 REST，且只读）：
  runtime/reports/accounts/<账号>/{operators.json,depot.json}   上号识别产物（t_c2442fc7）
  runtime/reports/stage_capability.json                         能力台账（t_db0211ba，30 天窗口）
  runtime/reports/server_events.json                            各服活动快照
  config/material_water_levels.json                             17 材料水位 + 每日 AP 预算
  config/farm_stages.json                                       材料 -> 候选关（企鹅物流掉率，AP/个升序）
  data/stage_apcost_v4.json                                     关卡计价（书写约定：unit_cost["<CODE>|普通"]）
  runtime/reports/mas_account_master_*.json                     （可选）账号->脚本 离线兜底

决策规则（可解释；每条依据写进 .md 的「依据 -> 结论」链）：
  1) 缺口 = max(0, 水位 - 仓库)；全达标 -> 补刀模式（余量最小材料）。
  2) 候选关 = farm_stages[材料] 全量备选（按 AP/个升序），AP 与 data/stage_apcost_v4.json 交叉核对。
  3) 能力台账闸门：failed 的关不排（记明原因）；unknown 的关可排但标「需先试探一次」；success 记为已证。
  4) 练度启发式：章节 vs box（E2 / E2-60 数）——本地**没有**关卡推荐等级数据，故标注为启发式；
     若该号在 >= 该章有台账 success 记录，则抑制该告警（实测证据优先于启发式）。
  5) 理智/药：每日 AP 预算取 config（默认 280）；当前理智用 --sanity 传入，未知则只给预算估算。
     药只写 MedicineNumb（不吃 = 0）；临期药（UseExpiringMedicine）AUTO-MAS 两层无字段，只能记录不能写。
  6) 活动：server_events 快照 <=3 天有效（>7 天拒绝据此判断）；该服有刷关型活动 ->
     activity_priority.enabled=true（写 Task.IfActivityFirst）。
  7) 磨难破平：同关磨难（-HARD）与普通价差 <=2% 时优先普通（磨难要单独解锁代理权）。
  8) 尾位兜底：stage_plan 恒留一位给 1-7（恒开放）。MAA 只取第一个「当日开放」者出战，故 1-7
     常态不参与，只在候选全不可用/被锁时兜底 —— 否则 MAA 会记「关卡计划内没有当日开放的关卡」
     并跳过理智作战（docs/MAS_RUN_PROFILE.md §6-11；run profile 也会对缺 1-7 的计划告警）。

输出（默认写盘；`--apply` 之外一律不碰 AUTO-MAS 配置）：
  runtime/reports/plans/<账号>_<yyyymmdd_HHMM>.md    人读（结论/依据链/候选表/风险）
  runtime/reports/plans/<账号>_<yyyymmdd_HHMM>.json  run profile（字段与 mas_run_profile.py 对齐 => 可直接执行）
  注：json 里 `_plan_builder` 为下划线注释块（mas_run_profile 校验时跳过），另有 notes 摘要。

执行入口（默认 dry-run；不擅自执行刷取，执行需用户/lead 批准）：
  --exec-dry-run   调 mas_run_profile.py --profile <json>（零写，只走一遍执行路径并打印将做的改动）
  --exec-preflight 上面再加 --preflight（多跑一次只读巡检）
  --apply          调 mas_run_profile.py --profile <json> --apply（真发车；带 preflight/单飞/审计/跑完还原）
  --disable-others / --all-accounts / --write-mode / --max-wait-seconds 原样转发给 run profile（多号脚本语义）。
  --apply 之前还有一票否决闸门：环境变量 MAAORCH_PLAN_BUILDER_NO_GO=1 -> rc=2（不调 run profile）。

用法见 docs/MAS_PLAN_BUILDER.md。自测：`python tools/mas_plan_builder.py --selftest`（离线，零触网零写盘）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
DEFAULT_RUNTIME = os.path.join(ROOT, "runtime")
TS_FMT = "%Y-%m-%d %H:%M:%S"
STAMP_FMT = "%Y%m%d_%H%M"
RUN_PROFILE = os.path.join(TOOLS, "mas_run_profile.py")

MATERIAL_ORDER_HINT = ()            # 材料排序以「缺口折算 AP」为准，无固定顺序
MODE_GAP = "gap"
MODE_TOPPING = "topping"            # 全达标 -> 余量最小补刀
STATUS_FAILED, STATUS_SUCCESS, STATUS_UNKNOWN = "failed", "success", "unknown"

# 磨难（-HARD）与普通同关的 AP/个 价差在 ≤2% 时优先普通：磨难要单独解锁代理权，风险更高
NORMAL_PREF_MARGIN = 1.02

# 活动类型分类（server_events.json 的 event.type 文本口径）
FARMING_EVENT_HINT = ("刷关", "SideStory")
NONFARMING_EVENT_HINT = ("爬塔", "miniGame", "签到", "时装", "礼包", "卡池", "寻访", "登录")

SERVER_ALIASES = {
    "cn_official": ("Official", "官服", "国服", "CN", "cn_official"),
    "cn_bilibili": ("Bilibili", "B服", "哔哩哔哩服", "哔哩哔哩"),
    "jp": ("YoStarJP", "日服", "JP", "jp"),
    "kr": ("YoStarKR", "韩服", "KR", "kr"),
    "tw": ("txwy", "台服", "繁中服", "TW", "tw"),
    "en": ("YoStarEN", "国际服", "Global", "EN", "en"),
}


# ---------------------------------------------------------------- 基础
def _now() -> str:
    return dt.datetime.now().strftime(TS_FMT)


def load_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def scrub(obj):
    """递归丢弃凭据类键（凭据红线，与 mas_run_profile 同口径）。"""
    banned = ("password", "token", "secret", "cookie", "credential", "skland")
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if not any(b in str(k).lower() for b in banned)}
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    return obj


class Refuse(Exception):
    """生成前的拒绝（fail-closed）。"""


# ---------------------------------------------------------------- 路径
class Paths:
    """所有输入/输出路径的集合（selftest 用临时目录覆盖）。"""

    def __init__(self, root: str = ROOT, runtime: str = ""):
        self.root = root
        self.runtime = runtime or os.path.join(root, "runtime")

    @property
    def accounts_dir(self):
        return os.path.join(self.runtime, "reports", "accounts")

    @property
    def cap_path(self):
        return os.path.join(self.runtime, "reports", "stage_capability.json")

    @property
    def events_path(self):
        return os.path.join(self.runtime, "reports", "server_events.json")

    @property
    def plans_dir(self):
        return os.path.join(self.runtime, "reports", "plans")

    @property
    def log_path(self):
        return os.path.join(self.runtime, "mas_plan_builder_log.jsonl")

    @property
    def water_path(self):
        return os.path.join(self.root, "config", "material_water_levels.json")

    @property
    def farm_path(self):
        return os.path.join(self.root, "config", "farm_stages.json")

    @property
    def apcost_path(self):
        return os.path.join(self.root, "data", "stage_apcost_v4.json")

    def account_dir(self, account: str) -> str:
        return os.path.join(self.accounts_dir, account)

    def master_paths(self):
        d = os.path.join(self.runtime, "reports")
        if not os.path.isdir(d):
            return []
        out = [os.path.join(d, n) for n in sorted(os.listdir(d))
               if n.startswith("mas_account_master_") and n.endswith(".json")]
        return out


def _unified_from_audit(paths: Paths, rec: dict) -> None:
    """事件行（mas_plan_builder_log）→ 统一流：stage.<event>（映射同 mas_audit._ad_plan）。"""
    ev = str(rec.get("event") or "").strip()
    if not ev:
        return
    action = "stage." + re.sub(r"[^a-z0-9_]+", "_", ev.lower())
    res = "refused" if "refus" in ev else ("info" if "dry" in ev else None)
    if res is None:
        rc = rec.get("rc")
        res = "fail" if (isinstance(rc, int) and rc != 0) else "ok"
    account = str(rec.get("account") or "")
    params = {k: rec.get(k) for k in ("script", "mode", "stage_plan", "json", "md",
                                      "activity_enabled", "apply", "rc", "profile", "reason")
              if rec.get(k) not in (None, "")}
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import mas_audit  # noqa: PLC0415
    mas_audit.safe_append(
        actor="tool:mas_plan_builder", action=action,
        target=("account:" + account) if account else "-", result=res,
        reason=str(rec.get("reason") or ""), params=params, evidence=[paths.log_path],
        card=os.environ.get("HERMES_KANBAN_TASK", ""),
        path=mas_audit.stream_for_runtime(paths.runtime))

def audit(paths: Paths, rec: dict) -> None:
    """追加一行到本工具审计流（脱敏；stamp 字段用严格 JSON 行）。"""
    os.makedirs(paths.runtime, exist_ok=True)
    rec = dict(rec)
    rec.setdefault("ts", _now())
    with open(paths.log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(scrub(rec), ensure_ascii=False) + "\n")
    try:
        _unified_from_audit(paths, rec)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 输入装载
def load_account_products(paths: Paths, account: str) -> dict:
    """读 runtime/reports/accounts/<账号>/ 的上号识别产物。"""
    d = paths.account_dir(account)
    op_p = os.path.join(d, "operators.json")
    dp_p = os.path.join(d, "depot.json")
    operators = load_json(op_p)
    depot = load_json(dp_p)
    miss = []
    if operators is None:
        miss.append(op_p)
    if depot is None:
        miss.append(dp_p)
    if miss:
        raise Refuse("账号 %s 的上号识别产物缺失/不可读：%s\n"
                     "  先做一次上号识别（操作员+仓库），产物落 runtime/reports/accounts/<账号>/"
                     "（见 docs/HERMES_ACCOUNT_INSPECT_PLAN.md 与 t_c2442fc7 的通道）"
                     % (account, "、".join(miss)))
    return {"operators": operators, "depot": depot, "dir": d, "operators_path": op_p, "depot_path": dp_p}


class Capability:
    """能力台账索引（runtime/reports/stage_capability.json，schema maaorch.stage_capability/1）。

    语义与 tools/stage_capability.py::can_farm 对齐：行存在 -> 行上 status（success/failed）；
    行不存在 -> unknown；pool（13 关）里有 tried 标记则一并带出。
    """

    def __init__(self, report: dict, source: str):
        self.report = report or {}
        self.source = source
        self.built_at = self.report.get("built_at") or ""
        self.window = self.report.get("window") or {}
        self._rows = {}
        for r in self.report.get("rows") or []:
            if isinstance(r, dict) and r.get("account") and r.get("stage"):
                self._rows[(r["account"], r["stage"])] = r
        self._unknown = self.report.get("unknown") or {}
        self.stages = list(self.report.get("stages") or [])

    @classmethod
    def load(cls, paths: Paths, skip: bool = False) -> "Capability":
        rep = load_json(paths.cap_path)
        if rep is None:
            if skip:
                return cls({}, "missing(--skip-capability)")
            raise Refuse("能力台账缺失/不可读：%s\n"
                         "  先跑 tools/stage_capability.py --build（或 --update）生成 30 天台账；"
                         "确实要跳过这条闸门用 --skip-capability（不推荐）。" % paths.cap_path)
        return cls(rep, paths.cap_path)

    def get(self, account: str, stage: str) -> dict:
        row = self._rows.get((account, stage))
        if row is not None:
            return {"status": row.get("status") or STATUS_UNKNOWN, "tried": True, "row": row,
                    "last_success": row.get("last_success") or "", "last_failure": row.get("last_failure") or "",
                    "success_n": row.get("success_n"), "fail_n": row.get("fail_n"),
                    "failure_types": row.get("failure_types") or {}, "impact": row.get("impact") or ""}
        tried = None
        for u in self._unknown.get(account) or []:
            if isinstance(u, dict) and u.get("stage") == stage:
                tried = bool(u.get("tried"))
                break
        return {"status": STATUS_UNKNOWN, "tried": tried, "row": None, "last_success": "", "last_failure": "",
                "success_n": None, "fail_n": None, "failure_types": {}, "impact": ""}

    def chapter_success_evidence(self, account: str, chapter: int, code_chapter) -> list:
        """该号 >= 章 chapter 的台账成功关（用于抑制练度启发式告警）。"""
        out = []
        for (acct, stage), row in self._rows.items():
            if acct != account or row.get("status") != STATUS_SUCCESS:
                continue
            ch = code_chapter(stage)
            if ch is not None and ch >= chapter:
                out.append(stage)
        return sorted(out)


def apcost_lookup(v4: dict, code: str) -> dict:
    """按 data/stage_apcost_v4.json 的书写约定查关：unit_cost["<CODE>|普通"] 优先。

    返回 {ap, source, type, always_open, note}；查不到 ap=None（消费方回退 farm_stages 的 ap）。
    """
    out = {"ap": None, "source": "", "type": "", "always_open": None, "note": ""}
    if not isinstance(v4, dict):
        return out
    uc = v4.get("unit_cost") or {}
    stages = v4.get("stages") or {}
    ci = v4.get("code_index") or {}
    code = str(code or "").strip()
    if not code:
        return out
    hard = code.upper().endswith("-HARD")
    base = code[:-5] if hard else code
    for key, note in (
        (code + "|普通", ""),
        (base + "|普通", "磨难（-HARD）沿用同关计价（v4 unit_cost 无 HARD 键）" if hard else ""),
        (base + "|突袭", "突袭价" if not hard else "磨难（-HARD）按同关突袭价"),
    ):
        if key in uc:
            out["ap"] = uc[key]
            out["source"] = "unit_cost[%s]" % key
            out["note"] = note
            break
    # 类型 / 恒开放（MAIN|SUB|DAILY = 常驻；ACTIVITY = 活动期才开放）
    sids = ci.get(base if hard else code) or []
    metas = [stages.get(s) or {} for s in sids]
    types = {m.get("type") for m in metas if m.get("type")}
    if len(types) == 1:
        out["type"] = types.pop()
        out["always_open"] = out["type"] in ("MAIN", "SUB", "DAILY")
    elif types:
        out["type"] = "/".join(sorted(types))
        out["always_open"] = "MAIN" in types or "SUB" in types
        out["note"] = (out["note"] + "；" if out["note"] else "") + "多价/多类型（按 zone 消歧，farm_stages 的 ap 为准）"
    return out


def load_water_cfg(paths: Paths) -> dict:
    cfg = load_json(paths.water_path)
    if not isinstance(cfg, dict):
        raise Refuse("材料水位配置缺失/不可读：%s" % paths.water_path)
    return cfg


def load_farm_stages(paths: Paths) -> dict:
    fs = load_json(paths.farm_path)
    if not isinstance(fs, dict) or not fs:
        raise Refuse("候选关掉率表缺失/不可读：%s（生成：python tools/material_stages.py --dump-farm --refresh）" % paths.farm_path)
    return fs


def stage_chapter(code: str):
    """关卡所属章节（同 services/material_planner.stage_chapter 口径）：10-6 -> 10；S3-4 -> 0；R8-11 -> 8。"""
    code = str(code or "")
    m = re.match(r"^(\d+)-", code)
    if m:
        return int(m.group(1))
    if code.upper().startswith("S"):
        return 0
    m = re.match(r"^[A-Za-z]+(\d+)-", code)
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------- 目标材料
def compute_targets(depot_items: dict, water_cfg: dict, materials=None, overrides=None) -> list:
    """缺口 = max(0, 水位 - 仓库)。overrides: {材料: 目标量}（含显式指定的材料，即使无缺口）。"""
    levels = dict(water_cfg.get("materials") or {})
    for k, v in (overrides or {}).items():
        levels[str(k)] = v
    owned = {str(k): int(v or 0) for k, v in (depot_items or {}).items()}
    names = list(materials) if materials else list(levels.keys())
    out = []
    for name in names:
        if name not in levels:
            # 显式指定但无水位 -> 用 --material 的目标量（若给）
            if overrides and name in overrides:
                lv = overrides[name]
            else:
                continue
        else:
            lv = levels[name]
        have = owned.get(name, 0)
        gap = max(0, int(lv) - have)
        out.append({"material": name, "owned": have, "target": int(lv), "gap": gap,
                    "surplus": have - int(lv), "explicit": bool(overrides and name in overrides)})
    if not out:
        raise Refuse("没有目标材料：水位表为空或 --materials 都不在表里")
    return out


def topping_choice(targets: list) -> dict:
    """全达标 -> 补刀建议：余量最小（最接近跌破水位）的材料。"""
    return min(targets, key=lambda t: (t["surplus"], t["material"]))


# ---------------------------------------------------------------- 候选关与台账闸门
def analyze_material(account: str, mat: str, cands: list, cap: Capability, apcost: dict) -> dict:
    """单材料候选关分析：排序 -> 台账闸门（failed 剔除 / unknown 标注）-> 选最优。"""
    analyzed, excluded = [], []
    for c in cands or []:
        if not isinstance(c, dict) or not c.get("stage"):
            continue
        ap = c.get("ap")
        rate = c.get("rate")
        try:
            ap_f, rate_f = float(ap), float(rate)
        except (TypeError, ValueError):
            continue
        if rate_f <= 0 or ap_f <= 0:
            continue
        info = cap.get(account, c["stage"])
        ax = apcost_lookup(apcost, c["stage"])
        rec = {"stage": c["stage"], "ap": ap_f, "rate": rate_f, "ap_per_item": round(ap_f / rate_f, 2),
               "cap": info["status"], "cap_note": cap_note(info), "cap_detail": {
                   "last_success": info["last_success"], "last_failure": info["last_failure"],
                   "success_n": info["success_n"], "fail_n": info["fail_n"],
                   "failure_types": info["failure_types"], "tried": info["tried"]},
               "ap_ref": ax["ap"], "ap_source": ax["source"], "ap_note": ax["note"],
               "stage_type": ax["type"], "always_open": ax["always_open"]}
        if info["status"] == STATUS_FAILED:
            rec["verdict"] = "excluded_failed"
            excluded.append(rec)
        else:
            rec["verdict"] = "ok"
            analyzed.append(rec)
    analyzed.sort(key=lambda r: (r["ap_per_item"], r["stage"]))
    best, note = _prefer_normal(analyzed)
    return {"material": mat, "candidates": analyzed, "excluded": excluded, "best": best,
            "best_note": note}


def _prefer_normal(cands: list) -> tuple:
    """磨难 vs 普通：价差 ≤ NORMAL_PREF_MARGIN 时优先普通（理由写进 note）。"""
    if not cands:
        return None, ""
    top = cands[0]
    if "HARD" not in top["stage"].upper():
        return top, ""
    lim = top["ap_per_item"] * NORMAL_PREF_MARGIN
    for c in cands:
        if "HARD" not in c["stage"].upper() and c["ap_per_item"] <= lim:
            return c, ("普通 %s（%.2f AP/个）与磨难 %s（%.2f AP/个）价差 ≤%.0f%% ⇒ 优先普通："
                       "磨难要单独解锁代理权，风险更高" % (c["stage"], c["ap_per_item"], top["stage"],
                                                          top["ap_per_item"], (NORMAL_PREF_MARGIN - 1) * 100))
    return top, ""


def cap_note(info: dict) -> str:
    st = info["status"]
    if st == STATUS_SUCCESS:
        return "台账成功（last %s，%s 次）" % (info["last_success"] or "?", info["success_n"])
    if st == STATUS_FAILED:
        ft = "、".join("%s×%d" % (k, v) for k, v in (info["failure_types"] or {}).items()) or "见台账"
        return "台账失败（%s%s）" % (ft, "；最近 %s" % info["last_failure"] if info["last_failure"] else "")
    if info.get("tried"):
        return "台账未知（30 天窗口内试过但无该关成功/失败记录）"
    if info.get("tried") is False:
        return "台账未知（窗口内没试过该关）→ 需先试探一次"
    return "台账未知（该关不在台账关池，窗口内无记录）→ 需先试探一次"


def pick_queue(account: str, targets: list, cap: Capability, farm_stages: dict, apcost: dict,
               prefer: str = "best") -> list:
    """把目标材料排成队列：缺口折算 AP 大的优先；每项给出最优关与备选。

    只收「有缺口」的材料；显式指定（--material）或补刀（全达标时的余量最小材料）的达标项保留，
    这类项 runs/ap_need=None（没有缺口量，刷多少以预算为准）。
    """
    queue = []
    for t in targets:
        mat = t["material"]
        if t["gap"] <= 0 and not (t["explicit"] or t.get("topping")):
            continue                                    # 达标且非显式 -> 不进队列（仍留在缺口表）
        cands = farm_stages.get(mat) or []
        if not cands:
            queue.append({"material": mat, "gap": t["gap"], "owned": t["owned"], "target": t["target"],
                          "surplus": t["surplus"], "explicit": t["explicit"],
                          "analyzed": {"material": mat, "candidates": [], "excluded": [], "best": None},
                          "no_table": True, "runs": None, "ap_need": None})
            continue
        an = analyze_material(account, mat, cands, cap, apcost)
        if prefer == "proven":
            proven = [c for c in an["candidates"] if c["cap"] == STATUS_SUCCESS]
            if proven:
                an["best"] = proven[0]
                an["best_note"] = "proven-first 策略：取台账已证里最省的一关（%s）" % proven[0]["stage"]
        need = t["gap"]
        if need > 0 and an["best"]:
            runs = int(-(-need // an["best"]["rate"]))            # ceil(gap / rate)
            ap_need = int(round(runs * an["best"]["ap"]))
        else:
            runs, ap_need = None, None
        queue.append({"material": mat, "gap": need, "owned": t["owned"], "target": t["target"],
                      "surplus": t["surplus"], "explicit": t["explicit"], "analyzed": an,
                      "no_table": False, "runs": runs, "ap_need": ap_need})
    queue.sort(key=lambda q: (-(q["ap_need"] if q["ap_need"] is not None else -1), q["material"]))
    return queue


# ---------------------------------------------------------------- 练度（启发式）
BOX_GRADES = ((30, "厚"), (15, "中上"), (8, "中"))
CHAPTER_BOX_MIN = {9: 0, 14: 8, 99: 15}      # 章节 -> E2-60+ 期望数（启发式，非官方推荐等级）


def box_report(operators: dict) -> dict:
    ops = [o for o in (operators.get("operators") or []) if isinstance(o, dict)]
    e2 = [o for o in ops if int(o.get("elite") or 0) >= 2]
    e2_60 = [o for o in e2 if int(o.get("level") or 0) >= 60]
    e2_80 = [o for o in e2 if int(o.get("level") or 0) >= 80]
    six = [o for o in ops if int(o.get("rarity") or 0) == 6]
    six_e2 = [o for o in six if int(o.get("elite") or 0) >= 2]
    grade = "薄"
    for thr, g in BOX_GRADES:
        if len(e2_60) >= thr:
            grade = g
            break
    return {"owned": len(ops), "all": (operators.get("counts") or {}).get("all"),
            "e2": len(e2), "e2_60": len(e2_60), "e2_80": len(e2_80),
            "six": len(six), "six_e2": len(six_e2), "grade": grade}


def box_verdict(box: dict, chapter, chapter_evidence: list) -> dict:
    """练度 vs 章节：启发式（本地无「关卡推荐等级」数据，别当官方口径）。"""
    if chapter is None:
        return {"level": "skip", "text": "该关章节未知（活动关/无章号），练度检查跳过"}
    expect = 0
    for ch in sorted(CHAPTER_BOX_MIN):
        if chapter <= ch:
            expect = CHAPTER_BOX_MIN[ch]
            break
    have = box["e2_60"]
    if have >= expect:
        return {"level": "ok", "text": "章%d 启发式门槛 E2-60+ ≥%d，实际 %d —— 达标" % (chapter, expect, have)}
    if chapter_evidence:
        return {"level": "ok", "text": "章%d 启发式门槛 E2-60+ ≥%d，实际 %d 偏少；但该号有 ≥章%d 的台账成功记录（%s）"
                                       "—— 实测证据优先，判过" % (chapter, expect, have, chapter,
                                                                   "、".join(chapter_evidence[:3]))}
    return {"level": "warn", "text": "章%d 启发式门槛 E2-60+ ≥%d，实际只有 %d（启发式，非官方推荐等级）；"
                                     "建议先试探一次再整轮刷" % (chapter, expect, have)}


# ---------------------------------------------------------------- 理智 / 药
def sanity_policy(cfg: dict, sanity, medicine: int, ap: float) -> dict:
    budget = float(cfg.get("ap_budget_per_day") or 280)
    out = {"budget_per_day": budget, "sanity": sanity, "medicine": medicine,
           "runs_per_day": (int(budget // ap) if ap else None),
           "ap_per_run": ap}
    if sanity is not None and ap:
        out["runs_this_session"] = int(float(sanity) // ap)
    out["medicine_note"] = ("不吃药（MedicineNumb=0）" if not medicine else
                            "吃 %d 瓶（MedicineNumb=%d，写 UseMedicine/MedicineCount）" % (medicine, medicine))
    out["expiring_note"] = ("不单独控制 —— AUTO-MAS 两层没有 UseExpiringMedicine/MedicineExpireDays 键，"
                            "本计划写不了它（真发车会 warn 并把该号 ConfigFile 实际值读回记账）；"
                            "实测该号模板为 true/2 天：配额用尽后会自动继续吃 48h 内过期的药"
                            "（docs/audits/2026-09-27_mas_medicine_expiry.md §1.1/§2）")
    return out


# ---------------------------------------------------------------- 活动
def server_key_of(token) -> str:
    t = str(token or "").strip()
    for key, aliases in SERVER_ALIASES.items():
        if t in aliases or t.lower() == key:
            return key
    return ""


def events_policy(paths: Paths, server_token, now: dt.datetime, warn_age=3.0, fail_age=7.0) -> dict:
    doc = load_json(paths.events_path)
    out = {"available": bool(doc), "source": paths.events_path, "server_key": server_key_of(server_token),
           "server_token": server_token, "farming": [], "active": [], "upcoming_farming": [],
           "enabled": False, "age_days": None, "stale": False, "verdict": "", "headline": ""}
    if not doc:
        out["verdict"] = "活动快照缺失：活动判断关闭（维持 activity_priority.enabled=false）"
        return out
    gen = doc.get("generated_at_bjt") or ""
    out["generated_at_bjt"] = gen
    out["headline"] = doc.get("headline") or ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", str(gen))
    if m:
        try:
            g = dt.datetime.strptime("%s %s" % (m.group(1), m.group(2)), "%Y-%m-%d %H:%M")
            out["age_days"] = round((now - g).total_seconds() / 86400.0, 2)
        except ValueError:
            pass
    if out["age_days"] is not None and out["age_days"] > fail_age:
        out["stale"] = True
        out["verdict"] = ("活动快照已 %.1f 天（>%.0f 天上限）：拒绝据此判断活动，活动优先保持关；"
                          "执行前按 docs/SERVER_EVENTS.md §8 重抓" % (out["age_days"], fail_age))
        return out
    if out["age_days"] is not None and out["age_days"] > warn_age:
        out["stale"] = True
        out["verdict"] = ("活动快照已 %.1f 天（>%.0f 天）：活动时间可能被官方改动，执行前建议重抓" %
                          (out["age_days"], warn_age))
    sk = out["server_key"]
    if not sk:
        out["verdict"] = (out["verdict"] + "；" if out["verdict"] else "") + \
            "服别未识别（%r）⇒ 活动判断关闭" % server_token
        return out
    sv = (doc.get("servers") or {}).get(sk) or {}
    out["verdict_events"] = sv.get("activity_verdict") or ""
    out["maa_stage_activity"] = sv.get("maa_stage_activity") or {}
    for e in sv.get("active_events") or []:
        if not isinstance(e, dict):
            continue
        et = str(e.get("type") or "")
        item = {"name": e.get("name"), "type": et, "start": e.get("start_bjt"), "end": e.get("end_bjt"),
                "source": e.get("source"), "note": e.get("note")}
        out["active"].append(item)
        if any(h in et for h in FARMING_EVENT_HINT) and not any(h in et for h in ("非刷关",)):
            out["farming"].append(item)
    for e in sv.get("upcoming_events") or []:
        if not isinstance(e, dict):
            continue
        et = str(e.get("type") or "")
        if any(h in et for h in FARMING_EVENT_HINT):
            out["upcoming_farming"].append({"name": e.get("name"), "type": et, "start": e.get("start_bjt")})
    if out["farming"] and not out["stale"]:
        out["enabled"] = True
    if not out["verdict"]:
        if out["farming"]:
            out["verdict"] = "该服在期刷关活动 %d 个 ⇒ activity_priority.enabled=true（AutoProxy 插「活动关优先」段）" % len(out["farming"])
        else:
            out["verdict"] = "该服无在期刷关活动 ⇒ 按主线/资源本排（activity_priority.enabled=false）"
    if out["enabled"]:
        out["caveat"] = ("AutoProxy 仅在活动列表非空时插「活动关优先」段（AutoProxy:646-659）；"
                         "列表来自 api.maa.plus StageActivityV2 —— 若该服 MAA 表滞后则不会插。"
                         "标『二手』的事件源（source=maa-stage-activity）仅 MAA 社区表，行动前宜在游戏内核对。")
    return out


# ---------------------------------------------------------------- 脚本解析（只读 REST / 离线兜底）
def resolve_script(paths: Paths, account: str, token: str = "", allow_rest=True, timeout=15) -> dict:
    """解析账号所在脚本：--script 显式 > 账号名册快照（离线） > REST 全扫（只读）。"""
    out = {"script": "", "sid": "", "dir": "", "emu": "", "source": "", "users": [], "multi": None,
           "warnings": []}
    if token:
        out.update(script=token, source="--script")
    snap = None
    if not out["script"]:
        snap = _snapshot_script(paths, account)          # 离线名册先给候选名，省一次全扫
        if snap:
            out.update(script=snap["script"], source=snap["source"])
    # REST（只读）：确认脚本 + 取用户面（启用号/掩码/StageMode）
    if allow_rest:
        try:
            rest = _rest_scripts(timeout=timeout)
        except Exception as e:  # noqa: BLE001
            rest = None
            out["warnings"].append("REST 不可达（%s: %s）⇒ 走离线兜底" % (type(e).__name__, e))
        if rest:
            scripts = rest
            sid = _find_sid_by_name(scripts, out["script"]) if out["script"] else ""
            if not sid:
                print("[resolve] 名册/参数里没有脚本名，全扫 %d 个脚本找账号 %s …" % (len(scripts), account), flush=True)
                sid = _scan_sid_for_account(scripts, account, timeout=timeout)
            if sid:
                info = (scripts[sid].get("Info") or {})
                out["sid"] = sid
                out["script"] = str(info.get("Name") or out["script"])
                if out["source"] != "--script":
                    out["source"] = "REST"
                out["dir"] = str(info.get("Path") or "")
                emu = (scripts[sid].get("Emulator") or {}).get("Index")
                out["emu"] = str(emu) if emu is not None else ""
                try:
                    users = _rest_users(sid, timeout=timeout)
                except Exception as e:  # noqa: BLE001
                    users = []
                    out["warnings"].append("取脚本用户失败：%s: %s" % (type(e).__name__, e))
                out["users"] = users
                enabled = [u for u in users if u.get("status") and str(u.get("remained_day")) != "0"]
                out["multi"] = len(enabled)
                out["server"] = next((u.get("server") for u in users if u.get("name") == account), "")
    if not out["script"]:
        raise Refuse("解析不到账号 %s 所在脚本名（REST 不可达且名册快照里没有它）" % account)
    if snap and out["source"] != "REST" and out["source"] != "--script":
        out["warnings"].append("脚本名来自账号名册快照（%s）——非实时，run profile 发车前会自己复核" % out["source"])
    return out


def _rest_post(path: str, body: dict, timeout: int = 15) -> dict:
    import urllib.request
    api = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
    req = urllib.request.Request(api + path, data=json.dumps(body or {}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _rest_scripts(timeout: int = 15) -> dict:
    data = _rest_post("/api/scripts/get", {}, timeout=timeout).get("data") or {}
    out = {}
    for sid, s in data.items():
        info = (s or {}).get("Info") or {}
        p = str(info.get("Path") or "")
        if not re.match(r"^[A-Za-z]:[\\/]MAA-Multi[\\/]MAA_\d+$", p.replace("/", "/"), re.I):
            p2 = p.replace("/", "\\")
            if not re.match(r"^[A-Za-z]:\\MAA-Multi\\MAA_\d+$", p2, re.I):
                continue
            p = p2
        out[str(sid)] = {"Info": info, "Emulator": (s or {}).get("Emulator") or {}}
    return out


def _rest_users(sid: str, timeout: int = 15) -> list:
    d = _rest_post("/api/scripts/user/get", {"scriptId": sid, "userId": None}, timeout=timeout).get("data") or {}
    out = []
    for uid, uv in d.items():
        if uid == "instances" or not isinstance(uv, dict):
            continue
        i = uv.get("Info") or {}
        out.append({"uid": str(uid), "name": str(i.get("Name") or ""), "status": bool(i.get("Status")),
                    "remained_day": i.get("RemainedDay"), "server": i.get("Server"),
                    "stage_mode": i.get("StageMode")})
    return out


def _find_sid_by_name(scripts: dict, name: str) -> str:
    for sid, s in scripts.items():
        if str((s.get("Info") or {}).get("Name") or "") == name:
            return sid
    return ""


def _scan_sid_for_account(scripts: dict, account: str, timeout: int = 15) -> str:
    for sid in scripts:
        try:
            for u in _rest_users(sid, timeout=timeout):
                if u.get("name") == account:
                    return sid
        except Exception:  # noqa: BLE001
            continue
    return ""


def _snapshot_script(paths: Paths, account: str) -> dict:
    """账号名册快照（runtime/reports/mas_account_master_*.json）里的账号 -> 脚本。"""
    for p in reversed(paths.master_paths()):
        doc = load_json(p)
        if not isinstance(doc, dict):
            continue
        for a in doc.get("accounts") or []:
            if isinstance(a, dict) and a.get("game_name_cfg") == account and a.get("script"):
                return {"script": str(a["script"]), "source": os.path.basename(p)}
    return {}


# ---------------------------------------------------------------- 计划草案 -> profile
def build_stage_slots(queue: list, tail_1_7: bool = True) -> tuple:
    """把队列折成 run profile 的 stage_plan（<=4，顺序=优先级；MAA 取第一个「当日开放」者）。"""
    slots, why = [], []
    if not queue:
        return slots, why
    q0 = queue[0]
    if q0.get("no_table") or not q0["analyzed"]["best"]:
        return slots, why
    best = q0["analyzed"]["best"]
    slots.append(best["stage"])
    why.append("主刷：%s（%s，%.2f AP/个，%s）%s" % (best["stage"], q0["material"], best["ap_per_item"],
                                                  best["cap_note"],
                                                  ("；" + q0["analyzed"]["best_note"]) if q0["analyzed"].get("best_note") else ""))
    # 尾位兜底：恒开放 1-7 **必进**计划（MAA 取第一个「当日开放」者出战，常态根本不会用到它；
    # 只在候选全不可用时兜底，避免整轮「关卡计划内没有当日开放的关卡」跳过理智作战
    # —— docs/MAS_RUN_PROFILE.md §6-11；run profile 也会对缺 1-7 的计划告警）⇒ 先给它留位
    reserve_tail = bool(tail_1_7 and "1-7" not in slots)
    limit = 3 if reserve_tail else 4
    for c in [x for x in q0["analyzed"]["candidates"] if x["stage"] != best["stage"]][:2]:
        if c["stage"] not in slots and len(slots) < limit:
            slots.append(c["stage"])
            why.append("回退位：%s（同材料备选，%.2f AP/个）" % (c["stage"], c["ap_per_item"]))
    if len(slots) < limit and len(queue) > 1:
        q1 = queue[1]
        if not q1.get("no_table") and q1["analyzed"]["best"] and q1["analyzed"]["best"]["stage"] not in slots:
            slots.append(q1["analyzed"]["best"]["stage"])
            why.append("回退位：%s（下一材料 %s 的最优关）" % (slots[-1], q1["material"]))
    if reserve_tail:
        slots.append("1-7")
        why.append("尾位兜底：1-7（恒开放；常态不参与，只在候选全不可用/被锁时兜底）")
    return slots[:4], why


def build_profile(account: str, script: str, slots: list, args, meta: dict) -> dict:
    prof = {
        "target": {"script": script, "account": account},
        "stage_plan": list(slots),
        "medicine": {"use": bool(args.medicine and args.medicine > 0), "count": int(args.medicine or 0),
                     "use_expiring": False},
        "activity_priority": {"enabled": bool(meta.get("activity_enabled")), "medicine": 0},
        "target_drop": {"enabled": False, "drop_id": "", "count": 0},
        "keep_remain_sanity_task": bool(args.keep_remain),
        "restore_after": str(args.restore_after or "run_end"),
        "notes": meta.get("notes") or "",
        "_plan_builder": meta,
    }
    if args.series is not None:
        prof["series"] = str(args.series)
    return prof


def validate_profile_with_tool(prof: dict) -> tuple:
    """直接调用 mas_run_profile.validate_profile —— 保证「字段对齐、可直接执行」。"""
    sys.path.insert(0, TOOLS)
    import importlib
    mod = importlib.import_module("mas_run_profile")
    return mod.validate_profile(prof)


# ---------------------------------------------------------------- 渲染
def _fmt_ev(e: dict) -> str:
    return "%s（%s，%s ~ %s%s）" % (e.get("name"), e.get("type"), e.get("start"), e.get("end"),
                                    "，源:%s" % e.get("source") if e.get("source") else "")


def render_md(plan: dict) -> str:
    L = []
    m = plan["meta"]
    acc = plan["account"]
    L.append("# 刷取计划草案 · %s（%s · UID %s）" % (acc, m["server_name"], m["uid"] or "?"))
    L.append("")
    L.append("生成：%s ｜ 工具：`tools/mas_plan_builder.py` ｜ 默认 dry-run：**本文件不执行任何发车**" % plan["generated_at"])
    L.append("")
    L.append("## 0. 结论（TL;DR）")
    L.append("")
    for line in plan["tldr"]:
        L.append("- %s" % line)
    L.append("")
    L.append("## 1. 输入与出处（证据）")
    L.append("")
    L.append("| 输入 | 值/签 | 出处 |")
    L.append("|---|---|---|")
    for k, v, src in m["inputs"]:
        L.append("| %s | %s | %s |" % (k, v, src))
    L.append("")
    L.append("## 2. 材料缺口（水位 vs 仓库）")
    L.append("")
    L.append("| 材料 | 仓库 | 水位/目标 | 缺口 | 余量 | 说明 |")
    L.append("|---|---|---|---|---|---|")
    for t in plan["targets"]:
        L.append("| %s | %d | %d | %d | %+d | %s |" %
                 (t["material"], t["owned"], t["target"], t["gap"], t["surplus"],
                  "显式指定" if t["explicit"] else ("缺口" if t["gap"] > 0 else "达标")))
    L.append("")
    L.append("## 3. 依据 → 结论 链（逐条）")
    L.append("")
    for i, c in enumerate(plan["chain"], 1):
        L.append("%d. %s" % (i, c))
    L.append("")
    L.append("## 4. 候选关表（含被剔除的关）")
    L.append("")
    if plan["queue"]:
        qtxt = "；".join("%s（缺口 %s，≈%s AP）" % (it["material"], it["gap"] if it["gap"] is not None else "-",
                                                it["ap_need"] if it["ap_need"] is not None else "-")
                        for it in plan["queue"])
        L.append("队列顺序（按缺口折算 AP 降序）：%s。" % qtxt)
        L.append("")
        L.append("> 一轮 run profile 的 stage_plan 只有 4 个位、且只出战「第一个当日开放」的关 —— "
                 "所以**一次只刷一个关**；本表其余材料按队列顺序另起一轮（`--materials <材料>` 可单独出草案）。")
        L.append("")
    for it in plan["queue"]:
        L.append("### %s（缺口 %s）" % (it["material"], it["gap"] if it["gap"] is not None else "?"))
        L.append("")
        if it.get("no_table"):
            L.append("> 掉率表里没有这个材料的候选关 → 只能人工定关（本材料不排）。")
            L.append("")
            continue
        L.append("| 关 | AP | 掉率 | AP/个 | 台账 | 关型 | 裁决 |")
        L.append("|---|---|---|---|---|---|---|")
        for c in it["analyzed"]["candidates"] + it["analyzed"]["excluded"]:
            L.append("| %s | %g | %s | %s | %s | %s | %s |" %
                     (c["stage"], c["ap"], c["rate"], c["ap_per_item"], c["cap_note"],
                      c["stage_type"] or "?", "排入" if c["verdict"] == "ok" else "剔除（failed 不排）"))
        if it["analyzed"]["excluded"]:
            L.append("")
            L.append("被剔除（台账 failed）：" + "；".join(
                "%s（%s）" % (c["stage"], c["cap_note"]) for c in it["analyzed"]["excluded"]))
        if it["runs"]:
            L.append("")
            L.append("估算：缺口 %d 个 ÷ 掉率 %.3f ≈ **%d 次** × %g AP ≈ **%d AP**。"
                     % (it["gap"], it["analyzed"]["best"]["rate"], it["runs"], it["analyzed"]["best"]["ap"], it["ap_need"]))
        L.append("")
    L.append("## 5. 练度体检（启发式）")
    L.append("")
    b = m["box"]
    L.append("box：%d 干员（E2 %d / E2-60+ %d / E2-80+ %d，6★ %d，6★E2 %d），练度档位=%s。"
             % (b["owned"], b["e2"], b["e2_60"], b["e2_80"], b["six"], b["six_e2"], b["grade"]))
    L.append("")
    for line in plan["box_lines"]:
        L.append("- %s" % line)
    L.append("")
    L.append("> ⚠ 本地**没有**「关卡推荐等级」数据源，以上为**启发式**（门槛：章≤9 无要求；章 10–14 需 E2-60+ ≥8；"
             "章 ≥15 需 ≥15 —— 非官方推荐等级）；台账实测（代理权）永远优先，有 ≥ 该章的 success 记录时本项告警自动失效。")
    L.append("")
    L.append("## 6. 理智 / 药 / 活动")
    L.append("")
    sp = plan["sanity"]
    L.append("- 每日 AP 预算：%g（config/material_water_levels.json）→ 该关约 **%s 次/天**。"
             % (sp["budget_per_day"], sp["runs_per_day"] if sp["runs_per_day"] is not None else "?"))
    if sp.get("sanity") is not None:
        L.append("- 当前理智：%s（--sanity）→ 本次约 %s 次。" % (sp["sanity"], sp.get("runs_this_session")))
    else:
        L.append("- 当前理智：**未知**（上号识别产物不含理智；要精确估算请 --sanity N）——以游戏内实际为准。")
    L.append("- 药：%s" % sp["medicine_note"])
    L.append("- 临期药：%s" % sp["expiring_note"])
    L.append("")
    ev = plan["events"]
    L.append("- 服别：%s ｜ 活动快照：%s（%.2f 天前）" % (ev.get("server_key") or "?", ev.get("generated_at_bjt") or "缺",
                                                         ev["age_days"] if ev.get("age_days") is not None else -1))
    L.append("- 判断：%s" % ev.get("verdict"))
    if ev.get("verdict_events"):
        L.append("- 该服口径：%s" % ev["verdict_events"])
    for e in ev.get("active") or []:
        L.append("  - 在期：%s" % _fmt_ev(e))
    for e in ev.get("upcoming_farming") or []:
        L.append("  - 待开（刷关型）：%s 起 %s" % (e.get("name"), e.get("start")))
    if ev.get("caveat"):
        L.append("- ⚠ %s" % ev["caveat"])
    L.append("")
    L.append("## 7. 执行（run profile）")
    L.append("")
    L.append("machine-readable：`%s`（字段与 `tools/mas_run_profile.py` 对齐，校验 rc=%s）" %
             (plan["json_rel"], plan["validate"]["rc"]))
    L.append("")
    L.append("```bash")
    L.append("# 1) 预览执行路径（零写）")
    L.append(plan["cmd_dry_run"])
    L.append("# 2) 真发车（需用户/lead 批准）")
    L.append(plan["cmd_apply"])
    L.append("```")
    L.append("")
    L.append("stage_plan = %s（MAA 取第一个「当日开放」的关；`-`=空位）" % json.dumps(plan["slots"], ensure_ascii=False))
    L.append("")
    if plan.get("slot_why"):
        L.append("排序理由（含回退位）：")
        L.append("")
        for w in plan["slot_why"]:
            L.append("  - %s" % w)
        L.append("")
    L.append("字段口径：只写 7 键的差分 + Task 键；series %s；restore_after=%s。"
             % ("未指定（沿用该号当期值）" if plan["profile"].get("series") is None else "=%s" % plan["profile"]["series"],
                plan["profile"]["restore_after"]))
    if plan["multi"] and plan["multi"] > 1:
        L.append("")
        L.append("> ⚠ 脚本「%s」有 **%d 个启用号**（%s）：发车粒度只到脚本 ⇒ 必须显式二选一："
                 "`--disable-others`（只跑目标号，临时停用其它号并还原）或 `--all-accounts`（整脚本跑）。"
                 "不加会被 run profile 拒绝（fail-closed）。"
                 % (plan["script"], plan["multi"], "、".join(plan["enabled_users"])))
    L.append("")
    L.append("## 8. 停刷条件")
    L.append("")
    for line in plan["stop_lines"]:
        L.append("- %s" % line)
    L.append("")
    L.append("## 9. 风险 / 未验证")
    L.append("")
    for line in plan["risks"]:
        L.append("- %s" % line)
    L.append("")
    return "\n".join(L)


def _cmd(profile_rel: str, apply_: bool, args) -> str:
    py = "python"
    parts = ["%s tools/mas_run_profile.py --profile %s" % (py, profile_rel)]
    if apply_:
        parts.append("--apply")
    if args.disable_others:
        parts.append("--disable-others")
    if args.all_accounts:
        parts.append("--all-accounts")
    if getattr(args, "write_mode", "plan") != "plan":
        parts.append("--write-mode %s" % args.write_mode)
    if getattr(args, "max_wait_seconds", None):
        parts.append("--max-wait-seconds %g" % args.max_wait_seconds)
    return " \\\n  ".join(parts)


# ---------------------------------------------------------------- 主流程
def build_all(paths: Paths, args, now: dt.datetime = None) -> dict:
    now = now or dt.datetime.now()
    account = args.account
    products = load_account_products(paths, account)
    operators, depot = products["operators"], products["depot"]
    uid = str(depot.get("uid") or operators.get("uid") or "")
    server_token = str(depot.get("server") or operators.get("server") or "")
    sc = resolve_script(paths, account, token=args.script or "", allow_rest=(not args.no_rest),
                        timeout=args.rest_timeout)
    if not sc.get("script"):
        raise Refuse("解析不到账号 %s 所在脚本名：REST 不可达且名册快照里没有它；"
                     "执行面需要的 target.script 无从填写 —— 请先让 REST 可达，或用 --script 显式指定。"
                     % account)
    if sc.get("server"):
        server_token = sc["server"]
    cap = Capability.load(paths, skip=args.skip_capability)
    water_cfg = load_water_cfg(paths)
    farm_stages = load_farm_stages(paths)
    apcost = load_json(paths.apcost_path, {}) or {}

    overrides = {}
    for kv in (args.material or []):
        if "=" not in kv:
            raise Refuse("--material 需要 材料=目标量 形式，实得 %r" % kv)
        k, v = kv.split("=", 1)
        try:
            overrides[k.strip()] = int(float(v))
        except ValueError:
            raise Refuse("--material %s 目标量不是数字" % kv) from None
    materials = None
    if args.materials:
        materials = [x.strip() for x in args.materials.split(",") if x.strip()]

    targets = compute_targets(depot.get("items") or {}, water_cfg, materials=materials, overrides=overrides)
    mode = MODE_GAP if any(t["gap"] > 0 for t in targets) else MODE_TOPPING
    topping = None
    if mode == MODE_TOPPING:
        topping = topping_choice(targets)
        if not args.materials:
            targets = [dict(topping, topping=True)]     # 全达标 -> 只排余量最小材料（补刀）
    queue = pick_queue(account, targets, cap, farm_stages, apcost, prefer=args.prefer)

    ev = events_policy(paths, server_token, now, warn_age=args.events_warn_age, fail_age=args.events_fail_age)

    slots, slot_why = build_stage_slots(queue, tail_1_7=(not args.no_tail_1_7))
    if not slots:
        raise Refuse("没有任何可排的关：材料缺口表里没有可评估的候选（检查 --materials / 掉率表）。")

    box = box_report(operators)
    best = queue[0]["analyzed"]["best"] if queue and not queue[0].get("no_table") else None
    ch = stage_chapter(best["stage"]) if best else None
    evid = cap.chapter_success_evidence(account, ch, stage_chapter) if ch is not None else []
    bv = box_verdict(box, ch, evid)
    sanity = sanity_policy(water_cfg, args.sanity, args.medicine, best["ap"] if best else 0)

    probe_needed = sorted({c["stage"] for it in queue for c in (it["analyzed"] or {}).get("candidates") or []
                           if c["cap"] == STATUS_UNKNOWN})
    cap_rows_n = sum(1 for (a, _s) in cap._rows if a == account)
    inputs = [
        ["上号识别·干员", "%s/%s owned（E2 %d）｜fetched_at=%s" % (box["owned"], box["all"] or "?", box["e2"],
                                                               operators.get("fetched_at") or "?"),
         _rel(paths.root, products["operators_path"])],
        ["上号识别·仓库", "%d 类物品｜fetched_at=%s" % (len(depot.get("items") or {}), depot.get("fetched_at") or "?"),
         _rel(paths.root, products["depot_path"])],
        ["能力台账", "built_at=%s｜窗口 %s~%s｜该号 %d 行" % (cap.built_at or "?",
                                                          (cap.window or {}).get("start", "?"),
                                                          (cap.window or {}).get("end", "?"), cap_rows_n),
         _rel(paths.root, paths.cap_path)],
        ["活动快照", "generated_at=%s（%s 天前）｜headline=%s" % (ev.get("generated_at_bjt") or "缺失",
                                                              ev.get("age_days") if ev.get("age_days") is not None else "?",
                                                              (ev.get("headline") or "")[:40]),
         _rel(paths.root, paths.events_path)],
        ["材料水位/预算", "%d 材料，预算 %g AP/天" % (len(water_cfg.get("materials") or {}), sanity["budget_per_day"]),
         _rel(paths.root, paths.water_path)],
        ["候选关掉率表", "%d 材料" % len(farm_stages), _rel(paths.root, paths.farm_path)],
        ["关卡计价(v4)", "unit_cost %d 键" % len((apcost or {}).get("unit_cost") or {}), _rel(paths.root, paths.apcost_path)],
        ["脚本解析", "source=%s｜dir=%s｜emu=%s" % (sc.get("source") or "?", sc.get("dir") or "?", sc.get("emu") or "?"), "—"],
    ]
    meta = {
        "generated_at": now.strftime(TS_FMT),
        "account": account, "uid": uid, "server_name": _pretty_server(server_token),
        "script": sc["script"], "script_source": sc.get("source") or "",
        "script_dir": sc.get("dir") or "", "script_emu": sc.get("emu") or "",
        "mode": mode, "topping": topping,
        "targets": targets, "queue": [{"material": it["material"], "gap": it["gap"], "best": (it["analyzed"]["best"] or {}).get("stage"),
                                       "runs": it["runs"], "ap_need": it["ap_need"]} for it in queue],
        "box": box, "box_verdict": bv, "probe_needed": probe_needed,
        "cap_built_at": cap.built_at, "cap_window": cap.window,
        "events": {"server_key": ev.get("server_key"), "verdict": ev.get("verdict"),
                   "enabled": ev.get("enabled"), "gen": ev.get("generated_at_bjt"), "age_days": ev.get("age_days")},
        "activity_enabled": ev.get("enabled"),
        "inputs": inputs, "budget": sanity["budget_per_day"],
        "water_levels_source": paths.water_path, "farm_source": paths.farm_path, "apcost_source": paths.apcost_path,
    }
    # notes（profile 的 notes 字段；一行摘要）
    q0 = queue[0] if queue else None
    meta["notes"] = ("由 mas_plan_builder 生成 %s；目标 %s；主刷 %s；用药 %d 瓶；活动优先 %s；"
                     "停刷=理智耗尽/任务自然结束；详见 runtime/reports/plans/ 同名 .md"
                     % (meta["generated_at"], q0["material"] if q0 else "-", slots[0],
                        int(args.medicine or 0), "开" if ev.get("enabled") else "关"))

    prof = build_profile(account, sc["script"], slots, args, {
        "activity_enabled": ev.get("enabled"),
        "notes": meta["notes"],
        "generated_at": meta["generated_at"],
        "mode": mode,
        "queue": meta["queue"],
        "probe_needed": probe_needed,
        "box": box,
        "events": meta["events"],
        "budget_per_day": sanity["budget_per_day"],
        "cap_built_at": cap.built_at,
        "inputs": [os.path.relpath(paths.cap_path, paths.root), os.path.relpath(paths.water_path, paths.root)],
    })
    norm, warns, errs = validate_profile_with_tool(prof)
    if errs:
        raise Refuse("生成的 profile 未通过 mas_run_profile 校验（rc 应为 0）：\n  - " + "\n  - ".join(errs))
    meta["profile_warnings"] = warns

    stamp = now.strftime(STAMP_FMT)
    base_name = "%s_%s" % (account, stamp)
    json_abs = os.path.join(paths.plans_dir, base_name + ".json")
    md_abs = os.path.join(paths.plans_dir, base_name + ".md")
    json_rel = _rel(paths.root, json_abs)

    # 证据链
    chain = []
    dl = products["depot_path"]
    ol = products["operators_path"]
    chain.append("缺口 ← 上号识别产物：`%s`（fetched_at=%s，uid=%s）与水位表 `%s`；"
                 "缺口 = max(0, 水位 − 仓库)，逐材料见 §2。"
                 % (_rel(paths.root, dl), depot.get("fetched_at") or "?", uid, _rel(paths.root, paths.water_path)))
    if mode == MODE_GAP:
        for t in targets:
            if t["gap"] > 0:
                chain.append("%s：仓库 %d（depot）< 水位 %d ⇒ 缺口 %d。" % (t["material"], t["owned"], t["target"], t["gap"]))
    else:
        chain.append("全部材料达标 ⇒ 补刀模式：取余量最小（%s，余量 %+d）继续刷，避免长期贴近水位。"
                     % (topping["material"], topping["surplus"]))
    if best:
        chain.append("候选关 ← `%s`（企鹅物流掉率，AP/个升序）：%s 的最优关 = %s（%g AP/个，AP 交叉核对 %s）；"
                     "AP 计价走 `%s` 的书写约定 unit_cost[\"<CODE>|普通\"]。"
                     % (_rel(paths.root, paths.farm_path), best and queue[0]["material"], best["stage"],
                        best["ap_per_item"], best["ap_source"] or "无（v4 未收录，用 farm_stages ap）",
                        _rel(paths.root, paths.apcost_path)))
        chain.append("台账闸门 ← `%s`（built_at=%s，窗口 %s~%s）：%s 该关 %s。"
                     % (_rel(paths.root, paths.cap_path), cap.built_at or "?",
                        (cap.window or {}).get("start", "?"), (cap.window or {}).get("end", "?"),
                        best["stage"], best["cap_note"]))
        exc = [c for it in queue for c in it["analyzed"]["excluded"]]
        if exc:
            chain.append("剔除的关（台账 failed，不排）：" + "；".join("%s（%s）" % (c["stage"], c["cap_note"]) for c in exc))
    chain.append("练度：%s" % bv["text"])
    chain.append("理智/药：%s；%s；%s" % (
        "每日预算 %g AP ⇒ 约 %s 次/天" % (sanity["budget_per_day"], sanity["runs_per_day"]),
        sanity["medicine_note"], "当前理智未知（未给 --sanity）" if args.sanity is None else "当前理智 %s" % args.sanity))
    chain.append("活动：%s（快照 %s，%s 天前；源 %s）" % (
        ev.get("verdict"), ev.get("generated_at_bjt") or "缺失",
        ev["age_days"] if ev.get("age_days") is not None else "?", _rel(paths.root, paths.events_path)))
    if probe_needed:
        _pn = probe_needed[:12]
        chain.append("需先试探一次的关（台账 unknown，可能没有代理权/30 天窗口无记录）：%s%s。"
                     % ("、".join(_pn), (" 等 %d 个" % len(probe_needed)) if len(probe_needed) > len(_pn) else ""))

    tldr = ["本轮主刷：**%s**（%s）" % (slots[0], queue[0]["material"] if queue else "-"),
            "候选回退位：%s" % (" → ".join(slots[1:]) if len(slots) > 1 else "无"),
            "用药：%s ｜ 活动优先：%s ｜ series：%s" % (
                sanity["medicine_note"], "开" if ev.get("enabled") else "关",
                "未指定" if args.series is None else args.series),
            "停刷条件：%s" % plan_stop_lines(ev)[0]]
    if probe_needed:
        tldr.append("⚠ 需先试探一次：%s（台账 unknown）" % "、".join(probe_needed[:5]))
    if bv["level"] == "warn":
        tldr.append("⚠ 练度启发式提示：%s" % bv["text"])

    plan = {"generated_at": now.strftime(TS_FMT), "account": account, "meta": meta, "targets": targets,
            "queue": queue, "slots": slots, "slot_why": slot_why, "chain": chain, "tldr": tldr,
            "box_lines": [bv["text"]], "sanity": sanity, "events": ev,
            "profile": prof, "validate": {"rc": 0, "warnings": warns}, "json_rel": json_rel,
            "script": sc["script"], "multi": sc.get("multi"), "enabled_users": [u["name"] for u in (sc.get("users") or [])
                                                                                if u.get("status")],
            "cmd_dry_run": _cmd(json_rel, False, args), "cmd_apply": _cmd(json_rel, True, args),
            "stop_lines": plan_stop_lines(ev), "risks": plan_risks(queue, cap, ev, args, sc, paths)}
    plan["json_abs"] = json_abs
    plan["md_abs"] = md_abs
    return plan


def plan_stop_lines(ev: dict) -> list:
    return [
        "MAA 任务自然结束（理智耗尽 / 药耗尽 / 关卡不可用跳过）——run profile 轮询 app.log「任务结束:<task_uid>」判完。",
        "时间盒：`restore_after=timeout:Ns`（本计划默认 run_end）⇒ 超时走 `dispatch/stop` 止损再还原。",
        "人工/lead 中止：停任务用 `tools/mas_ondemand.py --stop <task_uid>`（或 run profile 的 stop 路径）。",
        "「刷够 N 次」**不是**可用字段（AUTO-MAS 两层无总次数字段，简洁模式 EnableTimesLimit 硬关）："
        "§4 的「≈N 次」只是缺口估算，别当上限。",
        "跨 04:00 / 19:00 宿主计划重启窗口会中断任务（事件 id=1074）——长跑请错峰。",
    ]


def plan_risks(queue: list, cap: Capability, ev: dict, args, sc: dict, paths: Paths) -> list:
    risks = []
    unknown = [c["stage"] for it in queue for c in (it["analyzed"] or {}).get("candidates") or []
               if c["cap"] == STATUS_UNKNOWN]
    if unknown:
        risks.append("台账 unknown 的关（%s）：MAA 对『代理锁定』的关是**静默跳过**（不报错、当轮白排）⇒ "
                     "首轮务必人工看向导/记录，或先试探一次（演习/首战）。" % "、".join(sorted(set(unknown))[:6]))
    if not cap.report:
        risks.append("本次 --skip-capability 跳过了能力台账闸门 —— failed 关不会被自动剔除。")
    if ev.get("stale") or not ev.get("available"):
        risks.append("活动判断受限：%s" % ev.get("verdict"))
    if args.series is None:
        risks.append("series 未指定 ⇒ run profile 沿用该号当期值（连战倍数会随号不同）。")
    if args.medicine:
        risks.append("本轮会写 MedicineNumb=%d（UseMedicine=true）：请确认库存/是否想留给活动。" % args.medicine)
    if sc.get("multi") and sc["multi"] > 1:
        risks.append("多号脚本：默认会被 run profile 拒绝；要单号跑加 --disable-others，整脚本跑加 --all-accounts。")
    if not sc.get("users"):
        risks.append("脚本用户面未取到（REST 不可达或权限）⇒ 启用号数量/掩码未核（run profile 发车前会自己复核）。")
    risks.append("run profile 会新建临时计划表并还原（写面：计划表 + 该号 7 键差分 + Task 键）；"
                 "本工具不写任何 AUTO-MAS 配置，只产出草案文件。")
    return risks


def _pretty_server(token) -> str:
    k = server_key_of(token)
    return {"cn_official": "官服", "cn_bilibili": "B服", "jp": "日服", "kr": "韩服", "tw": "台服",
            "en": "国际服"}.get(k, str(token or "?"))


def _rel(root: str, p: str) -> str:
    try:
        return os.path.relpath(p, root).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


# ---------------------------------------------------------------- 执行入口
def exec_profile(paths: Paths, json_abs: str, apply_: bool, args) -> int:
    """调用 mas_run_profile.py（默认 dry-run）。--apply 里由 run profile 做 preflight/单飞/审计/还原。"""
    cmd = [sys.executable if sys.executable else "python", RUN_PROFILE, "--profile", json_abs]
    if apply_:
        cmd.append("--apply")
    if args.exec_preflight and not apply_:
        cmd.append("--preflight")
    for flag, on in (("--disable-others", args.disable_others), ("--all-accounts", args.all_accounts)):
        if on:
            cmd.append(flag)
    if args.write_mode != "plan":
        cmd += ["--write-mode", args.write_mode]
    if args.max_wait_seconds:
        cmd += ["--max-wait-seconds", str(args.max_wait_seconds)]
    audit(paths, {"event": "exec_apply" if apply_ else "exec_dry_run", "profile": _rel(paths.root, json_abs),
                  "cmd": " ".join(cmd)})
    print("[exec] %s" % " ".join(cmd), flush=True)
    rc = subprocess.call(cmd, cwd=paths.root)
    audit(paths, {"event": "exec_done", "apply": bool(apply_), "rc": rc})
    return rc


# ---------------------------------------------------------------- CLI
def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="刷取计划生成器：干员+仓库+能力台账+活动 -> 计划草案（.md 人读 + .json=run profile 可直接执行）")
    ap.add_argument("--account", help="账号名（runtime/reports/accounts/<账号>/ 必须有识别产物）")
    ap.add_argument("--script", help="脚本名/uid（默认自动解析：REST > 名册快照）")
    ap.add_argument("--materials", help="只排这些材料（逗号分隔，默认=水位表全部）")
    ap.add_argument("--material", action="append", metavar="材料=目标量",
                    help="改单材料水位（可重复；显式指定会强制进入计划）")
    ap.add_argument("--sanity", type=int, help="当前理智（可选；上号识别产物里没有理智，缺省只给预算估算）")
    ap.add_argument("--medicine", type=int, default=0, help="本轮吃药瓶数（默认 0=不吃；写 MedicineNumb）")
    ap.add_argument("--series", choices=["0", "1", "2", "3", "4", "5", "6", "-1"],
                    help="连战倍数（缺省不写该键，沿用该号当期值）")
    ap.add_argument("--restore-after", default="run_end", help="run profile 等待策略：run_end | timeout:Ns（默认 run_end）")
    ap.add_argument("--keep-remain/--no-keep-remain", dest="keep_remain", action="store_true", default=True,
                    help="保留该号「剩余理智」条目（默认保留：true=复制当日值）")
    ap.add_argument("--prefer", choices=["best", "proven"], default="best",
                    help="选关策略：best=最优效率（默认，unknown 也排并标注）；proven=只用台账已证的关")
    ap.add_argument("--no-tail-1-7", action="store_true", help="不补 1-7 尾位兜底（默认补：恒开放，只在候选全不可用时才轮到它）")
    ap.add_argument("--events-warn-age", type=float, default=3.0, help="活动快照超过 N 天告警（默认 3）")
    ap.add_argument("--events-fail-age", type=float, default=7.0, help="活动快照超过 N 天拒绝据此判断（默认 7）")
    ap.add_argument("--skip-capability", action="store_true", help="不读能力台账（不推荐：failed 关不会被剔除）")
    ap.add_argument("--no-rest", action="store_true", help="不碰 REST（只离线解析脚本名）")
    ap.add_argument("--rest-timeout", type=float, default=15.0, help="REST 单次超时秒")
    ap.add_argument("--out-dir", help="输出目录（默认 runtime/reports/plans）")
    ap.add_argument("--list-accounts", action="store_true", help="列出已有识别产物的账号")
    ap.add_argument("--selftest", action="store_true", help="离线自测（假数据；零触网、零写 runtime）")
    ap.add_argument("--json", action="store_true", help="机读输出（单行 JSON）")
    # 执行入口
    ap.add_argument("--exec-dry-run", action="store_true", help="生成后调 mas_run_profile.py 走 dry-run（零写）")
    ap.add_argument("--exec-preflight", action="store_true", help="--exec-dry-run 时顺带让它跑一次 preflight（只读）")
    ap.add_argument("--apply", action="store_true", help="真发车：调 mas_run_profile.py --apply（需用户/lead 批准）")
    ap.add_argument("--disable-others", action="store_true", help="转发：单号发车时临时停用同脚本其它启用号")
    ap.add_argument("--all-accounts", action="store_true", help="转发：接受整脚本跑")
    ap.add_argument("--write-mode", choices=["plan", "fixed"], default="plan", help="转发：run profile 的写路线")
    ap.add_argument("--max-wait-seconds", type=float, help="转发：restore_after=run_end 的等待上限")
    a = ap.parse_args(argv)
    return a


def cmd_list_accounts(paths: Paths) -> int:
    if not os.path.isdir(paths.accounts_dir):
        print("（还没有任何上号识别产物：%s）" % paths.accounts_dir)
        return 0
    for name in sorted(os.listdir(paths.accounts_dir)):
        d = paths.account_dir(name)
        op = os.path.exists(os.path.join(d, "operators.json"))
        dp = os.path.exists(os.path.join(d, "depot.json"))
        print("%-28s operators=%s depot=%s" % (name, "有" if op else "缺", "有" if dp else "缺"))
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.selftest:
        rep = selftest()
        if args.json:
            print(json.dumps(rep, ensure_ascii=False))
        else:
            print("SELFTEST=%s (%d/%d)" % ("OK" if rep["ok"] else "FAIL", rep["passed"], rep["passed"] + rep["failed"]))
            for c in rep["cases"]:
                print("  %s %s%s" % ("PASS" if c["ok"] else "FAIL", c["name"],
                                     "" if c["ok"] else "  ← " + c["detail"]))
        return 0 if rep["ok"] else 3
    paths = Paths(root=(os.environ.get("MAAORCH_PLAN_BUILDER_ROOT") or ROOT),
                  runtime=(os.environ.get("MAAORCH_PLAN_BUILDER_RUNTIME") or DEFAULT_RUNTIME))
    if args.out_dir:
        paths = _PathsWithOut(paths, args.out_dir)
    if args.list_accounts:
        return cmd_list_accounts(paths)
    if not args.account:
        print("缺 --account（或用 --list-accounts / --selftest）", file=sys.stderr)
        return 2
    try:
        plan = build_all(paths, args)
    except Refuse as e:
        print("拒绝：%s" % e, file=sys.stderr)
        audit(paths, {"event": "plan_refused", "account": args.account, "reason": str(e)[:300]})
        return 2
    # 写盘
    save_json(plan["json_abs"], plan["profile"])
    md = render_md(plan)
    os.makedirs(os.path.dirname(plan["md_abs"]), exist_ok=True)
    with open(plan["md_abs"], "w", encoding="utf-8") as f:
        f.write(md)
    audit(paths, {"event": "plan_built", "account": plan["account"], "script": plan["script"],
                  "stage_plan": plan["slots"], "mode": plan["meta"]["mode"],
                  "json": _rel(paths.root, plan["json_abs"]), "md": _rel(paths.root, plan["md_abs"]),
                  "probe_needed": plan["meta"]["probe_needed"], "activity_enabled": bool(plan["meta"]["activity_enabled"])})
    if args.json:
        print(json.dumps({"tool": "mas_plan_builder", "account": plan["account"], "script": plan["script"],
                          "stage_plan": plan["slots"], "md": _rel(paths.root, plan["md_abs"]),
                          "json": _rel(paths.root, plan["json_abs"]),
                          "primary": plan["queue"][0]["material"] if plan["queue"] else None,
                          "probe_needed": plan["meta"]["probe_needed"],
                          "activity_enabled": bool(plan["meta"]["activity_enabled"]),
                          "medicine": plan["profile"]["medicine"],
                          "warnings": plan["validate"]["warnings"]}, ensure_ascii=False))
    else:
        print("=== mas_plan_builder · %s（%s）===" % (plan["account"], plan["meta"]["server_name"]))
        for line in plan["tldr"]:
            print("- %s" % line)
        print("stage_plan = %s" % json.dumps(plan["slots"], ensure_ascii=False))
        print("草案：%s" % _rel(paths.root, plan["md_abs"]))
        print("profile：%s" % _rel(paths.root, plan["json_abs"]))
        print("执行（零写预览）：%s" % plan["cmd_dry_run"])
        print("执行（真发车，需批准）：%s" % plan["cmd_apply"])
        if plan["meta"]["profile_warnings"]:
            for w in plan["meta"]["profile_warnings"]:
                print("  ⚠(profile) %s" % w)
    if args.exec_dry_run or args.apply:
        if os.environ.get("MAAORCH_PLAN_BUILDER_NO_GO", "").strip().lower() in {"1", "true", "yes", "on"}:
            print("拒绝：MAAORCH_PLAN_BUILDER_NO_GO 置位（一票否决）", file=sys.stderr)
            return 2
        return exec_profile(paths, plan["json_abs"], apply_=bool(args.apply), args=args)
    return 0


class _PathsWithOut(Paths):
    def __init__(self, base: Paths, out_dir: str):
        super().__init__(root=base.root, runtime=base.runtime)
        self._out = out_dir

    @property
    def plans_dir(self):
        return self._out


# ---------------------------------------------------------------- 自测
def _fixture(root: str, runtime: str) -> None:
    """写一套合成数据（只为 selftest；不碰真 runtime）。"""
    os.makedirs(os.path.join(runtime, "reports", "accounts", "TestA"), exist_ok=True)
    ops = {"account": "TestA", "uid": "12345", "server": "官服",
           "operators": (
               [{"id": "c%d" % i, "name": "干员%d" % i, "rarity": 6, "elite": 2, "level": 60, "potential": 1}
                for i in range(10)] +
               [{"id": "d%d" % i, "name": "练度低%d" % i, "rarity": 4, "elite": 1, "level": 50, "potential": 0}
                for i in range(5)]),
           "counts": {"owned": 15, "all": 400}}
    depot = {"account": "TestA", "uid": "12345", "server": "官服", "fetched_at": "2026-09-28T00:00:00+0800",
             "items": {"固源岩组": 54, "聚酸酯组": 68, "糖组": 999}}
    save_json(os.path.join(runtime, "reports", "accounts", "TestA", "operators.json"), ops)
    save_json(os.path.join(runtime, "reports", "accounts", "TestA", "depot.json"), depot)
    cap = {"schema": "maaorch.stage_capability/1", "built_at": "2026-09-28 00:00:00",
           "window": {"days": 30, "start": "2026-08-30", "end": "2026-09-28"},
           "stages": ["1-7", "SR-7", "CE-6"],
           "rows": [{"account": "TestA", "stage": "1-7", "status": "success", "last_success": "2026-09-27",
                     "success_n": 9, "fail_n": 0, "failures": [], "failure_types": {}},
                    {"account": "TestA", "stage": "SR-7", "status": "failed", "last_failure": "2026-09-27",
                     "success_n": 0, "fail_n": 3, "failure_types": {"stage_not_open": 3}, "impact": "capability"},
                    {"account": "TestA", "stage": "CE-6", "status": "success", "last_success": "2026-09-20",
                     "success_n": 2, "fail_n": 0, "failure_types": {}}],
           "unknown": {"TestA": [{"stage": "1-7", "tried": True}, {"stage": "SR-7", "tried": True}]}}
    save_json(os.path.join(runtime, "reports", "stage_capability.json"), cap)
    events = {"schema": "maaorch.server_events/1", "generated_at_bjt": "2026-09-28 00:00:00+08:00",
              "headline": "官服无刷关活动", "servers": {
                  "cn_official": {"activity_verdict": "无刷关活动", "active_events": [
                      {"name": "签到", "type": "签到", "start_bjt": "2026-09-23", "end_bjt": "2026-09-30"}],
                      "upcoming_events": [{"name": "矢量突破#3", "type": "限时活动（爬塔类）", "start_bjt": "2026-09-29 16:00"}]},
                  "jp": {"activity_verdict": "在刷活动关", "active_events": [
                      {"name": "人々、我々", "type": "SideStory（刷关）", "start_bjt": "2026-09-16", "end_bjt": "2026-09-30",
                       "source": "jp-official-4626"}], "upcoming_events": []},
                  "en": {"activity_verdict": "在刷活动关", "active_events": [
                      {"name": "PA", "type": "SideStory（刷关）", "start_bjt": "2026-09-16", "end_bjt": "2026-09-30",
                       "source": "maa-stage-activity"}], "upcoming_events": []}}}
    save_json(os.path.join(runtime, "reports", "server_events.json"), events)
    save_json(os.path.join(runtime, "reports", "mas_account_master_20260101.json"),
              {"generated_at": "2026-01-01 00:00:00",
               "accounts": [{"script": "明日方舟-测试-T", "game_name_cfg": "TestA", "server": "Official"}]})
    save_json(os.path.join(root, "config", "material_water_levels.json"),
              {"ap_budget_per_day": 280, "materials": {"固源岩组": 100, "聚酸酯组": 100, "糖组": 100}})
    save_json(os.path.join(root, "config", "farm_stages.json"), {
        "固源岩组": [{"stage": "10-6", "ap": 21, "rate": 0.76}, {"stage": "2-4", "ap": 12, "rate": 0.42},
                     {"stage": "SR-7", "ap": 21, "rate": 0.6}],
        "聚酸酯组": [{"stage": "7-4", "ap": 18, "rate": 0.55}],
        "糖组": [{"stage": "2-5", "ap": 12, "rate": 0.37}]})
    save_json(os.path.join(root, "data", "stage_apcost_v4.json"), {
        "_conventions": {"突袭书写约定": "test"},
        "unit_cost": {"10-6|普通": 21, "10-6|突袭": 21, "2-4|普通": 12, "SR-7|普通": 21, "7-4|普通": 18,
                      "2-5|普通": 12, "1-7|普通": 6},
        "stages": {"main_10-05": {"ap": 21, "code": "10-6", "type": "MAIN"},
                   "main_02-04": {"ap": 12, "code": "2-4", "type": "MAIN"},
                   "act54side_07": {"ap": 21, "code": "SR-7", "type": "ACTIVITY"},
                   "main_07-03": {"ap": 18, "code": "7-4", "type": "MAIN"},
                   "main_02-05": {"ap": 12, "code": "2-5", "type": "MAIN"},
                   "main_01-07": {"ap": 6, "code": "1-7", "type": "MAIN"}},
        "code_index": {"10-6": ["main_10-05"], "2-4": ["main_02-04"], "SR-7": ["act54side_07"],
                       "7-4": ["main_07-03"], "2-5": ["main_02-05"], "1-7": ["main_01-07"]}})


def selftest() -> dict:
    import tempfile
    cases = []

    def check(name, ok, detail=""):
        cases.append({"name": name, "ok": bool(ok), "detail": str(detail)})

    tmp = tempfile.mkdtemp(prefix="mas_plan_builder_selftest_")
    root = os.path.join(tmp, "repo")
    runtime = os.path.join(root, "runtime")
    _fixture(root, runtime)
    paths = Paths(root=root, runtime=runtime)
    now = dt.datetime(2026, 9, 28, 12, 0)

    args = parse_args(["--account", "TestA", "--no-rest", "--script", "明日方舟-测试-T", "--sanity", "210"])
    try:
        plan = build_all(paths, args, now=now)
    except Exception as e:  # noqa: BLE001
        check("build_all 生成计划", False, "%s: %s" % (type(e).__name__, e))
        return {"ok": False, "passed": 0, "failed": len(cases), "cases": cases}
    check("build_all 生成计划", True)
    prof = plan["profile"]
    check("stage_plan 非空且 <=4", 1 <= len(prof["stage_plan"]) <= 4, prof["stage_plan"])
    check("主刷=10-6（固源岩组缺口 46 折算 AP 最大）", prof["stage_plan"][0] == "10-6", prof["stage_plan"])
    check("SR-7 因台账 failed 被剔除", all(c["stage"] != "SR-7" for it in plan["queue"] for c in it["analyzed"]["candidates"]),
          [c["stage"] for it in plan["queue"] for c in it["analyzed"]["candidates"]])
    check("10-6 标注 unknown→需先试探", "10-6" in plan["meta"]["probe_needed"], plan["meta"]["probe_needed"])
    check("缺口计算 固源岩组=46", [t for t in plan["targets"] if t["material"] == "固源岩组"][0]["gap"] == 46)
    check("缺口计算 聚酸酯组=32", [t for t in plan["targets"] if t["material"] == "聚酸酯组"][0]["gap"] == 32)
    check("糖组达标不进队列", all(it["material"] != "糖组" for it in plan["queue"]))
    check("候选排序按 AP/个 升序", [c["ap_per_item"] for c in plan["queue"][0]["analyzed"]["candidates"]] ==
          sorted(c["ap_per_item"] for c in plan["queue"][0]["analyzed"]["candidates"]))
    check("AP 交叉核对走 unit_cost|普通", plan["queue"][0]["analyzed"]["best"]["ap_source"] == "unit_cost[10-6|普通]",
          plan["queue"][0]["analyzed"]["best"]["ap_source"])
    check("预估次数/AP（46÷0.76≈61 次×21=1281）",
          plan["queue"][0]["runs"] == 61 and plan["queue"][0]["ap_need"] == 1281,
          (plan["queue"][0]["runs"], plan["queue"][0]["ap_need"]))
    check("练度体检（箱 10×E2-60）", plan["meta"]["box"]["e2_60"] == 10 and plan["meta"]["box"]["grade"] == "中",
          plan["meta"]["box"])
    check("理智策略：210//21=10 次", plan["sanity"].get("runs_this_session") == 10, plan["sanity"])
    check("每日预算 280//21=13 次/天", plan["sanity"]["runs_per_day"] == 13, plan["sanity"])
    check("活动：官服无刷关→enabled=False", prof["activity_priority"]["enabled"] is False, prof["activity_priority"])
    check("profile 过 mas_run_profile 校验", plan["validate"]["rc"] == 0, plan["validate"]["warnings"])
    check("target.script 解析（--script 优先）", prof["target"]["script"] == "明日方舟-测试-T", prof["target"])
    check("medicine 默认不吃", prof["medicine"] == {"use": False, "count": 0, "use_expiring": False}, prof["medicine"])
    check("json 元数据块是下划线注释", "_plan_builder" in prof and prof["_plan_builder"]["mode"] == MODE_GAP)

    # 恒开放兜底：把候选都换成活动关（SR-7 被剔，这里直接构造）
    q_fake = [{"material": "X", "no_table": False, "analyzed": {"best": {"stage": "SR-9", "ap_per_item": 1.0,
              "cap_note": "", "always_open": False, "ap": 21, "rate": 1.0}, "candidates": [
              {"stage": "SR-9", "ap_per_item": 1.0, "always_open": False, "ap": 21, "rate": 1.0, "cap_note": ""}],
              "excluded": []}, "gap": 1, "runs": 1, "ap_need": 21}]
    slots, why = build_stage_slots(q_fake, tail_1_7=True)
    check("活动关候选 -> 补 1-7 尾位", slots == ["SR-9", "1-7"], (slots, why))
    slots2, _ = build_stage_slots(q_fake, tail_1_7=False)
    check("--no-tail-1-7 不补", slots2 == ["SR-9"], slots2)
    # 新口径：主刷是常驻关时 1-7 也必进（run profile 对缺 1-7 的计划告警）
    check("stage_plan 恒含 1-7（主刷为常驻关时亦然）", "1-7" in plan["slots"], plan["slots"])
    # 满 4 位时 1-7 挤掉末位填充
    q_full = [dict(q_fake[0]), {"material": "Y", "no_table": False, "analyzed": {
        "best": {"stage": "7-4", "ap_per_item": 2.0, "cap_note": "", "always_open": True, "ap": 18, "rate": 9.0},
        "candidates": [{"stage": "7-4", "ap_per_item": 2.0, "always_open": True, "ap": 18, "rate": 9.0,
                        "cap_note": ""}], "excluded": []}, "gap": 1, "runs": 1, "ap_need": 18}]
    q0x = q_full[0]
    q0x["analyzed"]["candidates"] = [
        {"stage": "SR-9", "ap_per_item": 1.0, "always_open": False, "ap": 21, "rate": 1.0, "cap_note": ""},
        {"stage": "SR-9-HARD", "ap_per_item": 1.1, "always_open": False, "ap": 21, "rate": 0.9, "cap_note": ""},
        {"stage": "SR-10", "ap_per_item": 1.2, "always_open": False, "ap": 21, "rate": 0.8, "cap_note": ""}]
    slots3, why3 = build_stage_slots(q_full, tail_1_7=True)
    check("满位时先给 1-7 留位（不挤掉，直接不让填充占满）",
          slots3 == ["SR-9", "SR-9-HARD", "SR-10", "1-7"] and sum(1 for w in why3 if "1-7" in w) == 1,
          (slots3, why3))

    # 写盘 + 回读校验
    save_json(plan["json_abs"], prof)
    back = load_json(plan["json_abs"], None)
    _, _, errs2 = validate_profile_with_tool(back)
    check("落盘 json 回读再校验（可直接执行）", back is not None and not errs2, errs2)
    md = render_md(plan)
    check("md 渲染含「依据 → 结论」链", "依据 → 结论" in md and "10-6" in md and "停刷条件" in md)

    # 显式材料覆盖
    a2 = parse_args(["--account", "TestA", "--no-rest", "--script", "X", "--material", "糖组=1200"])
    p2 = build_all(paths, a2, now=now)
    check("--material 覆盖强制入队（糖组 999<1200）", any(it["material"] == "糖组" for it in p2["queue"]),
          [(it["material"], it["gap"]) for it in p2["queue"]])

    # 全达标 -> 补刀
    a3 = parse_args(["--account", "TestA", "--no-rest", "--script", "X"])
    depot = load_json(os.path.join(runtime, "reports", "accounts", "TestA", "depot.json"))
    depot["items"] = {"固源岩组": 999, "聚酸酯组": 999, "糖组": 999}
    save_json(os.path.join(runtime, "reports", "accounts", "TestA", "depot.json"), depot)
    p3 = build_all(paths, a3, now=now)
    check("全达标 -> 补刀模式", p3["meta"]["mode"] == MODE_TOPPING, p3["meta"]["mode"])

    # 活动：日服在刷关活动 -> activity_priority.enabled=True
    depot["items"] = {"固源岩组": 54, "聚酸酯组": 68, "糖组": 999}
    depot["server"] = "日服"
    save_json(os.path.join(runtime, "reports", "accounts", "TestA", "depot.json"), depot)
    ops = load_json(os.path.join(runtime, "reports", "accounts", "TestA", "operators.json"))
    ops["server"] = "日服"
    save_json(os.path.join(runtime, "reports", "accounts", "TestA", "operators.json"), ops)
    a4 = parse_args(["--account", "TestA", "--no-rest", "--script", "X"])
    p4 = build_all(paths, a4, now=now)
    check("日服刷关活动 -> activity_priority.enabled=True", p4["profile"]["activity_priority"]["enabled"] is True,
          p4["events"].get("farming") or p4["events"].get("verdict"))
    # 官方源 vs 二手源（source=maa-stage-activity）都要在 caveat 里点名
    depot["server"] = "国际服"
    save_json(os.path.join(runtime, "reports", "accounts", "TestA", "depot.json"), depot)
    ops["server"] = "国际服"
    save_json(os.path.join(runtime, "reports", "accounts", "TestA", "operators.json"), ops)
    p4b = build_all(paths, a4, now=now)
    check("国际服刷关活动（二手源）→ enabled + caveat 点名",
          p4b["profile"]["activity_priority"]["enabled"] is True and "二手" in (p4b["events"].get("caveat") or ""),
          p4b["events"].get("caveat"))
    # 快照过期 -> 关闭 + 告警
    ev_doc = load_json(paths.events_path)
    ev_doc["generated_at_bjt"] = "2026-09-10 00:00:00+08:00"
    save_json(paths.events_path, ev_doc)
    p5 = build_all(paths, a4, now=now)
    check("活动快照 >7 天 -> 拒绝据此判断（enabled=False + stale）",
          p5["profile"]["activity_priority"]["enabled"] is False and p5["events"]["stale"] is True,
          p5["events"].get("verdict"))
    _fixture(root, runtime)  # 恢复 fixture（供后续用例）

    # 拒绝面：产物缺失 / 台账缺失
    os.remove(os.path.join(runtime, "reports", "accounts", "TestA", "depot.json"))
    try:
        build_all(paths, a4, now=now)
        check("产物缺失 -> Refuse", False, "没有拒绝")
    except Refuse as e:
        check("产物缺失 -> Refuse", "depot.json" in str(e), str(e)[:80])
    _fixture(root, runtime)
    os.remove(paths.cap_path)
    try:
        build_all(paths, a4, now=now)
        check("台账缺失 -> Refuse", False, "没有拒绝")
    except Refuse as e:
        check("台账缺失 -> Refuse", "能力台账" in str(e), str(e)[:60])
    p6 = build_all(paths, parse_args(["--account", "TestA", "--no-rest", "--script", "X", "--skip-capability"]),
                   now=now)
    check("--skip-capability 仍能出草案（并记风险）", bool(p6["slots"]) and
          any("能力台账闸门" in r for r in p6["risks"]), p6["risks"][:1])

    # 与 tools/stage_capability.py::can_farm 的口径交叉核对（先恢复台账）
    _fixture(root, runtime)
    try:
        sys.path.insert(0, TOOLS)
        import importlib
        sc_mod = importlib.import_module("stage_capability")
        mine = Capability.load(paths)
        same = all(sc_mod.can_farm("TestA", s, path=paths.cap_path) == mine.get("TestA", s)["status"]
                   for s in ("1-7", "SR-7", "CE-6", "9-9"))
        check("台账口径与 can_farm 一致", same,
              [(s, sc_mod.can_farm("TestA", s, path=paths.cap_path), mine.get("TestA", s)["status"])
               for s in ("1-7", "SR-7", "CE-6", "9-9")])
    except Exception as e:  # noqa: BLE001
        check("台账口径与 can_farm 一致", False, "%s: %s" % (type(e).__name__, e))

    # 执行命令构建（不执行）
    a5 = parse_args(["--account", "TestA", "--script", "X", "--exec-dry-run"])
    cmd = _cmd("runtime/reports/plans/x.json", False, a5)
    check("dry-run 命令不含 --apply", "--apply" not in cmd and "mas_run_profile.py" in cmd, cmd)
    cmd2 = _cmd("p.json", True, parse_args(["--account", "T", "--script", "X", "--disable-others"]))
    check("apply 命令带 --apply/--disable-others", "--apply" in cmd2 and "--disable-others" in cmd2, cmd2)

    # NO_GO 闸门：exec 面必须被一票否决（且不真的去调 run profile）
    exec_calls = []
    orig_exec = globals()["exec_profile"]
    globals()["exec_profile"] = lambda paths_, j, apply_, args_: (exec_calls.append((j, apply_)), 99)[1]
    try:
        os.environ["MAAORCH_PLAN_BUILDER_NO_GO"] = "1"
        os.environ["MAAORCH_PLAN_BUILDER_ROOT"] = root
        os.environ["MAAORCH_PLAN_BUILDER_RUNTIME"] = runtime
        rc = main(["--account", "TestA", "--no-rest", "--script", "X", "--exec-dry-run", "--out-dir",
                   os.path.join(tmp, "plans")])
    finally:
        globals()["exec_profile"] = orig_exec
        os.environ.pop("MAAORCH_PLAN_BUILDER_NO_GO", None)
        os.environ.pop("MAAORCH_PLAN_BUILDER_ROOT", None)
        os.environ.pop("MAAORCH_PLAN_BUILDER_RUNTIME", None)
    check("NO_GO 一票否决（exec 面 rc=2，未调 run profile）", rc == 2 and not exec_calls, (rc, exec_calls))

    passed = sum(1 for c in cases if c["ok"])
    return {"ok": passed == len(cases), "passed": passed, "failed": len(cases) - passed, "cases": cases}


if __name__ == "__main__":
    sys.exit(main())
