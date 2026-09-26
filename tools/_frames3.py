"""战斗起算后每 15s 存帧（到 150s），并在 win/fail 瞬间存 _crash.png。"""
import sys, time, cv2
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
import screen_state as SS
sc=AdbScreen("2"); sc.ensure_connected()
t0=None; saved=set()
for i in range(240):
    f=sc.capture()
    if f is None: time.sleep(3); continue
    st=SS.state_of(f)[0]
    if st in ("win","fail"):
        cv2.imwrite("data/agent_run/_crash.png", f); print("崩盘帧已存 _crash.png", st, flush=True); break
    if t0 is None:
        if st in ("battle","other") and i>1: t0=time.time()
    else:
        el=int(time.time()-t0); m=el//15*15
        if m>=15 and m<=150 and m not in saved:
            saved.add(m); cv2.imwrite(f"data/agent_run/_c{m:03d}.png", f); print(f"  存帧 +{m}s", flush=True)
    time.sleep(4)
