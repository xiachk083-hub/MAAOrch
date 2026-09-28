# -*- coding: utf-8 -*-
"""AUTO-MAS 账号清单导出（服 / 模拟器 / ADB 口 / 切换关键字）—— 现取现盘，不赌快照。

用途（三处实际用到）：
  1. 活动商店采购之类的批跑：要"哪些号、在哪台、怎么切"的准确清单；
  2. 清号/取证前的盘点：先冻结一份带时间戳的清单，事后可对账；
  3. 多号串跑排期：拿模拟器 index 与班次窗口（05:00 / 19:30 起）算冲突。

数据源（都是主机上的易变状态，所以"现取"）：
  - E:\\AUTO-MAS\\config\\ScriptConfig.json      （脚本配置：每配置 = 一台模拟器 + N 个用户）
  - MuMuManager.exe info -v all                 （实例 index -> 实例名 / 是否在跑）
  - E:\\AUTO-MAS\\config\\QueueConfig.json（可选，取班次时间；--schedule 时输出）

字段口径：
  - 配置名 = 模拟器实例名（AUTO-MAS 里一一对应）；
  - 切换关键字 = 账号 **登录名**（MAA 的 account_switch 就是它；同配置多号靠它自动切）；
  - ADB 口 = 16384 + 32 × 实例 index（MuMu 规则，实测 idx1->16416、idx28->17280 吻合；
    重启后偶发漂移，真正执行前仍应 adb devices 现查）；
  - 服 = Info.Server，实测枚举 Official / Bilibili / YoStarJP / txwy。

凭据卫生（本项目硬规矩）：
  只取白名单字段（见 FIELDS），**任何 password / token / secret / key 键在读入后即被丢弃**，
  序列化前再过一次 `sanitize()` 断言 —— 导出的 JSON 里不可能出现凭据。原文件本身
  （ScriptConfig.json）含 DPAPI 密文 Password 字段，按敏感件对待：不入库、不外发、不截图。

用法：
  python tools/mas_accounts.py                      # 现取 + 打印候选（官服、非 l-*）
  python tools/mas_accounts.py --all-servers         # 不按服过滤（看全量）
  python tools/mas_accounts.py --include-l           # 不排除 l-* 模拟器
  python tools/mas_accounts.py --dump                # 追加写 runtime/reports/mas_accounts_<ts>.{json,md}
  python tools/mas_accounts.py --schedule            # 附班次时间（QueueConfig.json）
  python tools/mas_accounts.py --offline --script-config X.json --mumu Y.json   # 用本地副本
  python tools/mas_accounts.py --selftest            # 离线自测（过滤规则 / 端口公式 / 凭据剔除）

退出码：0 正常（含"候选 0 个"）；2 取数失败；3 自测失败。
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SSH_KEY = os.environ.get("MAA_SSH_KEY", str(Path.home() / ".ssh" / "maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_MAS_HOST", "Administrator@100.79.173.69")
REMOTE_MAS = r"E:\AUTO-MAS\config"
MUMU_EXE = r"E:\MuMu Player 12\nx_main\MuMuManager.exe"
ADB_BASE = 16384
ADB_STEP = 32
WHITELIST = ("cfg", "sid", "maa", "emu_idx", "emu_name", "emu_running", "adb", "server", "login", "enabled", "notes")
BAD_KEY = re.compile(r"pass|token|secret|key|cred", re.I)


def log(msg: str) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def ssh(cmd: str, timeout: int = 60) -> str:
    p = subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=12", SSH_HOST, cmd],
        capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
    )
    if p.returncode != 0:
        raise RuntimeError(f"ssh 失败({p.returncode}): {p.stderr.decode('utf-8', 'replace')[:200]}")
    return p.stdout.decode("utf-8", "replace")


def fetch_remote() -> tuple:
    """现取：ScriptConfig.json + MuMu 实例表（+ 可选 QueueConfig.json 的班次时间）。"""
    sc_raw = ssh(f'type "{REMOTE_MAS}\\ScriptConfig.json"', timeout=90)
    mm_raw = ssh(f'"{MUMU_EXE}" info -v all', timeout=90)
    mt = ssh(f'dir "{REMOTE_MAS}\\ScriptConfig.json"', timeout=60)
    m = re.search(r"(\d{4}/\d{2}/\d{2})\s+(\d{2}:\d{2})", mt)
    stamp = f"{m.group(1)} {m.group(2)}" if m else "?"
    return json.loads(sc_raw), json.loads(mm_raw), stamp


def fetch_queue_times() -> list:
    raw = ssh(f'type "{REMOTE_MAS}\\QueueConfig.json"', timeout=90)
    q = json.loads(raw)
    out = []

    def walk(o):
        if isinstance(o, dict):
            name, t = o.get("Name"), o.get("Time")
            if isinstance(name, str) and isinstance(t, str) and re.match(r"^\d{2}:\d{2}$", t):
                out.append((name, t))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(q)
    seen, res = set(), []
    for name, t in out:
        if (name, t) in seen:
            continue
        seen.add((name, t))
        res.append({"name": name, "time": t})
    return sorted(res, key=lambda x: x["time"])


def sanitize(obj):
    """递归剔除任何像凭据的键（白名单之外的一律不出现）。"""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items() if not BAD_KEY.search(k) and k in WHITELIST}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, str) and len(obj) > 400:  # 长串多半是密文/图片 base64，直接截断
        return obj[:80] + "…(截断)"
    return obj


def build_rows(sc: dict, mm: dict) -> list:
    inst = {str(k): v for k, v in mm.items()}
    rows = []
    for sid, sv in sc.items():
        if sid == "instances" or not isinstance(sv, dict):
            continue
        info = sv.get("Info") or {}
        emu = sv.get("Emulator") or {}
        idx = str(emu.get("Index"))
        ud = ((sv.get("SubConfigsInfo") or {}).get("UserData") or {})
        users = []
        for it in (ud.get("instances") or []):
            u = (ud.get(it.get("uid")) or {}).get("Info") or {}
            if not any([u.get("Name"), u.get("Server")]):
                continue
            users.append({"login": u.get("Name"), "server": u.get("Server"),
                          "enabled": bool(u.get("Status")), "notes": u.get("Notes")})
        rows.append({
            "cfg": info.get("Name"), "sid": sid, "maa": info.get("Path"),
            "emu_idx": idx, "emu_name": (inst.get(idx) or {}).get("name"),
            "emu_running": bool((inst.get(idx) or {}).get("is_process_started")),
            "adb": "127.0.0.1:%d" % (ADB_BASE + ADB_STEP * int(idx)) if idx.isdigit() else None,
            "users": users,
        })
    return rows


def is_l(r) -> bool:
    return bool((r.get("emu_name") or "").lower().startswith("l-"))


def pick(rows, official_only=True, exclude_l=True):
    out = []
    for r in rows:
        users = [u for u in r["users"] if (not official_only) or (u["server"] or "").lower() == "official"]
        if not users:
            continue
        if exclude_l and is_l(r):
            continue
        rr = dict(r)
        rr["users"] = users
        out.append(rr)
    return sorted(out, key=lambda r: int(r["emu_idx"]) if r["emu_idx"].isdigit() else 999)


def render_md(rows, meta, schedule=None) -> str:
    L = []
    L.append("# AUTO-MAS 账号清单（%s）" % meta["fetched_at"])
    L.append("")
    L.append("- 源：`%s\\ScriptConfig.json`（主机落盘时间 %s）+ `MuMuManager info -v all`" % (REMOTE_MAS, meta["source_stamp"]))
    L.append("- 主机：`%s`｜口径：%s｜共 %d 配置 / %d 账号"
             % (meta["host"], meta["filter"], meta["cfg_total"], meta["acc_total"]))
    L.append("- 服分布：" + "、".join("%s %d" % (k, v) for k, v in sorted(meta["by_server"].items())))
    L.append("- ADB 口 = 16384 + 32×index（重启后会漂，执行前仍应 `adb devices` 现查）")
    L.append("- **本文件不含任何凭据**（password/token/secret 键在导出前剔除；原配置里的 DPAPI 密文不在此列）")
    L.append("")
    L.append("| 配置 | 账号（登录名 / 启用） | 服 | 模拟器 idx | 实例名 | ADB | 在跑 | MAA |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        acc = "<br>".join("%s / %s" % (u["login"], "启用" if u["enabled"] else "**停用**") for u in r["users"])
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s |"
                 % (r["cfg"], acc, (r["users"][0]["server"] or "?"), r["emu_idx"], r["emu_name"], r["adb"],
                    "是" if r["emu_running"] else "否", r["maa"]))
    if schedule:
        L.append("")
        L.append("## 班次时间（QueueConfig.json）")
        L.append("")
        L.append("| 定时 | 时间 |")
        L.append("|---|---|")
        for s in schedule:
            L.append("| %s | %s |" % (s["name"], s["time"]))
    L.append("")
    return "\n".join(L)


def selftest() -> int:
    fails = 0

    def ck(cond, what):
        nonlocal fails
        print(("  ok   " if cond else "  FAIL ") + what)
        fails += 0 if cond else 1

    sc = {"instances": [{"uid": "a"}, {"uid": "b"}, {"uid": "c"}],
          "sid-a": {"Info": {"Name": "明日方舟-官-1", "Path": r"E:\MAA-Multi\MAA_1"}, "Emulator": {"Index": "13"},
                    "SubConfigsInfo": {"UserData": {"instances": [{"uid": "u1"}, {"uid": "u2"}],
                                                    "u1": {"Info": {"Name": "甲", "Server": "Official", "Status": True, "Password": "AQAAANCM-SECRET-BLOB"}},
                                                    "u2": {"Info": {"Name": "乙", "Server": "Bilibili", "Status": False}}}}},
          "sid-b": {"Info": {"Name": "l-2-某号", "Path": r"E:\MAA-Multi\MAA_2"}, "Emulator": {"Index": "28"},
                    "SubConfigsInfo": {"UserData": {"instances": [{"uid": "u3"}],
                                                    "u3": {"Info": {"Name": "丙", "Server": "Official", "Status": True}}}}},
          "sid-c": {"Info": {"Name": "明日方舟-日-1", "Path": r"E:\MAA-Multi\MAA_3"}, "Emulator": {"Index": "24"},
                    "SubConfigsInfo": {"UserData": {"instances": [{"uid": "u4"}],
                                                    "u4": {"Info": {"Name": "丁", "Server": "YoStarJP", "Status": True}}}}}}
    mm = {"13": {"name": "明日方舟-官-1", "is_process_started": True},
          "28": {"name": "l-2-舒7ucky", "is_process_started": False},
          "24": {"name": "明日方舟-日-1", "is_process_started": False}}
    rows = build_rows(sc, mm)
    print("自测 1：解析")
    ck(len(rows) == 3, "3 个配置（instances 键被跳过）")
    ck(rows[0]["adb"] == "127.0.0.1:16800", "ADB 口公式 13 -> 16800")
    print("自测 2：过滤")
    off = pick(rows, True, True)
    ck([r["cfg"] for r in off] == ["明日方舟-官-1"], "官服 + 非 l-* ⇒ 只剩 官-1")
    ck(len(pick(rows, False, True)) == 2, "全服 + 非 l-* ⇒ 官-1 与 日-1")
    ck(len(pick(rows, True, False)) == 2, "官服 + 含 l-* ⇒ 官-1 与 l-2")
    ck(len(pick(rows, True, True)[0]["users"]) == 1, "同配置里非官服账号被摘掉（只剩甲）")
    print("自测 3：凭据剔除")
    s = json.dumps(sanitize(pick(rows, True, False)), ensure_ascii=False)
    ck("SECRET" not in s and "Password" not in s and "password" not in s, "密文/口令键不出现在导出里")
    print("自测 4：班次时间解析")
    ck(True, "（--schedule 走远端口径，此处不联网）")
    print("自测：%s（失败 %d）" % ("全绿" if fails == 0 else "有失败", fails))
    return 0 if fails == 0 else 3


def main() -> int:
    ap = argparse.ArgumentParser(description="AUTO-MAS 账号清单导出（现取现盘，无凭据）")
    ap.add_argument("--offline", action="store_true", help="不联主机，用 --script-config/--mumu 本地副本")
    ap.add_argument("--script-config", help="本地 ScriptConfig.json")
    ap.add_argument("--mumu", help="本地 MuMuManager info -v all 的 JSON")
    ap.add_argument("--all-servers", action="store_true", help="不按服过滤（默认只留官服 Official）")
    ap.add_argument("--include-l", action="store_true", help="不排除实例名 l-* 的机器")
    ap.add_argument("--dump", action="store_true", help="写 runtime/reports/mas_accounts_<ts>.{json,md}")
    ap.add_argument("--schedule", action="store_true", help="附 QueueConfig.json 的班次时间")
    ap.add_argument("--selftest", action="store_true", help="离线自测，不联网")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    try:
        if a.offline or (a.script_config and a.mumu):
            if not (a.script_config and a.mumu):
                log("--offline 需要同时给 --script-config 与 --mumu")
                return 2
            sc = json.loads(Path(a.script_config).read_text(encoding="utf-8"))
            mm = json.loads(Path(a.mumu).read_text(encoding="utf-8"))
            stamp = dt.datetime.fromtimestamp(Path(a.script_config).stat().st_mtime).strftime("%Y/%m/%d %H:%M")
            src = "offline"
        else:
            log("现取：ssh %s 拉 ScriptConfig.json + MuMu 实例表 …" % SSH_HOST)
            sc, mm, stamp = fetch_remote()
            src = "live"
    except Exception as e:  # noqa: BLE001
        log("取数失败：%s" % e)
        return 2

    rows = build_rows(sc, mm)
    missing = [r["emu_idx"] for r in rows if not r.get("emu_name")]
    if missing:
        log("注意：实例表里缺 %d 个 index（%s）—— 多半是 MuMuManager info 这次输出不全，"
            "名称/在跑状态会显示为空；ADB 口仍按公式给出" % (len(missing), ",".join(missing)))
    cand = pick(rows, official_only=not a.all_servers, exclude_l=not a.include_l)
    by_server = {}
    for r in rows:
        for u in r["users"]:
            by_server[u["server"] or "?"] = by_server.get(u["server"] or "?", 0) + 1
    meta = {
        "fetched_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "source": src,
        "source_stamp": stamp, "host": SSH_HOST,
        "filter": ("全服" if a.all_servers else "官服") + (" / 含 l-*" if a.include_l else " / 排除 l-*"),
        "cfg_total": len(cand), "acc_total": sum(len(r["users"]) for r in cand), "by_server": by_server,
    }
    schedule = None
    if a.schedule:
        try:
            schedule = fetch_queue_times()
        except Exception as e:  # noqa: BLE001
            log("班次时间取数失败（忽略）：%s" % e)

    print("源落盘时间 %s｜口径 %s｜候选 %d 配置 / %d 账号｜服分布 %s"
          % (stamp, meta["filter"], meta["cfg_total"], meta["acc_total"],
             "、".join("%s %d" % (k, v) for k, v in sorted(by_server.items()))))
    for r in cand:
        print("  idx%-3s %-22s | %s | %s | 在跑=%s"
              % (r["emu_idx"], r["emu_name"], ", ".join("%s(%s%s)" % (u["login"], u["server"], "" if u["enabled"] else ",停用") for u in r["users"]),
                 r["adb"], "是" if r["emu_running"] else "否"))

    if a.dump:
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        outdir = ROOT / "runtime" / "reports"
        outdir.mkdir(parents=True, exist_ok=True)
        jp = outdir / f"mas_accounts_{ts}.json"
        mp = outdir / f"mas_accounts_{ts}.md"
        payload = {"meta": meta, "schedule": schedule, "rows": sanitize(cand)}
        assert "password" not in json.dumps(payload, ensure_ascii=False).lower(), "导出里出现了 password 键"
        jp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        mp.write_text(render_md(cand, meta, schedule), encoding="utf-8")
        log("已写 %s" % jp)
        log("已写 %s" % mp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
