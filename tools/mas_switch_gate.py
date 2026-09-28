#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""切号注入闸门 v2（只读）—— 校验 MAA 目录 config\gui.new.json 的账号切换注入是否符合口径。

规则（v2，2026-09-27 定稿；补丁 = AUTO-MAS app/task/MAA/AutoProxy.py L766-779）：
  Server=Official ⇒ StartUpTask.AccountName 必须是掩码（\d{3}\*{4}\d{4}，如 134****5276）
                    且 AccountSwitchEnabled=true（否则 MAA 静默不切号 → 串号）
  其它服（Bilibili/YoStarJP/txwy/YoStarEN/YoStarKR）
                  ⇒ AccountName 必须为 "" 且 AccountSwitchEnabled=false
  任何不符 ⇒ VIOLATION（只报告，不改配置）。

数据源（全部只读）：
  * AUTO-MAS REST（默认 http://100.79.173.69:36163）：脚本→MAA 目录、脚本→账号（Server/Id）
  * ssh 到主机读 <MAA目录>\config\gui.new.json

用法：
  python tools/mas_switch_gate.py             # 详细表 + 违规/地址清单 + 末行 SWITCH_GATE=
  python tools/mas_switch_gate.py --check     # 紧凑模式（巡检 cron 用），末行 SWITCH_GATE=OK|VIOLATION:<槽位>|UNKNOWN:<槽位>
  python tools/mas_switch_gate.py --json      # 机器可读（供 MCP 工具/下游）
  python tools/mas_switch_gate.py --selftest  # 纯本地规则自检（不连网）
退出码：0=OK；1=VIOLATION；2=UNKNOWN（读不到/判不了，fail-closed 不当通过）
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

API = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")

# 兜底槽位表（REST 不可达时仍做文件级校验）；与 REST 推导结果一致时就是这 12 个目录。
DEFAULT_DIRS = [r"E:\MAA-Multi\MAA_%d" % i for i in range(1, 13)]
MAA_DIR_RE = re.compile(r"^[A-Za-z]:[\\/]MAA-Multi[\\/]MAA_\d+$", re.I)
MASK_RE = re.compile(r"^\d{3}\*{4}\d{4}$")
PHONE_RE = re.compile(r"^\d{11}$")

# MAA 6.x ClientType 的三种出现形态要归一：
#   * AUTO-MAS 补丁写入：整数 0..5（见 AutoProxy.py L59-66）
#   * MAA 启动后回写：WPF 枚举名 —— Official / Bilibili / Txwy / EN / JP / KR（gz 实测 MAA_11=JP）
#   * 协议文档：YoStarEN / YoStarJP / YoStarKR / txwy
CT_INT_TO_NAME = {0: "Official", 1: "Bilibili", 2: "YoStarEN", 3: "YoStarJP", 4: "YoStarKR", 5: "txwy"}
CT_ALIASES = {
    "official": "Official", "bilibili": "Bilibili", "txwy": "txwy", "tw": "txwy",
    "en": "YoStarEN", "yostaren": "YoStarEN", "jp": "YoStarJP", "yostarjp": "YoStarJP",
    "kr": "YoStarKR", "yostarkr": "YoStarKR",
}
CT_KNOWN = set(CT_INT_TO_NAME.values())


def norm_path(p) -> str:
    return str(p or "").replace("\\\\", "\\").strip()


def mask_phone(v: str) -> str:
    return v[:3] + "****" + v[7:] if PHONE_RE.fullmatch(v) else v


def normalize_server(ct):
    """ClientType 可能是 'Official'/‘JP’ 字符串、0 整数或 '0' 字符串 ⇒ 归一成服名；不认识返回 None。"""
    if ct is None:
        return None
    if isinstance(ct, bool):
        return None
    if isinstance(ct, int):
        return CT_INT_TO_NAME.get(ct)
    s = str(ct).strip()
    if s.isdigit() and int(s) in CT_INT_TO_NAME:
        return CT_INT_TO_NAME[int(s)]
    return CT_ALIASES.get(s.lower())


# ---------------------------------------------------------------- REST 侧（只读）

