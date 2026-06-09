"""Platform detection, admin elevation, and system-level utilities."""
import sys, os, ctypes, platform, shutil
from pathlib import Path
import subprocess as _sp


def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_as_admin() -> None:
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable,
        _sp.list2cmdline(sys.argv), None, 1
    )


def get_platform_key() -> str:
    arch = platform.machine().lower()
    return f"win-{'x64' if arch in ('amd64', 'x86_64') else arch}"


def find_maa_cli() -> str | None:
    for name in ("maa-cli", "maa-cli.exe", "maa", "maa.exe"):
        if shutil.which(name):
            return shutil.which(name)
    for base in (
        Path(os.environ.get("LOCALAPPDATA", "")) / "maa-cli",
        Path(__file__).parent.parent / "maa-cli",
        Path("C:/Program Files/maa-cli"),
    ):
        for name in ("maa.exe", "maa-cli.exe"):
            p = base / name
            if p.exists():
                return str(p)
    return None
