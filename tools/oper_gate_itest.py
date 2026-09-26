#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**干员拥有性闸的调用方集成回归**（离线；不连 adb / 不 spawn MAA；2026-09-24 夜）。

验的是**队列侧**（`runtime/_prime_queue3.py`）与**闸**（`tools/stage_attempt.py`）之间那条缝：
  ① 队列的 `story_job_opers()` 现在能读**包装型**（老实现只读顶层 ⇒ 恒空表）；
  ② 队列的 `story_attempt()` 能把闸的判决从 JSON 里带回来（`gated/missing/verdict`）；
  ③ 端到端跑一次真集成：`story_attempt(u, 缺干员的轴)` ⇒ `gated=True`、退出码 7、
     **一个 MAA 都没起**（用 `tools/stage_attempt.py` 的 `--job` 分支真跑一次 —— 闸在
     spawn 之前返回，所以这一步**不碰设备**）。

用法: python tools/oper_gate_itest.py     （全绿返回 0；任一断言失败返回 1）
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                                 # noqa: BLE001
    pass
ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
PL = ROOT / "runtime" / "plans"
BAD = 0


def chk(name: str, cond: bool, detail: object = "") -> None:
    global BAD
    BAD += 0 if cond else 1
    print("  %s %s" % ("✓" if cond else "✗", name))
    if not cond:
        print("      got %s" % str(detail)[:200])


def load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    q = load("_prime_queue3_itest", ROOT / "runtime" / "_prime_queue3.py")
    sa = load("_maaorch_stage_attempt_itest", ROOT / "tools" / "stage_attempt.py")

    wrap = PL / "cvex" / "jobs" / "job_41427.json"              # 包装型（事件路径）
    flat = PL / "cvex" / "jobs" / "act28side_ex01_41496.json"    # 扁平型
    huang = PL / "story_jobs_big" / "7-5_23982.json"             # 需要未拥有的「煌」
    grouped = PL / "story_jobs_big" / "13-12_71775.json"         # 纯组轴（opers: []）

    # ① 读取器：包装型也要读出来（老实现恒返回 []）
    ops_w = q.story_job_opers({"path": wrap})
    ops_f = q.story_job_opers({"path": flat})
    chk("① 队列 `story_job_opers()` 读**包装型** → 7 名干员（老实现只读顶层 ⇒ 恒 []）",
        len(ops_w) == 7, ops_w)
    chk("① 队列 `story_job_opers()` 读**扁平型** → 1 名干员", ops_f == ["维什戴尔"], ops_f)
    chk("① 队列的读取器与闸**同一份实现**（`stage_attempt.axis_opers`）",
        ops_w == list(sa.axis_opers(wrap)[0]) and ops_f == list(sa.axis_opers(flat)[0]), "")

    # ② 判决分类：三桶
    chk("② `gated_bucket(axis-incompatible)=env-incompatible`",
        q.gated_bucket("axis-incompatible") == "env-incompatible", "")
    chk("② `gated_bucket(axis-group-unsat)=env-incompatible`",
        q.gated_bucket("axis-group-unsat") == "env-incompatible", "")
    chk("② `gated_bucket(axis-opers-unreadable)=data-gap`（与环境不兼容分开桶）",
        q.gated_bucket("axis-opers-unreadable") == "data-gap", "")
    chk("② `gated_bucket(allow)=\"\"`（放行不算拦下）", q.gated_bucket("allow") == "", "")

    # ③ 端到端：队列真的去起 stage_attempt ⇒ 闸在 spawn 前拦下（不碰设备）
    u = {"code": "7-5", "want": "普通", "stage_id": "main_07-05"}
    job = {"path": huang, "id": 23982, "match": 0, "stage_name": "main_07-05"}
    before = sa.MAA_SPAWN["n"]
    d = q.story_attempt(u, job, drill=True, tag="_opergate_itest_huang_drill")
    chk("③ 端到端：`story_attempt()` 认出闸的拦下（gated=True）", d["gated"] is True, d.get("verdict"))
    chk("③ 端到端：verdict=axis-incompatible 且 missing 里出现干员名「煌」",
        d["verdict"] == "axis-incompatible" and "煌" in (d["missing"] or []), d.get("verdict_tag"))
    chk("③ 端到端：桶 = 环境不兼容（**不降权**）", d["gate_bucket"] == "env-incompatible", "")
    chk("③ 端到端：**没有 spawn MAA**（计数不变）", sa.MAA_SPAWN["n"] == before, sa.MAA_SPAWN["n"])
    chk("③ 端到端：退出码 7、invalid（不是 3★、也不是 failed）",
        d["json"].get("exit") == 7 and d["json"].get("valid") is False, d["json"].get("exit"))
    chk("③ 端到端：**没有结算页读数**（没跑过就不该有 stars）", d["stars"] is None, d["stars"])
    chk("③ 端到端：不算「到不了 3★」（not3=False，别拿它降权）", d["not3"] is False, "")
    chk("③ 端到端：审计行落盘（每次判定一行）", (ROOT / "runtime" / "_oper_gate.jsonl").exists(), "")

    # ③b 纯组轴（opers 空 + 组）在队列侧也不该被判成「读不出」
    req_g = sa.axis_requirements(grouped)
    chk("③b 纯组轴（13-12_71775.json）不被判成 unreadable（`readable=True`）",
        req_g["readable"] is True and len(req_g["groups"]) == 3, req_g["shape"])
    chk("③b 组语义：3 个组都有成员拥有 ⇒ 放行（不误拦 fail-closed）",
        sa.oper_gate_verdict(req_g, sa.owned_opers()[0])["allow"] is True, "")
    print("\n集成回归：%s" % ("全绿 ✓" if not BAD else "**%d 项失败**" % BAD))
    return 1 if BAD else 0


if __name__ == "__main__":
    raise SystemExit(main())
