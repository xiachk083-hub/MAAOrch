from __future__ import annotations
import json,time,re,os,hmac,subprocess
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any
from PySide6.QtCore import QThread, Signal

# Operation log ring buffer
_OPLOG: list[dict] = []
def _log_op(action: str, detail: str = "") -> None:
    global _OPLOG
    _OPLOG.append({"ts": time.strftime("%H:%M:%S"), "action": action, "detail": detail})
    if len(_OPLOG) > 100:
        _OPLOG = _OPLOG[-100:]

class ApiServer(QThread):
    log_msg = Signal(str)

    def __init__(self, port: int, token: str, mw: Any) -> None:
        super().__init__(); self.port=port; self.token=token; self.mw=mw; self._httpd=None
    def run(self) -> None:
        mw=self.mw; token=self.token
        bind = mw.config.get("bind_address", "127.0.0.1") if hasattr(mw, 'config') else "127.0.0.1"
        # Simple rate limiter: max 200 req/min per IP (higher for localhost)
        _rate_buckets: dict[str,list[float]] = {}
        def _check_rate(ip: str, limit: int = 200) -> bool:
            now = time.time()
            bucket = _rate_buckets.get(ip, [])
            bucket = [t for t in bucket if t > now - 60]
            if len(bucket) >= limit:
                _rate_buckets[ip] = bucket
                return False
            bucket.append(now)
            _rate_buckets[ip] = bucket
            return True
        class Handler(BaseHTTPRequestHandler):
            def log_message(s,f,*a): pass
            def _check_rate_limit(s):
                ip = s.client_address[0]
                if not _check_rate(ip):
                    s.send_response(429)
                    s.send_header("Retry-After","60")
                    s.end_headers()
                    return False
                return True
            def _check_auth(s):
                if not token: return s._json({"error":"token not configured"}, 403)
                # CSRF: reject requests from non-local origins
                ref = s.headers.get("Referer", "")
                if ref and not ref.startswith("http://127.0.0.1") and not ref.startswith("http://localhost"):
                    return s._json({"error":"forbidden"}, 403)
                h=s.headers.get("x-agent-token","")
                # Web UI (no token) allowed from localhost
                if not h:
                    return True
                return hmac.compare_digest(h, token)
            def _json(s,data,code=200):
                body=json.dumps(data,ensure_ascii=False).encode()
                s.send_response(code); s.send_header("Content-Type","application/json"); s.send_header("Content-Length",str(len(body))); s.end_headers()
                s.wfile.write(body)
            def do_OPTIONS(s):
                s.send_response(200);s.send_header("Content-Type","application/json");s.end_headers()
            def do_GET(s):
                if not s._check_rate_limit(): return
                p=s.path.split("?")[0]
                # Static files: no auth required, try serving from ui/web/
                if not p.startswith("/api/"):
                    return s._serve_static(p)
                if not s._check_auth(): return s._json({"error":"unauthorized"},401)
                if p=="/api/status": return s._handle_status()
                if p=="/api/node/info": return s._handle_node_info()
                if p=="/api/node/dashboard": return s._handle_node_dashboard()
                if p.startswith("/api/account/") and p.endswith("/status"): return s._handle_account_status(p)
                if p.startswith("/api/account/") and p.endswith("/stats"): return s._handle_account_stats(p)
                if p.startswith("/api/account/") and p.endswith("/screenshot"): return s._handle_account_screenshot(p)
                if p.startswith("/api/screenshots/") and "/file/" in p: return s._handle_screenshot_file(p)
                if p.startswith("/api/screenshots/"): return s._handle_screenshots_list(p)
                if p=="/api/export/logs": return s._handle_export_logs()
                if p=="/api/stats/dashboard": return s._handle_stats_dashboard()
                if p=="/api/stats": return s._handle_all_stats()
                if p=="/api/queue": return s._handle_queue_status()
                if p=="/api/logs": return s._handle_logs(s.path)
                if p=="/api/maa/log": return s._handle_maa_log(s.path)
                if p=="/api/accounts": return s._handle_accounts()
                if p=="/api/oplog": return s._handle_oplog()
                if p=="/api/sse": return s._handle_sse()
                if p.startswith("/api/account/") and p.endswith("/config"): return s._handle_account_config(p)
                if p=="/api/config": return s._handle_get_config()
                if p=="/api/settings/smart": return s._handle_get_smart()
                if p=="/api/emulators": return s._handle_emulators()
                # Static file serving for Web UI
                if p=="/" or p.startswith("/ui/web/"):
                    return s._serve_static(p)
                s._json({"error":"not found"},404)
            def do_POST(s):
                if not s._check_rate_limit(): return
                if not s._check_auth(): return s._json({"error":"unauthorized"},401)
                cl=int(s.headers.get("Content-Length",0))
                body=json.loads(s.rfile.read(cl)) if cl>0 else {}
                p=s.path.split("?")[0]
                if p=="/api/pipeline/start": return s._handle_pipeline_start()
                if p=="/api/pipeline/stop": return s._handle_pipeline_stop()
                if p=="/api/pipeline/pause": return s._handle_pipeline_pause(body)
                if p=="/api/node/register": return s._handle_node_register(body)
                if p=="/api/node/heartbeat": return s._handle_node_heartbeat(body)
                if p.startswith("/api/account/") and p.endswith("/launch"): return s._handle_account_launch(p)
                if p.startswith("/api/account/") and p.endswith("/stop"): return s._handle_account_stop(p)
                if p=="/api/config/sync": return s._handle_config_sync(body)
                if p=="/api/queue/enqueue": return s._handle_queue_enqueue(body)
                if p=="/api/queue/dequeue": return s._handle_queue_dequeue(body)
                if p=="/api/account": return s._handle_create_account(body)
                if p.startswith("/api/account/") and p.endswith("/edit"): return s._handle_edit_account(p, body)
                if p.startswith("/api/account/") and p.endswith("/delete"): return s._handle_delete_account(p)
                if p.startswith("/api/account/") and p.endswith("/config"): return s._handle_save_account_config(p, body)
                if p.startswith("/api/screenshots/") and p.endswith("/delete"): return s._handle_screenshots_delete(p, body)
                if p=="/api/queue/clear": return s._handle_queue_clear()
                if p=="/api/queue/pause": return s._handle_queue_pause()
                if p=="/api/queue/resume": return s._handle_queue_resume()
                if p=="/api/config": return s._handle_save_config(body)
                if p=="/api/settings/smart": return s._handle_save_smart(body)
                if p=="/api/action/stop_all": return s._handle_stop_all()
                if p=="/api/action/smart_all": return s._handle_smart_all(body)
                if p=="/api/action/smart_selected": return s._handle_smart_selected(body)
                if p=="/api/instance/rebuild": return s._handle_rebuild()
                if p=="/api/sse": return s._handle_sse()
                if p=="/api/maa/check_update": return s._handle_maa_check_update()
                if p=="/api/maa/download_update": return s._handle_maa_download_update()
                if p=="/api/orch/check_update": return s._handle_orch_check_update()
                if p=="/api/orch/download_update": return s._handle_orch_download_update()
                if p=="/api/config/export": return s._handle_config_export()
                if p=="/api/config/import": return s._handle_config_import(body)
                s._json({"error":"not found"},404)
            def _handle_config_export(s):
                try:
                    import json
                    data = {"accounts": mw.accounts, "config": {k: mw.config[k] for k in ("parallel_max","schedule_mode","deficit","stuck_timeout","daily_batch_time","smart_global") if k in mw.config}}
                    s._json({"ok":True,"data":data})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_config_import(s,body):
                try:
                    data = body.get("data", {})
                    imported = 0
                    for ac in data.get("accounts", []):
                        aid = ac.get("id", "")
                        if any(a["id"] == aid for a in mw.accounts):
                            continue
                        ac.pop("consecutive_failures",None); ac.pop("stats",None); ac.pop("_persist_plan",None); ac.pop("dispatch_id",None); ac.pop("smart_plan",None)
                        mw.accounts.append(ac)
                        imported += 1
                    mw.config["accounts"] = mw.accounts
                    from models.config_manager import save_config
                    save_config(mw.config)
                    s._json({"ok":True,"imported":imported})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_sse(s):
                """Server-Sent Events: push state changes to Web UI."""
                s.send_response(200)
                s.send_header("Content-Type", "text/event-stream")
                s.send_header("Cache-Control", "no-cache")
                s.send_header("Connection", "keep-alive")
                s.send_header("Access-Control-Allow-Origin", "*")
                s.end_headers()
                try:
                    while not s.server._stopped:
                        data = json.dumps(_gather_sse_state(mw))
                        s.wfile.write(f"data: {data}\n\n".encode())
                        s.wfile.flush()
                        import time as _t
                        _t.sleep(1)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def _gather_sse_state(mw) -> dict:
                """Collect current state for SSE push."""
                try:
                    from services.dispatch_pool import get_template
                    accts = []
                    for a in mw.accounts:
                        aid = a.get("id", "")
                        running = mw.launch_queue.is_running(aid) if hasattr(mw, 'launch_queue') else False
                        queued = mw.launch_queue.is_queued(aid) if hasattr(mw, 'launch_queue') else False
                        accts.append({
                            "id": aid, "name": a.get("name",""),
                            "running": running, "queued": queued,
                            "emu": a.get("emu_instance_index",""),
                            "client": a.get("game_client",""),
                            "failures": a.get("consecutive_failures", 0),
                            "suspended": a.get("suspended", False),
                        })
                    q = {"count": mw.launch_queue.pending_count if hasattr(mw, 'launch_queue') and mw.launch_queue else 0}
                    notifs = getattr(mw, '_notifications', [])[-5:]
                    return {"ok": True, "accounts": accts, "queue": q, "notifications": notifs}
                except Exception:
                    return {"ok": False}
            def _handle_status(s):
                accts=[]
                proc_status = getattr(mw, "_proc_status", set())
                proc_times = getattr(mw, "_proc_start_times", {})
                for i,a in enumerate(mw.accounts):
                    progs=[w for w in mw.warehouse if w.get("account_ref")==a["id"]]
                    pid=progs[0]["id"] if progs else ""
                    running=pid in proc_status
                    elapsed=0
                    if running and pid in proc_times: elapsed=int(time.time()-proc_times[pid])
                    accts.append({"name":a.get("name",""),"index":i,"running":running,"elapsed":elapsed,"adb":a.get("adb_address",""),"emu_index":a.get("emu_instance_index","")})
                pt = getattr(mw, "pipeline_thread", None)
                pipeline_running = pt and pt.isRunning() if hasattr(pt, 'isRunning') else False
                running_count = getattr(mw, 'runner', None)
                if running_count:
                    running_count = len(running_count._active)
                else:
                    running_count = sum(1 for a in accts if a["running"])
                s._json({"accounts":accts,"pipeline_running":pipeline_running,"running":running_count})
            def _handle_node_info(s):
                import psutil as _ps
                mem = _ps.virtual_memory()
                cpu_count = _ps.cpu_count()
                node_id = mw.config.get("node_id", "")
                node_name = mw.config.get("node_name", "")
                s._json({"node_id": node_id, "node_name": node_name,
                         "version": "1.2.0", "parallel_max": mw.config.get("parallel_max", 3),
                         "account_count": len(mw.accounts),
                         "running_count": len(getattr(mw, "_proc_status", set())),
                         "cpu_count": cpu_count, "memory_total_mb": mem.total // 1048576,
                         "memory_available_mb": mem.available // 1048576})
            def _handle_node_dashboard(s):
                import psutil as _ps, subprocess as _sp, json as _js
                try:
                    # System
                    try:
                        mem = _ps.virtual_memory()
                        cpu_pct = _ps.cpu_percent(interval=0)
                        cpu_count = _ps.cpu_count()
                    except Exception:
                        mem = type('m',(),{'total':1,'available':1,'percent':0})()
                        cpu_pct = 0
                        cpu_count = 1
                    processes = []
                    runner = getattr(mw, 'runner', None)
                    if runner and hasattr(runner, '_proc_info'):
                        accounts_list = getattr(mw, 'accounts', []) or []
                        proc_status = getattr(mw, "_proc_status", set())
                        for aid, info in list(runner._proc_info.items()):
                            try:
                                ac = next((a for a in accounts_list if a.get("id") == aid), None)
                                if not ac: continue
                                maa = info.get("maa", {}) or {}
                                emu = info.get("emu", {}) or {}
                                processes.append({
                                    "aid": aid, "name": ac.get("name", aid),
                                    "running": aid in proc_status,
                                    "last_task": ac.get("_last_task", ""),
                                    "maa_mem_mb": maa.get("mem_mb", 0),
                                    "maa_cpu_pct": maa.get("cpu_pct", 0),
                                    "maa_pid": maa.get("pid"),
                                    "emu_mem_mb": emu.get("mem_mb", 0),
                                    "emu_cpu_pct": emu.get("cpu_pct", 0),
                                    "emu_pid": emu.get("pid"),
                                    "emu_name": emu.get("name", ""),
                                })
                            except Exception:
                                continue
                    # GPU
                    gpu = {"name": "", "usage": 0, "mem_used_mb": 0, "mem_total_mb": 0}
                    try:
                        o = _sp.check_output(["nvidia-smi","--query-gpu=name,utilization.gpu,memory.used,memory.total","--format=csv,noheader,nounits"], timeout=5, encoding="utf-8", errors="replace", creationflags=_sp.CREATE_NO_WINDOW)
                        parts = o.strip().split(", ")
                        if len(parts) >= 4:
                            gpu = {"name": parts[0], "usage": float(parts[1]),
                                   "mem_used_mb": int(float(parts[2])), "mem_total_mb": int(float(parts[3]))}
                    except: pass
                    # Capacity
                    total_proc_mem = sum((p.get("maa_mem_mb",0) or 0) + (p.get("emu_mem_mb",0) or 0) for p in processes) if processes else 0
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
                    s._json({"ok":True,"system":{"cpu_pct":cpu_pct,"cpu_count":cpu_count,"memory_total_mb":mem.total//1048576,
                             "memory_available_mb":mem.available//1048576,"memory_pct":mem.percent},
                             "gpu":gpu,"processes":processes,"capacity":{"parallel_max":parallel_max,
                             "running":running,"by_parallel":by_parallel,"by_memory":by_mem,"by_gpu":by_gpu,
                             "max":capacity,"limit_by":limit_by,"est_per_instance_mb":int(est_per),
                             "deficit":mw.config.get("deficit",0),"stuck_timeout":mw.config.get("stuck_timeout",10)},
                             "samples":samples[-360:] if samples else [],
                             "gantt":gantt[-100:] if gantt else []})
                except Exception as e:
                    _log_op("dashboard_error", str(e)[:80])
                    s._json({"ok":True,"system":{"cpu_pct":0,"cpu_count":1,"memory_total_mb":0,"memory_available_mb":0,"memory_pct":0},
                             "gpu":{"name":"","usage":0,"mem_used_mb":0,"mem_total_mb":0},"processes":[],"capacity":{"parallel_max":1,"running":0,"by_parallel":1,"by_memory":0,"by_gpu":0,"max":0,"limit_by":"N/A","est_per_instance_mb":1500,"deficit":0,"stuck_timeout":10},
                             "samples":[],"gantt":[]})
            def _handle_node_register(s, body):
                daigan_url = body.get("daigan_url", "")
                if daigan_url and daigan_url.startswith("https://"):
                    mw.config["daigan_url"] = daigan_url
                    from models.config_manager import save_config
                    save_config(mw.config)
                    mw._log(f"节点已注册到: {daigan_url}")
                s._json({"node_id": mw.config.get("node_id", ""), "status": "registered"})
            def _handle_node_heartbeat(s, body):
                import psutil as _ps
                s._json({"node_id": mw.config.get("node_id", ""), "status": "ok",
                         "ts": time.time(), "running": len(getattr(mw, "_proc_status", set())),
                         "queued": getattr(mw.launch_queue, "pending_count", 0) if hasattr(mw, "launch_queue") else 0,
                         "mem_avail_mb": _ps.virtual_memory().available // 1048576})
            def _handle_account_status(s,p):
                try: idx=int(p.split("/")[3])
                except: return s._json({"error":"bad index"},400)
                if idx<0 or idx>=len(mw.accounts): return s._json({"error":"not found"},404)
                a=mw.accounts[idx]; progs=[w for w in mw.warehouse if w.get("account_ref")==a["id"]]
                pid=progs[0]["id"] if progs else ""
                running=pid in mw._proc_status
                elapsed=0
                if running and pid in mw._proc_start_times: elapsed=int(time.time()-mw._proc_start_times[pid])
                s._json({"name":a.get("name",""),"running":running,"elapsed":elapsed})
            def _handle_account_stats(s,p):
                try: idx=int(p.split("/")[3])
                except: return s._json({"error":"bad index"},400)
                if idx<0 or idx>=len(mw.accounts): return s._json({"error":"not found"},404)
                a=mw.accounts[idx]
                from models.stats import RunStats
                st=RunStats(a["id"])
                progs=[w for w in mw.warehouse if w.get("account_ref")==a["id"]]
                pid=progs[0]["id"] if progs else ""
                running=pid in mw._proc_status
                s._json({"account_name":a.get("name",""),"running":running,"stats":st._data})
            def _handle_account_screenshot(s,p):
                try: idx=int(p.split("/")[3])
                except: return s._json({"error":"bad index"},400)
                if idx<0 or idx>=len(mw.accounts): return s._json({"error":"not found"},404)
                a=mw.accounts[idx]; addr=a.get("adb_address",""); adb=a.get("adb_path","") or "adb"
                if not addr: return s._json({"error":"no adb address"},400)
                try:
                    r=subprocess.run([adb,"-s",addr,"exec-out","screencap","-p"],capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
                    if r.returncode!=0 or len(r.stdout)<100: return s._json({"error":"screencap failed"},500)
                    s.send_response(200); s.send_header("Content-Type","image/png"); s.send_header("Content-Length",str(len(r.stdout))); s.send_header("Cache-Control","no-cache"); s.end_headers()
                    s.wfile.write(r.stdout)
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_screenshots_list(s, p=None):
                path = p or s.path; aid = path.split("/")[3] if p else ""
                if not aid: return s._json({"error":"missing aid"},400)
                _proj = Path(__file__).parent.parent
                shot_root = _proj / "screenshots" / aid
                runs = []
                if shot_root.exists():
                    dirs = sorted(shot_root.glob("run_*"), key=lambda d: d.stat().st_mtime, reverse=True)[:20]
                    for d in dirs:
                        shots = sorted(d.glob("*.png"), key=lambda f: f.stat().st_mtime)
                        runs.append({
                            "dir": d.name,
                            "ts": d.stat().st_mtime,
                            "shots": [{"file": f.name, "ts": f.stat().st_mtime} for f in shots]
                        })
                a = next((x for x in mw.accounts if x["id"]==aid), None)
                s._json({"ok":True,"aid":aid,"name":a.get("name","") if a else "","runs":runs})
            def _handle_screenshot_file(s, p=None):
                path = p or s.path; parts = path.split("/")
                # /api/screenshots/file/{aid}/{run_dir}/{filename}
                if len(parts) < 7: return s._json({"error":"invalid path"},400)
                aid, run_dir, fname = parts[4], parts[5], parts[6]
                fp = Path(__file__).parent.parent / "screenshots" / aid / run_dir / fname
                if not fp.exists(): return s._json({"error":"not found"},404)
                s.send_response(200)
                s.send_header("Content-Type","image/png"); s.send_header("Content-Length",str(fp.stat().st_size)); s.send_header("Access-Control-Allow-Origin","*"); s.end_headers()
                s.wfile.write(fp.read_bytes())
            def _handle_screenshots_delete(s, body):
                aid = s.path.split("/")[3]; run_dir = body.get("run_dir","")
                if not aid or not run_dir: return s._json({"error":"missing aid or run_dir"},400)
                fp = Path(__file__).parent.parent / "screenshots" / aid / run_dir
                if not fp.exists(): return s._json({"error":"not found"},404)
                import shutil as _su
                _su.rmtree(str(fp))
                s._json({"ok":True})
            def _handle_export_logs(s):
                try:
                    import io, zipfile, tempfile
                    _proj = Path(__file__).parent.parent
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                        # debug.log
                        dl = _proj / "debug.log"
                        if dl.exists(): zf.write(str(dl), "debug.log")
                        # config.json
                        cf = _proj / "models" / "config.json"
                        if cf.exists(): zf.write(str(cf), "config.json")
                        # screenshots
                        ss = _proj / "screenshots"
                        if ss.exists():
                            for f in ss.rglob("*"):
                                if f.is_file():
                                    zf.write(str(f), f"screenshots/{f.relative_to(ss)}")
                        # MAA instance logs
                        inst = _proj / "services" / "maa" / "instances"
                        if inst.exists():
                            for d in inst.iterdir():
                                al = d / "debug" / "asst.log"
                                if al.exists(): zf.write(str(al), f"maa_instances/{d.name}/asst.log")
                                idir = d / "debug" / "interface"
                                if idir.exists():
                                    for f in idir.glob("*.png"):
                                        zf.write(str(f), f"maa_instances/{d.name}/interface/{f.name}")
                    data = buf.getvalue()
                    s.send_response(200)
                    s.send_header("Content-Type","application/zip")
                    s.send_header("Content-Disposition",'attachment; filename="maorch_logs.zip"')
                    s.send_header("Content-Length",str(len(data)))
                    s.end_headers()
                    s.wfile.write(data)
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_all_stats(s):
                from models.stats import RunStats
                result=[]
                for i,a in enumerate(mw.accounts):
                    st=RunStats(a["id"])
                    progs=[w for w in mw.warehouse if w.get("account_ref")==a["id"]]
                    pid=progs[0]["id"] if progs else ""
                    running=pid in mw._proc_status
                    result.append({"index":i,"account_name":a.get("name",""),"running":running,"total_runs":st.total_runs,"stats":st._data})
                s._json({"accounts":result})
            def _handle_stats_dashboard(s):
                from models.stats import RunStats
                from datetime import datetime as _dt, timedelta as _td
                today = _dt.now()
                weekdays = ["周一","周二","周三","周四","周五","周六","周日"]
                # heatmap[day_of_week][hour] = run_count
                heatmap = [[0]*24 for _ in range(7)]
                daily_runs = {}
                daily_drops = {}
                all_drops = {}
                total_runs = 0
                for a in mw.accounts:
                    st = RunStats(a["id"])
                    for run in st._data.get("runs", []):
                        total_runs += 1
                        try:
                            dt = _dt.strptime(run["ts"], "%Y-%m-%d %H:%M:%S")
                        except:
                            continue
                        dow = dt.weekday()  # 0=Mon
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
                # Today's count
                today_key = today.strftime("%Y-%m-%d")
                today_runs = daily_runs.get(today_key, 0)
                s._json({"ok":True,"summary":{"total_runs":total_runs,"today_runs":today_runs,"accounts":len(mw.accounts),"total_drops":sum(all_drops.values())},"heatmap":heatmap,"weekdays":weekdays,"daily_runs":daily_runs,"daily_drops":daily_drops,"top_materials":top_mats})
            def _handle_pipeline_start(s):
                try: mw._start_pipeline(); s._json({"ok":True})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_pipeline_stop(s):
                try: mw._stop_pipeline(); s._json({"ok":True})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_pipeline_pause(s,body):
                action=body.get("action","pause")
                try:
                    if hasattr(mw,'pipeline_thread') and mw.pipeline_thread and mw.pipeline_thread.isRunning():
                        if action=="pause": mw.pipeline_thread.pause(); s._json({"ok":True,"state":"paused"})
                        else: mw.pipeline_thread.resume(); s._json({"ok":True,"state":"running"})
                    else: s._json({"ok":False,"error":"no pipeline running"})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_account_launch(s,p):
                try: idx=int(p.split("/")[3])
                except: return s._json({"error":"bad index"},400)
                if idx<0 or idx>=len(mw.accounts): return s._json({"error":"not found"},404)
                try: mw._la(idx); _log_op("启动",mw.accounts[idx].get("name","")); s._json({"ok":True})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_account_stop(s,p):
                try: idx=int(p.split("/")[3])
                except: return s._json({"error":"bad index"},400)
                if idx<0 or idx>=len(mw.accounts): return s._json({"error":"not found"},404)
                aid=mw.accounts[idx]["id"]
                runner=getattr(mw,'runner',None)
                if not runner: return s._json({"error":"runner not available"},500)
                try: runner.stop(aid); _log_op("停止",mw.accounts[idx].get("name","")); s._json({"ok":True})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_logs(s,path):
                qs=path.split("?")[-1] if "?" in path else ""
                lines=50
                for kv in qs.split("&"):
                    if kv.startswith("lines="):
                        try: lines=int(kv.split("=")[1])
                        except: pass
                lp=Path(__file__).parent.parent/"debug.log"
                if lp.exists():
                    try:
                        content=lp.read_text(encoding="utf-8",errors="replace").strip().split("\n")[-lines:]
                        s._json({"lines":content})
                    except: s._json({"error":"read failed"},500)
                else: s._json({"lines":[]})
            def _handle_maa_log(s,path):
                qs=path.split("?")[-1] if "?" in path else ""
                params={}
                for kv in qs.split("&"):
                    if "=" in kv: k,v=kv.split("=",1); params[k]=v
                aid=params.get("aid","")
                lines=int(params.get("lines",100))
                if not aid: return s._json({"error":"missing aid"},400)
                a=next((x for x in mw.accounts if x["id"]==aid),None)
                if not a: return s._json({"error":"account not found"},404)
                for w in mw.warehouse:
                    if w.get("account_ref")==aid:
                        lp=Path(w.get("path","")).parent/"debug"/"asst.log"
                        if lp.exists():
                            try:
                                content=lp.read_text(encoding="utf-8",errors="replace").strip().split("\n")[-lines:]
                                s._json({"lines":content,"name":a.get("name","")})
                            except: s._json({"error":"read failed"},500)
                        else: s._json({"lines":[],"name":a.get("name","")})
                        return
                s._json({"lines":[],"name":a.get("name",""),"error":"no MAA instance bound"})
            def _handle_oplog(s):
                s._json({"ok":True,"ops":_OPLOG[-50:]})
            def _handle_config_sync(s,body):
                acct_name=body.get("account_name",""); gui_json=body.get("gui_json")
                if not acct_name or not isinstance(gui_json,dict): return s._json({"error":"invalid fields"},400)
                a=next((a for a in mw.accounts if a.get("name")==acct_name),None)
                if not a: return s._json({"error":"account not found"},404)
                progs=[w for w in mw.warehouse if w.get("account_ref")==a["id"]]
                if not progs: return s._json({"error":"no MAA bound"},400)
                try:
                    inst_path=Path(progs[0]["path"]).resolve()
                    parts=inst_path.parts
                    if not any(parts[i:i+2]==("maa","instances") for i in range(len(parts)-1)):
                        return s._json({"error":"invalid path"},403)
                    d=inst_path.parent/"config"
                    d.mkdir(parents=True,exist_ok=True)
                    (d/"gui.json").write_text(json.dumps(gui_json,ensure_ascii=False,indent=2),encoding="utf-8")
                    (d/"gui.new.json").write_text(json.dumps(gui_json,ensure_ascii=False,indent=2),encoding="utf-8")
                    s._json({"ok":True})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_queue_status(s):
                lq=getattr(mw,"launch_queue",None)
                if not lq: return s._json({"error":"queue not available"},500)
                pending=[]
                src_map={"manual":"手动","schedule":"定时","sanity":"理智"}
                for e in sorted(lq._pending,key=lambda x:x.sort_key):
                    a=next((x for x in mw.accounts if x["id"]==e.account_id),None)
                    pending.append({"account_id":e.account_id,"account_name":a.get("name","") if a else "","source":src_map.get(e.source,e.source),"priority":e.sort_key[0],"not_before":e.not_before.strftime("%Y-%m-%d %H:%M:%S"),"suspended":a.get("suspended",False) if a else False})
                active=list(lq._active_emus.values())
                s._json({"pending":pending,"active":active,"pending_count":len(pending),"active_count":len(active),"paused":lq._paused})
            def _handle_queue_enqueue(s,body):
                lq=getattr(mw,"launch_queue",None)
                if not lq: return s._json({"error":"queue not available"},500)
                idx=body.get("account_index",-1)
                if isinstance(idx,str):
                    try: idx=int(idx)
                    except: idx=-1
                if idx<0 or idx>=len(mw.accounts): return s._json({"error":"invalid account_index"},400)
                aid=mw.accounts[idx]["id"]
                source=body.get("source","manual")
                priority=body.get("priority",0)
                not_before_str=body.get("not_before","")
                not_before=None
                if not_before_str:
                    from datetime import datetime as dt
                    try: not_before=dt.strptime(not_before_str,"%Y-%m-%d %H:%M:%S")
                    except: pass
                lq.enqueue(aid,source,priority,not_before)
                name=mw.accounts[idx].get("name","")
                _log_op("入队",name)
                s._json({"ok":True,"account_id":aid,"pending_count":lq.pending_count})
            def _handle_queue_dequeue(s,body):
                lq=getattr(mw,"launch_queue",None)
                if not lq: return s._json({"error":"queue not available"},500)
                aid=body.get("account_id","")
                if not aid:
                    idx=body.get("account_index",-1)
                    if isinstance(idx,str):
                        try: idx=int(idx)
                        except: idx=-1
                    if 0<=idx<len(mw.accounts): aid=mw.accounts[idx]["id"]
                if not aid: return s._json({"error":"invalid account"},400)
                lq.dequeue(aid)
                ac=next((x for x in mw.accounts if x["id"]==aid),None)
                _log_op("出队",ac.get("name","") if ac else aid)
                s._json({"ok":True,"account_id":aid,"pending_count":lq.pending_count})
            def _handle_accounts(s):
                try:
                    data=[]
                    for a in mw.accounts:
                        aid=a.get("id","")
                        running=mw.launch_queue.is_running(aid) if hasattr(mw,'launch_queue') else False
                        queued=mw.launch_queue.is_queued(aid) if hasattr(mw,'launch_queue') else False
                        data.append({"id":aid,"name":a.get("name",""),"game_client":a.get("game_client",""),"emu_instance_index":a.get("emu_instance_index",""),"adb_address":a.get("adb_address",""),"running":running,"queued":queued,"failures":a.get("consecutive_failures",0),"dispatch_id":a.get("dispatch_id",""),"suspended":a.get("suspended",False)})
                    s._json({"ok":True,"accounts":data})
                except Exception as e: s._json({"ok":False,"error":str(e)})
            def _handle_account_config(s,p):
                try:
                    idx=int(p.split("/")[3])
                    if idx<0 or idx>=len(mw.accounts): return s._json({"error":"not found"},404)
                    a=mw.accounts[idx]
                    s._json({"ok":True,"account_id":a["id"],"task_settings":a.get("task_settings",{})})
                except ValueError: s._json({"error":"bad index"},400)
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_get_config(s):
                try:
                    cfg=mw.config
                    s._json({"ok":True,"config":{"maa_version":cfg.get("maa_version",""),"parallel_max":cfg.get("parallel_max",3),"appearance_mode":cfg.get("appearance_mode","Dark"),"schedule_mode":cfg.get("schedule_mode","daily"),"maa_instances":cfg.get("maa_instances",0),"api_port":cfg.get("api_port",19999),"smart_global":cfg.get("smart_global",{}),"webhook_url":cfg.get("webhook_url",""),"bind_address":cfg.get("bind_address","127.0.0.1"),"api_token":cfg.get("api_token","")}})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_get_smart(s):
                try:
                    s._json({"ok":True,"smart_global":mw.config.get("smart_global",{})})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_emulators(s):
                try:
                    from infrastructure.task_constants import detect_emu_instances
                    instances=detect_emu_instances()
                    s._json({"ok":True,"emulators":instances})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_create_account(s,body):
                try:
                    import uuid as _uuid
                    new_id=_uuid.uuid4().hex[:12]
                    acct={"id":new_id,"name":body.get("name",""),"game_client":body.get("game_client",""),"adb_address":body.get("adb_address",""),"emu_instance_index":body.get("emu_instance_index",""),"task_settings":body.get("task_settings",{}),"dispatch_id":body.get("dispatch_id",""),"consecutive_failures":0}
                    mw.accounts.append(acct)
                    mw.config["accounts"]=mw.accounts
                    from models.config_manager import save_config
                    save_config(mw.config)
                    s._json({"ok":True,"id":new_id})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_edit_account(s,p,body):
                try:
                    idx=int(p.split("/")[3])
                    if idx<0 or idx>=len(mw.accounts): return s._json({"error":"not found"},404)
                    a=mw.accounts[idx]
                    for field in ("name","game_client","adb_address","emu_instance_index","dispatch_id","suspended","round_robin_deficit","stuck_timeout_min"):
                        if field in body: a[field]=body[field]
                    mw.config["accounts"]=mw.accounts
                    from models.config_manager import save_config
                    save_config(mw.config)
                    s._json({"ok":True})
                except ValueError: s._json({"error":"bad index"},400)
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_delete_account(s,p):
                try:
                    idx=int(p.split("/")[3])
                    if idx<0 or idx>=len(mw.accounts): return s._json({"error":"not found"},404)
                    del mw.accounts[idx]
                    mw.config["accounts"]=mw.accounts
                    from models.config_manager import save_config
                    save_config(mw.config)
                    s._json({"ok":True})
                except ValueError: s._json({"error":"bad index"},400)
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_save_account_config(s,p,body):
                try:
                    idx=int(p.split("/")[3])
                    if idx<0 or idx>=len(mw.accounts): return s._json({"error":"not found"},404)
                    mw.accounts[idx]["task_settings"]=body.get("task_settings",{})
                    mw.config["accounts"]=mw.accounts
                    from models.config_manager import save_config
                    save_config(mw.config)
                    s._json({"ok":True})
                except ValueError: s._json({"error":"bad index"},400)
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_queue_clear(s):
                try:
                    lq=getattr(mw,"launch_queue",None)
                    if not lq: return s._json({"error":"queue not available"},500)
                    lq._pending.clear()
                    lq._save_queue()
                    s._json({"ok":True})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_queue_pause(s):
                try:
                    lq=getattr(mw,"launch_queue",None)
                    if not lq: return s._json({"error":"queue not available"},500)
                    lq.pause()
                    s._json({"ok":True,"paused":True})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_queue_resume(s):
                try:
                    lq=getattr(mw,"launch_queue",None)
                    if not lq: return s._json({"error":"queue not available"},500)
                    lq.resume()
                    s._json({"ok":True,"paused":False})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_save_config(s,body):
                try:
                    for field in ("maa_version","parallel_max","appearance_mode","schedule_mode","deficit","stuck_timeout","webhook_url","bind_address","api_token"):
                        if field in body: mw.config[field]=body[field]
                    if "smart_global" in body: mw.config["smart_global"]=body["smart_global"]
                    from models.config_manager import save_config
                    save_config(mw.config)
                    s._json({"ok":True})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_save_smart(s,body):
                try:
                    sg = mw.config.setdefault("smart_global", {})
                    sg.update(body)
                    from models.config_manager import save_config
                    save_config(mw.config)
                    s._json({"ok":True})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_stop_all(s):
                try:
                    lq=getattr(mw,"launch_queue",None)
                    if not lq: return s._json({"error":"queue not available"},500)
                    count=lq.stop_all()
                    s._json({"ok":True,"stopped":count})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_smart_all(s,body):
                try:
                    from ui.side_bar import _run_smart_all
                    include_anni=body.get("include_anni",True)
                    only_anni=body.get("only_anni",False)
                    # Resume queue before scheduling
                    lq=getattr(mw,"launch_queue",None)
                    if lq and lq._paused:
                        lq.resume()
                    _run_smart_all(mw,include_anni,only_anni)
                    s._json({"ok":True})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_smart_selected(s,body):
                try:
                    from ui.side_bar import _run_smart_all
                    ids=body.get("account_ids",[])
                    include_anni=body.get("include_anni",True)
                    only_anni=body.get("only_anni",False)
                    lq=getattr(mw,"launch_queue",None)
                    if lq and lq._paused: lq.resume()
                    _run_smart_all(mw,include_anni,only_anni,account_ids=ids)
                    s._json({"ok":True})
                except Exception as e: s._json({"error":str(e)},500)
            def _serve_static(s, path):
                try:
                    import os, mimetypes
                    web_dir = os.path.join(os.path.dirname(__file__), "..", "ui", "web")
                    if path == "/":
                        path = "/index.html"
                    file_path = os.path.normpath(os.path.join(web_dir, path.lstrip("/")))
                    if not file_path.startswith(os.path.normpath(web_dir)):
                        return s._json({"error":"forbidden"},403)
                    if not os.path.isfile(file_path):
                        file_path = os.path.join(web_dir, "index.html")
                    content = open(file_path, "rb").read()
                    mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
                    s.send_response(200)
                    s.send_header("Content-Type", mime)
                    s.send_header("Content-Length", str(len(content)))
                    s.end_headers()
                    s.wfile.write(content)
                except Exception as e:
                    s._json({"error":str(e)},500)
            def _handle_rebuild(s):
                try:
                    from services.instance_pool import ensure_maa_instances_async
                    import threading
                    threading.Thread(target=lambda: ensure_maa_instances_async(mw.ctx, True), daemon=True).start()
                    s._json({"ok":True})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_maa_check_update(s):
                try:
                    import urllib.request, json, re
                    cur = mw.config.get("maa_version", "未知")
                    # Try GitHub releases page (works when api.github.com is blocked)
                    urls = [
                        "https://github.com/MaaAssistantArknights/MaaRelease/releases/latest",
                        "https://api.github.com/repos/MaaAssistantArknights/MaaRelease/releases/latest",
                    ]
                    tag = ""
                    for url in urls:
                        try:
                            headers = {"User-Agent":"MAAOrch"}
                            if "api.github.com" in url:
                                headers["Accept"] = "application/json"
                            req = urllib.request.Request(url, headers=headers)
                            resp = urllib.request.urlopen(req, timeout=10)
                            if "api.github.com" in url:
                                data = json.loads(resp.read())
                                tag = data.get("tag_name", "").lstrip("v")
                            else:
                                # Version is in the redirect URL: /releases/tag/v6.12.2
                                m = re.search(r'/tag/v?([\d.]+)', resp.url)
                                if m: tag = m.group(1)
                                if not tag:
                                    html = resp.read().decode("utf-8", errors="replace")
                                    m = re.search(r'/releases/tag/v?([\d.]+(?:-[\w.]+)?)', html)
                                    if m: tag = m.group(1)
                            if tag: break
                        except Exception:
                            continue
                    if tag:
                        s._json({"ok":True,"has_update":tag != cur,"current":cur,"latest":tag})
                    else:
                        s._json({"ok":False,"error":"无法获取版本信息", "manual_url":"https://github.com/MaaAssistantArknights/MaaAssistantArknights/releases"})
                except Exception as e: s._json({"ok":False,"error":"检查失败: "+str(e)[:60]})
            def _handle_maa_download_update(s):
                try:
                    import urllib.request, json, os, zipfile, io, shutil, threading, time
                    from pathlib import Path
                    from infrastructure.logger import Logger
                    _log = Logger("maa_update")
                    source_dir = Path(__file__).parent.parent / "services" / "maa" / "source"
                    def _do_update():
                        try:
                            req = urllib.request.Request("https://api.github.com/repos/MaaAssistantArknights/MaaAssistantArknights/releases/latest",
                                headers={"User-Agent":"MAAOrch"})
                            with urllib.request.urlopen(req, timeout=15) as r:
                                data = json.loads(r.read().decode())
                            tag = data.get("tag_name", "")
                            asset_url = ""
                            for a in data.get("assets", []):
                                if "win-x64" in a.get("name","") and a.get("name","").endswith(".zip"):
                                    asset_url = a.get("browser_download_url", "")
                                    break
                            if not asset_url:
                                _log.error("[MAA更新] 未找到 win-x64 更新包")
                                return
                            _log.info(f"下载 {tag}...")
                            dl_req = urllib.request.Request(asset_url, headers={"User-Agent":"MAAOrch"})
                            with urllib.request.urlopen(dl_req, timeout=120) as r:
                                data = r.read()
                            runner = getattr(mw, 'runner', None)
                            if runner:
                                for aid in list(runner._active.keys()):
                                    runner.stop(aid)
                                time.sleep(2)
                            _log.info(f"解压至 {source_dir}")
                            if source_dir.exists():
                                shutil.rmtree(source_dir, ignore_errors=True)
                            source_dir.mkdir(parents=True, exist_ok=True)
                            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                                zf.extractall(str(source_dir))
                            for f in source_dir.rglob("*"):
                                try: os.chmod(f, 0o755)
                                except: pass
                            from services.instance_pool import ensure_maa_instances_async
                            ensure_maa_instances_async(mw.ctx, True)
                            _log.info(f"{tag} 更新完成")
                        except Exception as e:
                            _log.error(f"失败: {e}")
                    threading.Thread(target=_do_update, daemon=True).start()
                    s._json({"ok":True,"message":"更新已开始后台下载"})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_orch_check_update(s):
                try:
                    import urllib.request, json
                    req=urllib.request.Request("https://api.github.com/repos/xiachk083-hub/MAAOrch/releases/latest",headers={"User-Agent":"MAAOrch-Updater"})
                    with urllib.request.urlopen(req,timeout=10) as r: data=json.loads(r.read().decode())
                    tag=data.get("tag_name",""); html_url=data.get("html_url","")
                    s._json({"ok":True,"latest":tag,"html_url":html_url})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_orch_download_update(s):
                try:
                    import webbrowser
                    webbrowser.open("https://github.com/xiachk083-hub/MAAOrch/releases/latest")
                    s._json({"ok":True})
                except Exception as e: s._json({"error":str(e)},500)
        try:
            cert = mw.config.get("ssl_cert", "") if hasattr(mw, 'config') else ""
            key = mw.config.get("ssl_key", "") if hasattr(mw, 'config') else ""
            self._httpd=HTTPServer((bind,self.port),Handler)
            if cert and key and Path(cert).exists() and Path(key).exists():
                import ssl
                self._httpd.socket = ssl.wrap_socket(self._httpd.socket, certfile=cert, keyfile=key, server_side=True)
                proto = "https"
            else:
                proto = "http"
            self.log_msg.emit(f"API 服务已启动: {proto}://{bind}:{self.port}")
            self._httpd.serve_forever()
        except OSError as e:
            self.log_msg.emit(f"API 启动失败 (端口 {self.port}): {e}")
        except Exception as e:
            self.log_msg.emit(f"API 服务异常: {e}")
    def stop_server(self):
        if self._httpd:
            try: self._httpd.shutdown()
            except Exception:
                pass
            self._httpd = None
