#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""周期性事件日历：剿灭周重置 / 活动开·关 / 签到 / 资源关轮换 / 商店刷新 —— 到点提醒与发车建议。

只读工具：不碰 AUTO-MAS/MAA 任何配置、不发车、不动实例。唯一写面 = 自己的日历产物
`runtime/reports/event_calendar.json|md`（可用 --no-write 关闭）。

## 它回答什么问题
- 「今天/未来 14 天，固定周期上会发生什么」（周切、日切、资源关开哪几个、宿主重启窗口）
- 「活动什么时候开/关、商店什么时候截止」（来自 runtime/reports/server_events.json 快照）
- 「所以什么时候发车/补刷最合适」（只给建议，不执行 —— 发车归 tools/mas_dispatch.py / run profile）

## 周期规则表（含依据来源；机器口径见本文件 RULES，人读版见 docs/EVENT_CALENDAR.md）
| 事件 | 周期/时刻（服务器本地） | 北京时间 | 影响 | 依据 |
|---|---|---|---|---|
| 服务器日切 | 每日 04:00 | CN/TW 04:00；JP/KR 03:00；EN 19:00 | 日常任务/信用商店刷新/月卡/剿灭计数日界 | AUTO-MAS `app/task/MAA/AutoProxy.py` L324-327（UTC+4 日界） |
| 周切（剿灭周重置） | 每周一 04:00 | 同上按服换算 | 剿灭周额度、周任务、周计划 | `AutoProxy.py` L82-98/L304-318（`AnnihilationStartWeekday` 默认 Monday）+ docs/SCHEDULER.md L18 |
| 资源关轮换 | 每日 04:00 按周表切换 | 同上 | 选关（MAA 只选「当日开放」的关） | MAA v6.18.0 `src/MaaWpfGui/Services/StageManager.cs` L677-695 + `Models/StageInfo.cs` L148-174 |
| 活动开/关 | 通常 16:00 开、03:59 止 | 按服换算 | Stage 计划、药投放、收尾 | runtime/reports/server_events.json（快照） |
| 信用商店刷新 | 每日 04:00 | 同上 | MAA Mall 链（信用购物） | 社区口径（待核验）；MAA `CreditShop-*` docs/MAA_CAPABILITY_MAP.md L86 |
| 凭证交易所刷新 | 每月（初） | — | 绿票区/黄票区库存 | Terra Wiki（二手，待核验精确时刻） |
| 宿主计划重启 | 每日 04:00 / 19:00 | 本机 | 跨窗口任务被吞（孤儿化） | tools/mas_plan_builder.py L1136（事件 id=1074） |

## 用法
    python tools/event_calendar.py                    # 生成 14 天日历 -> runtime/reports/event_calendar.{json,md}
    python tools/event_calendar.py --check            # 紧凑：末行 UPCOMING=…（巡检/日报接线用），不写盘
    python tools/event_calendar.py --days 7 --servers cn_official,jp   # 7 天 + 指定服别
    python tools/event_calendar.py --json             # 机读（单行 JSON）
    python tools/event_calendar.py --selftest         # 离线自测（临时目录，零写 runtime）
退出码：0=OK（含 DEGRADED）；2=UNKNOWN/fail-closed（不写盘）；3=自测失败。

`UPCOMING=` 行口径：窗口内（默认 48h）按期升序的事件，格式
`UPCOMING=<服·事件>:<YYYY-MM-DD HH:MM>[; …]`，无事件写 `UPCOMING=无`，数据不可用写
`UPCOMING=UNKNOWN:<原因>`（fail-closed，不当「无事件」用）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
BJT = dt.timezone(dt.timedelta(hours=8))
DEFAULT_DAYS = 14
DEFAULT_WINDOW_HOURS = 48.0
ADVICE_HOURS = 72.0
MAX_UPCOMING = 6
SCHEMA = "maaorch.event_calendar/1"
STAMP_FMT = "%Y-%m-%d %H:%M"
TS_FMT = "%Y-%m-%d %H:%M:%S"

WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# ---------------------------------------------------------------- 服别
SERVERS = {
    "cn_official": {"name": "国服官服", "short": "国服", "offset": 8, "tz": "UTC+8",
                    "aliases": ["Official", "官服", "国服", "CN", "cn_official"]},
    "cn_bilibili": {"name": "B服", "short": "B服", "offset": 8, "tz": "UTC+8",
                    "aliases": ["Bilibili", "B服", "哔哩哔哩服", "cn_bilibili"]},
    "jp": {"name": "日服", "short": "日服", "offset": 9, "tz": "UTC+9",
           "aliases": ["YoStarJP", "日服", "JP", "jp"]},
    "kr": {"name": "韩服", "short": "韩服", "offset": 9, "tz": "UTC+9",
           "aliases": ["YoStarKR", "韩服", "KR", "kr"]},
    "tw": {"name": "台服", "short": "台服", "offset": 8, "tz": "UTC+8",
           "aliases": ["txwy", "台服", "繁中服", "TW", "tw"]},
    "en": {"name": "国际服", "short": "国际服", "offset": -7, "tz": "UTC-7（官网口径；冬令时 UTC-8）",
           "aliases": ["YoStarEN", "国际服", "Global", "EN", "en"]},
}
SERVER_ORDER = ["cn_official", "cn_bilibili", "jp", "kr", "tw", "en"]
DEFAULT_FOCUS = ["cn_official", "cn_bilibili", "jp", "kr", "tw", "en"]

# ---------------------------------------------------------------- 资源关周表
# 口径 = MAA v6.18.0 src/MaaWpfGui/Services/StageManager.cs L677-695（AddPermanentStages）。
# days 用 Python weekday（周一=0…周日=6）；[] = 恒开放（StageInfo.cs「Null or Empty means always open」）。
# 物品对照：docs/MAA_DEPOT_MAINTAIN.md L52（CE-6 龙门币 / AP-5 采购凭证 / CA-5 技巧概要）+ MAA 资源备份
#           debug/maa_update/source_6160_backup/resource/stages.json + item_index.json（实测：SK-5 碳素、
#           LS-6 作战记录、PR-* 职业芯片）。
RESOURCE_STAGES = [
    {"code": "CE-6", "material": "龙门币", "days": [1, 3, 5, 6]},
    {"code": "AP-5", "material": "采购凭证", "days": [0, 3, 5, 6]},
    {"code": "CA-5", "material": "技巧概要", "days": [1, 2, 4, 6]},
    {"code": "SK-5", "material": "碳素", "days": [0, 2, 4, 5]},
    {"code": "LS-6", "material": "作战记录", "days": []},
    {"code": "PR-A-1", "material": "职业芯片（A）", "days": [0, 3, 4, 6]},
    {"code": "PR-B-1", "material": "职业芯片（B）", "days": [0, 1, 4, 5]},
    {"code": "PR-C-1", "material": "职业芯片（C）", "days": [2, 3, 5, 6]},
    {"code": "PR-D-1", "material": "职业芯片（D）", "days": [1, 2, 5, 6]},
]
RESOURCE_DAYS_LABEL = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}

# 主动提醒/建议用的事件分类
FARMING_HINTS = ("刷关", "SideStory", "爬塔")
SIGNIN_HINTS = ("签到",)
# 「全关卡全天开放」各服叫法（CN 原文 / 日服 / 国际服；韩/台暂无在档条目，新叫法出现时补这里）
OPEN_ALL_HINTS = ("全关卡全天开放", "全ステージ開放", "EVERYDAY ACCESS")
NON_FARMING_HINTS = ("非刷关", "miniGame", "时装", "礼包")