def _post(path: str, body: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body or {}).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def rest_slots() -> dict:
    """脚本/账号信息 → {dir_lower: {path, name, servers, masks, odd_official_id_lens, scripts}}。

    servers: 该目录下出现过的服名集合；masks: 该目录官服账号的期望掩码集合。
    """
    data = _post("/api/scripts/get", {"scriptId": None}).get("data") or {}
    slots: dict = {}
    for sid, s in data.items():
        info = (s or {}).get("Info") or {}
        p = norm_path(info.get("Path"))
        if not MAA_DIR_RE.match(p):
            continue
        slot = slots.setdefault(p.lower(), {"path": p, "name": os.path.basename(p),
                                            "servers": set(), "masks": set(),
                                            "odd_official_id_lens": set(), "scripts": []})
        users = (_post("/api/scripts/user/get", {"scriptId": sid}).get("data") or {})
        us = []
        for uid, u in users.items():
            ui = (u or {}).get("Info") or {}
            srv = ui.get("Server")
            idv = str(ui.get("Id") or "")
            if srv:
                slot["servers"].add(str(srv))
            if srv == "Official":
                if PHONE_RE.fullmatch(idv):
                    slot["masks"].add(mask_phone(idv))
                elif idv:
                    slot["odd_official_id_lens"].add(len(idv))
            us.append({"uid": str(uid), "server": srv})
        slot["scripts"].append({"sid": str(sid), "name": info.get("Name"), "users": us})
    return slots


# ---------------------------------------------------------------- 远端文件侧（只读）

_PS_TMPL = r"""
$dirs = @(__DIRS__)
$res = @()
foreach ($d in $dirs) {
  $n = Split-Path $d -Leaf
  $f = Join-Path $d 'config\gui.new.json'
  if (-not (Test-Path $f)) {
    $res += [pscustomobject]@{name=$n; path=$d; status='MISSING'; error='gui.new.json not found'}
    continue
  }
  try {
    $j = Get-Content -Raw -Encoding UTF8 $f | ConvertFrom-Json
    $tasks = @()
    foreach ($t in @($j.Configurations.Default.TaskQueue)) {
      $tt = ''; $ty = ''
      if ($t.PSObject.Properties.Name -contains 'TaskType') { $tt = [string]$t.TaskType }
      if ($t.PSObject.Properties.Name -contains '$type') { $ty = [string]$t.'$type' }
      if ($tt -ne 'StartUp' -and $ty -ne 'StartUpTask') { continue }
      $acct = $null; $sw = $null
      if ($t.PSObject.Properties.Name -contains 'AccountName') { $acct = [string]$t.AccountName }
      if ($t.PSObject.Properties.Name -contains 'AccountSwitchEnabled') { $sw = [bool]$t.AccountSwitchEnabled }
      $tasks += [pscustomobject]@{accountName=$acct; switchEnabled=$sw}
    }
    $ct = $null
    $def = $j.Configurations.Default
    if ($def.PSObject.Properties.Name -contains 'Gui') {
      $rs = $def.Gui.RuntimeSettings
      if ($rs -and ($rs.PSObject.Properties.Name -contains 'ClientType')) { $ct = $rs.ClientType }
    }
    $mt = (Get-Item $f).LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
    $res += [pscustomobject]@{name=$n; path=$d; status='OK'; clientType=$ct; tasks=$tasks; mtime=$mt}
  } catch {
    $res += [pscustomobject]@{name=$n; path=$d; status='ERROR'; error=[string]$_.Exception.Message}
  }
}
Write-Output ('__GATE_JSON__' + (ConvertTo-Json -Compress -Depth 8 @($res)))
"""


def _ssh_ps(script: str, timeout: int = 240) -> str:
    prefix = ("$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; "
              "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
    enc = (prefix + script).encode("utf-16-le")
    b64 = base64.b64encode(enc).decode("ascii")
    cmd = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
           SSH_HOST, "powershell -NoProfile -EncodedCommand " + b64]
    # stdin=DEVNULL 必须：在 MCP stdio server 里跑时，ssh 若继承父进程的 stdin 管道会挂死到超时
    # （巡检 triage 的 ssh 超时同一根因）。
    r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


def read_dirs(dirs: list[str]) -> list[dict]:
    """读一批 MAA 目录的 gui.new.json 摘要；返回按入参顺序的 fileinfo 列表。"""
    if not dirs:
        return []
    lit = ",".join("'" + d.replace("'", "''") + "'" for d in dirs)
    out = _ssh_ps(_PS_TMPL.replace("__DIRS__", lit))
    payload = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("__GATE_JSON__"):
            payload = json.loads(line[len("__GATE_JSON__"):])
            break
    if payload is None:
        return [{"name": os.path.basename(d), "path": d, "status": "ERROR",
                 "error": "ssh-output-no-marker"} for d in dirs]
    if isinstance(payload, dict):
        payload = [payload]
    got = {str(x.get("name")): x for x in payload}
    return [got.get(os.path.basename(d), {"name": os.path.basename(d), "path": d,
                                          "status": "ERROR", "error": "missing-in-ssh-output"}) for d in dirs]


