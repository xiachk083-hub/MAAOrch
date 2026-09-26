"""作业实验室（GUI 后端）—— 把 prts 作业筛选与「跑不了就换下一份」接到 Web 界面上。

为什么单独成模块:
    network/api_fastapi.py 是单文件大 app（@app.get 直接挂），新增路由一律走
    `register(app)` 注入，避免再去动主文件的路由区（也就避免覆盖事故）。

页面（ui/web/copilot_lab.js）用的四个接口:
    GET  /api/copilot/screen?stage=act54side_ex02[&top=6][&refresh=1]
    POST /api/copilot/fallback   {stage|codes, emu, max_tries, support, potion}
    GET  /api/copilot/fallback/status
    POST /api/copilot/fallback/stop
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import Body, Query
from fastapi.responses import JSONResponse

PROJ = Path(__file__).resolve().parent.parent
FINDER = PROJ / "tools" / "prts_job_finder.py"
RUNNER = PROJ / "tools" / "maa_job_fallback.py"
REPORT = PROJ / "data" / "copilot" / "fallback_report.json"
_SCREEN_DIR = PROJ / "data" / "copilot" / "screening"

_state: dict = {"proc": None, "cmd": [], "started": 0.0, "log": None}
_lock = threading.Lock()


def _operbox() -> Path:
    """默认用最新的一份干员箱（MAA OperBox 导出或手工上传的名单）"""
    cands = sorted(list((PROJ / "data").glob("operbox*.json")), key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        raise FileNotFoundError("data/ 下没有 operbox*.json（先用 tools/maa_operbox.py 读一次，或上传名单）")
    return cands[0]


def screen(stage: str, top: int = 6, refresh: bool = False) -> dict:
    """筛一关：调 tools/prts_job_finder.py（网络慢，结果落盘缓存）"""
    _SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    cache = _SCREEN_DIR / f"{stage}.json"
    if cache.exists() and not refresh and time.time() - cache.stat().st_mtime < 6 * 3600:
        return json.loads(cache.read_text(encoding="utf-8"))
    box = _operbox()
    cmd = [sys.executable, str(FINDER), "--stages", stage, "--operbox", str(box),
           "--out", str(cache), "--dump-dir", str(PROJ / "data" / "copilot" / "picked"), "--top", str(top)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                       encoding="utf-8", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if not cache.exists():
        raise RuntimeError((r.stdout or "")[-400:] + (r.stderr or "")[-200:])
    data = json.loads(cache.read_text(encoding="utf-8"))
    data["_console"] = (r.stdout or "")[-4000:]
    data["_operbox"] = box.name
    return data


def start(stage: str = "", codes: str = "", emu: str = "1", max_tries: int = 5,
          support: bool = True, potion: bool = True) -> dict:
    """起一次「自动降级重试」——独立进程跑，日志落盘，前端轮询状态"""
    with _lock:
        if _state["proc"] and _state["proc"].poll() is None:
            raise RuntimeError("已有一次运行在进行中")
        log = PROJ / "logs" / f"fallback_{time.strftime('%m%d_%H%M%S')}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(RUNNER), "--emu", str(emu), "--max-tries", str(max_tries),
               "--out", str(REPORT)]
        cmd += ["--stage", stage, "--screening", str(_SCREEN_DIR / f"{stage}.json")] if stage else \
               ["--codes", codes]
        if not support:
            cmd.append("--no-support")
        fh = open(log, "w", encoding="utf-8")
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _state.update({"proc": proc, "cmd": cmd, "started": time.time(), "log": log})
        return {"pid": proc.pid, "log": str(log), "cmd": " ".join(cmd[2:])}


def status() -> dict:
    """运行状态：进程是否在跑 + 日志尾部 + 最近一次报告"""
    with _lock:
        proc = _state.get("proc")
        running = bool(proc and proc.poll() is None)
        out = {"running": running, "elapsed": round(time.time() - _state["started"], 1) if _state["started"] else 0,
               "log": str(_state.get("log") or ""), "tail": ""}
        if _state.get("log") and Path(_state["log"]).exists():
            txt = Path(_state["log"]).read_text(encoding="utf-8", errors="replace")
            out["tail"] = txt[-4000:]
    if REPORT.exists():
        try:
            out["report"] = json.loads(REPORT.read_text(encoding="utf-8"))
        except Exception:
            pass
    return out


def stop() -> dict:
    with _lock:
        proc = _state.get("proc")
        if proc and proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _state["proc"] = None
            return {"stopped": True}
    return {"stopped": False}


def register(app) -> None:
    """把四条路由挂到主 app 上（主文件只需 import + register(app)）"""

    @app.get("/api/copilot/screen")
    def _screen(stage: str = Query(...), top: int = 6, refresh: int = 0):
        try:
            return JSONResponse(content=screen(stage, top=top, refresh=bool(refresh)))
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {e}"})

    @app.post("/api/copilot/fallback")
    def _start(payload: dict = Body(default={})):
        try:
            return JSONResponse(content=start(stage=payload.get("stage", ""), codes=payload.get("codes", ""),
                                              emu=str(payload.get("emu", "1")),
                                              max_tries=int(payload.get("max_tries", 5)),
                                              support=bool(payload.get("support", True)),
                                              potion=bool(payload.get("potion", True))))
        except Exception as e:
            return JSONResponse(status_code=400, content={"error": f"{type(e).__name__}: {e}"})

    @app.get("/api/copilot/fallback/status")
    def _status():
        return JSONResponse(content=status())

    @app.post("/api/copilot/fallback/stop")
    def _stop():
        return JSONResponse(content=stop())
