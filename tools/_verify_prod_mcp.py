"""生产 MCP 验证：读生产 api_token → mcp client 连生产 /mcp/ → list_tools + call_tool。token 不打印。"""
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

cfg = json.load(open(Path.home() / ".maorch_deploy.json", encoding="utf-8"))
MANAGER = cfg["manager"].rstrip("/")
MTOKEN = cfg["manager_token"]


def exec_ps(script, timeout=60):
    body = json.dumps({"command": script, "timeout": timeout}).encode()
    req = urllib.request.Request(MANAGER + "/api/exec", data=body,
                                 headers={"x-manager-token": MTOKEN, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout + 10).read().decode("utf-8", "replace"))


# 1) 读生产当前 api_token（仅脚本内部使用）
d = exec_ps(
    "python -c \"import json; print(json.load(open(r'E:\\\\MAAOrch\\\\models\\\\config.json', encoding='utf-8')).get('api_token',''))\"",
    60,
)
token = d.get("stdout", "").strip()
if not token or "Traceback" in token:
    print("读 token 失败:", d.get("stderr", "") or token)
    sys.exit(1)
print(f"token 读取 OK (len={len(token)})")


async def main():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = "http://100.79.173.69:19999/mcp/"
    try:
        async with streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (r, w, _sid):
            async with ClientSession(r, w) as sess:
                await sess.initialize()
                tools = await sess.list_tools()
                names = sorted(t.name for t in tools.tools)
                print(f"生产工具枚举 ({len(names)}): {names}")
                for t in tools.tools:
                    print(f"  - {t.name}: {t.description[:60]}")
                res = await sess.call_tool("status", {})
                print("生产 status 调用:", res.content[0].text[:300])
                res = await sess.call_tool("queue", {})
                print("生产 queue 调用:", res.content[0].text[:300])
    except Exception as e:
        print("生产 MCP 连接失败:", repr(e)[:500])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
