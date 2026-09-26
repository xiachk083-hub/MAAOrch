#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pending_reverify.py —— **⊘（报酬无源石 ⇒ 不打）待重验单位查询器**

为什么需要它（2026-09-24 审计员指出）：手工清单**结构上必然过期**。全库 ⊘ 已到 70 条，
横扫每几分钟就新写一条 —— 实测手工快照 `runtime/_ambiguous_skip_units.json`（20:01）就漏了
20:04 新写的 `CV-EX-7|普通 0.643` / `CV-EX-7|突袭 0.529`。
⇒ **清单必须是查询，不是快照**：每次现扫 `runtime/reports/*.jsonl` 重算。

待重验的行集 = `action == 'no_prime'` **且** 满足任一（任一可疑即入队）：
  ① `reward_primes is None`（压根没采 / 明写 null）—— 旧库 70 条 ⊘ 里 47 条是这种；
  ② `reward_score` 缺失或非数（读不到 ⇒ 判决只能靠猜）；
  ③ `reward_score > absent`（0.72）—— 分数说"说不定还有矿"，与"不打"的结论相冲；
  ④ `stars` 是整数且 `< 3` **且 `reward_score <= absent`（徽章不在）** —— 与"无矿"**内部矛盾**
     （没通关的关不可能没有首通源石）。⚠ 两个条件缺一不可：星数会误读（真通关读成 0★），
     只写 `stars<3` 会把已通关的健康行当成矛盾行（2026-09-24 时点 22 行；
     库在增长——2026-09-25 同条件已 38 行，**行数以 --json 实际输出为准，勿引用固定数**）。
  ⑤ `page_guard` 键缺失或为 null，**但只在 `ts >= 2026-09-24T12:15:48 (UTC)` 的行上**（新契约范围内）。
     ⚠️ **时间范围是必须的**（2026-09-24 与审计员逐单位对撞出的缺陷）：`page`/`page_guard`
     只从这个时刻起才写、历史行**永不改写** ⇒ 判据不带范围就是把**新契约的字段要求套在旧行**上，
     一个单位只要有过任何一行旧行就永远留在清单里（重验也清不掉）⇒ **清单每轮自己长大、永不终结**
     （与本轮修掉的 0.46/0.64 坏带是同一类错误：判决永远到不了终态）。

**另有一节 `partial`（只读、单独计数）**：`action == 'partial'` 的行
   = **徽章在（有矿）但没三星 ⇒ 源石未发**，是**还有确定收益**的目标；与 `no_prime`
   （可能已领 ⇒ 需重验）**性质不同，不许混算进同一个计数**（重试路径本来就会照打它：
   选关只跳过 `done`，`partial`/`failed` 照打 —— 实测 `AD-EX-3|普通` 分 0.998 就靠这条兜）。

阈值**单一来源** = `runtime/config/reward_gate.json`（缺文件时回退 0.75/0.72 并警告）。

用法：
    python tools/pending_reverify.py              # 回显 + 原子写 runtime/reports/pending_reverify.json
    python tools/pending_reverify.py --json       # 只打印 JSON（不写文件）
    python tools/pending_reverify.py --top 20     # 回显前 N 行（默认 10）
    python tools/pending_reverify.py --selftest    # 离线自测（合成行，不读真库；给 --selftest 用）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")            # Windows 控制台默认 GBK，中文会炸

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORTS = ROOT / "runtime" / "reports"
OUT = REPORTS / "pending_reverify.json"
GATE_CFG = ROOT / "runtime" / "config" / "reward_gate.json"
LEGACY_MANUAL = ROOT / "runtime" / "_ambiguous_skip_units.json"

_FALLBACK_PRESENT, _FALLBACK_ABSENT = 0.75, 0.72

# **页面守卫判据的时间范围**（2026-09-24 Lead/审计员逐单位对撞结论 + 达芬奇 2026-09-25 实测）：
#   全库第一条真正带 `page_guard` 的行 = **2026-09-24T12:15:48**（PV-3|普通）；
#   争议行 PV-2|普通 12:15:16 早 32 秒 ⇒ 契约的**实现**当时还不存在。
#   ⇒ 窗口必须从"合规**成为可能**"那一刻起算（12:15:48），不是从"被宣布"起算 ——
#   否则会自动生产一批任何补救都清不掉的条目。秒级比较（ts_key 只截到分钟，分不出 12:15:16/48）。
PAGE_GUARD_CONTRACT_FROM = "2026-09-24T12:15:48"    # 与 build_report 的 write_contract.applies_to 同一时刻

# partial 一节的行内说明（Lead 要求：理由必须写进行内说明）
PARTIAL_REASON = ("partial = 徽章在（有矿）但没三星 ⇒ 源石未发，属于还有确定收益的目标；"
                  "与 no_prime（可能已领 ⇒ 需重验）性质不同、**不许混算进同一个计数**。"
                  "另：两节名单**可重叠**（如 PV-EX-8|普通既在 pending-current 也在 partial-current）⇒ "
                  "**任何人不得把两节数字相加**，排产看并集去重后的单位集合。")


