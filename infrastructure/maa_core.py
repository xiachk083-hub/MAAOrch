"""MaaCore.dll ctypes bridge — direct integration with fallback to MAA.exe."""
from __future__ import annotations
import ctypes
import json
import threading
from pathlib import Path
from typing import Callable, Any

_loaded = False
_lib = None
_lock = threading.Lock()


def preload(source_path: str | Path) -> bool:
    """Load MaaCore.dll and resources. Call once at startup before any account runs.
    Returns True on success. If False, runner falls back to subprocess MAA.exe."""
    global _loaded, _lib
    with _lock:
        if _loaded:
            return True
        source = Path(source_path)
        dll_path = source / "MaaCore.dll"
        if not dll_path.exists():
            return False
        try:
            _lib = ctypes.WinDLL(str(dll_path))
            _setup_func_types()
            # Set user dir for logs
            user_dir = source / "debug"
            user_dir.mkdir(exist_ok=True)
            _lib.AsstSetUserDir(str(user_dir).encode("utf-8"))
            # Load resources
            ret = _lib.AsstLoadResource(str(source).encode("utf-8"))
            if not ret:
                return False
            _loaded = True
            return True
        except Exception:
            _loaded = False
            return False


def is_loaded() -> bool:
    return _loaded


def create_instance(callback: Callable[[int, str, Any], None], arg=None):
    """Create an Asst instance. Call preload() first."""
    global _lib
    if not _loaded or not _lib:
        return None
    CB_TYPE = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)
    cb = CB_TYPE(callback)
    ptr = _lib.AsstCreateEx(cb, arg)
    return _AsstInstance(ptr, cb)


def get_version() -> str:
    if _lib:
        return _lib.AsstGetVersion().decode("utf-8")
    return ""


# ── Message types (from MAA callback) ──
MSG_INTERNAL_ERROR = 0
MSG_INIT_FAILED = 1
MSG_CONNECTION_INFO = 2
MSG_ALL_TASKS_COMPLETED = 3
MSG_ASYNC_CALL_INFO = 4
MSG_DESTROYED = 5
MSG_TASK_CHAIN_ERROR = 10000
MSG_TASK_CHAIN_START = 10001
MSG_TASK_CHAIN_COMPLETED = 10002
MSG_TASK_CHAIN_EXTRA_INFO = 10003
MSG_TASK_CHAIN_STOPPED = 10004
MSG_SUB_TASK_ERROR = 20000
MSG_SUB_TASK_START = 20001
MSG_SUB_TASK_COMPLETED = 20002
MSG_SUB_TASK_EXTRA_INFO = 20003
MSG_SUB_TASK_STOPPED = 20004


class _AsstInstance:
    """Thin wrapper around a single MaaCore instance."""
    
    def __init__(self, ptr, cb):
        self._ptr = ptr
        self._cb = cb  # keep ref alive
    
    def connect(self, adb_path: str, address: str, config: str = "General") -> bool:
        return _lib.AsstConnect(self._ptr, adb_path.encode("utf-8"),
                                address.encode("utf-8"), config.encode("utf-8"))
    
    def append_task(self, type_name: str, params: dict = None) -> int:
        if params is None:
            params = {}
        return _lib.AsstAppendTask(self._ptr, type_name.encode("utf-8"),
                                    json.dumps(params, ensure_ascii=False).encode("utf-8"))
    
    def set_task_params(self, task_id: int, params: dict) -> bool:
        return _lib.AsstSetTaskParams(self._ptr, task_id,
                                       json.dumps(params, ensure_ascii=False).encode("utf-8"))
    
    def start(self) -> bool:
        return _lib.AsstStart(self._ptr)
    
    def stop(self) -> bool:
        return _lib.AsstStop(self._ptr)
    
    def running(self) -> bool:
        return _lib.AsstRunning(self._ptr)
    
    def get_image(self, size: int) -> bytes | None:
        buf = (ctypes.c_byte * size)()
        got = _lib.AsstGetImage(self._ptr, buf, size)
        if got and got > 0:
            return bytes(buf)
        return None
    
    def destroy(self):
        if self._ptr:
            _lib.AsstDestroy(self._ptr)
            self._ptr = None


def _setup_func_types():
    global _lib
    _lib.AsstSetUserDir.restype = ctypes.c_bool
    _lib.AsstSetUserDir.argtypes = (ctypes.c_char_p,)
    _lib.AsstLoadResource.restype = ctypes.c_bool
    _lib.AsstLoadResource.argtypes = (ctypes.c_char_p,)
    _lib.AsstCreate.restype = ctypes.c_void_p
    _lib.AsstCreateEx.restype = ctypes.c_void_p
    _lib.AsstCreateEx.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    _lib.AsstDestroy.argtypes = (ctypes.c_void_p,)
    _lib.AsstConnect.restype = ctypes.c_bool
    _lib.AsstConnect.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p)
    _lib.AsstAppendTask.restype = ctypes.c_int
    _lib.AsstAppendTask.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p)
    _lib.AsstSetTaskParams.restype = ctypes.c_bool
    _lib.AsstSetTaskParams.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p)
    _lib.AsstStart.restype = ctypes.c_bool
    _lib.AsstStart.argtypes = (ctypes.c_void_p,)
    _lib.AsstStop.restype = ctypes.c_bool
    _lib.AsstStop.argtypes = (ctypes.c_void_p,)
    _lib.AsstRunning.restype = ctypes.c_bool
    _lib.AsstRunning.argtypes = (ctypes.c_void_p,)
    _lib.AsstGetImage.restype = ctypes.c_int
    _lib.AsstGetImage.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)
    _lib.AsstGetVersion.restype = ctypes.c_char_p
