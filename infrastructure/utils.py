import sys,os,re,shutil,socket
import urllib.request
from pathlib import Path
from infrastructure.platform_helper import is_admin, run_as_admin, get_platform_key, find_maa_cli as _find_maa_cli


def setup_proxy() -> None:
    """Auto-detect proxy (Clash/v2ray/etc) for GitHub access. Call once at startup."""
    for p in [os.environ.get("HTTP_PROXY",""),os.environ.get("http_proxy",""),
              os.environ.get("HTTPS_PROXY",""),os.environ.get("https_proxy","")]:
        if p:
            urllib.request.install_opener(urllib.request.build_opener(
                urllib.request.ProxyHandler({"http":p,"https":p})))
            return
    for port in [7890,7891,1080,10809,8080]:
        try:
            with socket.socket() as s:
                s.settimeout(0.3); s.connect(("127.0.0.1",port))
            p=f"http://127.0.0.1:{port}"
            urllib.request.install_opener(urllib.request.build_opener(
                urllib.request.ProxyHandler({"http":p,"https":p})))
            return
        except Exception:
            pass

def is_admin() -> bool:
    try: return ctypes.windll.shell32.IsUserAnAdmin()!=0
    except: return False
def run_as_admin() -> None:
    import subprocess as _sp
    ctypes.windll.shell32.ShellExecuteW(None,"runas",sys.executable,_sp.list2cmdline(sys.argv),None,1)
def make_id() -> str:
    import uuid; return uuid.uuid4().hex[:8]
def parse_maa_version(path: Path) -> str | None:
    try:
        m=re.search(r'v?(\d+\.\d+\.\d+)',Path(path).parent.name)
        if m: return m.group(0) if m.group(0).startswith('v') else 'v'+m.group(0)
    except: pass
    return None
def get_platform_key() -> str:
    import platform; arch=platform.machine().lower()
    return f"win-{'x64' if arch in ('amd64','x86_64') else arch}"
def _version_tuple(v: str) -> tuple[int, ...]:
    try: return tuple(int(x) for x in v.lstrip('v').split('.'))
    except: return (0,)
def _rmtree_force(path: str | Path) -> None:
    def on_error(func,p,exc_info):
        try: os.chmod(p,0o777); func(p)
        except: pass
    shutil.rmtree(path,onerror=on_error)
def _find_maa_cli() -> str | None:
    import shutil as _s
    for n in ("maa-cli","maa-cli.exe","maa","maa.exe"):
        if _s.which(n): return _s.which(n)
    for d in (Path(os.environ.get("LOCALAPPDATA",""))/"maa-cli",Path(__file__).parent/"maa-cli",Path("C:/Program Files/maa-cli")):
        for n in ("maa.exe","maa-cli.exe"):
            if (d/n).exists(): return str(d/n)
    return None


