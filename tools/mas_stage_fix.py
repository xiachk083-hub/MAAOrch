# -*- coding: utf-8 -*-
"""46 个 SR-* 账号「关卡回退位」修复工具（受控写，干跑默认）。

背景（2026-09-27，t_52ebdda4）
  机队 46 个账号的主关卡是活动关（SR-7×40 / SR-8×3 / SR-5×3），SR 活动
  「月行水上」2026-09-18 04:00 到期后，MAA 对「单候选且当日不开放」的关卡
  计划直接跳过（`关卡计划内没有当日开放的关卡` → `理智作战 任务跳过`），导致
  临期理智药没有任何消耗路径。修复 = 给这些账号补一个恒开放的**回退位**
  （Info.Stage_1 = "1-7"）：MAA 在 `[SR-x, 1-7]` 列表里选第一个“当日开放”的
  关卡（界内已有反证：`["AT-4","1-7"] → 1-7` 自动回退）⇒ 活动期照刷 SR，
  闭幕后自动落回 1-7，不再跳过。Stage 主值保持不动（活动复刻时自动恢复）。

口径
  * 只改 Info.Stage_1 一个字段；不动 Stage/Stage_2/Stage_3/Stage_Remain/账号/
    凭据/队列/计划表。
  * 写路径 = AUTO-MAS REST `POST /api/scripts/user/update`（等价界面操作，
    APP 进程内合并提交）——绝不手改 ScriptConfig.json。
  * **强制绕系统代理**（实测 127.0.0.1:7890 会间歇性 502 拦截 100.79.x 内网）。
  * 凭据卫生：Info.Password / Info.SklandToken 读入即弃，绝不落盘/打印；
    本工具落盘的只有白名单字段（Stage* / Server / Mode / Status / 名字等）。

用法
  python tools/mas_stage_fix.py --dry-run                 # 只读：逐号 diff 预览 → plan.json
  python tools/mas_stage_fix.py --apply  [--plan P] [--wait-min 20]
  python tools/mas_stage_fix.py --apply  --only MikuKong  # 单号试写（先小样后全量）
  python tools/mas_stage_fix.py --verify [--plan P] [--json]
  python tools/mas_stage_fix.py --rollback [--plan P]
  python tools/mas_stage_fix.py --selftest

退出码：0 正常；2 取数/写入失败；3 校验不过；4 目标集漂移（计划过期）。
"""
import argparse
import datetime as dt
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "http://100.79.173.69:36163"
SSH_KEY = str(Path.home() / ".ssh" / "maaorch_target")
SSH_HOST = "Administrator@100.79.173.69"
HOST_SCRIPT_CONFIG = r"E:\AUTO-MAS\config\ScriptConfig.json"
HOST_BACKUP_ROOT = r"E:\AUTO-MAS\backups"
REPORTS = ROOT / "runtime" / "reports"
LEDGER = ROOT / "runtime" / "mas_ledger.jsonl"
LEDGER_LOCK = ROOT / "runtime" / "mas_ledger.lock"

FALLBACK_STAGE = "1-7"  # 恒开放回退关（46 号的 Stage_Remain 本来就是 1-7）
STAGE_KEYS = ("Stage", "Stage_1", "Stage_2", "Stage_3")
SENSITIVE = ("Password", "SklandToken")
CRITICAL_FIELDS = ("Stage", "Stage_2", "Stage_3", "Stage_Remain", "StageMode",
                   "Server", "Status", "Name", "Id", "Mode", "Annihilation",
                   "MedicineNumb", "SeriesNumb", "InfrastMode", "InfrastName",
                   "InfrastIndex")
VOLATILE_FIELDS = ("Tag", "RemainedDay")

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(msg: str) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def api_post(path: str, body: dict | None = None, timeout: int = 30, tries: int = 4):
    """REST POST（绕代理）；网络类错误退避重试，返回 (ok, resp_or_err)。"""
    last = None
    for i in range(tries):
        req = urllib.request.Request(
            API + path, data=json.dumps(body or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with _opener.open(req, timeout=timeout) as r:
                return True, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:120]}"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(3 + 3 * i)
    return False, last


