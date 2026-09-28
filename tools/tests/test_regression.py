#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""回归测试套件 —— 把 2026-09-27/28 的一批修复锁成可重跑用例（防补丁被覆盖后无声回归）。

覆盖（对齐 kanban t_19907707 卡面 6 项）：
  A 切号注入语义：官服 ⇒ AccountName=掩码 + AccountSwitchEnabled=true；非官服 ⇒ "" + false
    （规则实现 = tools/mas_switch_gate.py::evaluate；远端产物 = AutoProxy v2 注入，见 L2）
  B AutoProxy 注入的吃药/关卡字段：StagePlan / Series / MedicineCount / UseMedicine 与用户配置一致
    （判据实现 = tools/mas_run_profile.py::match_main/match_readback；含 09-28 实机 golden 样本）
  C 补跑闸门：mas_dispatch.py 白名单 / 单飞 / code==200+taskId 校验（坏参数拒绝路径）
  D ScriptConfig 语义：只装配置不跑链（源码级 + 文档级断言；我方工具永不发 ScriptConfig 车）
  E 台账/租约/快照：mas_ondemand_ledger 契约 + mas_run_profile 运行台账契约
  F 全工具离线自检（各 tools/*.py --selftest 的进程内版本）汇总 + 回归判据自身的核销用例
  L* 实机只读检查（仅 --live；REST+ssh，默认不跑）

用法：
    python tools/tests/test_regression.py                 # 离线层（秒级，零网络、零写入）
    python tools/tests/test_regression.py --live          # + 实机只读层（约 2–5 分钟）
    python tools/tests/test_regression.py --json          # 机器可读输出
    python tools/tests/test_regression.py --out R.json    # 存 JSON 报告
    python -m pytest tools/tests/test_regression.py -q    # pytest 也可跑（live 用例默认 skip）
    python tools/tests/test_regression.py --list          # 只列用例

末行 `REGRESSION=PASS|FAIL (…)` 供 cron/巡检 grep（与 SWITCH_GATE= / SELFTEST= 同风格）。

约束：默认零写入（runtime/ 只在临时目录里被 monkeypatch 顶替；--live 只做只读读取）；
      实机层不写任何配置、不发车、不改远端文件。详见 docs/REGRESSION.md。
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

try:  # pytest 可选：没有也照样能跑（本文件自带 runner）
    import pytest
except ImportError:  # pragma: no cover
    pytest = None

# ---------------------------------------------------------------- 骨架

_REGISTRY: list = []
_LIVE = os.environ.get("MAS_REGRESSION_LIVE", "").strip().lower() in {"1", "true", "yes", "on"}
_TIMEOUT_S = int(os.environ.get("MAS_REGRESSION_TIMEOUT", "420"))
_IN_RUNNER = False  # main() 置 True：区分「本文件自带 runner」与「pytest 收集」


class SkipCase(Exception):
    """用例主动跳过（环境不具备/未启用）。"""


def case(fn):
    _REGISTRY.append(fn)
    return fn


def _require_live():
    if _LIVE:
        return
    if pytest is not None and not _IN_RUNNER:
        pytest.skip("实机检查未启用（--live 或 MAS_REGRESSION_LIVE=1）")
    raise SkipCase("实机检查未启用（--live 或 MAS_REGRESSION_LIVE=1）")


@contextlib.contextmanager
def _patched(mod, **attrs):
    """临时替换模块级全局（monkeypatch 的免 pytest 版）。"""
    saved = {k: getattr(mod, k) for k in attrs}
    for k, v in attrs.items():
        setattr(mod, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(mod, k, v)


@contextlib.contextmanager
def _sandbox(prefix="regression_"):
    d = tempfile.mkdtemp(prefix=prefix)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _ck(cond, msg):
    if not cond:
        raise AssertionError(msg)


@contextlib.contextmanager
def _quiet():
    """压掉被调用工具自检的 stdout/stderr（它们会刷自己的用例表；失败详情由断言带出）。"""
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield


def _subprocess(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout or _TIMEOUT_S,
                          cwd=str(ROOT), encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- ssh 只读工具（live 层用）
# 与 tools/mas_switch_gate.py::_ssh_ps 同款：EncodedCommand(utf-16-le base64) + stdin=DEVNULL。
# 坑（2026-09-27 实测）：在 MCP stdio server 里跑时 ssh 若继承父进程 stdin 会挂到超时。
SSH_KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")
HOST_MAS = r"E:\AUTO-MAS"


def _ssh_ps(script: str, timeout: int = 240) -> str:
    prefix = ("$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; "
              "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
    enc = (prefix + script).encode("utf-16-le")
    b64 = base64.b64encode(enc).decode("ascii")
    cmd = ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
           SSH_HOST, "powershell -NoProfile -EncodedCommand " + b64]
    r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


def _fetch_remote_b64(paths: list) -> dict:
    """只读取回远端文件：{path: bytes}。base64 过 ssh，避开控制台编码坑。"""
    lit = "\n".join("  '%s'" % p for p in paths)
    out = _ssh_ps("$files = @(\n%s\n)\nforeach ($f in $files) {\n"
                  "  $b = [IO.File]::ReadAllBytes($f)\n"
                  "  Write-Output ('B64|' + $f + '|' + [Convert]::ToBase64String($b))\n}\n"
                  "Write-Output 'END'\n" % lit)
    got = {}
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("B64|"):
            continue
        _, p, b64 = line.split("|", 2)
        with contextlib.suppress(Exception):
            got[p] = base64.b64decode(b64)
    return got


# ================================================================ A 切号注入语义
# 规则出处：tools/mas_switch_gate.py 头注（v2, 2026-09-27；补丁 = AUTO-MAS AutoProxy.py L766-779）

def _gate_fi(name="MAA_1", client="Official", acct="134****5276", sw=True, mtime="2026-09-28 07:00:00"):
    return {"name": name, "path": "E:\\MAA-Multi\\" + name, "status": "OK", "clientType": client,
            "tasks": [{"accountName": acct, "switchEnabled": sw}], "mtime": mtime}


def _gate_slot(masks=("134****5276",), servers=("Official",), odd=()):
    return {"path": "E:\\MAA-Multi\\MAA_1", "name": "MAA_1", "servers": set(servers),
            "masks": set(masks), "odd_official_id_lens": set(odd), "scripts": []}


@case
def test_A1_switch_gate_official_ok():
    """官服 = 掩码 + switch true ⇒ OK（这是注入补丁的静止标准态）。"""
    import mas_switch_gate as sg
    ev = sg.evaluate(_gate_slot(), _gate_fi())
    _ck(ev["verdict"] == "OK", "官服合规样本被判 %s：%s" % (ev["verdict"], ev["issues"]))
    _ck(ev["server"] == "Official" and ev["switch"] is True and ev["account"] == "134****5276", repr(ev))


@case
def test_A2_switch_gate_official_violations():
    """官服违例三条全检出：开关非 true / 掩码缺失（空） / 掩码格式不对。"""
    import mas_switch_gate as sg
    ev = sg.evaluate(_gate_slot(), _gate_fi(sw=False))
    _ck(ev["verdict"] == "VIOLATION" and "official-switch-not-true" in ev["issues"], repr(ev))
    ev = sg.evaluate(_gate_slot(masks=()), _gate_fi(acct=""))
    _ck(ev["verdict"] == "VIOLATION" and any(i.startswith("official-account-not-masked") for i in ev["issues"]), repr(ev))
    ev = sg.evaluate(_gate_slot(masks=()), _gate_fi(acct="1380245"))
    _ck(ev["verdict"] == "VIOLATION" and any(i.startswith("official-account-not-masked") for i in ev["issues"]), repr(ev))


@case
def test_A3_switch_gate_nonofficial_ok_and_violations():
    """非官服 = \"\" + false ⇒ OK；非空账号 / 开关 true ⇒ 违例。"""
    import mas_switch_gate as sg
    slot = _gate_slot(masks=(), servers=("Bilibili",))
    ev = sg.evaluate(slot, _gate_fi(client="Bilibili", acct="", sw=False))
    _ck(ev["verdict"] == "OK", "非官服合规样本被判 %s：%s" % (ev["verdict"], ev["issues"]))
    ev = sg.evaluate(slot, _gate_fi(client="Bilibili", acct="134****5276", sw=False))
    _ck(ev["verdict"] == "VIOLATION" and any(i.startswith("nonofficial-account-not-empty") for i in ev["issues"]), repr(ev))
    ev = sg.evaluate(slot, _gate_fi(client="Bilibili", acct="", sw=True))
    _ck(ev["verdict"] == "VIOLATION" and "nonofficial-switch-on" in ev["issues"], repr(ev))


@case
def test_A4_switch_gate_clienttype_normalization_and_unknown():
    """ClientType 三形态归一（int / 字符串枚举名 / 协议别名）；不认识 ⇒ UNKNOWN（fail-closed）。"""
    import mas_switch_gate as sg
    _ck(sg.normalize_server(0) == "Official", "int 0 应归一 Official")
    _ck(sg.normalize_server("3") == "YoStarJP", "字符串 '3' 应归一 YoStarJP")
    _ck(sg.normalize_server("JP") == "YoStarJP" and sg.normalize_server("official") == "Official", "别名归一失败")
    _ck(sg.normalize_server(True) is None and sg.normalize_server("nope") is None, "未知形态应返回 None")
    # 整数 ClientType 的官方槽同样要判成 Official（不能因为 int 就放行/误判）
    ev = sg.evaluate(_gate_slot(), _gate_fi(client=0, sw=False))
    _ck(ev["verdict"] == "VIOLATION" and "official-switch-not-true" in ev["issues"], repr(ev))
    ev = sg.evaluate(_gate_slot(), _gate_fi(client="???"))
    _ck(ev["verdict"] == "UNKNOWN", "未知 ClientType 应 UNKNOWN（fail-closed）")
    ev = sg.evaluate(_gate_slot(), {"name": "MAA_1", "status": "OK", "clientType": "Official", "tasks": []})
    _ck(ev["verdict"] == "UNKNOWN", "无 StartUp 任务应 UNKNOWN")


@case
def test_A5_switch_gate_module_selftest():
    """闸门自带离线自检（纯本地规则）必须全绿。"""
    import mas_switch_gate as sg
    with _quiet():
        rc = sg.selftest()
    _ck(rc == 0, "mas_switch_gate --selftest rc=%r" % (rc,))


# ================================================================ B 注入字段读回一致性
# 判据实现：tools/mas_run_profile.py（L1 = gui.new.json 的 TaskQueue 断言）

_GOLDEN_LIVE_OBSERVED = {  # 09-28 07:51 实机读回（docs/audits/2026-09-28_mas_run_profile_live.run.log）
    "StagePlan": ["1-7"], "Series": 1, "MedicineCount": 0, "UseMedicine": False, "IsEnable": True,
    "EnableTimesLimit": False, "EnableTargetDrop": False, "UseExpiringMedicine": True,
}


def _gui_doc(main=None, remain=None, startup=None, extra=()):
    tq = [{"TaskType": "StartUp", "Name": "开始唤醒",
           **(startup if startup is not None else {"AccountName": "134****5276", "AccountSwitchEnabled": True})}]
    if main is not None:
        tq.append({"TaskType": "Fight", "Name": "理智作战", "IsEnable": True, **main})
    if remain is not None:
        tq.append({"TaskType": "Fight", "Name": "剩余理智", "IsEnable": True, **remain})
    for it in extra:
        tq.append(it)
    return {"Configurations": {"Default": {"TaskQueue": tq}}, "Current": "Default"}


def _exp(**kw):
    base = {"stage_plan": ["1-7"], "series": 1, "medicine": 0, "use_medicine": False, "if_fight": True,
            "strict_simple": True, "remain": True, "remain_plan": ["1-7"], "mask": "134****5276"}
    base.update(kw)
    return base


@case
def test_B1_readback_parse_gui_classifies_entries():
    """parse_gui 认条目：理智作战=主战斗、剩余理智单列、剿灭/活动关不误当主战斗。"""
    import mas_run_profile as rp
    parsed = rp.parse_gui(_gui_doc(main=dict(_GOLDEN_LIVE_OBSERVED), remain={"StagePlan": ["1-7"], "Series": 1},
                                   extra=[{"TaskType": "Fight", "Name": "剿灭作战", "StagePlan": ["Annihilation"]}]))
    _ck(parsed["main"] and parsed["main"]["Name"] == "理智作战", "主战斗识别失败")
    _ck(parsed["remain"] and parsed["remain"]["Name"] == "剩余理智", "剩余理智识别失败")
    _ck(parsed["annihilation"] is not None and parsed["startup"]["AccountName"] == "134****5276", repr(parsed))
    # 只有剿灭（无理智作战）时不能把剿灭当主战斗
    parsed = rp.parse_gui(_gui_doc(main=None, extra=[{"TaskType": "Fight", "Name": "剿灭作战"}]))
    _ck(parsed["main"] is None and parsed["annihilation"] is not None, repr(parsed))


@case
def test_B2_readback_match_main_five_keys():
    """主战斗 5 键（StagePlan/Series/MedicineCount/UseMedicine/IsEnable）与用户配置一致 ⇒ 通过；
    逐键扰动必须各自报出 diff（这是「注入被改坏」的最小判据）。"""
    import mas_run_profile as rp
    obs, exp = dict(_GOLDEN_LIVE_OBSERVED), _exp()
    ok, diffs = rp.match_main(obs, exp)
    _ck(ok and diffs == [], "golden 样本应通过：%s" % diffs)
    for key, bad in (("StagePlan", ["CE-6"]), ("Series", 2), ("MedicineCount", 3),
                     ("UseMedicine", True), ("IsEnable", False)):
        o = dict(obs)
        o[key] = bad
        ok, diffs = rp.match_main(o, exp)
        _ck(not ok and any(key in d for d in diffs), "%s 扰动未报 diff：%s" % (key, diffs))
    # 类型宽容：Series 字符串 "1"、UseMedicine 0/1 不应误报（AutoProxy 两种序列化形态都出现过）
    ok, diffs = rp.match_main({**obs, "Series": "1", "UseMedicine": 0, "MedicineCount": "0"}, exp)
    _ck(ok, "字符串/0-1 形态被误报：%s" % diffs)


@case
def test_B3_readback_strict_simple_flags():
    """简洁模式硬关项（EnableTimesLimit / EnableTargetDrop）被打开 ⇒ 必须报 diff。"""
    import mas_run_profile as rp
    obs, exp = dict(_GOLDEN_LIVE_OBSERVED), _exp()
    ok, diffs = rp.match_main({**obs, "EnableTimesLimit": True}, exp)
    _ck(not ok and any("EnableTimesLimit" in d for d in diffs), repr(diffs))
    ok, diffs = rp.match_main({**obs, "EnableTargetDrop": True}, exp)
    _ck(not ok and any("EnableTargetDrop" in d for d in diffs), repr(diffs))
    # 非简洁口径（strict_simple=False）只记录不阻断
    ok, diffs = rp.match_main({**obs, "EnableTimesLimit": True}, _exp(strict_simple=False))
    _ck(ok, "非简洁口径不应因 EnableTimesLimit 阻断：%s" % diffs)


@case
def test_B4_readback_attribution_mask_and_mtime_gate():
    """多号归属：同值不同掩码按 StartUp.AccountName 归属；mtime 不晚于发车前 ⇒ stale 不算命中。"""
    import mas_run_profile as rp
    doc = _gui_doc(main=dict(_GOLDEN_LIVE_OBSERVED), remain={"StagePlan": ["1-7"], "Series": 1})
    parsed = rp.parse_gui(doc)
    parsed["mtime"] = "2026-09-28 07:51:19"
    exp = {"u1": _exp(mask="134****5276"), "u2": _exp(mask="151****9793")}
    uid, info = rp.match_readback(parsed, exp, ["u1", "u2"], {"__pre__": "2026-09-28 07:00:00"})
    _ck(uid == "u1", "掩码归属错：%r（%r）" % (uid, info))
    uid, info = rp.match_readback(parsed, exp, ["u1", "u2"], {"__pre__": "2026-09-28 08:00:00"})
    _ck(uid is None and info.get("stale"), "旧快照（mtime ≤ 发车前）不应命中：%r" % (info,))
    # 剩余理智条目缺失 ⇒ 不命中
    parsed2 = rp.parse_gui(_gui_doc(main=dict(_GOLDEN_LIVE_OBSERVED)))
    parsed2["mtime"] = "2026-09-28 07:51:19"
    uid, info = rp.match_readback(parsed2, {"u1": _exp()}, ["u1"], {"__pre__": "2026-09-28 07:00:00"})
    _ck(uid is None and info.get("remain_mismatch"), "缺剩余理智条目应不命中：%r" % (info,))
    # 不需要剩余理智（keep_remain=False）时，存在条目 ⇒ 不命中
    parsed3 = rp.parse_gui(_gui_doc(main=dict(_GOLDEN_LIVE_OBSERVED), remain={"StagePlan": ["1-7"]}))
    parsed3["mtime"] = "2026-09-28 07:51:19"
    uid, info = rp.match_readback(parsed3, {"u1": _exp(remain=False)}, ["u1"],
                                  {"__pre__": "2026-09-28 07:00:00"})
    _ck(uid is None and info.get("remain_mismatch"), "多出剩余理智条目应不命中：%r" % (info,))


@case
def test_B5_readback_golden_live_sample():
    """09-28 实机 golden：真实注入产物 + 真实期望 ⇒ 命中；任一键被改 ⇒ 不命中。
    （样本出处：docs/audits/2026-09-28_mas_run_profile_live.run.log 07:51:51 读回通过行）"""
    import mas_run_profile as rp
    parsed = rp.parse_gui(_gui_doc(main=dict(_GOLDEN_LIVE_OBSERVED),
                                   remain={"StagePlan": ["1-7"], "Series": 1}))
    parsed["mtime"] = "2026-09-28 07:51:19"
    uid, _ = rp.match_readback(parsed, {"u1": _exp()}, ["u1"], {"__pre__": "2026-09-28 07:00:00"})
    _ck(uid == "u1", "golden 样本应命中 u1")
    bad = dict(_GOLDEN_LIVE_OBSERVED, StagePlan=["SR-7"])          # 回退位丢失 = 理智药修复被打回
    parsed_bad = rp.parse_gui(_gui_doc(main=bad, remain={"StagePlan": ["1-7"], "Series": 1}))
    parsed_bad["mtime"] = "2026-09-28 07:51:19"
    uid, _ = rp.match_readback(parsed_bad, {"u1": _exp()}, ["u1"], {"__pre__": "2026-09-28 07:00:00"})
    _ck(uid is None, "StagePlan 缺回退位时必须不命中（否则等于放行无声回归）")


@case
def test_B6_run_profile_module_selftest():
    """mas_run_profile 自带离线全链路自检（假传输）必须全绿。"""
    import mas_run_profile as rp
    with _quiet():
        rep = rp.selftest()
    _ck(rep.get("ok"), "mas_run_profile.selftest 失败：%s" % json.dumps(
        [c for c in rep.get("cases", []) if not c.get("ok")], ensure_ascii=False)[:400])


# ================================================================ C 补跑闸门（mas_dispatch）

_OKR = {"code": 200, "status": "success", "message": "操作成功", "taskId": "task-ok"}


def _md_runtime(md, tmp):
    return _patched(md, RUNTIME=str(tmp), STATE=os.path.join(str(tmp), "state.json"),
                    LOG=os.path.join(str(tmp), "log.jsonl"), ALERTS=os.path.join(str(tmp), "alerts.json"),
                    LOCK=os.path.join(str(tmp), "lock"), NO_GO=False)


def _md_script(md, reach=(), posts=(), calls=None):
    reach, posts = list(reach), list(posts)

    def _reach():
        return reach.pop(0) if reach else (True, "code=200")

    def _post(path, body=None, timeout=30):
        if calls is not None:
            calls.append((path, body))
        item = posts.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return _patched(md, reachable=_reach, post=_post)


@case
def test_C1_dispatch_whitelist_blocks_before_any_io():
    """白名单：非「日常/晚班定时N(1..12)」一律拒，且拒在任何 IO 之前。"""
    import mas_dispatch as md
    with _sandbox() as tmp, _md_runtime(md, tmp):
        def _boom(*a, **k):
            raise AssertionError("白名单不过时不应有任何 REST 调用")

        with _patched(md, post=_boom, reachable=_boom):
            for bad in ("打杂任务", "日常定时0", "日常定时13", "晚班定时13", "日常定时", "日常定时1 ", "temp-queue"):
                r = md.dispatch(bad, "uid-1", True)
                _ck(r.get("refused") == "not_whitelisted", "%r 应被白名单拒：%r" % (bad, r))
            for good in ("日常定时1", "日常定时12", "晚班定时12"):
                _ck(md.QUEUE_RE.match(good), "白名单正则应放行 %s" % good)


@case
def test_C2_dispatch_no_go_and_dry_run_paths():
    """NO_GO 一票否决；dry-run 只探活不发车。"""
    import mas_dispatch as md
    with _sandbox() as tmp, _md_runtime(md, tmp):
        with _patched(md, NO_GO=True, post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("NO_GO 下不应发车"))):
            r = md.dispatch("日常定时1", "uid-1", True)
            _ck(r.get("refused") == "dispatch_disabled", repr(r))
        with _md_script(md, reach=[(True, "code=200")], posts=[_OKR]) as _:
            r = md.dispatch("日常定时1", "uid-1", False)
            _ck(r.get("dry_run") and r.get("would_dispatch") == "日常定时1", repr(r))
        with _md_script(md, reach=[(False, "connect refused")]):
            r = md.dispatch("日常定时1", "uid-1", False)
            _ck(r.get("refused") == "upstream_unreachable", repr(r))


@case
def test_C3_dispatch_singleflight_duplicate_today():
    """单飞：同队列当天已发过 ⇒ duplicate_today（不重复发车）；--force 可显式重发。"""
    import mas_dispatch as md
    with _sandbox() as tmp, _md_runtime(md, tmp):
        today = dt.date.today().isoformat()
        pathlib.Path(md.STATE).write_text(json.dumps(
            {today: {"日常定时1": {"shift": "daily", "at": "05:00:00", "taskId": "prev"}}}), encoding="utf-8")
        with _md_script(md, reach=[(True, "code=200")], posts=[_OKR]):
            r = md.dispatch("日常定时1", "uid-1", True)
            _ck(r.get("refused") == "duplicate_today" and r.get("first", {}).get("taskId") == "prev", repr(r))
        with _md_script(md, reach=[(True, "code=200")], posts=[_OKR]):
            r = md.dispatch("日常定时1", "uid-1", True, force=True)
            _ck(r.get("dispatched") and r.get("taskId") == "task-ok", repr(r))
        st = json.loads(pathlib.Path(md.STATE).read_text(encoding="utf-8"))
        _ck(st[today]["日常定时1"]["taskId"] == "task-ok", "成功发车应落台账")


@case
def test_C4_dispatch_lock_busy_and_stale_reclaim():
    """并发锁：被占 ⇒ concurrent_dispatch；陈旧锁（> LOCK_STALE_S）⇒ 回收后继续。"""
    import mas_dispatch as md
    with _sandbox() as tmp, _md_runtime(md, tmp):
        lock = pathlib.Path(md.LOCK)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("pid=999 ts=fake", encoding="utf-8")
        with _md_script(md, reach=[(True, "code=200")], posts=[_OKR]):
            r = md.dispatch("日常定时1", "uid-1", True)
            _ck(r.get("refused") == "concurrent_dispatch" and lock.exists(), repr(r))
        old = time.time() - (md.LOCK_STALE_S + 60)
        os.utime(lock, (old, old))
        with _md_script(md, reach=[(True, "code=200")], posts=[_OKR]):
            r = md.dispatch("日常定时1", "uid-1", True)
            _ck(r.get("dispatched") and not lock.exists(), repr(r))


@case
def test_C5_dispatch_response_contract_bad_params():
    """code==200 必须带 taskId 才算成功；坏参数/上游异常按分类表拒绝（不可重试类不重试）。"""
    import mas_dispatch as md
    r = md._classify_resp({"code": 200, "taskId": ""})
    _ck(r["reason"] == "no_taskid" and not r["retryable"], repr(r))
    r = md._classify_resp({"code": 200, "taskId": "T-9"})
    _ck(r["ok"] and r["taskId"] == "T-9", repr(r))
    r = md._classify_resp({"code": 500, "message": "ValueError: 任务 X 无法找到对应脚本配置"})
    _ck(r["reason"] == "target_not_found" and not r["retryable"], repr(r))
    r = md._classify_resp({"code": 500, "message": "RuntimeError: 任务 X 已在运行"})
    _ck(r["reason"] == "target_running" and r["retryable"], repr(r))
    r = md._classify_resp({"code": 500, "message": "任务 官-1-1-7 已被其他任务调度器锁定"})
    _ck(r["reason"] == "slot_occupied" and r["retryable"], repr(r))
    # 坏 uid（上游 ValueError）⇒ 不重试（终态），只试一次
    import urllib.error
    with _sandbox() as tmp, _md_runtime(md, tmp), _md_script(
            md, reach=[(True, "code=200")],
            posts=[{"code": 500, "message": "ValueError: badly formed hexadecimal UUID string"}]):
        r = md.dispatch("日常定时1", "bad-uid", True, retries=3, retry_delays=[1])
        _ck(r.get("reason") == "target_not_found" and r.get("attempts") == 1, repr(r))
    # 队列名不存在（need_resolve 路径）⇒ queue_missing，不重试
    with _sandbox() as tmp, _md_runtime(md, tmp), _md_script(
            md, reach=[(True, "code=200")], posts=[{"code": 200, "data": {}}]):
        r = md.dispatch("日常定时1", "", True, retries=3)
        _ck(r.get("reason") == "queue_missing" and r.get("attempts", 0) == 1, repr(r))


@case
def test_C6_dispatch_module_selftest():
    """mas_dispatch 自带离线自检（假传输）必须全绿。"""
    import mas_dispatch as md
    with _quiet():
        rep = md.selftest()
    _ck(rep.get("ok") and rep.get("failed") == 0, "mas_dispatch.selftest: %r" % rep)


# ================================================================ D ScriptConfig 语义
# 口径：ScriptConfig 模式 = 只装配配置、不自动跑任务链（编辑模式）；要跑链必须 AutoProxy/ManualReview。
# 我方工具一律不用它发车（tools/mas_ondemand.py 头注、docs/MAS_ONDEMAND.md 表、handbook 第 9 条）。

_MODE_TOOLS = ("mas_ondemand.py", "mas_ondemand_mcp.py", "mas_run_profile.py",
               "mas_scenario_dispatch.py", "mas_dispatch.py", "mas_probe_farm.py", "mas_plan_builder.py")


@case
def test_D1_tools_never_dispatch_scriptconfig():
    """我方发车工具发给 /api/dispatch/start 的 mode ⊆ {AutoProxy, ManualReview}；绝不以 ScriptConfig 发车。"""
    modes = set()
    with_windows = []
    for name in _MODE_TOOLS:
        src = (TOOLS / name).read_text(encoding="utf-8")
        windows = []
        for marker in ("dispatch/start", "dispatch_start"):
            for m in re.finditer(re.escape(marker), src):
                windows.append(src[m.start():m.start() + 260])   # 只认发车调用附近，避免混入 write_mode 等
        found = set()
        for w in windows:
            found |= set(re.findall(r'"mode"\s*:\s*"([A-Za-z]+)"', w))
        _ck("ScriptConfig" not in found, "%s 以 ScriptConfig 发车（口径：只装配置不跑链，本工具不用它）" % name)
        if found:
            with_windows.append(name)
        modes |= found
    _ck(len(with_windows) >= 4, "发车 mode 只在 %d 个工具里被扫到（正则失效？）：%r" % (len(with_windows), with_windows))
    _ck(modes <= {"AutoProxy", "ManualReview"}, "出现了非预期发车模式：%s" % sorted(modes))
    _ck({"AutoProxy", "ManualReview"} <= modes, "两个合法模式应都在用：%s" % sorted(modes))
    # 复用他人发车骨架的工具（probe_farm 借 run_profile）⇒ 继承其 mode 约束
    pf = (TOOLS / "mas_probe_farm.py").read_text(encoding="utf-8")
    _ck("mas_run_profile" in pf, "mas_probe_farm 不再复用 mas_run_profile 发车骨架？复核它的 mode 口径")


@case
def test_D2_scriptconfig_semantics_documented():
    """文档断言：ScriptConfig=只装配置不跑链；不要用它验证注入/等任务链。"""
    ondemand_src = (TOOLS / "mas_ondemand.py").read_text(encoding="utf-8")
    _ck("只装配配置不跑链" in ondemand_src, "mas_ondemand.py 头注丢了 ScriptConfig 语义")
    doc = (ROOT / "docs" / "MAS_ONDEMAND.md").read_text(encoding="utf-8")
    _ck("ScriptConfig" in doc and "只装配置" in doc, "docs/MAS_ONDEMAND.md 丢了 ScriptConfig 行")
    hb = (ROOT / "docs" / "MAS_MASTER_HANDBOOK.md").read_text(encoding="utf-8")
    _ck("别用 ScriptConfig 模式等任务链" in hb, "handbook 丢了「别用 ScriptConfig 等任务链」红线")
    _ck("不要用 ScriptConfig 验证" in hb, "handbook 丢了「不要用 ScriptConfig 验证」红线")


@case
def test_D3_writers_never_touch_switch_fields():
    """切号字段是 AutoProxy 注入面的专属职责：我方写配置的工具一律不碰
    AccountName/AccountSwitchEnabled（mas_run_profile 只写 7 键；scenario 合并显式跳过）。"""
    import mas_run_profile as rp
    import mas_scenario_dispatch as msd
    _ck("AccountName" not in rp.STAGE_KEYS and "AccountSwitchEnabled" not in rp.STAGE_KEYS,
        "mas_run_profile 的写入键混入了切号字段")
    _ck(tuple(msd.ACCOUNT_FIELDS) == ("AccountName", "AccountSwitchEnabled"), repr(msd.ACCOUNT_FIELDS))
    doc = {"Configurations": {"Default": {"TaskQueue": [
        {"TaskType": "StartUp", "Name": "开始唤醒", "AccountName": "Masked_AB12", "AccountSwitchEnabled": True}]}}}
    doc2, summ = msd.merge_taskqueue(doc, [{"TaskType": "StartUp", "Name": "开始唤醒",
                                            "AccountName": "", "AccountSwitchEnabled": False}])
    it = doc2["Configurations"]["Default"]["TaskQueue"][0]
    _ck((it["AccountName"], it["AccountSwitchEnabled"]) == ("Masked_AB12", True), "切号字段被改写：%r" % it)
    _ck(sorted(summ[0]["skipped_policy"]) == ["AccountName", "AccountSwitchEnabled"], repr(summ))


# ================================================================ E 台账 / 租约 / 快照契约

@case
def test_E1_ledger_key_of_mapping():
    """key 口径：q:/s:/a:/w: 四类（CLI 与 MCP 共用一本账的前提）。"""
    import mas_ondemand_ledger as lg
    _ck(lg.key_of("queue", "uid1") == "q:uid1", lg.key_of("queue", "uid1"))
    _ck(lg.key_of("q", "", "target1") == "q:target1", lg.key_of("q", "", "target1"))
    _ck(lg.key_of("script", "sid") == "s:sid" and lg.key_of("s", "sid") == "s:sid", "script key")
    _ck(lg.key_of("wake", "sid") == "w:sid" and lg.key_of("w", "sid") == "w:sid", "wake key")
    _ck(lg.key_of("account", "烤箱") == "a:烤箱", lg.key_of("account", "烤箱"))


@case
def test_E2_ledger_record_idempotent_and_supersede():
    """同 task_uid 幂等更新（不产生 prev 杂物）；不同 task_uid 新旧并存（旧进 prev）。"""
    import mas_ondemand_ledger as lg
    with _sandbox() as tmp, _patched(lg, RUNTIME=tmp):
        st = {}
        e1 = lg.record(st, "queue", "日常定时1", "q1", "T1", "cli", ts="2026-09-28 06:00:00")
        _ck(e1["task_uid"] == "T1" and e1["kind"] == "queue", repr(e1))
        e2 = lg.record(st, "queue", "日常定时1", "q1", "T1", "cli", ts="2026-09-28 06:01:00",
                       note="第二次读回")
        _ck(e2["ts"] == "2026-09-28 06:01:00" and not e2.get("prev"), "同 task_uid 不应产生 prev：%r" % e2)
        _ck(e2["note"] == "第二次读回" and e2["kind"] == "queue", repr(e2))
        e3 = lg.record(st, "queue", "日常定时1", "q1", "T2", "mcp", ts="2026-09-28 06:30:00")
        _ck(e3["task_uid"] == "T2" and len(e3.get("prev") or []) == 1, "新任务应顶替且留 prev：%r" % e3)
        _ck(e3["prev"][0]["task_uid"] == "T1", repr(e3["prev"]))
        # 老任务后到（乱序）⇒ 记录顺序按 ts 判定，新者为主
        e4 = lg.record(st, "queue", "日常定时1", "q1", "T0", "cli", ts="2026-09-28 05:00:00")
        _ck(e4["task_uid"] == "T2", "乱序旧记录不应顶替新记录：%r" % e4)
        lg.save(st)
        _ck(pathlib.Path(lg.ledger_path()).exists(), "save 未落盘")
        st2 = lg.load()
        _ck(st2["2026-09-28"]["q:q1"]["task_uid"] == "T2", "load 回读不一致")


@case
def test_E3_ledger_open_window():
    """busy 窗口：busy_until 未到 ⇒ open；过期/无 task_uid ⇒ 不算；被顶替记录在窗内仍可见。"""
    import mas_ondemand_ledger as lg
    now = dt.datetime(2026, 9, 28, 12, 0, 0)
    rec = {"task_uid": "T1", "ts": "2026-09-28 11:00:00", "busy_until": "2026-09-28 12:30:00"}
    _ck(lg.is_open(rec, now=now) is True, "窗口内应 open")
    _ck(lg.is_open({**rec, "busy_until": "2026-09-28 11:30:00"}, now=now) is False, "过期应 close")
    _ck(lg.is_open({**rec, "busy_until": None, "ts": "2026-09-28 11:59:00"}, now=now,
                   hold_min=150) is True, "缺 busy_until 时应退 ts+hold_min")
    _ck(lg.is_open({**rec, "task_uid": ""}, now=now) is False, "无 task_uid 不算在场")
    st = {"2026-09-28": {"q:q1": {"task_uid": "T2", "ts": "2026-09-28 11:30:00",
                                  "busy_until": "2026-09-28 13:00:00",
                                  "prev": [{"task_uid": "T1", "ts": "2026-09-28 11:00:00",
                                            "busy_until": "2026-09-28 12:15:00"}]}}}
    rows = lg.open_entries(st, now=now)
    _ck(len(rows) == 2, "被顶替但仍在窗内的记录也应列出：%r" % rows)
    _ck([r["task_uid"] for r in rows] == ["T1", "T2"], repr([r["task_uid"] for r in rows]))
    _ck(rows[0].get("superseded_by") == "T2", repr(rows[0]))


@case
def test_E4_ledger_legacy_merge_and_corrupt_selfheal():
    """旧账（CLI runs / MCP day-key）只读合并进新账本且幂等；坏账本隔离后空账自愈。"""
    import mas_ondemand_ledger as lg
    with _sandbox() as tmp, _patched(lg, RUNTIME=tmp):
        cli, mcp = lg.legacy_paths()
        pathlib.Path(cli).write_text(json.dumps({"runs": [
            {"target_kind": "queue", "queue_id": "q1", "target": "日常定时1", "taskId": "T1",
             "at": "2026-09-28 06:00:00", "resources": {"scripts": ["sid1"]}}],
            "temp_queues": [{"id": "tq1", "name": "临时-按需-1"}]}), encoding="utf-8")
        pathlib.Path(mcp).write_text(json.dumps({"2026-09-28": {
            "q:q2": {"taskId": "T2", "at": "2026-09-28 06:30:00", "kind": "queue", "target": "日常定时2"}}}),
            encoding="utf-8")
        st = lg.load()
        _ck(st["2026-09-28"]["q:q1"]["task_uid"] == "T1", "CLI 旧账未合并：%r" % st.get("2026-09-28"))
        _ck(st["2026-09-28"]["q:q2"]["task_uid"] == "T2", "MCP 旧账未合并")
        _ck(st["meta"]["legacy_merged"][cli]["records"] == 1, "合并指纹未记：%r" % st["meta"])
        _ck(any(x.get("id") == "tq1" for x in st["temp_queues"]), "旧临时队列未并入")
        calls = []
        with _patched(lg, _write=lambda s: calls.append(1)):
            lg.load()
        _ck(calls == [], "指纹未变时不应重复写账本（幂等）：%d 次" % len(calls))
    # 坏账本隔离 + 空账自愈（独立沙箱：无旧账可合并，load 结果应为空）
    with _sandbox() as tmp2, _patched(lg, RUNTIME=tmp2):
        pathlib.Path(lg.ledger_path()).write_text("{坏 JSON", encoding="utf-8")
        errs = []
        st = lg.load(on_error=errs.append)
        _ck(st.get("meta") is not None and not [k for k in st if lg.DATE_RE.match(str(k))],
            "坏账本应隔离并返回空账：%r %r" % (st, errs))
        _ck(errs, "坏账本应回调 on_error")
        _ck(list(pathlib.Path(tmp2).glob("mas_ondemand_ledger.json.corrupt-*")), "坏账本未隔离出 .corrupt-*")


@case
def test_E5_ledger_retention_prunes():
    """保留策略：>7 天的日期段被裁掉；temp_queues 上限 200。"""
    import mas_ondemand_ledger as lg
    with _sandbox() as tmp, _patched(lg, RUNTIME=tmp):
        today = dt.date.today().isoformat()
        old = (dt.date.today() - dt.timedelta(days=30)).isoformat()
        st = {old: {"q:x": {"task_uid": "T0"}}, today: {"q:y": {"task_uid": "T1"}},
              "temp_queues": [{"id": "tq%d" % i} for i in range(205)]}
        lg.save(st)
        st2 = lg.load()
        _ck(old not in st2, "过期日期段未裁掉")
        _ck(today in st2, "当天段被误裁")
        _ck(len(st2["temp_queues"]) == 200, "temp_queues 上限失效：%d" % len(st2["temp_queues"]))


@case
def test_E6_run_profile_state_contract():
    """运行台账（租约）契约：未还原记录必留、已还原只留最近 50；坏文件回退空账。"""
    import mas_run_profile as rp
    with _sandbox() as tmp:
        state = os.path.join(tmp, "state.json")
        with _patched(rp, STATE=state):
            runs = {"rp_open_%d" % i: {"status": "open"} for i in range(2)}
            runs.update({"rp_rest_%03d" % i: {"status": "restored"} for i in range(60)})
            rp.save_state({"runs": dict(runs)})
            st = rp.load_state()
            _ck(sum(1 for v in st["runs"].values() if v["status"] == "open") == 2, "open 记录被裁")
            _ck(sum(1 for v in st["runs"].values() if v["status"] == "restored") <= 50, "restored 未按 50 上限裁")
            pathlib.Path(state).write_text("[]", encoding="utf-8")
            _ck(rp.load_state() == {"runs": {}}, "坏台账应回退 {'runs': {}}")
            pathlib.Path(state).write_text("{坏", encoding="utf-8")
            _ck(rp.load_state() == {"runs": {}}, "坏 JSON 应回退 {'runs': {}}")


# ======================================== 运行台账「在飞按需」核销（L3/L4 共用；离线可测）
# 定因（t_a8b4ceb0，2026-09-28 10:32–10:34 两次采到）：46 号里 用户_173920298 的 StageMode 是
# 指针而非 Fixed —— **不是在飞残留、也不是「设计如此」**，而是 run_profile 的**在飞**按需运行
# （rp_20260928_102708_-b-3：10:27:09 建临时计划表 bc659275-… 并挂指针，10:44:34 run_end 才还原）
# 被采样窗口套住。判据：差异要**逐键等于该在飞 run 自己声明的写入**（StageMode=它建的表 id /
# payload 七键 / 它关的 Status），且该 run 有活体证据（本地锁新鲜或实例租约在租）；
# 无人认领的指针（孤儿残留、任务已结束未还原、指针指向已删的表）仍按违例处理。
RUN_PROFILE_STATE = ROOT / "runtime" / "run_profile_state.json"
RUN_PROFILE_LOCK = ROOT / "runtime" / "mas_run_profile.lock"
INSTANCE_LEASES = ROOT / "runtime" / "instance_leases.json"
LOCK_FRESH_S = 300.0   # run_profile 长跑在 wait 里 touch 本地锁（wait_poll 默认 60s，放宽 5×）
LOCK_MATCH_S = 900.0   # 锁 ↔ run 记录的时间窗（同一趟 run 的 ts 与取锁时刻同秒级）


def _parse_ts(v):
    """台账/租约里的 'YYYY-mm-dd HH:MM:SS' → datetime；不可解析 ⇒ None。"""
    try:
        return dt.datetime.strptime(str(v).strip(), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _unrepr(v) -> str:
    """剥掉 diffs 文本里 repr 出来的引号（`'Fixed'` → `Fixed`）。"""
    s = str(v).strip()
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"" else s


def _diff_kv(diff: str):
    """`Stage:'SR-7'->'LS-6'` → ('Stage', 'LS-6')（值取箭头右侧最后一段）。"""
    key, sep, rest = str(diff).partition(":")
    if not sep:
        return None, None
    _, _, new = rest.rpartition("->")
    return key, _unrepr(new)


def _emu_set(v) -> set:
    """脚本实例号集合（台账里 emu 可能是 str/int 或列表）。"""
    vals = v if isinstance(v, (list, tuple, set)) else [v]
    return {str(x) for x in vals if x is not None}


def _run_profile_open_runs(state=None, leases=None, lock=None, now=None) -> dict:
    """未还原的 run_profile 记录 + 活体证据 = 「在飞」的机读定义（只读）。

    未还原 = `status != "restored"`（口径同 `mas_run_profile.py --status` 的告警行）。
    活体证据（任一即可）：① 本地单飞锁在位且新鲜（长跑在 wait 里 touch）；② 该脚本实例的实例
    租约在租未过期。两者皆无 ⇒ **滞留**（崩溃/被 kill 后留下的孤儿指针）。
    返回 {run_id: {status, ts, emu, write_mode, live, why, accounts:{uid:{name, disabled_by_us,
    declared_modes, declared_payload}}}}。
    """
    def _load(p, default):
        with contextlib.suppress(OSError, ValueError):
            return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
        return default

    st = _load(state or RUN_PROFILE_STATE, {})
    lz = _load(leases or INSTANCE_LEASES, {})
    lk = pathlib.Path(lock or RUN_PROFILE_LOCK)
    now = now or dt.datetime.now()

    lock_live, lock_ts = False, None
    with contextlib.suppress(OSError, ValueError):
        txt = lk.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"ts=([0-9\-: ]+)", txt)
        lock_ts = _parse_ts(m.group(1)) if m else None
        lock_live = (now - dt.datetime.fromtimestamp(lk.stat().st_mtime)) <= dt.timedelta(seconds=LOCK_FRESH_S)

    active = []
    for le in ((lz.get("leases") or {}) if isinstance(lz, dict) else {}).values():
        if isinstance(le, dict) and not le.get("ended_at") and (_parse_ts(le.get("expires_at")) or now) > now:
            active.append(le)

    out = {}
    for rid, r in ((st.get("runs") or {}) if isinstance(st, dict) else {}).items():
        if not isinstance(r, dict) or str(r.get("status")) == "restored":
            continue
        sc = r.get("script") or {}
        emus = _emu_set(sc.get("emu"))
        why, live = [], False
        run_ts = _parse_ts(r.get("ts")) or now
        if lock_live and (lock_ts is None or abs((run_ts - lock_ts).total_seconds()) <= LOCK_MATCH_S):
            live, why = True, why + ["锁新鲜"]
        if emus and any(str(le.get("idx")) in emus for le in active):
            live, why = True, why + ["实例租约在租（idx=%s）" % ",".join(sorted(emus))]
        accs = {}
        for a in (r.get("accounts") or []):
            if not isinstance(a, dict) or not a.get("uid"):
                continue
            exp = (r.get("expected") or {}).get(a["uid"]) or {}
            accs[a["uid"]] = {
                "name": a.get("name"), "disabled_by_us": bool(a.get("disabled_by_us")),
                "declared_modes": {str(tp.get("plan_id")) for tp in (r.get("temp_plans") or [])
                                   if isinstance(tp, dict) and tp.get("uid") == a["uid"] and tp.get("plan_id")},
                "declared_payload": dict(exp.get("payload") or {}),
            }
        out[rid] = {"status": str(r.get("status")), "ts": r.get("ts"), "emu": sc.get("emu"),
                    "write_mode": r.get("write_mode"), "live": live,
                    "why": "、".join(why) or "无活体证据（锁不在/陈旧 + 租约不在租）", "accounts": accs}
    return out


def _inflight_account(uid, open_runs):
    """该 uid 是否被某条「有活体证据的未还原 run」声明（是 ⇒ 返回核销凭据，否则 None）。"""
    for rid, r in sorted(open_runs.items()):
        if r.get("live"):
            acc = (r.get("accounts") or {}).get(uid)
            if acc:
                return dict(acc, run_id=rid, why=r.get("why"))
    return None


def _excusable_diff(diff: str, acc: dict) -> bool:
    """差异是否 = 该在飞 run 自己声明的写入（StageMode 指针 / payload 七键 / 它关掉的 Status）。

    只认「声明的值」：对不上（指针指向别的表、关卡停在未声明的关）一律不算核销。
    """
    key, new = _diff_kv(diff)
    if key == "StageMode":
        return new in (acc.get("declared_modes") or set())
    if key == "Status":
        return bool(acc.get("disabled_by_us")) and new == "False"
    pay = acc.get("declared_payload") or {}
    return key in pay and str(new) == str(pay[key])


# ================================================================ F 全工具离线自检汇总

@case
def test_F1_all_tool_selftests_green():
    """今日交付工具的自带离线自检全绿（各自覆盖自己的全链路）。"""
    import mas_plan_builder as pb
    import mas_probe_farm as pf
    import mas_ondemand as od
    import mas_preflight as pp
    import activity_switch as asw
    import maa_upgrade_check as uc
    results = {}
    for name, fn in (("mas_plan_builder", lambda: pb.selftest()),
                     ("mas_probe_farm", lambda: pf.selftest()),
                     ("mas_ondemand", lambda: od.selftest()),
                     ("activity_switch", lambda: asw.selftest())):
        with _quiet():   # 自检会刷自己的用例表/日志，压掉（失败详情由断言带出）
            rep = fn()
        results[name] = "%s (%s/%s)" % ("ok" if rep.get("ok") else "FAIL", rep.get("passed"),
                                        rep.get("passed", 0) + rep.get("failed", 0))
        _ck(rep.get("ok"), "%s.selftest 失败：%s" % (name, json.dumps(
            [c for c in rep.get("cases", []) if not c.get("ok")], ensure_ascii=False)[:300]))
    with _quiet():
        rc = pp.selftest()
    results["mas_preflight"] = "rc=%s" % rc
    _ck(rc == 0, "mas_preflight.selftest rc=%r" % rc)
    with _quiet():   # 升级护栏自检（35 项；离线假传输，live 只跑 dry-run/断线路径）
        rc = uc.selftest()
    results["maa_upgrade_check"] = "rc=%s" % rc
    _ck(rc == 0, "maa_upgrade_check.selftest rc=%r" % rc)
    import mas_rollback_drill as rd   # 回滚演练周跑入口（t_7fa96ea5）：三闸/判据/归档/告警 dry-run
    with _quiet():
        rc = rd.selftest()
    results["mas_rollback_drill"] = "rc=%s" % rc
    _ck(rc == 0, "mas_rollback_drill.selftest rc=%r" % rc)
    print("      自检明细: %s" % json.dumps(results, ensure_ascii=False))


@case
def test_F2_mapping_offline_invariants():
    """端口映射工具（t_ee50913f）：离线自检全绿 + 三条铁律（实测口优先/漂移留痕/凭据不落盘）。"""
    import mas_mapping as mm
    with _quiet():
        rc = mm.selftest()
    _ck(rc == 0, "mas_mapping.selftest rc=%r" % rc)

    # 公式本身（改了就是改口径，必须显式过目）
    _ck(mm.expected_port(0) == (16384, "A") and mm.expected_port(27) == (17248, "A")
        and mm.expected_port(32) == (16388, "B") and mm.expected_port(60) == (17284, "B"),
        "家族公式被改动：%r" % [mm.expected_port(i) for i in (0, 27, 32, 60)])
    _ck(mm.secondary_port(27) == 5609, "副口公式被改动")

    fx = mm._fixture()
    doc = mm.build_doc(fx)
    codes = {f["code"] for f in doc["findings"]}
    want = {"PORT_FORMULA_MISMATCH", "SCRIPT_IDX_MISSING", "SLOT_MEMORY_OFF_SLOT",
            "ADB_STALE_ENTRY", "NO_ENABLED_USER", "MUMU_STATE_LIE"}
    _ck(want <= codes, "假数据应命中的 finding 缺：%r（实得 %r）" % (sorted(want - codes), sorted(codes)))
    _ck("SLOT_LASTCONNECT_OFF_SLOT" not in codes, "误报：槽位最近连接属于本槽却报离槽")

    # 铁律①：串口必须用「实测值」，绝不回退公式（idx27 实测 17249 ≠ 公式 17248）
    got = mm.serial_for(doc, idx=27)
    _ck(got["serial"] == "127.0.0.1:17249", "serial_for(idx=27) 没用实测口：%r" % got)
    _ck("17248" not in json.dumps(got), "serial_for 把公式口带出来了：%r" % got)

    # 铁律②：基线漂移要留痕（端口改了 ⇒ changes + PORT_DRIFT 双证据）
    doc2 = mm.build_doc(fx, {"built_at": "2026-09-27 21:26:00", "instances": [{"idx": "27", "port": 17248}]})
    ch = [c for c in doc2["changes"] if c.get("kind") == "port_changed"]
    _ck(any(str(c.get("idx")) == "27" and c.get("from") == 17248 and c.get("to") == 17249 for c in ch),
        "基线端口漂移没记入 changes：%r" % ch)
    _ck("PORT_DRIFT" in [f["code"] for f in doc2["findings"]], "漂移没升级成 RED finding")

    # 铁律③：凭据/手机号不得进产物（明文扫一遍 JSON + md）
    blob = json.dumps(doc, ensure_ascii=False) + mm.render_md(doc)
    for bad in ("Password", "SklandToken", "passwd", "13800000000"):
        _ck(bad not in blob, "产物里出现敏感键/值：%s" % bad)
    _ck("| MAA_6 |" in mm.render_md(doc) and "当前 adb 串口（实测）" in mm.render_md(doc), "md 主表结构变了")
    print("      假数据 finding：%s" % ",".join(sorted(codes)))


@case
def test_F3_inflight_pointer_accounting():
    """在飞核销判据（t_a8b4ceb0 现场复盘）：在飞 run 声明的指针/差值 ⇒ 核销；孤儿 ⇒ 违例。"""
    # 现场：rp_20260928_102708_-b-3（10:27:09 挂指针 bc659275-… → 10:44:34 run_end 还原），
    # 被 10:32/10:34 两次采样套住 —— 所以既不是残留、也不是「该号设计如此」。
    uid = "bd2e68c5-5af7-4883-b316-82c18799439b"
    pid = "bc659275-698d-4371-9f1a-ad0f4888a1b0"
    other_pid = "41c738bb-4534-4af2-81c2-4f7b488c6cca"
    payload = {"MedicineNumb": 0, "SeriesNumb": "1", "Stage": "LS-6", "Stage_1": "-",
               "Stage_2": "-", "Stage_3": "-", "Stage_Remain": "-"}
    run = {"run_id": "rp_20260928_102708_-b-3", "ts": "2026-09-28 10:27:08", "status": "dispatched",
           "write_mode": "plan",
           "script": {"name": "明日方舟-b-3", "sid": "b4f8dbf2-d0dc-4f37-b94d-c563b55e9793", "emu": "2"},
           "accounts": [{"uid": uid, "name": "用户_173920298", "server": "Bilibili",
                         "disabled_by_us": False, "prev_stage_mode": "Fixed"}],
           "temp_plans": [{"uid": uid, "plan_id": pid, "name": "临时-按需-20260928_1027",
                           "payload": dict(payload)}],
           "expected": {uid: {"payload": dict(payload)}}}
    lease_on = {"leases": {"100.79.173.69:2": {"host": "100.79.173.69", "idx": "2", "owner": "t_73ff4db2",
                                               "expires_at": "2099-01-01 00:00:00"}}}
    lease_off = {"leases": {"100.79.173.69:2": {"host": "100.79.173.69", "idx": "2", "owner": "t_73ff4db2",
                                                "expires_at": "2026-09-28 10:00:00"}}}
    now = dt.datetime(2026, 9, 28, 10, 34, 4)   # 现场 L3 采样时刻

    with _sandbox() as tmp:
        sp = pathlib.Path(tmp) / "state.json"
        lz = pathlib.Path(tmp) / "leases.json"
        lk = pathlib.Path(tmp) / "run_profile.lock"

        def world(runs, leases, lock_ts=None, lock_age=0):
            sp.write_text(json.dumps({"runs": runs}, ensure_ascii=False), encoding="utf-8")
            lz.write_text(json.dumps(leases, ensure_ascii=False), encoding="utf-8")
            lk.unlink(missing_ok=True)
            if lock_ts is not None:
                lk.write_text("pid=38956 ts=%s\n" % lock_ts, encoding="utf-8")
                t = (now - dt.timedelta(seconds=lock_age)).timestamp()
                os.utime(lk, (t, t))
            return _run_profile_open_runs(state=sp, leases=lz, lock=lk, now=now)

        # ① 在飞：实例租约在租 ⇒ 活体；差异逐键等于它声明的值 ⇒ 核销
        got = world({run["run_id"]: run}, lease_on)
        _ck(list(got) == [run["run_id"]] and got[run["run_id"]]["live"], "在租的未还原 run 应判在飞：%r" % got)
        acc = _inflight_account(uid, got)
        if not acc:
            raise AssertionError("在飞 run 的声明没读全：%r" % got)
        _ck(acc["declared_modes"] == {pid} and str(acc["declared_payload"]["Stage"]) == "LS-6",
            "在飞 run 的声明没读全：%r" % acc)
        _ck(_excusable_diff("StageMode:'Fixed'->%s" % pid, acc), "声明的指针应核销")
        _ck(_excusable_diff("Stage:'SR-7'->'LS-6'", acc)
            and _excusable_diff("Stage_1:'1-7'->'-'", acc)
            and _excusable_diff("SeriesNumb:'0'->'1'", acc), "声明的 payload 键应核销")
        _ck(not _excusable_diff("StageMode:'Fixed'->%s" % other_pid, acc), "指向别人的表不得核销")
        _ck(not _excusable_diff("Server:'Bilibili'->'Official'", acc)
            and not _excusable_diff("Stage:'SR-7'->'CE-5'", acc), "未声明的键/值不得核销")

        # ② 在飞：本地锁新鲜（租约不在租也认）；③ 锁陈旧 ⇒ 滞留
        got_lk = world({run["run_id"]: run}, {"leases": {}}, lock_ts=run["ts"], lock_age=30)
        _ck(got_lk[run["run_id"]]["live"] and "锁" in got_lk[run["run_id"]]["why"], "新鲜锁应判在飞：%r" % got_lk)
        got_old = world({run["run_id"]: run}, lease_off, lock_ts=run["ts"], lock_age=7200)
        _ck(not got_old[run["run_id"]]["live"], "陈旧锁 + 过期租约 = 滞留：%r" % got_old)

        # ④ 滞留（崩溃残留）：无活体 ⇒ 指针没人认领 ⇒ 违例
        orphan = world({run["run_id"]: run}, lease_off)
        _ck(not orphan[run["run_id"]]["live"] and _inflight_account(uid, orphan) is None,
            "无活体证据的指针不得核销（那是孤儿残留）：%r" % orphan)

        # ⑤ 已还原的 run 不认领任何东西（还原后仍挂指针 = 真残留）
        done = dict(run, status="restored")
        got_done = world({run["run_id"]: done}, lease_on)
        _ck(run["run_id"] not in got_done and _inflight_account(uid, got_done) is None,
            "已还原的 run 不得核销：%r" % got_done)

    print("      在飞核销：声明值核销 ✓ / 未声明值拒绝 ✓ / 锁·租约活体 ✓ / 孤儿+已还原判违例 ✓")


# ================================================================ L 实机只读层（--live）

# 已知残留违例（还原不动点，见 docs/audits/2026-09-27_switch_gate_two_violations_rootcause.md）：
# 播种清理（false→true）已出方案但需 lead 拍板；在清理前它们属「已知、已定因」。
KNOWN_GATE_VIOLATIONS = {"MAA_10": "还原不动点残留（待受控播种清理，定因见 09-27 审计）",
                         "MAA_12": "同上"}
PINS = {  # 09-28 02:19–02:20 部署的补丁版（见 docs/audits/2026-09-27_emulator_liveness_fix.md §2）
    r"E:\AUTO-MAS\app\utils\emulator\mumu.py":
        "3FC40DE9DF4D38993F3F0FC944AD6226767517B9402BAF7AC2A3E17DA17D8874",
    r"E:\AUTO-MAS\app\utils\constants.py":
        "030EBBD0E39101D2D95979C2EFBD4462606B6406FEF90767EC570A4339CAD5D4",
    r"E:\AUTO-MAS\app\task\MAA\AutoProxy.py":
        "0F94B53B50445600214535D35995FAD8C469A7F59B6B0D0016441A366934E143",
}


@case
def test_L1_live_switch_gate_no_new_violations():
    """实机（只读）：12 槽 gui.new.json 的切号注入必须合规 —— 违例 ⊆ 已知残留；UNKNOWN 即失败。"""
    _require_live()
    import mas_switch_gate as sg
    res = sg.run_gate()
    _ck(res["rest"]["state"] == "ok", "REST 不可达：%r" % res["rest"])
    _ck(len(res["slots"]) >= 10, "槽位太少（%d），闸门没读全" % len(res["slots"]))
    _ck(not res["unknown"], "存在 UNKNOWN 槽位（fail-closed）：%r" % res["unknown"])
    new = [v for v in res["violations"] if v not in KNOWN_GATE_VIOLATIONS]
    _ck(not new, "出现新的切号注入违例（注入补丁可能被覆盖/回滚）：%r；全量=%r" % (new, res["violations"]))
    print("      SWITCH_GATE=%s（槽位 %d；已知残留 %s）" % (res["switch_gate"], len(res["slots"]),
                                                          ",".join(sorted(KNOWN_GATE_VIOLATIONS))))


@case
def test_L2_live_remote_patch_pins():
    """实机（只读）：侧车三补丁（判活关闭/理智药/切号注入）哈希与关键标记在位。"""
    _require_live()
    got = _fetch_remote_b64(list(PINS))
    _ck(len(got) == len(PINS), "远端文件读取不全：%r" % list(got))
    drift = []
    for path, want in PINS.items():
        raw = got[path]
        actual = hashlib.sha256(raw).hexdigest().upper()
        if actual != want:
            drift.append("%s：期望 %s，实际 %s" % (path.split("\\")[-1], want, actual))
    allow_drift = os.environ.get("MAS_REGRESSION_HASH_DRIFT", "").strip().lower() in {"1", "allow", "true", "yes"}
    if drift and not allow_drift:
        raise AssertionError("侧车补丁哈希漂移（先人工复核 markers 与行为，再更新 PINS；"
                             "已知迁移窗口内可 MAS_REGRESSION_HASH_DRIFT=allow 临时放行）：\n  " + "\n  ".join(drift))
    if drift:
        print("      WARN 哈希漂移已放行：%s" % "; ".join(drift))
    mumu = got[r"E:\AUTO-MAS\app\utils\emulator\mumu.py"].decode("utf-8", "replace")
    consts = got[r"E:\AUTO-MAS\app\utils\constants.py"].decode("utf-8", "replace")
    ap = got[r"E:\AUTO-MAS\app\task\MAA\AutoProxy.py"].decode("utf-8", "replace")

    # —— 判活/关闭（close-fix）：可靠判活 + 无条件尝试 + 重试；旧「未在线直接 return」只在回滚分支
    for marker in ("MUMU_RELIABLE_CLOSE = True", "mumu_close_legacy.flag", "def _find_vmm_pids",
                   "def _is_vmm_running", "def _is_adb_connectable", "async def _close_legacy"):
        _ck(marker in mumu, "mumu.py 缺标记：%s" % marker)
    m = re.search(r"async def close\(self, idx: str\).*?(?=\n    async def _close_legacy)", mumu, re.S)
    _ck(m, "mumu.py 找不到 close() 主体")
    body = m.group(0)
    for marker in ("_is_instance_running", "发送关闭命令", "已关闭", "MUMU_CLOSE_MAX_ATTEMPTS"):
        _ck(marker in body, "close() 主体缺标记：%s" % marker)
    _ck("未在线，当前状态" not in body, "close() 主路径又出现旧状态门文案（判活补丁被回退？）")
    m2 = re.search(r"async def _close_legacy\(self, idx: str\).*?(?=\n    async def |\n    def )", mumu, re.S)
    _ck(m2 and "未在线，当前状态" in m2.group(0), "回滚分支（_close_legacy）应保留旧行为")

    # —— 理智药：模板两处 True（剩余理智兜底 + 活动关优先）
    _ck(consts.count('"UseExpiringMedicine": True') >= 2 and '"UseExpiringMedicine": False' not in consts,
        "constants.py 理智药模板被改回：%r" % re.findall(r'"UseExpiringMedicine": \w+', consts))
    _ck(ap.count('"UseExpiringMedicine": True') >= 1, "AutoProxy.py 理智药模板被改回")

    # —— 切号注入 v2：官服=掩码+true；非官服=""+false
    for marker in ('task_set["StartUp"]["AccountName"] = (', 'task_set["StartUp"]["AccountSwitchEnabled"] = True',
                   'task_set["StartUp"]["AccountName"] = ""', 'task_set["StartUp"]["AccountSwitchEnabled"] = False'):
        _ck(marker in ap, "AutoProxy.py 切号注入块缺标记：%s" % marker)
    print("      侧车补丁：mumu/constants/AutoProxy 哈希与标记均在位%s" % ("（漂移已放行）" if drift else ""))


@case
def test_L3_live_stage_fallback_and_medicine_ledger():
    """实机（只读）：46 号 SR-* 的 Stage_1 回退位（理智药消耗路径）在 API 与落盘两层一致。

    合法态两类（t_a8b4ceb0 定因）：① `StageMode=Fixed`（常态）；② 正被「在飞」按需 run 挂了
    临时计划表指针 —— 差异必须**逐键等于该 run 自己声明的写入**（StageMode=它建的表 id / payload
    七键 / 它关的 Status），该 run 要有活体证据（锁新鲜或实例租约在租），且指针解引用得到那张表。
    孤儿指针 / 未声明的差异 / 非在飞的落盘待 flush 仍判 FAIL。
    """
    _require_live()
    out = _subprocess([sys.executable, str(TOOLS / "mas_stage_fix.py"), "--verify", "--json"])
    text = (out.stdout or "") + (out.stderr or "")
    if "没有 plan.json" in text:
        raise SkipCase("没有 stage_fix plan.json（先跑 --dry-run 生成）")
    m = re.search(r"API (\d+)/(\d+) 通过；落盘待 flush (\d+)；落盘异常 (\d+)", text)
    _ck(m, "verify 输出无法解析（rc=%s）：%s" % (out.returncode, text[-400:]))
    api_ok, api_all, pending, bad = (int(x) for x in m.groups())
    jm = re.search(r"^STAGE_FIX_VERIFY_JSON=(\{.*\})\s*$", text, re.M)
    if not jm:
        raise AssertionError("verify 没给机读摘要（--json 缺失/工具版本旧）：%s" % text[-300:])
    pay = json.loads(jm.group(1))

    open_runs = _run_profile_open_runs()
    viol, excuses, pids = [], [], []
    for f in pay.get("failures") or []:
        acc = _inflight_account(f.get("uid"), open_runs)
        left = [d for d in (f.get("diffs") or []) if not (acc and _excusable_diff(d, acc))]
        if left or not acc:
            viol.append("%s(%s)" % (f.get("name"), "；".join(left) or "无在飞 run 认领"))
            continue
        excuses.append("%s←%s" % (f.get("name"), acc["run_id"]))
        pids += [new for k, new in (_diff_kv(d) for d in (f.get("diffs") or [])) if k == "StageMode"]
    if pids:  # 指针解引用：在飞 run 的临时计划表必须还在（表被删 = 指针指向空处）
        import mas_run_profile as rp
        try:
            gone = [pid for pid in pids if rp.fetch_plan(pid) is None]
        except Exception as e:  # noqa: BLE001
            raise AssertionError("在飞指针解引用失败（fail-closed）：%s" % e)
        _ck(not gone, "在飞指针指向的计划表已不在：%r" % gone)
    pend_left = [p.get("name") for p in (pay.get("pending") or [])
                 if not _inflight_account(p.get("uid"), open_runs)]

    _ck(api_all >= 40, "目标数太少（%s），闸门没读全" % api_all)
    _ck(not viol, "API 复读不全（未核销）：%s/%s；未核销=%r；在飞核销=%r" % (api_ok, api_all, viol, excuses))
    _ck(bad == 0, "落盘异常 %d 号（回退位被回退）：%r" % (bad, pay.get("bad")))
    _ck(not pend_left, "落盘待 flush %d 号（非在飞）：%r（等一轮后复跑；重启前必须先 flush）"
                       % (len(pend_left), pend_left))
    _ck(out.returncode == 0 or (bool(excuses) and out.returncode == 3),
        "verify rc=%s（在飞核销=%r）" % (out.returncode, excuses))
    print("      stage_fix verify：API %d/%d；pending %d；bad %d%s"
          % (api_ok, api_all, pending, bad,
             "；在飞核销 %d 号（%s）" % (len(excuses), "、".join(excuses)) if excuses else ""))


@case
def test_L4_live_run_profile_no_open_runs():
    """实机（只读）：运行台账无「滞留」记录 —— 未还原但有活体证据（锁/租约）= 在飞，放行；
    未还原且无活体证据 = 滞留（FAIL，`--reconcile` 先 dry-run 再 apply）。

    旧版只匹配字面状态 `open`（台账里根本不存在这个值：态是 prepared/written/dispatched/
    restored/restore_failed）⇒ 这条闸门永远不会红（t_a8b4ceb0：10:32 明明有一条未还原记录却 P 了）。
    """
    _require_live()
    out = _subprocess([sys.executable, str(TOOLS / "mas_run_profile.py"), "--status"])
    _ck(out.returncode == 0, "status rc=%s：%s" % (out.returncode, (out.stderr or "")[-200:]))
    rows = [l.split() for l in out.stdout.splitlines()[1:] if l.strip()]
    rows = [r for r in rows if len(r) >= 5 and r[0].startswith("rp_")]     # 排除 ⚠ 提示行
    opened = [r[0] for r in rows if r[1] != "restored"]
    runs = _run_profile_open_runs()
    stale = [rid for rid in opened if not (runs.get(rid) or {}).get("live")]
    _ck(not stale, "存在滞留的未还原记录（无活体证据）：%r（跑 `mas_run_profile.py --reconcile` "
                   "先 dry-run，确认任务已结束后 --apply）；全量未还原=%r" % (stale, opened))
    note = ""
    if opened:
        note = "；在飞 %d 条（%s）—— 收尾后复跑本项" % (
            len(opened), "、".join("%s:%s" % (r, (runs.get(r) or {}).get("why")) for r in opened))
    print("      运行台账：%d 条历史，未还原 %d 条%s" % (len(rows), len(opened), note))


@case
def test_L5_live_ondemand_ledger_sane():
    """实机（只读）：按需发车共享账本可解析、schema 合规、在窗记录字段完整。"""
    _require_live()
    import mas_ondemand_ledger as lg
    p = ROOT / "runtime" / "mas_ondemand_ledger.json"
    if not p.exists():
        raise SkipCase("本地没有 %s" % p)
    st = json.loads(p.read_text(encoding="utf-8"))
    _ck(isinstance(st, dict) and st.get("meta", {}).get("v") == 1, "账本 schema 头缺失：%r" % list(st)[:6])
    rows = lg.open_entries(st)
    for r in rows:
        _ck(r.get("task_uid"), "在窗记录缺 task_uid：%r" % r)
        _ck(lg.parse_ts(r.get("busy_until")) is not None, "在窗记录 busy_until 不可解析：%r" % r.get("busy_until"))
    print("      账本：%d 条在窗记录（含 prev）" % len(rows))


# ================================================================ runner

def _run_case(fn) -> dict:
    t0 = time.time()
    rec = {"name": fn.__name__, "ok": False, "skip": False, "detail": "", "ms": 0}
    try:
        fn()
        rec["ok"] = True
    except SkipCase as e:
        rec["skip"] = True
        rec["detail"] = str(e)
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc().strip().splitlines()
        rec["detail"] = "%s: %s | %s" % (type(e).__name__, e, " <- ".join(tb[-3:]))
    rec["ms"] = int((time.time() - t0) * 1000)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MAAOrch 回归测试套件（默认离线只读）")
    ap.add_argument("--live", action="store_true", help="加跑实机只读检查（REST+ssh，约 2–5 分钟）")
    ap.add_argument("--json", action="store_true", help="机器可读输出（JSON）")
    ap.add_argument("--out", help="把 JSON 报告写到该路径")
    ap.add_argument("--list", action="store_true", help="只列用例")
    ap.add_argument("--only", help="只跑名字含该子串的用例")
    args = ap.parse_args(argv)
    global _LIVE, _IN_RUNNER
    _IN_RUNNER = True
    if args.live:
        _LIVE = True

    cases = [f for f in _REGISTRY if (not args.only or args.only in f.__name__)]
    if args.list:
        for f in cases:
            print("%-52s %s" % (f.__name__, (f.__doc__ or "").strip().splitlines()[0]))
        print("共 %d 例（--live 才会跑 test_L*）" % len(cases))
        return 0

    started = time.time()
    results = []
    for f in cases:
        rec = _run_case(f)
        results.append(rec)
        if not args.json:
            tag = "SKIP" if rec["skip"] else ("PASS" if rec["ok"] else "FAIL")
            print("[%s] %-52s %6dms %s" % (tag, rec["name"], rec["ms"], rec["detail"][:160]))
            sys.stdout.flush()
    npass = sum(1 for r in results if r["ok"])
    nskip = sum(1 for r in results if r["skip"])
    nfail = len(results) - npass - nskip
    payload = {"tool": "regression", "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "live": bool(_LIVE), "ok": nfail == 0, "passed": npass, "failed": nfail,
               "skipped": nskip, "seconds": round(time.time() - started, 1), "cases": results}
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print("\nREGRESSION=%s (%d passed, %d failed, %d skipped, %.1fs)%s"
              % ("PASS" if nfail == 0 else "FAIL", npass, nfail, nskip, payload["seconds"],
                 " [live]" if _LIVE else " [offline]"))
        for r in results:
            if not r["ok"] and not r["skip"]:
                print("  FAIL %s：%s" % (r["name"], r["detail"][:400]))
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
