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
            # Calculate target time today
            target=n.replace(hour=tg.hour,minute=tg.minute,second=0,microsecond=0)
            if target<=n: target+=timedelta(days=1)
            # Check weekday for weekly schedules
            is_daily=s.get("type","daily")=="daily"
            is_weekly=not is_daily and target.weekday() in s.get("days_of_week",[])
            if not is_daily and not is_weekly:
                # No match today, advance to next day
                self.msleep(min(60000,int((target-n).total_seconds()*1000))); continue
            # Sleep until target time (check every 5s for stop_flag)
            while self._r and datetime.now()<target:
                remain=(target-datetime.now()).total_seconds()
                self.msleep(min(5000,int(remain*1000)+500))
            if not self._r: break
            # Avoid double-trigger on same minute
            if self._last_run and (n-self._last_run).total_seconds()<120: self.msleep(30000); continue
            self.trigger.emit(); self._last_run=n
            self.msleep(60000)  # Wait 1 min after trigger
    def stop_thread(self): self._r=False
