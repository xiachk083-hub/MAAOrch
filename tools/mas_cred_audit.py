#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mas_cred_audit.py — 凭据暴露面只读审计扫描器（本地/远端同源，纯标准库）。

扫什么（文本/日志/文档/源码/转录/sqlite）：
  ① 未脱敏 11 位手机号，以及"掩码不彻底"的灰区（可见位数过多）
  ② Password / SklandToken / ServerChanKey / cookie 等敏感字段的字面量取值
  ③ 长 base64/hex/JWT/已知前缀 形状的疑似 token（含 DPAPI 密文）

输出：JSONL，每条 = 文件 / 行号 / 类型 / 长度 / 可见位数 / 建议分级 / 是否可逆。
预览一律脱敏（只留长度与形状；DPAPI/JWT 只保留公开指纹前缀），
不写明文、不写可对照的摘要（同值分组用进程内随机盐 HMAC，盐不落盘）。

用途：docs/CREDENTIAL_EXPOSURE_AUDIT.md 的取证引擎；也可接巡检/CI（--fail-on）。

用法
  python tools/mas_cred_audit.py --root "D:/1-2/MAAOrch/docs=doc" --root "D:/1-2/MAAOrch/tmp=tmp" \
      --jsonl out.jsonl --summary out.tsv
  python tools/mas_cred_audit.py --root "$HOME/.dsh/sessions=trace" --max-bytes 268435456 ...
  python tools/mas_cred_audit.py --selftest

zstd 转写（.zstd/.zst，含多帧拼接）会自动解压后扫描（--zstd off 关闭）；
需要 python-zstandard（或 Python 3.14+ 的 compression.zstd），无后端时记入 skipped。

role（--root 的 = 后缀）决定"建议分级"：
  cfg=原始配置(必须存在，受控) → a     doc/log/db/trace/src=文档/日志/看板/转录/仓库源码 → b
  tmp=临时与运行产物（灰区）    → c     其它 → c

