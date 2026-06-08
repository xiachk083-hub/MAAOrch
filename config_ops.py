from __future__ import annotations
import json, re
from pathlib import Path
from datetime import datetime
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
            elif tl=="fight":
                s=ac.get("fight_stage",""); ls.extend(["[[tasks]]",'type="Fight"'])
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
        pls.extend(["[instance_options]",f'touch_mode="{ac.get("touch_mode","MiniTouch")}"']); (pd/"default.toml").write_text("\n".join(pls)+"\n",encoding="utf-8")
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
            if to: c["Connect.TouchMode"]={"MiniTouch":"minitouch","MaaTouch":"maatouch","ADB":"adb"}.get(to,"minitouch")
            c["Connect.AdbReplaced"]="True"; c["Connect.AutoDetect"]="False"; c["Connect.AlwaysAutoDetect"]="False"
            if ac.get("game_client"): c["Start.ClientType"]=ac["game_client"]
            sw=ac.get("account_switch","")
            if sw: c["Start.RunDirectly"]="False"; c["Start.StartGame"]="True"
            else: c["Start.RunDirectly"]="True"; c["Start.StartGame"]="True"
            # Start options
            if ac.get("start_minimized"): d.setdefault("Global",{})["GUI.MinimizeToTray"]="True"
            if ac.get("start_directly"): c["Start.RunDirectly"]="True"
            if ac.get("post_action"): c["MainFunction.PostActions"]=ac["post_action"]
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
                ts_raw=ac.get("task_settings",{})
                ts={k.lower():v for k,v in ts_raw.items()}
                if ptasks and "TaskQueue" in c:
                    tq=c["TaskQueue"]
                    for item in tq:
                        tt=item.get("TaskType","").lower()
                        if tt in ptasks:
                            item["IsEnable"]=True
                            if tt in ts:
                                st=ts[tt]
                                if tt=="startup":
                                    if "client_type" in st: item["ClientType"]=st["client_type"]
                                elif tt=="fight":
                                    if st.get("stage"): item["StagePlan"]=[st["stage"]]; item["IsStageManually"]=True
                                    if "medicine" in st: item["UseMedicine"]=st["medicine"]>0; item["MedicineCount"]=st["medicine"]
                                    if "times" in st: item["Times"]=st["times"]
                                    if "stage_reset_mode" in st: item["StageResetMode"]=st["stage_reset_mode"]
                                    if "use_expiring_medicine" in st: item["UseExpiringMedicine"]=st["use_expiring_medicine"]
                                    if "medicine_expire_days" in st: item["MedicineExpireDays"]=st["medicine_expire_days"]
                                    if "use_expire_medicine_for_activity" in st: item["UseExpireMedicineForActivity"]=st["use_expire_medicine_for_activity"]
                                    if "use_stone" in st: item["UseStone"]=st["use_stone"]
                                    if "stone" in st: item["StoneCount"]=st["stone"]
                                    if "enable_times_limit" in st: item["EnableTimesLimit"]=st["enable_times_limit"]
                                    if "annihilation_stage" in st: item["AnnihilationStage"]=st["annihilation_stage"]
                                    if "use_custom_annihilation" in st: item["UseCustomAnnihilation"]=st["use_custom_annihilation"]
                                    if "hide_unavailable_stage" in st: item["HideUnavailableStage"]=st["hide_unavailable_stage"]
                                elif tt=="recruit":
                                    if "select" in st: item["Level3Choose"]=3 in st["select"]; item["Level4Choose"]=4 in st["select"]; item["Level5Choose"]=5 in st["select"]
                                    if "confirm" in st: item["Confirm"]=st["confirm"]
                                    if "times" in st: item["MaxTimes"]=st["times"]
                                    if "refresh" in st: item["Refresh"]=st["refresh"]
                                    if "force_refresh" in st: item["ForceRefresh"]=st["force_refresh"]
                                    if "prefer_tag_enabled" in st: item["PreferTagEnabled"]=st["prefer_tag_enabled"]
                                    if "preserve_tag_enabled" in st: item["PreserveTagEnabled"]=st["preserve_tag_enabled"]
                                    if "preserve_tags" in st: item["PreserveTags"]=st["preserve_tags"]
                                    if "level3_time" in st: item["Level3Time"]=st["level3_time"]
                                    if "level4_time" in st: item["Level4Time"]=st["level4_time"]
                                    if "level5_time" in st: item["Level5Time"]=st["level5_time"]
                                elif tt=="infrast":
                                    if "facilities" in st: item["RoomList"]=[{"Room":f} for f in st["facilities"]]
                                    if "drones" in st: item["UsesOfDrones"]=st["drones"]
                                    if "mode" in st: item["Mode"]=st["mode"]
                                    if "dorm_threshold" in st: item["DormThreshold"]=st["dorm_threshold"]
                                    if "dorm_trust_enabled" in st: item["DormTrustEnabled"]=st["dorm_trust_enabled"]
                                    if "originium_shard_auto" in st: item["OriginiumShardAuto"]=st["originium_shard_auto"]
                                    if "reception_clue" in st: item["ReceptionClue"]=st["reception_clue"]
                                    if "send_clue" in st: item["SendClue"]=st["send_clue"]
                                    if "continue_training" in st: item["ContinueTraining"]=st["continue_training"]
                                    if "filename" in st: item["Filename"]=st["filename"]
                                elif tt=="mall":
                                    if "shopping" in st: item["Shopping"]=st["shopping"]
                                    if "blacklist" in st: item["BlackList"]=st["blacklist"]
                                    if "credit_fight" in st: item["CreditFight"]=st["credit_fight"]
                                    if "visit_friends" in st: item["VisitFriends"]=st["visit_friends"]
                                    if "first_list" in st: item["FirstList"]=st["first_list"]
                                    if "only_buy_discount" in st: item["OnlyBuyDiscount"]=st["only_buy_discount"]
                                    if "reserve_max_credit" in st: item["ReserveMaxCredit"]=st["reserve_max_credit"]
                                elif tt=="award":
                                    if "award" in st: item["Award"]=st["award"]
                                    if "mail" in st: item["Mail"]=st["mail"]
                                    if "free_gacha" in st: item["FreeGacha"]=st["free_gacha"]
                                    if "orundum" in st: item["Orundum"]=st["orundum"]
                                    if "mining" in st: item["Mining"]=st["mining"]
                                    if "special_access" in st: item["SpecialAccess"]=st["special_access"]
                                elif tt=="roguelike":
                                    if "theme" in st: item["Theme"]=st["theme"]
                                    if "mode" in st: item["Mode"]="Exp" if st["mode"]==0 else "Investment"
                                    if "difficulty" in st: item["Difficulty"]=st["difficulty"]
                                    if "squad" in st: item["Squad"]=st["squad"]
                                    if "roles" in st: item["Roles"]=st["roles"]
                                    if "core_char" in st: item["CoreChar"]=st["core_char"]
                                    if "start_count" in st: item["StartsCount"]=st["start_count"]
                                    if "investment" in st: item["Investment"]=st["investment"]
                                    if "invest_count" in st: item["InvestCount"]=st["invest_count"]
                                    if "stop_when_level_max" in st: item["StopWhenLevelMax"]=st["stop_when_level_max"]
                                    if "stop_when_deposit_full" in st: item["StopWhenDepositFull"]=st["stop_when_deposit_full"]
                                    if "use_support" in st: item["UseSupport"]=st["use_support"]
                                    if "start_with_seed" in st: item["StartWithSeed"]=st["start_with_seed"]
                                    if "seed" in st: item["Seed"]=st["seed"]
                                elif tt=="reclamation":
                                    if "theme" in st: item["Theme"]=st["theme"]
                                    if "mode" in st: item["Mode"]=st["mode"]
                                    if "tool_to_craft" in st: item["ToolToCraft"]=st["tool_to_craft"]
                                    if "max_craft_count" in st: item["MaxCraftCount"]=st["max_craft_count"]
                                    if "clear_store" in st: item["ClearStore"]=st["clear_store"]
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
            tmp = gj.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")
            tmp.replace(gj)
        _wcfg("gui.json"); _wcfg("gui.new.json")

    def inject_for_thread(self, w: dict, ac: dict) -> None:
        """Called from PipelineThread (no MainWindow ref needed)."""
        self.inject(w, ac)

    def inject_smart(self, task_list: list[str], ac: dict, config_dir: str) -> None:
        """Inject smart-generated task list into MAA config directory."""
        cd = Path(config_dir)
        cd.mkdir(parents=True, exist_ok=True)

        def _write(fn, use_v6=False):
            gj = cd / fn
            d = {}
            if gj.exists():
                try:
                    d = json.loads(gj.read_text(encoding="utf-8"))
                except Exception:
                    d = {}
            d.setdefault("Configurations", {}).setdefault("Default", {})
            d.setdefault("Current", "Default")
            d.setdefault("Global", {})
            c = d["Configurations"]["Default"]
            d.setdefault("Resource", {})["AutoUpdate"] = True
            if use_v6:
                c.setdefault("InfrastOrder", {})
            # MAA v6 reads GUI settings from gui.json Global section
            g = d.setdefault("Global", {})
            g.setdefault("GUI.Localization", "zh-cn")
            g.setdefault("GUI.MinimizeToTray", "False")
            g.setdefault("GUI.UseTray", "True")

            for stale in ("Start.Minimized", "Start.MinimizeDirectly"):
                c.pop(stale, None)

            if not use_v6:
                if ac.get("adb_address"):
                    c["Connect.Address"] = ac["adb_address"]
                if ac.get("adb_path"):
                    c["Connect.AdbPath"] = ac["adb_path"]
                pr = ac.get("connection_preset", "")
                to = ac.get("touch_mode", "")
                if pr:
                    c["Connect.ConnectConfig"] = {"MuMuPro": "MuMuEmulator12"}.get(pr, pr)
                if to:
                    c["Connect.TouchMode"] = {"MiniTouch": "minitouch", "MaaTouch": "maatouch", "ADB": "adb"}.get(to, "minitouch")
                c["Connect.AdbReplaced"] = "True"
                c["Connect.AutoDetect"] = "False"
                c["Connect.AlwaysAutoDetect"] = "False"
                if ac.get("game_client"):
                    c["Start.ClientType"] = ac["game_client"]
                c["Start.RunDirectly"] = "True"
                c["Start.StartGame"] = "True"
                smart_cfg = self.ctx.config.get("smart_global", {})
                post = smart_cfg.get("post_action", "")
                if post:
                    c["MainFunction.PostActions"] = post
                else:
                    c.pop("MainFunction.PostActions", None)

            emu_idx = ac.get("emu_instance_index", "")
            if emu_idx and not ac.get("emu_launch"):
                cli = find_mumu_cli()
                if cli:
                    c["Start.EmulatorPath"] = str(cli)
                    c["Start.EmulatorAddCommand"] = f'control --vmindex {emu_idx} launch'
                    c["Start.OpenEmulatorAfterLaunch"] = "True"
                    if ac.get("emu_wait"):
                        c["Start.EmulatorWaitSeconds"] = str(ac["emu_wait"])

            if use_v6:
                task_set = {t.lower() for t in task_list}
                run_annihilation = "annihilation" in task_set
                weekday_names = ["mon","tue","wed","thu","fri","sat","sun"]
                today_key = weekday_names[datetime.now().weekday()]
                day_stage = ac.get(f"smart_{today_key}", "") or ac.get("smart_stage", "")
                # Sanitize: only allow valid stage names
                day_stage = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff_\-]', '', day_stage) if day_stage else ""

                existing_tq = c.get("TaskQueue", [])
                has_fight = "fight" in task_set
                anni = (ac.get("smart_annihilation", "") or "Annihilation") if run_annihilation else ""
                # If no TaskQueue, try loading from source MAA config as template
                if not existing_tq:
                    try:
                        ver = self.ctx.config.get("maa_version", "")
                        src_gj = Path(__file__).parent / "maa" / ver / "config" / "gui.new.json" if ver else None
                        if src_gj and src_gj.exists():
                            src_d = json.loads(src_gj.read_text(encoding="utf-8"))
                            src_tq = src_d.get("Configurations", {}).get("Default", {}).get("TaskQueue", [])
                            if src_tq:
                                existing_tq = list(src_tq)
                    except Exception:
                        pass
                if existing_tq:
                    anni_appended = False
                    # Don't use _smart_inserted (causes MAA v6 WPF crash).
                    # Instead, track original item count to identify inserted items.
                    orig_count = len(existing_tq)
                    clean_tq = list(existing_tq)
                    # Deduplicate Fight-type items — keep only the ones we need
                    fight_items = [(i, item) for i, item in enumerate(clean_tq) if item.get("TaskType", "").lower() == "fight"]
                    if len(fight_items) > 1:
                        keep_idx = fight_items[-1][0]
                        clean_tq = [item for i, item in enumerate(clean_tq)
                                    if not (item.get("TaskType", "").lower() == "fight" and i != keep_idx)]
                    for item in clean_tq:
                        tt = item.get("TaskType", "").lower()
                        sp = item.get("StagePlan", [])
                        if sp:
                            item["StagePlan"] = [s for s in sp if isinstance(s, str) and re.match(r'^[a-zA-Z0-9\u4e00-\u9fff_\-]+$', s)]
                        item["IsEnable"] = tt in task_set
                        if tt == "fight" and run_annihilation:
                            if has_fight and not anni_appended:
                                item["UseCustomAnnihilation"] = False
                                item["AnnihilationStage"] = ""
                                item["UseMedicine"] = False
                                if day_stage:
                                    item["StagePlan"] = [day_stage]
                                    item["IsStageManually"] = True
                                else:
                                    item["IsStageManually"] = False
                                anni_item = dict(item)
                                anni_item["IsEnable"] = True
                                anni_item["UseCustomAnnihilation"] = True
                                anni_item["AnnihilationStage"] = anni
                                anni_item["StagePlan"] = [anni]
                                anni_item["IsStageManually"] = True
                                anni_item["StageResetMode"] = "Current"
                                anni_item["UseMedicine"] = True
                                anni_item["MedicineCount"] = 999
                                clean_tq.insert(clean_tq.index(item), anni_item)
                                anni_appended = True
                            elif not anni_appended:
                                item["IsEnable"] = True
                                item["UseCustomAnnihilation"] = True
                                item["AnnihilationStage"] = anni
                                item["StagePlan"] = [anni]
                                item["IsStageManually"] = True
                                item["StageResetMode"] = "Current"
                                item["UseMedicine"] = True
                                item["MedicineCount"] = 999
                                anni_appended = True
                        elif tt == "fight":
                            item["UseCustomAnnihilation"] = False
                            item["AnnihilationStage"] = ""
                            item["UseMedicine"] = False
                            item["StagePlan"] = [day_stage] if day_stage else []
                            item["IsStageManually"] = bool(day_stage)
                    c["TaskQueue"] = clean_tq
                    c["TaskSelectedIndex"] = 0
                    c.setdefault("DragItemIsChecked", {})
                else:
                    # No existing TaskQueue — build one matching MAA v6 default types
                    c["TaskQueue"] = []
                    TYPE_MAP = {
                        "StartUp": "StartUpTask", "Fight": "FightTask",
                        "Infrast": "InfrastTask", "Recruit": "RecruitTask",
                        "Mall": "MallTask", "Award": "AwardTask",
                        "Roguelike": "RoguelikeTask", "Reclamation": "ReclamationTask",
                        "UserDataUpdate": "UserDataUpdateTask",
                    }
                    # Only include task types MAA v6 actually supports
                    for task_type in ["StartUp", "Fight", "Infrast", "Recruit", "Mall",
                                      "Award", "Roguelike", "Reclamation", "UserDataUpdate"]:
                        enabled = task_type.lower() in task_set or (task_type == "UserDataUpdate" and "depot" in task_set)
                        item = {"$type": TYPE_MAP.get(task_type, task_type + "Task"),
                                "Name": "", "IsEnable": enabled, "TaskType": task_type}
                        if task_type == "UserDataUpdate":
                            item.update({"UpdateOperBox": True, "UpdateDepot": True,
                                         "TriggerInterval": "EveryTime", "IsTriggered": True})
                        if task_type == "Fight":
                            item.update({"UseMedicine": enabled, "MedicineCount": 999,
                                         "UseStone": False, "StoneCount": 0,
                                         "EnableTimesLimit": False, "TimesLimit": 2147483647,
                                         "IsDrGrandet": False, "UseExpiringMedicine": True,
                                         "MedicineExpireDays": 2, "UseExpireMedicineForActivity": True,
                                         "HideUnavailableStage": False, "IsStageManually": False,
                                         "UseOptionalStage": False, "UseStoneAllowSave": False,
                                         "HideSeries": False, "UseWeeklySchedule": False,
                                         "Series": 0, "StageResetMode": "Current",
                                         "StagePlan": [], "AnnihilationStage": "",
                                         "UseCustomAnnihilation": False})
                            if run_annihilation:
                                if has_fight:
                                    if day_stage:
                                        item["StagePlan"] = [day_stage]
                                        item["IsStageManually"] = True
                                    anni_item = dict(item)
                                    anni_item["UseCustomAnnihilation"] = True
                                    anni_item["AnnihilationStage"] = anni
                                    anni_item["StagePlan"] = ["Annihilation"]
                                    anni_item["IsStageManually"] = True
                                    anni_item["StageResetMode"] = "Current"
                                    anni_item["UseMedicine"] = True
                                    anni_item["MedicineCount"] = 999
                                    c["TaskQueue"].append(anni_item)
                                else:
                                    item["UseCustomAnnihilation"] = True
                                    item["AnnihilationStage"] = anni
                                    item["StagePlan"] = ["Annihilation"]
                                    item["IsStageManually"] = True
                                    item["UseMedicine"] = True
                                    item["MedicineCount"] = 999
                            elif day_stage:
                                item["StagePlan"] = [day_stage]
                                item["IsStageManually"] = True
                        c["TaskQueue"].append(item)
                    c["TaskSelectedIndex"] = 0
                    c.setdefault("DragItemIsChecked", {})
            else:
                c.pop("TaskQueue", None)
            tmp = gj.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(gj)

        _write("gui.json", use_v6=False)
        _write("gui.new.json", use_v6=True)
