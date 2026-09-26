"""等当前这局出结果 → 清场 → 回到关卡详情页 → 起 v5（朝向修正版）。"""
import sys, cv2, time, pathlib, subprocess, json
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
import screen_state as SS
PROJ=pathlib.Path('.').resolve()
sc=AdbScreen("2"); sc.ensure_connected()
V5=PROJ/"data/copilot/ga_derived/act16side_ex02_derived_v5.json"
log=open("data/agent_run/_supervise_v5.log","w",encoding="utf-8")
def L(m):
    print(m,flush=True); log.write(m+"\n"); log.flush()
# 1) 等结果
res=None
for i in range(24):           # 最多 ~4 分钟
    f=sc.capture()
    if f is not None:
        st,c=SS.state_of(f)
        if i%4==0: L(f"  t+{i*10}s state={st}")
        if st in ("win","fail"):
            res=st; cv2.imwrite(f"data/agent_run/_result_{st}.png", f); L(f"结果页：{st}"); break
    time.sleep(10)
if res is None:
    L("4 分钟没出结果 → 保持观察，不动作"); sys.exit(0)
# 2) 清场回去
for i in range(6):
    sc.run_shell(["input","tap","640","660"]); time.sleep(1.6)
    f=sc.capture(); st,c=SS.state_of(f)
    L(f"  清场 {i+1}) state={st}")
    if st in ("detail","form"): break
    if st=="exmap":
        sc.run_shell(["input","tap","370","275"]); time.sleep(2.5)
        f=sc.capture(); st,c=SS.state_of(f); L(f"  点节点 → state={st}")
        if st=="detail": break
if res=="fail":
    L("失败 → 起 v5")
    subprocess.Popen([sys.executable, str(PROJ/"tools"/"clear_runner.py"), "--emu","2",
                      "--stage","act16side_ex02","--node","370,275","--plans",str(V5),"--max-tries","1"],
                     stdout=open("data/agent_run/_v5.log","w",encoding="utf-8"),
                     stderr=subprocess.STDOUT)
    L("v5 已在后台起跑（data/agent_run/_v5.log）")
else:
    L("通关了！GA-EX-2 普通模式拿下 → 突袭模式应该解锁了")
