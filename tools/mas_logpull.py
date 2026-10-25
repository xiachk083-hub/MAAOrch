#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""日志拉取层：劣化 SSH 通道下的 gzip + 增量 + 双上限（G5 加固，t_4aa03f79）。

背景（docs/MAS_SHIFT_GAPS.md §G5）：主机 SSH 通道劣化（有效带宽 <12KB/s，多 worker
共用）时，「全量拉日志」类工具双双超时——`mas_identity_trace.py report` 拉
`MAA_6\gui.log`（2.34MB）两次 TimeoutExpired（>300s）；`maa_error_daily.py` 一次
>300s 静默、一次 `UNKNOWN:ssh 超时`。本模块是两工具共用的拉取层，口径：

  * **压缩**：远端 gzip → base64 单行回传；SSH 侧再加 `-C`（zlib 压缩握手）。
  * **增量**：按远端路径记录 `offset`（状态 `runtime/logpull_state.json`），
    第二次起只拉新增尾巴；本地 `runtime/logpull_cache/` 合并缓存读回全文，
    消费方（如 report 的窗口解析）语义不变。文件被截断（size < offset）→ 自动
    重置并从 0 全拉。
  * **双上限（fail-closed）**：
      - 字节上限 `max_bytes`（wire 侧 base64 字符数）：远端在**发送载荷前**检查，
        超限只回 `META|status=too_big`（零载荷），本地随即抛 `LogPullError("too_large")`；
      - 秒上限 `timeout`：subprocess 硬超时 kill，抛 `LogPullError("timeout")`；
        再配 `ServerAliveInterval=15` 让死链路 ~60s 断言。
    **绝不静默挂死**：任何超限都是快速返回的异常，由调用方翻成 `UNKNOWN:<原因>`。
  * **缺失文件**：`status=missing` → 返回缓存（无则空串），不更新 offset（与
    `mas_identity_trace` 原「Test-Path 不存在回空」语义一致）。

设计约定：
  * `transport` 可注入（离线自测）：`transport(job, timeout) -> bytes`，job =
    {path, offset, filters, max_bytes, maxlines}。三种实现：
      - `SshTransport`：真机（`ssh -C … powershell -EncodedCommand`）；
      - `LocalPsTransport`：本机 PowerShell 跑同一段远端脚本（离线集成自测）；
      - `PyModelTransport`：纯 Python 镜像语义（单元自测；与 LocalPs 做差分对照）。
  * 远端脚本**只读**打开（FileShare::ReadWrite，日志正在被写）：seek(offset) →
    精确读 `size-offset` 字节 → 可选行过滤 → gzip → base64。
  * 各工具原有 `--timeout` 语义原样透传（本模块只做上限执行，不改 CLI 口径）。

用法（库）：

    from mas_logpull import pull_text, LogPullError
    text = pull_text(r"E:\MAA-Multi\MAA_6\debug\gui.log", filters=["Task Chain", "->>"],
                     timeout=300, max_bytes=4*1024*1024)

用法（自检 / 现场）：

    python tools/mas_logpull.py --selftest        # 离线：Python 模型 + 本地 PS 差分
    python tools/mas_logpull.py --pull "<远端路径>" [--filters A,B] [--max-bytes N]
                                  [--timeout S] [--localps] [--no-incremental] [--out F]

退出码：--selftest 0=PASS / 1=FAIL；--pull 0=成功 / 2=失败（fail-closed）。
末行判据：LOGPULL_SELFTEST=PASS|FAIL ・ LOGPULL=<OK|UNKNOWN:原因>。
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(ROOT, "runtime")
DEFAULT_STATE = os.path.join(RUNTIME, "logpull_state.json")
DEFAULT_CACHE = os.path.join(RUNTIME, "logpull_cache")

SSH_KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")

DEF_MAX_BYTES = int(os.environ.get("MAAORCH_LOGPULL_MAX_BYTES", str(4 * 1024 * 1024)))
DEF_KEEP_BYTES = int(os.environ.get("MAAORCH_LOGPULL_KEEP_BYTES", str(16 * 1024 * 1024)))
RAW_STDOUT_CAP = 48 * 1024 * 1024          # 本地硬护栏：stdout 原始字节
PS_MAXLINES = 200000                       # 远端过滤行数上限（安全阀）
ENC_LIMIT = 7800                           # 远端 cmd.exe 8191 上限内的 EncodedCommand 预算


