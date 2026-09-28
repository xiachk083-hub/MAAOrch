#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""maa_upgrade_check.py —— MAA 升级护栏：更新前快照 → 更新后校验注入 → 差异报告 → 回滚凭据。

定位：MAA 会自己更新（`resource/` 资源 OTA 每天在动 + 程序本体走 `MAA.Updater.exe`），
而**它的 GUI 配置 schema 会随大版本迁移**。真实教训：6.x 把「账号切换」从全局键搬到
per-task 字段 `StartUpTask.AccountName/AccountSwitchEnabled`，AUTO-MAS 注入层没跟上
⇒ **切号静默失效数月**（任务照样跑完，跑的是别人的号）。所以升级不是「装完就行」，
必须把「升级前是什么样」变成可比对的事实，并在升级后**在下一班次发车前**校验注入面。

权威判据分三层（别凭记忆改）：
  ① **程序身份** = `MAA.dll` ProductVersion（带 commit，形如 `v6.18.0+23feeddc…`）
     + `MaaCore.dll` sha256（MaaCore 无版本号，只能用哈希认）；
  ② **资源身份** = `resource\tasks\tasks.json` sha256（MaaCore 任务图，6.18.0 有 1090 个节点）
     + `resource\version.json`（游戏活动/卡池版本）+ `cache\version\summary.json`（上游版本面）；
  ③ **注入面** = `config\gui.new.json` 的 TaskQueue 字段/取值与关键设置键——
     **这一层才是 AUTO-MAS 真正写、MAA 真正读的东西，也是历史上被打穿的那一层**。
     账号注入合规不另立口径：一律复用 `tools/mas_switch_gate.py`（官方 masked+true / 非官 empty+false，fail-closed）。

「升级≠故障」：程序/资源变化只记 `changed`（报告里点名），判定失败只看
「我们依赖的字段/取值还在不在」。退出码 0/1/2 分别对应 无漂移 / 漂移违规 / 读不到(fail-closed)。

用法（默认全部只读；live 子命令默认 dry-run）：
    python tools/maa_upgrade_check.py snapshot                     # 升级前建基线
    python tools/maa_upgrade_check.py snapshot --all               # 含退役目录 MAA_13/14/25/28
    python tools/maa_upgrade_check.py verify                       # 升级后对比最近基线 → 报告
    python tools/maa_upgrade_check.py verify --baseline 20260928_092308   # 指定基线
    python tools/maa_upgrade_check.py live --script 明日方舟-官-23  # 注入→读回（dry-run）
    python tools/maa_upgrade_check.py live --script 明日方舟-官-23 --apply
    python tools/maa_upgrade_check.py show                         # 最近基线摘要
    python tools/maa_upgrade_check.py --selftest                   # 离线自测（假传输，不连网）

产出/审计：
    runtime/upgrade_guard/<stamp>/baseline.json       基线（身份三元组 + 注入面 + 闸门）
    runtime/upgrade_guard/<stamp>/tasks/<sha16>.json  各版本 tasks.json 原件（节点级 diff 的凭据）
    runtime/upgrade_guard/<stamp>/gui/<槽>.gui.new.json  各槽配置原件（注入面凭据）
    runtime/upgrade_guard/<stamp>_verify/report.md    verify 的人读报告（+ diff.json）
    runtime/upgrade_guard/current.json               最近基线指针
    runtime/upgrade_guard/log.jsonl                  每次 snapshot/verify/live 一行
    runtime/maa_upgrade_check.lock                   live 单飞锁

约束（写入方不变式）：
  * 只读远端：不升级/降级 MAA、不改任何远端文件；写入只落 local `runtime/`。
  * live 子命令唯一的外部写动作 = `POST /api/dispatch/start|stop`（默认 dry-run；`--apply` 才真发车，
    且拒绝班次窗口与占用槽位）。要停 live 车只走官方 stop，绝不 kill 进程/重启侧车。
  * 基线**不含凭据**：掩码手机号是身份不是秘密；账号明文 Id/密码不落盘。

退出码：0 = 无漂移；1 = 漂移/注入违规（需处置）；2 = UNKNOWN（读不到/判不了，不当通过）。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import mas_ondemand as od  # noqa: E402  （ssh/REST 基建单一来源）
import mas_switch_gate as gate  # noqa: E402  （注入合规闸门单一来源）

RUNTIME = os.environ.get("MAAORCH_UPGRADE_RUNTIME") or os.path.join(ROOT, "runtime")
GUARD_DIR = os.path.join(RUNTIME, "upgrade_guard")
CURRENT = os.path.join(GUARD_DIR, "current.json")
LOG = os.path.join(GUARD_DIR, "log.jsonl")
LOCK = os.path.join(RUNTIME, "maa_upgrade_check.lock")
STAMP_FMT = "%Y%m%d_%H%M%S"
DEFAULT_DIRS = [r"E:\MAA-Multi\MAA_%d" % i for i in range(1, 13)]
RETIRED_DIRS = [r"E:\MAA-Multi\MAA_%d" % i for i in (13, 14, 25, 28)]
MAA_DIR_RE = od.MAA_DIR_RE
# 远端 PS 脚本上限：命令行 >~8KB（base64 撑大后）会静默空输出 ⇒ 每段脚本必须远小于 3K 字符
PS_CHUNK_LIMIT = 2600

# 我们依赖的注入面（AUTO-MAS AutoProxy.py L725-800 写的就是这些键；少一个 = 那条链断了）
WATCH_TASK_TYPES = ["StartUpTask", "FightTask", "InfrastTask", "RecruitTask", "MallTask", "AwardTask",
                    "RoguelikeTask"]
WATCH_FIELDS = {
    "StartUpTask": ["AccountName", "AccountSwitchEnabled", "IsEnable"],
    "FightTask": ["StagePlan", "Series", "MedicineCount", "UseMedicine", "IsEnable",
                  "EnableTimesLimit", "EnableTargetDrop"],
    "InfrastTask": ["Mode", "IsEnable"],
    "RecruitTask": ["MaxTimes", "IsEnable"],
    "MallTask": ["Shopping", "IsEnable"],
    "AwardTask": ["Award", "IsEnable"],
    "RoguelikeTask": ["Theme", "Mode", "IsEnable"],
}
# tasks.json 节点级关注清单（切号/战斗/资源链上的关键节点；改名/消失 ⇒ 影响链）
WATCH_NODES = [
    "StartUp", "StartUpBegin", "SwitchAccount@StartUpBegin", "SwitchAccount@LoadingIcon",
    "Fight", "FightBegin", "Fight@StartButton", "Recruit", "RecruitBegin",
    "Infrast", "Mall", "Award", "CloseDown", "OfflineConfirm",
]
NO_GO_ENV = ("MAAORCH_UPGRADE_NO_GO", "MAAORCH_DISPATCH_NO_GO")


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------

def now_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def stamp() -> str:
    return dt.datetime.now().strftime(STAMP_FMT)


def ensure_dir(p: str) -> str:
    os.makedirs(p, exist_ok=True)
    return p


def log_line(rec: dict) -> None:
    try:
        ensure_dir(GUARD_DIR)
        rec = dict(rec)
        rec.setdefault("ts", now_str())
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def no_go() -> str | None:
    for env in NO_GO_ENV:
        if str(os.environ.get(env, "")).strip().lower() in {"1", "true", "yes", "on"}:
            return env
    return None


def _env_int(name: str, default: int) -> int:
    try:
        raw = str(os.environ.get(name, "")).strip()
        return int(raw) if raw else default
    except (TypeError, ValueError):
        return default


def read_json(p: str) -> dict | None:
    try:
        with io.open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(p: str, obj) -> None:
    ensure_dir(os.path.dirname(p))
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def b64_text(s: str) -> str:
    return base64.b64encode((s or "").encode("utf-8")).decode("ascii")