# ---------------------------------------------------------------- 周期规则表（机器口径）
RULES = [
    {
        "id": "server_daily_reset", "kind": "daily_reset",
        "event": "服务器日切（日常刷新）",
        "cycle": "每日 04:00（服务器本地）",
        "cycle_en": "每日 04:00 (UTC-7)，冬令时未核",
        "impact": "日常任务/信用商店刷新/每日免费寻访/月卡领取；AUTO-MAS 的「今天」= UTC+4（= 04:00 UTC+8）",
        "source": "AUTO-MAS D:\\MAS\\app\\task\\MAA\\AutoProxy.py L324-327（LastProxyDate 日界）+ docs/MAA_EVENT_FARMING_PLAYBOOK.md L120",
        "confidence": "高（CN；其它服按各服本地 04:00 的通行口径，未逐服一手核验）",
    },
    {
        "id": "weekly_reset", "kind": "weekly_reset",
        "event": "周切（剿灭周重置 / 周任务）",
        "cycle": "每周一 04:00（服务器本地）",
        "impact": "剿灭周额度重置、周任务刷新、周计划（Weekly）切周",
        "source": "AUTO-MAS AutoProxy.py L82-98（`_should_run_annihilation`）+ L304-318（`AnnihilationStartWeekday` 默认 Monday，周标记 `%G-W%V`@UTC+4）+ docs/SCHEDULER.md L18（annihilation next_run=下周一 04:00）",
        "confidence": "高（代码 + 本仓文档 + 实测：2026-W39 记账 49 号）",
    },
    {
        "id": "annihilation_weekly", "kind": "annihilation_weekly",
        "event": "剿灭作战周常窗口",
        "cycle": "每周一 04:00 起，本周内任意时刻跑 1 次（AUTO-MAS 按 ISO 周记账）",
        "impact": "该号本周是否还需跑剿灭：`Data.AnnihilationCompletedWeek` != 当周 ⇒ 会先跑剿灭模式再进日常",
        "source": "AUTO-MAS AutoProxy.py L938-964（完成记账，宁重试不误记）+ docs/MAA_EVENT_FARMING_PLAYBOOK.md §3.2/§3.3",
        "confidence": "高",
    },
    {
        "id": "resource_rotation", "kind": "resource_rotation",
        "event": "资源关轮换（MAA 当日开放表）",
        "cycle": "每日 04:00 切换，按周表",
        "impact": "选关：MAA 只选「当日开放」的关；排了闭着的关会被整条跳过（FightSkippedNoOpenStage）",
        "source": "MAA v6.18.0 src/MaaWpfGui/Services/StageManager.cs L677-695（周表）+ Models/StageInfo.cs L148-174（开放判定）；物品对照 docs/MAA_DEPOT_MAINTAIN.md L52",
        "confidence": "高（MAA 侧权威；与在用的周计划实测一致：docs/MAS_SCRIPTS_CATALOG.md L96-97）",
    },
    {
        "id": "resource_open_all", "kind": "resource_open_all",
        "event": "「资源收集」全关卡全天开放（活动）",
        "cycle": "活动期（逐期以 server_events 快照为准；写定时为 2026-09-29 16:00 → 10-20 03:59）",
        "impact": "期间全部资源关每日可刷（含 CE-6/AP-5/SK-5/PR 等），补材料/芯片的黄金窗口",
        "source": "runtime/reports/server_events.json（cn_official.upcoming_events）+ MAA StageInfo.cs L150-161（resourceCollection 活动在期 ⇒ 全开）",
        "confidence": "高（本期时间来自官网公告快照）",
    },
    {
        "id": "event_open_close", "kind": "event_open_close",
        "event": "活动开/关（惯例时刻）",
        "cycle": "开启通常 16:00；结束 03:59（服务器本地）",
        "impact": "Stage 计划、药投放（临期药窗口）、活动关生命周期；结束后不改 Stage = 理智作战静默跳过（SR 教训）",
        "source": "runtime/reports/server_events.json（逐条起止）+ docs/MAA_EVENT_FARMING_PLAYBOOK.md R1/§5.5",
        "confidence": "高（逐条来自官网公告；「16:00/03:59」为惯例不是规则）",
    },
    {
        "id": "event_shop_deadline", "kind": "event_shop_deadline",
        "event": "活动商店/里程碑/报酬领取截止",
        "cycle": "通常活动结束后 3 天 03:59（逐条以公告为准）",
        "impact": "代币/兑换、报酬领取；错过即损失",
        "source": "runtime/reports/server_events.json 的 note 字段（本工具自动抽取 `MM-DD HH:MM` 形式；note 内为该服本地时间，本工具换算 BJT）",
        "confidence": "中（自动抽取，需人工复核）",
    },
    {
        "id": "credit_store_daily", "kind": "credit_store_daily",
        "event": "信用商店刷新",
        "cycle": "每日 04:00（服务器本地）",
        "impact": "MAA Mall 链（信用购物）当天可买项变换；补跑失败后重试最好跨过刷新点看新商品",
        "source": "社区口径（**待核验**）；MAA `CreditShop-*` 链见 docs/MAA_CAPABILITY_MAP.md L86",
        "confidence": "低（未一手核验）",
    },
    {
        "id": "cert_exchange_monthly", "kind": "cert_exchange_monthly",
        "event": "凭证交易所刷新（绿票区/黄票区）",
        "cycle": "每月（初）；精确时刻（每月 1 日 04:00）**待核验**",
        "impact": "资质/高级凭证区库存与新干员信物更新；采购凭证方面无月度限制",
        "source": "Terra Wiki `Certificate Store`（二手：\"All items are reset every month\"，2026-09-28 抓取）",
        "confidence": "低（二手；跨月时人工确认）",
    },
    {
        "id": "host_restart_window", "kind": "host_restart_window",
        "event": "宿主计划重启窗口（AUTO-MAS 主机）",
        "cycle": "每日 04:00 / 19:00（本机时间）",
        "impact": "跨窗口的长任务会被吞（孤儿化）；晚班缺口须在 04:00 前收工；04:01 自重启后 AUTO-MAS 会再吞一批",
        "source": "tools/mas_plan_builder.py L1136（事件 id=1074）+ docs/MAS_SHIFT_PLAYBOOK.md §0/§7",
        "confidence": "高（本机计划任务口径）",
    },
]


# ---------------------------------------------------------------- 基础
def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="周期性事件日历：周期规则 + 未来 N 天日历 + 到点提醒 + 发车建议（只读）")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="日历天数（默认 14）")
    ap.add_argument("--servers", default=",".join(DEFAULT_FOCUS),
                    help="要展开的服别（逗号分隔；默认全部）。可选：%s" % "、".join(SERVER_ORDER))
    ap.add_argument("--window-hours", type=float, default=DEFAULT_WINDOW_HOURS, help="UPCOMING 窗口小时数（默认 48）")
    ap.add_argument("--check", action="store_true", help="紧凑模式：EVENT_CAL=/UPCOMING=/EVENT_ADVICE= 三行（巡检/日报接线用），不写盘")
    ap.add_argument("--no-advice", action="store_true", help="不打印发车建议（默认打印；md/JSON 里恒有）")
    ap.add_argument("--json", action="store_true", help="机读输出（单行 JSON）")
    ap.add_argument("--no-write", action="store_true", help="不写 runtime/reports/event_calendar.*")
    ap.add_argument("--out-dir", help="输出目录（默认 runtime/reports）")
    ap.add_argument("--events", help="server_events.json 路径（默认 runtime/reports/server_events.json）")
    ap.add_argument("--now", help="冻结当前时间（'YYYY-MM-DD HH:MM'，调试/复现用）")
    ap.add_argument("--selftest", action="store_true", help="离线自测（临时目录，零写 runtime）")
    return ap.parse_args(argv)


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_ts(s: str):
    """宽松解析 server_events 的时间串（BJT 语义）：'%Y-%m-%d [%H:%M[:%S]]'。"""
    s = str(s or "").strip().replace("T", " ")
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[ ](\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if not m:
        return None
    try:
        return dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                           int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0))
    except ValueError:
        return None


