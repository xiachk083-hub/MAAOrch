import sys,json,os,re
from pathlib import Path
from datetime import datetime
from utils import make_id, parse_maa_version
from account import Account

CONFIG_FILE: Path = Path(__file__).parent/"config.json"
STARTUP_DIR: Path = Path(os.environ['APPDATA'])/'Microsoft'/'Windows'/'Start Menu'/'Programs'/'Startup'

DEFAULT_CONFIG: dict = {"version":5,"appearance_mode":"Dark","window_geometry":"960x650","auto_start":False,
    "minimize_to_tray":True,"check_update_on_start":True,"auto_update_maa":True,"maa_update_interval":6,
    "daigan_url":"",
    "parallel_max":1,"daily_batch_time":"",
    "schedule":{"enabled":False,"type":"daily","time":"08:00","days_of_week":[]},"webhook_url":"",
    "queue":[],"api_port":19999,"api_token":"","warehouse":[],"groups":[],"accounts":[],
    "maa_version":"","maa_instances":0,"maa_instances_version":"",
    "smart_global":{"threshold":80,"expiring_medicine":True,"medicine_days":2,"annihilation_enabled":True,
        "infrast_times":["04:00","16:00"],"recruit_enabled":True,"mall_enabled":True,"post_action":"ExitArknights,ExitSelf",
        "materials":[
            {"name":"固源岩","min":200,"priority":1,"enabled":True},
            {"name":"龙门币","min":50000,"priority":2,"enabled":True},
            {"name":"作战记录","min":30000,"priority":3,"enabled":True},
            {"name":"装置","min":100,"priority":4,"enabled":False},
            {"name":"聚酸酯","min":100,"priority":5,"enabled":False},
            {"name":"异铁","min":100,"priority":6,"enabled":False},
            {"name":"糖","min":100,"priority":7,"enabled":False},
            {"name":"酮凝集","min":100,"priority":8,"enabled":False}
        ]}}

def migrate_v4_to_v5(data: dict) -> dict:
    data.setdefault("accounts",[]); data.setdefault("check_update_on_start",True)
    for a in data.get("accounts",[]):
        a.setdefault("task_settings",{}); a.setdefault("sync_tasks",False); a.setdefault("account_switch","")
        a.setdefault("emu_path",""); a.setdefault("emu_launch",False); a.setdefault("emu_wait",30)
        a.setdefault("emu_add_cmd",""); a.setdefault("emu_instance_index",""); a.setdefault("emu_instance_name","")
        a.setdefault("post_action",""); a.setdefault("start_minimized",False); a.setdefault("start_directly",False)
        a.setdefault("adb_fail_launch_emu",False); a.setdefault("adb_retry",0); a.setdefault("stats",{})
        a.setdefault("stuck_timeout_min",0); a.setdefault("tags",""); a.setdefault("round_robin_deficit",0)
        a.setdefault("smart_stage",""); a.setdefault("smart_annihilation","")
        for d in ["mon","tue","wed","thu","fri","sat","sun"]: a.setdefault(f"smart_{d}","")
        a.setdefault("smart_materials_enabled",True)
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

def load_config() -> dict:
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
                data["warehouse"]=warehouse; data["version"]=4; ver=4
            if ver in (4,): data=migrate_v4_to_v5(data); ver=5
            if ver>=5:
                data.setdefault("maa_version", "")
                data.setdefault("maa_instances", 0)
                data.setdefault("smart_global", dict(DEFAULT_CONFIG["smart_global"]))
                for a in data.get("accounts",[]):
                    raw=a.get("adb_address","")
                    if raw and not raw.startswith("127.0.0.1:"):
                        m=re.match(r'^2?7\.0\.0\.1:(\d+)$',raw)
                        if m: a["adb_address"]="127.0.0.1:"+m.group(1)
                    a.setdefault("smart_stage",""); a.setdefault("smart_annihilation","")
                    for d in ["mon","tue","wed","thu","fri","sat","sun"]: a.setdefault(f"smart_{d}","")
                    a.setdefault("smart_materials_enabled",True)
                # Convert accounts to Account objects
                data["accounts"]=[Account.from_dict(a) for a in data["accounts"]]
                return data
    except Exception as e:
        try:
            with (Path(__file__).parent/"debug.log").open("a",encoding="utf-8") as f:
                f.write(f"[ERR] load_config: {e}\n")
        except:
            pass
        # Try loading the most recent backup before giving up
        try:
            bp = Path(__file__).parent / "backups"
            backups = sorted(bp.glob("config_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
            if backups:
                data = json.loads(backups[0].read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("accounts",[])
                    data["accounts"] = [Account.from_dict(a) for a in data["accounts"]]
                    return data
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(data: dict) -> None:
    from copy import deepcopy
    # Convert Account objects back to plain dicts (deepcopy to avoid mutating callers)
    out = deepcopy(data)
    if "accounts" in out:
        out["accounts"] = [a.to_dict() if hasattr(a, "to_dict") else a for a in out["accounts"]]
    # Atomic write: write to temp file in same directory then rename
    tmp = CONFIG_FILE.with_name("config.json.tmp")
    try:
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_FILE)
        # Create backup copy after successful save
        try:
            bp = CONFIG_FILE.parent / "backups"
            bp.mkdir(exist_ok=True)
            dst = bp / f"config_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            if not dst.exists():
                import shutil as _su
                _su.copy2(str(CONFIG_FILE), str(dst))
            for f in sorted(bp.glob("config_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[5:]:
                f.unlink()
        except Exception:
            pass
    except Exception as e:
        try:
            with (Path(__file__).parent/"debug.log").open("a",encoding="utf-8") as f:
                f.write(f"[ERR] save_config: {e}\n")
        except:
            pass

def set_auto_start(enabled: bool) -> None:
    bp=STARTUP_DIR/"流水线启动器.bat"
    if enabled: bp.write_text(f'@start "" pythonw "{Path(__file__).parent/"main.pyw"}"\n',encoding="utf-8")
    elif bp.exists(): bp.unlink()
