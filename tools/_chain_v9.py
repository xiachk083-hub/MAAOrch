"""盯 v8：出结果 → 失败就退出→回详情页→自动上 v9（费用序版）。"""
import sys, time, pathlib, subprocess, cv2
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
import screen_state as SS
PROJ=pathlib.Path('.').resolve()
sc=AdbScreen("2"); sc.ensure_connected()
lg=open("data/agent_run/_chain_v9.log","w",encoding="utf-8")
def L(m): lg.write(m+"\n"); lg.flush()
def st():
    f=sc.capture(); return (SS.state_of(f) if f is not None else ("other",0)), f
res=None
for i in range(40):
    (s,c),f=st()
    if s in ("win","fail"):
        res=s; cv2.imwrite(f"data/agent_run/_v8_result_{s}.png", f); L(f"v8 结果={s}"); break
    if i%3==0: cv2.imwrite("data/agent_run/_v8_latest.png", f); L(f"[+{i*10}s] {s}")
    time.sleep(10)
if res is None: L("未见结果，停"); sys.exit(0)
if res=="fail":
    sc.run_shell(["input","tap","80","45"]); time.sleep(1.8)
    sc.run_shell(["input","tap","960","574"]); time.sleep(2.5)
    for j in range(8):
        (s,c),f=st(); L(f"  退场{j}) {s}")
        if s in ("detail","form"): break
        if s=="exmap": sc.run_shell(["input","tap","370","275"]); time.sleep(2.5); continue
        sc.run_shell(["input","tap","640","660"]); time.sleep(1.6)
    (s,c),_=st()
    if s in ("detail","form"):
        L("→ 起 v9")
        subprocess.Popen([sys.executable,str(PROJ/"tools"/"clear_runner.py"),"--emu","2",
            "--stage","act16side_ex02","--node","370,275",
            "--plans",str(PROJ/"data/copilot/ga_derived/act16side_ex02_derived_v9.json"),"--max-tries","1"],
            stdout=open("data/agent_run/_v9.log","w",encoding="utf-8"),stderr=subprocess.STDOUT)
        L("v9 已起跑（data/agent_run/_v9.log）")
    else: L(f"没回到关卡页({s})，不盲起")
else: L("v8 通关 → 核对三星/代理指挥")
