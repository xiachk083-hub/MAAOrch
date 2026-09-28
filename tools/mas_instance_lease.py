#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""实例租约（instance lease）—— 跨任务 / 跨工具的「一台实例同一时刻只归一个持有者」轻量互斥。

kanban t_65b69753。

痛点：补跑卡、班次发车、实机测试同时想用同一台实例 ⇒ 互相抢、失败、「已在运行」、甚至打断别人。
本工具给「用实例」这件事加一层**协调清单**：谁要用，先 `acquire`；用完 `release`；
拿不到就**别发车**（NO-GO）——要么换实例、要么 `--wait` 等它空出来。

职责边界（不是发车器、不是发车闸门）：
  * 只写 `runtime/instance_leases.json`，不发车、不启停实例、不改 AUTO-MAS/MAA 任何配置；
  * 「能不能发」的裁决仍归 `tools/mas_preflight.py` / `tools/mas_ondemand.py`（本工具给它们喂一条事实：
    实例在租期内被谁占着）。接入建议见 `docs/MAS_INSTANCE_LEASE.md`。

文件与语义：
  * 账本 `runtime/instance_leases.json`，键 `<host>:<idx>`（见下「host 口径」）：
      {idx, host, owner, purpose, kind, acquired_at, renewed_at, expires_at, ttl_s, pid, src, note}
  * **过期自动失效**：任何时刻 `expires_at <= now` 的租约一律按「没有」算；下一次写账时它会被
    清进 `history`（保留最近 200 条，含 released/expired/stolen 三种结束原因）。
  * 锁 `runtime/instance_leases.lock`（`os.O_CREAT|O_EXCL`；临界区毫秒级；陈旧阈值 30s 可抢，
    可用 `MAAORCH_LEASE_LOCK_STALE_S` 覆盖）——保证「读-判-写」原子：两个进程同时抢同一实例，
    只有一个拿到（自测里有真·多进程互抢用例）。

host 口径（**必须统一，否则互相看不见**）：
  * 默认 = 环境变量 `MAAORCH_LEASE_HOST`；没有则取 `MAAORCH_SSH_HOST` 去掉 `user@`
    （本 rig 实测 = `100.79.173.69`，即 MRFZ-0000 机队）；
  * 本机（DESKTOP-L1OEMR3）实例用 `--host local`（肉鸽 #0 / 设备 V）。
  * idx 语义 = MuMu 实例号（档案见 `docs/MAS_EMULATORS.md`）；本工具**不校验 idx 是否存在**
    （台账不管实例死活；映射用 `resolve` 现查）。

用法：
  python tools/mas_instance_lease.py list                       # 看在租实例
  python tools/mas_instance_lease.py list --all                 # 加过期残留 + 最近历史
  python tools/mas_instance_lease.py check --idx 13             # 空着吗？（rc 0 空 / rc 1 被占）
  python tools/mas_instance_lease.py check --idx 13 --owner t_x # 自己被占也算空（rc 0）
  python tools/mas_instance_lease.py acquire --idx 13 --owner t_abc --purpose "补跑 官-1-1-7" --ttl 1800
  python tools/mas_instance_lease.py acquire --idx 13,14 --owner t_abc --purpose "..." --wait 120
  python tools/mas_instance_lease.py acquire --script 明日方舟-官-6 --owner t_abc --purpose "实机测试"
  python tools/mas_instance_lease.py renew  --idx 13 --owner t_abc [--ttl 1800]
  python tools/mas_instance_lease.py release --idx 13 [--owner t_abc]
  python tools/mas_instance_lease.py release --owner t_abc      # 放掉该持有者的全部租约
  python tools/mas_instance_lease.py resolve --script 明日方舟-官-6 [--with-procs]
  python tools/mas_instance_lease.py resolve --queue 晚班定时1 [--with-procs]
  python tools/mas_instance_lease.py prune
  python tools/mas_instance_lease.py selftest [--race 4]
  `--check --idx N` 等价 `check --idx N`；`--list` / `--selftest` 同理（兼容写法）。

退出码：0=完成（acquire/renew/release 成功、check 空着、list/resolve 正常）；
        1=被占/被拒（acquire 撞别人、check 被别人占、release 持有者不符）；
        2=用法错误 / 解析不到（REST 或脚本名解析失败一律 fail-closed）。

末尾行（grep 用）：`LEASE=acquired|renewed|stolen|busy|free|self|released|expired ...`；
`--json` 时输出单行 JSON（含同样的字段，不打印 LEASE= 行）。

环境变量：
  MAAORCH_LEASE_HOST          host 标签（默认见上）
  MAAORCH_LEASE_RUNTIME       覆盖 runtime 目录（测试用；等价 --runtime）
  MAAORCH_LEASE_LOCK_STALE_S  陈旧锁阈值（默认 30s）
  MAAORCH_SSH_HOST / MAAORCH_SSH_KEY   resolve --with-procs 用（默认 Administrator@100.79.173.69）
  HERMES_KANBAN_TASK           acquire 未给 --owner 时的兜底（看板 worker 的卡 id）

给其它工具的 API（同目录 import mas_instance_lease as L）：
  L.set_runtime(None); L.acquire(host, idx, owner, purpose, ttl_s) -> dict
  L.renew(...) / L.release(...) / L.check(host, idx, owner=None) -> dict
  L.hold(host, idx, owner, purpose, ttl_s) —— 上下文管理器（同步任务最顺手；异常也保证释放）
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
if TOOLS not in sys.path:                      # 复用 mas_preflight 的现查映射
    sys.path.insert(0, TOOLS)

TS_FMT = "%Y-%m-%d %H:%M:%S"
FILE_NAME = "instance_leases.json"
LOCK_NAME = "instance_leases.lock"
DEFAULT_TTL_S = 1800                           # 卡面口径：默认 30 分钟
MIN_TTL_S = 1
MAX_TTL_S = 86400
LOCK_STALE_S = float(os.environ.get("MAAORCH_LEASE_LOCK_STALE_S", 30))
WAIT_POLL_S = 2.0
KEEP_HISTORY = 200

_PATHS = {"file": "", "lock": ""}


# ---------------------------------------------------------------- 时间 / 小工具
def now_dt() -> dt.datetime:
    return dt.datetime.now()


def now_str(t: dt.datetime | None = None) -> str:
    return (t or now_dt()).strftime(TS_FMT)


def parse_ts(v) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(str(v), TS_FMT)
    except (TypeError, ValueError):
        return None


