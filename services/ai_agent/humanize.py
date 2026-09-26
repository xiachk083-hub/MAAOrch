"""拟人化执行器：所有游戏操作加随机扰动，避免固定间隔/固定坐标的机械特征。

2026-09-16 定稿参数（含用户修正）：
  · tap 坐标        ±4 px
  · 步进时长         uniform(1.0, 2.5)      —— 解暂停后让游戏走多久
  · 暂停/解暂停间隔  uniform(0.1, 0.4)      —— 固定 0.2s 同样会被行为分析看出
  · 无意义操作       每 15~30 回合、30% 概率触发（不是每 5~10 回合：太频繁本身是异常模式）
  · 随机种子         默认不加（用系统熵源）；只有调试才显式传 seed
自检口径：范围覆盖 min<1.2 / max>2.3，标准差 ≥0.3（uniform(1.0,2.5) 理论 σ≈0.43）
"""
from __future__ import annotations
import random
import time


class Humanizer:
    def __init__(self, rng: random.Random | None = None, tap_jitter: int = 4):
        # 不传 rng 就用系统熵源：避免"调试时 seed(42) 忘了改 → 生产行为完全固定"
        self.rng = rng or random.Random()
        self.tap_jitter = tap_jitter
        self.rounds = 0
        self._next_idle = self._draw_idle_gap()

    # ---- 参数抽取 ----
    def _draw_idle_gap(self) -> int:
        return self.rng.randint(15, 30)

    def jitter(self, x: int, y: int) -> tuple[int, int]:
        return (x + self.rng.randint(-self.tap_jitter, self.tap_jitter),
                y + self.rng.randint(-self.tap_jitter, self.tap_jitter))

    def step_duration(self) -> float:
        return self.rng.uniform(1.0, 2.5)

    def pause_gap(self) -> float:
        return self.rng.uniform(0.1, 0.4)

    def idle_decision(self) -> bool:
        """本回合是否插入无意义操作（低频 + 概率触发）。"""
        self.rounds += 1
        if self.rounds >= self._next_idle:
            self._next_idle = self.rounds + self._draw_idle_gap()
            return self.rng.random() < 0.30
        return False

    # ---- 与执行器结合的示例用法 ----
    def tap(self, executor_tap, x: int, y: int, w: float = 0.0) -> None:
        jx, jy = self.jitter(x, y)
        executor_tap(jx, jy, w if w else self.pause_gap())
        if self.idle_decision():
            ix = self.rng.randint(400, 1500)
            iy = self.rng.randint(200, 600)
            time.sleep(self.rng.uniform(0.3, 0.8))
            executor_tap(ix, iy, self.pause_gap())


if __name__ == "__main__":
    h = Humanizer()
    steps = [h.step_duration() for _ in range(50)]
    gaps = [h.pause_gap() for _ in range(50)]
    offs = [h.jitter(0, 0) for _ in range(50)]

    def stats(name, xs, lo_bar, hi_bar, sd_bar):
        import statistics
        mn, mx, sd = min(xs), max(xs), statistics.pstdev(xs)
        ok = (mn < lo_bar) and (mx > hi_bar) and (sd >= sd_bar)
        print(f"  {name:<12} min={mn:.2f} max={mx:.2f} σ={sd:.3f}  "
              f"判据(min<{lo_bar}, max>{hi_bar}, σ≥{sd_bar}) → {'通过 ✓' if ok else '不达标 ✗'}")
        return ok

    print("随机化自检（50 样本）:")
    s1 = stats("步进时长", steps, 1.2, 2.3, 0.30)
    s2 = stats("暂停间隔", gaps, 0.12, 0.38, 0.06)
    xs = [o[0] for o in offs]; ys = [o[1] for o in offs]
    print(f"  tap 偏移      x∈[{min(xs)},{max(xs)}] y∈[{min(ys)},{max(ys)}]（期望都在 ±4 内，且不是常数）")
    booleans = [h.idle_decision() for _ in range(300)]
    trig = sum(booleans)
    print(f"  无意义操作    300 回合触发 {trig} 次 ≈ {trig/300*100:.1f}%（预期 ≈30% × 1/22.5 ≈ 1.3%~2%）")
    print("总判定:", "通过 ✓" if (s1 and s2) else "有项目不达标 ✗")