def now_bjt(now_s: str | None = None) -> dt.datetime:
    if now_s:
        d = parse_ts(now_s)
        if d is None:
            raise ValueError("--now 解析失败：%r" % now_s)
        return d.replace(tzinfo=BJT)
    return dt.datetime.now(tz=BJT)


def server_local(t_bjt: dt.datetime, offset: int) -> dt.datetime:
    return (t_bjt.astimezone(dt.timezone(dt.timedelta(hours=offset))))


def fmt(ts: dt.datetime) -> str:
    return ts.strftime(STAMP_FMT)


def rel_hours(ts: dt.datetime, now: dt.datetime) -> float:
    return (ts - now).total_seconds() / 3600.0


class Refuse(Exception):
    """无法产出日历（fail-closed）。"""


# ---------------------------------------------------------------- 事件源
def event_key_of(token) -> str:
    t = str(token or "").strip()
    for key, meta in SERVERS.items():
        if t == key or t in meta["aliases"]:
            return key
    return ""


def load_events(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:  # noqa: BLE001
        raise Refuse("活动快照不可读：%s（%s）" % (path, e))


def events_age_days(doc: dict) -> float | None:
    g = parse_ts(doc.get("generated_at_bjt") or "")
    if g is None:
        return None
    return (now_bjt() - g.replace(tzinfo=BJT)).total_seconds() / 86400.0


def collect_events(doc: dict, key: str) -> list:
    """把某服 snapshot 里的 active/upcoming/ended 事件折成统一条目。"""
    sv = (doc.get("servers") or {}).get(key) or {}
    out = []
    for group, status in (("active_events", "active"), ("upcoming_events", "upcoming"), ("ended_events", "ended")):
        for e in sv.get(group) or []:
            if not isinstance(e, dict):
                continue
            st = parse_ts(e.get("start_bjt") or e.get("start_local") or "")
            en = parse_ts(e.get("end_bjt") or e.get("end_local") or "")
            name = str(e.get("name") or "?")
            etype = str(e.get("type") or "")
            out.append({"name": name, "type": etype, "status": status, "start": st, "end": en,
                        "source": e.get("source") or "", "note": str(e.get("note") or ""),
                        "raw_start": e.get("start_bjt") or "", "raw_end": e.get("end_bjt") or ""})
    return out


DEADLINE_RE = re.compile(r"(?:到|至|截止)\s*(\d{4}-)?(\d{1,2})-(\d{1,2})\s*(\d{1,2}):(\d{2})")


def note_deadlines(ev: dict, year: int, offset: int) -> list:
    """从 note 里抽「到 10-03 03:59」样式的截止点（供人工复核，标 kind=deadline_from_note）。

    note 内的时间为**该服本地时间**（JP/KR = JST/KST，EN = UTC-7 —— 见 docs/SERVER_EVENTS.md
    §2/§5 口径），返回 {"ts": 本地 naive, "ts_bjt": BJT naive, "text": 原文}；
    与活动本体结束同刻（按 BJT 比较）则跳过，避免重复记。
    """
    out = []
    ev_end = ev.get("end")
    for m in DEADLINE_RE.finditer(ev.get("note") or ""):
        y = int(m.group(1)[:-1]) if m.group(1) else year
        try:
            ts = dt.datetime(y, int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)))
        except ValueError:
            continue
        ts_bjt = ts + dt.timedelta(hours=8 - offset)
        if ev_end and ts_bjt == ev_end:
            continue                                     # 与活动本体结束同刻 -> 不重复记
        out.append({"ts": ts, "ts_bjt": ts_bjt, "text": m.group(0)})
    return out


# ---------------------------------------------------------------- 资源关
def resource_open_for(game_date: dt.date, open_all: bool = False) -> tuple:
    if open_all:
        return [s["code"] for s in RESOURCE_STAGES], True
    codes = [s["code"] for s in RESOURCE_STAGES if (not s["days"]) or (game_date.weekday() in s["days"])]
    return codes, False


def resource_label(code: str) -> str:
    for s in RESOURCE_STAGES:
        if s["code"] == code:
            return "%s（%s）" % (s["code"], s["material"])
    return code


