"""把 mcp-maaorch 注册进 DSH web profile 的 cordis.patch.yml（幂等）。token 直接落盘，不打印。"""
import json
import urllib.request
from pathlib import Path

PATCH = Path.home() / ".dsh" / "profiles" / "web" / "cordis.patch.yml"

# 1) 读生产 api_token
cfg = json.load(open(Path.home() / ".maorch_deploy.json", encoding="utf-8"))
MANAGER = cfg["manager"].rstrip("/")
MTOKEN = cfg["manager_token"]
body = json.dumps({"command": "python -c \"import json; print(json.load(open(r'E:\\\\MAAOrch\\\\models\\\\config.json', encoding='utf-8')).get('api_token',''))\"", "timeout": 60}).encode()
req = urllib.request.Request(MANAGER + "/api/exec", data=body,
                             headers={"x-manager-token": MTOKEN, "Content-Type": "application/json"})
d = json.loads(urllib.request.urlopen(req, timeout=70).read().decode("utf-8", "replace"))
token = d.get("stdout", "").strip()
if len(token) != 32:
    print("token 异常:", repr(token[:20]))
    raise SystemExit(1)

content = PATCH.read_text(encoding="utf-8")
if "mcp-maaorch" in content:
    print("patch.yml 已包含 mcp-maaorch，跳过")
else:
    block = f"""
# MAAOrch MCP 工具（2026-08-14）：DSH Agent 直接查询/调度 MAAOrch（mcp__maaorch__*）
- id: mcp-maaorch
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: maaorch
    transport: streamable-http
    url: http://100.79.173.69:19999/mcp/
    headers:
      Authorization: 'Bearer {token}'
"""
    PATCH.write_text(content.rstrip() + "\n" + block, encoding="utf-8")
    print("已写入 mcp-maaorch 注册块")
print(PATCH)
