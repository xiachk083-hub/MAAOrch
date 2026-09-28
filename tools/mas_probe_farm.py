#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""mas_probe_farm.py —— 先试探后开刷（AUTO-MAS）：查台账 → 必要时发一次**最小试探** → 确认能刷才正式刷。

定位：解决「不知道这个号能不能刷这个关」。三态决策 + 三条证据判定 + 复用 `mas_run_profile.py`
的发车/读回/还原骨架（不重造）。

三态决策（台账 = `tools/stage_capability.py` 的 `can_farm(account, stage)`）：
  success  ⇒ **直刷**：跳过试探，直接按 `--times` 正式发车（除非 `--force-probe`）
  failed   ⇒ **默认拒绝发车**（rc=2，附失败类型与证据行）；确实已修好才用
             `--retry-after-fix "<修复说明>"` 放行 ⇒ 进试探流程
  unknown  ⇒ 进试探流程（台账查不到/试过没结论，一律不猜）

试探（一次最小任务）：
  * 骨架 = `python tools/mas_run_profile.py --profile <临时 profile> --apply`（八步时序/读回/还原全在那边）：
    `stage_plan=[<关>]`、**恒 `Series=1`**（最小）、保守用药（`--medicine` 默认 0）、
    不启用活动关优先（不写 `Task` 键）、剩余理智**显式声明**（默认不挂 = `Stage_Remain="-"`；
    `--remain-sanity` 才复制当日值）、`restore_after=timeout:<--timeout>s`（默认 600，
    到点走 `dispatch/stop` ⇒ unlock + Temp 还原）。
  * **判定三条同时看**（同一口径 = `stage_capability.classify_attempt`）：
      ① 试探窗口内新增的 `history/<date>/<account>/*.json` 的 `maa_result`
      ② 该尝试的 `drop_statistics` 键 / 副本日志 `<关> 掉落统计` 块
      ③ `<MAA目录>/debug/asst.log` 发车后锚点（`append_task Fight` 的 stage/series/medicine、
         `Fight@StartButton`/`Fight@PRTS`/`StageDropsTaskPlugin`/`analyze_sanity_*`/`FightMissionFailed`）
    ⇒ `success`（该关出现在成功记录里）｜ `failed`（能力类：`stage_not_open`/`stage_unavailable`/`proxy_mismatch`）
      ｜ `inconclusive`（环境/状态类：掉线、**理智不足**、没轮到、被中止 —— **不写进能力台账、不冒充结论**）
  * success ⇒ 跑 `stage_capability.py --update` 把这次尝试并进台账（再读回核对）⇒ **正式刷**
    （`--times` ⇒ `SeriesNumb`）；`--probe-only` 则到此为止。
  * failed / inconclusive ⇒ **立即停止**；调 `mas_error_triage.py --account <号>` 归类并输出证据；**不重试**。

还原：每次发车自带 `try/finally` 还原（mas_run_profile 的 ①-⑧）；本工具收尾再复核
（`--status` + 计划表列表，有 open/残留 ⇒ rc=4 显式告警），并把证据落
`runtime/probe_farm/<run_id>/`，全局台账落 `runtime/mas_ledger.jsonl`（kind=probe_farm）。

用法（**默认 dry-run；真跑要 --apply**）：
    python tools/mas_probe_farm.py --account <号> --stage <关> --times <N>            # dry-run
    python tools/mas_probe_farm.py --account <号> --stage <关> --times <N> --apply
    python tools/mas_probe_farm.py --account <号> --stage <关> --apply --probe-only   # 只试探不开刷
    python tools/mas_probe_farm.py --list unknown                                     # 只读：台账未知池
    python tools/mas_probe_farm.py --status                                           # 只读：历史记录
    python tools/mas_probe_farm.py --selftest                                         # 离线自测（零触网）
`--times N` = 正式刷阶段的**连战倍数**（`SeriesNumb`，N∈0..6，0=自动，-1=不调整）——**不是总次数上限**
（AUTO-MAS 两层没有该字段，docs/MAS_RUN_PROFILE.md §2 行 4）；试探阶段恒为 `Series=1`。

退出码：0 期望路径完成；2 拒绝/发车前失败（零残留）；3 试探未证明可刷（failed/inconclusive，已还原）；
        4 有残留（还原失败，需人工）；5 试探成功但正式刷阶段失败。
环境变量：MAAORCH_MAS_API / MAAORCH_SSH_KEY / MAAORCH_SSH_HOST（同其他 mas_* 工具）；
         MAAORCH_PROBE_FARM_NO_GO=1 一票否决；MAAORCH_PROBE_FARM_RUNTIME 改 runtime 落点（自测用）。
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

if hasattr(sys.stdout, "reconfigure"):                      # Windows 控制台中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
RUNTIME = os.environ.get("MAAORCH_PROBE_FARM_RUNTIME") or os.path.join(ROOT, "runtime")
LOG = os.path.join(RUNTIME, "mas_probe_farm_log.jsonl")
LOCK = os.path.join(RUNTIME, "mas_probe_farm.lock")
RUNS_DIR = os.path.join(RUNTIME, "probe_farm")
CAP_JSON = os.path.join(RUNTIME, "reports", "stage_capability.json")
RUN_PROFILE = os.path.join(TOOLS, "mas_run_profile.py")
CAPABILITY = os.path.join(TOOLS, "stage_capability.py")
TRIAGE = os.path.join(TOOLS, "mas_error_triage.py")
HIST_ROOT = r"E:\AUTO-MAS\history"
PY312 = os.environ.get("MAAORCH_PROBE_FARM_PYTHON",
                       r"C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe")

TS_FMT = "%Y-%m-%d %H:%M:%S"
LOCK_STALE_S = 6 * 3600
PROBE_SERIES = "1"                     # 试探恒最小
TIMES_OK = ("0", "6", "5", "4", "3", "2", "1", "-1")   # = mas_run_profile.SERIES_OK
ANNI_NAMES = {"annihilation", "剿灭", "剿灭作战"}
NO_GO_ENVS = ("MAAORCH_PROBE_FARM_NO_GO", "MAAORCH_DISPATCH_NO_GO")
ST_SUCCESS, ST_FAILED, ST_UNKNOWN = "success", "failed", "unknown"

_LOGPFX = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\]\[\w+\]\[[^\]]*\]\s*")
_MODPFX = re.compile(r"^<\d+>\s*")
_TSLINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d{3}\]")


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


def _now() -> str:
    return dt.datetime.now().strftime(TS_FMT)


def log(msg: str) -> None:
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def scrub(obj):
    return _rp().scrub(obj)


def ledger_append(rec: dict) -> None:
    """全局台账 runtime/mas_ledger.jsonl（复用 run_profile 的锁+脱敏）。"""
    _rp().ledger_append(rec)


# ---------------------------------------------------------------- 统一审计流（双写期）
# 接线卡 t_a190ca4b：旧流照写（不动 audit() 的写出行为），旁路再补一条 tools/mas_audit.py
# 统一事件（schema maaorch.audit.v1）；审计失败绝不打断主流程。
# 台账（runtime/mas_ledger.jsonl）经 mas_run_profile.ledger_append 写入——那条共享写者按
# kind（probe_farm / probe_farm_refused）分派 actor/action，故本文件不再另加，避免重复计数。
def _unified_guard(fn, rec: dict) -> None:
    """统一审计写入的兜底：任何失败都不打断主流程（同 tools/stage_capability.py::_audit_write）。"""
    try:
        fn(rec)
    except Exception:  # noqa: BLE001
        pass


def _unified_from_audit(rec: dict) -> None:
    """事件行（mas_probe_farm_log）→ 统一流：farm.<event>（映射同 mas_audit._ad_probe）。"""
    ev = str(rec.get("event") or "").strip()
    if not ev:
        return
    amap = {"done": "farm.done", "ledger_update": "stage.ledger_update", "refuse": "farm.refuse"}
    action = amap.get(ev, "farm." + re.sub(r"[^a-z0-9_]+", "_", ev.lower()))
    rc = rec.get("rc")
    res = "ok" if rc == 0 else ("info" if rc is None else "fail")
    if "refus" in ev and rc is None:
        res = "refused"
    run_id = str(rec.get("run_id") or "")
    account = str(rec.get("account") or "")
    params = {k: rec.get(k) for k in ("stage", "before", "after", "rc", "verdict", "farm_rc",
                                      "probe_rc", "reason", "detail", "decision", "env", "kind",
                                      "ledger", "restore_check", "warnings", "probe", "summary")
              if rec.get(k) not in (None, "")}
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import mas_audit  # noqa: PLC0415
    mas_audit.safe_append(
        actor="tool:mas_probe_farm", action=action,
        target=("account:" + account) if account else (("run:" + run_id) if run_id else "-"),
        result=res, reason=str(rec.get("verdict") or rec.get("reason") or ""), params=params,
        evidence=[LOG], card=os.environ.get("HERMES_KANBAN_TASK", ""), run_id=run_id,
        path=mas_audit.stream_for_runtime(RUNTIME))


def audit(rec: dict) -> None:
    os.makedirs(RUNTIME, exist_ok=True)
    rec = dict(rec)
    rec.setdefault("ts", _now())
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(scrub(rec), ensure_ascii=False) + "\n")
    _unified_guard(_unified_from_audit, rec)


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
    out = re.sub(r"[^0-9A-Za-z_\-]+", "", str(s or ""))
    return out[:n] or "run"


def pick_py() -> str:
    for c in (os.environ.get("MAAORCH_PROBE_FARM_PYTHON"), PY312, sys.executable):
        if c and os.path.exists(c):
            return c
    return sys.executable