# ---------------------------------------------------------------- 规则判定

def evaluate(slot: dict, fi: dict) -> dict:
    """对单个槽位判定。slot=REST 元数据（可为 {}），fi=远端文件摘要。返回 verdict/issues/notes。"""
    name = fi.get("name")
    res = {"name": name, "status": fi.get("status"), "issues": [], "notes": [], "verdict": "OK",
           "server": None, "account": None, "switch": None, "mtime": fi.get("mtime")}
    if fi.get("status") != "OK":
        res.update(verdict="UNKNOWN", issues=["file-%s" % str(fi.get("status", "?")).lower()
                                              + (("(%s)" % str(fi.get("error"))[:60]) if fi.get("error") else "")])
        return res
    server = normalize_server(fi.get("clientType"))
    if server is None:
        res.update(verdict="UNKNOWN", issues=["client-type-unknown(%r)" % (fi.get("clientType"),)])
        return res
    res["server"] = server
    tasks = fi.get("tasks") or []
    if not tasks:
        res.update(verdict="UNKNOWN", issues=["no-startup-task"])
        return res
    official = server == "Official"
    for t in tasks:
        acct = t.get("accountName")
        acct = "" if acct is None else str(acct)
        sw = t.get("switchEnabled")
        if official:
            if not MASK_RE.fullmatch(acct):
                res["issues"].append("official-account-not-masked(%s)" % (acct or "<empty>"))
            elif slot.get("masks") and acct not in slot["masks"]:
                res["notes"].append("mask-not-in-dir-accounts")
            if sw is not True:
                res["issues"].append("official-switch-%s" % ("missing" if sw is None else "not-true"))
        else:
            if acct != "":
                res["issues"].append("nonofficial-account-not-empty(%s)" % acct[:24])
            if sw is True:
                res["issues"].append("nonofficial-switch-on")
            elif sw is None:
                res["notes"].append("switch-missing")
        if official and slot.get("odd_official_id_lens"):
            res["notes"].append("official-id-not-11-digits")
    res["verdict"] = "VIOLATION" if res["issues"] else "OK"
    res["server"] = server
    res["account"] = str(tasks[-1].get("accountName") or "") if tasks else None
    res["switch"] = tasks[-1].get("switchEnabled") if tasks else None
    if len(tasks) > 1:
        res["notes"].append("multi-startup-tasks(%d)" % len(tasks))
    return res


# ---------------------------------------------------------------- 主流程

def run_gate(dirs: list[str] | None = None, timeout: int = 240, strict_rest: bool = False) -> dict:
    """全流程（只读），返回结构化结果（供 --json / MCP 工具共用）。"""
    started = dt.datetime.now()
    rest_meta: dict = {}
    rest_state = "ok"
    rest_error = None
    try:
        rest_meta = rest_slots()
    except Exception as e:  # noqa: BLE001
        rest_state = "down"
        rest_error = str(e)[:200]
    if dirs is None:
        if rest_meta:
            def _dirnum(p: str) -> int:
                m = re.search(r"(\d+)$", p)
                return int(m.group(1)) if m else 0
            dirs = sorted((v["path"] for v in rest_meta.values()), key=_dirnum)
        else:
            dirs = list(DEFAULT_DIRS)
    infos = read_dirs(dirs)
    bad = [d for d, fi in zip(dirs, infos) if fi.get("status") != "OK"]
    if bad:
        time.sleep(3)
        retry = {fi["name"]: fi for fi in read_dirs(bad)}
        infos = [retry.get(fi["name"], fi) for fi in infos]
    slots = []
    for d, fi in zip(dirs, infos):
        meta = rest_meta.get(d.lower(), {})
        ev = evaluate(meta, fi)
        ev["path"] = d
        if meta:
            ev["expected_servers"] = sorted(meta.get("servers") or [])
            ev["expected_masks"] = sorted(meta.get("masks") or [])
            ev["scripts"] = [s.get("name") for s in meta.get("scripts") or []]
            if ev.get("server") and ev["expected_servers"] and ev["server"] not in ev["expected_servers"]:
                ev["notes"].append("server-not-in-dir-scripts(%s)" % ev["server"])
        else:
            ev["expected_servers"] = []
            ev["notes"].append("no-rest-meta")
        slots.append(ev)
    viol = [s["name"] for s in slots if s["verdict"] == "VIOLATION"]
    unk = [s["name"] for s in slots if s["verdict"] == "UNKNOWN"]
    if viol:
        gate = "VIOLATION:" + ",".join(viol)
    elif unk:
        def _reason(s: dict) -> str:
            return ",".join(s["issues"]).replace("(", "[").replace(")", "]")
        gate = "UNKNOWN:" + ",".join("%s(%s)" % (s["name"], _reason(s)) for s in slots if s["verdict"] == "UNKNOWN")
    else:
        gate = "OK"
    out = {
        "tool": "mas_switch_gate",
        "generated_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "api": API,
        "rest": {"state": rest_state, "error": rest_error, "scripts": sum(len(v["scripts"]) for v in rest_meta.values()),
                 "slots": len(rest_meta)},
        "dirs": dirs,
        "slots": slots,
        "violations": viol,
        "unknown": unk,
        "switch_gate": gate,
    }
    if strict_rest and rest_state != "ok":
        out["switch_gate"] = gate if gate.startswith("VIOLATION") else "UNKNOWN:rest-down"
    return out


