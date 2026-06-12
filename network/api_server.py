from __future__ import annotations
import json,time,re,os,hmac
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any
from PySide6.QtCore import QThread, Signal

class ApiServer(QThread):
    log_msg = Signal(str)

    def __init__(self, port: int, token: str, mw: Any) -> None:
        super().__init__(); self.port=port; self.token=token; self.mw=mw; self._httpd=None
    def run(self) -> None:
        mw=self.mw; token=self.token
        # Simple rate limiter: max 60 req/min per IP
        _rate_buckets: dict[str,list[float]] = {}
        def _check_rate(ip: str, limit: int = 60) -> bool:
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
                return hmac.compare_digest(h, token)
            def _json(s,data,code=200):
                s.send_response(code); s.send_header("Content-Type","application/json"); s.end_headers()
                s.wfile.write(json.dumps(data,ensure_ascii=False).encode())
            def do_OPTIONS(s):
                s.send_response(200);s.send_header("Content-Type","application/json");s.end_headers()
            def do_GET(s):
                if not s._check_rate_limit(): return
                if not s._check_auth(): return s._json({"error":"unauthorized"},401)
                p=s.path.split("?")[0]
                if p=="/api/status": return s._handle_status()
                if p=="/api/node/info": return s._handle_node_info()
                if p.startswith("/api/account/") and p.endswith("/status"): return s._handle_account_status(p)
                if p.startswith("/api/account/") and p.endswith("/stats"): return s._handle_account_stats(p)
                if p=="/api/stats": return s._handle_all_stats()
                if p=="/api/queue": return s._handle_queue_status()
                if p=="/api/logs": return s._handle_logs(s.path)
                if p=="/api/accounts": return s._handle_accounts()
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
                if p=="/api/config/sync": return s._handle_config_sync(body)
                if p=="/api/queue/enqueue": return s._handle_queue_enqueue(body)
                if p=="/api/queue/dequeue": return s._handle_queue_dequeue(body)
                if p=="/api/account": return s._handle_create_account(body)
                if p.startswith("/api/account/") and p.endswith("/edit"): return s._handle_edit_account(p, body)
                if p.startswith("/api/account/") and p.endswith("/delete"): return s._handle_delete_account(p)
                if p.startswith("/api/account/") and p.endswith("/config"): return s._handle_save_account_config(p, body)
                if p=="/api/queue/clear": return s._handle_queue_clear()
                if p=="/api/config": return s._handle_save_config(body)
                if p=="/api/settings/smart": return s._handle_save_smart(body)
                if p=="/api/action/stop_all": return s._handle_stop_all()
			if p=="/api/action/smart_all": return s._handle_smart_all(body)
				if p=="/api/instance/rebuild": return s._handle_rebuild()
                s._json({"error":"not found"},404)
            def _handle_status(s):
                accts=[]
                for i,a in enumerate(mw.accounts):
                    progs=[w for w in mw.warehouse if w.get("account_ref")==a["id"]]
                    pid=progs[0]["id"] if progs else ""
                    running=pid in getattr(mw,"_proc_status",set())
                    elapsed=0
                    if running and pid in mw._proc_start_times: elapsed=int(time.time()-mw._proc_start_times[pid])
                    accts.append({"name":a.get("name",""),"index":i,"running":running,"elapsed":elapsed,"adb":a.get("adb_address",""),"emu_index":a.get("emu_instance_index","")})
                pipeline_running=mw.pipeline_thread.isRunning() if hasattr(mw,'pipeline_thread') and mw.pipeline_thread else False
                s._json({"accounts":accts,"pipeline_running":pipeline_running})
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
                try: mw._la(idx); s._json({"ok":True})
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
                    pending.append({"account_id":e.account_id,"account_name":a.get("name","") if a else "","source":src_map.get(e.source,e.source),"priority":e.sort_key[0],"not_before":e.not_before.strftime("%Y-%m-%d %H:%M:%S")})
                active=list(lq._active_emus.values())
                s._json({"pending":pending,"active":active,"pending_count":len(pending),"active_count":len(active)})
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
                s._json({"ok":True,"account_id":aid,"pending_count":lq.pending_count})
            def _handle_accounts(s):
                try:
                    data=[]
                    for a in mw.accounts:
                        aid=a.get("id","")
                        running=mw.launch_queue.is_running(aid) if hasattr(mw,'launch_queue') else False
                        queued=mw.launch_queue.is_queued(aid) if hasattr(mw,'launch_queue') else False
                        data.append({"id":aid,"name":a.get("name",""),"game_client":a.get("game_client",""),"emu_instance_index":a.get("emu_instance_index",""),"adb_address":a.get("adb_address",""),"running":running,"queued":queued,"failures":a.get("consecutive_failures",0),"dispatch_id":a.get("dispatch_id","")})
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
                    s._json({"ok":True,"config":{"ma_version":cfg.get("ma_version",""),"parallel_max":cfg.get("parallel_max",3),"appearance_mode":cfg.get("appearance_mode","dark"),"schedule_mode":cfg.get("schedule_mode","daily"),"smart_global":cfg.get("smart_global",{})}})
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
                    for field in ("name","game_client","adb_address","emu_instance_index","dispatch_id"):
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
            def _handle_save_config(s,body):
                try:
                    for field in ("ma_version","parallel_max","appearance_mode","schedule_mode"):
                        if field in body: mw.config[field]=body[field]
                    if "smart_global" in body: mw.config["smart_global"]=body["smart_global"]
                    from models.config_manager import save_config
                    save_config(mw.config)
                    s._json({"ok":True})
                except Exception as e: s._json({"error":str(e)},500)
            def _handle_save_smart(s,body):
                try:
                    mw.config["smart_global"]=body
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
                    _run_smart_all(mw,include_anni,only_anni)
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
        try:
            self._httpd=HTTPServer(("127.0.0.1",self.port),Handler)
            self.log_msg.emit(f"API 服务已启动: http://127.0.0.1:{self.port}")
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