def no_go() -> str:
    for k in NO_GO_ENVS:
        if str(os.environ.get(k, "")).strip().lower() in {"1", "true", "yes", "on"}:
            return k
    return ""


class LockBusy(RuntimeError):
    pass


@contextlib.contextmanager
def hold_lock(enabled: bool = True):
    """单飞锁：同一时刻只允许一个 probe_farm 任务（长跑心跳续期）。"""
    if not enabled:
        yield lambda: None
        return
    os.makedirs(RUNTIME, exist_ok=True)
    fd = None
    for attempt in (1, 2):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, ("pid=%d ts=%s\n" % (os.getpid(), _now())).encode("utf-8"))
            break
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(LOCK)
            except OSError:
                age = 0.0
            if attempt == 1 and age > LOCK_STALE_S:
                log("发现陈旧锁（%.0f 分钟前）——抢占" % (age / 60))
                with contextlib.suppress(OSError):
                    os.remove(LOCK)
                continue
            raise LockBusy("锁被占（%.0f 秒前创建，%s）——同一时刻只允许一个 probe_farm 任务" % (age, LOCK))
    def touch():
        with contextlib.suppress(OSError):
            os.utime(LOCK, None)
    try:
        yield touch
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                os.remove(LOCK)


# ---------------------------------------------------------------- 台账（只读）
def ledger_lookup(account: str, stage: str, json_path=None) -> dict:
    """查能力台账 -> {status, row, tried, in_pool, report_built_at, missing_report}。"""
    p = json_path or CAP_JSON
    scap = _scap()
    out = {"status": ST_UNKNOWN, "row": None, "tried": None, "in_pool": None,
           "report_built_at": "", "missing_report": False, "json_path": p}
    if not os.path.exists(p):
        out["missing_report"] = True
        return out
    row = scap.can_farm_detail(account, stage, p)
    out["row"] = row
    if row:
        out["status"] = row["status"]
    rep = load_json(p, None) or {}
    out["report_built_at"] = rep.get("built_at") or ""
    out["in_pool"] = stage in (rep.get("stages") or [])
    for x in (rep.get("unknown") or {}).get(account, []) or []:
        if x.get("stage") == stage:
            out["tried"] = bool(x.get("tried"))
    return out


def decide(ledger: dict, force_probe: bool = False, fix_note: str = "") -> dict:
    """三态决策 -> {action: farm|probe|refuse, why}。"""
    st = ledger.get("status")
    if st == ST_SUCCESS:
        if force_probe:
            return {"action": "probe", "why": "台账=success，但 --force-probe 要求先试探一次"}
        return {"action": "farm", "why": "台账=success ⇒ 直接正式刷（跳过试探）"}
    if st == ST_FAILED:
        if fix_note:
            return {"action": "probe", "why": "台账=failed，但已给修复说明 ⇒ 先试探验证（修复：%s）" % fix_note}
        return {"action": "refuse",
                "why": "台账=failed ⇒ 默认拒绝发车；确认已修复后加 --retry-after-fix \"<修复说明>\" 放行"}
    return {"action": "probe", "why": "台账=unknown ⇒ 先试探后开刷（不猜）"}


def ledger_refusal_text(ledger: dict) -> str:
    row = ledger.get("row") or {}
    lines = ["台账判定：%s（built_at=%s）" % (ledger.get("status"), ledger.get("report_built_at") or "?")]
    if row:
        lines.append("  最近失败 %s ｜ 失败类型 %s ｜ 能力类失败 ×%s"
                     % (row.get("last_failure") or "?", row.get("failure_types") or {},
                        row.get("fail_n")))
        for f in (row.get("failures") or []):
            if f.get("capability_impact"):
                lines.append("  证据[%s] %s：%s" % (f.get("type"), f.get("day"), (f.get("line") or "")[:150]))
    else:
        lines.append("  （台账里没有 %s × %s 这条：既没刷成过也没有失败证据）"
                     % (ledger.get("account") or "?", ledger.get("stage") or "?"))
    lines.append("  后续：确认已修（关卡开放/代理已设/干员到位…）后加 --retry-after-fix \"<一句话说明>\" 再跑")
    return "\n".join(lines)


# ---------------------------------------------------------------- profile 构建
def build_profile(account: str, script: str, stage: str, series, medicine: int,
                  keep_remain: bool, timeout_s=None, notes: str = "") -> dict:
    """构造 mas_run_profile 的 profile dict。timeout_s 有值 => restore_after=timeout:Ns（试探）；否则 run_end（正式刷）。"""
    return {
        "target": {"script": script, "account": account},
        "stage_plan": [stage],
        "series": str(series),
        "medicine": {"use": int(medicine) > 0, "count": int(medicine)},
        "activity_priority": {"enabled": False, "medicine": 0},
        "keep_remain_sanity_task": bool(keep_remain),
        "restore_after": ("timeout:%ds" % int(timeout_s)) if timeout_s else "run_end",
        "notes": notes,
    }


# ---------------------------------------------------------------- 主机侧（只读证据）
def _ps_quote(s) -> str:
    return str(s).replace("'", "''")


def host_attempts(day: str, account: str, timeout: int = 180) -> dict:
    """只读：当天该账号 history 的尝试清单（json 元信息）+ 主机钟。-> {ok, attempts, host_now}"""
    base = "%s\\%s\\%s" % (HIST_ROOT, day, account)
    ps = r"""
$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'
$OutputEncoding=[System.Text.Encoding]::UTF8; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$base = '@@BASE@@'
Write-Output ('NOW|' + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))
if (-not (Test-Path -LiteralPath $base)) { Write-Output 'NOACCT'; Write-Output ('__JSON__' + '[]'); exit 0 }
$js = @(Get-ChildItem -LiteralPath $base -Filter *.json -File | Sort-Object LastWriteTime -Descending)
$out = @()
foreach ($j in $js) {
  $rec = [ordered]@{ file=[string]$j.Name; mt=$j.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'); size=[int]$j.Length; result=''; drops=@(); bad=$false }
  try {
    $o = Get-Content -Raw -Encoding UTF8 $j.FullName | ConvertFrom-Json -ErrorAction Stop
    $rec.result = [string]$o.maa_result
    if ($null -ne $o.drop_statistics) { $rec.drops = @($o.drop_statistics.PSObject.Properties | ForEach-Object { [string]$_.Name }) }
  } catch { $rec.bad = $true }
  $out += $rec
}
Write-Output ('__JSON__' + (ConvertTo-Json -Compress -Depth 6 @($out)))
""".replace("@@BASE@@", _ps_quote(base))
    try:
        out, err = _scap().ssh_ps(ps, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "attempts": [], "host_now": "", "noacct": False,
                "error": "ssh: %s: %s" % (type(e).__name__, e)}
    now, attempts, noacct = "", [], False
    for ln in out.splitlines():
        ln = ln.rstrip("\r")
        if ln.startswith("NOW|"):
            now = ln[4:].strip()
        elif ln.strip() == "NOACCT":
            noacct = True
        elif ln.strip().startswith("__JSON__"):
            try:
                doc = json.loads(ln.strip()[len("__JSON__"):])
            except ValueError:
                doc = None
            if doc is None:
                return {"ok": False, "attempts": [], "host_now": now, "noacct": noacct,
                        "error": "JSON 解析失败：" + ln[-200:]}
            attempts = doc if isinstance(doc, list) else [doc]
    if not now and not attempts and not noacct:
        return {"ok": False, "attempts": [], "host_now": "", "noacct": False,
                "error": "SSH 无有效输出：" + (out or "(空)")[-200:] + " | " + (err or "")[-150:]}
    return {"ok": True, "attempts": attempts, "host_now": now, "noacct": noacct}


