"""可观测性三件套（阶段 1-2）：trace.jsonl + 关键事件通知 + 回放报告。

设计取舍（2026-09-16）：
  · 通知层默认写 data/agent_run/notify.log（可离线验收，不依赖任何外部服务）；
    仅当环境变量 TG_TOKEN + TG_CHAT_ID 都存在时才同时推 Telegram——
    外发消息属于对外动作，没有凭据就不发，避免静默把内容发出去。
  · battle_time 用 asst.log 的 `_run | enter` 墙钟做基准（今晚验证过：与
    `leave - 时长` 精确吻合到毫秒），不依赖状态判别器（它在 battle↔other 闪变）。
"""
from __future__ import annotations
import json
import os
import pathlib
import re
import time
import datetime

PROJ = pathlib.Path(__file__).resolve().parent.parent.parent
RUN_DIR = PROJ / "data" / "agent_run"
TRACE = RUN_DIR / "trace.jsonl"
NOTIFY = RUN_DIR / "notify.log"
FRAMES = RUN_DIR / "frames"
ASST_LOG = PROJ / "services" / "maa" / "instances" / "1" / "debug" / "asst.log"
_TS = re.compile(r'^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\]')


def battle_start_from_log(offset: int = 0) -> datetime.datetime | None:
    """offset 之后**第一条** enter（不是最后一条：否则会拿到上一局的）。"""
    """从 asst.log 找最近一次 BattleProcessTask::_run | enter 的墙钟（帧/记录的时间基准）。"""
    try:
        txt = ASST_LOG.read_text(encoding="utf-8", errors="ignore")[offset:]
    except Exception:
        return None
    for ln in txt.splitlines():
        if "BattleProcessTask::_run | enter" in ln:
            m = _TS.match(ln)
            if m:
                return datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f")
    return None


_TS_TOLERANT = [
    "%Y-%m-%d %H:%M:%S.%f",   # 标准（MAA 当前格式）
    "%Y-%m-%d %H:%M:%S",      # 无毫秒（版本微调容错）
]


def parse_ts(s: str):
    """容忍日志格式微调：都失败则返回 None（调用方回退按行号并告警）。"""
    for fmt in _TS_TOLERANT:
        try:
            return datetime.datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def last_log_mark() -> tuple[int, object]:
    """武装锚点：(字节大小, 最后一行时间戳对象或 None)。"""
    if not ASST_LOG.exists():
        return 0, None
    data = ASST_LOG.read_bytes()
    for ln in reversed(data[-16384:].decode("utf-8", errors="ignore").splitlines()):
        m = _TS.match(ln)
        if m:
            return len(data), parse_ts(m.group(1))
    return len(data), None


def lines_since(mark) -> list[str]:
    """只取**严格晚于**锚点时间戳的行（用 > 不用 >=，避免同毫秒重复）。

    时间戳切片对「日志被 MAA 启动清空/重建」免疫——这是字节偏移做不到的。
    锚点时间戳解析失败时回退按字节偏移，并由调用方告警。
    """
    size, ts = mark
    if not ASST_LOG.exists():
        return []
    if ts is None:
        data = ASST_LOG.read_bytes()
        return data[size:].decode("utf-8", errors="ignore").splitlines() if len(data) >= size else []
    out = []
    for ln in ASST_LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = _TS.match(ln)
        if not m:
            continue
        t = parse_ts(m.group(1))
        if t is not None and t > ts:
            out.append(ln)
    return out


def truncation_warning(mark) -> str | None:
    """时间戳切片免疫截断，但截断本身要知道（否则不知道何时观测不可信）。"""
    size, _ = mark
    now = ASST_LOG.stat().st_size if ASST_LOG.exists() else 0
    if now < size:
        return f"[警告] 日志被截断：{size} → {now}（本局事件数可能不可信，已用时间戳切片交叉验证）"
    return None


class Trace:
    """每步一条记录，不可省略（没有 trace 后面全是黑盒）。"""

    def __init__(self, run_tag: str = ""):
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        FRAMES.mkdir(parents=True, exist_ok=True)
        self.run_tag = run_tag
        self.step = 0
        # 日志按偏移切分：只认「本次起跑之后」出现的 enter，避免拿到上一局的
        self.offset = ASST_LOG.stat().st_size if ASST_LOG.exists() else 0
        self.enter = None

    def log_enter(self) -> None:
        """记录本局战斗开始时刻并推「开局」通知（只认本次起跑后的 enter）。"""
        self.enter = battle_start_from_log(self.offset)
        if self.enter:
            self.push(f"[开局] {self.run_tag} 战斗开始 {self.enter.strftime('%H:%M:%S')}")
        else:
            self.push(f"[告警] {self.run_tag} 本次起跑后未见新 enter —— 战斗可能未开始，battle_time 记 -1")

    def battle_time(self) -> float:
        if not self.enter:
            return -1.0   # 未见本局 enter：宁记 -1，也不拿上一局的时间冒名
        return (datetime.datetime.now() - self.enter).total_seconds()

    def add(self, perception: dict, decision: dict | None, execution: dict | None,
            guard: dict | None = None, frame=None) -> dict:
        import cv2
        self.step += 1
        fpath = None
        if frame is not None:
            fpath = f"frames/step_{self.step:03d}.png"
            cv2.imwrite(str(RUN_DIR / fpath), frame)
        rec = {
            "step": self.step,
            "wall_time": datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "battle_time": round(self.battle_time(), 1),
            "perception": {"frame": fpath, "state": perception},
            "decision": decision or {},
            "execution": execution or {},
            "guard": guard or {"alerts": [], "paused": False},
        }
        with TRACE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def push(self, msg: str) -> None:
        """关键事件通知：默认落文件；有 TG 凭据才外发。"""
        line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
        with NOTIFY.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        tok, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT_ID")
        if tok and chat:
            try:
                import requests
                requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              json={"chat_id": chat, "text": msg}, timeout=10)
            except Exception as e:
                with NOTIFY.open("a", encoding="utf-8") as f:
                    f.write(f"  (Telegram 推送失败: {type(e).__name__})\n")

    @staticmethod
    def report(limit: int = 200) -> str:
        """把 trace 变成人可读的时间轴（手机端看的形态）。"""
        if not TRACE.exists():
            return "(无 trace)"
        out = []
        for ln in TRACE.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            st = r["perception"]["state"]
            act = r["decision"].get("action", {}) or {}
            out.append(f"T+{r['battle_time']:>6}s 费用={st.get('cost')} 生命={st.get('life')} "
                       f"→ {act.get('type','?')} {act.get('operator','')} {act.get('reasoning','')[:40]}")
        return "\n".join(out)


if __name__ == "__main__":
    tr = Trace(run_tag="smoke")
    tr.log_enter()
    for i in range(3):
        tr.push(f"[关键] T+{i*7}s Deploy 凛御银灰 [1,4] 朝上")
        tr.add(perception={"cost": 10 + i, "life": 3, "is_paused": True, "screen": "battle"},
               decision={"action": {"type": "deploy", "operator": "凛御银灰",
                                    "position": [1, 4], "direction": "up",
                                    "reasoning": "费用够，右漏斗第一波 T+13 到"}},
               execution={"adb_cmd": "input tap 980 720", "result": "ok", "elapsed_ms": 224})
        time.sleep(0.2)
    print("trace 行数:", len((RUN_DIR / "trace.jsonl").read_text(encoding='utf-8').splitlines()))
    print("--- 通知文件 ---"); print((RUN_DIR / "notify.log").read_text(encoding='utf-8').strip()[-300:])
    print("--- 回放报告 ---"); print(Trace.report())
