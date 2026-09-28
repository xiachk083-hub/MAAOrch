#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""storage_waterline.py —— 存储水位只读巡检（AUTO-MAS 主机 + 本机 MAAOrch）

盯「会不会被日志/镜像撑爆」：盘剩余、MuMu 实例镜像总量、MAA debug 日志/截图、
AUTO-MAS history、回收站、Public\\mumu 的实例导出残留、可再建实例数、
**本机 runtime/audit/ 统一审计流**（live + archive：体积 / 最老文件天数 / 趋势）。

红线：**只读**。本工具只跑查询命令（CIM 查询 / Get-ChildItem / Measure-Object），
不删除、不改名、不动任何配置。清理动作见 docs/audits/2026-09-27_storage_policy.md。

用法：
  python tools/storage_waterline.py               # 全量（含远端主机）
  python tools/storage_waterline.py --json        # 机器可读
  python tools/storage_waterline.py --local-only  # 只看本机（不连主机）
  python tools/storage_waterline.py --runtime DIR # 本机 runtime 目录（默认 <repo>/runtime；演练/异机）
  python tools/storage_waterline.py --deep        # 追加整树扫描（E:\\MAA-Multi / E:\\AUTO-MAS 全量，慢）

环境变量：MAAORCH_MAS_HOST（默认 Administrator@100.79.173.69）、MAA_SSH_KEY。