def host_attempt_lines(day: str, account: str, logname: str, limit: int = 120, timeout: int = 180) -> list:
    """只读：某次尝试的副本日志（gui.log 切片）判据行（与 stage_capability 同一 PATTERN，前缀本地剥）。"""
    scap = _scap()
    lf = "%s\\%s\\%s\\%s" % (HIST_ROOT, day, account, logname)
    ps = r"""
$ErrorActionPreference='Continue'
$OutputEncoding=[System.Text.Encoding]::UTF8; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$lf = '@@LF@@'
if (-not (Test-Path -LiteralPath $lf)) { Write-Output 'NOLOG'; exit 0 }
$ms = @(Select-String -LiteralPath $lf -Pattern '@@PAT@@' -Encoding UTF8 -ErrorAction SilentlyContinue)
$o = @()
foreach ($m in ($ms | Select-Object -First @@LIM@@)) { $o += [string]$m.Line }
Write-Output ('__JSON__' + (ConvertTo-Json -Compress -Depth 4 @($o)))
""".replace("@@LF@@", _ps_quote(lf)).replace("@@PAT@@", scap.PS_PATTERN).replace("@@LIM@@", str(int(limit)))
    try:
        out, err = _scap().ssh_ps(ps, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        log("读副本日志失败（%s）：%s" % (logname, e))
        return []
    for ln in out.splitlines():
        s = ln.strip()
        if s.startswith("__JSON__"):
            try:
                doc = json.loads(s[len("__JSON__"):])
            except ValueError:
                return []
            raw = doc if isinstance(doc, list) else [doc]
            return [_clean_line(x) for x in raw]
    return []


def _clean_line(raw: str) -> str:
    s = _MODPFX.sub("", _LOGPFX.sub("", str(raw or ""))).strip()
    return s[:400]


def host_asst_anchors(maa_dir: str, tail: int = 6000, timeout: int = 240) -> dict:
    """只读：<MAA目录>/debug/asst.log 尾部锚点行（战斗/结算相关）。"""
    p = "%s\\debug\\asst.log" % str(maa_dir).rstrip("\\/")
    ps = r"""
$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'
$OutputEncoding=[System.Text.Encoding]::UTF8; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$p = '@@P@@'
if (-not (Test-Path -LiteralPath $p)) { Write-Output 'MISSING'; exit 0 }
$lines = @(Get-Content -LiteralPath $p -Tail @@TAIL@@ -Encoding UTF8 -ErrorAction SilentlyContinue |
  Where-Object { $_ -match 'append_task Fight|Fight@|analyze_sanity|sanity_cost|StageDropsTaskPlugin|FightMissionFailed|CloseStonePage' })
$o = @($lines | Select-Object -Last 160 | ForEach-Object { [string]$_ })
Write-Output ('__JSON__' + (ConvertTo-Json -Compress -Depth 4 @($o)))
""".replace("@@P@@", _ps_quote(p)).replace("@@TAIL@@", str(int(tail)))
    try:
        out, err = _scap().ssh_ps(ps, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "lines": [], "error": "ssh: %s: %s" % (type(e).__name__, e)}
    for ln in out.splitlines():
        s = ln.strip()
        if s == "MISSING":
            return {"ok": False, "missing": True, "lines": [], "error": "asst.log 不存在"}
        if s.startswith("__JSON__"):
            try:
                doc = json.loads(s[len("__JSON__"):])
            except ValueError:
                return {"ok": False, "lines": [], "error": "JSON 解析失败：" + s[-160:]}
            rows = doc if isinstance(doc, list) else [doc]
            return {"ok": True, "lines": [str(x) for x in rows], "error": ""}
    return {"ok": False, "lines": [], "error": "SSH 无有效输出：" + (out or "(空)")[-160:]}


def asst_summary(lines: list, since: dt.datetime | None = None) -> dict:
    """把 asst.log 锚点行归总：核心实收参数 + 战斗/结算/异常计数 + 理智线索（可按时窗过滤）。"""
    out = {"lines_n": 0, "window_lines": 0, "params": None, "params_ts": "", "counts": {},
           "sanity_remain": None, "sanity_cost": None, "series_core": None, "notes": []}
    counts = out["counts"]
    for raw in lines or []:
        ts = ""
        m = _TSLINE.match(raw)
        if m:
            ts = m.group(1)
        if since and ts:
            try:
                if dt.datetime.strptime(ts, TS_FMT) < since:
                    continue
            except ValueError:
                pass
        out["window_lines"] += 1
        out["lines_n"] += 1
        for tok in ("Fight@StartButton1", "Fight@StartButton2", "Fight@PRTS1", "Fight@EndOfAction",
                    "StageDropsTaskPlugin", "FightMissionFailed", "Fight@CloseStonePageExceeded",
                    "Fight@UseMedicine", "Fight@UseStone", "Fight@OfflineConfirm", "Fight@PrtsErrorConfirm"):
            if tok in raw:
                counts[tok] = counts.get(tok, 0) + 1
        m = re.search(r"Current Sanity:\s*(\d+)", raw)
        if m:
            out["sanity_remain"] = int(m.group(1))
        m = re.search(r'"sanity_cost":\s*(\d+)', raw) or re.search(r"sanity_cost[:=]\s*(\d+)", raw)
        if m:
            out["sanity_cost"] = int(m.group(1))
        m = re.search(r'"series":\s*(\d+)', raw)
        if m:
            out["series_core"] = int(m.group(1))
        m = re.search(r"append_task Fight (\{.*\})\s*$", raw)
        if m:
            try:
                prm = json.loads(m.group(1))
                out["params"] = {k: prm.get(k) for k in ("stage", "series", "medicine", "times", "client_type")}
                out["params_ts"] = ts
            except ValueError:
                pass
    return out


# ---------------------------------------------------------------- 判定（三条证据）
def judge_probe(stage: str, new_attempts: list, asst: dict, scan_ok: bool = True, scan_error: str = "") -> dict:
    """三证据判定 -> {status: success|failed|inconclusive, kind, reason, evidence}。

    * success  该关出现在成功记录里（drop_statistics 键 / 副本日志 `<关> 掉落统计` 块 / 剿灭链完成）
    * failed   能力类失败（stage_not_open / stage_unavailable / proxy_mismatch）
    * inconclusive  环境/状态类（掉线、理智不足、没轮到、被中止…）——不冒充结论
    """
    scap = _scap()
    ev = {"attempts": [], "stage": stage, "scan_ok": scan_ok, "scan_error": scan_error}
    if not scan_ok:
        return {"status": "inconclusive", "kind": "evidence_scan_failed",
                "reason": "读主机 history 失败（没法判定）：%s" % (scan_error or "?"), "evidence": ev}
    kinds_failed, net_seen, no_evidence = [], False, False
    for att in new_attempts:
        cls = scap.classify_attempt(att.get("lines") or [], att.get("drops") or [], att.get("result", ""))
        rec = {"file": att.get("file"), "mt": att.get("mt"), "maa_result": att.get("result"),
               "drops": [s for s in (att.get("drops") or []) if scap.is_stage_code(s)],
               "drop_log": cls.get("drop_log"), "selected": cls.get("selected"),
               "planned": cls.get("planned"), "not_open": cls.get("not_open"), "actions": cls.get("actions"),
               "round_ok": cls.get("round_ok"), "round_partial": cls.get("round_partial"),
               "annih_done": cls.get("annih_done"), "net_marks": cls.get("net_marks"),
               "fights": cls.get("fights"), "skipped": cls.get("skipped"), "bad": bool(att.get("bad"))}
        ev["attempts"].append(rec)
        if stage in rec["drops"]:
            return {"status": ST_SUCCESS, "kind": "stage_farmed",
                    "reason": "drop_statistics 出现该关（主证据）", "evidence": ev}
        if stage in (rec["drop_log"] or []):
            return {"status": ST_SUCCESS, "kind": "fight_log_drop",
                    "reason": "副本日志出现 `<关> 掉落统计` 块", "evidence": ev}
        if stage == scap.ANNIH_STAGE and rec.get("annih_done"):
            return {"status": ST_SUCCESS, "kind": "annihilation_chain_done",
                    "reason": "剿灭链 `完成任务: 剿灭作战`（剿灭不掉落）", "evidence": ev}
        vs = (rec.get("selected") or []) + (rec.get("not_open") or []) + (rec.get("planned") or [])
        stage_touched = (stage in vs or stage in rec["drops"] or stage in rec["drop_log"])
        only_us = bool(vs) and not (set(vs) - {stage, scap.ANNIH_STAGE, ""})
        if stage in (rec["not_open"] or []):
            kinds_failed.append((scap.F_NOTOPEN, rec))
        for fr in rec.get("fights") or []:
            if fr.get("stage") == stage and fr.get("kind") in scap.CAP_FAIL:
                kinds_failed.append((fr.get("kind"), rec))
        if stage_touched and only_us:
            evmap = cls.get("evidence") or {}
            for k in (scap.F_NOTOPEN, scap.F_UNAVAIL, scap.F_PROXY):
                if evmap.get(k):
                    kinds_failed.append((k, rec))
        if rec.get("net_marks"):
            net_seen = True
        if not (rec["selected"] or rec["not_open"] or rec["drops"] or rec["drop_log"]
                or rec["actions"] or rec["fights"]):
            no_evidence = True
    ev["asst"] = asst
    if kinds_failed:
        kind, rec = kinds_failed[-1]
        return {"status": ST_FAILED, "kind": kind,
                "reason": "能力类失败证据：%s（%s）" % (kind, scap.FAIL_ZH.get(kind, kind)), "evidence": ev}
    if net_seen:
        return {"status": "inconclusive", "kind": scap.F_NET,
                "reason": "只见掉线/重连类环境失败（不判能力）", "evidence": ev}
    sanity_hint = ""
    if isinstance(asst, dict):
        sr, scc = asst.get("sanity_remain"), asst.get("sanity_cost")
        if sr is not None and scc is not None and sr < scc:
            sanity_hint = "理智不足（remain=%s < cost=%s）" % (sr, scc)
        if (asst.get("counts") or {}).get("Fight@CloseStonePageExceeded"):
            sanity_hint = sanity_hint or "补理智页连续被关（CloseStonePageExceeded）⇒ 理智不足/源石策略"
    if not new_attempts:
        return {"status": "inconclusive", "kind": "no_attempt",
                "reason": "试探窗口内没有新增 history 尝试（可能没跑到/被拒/未写入）", "evidence": ev}
    if sanity_hint and not any((r.get("actions") or 0) > 0 for r in ev["attempts"]):
        return {"status": "inconclusive", "kind": "insufficient_sanity", "reason": sanity_hint, "evidence": ev}
    if any(stage in (r.get("selected") or []) for r in ev["attempts"]):
        return {"status": "inconclusive", "kind": "selected_no_result",
                "reason": "该关被选中但没拿到结论（没开打/被中止/超出窗口）%s" % ("；" + sanity_hint if sanity_hint else ""),
                "evidence": ev}
    if no_evidence:
        return {"status": "inconclusive", "kind": "no_evidence",
                "reason": "新增尝试里没有该关的任何判据行（窗口太短/没轮到）%s" % ("；" + sanity_hint if sanity_hint else ""),
                "evidence": ev}
    return {"status": "inconclusive", "kind": "no_conclusion",
            "reason": "证据不足，无法判定 %s × %s" % (ev.get("stage"), sanity_hint or "（无理智线索）"), "evidence": ev}


# ---------------------------------------------------------------- 子进程调用
def run_rp(profile_path: str, apply: bool, extra: list, timeout: float) -> dict:
    """调 mas_run_profile.py（黑盒：八步时序/读回/还原都在它那边）。"""
    cmd = [pick_py(), RUN_PROFILE, "--profile", profile_path] + list(extra) + (["--apply"] if apply else [])
    t0 = time.time()
    rec = {"cmd": " ".join(cmd[1:]), "rc": None, "timeout": False, "out": "", "err": "", "summary": None,
           "seconds": 0}
    try:
        p = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout, cwd=ROOT)
        rec["rc"] = p.returncode
        rec["out"] = (p.stdout or b"").decode("utf-8", "replace")
        rec["err"] = (p.stderr or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        rec["timeout"] = True
        rec["out"] = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        rec["err"] = (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
    rec["seconds"] = int(time.time() - t0)
    for ln in reversed((rec["out"] or "").splitlines()):
        ln = ln.strip()
        if ln.startswith('{"tool"'):
            with contextlib.suppress(ValueError):
                rec["summary"] = json.loads(ln)
                break
    return rec


def run_capability_update(days: int = 1, timeout: int = 900) -> dict:
    """跑 stage_capability.py --update（增量并入台账）。"""
    cmd = [pick_py(), CAPABILITY, "--update", "--update-days", str(int(days))]
    try:
        p = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout, cwd=ROOT)
        return {"rc": p.returncode, "out": (p.stdout or b"").decode("utf-8", "replace")[-2000:],
                "err": (p.stderr or b"").decode("utf-8", "replace")[-800:]}
    except subprocess.TimeoutExpired:
        return {"rc": None, "timeout": True, "out": "", "err": "超时"}
    except Exception as e:  # noqa: BLE001
        return {"rc": None, "out": "", "err": "%s: %s" % (type(e).__name__, e)}


def run_triage(account: str, timeout: int = 400) -> dict:
    """跑 mas_error_triage.py 归类失败（只读）。"""
    cmd = [pick_py(), TRIAGE, "--account", account, "--json"]
    try:
        p = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout, cwd=ROOT)
        blob = (p.stdout or b"").decode("utf-8", "replace")
        data = None
        with contextlib.suppress(ValueError):
            data = json.loads(blob[blob.index("{"):]) if "{" in blob else None
        return {"rc": p.returncode, "data": data, "out": blob[-1200:], "err": (p.stderr or b"").decode("utf-8", "replace")[-400:]}
    except subprocess.TimeoutExpired:
        return {"rc": None, "timeout": True, "data": None, "out": "", "err": "超时"}
    except Exception as e:  # noqa: BLE001
        return {"rc": None, "data": None, "out": "", "err": "%s: %s" % (type(e).__name__, e)}


