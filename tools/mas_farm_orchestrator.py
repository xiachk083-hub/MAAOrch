#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""mas_farm_orchestrator.py —— 端到端刷关编排器（一句话入口）。

把已交付的各环节工具串成一条链（每个环节仍是它自己的权威，本工具只做「串 + 判 + 报告」）：

    ① preflight（mas_preflight 闸门；顺带查实例租约是否被别人占）
    ② 台账查询（stage_capability.can_farm → success/failed/unknown）
    ③ unknown（或 --force-probe）⇒ 试探 1 场（mas_probe_farm --probe-only）
    ④ 生成计划（本工具生成 run_profile profile + 校验 + 干跑预览，拒绝项 fail-closed）
    ⑤ 取实例租约（mas_instance_lease；拿不到就停，零写）
    ⑥ 执行（mas_run_profile --apply；内含备份→写配置→preflight→发车→读回→等完→还原；`--lease-owner` 透传本工具持有者）
    ⑦ 读回校验（run_profile L1/L2 结果 + 本工具独立复核 run 状态）
    ⑧ 还原复核（run 是否 restored + 临时计划表是否删净 + StageMode 是否回写）
    ⑨ 写报告（runtime/reports/farm_run_<ts>.md）+ 审计（mas_ledger.jsonl / 本工具 jsonl）

失败语义（与各子工具一致）：**任一步失败 ⇒ 立即停止 + 还原 + 照实报告，绝不自动重试**。
每个步骤都有结构化记录（步骤名/rc/耗时/判据/命令/证据路径），落 run 目录 steps.json。

用法（**默认 dry-run；真跑要 --apply**）：
    python tools/mas_farm_orchestrator.py --account <号> --stage <关> [--times N] [--medicine N]   # dry-run
    python tools/mas_farm_orchestrator.py --account <号> --stage <关> --times 6 --apply
    python tools/mas_farm_orchestrator.py --status      # 只读：最近运行 + 锁状态
    python tools/mas_farm_orchestrator.py --selftest    # 离线自测（零触网/零写 runtime）

约束（硬）：
  * 只走 7 键路线（`Stage/Stage_1..3/SeriesNumb/MedicineNumb/Stage_Remain`）；剿灭（Annihilation）
    不在本工具范围（它由 `Info.Annihilation` 配置）——直接拒绝。
  * `--times N` = **连战倍数**（`SeriesNumb`，0..6/-1），不是总次数上限（AUTO-MAS 两层没有该字段）。
  * 单次真跑建议最小规模（1 个号、1 个关、≤6）；默认不碰其它账号（多号脚本会 fail-closed，
    要显式 `--disable-others` 或 `--all-accounts`）。
  * 不重启侧车；失败不重试；写面全在子工具里（本工具不直接写 AUTO-MAS/MAA 配置）。

退出码：0 期望路径完成（试探[如需]+执行+还原都过）｜2 拒绝/发车前失败（零残留：preflight NO-GO、
台账 failed 拒绝、目标解析失败、计划被拒、租约被占、无试探时 farm 发车前失败）｜3 试探未证明可刷
（failed/inconclusive）或无试探时执行后校验失败｜4 **有残留**（还原失败/open run/临时表未删）｜
5 试探成功但执行阶段失败。

环境变量：MAAORCH_MAS_API / MAAORCH_SSH_KEY / MAAORCH_SSH_HOST（同其他 mas_* 工具）；
         MAAORCH_FARM_ORCH_NO_GO=1 一票否决（另沿用 MAAORCH_PROBE_FARM_NO_GO / MAAORCH_DISPATCH_NO_GO /
         MAAORCH_RUN_PROFILE_NO_GO 子工具自带闸门）；MAAORCH_FARM_ORCH_RUNTIME 改 runtime 落点（自测用）；
         MAAORCH_FARM_ORCH_PYTHON 指定子进程解释器（默认 Python312）。
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):                          # Windows 控制台中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
RUNTIME = os.environ.get("MAAORCH_FARM_ORCH_RUNTIME") or os.path.join(ROOT, "runtime")
REPORTS = os.path.join(RUNTIME, "reports")
RUNS_DIR = os.path.join(RUNTIME, "farm_orchestrator")
CAP_JSON = os.path.join(REPORTS, "stage_capability.json")
PROBE_FARM = os.path.join(TOOLS, "mas_probe_farm.py")
RUN_PROFILE = os.path.join(TOOLS, "mas_run_profile.py")
LEASE_TOOL = os.path.join(TOOLS, "mas_instance_lease.py")
PY312 = os.environ.get("MAAORCH_FARM_ORCH_PYTHON",
                       r"C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe")

TS_FMT = "%Y-%m-%d %H:%M:%S"
LOCK_STALE_S = 6 * 3600
TIMES_OK = ("0", "6", "5", "4", "3", "2", "1", "-1")           # = mas_run_profile.SERIES_OK
ANNI_NAMES = {"annihilation", "剿灭", "剿灭作战"}
NO_GO_ENVS = ("MAAORCH_FARM_ORCH_NO_GO", "MAAORCH_PROBE_FARM_NO_GO",
              "MAAORCH_DISPATCH_NO_GO", "MAAORCH_RUN_PROFILE_NO_GO")
ST_SUCCESS, ST_FAILED, ST_UNKNOWN = "success", "failed", "unknown"
LEASE_TTL_S = 3600                                             # 租约 TTL（覆盖执行+还原的余量）


class RefuseError(RuntimeError):
    """用法/输入/闸门拒绝（发车前，零残留）。"""


# ---------------------------------------------------------------- 基础
def _rp():
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    import mas_run_profile  # noqa: PLC0415
    return mas_run_profile


def _scap():
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    import stage_capability  # noqa: PLC0415
    return stage_capability


def _lease_mod():
    """实例租约工具（t_65b69753 交付后才有；没交付 => None，按「跳过」处理）。"""
    if not os.path.exists(LEASE_TOOL):
        return None
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    import mas_instance_lease  # noqa: PLC0415
    return mas_instance_lease


def _now() -> str:
    return dt.datetime.now().strftime(TS_FMT)


def log(msg: str) -> None:
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def scrub(obj):
    return _rp().scrub(obj)


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def save_text(path, text) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def slug(s, n=16) -> str:
    out = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "", str(s or ""))
    return out[:n] or "run"


def pick_py() -> str:
    for c in (os.environ.get("MAAORCH_FARM_ORCH_PYTHON"), PY312, sys.executable):
        if c and os.path.exists(c):
            return c
    return sys.executable


def no_go() -> str:
    for k in NO_GO_ENVS:
        if str(os.environ.get(k, "")).strip().lower() in {"1", "true", "yes", "on"}:
            return k
    return ""


def hold_lock(lock_path: str, enabled: bool = True):
    """单飞锁：同一时刻只允许一个编排器任务。"""
    @contextlib.contextmanager
    def _cm():
        if not enabled:
            yield lambda: None
            return
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        fd = None
        for attempt in (1, 2):
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, ("pid=%d ts=%s\n" % (os.getpid(), _now())).encode("utf-8"))
                break
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(lock_path)
                except OSError:
                    age = 0.0
                if attempt == 1 and age > LOCK_STALE_S:
                    log("发现陈旧锁（%.0f 分钟前）——抢占" % (age / 60))
                    with contextlib.suppress(OSError):
                        os.remove(lock_path)
                    continue
                raise RuntimeError("锁被占（%.0f 秒前创建，%s）——同一时刻只允许一个编排器任务"
                                   % (age, lock_path))
        def touch():
            with contextlib.suppress(OSError):
                os.utime(lock_path, None)
        try:
            yield touch
        finally:
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
                with contextlib.suppress(OSError):
                    os.remove(lock_path)
    return _cm()


# ---------------------------------------------------------------- 子进程
def _parse_last_json(blob: str, tool: str):
    for ln in reversed((blob or "").splitlines()):
        ln = ln.strip()
        if ln.startswith('{"tool"') and ('"%s"' % tool) in ln:
            with contextlib.suppress(ValueError):
                return json.loads(ln)
    return None