# ---------------------------------------------------------------- 日历主流程
def build_calendar(now: dt.datetime, days: int, servers: list, events: dict, events_path: str,
                   events_loaded: bool, span_hours: float = DEFAULT_WINDOW_HOURS) -> dict:
    days = max(1, min(int(days), 60))
    out = {
        "schema": SCHEMA,
        "generated_at_bjt": now.strftime(TS_FMT) + "+08:00",
        "generated_at_epoch": int(now.timestamp()),
        "purpose": "周期性事件日历（剿灭周重置/活动开关/签到/资源关轮换/商店刷新）+ 到点提醒 + 发车建议（只读，只建议不发车）",
        "days": days,
        "servers_requested": servers,
        "events_source": {"path": events_path, "loaded": bool(events_loaded),
                          "generated_at_bjt": events.get("generated_at_bjt") or "",
                          "age_days": events_age_days(events) if events_loaded else None},
        "rules": RULES,
        "resource_stage_table": [
            {"code": s["code"], "material": s["material"],
             "open_days_cn": "恒开放" if not s["days"] else "/".join(RESOURCE_DAYS_LABEL[d] for d in s["days"])}
            for s in RESOURCE_STAGES],
        "per_server": {},
        "upcoming": [],
        "deadlines": [],
        "advice": [],
        "warnings": [],
    }
    age = out["events_source"]["age_days"]
    if not events_loaded:
        out["warnings"].append("活动快照缺失（%s）：只出周期性事件；活动开/关与商店截止不可用" % events_path)
    elif age is None:
        out["warnings"].append("活动快照缺 generated_at_bjt：无法判断新鲜度")
    elif age > 7:
        out["warnings"].append("活动快照已 %.1f 天（>7 天上限）：活动时间可能已变，执行前按 docs/SERVER_EVENTS.md §8 重抓" % age)
    elif age > 3:
        out["warnings"].append("活动快照已 %.1f 天（>3 天）：建议重抓（docs/SERVER_EVENTS.md §8）" % age)

    # ---- 逐服逐日
    all_upcoming = []
    for key in servers:
        meta = SERVERS.get(key)
        if meta is None:
            raise Refuse("未知服别 %r（可选：%s）" % (key, "、".join(SERVER_ORDER)))
        off = meta["offset"]
        tz = dt.timezone(dt.timedelta(hours=off))
        now_loc = now.astimezone(tz)
        # 服务器「游戏日」= 本地时间 - 4h 的日期（04:00 日切）
        first_game = (now_loc - dt.timedelta(hours=4)).date()
        ev_list = collect_events(events, key) if events_loaded else []
        if key == "cn_bilibili" and not ev_list and events_loaded:
            ev_list = collect_events(events, "cn_official")      # B 服无独立公告面：跟随官服
            out["warnings"].append("B服事件直接引用官服口径（无独立公告面；见 docs/SERVER_EVENTS.md §2）")
        open_all_windows = [(e["start"].replace(tzinfo=BJT), e["end"].replace(tzinfo=BJT)) for e in ev_list
                            if any(h in ((e.get("name") or "") + (e.get("type") or "") + (e.get("note") or ""))
                                   for h in OPEN_ALL_HINTS)
                            and e["start"] and e["end"]]
        day_rows = []
        for i in range(days):
            gd = first_game + dt.timedelta(days=i)
            start_loc = dt.datetime.combine(gd, dt.time(4, 0), tzinfo=tz)
            end_loc = start_loc + dt.timedelta(days=1)
            # 全开放窗口与「游戏日」按重叠判定（活动常在 16:00 开启 -> 当日只覆盖后半段）
            open_hits = [s for s, en in open_all_windows if s < end_loc and en > start_loc]
            is_open_all = bool(open_hits)
            open_all_from = min([s for s in open_hits if s > start_loc], default=None)
            res_base, _ = resource_open_for(gd, False)      # 周表基表（部分覆盖日：HH:MM 前按它）
            res_codes, forced = resource_open_for(gd, is_open_all)
            entries = []
            # 周切（周一 04:00）
            if gd.weekday() == 0:
                entries.append({
                    "kind": "weekly_reset", "title": "周切：剿灭周额度重置 + 周任务刷新",
                    "ts_bjt": fmt(start_loc.astimezone(BJT)), "ts_local": fmt(start_loc),
                    "server": key, "source": "AUTO-MAS AutoProxy.py L82-98/L304-318（AnnihilationStartWeekday=Monday, UTC+4）",
                })
            # 活动条目（该服；按「本地自然日」归档 —— 03:59 结束的活动归它真正发生的那个日期）
            for e in ev_list:
                for field, kind, label in (("start", "event_start", "开启"), ("end", "event_end", "结束")):
                    t = e.get(field)
                    if t is None:
                        continue
                    ts_loc = t.replace(tzinfo=BJT).astimezone(tz)
                    if ts_loc.date() != gd:
                        continue
                    entries.append({
                        "kind": kind, "title": "%s%s" % (e["name"], label),
                        "event_name": e["name"], "event_type": e["type"], "event_status": e["status"],
                        "ts_bjt": fmt(t), "ts_local": fmt(ts_loc), "server": key,
                        "source": e.get("source") or "", "note": e.get("note") or "",
                    })
                for dl in note_deadlines(e, gd.year, off):
                    ts_loc = dl["ts"]                            # note 内为服务器本地时间
                    if ts_loc.date() != gd:
                        continue
                    entries.append({
                        "kind": "deadline_from_note", "title": "%s·商店/奖励截止" % e["name"],
                        "event_name": e["name"], "ts_bjt": fmt(dl["ts_bjt"]), "ts_local": fmt(ts_loc),
                        "server": key, "source": "note: %s" % dl["text"], "note": e.get("note") or "",
                    })
            # 宿主重启（本机窗口，与服别无关；只在焦点为 cn_official 时记一次，避免重复 6 份）
            if key == "cn_official":
                for hh in (4, 19):
                    ts = dt.datetime.combine(gd, dt.time(hh, 0), tzinfo=BJT)
                    ts_loc = ts.astimezone(tz)
                    if ts_loc.date() == gd and now <= ts <= now + dt.timedelta(days=days):
                        entries.append({"kind": "host_restart", "title": "宿主机计划重启窗口（04:00/19:00；跨窗口任务会被吞）",
                                        "ts_bjt": fmt(ts), "ts_local": fmt(ts_loc), "server": "host",
                                        "source": "tools/mas_plan_builder.py L1136（事件 id=1074）"})
            entries.sort(key=lambda x: x["ts_bjt"])
            day_rows.append({
                "game_date": gd.isoformat(), "weekday": WEEKDAY_CN[gd.weekday()],
                "starts_local": fmt(start_loc), "starts_bjt": fmt(start_loc.astimezone(BJT)),
                "daily_reset_bjt": fmt(start_loc.astimezone(BJT)),
                "resource_open": res_codes,
                "resource_open_base": res_base,
                "resource_open_all": bool(is_open_all or forced),
                "resource_open_all_from_bjt": fmt(open_all_from.astimezone(BJT)) if open_all_from else "",
                "entries": entries,
            })
        out["per_server"][key] = {"name": meta["name"], "offset_hours": off, "tz": meta["tz"],
                                  "daily_reset_local": "04:00", "days": day_rows}

    # ---- 跨服 upcoming（按 BJT 升序）
    horizon_end = now + dt.timedelta(hours=span_hours)
    for key, block in out["per_server"].items():
        for d in block["days"]:
            for e in d["entries"]:
                if e["kind"] in ("host_restart", "weekly_reset") and e.get("server") == "host":
                    continue
                ts = parse_ts(e["ts_bjt"]).replace(tzinfo=BJT)
                if now <= ts <= horizon_end:
                    item = dict(e)
                    item["in_hours"] = round(rel_hours(ts, now), 1)
                    all_upcoming.append(item)
    all_upcoming.sort(key=lambda x: (x["ts_bjt"], x["server"]))
    out["upcoming"] = all_upcoming[:MAX_UPCOMING * 4]     # JSON 里多留一些；UPCOMING 行只取前 N

    # ---- 截止清单：所有请求服别的 note 截止（含 14 天窗外；本工具只报告，不动作）
    now_naive = now.replace(tzinfo=None)
    far = now_naive + dt.timedelta(days=days + 30)
    for key in servers:
        off = SERVERS[key]["offset"]
        ev_list = collect_events(events, key) if events_loaded else []
        if key == "cn_bilibili" and not ev_list and events_loaded:
            ev_list = collect_events(events, "cn_official")
        for e in ev_list:
            for dl in note_deadlines(e, now.year, off):
                ts_bjt = dl["ts_bjt"]
                if ts_bjt < now_naive - dt.timedelta(days=1) or ts_bjt > far:
                    continue
                out["deadlines"].append({
                    "server": key, "kind": "deadline_from_note",
                    "title": "%s·商店/奖励截止" % e["name"], "event_name": e["name"],
                    "ts_bjt": fmt(ts_bjt),
                    "in_window": ts_bjt <= now_naive + dt.timedelta(days=days),
                    "source": "note: %s" % dl["text"], "note": e.get("note") or ""})
    out["deadlines"].sort(key=lambda x: x["ts_bjt"])

    out["advice"] = build_advice(out, now)
    return out