def verify_restored(started_at: str, run_ids: list | None, timeout: int = 300) -> dict:
    """收尾复核（只读）：本工具发起的 run_profile 运行是否 restored + 临时计划表是否删净。

    只认**自己这次发起**的 run（run_ids 或 ts≥started_at），不误报别的工具留下的东西。
    """
    out = {"runs": {}, "open_runs": [], "temp_plans": [], "ids": []}
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
                    out.setdefault("errors", []).append("fetch_plan: %s" % e)
    except Exception as e:  # noqa: BLE001
        out["error"] = "%s: %s" % (type(e).__name__, e)
    return out


def load_json_blob(blob: str):
    s = blob.strip()
    if not s:
        return None
    i = s.find("{")
    if i < 0:
        return None
    try:
        return json.loads(s[i:])
    except ValueError:
        return None


# ---------------------------------------------------------------- 证据收集
def collect_evidence(account: str, stage: str, script: dict, day: str, pre_files: set,
                     since_local: str, timeout: int = 240) -> dict:
    """试探后：history 新增尝试（含判据行）+ asst.log 锚点。"""
    post = host_attempts(day, account, timeout=timeout)
    ev = {"day": day, "host_now": post.get("host_now"), "attempts_new": [], "attempts_all_n": 0,
          "scan_ok": bool(post.get("ok")), "scan_error": post.get("error", ""), "asst": {}}
    if not post.get("ok"):
        return ev
    all_att = post.get("attempts") or []
    ev["attempts_all_n"] = len(all_att)
    new = [a for a in all_att if a.get("file") not in pre_files]
    clock_off = 0
    try:
        if post.get("host_now") and since_local:
            clock_off = int((dt.datetime.strptime(post["host_now"], TS_FMT)
                             - dt.datetime.strptime(since_local, TS_FMT)).total_seconds())
    except ValueError:
        clock_off = 0
    ev["clock_offset_s"] = clock_off
    since_host = None
    with contextlib.suppress(ValueError):
        since_host = dt.datetime.strptime(since_local, TS_FMT) + dt.timedelta(seconds=clock_off - 120)
    for a in new[:3]:
        logname = re.sub(r"\.json$", ".log", str(a.get("file") or ""), flags=re.I)
        a = dict(a)
        a["lines"] = host_attempt_lines(day, account, logname, timeout=timeout) if logname else []
        ev["attempts_new"].append(a)
    if script and script.get("dir"):
        an = host_asst_anchors(script["dir"], timeout=timeout)
        ev["asst"] = asst_summary(an.get("lines") or [], since=since_host)
        ev["asst"]["ok"] = bool(an.get("ok"))
        ev["asst"]["error"] = an.get("error", "")
    return ev


# ---------------------------------------------------------------- 计划/渲染
def summarize_rp_plan(rp_out: dict) -> list:
    """把 mas_run_profile 的 --json 计划（或纯文本）折成几行，别把整坨 JSON 打出来。"""
    blob = rp_out.get("out") or rp_out.get("plan_text") or ""
    doc = None
    for ln in reversed(blob.splitlines()):
        ln = ln.strip()
        if ln.startswith('{"tool"'):
            with contextlib.suppress(ValueError):
                doc = json.loads(ln)
            break
    L = []
    if doc is None:
        txt = (rp_out.get("plan_text") or blob or "").strip()
        L.extend(("  " + x[:200]) for x in txt.splitlines()[:30])
        return L
    sc = doc.get("script") or {}
    if sc:
        L.append("  脚本: %s（目录 %s, emu=%s）｜REST %s"
                 % (sc.get("name"), sc.get("dir"), sc.get("emu"),
                    (doc.get("reachable") or {}).get("detail") or "?"))
    for a in doc.get("accounts") or []:
        exp = (doc.get("expected") or {}).get(a.get("uid")) or {}
        base = exp.get("base_before") or {}
        L.append("  账号: %s（%s/%s/%s，StageMode=%s）%s"
                 % (a.get("name"), a.get("server"), a.get("mode"), a.get("stage_mode"),
                    str(a.get("stage_mode"))[:30], "（临时停用）" if a.get("will_disable") else ""))
        if exp.get("payload"):
            L.append("    将写 7 键: %s" % json.dumps(exp["payload"], ensure_ascii=False))
            L.append("    当日生效值: %s" % json.dumps(base, ensure_ascii=False))
            L.append("    读回期望: StagePlan=%s Series=%s MedicineCount=%s UseMedicine=%s 剩余理智=%s"
                     % (exp.get("stage_plan"), exp.get("series"), exp.get("medicine"),
                        exp.get("use_medicine"), exp.get("remain")))
    steps = doc.get("steps") or []
    if steps:
        L.append("  步骤: %s" % " → ".join(str(s.get("op")) for s in steps))
    for w in doc.get("warnings") or []:
        L.append("  WARN: %s" % w)
    for r in doc.get("refusals") or []:
        L.append("  拒绝[%s]: %s — %s" % (r.get("gate"), r.get("reason"), r.get("detail")))
    return L


def render_plan_text(plan: dict) -> str:
    L = []
    L.append("=== mas_probe_farm · %s ===" % ("APPLY" if plan.get("apply") else "DRY-RUN"))
    tgt = plan.get("target") or {}
    L.append("目标: 账号 %s / 关卡 %s%s" % (tgt.get("account"), tgt.get("stage"),
                                            ("　　脚本 " + str(tgt.get("script"))) if tgt.get("script") else ""))
    lg = plan.get("ledger") or {}
    L.append("台账: status=%s（report built_at=%s%s）"
             % (lg.get("status"), lg.get("report_built_at") or "?",
                "；该号×关不在 30 天记录里" if not lg.get("row") else
                ("；失败类型 %s ｜ 最近失败 %s" % (lg.get("row", {}).get("failure_types"),
                                                  lg.get("row", {}).get("last_failure")))))
    dec = plan.get("decision") or {}
    L.append("决策: %s —— %s" % (dec.get("action"), dec.get("why")))
    if plan.get("script"):
        sc = plan["script"]
        L.append("脚本落点: %s（sid=%s, emu=%s, 目录=%s）"
                 % (sc.get("name"), str(sc.get("sid"))[:8], sc.get("emu"), sc.get("dir")))
    prof = plan.get("probe_profile")
    if prof:
        L.append("试探 profile: stage_plan=%s series=%s medicine=%s 剩余理智=%s restore_after=%s"
                 % (prof["stage_plan"], prof["series"], prof["medicine"],
                    "保留(复制当日值)" if prof["keep_remain_sanity_task"] else "不挂(=stage_remain '-')",
                    prof["restore_after"]))
    if plan.get("farm_profile") is not None:
        L.append("正式刷: series=%s（连战倍数，不是总次数上限；--times 直译 SeriesNumb）"
                 % plan["farm_profile"]["series"])
    for w in plan.get("warnings") or []:
        L.append("WARN: %s" % w)
    for rp_out in plan.get("rp_runs") or []:
        L.append("--- mas_run_profile 计划（%s, rc=%s）---" % (rp_out.get("phase"), rp_out.get("rc")))
        L.extend(summarize_rp_plan(rp_out))
    return "\n".join(L)


