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
        self._stopped = False
        self._fp = None
        self._gui_fp = None

    def stop(self) -> None:
        """停止监控。2026-08-16 修复：置标志 + **立即关闭句柄**。

        原实现只置 _stopped 标志，线程若阻塞在 readline()（MAA 空闲时的
        常态），要等 MAA 写下一行才走到 finally 关句柄；runner cleanup 的
        join(2.0) 超时后继续 → 句柄残留 → 下一轮 MAA 启动 Bootstrapper
        MoveFile（旋转 gui.log/asst.log）被占 → IOException →
        ErrorCongratulations 启动崩溃（2026-08-16 实测 15+ 账号连环崩）。
        关闭后 readline() 立即返回 → 循环自然退出，finally 幂等再关一次。
        """
        self._stopped = True
        try:
            if self._fp:
                self._fp.close()
        except Exception:
            pass
        try:
            if self._gui_fp:
                self._gui_fp.close()
        except Exception:
            pass

    def cur_task(self) -> str:
        """当前任务名（asst.log 最新 cur_task — 单点解析，2026-08-15 收敛：
        runner._update_status 原自读文件解析，改查这里）。"""
        return getattr(self, "_cur_task", "") or ""

    def mtime_age(self) -> float:
        """asst.log 最后修改距今秒数（单点 mtime 缓存 — runner 各停滞检测
        原自读文件 stat，改查这里）。文件不存在返回极大值。"""
        try:
            if self._path.exists():
                return time.time() - self._path.stat().st_mtime
        except Exception:
            pass
        return 99999.0

    def tail_tasks(self) -> list[str]:
        """asst.log 尾部 taskchain 序列（TaskChainStart/Completed/Error —
        单点解析，runner._parse_log 原自读文件，改查这里）。"""
        try:
            if not self._path.exists():
                return []
            import re as _re
            _sz = self._path.stat().st_size
            with self._path.open("r", encoding="utf-8", errors="replace") as _f:
                _f.seek(max(0, _sz - 200000))
                _tail = _f.read()
            out = []
            for _mm in _re.finditer(
                    r'(TaskChainStart|TaskCompleted|TaskChainError).*?"taskchain":"([^"]+)"',
                    _tail):
                out.append((_mm.group(1), _mm.group(2)))
            return out
        except Exception:
            return []

    def _open_tail(self) -> bool:
        """打开文件并 seek 尾部（只读新内容，不重读历史）。

        2026-08-16 关键修复: CreateFileW + FILE_SHARE_DELETE 代替普通
        open()。普通 open() 默认共享不含 FILE_SHARE_DELETE → watcher 持续
        持有句柄阻止 MAA 启动时 Bootstrapper MoveFile（轮转 asst.log/
        gui.log）→ IOException → ErrorCongratulations 崩溃（实测连环崩，
        句柄锁根因）。FILE_SHARE_DELETE 允许其他进程删除/重命名本文件，
        watcher 可安全持续持有（fc2674b 防丢行）。Python 3.12 无
        os.O_SHARE_DELETE，用 CreateFileW + msvcrt.open_osfhandle。
        """
        try:
            self._fp = self._open_shared(self._path)
            if self._fp:
                self._fp.seek(0, 2)
                return True
        except Exception:
            pass
        self._fp = None
        return False

    def _open_gui_tail(self) -> bool:
        """gui.log 尾部跟随（降级/配置信号在 gui.log — 2026-08-11 补全）。
        同 _open_tail: FILE_SHARE_DELETE 防阻塞 MAA 日志轮转（2026-08-16）。"""
        try:
            self._gui_fp = self._open_shared(self._gui_path)
            if self._gui_fp:
                self._gui_fp.seek(0, 2)
                return True
        except Exception:
            pass
        self._gui_fp = None
        return False

    @staticmethod
    def _open_shared(path) -> object | None:
        """CreateFileW 带 FILE_SHARE_READ|WRITE|DELETE 打开（Windows）。
        返回文本文件对象（utf-8, errors=replace）；失败返回 None。"""
        try:
            import ctypes as _ct
            import msvcrt as _ms
            import os as _os
            _k32 = _ct.windll.kernel32
            GENERIC_READ = 0x80000000
            FILE_SHARE_READ = 1
            FILE_SHARE_WRITE = 2
            FILE_SHARE_DELETE = 4
            OPEN_EXISTING = 3
            _k32.CreateFileW.restype = _ct.c_void_p
            h = _k32.CreateFileW(
                str(path), GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                None, OPEN_EXISTING, 0, None)
            if not h or h == _ct.c_void_p(-1).value:
                return None
            fd = _ms.open_osfhandle(h, 0)  # 0 = os.O_RDONLY
            return _os.fdopen(fd, "r", encoding="utf-8", errors="replace")
        except Exception:
            return None

    def run(self) -> None:
        try:
            if not self._open_tail():
                # 文件尚未出现（MAA 启动后才有）— 轮询等待
                while not self._stopped:
                    if self._open_tail():
                        break
                    time.sleep(1)
            self._open_gui_tail()
            while not self._stopped:
                try:
                    line = self._fp.readline()
                    if line:
                        # 2026-08-15: MAA 6.16.8 长 JSON 折行（SubTaskExtraInfo
                        # 的 uuid 字段被拆成两行）→ 单行 `{.*\}` 永远解析失败
                        # （体力快照全停更根因）。`{` 未配平时拼接后续行，
                        # 最多拼 3 行（防异常行死循环）。
                        _joins = 0
                        while line.count("{") > line.count("}") and _joins < 3:
                            _nxt = self._fp.readline()
                            if not _nxt:
                                break
                            line = line.rstrip("\r\n") + _nxt
                            _joins += 1
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
                    # 句柄失效（文件被删/重开失败）→ 尝试重开尾部跟随，
                    # 失败则稍后重试（防空转）。stop() 后的关闭不重开
                    # （2026-08-16: stop 立即关句柄防 MoveFile 锁，重开
                    # 会抵消 → 句柄残留 → 下轮 MAA 启动崩溃）。
                    if self._stopped:
                        break
                    time.sleep(1)
                    try:
                        if self._fp and self._fp.closed:
                            self._open_tail()
                    except Exception:
                        pass
                # ⚠️ 2026-08-16: 不再每轮 close+reopen。原 3c74e39（08-11 句柄
                # 泄漏修复）每轮循环都 close + _open_tail(seek 尾部) → 每轮
                # 只读 1 行，MAA 快速输出（启动/Fight 结算）时其余行全被
                # 跳过 → cb 事件缺失、sanity 行丢失（体力快照停更）。
                # CPython 的 open() 在 Windows 默认 FILE_SHARE_READ|WRITE|
                # DELETE — 句柄持续持有不会阻止 MAA 清空/写入 asst.log；
                # 防泄漏由 finally 关闭 + cleanup join 保证。轮转检测
                # （tell>size 时重开）在上方分支保留。
        finally:
            # 线程退出（stop 置位）→ 释放文件句柄（08-11 审查「句柄泄漏」：
            # 旧句柄会阻止 MAA 重启时 asst.log 删除重建；daemon 线程 + 句柄
            # 不关 = 每账号每轮泄漏 2 个句柄，长期积累）。
            try:
                if self._fp:
                    self._fp.close()
            except Exception:
                pass
            try:
                if self._gui_fp:
                    self._gui_fp.close()
            except Exception:
                pass

    def _fire(self, event_type: str, line: str) -> None:
        """事件分发：兼容现有 runner 回调 + 同时进事件总线（单点收敛
        Step 2 — LogWatcher 事件唯一化，其余模块不再自读 asst.log）。"""
        try:
            self._on_event(event_type, self._aid, line)
        except Exception:
            pass
        try:
            from services.event_bus import emit
            _type = {"adb_connected": "emu.adb_connected",
                     "completed": "maa.completed",
                     "sanity_before": "fight.sanity_before",
                     "medicine_used": "fight.medicine_used"}.get(event_type,
                                                               "maa." + event_type)
            _payload = {"line": line[:200]}
            # Step 3a: sanity 事件带结构化数据 + 实例目录（归属校验）—
            # bus 订阅者（sanity_tracker）直接消费，不再各自解析原始行
            if event_type == "sanity_before":
                try:
                    from services.sanity_tracker import parse_line
                    _det = parse_line(line)
                    if _det:
                        _payload["sanity"] = _det
                        _payload["inst_dir"] = str(self._path.parent.parent)
                except Exception:
                    pass
            emit(_type, _payload, aid=self._aid)
        except Exception:
            pass

    def _dispatch_gui(self, line: str) -> None:
        """gui.log 事件：关卡无效/配置问题（降级信号）。"""
        try:
            if ("添加任务失败" in line and "理智" in line) or \
               ("selected null" in line and "FightStage" in line) or \
               "配置无效" in line:
                _record_event(self._aid, "downgrade_signal", line)
                try:
                    self._fire("downgrade_signal", line)
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
                    self._fire("subtask_error", line)
                except Exception:
                    pass
            # 任务链错误（2026-08-15 日-4 实锤: TaskChainError 也会触发
            # AllTasksCompleted = 假完成 — 识别界面失败→报错→被当"完成"→
            # 体力不刷还续跑循环）。单独事件，runner 用来判假完成。
            if "TaskChainError" in line:
                _record_event(self._aid, "taskchain_error", line)
                try:
                    self._fire("taskchain_error", line)
                except Exception:
                    pass
            # MAA 成功执行 adb（截图） = 已连接模拟器开始干活 — 启动节流
            # 就绪信号（2026-08-12: 等上一台 boot + MAA 连上再启动下一台）。
            # runner 侧只在首次处理（后续截图行不再通知）。
            if " Call ` " in line and "adb" in line and " ret 0" in line:
                _record_event(self._aid, "adb_connected", line)
                try:
                    self._fire("adb_connected", line)
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
                    # StartUp/加载阶段豁免（2026-08-12 用户: 日-4 日志正常只
                    # 是时间差 — 游戏加载慢（VAddress 渲染 → 截图 479ms）时
                    # MAA 在 LoadingIcon DoNothing 等待加载，3 分钟正常 →
                    # 40 次误判"空转"→ 误杀重启循环）。只检测刷关/任务阶段。
                    if _task.startswith("StartUp") or "Loading" in _task:
                        self._dn_task = None
                        self._dn_count = 0
                        return
                    if getattr(self, "_dn_task", None) == _task:
                        self._dn_count = getattr(self, "_dn_count", 0) + 1
                    else:
                        self._dn_task = _task
                        self._dn_count = 1
                    if getattr(self, "_dn_count", 0) >= 40:
                        self._dn_count = 0  # 重置防重复触发
                        _record_event(self._aid, "stall_loop", line)
                        try:
                            self._fire("stall_loop", line)
                        except Exception:
                            pass
            except Exception:
                pass
            if "AllTasksCompleted" in line:
                _record_event(self._aid, "completed", line)
                self._fire("completed", line)
            elif "FightMissionFailed" in line or "PrtsErrorConfirm" in line:
                _record_event(self._aid, "battle_failed", line)
                self._fire("battle_failed", line)
            elif "ExceededLimit" in line:
                _record_event(self._aid, "exceeded", line)
                self._fire("exceeded", line)
            # 补全事件类型（2026-08-11 用户: 日志要全量收集，避免遗漏）
            elif "TaskStart" in line or "TaskChainStart" in line:
                _record_event(self._aid, "task_start", line)
            elif "TaskCompleted" in line and "SubTaskCompleted" not in line:
                # 断点续做（2026-08-16）: 单任务链完成 → 记入 AccountState
                # done_chains（本次任务已完成方块），重启注入时跳过。
                # 格式: ...TaskChainCompleted... "taskchain":"StartUp"
                # ⚠️ 2026-08-17 修复: 必须排除 SubTaskCompleted（子任务完成，
                # 也含 "TaskCompleted" 子串 + taskchain 字段）— 否则 Fight 第一个
                # 子任务完成就被误记 "Fight" 完成 → 断点续做跳过刷体力。
                _record_event(self._aid, "task_completed", line)
                try:
                    import re as _rc
                    _mch = _rc.search(r'"taskchain":"([^"]+)"', line)
                    if _mch:
                        _chain = _mch.group(1)
                        from models.account_state import AccountState
                        AccountState(self._aid).mark_chain_done(_chain)
                        try:
                            self._fire("task_completed", line)
                        except Exception:
                            pass
                except Exception:
                    pass
            elif "FightTimes" in line and "sanity" in line.lower():
                _record_event(self._aid, "fight_sanity", line)  # 刷次数+体力
                # 2026-08-15 收敛: times_finished 信号（刷关次数 — 假完成
                # 复核用）原由 runner 自读日志解析，改由 LogWatcher 单点
                # 解析发事件（runner._on_log_event 订阅）。
                try:
                    import json as _json2
                    _jm = _json2.loads(line[line.index("{"):line.rindex("}") + 1]) if "{" in line else None
                    if _jm:
                        _det = _jm.get("details") or {}
                        if "times_finished" in _det:
                            _record_event(self._aid, "fight_times", line)
                            try:
                                self._fire("fight_times", line)
                            except Exception:
                                pass
                except Exception:
                    pass
                # 体力实时快照（2026-08-12 用户: 以最后实时的结果为准）—
                # SanityBeforeStage 每次进关卡都报 → 快照即最新体力，
                # 消费方（sanity 门/界面）读它而不是运行结束的死值。
                # inst_dir 传实例目录 → .meta 归属校验（防账号串号）。
                try:
                    from services.sanity_tracker import update_from_line
                    update_from_line(self._aid, line, self._path.parent.parent)
                except Exception:
                    pass
                # 通知 runner 记录本次运行首体力（统计真实消耗 — 2026-08-13）
                try:
                    if self._on_event:
                        self._fire("sanity_before", line)
                except Exception:
                    pass
            elif ("MedicineConfirmed" in line or '"task":"UseMedicine"' in line):
                # 理智药使用（我们策略普通药关，只有过期药自动吃 → 吃了
                # 就是 48h 过期药突击 — 2026-08-13 用户: 挤一天刷会虚高）
                _record_event(self._aid, "medicine_used", line)
                try:
                    if self._on_event:
                        self._fire("medicine_used", line)
                except Exception:
                    pass
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