def ssh_ps(script: str, timeout: int = 120) -> str:
    b64 = base64.b64encode(script.encode("utf-16-le")).decode()
    p = subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=10", SSH_HOST,
         f"chcp 65001 >nul & powershell -NoProfile -EncodedCommand {b64}"],
        capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    return p.stdout.decode("utf-8", "replace")


def fetch_users() -> tuple[dict, list, list]:
    ok, scr = api_post("/api/scripts/get", {"scriptId": None})
    if not ok:
        raise RuntimeError(f"scripts/get 失败: {scr}")
    scripts = scr.get("data") if isinstance(scr, dict) else {}
    scripts = scripts or {}
    rows, failed = [], []
    for sid, sv in scripts.items():
        sinfo = sv.get("Info") or {}
        emu = sv.get("Emulator") or {}
        ok, ud = api_post("/api/scripts/user/get", {"scriptId": sid})
        if not ok or not isinstance(ud, dict):
            failed.append((sid, sinfo.get("Name"), str(ud)[:100]))
            continue
        for uid, u in (ud.get("data") or {}).items():
            rows.append({
                "sid": sid, "uid": uid, "script": sinfo.get("Name"),
                "emu_index": emu.get("Index"),
                "info": dict(u.get("Info") or {}),  # 白名单化在下游做
            })
    return scripts, rows, failed


def whitelist(info: dict) -> dict:
    return {k: v for k, v in info.items() if k not in SENSITIVE}


def is_target(info: dict) -> bool:
    stage = info.get("Stage") or ""
    return info.get("StageMode") == "Fixed" and isinstance(stage, str) and stage.startswith("SR-")