# ---------------------------------------------------------------- 主流程
def run(args) -> tuple:
    """返回 (rc, summary)。args 已由 main 校验。"""
    run_id = "pf_%s_%s" % (dt.datetime.now().strftime("%Y%m%d_%H%M%S"), slug(args.account))
    run_dir = os.path.join(RUNS_DIR, run_id)
    summary = {"tool": "mas_probe_farm", "run_id": run_id, "account": args.account, "stage": args.stage,
               "apply": bool(args.apply), "probe_only": bool(args.probe_only), "phases": [],
               "ledger": {}, "decision": {}, "script": None, "probe": None, "ledger_update": None,
               "farm": None, "restore_check": None, "warnings": [], "rc": None}
    py = pick_py()
    os.makedirs(run_dir, exist_ok=True)
    t_start = _now()
    rp_run_ids = []                                   # 本工具发起的 run_profile run_id（收尾复核用）

    # 参数派生
    stage = args.stage
    timeout_s = int(float(args.timeout))
    # ① 台账（只读）
    ledger = ledger_lookup(args.account, stage, json_path=args.capability_json)
    ledger["account"], ledger["stage"] = args.account, stage
    summary["ledger"] = {"status": ledger["status"], "report_built_at": ledger.get("report_built_at"),
                         "row": scrub(ledger.get("row")), "tried": ledger.get("tried"),
                         "in_pool": ledger.get("in_pool"), "missing_report": ledger.get("missing_report")}
    dec = decide(ledger, force_probe=args.force_probe, fix_note=args.retry_after_fix or "")
    summary["decision"] = dec
    log("台账：%s × %s = %s ⇒ 决策 %s" % (args.account, stage, ledger["status"], dec["action"]))

    plan = {"tool": "mas_probe_farm", "apply": bool(args.apply),
            "target": {"account": args.account, "stage": stage, "script": args.script or ""},
            "ledger": ledger, "decision": dec, "warnings": [], "rp_runs": []}

    if dec["action"] == "refuse":
        # 台账=failed：默认拒绝（dry-run 也拒；--apply 同样拒）
        txt = ledger_refusal_text(ledger)
        plan["refusal_text"] = txt
        if args.apply:
            audit({"run_id": run_id, "event": "refused", "reason": "ledger_failed", "stage": stage})
            ledger_append({"kind": "probe_farm_refused", "ok": False, "run_id": run_id,
                           "scope": "%s × %s" % (args.account, stage), "detail": "台账=failed 拒绝发车"})
        print(render_plan_text(plan))
        print(txt)
        summary["rc"] = 2
        return 2, summary

    # ② 解析目标脚本（REST 只读；--script 缺省时按账号查）
    script = None
    try:
        rp = _rp()
        scripts = rp.fetch_scripts()
        if args.script:
            token = str(args.script).strip()
            for sid, sc in scripts.items():
                if sc["name"] == token or sid == token:
                    script = sc
                    break
            if script is None:
                raise RefuseError("脚本 %r 不在 AUTO-MAS 里（名字或 uid 要对得上）" % token)
            if not _script_has_account(script, args.account):
                raise RefuseError("脚本 %s 里没有账号 %r" % (script["name"], args.account))
        else:
            hits = [sc for sc in scripts.values() if _script_has_account(sc, args.account)]
            if len(hits) != 1:
                raise RefuseError("账号 %r 在 %d 个脚本里命中（%s）——用 --script 显式指定"
                                  % (args.account, len(hits), "、".join(s["name"] for s in hits)))
            script = hits[0]
    except RefuseError as e:
        plan["refusals"] = [str(e)]
        if args.apply:
            audit({"run_id": run_id, "event": "refused", "reason": "target_unresolved", "detail": str(e)})
            ledger_append({"kind": "probe_farm_refused", "ok": False, "run_id": run_id,
                           "scope": "%s × %s" % (args.account, stage), "detail": str(e)[:300]})
        print(render_plan_text(plan))
        summary["rc"] = 2
        return 2, summary
    except Exception as e:  # noqa: BLE001   （REST 不可达等）
        plan["refusals"] = ["无法解析脚本（REST）：%s: %s" % (type(e).__name__, e)]
        if args.apply:
            audit({"run_id": run_id, "event": "refused", "reason": "rest_unreachable", "detail": str(e)[:300]})
        print(render_plan_text(plan))
        summary["rc"] = 2
        return 2, summary
    summary["script"] = {"name": script["name"], "sid": script["sid"], "dir": script["dir"], "emu": script["emu"]}

    # ③ 构建 profile
    probe_prof = build_profile(args.account, script["name"], stage, PROBE_SERIES, args.medicine,
                               args.remain_sanity, timeout_s=timeout_s,
                               notes="probe_farm 试探（最小任务；Series=1）")
    farm_prof = build_profile(args.account, script["name"], stage, str(args.times), args.medicine,
                              args.remain_sanity, timeout_s=None,
                              notes="probe_farm 正式刷（SeriesNumb=%s；连战倍数非总次数）" % args.times)
    plan["probe_profile"] = probe_prof if dec["action"] == "probe" else None
    plan["farm_profile"] = farm_prof
    probe_path = os.path.join(run_dir, "profile.probe.json")
    farm_path = os.path.join(run_dir, "profile.farm.json")
    save_json(probe_path, probe_prof)
    save_json(farm_path, farm_prof)

    rp_extra = ["--write-mode", args.write_mode]
    if args.disable_others:
        rp_extra.append("--disable-others")
    if args.all_accounts:
        rp_extra.append("--all-accounts")

    # dry-run：把 mas_run_profile 的 dry-run 计划也拉出来（只读）
    if not args.apply:
        if dec["action"] == "probe":
            dry = run_rp(probe_path, apply=False, extra=rp_extra + ["--json"], timeout=900)
            with contextlib.suppress(Exception):
                dry["plan_text"] = (dry.get("out") or "")
            plan["rp_runs"].append({"phase": "probe-dry-run", "rc": dry.get("rc"), "plan_text": dry.get("out")})
        dryf = run_rp(farm_path, apply=False, extra=rp_extra + ["--json"], timeout=900)
        plan["rp_runs"].append({"phase": "farm-dry-run", "rc": dryf.get("rc"), "plan_text": dryf.get("out")})
        save_text(os.path.join(run_dir, "plan.txt"), render_plan_text(plan))
        print(render_plan_text(plan))
        summary["rc"] = 0
        return 0, summary

    # ---- apply ----
    go = no_go()
    if go:
        audit({"run_id": run_id, "event": "refused", "reason": "no_go", "env": go})
        print("拒绝：%s 置位（一票否决）" % go, file=sys.stderr)
        summary["rc"] = 2
        return 2, summary

    rc = 0
    try:
        with hold_lock(True) as touch:
            touch()
            # 预快照：当天该账号已有尝试（用于判定「新增」）
            day = dt.datetime.now().strftime("%Y-%m-%d")
            pre = host_attempts(day, args.account)
            pre_files = {a.get("file") for a in (pre.get("attempts") or [])}
            save_json(os.path.join(run_dir, "host_pre.json"), pre)
            since_local = _now()
            summary["phases"].append({"phase": "pre_snapshot", "ok": bool(pre.get("ok")),
                                      "attempts_today": len(pre_files), "host_now": pre.get("host_now")})

            probe_rc = None
            verdict = None
            if dec["action"] == "probe":
                # ④ 试探（一次最小任务）
                audit({"run_id": run_id, "event": "probe_dispatch", "account": args.account, "stage": stage,
                       "profile": scrub(probe_prof)})
                rp_timeout = max(1800.0, 300 + timeout_s + 1500)
                r = run_rp(probe_path, apply=True, extra=rp_extra + ["--json"], timeout=rp_timeout)
                probe_rc = r.get("rc")
                summary["probe"] = {"rc": r.get("rc"), "timeout": r.get("timeout"), "seconds": r.get("seconds"),
                                   "summary": scrub(r.get("summary")), "profile": scrub(probe_prof)}
                if (r.get("summary") or {}).get("run_id"):
                    rp_run_ids.append((r["summary"] or {}).get("run_id"))
                save_text(os.path.join(run_dir, "probe_run.log"), r.get("out") or "")
                if r.get("err"):
                    save_text(os.path.join(run_dir, "probe_run.err.txt"), r.get("err"))
                touch()
                summary["phases"].append({"phase": "probe", "rc": probe_rc, "timeout": r.get("timeout"),
                                          "seconds": r.get("seconds")})
                log("试探发车返回 rc=%s（%ss）" % (probe_rc, r.get("seconds")))
                if r.get("timeout"):
                    summary["warnings"].append(
                        "试探子进程超时被终止——可能有残留！跑 `mas_run_profile.py --status` / `--reconcile --apply` 复核")
                    summary["rc"] = 4
                    rc = 4
                    raise SystemExit(4)
                # ⑤ 证据
                ev = collect_evidence(args.account, stage, script, day, pre_files, since_local)
                save_json(os.path.join(run_dir, "evidence.json"), ev)
                verdict = judge_probe(stage, ev.get("attempts_new") or [], ev.get("asst") or {},
                                      scan_ok=bool(ev.get("scan_ok")), scan_error=str(ev.get("scan_error") or ""))
                verdict["probe_rc"] = probe_rc
                summary["verdict"] = scrub(verdict)
                audit({"run_id": run_id, "event": "verdict", "stage": stage, "status": verdict["status"],
                       "kind": verdict["kind"], "reason": verdict["reason"],
                       "attempts_new": len(ev.get("attempts_new") or [])})
                log("判定：%s（%s）—— %s" % (verdict["status"], verdict["kind"], verdict["reason"]))
                if probe_rc not in (0, 3):
                    # 发车前被拒 / 还原失败：不做台账更新，如实报告
                    if probe_rc == 4:
                        summary["warnings"].append("试探还原失败（rc=4）——有残留，需要人工复核")
                        rc = 4
                    elif probe_rc == 2:
                        summary["warnings"].append("试探在发车前失败/被拒（rc=2）——没有发出任务，台账不动")
                        rc = 2
                    else:
                        summary["warnings"].append("试探返回非常规 rc=%s——按未通过处理（已按 run_profile 自身还原路径收尾）" % probe_rc)
                        rc = 3
                    summary["rc"] = rc
                    verdict = dict(verdict, status="aborted",
                                   kind="dispatch_aborted",
                                   reason="试探未发出（mas_run_profile rc=%s）" % probe_rc)
                    summary["verdict"] = scrub(verdict)
                    raise SystemExit(rc)
                if verdict["status"] != ST_SUCCESS:
                    # 失败/无定论 ⇒ 立即停止 + 归类 + 证据；不重试
                    tri = run_triage(args.account)
                    summary["triage"] = scrub({"rc": tri.get("rc"), "data": tri.get("data"),
                                               "error": tri.get("err")})
                    audit({"run_id": run_id, "event": "triage", "kind": (tri.get("data") or {}).get("kind"),
                           "severity": (tri.get("data") or {}).get("severity")})
                    if verdict["status"] == ST_FAILED and not args.no_ledger_update:
                        upd = update_and_verify(ledger, args.account, stage, args)
                        summary["ledger_update"] = scrub(upd)
                    summary["rc"] = 3
                    rc = 3
                    raise SystemExit(3)
                # success ⇒ 记台账
                if not args.no_ledger_update:
                    upd = update_and_verify(ledger, args.account, stage, args)
                    summary["ledger_update"] = scrub(upd)
                    if upd.get("after_status") not in (None, ST_SUCCESS):
                        summary["warnings"].append(
                            "台账更新后 status=%s ≠ success（已如实记录，见 ledger_update）" % upd.get("after_status"))
                        log("⚠ 台账更新后仍是 %s —— 见 evidence/ledger_update" % upd.get("after_status"))
                    else:
                        log("台账已并进：%s × %s = %s" % (args.account, stage, upd.get("after_status")))
                if args.probe_only:
                    log("--probe-only：试探成功，按约定不再正式刷")
                    summary["rc"] = 0
                    rc = 0
                    raise SystemExit(0)

            # ⑥ 正式刷（台账 success ⇒ 直刷；或试探成功后）
            audit({"run_id": run_id, "event": "farm_dispatch", "account": args.account, "stage": stage,
                   "series": str(args.times), "profile": scrub(farm_prof)})
            farm_timeout = max(1800.0, 300 + float(args.farm_max_wait) + 1500)
            r = run_rp(farm_path, apply=True,
                       extra=rp_extra + ["--json", "--max-wait-seconds", str(float(args.farm_max_wait))],
                       timeout=farm_timeout)
            summary["farm"] = {"rc": r.get("rc"), "timeout": r.get("timeout"), "seconds": r.get("seconds"),
                               "summary": scrub(r.get("summary")), "profile": scrub(farm_prof)}
            if (r.get("summary") or {}).get("run_id"):
                rp_run_ids.append((r["summary"] or {}).get("run_id"))
            save_text(os.path.join(run_dir, "farm_run.log"), r.get("out") or "")
            summary["phases"].append({"phase": "farm", "rc": r.get("rc"), "timeout": r.get("timeout"),
                                      "seconds": r.get("seconds")})
            log("正式刷返回 rc=%s（%ss）" % (r.get("rc"), r.get("seconds")))
            if r.get("timeout"):
                summary["warnings"].append("正式刷子进程超时被终止——可能有残留！需要复核")
                summary["rc"] = 4
                rc = 4
                raise SystemExit(4)
            if r.get("rc") == 0:
                summary["rc"] = 0
                rc = 0
            else:
                summary["rc"] = 5
                summary["warnings"].append("正式刷阶段失败（rc=%s）——试探的结论已记录，正式刷未按预期完成" % r.get("rc"))
                rc = 5
            touch()
    except SystemExit as e:
        code = getattr(e, "code", 3)
        rc = int(code) if code is not None else 3

    # ⑦ 收尾复核（无论在不在锁内都已还原）
    chk = verify_restored(t_start, rp_run_ids)
    summary["restore_check"] = scrub(chk)
    if chk.get("open_runs") or chk.get("temp_plans"):
        summary["warnings"].append("收尾复核有异常：%s" % json.dumps(
            {"open_runs": chk.get("open_runs"), "temp_plans": chk.get("temp_plans")}, ensure_ascii=False))
        if rc in (0, 3, 5):
            rc = 4
    if chk.get("error"):
        summary["warnings"].append("收尾复核未跑通（%s）——请人工看一眼 run_profile --status" % chk.get("error"))

    summary["rc"] = rc
    # ⑧ 审计（全局台账一行）
    try:
        verdict = summary.get("verdict") or {}
        ledger_append({
            "kind": "probe_farm", "ok": rc == 0, "run_id": run_id,
            "scope": "%s × %s（tf: %s）" % (args.account, stage, summary.get("ledger", {}).get("status")),
            "detail": json.dumps({
                "decision": (summary.get("decision") or {}).get("action"),
                "probe_rc": (summary.get("probe") or {}).get("rc"),
                "verdict": {"status": verdict.get("status"), "kind": verdict.get("kind")},
                "ledger_after": (summary.get("ledger_update") or {}).get("after_status"),
                "farm_rc": (summary.get("farm") or {}).get("rc"),
                "restore": {"open_runs": (chk.get("open_runs") or []),
                            "temp_plans": (chk.get("temp_plans") or [])},
            }, ensure_ascii=False)[:500]})
    except Exception as e:  # noqa: BLE001
        summary["warnings"].append("写全局台账失败：%s" % e)
    audit({"run_id": run_id, "event": "done", "rc": rc,
           "verdict": (summary.get("verdict") or {}).get("kind"),
           "farm_rc": (summary.get("farm") or {}).get("rc")})
    save_json(os.path.join(run_dir, "summary.json"), scrub(summary))
    if args.json:
        print(json.dumps(scrub(summary), ensure_ascii=False))
    else:
        print(render_plan_text(plan))
        if summary.get("probe"):
            print("试探：rc=%s（%ss）" % ((summary["probe"] or {}).get("rc"), (summary["probe"] or {}).get("seconds")))
        v = summary.get("verdict") or {}
        if v:
            print("判定：%s（%s）—— %s" % (v.get("status"), v.get("kind"), v.get("reason")))
        lu = summary.get("ledger_update") or {}
        if lu:
            print("台账：%s → %s（update rc=%s）" % (lu.get("before_status"), lu.get("after_status"), lu.get("rc")))
        f = summary.get("farm") or {}
        if f:
            print("正式刷：rc=%s（%ss）" % (f.get("rc"), f.get("seconds")))
        print("⇒ rc=%d ｜ 证据目录 %s" % (rc, run_dir))
    return rc, summary


