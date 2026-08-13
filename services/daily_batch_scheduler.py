"""日常定时批量入队（恢复被误删的活功能 — 2026-08-14 核对）。

背景：前端有「日常定时」（daily_batch_time，默认 08:00）配置项，
但唯一消费方 batch_enqueue_all 无任何调用方（08-13 审查：main_web
曾有活调用被当死代码删，e035a29）→ 设置从不生效。
恢复：到点调 launch_queue.batch_enqueue_all（每日一次，防重复）。

模式照抄 PenguinScheduler（services/penguin_data.py）：
daemon 线程 + HH:MM 匹配 + 当天已跑标记。
"""
from __future__ import annotations

import threading
import time


class DailyBatchScheduler(threading.Thread):
    """每日 daily_batch_time 自动批量入队（main_web.pyw 启动一次）。"""

    def __init__(self, mw) -> None:
        super().__init__(daemon=True, name="daily-batch-scheduler")
        self.mw = mw
        self._last_run_date = ""

    def _tick(self) -> None:
        try:
            cfg = self.mw.config or {}
            hhmm = str(cfg.get("daily_batch_time", "") or "")
            if not hhmm:
                return  # 未配置 = 不启用（UI 默认 08:00，但保存空串则不跑）
            today = time.strftime("%Y-%m-%d")
            if time.strftime("%H:%M") == hhmm and self._last_run_date != today:
                self._last_run_date = today
                fn = getattr(self.mw, "_log", None)
                try:
                    lq = getattr(self.mw, "launch_queue", None)
                    if lq is None:
                        return
                    if getattr(lq, "_paused", False):
                        lq.resume()
                    lq.batch_enqueue_all()
                    if fn:
                        fn(f"📅 日常定时批量入队（{hhmm}）")
                except Exception as e:
                    if fn:
                        fn(f"📅 日常定时批量入队异常: {e}")
        except Exception:
            pass

    def run(self) -> None:
        while True:
            try:
                self._tick()
            except Exception:
                pass
            time.sleep(5)
