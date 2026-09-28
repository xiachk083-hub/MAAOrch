# -*- coding: utf-8 -*-
"""按场景发车器的离线测试（不连真实 MAS）。

守住的线：
  1) `build_plan`（dry-run 主路径）**一个写请求都不发**；
  2) 写端点白名单恰好两条，且未白名单的端点拒在网络之前；
  3) 合并规则：不改的不写 / 缺项不新增 / 切号字段不注入；
  4) 真发车双闸默认关闭（--apply 无 env ⇒ 计划即拒绝）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import mas_scenario_dispatch as m  # noqa: E402


def args_offline(**kw):
    base = dict(scenario="rogue", window="21:30-22:30", duration=None, template=None,
                apply=False, offline=True, script="明日方舟-官-1-1-7", instance=None,
                times=None, set=None, allow_no_preflight=False, force=False, json=False)
    base.update(kw)
    return argparse.Namespace(**base)


# --------------------------------------------------------------- 纯函数
def test_window_parsing_and_yield_zones():
    w = m.parse_window("22:30-03:30")
    assert (w["cross_midnight"], w["span_min"]) == (True, 300)
    assert m.yield_conflicts(w["start_min"], w["span_min"]) == []
    # 闭区间同口径：20:30 仍在让位区，20:31 起出区
    assert m.yield_conflicts(m.hhmm_to_min("20:30"), 30) == ["18:30-20:30"]
    assert m.yield_conflicts(m.hhmm_to_min("20:31"), 30) == []
    assert m.yield_conflicts(m.hhmm_to_min("05:00"), 30) == ["04:00-05:33"]


def test_window_dates_cross_midnight_pushes_one_day():
    base = dt.datetime(2026, 9, 27, 22, 30)
    s, e = m.window_dates(m.parse_window("22:30-03:30"), base)
    assert (s.isoformat(), e.isoformat()) == ("2026-09-27T22:30:00", "2026-09-28T03:30:00")


def test_scenario_aliases():
    assert m.resolve_scenario("肉鸽")[0] == "rogue"
    assert m.resolve_scenario("活动")[0] == "event"
    assert m.resolve_scenario("剿灭")[0] == "annihilation"
    with pytest.raises(ValueError):
        m.resolve_scenario("nope")


def test_task_patch_writes_all_ten_keys_explicitly():
    after = m.build_task_patch("rogue", {}, {"IfRoguelike": False, "IfFight": True})
    assert sorted(after) == sorted(m.TASK_KEYS)
    assert after["IfRoguelike"] is True and after["IfFight"] is False and after["IfAward"] is False


# --------------------------------------------------------------- 合并规则
def _doc(queue):
    return {"Configurations": {"Default": {"TaskQueue": queue}}}


def test_merge_hits_by_name_for_fight_family():
    doc = _doc([{"TaskType": "Fight", "Name": "理智作战", "IsEnable": True, "StagePlan": ["1-7"]},
                {"TaskType": "Fight", "Name": "剿灭作战", "IsEnable": False, "StagePlan": []}])
    doc, summ = m.merge_taskqueue(doc, [{"TaskType": "Fight", "Name": "剿灭作战",
                                         "StagePlan": ["Annihilation"]}])
    q = doc["Configurations"]["Default"]["TaskQueue"]
    assert q[1]["StagePlan"] == ["Annihilation"] and q[1]["IsEnable"] is True
    assert q[0]["StagePlan"] == ["1-7"]                      # 未命中的条目原样
    assert summ[0]["how"] == "TaskType+Name"


def test_merge_never_invents_items():
    doc = _doc([{"TaskType": "StartUp", "IsEnable": True}])
    doc, summ = m.merge_taskqueue(doc, [{"TaskType": "Roguelike", "StartCount": 1}])
    assert len(doc["Configurations"]["Default"]["TaskQueue"]) == 1
    assert "skip" in summ[0]


def test_merge_skips_account_switch_fields():
    doc = _doc([{"TaskType": "StartUp", "Name": "开始唤醒", "IsEnable": True,
                 "AccountName": "Masked_AB12", "AccountSwitchEnabled": True}])
    doc, summ = m.merge_taskqueue(doc, [{"TaskType": "StartUp", "Name": "开始唤醒",
                                         "AccountName": "", "AccountSwitchEnabled": False}])
    it = doc["Configurations"]["Default"]["TaskQueue"][0]
    assert (it["AccountName"], it["AccountSwitchEnabled"]) == ("Masked_AB12", True)
    assert sorted(summ[0]["skipped_policy"]) == ["AccountName", "AccountSwitchEnabled"]


def test_set_path_refuses_unknown_target():
    tgt = {"task": {}, "config": [{"TaskType": "Roguelike", "StartCount": 99999}], "times_path": None}
    ok, _ = m.set_path(tgt, "TaskQueue.NotExist.X", 1)
    assert ok is False and tgt["config"][0]["StartCount"] == 99999      # 不造字段
    ok, _ = m.set_path(tgt, "TaskQueue.Roguelike.StartCount", 1)
    assert ok is True and tgt["config"][0]["StartCount"] == 1


# --------------------------------------------------------------- 只读不变量
def test_build_plan_offline_never_calls_api(monkeypatch):
    calls = []

    def boom(*a, **k):                                          # 任何 API 调用都算越界
        calls.append((a, k))
        raise AssertionError("build_plan 不该触达 API：%r %r" % (a, k))

    monkeypatch.setattr(m, "api_post", boom)
    plan = m.build_plan(args_offline())
    assert calls == []
    assert plan["refused"] == []
    marks = {g["gate"]: g["status"] for g in plan["gates"]}
    assert marks.get("G4 让位区") == "GO"
    assert marks.get("G3 双闸") == "SKIP"


def test_apply_without_env_is_refused_and_readonly(monkeypatch):
    monkeypatch.setattr(m, "api_post",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应触网")))
    monkeypatch.delenv(m.APPLY_ENV, raising=False)
    monkeypatch.delenv(m.NO_GO_ENV, raising=False)
    plan = m.build_plan(args_offline(apply=True, offline=False))
    # 双闸未开 ⇒ 必须拒绝；且绝不出现在线写
    assert any("双闸" in r for r in plan["refused"])
    assert plan["refused"], "apply 无 env 必须被拒"


def test_write_endpoint_allowlist_exact():
    assert m.WRITE_ENDPOINTS == {"/api/scripts/user/update", "/api/dispatch/start"}
    with pytest.raises(RuntimeError):
        m.api_post("/api/scripts/delete", {}, writes=True, timeout=1)     # 白名单先抛，不触网
    with pytest.raises(RuntimeError):
        m.api_post("/api/queue/add", {}, writes=True, timeout=1)


def test_scrub_drops_credentials():
    raw = {"Info": {"Name": "a", "Password": "x", "SklandToken": "y"}, "n": 1}
    assert m.scrub(raw) == {"Info": {"Name": "a"}, "n": 1}


# --------------------------------------------------------------- 与并行产物对齐
TPL_DIR = ROOT / "runtime" / "templates" / "maa"


@pytest.mark.skipif(not (TPL_DIR / "roguelike.json").exists(), reason="模板库未落地")
def test_roguelike_template_loads_and_flags_6175_form():
    t = m.load_template(TPL_DIR / "roguelike.json")
    assert len(t["tasks"]) == 2
    assert m.template_form(t) == "6.17.5"            # 6.18.0 机队会 WARN（README §3）


@pytest.mark.skipif(not (TPL_DIR / "annihilation.json").exists(), reason="模板库未落地")
def test_single_object_templates_are_wrapped():
    for name in ("annihilation.json", "activity_priority.json"):
        t = m.load_template(TPL_DIR / name)
        assert len(t["tasks"]) == 1
        assert t["tasks"][0]["TaskType"] == "Fight"
        assert t["tasks"][0]["Name"] in ("剿灭作战", "活动关优先")
