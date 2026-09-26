"""等战斗开始后 ~35s 抓一张有干员的帧，用于朝向对账。"""
import sys, time, cv2
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
import screen_state as SS
sc=AdbScreen("2"); sc.ensure_connected()
t0=None
for i in range(120):
    f=sc.capture()
    if f is None: time.sleep(3); continue
    st=SS.state_of(f)[0]
    if t0 is None and st=="battle": t0=time.time(); print("战斗起算", flush=True)
    if t0 and time.time()-t0>=35:
        cv2.imwrite("data/agent_run/_face35.png", f); print("已存 _face35.png", flush=True); break
    time.sleep(3)