class LogPullError(Exception):
    """拉取层统一异常（fail-closed）。

    kind:
      * ``timeout``    —— 秒上限（subprocess 硬超时 kill）
      * ``too_large``  —— 字节上限（远端发送前检查 / 本地复检）
      * ``remote``     —— 远端脚本报错（status=error）
      * ``bad_output`` —— 远端输出不可解析（无 META / 载荷坏）
      * ``ssh``        —— ssh 进程失败（rc!=0 / 起不来）
    """

    def __init__(self, kind: str, message: str, **detail):
        super().__init__(message)
        self.kind = kind
        self.detail = detail

    def short(self) -> str:
        return "%s:%s" % (self.kind, self)


def _psq(s: str) -> str:
    """PowerShell 单引号字面量（内部单引号翻倍）。"""
    return "'" + str(s).replace("'", "''") + "'"


def build_remote_script(job: dict) -> str:
    """远端拉取脚本（minified；EncodedCommand 上限内）。"""
    filters = list(job.get("filters") or [])
    if filters:
        f_arr = "@(" + ",".join(_psq(f) for f in filters) + ")"
    else:
        f_arr = "@()"
    path = _psq(job["path"])
    off = int(job.get("offset") or 0)
    maxb = int(job.get("max_bytes") or 0)
    maxlines = int(job.get("maxlines") or PS_MAXLINES)
    script = (
        "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$p=" + path + ";$o=" + str(off) + ";$m=" + str(maxb) + ";$f=" + f_arr + ";"
        "$ml=" + str(maxlines) + ";"
        "if(-not(Test-Path -LiteralPath $p)){Write-Output 'META|status=missing';exit 0};"
        "$s=$null;try{"
        "$s=[IO.File]::Open($p,3,1,3);$sz=$s.Length;$st=$o;$r0=0;"
        "if($st -gt $sz){$st=0;$r0=1};$rm=$sz-$st;"
        "if($rm -eq 0){Write-Output ('META|status=empty;size='+$sz+';start='+$st+';sent=0;reset='+$r0);exit 0};"
        "if(($f.Count -eq 0)-and($m -gt 0)-and($rm -gt $m)){"
        "Write-Output ('META|status=too_big;size='+$sz+';start='+$st+';remain='+$rm+';limit='+$m);exit 0};"
        "$null=$s.Seek($st,0);"
        "$b=New-Object byte[] $rm;$n=0;"
        "while($n -lt $rm){$k=$s.Read($b,$n,$rm-$n);if($k -le 0){break};$n+=$k};"
        "$t=[Text.Encoding]::UTF8.GetString($b,0,$n);"
        "if($f.Count -gt 0){$x=(@($f|%{[regex]::Escape($_)})-join '|');"
        "$q=New-Object Collections.Generic.List[string];"
        "foreach($l in($t -split \"`n\")){if($q.Count -ge $ml){break};"
        "if($l -match $x){$q.Add($l.TrimEnd([char]13))}};$t=($q -join \"`n\")};"
        "$pb=[Text.Encoding]::UTF8.GetBytes($t);"
        "$ms=New-Object IO.MemoryStream;"
        "$gz=New-Object IO.Compression.GZipStream($ms,[IO.Compression.CompressionMode]::Compress);"
        "$gz.Write($pb,0,$pb.Length);$gz.Dispose();"
        "$b64=[Convert]::ToBase64String($ms.ToArray());$ms.Dispose();"
        "if(($m -gt 0)-and($b64.Length -gt $m)){"
        "Write-Output ('META|status=too_big;size='+$sz+';start='+$st+';remain='+$rm+';limit='+$m);exit 0};"
        "Write-Output ('META|status=ok;size='+$sz+';start='+$st+';sent='+$b64.Length+';reset='+$r0+';scanned='+$n);"
        "Write-Output ('PAYLOAD|'+$b64)"
        "}catch{Write-Output ('META|status=error;msg='+($_.Exception.Message -replace '[\r\n|]',' '))}"
        "finally{if($s){$s.Close()}}"
    )
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    if len(b64) > ENC_LIMIT:
        raise LogPullError("remote", "远端脚本超 EncodedCommand 预算（b64=%d）" % len(b64))
    return script


