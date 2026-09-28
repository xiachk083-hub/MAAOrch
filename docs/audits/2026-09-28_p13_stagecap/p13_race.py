#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""P1-3 冒烟①：读者×写者竞态探针 —— 直写(naive，旧写法) vs tmp+os.replace(atomic，新写法)。

用法（PY312）：
  python p13_race.py --mode naive  [--iters 400] [--pad 500000] [--gap 0.02] [--kill-after 0]
  python p13_race.py --mode atomic [--iters 400] [--pad 500000] [--kill-after 0]

行为：主进程当**读者**（循环 json.loads 目标文件，记失败次数）；写者子进程按模式刷写同一文件。
  --kill-after S：跑 S 秒后 SIGKILL 写者（模拟「手工中断/被杀」），随后检查终态文件是否仍可解析。
输出末行：`RACE mode=<m> reads=<n> bad=<m> killed=<0|1> final_ok=<0|1> tmp_left=<n>`
判据：atomic ⇒ bad=0（读者永不见半截）且被杀后 final_ok=1；naive ⇒ bad>0 且被杀后 final_ok 常为 0（旧写法会留半截）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]                      # docs/audits/<dir>/ -> MAAOrch 根
TOOLS = ROOT / "tools"


def _payload(i, pad):
    return json.dumps({"i": i, "pad": "x" * pad, "list": list(range(50))}, ensure_ascii=False)


def writer_main(a):
    target = a.target
    os.makedirs(os.path.dirname(target), exist_ok=True)
    done = 0
    try:
        if a.mode == "atomic":
            sys.path.insert(0, str(TOOLS))
            import stage_capability as sc  # noqa: PLC0415
            for i in range(a.iters):
                sc._write_text_atomic(target, _payload(i, a.pad))
                done += 1
        else:                                   # naive：旧写法（open(path,"w") 直写、分两半、中间留窗口）
            for i in range(a.iters):
                text = _payload(i, a.pad)
                half = len(text) // 2
                with open(target, "w", encoding="utf-8") as f:
                    f.write(text[:half])
                    f.flush()
                    os.fsync(f.fileno())
                    time.sleep(a.gap)
                    f.write(text[half:])
                done += 1
    finally:
        print("WRITER done=%d/%d rc=%d" % (done, a.iters, 0 if done == a.iters else 1), flush=True)
    return 0 if done == a.iters else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["naive", "atomic"], required=True)
    ap.add_argument("--reader-retry", action="store_true",
                    help="读者用 stage_capability._load_json（含替换瞬时重试）而不是裸 open+json.loads")
    ap.add_argument("--writer", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--target", default="")
    ap.add_argument("--iters", type=int, default=400)
    ap.add_argument("--pad", type=int, default=500000)
    ap.add_argument("--gap", type=float, default=0.02, help="naive 半写窗口（秒）")
    ap.add_argument("--kill-after", type=float, default=0.0, help=">0 = 到点了 SIGKILL 写者（手工中断冒烟）")
    ap.add_argument("--reader-gap", type=float, default=0.0, help="读者两次读之间的间隔秒（0=紧循环，病态极限）")
    a = ap.parse_args()
    if a.writer:
        return writer_main(a)

    sc = None
    if a.reader_retry:
        sys.path.insert(0, str(TOOLS))
        import stage_capability as sc  # noqa: PLC0415,F811

    target = a.target or str(HERE / ("_race_%s.json" % a.mode))
    with open(target, "w", encoding="utf-8") as f:
        f.write(_payload(0, a.pad))       # 预置有效文件：消除「文件还没建」的启动噪声
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--writer",
                          "--mode", a.mode, "--target", target, "--iters", str(a.iters),
                          "--pad", str(a.pad), "--gap", str(a.gap)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         encoding="utf-8", errors="replace")
    torn, enoent, eacces, other, good = 0, 0, 0, 0, 0
    killed = False
    out = ""
    t0 = time.time()
    while True:
        if a.kill_after and time.time() - t0 > a.kill_after and p.poll() is None:
            p.kill()
            killed = True
            break
        try:
            if sc is not None:
                sc._load_json(target)            # 工具内的读法（含重试）
            else:
                with open(target, "r", encoding="utf-8") as f:
                    json.loads(f.read())
            good += 1
        except ValueError:                       # 半截 JSON（读者抓到的伤口——本质缺陷）
            torn += 1
        except FileNotFoundError:                # 替换瞬间名字不存在
            enoent += 1
        except PermissionError:                  # Windows 共享锁拒绝打开
            eacces += 1
        except OSError:                          # 其它 IO 错误（单独记，别混进 torn）
            other += 1
        if a.reader_gap:
            time.sleep(a.reader_gap)
        if p.poll() is not None:
            out = p.communicate()[0] or ""
            break
    if killed:
        out = p.communicate()[0] or ""
    p.wait()
    done_line = next((ln for ln in (out or "").splitlines() if ln.startswith("WRITER ")), "(无 WRITER 行)")
    err_line = ""
    for ln in (out or "").splitlines():
        if ln and not ln.startswith("WRITER ") and "Error" in ln:
            err_line = ln[:90]
            break
    # 终态：文件必须可解析（被杀后不许留半截）
    final_ok = 0
    try:
        with open(target, "r", encoding="utf-8") as f:
            json.loads(f.read())
        final_ok = 1
    except Exception:  # noqa: BLE001
        final_ok = 0
    tmp_left = len([x for x in os.listdir(os.path.dirname(target)) if ".tmp." in x])
    print("RACE mode=%s reader=%s gap=%.0fms good=%d torn=%d enoent=%d eacces=%d other=%d [%s]%s killed=%s final_ok=%d tmp_left=%d"
          % (a.mode, "retry" if a.reader_retry else "raw", a.reader_gap * 1000, good, torn, enoent,
             eacces, other, done_line, (" 异常=" + err_line) if err_line else "",
             "1" if killed else "0", final_ok, tmp_left))
    return 0


if __name__ == "__main__":
    sys.exit(main())
