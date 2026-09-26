#!/usr/bin/env python3
"""演习陪练 — 一键把角色送进"零理智成本的练习战斗"，供战斗模块反复迭代。

这是把 agent 已经走通的路线**固化**下来的产物（P2 的"导航固化"在实战里的用法）：
    主界面 → 终端卡片 → 主题曲「离解复合」 → 开启旅途 → 15-11 详情页
    → 演习 → 编队页 → 蓝色开始按钮 → 跳过战前剧情 → 战斗中

为什么固定坐标可以用：这些是**固定的 UI 布局**（1280x720），而且每一步都有
状态校验兜底 —— 走岔了就报错停下，不会瞎点（教训：手工锚点必须验证）。

用法:
    python tools/ai_agent_drill.py --emu 1                 # 一路进到战斗并截图
    python tools/ai_agent_drill.py --emu 1 --stage-name 一段长梦将醒
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from services.ai_agent.actuator import Actuator, Session  # noqa: E402
from services.ai_agent.percept import Perception, log  # noqa: E402

OUT = PROJ / "logs" / "agent"


def wait_state(p: Perception, want, timeout: float = 25.0,
               interval: float = 1.5) -> bool:
    """等状态里的关键字（want 可以是 str 或关键字列表）。

    状态名有重叠（终端页有时被标成"关卡列表页"），所以调用方也可以直接用
    `wait_text()` 按画面文字判定 —— 那比状态词稳。
    """
    wants = [want] if isinstance(want, str) else list(want)
    dl = time.time() + timeout
    last = ""
    while time.time() < dl:
        st = p.state(p.shot())["verdict"]
        last = st
        if any(w in st for w in wants):
            log(f"  状态 → {st}")
            return True
        time.sleep(interval)
    log(f"  等待 {wants} 超时，当前 {last}")
    return False


def wait_text(p: Perception, needles, timeout: float = 25.0,
              interval: float = 1.5) -> bool:
    """等画面里出现某些文字（内容判定，绕开状态词的重叠）。"""
    needles = [needles] if isinstance(needles, str) else list(needles)
    dl = time.time() + timeout
    seen = ""
    while time.time() < dl:
        img = p.shot()
        texts = [h.label for h in p.texts(img)]
        seen = " ".join(texts)[:60]
        if any(n in t for n in needles for t in texts):
            log(f"  命中文字 {needles}")
            return True
        time.sleep(interval)
    log(f"  等待文字 {needles} 超时，当前看到: {seen}")
    return False


def step(act: Actuator, p: Perception, point, want: str, note: str,
         delay: float = 2.0, timeout: float = 25.0) -> bool:
    log(f"→ {note} @{point}")
    act.tap(*point)
    time.sleep(delay)
    return wait_state(p, want, timeout=timeout)


def skip_plot(act: Actuator, p: Perception, rounds: int = 20) -> bool:
    """跳过战前剧情直到进战斗（用 MAA 任务模板定位「跳过」，别用 OCR）。"""
    for i in range(rounds):
        img = p.shot()
        st = p.state(img)["verdict"]
        if "战斗" in st:
            log(f"  已进入战斗（{i} 次跳过/推进）")
            return True
        conf = p.find_task_text("SkipThePreBattlePlotConfirm", img=img)
        if conf:
            act.tap(*conf.center)
            time.sleep(1.0)
            continue
        skip = p.find_task_text("SkipThePreBattlePlot", img=img)
        if skip:
            act.tap(*skip.center)
        else:
            act.tap(640, 360)      # 剧情页：点屏幕推进
        time.sleep(1.2)
    return "战斗" in p.state(p.shot())["verdict"]


def main() -> None:
    ap = argparse.ArgumentParser(description="进演习战斗陪练场")
    ap.add_argument("--emu", default="1")
    ap.add_argument("--instance", default="1")
    ap.add_argument("--client", default="Official")
    ap.add_argument("--skip-nav", action="store_true", help="已在详情页，直接从演习开始")
    args = ap.parse_args()

    p = Perception.for_emu(args.emu, instance=args.instance, client=args.client)
    act = Actuator(p, session=Session("drill"), post_delay=1.2)
    OUT.mkdir(parents=True, exist_ok=True)

    st = p.state(p.shot())["verdict"]
    log(f"当前状态: {st}")

    if not args.skip_nav:
        if "主界面" in st:
            log("→ 点主界面终端卡片 @(970, 161)")
            act.tap(970, 161)
            time.sleep(2.5)
        # 终端页：用内容判定（能看到 MAIN THEME / 主题曲卡）
        if not wait_text(p, ["MAIN THEME", "离解复合", "怒号光明", "进度"], timeout=20):
            log("没看到主题曲卡片，退出")
            return
        log("→ 点「离解复合」主题卡 @(720, 185)")
        act.tap(720, 185)
        time.sleep(2.5)
        act.tap(640, 485)                     # 弹窗「开启旅途」
        time.sleep(3.0)
        if not wait_text(p, ["15-11", "代理指挥", "推荐平均等级"], timeout=20):
            log("没进关卡详情页，退出")
            return
        # 演习（详情页右下角，开始行动左侧）
        log("→ 点演习 @(997, 667)")
        act.tap(997, 667)
        time.sleep(3.0)
        if not wait_text(p, ["快捷编队", "助战干员", "开始行动"], timeout=20)                 and not wait_state(p, "编队页", timeout=5):
            log("没进编队页，退出")
            return
        # 编队页蓝色开始按钮 = 演习模式（普通模式是橙色）
        if not step(act, p, (1100, 490), "剧情", "点蓝色开始（演习）", delay=6.0, timeout=40):
            # 有时直接进战斗（无剧情）
            if "战斗" not in p.state(p.shot())["verdict"]:
                return
    if not skip_plot(act, p):
        log("未能进入战斗，退出")
        return

    img = p.shot()
    import cv2
    path = OUT / f"drill_battle_{int(time.time())}.png"
    cv2.imwrite(str(path), img)
    log(f"战斗截图: {path}")
    log(f"最后状态: {p.state(img, use_ocr=True)['verdict']}")


if __name__ == "__main__":
    main()
