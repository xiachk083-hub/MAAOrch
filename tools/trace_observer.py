"""旁挂观察者：只读日志事件 + 采样画面 → trace.jsonl + notify + 心跳。不干预 runner。

2026-09-16 定稿（修掉「观测层不可信」的根因）：
  · 锚点 mark = last_log_mark() **只在武装时取一次**；每次轮询重取会漏掉两次轮询之间的事件
  · 轮询用 lines_since(mark)：返回 mark 之后**所有**行，对「MAA 启动清空日志」免疫
  · 去重键 =(行号, 行内容)：MAA 日志无天然消息 ID，纯 hash(内容) 会把同秒同内容的行误判重复
  · 45s 内未见本局 enter → 显式告警（锚点/开打状态问题的第一信号）
  · 写 agents_health.json 心跳（B 线修复者的输入）
"""
import sys, time, re, json, datetime, cv2, pathlib, threading
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
from services.ai_agent.percept import AdbScreen
from services.ai_agent.trace import (Trace, ASST_LOG, RUN_DIR,
                                     last_log_mark, lines_since, truncation_warning)
import screen_state as SS

TS = re.compile(r'^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\]')
ACT = re.compile(r'"action":"(Deploy|Retreat|SkillDaemon|SwitchSpeed|SpeedUp)","doc":"[^"]*",'
                 r'"doc_color":"[^"]*","elapsed_time":-?\d+(?:,"target":"([^"]*)")?')
HEALTH = RUN_DIR / "agents_health.json"
_last_output = "starting"      # 主循环更新、心跳线程读（简单字符串赋值在 CPython 下安全）


def heartbeat_loop(interval: float = 2.0) -> None:
    """独立 daemon 线程写心跳。

    为什么必须独立（2026-09-16）：主循环会卡在 screencap（实测单张 224ms、最坏 875ms）
    或别的阻塞调用上；心跳若写在主循环里，修复者会把「这一轮慢」误报成 STALE，
    于是「进程死了」和「进程活着但卡住」又分不开了——而那正是最该区分的两种。
    """
    while True:
        try:
            beat("observer", "alive", _last_output)
        except Exception:
            pass
        time.sleep(interval)


def beat(agent: str, status: str, last_output: str = "") -> None:
    """B2 心跳：每个 Agent 一个条目（修复者的唯一输入）。"""
    try:
        d = json.loads(HEALTH.read_text(encoding='utf-8')) if HEALTH.exists() else {}
    except Exception:
        d = {}
    d[agent] = {"agent": agent, "last_seen": datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "last_output": last_output, "status": status,
                "expected_interval": 2 if agent == "observer" else 5}
    HEALTH.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')


def main() -> None:
    sc = AdbScreen("2"); sc.ensure_connected()
    tr = Trace(run_tag="observer")
    mark = last_log_mark()
    print(f"[武装] mark=(size={mark[0]}, ts={mark[1]})", flush=True)
    if mark[1] is None:
        print("[告警] 未能解析日志最后一行时间戳 → 回退按字节偏移（可能受日志截断影响）", flush=True)
    enter = None; seen = set(); last_notify = {}; seen_actions = set()
    t_arm = time.time(); deadline = t_arm + 900
    threading.Thread(target=heartbeat_loop, daemon=True).start()   # daemon：主进程退出即随退

    def notify_once(key, msg, gap=5.0):
        now = time.time()
        if now - last_notify.get(key, 0) > gap:
            last_notify[key] = now; tr.push(msg)

    while time.time() < deadline:
        warn = truncation_warning(mark)
        if warn:
            print(warn, flush=True)
        for lineno, ln in enumerate(lines_since(mark)):
            key = (lineno, ln)
            if key in seen:
                continue
            seen.add(key)
            m = TS.match(ln)
            if not m:
                continue
            t = datetime.datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S.%f')
            if enter is None and 'BattleProcessTask::_run | enter' in ln:
                enter = t; tr.enter = enter; tr.log_enter()
                print(f"[开局] enter={enter.time()}", flush=True)
            mm = ACT.search(ln)
            if mm and enter is not None:
                kind, tgt = mm.group(1), (mm.group(2) or "")
                # 动作级去重：同一动作在日志里有两条不同行（播报行 + ProcessTask 行），
                # 行级去重拦不住；按 (动作, 目标, 秒) 收敛成一条记录（2026-09-16 实测 20 vs 期望 10）
                akey = (kind, tgt, t.strftime("%H:%M:%S"))
                if akey in seen_actions:
                    continue
                seen_actions.add(akey)
                bt = (t - enter).total_seconds()
                notify_once(f"first_{kind}", f"[关键] T+{bt:.1f}s 首次 {kind} {tgt}")
                cat = {"Deploy": "deploy", "Retreat": "retreat",
                       "SwitchSpeed": "speed", "SpeedUp": "speed"}.get(kind, "skill_daemon")
                tr.add(perception={"screen": "battle"},
                       decision={"action": {"type": kind, "operator": tgt, "category": cat,
                                             "reasoning": "日志事件"}},
                       execution={"result": "observed", "log_ts": m.group(1)})
        if enter is None and time.time() - t_arm > 120:
            print("[告警] 武装 120s 后仍未见本局 enter —— 检查：本局是否真打了 / mark 是否晚于 enter", flush=True)
            notify_once("no_enter", "[告警] 观察者 120s 未见 enter")
        globals()['_last_output'] = f"trace_lines={tr.step}"
        f = sc.capture()
        if f is not None:
            st, _ = SS.state_of(f)
            if st in ("win", "fail") and enter is not None:
                tr.push(f"[结算] {st} 存活 {(datetime.datetime.now() - enter).total_seconds():.1f}s（日志口径）")
                tr.add(perception={"screen": st}, decision={"action": {"type": "end"}},
                       execution={}, frame=f)
                globals()['_last_output'] = "settled"
                print(Trace.report()); break
        time.sleep(2)
    print("trace 行数:", len((RUN_DIR / 'trace.jsonl').read_text(encoding='utf-8').splitlines())
          if (RUN_DIR / 'trace.jsonl').exists() else 0, flush=True)


if __name__ == "__main__":
    main()
