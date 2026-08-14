"""本地 MCP 全链路测试（临时）：假 MAAOrch API + 真 mcp_server，mcp 官方客户端验证。

用法: python tools/_test_mcp.py
"""
import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request
import uvicorn

from network.mcp_server import build_mcp_app


class FakeMW:
    config = {"api_port": 19997, "api_token": "test-token"}

    def _log(self, *a):
        print("[fake mw]", *a)


def make_fake_app():
    app = FastAPI()
    received = {}

    @app.get("/api/status")
    async def status():
        return {"ok": True, "emulators": [{"name": "官-14", "running": True}], "queue_len": 2}

    @app.get("/api/queue")
    async def queue():
        return {"pending": [{"account_name": "A", "position": 1}], "active": [], "pending_count": 1, "active_count": 0, "paused": False}

    @app.post("/api/queue/enqueue")
    async def enqueue(body: dict, request: Request):
        received["enqueue"] = body
        return {"ok": True, "account_id": f"acct-{body.get('account_index')}"}

    @app.post("/api/action/smart_selected")
    async def smart(body: dict, request: Request):
        received["smart"] = body
        return {"ok": True, "count": len(body.get("account_ids", []))}

    mcp_app = build_mcp_app(FakeMW())
    app.mount("/mcp", mcp_app)
    if getattr(mcp_app, "lifespan", None):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _combined(_a):
            async with mcp_app.lifespan(_a):
                yield

        app.router.lifespan_context = _combined
    return app, received


def run_server(app):
    cfg = uvicorn.Config(app, host="127.0.0.1", port=19997, log_level="warning")
    uvicorn.Server(cfg).run()


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.streamable_http import streamablehttp_client

    app, received = make_fake_app()
    t = threading.Thread(target=run_server, args=(app,), daemon=True)
    t.start()
    time.sleep(2)

    # 1) 无 token → 应拒绝
    try:
        async with streamablehttp_client("http://127.0.0.1:19997/mcp/") as (r, w, _sid):
            async with ClientSession(r, w) as sess:
                await sess.initialize()
                tools = await sess.list_tools()
        print("✗ 无 token 竟然成功，鉴权失效！")
        return 1
    except Exception as e:
        print(f"✓ 无 token 被拒: {type(e).__name__}: {str(e)[:100]}")

    # 2) 带 token → 工具枚举
    async with streamablehttp_client(
        "http://127.0.0.1:19997/mcp/",
        headers={"Authorization": "Bearer test-token"},
    ) as (r, w, _sid2):
        async with ClientSession(r, w) as sess:
            await sess.initialize()
            tools = await sess.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"✓ 工具枚举 ({len(names)}): {names}")
            assert len(names) == 15, f"工具数不对: {len(names)}"

            # 3) 只读调用
            res = await sess.call_tool("status", {})
            print("✓ status 调用:", res.content[0].text[:120])
            assert "官-14" in res.content[0].text

            # 4) 写调用
            res = await sess.call_tool("enqueue", {"account_index": 3, "priority": 5})
            print("✓ enqueue 调用:", res.content[0].text)
            assert received.get("enqueue") == {"account_index": 3, "priority": 5}, received
            print("✓ enqueue 请求体透传正确:", received["enqueue"])

            res = await sess.call_tool("smart_dispatch", {"account_ids": ["a1", "a2"], "only_anni": True})
            print("✓ smart_dispatch 调用:", res.content[0].text)
            assert received.get("smart", {}).get("only_anni") is True

            # 5) 参数校验（非法 action）
            res = await sess.call_tool("queue_control", {"action": "explode"})
            print("✓ queue_control 非法参数:", res.content[0].text)
            assert "error" in res.content[0].text

    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

