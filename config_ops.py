from __future__ import annotations
import json
from pathlib import Path
from task_constants import find_mumu_cli
from callbacks import ServiceContext

class ConfigService:
    """MAA config injection — writes gui.json / gui.new.json / TOML task configs."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    def gtc(self, ac: dict, w: dict) -> str | None:
        pl=w.get("task_pipeline","")
        if not pl: return None
        ts=[t.strip() for t in pl.split(",") if t.strip()]
        if not ts: return None
        md=Path(w["path"]).parent; td=md/"config"/"tasks"; td.mkdir(parents=True,exist_ok=True); ls=[]
        for t in ts:
            tl=t.lower()
            if tl=="startup": ls.extend(["[[tasks]]",'type="StartUp"',"[tasks.params]",f'client_type="{ac.get("game_client","Official")}"',"start_game_enabled=true"])
            elif tl=="fight": s=ac.get("fight_stage",""); ls.extend(["[[tasks]]",'type="Fight"'])
            if s: ls.extend(["[tasks.params]",f'stage="{s}"'])
            elif tl=="recruit": ls.extend(["[[tasks]]",'type="Recruit"',"[tasks.params]","refresh=true","select=[3,4,5]","confirm=[3,4,5]","times=4"])
            elif tl=="infrast": ls.extend(["[[tasks]]",'type="Infrast"',"[tasks.params]","mode=0",'facility=["Trade","Reception","Mfg","Control","Power","Office","Dorm"]',"dorm_trust_enabled=true"])
            elif tl=="mall": ls.extend(["[[tasks]]",'type="Mall"',"[tasks.params]","shopping=true"])
            elif tl=="award": ls.extend(["[[tasks]]",'type="Award"'])
            elif tl=="roguelike": ls.extend(["[[tasks]]",'type="Roguelike"',"[tasks.params]",'theme="Sarkaz"',"mode=0"])
            elif tl=="reclamation": ls.extend(["[[tasks]]",'type="Reclamation"',"[tasks.params]",'theme="Tales"'])
            elif tl=="closedown": ls.extend(["[[tasks]]",'type="CloseDown"'])
            ls.append("")
        (td/"daily.toml").write_text("\n".join(ls),encoding="utf-8")
        pd=md/"config"/"profiles"; pd.mkdir(parents=True,exist_ok=True); pls=["[connection]"]
        if ac.get("adb_address"): pls.append(f'address="{ac["adb_address"]}"')
        if ac.get("adb_path"): pls.append(f'adb_path="{ac["adb_path"].replace(chr(92),chr(92)+chr(92))}"')
        if ac.get("connection_preset"): pls.append(f'preset="{ac["connection_preset"]}"')
        pls.extend(["[instance_options]",f'touch_mode="{ac.get("touch_mode","ADB")}"']); (pd/"default.toml").write_text("\n".join(pls)+"\n",encoding="utf-8")
        return "daily"

    def inject(self, w: dict, ac: dict) -> None:
        p=w.get("path",""); md=Path(p).parent if p else None
        if not md or not md.exists(): return
        cd=md/"config"; cd.mkdir(parents=True,exist_ok=True); pl=w.get("task_pipeline",""); ptasks=[t.strip().lower() for t in pl.split(",") if t.strip()] if pl else []
        def _wcfg(fn):
            gj=cd/fn; d={}
            if gj.exists():
                try: d=json.loads(gj.read_text(encoding="utf-8"))
                except: d={}
            d.setdefault("Configurations",{}).setdefault("Default",{}); d.setdefault("Current","Default"); d.setdefault("Global",{}); c=d["Configurations"]["Default"]
            # Resource auto-update: let MAA self-update game data
            d.setdefault("Resource",{})["AutoUpdate"]=True
            if ac.get("adb_address"): c["Connect.Address"]=ac["adb_address"]
            if ac.get("adb_path"): c["Connect.AdbPath"]=ac["adb_path"]
            pr=ac.get("connection_preset",""); to=ac.get("touch_mode","")
            if pr: c["Connect.ConnectConfig"]={"MuMuPro":"MuMuEmulator12"}.get(pr,pr)
            if to: c["Connect.TouchMode"]={"MiniTouch":"minitouch","MaaTouch":"maatouch","ADB":"adb"}.get(to,"adb")
            c["Connect.AdbReplaced"]="True"; c["Connect.AutoDetect"]="False"; c["Connect.AlwaysAutoDetect"]="False"
            if ac.get("game_client"): c["Start.ClientType"]=ac["game_client"]
            sw=ac.get("account_switch","")
            if sw: c["Start.RunDirectly"]="False"; c["Start.StartGame"]="True"
            else: c["Start.RunDirectly"]="True"; c["Start.StartGame"]="True"
            # Start options
            if ac.get("start_minimized"): d.setdefault("Global",{})["GUI.MinimizeToTray"]="True"
            if ac.get("start_directly"): c["Start.RunDirectly"]="True"
            if ac.get("post_action"): c["MainFunction.PostActions"]='"'+ac["post_action"]+'"'
            if ac.get("adb_retry",0)>0: c["Connect.RetryOnDisconnected"]="True"
            # Emulator: unchecked = MAA handles, checked = we handle
            if ac.get("emu_instance_index","") and not ac.get("emu_launch"):
                cli=find_mumu_cli()
                if cli:
                    c["Start.EmulatorPath"]=str(cli)
                    c["Start.EmulatorAddCommand"]=f'control --vmindex {ac["emu_instance_index"]} launch'
                    c["Start.OpenEmulatorAfterLaunch"]="True"
                    if ac.get("emu_wait"): c["Start.EmulatorWaitSeconds"]=str(ac["emu_wait"])
            # Account switch in TaskQueue
            sw=ac.get("account_switch","")
            if sw and "TaskQueue" in c:
                for item in c["TaskQueue"]:
                    if item.get("TaskType","").lower()=="startup": item["AccountName"]=sw; break
            if ac.get("sync_tasks",False):
                ts=ac.get("task_settings",{})
                if ptasks and "TaskQueue" in c:
                    tq=c["TaskQueue"]
                    for item in tq:
                        tt=item.get("TaskType","").lower()
                        if tt in ptasks:
                            item["IsEnable"]=True
                            if tt in ts:
                                st=ts[tt]
                                if tt=="fight":
                                    if st.get("stage"): item["StagePlan"]=[st["stage"]]; item["IsStageManually"]=True
                                    if "medicine" in st: item["UseMedicine"]=st["medicine"]>0; item["MedicineCount"]=st["medicine"]
                                    if "use_stone" in st: item["UseStone"]=st["use_stone"]; item["StoneCount"]=st.get("stone",0)
                                    if "hide_series" in st: item["HideSeries"]=st["hide_series"]
                                    if "stage_reset_mode" in st: item["StageResetMode"]=st["stage_reset_mode"]
                                    if "use_expiring_medicine" in st: item["UseExpiringMedicine"]=st["use_expiring_medicine"]
                                    if "medicine_expire_days" in st: item["MedicineExpireDays"]=st["medicine_expire_days"]
                                    if "use_expire_medicine_for_activity" in st: item["UseExpireMedicineForActivity"]=st["use_expire_medicine_for_activity"]
                                elif tt=="recruit":
                                    if "select" in st: item["Level3Choose"]=3 in st["select"]; item["Level4Choose"]=4 in st["select"]; item["Level5Choose"]=5 in st["select"]
                                    if "confirm" in st: item["Confirm"]=st["confirm"]
                                    if "times" in st: item["MaxTimes"]=st["times"]
                                elif tt=="infrast":
                                    if "facilities" in st: item["RoomList"]=[{"Room":f} for f in st["facilities"]]
                                    if "drones" in st: item["UsesOfDrones"]=st["drones"]
                                elif tt=="mall":
                                    if "shopping" in st: item["Shopping"]=st["shopping"]
                                    if "blacklist" in st: item["BlackList"]=st["blacklist"]
                                elif tt=="award":
                                    if "award" in st: item["Award"]=st["award"]
                                    if "mail" in st: item["Mail"]=st["mail"]
                                elif tt=="roguelike":
                                    if "theme" in st: item["Theme"]=st["theme"]
                                    if "mode" in st: item["Mode"]="Exp" if st["mode"]==0 else "Investment"
                                elif tt=="reclamation":
                                    if "theme" in st: item["Theme"]=st["theme"]
                        else: item["IsEnable"]=False
                    c["TaskQueue"]=tq
            # Always inject fight_stage if set (regardless of sync_tasks)
            fs=ac.get("fight_stage","")
            if fs and "TaskQueue" in c:
                for item in c["TaskQueue"]:
                    if item.get("TaskType","").lower()=="fight":
                        item["StagePlan"]=[fs]
                        item["IsStageManually"]=True
                        self.ctx.log(f"inject关卡: {fs} → {fn}")
                        break
            elif fs:
                self.ctx.log(f"inject关卡: {fs} 但TaskQueue不存在于 {fn}")
            gj.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")
        _wcfg("gui.json"); _wcfg("gui.new.json")

    def inject_for_thread(self, w: dict, ac: dict) -> None:
        """Called from PipelineThread (no MainWindow ref needed)."""
        self.inject(w, ac)