# ---------------------------------------------------------------- transport

def ssh_argv(host: "str | None" = None, key: "str | None" = None, *, ssh_exe: "str | None" = None,
             compress: bool = True, connect_timeout: int = 20, alive: bool = True,
             extra: "list | None" = None) -> list:
    """ssh 基础 argv（-C 压缩；死链路 ~60s 断言）。MCP 场景调用方可传 ssh_exe/env。"""
    if ssh_exe is None:
        p = r"C:\Windows\System32\OpenSSH\ssh.exe"
        ssh_exe = p if os.path.exists(p) else (shutil.which("ssh") or "ssh")
    argv = [ssh_exe]
    if compress:
        argv.append("-C")
    argv += ["-i", key or SSH_KEY, "-o", "StrictHostKeyChecking=no",
             "-o", "BatchMode=yes", "-o", "ConnectTimeout=%d" % connect_timeout]
    if alive:
        argv += ["-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4"]
    argv += list(extra or [])
    argv.append(host or SSH_HOST)
    return argv


class SshTransport:
    """真机传输：ssh -C → 远端 PowerShell EncodedCommand。"""

    def __init__(self, host: "str | None" = None, key: "str | None" = None, *,
                 ssh_exe: "str | None" = None, env: "dict | None" = None, compress: bool = True):
        self.host = host or SSH_HOST
        self.key = key or SSH_KEY
        self.ssh_exe = ssh_exe
        self.env = env
        self.compress = compress
        self.last_cmd = None

    def describe(self) -> str:
        return "ssh%s %s" % (" -C" if self.compress else "", self.host)

    def run(self, job: dict, timeout: float) -> bytes:
        script = build_remote_script(job)
        enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        argv = ssh_argv(self.host, self.key, ssh_exe=self.ssh_exe, compress=self.compress)
        argv.append("powershell -NoProfile -EncodedCommand " + enc)
        self.last_cmd = argv
        try:
            r = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL,
                               timeout=timeout, env=self.env)
        except subprocess.TimeoutExpired as e:  # fail-closed：硬 kill
            raise LogPullError("timeout", "拉取超时（>%.0fs）：%s" % (timeout, job.get("path"))) from e
        except OSError as e:
            raise LogPullError("ssh", "ssh 起不来：%s" % e) from e
        if r.returncode != 0:
            raise LogPullError("ssh", "ssh rc=%d：%s" % (
                r.returncode, (r.stderr or b"").decode("utf-8", "replace")[:200]))
        return r.stdout


class LocalPsTransport:
    """本机 PowerShell 跑同一段远端脚本（离线集成自测；路径按本机解析）。"""

    def __init__(self, powershell: "str | None" = None):
        self.ps = powershell or shutil.which("powershell") or "powershell.exe"

    def describe(self) -> str:
        return "localps %s" % self.ps

    def available(self) -> bool:
        return bool(shutil.which(self.ps) or os.path.exists(self.ps))

    def run(self, job: dict, timeout: float) -> bytes:
        script = build_remote_script(job)
        enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        argv = [self.ps, "-NoProfile", "-NonInteractive", "-EncodedCommand", enc]
        try:
            r = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL,
                               timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise LogPullError("timeout", "拉取超时（>%.0fs）：%s" % (timeout, job.get("path"))) from e
        if r.returncode != 0:
            raise LogPullError("ssh", "powershell rc=%d：%s" % (
                r.returncode, (r.stderr or b"").decode("utf-8", "replace")[:200]))
        return r.stdout


class PyModelTransport:
    """纯 Python 镜像语义（自测差分用；不碰网络/进程）。"""

    def describe(self) -> str:
        return "pymodel"

    def run(self, job: dict, timeout: float) -> bytes:
        return envoy(job)


