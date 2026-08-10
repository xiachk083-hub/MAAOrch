"""每实例日志监控线程 — 事件驱动替代 5s 轮询（2026-08-11 架构计划 P1）。

MAA 无法主动推（Python asst 无外部回调）→ 日志事件流是唯一事件源。
tail 模式：文件句柄自动跟随，行 → 事件 → 回调（<0.2s 响应，替代
_check_one 的 5s 增量轮询；消除增量位置跨线程共享的竞态）。

事件类型：
- completed    AllTasksCompleted（任务链全部完成）
- battle_failed FightMissionFailed / PrtsErrorConfirm（作战失败 → 降级）
- exceeded     ExceededLimit（重试耗尽）

日志样本收集（2026-08-11 用户）：
每次事件触发时把「事件 + 原始行 + 上下文」追加到 logs/log_samples/*.jsonl
— 现场真实日志样本，供后续训练/日志模式→动作映射规则提炼。
"""
from __future__ import annotations
import json
import threading
import time
from datetime import datetime
from pathlib import Path

_SAMPLES_DIR = Path(__file__).parent.parent / "logs" / "log_samples"


def _record_event(aid: str, event_type: str, line: str) -> None:
    """事件样本落盘（jsonl 追加，供训练/规则提炼）。"""
    try:
        _SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "aid": aid[:8],
            "event": event_type,
            "line": line.strip()[:400],
        }
        with open(_SAMPLES_DIR / f"{aid[:8]}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


class LogWatcher(threading.Thread):
    def __init__(self, inst_path: str, aid: str, on_event, name: str = ""):
        super().__init__(daemon=True, name=f"logwatch_{name or aid[:6]}")
        self._path = Path(inst_path) / "debug" / "asst.log"
        self._aid = aid
        self._on_event = on_event  # callable(event_type: str, aid: str, line: str)
        self._stop = False
        self._fp = None

    def stop(self) -> None:
        self._stop = True

    def _open_tail(self) -> bool:
        """打开文件并 seek 尾部（只读新内容，不重读历史）。"""
        try:
            self._fp = open(self._path, "r", encoding="utf-8", errors="replace")
            self._fp.seek(0, 2)
            return True
        except Exception:
            self._fp = None
            return False

    def run(self) -> None:
        if not self._open_tail():
            # 文件尚未出现（MAA 启动后才有）— 轮询等待
            while not self._stop:
                if self._open_tail():
                    break
                time.sleep(1)
        while not self._stop:
            try:
                line = self._fp.readline()
                if line:
                    self._dispatch(line)
                else:
                    time.sleep(0.2)
                    # 文件轮转检测：MAA 重启会清空 asst.log，句柄位置超过
                    # 文件大小 → 重开（重新尾部跟随）
                    try:
                        if self._path.exists() and self._fp.tell() > self._path.stat().st_size:
                            self._fp.close()
                            self._open_tail()
                    except Exception:
                        pass
            except Exception:
                time.sleep(1)
                try:
                    self._fp.close()
                except Exception:
                    pass
                if not self._open_tail():
                    time.sleep(2)

    def _dispatch(self, line: str) -> None:
        try:
            if "AllTasksCompleted" in line:
                _record_event(self._aid, "completed", line)
                self._on_event("completed", self._aid, line)
            elif "FightMissionFailed" in line or "PrtsErrorConfirm" in line:
                _record_event(self._aid, "battle_failed", line)
                self._on_event("battle_failed", self._aid, line)
            elif "ExceededLimit" in line:
                _record_event(self._aid, "exceeded", line)
                self._on_event("exceeded", self._aid, line)
        except Exception:
            pass
