"""Web UI entry point — browser + system tray (no pywebview)."""
import sys, os, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from infrastructure.platform_helper import is_admin, run_as_admin
from infrastructure.logger import Logger

_LOG = Logger("app")

def main():
    if "--admin" in sys.argv and not is_admin():
        run_as_admin()
        sys.exit(0)
    # --no-elevate kept for backward compatibility (no-op now)

    import ctypes as _ct
    hwnd = _ct.windll.user32.FindWindowW(None, "MAAOrchWeb")
    if hwnd:
        _ct.windll.user32.ShowWindow(hwnd, 9)
        _ct.windll.user32.SetForegroundWindow(hwnd)
        sys.exit(0)

    # PySide6 is optional — pure Web mode works without it
    try:
        from PySide6.QtWidgets import QApplication
        _app = QApplication(sys.argv)
        _app.setQuitOnLastWindowClosed(False)
        _app.setStyle("Fusion")
        _has_qt = True
    except ImportError:
        _app = None
        _has_qt = False
        _LOG.info("PySide6 未安装，使用纯 Web 模式（无托盘图标）")

    _LOG.info("══ MAAOrch Web 启动 ══")

    from infrastructure.maa_core import preload, get_version
    source_path = Path(__file__).parent / "services" / "maa" / "source"
    if source_path.exists() and (source_path / "MaaCore.dll").exists():
        if preload(source_path):
            _LOG.info(f"MaaCore 已加载 (版本: {get_version()})")
        else:
            _LOG.warning("MaaCore 加载失败，回退到子进程模式")
    else:
        _LOG.info("MaaCore.dll 未找到，使用子进程模式")

    desktop_bat = Path(os.environ.get("USERPROFILE", ".")) / "Desktop" / "MAAOrch.bat"
    if not desktop_bat.exists():
        try:
            desktop_bat.write_text(f'''@echo off
chcp 65001 >nul
title MAAOrch
cd /d "{Path(__file__).parent}"
echo [MAAOrch] Starting...
start /min "" "{sys.executable}" "{__file__}"
echo [MAAOrch] Waiting for server...
set WAIT_SEC=0
:loop
timeout /t 3 /nobreak >nul
set /a WAIT_SEC+=3
curl -s http://127.0.0.1:19999/ >nul 2>&1
if not errorlevel 1 (
    echo [MAAOrch] Ready!
    start http://127.0.0.1:19999/
    exit /b 0
)
if %WAIT_SEC% lss 120 goto loop
echo [MAAOrch] Timeout, check debug.log
pause
''')
        except Exception:
            pass

    from models.config_manager import load_config, save_config
    config = load_config()

    from app.service_context import ServiceContext
    ctx = ServiceContext(
        log=lambda msg: _LOG.info(msg),
        save=lambda: save_config(config),
        set_status=lambda msg: _LOG.info(f"[状态] {msg}"),
        set_theme=lambda m: None,
        show_dashboard=lambda a: None,
        inject_config=lambda w, a: None,
        launch_program=lambda w, a: None,
        start_pipeline=lambda: None,
        restart_api_server=lambda: None,
        accounts=config.get("accounts", []),
        warehouse=config.get("warehouse", []),
        config=config,
        groups=config.get("groups", []),
    )
    config["accounts"] = ctx.accounts

    from services.config_injector import ConfigService
    ctx.cfg = ConfigService(ctx)

    # Auto-download MAA if missing (background — do NOT block server startup)
    from pathlib import Path as _P
    _maa_source = _P(__file__).parent / "services" / "maa" / "source"
    from services.maa_download import ensure_maa_available

    def _maa_bg():
        try:
            _LOG.info("[MAA] 后台检查/下载 MAA（首次启动需下载 ~200MB）")
            if ensure_maa_available(ctx, _maa_source):
                _LOG.info("[MAA] 下载完成，后台创建实例池...")
                from services.instance_pool import ensure_maa_instances_async
                ensure_maa_instances_async(ctx)
            else:
                _LOG.warning("[MAA] MAA 未就绪（详见日志），可稍后手动触发下载")
        except Exception as e:
            _LOG.error(f"[MAA] 后台初始化异常: {e}")

    threading.Thread(target=_maa_bg, daemon=True).start()

    from services.runner import AccountRunner
    from services.launch_queue import LaunchQueue
    runner = AccountRunner(ctx)
    launch_queue = LaunchQueue(ctx)
    runner._log_msg_callbacks.append(lambda m: _LOG.info(f"[MAA] {m}"))
    runner._finished_callbacks.append(launch_queue.on_account_finished)
    # Gantt history
    from pathlib import Path as _P
    _gantt_file = _P(__file__).parent / "screenshots" / "gantt_history.json"
    _gantt_events = []
    if _gantt_file.exists():
        try:
            import json as _j
            _gantt_events = _j.loads(_gantt_file.read_text(encoding="utf-8"))
        except: pass
    def _save_gantt():
        try:
            import json as _j
            _gantt_file.parent.mkdir(parents=True, exist_ok=True)
            _gantt_file.write_text(_j.dumps(_gantt_events[-500:], ensure_ascii=False), encoding="utf-8")
        except: pass
    # Set ctx._mw BEFORE start() so _bg_tick can access runner
    from app.web_context import WebContext
    ctx._mw = WebContext(
        runner=runner, launch_queue=launch_queue, config=config,
        accounts=ctx.accounts, ctx=ctx, warehouse=[],
        _proc_status=ctx.proc_status, _proc_start_times={},
        _notifications=[], _ai_insights=[], _oplog=[], _res_samples=[],
        _gantt_events=_gantt_events, _save_gantt=_save_gantt,
        _log=lambda msg: _LOG.info(msg),
    )
    launch_queue._restore()
    launch_queue.start()
    launch_queue.resume()  # Queue starts paused; resume for Web UI

    from services.scheduler import start_scheduler
    start_scheduler(ctx)

    from services.dispatch_pool import create_dispatch
    for a in ctx.accounts:
        plan = a.get("smart_plan", "")
        if plan and not a.get("dispatch_id"):
            a["dispatch_id"] = create_dispatch(plan.split(","))

    port = config.get("api_port", 19999)
    token = config.get("api_token", "")
    import mimetypes
    for ext, ct in {".css":"text/css; charset=utf-8", ".html":"text/html; charset=utf-8", ".htm":"text/html; charset=utf-8", ".js":"application/javascript; charset=utf-8", ".json":"application/json; charset=utf-8", ".txt":"text/plain; charset=utf-8", ".svg":"image/svg+xml; charset=utf-8"}.items():
        mimetypes.add_type(ct, ext)
    from network.api_fastapi import create_app, start_server
    import uvicorn, threading as _th
    _fastapi_app = create_app(ctx._mw)
    _bind = config.get("bind_address", "0.0.0.0")
    _uvicorn_config = uvicorn.Config(_fastapi_app, host=_bind, port=port, log_level="info")
    _uvicorn_server = uvicorn.Server(_uvicorn_config)
    _th.Thread(target=_uvicorn_server.run, daemon=True).start()

    url = f"http://{_bind}:{port}/"
    _LOG.info(f"Web UI: {url}")

    import webbrowser
    webbrowser.open(url)

    def _quit():
        try:
            for aid in list(runner._active.keys()):
                runner.stop(aid)
        except Exception as e:
            _LOG.error(f"清理异常: {e}")
        _LOG.info("══ MAAOrch 退出 ══")
        if _app:
            _app.quit()

    if _has_qt:
        from PySide6.QtWidgets import QSystemTrayIcon, QMenu
        from PySide6.QtGui import QIcon
        tray = QSystemTrayIcon(_app)
        tray.setToolTip("MAAOrch")
        menu = QMenu()
        menu.addAction("打开浏览器", lambda: webbrowser.open(url))
        menu.addAction("退出", _quit)
        tray.setContextMenu(menu)
        tray.show()
        sys.exit(_app.exec())
    else:
        _LOG.info("按 Ctrl+C 退出")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            _quit()

if __name__ == "__main__":
    main()
