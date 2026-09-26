"""3 秒间隔连续帧，文件名带墙钟 HHMMSS，用于对齐 _run enter 校准的开局前 20 秒。"""
import sys, time, cv2, datetime
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
sc=AdbScreen("2"); sc.ensure_connected()
t_end=time.time()+260
while time.time()<t_end:
    f=sc.capture()
    if f is not None:
        cv2.imwrite("data/agent_run/_t%s.png" % datetime.datetime.now().strftime("%H%M%S"), f)
    time.sleep(3)
