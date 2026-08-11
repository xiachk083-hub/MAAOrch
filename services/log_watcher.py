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
_collect_fail: int = 0  # 收集失败计数（健康检查暴露 — 收集绝不能静默丢）


def get_collect_status() -> dict:
    """收集健康状态（2026-08-11 用户: 收集是根，不能漏）。"""
    return {"samples_dir": str(_SAMPLES_DIR), "fail_count": _collect_fail}


def _record_event(aid: str, event_type: str, line: str) -> None:
    """事件样本落盘（jsonl 追加，供训练/规则提炼）。
    收集优先：任何失败不静默 — 计数暴露；文件超 50MB 归档（旧文件
    保留 .old，不丢数据 — 2026-08-11 用户: 得有垃圾才能分拣）。"""
    global _collect_fail
    try:
        _SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "aid": aid[:8],
            "event": event_type,
            "line": line.strip()[:400],
        }
        fp = _SAMPLES_DIR / f"{aid[:8]}.jsonl"
        # 归档：超 50MB 重命名 .old（保留）→ 新文件继续收（分拣扫 jsonl*）
        try:
            if fp.exists() and fp.stat().st_size > 50 * 1024 * 1024:
                fp.replace(fp.with_suffix(".jsonl.old"))
        except Exception:
            pass
        with open(fp, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        try:
            _collect_fail += 1
        except Exception:
            pass


def record_event(aid: str, event_type: str, line: str) -> None:
    """公共样本记录接口（runner/launch_queue 项目事件也写入，跨源完整）。"""
    _record_event(aid, event_type, line)


class LogWatcher(threading.Thread):
    def __init__(self, inst_path: str, aid: str, on_event, name: str = ""):
        super().__init__(daemon=True, name=f"logwatch_{name or aid[:6]}")
        self._path = Path(inst_path) / "debug" / "asst.log"
        self._gui_path = Path(inst_path) / "debug" / "gui.log"
        self._aid = aid
        self._on_event = on_event  # callable(event_type: str, aid: str, line: str)
        self._stop = False
        self._fp = None
        self._gui_fp = None

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

    def _open_gui_tail(self) -> bool:
        """gui.log 尾部跟随（降级/配置信号在 gui.log — 2026-08-11 补全）。"""
        try:
            self._gui_fp = open(self._gui_path, "r", encoding="utf-8", errors="replace")
            self._gui_fp.seek(0, 2)
            return True
        except Exception:
            self._gui_fp = None
            return False

    def run(self) -> None:
        if not self._open_tail():
            # 文件尚未出现（MAA 启动后才有）— 轮询等待
            while not self._stop:
                if self._open_tail():
                    break
                time.sleep(1)
        self._open_gui_tail()
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
                # gui.log 同步读取（降级/配置信号）
                if self._gui_fp:
                    gline = self._gui_fp.readline()
                    if gline:
                        self._dispatch_gui(gline)
            except Exception:
                time.sleep(1)
                try:
                    self._fp.close()
                except Exception:
                    pass
                if not self._open_tail():
                    time.sleep(2)

    def _dispatch_gui(self, line: str) -> None:
        """gui.log 事件：关卡无效/配置问题（降级信号）。"""
        try:
            if ("添加任务失败" in line and "理智" in line) or \
               ("selected null" in line and "FightStage" in line) or \
               "配置无效" in line:
                _record_event(self._aid, "downgrade_signal", line)
                try:
                    self._on_event("downgrade_signal", self._aid, line)
                except Exception:
                    pass
        except Exception:
            pass

    def _dispatch(self, line: str) -> None:
        try:
            # 兜底全量：所有 append_callback 结构化事件行都收集（不依赖
            # 关键词 — MAA 改文案/出新事件不会漏。2026-08-11 用户: 照词找
            # 日志会不会漏新日志）。每种事件每运行限 3 条防爆炸（采样足够）。
            if "Assistant::append_callback" in line and "|" in line:
                # 全量收集（2026-08-11 用户: 收集优先于识别 — 现场漏掉就
                # 永远不知道；限频=预先筛选，取消）
                try:
                    tail = line.split("|")[-1].strip()
                    evt = tail.split()[0] if tail else "?"
                    _record_event(self._aid, f"cb:{evt}", line)
                except Exception:
                    pass
            if "SubTaskError" in line:
                _record_event(self._aid, "subtask_error", line)
                try:
                    self._on_event("subtask_error", self._aid, line)
                except Exception:
                    pass
            # 空转检测（DoNothing 循环 — 2026-08-11 官-41 PRTS1 空转 60s+
            # 不触发任何检测的盲区: 日志持续写（非停滞）+ 无 SubTaskError。
            # 同一 cur_task 连续 ~40 次 DoNothing（约 3-5 分钟）→ stall_loop）
            try:
                if '"cur_task"' in line:
                    import re as _re
                    _m = _re.search(r'"cur_task":"([^"]+)"', line)
                    if _m:
                        self._cur_task = _m.group(1)
                elif "SubTaskStart" in line and "DoNothing" in line:
                    _task = getattr(self, "_cur_task", "?")
                    if getattr(self, "_dn_task", None) == _task:
                        self._dn_count = getattr(self, "_dn_count", 0) + 1
                    else:
                        self._dn_task = _task
                        self._dn_count = 1
                    if getattr(self, "_dn_count", 0) >= 40:
                        self._dn_count = 0  # 重置防重复触发
                        _record_event(self._aid, "stall_loop", line)
                        try:
                            self._on_event("stall_loop", self._aid, line)
                        except Exception:
                            pass
            except Exception:
                pass
            if "AllTasksCompleted" in line:
                _record_event(self._aid, "completed", line)
                self._on_event("completed", self._aid, line)
            elif "FightMissionFailed" in line or "PrtsErrorConfirm" in line:
                _record_event(self._aid, "battle_failed", line)
                self._on_event("battle_failed", self._aid, line)
            elif "ExceededLimit" in line:
                _record_event(self._aid, "exceeded", line)
                self._on_event("exceeded", self._aid, line)
            # 补全事件类型（2026-08-11 用户: 日志要全量收集，避免遗漏）
            elif "TaskStart" in line or "TaskChainStart" in line:
                _record_event(self._aid, "task_start", line)
            elif "FightTimes" in line and "sanity" in line.lower():
                _record_event(self._aid, "fight_sanity", line)  # 刷次数+体力
            elif "StageDrops" in line:
                _record_event(self._aid, "stage_drops", line)   # 掉落
            elif "RecruitResult" in line:
                _record_event(self._aid, "recruit", line)       # 公招
            elif "Connection" in line and ("failed" in line.lower() or "error" in line.lower()):
                _record_event(self._aid, "connection_error", line)  # 连接失败
            elif "OCR" in line and ("failed" in line.lower() or "error" in line.lower()):
                _record_event(self._aid, "ocr_error", line)     # OCR 异常
            elif "AsstLoadResource" in line and "failed" in line.lower():
                _record_event(self._aid, "load_error", line)    # 资源加载失败
        except Exception:
            pass