def build_advice(cal: dict, now: dt.datetime) -> list:
    """发车建议（只建议；每条带依据）。"""
    adv = []
    horizon = now + dt.timedelta(hours=ADVICE_HOURS)

    def add(rule: str, text: str, evidence: str, level: str = "info"):
        adv.append({"rule": rule, "level": level, "text": text, "evidence": evidence})

    # 1) 剿灭周常
    cn_days = cal["per_server"].get("cn_official", {}).get("days") or []
    next_weekly = None
    for d in cn_days:
        for e in d["entries"]:
            if e["kind"] == "weekly_reset":
                next_weekly = parse_ts(e["ts_bjt"]).replace(tzinfo=BJT)
                break
        if next_weekly:
            break
    if now.weekday() == 0:
        add("annihilation_weekly",
            "今日（周一）04:00 已周切：本周（%s 起）剿灭额度刷新 —— 未跑剿灭的号本周内跑 1 次即可（AUTO-MAS 按 ISO 周记账）；"
            "发车走早班窗口 05:00–05:33（晚班 19:30–20:03），避开让位区 04:00–05:33 / 18:30–20:30。"
            % now.date().isoformat(),
            "AUTO-MAS AutoProxy.py L82-98/L304-318；docs/SCHEDULER.md L18；docs/MAA_EVENT_FARMING_PLAYBOOK.md §5.1")
    elif next_weekly and next_weekly <= horizon:
        add("annihilation_weekly",
            "下周一 04:00（%s）周切：剿灭额度将重置 —— 本周还没跑剿灭的号，最晚在周日晚（20:30 后到 04:00 前）补跑；"
            "否则要等下周一早班窗口。" % fmt(next_weekly),
            "AUTO-MAS AutoProxy.py L82-98；docs/MAA_EVENT_FARMING_PLAYBOOK.md §5.1")

    # 2) 资源关（今明两天；全开放期优先）
    if cn_days:
        today = cn_days[0]
        nxt = cn_days[1] if len(cn_days) > 1 else None
        if today.get("resource_open_all"):
            from_txt = today.get("resource_open_all_from_bjt") or ""
            if from_txt:
                head = "资源收集「全关卡全天开放」今日 %s 起（BJT）" % from_txt[11:]
                tail = "；%s 前按周表（%s）" % (from_txt[11:], "、".join(today.get("resource_open_base") or []) or "—")
            else:
                head = "资源收集「全关卡全天开放」在期（今日全天）"
                tail = ""
            add("resource_open_all",
                head + "⇒ 任意资源关（CE-6/AP-5/CA-5/SK-5/PR 等）可刷，补龙门币/技能概要/芯片优先排这段窗口" + tail + "。",
                "server_events.json（资源收集全关卡全天开放）+ MAA StageInfo.cs L150-161")
        else:
            nxt_txt = ""
            if nxt:
                if nxt.get("resource_open_all"):
                    nxt_from = nxt.get("resource_open_all_from_bjt") or ""
                    nxt_txt = "；明天（%s %s）：全开放（活动%s）" % (
                        nxt["game_date"], nxt["weekday"], (" %s 起（BJT）" % nxt_from[11:]) if nxt_from else "")
                else:
                    nxt_txt = "；明天（%s %s）：%s" % (nxt["game_date"], nxt["weekday"], "、".join(nxt["resource_open"]))
            add("resource_rotation",
                "今日（%s %s）开放资源关：%s%s。挑关先看当日开放表（排了闭着的关会被整条跳过）。"
                % (today["game_date"], today["weekday"], "、".join(today["resource_open"]), nxt_txt),
                "MAA StageManager.cs L677-695 + StageInfo.cs L148-174；docs/MAA_EVENT_FARMING_PLAYBOOK.md L56/R2")

    # 3) 活动开/关 & 商店截止（72h 内）
    for e in cal["upcoming"]:
        ts = parse_ts(e["ts_bjt"]).replace(tzinfo=BJT)
        if ts > horizon:
            continue
        if any(h in (e.get("event_type") or "") for h in SIGNIN_HINTS):
            add("signin_window",
                "%s %s（%s）：签到按日累计（漏一天少一天），临期别忘每天上号点一次%s。"
                % (SERVERS.get(e["server"], {}).get("short", e["server"]), e["title"], e["ts_bjt"],
                   "（今天是最后一天）" if e["kind"] == "event_end" else ""),
                (e.get("source") or "server_events.json") + "；docs/SERVER_EVENTS.md")
        elif e["kind"] == "event_start":
            blob = (e.get("event_name") or "") + (e.get("event_type") or "") + (e.get("note") or "")
            farming = any(h in (e.get("event_type") or "") for h in FARMING_HINTS) and not any(
                h in (e.get("event_type") or "") for h in NON_FARMING_HINTS)
            tag = "刷关型" if farming else (e.get("event_type") or "活动")
            if any(h in blob for h in OPEN_ALL_HINTS):
                hint = "期间资源关不受当日周表限制，补材料/芯片优先排这段"
            else:
                hint = ("活动关优先/Stage 切换前先核 MAA 活动表是否补录该活动（未补录则要手输关卡代码）；"
                        "若当日有停机维护（通常 10:00–16:00，以公告为准），别把长任务排在维护窗口里")
            add("event_start",
                "%s %s（%s，%s）：%s。" % (SERVERS.get(e["server"], {}).get("short", e["server"]), e["title"],
                                          tag, e["ts_bjt"], hint),
                e.get("source") or "server_events.json")
        elif e["kind"] == "event_end":
            add("event_end",
                "%s %s（%s）：结束前把活动关刷完、药投干净；结束后**立刻**把受影响号的 Stage 改回常驻关 —— "
                "活动关过期不改 = 理智作战静默跳过（SR 教训：46 号白挂 9 天）。"
                % (SERVERS.get(e["server"], {}).get("short", e["server"]), e["title"], e["ts_bjt"]),
                "docs/MAA_EVENT_FARMING_PLAYBOOK.md R1/§5.5")
        elif e["kind"] == "deadline_from_note":
            add("event_shop_deadline",
                "%s（%s）：记得兑换/领取（代币过期作废）。"
                % (e["title"], e["ts_bjt"]), e.get("source") or "server_events.json note")

    # 4) 宿主重启（24h 内）
    for d in cn_days[:2]:
        for e in d["entries"]:
            if e["kind"] != "host_restart":
                continue
            ts = parse_ts(e["ts_bjt"]).replace(tzinfo=BJT)
            if 0 <= rel_hours(ts, now) <= 24:
                add("host_restart_window",
                    "宿主计划重启窗口 %s：跨该时刻的长任务会被吞（孤儿化）；晚班缺口/补跑请在 04:00 前收工，"
                    "别跨重启排长任务。" % e["ts_bjt"],
                    "tools/mas_plan_builder.py L1136（事件 id=1074）+ docs/MAS_SHIFT_PLAYBOOK.md §7")
                break

    # 5) 数据新鲜度
    for w in cal.get("warnings") or []:
        if "快照" in w:
            add("stale_snapshot", w + " ⇒ 据此排期时先重抓。", "docs/SERVER_EVENTS.md §8", level="warn")
    return adv


# ---------------------------------------------------------------- 渲染
def upcoming_line(cal: dict, max_items: int = MAX_UPCOMING) -> str:
    items = cal.get("upcoming") or []
    if not items:
        return "UPCOMING=无（窗口内无已登记事件）"
    parts = []
    for e in items[:max_items]:
        short = SERVERS.get(e["server"], {}).get("short", e["server"])
        parts.append("%s %s:%s（约 %s 小时后）" % (short, e["title"], e["ts_bjt"][:16], e["in_hours"]))
    more = len(items) - max_items
    tail = "；等另有 %d 项在窗内" % more if more > 0 else ""
    return "UPCOMING=" + "；".join(parts) + tail


