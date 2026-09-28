#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""AUTO-MAS 补丁持久化闸门（只读为主）—— 升级/重装覆盖我们的补丁后，第一时间发现。

背景：我们给 AUTO-MAS 打了若干补丁（`AutoProxy.py` 切号 v2 / 理智药、`mumu.py` 判活关闭、
`constants.py` 理智药）。升级或重装会**静默覆盖**它们，重装后的新文件本身不会报错——
表现为「切号悄悄不切了 / 关模拟器又不关了」这类低频故障。本闸门把「补丁还在不在」变成一条
可巡检的判据。

清单：`runtime/patches/manifest.json`
  每个补丁记录：文件路径 | 关键特征串/正则断言 | 期望哈希 | 备份文件名 | 作用 | 回滚方式 | 重放方式
快照：`runtime/patches/payloads/*.patched`
  打补丁后的整文件快照（供 `--reapply` 重放；`--capture` 重新录制）。

用法：
  python tools/mas_patch_guard.py                  # 详细报告（人读）
  python tools/mas_patch_guard.py --check          # 紧凑（巡检 cron 用），末行 PATCH_GUARD=OK|MISSING:<n>
  python tools/mas_patch_guard.py --json           # 机器可读
  python tools/mas_patch_guard.py --selftest       # 纯本地规则自检（不连网）
  python tools/mas_patch_guard.py --reapply <补丁名>            # 重放（**默认 dry-run**，零写入）
  python tools/mas_patch_guard.py --reapply <补丁名> --apply    # 真重放（改前备份、改后回读；需批准）
  python tools/mas_patch_guard.py --capture [--apply]           # 重新录制 payload/哈希基线
判据（fail-closed）：文件在 + 全部特征断言通过 ⇒ OK；断言不过/文件没了 ⇒ MISSING；读不到 ⇒ UNKNOWN。
退出码：0=OK 1=MISSING 2=UNKNOWN（另外 WARN=哈希漂移、STALE=补丁在盘但侧车没重启加载，都不改退出码）。

真重放的纪律（与 auto-mas-host-ops 技能「改动纪律」一致）：
  1) 只改文件不动进程；**改完必须重启侧车才生效**（Python 模块启动时导入、不热更）；
  2) 班次窗口（05:00-08:00 / 19:30-23:00）内禁止重启侧车；
  3) 重放前先 dry-run 看 diff；`--apply` 会先备份（.bak-patchguard-<ts>）再写、写完回读校验；
  4) 升级后整文件重放会丢上游改动 ⇒ 先看 diff 再拍板（--apply 前请人工确认）。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(ROOT, "runtime", "patches", "manifest.json")
PAYLOAD_DIR = os.path.join(ROOT, "runtime", "patches", "payloads")
LEDGER = os.path.join(ROOT, "runtime", "mas_patch_guard_log.jsonl")
KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")
NO_GO = os.environ.get("MAAORCH_PATCH_NO_GO", "") == "1"

# ------------------------------------------------------------------ 基础

def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: str) -> str:
    with open(p, "rb") as fh:
        return sha256(fh.read())


def short(s: str, n: int = 60) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


# ------------------------------------------------------------------ 远端读取

_PS_FETCH = r"""
$paths = @(__PATHS__)
$res = @()
foreach ($p in $paths) {
  if (Test-Path -LiteralPath $p) {
    try {
      $i = Get-Item -LiteralPath $p
      $b = [IO.File]::ReadAllBytes($p)
      $res += [pscustomobject]@{path=$p; status='OK'; size=$i.Length;
        mtime=$i.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'); b64=[Convert]::ToBase64String($b)}
    } catch {
      $res += [pscustomobject]@{path=$p; status='ERROR'; size=0; mtime=''; b64=''; error=[string]$_.Exception.Message}
    }
  } else {
    $res += [pscustomobject]@{path=$p; status='MISSING'; size=0; mtime=''; b64=''; error='not found'}
  }
}
$sc = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'AUTO-MAS\\main\.py' } | Select-Object -First 1
$sidecar = [pscustomobject]@{pid=$null; start=''}
if ($sc) {
  $sidecar.pid = [int]$sc.ProcessId
  try { $sidecar.start = (Get-Process -Id $sc.ProcessId -ErrorAction Stop).StartTime.ToString('yyyy-MM-dd HH:mm:ss') } catch { $sidecar.start = '' }
}
Write-Output ('__PATCHGUARD__' + (ConvertTo-Json -Compress -Depth 5 ([pscustomobject]@{files=$res; sidecar=$sidecar})))
"""