def envoy(job: dict) -> bytes:
    """Python 侧的「远端脚本」镜像：与 PS 脚本同协议（供差分对照）。"""
    path, off = job["path"], int(job.get("offset") or 0)
    filters, maxb = list(job.get("filters") or []), int(job.get("max_bytes") or 0)
    if not os.path.exists(path):
        return b"META|status=missing"
    size = os.path.getsize(path)
    st, reset = (off, 0) if off <= size else (0, 1)
    if size - st == 0:
        return ("META|status=empty;size=%d;start=%d;sent=0;reset=%d" % (size, st, reset)).encode()
    with open(path, "rb") as f:
        f.seek(st)
        data = f.read(size - st)
    if not filters and maxb > 0 and len(data) > maxb:
        return ("META|status=too_big;size=%d;start=%d;remain=%d" % (size, st, len(data))).encode()
    text = data.decode("utf-8", "replace")
    if filters:
        import re as _re
        rx = _re.compile("|".join(_re.escape(x) for x in filters))
        lines = [ln.rstrip("\r") for ln in text.split("\n") if rx.search(ln)]
        text = "\n".join(lines[:PS_MAXLINES])
    b64 = base64.b64encode(gzip.compress(text.encode("utf-8"))).decode("ascii")
    if maxb > 0 and len(b64) > maxb:
        return ("META|status=too_big;size=%d;start=%d;remain=%d" % (size, st, size - st)).encode()
    meta = "META|status=ok;size=%d;start=%d;sent=%d;reset=%d;scanned=%d" % (
        size, st, len(b64), reset, size - st)
    return (meta + "\nPAYLOAD|" + b64).encode()


# ---------------------------------------------------------------- 协议解析

