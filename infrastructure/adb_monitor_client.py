"""Integration with Go ADB monitor service."""
import json
import urllib.request
import urllib.error

ADB_MONITOR_URL = "http://127.0.0.1:19998"

def _is_available() -> bool:
    try:
        r = urllib.request.urlopen(f"{ADB_MONITOR_URL}/", timeout=1)
        return r.status == 200
    except Exception:
        return False

def ping(addr: str) -> bool:
    """Ping an ADB device. Returns True if reachable."""
    try:
        r = urllib.request.urlopen(f"{ADB_MONITOR_URL}/ping?addr={addr}", timeout=5)
        d = json.loads(r.read())
        return d.get("ok", False)
    except Exception:
        return False

def connect(addr: str) -> bool:
    """Connect to an ADB device. Returns True if connected."""
    try:
        r = urllib.request.urlopen(f"{ADB_MONITOR_URL}/connect?addr={addr}", timeout=5)
        d = json.loads(r.read())
        return d.get("ok", False)
    except Exception:
        return False

def health_check() -> dict:
    """Check and repair ADB server. Returns status dict."""
    try:
        r = urllib.request.urlopen(f"{ADB_MONITOR_URL}/health", timeout=10)
        return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}

def mumu_launch(vm: str) -> bool:
    """Launch a MuMu emulator VM."""
    try:
        data = json.dumps({"action": "launch"}).encode()
        r = urllib.request.urlopen(f"{ADB_MONITOR_URL}/mumu/launch?vm={vm}", data=data, timeout=15)
        d = json.loads(r.read())
        return d.get("ok", False)
    except Exception:
        return False

def mumu_shutdown(vm: str) -> bool:
    """Shutdown a MuMu emulator VM."""
    try:
        r = urllib.request.urlopen(f"{ADB_MONITOR_URL}/mumu/shutdown?vm={vm}", timeout=10)
        d = json.loads(r.read())
        return d.get("ok", False)
    except Exception:
        return False

def mumu_info(vm: str) -> dict | None:
    """Get info about a MuMu emulator VM."""
    try:
        r = urllib.request.urlopen(f"{ADB_MONITOR_URL}/mumu/info?vm={vm}", timeout=10)
        d = json.loads(r.read())
        return d.get("data") if d.get("ok") else None
    except Exception:
        return None