def ssh_ps(script: str, timeout: int = 240) -> str:
    if len(script) > 12000:
        # 防 WinError 206（命令行过长）：EncodedCommand 走命令行 ⇒ 脚本 ≤ ~12k 字符。
        # 大 payload 上传请用 ssh_ps_stdin（走 stdin，不受命令行长度限制）。
        raise ValueError("PS 脚本过长（%d 字符，上限 12000）" % len(script))
    prefix = ("$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; "
              "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
    b64 = base64.b64encode((prefix + script).encode("utf-16-le")).decode("ascii")
    cmd = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
           SSH_HOST, "powershell -NoProfile -EncodedCommand " + b64]
    # stdin=DEVNULL 必须：在 MCP stdio server 里跑时，ssh 若继承父进程 stdin 管道会挂死到超时。
    r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


def ssh_ps_stdin(script: str, timeout: int = 300) -> str:
    """把 PS 脚本走 **stdin**（`powershell -Command -`）发到远端 —— 不受命令行长度限制。

    用于大 payload（base64 整文件上传，实测 65KB 单块 OK）。脚本必须是 ASCII/UTF-8 文本，
    多行按行执行；输出仍按 UTF-8 解码。
    """
    cmd = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
           SSH_HOST, "powershell -NoProfile -Command -"]
    r = subprocess.run(cmd, input=script.encode("utf-8"), capture_output=True, timeout=timeout)
    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace").strip()
    if err and "WARNING" not in err.upper():
        out += "\n__STDERR__ " + err[:400]
    return out


def remote_fetch(paths: list, timeout: int = 240, retries: int = 1) -> dict:
    """一次 ssh 读回若干文件（内容+哈希+元数据）+ 侧车进程信息。

    返回 {path: {status, size, mtime, sha256, content(bytes)|None, error}}；另含 "__sidecar__"。
    """
    out: dict = {}
    if not paths:
        return out
    lit = ",".join("'" + str(p).replace("'", "''") + "'" for p in paths)
    last_err = "no-marker"
    for attempt in range(retries + 1):
        raw = ssh_ps(_PS_FETCH.replace("__PATHS__", lit), timeout=timeout)
        payload = None
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if line.startswith("__PATCHGUARD__"):
                payload = json.loads(line[len("__PATCHGUARD__"):])
                break
        if payload is not None:
            res_by_path = {str(f.get("path")): f for f in payload.get("files") or []}
            for p in paths:
                f = res_by_path.get(str(p))
                if f is None:
                    out[str(p)] = {"status": "ERROR", "error": "missing-in-ssh-output",
                                   "size": 0, "mtime": "", "sha256": None, "content": None}
                    continue
                st = str(f.get("status") or "ERROR")
                content = None
                sh = None
                if st == "OK" and f.get("b64"):
                    try:
                        content = base64.b64decode(f["b64"])
                        sh = sha256(content)
                    except Exception as e:  # noqa: BLE001
                        st, content = "ERROR", None
                        f["error"] = "b64-decode-failed: %s" % e
                out[str(p)] = {"status": st, "size": f.get("size") or 0, "mtime": f.get("mtime") or "",
                               "sha256": sh, "content": content, "error": f.get("error")}
            out["__sidecar__"] = payload.get("sidecar") or {}
            return out
        last_err = raw.strip()[-200:] or "empty-output"
        if attempt < retries:
            time.sleep(3)
    for p in paths:
        out.setdefault(str(p), {"status": "ERROR", "error": "ssh-output-no-marker(%s)" % last_err,
                                "size": 0, "mtime": "", "sha256": None, "content": None})
    out["__sidecar__"] = {}
    return out


def remote_ps(script: str, timeout: int = 180) -> str:
    return ssh_ps(script, timeout=timeout)