def _fmt_val(v) -> str:
    if v is None:
        return "-"
    if v is True:
        return "true"
    if v is False:
        return "false"
    return str(v)


def print_report(res: dict, mode: str) -> None:
    if mode == "json":
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    if mode == "check":
        for s in res["slots"]:
            if s["verdict"] != "OK":
                print("%s %s: %s%s" % (s["verdict"], s["name"],
                                       "; ".join(s["issues"]),
                                       ("  [note: %s]" % "; ".join(s["notes"])) if s["notes"] else ""))
        print("切号注入闸门: %d 槽位, 违规 %d, 未知 %d, rest=%s, %s" % (
            len(res["slots"]), len(res["violations"]), len(res["unknown"]), res["rest"]["state"],
            res["generated_at"]))
        print("SWITCH_GATE=%s" % res["switch_gate"])
        return
    # 详细模式
    print("切号注入闸门 v2（只读）  %s" % res["generated_at"])
    print("REST=%s 槽位=%d 脚本=%d" % (res["rest"]["state"], len(res["slots"]), res["rest"]["scripts"]))
    print("")
    print("%-8s %-10s %-14s %-6s %-9s %s" % ("槽位", "服务器", "账号注入", "开关", "判定", "备注"))
    for s in res["slots"]:
        note = "; ".join(s["notes"])
        if s["verdict"] == "UNKNOWN":
            note = (note + "; " if note else "") + "; ".join(s["issues"])
        print("%-8s %-10s %-14s %-6s %-9s %s" % (
            s["name"], s.get("server") or "-", _fmt_val(s.get("account")), _fmt_val(s.get("switch")),
            s["verdict"], note))
    print("")
    if res["violations"]:
        print("违规清单（需人工处理，脚本不改配置）:")
        for s in res["slots"]:
            if s["verdict"] == "VIOLATION":
                print("  [VIOLATION] %s: %s" % (s["name"], "; ".join(s["issues"])))
    if res["unknown"]:
        print("未知（读不到/判不了，fail-closed）:")
        for s in res["slots"]:
            if s["verdict"] == "UNKNOWN":
                print("  [UNKNOWN] %s: %s" % (s["name"], "; ".join(s["issues"])))
    print("")
    print("SWITCH_GATE=%s" % res["switch_gate"])


# ---------------------------------------------------------------- 自检

