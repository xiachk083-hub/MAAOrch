"""FastAPI 入口：uvicorn 起服务 + 挂载各 API + 静态 UI + SSE。

用法:
    python server/main.py             # 本机模式（127.0.0.1:18889）
    python server/main.py 0.0.0.0     # 远程模式（目标机部署用）
"""
from __future__ import annotations
import sys
import threading
import time
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parent.parent
if str(_TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOL_ROOT))

try:
    from .config import setup_sys_path, get_config, save_config, WEB_DIR
except ImportError:  # python server/main.py 直接运行（无包上下文）
    from server.config import setup_sys_path, get_config, save_config, WEB_DIR

setup_sys_path()

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from starlette.staticfiles import StaticFiles  # noqa: E402

try:
    from . import account_api, emu_api, maa_api, state  # noqa: E402
    from .auth import verify_token  # noqa: E402
    from .oplog import recent as oplog_recent  # noqa: E402
except ImportError:
    from server import account_api, emu_api, maa_api, state  # noqa: E402
    from server.auth import verify_token  # noqa: E402
    from server.oplog import recent as oplog_recent  # noqa: E402


def _wrap_with_broadcast(app: FastAPI) -> None:
    """给写操作端点包一层：执行后主动广播状态变化（SSE 秒级更新）。"""
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods or "POST" not in methods:
            continue
        if route.path.startswith("/api/sse"):
            continue
        original = route.endpoint

        def make_wrapper(fn):
            def wrapped(*args, **kwargs):
                try:
                    result = fn(*args, **kwargs)
                finally:
                    threading.Thread(target=state.broadcast_now, daemon=True).start()
                return result
            wrapped.__name__ = getattr(fn, "__name__", "wrapped")
            return wrapped

        route.endpoint = make_wrapper(original)


def create_app() -> FastAPI:
    app = FastAPI(title="MAAOrch 小工具", docs_url=None, redoc_url=None)

    # 鉴权中间件（作用于 /api/*；token 为空 = 本机模式不鉴权）
    @app.middleware("http")
    async def _auth_mw(request, call_next):
        if request.url.path.startswith("/api/"):
            # EventSource(SSE) 无法带 header，token 走 query 参数
            header_tok = request.headers.get("x-agent-token")
            query_tok = request.query_params.get("token")
            tok = header_tok or query_tok
            try:
                verify_token(x_agent_token=tok)
            except Exception:
                from fastapi.responses import JSONResponse
                return JSONResponse({"ok": False, "error": "无效的 token"},
                                    status_code=401)
        return await call_next(request)

    app.include_router(account_api.router)
    app.include_router(emu_api.router)
    app.include_router(maa_api.router)
    app.include_router(state.router)

    @app.get("/api/oplog")
    def api_oplog(limit: int = 200):
        return {"ok": True, "logs": oplog_recent(limit)}

    @app.get("/api/config")
    def api_config():
        cfg = get_config()
        return {"ok": True, "port": cfg.get("port"),
                "maa_source_path": cfg.get("maa_source_path"),
                "project_root": cfg.get("project_root"),
                "pool_size": cfg.get("pool_size"),
                "auth_enabled": bool(cfg.get("token"))}

    _wrap_with_broadcast(app)

    # 静态 UI
    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(WEB_DIR / "index.html"))

    return app


def start_server(host: str = "127.0.0.1", port: int = 0) -> None:
    cfg = get_config()
    if not port:
        port = int(cfg.get("port", 18889) or 18889)
    app = create_app()
    state.start_monitor()

    import uvicorn
    print(f"[MAAOrch tool] server starting: http://{host}:{port}")
    print(f"[MAAOrch tool] maa_source_path: {cfg.get('maa_source_path', 'not set')}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    start_server(host)
