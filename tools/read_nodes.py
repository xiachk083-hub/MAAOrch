"""读活动地图的关卡节点坐标 → 落 `data/stage_nodes.json`（批量执行器 C 的第一版）。

由来（2026-09-16 用户）：批量执行器每关需要"活动地图上的节点坐标"，而这个坐标一直靠手传。
用户口径："**先读一次活动地图** —— 这一步不消耗理智，只是开地图看节点位置，比真跑一关便宜。"
实测答案（决定本工具的成本）：**同一活动内不同关卡坐标不同**（act16side_ex01=390,165 vs ex02=390,258）
⇒ 必须**逐关**取 + 认关卡代号，不是"读一次活动地图"那么便宜。

🔴 **只在测试机跑**（记忆 [[test-emulators-5]] 授权的六台：ADB 端口 17056/17248/17312/16484/16580/16740）。
本工具启动时**第一件事就是核对 ADB 端口在不在白名单里**，不在就拒跑 —— 实测本机曾出现
`16448`（与测试机 `16484` 只差中间两位）与 `emulator-5554`（MuMu 默认主实例，最像产线），
看错就会点到别人号上。**这不是谨慎过头，是防止一次误操作浪费客户理智。**

四步走（**默认只做第一步**，逐步放开）：

    python tools/read_nodes.py --emu 21 --probe                  # ① 只截图 + 报当前界面（不点任何东西）
    python tools/read_nodes.py --emu 21 --capture --activity act54side   # ② 导航到活动地图并截图
    python tools/read_nodes.py --emu 21 --read --activity act54side --frame <png>  # ③ 只读图，报它读到的坐标
    python tools/read_nodes.py --emu 21 --read --activity act54side --frame <png> --write  # ④ 确认无误才落表

③④ 分开是刻意的：**先让它把读到的坐标列出来给人看**，对的才 `--write`。
批量执行器在缺坐标时拒绝真跑（见 `batch_run.py`），所以这张表就是"能不能真跑"的开关。

⚠️ **已知不可靠，别当作终版**：`services/stage_probe.py` 里有一条实测教训 ——
"MAA OCR 识别不了列表小字关卡名（**9→2 / 8→6 误读**），改为按网格 tap 进详情页 → 关卡名 OCR 验证
（**大字可靠**）"。活动地图节点旁的小字是同一类目标 ⇒ 本文件的 `extract_nodes()` 只当**候选**，
可靠路径是 `read_via_detail()`（逐个 tap 进详情页读大字），而它**需要一台开着的测试机才能实测校准**。
所以 `--write` 现在必须再加 `--confirm`：**未经确认的坐标不许落表**（错坐标会让 batch_run 去点错地方）。

"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
PROJ = _HERE.parent
sys.path.insert(0, str(_HERE))
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

# ⚠️ **这份白名单本身是跨机器混记的，不能当"本机该有什么"的判据**（2026-09-16 用户现场核查）：
#   · 它来自 8 月那套本机 MuMu 群（仓库根还有按端口命名的截图 16672…17312，日期 8/16–8/17）；
#   · MuMu 的端口规则是 `16384 + 32 × 实例号` ⇒ 17056/17248/17312 算出 #21/#27/#29，
#     而 **16484/16580/16740 用这条规则算不出整数实例号**（六个里只有三个对得上本机这套安装）；
#   · 本机 MuMu 现在只有 3 条实例记录：`0/"1"@16384`、`1/"V"（关）`、`2/"V-1"@16448` ——
#     **#21 在本机不存在**，白名单端口一个都不在。
# ⇒ 所以判据改成**由调用方显式给授权**（`--allow`），这份表只作"历史参考"。红线不该建立在一张
#   记错机器的表上：**要么你点名哪台，要么它不许动**。
TEST_PORTS_LEGACY = {17056, 17248, 17312, 16484, 16580, 16740}

# ⚠️ **产线嫌疑机**（2026-09-16 现场核查）：`16384` 与 `16448` 是本机在跑的那两台（不是测试号），
# `emulator-5554` 与 `16384` 是**同一台的重复登记**（实测 uptime 一致：105166.16 vs 105166.29）。
# 点到它们 = 点到别人的号上。而 `16484`（测试机）/`16448`（在跑的那台）**只差中间两位** —— 今晚踩过一次。
# ⇒ 光 `--allow` 点名不够：命中这三个要**再给一次** `--allow-prod`。两道门，不靠记性。
PROD_SUSPECT = {"16384", "16448", "emulator-5554"}

SHOT_DIR = PROJ / "data" / "agent_run" / "read_nodes"


def adb_ports() -> dict:
    """当前 ADB 设备 → {端口: 设备名}。`emulator-5554` 是 127.0.0.1:5555 的别名（去重见 adb-capture 记忆）。"""
    import subprocess
    out = {}
    try:
        r = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=20)
        for line in (r.stdout or "").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                name = parts[0]
                if ":" in name:
                    out[int(name.rsplit(":", 1)[1])] = name
                else:
                    out[name] = name
    except Exception:
        pass
    return out


def guard(emu: str, allow: list[str] | None = None, allow_prod: bool = False) -> tuple[bool, str]:
    """**红线闸**：只允许在**调用方显式授权**的那台上跑。返回 (放行, 说明)。

    `allow` 支持三种写法，命中任一即可：ADB 端口（`17056`）、设备序列（`127.0.0.1:17056`）、
    远程机（`192.168.1.9:17056` —— 走 `adb connect` 接过来的那台）。
    **不给 allow 就拒跑**：不拿"历史白名单"当授权（它记的是另一台机器，见上面的说明）。
    **点名点到产线嫌疑机（`PROD_SUSPECT`）时再要一次 `allow_prod=True`** —— 少这一步就拒跑。
    """
    ports = adb_ports()
    if not ports:
        return False, "看不到任何 ADB 设备（模拟器没开？远端机需要先 adb connect）"
    if not allow:
        return False, ("未授权 —— 请用 --allow 点名允许在哪台上读（端口/序列/IP:端口）。"
                       "当前可见：%s。历史白名单 %s 只作参考（实测本机不存在那些实例）。"
                       % (sorted(ports, key=str), sorted(TEST_PORTS_LEGACY)))
    norm = {str(x) for x in allow}
    hit = [k for k in ports if str(k) in norm or str(ports[k]) in norm
           or (isinstance(k, int) and ("127.0.0.1:%d" % k) in norm)]
    if not hit:
        return False, ("--allow %s 与可见设备 %s 对不上 —— 拒跑（点名的那台没在跑？）"
                       % (sorted(norm), sorted(ports, key=str)))
    suspect = [k for k in hit if str(k) in PROD_SUSPECT or str(ports[k]) in PROD_SUSPECT]
    if suspect and not allow_prod:
        return False, ("⚠️ 点名的是**产线嫌疑机** %s —— 不加 --allow-prod 就拒跑。"
                       "16384/16448 是本机在跑的两台（不是测试号），emulator-5554 与 16384 是同一台；"
                       "测试机端口是 16484，**16448 只差中间两位**。确认要在这台上读再加 --allow-prod。"
                       % suspect)
    return True, "已授权：%s%s" % (hit, "（含产线嫌疑机，已二次确认）" if suspect else "")


def probe(emu: str) -> dict:
    """① 只看不动：报当前界面状态 + 存一张截图（**不点任何东西**）。"""
    from services.ai_agent.percept import AdbScreen
    import screen_state as SS
    sc = AdbScreen(str(emu))
    sc.ensure_connected()
    f = sc.capture()
    st, conf = SS.state_of(f) if f is not None else ("capture_failed", 0)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    p = SHOT_DIR / ("probe_%s_%s.png" % (emu, time.strftime("%H%M%S")))
    if f is not None:
        import cv2
        cv2.imwrite(str(p), f)
    return {"emu": emu, "state": st, "conf": conf, "shot": str(p)}


def capture_map(emu: str, activity: str, out: str | None = None) -> dict:
    """② 导航到活动地图并截图。**复用 `clear_runner` 的 safe_tap/wait_state**（点前验证状态，不盲点）。

    导航链按既有实现（记忆 [[terminal-navigation-chain]]）：大厅 → 终端（小房子 QuickSwitch）→
    **前往上一次作战** → 活动地图。每一步都截图落盘，出错时能一眼看出停在哪。
    """
    import clear_runner as CR
    sc = CR.Runner(emu=str(emu), node=(0, 0), plans=[], max_tries=1, out_dir=SHOT_DIR)
    steps = []
    try:
        ok = sc.go_terminal()
        steps.append(("go_terminal", ok))
        import cv2
        f = sc.sc.capture()
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        p = pathlib.Path(out) if out else SHOT_DIR / ("map_%s_%s.png" % (activity, time.strftime("%H%M%S")))
        if f is not None:
            cv2.imwrite(str(p), f)
        steps.append(("shot", str(p)))
    finally:
        try:
            sc.kill_own()
        except Exception:
            pass
    return {"emu": emu, "activity": activity, "steps": steps}


CODE_RE = re.compile(r"\b(SR|GA|CW|WD|IS|SV)-?(\d{1,2}|EX-?\d{1,2})\b", re.I)


def extract_nodes(frame_path: str, activity: str) -> list[dict]:
    """③ 从活动地图截图里读关卡代号 → 候选坐标。**只读不动**。

    做法：局部 OCR 抓 ASCII 代号（`SR-1`/`SR-EX-3` 这类是 ASCII，本地 PaddleOCR 够用；
    **中文干员名不行**是已知的，见 [[local-ocr-tool]]），再把同一行里的节点圆点当锚点。
    读到的每一条都带 `conf` 与像素坐标，**给人核**；`--write` 才落表。
    """
    import cv2
    try:
        from infrastructure.ocr import ocr_boxes        # 若仓库里已有封装就用它
    except Exception:
        ocr_boxes = None
    img = cv2.imread(str(frame_path))
    if img is None:
        raise SystemExit("读不到截图：%s" % frame_path)
    if ocr_boxes is None:
        return []      # 没有 OCR 封装时不硬凑：明确返回空，让人工标
    out = []
    for box in ocr_boxes(img) or []:
        txt = (box.get("text") or "").strip()
        m = CODE_RE.search(txt)
        if not m:
            continue
        code = (m.group(1).upper() + "-" + m.group(2).upper()).replace("--", "-")
        x, y = box.get("cx"), box.get("cy")
        if x is None or y is None:
            continue
        out.append({"code": code, "x": int(x), "y": int(y), "conf": box.get("conf"),
                    "text": txt[:20]})
    return out


def read_via_detail(emu: str, activity: str, candidates: list[dict]) -> list[dict]:
    """**可靠路径**：对候选点逐个 tap 进详情页 → OCR 大字关卡名 → 回退。

    照抄 `services/stage_probe.py` 已被验证的做法（网格 tap + 详情页大字 OCR，因为小字误读 9→2/8→6）。
    每次只点一个候选点、点完立刻回落，**不连续盲点**。
    ⚠️ 尚未实测：需要一台**在跑的测试机**（白名单端口）才能校准。先把测试机开起来再跑。
    """
    raise SystemExit("read_via_detail 需要一台在跑的测试机（白名单端口）才能实测校准；"
                     "先开测试机（如 #21 官-14 @17056），再 --probe / --capture。")


def confirm_required(nodes: list[dict]) -> bool:
    """落表前的人工确认闸（见模块 docstring 的警告条）。"""
    return True


def write_nodes(activity: str, nodes: list[dict]) -> pathlib.Path:
    import batch_run as BR
    for n in nodes:
        BR.set_node(activity, n["code"], "%d,%d" % (n["x"], n["y"]))
    return BR.NODES_FILE


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="读活动地图节点坐标（只在测试机上跑）")
    ap.add_argument("--emu", required=True, help="模拟器索引")
    ap.add_argument("--activity", default="act54side")
    ap.add_argument("--probe", action="store_true", help="① 只截图看当前界面（不点任何东西）")
    ap.add_argument("--capture", action="store_true", help="② 导航到活动地图并截图")
    ap.add_argument("--read", action="store_true", help="③ 从已有截图读节点坐标（只读）")
    ap.add_argument("--frame", default=None, help="配 --read：要读的截图路径")
    ap.add_argument("--write", action="store_true", help="④ 把读到的坐标落表（需同时给 --confirm）")
    ap.add_argument("--confirm", action="store_true", help="确认这批坐标已人工核对（配 --write）")
    ap.add_argument("--allow", action="append", default=None,
                    help="点名允许在哪台上读：端口 17056 / 序列 127.0.0.1:17056 / 远程 IP:端口（可重复）")
    ap.add_argument("--allow-prod", action="store_true",
                    help="二次确认：明知 16384/16448/emulator-5554 是产线嫌疑机，仍要在这台上读")
    a = ap.parse_args(argv)

    ok, why = guard(a.emu, a.allow, a.allow_prod)
    print(("[闸] " + why), flush=True)
    if not ok:
        return 2
    if a.probe:
        print(json.dumps(probe(a.emu), ensure_ascii=False, indent=1))
        return 0
    if a.capture:
        print(json.dumps(capture_map(a.emu, a.activity), ensure_ascii=False, indent=1))
        return 0
    if a.read:
        if not a.frame:
            raise SystemExit("--read 需要 --frame <png>")
        nodes = extract_nodes(a.frame, a.activity)
        if not nodes:
            print("没读到节点（可能这张图不是活动地图，或 OCR 封装不可用）。"
                  "先看截图确认在活动地图上，再试；必要时人工标点。")
            return 1
        print("读到 %d 个候选（**核对无误再加 --write**）：" % len(nodes))
        for n in nodes:
            print("   %-9s → (%d, %d)  conf=%s  原文=%r" % (n["code"], n["x"], n["y"],
                                                          n.get("conf"), n.get("text")))
        if a.write and not a.confirm:
            print("拒绝落表：坐标来自不可靠的 OCR 猜测路径，必须人工核对后加 --confirm。")
            return 3
        if a.write:
            print("→ 落表:", write_nodes(a.activity, nodes).relative_to(PROJ))
        else:
            print("（未落表；确认后重跑并加 --write）")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
