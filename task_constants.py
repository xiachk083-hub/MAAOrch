import os,json,subprocess,time
from pathlib import Path
from PySide6.QtCore import QThread, Signal

TASK_NAMES={"StartUp":"启动游戏","Fight":"刷关作战","Recruit":"公开招募","Infrast":"基建换班",
    "Mall":"信用商店","Award":"领取奖励","Roguelike":"肉鸽探索","Reclamation":"生息演算","closedown":"关闭游戏"}
TASK_DEFAULTS={
    "StartUp":{"client_type":"Official"},
    "Fight":{"stage":"","medicine":0,"times":99,"use_expiring_medicine":True,"medicine_expire_days":2,"use_stone":False,"stone":0,"enable_times_limit":False,"stage_reset_mode":"Current","annihilation_stage":"Annihilation","use_custom_annihilation":False,"hide_unavailable_stage":False},
    "Recruit":{"select":[3,4,5],"confirm":[3,4,5],"times":4,"refresh":True,"force_refresh":True,"prefer_tag_enabled":True,"preserve_tag_enabled":False,"preserve_tags":"支援机械","level3_time":540,"level4_time":540,"level5_time":540},
    "Infrast":{"mode":"Normal","facilities":["Trade","Mfg","Control","Power","Reception","Office","Dorm"],"drones":"Money","dorm_threshold":30,"dorm_trust_enabled":True,"originium_shard_auto":True,"reception_clue":True,"send_clue":True,"continue_training":False,"filename":""},
    "Mall":{"shopping":True,"credit_fight":False,"visit_friends":True,"first_list":"招聘许可","blacklist":"碳;家具","only_buy_discount":False,"reserve_max_credit":False},
    "Award":{"award":True,"mail":False,"free_gacha":False,"orundum":False,"mining":False,"special_access":False},
    "Roguelike":{"theme":"Sarkaz","mode":0,"difficulty":15,"squad":"","roles":"","core_char":"","start_count":99999,"investment":True,"invest_count":999,"stop_when_level_max":False,"stop_when_deposit_full":False,"use_support":False,"start_with_seed":False,"seed":""},
    "Reclamation":{"theme":"Tales","mode":"ProsperityInSave","tool_to_craft":"","max_craft_count":16,"clear_store":False},
}
EMU_PRESETS=[
    {"name":"MuMu 12(默认)","type":"MuMuEmulator12","ports":[str(16384+i*32) for i in range(100)],"detect":"MuMu 12"},
    {"name":"MuMu 6(默认)","type":"MuMu","ports":["7555"],"detect":"MuMu 6"},
    {"name":"MuMu Pro","type":"MuMuPro","ports":["16384"],"detect":"MuMu 12"},
    {"name":"雷电 9(默认)","type":"LDPlayer","ports":["5555"],"detect":"雷电 9"},
    {"name":"雷电 4","type":"LDPlayer","ports":["5555"],"detect":"雷电 9"},
    {"name":"蓝叠 国际版","type":"BlueStacks","ports":["5555"],"detect":"蓝叠"},
    {"name":"蓝叠 中国版","type":"BlueStacks","ports":["5555"],"detect":"蓝叠"},
    {"name":"夜神","type":"Nox","ports":["62001"],"detect":"夜神"},
    {"name":"逍遥","type":"XYAZ","ports":["21503"],"detect":"逍遥"},
    {"name":"自定义","type":"General","ports":["5555"],"detect":""},
]

MUMU_INSTANCE_DIRS=[
    Path(os.environ.get("APPDATA",""))/"Netease"/"MuMuPlayer-12.0"/"vms",
    Path(os.environ.get("LOCALAPPDATA",""))/"Netease"/"MuMuPlayer-12.0"/"vms",
    Path("D:/Program Files/Netease/MuMuPlayer-12.0/vms"),
    Path("C:/Program Files/Netease/MuMuPlayer-12.0/vms"),
]

MUMU_CLI_CANDIDATES=[
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\mumu-cli.exe",
    r"C:\Program Files\Netease\MuMuPlayer-12.0\nx_main\mumu-cli.exe",
    r"C:\Program Files\Netease\MuMuPlayer\nx_main\mumu-cli.exe",
    r"D:\Program Files\Netease\MuMuPlayer-12.0\shell\mumu-cli.exe",
    r"D:\Program Files\Netease\MuMuPlayer-12.0\nx_main\mumu-cli.exe",
    r"C:\Program Files (x86)\Netease\MuMuPlayer\nx_main\mumu-cli.exe",
] + [str(Path(d)/"MuMuPlayer"/"nx_main"/"mumu-cli.exe") for d in [
    Path(os.environ.get("USERPROFILE","C:/")),
    Path(os.environ.get("HOMEDRIVE","D:/")),
] if (Path(d)/"MuMuPlayer"/"nx_main"/"mumu-cli.exe").exists()]
# Also check MUMU_CLI_HOME env var for custom installs
if (ev:=os.environ.get("MUMU_CLI_HOME","")):
    MUMU_CLI_CANDIDATES.insert(0,str(Path(ev)/"mumu-cli.exe"))

