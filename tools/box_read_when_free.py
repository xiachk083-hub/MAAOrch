"""等模拟器空闲就自动读一次干员仓库（MAA OperBox），落盘缓存。

为什么（2026-09-17 用户："等任何一台空出来，立刻跑 box 读取 —— 这是最大的信息缺口"）:
没有 box，"这份作业这个号能不能用"就只能靠试，试一次几分钟 + 可能白花理智。
工具本身是好的（2026-09-16 实测读到 391 人），失败的前提只有一个：**MAA 起 OperBox 时
游戏不在能被接管的界面**（比如停在活动地图里），或者那台机器正被推图进程占着。

做法：轮询等 `emu_<N>.lock` 消失且没有 auto_push 在跑 → **自己抢同一把锁** →
跑 maa_operbox 并落盘 → 释放。抢锁是为了不和"刚好起来的新一轮推图"再撞车。

用法:
    python tools/box_read_when_free.py --emu 1 --uid 394162955 [--timeout 3600]
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import subprocess
import sys
import time

# Windows：不带这个标志，子进程（adb/ssh/ping）会**闪一个 cmd 黑窗** ✗
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
SR54 = pathlib.Path(r"C:\Users\xiach\.codex\visualizations\2026\09\16"
                    r"\01a0aaf9-9a07-7931-b068-f38e4064ea0f\sr54")


def ap_mod():
    spec = importlib.util.spec_from_file_location("auto_push", SR54 / "auto_push.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["auto_push"] = m
    spec.loader.exec_module(m)
    return m


def busy(emu: str) -> bool:
    lk = SR54 / f"emu_{emu}.lock"
    if lk.is_file():
        try:
            pid = int(lk.read_text(encoding="utf-8").split()[0])
        except Exception:
            return False
        AP._pid_alive(pid) and None
        if AP._pid_alive(pid):
            return True
    return False


def goto_lobby(AP) -> bool:
    """把游戏开回**大厅（首页）**—— MAA 的 OperBox 要从能被接管的界面起步。

    为什么必须（2026-09-17 实测）: MAA 的 OperBox 今天两次都返回 `own: []`（它认为这个号
    一个干员都没有，明显不对），而 04:16 成功过一次拿到全部干员 —— 差别就在**当时游戏停在大厅**。
    今天失败时游戏停在活动地图里，MAA 起不了这个任务。
    路径（1280×720 实测）：左上小房子(270,35) → 快捷导航浮层 → 点「首页」。
    """
    for _ in range(5):
        AP.dismiss_popups()
        img = AP.shot("tolobby")
        blob = " ".join(t for _x, _y, t in AP.texts(img))
        if "采购中心" in blob:                       # 只有大厅有「采购中心」
            print("[大厅] 已在大厅 ✓", flush=True)
            return True
        AP.tap(270, 35, 2.0)                         # 左上小房子
        AP.tap_text("首页", wait=3.0)                # 快捷导航浮层里的「首页」
        AP.back(1)
    print("[大厅] 5 轮没回到大厅 ✗", flush=True)
    return False


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--emu", required=True)
    a.add_argument("--uid", required=True)
    a.add_argument("--timeout", type=float, default=3600.0)
    args = a.parse_args()

    global AP
    AP = ap_mod()
    t0 = time.time()
    print(f"[等空机] 实例 {args.emu}（账号 {args.uid}）……", flush=True)
    while time.time() - t0 < args.timeout:
        if not busy(args.emu):
            break
        time.sleep(15)
    else:
        print("[放弃] 等太久了", flush=True)
        return

    AP.EMU = str(args.emu)
    AP.SERIAL = f"127.0.0.1:{16384 + 32 * int(args.emu)}"
    AP.ensure_device()
    AP.take_emu_lock()                      # 抢同一把锁：读 box 期间也不许推图进来
    try:
        goto_lobby(AP)                      # 先回大厅 —— MAA 的 OperBox 要从能接管的界面起步
        # **身份必须来自画面**（2026-09-17 用户："必须解决这个储存的问题"）:
        # 以前 tag 直接取 `--uid` 参数 ⇒ 参数写错/换号/别名回退时，box 会挂到**别的号**名下
        # （实测：V 名下有 396 人的 box=SquidP 的、「1」名下有 371 人的 ✗）。大厅那行
        # `RHODES ID:xxx` 是游戏原话 ⇒ 以它为准；与 --uid 不符就**报错停手**，不写文件。
        import re as _re
        _img = AP.shot("whoami")
        _blob = " ".join(t for _x, _y, t in AP.texts(_img)) if _img is not None else ""
        _ids = list(dict.fromkeys(_re.findall(r"(?<!\d)(\d{8,9})(?!\d)", _blob)))
        _uid_screen = _ids[0] if len(_ids) == 1 else ""
        if not _uid_screen:
            print(f"[闸] 读不出画面上的 RHODES ID（候选 {_ids}）⇒ 不写 box（别让文件挂错号）", flush=True)
            raise SystemExit(2)
        if _uid_screen != str(args.uid):
            print(f"[闸] 画面上的账号是 {_uid_screen}，而 --uid 是 {args.uid} ⇒ 停手不写"
                  f"（这正是 box 挂错号的入口）", flush=True)
            raise SystemExit(2)
        tag = f"{_uid_screen}_{time.strftime('%m%d_%H%M')}"
        print(f"[开始读] maa_operbox --emu {args.emu} --tag {tag}", flush=True)
        p = subprocess.run([sys.executable, str(PROJ / "tools" / "maa_operbox.py"),
                            "--emu", str(args.emu), "--instance", str(int(args.emu) + 1), "--tag", tag],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600, creationflags=NOWIN)
        out = (p.stdout or "") + (p.stderr or "")
        # 把身份**写进 box 文件本身**（文件名可以被改，文件内的 uid 是证据）
        try:
            import json as _json
            _bp = PROJ / "data" / f"operbox_{tag}.json"
            if _bp.is_file():
                _d = _json.loads(_bp.read_text(encoding="utf-8"))
                _d["uid"] = _uid_screen
                _bp.write_text(_json.dumps(_d, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"[身份] box 文件已写入 uid={_uid_screen}", flush=True)
        except Exception as e:                                 # noqa: BLE001
            print(f"[warn] 写 uid 失败：{type(e).__name__}: {e}", flush=True)
        (SR54 / f"box_{args.uid}.log").write_text(out, encoding="utf-8")
        ok = "没收到干员数据" not in out
        print(f"[结果] {'成功' if ok else '失败'} ｜ 尾部：{out.strip()[-200:]!r}", flush=True)
        # **读完必须把游戏放回大厅**（2026-09-17 血的教训）: MAA 读 box 会把游戏开到**干员页**，
        # 不收拾就走 ⇒ 紧接着起来的推图从那屏起步、`可见：[]`、报导航失败。
        # 三个号当时全是这个症状，而根因是**我自己的 box 读取**。
        # 归位逻辑统一收在 auto_push.cleanup_to_lobby()（推图/校准/读 box 共用一处）。
        AP.cleanup_to_lobby()
    finally:
        lk = SR54 / f"emu_{args.emu}.lock"
        lk.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