def check_lines(cal: dict, cal_state: str, max_items: int = MAX_UPCOMING) -> list:
    src = cal.get("events_source") or {}
    age = src.get("age_days")
    age_txt = "缺失" if age is None else ("%.1f 天前" % age)
    L = ["EVENT_CAL=%s（%d 天 / 事件源 %s / 快照 %s）" % (cal_state, cal.get("days"), "server_events" if src.get("loaded") else "缺",
                                                       age_txt)]
    L.append(upcoming_line(cal, max_items))
    adv = cal.get("advice") or []
    L.append("EVENT_ADVICE=%s" % (adv[0]["text"] if adv else "无"))
    return L


def render_md(cal: dict, servers: list) -> str:
    L = []
    L.append("# 周期性事件日历 · 未来 %d 天（%s）" % (cal["days"], cal["generated_at_bjt"]))
    L.append("")
    L.append("生成：`tools/event_calendar.py` ｜ **只读工具**：不改配置、不发车；本文件 = 建议与提醒，执行归 mas_dispatch / run profile。")
    L.append("")
    L.append("事件源：`%s`（快照 %s，%s）" % (
        cal["events_source"]["path"], cal["events_source"]["generated_at_bjt"] or "缺失",
        ("%.2f 天前" % cal["events_source"]["age_days"]) if cal["events_source"]["age_days"] is not None else "年龄未知"))
    L.append("")
    if cal.get("warnings"):
        L.append("## ⚠ 警告")
        L.append("")
        for w in cal["warnings"]:
            L.append("- %s" % w)
        L.append("")
    L.append("## 0. 到点提醒（窗口内）")
    L.append("")
    L.append("```")
    L.append(upcoming_line(cal, max_items=12))
    L.append("```")
    L.append("")
    L.append("## 1. 发车建议（只建议）")
    L.append("")
    for a in cal.get("advice") or []:
        L.append("- **[%s]** %s" % (a["rule"], a["text"]))
        L.append("  - 依据：%s" % a["evidence"])
    if not cal.get("advice"):
        L.append("- （无）")
    L.append("")
    L.append("## 2. 周期规则表（机器口径）")
    L.append("")
    L.append("| 事件 | 周期/时刻（服务器本地） | 影响什么 | 依据来源 | 置信 |")
    L.append("|---|---|---|---|---|")
    for r in cal["rules"]:
        L.append("| %s | %s | %s | %s | %s |" % (r["event"], r["cycle"], r["impact"], r["source"], r["confidence"]))
    L.append("")
    L.append("## 3. 资源关周表（MAA 当日开放判定）")
    L.append("")
    L.append("| 关 | 产出 | 开放日（服务器本地） |")
    L.append("|---|---|---|")
    for s in cal["resource_stage_table"]:
        L.append("| %s | %s | %s |" % (s["code"], s["material"], s["open_days_cn"]))
    L.append("")
    L.append("## 4. 逐日日历")
    L.append("")
    for key in servers:
        block = cal["per_server"].get(key)
        if not block:
            continue
        L.append("### %s（%s，日切 04:00）" % (block["name"], block["tz"]))
        L.append("")
        L.append("| 日期 | 星期 | 资源关（当日开放） | 事件（服务器本地 / 北京时间） |")
        L.append("|---|---|---|---|")
        for d in block["days"]:
            ev_txt = []
            hr_times = []
            for e in d["entries"]:
                if e["kind"] == "weekly_reset":
                    ev_txt.append("**周切**（剿灭额度/周任务）")
                elif e["kind"] == "host_restart":
                    hr_times.append(e["ts_bjt"][11:])
                else:
                    ev_txt.append("%s（%s / %s）" % (e["title"], e["ts_local"][11:], e["ts_bjt"]))
            if hr_times:
                ev_txt.append("宿主重启窗口 %s" % "、".join(hr_times))
            if d["resource_open_all"]:
                if d.get("resource_open_all_from_bjt"):
                    res = "全开放（活动 %s BJT 起；此前 %s）" % (
                        d["resource_open_all_from_bjt"][11:],
                        "、".join(d.get("resource_open_base") or []) or "—")
                else:
                    res = "全开放（活动全天）"
            else:
                res = "、".join(d["resource_open"])
            L.append("| %s | %s | %s | %s |" % (d["game_date"], d["weekday"], res, "；".join(ev_txt) or "—"))
        L.append("")
    L.append("## 5. 商店/奖励截止清单（note 自动抽取；含窗外）")
    L.append("")
    for d in cal.get("deadlines") or []:
        L.append("- %s %s ｜ %s%s" % (SERVERS.get(d["server"], {}).get("short", d["server"]), d["ts_bjt"], d["title"],
                                      "" if d.get("in_window") else "（14 天窗外）"))
    if not cal.get("deadlines"):
        L.append("- （无）")
    L.append("")
    L.append("## 6. 口径与边界")
    L.append("")
    L.append("- 日切/周切按**服务器本地时间** 04:00；北京时间换算：CN/TW = 本地；JP/KR = 本地 − 1h；EN(UTC-7) = 本地 + 15h。")
    L.append("- 资源关开放表 = MAA 侧口径（MAA 只认它的表；活动期「全关卡全天开放」时全开）。")
    L.append("- 活动条目来自快照（可能过期）；快照 >3 天建议重抓、>7 天本工具会告警。")
    L.append("- note 截止时间在快照里是**该服本地时间**；本工具已换算 BJT（JP/KR −1h；EN +15h；CN/TW 同刻）。")
    L.append("- 索引：`docs/EVENT_CALENDAR.md`（规则表人读版 + 接线说明）；`docs/SERVER_EVENTS.md`（活动抓取流程）。")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- 输出
