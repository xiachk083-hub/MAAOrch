from __future__ import annotations
import json,time,re
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
                if not token: return True
                h=s.headers.get("x-agent-token","")
                return h==token
            def _json(s,data,code=200):
                s.send_response(code); s.send_header("Content-Type","application/json"); s.send_header("Access-Control-Allow-Origin","*"); s.end_headers()
                s.wfile.write(json.dumps(data,ensure_ascii=False).encode())
            def do_OPTIONS(s):
                s.send_response(200);s.send_header("Access-Control-Allow-Origin","*");s.send_header("Access-Control-Allow-Headers","x-agent-token,content-type");s.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS");s.end_headers()
            def do_GET(s):
                if not s._check_rate_limit(): return
                if not s._check_auth(): return s._json({"error":"unauthorized"},401)
                p=s.path.split("?")[0]
                if p=="/api/status": return s._handle_status()
                if p.startswith("/api/account/") and p.endswith("/status"): return s._handle_account_status(p)
                if p.startswith("/api/account/") and p.endswith("/stats"): return s._handle_account_stats(p)
                if p=="/api/stats": return s._handle_all_stats()
                if p=="/api/queue": return s._handle_queue_status()
                if p=="/api/logs": return s._handle_logs(s.path)
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
                if p.startswith("/api/account/") and p.endswith("/launch"): return s._handle_account_launch(p)
                if p=="/api/config/sync": return s._handle_config_sync(body)
                if p=="/api/queue/enqueue": return s._handle_queue_enqueue(body)
                if p=="/api/queue/dequeue": return s._handle_queue_dequeue(body)
                s._json({"error":"not found"},404)
            def _handle_status(s):
                accts=[]
                for i,a in enumerate(mw.accounts):
                    progs=[w for w in mw.warehouse if w.get("account_ref")==a["id"]]
                    pid=progs[0]["id"] if progs else ""
                    running=pid in mw._proc_status
                    elapsed=0
                    if running and pid in mw._proc_start_times: elapsed=int(time.time()-mw._proc_start_times[pid])
                    accts.append({"name":a.get("name",""),"index":i,"running":running,"elapsed":elapsed,"adb":a.get("adb_address",""),"emu_index":a.get("emu_instance_index","")})
                pipeline_running=mw.pipeline_thread.isRunning() if hasattr(mw,'pipeline_thread') and mw.pipeline_thread else False
                s._json({"accounts":accts,"pipeline_running":pipeline_running})
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
                from stats import RunStats
                st=RunStats(a["id"])
                progs=[w for w in mw.warehouse if w.get("account_ref")==a["id"]]
                pid=progs[0]["id"] if progs else ""
                running=pid in mw._proc_status
                s._json({"account_name":a.get("name",""),"running":running,"stats":st._data})
            def _handle_all_stats(s):
                from stats import RunStats
                result=[]
                for i,a in enumerate(mw.accounts):
                    st=RunStats(a["id"])
                    progs=[w for w in mw.warehouse if w.get("account_ref")==a["id"]]
                    pid=progs[0]["id"] if progs else ""
                    running=pid in mw._proc_status
                    result.append({"index":i,"account_name":a.get("name",""),"running":running,"total_runs":st.total_runs,"stats":st._data})
                s._json({"accounts":result})
            def _handle_pipeline_start(s):
                mw._start_pipeline(); s._json({"ok":True})
            def _handle_pipeline_stop(s):
                mw._stop_pipeline(); s._json({"ok":True})
            def _handle_pipeline_pause(s,body):
                action=body.get("action","pause")
                if hasattr(mw,'pipeline_thread') and mw.pipeline_thread and mw.pipeline_thread.isRunning():
                    if action=="pause": mw.pipeline_thread.pause(); s._json({"ok":True,"state":"paused"})
                    else: mw.pipeline_thread.resume(); s._json({"ok":True,"state":"running"})
                else: s._json({"ok":False,"error":"no pipeline running"})
            def _handle_account_launch(s,p):
                try: idx=int(p.split("/")[3])
                except: return s._json({"error":"bad index"},400)
                if idx<0 or idx>=len(mw.accounts): return s._json({"error":"not found"},404)
                mw._la(idx); s._json({"ok":True})
            def _handle_logs(s,path):
                qs=path.split("?")[-1] if "?" in path else ""
                lines=50
                for kv in qs.split("&"):
                    if kv.startswith("lines="):
                        try: lines=int(kv.split("=")[1])
                        except: pass
                lp=Path(__file__).parent/"debug.log"
                if lp.exists():
                    try:
                        content=lp.read_text(encoding="utf-8",errors="replace").strip().split("\n")[-lines:]
                        s._json({"lines":content})
                    except: s._json({"error":"read failed"},500)
                else: s._json({"lines":[]})
            def _handle_config_sync(s,body):
                acct_name=body.get("account_name",""); gui_json=body.get("gui_json")
                if not acct_name or not gui_json: return s._json({"error":"missing fields"},400)
                a=next((a for a in mw.accounts if a.get("name")==acct_name),None)
                if not a: return s._json({"error":"account not found"},404)
                progs=[w for w in mw.warehouse if w.get("account_ref")==a["id"]]
                if not progs: return s._json({"error":"no MAA bound"},400)
                try:
                    d=Path(progs[0]["path"]).parent/"config"
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
