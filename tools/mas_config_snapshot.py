#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""mas_config_snapshot.py — AUTO-MAS 配置快照与一键回滚（定期备份 + diff + restore + 漂移检测 + 演练）。

背景：E:\AUTO-MAS 的配置（config\*.json）、每脚本/每用户的 MAA 配置目录（data\**）
与关键代码补丁（app\task\MAA\AutoProxy.py 等）此前没有定期快照——一次误操作或 MAA
升级覆盖就可能把 60 个号的配置搞乱。本工具给出：快照 → 差异 → 回滚 → 演练 的闭环。

双存储模型（凭据红线是这么定的，别改）：
  * 敏感文件（含凭据键值，如 ScriptConfig.json 的 DPAPI 密文 Password）：
      - 全保副本只放**主机侧** E:\AUTO-MAS\backups\cfgsnap_<ts>\（密文不离开主机，遵
        tools/redact_scriptconfig.py 的口径「raw 不留，留脱敏版」）；
      - 本地快照只存**脱敏副本**（redacted/）＋原始哈希（供 diff 与漂移比对）。
  * 非敏感文件：本地快照存全保副本（files/），回滚时从本地推回。
  * 回滚时敏感文件从主机侧 store 复拷（远程侧 Copy-Item），非敏感文件从本地推。

子命令（默认动作 = 快照）：
  python tools/mas_config_snapshot.py                       # 快照（远程生产，只读读取 + 写主机 backups）
  python tools/mas_config_snapshot.py --label pre-change    # 变更前快照（约定）
  python tools/mas_config_snapshot.py --list [--json]       # 列出快照
  python tools/mas_config_snapshot.py --diff A B [--only G] # 两次快照差异（文件级 + 字段级）
  python tools/mas_config_snapshot.py --restore <ts>        # 一键回滚（默认 dry-run，逐文件显示将覆盖什么）
  python tools/mas_config_snapshot.py --restore <ts> --apply   # 真回滚（生产根自动前置快照 + 复读校验）
  python tools/mas_config_snapshot.py --restore <ts> --apply --to <dir>          # 本地临时副本（演练用）
  python tools/mas_config_snapshot.py --restore <ts> --apply --remote-root <dir> # 远程临时副本（演练用）
  python tools/mas_config_snapshot.py --drift [--against <ts>]  # 当前生产 vs 最近快照（只读哈希扫描）
  python tools/mas_config_snapshot.py --drill [--against <ts>]  # 恢复演练（本地临时副本 + 主机 scratch，不碰生产）
  python tools/mas_config_snapshot.py --selftest            # 纯离线自检（不连网）

退出码：0=OK；1=--drift 发现漂移；2=传输/读取失败；3=回滚有失败项；4=自检/演练失败；5=被闸门拒绝。

产出：runtime/snapshots/<ts>/{manifest.json, files/…, redacted/…}
      主机侧 runtime 无关的 store：E:\AUTO-MAS\backups\cfgsnap_<ts>\
审计：runtime/mas_config_snapshot_log.jsonl（每次动作一行，无凭据值）
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import zipfile

if hasattr(sys.stdout, "reconfigure"):                          # Windows 控制台中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
RUNTIME = os.environ.get("MAAORCH_SNAPSHOT_RUNTIME") or os.path.join(ROOT, "runtime")
SNAPS = os.path.join(RUNTIME, "snapshots")
DRILLS = os.path.join(SNAPS, "_drills")
LOCK = os.path.join(RUNTIME, "mas_config_snapshot.lock")
LOG = os.path.join(RUNTIME, "mas_config_snapshot_log.jsonl")

SSH_KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")
REMOTE_ROOT = os.environ.get("MAAORCH_SNAPSHOT_REMOTE_ROOT", r"E:\AUTO-MAS")
REMOTE_BACKUPS = REMOTE_ROOT + r"\backups"
STORE_PREFIX = "cfgsnap_"
DRILL_PREFIX = "_cfgsnap_drill_"

# 关键补丁文件（必选；再叠加「有 .bak* 兄弟文件」的自动发现，见 scan）
KNOWN_PATCHES = (r"app\task\MAA\AutoProxy.py", r"app\utils\emulator\mumu.py", r"app\utils\constants.py")

# 敏感键口径与 tools/redact_scriptconfig.py 对齐（同集合、同正则、同标记）
SENS_EXACT = {"Password", "SklandToken", "ServerChanKey", "Notify_CustomWebhooks"}
SENS_RE = re.compile(r"(pass|token|secret|cookie)", re.I)
MARK = "[已脱敏]"
LONG_B64 = re.compile(r"[A-Za-z0-9+/=]{60,}")   # 兜底：脱敏后仍出现的长 base64 串 => 拒绝写盘

TS_FMT = "%Y-%m-%d %H:%M:%S"
SNAP_RE = re.compile(r"^\d{8}_\d{6}(__[0-9A-Za-z_\-]+)?$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

KEEP_DEFAULT = 30
DRILL_KEEP = 10
LOCK_STALE_S = 2 * 3600
BATCH_BYTES = 2_500_000          # 单次 ssh 传输的原始字节上限（fetch/upload 分批）
BATCH_FILES = 60

# 上传（本机 → 主机）通道口径：Windows sshd 对大 stdin 载荷会**随机死锁**——2026-09-28 实测
# payload(64KB)≈5/6 成功、256KB≈3/6、1.6MB≈0/16；卡死的远端进程连脚本体都进不去（marker 不落盘），
# 且每次卡死会在主机留一个僵尸 powershell（累积 38 个）。因此上传改为：
#   主通道 = HTTP 拉取：本机起一次性 HTTP 服务，主机 Invoke-WebRequest 拉 zip → 解包落盘（8MB ≈ 5s 实测）
#   兜底   = stdin 小分片 + 每片重试（仅在 HTTP 通道不可用 / 有失败项时使用；慢，但能收敛）
STDIN_UPLOAD_CAP = 48_000        # stdin 兜底通道：单次原始字节上限（base64 后 ≈64KB）
UPLOAD_ATTEMPTS = 4              # stdin 兜底通道：每片最多尝试次数
UPLOAD_ATTEMPT_TIMEOUT = 30      # stdin 兜底通道：单次尝试超时（秒；卡死必须快速重试）
PULL_TTL_S = 600                 # HTTP 拉取通道：主机侧下载超时（秒）


class TransportError(RuntimeError):
    """REST/SSH 传输失败。"""


class SnapshotError(RuntimeError):
    """快照/回滚流程错误。"""


# ---------------------------------------------------------------- 基础工具

def _now() -> str:
    return dt.datetime.now().strftime(TS_FMT)


def log(msg: str) -> None:
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=False)
    os.replace(tmp, path)


def _unified_from_audit(rec: dict) -> None:
    """事件行（mas_config_snapshot_log）→ 统一流：config.<action>（同 mas_audit._ad_snapshot）。"""
    act = str(rec.get("action") or "").strip()
    if not act:
        return
    ok = rec.get("ok")
    res = "ok" if ok is True or (isinstance(ok, int) and not isinstance(ok, bool)) else (
        "fail" if ok is False else "info")
    params = {k: rec.get(k) for k in ("label", "files", "sensitive", "store", "dir", "root",
                                      "root_kind", "failed", "skipped", "verify_bad", "restored",
                                      "drift", "against", "drill", "steps", "reason", "source")
              if rec.get(k) not in (None, "")}
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import mas_audit  # noqa: PLC0415
    mas_audit.safe_append(
        actor="tool:mas_config_snapshot", action="config." + re.sub(r"[^a-z0-9_]+", "_", act.lower()),
        target=("snapshot:" + str(rec["snapshot"])) if rec.get("snapshot") else "-", result=res,
        reason=str(rec.get("reason") or "")[:300], params=params, evidence=[LOG],
        card=os.environ.get("HERMES_KANBAN_TASK", ""),
        path=mas_audit.stream_for_runtime(RUNTIME))


def audit(rec: dict) -> None:
    """追加一行审计（无凭据值）。"""
    os.makedirs(RUNTIME, exist_ok=True)
    rec = dict(rec)
    rec.setdefault("ts", _now())
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    try:
        _unified_from_audit(rec)
    except Exception:  # noqa: BLE001
        pass


def norm_rel(p: str) -> str:
    """归一相对路径为反斜杠形式；拒绝越界与绝对路径。"""
    s = str(p or "").strip().replace("/", "\\")
    s = re.sub(r"\\+", "\\\\", s).strip("\\")
    if not s or s.startswith("\\\\") or ":" in s:
        raise SnapshotError("非法相对路径: %r" % p)
    parts = [x for x in s.split("\\") if x]
    if any(x == ".." for x in parts):
        raise SnapshotError("路径越界: %r" % p)
    return "\\".join(parts)


def rel_to_local(base: str, rel: str) -> str:
    return os.path.join(base, *[x for x in norm_rel(rel).split("\\")])


def kind_of(rel: str) -> str:
    r = rel.lower()
    if r.startswith("config\\"):
        return "config"
    if r.startswith("data\\"):
        return "data"
    if r.startswith("app\\"):
        return "patch"
    return "other"


def ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def ps_list(items) -> str:
    return ",".join(ps_quote(x) for x in items)


# ---------------------------------------------------------------- 锁
# 锁语义（t_6fa92a0a 起）：O_EXCL 独占；活锁主绝不抢占（新进程只能等）；
#   崩溃遗留的**死锁**（锁内 pid 已不存在）在下次加锁时自动清理重试；
#   pid 读不到 / 判活失败（跨平台）= 回退原有「mtime 超 2h 视为过期」逻辑。

def read_lock_pid(path: str) -> int | None:
    """读锁文件内记录的 pid；读不到 / 非数字 => None（按「判不了」处理）。"""
    try:
        with open(path, "r", encoding="ascii", errors="ignore") as f:
            txt = f.read(64).strip()
    except OSError:
        return None
    return int(txt) if txt.isdigit() and int(txt) > 0 else None


def pid_alive(pid: int) -> bool | None:
    """进程判活：True=活 / False=死 / None=判不了（调用方回退 mtime 逻辑）。

    Windows 走 OpenProcess（0x1000=PROCESS_QUERY_LIMITED_INFORMATION）：
      * 拿不到句柄：ERROR_ACCESS_DENIED(5)=进程在但无权查 → 活；
        ERROR_INVALID_PARAMETER(87)=无此 pid → 死；其余错误码/异常 → None。
      * 拿到句柄还要看 GetExitCodeProcess：STILL_ACTIVE(259)=真活；
        其它=已退出（实测：进程退出后只要还有人握着它的句柄，OpenProcess
        依然成功——这种「僵尸 pid」必须判死，否则崩溃锁永远清不掉）。
    非 Windows 走 os.kill(pid, 0)（Linux 上补一刀：/proc 状态为 Z 的僵尸判死）。
    pid<=0 视为死。
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = ctypes.c_void_p
            k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            h = k32.OpenProcess(0x1000, 0, int(pid))
            if h:
                try:
                    code = ctypes.c_ulong(0)
                    if k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code)):
                        return code.value == 259      # STILL_ACTIVE=259
                    return True                        # 查不到退出码：保守当活
                finally:
                    k32.CloseHandle(h)
            err = ctypes.get_last_error()
            if err == 5:                      # ERROR_ACCESS_DENIED
                return True
            if err == 87:                     # ERROR_INVALID_PARAMETER（无此 pid）
                return False
            return None
        except Exception:                     # pragma: no cover
            return None
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:                           # pragma: no cover
        return None
    try:                                      # Linux：僵尸（Z）不算活
        with open("/proc/%d/stat" % int(pid), "rb") as f:
            state = f.read().decode("ascii", "ignore").rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except (OSError, IndexError):             # macOS 等无 /proc：保守当活
        return True


@contextlib.contextmanager
def run_lock(enabled: bool = True):
    if not enabled:
        yield
        return
    os.makedirs(RUNTIME, exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            # 自愈：锁内 pid 已死（崩溃遗留）=> 清掉重试。活主/判不了 => 原样拒绝。
            healed = False
            with contextlib.suppress(OSError):
                owner = read_lock_pid(LOCK)
                if owner is not None and pid_alive(owner) is False:
                    os.remove(LOCK)
                    healed = True
                    log("锁自愈：残留锁 %s 的锁主 pid=%d 已不存在，清理后重试" % (LOCK, owner))
            if healed:
                continue
            with contextlib.suppress(OSError):
                if time.time() - os.path.getmtime(LOCK) > LOCK_STALE_S:
                    os.remove(LOCK)
                    continue
            raise SnapshotError("已有快照/回滚在跑（锁 %s 被占）；稍后再试" % LOCK)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(LOCK)


# ---------------------------------------------------------------- SSH 通道（脚本经 stdin 送入）
#
# 教训：Windows 上 sshd 经 cmd.exe 执行命令，命令行上限 8191 字符；一旦
# `powershell -EncodedCommand <b64>` 超过即报「命令行太长」且 stdout 为空（假成功）。
# 因此**脚本本体一律走 stdin**：命令行只放固定的小 wrapper（≤2KB），wrapper 从 stdin
# 读「b64(脚本UTF8)\n[+ 附加 payload]」，iex 执行脚本；脚本内用 $PAYLOAD 取附加数据。
# 附带好处：不再有「ssh 继承父 stdin 挂死」问题（stdin 由我们显式供给）。

WRAPPER_PS = r"""
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$x = [System.Console]::In.ReadToEnd()
$i = $x.IndexOf("`n")
if ($i -lt 0) { $i = $x.Length }
$body = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($x.Substring(0, $i)))
$script:PAYLOAD = ""
if ($i -lt $x.Length - 1) { $script:PAYLOAD = $x.Substring($i + 1) }
iex $body
"""
_WRAPPER_CMD = "powershell -NoProfile -EncodedCommand " + base64.b64encode(
    WRAPPER_PS.encode("utf-16-le")).decode("ascii")


def ssh_ps(script: str, payload: bytes | None = None, timeout: int = 300) -> str:
    """在主机上跑一段 PowerShell（脚本走 stdin；payload 追加给脚本的 $PAYLOAD）。"""
    stdin = base64.b64encode(script.encode("utf-8")) + b"\n" + (payload or b"")
    cmd = ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
           SSH_HOST, _WRAPPER_CMD]
    try:
        r = subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise TransportError("ssh 超时（%ss）" % timeout)
    return r.stdout.decode("utf-8", "replace")


SCAN_PS = r"""
$base = __BASE__
$list = New-Object System.Collections.ArrayList
Get-ChildItem -File (Join-Path $base 'config\*.json') -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -notmatch '\.bak' } | ForEach-Object { [void]$list.Add($_) }
Get-ChildItem -Recurse -File (Join-Path $base 'data') -ErrorAction SilentlyContinue |
  ForEach-Object { [void]$list.Add($_) }