# ------------------------------------------------------------------ 断言判定

def run_checks(checks: list, text: str) -> list:
    """逐条跑断言；返回失败描述列表（空 = 全过）。"""
    fails = []
    for c in checks or []:
        ct = str(c.get("type") or "").lower()
        v = str(c.get("value") or "")
        if ct == "contains":
            if v not in text:
                fails.append("contains[%s]" % short(v))
        elif ct == "not_contains":
            if v in text:
                fails.append("not_contains[%s]" % short(v))
        elif ct == "regex":
            try:
                if not re.search(v, text, re.M):
                    fails.append("regex[%s]" % short(v))
            except re.error as e:
                fails.append("regex-invalid[%s](%s)" % (short(v), e))
        elif ct == "count":
            n = text.count(v)
            lo, hi, eq = c.get("min"), c.get("max"), c.get("eq")
            if eq is not None and n != int(eq):
                fails.append("count[%s]=%d(!=%d)" % (short(v), n, int(eq)))
            if lo is not None and n < int(lo):
                fails.append("count[%s]=%d(<%d)" % (short(v), n, int(lo)))
            if hi is not None and n > int(hi):
                fails.append("count[%s]=%d(>%d)" % (short(v), n, int(hi)))
        else:
            fails.append("unknown-check-type[%s]" % ct)
    return fails


def effective_path(patch: dict, overrides: dict) -> str:
    p = str(patch.get("file") or "")
    return overrides.get(p.lower(), p)


def sidecar_state(sidecar: dict, files_mtimes: list) -> tuple:
    """返回 (state, detail)。stale = 补丁文件比侧车进程还新（改了没重启）。"""
    start = str((sidecar or {}).get("start") or "")
    if not start or not files_mtimes:
        return "UNKNOWN", "sidecar-start-unknown"
    stale = [f["name"] for f in files_mtimes if f.get("mtime") and str(f["mtime"]) > start]
    if stale:
        return "STALE", ",".join(stale)
    return "OK", ""


# ------------------------------------------------------------------ 主流程

