"""全自动推图 —— 读节点 → 进关 → 抄作业 → 清剧情/结算/弹窗 → 下一关。

为什么需要（用户 2026-09-17）: 之前每关都要人盯着分步点，打完还卡在剧情/结算上。
这个脚本把闭环补齐：MAA 负责编队+战斗，脚本负责"战斗之外的一切界面"。

界面处理（全部靠 OCR 文本定位，不靠记忆坐标，除了三个实测死点）:
  · 「跳过」(1165,55) → 弹「是否跳过剧情?」 → 点右侧 ✓(800,461)   ← 用户指出的关键点
  · 「行动结束/任务失败」结算 → 点 (640,400) 点掉，直到看见地图节点
  · 「获得塔罗牌」奖励窗 → 点右上 ✕(1041,222)
  · 编队失败（SubTaskError）→ keyevent 4 退回详情页，换下一份作业

用法: python auto_push.py [--stages SR-5,SR-6,SR-7,SR-8] [--dry]
"""
from __future__ import annotations

import argparse
import atexit
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import time

import cv2
import numpy as np

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "services" / "ai_agent"))
from fastshot import find_adb, nc_capture                      # noqa: E402
from infrastructure.ocr import ocr_boxes                       # noqa: E402

ADB = find_adb()
EMU = "2"                                     # MuMu 实例号（端口 = 16384 + 32*实例号）
SERIAL = "127.0.0.1:16448"                     # 由 apply_target() 按 --emu 覆盖
JOB_DIR = PROJ / "data" / "copilot" / "event54_c"
STATE_PATH = HERE / "push_state.json"          # 推图状态（UI 只读它 + workspace 的作业存档）
ACCOUNT = ""                                   # 账号键（多开时区分状态/日志）
SCALE = 1.0                                    # 屏幕缩放：仓库里写死的坐标都按 1280×720


