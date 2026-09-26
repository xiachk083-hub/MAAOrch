"""不是三星就立刻退出：齿轮→放弃行动→清结算→回详情页→起 v5。"""
import sys, cv2, time, pathlib, subprocess
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
import screen_state as SS
PROJ=pathlib.Path('.').resolve()
sc=AdbScreen("2"); sc.ensure_connected()
def st():
    f=sc.capture()
    return SS.state_of(f) if f is not None else ("other",0.0)
print("1) 开齿轮菜单"); sc.run_shell(["input","tap","80","45"]); time.sleep(1.8)
s,_=st(); print("   ->", s); cv2.imwrite("data/agent_run/_q1.png", sc.capture())
print("2) 放弃行动"); sc.run_shell(["input","tap","960","574"]); time.sleep(2.5)
s,_=st(); print("   ->", s)
for i in range(8):
    s,c=st(); print(f"   {i}) {s}")
    if s in ("detail","form"): break
    if s=="exmap":
        sc.run_shell(["input","tap","370","275"]); time.sleep(2.5); continue
    sc.run_shell(["input","tap","640","660"]); time.sleep(1.6)
s,c=st(); print("最终 state:", s, c); cv2.imwrite("data/agent_run/_q_final.png", sc.capture())
if s in ("detail","form"):
    subprocess.Popen([sys.executable, str(PROJ/"tools"/"clear_runner.py"), "--emu","2",
                      "--stage","act16side_ex02","--node","370,275",
                      "--plans",str(PROJ/"data/copilot/ga_derived/act16side_ex02_derived_v5.json"),
                      "--max-tries","1"],
                     stdout=open("data/agent_run/_v5.log","w",encoding="utf-8"), stderr=subprocess.STDOUT)
    print("→ v5 已起跑（data/agent_run/_v5.log）")
else:
    print("没回到详情页，不盲目起 MAA")
