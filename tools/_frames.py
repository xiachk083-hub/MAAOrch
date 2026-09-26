import sys, time, cv2
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
sc=AdbScreen("2"); sc.ensure_connected()
for t in (15,30,45,60,75):
    time.sleep(15)
    f=sc.capture()
    if f is not None: cv2.imwrite(f"data/agent_run/_t{t:02d}.png", f)