$appAll = Get-ChildItem -Recurse -File (Join-Path $base 'app') -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -notmatch '__pycache__' -and $_.Name -notmatch '\.bak' }
foreach ($f in $appAll) {
  $baks = @(Get-ChildItem -File ($f.FullName + '.bak*') -ErrorAction SilentlyContinue)
  if ($baks.Count -gt 0) { [void]$list.Add($f) }
}
foreach ($p in @(__PATCHES__)) {
  $fi = Get-Item -LiteralPath (Join-Path $base $p) -ErrorAction SilentlyContinue
  if ($fi) { [void]$list.Add($fi) }
}
$seen = @{}
foreach ($f in $list) {
  $rel = $f.FullName.Substring($base.Length + 1)
  if ($seen.ContainsKey($rel)) { continue }
  $seen[$rel] = $true
  $h = (Get-FileHash -Algorithm SHA256 -LiteralPath $f.FullName).Hash.ToLower()
  Write-Output ('F|' + $rel + '|' + $f.Length + '|' + $f.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') + '|' + $h)
}
Write-Output ('TOTAL|' + $seen.Count)
"""


def remote_scan(base: str = REMOTE_ROOT, timeout: int = 300) -> list[dict]:
    """远程枚举文件集（config/*.json 非 bak + data/** + 补丁文件）并逐文件 SHA256。"""
    script = SCAN_PS.replace("__BASE__", ps_quote(base)).replace("__PATCHES__", ps_list(KNOWN_PATCHES))
    out = ssh_ps(script, timeout=timeout)
    files = []
    seen_total = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("F|"):
            _, rel, size, mtime, h = line.split("|", 4)
            files.append({"path": rel, "size": int(size), "mtime": mtime, "sha256": h})
        elif line.startswith("TOTAL|"):
            seen_total = int(line.split("|", 1)[1])
    if seen_total is None:
        raise TransportError("远程枚举失败（无 TOTAL 标记；host=%s）" % SSH_HOST)
    if len(files) != seen_total:
        raise TransportError("远程枚举计数不符：标记 %d 实得 %d" % (seen_total, len(files)))
    return files


FETCH_PS = r"""
$base = __BASE__
$ps = @(__LIST__)
foreach ($p in $ps) {
  $fp = Join-Path $base $p
  if (-not (Test-Path -LiteralPath $fp -PathType Leaf)) { Write-Output ('M|' + $p); continue }
  $b = [System.IO.File]::ReadAllBytes($fp)
  Write-Output ('B|' + $p + '|' + $b.Length + '|' + [Convert]::ToBase64String($b))
}
"""


def _batches(entries: list[dict], get_size, max_bytes: int = BATCH_BYTES, max_files: int = BATCH_FILES):
    cur, cur_bytes = [], 0
    for e in entries:
        s = max(1, int(get_size(e)))
        if cur and (cur_bytes + s > max_bytes or len(cur) >= max_files):
            yield cur
            cur, cur_bytes = [], 0
        cur.append(e)
        cur_bytes += s
    if cur:
        yield cur


def remote_fetch(relpaths: list[str], base: str = REMOTE_ROOT, timeout: int = 300) -> dict[str, bytes]:
    """按批拉取文件内容（base64 走 stdout）。返回 {rel: bytes}；缺失文件抛错（fail-closed）。"""
    got: dict[str, bytes] = {}
    todo = [{"path": p, "size": 1} for p in relpaths]
    for batch in _batches(todo, lambda x: 1, max_bytes=10 ** 9, max_files=120):
        script = (FETCH_PS.replace("__BASE__", ps_quote(base))
                  .replace("__LIST__", ps_list([x["path"] for x in batch])))
        out = ssh_ps(script, timeout=timeout)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("B|"):
                _, rel, size, b64 = line.split("|", 3)
                data = base64.b64decode(b64) if b64 else b""
                if len(data) != int(size):
                    raise TransportError("拉取长度不符 %s：声明 %s 实得 %d" % (rel, size, len(data)))
                got[rel] = data
            elif line.startswith("M|"):
                raise TransportError("远程文件缺失：%s" % line.split("|", 1)[1])
    missing = [p for p in relpaths if p not in got]
    if missing:
        raise TransportError("拉取不全：缺 %d 个（%s…）" % (len(missing), missing[0]))
    return got


HASH_PS = r"""
$root = __ROOT__
$items = @(__LIST__)
foreach ($p in $items) {
  $fp = Join-Path $root $p
  if (Test-Path -LiteralPath $fp -PathType Leaf) {
    $h = (Get-FileHash -Algorithm SHA256 -LiteralPath $fp).Hash.ToLower()
    Write-Output ('H|' + $p + '|' + $h)
  } else { Write-Output ('H|' + $p + '|-') }
}
"""


def remote_hash(root: str, relpaths: list[str], timeout: int = 300) -> dict[str, str | None]:
    """远程求一批文件的 SHA256（不存在 → None）。"""
    res: dict[str, str | None] = {}
    for batch in _batches([{"path": p} for p in relpaths], lambda x: 1, max_bytes=10 ** 9, max_files=200):
        script = (HASH_PS.replace("__ROOT__", ps_quote(root))
                  .replace("__LIST__", ps_list([x["path"] for x in batch])))
        out = ssh_ps(script, timeout=timeout)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("H|"):
                _, rel, h = line.split("|", 2)
                res[rel] = None if h == "-" else h
    missing = [p for p in relpaths if p not in res]
    if missing:
        raise TransportError("远程哈希扫描不全：缺 %d（%s…）" % (len(missing), missing[0]))
    return res


WRITE_PS = r"""
$raw = $PAYLOAD
$lines = $raw.Split("`n")
foreach ($ln in $lines) {
  $ln = $ln.TrimEnd("`r")
  if ($ln.Length -eq 0) { continue }
  $parts = $ln.Split('|')
  if ($parts.Length -lt 3) { Write-Output ('E|badline'); continue }
  $target = $parts[0]; $esha = $parts[1]; $cb = $parts[2]
  try {
    $dir = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $bytes = [Convert]::FromBase64String($cb)
    $tmp = $target + '.cfgsnap.tmp'
    [System.IO.File]::WriteAllBytes($tmp, $bytes)
    Move-Item -Force -LiteralPath $tmp -Destination $target
    $h = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLower()
    Write-Output ('W|' + $target + '|' + [string]($h -eq $esha) + '|' + $h)
  } catch {
    Write-Output ('E|' + $target + '|' + [string]$_.Exception.Message)
  }
}
Write-Output 'WRITE_DONE'
"""


# ---- 上传通道 2：HTTP 拉取（主通道；见常量区「上传通道口径」）----

PULL_PS = r"""
$url = __URL__
$zip = __ZIP__
$stage = __STAGE__
$ok = $true
try {
  if (Test-Path -LiteralPath $zip) { Remove-Item -Force -LiteralPath $zip }
  Invoke-WebRequest -Uri $url -OutFile $zip -TimeoutSec __TTL__ -UseBasicParsing | Out-Null
} catch {
  Write-Output ('E|download|' + [string]$_.Exception.Message)
  $ok = $false
}
if ($ok) {
  try {
    if (Test-Path -LiteralPath $stage) { Remove-Item -Recurse -Force -LiteralPath $stage }
    Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force
    $man = Get-Content -LiteralPath (Join-Path $stage 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($e in @($man)) {
      $dst = $e.target
      try {
        $src = Join-Path $stage ($e.i -replace '/', '\')
        $dd = Split-Path -Parent $dst
        if (-not (Test-Path -LiteralPath $dd)) { New-Item -ItemType Directory -Force -Path $dd | Out-Null }
        $tmp = $dst + '.cfgsnap.tmp'
        Copy-Item -Force -LiteralPath $src -Destination $tmp
        Move-Item -Force -LiteralPath $tmp -Destination $dst
        $h = (Get-FileHash -Algorithm SHA256 -LiteralPath $dst).Hash.ToLower()
        Write-Output ('W|' + $dst + '|' + [string]($h -eq $e.sha256) + '|' + $h)
      } catch {
        Write-Output ('E|' + $dst + '|' + [string]$_.Exception.Message)
      }
    }
  } catch {
    Write-Output ('E|stage|' + [string]$_.Exception.Message)
  }
}
Remove-Item -Recurse -Force -LiteralPath $stage -ErrorAction SilentlyContinue
Remove-Item -Force -LiteralPath $zip -ErrorAction SilentlyContinue
Write-Output 'WRITE_DONE'
"""


class PullUnavailable(RuntimeError):
    """HTTP 拉取通道不可用（算不出本机可达 IP / 本地服务起不来）。"""


def _pull_host() -> str | None:
    """本机对主机可见的地址（让主机回连拉取）。环境变量 MAAORCH_SNAPSHOT_PULL_HOST 优先。"""
    env = os.environ.get("MAAORCH_SNAPSHOT_PULL_HOST", "").strip()
    if env:
        return env
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((SSH_HOST.split("@")[-1], 22))   # 只取本机出口地址，不发包
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip or None
    except OSError:
        return None


def build_zip(files: dict[str, bytes]) -> bytes:
    """{目标绝对路径: bytes} → zip（条目 f/NNNN + manifest.json:[{i,target,sha256,size}]）。"""
    man = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for i, target in enumerate(sorted(files)):
            blob = files[target]
            name = "f/%04d" % i
            zf.writestr(name, blob)
            man.append({"i": name, "target": target, "sha256": sha256_bytes(blob), "size": len(blob)})
        zf.writestr("manifest.json", json.dumps(man, ensure_ascii=False))
    return buf.getvalue()


def _serve_zip(zip_bytes: bytes, path: str):
    """一次性 HTTP 服务（绑本机对主机可见 IP、随机端口），返回 (base_url, server)。"""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    ip = _pull_host()
    if not ip:
        raise PullUnavailable("算不出本机对主机可见的 IP（可设 MAAORCH_SNAPSHOT_PULL_HOST）")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802（http.server 约定）
            if self.path != path:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(zip_bytes)))
            self.end_headers()
            self.wfile.write(zip_bytes)

        def log_message(self, *a):     # 静默，不污染工具输出
            pass

    try:
        srv = HTTPServer((ip, 0), Handler)
    except OSError as e:
        raise PullUnavailable("本机 HTTP 服务起不来（%s）：%s" % (ip, e))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return "http://%s:%d" % (ip, srv.server_address[1]), srv


def remote_pull_zip(files: dict[str, bytes], timeout: int = 300) -> dict[str, dict]:
    """HTTP 拉取通道：本机起一次性 HTTP 服务 → 主机下载 zip → 解包逐文件落盘 + 校验。
    文件级失败（W|…|False / E|<目标>|…）以回执返回；通道级失败抛 TransportError（调用方回退）。"""
    for tgt in files:
        if not re.match(r"^[A-Za-z]:[\\/]", tgt or ""):
            raise TransportError("拒绝上传：目标不是绝对路径：%r" % (tgt,))
    token = uuid.uuid4().hex[:16]
    zb = build_zip(files)
    log("HTTP 拉取通道：%d 文件 / %.2f MB（zip %.2f MB）"
        % (len(files), sum(len(b) for b in files.values()) / 1048576, len(zb) / 1048576))
    base, srv = _serve_zip(zb, "/" + token + "/cfgsnap.zip")
    zip_path = REMOTE_BACKUPS + "\\_cfgsnap_pull_" + token + ".zip"
    stage = REMOTE_BACKUPS + "\\_cfgsnap_stage_" + token
    try:
        script = (PULL_PS.replace("__URL__", ps_quote(base + "/" + token + "/cfgsnap.zip"))
                  .replace("__ZIP__", ps_quote(zip_path)).replace("__STAGE__", ps_quote(stage))
                  .replace("__TTL__", str(int(PULL_TTL_S))))
        out = ssh_ps(script, timeout=max(120, int(timeout)))
    finally:
        with contextlib.suppress(Exception):
            srv.shutdown()
        with contextlib.suppress(Exception):
            srv.server_close()
    results: dict[str, dict] = {}
    errors: list[str] = []
    done = False
    for line in out.splitlines():
        line = line.strip()
        if line == "WRITE_DONE":
            done = True
        elif line.startswith("W|"):
            _, target, ok, h = line.split("|", 3)
            results[target] = {"ok": ok.lower() == "true", "sha256": h}
        elif line.startswith("E|"):
            parts = line.split("|", 2)
            if len(parts) == 3 and re.match(r"^[A-Za-z]:[\\/]", parts[1]):
                results[parts[1]] = {"ok": False, "error": parts[2][:200]}
            else:
                errors.append(line[2:][:200])
    if not done:
        raise TransportError("HTTP 拉取无 WRITE_DONE：%s" % (out[:200] or "无输出"))
    if len(results) < len(files):
        raise TransportError("HTTP 拉取回执不全：%d/%d 文件（%s）"
                             % (len(results), len(files), "; ".join(errors[:2]) or "无错误行"))
    return results


def remote_write(files: dict[str, bytes], timeout: int = 300) -> dict[str, dict]:
    """把 {绝对目标路径: bytes} 落盘到主机。HTTP 拉取通道优先；通道不可用/有失败项时回退 stdin 分片。
    返回 {目标: {"ok": bool, "sha256": str|None}}；通道全失败抛 TransportError。"""
    if not files:
        return {}
    mode = os.environ.get("MAAORCH_SNAPSHOT_PULL", "auto").strip().lower()
    results: dict[str, dict] = {}
    if mode not in ("0", "off", "no", "false", "stdin"):
        try:
            results = remote_pull_zip(files, timeout=timeout)
        except (PullUnavailable, TransportError) as e:
            log("HTTP 拉取通道不可用（%s）；回退 stdin 分片（慢）" % str(e)[:160])
            results = {}
    failed = [p for p in files if not (results.get(p) or {}).get("ok")]
    if failed:
        if results:
            log("HTTP 通道有 %d 个失败项，回退 stdin 分片重传" % len(failed))
        sub = remote_write_stdin({p: files[p] for p in failed}, timeout=timeout)
        for p in failed:
            if (sub.get(p) or {}).get("ok") or p not in results:
                results[p] = sub.get(p) or {"ok": False, "error": "未回执"}
    return results


def remote_write_stdin(files: dict[str, bytes], timeout: int = 300,
                       cap_bytes: int = STDIN_UPLOAD_CAP, attempts: int = UPLOAD_ATTEMPTS) -> dict[str, dict]:
    """兜底通道：stdin 小分片 + 每片重试（大载荷会随机死锁 → 片要小、超时要短、失败重来）。"""
    results: dict[str, dict] = {}
    entries = [{"path": p, "size": len(b)} for p, b in files.items()]
    for batch in _batches(entries, lambda x: x["size"], max_bytes=cap_bytes, max_files=8):
        lines = []
        for e in batch:
            p = e["path"]
            lines.append("%s|%s|%s" % (p, sha256_bytes(files[p]), base64.b64encode(files[p]).decode("ascii")))
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        last: Exception | None = None
        for k in range(attempts):
            try:
                out = ssh_ps(WRITE_PS, payload=payload, timeout=min(int(timeout), UPLOAD_ATTEMPT_TIMEOUT))
            except TransportError as e:
                last = e
            else:
                got: dict[str, dict] = {}
                done = False
                for line in out.splitlines():
                    line = line.strip()
                    if line == "WRITE_DONE":
                        done = True
                    elif line.startswith("W|"):
                        _, target, ok, h = line.split("|", 3)
                        got[target] = {"ok": ok.lower() == "true", "sha256": h}
                    elif line.startswith("E|"):
                        got[line.split("|", 2)[-1] if line.count("|") >= 2 else line[2:]] = {
                            "ok": False, "error": line}
                if done:
                    results.update(got)
                    last = None
                    break
                last = TransportError("上传批次异常（无 WRITE_DONE）：%s" % out[:200])
            if k + 1 < attempts:
                log("stdin 分片重试 %d/%d（%s）" % (k + 2, attempts, str(last)[:100]))
                time.sleep(1.5)
        if last is not None:
            raise TransportError("stdin 兜底通道 %d 次尝试均失败：%s" % (attempts, str(last)[:200]))
    return results


COPY_PS = r"""
$base = __BASE__; $store = __STORE__
$items = @(__LIST__)
foreach ($p in $items) {
  try {
    $src = Join-Path $base $p
    $dst = Join-Path $store $p
    $dd = Split-Path -Parent $dst
    if (-not (Test-Path -LiteralPath $dd)) { New-Item -ItemType Directory -Force -Path $dd | Out-Null }
    Copy-Item -Force -LiteralPath $src -Destination $dst
    $h1 = (Get-FileHash -Algorithm SHA256 -LiteralPath $src).Hash.ToLower()
    $h2 = (Get-FileHash -Algorithm SHA256 -LiteralPath $dst).Hash.ToLower()
    Write-Output ('C|' + $p + '|' + [string]($h1 -eq $h2) + '|' + $h2)
  } catch {
    Write-Output ('E|' + $p + '|' + [string]$_.Exception.Message)
  }
}
Write-Output 'COPY_DONE'
"""


def remote_store_copy(relpaths: list[str], store: str, base: str = REMOTE_ROOT, timeout: int = 300) -> dict[str, dict]:
    r"""主机侧同机复制：base\<rel> -> store\<rel>（密文不出主机），逐文件校验哈希。"""
    res: dict[str, dict] = {}
    for batch in _batches([{"path": p} for p in relpaths], lambda x: 1, max_bytes=10 ** 9, max_files=80):
        script = (COPY_PS.replace("__BASE__", ps_quote(base)).replace("__STORE__", ps_quote(store))
                  .replace("__LIST__", ps_list([x["path"] for x in batch])))
        out = ssh_ps(script, timeout=timeout)
        done = False
        for line in out.splitlines():
            line = line.strip()
            if line == "COPY_DONE":
                done = True
            elif line.startswith("C|"):
                _, rel, ok, h = line.split("|", 3)
                res[rel] = {"ok": ok.lower() == "true", "sha256": h}
            elif line.startswith("E|"):
                res[line.split("|", 1)[1]] = {"ok": False, "error": line}
        if not done:
            raise TransportError("store 复制批次异常：%s" % out[:200])
    return res


def remote_scratch_write(relpaths: list[str], root: str, store: str, timeout: int = 300) -> dict[str, dict]:
    r"""主机侧：store\<rel> -> root\<rel>（store 与根都在主机）。"""
    res: dict[str, dict] = {}
    for batch in _batches([{"path": p} for p in relpaths], lambda x: 1, max_bytes=10 ** 9, max_files=80):
        script = (COPY_PS.replace("__BASE__", ps_quote(store)).replace("__STORE__", ps_quote(root))
                  .replace("__LIST__", ps_list([x["path"] for x in batch])))
        out = ssh_ps(script, timeout=timeout)
        done = False
        for line in out.splitlines():
            line = line.strip()
            if line == "COPY_DONE":
                done = True
            elif line.startswith("C|"):
                _, rel, ok, h = line.split("|", 3)
                res[rel] = {"ok": ok.lower() == "true", "sha256": h}
            elif line.startswith("E|"):
                res[line.split("|", 1)[1]] = {"ok": False, "error": line}
        if not done:
            raise TransportError("scratch 复制批次异常：%s" % out[:200])
    return res


RM_PS = r"""
$guard = __GUARD__
$targets = @(__LIST__)
foreach ($p in $targets) {
  if ($p -notlike ($guard + '*')) { Write-Output ('RM|' + $p + '|REFUSED-guard'); continue }
  if (-not (Test-Path -LiteralPath $p)) { Write-Output ('RM|' + $p + '|GONE'); continue }
  try {
    Remove-Item -Recurse -Force -LiteralPath $p -ErrorAction Stop
    Write-Output ('RM|' + $p + '|OK')
  } catch {
    Write-Output ('RM|' + $p + '|FAIL:' + [string]$_.Exception.Message)
  }
}
"""


def remote_rmdir(paths: list[str], guard: str, timeout: int = 120) -> dict[str, str]:
    """删除主机上 guard 前缀内的目录（护栏内置）。"""
    out = ssh_ps(RM_PS.replace("__GUARD__", ps_quote(guard)).replace("__LIST__", ps_list(paths)), timeout=timeout)
    res = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("RM|"):
            _, p, st = line.split("|", 2)
            res[p] = st
    return res


LS_DIRS_PS = r"""
Get-ChildItem -Directory __GLOB__ -ErrorAction SilentlyContinue | ForEach-Object { Write-Output ('D|' + $_.FullName) }
"""


def remote_list_dirs(pattern: str, timeout: int = 120) -> list[str]:
    out = ssh_ps(LS_DIRS_PS.replace("__GLOB__", ps_quote(pattern)), timeout=timeout)
    return [line.split("|", 1)[1] for line in out.splitlines() if line.startswith("D|")]


def remote_drill_residuals(keep: str | None = None, timeout: int = 120) -> tuple[list[str], dict[str, str]]:
    r"""扫主机 backups 下 `_cfgsnap_drill_*` 残留（crash 没走到 cleanup 的 scratch）并清理。

    返回 (发现的残留列表, {路径: RM 状态})。守卫前缀内置在 remote_rmdir 里。
    keep = 保留的目录名（当前 drill 自己的 scratch，理论上因持锁不会同时存在）。
    """
    pref = REMOTE_BACKUPS + "\\" + DRILL_PREFIX
    found = [p for p in remote_list_dirs(pref + "*", timeout=timeout)
             if os.path.basename(p) != keep]
    if not found:
        return [], {}
    return found, remote_rmdir(found, pref, timeout=timeout)


REMOTE_TOUCH_PS = r"""
$p = __PATH__
$mode = __MODE__   # append | decoy
if ($mode -eq 'append') {
  try {
    $b = [System.IO.File]::ReadAllBytes($p)
    $extra = [System.Text.Encoding]::UTF8.GetBytes("`r`n# BROKEN-BY-DRILL`r`n")
    $nb = New-Object byte[] ($b.Length + $extra.Length)
    [Array]::Copy($b, 0, $nb, 0, $b.Length)
    [Array]::Copy($extra, 0, $nb, $b.Length, $extra.Length)
    [System.IO.File]::WriteAllBytes($p, $nb)
    $h = (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLower()
    Write-Output ('T|OK|' + $h)
  } catch { Write-Output ('T|FAIL|' + [string]$_.Exception.Message) }
}
"""


def remote_append_bytes(path_abs: str, timeout: int = 120) -> dict:
    out = ssh_ps(REMOTE_TOUCH_PS.replace("__PATH__", ps_quote(path_abs)).replace("__MODE__", "'append'"),
                 timeout=timeout)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("T|"):
            _, st, h = line.split("|", 2)
            return {"ok": st == "OK", "sha256": h}
    return {"ok": False, "error": out[:200]}


# ---------------------------------------------------------------- 敏感判定 / 脱敏（与 redact_scriptconfig 对齐）

def _sensitive_key(k: str) -> bool:
    return k in SENS_EXACT or bool(SENS_RE.search(str(k)))


def redact_obj(obj) -> tuple[dict, int]:
    """递归脱敏（原地改副本）；返回 (obj, 脱敏计数)。空值不动（与 redact 工具一致）。"""
    count = [0]

    def rec(o):
        if isinstance(o, dict):
            for k, v in list(o.items()):
                if _sensitive_key(k):
                    if isinstance(v, str) and v:
                        o[k] = MARK
                        count[0] += 1
                        continue
                    if isinstance(v, list) and v:
                        o[k] = []
                        count[0] += 1
                        continue
                rec(v)
        elif isinstance(o, list):
            for v in o:
                rec(v)

    rec(obj)
    return obj, count[0]


def classify_and_redact(raw: bytes) -> dict:
    """判定文件是否含凭据 + 生成脱敏副本字节。返回 {sensitive, redacted(bytes|None), count, parse_ok}。
    JSON 解析失败（含空文件/二进制）按非敏感处理（工具不认识的格式保守留存原文）。"""
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"sensitive": False, "redacted": None, "count": 0, "parse_ok": False}
    scan = {"n": 0}

    def has_sens(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if _sensitive_key(k) and ((isinstance(v, str) and v) or (isinstance(v, list) and v)):
                    scan["n"] += 1
                has_sens(v)
        elif isinstance(o, list):
            for v in o:
                has_sens(v)

    has_sens(obj)
    if scan["n"] == 0:
        return {"sensitive": False, "redacted": None, "count": 0, "parse_ok": True}
    red, n = redact_obj(obj)
    blob = json.dumps(red, ensure_ascii=False, indent=1).encode("utf-8")
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError:  # pragma: no cover
        text = ""
    if LONG_B64.search(text.replace(MARK, "")):
        return {"sensitive": True, "redacted": None, "count": n, "parse_ok": True,
                "refused": "redacted-still-has-long-b64"}
    return {"sensitive": True, "redacted": blob, "count": n, "parse_ok": True}


# ---------------------------------------------------------------- 快照

def _snapshot_dir(ts: str) -> str:
    return os.path.join(SNAPS, ts)


def next_ts(label: str | None = None) -> str:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    if label:
        ts += "__" + re.sub(r"[^0-9A-Za-z_\-]+", "", label)[:24]
    return ts


def scan_local(base: str) -> list[dict]:
    files = []
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            fp = os.path.join(dirpath, name)
            rel = os.path.relpath(fp, base).replace("/", "\\")
            st = os.stat(fp)
            files.append({"path": rel, "size": st.st_size,
                          "mtime": dt.datetime.fromtimestamp(st.st_mtime).strftime(TS_FMT),
                          "sha256": sha256_file(fp)})
    files.sort(key=lambda x: x["path"].lower())
    return files


def do_snapshot(args) -> int:
    """快照主流程。source=remote（默认）或 local（--source）。"""
    source_local = getattr(args, "source", None)
    out_dir = getattr(args, "out", None)
    ts = os.path.basename(os.path.normpath(out_dir)) if out_dir else next_ts(getattr(args, "label", None))
    final_dir = out_dir or _snapshot_dir(ts)
    staging = final_dir + ".partial"
    label = getattr(args, "label", None) or ("local" if source_local else "manual")
    keep = int(getattr(args, "keep", KEEP_DEFAULT))

    if source_local:
        base = os.path.abspath(source_local)
        if not os.path.isdir(base):
            log("快照失败：--source 不是目录 %s" % base)
            return 2
        log("扫描本地源 %s" % base)
        files = scan_local(base)
        source_meta = {"kind": "local", "root": base}
    else:
        log("远程枚举 %s@%s …" % (SSH_HOST, REMOTE_ROOT))
        files = remote_scan()
        source_meta = {"kind": "remote", "host": SSH_HOST, "root": REMOTE_ROOT}

    if not files:
        log("快照失败：文件集为空")
        return 2
    total_bytes = sum(f["size"] for f in files)
    log("文件集 %d 个 / %.1f MB" % (len(files), total_bytes / 1024 / 1024))

    with run_lock(enabled=not getattr(args, "no_lock", False)):
        if os.path.exists(final_dir):
            log("快照失败：目标已存在 %s" % final_dir)
            return 2
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging, exist_ok=True)
        try:
            entries: list[dict] = []
            sensitive_rels: list[str] = []
            refused: list[str] = []
            to_fetch = [f["path"] for f in files]
            if source_local:
                # 本地源：直接读盘（含敏感文件也一样脱敏，双存储只剩本地脱敏版）
                fetched: dict[str, bytes] = {}
                for rel in to_fetch:
                    with open(rel_to_local(source_meta["root"], rel), "rb") as fh:
                        fetched[rel] = fh.read()
            else:
                fetched = {}
                for batch in _batches(files, lambda x: x["size"]):
                    part = remote_fetch([x["path"] for x in batch])
                    fetched.update(part)
            for f in files:
                rel = f["path"]
                raw = fetched[rel]
                got_sha = sha256_bytes(raw)
                if got_sha != f["sha256"]:
                    raise SnapshotError("内容哈希与枚举不符：%s（%s!=%s）" % (rel, got_sha[:12], f["sha256"][:12]))
                cls = classify_and_redact(raw)
                entry = {"path": rel, "kind": kind_of(rel), "size": f["size"], "mtime": f["mtime"],
                         "sha256": f["sha256"], "sensitive": bool(cls["sensitive"]),
                         "local": None, "redacted": None, "redact_count": cls["count"]}
                if cls["sensitive"]:
                    sensitive_rels.append(rel)
                    if cls.get("redacted") is not None:
                        rp = rel_to_local(os.path.join(staging, "redacted"), rel)
                        os.makedirs(os.path.dirname(rp), exist_ok=True)
                        with open(rp, "wb") as fh:
                            fh.write(cls["redacted"])
                        entry["redacted"] = "redacted/" + rel.replace("\\", "/")
                        entry["cmp_sha256"] = sha256_bytes(cls["redacted"])
                    else:
                        refused.append(rel)
                        entry["cmp_sha256"] = f["sha256"]
                        entry["redact_refused"] = cls.get("refused", "?")
                else:
                    lp = rel_to_local(os.path.join(staging, "files"), rel)
                    os.makedirs(os.path.dirname(lp), exist_ok=True)
                    with open(lp, "wb") as fh:
                        fh.write(raw)
                    entry["local"] = "files/" + rel.replace("\\", "/")
                    entry["cmp_sha256"] = f["sha256"]
                entries.append(entry)

            # 主机侧全保 store（仅敏感文件；密文不出主机）
            store_dir = None
            store_info = None
            if sensitive_rels and not source_local and not getattr(args, "no_remote_store", False):
                store_dir = REMOTE_BACKUPS + "\\" + STORE_PREFIX + ts
                log("主机侧 store 复制 %d 个敏感文件 → %s" % (len(sensitive_rels), store_dir))
                cp = remote_store_copy(sensitive_rels, store_dir)
                bad = [k for k, v in cp.items() if not v.get("ok")]
                if bad:
                    raise SnapshotError("store 复制校验失败：%s" % bad[:3])
                store_info = {"dir": store_dir, "files": len(sensitive_rels),
                              "sha256_ok": True, "created_at": _now()}
                for e in entries:
                    if e["sensitive"]:
                        e["remote_copy"] = store_dir + "\\" + e["path"]
                # store 内嵌 MANIFEST（人读+机读，无凭据值）
                man = {"tool": "mas_config_snapshot.py", "snapshot": ts, "label": label,
                       "created_at": _now(), "host": SSH_HOST,
                       "files": [{"path": e["path"], "size": e["size"], "sha256": e["sha256"]}
                                 for e in entries if e["sensitive"]]}
                man_bytes = json.dumps(man, ensure_ascii=False, indent=1).encode("utf-8")
                wr = remote_write({store_dir + "\\MANIFEST.json": man_bytes})
                if not wr.get(store_dir + "\\MANIFEST.json", {}).get("ok"):
                    raise SnapshotError("store MANIFEST 写入校验失败")
            elif sensitive_rels and not source_local:
                log("提示：敏感文件 %d 个未做主机侧全保（--no-remote-store）" % len(sensitive_rels))

            manifest = {
                "schema": "mas_config_snapshot/1",
                "snapshot": ts,
                "label": label,
                "created_at": _now(),
                "source": source_meta,
                "counts": {"files": len(entries), "bytes": total_bytes,
                           "sensitive": len(sensitive_rels), "redact_refused": len(refused)},
                "remote_store": store_info,
                "files": entries,
            }
            save_json(os.path.join(staging, "manifest.json"), manifest)
            os.replace(staging, final_dir)
        except (TransportError, SnapshotError) as e:
            log("快照失败：%s" % e)
            shutil.rmtree(staging, ignore_errors=True)
            audit({"action": "snapshot", "ok": False, "reason": str(e)[:200], "source": source_meta})
            return 2

    if not out_dir:
        pruned = rotate_snapshots(keep)
        if pruned:
            log("轮转：清理旧快照 %s" % ", ".join(pruned))
        if sensitive_rels and not source_local and not getattr(args, "no_remote_store", False):
            prune_remote_stores(keep)
        prune_drills()
    audit({"action": "snapshot", "ok": True, "snapshot": ts, "label": label,
           "files": len(entries), "sensitive": len(sensitive_rels),
           "store": (store_info or {}).get("dir"), "dir": final_dir})
    summary = {"snapshot": ts, "dir": final_dir, "label": label, "files": len(entries),
               "bytes": total_bytes, "sensitive": len(sensitive_rels),
               "redact_refused": refused, "store": (store_info or {}).get("dir")}
    if getattr(args, "json", False):
        print(json.dumps(summary, ensure_ascii=False))
    print("SNAPSHOT=OK ts=%s files=%d bytes=%d sensitive=%d store=%s"
          % (ts, len(entries), total_bytes, len(sensitive_rels), (store_info or {}).get("dir") or "none"))
    log("快照完成：%s" % final_dir)
    return 0


def rotate_snapshots(keep: int) -> list[str]:
    if keep <= 0:
        return []
    names = sorted([n for n in os.listdir(SNAPS) if SNAP_RE.match(n) and os.path.isdir(os.path.join(SNAPS, n))])
    drop = names[:-keep] if len(names) > keep else []
    for n in drop:
        shutil.rmtree(os.path.join(SNAPS, n), ignore_errors=True)
    return drop


def prune_drills(keep: int = DRILL_KEEP) -> None:
    if not os.path.isdir(DRILLS):
        return
    names = sorted([n for n in os.listdir(DRILLS) if os.path.isdir(os.path.join(DRILLS, n))])
    for n in (names[:-keep] if len(names) > keep else []):
        shutil.rmtree(os.path.join(DRILLS, n), ignore_errors=True)


def prune_remote_stores(keep: int) -> None:
    try:
        dirs = remote_list_dirs(REMOTE_BACKUPS + "\\" + STORE_PREFIX + "*")
    except TransportError as e:
        log("提示：store 轮转跳过（%s）" % e)
        return
    dirs = sorted(dirs)
    drop = dirs[:-keep] if len(dirs) > keep else []
    if not drop:
        return
    res = remote_rmdir(drop, REMOTE_BACKUPS + "\\" + STORE_PREFIX)
    bad = [k for k, v in res.items() if v not in ("OK", "GONE")]
    if bad:
        log("提示：store 轮转部分未删成：%s" % bad[:3])
    else:
        log("主机侧 store 轮转：清理 %d 个旧目录" % len(drop))


def resolve_snapshot(token: str) -> str:
    """ts / 唯一前缀 / latest / 目录路径 → 快照目录（含 manifest.json）。"""
    if not token:
        raise SnapshotError("需要快照标识（ts / latest）")
    t = token.strip()
    cands: list[str] = []
    if os.path.isdir(t) and os.path.exists(os.path.join(t, "manifest.json")):
        return os.path.abspath(t)
    if t.lower() in ("latest", "last", "最新"):
        names = sorted([n for n in os.listdir(SNAPS) if SNAP_RE.match(n)
                        and os.path.isdir(os.path.join(SNAPS, n))]) if os.path.isdir(SNAPS) else []
        if not names:
            raise SnapshotError("没有可用快照")
        return os.path.join(SNAPS, names[-1])
    for base in (SNAPS, DRILLS):
        if not os.path.isdir(base):
            continue
        for n in os.listdir(base):
            full = os.path.join(base, n)
            if os.path.isdir(full) and os.path.exists(os.path.join(full, "manifest.json")) and n.startswith(t):
                cands.append(full)
            # 演练目录 drill_<ts> 里可能有 snapB 子目录
            if os.path.isdir(full):
                sub = os.path.join(full, "snapB")
                if os.path.isdir(sub) and os.path.exists(os.path.join(sub, "manifest.json")) \
                        and (n.startswith(t) or t in n):
                    cands.append(sub)
    if len(cands) == 1:
        return cands[0]
    if not cands:
        raise SnapshotError("找不到快照：%s" % token)
    raise SnapshotError("快照前缀匹配到多个：%s（%s）" % (token, [os.path.basename(c) for c in cands]))


def list_snapshots() -> list[dict]:
    rows = []
    if not os.path.isdir(SNAPS):
        return rows
    for n in sorted([x for x in os.listdir(SNAPS) if SNAP_RE.match(x)
                     and os.path.isdir(os.path.join(SNAPS, x))], reverse=True):
        man = load_json(os.path.join(SNAPS, n, "manifest.json"), {}) or {}
        rows.append({"snapshot": n, "label": man.get("label"), "created_at": man.get("created_at"),
                     "files": (man.get("counts") or {}).get("files"),
                     "bytes": (man.get("counts") or {}).get("bytes"),
                     "sensitive": (man.get("counts") or {}).get("sensitive"),
                     "store": (man.get("remote_store") or {}).get("dir"),
                     "source": (man.get("source") or {}).get("kind")})
    return rows


# ---------------------------------------------------------------- diff

def _fmt_val(v, limit: int = 80) -> str:
    if isinstance(v, str):
        s = json.dumps(v, ensure_ascii=False)
        if LONG_B64.search(v):
            return '"<长串已隐去(%d)>"' % len(v)
    else:
        s = json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit - 1] + "…"


def _val_masked(key: str, v, sensitive_side: bool):
    if key and _sensitive_key(key) and isinstance(v, str) and v and v != MARK:
        return "<敏感已隐去>"
    return _fmt_val(v)


def json_field_diff(a, b, max_changes: int = 200) -> tuple[list[dict], int]:
    """结构化字段级 diff：路径式 + 列表按 uid/qid/id/name 键对齐。返回 (changes, elided)。"""
    changes: list[dict] = []
    elided = [0]

    def add(op, path, old=None, new=None, key=None):
        if len(changes) >= max_changes:
            elided[0] += 1
            return
        rec = {"op": op, "path": path}
        if op != "add":
            rec["old"] = _val_masked(key, old, False)
        if op != "del":
            rec["new"] = _val_masked(key, new, False)
        changes.append(rec)

    def list_key(items):
        if not items or not all(isinstance(i, dict) for i in items):
            return None
        for k in ("uid", "qid", "id", "Id", "name", "Name", "key", "Key", "Time", "Stage"):
            vals = [i.get(k) for i in items]
            if all(v is not None for v in vals) and len({str(v) for v in vals}) == len(vals):
                return k
        return None

    def rec(x, y, path):
        if type(x) is not type(y) and not (isinstance(x, (int, float)) and isinstance(y, (int, float))):
            add("chg", path or "$", x, y)
            return
        if isinstance(x, dict):
            for k in sorted(set(x) | set(y)):
                p = "%s.%s" % (path, k) if path else str(k)
                if k not in x:
                    add("add", p, None, y[k], key=str(k))
                elif k not in y:
                    add("del", p, x[k], None, key=str(k))
                else:
                    rec(x[k], y[k], p)
        elif isinstance(x, list):
            kk = list_key(x) or list_key(y)
            if kk and all(isinstance(i, dict) and kk in i for i in x) and all(isinstance(i, dict) and kk in i for i in y):
                xs = {str(i[kk]): i for i in x}
                ys = {str(i[kk]): i for i in y}
                for k in sorted(set(xs) | set(ys), key=str):
                    p = "%s[%s=%s]" % (path, kk, k)
                    if k not in xs:
                        add("add", p, None, ys[k])
                    elif k not in ys:
                        add("del", p, xs[k], None)
                    else:
                        rec(xs[k], ys[k], p)
            elif len(x) == len(y):
                for i, (xi, yi) in enumerate(zip(x, y)):
                    rec(xi, yi, "%s[%d]" % (path, i))
            else:
                add("chg", path or "$", "<list[%d]>" % len(x), "<list[%d]>" % len(y))
        else:
            if x != y:
                add("chg", path or "$", x, y, key=path.rsplit(".", 1)[-1].split("[", 1)[0])

    rec(a, b, "")
    return changes, elided[0]


def first_diff_offset(a: bytes, b: bytes) -> int | None:
    n = min(len(a), len(b), 4 * 1024 * 1024)
    for i in range(n):
        if a[i] != b[i]:
            return i
    return None if len(a) == len(b) else n


def manifest_files(man: dict) -> dict[str, dict]:
    return {e["path"]: e for e in man.get("files") or []}


def _load_side_bytes(snap_dir: str, entry: dict) -> bytes | None:
    if not entry:
        return None
    if entry.get("local"):
        p = os.path.join(snap_dir, *entry["local"].split("/"))
        if os.path.exists(p):
            with open(p, "rb") as fh:
                return fh.read()
    if entry.get("redacted"):
        p = os.path.join(snap_dir, *entry["redacted"].split("/"))
        if os.path.exists(p):
            with open(p, "rb") as fh:
                return fh.read()
    return None


def diff_snapshots(a_dir: str, b_dir: str, only: str | None = None,
                   max_changes: int = 200, skip_missing_sensitive: bool = False) -> dict:
    ma = load_json(os.path.join(a_dir, "manifest.json"))
    mb = load_json(os.path.join(b_dir, "manifest.json"))
    if not ma or not mb:
        raise SnapshotError("manifest.json 缺失（%s / %s）" % (a_dir, b_dir))
    fa, fb = manifest_files(ma), manifest_files(mb)

    def keep(rel: str) -> bool:
        if not only:
            return True
        pat = only.replace("/", "\\")
        return fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(os.path.basename(rel), pat)

    changed, added, removed, mtime_only, not_comparable, field_reports = [], [], [], [], [], []
    for rel in sorted(set(fa) | set(fb), key=str.lower):
        if not keep(rel):
            continue
        ea, eb = fa.get(rel), fb.get(rel)
        if ea and not eb:
            if skip_missing_sensitive and ea.get("sensitive"):
                not_comparable.append({"path": rel, "why": "sensitive-missing-in-b"})
            else:
                removed.append({"path": rel, "size": ea.get("size")})
            continue
        if eb and not ea:
            if skip_missing_sensitive and eb.get("sensitive"):
                not_comparable.append({"path": rel, "why": "sensitive-missing-in-a"})
            else:
                added.append({"path": rel, "size": eb.get("size")})
            continue
        ka = ea.get("cmp_sha256") or ea.get("sha256")
        kb = eb.get("cmp_sha256") or eb.get("sha256")
        if ka != kb:
            changed.append({"path": rel, "old_sha": ka[:12], "new_sha": kb[:12],
                            "old_size": ea.get("size"), "new_size": eb.get("size")})
            if ea.get("sensitive") or eb.get("sensitive"):
                continue  # 敏感文件字段级走脱敏副本，下面统一处理
            ba = _load_side_bytes(a_dir, ea)
            bb = _load_side_bytes(b_dir, eb)
            if ba is None or bb is None:
                field_reports.append({"path": rel, "parse": "no-local-copy"})
                continue
            item = {"path": rel, "changes": [], "elided": 0}
            try:
                oa, ob = json.loads(ba.decode("utf-8")), json.loads(bb.decode("utf-8"))
                ch, el = json_field_diff(oa, ob, max_changes)
                item["changes"], item["elided"] = ch, el
            except (ValueError, UnicodeDecodeError):
                item["parse"] = "non-json"
                item["first_diff_offset"] = first_diff_offset(ba, bb)
                item["size"] = {"old": len(ba), "new": len(bb)}
            field_reports.append(item)
        else:
            if ea.get("mtime") != eb.get("mtime"):
                mtime_only.append({"path": rel, "mtime": [ea.get("mtime"), eb.get("mtime")]})
    # 敏感文件（两侧都有）的字段级：用脱敏副本比
    for rel in sorted(set(fa) & set(fb), key=str.lower):
        if not keep(rel):
            continue
        ea, eb = fa[rel], fb[rel]
        if not (ea.get("sensitive") or eb.get("sensitive")):
            continue
        if (ea.get("cmp_sha256") or ea.get("sha256")) == (eb.get("cmp_sha256") or eb.get("sha256")):
            continue
        ba = _load_side_bytes(a_dir, ea) if ea.get("redacted") else None
        bb = _load_side_bytes(b_dir, eb) if eb.get("redacted") else None
        if ba is None or bb is None:
            field_reports.append({"path": rel, "parse": "redacted-missing", "sensitive": True})
            continue
        item = {"path": rel, "changes": [], "elided": 0, "sensitive": True}
        try:
            oa, ob = json.loads(ba.decode("utf-8")), json.loads(bb.decode("utf-8"))
            ch, el = json_field_diff(oa, ob, max_changes)
            item["changes"], item["elided"] = ch, el
        except (ValueError, UnicodeDecodeError):
            item["parse"] = "redacted-non-json"
        field_reports.append(item)
    return {"a": {"dir": a_dir, "snapshot": ma.get("snapshot"), "label": ma.get("label"), "created_at": ma.get("created_at")},
            "b": {"dir": b_dir, "snapshot": mb.get("snapshot"), "label": mb.get("label"), "created_at": mb.get("created_at")},
            "changed": changed, "added": added, "removed": removed,
            "mtime_only": mtime_only, "not_comparable": not_comparable,
            "fields": field_reports}


def print_diff(rep: dict, want_json: bool) -> None:
    if want_json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        a, b = rep["a"], rep["b"]
        print("== diff %s(%s) -> %s(%s) ==" % (a["snapshot"], a.get("label"), b["snapshot"], b.get("label")))
        if rep["changed"]:
            print("--- 内容改动（%d）---" % len(rep["changed"]))
            for c in rep["changed"]:
                print("  [改] %-60s %s→%s  %sB→%sB" % (c["path"], c["old_sha"], c["new_sha"],
                                                         c["old_size"], c["new_size"]))
        if rep["added"]:
            print("--- B 新增（%d）---" % len(rep["added"]))
            for c in rep["added"]:
                print("  [+] %s" % c["path"])
        if rep["removed"]:
            print("--- B 删除（%d）---" % len(rep["removed"]))
            for c in rep["removed"]:
                print("  [-] %s" % c["path"])
        if rep["mtime_only"]:
            print("--- 仅 mtime 变化（内容同，%d）---" % len(rep["mtime_only"]))
            for c in rep["mtime_only"][:20]:
                print("  [m] %s  %s→%s" % (c["path"], c["mtime"][0], c["mtime"][1]))
        if rep["not_comparable"]:
            print("--- 敏感文件未参与比较（本地仅脱敏副本，%d；真回滚走主机 store）---" % len(rep["not_comparable"]))
            for c in rep["not_comparable"]:
                print("  [s] %s (%s)" % (c["path"], c["why"]))
        if rep["fields"]:
            print("--- 字段级（谁改了哪个字段）---")
            for f in rep["fields"]:
                if f.get("parse") in ("non-json",):
                    print("  %s: 非 JSON（首个差异字节 offset=%s, size %s→%s）"
                          % (f["path"], f.get("first_diff_offset"), f["size"]["old"], f["size"]["new"]))
                    continue
                if f.get("parse"):
                    print("  %s: %s" % (f["path"], f["parse"]))
                    continue
                tag = "（脱敏视图）" if f.get("sensitive") else ""
                print("  %s%s" % (f["path"], tag))
                for ch in f["changes"]:
                    op = {"add": "+", "del": "-", "chg": "~"}.get(ch["op"], "?")
                    if ch["op"] == "add":
                        print("    + %s = %s" % (ch["path"], ch.get("new")))
                    elif ch["op"] == "del":
                        print("    - %s（原 %s）" % (ch["path"], ch.get("old")))
                    else:
                        print("    ~ %s: %s -> %s" % (ch["path"], ch.get("old"), ch.get("new")))
                if f.get("elided"):
                    print("    …（另有 %d 条省略）" % f["elided"])
        n = len(rep["changed"]) + len(rep["added"]) + len(rep["removed"])
        print("合计：改动 %d 文件（+%d/-%d）、仅mtime %d、字段报告 %d"
              % (n, len(rep["added"]), len(rep["removed"]), len(rep["mtime_only"]), len(rep["fields"])))
    print("DIFF=changed:%d added:%d removed:%d" % (len(rep["changed"]), len(rep["added"]), len(rep["removed"])))


# ---------------------------------------------------------------- 回滚（plan / apply）

def plan_restore(snap_dir: str, root_kind: str, root: str, only: str | None = None,
                 timeout: int = 300) -> dict:
    man = load_json(os.path.join(snap_dir, "manifest.json"))
    if not man:
        raise SnapshotError("manifest.json 缺失：%s" % snap_dir)
    entries = []
    for e in man["files"]:
        e = dict(e)
        e["path"] = norm_rel(e["path"])                     # 防手改 manifest 越界
        if not only or fnmatch.fnmatch(e["path"], only.replace("/", "\\")) \
                or fnmatch.fnmatch(os.path.basename(e["path"]), only):
            entries.append(e)
    rels = [e["path"] for e in entries]
    if root_kind == "remote":
        cur = remote_hash(root, rels, timeout=timeout)
    else:
        cur = {}
        for rel in rels:
            fp = rel_to_local(root, rel)
            cur[rel] = sha256_file(fp) if os.path.exists(fp) else None
    plan = []
    for e in entries:
        rel = e["path"]
        c = cur.get(rel)
        if c == e["sha256"]:
            state = "same"
        elif c is None:
            state = "missing"
        else:
            state = "diff"
        if state == "same":
            src = "none"
        elif e.get("local"):
            src = "local"
        elif e.get("remote_copy"):
            src = "remote_store"
        else:
            src = "none"
        plan.append({"path": rel, "state": state, "src": src,
                     "snapshot_sha": e["sha256"], "current_sha": c,
                     "sensitive": bool(e.get("sensitive")),
                     "store": e.get("remote_copy")})
    return {"snapshot": man.get("snapshot"), "dir": snap_dir, "root_kind": root_kind, "root": root,
            "store_dir": (man.get("remote_store") or {}).get("dir"), "plan": plan,
            "counts": {"same": sum(1 for p in plan if p["state"] == "same"),
                       "diff": sum(1 for p in plan if p["state"] == "diff"),
                       "missing": sum(1 for p in plan if p["state"] == "missing"),
                       "unrecoverable": sum(1 for p in plan if p["state"] != "same" and p["src"] == "none")}}


def print_plan(pl: dict, want_json: bool, sink: list | None = None) -> list[str]:
    """打印回滚计划；sink 不为 None 时同时追加到该列表（演练证据用）。"""
    out: list[str] = []

    def P(s: str):
        out.append(s)
        if sink is not None:
            sink.append(s)
        if sink is None:
            print(s)

    if want_json and sink is None:
        print(json.dumps(pl, ensure_ascii=False, indent=1))
        return []
    P("== 回滚计划：快照 %s → %s（%s） ==" % (pl["snapshot"], pl["root"], pl["root_kind"]))
    for p in pl["plan"]:
        if p["state"] == "same" and p["src"] == "none":
            continue
        mark = {"diff": "覆盖", "missing": "新建"}.get(p["state"], "?")
        if p["src"] == "none":
            P("  [不可恢复] %-58s（敏感文件无本地全保副本；请走主机 store 或重开快照）" % p["path"])
        else:
            src = {"local": "本地快照", "remote_store": "主机 store"}.get(p["src"], p["src"])
            P("  [%s] %-58s sha %s → %s（源:%s）"
              % (mark, p["path"], (p["current_sha"] or "-")[:8], p["snapshot_sha"][:8], src))
    c = pl["counts"]
    P("计划：覆盖 %d / 新建 %d / 已是快照 %d / 不可恢复 %d" % (c["diff"], c["missing"], c["same"], c["unrecoverable"]))
    return out


def apply_restore(pl: dict, apply: bool, args) -> int:
    c = pl["counts"]
    if not apply:
        print("RESTORE=DRY-RUN diff=%d missing=%d unrecoverable=%d" % (c["diff"], c["missing"], c["unrecoverable"]))
        return 0
    todo = [p for p in pl["plan"] if p["state"] in ("diff", "missing")]
    if not todo:
        print("RESTORE=DONE ok=0 failed=0（已是快照内容，无需回滚）")
        return 0
    snap_dir = pl["dir"]
    man = load_json(os.path.join(snap_dir, "manifest.json"), {})
    entries = manifest_files(man)
    root_kind, root = pl["root_kind"], pl["root"]

    # 生产根守卫：NO_GO 硬闸 + 自动前置快照
    if root_kind == "remote" and root.lower() == REMOTE_ROOT.lower():
        if str(os.environ.get("MAAORCH_SNAPSHOT_NO_GO", "")).strip().lower() in {"1", "true", "yes", "on"}:
            log("拒绝：MAAORCH_SNAPSHOT_NO_GO 已设（生产回滚被硬闸拦住）")
            return 5
        if not getattr(args, "no_pre_snapshot", False):
            log("生产回滚前自动前置快照（pre-restore）…")
            pre_args = argparse.Namespace(**vars(args))
            pre_args.label = "pre-restore"
            pre_args.source = None
            pre_args.out = None
            rc = do_snapshot(pre_args)
            if rc != 0:
                log("拒绝：前置快照失败（fail-closed）")
                return 5

    results = {"ok": 0, "failed": [], "skipped": []}
    # 1) 本地源文件：推送到目标（远程：批量上传；本地：直接写）
    local_files = {}
    for p in todo:
        e = entries[p["path"]]
        if p["src"] != "local":
            continue
        src = os.path.join(snap_dir, *e["local"].split("/"))
        with open(src, "rb") as fh:
            local_files[p["path"]] = fh.read()
    if local_files:
        if root_kind == "remote":
            absmap = {root + "\\" + rel: b for rel, b in local_files.items()}
            wr = remote_write(absmap, timeout=int(getattr(args, "timeout", 300)))
            for rel in local_files:
                r = wr.get(root + "\\" + rel, {})
                if r.get("ok"):
                    results["ok"] += 1
                else:
                    results["failed"].append(rel)
        else:
            for rel, blob in local_files.items():
                fp = rel_to_local(root, rel)
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                tmp = fp + ".cfgsnap.tmp"
                with open(tmp, "wb") as fh:
                    fh.write(blob)
                os.replace(tmp, fp)
                if sha256_file(fp) == sha256_bytes(blob):
                    results["ok"] += 1
                else:
                    results["failed"].append(rel)
    # 2) 主机 store 文件：远程侧同机 Copy（密文不出主机）
    store_files = [p for p in todo if p["src"] == "remote_store"]
    if store_files:
        if root_kind != "remote":
            for p in store_files:
                results["skipped"].append(p["path"] + "（本地目标无 store 通道）")
        elif not pl.get("store_dir"):
            for p in store_files:
                results["skipped"].append(p["path"] + "（快照无主机 store）")
        else:
            cp = remote_scratch_write([p["path"] for p in store_files], root, pl["store_dir"],
                                      timeout=int(getattr(args, "timeout", 300)))
            for p in store_files:
                r = cp.get(p["path"], {})
                if r.get("ok"):
                    results["ok"] += 1
                else:
                    results["failed"].append(p["path"])
    # 3) 不可恢复（敏感文件无副本）
    for p in todo:
        if p["src"] == "none":
            results["skipped"].append(p["path"] + "（敏感文件无全保副本，未动）")

    # 4) 复读校验
    restored = [p["path"] for p in todo if p["path"] not in results["failed"]
                and not any(s.startswith(p["path"] + "（") for s in results["skipped"])]
    verify_bad = []
    if restored:
        if root_kind == "remote":
            actual = remote_hash(root, restored, timeout=int(getattr(args, "timeout", 300)))
        else:
            actual = {rel: (sha256_file(rel_to_local(root, rel))
                            if os.path.exists(rel_to_local(root, rel)) else None) for rel in restored}
        for rel in restored:
            if actual.get(rel) != entries[rel]["sha256"]:
                verify_bad.append(rel)
    ok_n = results["ok"] - len(set(verify_bad))
    audit({"action": "restore", "snapshot": pl["snapshot"], "root": root, "root_kind": root_kind,
           "ok": ok_n, "failed": results["failed"][:10], "skipped": results["skipped"][:10],
           "verify_bad": verify_bad[:10]})
    for s in results["skipped"]:
        log("跳过：%s" % s)
    if results["failed"] or verify_bad:
        print("RESTORE=FAIL ok=%d failed=%d verify_bad=%d" % (results["ok"], len(results["failed"]), len(verify_bad)))
        for rel in (results["failed"] + verify_bad)[:10]:
            log("失败项：%s" % rel)
        return 3
    print("RESTORE=DONE ok=%d failed=0 skipped=%d" % (ok_n, len(results["skipped"])))
    return 0


# ---------------------------------------------------------------- 漂移检测（只读）

def do_drift(args) -> int:
    snap_dir = resolve_snapshot(getattr(args, "against", None) or "latest")
    man = load_json(os.path.join(snap_dir, "manifest.json"), {})
    if not man:
        log("漂移检测失败：manifest 读不到")
        return 2
    log("当前生产枚举 vs 快照 %s …" % man.get("snapshot"))
    try:
        cur_files = remote_scan()
    except TransportError as e:
        log("漂移检测失败：%s" % e)
        print("SNAPSHOT_DRIFT=UNKNOWN reason=scan-failed")
        return 2
    fa = manifest_files(man)
    fb = {f["path"]: f for f in cur_files}

    def scoped(rel: str) -> bool:
        if not args.include_temp and rel.lower().startswith("data\\") and "\\temp\\" in rel.lower():
            return False
        return True

    changed, added, missing = [], [], []
    for rel in sorted(set(fa) | set(fb), key=str.lower):
        if not scoped(rel):
            continue
        ea, eb = fa.get(rel), fb.get(rel)
        if ea and not eb:
            missing.append(rel)
        elif eb and not ea:
            added.append(rel)
        elif (ea.get("sha256") or "") != (eb.get("sha256") or ""):
            changed.append({"path": rel, "snapshot_sha": ea["sha256"], "current_sha": eb["sha256"],
                            "kind": ea.get("kind")})
    drift_n = len(changed) + len(added) + len(missing)
    res = {"against": man.get("snapshot"), "checked_at": _now(),
           "changed": changed, "added": added, "missing": missing,
           "scope": {"include_temp": bool(args.include_temp)}}
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("== 漂移检测：快照 %s vs 当前生产 ==" % man.get("snapshot"))
        for c in changed[:40]:
            print("  [改动] %-58s %s→%s（%s）" % (c["path"], c["snapshot_sha"][:8], c["current_sha"][:8], c["kind"]))
        for rel in added[:20]:
            print("  [新增] %s" % rel)
        for rel in missing[:20]:
            print("  [缺失] %s" % rel)
        if drift_n == 0:
            print("  无漂移")
        patched = [c["path"] for c in changed if c.get("kind") == "patch"]
        if patched:
            print("  注意：补丁文件漂移（可能被 MAA 升级覆盖）：%s" % ", ".join(patched[:5]))
            print("       处置：回滚补丁 + 重启侧车（见 auto-mas-host-ops 技能「改动纪律」）")
    if drift_n:
        print("SNAPSHOT_DRIFT=DRIFT:%d" % drift_n)
        print("建议回滚: python tools/mas_config_snapshot.py --restore %s" % man.get("snapshot"))
        audit({"action": "drift", "ok": True, "drift": drift_n, "against": man.get("snapshot")})
        return 1
    print("SNAPSHOT_DRIFT=OK")
    audit({"action": "drift", "ok": True, "drift": 0, "against": man.get("snapshot")})
    return 0


# ---------------------------------------------------------------- 恢复演练（不碰生产）

def _break_json_leaf(path: str) -> str | None:
    """把文件里第一个标量叶子改坏；返回字段路径描述（None=没找到）。"""
    obj = load_json(path)
    if obj is None:
        return None
    target = []

    def find(o, p):
        if target:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    find(v, p + [str(k)])
                elif v is not None and not target:
                    target.append((p + [str(k)], k, v))
                    return
                elif v is None and not target:
                    target.append((p + [str(k)], k, v))
                    return
        elif isinstance(o, list):
            for i, v in enumerate(o):
                if isinstance(v, (dict, list)):
                    find(v, p + [str(i)])
                elif not target:
                    target.append((p + [str(i)], str(i), v))
                    return

    find(obj, [])
    if not target:
        return None
    path_keys, key, val = target[0]
    nv = {"b": 17, "i": 17, "f": 1.5, "s": "-BROKEN", "n": "BROKEN"}
    if isinstance(val, bool):
        new = not val
    elif isinstance(val, int):
        new = val + 17
    elif isinstance(val, float):
        new = val + 1.5
    elif isinstance(val, str):
        new = (val + "-BROKEN")[:200]
    elif val is None:
        new = "BROKEN"
    else:
        return None

    def setv(o, keys):
        if len(keys) == 1:
            if isinstance(o, list):
                o[int(keys[0])] = new
            else:
                o[keys[0]] = new
        else:
            k = keys[0]
            setv(o[int(k)] if isinstance(o, list) else o[k], keys[1:])

    setv(obj, path_keys)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    desc = "%s: %s -> %s" % (".".join(str(x) for x in path_keys), repr(val)[:40], repr(new)[:40])
    return desc


def do_drill(args) -> int:
    ts = next_ts("drill")
    drill_dir = os.path.join(DRILLS, ts)
    root = os.path.join(drill_dir, "root")
    os.makedirs(root, exist_ok=True)
    ev: dict = {"drill": ts, "steps": [], "ok": True}
    lines: list[str] = []

    def step(name: str, ok: bool, detail: str = ""):
        ev["steps"].append({"step": name, "ok": bool(ok), "detail": detail[:500]})
        lines.append("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))
        if not ok:
            ev["ok"] = False

    def emit(msg: str):
        lines.append(msg)
        log(msg)

    try:
        snap_a = resolve_snapshot(getattr(args, "against", None) or "latest")
    except SnapshotError as e:
        print("DRILL=FAIL phase1=no-snapshot reason=%s" % e)
        return 4
    man_a = load_json(os.path.join(snap_a, "manifest.json"), {})
    entries_a = man_a.get("files") or []
    with run_lock():
        try:
            # ---- 阶段0：主机 drill 残留自清（crash 后没走到 cleanup 的 scratch；自己/旧的都算）
            ev["residual_before"] = []
            if getattr(args, "skip_remote", False):
                step("residual-cleanup", True, "跳过（--skip-remote）")
            else:
                try:
                    found, rm = remote_drill_residuals(keep=DRILL_PREFIX + ts,
                                                       timeout=int(getattr(args, "timeout", 300)))
                    ev["residual_before"] = found
                    bad_rm = {p: v for p, v in (rm or {}).items() if v not in ("OK", "GONE")}
                    if found:
                        step("residual-cleanup", not bad_rm,
                             "上次未清 %d 个已清：%s" % (len(found),
                                                       ", ".join(os.path.basename(p) for p in found[:5])))
                    else:
                        step("residual-cleanup", True, "无 _cfgsnap_drill_* 残留")
                except (TransportError, SnapshotError, OSError) as e:
                    step("residual-cleanup", False, "残留扫描/清理失败：%s" % str(e)[:200])

            # ---- 阶段1a：将快照物化到本地临时副本（演练"初始状态"）
            emit("阶段1a：物化 %d 个文件到本地临时副本（基线 %s）…" % (len(entries_a), os.path.basename(snap_a)))
            pl = plan_restore(snap_a, "local", root, timeout=int(getattr(args, "timeout", 300)))
            rc = apply_restore(pl, True, argparse.Namespace(no_pre_snapshot=True, timeout=args.timeout))
            step("materialize", rc == 0, "plan diff=%d missing=%d" % (pl["counts"]["diff"], pl["counts"]["missing"]))
            materialized = [p["path"] for p in pl["plan"] if p["state"] in ("diff", "missing") and p["src"] != "none"]
            skipped_sens = [p["path"] for p in pl["plan"] if p["state"] in ("diff", "missing") and p["src"] == "none"]
            step("materialized-count", len(materialized) == pl["counts"]["diff"] + pl["counts"]["missing"] - len(skipped_sens),
                 "物化 %d 个；敏感未物化 %d（设计）" % (len(materialized), len(skipped_sens)))
            if skipped_sens:
                emit("  说明：敏感文件未物化到本地（本地只有脱敏副本）：%s" % ", ".join(skipped_sens[:3]))

            # ---- 阶段1b：改坏（4 类：JSON 字段 / 补丁文件追加 / 删除文件 / 仅触碰 mtime）
            broke: list[dict] = []
            cfg_json = next((e for e in entries_a if e["kind"] == "config" and e["path"].lower().endswith(".json")
                             and e.get("local")), None)
            if cfg_json:
                fp = rel_to_local(root, cfg_json["path"])
                desc = _break_json_leaf(fp)
                if desc:
                    broke.append({"path": cfg_json["path"], "how": "json-field", "desc": desc})
            data_json = next((e for e in entries_a if e["kind"] == "data" and e["path"].lower().endswith("gui.new.json")
                              and e.get("local")), None)
            if data_json:
                fp = rel_to_local(root, data_json["path"])
                desc = _break_json_leaf(fp)
                if desc:
                    broke.append({"path": data_json["path"], "how": "json-field", "desc": desc})
            patch_e = next((e for e in entries_a if e["kind"] == "patch" and e.get("local")), None)
            if patch_e:
                fp = rel_to_local(root, patch_e["path"])
                with open(fp, "ab") as fh:
                    fh.write(b"\r\n# BROKEN-BY-DRILL\r\n")
                broke.append({"path": patch_e["path"], "how": "append-bytes", "desc": "append # BROKEN"})
            del_e = next((e for e in entries_a if e["kind"] == "data" and e.get("local") and e["size"] < 8192
                          and not e["path"].lower().endswith(".bak")), None)
            if del_e:
                fp = rel_to_local(root, del_e["path"])
                if os.path.exists(fp):
                    os.remove(fp)
                broke.append({"path": del_e["path"], "how": "delete", "desc": "删除（验 restore-missing）"})
            touch_e = next((e for e in entries_a if e.get("local") and e["path"] not in
                            {b["path"] for b in broke}), None)
            if touch_e:
                fp = rel_to_local(root, touch_e["path"])
                st = os.stat(fp)
                os.utime(fp, (st.st_atime, st.st_mtime + 3600))
                broke.append({"path": touch_e["path"], "how": "touch-mtime", "desc": "仅 mtime +1h"})
            ev["broke"] = broke
            step("break-files", len(broke) >= 4, "改坏 %d 个：%s" % (len(broke), ", ".join(b["how"] for b in broke)))

            # ---- 阶段1c：对改坏后的树做快照 B，跑 --diff 找差异
            snap_b = os.path.join(drill_dir, "snapB")
            sargs = argparse.Namespace(label="drill-broken", source=root, out=snap_b, json=False,
                                       no_remote_store=True, no_lock=True, keep=0, timeout=args.timeout)
            rc = do_snapshot(sargs)
            man_b = load_json(os.path.join(snap_b, "manifest.json"), {}) or {}
            step("snapshot-broken", rc == 0, "snapshot B @ %s（files=%s）"
                 % (snap_b, (man_b.get("counts") or {}).get("files")))
            rep = diff_snapshots(snap_a, snap_b, skip_missing_sensitive=True)
            det = "改动 %d / 新增 %d / 删除 %d / 字段报告 %d" % (
                len(rep["changed"]), len(rep["added"]), len(rep["removed"]), len(rep["fields"]))
            expect_paths = {b["path"] for b in broke}
            got_paths = ({c["path"] for c in rep["changed"]} | {c["path"] for c in rep["removed"]}
                         | {c["path"] for c in rep["mtime_only"]})
            miss = expect_paths - got_paths
            step("diff-detects", not miss, det if not miss else "%s；漏检：%s" % (det, sorted(miss)))
            for f in rep["fields"]:
                for ch in (f.get("changes") or [])[:4]:
                    emit("  diff字段: %s %s %s -> %s" % (f["path"], ch["path"], ch.get("old"), ch.get("new")))
            ev["diff"] = {"changed": rep["changed"], "fields": rep["fields"], "removed": rep["removed"]}

            # ---- 阶段1d：dry-run 回滚（显示将覆盖什么）→ 真回滚（本地副本）→ 校验
            pl2 = plan_restore(snap_a, "local", root, timeout=int(getattr(args, "timeout", 300)))
            print_plan(pl2, False, sink=lines)
            emit("dry-run 计划：覆盖 %d / 新建 %d / 已是快照 %d / 不可恢复 %d"
                 % (pl2["counts"]["diff"], pl2["counts"]["missing"],
                    pl2["counts"]["same"], pl2["counts"]["unrecoverable"]))
            rc = apply_restore(pl2, True, argparse.Namespace(no_pre_snapshot=True, timeout=args.timeout))
            step("restore-apply", rc == 0, "RESTORE rc=%d（将 mtime-only 不还原：内容级回滚）" % rc)

            bad = []
            for e in entries_a:
                if not e.get("local") and not e.get("redacted"):
                    continue
                fp = rel_to_local(root, e["path"])
                if not os.path.exists(fp):
                    if e.get("local"):
                        bad.append(e["path"] + "(缺失)")
                    continue
                if e.get("local") and sha256_file(fp) != e["sha256"]:
                    bad.append(e["path"] + "(哈希不符)")
            step("verify-hashes", not bad, "全部字段校验通过" if not bad else "异常 %d：%s" % (len(bad), bad[:5]))

            # ---- 阶段1e：敏感文件不可本地物化是设计行为（明示）
            if skipped_sens:
                step("sensitive-not-local", True, "敏感文件 %s 仅主机侧保全（本地不可物化，符合口径）"
                     % ", ".join(skipped_sens[:2]))
        except (TransportError, SnapshotError, OSError) as e:
            step("phase1-exception", False, str(e)[:300])

        # ---- 阶段2：主机 scratch 上真回滚（含敏感文件，全保走主机 store；不碰生产路径）
        ev["phase2"] = {"skipped": True}
        store_dir = (man_a.get("remote_store") or {}).get("dir")
        if not store_dir:
            step("phase2", True, "跳过：该快照无主机 store（--no-remote-store 采集）")
        elif not getattr(args, "skip_remote", False):
            scratch = REMOTE_BACKUPS + "\\" + DRILL_PREFIX + ts
            try:
                emit("阶段2：主机 scratch 演练 %s" % scratch)
                emit("  2a 计划：对比 scratch 现状（%d 文件；物化走 HTTP 拉取通道）…" % len(entries_a))
                pl3 = plan_restore(snap_a, "remote", scratch, timeout=int(getattr(args, "timeout", 300)))
                emit("  2a 完成：新建 %d / 覆盖 %d / 已是快照 %d"
                     % (pl3["counts"]["missing"], pl3["counts"]["diff"], pl3["counts"]["same"]))
                rc = apply_restore(pl3, True, argparse.Namespace(no_pre_snapshot=True, timeout=args.timeout))
                emit("  2b 物化完成 rc=%d" % rc)
                step("remote-scratch-restore", rc == 0,
                     "全保物化 %d 个（含敏感：走 store 同机复制）" % (pl3["counts"]["diff"] + pl3["counts"]["missing"]))
                # 破坏两个文件（含敏感）后再回滚一次
                scratch_patch = scratch + "\\" + (KNOWN_PATCHES[0] if any(
                    e["path"] == KNOWN_PATCHES[0] for e in entries_a) else entries_a[0]["path"])
                r1 = remote_append_bytes(scratch_patch)
                sens_path = next((e["path"] for e in entries_a if e.get("sensitive")), None)
                r2 = {"ok": True}
                if sens_path:
                    r2 = remote_append_bytes(scratch + "\\" + sens_path)
                step("remote-break", r1.get("ok", False) and r2.get("ok", False),
                     "改坏 %s%s" % (scratch_patch.split("\\")[-1], (" + " + sens_path.split("\\")[-1]) if sens_path else ""))
                pl4 = plan_restore(snap_a, "remote", scratch, timeout=int(getattr(args, "timeout", 300)))
                rc = apply_restore(pl4, True, argparse.Namespace(no_pre_snapshot=True, timeout=args.timeout))
                emit("  2d 二次回滚完成 rc=%d（diff=%d）" % (rc, pl4["counts"]["diff"]))
                step("remote-restore-again", rc == 0, "二次回滚 diff=%d" % pl4["counts"]["diff"])
                if not getattr(args, "keep_scratch", False):
                    rm = remote_rmdir([scratch], REMOTE_BACKUPS + "\\" + DRILL_PREFIX)
                    ok_rm = rm.get(scratch) in ("OK", "GONE")
                    step("remote-cleanup", ok_rm, "清理 scratch：%s" % rm.get(scratch))
                    ev["phase2"] = {"scratch": scratch, "cleaned": True}
                else:
                    step("remote-cleanup", True, "保留 scratch（--keep-scratch）")
                    ev["phase2"] = {"scratch": scratch, "cleaned": False}
            except (TransportError, SnapshotError) as e:
                step("phase2-exception", False, str(e)[:300])
                with contextlib.suppress(Exception):
                    remote_rmdir([scratch], REMOTE_BACKUPS + "\\" + DRILL_PREFIX)
        else:
            step("phase2", True, "跳过（--skip-remote）")

    os.makedirs(drill_dir, exist_ok=True)
    ev["finished_at"] = _now()
    with open(os.path.join(drill_dir, "evidence.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    save_json(os.path.join(drill_dir, "evidence.json"), ev)
    audit({"action": "drill", "ok": ev["ok"], "drill": ts, "steps": len(ev["steps"])})
    print("\n".join(lines))
    print("DRILL=%s drill=%s evidence=%s" % ("PASS" if ev["ok"] else "FAIL", ts,
                                             os.path.join(drill_dir, "evidence.txt")))
    return 0 if ev["ok"] else 4


# ---------------------------------------------------------------- 自检（离线）

def selftest() -> int:
    import tempfile
    global LOG, RUNTIME, LOCK
    _old_log, _old_runtime, _old_lock = LOG, RUNTIME, LOCK
    _td = tempfile.mkdtemp(prefix="mas_snap_selftest_")
    LOG, RUNTIME = os.path.join(_td, "log.jsonl"), _td     # 自检不污染真实审计流
    LOCK = os.path.join(_td, "mas_config_snapshot.lock")   # 锁用例全走临时路径，不碰真实锁
    bad = 0
    total = 0

    def check(label, cond, extra=""):
        nonlocal bad, total
        total += 1
        ok = bool(cond)
        bad += 0 if ok else 1
        print("%s %-46s %s" % ("PASS" if ok else "FAIL", label, extra))

    # 1) 脱敏
    raw = b'{"Info": {"Id": "13412345276", "Password": "AQAAANCMnd8BFdERjHoAwE/Cl+AAAAAAA"}, "List": [{"Token": "abc"}], "Empty": ""}'
    cls = classify_and_redact(raw)
    check("classify: Password/Token => sensitive", cls["sensitive"] and cls["count"] == 2, str(cls["count"]))
    txt = cls["redacted"].decode("utf-8")
    check("redact: 密文替换为标记", "AQAAANCMnd" not in txt and "[已脱敏]" in txt)
    check("redact: 空值不动", '"Empty": ""' in txt)
    check("redact: 无长b64兜底触发", "refused" not in cls)
    raw2 = json.dumps({"Custom": "x" * 80}).encode()
    cls2 = classify_and_redact(raw2)
    check("classify: 无敏感键非敏感", not cls2["sensitive"])

    # 2) json_field_diff
    a = {"Q": [{"uid": "u1", "Time": "19:33", "On": True}, {"uid": "u2", "Time": "19:45"}],
         "N": {"x": 1}, "Gone": 5}
    b = {"Q": [{"uid": "u1", "Time": "19:45", "On": True}, {"uid": "u3", "Time": "19:45"}],
         "N": {"x": 2, "y": 9}, "New": "z"}
    ch, el = json_field_diff(a, b, 200)
    paths = {(c["op"], c["path"]) for c in ch}
    check("diff: 列表按键对齐(改)", ("chg", "Q[uid=u1].Time") in paths, str(sorted(paths)[:4]))
    check("diff: 列表按键对齐(删/增)", ("del", "Q[uid=u2]") in paths and ("add", "Q[uid=u3]") in paths)
    check("diff: dict 数值变化", ("chg", "N.x") in paths)
    check("diff: dict 新增键", ("add", "N.y") in paths)
    check("diff: 顶层删键", ("del", "Gone") in paths)
    ch2, _ = json_field_diff({"L": [1, 2, 3]}, {"L": [1, 9, 3]}, 200)
    check("diff: 无键列表按索引", any(c["path"] == "L[1]" for c in ch2), str(ch2))
    ch3, _ = json_field_diff({"s": "x" * 200}, {"s": "y" * 200}, 200)
    check("diff: 长值截断", len(ch3[0]["new"]) <= 81, str(len(ch3[0]["new"])))

    # 3) 敏感值不出现在 diff 文本
    ch4, _ = json_field_diff({"Password": "REAL-SECRET-1"}, {"Password": "REAL-SECRET-2"}, 200)
    blob = json.dumps(ch4, ensure_ascii=False)
    check("diff: 敏感键值隐去", "REAL-SECRET" not in blob, blob[:60])

    # 4) 记录写入/读取 + plan/apply 本地回滚
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "base")
        os.makedirs(os.path.join(base, "config"))
        os.makedirs(os.path.join(base, "data", "s1", "Default", "ConfigFile"))
        with open(os.path.join(base, "config", "QueueConfig.json"), "w", encoding="utf-8") as fh:
            fh.write('{"a": 1}')
        with open(os.path.join(base, "data", "s1", "Default", "ConfigFile", "gui.json"), "w", encoding="utf-8") as fh:
            fh.write('{"gui": true}')
        with open(os.path.join(base, "config", "ScriptConfig.json"), "w", encoding="utf-8") as fh:
            fh.write('{"U": {"Password": "cipher-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"}}')
        snap = os.path.join(td, "snap")
        import types
        rc = do_snapshot(types.SimpleNamespace(label="st", source=base, out=snap, json=False,
                                               no_remote_store=True, no_lock=True, keep=0, timeout=60))
        check("snapshot(local): rc=0", rc == 0)
        man = load_json(os.path.join(snap, "manifest.json"), {})
        by = manifest_files(man)
        check("snapshot: 敏感文件只留脱敏副本", by["config\\ScriptConfig.json"]["sensitive"]
              and by["config\\ScriptConfig.json"]["local"] is None
              and by["config\\ScriptConfig.json"]["redacted"] is not None)
        check("snapshot: 非敏感留全保副本", by["config\\QueueConfig.json"]["local"] is not None)
        # 改坏（3 类：普通 JSON 字段 / 删除文件 / 敏感文件）
        with open(os.path.join(base, "config", "QueueConfig.json"), "w", encoding="utf-8") as fh:
            fh.write('{"a": 999}')
        os.remove(os.path.join(base, "data", "s1", "Default", "ConfigFile", "gui.json"))
        with open(os.path.join(base, "config", "ScriptConfig.json"), "w", encoding="utf-8") as fh:
            fh.write('{"U": {"Password": "BREAKEN-cipher-xxxxxxxxxxxxxxxxxxxxxxxx"}}')
        pl = plan_restore(snap, "local", base, timeout=60)
        check("plan: 识别 diff/missing/unrecoverable",
              pl["counts"]["diff"] == 2 and pl["counts"]["missing"] == 1 and pl["counts"]["unrecoverable"] == 1,
              str(pl["counts"]))
        sens_entry = next((p for p in pl["plan"] if p["sensitive"]), None)
        check("plan: 敏感文件标注不可本地恢复", bool(sens_entry) and sens_entry["src"] == "none")
        rc = apply_restore(pl, True, types.SimpleNamespace(no_pre_snapshot=True, timeout=60))
        check("restore(local): rc=0（敏感跳过不算失败）", rc == 0)
        with open(os.path.join(base, "config", "QueueConfig.json"), encoding="utf-8") as fh:
            check("restore: 内容还原", json.load(fh) == {"a": 1})
        check("restore: missing 重建", os.path.exists(os.path.join(base, "data", "s1", "Default", "ConfigFile", "gui.json")))
        with open(os.path.join(base, "config", "ScriptConfig.json"), encoding="utf-8") as fh:
            check("restore: 敏感文件被跳过未动", "BREAKEN" in fh.read())
        pl2 = plan_restore(snap, "local", base, timeout=60)
        rest_all_same = all(p["state"] == "same" for p in pl2["plan"] if not p["sensitive"])
        check("plan: 回滚后非敏感全 same", rest_all_same and pl2["counts"]["missing"] == 0, str(pl2["counts"]))
        # 额外文件不被删（restore 只恢复快照内文件）
        extra = os.path.join(base, "config", "extra.json")
        with open(extra, "w", encoding="utf-8") as fh:
            fh.write("{}")
        apply_restore(plan_restore(snap, "local", base, timeout=60), True,
                      types.SimpleNamespace(no_pre_snapshot=True, timeout=60))
        check("restore: 快照外文件不动", os.path.exists(extra))
        # diff A/B
        snap2 = os.path.join(td, "snap2")
        with open(os.path.join(base, "config", "QueueConfig.json"), "w", encoding="utf-8") as fh:
            fh.write('{"a": 2}')
        do_snapshot(types.SimpleNamespace(label="st2", source=base, out=snap2, json=False,
                                          no_remote_store=True, no_lock=True, keep=0, timeout=60))
        rep = diff_snapshots(snap, snap2)
        check("diff A/B: 捕到改动", any(c["path"] == "config\\QueueConfig.json" for c in rep["changed"]))
        check("diff A/B: 字段级可读", rep["fields"] and rep["fields"][0]["changes"], str(rep["fields"])[:80])

    # 5) 路径安全
    try:
        norm_rel("..\\..\\x")
        check("path: 拒绝越界", False)
    except SnapshotError:
        check("path: 拒绝越界", True)
    check("path: 归一化", norm_rel("data/a/b") == "data\\a\\b")

    # 6) 轮转
    with tempfile.TemporaryDirectory() as td:
        global SNAPS
        old = SNAPS
        SNAPS = td
        for n in ["20260101_000000", "20260102_000000", "20260103_000000"]:
            os.makedirs(os.path.join(td, n))
        pruned = rotate_snapshots(2)
        check("rotate: 只留最新 N", pruned == ["20260101_000000"] and len(os.listdir(td)) == 2, str(pruned))
        SNAPS = old

    # 7) _break_json_leaf
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "x.json")
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write('{"a": {"b": "keep"}}')
        desc = _break_json_leaf(fp)
        after = load_json(fp)
        check("break: 叶子被改坏", desc is not None and after["a"]["b"] != "keep", str(desc))

    # 8) 锁自愈（t_6fa92a0a）：死锁清 / 活锁拒 / 判不了回退 2h
    dead = subprocess.Popen([sys.executable, "-c", "pass"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    dead_pid = dead.pid
    dead.wait()
    check("pid_alive: 死 pid => False", pid_alive(dead_pid) is False, "pid=%d" % dead_pid)
    check("pid_alive: 本进程 => True", pid_alive(os.getpid()) is True)
    with open(LOCK, "wb") as fh:                 # 构造死锁（mtime 新鲜：旧逻辑会拒满 2h）
        fh.write(str(dead_pid).encode())
    got = held = False
    try:
        with run_lock():
            got = True
            with open(LOCK, encoding="ascii") as fh:
                held = fh.read().strip() == str(os.getpid())
    except SnapshotError:
        pass
    check("lock: 死锁被清（新鲜 mtime 不等 2h）", got and held and not os.path.exists(LOCK),
          "dead_pid=%d got=%s held=%s" % (dead_pid, got, held))
    with open(LOCK, "wb") as fh:                 # 活锁：本进程 pid（活着）+ 新鲜 mtime
        fh.write(str(os.getpid()).encode())
    refused = False
    try:
        with run_lock():
            pass
    except SnapshotError:
        refused = True
    kept_live = os.path.exists(LOCK) and read_lock_pid(LOCK) == os.getpid()
    check("lock: 活锁按原样拒绝（不抢、锁保留）", refused and kept_live,
          "refused=%s kept=%s" % (refused, kept_live))
    with open(LOCK, "wb") as fh:                 # 判不了活（非数字内容）：回退 2h 逻辑
        fh.write(b"not-a-pid")
    refused2 = False
    try:
        with run_lock():
            pass
    except SnapshotError:
        refused2 = True
    kept_fresh = os.path.exists(LOCK)
    old_t = time.time() - (LOCK_STALE_S + 60)
    os.utime(LOCK, (old_t, old_t))               # 超期 => 老逻辑清理
    got2 = False
    try:
        with run_lock():
            got2 = True
    except SnapshotError:
        pass
    check("lock: 判不了活→回退 2h（新鲜拒/超期清）", refused2 and kept_fresh and got2,
          "refused=%s fresh_kept=%s stale_cleared=%s" % (refused2, kept_fresh, got2))

    print("selftest: %s (%d/%d)" % ("OK" if not bad else "FAIL", total - bad, total))
    LOG, RUNTIME, LOCK = _old_log, _old_runtime, _old_lock
    shutil.rmtree(_td, ignore_errors=True)
    return 0 if not bad else 4


# ---------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser(description="AUTO-MAS 配置快照与一键回滚（默认动作=快照）",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", help="快照标签（如 pre-change / pre-restore）")
    ap.add_argument("--keep", type=int, default=KEEP_DEFAULT, help="本地/主机快照保留数（默认 %d）" % KEEP_DEFAULT)
    ap.add_argument("--no-remote-store", action="store_true", help="敏感文件不做主机侧全保 store")
    ap.add_argument("--source", help="本地源目录（演练/离线快照；默认远程生产）")
    ap.add_argument("--out", help="快照输出目录（默认 runtime/snapshots/<ts>）")
    ap.add_argument("--timeout", type=int, default=300, help="单次 ssh 传输超时秒（默认 300）")

    ap.add_argument("--list", action="store_true", help="列出快照")
    ap.add_argument("--diff", nargs=2, metavar=("A", "B"), help="两次快照差异")
    ap.add_argument("--only", help="路径 glob 过滤器（--diff/--restore）")
    ap.add_argument("--max-changes", type=int, default=200, help="字段级 diff 每文件上限")
    ap.add_argument("--restore", metavar="TS", help="回滚到快照 TS（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="真回滚（默认 dry-run）")
    ap.add_argument("--to", help="本地目标目录（临时副本演练；与 --remote-root 二选一）")
    ap.add_argument("--remote-root", help="远程目标根（临时副本演练用；默认生产 %s）" % REMOTE_ROOT)
    ap.add_argument("--no-pre-snapshot", action="store_true", help="生产回滚时不自动前置快照（不推荐）")
    ap.add_argument("--drift", action="store_true", help="当前生产 vs 最近快照（只读哈希扫描）")
    ap.add_argument("--against", help="--drift/--drill 的基准快照（默认 latest）")
    ap.add_argument("--include-temp", action="store_true", help="漂移检测包含 data\\*\\Temp\\**")
    ap.add_argument("--drill", action="store_true", help="恢复演练（本地临时副本 + 主机 scratch）")
    ap.add_argument("--skip-remote", action="store_true", help="演练跳过主机 scratch 阶段")
    ap.add_argument("--keep-scratch", action="store_true", help="演练后保留主机 scratch 目录")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--selftest", action="store_true", help="离线自检")
    args = ap.parse_args()

    try:
        if args.selftest:
            return selftest()
        if args.list:
            rows = list_snapshots()
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=1))
            else:
                print("== 本地快照（%s） ==" % SNAPS)
                for r in rows:
                    print("  %-32s %-12s files=%-5s %.1fMB sens=%s store=%s"
                          % (r["snapshot"], r["label"] or "-", r["files"],
                             (r["bytes"] or 0) / 1024 / 1024, r["sensitive"], "yes" if r["store"] else "-"))
                if not rows:
                    print("  （无）")
            return 0
        if args.diff:
            a = resolve_snapshot(args.diff[0])
            b = resolve_snapshot(args.diff[1])
            rep = diff_snapshots(a, b, only=args.only, max_changes=args.max_changes)
            print_diff(rep, args.json)
            return 0
        if args.restore:
            snap_dir = resolve_snapshot(args.restore)
            if args.to and args.remote_root:
                log("--to 与 --remote-root 不能同时给")
                return 5
            if args.to:
                root_kind, root = "local", os.path.abspath(args.to)
                os.makedirs(root, exist_ok=True)
            else:
                root_kind, root = "remote", (args.remote_root or REMOTE_ROOT).rstrip("\\/")
            pl = plan_restore(snap_dir, root_kind, root, only=args.only, timeout=args.timeout)
            print_plan(pl, args.json)
            if not args.apply:
                c = pl["counts"]
                print("RESTORE=DRY-RUN diff=%d missing=%d unrecoverable=%d（未写任何文件；加 --apply 执行）"
                      % (c["diff"], c["missing"], c["unrecoverable"]))
                return 0
            with run_lock():
                rc = apply_restore(pl, True, args)
            if rc != 0:
                return rc
            if args.json:
                print(json.dumps({"snapshot": pl["snapshot"], "root": root, "counts": pl["counts"]},
                                 ensure_ascii=False))
            else:
                print("回滚完成：快照 %s → %s" % (pl["snapshot"], root))
            return 0
        if args.drift:
            return do_drift(args)
        if args.drill:
            return do_drill(args)
        # 默认：快照
        return do_snapshot(args)
    except (TransportError, SnapshotError) as e:
        log("失败：%s" % e)
        audit({"action": "error", "ok": False, "reason": str(e)[:200]})
        return 2


if __name__ == "__main__":
    sys.exit(main())
