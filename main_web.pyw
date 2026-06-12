"""Web UI entry point — pywebview native window + browser access."""
import sys, os, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from infrastructure.platform_helper import is_admin, run_as_admin
from infrastructure.utils import setup_proxy
from infrastructure.logger import Logger

def main():
    if not is_admin() and "--no-elevate" not in sys.argv:
        run_as_admin()
        sys.exit(0)

    # Single instance check
    import ctypes as _ct
    hwnd = _ct.windll.user32.FindWindowW(None, "MAAOrchWeb")
    if hwnd:
        _ct.windll.user32.ShowWindow(hwnd, 9)
        _ct.windll.user32.SetForegroundWindow(hwnd)
        sys.exit(0)

    # Minimal Qt for ApiServer QThread + system tray
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")

    _LOG = Logger("app")
    _LOG.info("══ MAAOrch Web 启动 ══")

    # Load config (minimal bootstrap)
    from models.config_manager import load_config, save_config
    config = load_config()

    # Create ServiceContext (minimal)
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

    # Initialize runner + launch_queue
    from services.runner import AccountRunner
    from services.launch_queue import LaunchQueue
    runner = AccountRunner(ctx)
    launch_queue = LaunchQueue(ctx)
    runner.log_msg.connect(lambda m: _LOG.info(f"[MAA] {m}"))
    runner.account_finished.connect(launch_queue.on_account_finished)
    ctx._mw = type('MW', (), {
        'runner': runner, 'launch_queue': launch_queue, 'config': config,
        'accounts': ctx.accounts, 'ctx': ctx
    })()

    # Migrate old accounts
    from services.dispatch_pool import create_dispatch
    for a in ctx.accounts:
        plan = a.get("smart_plan", "")
        if plan and not a.get("dispatch_id"):
            a["dispatch_id"] = create_dispatch(plan.split(","))

    # Start API server
    port = config.get("api_port", 19999)
    token = config.get("api_token", "")
    from network.api_server import ApiServer
    api = ApiServer(port, token, ctx._mw)
    api.daemon = True
    api.start()

    # Open browser + pywebview
    import webbrowser
    url = f"http://127.0.0.1:{port}/"
    webbrowser.open(url)

    has_webview = False
    try:
        import webview
        has_webview = True
    except ImportError:
        pass

    def cleanup():
        try:
            if hasattr(api, 'stop_server'):
                api.stop_server()
            for aid in list(runner._active.keys()):
                runner.stop(aid)
        except Exception as e:
            _LOG.error(f"清理异常: {e}")
        _LOG.info("══ MAAOrch 退出 ══")

    if has_webview:
        _LOG.info(f"Web UI: {url} (pywebview + 浏览器)")
        import webview as _wv
        _wv.create_window("MAAOrch", url, width=1100, height=700, resizable=True)
        _wv.start(private_mode=False)
    else:
        _LOG.info(f"Web UI: {url} (浏览器 + 系统托盘)")
        from PySide6.QtWidgets import QSystemTrayIcon, QMenu
        from PySide6.QtGui import QIcon
        tray = QSystemTrayIcon(QIcon(), app)
        tray.setToolTip("MAAOrch")
        menu = QMenu()
        menu.addAction("打开浏览器", lambda: webbrowser.open(url))
        menu.addAction("退出", lambda: (cleanup(), app.quit()))
        tray.setContextMenu(menu)
        tray.show()
        import sys as _sys
        _sys.exit(app.exec())
        return

    cleanup()

if __name__ == "__main__":
    main()