_SELFTEST_CASES = [
    ("official-ok", {"server": "Official", "masks": {"134****5276"}},
     {"name": "MAA_6", "status": "OK", "clientType": "Official",
      "tasks": [{"accountName": "134****5276", "switchEnabled": True}]}, "OK"),
    ("official-int-clienttype", {"server": None, "masks": set()},
     {"name": "MAA_6", "status": "OK", "clientType": 0,
      "tasks": [{"accountName": "134****5276", "switchEnabled": True}]}, "OK"),
    ("official-not-masked", {"masks": set()},
     {"name": "MAA_3", "status": "OK", "clientType": "Official",
      "tasks": [{"accountName": "", "switchEnabled": True}]}, "VIOLATION"),
    ("official-raw-phone", {"masks": set()},
     {"name": "MAA_3", "status": "OK", "clientType": "Official",
      "tasks": [{"accountName": "13412345276", "switchEnabled": True}]}, "VIOLATION"),
    ("official-switch-off", {"masks": set()},
     {"name": "MAA_3", "status": "OK", "clientType": "Official",
      "tasks": [{"accountName": "134****5276", "switchEnabled": False}]}, "VIOLATION"),
    ("official-switch-missing", {"masks": set()},
     {"name": "MAA_3", "status": "OK", "clientType": "Official",
      "tasks": [{"accountName": "134****5276"}]}, "VIOLATION"),
    ("bilibili-ok", {"servers": {"Bilibili"}},
     {"name": "MAA_9", "status": "OK", "clientType": "Bilibili",
      "tasks": [{"accountName": "", "switchEnabled": False}]}, "OK"),
    ("bilibili-account-not-empty", {"servers": {"Bilibili"}},
     {"name": "MAA_9", "status": "OK", "clientType": "Bilibili",
      "tasks": [{"accountName": "MikuKong", "switchEnabled": False}]}, "VIOLATION"),
    ("bilibili-switch-on", {"servers": {"Bilibili"}},
     {"name": "MAA_9", "status": "OK", "clientType": "Bilibili",
      "tasks": [{"accountName": "", "switchEnabled": True}]}, "VIOLATION"),
    ("yostarjp-ok", {"servers": {"YoStarJP"}},
     {"name": "MAA_7", "status": "OK", "clientType": "YoStarJP",
      "tasks": [{"accountName": "", "switchEnabled": False}]}, "OK"),
    ("txwy-int", {"servers": {"txwy"}},
     {"name": "MAA_10", "status": "OK", "clientType": 5,
      "tasks": [{"accountName": "", "switchEnabled": False}]}, "OK"),
    ("jp-enum-name", {"servers": {"YoStarJP"}},
     {"name": "MAA_11", "status": "OK", "clientType": "JP",
      "tasks": [{"accountName": "", "switchEnabled": False}]}, "OK"),
    ("txwy-enum-name", {"servers": {"txwy"}},
     {"name": "MAA_10", "status": "OK", "clientType": "Txwy",
      "tasks": [{"accountName": "", "switchEnabled": False}]}, "OK"),
    ("en-enum-name", {"servers": {"YoStarEN"}},
     {"name": "MAA_10", "status": "OK", "clientType": "EN",
      "tasks": [{"accountName": "", "switchEnabled": False}]}, "OK"),
    ("kr-enum-name", {"servers": {"YoStarKR"}},
     {"name": "MAA_10", "status": "OK", "clientType": "KR",
      "tasks": [{"accountName": "", "switchEnabled": False}]}, "OK"),
    ("int-as-string", {"servers": {"Official"}},
     {"name": "MAA_2", "status": "OK", "clientType": "0",
      "tasks": [{"accountName": "192****7973", "switchEnabled": True}]}, "OK"),
    ("jp-enum-switch-on", {"servers": {"YoStarJP"}},
     {"name": "MAA_11", "status": "OK", "clientType": "JP",
      "tasks": [{"accountName": "", "switchEnabled": True}]}, "VIOLATION"),
    ("unknown-server", {}, {"name": "MAA_4", "status": "OK", "clientType": "Nope",
                            "tasks": [{"accountName": "", "switchEnabled": False}]}, "UNKNOWN"),
    ("no-startup-task", {}, {"name": "MAA_4", "status": "OK", "clientType": "Official",
                             "tasks": []}, "UNKNOWN"),
    ("missing-file", {}, {"name": "MAA_4", "status": "MISSING"}, "UNKNOWN"),
]


def selftest() -> int:
    bad = 0
    for label, slot, fi, want in _SELFTEST_CASES:
        ev = evaluate(slot, fi)
        ok = ev["verdict"] == want
        bad += 0 if ok else 1
        print("%s %-28s verdict=%-9s want=%-9s %s" % ("PASS" if ok else "FAIL", label,
                                                       ev["verdict"], want,
                                                       "; ".join(ev["issues"] + ev["notes"])))
    print("selftest: %s (%d/%d)" % ("OK" if not bad else "FAIL", len(_SELFTEST_CASES) - bad, len(_SELFTEST_CASES)))
    return 0 if not bad else 1


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="切号注入闸门 v2（只读）")
    ap.add_argument("--check", action="store_true", help="紧凑模式（巡检用）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--selftest", action="store_true", help="本地规则自检（不连网）")
    ap.add_argument("--dirs", help="逗号分隔的 MAA 目录名/路径（默认取 REST 推导的槽位表）")
    ap.add_argument("--timeout", type=int, default=240)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    dirs = None
    if a.dirs:
        dirs = [d.strip() for d in a.dirs.split(",") if d.strip()]
        dirs = [d if re.match(r"^[A-Za-z]:[\\/]", d) else r"E:\MAA-Multi" + "\\" + d for d in dirs]
    res = run_gate(dirs=dirs, timeout=a.timeout)
    print_report(res, "json" if a.json else ("check" if a.check else "full"))
    if res["switch_gate"].startswith("VIOLATION"):
        return 1
    if res["switch_gate"].startswith("OK"):
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
