"""Test script to find the MAA crash cause."""
import sys, json, subprocess, time
from pathlib import Path

ACCOUNT = "c45a7845"
MAA_DIR = Path(__file__).parent / "accounts" / ACCOUNT / "MAA"
CONFIG_DIR = MAA_DIR / "config"
BACKUP_GUI = CONFIG_DIR / "gui.json.bak"
BACKUP_NEW = CONFIG_DIR / "gui.new.json.bak"
GUI = CONFIG_DIR / "gui.json"
NEW = CONFIG_DIR / "gui.new.json"


def restore_backup():
    for src, dst in [(BACKUP_GUI, GUI), (BACKUP_NEW, NEW)]:
        if src.exists():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def test_maa() -> bool:
    proc = subprocess.Popen([str(MAA_DIR / "MAA.exe")], cwd=str(MAA_DIR), shell=False)
    time.sleep(6)
    if proc.poll() is None:
        proc.kill()
        return True
    return False


def apply_inject(task_list, gui_file):
    d = {}
    if gui_file.exists():
        try:
            d = json.loads(gui_file.read_text(encoding="utf-8"))
        except Exception:
            d = {}
    d.setdefault("Configurations", {}).setdefault("Default", {})
    d.setdefault("Current", "Default")
    d.setdefault("Global", {})
    c = d["Configurations"]["Default"]
    d.setdefault("Resource", {})["AutoUpdate"] = True
    d.setdefault("Global", {}).setdefault("GUI.Localization", "zh-cn")
    d.setdefault("Global", {}).setdefault("GUI.MinimizeToTray", "False")
    d.setdefault("Global", {}).setdefault("GUI.UseTray", "True")

    task_lower = {t.lower() for t in task_list}
    for item in c.get("TaskQueue", []):
        tt = item.get("TaskType", "").lower()
        item["IsEnable"] = tt in task_lower

    gui_file.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


print("=" * 50)
print("MAA Crash Test")
print("=" * 50)

# Phase 1
print("\n[1] Backup config:", end=" ")
restore_backup()
print("OK" if test_maa() else "FAIL")

task_list = ["Award", "Fight", "Infrast", "Recruit", "Mall", "Depot", "CloseDown"]

# Phase 2a - gui.json only
print("[2a] Modify gui.json:", end=" ")
restore_backup()
apply_inject(task_list, GUI)
r = test_maa()
print("OK" if r else "FAIL")
if not r:
    print("  -> gui.json modification causes crash")
else:
    # Phase 2b - gui.new.json only
    print("[2b] Modify gui.new.json:", end=" ")
    restore_backup()
    apply_inject(task_list, NEW)
    r = test_maa()
    print("OK" if r else "FAIL")
    if not r:
        print("  -> gui.new.json modification causes crash")
    else:
        # Phase 2c - both
        print("[2c] Modify both:", end=" ")
        restore_backup()
        apply_inject(task_list, GUI)
        apply_inject(task_list, NEW)
        r = test_maa()
        print("OK" if r else "FAIL")

print("\nDone")
