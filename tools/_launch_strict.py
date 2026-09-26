"""严格起跑：只有 state==detail 才允许起 MAA；form 先退回；并在开局 15/30/45/60s 定点存帧。"""
import sys, time, pathlib, subprocess, cv2, numpy as np, hashlib
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
import screen_state as SS
PROJ=pathlib.Path('.').resolve(); plan=sys.argv[1]; tag=pathlib.Path(plan).stem
sc=AdbScreen("2"); sc.ensure_connected()
def st(tagx=None):
    f=sc.capture()
    if f is not None and tagx: cv2.imwrite(f"data/agent_run/_{tagx}.png", f)
    return SS.state_of(f) if f is not None else ("other",0), f
s,f=st("lv_before"); print("起始:", s)
for attempt in range(8):
    if s=="detail": break
    if s=="form":
        print("  form（编队页残留）→ 按返回退回详情页")
        sc.run_shell(["input","keyevent","4"]); time.sleep(2.0)
    elif s=="exmap":
        print("  在地图 → 点 GA-EX-2 节点"); sc.run_shell(["input","tap","370","275"]); time.sleep(2.5)
    elif s in ("win","fail"):
        print("  结算页 → 点掉"); sc.run_shell(["input","tap","640","660"]); time.sleep(1.8)
    else:
        print(f"  {s} → 按一次返回+验证")
        h0=hashlib.md5(f.tobytes()).hexdigest()[:8]
        sc.run_shell(["input","keyevent","4"]); time.sleep(2.0)
        f2=sc.capture()
        if f2 is not None and hashlib.md5(f2.tobytes()).hexdigest()[:8]==h0:
            print("  !! 画面没变（返回无效）→ 停下，不盲点")
            sys.exit(1)
    s,f=st(f"lv_{attempt}")
s,c=st("lv_final"); print("最终:", s, c)
if s!="detail":
    print("不是详情页 → 不盲起"); sys.exit(1)
print("→ 干净开局，起", tag)
subprocess.Popen([sys.executable,str(PROJ/"tools"/"clear_runner.py"),"--emu","2",
  "--stage","act16side_ex02","--node","370,275","--plans",str(PROJ/plan),"--max-tries","1"],
  stdout=open(f"data/agent_run/_{tag}.log","w",encoding="utf-8"),stderr=subprocess.STDOUT)
# 定点帧：15/30/45/60/75s
for t in (15,30,45,60,75):
    time.sleep(15)
    f=sc.capture()
    if f is not None:
        cv2.imwrite(f"data/agent_run/_t{t:02d}.png", f); print(f"  存帧 t+{t}s")