def load_thresholds() -> tuple[float, float, str]:
    """(present, absent, source)。读不到配置 ⇒ 回退内置值并在 stderr 警告（不许静默）。"""
    try:
        j = json.loads(GATE_CFG.read_text(encoding="utf-8"))
        p, a = float(j["present"]), float(j["absent"])
        if not p > a:
            raise ValueError(f"present({p}) 必须 > absent({a})")
        return p, a, str(j.get("source") or GATE_CFG)
    except Exception as e:                          # noqa: BLE001
        print(f"⚠️ 读不到 {GATE_CFG}（{e}）⇒ 回退内置 present={_FALLBACK_PRESENT}"
              f"/absent={_FALLBACK_ABSENT}（tools/panel_gate.py:28 原文）", file=sys.stderr)
        return _FALLBACK_PRESENT, _FALLBACK_ABSENT, f"内置回退（{GATE_CFG} 读不到）"


def ts_key(row: dict) -> str | None:
    """把行的 `ts` 归一成可比串（截到分钟）。**读不到/说不清 ⇒ None**，绝不抛异常。

    只有能证明"这行写在契约时刻之后"的行才受守卫字段要求约束；说不清的按历史行处理
    （宁可漏判字段，也不让清单因历史行永远清不掉）。
    """
    ts = row.get("ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):   # 纪元秒/毫秒
        try:
            v = float(ts)
            if abs(v) > 1e11:
                v /= 1000.0
            return _dt.datetime.fromtimestamp(v, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")
        except Exception:                            # noqa: BLE001
            return None
    if not isinstance(ts, str) or not ts.strip():
        return None
    s = ts.strip().replace("/", "-").replace(" ", "T")
    if s[-1:] in ("Z", "z"):
        s = s[:-1]
    return s[:16] if len(s) >= 16 else None


def ts_sec(row: dict) -> str | None:
    """把行的 `ts` 归一成秒级可比串（YYYY-MM-DDTHH:MM:SS）。读不清 ⇒ None。

    2026-09-25 必须品：窗口是 12:15:48（秒级），ts_key 只截到分钟，分不出
    PV-2|普通 12:15:16（契约实现还不存在）与 PV-3|普通 12:15:48（第一条合规行）。
    """
    ts = row.get("ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        try:
            v = float(ts)
            if abs(v) > 1e11:
                v /= 1000.0
            return _dt.datetime.fromtimestamp(v, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:                            # noqa: BLE001
            return None
    if not isinstance(ts, str) or not ts.strip():
        return None
    s = ts.strip().replace("/", "-").replace(" ", "T")
    if s[-1:] in ("Z", "z"):
        s = s[:-1]
    return s[:19] if len(s) >= 19 else None


def guard_required(row: dict) -> bool:
    """这一行是否**在新契约范围内**（⇒ 必须落 `page`/`page_guard`）。历史行不要求。

    秒级比较（窗口 2026-09-24T12:15:48）：ts 说不清 ⇒ 按历史行处理（保守）。
    """
    k = ts_sec(row)
    return k is not None and k >= PAGE_GUARD_CONTRACT_FROM


def is_int(v) -> bool:
    """**不许把 True/False 当整数**（bool 是 int 的子类，这里踩过就白踩）。"""
    return isinstance(v, int) and not isinstance(v, bool)


def audit_reasons(row: dict, absent: float) -> list[str]:
    """这一行为什么要重验（可多因）。空列表 = 凭据齐全，不必重验。

    ⚠️ **键「在场」与「显式为 null」是两件事**（Lead 2026-09-24）：
      · `reward_primes: null`（键在场）= 有语义读数："页面不是「关卡详情」⇒ 报酬区不在面板上"；
      · **键不在场** = 写的人压根没采（旧库 47/70 条是这种）。
    两者**都进待重验**（结论相同），但原因分开记，好让"写入契约"能被审计。
    字段缺到任何程度都不许崩（含整行不是 dict ⇒ 调用方先挡掉）。
    """
    why: list[str] = []
    if not isinstance(row, dict):                # 防御：非字典行（调用方已挡，这里兜底）
        return ["row-not-object"]
    if "reward_primes" not in row:
        why.append("reward_primes-key-missing")  # 键不在场：压根没采
    elif row.get("reward_primes") is None:
        why.append("reward_primes-null")         # 显式 null：页面不是「关卡详情」
    score = row.get("reward_score")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        why.append("score-missing")
    elif float(score) < 0:                      # -1 = 模板/ROI 读不到（哨兵）⇒ 当"没读到"
        why.append("score-missing")
    elif float(score) > absent:
        why.append(f"score>{absent}" if float(score) <= _FALLBACK_PRESENT else "score>=present")
    stars = row.get("stars")
    stars_i = stars if is_int(stars) else None            # 只认真整数（bool 不算）
    # ④ **内部矛盾**（"没通关"却"没有首通源石"）。⚠ 2026-09-24 收紧（达芬奇实测）：**只写
    #   `stars<3` 会捞进健康行** —— 星数会被误读（已通关的关读成 0★），实测 `stars==0 且
    #   primes is None` 的 27 行里有 **14 行 score 0.997~1.000（徽章在 = 真已通关）** ⇒ 矛盾要
    #   成立必须**再加"徽章不在"**（`score <= absent`）。分数读不到时不判（那是 ② 的事）。
    if stars_i is not None and stars_i < 3:
        if isinstance(score, (int, float)) and not isinstance(score, bool) and float(score) <= absent:
            why.append(f"stars={stars_i}<3-contradiction")
    # **页面守卫**（**只在契约时间范围内判**）：新契约（`ts >= PAGE_GUARD_CONTRACT_FROM`）要求
    #   每条 ⊘/unreliable 行落 `page`+`page_guard`；在范围内而键不在场或为 null ⇒ 判决不可审计
    #   ⇒ 一律进待重验（缺字段绝不静默放过）。
    #   **历史行不适用**（那时还没这个契约）：否则一个单位只要有过旧行就永远留在清单里、
    #   重验也清不掉、清单每轮自己长大 ⇒ 判决永远到不了终态。
    if guard_required(row) and row.get("page_guard") is None:
        why.append("page-guard-missing")
    return why


def activity_of(code) -> str:
    """活动 = 关卡代号的前缀（`CV-EX-7` → `CV`，`AD-1` → `AD`）。**code 缺失也不许崩**。"""
    c = str(code or "").strip()
    return c.split("-")[0] if c else "(未知)"


def scan(paths: list[pathlib.Path], absent: float) -> tuple[list[dict], dict]:
    """扫一批 jsonl，返回（待重验行, 统计）。**只读**，不改任何账本。

    2026-09-25（达芬奇三件套重放结论）：并集口径会把**陈旧行**永久钉住 ——
    同一单位的新行已是干净读数，旧行仍把它留在清单里（pending 36 里 32 个是这类）。
    ⇒ 额外保留 `latest_no_prime`（每单位最新一条同 action 行，按 ts_sec 比；只比同 action，
    一次 blocked 失败尝试顶不掉一条 no_prime 判决）与 `unit_action_ts`（每单位×每 action 的
    最新 ts，用于 partial 跨判决比较；blocked/failed 尝试不顶掉判决）。
    """
    rows: list[dict] = []
    stat = {"ledgers": 0, "lines": 0, "json_errors": 0, "no_prime": 0, "actions": {},
            "unreliable_rows": 0, "unreliable_units": [],
            # 只读附带：partial 一节 + 守卫判据的时间范围记账（**不进 no_prime 计数**）
            "partial_rows": 0, "partial_units": [], "partial": [],
            "guard_scope_rows": 0, "guard_scope_ts_unknown": 0,
            "latest_no_prime": {}, "latest_unreliable": {}, "latest_partial": {},
            "unit_action_ts": {}, "unreliable_detail": []}
    for p in paths:
        stat["ledgers"] += 1
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            stat["lines"] += 1
            try:
                r = json.loads(ln)
            except Exception:                        # noqa: BLE001
                stat["json_errors"] += 1
                continue
            if not isinstance(r, dict):              # **缺字段/畸形行不许崩**：非字典直接记账跳过
                stat["non_objects"] = stat.get("non_objects", 0) + 1
                continue
            act = r.get("action")
            stat["actions"][act] = stat["actions"].get(act, 0) + 1
            # 跨判决比较用的轻量时钟：每单位×每 action 的最新 ts（秒级串最大值；说不清记 ""）。
            # 用途只有 partial 跨 action 比较（done/干净 no_prime 是否晚于 partial）；
            # blocked/failed 是尝试/非终态，比较时显式忽略（见 build_report）。
            try:
                _uts = ts_sec(r) or ""
            except Exception:                            # noqa: BLE001
                _uts = ""
            if act in ("no_prime", "partial", "unreliable", "done", "failed", "blocked", "run"):
                _u = f'{r.get("code")}|{r.get("want")}'
                _m = stat["unit_action_ts"].setdefault(_u, {})
                if _uts >= str(_m.get(act) or ""):
                    _m[act] = _uts
            if act == "unreliable":                  # **同一单位的第二行不算第二份工作** ⇒ 一行一单位都记
                stat["unreliable_rows"] += 1
                u = f'{r.get("code")}|{r.get("want")}'
                if u not in stat["unreliable_units"]:
                    stat["unreliable_units"].append(u)
                # 最新一条同 action 行 + 重判输入（旧规则遗言行：分数/徽章/星，见 build_report）
                _prev = stat["latest_unreliable"].get(u)
                if _prev is None or _uts >= str(_prev.get("_ts") or ""):
                    stat["latest_unreliable"][u] = {"_ts": _uts, "reward_score": r.get("reward_score"),
                        "reward_primes": r.get("reward_primes"), "stars": r.get("stars"),
                        "ts": r.get("ts"), "ledger": p.name}
                stat["unreliable_detail"].append({"unit": u, "reward_score": r.get("reward_score"),
                    "reward_primes": r.get("reward_primes"), "stars": r.get("stars"),
                    "ts": r.get("ts"), "ledger": p.name})
            if act == "partial":
                # **只读一节**：partial = 徽章在（有矿）但没三星 ⇒ 源石未发，还有确定收益的目标。
                # 与 no_prime（可能已领 ⇒ 需重验）**性质不同** ⇒ 单独计数，绝不混算。
                stat["partial_rows"] += 1
                pu = f'{r.get("code")}|{r.get("want")}'
                if pu not in stat["partial_units"]:
                    stat["partial_units"].append(pu)
                stat["partial"].append({
                    "code": r.get("code"), "want": r.get("want"), "stage": r.get("stage"),
                    "activity": activity_of(r.get("code")), "unit": pu,
                    "reward_score": r.get("reward_score"),
                    "reward_primes": r.get("reward_primes"),
                    "reward_primes_key": "reward_primes" in r,
                    "reward_verdict": r.get("reward_verdict"),
                    "stars": r.get("stars"), "page": r.get("page"),
                    "page_guard": r.get("page_guard"), "ts": r.get("ts"), "ledger": p.name,
                    "why": [PARTIAL_REASON],      # 行内说明：为何单列、与 no_prime 有何不同
                })
                _pprev = stat["latest_partial"].get(pu)
                if _pprev is None or _uts >= str(_pprev.get("_ts") or ""):
                    stat["latest_partial"][pu] = {"_ts": _uts, "ts": r.get("ts"),
                        "reward_primes": r.get("reward_primes"), "ledger": p.name}
            if act != "no_prime":
                continue
            stat["no_prime"] += 1
            # 每单位最新一条同 action 行（干净/命中判据都留；比较键 ts_sec，说不清记 ""）——
            # pending-current 口径的输入。只比同 action：blocked 尝试顶不掉 no_prime 判决。
            _nu = f'{r.get("code")}|{r.get("want")}'
            _nprev = stat["latest_no_prime"].get(_nu)
            _nwhy = audit_reasons(r, absent)
            if _nprev is None or _uts >= str(_nprev.get("_ts") or ""):
                stat["latest_no_prime"][_nu] = {"_ts": _uts, "why": _nwhy, "ts": r.get("ts"),
                    "reward_score": r.get("reward_score"), "reward_primes": r.get("reward_primes"),
                    "reward_primes_key": "reward_primes" in r, "stars": r.get("stars"),
                    "page_guard": r.get("page_guard"), "ledger": p.name}
            if guard_required(r):                       # 守卫判据的适用范围（可审计）
                stat["guard_scope_rows"] += 1
            elif ts_key(r) is None:
                stat["guard_scope_ts_unknown"] += 1
            # **优雅退化**：字段缺到任何程度都只靠 .get()/in 判断，绝不 KeyError/TypeError
            why = audit_reasons(r, absent)
            if why:
                has_primes_key = "reward_primes" in r
                if has_primes_key:
                    stat["primes_key_present"] = stat.get("primes_key_present", 0) + 1
                else:
                    stat["primes_key_missing"] = stat.get("primes_key_missing", 0) + 1
                if r.get("page_guard"):
                    stat["page_guard_present"] = stat.get("page_guard_present", 0) + 1
                else:
                    stat["page_guard_missing"] = stat.get("page_guard_missing", 0) + 1
                rows.append({
                    "code": r.get("code"), "want": r.get("want"), "stage": r.get("stage"),
                    "activity": activity_of(r.get("code")),
                    "unit": f'{r.get("code")}|{r.get("want")}',
                    "reward_score": r.get("reward_score"),
                    # 三态 + **键在不在场**（键缺失 = 没采；显式 null = "页面不是关卡详情"）
                    "reward_primes": r.get("reward_primes"), "reward_primes_key": has_primes_key,
                    "reward_verdict": r.get("reward_verdict"),
                    "stars": r.get("stars"), "page": r.get("page"), "page_guard": r.get("page_guard"),
                    "needs_discriminator": r.get("needs_discriminator"),
                    "ts": r.get("ts"), "ledger": p.name, "why": why,
                })
    rows.sort(key=lambda x: (str(x.get("activity")), str(x.get("code")), str(x.get("want"))))
    return rows, stat


def by_key(rows: list[dict], key) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        k = key(r) if callable(key) else r.get(key)
        out[str(k)] = out.get(str(k), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def build_report(rows: list[dict], stat: dict, present: float, absent: float, src: str) -> dict:
    legacy = None
    try:
        legacy = {"file": str(LEGACY_MANUAL.relative_to(ROOT)).replace("\\", "/"),
                  "note": "旧的手工清单（Lead 20:01）—— **原文保留**在此以防丢证据；"
                          "它已过期（漏了 20:04 新增的两行），**请以本文件的 `rows` 为准**。",
                  "content": LEGACY_MANUAL.read_text(encoding="utf-8")}
    except Exception as e:                           # noqa: BLE001
        legacy = {"file": str(LEGACY_MANUAL), "note": f"读不到（{e}）", "content": None}
    return {
        "_note": "**由 tools/pending_reverify.py 生成，可随时重算**（手工清单结构上必然过期："
                 "全库 ⊘ 每几分钟就新写一条）。待重验 = action=='no_prime' 且任一："
                 "reward_primes 键缺失/显式 null、reward_score 缺失/非数/负数哨兵、score > absent、"
                 "stars 为整数且 <3 **且 score <= absent**（两条件合取，缺一即超集；与'无矿'矛盾）、"
                 "page_guard 缺失（**只限 ts >= "
                 + PAGE_GUARD_CONTRACT_FROM + " 的行 (UTC)**）。另有一节 `partial`"
                 "（action=='partial'）：徽章在（有矿）但没三星 ⇒ 源石未发、还有确定收益，"
                 "**单独计数、不与 ⊘ 混算**。",
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "generator": "tools/pending_reverify.py",
        "thresholds": {"present": present, "absent": absent, "source": src,
                       "config": "runtime/config/reward_gate.json"},
        "criteria": ["reward_primes 键不在场（没采）", "reward_primes 显式 null（页面不是「关卡详情」）",
                     "reward_score 缺失/非数/负数哨兵(-1)", f"reward_score > absent({absent})",
                     "stars 为整数且 < 3 **且 reward_score <= absent**（两条件合取，缺一即超集；与'无矿'矛盾）",
                     f"page_guard 键缺失或 null，**但只限 ts >= {PAGE_GUARD_CONTRACT_FROM} (UTC) 的行**"
                     "（新契约范围内的判决不可审计；历史行不适用 ⇒ 清单能自清空、不会自己长大）"],
        "guard_criterion_window": {
            "from": PAGE_GUARD_CONTRACT_FROM,
            "why": "page/page_guard 只从该时刻起才写、历史行永不改写。判据不带时间范围 ⇒ 把新契约"
                   "套在旧行上 ⇒ 一个单位只要有过旧行就永远清不掉（重验也清不掉）、清单每轮自己长大、"
                   "判决永远到不了终态（与 0.46/0.64 坏带同类）。",
            "ts_unknown_rows": "ts 读不到/说不清 ⇒ 按历史行处理（不判 page-guard-missing）；"
                               "两套计数见 `scanned.guard_scope_rows` / `scanned.guard_scope_ts_unknown`。",
        },
        "write_contract": {
            "keys_must_be_present": "新行**必须**显式落 reward_score / reward_primes（三态 true|false|null）/ "
                                    "page + page_guard / stars —— 缺字段要写 null，**不是不写这个键**。"
                                    "`reward_primes: null` 有语义（页面不是「关卡详情」⇒ 报酬区不在面板上）。"
                                    "`event_sweep.mjs::rewardAuditFields()` 是唯一写行者。",
            "applies_to": "**从 2026-09-24T12:15:48 起新写的行**（全库第一条真正带 page_guard 的行 = "
                          "PV-3|普通 12:15:48；争议行 PV-2|普通 12:15:16 早 32 秒，契约的**实现**当时还不存在 ⇒ "
                          "窗口从“合规成为可能”起算；秒级比较）；历史行（reward_primes 仅 27/74 有键、"
                          "page_guard 仅个别有）不要求合格 —— 它们**本来就该进这份待重验清单**。",
        },
        # ── current-verdict 口径（2026-09-25 达芬奇第二实现重放结论＋本工具第一实现）───
        # 并集（pending_rows/pending_units）把陈旧行永久钉住：同一单位新行已是干净读数，
        # 旧行仍把它留在清单里。current 口径 = 每单位只看**最新一条同 action**的行
        # （只比同 action：一次 blocked 失败尝试顶不掉一条 no_prime 判决）。
        "pending_current_units": sorted(u for u, v in (stat.get("latest_no_prime") or {}).items()
                                        if v.get("why")),
        "pending_current_detail": {
            u: {"why": v.get("why"), "ts": v.get("ts"), "ledger": v.get("ledger"),
                "reward_score": v.get("reward_score"), "stars": v.get("stars")}
            for u, v in (stat.get("latest_no_prime") or {}).items() if v.get("why")},
        # unreliable 重判（旧规则遗言行：分数全在已删 0.46–0.64 带里；最新行规则清不掉它们，
        # 因为最新行**就是** unreliable）。只重判**单调侧 absent**：首通源石一旦领了不可逆 ⇒
        # absent 之后永远 absent；present/窄带/非数**不许**重判（否则会把未领的矿判死）。
        "unreliable_rejudged_absent": sorted(
            u for u, v in (stat.get("latest_unreliable") or {}).items()
            if isinstance(v.get("reward_score"), (int, float))
            and not isinstance(v.get("reward_score"), bool)
            and v.get("reward_score") == v.get("reward_score")
            and v.get("reward_score") <= absent),
        "unreliable_kept": sorted(
            u for u, v in (stat.get("latest_unreliable") or {}).items()
            if not (isinstance(v.get("reward_score"), (int, float))
                    and not isinstance(v.get("reward_score"), bool)
                    and v.get("reward_score") == v.get("reward_score")
                    and v.get("reward_score") <= absent)),
        # partial-current：partial 留下 ⟺ 没有**更晚**的 done，也没有更晚的**干净** no_prime；
        # blocked/failed/run 是尝试/非终态，**不顶掉**判决（EP-EX-4 最新任意行是 blocked 但判决是 done ⇒ 出列）。
        "partial_current_units": sorted(
            u for u, v in (stat.get("latest_partial") or {}).items()
            if not (((stat.get("unit_action_ts") or {}).get(u, {}).get("done") or "") >= (v.get("_ts") or ""))
            and not (((stat.get("latest_no_prime") or {}).get(u) or {}).get("_ts", "") >= (v.get("_ts") or "")
                     and not ((stat.get("latest_no_prime") or {}).get(u) or {}).get("why"))),
        "scanned": stat,
        # ── partial 一节（**独立计数**：与 no_prime 的待重验数不混算）────────────────────
        "partial_rows": stat["partial_rows"],
        "partial_units": len(stat["partial_units"]),
        "partial_unit_list": stat["partial_units"],
        "partial_unit_rows": by_key(stat["partial"], "unit"),
        "partial_by_activity": by_key(stat["partial"], "activity"),
        "partial_note": PARTIAL_REASON,
        "partial_rows_detail": stat["partial"],
        "pending_rows": len(rows),
        "pending_units": len({r.get("unit") for r in rows}),
        "unreliable_rows": stat["unreliable_rows"],
        "unreliable_units": len(stat["unreliable_units"]),
        "unreliable_unit_list": stat["unreliable_units"],
        "unit_rows": by_key(rows, "unit"),          # 单位 → 行数（横扫会给同一单位反复写行）
        "by_activity": by_key(rows, "activity"),
        "by_reason": by_key(rows, lambda r: "+".join(r["why"])),
        "by_want": by_key(rows, "want"),
        "rows": rows,
        "legacy_manual_snapshot": legacy,
    }


def write_atomic(path: pathlib.Path, text: str) -> None:
    """原子写（tmp + os.replace）：另一个 bot 随时可能读这个文件，绝不给它半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".pending_reverify_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def run_selftest() -> int:
    """离线自测：合成 6 行，断言"该抓的抓到、干净的不抓、缺字段不崩"。不读真库、不写产物。"""
    present, absent, src = load_thresholds()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="pending_reverify_selftest_"))
    good = {"code": "CV-EX-7", "want": "普通", "action": "no_prime", "reward_score": 0.643,
            "reward_primes": False, "stars": 3, "page": "关卡详情", "page_guard": "ok",
            # ts 在**契约时刻之后** ⇒ 这一行受"必须落 page/page_guard"的新契约约束
            "ts": "2026-09-24T13:00:00.000Z"}
    cases = {
        "primes-null": {**good, "reward_primes": None, "code": "A-1"},
        "score-missing-key": {**good, "reward_primes": False, "code": "A-2"},
        "score-over-absent": {**good, "reward_primes": False, "reward_score": 0.73, "code": "A-3"},
        "stars-contradiction": {**good, "reward_primes": False, "reward_score": 0.638,
                                "stars": 0, "code": "A-4"},
        # **缺字段优雅退化**：整行只有身份 + action（旧库里 reward_primes 键压根不在场的那种）
        "all-keys-missing": {"code": "A-5", "want": "普通", "action": "no_prime"},
        "clean": good,
    }
    del cases["score-missing-key"]["reward_score"]
    del cases["primes-null"]["page_guard"]          # 显式 null 的三态行 / 守卫字段缺失
    f = tmp / "event_sweep_selftest_20260924_0000.jsonl"
    f.write_text("\n".join(json.dumps(v, ensure_ascii=False) for v in cases.values()) + "\n",
                 encoding="utf-8")
    rows, _stat = scan([f], absent)
    caught = {r["unit"] for r in rows}
    want = {"A-1|普通", "A-2|普通", "A-3|普通", "A-4|普通", "A-5|普通"}
    ok = caught == want and len(rows) == 5
    # 边界 ①：分数正好 = absent ⇒ 不抓（那是"确信不在"）；干净行也不抓
    edge = {**good, "code": "B-1", "reward_score": absent, "reward_primes": False, "stars": 3}
    f2 = tmp / "event_sweep_selftest_20260924_0001.jsonl"
    f2.write_text(json.dumps(edge, ensure_ascii=False) + "\n", encoding="utf-8")
    edge_rows, _ = scan([f2], absent)
    ok = ok and not edge_rows
    # 边界 ②：**畸形行不许崩**（裸数组、字符串、坏 JSON 各一条 ⇒ 计数进 stat，不抛异常）
    f3 = tmp / "event_sweep_selftest_20260924_0002.jsonl"
    f3.write_text("[1,2,3]\n\"just a string\"\n{not json}\n", encoding="utf-8")
    junk_rows, junk_stat = scan([f3], absent)
    ok = ok and not junk_rows and junk_stat["json_errors"] + junk_stat.get("non_objects", 0) == 3
    # 边界 ③：**清单必须能自清空**（2026-09-24 Lead/审计员的逐单位对撞结论）——
    #   同一单位：一行历史行（契约时刻前、缺 page_guard 键）+ 一行新的合规行 ⇒ 该单位**不在**清单里；
    #   同一单位若**新行**也缺 page_guard ⇒ 仍要在（新契约必须被审）。判定逻辑其余部分未动。
    hist = {"code": "C-1", "want": "普通", "action": "no_prime", "reward_score": 0.47,
            "reward_primes": False, "stars": 3, "page": "关卡详情",
            "ts": "2026-09-24T08:00:00.000Z"}                       # 历史行：**没有** page_guard 键
    new_ok = {**hist, "ts": "2026-09-24T13:00:00.000Z", "page_guard": "ok"}
    f4 = tmp / "event_sweep_selftest_20260924_0003.jsonl"
    f4.write_text(json.dumps(hist, ensure_ascii=False) + "\n" + json.dumps(new_ok, ensure_ascii=False) + "\n",
                  encoding="utf-8")
    got4, _s4 = scan([f4], absent)
    self_clearing = not got4                                    # 该单位已凭新行自清空
    new_bad = {**hist, "ts": "2026-09-24T13:00:00.000Z"}         # 新行也缺守卫 ⇒ 仍要被抓
    f5 = tmp / "event_sweep_selftest_20260924_0004.jsonl"
    f5.write_text(json.dumps(hist, ensure_ascii=False) + "\n"
                  + json.dumps(new_bad, ensure_ascii=False) + "\n", encoding="utf-8")
    got5, _s5 = scan([f5], absent)
    scope_ok = (len(got5) == 1 and got5[0]["unit"] == "C-1|普通"
                and got5[0]["why"] == ["page-guard-missing"])
    # 边界 ④：**partial 单独计数**（只读一节）——绝不许混进 no_prime 的待重验数
    part_row = {**good, "code": "AD-EX-3", "action": "partial", "reward_score": 0.998,
                "reward_primes": True, "stars": 0, "page": "关卡详情", "page_guard": "ok",
                "ts": "2026-09-24T11:47:12.429Z"}
    f6 = tmp / "event_sweep_selftest_20260924_0005.jsonl"
    f6.write_text(json.dumps(part_row, ensure_ascii=False) + "\n", encoding="utf-8")
    got6, s6 = scan([f6], absent)
    partial_ok = (not got6 and s6.get("partial_rows") == 1
                  and s6.get("partial_units") == ["AD-EX-3|普通"]
                  and s6.get("no_prime") == 0)
    # 键「在场 vs 显式 null」必须能被分开记（Lead ①）
    reasons_ok = (any("reward_primes-key-missing" in r["why"] for r in rows)
                  and any("reward_primes-null" in r["why"] for r in rows)
                  and any("page-guard-missing" in r["why"] for r in rows))
    ok = ok and reasons_ok
    # 边界 ⑤：**current-verdict 口径**（2026-09-25 达芬奇第二实现重放结论的第一实现）——
    # D-1 旧行被抓、新行干净 ⇒ 并集仍抓 D-1，但 pending_current 必须出列；
    # unreliable 旧带分数 0.464 ⇒ 重判 absent；present 侧 0.96 ⇒ 留下（单调性：只重判 absent 侧）；
    # partial 之后有 done ⇒ partial_current 出列；孤立 partial ⇒ 留下。
    d_old = {"code": "D-1", "want": "普通", "action": "no_prime", "reward_score": 0.47,
             "stars": 3, "page": "关卡详情", "ts": "2026-09-24T08:00:00.000Z"}  # 历史行：无 primes 键 ⇒ 被抓
    d_new = {**d_old, "reward_score": 0.643, "reward_primes": False,
             "page_guard": "ok", "ts": "2026-09-24T13:00:00.000Z"}              # 新行干净
    u_old = {"code": "PV-1", "want": "普通", "action": "unreliable", "reward_score": 0.464,
             "reward_primes": False, "stars": 3, "ts": "2026-09-24T12:14:02.167Z"}
    u_hi = {"code": "PV-9", "want": "普通", "action": "unreliable", "reward_score": 0.96,
            "reward_primes": False, "stars": 3, "ts": "2026-09-24T12:14:02.167Z"}
    p_then_done_old = {**good, "code": "PV-EX-3", "action": "partial", "reward_score": 0.998,
                       "reward_primes": True, "stars": 0, "ts": "2026-09-24T17:50:00.000Z"}
    p_then_done_new = {**good, "code": "PV-EX-3", "action": "done", "ts": "2026-09-24T18:44:00.000Z"}
    p_alone = {**good, "code": "AD-EX-3", "action": "partial", "reward_score": 0.998,
               "reward_primes": True, "stars": 0, "ts": "2026-09-24T11:47:12.429Z"}
    f7 = tmp / "event_sweep_selftest_20260924_0006.jsonl"
    f7.write_text("\n".join(json.dumps(v, ensure_ascii=False) for v in
                            [d_old, d_new, u_old, u_hi, p_then_done_old, p_then_done_new, p_alone]) + "\n",
                  encoding="utf-8")
    got7, s7 = scan([f7], absent)
    rep7 = build_report(got7, s7, present, absent, src)
    current_ok = ("D-1|普通" not in rep7["pending_current_units"]
                  and rep7["unreliable_rejudged_absent"] == ["PV-1|普通"]
                  and rep7["unreliable_kept"] == ["PV-9|普通"]
                  and rep7["partial_current_units"] == ["AD-EX-3|普通"])
    # 边界 ⑥：**窗口秒级** —— PV-2|普通 12:15:16（实现还不存在 ⇒ 不判守卫缺）vs
    # PV-3|普通 12:15:48（第一条合规行；它有 guard 键 ⇒ 本来就不被抓）。分钟级 ts_key 分不出这两行。
    pv2 = {"code": "PV-2", "want": "普通", "action": "no_prime", "reward_score": 0.47,
           "reward_primes": False, "stars": 3, "page": "关卡详情", "ts": "2026-09-24T12:15:16.000Z"}
    f8 = tmp / "event_sweep_selftest_20260924_0007.jsonl"
    f8.write_text(json.dumps(pv2, ensure_ascii=False) + "\n", encoding="utf-8")
    got8, _s8 = scan([f8], absent)
    window_ok = not got8   # PV-2 在窗口外 ⇒ 干净行不被抓
    ok = ok and current_ok and window_ok
    print(json.dumps({"selftest": {"ok": bool(ok), "caught": sorted(caught),
                                   "want": sorted(want),
                                   "clean_caught": sorted(u for u in caught if u.startswith("CV-EX-7")),
                                   "edge_absent_not_caught": not edge_rows,
                                   "junk_lines_handled": junk_stat["json_errors"]
                                                        + junk_stat.get("non_objects", 0),
                                   "key_vs_null_distinguished": reasons_ok,
                                   "guard_scope_window": PAGE_GUARD_CONTRACT_FROM,
                                   "self_clearing": bool(self_clearing),
                                   "new_row_missing_guard_flagged": bool(scope_ok),
                                   "partial_counted_separately": bool(partial_ok),
                                   "current_verdict_ok": bool(current_ok),
                                   "window_second_precision_ok": bool(window_ok),
                                   "present": present, "absent": absent}},
                     ensure_ascii=False))
    for p in (f, f2, f3, f4, f5, f6, f7, f8):
        p.unlink(missing_ok=True)
    tmp.rmdir()
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="只打印 JSON（不写文件）")
    ap.add_argument("--top", type=int, default=10, help="回显前 N 行（默认 10）")
    ap.add_argument("--selftest", action="store_true", help="离线自测（合成行）")
    a = ap.parse_args()
    if a.selftest:
        return run_selftest()

    present, absent, src = load_thresholds()
    paths = sorted(REPORTS.glob("*.jsonl"))
    rows, stat = scan(paths, absent)
    rep = build_report(rows, stat, present, absent, src)
    text = json.dumps(rep, ensure_ascii=False, indent=1) + "\n"
    if a.json:
        print(text)
        return 0
    write_atomic(OUT, text)
    print(f"== ⊘ 待重验（查询，不是快照）｜扫 {stat['ledgers']} 份账本 / {stat['lines']} 行"
          f"｜⊘ 共 {stat['no_prime']} 行 ⇒ **待重验 {rep['pending_rows']} 行 / {rep['pending_units']} 单位**"
          f"（**引用一律报单位数**：横扫会给同一单位反复写行）")
    print(f"   阈值：present={present} absent={absent}（单一来源 runtime/config/reward_gate.json）"
          f"｜产物：{OUT.relative_to(ROOT)}｜原子写 tmp→os.replace")
    print(f"   按活动（行）：{json.dumps(rep['by_activity'], ensure_ascii=False)}")
    print(f"   按原因（行）：{json.dumps(rep['by_reason'], ensure_ascii=False)}")
    print(f"   键在场/缺键：primes 键在场 {stat.get('primes_key_present', 0)} / 缺键 "
          f"{stat.get('primes_key_missing', 0)}｜page_guard 有 {stat.get('page_guard_present', 0)} / "
          f"缺 {stat.get('page_guard_missing', 0)}")
    top_units = list(rep["unit_rows"].items())[:a.top]
    print(f"   ⊘?（unreliable）现状：{rep['unreliable_rows']} 行 / **{rep['unreliable_units']} 单位**"
          f"（同一单位被横扫反复写行 ⇒ 报单位数）"
          f"：{json.dumps(rep['unreliable_unit_list'], ensure_ascii=False)}")
    print(f"   单位（行数）前 {len(top_units)}："
          + "、".join(f"{u}×{n}" for u, n in top_units))
    print(f"   前 {min(a.top, len(rows))} 行（单位 分数 徽章 星 守卫 原因 账本）：")
    for r in rows[:a.top]:
        primes = r["reward_primes"] if r["reward_primes_key"] else "（键缺失）"
        print(f"     {r['unit']}  score={r['reward_score']}  primes={primes}  stars={r['stars']}"
              f"  guard={r['page_guard'] or '（缺）'}  why={','.join(r['why'])}  {r['ledger']}")
    print(f"   守卫判据时间范围：只对 ts >= {PAGE_GUARD_CONTRACT_FROM} (UTC；本地 20:15:48) 的行判 page-guard-missing"
          f"（范围内 {stat['guard_scope_rows']} 行；ts 说不清按历史行处理 "
          f"{stat['guard_scope_ts_unknown']} 行）⇒ **清单能自清空**，不会因历史行永远长大")
    # ── current-verdict 口径（只读派生，不改并集口径的 rows 计数）────────────────────
    print(f"   current-verdict（每单位最新一条同 action 行）：pending {rep['pending_units']} 单位 ⇒ "
          f"**{len(rep['pending_current_units'])} 单位**："
          f"{json.dumps(rep['pending_current_units'], ensure_ascii=False)}")
    print(f"   unreliable 重判（只判单调侧 absent，present/窄带/非数留下）："
          f"{rep['unreliable_units']} 单位 ⇒ 重判 absent {len(rep['unreliable_rejudged_absent'])}"
          f"{json.dumps(rep['unreliable_rejudged_absent'], ensure_ascii=False)}"
          f"｜留下 {len(rep['unreliable_kept'])}"
          f"{json.dumps(rep['unreliable_kept'], ensure_ascii=False)}")
    print(f"   partial-current（done/干净 no_prime 更晚 ⇒ 出列；blocked/failed 不顶掉判决）："
          f"{rep['partial_units']} 单位 ⇒ **{len(rep['partial_current_units'])} 单位**："
          f"{json.dumps(rep['partial_current_units'], ensure_ascii=False)}")
    # ── partial（**只读一节、单独计数**）──────────────────────────────────────────────
    print(f"== ⚔ 待打（partial）：{rep['partial_rows']} 行 / **{rep['partial_units']} 单位**"
          f"｜按活动（行）：{json.dumps(rep['partial_by_activity'], ensure_ascii=False)}"
          f"（**与上面 ⊘ 的计数分开，不许混算**）")
    print(f"   理由（行内说明）：{PARTIAL_REASON}")
    print("   单位（行数）：" + ("、".join(f"{u}×{n}" for u, n in rep["partial_unit_rows"].items())
                                or "（无）"))
    for r in stat["partial"][:a.top]:
        primes = r["reward_primes"] if r["reward_primes_key"] else "（键缺失）"
        print(f"     {r['unit']}  score={r['reward_score']}  primes={primes}  stars={r['stars']}"
              f"  guard={r['page_guard'] or '（缺）'}  ts={r['ts']}  {r['ledger']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
