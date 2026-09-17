#!/usr/bin/env python3
"""跑一份抄作业（MAA Copilot）—— 把作业站的作业真正执行掉。

为什么用子进程 + C API（而不是 MAA GUI）:
  · 项目既定做法: in-process MaaCore 会让项目进程崩（2026-08-12 事故），
    一律独立子进程（同 services/stage_probe.py）。
  · 作业站的下载在 GUI 层，MaaCore 只吃**本地文件**（DLL 里没有任何 http 代码），
    所以先自己把作业 json 拉下来（见 tools/maa_copilot_fetch.py），再喂给它。

抄作业的两个前提（MAA 官方文档）:
  · 帧率 ≥60 且稳定
  · **触控模式必须是 minitouch / maatouch / MuMu 触控增强** —— 纯 adb 点击
    达不到战斗时序精度（这也是我们自己 adb 部署手势不稳的原因）

用法:
    python tools/maa_copilot_run.py --emu 1 --copilot data/copilot/15-11_50051.json
    python tools/maa_copilot_run.py --emu 1 --copilot <path> --touch maatouch --no-startup
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from infrastructure.task_constants import detect_emu_adb, find_adb  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="跑 MAA 抄作业")
    ap.add_argument("--emu", default="1", help="模拟器索引")
    ap.add_argument("--instance", default="1", help="MAA 实例目录")
    ap.add_argument("--copilot", required=True, help="作业 json 路径")
    ap.add_argument("--client", default="Official")
    ap.add_argument("--touch", default="maatouch", help="minitouch / maatouch / adb")
    ap.add_argument("--no-startup", action="store_true", help="不附带 StartUp（游戏已在前台）")
    ap.add_argument("--timeout", type=float, default=600.0, help="跑作业的最长秒数")
    ap.add_argument("--loop", type=int, default=1, help="循环次数")
    ap.add_argument("--formation", dest="formation", action="store_true", default=True,
                    help="启用自动编队（默认开；这是 MAA 的 formation 参数，官方默认 false）")
    ap.add_argument("--no-formation", dest="formation", action="store_false",
                    help="不用自动编队（直接用当前编队）")
    ap.add_argument("--is-raid", action="store_true", default=False,
                    help="切险地/突袭（挑战模式）；走 copilot_list 的 is_raid 字段")
    ap.add_argument("--formation-index", type=int, default=0,
                    help="编入哪个编队位：0=当前编队，1~4=第1~4编队")
    ap.add_argument("--ignore-requirements", action="store_true",
                    help="忽略作业里的精英/等级/技能要求")
    ap.add_argument("--support", dest="support", action="store_true", default=True,
                    help="缺一组时借好友助战顶上（support_unit_usage=1，MAA 界面里叫「补漏」）")
    ap.add_argument("--no-support", dest="support", action="store_false",
                    help="不借助战（缺干员的作业会直接编队失败）")
    args = ap.parse_args()

    inst = (PROJ / "services" / "maa" / "instances" / str(args.instance)).resolve()
    cop = pathlib.Path(args.copilot)
    if not cop.is_absolute():
        cop = (PROJ / cop).resolve()
    if not cop.exists():
        sys.exit(f"作业文件不存在: {cop}")
    adb = find_adb() or "adb"
    addr = detect_emu_adb(args.emu)
    if not addr:
        sys.exit("取不到模拟器 adb 端口（MuMuManager）")
    py_dir = inst / "Python"
    log_path = inst / "debug" / "asst.log"
    print(f"实例 {inst}\n模拟器 #{args.emu} @ {addr}\n作业 {cop}\n触控 {args.touch}")

    code = "\n".join([
        "import sys, time",
        f"sys.path.insert(0, {str(py_dir)!r})",
        "from asst.asst import Asst, InstanceOptionType",
        f"Asst.load(path={str(inst)!r})",
        "asst = Asst()",
        f"asst.set_instance_option(InstanceOptionType.touch_type, {args.touch!r})",
        f"ok = asst.connect({adb!r}, {addr!r})",
        "if not ok:",
        "    print('CONNECT_FAIL'); sys.exit(1)",
        "print('CONNECTED')",
        ("    " and "if True:") if not args.no_startup else "if False:",
        f"    asst.append_task('StartUp', {{'client_type': {args.client!r}, 'start_game_enabled': True}})",
        # 官方协议：formation 默认 false（必须显式开，否则 MAA 跳过编队）
        ("cop_params = {'copilot_list': [{'filename': %r, 'is_raid': %r}]}" % (str(cop), args.is_raid))
        if args.is_raid else
        ("cop_params = {'filename': %r, 'loop_times': %r}" % (str(cop), args.loop)),
        "cop_params['formation'] = %r" % args.formation,
        "cop_params['formation_index'] = %d" % args.formation_index,
        ("cop_params['ignore_requirements'] = True") if args.ignore_requirements else "pass",
        ("cop_params['support_unit_usage'] = 1") if args.support else "pass",
        "asst.append_task('Copilot', cop_params)",
        "print('COPLOT_PARAMS', cop_params)",
        "asst.start()",
        f"dl = time.time() + {args.timeout}",
        "while time.time() < dl and asst.running():",
        "    time.sleep(3)",
        "asst.stop()",
        "print('DONE')",
    ])

    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           timeout=args.timeout + 120, encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
    except subprocess.TimeoutExpired:
        out, err = "", "子进程超时"
    print(f"--- 子进程（{time.time() - t0:.0f}s）---\n{out[-600:]}")
    if err:
        print("stderr:", err[-400:])

    # 从 asst.log 里取证: 导航/编队/缺失干员/结果
    print("\n--- asst.log 关键行 ---")
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        keys = ("Copilot", "OperatorMissing", "not found in formation", "BattleFormation",
                "UsePrts", "StartButton", "StageDrops", "MissionFailed", "MissionCompleted",
                "AllTasksCompleted", "错误", "ERROR", "ERR")
        hits = [l for l in lines[-8000:] if any(k in l for k in keys)]
        for l in hits[-25:]:
            print("  ", l.split("] ", 1)[-1][:150])
        if not hits:
            print("   （没抓到关键字，看 tail）")
            for l in lines[-8:]:
                print("  ", l[:150])
    except Exception as e:
        print("   读 asst.log 失败:", e)


if __name__ == "__main__":
    main()
