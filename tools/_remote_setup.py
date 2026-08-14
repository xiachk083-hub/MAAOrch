"""远程：读生产 api_token（打码）+ 安装 fastmcp。"""
import json
import urllib.request
from pathlib import Path

cfg = json.load(open(Path.home() / ".maorch_deploy.json", encoding="utf-8"))
MANAGER = cfg["manager"].rstrip("/")
MTOKEN = cfg["manager_token"]


def exec_ps(script, timeout=180):
    body = json.dumps({"command": script, "timeout": timeout}).encode()
    req = urllib.request.Request(MANAGER + "/api/exec", data=body,
                                 headers={"x-manager-token": MTOKEN, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout + 10).read().decode("utf-8", "replace"))


# 1) 生产 api_token 状态（打码）
script1 = (
    "python -c \"import json; c=json.load(open(r'E:\\MAAOrch\\models\\config.json', encoding='utf-8')); "
    "t=c.get('api_token',''); print('len', len(t), 'head', t[:4], 'tail', t[-4:] if len(t)>8 else '')\""
)
d = exec_ps(script1, 60)
print("api_token:", d.get("stdout", "").strip() or d.get("stderr", "").strip())

# 2) 远程装 fastmcp
d = exec_ps("pip install fastmcp 2>&1 | Select-Object -Last 3", 280)
print("pip:", (d.get("stdout", "") or d.get("stderr", "")).strip()[-300:])

# 3) 验证版本
d = exec_ps("python -c \"import fastmcp; print(fastmcp.__version__)\"", 60)
print("fastmcp 远程版本:", (d.get("stdout", "") or d.get("stderr", "")).strip())
