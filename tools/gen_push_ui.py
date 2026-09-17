#!/usr/bin/env python3
"""生成推图 Agent 的界面（单文件 HTML，仿方舟官方进度页）。

为什么要"生成"而不是"页面自己读文件":
  file:// 下浏览器禁止 fetch 本地文件（CORS），所以由本脚本把日志/状态**内联**进
  HTML；刷新页面 = 重跑一次本脚本（`python tools/gen_push_ui.py`）。
  页面同时支持把日志/JSON 直接拖进去（不开服务也能看）。

数据来源（只读，不动任何后端）:
  · 任务目录里的 auto_push.log / maa_*.log（推图 Agent 的实时日志）
  · data/workspaces/<账号>/… 或 work/batch54_log.json（历史记录，有则用）

用法: python tools/gen_push_ui.py [--account 9036] [--event act54side] [--sanity 142]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import pathlib
import re
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
OUT = PROJ / "ui" / "push_agent.html"
TOKEN_FILE = PROJ / "ui" / "control_token.txt"   # ui/control_server.py 写的；局域网访问才用得上
LOG_DIRS = [
    pathlib.Path(r"C:\Users\xiach\.codex\visualizations\2026\09\16"
                 r"\01a0aaf9-9a07-7931-b068-f38e4064ea0f\sr54"),
    PROJ / "work",
]
# 多账号（2026-09-17）：--log-key 指定后，只认文件名里带这个键的日志（不带键的是 9036 的老日志）
LOG_KEY = ""
STATE_OVERRIDE: pathlib.Path | None = None   # 多账号：指定读哪个 push_state
OUT_OVERRIDE: pathlib.Path | None = None     # 多账号：指定写到哪个 HTML


def control_token() -> str:
    """读重试通道的 token（有就内联进页面 —— 否则手机上点了会 401）。"""
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

# ── 已确认的通关状态（今晚实测；后续接 data/workspaces 的真实状态）────────
STATES = {
    "SR-1": 3, "SR-2": 3, "SR-3": 3, "SR-4": 3, "SR-5": 3, "SR-6": 3, "SR-7": 3,
    "SR-8": "done-unconfirmed",
    **{f"EX-{i}": "locked" for i in range(1, 9)},
}
STATE_FILES = [
    # 旧共享 push_state.json 已废弃（多账号互相覆盖的根源）——别再列它：
    # 一旦它被误建回来，任何账号都会被读成它的内容。
    PROJ / "work" / "push_state.json",
]

# 多账号切换条（2026-09-17 用户："相互之间不能切换嘛，来回敲网址很慢"）:
# 每个账号起一个 `ui/control_server.py --port 889x`，页面顶上给三个链接，一键互跳。
ACCOUNTS_UI = (
    ("8890", "32822410", "「1」B服"),
    ("8891", "394162955", "V 官服"),
    ("8892", "SquidP", "SquidP 官服"),
)


def acct_nav(current: str) -> str:
    """账号切换条：当前账号高亮、其余可点（样式交给 CSS 的 `.acctnav a`）。"""
    out = []
    for port, uid, name in ACCOUNTS_UI:
        cls = "on" if uid == current else ""
        out.append(f'<a class="{cls}" href="http://127.0.0.1:{port}/">{name}</a>')
    return "\n".join(out)


def load_push_state() -> dict:
    """读 auto_push 落下的 push_state.json（六种状态）。没有就退回内置快照。"""
    files = ([STATE_OVERRIDE] if STATE_OVERRIDE else []) + STATE_FILES
    for p in files:
        if p is None:
            continue
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {"stages": {}}


def box_info_for(account: str) -> str:
    """页面顶部显示"挑作业用的干员名单（box）"有多新。

    为什么（2026-09-17 用户："这个网站没有扫描干员的按钮"）: 挑作业靠 box（"用算不用试"），
    过期或没有就只能瞎试 ⇒ 得让人一眼看到 box 是哪一份、多少人、什么时候读的。
    """
    files = sorted((PROJ / "data").glob(f"operbox_{account}_*.json"),
                   key=lambda p: p.stat().st_mtime)
    if not files:
        return "box：还没有（点右边「扫描干员」）"
    p = files[-1]
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        n = len(d.get("own") or []) if isinstance(d, dict) else len(d or [])
    except Exception:                                        # noqa: BLE001
        n = 0
    ts = dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
    return f"box：{n} 人 ｜ {ts}"


def cell(name: str, rec: dict, cands: list | None = None) -> str:
    """一个六边形格子。状态 → (class, 星/符号, 备注, 悬浮提示)。"""
    st = str(rec.get("status") or "未打")
    job = rec.get("job_used") or ""
    att = rec.get("attempts") or 0
    tip = f"{name} ｜ {st}" + (f" ｜ {job}" if job else "") + (f" ｜ 第 {att} 次" if att else "")
    if st == "三星":
        cls, marks, note = "n3", "★★★", "三星"
    elif st == "通关":
        # 通关了但星级没核（多账号接手别人的进度时会出现）—— 不能瞎写三星，也别当没打
        cls, marks, note = "nd", "◈", "通关"
    elif st == "二星":
        # 通关了但没三星（MAA 打完只保证"过"，星级要看奖章）—— 单独一档，别混进三星
        cls, marks, note = "n2", "★★☆", "二星"
    elif st == "失败":
        cls, marks, note = "nbad", "⚠", str(rec.get("reason") or "失败")
    elif st == "未确认":
        # 跑完了但**没看到结算页**（2026-09-17 新增档）—— 没证据说通关（不写三星），
        # 也没看到败仗（不写"失败"）：一个问号 + 人话，调度按"未完成"下次重试。
        cls, marks, note = "nq", "？", str(rec.get("reason") or "未确认")
    elif st == "战斗中":
        cls, marks, note = "nd npulse", "◈", "战斗中"
    elif st == "跳过":
        cls, marks, note = "nl", "—", "跳过"
    elif st == "未解锁":
        cls, marks, note = "nl", "🔒", "未解锁"
    else:
        cls, marks, note = "n0", "☆☆☆", "未打"
    if att > 1 and note in ("三星", "未打"):
        note = f"{note}·{att}试"
    # 失败格上挂「重试」：点一下 = 让 Agent 自己去打这一关（走 ui/control_server.py 的本地通道）
    # **每一格都要能点**（2026-09-17 用户："我连让他打都没有办法让他打"）:
    # 记录里的「未解锁」也可能是错的（上一关其实通了、只是判定出过错）⇒ 未解锁也挂按钮，
    # 让人能强行试一把；真锁着的话游戏自己会拒绝，不会白花理智。
    # **每行只有两个动作**（2026-09-17 用户："这个是否直接打，其实是个总开关"）:
    # 「▶ 打」按**顶部开关**走（目标：普通/突袭 ｜ 路径：自动导航/就在这一屏），
    # 「↻ 换作业」= 同设置下换下一份候选。三个轴拆成开关以后，
    # "直接打 + 突袭"、"直接打 + 换作业"这些组合才点得出来。
    btn = f'<button class="rb go" onclick="run(\'{name}\')">▶ 打</button>'
    raid_st = str(rec.get("raid_status") or "")
    if raid_st:
        note = f"{note}·突袭{raid_st}"
    nbtn = ("" if st == "未解锁" else
            f'<button class="rb next" onclick="nextjob(\'{name}\')" '
            f'title="同设置下换一份作业（连点就沿候选列表一份份换）">↻ 换作业</button>')
    # ⚡ **快速入口**：一排「▶ 作业号」——点了就调**现有的** maa_copilot_run.py 跑那一份。
    #   缺人的照旧可点（MAA 会借助战补：实测 9036 借「圣聆初雪」一次通关 EX-7）。
    picks = ""
    if cands:
        btns = []
        for num, n_miss, rel in cands:
            label = num + ((" 缺" + str(n_miss)) if n_miss else "")
            cls2 = "chip warn" if n_miss else "chip"
            # onclick 里必须是 `runone('路径')` —— **别再改回字符串拼接**：2026-09-17 实测
            # 一次改动把转义写成了 `'' + rel + ''`，于是属性里落了字面量 `runone( + rel + )`
            # ⇒ 点一下就是 JS 语法错误、静默无事发生（用户："我点这个为什么没有反应"）。
            btns.append(f'<button class="{cls2}" onclick="runone(\'{rel}\')"'
                        f' title="{rel}">▶ {label}</button>')
    else:
        picks = '<span class="empty">没有可用作业（先「扫描干员」）</span>'
    # **现场搜**按钮（用户 2026-09-17《现场搜 + 码直跑》）：点了才去作业站搜，不预加载
    _q = chr(39)          # 单引号用 chr 拼，避免任何转义踩坑（已经被吃过两次）
    sbtn = ('<button class="rb" onclick="searchjobs(' + _q + name + _q + ')" '
            'title="现场去作业站搜这一关（按本号 box 评估）；只在点它时才搜">🔍 搜</button>')
    if not cands:
        picks = '<span class="empty">没有可用作业（先「扫描干员」，或点 🔍 搜站上）</span>'
    else:
        picks = '<div class="picks">' + "".join(btns) + '</div>'
    pill_cls = {"三星": "ok", "二星": "ok", "通关": "ok", "未确认": "warn",
                "失败": "bad", "跳过": "mute", "未解锁": "mute", "未打": "mute"}.get(st, "mute")
    meta = (job or "—") + ((" · 第 " + str(att) + " 次") if att else "")
    return (f'<div class="card {cls}"><div class="hd"><span class="id">{name}</span>'
            f'<span class="pill {pill_cls}">{note}</span></div>'
            f'<div class="meta">{meta}</div>{picks}'
            f'<div class="acts">{btn}{nbtn}{sbtn}</div></div>')


def fix_mojibake(s: str) -> str:
    """修 PowerShell 重定向写坏的中文（UTF-8 字节被当 GBK 写）。"""
    try:
        fixed = s.encode("gbk", errors="strict").decode("utf-8", errors="strict")
        return fixed if sum(1 for c in fixed if "\u4e00" <= c <= "\u9fff") > 3 else s
    except Exception:
        return s


def read_text_any(p: pathlib.Path) -> str:
    """按"能读通就用"的顺序解编码 —— 踩过的坑（2026-09-17 实证）:
    PowerShell 的 `*>>` 把日志写成 **UTF-16LE（带 BOM）**，用 UTF-8 读整份是乱码
    （原始行长这样 '\x00=\x00=\x00='），于是"事件 0 条"。所以这里先探编码。
    """
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "gbk"):
        try:
            t = raw.decode(enc)
        except Exception:
            continue
        if t.count("\x00") == 0 and sum(1 for c in t[:400] if c.isprintable()) > 10:
            return t
    return raw.decode("utf-8", errors="replace")


def find_logs() -> list[pathlib.Path]:
    out = []
    for d in LOG_DIRS:
        if d.is_dir():
            out += sorted(d.glob("auto_push*.log")) + sorted(d.glob("maa_*.log"))
    # 多账号：这个号的页面只认带自己键的日志；9036（不传键）排除别的号（键形如 emu0/emu1）
    out = ([p for p in out if LOG_KEY in p.name] if LOG_KEY
           else [p for p in out if "_emu" not in p.name])
    # 按修改时间取最近的（auto_push.log 是最重要的那本）
    return sorted(out, key=lambda p: p.stat().st_mtime)[-14:]


def read_events(limit: int = 26) -> list[tuple[str, str]]:
    """→ [(时间, 文本)]，来自各日志里看得懂的行。"""
    rows: list[tuple[str, str]] = []
    for p in find_logs():
        try:
            txt = read_text_any(p)
        except Exception:
            continue
        for line in txt.splitlines():
            line = fix_mojibake(line).strip()
            if not line or line.startswith("---"):
                continue
            # 两种时间格式都要认：[01:37:07]（脚本日志）与
            # [2026-09-17 01:25:14.123]（MaaCore 日志）
            m = re.search(r"(\d{2}:\d{2}:\d{2})", line)
            if m:
                rows.append((m.group(1), line[m.end():].lstrip(" ].")[:90]))
            elif line.startswith(("SR-", "===", "   SR-")):
                rows.append(("", line[:90]))
    return rows[-limit:]


def read_attempts() -> dict[str, list[tuple[str, str]]]:
    """从 maa_<关卡>_<作业>.log 的文件名 + 内容判失败原因。"""
    out: dict[str, list[tuple[str, str]]] = {}
    for p in find_logs():
        # 多账号：文件名可能是 maa_<账号键>_SR-8_<作业>.log
        m = re.match(r"maa_(?:[A-Za-z0-9]+_)?(SR-\d+|EX-\d+)_(.+)\.log$", p.name)
        if not m:
            continue
        stage, job = m.group(1), m.group(2)
        try:
            txt = read_text_any(p)
        except Exception:
            txt = ""
        if "CONNECT_FAIL" in txt:
            why = "连接失败"
        elif "BattleFormationTask" in txt and "SubTaskError" in txt:
            why = "编队缺人"
        elif "SubTaskError" in txt:
            why = "执行出错"
        elif "AllTasksCompleted" in txt:
            why = "战斗完成"
        else:
            why = "未完成"
        out.setdefault(stage, []).append((job, why))
    return out


HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>推图 Agent · {account} · {event}</title>
<style>
:root{{--bg:#0e1014;--card:#171b21;--card2:#1e242c;--line:#2a323c;--line2:#39424e;
  --txt:#e9edf2;--dim:#8d99a8;--dim2:#5d6876;--accent:#f2a33c;
  --ok:#63c06a;--warn:#e3a944;--bad:#e2665f;--info:#5aa9e6}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--txt);
  font:14px/1.6 "Inter","Source Han Sans SC","Noto Sans CJK SC","Microsoft YaHei",system-ui,sans-serif;
  padding:30px 34px 80px;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1260px;margin:0 auto}}
.top{{display:flex;align-items:flex-start;gap:20px;flex-wrap:wrap;padding-bottom:18px;
  border-bottom:1px solid var(--line)}}
.brand{{font-weight:800;font-size:21px;letter-spacing:.01em}}
.brand em{{font-style:normal;color:var(--accent)}}
.sub{{color:var(--dim);font-size:12.5px;margin-top:4px}}
.sub b{{color:var(--txt)}}
.kpis{{display:flex;gap:10px;margin-left:auto}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 16px;min-width:96px}}
.kpi b{{display:block;font:700 19px/1.25 ui-monospace,"Cascadia Mono",Consolas,monospace}}
.kpi b i{{font-style:normal;color:var(--dim2);font-size:13px}}
.kpi span{{font-size:11px;color:var(--dim);letter-spacing:.06em}}
.bar{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:16px 0 18px}}
.seg{{display:inline-flex;align-items:center;background:var(--card);border:1px solid var(--line);
  border-radius:9px;padding:3px}}
.seg .lbl{{color:var(--dim2);font-size:11.5px;padding:0 9px 0 7px;letter-spacing:.06em}}
.seg label{{cursor:pointer}}
.seg label input{{display:none}}
.seg label i{{display:inline-block;font-style:normal;padding:4px 12px;border-radius:6px;
  font-size:12.5px;color:var(--dim);transition:.15s}}
.seg label input:checked + i{{background:var(--accent);color:#191308;font-weight:700}}
.seg label:hover i{{color:var(--txt)}}
.bar-right{{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.btn{{background:var(--card);color:var(--txt);border:1px solid var(--line2);border-radius:8px;
  padding:6px 13px;font:600 12.5px/1.4 inherit;cursor:pointer;transition:.15s}}
.btn:hover{{background:var(--card2);border-color:#4a5563}}
.btn.danger{{color:#f0a0a0;border-color:#4a2b2b;background:#201618}}
.btn.danger:hover{{background:#2c1c1f}}
.acctnav a{{color:var(--dim);text-decoration:none;font-size:12.5px;padding:5px 11px;
  border-radius:999px;border:1px solid var(--line)}}
.acctnav a:hover{{color:var(--txt);border-color:var(--line2)}}
.acctnav a.on{{color:#191308;background:var(--accent);border-color:var(--accent);font-weight:700}}
.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}
@media (max-width:1100px){{.grid{{grid-template-columns:repeat(3,minmax(0,1fr))}}}}
@media (max-width:820px){{.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 13px;
  display:flex;flex-direction:column;gap:8px;border-left:3px solid var(--line2)}}
.card:hover{{border-color:var(--line2)}}
.card.n3,.card.n2{{border-left-color:var(--ok)}}
.card.nbad{{border-left-color:var(--bad)}}
.card.nq{{border-left-color:var(--warn)}}
.card.nd{{border-left-color:var(--info)}}
.card.nl{{border-left-color:#333b45;opacity:.62}}
.card .hd{{display:flex;align-items:center;gap:8px;min-width:0}}
.card .id{{white-space:nowrap;font:800 16px/1.2 ui-monospace,"Cascadia Mono",Consolas,monospace}}
.card .meta{{font-size:11.5px;color:var(--dim2);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;min-height:17px}}
.pill{{max-width:58%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;padding:2px 9px;border-radius:999px;border:1px solid var(--line2);
  color:var(--dim);white-space:nowrap}}
.pill.ok{{color:var(--ok);border-color:#2f5b34;background:#16241a}}
.pill.bad{{color:var(--bad);border-color:#5a2f2c;background:#241618}}
.pill.warn{{color:var(--warn);border-color:#5b4622;background:#241d13}}
.picks{{display:flex;flex-wrap:wrap;gap:6px;min-height:24px}}
.chip{{background:var(--card2);color:var(--info);border:1px solid #2f4a5e;border-radius:999px;
  padding:3px 10px;font:600 11.5px/1.5 ui-monospace,"Cascadia Mono",Consolas,monospace;
  cursor:pointer;transition:.15s}}
.chip:hover{{background:#22384a;color:#fff;border-color:#3d647e}}
.chip.warn{{color:var(--warn);border-color:#5b4622}}
.chip.warn:hover{{background:#3a2e17;color:#fff}}
.empty{{color:var(--dim2);font-size:11.5px;padding:3px 0}}
.acts{{display:flex;gap:6px;margin-top:auto}}
.acts .rb{{flex:1;background:var(--card2);color:var(--dim);border:1px solid var(--line2);
  border-radius:7px;padding:4px 0;font:600 11.5px/1.5 inherit;cursor:pointer;transition:.15s}}
.acts .rb:hover{{color:var(--txt);border-color:#4a5563}}
.acts .rb.go{{color:#dff5cf;border-color:#4a7a34;background:#1e2a19}}
.acts .rb.go:hover{{background:#2a3d22;color:#fff}}
.acts .rb.next{{color:#9fd8d8;border-color:#3a6a6a;background:#182a2a}}
.acts .rb.next:hover{{background:#224040;color:#fff}}
.runbar{{display:none;align-items:center;gap:10px;
  background:#241d13;border:1px solid #5b4622;border-radius:10px;padding:10px 14px;margin:0 0 12px;
  font-size:13px}}
.runbar .dot{{width:9px;height:9px;border-radius:50%;background:var(--accent);
  animation:pulse 1.1s ease-in-out infinite;flex:0 0 auto}}
.runbar .what{{font-weight:700}}
.runbar .el{{color:var(--accent);font:600 13px/1.4 ui-monospace,Consolas,monospace}}
.runbar .hint{{color:var(--dim2);font-size:11.5px;margin-left:auto}}
.runbar.done{{background:#16241a;border-color:#2f5b34}}
.runbar.done .dot{{background:var(--ok);animation:none}}
.runbar.err{{background:#241618;border-color:#5a2f2c}}
.runbar.err .dot{{background:var(--bad);animation:none}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.35}}}}
body.busy .chip,body.busy .acts .rb{{opacity:.4;pointer-events:none}}
.cols{{display:grid;grid-template-columns:1.05fr .95fr;gap:14px;margin-top:18px;align-items:start}}
@media (max-width:1000px){{.cols{{grid-template-columns:1fr}}}}
.panel{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
.panel h3{{font-size:11.5px;letter-spacing:.18em;color:var(--dim);font-weight:700;margin-bottom:9px}}
.panel img{{width:100%;border-radius:8px;border:1px solid var(--line);background:#000;display:block}}
.cur{{font-size:16px;font-weight:700}}
.cur em{{font-style:normal;color:var(--accent)}}
.note{{color:var(--dim);font-size:12px;margin-top:8px}}
pre{{font:12px/1.7 ui-monospace,"Cascadia Mono",Consolas,monospace;background:#0c0f13;
  border:1px solid var(--line);border-radius:8px;padding:10px 12px;max-height:280px;overflow:auto;
  white-space:pre-wrap;word-break:break-all}}
pre .t{{color:var(--dim2)}} pre .x{{color:var(--bad)}} pre .v{{color:var(--ok)}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}
td,th{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line)}}
th{{color:var(--dim);font-weight:600;letter-spacing:.08em;font-size:11px}}
tr:last-child td{{border-bottom:0}}
.drop{{border:1px dashed var(--line2);border-radius:8px;padding:9px;text-align:center;
  color:var(--dim2);font-size:11.5px;margin-top:10px}}
</style></head><body><div class="wrap">
<div class="top">
  <div>
    <div class="brand">MAAOrch <em>推图控制台</em></div>
    <div class="sub">账号 <b>{account}</b> ｜ 活动 {event} ｜ {box_info}</div>
  </div>
  <div class="kpis">
    <div class="kpi"><b>{done}<i>/{total}</i></b><span>已通关</span></div>
    <div class="kpi"><b>{sanity}</b><span>理智</span></div>
    <div class="kpi"><b>{elapsed}</b><span>用时</span></div>
  </div>
</div>

<div class="bar">
  <div class="seg" title="打普通还是打突袭（突袭前提：该关普通已通关）"><span class="lbl">目标</span>
    <label><input type="radio" name="mode" value="normal" checked><i>普通</i></label>
    <label><input type="radio" name="mode" value="raid"><i>突袭</i></label></div>
  <div class="seg" title="自动导航 = 脚本自己进活动找关；就在这一屏 = 你已把画面摆到能开打的地方（最快）"><span class="lbl">路径</span>
    <label><input type="radio" name="path" value="nav" checked><i>自动导航</i></label>
    <label><input type="radio" name="path" value="here"><i>就在这一屏</i></label></div>
  <div class="seg" title="缺核心时借用好友/推荐的助战顶上（实测 9036 借圣聆初雪一次通关 EX-7）"><span class="lbl">借助战</span>
    <label><input type="radio" name="support" value="1" checked><i>开</i></label>
    <label><input type="radio" name="support" value="0"><i>关</i></label></div>
  <div class="seg" title="MAA 面板那个勾。开着编队必成（挂件可能是没练的）；关掉按作者设的练度门槛选人"><span class="lbl">忽略属性要求</span>
    <label><input type="radio" name="ignore" value="1" checked><i>开</i></label>
    <label><input type="radio" name="ignore" value="0"><i>关</i></label></div>
  <div class="bar-right">
    <span class="acctnav">{acct_nav}</span>
    <button class="btn" onclick="boxread()" title="读这个号的干员 box —— 挑作业靠它（没有 box 就只能瞎试）">扫描干员</button>
    <button class="btn" onclick="calibrate()" title="用地图上的黄点核对真实通关情况，把假三星翻回未确认">按画面校准</button>
    <button class="btn danger" onclick="stopall()" title="停掉这台模拟器上正在跑的推图 / MAA 运行器并释放锁">■ 停止</button>
  </div>
</div>

<div class="runbar" id="runbar"><span class="dot"></span><b class="what">…</b><span class="el"></span><span class="hint">在跑就别关页面；卡住了点上面的 ■ 停止</span></div>
<div class="grid">{nodes}</div>
<p class="note" id="status">每关下面那排 <b>▶ 作业号</b> = 直接让 MAA 跑那一份（你先把游戏点到那一关的详情页）。缺人的也能点 —— MAA 会借助战补。</p>

<div class="cols">
  <section class="panel">
    <h3>实时画面</h3>
    <img id="live" src="/live.png" alt="还没有画面：先跑 python tools/screen_watch.py">
    <p class="note">每 3 秒刷新（源：<code>tools/screen_watch.py</code> 的 <code>watch/{acct}/latest.png</code>）</p>
  </section>
  <section class="panel">
    <h3>当前</h3>
    <div class="cur">{current}</div>
    <div class="note">{current_sub}</div>
    <h3 style="margin-top:16px">日志</h3>
    <pre>{log}</pre>
    <div class="drop">把 auto_push.log 或 batch54_log.json 拖到这里，可临时查看</div>
  </section>
</div>

<section class="panel" style="margin-top:14px">
  <h3>尝试历史</h3>
  <table>
    <tr><th>关卡</th><th>作业</th><th>结果</th></tr>{attempts}
  </table>
  <p class="note">生成时间 {now} ｜ 数据来自 auto_push / maa_* 日志（页面只读）｜ 动作走 ui/control_server.py 的本机通道</p>
</section>
</div>
<script>
const rows={rows_json};
const API = location.protocol === "file:" ? "http://127.0.0.1:8898" : "";  // 文件打开时直连本机通道
const TOKEN = "{token}";
async function _post(path, label){{
  // **失败一定要"看得见"**（2026-09-17 用户："我为什么点半天没有反应呢"）:
  // 原来失败只写进角落那行小状态栏，很容易漏看 ⇒ 现在除了写状态栏还弹窗。
  const box = document.getElementById("status");
  box.textContent = `已发起${{label}} …`;
  try {{
    const r = await fetch(`${{API}}${{path}}`,
      {{method: "POST", headers: {{"X-Token": TOKEN}}}});
    const j = await r.json().catch(() => ({{}}));
    if (r.ok) {{
      box.textContent = `已发起${{label}}（pid ${{j.pid}} ｜ 日志 ${{j.log}}）`;
    }} else {{
      const m = `发起失败：${{j.error || r.status}}`;
      box.textContent = m; alert(m);
    }}
  }} catch (e) {{
    const m = `发起失败：连不上通道（${{e.message}}）—— 先跑 python ui/control_server.py`;
    box.textContent = m; alert(m);
  }}
}}
async function retry(stage){{ return run(stage); }}
function sw(){{
  // 顶部两个开关：**目标**（普通/突袭）、**路径**（自动导航 / 就在这一屏）
  return {{mode: document.querySelector('input[name=mode]:checked').value,
          here: document.querySelector('input[name=path]:checked').value === 'here' ? 1 : 0,
          support: document.querySelector('input[name=support]:checked').value,
          ignore: document.querySelector('input[name=ignore]:checked').value}};
}}
const PICK = {{}};
// **开关要记得住**（2026-09-17 用户："我一换其他页面再回来…返回默认了"）:
// 存到服务器（/prefs，落 ui/prefs.json）—— 换页面、换账号回来都还在。
async function loadPrefs(){{
  try {{
    const r = await fetch(`${{API}}/prefs`);
    const p = await r.json();
    const set = (name, val) => {{
      const el = document.querySelector(`input[name="${{name}}"][value="${{val}}"]`);
      if (el) el.checked = true;
    }};
    if (p.mode) set("mode", p.mode);
    if (p.path) set("path", p.path);
    if (p.support) set("support", p.support);
    if (p.ignore) set("ignore", p.ignore);
  }} catch (e) {{ /* 读不到就用默认，不吵 */ }}
}}
function savePrefs(){{
  const o = {{mode: document.querySelector('input[name=mode]:checked').value,
             path: document.querySelector('input[name=path]:checked').value,
             support: document.querySelector('input[name=support]:checked').value,
             ignore: document.querySelector('input[name=ignore]:checked').value}};
  fetch(`${{API}}/prefs`, {{method: "POST",
    headers: {{"Content-Type": "application/json", "X-Token": TOKEN}},
    body: JSON.stringify(o)}}).catch(() => {{}});
}}
document.querySelectorAll('input[name=mode],input[name=path],input[name=support],input[name=ignore]')
  .forEach(el => el.addEventListener("change", savePrefs));
loadPrefs();                    // 每关点了几次「换作业」→ 沿候选列表轮换（同设置换下一份）
async function run(stage){{
  const o = sw();
  return _post(`/run/${{stage}}?mode=${{o.mode}}&here=${{o.here}}`,
               `打 ${{stage}}（${{o.mode === 'raid' ? '突袭' : '普通'}}·${{o.here ? '就在这一屏' : '自动导航'}}）`);
}}
async function nextjob(stage){{
  const o = sw(); PICK[stage] = (PICK[stage] || 0) + 1;
  return _post(`/run/${{stage}}?mode=${{o.mode}}&here=${{o.here}}&pick=${{PICK[stage]}}`,
               `换作业打 ${{stage}}（从第 ${{PICK[stage] + 1}} 份候选起）`);
}}
async function restoreRun(){{
  // **刷新/切页面回来，横幅还得在**（2026-09-17 用户："横幅我切换之后就刷新，之后就不见了"）:
  // 执行状态存在服务器上（/lastrun），这里照着它重建；还在跑就每 3 秒问一次。
  try {{
    const p = await (await fetch(`${{API}}/lastrun`)).json();
    if (!p || !p.label) return;
    const bar = document.getElementById("runbar");
    bar.style.display = "flex";
    const show = (cls, what, el) => {{
      bar.className = "runbar " + cls;
      bar.querySelector(".what").textContent = what;
      bar.querySelector(".el").textContent = el || "";
    }};
    if (p.running) {{
      const t0 = Date.now() - (p.elapsed || 0) * 1000;
      show("", "正在跑：" + p.label, "");
      const tick = setInterval(() => {{
        bar.querySelector(".el").textContent = Math.round((Date.now() - t0) / 1000) + "s";
      }}, 500);
      const poll = setInterval(async () => {{
        const q2 = await (await fetch(`${{API}}/lastrun`)).json();
        if (!q2.running) {{
          clearInterval(tick); clearInterval(poll);
          const last = (q2.tail && q2.tail.length) ? q2.tail[q2.tail.length - 1] : "";
          if (q2.rc === 0 && last && last.indexOf("⚠️") !== 0) show("done", q2.label + "：" + last, (q2.elapsed || 0) + "s");
          else show("err", q2.label + "：" + (last || "没跑起来"), (q2.elapsed || 0) + "s");
        }}
      }}, 3000);
    }} else {{
      // **刷新重建时也要按判词显示**（2026-09-17：这条路径漏了 ⇒ 刷新后横幅又变回
      // "跑完（退出码 0）"，把"没打起来"说成成功 ✗）
      const last = (p.tail && p.tail.length) ? p.tail[p.tail.length - 1] : "";
      if (p.rc === 0 && last && last.indexOf("⚠️") !== 0) show("done", p.label + "：" + last, (p.elapsed || 0) + "s");
      else show("err", p.label + "：" + (last || "没跑起来"), (p.elapsed || 0) + "s");
    }}
  }} catch (e) {{ /* 读不到就算了，不吵 */ }}
}}
restoreRun();
async function searchjobs(stage){{
  // **现场搜**（用户 2026-09-17《现场搜 + 码直跑》）：只在点它时才搜，不在开页时搜。
  // 返回的候选带 `maa://码` ⇒ 点 ▶ 直接把码喂给 MAA（拉临时文件、跑完删）。
  const bar = document.getElementById("runbar");
  bar.style.display = "flex"; bar.className = "";
  bar.querySelector(".what").textContent = "正在作业站搜 " + stage + " …";
  bar.querySelector(".el").textContent = "";
  try {{
    const r = await fetch(`${{API}}/search/${{stage}}`, {{method: "POST", headers: {{"X-Token": TOKEN}}}});
    const j = await r.json().catch(() => ({{}}));
    if (!r.ok) {{ bar.className = "runbar err"; bar.querySelector(".what").textContent = "搜不到：" + (j.error || r.status); return; }}
    const card = [...document.querySelectorAll('.card')].find(c => c.querySelector('.id').textContent === stage);
    // **没有候选时卡片里没有 .picks 容器**（只有一句"没有可用作业"）⇒ 得现造一个塞进去，
    // 否则搜到结果也没地方放（2026-09-17 用户："还是刷不出来"）。
    let box = card ? card.querySelector('.picks') : null;
    if (card && !box) {{
      box = document.createElement('div');
      box.className = 'picks';
      const acts = card.querySelector('.acts');
      card.insertBefore(box, acts || null);
    }}
    if (box && j.candidates && j.candidates.length) {{
      box.innerHTML = j.candidates.map(c =>
        `<button class="${{c.miss ? 'chip warn' : 'chip'}}" onclick="runone('${{c.code}}')" ` +
        `title="${{c.code}} ${{c.title}}">▶ ${{c.code.replace('maa://','')}}${{c.miss ? ' 缺' + c.miss : ''}}</button>`).join('');
      bar.className = "runbar done";
      bar.querySelector(".what").textContent = stage + "：站上搜到 " + j.candidates.length + " 份（点 ▶ 直接跑，码直喂 MAA）";
    }} else {{
      bar.className = "runbar err";
      bar.querySelector(".what").textContent = stage + "：站上没搜到能用的（或结果格式没对上）";
    }}
  }} catch (e) {{
    bar.className = "runbar err";
    bar.querySelector(".what").textContent = "搜索失败：" + e.message;
  }}
}}
async function boxread(){{ return _post("/boxread", "扫描干员（读 box）"); }}
let BUSY = false;
async function runone(rel){{
  // "点了没反应"修法（2026-09-17 用户）：这个接口是**同步**的 —— 服务器要等 MAA 跑完整局
  // 才回话。所以点下去必须**立刻**给反馈：横幅出现 + 计时 + 锁住所有作业按钮。
  const bar = document.getElementById("runbar");
  if (BUSY) {{ alert("已经有一份在跑了 —— 等它完（看上面那条横幅的计时），或点 ■ 停止"); return; }}
  BUSY = true;
  document.body.classList.add("busy");
  bar.className = "runbar";
  bar.style.display = "flex";
  bar.querySelector(".what").textContent = "正在跑：" + rel;
  const t0 = Date.now();
  const tick = setInterval(() => {{
    bar.querySelector(".el").textContent = Math.round((Date.now() - t0) / 1000) + "s";
  }}, 500);
  try {{
    const o = (typeof sw === "function") ? sw() : {{support: 1, ignore: 1}};
    const r = await fetch(`${{API}}/runjob?job=${{encodeURIComponent(rel)}}&support=${{o.support}}&ignore=${{o.ignore}}`,
      {{method: "POST", headers: {{"X-Token": TOKEN}}}});
    const j = await r.json().catch(() => ({{}}));
    clearInterval(tick);
    if (r.ok) {{
      // 退出码 1 时**把脚本最后一行原话贴出来**（2026-09-17 用户："这什么意思啊"——
      // 原来只写"退出码 1"，看不出是被"不在详情页"拦下的）。成功时那句客套话留着。
      const last = (j.tail && j.tail.length) ? j.tail[j.tail.length - 1] : "";
      // **判词以脚本最后一行原话为准**（2026-09-17 用户："我给你发这个，就是告诉你这个是不
      // 正常的"）：退出码 0 也可能什么都没打 ⇒ 不再无脑写"跑完"，直接显示脚本的结论。
      if (j.rc === 0 && last && last.indexOf("⚠️") !== 0) {{
        bar.className = "runbar done";
        bar.querySelector(".what").textContent = rel + "：" + last;
      }} else {{
        bar.className = "runbar err";
        bar.querySelector(".what").textContent = rel + " 没跑起来：" + last;
      }}
      bar.querySelector(".el").textContent = j.elapsed + "s";
    }} else {{
      bar.className = "runbar err";
      bar.querySelector(".what").textContent = "起不来：" + (j.error || r.status);
    }}
  }} catch (e) {{
    clearInterval(tick);
    bar.className = "runbar err";
    bar.querySelector(".what").textContent = "连不上通道：" + e.message;
  }} finally {{
    BUSY = false;
    document.body.classList.remove("busy");
  }}
}}
async function stopall(){{
  // 一键停止：在跑的进程不一定是我这条通道起的（命令行/上次重启前起的），所以后端按**模拟器**清场
  if (!confirm("停掉这台模拟器上正在跑的推图 / MAA 运行器，并释放锁？")) return;
  return _post("/stop", "一键停止");
}}
async function calibrate(){{
  // 按**游戏画面上的黄点**校准状态：纠正"没打过却记了三星"的假通关
  // （2026-09-17 用户："已经通关的关卡没办法让他再打吗，有些其实是误判"）
  const box = document.getElementById("status");
  box.textContent = "已发起「按游戏画面校准」…（用地图黄点纠正假通关，跑完刷新本页）";
  try {{
    const r = await fetch(`${{API}}/calibrate`, {{method: "POST", headers: {{"X-Token": TOKEN}}}});
    const j = await r.json().catch(() => ({{}}));
    box.textContent = r.ok ? `校准已启动（pid ${{j.pid}} ｜ 日志 ${{j.log}}）—— 跑完刷新本页`
                           : `校准失败：${{j.error || r.status}}`;
  }} catch (e) {{
    box.textContent = `校准失败：连不上通道（${{e.message}}）`;
  }}
}}
// 实时画面每 3 秒刷一次（加时间戳绕开缓存）——"这个号此刻在干什么"最快的一眼
setInterval(() => {{
  const el = document.getElementById("live");
  if (el) el.src = `/live.png?t=${{Date.now()}}`;
}}, 3000);
document.querySelector('.drop').addEventListener('dragover',e=>e.preventDefault());
document.querySelector('.drop').addEventListener('drop',async e=>{{
  e.preventDefault();const f=e.dataTransfer.files[0];if(!f)return;
  const t=await f.text();const pre=document.querySelector('pre');
  pre.textContent=t.split('\\n').slice(-40).join('\\n');
}});
</script></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="",
                    help="账号键（UID）。不给就从 --state 的文件名 push_state_<uid>.json 认，"
                         "认不出才回退 9036 —— 以前默认写死 9036，三个页面标题全写 9036")
    ap.add_argument("--event", default="act54side")
    ap.add_argument("--sanity", default="—")
    ap.add_argument("--elapsed", default="1h23m")
    # 多账号（每个模拟器一个页面）
    ap.add_argument("--state", default="", help="指定读哪个 push_state（多账号）")
    ap.add_argument("--out", default="", help="指定输出 HTML 路径（多账号）")
    ap.add_argument("--log-key", default="", help="只认文件名带这个键的日志（多账号，如 emu0）")
    a = ap.parse_args()
    global LOG_KEY, STATE_OVERRIDE, OUT_OVERRIDE          # noqa: PLW0603
    LOG_KEY = a.log_key
    if a.state:
        STATE_OVERRIDE = pathlib.Path(a.state)
    if a.out:
        OUT_OVERRIDE = pathlib.Path(a.out)
    # 账号键：没显式给就从状态文件名 push_state_<uid>.json 里认（页面标题 + 切换条高亮都要它）
    acct = a.account
    if not acct and a.state:
        m = re.search(r"push_state_([0-9A-Za-z_]+)\.json", a.state)
        if m:
            acct = m.group(1)
    acct = acct or "9036"

    cells = []
    # ⚡ **快速入口**（用户 2026-09-17："不是做极简系统，是给现有页面加一个快速入口"）:
    # 每一关多一行「▶ 作业号」按钮 —— 点了就用**现有的** maa_copilot_run.py 跑那一份。
    # 候选与"缺几个"**复用 tools/job_fit.py**，不另写判据。
    try:
        import job_fit as _JF                                    # noqa: PLC0415
        _box, _meta = _JF.load_box(acct)
    except Exception:                                           # noqa: BLE001
        _box = set()

    def cands_for(stage_name: str) -> list[tuple[str, int, str]]:
        """这一关的候选：[(作业号, 缺几个, 相对路径)]，按缺的人数排。"""
        if not _box:
            return []
        num = stage_name.split("-")[1]
        pat = (f"act54side_ex{int(num):02d}_" if stage_name.upper().startswith("EX-")
               else f"act54side_{int(num):02d}_")
        out, seen = [], set()
        for p in sorted(set(glob.glob(str(PROJ / "data" / "copilot" / "*" / f"{pat}*.json")))):
            try:
                raw = pathlib.Path(p).read_text(encoding="utf-8")
                d = json.loads(raw)
            except Exception:                                   # noqa: BLE001
                continue
            if not isinstance(d, dict) or not (d.get("actions") or []):
                continue                                        # 空壳不显示（点了白跑）
            # **同一份作业可能被收进多个池子 ⇒ 按内容去重**
            # （用户 2026-09-17："为什么有些码是一样的"：102541 同时躺在
            #  event54_394162955/ 和 event54_d/，列表里就冒出两个 102541）
            sig = raw.strip()
            if sig in seen:
                continue                                        # 内容一致 = 同一份，只留一份
            seen.add(sig)
            miss, dead, _t = _JF.fit(p, _box)
            n = len(list(miss) + list(dead))
            # **缺 ≥2 的不列**（2026-09-17 用户问"缺2 的为什么也在"）: 一局只能带 1 个助战
            # ⇒ 缺 2 个借 1 个也补不上，点了必然编不满、白跑一趟。缺 1 的照列（能借）。
            if n >= 2 or any(str(x).startswith("〈") for x in miss):
                continue                                        # 缺≥2 / 空壳 / MAA 读不了 ⇒ 不列
            rel = pathlib.Path(p).relative_to(PROJ / "data" / "copilot").as_posix()
            out.append((pathlib.Path(p).stem.split("_")[-1], n, rel))
        # 把"站上搜到的码"并进来（标 ☁；码直跑，不留本地）。
        # **必须在这里读**（2026-09-17 实测）：早先写成"启动时读一次"，而那一刻
        # `acct` 还没解析（生成器里有 `acct = acct or "9036"`）⇒ 恒去读 search_9036.json
        # ⇒ 页面上一个码都没有 ✗（用户："刷新一下，找到了的作业就没了"）。
        try:
            _f = PROJ / "ui" / f"search_{acct}.json"
            _searched = json.loads(_f.read_text(encoding="utf-8")) if _f.is_file() else {}
        except Exception:                                      # noqa: BLE001
            _searched = {}
        for c in (_searched.get(stage_name) or []):
            if isinstance(c, dict) and c.get("code"):
                out.append((c["code"].replace("maa://", "☁") + str(c.get("mis", "")),
                            int(c.get("miss") or 0), c["code"]))
        out.sort(key=lambda x: x[1])
        return out[:8]                                              # 一关最多露 6 个，够挑

    # 新账号（--state 指了个还不存在的文件）必须停在"未打/未解锁"：照抄内置快照会假报三星
    fresh = bool(STATE_OVERRIDE) and not pathlib.Path(STATE_OVERRIDE).is_file()
    live = {} if fresh else (load_push_state().get("stages") or {})
    for name in [f"SR-{i}" for i in range(1, 9)] + [f"EX-{i}" for i in range(1, 9)]:
        short = name if name.startswith("EX-") else name      # EX 在 state 里写成 EX-1
        rec = live.get(name) or live.get(short)
        if rec:
            cells.append(cell(name, rec, cands_for(name)))
            continue
        if fresh:
            cells.append(cell(name, {"status": "未解锁" if name.startswith("EX-") else "未打"}, cands_for(name)))
            continue
        # **没有实时状态 ⇒ 一律"未打/未解锁"，绝不回退内置快照**
        # （2026-09-17 用户："这个干员池是不是相互之间污染了" —— 污染的不是 box，是**状态显示**：
        #  状态文件里没有的关会去查一份内置 STATES 快照，于是把**别的号/别的时期**的成绩
        #  显示在了这个号上 ✗。SquidP 的 SR-4/5/6/7 就是这么冒出来的。）
        if True:
            cells.append(cell(name, {"status": "未解锁" if name.startswith("EX-") else "未打"},
                              cands_for(name)))
            continue
        st = STATES.get(name, "locked")                        # （下面这段已废弃，保留仅为对照）
        if isinstance(st, int):
            cells.append(cell(name, {"status": "三星" if st == 3 else "未打"}, cands_for(name)))
        elif st == "done-unconfirmed":
            # 走**同一个卡片函数**（2026-09-17 用户报"混搭"：这格原来硬写旧版 HTML，
            # 于是同一页上 SR-8 是旧样式、其余是新卡片 ⇒ 看着像两个版本拼的）
            cells.append(cell(name, {"status": "未确认"}, cands_for(name)))
        else:
            cells.append(cell(name, {"status": "未解锁"}, cands_for(name)))
    # 进度 = 已通关的关数（二星也算通关；三星与否看格子上的星）
    # 新账号（fresh）必须是 0 —— 兜底那半句会去数内置快照里的 7 关 ✗
    done = 0 if fresh else (
        sum(1 for n in [f"SR-{i}" for i in range(1, 9)] + [f"EX-{i}" for i in range(1, 9)]
            if (live.get(n) or {}).get("status") in ("三星", "二星")) or
        sum(1 for v in STATES.values() if v == 3 and not live))
    events = read_events()
    log = "\n".join(
        (f'<span class="t">{t}</span> ' if t else "") +
        (f'<span class="x">{s}</span>' if any(k in s for k in ("失败", "false", "Failed", "超时"))
         else f'<span class="v">{s}</span>' if any(k in s for k in ("通关", "map", "完成", "True"))
         else s)
        for t, s in events)
    atts = []
    for stage, items in sorted(read_attempts().items()):
        for job, why in items:
            cls = "bad" if why in ("编队缺人", "连接失败", "执行出错", "未完成") else "ok"
            atts.append(f"<tr class='{'fail' if cls == 'bad' else ''}'><td>{stage}</td>"
                        f"<td>{job}</td><td><span class='tag {cls}'>{why}</span></td></tr>")
    # 当前区：**按状态分支**（不要写成 f"战斗中：{stage or '—'}" —— 状态不对时显示错，实测踩到）
    cur, cur_sub = "空闲", "还没有 push_state.json —— auto_push 跑一次就会有"
    if live:
        cur_sub = "状态源 push_state.json ｜ 更新于 " + str(load_push_state().get("updated") or "—")
        # 取"最新的那关"：**按 timestamp 排序**，不要按 JSON 顺序（实测会一直显示 SR-1）
        inbattle = [k for k, v in live.items() if v.get("status") == "战斗中"]
        threestar = [(str(v.get("timestamp") or ""), k)
                     for k, v in live.items() if v.get("status") == "三星"]
        fails = [(str(v.get("timestamp") or ""), k, str(v.get("reason") or ""))
                 for k, v in live.items() if v.get("status") == "失败"]
        if inbattle:
            cur = f"战斗中：{inbattle[0]}"
        elif threestar:
            cur = f"最近完成：{max(threestar)[1]}"
        elif fails:
            _t, _k, _why = max(fails)
            cur = f"失败：{_k}（{_why}）"
    html = HTML.format(
        account=acct, event=a.event, done=done, total=16, sanity=a.sanity,
        elapsed=a.elapsed, nodes="\n".join(cells),
        current=cur, current_sub=cur_sub,
        acct_nav=acct_nav(acct), acct=acct,
        box_info=box_info_for(acct),
        log=log or "（暂无日志）", rows_json=json.dumps(events, ensure_ascii=False),
        attempts="\n".join(atts) or "<tr><td colspan=3 class='mut'>暂无记录</td></tr>",
        now=dt.datetime.now().strftime("%Y-%m-%d %H:%M"), token=control_token())
    out_path = OUT_OVERRIDE or OUT
    out_path.write_text(html, encoding="utf-8")
    print(f"已生成 {out_path}（{len(html)} 字节）")
    print(f"进度 {done}/16 ｜ 事件 {len(events)} 条 ｜ 尝试记录 {len(atts)} 条")


if __name__ == "__main__":
    main()