def ensure_device() -> None:
    """确认 SERIAL 这台在 adb 里真的存在，不在就 connect 一次。

    为什么必须有（2026-09-17 实测）: adb server 只认 `emulator-5554` 这种名字时，
    `adb -s 127.0.0.1:16448` 直接报 `device not found` ⇒ 抓帧全 None，整条链路静默失效。
    adb server 重启、模拟器重连都会把这个名字弄丢，所以每次运行前先补一次。
    """
    r = subprocess.run([ADB, "-s", SERIAL, "get-state"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=20)
    if (r.stdout or "").strip() == "device":
        return
    subprocess.run([ADB, "connect", SERIAL], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=20)
    r = subprocess.run([ADB, "-s", SERIAL, "get-state"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=20)
    if (r.stdout or "").strip() != "device":
        raise SystemExit(f"[闸] adb 里没有设备 {SERIAL}（get-state="
                         f"{(r.stdout or '').strip()!r}）——先确认这台模拟器在跑，"
                         f"再手动 `{ADB} connect {SERIAL}`")


def _cap_png(timeout: float = 25.0):
    """抓一帧（BGR）— 走 `adb exec-out screencap -p`，**不用 nc**。

    为什么把 nc 换掉（2026-09-17 实测，今晚最贵的一个坑）: `nc_capture` 里
    `subprocess.run(..., capture_output=True, timeout=N)` 超时后会先 `kill()` 再
    `communicate()` 收尾 —— 而 adb **客户端把 stdout 管道交给了 adb server 继承**，
    server 不退出 ⇒ 收尾**永远收不干净** ⇒ 超时形同虚设、整个推图进程被一条 adb 冻住。
    现场证据：一条 16:54:57 起的 `adb … exec-out "screencap | nc -w 3 10.0.2.2 …"` 一直挂在那里
    （我 16:57 抓到它时已挂约 3 分钟，**而这类挂法不会自己结束**），V 的推图日志从 16:54 起静默、
    进程却"活着在等"。这里改成 Popen + 超时就 kill 后**直接返回**（不再等 communicate 收尾）；
    screencap -p 这条路监控进程一直在用，稳。
    """
    try:
        p = subprocess.Popen([ADB, "-s", SERIAL, "exec-out", "screencap", "-p"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except Exception:                                    # noqa: BLE001
        return None
    try:
        out = p.communicate(timeout=timeout)[0]
    except subprocess.TimeoutExpired:
        p.kill()
        print(f"   [抓帧] adb screencap 超过 {timeout:.0f}s ⇒ 丢掉这一帧（不再等管道收尾）", flush=True)
        return None
    if not out or len(out) < 10000:
        return None
    try:
        return cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    except Exception:                                    # noqa: BLE001
        return None


def detect_scale() -> float:
    """按第一帧宽度算缩放系数（名字"1"那个实例是 1920×1080 ⇒ 1.5）。

    ⚠️ **两套坐标系，别混**（2026-09-17 修）:
      · **写死的坐标**（跳过剧情 1165,55 / 结算 640,400 / 弹窗按钮…）是按 1280×720 写的
        ⇒ 走 `tap()`，会自动乘 SCALE
      · **OCR / read_nodes 读出来的坐标**已经是帧自身的像素 ⇒ 走 `tap_px()`，**绝不能乘 SCALE**
    以前只有一个 `tap()` 一律乘 SCALE ⇒ 1080p 的实例（"1"）上 OCR 坐标被二次放大 1.5 倍，
    点「月行水上」永远点不中：日志里 `[导航] 进入活动「月行水上」` 连续打印 3 次、界面不动，
    最后记成 `SR-1 → 失败（导航失败）`。V/9036 是 1280×720（SCALE=1.0）所以没暴露。
    """
    f = _cap_png()                                 # 不用 nc：它会卡死（见 _cap_png 的说明）
    if f is None or getattr(f, "shape", (0, 0, 0))[1] == 0:
        raise SystemExit(f"[闸] 抓不到 {SERIAL} 的帧 ⇒ 缩放系数无从判断。"
                         f"宁可停，也别静默按 ×1.0 去点（1080p 的实例会全点歪）")
    return round(f.shape[1] / 1280.0, 3)


def apply_target(emu: str, account: str = "", pool: str = "",
                 allow_shared: bool = False) -> None:
    """选目标模拟器/账号/作业池 —— 多开同时打一个活动用。

    为什么要参数化（2026-09-17 用户又开了两个模拟器）: 以前 SERIAL 写死 16448、
    状态写死 push_state.json ⇒ 第二个账号一跑就把第一个的状态覆盖了。
    端口规律实测：MuMu 12 第 N 个实例 = 16384 + 32*N（0→16384 / 1→16416 / 2→16448）。

    **没传 --account 直接拦停**（2026-09-17 加）: 退回共享 `push_state.json` 是三个号
    互相覆盖的根因（04:24 前的日志里池名 `event54_c`、状态就是共享的那个）。宁可报错，
    也不让"忘了传参"又悄悄写出一个共享文件。
    """
    global SERIAL, STATE_PATH, JOB_DIR, EMU
    EMU = str(emu)
    SERIAL = f"127.0.0.1:{16384 + 32 * int(emu)}"
    JOB_DIR = PROJ / "data" / "copilot" / (pool or "event54_c")   # 不传池 = 退回默认池
    if account:
        globals()["ACCOUNT"] = account
        STATE_PATH = HERE / f"push_state_{account}.json"
    else:
        if not allow_shared:
            raise SystemExit(
                "[闸] 没传 --account ⇒ 会退回**共享** push_state.json，多个账号会互相覆盖"
                "（这就是 04:24 前三个号串号的根因）。请显式传 `--account <UID>`；"
                "确认只跑单账号、就是要用共享文件时才加 `--allow-shared`")
        globals()["ACCOUNT"] = ""                # 不传账号 = 退回默认；同一个进程里切成
        STATE_PATH = HERE / "push_state.json"
    ensure_device()

# ── 状态（六种，固定枚举）────────────────────────────────────────────
# 未解锁 / 未打 / 战斗中 / 三星 / 二星 / 失败 / 未确认    失败原因: 编队缺人/战斗失败/导航失败/超时
# 「未确认」= 跑完了但**没看到结算页**（2026-09-17 新增）。它既不宣称通关、也不当"战斗失败"，
# 调度按"未完成"处理、下次重试。加它的理由：以前"没看到结算"直接落到三星，把没开打的局
# （V 的 EX-3：35 秒、零个结算行、战斗从未开始）记成通关，于是整条链都在追不存在的关。
def load_state() -> dict:
    """读已有状态；没有就返回空骨架（**不重置**已有文件）。

    `account` 一律以 `--account` 为准：老代码这里硬编码了 `"9036"` 当默认值，而
    apply_target 设的 ACCOUNT **从来没写进状态字典** ⇒ 三个账号的状态文件全挂着
    "9036"，归属只能靠文件名认（2026-09-17 实测）。改数据不改这里必然复发。
    """
    try:
        st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        st = {"account": ACCOUNT, "activity": "act54side", "updated": "", "stages": {}}
    if ACCOUNT:
        st["account"] = ACCOUNT
    return st


def save_state(state: dict) -> None:
    """原子写（.tmp + os.replace）——写一半崩了不会损坏原文件。"""
    state["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def mark(stage: str, status: str = "", reason: str | None = None, **kw) -> None:
    """写状态。**关键**: 状态回到 战斗中/三星 等"没有失败含义"的档位时，
    必须把旧的 reason 清掉 —— 否则会出现 `status=战斗中` 还挂着 `reason=编队缺人`
    这种自相矛盾的条目（2026-09-17 实跑踩到）。"""
    st = load_state()
    # **突袭模式（`--raid`）写的是 `raid_*` 字段，绝不碰普通那套**（2026-09-17 用户：
    # "#1#2 他俩的突袭没有打完"）: 突袭和普通是两笔账 —— 一次突袭尝试**绝不能把
    # 「普通三星」覆盖成"战斗中/失败"**，否则中途崩一次，普通那 16/16 就花了。
    if globals().get("RAID_MODE"):
        rec = st["stages"].setdefault(stage, {"status": "未打"})
        if status:
            rec["raid_status"] = status
        if reason:
            rec["raid_reason"] = reason
        elif status in ("三星", "二星", "通关", "战斗中"):
            rec.pop("raid_reason", None)
        for k, v in kw.items():
            rec["raid_" + k] = v
        save_state(st)
        print(f"   [state·突袭] {stage} → {rec.get('raid_status')}"
              + (f"（{rec.get('raid_reason')}）" if rec.get("raid_reason") else "")
              + f" 作业 {rec.get('raid_job_used', '-')}", flush=True)
        return
    rec = st["stages"].setdefault(stage, {"status": "未打"})
    if status:
        rec["status"] = status
    if status in ("三星", "二星", "通关"):
        rec.pop("prev_status", None)            # 已经落到成交档，就不再需要"恢复用"的备份
    if rec.get("status") in ("战斗中", "三星", "未打", "未解锁", "跳过"):
        rec.pop("reason", None)
    elif reason:
        rec["reason"] = reason
    rec.update(kw)
    save_state(st)
    print(f"   [state] {stage} → {rec.get('status')}"
          + (f"（{rec.get('reason')}）" if rec.get("reason") else "")
          + f" 作业 {rec.get('job_used', '-')} 第 {rec.get('attempts', 1)} 次", flush=True)


def _tag() -> str:
    """文件名里的账号前缀（`394162955.`），没传 --account 时为空。

    **多开必须有前缀**（2026-09-17 实测）: 两个进程在同一个目录里跑，`shot_nodes.png` /
    `shot_nav.png` / `shot_ui.png` 是**同一个文件** —— A 刚写完、B 就覆盖，A 的 read_nodes
    子进程读到的其实是**B 的界面**。症状极具迷惑性：`navigate_to` 刚打印"目标 EX-4 已在
    当前视野"，紧接着 push_stage 重读同一屏却只剩 EX-1~3，然后记成"导航失败"。
    """
    return f"{ACCOUNT}." if ACCOUNT else ""


def shot(name: str = "ap"):
    """抓一帧并落盘。抓不到或**黑帧**就重试（实测 screencap 会间歇返回黑帧，
    集中在刚发完 input 之后）——重试仍失败返回 None，调用方各自有兜底。"""
    for _ in range(3):
        f = _cap_png()                             # 不用 nc（见 _cap_png 的说明：会把进程卡死）
        if f is None:
            time.sleep(0.6)
            continue
        if int(f.max()) > 20:                      # 全黑 = 这一帧不算数
            cv2.imwrite(str(HERE / f"shot_{_tag()}{name}.png"), f)
            return f
        time.sleep(0.6)
    return None


def texts(img) -> list[tuple[int, int, str]]:
    if img is None:                                 # 抓帧失败 → 当"没读到文字"，别让 OCR 抛
        return []
    return [(b["cx"], b["cy"], b["text"]) for b in (ocr_boxes(img) or [])]


def tap(x, y, wait=2.0):
    """点**写死的 1280×720 基准坐标**（自动乘 SCALE）。"""
    subprocess.run([ADB, "-s", SERIAL, "shell", "input", "tap",
                    str(int(x * SCALE)), str(int(y * SCALE))],
                   timeout=20)
    time.sleep(wait)


def tap_px(x, y, wait=2.0):
    """点**帧像素坐标**（OCR / read_nodes 读出来的）——已经是真实像素，**不乘 SCALE**。

    只给"坐标来自读图"的调用点用；写死的坐标请走 `tap()`。见 detect_scale 的说明。
    """
    subprocess.run([ADB, "-s", SERIAL, "shell", "input", "tap",
                    str(int(x)), str(int(y))],
                   timeout=20)
    time.sleep(wait)


def back(n=1):
    for _ in range(n):
        subprocess.run([ADB, "-s", SERIAL, "shell", "input", "keyevent", "4"], timeout=20)
        time.sleep(1.5)


def read_nodes(img) -> dict[str, tuple[int, int]]:
    """认地图节点。**优先进程内直接算**（2026-09-17 性能改造）；失败才退回子进程老路。

    为什么要改（用户实测 CPU 98%）: 老路每次都 `subprocess.run` 新起一个 Python 跑
    `tools/read_nodes.py` ⇒ **每调用一次 = 重启进程 + 重载 OCR 模型 ≈ 3.1 秒、吃满一核**；
    一关要调 5~20 次 ⇒ 单关 15~60 秒纯加载。而 `extract_nodes()` 本身就是 `ocr_boxes`
    的纯函数包装 —— 进程内调一次只要 ~0.2 秒，且 OCR 模型在本进程里**只加载一次**。

    **为什么不影响在跑的进程**（用户要求的确认）: Python 的代码对象在启动时加载进内存，
    磁盘上改文件**不会**改变已运行进程的行为；而本函数的 fallback 保证"进程内失败
    （如 import 失败）仍走老路"——两个路径的输出口径一致（同一套正则解析）。
    子进程路径保留着：授权闸（`--allow`/`--allow-prod`）由它继续承担。
    """
    if img is None:                                 # 抓帧失败 → 当"没读到节点"，别让 cv2 抛
        return {}
    p = HERE / f"shot_{_tag()}nodes.png"            # 带账号前缀，多开不互踩（见 _tag）
    cv2.imwrite(str(p), img)
    try:
        spec = importlib.util.spec_from_file_location("read_nodes_mod", PROJ / "tools" / "read_nodes.py")
        mod = getattr(read_nodes, "_mod", None)
        if mod is None:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)            # 只执行模块定义（不碰 __main__ 的 CLI 分支）
            read_nodes._mod = mod                   # 模块与 OCR 引擎都常驻，下次直接用
        nodes = mod.extract_nodes(str(p), "act54side")
        out = {}
        for n in nodes or []:
            code = n["code"].replace("SR-EX-", "EX-")
            x, y = int(n["x"]), int(n["y"])
            # **贴着画面右缘的"节点"不是地图节点**（2026-09-17 实测）: 终端页右下那张
            # 「前往上一次作战」卡片上就印着 `SR-EX-5`，照收的后果是（a）导航以为"已经在地图上"
            # 于是不去点区域入口、白滚 16 次（b）黄点采样窗口 x+80 出画 ⇒ 误判成"没通关"。
            # 地图节点标签实测都在画面左半边（殡仪堂 1280 宽下 x≈118–292）。
            if x > 0.88 * img.shape[1]:
                continue
            out[code] = (x, y)
        if out:
            return out
    except Exception as e:                          # noqa: BLE001 —— 进程内失败退回老路，不改变行为
        print(f"   [read_nodes] 进程内失败（{type(e).__name__}: {e}）→ 退回子进程", flush=True)
    r = subprocess.run([sys.executable, str(PROJ / "tools" / "read_nodes.py"),
                        "--emu", EMU, "--allow", SERIAL.split(":")[1], "--allow-prod",
                        "--frame", str(p), "--read"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120)
    out = {}
    # 两种节点名都要认（2026-09-17 加）：SR-7 → SR-7；**SR-EX-1 → EX-1**
    # （EX 区在活动主页的「殡仪堂」，节点印的就是 SR-EX-1，而状态表/按钮用的是 EX-1）
    _w = img.shape[1]
    for m in re.finditer(r"(SR-(?:EX-)?\d+)\s*→\s*\((\d+),\s*(\d+)\)", r.stdout or ""):
        code = m.group(1).replace("SR-EX-", "EX-")
        x, y = int(m.group(2)), int(m.group(3))
        # **贴着画面右缘的"节点"不是地图节点**（2026-09-17 实测）: 终端页右下那张
        # 「前往上一次作战」卡片上就印着 `SR-EX-5`，照收的后果是（a）导航以为"已经在地图上"
        # 于是不去点区域入口、白滚 16 次（b）黄点采样窗口 x+80 出画、判不了还误判成"没通关"。
        # 地图节点标签实测都在画面左半边（殡仪堂 1280 宽下 x≈118–292）。
        if x > 0.88 * _w:
            continue
        out[code] = (x, y)
    return out


# ── 「游戏原话」判据：节点右侧的实心黄点 = 该关已通关 ─────────────────────────
# 为什么必须用它（2026-09-17 用户看出来的）: 我们的状态文件会把"没看到结算页"的跑法记成三星，
# 于是 V 的 SR-EX-3 其实没通、状态却写三星 ⇒ 推图循环跳过 EX-3、去追**根本不存在**的 EX-4。
# 地图只显示到「已通关的最后一关 + 1」这个前沿，所以"找不到"往往不是滚动问题而是没解锁。
def has_yellow_dot(img, x: int, y: int) -> bool | None:
    """节点标签右侧有没有实心黄点（= 已通关）。黄点在 x+80..x+150、y±22 一带。

    **返回 None = 采样窗口根本不在画面里**，与"确实没黄点"必须分开（2026-09-17 实测）:
    终端页右下那张「前往上一次作战」卡片上印着 `SR-EX-5`，被当成地图节点收下
    （中心 x=1194，而画面宽只有 1280）⇒ x+80=1274 已经贴边、x+150 出画，采到一条空带
    ⇒ 旧写法返回 False ⇒ 被读成"前一关没有黄点" ⇒ **EX-6 被判未解锁，V 被锁在门外**。
    同一次还会让导航"以为已经在地图上"，白滚 16 次。
    """
    if img is None:
        return None
    h, w = img.shape[:2]
    x0, x1 = max(0, x + 80), min(w, x + 150)
    y0, y1 = max(0, y - 22), min(h, y + 22)
    if x1 - x0 < 40 or y1 - y0 < 20:            # 窗口被画面截掉 ⇒ 不敢下结论
        return None
    roi = img[y0:y1, x0:x1]
    b = roi[:, :, 0].astype(int)
    g = roi[:, :, 1].astype(int)
    r = roi[:, :, 2].astype(int)
    return int(((r > 190) & (g > 190) & (b < 130)).sum()) >= 40    # 一个点约 20×20 像素


def prev_stage(stage: str) -> str | None:
    """上一关（前置关）。EX-1 的前置是 SR-8（中间的 SR-ST-2/SR-TR-1 是 0 理智剧情关）。"""
    kind, num = stage.split("-")
    n = int(num)
    if n > 1:
        return f"{kind}-{n - 1}"
    return "SR-8" if kind.upper() == "EX" else None


def cleared_in_state(stage: str) -> bool:
    rec = (load_state().get("stages") or {}).get(stage) or {}
    return rec.get("status") in ("三星", "二星", "通关")


def stage_cleared_now(stage: str) -> bool | None:
    """从**当前画面**判 stage 通没通（黄点）。None = 判不了（**维持结算页的结论**）。

    ⚠️ **只对 EX 区（殡仪堂）有效**（2026-09-17 用户报障后修的）:
    通学路(SR 区)那张图的通关标记是节点左侧的**青色六边形**、**没有黄点**
    ⇒ 拿黄点去判 SR 会**恒返回"没黄点"**，于是把已经打赢的 SR 关也判成"没通关"，
    SR 链从此卡死：后面的关全不解锁、手点也没得打。
    （用户原话："第二关已经打过了，为什么会判失败？而且后面的都没有解锁"。）
    SR 区的判据还没验证过，所以这里**直接返回 None**、不参与判定。
    """
    if not stage.upper().startswith("EX-"):
        return None
    img = shot("after")
    nodes = read_nodes(img)
    if stage not in nodes:
        return None
    return has_yellow_dot(img, *nodes[stage])


def unlock_next(stage: str) -> None:
    """通关后把"下一关"从 未解锁 翻成 未打 —— 否则驱动会以为后面全锁着 ✗
    （2026-09-17 实测：EX-3 过了，驱动还说"没有可打的关了"，因为 EX-4 挂着未解锁）"""
    kind, num = stage.split("-")
    if int(num) >= 8:
        return
    nxt = f"{kind}-{int(num) + 1}"
    if ((load_state().get("stages") or {}).get(nxt) or {}).get("status") == "未解锁":
        mark(nxt, status="未打")


# ── 活动导航（2026-09-17 用户："导航你搞的不太行，每次都是上一个"）─────────────
# 目标：从**任何界面**（终端 / 弹窗 / 活动主页 / 地图）把游戏开到目标关，
# 不再靠人手截图瞎点。只点 OCR 认得出的文字和固定按钮，**不盲点**。
def dismiss_popups(tries: int = 4) -> None:
    """关掉挡路的弹窗。**只点 ✗/✓，不按返回键**（返回键会弹出"是否确认退出游戏"）。"""
    for _ in range(tries):
        img = shot("nav")
        if img is None:
            return
        blob = " ".join(t for _x, _y, t in texts(img))
        if "是否确认退出游戏" in blob:
            tap(448, 493)          # 左边 ✗ = 取消；右边是"确认退出"，绝不能点
            continue
        if "获得物资" in blob:
            tap(641, 634)          # 奖励弹窗的 ✓
            continue
        if "公告" in blob:
            tap(1204, 96)          # 右上角 X
            continue
        break


def _sim(a: str, b: str) -> float:
    """两段文字的相似度 = 逐字集合的 Jaccard（容忍 OCR 错字）。"""
    sa, sb = set(a.strip()), set(b.strip())
    return len(sa & sb) / len(sa | sb) if (sa or sb) else 0.0


def tap_text(needle: str, wait: float = 5.0, skip: tuple[str, ...] = ("解锁",),
             fuzzy: float = 0.5) -> bool:
    """在当前屏幕上 OCR 找到这段文字并点它（找不到返回 False）。

    **先"整体相等"再退"包含"，最后容忍错字**，并跳过 `skip` 里的词所在的框：
      · 活动主页上同时存在「通学路」入口和锁住提示「完成「通学路」后解锁」⇒ 只做包含匹配
        会先命中后者、点在锁住的图标上（2026-09-17 实测：连点两次界面不动）。
      · OCR 会把「月行水**上**」读成「月行**永**上」⇒ 严格子串匹配直接失败、导航整轮空转
        （2026-09-17 实测：V 的 EX-4 就这样被记成"导航失败"）。
    """
    img = shot("nav")
    if img is None:
        return False
    cands = [(x, y, t) for x, y, t in texts(img)
             if not any(s in t for s in skip)]
    for x, y, t in cands:                       # ① 整体相等最可靠
        if t.strip() == needle.strip():
            tap_px(x, y, wait)                  # OCR 坐标 = 帧像素，不能再乘 SCALE
            return True
    for x, y, t in cands:                       # ② 包含（例：图上写"月行水上。"带句号）
        if needle in t:
            tap_px(x, y, wait)
            return True
    if len(needle.strip()) >= 3:                # ③ 容忍错字；短词太易误命中，不做模糊
        near = [c for c in cands if abs(len(c[2].strip()) - len(needle.strip())) <= 1]
        best = max(near, key=lambda c: _sim(needle, c[2]), default=None)
        if best is not None and _sim(needle, best[2]) >= fuzzy:
            print(f"   [导航] OCR 读成 {best[2]!r}，按相似度当 {needle!r} 点", flush=True)
            tap_px(best[0], best[1], wait)
            return True
    return False


def scroll_map(direction: str = "right") -> None:
    """活动地图是横向"路线"，节点会滚出视野 → 左右拖。"""
    x1, x2 = (1120, 120) if direction == "right" else (120, 1120)
    y = 420
    subprocess.run([ADB, "-s", SERIAL, "shell", "input", "swipe",
                    str(int(x1 * SCALE)), str(int(y * SCALE)),
                    str(int(x2 * SCALE)), str(int(y * SCALE)), "450"], timeout=20)
    time.sleep(1.8)


def goto_lobby(verbose: bool = True) -> bool:
    """把游戏开回**大厅（首页）**——任何"动过游戏"的动作结束后都该回到这里再交棒。

    为什么必须（2026-09-17 血的教训，用户点名要的收尾）: **根因常常不是"某一环坏了"，
    而是"链条上少一环"** —— box 读取正常、推图执行也正常，缺的是中间那个收尾：
    MAA 读 box 会把游戏开到**干员页**，读完不收拾就走 ⇒ 紧接着起来的推图从那屏起步，
    `可见：[]`、报导航失败（三个号当时全是这症状，根因是我自己）。
    同类还有：MAA 退出时停在结算页/编队页。

    路径（1280×720 实测）：左上小房子(270,35) → 快捷导航浮层 → 点「首页」。
    """
    for _ in range(5):
        dismiss_popups()
        img = shot("tolobby")
        blob = " ".join(t for _x, _y, t in texts(img))
        if "采购中心" in blob:                       # 只有大厅有「采购中心」
            if verbose:
                print("   [归位] 已在大厅 ✓", flush=True)
            return True
        tap(270, 35, 2.0)                            # 左上小房子
        tap_text("首页", wait=3.0)                   # 快捷导航浮层里的「首页」
        back(1)
    if verbose:
        print("   [归位] 5 轮没回到大厅 ✗（下一个动作起点可能不干净）", flush=True)
    return False


# ── 云端视觉：**本机不花 CPU** 的"看一眼" ─────────────────────────────────────
# 为什么（2026-09-17 用户点名："而且不是填了那个API嘛，你直接让模型看就是了"）:
# 本机 OCR 唯一的毛病是**只会读字、不会理解这一屏是什么**，而且它是这台机器最大的 CPU 开销
# （CPU 版 onnxruntime，用不了显卡；一次全屏 2~4 CPU 秒，拖慢模拟器渲染）。
# 实测视觉调用 **1.6 秒 / 约 800 token / 本机 CPU 0**，给的坐标和本地 OCR 一致
# （EX-7 节点：模型 x=0.16 y=0.35 ↔ OCR 的 196,253 换算 0.153/0.351）。
# 所以分工：**高频、只认字**的（轮询弹窗、读节点坐标）继续本地 OCR；
# **低频、要理解**的（卡住了、不知道在哪一屏、该点哪）交模型 —— 一关最多几次。
VISION_SYS = ("你是明日方舟模拟器（MuMu，1280×720）的界面助手。回答必须极简："
              "第一行一句话说这是什么界面；第二行只给要点击位置的**相对坐标**，"
              "形如 `x=0.16 y=0.35`（整幅图左上角 0,0、右下角 1,1）。")


def _vision_ask(img, question: str, budget: int = 700) -> str:
    """问云端视觉模型看一眼；任何失败都**只是返回空串**，绝不把推图带崩。"""
    if img is None:
        return ""
    try:
        sys.path.insert(0, str(PROJ))
        from services.ai_agent.llm import LLM        # 延迟导入：AI 通道坏了也不影响推图
        llm = LLM()
        if not llm.vision_ready:
            print("   [视觉] 没配 vision_model ⇒ 跳过", flush=True)
            return ""
        r = llm.chat(system=VISION_SYS, user=question, image=img, vision=True,
                     max_tokens=budget, temperature=0)
        if not r.get("ok"):
            print(f"   [视觉] 调用失败：{r.get('error')}", flush=True)
            return ""
        return (r.get("text") or "").strip()
    except Exception as e:                            # noqa: BLE001
        print(f"   [视觉] 用不了（{type(e).__name__}: {e}）", flush=True)
        return ""


def _vision_point(text: str):
    """从模型回答里抠相对坐标（`x=0.16 y=0.35` 或 `(0.16, 0.35)` 两种写法都认）。"""
    m = (re.search(r"x\s*[=:]\s*([0-9.]+)[^0-9]+y\s*[=:]\s*([0-9.]+)", text, re.I)
         or re.search(r"\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\)", text))
    if not m:
        return None
    x, y = float(m.group(1)), float(m.group(2))
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return None
    return x, y


def vision_tap(img, question: str) -> bool:
    """让模型看一眼、点它说的位置。返回是否真的点了。"""
    ans = _vision_ask(img, question)
    if not ans:
        return False
    print(f"   [视觉] {ans.splitlines()[0][:64]}", flush=True)
    pt = _vision_point(ans)
    if pt is None:
        print(f"   [视觉] 没抠到坐标：{ans[:90]!r}", flush=True)
        return False
    h, w = img.shape[:2]
    tap_px(int(pt[0] * w), int(pt[1] * h), 3.5)
    print(f"   [视觉] 已点 ({pt[0]:.2f},{pt[1]:.2f}) → 像素 ({int(pt[0] * w)},{int(pt[1] * h)})", flush=True)
    return True


def cleanup_to_lobby() -> None:
    """**交棒前统一收尾**：任何会动游戏的动作（推图 / 校准 / 读 box）结束时都调它。

    这样就不必逐个去修"XX 之后记得回大厅"—— 一处规矩、三处生效。
    收尾失败不该把主流程带崩，所以整段兜住。
    """
    try:
        goto_lobby()
    except Exception as e:                            # noqa: BLE001
        print(f"   [归位] 失败（{type(e).__name__}: {e}）—— 不影响本次结论", flush=True)


def navigate_to(stage: str, verbose: bool = True) -> bool:
    """把游戏开到目标关（节点出现在地图上，或已经站在它的详情页）。"""
    want_area = "殡仪堂" if stage.upper().startswith("EX") else "通学路"
    for _round in range(3):
        dismiss_popups()
        img = shot("nav")
        blob = " ".join(t for _x, _y, t in texts(img)) if img is not None else ""

        # ① 已经在地图上（能看到节点名）
        if re.search(r"SR-(?:EX-)?\d+", blob):
            # **先看眼前这一屏**（2026-09-17：原来一上来就左拖两次，目标就在眼前也被拖走 ✗）
            nodes_here, _img = read_nodes_retry()
            if stage in nodes_here:
                if verbose:
                    print(f"   [导航] 目标 {stage} 已在当前视野", flush=True)
                return True
            # 图上印的是 SR-EX-N（殡仪堂）；read_nodes 会把它们归一成 EX-N
            cur_area = "殡仪堂" if any(k.startswith("EX-") for k in nodes_here) else "通学路"
            if cur_area != want_area:
                # **走错区了就别扫**（2026-09-17）: 在错误的区里滚 16 个位置纯属烧时间
                # （每个位置一次 read_nodes = 3.1 秒）。退出去换区，让下一轮走 ②。
                if verbose:
                    print(f"   [导航] 现在在「{cur_area}」、目标是「{want_area}」⇒ 退出去换区", flush=True)
                back(1)
                continue
            # **在对的区里只扫一轮**（2026-09-17）: 那张地图实测**不可滚动**（上下左右四个方向
            # 各拖两次，节点集合一个字都不变），而且地图只画到「已通关的最后一关 + 1」——
            # 找不到通常意味着**那关还没解锁**，不是"再扫几轮就出来了"。
            # 原来看不到就 `continue` 再来两轮，每轮 16 个位置 ⇒ 一关白烧 3~5 分钟。
            for _ in range(8):
                if stage in read_nodes(shot("nodes")):
                    if verbose:
                        print(f"   [导航] 目标 {stage} 已在地图上", flush=True)
                    return True
                scroll_map("right")
            for _ in range(8):                     # 往右没找到 → 回头往左找
                scroll_map("left")
                if stage in read_nodes(shot("nodes")):
                    if verbose:
                        print(f"   [导航] 目标 {stage} 已在地图上（回滚找到）", flush=True)
                    return True
            if verbose:
                print(f"   [导航] 在「{cur_area}」扫完一轮没有 {stage} ⇒ 收手（多半是没解锁）", flush=True)
            return False

        # ② 在活动主页 → 点区域入口（通学路 / 殡仪堂）
        if want_area in blob and tap_text(want_area):
            if verbose:
                print(f"   [导航] 进入「{want_area}」", flush=True)
            continue

        # ②.5 在大厅 → **先打开「终端」**（2026-09-17 V 的 EX-6 根因，同一份代码
        #   9036 过了、V 连挂两轮 ⇒ 不是"坏"，是**靠运气**）。根因：大厅右上那张活动横幅
        #   是**轮播**的（同一位置时而是活动、时而是别的），OCR 也时有时无 ⇒ 第③步
        #   在大厅就误命中、点了横幅又没进活动。「采购中心」只有大厅有；改成先点固定
        #   大目标（终端卡片）再走 ③，把运气成分去掉。
        if "采购中心" in blob:
            if verbose:
                print("   [导航] 在大厅 ⇒ 先开「终端」", flush=True)
            if not tap_text("终端", wait=3.0):       # 卡片上的「终端」标签
                tap(880, 200, 3.0)                   # 兜底：终端卡片中心（1280×720 基准）
            continue

        # ③ 在终端 → 点活动卡（月行水上）
        if "终端" in blob and tap_text("月行水上"):
            if verbose:
                print("   [导航] 进入活动「月行水上」", flush=True)
            continue

        # ④ 都不是 → 退一级再来（返回键可能弹退出对话框，下一轮会被 dismiss_popups 关掉）。
        # **但大厅里绝不按返回**（2026-09-17 用户："他为什么每次都要回到桌面，然后再去啊"）:
        # 大厅按返回会弹「退出游戏」确认框 —— 万一没关掉/点错，游戏就**退回桌面**，
        # 下次又得重新唤醒+进活动，越拖越慢。大厅没招时宁可空转一轮。
        if "采购中心" in blob:
            print("   [导航] 在大厅且这一轮没招 ⇒ 不按返回（按了会弹退出游戏）", flush=True)
            time.sleep(1.0)
            continue
        back(1)
    # **规则走不通了 → 交给视觉模型接管**（本机零 CPU，实测 1.6s/次、约 800 token）。
    # 为什么让它当"救火队"而不是只问一次坐标（2026-09-17 实测）：这一整晚卡住的关，
    # 几乎全是"字读到了、意思判错了"——大厅轮播横幅当按钮 / 终端页当地图 / 地图当详情页 /
    # 在活动主页又把「月行水上」点了一次、结果进了通学路。这些都是**需要理解这一屏**的问题，
    # 而"这是什么界面、现在最该点哪"正是视觉模型的强项（它一眼就认出了那帧是「终端（主界面）」）。
    for _try in range(4):
        img = shot("vision_" + stage)
        if img is None:
            break
        ans = _vision_ask(
            img,
            f"这是明日方舟模拟器截图（1280×720）。我的目标：进入活动关卡「{stage}」，"
            f"它在活动区「{want_area}」里（另一个区叫「"
            f"{'通学路' if want_area == '殡仪堂' else '殡仪堂'}」，不要点它）。\n"
            f"请先一句话说这是哪一屏（大厅/终端/活动主页/关卡地图/关卡详情页/其他），"
            f"第二行只给**现在最该点的一个位置**的相对坐标（形如 x=0.16 y=0.35）：\n"
            f"- 已经在关卡地图上且能看到 {stage} ⇒ 直接给该关卡节点的坐标；\n"
            f"- 还没进活动 ⇒ 点进活动的入口（大厅点终端卡片、终端点活动卡、活动主页点「{want_area}」）；\n"
            f"- 进错区了（看到的是另一个区的关卡，如 {"SR-" if want_area == "殡仪堂" else "SR-EX-"} 开头的）⇒ 给返回键的坐标。")
        if not ans:
            break
        print(f"   [视觉] {ans.splitlines()[0][:64]}", flush=True)
        pt = _vision_point(ans)
        if pt is None:
            print(f"   [视觉] 没抠到坐标：{ans[:90]!r}", flush=True)
            break
        h, w = img.shape[:2]
        tap_px(int(pt[0] * w), int(pt[1] * h), 3.5)
        img2 = shot("nodes")
        nodes2 = read_nodes(img2) if img2 is not None else {}
        print(f"   [视觉] 点了 ({pt[0]:.2f},{pt[1]:.2f}) ⇒ 可见 {sorted(nodes2)}", flush=True)
        if stage in nodes2:
            print(f"   [视觉] 导航成功（{stage} 已在地图上）", flush=True)
            return True
    return False


def handle_ui(max_seconds: float = 90.0) -> str:
    """把当前屏幕上的一切"非地图"界面清掉。返回最后看到的文字摘要。

    返回值的约定（2026-09-17 加）：看到结算页时**先记账再点掉**，最后把结论带出来 ——
    `map:won`（行动结束）/ `map:lost`（任务失败）/ `map`（没看到结算）。只有"行动结束"
    才算赢：以前只看"回到地图"就当三星，可**打输也会回地图**，于是输赢都记三星 ✗。
    """
    t0, seen, verdict = time.time(), [], ""
    prev_small = None
    _idle = 0                                    # 连续看到"大厅/终端"的帧数（≥2 就早退，别白等 30 秒）
    while time.time() - t0 < max_seconds:
        img = shot("ui")
        if img is None:
            time.sleep(1)
            continue
        # **画面没变就别再 OCR**（2026-09-17 用户点名"CPU 占用这么高不行的呀，
        # 还会影响模拟器它自己游戏的运行"）: 一次全屏 OCR 要 2~4 个 CPU 秒，而这个循环
        # 原来是每 1.5 秒重做一遍 —— 结算页、等待页**都是静止的**，重复识别纯属白烧。
        # 判据用缩到 160×90 的灰度图求平均差（微秒级）：画面没变 ⇒ OCR 结果必然一样；
        # 一旦有弹窗/转场，画面必变 ⇒ 照旧识别，**不会漏**。
        small = cv2.cvtColor(cv2.resize(img, (160, 90)), cv2.COLOR_BGR2GRAY)
        if prev_small is not None and cv2.absdiff(small, prev_small).mean() < 1.0:
            time.sleep(0.8)
            continue
        prev_small = small
        ts = texts(img)
        blob = " ".join(t for _x, _y, t in ts)
        seen.append(blob[:60])
        if "是否跳过剧情" in blob:
            tap(800, 461); continue
        if "跳过" in blob and "行动结束" not in blob and "任务失败" not in blob:
            tap(1165, 55); continue
        if "塔罗牌" in blob:
            tap(1041, 222); continue
        if "行动结束" in blob or "任务失败" in blob:
            # **失败结算也写「行动结束」**（2026-09-17 实测原文：`行动结束 MISSIONRESULTS …
            # 就差一点点 … 理智返还`）。所以要认这三个失败信号，缺一个就会把败仗记成三星：
            #   任务失败 / MISSION FAILED（第一页）、理智返还（后面几页）
            if ("任务失败" in blob or "MISSION FAILED" in blob or "理智返还" in blob):
                verdict = "lost"
            elif "演习" in blob or "PRACTICE" in blob.upper():
                # 演习（PRACTICE）的结算页也写「行动结束」，但不扣理智、不记录通关 ⇒ 不算赢
                verdict = "drill"
            else:
                verdict = "won"
            # 把结算页原文打出来：下次判错时，日志里就能直接看到它当初写的是什么
            print(f"   [结算] {verdict} ｜ {blob[:90]}", flush=True)
            tap(640, 400, 3.0); continue
        if re.search(r"SR-\d+", blob) and ("开始行动" in blob or "代理指挥" in blob):
            return "detail"                     # 已经站在某关详情页
        # **大厅/终端这种"无事可做"的界面别白等满 30 秒**（2026-09-17 用户："ex-8没有在打"
        # —— 实测每关开局都先在这里空转 30 秒，看着就像没动静）。这类屏没有结算要清、
        # 没有剧情要跳，进活动/选关是 navigate_to 的活 ⇒ 连看两帧都确认是大厅/终端就早退。
        if "采购中心" in blob or "终端" in blob:
            _idle += 1
            if _idle >= 2:
                return "timeout:" + blob[:60]
            time.sleep(1.0)
            continue
        _idle = 0
        # 地图判定：**≥2 个节点名就够**，但必须在"详情页"分支之后（详情页也有 SR-7/SR-8 两条，
        # 外加 开始行动/代理指挥）。原来要求 ≥3 个节点 —— 而晚场这张图只露出 SR-7/SR-8 两个，
        # 于是**打赢也会被判成"超时"**，状态机一直记错（2026-09-17 实测：SR-8 打完回地图，
        # 却被写成 失败（超时））。
        # 节点名也要认 EX（殡仪堂那张图印的是 "SR-EX-1"，不是 "SR-1"）
        n_nodes = sum(1 for t in ts if re.match(r"SR-(?:EX-)?\d+$", t[2].strip()))
        if n_nodes >= 2 and "开始行动" not in blob and "代理指挥" not in blob:
            return f"map:{verdict}" if verdict else "map"
        # EX 区（活动主页「殡仪堂」）那张图上**只露一个节点**，≥2 永远不成立 ⇒ 见过结算结论
        # 之后只要还有 ≥1 个节点名、且不是详情页，就算回到地图了（2026-09-17 实测）。
        if verdict and n_nodes >= 1 and "开始行动" not in blob and "代理指挥" not in blob:
            return f"map:{verdict}"
        time.sleep(1.5)
    return "timeout:" + (seen[-1] if seen else "")


def kill_stale_runners() -> int:
    """干掉**连着我们这台模拟器、但不是本进程拉起来的** MAA 运行器（孤儿）。

    为什么必须有（2026-09-17 用户点名"进程管理 bug"）: 推图进程被杀（或自己崩）时，
    它 `subprocess.run` 起的 MAA 运行器**不会跟着死** —— 那台模拟器上就同时有了两个控制器
    （一个在抄作业、一个在导航），现场表现是"游戏莫名跑到别的画面"。
    实测抓到过一个跑了 35 分钟的孤儿，一直在用 V 的模拟器跑 EX-7 的作业。
    调用点：`run_job` 开头（**先清场再开打**），此时本进程还没起自己的运行器，不会误杀。
    """
    port = SERIAL.split(":")[1]
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -match 'asst\\.asst' -and "
          f"$_.CommandLine -match ':{port}' }} | ForEach-Object {{ $_.ProcessId }}")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:                                    # noqa: BLE001 —— 清场失败不该拦推图
        return 0
    killed = 0
    for tok in out.split():
        if not tok.strip().isdigit():
            continue
        pid = int(tok.strip())
        if pid == os.getpid():
            continue
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=15)
        except Exception:                                # noqa: BLE001
            continue
        print(f"   [孤儿] 干掉连在 {SERIAL} 上的旧 MAA 运行器 pid={pid}", flush=True)
        killed += 1
    return killed


def run_job(job: pathlib.Path, tag: str) -> dict:
    # 多开时日志要带账号前缀，否则两个号的"尝试历史"会串（UI 按这个文件名解析关卡/作业）
    log = HERE / f"maa_{ACCOUNT + '_' if ACCOUNT else ''}{tag}.log"
    if log.exists():
        log.unlink()
    kill_stale_runners()          # **先清场再开打**：上一轮留下的孤儿运行器会跟我们一起抢这台模拟器
    t0 = time.time()
    p = subprocess.run([sys.executable, str(PROJ / "tools" / "maa_copilot_run.py"),
                        "--emu", EMU, "--copilot", str(job),
                        # 每台模拟器一个 MAA 实例（三个号曾全挤在 instances/1 ⇒ 并跑互相打断）
                        "--instance", str(int(EMU) + 1),
                        # **忽略作业里的精英/等级/技能要求**（2026-09-17 用户："一切为通关服务"）:
                        # 编队缺人的主因就是这个 —— 9036 的日志原文 `check_oper_level | Elite 1,
                        # require: 2`：干员有、但作业要求精英 2。不忽略就永远编不满、一关都过不了。
                        # **「忽略干员属性要求」= MAA 面板上那个勾**（2026-09-17 用户贴了面板）:
                        # 开着 ⇒ 编队必成（绕开精英/等级门槛），但**挂件可能是一堆没练的**
                        # ⇒ 核再强轴也崩（今晚 9036 的 EX-7 第一次就这么输的）；作业作者也明写
                        # "请勿勾选"。想关掉：启动前设 `MAA_IGNORE_REQ=0`（默认保持开 = 今晚
                        # 33 关是开着过的，不擅自翻默认值）。
                        *(["--ignore-requirements"]
                          if os.environ.get("MAA_IGNORE_REQ", "1") != "0" else []),
                        # MAA 跑作业的最长秒数：420 太短 —— "单核"作业本来就要拖很久，
                        # 2026-09-17 实测 102169 跑到 423s 被这里掐断，游戏里那场还在打（20/21 生命）✗
                        "--touch", "maatouch", "--no-startup", "--timeout", "900",
                        "--formation-index", str(int(globals().get("FORMATION_INDEX") or 0))],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=900)
    out = (p.stdout or "") + (p.stderr or "")
    log.write_text(out, encoding="utf-8")
    # **"编队缺人"和"点不到开始行动"必须分开**（2026-09-17 更正）: 原来只要是
    # "BattleFormationTask"+"SubTaskError" 就记编队缺人 —— 但 V 的 EX-3 日志里
    # 编队明明是 `SubTaskCompleted`，失败的是 `first:["BattleStartAll"]`（找不到开始按钮），
    # 却被记成"编队缺人"，把排查方向带偏了一路。
    # **asst.log 里连"子任务清单"都写着 `"first":["BattleStartAll"]`** ⇒ 拿子串去找一定会中，
    # 但那不代表"它出错了"（旧写法漏个引号反而歪打正着不算，但方向也是错的）。
    # 正确判据 = **第一个 SubTaskError 的子任务名**（后面的错常是它的连锁反应）:
    #   BattleStartPre 挂 = MAA 压根没认出"关卡详情页"（9036 的 EX-7：42 秒里连挂 3 次，
    #   编队对着地图瞎点 26 秒，最后还被记成"编队缺人"——把人引向"是不是缺干员"的错误方向）。
    _m = re.search(r'SubTaskError.*?"first":\["(\w+)"\]', out)
    err_first = _m.group(1) if _m else ""
    start_failed = err_first == "BattleStartAll"
    pre_failed = err_first == "BattleStartPre"
    formation_failed = (not start_failed and not pre_failed) and (
        ("BattleFormationTask" in out and "SubTaskError" in out) or "CONNECT_FAIL" in out)
    # **"运行器没起来"必须与上面两种分开**: 端口探测失败时 maa_copilot_run.py 只打印一行
    # `取不到模拟器 adb 端口（MuMuManager）` 就退，MAA 一秒都没跑（实测）。
    runner_dead = len(out.strip()) < 200 or "取不到模拟器 adb 端口" in out
    print(f"   [{time.strftime('%H:%M:%S')}] {job.name} 跑完 {time.time() - t0:.0f}s"
          f" 编队失败={formation_failed}"
          + (" 点不到开始=!!" if start_failed else "")
          + (" 运行器没起来=!!" if runner_dead else ""), flush=True)
    if runner_dead:
        print(f"   [!!] 运行器输出只有 {len(out.strip())} 字符：{out.strip()[:140]!r}", flush=True)
    return {"ok": not (formation_failed or runner_dead or start_failed or pre_failed),
            "dead": runner_dead, "start_failed": start_failed,
            "pre_failed": pre_failed, "out": out}


def take_emu_lock() -> None:
    """按模拟器实例上锁：**同一台只能有一个推图进程**（2026-09-17 惨痛教训）。

    为什么必须（用户实测症状："一直显示战斗中，但并没有在战斗"）:
    网页的「重打」按钮和命令行推图同时打在**同一个实例**上时，两个进程互相点掉对方的
    操作 ⇒ 谁都推进不了；而两边在开打前都已经写下 `status=战斗中`，没人擦，
    于是页面永远挂着"战斗中"。实例 2 上当时有三个进程在抢。
    锁文件里放 pid；pid 不在了（上次被杀/崩）就当过期，直接接管。
    """
    lk = HERE / f"emu_{EMU}.lock"
    if lk.is_file():
        try:
            old = int(lk.read_text(encoding="utf-8").split()[0])
        except Exception:
            old = 0
        if old and old != os.getpid() and _pid_alive(old):
            raise SystemExit(
                f"[闸] 模拟器实例 {EMU} 上已经有一个推图/校准进程在跑（pid={old}）。\n"
                f"      同一台模拟器只能有一个控制者，否则两个进程互相点掉操作、\n"
                f"      状态会一直挂着「战斗中」却打不动（这正是你看到的现象）。\n"
                f"      要么等它跑完，要么先停掉： Stop-Process -Id {old} -Force\n"
                f"      （如果确认它其实已经死了，删掉 {lk} 再重来）")
    lk.write_text(f"{os.getpid()} {time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")
    atexit.register(lambda: lk.unlink(missing_ok=True))


def _pid_alive(pid: int) -> bool:
    """这个 PID 还活着吗（tasklist 精确问，不猜）。"""
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=20)
        return f'"{pid}"' in (r.stdout or "")
    except Exception:
        return False


def heal_interrupted() -> None:
    """把上次被中断留下的「战斗中」翻掉（2026-09-17）。

    为什么会留下: `战斗中` 是**作业开跑前**写的（意思是"我进了这关"），进程被杀/崩了没人擦。
    现在有按实例的锁 ⇒ 拿得到锁就说明此刻没有别的进程在跑 ⇒ 任何「战斗中」都是上次的残留。

    **优先恢复 `prev_status`**（2026-09-17 补）: 网页「重打」一个已通关的关卡时，
    `战斗中` 会把那条**真三星**盖掉；进程一被杀，那条三星就永久没了（实测踩到：V 的 EX-4）。
    所以开打前先把成交档存进 `prev_status`，自愈时先恢复它，实在没有才判「未确认」。
    """
    st = load_state()
    n, back = 0, 0
    for _k, v in (st.get("stages") or {}).items():
        if (v or {}).get("status") != "战斗中":
            continue
        prev = v.pop("prev_status", None)
        if prev in ("三星", "二星", "通关"):
            v["status"] = prev
            v.pop("reason", None)
            back += 1
        else:
            v["status"] = "未确认"
            v["reason"] = "上次运行被中断（进程已不在）"
            n += 1
    if n or back:
        save_state(st)
        print(f"[state] 自愈残留的「战斗中」：恢复成交档 {back} 个"
              + (f"、判未确认 {n} 个" if n else ""), flush=True)


def _job_fit():
    """按需加载仓库里的 `tools/job_fit.py` —— 同一套「作业 × box」口径，不在这里另写一份。"""
    if "_JF" not in globals():
        import importlib.util
        spec = importlib.util.spec_from_file_location("job_fit", PROJ / "tools" / "job_fit.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules["job_fit"] = m
        spec.loader.exec_module(m)
        globals()["_JF"] = m
    return globals()["_JF"]


def preflight_jobs(stage: str, cands: list) -> list:
    """开跑前按 box 算一遍：**只留下能编满的作业**（2026-09-17 用户点名要的固定动作）。

    为什么要（今晚的账）: 失败主因是 `no oper found`（364 次 vs 16 次），而以前只能一份一份试
    ——试一次几分钟、可能白花理智。现在有 box（`data/operbox_<uid>_*.json`）就能提前算出来：
    `opers[]` 是硬需求（每个都得有），`groups[]` 是软需求（组里任一人有即可）。
    一份都编不满时**直说不跑**，而不是硬着头皮花 15 理智。
    """
    try:
        jf = _job_fit()
        box, _meta = jf.load_box(ACCOUNT)
    except Exception as e:                                   # noqa: BLE001
        print(f"   [preflight] 算不了（{type(e).__name__}: {e}）—— 照旧全部试", flush=True)
        return cands
    if not box:
        print(f"   [preflight] 这个号没有 box（先跑 tools/box_read_when_free.py --uid {ACCOUNT}）"
              f" —— 照旧全部试", flush=True)
        return cands
    ok, borrow, bad = [], [], []
    for j in cands:
        try:
            miss, dead, _title = jf.fit(str(j), box)
        except Exception:                                    # noqa: BLE001 —— 读不了的当"未知"，别拦
            ok.append(j)
            continue
        if dead or any(str(x).startswith("〈") for x in miss):
            # 整组一个都没有 ⇒ 借也补不上（一局只能带 1 个助战）；
            # 〈…〉是空壳作业的哨兵 ⇒ 同样不可用（它写进 missing 里是为了让上层看得见）
            bad.append((j, miss, dead))
        elif not miss:
            ok.append(j)
        elif len(miss) == 1:
            # **缺 1 个 ⇒ 走助战借核，照试**（2026-09-17 用户："不是可以借人吗？缺一个不是
            # 可以借嘛"）—— 实测 9036 缺圣聆初雪、借来一次通关 EX-7。缺 1 的排在没有缺的后头。
            borrow.append((j, miss, dead))
        else:
            bad.append((j, miss, dead))
    good = [x[0] if isinstance(x, tuple) else x for x in ok] \
        + [x[0] for x in borrow]
    print(f"   [preflight] box {len(box)} 人 ⇒ 能编满 {len(ok)} 份"
          + (f"，缺 1 人（走助战借核）{len(borrow)} 份：" + ", ".join(
              f"{j.name}(缺{m[0]})" for j, m, _d in borrow[:3]) if borrow else "")
          + (f"；编不满的：" + ", ".join(f"{j.name}(缺{','.join(m[:2])}{'/组空' if d else ''})"
                                         for j, m, d in bad[:3]) if bad else ""), flush=True)
    return good


def read_nodes_retry(tag: str = "nodes", tries: int = 3, gap: float = 2.5
                     ) -> tuple[dict, object]:
    """读地图节点，**读空就重试**（2026-09-17 实测：刚进区/刚转场时那一帧往往还没画出来）。

    症状：三个号同时出现"进了「殡仪堂」但 `可见：[]`、于是报导航失败"——而画面回看时
    地图明明已经打开了。根因是转场瞬间读得太早（三台机器同时被 MAA 占着时更明显）。
    返回 (节点表, 最后那帧)——调用方要拿帧做黄点判定时不用再抓一次。
    """
    img = None
    for i in range(tries):
        img = shot(tag)
        nodes = read_nodes(img) if img is not None else {}
        if nodes:
            return nodes, img
        if i < tries - 1:
            time.sleep(gap)
    return {}, img


def jobs_for(stage: str) -> list[pathlib.Path]:
    """返回该关的候选作业：**普通版优先于突袭版**，同档内再"挂件兼容/无核"这类灵活的优先。

    ⚠️ 为什么必须区分突袭（2026-09-17 实跑）: **没通关普通版时进突袭会被游戏强制当演习跑**
    （结算页原文「可使用演习不消耗理智模拟作战」），**演习不计通关、永远打不过**。
    9036 的 EX-4 池 4 份里 3 份是「仅突袭」⇒ 反复白跑，还被旧判定逻辑误记成三星。
    突袭版没通关时仍会排在后面（有些关只有突袭作业可选，不能直接丢掉）。
    """
    # 关卡名 → 作业文件名的前缀：SR-8 → act54side_08_*，EX-1 → act54side_ex01_*。
    # **必须分开**：以前不管 SR/EX 都按数字取前缀，于是 EX-1 会去匹配 SR-1 的作业
    # （点重试 EX-1 结果打了 SR-1，白花 21 理智）—— 2026-09-17 发现。
    kind, num = stage.split("-")
    prefix = f"act54side_ex{num.zfill(2)}_" if kind.upper() == "EX" else f"act54side_{num.zfill(2)}_"
    files = sorted(JOB_DIR.glob(prefix + "*.json"))

    def rank(p: pathlib.Path):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return (9, 9)
        title = ((d.get("doc") or {}).get("title") or "")
        raid = 1 if "突袭" in title else 0                       # 普通(0) 排突袭(1) 前面
        flex = 0 if ("挂件兼容" in title or "无核" in title) else 1
        return (raid, flex)
    return sorted(files, key=rank)


def job_title(p: pathlib.Path) -> str:
    """作业标题（`doc.title`）——「仅突袭 / 仅普通 / 挂件兼容」这些标记都在这里。"""
    try:
        return ((json.loads(p.read_text(encoding="utf-8")).get("doc") or {}).get("title") or "")
    except Exception:                                        # noqa: BLE001 —— 读不了当"没标记"
        return ""


def stage_of_job(p: pathlib.Path) -> str:
    """作业文件 → 关卡代号（act54side_ex07 → EX-7；act54side_07 → SR-7）。"""
    try:
        sn = str(json.loads(p.read_text(encoding="utf-8")).get("stage_name") or "")
    except Exception:                                    # noqa: BLE001
        return ""
    m = re.search(r"act54side_(ex)?(\d+)$", sn)
    if not m:
        return ""
    return ("EX-" if m.group(1) else "SR-") + str(int(m.group(2)))


FORCE_JOBS: dict[str, str] = {}          # 关卡 → 强制使用的作业文件名（`--jobs` 用）
RAID_MODE = False                        # `--raid`：只打「仅突袭」作业，结果写 raid_* 字段
PICK = 0                                 # `--pick N`：跳过候选列表里前 N 份（页面「换作业」按钮用）
HERE_MODE = False                        # `--here`：游戏已在目标关详情页 ⇒ 不导航、不按返回，直接打


def push_stage(stage: str) -> bool:
    print(f"\n=== {stage} ===", flush=True)
    # **直接打**（用户 2026-09-17："他为什么每次都要回到桌面，然后再去啊 这怎么慢成这个样子
    # 能不能做一个不导航直接打的"）: 人已经把游戏点到这一关的详情页 ⇒ **一次不点、一次不返回**，
    # 直接编队开打。导航里那些 `back(1)` 在大厅会弹出「退出游戏」确认框 —— 一旦点错就退回桌面，
    # 下次又得重新唤醒/进活动，越拖越慢。`--here` 模式干脆不碰返回键。
    here = bool(globals().get("HERE_MODE"))
    if not here:
        dismiss_popups()                                  # 先清掉挡路的弹窗（退出确认/奖励/公告）
    st = handle_ui(30) if not here else "here"            # 先把界面清到地图；直接打模式不等
    print(f"   界面：{st}", flush=True)
    if st == "timeout:map":
        st = "map"
    if st != "map":
        shot("pre_" + stage)
    if here:
        nodes, img = {stage: (0, 0)}, shot("here_" + stage)   # 不读节点、不导航，就地开打
    else:
        nodes, img = read_nodes_retry()
    if stage not in nodes:
        # 目标不在视野里 → **脚本自己导航**（不再需要人手动把游戏点到某张图）
        print(f"   没看到 {stage}（可见：{list(nodes)}）→ 自动导航", flush=True)
        if navigate_to(stage):
            img = shot("nodes")
            nodes = read_nodes(img) if img is not None else {}
    if stage not in nodes:
        # 找不到时先分清「没解锁」和「只是没在视野里」，别盲目滚（2026-09-17）:
        # 地图只画到「已通关的最后一关 + 1」⇒ 目标不在图上、而前一关没通关，就是没解锁。
        # 判据优先用**画面上的黄点**（游戏原话），其次才退回状态记录。
        prev = prev_stage(stage)
        # **"采样窗口不在画面里"（None）不算证据**：只有明确采到"没有黄点"才敢说未解锁。
        proven_locked = bool(prev and prev in nodes and img is not None
                             and has_yellow_dot(img, *nodes[prev]) is False)
        if prev and (proven_locked or not cleared_in_state(prev)):
            why = "地图上前一关没有黄点" if proven_locked else f"记录里 {prev} 未通关"
            # **只有 EX 区敢下"未解锁"的结论**（2026-09-17）: EX 区有黄点可证；SR 区的通关
            # 标记是青色六边形、判据未验证，而我们的记录本身可能出错（"1" 的 SR-3 就这样被
            # 记成失败、其实游戏里已通）⇒ SR 只说"没看到"，不写"未解锁"、免得把能打的关锁死。
            if stage.upper().startswith("EX-"):
                mark(stage, status="未解锁", reason=f"{prev} 未通关")
                print(f"   {stage} 未解锁：{why} ⇒ 直接跳过（不做无用的滚动搜索）", flush=True)
            else:
                mark(stage, status="失败", reason="不在图上（SR 区无法证明是否解锁）", attempts=0)
                print(f"   {stage} 没在图上（{why}）—— SR 区不下「未解锁」的结论，留给下次重试",
                      flush=True)
            return False
        mark(stage, status="失败", reason="导航失败", attempts=0)
        print(f"   自动导航也没找到 {stage}（可见：{list(nodes)}）", flush=True)
        return False
    if not here:
        tap_px(*nodes[stage], 4)        # read_nodes 的坐标 = 帧像素，不能再乘 SCALE
        img = shot("in_" + stage)
    # 详情页标题：SR-8 → "SR-8"，但 **EX-1 → "SR-EX-1"**（以前这里写死 SR-1，
    # 于是进到 EX-1 详情页也判"导航失败"退出来 —— 2026-09-17 实测）
    num = stage.split("-")[1]
    want = f"SR-EX-{num}" if stage.upper().startswith("EX-") else f"SR-{num}"
    # **地图上也印着节点名**（2026-09-17 实测翻车）: 点了节点但没进详情页、画面还是那张地图时，
    # 旧判据（只查 `want` 在不在文字里）照样通过 —— 因为地图上就写着 "SR-EX-7"。
    # 于是**把地图当详情页交给了 MAA**：`BattleStartPre` 连挂 3 次 → 编队对着地图瞎点 26 秒
    # → `BattleStartAll` 再挂 3 次，白烧 42 秒，最后还被记成"编队缺人"（9036 的 EX-7）。
    # 详情页的独有标志 = 「开始行动」/「代理指挥」按钮，地图上没有这两个词。
    ok_detail = False
    _ans = ""
    # **直接打模式优先用视觉判屏**（2026-09-17 用户："他为什么这个前期准备时间会这么长"）:
    # 视觉一次 1.6s 且**不用载 OCR 模型**；而首次 OCR 要 5.4s（三个 ONNX 会话读进内存）。
    # 画面不对时更能立刻停下（2 秒级），不再白跑一遍 OCR。
    if here:
        # **直接把画面交给 MAA，自己不做任何检测**（2026-09-17 用户："都不需要检测，maa 他作业，
        # 他有时候自己就有这个检测"）: MAA 的 copilot 自己会认屏 —— asst.log 原文
        # `MatchTemplate BattleStartPre@BattleQuickFormation`，即**它同时接受"关卡详情页"和
        # "快捷编队/行动准备页"**，认不出就自己报错。我们多加一道检查只会**挡住本来能打的情况**
        # （实测：人把屏停在编队页，我们判"不在详情页"直接停手 ✗）。
        ok_detail = True
        print("   [直接打] 不检测，直接把画面交给 MAA（它自己会认屏）", flush=True)
    for _try in range(2):
        if ok_detail or here:                        # 直接打模式上面已经判过了
            break
        if _try and not here:                        # 第一次没进去 → 再点一次（转场/点击丢失都常见）
            tap_px(*nodes[stage], 4)
            img = shot("in_" + stage)
        blob = " ".join(t for _x, _y, t in texts(img)) if img is not None else ""
        if want in blob and ("开始行动" in blob or "代理指挥" in blob):
            ok_detail = True
            break
        print(f"   第 {_try + 1} 次点 {stage} 没进详情页（{(blob[:60] or '没文字')!r}）"
              f"⇒ {'再点一次' if _try == 0 else '放弃'}", flush=True)
    if not ok_detail and not here:
        # 连点两次都没进详情页 → 让模型看一眼"该点哪里"（本机零 CPU；实测它认界面很准）
        if vision_tap(img, f"这是明日方舟模拟器截图。我要点开关卡「{stage}」的详情页"
                          f"（进去后能看到「开始行动」按钮）。请先一句话说明这一屏是什么；"
                          f"第二行只给应该点击位置的相对坐标（形如 x=0.16 y=0.35）。"):
            img = shot("in_" + stage)
            blob = " ".join(t for _x, _y, t in texts(img)) if img is not None else ""
            ok_detail = want in blob and ("开始行动" in blob or "代理指挥" in blob)
            if ok_detail:
                print("   [视觉] 点进详情页了 ✓", flush=True)
    if not ok_detail:
        if here:
            # 直接打模式：**一个键都不按**（按返回在大厅会弹退出确认，点错就退回桌面）
            print(f"   [直接打] 现在不在 {want} 的详情页 ⇒ 停手。请把游戏点到这一关再点一次"
                  f"（页面上那个「直接打」按钮）", flush=True)
            mark(stage, status="失败", reason=f"不在 {want} 详情页（直接打模式，未动游戏）", attempts=0)
            return False
        mark(stage, status="失败", reason="导航失败", attempts=0)
        back(1)
        return False
    t_start = time.time()
    # **开跑前先按 box 算一遍，只试能编满的**（2026-09-17 用户点名要的固定动作）
    cands = preflight_jobs(stage, jobs_for(stage))
    # **人工指定某一份作业**（`--jobs`；2026-09-17 用户："不行就换一份吧，这一个作业他都抄了好几次了"）：
    # 用来跳过 preflight 直接试"缺核心但可能借到助战"的作业 —— 能不能借到只有真跑才知道。
    _forced = FORCE_JOBS.get(stage)
    if _forced:
        cands = [JOB_DIR / _forced]
        print(f"   [指定] 只用 {_forced}（跳过 preflight）", flush=True)
    if globals().get("RAID_MODE"):
        # **突袭模式**：只挑「仅突袭」的作业。前提是**普通已通关** —— 否则游戏把突袭
        # 强制当演习跑（不扣理智、不计通关），白跑一趟。
        if not cleared_in_state(stage):
            print(f"   [突袭] {stage} 普通还没通关 ⇒ 突袭打不了（进去会被当演习跑）", flush=True)
            mark(stage, status="跳过", reason="突袭需先通关普通", attempts=0)
            return False
        _raid = [j for j in cands if "突袭" in job_title(j)]
        print(f"   [突袭] 只挑仅突袭作业：{len(_raid)}/{len(cands)} 份", flush=True)
        cands = _raid
    elif cands and not cleared_in_state(stage):
        # **仅突袭的作业，在普通没通关时等于白跑**（2026-09-17 实测：9036 的 EX-7 白烧 3.5 分钟）:
        # 没通关普通时进突袭，游戏会强制当**演习**跑（结算页原文「可使用演习不消耗理智模拟作战」），
        # 而演习不计通关 ⇒ 永远打不过。判据用状态记录里的**普通**通关档；只有确实没通关才丢，
        # 剩下的池子里若一份都没有，会走下面的「跳过」分支直说不跑（比硬跑一趟诚实）。
        keep = [j for j in cands if "突袭" not in job_title(j)]
        if len(keep) != len(cands):
            print(f"   [突袭] {stage} 普通未通关 ⇒ 丢掉 {len(cands) - len(keep)} 份仅突袭作业"
                  f"（会被当演习跑）", flush=True)
        cands = keep
    # **换一份作业**（`--pick N`，页面上那个「换作业」按钮就是它）: 环形轮换候选列表，
    # 所以连点几次都不会走到空列表 —— 2026-09-17 用户的用法（"换一份吧，这个作业都抄了好几次了"）。
    _pick = int(globals().get("PICK") or 0)
    if _pick and len(cands) > 1:
        _k = _pick % len(cands)
        cands = cands[_k:] + cands[:_k]
        print(f"   [换作业] 轮换 {_k} 位 ⇒ 从 {cands[0].name} 开始（共 {len(cands)} 份）", flush=True)
    if not cands:
        mark(stage, status="跳过", reason="池子里没有能编满的作业（box 判定）")
        print(f"   {stage} 跳过：这个池子一份都编不满 —— 直说不跑，不白花理智和时间", flush=True)
        return False
    for k, job in enumerate(cands, 1):
        print(f"   试作业 {job.name}", flush=True)
        # **开打前先记住原来的成交档**（2026-09-17）: 网页「重打」一个已通关的关卡时，
        # 这条「战斗中」会把真三星盖掉；进程若被中断，自愈才有东西可恢复（见 heal_interrupted）。
        _cur = ((load_state().get("stages") or {}).get(stage) or {}).get("status")
        _keep = _cur if _cur in ("三星", "二星", "通关") else None
        mark(stage, status="战斗中", job_used=job.name, attempts=k,
             prev_status=_keep,
             started=time.strftime("%Y-%m-%d %H:%M:%S"))
        r = run_job(job, f"{stage}_{job.stem}")
        if r.get("dead"):
            # 运行器自身没起来 = 环境问题，换下一份作业也是一样的结果 ⇒ 直接停，别刷日志
            mark(stage, status="未确认", reason="MAA 运行器没起来（见日志）",
                 job_used=job.name, attempts=k, time_spent=int(time.time() - t_start))
            print(f"   {stage} 记「未确认」并停手：运行器没起来，换作业也白搭", flush=True)
            break
        if not r["ok"]:
            mark(stage, status="失败",
                 reason=("点不到开始行动" if r.get("start_failed")
                         else "MAA 没认出详情页（BattleStartPre）" if r.get("pre_failed")
                         else "编队缺人"),
                 job_used=job.name, attempts=k,
                 time_spent=int(time.time() - t_start))
            # 退几级看**上一个界面在哪**: 编队页(编队失败)→退 2 级回详情页；
            # 点不到开始行动时人还在详情页 → 只退 1 级，退 2 会退到地图上、下一份作业会对着错屏操作
            back(1) if r.get("start_failed") else back(2)
            continue
        res = str(handle_ui(120))                          # 打完清剧情/结算
        print(f"   战后界面：{res}", flush=True)
        # **这一次 MAA 到底有没有真的动过手**（2026-09-17 用户："他这个作业是对的吗？…他怎么
        # 什么操作都没有"）: 池子里有 6 份"只有标题+视频链接"的空壳作业（0 动作），MAA 拿到
        # 零动作作业会**原地空转**；而屏幕上若残着上一场的结算页，判定就会把**别人的战报**
        # 记到这一关上（实测受害：V 的 EX-4 突袭被记"失败"、「1」的 SR-8 被记"三星"）。
        # 判据：MAA 日志里连一个 `Deploy` 都没有 ⇒ 这一局啥也没干 ⇒ 一律记「未确认」。
        if '"action":"Deploy"' not in str(r.get("out") or "") and res.startswith("map"):
            mark(stage, status="未确认", reason="MAA 这一局没有任何部署动作（空壳作业/原地空转）",
                 job_used=job.name, attempts=k, time_spent=int(time.time() - t_start))
            print(f"   {stage} 这次一次部署都没有 ⇒ 记「未确认」（结算页可能是残留的，不能算数）",
                  flush=True)
            continue
        # handle_ui 的返回值只有这几种：map:won / map:lost / map:drill / map / detail / timeout:...
        # **只有"确实赢了"才算通关**（2026-09-17 修，最高优先级 bug）:
        #   以前 `res == "map"`（**根本没看到结算页**）也落到 mark(三星) ⇒ V 的 EX-3 那轮跑了
        #   35 秒、日志零个结算行、战斗从未开始（MAA 日志停在一个 Swipe），照样记三星，
        #   于是整条链跳过 EX-3 去追**根本不存在**的 EX-4。
        #   这种"什么结论都没看到"的局：先用**黄点**问游戏（真通关了别冤枉它），问不出结果就记
        #   「未确认」—— 不宣称通关、也不算"战斗失败"。
        if res == "map:won":
            # **判赢也要用黄点复核**（2026-09-17 实测翻车）: 结算页 OCR 糊掉时（原文
            # `Hinl Jbrg and tr Phrdes Isl`）会把演习/失败的信号读丢，于是败仗被判成 `won`
            # —— V 的 EX-4 就这样"通过"了，而地图上根本没有黄点。
            # 只有**明确看到没有黄点**才推翻（返回 None = 那关不在画面上，维持原判）。
            if stage_cleared_now(stage) is False:
                mark(stage, status="未确认", reason="结算页判赢但地图上无黄点",
                     job_used=job.name, attempts=k, time_spent=int(time.time() - t_start))
                print(f"   {stage} 结算页看着像赢，但地图上没有黄点 ⇒ 记「未确认」，不宣布通关",
                      flush=True)
                continue
            mark(stage, status="三星", job_used=job.name, attempts=k,
                 time_spent=int(time.time() - t_start),
                 timestamp=time.strftime("%Y-%m-%d %H:%M:%S"))
            unlock_next(stage)
            return True
        if res in ("map:lost", "map:drill"):
            mark(stage, status="失败",
                 reason="演习（没花理智、未记录通关）" if res == "map:drill" else "战斗失败",
                 job_used=job.name, attempts=k,
                 time_spent=int(time.time() - t_start))
            back(2)
            continue
        if res.startswith("map") or res == "detail":       # 包括裸 "map" = 没看到结算页
            if stage_cleared_now(stage):
                print(f"   {stage} 没看到结算页，但地图上有黄点 ⇒ 确实通关了", flush=True)
                mark(stage, status="三星", job_used=job.name, attempts=k,
                     time_spent=int(time.time() - t_start),
                     timestamp=time.strftime("%Y-%m-%d %H:%M:%S"))
                unlock_next(stage)
                return True
            mark(stage, status="未确认", reason="没看到结算页",
                 job_used=job.name, attempts=k, time_spent=int(time.time() - t_start))
            print(f"   {stage} 没看到结算页、地图上也没有黄点 ⇒ 记「未确认」，不宣称通关", flush=True)
            continue                                       # 没通关，所以换下一份作业不算重复花理智
        mark(stage, status="失败",
             reason="超时" if res.startswith("timeout") else "战斗失败",
             job_used=job.name, attempts=k,
             time_spent=int(time.time() - t_start))
        back(2)
    # 所有作业都试完：**保留最后一次的真实原因**，不要把 attempts 归零、也不要写一个笼统的
    # "战斗失败"把"超时"盖掉（2026-09-17 实测：超时的原因就是这样被覆盖的）。
    # 但如果停在「未确认」，就保持未确认 —— "没看到结算页"不是"战斗失败"，别把它降级。
    cur = (load_state().get("stages") or {}).get(stage) or {}
    keep = "未确认" if cur.get("status") == "未确认" else "失败"
    mark(stage, status=keep, job_used=cur.get("job_used") or "",
         attempts=cur.get("attempts") or 0,
         time_spent=cur.get("time_spent") or int(time.time() - t_start))
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="SR-7,SR-8")
    ap.add_argument("--jobs", default="",
                    help="强制指定作业文件名（逗号分隔）；关卡由作业的 stage_name 推出，跳过 preflight")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--raid", action="store_true",
                    help="突袭模式：只打「仅突袭」作业，结果记在 raid_* 字段（不碰普通记录）")
    ap.add_argument("--pick", type=int, default=0,
                    help="跳过候选列表前 N 份作业（页面「换作业」按钮用；环形轮换）")
    ap.add_argument("--here", action="store_true",
                    help="直接打：游戏已经在目标关详情页 ⇒ 不导航、不按返回键，就地编队开打")
    ap.add_argument("--formation-index", type=int, default=0,
                    help="透传给 MAA 的 formation_index（实验用：MAA 会拿游戏里的「快捷编队」"
                         "判定『和上次一样』就跳过编队，换索引看能否强制重编）")
    ap.add_argument("--emu", default="2", help="MuMu 实例号（端口 = 16384 + 32*N）")
    ap.add_argument("--account", default="", help="账号键：状态存成 push_state_<账号>.json")
    ap.add_argument("--pool", default="", help="作业池目录名（data/copilot/<pool>，默认 event54_c）")
    ap.add_argument("--allow-shared", action="store_true",
                    help="允许不传 --account 而退回共享 push_state.json（默认拦停）")
    a = ap.parse_args()
    apply_target(a.emu, a.account, a.pool, a.allow_shared)
    globals()["RAID_MODE"] = bool(a.raid)
    globals()["PICK"] = max(0, int(a.pick or 0))
    globals()["HERE_MODE"] = bool(a.here)
    globals()["FORMATION_INDEX"] = int(a.formation_index or 0)
    if a.here:
        print("[直接打] 不导航、不按返回键：只认「现在是不是停在那一关的详情页」", flush=True)
    if a.raid:
        print("[突袭模式] 只打「仅突袭」作业；结果写 raid_* 字段（不动普通记录）", flush=True)
    if a.jobs:                                   # 指定作业 ⇒ 关卡顺序由作业自己决定
        _stages = []
        for _n in [x.strip() for x in a.jobs.split(",") if x.strip()]:
            _st = stage_of_job(JOB_DIR / _n)
            if not _st:
                raise SystemExit(f"[闸] 认不出 {_n} 的关卡（stage_name 不是 act54side_(ex)NN）")
            FORCE_JOBS[_st] = _n
            if _st not in _stages:
                _stages.append(_st)
        a.stages = ",".join(_stages)
        print(f"[指定作业] {FORCE_JOBS} ⇒ 关卡 {a.stages}", flush=True)
    globals()["SCALE"] = detect_scale()          # 1080p 的实例要 ×1.5，否则点歪
    if a.dry:
        for s in a.stages.split(","):
            print(s, "→", [p.name for p in jobs_for(s)])
        return
    print(f"[target] 模拟器实例 {EMU} @ {SERIAL} ｜ 作业池 {JOB_DIR.name}"
          f" ｜ 状态 {STATE_PATH.name} ｜ 坐标缩放 ×{SCALE}", flush=True)
    take_emu_lock()                                        # 同一台只允许一个控制者
    heal_interrupted()                                     # 擦掉上次被中断留下的「战斗中」
    st0 = load_state()                                     # 启动时初始化：已有状态不重置
    for s in a.stages.split(","):
        st0["stages"].setdefault(s.strip(), {"status": "未打"})
    save_state(st0)
    print(f"[state] {STATE_PATH.name} 就绪：{len(st0['stages'])} 关", flush=True)
    done = []
    try:
        for s in a.stages.split(","):
            ok = push_stage(s.strip())
            done.append((s, ok))
            print(f"   {s}: {'✅ 通关' if ok else '❌ 未完成'}", flush=True)
    finally:
        # **交棒前统一收尾**（2026-09-17 用户点名要的）: 不管成没成，都把游戏放回大厅。
        # 否则下一个动作（推图 / 校准 / 读 box）会从"没人管的界面"起步、只见一片空白。
        # **直接打模式例外**（2026-09-17 用户："他为什么这个前期准备时间会这么长"）:
        # 那一屏是人自己摆的，归位要跑好几轮点击（实测 20 秒）—— 不动它，也省掉这 20 秒。
        if globals().get("HERE_MODE"):
            print("[直接打] 不归位（这一屏是你摆的，不动它）", flush=True)
        else:
            cleanup_to_lobby()
    print("\n总结:", done, flush=True)


if __name__ == "__main__":
    main()