def write_outputs(cal: dict, servers: list, out_dir: str) -> tuple:
    os.makedirs(out_dir, exist_ok=True)
    jp = os.path.join(out_dir, "event_calendar.json")
    mp = os.path.join(out_dir, "event_calendar.md")
    with open(jp + ".tmp", "w", encoding="utf-8") as f:
        json.dump(cal, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(jp + ".tmp", jp)
    with open(mp + ".tmp", "w", encoding="utf-8") as f:
        f.write(render_md(cal, servers))
    os.replace(mp + ".tmp", mp)
    return jp, mp


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.selftest:
        rep = selftest()
        if args.json:
            print(json.dumps(rep, ensure_ascii=False))
        else:
            print("SELFTEST=%s (%d/%d)" % ("OK" if rep["ok"] else "FAIL", rep["passed"], rep["passed"] + rep["failed"]))
            for c in rep["cases"]:
                print("  %s %s%s" % ("PASS" if c["ok"] else "FAIL", c["name"],
                                     "" if c["ok"] else "  ← " + c["detail"]))
        return 0 if rep["ok"] else 3

    servers = [s.strip() for s in (args.servers or "").split(",") if s.strip()]
    servers = [event_key_of(s) or s for s in servers]
    events_path = args.events or os.path.join(ROOT, "runtime", "reports", "server_events.json")
    out_dir = args.out_dir or os.path.join(ROOT, "runtime", "reports")
    try:
        now = now_bjt(args.now)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    events_loaded = os.path.exists(events_path)
    try:
        events = load_events(events_path) if events_loaded else {}
        cal = build_calendar(now, args.days, servers, events, events_path, events_loaded,
                             span_hours=args.window_hours)
    except Refuse as e:
        if args.check:
            print("EVENT_CAL=UNKNOWN:%s" % str(e)[:180])
            print("UPCOMING=UNKNOWN:%s" % str(e)[:180])
            print("EVENT_ADVICE=UNKNOWN:%s" % str(e)[:180])
        else:
            print("拒绝：%s" % e, file=sys.stderr)
        return 2

    # 状态判定（fail-closed 口径见 docstring）
    if not events_loaded:
        cal_state = "DEGRADED:活动快照缺失（仅周期性事件）"
    elif (cal["events_source"].get("age_days") or 0) > 7:
        cal_state = "DEGRADED:活动快照过期（%.1f 天）" % cal["events_source"]["age_days"]
    else:
        cal_state = "OK"
    cal["state"] = cal_state

    if args.check:
        for ln in check_lines(cal, cal_state):
            print(ln)
        return 0

    if args.json:
        print(json.dumps(cal, ensure_ascii=False))
    else:
        print("=== 周期性事件日历 · 未来 %d 天（%s）｜%s ===" % (cal["days"], cal["generated_at_bjt"], cal_state))
        for ln in check_lines(cal, cal_state, max_items=8):
            print(ln)
        if not args.no_advice:
            print("-- 发车建议（只建议，不执行）--")
            for a in cal["advice"]:
                print("  [%s] %s" % (a["rule"], a["text"]))
        if not args.no_write:
            jp, mp = write_outputs(cal, servers, out_dir)
            print("日历：%s" % os.path.relpath(mp, ROOT).replace("\\", "/"))
            print("数据：%s" % os.path.relpath(jp, ROOT).replace("\\", "/"))
    if not args.no_write and args.json:
        jp, mp = write_outputs(cal, servers, out_dir)
    return 0


# ---------------------------------------------------------------- 自测
FIXTURE_EVENTS = {
    "schema": "maaorch.server_events/1",
    "generated_at_bjt": "2026-09-28 04:49:45+08:00",
    "generated_at_epoch": 1790542185,
    "servers": {
        "cn_official": {
            "active_events": [
                {"name": "「逐影集趣」限时活动", "type": "限时活动（小活动）",
                 "start_bjt": "2026-09-20 16:00", "end_bjt": "2026-09-30 03:59",
                 "source": "cn-official-2819", "note": "活动商店【逐影之礼】开放到 10-03 03:59。"},
                {"name": "稳态测定 签到活动", "type": "签到", "start_bjt": "2026-09-28 04:00",
                 "end_bjt": "2026-10-08 03:59", "source": "cn-official-2819", "note": "累计 7 日。"},
            ],
            "upcoming_events": [
                {"name": "矢量突破#3「拟生态」", "type": "限时活动（爬塔类刷关）",
                 "start_bjt": "2026-09-29 16:00", "end_bjt": "2026-10-20 03:59", "source": "cn-official-1455",
                 "note": "【突破里程碑】到 10-23 03:59；解锁条件 通关主线 2-10。"},
                {"name": "「资源收集」全关卡全天开放", "type": "常驻资源关加成",
                 "start_bjt": "2026-09-29 16:00", "end_bjt": "2026-10-20 03:59", "source": "cn-official-1455"},
            ],
            "ended_events": [
                {"name": "SideStory「月行水上」", "type": "联动 SideStory", "start_bjt": "2026-09-04 12:00",
                 "end_bjt": "2026-09-25 03:59", "source": "cn-official-news"},
            ],
        },
        "jp": {
            "active_events": [
                {"name": "サイドストーリー「人々、我々」", "type": "SideStory（刷关）",
                 "start_bjt": "2026-09-16 15:00", "end_bjt": "2026-09-30 02:59", "source": "jp-official-4626",
                 "note": "報酬受取到 10-07 03:59。"},
            ],
            "upcoming_events": [
                {"name": "資源調達 全ステージ開放", "type": "常駐資源ステージ開放",
                 "start_bjt": "2026-10-05 15:00", "end_bjt": "2026-10-20 02:59", "source": "fixture"},
            ],
            "ended_events": [],
        },
        "kr": {"active_events": [], "upcoming_events": [], "ended_events": []},
        "tw": {"active_events": [], "upcoming_events": [], "ended_events": []},
        "en": {
            "active_events": [
                {"name": "SIDESTORY: PEOPLE, A PEOPLE", "type": "SideStory（刷关）",
                 "start_bjt": "2026-09-16 23:00", "end_bjt": "2026-09-30 18:59", "source": "en-official-4635",
                 "note": "部分奖励/商店到 10-07 03:59（UTC-7）。"},
            ],
            "upcoming_events": [], "ended_events": [],
        },
    },
}


def selftest() -> dict:
    import tempfile
    cases = []

    def check(name, ok, detail=""):
        cases.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})

    tmp = tempfile.mkdtemp(prefix="event_calendar_selftest_")
    ev_path = os.path.join(tmp, "server_events.json")
    with open(ev_path, "w", encoding="utf-8") as f:
        json.dump(FIXTURE_EVENTS, f, ensure_ascii=False)
    now = now_bjt("2026-09-28 09:30")          # 周一，官服
    servers = SERVER_ORDER

    cal = build_calendar(now, 14, servers, FIXTURE_EVENTS, ev_path, True)
    check("日历 14 天窗口 = 09-28~10-11", [cal["per_server"]["cn_official"]["days"][0]["game_date"],
                                          cal["per_server"]["cn_official"]["days"][-1]["game_date"]] ==
          ["2026-09-28", "2026-10-11"],
          [cal["per_server"]["cn_official"]["days"][0]["game_date"], cal["per_server"]["cn_official"]["days"][-1]["game_date"]])

    def entries_of(server_key, date_s, kind=None):
        for d in cal["per_server"][server_key]["days"]:
            if d["game_date"] == date_s:
                return [e for e in d["entries"] if kind is None or e["kind"] == kind]
        return []

    # 已知事件（对照 docs/SERVER_EVENTS.md §1.1/§1.2）
    e = entries_of("cn_official", "2026-09-29", "event_start")
    check("09-29 有矢量突破#3 开启 16:00",
          any("矢量突破#3" in x["title"] and x["ts_bjt"] == "2026-09-29 16:00" for x in e), [x["title"] for x in e])
    e = entries_of("cn_official", "2026-09-30", "event_end")
    check("09-30 逐影集趣 03:59 结束", any("逐影集趣" in x["title"] and x["ts_bjt"] == "2026-09-30 03:59" for x in e),
          [x["ts_bjt"] for x in e])
    e = entries_of("cn_official", "2026-10-08", "event_end")
    check("10-08 稳态测定 03:59 结束", any("稳态测定" in x["title"] and x["ts_bjt"] == "2026-10-08 03:59" for x in e),
          [x["ts_bjt"] for x in e])
    d = entries_of("cn_official", "2026-10-05", "weekly_reset")
    check("10-05（下周一）有周切条目", len(d) == 1 and d[0]["ts_bjt"] == "2026-10-05 04:00", d)
    # 跨服时区换算：日服 PA 结束 = 09-30 03:59 JST = 02:59 BJT
    d = cal["per_server"]["jp"]["days"]
    pa_end = [x for day in d for x in day["entries"] if x["kind"] == "event_end" and "人々" in x["title"]]
    check("日服 PA 结束 = BJT 02:59（JST-1h）", len(pa_end) == 1 and pa_end[0]["ts_bjt"] == "2026-09-30 02:59",
          [x["ts_bjt"] for x in pa_end])
    # 资源关表（09-28 周一 / 09-29 周二）
    res_mon = cal["per_server"]["cn_official"]["days"][0]["resource_open"]
    check("周一开放含 AP-5/SK-5/LS-6、不含 CE-6", "AP-5" in res_mon and "SK-5" in res_mon and "LS-6" in res_mon
          and "CE-6" not in res_mon, res_mon)
    # 周二周表（纯函数口径）；09-29 当日起 16:00 进入「全开放」，故这里断言周表本身
    tue_codes, _ = resource_open_for(dt.date(2026, 9, 29), False)
    check("周二周表含 CE-6/CA-5、不含 AP-5", "CE-6" in tue_codes and "CA-5" in tue_codes and "AP-5" not in tue_codes,
          tue_codes)
    # 全开放（09-29 16:00 起）：09-29 当日=部分（16:00 起），09-30 起=全天全开放
    d29 = cal["per_server"]["cn_official"]["days"][1]
    check("09-29 资源关=全开放（16:00 起）", d29["resource_open_all"] is True
          and len(d29["resource_open"]) == len(RESOURCE_STAGES) and d29["resource_open_all_from_bjt"].endswith("16:00"),
          (d29["resource_open_all"], len(d29["resource_open"]), d29.get("resource_open_all_from_bjt")))
    check("09-29 基表（16:00 前）含 CE-6/CA-5、不含 AP-5",
          "CE-6" in (d29.get("resource_open_base") or []) and "AP-5" not in (d29.get("resource_open_base") or []),
          d29.get("resource_open_base"))
    d30 = cal["per_server"]["cn_official"]["days"][2]
    check("09-30 起资源关=全开放（全天）", d30["resource_open_all"] is True and not d30.get("resource_open_all_from_bjt")
          and len(d30["resource_open"]) == len(RESOURCE_STAGES),
          (d30["resource_open_all"], d30.get("resource_open_all_from_bjt"), len(d30["resource_open"])))
    # 多语言 hint：日服「全ステージ開放」也须识别为全开放（10-05 15:00 BJT 起 = 16:00 JST）
    jp05 = [d for d in cal["per_server"]["jp"]["days"] if d["game_date"] == "2026-10-05"]
    check("日服「全ステージ開放」命中全开放（多语言 hint）",
          bool(jp05) and jp05[0]["resource_open_all"] is True
          and jp05[0].get("resource_open_all_from_bjt", "").endswith("15:00"),
          str((jp05[0]["resource_open_all"], jp05[0].get("resource_open_all_from_bjt"))) if jp05 else "")
    # note 截止抽取（10-23 在 14 天窗外 -> 不在 upcoming，但应在 deadlines 全表里）
    check("note 截止抽取：突破里程碑 10-23 03:59",
          any("10-23 03:59" in x["ts_bjt"] and "矢量突破#3" in x["title"] for x in cal["deadlines"]),
          cal["deadlines"])
    check("note 截止抽取：逐影之礼 10-03 03:59",
          any(x["ts_bjt"] == "2026-10-03 03:59" for x in cal["deadlines"]), cal["deadlines"])
    # note 内为该服本地时间 -> 换算 BJT（JP −1h / EN +15h）
    check("note 截止：日服按 JST→BJT 换算（10-07 03:59 → 02:59）",
          any(x["ts_bjt"] == "2026-10-07 02:59" and "人々" in x["title"] for x in cal["deadlines"]),
          str([x["ts_bjt"] for x in cal["deadlines"] if "人々" in x["title"]]))
    check("note 截止：国际服按 UTC-7→BJT 换算（10-07 03:59 → 18:59）",
          any(x["ts_bjt"] == "2026-10-07 18:59" for x in cal["deadlines"]),
          str([x["ts_bjt"] for x in cal["deadlines"]]))
    # UPCOMING（48h 窗口）
    line = upcoming_line(cal)
    check("UPCOMING 行含矢量突破#3 16:00", line.startswith("UPCOMING=") and "矢量突破#3「拟生态」开启:2026-09-29 16:00" in line, line)
    check("UPCOMING 行含逐影集趣结束", "逐影集趣" in upcoming_line(cal, max_items=12), upcoming_line(cal, max_items=12))
    # 建议
    adv_ids = [a["rule"] for a in cal["advice"]]
    check("建议含 annihilation/resource/event_start/event_end",
          {"annihilation_weekly", "resource_rotation", "event_start", "event_end"} <= set(adv_ids), adv_ids)
    check("周一建议指向早班窗口 05:00–05:33",
          any("05:00–05:33" in a["text"] for a in cal["advice"] if a["rule"] == "annihilation_weekly"), cal["advice"])
    # check 行
    lines = check_lines(cal, "OK")
    check("check 三行格式", lines[0].startswith("EVENT_CAL=OK") and lines[1].startswith("UPCOMING=")
          and lines[2].startswith("EVENT_ADVICE="), lines)
    # md 渲染
    md = render_md(cal, servers)
    check("md 含规则表/资源表/逐日", "周期规则表" in md and "资源关周表" in md and "逐日日历" in md, md[:120])
    check("md 信封（14 天）", "未来 14 天" in md)
    check("md 部分覆盖日标注（此前 周表）", "此前 CE-6" in md and "全开放（活动 16:00 BJT 起" in md,
          str([ln for ln in md.splitlines() if "2026-09-29" in ln][:1]))

    # 快照缺失 -> DEGRADED（fail-soft，不是 UNKNOWN）
    cal2 = build_calendar(now, 14, ["cn_official"], {}, os.path.join(tmp, "nope.json"), False)
    check("快照缺失 -> 仍出周期性日历 + 警告", bool(cal2["per_server"]["cn_official"]["days"])
          and any("快照缺失" in w for w in cal2["warnings"]), cal2["warnings"])
    check("快照缺失 -> UPCOMING 行不给 UNKNOWN（降级但可用）",
          upcoming_line(cal2).startswith("UPCOMING=") and "UNKNOWN" not in upcoming_line(cal2), upcoming_line(cal2))
    ld = check_lines(cal2, "DEGRADED:活动快照缺失")
    check("快照缺失 -> check 首行 DEGRADED", ld[0].startswith("EVENT_CAL=DEGRADED"), ld)
    # 过期快照 -> DEGRADED:过期
    cal3 = build_calendar(now, 14, ["cn_official"], dict(FIXTURE_EVENTS, generated_at_bjt="2026-09-10 00:00:00+08:00"),
                          ev_path, True)
    check("快照 >7 天 -> 警告", any(">7 天" in w for w in cal3["warnings"]), cal3["warnings"])
    # 未知服别 -> Refuse（fail-closed）
    try:
        build_calendar(now, 14, ["nope"], FIXTURE_EVENTS, ev_path, True)
        check("未知服别 -> Refuse", False, "没有拒绝")
    except Refuse as ex:
        check("未知服别 -> Refuse", "未知服别" in str(ex), str(ex))
    # --check 的 fail-closed：模拟 events 文件损坏
    bad = os.path.join(tmp, "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{not-json")
    try:
        load_events(bad)
        check("坏快照 -> Refuse", False, "没有拒绝")
    except Refuse as ex:
        check("坏快照 -> Refuse", "不可读" in str(ex), str(ex))
    # --now 解析
    check("--now 解析", now_bjt("2026-09-28 09:30").strftime(STAMP_FMT) == "2026-09-28 09:30")
    # 主入口 --check 不写盘（零写）
    rc = main(["--check", "--now", "2026-09-28 09:30", "--events", ev_path, "--days", "14"])
    check("--check 主入口 rc=0", rc == 0, rc)

    passed = sum(1 for c in cases if c["ok"])
    return {"ok": passed == len(cases), "passed": passed, "failed": len(cases) - passed, "cases": cases}


if __name__ == "__main__":
    sys.exit(main())