def update_and_verify(ledger: dict, account: str, stage: str, args) -> dict:
    """stage_capability --update 把本次尝试并进台账，再读回核对。"""
    before = ledger.get("status")
    r = run_capability_update(days=int(getattr(args, "ledger_days", 1) or 1))
    after = ledger_lookup(account, stage, json_path=args.capability_json)
    out = {"rc": r.get("rc"), "before_status": before, "after_status": after.get("status"),
           "report_built_at": after.get("report_built_at"), "row": scrub(after.get("row")),
           "stdout_tail": (r.get("out") or "").strip().splitlines()[-3:], "err": (r.get("err") or "")[-300:]}
    audit({"event": "ledger_update", "account": account, "stage": stage, "before": before,
           "after": after.get("status"), "rc": r.get("rc")})
    return out


def _script_has_account(script: dict, account: str) -> bool:
    try:
        rp = _rp()
        users = rp.fetch_users(script["sid"])
    except Exception:  # noqa: BLE001
        return False
    return any(u.get("name") == account for u in users)


# ---------------------------------------------------------------- 其它命令
def cmd_list(args) -> int:
    rep = load_json(args.capability_json or CAP_JSON, None)
    if not rep:
        print("读不到台账 %s —— 先跑 python tools/stage_capability.py --build" % (args.capability_json or CAP_JSON))
        return 2
    want = args.list
    rows = [r for r in rep.get("rows", []) if want == "all" or r.get("status") == want]
    print("台账 %s（built_at %s，窗口 %s~%s）｜筛选=%s ｜ %d 条"
          % (args.capability_json or CAP_JSON, rep.get("built_at"), rep["window"]["start"],
             rep["window"]["end"], want, len(rows)))
    mapping = {}
    if not args.no_rest:
        with contextlib.suppress(Exception):
            rp = _rp()
            for sid, sc in rp.fetch_scripts().items():
                users = rp.fetch_users(sid)
                for u in users:
                    mapping.setdefault(u.get("name"), (sc["name"], sc["dir"],
                                                       bool(u.get("status")), len(users)))
    for r in rows:
        m = mapping.get(r["account"])
        print("  %-12s %-24s %-10s last_ok=%-10s last_fail=%-10s fail=%s%s"
              % (r["status"], r["account"], r["stage"], r.get("last_success") or "-",
                 r.get("last_failure") or "-",
                 ",".join((r.get("failure_types") or {}).keys()) if isinstance(r.get("failure_types"), dict)
                 else (r.get("failure_types") or []),
                 ("  脚本=%s/%s%s" % (m[0], m[1], "" if m[2] else "（未启用）")) if m else ""))
    for acct, lst in (rep.get("unknown") or {}).items():
        if want in ("unknown", "all"):
            tried = [x["stage"] for x in lst if x.get("tried")]
            untried = [x["stage"] for x in lst if not x.get("tried")]
            if tried or untried:
                m = mapping.get(acct)
                print("  unknown池 %-22s 试过没成=%s 从未尝试=%s%s"
                      % (acct, "、".join(tried) or "—", "、".join(untried) or "—",
                         ("  脚本=%s/%s" % (m[0], m[1])) if m else ""))
    return 0