退出码：0 全绿；2 有黄灯（WARN）；3 有红灯（CRIT）；1 取数失败。
"""

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SSH_KEY = os.environ.get("MAA_SSH_KEY", str(Path.home() / ".ssh" / "maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_MAS_HOST", "Administrator@100.79.173.69")
REMOTE_PROBE = r"C:\Windows\Temp\mas_storage_probe.ps1"

# ---- 阈值（GB / MB，集中在这里改） ----
DISK = {
    "host_C": {"warn_free_gb": 35, "crit_free_gb": 15},   # 一次实例导出会在 C 落 ~24GB
    "host_E": {"warn_free_gb": 120, "crit_free_gb": 60},  # 一个 MuMu 实例 ≈ 29-32GB
    "local_C": {"warn_free_gb": 60, "crit_free_gb": 30},
    "local_D": {"warn_free_gb": 60, "crit_free_gb": 30},
}
MAA_DEBUG_WARN_MB = 400
MAA_DEBUG_CRIT_MB = 600
MAA_PNG_STALE_DAYS = 45
HIST_WARN_MB = 500
HIST_CRIT_MB = 1000
RECYCLE_WARN_GB = 20
PUBLIC_WARN_GB = 1  # Public\mumu 下任何 *.mumudata 残留都会报

# —— 本机 runtime/audit/（统一审计流）阈值：**与 tools/audit_waterline.py 同口径**（改一处要同步）——
AUDIT_WARN_MB = 10.0           # live > 10 MiB ⇒ WARN（= mas_audit.LEGACY_WARN_BYTES 口径）
AUDIT_CRIT_MB = 25.0           # live > 25 MiB ⇒ CRIT
AUDIT_ROTATE_MB = 8.0          # 轮转阈值（= mas_audit.MAX_MB_DEFAULT）；归档缺失而 live 超它 ⇒ CRIT
AUDIT_ARCHIVE_STALE_DAYS = 90  # 归档保留期（超期只提示「人工 prune」，不升级判据）

PROBE_PS1 = r'''
$ErrorActionPreference='SilentlyContinue'
Write-Output 'WATERLINE-BEGIN'
foreach($d in @('C:','E:')){
  $ld = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$d'"
  if($ld){ Write-Output ("DISK|{0}|{1}|{2}" -f $d, [math]::Round($ld.FreeSpace/1GB,1), [math]::Round($ld.Size/1GB,1)) }
}
foreach($d in @('C:','E:')){
  $rb = @(Get-ChildItem -LiteralPath ("{0}\`$RECYCLE.BIN" -f $d) -File -Recurse -Force -ErrorAction SilentlyContinue)
  $s = ($rb | Measure-Object -Sum Length).Sum
  if($null -eq $s){ $s = 0 }
  Write-Output ("RECYCLE|{0}|{1}|{2}" -f $d, [math]::Round($s/1GB,2), $rb.Count)
}
foreach($p in @('E:\MAA-Multi','E:\AUTO-MAS\history','E:\AUTO-MAS\debug','C:\Users\Public\mumu')){
  if(-not (Test-Path -LiteralPath $p)){ continue }
  $f = @(Get-ChildItem -LiteralPath $p -File -Recurse -Force -ErrorAction SilentlyContinue)
  $s = ($f | Measure-Object -Sum Length).Sum
  if($null -eq $s){ $s = 0 }
  Write-Output ("SIZE|{0}|{1}|{2}" -f $p.Replace('|','/'), [math]::Round($s/1MB,1), $f.Count)
}
$v = Get-ChildItem -LiteralPath 'E:\MuMu Player 12\vms' -Directory -ErrorAction SilentlyContinue
$vf = @(Get-ChildItem -LiteralPath 'E:\MuMu Player 12\vms' -File -Recurse -Force -ErrorAction SilentlyContinue)
$vs = ($vf | Measure-Object -Sum Length).Sum
if($null -eq $vs){ $vs = 0 }
Write-Output ("VMS|{0}|{1}|{2}" -f $v.Count, [math]::Round($vs/1GB,1), $vf.Count)
$md = @(Get-ChildItem -LiteralPath 'C:\Users\Public\mumu' -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*.mumudata' })
$mds = ($md | Measure-Object -Sum Length).Sum
if($null -eq $mds){ $mds = 0 }
Write-Output ("MUMUDATA|{0}|{1}" -f $md.Count, [math]::Round($mds/1GB,2))
$md | ForEach-Object { Write-Output ("MUMUDATA_FILE|{0}|{1}" -f $_.LastWriteTime.ToString('yyyy-MM-dd'), [math]::Round($_.Length/1GB,2)) }
foreach($d in Get-ChildItem -LiteralPath 'E:\MAA-Multi' -Directory -ErrorAction SilentlyContinue){
  $dbg = Join-Path $d.FullName 'debug'
  if(-not (Test-Path -LiteralPath $dbg)){ continue }
  $f = @(Get-ChildItem -LiteralPath $dbg -File -Recurse -Force -ErrorAction SilentlyContinue)
  $s = ($f | Measure-Object -Sum Length).Sum
  if($null -eq $s){ $s = 0 }
  $png = @($f | Where-Object { $_.Name -like '*_raw.png' })
  $ps = ($png | Measure-Object -Sum Length).Sum
  if($null -eq $ps){ $ps = 0 }
  $old = ($png | Sort-Object LastWriteTime | Select-Object -First 1)
  $new = ($png | Sort-Object LastWriteTime | Select-Object -Last 1)
  Write-Output ("MAA|{0}|{1}|{2}|{3}|{4}|{5}|{6}" -f $d.Name, [math]::Round($s/1MB,1), $png.Count, [math]::Round($ps/1MB,1), $(if($old){$old.LastWriteTime.ToString('yyyy-MM-dd')}else{'-'}), $(if($new){$new.LastWriteTime.ToString('yyyy-MM-dd')}else{'-'}), $f.Count)
}
$inst = & 'E:\MuMu Player 12\nx_main\MuMuManager.exe' info -v all 2>$null
if($inst){
  try { $o = $inst | Out-String | ConvertFrom-Json; Write-Output ("INSTANCES|{0}" -f ($o.PSObject.Properties.Count)) } catch { Write-Output 'INSTANCES|?' }
}
Write-Output 'WATERLINE-END'
'''


def log(msg):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def ssh_run(cmd, timeout=120):
    p = subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=12", SSH_HOST, cmd],
        capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
    )
    if p.returncode != 0:
        raise RuntimeError(f"ssh 失败({p.returncode}): {p.stderr.decode('utf-8', 'replace')[:200]}")
    return p.stdout.decode("utf-8", "replace")


def push_probe():
    local = Path(tempfile.gettempdir()) / "mas_storage_probe.ps1"
    local.write_text(PROBE_PS1, encoding="utf-8")
    p = subprocess.run(["scp", "-O", "-q", "-i", SSH_KEY, "-o", "BatchMode=yes",
                        "-o", "StrictHostKeyChecking=no", str(local),
                        f"{SSH_HOST}:C:/Windows/Temp/mas_storage_probe.ps1"],
                       capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    if p.returncode != 0:
        raise RuntimeError("scp 失败: " + p.stderr.decode("utf-8", "replace")[:200])


def fetch_remote(deep=False):
    push_probe()
    raw = ssh_run(f'powershell -NoProfile -ExecutionPolicy Bypass -File "{REMOTE_PROBE}"', timeout=300)
    out = {"disks": {}, "recycle": {}, "sizes": {}, "maas": {}, "vms": None, "mumudata": None, "instances": None}
    for line in raw.splitlines():
        parts = line.strip().split("|")
        if not parts or not parts[0]:
            continue
        try:
            if parts[0] == "DISK":
                out["disks"][parts[1]] = {"free_gb": float(parts[2]), "total_gb": float(parts[3])}
            elif parts[0] == "RECYCLE":
                out["recycle"][parts[1]] = {"gb": float(parts[2]), "files": int(parts[3])}
            elif parts[0] == "SIZE":
                out["sizes"][parts[1]] = {"mb": float(parts[2]), "files": int(parts[3])}
            elif parts[0] == "MAA":
                out["maas"][parts[1]] = {"debug_mb": float(parts[2]), "png_n": int(parts[3]),
                                         "png_mb": float(parts[4]), "png_oldest": parts[5],
                                         "png_newest": parts[6], "files": int(parts[7])}
            elif parts[0] == "VMS":
                out["vms"] = {"dirs": int(parts[1]), "gb": float(parts[2]), "files": int(parts[3])}
            elif parts[0] == "MUMUDATA":
                out["mumudata"] = {"n": int(parts[1]), "gb": float(parts[2])}
            elif parts[0] == "INSTANCES":
                out["instances"] = parts[1]
        except (IndexError, ValueError):
            continue
    if not out["disks"]:
        raise RuntimeError("远端探针没回数（检查主机可达/PS 执行）")
    if deep:
        pass  # 预留：需要时可加整树扫描
    return out


def du(path):
    total = 0
    n = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.stat(os.path.join(root, f)).st_size
                n += 1
            except OSError:
                pass
    return total, n


def audit_stats(runtime_dir):
    """本机 runtime/audit/（统一审计流）水位：live + archive 的 体积 / 最老文件天数 / 趋势。

    字段口径与 tools/audit_waterline.py 同款（同一套阈值：warn>10MB / crit>25MB /
    归档缺失且 live>8MB ⇒ crit）；趋势 = 毛量 MB/天（按 live+归档文件 mtime 跨度估，跨度
    <1 天按 1 天算，避免放大）。只读：只 stat/扫描，永不 rotate/prune（清理是人拍板）。
    """
    d = Path(runtime_dir) / "audit"
    if not d.is_dir():
        return None
    out = {"dir": str(d), "total_mb": 0.0, "files": 0, "live_mb": 0.0, "live_bytes": 0,
           "archives_n": 0, "archive_mb": 0.0, "oldest_days": None, "oldest_date": None,
           "newest_date": None, "span_days": None, "mb_per_day": None, "level": "OK",
           "note": "", "stale_archives_n": 0}
    total = 0
    mtimes = []
    for root, _dirs, files in os.walk(str(d)):
        for f in files:
            p = os.path.join(root, f)
            try:
                stt = os.stat(p)
            except OSError:
                continue
            total += stt.st_size
            mtimes.append(stt.st_mtime)
            out["files"] += 1
            rel = os.path.relpath(p, str(d)).replace("\\", "/")
            if rel.startswith("archive/"):
                out["archives_n"] += 1
                out["archive_mb"] += stt.st_size / 1e6
                if (time.time() - stt.st_mtime) / 86400 > AUDIT_ARCHIVE_STALE_DAYS:
                    out["stale_archives_n"] += 1
            elif f == "mas_audit.jsonl":
                out["live_bytes"] = stt.st_size
                out["live_mb"] = stt.st_size / 1e6
    out["total_mb"] = round(total / 1e6, 2)
    out["live_mb"] = round(out["live_mb"], 2)
    out["archive_mb"] = round(out["archive_mb"], 2)
    if mtimes:
        oldest, newest = min(mtimes), max(mtimes)
        out["oldest_days"] = round((time.time() - oldest) / 86400, 1)
        out["oldest_date"] = dt.datetime.fromtimestamp(oldest).strftime("%Y-%m-%d")
        out["newest_date"] = dt.datetime.fromtimestamp(newest).strftime("%Y-%m-%d")
        span = (newest - oldest) / 86400
        out["span_days"] = round(span, 2)
        out["mb_per_day"] = round(out["total_mb"] / max(span, 1.0), 3)
    if out["live_mb"] > AUDIT_CRIT_MB:
        out["level"] = "CRIT"
        out["note"] = "live=%.1fMB>%.0fMB（轮转疑似失效）" % (out["live_mb"], AUDIT_CRIT_MB)
    elif out["archives_n"] == 0 and out["live_mb"] > AUDIT_ROTATE_MB:
        out["level"] = "CRIT"
        out["note"] = "归档缺失（0 份）而 live=%.1fMB>轮转阈值%.0fMB" % (out["live_mb"], AUDIT_ROTATE_MB)
    elif out["live_mb"] > AUDIT_WARN_MB:
        out["level"] = "WARN"
        out["note"] = "live=%.1fMB>%.0fMB" % (out["live_mb"], AUDIT_WARN_MB)
    return out


def local_stats(runtime_dir=None):
    out = {"disks": {}, "runtime": None, "audit": None}
    for d in ("C:\\", "D:\\"):
        try:
            u = shutil.disk_usage(d)
            out["disks"][d] = {"free_gb": round(u.free / 1e9, 1), "total_gb": round(u.total / 1e9, 1)}
        except OSError:
            pass
    rt = Path(runtime_dir) if runtime_dir else (ROOT / "runtime")
    if rt.is_dir():
        sz, n = du(str(rt))
        out["runtime"] = {"mb": round(sz / 1e6, 1), "files": n}
    out["audit"] = audit_stats(str(rt))
    return out


def main():
    ap = argparse.ArgumentParser(description="存储水位只读巡检")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--local-only", action="store_true", help="只看本机")
    ap.add_argument("--runtime", default=os.environ.get("MAAORCH_RUNTIME_DIR") or "",
                    help="本机 runtime 目录（默认 <repo>/runtime；演练/异机时可指到假目录）")
    args = ap.parse_args()

    findings = []   # (level, text)

    def add(level, text):
        findings.append((level, text))

    try:
        loc = local_stats(args.runtime or None)
    except Exception as e:
        print(f"本机取数失败: {e}", file=sys.stderr)
        return 1

    for d, key in (("C:\\", "local_C"), ("D:\\", "local_D")):
        if d in loc["disks"]:
            f = loc["disks"][d]["free_gb"]
            th = DISK[key]
            if f < th["crit_free_gb"]:
                add("CRIT", f"本机 {d} 仅剩 {f}GB（< {th['crit_free_gb']}GB）")
            elif f < th["warn_free_gb"]:
                add("WARN", f"本机 {d} 剩 {f}GB（< {th['warn_free_gb']}GB）")

    a = loc.get("audit")
    if a:
        if a["level"] == "CRIT":
            add("CRIT", f"本机 runtime/audit 统一流 {a['note']}（轮转疑似失效；先人工确认再 "
                        f"python tools/mas_audit.py rotate --apply —— 巡检/本工具从不自动清理）")
        elif a["level"] == "WARN":
            add("WARN", f"本机 runtime/audit 统一流 {a['note']}（阈值口径同 tools/audit_waterline.py）")
        if a["stale_archives_n"]:
            add("INFO", f"runtime/audit 归档 {a['stale_archives_n']} 份超 {AUDIT_ARCHIVE_STALE_DAYS} 天保留期"
                        f" ⇒ prune 候选（人工拍板；默认 dry-run）")

    remote = None
    if not args.local_only:
        try:
            remote = fetch_remote()
        except Exception as e:
            print(f"远端取数失败: {e}", file=sys.stderr)

    if remote:
        for d, key in (("C:", "host_C"), ("E:", "host_E")):
            if d in remote["disks"]:
                f = remote["disks"][d]["free_gb"]
                th = DISK[key]
                if f < th["crit_free_gb"]:
                    add("CRIT", f"主机 {d} 仅剩 {f}GB（< {th['crit_free_gb']}GB）")
                elif f < th["warn_free_gb"]:
                    add("WARN", f"主机 {d} 剩 {f}GB（< {th['warn_free_gb']}GB）")
        for d, r in remote["recycle"].items():
            if r["gb"] > RECYCLE_WARN_GB:
                add("WARN", f"主机 {d}\\$RECYCLE.BIN = {r['gb']}GB / {r['files']} 文件（建议人工清空）")
        h = remote["sizes"].get(r"E:\AUTO-MAS\history")
        if h:
            if h["mb"] > HIST_CRIT_MB:
                add("CRIT", f"AUTO-MAS history = {h['mb']}MB（> {HIST_CRIT_MB}MB）")
            elif h["mb"] > HIST_WARN_MB:
                add("WARN", f"AUTO-MAS history = {h['mb']}MB（> {HIST_WARN_MB}MB）")
        mu = remote["mumudata"]
        if mu and mu["n"] > 0:
            add("WARN", f"C:\\Users\\Public\\mumu 有 {mu['n']} 个 *.mumudata 残留 = {mu['gb']}GB"
                        f"（实例导出体；不清会一直占 C 盘，且下次克隆还要再占一份）")
        for name, m in sorted(remote["maas"].items()):
            if m["debug_mb"] > MAA_DEBUG_CRIT_MB:
                add("CRIT", f"{name} debug = {m['debug_mb']}MB（> {MAA_DEBUG_CRIT_MB}MB）")
            elif m["debug_mb"] > MAA_DEBUG_WARN_MB:
                add("WARN", f"{name} debug = {m['debug_mb']}MB（> {MAA_DEBUG_WARN_MB}MB）")
            if m["png_oldest"] not in ("-", ""):
                try:
                    age = (dt.date.today() - dt.datetime.strptime(m["png_oldest"], "%Y-%m-%d").date()).days
                    if age > MAA_PNG_STALE_DAYS and m["png_mb"] > 50:
                        add("WARN", f"{name} 错误截图最老 {m['png_oldest']}（{age} 天，{m['png_mb']}MB）→ 可按策略清")
                except ValueError:
                    pass
        if remote["vms"]:
            add("INFO", f"MuMu 实例镜像 {remote['vms']['dirs']} 个 × 合计 {remote['vms']['gb']}GB")

    if remote and "E:" in remote["disks"]:
        free = remote["disks"]["E:"]["free_gb"]
        add("INFO", f"E: 剩余 {free}GB ⇒ 还能再建约 {int(max(0, (free - 20)) // 32)} 个 MuMu 实例（按 32GB/个并留 20GB 余量）")

    levels = [lv for lv, _ in findings]
    worst = 3 if "CRIT" in levels else (2 if "WARN" in levels else 0)

    if args.json:
        print(json.dumps({
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "local": loc, "remote": remote,
            "findings": [{"level": lv, "text": t} for lv, t in findings],
            "worst": {"0": "OK", "2": "WARN", "3": "CRIT"}[str(worst)],
        }, ensure_ascii=False, indent=2))
        return worst

    print("=" * 78)
    print(f"存储水位（{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}） 只读巡检")
    print("=" * 78)
    for d, v in loc["disks"].items():
        print(f"本机 {d:<4} 剩余 {v['free_gb']:>7} GB / 共 {v['total_gb']} GB")
    if loc["runtime"]:
        print(f"本机 MAAOrch runtime: {loc['runtime']['mb']} MB / {loc['runtime']['files']} 文件")
    if loc.get("audit"):
        a = loc["audit"]
        print(f"本机 runtime/audit 审计流: {a['total_mb']} MB / {a['files']} 文件"
              f"（live {a['live_mb']} MB / 归档 {a['archives_n']} 份 {a['archive_mb']} MB；"
              f"最老 {a['oldest_days']} 天（{a['oldest_date']}）；趋势 {a['mb_per_day']} MB/天）")
    if remote:
        for d, v in remote["disks"].items():
            print(f"主机 {d:<4} 剩余 {v['free_gb']:>7} GB / 共 {v['total_gb']} GB")
        for d, r in remote["recycle"].items():
            print(f"主机回收站 {d}\\$RECYCLE.BIN: {r['gb']} GB / {r['files']} 文件")
        for p, s in remote["sizes"].items():
            print(f"主机 {p}: {s['mb']} MB / {s['files']} 文件")
        if remote["vms"]:
            print(f"主机 MuMu 镜像 vms: {remote['vms']['dirs']} 目录 {remote['vms']['gb']} GB")
        for name, m in sorted(remote["maas"].items()):
            print(f"  {name:<7} debug={m['debug_mb']:>7}MB  截图 {m['png_mb']:>6}MB/{m['png_n']}张 "
                  f"（{m['png_oldest']}..{m['png_newest']}）")
    print("-" * 78)
    if not findings:
        print("结论：全绿，无超阈项。")
    for lv, t in findings:
        print(f"{lv:<5} {t}")
    print("-" * 78)
    print("结论：" + {"0": "OK（全绿）", "2": "WARN（有黄灯，按审计文档执行清理）", "3": "CRIT（有红灯，先停建新实例）"}[str(worst)])
    return worst


if __name__ == "__main__":
    sys.exit(main())
