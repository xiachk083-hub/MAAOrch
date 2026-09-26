"""日志校准采集器：以 asst.log 的 _run | enter 墙钟为基准，在 enter+35s 抓帧。

不依赖状态判别器（它在 battle↔other 闪变，今晚已多次误事）。
"""
import re, time, pathlib, datetime, cv2, sys
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
LOG=pathlib.Path("services/maa/instances/1/debug/asst.log")
TS=re.compile(r'^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\]')
sc=AdbScreen("2"); sc.ensure_connected()
base_size=LOG.stat().st_size
enter=None
for i in range(400):
    try:
        txt=LOG.read_text(encoding='utf-8',errors='ignore')[base_size:]
    except Exception:
        txt=""
    for ln in txt.splitlines():
        if 'BattleProcessTask::_run | enter' in ln:
            m=TS.match(ln)
            if m:
                enter=datetime.datetime.strptime(m.group(1),'%Y-%m-%d %H:%M:%S.%f')
                print("收到 enter:", enter.time(), flush=True)
    if enter and (datetime.datetime.now()-enter).total_seconds()>=35:
        f=sc.capture()
        if f is not None:
            cv2.imwrite("data/agent_run/_face35.png", f)
            print("已存 _face35.png @ enter+%.0fs" % (datetime.datetime.now()-enter).total_seconds(), flush=True)
        break
    time.sleep(2)
else:
    print("等待超时，未拍到", flush=True)
