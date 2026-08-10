#!/usr/bin/env python3
"""MAAOrch 一键部署工具 — 本地代码 → 目标机同步 + 重启 + 验证。

用法:
    python tools/deploy.py                  # 部署默认清单并重启
    python tools/deploy.py --no-restart     # 只传文件不重启
    python tools/deploy.py --file services/runner.py   # 只部署指定文件

凭据来源（优先级从高到低）:
    命令行参数 > 环境变量 (MAORCH_MANAGER_URL / MAORCH_MANAGER_TOKEN / MAORCH_API_TOKEN)
    > 本地文件 ~/.maorch_deploy.json（仓库外，不提交 git）

原理:
    文件 base64 分块 → manager /api/exec 写临时文件 → 目标机 python 解码
    写目标路径 → 校验大小 → （可选）manager stop/start 重启项目。
"""
from __future__ import annotations
import argparse
import base64
import json
import os
import sys
import urllib.request

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 运行时凭据（main 中 _load_config 填充，不硬编码）
G_MANAGER = ""
G_MANAGER_TOKEN = ""
G_PROJECT_URL = ""
G_API_TOKEN = ""

DEFAULT_FILES = [
    "models/stats.py",
    "models/queue_entry.py",
    "services/runner.py",
    "services/launch_queue.py",
    "services/stats_aggregator.py",
    "services/instance_pool.py",
    "services/config_injector.py",
    "services/stage_probe.py",
    "services/backfill_stats.py",
    "network/api_fastapi.py",
    "network/api_server.py",
    "ui/web/app.js",
    "ui/web/index.html",
    "manager/manager.py",
]

CHUNK = 18000


def _load_config(args) -> None:
    """凭据: 命令行 > 环境变量 > ~/.maorch_deploy.json（仓库外）。缺失则报错退出。"""
    global G_MANAGER, G_MANAGER_TOKEN, G_PROJECT_URL, G_API_TOKEN
    cfg = {"manager": "", "manager_token": "", "api_token": ""}
    local = os.path.join(os.path.expanduser("~"), ".maorch_deploy.json")
    if os.path.exists(local):
        try:
            cfg.update(json.load(open(local, encoding="utf-8")))
        except Exception:
            print("警告: ~/.maorch_deploy.json 解析失败，忽略")
    cfg["manager"] = os.environ.get("MAORCH_MANAGER_URL", cfg["manager"])
    cfg["manager_token"] = os.environ.get("MAORCH_MANAGER_TOKEN", cfg["manager_token"])
    cfg["api_token"] = os.environ.get("MAORCH_API_TOKEN", cfg["api_token"])
    if args.manager:
        cfg["manager"] = args.manager
    if args.manager_token:
        cfg["manager_token"] = args.manager_token
    if args.api_token:
        cfg["api_token"] = args.api_token
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        print("缺少部署凭据: " + ", ".join(missing))
        print("设置方式: 环境变量 MAORCH_MANAGER_URL / MAORCH_MANAGER_TOKEN / MAORCH_API_TOKEN")
        print("          或本地文件 ~/.maorch_deploy.json -> {\"manager\": \"http://host:19998\","
              " \"manager_token\": \"...\", \"api_token\": \"...\"}")
        sys.exit(1)
    G_MANAGER = cfg["manager"].rstrip("/")
    G_MANAGER_TOKEN = cfg["manager_token"]
    G_API_TOKEN = cfg["api_token"]
    # URL 校验：仅接受 http/https 且 host 非空（地址来自用户显式配置，非外部输入）
    host = G_MANAGER.split("/")[2] if G_MANAGER.startswith(("http://", "https://")) else ""
    if not host:
        print("错误: manager 地址须为 http(s)://host:port 格式: " + G_MANAGER)
        sys.exit(1)
    # 项目 Web API 端口固定为 manager 端口 -1（19998 → 19999）
    G_PROJECT_URL = G_MANAGER.replace(":19998", ":19999") + ("" if G_MANAGER.endswith(":19999") else "")
    if G_PROJECT_URL == G_MANAGER and ":19999" not in G_MANAGER:
        G_PROJECT_URL = G_MANAGER + "/"


