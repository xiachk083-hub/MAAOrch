"""Web UI entry point — browser + system tray (no pywebview)."""
import sys, os, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from infrastructure.platform_helper import is_admin, run_as_admin
from infrastructure.logger import Logger

def main():
    if not is_admin() and "--no-elevate" not in sys.argv:
        run_as_admin()
        sys.exit(0)

    import ctypes as _ct
    hwnd = _ct.windll.user32.FindWindowW(None, "MAAOrchWeb")
    if hwnd:
        _ct.windll.user32.ShowWindow(hwnd, 9)
        _ct.windll.user32.SetForegroundWindow(hwnd)
        sys.exit(0)

    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")

    _LOG = Logger("app")
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
            desktop_bat.write_text(f'@start /min "" "{sys.executable}" "{__file__}"')
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

    from services.runner import AccountRunner
    from services.launch_queue import LaunchQueue
    runner = AccountRunner(ctx)
    launch_queue = LaunchQueue(ctx)
    runner.log_msg.connect(lambda m: _LOG.info(f"[MAA] {m}"))
    runner.account_finished.connect(launch_queue.on_account_finished)
    launch_queue._restore()
    launch_queue.start()
    ctx._mw = type('MW', (), {
        'runner': runner, 'launch_queue': launch_queue, 'config': config,
        'accounts': ctx.accounts, 'ctx': ctx, 'warehouse': [],
        '_proc_status': set(), '_proc_start_times': {},
        '_log': lambda self, msg: _LOG.info(msg),
    })()

    from services.dispatch_pool import create_dispatch
    for a in ctx.accounts:
        plan = a.get("smart_plan", "")
        if plan and not a.get("dispatch_id"):
            a["dispatch_id"] = create_dispatch(plan.split(","))

    port = config.get("api_port", 19999)
    token = config.get("api_token", "")
    from network.api_server import ApiServer
    api = ApiServer(port, token, ctx._mw)
    api.daemon = True
    api.start()

    url = f"http://127.0.0.1:{port}/"
    _LOG.info(f"Web UI: {url}")

    import webbrowser
    webbrowser.open(url)

    from PySide6.QtWidgets import QSystemTrayIcon, QMenu
    from PySide6.QtGui import QIcon
    icon_path = str(Path(__file__).parent / "icon.ico")
    icon = QIcon(icon_path) if Path(icon_path).exists() else QIcon()
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("MAAOrch")
    menu = QMenu()
    menu.addAction("打开浏览器", lambda: webbrowser.open(url))
    def _quit():
        try:
            if hasattr(api, 'stop_server'):
                api.stop_server()
            for aid in list(runner._active.keys()):
                runner.stop(aid)
        except Exception as e:
            _LOG.error(f"清理异常: {e}")
        _LOG.info("══ MAAOrch 退出 ══")
        app.quit()
    menu.addAction("退出", _quit)
    tray.setContextMenu(menu)
    tray.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
