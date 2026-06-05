import time
from datetime import datetime, time as dtime, timedelta
from PySide6.QtCore import QThread, Signal

class ScheduleThread(QThread):
    trigger=Signal()
    def __init__(self,c): super().__init__(); self.c=c; self._r=True; self._last_run=None
    def run(self):
        while self._r:
            s=self.c.get("schedule",{})
            if not s.get("enabled"):
                self.msleep(15000); continue
            n=datetime.now()
            try: tg=dtime.fromisoformat(s.get("time","08:00"))
            except: tg=dtime(8,0)
            target=n.replace(hour=tg.hour,minute=tg.minute,second=0,microsecond=0)
            is_daily=s.get("type","daily")=="daily"
            # For weekly: find next matching weekday (search up to 7 days)
            if not is_daily:
                allowed=set(s.get("days_of_week",[]))
                if not allowed: self.msleep(60000); continue
                for _ in range(7):
                    if target.weekday() in allowed and target>n:
                        break
                    target+=timedelta(days=1)
                else: self.msleep(60000); continue  # no match in 7 days
            else:
                if target<=n: target+=timedelta(days=1)
            # Sleep until target time
            while self._r and datetime.now()<target:
                remain=(target-datetime.now()).total_seconds()
                self.msleep(min(5000,int(remain*1000)+500))
            if not self._r: break
            if self._last_run and (n-self._last_run).total_seconds()<120: self.msleep(30000); continue
            self.trigger.emit(); self._last_run=n
            self.msleep(60000)
    def stop_thread(self): self._r=False