def from_b64_text(s: str) -> str:
    try:
        return base64.b64decode((s or "").encode("ascii")).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def _val(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# ---------------------------------------------------------------------------
# 远端只读探针（分段跑：PS 脚本经 -EncodedCommand 有 ~8KB 命令行上限，超了会静默空输出）
# ---------------------------------------------------------------------------

_PS_HEAD = r"""$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'
$OutputEncoding=[System.Text.Encoding]::UTF8; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
"""

_PS_A = r"""$dirs=@(__DIRS__)
$run=@()
foreach($p in @(Get-CimInstance Win32_Process -Filter "Name='MAA.exe'" -EA SilentlyContinue)){try{$run+=[string]$p.ExecutablePath}catch{}}
foreach($d in $dirs){
 $n=Split-Path $d -Leaf
 if(-not (Test-Path -LiteralPath $d)){Write-Output ("A|$n|MISSING|0|");continue}
 $r=$false;foreach($x in $run){if($x -and $x.StartsWith($d,'OrdinalIgnoreCase')){$r=$true}}
 $exe=Join-Path $d 'MAA.exe';$fv='';$pv=''
 if(Test-Path -LiteralPath $exe){$vi=(Get-Item -LiteralPath $exe).VersionInfo;$fv=[string]$vi.FileVersion;$pv=[string]$vi.ProductVersion}
 $dl=Join-Path $d 'MAA.dll';$dfv='';$dpv=''
 if(Test-Path -LiteralPath $dl){$vi2=(Get-Item -LiteralPath $dl).VersionInfo;$dfv=[string]$vi2.FileVersion;$dpv=[string]$vi2.ProductVersion}
 if($r){$rs=1}else{$rs=0}
 $o="A|$n|OK|$rs|exe=$fv#$pv|dll=$dfv#$dpv"
 foreach($k in @('MaaCore.dll','MAA.Updater.exe','filelist.txt','resource\tasks\tasks.json')){
  $p=Join-Path $d $k
  if(Test-Path -LiteralPath $p){
   $i=Get-Item -LiteralPath $p;$h=''
   if($k -eq 'MaaCore.dll' -or $k -eq 'resource\tasks\tasks.json'){$h='#'+(Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.Substring(0,16).ToLower()}
   $o=$o+"|"+$k+"="+$i.Length+"@"+$i.LastWriteTime.ToString('yyyyMMddHHmmss')+$h
  }else{$o=$o+"|"+$k+"=NONE"}
 }
 Write-Output $o
}
Write-Output 'A-END'
"""

_PS_B = r"""$dirs=@(__DIRS__)
foreach($d in $dirs){
 $n=Split-Path $d -Leaf
 foreach($k in @('resource\version.json','cache\version\summary.json','cache\etag.json','cache\last_modified.json','cache\version\stable.json')){
  $p=Join-Path $d $k
  if(Test-Path -LiteralPath $p){
   $i=Get-Item -LiteralPath $p;$b=''
   if($i.Length -le 65536){$b=[Convert]::ToBase64String([IO.File]::ReadAllBytes($p))}
   Write-Output ("B|$n|$k|"+$i.Length+"@"+$i.LastWriteTime.ToString('yyyyMMddHHmmss')+"|"+$b)
  } else { Write-Output ("B|$n|$k|NONE|") }
 }
}
Write-Output 'B-END'
"""


def _ssh_script(script: str, timeout: int) -> str:
    """跑一段远端 PowerShell（-EncodedCommand）。段内脚本必须 <PS_CHUNK_LIMIT 字符。"""
    if len(script) > PS_CHUNK_LIMIT:
        raise ValueError("PS 段过长（%d 字符）——拆小段，避免命令行上限静默空输出" % len(script))
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    cmd = od._ssh_base() + ["powershell -NoProfile -NonInteractive -EncodedCommand " + b64]
    r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


def _ps_dirs(script: str, dirs: list[str]) -> str:
    lit = ",".join("'" + d.replace("'", "''") + "'" for d in dirs)
    return _PS_HEAD + script.replace("__DIRS__", lit)


def parse_probe_out(out_a: str, out_b: str, dirs: list[str]) -> dict:
    """解析 A/B 两段输出 → {name: record}。纯函数（selftest 用）。"""
    recs = {os.path.basename(d): {"name": os.path.basename(d), "path": d, "status": "ERROR",
                                 "error": "missing-in-output", "running": False, "files": {},
                                 "texts": {}} for d in dirs}
    for ln in out_a.splitlines():
        ln = ln.rstrip("\r")
        if not ln.startswith("A|"):
            continue
        p = ln.split("|")
        if len(p) < 5:
            continue
        name, status, running = p[1], p[2], p[3]
        rec = recs.get(name)
        if rec is None:
            continue
        rec["status"] = status
        rec["running"] = running == "1"
        rec["error"] = "" if status == "OK" else "dir not found"
        for item in p[4:]:
            if "=" not in item:
                continue
            k, v = item.split("=", 1)
            rec["files"][k.replace("\\", "/")] = v
    for ln in out_b.splitlines():
        ln = ln.rstrip("\r")
        if not ln.startswith("B|"):
            continue
        p = ln.split("|")
        if len(p) < 5:
            continue
        name, k, meta, b64 = p[1], p[2], p[3], p[4]
        rec = recs.get(name)
        if rec is None:
            continue
        rec["texts"][k.replace("\\", "/")] = {"meta": meta, "b64": b64,
                                              "text": from_b64_text(b64) if b64 else ""}
    return recs


def probe_files(dirs: list[str], timeout: int = 300) -> dict:
    """A 段（文件事实+版本+哈希）+ B 段（小文本文件 base64）。返回 {name: rec}。"""
    out_a = out_b = ""
    errs = []
    try:
        out_a = _ssh_script(_ps_dirs(_PS_A, dirs), timeout)
        if "A-END" not in out_a:
            errs.append("A 段无结束标记")
    except Exception as e:  # noqa: BLE001
        errs.append("A 段：%s: %s" % (type(e).__name__, str(e)[:120]))
    time.sleep(1)
    try:
        out_b = _ssh_script(_ps_dirs(_PS_B, dirs), timeout)
        if "B-END" not in out_b:
            errs.append("B 段无结束标记")
    except Exception as e:  # noqa: BLE001
        errs.append("B 段：%s: %s" % (type(e).__name__, str(e)[:120]))
    recs = parse_probe_out(out_a, out_b, dirs)
    if errs:
        for name, rec in recs.items():
            if rec.get("status") != "OK":
                rec["error"] = "; ".join(errs) + "；" + str(rec.get("error") or "")
    return recs


def _split_meta(meta: str) -> tuple:
    """"size@yyyymmddHHMMSS" 或 "NONE" → (size, mtime)。"""
    if not meta or meta == "NONE":
        return (None, "")
    size, _, ts = meta.partition("@")
    try:
        size_i = int(size)
    except ValueError:
        size_i = None
    mt = ""
    if len(ts) == 14 and ts.isdigit():
        mt = "%s-%s-%s %s:%s:%s" % (ts[0:4], ts[4:6], ts[6:8], ts[8:10], ts[10:12], ts[12:14])
    return (size_i, mt)


def _file_fact(rec: dict, rel: str) -> dict:
    """"size@mtime#sha16" / "NONE" → {exists,size,mtime,sha}。"""
    raw = ((rec.get("files") or {}).get(rel) or "NONE")
    if raw == "NONE":
        return {"exists": False, "size": None, "mtime": "", "sha": ""}
    body, _, sha = raw.partition("#")
    size, mtime = _split_meta(body)
    return {"exists": True, "size": size, "mtime": mtime, "sha": sha}


def _ver_fact(rec: dict, key: str) -> tuple:
    raw = ((rec.get("files") or {}).get(key) or "")
    fv, _, pv = raw.partition("#")
    return (fv, pv)


# ---------------------------------------------------------------------------
# 注入面：本地解析 gui.new.json（精确、可离线自测；原件同时留档）
# ---------------------------------------------------------------------------

def config_facts(gui: dict | None) -> dict:
    """gui.new.json → {parsed, types, schemas, inject, watch}。纯函数。"""
    out = {"parsed": False, "types": [], "schemas": {}, "inject": {"startup": None, "fight": []}, "watch": {}}
    if not isinstance(gui, dict):
        return out
    out["parsed"] = True
    cfgs = gui.get("Configurations") or {}
    cur = gui.get("Current") or "Default"
    spec = cfgs.get(cur) or cfgs.get("Default") or {}
    queue = spec.get("TaskQueue") or []
    types, schemas = [], {}
    fight, startup = [], None
    for t in queue:
        if not isinstance(t, dict):
            continue
        ty = t.get("$type") or t.get("TaskType") or ""
        if not ty:
            continue
        ty = str(ty)
        if ty not in types:
            types.append(ty)
        keys = sorted(k for k in t.keys() if k != "Doc")
        schemas[ty] = sorted(set(schemas.get(ty, [])) | set(keys))
        if ty == "StartUpTask":
            startup = {"accountName": str(t.get("AccountName") or ""),
                       "switchEnabled": t.get("AccountSwitchEnabled"),
                       "isEnable": t.get("IsEnable")}
        elif ty == "FightTask":
            sp = t.get("StagePlan") if t.get("StagePlan") is not None else t.get("Stage")
            sp_text = " ".join(str(x) for x in sp) if isinstance(sp, (list, tuple)) else str(sp or "")
            fight.append({"stagePlan": sp if sp is not None else "", "stagePlanText": sp_text,
                          "isEnable": t.get("IsEnable"), "series": t.get("Series"),
                          "medicineCount": t.get("MedicineCount"), "useMedicine": t.get("UseMedicine"),
                          "enableTimesLimit": t.get("EnableTimesLimit"),
                          "enableTargetDrop": t.get("EnableTargetDrop")})
    out["types"], out["schemas"] = types, schemas
    g = spec.get("Gui") or {}
    ss, cset, rs = (g.get("StartUpSettings") or {}), (g.get("ConnectSettings") or {}), (g.get("RuntimeSettings") or {})
    up = gui.get("Update") or {}
    out["inject"] = {"startup": startup, "fight": fight}
    out["watch"] = {
        "StartUpSettings": {"RunDirectly": ss.get("RunDirectly") if ss else None,
                            "SkipStartupAutoRunAfterUpdate": ss.get("SkipStartupAutoRunAfterUpdate") if ss else None,
                            "StartEmulator": ss.get("StartEmulator") if ss else None},
        "ConnectSettings": {"AutoDetect": cset.get("AutoDetect") if cset else None,
                            "Address": str(cset.get("Address") or "") if cset else "",
                            "Config": str(cset.get("Config") or "") if cset else ""},
        "RuntimeSettings": {"ClientType": str(rs.get("ClientType")) if (rs and rs.get("ClientType") is not None) else "",
                            "StartGame": rs.get("StartGame") if rs else None},
        "Update": {"VersionType": str(up.get("VersionType") or ""),
                   "CheckOnStartup": up.get("CheckOnStartup"),
                   "CheckOnSchedule": up.get("CheckOnSchedule"),
                   "AutoDownloadUpdatePackage": up.get("AutoDownloadUpdatePackage"),
                   "AutoInstallUpdatePackage": up.get("AutoInstallUpdatePackage")},
        "ConfigVersion": {"value": str(gui.get("ConfigVersion", ""))},
    }
    return out


def _read_gui_local(path: str) -> dict | None:
    try:
        with io.open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 身份三元组
# ---------------------------------------------------------------------------

def identity(rec: dict, cfg: dict) -> dict:
    exe_fv, exe_pv = _ver_fact(rec, "exe")
    dll_fv, dll_pv = _ver_fact(rec, "dll")
    core = _file_fact(rec, "MaaCore.dll")
    tasks = _file_fact(rec, "resource/tasks/tasks.json")
    updater = _file_fact(rec, "MAA.Updater.exe")
    filelist = _file_fact(rec, "filelist.txt")
    rvj = (rec.get("texts") or {}).get("resource/version.json") or {}
    try:
        rvj_obj = json.loads(rvj.get("text") or "") or {}
    except ValueError:
        rvj_obj = {}
    csum = (rec.get("texts") or {}).get("cache/version/summary.json") or {}
    etag = (rec.get("texts") or {}).get("cache/etag.json") or {}
    clm = (rec.get("texts") or {}).get("cache/last_modified.json") or {}
    cstable = (rec.get("texts") or {}).get("cache/version/stable.json") or {}
    st_size, st_mtime = _split_meta(cstable.get("meta") or "")
    return {
        "status": rec.get("status"), "error": rec.get("error") or "", "running": bool(rec.get("running")),
        "program": {"exe_fv": exe_fv, "exe_pv": exe_pv, "dll_fv": dll_fv, "dll_pv": dll_pv,
                    "core_sha": core.get("sha") or "", "core_size": core.get("size"),
                    "core_mtime": core.get("mtime") or "",
                    "updater_mtime": updater.get("mtime") or "",
                    "filelist_size": filelist.get("size"), "filelist_mtime": filelist.get("mtime") or ""},
        "resource": {"tasks_sha": tasks.get("sha") or "", "tasks_size": tasks.get("size"),
                     "tasks_mtime": tasks.get("mtime") or "",
                     "game_version": rvj_obj, "game_updated": rvj_obj.get("last_updated") or "",
                     "cache_summary": csum.get("text") or "",
                     "cache_stable_size": st_size, "cache_stable_mtime": st_mtime,
                     "etag": etag.get("text") or "", "last_modified": clm.get("text") or ""},
        "config": cfg,
    }


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------

def resolve_dirs(include_retired: bool = False, dirs_arg: str | None = None) -> list:
    """默认取 REST 推导的 MAA 目录（失败退 MAA_1..12）；`--all` 追加退役目录。"""
    if dirs_arg:
        out = []
        for d in dirs_arg.split(","):
            d = d.strip()
            if not d:
                continue
            out.append(d if re.match(r"^[A-Za-z]:[\\/]", d) else r"E:\MAA-Multi" + "\\" + d)
        return out
    dirs = list(DEFAULT_DIRS)
    try:
        scripts = od.fetch_scripts()
        seen = []
        for s in scripts.values():
            p = s.get("dir") or ""
            if p and MAA_DIR_RE.match(p) and p.lower() not in [x.lower() for x in seen]:
                seen.append(p)
        if seen:
            dirs = sorted(seen, key=lambda p: int(re.search(r"(\d+)$", p).group(1))
                          if re.search(r"(\d+)$", p) else 0)
    except Exception:  # noqa: BLE001
        pass
    if include_retired:
        for d in RETIRED_DIRS:
            if d.lower() not in [x.lower() for x in dirs]:
                dirs.append(d)
    return dirs


def pull_file(remote: str, local: str) -> bool:
    """base64 拉单个远端文件（只读；二进制安全）。"""
    ps = ("$ErrorActionPreference='Stop'; $b=[IO.File]::ReadAllBytes('%s'); "
          "Write-Output ([Convert]::ToBase64String($b))" % remote.replace("'", "''"))
    try:
        b64 = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
        cmd = od._ssh_base() + ["powershell -NoProfile -NonInteractive -EncodedCommand " + b64]
        r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
        txt = r.stdout.decode("ascii", "ignore").strip()
        if not txt:
            return False
        data = base64.b64decode(txt)
        ensure_dir(os.path.dirname(local))
        with open(local, "wb") as f:
            f.write(data)
        return True
    except Exception:  # noqa: BLE001
        return False


def snapshot(dirs: list[str], out_dir: str | None = None, fetch_tasks: bool = True,
             timeout: int = 300, set_current: bool = True) -> dict:
    """建基线：探针 + 拉原件 + 落盘。只读远端；写只落 runtime/。

    `set_current=False`：verify 的即时快照（不移动「最近基线」指针）。
    """
    st = stamp()
    out = out_dir or os.path.join(GUARD_DIR, st)
    ensure_dir(out)
    ensure_dir(os.path.join(out, "tasks"))
    ensure_dir(os.path.join(out, "gui"))
    recs = probe_files(dirs, timeout=timeout)
    slots, task_shas, gui_saved = {}, {}, []
    for d in dirs:
        name = os.path.basename(d)
        rec = recs.get(name) or {"name": name, "path": d, "status": "ERROR", "error": "no-record"}
        gui_local = os.path.join(out, "gui", "%s.gui.new.json" % name)
        if rec.get("status") == "OK" and pull_file(os.path.join(d, r"config\gui.new.json"), gui_local):
            gui_saved.append(gui_local)
        cfg = config_facts(_read_gui_local(gui_local))
        ident = identity(rec, cfg)
        ident["retired"] = d.lower() in [x.lower() for x in RETIRED_DIRS]
        slots[name] = ident
        sha = ident["resource"]["tasks_sha"]
        if sha:
            task_shas.setdefault(sha, d)
    pulled_tasks = {}
    if fetch_tasks:
        for sha, d in task_shas.items():
            p = os.path.join(out, "tasks", sha + ".json")
            if os.path.exists(p) or pull_file(os.path.join(d, r"resource\tasks\tasks.json"), p):
                pulled_tasks[sha] = p
    try:
        gv = gate.run_gate(dirs=dirs, timeout=max(timeout, 240))
    except Exception as e:  # noqa: BLE001
        gv = {"switch_gate": "UNKNOWN:gate-exception(%s)" % type(e).__name__, "slots": [],
              "violations": [], "unknown": [], "rest": {"state": "down", "error": str(e)[:160]}}
    base = {
        "tool": "maa_upgrade_check", "version": 1, "kind": "baseline",
        "stamp": st, "generated_at": now_str(), "host": od.SSH_HOST, "api": od.API,
        "dir": out, "dirs": dirs, "slots": slots,
        "tasks_files": {sha: os.path.relpath(p, GUARD_DIR).replace("\\", "/") for sha, p in pulled_tasks.items()},
        "switch_gate": {"verdict": gv.get("switch_gate"), "violations": gv.get("violations"),
                        "unknown": gv.get("unknown"), "rest": gv.get("rest"),
                        "slots": [{"name": s.get("name"), "verdict": s.get("verdict"),
                                   "issues": s.get("issues"), "server": s.get("server"),
                                   "account": s.get("account"), "switch": s.get("switch")}
                                  for s in (gv.get("slots") or [])]},
        "watch": {"task_types": WATCH_TASK_TYPES, "fields": WATCH_FIELDS},
        "gui_files": [os.path.basename(p) for p in gui_saved],
    }
    write_json(os.path.join(out, "baseline.json"), base)
    if set_current:
        write_json(CURRENT, {"stamp": st, "dir": out, "path": os.path.join(out, "baseline.json")})
    log_line({"kind": "snapshot", "stamp": st, "dir": out, "dirs": len(dirs),
              "slots_ok": sum(1 for s in slots.values() if s.get("status") == "OK"),
              "tasks_pulled": len(pulled_tasks), "gate": base["switch_gate"]["verdict"]})
    return base


# ---------------------------------------------------------------------------
# 差异（纯函数：selftest 用同一份逻辑）
# ---------------------------------------------------------------------------

def identify_versions(base: dict) -> dict:
    out = {}
    for name, s in (base.get("slots") or {}).items():
        p, r = (s.get("program") or {}), (s.get("resource") or {})
        out[name] = {"program": (p.get("dll_pv") or p.get("exe_pv") or "?"),
                     "core_sha": p.get("core_sha") or "", "tasks_sha": r.get("tasks_sha") or ""}
    return out


def load_tasks_json(base: dict, sha: str) -> dict | None:
    rel = (base.get("tasks_files") or {}).get(sha)
    if not rel:
        return None
    p = rel if os.path.isabs(rel) else os.path.join(GUARD_DIR, rel)
    try:
        with io.open(p, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def diff_tasks(a: dict | None, b: dict | None, limit: int = 12) -> dict:
    """tasks.json 节点级差异：新增/删除节点 + 同名节点字段集变化 + 关注节点存亡。"""
    out = {"available": bool(a and b), "added": [], "removed": [],
           "field_changes": [], "watch_nodes": [], "counts": {}}
    for k in WATCH_NODES:
        sa = "present" if (a or {}).get(k) is not None else "absent"
        sb = "present" if (b or {}).get(k) is not None else "absent"
        out["watch_nodes"].append({"node": k, "before": sa, "after": sb, "changed": sa != sb})
    if not out["available"]:
        return out
    ka, kb = set(a.keys()), set(b.keys())
    out["counts"] = {"before": len(ka), "after": len(kb), "added": len(kb - ka), "removed": len(ka - kb)}
    out["added"] = sorted(kb - ka)[:limit]
    out["removed"] = sorted(ka - kb)[:limit]
    out["added_more"] = max(0, len(kb - ka) - limit)
    out["removed_more"] = max(0, len(ka - kb) - limit)
    fld = []
    for k in sorted(ka & kb):
        if not isinstance(a.get(k), dict) or not isinstance(b.get(k), dict):
            continue
        sa, sb = set(a[k].keys()), set(b[k].keys())
        if sa ^ sb:
            fld.append({"node": k, "added": sorted(sb - sa), "removed": sorted(sa - sb)})
    out["field_changes"] = fld[:limit]
    out["field_changes_more"] = max(0, len(fld) - limit)
    # AUTO-MAS 自身的 per-run 注入（app/task/MAA/tools/bilibili.py::agree_bilibili）：
    # B 服脚本开跑时往 tasks.json 插 BilibiliAgreement_AUTO 节点、收尾时删——于是 B 服槽位的
    # tasks.json 哈希随「是否有 B 服在跑」振荡。若差异**仅**此一节点 ⇒ 记为 benign（非升级漂移）。
    # 实机样本：20260928_101942 基线里 MAA_9（正在跑 B 服 b-3）与其余 11 槽对不上，就是它。
    _benign_pool = {"BilibiliAgreement_AUTO"}
    benign: list = []
    if (out["added"] or out["removed"]) and not out["added_more"] and not out["removed_more"] and not fld:
        if set(out["added"]) <= _benign_pool and set(out["removed"]) <= _benign_pool:
            benign = sorted(set(out["added"]) | set(out["removed"]))
    out["benign"] = benign
    return out


def diff_slots(base: dict, cur: dict) -> dict:
    """逐槽对比：程序/资源/注入面/观察键，并按「我们依赖的链」定性。"""
    res = {"slots": [], "program_changed": [], "resource_changed": [],
           "schema_drift": [], "injection_issues": [], "unknown": [], "notes": []}
    b_slots, c_slots = (base.get("slots") or {}), (cur.get("slots") or {})
    gate_map = {s.get("name"): s for s in ((cur.get("switch_gate") or {}).get("slots") or [])}
    for name in sorted(set(b_slots) | set(c_slots)):
        b, c = b_slots.get(name) or {}, c_slots.get(name) or {}
        row = {"name": name, "status": c.get("status"), "running": bool(c.get("running")),
               "changes": [], "verdict": "OK"}
        if not b:
            row["changes"].append("基线无此槽（新增目录）")
        if c.get("status") != "OK":
            row["verdict"] = "UNKNOWN"
            res["unknown"].append("%s(%s)" % (name, c.get("error") or c.get("status")))
            res["slots"].append(row)
            continue
        bp, cp = (b.get("program") or {}), (c.get("program") or {})
        if (bp.get("dll_pv"), bp.get("core_sha")) != (cp.get("dll_pv"), cp.get("core_sha")):
            row["changes"].append("程序: %s/%s → %s/%s" % (
                bp.get("dll_pv") or "?", (bp.get("core_sha") or "?")[:8],
                cp.get("dll_pv") or "?", (cp.get("core_sha") or "?")[:8]))
            res["program_changed"].append(name)
        br, cr = (b.get("resource") or {}), (c.get("resource") or {})
        if br.get("tasks_sha") != cr.get("tasks_sha"):
            row["changes"].append("资源 tasks.json: %s → %s (size %s→%s)" % (
                (br.get("tasks_sha") or "?")[:8], (cr.get("tasks_sha") or "?")[:8],
                br.get("tasks_size"), cr.get("tasks_size")))
            res["resource_changed"].append(name)
        if (br.get("game_updated") or "") != (cr.get("game_updated") or ""):
            row["changes"].append("游戏资源版本: %s → %s" % (br.get("game_updated") or "-",
                                                          cr.get("game_updated") or "-"))
        bcfg, ccfg = (b.get("config") or {}), (c.get("config") or {})
        if bcfg.get("parsed") and not ccfg.get("parsed"):
            res["unknown"].append("%s(config-unparsable)" % name)
            row["verdict"] = "UNKNOWN"
            res["slots"].append(row)
            continue
        bsch, csch = (bcfg.get("schemas") or {}), (ccfg.get("schemas") or {})
        btypes, ctypes = set(bcfg.get("types") or []), set(ccfg.get("types") or [])
        for ty in sorted(btypes - ctypes):
            res["schema_drift"].append("%s: 任务类型 %s 消失（基线有/现在无）" % (name, ty))
            row["changes"].append("任务类型消失: %s" % ty)
        for ty, fields in WATCH_FIELDS.items():
            if ty not in bsch:
                continue
            now = csch.get(ty)
            miss = [f for f in fields if (now is None or f not in now)]
            if miss:
                res["schema_drift"].append("%s: %s 缺字段 %s（基线有）" % (name, ty, ",".join(miss)))
                row["changes"].append("%s 缺字段 %s" % (ty, ",".join(miss)))
        bi, ci = (bcfg.get("inject") or {}), (ccfg.get("inject") or {})
        bs, cs = bi.get("startup"), ci.get("startup")
        if bs and cs and (bs.get("accountName"), bs.get("switchEnabled")) != (cs.get("accountName"), cs.get("switchEnabled")):
            row["changes"].append("StartUp 注入: %s/%s → %s/%s" % (
                _val(bs.get("accountName")), _val(bs.get("switchEnabled")),
                _val(cs.get("accountName")), _val(cs.get("switchEnabled"))))
        for i, (bf, cf) in enumerate(zip(bi.get("fight") or [], ci.get("fight") or [])):
            keys = ["stagePlanText", "series", "medicineCount", "useMedicine", "enableTimesLimit",
                    "enableTargetDrop"]
            diff = {k: (bf.get(k), cf.get(k)) for k in keys if bf.get(k) != cf.get(k)}
            if diff:
                row["changes"].append("战斗#%d 注入变化: %s" % (
                    i, ", ".join("%s %s→%s" % (k, _val(v[0]), _val(v[1])) for k, v in diff.items())))
        bw, cw = (bcfg.get("watch") or {}), (ccfg.get("watch") or {})
        for grp in ("StartUpSettings", "ConnectSettings", "RuntimeSettings", "Update", "ConfigVersion"):
            bg, cg = (bw.get(grp) or {}), (cw.get(grp) or {})
            for k in sorted(set(bg) | set(cg)):
                if _val(bg.get(k)) != _val(cg.get(k)):
                    row["changes"].append("%s.%s: %s → %s" % (grp, k, _val(bg.get(k)), _val(cg.get(k))))
        g = gate_map.get(name) or {}
        retired = bool(c.get("retired") or b.get("retired"))
        row["retired"] = retired
        if g:
            row["gate"] = g.get("verdict")
            if g.get("verdict") == "VIOLATION":
                msg = "%s: %s" % (name, "; ".join(g.get("issues") or []))
                if retired:
                    # 退役目录（MAA_13/14/25/28）不进出带，违规只记观察项，不翻转判定
                    res["notes"].append("退役目录 %s（不进产线，无需处置）" % msg)
                else:
                    res["injection_issues"].append(msg)
                    row["verdict"] = "VIOLATION"
            elif g.get("verdict") == "UNKNOWN":
                if retired:
                    res["notes"].append("退役目录 %s 闸门 UNKNOWN" % name)
                else:
                    res["unknown"].append("%s(gate-unknown)" % name)
                    row["verdict"] = "UNKNOWN"
        if row["verdict"] != "VIOLATION" and row["changes"]:
            row["verdict"] = "CHANGED"
        res["slots"].append(row)
    return res


def compare(baseline: dict, current: dict) -> dict:
    """基线 vs 现状 → 结构化差异 + 判定（纯计算 + 读 tasks 原件）。"""
    diff = diff_slots(baseline, current)
    b_ident, c_ident = identify_versions(baseline), identify_versions(current)
    pairs = {}
    for name in sorted(c_ident):
        b, c = b_ident.get(name) or {}, c_ident.get(name) or {}
        if b.get("tasks_sha") != c.get("tasks_sha"):
            key = (b.get("tasks_sha") or "", c.get("tasks_sha") or "")
            if key not in pairs:
                a = load_tasks_json(baseline, key[0]) if key[0] else None
                bb = load_tasks_json(current, key[1]) if key[1] else None
                pairs[key] = {"slots": [], "diff": diff_tasks(a, bb)}
            pairs[key]["slots"].append(name)
    diff["tasks_diff"] = [{"from": k[0][:8] or "-", "to": k[1][:8] or "-",
                           "slots": v["slots"], **v["diff"]} for k, v in pairs.items()]
    if diff["injection_issues"]:
        diff["verdict"] = "VIOLATION"
    elif diff["schema_drift"]:
        diff["verdict"] = "DRIFT"
    elif diff["unknown"]:
        diff["verdict"] = "UNKNOWN"
    else:
        diff["verdict"] = "OK"
    diff["program_changed"] = sorted(set(diff["program_changed"]))
    diff["resource_changed"] = sorted(set(diff["resource_changed"]))
    diff["upgrade_seen"] = bool(diff["program_changed"])
    diff["resource_moved"] = bool(diff["resource_changed"])
    return diff


def render_report(baseline: dict, current: dict, diff: dict) -> str:
    L = []
    A = L.append
    A("# MAA 升级校验报告")
    A("")
    A("- 生成：%s" % now_str())
    A("- 基线：%s（%s）" % (baseline.get("stamp"), baseline.get("generated_at")))
    A("- 现状：%s" % current.get("generated_at"))
    A("- 判定：**%s**" % diff.get("verdict"))
    A("- 程序变化槽位：%s" % (", ".join(diff.get("program_changed")) or "无"))
    A("- 资源变化槽位：%s" % (", ".join(diff.get("resource_changed")) or "无"))
    A("")
    A("## 一、升级是否发生")
    A("")
    if diff.get("upgrade_seen"):
        A("程序本体发生变化（MAA.dll ProductVersion / MaaCore.dll 哈希）——本次校验即一次真升级后的复检：")
        for s in diff["slots"]:
            if s["name"] in diff["program_changed"]:
                A("- `%s`: %s" % (s["name"], "; ".join(s["changes"]) or "（仅程序变化）"))
    else:
        A("程序本体未变（无升级事件）；资源/注入面的变化在下面单列。")
    A("")
    A("## 二、tasks.json 节点级差异")
    A("")
    if not diff.get("tasks_diff"):
        A("无（各槽资源同一版本）")
    for td in diff.get("tasks_diff") or []:
        A("### %s → %s（槽位：%s）" % (td.get("from"), td.get("to"), ", ".join(td.get("slots") or [])))
        if not td.get("available"):
            A("- ⚠️ tasks.json 原件缺失（未拉取），无法做节点级 diff")
        else:
            c = td.get("counts") or {}
            A("- 节点数 %s → %s；新增 %s、删除 %s" % (c.get("before"), c.get("after"),
                                                  c.get("added"), c.get("removed")))
            if td.get("added"):
                A("- 新增节点：%s%s" % (", ".join("`%s`" % x for x in td["added"]),
                                     (" …(+%d)" % td["added_more"]) if td.get("added_more") else ""))
            if td.get("removed"):
                A("- **删除节点**：%s%s" % (", ".join("`%s`" % x for x in td["removed"]),
                                        (" …(+%d)" % td["removed_more"]) if td.get("removed_more") else ""))
            if td.get("benign"):
                A("- ℹ️ 上述差异仅为 `%s`（AUTO-MAS per-run 注入：B 服脚本 `agree_bilibili` 开跑增/收尾删该节点，"
                  "随「是否有 B 服在跑」振荡）——**非升级漂移**，按 §3 处置表 D2 忽略" % ", ".join(td["benign"]))
            if td.get("field_changes"):
                A("- 字段集变化节点：")
                for f in td["field_changes"]:
                    A("  - `%s`: +%s -%s" % (f["node"], f.get("added"), f.get("removed")))
            watch = [w for w in (td.get("watch_nodes") or []) if w.get("changed")]
            if watch:
                A("- ⚠️ 关注节点存亡变化：%s" % ", ".join("%s(%s→%s)" % (w["node"], w["before"], w["after"])
                                                     for w in watch))
    A("")
    A("## 三、逐槽差异")
    A("")
    A("| 槽位 | 判定 | 变化 |")
    A("|---|---|---|")
    for s in diff["slots"]:
        A("| %s | %s | %s |" % (s["name"], s.get("verdict") or "-", "<br>".join(s["changes"]) or "—"))
    A("")
    if diff.get("schema_drift"):
        A("## ⚠️ 四、schema 漂移（我们注入的字段/类型不见了）")
        A("")
        for x in diff["schema_drift"]:
            A("- %s" % x)
        A("")
    if diff.get("injection_issues"):
        A("## ⚠️ 五、注入闸门违规（切号口径）")
        A("")
        for x in diff["injection_issues"]:
            A("- %s" % x)
        A("")
    if diff.get("unknown"):
        A("## 六、未知（fail-closed，不当通过）")
        A("")
        for x in diff["unknown"]:
            A("- %s" % x)
        A("")
    if diff.get("notes"):
        A("## 六之二、观察项（不影响判定）")
        A("")
        for x in diff["notes"]:
            A("- %s" % x)
        A("")
    A("## 七、处置建议")
    A("")
    if diff.get("verdict") == "OK":
        A("- 无漂移：不需要动作，下一次班次照常；建议把本报告路径写进当日复盘。")
    else:
        A("- 按 `docs/MAA_UPGRADE_GUARD.md` §3『漂移处置表』逐条确认；升级后首次校验建议加跑一次 live 注入验证：")
        A("  `python tools/maa_upgrade_check.py live --script <目标脚本> --apply`")
    A("- 切号注入闸门：`%s`" % (current.get("switch_gate") or {}).get("verdict"))
    A("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# live：注入 → 读回（真发车，默认 dry-run）
# ---------------------------------------------------------------------------

_SHIFT_WINDOWS = [(5 * 60, 8 * 60), (19 * 60 + 30, 23 * 60)]


def in_shift_window(now: dt.datetime | None = None) -> tuple:
    n = now or dt.datetime.now()
    m = n.hour * 60 + n.minute
    for a, b in _SHIFT_WINDOWS:
        if a <= m < b:
            return True, "%02d:%02d" % (a // 60, a % 60)
    return False, ""


def _resolve_script(token: str) -> dict | None:
    """脚本 token → {sid,name,dir,users}（复用 mas_ondemand 的解析口径）。"""
    try:
        t = od.resolve_script(token)
    except Exception:  # noqa: BLE001
        return None
    if not t or not t.get("sid"):
        return None
    users = []
    try:
        users = od.fetch_users(t["sid"])
    except Exception:  # noqa: BLE001
        pass
    return {"sid": t["sid"], "name": t.get("name"), "dir": t.get("dir"), "users": users}


def expect_masks(users: list | None) -> list:
    """官服用户的期望掩码集合（次序保留、去重）。

    口径与 tools/mas_switch_gate.py::rest_slots 一致：只认 11 位纯数字手机号（PHONE_RE）；
    非 11 位官服 id 不产掩码（那种异常由闸门的 official-id-not-11-digits 备注兜住）。
    """
    out: list = []
    for u in users or []:
        idv = str(u.get("id") or "")
        if str(u.get("server")) == "Official" and gate.PHONE_RE.fullmatch(idv):
            m = gate.mask_phone(idv)
            if m not in out:
                out.append(m)
    return out


def read_slot_config(dir_: str, timeout: int = 180) -> dict:
    """读单个槽的注入面（拉 gui.new.json 原件到本地临时目录解析；只读）。"""
    tmp = ensure_dir(os.path.join(GUARD_DIR, "_live_tmp"))
    dst = os.path.join(tmp, "%s.gui.new.json" % os.path.basename(dir_))
    ok = pull_file(os.path.join(dir_, r"config\gui.new.json"), dst)
    return {"ok": ok, "config": config_facts(_read_gui_local(dst) if ok else None), "local": dst if ok else ""}


def live_check(script_token: str, apply_: bool = False, timeout: int = 240, poll: int = 15,
               force_window: bool = False) -> dict:
    """注入 → 读回验证：发车 → 轮询 gui.new.json → 断言 → stop（走官方 stop 才还原配置）。

    断言（口径与 mas_switch_gate / mas_run_profile 一致）：
      * StartUpTask：官服 ⇒ 掩码 + AccountSwitchEnabled=true；非官 ⇒ "" + false
      * FightTask：StagePlan 非空（关卡计划真的注入进去了）
      * 官服：gui.log 出现 `账号切换 -->> <掩码>`（**切号权威信号**，证明 MAA 真读了字段）
    """
    out = {"tool": "maa_upgrade_check", "kind": "live", "generated_at": now_str(),
           "script": None, "dir": None, "dry_run": not apply_, "gates": [], "steps": [],
           "verdict": "UNKNOWN", "task_uid": None}
    blocker = no_go()
    if blocker:
        out["gates"].append({"gate": "NO_GO", "ok": False, "detail": "%s=1" % blocker})
        out["verdict"] = "BLOCKED"
        return out
    ok_rest, detail = od.reachable()
    out["gates"].append({"gate": "REST", "ok": bool(ok_rest), "detail": detail})
    if not ok_rest:
        out["verdict"] = "BLOCKED"
        return out
    sc = _resolve_script(script_token)
    out["script"] = {k: v for k, v in (sc or {}).items() if k != "users"} or None
    if not sc:
        out["gates"].append({"gate": "TARGET", "ok": False, "detail": "脚本解析不到：%s" % script_token})
        out["verdict"] = "BLOCKED"
        return out
    dir_ = sc.get("dir") or ""
    out["dir"] = dir_
    if not dir_ or not MAA_DIR_RE.match(dir_):
        out["gates"].append({"gate": "TARGET", "ok": False, "detail": "目录非法：%s" % dir_})
        out["verdict"] = "BLOCKED"
        return out
    is_win, since = in_shift_window()
    out["gates"].append({"gate": "WINDOW", "ok": not is_win,
                         "detail": ("班次窗口内（%s 起）" % since) if is_win else "窗口外"})
    if not apply_:
        out["steps"].append("dry-run：将发车 AutoProxy(script=%s) → 轮询 %s\\config\\gui.new.json → 断言 → stop"
                            % (str(sc["sid"])[:8], dir_))
        out["verdict"] = "DRY-RUN"
        return out
    if is_win and not force_window:
        out["verdict"] = "BLOCKED"
        return out
    pre = read_slot_config(dir_)
    if not pre.get("ok") or not (pre.get("config") or {}).get("parsed"):
        out["gates"].append({"gate": "PROBE", "ok": False, "detail": "槽位 gui.new.json 读不到/解析不了"})
        out["verdict"] = "BLOCKED"
        return out
    recs = probe_files([dir_], timeout=180)
    rec = recs.get(os.path.basename(dir_)) or {}
    if rec.get("status") != "OK":
        out["gates"].append({"gate": "PROBE", "ok": False, "detail": "槽位探针失败：%s" % rec.get("error")})
        out["verdict"] = "BLOCKED"
        return out
    if rec.get("running"):
        out["gates"].append({"gate": "IDLE", "ok": False, "detail": "该目录有 MAA.exe 在跑，换空闲槽"})
        out["verdict"] = "BLOCKED"
        return out
    out["gates"].append({"gate": "IDLE", "ok": True, "detail": "无 MAA.exe"})
    pcfg = pre["config"]
    out["before"] = {"startup": (pcfg.get("inject") or {}).get("startup"),
                     "fight": (pcfg.get("inject") or {}).get("fight")}
    lock_fd = _acquire_lock()
    if lock_fd is None:
        out["gates"].append({"gate": "LOCK", "ok": False, "detail": "已有 live 校验在跑（%s）" % LOCK})
        out["verdict"] = "BLOCKED"
        return out
    try:
        # 发车前快照 gui.log 里已有的「账号切换」行：本轮只认新增行，防陈旧行冒充（假 OK）
        pre_switch = _switch_lines(dir_, tail=4000)
        out["switch_lines_pre"] = len(pre_switch)
        resp = od.post("/api/dispatch/start", {"taskId": sc["sid"], "mode": "AutoProxy"})
        res = od._upstream_result(resp)
        if not res.get("ok"):
            out["gates"].append({"gate": "DISPATCH", "ok": False,
                                 "detail": json.dumps(res, ensure_ascii=False)[:300]})
            out["verdict"] = "BLOCKED"
            return out
        tid = res["taskId"]
        out["task_uid"] = tid
        out["steps"].append("已发车：task_uid=%s" % str(tid)[:8])
        want_masks = expect_masks(sc.get("users"))
        out["expect_masks"] = want_masks
        deadline = time.time() + max(60, timeout)
        seen = None
        while time.time() < deadline:
            time.sleep(max(5, poll))
            cur = read_slot_config(dir_)
            if not cur.get("ok"):
                continue
            inj = (cur["config"].get("inject") or {})
            st, ft = inj.get("startup") or {}, inj.get("fight") or []
            stage_ok = any(str(f.get("stagePlanText") or f.get("stagePlan") or "").strip() not in ("", "-")
                           for f in ft)
            if "switchEnabled" in (st or {}):
                if want_masks:
                    mask_ok = str(st.get("accountName")) in want_masks and st.get("switchEnabled") is True
                else:
                    mask_ok = (not st.get("accountName")) and st.get("switchEnabled") is False
            else:
                mask_ok = False
            if stage_ok and mask_ok:
                seen = {"startup": st, "fight": ft}
                break
        out["readback"] = seen
        if not seen:
            out["steps"].append("轮询 %ds 未读到期望注入（StartUp 掩码/开关 + Fight 关卡）" % timeout)
        else:
            out["steps"].append("读回成功：StartUp=%s/%s，Fight#0 stagePlan=%s" % (
                _val(seen["startup"].get("accountName")), _val(seen["startup"].get("switchEnabled")),
                _val((seen["fight"] or [{}])[0].get("stagePlan"))))
        if want_masks:
            line = _find_switch_line(dir_, tail=4000, exclude=pre_switch)
            out["switch_line"] = line
            if line:
                out["steps"].append("gui.log 切号信号：%s" % line[-90:])
            else:
                out["steps"].append("gui.log 未见『账号切换 -->>』新增行（可能还在启动，或切号真失效）")
        verdict = "OK"
        if not seen:
            verdict = "FAIL"
        elif want_masks and not out.get("switch_line"):
            verdict = "PARTIAL"
        stop_resp = od.post("/api/dispatch/stop", {"taskId": tid})
        sres = od._upstream_result(stop_resp)
        stop_ok, stop_why = _stop_result(stop_resp)
        out["stop"] = {"resp": stop_resp if isinstance(stop_resp, dict) else str(stop_resp)[:200],
                       "parsed": sres, "ok": stop_ok, "reason": stop_why}
        out["steps"].append("已 stop：%s（判定 %s%s）" % (json.dumps(stop_resp, ensure_ascii=False)[:160],
                                                        "OK" if stop_ok else "FAIL",
                                                        ("；%s" % stop_why) if stop_why else ""))
        if not stop_ok:
            verdict = "STOP-FAILED"
        out["verdict"] = verdict
        return out
    finally:
        _release_lock(lock_fd)
        log_line({"kind": "live", "script": (sc or {}).get("name"), "dir": dir_,
                  "verdict": out.get("verdict"), "task_uid": out.get("task_uid")})


def _stop_result(resp, parsed: dict | None = None) -> tuple:
    """stop 应答判定 —— **不能拿 start 口径判**：stop 的 200 应答不带 taskId（start 才带），
    `_upstream_result` 会把「code=200, message=操作成功」误判成 no_taskid 失败（实机假阴性，2026-09-28 踩过）。

    口径（与 tools/mas_ondemand.py::cmd_stop 一致）：code==200 ⇒ 成功；应答含「已结束」也当成功。
    """
    if not isinstance(resp, dict):
        return False, "bad_response"
    if resp.get("code") == 200:
        return True, ""
    msg = json.dumps(resp, ensure_ascii=False)
    if "已结束" in msg:
        return True, "already-finished"
    return False, "code=%s msg=%s" % (resp.get("code"), str(resp.get("message") or "")[:120])


def _switch_lines(dir_: str, tail: int = 4000, timeout: int = 120) -> list:
    """在 gui.log 尾部取所有含 `账号切换` 的行（旧→新）。失败返回 []（调用方自行降级）。"""
    ps = _PS_HEAD + ("$log='%s'\n"
                     "if(Test-Path -LiteralPath $log){"
                     "Get-Content -LiteralPath $log -Tail %d -Encoding UTF8 -EA SilentlyContinue |"
                     "Select-String -Pattern '账号切换' |"
                     "ForEach-Object { Write-Output ('SW|' + $_.Line.Trim()) }}\n"
                     "Write-Output 'SW-END'\n"
                     % (os.path.join(dir_, r"debug\gui.log").replace("'", "''"), int(tail)))
    try:
        out = _ssh_script(ps, timeout=timeout)
    except Exception:  # noqa: BLE001
        return []
    return [ln.strip()[3:] for ln in out.splitlines() if ln.strip().startswith("SW|")]


def _find_switch_line(dir_: str, tail: int = 4000, exclude=None, timeout: int = 120) -> str | None:
    """在 gui.log 尾部找 `账号切换 -->> <掩码>`（切号权威信号），跳过 exclude 里的陈旧行。

    exclude：发车前已存在的行集合（live_check 传入）——只认本轮新增，防旧行冒充。
    """
    lines = _switch_lines(dir_, tail, timeout)
    ex = set(exclude or [])
    for ln in reversed(lines):
        if ln not in ex:
            return ln
    return None


def _acquire_lock():
    try:
        ensure_dir(os.path.dirname(LOCK))
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, ("%s pid=%d\n" % (now_str(), os.getpid())).encode("utf-8"))
        return fd
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(LOCK) > 3600:
                os.unlink(LOCK)
                return _acquire_lock()
        except OSError:
            pass
        return None


def _release_lock(fd) -> None:
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(LOCK)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

def _render_slots_table(base: dict) -> list:
    lines = ["%-9s %-30s %-9s %-9s %s" % ("槽位", "程序(MAA.dll)", "核sha", "资源sha", "注入 StartUp")]
    for name, s in sorted((base.get("slots") or {}).items()):
        p, r = (s.get("program") or {}), (s.get("resource") or {})
        st = ((s.get("config") or {}).get("inject") or {}).get("startup") or {}
        lines.append("%-9s %-30s %-9s %-9s %s/%s%s" % (
            name, (p.get("dll_pv") or "?")[:30], (p.get("core_sha") or "?")[:8],
            (r.get("tasks_sha") or "?")[:8], _val(st.get("accountName")), _val(st.get("switchEnabled")),
            "  (running)" if s.get("running") else ""))
    return lines


def cmd_show(as_json: bool = False) -> int:
    cur = read_json(CURRENT)
    if not cur:
        print("还没有基线：先跑 `python tools/maa_upgrade_check.py snapshot`")
        return 2
    base = read_json(cur.get("path") or "")
    if not base:
        print("基线文件缺失：%s" % cur.get("path"))
        return 2
    if as_json:
        print(json.dumps(base, ensure_ascii=False, indent=1))
        return 0
    print("最近基线：%s（%s）" % (base.get("stamp"), base.get("generated_at")))
    print("目录：%s" % base.get("dir"))
    for ln in _render_slots_table(base):
        print(ln)
    print("tasks.json 原件：%d 份" % len(base.get("tasks_files") or {}))
    print("切号注入闸门：%s" % ((base.get("switch_gate") or {}).get("verdict")))
    return 0


# ---------------------------------------------------------------------------
# 自检（离线；假传输）
# ---------------------------------------------------------------------------

def _fixture(program="v6.18.0+aaa", core="core1234", tasks="taskabcd",
             mask="134****5276", sw=True, stage="1-7", types=None, drop_field=False):
    types = types or list(WATCH_FIELDS.keys())
    schemas = {t: sorted(set(["$type", "Name", "TaskType", "IsEnable"] + list(WATCH_FIELDS.get(t, []))))
               for t in types}
    if drop_field:
        schemas["StartUpTask"] = [f for f in schemas["StartUpTask"]
                                  if f not in ("AccountName", "AccountSwitchEnabled")]
    return {
        "generated_at": "2026-09-28 09:00:00",
        "slots": {"MAA_1": {
            "status": "OK", "running": False,
            "program": {"dll_pv": program, "core_sha": core, "exe_pv": program.split("+")[0],
                        "core_size": 5902336, "core_mtime": "2026-09-23 21:17:12"},
            "resource": {"tasks_sha": tasks, "tasks_size": 255010,
                         "game_updated": "2026-09-20 12:47:33.000"},
            "config": {"parsed": True, "types": types, "schemas": schemas,
                       "inject": {"startup": {"accountName": mask, "switchEnabled": sw, "isEnable": True},
                                  "fight": [{"stagePlan": stage, "isEnable": True, "series": 1,
                                             "medicineCount": 0, "useMedicine": False,
                                             "enableTimesLimit": False, "enableTargetDrop": False}]},
                       "watch": {"StartUpSettings": {"RunDirectly": True, "SkipStartupAutoRunAfterUpdate": True,
                                                     "StartEmulator": False},
                                 "ConnectSettings": {"AutoDetect": False, "Address": "127.0.0.1:16736"},
                                 "RuntimeSettings": {"ClientType": "0", "StartGame": True},
                                 "Update": {"AutoDownloadUpdatePackage": True, "AutoInstallUpdatePackage": False}}},
        }},
        "tasks_files": {},
        "switch_gate": {"verdict": "OK", "slots": [], "violations": [], "unknown": [], "rest": {"state": "ok"}},
    }


def _gate_verdict(v, slots=None):
    return {"verdict": v, "slots": slots or [], "violations": [], "unknown": [], "rest": {"state": "ok"}}


def selftest() -> int:
    bad = 0
    total = 0

    def ck(label, cond, extra=""):
        nonlocal bad, total
        total += 1
        if not cond:
            bad += 1
        print("%s %s %s" % ("PASS" if cond else "FAIL", label, extra))

    b = _fixture()

    # 1) 无变化 ⇒ OK
    ck("no-change-OK", compare(b, _fixture())["verdict"] == "OK")

    # 2) 程序升级 ⇒ OK 但点名升级事件
    d = compare(b, _fixture(program="v6.19.0+bbb", core="core9999"))
    ck("program-upgrade-detected", d["upgrade_seen"] and d["verdict"] == "OK"
       and d["program_changed"] == ["MAA_1"], json.dumps(d["program_changed"]))

    # 3) 资源 OTA ⇒ 记录资源变化
    d = compare(b, _fixture(tasks="taskzzzz"))
    ck("resource-move-detected", d["resource_moved"] and d["verdict"] == "OK", json.dumps(d["resource_changed"]))

    # 4) 切号字段被新版删掉（schema 迁移复现）⇒ DRIFT
    d = compare(b, _fixture(drop_field=True))
    ck("schema-migration-caught", d["verdict"] == "DRIFT"
       and any("StartUpTask 缺字段" in x for x in d["schema_drift"]), "; ".join(d["schema_drift"])[:110])

    # 5) 闸门违规（切号口径破）⇒ VIOLATION
    c = _fixture(mask="某人", sw=True)
    c["switch_gate"] = _gate_verdict("VIOLATION:MAA_1", [{"name": "MAA_1", "verdict": "VIOLATION",
                                                          "issues": ["nonofficial-switch-on"]}])
    ck("gate-violation-caught", compare(b, c)["verdict"] == "VIOLATION")

    # 6) 读不到 ⇒ UNKNOWN（fail-closed）
    c = _fixture()
    c["slots"]["MAA_1"] = {"status": "ERROR", "error": "ssh-timeout"}
    ck("unknown-fail-closed", compare(b, c)["verdict"] == "UNKNOWN")

    # 7) 注入取值变化（升级后 MAA 把 StartUp 重置）⇒ 记 CHANGED
    d = compare(b, _fixture(mask="", sw=False))
    ck("inject-value-change-logged", any("StartUp 注入" in ch for s in d["slots"] for ch in s["changes"]),
       json.dumps([s["changes"] for s in d["slots"]], ensure_ascii=False)[:100])

    # 8) 观察键变化（更新策略被改）⇒ 记录
    c = _fixture()
    c["slots"]["MAA_1"]["config"]["watch"]["Update"] = {"AutoDownloadUpdatePackage": False,
                                                        "AutoInstallUpdatePackage": True}
    d = compare(b, c)
    ck("watch-setting-change-logged",
       any("Update.AutoInstallUpdatePackage" in ch for s in d["slots"] for ch in s["changes"]))

    # 9) 任务类型消失 ⇒ schema drift
    d = compare(b, _fixture(types=[t for t in WATCH_FIELDS if t != "RoguelikeTask"]))
    ck("task-type-vanish-flagged", any("任务类型 RoguelikeTask 消失" in x for x in d["schema_drift"]),
       "; ".join(d["schema_drift"])[:90])

    # 10) tasks.json 节点级 diff
    a = {"StartUp": {"action": "DoNothing", "next": ["a"]}, "Fight": {"action": "ClickSelf"},
         "OldNode": {"action": "DoNothing"}}
    bb = {"StartUp": {"action": "DoNothing", "template": "x.png"}, "Fight": {"action": "ClickSelf"},
          "NewNode": {"action": "DoNothing"}}
    td = diff_tasks(a, bb)
    ck("tasks-diff-added-removed", td["added"] == ["NewNode"] and td["removed"] == ["OldNode"],
       json.dumps(td["counts"]))
    ck("tasks-diff-fields", any(f["node"] == "StartUp" and f["added"] == ["template"] for f in td["field_changes"]),
       json.dumps(td["field_changes"]))
    ck("tasks-diff-watch-node", any(w["node"] == "Fight" and not w["changed"] for w in td["watch_nodes"]))
    bb2 = dict(bb)
    bb2.pop("StartUp")
    ck("tasks-watch-node-removed-flagged",
       any(w["node"] == "StartUp" and w["changed"] for w in diff_tasks(a, bb2)["watch_nodes"]))

    # 11) 窗口判定
    w_in, since = in_shift_window(dt.datetime(2026, 9, 28, 6, 30))
    w_out, _ = in_shift_window(dt.datetime(2026, 9, 28, 12, 0))
    ck("shift-window", w_in and not w_out, "%s/%s" % (w_in, since))

    # 12) 报告渲染
    rep = render_report(b, _fixture(drop_field=True), compare(b, _fixture(drop_field=True)))
    ck("report-render", "MAA 升级校验报告" in rep and "schema 漂移" in rep and len(rep) > 300)

    # 13) base64 往返 + 掩码头 + meta 解析
    ck("b64-roundtrip", from_b64_text(b64_text("逐影集趣 2026-09-20")) == "逐影集趣 2026-09-20")
    ck("reuse-gate-mask", gate.mask_phone("13412345276") == "134****5276")
    ck("meta-parse", _split_meta("255010@20260928075754") == (255010, "2026-09-28 07:57:54"))

    # 14) PS 段长度闸（防「超 8KB 静默空输出」回归）
    ck("ps-chunk-guard", len(_ps_dirs(_PS_A, DEFAULT_DIRS)) < PS_CHUNK_LIMIT
       and len(_ps_dirs(_PS_B, DEFAULT_DIRS)) < PS_CHUNK_LIMIT,
       "A=%d B=%d" % (len(_ps_dirs(_PS_A, DEFAULT_DIRS)), len(_ps_dirs(_PS_B, DEFAULT_DIRS))))

    # 15) A/B 段解析 + 注入面解析（假远端输出）
    out_a = ("A|MAA_1|OK|1|exe=6.18.0#v6.18.0|dll=6.18.0#v6.18.0+abc|"
             "MaaCore.dll=5902336@20260923211712#eff5c522650f9671|"
             "MAA.Updater.exe=339968@20260923210432|filelist.txt=756209@20260923211922|"
             "resource/tasks/tasks.json=255010@20260928075754#38e897cec4611ed5\r\nA-END\r\n")
    out_b = ("B|MAA_1|resource/version.json|221@20260923210244|%s\r\nB-END\r\n"
             % b64_text('{"last_updated":"2026-09-20 12:47:33.000"}'))
    rec = parse_probe_out(out_a, out_b, [r"E:\MAA-Multi\MAA_1"])["MAA_1"]
    ident = identity(rec, config_facts({"Configurations": {"Default": {"TaskQueue": [
        {"$type": "StartUpTask", "AccountName": "134****5276", "AccountSwitchEnabled": True, "IsEnable": True},
        {"$type": "FightTask", "StagePlan": "1-7", "Series": 1, "MedicineCount": 0, "UseMedicine": False}]}},
        "Current": "Default", "ConfigVersion": 1, "Update": {"AutoInstallUpdatePackage": False}}))
    ck("probe-parse", ident["status"] == "OK" and ident["running"] is True
       and ident["program"]["core_sha"] == "eff5c522650f9671"
       and ident["resource"]["tasks_sha"] == "38e897cec4611ed5"
       and ident["resource"]["game_updated"] == "2026-09-20 12:47:33.000",
       json.dumps(ident["program"], ensure_ascii=False)[:110])
    ck("config-facts-parse", ident["config"]["types"] == ["StartUpTask", "FightTask"]
       and ident["config"]["inject"]["startup"]["accountName"] == "134****5276"
       and ident["config"]["inject"]["fight"][0]["stagePlan"] == "1-7"
       and ident["config"]["watch"]["Update"]["AutoInstallUpdatePackage"] is False)

    # 16) 真实跨版本 fixture（放 runtime/upgrade_guard/fixtures 时启用）
    fx = os.path.join(GUARD_DIR, "fixtures")
    if os.path.isdir(fx):
        try:
            old = _read_gui_local(os.path.join(fx, "tasks_6168.json"))
            new = _read_gui_local(os.path.join(fx, "tasks_6180.json"))
            tdr = diff_tasks(old, new)
            ck("real-fixture-diff", tdr["counts"]["before"] < tdr["counts"]["after"],
               json.dumps(tdr["counts"]))
        except Exception as e:  # noqa: BLE001
            ck("real-fixture-diff", False, str(e)[:80])
    else:
        ck("real-fixture-diff", True, "（无 fixtures，跳过：先跑 snapshot --all 生成）")

    # 17) live dry-run 计划 + REST 断线 fail-closed
    orig_reach, orig_resolve = od.reachable, globals()["_resolve_script"]
    try:
        od.reachable = lambda: (True, "fake")
        globals()["_resolve_script"] = lambda tok: {"sid": "sid-x", "name": "假脚本",
                                                    "dir": r"E:\MAA-Multi\MAA_1", "users": []}
        lv = live_check("假脚本", apply_=False)
        ck("live-dryrun-plan", lv["verdict"] == "DRY-RUN" and lv["dir"].endswith("MAA_1"),
           json.dumps(lv.get("gates"), ensure_ascii=False)[:110])
        od.reachable = lambda: (False, "down")
        ck("live-blocked-rest", live_check("假脚本", apply_=True)["verdict"] == "BLOCKED")
    finally:
        od.reachable = orig_reach
        globals()["_resolve_script"] = orig_resolve

    # 18) expect_masks：官服 11 位手机号产掩码；非官/非 11 位/重复不干扰
    em = expect_masks([{"server": "Official", "id": "13412345276"},
                       {"server": "YoStarJP", "id": "13412345276"},
                       {"server": "Official", "id": "12345"},
                       {"server": "Official", "id": ""},
                       {"server": "Official", "id": "13412345276"}])
    ck("expect-masks-official-11digits", em == ["134****5276"], json.dumps(em))
    ck("expect-masks-none", expect_masks([{"server": "Bilibili", "id": "x"}, {}]) == [])
    ck("expect-masks-id-from-fetch-users",
       expect_masks([dict(u, server="Official") for u in
                     [{"id": "18519883616"}]]) == ["185****3616"])   # fetch_users 现带 id（Info.Id）

    # 19) 陈旧切号行防伪：exclude 命中即跳过（monkeypatch _switch_lines，不连网）
    orig_swl = globals()["_switch_lines"]
    try:
        globals()["_switch_lines"] = lambda d, tail=4000, timeout=120: ["10:00:00 账号切换 -->> 111****1111",
                                                                       "10:05:00 账号切换 -->> 222****2222"]
        l1 = _find_switch_line("x", exclude=["10:00:00 账号切换 -->> 111****1111"])
        l2 = _find_switch_line("x", exclude=["10:00:00 账号切换 -->> 111****1111",
                                             "10:05:00 账号切换 -->> 222****2222"])
        ck("stale-switch-line-skipped", bool(l1) and l1.endswith("222****2222") and l2 is None, "%r/%r" % (l1, l2))
    finally:
        globals()["_switch_lines"] = orig_swl

    # 20) stop 应答口径：200 不带 taskId 也算成功（2026-09-28 实机 STOP-FAILED 假阴性回归）
    ck("stop-result-200-no-taskid", _stop_result({"code": 200, "message": "操作成功"})[0] is True)
    ck("stop-result-already-finished", _stop_result({"code": 500, "message": "任务已结束"})[0] is True)
    ck("stop-result-real-fail", _stop_result({"code": 500, "message": "boom"})[0] is False)
    ck("stop-result-bad-shape", _stop_result("nope")[0] is False)

    # 21) diff_tasks benign：仅 BilibiliAgreement_AUTO ±1 节点 ⇒ 标记 benign（AUTO-MAS per-run 注入噪声）
    a2 = {"StartUpThemes": {"next": ["X"]}, "N1": {"action": "DoNothing"}}
    b2 = {"StartUpThemes": {"next": ["BilibiliAgreement_AUTO", "X"]}, "N1": {"action": "DoNothing"},
          "BilibiliAgreement_AUTO": {"action": "ClickSelf"}}
    td2 = diff_tasks(a2, b2)
    ck("tasks-diff-benign-bilibili", td2.get("benign") == ["BilibiliAgreement_AUTO"],
       json.dumps(td2.get("benign")))
    b3 = dict(b2)
    b3["OtherNew"] = {}
    ck("tasks-diff-benign-not-swallowed", not diff_tasks(a2, b3).get("benign"),
       json.dumps(diff_tasks(a2, b3).get("benign")))
    ck("tasks-diff-benign-removed-side", diff_tasks(b2, a2).get("benign") == ["BilibiliAgreement_AUTO"])

    print("selftest: %s (%d/%d)" % ("OK" if not bad else "FAIL", total - bad, total))
    return 0 if not bad else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="MAA 升级护栏（更新前快照 / 更新后校验 / 差异报告）")
    ap.add_argument("action", nargs="?", default="verify", choices=["snapshot", "verify", "live", "show"])
    ap.add_argument("--dirs", help="逗号分隔 MAA 目录（默认 REST 推导的 12 槽）")
    ap.add_argument("--all", action="store_true", help="含退役目录 MAA_13/14/25/28")
    ap.add_argument("--out", help="snapshot 输出目录（默认 runtime/upgrade_guard/<stamp>）")
    ap.add_argument("--baseline", help="verify 指定基线 stamp 或目录")
    ap.add_argument("--script", help="live：脚本名/前缀/sid")
    ap.add_argument("--apply", action="store_true", help="live：真发车（默认 dry-run）")
    ap.add_argument("--timeout", type=int, default=_env_int("MAAORCH_UPGRADE_TIMEOUT", 240),
                    help="live 轮询上限秒（默认 240）")
    ap.add_argument("--poll", type=int, default=_env_int("MAAORCH_UPGRADE_POLL", 15))
    ap.add_argument("--force-window", action="store_true", help="live：允许班次窗口内跑（危险，默认拒绝）")
    ap.add_argument("--no-tasks-pull", action="store_true", help="不拉 tasks.json 原件（省时间）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()

    if a.action == "show":
        return cmd_show(as_json=a.json)

    if a.action == "live":
        if not a.script:
            print("live 需要 --script <名|前缀|sid>")
            return 2
        out = live_check(a.script, apply_=a.apply, timeout=a.timeout, poll=a.poll,
                         force_window=a.force_window)
        if a.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
        else:
            print("live 注入→读回验证：%s（%s）" % (out.get("verdict"), "真发车" if a.apply else "dry-run"))
            print("目标：%s → %s" % ((out.get("script") or {}).get("name"), out.get("dir")))
            for g in out.get("gates") or []:
                print("  闸门 %-9s %s  %s" % (g["gate"], "OK " if g.get("ok") else "NO ", g.get("detail")))
            for s in out.get("steps") or []:
                print("  - %s" % s)
            if out.get("task_uid"):
                print("  task_uid=%s" % out["task_uid"])
        return {"OK": 0, "PARTIAL": 1, "DRY-RUN": 0, "FAIL": 1}.get(out.get("verdict"), 2)

    blocker = no_go()
    if blocker:
        print("拒绝：%s=1（升级护栏被闸门关掉）" % blocker)
        return 2

    if a.action == "snapshot":
        dirs = resolve_dirs(include_retired=a.all, dirs_arg=a.dirs)
        base = snapshot(dirs, out_dir=a.out, fetch_tasks=not a.no_tasks_pull)
        if a.json:
            print(json.dumps(base, ensure_ascii=False, indent=1))
            return 0
        print("基线已建：%s（%s）" % (base["dir"], base["stamp"]))
        for ln in _render_slots_table(base):
            print(ln)
        print("tasks.json 原件：%d 份" % len(base.get("tasks_files") or {}))
        print("切号注入闸门：%s" % base["switch_gate"]["verdict"])
        return 0

    # verify
    ref = None
    if a.baseline:
        cand = a.baseline
        if os.path.isdir(cand) and os.path.exists(os.path.join(cand, "baseline.json")):
            ref = {"stamp": os.path.basename(cand), "dir": cand, "path": os.path.join(cand, "baseline.json")}
        elif os.path.isfile(cand):
            ref = {"stamp": os.path.basename(os.path.dirname(cand)), "dir": os.path.dirname(cand), "path": cand}
        else:
            ref = {"stamp": cand, "dir": os.path.join(GUARD_DIR, cand),
                   "path": os.path.join(GUARD_DIR, cand, "baseline.json")}
    else:
        ref = read_json(CURRENT)
    if not ref:
        print("没有基线：先跑 `python tools/maa_upgrade_check.py snapshot`")
        return 2
    base = read_json(ref.get("path") or "")
    if not base:
        print("基线读不到：%s" % ref.get("path"))
        return 2
    dirs = base.get("dirs") or resolve_dirs()
    cur_stamp = stamp()
    cur_dir = ensure_dir(os.path.join(GUARD_DIR, cur_stamp + "_verify"))
    cur = snapshot(dirs, out_dir=cur_dir, fetch_tasks=not a.no_tasks_pull, set_current=False)
    cur["kind"] = "verify"
    diff = compare(base, cur)
    rep = render_report(base, cur, diff)
    rep_path = os.path.join(cur_dir, "report.md")
    with io.open(rep_path, "w", encoding="utf-8") as f:
        f.write(rep)
    write_json(os.path.join(cur_dir, "diff.json"), diff)
    log_line({"kind": "verify", "stamp": cur_stamp, "baseline": base.get("stamp"),
              "verdict": diff["verdict"], "program_changed": diff["program_changed"],
              "resource_changed": diff["resource_changed"]})
    if a.json:
        print(json.dumps(diff, ensure_ascii=False, indent=1))
    else:
        print(rep)
        print("报告：%s" % rep_path)
    if diff["verdict"] in ("VIOLATION", "DRIFT"):
        return 1
    if diff["verdict"] == "UNKNOWN":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
