"""等首次进入战斗后再起算，10/25/40 秒各存一帧（避开编队阶段的 45s 空转）。"""
import sys, time, cv2
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
import screen_state as SS
sc=AdbScreen("2"); sc.ensure_connected()
t0=None
for i in range(60):
    f=sc.capture()
    if f is None: time.sleep(5); continue
    st=SS.state_of(f)[0]
    if t0 is None and st in ("battle","other") and i>2: t0=time.time(); print("战斗起算", st, flush=True)
    if t0 is not None:
        el=time.time()-t0
        for m in (10,25,40,55):
            if el>=m and not globals().get(f"s{m}"):
                globals()[f"s{m}"]=True; cv2.imwrite(f"data/agent_run/_b{m:02d}.png", f); print(f"  存帧 +{m}s", flush=True)
        if el>60: break
    time.sleep(4)
