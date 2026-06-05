import sys,json,os,re
from pathlib import Path
from datetime import datetime
from utils import make_id, parse_maa_version

CONFIG_FILE=Path(__file__).parent/"config.json"
STARTUP_DIR=Path(os.environ['APPDATA'])/'Microsoft'/'Windows'/'Start Menu'/'Programs'/'Startup'

DEFAULT_CONFIG={"version":5,"appearance_mode":"Dark","window_geometry":"960x650","auto_start":False,
    "minimize_to_tray":True,"check_update_on_start":True,    "schedule":{"enabled":False,"type":"daily","time":"08:00","days_of_week":[]},"webhook_url":"",
    "api_port":19999,"api_token":"","warehouse":[],"groups":[],"accounts":[]}

def migrate_v4_to_v5(data):
    data.setdefault("accounts",[]); data.setdefault("check_update_on_start",True)
    for a in data.get("accounts",[]): a.setdefault("task_settings",{}); a.setdefault("sync_tasks",False); a.setdefault("account_switch",""); a.setdefault("emu_path",""); a.setdefault("emu_launch",False); a.setdefault("emu_wait",30); a.setdefault("emu_add_cmd",""); a.setdefault("emu_instance_index",""); a.setdefault("emu_instance_name",""); a.setdefault("post_action",""); a.setdefault("start_minimized",False); a.setdefault("start_directly",False); a.setdefault("adb_fail_launch_emu",False); a.setdefault("adb_retry",0); a.setdefault("stats",{})
    data.setdefault("webhook_url","")
    for w in data.get("warehouse",[]):
        for k,v in [("maa_type","general"),("maa_version",""),("update_channel","Stable"),
                     ("auto_update",False),("account_ref",""),("launch_mode","gui"),
                     ("task_pipeline",""),("guard_enabled",False),("guard_max_restart",3),
                     ("guard_capture_log",False)]: w.setdefault(k,v)
        w.setdefault("env",w.get("env",{}))
        if w.get("maa_type")=="general" and Path(w.get("path","")).stem.lower() in ("maa","maa.exe"):
            w["maa_type"]="maa"
            if not w["maa_version"]:
                v=parse_maa_version(w.get("path",""))
                if v: w["maa_version"]=v
    data["version"]=5; return data

def load_config():
    try:
        if CONFIG_FILE.exists():
            data=json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            ver=data.get("version",0)
            if ver in (2,3):
                data.setdefault("window_geometry","900x620"); data.setdefault("auto_start",False)
                data.setdefault("minimize_to_tray",True)
                warehouse=[]
                for g in data.get("groups",[]):
                    for p in g.get("programs",[]):
                        pth=p.get("path","")
                        ex=next((w for w in warehouse if w["path"]==pth),None)
                        pid=ex["id"] if ex else make_id()
                        if not ex: warehouse.append({"id":pid,"path":pth,"args":p.get("args",[]),"cwd":p.get("cwd",""),"env":{}})
                        pd=p.get("pre_delay",0); p.clear(); p["ref"]=pid; p["pre_delay"]=pd
                data["warehouse"]=warehouse; data["version"]=4
            if ver==4: data=migrate_v4_to_v5(data)
            if data.get("version",0)>=5:
                # Sanitize adb_address: fix encoding artifacts like "27.0.0.1" -> "127.0.0.1"
                for a in data.get("accounts",[]):
                    raw=a.get("adb_address","")
                    if raw and not raw.startswith("127.0.0.1:"):
                        m=re.search(r':(\d+)$',raw)
                        if m: a["adb_address"]="127.0.0.1:"+m.group(1)
                return data
    except: pass
    return dict(DEFAULT_CONFIG)

def save_config(data):
    try: CONFIG_FILE.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    except Exception as e:
        try: (Path(__file__).parent/"debug.log").open("a",encoding="utf-8").write(f"[ERR] save_config: {e}\n")
        except: pass

def set_auto_start(enabled):
    bp=STARTUP_DIR/"流水线启动器.bat"
    if enabled: bp.write_text(f'@start "" "{sys.executable}" "{Path(__file__).parent/"main.pyw"}"\n',encoding="utf-8")
    elif bp.exists(): bp.unlink()
