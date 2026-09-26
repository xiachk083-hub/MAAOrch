import sys, time, cv2, pathlib
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
sc=AdbScreen("2"); sc.ensure_connected()
for i in range(30):
    f=sc.capture()
    if f is not None: cv2.imwrite(f"data/agent_run/_seq_{i:02d}.png", f)
    time.sleep(12)