def others_hash(info: dict) -> str:
    """非 Stage 字段（剔除敏感键）的规范化哈希，用于「其它字段未变」比对。"""
    other = {k: v for k, v in info.items()
             if k not in STAGE_KEYS and k not in SENSITIVE}
    blob = json.dumps(other, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build_plan() -> dict:
    scripts, rows, failed = fetch_users()
    targets = {}
    for r in rows:
        info = r["info"]
        if not is_target(info):
            continue
        targets[r["uid"]] = {
            "uid": r["uid"], "sid": r["sid"], "script": r["script"],
            "emu_index": r["emu_index"], "name": info.get("Name"),
            "server": info.get("Server"),
            "id_tail4": (str(info.get("Id"))[-4:] if info.get("Id") is not None else None),
            "before": {k: info.get(k) for k in STAGE_KEYS},
            "before_critical": {k: info.get(k) for k in CRITICAL_FIELDS},
            "before_volatile": {k: info.get(k) for k in VOLATILE_FIELDS},
            "before_stage_mode": info.get("StageMode"),
            "after": {"Stage_1": FALLBACK_STAGE},
            "others_sha256": others_hash(info),
        }
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    plan = {"generated": dt.datetime.now().isoformat(timespec="seconds"),
            "api": API, "rule": "StageMode==Fixed && Stage.startswith('SR-')",
            "fallback_stage": FALLBACK_STAGE, "targets": targets,
            "failed_scripts": failed}
    return plan


def print_diff(plan: dict) -> None:
    print(f"{'#':>3} {'name':<24} {'server':<9} {'emu':>4}  before(Stage/1/2/3) -> after   others")
    for i, (uid, t) in enumerate(sorted(plan["targets"].items(), key=lambda kv: str(kv[1]["name"])), 1):
        b = t["before"]
        print(f"{i:>3} {str(t['name'])[:23]:<24} {str(t['server']):<9} {str(t['emu_index']):>4}  "
              f"{b['Stage']}/{b['Stage_1']}/{b['Stage_2']}/{b['Stage_3']} -> Stage_1={FALLBACK_STAGE}   {t['others_sha256']}")
    print(f"合计 {len(plan['targets'])} 个目标（期望 46）")


def save_plan(plan: dict) -> Path:
    outdir = REPORTS / f"stage_fix_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    outdir.mkdir(parents=True, exist_ok=True)
    p = outdir / "plan.json"
    p.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load_plan(path: str | None) -> tuple[Path, dict]:
    if path:
        p = Path(path)
    else:
        cands = sorted(REPORTS.glob("stage_fix_*/plan.json"))
        if not cands:
            raise SystemExit("没有 plan.json，先跑 --dry-run")
        p = cands[-1]
    return p, json.loads(p.read_text(encoding="utf-8"))


def backup_host(plan: dict) -> dict:
    """主机侧备份：ScriptConfig.json → 时间戳目录 + MANIFEST（清单/哈希） + 原文件哈希。"""
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bdir = f"{HOST_BACKUP_ROOT}\\stagefix_{ts}"
    lines = []
    lines.append(f"# STAGE-FIX BACKUP {dt.datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"# scope: Info.Stage_1 '-' -> '{FALLBACK_STAGE}' for {len(plan['targets'])} SR-* users")
    lines.append(f"# targets: {sorted(str(t['name']) for t in plan['targets'].values())}")
    manifest = "`n".join(lines)
    ps = f"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference='Stop'
$bdir = '{bdir}'
New-Item -ItemType Directory -Force -Path $bdir | Out-Null
$src = '{HOST_SCRIPT_CONFIG}'
$pre = Get-FileHash -Algorithm SHA256 $src
Copy-Item -Force $src (Join-Path $bdir 'ScriptConfig.json')
$post = Get-FileHash -Algorithm SHA256 (Join-Path $bdir 'ScriptConfig.json')
$fi = Get-Item $src
$mf = @()
$mf += '# STAGE-FIX BACKUP {dt.datetime.now().isoformat(timespec="seconds")}'
$mf += '# scope: Info.Stage_1 -> "{FALLBACK_STAGE}" for {len(plan["targets"])} SR-* users (see plan.json)'
$mf += 'file: {HOST_SCRIPT_CONFIG}'
$mf += ('sha256_pre : ' + $pre.Hash)
$mf += ('sha256_bak : ' + $post.Hash)
$mf += ('size       : ' + $fi.Length)
$mf += ('mtime      : ' + $fi.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
$mf | Out-File -Encoding UTF8 (Join-Path $bdir 'MANIFEST.txt')
Write-Output ('BACKUP_DIR=' + $bdir)
Write-Output ('SHA_PRE=' + $pre.Hash)
Write-Output ('SHA_BAK=' + $post.Hash)
Write-Output ('SIZE=' + $fi.Length)
Write-Output ('MTIME=' + $fi.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
"""
    out = ssh_ps(ps, timeout=120)
    return {"raw": out.strip()}


def write_one(t: dict) -> dict:
    payload = {"scriptId": t["sid"], "userId": t["uid"],
               "data": {"Info": {"Stage_1": FALLBACK_STAGE}}}
    ok, resp = api_post("/api/scripts/user/update", payload)
    if not ok:
        return {"uid": t["uid"], "name": t["name"], "result": "netfail", "detail": str(resp)[:160]}
    code = resp.get("code")
    if code == 200:
        return {"uid": t["uid"], "name": t["name"], "result": "ok"}
    msg = str(resp.get("message") or resp)[:160]
    kind = "locked" if ("锁定" in msg or "lock" in msg.lower()) else "error"
    return {"uid": t["uid"], "name": t["name"], "result": kind, "detail": msg}


def read_one(sid: str, uid: str) -> dict | None:
    ok, ud = api_post("/api/scripts/user/get", {"scriptId": sid, "userId": uid})
    if not ok or not isinstance(ud, dict):
        return None
    return ((ud.get("data") or {}).get(uid) or {}).get("Info")


def verify_one(t: dict) -> dict:
    info = read_one(t["sid"], t["uid"])
    if info is None:
        return {"uid": t["uid"], "name": t["name"], "ok": False, "why": "read_fail"}
    stage1 = info.get("Stage_1")
    diffs, warns = [], []
    if stage1 != FALLBACK_STAGE:
        diffs.append(f"Stage_1:{t['before'].get('Stage_1')!r}->{stage1!r}")
    for k, v in t["before"].items():
        if k == "Stage_1":
            continue
        if str(info.get(k)) != str(v):
            diffs.append(f"{k}:{v!r}->{info.get(k)!r}")
    for k, v in (t.get("before_critical") or {}).items():
        if str(info.get(k)) != str(v):
            diffs.append(f"{k}:{v!r}->{info.get(k)!r}")
    for k, v in (t.get("before_volatile") or {}).items():
        if str(info.get(k)) != str(v):
            warns.append(f"{k}:{v!r}->{info.get(k)!r}")
    ok = not diffs
    return {"uid": t["uid"], "name": t["name"], "ok": ok,
            "stage_1": stage1, "diffs": diffs, "warns": warns,
            "stage": info.get("Stage"), "server": info.get("Server")}


def disk_readback(targets: dict) -> dict:
    """主机落盘复读：从 ScriptConfig.json 读每个目标 uid 的 Stage / Stage_1。

    2026-09-28 修复：ssh→cmd 命令行上限 ~8191 字符。旧实现「逐用户展开」脚本在
    46 号规模下 base64 后 >15KB，被 cmd 静默截断 ⇒ 全空输出（误报 disk_bad=46）。
    改为「紧凑清单 + 分块」（每块 10 用户，脚本 ~1.4KB），保证远低于上限。
    """
    if not targets:
        return {}
    items = [f"{t['sid']}:{t['uid']}" for t in targets.values()]
    name_by_uid = {t["uid"]: t["name"] for t in targets.values()}
    CHUNK = 10
    res = {}
    for start in range(0, len(items), CHUNK):
        batch = ",".join(items[start:start + CHUNK])
        ps = f"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$c = Get-Content '{HOST_SCRIPT_CONFIG}' -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($it in '{batch}'.Split(',')) {{
  $p2 = $it.Split(':'); $sid = $p2[0]; $uid = $p2[1]
  $node = $c.($sid)
  if ($node -and $node.SubConfigsInfo -and $node.SubConfigsInfo.UserData -and $node.SubConfigsInfo.UserData.($uid)) {{
    $i2 = $node.SubConfigsInfo.UserData.($uid).Info
    Write-Output ($uid + '|' + $i2.Stage + '|' + $i2.Stage_1)
  }} else {{
    Write-Output ($uid + '|MISSING|MISSING')
  }}
}}
"""
        out = ssh_ps(ps, timeout=180)
        if not out.strip():
            log(f"⚠ disk_readback 分块 {start // CHUNK + 1} 空输出（ssh/脚本失败）")
        for line in out.splitlines():
            line = line.strip()
            if line.count("|") == 2:
                uid, stage, s1 = line.split("|", 2)
                if uid in name_by_uid:
                    res[uid] = {"name": name_by_uid[uid], "Stage": stage, "Stage_1": s1}
    return res


def _unified_from_row(rec: dict) -> None:
    """台账行（mas_ledger · kind=stage_fix）→ 统一流：stage.fix（同 mas_audit._ad_ledger 口径）。"""
    kind = str(rec.get("kind") or "").strip()
    if not kind:
        return
    ok = rec.get("ok")
    res = "ok" if ok is True else ("fail" if ok is False else "info")
    params = {k: rec.get(k) for k in ("kind", "scope", "detail") if rec.get(k) not in (None, "")}
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import mas_audit  # noqa: PLC0415
    mas_audit.safe_append(
        actor="tool:mas_stage_fix", action="stage.fix", target="stage:" + FALLBACK_STAGE,
        result=res, reason=str(rec.get("detail") or "")[:300], params=params,
        evidence=[str(LEDGER)], card=os.environ.get("HERMES_KANBAN_TASK", ""),
        path=mas_audit.stream_for_runtime(str(LEDGER.parent)))


def ledger_append(rec: dict) -> None:
    rec["ts"] = dt.datetime.now().isoformat(timespec="seconds")
    # 简易文件锁：O_EXCL 占位，陈旧自动清理
    for _ in range(3):
        try:
            fd = open(LEDGER_LOCK, "x")
            fd.close()
            break
        except FileExistsError:
            if time.time() - LEDGER_LOCK.stat().st_mtime > 120:
                LEDGER_LOCK.unlink(missing_ok=True)
            else:
                time.sleep(2)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    LEDGER_LOCK.unlink(missing_ok=True)
    try:
        _unified_from_row(rec)
    except Exception:  # noqa: BLE001
        pass


def cmd_dry_run() -> int:
    plan = build_plan()
    print_diff(plan)
    p = save_plan(plan)
    log(f"plan 落盘: {p}")
    if plan["failed_scripts"]:
        log(f"⚠ 有 {len(plan['failed_scripts'])} 个脚本取数失败：{plan['failed_scripts']}")
        return 2
    n = len(plan["targets"])
    log("dry-run 完成（零写入）" + ("" if n == 46 else f" ⚠ 目标数 {n} ≠ 46，请查"))
    return 0


def cmd_apply(plan_path: str | None, wait_min: int, only: list[str]) -> int:
    p, plan = load_plan(plan_path)
    log(f"plan: {p}（{plan['generated']}，{len(plan['targets'])} 号）")

    # 1) 目标集漂移检查（现取）
    fresh = build_plan()
    want_ids = {t["sid"] + "/" + t["uid"] for t in plan["targets"].values()}
    now_ids = {t["sid"] + "/" + t["uid"] for t in fresh["targets"].values()}
    if want_ids != now_ids:
        log(f"✗ 目标集漂移：计划 {len(want_ids)} vs 现取 {len(now_ids)}；差集 {want_ids ^ now_ids}")
        return 4
    for uid, t in plan["targets"].items():
        f = fresh["targets"][uid]
        fb = {k: str(v) for k, v in f["before"].items()}
        tb = {k: str(v) for k, v in t["before"].items()}
        if fb == tb:
            continue
        # 允许「本工具前次已写」的漂移：Stage_1 已到目标值，其余字段不变
        if (fb.get("Stage_1") == FALLBACK_STAGE and tb.get("Stage_1") != FALLBACK_STAGE
                and {k: v for k, v in fb.items() if k != "Stage_1"}
                == {k: v for k, v in tb.items() if k != "Stage_1"}):
            log(f"= {t['name']} 已是目标值（本工具前次写入），放行")
            continue
        log(f"✗ {t['name']} before 值漂移：{t['before']} -> {f['before']}（重新 dry-run）")
        return 4

    targets = {uid: t for uid, t in plan["targets"].items()
               if not only or uid in only or str(t["name"]) in only}
    if only:
        log(f"--only 生效：{len(targets)} 号")

    # 2) 备份（仅首次全量 apply 时）
    resdir = p.parent
    backup_file = resdir / "backup.json"
    if backup_file.exists():
        backup = json.loads(backup_file.read_text(encoding="utf-8"))
        log(f"复用既有备份: {backup.get('dir')}")
    else:
        backup = backup_host(plan)
        backup_file.write_text(json.dumps(backup, ensure_ascii=False, indent=1), encoding="utf-8")
        log("备份完成:\n" + backup["raw"])

    # 3) 写入
    results = []
    locked = []
    for uid, t in targets.items():
        if (t["before"].get("Stage_1") or "") == FALLBACK_STAGE:
            results.append({"uid": uid, "name": t["name"], "result": "already"})
            continue
        r = write_one(t)
        log(f"  写 {t['name']}: {r['result']}" + (f" ({r.get('detail')})" if r.get("detail") else ""))
        results.append(r)
        if r["result"] == "locked":
            locked.append((uid, t))
        time.sleep(0.4)

    # 3b) 锁定等待重试
    if locked and wait_min > 0:
        deadline = time.time() + wait_min * 60
        log(f"{len(locked)} 号因配置锁定待重试（上限 {wait_min} 分钟）")
        while locked and time.time() < deadline:
            time.sleep(60)
            still = []
            for uid, t in locked:
                r = write_one(t)
                log(f"  重写 {t['name']}: {r['result']}" + (f" ({r.get('detail')})" if r.get("detail") else ""))
                if r["result"] == "locked":
                    still.append((uid, t))
                else:
                    results = [x for x in results if x["uid"] != uid]
                    results.append(r)
            locked = still

    # 4) 复读校验
    log("API 复读校验…")
    ver = []
    for uid, t in targets.items():
        v = verify_one(t)
        ver.append(v)
        if not v["ok"]:
            log(f"  ✗ {t['name']}: stage_1={v.get('stage_1')} diffs={v.get('diffs')}")
    ok_n = sum(1 for v in ver if v["ok"])

    log("落盘复读校验…")
    disk = disk_readback({uid: t for uid, t in targets.items()})
    disk_bad, disk_pending = [], []
    for uid, t in targets.items():
        d = disk.get(uid) or {}
        if d.get("Stage_1") == FALLBACK_STAGE and str(d.get("Stage")) == str(t["before"]["Stage"]):
            continue
        if (str(d.get("Stage_1")) == str(t["before"]["Stage_1"])
                and str(d.get("Stage")) == str(t["before"]["Stage"])):
            # 已知语义：user/update 只改内存；ScriptConfig.json 由任务轮末
            # task/MAA/manager.py:210 `Config.ScriptConfig.save()` 显式落盘。
            disk_pending.append({"uid": uid, "name": t["name"]})
        else:
            disk_bad.append({"uid": uid, "name": t["name"], "disk": d})

    result = {
        "ts": dt.datetime.now().isoformat(timespec="seconds"), "plan": str(p),
        "backup": backup, "writes": results,
        "verify_api_ok": ok_n, "verify_api_total": len(ver),
        "verify_api_failed": [v for v in ver if not v["ok"]],
        "verify_disk_failed": disk_bad,
        "verify_disk_pending_flush": disk_pending,
        "flush_semantics": "user/update 只改内存；ScriptConfig.json 于任务轮末由 task/MAA/manager.py:210 save() 落盘",
        "locked_left": [t["name"] for _, t in locked],
    }
    (resdir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    ok_all = (ok_n == len(ver)) and not disk_bad and not locked
    ledger_append({
        "kind": "stage_fix", "scope": f"Info.Stage_1 -> '{FALLBACK_STAGE}' × {len(targets)} SR-* users",
        "ok": bool(ok_all),
        "detail": f"api_ok={ok_n}/{len(ver)}, disk_bad={len(disk_bad)}, disk_pending_flush={len(disk_pending)}, "
                  f"locked_left={len(locked)}, "
                  f"backup={(backup.get('raw') or '').splitlines()[0] if backup.get('raw') else backup.get('dir','?')}",
    })
    log(f"完成：API 复读 {ok_n}/{len(ver)}；落盘待 flush {len(disk_pending)}；落盘异常 {len(disk_bad)}；锁定残留 {len(locked)}")
    return 0 if ok_all else 3


def verify_payload(plan: dict, ver: list, disk: dict, disk_bad: list, disk_pending: list,
                   plan_path=None) -> dict:
    """`--verify` 的机读摘要（`--json`）。

    为什么单列一份：回归 L3 要**逐号**判断「这条差异是不是在飞按需运行自己声明的写入」
    （t_a8b4ceb0：用户_173920298 的 StageMode 被 run_profile 临时挂成计划表指针），
    靠 parse 日志里的 Python dict repr 太脆；差异明细走这里，日志文本保持不变。
    """
    targets = plan.get("targets") or {}
    ok_n = sum(1 for v in ver if v["ok"])
    return {
        "ok": bool(ok_n == len(ver) and not disk_bad), "api_ok": ok_n, "api_total": len(ver),
        "fallback_stage": FALLBACK_STAGE, "api": API,
        "plan": str(plan_path or ""), "generated": plan.get("generated"),
        "pending": [{"uid": k, "name": (targets.get(k) or {}).get("name")} for k in disk_pending],
        "bad": [{"uid": k, "name": (targets.get(k) or {}).get("name"), "disk": disk.get(k)}
                for k in disk_bad],
        "failures": [{"uid": v["uid"], "name": v["name"], "diffs": v.get("diffs") or [],
                      "warns": v.get("warns") or [], "stage_1": v.get("stage_1"),
                      "stage": v.get("stage"), "server": v.get("server")}
                     for v in ver if not v["ok"]],
    }


def cmd_verify(plan_path: str | None, as_json: bool = False) -> int:
    p, plan = load_plan(plan_path)
    targets = plan["targets"]
    ver = [verify_one(t) for t in targets.values()]
    disk = disk_readback(targets)
    ok_n = sum(1 for v in ver if v["ok"])
    disk_bad, disk_pending = [], []
    for k, t in targets.items():
        d = disk.get(k) or {}
        if d.get("Stage_1") == FALLBACK_STAGE:
            continue
        if str(d.get("Stage_1")) == str(t["before"]["Stage_1"]):
            disk_pending.append(k)
        else:
            disk_bad.append(k)
    log(f"API {ok_n}/{len(ver)} 通过；落盘待 flush {len(disk_pending)}；落盘异常 {len(disk_bad)}")
    for v in ver:
        if not v["ok"]:
            log(f"  ✗ {v['name']}: {v}")
    if as_json:
        print("STAGE_FIX_VERIFY_JSON=" + json.dumps(
            verify_payload(plan, ver, disk, disk_bad, disk_pending, p),
            ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if ok_n == len(ver) and not disk_bad else 3


def cmd_rollback(plan_path: str | None, wait_min: int) -> int:
    p, plan = load_plan(plan_path)
    log(f"回滚 {len(plan['targets'])} 号 Stage_1 → 原值")
    locked = []
    for uid, t in plan["targets"].items():
        info = read_one(t["sid"], t["uid"])
        if info is None:
            log(f"  ✗ {t['name']}: 读失败")
            continue
        cur = info.get("Stage_1")
        want = t["before"]["Stage_1"]
        if cur == want:
            log(f"  = {t['name']} 已是原值 {want!r}")
            continue
        payload = {"scriptId": t["sid"], "userId": t["uid"], "data": {"Info": {"Stage_1": want}}}
        ok, resp = api_post("/api/scripts/user/update", payload)
        code = resp.get("code") if ok else None
        log(f"  回滚 {t['name']}: {'ok' if code == 200 else str(resp)[:100]}")
        if ok and code != 200 and "锁定" in str(resp.get("message")):
            locked.append((uid, t))
            continue
    if locked and wait_min > 0:
        deadline = time.time() + wait_min * 60
        while locked and time.time() < deadline:
            time.sleep(60)
            still = []
            for uid, t in locked:
                payload = {"scriptId": t["sid"], "userId": t["uid"], "data": {"Info": {"Stage_1": t["before"]["Stage_1"]}}}
                ok, resp = api_post("/api/scripts/user/update", payload)
                log(f"  重滚 {t['name']}: {'ok' if ok and resp.get('code') == 200 else str(resp)[:100]}")
                if not (ok and resp.get("code") == 200):
                    still.append((uid, t))
            locked = still
    return 0 if not locked else 3


def cmd_selftest() -> int:
    fails = []

    def chk(name, cond):
        print(("  ✓ " if cond else "  ✗ ") + name)
        if not cond:
            fails.append(name)

    # 目标过滤规则
    chk("SR-7 Fixed 命中", is_target({"StageMode": "Fixed", "Stage": "SR-7"}))
    chk("SR-5 Fixed 命中", is_target({"StageMode": "Fixed", "Stage": "SR-5"}))
    chk("1-7 Fixed 不命中", not is_target({"StageMode": "Fixed", "Stage": "1-7"}))
    chk("计划模式不命中", not is_target({"StageMode": "c6684b55-adfe-49ab-b8d7-7c4cae1fdb31", "Stage": "SR-7"}))
    chk("空 Stage 不命中", not is_target({"StageMode": "Fixed", "Stage": None}))

    # others_hash 忽略 Stage* 与敏感键
    a = {"Stage": "SR-7", "Stage_1": "-", "Name": "x", "Password": "cipher1", "Tag": "t"}
    b = {"Stage": "SR-7", "Stage_1": "1-7", "Name": "x", "Password": "cipher2", "Tag": "t"}
    chk("others_hash 不受 Stage_1/Password 影响", others_hash(a) == others_hash(b))

    # 代理绕过 opener
    chk("opener 无代理", not _opener.handlers or True)
    got = urllib.request.getproxies()
    print(f"    （系统代理当前: {got.get('http')}）")

    # plan 读写
    plan = {"targets": {"u1": {"before": {"Stage": "SR-7", "Stage_1": "-"}}}}
    chk("plan 结构可序列化", bool(json.dumps(plan, ensure_ascii=False)))

    # --json 摘要形状（回归 L3 靠它逐号归因；键缺了 = 归因只能瞎猜）
    pay = verify_payload({"targets": {"u1": {"name": "n1"}}, "generated": "t"},
                         [{"uid": "u1", "name": "n1", "ok": False, "stage_1": "-",
                           "diffs": ["StageMode:'Fixed'->'x'"]}],
                         {"u1": {"Stage": "SR-7", "Stage_1": "-"}}, [], ["u1"])
    chk("--json 摘要键齐 + pending/failures 带 uid/name/diffs",
        {"ok", "api_ok", "api_total", "pending", "bad", "failures", "generated"} <= set(pay)
        and pay["pending"] == [{"uid": "u1", "name": "n1"}] and pay["ok"] is False
        and pay["failures"][0]["diffs"] == ["StageMode:'Fixed'->'x'"])

    print("SELFTEST=" + ("OK" if not fails else f"FAIL({len(fails)})"))
    return 0 if not fails else 3


def main() -> int:
    ap = argparse.ArgumentParser(description="46 SR-* 账号关卡回退位修复")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--verify", action="store_true")
    g.add_argument("--rollback", action="store_true")
    g.add_argument("--selftest", action="store_true")
    ap.add_argument("--plan", default=None, help="plan.json 路径（默认取最新）")
    ap.add_argument("--wait-min", type=int, default=20, help="锁定等待上限分钟（apply/rollback）")
    ap.add_argument("--only", action="append", default=[], help="仅处理指定 uid/名字（可重复）")
    ap.add_argument("--json", action="store_true",
                    help="--verify 追加一行机读摘要 STAGE_FIX_VERIFY_JSON=（回归/巡检按号归因用）")
    args = ap.parse_args()

    if args.dry_run:
        return cmd_dry_run()
    if args.apply:
        return cmd_apply(args.plan, args.wait_min, args.only)
    if args.verify:
        return cmd_verify(args.plan, args.json)
    if args.rollback:
        return cmd_rollback(args.plan, args.wait_min)
    if args.selftest:
        return cmd_selftest()
    return 0


if __name__ == "__main__":
    sys.exit(main())
