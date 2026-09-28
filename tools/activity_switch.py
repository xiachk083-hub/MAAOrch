#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""activity_switch.py —— 活动期「刷取计划」自动切换（受控 + 审批 + 可还原）：活动开→切活动关，活动关→切回原计划。

定位：把「活动一开很多号该改刷活动关 / 活动一关该切回主线」做成**受控自动化**，不再靠人记得改
（现场教训：SR「月行水上」09-18 结束，46 个号白挂到 09-27 被巡检抓到，见 maa-event-farming 技能 §0-2）。
不重造骨架：写面/还原走 `tools/mas_run_profile.py` 的「备份 → 临时计划表 + StageMode 指针 → 还原」骨架
（import 复用其函数；**不直接改存量计划表**，也不动账号自己的 7 键）。

三条判据（全部可解释、落进报告的「依据 → 结论」链）：
  ① 触发：`runtime/reports/server_events.json`（≤3 天可用，>7 天拒绝据此切换）——各服在期/待开活动；
  ② 可刷层（MAA 侧权威）：MAA 活动表 `StageActivityV2.json` 里该 client 段**未过期**的 `sideStoryStage`
     → 关卡码列表（去掉「代理」伪条目）；这是「活动关是否真的可刷」的判定；表里没有 = 不可切（fail-closed）。
  ③ 能力台账：`runtime/reports/stage_capability.json`（账号×关卡 success/failed/unknown）——
     unknown ⇒ **先试探一次再切**（`--probe-unknown` 才真试探）；failed ⇒ 该关从目标里剔除。

动作（对指定范围的账号）：
  * switch  —— 活动在期且可刷：目标计划 = [活动关（MAA 顺序，剔除 failed）≤4 位] + 尾位 1-7（恒开放兜底）；
  * probe   —— 首关台账 unknown：默认只列为「需试探」；`--probe-unknown` 时先跑
               `mas_probe_farm.py --probe-only`（复用其发车/读回/还原），成功才切；
  * restore —— 我方以前切过、且该活动已结束（记录 end 已过 or MAA 已不可刷）：切回原计划（备份值）；
  * heal    —— （可选 `--heal-stale`）计划所有非空位都是**已关闭的活动关**（如 SR-7）⇒
               MAA 会「关卡计划内没有当日开放的关卡」整段跳过（白烧）；收尾切到 `--heal-to`（默认 1-7）；
  * already/skip/noop —— 不写。

写面（每号）：新建「临时-活动-<tag>-<stamp>」计划表（全量复制该号当日生效值 + 只差分期关卡位），
把 `Info.StageMode` 指过去 —— 账号自己的值/存量计划表一律不碰；还原 = StageMode 回写 + 删临时表。
跑中「配置锁定」：写被 AUTO-MAS 拒绝 ⇒ 该号跳过（零残留），绝不 kill/重启任何东西。

闸门（apply 时逐条；dry-run 只打印）：
  G0 `MAAORCH_ACTIVITY_SWITCH_NO_GO` / `MAAORCH_DISPATCH_NO_GO` 一票否决（rc=2）
  G1 REST 可达（绕系统代理）
  G2 范围合法：**apply 必须显式给范围**（--server / --accounts / --scripts）
  G3 审批：apply 必须 `--approved-by <谁/依据>`（代入审计与台账；缺 = 拒绝）
  G4 证据新鲜：server_events 快照 >7 天 ⇒ 拒绝切换（>3 天告警）
  G5 单飞锁 `runtime/activity_switch.lock`（同一时刻一个切换任务）
  G6 班次运行中不动：先 ssh 探 MAA.exe 占用（忙=跳过该脚本的号；探不到=告警后仍走，锁由 AUTO-MAS 兜底）

用法（**默认 dry-run；真写要 --apply + --approved-by**）：
    python tools/activity_switch.py                              # dry-run（全队，只算不写；落计划文件）
    python tools/activity_switch.py --server jp                  # 只算日服
    python tools/activity_switch.py --server cn_official --pretend-now "2026-09-29 17:00"   # 预演（不写）
    python tools/activity_switch.py --server jp --stages "PA-8,PA-7,PA-6"                    # 显式目标关
    python tools/activity_switch.py --server jp --apply --approved-by lead                   # 真切（写临时活动计划表）
    python tools/activity_switch.py --server jp --apply --approved-by lead --probe-unknown   # unknown 先试探
    python tools/activity_switch.py --restore asw_20260928_0930_jp [--apply]                  # 一键还原（dry-run 须 --apply）
    python tools/activity_switch.py --restore-all --apply                                     # 还原所有未还原切换
    python tools/activity_switch.py --status                     # 切换台账（含未还原）
    python tools/activity_switch.py --list-plans                 # 只读：计划表 + 「临时-活动-*」残留
    python tools/activity_switch.py --selftest                   # 离线自测（假传输，零触网零写 runtime）

