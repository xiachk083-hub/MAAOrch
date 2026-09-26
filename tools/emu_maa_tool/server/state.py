"""状态监控线程（psutil 进程快照 <1s）+ SSE 广播。

不轮询前端：后端后台线程周期性构建卡片快照，变化时推给所有 SSE 连接。
模拟器运行状态用 psutil 真实进程检测（快），端口用 detect_emu_adb（账号
绑定的才查）。"""
from __future__ import annotations
import json
import threading
import time
from collections import deque

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from .config import setup_sys_path, get_config, INSTANCES_DIR
from .account_api import _load as load_accounts

setup_sys_path()

from services.emu_service import _headless_alive, _running_headless_idx  # noqa: E402
from .emu_api import detect_emu_port  # noqa: E402

router = APIRouter()

_clients: dict[int, deque] = {}
_clients_lock = threading.Lock()
_last_snapshot: dict = {}
_last_snapshot_lock = threading.Lock()
_next_client_id = [0]


def broadcast(payload: dict) -> None:
    """推给所有 SSE 客户端。"""
    data = "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
    with _clients_lock:
        for cid, q in list(_clients.items()):
            try:
                q.put(data)
            except Exception:
                _clients.pop(cid, None)


def _build_snapshot() -> dict:
    """构建卡片快照：账号 × (模拟器状态/端口/MAA 状态/窗口/配置)。"""
    data = load_accounts()
    accounts = data.get("accounts", [])
    emus_running = set(_running_headless_idx())  # psutil，快

    # MAA 实例占用情况（副本 .meta/.pid）
    inst_by_account: dict[str, dict] = {}
    if INSTANCES_DIR.is_dir():
        for d in INSTANCES_DIR.iterdir():
            if not d.is_dir():
                continue
            meta_p = d / ".meta"
            pid_p = d / ".pid"
            meta = {}
            try:
                aid, name, emu = meta_p.read_text(encoding="utf-8").split("|", 2)
                meta = {"aid": aid, "name": name, "emu_index": emu}
            except Exception:
                continue
            if not meta:
                continue
            pid = 0
            try:
                pid = int(pid_p.read_text().strip())
            except Exception:
                pass
            alive = False
            if pid:
                try:
                    import psutil
                    p = psutil.Process(pid)
                    alive = p.is_running() and p.name().lower() == "maa.exe"
                except Exception:
                    alive = False
            inst_by_account[meta["aid"]] = {
                "running": alive, "pid": pid, "inst": d.name,
            }

    out = []
    for acc in accounts:
        aid = acc.get("id", "")
        emu_index = str(acc.get("emu_index", "") or "")
        emu_running = emu_index in emus_running
        port = ""
        if emu_index:
            port = detect_emu_port(emu_index) if emu_running else ""
        inst = inst_by_account.get(aid, {})
        maa_running = bool(inst.get("running"))
        out.append({
            "id": aid,
            "name": acc.get("name", aid),
            "emu_index": emu_index,
            "group": acc.get("group", ""),
            "note": acc.get("note", ""),
            "emu": {"running": emu_running, "adb_port": port},
            "maa": {
                "running": maa_running,
                "instance": inst.get("inst", ""),
                "pid": inst.get("pid", 0),
            },
        })
    return {"accounts": out, "ts": time.time()}


def _monitor_loop(interval: float = 3.0) -> None:
    """后台周期构建快照，变化时广播。"""
    global _last_snapshot
    while True:
        try:
            snap = _build_snapshot()
            with _last_snapshot_lock:
                changed = snap != _last_snapshot
                _last_snapshot = snap
            if changed:
                broadcast({"type": "snapshot", **snap})
        except Exception:
            pass
        time.sleep(interval)


def start_monitor() -> None:
    threading.Thread(target=_monitor_loop, daemon=True).start()


def get_snapshot() -> dict:
    with _last_snapshot_lock:
        if _last_snapshot:
            return _last_snapshot
    return _build_snapshot()


@router.get("/api/sse")
def api_sse():
    """SSE 流：连接即推一次快照，之后持续推变化。"""
    q: deque = deque(maxlen=100)

    def gen():
        # 立即推一次当前快照
        yield "data: " + json.dumps({"type": "snapshot", **get_snapshot()},
                                    ensure_ascii=False) + "\n\n"
        with _clients_lock:
            _next_client_id[0] += 1
            cid = _next_client_id[0]
            _clients[cid] = q
        try:
            while True:
                try:
                    item = q.popleft()
                except IndexError:
                    yield ": keepalive\n\n"
                    time.sleep(15)
                    continue
                yield item
                time.sleep(0.05)
        except Exception:
            pass
        finally:
            with _clients_lock:
                _clients.pop(cid, None)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def broadcast_now() -> None:
    """操作后主动广播一次。"""
    try:
        broadcast({"type": "snapshot", **_build_snapshot()})
    except Exception:
        pass
