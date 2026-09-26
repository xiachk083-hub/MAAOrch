#!/usr/bin/env python3
"""执行层自检 — 离线跑通"定位 → 点击 → 验证 → 留证 → 可重放"闭环。

不动模拟器（用离线截图源，adb 输入自动降级为空操作），验的是逻辑:
  · 目标解析（文字/模板/坐标）能给出绝对坐标
  · do() 能定位、记录命中坐标与分数、跑验证、写轨迹
  · wait_until 能轮询到条件成立（"等元素出现"——仓库里原本没有这个能力）
  · 轨迹落盘后能被 load_trace 读回并重放（P2 导航固化的基础）

用法: python tools/ai_agent_actuator_selftest.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from services.ai_agent.percept import Perception, ResourcePaths, TemplateMatcher, imread  # noqa: E402
from services.ai_agent.actuator import Actuator, Session  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    RESULTS.append((ok, label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")


def pick(images: list[pathlib.Path], predicate) -> pathlib.Path | None:
    for p in images:
        img = imread(p)
        if img is None:
            continue
        if predicate(img):
            return p
    return None


def main() -> None:
    tmpl = TemplateMatcher(ResourcePaths(1))
    shots = sorted((PROJ / "logs" / "rl_long").glob("*.png")) \
        + sorted((PROJ / "logs" / "probe_shots").glob("*.png"))
    battle = pick(shots, lambda im: tmpl.find(im, "BattleOfficiallyBegin", threshold=0.7))
    print(f"战斗截图: {battle}")
    check(battle is not None, "找到一张真实战斗截图（HUD 标记命中）")
    if battle is None:
        return

    with tempfile.TemporaryDirectory() as tmp:
        sess = Session("selftest", root=pathlib.Path(tmp))
        p = Perception.offline(battle, instance=1)
        act = Actuator(p, session=sess, post_delay=0.0)

        # ① 目标解析: 模板 → 绝对坐标
        hud = p.locate({"template": "BattleOfficiallyBegin"})
        check(hud is not None and hud.center[0] > 1000, "模板目标解析出绝对坐标",
              f"{hud.center if hud else None}")

        # ② do(): 定位 + （离线不点）+ 验证 + 留证
        res = act.do({"template": "BattleOfficiallyBegin"},
                     verify={"state": "战斗中"}, retries=0)
        check(res.ok, "do() 定位+验证通过", res.detail)
        check(res.point is not None and res.hit_score is not None,
              "步骤记录含命中坐标与分数", f"@{res.point} {res.hit_score}")
        check(bool(res.state_before) or bool(res.state_after), "步骤记录含画面状态",
              f"{res.state_before} → {res.state_after}")
        check(pathlib.Path(sess.dir / "steps.jsonl").exists(), "轨迹 steps.jsonl 落盘")
        check(len(res.shots) >= 1, "前后截图留证", f"{len(res.shots)} 张")

        # ③ wait_until: 等元素出现（本来就在 → 立刻成立）
        v = act.wait_until(lambda pp: pp.find("BattleOfficiallyBegin") is not None,
                           timeout=3, interval=0.2, desc="等待战斗标记")
        check(v.ok, "wait_until 轮询到条件成立", v.detail)
        v2 = act.wait_until(lambda pp: pp.find("NoSuchTemplateXyz") is not None,
                            timeout=1, interval=0.2, desc="等待不存在的元素")
        check(not v2.ok, "wait_until 超时正确失败", v2.detail)

        # ④ 重放: 轨迹读回 → 重放同一语义目标
        recs = Actuator.load_trace(sess.dir / "steps.jsonl")
        check(len(recs) >= 1, "轨迹可读回", f"{len(recs)} 条")
        if recs:
            r2 = act.replay(recs[:1], stop_on_fail=False)
            check(r2 and r2[0].point is not None, "重放重新定位并给出坐标",
                  f"{r2[0].point if r2 else None}")

    ok = sum(1 for s, _ in RESULTS if s)
    print(f"\n{ok}/{len(RESULTS)} 通过")
    sys.exit(0 if ok == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
