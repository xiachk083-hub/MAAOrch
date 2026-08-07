"""FastAPI-based API server for MAAOrch."""
from __future__ import annotations
import asyncio, json, time, re, os, sys, hmac, subprocess, io, zipfile, shutil, urllib.request, uuid, mimetypes, threading, tempfile
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn


# ── Shared state ──

_OPLOG: list[dict] = []

def _log_op(action: str, detail: str = "") -> None:
    global _OPLOG
    _OPLOG.append({"ts": time.strftime("%H:%M:%S"), "action": action, "detail": detail})
    if len(_OPLOG) > 100:
        _OPLOG = _OPLOG[-100:]


def _get_web_schedule_tasks(mw: Any, include_anni: bool = True, only_anni: bool = False) -> list[str]:
    mode = mw.config.get("schedule_mode", "daily") if hasattr(mw, 'config') else "daily"
    if mode == "roguelike":
        tasks = ["StartUp", "Roguelike"]
        if only_anni or include_anni:
            tasks.append("Annihilation")
        return tasks
    if mode == "reclamation":
        tasks = ["StartUp", "Reclamation"]
        if only_anni or include_anni:
            tasks.append("Annihilation")
        return tasks
    sg = mw.config.get("smart_global", {})
    tasks = ["StartUp"]
    if only_anni:
        tasks.append("Annihilation")
        return tasks
    if sg.get("annihilation_enabled", True) and include_anni:
        tasks.append("Annihilation")
    tasks.append("Fight")
    if sg.get("recruit_enabled", True):
        tasks.append("Recruit")
    if sg.get("infrast_enabled", True):
        tasks.append("Infrast")
    if sg.get("mall_enabled", True):
        tasks.append("Mall")
    tasks.append("Award")
    return tasks


# ── Rate limiter ──

_rate_buckets: dict[str, list[float]] = {}

def _check_rate(ip: str, limit: int = 200) -> bool:
    if ip in ("127.0.0.1", "::1"):
        return True
    now = time.time()
    bucket = _rate_buckets.get(ip, [])
    bucket = [t for t in bucket if t > now - 60]
    if len(bucket) >= limit:
        _rate_buckets[ip] = bucket
        return False
    bucket.append(now)
    _rate_buckets[ip] = bucket
    return True


# ── App factory ──