def cmd_status(args) -> int:
    print("锁：%s" % ("空闲" if not os.path.exists(LOCK) else "被占（%s）" % LOCK))
    if not os.path.exists(LOG):
        print("（无历史记录 %s）" % LOG)
        return 0
    lines = [x for x in open(LOG, "r", encoding="utf-8").read().splitlines() if x.strip()]
    for ln in lines[-max(1, int(args.tail)):]:
        with contextlib.suppress(ValueError):
            rec = json.loads(ln)
            print("  %s %-18s %s" % (rec.get("ts"), rec.get("event"),
                                     json.dumps({k: v for k, v in rec.items()
                                                 if k not in ("ts", "event")}, ensure_ascii=False)[:220]))
    runs = sorted(d for d in os.listdir(RUNS_DIR) if os.path.isdir(os.path.join(RUNS_DIR, d))) \
        if os.path.isdir(RUNS_DIR) else []
    print("证据目录（最近 8 个）：%s" % ("、".join(runs[-8:]) or "—"))
    return 0


# ---------------------------------------------------------------- 离线自测
def _mk_attempt(file, result, drops, lines, mt="2026-09-28 09:00:00"):
    return {"file": file, "mt": mt, "result": result, "drops": drops, "lines": lines, "bad": False}


def _fixture_report(path):
    rep = {"schema": "maaorch.stage_capability/1", "built_at": "2026-09-28 06:19:38",
           "window": {"days": 30, "start": "2026-08-30", "end": "2026-09-28", "attempts": 10},
           "stats": {}, "accounts": ["甲", "乙", "丙"], "account_days": {}, "stages": ["1-7", "SR-7"],
           "rows": [
               {"account": "甲", "stage": "1-7", "status": "success", "last_success": "2026-09-28",
                "last_failure": "", "last_attempt": "2026-09-28", "success_n": 3, "selected_n": 3,
                "not_open_n": 0, "attempt_n": 3, "actions": 3, "sources": ["drop_statistics"],
                "failures": [], "net_fail_n": 0, "failure_types": {}, "fail_n": 0,
                "fail_after_success": False, "impact": ""},
               {"account": "乙", "stage": "SR-7", "status": "failed", "last_success": "",
                "last_failure": "2026-09-27", "last_attempt": "2026-09-27", "success_n": 0,
                "selected_n": 1, "not_open_n": 1, "attempt_n": 1, "actions": 0, "sources": [],
                "failures": [{"type": "stage_not_open", "day": "2026-09-27", "file": "x.json",
                              "line": "GetFightStage: from [\"SR-7\"], selected null",
                              "capability_impact": True}],
                "net_fail_n": 0, "failure_types": {"stage_not_open": 1}, "fail_n": 1,
                "fail_after_success": False, "impact": "capability"},
           ], "unknown": {"丙": [{"stage": "1-7", "tried": True}]}}
    save_json(path, rep)
    return path