产出/审计：
    runtime/activity_switch_state.json            切换台账（凭它还原，不凭记忆）
    runtime/activity_switch/backups/<switch_id>/  备份（账号/计划表快照 + sha256；凭据键读入即弃）
    runtime/reports/activity_switch/*.md|.json    每次的计划/执行报告（人读 + 机读）
    runtime/mas_activity_switch_log.jsonl         本工具审计流
    runtime/mas_ledger.jsonl                      全局台账（kind=activity_switch）

退出码：0 正常（dry-run 计划成立 / apply 全部完成）；2 拒绝（参数/闸门/证据过期，零写）；
        3 部分完成（班次忙跳过/试探未证/单项失败已回滚）；4 有残留（还原失败，需人工）。
环境变量：MAAORCH_MAS_API / MAAORCH_SSH_KEY / MAAORCH_SSH_HOST（同其他 mas_* 工具）；
         MAAORCH_ACTIVITY_SWITCH_RUNTIME 改 runtime 落点（自测用）；MAAORCH_ACTIVITY_SWITCH_NO_GO 一票否决。
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
import urllib.request

if hasattr(sys.stdout, "reconfigure"):                      # Windows 控制台中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
RUNTIME = os.environ.get("MAAORCH_ACTIVITY_SWITCH_RUNTIME") or os.path.join(ROOT, "runtime")
LOG = os.path.join(RUNTIME, "mas_activity_switch_log.jsonl")
STATE = os.path.join(RUNTIME, "activity_switch_state.json")
LOCK = os.path.join(RUNTIME, "activity_switch.lock")
BACKUPS = os.path.join(RUNTIME, "activity_switch", "backups")
REPORT_DIR = os.path.join(RUNTIME, "reports", "activity_switch")
EVENTS = os.path.join(RUNTIME, "reports", "server_events.json")
CAP_JSON = os.path.join(RUNTIME, "reports", "stage_capability.json")
ACTIVITY_STAGES_CFG = os.path.join(ROOT, "config", "activity_stages.json")
PLANS_CFG = os.path.join(ROOT, "config", "activity_switch_plans.json")
PROBE_FARM = os.path.join(TOOLS, "mas_probe_farm.py")
PY312 = os.environ.get("MAAORCH_ACTIVITY_SWITCH_PYTHON",
                       r"C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe")

MAA_URL = os.environ.get("MAAORCH_MAA_STAGE_ACTIVITY_URL",
                         "https://api.maa.plus/MaaAssistantArknights/api/gui/StageActivityV2.json")
TS_FMT = "%Y-%m-%d %H:%M:%S"
STAMP_FMT = "%Y%m%d_%H%M%S"
ACT_PLAN_PREFIX = "临时-活动"
EVENTS_WARN_DAYS = 3.0
EVENTS_FAIL_DAYS = 7.0
LOCK_STALE_S = 6 * 3600
TAIL_STAGE = "1-7"                                          # 恒开放尾位兜底（技能 §6-4）
HEAL_DEFAULT = "1-7"
FARM_HINTS = ("刷关", "SideStory")                          # 与 mas_plan_builder 同口径
ST_OK, ST_FAILED, ST_UNKNOWN = "success", "failed", "unknown"
_SLEEP = time.sleep                                          # selftest 里快进
NO_GO_ENVS = ("MAAORCH_ACTIVITY_SWITCH_NO_GO", "MAAORCH_DISPATCH_NO_GO")

# server key（server_events.json 口径）→ MAA client / 短名 / 别名
SERVERS = {
    "cn_official": {"client": "Official", "tag": "官", "name": "国服（官服）",
                    "aliases": ("cn_official", "official", "cn", "国服", "官服", "官")},
    "cn_bilibili": {"client": "Bilibili", "maafallback": "Official", "tag": "B", "name": "B服",
                    "aliases": ("cn_bilibili", "bilibili", "b", "b服")},
    "jp": {"client": "YoStarJP", "tag": "日", "name": "日服",
           "aliases": ("jp", "yostarjp", "日服", "日")},
    "kr": {"client": "YoStarKR", "tag": "韩", "name": "韩服",
           "aliases": ("kr", "yostarkr", "韩服", "韩")},
    "tw": {"client": "txwy", "tag": "台", "name": "台服（繁中服）",
           "aliases": ("tw", "txwy", "台服", "繁中服", "台")},
    "en": {"client": "YoStarEN", "tag": "国际", "name": "国际服",
           "aliases": ("en", "yostaren", "global", "国际服", "国际")},
}
CLIENT2KEY = {}
for _k, _v in SERVERS.items():
    CLIENT2KEY[_v["client"]] = _k
    if _v.get("maafallback"):
        CLIENT2KEY.setdefault(_v["maafallback"], _k)


class RefuseError(RuntimeError):
    """闸门/输入拒绝（写之前，零残留）。"""


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
        f.write("\n")
    os.replace(tmp, path)


def scrub(obj):
    return _rp().scrub(obj)


def audit(rec: dict) -> None:
    os.makedirs(RUNTIME, exist_ok=True)
    rec = dict(rec)
    rec.setdefault("ts", _now())
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(scrub(rec), ensure_ascii=False) + "\n")


def ledger_append(rec: dict) -> None:
    with contextlib.suppress(Exception):
        _rp().ledger_append(rec)


# ---------------------------------------------------------------- 锁
class LockBusy(RuntimeError):
    pass


@contextlib.contextmanager
def acquire_lock():
    fd = None
    try:
        os.makedirs(RUNTIME, exist_ok=True)
        if os.path.exists(LOCK):
            age = time.time() - os.path.getmtime(LOCK)
            if age < LOCK_STALE_S:
                raise LockBusy("另一个 activity_switch 任务在跑（锁 %s，%.0fs 前刷新）" % (LOCK, age))
            log("发现陈旧锁（%.0fs）；覆盖" % age)
            with contextlib.suppress(OSError):
                os.remove(LOCK)
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise LockBusy("另一个 activity_switch 任务刚拿到锁（%s）" % LOCK)
        os.write(fd, ("%d %s\n" % (os.getpid(), _now())).encode("utf-8"))
        yield
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                os.remove(LOCK)


def _touch_lock():
    """长动作（逐个试探/逐号写）期间续锁，防 6h 陈旧阈值被二次进人。"""
    with contextlib.suppress(OSError):
        if os.path.exists(LOCK):
            os.utime(LOCK, None)


# ---------------------------------------------------------------- 时间/解析
def parse_ts(s, tz_hours=None):
    """解析 '2026-09-29 16:00(:00)'；给 tz_hours 则附加时区。"""
    s = str(s or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            d = dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
        if tz_hours is None:
            return d
        return d.replace(tzinfo=dt.timezone(dt.timedelta(hours=float(tz_hours))))
    with contextlib.suppress(ValueError):            # ISO 8601（含 T/时区偏移）：记录里的 end / 部分快照用它
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if tz_hours is not None and d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone(dt.timedelta(hours=float(tz_hours))))
        return d
    return None


def as_local(d):
    """aware/native → 本机时钟的 naive 值（本机 = 北京时间）。"""
    if d is None:
        return None
    if d.tzinfo is None:
        return d
    return d.astimezone().replace(tzinfo=None)


def fmt_d(d):
    return d.strftime(TS_FMT) if isinstance(d, dt.datetime) else str(d or "")


def iso(d):
    return d.isoformat() if isinstance(d, dt.datetime) else str(d or "")


# ---------------------------------------------------------------- 输入①：server_events
def events_freshness(ev_doc, now):
    gen = ev_doc.get("generated_at_epoch")
    gen_dt = None
    if gen:
        with contextlib.suppress(Exception):
            gen_dt = dt.datetime.fromtimestamp(float(gen))
    if gen_dt is None:
        s = str(ev_doc.get("generated_at_bjt") or "").replace("+08:00", "").strip()
        gen_dt = parse_ts(s)
    age = (now - gen_dt).total_seconds() / 86400 if gen_dt else None
    return {"generated_at": fmt_d(gen_dt), "age_days": round(age, 2) if age is not None else None,
            "warn": (age is not None and age > EVENTS_WARN_DAYS),
            "ok": (age is not None and age <= EVENTS_FAIL_DAYS)}


def load_events(path):
    doc = load_json(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("servers"), dict):
        raise RefuseError("server_events 快照不可读/结构不符：%s" % path)
    return doc


def is_farm_event(e: dict) -> bool:
    t = str((e or {}).get("type") or "")
    return any(h in t for h in FARM_HINTS)


def events_server_view(ev_doc, key):
    s = (ev_doc.get("servers") or {}).get(key) or {}
    ref = s.get("events_ref")
    if ref and ref != key:                                  # B 服：引用官服时间表
        s2 = dict((ev_doc.get("servers") or {}).get(ref) or {})
        s2.update({k: v for k, v in s.items() if k in ("maa_stage_activity", "note") and v})
        s = s2
        s["_ref"] = ref
    return {"key": key, "in_period": s.get("is_in_event_period"),
            "verdict": s.get("activity_verdict") or "",
            "active": [e for e in (s.get("active_events") or []) if is_farm_event(e)],
            "upcoming": [e for e in (s.get("upcoming_events") or []) if is_farm_event(e)],
            "ended": [e for e in (s.get("ended_events") or []) if is_farm_event(e)],
            "maa_stage_activity": s.get("maa_stage_activity") or {},
            "note": s.get("note") or ""}


# ---------------------------------------------------------------- 输入②：MAA 活动表
def fetch_maa_doc(url=MAA_URL, timeout=30):
    """拉 MAA 活动表（公开 JSON；绕代理）。失败抛 RefuseError。"""
    req = urllib.request.Request(url, headers={"User-Agent": "MAAOrch/1.0"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        raise RefuseError("拉取 MAA 活动表失败：%s: %s（可 --maa-json <本地快照> 离线）"
                          % (type(e).__name__, e))


def _real_stage(entry: dict) -> bool:
    """过滤「代理」伪条目/大小写异常值；保留 PA-8 / VEC-SP01 / SSReopen-AT 这类真关码。"""
    v = str((entry or {}).get("Value") or "").strip()
    drop = str((entry or {}).get("Drop") or "")
    if not v or "代理" in drop:
        return False
    if v != v.upper():
        return False
    return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9\-]{0,15}", v))


def maa_client_view(maa_doc, client, now):
    """某 client 段的：在期可刷段（含关码列表）+ 已过期段收集的关码（= 已关闭活动关）。"""
    sec = (maa_doc or {}).get(client) or {}
    farmable, closed, all_sections = [], set(), []
    for key, ent in (sec.get("sideStoryStage") or {}).items():
        act = (ent or {}).get("Activity") or {}
        tz_h = act.get("TimeZone")
        start = parse_ts(act.get("UtcStartTime"), tz_h)
        end = parse_ts(act.get("UtcExpireTime"), tz_h)
        stages = [st.get("Value") for st in (ent.get("Stages") or []) if _real_stage(st)]
        all_sections.append({"key": key, "name": act.get("Tip") or act.get("StageName") or key,
                             "start": iso(start), "end": iso(end), "stages": stages,
                             "expired": bool(end and as_local(end) <= now)})
        if end and as_local(end) > now:
            farmable.append({"section": key, "name": act.get("Tip") or act.get("StageName") or key,
                             "start": iso(start), "end": iso(end), "stages": stages})
        else:
            closed.update(stages)
    return {"client": client, "sections": all_sections, "farmable": farmable,
            "closed_stages": sorted(closed),
            "mini": [str((m or {}).get("Display") or (m or {}).get("Value") or "")
                     for m in (sec.get("miniGame") or [])],
            "has_section": client in (maa_doc or {})}


# ---------------------------------------------------------------- 输入③：账号范围（REST）
def norm_server(token: str):
    t = str(token or "").strip().lower()
    for k, v in SERVERS.items():
        if t == k or t in [str(a).lower() for a in v["aliases"]]:
            return k
    return None


def server_of_account(server_raw):
    """账号 Info.Server（Official/Bilibili/txwy/YoStarJP…）→ server key。"""
    s = str(server_raw or "").strip()
    if s in CLIENT2KEY:
        return CLIENT2KEY[s]
    return None


def select_scope(args, now):
    """读 REST：脚本 → 账号；按 --server/--accounts/--scripts/--exclude 过滤。
    返回 (accounts, meta)。accounts 每项含 7 键 + task + server_key。"""
    rp = _rp()
    scripts = rp.fetch_scripts()
    accs, skipped_scripts = [], []
    for sid, sc in sorted(scripts.items(), key=lambda kv: kv[1]["name"]):
        try:
            users = rp.fetch_users(sid)
        except Exception as e:  # noqa: BLE001
            skipped_scripts.append({"script": sc["name"], "error": "%s: %s" % (type(e).__name__, e)})
            continue
        for u in users:
            accs.append({"sid": sid, "script": sc["name"], "dir": sc["dir"], "emu": sc.get("emu"),
                         "uid": u["uid"], "name": u["name"], "server_raw": u.get("server"),
                         "server": server_of_account(u.get("server")),
                         "enabled": bool(u.get("status")) and str(u.get("remained_day")) != "0",
                         "remained_day": u.get("remained_day"), "mode": u.get("mode"),
                         "stage_mode": u.get("stage_mode"),
                         "stage": {k: (u.get("stage") or {}).get(k) for k in rp.STAGE_KEYS},
                         "task": dict(u.get("task") or {})})
    # 过滤
    if args.server:
        want = {x for x in (norm_server(t) for t in args.server) if x}
        unknown = [t for t in args.server if norm_server(t) is None]
        if unknown:
            raise RefuseError("--server 不认识：%s（可用：%s）" % ("、".join(unknown), "、".join(SERVERS)))
        accs = [a for a in accs if a["server"] in want]
    if args.scripts:
        want = {s.strip() for s in args.scripts if s.strip()}
        accs = [a for a in accs if a["script"] in want]
    if args.accounts:
        want = {s.strip() for s in args.accounts if s.strip()}
        accs = [a for a in accs if a["name"] in want]
    if args.exclude:
        drop = {s.strip() for s in args.exclude if s.strip()}
        accs = [a for a in accs if a["name"] not in drop]
    if args.server or args.scripts:
        # 也把没有归属服务器的号留个报告（只统计）
        pass
    meta = {"scripts_total": len(scripts), "skipped_scripts": skipped_scripts,
            "scope": {"server": list(args.server or []), "scripts": list(args.scripts or []),
                      "accounts": list(args.accounts or []), "exclude": list(args.exclude or [])},
            "explicit": bool(args.server or args.scripts or args.accounts)}
    return accs, meta


# ---------------------------------------------------------------- 能力台账
def cap_lookup(name, stage):
    row = None
    with contextlib.suppress(Exception):
        row = _scap().can_farm_detail(name, stage, CAP_JSON)
    return {"stage": stage, "status": (row or {}).get("status") or ST_UNKNOWN, "row": row}


# ---------------------------------------------------------------- 目标计划解析
def parse_stages_arg(s):
    out = [x.strip() for x in str(s or "").split(",") if x.strip()]
    if not (1 <= len(out) <= 4):
        raise RefuseError("--stages 应为 1–4 个关卡码（逗号分隔），实得 %r" % s)
    return out


def resolve_target(args, server_key, ev_view, maa_view):
    """目标活动关解析（fail-closed）。返回 {stages, source, forced, note} 或 None。"""
    if args.stages:
        return {"stages": parse_stages_arg(args.stages), "source": "cli(--stages)", "forced": True,
                "note": "显式指定：不依赖 MAA 表（表未收录也行）；请自行确认关码正确"}
    cfg = load_json(PLANS_CFG) or {}
    ent = (cfg.get("servers") or {}).get(server_key) or {}
    if ent and ent.get("stages"):
        return {"stages": [str(x) for x in ent["stages"]][:4], "source": "config/activity_switch_plans.json",
                "forced": bool(ent.get("force")), "note": ent.get("note") or ""}
    farm = (maa_view or {}).get("farmable") or []
    for f in farm:
        if f.get("stages"):
            return {"stages": f["stages"][:4], "source": "maa:%s" % f["section"],
                    "forced": False, "note": "%s（%s ~ %s）" % (f["name"], f["start"][:16], f["end"][:16])}
    if server_key == "cn_official":
        cfg2 = load_json(ACTIVITY_STAGES_CFG) or {}
        if cfg2.get("active"):
            cand = []
            for m in (cfg2.get("material") or [])[:3]:
                if m.get("stage"):
                    cand.append(m["stage"])
            if (cfg2.get("orundum") or {}).get("stage"):
                cand.append(cfg2["orundum"]["stage"])
            if cand:
                return {"stages": cand[:4], "source": "config/activity_stages.json（MAAOrch 检测器）",
                        "forced": False, "note": cfg2.get("zone_name") or ""}
    return None


def build_target_plan(target, usable):
    plan = list(usable[:4])
    if len(plan) < 4 and TAIL_STAGE not in plan:
        plan.append(TAIL_STAGE)
    return plan


def first_slot(stage_vals):
    for k in ("Stage", "Stage_1", "Stage_2", "Stage_3"):
        v = str((stage_vals or {}).get(k) or "-").strip()
        if v and v != "-":
            return v
    return None


def all_slots(stage_vals):
    return [str((stage_vals or {}).get(k) or "-").strip() for k in ("Stage", "Stage_1", "Stage_2", "Stage_3")]


# ---------------------------------------------------------------- 台账状态
def load_state() -> dict:
    st = load_json(STATE, None)
    if not isinstance(st, dict) or not isinstance(st.get("switches"), dict):
        return {"schema": "maaorch.activity_switch/1", "updated": "", "switches": {}}
    return st


def save_state(st: dict) -> None:
    st["updated"] = _now()
    save_json(STATE, st)


def open_records_for_account(st: dict, name: str) -> list:
    out = []
    for sid_, sw in (st.get("switches") or {}).items():
        if sw.get("status") in ("restored",):
            continue
        for a in sw.get("accounts") or []:
            if a.get("name") == name and a.get("status") == "switched":
                out.append((sid_, a))
    return out


def record_due(rec, now, maa_view):
    """我方切换记录是否该还原：活动 end 已过 or MAA 已不可刷（有证据才算）。"""
    end = parse_ts(rec.get("end")) if rec.get("end") else None
    if end is not None and now >= as_local(end):
        return True, "活动已结束（%s）" % fmt_d(as_local(end))
    stages = [s for s in (rec.get("stages") or []) if s != TAIL_STAGE]
    if stages and maa_view:
        farm_set = set()
        for f in maa_view.get("farmable") or []:
            farm_set.update(f.get("stages") or [])
        if not (set(stages) & farm_set):
            return True, "MAA 表已无该活动在期段（%s 不可刷）" % "、".join(stages)
    return False, "仍在其活动期"


# ---------------------------------------------------------------- 报告（dry-run / apply 共用）
def build_report(args, now):
    rep = {"tool": "activity_switch", "schema": "maaorch.activity_switch_plan/1",
           "generated_at": _now(), "apply": bool(args.apply and not args.pretend_now),
           "pretend_now": fmt_d(args.pretend_now) if args.pretend_now else None,
           "gates": [], "refusals": [], "warnings": [], "servers": {}, "accounts": [],
           "switches": [], "counts": {}, "notes": [], "artifacts": {}, "explicit_scope": False}
    # G4 证据新鲜
    ev_doc = load_events(EVENTS)
    ev_fresh = events_freshness(ev_doc, now)
    rep["events"] = {"path": EVENTS, **ev_fresh}
    if ev_fresh["age_days"] is None:
        rep["warnings"].append("server_events 无时间戳（age 未知）——切换判定不可靠")
    elif not ev_fresh["ok"]:
        rep["warnings"].append("server_events 快照 %s 天前（>%.0f 天：过期）——本次拒绝据此切换"
                               % (ev_fresh["age_days"], EVENTS_FAIL_DAYS))
    elif ev_fresh["warn"]:
        rep["warnings"].append("server_events 快照 %.1f 天前（>%.0f 天告警）；引用前按 docs/SERVER_EVENTS.md §8 重查"
                               % (ev_fresh["age_days"], EVENTS_WARN_DAYS))
    if not ev_fresh["ok"]:
        rep["refusals"].append({"gate": "G4", "reason": "events_stale",
                                "detail": "server_events 快照 %.1f 天前（>%.0f 天）：拒绝据此切换（可用 --events-file）"
                                          % (ev_fresh["age_days"] or -1, EVENTS_FAIL_DAYS)})
    # MAA 表
    maa_doc, maa_meta = None, {"source": None, "error": None}
    try:
        if args.maa_json:
            maa_doc = load_json(args.maa_json)
            if not isinstance(maa_doc, dict):
                raise RefuseError("--maa-json 不可读：%s" % args.maa_json)
            maa_meta = {"source": args.maa_json, "fetched_at": _now()}
        else:
            maa_doc = fetch_maa_doc()
            maa_meta = {"source": MAA_URL, "fetched_at": _now()}
    except RefuseError as e:
        maa_meta["error"] = str(e)
        rep["warnings"].append(str(e))
    rep["maa"] = maa_meta
    # 各服视图（只算范围里出现的服 + 我台账里有的服）
    st = load_state()
    my_servers = set()
    for sw in (st.get("switches") or {}).values():
        if sw.get("status") != "restored":
            my_servers.add(sw.get("server"))
    scope_servers = set()
    for a in (args.server or []):
        k = norm_server(a)
        if k:
            scope_servers.add(k)
    servers_to_show = sorted([s for s in set(list(scope_servers) + list(my_servers)) if s] or list(SERVERS))
    for k in servers_to_show:
        ev_v = events_server_view(ev_doc, k)
        client = SERVERS[k]["client"]
        mv = maa_client_view(maa_doc or {}, client, now) if maa_doc else None
        if mv is None and SERVERS[k].get("maafallback") and maa_doc:
            mv = maa_client_view(maa_doc or {}, SERVERS[k]["maafallback"], now)
            mv["client"] = client
            mv["fallback_from"] = SERVERS[k]["maafallback"]
        verdict, why = "unknown", ""
        if mv is None:
            verdict, why = "maa_unavailable", "MAA 活动表不可用（网络/快照缺失）"
        elif mv["farmable"]:
            verdict, why = "switchable", "MAA 表在期活动关：%s" % "、".join(
                s for f in mv["farmable"] for s in f["stages"])[:120]
        else:
            up = "；待开：%s" % "、".join(e.get("name") or "" for e in ev_v["upcoming"][:2]) if ev_v["upcoming"] else ""
            verdict, why = "no_farmable", "MAA 表该段无在期活动关%s" % up
        rep["servers"][k] = {"name": SERVERS[k]["name"], "client": client, "verdict": verdict, "why": why,
                             "events": {"in_period": ev_v["in_period"], "verdict": ev_v["verdict"],
                                        "active": [e.get("name") for e in ev_v["active"]],
                                        "upcoming": [{"name": e.get("name"), "start": e.get("start_bjt"),
                                                      "end": e.get("end_bjt")} for e in ev_v["upcoming"]],
                                        "ended": [e.get("name") for e in ev_v["ended"]][:6],
                                        "maa_stage_activity": ev_v["maa_stage_activity"]},
                             "maa": ({"farmable": mv["farmable"], "closed_stages": mv["closed_stages"],
                                      "sections": mv["sections"], "mini": mv["mini"],
                                      "fallback_from": mv.get("fallback_from")} if mv else None)}
    # 范围
    accounts, meta = select_scope(args, now)
    rep["scope_meta"] = {k: v for k, v in meta.items() if k != "skipped_scripts"}
    rep["explicit_scope"] = meta["explicit"]
    if meta["skipped_scripts"]:
        rep["warnings"].append("有 %d 个脚本读账号失败：%s" % (
            len(meta["skipped_scripts"]), "、".join(s["script"] for s in meta["skipped_scripts"][:5])))
    # apply 天条（G0/G2/G3）：dry-run 报告也展示「若要 apply 缺什么」；执行侧由 cmd_apply/do_apply 统一拦截
    if args.apply:
        if any(str(os.environ.get(e, "")).strip().lower() in ("1", "true", "yes", "on") for e in NO_GO_ENVS):
            rep["refusals"].append({"gate": "G0", "reason": "no_go",
                                    "detail": "NO_GO 置位（%s）" % "/".join(NO_GO_ENVS)})
        if not rep["explicit_scope"]:
            rep["refusals"].append({"gate": "G2", "reason": "need_explicit_scope",
                                    "detail": "apply 必须显式给范围：--server / --accounts / --scripts"})
        if not (getattr(args, "approved_by", None) or "").strip():
            rep["refusals"].append({"gate": "G3", "reason": "need_approval",
                                    "detail": "apply 需要 --approved-by <谁/依据>（审批闸门）"})
    # 逐号判定
    rp = _rp()
    plans = {}
    with contextlib.suppress(Exception):
        plans = rp.fetch_plans()
    for a in accounts:
        srv = rep["servers"].get(a["server"]) or {}
        entry = {"name": a["name"], "script": a["script"], "sid": a["sid"], "uid": a["uid"],
                 "dir": a["dir"],
                 "server": a["server"], "server_raw": a["server_raw"], "enabled": a["enabled"],
                 "stage_mode": a["stage_mode"], "slots": all_slots(a["stage"]),
                 "first": first_slot(a["stage"]), "action": "noop", "reason": "", "target": None,
                 "cap": {}, "probe_stage": None, "record": None, "heal_to": None,
                 "remain": str((a["stage"] or {}).get("Stage_Remain") or "-")}
        entry["base_source"] = None
        if not a["server"]:
            entry.update(action="skip", reason="账号 Server 字段不认识（%r）" % a["server_raw"])
        elif not a["enabled"]:
            entry.update(action="skip", reason="未启用/RemainedDay=0（AutoProxy 会跳过）")
        else:
            # 生效值（供全量复制）
            base, src = None, None
            with contextlib.suppress(Exception):
                base, src = rp.account_effective({"name": a["name"], "stage_mode": a["stage_mode"],
                                                  "stage": a["stage"]}, plans, now)
            entry["base_source"] = src
            entry["base"] = base
            # ① 我方历史记录（优先：未还原；StageMode 未指向临时表 = 记录失效，忽略后按现况重判）
            recs = [(rid_, r_) for rid_, r_ in open_records_for_account(load_state(), a["name"])
                    if str(a["stage_mode"]) == str((r_.get("temp_plan") or {}).get("plan_id"))]
            if recs:
                dead = [(rid_, r_) for rid_, r_ in recs
                        if r_.get("server") == a["server"] or not a["server"]]
                rid_, rec = (dead or recs)[0]
                mv = (rep["servers"].get(a["server"]) or {}).get("maa")
                due, why = record_due(rec, now, mv)
                if due and rec.get("temp_plan", {}).get("plan_id"):
                    entry.update(action="restore", reason="还原我方切换 %s：%s" % (rid_, why),
                                 record={"switch_id": rid_, **{k: rec.get(k) for k in
                                         ("uid", "sid", "script", "temp_plan", "prev_stage_mode", "stages")}})
                else:
                    entry.update(action="already", reason="已在我方切换记录 %s 中（%s）" % (rid_, why))
            else:
                tgt = resolve_target(args, a["server"], rep["servers"].get(a["server"], {}).get("events") or {},
                                     srv.get("maa")) or None
                mv = srv.get("maa")
                if not ev_fresh["ok"]:
                    entry.update(action="skip",
                                 reason="server_events 快照不可用/过期（%s 天）⇒ 拒绝据此切换（fail-closed）"
                                        % ("-" if ev_fresh["age_days"] is None else ev_fresh["age_days"]))
                    tgt = None
                elif tgt is None or not tgt.get("forced"):
                    if mv is None:
                        entry.update(action="skip", reason="MAA 活动表不可用 ⇒ 不可切（fail-closed）")
                    elif not mv.get("farmable"):
                        entry.update(action="skip", reason="该服 MAA 表无在期活动关（不可切/fail-closed）")
                if entry["action"] in ("noop",) and tgt:
                    stages = [s for s in tgt["stages"] if s]
                    cap = {s: cap_lookup(a["name"], s)["status"] for s in stages}
                    entry["cap"] = {k: v for k, v in cap.items()}
                    usable = [s for s in stages if cap.get(s) != ST_FAILED]
                    dropped = [s for s in stages if cap.get(s) == ST_FAILED]
                    if not usable:
                        entry.update(action="skip", reason="目标关全部台账=failed（%s）" % "、".join(stages))
                    else:
                        plan = build_target_plan(tgt, usable)
                        entry["target"] = {"stages": plan, "source": tgt["source"],
                                           "note": tgt.get("note") or "", "dropped_failed": dropped}
                        first_t = usable[0]
                        cap0 = cap.get(first_t, ST_UNKNOWN)
                        if entry["first"] == first_t:
                            entry.update(action="already", reason="当前计划首位已是 %s（无需改）" % first_t)
                        elif cap0 == ST_OK:
                            entry.update(action="switch", reason="台账 success（%s）⇒ 直切" % first_t)
                        elif cap0 == ST_UNKNOWN:
                            entry.update(action="probe", reason="台账 unknown（%s）⇒ 先试探一次再切" % first_t,
                                         probe_stage=first_t)
                        else:
                            entry.update(action="skip", reason="首位 %s 台账=%s" % (first_t, cap0))
            # ② 收尾（heal）候选：全部非空位都是已关闭活动关（如 SR-7）；
            #    「不可切（fail-closed）/无在期活动关」等 skip 也可收尾——只要号在跑且 MAA 表有依据
            if (entry["action"] in ("noop", "skip") and args.heal_stale and srv.get("maa")
                    and entry.get("enabled") and entry.get("server")):
                closed = set(srv["maa"].get("closed_stages") or [])
                slots = [s for s in entry["slots"] if s not in ("-", "")]
                if slots and closed and all(s in closed for s in slots):
                    heal_to = args.heal_to or (entry["remain"] if entry["remain"] not in ("-", "") else HEAL_DEFAULT)
                    entry.update(action="heal", heal_to=heal_to,
                                 reason="计划全部位都是已关闭活动关（%s）⇒ 整段会被 MAA 跳过；收尾切到 %s"
                                        % ("、".join(slots), heal_to))
        rep["accounts"].append(entry)
    # 历史切换汇总（还原计划）
    for sid_, sw in sorted((load_state().get("switches") or {}).items()):
        if sw.get("status") == "restored":
            continue
        recs = [a for a in sw.get("accounts") or [] if a.get("status") == "switched"]
        if recs:
            mv = (rep["servers"].get(sw.get("server")) or {}).get("maa")
            due, why = record_due({"end": (sw.get("activity") or {}).get("end"),
                                   "stages": sw.get("stages") or []}, now, mv)
            rep["switches"].append({"switch_id": sid_, "server": sw.get("server"),
                                    "activity": (sw.get("activity") or {}).get("name"),
                                    "ts": sw.get("ts"), "accounts": [a.get("name") for a in recs],
                                    "due": due, "why": why})
    cnt = {}
    for e in rep["accounts"]:
        cnt[e["action"]] = cnt.get(e["action"], 0) + 1
    for s in rep["switches"]:
        if s["due"]:
            cnt["restore_pending"] = cnt.get("restore_pending", 0) + len(s["accounts"])
    rep["counts"] = cnt
    return rep


def render_report_md(rep) -> str:
    L = []
    L.append("# 活动期计划切换 · %s" % ("APPLY" if rep["apply"] else "DRY-RUN"))
    L.append("")
    L.append("> 生成 %s%s ｜ 触发源 `runtime/reports/server_events.json`（快照 %s，%.1f 天前）｜ MAA 表 %s"
             % (rep["generated_at"], (" ｜ 预演时刻 " + rep["pretend_now"]) if rep.get("pretend_now") else "",
                rep["events"]["generated_at"], rep["events"]["age_days"] or -1,
                rep["maa"].get("source") or "不可用"))
    L.append("")
    L.append("## 0. 结论")
    L.append("")
    cnt = rep["counts"]
    L.append("会改：**switch %d** ｜ probe(需先试探) %d ｜ heal(收尾) %d ｜ restore(切回原计划) %d ｜ "
             "already/skip/noop %d"
             % (cnt.get("switch", 0), cnt.get("probe", 0), cnt.get("heal", 0),
                cnt.get("restore", 0), cnt.get("skip", 0) + cnt.get("already", 0) + cnt.get("noop", 0)))
    for r in rep["refusals"]:
        L.append("- ✗ 拒绝 [%s %s] %s" % (r["gate"], r["reason"], r["detail"]))
    for w in rep["warnings"]:
        L.append("- ⚠ %s" % w)
    L.append("")
    L.append("## 1. 各服判定（触发 + 可刷层）")
    L.append("")
    L.append("| 服 | client | 裁决 | events 在期活动 | MAA 在期活动关 | 说明 |")
    L.append("|---|---|---|---|---|---|")
    for k, s in rep["servers"].items():
        stages = "、".join(x for f in (s.get("maa") or {}).get("farmable", []) for x in f["stages"]) or "—"
        L.append("| %s | %s | **%s** | %s | %s | %s |"
                 % (s["name"], s["client"], s["verdict"],
                    "、".join(s["events"]["active"]) or "—", stages, s["why"][:60]))
    L.append("")
    for k, s in rep["servers"].items():
        up = s["events"]["upcoming"]
        if up:
            L.append("- %s 待开：%s" % (s["name"], "；".join("%s（%s 起）" % (u["name"], u["start"]) for u in up)))
        ended = s["events"]["ended"]
        if ended:
            L.append("- %s 近期已结束（刷关型）：%s" % (s["name"], "、".join(ended)))
        if (s.get("maa") or {}).get("closed_stages"):
            L.append("- %s MAA 已关闭活动关（可判别收尾）：%s"
                     % (s["name"], "、".join(s["maa"]["closed_stages"][:16])))
    L.append("")
    L.append("## 2. 账号动作清单")
    L.append("")
    L.append("| 服 | 账号 | 脚本 | 当前计划 | 动作 | 目标/说明 |")
    L.append("|---|---|---|---|---|---|")
    order = {"switch": 0, "heal": 1, "probe": 2, "restore": 3, "already": 4, "skip": 5, "noop": 6}
    for e in sorted(rep["accounts"], key=lambda x: (order.get(x["action"], 9), x["server"] or "", x["name"])):
        cur = "[" + " ".join(e["slots"]) + "]"
        tgt = ""
        if e.get("target"):
            tgt = "[%s]（%s）" % (" ".join(e["target"]["stages"]), e["target"]["source"])
        elif e["action"] == "heal":
            tgt = "→ %s" % e["heal_to"]
        L.append("| %s | %s | %s | %s | **%s** | %s |"
                 % ((e["server"] or "?"), e["name"], e["script"], cur, e["action"],
                    (tgt + " ｜ " + e["reason"]) if tgt else e["reason"]))
    L.append("")
    L.append("## 3. 依据 → 结论 链")
    L.append("")
    L.append("1. 触发：server_events 快照 %s（%.1f 天）⇒ %s" % (
        rep["events"]["generated_at"], rep["events"]["age_days"] or -1,
        "可用" if rep["events"]["ok"] else "**过期，拒绝切换**"))
    L.append("2. 可刷层（权威=MAA 表）：%s ⇒ 只有该服段有未过期 sideStoryStage 才切（fail-closed）"
             % (rep["maa"].get("source") or "不可用: %s" % rep["maa"].get("error")))
    L.append("3. 能力台账：%s（success 直切 / unknown 先试探 / failed 剔除）"
             % CAP_JSON)
    L.append("4. 写面：新建「%s-*」临时计划表（全量复制当日生效值 + 只差分期关卡位）+ StageMode 指针；"
             "存量计划表与账号自己的 7 键不碰" % ACT_PLAN_PREFIX)
    L.append("5. 还原：StageMode 回写备份值 + 删临时表（`--restore <id>`；活动结束自动纳入计划）；"
             "跑中「配置锁定」⇒ 跳过不硬来")
    L.append("")
    if rep["switches"]:
        L.append("## 4. 我方历史切换（未还原）")
        L.append("")
        for s in rep["switches"]:
            L.append("- `%s` %s %s（%s）：%d 号；%s %s"
                     % (s["switch_id"], s["server"], s["activity"], s["ts"], len(s["accounts"]),
                        "**应还原**" if s["due"] else "仍在其期", s["why"]))
        L.append("")
    L.append("## 5. 执行")
    L.append("")
    if rep["apply"]:
        L.append("本次为 APPLY —— 见 runtime/mas_activity_switch_log.jsonl 与 activity_switch_state.json。")
    else:
        L.append("```bash")
        scope = " ".join(["--server " + ",".join(rep["scope_meta"]["scope"]["server"])] if
                         rep["scope_meta"]["scope"]["server"] else [])
        L.append("python tools/activity_switch.py %s --apply --approved-by <批准人>" % scope)
        L.append("```")
    return "\n".join(L)


def report_counts_line(rep) -> str:
    c = rep["counts"]
    return ("switch=%d probe=%d heal=%d restore=%d skip=%d already=%d"
            % (c.get("switch", 0), c.get("probe", 0), c.get("heal", 0), c.get("restore", 0),
               c.get("skip", 0), c.get("already", 0)))


# ---------------------------------------------------------------- 写面（复用 run_profile 骨架）
def temp_plan_name(existing, tag, stamp, n):
    while True:
        name = "%s-%s-%s" % (ACT_PLAN_PREFIX, tag, stamp) if n == 1 else \
               "%s-%s-%s-%d" % (ACT_PLAN_PREFIX, tag, stamp, n)
        if name not in existing:
            return name, n
        n += 1


def snapshot_backup(run, plan_like):
    """复用 run_profile.backup_snapshot：落 meta/plans_before/accounts_before/expected + sha256。"""
    return _rp().backup_snapshot(run, plan_like)


def do_switch_write(acct, payload, tag, now, stale_names=None):
    """建临时计划表 + StageMode 指针 + 读回；失败清理。返回 dict。"""
    rp = _rp()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    existing = set(stale_names or [])
    if stale_names is None:
        with contextlib.suppress(Exception):
            existing = {str((v.get("Info") or {}).get("Name") or "") for v in rp.fetch_plans().values()}
    name, _ = temp_plan_name(existing, tag, stamp, 1)
    pid = rp.plan_add("MaaPlan")
    rp.plan_update(pid, {"Info": {"Name": name, "Mode": "ALL"}, "ALL": dict(payload)})
    got = rp.fetch_plan(pid) or {}
    got_all = got.get("ALL") or {}
    diffs = {k: (got_all.get(k), payload.get(k)) for k in rp.STAGE_KEYS
             if str(got_all.get(k)) != str(payload.get(k))}
    if diffs:
        with contextlib.suppress(Exception):
            rp.plan_delete(pid)
        return {"ok": False, "plan_id": None, "name": name, "busy": False,
                "error": "plan/update 后读回不符：%s" % json.dumps(diffs, ensure_ascii=False)}
    ok, msg = rp.user_update(acct["sid"], acct["uid"], {"Info": {"StageMode": pid}})
    if not ok:
        with contextlib.suppress(Exception):
            rp.plan_delete(pid)
        busy = ("锁定" in str(msg)) or ("运行" in str(msg)) or ("lock" in str(msg).lower())
        return {"ok": False, "plan_id": None, "name": name, "busy": busy,
                "error": "写 StageMode 被拒：%s" % msg}
    u = rp.fetch_user(acct["sid"], acct["uid"]) or {}
    got_sm = str((u.get("Info") or {}).get("StageMode"))
    if got_sm != str(pid):
        with contextlib.suppress(Exception):
            rp.user_update(acct["sid"], acct["uid"], {"Info": {"StageMode": acct["stage_mode"]}})
        with contextlib.suppress(Exception):
            rp.plan_delete(pid)
        return {"ok": False, "plan_id": None, "name": name, "busy": False,
                "error": "StageMode 读回不符：%s≠%s（已回滚）" % (got_sm, pid)}
    return {"ok": True, "plan_id": pid, "name": name, "payload": dict(payload)}


def do_restore_account(rec, attempts=6, gap=20) -> tuple:
    """还原单个账号记录（幂等）。"""
    rp = _rp()
    sid, uid = rec["sid"], rec["uid"]
    prev = rec.get("prev_stage_mode")
    pid = (rec.get("temp_plan") or {}).get("plan_id")
    last = ""
    for i in range(1, max(1, attempts) + 1):
        pending = []
        cur = rp.fetch_user(sid, uid)
        if cur is None:
            pending.append("读账号失败")
        else:
            info = cur.get("Info") or {}
            cur_sm = str(info.get("StageMode"))
            if pid and cur_sm == str(pid):
                ok, msg = rp.user_update(sid, uid, {"Info": {"StageMode": prev}})
                if not ok:
                    pending.append("回写 StageMode：%s" % msg)
            elif pid and prev is not None and cur_sm != str(prev):
                # 他方已改：不覆盖
                record_note = "StageMode 已被他方改动（%s），不覆盖" % cur_sm
        if pid and not pending:
            with contextlib.suppress(Exception):
                if rp.fetch_plan(pid) is not None:
                    ok, msg = rp.plan_delete(pid)
                    if not ok and ("不存在" not in str(msg)):
                        pending.append("删临时计划表：%s" % msg)
        if not pending:
            chk = rp.fetch_user(sid, uid) or {}
            got = str((chk.get("Info") or {}).get("StageMode"))
            if pid and prev is not None and got != str(prev):
                pending.append("回读 StageMode=%s≠%s" % (got, prev))
            elif pid and rp.fetch_plan(pid) is not None:
                pending.append("临时计划表仍在")
        if not pending:
            return True, "已还原（%d 次尝试）" % i
        last = "；".join(map(str, pending))
        if i < attempts:
            _SLEEP(max(1, gap))
    return False, "还原未完成：%s" % last[:300]


# ---------------------------------------------------------------- apply
def _run_probe_cmd(cmd, timeout):
    """跑 mas_probe_farm.py（selftest 里替换）。返回 (rc, tail text)。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                           errors="replace")
        return p.returncode, ((p.stdout or "") + (p.stderr or ""))[-400:]
    except subprocess.TimeoutExpired:
        return 124, "probe 超时"
    except Exception as e:  # noqa: BLE001
        return 125, "%s: %s" % (type(e).__name__, e)


def do_apply(rep, args, now) -> int:
    rp = _rp()
    if not rep["explicit_scope"]:
        rep["refusals"].append({"gate": "G2", "reason": "need_explicit_scope",
                                "detail": "apply 必须显式给范围：--server / --accounts / --scripts"})
    if not (args.approved_by or "").strip():
        rep["refusals"].append({"gate": "G3", "reason": "need_approval",
                                "detail": "apply 需要 --approved-by <谁/依据>（审批闸门）"})
    if any(str(os.environ.get(e, "")).strip().lower() in ("1", "true", "yes", "on") for e in NO_GO_ENVS):
        rep["refusals"].append({"gate": "G0", "reason": "no_go", "detail": "NO_GO 置位（%s）" % "/".join(NO_GO_ENVS)})
    with contextlib.suppress(Exception):
        ok, why = rp.reachable()
        if not ok:
            rep["refusals"].append({"gate": "G1", "reason": "rest_unreachable", "detail": why})
    for r in rep["refusals"]:
        if r["gate"] in ("G0", "G1", "G2", "G3", "G4"):
            log("✗ 拒绝 [%s %s] %s" % (r["gate"], r["reason"], r["detail"]))
    if any(r.get("gate") in ("G0", "G1", "G2", "G3", "G4") for r in rep["refusals"]):
        rep["done"] = True                     # 有闸门拒绝：零写返回（不取锁、不写任何东西）
        return 2
    rc, sw = 0, None
    try:
        with acquire_lock():
            rc = _apply_locked(rep, args, now)
    except LockBusy as e:
        log("✗ %s" % e)
        rep["refusals"].append({"gate": "G5", "reason": "lock_busy", "detail": str(e)})
        rc = 2
    except Exception as e:  # noqa: BLE001 —— 任何中断都不静默：记忆 + 审计 + rc=4（人工核残留）
        log("✗ 执行中断：%s: %s" % (type(e).__name__, e))
        rep["warnings"].append("apply 中断：%s: %s（请用 --status/--list-plans 核对残留）"
                               % (type(e).__name__, str(e)[:200]))
        audit({"event": "apply_crashed", "error": "%s: %s" % (type(e).__name__, str(e)[:300])})
        rc = 4
    rep["done"] = True
    return rc


def _apply_locked(rep, args, now) -> int:
    rp = _rp()
    # 逐服聚合执行（switch/heal → 一个 switch_id；restore 按记录）
    to_switch = [e for e in rep["accounts"] if e["action"] in ("switch", "heal", "probe")]
    to_restore = [e for e in rep["accounts"] if e["action"] == "restore"]
    busy_dirs, probe_rc = set(), 0
    # G6 班次运行中不动：一次 ssh 探所有相关 MAA 目录（探不到=告警后仍走，锁由 AUTO-MAS 兜底）
    dirmap = {}
    for e in to_switch + to_restore:
        if e.get("dir"):
            dirmap[e["sid"]] = e["dir"]
    dir_paths = sorted(set(dirmap.values()))
    if dir_paths:
        try:
            pr = rp.host_probe(None, dir_paths)
            want = {rp.dkey(d) for d in dir_paths}
            for p in (pr.get("maa_dirs") or []):
                k = rp.dkey(os.path.dirname(str(p)))
                if k in want:
                    busy_dirs.add(k)
        except Exception as e:  # noqa: BLE001
            rep["warnings"].append("班次占用探测失败（%s）：仍继续（写被锁时 AUTO-MAS 会拒绝，零残留）"
                                   % str(e)[:120])

    def busy_for(e):
        d = dirmap.get(e.get("sid"))
        return bool(d and rp.dkey(d) in busy_dirs)
    # 目标准备：切/收尾
    results = {"switch": [], "heal": [], "probe": [], "restore": [], "skip": []}
    plan_like_accounts, temp_plans, expected = [], [], {}
    tag_by_uid = {}
    for e in to_switch:
        if busy_for(e):
            e["result"] = "skip_busy"
            e["reason"] += "（班次运行中：MAA.exe 占用 %s）" % dirmap.get(e.get("sid"))
            results["skip"].append(e)
            continue
        tag = (SERVERS.get(e["server"]) or {}).get("tag") or "X"
        if e["action"] == "probe" and not args.probe_unknown:
            e["result"] = "skip_need_probe"
            cmd = ("python tools/mas_probe_farm.py --account %s --stage %s --apply --probe-only --script %s"
                   % (e["name"], e["probe_stage"], e["script"]))
            e["reason"] += "（未加 --probe-unknown；可先手动：%s）" % cmd
            results["probe"].append(e)
            continue
        if e["action"] == "probe" and args.probe_unknown:
            if args.max_probes and len([x for x in results["probe"] if x.get("probed")]) >= args.max_probes:
                e["result"] = "skip_probe_limit"
                e["reason"] += "（--max-probes 已达上限）"
                results["skip"].append(e)
                continue
            cmd = [sys.executable, PROBE_FARM, "--account", e["name"], "--stage", e["probe_stage"],
                   "--apply", "--probe-only", "--script", e["sid"]]
            if args.probe_disable_others:
                cmd.append("--disable-others")
            else:
                cmd.append("--all-accounts")
            log("试探 %s × %s …" % (e["name"], e["probe_stage"]))
            _touch_lock()
            prc, tail = _run_probe_cmd(cmd, args.probe_timeout_max)
            e["probed"], e["probe_rc"], e["probe_tail"] = True, prc, tail
            probe_rc = max(probe_rc, 0 if prc == 0 else 1)
            if prc != 0:
                e["result"] = "probe_failed"
                e["reason"] += "（试探未证明可刷：rc=%d）" % prc
                results["probe"].append(e)
                audit({"event": "probe_result", "account": e["name"], "stage": e["probe_stage"],
                       "rc": prc, "tail": tail[:200]})
                continue
            cap = cap_lookup(e["name"], e["probe_stage"])
            if cap["status"] != ST_OK:
                e["result"] = "probe_inconclusive"
                e["reason"] += "（试探后台账=%s，仍不切）" % cap["status"]
                results["probe"].append(e)
                continue
            e["reason"] += "（试探成功，台账已证）"
            e["action"] = "switch"
        # 组装写面
        payload = None
        if e["action"] == "heal":
            payload = build_stage_payload_for(e, [e["heal_to"]])
        else:
            payload = build_stage_payload_for(e, e["target"]["stages"])
        e["_payload"] = payload
        tag_by_uid[e["uid"]] = (tag if e["action"] != "heal" else "回退")
        plan_like_accounts.append({"uid": e["uid"], "name": e["name"], "will_disable": False})
        temp_plans.append({"uid": e["uid"], "name": None, "payload": payload})
        expected[e["uid"]] = {"payload": payload}
    # 一次备份（全部涉及账号）
    switch_ids = {}
    if plan_like_accounts:
        stamp = now.strftime(STAMP_FMT)
        sids = sorted({e["sid"] for e in to_switch if e.get("_payload")})
        sw_id = "asw_%s_%s" % (stamp, (rep["scope_meta"]["scope"]["server"] or ["fleet"])[0]
                               if rep["scope_meta"]["scope"]["server"] else "fleet")
        st = load_state()
        n = 2
        base_id = sw_id
        while sw_id in (st.get("switches") or {}):
            sw_id = "%s-%d" % (base_id, n)
            n += 1
        # 备份（复用 run_profile 骨架）
        script0 = next((a for a in rep["accounts"] if a["uid"] == plan_like_accounts[0]["uid"]), None) or {}
        run_like = {"run_id": sw_id, "backup_dir": os.path.join(BACKUPS, sw_id)}
        plan_like = {"script": {"sid": sids[0], "name": script0.get("script") or "?", "dir": ""},
                     "write_mode": "plan", "accounts": plan_like_accounts, "expected": expected,
                     "profile": {"tool": "activity_switch", "apply": True}}
        try:
            bk = snapshot_backup(run_like, plan_like)
            log("备份完成：%s" % bk["dir"])
        except Exception as e:  # noqa: BLE001
            log("✗ 备份失败，拒绝写入：%s" % e)
            audit({"event": "backup_failed", "error": str(e)[:300], "switch_id": sw_id})
            for e in to_switch:
                if e.get("_payload"):
                    e["result"] = "backup_failed"
                    e["reason"] += "（备份失败，未写）"
                    results["skip"].append(e)
            return 3
        sw = {"switch_id": sw_id, "ts": _now(), "apply": True, "approved_by": args.approved_by,
              "server": (rep["scope_meta"]["scope"]["server"] or ["fleet"])[0]
              if rep["scope_meta"]["scope"]["server"] else "fleet",
              "activity": None, "stages": [], "backup_dir": run_like["backup_dir"],
              "status": "switching", "accounts": []}
        sv = rep["servers"].get(sw["server"]) or {}
        if sv.get("maa", {}).get("farmable"):
            f0 = sv["maa"]["farmable"][0]
            sw["activity"] = {"name": f0["name"], "start": f0["start"], "end": f0["end"],
                              "source": "maa:%s" % f0["section"]}
            sw["stages"] = f0["stages"]
        (st.setdefault("switches", {}))[sw_id] = sw
        save_state(st)
        switch_ids[sw_id] = sw
        # 写
        for e in to_switch:
            if not e.get("_payload"):
                continue
            acct = {"sid": e["sid"], "uid": e["uid"], "stage_mode": e["stage_mode"]}
            _touch_lock()
            try:
                res = do_switch_write(acct, e["_payload"], tag_by_uid.get(e["uid"], "X"), now)
            except Exception as ex:  # noqa: BLE001 —— 单号写面异常不拖垮整批（其余照走，记录 skip）
                res = {"ok": False, "busy": False, "plan_id": None,
                       "error": "%s: %s" % (type(ex).__name__, str(ex)[:160])}
            e["result"] = "written" if res["ok"] else ("skip_busy" if res.get("busy") else "write_failed")
            if not res["ok"]:
                e["reason"] += "（%s）" % res["error"]
                results["skip"].append(e)
                audit({"event": "write_failed", "switch_id": sw_id, "account": e["name"],
                       "error": res["error"][:200]})
                continue
            rec = {"uid": e["uid"], "name": e["name"], "sid": e["sid"], "script": e["script"],
                   "prev_stage_mode": e["stage_mode"], "prev_stage_keys": e.get("base") or e.get("_base_keys"),
                   "temp_plan": {"plan_id": res["plan_id"], "name": res["name"], "payload": res["payload"]},
                   "stages": e["_payload"].get("Stage") and [str(e["_payload"].get(k)) for k in
                                                             ("Stage", "Stage_1", "Stage_2", "Stage_3")] or [],
                   # heal（收尾到 1-7）是终态：留档 "healed" 不参与还原（还原会把它推回已关闭活动关）
                   "status": ("healed" if e["action"] == "heal" else "switched"), "ts": _now()}
            sw["accounts"].append(rec)
            save_state(st)
            audit({"event": "switched", "switch_id": sw_id, "account": e["name"],
                   "plan_id": res["plan_id"], "plan_name": res["name"], "to": res["payload"]})
            (results["heal"] if e["action"] == "heal" else results["switch"]).append(e)
        sw["status"] = ("switched" if any(x.get("status") == "switched" for x in sw["accounts"])
                        else ("healed" if sw["accounts"] else "noop"))
        save_state(st)
    # 还原（due 的记录）
    for e in to_restore:
        rec_ref = e.get("record") or {}
        rid_ = rec_ref.get("switch_id")
        st = load_state()
        sw = (st.get("switches") or {}).get(rid_)
        if not sw:
            e["result"] = "restore_missing_record"
            e["reason"] += "（找不到记录 %s）" % rid_
            results["skip"].append(e)
            continue
        rec = next((a for a in sw.get("accounts") or []
                    if a.get("name") == e["name"] and a.get("status") == "switched"), None)
        if rec is None:
            e["result"] = "restore_done"
            continue
        if busy_for(e):
            e["result"] = "skip_busy"
            e["reason"] += "（班次运行中：稍后重试）"
            results["skip"].append(e)
            continue
        ok, detail = do_restore_account(rec, attempts=args.restore_attempts,
                                        gap=args.restore_gap_seconds)
        if ok:
            rec["status"] = "restored"
            rec["restored_at"] = _now()
            e["result"] = "restore_done"
        else:
            rec["status"] = "restore_failed"
            rec["restore_error"] = detail[:300]
            e["result"] = "restore_failed"
            e["reason"] += "（%s）" % detail
        if all((x.get("status") in ("restored", "restore_failed")) for x in (sw.get("accounts") or [])):
            sw["status"] = "restore_failed" if any(x.get("status") == "restore_failed"
                                                   for x in sw["accounts"]) else "restored"
            sw["restored_at"] = _now()
        save_state(st)
        audit({"event": "restore_account", "switch_id": rid_, "account": e["name"], "ok": ok,
               "detail": detail[:200]})
        results["restore"].append(e)
    rep["apply_results"] = {k: [{"name": x["name"], "result": x.get("result"), "reason": x.get("reason", "")[:200]}
                                for x in v] for k, v in results.items()}
    # rc
    rc = 0
    probe_unproven = any(str(x.get("result") or "") in ("skip_need_probe", "probe_failed", "probe_inconclusive")
                         for x in results["probe"])
    if results["skip"] or probe_unproven or probe_rc:
        rc = max(rc, 3)
    if any(x.get("result") == "restore_failed" for x in results["restore"]):
        rc = 4
    counts = {k: len(v) for k, v in results.items()}
    rep["counts"]["applied"] = counts
    ledger_append({"kind": "activity_switch", "ok": rc in (0,), "scope": json.dumps(
        {"server": rep["scope_meta"]["scope"]["server"], "accounts": len(rep["accounts"])}, ensure_ascii=False),
        "detail": "apply rc=%d %s approved_by=%s" % (rc, json.dumps(counts, ensure_ascii=False),
                                                     args.approved_by)[:300]})
    return rc


def build_stage_payload_for(entry, stages):
    """全量复制该号当日生效值 + 只差分期关卡位（复用 run_profile.build_stage_payload 口径）。"""
    rp = _rp()
    base = entry.get("base") or {}
    if not base:
        base = {k: ("0" if k == "SeriesNumb" else ("-" if k != "MedicineNumb" else 0)) for k in rp.STAGE_KEYS}
    norm = {"stage_plan": stages, "series": None, "medicine": None, "activity_priority": None,
            "keep_remain_sanity_task": True, "restore_after": "run_end"}
    return rp.build_stage_payload(base, norm)


# ---------------------------------------------------------------- 命令
def cmd_plan(args) -> int:
    now = args.pretend_now or dt.datetime.now()
    try:
        rep = build_report(args, now)
    except RefuseError as e:
        print("✗ %s" % e, file=sys.stderr)
        return 2
    # 计划文件
    if not args.no_write:
        # 文件名用真实时刻（预演不走未来时间戳，加 "_pretend" 标记防混淆）
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M") + ("_pretend" if args.pretend_now else "")
        scope = "-".join(rep["scope_meta"]["scope"]["server"] or ["fleet"])
        base = os.path.join(args.out_dir or REPORT_DIR, "%s_%s" % (stamp, scope))
        save_json(base + ".json", scrub(rep))
        with open(base + ".md", "w", encoding="utf-8") as f:
            f.write(render_report_md(rep))
        rep["artifacts"] = {"md": base + ".md", "json": base + ".json"}
    if args.json:
        print(json.dumps(scrub(rep), ensure_ascii=False))
    else:
        print(render_report_md(rep))
        if rep["artifacts"]:
            print("\n计划文件：%s" % rep["artifacts"]["md"])
    verdict = "REFUSED" if any(r["gate"] == "G4" for r in rep["refusals"]) else "PLAN_OK"
    print("ACTIVITY_SWITCH=%s %s" % (verdict, report_counts_line(rep)))
    return 2 if verdict == "REFUSED" else 0


def cmd_apply(args) -> int:
    if args.pretend_now:
        print("✗ --pretend-now 只用于 dry-run 预演，不能与 --apply 同用", file=sys.stderr)
        return 2
    now = dt.datetime.now()
    try:
        rep = build_report(args, now)
    except RefuseError as e:
        print("✗ %s" % e, file=sys.stderr)
        return 2
    if rep["refusals"]:
        for r in rep["refusals"]:
            print("✗ 拒绝 [%s %s] %s" % (r["gate"], r["reason"], r["detail"]), file=sys.stderr)
        print("ACTIVITY_SWITCH=REFUSED %s" % report_counts_line(rep))
        return 2
    rc = do_apply(rep, args, now)
    if not args.no_write:
        stamp = now.strftime("%Y%m%d_%H%M")
        base = os.path.join(args.out_dir or REPORT_DIR, "%s_%s_apply" % (stamp,
                          "-".join(rep["scope_meta"]["scope"]["server"] or ["fleet"])))
        save_json(base + ".json", scrub(rep))
        with open(base + ".md", "w", encoding="utf-8") as f:
            f.write(render_report_md(rep))
        rep["artifacts"] = {"md": base + ".md", "json": base + ".json"}
    if args.json:
        print(json.dumps(scrub(rep), ensure_ascii=False))
    else:
        print(render_report_md(rep))
        if rep.get("apply_results"):
            print("\n执行结果：%s" % json.dumps(rep["apply_results"], ensure_ascii=False)[:2000])
        if rep["artifacts"]:
            print("报告：%s" % rep["artifacts"]["md"])
    tag = {0: "DONE", 2: "REFUSED", 3: "PARTIAL", 4: "RESTORE_FAILED"}.get(rc, "RC%d" % rc)
    print("ACTIVITY_SWITCH=%s rc=%d %s" % (tag, rc, report_counts_line(rep)))
    return rc


def cmd_restore(args) -> int:
    st = load_state()
    ids = []
    if args.restore_all:
        ids = [k for k, v in (st.get("switches") or {}).items()
               if v.get("status") not in ("restored",)]
    elif args.restore:
        ids = [args.restore]
    if not ids:
        print("（没有可还原的切换记录）")
        return 0
    rc = 0
    for sid_ in ids:
        sw = (st.get("switches") or {}).get(sid_)
        if not sw:
            print("未知 switch_id：%s（--status 看台账）" % sid_, file=sys.stderr)
            rc = max(rc, 2)
            continue
        recs = [a for a in sw.get("accounts") or [] if a.get("status") == "switched"]
        print("%s：%s %s（%d 个待还原号）" % (sid_, sw.get("server"),
                                             (sw.get("activity") or {}).get("name"), len(recs)))
        if not args.apply:
            for a in recs:
                print("  [dry-run] would restore %s（StageMode %s → %s，删 %s）"
                      % (a["name"], (a.get("temp_plan") or {}).get("plan_id"),
                         a.get("prev_stage_mode"), (a.get("temp_plan") or {}).get("name")))
            continue
        if any(str(os.environ.get(e, "")).strip().lower() in ("1", "true", "yes", "on") for e in NO_GO_ENVS):
            print("拒绝：NO_GO 置位", file=sys.stderr)
            return 2
        for a in recs:
            ok, detail = do_restore_account(a, attempts=args.restore_attempts,
                                            gap=args.restore_gap_seconds)
            a["status"] = "restored" if ok else "restore_failed"
            a["restored_at" if ok else "restore_error"] = _now() if ok else detail[:300]
            print("  %s %s：%s" % ("✓" if ok else "✗", a["name"], detail))
            audit({"event": "restore_account", "switch_id": sid_, "account": a["name"], "ok": ok,
                   "detail": detail[:200], "manual": True})
            if not ok:
                rc = 4
        if all((a.get("status") in ("restored", "restore_failed")) for a in sw.get("accounts") or []):
            sw["status"] = "restore_failed" if any(a.get("status") == "restore_failed"
                                                   for a in sw["accounts"]) else "restored"
            sw["restored_at"] = _now()
        save_state(st)
    if args.apply:
        ledger_append({"kind": "activity_switch_restore", "ok": rc in (0,), "scope": "、".join(ids),
                       "detail": "manual restore rc=%d" % rc})
        print("ACTIVITY_SWITCH=%s rc=%d" % ("RESTORED" if rc == 0 else "RESTORE_FAILED", rc))
    return rc


def cmd_status(args) -> int:
    st = load_state()
    sws = st.get("switches") or {}
    if args.json:
        print(json.dumps(sws, ensure_ascii=False, indent=2, default=str))
        return 0
    if not sws:
        print("（无切换记录）state=%s" % STATE)
        return 0
    print("%-34s %-12s %-10s %-18s %s" % ("switch_id", "status", "server", "activity", "ts"))
    for k, v in sorted(sws.items()):
        print("%-34s %-12s %-10s %-18s %s" % (k, v.get("status"), v.get("server") or "",
                                              ((v.get("activity") or {}).get("name") or "")[:18],
                                              v.get("ts")))
    open_n = [v for v in sws.values() if v.get("status") not in ("restored",)]
    if open_n:
        print("\n⚠ %d 条未还原（有临时「%s-*」计划表与 StageMode 指针）："
              "--restore <id> / --restore-all 处理" % (len(open_n), ACT_PLAN_PREFIX))
    return 0


def cmd_list_plans(args) -> int:
    rp = _rp()
    plans = rp.fetch_plans()
    leftovers = []
    for pid, p in sorted(plans.items(), key=lambda kv: str((kv[1].get("Info") or {}).get("Name"))):
        name = str((p.get("Info") or {}).get("Name") or "")
        mark = ""
        if name.startswith(ACT_PLAN_PREFIX):
            mark = "  ← 本工具（活动切换）"
            leftovers.append(name)
        print("%s  name=%r mode=%s ALL=%s%s" % (pid, name, (p.get("Info") or {}).get("Mode"),
                                                json.dumps(p.get("ALL") or {}, ensure_ascii=False), mark))
    if leftovers:
        print("\n⚠ %d 张「%s-*」：未还原的切换会留着它们（正常）；已 restored 仍残留的要人工删"
              % (len(leftovers), ACT_PLAN_PREFIX))
    return 0


# ---------------------------------------------------------------- 离线自测
class _FakeMAS:
    def __init__(self):
        self.reset()

    def reset(self):
        self.calls = []
        self.scripts = {"sid-1": {"Info": {"Name": "脚本日-1", "Path": r"E:\MAA-Multi\MAA_1"},
                                  "Emulator": {"Index": 13}},
                        "sid-2": {"Info": {"Name": "脚本官-1", "Path": r"E:\MAA-Multi\MAA_2"},
                                  "Emulator": {"Index": 14}}}
        self.users = {
            "sid-1": {
                "u1": {"Info": {"Name": "日甲", "Id": "12345678901", "Mode": "简洁", "StageMode": "Fixed",
                                "Server": "YoStarJP", "Status": True, "RemainedDay": -1, "MedicineNumb": 0,
                                "SeriesNumb": "0", "Stage": "1-7", "Stage_1": "-", "Stage_2": "-",
                                "Stage_3": "-", "Stage_Remain": "1-7"},
                       "Task": {"IfFight": True, "IfActivityFirst": False, "ActivityMedicineNumb": 0}},
                "u2": {"Info": {"Name": "日乙", "Id": "22345678902", "Mode": "简洁", "StageMode": "Fixed",
                                "Server": "YoStarJP", "Status": True, "RemainedDay": -1, "MedicineNumb": 0,
                                "SeriesNumb": "0", "Stage": "PA-8", "Stage_1": "1-7", "Stage_2": "-",
                                "Stage_3": "-", "Stage_Remain": "1-7"},
                       "Task": {"IfFight": True, "IfActivityFirst": False, "ActivityMedicineNumb": 0}}},
            "sid-2": {
                "u3": {"Info": {"Name": "官丙", "Id": "32345678903", "Mode": "简洁", "StageMode": "Fixed",
                                "Server": "Official", "Status": True, "RemainedDay": -1, "MedicineNumb": 0,
                                "SeriesNumb": "0", "Stage": "SR-7", "Stage_1": "-", "Stage_2": "-",
                                "Stage_3": "-", "Stage_Remain": "1-7"},
                       "Task": {"IfFight": True, "IfActivityFirst": False, "ActivityMedicineNumb": 0}},
                "u4": {"Info": {"Name": "官丁", "Id": "42345678904", "Mode": "简洁", "StageMode": "Fixed",
                                "Server": "Official", "Status": False, "RemainedDay": 0, "MedicineNumb": 0,
                                "SeriesNumb": "0", "Stage": "1-7", "Stage_1": "-", "Stage_2": "-",
                                "Stage_3": "-", "Stage_Remain": "-"},
                       "Task": {"IfFight": True, "IfActivityFirst": False, "ActivityMedicineNumb": 0}}}}
        self.plans = {}
        self.n_plan = 0
        self.locked = set()               # sid 集合：模拟「配置锁定」

    def post(self, path, body=None, timeout=60, tries=4):
        body = body if isinstance(body, dict) else {}
        self.calls.append((path, body))
        if path == "/api/scripts/get":
            return {"code": 200, "data": self.scripts}
        if path == "/api/scripts/user/get":
            sid, uid = str(body.get("scriptId") or ""), body.get("userId")
            if uid:
                u = self.users.get(sid, {}).get(str(uid))
                return {"code": 200, "data": ({str(uid): u} if u else {})}
            return {"code": 200, "data": dict(self.users.get(sid, {}))}
        if path == "/api/scripts/user/update":
            sid, uid = str(body.get("scriptId")), str(body.get("userId"))
            if sid in self.locked:
                return {"code": 500, "status": "error", "message": "配置已锁定, 无法修改"}
            u = self.users.setdefault(sid, {}).setdefault(uid, {"Info": {}, "Task": {}})
            for grp, kv in (body.get("data") or {}).items():
                u.setdefault(grp, {}).update(kv)
            return {"code": 200, "status": "success", "message": "操作成功"}
        if path == "/api/plan/get":
            pid = body.get("planId")
            if pid:
                return {"code": 200, "index": [], "data": ({str(pid): self.plans[str(pid)]}
                                                           if str(pid) in self.plans else {})}
            return {"code": 200, "index": [{"uid": k, "type": "MaaPlanConfig"} for k in self.plans],
                    "data": dict(self.plans)}
        if path == "/api/plan/add":
            self.n_plan += 1
            pid = "pl-new-%d" % self.n_plan
            self.plans[pid] = {"Info": {"Name": "新 MAA 计划表", "Mode": "ALL"}, "ALL": {}}
            return {"code": 200, "status": "success", "message": "操作成功", "planId": pid,
                    "data": dict(self.plans[pid])}
        if path == "/api/plan/update":
            pid = str(body.get("planId"))
            if pid not in self.plans:
                return {"code": 500, "status": "error", "message": "计划表不存在"}
            for grp, kv in (body.get("data") or {}).items():
                self.plans[pid].setdefault(grp, {}).update(kv)
            return {"code": 200, "status": "success", "message": "操作成功"}
        if path == "/api/plan/delete":
            self.plans.pop(str(body.get("planId")), None)
            return {"code": 200, "status": "success", "message": "操作成功"}
        return {"code": 404, "message": "unknown endpoint %s" % path}


def _fix_events(tmp, now, *, jp_active=True, cn_upcoming=True, cn_active_farm=False, tw_active=True):
    """夹具：server_events（默认：JP 在期可刷、CN 待开、TW 在期但非刷关）。"""
    def ev(name, typ, s, e, status):
        return {"name": name, "type": typ, "start_bjt": s, "end_bjt": e, "status": status,
                "source": "fixture"}
    servers = {
        "cn_official": {"maa_client": "Official", "is_in_event_period": True,
                        "active_events": ([ev("矢量突破#3", "限时活动（爬塔）", "2026-09-29 16:00",
                                              "2026-10-20 03:59", "active")] if cn_active_farm else []) + [
                            ev("逐影集趣", "限时活动（小活动）", "2026-09-20 16:00", "2026-09-30 03:59", "active")],
                        "upcoming_events": ([ev("矢量突破#3「拟生态」", "限时活动（爬塔/SideStory 类刷关）",
                                                "2026-09-29 16:00", "2026-10-20 03:59", "upcoming")]
                                            if cn_upcoming else []),
                        "ended_events": [ev("月行水上", "SideStory（刷关）", "2026-09-04 12:00",
                                            "2026-09-25 03:59", "ended")],
                        "maa_stage_activity": {"client": "Official", "has_active_stage_activity": False}},
        "cn_bilibili": {"maa_client": "Bilibili", "is_in_event_period": True, "active_events": [],
                        "upcoming_events": [], "ended_events": [], "events_ref": "cn_official",
                        "maa_stage_activity": {"client": "Bilibili", "has_active_stage_activity": None}},
        "jp": {"maa_client": "YoStarJP", "is_in_event_period": True,
               "active_events": ([ev("人々、我々", "SideStory（刷关）", "2026-09-16 15:00",
                                     "2026-09-30 02:59", "active")] if jp_active else []),
               "upcoming_events": [], "ended_events": [],
               "maa_stage_activity": {"client": "YoStarJP", "has_active_stage_activity": True}},
        "kr": {"maa_client": "YoStarKR", "is_in_event_period": True, "active_events": [],
               "upcoming_events": [], "ended_events": [], "maa_stage_activity": {}},
        "tw": {"maa_client": "txwy", "is_in_event_period": True,
               "active_events": ([ev("衛戍協議：盟約 下半期", "miniGame/下半期（非刷关）", "2026-09-24 16:00",
                                     "2026-11-05 03:59", "active")] if tw_active else []),
               "upcoming_events": [], "ended_events": [],
               "maa_stage_activity": {"client": "txwy", "has_active_stage_activity": False}},
        "en": {"maa_client": "YoStarEN", "is_in_event_period": True, "active_events": [],
               "upcoming_events": [], "ended_events": [], "maa_stage_activity": {}},
    }
    path = os.path.join(tmp, "server_events.json")
    save_json(path, {"schema": "maaorch.server_events/1",
                     "generated_at_bjt": "%s+08:00" % (now - dt.timedelta(hours=2)).strftime(TS_FMT),
                     "generated_at_epoch": (now - dt.timedelta(hours=2)).timestamp(), "servers": servers})
    return path


def _fix_maa(tmp, now, *, jp_farmable=True, cn_farmable=False):
    def sec(key, tip, st, et, stages, tz=9):
        return {key: {"Activity": {"Tip": tip, "StageName": key, "UtcStartTime": st, "UtcExpireTime": et,
                                   "TimeZone": tz},
                      "Stages": [{"Display": s, "Value": s, "Drop": "30053"} for s in stages]}}
    jp = {}
    jp.update(sec("PA", "サイドストーリー「人々、我々」", "2026/09/16 16:00:00", "2026/09/30 03:59:59",
                  ["PA-8", "PA-7", "PA-6", "SSReopen-PA"]))
    if not jp_farmable:
        jp["PA"]["Activity"]["UtcExpireTime"] = "2026/09/17 03:59:59"
    official = {"sideStoryStage": {}}
    official["sideStoryStage"].update(sec("SR", "SideStory「月行水上」", "2026/09/04 12:00:00",
                                          "2026/09/18 03:59:59", ["SR-8", "SR-7", "SR-6", "SR-5"], tz=8))
    if cn_farmable:
        official["sideStoryStage"].update(sec("VEC3", "矢量突破#3「拟生态」", "2026/09/29 16:00:00",
                                              "2026/10/20 03:59:59", ["VEC-01", "VEC-02"], tz=8))
    doc = {"Official": official, "YoStarJP": {"sideStoryStage": jp},
           "YoStarKR": {"sideStoryStage": sec("PA", "사람들, 우리들", "2026/09/16 16:00:00",
                                              "2026/09/30 03:59:59", ["PA-8", "PA-7", "PA-6"])},
           "txwy": {"sideStoryStage": sec("TA", "辭歲行", "2026/08/18 16:00:00", "2026/09/08 03:59:59",
                                          ["TA-9", "TA-8"], tz=8)}}
    path = os.path.join(tmp, "maa.json")
    save_json(path, doc)
    return path


def selftest() -> dict:
    """离线回归：假传输 + 假 MAA 表 + 假事件快照；零触网、零写 runtime 真目录。"""
    import io
    import shutil
    import tempfile

    cases = []

    def add(name, fn):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                fn()
            cases.append({"name": name, "ok": True, "detail": ""})
        except Exception as e:  # noqa: BLE001
            cases.append({"name": name, "ok": False, "detail": "%s: %s" % (type(e).__name__, e)})

    def ck(cond, msg):
        if not cond:
            raise AssertionError(msg)

    rp = _rp()
    tmp = tempfile.mkdtemp(prefix="activity_switch_selftest_")
    saved_g = {k: globals()[k] for k in ("RUNTIME", "STATE", "LOG", "LOCK", "BACKUPS", "REPORT_DIR",
                                         "EVENTS", "CAP_JSON", "_SLEEP")}
    saved_rp = {k: getattr(rp, k) for k in ("post", "host_probe", "reachable", "ledger_append")}
    saved_run_probe = globals()["_run_probe_cmd"]
    F = _FakeMAS()
    globals()["RUNTIME"] = tmp
    globals()["STATE"] = os.path.join(tmp, "state.json")
    globals()["LOG"] = os.path.join(tmp, "log.jsonl")
    globals()["LOCK"] = os.path.join(tmp, "act.lock")
    globals()["BACKUPS"] = os.path.join(tmp, "backups")
    globals()["REPORT_DIR"] = os.path.join(tmp, "reports")
    globals()["_SLEEP"] = lambda s: None
    rp.post = F.post
    rp.reachable = lambda: (True, "code=200")
    rp.ledger_append = lambda rec: None
    rp.host_probe = lambda tid, dirs, event_lines=4000: {"ended": False, "end_line": "", "started_seen": True,
                                                         "maa_in_dir": False, "maa_dirs": [], "tail_lines": 0}
    probe_state = {"rc": 0, "calls": []}

    def fake_probe(cmd, timeout):
        probe_state["calls"].append(list(cmd))
        if probe_state["rc"] == 0:
            # 模拟 mas_probe_farm --apply --probe-only：试探成功会把能力台账写成 success（读回据此判"已证"）
            def _arg(name):
                return cmd[cmd.index(name) + 1] if name in cmd else ""
            acct, stage = _arg("--account"), _arg("--stage")
            rows = [r for r in probe_state.get("rows") or []
                    if (r.get("account"), r.get("stage")) != (acct, stage)]
            rows.append({"account": acct, "stage": stage, "status": "success"})
            probe_state["rows"] = rows
            save_json(globals()["CAP_JSON"], {"rows": rows, "stages": [stage], "unknown": {}})
        return probe_state["rc"], "PROBE fixture rc=%d" % probe_state["rc"]

    globals()["_run_probe_cmd"] = fake_probe
    now = dt.datetime(2026, 9, 28, 10, 0, 0)
    try:
        ev_path = _fix_events(tmp, now)
        maa_path = _fix_maa(tmp, now)
        globals()["EVENTS"] = ev_path
        globals()["CAP_JSON"] = os.path.join(tmp, "cap.json")
        save_json(globals()["CAP_JSON"], {"schema": "maaorch.stage_capability/1", "built_at": "2026-09-28 08:58",
            "rows": [{"account": "日甲", "stage": "PA-8", "status": "success"},
                     {"account": "日乙", "stage": "PA-8", "status": "unknown"}],
            "stages": ["PA-8"], "unknown": {"日乙": [{"stage": "PA-8", "tried": False}]}})

        def mk_args(**kw):
            a = argparse.Namespace(server=None, accounts=None, scripts=None, exclude=None, stages=None,
                                   heal_stale=False, heal_to=None, maa_json=maa_path, out_dir=None,
                                   no_write=True, json=False, apply=False, approved_by=None,
                                   pretend_now=None, probe_unknown=False, probe_disable_others=False,
                                   probe_timeout_max=1800, max_probes=0, restore=None, restore_all=False,
                                   restore_attempts=2, restore_gap_seconds=1)
            for k, v in kw.items():
                setattr(a, k, v)
            return a

        def fresh_rep(args):
            return build_report(args, args.pretend_now or now)

        def refresh_fixtures(**kw):
            _fix_events(tmp, now, **{k: v for k, v in kw.items()
                                     if k in ("jp_active", "cn_upcoming", "cn_active_farm", "tw_active")})
            globals()["EVENTS"] = ev_path

        # --- 1) JP：PA 在期可刷 → switch/probe 判定
        def c1():
            F.reset()
            refresh_fixtures()
            rep = fresh_rep(mk_args(server=["jp"]))
            ck(rep["servers"]["jp"]["verdict"] == "switchable", "JP 应 switchable：%s" % rep["servers"]["jp"])
            acts = {e["name"]: e for e in rep["accounts"]}
            ck(acts["日甲"]["action"] == "switch", "日甲（PA-8 success）应直切：%s" % acts["日甲"])
            ck(acts["日乙"]["action"] == "already", "日乙首位 PA-8（已是目标）应 already：%s" % acts["日乙"])
            ck(acts["日甲"]["target"]["stages"] == ["PA-8", "PA-7", "PA-6", "1-7"],
               "目标应含尾位 1-7：%s" % acts["日甲"]["target"])
            ck(not F.plans, "dry-run 不得建计划表")
        add("JP 可刷：直切判定 + 尾位 1-7 + dry-run 零写", c1)

        # --- 2) CN：在期但 MAA 无活动关 → skip（fail-closed）；待开只提示
        def c2():
            F.reset()
            refresh_fixtures()
            rep = fresh_rep(mk_args(server=["cn_official"]))
            ck(rep["servers"]["cn_official"]["verdict"] == "no_farmable", "CN 应 no_farmable")
            acts = {e["name"]: e for e in rep["accounts"]}
            ck(acts["官丙"]["action"] == "skip", "官丙应 skip：%s" % acts["官丙"])
            ck("不可切" in acts["官丙"]["reason"] or "fail-closed" in acts["官丙"]["reason"], acts["官丙"]["reason"])
            ck(any("矢量突破#3" in str(u) for u in rep["servers"]["cn_official"]["events"]["upcoming"]),
               "待开活动应列出")
        add("CN：在期但 MAA 表无活动关 ⇒ 不可切（fail-closed）", c2)

        # --- 3) unknown ⇒ probe（需 --probe-unknown 才真试探）；--probe-unknown 成功 ⇒ 切
        def c3():
            F.reset()
            refresh_fixtures()
            F.users["sid-1"]["u1"]["Info"]["Stage"] = "1-7"
            rep = fresh_rep(mk_args(server=["jp"], accounts=["日甲"]))
            e0 = rep["accounts"][0]
            ck(e0["action"] == "switch", "日甲 success 直切：%s" % e0)
            # unknown 号：把日甲台账改成 unknown
            save_json(globals()["CAP_JSON"], {"rows": [], "stages": [], "unknown": {}})
            rep = fresh_rep(mk_args(server=["jp"], accounts=["日甲"]))
            e0 = rep["accounts"][0]
            ck(e0["action"] == "probe", "unknown 应 probe：%s" % e0)
            ck(e0["probe_stage"] == "PA-8", "probe 关=PA-8：%s" % e0.get("probe_stage"))
            # 不带 --probe-unknown：apply 时跳过
            args = mk_args(server=["jp"], accounts=["日甲"], apply=True, approved_by="tester", no_write=True)
            rc = do_apply(fresh_rep(args), args, now)
            ck(rc == 3, "未 --probe-unknown 应 rc=3（部分完成）：rc=%d" % rc)
            ck(not F.plans, "跳过时不得写：%s" % F.plans)
            # 带 --probe-unknown：台账先 unknown ⇒ 真试探；探针成功（写回台账）→ 切
            probe_state["rc"] = 0
            save_json(globals()["CAP_JSON"], {"rows": [], "stages": [], "unknown": {}})
            args = mk_args(server=["jp"], accounts=["日甲"], apply=True, approved_by="tester",
                           probe_unknown=True, no_write=True)
            rc = do_apply(fresh_rep(args), args, now)
            ck(rc == 0, "试探成功应 rc=0：rc=%d" % rc)
            ck(len(probe_state["calls"]) == 1, "应调用一次探针")
            ck("--probe-only" in probe_state["calls"][0], "探针应 --probe-only")
            ck(F.plans, "试探成功后应建临时计划表")
            u1 = F.users["sid-1"]["u1"]["Info"]
            ck(str(u1["StageMode"]).startswith("pl-new-"), "StageMode 应指向新计划表：%s" % u1["StageMode"])
            st = load_state()
            sws = st["switches"]
            ck(len(sws) == 1, "应有 1 条切换记录")
            sw = list(sws.values())[0]
            ck(sw["status"] == "switched" and sw["accounts"][0]["name"] == "日甲", "记录应 switched")
            # 试探失败 ⇒ 不写
            probe_state["rc"] = 3
            F.reset()
            save_json(globals()["CAP_JSON"], {"rows": [], "stages": [], "unknown": {}})
            args = mk_args(server=["jp"], accounts=["日甲"], apply=True, approved_by="tester",
                           probe_unknown=True, no_write=True)
            rc = do_apply(fresh_rep(args), args, now)
            ck(rc == 3 and not F.plans, "试探失败应 rc=3 且零写：rc=%d plans=%s" % (rc, F.plans))
        add("unknown ⇒ 试探（未开探针跳过 / 探针成功才切 / 探针失败零写）", c3)

        # --- 4) 写面：全量复制当日生效值 + 只差分期关卡位
        def c4():
            F.reset()
            refresh_fixtures()
            save_json(globals()["CAP_JSON"], {"rows": [{"account": "日甲", "stage": "PA-8", "status": "success"},
                                                       {"account": "日乙", "stage": "PA-8", "status": "success"}],
                                              "stages": ["PA-8"], "unknown": {}})
            args = mk_args(server=["jp"], apply=True, approved_by="tester", no_write=True)
            rc = do_apply(fresh_rep(args), args, now)
            ck(rc == 0, "apply rc=%d" % rc)
            u1 = F.users["sid-1"]["u1"]["Info"]
            pid = u1["StageMode"]
            ck(str(pid).startswith("pl-new-"), "u1 StageMode=%s" % pid)
            pl = F.plans[pid]["ALL"]
            ck([pl["Stage"], pl["Stage_1"], pl["Stage_2"], pl["Stage_3"]] == ["PA-8", "PA-7", "PA-6", "1-7"],
               "计划位：%s" % [pl[k] for k in ("Stage", "Stage_1", "Stage_2", "Stage_3")])
            ck(str(pl["Stage_Remain"]) == "1-7", "剩余理智应保留：%s" % pl["Stage_Remain"])
            ck(pl["SeriesNumb"] == "0" and pl["MedicineNumb"] == 0, "药/连战不应被改：%s" % pl)
            ck(str(pid) != "Fixed" and str(F.users["sid-1"]["u1"]["Info"]["Stage"]) == "1-7",
               "账号自己的 7 键不得被改")
            # 备份文件
            st = load_state()
            bdir = list(st["switches"].values())[0]["backup_dir"]
            ck(os.path.exists(os.path.join(bdir, "accounts_before.json")), "备份 accounts_before 应存在")
            ck(os.path.exists(os.path.join(bdir, "hashes.json")), "备份 hashes 应存在")
        add("写面：临时计划表（复制+差分）+ StageMode 指针 + 备份落盘", c4)

        # --- 5) 还原：--restore dry-run 不写；--apply 回写 + 删表；幂等
        def c5():
            st = load_state()
            swid = list(st["switches"].keys())[0]
            rc = cmd_restore(mk_args(restore=swid, apply=False, no_write=True))
            ck(rc == 0, "dry-run restore rc=%d" % rc)
            ck(F.users["sid-1"]["u1"]["Info"]["StageMode"].startswith("pl-new-"), "dry-run 不得回写")
            rc = cmd_restore(mk_args(restore=swid, apply=True, no_write=True))
            ck(rc == 0, "restore rc=%d" % rc)
            ck(str(F.users["sid-1"]["u1"]["Info"]["StageMode"]) == "Fixed", "应回写 Fixed")
            ck(not F.plans, "临时计划表应删除：%s" % F.plans)
            ck(load_state()["switches"][swid]["status"] == "restored", "记录应 restored")
            rc = cmd_restore(mk_args(restore=swid, apply=True, no_write=True))
            ck(rc == 0, "幂等二次还原 rc=%d" % rc)
        add("还原：dry-run 零写 / apply 回写+删表 / 幂等", c5)

        # --- 6) 活动结束自动还原（记录 end 已过）：plan 里出现 restore 动作
        def c6():
            F.reset()
            refresh_fixtures(jp_active=False)
            save_json(globals()["CAP_JSON"], {"rows": [{"account": "日甲", "stage": "PA-8", "status": "success"}],
                                              "stages": ["PA-8"], "unknown": {}})
            args = mk_args(server=["jp"], apply=True, approved_by="tester", no_write=True)
            rep = fresh_rep(args)
            rc = do_apply(rep, args, now)
            ck(rc == 0, "rc=%d" % rc)
            # 手动把记录 end 改成昨天 → 下次 plan 应 due
            st = load_state()
            swid = list(st["switches"].keys())[0]
            st["switches"][swid]["activity"] = {"name": "PA", "end": (now - dt.timedelta(days=1)).isoformat()}
            st["switches"][swid]["stages"] = ["PA-8"]
            save_state(st)
            # 活动不可刷（MAA JP 段过期）
            _fix_maa(tmp, now, jp_farmable=False)
            rep2 = fresh_rep(mk_args(server=["jp"]))
            e0 = [e for e in rep2["accounts"] if e["name"] == "日甲"][0]
            ck(e0["action"] == "restore", "应出现 restore 动作：%s" % e0)
            rc = do_apply(rep2, mk_args(server=["jp"], apply=True, approved_by="tester", no_write=True),
                          now)
            ck(rc == 0, "自动还原 rc=%d" % rc)
            ck(str(F.users["sid-1"]["u1"]["Info"]["StageMode"]) == "Fixed", "应已还原")
            ck(load_state()["switches"][swid]["status"] == "restored", "记录 restored")
            _fix_maa(tmp, now)
        add("活动结束（end 已过/MAA 不可刷）⇒ 自动还原", c6)

        # --- 7) 班次运行中（配置锁定）⇒ 跳过零残留
        def c7():
            F.reset()
            refresh_fixtures()
            save_json(globals()["CAP_JSON"], {"rows": [{"account": "日甲", "stage": "PA-8", "status": "success"}],
                                              "stages": ["PA-8"], "unknown": {}})
            F.locked.add("sid-1")
            args = mk_args(server=["jp"], accounts=["日甲"], apply=True, approved_by="tester", no_write=True)
            rep = fresh_rep(args)
            rc = do_apply(rep, args, now)
            ck(rc == 3, "被锁应 rc=3：rc=%d" % rc)
            ck(str(F.users["sid-1"]["u1"]["Info"]["StageMode"]) == "Fixed", "被锁不得改 StageMode")
            ck(len([k for k in F.plans if k.startswith("pl-new-")]) == 0, "被拒后临时表应清理：%s" % F.plans)
            ck([e for e in rep["apply_results"]["skip"]], "应记录 skip 原因")
            F.locked.discard("sid-1")
        add("班次运行中（配置锁定）：跳过 + 零残留", c7)

        # --- 8) 收尾（heal）：全部位都是已关闭活动关 → 切 1-7（--heal-stale 才做）
        def c8():
            F.reset()
            refresh_fixtures()
            save_json(globals()["CAP_JSON"], {"rows": [], "stages": [], "unknown": {}})
            rep = fresh_rep(mk_args(server=["cn_official"], heal_stale=False))
            e0 = [e for e in rep["accounts"] if e["name"] == "官丙"][0]
            ck(e0["action"] == "skip", "未开 --heal-stale 应 skip：%s" % e0["action"])
            rep = fresh_rep(mk_args(server=["cn_official"], heal_stale=True))
            e0 = [e for e in rep["accounts"] if e["name"] == "官丙"][0]
            ck(e0["action"] == "heal" and e0["heal_to"] == "1-7", "应 heal→1-7：%s" % e0)
            args = mk_args(server=["cn_official"], heal_stale=True, apply=True, approved_by="tester",
                           no_write=True)
            rc = do_apply(fresh_rep(args), args, now)
            ck(rc == 0, "heal apply rc=%d" % rc)
            pid = F.users["sid-2"]["u3"]["Info"]["StageMode"]
            ck(F.plans[pid]["ALL"]["Stage"] == "1-7", "收尾后计划=1-7：%s" % F.plans[pid]["ALL"])
        add("收尾（heal）：计划全为已关闭活动关 ⇒ 切 1-7（默认关，opt-in）", c8)

        # --- 9) 闸门：apply 需显式范围 + --approved-by；NO_GO 一票否决；pretend-now 不与 apply 同用
        def c9():
            F.reset()
            refresh_fixtures()
            save_json(globals()["CAP_JSON"], {"rows": [{"account": "日甲", "stage": "PA-8", "status": "success"}],
                                              "stages": ["PA-8"], "unknown": {}})
            args = mk_args(apply=True, approved_by="tester", no_write=True)   # 无范围
            rep = fresh_rep(args)
            ck(any(r["reason"] == "need_explicit_scope" for r in rep["refusals"]), "缺范围应拒绝")
            args = mk_args(server=["jp"], apply=True, approved_by="", no_write=True)
            rep = fresh_rep(args)
            ck(any(r["reason"] == "need_approval" for r in rep["refusals"]), "缺审批应拒绝")
            os.environ["MAAORCH_ACTIVITY_SWITCH_NO_GO"] = "1"
            args = mk_args(server=["jp"], apply=True, approved_by="tester", no_write=True)
            rep = fresh_rep(args)
            ck(any(r["reason"] == "no_go" for r in rep["refusals"]), "NO_GO 应一票否决")
            del os.environ["MAAORCH_ACTIVITY_SWITCH_NO_GO"]
            rc = cmd_apply(mk_args(server=["jp"], apply=True, approved_by="a", pretend_now=now, no_write=True))
            ck(rc == 2, "pretend-now+apply 应拒绝：rc=%d" % rc)
        add("闸门：显式范围 / --approved-by / NO_GO / pretend-now 不与 apply 同用", c9)

        # --- 10) 证据过期：events >7 天 ⇒ 拒绝切换
        def c10():
            p = _fix_events(tmp, now)
            doc = load_json(p)
            doc["generated_at_epoch"] = (now - dt.timedelta(days=9)).timestamp()
            save_json(p, doc)
            globals()["EVENTS"] = p
            rep = fresh_rep(mk_args(server=["jp"]))
            ck(any(r["reason"] == "events_stale" for r in rep["refusals"]), "过期应拒绝")
            ck(all(e["action"] != "switch" for e in rep["accounts"]), "过期时不得出现 switch")
            ck(any("快照" in w and "天前" in w for w in rep["warnings"]), "应有告警")
            _fix_events(tmp, now)
        add("证据新鲜：server_events >7 天 ⇒ 拒绝切换（G4）", c10)

        # --- 11) --stages 显式目标（forced 可绕过 MAA 表）；4 位截断
        def c11():
            F.reset()
            refresh_fixtures()
            save_json(globals()["CAP_JSON"], {"rows": [{"account": "官丙", "stage": "VEC-01", "status": "success"}],
                                              "stages": ["VEC-01"], "unknown": {}})
            rep = fresh_rep(mk_args(server=["cn_official"], accounts=["官丙"], stages="VEC-01,VEC-02"))
            e0 = rep["accounts"][0]
            ck(e0["action"] == "switch", "显式目标应可切（forced）：%s" % e0)
            ck(e0["target"]["stages"] == ["VEC-01", "VEC-02", "1-7"], "目标：%s" % e0["target"])
            ck(e0["target"]["source"].startswith("cli"), "来源应标注 cli")
            try:
                fresh_rep(mk_args(server=["cn_official"], stages="A,B,C,D,E"))
                raise AssertionError("5 个关码应拒绝")
            except RefuseError:
                pass
        add("--stages 显式目标（forced）+ 关码数校验", c11)

        # --- 12) 报告渲染 + 计划文件
        def c12():
            F.reset()
            refresh_fixtures(jp_active=True)
            rep = fresh_rep(mk_args(server=["jp"]))
            md = render_report_md(rep)
            ck("依据 → 结论" in md and "账号动作清单" in md, "报告应含关键节")
            args = mk_args(server=["jp"], out_dir=os.path.join(tmp, "reports"), no_write=False)
            rc = cmd_plan(args)
            ck(rc == 0, "cmd_plan rc=%d" % rc)
            files = os.listdir(os.path.join(tmp, "reports"))
            ck(any(f.endswith(".md") for f in files) and any(f.endswith(".json") for f in files),
               "应落 md+json：%s" % files)
        add("报告渲染 + 计划文件落盘", c12)

        # --- 13) 台账状态：open_records / record_due 语义
        def c13():
            rec = {"end": (now - dt.timedelta(hours=1)).isoformat(), "stages": ["PA-8"]}
            due, why = record_due(rec, now, {"farmable": [{"stages": ["PA-8"]}]})
            ck(due and "已结束" in why, "end 已过应 due：%s" % why)
            rec = {"end": (now + dt.timedelta(days=3)).isoformat(), "stages": ["PA-8"]}
            due, why = record_due(rec, now, {"farmable": [{"stages": ["PA-8"]}]})
            ck(not due, "仍在其期不应 due")
            due, why = record_due(rec, now, {"farmable": []})
            ck(due and "不可刷" in why, "MAA 不可刷应 due：%s" % why)
        add("record_due：end 已过 / MAA 不可刷 两条判据", c13)
    finally:
        rp.post = saved_rp["post"]
        rp.host_probe = saved_rp["host_probe"]
        rp.reachable = saved_rp["reachable"]
        rp.ledger_append = saved_rp["ledger_append"]
        globals()["_run_probe_cmd"] = saved_run_probe
        for k, v in saved_g.items():
            globals()[k] = v
        shutil.rmtree(tmp, ignore_errors=True)
    ok = all(c["ok"] for c in cases)
    return {"ok": ok, "passed": sum(1 for c in cases if c["ok"]), "failed": sum(1 for c in cases if not c["ok"]),
            "cases": cases}


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="活动期刷取计划自动切换（受控+审批+还原）：活动开→切活动关，活动关→切回原计划")
    ap.add_argument("--server", action="append", default=None,
                    help="范围：服（可重复/逗号；cn_official/B服名字/jp/kr/tw/en 及中文别名）")
    ap.add_argument("--accounts", help="范围：账号名（逗号分隔，与 AUTO-MAS 一致）")
    ap.add_argument("--scripts", help="范围：脚本名（逗号分隔）")
    ap.add_argument("--exclude", help="排除账号（逗号分隔）")
    ap.add_argument("--stages", help="显式目标活动关（逗号分隔，≤4；不依赖 MAA 表）")
    ap.add_argument("--heal-stale", action="store_true",
                    help="收尾：计划全部位都是已关闭活动关（如 SR-7）的号，切到 --heal-to（默认 1-7）")
    ap.add_argument("--heal-to", help="收尾目标关（默认 1-7 / 该号剩余理智位）")
    ap.add_argument("--pretend-now", help="预演时刻（'YYYY-MM-DD HH:MM'）：以该时刻判定（只允许 dry-run）")
    ap.add_argument("--maa-json", help="MAA 活动表本地快照（缺省实时拉取）")
    ap.add_argument("--apply", action="store_true", help="真执行（默认 dry-run：只打印将做的事与接口）")
    ap.add_argument("--approved-by", help="审批（apply 必填）：批准人/依据，写入审计与台账")
    ap.add_argument("--probe-unknown", action="store_true",
                    help="apply 时对台账 unknown 的号先跑 mas_probe_farm --probe-only，成功才切")
    ap.add_argument("--probe-disable-others", action="store_true",
                    help="试探用 --disable-others（默认 --all-accounts）")
    ap.add_argument("--probe-timeout-max", type=float, default=3600,
                    help="单次试探子进程上限秒（默认 3600；探针自身 --timeout 600s）")
    ap.add_argument("--max-probes", type=int, default=0, help="本次 apply 最多试探几个号（0=不限）")
    ap.add_argument("--restore", metavar="SWITCH_ID", help="一键还原某次切换（dry-run 须加 --apply）")
    ap.add_argument("--restore-all", action="store_true", help="还原所有未还原切换")
    ap.add_argument("--restore-attempts", type=int, default=6, help="还原重试次数（默认 6）")
    ap.add_argument("--restore-gap-seconds", type=float, default=20, help="还原重试间隔秒（默认 20）")
    ap.add_argument("--out-dir", help="报告落点（默认 runtime/reports/activity_switch/）")
    ap.add_argument("--no-write", action="store_true", help="不写报告文件（只打印）")
    ap.add_argument("--status", action="store_true", help="切换台账（只读）")
    ap.add_argument("--list-plans", action="store_true", help="只读：计划表 + 「临时-活动-*」残留")
    ap.add_argument("--selftest", action="store_true", help="离线自测（假传输，零触网零写）")
    ap.add_argument("--json", action="store_true", help="JSON 输出（单行）")
    a = ap.parse_args(argv)

    for k in ("server", "accounts", "scripts", "exclude"):
        v = getattr(a, k)
        if isinstance(v, list):
            out = []
            for x in v:
                out.extend([t for t in str(x).split(",") if t.strip()])
            setattr(a, k, out)
        elif isinstance(v, str):
            setattr(a, k, [t for t in v.split(",") if t.strip()])
    if a.pretend_now:
        a.pretend_now = parse_ts(a.pretend_now)
        if a.pretend_now is None:
            print("✗ --pretend-now 格式应为 'YYYY-MM-DD HH:MM[:SS]'", file=sys.stderr)
            return 2

    if a.selftest:
        rep = selftest()
        if a.json:
            print(json.dumps(rep, ensure_ascii=False))
        else:
            print("SELFTEST=%s (%d/%d)" % ("OK" if rep["ok"] else "FAIL", rep["passed"],
                                           rep["passed"] + rep["failed"]))
            for c in rep["cases"]:
                print("  %s %s%s" % ("PASS" if c["ok"] else "FAIL", c["name"],
                                     ("" if c["ok"] else "  ← " + c["detail"])))
        return 0 if rep["ok"] else 3
    if a.status:
        return cmd_status(a)
    if a.list_plans:
        return cmd_list_plans(a)
    if a.restore or a.restore_all:
        return cmd_restore(a)
    if a.apply:
        return cmd_apply(a)
    return cmd_plan(a)


if __name__ == "__main__":
    sys.exit(main())