def exec_ps(script: str, timeout: int = 120) -> tuple[str, str]:
    body = json.dumps({"command": script, "timeout": timeout}).encode("utf-8")
    req = urllib.request.Request(G_MANAGER + "/api/exec", data=body,
        headers={"x-manager-token": G_MANAGER_TOKEN, "Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=timeout + 10)
    d = json.loads(resp.read().decode("utf-8", "replace"))
    return d.get("stdout", ""), d.get("stderr", "")


def deploy_file(local: str, remote: str) -> bool:
    data = open(local, "rb").read()
    b64 = base64.b64encode(data).decode("ascii")
    tmp = r"E:\MAAOrch\probe_shots\_dep.b64"
    exec_ps("[IO.File]::WriteAllText('" + tmp + "', '')")
    parts = [b64[i:i + CHUNK] for i in range(0, len(b64), CHUNK)]
    for part in parts:
        out, err = exec_ps("Add-Content -Path '" + tmp + "' -Value '" + part + "' -NoNewline", 120)
        if err.strip():
            print("  chunk err:", err[:120])
            return False
    out, err = exec_ps("python 'E:\\MAAOrch\\probe_shots\\_dep2.py' '" + remote + "'", 60)
    if "WROTE" not in out:
        print("  WRITE FAIL:", out[:200], err[:200])
        return False
    out, err = exec_ps("(Get-Item '" + remote + "').Length", 30)
    got = out.strip()
    ok = str(len(data)) in got
    print(("OK  " if ok else "SIZE? ") + remote + " " + got + "/" + str(len(data)))
    return ok


def ensure_decoder() -> None:
    src = ("import sys, base64\n"
           "raw = open(r'E:\\MAAOrch\\probe_shots\\_dep.b64', encoding='ascii').read()\n"
           "data = base64.b64decode(raw)\n"
           "open(sys.argv[1], 'wb').write(data)\n"
           "print('WROTE', len(data))\n")
    b64 = base64.b64encode(src.encode("utf-8")).decode("ascii")
    exec_ps("[IO.File]::WriteAllText('E:\\MAAOrch\\probe_shots\\_dep2.py', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + b64 + "')))", 30)


def restart() -> None:
    def post(path: str, timeout: int = 180) -> str:
        req = urllib.request.Request(MANAGER + path, data=b"{}",
            headers={"x-manager-token": MANAGER_TOKEN, "Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read().decode("utf-8", "replace")
    print("stop:", post("/api/stop", 120))
    import time
    time.sleep(5)
    print("start:", post("/api/start", 180))
    time.sleep(15)
    # 项目地址在 _load_config 中校验（http(s) + host 非空），来自用户显式配置
    try:
        req = urllib.request.Request(G_PROJECT_URL + "/api/accounts",
            headers={"x-agent-token": G_API_TOKEN})
        urllib.request.urlopen(req, timeout=30)
        print("project API: OK")
    except Exception as e:
        print("project API check:", repr(e))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", action="append", default=[], help="只部署指定文件（相对项目根）")
    ap.add_argument("--no-restart", action="store_true", help="不重启项目")
    ap.add_argument("--manager", default="", help="manager 地址（如 http://host:19998）")
    ap.add_argument("--manager-token", default="", help="manager token")
    ap.add_argument("--api-token", default="", help="项目 API token")
    args = ap.parse_args()
    _load_config(args)

    files = args.file or DEFAULT_FILES
    ensure_decoder()
    allok = True
    for rel in files:
        local = os.path.join(PROJ, rel.replace("/", os.sep))
        remote = r"E:\MAAOrch" + "\\" + rel.replace("/", "\\")
        if not os.path.exists(local):
            print("SKIP 本地不存在:", rel)
            continue
        if not deploy_file(local, remote):
            allok = False
    print("ALL DEPLOYED" if allok else "SOME FAILED")
    if allok and not args.no_restart:
        print("重启项目...")
        restart()


if __name__ == "__main__":
    main()
