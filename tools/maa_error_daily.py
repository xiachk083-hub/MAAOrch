#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAA 每日错误报告聚合（只读）—— TaskChainError / 任务出错 / 可用干员不足 合成一页日报。

背景（2026-09-27）：错误信息散在四处 —— MAA 目录的 gui/asst 日志、AUTO-MAS 逐次尝试的
history/<date>/<账号>/*.json（maa_result）与同名 .log、主机的 debug/app.log。巡检
（cron job mas-readonly-inspection）与人工复盘都需要一眼看清"今天错在哪、哪些要管、
哪些不用管"。

数据源（全部只读；主机 = 100.79.173.69，经 SSH）：
  1. E:\\AUTO-MAS\\history\\<date>\\<账号>\\*.json  -> maa_result（账号级权威口径）
     + 同名 .log -> 「任务出错: <链名>」/「掉线」（定因证据）
  2. E:\\MAA-Multi\\MAA_*\\debug\\gui.log           -> [ERR] 行（任务出错 / 可用干员不足 /
     截图失败 / 游戏掉线）+ 运行收尾行（任务已全部完成 / 任务已完成，但出现错误）
  3. E:\\MAA-Multi\\MAA_*\\debug\\asst.log          -> TaskChainError / SubTaskError（链级）
  4. E:\\AUTO-MAS\\debug\\app.log                   -> WARNING/ERROR 行（用户级异常 / 模拟器
     启动失败 / 配置解析错误）
  5. AUTO-MAS REST（只读）                          -> 脚本→MAA目录、uuid→账号名（不可达时降级为 "-"）

窗口：班次日口径 [D 05:00, D+1 05:00)；--date 缺省 = 今天（本地时间 <05:00 时取昨天，覆盖夜班尾巴）。

产出：
  runtime/reports/maa_errors_<date>.md    —— 每日一页报告（含 ERR_SUMMARY 行）
  runtime/reports/maa_errors_<date>.json  —— 机读版
  --summary 只打印一行：`ERR_SUMMARY=<n> 条，Top=<类型>`（巡检 cron 用）

归类：客户端未就绪 / 资源类 / 干员不足 / 网络 / 人为 / 未知（"人为"=手动中止，非故障）。
致命 vs 告警 vs 可忽略（可忽略 = 已知无需动作，如公招资源类；告警 = 出现但已实测不影响结果，
如「可用干员不足」在基建换班里继续跑完）。

用法：
  python tools/maa_error_daily.py                 # 全量：扫描→写 report/json→stdout 摘要
  python tools/maa_error_daily.py --summary       # 只打 ERR_SUMMARY 一行
  python tools/maa_error_daily.py --json          # 打印结构化 JSON（不写盘）
  python tools/maa_error_daily.py --date 2026-09-26
  python tools/maa_error_daily.py --no-write      # 不写盘
  python tools/maa_error_daily.py --check         # 连通性自检（REST+SSH），退出码 0/2
  python tools/maa_error_daily.py --selftest      # 离线分类器自检（不连网）

退出码：0=成功；2=数据源不可达（报告里会写 ERR_SUMMARY=UNKNOWN:<原因>）；1=未预期错误。
环境变量：MAAORCH_MAS_API / MAAORCH_SSH_KEY / MAAORCH_SSH_HOST（同其他 mas_* 工具）。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from collections import Counter, OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(ROOT, "runtime")
REPORTS = os.path.join(RUNTIME, "reports")
API = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")

HIST_ROOT = r"E:\AUTO-MAS\history"
APP_LOG = r"E:\AUTO-MAS\debug\app.log"
MAA_GLOB = r"E:\MAA-Multi\MAA_*"

WIN_START_HOUR = 5          # 班次日窗口起：D 05:00
CAP_GUI = 400               # 每目录 gui 行上限
CAP_ASST = 300              # 每目录 asst 行上限
CAP_APP = 600               # app.log 行上限
LOGIN_ESCALATE = 20         # 登录类同日 ≥ 此值 → 升级告警（巡检提示词既有口径）

CAT_CLIENT = "客户端未就绪"
CAT_RESOURCE = "资源类"
CAT_DEFICIT = "干员不足"
CAT_NET = "网络"
CAT_HUMAN = "人为"
CAT_UNKNOWN = "未知"
CATS = [CAT_CLIENT, CAT_RESOURCE, CAT_DEFICIT, CAT_NET, CAT_HUMAN, CAT_UNKNOWN]

SEV_FATAL = "fatal"
SEV_WARN = "warn"
SEV_BENIGN = "benign"
SEV_NAMES = {SEV_FATAL: "致命", SEV_WARN: "告警", SEV_BENIGN: "可忽略"}

# ---------------------------------------------------------------- 分类表

# 链名（中文 GUI 名 / 英文链名）→ (归类, 备注)
CHAIN_CAT = {
    "开始唤醒": (CAT_CLIENT, "登录/唤醒链"),
    "startup": (CAT_CLIENT, "登录/唤醒链"),
    "自动公招": (CAT_RESOURCE, "公招链（招聘许可不足等资源原因常见）"),
    "recruit": (CAT_RESOURCE, "公招链（招聘许可不足等资源原因常见）"),
    "基建换班": (CAT_UNKNOWN, "基建链（干员不足有独立告警行）"),
    "infrast": (CAT_UNKNOWN, "基建链（干员不足有独立告警行）"),
    "理智作战": (CAT_UNKNOWN, "作战链（理智/关卡/代理）"),
    "fight": (CAT_UNKNOWN, "作战链（理智/关卡/代理）"),
    "信用收支": (CAT_UNKNOWN, "信用购物链"),
    "mall": (CAT_UNKNOWN, "信用购物链"),
    "领取奖励": (CAT_UNKNOWN, "领奖链"),
    "award": (CAT_UNKNOWN, "领奖链"),
}

# gui.log 噪音（已知无意义，不计入条目）
GUI_NOISE = (
    "Failed to set startup",
    "Toast 通知不可用",
    "Application is releasing",
)
# app.log 噪音
APP_NOISE = (
    "屏蔽 MuMu 应用商店悬浮广告失败",
    "停止 MuMu 应用商店广告进程失败",
    "device offline",
    "未在线",
    "All connection attempts failed",
    "无法获取远程分支信息",
    "Retrying (Retry(",
    "日志文件今天未被修改",
    "MuMu 应用未进入前台",
)

RESULT_LOGIN = "MAA 未能正确登录 PRTS"
RESULT_QUIT = "MAA 在完成任务前退出"
RESULT_ABORT = "MAA 在完成任务前中止"
RESULT_PARTIAL = "MAA 部分任务执行失败"
RESULT_TIMEOUT = "MAA 进程超时"
RESULT_HUMAN = "任务被用户手动中止"
RESULT_NOWATCH = "未开始监看日志"


def cat_of_chain(chain):
    """链名 -> (归类, 备注)；未知链 -> (未知, 未识别链)。"""
    c = str(chain or "").strip()
    low = c.lower()
    if c in CHAIN_CAT:
        return CHAIN_CAT[c]
    if low in CHAIN_CAT:
        return CHAIN_CAT[low]
    return (CAT_UNKNOWN, "未识别链")


def _pick_cat(chains):
    """一组链名 -> (归类, 备注)；混合归类时取 未知。"""
    cats = OrderedDict()
    for c in chains:
        cat, why = cat_of_chain(c)
        cats.setdefault(cat, why)
    if len(cats) == 1:
        cat = next(iter(cats))
        return (cat, cats[cat])
    return (CAT_UNKNOWN, "多链混合：" + "/".join(list(cats)[:4]))


def classify_result(result, chains=None, dropped=False, bad_json=False):
    """history maa_result（+链证据）-> 条目 dict 或 None（成功/空）。

    chains: 该次尝试 .log 里抓到的链名列表（已去重）。
    dropped: .log 出现「掉线」。bad_json: 该记录 json 损坏。
    """
    chains = list(chains or [])
    if bad_json:
        return _item(CAT_UNKNOWN, SEV_WARN, "history json 损坏（0 字节/解析失败）")
    r = str(result or "").strip()
    if not r:
        return _item(CAT_UNKNOWN, SEV_WARN, "未读到 maa_result")
    if r == "Success!":
        return None
    if r.startswith("模拟器启动失败"):
        return _item(CAT_CLIENT, SEV_FATAL, "实例未起来（模拟器级）")
    if r == RESULT_LOGIN:
        return _item(CAT_CLIENT, SEV_WARN, "登录瞬时失败（自动重试级，不单独立项）")
    if r in (RESULT_QUIT, RESULT_ABORT):
        if dropped:
            return _item(CAT_NET, SEV_FATAL, "运行中掉线（" + r + "）")
        if not chains:
            return _item(CAT_UNKNOWN, SEV_FATAL, "退出/中止但无链证据（.log 缺）")
        cat, why = _pick_cat(chains)
        if cat == CAT_RESOURCE:
            return _item(CAT_RESOURCE, SEV_BENIGN,
                         "仅公招链失败（资源类，等券自愈）: " + "/".join(chains))
        if cat == CAT_CLIENT:
            return _item(CAT_CLIENT, SEV_WARN, "登录/唤醒类（重试级）: " + "/".join(chains))
        return _item(cat, SEV_FATAL, why + "；链: " + "/".join(chains[:4]))
    if r == RESULT_PARTIAL:
        if not chains:
            return _item(CAT_UNKNOWN, SEV_FATAL, "部分任务失败但无链证据")
        cat, why = _pick_cat(chains)
        sev = SEV_WARN if cat == CAT_CLIENT else SEV_FATAL
        return _item(cat, sev, "部分链失败: " + "/".join(chains[:4]))
    if r == RESULT_TIMEOUT:
        return _item(CAT_UNKNOWN, SEV_FATAL, "MAA 挂起/未按时退出（含「启动即冻结」家族）")
    if r == RESULT_HUMAN:
        return _item(CAT_HUMAN, SEV_BENIGN, "人为中止（非故障）")
    if r == RESULT_NOWATCH:
        return _item(CAT_CLIENT, SEV_FATAL, "MAA 未进入可监看状态（未开始监看日志）")
    return _item(CAT_UNKNOWN, SEV_FATAL, "未识别 maa_result", raw=r)


def classify_gui(msg, chain=None, outcome=None):
    """gui.log 一行 -> 条目 / ("marker", why) / ("noise", why) / None（未识别）。

    chain/outcome 由调用方补给（outcome: all_done / done_with_err / none）。
    """
    m = str(msg or "").strip()
    if any(n in m for n in GUI_NOISE):
        return ("noise", m[:80])
    if m.startswith("任务已完成，但出现错误"):
        return ("marker", "done_with_err")
    if m.startswith("任务已全部完成"):
        return ("marker", "all_done")
    if m.startswith("本轮任务出现以下错误"):
        return ("marker", "errlist")
    if m.startswith("出现错误, 已返回"):
        return ("marker", "err-ret")
    mc = re.match(r"任务出错[:：]\s*(.+)$", m)
    if mc:
        ch = mc.group(1).strip()
        cat, why = cat_of_chain(ch)
        if cat == CAT_CLIENT and ch.startswith("开始唤醒"):
            return _item(CAT_CLIENT, SEV_WARN, "登录/唤醒重试级", chain=ch)
        if cat == CAT_RESOURCE:
            return _item(CAT_RESOURCE, SEV_BENIGN, why + "（资源类，等券自愈，不补跑）", chain=ch)
        if outcome == "all_done":
            return _item(cat, SEV_WARN, why + "（本轮同窗已恢复并全部完成）", chain=ch)
        if outcome == "done_with_err":
            return _item(cat, SEV_FATAL, why + "（本轮以「已完成但出现错误」收尾）", chain=ch)
        return _item(cat, SEV_FATAL, why + "（未见收尾行，运行中或未恢复）", chain=ch)
    if "可用干员不足" in m:
        return _item(CAT_DEFICIT, SEV_WARN, "基建换班/设施配置干员不够：实测继续跑完，不影响结果")
    if "截图失败" in m:
        return _item(CAT_NET, SEV_FATAL, "截图链路失败（网络/模拟器）")
    if "游戏掉线" in m:
        return _item(CAT_NET, SEV_FATAL, "游戏掉线，停止任务")
    return None


def classify_app(level, module, msg):
    """app.log 一行 -> 条目 / ("noise", why) / ("other", key) / ("skip", why)。"""
    m = str(msg or "").strip()
    if any(n in m for n in APP_NOISE):
        return ("noise", "MuMu 装饰性操作失败")
    if "代理任务异常: " in m:
        r = m.split("代理任务异常: ", 1)[1].strip()
        item = classify_result(r)
        if item is None:
            item = _item(CAT_UNKNOWN, SEV_WARN, "用户级异常（未识别）", raw=r)
        item["note"] = "用户级（app.log）: " + item.get("note", "")
        item["result"] = r
        return item
    if "的自动代理任务未完成" in m:
        return ("skip", "与「代理任务异常」重复")
    if "模拟器启动失败" in m:
        return _item(CAT_CLIENT, SEV_FATAL, "模拟器启动失败（实例级）")
    if "无法解析文件" in m:
        return ("noise", "历史库旧文件解析失败（历史搜索时复现，非当日故障）")
    if "Error calling" in m:
        return _item(CAT_UNKNOWN, SEV_WARN, "主程序 API 调用异常")
    if "未通过配置检查" in m:
        return _item(CAT_UNKNOWN, SEV_WARN, "配置检查未通过（零星可忽略）")
    if level == "ERROR":
        return ("other", _norm_msg(m))
    return ("other", _norm_msg(m))


def _norm_msg(m):
    s = re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{20,}", "<uuid>", m)
    s = re.sub(r"\d+", "#", s)
    return s[:110]


def _item(cat, sev, note="", chain="", raw="", result=""):
    return {"cat": cat, "sev": sev, "note": note, "chain": chain, "raw": raw, "result": result}


def _l(v):
    """PS ConvertTo-Json 的单元素数组可能退化为标量 -> 统一成 list。"""
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


# ---------------------------------------------------------------- 时间 / 窗口

def resolve_date(s):
    if s:
        return dt.date.fromisoformat(s)
    now = dt.datetime.now()
    if now.hour < WIN_START_HOUR:
        return (now - dt.timedelta(days=1)).date()
    return now.date()


def window(d):
    """-> (start_str, end_str, regex_bracket, regex_plain)

    regex_bracket 用于以 `[yyyy-mm-dd hh:mm:ss...]` 开头的行（gui/asst）；
    regex_plain 用于无方括号前缀的行（app.log 的 `yyyy-mm-dd hh:mm:ss.mmm | LEVEL | ...`）。
    """
    d2 = d + dt.timedelta(days=1)
    d1s, d2s = d.isoformat(), d2.isoformat()
    part = (r"(%s (0[5-9]|1[0-9]|2[0-3]):|%s 0[0-4]:)" % (d1s, d2s))
    return (d1s + " 05:00:00", d2s + " 05:00:00", r"^\[" + part, part)


# ---------------------------------------------------------------- 远端采集

_PS_TMPL = r"""
$ErrorActionPreference = 'Continue'; $ProgressPreference = 'SilentlyContinue'
$wpat = '@@WINDOW@@'
$wapp = '@@WINDOW_APP@@'
$histRoot = '@@HIST@@'
$res = [ordered]@{ hist = @(); slots = @(); app = [ordered]@{ lines = @(); rounds = 0 }; meta = [ordered]@{} }
$t0 = [datetime]'@@T0@@'

# ---- 1) history：逐账号逐尝试（扫 D-1/D/D+1 三个目录，按内容时间戳过滤）----
$h = @()
foreach ($hd in @(@@HIST_DIRS@@)) {
  if (-not (Test-Path $hd)) { continue }
  foreach ($ad in (Get-ChildItem $hd -Directory)) {
    foreach ($jf in (Get-ChildItem $ad.FullName -Filter *.json -File)) {
      $rec = [ordered]@{
        acct = $ad.Name; file = $jf.Name; dirday = $hd.Substring($hd.Length - 10)
        mt_day = $jf.LastWriteTime.ToString('yyyy-MM-dd'); mt_hms = $jf.LastWriteTime.ToString('HH:mm:ss')
        t_first = ''; t_last = ''
        result = ''; sanity = $null; bad = $false; chains = @(); dropped = $false
      }
      try {
        $j = (Get-Content -Raw -Encoding UTF8 -ErrorAction Stop $jf.FullName | ConvertFrom-Json -ErrorAction Stop)
        $rec.result = [string]$j.maa_result
        $rec.sanity = $j.sanity
      } catch { $rec.bad = $true }
      $lf = [System.IO.Path]::ChangeExtension($jf.FullName, '.log')
      if (Test-Path $lf) {
        # 内容首末时间戳 = 该次尝试实际起止（文件名时间不可信：实测与内容差 4h）
        $ts = @(Select-String -Path $lf -Pattern '^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}' -Encoding UTF8)
        if ($ts.Count -ge 1) {
          $m1 = [regex]::Match($ts[0].Line, '^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
          if ($m1.Success) { $rec.t_first = $m1.Groups[1].Value }
          $m2 = [regex]::Match($ts[$ts.Count - 1].Line, '^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
          if ($m2.Success) { $rec.t_last = $m2.Groups[1].Value }
        }
        $seen = @{}
        foreach ($x in @(Select-String -Path $lf -Pattern '任务出错[:：]' -Encoding UTF8 | Select-Object -First 12)) {
          $c = $x.Line.Trim()
          $mm = [regex]::Match($c, '任务出错[:：]\s*(.+)$')
          if ($mm.Success) {
            $ch = $mm.Groups[1].Value.Trim()
            if (-not $seen.ContainsKey($ch)) { $seen[$ch] = 1; $rec.chains += $ch }
          }
        }
        if (@(Select-String -Path $lf -Pattern '掉线' -Encoding UTF8 | Select-Object -First 1).Count -gt 0) { $rec.dropped = $true }
      }
      $h += $rec
    }
  }
}
$res.hist = $h

# ---- 2/3) 每槽位 gui/asst ----
$s = @()
foreach ($sd in (Get-ChildItem '@@MAA_GLOB@@' -Directory)) {
  $g = Join-Path $sd.FullName 'debug\gui.log'
  $a = Join-Path $sd.FullName 'debug\asst.log'
  $hasG = Test-Path $g; $hasA = Test-Path $a
  if (-not $hasG -and -not $hasA) { continue }
  $newest = $null
  if ($hasG) { $gi = Get-Item $g; if ($null -eq $newest -or $gi.LastWriteTime -gt $newest) { $newest = $gi.LastWriteTime } }
  if ($hasA) { $ai = Get-Item $a; if ($null -eq $newest -or $ai.LastWriteTime -gt $newest) { $newest = $ai.LastWriteTime } }
  if ($null -ne $newest -and $newest -lt $t0) { continue }
  $row = [ordered]@{ dir = $sd.Name; gui = @(); asst = @() }
  if ($hasG) {
    $m = @(Select-String -Path $g -Pattern '\[ERR\]|任务已全部完成' -Encoding UTF8 | Where-Object { $_.Line -match $wpat } | Select-Object -First @@CAP_GUI@@)
    foreach ($x in $m) { $row.gui += ('{0}|{1}' -f $x.LineNumber, $x.Line.Trim()) }
  }
  if ($hasA) {
    $m = @(Select-String -Path $a -Pattern 'TaskChainError|SubTaskError|可用干员不足|掉线|截图失败' -Encoding UTF8 | Where-Object { $_.Line -match $wpat } | Select-Object -First @@CAP_ASST@@)
    foreach ($x in $m) { $row.asst += ('{0}|{1}' -f $x.LineNumber, $x.Line.Trim()) }
  }
  $s += $row
}
$res.slots = $s

# ---- 4) app.log（行首无方括号：用 $wapp）----
if (Test-Path '@@APP@@') {
  $m = @(Select-String -Path '@@APP@@' -Pattern '\| (WARNING|ERROR)\s+\|' -Encoding UTF8 | Where-Object { $_.Line -match $wapp } | Select-Object -First @@CAP_APP@@)
  foreach ($x in $m) { $res.app.lines += ('{0}|{1}' -f $x.LineNumber, $x.Line.Trim()) }
  $res.app.rounds = @(Select-String -Path '@@APP@@' -Pattern '开始代理用户' -Encoding UTF8 | Where-Object { $_.Line -match $wapp }).Count
}
$res.meta = [ordered]@{ histDirs = (@(@@HIST_DIRS@@) -join ','); slots = $s.Count; win = $wpat }
Write-Output ('__JSON__' + (ConvertTo-Json -Compress -Depth 7 $res))
"""


def _ssh_exe():
    # 优先系统自带 OpenSSH：git-bash 的 /usr/bin/ssh 对 Windows 路径参数（-i C:\...）不可靠。
    p = r"C:\Windows\System32\OpenSSH\ssh.exe"
    if os.path.exists(p):
        return p
    return shutil.which("ssh") or "ssh"


_WIN_ENV_DEFAULTS = {
    "ComSpec": r"C:\WINDOWS\System32\cmd.exe",
    "windir": r"C:\WINDOWS",
    "OS": "Windows_NT",
    "ProgramData": r"C:\ProgramData",
    "ProgramFiles": r"C:\Program Files",
    "ProgramFiles(x86)": r"C:\Program Files (x86)",
    "ProgramW6432": r"C:\Program Files",
    "CommonProgramFiles": r"C:\Program Files\Common Files",
    "ALLUSERSPROFILE": r"C:\ProgramData",
    "SystemDrive": "C:",
    "SESSIONNAME": "Console",
    "NUMBER_OF_PROCESSORS": "16",
    "PROCESSOR_IDENTIFIER": "AMD64",
    "USERDOMAIN": "DESKTOP",
}


def _child_env():
    """子进程环境「自愈」。MCP stdio 形态下本工具由 MCP server（如 mas-triage）以 SDK 极简
    env（get_default_environment ~12 键，无 ProgramData/ComSpec/windir 等）拉起，此环境下
    **ssh.exe 静默 rc=255、stdout/stderr 全空**（t_6cfe7865 实测；负对照：
    scratch/env_reduced_check.py —— 4.4s 即 `SSH 上传未确认: `；同一命令全量 env 正常）。
    合入 registry 环境（HKLM Session Manager\\Environment + HKCU\\Environment）+ Windows 关键
    默认值 —— 与 tools/mas_observe_mcp.py::_env() 同口径（同一实验定稿的那一组）。
    """
    env = dict(os.environ)
    reg = {}
    if os.name == "nt":
        try:
            import winreg
            for hive, path in ((winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                               (winreg.HKEY_CURRENT_USER, "Environment")):
                try:
                    with winreg.OpenKey(hive, path) as k:
                        for i in range(winreg.QueryInfoKey(k)[1]):
                            try:
                                name, val, _ = winreg.EnumValue(k, i)
                            except OSError:
                                continue
                            if isinstance(val, str):
                                reg[name.upper()] = os.path.expandvars(val)
                except OSError:
                    pass
        except ImportError:
            pass
        # Windows 环境块大小写不敏感：统一大写键名；'Path' 与 'PATH' 双写会让子进程拿到
        # 非预期的那份（t_6cfe7865 实测：ssh 解析不到 → WinError 2）。PATH 两段合并去重。
        env = {k.upper(): v for k, v in env.items()}
        if "PATH" in reg:
            merged, seen = [], set()
            for seg in (env.get("PATH") or "", reg.pop("PATH")):
                for p in seg.split(";"):
                    low = p.rstrip("\\").lower()
                    if p and low not in seen:
                        seen.add(low)
                        merged.append(p)
            env["PATH"] = ";".join(merged)
        env.update(reg)
        env.update({k.upper(): v for k, v in _WIN_ENV_DEFAULTS.items()})
        C = str.upper
    else:
        C = (lambda s: s)
    win = env.get(C("SystemRoot")) or env.get(C("windir")) or r"C:\Windows"
    env[C("SystemRoot")] = win
    env.setdefault(C("windir"), win)
    env.setdefault(C("TEMP"), os.path.join(win, "Temp"))
    env.setdefault(C("TMP"), env[C("TEMP")])
    env.setdefault(C("ComSpec"), os.path.join(win, "System32", "cmd.exe"))
    env.setdefault(C("PATHEXT"), ".COM;.EXE;.BAT;.CMD")
    home = env.get(C("USERPROFILE")) or os.path.expanduser("~")
    if not home or home == "~":
        home = r"C:\Users\xiach"
    env.setdefault(C("USERPROFILE"), home)
    env.setdefault(C("HOME"), home)
    env.setdefault(C("USERNAME"), "xiach")
    must = [os.path.join(win, "System32"), os.path.join(win, "System32", "OpenSSH"),
            os.path.join(win, "System32", "Wbem"), win]
    parts = [p for p in (env.get(C("PATH")) or "").split(";") if p and os.path.isdir(p)]
    lowered = [p.rstrip("\\").lower() for p in parts]
    for d in must:
        if os.path.isdir(d) and d.rstrip("\\").lower() not in lowered:
            parts.append(d)
    env[C("PATH")] = ";".join(parts)
    return env


_REMOTE_PS = "E:\\_hermes_errdaily_probe.ps1"


def ssh_ps(script, timeout=420):
    """远端执行 PowerShell 脚本（只读约定），返回 (stdout, stderr)。

    传输方式定稿（2026-09-27 深夜实测，血泪史，勿回退）：
      * `-EncodedCommand <整段 b64(UTF-16LE)>`：撞 cmd.exe 8191 命令行上限（UTF-16 膨胀 x2.67）。
      * 「EncodedCommand bootstrap + [Console]::In.ReadToEnd()」：Windows OpenSSH 下**死锁**
        （远端 PS 等 stdin EOF、ssh 不回送；实测 45s/120s/180s 全挂），且每次挂死在远端留下
        一个常驻 powershell.exe（Services 会话，实测堆积 8 个）。即原 t_d054ba36 实现的形态。
      * 「-Command - 脚本原文走 stdin」：小脚本可用；本工具全文（4.7KB，三段拼接）在主机
        负载上来后**静默返回空输出**（rc=0、stdout/stderr 全空、~30s 即返回；而 p1/p2/vapp
        分段单跑都正常）——PS 5.1 对 stdin 长脚本的行为不可靠，不能依赖。
      * 现在 = tools/mas_idle_emulators.py 同款两跳：
        ① b64(BOM+UTF-8) 单行上传到远端 `E:\\_hermes_errdaily_probe.ps1`（单条语句，无解析
           歧义）；上传必须回显 `written N` 才算成功（否则宁可报错，也不跑到旧脚本）；
        ② `powershell -NoProfile -ExecutionPolicy Bypass -File <远端路径>` 执行（stdin=DEVNULL，
           无 stdin 载荷）。BOM 规避 PS 5.1 无 BOM 中文乱码。
    """
    prefix = ("$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; "
              "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
    payload = b"\xef\xbb\xbf" + (prefix + script).encode("utf-8")
    b64 = base64.b64encode(payload).decode("ascii")
    upload = ("$b64='%s'\n"
              "$b=[Convert]::FromBase64String($b64)\n"
              "[IO.File]::WriteAllBytes('%s', $b)\n"
              "Write-Output ('written ' + $b.Length)\n" % (b64, _REMOTE_PS))
    up = [_ssh_exe(), "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
          SSH_HOST, "chcp 65001 >nul & powershell -NoProfile -Command -"]
    r = subprocess.run(up, input=upload.encode("utf-8"), capture_output=True,
                       timeout=min(timeout, 180), env=_child_env())
    if b"written" not in r.stdout:
        raise RuntimeError("SSH 上传未确认: " + (r.stdout + r.stderr).decode("utf-8", "replace")[:300])
    run = [_ssh_exe(), "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
           SSH_HOST, "powershell -NoProfile -ExecutionPolicy Bypass -File " + _REMOTE_PS]
    r = subprocess.run(run, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
                       env=_child_env())
    out = r.stdout.decode("utf-8", "replace")
    err = r.stderr.decode("utf-8", "replace")
    return out, err


def collect_remote(d, timeout=420):
    d1s = d.isoformat()
    hist = HIST_ROOT + "\\" + d1s
    hist_dirs = ", ".join("'%s\\%s'" % (HIST_ROOT, x) for x in
                          ((d - dt.timedelta(days=1)).isoformat(), d1s,
                           (d + dt.timedelta(days=1)).isoformat()))
    _st, _en, win, win_app = window(d)
    ps = (_PS_TMPL
          .replace("@@WINDOW_APP@@", win_app)
          .replace("@@WINDOW@@", win)
          .replace("@@HIST_DIRS@@", hist_dirs)
          .replace("@@HIST@@", hist)
          .replace("@@T0@@", d1s + " 00:00:00")
          .replace("@@MAA_GLOB@@", MAA_GLOB)
          .replace("@@APP@@", APP_LOG)
          .replace("@@CAP_GUI@@", str(CAP_GUI))
          .replace("@@CAP_ASST@@", str(CAP_ASST))
          .replace("@@CAP_APP@@", str(CAP_APP)))
    out, err = ssh_ps(ps, timeout=timeout)
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("__JSON__"):
            return json.loads(line[len("__JSON__"):])
    raise RuntimeError("SSH 采集失败（无 __JSON__ 标记）: " + ((out or "(空)")[-200:]) + " ｜ stderr: " + ((err or "(空)")[-400:]))


# ---------------------------------------------------------------- REST（只读，可降级）

def post(path, body=None, timeout=30):
    req = urllib.request.Request(API + path, data=json.dumps(body or {}).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def rest_maps():
    """-> (script->path, name->path, uid->account)。失败 -> ({}, {}, {}）。"""
    s2p, n2p, uid2acct = {}, {}, {}
    try:
        scripts = (post("/api/scripts/get", {"scriptId": None}).get("data") or {})
    except Exception:  # noqa: BLE001
        return s2p, n2p, uid2acct
    for sid, s in scripts.items():
        info = (s or {}).get("Info") or {}
        name = str(info.get("Name") or "")
        path = str(info.get("Path") or "").replace("\\\\", "\\").rstrip("\\/")
        if name and path:
            s2p[sid] = path
            n2p[name] = path
        try:
            users = (post("/api/scripts/user/get", {"scriptId": sid}).get("data") or {})
        except Exception:  # noqa: BLE001
            users = {}
        for uid, u in users.items():
            nm = ((u or {}).get("Info") or {}).get("Name")
            if nm:
                uid2acct[str(uid)] = str(nm)
    return s2p, n2p, uid2acct


def slot_of_path(path):
    if not path:
        return "-"
    b = os.path.basename(path.rstrip("\\/"))
    return b if b else "-"


# ---------------------------------------------------------------- 解析日志行

_GUI_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\.\d+\]\[(\w+)\]\[([\w.]+)\]\s*<(\d+)>\s*(.*)$")
_APP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\.\d+ \| (\w+)\s*\| ([^|]+)\| (.*)$")


def parse_gui(dirname, s):
    no, _, line = str(s).partition("|")
    m = _GUI_RE.match(line)
    if not m:
        return None
    day, hms, level, comp, inst, msg = m.groups()
    return {"slot": dirname, "no": int(no or 0), "day": day, "hms": hms,
            "level": level, "comp": comp, "msg": msg.strip(), "line": line}


def parse_app(s):
    no, _, line = str(s).partition("|")
    m = _APP_RE.match(line)
    if not m:
        return None
    day, hms, level, module, msg = m.groups()
    return {"no": int(no or 0), "day": day, "hms": hms, "level": level,
            "module": module.strip(), "msg": msg.strip(), "line": line}


# ---------------------------------------------------------------- 聚合

CANON = {"开始唤醒": "startup", "自动公招": "recruit", "基建换班": "infrast",
         "理智作战": "fight", "信用收支": "mall", "领取奖励": "award"}


def canon_chain(chain):
    c = str(chain or "").strip()
    return CANON.get(c, c.lower())


def _pdt(s):
    """'yyyy-mm-dd HH:MM:SS' -> datetime 或 None。"""
    try:
        return dt.datetime.strptime(str(s).strip(), "%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return None


def fmt_ts(t, d):
    """窗口内时间显示：当天 HH:MM:SS；D+1 前缀 '⁺'；其余带月-日。"""
    if t is None:
        return "??:??:??"
    if t.date() == d:
        return t.strftime("%H:%M:%S")
    if t.date() == d + dt.timedelta(days=1):
        return "⁺" + t.strftime("%H:%M:%S")
    return t.strftime("%m-%d %H:%M:%S")


def _rlike(a, b):
    a, b = str(a or "").strip(), str(b or "").strip()
    return bool(a) and bool(b) and (a == b or a.startswith(b) or b.startswith(a))


def build(d, raw, s2p, n2p, uid2acct):
    _ws, _we, _rx, _rxa = window(d)
    w0 = _pdt(_ws) or dt.datetime.min
    w1 = _pdt(_we) or dt.datetime.max
    items = []
    hist_rows = []
    subtask_agg = Counter()
    gui_noise = Counter()
    app_noise = Counter()
    app_other = Counter()
    deficit_net = {}
    dedupe = {"hist_out_of_window": 0, "asst_vs_gui": 0, "app_vs_hist": 0}

    # ---- history：窗口判定用「内容末时间戳」（无内容日志回退 mtime）----
    hist = _l(raw.get("hist"))
    hist_ends = []  # [(acct, result, t_end)] 供 app 去重
    accounts = set()
    n_fail = 0
    n_ok = 0
    for rec in hist:
        acct = str(rec.get("acct") or "")
        script = re.sub(r"-\d{2}-\d{2}-\d{2}$", "", str(rec.get("file") or "").rsplit(".", 1)[0])
        t_end = (_pdt(rec.get("t_last"))
                 or _pdt(str(rec.get("mt_day") or "") + " " + str(rec.get("mt_hms") or "")))
        t_start = _pdt(rec.get("t_first")) or t_end
        if t_end is None or not (w0 <= t_end < w1):
            dedupe["hist_out_of_window"] += 1
            continue
        accounts.add(acct)
        slot = slot_of_path(n2p.get(script, ""))
        result = str(rec.get("result") or "")
        chains = _l(rec.get("chains"))
        hist_ends.append((acct, result, t_end))
        row = {"time": fmt_ts(t_end, d), "t_start": fmt_ts(t_start, d), "t_end": t_end,
               "slot": slot, "acct": acct, "script": script,
               "result": result or ("(json 损坏)" if rec.get("bad") else "(空)"),
               "chains": "、".join(chains) or "-"}
        it = classify_result(result, chains, rec.get("dropped"), rec.get("bad"))
        if result.strip() == "Success!" and not rec.get("bad"):
            n_ok += 1
            row.update({"cat": "—", "sev": "—"})
        else:
            n_fail += 1
            if it:
                it.update({"src": "history", "slot": slot, "ts": row["time"], "account": acct,
                           "script": script, "raw": result})
                items.append(it)
                row.update({"cat": it["cat"], "sev": it["sev"]})
            else:
                row.update({"cat": "-", "sev": "-"})
        hist_rows.append(row)
    hist_rows.sort(key=lambda r: r["t_end"])
    for _r in hist_rows:
        _r.pop("t_end", None)  # 仅供排序的 datetime，不进 JSON
    accounts = set(r["acct"] for r in hist_rows)

    # ---- slots: gui ----
    gui_chain_keys = []  # [(slot, canon_chain, t_dt)] 供 asst 去重
    for slot in _l(raw.get("slots")):
        dirname = str(slot.get("dir") or "")
        parsed = []
        for s in _l(slot.get("gui")):
            p = parse_gui(dirname, s)
            if p:
                parsed.append(p)
        parsed.sort(key=lambda x: x["no"])
        for i, p in enumerate(parsed):
            msg = p["msg"]
            t = _pdt(p["day"] + " " + p["hms"])
            ts = fmt_ts(t, d)
            if "任务出错" in msg:
                outcome = "none"
                for q in parsed[i + 1:]:
                    if q["msg"].startswith("任务已完成，但出现错误"):
                        outcome = "done_with_err"
                        break
                    if q["msg"].startswith("任务已全部完成"):
                        outcome = "all_done"
                        break
                chain_m = re.match(r"任务出错[:：]\s*(.+)$", msg)
                it = classify_gui(msg, chain=chain_m.group(1).strip() if chain_m else None, outcome=outcome)
            else:
                it = classify_gui(msg)
            if it is None:
                items.append(dict(_item(CAT_UNKNOWN, SEV_WARN, "gui [ERR] 未识别", raw=msg),
                                  src="gui", slot=dirname, ts=ts, account="", script=""))
                continue
            if isinstance(it, tuple):
                kind, why = it
                if kind == "noise":
                    gui_noise[why[:40]] += 1
                continue
            it.update({"src": "gui", "slot": dirname, "ts": ts, "account": "", "script": ""})
            if it["cat"] == CAT_DEFICIT:
                deficit_net[(dirname, p["hms"][:5])] = True
            if it.get("chain"):
                gui_chain_keys.append((dirname, canon_chain(it["chain"]), t))
            items.append(it)

        # ---- slots: asst ----
        for s in _l(slot.get("asst")):
            _no, _, line = str(s).partition("|")
            m = re.search(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\.\d+\]", line)
            hms = m.group(2) if m else "??:??:??"
            t = _pdt((m.group(1) + " " + m.group(2)) if m else "") or None
            ts = fmt_ts(t, d)
            if "TaskChainError" in line:
                cm = re.search(r'"taskchain"\s*:\s*"([^"]+)"', line)
                chain = cm.group(1) if cm else "?"
                cat, why = cat_of_chain(chain)
                ck = canon_chain(chain)
                dup = t is not None and any(
                    gs == dirname and gc == ck and gt is not None
                    and abs((t - gt).total_seconds()) <= 180 for (gs, gc, gt) in gui_chain_keys)
                if dup:
                    dedupe["asst_vs_gui"] += 1
                    continue
                if cat == CAT_CLIENT:
                    sev, note = SEV_WARN, "asst 链级（%s）— 登录/唤醒重试级" % chain
                elif cat == CAT_RESOURCE:
                    sev, note = SEV_BENIGN, "asst 链级（%s）— 公招资源类，等券自愈" % chain
                else:
                    sev, note = SEV_FATAL, "asst 链级（%s）— %s" % (chain, why)
                items.append(dict(_item(cat, sev, note, chain=chain),
                                  src="asst", slot=dirname, ts=ts, account="", script="",
                                  raw=line.strip()[-200:]))
            elif "SubTaskError" in line:
                cm = re.search(r'"taskchain"\s*:\s*"([^"]+)"', line)
                cls = re.search(r'"class"\s*:\s*"([^"]+)"', line)
                first = re.search(r'"first"\s*:\s*\["([^"]+)"', line)
                key = "%s / %s%s" % (cm.group(1) if cm else "?",
                                     cls.group(1).replace("asst::", "") if cls else "?",
                                     (" → " + first.group(1)) if first else "")
                subtask_agg[(dirname, key)] += 1
            elif "可用干员不足" in line:
                if (dirname, hms[:5]) not in deficit_net:
                    deficit_net[(dirname, hms[:5])] = True
                    items.append(dict(_item(CAT_DEFICIT, SEV_WARN,
                                            "基建换班/设施配置干员不够：实测继续跑完，不影响结果"),
                                      src="asst", slot=dirname, ts=ts, account="", script=""))
            elif ("掉线" in line) or ("截图失败" in line):
                fam = "游戏掉线" if "掉线" in line else "截图失败"
                items.append(dict(_item(CAT_NET, SEV_FATAL, "asst: " + fam),
                                  src="asst", slot=dirname, ts=ts, account="", script=""))

    # ---- app.log ----
    for s in _l((raw.get("app") or {}).get("lines")):
        p = parse_app(s)
        if not p:
            continue
        t = _pdt(p["day"] + " " + p["hms"])
        ts = fmt_ts(t, d)
        it = classify_app(p["level"], p["module"], p["msg"])
        if isinstance(it, tuple):
            kind, why = it
            if kind == "noise":
                app_noise[why] += 1
            elif kind == "other":
                app_other[why] += 1
            continue
        acct = ""
        um = re.search(r"用户[:：]\s*([0-9a-fA-F-]{8,})", p["msg"])
        if um:
            acct = uid2acct.get(um.group(1), "")
        # 「代理任务异常」= history 的用户级镜像：与窗口内 history 尝试匹配则去重
        if "代理任务异常" in p["msg"] and acct and t is not None:
            res = it.get("result") or ""
            if any(a == acct and _rlike(res, r) and abs((t - te).total_seconds()) <= 1800
                   for (a, r, te) in hist_ends):
                dedupe["app_vs_hist"] += 1
                continue
        it.update({"src": "app", "slot": "-", "ts": ts, "account": acct, "script": ""})
        items.append(it)

    # ---- 登录类升级 ----
    login_n = sum(1 for it in items if it["cat"] == CAT_CLIENT and "登录" in (it.get("note") or "")
                  and it["sev"] != SEV_FATAL)
    escalate = login_n >= LOGIN_ESCALATE
    if escalate:
        for it in items:
            if it["cat"] == CAT_CLIENT and "登录" in (it.get("note") or "") and it["sev"] == SEV_WARN:
                it["sev"] = SEV_FATAL
                it["note"] += "（同日 ≥%d 次，升级为告警）" % LOGIN_ESCALATE

    counts = Counter(it["cat"] for it in items)
    sevs = Counter(it["sev"] for it in items)
    top = counts.most_common(1)[0][0] if counts else "—"
    data = {
        "date": d.isoformat(),
        "window": window(d)[:2],
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items": items,
        "hist": {"accounts": sorted(accounts), "attempts": len(hist_rows), "ok": n_ok, "fail": n_fail,
                 "rows": hist_rows, "out_of_window": dedupe["hist_out_of_window"]},
        "slots": [str(sl.get("dir")) for sl in _l(raw.get("slots"))],
        "app": {"rounds": (raw.get("app") or {}).get("rounds", 0),
                "noise": app_noise.most_common(8), "other": app_other.most_common(8)},
        "gui_noise": gui_noise.most_common(8),
        "subtask": subtask_agg.most_common(12),
        "dedupe": dedupe,
        "counts": {"n": len(items), "by_cat": dict(counts), "by_sev": dict(sevs),
                   "top": top, "fatal": sevs.get(SEV_FATAL, 0),
                   "warn": sevs.get(SEV_WARN, 0), "benign": sevs.get(SEV_BENIGN, 0),
                   "login_n": login_n, "escalated": escalate},
    }
    return data


def err_summary_line(data):
    c = data["counts"]
    return "ERR_SUMMARY=%d 条，Top=%s" % (c["n"], c["top"])


# ---------------------------------------------------------------- 报告渲染

def _fmt_slot(slot):
    return slot or "-"


def render_md(data):
    d = data["date"]
    c = data["counts"]
    L = []
    L.append("# MAA 错误日报 — %s" % d)
    L.append("")
    L.append("窗口 %s → %s ｜ 生成 %s（快照）" % (data["window"][0], data["window"][1], data["generated_at"]))
    L.append("")
    L.append("**%s**" % err_summary_line(data))
    L.append("")
    L.append("- 条目 %d（致命 %d ｜ 告警 %d ｜ 可忽略 %d）；来源：history 尝试 %d（失败 %d）/ %d 账号、槽位日志 %d 目录、app.log %d 轮"
             % (c["n"], c["fatal"], c["warn"], c["benign"],
                data["hist"]["attempts"], data["hist"]["fail"], len(data["hist"]["accounts"]),
                len(data["slots"]), data["app"]["rounds"]))
    if c["by_cat"]:
        L.append("- 分类计数：" + " ｜ ".join("%s×%d" % (k, v) for k, v in
                                              sorted(c["by_cat"].items(), key=lambda kv: -kv[1])))
    if c["escalated"]:
        L.append("- ⚠️ 登录类 %d 次 ≥ %d：升级为告警（查当日 MAA/游戏更新记录）" % (c["login_n"], LOGIN_ESCALATE))
    de = data.get("dedupe") or {}
    if any(de.values()):
        L.append("- 跨源去重：asst↔gui %d 条、app↔history %d 条；窗口外 history 尝试 %d 条已忽略。"
                 % (de.get("asst_vs_gui", 0), de.get("app_vs_hist", 0), de.get("hist_out_of_window", 0)))
    L.append("- 口径：多源同一起因可能重复计数（history=账号级权威、gui/asst=槽位级、app=用户级）；history 只留部分运行，不能当完成率。")
    L.append("")

    # §1 失败账号
    L.append("## 1. 失败账号（history maa_result，账号级）")
    L.append("")
    rows = [r for r in data["hist"]["rows"] if r["sev"] != "—"]
    L.append("| 时间（止） | 槽位 | 账号 | 脚本 | 结果 | 链条 | 归类/级别 |")
    L.append("|---|---|---|---|---|---|---|")
    if not rows:
        L.append("| - | - | （无失败记录） | | | | |")
    else:
        # 相同（账号+结果）折叠；链条取并集、归类取众数
        agg = OrderedDict()
        for r in rows:
            k = (r["acct"], r["result"])
            g = agg.get(k)
            if g is None:
                g = agg[k] = dict(n=0, first=None, last=None, chains=set(),
                                  cats=Counter(), sevs=Counter(), slot=r["slot"],
                                  acct=r["acct"], script=r["script"], result=r["result"])
            g["n"] += 1
            g["first"] = r["time"] if g["first"] is None else min(g["first"], r["time"])
            g["last"] = r["time"] if g["last"] is None else max(g["last"], r["time"])
            if r["chains"] != "-":
                g["chains"].update(r["chains"].split("、"))
            if r["cat"] != "-":
                g["cats"][r["cat"]] += 1
            if r["sev"] in (SEV_FATAL, SEV_WARN, SEV_BENIGN):
                g["sevs"][r["sev"]] += 1
        disp = list(agg.values())
        for g in disp[:30]:
            t = g["first"] if g["n"] == 1 else "%s~%s ×%d" % (g["first"], g["last"], g["n"])
            ch = "、".join(sorted(g["chains"])) or "-"
            cat = g["cats"].most_common(1)[0][0] if g["cats"] else "-"
            sev = SEV_NAMES.get(g["sevs"].most_common(1)[0][0], "") if g["sevs"] else ""
            L.append("| %s | %s | %s | %s | %s | %s | %s%s |"
                     % (t, _fmt_slot(g["slot"]), g["acct"], g["script"], g["result"], ch, cat,
                        ("/" + sev) if sev else ""))
        if len(disp) > 30:
            L.append("| … | 其余 %d 组 | | | | | |" % (len(disp) - 30))
    L.append("")

    # §2 槽位日志致命项
    L.append("## 2. 槽位日志致命项（gui/asst）")
    L.append("")
    fatal = [it for it in data["items"] if it["sev"] == SEV_FATAL and it["src"] in ("gui", "asst")]
    L.append("| 槽位 | 时间 | 链/家族 | 归类 | 说明 |")
    L.append("|---|---|---|---|---|")
    if not fatal:
        L.append("| - | - | （无） | | |")
    else:
        gk = OrderedDict()
        for it in fatal:
            k = (it["slot"], it.get("chain") or _fam_of(it))
            gk.setdefault(k, []).append(it)
        for (slot, fam), group in list(gk.items())[:28]:
            t0 = min(x["ts"] for x in group)
            t1 = max(x["ts"] for x in group)
            t = t0 if len(group) == 1 else "%s~%s ×%d" % (t0, t1, len(group))
            L.append("| %s | %s | %s | %s | %s |" % (slot, t, fam or "-", group[0]["cat"],
                                                     (group[0].get("note") or "")[:60]))
    L.append("")

    # §2b app 致命
    app_fatal = [it for it in data["items"] if it["sev"] == SEV_FATAL and it["src"] == "app"]
    if app_fatal:
        L.append("### app.log 致命项")
        L.append("")
        for it in app_fatal[:10]:
            L.append("- %s %s（%s）：%s" % (it["ts"], it.get("account") or "?", it["cat"], (it.get("note") or "")[:70]))
        L.append("")

    # §3 告警（不影响结果）
    L.append("## 3. 告警（不影响结果，无需动作）")
    L.append("")
    warns = [it for it in data["items"] if it["sev"] == SEV_WARN]
    if not warns:
        L.append("- （无）")
    else:
        wk = OrderedDict()
        for it in warns:
            k = (it["cat"], (it.get("note") or "")[:38])
            wk.setdefault(k, []).append(it)
        for (cat, note), group in sorted(wk.items(), key=lambda kv: -len(kv[1])):
            slots = Counter(x["slot"] for x in group)
            L.append("- **%s ×%d**（%s）— %s；例：%s %s"
                     % (cat, len(group), "/".join("%s×%d" % (s, n) for s, n in slots.most_common(4)),
                        note, group[0]["ts"], group[0]["slot"]))
    L.append("")

    # §4 已知可忽略
    benign = [it for it in data["items"] if it["sev"] == SEV_BENIGN]
    if benign:
        L.append("## 4. 已知可忽略（不补跑、不告警）")
        L.append("")
        bk = Counter((it["cat"], (it.get("note") or "")[:40]) for it in benign)
        for (cat, note), n in bk.most_common():
            L.append("- %s ×%d — %s" % (cat, n, note))
        L.append("")

    # §5 噪音
    L.append("## 5. 噪音（不用管）")
    L.append("")
    if data["gui_noise"]:
        L.append("- gui.log：" + "；".join("%s ×%d" % (k, v) for k, v in data["gui_noise"]))
    if data["app"]["noise"]:
        L.append("- app.log：" + "；".join("%s ×%d" % (k, v) for k, v in data["app"]["noise"]))
    if data["app"]["other"]:
        L.append("- app.log 其他（未分类，前 5）：")
        for k, v in data["app"]["other"][:5]:
            L.append("  - ×%d %s" % (v, k))
    L.append("")

    # §6 细因 / 口径
    if data["subtask"]:
        L.append("## 6. asst SubTaskError 链内细因（计数）")
        L.append("")
        for (slot, key), n in data["subtask"]:
            L.append("- %s ×%d：%s" % (slot, n, key))
        L.append("")

    L.append("## 口径")
    L.append("")
    L.append("- 窗口 [D 05:00, D+1 05:00)：跨零点夜班尾巴算前一天；history 目录按 AUTO-MAS 班次日归档。")
    L.append("- 归类：客户端未就绪（登录/唤醒/模拟器/客户端）；资源类（公招券类）；干员不足（基建设施配置）；")
    L.append("  网络（掉线/截图链路）；人为（手动中止）；未知（其余，含作战/基建/信用/领奖链错误与超时）。")
    L.append("- 致命 = 真失败需关注；告警 = 出现但已实测/已恢复、不影响结果；可忽略 = 已知无需动作。")
    L.append("- 复跑：`python tools/maa_error_daily.py --date %s`；机读：`runtime/reports/maa_errors_%s.json`。" % (d, d))
    L.append("")
    return "\n".join(L)


def _fam_of(it):
    raw = it.get("raw") or ""
    if "截图失败" in raw:
        return "截图失败"
    if "掉线" in raw:
        return "游戏掉线"
    if "模拟器启动失败" in raw:
        return "模拟器启动失败"
    return ""


# ---------------------------------------------------------------- 子命令

def cmd_check():
    problems = []
    try:
        r = post("/api/scripts/get", {"scriptId": None})
        if r.get("code") != 200:
            problems.append("REST code=%s" % r.get("code"))
        n = len(r.get("data") or {})
    except Exception as e:  # noqa: BLE001
        problems.append("REST 不可达: %s" % e)
        n = 0
    ssh_ok = False
    try:
        out, _err = ssh_ps("Write-Output ('H=' + (hostname)); "
                     "Write-Output ('APP=' + (Test-Path '%s')); "
                     "Write-Output ('MAA=' + @(Get-ChildItem '%s' -Directory).Count)" % (APP_LOG, MAA_GLOB),
                     timeout=90)
        ssh_ok = "H=" in out
        if not ssh_ok:
            problems.append("SSH 无输出")
    except Exception as e:  # noqa: BLE001
        problems.append("SSH 失败: %s" % e)
    print("maa_error_daily --check: %s" % ("FAIL" if problems else "OK"))
    print("  REST scripts=%d ｜ SSH=%s" % (n, "ok" if ssh_ok else "fail"))
    for p in problems:
        print("  - " + p)
    return 2 if problems else 0


def _selftest():
    ok = True
    cases = [
        # (fn, args, 期望)
        (classify_result, ("Success!",), None),
        (classify_result, (RESULT_LOGIN,), (CAT_CLIENT, SEV_WARN)),
        (classify_result, ("模拟器启动失败, MAA 未实际运行",), (CAT_CLIENT, SEV_FATAL)),
        (classify_result, (RESULT_QUIT, ["自动公招"]), (CAT_RESOURCE, SEV_BENIGN)),
        (classify_result, (RESULT_QUIT, ["基建换班", "自动公招"]), (CAT_UNKNOWN, SEV_FATAL)),
        (classify_result, (RESULT_QUIT, [], False, True), (CAT_UNKNOWN, SEV_WARN)),
        (classify_result, (RESULT_TIMEOUT,), (CAT_UNKNOWN, SEV_FATAL)),
        (classify_result, (RESULT_HUMAN,), (CAT_HUMAN, SEV_BENIGN)),
        (classify_result, (RESULT_QUIT, ["开始唤醒"]), (CAT_CLIENT, SEV_WARN)),
        (classify_result, (RESULT_ABORT, ["理性作战"]), (CAT_UNKNOWN, SEV_FATAL)),
    ]
    for fn, args, exp in cases:
        got = fn(*args)
        if exp is None:
            good = got is None
        else:
            good = got is not None and (got["cat"], got["sev"]) == exp
        if not good:
            ok = False
            print("FAIL %s%r -> %r（期望 %r）" % (fn.__name__, args, got, exp))
    gui_cases = [
        ("任务出错: 开始唤醒", dict(chain="开始唤醒", outcome="none"), (CAT_CLIENT, SEV_WARN)),
        ("任务出错: 自动公招", dict(chain="自动公招", outcome="done_with_err"), (CAT_RESOURCE, SEV_BENIGN)),
        ("任务出错: 理智作战", dict(chain="理智作战", outcome="all_done"), (CAT_UNKNOWN, SEV_WARN)),
        ("可用干员不足", dict(), (CAT_DEFICIT, SEV_WARN)),
        ("截图失败，如反复出现请尝试重启或更换模拟器！", dict(), (CAT_NET, SEV_FATAL)),
        ("游戏掉线，停止任务", dict(), (CAT_NET, SEV_FATAL)),
    ]
    for msg, kw, exp in gui_cases:
        got = classify_gui(msg, **kw)
        good = got is not None and not isinstance(got, tuple) and (got["cat"], got["sev"]) == exp
        if not good:
            ok = False
            print("FAIL classify_gui(%r) -> %r（期望 %r）" % (msg, got, exp))
    for msg, exp in [("任务已完成，但出现错误！", ("marker", "done_with_err")),
                     ("Failed to set startup: x", None)]:
        got = classify_gui(msg)
        good = (isinstance(got, tuple) and got == exp) if exp else (isinstance(got, tuple) and got[0] == "noise")
        if not good:
            ok = False
            print("FAIL classify_gui(%r) -> %r" % (msg, got))
    app_cases = [
        ("WARNING", "MAA 自动代理", "用户: abcdef12-3456-7890-abcd-ef1234567890 - 代理任务异常: " + RESULT_LOGIN,
         (CAT_CLIENT, SEV_WARN)),
        ("WARNING", "MAA 自动代理", "用户: abcdef12-3456-7890-abcd-ef1234567890 - 代理任务异常: " + RESULT_QUIT,
         (CAT_UNKNOWN, SEV_FATAL)),
        ("INFO", "配置管理", "开始处理 MAA 日志, 日志长度: 28", ("skip2",)),
        ("WARNING", "MuMu模拟器管理", "屏蔽 MuMu 应用商店悬浮广告失败: adb.exe: device offline", ("noise2",)),
    ]
    for level, mod, msg, exp in app_cases:
        got = classify_app(level, mod, msg)
        if exp == ("skip2",) or exp == ("noise2",):
            good = isinstance(got, tuple)
        else:
            good = got is not None and not isinstance(got, tuple) and (got["cat"], got["sev"]) == exp
        if not good:
            ok = False
            print("FAIL classify_app(%r) -> %r（期望 %r）" % (msg, got, exp))
    # 窗口正则
    d = dt.date(2026, 9, 27)
    rx = re.compile(window(d)[2])
    wx = [("[2026-09-27 05:00:00.000]x", True), ("[2026-09-27 04:59:59.000]x", False),
          ("[2026-09-27 23:59:59.000]x", True), ("[2026-09-28 03:00:00.000]x", True),
          ("[2026-09-28 05:00:01.000]x", False), ("[2026-09-26 21:00:00.000]x", False)]
    for s, exp in wx:
        if bool(rx.search(s)) != exp:
            ok = False
            print("FAIL window regex: %r 期望 %s" % (s, exp))
    print("maa_error_daily --selftest: %s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="MAA 每日错误报告聚合（只读）")
    ap.add_argument("--date")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--timeout", type=int, default=420)
    a = ap.parse_args()
    if a.check:
        return cmd_check()
    if a.selftest:
        return _selftest()

    d = resolve_date(a.date)
    try:
        raw = collect_remote(d, timeout=a.timeout)
    except Exception as e:  # noqa: BLE001
        msg = "ERR_SUMMARY=UNKNOWN:%s" % str(e).replace("\n", " ")[:160]
        print(msg)
        return 2
    s2p, n2p, uid2acct = rest_maps()
    data = build(d, raw, s2p, n2p, uid2acct)
    data["meta"] = {"rest_ok": bool(n2p), "ssh_host": SSH_HOST, "api": API,
                    "caps": {"gui": CAP_GUI, "asst": CAP_ASST, "app": CAP_APP}}
    line = err_summary_line(data)

    md = render_md(data)
    outs = []
    if not a.no_write and (not a.json or a.out):
        os.makedirs(REPORTS, exist_ok=True)
        mp = a.out or os.path.join(REPORTS, "maa_errors_%s.md" % d.isoformat())
        jp = os.path.splitext(mp)[0] + ".json"
        # 原子写（tmp+replace）：班次日报与 30min 巡检可能同时触发本工具，
        # 直接覆盖会让并发读方吃到半截文件（2026-09-28 日报接入时发现的竞态）。
        for path, payload in ((mp, md), (jp, json.dumps(data, ensure_ascii=False, indent=1))):
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, path)
        outs = [mp, jp]

    if a.summary:
        print(line)
        return 0
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0
    c = data["counts"]
    print("[maa_error_daily] %s 窗口 %s → %s" % (d.isoformat(), data["window"][0], data["window"][1]))
    print("  history: %d 账号 / %d 尝试（成功 %d，失败 %d）｜ 槽位日志: %d 目录 ｜ app.log: %d 轮"
          % (len(data["hist"]["accounts"]), data["hist"]["attempts"], data["hist"]["ok"],
             data["hist"]["fail"], len(data["slots"]), data["app"]["rounds"]))
    print("  条目 %d（致命 %d ｜ 告警 %d ｜ 可忽略 %d）Top=%s"
          % (c["n"], c["fatal"], c["warn"], c["benign"], c["top"]))
    for p in outs:
        print("写入: %s" % p)
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
