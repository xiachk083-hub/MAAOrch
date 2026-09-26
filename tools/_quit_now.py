import sys, time, cv2, pathlib
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
import screen_state as SS
sc=AdbScreen("2"); sc.ensure_connected()
def st():
    f=sc.capture(); return SS.state_of(f) if f is not None else ("other",0)
print("state:", st())
sc.run_shell(["input","tap","80","45"]); time.sleep(1.8)
sc.run_shell(["input","tap","960","574"]); time.sleep(2.5)
for j in range(8):
    s,c=st(); print(f"  {j}) {s}")
    if s in ("detail","form"): break
    if s=="exmap": sc.run_shell(["input","tap","370","275"]); time.sleep(2.5); continue
    sc.run_shell(["input","tap","640","660"]); time.sleep(1.6)
print("final:", st())
