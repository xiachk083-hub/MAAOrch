"""Simulate exactly what MAAOrch's _spawn and inject_smart do."""
import sys, json, subprocess, time
from pathlib import Path

ACCOUNT = "c45a7845"
MAA_DIR = Path(__file__).parent / "accounts" / ACCOUNT / "MAA"
CONFIG_DIR = MAA_DIR / "config"
GUI = CONFIG_DIR / "gui.json"
NEW = CONFIG_DIR / "gui.new.json"

# Exactly what inject_smart does
def inject_smart():
    for fn in [GUI, NEW]:
        d = {}
        if fn.exists():
            try:
                d = json.loads(fn.read_text(encoding="utf-8"))
            except:
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
        fn.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

# Restore backup first
for src, dst in [(Path(__file__).parent / "accounts" / ACCOUNT / "MAA" / "config" / "gui.json.bak", GUI),
                 (Path(__file__).parent / "accounts" / ACCOUNT / "MAA" / "config" / "gui.new.json.bak", NEW)]:
    if src.exists():
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

task_list = ["Award", "Fight", "Infrast", "Recruit", "Mall", "Depot", "CloseDown"]
inject_smart()

# Exactly what _spawn does
exe = str(MAA_DIR / "MAA.exe")
cwd = str(MAA_DIR)
args = []
env = None

kwargs = {"shell": False, "cwd": cwd, "env": env}
print(f"  Starting MAA from {cwd}")
p = subprocess.Popen([exe] + args, **kwargs)
pid = p.pid
print(f"  PID={pid}")

# Wait like poll timer does
for i in range(6):
    time.sleep(1)
    if p.poll() is not None:
        print(f"  MAA EXITED after {i+1}s, code={p.returncode}")
        break
else:
    print(f"  MAA running after 6s (killing)")
    p.kill()
