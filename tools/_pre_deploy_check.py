"""部署前检查：生产 main_web 进程、队列状态、远程 pip 环境。token 不出现在输出。"""
import json
import sys
import urllib.request
from pathlib import Path

cfg = json.load(open(Path.home() / ".maorch_deploy.json", encoding="utf-8"))
MANAGER = cfg["manager"].rstrip("/")
MTOKEN = cfg["manager_token"]
ATOKEN = cfg["api_token"]
PROJ = MANAGER.replace(":19998", ":19999")


def exec_ps(script, timeout=120):
    body = json.dumps({"command": script, "timeout": timeout}).encode()
    req = urllib.request.Request(MANAGER + "/api/exec", data=body,
                                 headers={"x-manager-token": MTOKEN, "Content-Type": "application/json"})
    d = json.loads(urllib.request.urlopen(req, timeout=timeout + 10).read().decode("utf-8", "replace"))
    return d.get("stdout", ""), d.get("stderr", "")


# 1) main_web 进程 + 端口
out, err = exec_ps("Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" | Where-Object { $_.CommandLine -match 'main_web' } | Select-Object ProcessId, @{N='Cmd';E={$_.CommandLine.Substring(0,[Math]::Min(80,$_.CommandLine.Length))}} | Format-List | Out-String")
print("== main_web 进程 =="); print(out.strip() or "(无)", err.strip()[:200] if err else "")

# 2) 队列/任务运行状态（只打印运行中数量级信息）
try:
    req = urllib.request.Request(PROJ + "/api/queue", headers={"x-agent-token": ATOKEN})
    q = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
    print(f"== 队列: pending={q.get('pending_count')} active={q.get('active_count')} paused={q.get('paused')} ==")
    for a in q.get("active", [])[:5]:
        print("  运行中:", a.get("account_name"))
except Exception as e:
    print("队列查询失败:", repr(e))

# 3) 生产 python fastmcp 状态
out, err = exec_ps("python -c \"import fastmcp; print('fastmcp', fastmcp.__version__)\"", 60)
print("== 生产 fastmcp =="); print((out or err).strip())
