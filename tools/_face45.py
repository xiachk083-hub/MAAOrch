"""日志校准 + 先 ESC 关面板，再抓 enter+45s 的帧（用于朝向对账）。"""
import re, time, pathlib, datetime, cv2, sys, subprocess
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
from services.ai_agent.trace import ASST_LOG
TS=re.compile(r'^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\]')
sc=AdbScreen("2"); sc.ensure_connected()
base=ASST_LOG.stat().st_size if ASST_LOG.exists() else 0
enter=None
for i in range(300):
    try: txt=ASST_LOG.read_text(encoding='utf-8',errors='ignore')[base:]
    except Exception: txt=""
    for ln in txt.splitlines():
        if 'BattleProcessTask::_run | enter' in ln:
            m=TS.match(ln)
            if m: enter=datetime.datetime.strptime(m.group(1),'%Y-%m-%d %H:%M:%S.%f')
    if enter and (datetime.datetime.now()-enter).total_seconds()>=45:
        sc.run_shell(["input","keyevent","111"]); time.sleep(0.5)   # 先关面板
        f=sc.capture()
        if f is not None:
            cv2.imwrite("data/agent_run/_face45.png", f)
            print("已存 _face45.png @ enter+%.0fs"%(datetime.datetime.now()-enter).total_seconds(), flush=True)
        break
    time.sleep(2)
else: print("超时未拍到", flush=True)