def create_app(mw: Any) -> FastAPI:
    app = FastAPI(title="MAAOrch", docs_url=None, redoc_url=None)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    token = mw.config.get("api_token", "") if hasattr(mw, 'config') else ""

    @app.middleware("http")
    async def auth_rate_middleware(request: Request, call_next):
        ip = request.client.host if request.client else "127.0.0.1"
        if not _check_rate(ip):
            return JSONResponse({"error": "rate_limited"}, 429)
        # Static files and SSE don't need auth
        path = request.url.path
        if path.startswith("/api/") and path != "/api/sse" and token:
            h = request.headers.get("x-agent-token", "")
            if h and not hmac.compare_digest(h, token):
                return JSONResponse({"error": "unauthorized"}, 401)
        return await call_next(request)

    # ── Static file serving (catch-all, MUST be last route) ──

    web_dir = Path(__file__).parent.parent / "ui" / "web"

    # ── Helpers ──

    _CF = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0

    def _proc_pid(a):
        progs = [w for w in mw.warehouse if w.get("account_ref") == a["id"]]
        return progs[0]["id"] if progs else ""

    def _is_running(pid):
        return pid in mw._proc_status

    def _account_by_idx(idx):
        if idx < 0 or idx >= len(mw.accounts):
            raise HTTPException(404)
        return mw.accounts[idx]

    # ── Queue helpers ──

    def _lq():
        lq = getattr(mw, "launch_queue", None)
        if not lq:
            raise HTTPException(503, "queue not available")
        return lq

    def _runner():
        r = getattr(mw, 'runner', None)
        if not r:
            raise HTTPException(503, "runner not available")
        return r

    # ═══════════════════════════════════════════
    # GET endpoints
    # ═══════════════════════════════════════════

    @app.get("/api/status")
    def handle_status():
        accts = []
        alive = set()
        try:
            r = subprocess.run(["tasklist", "/NH", "/FI", "IMAGENAME eq MAA.exe"],
                               capture_output=True, text=True, timeout=5, creationflags=_CF)
            for line in r.stdout.splitlines():
                if "MAA.exe" in line:
                    alive.add(line.split()[1])
        except:
            pass
        for i, a in enumerate(mw.accounts):
            pid = _proc_pid(a)
            running = pid in mw._proc_status and pid in alive if pid else False
            elapsed = 0
            if running and pid in mw._proc_start_times:
                elapsed = int(time.time() - mw._proc_start_times[pid])
            accts.append({"name": a.get("name", ""), "index": i, "running": running,
                          "elapsed": elapsed, "adb": a.get("adb_address", ""),
                          "emu_index": a.get("emu_instance_index", "")})
        rc = getattr(mw, 'runner', None)
        running_count = len(rc._active) if rc else sum(1 for a in accts if a["running"])
        return {"accounts": accts, "pipeline_running": False, "running": running_count}

    @app.get("/api/node/info")
    def handle_node_info():
        import psutil as _ps
        mem = _ps.virtual_memory()
        return {
            "node_id": mw.config.get("node_id", ""),
            "node_name": mw.config.get("node_name", ""),
            "version": "1.2.0",
            "parallel_max": mw.config.get("parallel_max", 3),
            "account_count": len(mw.accounts),
            "running_count": len(getattr(mw, "_proc_status", set())),
            "cpu_count": _ps.cpu_count(),
            "memory_total_mb": mem.total // 1048576,
            "memory_available_mb": mem.available // 1048576,
        }

    @app.get("/api/node/dashboard")
    def handle_node_dashboard():
        import psutil as _ps
        try:
            mem = _ps.virtual_memory()
            cpu_pct = _ps.cpu_percent(interval=0)
            cpu_count = _ps.cpu_count()
        except:
            mem = type('m', (), {'total': 1, 'available': 1, 'percent': 0})()
            cpu_pct = 0
            cpu_count = 1
        processes = []
        runner = getattr(mw, 'runner', None)
        accounts_list = getattr(mw, 'accounts', []) or []
        proc_status = getattr(mw, "_proc_status", set())
        seen_pids = set()
        seen_aids = set()
        actual_pids = set()
        try:
            r2 = subprocess.run(["tasklist", "/NH", "/FI", "IMAGENAME eq MAA.exe"],
                                capture_output=True, text=True, timeout=5, creationflags=_CF)
            for _line in r2.stdout.splitlines():
                if "MAA.exe" not in _line:
                    continue
                _parts = _line.split()
                if len(_parts) >= 2:
                    try:
                        actual_pids.add(int(_parts[1]))
                    except:
                        pass
        except:
            pass
        if runner and hasattr(runner, '_proc_info'):
            for aid, info in list(runner._proc_info.items()):
                try:
                    ac = next((a for a in accounts_list if a.get("id") == aid), None)
                    if not ac:
                        continue
                    maa = info.get("maa", {}) or {}
                    maa_pid = maa.get("pid")
                    if aid in seen_aids:
                        continue
                    is_running = aid in proc_status
                    if not maa_pid and not is_running:
                        continue
                    if maa_pid and (maa_pid in seen_pids or maa_pid not in actual_pids):
                        continue
                    seen_aids.add(aid)
                    if maa_pid:
                        seen_pids.add(maa_pid)
                    emu = info.get("emu", {}) or {}
                    processes.append({
                        "aid": aid, "name": ac.get("name", aid),
                        "running": is_running if not maa_pid else maa_pid in actual_pids,
                        "last_task": ac.get("_last_task", ""),
                        "maa_mem_mb": maa.get("mem_mb", 0),
                        "maa_cpu_pct": maa.get("cpu_pct", 0),
                        "maa_pid": maa_pid,
                        "emu_mem_mb": emu.get("mem_mb", 0),
                        "emu_cpu_pct": emu.get("cpu_pct", 0),
                        "emu_pid": emu.get("pid"),
                        "emu_name": emu.get("name", ""),
                    })
                except:
                    continue
        lq = getattr(mw, "launch_queue", None)
        if lq and hasattr(lq, '_active_emus'):
            active_emus = getattr(lq, '_active_emus', {})
            for ac in accounts_list:
                aid = ac.get("id", "")
                if aid not in seen_aids and aid in active_emus.values():
                    processes.append({
                        "aid": aid, "name": ac.get("name", aid),
                        "running": True, "last_task": "", "maa_mem_mb": 0, "maa_cpu_pct": 0,
                        "maa_pid": None, "emu_mem_mb": 0, "emu_cpu_pct": 0,
                        "emu_pid": None, "emu_name": "",
                    })
                    seen_aids.add(aid)
        try:
            r2 = subprocess.run(["tasklist", "/NH", "/FI", "IMAGENAME eq MAA.exe"],
                                capture_output=True, text=True, timeout=5, creationflags=_CF)
            maa_count = r2.stdout.count("MAA.exe")
            if maa_count > 0 and len(processes) < maa_count:
                seen_pids = {p.get("maa_pid") for p in processes if p.get("maa_pid")}
                for pf in Path(__file__).parent.parent.glob("services/maa/instances/*/.pid"):
                    try:
                        pid_str = pf.read_text().strip()
                        if not pid_str:
                            continue
                        pid = int(pid_str)
                        if pid in seen_pids or pid not in actual_pids:
                            continue
                        meta_file = pf.parent / ".meta"
                        name = "MAA-" + pid_str[-4:]
                        meta_aid = ""
                        if meta_file.exists():
                            meta = meta_file.read_text().strip()
                            if "|" in meta:
                                meta_aid, name = meta.split("|", 1)
                                if meta_aid in seen_aids:
                                    continue
                        processes.append({
                            "aid": meta_aid, "name": name, "running": True,
                            "last_task": "", "maa_mem_mb": 0, "maa_cpu_pct": 0,
                            "maa_pid": pid, "emu_mem_mb": 0, "emu_cpu_pct": 0,
                            "emu_pid": None, "emu_name": "",
                        })
                        seen_pids.add(pid)
                        if meta_aid:
                            seen_aids.add(meta_aid)
                    except:
                        pass
        except:
            pass
        gpu = {"name": "", "usage": 0, "mem_used_mb": 0, "mem_total_mb": 0}
        try:
            o = subprocess.check_output(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                                         "--format=csv,noheader,nounits"], timeout=2, encoding="utf-8",
                                        errors="replace", creationflags=_CF)
            parts = o.strip().split(", ")
            if len(parts) >= 4:
                gpu = {"name": parts[0], "usage": float(parts[1]),
                       "mem_used_mb": int(float(parts[2])), "mem_total_mb": int(float(parts[3]))}
        except:
            pass
        seen = set()
        deduped = []
        for p in processes:
            n = p.get("name", "")
            if n and n not in seen:
                seen.add(n)
                deduped.append(p)
        processes = deduped
        total_proc_mem = sum((p.get("maa_mem_mb", 0) or 0) + (p.get("emu_mem_mb", 0) or 0) for p in processes) if processes else 0
        running = sum(1 for p in processes if p["running"])
        parallel_max = mw.config.get("parallel_max", 3)
        by_parallel = max(0, parallel_max - running)
        est_per = max(1, (total_proc_mem / max(running, 1))) if running > 0 else 1500
        by_mem = max(0, int((mem.available / 1048576) / est_per))
        gpu_free = max(0, gpu["mem_total_mb"] - gpu["mem_used_mb"])
        by_gpu = max(0, int(gpu_free / est_per)) if gpu["mem_total_mb"] > 0 else 99
        capacity = min(by_parallel, by_mem, by_gpu)
        limit_by = "并行上限" if capacity == by_parallel else ("内存" if capacity == by_mem else "显存")
        samples = getattr(mw, "_res_samples", [])
        gantt = getattr(mw, "_gantt_events", [])
        return {
            "ok": True,
            "system": {"cpu_pct": cpu_pct, "cpu_count": cpu_count,
                       "memory_total_mb": mem.total // 1048576,
                       "memory_available_mb": mem.available // 1048576,
                       "memory_pct": mem.percent},
            "gpu": gpu, "processes": processes,
            "capacity": {"parallel_max": parallel_max, "running": running,
                         "by_parallel": by_parallel, "by_memory": by_mem, "by_gpu": by_gpu,
                         "max": capacity, "limit_by": limit_by,
                         "est_per_instance_mb": int(est_per),
                         "deficit": mw.config.get("deficit", 0),
                         "stuck_timeout": mw.config.get("stuck_timeout", 10)},
            "samples": samples[-360:] if samples else [],
            "gantt": gantt[-100:] if gantt else [],
        }

    @app.get("/api/account/{idx}/status")
    def handle_account_status(idx: int):
        a = _account_by_idx(idx)
        pid = _proc_pid(a)
        running = _is_running(pid)
        elapsed = 0
        if running and pid in mw._proc_start_times:
            elapsed = int(time.time() - mw._proc_start_times[pid])
        return {"name": a.get("name", ""), "running": running, "elapsed": elapsed}

    @app.get("/api/account/{idx}/stats")
    def handle_account_stats(idx: int):
        from models.stats import RunStats
        a = _account_by_idx(idx)
        st = RunStats(a["id"])
        pid = _proc_pid(a)
        running = _is_running(pid)
        return {"account_name": a.get("name", ""), "running": running, "stats": st._data}

    @app.get("/api/account/{idx}/screenshot")
    def handle_account_screenshot(idx: int):
        a = _account_by_idx(idx)
        addr = a.get("adb_address", "")
        adb = a.get("adb_path", "")
        if not addr and a.get("emu_instance_index"):
            try:
                port = 16384 + int(a["emu_instance_index"]) * 32
                addr = f"127.0.0.1:{port}"
            except:
                pass
        if not addr:
            raise HTTPException(400, "no adb address")
        if not adb:
            from infrastructure.task_constants import find_mumu_cli
            cli = find_mumu_cli()
            if cli:
                cand = str(Path(cli).parent / "adb.exe")
                if Path(cand).exists():
                    adb = cand
            if not adb:
                adb = "adb"
        subprocess.run([adb, "-s", addr, "shell", "input", "keyevent", "KEYCODE_WAKEUP"],
                       capture_output=True, timeout=5, creationflags=_CF)
        subprocess.run([adb, "-s", addr, "shell", "input", "keyevent", "KEYCODE_MENU"],
                       capture_output=True, timeout=5, creationflags=_CF)
        r = subprocess.run([adb, "-s", addr, "exec-out", "screencap", "-p"],
                           capture_output=True, timeout=15, creationflags=_CF)
        if r.returncode != 0 or len(r.stdout) < 100:
            raise HTTPException(500, "screencap failed")
        return StreamingResponse(io.BytesIO(r.stdout), media_type="image/png",
                                 headers={"Cache-Control": "no-cache"})

    @app.get("/api/screenshots/{aid}")
    def handle_screenshots_list(aid: str):
        _proj = Path(__file__).parent.parent
        shot_root = _proj / "screenshots" / aid
        runs = []
        if shot_root.exists():
            dirs = sorted(shot_root.glob("run_*"), key=lambda d: d.stat().st_mtime, reverse=True)[:20]
            for d in dirs:
                shots = sorted(d.glob("*.png"), key=lambda f: f.stat().st_mtime)
                runs.append({"dir": d.name, "ts": d.stat().st_mtime,
                             "shots": [{"file": f.name, "ts": f.stat().st_mtime} for f in shots]})
        a = next((x for x in mw.accounts if x["id"] == aid), None)
        return {"ok": True, "aid": aid, "name": a.get("name", "") if a else "", "runs": runs}

    @app.get("/api/screenshots/file/{aid}/{run_dir}/{fname:path}")
    def handle_screenshot_file(aid: str, run_dir: str, fname: str):
        fp = Path(__file__).parent.parent / "screenshots" / aid / run_dir / fname
        if not fp.exists():
            raise HTTPException(404)
        return FileResponse(str(fp), media_type="image/png")

    @app.get("/api/screenshots/{aid}/export/{run_ts}")
    def handle_screenshot_export(aid: str, run_ts: str):
        ss_dir = Path(__file__).parent.parent / "screenshots" / aid
        run_dir = ss_dir / run_ts if run_ts else None
        if not run_dir or not run_dir.exists():
            raise HTTPException(404)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(run_dir.rglob("*")):
                if f.is_file():
                    zf.write(str(f), str(f.relative_to(run_dir.parent)))
        return StreamingResponse(io.BytesIO(buf.getvalue()), media_type="application/zip",
                                 headers={"Content-Disposition": f'attachment; filename="{aid}_{run_ts}.zip"'})

    @app.get("/api/export/logs")
    def handle_export_logs():
        _proj = Path(__file__).parent.parent
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            dl = _proj / "debug.log"
            if dl.exists():
                zf.write(str(dl), "debug.log")
            cf = _proj / "models" / "config.json"
            if cf.exists():
                zf.write(str(cf), "config.json")
            ss = _proj / "screenshots"
            if ss.exists():
                for f in ss.rglob("*"):
                    if f.is_file():
                        zf.write(str(f), f"screenshots/{f.relative_to(ss)}")
            inst = _proj / "services" / "maa" / "instances"
            if inst.exists():
                for d in inst.iterdir():
                    al = d / "debug" / "asst.log"
                    if al.exists():
                        zf.write(str(al), f"maa_instances/{d.name}/asst.log")
                    idir = d / "debug" / "interface"
                    if idir.exists():
                        for f in idir.glob("*.png"):
                            zf.write(str(f), f"maa_instances/{d.name}/interface/{f.name}")
        return StreamingResponse(io.BytesIO(buf.getvalue()), media_type="application/zip",
                                 headers={"Content-Disposition": 'attachment; filename="maorch_logs.zip"'})

    @app.get("/api/export/gantt")
    def handle_export_gantt():
        gantt = getattr(mw, '_gantt_events', [])
        if not gantt:
            return {"ok": True, "html": "<html><body><p>暂无编年史数据</p></body></html>"}
        rows = ""
        for e in gantt[-200:]:
            ts_str = datetime.fromtimestamp(e.get("ts", 0)).strftime("%Y-%m-%d %H:%M") if e.get("ts") else ""
            name = e.get("name", "?")
            evt = e.get("event", "")
            color = {"start": "#4caf50", "stop": "#f44336", "task": "#2196f3"}.get(evt, "#999")
            rows += f"<tr><td>{ts_str}</td><td>{name}</td><td style='color:{color}'>{evt}</td></tr>"
        html = f"""<!DOCTYPE html><html lang=zh-cn><meta charset=utf-8><title>编年史导出</title>
<style>body{{font-family:sans-serif;background:#1e1e1e;color:#ccc;padding:20px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{padding:6px 8px;text-align:left;border-bottom:1px solid #333}}
th{{color:#888;font-weight:normal}}tr:hover{{background:#2a2a2a}}</style>
<h2 style=color:#498205>📜 编年史</h2><p style=color:#888;font-size:11px>导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 共 {len(gantt)} 条</p>
<table><tr><th>时间</th><th>账号</th><th>事件</th></tr>{rows}</table></html>"""
        return HTMLResponse(html, headers={"Content-Disposition": 'attachment; filename="gantt_export.html"'})

    @app.get("/api/stats/dashboard")
    def handle_stats_dashboard():
        from models.stats import RunStats
        today = datetime.now()
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        heatmap = [[0] * 24 for _ in range(7)]
        daily_runs = {}
        daily_drops = {}
        all_drops = {}
        total_runs = 0
        for a in mw.accounts:
            st = RunStats(a["id"])
            for run in st._data.get("runs", []):
                total_runs += 1
                try:
                    dt = datetime.strptime(run["ts"], "%Y-%m-%d %H:%M:%S")
                except:
                    continue
                dow = dt.weekday()
                hr = dt.hour
                heatmap[dow][hr] += 1
                date_key = dt.strftime("%Y-%m-%d")
                daily_runs[date_key] = daily_runs.get(date_key, 0) + 1
                for item, qty in run.get("drops", {}).items():
                    all_drops[item] = all_drops.get(item, 0) + qty
                    if date_key not in daily_drops:
                        daily_drops[date_key] = {}
                    daily_drops[date_key][item] = daily_drops[date_key].get(item, 0) + qty
        top_mats = sorted(all_drops, key=all_drops.get, reverse=True)[:10]
        today_key = today.strftime("%Y-%m-%d")
        return {
            "ok": True,
            "summary": {"total_runs": total_runs, "today_runs": daily_runs.get(today_key, 0),
                        "accounts": len(mw.accounts), "total_drops": sum(all_drops.values())},
            "heatmap": heatmap, "weekdays": weekdays,
            "daily_runs": daily_runs, "daily_drops": daily_drops,
            "top_materials": top_mats,
        }

    @app.get("/api/stats")
    def handle_all_stats():
        from models.stats import RunStats
        result = []
        for i, a in enumerate(mw.accounts):
            st = RunStats(a["id"])
            pid = _proc_pid(a)
            running = _is_running(pid)
            result.append({"index": i, "account_name": a.get("name", ""),
                           "running": running, "total_runs": st.total_runs, "stats": st._data})
        return {"accounts": result}

    @app.get("/api/queue")
    def handle_queue_status():
        lq = _lq()
        src_map = {"manual": "手动", "schedule": "定时", "sanity": "理智"}
        pending = []
        for e in sorted(lq._pending, key=lambda x: x.sort_key):
            a = next((x for x in mw.accounts if x["id"] == e.account_id), None)
            pending.append({
                "account_id": e.account_id,
                "account_name": a.get("name", "") if a else "",
                "source": src_map.get(e.source, e.source),
                "priority": e.sort_key[0],
                "not_before": e.not_before.strftime("%Y-%m-%d %H:%M:%S"),
                "suspended": a.get("suspended", False) if a else False,
            })
        active = list(lq._active_emus.values())
        return {"pending": pending, "active": active,
                "pending_count": len(pending), "active_count": len(active),
                "paused": lq._paused}

    @app.get("/api/logs")
    def handle_logs(lines: int = 50):
        lp = Path(__file__).parent.parent / "debug.log"
        if lp.exists():
            try:
                content = lp.read_text(encoding="utf-8", errors="replace").strip().split("\n")[-lines:]
                return {"lines": content}
            except:
                raise HTTPException(500, "read failed")
        return {"lines": []}

    @app.get("/api/maa/log")
    def handle_maa_log(aid: str = "", lines: int = 100):
        if not aid:
            raise HTTPException(400, "missing aid")
        a = next((x for x in mw.accounts if x["id"] == aid), None)
        if not a:
            raise HTTPException(404, "account not found")
        for w in mw.warehouse:
            if w.get("account_ref") == aid:
                lp = Path(w.get("path", "")).parent / "debug" / "asst.log"
                if lp.exists():
                    try:
                        content = lp.read_text(encoding="utf-8", errors="replace").strip().split("\n")[-lines:]
                        return {"lines": content, "name": a.get("name", "")}
                    except:
                        raise HTTPException(500, "read failed")
                return {"lines": [], "name": a.get("name", "")}
        # Fallback: search instance directories via .meta file
        _inst_root = Path(__file__).parent.parent / "services" / "maa" / "instances"
        for _inst_dir in _inst_root.glob("*/"):
            _meta = _inst_dir / ".meta"
            if _meta.exists():
                _meta_content = _meta.read_text(encoding="utf-8", errors="replace").strip()
                if "|" in _meta_content and _meta_content.split("|", 1)[0] == aid:
                    _lp = _inst_dir / "debug" / "asst.log"
                    if _lp.exists():
                        try:
                            _content = _lp.read_text(encoding="utf-8", errors="replace").strip().split("\n")[-lines:]
                            return {"lines": _content, "name": a.get("name", "")}
                        except:
                            raise HTTPException(500, "read failed")
                    return {"lines": [], "name": a.get("name", "")}
        return {"lines": [], "name": a.get("name", ""), "error": "no .meta file found, launch MAA first"}

    @app.get("/api/accounts")
    def handle_accounts():
        data = []
        for a in mw.accounts:
            if a.get("_connect_only"):
                continue
            aid = a.get("id", "")
            lq = getattr(mw, 'launch_queue', None)
            running = lq.is_running(aid) if lq else False
            queued = lq.is_queued(aid) if lq else False
            data.append({
                "id": aid, "name": a.get("name", ""),
                "game_client": a.get("game_client", ""),
                "emu_instance_index": a.get("emu_instance_index", ""),
                "account_switch": a.get("account_switch", ""),
                "uid": a.get("uid", ""),
                "running": running, "queued": queued,
                "failures": a.get("consecutive_failures", 0),
                "suspended": a.get("suspended", False),
                "stages": a.get("stages", []),
                "smart_annihilation": a.get("smart_annihilation", ""),
                "fight_mode": a.get("fight_mode", "schedule"),
                "fight_default": a.get("fight_default", "1-7"),
                "schedule_weekly": a.get("schedule_weekly", {}),
                "schedule_monthly": a.get("schedule_monthly", {}),
                "fight_priority": a.get("fight_priority", {}),
                "fight_materials": a.get("fight_materials", []),
            })
        return {"ok": True, "accounts": data}

    @app.get("/api/accounts/export")
    def handle_accounts_export():
        """Export accounts as CSV (includes stage columns)."""
        import io, csv
        output = io.StringIO()
        _stage_lib = mw.config.get("stage_library", [])
        _stage_ids = [s.get("id", "") for s in _stage_lib if s.get("id")]
        _cn_headers = ["id","名称","游戏客户端","模拟器索引","切换标识","UID","备注","过期日","已暂停"] + _stage_ids
        _field_map = {"id":"id","名称":"name","游戏客户端":"game_client","模拟器索引":"emu_instance_index","切换标识":"account_switch","UID":"uid","备注":"note","过期日":"expire_date","已暂停":"suspended"}
        w = csv.writer(output)
        w.writerow(_cn_headers)
        for a in mw.accounts:
            _ac_stages = set(a.get("stages", []) or [])
            _row = [a.get(_field_map[h], "") for h in _cn_headers[:9]]
            _row += ["1" if s in _ac_stages else "0" for s in _stage_ids]
            w.writerow(_row)
        from datetime import datetime as _dt
        _ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        return StreamingResponse(io.BytesIO(output.getvalue().encode("utf-8-sig")),
                                 media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": f"attachment; filename=accounts_{_ts}.csv"})

    @app.post("/api/accounts/csv_import")
    def handle_accounts_csv_import(body: dict):
        """Import accounts from CSV text. Updates existing by id, creates new."""
        import csv, io
        csv_text = body.get("csv", "")
        if not csv_text:
            raise HTTPException(400, "missing csv")
        _header_map = {
            "id": "id", "名称": "name", "name": "name",
            "游戏客户端": "game_client", "game_client": "game_client",
            "模拟器索引": "emu_instance_index", "emu_instance_index": "emu_instance_index",
            "切换标识": "account_switch", "account_switch": "account_switch",
            "UID": "uid", "uid": "uid",
            "备注": "note", "note": "note",
            "过期日": "expire_date", "expire_date": "expire_date",
            "已暂停": "suspended", "suspended": "suspended",
        }
        reader = csv.DictReader(io.StringIO(csv_text))
        if not reader.fieldnames:
            raise HTTPException(400, "empty or invalid csv")
        updated = 0
        created = 0
        errors = []
        for i, row in enumerate(reader, 2):
            try:
                # Map Chinese headers to English field names
                mapped = {}
                for k, v in row.items():
                    fname = _header_map.get(k.strip(), k.strip())
                    mapped[fname] = v.strip() if v else ""
                aid = mapped.get("id", "").strip()
                if not aid:
                    aid = uuid.uuid4().hex[:12]
                existing = next((a for a in mw.accounts if a["id"] == aid), None)
                if existing:
                    for f in ("name","game_client","emu_instance_index","account_switch","uid","note","expire_date"):
                        if f in mapped:
                            existing[f] = mapped[f]
                    if "suspended" in mapped:
                        existing["suspended"] = mapped["suspended"].lower() in ("true","1","yes")
                    updated += 1
                else:
                    new = {"id": aid, "consecutive_failures": 0}
                    for f in ("name","game_client","emu_instance_index","account_switch","uid","note","expire_date"):
                        new[f] = mapped.get(f, "")
                    new["suspended"] = mapped.get("suspended","").lower() in ("true","1","yes")
                    mw.accounts.append(new)
                    created += 1
            except Exception as e:
                errors.append(f"行{i}: {e}")
        mw.config["accounts"] = mw.accounts
        try:
            from models.config_manager import save_config
            save_config(mw.config)
        except Exception:
            pass
        return {"ok": True, "updated": updated, "created": created, "errors": errors}

    @app.post("/api/accounts/batch_save")
    def handle_accounts_batch_save(body: dict):
        """Save all accounts from table edit. Body: { accounts: [...], new_stages: [str] }"""
        _stage_lib = mw.config.setdefault("stage_library", [])
        new_stages = body.get("new_stages", [])
        for sid in new_stages:
            if sid and not any(s.get("id") == sid for s in _stage_lib):
                _stage_lib.append({"id": sid, "name": sid, "count": 0})
        updated = 0
        created = 0
        for ac in body.get("accounts", []):
            aid = ac.get("id", "").strip()
            if not aid:
                aid = uuid.uuid4().hex[:12]
            existing = next((a for a in mw.accounts if a["id"] == aid), None)
            if existing:
                for f in ("name", "game_client", "emu_instance_index", "account_switch", "uid", "note", "expire_date", "smart_annihilation", "suspended"):
                    if f in ac:
                        existing[f] = ac[f]
                if "stages" in ac:
                    existing["stages"] = ac["stages"] if isinstance(ac["stages"], list) else []
                updated += 1
            else:
                new_ac = {"id": aid, "consecutive_failures": 0}
                for f in ("name", "game_client", "emu_instance_index", "account_switch", "uid", "note", "expire_date", "smart_annihilation", "suspended", "stages"):
                    if f in ac:
                        new_ac[f] = ac[f]
                mw.accounts.append(new_ac)
                created += 1
        mw.config["accounts"] = mw.accounts
        _cfg_err = ""
        try:
            from models.config_manager import save_config
            save_config(mw.config)
        except Exception as _e:
            _cfg_err = str(_e)
            mw._log(f"[保存] 写入 config.json 失败: {_e}")
        return {"ok": True, "updated": updated, "created": created, "config_error": _cfg_err}

    @app.get("/api/oplog")
    def handle_oplog():
        return {"ok": True, "ops": _OPLOG[-50:]}

    @app.get("/api/config")
    def handle_get_config():
        cfg = mw.config
        return {"ok": True, "config": {
            "maa_version": cfg.get("maa_version", ""),
            "parallel_max": cfg.get("parallel_max", 3),
            "appearance_mode": cfg.get("appearance_mode", "Dark"),
            "schedule_mode": cfg.get("schedule_mode", "daily"),
            "maa_instances": cfg.get("maa_instances", 0),
            "api_port": cfg.get("api_port", 19999),
            "smart_global": cfg.get("smart_global", {}),
            "webhook_url": cfg.get("webhook_url", ""),
            "bind_address": cfg.get("bind_address", "0.0.0.0"),
            "api_token": cfg.get("api_token", ""),
            "ai_provider": cfg.get("ai_provider", "openai"),
            "ai_api_key": cfg.get("ai_api_key", ""),
            "ai_endpoint": cfg.get("ai_endpoint", ""),
            "ai_model": cfg.get("ai_model", ""),
            "ai_auto_analyze": cfg.get("ai_auto_analyze", False),
            "tg_token": cfg.get("tg_token", ""),
            "tg_chat_id": cfg.get("tg_chat_id", ""),
        }}

    @app.get("/api/settings/smart")
    def handle_get_smart():
        return {"ok": True, "smart_global": mw.config.get("smart_global", {})}

    @app.get("/api/emulators")
    def handle_emulators():
        from infrastructure.task_constants import detect_emu_instances
        return {"ok": True, "emulators": detect_emu_instances()}

    @app.get("/api/emulator/{idx}")
    def handle_emulator_status(idx: str):
        from infrastructure.task_constants import detect_emu_instances
        for e in detect_emu_instances():
            if str(e.get("index")) == idx:
                return {"ok": True, "emulator": e}
        raise HTTPException(404)

    @app.get("/api/account/{idx}/config")
    def handle_account_config(idx: int):
        a = _account_by_idx(idx)
        return {"ok": True, "account_id": a["id"], "task_settings": a.get("task_settings", {})}

    @app.get("/api/stages")
    def handle_get_stages():
        lib = mw.config.get("stage_library", [])
        for st in lib:
            sid = st.get("id", "")
            st["count"] = sum(1 for a in mw.accounts if sid in a.get("stages", []))
            st["account_ids"] = [a.get("id", "") for a in mw.accounts if sid in a.get("stages", [])]
        return {"ok": True, "stages": lib}

    # ═══════════════════════════════════════════
    # SSE endpoint (streaming)
    # ═══════════════════════════════════════════

    @app.get("/api/sse")
    async def handle_sse(request: Request):
        async def event_stream():
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = _gather_sse_state()
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                except:
                    yield f"data: {json.dumps({'ok': False})}\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(event_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

    def _gather_sse_state() -> dict:
        try:
            accts = []
            for a in mw.accounts:
                aid = a.get("id", "")
                lq = getattr(mw, 'launch_queue', None)
                running = lq.is_running(aid) if lq else False
                queued = lq.is_queued(aid) if lq else False
                accts.append({
                    "id": aid, "name": a.get("name", ""),
                    "running": running, "queued": queued,
                    "emu": a.get("emu_instance_index", ""),
                    "client": a.get("game_client", ""),
                    "failures": a.get("consecutive_failures", 0),
                    "suspended": a.get("suspended", False),
                    "stages": a.get("stages", []),
                    "smart_annihilation": a.get("smart_annihilation", ""),
                })
            q = {"count": mw.launch_queue.pending_count if hasattr(mw, 'launch_queue') and mw.launch_queue else 0}
            notifs = getattr(mw, '_notifications', [])[-5:]
            insights = getattr(mw, '_ai_insights', [])[-3:]
            return {"ok": True, "accounts": accts, "queue": q, "notifications": notifs, "ai_insights": insights}
        except:
            return {"ok": False}

    # ═══════════════════════════════════════════
    # POST endpoints
    # ═══════════════════════════════════════════

    @app.post("/api/sse")
    async def handle_sse_post(request: Request):
        return await handle_sse(request)

    @app.post("/api/node/register")
    def handle_node_register(body: dict):
        daigan_url = body.get("daigan_url", "")
        if daigan_url and daigan_url.startswith("https://"):
            mw.config["daigan_url"] = daigan_url
            from models.config_manager import save_config
            save_config(mw.config)
            mw._log(f"节点已注册到: {daigan_url}")
        return {"node_id": mw.config.get("node_id", ""), "status": "registered"}

    @app.post("/api/node/heartbeat")
    def handle_node_heartbeat(body: dict):
        import psutil as _ps
        lq = getattr(mw, "launch_queue", None)
        return {
            "node_id": mw.config.get("node_id", ""), "status": "ok",
            "ts": time.time(),
            "running": len(getattr(mw, "_proc_status", set())),
            "queued": lq.pending_count if lq else 0,
            "mem_avail_mb": _ps.virtual_memory().available // 1048576,
        }

    @app.post("/api/account/{idx}/launch")
    def handle_account_launch(idx: int):
        a = _account_by_idx(idx)
        runner = _runner()
        ok = runner.launch(idx)
        if ok:
            _log_op("启动", a.get("name", ""))
        return {"ok": ok}

    @app.post("/api/account/{idx}/stop")
    def handle_account_stop(idx: int):
        a = _account_by_idx(idx)
        runner = _runner()
        runner.stop(a["id"])
        _log_op("停止", a.get("name", ""))
        return {"ok": True}

    @app.post("/api/config/sync")
    def handle_config_sync(body: dict):
        acct_name = body.get("account_name", "")
        gui_json = body.get("gui_json")
        if not acct_name or not isinstance(gui_json, dict):
            raise HTTPException(400, "invalid fields")
        a = next((a for a in mw.accounts if a.get("name") == acct_name), None)
        if not a:
            raise HTTPException(404, "account not found")
        progs = [w for w in mw.warehouse if w.get("account_ref") == a["id"]]
        if not progs:
            raise HTTPException(400, "no MAA bound")
        inst_path = Path(progs[0]["path"]).resolve()
        parts = inst_path.parts
        if not any(parts[i:i+2] == ("maa", "instances") for i in range(len(parts)-1)):
            raise HTTPException(403, "invalid path")
        d = inst_path.parent / "config"
        d.mkdir(parents=True, exist_ok=True)
        (d / "gui.json").write_text(json.dumps(gui_json, ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "gui.new.json").write_text(json.dumps(gui_json, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True}

    @app.post("/api/queue/enqueue")
    def handle_queue_enqueue(body: dict):
        lq = _lq()
        idx = body.get("account_index", -1)
        if isinstance(idx, str):
            try:
                idx = int(idx)
            except:
                idx = -1
        a = _account_by_idx(idx)
        source = body.get("source", "manual")
        priority = body.get("priority", 0)
        not_before_str = body.get("not_before", "")
        not_before = None
        if not_before_str:
            try:
                not_before = datetime.strptime(not_before_str, "%Y-%m-%d %H:%M:%S")
            except:
                pass
        lq.enqueue(a["id"], source, priority, not_before)
        _log_op("入队", a.get("name", ""))
        return {"ok": True, "account_id": a["id"], "pending_count": lq.pending_count}

    @app.post("/api/queue/dequeue")
    def handle_queue_dequeue(body: dict):
        lq = _lq()
        aid = body.get("account_id", "")
        if not aid:
            idx = body.get("account_index", -1)
            if isinstance(idx, str):
                try:
                    idx = int(idx)
                except:
                    idx = -1
            if 0 <= idx < len(mw.accounts):
                aid = mw.accounts[idx]["id"]
        if not aid:
            raise HTTPException(400, "invalid account")
        lq.dequeue(aid)
        ac = next((x for x in mw.accounts if x["id"] == aid), None)
        _log_op("出队", ac.get("name", "") if ac else aid)
        return {"ok": True, "account_id": aid, "pending_count": lq.pending_count}

    @app.post("/api/queue/clear")
    def handle_queue_clear():
        lq = _lq()
        lq._pending.clear()
        lq._save_queue()
        return {"ok": True}

    @app.post("/api/queue/pause")
    def handle_queue_pause():
        lq = _lq()
        lq.pause()
        return {"ok": True, "paused": True}

    @app.post("/api/queue/resume")
    def handle_queue_resume():
        lq = _lq()
        lq.resume()
        return {"ok": True, "paused": False}

    @app.post("/api/account")
    def handle_create_account(body: dict):
        new_id = uuid.uuid4().hex[:12]
        acct = {
            "id": new_id, "name": body.get("name", ""),
            "game_client": body.get("game_client", ""),
            "emu_instance_index": body.get("emu_instance_index", ""),
            "account_switch": body.get("account_switch", ""),
            "uid": body.get("uid", ""),
            "note": body.get("note", ""),
            "expire_date": body.get("expire_date", ""),
            "consecutive_failures": 0,
        }
        mw.accounts.append(acct)
        mw.config["accounts"] = mw.accounts
        from models.config_manager import save_config
        save_config(mw.config)
        return {"ok": True, "id": new_id}

    @app.post("/api/account/{idx}/edit")
    def handle_edit_account(idx: int, body: dict):
        a = _account_by_idx(idx)
        for field in ("name", "game_client", "emu_instance_index", "account_switch",
                      "uid", "suspended", "note", "expire_date", "stages"):
            if field in body:
                a[field] = body[field]
        mw.config["accounts"] = mw.accounts
        from models.config_manager import save_config
        save_config(mw.config)
        return {"ok": True}

    @app.post("/api/account/{idx}/delete")
    def handle_delete_account(idx: int):
        a = _account_by_idx(idx)
        del mw.accounts[idx]
        mw.config["accounts"] = mw.accounts
        from models.config_manager import save_config
        save_config(mw.config)
        return {"ok": True}

    @app.post("/api/account/{idx}/config")
    def handle_save_account_config(idx: int, body: dict):
        a = _account_by_idx(idx)
        a["task_settings"] = body.get("task_settings", {})
        mw.config["accounts"] = mw.accounts
        from models.config_manager import save_config
        save_config(mw.config)
        return {"ok": True}

    @app.get("/api/account/{idx}/fight_config")
    def handle_get_fight_config(idx: int):
        a = _account_by_idx(idx)
        return {
            "ok": True,
            "fight_mode": a.get("fight_mode", "schedule"),
            "fight_default": a.get("fight_default", "1-7"),
            "schedule_weekly": a.get("schedule_weekly", {}),
            "schedule_monthly": a.get("schedule_monthly", {}),
            "fight_priority": a.get("fight_priority", {}),
            "fight_materials": a.get("fight_materials", []),
        }

    @app.post("/api/account/{idx}/fight_config")
    def handle_save_fight_config(idx: int, body: dict):
        a = _account_by_idx(idx)
        for f in ("fight_mode", "fight_default", "schedule_weekly", "schedule_monthly", "fight_priority", "fight_materials"):
            if f in body:
                a[f] = body[f]
        mw.config["accounts"] = mw.accounts
        from models.config_manager import save_config
        save_config(mw.config)
        return {"ok": True}

    @app.post("/api/screenshots/{aid}/delete")
    def handle_screenshots_delete(aid: str, body: dict):
        run_dir = body.get("run_dir", "")
        if not aid or not run_dir:
            raise HTTPException(400, "missing aid or run_dir")
        fp = Path(__file__).parent.parent / "screenshots" / aid / run_dir
        if not fp.exists():
            raise HTTPException(404)
        shutil.rmtree(str(fp))
        return {"ok": True}

    @app.post("/api/config")
    def handle_save_config(body: dict):
        for field in ("maa_version", "maa_instances", "parallel_max", "appearance_mode",
                      "schedule_mode", "deficit", "stuck_timeout", "webhook_url",
                      "bind_address", "api_token", "ai_provider", "ai_api_key",
                      "ai_endpoint", "ai_model", "ai_auto_analyze", "tg_token", "tg_chat_id"):
            if field in body:
                mw.config[field] = body[field]
        if "smart_global" in body:
            mw.config["smart_global"] = body["smart_global"]
        from models.config_manager import save_config
        save_config(mw.config)
        # Rebuild instances if parallel_max changed
        if "parallel_max" in body:
            from services.instance_pool import ensure_maa_instances_async
            threading.Thread(target=lambda: ensure_maa_instances_async(mw.ctx, True), daemon=True).start()
        return {"ok": True}

    @app.post("/api/settings/smart")
    def handle_save_smart(body: dict):
        sg = mw.config.setdefault("smart_global", {})
        sg.update(body)
        from models.config_manager import save_config
        save_config(mw.config)
        return {"ok": True}

    @app.post("/api/ai/analyze")
    def handle_ai_analyze(body: dict):
        from services.ai_assistant import analyze_failure
        aid = body.get("aid", "")
        if not aid:
            raise HTTPException(400, "missing aid")
        acct = next((a for a in mw.accounts if a.get("id") == aid), None)
        if not acct:
            raise HTTPException(404, "account not found")
        result = analyze_failure(
            aid=aid,
            name=acct.get("name", aid),
            exit_code=body.get("exit_code", -11),
            failed_tasks=body.get("failed_tasks", []),
            consecutive_failures=acct.get("consecutive_failures", 0),
            game_client=acct.get("game_client", ""),
            warehouse=mw.warehouse,
            config=mw.config,
        )
        if result:
            if not hasattr(mw, '_ai_insights'):
                mw._ai_insights = []
            mw._ai_insights.append({"aid": aid, "ts": time.time(), **result})
            if len(mw._ai_insights) > 20:
                mw._ai_insights = mw._ai_insights[-20:]
        return {"ok": True, "result": result}

    @app.post("/api/stages")
    def handle_save_stages(body: dict):
        mw.config["stage_library"] = body.get("stages", [])
        from models.config_manager import save_config
        save_config(mw.config)
        return {"ok": True}

    @app.post("/api/stages/apply")
    def handle_stages_apply(body: dict):
        stage_id = body.get("stage_id", "")
        account_ids = body.get("account_ids", [])
        toggle = body.get("toggle", True)
        if not stage_id or not account_ids:
            raise HTTPException(400, "missing stage_id or account_ids")
        for a in mw.accounts:
            if a.get("id") in account_ids:
                stages = a.setdefault("stages", [])
                if toggle:
                    if stage_id not in stages:
                        stages.append(stage_id)
                else:
                    if stage_id in stages:
                        stages.remove(stage_id)
        mw.config["accounts"] = mw.accounts
        from models.config_manager import save_config
        save_config(mw.config)
        return {"ok": True}

    @app.post("/api/action/stop_all")
    def handle_stop_all():
        lq = _lq()
        count = lq.stop_all()
        return {"ok": True, "stopped": count}

    def _stop_maa_for_emu(emu_idx) -> None:
        """Stop any MAA connected to this emulator before shutting it down.
        Otherwise MAA's ADB keepalive (RetryOnDisconnected) keeps reconnecting
        during shutdown, corrupting the emulator's state."""
        runner = _runner()
        if not runner:
            return
        for aid, ac in list(runner._active.items()):
            if str(ac.get("emu_instance_index", "")) == str(emu_idx):
                runner.stop(aid)

    def _wait_emu_stopped(cli: str, idx: str, timeout: int = 30) -> bool:
        """Poll mumu-cli info until the emulator fully exits (both process and android off)."""
        for _ in range(timeout):
            try:
                r = subprocess.run([cli, "info", "--vmindex", str(idx)],
                                   capture_output=True, text=True, timeout=5,
                                   creationflags=_CF, encoding="utf-8", errors="replace")
                if r.returncode == 0:
                    d = json.loads(r.stdout)
                    if not d.get("is_process_started") and not d.get("is_android_started"):
                        return True
            except Exception:
                pass
            time.sleep(1)
        return False

    @app.post("/api/emulator/{idx}/{action}")
    def handle_emulator_control(idx: str, action: str):
        from infrastructure.task_constants import find_mumu_cli
        cli = find_mumu_cli()
        if not cli:
            raise HTTPException(500, "mumu-cli not found")
        if action == "start":
            subprocess.run([cli, "control", "--vmindex", idx, "launch"], timeout=30, creationflags=_CF)
            return {"ok": True, "action": "started"}
        elif action == "stop":
            _stop_maa_for_emu(idx)
            time.sleep(2)  # let MAA exit and release ADB before shutting down
            subprocess.run([cli, "control", "--vmindex", idx, "shutdown"], timeout=15, creationflags=_CF)
            return {"ok": True, "action": "stopped"}
        elif action == "restart":
            _stop_maa_for_emu(idx)
            time.sleep(2)
            subprocess.run([cli, "control", "--vmindex", idx, "shutdown"], timeout=15, creationflags=_CF)
            _wait_emu_stopped(cli, idx)  # wait for full exit instead of fixed 3s
            time.sleep(2)
            subprocess.run([cli, "control", "--vmindex", idx, "launch"], timeout=30, creationflags=_CF)
            return {"ok": True, "action": "restarted"}
        raise HTTPException(400, "bad action")

    @app.post("/api/system/close_popups")
    def handle_close_popups():
        from services.runner import _close_mumu_popups
        _close_mumu_popups()
        return {"ok": True, "message": "弹窗已关闭"}

    @app.post("/api/system/open_folder")
    def handle_open_folder(body: dict = {}):
        target = body.get("path", "")
        if not target:
            return {"ok": False, "error": "missing path"}
        from pathlib import Path as _P
        root = _P(__file__).parent.parent.resolve()
        # Treat leading "/" as project-root relative (Windows-safe)
        if target.startswith("/"):
            target = target.lstrip("/")
            p = (root / target).resolve()
        else:
            p = _P(target).resolve()
        # Path safety: only allow folders under the project root
        if not str(p).startswith(str(root)):
            return {"ok": False, "error": "path outside project root"}
        p.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(p))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/api/system/kill_maa")
    def handle_kill_maa():
        r = subprocess.run(["tasklist", "/NH", "/FI", "IMAGENAME eq MAA.exe"],
                           capture_output=True, text=True, timeout=5, creationflags=_CF)
        killed = 0
        for line in r.stdout.splitlines():
            if "MAA.exe" not in line:
                continue
            p = line.split()
            if len(p) >= 2:
                subprocess.run(["taskkill", "/F", "/PID", p[1]],
                               capture_output=True, timeout=3, creationflags=_CF)
                killed += 1
        for img in ["MAA.Updater.exe", "maa-cli.exe"]:
            subprocess.run(["taskkill", "/F", "/IM", img],
                           capture_output=True, timeout=3, creationflags=_CF)
        return {"ok": True, "killed": killed}

    @app.post("/api/system/restart")
    def handle_system_restart():
        def _do_restart():
            time.sleep(2)
            subprocess.Popen([sys.executable, "-u", "main_web.pyw", "--no-elevate"],
                             cwd=str(Path(__file__).parent.parent), creationflags=subprocess.CREATE_NO_WINDOW)
            time.sleep(1)
            os._exit(0)
        threading.Thread(target=_do_restart, daemon=True).start()
        return {"ok": True, "message": "重启中..."}

    def _dispatch_slot(ac, tasks, slot: str) -> None:
        """Create a dispatch for the given slot and store on the account."""
        from services.dispatch_pool import create_dispatch
        key = f"_dispatch_{slot}" if slot else "dispatch_id"
        ac[key] = create_dispatch(tasks)

    _MATERIAL_STAGES = {
        "固源岩": "1-7", "装置": "S3-4", "聚酸酯": "S3-3", "酯": "S3-1",
        "异铁": "S3-2", "酮凝集": "S3-5", "凝胶": "S3-5", "龙门币": "CE-6",
        "作战记录": "LS-6",
    }

    def _is_stage_usable(stage_id: str) -> bool:
        """Check if a stage is within its time window (available_from/available_until)."""
        lib = mw.config.get("stage_library", [])
        entry = next((s for s in lib if s.get("id") == stage_id), None)
        if not entry:
            return True
        now = datetime.now()
        af = entry.get("available_from", "")
        au = entry.get("available_until", "")
        if af:
            try:
                h, m = map(int, af.split(":"))
                if now.hour < h or (now.hour == h and now.minute < m):
                    return False
            except:
                pass
        if au:
            try:
                h, m = map(int, au.split(":"))
                if now.hour > h or (now.hour == h and now.minute > m):
                    return False
            except:
                pass
        return True

    def _pick_fight_stage(a) -> str:
        """Pick a stage based on the account's fight strategy."""
        mode = a.get("fight_mode", "schedule")
        default = a.get("fight_default", "1-7")
        stages = a.get("stages", []) or []

        if mode == "schedule":
            # Check weekly schedule
            weekdays = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
            wd = weekdays[datetime.now().weekday()]
            weekly = a.get("schedule_weekly", {}) or {}
            if wd in weekly and weekly[wd]:
                stage = weekly[wd]
                if stage in stages or stage == "Annihilation":
                    if _is_stage_usable(stage):
                        return stage
            # Check monthly schedule
            day = str(datetime.now().day)
            monthly = a.get("schedule_monthly", {}) or {}
            if day in monthly and monthly[day]:
                stage = monthly[day]
                if stage in stages or stage == "Annihilation":
                    if _is_stage_usable(stage):
                        return stage
            return default

        elif mode == "priority":
            priority = a.get("fight_priority", {}) or {}
            if stages:
                sorted_stages = sorted(
                    [s for s in stages if s in priority and _is_stage_usable(s)],
                    key=lambda s: priority.get(s, 0), reverse=True
                )
                if sorted_stages:
                    return sorted_stages[0]
            return default

        elif mode == "material":
            materials = a.get("fight_materials", []) or []
            unfinished = [m for m in materials if m.get("achieved", 0) < m.get("target", 0)]
            if unfinished:
                for m in unfinished:
                    item = m.get("item", "")
                    if item in _MATERIAL_STAGES:
                        stage = _MATERIAL_STAGES[item]
                        if _is_stage_usable(stage):
                            return stage
            return default

        return default

    def _account_usable(a, lq) -> bool:
        """Check if an account can be dispatched (has ADB/emu, not running)."""
        if a.get("_connect_only"):
            return False
        if not a.get("adb_address", "").strip() and not a.get("emu_instance_index", ""):
            return False
        if a.get("suspended", False):
            return False
        if lq.is_running(a.get("id", "")):
            return False
        return True

    @app.post("/api/action/smart_all")
    def handle_smart_all(body: dict):
        """Dispatch all three slots for all usable accounts."""
        lq = _lq()
        if lq._paused:
            lq.resume()
        count = 0
        for a in mw.accounts:
            aid = a.get("id", "")
            if not _account_usable(a, lq):
                continue
            if lq.is_queued(aid):
                continue
            # Maintenance slot
            maint_tasks = ["StartUp", "Infrast", "Recruit", "Mall", "Award"]
            _dispatch_slot(a, maint_tasks, "maintenance")
            lq.enqueue(aid, "force", priority=0, slot="maintenance")
            # Fight slot (stage selected by inject_smart in config_injector.py)
            fight_tasks = ["StartUp", "Fight"]
            _dispatch_slot(a, fight_tasks, "fight")
            lq.enqueue(aid, "force", priority=0, slot="fight")
            # Annihilation slot
            if a.get("smart_annihilation", ""):
                anni_tasks = ["StartUp", "Fight"]
                _dispatch_slot(a, anni_tasks, "annihilation")
                lq.enqueue(aid, "force", priority=0, slot="annihilation")
            count += 1
        if count:
            mw._log(f"▶ 全调度: {count} 个账号")
            _log_op("一键调度", f"{count} 个账号")
            lq.tick()
        return {"ok": True, "count": count}

    @app.post("/api/action/smart_maintenance")
    def handle_smart_maintenance():
        """Dispatch only the maintenance slot (Infrast/Recruit/Mall/Award)."""
        lq = _lq()
        if lq._paused:
            lq.resume()
        count = 0
        for a in mw.accounts:
            aid = a.get("id", "")
            if not _account_usable(a, lq):
                continue
            if lq.is_queued(aid, slot="maintenance"):
                continue
            tasks = ["StartUp", "Infrast", "Recruit", "Mall", "Award"]
            _dispatch_slot(a, tasks, "maintenance")
            lq.enqueue(aid, "force", priority=0, slot="maintenance")
            count += 1
        if count:
            mw._log(f"▶ 维护调度: {count} 个账号")
            _log_op("维护调度", f"{count} 个账号")
            lq.tick()
        return {"ok": True, "count": count}

    @app.post("/api/action/smart_fight")
    def handle_smart_fight():
        """Dispatch only the fight slot (stage selected by inject_smart)."""
        lq = _lq()
        if lq._paused:
            lq.resume()
        count = 0
        for a in mw.accounts:
            aid = a.get("id", "")
            if not _account_usable(a, lq):
                continue
            if lq.is_queued(aid, slot="fight"):
                continue
            tasks = ["StartUp", "Fight"]
            _dispatch_slot(a, tasks, "fight")
            lq.enqueue(aid, "force", priority=0, slot="fight")
            count += 1
        if count:
            mw._log(f"▶ 刷关调度: {count} 个账号")
            _log_op("刷关调度", f"{count} 个账号")
            lq.tick()
        return {"ok": True, "count": count}

    @app.post("/api/action/connect_running_emus")
    def handle_connect_running_emus():
        """Detect running emulators and launch a connect-only MAA instance for each.
        No tasks are enabled — MAA just attaches to the emulator and waits for manual use."""
        from infrastructure.task_constants import detect_emu_instances
        lq = _lq()
        if lq._paused:
            lq.resume()
        # Clean up stale connect-only accounts (previous session or stopped ones)
        conn = getattr(mw, 'connect_accounts', None)
        if conn is None:
            mw.connect_accounts = []
            conn = mw.connect_accounts
        for ac in list(conn):
            if ac.get("id") and not lq.is_running(ac["id"]) and not lq.is_queued(ac["id"]):
                try: conn.remove(ac)
                except ValueError: pass
        # Remove old connect-only entries lingering in main accounts list
        for a in list(mw.accounts):
            if a.get("_connect_only"):
                try: mw.accounts.remove(a)
                except ValueError: pass

        emus = [e for e in detect_emu_instances() if e.get("running")]
        runner = _runner()
        connected = 0
        for e in emus:
            idx = e.get("index", "")
            aid = f"emu{idx}"
            real = runner._has_real_process(aid) if runner else False
            if real or lq.is_queued(aid):
                continue
            port = e.get("adb_port", "")
            ac = _connect_account(conn, idx, port, mode="connect")
            lq.enqueue(aid, "force", priority=0, slot="connect")
            connected += 1
            mw._log(f"🔌 连接模拟器 #{idx} (ADB 127.0.0.1:{port})")
        if connected:
            _log_op("连接模拟器", f"{connected} 个运行中的模拟器")
            lq.tick()
        mw.config["accounts"] = mw.accounts
        return {"ok": True, "found": len(emus), "connected": connected}

    def _connect_account(conn: list, idx, port: str, mode: str = "connect") -> dict:
        """Create (or reuse) a connect-only temp account and dispatch tasks for it."""
        aid = f"emu{idx}"
        ac = next((x for x in conn if x.get("id") == aid), None)
        if ac is None:
            ac = {
                "id": aid,
                "name": f"模拟器#{idx}",
                "game_client": "",
                "emu_instance_index": idx,
                "adb_address": f"127.0.0.1:{port}" if port else "",
                "adb_path": "",
                "account_switch": "", "uid": "", "note": "仅连接(手动操作)",
                "consecutive_failures": 0,
                "_connect_only": True,
            }
            conn.append(ac)
        if mode == "daily":
            tasks = ["StartUp", "Infrast", "Recruit", "Mall", "Award"]
            ac["note"] = "通用日常"
        else:
            tasks = []
            ac["note"] = "仅连接(手动操作)"
        _dispatch_slot(ac, tasks, "connect")
        return ac

    @app.get("/api/connect/status")
    def handle_connect_status():
        from infrastructure.task_constants import detect_emu_instances
        lq = _lq()
        conn = getattr(mw, 'connect_accounts', None) or []
        emus = [e for e in detect_emu_instances() if e.get("running")]
        data = []
        for e in emus:
            idx = e.get("index", "")
            aid = f"emu{idx}"
            ac = next((x for x in conn if x.get("id") == aid), None)
            runner = _runner()
            real = runner._has_real_process(aid) if runner else False
            queued = lq.is_queued(aid) if lq else False
            data.append({
                "index": idx,
                "name": e.get("name", ""),
                "adb_port": e.get("adb_port", ""),
                "running": True,
                "maa_running": real or queued,
                "maa_queued": queued,
                "mode": (ac or {}).get("note", ""),
            })
        return {"ok": True, "emulators": data}

    @app.post("/api/connect/launch")
    def handle_connect_launch(body: dict):
        """Launch MAA for one emulator. mode: connect (idle) | daily (generic dailies)."""
        idx = body.get("index", "")
        mode = body.get("mode", "connect")
        if mode not in ("connect", "daily"):
            return {"ok": False, "error": f"未知模式: {mode}"}
        from infrastructure.task_constants import detect_emu_instances
        lq = _lq()
        if lq._paused:
            lq.resume()
        emu = next((e for e in detect_emu_instances()
                    if e.get("index") == idx and e.get("running")), None)
        if not emu:
            return {"ok": False, "error": f"模拟器 #{idx} 未运行"}
        aid = f"emu{idx}"
        # Use real process check, not queue marks — stale _active_emus entries
        # would otherwise block relaunch after a crash/stop.
        runner = _runner()
        if runner and runner._has_real_process(aid):
            return {"ok": False, "error": f"模拟器 #{idx} 的 MAA 已在运行"}
        if lq.is_queued(aid):
            return {"ok": False, "error": f"模拟器 #{idx} 的 MAA 排队中"}
        conn = getattr(mw, 'connect_accounts', None)
        if conn is None:
            mw.connect_accounts = []
            conn = mw.connect_accounts
        ac = _connect_account(conn, idx, emu.get("adb_port", ""), mode=mode)
        lq.enqueue(aid, "force", priority=0, slot="connect")
        lq.tick()
        _log_op("连接", f"模拟器#{idx} ({mode})")
        return {"ok": True, "aid": aid, "mode": mode}

    @app.post("/api/connect/{aid}/stop")
    def handle_connect_stop(aid: str):
        runner = _runner()
        runner.stop(aid)
        return {"ok": True}

    @app.post("/api/connect/{aid}/restart")
    def handle_connect_restart(aid: str, body: dict = {}):
        runner = _runner()
        runner.stop(aid)
        idx = aid.replace("emu", "", 1)
        mode = body.get("mode", "connect")
        from infrastructure.task_constants import detect_emu_instances
        lq = _lq()
        emu = next((e for e in detect_emu_instances()
                    if e.get("index") == idx and e.get("running")), None)
        if not emu:
            return {"ok": False, "error": f"模拟器 #{idx} 未运行"}
        conn = getattr(mw, 'connect_accounts', None)
        if conn is None:
            mw.connect_accounts = []
            conn = mw.connect_accounts
        ac = _connect_account(conn, idx, emu.get("adb_port", ""), mode=mode)
        lq.enqueue(aid, "force", priority=0, slot="connect")
        lq.tick()
        return {"ok": True}

    @app.get("/api/connect/{aid}/log")
    def handle_connect_log(aid: str):
        runner = _runner()
        p = runner._procs.get(aid)
        inst_path = getattr(p, "_inst_path", None)
        if not inst_path:
            return {"ok": True, "lines": ""}
        lp = Path(inst_path) / "debug" / "asst.log"
        try:
            if not lp.exists():
                return {"ok": True, "lines": ""}
            size = lp.stat().st_size
            start = max(0, size - 6000)
            with open(lp, "r", encoding="utf-8", errors="replace") as f:
                f.seek(start)
                text = f.read()
            lines = text.splitlines()[-30:]
            return {"ok": True, "lines": "\n".join(lines)}
        except Exception as e:
            return {"ok": True, "lines": f"(日志读取失败: {e})"}

    @app.get("/api/connect/{aid}/screenshot")
    def handle_connect_screenshot(aid: str):
        conn = getattr(mw, 'connect_accounts', None) or []
        ac = next((x for x in conn if x.get("id") == aid), None)
        if not ac:
            raise HTTPException(404, "connection not found")
        addr = ac.get("adb_address", "")
        if not addr and ac.get("emu_instance_index"):
            try:
                port = 16384 + int(ac["emu_instance_index"]) * 32
                addr = f"127.0.0.1:{port}"
            except Exception:
                pass
        if not addr:
            raise HTTPException(400, "no adb address")
        adb = ac.get("adb_path", "") or "adb"
        subprocess.run([adb, "-s", addr, "shell", "input", "keyevent", "KEYCODE_WAKEUP"],
                       capture_output=True, timeout=5, creationflags=_CF)
        r = subprocess.run([adb, "-s", addr, "exec-out", "screencap", "-p"],
                           capture_output=True, timeout=15, creationflags=_CF)
        if r.returncode != 0 or len(r.stdout) < 100:
            raise HTTPException(500, "screencap failed")
        return StreamingResponse(io.BytesIO(r.stdout), media_type="image/png",
                                 headers={"Cache-Control": "no-cache"})

    @app.post("/api/action/smart_login")
    def handle_smart_login():
        """Dispatch only the login slot (StartUp only, no tasks)."""
        lq = _lq()
        if lq._paused:
            lq.resume()
        count = 0
        for a in mw.accounts:
            aid = a.get("id", "")
            if not _account_usable(a, lq):
                continue
            if lq.is_queued(aid, slot="login"):
                continue
            tasks = ["StartUp"]
            _dispatch_slot(a, tasks, "login")
            lq.enqueue(aid, "force", priority=0, slot="login")
            count += 1
        if count:
            mw._log(f"▶ 登录验证: {count} 个账号")
            _log_op("登录验证", f"{count} 个账号")
            lq.tick()
        return {"ok": True, "count": count}

    @app.post("/api/action/smart_annihilation")
    def handle_smart_annihilation():
        """Dispatch only the annihilation slot for accounts with smart_annihilation set."""
        lq = _lq()
        if lq._paused:
            lq.resume()
        count = 0
        for a in mw.accounts:
            aid = a.get("id", "")
            if not _account_usable(a, lq):
                continue
            if not a.get("smart_annihilation", ""):
                continue
            if lq.is_queued(aid, slot="annihilation"):
                continue
            tasks = ["StartUp", "Fight"]
            _dispatch_slot(a, tasks, "annihilation")
            lq.enqueue(aid, "force", priority=0, slot="annihilation")
            count += 1
        if count:
            mw._log(f"▶ 剿灭调度: {count} 个账号")
            _log_op("剿灭调度", f"{count} 个账号")
            lq.tick()
        return {"ok": True, "count": count}

    @app.post("/api/action/smart_selected")
    def handle_smart_selected(body: dict):
        from services.dispatch_pool import create_dispatch
        ids = body.get("account_ids", [])
        include_anni = body.get("include_anni", True)
        only_anni = body.get("only_anni", False)
        lq = _lq()
        if lq._paused:
            lq.resume()
        smart = mw.config.get("smart_global", {}).get("enabled", False)
        base_tasks = _get_web_schedule_tasks(mw, include_anni, only_anni) if smart else [
            "StartUp", "Fight", "Infrast", "Recruit", "Mall", "Award"]
        count = 0
        for a in mw.accounts:
            aid = a.get("id", "")
            if aid not in ids:
                continue
            if not a.get("adb_address", "").strip() and not a.get("emu_instance_index", ""):
                continue
            if a.get("suspended", False):
                continue
            if lq.is_queued(aid) or lq.is_running(aid):
                continue
            # Per-account annihilation
            tasks = list(base_tasks)
            anni = a.get("smart_annihilation", "")
            has_anni = "Annihilation" in tasks
            if has_anni and not anni:
                tasks.remove("Annihilation")
            elif not has_anni and anni:
                tasks.append("Annihilation")
            a["dispatch_id"] = create_dispatch(tasks)
            lq.enqueue(aid, "force", priority=0)
            count += 1
        if count:
            mw._log(f"▶ 调度选中: {count} 个账号已入队")
            _log_op("调度选中", f"{count} 个账号")
            lq.tick()
        return {"ok": True, "count": count}

    @app.post("/api/instance/rebuild")
    def handle_rebuild():
        from services.instance_pool import ensure_maa_instances_async
        threading.Thread(target=lambda: ensure_maa_instances_async(mw.ctx, True), daemon=True).start()
        return {"ok": True}

    @app.post("/api/maa/check_update")
    def handle_maa_check_update():
        cur = mw.config.get("maa_version", "未知")
        urls = [
            "https://github.com/MaaAssistantArknights/MaaRelease/releases/latest",
            "https://api.github.com/repos/MaaAssistantArknights/MaaRelease/releases/latest",
        ]
        tag = ""
        for url in urls:
            try:
                headers = {"User-Agent": "MAAOrch"}
                if "api.github.com" in url:
                    headers["Accept"] = "application/json"
                req = urllib.request.Request(url, headers=headers)
                resp = urllib.request.urlopen(req, timeout=10)
                if "api.github.com" in url:
                    data = json.loads(resp.read())
                    tag = data.get("tag_name", "").lstrip("v")
                else:
                    m = re.search(r'/tag/v?([\d.]+)', resp.url)
                    if m:
                        tag = m.group(1)
                    if not tag:
                        html = resp.read().decode("utf-8", errors="replace")
                        m = re.search(r'/releases/tag/v?([\d.]+(?:-[\w.]+)?)', html)
                        if m:
                            tag = m.group(1)
                if tag:
                    break
            except:
                continue
        if tag:
            return {"ok": True, "has_update": tag != cur, "current": cur, "latest": tag}
        return {"ok": False, "error": "无法获取版本信息",
                "manual_url": "https://github.com/MaaAssistantArknights/MaaAssistantArknights/releases"}

    @app.post("/api/maa/download_update")
    def handle_maa_download_update():
        def _do_update():
            try:
                from services.maa_download import ensure_maa_available
                source_dir = Path(__file__).parent.parent / "services" / "maa" / "source"
                # Stop all running MAA before updating
                runner = getattr(mw, 'runner', None)
                if runner:
                    for aid in list(runner._active.keys()):
                        runner.stop(aid)
                    time.sleep(2)
                ensure_maa_available(mw.ctx, source_dir)
                # Rebuild instances after update
                from services.instance_pool import ensure_maa_instances_async
                ensure_maa_instances_async(mw.ctx, True)
            except Exception as e:
                mw.ctx.log(f"[MAA更新] 失败: {e}")
        threading.Thread(target=_do_update, daemon=True).start()
        return {"ok": True, "message": "更新已开始后台下载"}

    @app.get("/api/maa/download_status")
    def handle_maa_download_status():
        from services.maa_download import get_download_status
        return {"ok": True, **get_download_status()}

    @app.get("/api/maa/status")
    def handle_maa_status():
        from services.maa_download import get_download_status, _is_source_ready, _detect_version
        source_dir = Path(__file__).parent.parent / "services" / "maa" / "source"
        ready = _is_source_ready(source_dir)
        version = mw.config.get("maa_version", "")
        source_exe = source_dir / "MAA.exe"
        source_running = False
        if source_exe.exists():
            r = subprocess.run(["tasklist", "/NH", "/FI", "IMAGENAME eq MAA.exe"],
                               capture_output=True, text=True, timeout=3, creationflags=_CF)
            source_running = "MAA.exe" in r.stdout
        pool = Path(__file__).parent.parent / "services" / "maa" / "instances"
        total = 0
        running = 0
        if pool.exists():
            for d in sorted(pool.glob("*"), key=lambda x: x.name):
                if not d.is_dir() or not d.name.isdigit():
                    continue
                total += 1
                pid_file = d / ".pid"
                if pid_file.exists():
                    try:
                        pid = int(pid_file.read_text().strip())
                        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                           capture_output=True, text=True, timeout=2,
                                           creationflags=_CF)
                        if str(pid) in r.stdout:
                            running += 1
                    except Exception:
                        pass
        dl = get_download_status()
        return {"ok": True, "ready": ready, "version": version,
                "source_ready": ready, "source_version": _detect_version(source_dir),
                "source_running": source_running,
                "instances": total, "instances_running": running,
                "download": dl}

    @app.post("/api/maa/launch_source")
    def handle_maa_launch_source():
        """Launch the source MAA.exe in a normal window so the user can complete
        first-run initialization (protocol/hot-update) manually, then close it."""
        source_dir = Path(__file__).parent.parent / "services" / "maa" / "source"
        exe = source_dir / "MAA.exe"
        if not exe.exists():
            return {"ok": False, "error": "源 MAA 未安装，请先下载"}
        r = subprocess.run(["tasklist", "/NH", "/FI", "IMAGENAME eq MAA.exe"],
                           capture_output=True, text=True, timeout=3, creationflags=_CF)
        if "MAA.exe" in r.stdout:
            return {"ok": False, "error": "MAA 已在运行"}
        try:
            subprocess.Popen([str(exe)])  # normal window — user needs to see/operate it
            mw._log("🚀 启动源 MAA（请完成初始化后关闭窗口）")
            return {"ok": True, "message": "源 MAA 已启动"}
        except Exception as e:
            return {"ok": False, "error": f"启动失败: {e}"}

    @app.post("/api/maa/check_source")
    def handle_maa_check_source():
        from services.maa_download import _is_source_ready, _detect_version
        source_dir = Path(__file__).parent.parent / "services" / "maa" / "source"
        exe = source_dir / "MAA.exe"
        r = subprocess.run(["tasklist", "/NH", "/FI", "IMAGENAME eq MAA.exe"],
                           capture_output=True, text=True, timeout=3, creationflags=_CF)
        running = "MAA.exe" in r.stdout
        return {"ok": True,
                "exe_exists": exe.exists(),
                "ready": _is_source_ready(source_dir) if exe.exists() else False,
                "version": _detect_version(source_dir) if exe.exists() else "",
                "running": running}

    @app.get("/api/maa/instances")
    def handle_maa_instances_list():
        pool = Path(__file__).parent.parent / "services" / "maa" / "instances"
        items = []
        if pool.exists():
            for d in sorted(pool.glob("*"), key=lambda x: x.name):
                if not d.is_dir() or not d.name.isdigit():
                    continue
                exe = d / "MAA.exe"
                pid_file = d / ".pid"
                meta_file = d / ".meta"
                pid = ""
                running = False
                meta = ""
                if pid_file.exists():
                    try:
                        pid = pid_file.read_text().strip()
                        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                           capture_output=True, text=True, timeout=2,
                                           creationflags=_CF)
                        running = str(pid) in r.stdout
                    except Exception:
                        pass
                if meta_file.exists():
                    try:
                        meta = meta_file.read_text(encoding="utf-8", errors="replace").strip()
                    except Exception:
                        pass
                items.append({"index": d.name, "exe": exe.exists(), "pid": pid,
                              "running": running, "meta": meta})
        return {"ok": True, "instances": items}

    @app.get("/api/maa/instances/{n}/config")
    def handle_maa_instance_config(n: str):
        """Diagnostic: show connection-related keys in an instance's MAA config files.
        Read-only — used to verify what MAA actually reads (Connect.Address etc.).
        Also lists all files in the config dir (incl. .bak which MAA may fall back to)."""
        inst_dir = Path(__file__).parent.parent / "services" / "maa" / "instances" / str(n)
        cfg_dir = inst_dir / "config"
        result = {"ok": True, "index": n, "files": {}}
        # List all files in config dir (bak files matter — MAA restores from them)
        result["dir_files"] = sorted(f.name for f in cfg_dir.iterdir()) if cfg_dir.exists() else []
        for fn in ("gui.json", "gui.new.json", "gui.json.bak", "gui.new.json.bak"):
            fp = cfg_dir / fn
            entry = {"exists": fp.exists()}
            if fp.exists():
                try:
                    d = json.loads(fp.read_text(encoding="utf-8"))
                    entry["top_keys"] = list(d.keys())
                    c = d.get("Configurations", {}).get("Default", {})
                    entry["current"] = d.get("Current", "")
                    entry["connect"] = {
                        "Connect.Address": c.get("Connect.Address", ""),
                        "Connect.AddressHistory": c.get("Connect.AddressHistory", ""),
                        "Connect.AdbPath": c.get("Connect.AdbPath", ""),
                        "Connect.AutoDetect": c.get("Connect.AutoDetect", ""),
                        "Connect.AlwaysAutoDetect": c.get("Connect.AlwaysAutoDetect", ""),
                        "Connect.ConnectConfig": c.get("Connect.ConnectConfig", ""),
                        "Connect.TouchMode": c.get("Connect.TouchMode", ""),
                        "Start.ClientType": c.get("Start.ClientType", ""),
                        "Start.RunDirectly": c.get("Start.RunDirectly", ""),
                        "Start.StartGame": c.get("Start.StartGame", ""),
                        "Start.EmulatorPath": c.get("Start.EmulatorPath", ""),
                        "Start.EmulatorAddCommand": c.get("Start.EmulatorAddCommand", ""),
                        "Start.OpenEmulatorAfterLaunch": c.get("Start.OpenEmulatorAfterLaunch", ""),
                        "MainFunction.PostActions": c.get("MainFunction.PostActions", ""),
                    }
                    tq = c.get("TaskQueue", [])
                    entry["task_queue"] = [
                        {"type": t.get("TaskType", ""), "enable": t.get("IsEnable", False)}
                        for t in tq if isinstance(t, dict)
                    ]
                    entry["global"] = {k: v for k, v in d.get("Global", {}).items()
                                       if k.startswith(("GUI.", "Start."))}
                except Exception as e:
                    entry["error"] = str(e)
            result["files"][fn] = entry
        return result

    @app.post("/api/orch/check_update")
    def handle_orch_check_update():
        # Git-based detection (deployment is a git clone) — most accurate
        root = Path(__file__).parent.parent
        git_error = ""
        try:
            r = subprocess.run(["git", "-C", str(root), "fetch", "origin"],
                               capture_output=True, timeout=30, creationflags=_CF,
                               encoding="utf-8", errors="replace")
            if r.returncode != 0:
                git_error = (r.stderr or "git fetch failed").strip().splitlines()
                git_error = git_error[0][:80] if git_error else "git fetch failed"
            else:
                branch = subprocess.run(["git", "-C", str(root), "symbolic-ref", "--short", "HEAD"],
                                        capture_output=True, timeout=10, creationflags=_CF,
                                        encoding="utf-8", errors="replace").stdout.strip()
                if not branch:
                    branch = "main"
                rev = subprocess.run(["git", "-C", str(root), "rev-list", "--count", f"HEAD..origin/{branch}"],
                                     capture_output=True, timeout=10, creationflags=_CF,
                                     encoding="utf-8", errors="replace")
                if rev.returncode == 0:
                    behind = int(rev.stdout.strip() or 0)
                    return {"ok": True, "has_update": behind > 0, "behind": behind,
                            "branch": branch, "method": "git"}
                git_error = "git rev-list failed"
        except FileNotFoundError:
            git_error = "git 未安装"
        except subprocess.TimeoutExpired:
            git_error = "git fetch 超时（网络慢）"
        except Exception as e:
            git_error = str(e)[:80]
        # Fallback: GitHub release check
        try:
            req = urllib.request.Request(
                "https://api.github.com/repos/xiachk083-hub/MAAOrch/releases/latest",
                headers={"User-Agent": "MAAOrch-Updater"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
            tag = data.get("tag_name", "")
            html_url = data.get("html_url", "")
            # Zip mode: git unavailable (zip deployment) — offer main-branch zip download
            return {"ok": True, "latest": tag, "html_url": html_url,
                    "method": "release", "git_error": git_error,
                    "zip_mode": True,
                    "zip_url": "https://github.com/xiachk083-hub/MAAOrch/archive/refs/heads/main.zip"}
        except Exception as e:
            return {"ok": False, "error": str(e), "method": "release",
                    "git_error": git_error}

    def _orch_update_zip(root: Path, result_file: Path, mw_ref) -> None:
        """Zip-deployment update: download main.zip, extract to _update/, then
        replace via replace.bat (taskkill → xcopy → restart). Runs in background."""
        import shutil as _sh
        from infrastructure.utils import is_safe_zip_path
        _log = mw_ref._log
        _log("[更新] git 不可用，使用 zip 模式更新（main 分支最新）")
        zip_url = "https://github.com/xiachk083-hub/MAAOrch/archive/refs/heads/main.zip"
        try:
            # Graceful shutdown: stop running MAA first
            runner = getattr(mw_ref, 'runner', None)
            if runner:
                for aid in list(runner._active.keys()):
                    try:
                        runner.stop(aid)
                    except Exception:
                        pass
            time.sleep(2)
            # Download main.zip (small: gitignored files excluded)
            tmp_dir = Path(tempfile.mkdtemp(prefix="maorch_upd_"))
            tmp_zip = tmp_dir / "main.zip"
            _log(f"[更新] 下载 {zip_url.split('/')[-1]}")
            req = urllib.request.Request(zip_url, headers={"User-Agent": "MAAOrch-Updater"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                with open(tmp_zip, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
            _log(f"[更新] 下载完成 {tmp_zip.stat().st_size // 1024}KB")
            # Extract to _update/ (strip top-level folder, zip-slip safe)
            update_dir = root / "_update"
            if update_dir.exists():
                _sh.rmtree(str(update_dir), ignore_errors=True)
            update_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(str(tmp_zip)) as zf:
                for member in zf.namelist():
                    if not is_safe_zip_path(member, update_dir.parent):
                        raise ValueError(f"zip slip: {member}")
                    parts = member.split("/", 1)
                    if len(parts) < 2 or not parts[1]:
                        continue  # top-level folder itself
                    target = update_dir / parts[1]
                    if member.endswith("/"):
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(str(target), "wb") as dst:
                            dst.write(src.read())
            _sh.rmtree(str(tmp_dir), ignore_errors=True)
            _log(f"[更新] 解压完成 {update_dir}")
            # Generate replace.bat
            bat = root / "replace.bat"
            bat.write_text(
                '@echo off\r\n'
                'chcp 65001 >nul\r\n'
                f'taskkill /f /fi "WINDOWTITLE eq MAAOrch" 2>nul\r\n'
                'timeout /t 3 /nobreak >nul\r\n'
                f'cd /d "{root}"\r\n'
                f'xcopy /E /Y "{root}\\_update\\*" "{root}" >"{result_file}" 2>&1\r\n'
                'if errorlevel 1 (\r\n'
                f'  echo [RESULT] FAILED >>"{result_file}"\r\n'
                ') else (\r\n'
                f'  echo [RESULT] OK >>"{result_file}"\r\n'
                ')\r\n'
                f'rmdir /S /Q "{root}\\_update"\r\n'
                f'start /min "" "{sys.executable}" "{root}\\main_web.pyw"\r\n'
                'del "%~f0"\r\n', encoding="utf-8")
            subprocess.Popen([str(bat)], shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
            time.sleep(1)
            os._exit(0)
        except Exception as e:
            try:
                result_file.write_text(f"[RESULT] FAILED\n{e}", encoding="utf-8")
            except Exception:
                pass
            _log(f"[更新] zip 更新失败: {e}")
            os._exit(0)

    @app.post("/api/orch/update")
    def handle_orch_update():
        root = Path(__file__).parent.parent
        result_file = Path(tempfile.gettempdir()) / "maorch_update_result.txt"
        # Try git mode first; fall back to zip mode for zip deployments
        git_ok = False
        try:
            st = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                                capture_output=True, timeout=10, creationflags=_CF,
                                encoding="utf-8", errors="replace")
            if st.returncode == 0:
                git_ok = True
                if st.stdout.strip():
                    return {"ok": False, "error": "本地有未提交的改动，请先处理（git stash / commit）"}
        except Exception:
            git_ok = False
        if git_ok:
            # Generate update.bat: pull + write result + restart, run detached
            try:
                bat = root / "update.bat"
                bat.write_text(
                    '@echo off\r\n'
                    'chcp 65001 >nul\r\n'
                    f'cd /d "{root}"\r\n'
                    'timeout /t 2 /nobreak >nul\r\n'
                    f'git pull --ff-only >"{result_file}" 2>&1\r\n'
                    'if errorlevel 1 (\r\n'
                    f'  echo [RESULT] FAILED >>"{result_file}"\r\n'
                    ') else (\r\n'
                    f'  echo [RESULT] OK >>"{result_file}"\r\n'
                    ')\r\n'
                    f'start /min "" "{sys.executable}" "{root}\\main_web.pyw"\r\n'
                    'del "%~f0"\r\n', encoding="utf-8")
            except Exception as e:
                return {"ok": False, "error": f"写入 update.bat 失败: {e}"}

            def _do_git():
                runner = getattr(mw, 'runner', None)
                if runner:
                    for aid in list(runner._active.keys()):
                        try:
                            runner.stop(aid)
                        except Exception:
                            pass
                time.sleep(2)
                subprocess.Popen([str(bat)], shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
                time.sleep(1)
                os._exit(0)
            threading.Thread(target=_do_git, daemon=True).start()
            return {"ok": True, "message": "正在更新并重启，页面将短暂不可用"}
        # Zip mode (git unavailable — zip deployment)
        threading.Thread(target=_orch_update_zip,
                         args=(root, result_file, mw), daemon=True).start()
        return {"ok": True, "message": "正在下载并更新（zip 模式），页面将短暂不可用"}

    @app.get("/api/orch/update_result")
    def handle_orch_update_result():
        rf = Path(tempfile.gettempdir()) / "maorch_update_result.txt"
        if not rf.exists():
            return {"ok": True, "has_result": False}
        try:
            text = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return {"ok": True, "has_result": False}
        ok = "[RESULT] OK" in text
        rf.unlink(missing_ok=True)  # one-shot: delete after read
        return {"ok": True, "has_result": True, "success": ok, "detail": text[-500:]}

    @app.post("/api/orch/download_update")
    def handle_orch_download_update():
        import webbrowser
        webbrowser.open("https://github.com/xiachk083-hub/MAAOrch/releases/latest")
        return {"ok": True}

    @app.post("/api/config/export")
    def handle_config_export():
        data = {
            "accounts": mw.accounts,
            "config": {k: mw.config[k] for k in
                       ("parallel_max", "schedule_mode", "deficit", "stuck_timeout",
                        "daily_batch_time", "smart_global") if k in mw.config}
        }
        return {"ok": True, "data": data}

    @app.post("/api/config/import")
    def handle_config_import(body: dict):
        data = body.get("data", {})
        imported = 0
        for ac in data.get("accounts", []):
            aid = ac.get("id", "")
            if any(a["id"] == aid for a in mw.accounts):
                continue
            for k in ("consecutive_failures", "stats", "_persist_plan", "dispatch_id", "smart_plan"):
                ac.pop(k, None)
            mw.accounts.append(ac)
            imported += 1
        mw.config["accounts"] = mw.accounts
        from models.config_manager import save_config
        save_config(mw.config)
        return {"ok": True, "imported": imported}

    # Catch-all: serve static files (must be last route)
    @app.get("/{full_path:path}")
    async def serve_static(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404)
        fp = web_dir / full_path if full_path else web_dir / "index.html"
        if not str(fp.resolve()).startswith(str(web_dir.resolve())):
            raise HTTPException(403)
        if not fp.exists() or not fp.is_file():
            fp = web_dir / "index.html"
        mime = mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
        return FileResponse(str(fp), media_type=mime)

    return app


def start_server(mw: Any, port: int = 19999) -> None:
    bind = mw.config.get("bind_address", "0.0.0.0") if hasattr(mw, 'config') else "0.0.0.0"
    cert = mw.config.get("ssl_cert", "") if hasattr(mw, 'config') else ""
    key = mw.config.get("ssl_key", "") if hasattr(mw, 'config') else ""
    app = create_app(mw)
    config = uvicorn.Config(app, host=bind, port=port, log_level="info",
                            ssl_certfile=cert if cert and key else None,
                            ssl_keyfile=key if cert and key else None)
    server = uvicorn.Server(config)
    server.run()
