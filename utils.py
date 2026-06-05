import sys,os,ctypes,re,shutil
from pathlib import Path

def is_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin()!=0
    except: return False
def run_as_admin():
    ctypes.windll.shell32.ShellExecuteW(None,"runas",sys.executable,'"'+'" "'.join(sys.argv)+'"',None,1)
def make_id():
    import uuid; return uuid.uuid4().hex[:8]
def parse_maa_version(path):
    try:
        m=re.search(r'v?(\d+\.\d+\.\d+)',Path(path).parent.name)
        if m: return m.group(0) if m.group(0).startswith('v') else 'v'+m.group(0)
    except: pass
    return None
def get_platform_key():
    import platform; arch=platform.machine().lower()
    return f"win-{'x64' if arch in ('amd64','x86_64') else arch}"
def _version_tuple(v):
    try: return tuple(int(x) for x in v.lstrip('v').split('.'))
    except: return (0,)
def _rmtree_force(path):
    def on_error(func,p,exc_info):
        try: os.chmod(p,0o777); func(p)
        except: pass
    shutil.rmtree(path,onerror=on_error)
def _find_maa_cli():
    import shutil as _s
    for n in ("maa","maa-cli","maa.exe","maa-cli.exe"):
        if _s.which(n): return _s.which(n)
    for d in (Path(os.environ.get("LOCALAPPDATA",""))/"maa-cli",Path(__file__).parent/"maa-cli",Path("C:/Program Files/maa-cli")):
        for n in ("maa.exe","maa-cli.exe"):
            if (d/n).exists(): return str(d/n)
    return None

