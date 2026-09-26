"""连接/对应关系勘察 — 模拟器 ↔ MAA ↔ ADB 端口一致性 + 任务 + 注入配置。

核心勘察点（用户 2026-08-17 指定）:
  1. 模拟器数量 / MAA 数量 / 对应关系
  2. 模拟器真实 ADB 端口  vs  MAA 配置里填的 ADB 端口  → 是否一致
  3. MAA 任务（配置的任务链）
  4. 配置注入（注入到 MAA 的 TaskQueue 是否正确）

用法:
    python tools/link_audit.py              # 全部官服账号对应关系表
    python tools/link_audit.py 官-12        # 单个账号深挖
"""
from __future__ import annotations
import base64
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
CRED = pathlib.Path.home() / ".maorch_deploy.json"


def _api(path: str, timeout: int = 20) -> dict:
    tok = json.loads(CRED.read_text(encoding="utf-8")).get("api_token", "")
    req = urllib.request.Request("http://100.79.173.69:19999" + path,
                                 headers={"x-agent-token": tok})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _exec(cmd: str, timeout: int = 90) -> dict:
    cred = json.loads(CRED.read_text(encoding="utf-8"))
    req = urllib.request.Request("http://100.79.173.69:19998/api/exec",
        data=json.dumps({"command": cmd}).encode(),
        headers={"x-manager-token": cred.get("manager_token", ""),
                 "Content-Type": "application/json"}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception as e:
        return {"error": str(e)[:100]}


def _run_remote(script: str) -> str:
    """目标机跑 python（base64 内嵌），返回 stdout。"""
    b64 = base64.b64encode(script.encode()).decode()
    r = _exec(f'python -c "import base64;exec(base64.b64decode(\'{b64}\').decode())"')
    return r.get("stdout") or ""


def _emu_table() -> dict:
    """枚举所有模拟器：真实 ADB 端口 + 开没开 + 名字。"""
    script = r'''
import subprocess, json
cli = "E:/MuMu Player 12/nx_main/MuMuManager.exe"
out = {}
for i in range(0, 61):
    try:
        r = subprocess.run([cli, "info", "-v", str(i)], capture_output=True, text=True, timeout=5,
                           encoding="utf-8", errors="replace")
        d = json.loads(r.stdout.lstrip(chr(0xfeff)).strip())
        nm = d.get("name", "")
        if nm:
            out[nm.replace("明日方舟-", "")] = {
                "idx": i, "on": bool(d.get("is_process_started")),
                "adb_real": d.get("adb_port"),
                "started": d.get("is_android_started")}
    except Exception:
        pass
print(json.dumps(out, ensure_ascii=False))
'''
    try:
        return json.loads(_run_remote(script))
    except Exception:
        return {}


def _maa_table() -> dict:
    """枚举所有 MAA 实例：配置里的 ADB 端口 + 归属账号 + 任务链 + MAA 是否活。"""
    script = r'''
import os, json, re, subprocess
base = "E:/MAAOrch/services/maa/instances"
out = {}
for inst in os.listdir(base):
    if not inst.isdigit(): continue
    gnew = os.path.join(base, inst, "config", "gui.new.json")
    mp = os.path.join(base, inst, ".meta")
    if not os.path.exists(mp): continue
    meta = open(mp, encoding="utf-8", errors="replace").read()
    nm = meta.split("|")[-1].strip()
    pidp = os.path.join(base, inst, ".pid")
    pid = open(pidp).read().strip() if os.path.exists(pidp) else ""
    maa_alive = False
    if pid:
        r = subprocess.run(["tasklist", "/FI", "PID eq %s" % pid, "/FO", "CSV"],
                           capture_output=True, text=True, timeout=15, errors="replace")
        maa_alive = "MAA.exe" in r.stdout
    # 配置里填的 ADB 端口
    addr_cfg = ""
    if os.path.exists(gnew):
        try:
            d = json.load(open(gnew, encoding="utf-8"))
            cfg = d.get("Configurations", {}).get("Default", {})
            addr_cfg = cfg.get("Connect.Address") or cfg.get("Connect", {}).get("Address", "")
        except Exception:
            pass
    # 任务链（enabled 的 TaskType）
    tasks = []
    if os.path.exists(gnew):
        try:
            d = json.load(open(gnew, encoding="utf-8"))
            tq = d["Configurations"]["Default"].get("TaskQueue")
            if isinstance(tq, str): tq = json.loads(tq)
            for it in (tq or []):
                if isinstance(it, str):
                    try: it = json.loads(it)
                    except: continue
                if it.get("IsEnable"):
                    tasks.append(it.get("TaskType", "?"))
        except Exception:
            pass
    out[nm] = {"inst": inst, "addr_cfg": addr_cfg, "maa_alive": maa_alive, "tasks": tasks}
print(json.dumps(out, ensure_ascii=False))
'''
    try:
        return json.loads(_run_remote(script))
    except Exception:
        return {}


def main() -> None:
    accts = _api("/api/accounts").get("accounts", [])
    qd = _api("/api/queue")
    active = {a["account_name"].replace("明日方舟-", "") for a in qd.get("active", [])}
    pending = {p["account_name"].replace("明日方舟-", "") for p in qd.get("pending", [])}
    emu = _emu_table()
    maa = _maa_table()

    names = []
    for a in accts:
        if a["name"].startswith("官-") and "l-" not in a["name"]:
            names.append(a["name"].replace("明日方舟-", ""))
    names.sort()

    print("模拟器数量:", len(emu), "| MAA 实例数量:", len(maa))
    print(f"{'账号':<9} {'模拟器':<4} {'模拟器真实端口':<10} {'MAA配置端口':<10} {'一致':<3} {'模拟器开':<5} {'MAA活':<4} {'队列':<3} 任务")
    mismatches = []
    for nm in names:
        ac = next((x for x in accts if x["name"].replace("明日方舟-", "") == nm), {})
        ei = str(ac.get("emu_instance_index", ""))
        e = emu.get(nm) or {}
        m = maa.get(nm) or {}
        real_port = e.get("adb_real")
        cfg_port = m.get("addr_cfg", "")
        # 从 cfg 端口串里提取端口号（127.0.0.1:17248）
        cfg_num = ""
        if cfg_port:
            import re
            mm = re.search(r":(\d+)", str(cfg_port))
            cfg_num = mm.group(1) if mm else str(cfg_port)
        match = (real_port and str(real_port) == str(cfg_num))
        if real_port and cfg_num and not match:
            mismatches.append(nm)
        que = "A" if nm in active else ("P" if nm in pending else "-")
        emu_on = "开" if e.get("on") else ("关" if e else "-")
        maa_alive = "Y" if m.get("maa_alive") else "-"
        tasks = ",".join(m.get("tasks", []))[:36]
        match_s = "✓" if match else ("!" if (real_port and cfg_num) else "?")
        print(f"{nm:<9} {ei:<4} {str(real_port):<10} {cfg_num:<10} {match_s:<3} {emu_on:<5} {maa_alive:<4} {que:<3} {tasks}")
    if mismatches:
        print("\n⚠️ ADB 端口不一致:", mismatches)


if __name__ == "__main__":
    main()
