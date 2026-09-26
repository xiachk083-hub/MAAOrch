"""扫 box —— 极简执行器第 ① 步：连模拟器 → 读 UID → 起 MAA 读 box → 存盘。

用法:
    python tools/scan_box.py --emu 2
    python tools/scan_box.py --emu 2 --uid 751737728      # OCR 读不准时人工指定

产物:
    data/boxes/box_<uid>.json        （规格要求的统一位置）
    data/operbox_<uid>.json          （MAA 读出来的原始件，同上内容）

规格（用户 2026-09-17《极简执行器》）:
    · **UID 从画面读**（大厅那行 `RHODES ID:…`），**不从旧 box 继承** ——
      继承的话换号后会把新号的 box 打上旧号的标签（串号）。
    · 只做：连模拟器 / 等主界面 / 读 box / 存盘 / 打印一行。
    · **不做**：不对比旧 box、不迁移状态、不判断进度。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from infrastructure.ocr import ocr_boxes                       # noqa: E402
from services.ai_agent.fastshot import find_adb                # noqa: E402

# Windows：不带这个标志，子进程（adb/ssh/ping）会**闪一个 cmd 黑窗** ✗
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BOX_DIR = PROJ / "data" / "boxes"


def serial_of(emu: str) -> str:
    """MuMu 12 第 N 个实例的 adb 端口 = 16384 + 32*N（实测规律）。"""
    return f"127.0.0.1:{16384 + 32 * int(emu)}"


def cap(adb: str, serial: str, timeout: float = 25.0):
    """抓一帧（BGR）。用 `screencap -p`：超时就 kill、**不等 communicate 收尾**
    （`nc` 那条路的超时是假的，能把进程永久卡死 —— 2026-09-17 实测）。"""
    try:
        p = subprocess.Popen([adb, "-s", serial, "exec-out", "screencap", "-p"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out = p.communicate(timeout=timeout)[0]
    except Exception:                                          # noqa: BLE001
        try:
            p.kill()                                           # type: ignore[possibly-undefined]
        except Exception:                                      # noqa: BLE001
            pass
        return None
    if not out or len(out) < 10000:
        return None
    import cv2
    import numpy as np
    return cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)


def ensure_device(adb: str, serial: str) -> None:
    st = subprocess.run([adb, "-s", serial, "get-state"], capture_output=True,
                        text=True, timeout=20, creationflags=NOWIN).stdout.strip()
    if st != "device":
        subprocess.run([adb, "connect", serial], capture_output=True, timeout=25, creationflags=NOWIN)
        time.sleep(2)


def read_uid(adb: str, serial: str, wait_lobby: float = 60.0) -> str:
    """等游戏停在**主界面**，再从画面读 UID（RHODES ID）。

    OCR 常常给出多个 8-9 位候选（实测 3 个）⇒ **不猜**：>1 个候选就直接抛错，让人 `--uid` 指定。
    """
    t0 = time.time()
    while time.time() - t0 < wait_lobby:
        img = cap(adb, serial)
        if img is None:
            time.sleep(2)
            continue
        ts = [b["text"] for b in (ocr_boxes(img) or [])]
        blob = " ".join(ts)
        if "采购中心" in blob or "终端" in blob:                # 只有大厅/终端页有这些
            near = [t for t in ts if re.search(r"\bID\b|RHODES|HODES", t, re.I)]
            cands: list[str] = []
            for t in (near or ts):
                cands += re.findall(r"(?<!\d)(\d{8,9})(?!\d)", t)
            uniq = list(dict.fromkeys(cands))
            if len(uniq) == 1:
                return uniq[0]
            raise SystemExit(f"[闸] 画面上的 UID 候选不唯一：{uniq} ⇒ 请用 `--uid <真实UID>` 指定"
                             f"（宁可人工给，也不猜错——猜错等于给 box 挂错标签）")
        time.sleep(3)
    raise SystemExit(f"[闸] 等了 {int(wait_lobby)} 秒没看到主界面 ⇒ 先把游戏停到大厅（首页）再跑："
                     "本脚本只读画面、不碰导航")


def read_box(emu: str, uid: str, timeout: float) -> dict:
    """读 box —— **复用现成的 `tools/box_read_when_free.py`**（它已经做全了：
    抢实例锁 → 先回大厅 → 跑 maa_operbox → 读完归位，这些我都不重写）。

    然后把它落下的 `data/operbox_<uid>_<日期>.json` 复制到规格要求的 `data/boxes/box_<uid>.json`。
    """
    r = subprocess.run([sys.executable, str(PROJ / "tools" / "box_read_when_free.py"),
                        "--emu", emu, "--uid", uid, "--timeout", str(timeout)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout + 180, creationflags=NOWIN)
    print((r.stdout or "").strip()[-300:])
    # **只认"刚刚读出来的"那一份**（2026-09-18 实测漏洞）：读被身份闸拒绝时，这里如果退而
    # 拿上一次的旧文件，就会把旧 box 复制成 `data/boxes/box_<uid>.json` ⇒ **看起来像扫成功了** ✗。
    # 判据：文件必须是这几分钟内落下的（读一次最多几分钟）。
    import time as _t
    fresh = [f for f in (PROJ / "data").glob(f"operbox_{uid}_*.json")
             if _t.time() - f.stat().st_mtime < 600]
    if not fresh:
        raise SystemExit("[闸] 这次没有**新读出**的 box（读被拒或没落盘）⇒ 什么都不写，"
                         "别拿旧文件冒充（看上面的输出：多半是画面上的账号与 --uid 不符）")
    f = max(fresh, key=lambda x: x.stat().st_mtime)
    return json.loads(f.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description="扫 box（极简执行器第 ① 步）")
    ap.add_argument("--emu", required=True, help="MuMu 实例号（端口 = 16384 + 32*N）")
    ap.add_argument("--uid", default="", help="人工指定 UID（OCR 读不准时用）")
    ap.add_argument("--timeout", type=float, default=420.0)
    a = ap.parse_args()

    adb, serial = find_adb(), serial_of(a.emu)
    ensure_device(adb, serial)
    uid = a.uid or read_uid(adb, serial)
    print(f"UID = {uid}")
    data = read_box(a.emu, uid, a.timeout)
    own = data.get("own") or []

    BOX_DIR.mkdir(parents=True, exist_ok=True)
    out = BOX_DIR / f"box_{uid}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    e2 = sum(1 for o in own if isinstance(o, dict) and o.get("elite") == 2)
    print(f"uid={uid}, N={len(own)}, 精二 {e2}")
    print(f"已存 {out}")


if __name__ == "__main__":
    main()
