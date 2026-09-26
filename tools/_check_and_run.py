"""退场 → 看 GA-EX-2 节点颜色（判第一把是否三星）→ 用修好的 runner 重起 v10 方案。"""
import sys, time, pathlib, subprocess, cv2, numpy as np
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
import screen_state as SS
PROJ=pathlib.Path('.').resolve()
sc=AdbScreen("2"); sc.ensure_connected()
def st():
    f=sc.capture(); return SS.state_of(f) if f is not None else ("other",0), f
# 退场
sc.run_shell(["input","tap","80","45"]); time.sleep(1.8)          # 齿轮
sc.run_shell(["input","tap","960","574"]); time.sleep(2.5)        # 放弃行动
for j in range(8):
    s,f=st(); print(f"  退场{j}) {s}")
    if s in ("detail","form"): break
    if s=="exmap":
        cv2.imwrite("data/agent_run/_map_check.png", f)
        # 节点颜色：EX-1(已亮,作对照) vs EX-2
        def node_color(x0,y0,x1,y1):
            r=f[y0:y1, x0:x1].reshape(-1,3).astype(float)
            hsv=cv2.cvtColor(r.reshape(-1,1,3).astype(np.uint8), cv2.COLOR_BGR2HSV).reshape(-1,3)
            return r.mean(0), hsv[:,0].mean(), hsv[:,1].mean()
        for tag,(x0,y0,x1,y1) in {"GA-EX-1(对照)":(330,140,420,190),"GA-EX-2(目标)":(330,250,420,300)}.items():
            bgr,h,s=node_color(x0,y0,x1,y1)
            print(f"    {tag}: BGR={bgr.round(0)} H={h:.0f} S={s:.0f}")
        sc.run_shell(["input","tap","370","275"]); time.sleep(2.5); continue
    sc.run_shell(["input","tap","640","660"]); time.sleep(1.6)
s,f=st(); print("最终:",s)
if s in ("detail","form"):
    subprocess.Popen([sys.executable,str(PROJ/"tools"/"clear_runner.py"),"--emu","2",
      "--stage","act16side_ex02","--node","370,275",
      "--plans",str(PROJ/"data/copilot/ga_derived/act16side_ex02_derived_v10.json"),"--max-tries","1"],
      stdout=open("data/agent_run/_v11.log","w",encoding="utf-8"),stderr=subprocess.STDOUT)
    print("→ v11（修好自愈后重跑同一方案）已起跑")
else: print("没回到关卡页，不盲起")