def fmt_age(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm" % (s // 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)


def default_host() -> str:
    h = str(os.environ.get("MAAORCH_LEASE_HOST", "")).strip()
    if h:
        return h
    ssh = str(os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")).strip()
    if "@" in ssh:
        ssh = ssh.split("@", 1)[1]
    return ssh or "local"


def norm_idx(v) -> str:
    """实例号归一为字符串；非数字直接抛 UsageError（不猜）。"""
    s = str(v).strip()
    if not s.isdigit():
        raise UsageError("实例号必须是数字，实得 %r" % (v,))
    return str(int(s))


def split_idxs(values) -> list:
    """['13,14','15'] -> ['13','14','15']（保序去重）。"""
    out = []
    for v in values or []:
        for part in str(v).split(","):
            p = part.strip()
            if not p:
                continue
            p = norm_idx(p)
            if p not in out:
                out.append(p)
    return out


def key_of(host: str, idx) -> str:
    return "%s:%s" % (host, norm_idx(idx))


class UsageError(RuntimeError):
    """用法/环境错误（退出码 2）。"""


class LockBusy(RuntimeError):
    """租约文件锁被占（临界区拿不到）。"""


class Busy(RuntimeError):
    """目标实例已被别人租下（hold() 用）。"""

    def __init__(self, msg, holder=None):
        super().__init__(msg)
        self.holder = holder or {}


# ---------------------------------------------------------------- 路径 / 锁
def set_runtime(runtime: str | None) -> None:
    rt = runtime or os.environ.get("MAAORCH_LEASE_RUNTIME") or os.path.join(ROOT, "runtime")
    _PATHS["file"] = os.path.join(rt, FILE_NAME)
    _PATHS["lock"] = os.path.join(rt, LOCK_NAME)


def file_path() -> str:
    if not _PATHS["file"]:
        set_runtime(None)
    return _PATHS["file"]


def lock_path() -> str:
    if not _PATHS["lock"]:
        set_runtime(None)
    return _PATHS["lock"]


@contextlib.contextmanager
def _lock(wait_s: float = 10.0):
    """跨进程互斥：O_CREAT|O_EXCL 占位 + 陈旧抢占（持有者活着时句柄占用，别人删不掉，天然防误抢）。"""
    os.makedirs(os.path.dirname(lock_path()), exist_ok=True)
    token = "%d-%d" % (os.getpid(), time.time_ns())
    deadline = time.time() + max(0.0, float(wait_s))
    fd = None
    while True:
        try:
            fd = os.open(lock_path(), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, ("pid=%d ts=%s token=%s\n" % (os.getpid(), now_str(), token)).encode("utf-8"))
            # 回读验证：拿到的是「我写的」那把（防陈旧抢占竞态里互相顶掉）
            try:
                with open(lock_path(), "r", encoding="utf-8", errors="replace") as f:
                    if token not in f.read():
                        os.close(fd)
                        fd = None
                        time.sleep(0.05)
                        continue
            except OSError:
                pass
            break
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(lock_path())
            except OSError:
                age = 0.0
            if age > LOCK_STALE_S and _try_remove_stale():
                time.sleep(0.02)                # 陈旧锁（持有进程被 kill）：抢过来继续
                continue
            if time.time() >= deadline:
                raise LockBusy("租约锁被占（%.0fs 前创建，%s）" % (age, lock_path()))
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        # 只删「还是我的」那把锁（万一被别人陈旧抢占顶掉，别误删人家的）
        try:
            with open(lock_path(), "r", encoding="utf-8", errors="replace") as f:
                mine = token in f.read()
        except OSError:
            mine = False
        if mine:
            with contextlib.suppress(OSError):
                os.remove(lock_path())


def _try_remove_stale() -> bool:
    """陈旧锁清理：删掉且确认已删（活着持有者的锁在 Windows 上删不掉 => 返回 False）。"""
    with contextlib.suppress(OSError):
        os.remove(lock_path())
    return not os.path.exists(lock_path())


# ---------------------------------------------------------------- 账本读写
def _empty() -> dict:
    return {"meta": {}, "leases": {}, "history": []}


def _norm_store(data) -> dict:
    st = _empty()
    if not isinstance(data, dict):
        return st
    if isinstance(data.get("meta"), dict):
        st["meta"] = dict(data["meta"])
    leases = data.get("leases")
    if isinstance(leases, dict):
        for k, v in leases.items():
            if not isinstance(v, dict) or not v.get("owner"):
                continue
            rec = dict(v)
            host, _, idx = str(k).partition(":")
            rec.setdefault("host", host)
            rec.setdefault("idx", idx)
            st["leases"][str(k)] = rec
    hist = data.get("history")
    if isinstance(hist, list):
        st["history"] = [x for x in hist if isinstance(x, dict)]
    for k, v in data.items():                  # 未知顶层键原样保留
        if k not in st:
            st[k] = v
    return st


def _read() -> dict:
    path = file_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _norm_store(json.load(f))
    except FileNotFoundError:
        return _empty()
    except Exception:                          # noqa: BLE001  损坏 => 隔离 + 空账自愈（不阻塞）
        with contextlib.suppress(OSError):
            os.replace(path, "%s.corrupt-%s" % (path, now_dt().strftime("%Y%m%d-%H%M%S")))
        return _empty()


def _write(st: dict) -> None:
    os.makedirs(os.path.dirname(file_path()), exist_ok=True)
    st.setdefault("meta", {})
    st["meta"]["v"] = 1
    st["meta"]["updated"] = now_str()
    st["history"] = (st.get("history") or [])[-KEEP_HISTORY:]
    tmp = file_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, file_path())


def is_active(rec: dict, now: dt.datetime | None = None) -> bool:
    """过期自动失效：`expires_at` 解析不了或已过期 => 不算在租。"""
    if not isinstance(rec, dict) or not rec.get("owner"):
        return False
    exp = parse_ts(rec.get("expires_at"))
    if exp is None:
        return False
    return exp > (now or now_dt())


def _purge_expired(st: dict, now: dt.datetime) -> int:
    """把过期租约清进 history（ended_reason=expired）。返回清掉几条。"""
    n = 0
    for k, rec in list((st.get("leases") or {}).items()):
        if is_active(rec, now):
            continue
        gone = dict(rec)
        gone.update({"ended_at": now_str(now), "ended_reason": "expired"})
        st.setdefault("history", []).append(gone)
        st["leases"].pop(k, None)
        n += 1
    return n


def _mk_rec(host, idx, owner, purpose, ttl_s, note, src, now, **extra) -> dict:
    rec = {"host": str(host), "idx": str(idx), "owner": str(owner), "purpose": str(purpose or ""),
           "acquired_at": now_str(now), "renewed_at": now_str(now),
           "expires_at": now_str(now + dt.timedelta(seconds=int(ttl_s))),
           "ttl_s": int(ttl_s), "pid": os.getpid(), "src": str(src or "cli")}
    if note:
        rec["note"] = str(note)[:300]
    rec.update(extra)
    return rec


# ---------------------------------------------------------------- 公开 API（给别的工具 import）
def load() -> dict:
    """读当前账本副本（不含锁；过期条目按未在租看待）。"""
    return _read()


def get(host: str, idx, now: dt.datetime | None = None) -> dict | None:
    """返回该实例当前**在租**的记录（已过期 => None）。"""
    st = _read()
    rec = (st.get("leases") or {}).get(key_of(host, idx))
    return dict(rec) if rec and is_active(rec, now) else None


def active(host: str | None = None, owner: str | None = None) -> list:
    """当前在租清单（副本）。"""
    now = now_dt()
    out = []
    for _k, rec in (_read().get("leases") or {}).items():
        if not is_active(rec, now):
            continue
        if host and str(rec.get("host")) != str(host):
            continue
        if owner and str(rec.get("owner")) != str(owner):
            continue
        out.append(dict(rec))
    out.sort(key=lambda r: (str(r.get("host")), int(r.get("idx") or 0)))
    return out


def acquire(host: str, idx, owner: str, purpose: str, ttl_s: int = DEFAULT_TTL_S, force: bool = False,
            note: str = "", src: str = "cli", now: dt.datetime | None = None, hold_ms: int = 0) -> dict:
    """单实例取租（原子）。返回 {ok,status,lease|holder,detail}；同主重取=续租。"""
    now = now or now_dt()
    idx = norm_idx(idx)
    owner = str(owner or "").strip()
    if not owner:
        raise UsageError("acquire 必须给持有者（--owner，如任务卡 id t_65b69753）")
    purpose = str(purpose or "").strip()
    if not purpose:
        raise UsageError("acquire 必须给 --purpose（写清拿这台实例干什么）")
    ttl_s = int(ttl_s)
    if not (MIN_TTL_S <= ttl_s <= MAX_TTL_S):
        raise UsageError("--ttl 必须在 %d..%d 秒之间" % (MIN_TTL_S, MAX_TTL_S))
    with _lock():
        if hold_ms:                            # 测试用：把临界区窗口撑大，逼出竞态
            time.sleep(max(0, int(hold_ms)) / 1000.0)
        st = _read()
        purged = _purge_expired(st, now)
        key = key_of(host, idx)
        cur = st["leases"].get(key)
        if cur is not None:
            if cur.get("owner") == owner:      # 同主：续租（不换 acquired_at，保留原始起始）
                cur.update({"renewed_at": now_str(now),
                            "expires_at": now_str(now + dt.timedelta(seconds=ttl_s)),
                            "ttl_s": ttl_s, "pid": os.getpid()})
                if purpose:
                    cur["purpose"] = purpose
                _write(st)
                return {"ok": True, "status": "renewed", "lease": dict(cur), "host": str(host), "idx": idx,
                        "detail": "同一持有者续租到 %s" % cur["expires_at"]}
            if not force:
                if purged:
                    _write(st)                 # 只把过期清理落盘，不动别人的租
                return {"ok": False, "status": "busy", "holder": dict(cur), "host": str(host), "idx": idx,
                        "detail": "被 %s 占着（用途：%s；到期 %s）"
                                  % (cur.get("owner"), cur.get("purpose") or "?", cur.get("expires_at"))}
            stolen = dict(cur)
            stolen.update({"ended_at": now_str(now), "ended_reason": "stolen", "ended_by": owner})
            st.setdefault("history", []).append(stolen)
            rec = _mk_rec(host, idx, owner, purpose, ttl_s, note, src, now, stolen_from=str(cur.get("owner")))
            st["leases"][key] = rec
            _write(st)
            return {"ok": True, "status": "stolen", "lease": dict(rec), "stolen_from": cur.get("owner"),
                    "host": str(host), "idx": idx,
                    "detail": "强制抢租：原持有者 %s（用途 %s）" % (cur.get("owner"), cur.get("purpose") or "?")}
        rec = _mk_rec(host, idx, owner, purpose, ttl_s, note, src, now)
        st["leases"][key] = rec
        _write(st)
        return {"ok": True, "status": "acquired", "lease": dict(rec), "host": str(host), "idx": idx,
                "detail": "取租成功，到期 %s" % rec["expires_at"]}


def acquire_many(host: str, idxs: list, owner: str, purpose: str, ttl_s: int = DEFAULT_TTL_S,
                 force: bool = False, note: str = "", src: str = "cli", hold_ms: int = 0) -> dict:
    """多实例取租，**全有或全无**：中途撞别人 => 把本次新拿到的回滚释放（续租的不动）。"""
    got_new, got_renewed, statuses = [], [], []
    for i in idxs:
        r = acquire(host, i, owner, purpose, ttl_s=ttl_s, force=force, note=note, src=src, hold_ms=hold_ms)
        if not r.get("ok"):
            for g in got_new:
                release(host, g, owner=owner)
            r["rollback"] = list(got_new)
            return r
        statuses.append(r["status"])
        (got_new if r["status"] in ("acquired", "stolen") else got_renewed).append(norm_idx(i))
    status = "stolen" if "stolen" in statuses else ("acquired" if "acquired" in statuses else "renewed")
    return {"ok": True, "status": status, "idxs": [norm_idx(i) for i in idxs],
            "new": got_new, "renewed": got_renewed, "leases": [get(host, i) or {} for i in idxs],
            "detail": "取到 %d 台（新 %d / 续 %d）" % (len(idxs), len(got_new), len(got_renewed))}


def renew(host: str, idx, owner: str, ttl_s: int | None = None, now: dt.datetime | None = None) -> dict:
    """续租（只有持有者能续；已过期 => missing，重新 acquire）。"""
    now = now or now_dt()
    idx = norm_idx(idx)
    owner = str(owner or "").strip()
    if not owner:
        raise UsageError("renew 必须给 --owner")
    with _lock():
        st = _read()
        purged = _purge_expired(st, now)
        key = key_of(host, idx)
        cur = st["leases"].get(key)
        if cur is None:
            if purged:
                _write(st)
            return {"ok": False, "status": "missing", "detail": "该实例没有在租记录（或已过期）——用 acquire 重取"}
        if cur.get("owner") != owner:
            if purged:
                _write(st)
            return {"ok": False, "status": "not_owner", "holder": dict(cur),
                    "detail": "租约在 %s 手上，不是你（%s）" % (cur.get("owner"), owner)}
        ttl = int(ttl_s if ttl_s else (cur.get("ttl_s") or DEFAULT_TTL_S))
        if not (MIN_TTL_S <= ttl <= MAX_TTL_S):
            raise UsageError("--ttl 必须在 %d..%d 秒之间" % (MIN_TTL_S, MAX_TTL_S))
        cur.update({"renewed_at": now_str(now),
                    "expires_at": now_str(now + dt.timedelta(seconds=ttl)),
                    "ttl_s": ttl, "pid": os.getpid()})
        _write(st)
        return {"ok": True, "status": "renewed", "lease": dict(cur),
                "detail": "续租到 %s" % cur["expires_at"]}


def release(host: str, idx, owner: str | None = None, note: str = "", now: dt.datetime | None = None) -> dict:
    """释放。给了 owner 就只放自己的（不符 => 拒）；没给则放掉（会打印原持有者）。"""
    now = now or now_dt()
    idx = norm_idx(idx)
    with _lock():
        st = _read()
        purged = _purge_expired(st, now)
        key = key_of(host, idx)
        cur = st["leases"].get(key)
        if cur is None:
            if purged:
                _write(st)
            return {"ok": True, "status": "free", "detail": "本来就没有在租记录（过期与否一并算空）"}
        if owner and cur.get("owner") != str(owner):
            _write(st) if purged else None
            return {"ok": False, "status": "not_owner", "holder": dict(cur),
                    "detail": "租约在 %s 手上，不是你（%s）——要强放就 release 不带 --owner" % (cur.get("owner"), owner)}
        gone = dict(cur)
        gone.update({"ended_at": now_str(now), "ended_reason": "released",
                     "ended_by": str(owner or cur.get("owner"))})
        st["leases"].pop(key, None)
        st.setdefault("history", []).append(gone)
        _write(st)
        return {"ok": True, "status": "released", "lease": gone,
                "detail": "已释放（原持有者 %s）" % gone.get("owner")}


def release_owner(host: str | None, owner: str, now: dt.datetime | None = None) -> dict:
    """放掉某持有者的全部租约（收尾/救火用）。"""
    now = now or now_dt()
    owner = str(owner or "").strip()
    if not owner:
        raise UsageError("release --owner 不能为空")
    with _lock():
        st = _read()
        _purge_expired(st, now)
        keys = [k for k, rec in (st.get("leases") or {}).items()
                if rec.get("owner") == owner and (not host or str(rec.get("host")) == str(host))]
        for k in keys:
            gone = dict(st["leases"].pop(k))
            gone.update({"ended_at": now_str(now), "ended_reason": "released", "ended_by": owner})
            st.setdefault("history", []).append(gone)
        _write(st)
        return {"ok": True, "status": "released" if keys else "free", "count": len(keys), "keys": keys,
                "detail": "释放 %d 条（%s）" % (len(keys), owner)}


def check(host: str, idx, owner: str | None = None, now: dt.datetime | None = None) -> dict:
    """能不能用？status: free / self / busy。"""
    now = now or now_dt()
    idx = norm_idx(idx)
    rec = (_read().get("leases") or {}).get(key_of(host, idx))
    if rec and is_active(rec, now):
        if owner and rec.get("owner") == str(owner):
            return {"ok": True, "status": "self", "lease": dict(rec),
                    "detail": "自己持有（到 %s）" % rec.get("expires_at")}
        return {"ok": False, "status": "busy", "holder": dict(rec),
                "detail": "被 %s 占着（用途：%s；到期 %s）"
                          % (rec.get("owner"), rec.get("purpose") or "?", rec.get("expires_at"))}
    return {"ok": True, "status": "free", "detail": "空着"}


@contextlib.contextmanager
def hold(host: str, idx, owner: str, purpose: str, ttl_s: int = DEFAULT_TTL_S, force: bool = False,
         wait_s: float = 0.0, note: str = "", src: str = "cli"):
    """同步任务最顺手：`with hold(...):` —— 拿不到抛 Busy；块内异常也保证释放。"""
    r = acquire_wait(host, idx, owner, purpose, ttl_s=ttl_s, force=force, wait_s=wait_s, note=note, src=src)
    if not r.get("ok"):
        raise Busy("实例 %s:%s 拿不到：%s" % (host, idx, r.get("detail") or r.get("status")), r.get("holder"))
    try:
        yield r["lease"]
    finally:
        with contextlib.suppress(Exception):
            release(host, idx, owner=owner)


def acquire_many_wait(host: str, idxs: list, owner: str, purpose: str, ttl_s: int = DEFAULT_TTL_S,
                      force: bool = False, wait_s: float = 0.0, note: str = "", src: str = "cli",
                      hold_ms: int = 0) -> dict:
    """带等待的批量取租（全有或全无）：每 WAIT_POLL_S 秒重试，直到拿到或超时（不保证先来先得）。"""
    start = time.time()
    deadline = start + max(0.0, float(wait_s))
    while True:
        r = acquire_many(host, idxs, owner, purpose, ttl_s=ttl_s, force=force, note=note, src=src,
                         hold_ms=hold_ms)
        r["waited_s"] = int(time.time() - start)
        if r.get("ok") or time.time() >= deadline:
            return r
        time.sleep(min(WAIT_POLL_S, max(0.1, deadline - time.time())))


def acquire_wait(host: str, idx, owner: str, purpose: str, ttl_s: int = DEFAULT_TTL_S, force: bool = False,
                 wait_s: float = 0.0, note: str = "", src: str = "cli") -> dict:
    """单实例版带等待取租（hold() 用它）。"""
    r = acquire_many_wait(host, [norm_idx(idx)], owner, purpose, ttl_s=ttl_s, force=force,
                          wait_s=wait_s, note=note, src=src)
    if r.get("ok"):
        r["lease"] = (r.get("leases") or [None])[0]
    return r


# ---------------------------------------------------------------- 现查映射（不写死）
def _preflight_mod():
    try:
        import mas_preflight
        return mas_preflight
    except Exception as e:                     # noqa: BLE001
        raise UsageError("resolve 依赖同目录 tools/mas_preflight.py（现查映射），导入失败：%s: %s"
                         % (type(e).__name__, e))


def resolve_live(script: str | None = None, queue: str | None = None, with_procs: bool = False) -> dict:
    """现查「脚本/队列 → 实例 idx」：REST（/api/scripts/get + /api/queue/*）+ （可选）运行中 MAA 进程。

    映射唯一来源是 AUTO-MAS 现势：ScriptConfig.Emulator.Index 与队列项；**没有任何本地写死的表**。
    """
    mp = _preflight_mod()
    ns = argparse.Namespace(script=script, queue=queue)
    tgt = mp.resolve_targets(ns)               # 抛 TargetError/RuntimeError => CLI 转 rc 2
    rows = []
    for t in tgt["targets"]:
        rows.append({"script": t.get("name"), "sid": (t.get("sid") or "")[:8],
                     "dir": t.get("dir"), "idx": str(t.get("emu") or "")})
    unknown = [r for r in rows if not r["idx"]]
    idxs = sorted({r["idx"] for r in rows if r["idx"]}, key=int)
    out = {"ok": True, "kind": tgt.get("kind"), "name": tgt.get("name"), "rows": rows,
           "idxs": idxs, "unknown": unknown, "with_procs": bool(with_procs), "api": mp.API}
    if with_procs:
        try:
            survey = mp.remote_survey()
            procs = survey.get("maa") or []
            busy_dirs = set()
            for p in procs:
                exe = mp.norm_dir(p.get("exe")).lower()
                if exe:
                    busy_dirs.add(os.path.dirname(exe).lower())
            for r in rows:
                r["slot_busy"] = mp.norm_dir(r["dir"]).lower() in busy_dirs
            out["maa_procs"] = [{"pid": p.get("pid"), "dir": os.path.dirname(mp.norm_dir(p.get("exe")))}
                                for p in procs]
            out["vmm_running"] = sorted((survey.get("vmm") or {}).keys(), key=int)
        except Exception as e:                 # noqa: BLE001  ssh 失败只降级，不阻断纯映射
            out["procs_error"] = "%s: %s" % (type(e).__name__, e)
    return out


# ---------------------------------------------------------------- 输出
def _print(out_json: bool, payload: dict, lines: list, tail: str) -> None:
    if out_json:
        print(json.dumps(payload, ensure_ascii=False))
        return
    for ln in lines:
        print(ln)
    print(tail)


def _lease_tail(status: str, **kw) -> str:
    parts = ["LEASE=%s" % status]
    for k in ("host", "idx", "owner", "holder", "expires", "count", "idxs"):
        if kw.get(k) not in (None, ""):
            parts.append("%s=%s" % (k, kw[k]))
    return " ".join(parts)


def _ttl_of(a) -> int:
    return int(getattr(a, "ttl", None) or DEFAULT_TTL_S)


def _owner_of(a) -> str:
    owner = str(getattr(a, "owner", "") or "").strip()
    if owner:
        return owner
    env = str(os.environ.get("HERMES_KANBAN_TASK", "")).strip()
    if env:
        return env
    raise UsageError("必须给 --owner（持有者标识，如任务卡 id t_65b69753）；没给时可用环境变量 HERMES_KANBAN_TASK 兜底")


def cmd_acquire(a) -> int:
    host = a.host
    owner = _owner_of(a)
    idxs = split_idxs(a.idx)
    if not idxs and a.script:
        r = resolve_live(script=a.script, queue=None, with_procs=bool(a.with_procs))
        if not r["idxs"]:
            raise UsageError("脚本 %r 解析不出实例号（Emulator.Index 空）" % a.script)
        if len(r["idxs"]) > 1:
            raise UsageError("脚本 %r 命中多台实例 %s —— 用 --idx 逐个/逗号指定" % (a.script, r["idxs"]))
        idxs = r["idxs"]
    if not idxs:
        raise UsageError("给 --idx N[,M...] 或 --script <脚本名>")

    wait_s = float(getattr(a, "wait", 0) or 0)
    res = acquire_many_wait(host, idxs, owner, a.purpose, ttl_s=_ttl_of(a), force=bool(a.force),
                            wait_s=wait_s, note=a.note or "", hold_ms=int(getattr(a, "hold_ms", 0) or 0))

    lease = None
    if res.get("ok"):
        leases = res.get("leases") or []
        lease = leases[0] if leases else None
        lines = ["取租: %s（host=%s idx=%s owner=%s）" % (res["detail"], host, ",".join(idxs), owner),
                 "用途: %s" % a.purpose]
        for l in leases:
            if l:
                lines.append("  idx=%-4s 到期=%s ttl=%ss" % (l.get("idx"), l.get("expires_at"), l.get("ttl_s")))
        tail = _lease_tail(res["status"], host=host, idx=",".join(idxs), owner=owner,
                           expires=(lease or {}).get("expires_at"))
        _print(a.json, {"ok": True, "tool": "mas_instance_lease", "cmd": "acquire", **res,
                        "host": host, "owner": owner}, lines, tail)
        return 0
    holder = res.get("holder") or {}
    lines = ["取租被拒: %s" % res.get("detail"),
             "  host=%s idx=%s 持有者=%s 用途=%s 到期=%s"
             % (host, ",".join(idxs), holder.get("owner"), holder.get("purpose") or "?", holder.get("expires_at")),
             "建议: 换一台实例 / --wait 秒数 排队 / 确认持有者已死再 --force 抢租（会留痕）"]
    tail = _lease_tail("busy", host=host, idx=res.get("idx") or ",".join(idxs), holder=holder.get("owner"),
                       expires=holder.get("expires_at"))
    _print(a.json, {"ok": False, "tool": "mas_instance_lease", "cmd": "acquire", **res,
                    "host": host, "owner": owner}, lines, tail)
    return 1


def cmd_renew(a) -> int:
    res = renew(a.host, a.idx, _owner_of(a), ttl_s=(int(a.ttl) if a.ttl else None))
    lease = res.get("lease") or {}
    lines = ["续租: %s" % res["detail"]]
    tail = _lease_tail(res["status"], host=a.host, idx=norm_idx(a.idx), owner=_owner_of(a),
                       expires=lease.get("expires_at"))
    _print(a.json, {"ok": bool(res.get("ok")), "tool": "mas_instance_lease", "cmd": "renew", **res},
           lines, tail)
    return 0 if res.get("ok") else 1


def cmd_release(a) -> int:
    if a.idx:
        res = release(a.host, a.idx, owner=(a.owner or None))
    elif a.owner:
        res = release_owner(a.host, a.owner)
    else:
        raise UsageError("release 需要 --idx N（可加 --owner 限本人）或 --owner X（放掉其全部）")
    lease = res.get("lease") or {}
    lines = ["释放: %s" % res["detail"]]
    tail = _lease_tail(res["status"], host=a.host, idx=norm_idx(a.idx) if a.idx else "",
                       owner=lease.get("owner") or (a.owner or ""), count=res.get("count") or "")
    _print(a.json, {"ok": bool(res.get("ok")), "tool": "mas_instance_lease", "cmd": "release", **res},
           lines, tail)
    return 0 if res.get("ok") else 1


def cmd_check(a) -> int:
    host = a.host
    owner = str(a.owner or "").strip() or None
    items = []                                  # (idx, 来源说明)
    for i in split_idxs(a.idx):
        items.append((i, "cli"))
    if a.script or a.queue:
        r = resolve_live(script=a.script, queue=a.queue, with_procs=bool(a.with_procs))
        for i in r["idxs"]:
            items.append((i, ("脚本 %s" % a.script) if a.script else ("队列 %s" % a.queue)))
        for u in r["unknown"]:
            items.append(("?", "脚本 %s 的 Emulator.Index 为空（判不了）" % u.get("script")))
    if not items:
        raise UsageError("给 --idx N（可逗号）或 --script/--queue（现查映射）")
    rows, bad, unknown = [], False, False
    for idx, src in items:
        if idx == "?":
            rows.append({"idx": "?", "src": src, "status": "unknown", "detail": src})
            unknown = True
            continue
        r = check(host, idx, owner=owner)
        rows.append({"idx": idx, "src": src, **r})
        if not r.get("ok"):
            bad = True
    lines = []
    for r in rows:
        mark = {"free": "空", "self": "自己持有", "busy": "被占", "unknown": "判不了"}.get(r.get("status"), r.get("status"))
        lines.append("  idx=%-4s %-5s %s" % (r.get("idx"), mark, r.get("detail") or r.get("src")))
    if bad:
        status = "busy"
    elif unknown:
        status = "unknown"
    else:
        status = "free"
    idxs_str = ",".join(r["idx"] for r in rows)
    tail = _lease_tail(status, host=host, idx=idxs_str, owner=owner or "")
    _print(a.json, {"ok": status == "free", "tool": "mas_instance_lease", "cmd": "check",
                    "status": status, "host": host, "owner": owner or "", "rows": rows}, lines, tail)
    return 0 if status == "free" else (2 if unknown else 1)


def cmd_list(a) -> int:
    now = now_dt()
    st = load()
    rows = active(host=a.host, owner=(a.owner or None))
    lines = []
    if rows:
        lines.append("%-15s %-4s %-16s %-7s %-6s %-19s %-7s %s"
                     % ("HOST", "IDX", "OWNER", "TTL(s)", "AGE", "EXPIRES", "PID", "PURPOSE"))
        for r in rows:
            age_base = parse_ts(r.get("renewed_at") or r.get("acquired_at")) or now
            lines.append("%-15s %-4s %-16s %-7s %-6s %-19s %-7s %s"
                         % (r.get("host"), r.get("idx"), str(r.get("owner"))[:16], r.get("ttl_s"),
                            fmt_age((now - age_base).total_seconds()), r.get("expires_at"), r.get("pid"),
                            r.get("purpose")))
    else:
        lines.append("（没有在租实例）")
    extra = {}
    if getattr(a, "all", False):
        expired = [dict(v) for v in (st.get("leases") or {}).values() if not is_active(v, now)]
        hist = (st.get("history") or [])[-15:]
        if expired:
            lines.append("过期残留（下次写账即清理）:")
            for v in expired:
                lines.append("  %s:%s %s 用途=%s 过期于 %s"
                             % (v.get("host"), v.get("idx"), v.get("owner"), v.get("purpose") or "?",
                                v.get("expires_at")))
        if hist:
            lines.append("最近历史（末 15 条）:")
            for h in hist:
                lines.append("  %s %s:%s %s %s（%s）" % (h.get("ended_at") or "?", h.get("host"), h.get("idx"),
                                                         h.get("owner"), h.get("ended_reason") or "?",
                                                         h.get("purpose") or "?"))
        extra = {"expired": expired, "history": hist}
    tail = "LEASES=%d" % len(rows)
    _print(a.json, {"ok": True, "tool": "mas_instance_lease", "cmd": "list", "count": len(rows),
                    "host": a.host, "owner": a.owner or "", "leases": rows, **extra}, lines, tail)
    return 0


def cmd_resolve(a) -> int:
    if not (a.script or a.queue):
        raise UsageError("resolve 需要 --script <名> 或 --queue <名>")
    r = resolve_live(script=a.script, queue=a.queue, with_procs=bool(a.with_procs))
    nm = ("脚本 %s" % a.script) if a.script else ("队列 %s" % a.queue)
    lines = ["现查映射（REST %s）: %s → %d 脚本 / %d 实例" % (r.get("api"), nm, len(r["rows"]), len(r["idxs"]))]
    for row in r["rows"]:
        busy = row.get("slot_busy")
        lines.append("  script=%-22s idx=%-4s dir=%s%s"
                     % (row.get("script"), row.get("idx") or "?", row.get("dir"),
                        ("  slot_busy=yes" if busy else ("  slot_busy=no" if busy is not None else ""))))
    if r["unknown"]:
        lines.append("  ！有脚本的 Emulator.Index 为空，判不了：%s"
                     % ", ".join(str(u.get("script")) for u in r["unknown"]))
    if a.with_procs:
        if r.get("procs_error"):
            lines.append("  ！在跑进程检查失败（降级）：%s" % r["procs_error"])
        else:
            lines.append("  运行中实例（VMM）：%s" % (",".join(r.get("vmm_running") or []) or "无"))
            lines.append("  在跑 MAA 槽位：%s"
                         % (", ".join("%s(pid %s)" % (p.get("dir"), p.get("pid")) for p in r.get("maa_procs") or [])
                            or "无"))
    lines.append("idxs=%s" % (",".join(r["idxs"]) or "无"))
    tail = _lease_tail("resolved" if r["idxs"] else "unknown", idxs=",".join(r["idxs"]))
    _print(a.json, {"ok": bool(r["idxs"]), "tool": "mas_instance_lease", "cmd": "resolve", **r}, lines, tail)
    return 0 if r["idxs"] else 2


def cmd_prune(a) -> int:
    with _lock():
        st = _read()
        n = _purge_expired(st, now_dt())
        before = len(st.get("history") or [])
        _write(st)
        after = len(st.get("history") or [])
    lines = ["清理: 过期租约 %d 条进历史；历史 %d → %d（上限 %d）" % (n, before, after, KEEP_HISTORY)]
    _print(a.json, {"ok": True, "tool": "mas_instance_lease", "cmd": "prune", "removed": n,
                    "history": after}, lines, "LEASE_PRUNE removed=%d" % n)
    return 0


# ---------------------------------------------------------------- 自测
def _tool_cmd(args: list, runtime: str) -> list:
    return [sys.executable, os.path.abspath(__file__)] + args + ["--runtime", runtime]


def _run(args: list, runtime: str, timeout: int = 60) -> tuple:
    p = subprocess.run(_tool_cmd(args, runtime), capture_output=True, stdin=subprocess.DEVNULL,
                       timeout=timeout)
    out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode, out


def _spawn(args: list, runtime: str):
    return subprocess.Popen(_tool_cmd(args, runtime), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL)


def _json_of(out: str) -> dict:
    for ln in reversed(out.strip().splitlines()):
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                return json.loads(ln)
            except ValueError:
                continue
    return {}


def selftest(race_n: int = 4) -> int:
    bad = 0
    tmp = tempfile.mkdtemp(prefix="lease_selftest_")
    rt = os.path.join(tmp, "runtime")
    set_runtime(rt)
    host = "st"
    def ck(label, got, want):
        nonlocal bad
        ok = got == want
        bad += 0 if ok else 1
        print("%s %-42s got=%r want=%r" % ("PASS" if ok else "FAIL", label, got, want))

    print("== 单进程语义（隔离 runtime=%s）==" % rt)
    r = acquire(host, "13", "t_a", "单测-取租", ttl_s=60)
    ck("acquire free", (r["ok"], r["status"]), (True, "acquired"))
    ck("check free→busy(other)", check(host, "13", owner="t_b")["status"], "busy")
    ck("check self", check(host, "13", owner="t_a")["status"], "self")
    r2 = acquire(host, "13", "t_b", "单测-撞车", ttl_s=60)
    ck("acquire busy", (r2["ok"], r2["status"]), (False, "busy"))
    ck("busy holder", r2["holder"]["owner"], "t_a")
    r3 = acquire(host, "13", "t_a", "单测-续租", ttl_s=120)
    ck("same owner→renewed", (r3["ok"], r3["status"]), (True, "renewed"))
    ck("ttl updated", r3["lease"]["ttl_s"], 120)
    ck("acquired_at kept", r3["lease"]["acquired_at"] == r["lease"]["acquired_at"], True)
    ck("renew by other", renew(host, "13", "t_b")["status"], "not_owner")
    ck("release by other", (release(host, "13", owner="t_b")["ok"], release(host, "13", owner="t_b")["status"]),
       (False, "not_owner"))
    ck("release by owner", (release(host, "13", "t_a")["ok"], release(host, "13", "t_a")["status"]),
       (True, "free"))
    ck("check after release", check(host, "13")["status"], "free")

    print("== 多实例全有或全无（回滚）==")
    acquire(host, "92", "t_a", "占住 92", ttl_s=60)
    r4 = acquire_many(host, ["93", "92"], "t_b", "批量撞车", ttl_s=60)   # 93 先拿到、92 撞车 ⇒ 93 必须回滚
    ck("bulk busy", (r4["ok"], r4["status"]), (False, "busy"))
    ck("rollback released 93", (check(host, "93")["status"], r4.get("rollback")), ("free", ["93"]))
    ck("92 still A", check(host, "92")["status"], "busy")
    r5 = acquire_many(host, ["93", "94"], "t_b", "批量", ttl_s=60)
    ck("bulk ok", (r5["ok"], r5["status"]), (True, "acquired"))
    release_owner(host, "t_b")
    release_owner(host, "t_a")

    print("== --force 抢租留痕 ==")
    acquire(host, "95", "t_a", "原持有", ttl_s=60)
    r6 = acquire(host, "95", "t_b", "接管", ttl_s=60, force=True)
    ck("force steal", (r6["ok"], r6["status"], r6.get("stolen_from")), (True, "stolen", "t_a"))
    hist = load().get("history") or []
    ck("history stolen", [h.get("ended_reason") for h in hist if h.get("idx") == "95"], ["stolen"])
    release_owner(host, "t_b")

    print("== hold() 上下文管理器（给别人抛 Busy；退出/异常必释放）==")
    with hold(host, "98", "t_hold", "上下文占位", ttl_s=60) as lease:
        ck("hold acquired", lease.get("owner"), "t_hold")
        ck("hold blocks others", check(host, "98")["status"], "busy")
        try:
            with hold(host, "98", "t_other", "撞车", ttl_s=60):
                pass
            ck("hold raises Busy", "no-raise", "Busy")
        except Busy as e:
            ck("hold raises Busy", (e.holder or {}).get("owner"), "t_hold")
    ck("hold released after block", check(host, "98")["status"], "free")
    try:
        with hold(host, "98", "t_hold2", "异常路径", ttl_s=60):
            raise ValueError("boom")
    except ValueError:
        pass
    ck("released after exception", check(host, "98")["status"], "free")

    print("== TTL 过期自动失效 + 被接管（真时钟，ttl=4s）==")
    t_start = time.time()
    r7 = acquire(host, "96", "t_ttl_a", "短租", ttl_s=4)
    ck("短租取到", (r7["ok"], r7["status"]), (True, "acquired"))
    ck("在租期内=busy", check(host, "96")["status"], "busy")
    while time.time() - t_start < 8.0 and check(host, "96")["status"] != "free":
        time.sleep(0.2)
    ck("过期满 4s→free", check(host, "96")["status"], "free")
    rc, out = _run(["acquire", "--idx", "96", "--owner", "t_ttl_b", "--purpose", "接班",
                    "--ttl", "60", "--host", host, "--json"], rt)
    ck("child takeover rc0", (rc, _json_of(out).get("status")), (0, "acquired"))
    ck("history expired", [h.get("ended_reason") for h in (load().get("history") or [])
                           if h.get("idx") == "96" and h.get("owner") == "t_ttl_a"], ["expired"])
    release_owner(host, "t_ttl_b")

    print("== 真·多进程互抢（%d 个进程同时 acquire 同一实例，临界区撑 400ms）==" % race_n)
    procs = []
    for i in range(race_n):
        procs.append(_spawn(["acquire", "--idx", "999", "--owner", "race-%d" % i,
                             "--purpose", "race", "--ttl", "60", "--host", host, "--json",
                             "--hold-ms", "400"], rt))
    results = []
    for p in procs:
        out = (p.communicate(timeout=90)[0] or b"").decode("utf-8", "replace")
        results.append((p.returncode, _json_of(out)))
    winners = [i for i, (rc, _j) in enumerate(results) if rc == 0]
    ck("exactly one winner", len(winners), 1)
    ck("losers busy", sorted({j.get("status") for rc, j in results if rc != 0}), ["busy"])
    ck("losers rc1", sorted({rc for rc, _j in results if rc != 0}), [1])
    rows = [r for r in active(host=host) if r.get("idx") == "999"]
    ck("exactly one lease on disk", len(rows), 1)
    ck("winner is the winner", rows[0]["owner"] if rows else None,
       "race-%d" % winners[0] if winners else "?")
    ck("all ops yielded valid json", all(bool(j) for _rc, j in results), True)

    print("== 同主并发（幂等续租，仍只有一条）==")
    procs = [_spawn(["acquire", "--idx", "997", "--owner", "same-owner", "--purpose", "同主",
                     "--ttl", "60", "--host", host, "--json"], rt) for _ in range(3)]
    rcs = []
    for p in procs:
        p.communicate(timeout=90)
        rcs.append(p.returncode)
    ck("same owner all rc0", rcs, [0, 0, 0])
    ck("one lease row", len([x for x in active(host=host) if x.get("idx") == "997"]), 1)

    print("== 文件卫生 ==")
    with open(file_path(), "r", encoding="utf-8") as f:
        data = json.load(f)
    ck("ledger is valid json", isinstance(data.get("leases"), dict), True)
    ck("no tmp leftover", os.path.exists(file_path() + ".tmp"), False)
    ck("lock removed", os.path.exists(lock_path()), False)
    ck("history all ended", all(h.get("ended_reason") for h in (data.get("history") or [])), True)

    print("== check 子命令退出码（子进程真跑）==")
    rc, _ = _run(["check", "--idx", "996", "--host", host], rt)
    ck("check free rc0", rc, 0)
    _run(["acquire", "--idx", "996", "--owner", "t_ck", "--purpose", "占用", "--ttl", "60",
          "--host", host], rt)
    rc, out = _run(["check", "--idx", "996", "--host", host], rt)
    ck("check busy rc1", rc, 1)
    ck("check busy token", out.strip().splitlines()[-1].split()[0], "LEASE=busy")
    rc, _ = _run(["check", "--idx", "996", "--host", host, "--owner", "t_ck"], rt)
    ck("check self rc0", rc, 0)
    rc, _ = _run(["--check", "--idx", "996", "--host", host], rt)     # 兼容写法
    ck("alias --check rc1", rc, 1)

    print("\nselftest: %s (%d 失败)  [runtime=%s]" % ("OK" if not bad else "FAIL", bad, rt))
    if not bad:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if not bad else 1


# ---------------------------------------------------------------- CLI
ALIASES = {"--check": "check", "--list": "list", "--selftest": "selftest", "--prune": "prune"}


def add_common(p):
    p.add_argument("--host", default=None, help="机台标签（默认：MAAORCH_LEASE_HOST，否则 MAAORCH_SSH_HOST 去 user@；本机用 local）")
    p.add_argument("--runtime", default=None, help="runtime 目录覆盖（测试用；默认 runtime/）")
    p.add_argument("--json", action="store_true", help="单行 JSON 输出")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mas_instance_lease.py",
                                 description="实例租约：跨任务/跨工具的一台实例一人用（只写 runtime/instance_leases.json，不发车）",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("acquire", help="取租（同主=续租；多实例=全有或全无）")
    add_common(p)
    p.add_argument("--idx", action="append", default=[], help="实例号，可逗号分隔/重复；如 --idx 13 或 --idx 13,14")
    p.add_argument("--script", default=None, help="按脚本现查实例号（REST）再取租；与 --idx 二选一")
    p.add_argument("--owner", default=None, help="持有者（任务卡 id，如 t_65b69753）；缺省取 HERMES_KANBAN_TASK")
    p.add_argument("--purpose", default=None, help="用途（必填）：写清拿这台实例干什么")
    p.add_argument("--ttl", type=int, default=DEFAULT_TTL_S, help="租期秒数（默认 %d，1..%d）" % (DEFAULT_TTL_S, MAX_TTL_S))
    p.add_argument("--wait", type=float, default=0.0, help="等空位的秒数（每 2s 重试；不保证先来先得）")
    p.add_argument("--force", action="store_true", help="抢租（会写 history=stolen；仅在确认持有者已死时用）")
    p.add_argument("--note", default=None, help="备注（落账，便于回查）")
    p.add_argument("--with-procs", action="store_true", help="随 --script 一起现查运行中 MAA 进程（ssh，慢）")
    p.add_argument("--hold-ms", type=int, default=0, help=argparse.SUPPRESS)   # 自测用：撑大临界区
    p.set_defaults(fn=cmd_acquire)

    p = sub.add_parser("renew", help="续租（只有持有者能续）")
    add_common(p)
    p.add_argument("--idx", required=True, help="实例号")
    p.add_argument("--owner", default=None)
    p.add_argument("--ttl", type=int, default=None, help="新租期秒数（默认沿用账上 ttl_s）")
    p.set_defaults(fn=cmd_renew)

    p = sub.add_parser("release", help="释放（--idx N [--owner X] 或 --owner X 放掉其全部）")
    add_common(p)
    p.add_argument("--idx", default=None, help="实例号")
    p.add_argument("--owner", default=None, help="持有者（给了就只放自己的）")
    p.set_defaults(fn=cmd_release)

    p = sub.add_parser("check", help="能不能用？free=rc0 / busy=rc1 / 判不了=rc2")
    add_common(p)
    p.add_argument("--idx", action="append", default=[], help="实例号（可逗号/重复）")
    p.add_argument("--script", default=None, help="按脚本名现查实例号再查")
    p.add_argument("--queue", default=None, help="按队列名现查实例号再查")
    p.add_argument("--owner", default=None, help="给了就把「自己的租约」算空（rc0）")
    p.add_argument("--with-procs", action="store_true", help="随 --script/--queue 一起现查在跑进程（ssh，慢）")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("list", help="看在租清单")
    add_common(p)
    p.add_argument("--all", action="store_true", help="附：过期残留 + 最近历史")
    p.add_argument("--owner", default=None, help="只看某持有者的")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("resolve", help="现查「脚本/队列 → 实例 idx」（REST + 可选在跑进程；不写账）")
    add_common(p)
    p.add_argument("--script", default=None)
    p.add_argument("--queue", default=None)
    p.add_argument("--with-procs", action="store_true", help="同时 ssh 现查在跑 MAA 槽位 / VMM 实例")
    p.set_defaults(fn=cmd_resolve)

    p = sub.add_parser("prune", help="把过期租约清进 history")
    add_common(p)
    p.set_defaults(fn=cmd_prune)

    p = sub.add_parser("selftest", help="离线自测（隔离 runtime；含多进程互抢 / TTL 接管）")
    add_common(p)
    p.add_argument("--race", type=int, default=4, help="互抢进程数（默认 4）")
    p.set_defaults(fn=lambda a: selftest(race_n=max(2, int(a.race))))

    return ap


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                          # noqa: BLE001
        pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ALIASES:            # 兼容 `--check --idx N` 写法
        argv = [ALIASES[argv[0]]] + argv[1:]
    ap = build_parser()
    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        ap.print_help()
        return 2
    set_runtime(getattr(a, "runtime", None))
    if hasattr(a, "host") and not getattr(a, "host", None):
        a.host = default_host()                # 统一 host 标签口径（默认 MAAORCH_LEASE_HOST / SSH 主机）
    try:
        code = a.fn(a)
    except UsageError as e:
        payload = {"ok": False, "tool": "mas_instance_lease", "cmd": a.cmd, "error": str(e)}
        print(json.dumps(payload, ensure_ascii=False) if getattr(a, "json", False) else str(e),
              file=sys.stderr)
        return 2
    except LockBusy as e:
        print("并发冲突：%s" % e, file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    except Exception as e:                     # noqa: BLE001  收窄到一条干净报错（fail-closed）
        print("失败（%s）: %s" % (type(e).__name__, e), file=sys.stderr)
        return 2
    return int(code or 0)


if __name__ == "__main__":
    sys.exit(main())