def load_manifest(path: str = MANIFEST_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def run_guard(manifest: dict, overrides: dict | None = None, timeout: int = 240) -> dict:
    overrides = {k.lower(): v for k, v in (overrides or {}).items()}
    patches = manifest.get("patches") or []
    targets = sorted({effective_path(p, overrides) for p in patches})
    fetched = remote_fetch(targets, timeout=timeout)
    results, missing, unknown, warns, fresh = [], [], [], [], []
    for p in patches:
        name = p.get("name")
        path = str(p.get("file") or "")
        tgt = effective_path(p, overrides)
        fr = fetched.get(tgt) or {"status": "ERROR", "error": "not-fetched"}
        row = {"name": name, "file": path, "effective_path": tgt,
               "purpose": p.get("purpose"), "verdict": "OK", "issues": [], "notes": [],
               "size": fr.get("size"), "mtime": fr.get("mtime"), "sha256": fr.get("sha256")}
        if fr.get("status") == "MISSING":
            row.update(verdict="MISSING", issues=["file-missing"])
        elif fr.get("status") != "OK" or fr.get("content") is None:
            row.update(verdict="UNKNOWN", issues=["read-failed(%s)" % short(fr.get("error") or fr.get("status"))])
        else:
            text = fr["content"].decode("utf-8", "replace")
            fails = run_checks(p.get("checks"), text)
            if fails:
                row.update(verdict="MISSING", issues=fails)
            exp = str(p.get("patched_sha256") or "")
            if exp and fr.get("sha256") != exp:
                row["notes"].append("hash-drift(记录 %s → 现 %s)" % (exp[:12], (fr.get("sha256") or "?")[:12]))
                if not fails:
                    row["verdict"] = "WARN"
                    warns.append(name)
            if not fails:
                fresh.append({"name": name, "mtime": fr.get("mtime")})
        if row["verdict"] == "MISSING":
            missing.append(name)
        elif row["verdict"] == "UNKNOWN":
            unknown.append(name)
        results.append(row)
    n_missing = len(missing)
    if n_missing:
        gate = "MISSING:%d" % n_missing
    elif unknown:
        gate = "UNKNOWN:" + ",".join(unknown)
    else:
        gate = "OK"
    loaded, loaded_detail = sidecar_state(fetched.get("__sidecar__") or {}, fresh)
    if overrides:
        # 沙箱/替身路径下「侧车是否加载」无意义（读的不是生产文件）⇒ 不报
        loaded, loaded_detail = "UNKNOWN", "override-map-active"
    return {"tool": "mas_patch_guard", "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ssh_host": SSH_HOST, "manifest": MANIFEST_PATH,
            "sidecar": fetched.get("__sidecar__") or {}, "patches": results,
            "missing": missing, "unknown": unknown, "warn": warns,
            "patch_guard": gate, "loaded": loaded, "loaded_detail": loaded_detail,
            "ok_count": len([r for r in results if r["verdict"] == "OK"])}


def _fmt_patch_row(r: dict) -> str:
    tail = "; ".join(r["issues"] + r["notes"])
    return "%-20s %-9s %-26s %s" % (r["name"], r["verdict"], r["file"].split("\\")[-1], tail)


def print_report(res: dict, mode: str) -> None:
    if mode == "json":
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    if mode == "check":
        for r in res["patches"]:
            if r["verdict"] != "OK":
                print("%s %s: %s" % (r["verdict"], r["name"], "; ".join(r["issues"] + r["notes"])))
        print("补丁闸门: %d 条, OK %d, 丢失 %d, 未知 %d, 漂移 %d, 侧车加载=%s, %s" % (
            len(res["patches"]), res["ok_count"], len(res["missing"]), len(res["unknown"]),
            len(res["warn"]), res["loaded"], res["generated_at"]))
        if res["missing"]:
            print("处理: " + "；".join(
                "%s → python tools/mas_patch_guard.py --reapply %s [--apply]" % (n, n) for n in res["missing"])
                + "（改完必须重启侧车、走变更窗口）")
        print("PATCH_GUARD=%s" % res["patch_guard"])
        if res["loaded"] == "STALE":
            print("PATCH_GUARD_LOADED=STALE:%s" % res["loaded_detail"])
        return
    # 详细
    print("AUTO-MAS 补丁闸门（只读）  %s" % res["generated_at"])
    sc = res.get("sidecar") or {}
    print("主机=%s  侧车 pid=%s start=%s" % (res["ssh_host"], sc.get("pid") or "-", sc.get("start") or "-"))
    print("")
    print("%-20s %-9s %-26s %s" % ("补丁", "判定", "文件", "问题/备注"))
    for r in res["patches"]:
        print(_fmt_patch_row(r))
    print("")
    for r in res["patches"]:
        p = None
        for pp in load_manifest()["patches"]:  # 只用于展示作用/回滚（清单读失败不影响判定）
            if pp.get("name") == r["name"]:
                p = pp
                break
        if p:
            print("  [%s] %s" % (r["name"], p.get("purpose")))
            print("        备份=%s" % "; ".join(b.get("name") for b in p.get("backups") or []))
            print("        回滚=%s" % p.get("rollback"))
    print("")
    if res["warn"]:
        print("哈希漂移（断言全过但文件与我们记录的不同，通常是升级后重打了补丁或另有改动）: %s" % ", ".join(res["warn"]))
    print("PATCH_GUARD=%s" % res["patch_guard"])
    print("PATCH_GUARD_LOADED=%s%s" % (res["loaded"], (":" + res["loaded_detail"]) if res["loaded_detail"] else ""))


# ------------------------------------------------------------------ 重放

def _payload_path(patch: dict) -> str:
    rel = ((patch.get("reapply") or {}).get("payload") or "").replace("/", os.sep)
    return os.path.join(ROOT, "runtime", "patches", rel) if rel else ""


def _diff_summary(cur: bytes, want: bytes, max_lines: int = 24) -> str:
    a = cur.decode("utf-8", "replace").splitlines()
    b = want.decode("utf-8", "replace").splitlines()
    diff = list(difflib.unified_diff(a, b, fromfile="当前", tofile="补丁版", lineterm="", n=1))
    changed = sum(1 for l in diff if l[:1] in "+-" and not l.startswith(("+++", "---")))
    head = "\n".join(diff[:max_lines])
    more = "" if len(diff) <= max_lines else "\n    …（共 %d 行 diff，只显示前 %d 行）" % (len(diff), max_lines)
    return "diff 摘要: 变更行 %d（当前 %d 行 → 补丁版 %d 行）\n%s%s" % (changed, len(a), len(b), head, more)


def do_reapply(manifest: dict, name: str, apply_: bool, force: bool, overrides: dict,
               timeout: int = 240) -> int:
    patch = next((p for p in manifest.get("patches") or [] if p.get("name") == name), None)
    if patch is None:
        print("REAPPLY=FAIL 未知补丁名 %r（可用: %s）" % (name, ", ".join(p.get("name") for p in manifest.get("patches") or [])))
        return 2
    path = str(patch.get("file") or "")
    tgt = effective_path(patch, {k.lower(): v for k, v in overrides.items()})
    pp = _payload_path(patch)
    if not pp or not os.path.exists(pp):
        print("REAPPLY=FAIL 缺少补丁版快照（payload）: %s —— 先在有补丁的机器上 --capture" % pp)
        return 2
    want = open(pp, "rb").read()
    want_sha = sha256(want)
    rec = str(patch.get("patched_sha256") or "")
    if rec and want_sha != rec:
        print("REAPPLY=FAIL payload 哈希与清单不符（payload=%s 清单=%s）—— 先核对/重录基线" % (want_sha[:12], rec[:12]))
        return 2
    fr = remote_fetch([tgt], timeout=timeout).get(tgt) or {}
    if fr.get("status") != "OK":
        print("REAPPLY=FAIL 读不到目标（%s: %s）—— 升级可能改了路径/删了文件，需人工确认" % (tgt, fr.get("error") or fr.get("status")))
        return 2
    cur = fr["content"]
    cur_sha = fr.get("sha256")
    print("REAPPLY 目标=%s" % tgt)
    print("  patch=%s  作用=%s" % (name, patch.get("purpose")))
    print("  当前 sha256=%s (%d B, mtime %s)" % ((cur_sha or "?")[:16], fr.get("size") or 0, fr.get("mtime")))
    print("  补丁版 sha256=%s (%d B, 录制于 %s)" % (want_sha[:16], len(want), patch.get("captured_at") or "?"))
    if cur_sha == want_sha and not force:
        print("REAPPLY=ALREADY_PATCHED（当前文件已等于补丁版，无需重放）")
        return 0
    fails = run_checks(patch.get("checks"), cur.decode("utf-8", "replace"))
    print("  当前文件断言: %s" % ("全部通过（漂移/强制重放）" if not fails else "失败 %d 条: %s" % (len(fails), "; ".join(fails))))
    print(_diff_summary(cur, want))
    if not apply_:
        ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        print("")
        print("DRY-RUN（零写入）。真重放请在批准后执行：")
        print("  python tools/mas_patch_guard.py --reapply %s --apply%s" % (name, " --force" if force else ""))
        print("  写着预期：① 备份 %s → %s.bak-patchguard-%s（校验一致才继续）"
              % (os.path.basename(tgt), os.path.basename(tgt), ts))
        print("             ② 上传补丁版（base64 走 stdin 整块写，终读 sha256 必须等于 %s；.py 先 py_compile）" % want_sha[:12])
        print("             ③ 回读复验断言 + 哈希 ⇒ REAPPLY=OK")
        print("  提醒：只改文件不动进程；**改完必须重启侧车才生效**（班次窗口 05:00-08:00 / 19:30-23:00 内禁止重启）")
        print("REAPPLY=DRY-RUN")
        return 0
    if NO_GO:
        print("REAPPLY=REFUSED MAAORCH_PATCH_NO_GO=1（硬闸）")
        return 2
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = "%s.bak-patchguard-%s" % (tgt, ts)
    st = remote_ps(r"""
$src = '__SRC__'; $dst = '__DST__'
Copy-Item -LiteralPath $src -Destination $dst -Force
if (-not (Test-Path -LiteralPath $dst)) { Write-Output 'BACKUP_FAILED'; exit }
$a = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLower()
$b = (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash.ToLower()
Write-Output ('BACKUP=' + $b + ' same=' + ($a -eq $b))
""".replace("__SRC__", tgt.replace("'", "''")).replace("__DST__", bak.replace("'", "''")), timeout=timeout)
    if "same=True" not in st:
        print("REAPPLY=FAIL 备份失败/校验不一致: %s" % short(st, 160))
        return 2
    print("  备份 OK: %s" % bak)
    tmp = tgt + ".patchguard-new"
    b64 = base64.b64encode(want).decode("ascii")
    # 大 payload 必须走 stdin：EncodedCommand 是把脚本塞进命令行，实测 65KB 的 base64 会被
    # Windows 拒成「命令行太长」（WinError 206）；stdin 无长度限制（单块实测 OK）。
    # stdin 模式（-Command -）按**行**执行 ⇒ 每条语句必须单行，多行 { } 块会静默截断（实测坑）。
    up = ssh_ps_stdin(r"""
$ErrorActionPreference='Continue'
$tmp = '__TMP__'; $dst = '__DST__'; $want = '__WANT__'
$b = [Convert]::FromBase64String('__B64__')
[IO.File]::WriteAllBytes($tmp, $b)
$h = (Get-FileHash -LiteralPath $tmp -Algorithm SHA256).Hash.ToLower()
Write-Output ('TMP_SHA=' + $h)
if ($h -ne $want) { Write-Output 'HASH_MISMATCH'; exit 1 }
$isPy = $dst.EndsWith('.py')
if ($isPy) { & '__PY__' -c "import py_compile,sys; py_compile.compile(sys.argv[1], doraise=True)" $tmp }
if ($isPy) { Write-Output ('PYCOMPILE=' + $LASTEXITCODE) }
if ($isPy -and $LASTEXITCODE -ne 0) { Write-Output 'COMPILE_FAILED'; exit 1 }
Move-Item -LiteralPath $tmp -Destination $dst -Force
Write-Output ('PLACED=' + (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash.ToLower())
""".replace("__TMP__", tmp.replace("'", "''")).replace("__DST__", tgt.replace("'", "''"))
   .replace("__WANT__", want_sha).replace("__B64__", b64)
   .replace("__PY__", r"E:\AUTO-MAS\environment\python\python.exe"), timeout=timeout)
    if "PLACED=%s" % want_sha not in up:
        print("REAPPLY=FAIL 上传/落位校验失败: %s" % short(up.strip(), 240))
        return 2
    fr2 = remote_fetch([tgt], timeout=timeout).get(tgt) or {}
    fails2 = run_checks(patch.get("checks"), (fr2.get("content") or b"").decode("utf-8", "replace"))
    ok = fr2.get("sha256") == want_sha and not fails2
    print("  回读: sha256=%s size=%s mtime=%s" % ((fr2.get("sha256") or "?")[:16], fr2.get("size"), fr2.get("mtime")))
    _ledger({"ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "kind": "reapply",
             "patch": name, "file": tgt, "backup": bak, "ok": bool(ok),
             "sha256": fr2.get("sha256"), "fails": fails2})
    print("REAPPLY=%s  backup=%s" % ("OK" if ok else "FAIL", bak))
    print("提醒：补丁已落盘但**未生效** —— 侧车是启动时导入、不热更；请在班次窗口外重启侧车（KILL 侧车→Electron 自动重拉→REST 40-50s 恢复）。")
    return 0 if ok else 1


# ------------------------------------------------------------------ 基线录制

def do_capture(manifest: dict, apply_: bool, force: bool, timeout: int = 240) -> int:
    patches = manifest.get("patches") or []
    targets = sorted({str(p.get("file")) for p in patches})
    fetched = remote_fetch(targets, timeout=timeout)
    changes, refusals = [], []
    for p in patches:
        path = str(p.get("file"))
        fr = fetched.get(path) or {}
        if fr.get("status") != "OK":
            refusals.append("%s: 读不到（%s）" % (p.get("name"), fr.get("error") or fr.get("status")))
            continue
        fails = run_checks(p.get("checks"), (fr.get("content") or b"").decode("utf-8", "replace"))
        if fails and not force:
            refusals.append("%s: 断言未全过，拒绝录制基线（%s）" % (p.get("name"), "; ".join(fails)))
            continue
        rel = ((p.get("reapply") or {}).get("payload") or "").strip()
        if not rel:
            refusals.append("%s: 清单缺 reapply.payload 路径" % p.get("name"))
            continue
        dst = os.path.join(ROOT, "runtime", "patches", rel.replace("/", os.sep))
        changes.append({"patch": p.get("name"), "file": path, "payload": dst,
                        "sha256": fr.get("sha256"), "size": fr.get("size"), "mtime": fr.get("mtime"),
                        "content": fr.get("content")})
    for c in changes:
        print("%s %-20s → %s (%d B, %s)" % ("[apply]" if apply_ else "[dry]", c["patch"],
                                            os.path.relpath(c["payload"], ROOT), c["size"], c["sha256"][:12]))
    for r in refusals:
        print("[skip ] %s" % r)
    if not apply_:
        print("CAPTURE=DRY-RUN（加 --apply 才写 runtime/patches/payloads/ 与清单哈希）")
        return 2 if refusals else 0
    os.makedirs(PAYLOAD_DIR, exist_ok=True)
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    by_patch = {c["patch"]: c for c in changes}
    for p in patches:
        c = by_patch.get(p.get("name"))
        if not c:
            continue
        os.makedirs(os.path.dirname(c["payload"]), exist_ok=True)
        with open(c["payload"], "wb") as fh:
            fh.write(c["content"])
        p["patched_sha256"] = c["sha256"]
        p["patched_size"] = c["size"]
        p["patched_mtime"] = c["mtime"]
        p["captured_at"] = now
    manifest["captured_at"] = now
    with open(MANIFEST_PATH, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("CAPTURE=OK payloads=%d manifest=%s captured_at=%s" % (len(changes), MANIFEST_PATH, now))
    return 0 if not refusals else 2


def _unified_from_row(row: dict) -> None:
    """补丁台账行 → 统一流：patch.<kind>（同 mas_audit._ad_patch）。"""
    kind = str(row.get("kind") or "").strip()
    if not kind:
        return
    params = {k: row.get(k) for k in ("patch", "backup", "sha256", "fails", "size", "mtime")
              if row.get(k) not in (None, "")}
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import mas_audit  # noqa: PLC0415
    mas_audit.safe_append(
        actor="tool:mas_patch_guard", action="patch." + re.sub(r"[^a-z0-9_]+", "_", kind.lower()),
        target=("file:" + str(row["file"])) if row.get("file") else "-",
        result="ok" if row.get("ok") else "fail", params=params, evidence=[LEDGER],
        card=os.environ.get("HERMES_KANBAN_TASK", ""),
        path=mas_audit.stream_for_runtime(os.path.dirname(LEDGER)))


def _ledger(row: dict) -> None:
    try:
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
    try:
        _unified_from_row(row)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ 自检（不连网）

def selftest() -> int:
    bad = 0
    cases = [
        # (label, checks, text, want_fail_count)
        ("contains-hit", [{"type": "contains", "value": "abc"}], "xxabcxx", 0),
        ("contains-miss", [{"type": "contains", "value": "abc"}], "xxabxx", 1),
        ("not_contains-ok", [{"type": "not_contains", "value": "abc"}], "xxabxx", 0),
        ("not_contains-bad", [{"type": "not_contains", "value": "abc"}], "xxabcxx", 1),
        ("regex-hit", [{"type": "regex", "value": r"AccountSwitchEnabled\"\] = True"}], 'task_set["StartUp"]["AccountSwitchEnabled"] = True', 0),
        ("regex-miss", [{"type": "regex", "value": r"AccountSwitchEnabled\"\] = True"}], 'task_set["StartUp"]["AccountSwitchEnabled"] = False', 1),
        ("count-eq-ok", [{"type": "count", "value": "A", "eq": 2}], "A-A", 0),
        ("count-min-bad", [{"type": "count", "value": "A", "min": 3}], "A-A", 1),
        ("count-max-bad", [{"type": "count", "value": "A", "max": 1}], "A-A", 1),
        ("multi", [{"type": "contains", "value": "ok"}, {"type": "not_contains", "value": "bad"}], "ok only", 0),
        ("multi-one-fail", [{"type": "contains", "value": "ok"}, {"type": "contains", "value": "nope"}], "ok only", 1),
        ("unknown-type", [{"type": "wat", "value": "x"}], "x", 1),
    ]
    for label, checks, text, want in cases:
        fails = run_checks(checks, text)
        ok = len(fails) == want
        bad += 0 if ok else 1
        print("%s %-18s fails=%d want=%d %s" % ("PASS" if ok else "FAIL", label, len(fails), want,
                                                "; ".join(fails)))
    # 清单 schema 校验
    try:
        m = load_manifest()
        names = []
        for p in m.get("patches") or []:
            names.append(p.get("name"))
            for k in ("name", "file", "purpose", "checks", "backups", "rollback", "reapply"):
                if not p.get(k):
                    bad += 1
                    print("FAIL schema: 补丁 %s 缺字段 %s" % (p.get("name"), k))
            rel = ((p.get("reapply") or {}).get("payload") or "")
            if rel and not os.path.exists(os.path.join(ROOT, "runtime", "patches", rel.replace("/", os.sep))):
                print("FAIL schema: 补丁 %s 的 payload 不存在: %s（先 --capture --apply）" % (p.get("name"), rel))
                bad += 1
        dupes = [n for n in set(names) if names.count(n) > 1]
        if dupes:
            bad += 1
            print("FAIL schema: 补丁名重复 %s" % dupes)
        print("PASS schema: patches=%d 名称=%s" % (len(names), ", ".join(names)))
    except Exception as e:  # noqa: BLE001
        bad += 1
        print("FAIL schema: 清单读不了: %s" % e)
    print("selftest: %s (%d 失败)" % ("OK" if not bad else "FAIL", bad))
    return 0 if not bad else 1


# ------------------------------------------------------------------ CLI

def _load_overrides(arg: str) -> dict:
    if not arg:
        return {}
    with open(arg, "r", encoding="utf-8") as fh:
        return {str(k): str(v) for k, v in json.load(fh).items()}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="AUTO-MAS 补丁持久化闸门（只读为主）")
    ap.add_argument("--check", action="store_true", help="紧凑模式（巡检用）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--selftest", action="store_true", help="本地规则自检（不连网）")
    ap.add_argument("--reapply", metavar="NAME", help="重放某补丁（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="配合 --reapply/--capture 真写")
    ap.add_argument("--force", action="store_true", help="重放：文件已等于补丁版也照走；capture：断言未全过也录")
    ap.add_argument("--capture", action="store_true", help="重新录制 payload/哈希基线")
    ap.add_argument("--override-map", metavar="FILE", help="测试用：{远端路径: 替身路径} 读改到替身（自测沙箱）")
    ap.add_argument("--manifest", default=MANIFEST_PATH)
    ap.add_argument("--timeout", type=int, default=240)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    try:
        manifest = load_manifest(a.manifest)
    except Exception as e:  # noqa: BLE001
        print("PATCH_GUARD=UNKNOWN:manifest-unreadable(%s)" % short(e))
        return 2
    overrides = _load_overrides(a.override_map)
    try:
        if a.reapply:
            return do_reapply(manifest, a.reapply, a.apply, a.force, overrides, timeout=a.timeout)
        if a.capture:
            return do_capture(manifest, a.apply, a.force, timeout=a.timeout)
        res = run_guard(manifest, overrides=overrides, timeout=a.timeout)
    except Exception as e:  # noqa: BLE001 —— 任何异常都 fail-closed，绝不当作 OK
        tag = "REAPPLY" if a.reapply else ("CAPTURE" if a.capture else "PATCH_GUARD")
        print("%s=%s 异常: %s: %s" % (tag, "FAIL" if a.reapply else "UNKNOWN",
                                      type(e).__name__, str(e)[:300]))
        return 2
    print_report(res, "json" if a.json else ("check" if a.check else "full"))
    if res["patch_guard"].startswith("MISSING"):
        return 1
    if res["patch_guard"].startswith("OK"):
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