只读：不修改任何被扫文件；sqlite 会先复制到 --work 再读（避免锁与 WAL 影响）。
退出码：0 正常；2 = --fail-on 命中（供 CI/巡检接线）。
"""
import argparse
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import sys
import time

VERSION = "1.1.2"

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "site-packages",
             "dist", "build", ".mypy_cache", ".pytest_cache", ".idea", "_trash",
             ".opencode", "egg-info", ".gradle", ".next", ".cache"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg", ".npy", ".npz",
            ".pt", ".pth", ".onnx", ".tflite", ".pb", ".binarypb", ".dll", ".pyd", ".exe",
            ".so", ".zip", ".7z", ".rar", ".gz", ".xz", ".zst", ".mp4", ".mp3", ".wav",
            ".ttf", ".otf", ".woff", ".woff2", ".pyc", ".class", ".jar", ".pdf", ".xlsx",
            ".docx", ".pptx", ".map", ".bin", ".model", ".pk", ".pkl", ".h5", ".npy.bak"}

RE_PHONE_RUN = re.compile(r"(?<![0-9A-Za-z*])1[3-9][0-9*]{4,12}(?![0-9A-Za-z*])")
RE_PHONE_SPACED_FULL = re.compile(
    r"(?<![0-9A-Za-z*])(1[3-9]\d)[\s\-·.]{1,2}(\d{4})[\s\-·.]{1,2}(\d{4})(?![0-9A-Za-z*])")
RE_ANSI = re.compile(r"(?:\x1b|\\u001b)\[[0-9;?]*[A-Za-z]")
RE_PHONE_SPACED = re.compile(
    r"(?<![0-9A-Za-z*])1[3-9]\d[\s\-–—·.]{0,3}\*{2,}[\s\-–—·.]{0,3}\d{3,4}(?![0-9A-Za-z*])")
RE_KEY_NAMED = re.compile(
    r"(?i)(?P<key>\"?(?:password|passwd|passcode|pwd|skland_?token|skland_?account|"
    r"serverchan_?key|send_?key|sct_?key|push_?key|api_?key|apikey|access_?token|refresh_?token|"
    r"auth_?token|client_?secret|app_?secret|secret_?key|private_?key|ssh_?key|cookie|authorization)"
    r"\"?\s*[:=]\s*)(?P<val>\"?[^\s\"',;}\]]{1,600}+)")
RE_KEY_GENERIC = re.compile(r"(?i)(?P<key>\"?(?:token|secret|key|pass)\"?\s*[:=]\s*)"
                            r"(?P<val>\"?[^\s\"',;}\]]{1,600}+)")
RE_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{4,}")
RE_BEARER = re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._\-]{20,})")
RE_PEM = re.compile(r"-----BEGIN [A-Z ]{0,30}PRIVATE KEY-----")
RE_PREFIXED = re.compile(r"\b(?:sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
                         r"|xox[bpoas]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{30,}"
                         r"|npm_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_\-]{16,})\b")
RE_DPAPI = re.compile(r"AQAAANCM[A-Za-z0-9+/=]{40,}")
RE_BLOB = re.compile(r"(?<![A-Za-z0-9+/=_-])([A-Za-z0-9+/=_-]{28,})(?![A-Za-z0-9+/=_-])")
RE_COOKIE = re.compile(r"(?i)\b(SESSDATA|bili_jct|DedeUserID|buvid3|cf_clearance)\s*=\s*([^\s;&\"']{8,})")

HIGH_KINDS = {"phone_full", "phone_full_spaced", "phone_mask_leaky", "key_literal", "dpapi_blob",
              "tokenish_hi", "jwt", "bearer", "pem", "prefixed_key", "cookie_val", "b64_long"}

REVERSIBILITY = {
    "phone_full": "明文直读",
    "phone_full_spaced": "明文直读（空格/分隔符拆开）",
    "phone_mask_leaky": "可见位数等价明文（可还原）",
    "phone_mask_partial": "可见位数偏多（结合他处可还原）",
    "phone_mask_std": "仅部分可见（不可完整还原）",
    "key_literal": "明文直读（该字段本应加密）",
    "dpapi_blob": "DPAPI 密文：本机同用户可逆，跨机/跨用户不可逆",
    "tokenish_hi": "高熵串，直读即可用（若是 token）",
    "tokenish_mid": "中熵串，需人工判读",
    "b64_long": "base64 密文/串，需人工判读",
    "hex_digest": "十六进制摘要，通常不可逆",
    "jwt": "明文直读（自包含 JWT）",
    "bearer": "明文直读",
    "pem": "私钥明文直读",
    "prefixed_key": "已知前缀的 API key，明文直读",
    "cookie_val": "cookie 值明文直读",
    "phoneish": "低信号（11 位数字但不像手机号）",
}

PLACEHOLDER_RE = re.compile(
    r"(?i)^(\*+|x+|y+|z+|0+|1+|9+|<.*>|\$\{.*\}|%[sd]|none|null|true|false|changeme|"
    r"your[_-]?.*|test|example|placeholder|redacted|masked|todo|n/?a|undefined)$")
CODE_HINTS = ("(", ")", "[", "]", "{", "}", "${", "<", ">", "?", "+", "%", "&", "|", "^", "!",
              "\\", "request", "self", "None", "null", "true", "false", "os.", "str(", "dict(",
              "len(", "get(", "args", "kwargs", "bool", "int(", "float(")

MAX_PER_FILE_DEFAULT = 200

# 长行（工具结果/巨型 JSON）上的命名字段扫描：先用便宜的子串预筛，避免在 100k+ 字符行上
# 全量跑 alternation（实测 13MB 长行 ≈ 8s，占总扫描时间大头）；超巨行只做定向关键词。
LONG_LINE = 4096
HUGE_LINE = 32768
KEY_NAMED_HINTS = ("passw", "pwd", "key", "token", "secret", "cookie", "authoriz", "skland")
KEY_GENERIC_HINTS = ("token", "secret", "key", "pass")


def is_zstd_name(path):
    n = os.path.basename(path).lower()
    return n.endswith(".zstd") or n.endswith(".zst") or ".zstd." in n or ".zst." in n


def zstd_read(path):
    """读 zstd（含 DSH 会话那种多帧拼接）→ bytes。

    优先 python-zstandard（stream_reader 跨帧读）；退化到 Python 3.14 compression.zstd。
    无后端抛 RuntimeError；尾巴撕裂（会话写作中途）尽力保留已解出的部分。
    """
    try:
        import zstandard  # type: ignore
    except ImportError:
        zstandard = None
    if zstandard is not None:
        dctx = zstandard.ZstdDecompressor()
        try:
            with open(path, "rb") as fh:
                with dctx.stream_reader(fh) as r:
                    return r.read()
        except zstandard.ZstdError:
            out = []
            obj = zstandard.ZstdDecompressor().decompressobj()
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    try:
                        out.append(obj.decompress(chunk))
                    except zstandard.ZstdError:
                        break
            return b"".join(out)
    try:
        from compression import zstd as cz  # type: ignore  # Python 3.14+
        with open(path, "rb") as fh:
            return cz.decompress(fh.read())
    except ImportError:
        raise RuntimeError("no zstd backend (pip install zstandard, or Python 3.14+)")


def zstd_available():
    try:
        import zstandard  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        from compression import zstd  # noqa: F401
        return True
    except ImportError:
        return False


def shannon(s):
    if not s:
        return 0.0
    freq = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def char_classes(s):
    cls = set()
    for ch in s:
        if ch.isdigit():
            cls.add("d")
        elif ch.isupper():
            cls.add("U")
        elif ch.islower():
            cls.add("l")
        else:
            cls.add("s")
    return cls


def classify_value(val):
    """敏感字段取值 → (kind, note)；None = 忽略。"""
    v = val.strip().strip("\"'")
    if not v or PLACEHOLDER_RE.match(v):
        return None
    if RE_DPAPI.match(v):
        return ("dpapi_blob", "dpapi")
    if v.startswith("AQAAANCM"):
        return ("dpapi_blob", "dpapi-prefix")
    if any(h in v for h in CODE_HINTS):
        return None
    if len(v) < 8:
        return None
    if RE_JWT.match(v):
        return ("jwt", "")
    if v.isdigit() and len(v) == 11 and v[0] == "1" and v[1] in "3456789":
        return ("key_literal", "phone-as-field")
    b64ish = bool(re.fullmatch(r"[A-Za-z0-9+/=_-]{20,}", v))
    if b64ish:
        cls = char_classes(v)
        if len(cls) >= 2:
            return ("b64_long", "len=%d" % len(v))
    return ("key_literal", "len=%d" % len(v))


def classify_blob(s):
    """长串 → token 形状判定；None = 不算。"""
    if len(s) < 30:
        return None
    cls = char_classes(s)
    if cls <= {"d"}:
        return None
    if re.fullmatch(r"[0-9a-fA-F]+", s):
        return ("hex_digest", "hex%d" % len(s))
    if "/" in s and re.search(r"[a-z]{5,}", s):
        return None  # 路径/URL 形状
    if "-" in s and re.search(r"[a-z]{4,}-[a-z]{4,}", s):
        return None  # 连字短语形状
    h = shannon(s)
    if len(cls) >= 3 and h >= 3.9:
        return ("tokenish_hi", "H=%.2f" % h)
    if len(cls) >= 2 and h >= 3.2:
        return ("tokenish_mid", "H=%.2f" % h)
    return None


def preview_of(kind, raw):
    """绝不含明文：只给形状/公开指纹。"""
    if kind.startswith("phone"):
        return ""
    if kind == "dpapi_blob":
        return "AQAAANCM…"
    if kind == "jwt":
        return "eyJ…"
    return ""


def suggest_class(role, kind):
    if role == "cfg":
        return "a"
    if kind == "hex_digest":
        return "c"
    if role in ("doc", "log", "db", "trace", "src"):
        return "b"
    return "c"


class Scanner:
    def __init__(self, args):
        self.args = args
        self.salt = secrets.token_bytes(32)
        self.hits = []
        self.files_scanned = 0
        self.files_skipped = []
        self.errors = []

    # ---------- 核心：一行文本 ----------
    def scan_text(self, text, path, role):
        found = []
        for lineno, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            found.extend(self.scan_line(line, path, role, lineno))
        return found

    def scan_line(self, line, path, role, lineno):
        out = []
        seen = set()  # (kind, 归一化取值)：同一行同一值被多条规则命中只计一次
        line = RE_ANSI.sub("", line)  # 板/终端日志里 ESC[2;3m 之类紧贴数字会挡边界守卫

        def add(kind, raw, extra=None):
            norm = raw.strip().strip("\"'")
            if (kind, norm) in seen:
                return
            seen.add((kind, norm))
            rec = {
                "file": path, "line": lineno, "kind": kind, "role": role,
                "cls": suggest_class(role, kind), "rev": REVERSIBILITY.get(kind, ""),
                "len": len(raw), "preview": preview_of(kind, raw),
            }
            if extra:
                rec.update(extra)
            out.append(rec)
            if kind != "hex_digest":
                rec["_dg"] = hmac.new(self.salt, raw.encode("utf-8", "replace"),
                                      hashlib.blake2b).digest()[:8]

        # ① 手机号（连续 run：明文或掩码）
        run_spans = []
        for m in RE_PHONE_RUN.finditer(line):
            run = m.group(0)
            run_spans.append(m.span())
            stars = run.count("*")
            digits = len(run) - stars
            if stars == 0:
                if len(run) == 11 and run[1] in "3456789":
                    add("phone_full", run)
                elif len(run) == 11:
                    add("phoneish", run)
                continue
            # 标准掩码 = 前3后4（可见 7 位）；超出即灰区
            if digits >= 10:
                add("phone_mask_leaky", run, {"digits": digits, "stars": stars})
            elif digits >= 8:
                add("phone_mask_partial", run, {"digits": digits, "stars": stars})
            else:
                add("phone_mask_std", run, {"digits": digits, "stars": stars})
        # ①a 空格/点/短横线分隔的完整号码（135 1234 5678）
        for m in RE_PHONE_SPACED_FULL.finditer(line):
            add("phone_full_spaced", m.group(0))
        # ①b 带分隔符的掩码（192 **** 7973）——与上面重叠的不重复计
        for m in RE_PHONE_SPACED.finditer(line):
            if any(m.start() >= s and m.end() <= e for s, e in run_spans):
                continue
            run = m.group(0)
            digits = sum(c.isdigit() for c in run)
            add("phone_mask_partial" if digits >= 8 else "phone_mask_std", run,
                {"digits": digits, "stars": run.count("*")})

        # ② 命名敏感字段（长行先做子串预筛；超巨行丢弃低信号的泛型匹配）
        do_named = True
        do_generic = True
        if len(line) > LONG_LINE:
            low = line.lower()
            do_named = any(h in low for h in KEY_NAMED_HINTS)
            do_generic = any(h in low for h in KEY_GENERIC_HINTS)
            if len(line) > HUGE_LINE:
                do_generic = False
        for rex, enabled in ((RE_KEY_NAMED, do_named), (RE_KEY_GENERIC, do_generic)):
            if not enabled:
                continue
            for m in rex.finditer(line):
                key = m.group("key")
                raw_val = m.group("val")
                if rex is RE_KEY_GENERIC and len(raw_val.strip("\"'")) < 24:
                    continue
                res = classify_value(raw_val)
                if not res:
                    continue
                kind, note = res
                add(kind, raw_val, {"ctx": key.strip().rstrip(":=").strip("\" ")[:40], "note": note})

        # ③ DPAPI / JWT / bearer / PEM / 已知前缀 / cookie
        for m in RE_DPAPI.finditer(line):
            add("dpapi_blob", m.group(0))
        for m in RE_JWT.finditer(line):
            add("jwt", m.group(0))
        for m in RE_BEARER.finditer(line):
            add("bearer", m.group(1))
        if RE_PEM.search(line):
            add("pem", RE_PEM.search(line).group(0))
        for m in RE_PREFIXED.finditer(line):
            add("prefixed_key", m.group(0))
        for m in RE_COOKIE.finditer(line):
            add("cookie_val", m.group(2), {"ctx": m.group(1)})

        # ④ 长串形状
        for m in RE_BLOB.finditer(line):
            blob = m.group(1)
            res = classify_blob(blob)
            if not res:
                continue
            kind, note = res
            add(kind, blob, {"note": note})
        return out

    # ---------- 文件级 ----------
    def scan_file(self, path, role):
        ext = os.path.splitext(path)[1].lower()
        if ext in SKIP_EXT:
            return []
        if self.args.zstd != "off" and is_zstd_name(path):
            return self.scan_zstd(path, role)
        try:
            size = os.path.getsize(path)
        except OSError as e:
            self.errors.append("%s: %s" % (path, e))
            return []
        if size > self.args.max_bytes:
            self.files_skipped.append((path, size, "too-large"))
            return []
        if size == 0:
            return []
        try:
            with open(path, "rb") as fh:
                head = fh.read(4096)
                if b"\x00" in head and ext not in (".db", ".sqlite", ".sqlite3", ".dat"):
                    self.files_skipped.append((path, size, "binary"))
                    return []
                fh.seek(0)
                data = fh.read()
        except OSError as e:
            self.errors.append("%s: %s" % (path, e))
            return []
        if data[:16] == b"SQLite format 3\x00":
            return self.scan_sqlite_bytes(path, role)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        self.files_scanned += 1
        got = self.scan_text(text, path, role)
        return self.cap(path, got)

    def scan_zstd(self, path, role):
        """zstd 转写（.zstd/.zst，含多帧拼接）→ 解压后按文本扫；命中记录仍指向原文件。"""
        if not zstd_available():
            self.files_skipped.append((path, os.path.getsize(path), "zstd-no-backend"))
            return []
        try:
            data = zstd_read(path)
        except Exception as e:
            self.errors.append("%s: zstd %s: %s" % (path, type(e).__name__, e))
            return []
        if len(data) > self.args.max_bytes:
            self.files_skipped.append((path, len(data), "too-large(decompressed)"))
            return []
        text = data.decode("utf-8", "replace")
        self.files_scanned += 1
        return self.cap(path, self.scan_text(text, path, role))

    def cap(self, path, hits):
        if len(hits) > self.args.max_per_file:
            self.files_skipped.append((path, len(hits), "hit-cap"))
            return hits[: self.args.max_per_file]
        return hits

    # ---------- sqlite ----------
    def scan_sqlite_bytes(self, path, role):
        dbfile = path
        if self.args.work:
            os.makedirs(self.args.work, exist_ok=True)
            tag = hashlib.sha1(path.encode("utf-8", "replace")).hexdigest()[:12]
            dst = os.path.join(self.args.work, tag + os.path.basename(path))
            try:
                shutil.copyfile(path, dst)
                for sib in ("-wal", "-shm"):
                    if os.path.exists(path + sib):
                        shutil.copyfile(path + sib, dst + sib)
                dbfile = dst
            except OSError as e:
                self.errors.append("sqlite copy %s: %s" % (path, e))
        hits = []
        try:
            con = sqlite3.connect(dbfile)
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            tables = [r[0] for r in con.execute(
                "select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
            # FTS 影子表（messages_fts*）内容与源列重复，跳过以免重复计数与拖慢
            tables = [t for t in tables if "_fts" not in t.lower()]
            for t in tables:
                try:
                    rows = con.execute('select * from "%s"' % t.replace('"', '""'))
                except sqlite3.Error as e:
                    self.errors.append("sqlite %s.%s: %s" % (path, t, e))
                    continue
                cols = [d[0] for d in rows.description]
                for rid, row in enumerate(rows):
                    for ci, cell in enumerate(row):
                        if isinstance(cell, bytes) or cell is None:
                            continue
                        s = cell if isinstance(cell, str) else str(cell)
                        if len(s) < 8:
                            continue
                        if len(s) > 4_000_000:
                            s = s[:4_000_000]
                        got = self.scan_text(s, "%s#%s[%s]" % (path, t, cols[ci]), role)
                        for h in got:
                            h["row"] = rid
                        hits.extend(got)
            con.close()
        except sqlite3.Error as e:
            self.errors.append("sqlite %s: %s" % (path, e))
        self.files_scanned += 1
        return self.cap(path, hits)

    # ---------- 目录 ----------
    def scan_root(self, root, role):
        if os.path.isfile(root):
            return self.scan_file(root, role)
        out = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn in self.args.exclude_name:
                    continue
                p = os.path.join(dirpath, fn)
                out.extend(self.scan_file(p, role))
        return out

    def run(self):
        for root, role in self.args.root:
            root_abs = os.path.abspath(root)
            if not os.path.exists(root_abs):
                self.errors.append("MISSING ROOT: %s" % root_abs)
                continue
            t0 = time.time()
            got = self.scan_root(root_abs, role)
            self.hits.extend(got)
            sys.stderr.write("[root] %s (%s): %d hits in %.1fs\n"
                             % (root_abs, role, len(got), time.time() - t0))
        # 同值分组：只统计"同一凭据出现在几个文件"，digest 只在内存里存活；
        # 这里统一 pop（_dg 是 bytes，绝不能进任何输出）
        groups = {}
        for h in self.hits:
            dig = h.pop("_dg", None)
            if dig is None:
                continue
            groups.setdefault((h["kind"], dig), []).append(h)
        for (_kind, _dig), hs in groups.items():
            if len(hs) > 1:
                nfiles = len({h["file"].split("#")[0] for h in hs})
                if nfiles > 1:
                    for h in hs:
                        h["dup_files"] = nfiles
        return self.hits

    def write(self, path_jsonl, path_summary):
        if path_jsonl:
            with open(path_jsonl, "w", encoding="utf-8") as fh:
                for h in self.hits:
                    h.pop("_dg", None)  # 双保险：任何情况下 digest 都不落盘
                    fh.write(json.dumps(h, ensure_ascii=False) + "\n")
        if path_summary:
            agg = {}
            for h in self.hits:
                k = (h["kind"], h["cls"], h["file"].split("#")[0])
                a = agg.setdefault(k, {"n": 0, "lines": [], "cls": h["cls"], "role": h["role"]})
                a["n"] += 1
                if len(a["lines"]) < 6:
                    a["lines"].append(h["line"] if "line" in h else h.get("row"))
            with open(path_summary, "w", encoding="utf-8") as fh:
                fh.write("kind\tcls\trole\thits\tlines\tsamples\tfile\n")
                for (kind, cls, path), a in sorted(agg.items(), key=lambda x: (x[0][1], -x[1]["n"])):
                    fh.write("%s\t%s\t%s\t%d\t%s\t%s\t%s\n" % (
                        kind, cls, a["role"], a["n"],
                        ",".join(str(x) for x in a["lines"][:6]), "", path))

    def report(self):
        by_kind = {}
        for h in self.hits:
            by_kind[h["kind"]] = by_kind.get(h["kind"], 0) + 1
        return {
            "files_scanned": self.files_scanned,
            "hits": len(self.hits),
            "by_kind": by_kind,
            "skipped": len(self.files_skipped),
            "errors": self.errors[:20],
            "high_b": sum(1 for h in self.hits if h["cls"] == "b" and h["kind"] in HIGH_KINDS),
        }


def selftest():
    """合成样本自测：判定/分级/脱敏都要正确，且输出里不得出现明文。"""
    sc = Scanner(argparse.Namespace(max_bytes=10 ** 7, max_per_file=100, work=None,
                                    exclude_name=set(), zstd="off"))
    fake_phone = "1" + "3" + "5" + "12345678"
    masked_std = fake_phone[:3] + "*" * 4 + fake_phone[-4:]
    masked_leak = fake_phone[:8] + "*" * 2 + fake_phone[-2:]  # 掩码不彻底：可见 10 位
    dpapi = "AQAAANCM" + "ndm4D" * 30
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnop"
    samples = [
        ('{"phone": "%s"}' % fake_phone, "phone_full"),
        ('\x1b[2;3m%s' % fake_phone, "phone_full"),  # 板日志 ANSI 前缀不得挡边界守卫
        ('%s %s %s' % (fake_phone[:3], fake_phone[3:7], fake_phone[7:]), "phone_full_spaced"),
        ('账号切换 -->> %s' % masked_std, "phone_mask_std"),
        ('id=%s' % masked_leak, "phone_mask_leaky"),
        ('"Password": "%s"' % dpapi, "dpapi_blob"),
        ('"SklandToken": "AbCdEf1234567890XyZ9876543210QwEr"', "b64_long"),
        ('Authorization: Bearer %s' % jwt, "bearer"),
        ('mid="%s"' % jwt, "jwt"),
        ('{"ServerChanKey": "<redacted>"}', None),
        ('password = os.environ.get("PW")', None),
    ]
    ok = True
    for text, want in samples:
        got = sc.scan_line(text, "selftest.txt", "log", 1)
        kinds = [g["kind"] for g in got]
        hit = want in kinds if want else not kinds
        if not hit:
            ok = False
            print("FAIL want=%s got=%s :: %s" % (want, kinds, text[:40]))
    blobs = [g for g in sc.scan_line('x="%s"' % ("Zq" * 40 + "9" * 8), "s.txt", "log", 1)]
    for h in blobs:
        if h["preview"] and "AQAA" not in h["preview"]:
            ok = False
            print("FAIL preview leak: %s" % h)
    print("SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def parse_root(spec):
    if "=" in spec:
        p, role = spec.rsplit("=", 1)
        return p, role.strip().lower()
    return spec, "tmp"


def main(argv=None):
    ap = argparse.ArgumentParser(description="凭据暴露面只读审计扫描器")
    ap.add_argument("--root", action="append", default=[], help="路径[=role]（cfg|doc|log|db|trace|src|tmp）")
    ap.add_argument("--jsonl", help="命中明细输出（JSONL）")
    ap.add_argument("--summary", help="按文件聚合输出（TSV）")
    ap.add_argument("--work", default="", help="sqlite 复制工作目录（默认 %TEMP%）")
    ap.add_argument("--max-bytes", type=int, default=32 * 1024 * 1024)
    ap.add_argument("--max-per-file", type=int, default=MAX_PER_FILE_DEFAULT)
    ap.add_argument("--zstd", choices=["auto", "off"], default="auto",
                    help="zstd 转写（.zstd/.zst）解压后扫描；auto=有后端就解")
    ap.add_argument("--exclude-name", action="append", default=["mas_cred_audit.py"], help="按文件名跳过")
    ap.add_argument("--fail-on", choices=["none", "b"], default="none")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.work:
        args.work = os.path.join(os.environ.get("TEMP", "/tmp"), "mas_cred_audit_work")
    args.root = [parse_root(s) for s in args.root]
    args.exclude_name = set(args.exclude_name or [])
    sc = Scanner(args)
    sc.run()
    sc.write(args.jsonl, args.summary)
    rep = sc.report()
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if args.fail_on == "b" and rep["high_b"] > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