def _run_cmd(cmd: list, timeout: float, cwd: str = None) -> dict:
    rec = {"cmd": " ".join(str(c) for c in cmd), "rc": None, "timeout": False,
           "out": "", "err": "", "seconds": 0, "summary": None}
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=timeout, cwd=cwd or ROOT)
        rec["rc"] = p.returncode
        rec["out"] = (p.stdout or b"").decode("utf-8", "replace")
        rec["err"] = (p.stderr or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        rec["timeout"] = True
        rec["out"] = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        rec["err"] = (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
    rec["seconds"] = int(time.time() - t0)
    return rec


# ---------------------------------------------------------------- 默认依赖（可注入；selftest 用假的）
def deps_resolve(account: str, script_token: str) -> dict:
    """REST 只读：账号 → 唯一脚本（多命中/零命中都拒绝）。返回 {name, sid, dir, emu}。"""
    rp = _rp()
    scripts = rp.fetch_scripts()
    if script_token:
        token = str(script_token).strip()
        hit = None
        for sid, sc in scripts.items():
            if sc["name"] == token or sid == token:
                hit = sc
                break
        if hit is None:
            raise RefuseError("脚本 %r 不在 AUTO-MAS 里（名字或 uid 要对得上）" % token)
        if not _script_has_account(hit, account):
            raise RefuseError("脚本 %s 里没有账号 %r" % (hit["name"], account))
        return dict(hit)
    hits = [sc for sc in scripts.values() if _script_has_account(sc, account)]
    if len(hits) != 1:
        raise RefuseError("账号 %r 在 %d 个脚本里命中（%s）——用 --script 显式指定"
                          % (account, len(hits), "、".join(s["name"] for s in hits)))
    return dict(hits[0])


def _script_has_account(script: dict, account: str) -> bool:
    try:
        users = _rp().fetch_users(script["sid"])
    except Exception:  # noqa: BLE001
        return False
    return any(u.get("name") == account for u in users)


def deps_script_users(script_sid: str) -> list:
    """脚本内用户（enabled 判定用；只读）。"""
    return _rp().fetch_users(script_sid)


def deps_preflight(script_name: str) -> dict:
    return _rp().run_preflight_check(script_name)


def deps_ledger(account: str, stage: str) -> dict:
    """能力台账（只读）→ {status,row,report_built_at,missing_report,json_path}。"""
    p = CAP_JSON
    out = {"status": ST_UNKNOWN, "row": None, "report_built_at": "", "missing_report": False, "json_path": p}
    if not os.path.exists(p):
        out["missing_report"] = True
        return out
    row = _scap().can_farm_detail(account, stage, p)
    out["row"] = row
    if row:
        out["status"] = row["status"]
    rep = load_json(p, None) or {}
    out["report_built_at"] = rep.get("built_at") or ""
    return out


def deps_probe(args, script_name: str, stage: str, medicine: int, timeout_s: int, run_dir: str) -> dict:
    """③ 试探：mas_probe_farm --probe-only --apply（黑盒；判定/台账更新/归类都在它那边）。"""
    cmd = [pick_py(), PROBE_FARM, "--account", args.account, "--stage", stage,
           "--probe-only", "--apply", "--json",
           "--medicine", str(int(medicine)), "--timeout", str(int(timeout_s)),
           "--script", script_name, "--write-mode", args.write_mode]
    if args.retry_after_fix:
        cmd += ["--retry-after-fix", args.retry_after_fix]
    if args.remain_sanity:
        cmd += ["--remain-sanity"]
    if args.disable_others:
        cmd += ["--disable-others"]
    if args.all_accounts:
        cmd += ["--all-accounts"]
    timeout = max(2400.0, 300 + float(timeout_s) + 1800)
    rec = _run_cmd(cmd, timeout=timeout)
    rec["summary"] = _parse_last_json(rec["out"], "mas_probe_farm")
    save_text(os.path.join(run_dir, "probe_run.log"), rec["out"] or "")
    if rec.get("err"):
        save_text(os.path.join(run_dir, "probe_run.err.txt"), rec["err"])
    return rec


def deps_plan_preview(profile_path: str, timeout: float = 900, lease_owner: str | None = None) -> dict:
    """④b 计划干跑预览（run_profile 无 --apply：只读 REST，零写；带上本工具的租约持有者口径）。"""
    cmd = [pick_py(), RUN_PROFILE, "--profile", profile_path, "--json"]
    if lease_owner:
        cmd += ["--lease-owner", str(lease_owner)]
    rec = _run_cmd(cmd, timeout=timeout)
    rec["summary"] = _parse_last_json(rec["out"], "mas_run_profile")
    return rec


def deps_farm(profile_path: str, args, farm_max_wait: float, run_dir: str) -> dict:
    """⑥ 执行：mas_run_profile --apply（备份/写/发车/读回/等完/还原全在里面）。

    `--lease-owner` 必须带上本工具的持有者：步骤 ⑤ 已经以它取过租，run_profile 的 G3L
    按「同主=续租」认它（否则会被当成别人占着而拒发）；run_profile 收尾释放后，
    本工具 ⑨b 的 release 会读到「本来就没有」——幂等，属预期。
    """
    cmd = [pick_py(), RUN_PROFILE, "--profile", profile_path, "--apply", "--json",
           "--write-mode", args.write_mode, "--max-wait-seconds", str(float(farm_max_wait)),
           "--lease-owner", str(args.owner)]
    if args.disable_others:
        cmd += ["--disable-others"]
    if args.all_accounts:
        cmd += ["--all-accounts"]
    timeout = max(2400.0, 300 + float(farm_max_wait) + 1500)
    rec = _run_cmd(cmd, timeout=timeout)
    rec["summary"] = _parse_last_json(rec["out"], "mas_run_profile")
    save_text(os.path.join(run_dir, "farm_run.log"), rec["out"] or "")
    if rec.get("err"):
        save_text(os.path.join(run_dir, "farm_run.err.txt"), rec["err"])
    return rec


def deps_verify(run_ids: list, script_sid: str, accounts: list, started_at: str) -> dict:
    """⑧ 独立复核（只读）：run 是否 restored + 临时计划表删净 + StageMode 回写。"""
    out = {"runs": {}, "open_runs": [], "temp_plans": [], "stage_mode": [], "errors": []}
    try:
        rp = _rp()
        st = rp.load_state()
        runs = st.get("runs") or {}
        ids = [str(x) for x in (run_ids or []) if x]
        if not ids:
            ids = [rid for rid, r in runs.items() if str((r or {}).get("ts") or "") >= str(started_at or "")]
        out["ids"] = ids
        for rid in ids:
            r = runs.get(rid)
            if not r:
                out["runs"][rid] = "missing"
                continue
            status = str(r.get("status"))
            out["runs"][rid] = status
            if status != "restored":
                out["open_runs"].append({"run_id": rid, "status": status})
            for tp in (r.get("temp_plans") or []):
                pid = tp.get("plan_id")
                if not pid:
                    continue
                try:
                    if rp.fetch_plan(pid) is not None:
                        out["temp_plans"].append({"run_id": rid, "plan": tp.get("name"), "plan_id": pid})
                except Exception as e:  # noqa: BLE001
                    out["errors"].append("fetch_plan: %s" % e)
            # StageMode 回写核对（逐号）
            if script_sid:
                for acc in (r.get("accounts") or []):
                    uid, name, prev = acc.get("uid"), acc.get("name"), acc.get("prev_stage_mode")
                    if not uid:
                        continue
                    try:
                        u = rp.fetch_user(script_sid, uid) or {}
                        now_mode = (u.get("Info") or {}).get("StageMode")
                        out["stage_mode"].append({"run_id": rid, "account": name, "prev": prev, "now": now_mode,
                                                  "ok": str(now_mode) == str(prev)})
                    except Exception as e:  # noqa: BLE001
                        out["errors"].append("fetch_user %s: %s" % (name, e))
    except Exception as e:  # noqa: BLE001
        out["error"] = "%s: %s" % (type(e).__name__, e)
    return out


def deps_lease_ctx(idx: str, owner: str, purpose: str, dry: bool) -> dict:
    """⑤ 实例租约：dry ⇒ 只 check；apply ⇒ acquire。返回 {ok,status,detail,...}。"""
    L = _lease_mod()
    if L is None:
        return {"ok": True, "status": "skipped", "detail": "mas_instance_lease.py 不在位（t_65b69753 未交付）——跳过"}
    if not str(idx or "").strip():
        return {"ok": True, "status": "skipped", "detail": "脚本没有 Emulator.Index——无法定位实例，跳过"}
    host = L.default_host()
    try:
        if dry:
            r = L.check(host, idx, owner=owner)
            return {"ok": True, "status": "check:" + str(r.get("status")), "detail": r.get("detail"),
                    "host": host, "idx": L.norm_idx(idx), "holder": r.get("holder")}
        r = L.acquire(host, idx, owner=owner, purpose=purpose, ttl_s=LEASE_TTL_S, src="farm_orchestrator")
        r["host"], r["idx"] = host, L.norm_idx(idx)
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": "error", "detail": "%s: %s" % (type(e).__name__, e), "host": host}


def deps_lease_release(idx: str, owner: str) -> dict:
    L = _lease_mod()
    if L is None or not str(idx or "").strip():
        return {"ok": True, "status": "skipped", "detail": "无租约工具/无实例号"}
    try:
        return L.release(L.default_host(), idx, owner=owner)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": "error", "detail": "%s: %s" % (type(e).__name__, e)}


def deps_ledger_append(rec: dict) -> None:
    _rp().ledger_append(rec)


# ---------------------------------------------------------------- 决策 / 计划
def decide(status: str, force_probe: bool = False, fix_note: str = "") -> dict:
    """三态决策（与 mas_probe_farm 同口径）→ {action: farm|probe|refuse, why}。"""
    if status == ST_SUCCESS:
        if force_probe:
            return {"action": "probe", "why": "台账=success，但 --force-probe 要求先试探一次"}
        return {"action": "farm", "why": "台账=success ⇒ 直接正式刷（跳过试探）"}
    if status == ST_FAILED:
        if fix_note:
            return {"action": "probe", "why": "台账=failed，但已给修复说明 ⇒ 先试探验证（修复：%s）" % fix_note}
        return {"action": "refuse",
                "why": "台账=failed ⇒ 默认拒绝发车；确认已修复后加 --retry-after-fix \"<修复说明>\" 放行"}
    return {"action": "probe", "why": "台账=unknown ⇒ 先试探后开刷（不猜）"}


def build_farm_profile(account: str, script_name: str, stage: str, times, medicine: int,
                       keep_remain: bool, notes: str = "") -> dict:
    """④ 生成计划：run_profile profile（farm 段；restore_after=run_end）。"""
    return {
        "target": {"script": script_name, "account": account},
        "stage_plan": [stage],
        "series": str(times),
        "medicine": {"use": int(medicine) > 0, "count": int(medicine)},
        "activity_priority": {"enabled": False, "medicine": 0},
        "keep_remain_sanity_task": bool(keep_remain),
        "restore_after": "run_end",
        "notes": notes,
    }


def validate_profile(prof: dict) -> tuple:
    return _rp().validate_profile(prof)


# ---------------------------------------------------------------- 主流程
def run(args, deps=None, runtime_dir: str = None) -> tuple:
    """返回 (rc, summary)。args 已由 main 校验。"""
    d = dict(default_deps())
    if deps:
        d.update(deps)
    rt = runtime_dir or RUNTIME
    paths = {"log": os.path.join(rt, "mas_farm_orchestrator_log.jsonl"),
             "lock": os.path.join(rt, "mas_farm_orchestrator.lock"),
             "runs": os.path.join(rt, "farm_orchestrator"),
             "reports": os.path.join(rt, "reports")}
    t_start = _now()
    run_id = "fo_%s_%s" % (dt.datetime.now().strftime("%Y%m%d_%H%M%S"), slug(args.account))
    run_dir = os.path.join(paths["runs"], run_id)
    os.makedirs(run_dir, exist_ok=True)
    summary = {"tool": "mas_farm_orchestrator", "run_id": run_id, "account": args.account, "stage": args.stage,
               "apply": bool(args.apply), "ts": t_start, "owner": args.owner, "steps": [], "warnings": [],
               "script": None, "ledger": {}, "decision": {}, "probe": None, "plan": None, "lease": None,
               "farm": None, "readback": None, "restore": None, "report": None, "rc": None,
               "conclusion": ""}

    def add_step(name: str, ok, seconds, detail=None, **extra) -> dict:
        rec = {"step": name, "ok": bool(ok), "seconds": int(seconds), "ts": _now()}
        if detail:
            rec["detail"] = scrub(detail)
        rec.update(extra)
        summary["steps"].append(rec)
        save_json(os.path.join(run_dir, "steps.json"), summary["steps"])
        return rec

    def _unified_from_audit(rec: dict) -> None:
        """本工具事件行 → 统一流：farm.<event>（映射同 mas_audit._ad_farm）；证据链带报告 md。"""
        ev = str(rec.get("event") or "").strip()
        if not ev:
            return
        action = "farm." + re.sub(r"[^a-z0-9_]+", "_", ev.lower())
        rc = rec.get("rc")
        res = "ok" if rc == 0 else ("info" if rc is None else "fail")
        if "refus" in ev and rc is None:
            res = "refused"
        run_id_ = str(rec.get("run_id") or "")
        ev_paths = [p for p in (summary.get("report"), os.path.join(run_dir, "summary.json")) if p]
        params = {k: rec.get(k) for k in ("rc", "conclusion", "reason", "env", "decision", "lease")
                  if rec.get(k) not in (None, "")}
        params["account"] = summary.get("account")
        params["stage"] = summary.get("stage")
        params["apply"] = bool(summary.get("apply"))
        tools_dir = os.path.dirname(os.path.abspath(__file__))
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import mas_audit  # noqa: PLC0415
        mas_audit.safe_append(
            actor="tool:mas_farm_orchestrator", action=action,
            target=("run:" + run_id_) if run_id_ else "-", result=res,
            reason=str(rec.get("conclusion") or rec.get("reason") or ""), params=params,
            evidence=ev_paths or [paths["log"]], card=os.environ.get("HERMES_KANBAN_TASK", ""),
            run_id=run_id_, path=mas_audit.stream_for_runtime(rt))

    def audit(rec: dict) -> None:
        os.makedirs(rt, exist_ok=True)
        rec = dict(rec)
        rec.setdefault("ts", _now())
        with open(paths["log"], "a", encoding="utf-8") as f:
            f.write(json.dumps(scrub(rec), ensure_ascii=False) + "\n")
        try:
            _unified_from_audit(rec)
        except Exception:  # noqa: BLE001
            pass

    def release_lease_once() -> None:
        """⑨b 释放实例租约（只在真取到过时；幂等，任何终局路径都调）。"""
        lr = summary.get("lease") or {}
        sc = summary.get("script") or {}
        if not args.apply or lr.get("status") != "acquired" or not sc.get("emu"):
            return
        if summary.get("lease_release") is not None:
            return
        t0 = time.time()
        try:
            rel = d["lease_release"](sc.get("emu") or "", args.owner)
        except Exception as e:  # noqa: BLE001
            rel = {"ok": False, "status": "error", "detail": "%s: %s" % (type(e).__name__, e)}
        summary["lease_release"] = scrub(rel)
        add_step("lease_release", bool(rel.get("ok")), time.time() - t0,
                 {"status": rel.get("status"), "idx": sc.get("emu"), "detail": rel.get("detail")})
        if not rel.get("ok"):
            summary["warnings"].append("释放实例租约异常：%s" % rel.get("detail"))
        audit({"run_id": run_id, "event": "lease_release", "status": rel.get("status"),
               "idx": sc.get("emu"), "detail": str(rel.get("detail"))[:200]})

    def finish(rc: int) -> tuple:
        summary["rc"] = rc
        summary["seconds"] = int((dt.datetime.now() - dt.datetime.strptime(t_start, TS_FMT)).total_seconds())
        release_lease_once()
        # 结论一句话
        s_steps = {s["step"]: s for s in summary["steps"]}
        if rc == 0:
            if not args.apply:
                summary["conclusion"] = "dry-run 全链预演通过（%s；未发车、未写任何配置）" % (
                    "未知台账 ⇒ 真跑会先试探一次" if (summary.get("decision") or {}).get("action") == "probe"
                    else "台账 success ⇒ 真跑直接正式刷")
            else:
                summary["conclusion"] = "整链跑通（%s）" % (
                    "试探→执行→还原" if summary.get("probe") else "台账直刷→执行→还原")
        else:
            bad = [s["step"] for s in summary["steps"] if not s["ok"]]
            summary["conclusion"] = "卡在 %s（rc=%d）" % ("、".join(bad) or "?", rc)
        report_path = write_report(paths["reports"], summary, run_dir)
        summary["report"] = report_path
        save_json(os.path.join(run_dir, "summary.json"), scrub(summary))
        try:
            d["ledger_append"]({"kind": "farm_orchestrator", "ok": rc == 0, "run_id": run_id,
                                "scope": "%s × %s（%s）" % (args.account, args.stage,
                                                            "apply" if args.apply else "dry-run"),
                                "detail": json.dumps({
                                    "rc": rc, "decision": (summary.get("decision") or {}).get("action"),
                                    "probe_rc": (summary.get("probe") or {}).get("rc"),
                                    "farm_rc": (summary.get("farm") or {}).get("rc"),
                                    "lease": (summary.get("lease") or {}).get("status"),
                                    "conclusion": summary["conclusion"]}, ensure_ascii=False)[:400]})
        except Exception as e:  # noqa: BLE001
            summary["warnings"].append("写全局台账失败：%s" % e)
        audit({"run_id": run_id, "event": "done", "rc": rc, "conclusion": summary["conclusion"]})
        return rc, summary

    # ---- 闸门（apply 才有一票否决）----
    if args.apply:
        go = no_go()
        if go:
            add_step("gate", False, 0, {"reason": "no_go", "env": go})
            audit({"run_id": run_id, "event": "refused", "reason": "no_go", "env": go})
            print("拒绝：%s 置位（一票否决）" % go, file=sys.stderr)
            return finish(2)

    # ---- ① 目标解析（只读）----
    t0 = time.time()
    try:
        script = d["resolve"](args.account, args.script or "")
    except RefuseError as e:
        add_step("resolve", False, time.time() - t0, {"error": str(e)})
        print("拒绝：%s" % e, file=sys.stderr)
        return finish(2)
    except Exception as e:  # noqa: BLE001
        add_step("resolve", False, time.time() - t0, {"error": "%s: %s" % (type(e).__name__, e)})
        print("拒绝：无法解析脚本（REST）：%s: %s" % (type(e).__name__, e), file=sys.stderr)
        return finish(2)
    summary["script"] = {"name": script["name"], "sid": script["sid"], "dir": script["dir"], "emu": script["emu"]}
    add_step("resolve", True, time.time() - t0,
             {"script": script["name"], "dir": script["dir"], "emu": script["emu"]})
    log("目标：%s × %s ⇒ 脚本 %s（%s，实例 %s）"
        % (args.account, args.stage, script["name"], script["dir"], script["emu"] or "?"))

    # 多号脚本闸门（fail-closed；零成本，先于任何动作）
    if d.get("script_users") and not args.all_accounts:
        try:
            users = d["script_users"](script["sid"])
            enabled = [u for u in users if u.get("status") and str(u.get("remained_day")) != "0"]
            others = [u["name"] for u in enabled if u["name"] != args.account]
            me = [u for u in enabled if u["name"] == args.account]
            if not me:
                add_step("resolve", False, 0, {"error": "账号未启用/RemainedDay=0"})
                print("拒绝：账号 %s 未启用或 RemainedDay=0 —— AutoProxy 会跳过它" % args.account, file=sys.stderr)
                return finish(2)
            if others and not args.disable_others:
                add_step("resolve", False, 0, {"error": "multi_account_needs_explicit", "others": others})
                print("拒绝：脚本 %s 还有其它启用号（%s）——发车粒度只到脚本；"
                      "加 --disable-others（临时停用它们）或 --all-accounts（接受整脚本跑）"
                      % (script["name"], "、".join(others)), file=sys.stderr)
                return finish(2)
        except Exception as e:  # noqa: BLE001
            summary["warnings"].append("多号闸门检查未跑通（%s）——交给 run_profile 自己的闸门兜底" % e)

    # ---- ② preflight（只读闸门；顺带查租约是否被别人占）----
    t0 = time.time()
    pf = d["preflight"](script["name"])
    pf_ok = str(pf.get("verdict")) == "GO"
    add_step("preflight", pf_ok, time.time() - t0,
             {"verdict": pf.get("verdict"), "exit": pf.get("exit"), "failing": pf.get("failing"),
              "reasons": pf.get("reasons")})
    log("preflight：%s%s" % (pf.get("verdict"), ("（不过项：%s）" % pf.get("failing")) if pf.get("failing") else ""))
    if not pf_ok:
        summary["warnings"].append("preflight 不过：%s" % json.dumps(
            {"verdict": pf.get("verdict"), "failing": pf.get("failing"), "reasons": pf.get("reasons")},
            ensure_ascii=False)[:300])
        return finish(2)
    lease_pre = d["lease_check"](script["emu"], args.owner)
    summary["lease_check"] = scrub(lease_pre)
    add_step("lease_check", bool(lease_pre.get("ok")), 0, lease_pre)
    if not lease_pre.get("ok"):
        summary["warnings"].append("实例 %s 已被别人占用（%s）——先停，别抢" % (script["emu"], lease_pre.get("detail")))
        return finish(2)

    # ---- ③ 台账查询 ----
    t0 = time.time()
    lg = d["ledger"](args.account, args.stage)
    summary["ledger"] = {"status": lg.get("status"), "report_built_at": lg.get("report_built_at"),
                         "row": scrub(lg.get("row")), "missing_report": lg.get("missing_report")}
    add_step("ledger", True, time.time() - t0,
             {"status": lg.get("status"), "report_built_at": lg.get("report_built_at"),
              "has_row": bool(lg.get("row"))})
    log("台账：%s × %s = %s（报告 built_at=%s）"
        % (args.account, args.stage, lg.get("status"), lg.get("report_built_at") or "?"))

    # ---- ④ 决策 ----
    dec = decide(lg.get("status"), force_probe=args.force_probe, fix_note=args.retry_after_fix or "")
    summary["decision"] = dec
    add_step("decision", dec["action"] != "refuse", 0, dec)
    log("决策：%s —— %s" % (dec["action"], dec["why"]))
    if dec["action"] == "refuse":
        audit({"run_id": run_id, "event": "refused", "reason": "ledger_failed"})
        return finish(2)

    # ---- ⑤ 试探（unknown/force）----
    probe_done = False
    if dec["action"] == "probe":
        if not args.apply:
            add_step("probe", True, 0, {"skipped": "dry-run（将执行：mas_probe_farm --probe-only --apply）"})
        else:
            t0 = time.time()
            with hold_lock(paths["lock"], True) as touch:
                pr = d["probe"](args, script["name"], args.stage, args.medicine, args.timeout_s, run_dir)
                touch()
            probe_done = True
            prc = pr.get("rc")
            psummary = pr.get("summary") or {}
            verdict = (psummary.get("verdict") or {})
            summary["probe"] = {"rc": prc, "timeout": pr.get("timeout"), "seconds": pr.get("seconds"),
                                "run_id": psummary.get("run_id"), "verdict": scrub(verdict),
                                "ledger_update": scrub(psummary.get("ledger_update")),
                                "triage": scrub(psummary.get("triage")),
                                "cmd": pr.get("cmd")}
            add_step("probe", prc == 0, time.time() - t0,
                     {"rc": prc, "timeout": pr.get("timeout"), "verdict_status": verdict.get("status"),
                      "verdict_kind": verdict.get("kind"), "reason": verdict.get("reason"),
                      "probe_run_id": psummary.get("run_id")})
            log("试探：rc=%s 判定=%s/%s（%s）"
                % (prc, verdict.get("status"), verdict.get("kind"), str(verdict.get("reason"))[:80]))
            if pr.get("timeout"):
                summary["warnings"].append("试探子进程超时被终止——可能有残留！复核 mas_run_profile --status")
                return finish(4)
            if prc == 2:
                summary["warnings"].append("试探在发车前失败/被拒（rc=2）——没有发出任务，台账不动")
                return finish(2)
            if prc == 4:
                summary["warnings"].append("试探还原失败（rc=4）——有残留，需要人工复核")
                return finish(4)
            if prc != 0:
                summary["warnings"].append("试探未证明可刷（rc=%s，%s/%s）——按约定停止，不重试"
                                           % (prc, verdict.get("status"), verdict.get("kind")))
                return finish(3)
            # 试探成功：台账应已更新为 success
            lg2 = d["ledger"](args.account, args.stage)
            if lg2.get("status") != ST_SUCCESS:
                summary["warnings"].append("试探成功但台账读回=%s（≠success）——如实记录" % lg2.get("status"))
            log("试探成功，台账读回=%s" % lg2.get("status"))

    # ---- ⑥ 生成计划（profile + 校验 + 干跑预览）----
    t0 = time.time()
    prof = build_farm_profile(args.account, script["name"], args.stage, args.times, args.medicine,
                              args.remain_sanity,
                              notes="farm_orchestrator %s × %s（times=SeriesNumb=%s）"
                                    % (args.account, args.stage, args.times))
    norm, warns, errs = validate_profile(prof)
    prof_path = os.path.join(run_dir, "profile.farm.json")
    save_json(prof_path, prof)
    if errs:
        add_step("plan", False, time.time() - t0, {"errors": errs})
        print("拒绝：计划 profile 不合法：%s" % "；".join(errs), file=sys.stderr)
        return finish(2)
    summary["plan"] = {"profile": scrub(prof), "path": prof_path, "warnings": warns,
                       "series": str(args.times), "medicine": int(args.medicine)}
    prev = d["plan_preview"](prof_path, lease_owner=args.owner)
    psum = prev.get("summary") or {}
    refusals = psum.get("refusals") or []
    summary["plan"]["preview"] = {"rc": prev.get("rc"), "refusals": scrub(refusals),
                                  "warnings": scrub(psum.get("warnings") or []),
                                  "expected": scrub(psum.get("expected") or {}),
                                  "accounts": psum.get("accounts") or []}
    plan_ok = (prev.get("rc") == 0 and not refusals)
    add_step("plan", plan_ok, time.time() - t0,
             {"profile": prof_path, "series": str(args.times), "medicine": int(args.medicine),
              "preview_rc": prev.get("rc"), "refusals": scrub(refusals)})
    log("计划：profile=%s（series=%s，medicine=%s）干跑 rc=%s%s"
        % (os.path.basename(prof_path), args.times, args.medicine, prev.get("rc"),
           "，拒绝=%s" % refusals if refusals else ""))
    if not plan_ok:
        summary["warnings"].append("计划干跑被拒：%s" % json.dumps(refusals, ensure_ascii=False)[:300])
        return finish(2)

    # ---- ⑦ 取实例租约 ----
    t0 = time.time()
    purpose = "farm_orchestrator %s×%s（%s）" % (args.account, args.stage, args.owner)
    lr = d["lease_acquire"](script["emu"], args.owner, purpose, not args.apply)
    summary["lease"] = scrub(lr)
    lease_ok = bool(lr.get("ok"))
    add_step("lease", lease_ok, time.time() - t0,
             {"status": lr.get("status"), "detail": lr.get("detail"), "idx": lr.get("idx"),
              "expires": (lr.get("lease") or {}).get("expires_at") if isinstance(lr.get("lease"), dict) else None})
    log("租约：%s（%s）" % (lr.get("status"), str(lr.get("detail"))[:120]))
    if lr.get("status") == "skipped":
        summary["warnings"].append("租约跳过：%s" % lr.get("detail"))
    if not lease_ok:
        summary["warnings"].append("实例租约拿不到：%s" % lr.get("detail"))
        return finish(2)

    # ---- ⑧ 执行 ----
    t0 = time.time()
    if not args.apply:
        add_step("execute", True, 0, {"skipped": "dry-run（将执行：mas_run_profile --profile %s --apply）"
                                               % os.path.basename(prof_path)})
        return finish(0)
    with hold_lock(paths["lock"], True) as touch:
        fr = d["farm"](prof_path, args, args.farm_max_wait, run_dir)
        touch()
    frc = fr.get("rc")
    fsum = fr.get("summary") or {}
    summary["farm"] = {"rc": frc, "timeout": fr.get("timeout"), "seconds": fr.get("seconds"),
                       "run_id": fsum.get("run_id"), "failure": scrub(fsum.get("failure")),
                       "task_uid": fsum.get("task_uid"), "backup": fsum.get("backup"),
                       "readback": scrub(fsum.get("readback")), "wait": scrub(fsum.get("wait")),
                       "l2": scrub(fsum.get("l2")), "restore": scrub(fsum.get("restore")),
                       "cmd": fr.get("cmd")}
    add_step("execute", frc == 0, time.time() - t0,
             {"rc": frc, "timeout": fr.get("timeout"), "run_id": fsum.get("run_id"),
              "task_uid": fsum.get("task_uid"), "failure": scrub(fsum.get("failure"))})
    log("执行：rc=%s run_id=%s%s" % (frc, fsum.get("run_id"), "（超时被终止！）" if fr.get("timeout") else ""))
    if fr.get("timeout"):
        summary["warnings"].append("执行子进程超时被终止——可能有残留！复核 mas_run_profile --status")
        return finish(4)

    # ---- ⑨ 读回校验（run_profile L1/L2 结果）----
    rb = (fsum.get("readback") or {})
    l2 = (fsum.get("l2") or {})
    wait = (fsum.get("wait") or {})
    rb_ok = bool(rb.get("ok")) and bool(wait.get("ended"))
    add_step("readback", rb_ok, 0,
             {"l1_ok": rb.get("ok"), "satisfied": rb.get("satisfied"), "polls": rb.get("polls"),
              "l2_ok": l2.get("ok"), "l2_diffs": l2.get("diffs"), "wait": scrub(wait)})
    if l2 and l2.get("ok") is False:
        summary["warnings"].append("L2（asst.log 实收参数）与期望有落差（默认仅告警）：%s" % l2.get("diffs"))

    # ---- ⑩ 还原复核（独立只读）----
    t0 = time.time()
    run_ids = [fsum.get("run_id")] if fsum.get("run_id") else []
    if summary.get("probe", {}) and (summary.get("probe") or {}).get("run_id"):
        pass
    chk = d["verify"](run_ids, script["sid"], [args.account], t_start)
    summary["restore"] = scrub(chk)
    restore_ok = (not chk.get("open_runs")) and (not chk.get("temp_plans")) and not chk.get("error")
    sm_ok = all(x.get("ok") for x in (chk.get("stage_mode") or [])) if chk.get("stage_mode") else True
    add_step("restore", restore_ok and sm_ok, time.time() - t0,
             {"open_runs": chk.get("open_runs"), "temp_plans": chk.get("temp_plans"),
              "stage_mode": chk.get("stage_mode"), "error": chk.get("error")})
    log("还原复核：open_runs=%s temp_plans=%s stage_mode=%s"
        % (chk.get("open_runs") or "无", chk.get("temp_plans") or "无",
           ("OK" if sm_ok else "不符！")))
    if not restore_ok or not sm_ok:
        summary["warnings"].append("还原复核异常：%s" % json.dumps(
            {"open_runs": chk.get("open_runs"), "temp_plans": chk.get("temp_plans"),
             "stage_mode": chk.get("stage_mode"), "error": chk.get("error")}, ensure_ascii=False)[:400])
        return finish(4)

    # ---- 结果映射 ----
    if frc == 0 and rb_ok:
        return finish(0)
    if frc == 4:
        return finish(4)
    # farm 非 0（已确认还原干净）
    if probe_done:
        summary["warnings"].append("试探成功但执行阶段失败（rc=%s）" % frc)
        return finish(5)
    return finish(frc if frc in (2, 3) else 3)


def default_deps() -> dict:
    return {
        "resolve": deps_resolve,
        "script_users": deps_script_users,
        "preflight": deps_preflight,
        "ledger": deps_ledger,
        "probe": deps_probe,
        "plan_preview": deps_plan_preview,
        "farm": deps_farm,
        "verify": deps_verify,
        "lease_check": lambda idx, owner: deps_lease_ctx(idx, owner, "", dry=True),
        "lease_acquire": deps_lease_ctx,
        "lease_release": deps_lease_release,
        "ledger_append": deps_ledger_append,
    }


# ---------------------------------------------------------------- 报告
def write_report(reports_dir: str, summary: dict, run_dir: str) -> str:
    ts = summary.get("run_id", "run").replace("fo_", "")
    path = os.path.join(reports_dir, "farm_run_%s.md" % ts)
    a = summary.get("account")
    st = summary.get("stage")
    L = []
    L.append("# 端到端刷关报告 — %s × %s" % (a, st))
    L.append("")
    L.append("> run_id `%s` ｜ 模式 **%s** ｜ 生成 %s ｜ rc=%s"
             % (summary.get("run_id"), "APPLY" if summary.get("apply") else "DRY-RUN",
                _now(), summary.get("rc")))
    L.append("> **结论：%s**" % summary.get("conclusion"))
    L.append("")
    sc = summary.get("script") or {}
    L.append("- 脚本：%s（目录 %s，实例 %s）" % (sc.get("name"), sc.get("dir"), sc.get("emu") or "?"))
    L.append("- 参数：times(SeriesNumb)=%s ｜ medicine=%s ｜ owner=%s"
             % ((summary.get("plan") or {}).get("series"), (summary.get("plan") or {}).get("medicine"),
                summary.get("owner") or ""))
    L.append("")
    L.append("## 1. 步骤表")
    L.append("")
    L.append("| # | 步骤 | 结果 | 耗时(s) | 判据/详情 |")
    L.append("|---|---|---|---:|---|")
    for i, s in enumerate(summary.get("steps") or [], 1):
        det = s.get("detail") or {k: v for k, v in s.items()
                                  if k not in ("step", "ok", "seconds", "ts", "detail")}
        L.append("| %d | %s | %s | %s | %s |"
                 % (i, s.get("step"), "OK" if s.get("ok") else "FAIL", s.get("seconds"),
                    json.dumps(det, ensure_ascii=False)[:300].replace("|", "\\|")))
    L.append("")
    L.append("## 2. 判定链")
    L.append("")
    lg = summary.get("ledger") or {}
    dec = summary.get("decision") or {}
    L.append("1. **台账**：%s × %s = `%s`（报告 built_at %s%s）"
             % (a, st, lg.get("status"), lg.get("report_built_at") or "?",
                "；台账报告缺失" if lg.get("missing_report") else ""))
    row = lg.get("row") or {}
    if row:
        L.append("   - 最近成功 %s ｜ 最近失败 %s ｜ 失败类型 %s ｜ 能力类失败 ×%s"
                 % (row.get("last_success") or "-", row.get("last_failure") or "-",
                    row.get("failure_types") or {}, row.get("fail_n")))
    L.append("2. **决策**：`%s` —— %s" % (dec.get("action"), dec.get("why")))
    pr = summary.get("probe")
    if pr:
        v = pr.get("verdict") or {}
        L.append("3. **试探**（mas_probe_farm --probe-only）：rc=%s ｜ 判定 `%s/%s` ｜ %s"
                 % (pr.get("rc"), v.get("status"), v.get("kind"), str(v.get("reason"))[:160]))
        if pr.get("ledger_update"):
            lu = pr["ledger_update"]
            L.append("   - 台账更新：%s → %s（update rc=%s）"
                     % (lu.get("before_status"), lu.get("after_status"), lu.get("rc")))
        if pr.get("run_id"):
            L.append("   - 试探证据目录：`runtime/probe_farm/%s/`" % pr.get("run_id"))
    else:
        L.append("3. **试探**：未发生（%s）"
                 % ("dry-run" if not summary.get("apply") else "台账 success 直刷"))
    pl = summary.get("plan") or {}
    if pl:
        L.append("4. **计划**：`%s`（series=%s medicine=%s keep_remain=%s）"
                 % (os.path.basename(str(pl.get("path"))), pl.get("series"), pl.get("medicine"),
                    (pl.get("profile") or {}).get("keep_remain_sanity_task")))
        pv = pl.get("preview") or {}
        exp = pv.get("expected") or {}
        for uid, e in exp.items():
            L.append("   - 干跑期望（%s）：payload=%s ｜ StagePlan=%s Series=%s MedicineCount=%s UseMedicine=%s 剩余=%s"
                     % (str(uid)[:8], json.dumps(e.get("payload"), ensure_ascii=False),
                        e.get("stage_plan"), e.get("series"), e.get("medicine"), e.get("use_medicine"),
                        e.get("remain")))
    ls = summary.get("lease") or {}
    L.append("5. **实例租约**：%s ｜ %s" % (ls.get("status"), str(ls.get("detail"))[:160]))
    if summary.get("lease_check"):
        L.append("   - 事前查租：%s" % json.dumps(summary["lease_check"], ensure_ascii=False)[:200])
    if summary.get("lease_release"):
        rl = summary["lease_release"]
        L.append("   - 释放：%s ｜ %s" % (rl.get("status"), str(rl.get("detail"))[:160]))
    fa = summary.get("farm")
    if fa:
        L.append("6. **执行**（mas_run_profile --apply）：rc=%s ｜ run_id `%s` ｜ task_uid `%s` ｜ 耗时 %ss"
                 % (fa.get("rc"), fa.get("run_id"), str(fa.get("task_uid"))[:8], fa.get("seconds")))
        if fa.get("failure"):
            L.append("   - failure：%s" % json.dumps(fa.get("failure"), ensure_ascii=False)[:300])
        rb = fa.get("readback") or {}
        L.append("   - 读回 L1：ok=%s（satisfied=%s，polls=%s）｜ L2：%s ｜ wait：%s"
                 % (rb.get("ok"), rb.get("satisfied"), rb.get("polls"),
                    json.dumps(fa.get("l2"), ensure_ascii=False)[:160],
                    json.dumps(fa.get("wait"), ensure_ascii=False)[:160]))
        if fa.get("backup"):
            L.append("   - 备份目录：`%s`" % fa.get("backup"))
    else:
        L.append("6. **执行**：未发生（dry-run）")
    rs = summary.get("restore") or {}
    if rs:
        L.append("7. **还原复核**（独立只读）：runs=%s ｜ open_runs=%s ｜ temp_plans=%s ｜ stage_mode=%s"
                 % (json.dumps(rs.get("runs"), ensure_ascii=False),
                    rs.get("open_runs") or "无", rs.get("temp_plans") or "无",
                    json.dumps(rs.get("stage_mode"), ensure_ascii=False)[:200]))
    L.append("")
    L.append("## 3. 命令与证据")
    L.append("")
    L.append("- run 目录：`%s`" % run_dir)
    if summary.get("probe"):
        L.append("- 试探命令：`%s`" % (summary["probe"].get("cmd") or ""))
    if summary.get("farm"):
        L.append("- 执行命令：`%s`" % (summary["farm"].get("cmd") or ""))
    L.append("- 全局台账：`runtime/mas_ledger.jsonl`（kind=farm_orchestrator）；本工具流水："
             "`runtime/mas_farm_orchestrator_log.jsonl`")
    if summary.get("warnings"):
        L.append("")
        L.append("## 4. 告警与备注")
        L.append("")
        for w in summary["warnings"]:
            L.append("- %s" % w)
    L.append("")
    save_text(path, "\n".join(L) + "\n")
    return path


# ---------------------------------------------------------------- 只读命令
def cmd_status(args) -> int:
    log_path = os.path.join(RUNTIME, "mas_farm_orchestrator_log.jsonl")
    lock = os.path.join(RUNTIME, "mas_farm_orchestrator.lock")
    print("锁：%s" % ("空闲" if not os.path.exists(lock) else "被占（%s）" % lock))
    runs = sorted(d for d in os.listdir(RUNS_DIR) if os.path.isdir(os.path.join(RUNS_DIR, d))) \
        if os.path.isdir(RUNS_DIR) else []
    print("run 目录（最近 8 个）：%s" % ("、".join(runs[-8:]) or "—"))
    if not os.path.exists(log_path):
        print("（无流水 %s）" % log_path)
        return 0
    lines = [x for x in open(log_path, "r", encoding="utf-8").read().splitlines() if x.strip()]
    for ln in lines[-max(1, int(args.tail)):]:
        with contextlib.suppress(ValueError):
            rec = json.loads(ln)
            print("  %s %-18s %s" % (rec.get("ts"), rec.get("event"),
                                     json.dumps({k: v for k, v in rec.items()
                                                 if k not in ("ts", "event")}, ensure_ascii=False)[:220]))
    return 0


# ---------------------------------------------------------------- 离线自测
def selftest() -> dict:
    import tempfile
    cases = []

    def case(name, fn):
        try:
            fn()
            cases.append({"name": name, "ok": True, "detail": ""})
        except Exception as e:  # noqa: BLE001
            cases.append({"name": name, "ok": False, "detail": "%s: %s" % (type(e).__name__, e)})

    def chk(cond, msg=None):
        if not cond:
            raise AssertionError(repr(msg) if msg is not None else "条件不成立")

    def ns(**kw):
        base = dict(account="甲", stage="1-7", times="1", medicine=0, script="", write_mode="plan",
                    retry_after_fix="", remain_sanity=False, disable_others=False, all_accounts=False,
                    force_probe=False, timeout_s=600, farm_max_wait=600, apply=False,
                    owner="t_selftest")
        base.update(kw)
        return argparse.Namespace(**base)

    SCRIPT = {"name": "明日方舟-官-1", "sid": "sid-1", "dir": r"E:\MAA-Multi\MAA_1", "emu": "13"}

    def mk_deps(preflight="GO", ledger_status="unknown", probe_rc=0, probe_verdict=None,
                farm_rc=0, lease_ok=True, lease_status=None, users=None):
        calls = []

        def resolve(account, token):
            calls.append("resolve")
            return dict(SCRIPT)

        def script_users(sid):
            return users if users is not None else [{"name": "甲", "status": True, "remained_day": -1}]

        def preflight_fn(name):
            calls.append("preflight")
            if preflight == "GO":
                return {"verdict": "GO", "exit": 0, "failing": [], "reasons": []}
            return {"verdict": preflight, "exit": 1, "failing": ["maa_slot"], "reasons": ["槽位忙"]}

        def ledger(account, stage):
            calls.append("ledger")
            row = None
            if ledger_status in ("success", "failed"):
                row = {"account": account, "stage": stage, "status": ledger_status,
                       "last_success": "2026-09-28" if ledger_status == "success" else "",
                       "last_failure": "" if ledger_status == "success" else "2026-09-27",
                       "failure_types": {} if ledger_status == "success" else {"stage_not_open": 1},
                       "fail_n": 0 if ledger_status == "success" else 1}
            return {"status": ledger_status, "row": row, "report_built_at": "2026-09-28 06:00:00",
                    "missing_report": False, "json_path": "x"}

        def probe(args, script_name, stage, medicine, timeout_s, run_dir):
            calls.append("probe")
            v = probe_verdict or ({"status": "success", "kind": "stage_farmed", "reason": "drop_statistics 出现该关"}
                                  if probe_rc == 0 else
                                  {"status": "failed", "kind": "stage_not_open", "reason": "未开放"})
            return {"rc": probe_rc, "timeout": False, "seconds": 3, "cmd": "probe-cmd",
                    "summary": {"tool": "mas_probe_farm", "run_id": "pf_x", "rc": probe_rc, "verdict": v,
                                "ledger_update": {"before_status": "unknown", "after_status": "success", "rc": 0},
                                "triage": None}}

        def plan_preview(profile_path, timeout=900, lease_owner=None):
            calls.append("preview")
            return {"rc": 0, "summary": {"tool": "mas_run_profile", "refusals": [],
                                         "warnings": [], "expected": {"uid1": {"payload": {"Stage": "1-7"},
                                                                              "stage_plan": ["1-7"], "series": 1,
                                                                              "medicine": 0, "use_medicine": False,
                                                                              "remain": None}},
                                         "accounts": ["甲"]}}

        def farm(profile_path, args, farm_max_wait, run_dir):
            calls.append("farm")
            return {"rc": farm_rc, "timeout": False, "seconds": 5, "cmd": "farm-cmd",
                    "summary": {"tool": "mas_run_profile", "run_id": "rp_x", "rc": farm_rc,
                                "failure": None if farm_rc == 0 else ["readback_mismatch", "读回不符"],
                                "task_uid": "tid", "backup": "bk",
                                "readback": {"ok": farm_rc == 0, "satisfied": ["uid1"], "polls": 3},
                                "wait": {"ended": True, "reason": "run_end", "duration_s": 120},
                                "l2": {"ok": True}, "restore": {"status": "restored"}}}

        def verify(run_ids, sid, accounts, started_at):
            calls.append("verify")
            return {"runs": {"rp_x": "restored"}, "open_runs": [], "temp_plans": [],
                    "stage_mode": [{"run_id": "rp_x", "account": "甲", "prev": "Fixed", "now": "Fixed", "ok": True}],
                    "errors": []}

        def lease_check(idx, owner):
            return {"ok": lease_ok, "status": "check:free" if lease_ok else "check:busy",
                    "detail": "空着" if lease_ok else "被 t_x 占着"}

        def lease_acquire(idx, owner, purpose, dry):
            calls.append("lease")
            if not lease_ok:
                return {"ok": False, "status": "busy", "detail": "被 t_x 占着"}
            return {"ok": True, "status": "acquired", "detail": "取租成功", "idx": "13",
                    "lease": {"expires_at": "2026-09-28 10:00:00"}}

        def lease_release(idx, owner):
            calls.append("lease_release")
            return {"ok": True, "status": "released", "detail": "已释放"}

        def ledger_append(rec):
            calls.append("ledger_append")

        return {"resolve": resolve, "script_users": script_users, "preflight": preflight_fn,
                "ledger": ledger, "probe": probe, "plan_preview": plan_preview, "farm": farm,
                "verify": verify, "lease_check": lease_check, "lease_acquire": lease_acquire,
                "lease_release": lease_release, "ledger_append": ledger_append}, calls

    tmp = tempfile.mkdtemp(prefix="farm_orch_selftest_")

    def run_case(args, deps):
        return run(args, deps=deps, runtime_dir=tmp)

    # ---- 决策矩阵 ----
    def t_decide_success():
        chk(decide("success")["action"] == "farm")
        chk(decide("success", force_probe=True)["action"] == "probe")

    def t_decide_unknown():
        chk(decide("unknown")["action"] == "probe")

    def t_decide_failed():
        chk(decide("failed")["action"] == "refuse")
        chk(decide("failed", fix_note="已修")["action"] == "probe")

    def t_profile_build():
        p = build_farm_profile("甲", "S", "1-7", "6", 2, False)
        chk(p["stage_plan"] == ["1-7"] and p["series"] == "6", p)
        chk(p["medicine"] == {"use": True, "count": 2} and p["restore_after"] == "run_end")
        chk(p["activity_priority"] == {"enabled": False, "medicine": 0})
        norm, warns, errs = validate_profile(p)
        chk(not errs, errs)

    def t_profile_bad_times():
        p = build_farm_profile("甲", "S", "1-7", "9", 0, False)
        norm, warns, errs = validate_profile(p)
        chk(errs, "series=9 应被拒")

    # ---- 全链（fake）----
    def t_chain_unknown_success():
        deps, calls = mk_deps(ledger_status="unknown", probe_rc=0, farm_rc=0)
        rc, sm = run_case(ns(apply=True), deps)
        chk(rc == 0, (rc, sm.get("conclusion")))
        chk([s["step"] for s in sm["steps"]] ==
            ["resolve", "preflight", "lease_check", "ledger", "decision", "probe", "plan", "lease",
             "execute", "readback", "restore", "lease_release"], [s["step"] for s in sm["steps"]])
        chk(sm["conclusion"].startswith("整链跑通"), sm["conclusion"])
        chk(sm["report"] and os.path.exists(sm["report"]), sm["report"])
        chk("lease_release" in calls, calls)
        chk((sm.get("lease_release") or {}).get("ok") is True, sm.get("lease_release"))

    def t_chain_success_direct():
        deps, calls = mk_deps(ledger_status="success", farm_rc=0)
        rc, sm = run_case(ns(apply=True), deps)
        chk(rc == 0, rc)
        chk("probe" not in calls, calls)

    def t_chain_failed_refuse():
        deps, calls = mk_deps(ledger_status="failed")
        rc, sm = run_case(ns(apply=True), deps)
        chk(rc == 2, rc)
        chk("probe" not in calls and "farm" not in calls, calls)
        chk(sm["conclusion"].startswith("卡在"), sm["conclusion"])

    def t_chain_preflight_nogo():
        deps, calls = mk_deps(preflight="NO-GO")
        rc, sm = run_case(ns(apply=True), deps)
        chk(rc == 2, rc)
        chk("ledger" not in calls, calls)

    def t_chain_probe_failed():
        deps, calls = mk_deps(ledger_status="unknown", probe_rc=3)
        rc, sm = run_case(ns(apply=True), deps)
        chk(rc == 3, rc)
        chk("farm" not in calls, calls)

    def t_chain_probe_rc2():
        deps, calls = mk_deps(ledger_status="unknown", probe_rc=2)
        rc, sm = run_case(ns(apply=True), deps)
        chk(rc == 2, rc)

    def t_chain_probe_rc4():
        deps, calls = mk_deps(ledger_status="unknown", probe_rc=4)
        rc, sm = run_case(ns(apply=True), deps)
        chk(rc == 4, rc)

    def t_chain_farm_fail_after_probe():
        deps, calls = mk_deps(ledger_status="unknown", probe_rc=0, farm_rc=3)
        rc, sm = run_case(ns(apply=True), deps)
        chk(rc == 5, rc)
        chk("lease_release" in calls, calls)          # 执行失败也要放租约

    def t_chain_farm_rc2_no_probe():
        deps, calls = mk_deps(ledger_status="success", farm_rc=2)
        rc, sm = run_case(ns(apply=True), deps)
        chk(rc == 2, rc)

    def t_chain_lease_busy():
        deps, calls = mk_deps(ledger_status="unknown", probe_rc=0, lease_ok=False)
        rc, sm = run_case(ns(apply=True), deps)
        chk(rc == 2, rc)
        chk("farm" not in calls, calls)

    def t_chain_lease_busy_precheck():
        deps, calls = mk_deps(lease_ok=False)
        rc, sm = run_case(ns(apply=True), deps)
        chk(rc == 2 and "ledger" not in calls, calls)   # 事前查租被占 ⇒ 连台账都不查

    def t_chain_multiuser_gate():
        deps, calls = mk_deps(users=[{"name": "甲", "status": True, "remained_day": -1},
                                     {"name": "乙", "status": True, "remained_day": -1}])
        rc, sm = run_case(ns(apply=True), deps)
        chk(rc == 2, rc)
        chk("preflight" not in calls, calls)

    def t_chain_multiuser_disable_others():
        deps, calls = mk_deps(users=[{"name": "甲", "status": True, "remained_day": -1},
                                     {"name": "乙", "status": True, "remained_day": -1}],
                              ledger_status="success", farm_rc=0)
        rc, sm = run_case(ns(apply=True, disable_others=True), deps)
        chk(rc == 0, rc)

    def t_chain_dry_run():
        deps, calls = mk_deps(ledger_status="unknown")
        rc, sm = run_case(ns(apply=False), deps)
        chk(rc == 0, rc)
        chk("probe" not in calls and "farm" not in calls, calls)
        chk("preview" in calls, calls)
        chk("lease_release" not in calls, calls)      # dry-run 只查租，不取不释放
        chk(sm["conclusion"].startswith("dry-run 全链预演通过"), sm["conclusion"])
        chk(sm["steps"][-1]["step"] == "execute" and sm["steps"][-1].get("detail", {}).get("skipped"), sm["steps"])

    def t_no_go_env():
        os.environ["MAAORCH_FARM_ORCH_NO_GO"] = "1"
        try:
            deps, calls = mk_deps()
            rc, sm = run_case(ns(apply=True), deps)
            chk(rc == 2, rc)
            chk("resolve" not in calls, calls)
        finally:
            os.environ.pop("MAAORCH_FARM_ORCH_NO_GO", None)

    def t_stage_annihilation_refused():
        rc = main(["--account", "甲", "--stage", "Annihilation", "--selftest-stage-guard"])
        # 走 main 的闸门（会直接拒绝，不进 run）
        chk(rc == 2, rc)

    def t_report_render():
        deps, calls = mk_deps(ledger_status="unknown", probe_rc=0, farm_rc=0)
        rc, sm = run_case(ns(apply=True), deps)
        txt = open(sm["report"], "r", encoding="utf-8").read()
        for token in ("端到端刷关报告", "结论", "步骤表", "判定链", "台账", "试探", "计划", "实例租约",
                      "执行", "还原复核", "命令与证据"):
            chk(token in txt, token)

    def t_lock_semantics():
        p = os.path.join(tmp, "lk")
        with hold_lock(p, True):
            chk(os.path.exists(p))
        chk(not os.path.exists(p))

    def t_farm_cmd_lease_owner():
        """⑥/④b 必须把本工具的租约持有者透给 run_profile——否则 G3L 同族自锁（把自己当外人占着而拒发）。"""
        seen = []

        def fake(cmd, timeout, cwd=None):
            seen.append([str(c) for c in cmd])
            return {"cmd": " ".join(str(c) for c in cmd), "rc": 0, "timeout": False, "out": "", "err": "",
                    "seconds": 0, "summary": None}

        real = globals()["_run_cmd"]
        globals()["_run_cmd"] = fake
        try:
            deps_farm("P.json", ns(apply=True), 60.0, "run_dir")
            deps_plan_preview("P.json", lease_owner="t_selftest")
        finally:
            globals()["_run_cmd"] = real
        chk(len(seen) == 2, seen)
        chk("--apply" in seen[0] and "--lease-owner" in seen[0] and "t_selftest" in seen[0],
           "⑥ farm 命令应带 --lease-owner：%r" % seen[0])
        chk("--lease-owner" in seen[1] and "t_selftest" in seen[1] and "--apply" not in seen[1],
           "④b 预览命令应带 --lease-owner（仍无 --apply）：%r" % seen[1])

    def t_times_domain():
        chk("6" in TIMES_OK and "9" not in TIMES_OK)

    case("决策：success ⇒ 直刷/force 试探", t_decide_success)
    case("决策：unknown ⇒ 试探", t_decide_unknown)
    case("决策：failed ⇒ 拒绝/修复后试探", t_decide_failed)
    case("计划：profile 字段与校验", t_profile_build)
    case("计划：非法 times 被拒", t_profile_bad_times)
    case("全链：unknown→试探→执行 rc=0", t_chain_unknown_success)
    case("全链：success 直刷（跳过试探）", t_chain_success_direct)
    case("全链：台账 failed 拒绝", t_chain_failed_refuse)
    case("全链：preflight NO-GO 停", t_chain_preflight_nogo)
    case("全链：试探 rc=3 ⇒ 3", t_chain_probe_failed)
    case("全链：试探 rc=2 ⇒ 2", t_chain_probe_rc2)
    case("全链：试探 rc=4 ⇒ 4", t_chain_probe_rc4)
    case("全链：试探成功+执行失败 ⇒ 5", t_chain_farm_fail_after_probe)
    case("全链：无试探+执行 rc=2 ⇒ 2", t_chain_farm_rc2_no_probe)
    case("全链：租约被占（事后取租）⇒ 2", t_chain_lease_busy)
    case("全链：事前查租被占 ⇒ 早停", t_chain_lease_busy_precheck)
    case("全链：多号脚本 fail-closed", t_chain_multiuser_gate)
    case("全链：--disable-others 放行", t_chain_multiuser_disable_others)
    case("全链：dry-run 零发车", t_chain_dry_run)
    case("闸门：NO_GO 一票否决", t_no_go_env)
    case("闸门：剿灭拒绝", t_stage_annihilation_refused)
    case("报告：关键小节齐全", t_report_render)
    case("锁：创建/释放", t_lock_semantics)
    case("租约：⑥/④b 命令带 --lease-owner（同族不自锁）", t_farm_cmd_lease_owner)
    case("取值域：times", t_times_domain)
    passed = sum(1 for c in cases if c["ok"])
    return {"ok": passed == len(cases), "passed": passed, "failed": len(cases) - passed, "cases": cases}


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="端到端刷关编排器：preflight → 台账 → 试探(unknown) → 计划 → 租约 → 执行 → 读回 → 还原 → 报告")
    ap.add_argument("--account", help="账号名（与 AUTO-MAS 一致）")
    ap.add_argument("--stage", help="关卡码（1-7 / CE-6 / SR-7…；剿灭请走 mas_run_profile 对应路线）")
    ap.add_argument("--times", default="1",
                    help="正式刷的连战倍数 SeriesNumb（0..6，0=自动，-1=不调整；**不是总次数**）。默认 1")
    ap.add_argument("--medicine", type=int, default=0, help="理智药额度（0=不吃；默认 0=保守）")
    ap.add_argument("--script", help="目标脚本名/uid（默认按账号自动解析；必须唯一命中）")
    ap.add_argument("--force-probe", action="store_true", help="台账 success 也先试探一次")
    ap.add_argument("--retry-after-fix", metavar="说明", default="",
                    help="台账 failed 时放行（进试探）：写一句话说明修了什么")
    ap.add_argument("--remain-sanity", dest="remain_sanity", action="store_true",
                    help="保留「剩余理智」任务（复制该号当日 Stage_Remain；默认不挂）")
    ap.add_argument("--timeout", type=float, default=1500.0, dest="timeout_s",
                    help="试探等待上限秒（restore_after=timeout:Ns；默认 1500——冷启动+完整日常链"
                         "实测要 16~18 分钟才打完第一场，600 会在开打前就停）")
    ap.add_argument("--farm-max-wait", type=float, default=10800.0,
                    help="执行等待上限秒（restore_after=run_end 的兜底，默认 10800=3h）")
    ap.add_argument("--write-mode", choices=["plan", "fixed"], default="plan",
                    help="发车写路线（默认 plan=临时计划表）")
    ap.add_argument("--disable-others", dest="disable_others", action="store_true",
                    help="多号脚本：临时停用其它启用号（透传子工具）")
    ap.add_argument("--all-accounts", dest="all_accounts", action="store_true",
                    help="多号脚本：接受整脚本跑（透传子工具）")
    ap.add_argument("--owner", default=os.environ.get("HERMES_KANBAN_TASK") or "farm_orchestrator",
                    help="实例租约持有者标识（默认取 HERMES_KANBAN_TASK）")
    ap.add_argument("--apply", action="store_true", help="真执行（默认 dry-run：只打印计划，零写）")
    ap.add_argument("--dry-run", action="store_true", help="显式 dry-run（默认行为）")
    ap.add_argument("--status", action="store_true", help="只读：最近运行 + 锁状态")
    ap.add_argument("--tail", type=int, default=15, help="--status 显示条数")
    ap.add_argument("--selftest", action="store_true", help="离线自测（零触网）")
    ap.add_argument("--json", action="store_true", help="JSON 输出（单行）")
    ap.add_argument("--selftest-stage-guard", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    if a.selftest:
        rep = selftest()
        if a.json:
            print(json.dumps(rep, ensure_ascii=False))
        else:
            print("SELFTEST=%s (%d/%d)" % ("OK" if rep["ok"] else "FAIL", rep["passed"],
                                           rep["passed"] + rep["failed"]))
            for c in rep["cases"]:
                print("  %s %s%s" % ("PASS" if c["ok"] else "FAIL", c["name"],
                                     "" if c["ok"] else "  ← " + c["detail"]))
        return 0 if rep["ok"] else 3
    if a.status:
        return cmd_status(a)
    if not a.account or not a.stage:
        ap.print_help()
        return 2
    a.account = str(a.account).strip()
    a.stage = _rp().normalize_stage(a.stage)
    if a.stage.lower() in ANNI_NAMES:
        print("拒绝：剿灭（%s）不在这条 7 键路线里——它由账号的 Info.Annihilation 配置，"
              "本工具不碰该字段（要试剿灭请用 mas_run_profile 的对应路线）。" % a.stage, file=sys.stderr)
        return 2
    if a.stage == "-":
        print("拒绝：--stage 不能为空", file=sys.stderr)
        return 2
    if str(a.times) not in TIMES_OK:
        print("拒绝：--times=%s 越界（只允许 %s）。注意 Series 是连战倍数、**不是总次数上限**。"
              % (a.times, "/".join(TIMES_OK)), file=sys.stderr)
        return 2
    if not (0 <= int(a.medicine) <= 9999):
        print("拒绝：--medicine 取值域 0–9999", file=sys.stderr)
        return 2
    if a.disable_others and a.all_accounts:
        print("拒绝：--disable-others 与 --all-accounts 互斥", file=sys.stderr)
        return 2
    if a.timeout_s < 60:
        print("拒绝：--timeout 太小（<60s）；试探需要跑完一轮登录+战斗，建议 ≥600", file=sys.stderr)
        return 2
    a.apply = bool(a.apply and not a.dry_run)
    try:
        rc, summary = run(a)
        if a.json:
            print(json.dumps(scrub(summary), ensure_ascii=False))
        else:
            print("⇒ rc=%d ｜ %s" % (rc, summary.get("conclusion")))
            print("报告：%s" % summary.get("report"))
            print("run 目录：%s" % os.path.join(RUNS_DIR, summary.get("run_id")))
        return rc
    except RuntimeError as e:
        print("拒绝：%s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