def selftest() -> dict:
    import tempfile
    cases = []

    def case(name, fn):
        try:
            fn()
            cases.append({"name": name, "ok": True, "detail": ""})
        except Exception as e:  # noqa: BLE001
            cases.append({"name": name, "ok": False, "detail": "%s: %s" % (type(e).__name__, e)})

    tmp = tempfile.mkdtemp(prefix="probe_farm_selftest_")
    cap = _fixture_report(os.path.join(tmp, "cap.json"))

    def chk(cond, msg=None):
        if not cond:
            raise AssertionError(repr(msg) if msg is not None else "条件不成立")

    def t_ledger_success():
        lg = ledger_lookup("甲", "1-7", json_path=cap)
        chk(lg["status"] == "success", lg)
        d = decide(lg)
        chk(d["action"] == "farm", d)

    def t_force_probe():
        lg = ledger_lookup("甲", "1-7", json_path=cap)
        chk(decide(lg, force_probe=True)["action"] == "probe")

    def t_failed_refuse():
        lg = ledger_lookup("乙", "SR-7", json_path=cap)
        chk(lg["status"] == "failed")
        d = decide(lg)
        chk(d["action"] == "refuse", d)
        chk("stage_not_open" in ledger_refusal_text(lg))

    def t_failed_retry():
        lg = ledger_lookup("乙", "SR-7", json_path=cap)
        chk(decide(lg, fix_note="关卡已开放")["action"] == "probe")

    def t_unknown_probe():
        lg = ledger_lookup("丙", "1-7", json_path=cap)
        chk(lg["status"] == "unknown" and lg["tried"] is True, lg)
        chk(decide(lg)["action"] == "probe")

    def t_missing_report():
        lg = ledger_lookup("Nobody", "1-7", json_path=os.path.join(tmp, "nope.json"))
        chk(lg["status"] == "unknown" and lg["missing_report"])
        chk(decide(lg)["action"] == "probe")

    def t_profile_probe():
        p = build_profile("甲", "S", "1-7", PROBE_SERIES, 0, False, timeout_s=600)
        chk(p["series"] == "1" and p["stage_plan"] == ["1-7"], p)
        chk(p["restore_after"] == "timeout:600s")
        chk(p["keep_remain_sanity_task"] is False)
        chk(p["medicine"] == {"use": False, "count": 0})
        chk(p["activity_priority"] == {"enabled": False, "medicine": 0})

    def t_profile_farm():
        p = build_profile("甲", "S", "1-7", "3", 2, True, timeout_s=None)
        chk(p["series"] == "3" and p["restore_after"] == "run_end")
        chk(p["medicine"] == {"use": True, "count": 2} and p["keep_remain_sanity_task"] is True)

    def t_judge_success_drops():
        v = judge_probe("1-7", [_mk_attempt("a.json", "Success!", ["1-7"], [])], {})
        chk(v["status"] == "success" and v["kind"] == "stage_farmed", v)

    def t_judge_success_droplog():
        lines = ["GetFightStage: from [\"1-7\"], selected 1-7", "开始行动 1 次, -6理智", "1-7 掉落统计: "]
        v = judge_probe("1-7", [_mk_attempt("a.json", "任务被用户手动中止", [], lines)], {})
        chk(v["status"] == "success" and v["kind"] == "fight_log_drop", v)

    def t_judge_not_open():
        lines = ["GetFightStage: from [\"SR-7\"], selected null"]
        v = judge_probe("SR-7", [_mk_attempt("a.json", "Success!", [], lines)], {})
        chk(v["status"] == "failed" and v["kind"] == "stage_not_open", v)

    def t_judge_unavailable():
        lines = ["GetFightStage: from [\"1-7\"], selected 1-7", "Task Chain: Fight", "任务出错: 理智作战"]
        v = judge_probe("1-7", [_mk_attempt("a.json", "任务被用户手动中止", [], lines)], {})
        chk(v["status"] == "failed" and v["kind"] == "stage_unavailable", v)

    def t_judge_proxy():
        lines = ["GetFightStage: from [\"1-7\"], selected 1-7", "开始行动 1 次, -6理智", "代理指挥失误"]
        v = judge_probe("1-7", [_mk_attempt("a.json", "任务被用户手动中止", [], lines)], {})
        chk(v["status"] == "failed" and v["kind"] == "proxy_mismatch", v)

    def t_judge_net():
        # 现实中掉线标记在「任务出错」之前（stage_capability 同款时序口径）
        lines = ["GetFightStage: from [\"1-7\"], selected 1-7", "游戏掉线", "任务出错: 理智作战"]
        v = judge_probe("1-7", [_mk_attempt("a.json", "任务被用户手动中止", [], lines)], {})
        chk(v["status"] == "inconclusive" and v["kind"] == "fight_error_net", v)

    def t_judge_no_attempt():
        v = judge_probe("1-7", [], {})
        chk(v["status"] == "inconclusive" and v["kind"] == "no_attempt", v)

    def t_judge_sanity():
        lines = ["GetFightStage: from [\"1-7\"], selected 1-7"]
        asst = {"sanity_remain": 3, "sanity_cost": 6, "counts": {}}
        v = judge_probe("1-7", [_mk_attempt("a.json", "任务被用户手动中止", [], lines)], asst)
        chk(v["status"] == "inconclusive" and v["kind"] == "insufficient_sanity", v)

    def t_judge_selected_no_result():
        lines = ["GetFightStage: from [\"1-7\"], selected 1-7", "开始行动 1 次, -6理智"]
        v = judge_probe("1-7", [_mk_attempt("a.json", "任务被用户手动中止", [], lines)], {})
        chk(v["status"] == "inconclusive" and v["kind"] == "selected_no_result", v)

    def t_judge_annih():
        v = judge_probe("Annihilation", [_mk_attempt("a.json", "Success!", [],
                                                     ["GetFightStage: from [\"Annihilation\"], selected Annihilation",
                                                      "完成任务: 剿灭作战"])], {})
        chk(v["status"] == "success" and v["kind"] == "annihilation_chain_done", v)

    def t_asst_summary():
        lines = ['[2026-09-28 08:00:57.741][INF][Px1][Tx1] asst::FightTimesTaskPlugin::analyze_sanity_remain Current Sanity: 3 , Max Sanity: 210',
                 '[2026-09-28 08:00:57.827][INF][Px1][Tx1] SubTaskExtraInfo {"class":"asst::FightTimesTaskPlugin","details":{"sanity_cost":6,"series":1}}',
                 '[2026-09-28 08:00:57.309][TRC][Px1][Tx1] asst::PipelineAnalyzer::analyze | OcrDetect Fight@StartButton1 [{ text: 开始行动 }] -> ClickSelf',
                 '[2026-09-28 08:00:56.000][INF][Px1][Tx1] asst::Assistant::append_task Fight {"stage":"1-7","medicine":0,"times":2147483647,"series":1}']
        s = asst_summary(lines, since=dt.datetime(2026, 9, 28, 0, 0, 0))
        chk(s["sanity_remain"] == 3 and s["sanity_cost"] == 6, s)
        chk(s["params"]["stage"] == "1-7" and s["params"]["series"] == 1, s)
        chk(s["counts"].get("Fight@StartButton1") == 1, s)

    def t_asst_window_filter():
        lines = ['[2026-09-28 06:00:00.000][INF][Px1][Tx1] asst::Assistant::append_task Fight {"stage":"9-9","series":9,"medicine":9}',
                 '[2026-09-28 09:00:00.000][INF][Px1][Tx1] asst::Assistant::append_task Fight {"stage":"1-7","series":1,"medicine":0}']
        s = asst_summary(lines, since=dt.datetime(2026, 9, 28, 8, 0, 0))
        chk(s["params"]["stage"] == "1-7", s)

    def t_times_domain():
        chk(str(6) in TIMES_OK and "9" not in TIMES_OK and "-1" in TIMES_OK)

    def t_clean_line():
        raw = "[2026-09-28 08:00:41.866][INF][AsstProxy]              <2> Completed Task Chain: Fight, Task ID: 2"
        chk(_clean_line(raw).startswith("Completed Task Chain"), _clean_line(raw))

    def t_lock_release():
        os.environ["MAAORCH_PROBE_FARM_RUNTIME"] = tmp
        # hold_lock 用模块级 LOCK（导入期固定）——这里直接测文件语义
        p = os.path.join(tmp, "lk")
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        chk(os.path.exists(p))
        os.remove(p)
        chk(not os.path.exists(p))

    case("台账 success ⇒ 直刷", t_ledger_success)
    case("--force-probe 覆盖 success", t_force_probe)
    case("台账 failed ⇒ 拒绝", t_failed_refuse)
    case("failed + 修复说明 ⇒ 试探", t_failed_retry)
    case("台账 unknown ⇒ 试探", t_unknown_probe)
    case("台账报告缺失 ⇒ unknown/试探", t_missing_report)
    case("probe profile 字段（Series=1/timeout/不挂剩余理智）", t_profile_probe)
    case("farm profile 字段（SeriesNumb/run_end/保留）", t_profile_farm)
    case("判定：drop_statistics 命中 ⇒ success", t_judge_success_drops)
    case("判定：副本日志掉落统计 ⇒ success", t_judge_success_droplog)
    case("判定：未开放 ⇒ failed/stage_not_open", t_judge_not_open)
    case("判定：选中没开打 ⇒ failed/stage_unavailable", t_judge_unavailable)
    case("判定：代理指挥失误 ⇒ failed/proxy_mismatch", t_judge_proxy)
    case("判定：掉线 ⇒ inconclusive/fight_error_net", t_judge_net)
    case("判定：无新增尝试 ⇒ inconclusive/no_attempt", t_judge_no_attempt)
    case("判定：理智不足 ⇒ inconclusive/insufficient_sanity", t_judge_sanity)
    case("判定：选中无结论 ⇒ inconclusive/selected_no_result", t_judge_selected_no_result)
    case("判定：剿灭链完成 ⇒ success", t_judge_annih)
    case("asst 锚点归总（sanity/params/计数）", t_asst_summary)
    case("asst 时窗过滤（旧行不算）", t_asst_window_filter)
    case("times 取值域（0..6/-1）", t_times_domain)
    case("日志行前缀剥除", t_clean_line)
    case("单飞锁文件语义", t_lock_release)
    passed = sum(1 for c in cases if c["ok"])
    return {"ok": passed == len(cases), "passed": passed, "failed": len(cases) - passed, "cases": cases}


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="先试探后开刷（AUTO-MAS）：台账 → 必要时一次最小试探 → 成功才正式刷 N 次")
    ap.add_argument("--account", help="账号名（与 AUTO-MAS 一致）")
    ap.add_argument("--stage", help="关卡码（1-7 / CE-6 / SR-7…；剿灭请走剿灭配置，不在本工具范围）")
    ap.add_argument("--times", default="1",
                    help="正式刷的连战倍数 SeriesNumb（0..6，0=自动，-1=不调整；**不是总次数**）。默认 1")
    ap.add_argument("--medicine", type=int, default=0, help="理智药额度（0=不吃；默认 0=保守）")
    ap.add_argument("--force-probe", action="store_true", help="台账 success 也要先试探一次")
    ap.add_argument("--retry-after-fix", metavar="说明", default="",
                    help="台账 failed 时放行（进试探）：写一句话说明修了什么")
    ap.add_argument("--timeout", type=float, default=600.0, help="试探等待上限秒（restore_after=timeout:Ns，默认 600）")
    ap.add_argument("--farm-max-wait", type=float, default=10800.0,
                    help="正式刷等待上限秒（restore_after=run_end 的兜底，默认 10800=3h）")
    ap.add_argument("--probe-only", action="store_true", help="只试探：成功后不进入正式刷")
    ap.add_argument("--remain-sanity", dest="remain_sanity", action="store_true",
                    help="保留「剩余理智」任务（复制该号当日 Stage_Remain；默认不挂）")
    ap.add_argument("--no-remain-sanity", dest="remain_sanity", action="store_false", help="（默认）不挂剩余理智")
    ap.set_defaults(remain_sanity=False)
    ap.add_argument("--script", help="目标脚本名/uid（默认按账号自动解析；必须唯一命中）")
    ap.add_argument("--disable-others", dest="disable_others", action="store_true",
                    help="多号脚本：临时停用其它启用号（透传 mas_run_profile）")
    ap.add_argument("--all-accounts", dest="all_accounts", action="store_true",
                    help="多号脚本：接受整脚本跑（透传 mas_run_profile）")
    ap.add_argument("--write-mode", choices=["plan", "fixed"], default="plan", help="发车写路线（默认 plan=临时计划表）")
    ap.add_argument("--no-ledger-update", action="store_true", help="不跑 stage_capability --update")
    ap.add_argument("--ledger-days", type=int, default=1, help="台账增量扫描天数（默认 1 天）")
    ap.add_argument("--capability-json", help="台账 JSON 路径覆盖（自测/离线用）")
    ap.add_argument("--apply", action="store_true", help="真执行（默认 dry-run：只打印计划，零写）")
    ap.add_argument("--dry-run", action="store_true", help="显式 dry-run（默认行为）")
    ap.add_argument("--list", choices=["unknown", "failed", "success", "all"], help="只读：列台账行与账号脚本归属")
    ap.add_argument("--no-rest", action="store_true", help="--list 时不查 REST（离线）")
    ap.add_argument("--status", action="store_true", help="只读：本工具历史记录 + 锁状态")
    ap.add_argument("--tail", type=int, default=15, help="--status 显示条数")
    ap.add_argument("--selftest", action="store_true", help="离线自测（零触网）")
    ap.add_argument("--json", action="store_true", help="JSON 输出（单行）")
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
    if a.list:
        return cmd_list(a)
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
        print("拒绝：--times=%s 越界（只允许 %s）。注意 Series 是连战倍数、**不是总次数上限**——"
              "AUTO-MAS 两层没有「只刷 N 次」字段（docs/MAS_RUN_PROFILE.md §2 行 4）。"
              % (a.times, "/".join(TIMES_OK)), file=sys.stderr)
        return 2
    if not (0 <= int(a.medicine) <= 9999):
        print("拒绝：--medicine 取值域 0–9999", file=sys.stderr)
        return 2
    if a.disable_others and a.all_accounts:
        print("拒绝：--disable-others 与 --all-accounts 互斥", file=sys.stderr)
        return 2
    if a.timeout < 60:
        print("拒绝：--timeout 太小（<60s）；试探需要跑完一轮登录+战斗，建议 ≥600", file=sys.stderr)
        return 2
    a.apply = bool(a.apply and not a.dry_run)
    try:
        rc, _summary = run(a)
        return rc
    except LockBusy as e:
        print("拒绝：%s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