def find_mumu_cli() -> str | None:
    # Check known paths + USERPROFILE
    extra=[str(Path(os.environ.get("USERPROFILE","."))/"MuMuPlayer"/"nx_main"/"mumu-cli.exe")]
    for c in extra + MUMU_CLI_CANDIDATES:
        if Path(c).exists(): return c
    # Search drives for MuMuPlayer
    for drv in "CDEFGH":
        base=Path(f"{drv}:\\")
        if not base.exists(): continue
        try:
            for d in base.iterdir():
                if d.is_dir() and "mumu" in d.name.lower():
                    for sub in ["nx_main\\mumu-cli.exe","shell\\mumu-cli.exe"]:
                        p=d/sub
                        if p.exists(): return str(p)
                # Also check subdirectories (e.g. D:\Xiach\MuMuPlayer)
                if d.is_dir():
                    try:
                        for sd in d.iterdir():
                            if sd.is_dir() and "mumu" in sd.name.lower():
                                for sub in ["nx_main\\mumu-cli.exe","shell\\mumu-cli.exe"]:
                                    p=sd/sub
                                    if p.exists(): return str(p)
                    except PermissionError: pass
        except PermissionError: pass
        except Exception: pass
    return None

def detect_emu_instances() -> list[dict]:
    """Detect all emulator instances via mumu-cli or directory scan"""
    instances=[]
    cli=find_mumu_cli()
    if cli:
        try:
            r=subprocess.run([cli,"info","--vmindex","all"],capture_output=True,text=True,timeout=10,creationflags=CF,encoding="utf-8",errors="replace")
            if r.stdout.strip():
                data=json.loads(r.stdout)
                for idx,info in data.items():
                    if isinstance(info,dict):
                        instances.append({
                            "emu":"MuMu","name":info.get("name",idx),
                            "index":idx,"adb_port":str(info.get("adb_port","")),
                            "running":info.get("is_process_started",False) or info.get("is_android_started",False)
                        })
                return instances
        except: pass
    # Fallback: directory scan
    for vms_dir in MUMU_INSTANCE_DIRS:
        if vms_dir.exists():
            for vm in sorted(vms_dir.iterdir()):
                if vm.is_dir() and (vm/"config.json").exists():
                    try:
                        cfg=json.loads((vm/"config.json").read_text(encoding="utf-8"))
                        name=cfg.get("vm_name",vm.name)
                        adb_port=cfg.get("adb_port","")
                        instances.append({"emu":"MuMu 12","name":name,"index":vm.name,"adb_port":str(adb_port),"path":str(vm)})
                    except: pass
            if instances: break
    # MuMu 6 instances
    for base in [Path(os.environ.get("APPDATA",""))/"Netease"/"MuMu",
                 Path("D:/Program Files/Netease/MuMu/emulator/nemu/vms"),
                 Path("C:/Program Files/Netease/MuMu/emulator/nemu/vms")]:
        if base.exists():
            for vm in sorted(base.iterdir()):
                if vm.is_dir(): instances.append({"emu":"MuMu 6","name":vm.name,"index":vm.name,"adb_port":"","path":str(vm)})
            if instances: break
    # LDPlayer instances
    for base in [Path("C:/leidian/LDPlayer9/vms"),Path("D:/leidian/LDPlayer9/vms")]:
        if base.exists():
            for vm in sorted(base.iterdir()):
                if vm.is_dir(): instances.append({"emu":"雷电 9","name":vm.name,"index":vm.name,"adb_port":"","path":str(vm)})
            if instances: break
    return instances
CLIENT_TYPES={"Official":"官服","Bilibili":"B服","YoStarEN":"国际服","YoStarJP":"日服","YoStarKR":"韩服","txwy":"繁中"}
CF=subprocess.CREATE_NO_WINDOW

class EmuMonitor(QThread):
    updated=Signal(list)
    def run(self):
        while True:
            cli=find_mumu_cli()
            if cli:
                try:
                    r=subprocess.run([cli,"info","--vmindex","all"],capture_output=True,text=True,timeout=8,creationflags=CF,encoding="utf-8",errors="replace")
                    if r.stdout.strip():
                        data=json.loads(r.stdout)
                        results=[]
                        for idx,info in data.items():
                            if isinstance(info,dict):
                                results.append({"name":info.get("name",idx),"index":idx,"running":info.get("is_process_started",False) or info.get("is_android_started",False)})
                        self.updated.emit(results)
                except: pass
            time.sleep(30)