def parse_out(raw: bytes) -> dict:
    """远端 stdout（bytes）-> {'meta': {...}, 'payload_b64': str|None}。"""
    if len(raw) > RAW_STDOUT_CAP:
        raise LogPullError("too_large", "stdout 原始字节超护栏（%d）" % len(raw))
    text = raw.decode("utf-8", "replace")
    meta = None
    payload = None
    for ln in text.splitlines():
        if ln.startswith("META|"):
            meta = {}
            for kv in ln[5:].split(";"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    meta[k] = v
        elif ln.startswith("PAYLOAD|"):
            payload = ln[8:]
    if meta is None:
        raise LogPullError("bad_output", "远端输出无 META：%r" % text[:200])
    st = meta.get("status")
    if st == "error":
        raise LogPullError("remote", "远端脚本错误：%s" % meta.get("msg", "")[:200])
    if st == "too_big":
        raise LogPullError("too_large", "拉取超字节上限：remain=%s sent=%s limit=%s path=%s" % (
            meta.get("remain"), meta.get("sent"), meta.get("limit", "-"), meta.get("path", "")))
    if payload is None and st in ("ok",):
        raise LogPullError("bad_output", "status=ok 但缺 PAYLOAD")
    return {"meta": meta, "payload_b64": payload}


def _decode_payload(b64: str) -> str:
    try:
        raw = base64.b64decode(b64 or "")
        return gzip.decompress(raw).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        raise LogPullError("bad_output", "载荷解码失败：%s" % e) from e


# ---------------------------------------------------------------- 状态 / 缓存

def _key_of(path: str, filters) -> str:
    sig = "|".join(sorted(str(f) for f in (filters or [])))
    return hashlib.sha1(("%s\n%s\n%s" % (SSH_HOST, path, sig)).encode("utf-8")).hexdigest()


def _load_state(state_path: str) -> dict:
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_state(state_path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, state_path)


def _cache_path(cache_dir: str, key: str) -> str:
    return os.path.join(cache_dir, key + ".log")


def _read_cache(cache_dir: str, key: str) -> str:
    try:
        with open(_cache_path(cache_dir, key), "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _write_cache(cache_dir: str, key: str, text: str) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    p = _cache_path(cache_dir, key)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, p)


def _merge(cache: str, delta: str) -> str:
    if not cache:
        return delta
    if not delta:
        return cache
    sep = "" if cache.endswith("\n") else "\n"
    return cache + sep + delta


def _trim(text: str, keep: int):
    """超 keep 字节时按行边界截头 -> (text, trimmed)。"""
    if keep <= 0 or len(text.encode("utf-8")) <= keep:
        return text, False
    cut = len(text) - keep
    idx = text.find("\n", max(0, cut))
    return (text[idx + 1:] if idx >= 0 else text[-keep:]), True


# ---------------------------------------------------------------- 拉取主函数

def pull_text(path: str, *, filters: "list | None" = None, timeout: float = 300.0,
              max_bytes: "int | None" = None, incremental: bool = True,
              state_path: "str | None" = None, cache_dir: "str | None" = None,
              keep_bytes: "int | None" = None,
              transport=None, label: "str | None" = None) -> str:
    """拉取远端文本（gzip + 增量 + 双上限；超限 fail-closed 抛 LogPullError）。

    返回：合并后的缓存全文（首拉=全量过滤结果；后续=缓存+新增尾巴）。
    ``incremental=False`` 时返回本次增量本身，不读写状态/缓存（一次性用途）。
    """
    state_path = state_path or os.environ.get("MAAORCH_LOGPULL_STATE") or DEFAULT_STATE
    cache_dir = cache_dir or os.environ.get("MAAORCH_LOGPULL_CACHE") or DEFAULT_CACHE
    max_bytes = DEF_MAX_BYTES if max_bytes is None else int(max_bytes)
    keep_bytes = DEF_KEEP_BYTES if keep_bytes is None else int(keep_bytes)
    transport = transport or SshTransport()
    key = _key_of(path, filters)

    state = _load_state(state_path) if incremental else {}
    ent = state.get(key) or {}
    offset = int(ent.get("offset") or 0) if incremental else 0
    cache = _read_cache(cache_dir, key) if incremental else ""
    if incremental and offset > 0 and not cache:
        offset = 0  # 缓存丢了 → 状态不可信，重全量（防静默丢头）
    if not incremental:
        cache = ""

    job = {"path": path, "offset": offset, "filters": filters, "max_bytes": max_bytes}
    t0 = time.time()
    raw = transport.run(job, timeout)
    out = parse_out(raw)
    meta, payload_b64 = out["meta"], out["payload_b64"]
    st = meta.get("status")

    if st == "missing":
        return cache  # 与旧语义一致：文件不存在 -> 缓存（无则空）
    if st == "empty":
        if incremental:
            ent.update({"offset": int(meta.get("size") or offset), "ts": time.time(),
                        "path": path, "last": "empty"})
            state[key] = ent
            _save_state(state_path, state)
        return cache
    if st != "ok":
        raise LogPullError("bad_output", "未知 status=%r" % st)

    if max_bytes and payload_b64 and len(payload_b64) > max_bytes:
        raise LogPullError("too_large", "载荷超字节上限（%d > %d）：%s" % (
            len(payload_b64), max_bytes, label or path))
    delta = _decode_payload(payload_b64)
    size = int(meta.get("size") or 0)
    reset = meta.get("reset") == "1"
    new_off = size if size else offset + int(meta.get("sent") or 0)

    if not incremental:
        return delta

    if reset:
        merged = delta
    else:
        merged = _merge(cache, delta)
    merged, trimmed = _trim(merged, keep_bytes)
    _write_cache(cache_dir, key, merged)
    ent.update({"offset": new_off, "size": size, "ts": time.time(), "path": path,
                "elapsed": round(time.time() - t0, 2), "sent": int(meta.get("sent") or 0),
                "trimmed": bool(trimmed)})
    state[key] = ent
    _save_state(state_path, state)
    return merged


# ---------------------------------------------------------------- selftest

def _selftest_cases():
    """-> (name, fn)：fn() -> None（失败抛 AssertionError）。"""
    cases = []

    def _fixture(root):
        small = os.path.join(root, "small.log")
        with open(small, "w", encoding="utf-8", newline="") as f:
            for i in range(200):
                f.write("[2026-09-28 %02d:%02d:%02d.000][INF][AsstProxy] <2> Start Task Chain: StartUp %d\n"
                        % (5 + i // 60, i % 60, i % 60, i))
                if i % 7 == 0:
                    f.write("[2026-09-28 06:%02d:00.000][INF][TaskQueueViewModel] <2> 账号切换 -->> 134****5276\n" % (i % 60))
        return small

    def _root():
        import tempfile
        d = tempfile.mkdtemp(prefix="logpull_st_")
        return d

    # 1) 小文件：Python 模型拉取（过滤 + gzip 往返）
    def c1():
        d = _root()
        try:
            p = _fixture(d)
            logical = os.path.join(d, "empty_state.json")
            txt = pull_text(p, filters=["Task Chain", "->>"], timeout=30, incremental=False,
                            transport=PyModelTransport())
            n_sw = txt.count("账号切换")
            assert n_sw == 29, "过滤命中数不符：%d" % n_sw
        finally:
            shutil.rmtree(d, ignore_errors=True)
    cases.append(("python-model 过滤拉取", c1))

    # 2) 增量：第二次只取新增（offset 生效）
    def c2():
        d = _root()
        try:
            p = _fixture(d)
            sp = os.path.join(d, "state.json")
            cd = os.path.join(d, "cache")
            t1 = pull_text(p, filters=None, timeout=30, state_path=sp, cache_dir=cd,
                           transport=PyModelTransport())
            s1 = os.path.getsize(p)
            with open(p, "a", encoding="utf-8", newline="") as f:
                f.write("NEWLINE-A\n")
            jobs = []

            class Rec:
                def run(self, job, timeout):
                    jobs.append(dict(job))
                    return PyModelTransport().run(job, timeout)

            t2 = pull_text(p, filters=None, timeout=30, state_path=sp, cache_dir=cd,
                           transport=Rec())
            assert jobs[0]["offset"] == s1, "第二次 offset=%r 应为 %r" % (jobs[0]["offset"], s1)
            assert t2 == t1 + "NEWLINE-A\n", "增量合并不符（len %d vs %d）" % (len(t2), len(t1) + 10)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    cases.append(("增量：第二次只拉新增（offset）", c2))

    # 3) 超限：fail-closed + 快（结构性：远端只回 META 不回载荷）
    def c3():
        d = _root()
        try:
            p = os.path.join(d, "big.log")
            with open(p, "w", encoding="utf-8") as f:
                f.write("x" * (1 << 20) + "\n")
            t0 = time.time()
            try:
                pull_text(p, filters=None, timeout=30, max_bytes=4096, incremental=False,
                          transport=PyModelTransport())
                raise AssertionError("超限未抛 LogPullError")
            except LogPullError as e:
                assert e.kind == "too_large", "kind=%r" % e.kind
            dt = time.time() - t0
            assert dt < 5, "超限未快速返回：%.2fs" % dt
        finally:
            shutil.rmtree(d, ignore_errors=True)
    cases.append(("超限：快速 fail-closed（too_large）", c3))

    # 4) 秒上限：真实 kill 路径（PS 启动 >50ms 必超）-> timeout，且快速返回
    def c4():
        tr = LocalPsTransport()
        if not tr.available():
            print("      SKIP localps 不可用")
            return
        d = _root()
        try:
            p = _fixture(d)
            t0 = time.time()
            try:
                pull_text(p, timeout=0.05, incremental=False, transport=tr)
                raise AssertionError("超时未抛 LogPullError")
            except LogPullError as e:
                assert e.kind == "timeout", "kind=%r (%s)" % (e.kind, e)
            dt = time.time() - t0
            assert dt < 3, "超时回得太慢：%.2fs" % dt
        finally:
            shutil.rmtree(d, ignore_errors=True)
    cases.append(("秒上限：真实 kill 快速返回（timeout）", c4))

    # 5) 截断重置：offset > size -> reset=1，缓存重建
    def c5():
        d = _root()
        try:
            p = _fixture(d)
            sp = os.path.join(d, "state.json")
            cd = os.path.join(d, "cache")
            pull_text(p, filters=None, timeout=30, state_path=sp, cache_dir=cd,
                      transport=PyModelTransport())
            with open(p, "w", encoding="utf-8", newline="") as f:   # 截断重写
                f.write("FULL-NEW-CONTENT\n")
            t2 = pull_text(p, filters=None, timeout=30, state_path=sp, cache_dir=cd,
                           transport=PyModelTransport())
            assert t2 == "FULL-NEW-CONTENT\n", "截断重置结果：%r" % t2[:60]
        finally:
            shutil.rmtree(d, ignore_errors=True)
    cases.append(("截断：reset 重建缓存", c5))

    # 6) 本地 PS 差分：同一 job，LocalPsTransport 与 PyModel 输出逐字节一致
    def c6():
        d = _root()
        try:
            tr = LocalPsTransport()
            if not tr.available():
                print("      SKIP localps 不可用")
                return
            p = _fixture(d)
            jobs = [
                {"path": p, "offset": 0, "filters": ["Task Chain", "->>"], "max_bytes": 0},
                {"path": p, "offset": 4096, "filters": [], "max_bytes": 0},
                {"path": p, "offset": 0, "filters": [], "max_bytes": 512},
                {"path": p, "offset": 0, "filters": ["Task Chain"], "max_bytes": 64},
            ]
            def _probe(transport, job):
                try:
                    o = parse_out(transport.run(dict(job), 60))
                    return o["meta"]["status"], o
                except LogPullError as e:
                    return ("raise:" + e.kind), None

            for j in jobs:
                sa, oa = _probe(tr, j)
                sb, ob = _probe(PyModelTransport(), j)
                assert sa == sb, "status 差分：%s vs %s（job=%r）" % (sa, sb, j)
                if sa == "ok":
                    da = _decode_payload(oa["payload_b64"])
                    db = _decode_payload(ob["payload_b64"])
                    assert da == db, "载荷差分（offset=%s filters=%s max_bytes=%s）" % (
                        j["offset"], j["filters"], j["max_bytes"])
                print("      差分 OK：offset=%s filters=%s max_bytes=%s -> %s" % (
                    j["offset"], j["filters"], j["max_bytes"], sa))
        finally:
            shutil.rmtree(d, ignore_errors=True)
    cases.append(("本地 PS 与 Python 模型差分（真实 gzip/offset/上限）", c6))

    # 7) 状态文件/缓存路径必须落在 runtime/（约定）
    def c7():
        assert os.path.abspath(DEFAULT_STATE).startswith(os.path.abspath(RUNTIME)), DEFAULT_STATE
        assert os.path.abspath(DEFAULT_CACHE).startswith(os.path.abspath(RUNTIME)), DEFAULT_CACHE
    cases.append(("状态/缓存路径在 runtime/", c7))

    return cases


def run_selftest() -> int:
    fails = 0
    for name, fn in _selftest_cases():
        try:
            fn()
            print("PASS", name)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL", name, "->", e)
    print("LOGPULL_SELFTEST=%s" % ("PASS" if not fails else "FAIL(%d)" % fails))
    return 0 if not fails else 1


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="日志拉取层（gzip/增量/双上限；只读）")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--pull", metavar="REMOTE_PATH")
    ap.add_argument("--filters", help="逗号分隔行过滤（超集安全）")
    ap.add_argument("--max-bytes", type=int)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--out")
    ap.add_argument("--no-incremental", action="store_true")
    ap.add_argument("--localps", action="store_true", help="用本机 PowerShell 跑同一远端脚本（离线）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return run_selftest()
    if not a.pull:
        ap.print_help()
        return 1

    filters = [x.strip() for x in (a.filters or "").split(",") if x.strip()] or None
    transport = LocalPsTransport() if a.localps else SshTransport()
    t0 = time.time()
    try:
        txt = pull_text(a.pull, filters=filters, timeout=a.timeout, max_bytes=a.max_bytes,
                        incremental=not a.no_incremental, transport=transport)
    except LogPullError as e:
        print("LOGPULL=UNKNOWN:%s" % e)
        return 2
    dt = time.time() - t0
    info = {"path": a.pull, "bytes": len(txt.encode("utf-8")), "elapsed": round(dt, 2),
            "transport": transport.describe(), "incremental": not a.no_incremental}
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(txt)
        info["out"] = a.out
    if a.json:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print("拉取 %.2fs %d B（%s）" % (dt, info["bytes"], transport.describe()))
    print("LOGPULL=OK")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
