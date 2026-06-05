import time
from datetime import datetime, time as dtime
from PySide6.QtCore import QThread, Signal

class ScheduleThread(QThread):
    trigger=Signal()
    def __init__(self,c): super().__init__(); self.c=c; self._r=True
    def run(self):
        while self._r:
            s=self.c.get("schedule",{})
            if s.get("enabled"):
                n=datetime.now()
                try: tg=dtime.fromisoformat(s.get("time","08:00"))
                except: tg=dtime(8,0)
                if n.hour==tg.hour and n.minute==tg.minute and n.second<30:
                    if s.get("type")=="daily" or (s.get("type")=="weekly" and n.weekday() in s.get("days_of_week",[])): self.trigger.emit(); time.sleep(61)
            time.sleep(15)
    def stop_thread(self): self._r=False

