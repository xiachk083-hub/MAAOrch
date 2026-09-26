"""第 1 步：纯 trace 验收（观察者旁挂，不干预 runner）。

朝向账（第 2 步，另起一局，勿与本脚本同局）安全点：
  · 选中/取消选中干员的「空白点」用屏幕中央偏下 (640, 500)，已确认避开暂停(1200,50)/
    倍速(1100,50)/待部署栏(底部 y>620)/齿轮(80,45) —— 仅选中，绝不点「撤退」「技能」。

顺序硬要求（2026-09-16 教训）：**先武装观察者并校验 offset 早于 enter，再起 runner**。
验收口径：trace 行数 == 本局窗口内 asst.log 的 Deploy + Retreat 总数。
"""
import re, sys, time, pathlib, subprocess, datetime, json
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
from services.ai_agent.trace import (ASST_LOG, TRACE, RUN_DIR, Trace,
                                      last_log_mark, lines_since, truncation_warning)

TS = re.compile(r'^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\]')
PLAN = "data/copilot/ga_derived/act16side_ex02_derived_v17.json"


def last_line_time() -> tuple[int, str]:
    if not ASST_LOG.exists():
        return 0, "?"
    data = ASST_LOG.read_bytes()
    tail = data[-8192:].decode('utf-8', errors='ignore').splitlines()
    for ln in reversed(tail):
        m = TS.match(ln)
        if m:
            return len(data), m.group(1)
    return len(data), "?"


def main() -> None:
    if TRACE.exists():
        TRACE.rename(RUN_DIR / f"trace_prev_{datetime.datetime.now():%H%M%S}.jsonl")
    mark = last_log_mark()
    offset, ts = mark[0], mark[1]
    print(f"[武装] 观察者 offset={offset}  日志最后一行时间={ts}")
    print("       校验：本局 enter 必须出现在这个 offset 之后（否则会漏掉 enter→首个 Deploy 之间的事件）")

    obs = subprocess.Popen([sys.executable, "tools/trace_observer.py"],
                           stdout=open(RUN_DIR / "_obs2.log", "w", encoding="utf-8"),
                           stderr=subprocess.STDOUT)
    time.sleep(2)
    run = subprocess.Popen([sys.executable, "tools/clear_runner.py", "--emu", "2",
                            "--stage", "act16side_ex02", "--node", "370,275",
                            "--plans", PLAN, "--max-tries", "1"],
                           stdout=open(RUN_DIR / "_v24.log", "w", encoding="utf-8"),
                           stderr=subprocess.STDOUT)
    print("[起跑] runner + 观察者已启动；等 runner 结束（最多 8 分钟）")
    try:
        run.wait(timeout=480)
    except subprocess.TimeoutExpired:
        print("!! runner 超时未结束")
    finally:
        try:
            obs.terminate()
        except Exception:
            pass

    # 验收：trace 行数 vs 本局窗口内 Deploy+Retreat 数
    trace_n = len(TRACE.read_text(encoding='utf-8').splitlines()) if TRACE.exists() else 0
    # 时间戳切片（对日志被清空/重建免疫），不再用 log[offset:]
    new_lines = lines_since(mark)
    warn = truncation_warning(mark)
    if warn:
        print(warn)
    data = "\n".join(new_lines)
    dep = len(re.findall(r'"action":"Deploy"', data))
    ret = len(re.findall(r'"action":"Retreat"', data))
    print(f"\n[验收] trace 行数 = {trace_n}")
    print(f"       本局窗口内 Deploy={dep}  Retreat={ret}  合计={dep + ret}")
    if dep + ret == 0:
        print("[判定] 无法判定 —— 窗口内 Deploy+Retreat = 0（0==0 不算通过）。"
              "先确认：本局是否真的打了、日志是否被截断、锚点是否早于本局 enter")
    else:
        print("[判定]", "通过 ✓" if trace_n == dep + ret else
              f"不通过 ✗（差 {abs(trace_n - (dep + ret))} 条；若 trace=0 说明锚点晚于本局 enter）")

    # 顺序对账（行数相等只说明完整，还要确认没错位）
    log_seq = [(m.group(1), (m.group(2) or ""))
               for m in re.finditer(r'"action":"(Deploy|Retreat)","doc":"[^"]*","doc_color":"[^"]*","elapsed_time":-?\d+(?:,"target":"([^"]*)")?', data)]
    tr_seq = []
    if TRACE.exists():
        for ln in TRACE.read_text(encoding='utf-8').splitlines():
            try:
                a = (json.loads(ln)["decision"] or {}).get("action", {}) or {}
            except Exception:
                continue
            if a.get("category") in ("deploy", "retreat"):     # 判据只看部署/撤退（口径 10）
                tr_seq.append((a.get("type"), a.get("operator", "")))
    if log_seq == tr_seq:
        print(f"[顺序] 一致 ✓（{len(log_seq)} 条逐条对齐：{' → '.join(x[1] or x[0] for x in log_seq[:5])} …）")
    else:
        for i, (a, b) in enumerate(zip(log_seq, tr_seq)):
            if a != b:
                print(f"[顺序] 错位 ✗ 第 {i+1} 条：日志={a} trace={b}")
                break
        else:
            print(f"[顺序] 错位 ✗ 长度不同：日志 {len(log_seq)} vs trace {len(tr_seq)}")


if __name__ == "__main__":
    main()
