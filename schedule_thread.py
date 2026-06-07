from __future__ import annotations
import time
from datetime import datetime, time as dtime, timedelta
from PySide6.QtCore import QThread, Signal

class ScheduleThread(QThread):
    trigger = Signal()
    batch_trigger = Signal()

    def __init__(self, c: dict) -> None:
        super().__init__(); self.c=c; self._r=True; self._last_run=None; self._last_batch_date=None

    def run(self) -> None:
        while self._r:
            s=self.c.get("schedule",{})
            if not s.get("enabled"):
                self.msleep(15000); continue
            n=datetime.now()
            try:
                raw=s.get("time","08:00")
                tg=dtime.fromisoformat(raw)
                if not (0<=tg.hour<24 and 0<=tg.minute<60):
                    tg=dtime(8,0)
            except:
                tg=dtime(8,0)
            target=n.replace(hour=tg.hour,minute=tg.minute,second=0,microsecond=0)
            is_daily=s.get("type","daily")=="daily"
            if not is_daily:
                allowed=set(s.get("days_of_week",[]))
                if not allowed: self.msleep(60000); continue
                for _ in range(7):
                    if target.weekday() in allowed and target>n: break
                    target+=timedelta(days=1)
                else: self.msleep(60000); continue
            else:
                if target<=n: target+=timedelta(days=1)
            # ── Daily batch check ──
            bt = self.c.get("daily_batch_time","")
            if bt:
                try:
                    bh, bm = map(int, bt.split(":"))
                    batch_target = n.replace(hour=bh, minute=bm, second=0, microsecond=0)
                    if batch_target <= n: batch_target += timedelta(days=1)
                    if batch_target < target:
                        target = batch_target  # batch time is sooner
                except Exception: pass
            # Sleep until target time
            while self._r and datetime.now()<target:
                remain=(target-datetime.now()).total_seconds()
                self.msleep(min(5000,int(remain*1000)+500))
            if not self._r: break
            n=datetime.now()  # refresh after sleep
            # Check if this is a batch trigger
            is_batch = False
            today = n.strftime("%Y-%m-%d")
            if bt and self._last_batch_date != today:
                try:
                    bh, bm = map(int, bt.split(":"))
                    if n.hour == bh and n.minute >= bm:
                        is_batch = True
                        self._last_batch_date = today
                        self.batch_trigger.emit(); self._last_run=n
                except Exception: pass
            if not is_batch:
                if self._last_run and (n-self._last_run).total_seconds()<120: self.msleep(30000); continue
                self.trigger.emit(); self._last_run=n
            self.msleep(60000)
    def stop_thread(self) -> None: self._r=False
