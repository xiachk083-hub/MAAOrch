"""执行层 — 把"点这个/滑这里"落到 adb，并保证可验证、可重放、可回退。

仓库里缺的三件东西（2026-09-14 取证）:
  1. **等到元素出现**：全仓没有任何 wait_for(predicate)；现有代码一律
     `time.sleep(2.0)` 硬等（stage_probe._judge_normal 每次 tap 后只查一次）。
     网络/加载一慢就误判 → 这里补轮询 + 连续 N 次稳定才算成立。
  2. **步级留证与回退**：失败要么 `_back()` 换个网格点，要么直接返回；没有
     "重试 / 留证 / 报哪一步挂了"。这里每步存前后截图 + 画面变化量 + 命中
     坐标，失败按 attempts 退避重试。
  3. **操作序列可重放**：没有把一次操作落盘再回放的录制器。这里每步写
     steps.jsonl（含目标语义、解析出的像素、命中分数、前后状态），
     P2 的"导航固化"和 P3 的"作业固化"都从这份轨迹长出来。

坐标纪律：每步点击前**重新定位**，不用上一帧的坐标 —— 列表滑动、动画、
关卡详情页位置都会变，缓存坐标是这类脚本最常见的翻车原因。
"""
from __future__ import annotations

import json
import pathlib
import time
from dataclasses import dataclass, field, asdict
from typing import Callable

import numpy as np

from services.ai_agent.percept import PROJ, Hit, Perception, ResourcePaths, log

TRACE_ROOT = PROJ / "logs" / "agent"


@dataclass
class StepResult:
    """一步操作的结果 —— 也是写入 steps.jsonl 的记录体。

    target 是给人看的标签；target_spec 是**机器可读的原始目标**（dict/str/坐标），
    重放要走 target_spec —— 自检时踩过：只存标签的话，重放拿到的是
    `"{'template': 'X'}"` 这种 str(dict)，解析必然失败。
    """

    action: str
    target: str
    ok: bool
    attempts: int = 0
    detail: str = ""
    target_spec: object | None = None
    point: tuple[int, int] | None = None
    hit_score: float | None = None
    hit_box: list[int] | None = None
    state_before: str = ""
    state_after: str = ""
    diff: float = 0.0
    elapsed: float = 0.0
    soft_pass: bool = False     # 验证词未命中但画面已变化（明确标记，别当严格通过）
    shots: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["point"] = list(self.point) if self.point else None
        d["target_spec"] = _jsonable(self.target_spec)
        d["ts"] = time.time()
        return d


def _jsonable(obj):
    """把目标规格转成可落盘的形态（Hit / tuple 都要能进 jsonl）。"""
    if isinstance(obj, Hit):
        return {"template": obj.label, "resolved": obj.as_dict()}
    if isinstance(obj, tuple):
        return list(obj)
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, set)):
        return [_jsonable(v) for v in obj]
    return str(obj)


class Session:
    """一次执行会话：一个目录 + 一份 steps.jsonl + 顺序编号的截图。"""

    def __init__(self, tag: str = "session", root: pathlib.Path | None = None) -> None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)[:40]
        self.dir = (root or TRACE_ROOT) / f"{stamp}_{safe}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._jsonl = self.dir / "steps.jsonl"
        self.n = 0

    def shot(self, img: np.ndarray | None, label: str) -> str | None:
        """落盘一张截图，返回路径（相对仓库根，便于前端直接取）。"""
        if img is None:
            return None
        import cv2
        self.n += 1
        p = self.dir / f"{self.n:03d}_{label}.png"
        try:
            cv2.imwrite(str(p), img)
        except Exception as e:
            log(f"截图落盘失败: {e}")
            return None
        return str(p)

    def record(self, step: StepResult, extra: dict | None = None) -> None:
        rec = step.as_dict()
        if extra:
            rec.update(extra)
        try:
            with open(self._jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            log(f"轨迹落盘失败: {e}")


def frame_diff(a: np.ndarray | None, b: np.ndarray | None) -> float:
    """两帧画面变化量（0-1）。压到 160x90 灰度再比，抗压缩噪声、几毫秒出结果。

    用来做最弱的"有没有发生事情"判定：点了没变化 = 这一下多半没生效。
    """
    import cv2
    if a is None or b is None:
        return 0.0
    ga = cv2.cvtColor(cv2.resize(a, (160, 90)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    gb = cv2.cvtColor(cv2.resize(b, (160, 90)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(np.abs(ga - gb).mean() / 255.0)


@dataclass
class Verdict:
    ok: bool
    detail: str = ""


class Actuator:
    """adb 输入 + 验证 + 留证。

    screen 有 run_shell 才真正下发输入（在线）；离线截图源（ImageScreen）
    只走感知与验证、不点 —— 离线回归靠这个跑通逻辑而不动设备。
    """

    def __init__(self, percept: Perception, session: Session | None = None,
                 post_delay: float = 1.1, verify_timeout: float = 6.0) -> None:
        self.p = percept
        self.session = session or Session("agent")
        # 动作后等画面稳定。0.5s 太短 —— 游戏页面切换（主界面→终端）要 ~1s，
        # 实测会导致"点成功了但验证读到旧界面"（2026-09-14 第一次真机动作）。
        self.post_delay = post_delay
        self.verify_timeout = verify_timeout    # 验证轮询上限

    def _verify_polling(self, verify, before, min_diff: float, deadline: float):
        """点击后**轮询**验证条件，直到成立或超时。

        单次快照验证对"页面切换/加载"类动作是错的判定方式（画面还没切完）。
        这里是 wait_until 的落地版：边等边重截，命中即成功。
        """
        last = Verdict(False, "未验证")
        img = None
        while time.time() < deadline:
            img = self.p.screen.capture()
            last = self.check(verify, img, before=before, min_diff=min_diff)
            if last.ok:
                return last, img
            time.sleep(0.4)
        return last, img

    # ── 底层输入 ──
    def _exec(self, args: list[str], dry: bool = False) -> bool:
        if dry or not hasattr(self.p.screen, "run_shell"):
            return False
        try:
            r = self.p.screen.run_shell(args)
            return getattr(r, "returncode", 0) == 0
        except Exception as e:
            log(f"adb 输入失败 {' '.join(args)}: {e}")
            return False

    def tap(self, x: int, y: int) -> bool:
        return self._exec(["input", "tap", str(int(x)), str(int(y))])

    def swipe(self, x1, y1, x2, y2, duration: int = 500) -> bool:
        return self._exec(["input", "swipe", str(int(x1)), str(int(y1)),
                           str(int(x2)), str(int(y2)), str(int(duration))])

    def back(self) -> bool:
        return self._exec(["input", "keyevent", "4"])

    def keyevent(self, code: int) -> bool:
        return self._exec(["input", "keyevent", str(int(code))])

    # ── 等待 ──
    def wait_until(self, predicate: Callable[[Perception], object],
                   timeout: float = 10.0, interval: float = 0.5,
                   stable: int = 1, desc: str = "") -> Verdict:
        """轮询直到 predicate 成立（连续 stable 次），超时返回失败。

        predicate(percept) 返回:
            True / 非空  → 成立
            (bool, str)  → 成立性 + 说明（说明会写进日志，排查用）
        """
        deadline = time.time() + timeout
        hits = 0
        last = ""
        while time.time() < deadline:
            try:
                r = predicate(self.p)
            except Exception as e:
                r = (False, f"predicate 异常: {e}")
            ok, detail = (r if isinstance(r, tuple) else (bool(r), ""))
            last = detail or last
            hits = hits + 1 if ok else 0
            if hits >= stable:
                return Verdict(True, f"{desc or '条件成立'}{('（' + detail + '）') if detail else ''}")
            time.sleep(interval)
        return Verdict(False, f"{desc or '条件'}超时 {timeout}s{('：' + last) if last else ''}")

    # ── 验证 ──
    def check(self, verify, img: np.ndarray, before: np.ndarray | None = None,
              min_diff: float = 0.0) -> Verdict:
        """统一的验证入口。

        verify 形态:
            None                      → 只要画面有变化（min_diff）就算过
            "文字" / {"ocr": ...} 等   → 能在新画面里定位到它
            {"absent": target}        → 定位不到才算过（弹窗消失类）
            {"state": "战斗中"}        → 状态判别命中该状态
            callable(percept)         → 自定义判定
        """
        if verify is None:
            if min_diff <= 0:
                return Verdict(True, "无验证条件")
            d = frame_diff(before, img)
            return Verdict(d >= min_diff, f"画面变化 {d:.3f}（阈值 {min_diff}）")
        if callable(verify):
            try:
                r = verify(self.p)
            except Exception as e:
                return Verdict(False, f"验证异常: {e}")
            return Verdict(*r) if isinstance(r, tuple) else Verdict(bool(r), "自定义验证")
        if isinstance(verify, dict) and "state" in verify:
            st = self.p.state(img)
            ok = verify["state"] in st["verdict"]
            return Verdict(ok, f"状态 {st['verdict']}（{st['score']}）")
        if isinstance(verify, dict) and "absent" in verify:
            hit = self.p.locate(verify["absent"], img=img)
            return Verdict(hit is None, "已消失" if hit is None else f"仍存在 {hit.center}")
        hit = self.p.locate(verify, img=img)
        if hit:
            return Verdict(True, f"命中 {hit.label} @{hit.center} ({hit.score:.2f})")
        return Verdict(False, "未找到验证目标")

    # ── 一步操作 ──
    def do(self, target, verify=None, retries: int = 2, note: str = "",
           post_delay: float | None = None, min_diff: float = 0.0,
           dry: bool = False) -> StepResult:
        """定位 → 点击 → 验证；失败重试（每次重新定位）。

        target 见 Perception.locate；verify 见 Actuator.check。
        """
        t0 = time.time()
        label = note or (target if isinstance(target, str) else json.dumps(target, ensure_ascii=False))
        res = StepResult(action="tap", target=str(label), target_spec=target, ok=False)
        for attempt in range(1, retries + 2):
            res.attempts = attempt
            before = self.p.screen.capture()
            try:
                st = self.p.state(before) if before is not None else {"verdict": ""}
                res.state_before = st.get("verdict", "")
            except Exception:
                pass
            hit = self.p.locate(target, img=before)
            if hit is None:
                res.detail = f"第 {attempt} 次未定位到目标"
                log(f"  [step] {label} → {res.detail}")
                if attempt <= retries:
                    time.sleep(0.6)
                continue
            res.point = hit.center
            res.hit_score = round(hit.score, 3)
            res.hit_box = list(hit.box)
            if dry:
                self._exec([], dry=True)
            else:
                self.tap(*hit.center)
            time.sleep(self.post_delay if post_delay is None else post_delay)
            after = self.p.screen.capture()
            if verify is not None:
                v, after2 = self._verify_polling(
                    verify, before, min_diff, time.time() + self.verify_timeout)
                after = after2 if after2 is not None else after   # ndarray 不能用 or
                res.diff = frame_diff(before, after)
            try:
                st2 = self.p.state(after) if after is not None else {"verdict": ""}
                res.state_after = st2.get("verdict", "")
            except Exception:
                pass
            if verify is None:
                v = self.check(verify, after, before=before, min_diff=min_diff)
            elif not v.ok:
                pass          # 轮询已给出结论
            res.detail = f"@{hit.center} 命中 {hit.score:.2f}；{v.detail}"
            res.shots = [s for s in (self.session.shot(before, f"{label}_before"[:40]),
                                     self.session.shot(after, f"{label}_after"[:40])) if s]
            if not v.ok and verify is not None and res.state_before and res.state_after                     and res.state_before != res.state_after:
                # 验证词没对上，但画面确实变了 —— 记为"推进"并明确标注，
                # 由下一步重新观察来兜（导航每一步都会重新决策）。
                # 不标出来的话，这类"其实成了"的步骤会被当成失败重试，
                # 而重试时目标已经不在画面上（2026-09-14 反复踩到）。
                v = Verdict(True, f"{v.detail}｜画面已变化 {res.state_before} → "
                                  f"{res.state_after}，按推进处理(soft_pass)")
                res.soft_pass = True
            if v.ok:
                res.ok = True
                res.elapsed = round(time.time() - t0, 2)
                self.session.record(res)
                log(f"  [step] {label} ✓ {res.detail}（{res.elapsed}s）")
                return res
            log(f"  [step] {label} ✗ 第 {attempt} 次 {res.detail}")
            if attempt <= retries:
                time.sleep(0.8)
        res.elapsed = round(time.time() - t0, 2)
        self.session.record(res)
        return res

    def swipe_do(self, x1, y1, x2, y2, duration: int = 500, verify=None,
                 retries: int = 1, note: str = "") -> StepResult:
        """滑动 + 验证（列表翻页/镜头移动用）。"""
        t0 = time.time()
        spec = {"swipe": [int(x1), int(y1), int(x2), int(y2)], "duration": int(duration)}
        res = StepResult(action="swipe", target=note or f"{x1},{y1}->{x2},{y2}",
                         target_spec=spec, ok=False)
        for attempt in range(1, retries + 2):
            res.attempts = attempt
            before = self.p.screen.capture()
            st = self.p.state(before) if before is not None else {"verdict": ""}
            res.state_before = st.get("verdict", "")
            self.swipe(x1, y1, x2, y2, duration)
            time.sleep(max(self.post_delay, 0.6))
            after = self.p.screen.capture()
            res.diff = frame_diff(before, after)
            st2 = self.p.state(after) if after is not None else {"verdict": ""}
            res.state_after = st2.get("verdict", "")
            v = self.check(verify, after, before=before, min_diff=0.0 if verify else 0.01)
            res.detail = v.detail
            res.shots = [s for s in (self.session.shot(before, (res.target + "_before")[:40]),
                                     self.session.shot(after, (res.target + "_after")[:40])) if s]
            if not v.ok and verify is not None and res.state_before and res.state_after                     and res.state_before != res.state_after:
                # 验证词没对上，但画面确实变了 —— 记为"推进"并明确标注，
                # 由下一步重新观察来兜（导航每一步都会重新决策）。
                # 不标出来的话，这类"其实成了"的步骤会被当成失败重试，
                # 而重试时目标已经不在画面上（2026-09-14 反复踩到）。
                v = Verdict(True, f"{v.detail}｜画面已变化 {res.state_before} → "
                                  f"{res.state_after}，按推进处理(soft_pass)")
                res.soft_pass = True
            if v.ok:
                res.ok = True
                res.elapsed = round(time.time() - t0, 2)
                self.session.record(res)
                return res
        res.elapsed = round(time.time() - t0, 2)
        self.session.record(res)
        return res

    # ── 重放（P2 导航固化 / P3 作业固化的基础） ──
    @staticmethod
    def load_trace(path) -> list[dict]:
        out = []
        for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        return out

    def replay(self, records: list[dict], stop_on_fail: bool = True) -> list[StepResult]:
        """按记录的语义目标重放。

        点击走 target_spec 重新定位（**不用记录里的像素坐标** —— 列表滑动、
        动画、关卡详情页位置都会变，缓存坐标是这类脚本最常见的翻车点）。
        滑动是几何动作、没有语义可重解析，所以按记录的坐标重放并在注释里说明。
        """
        results = []
        for rec in records:
            action = rec.get("action", "tap")
            spec = rec.get("target_spec")
            if isinstance(spec, dict) and "template" in spec and "resolved" in spec:
                spec = {"template": spec["template"]}      # 命中记录 → 用模板名重解析
            if action == "swipe":
                geo = (spec or {}).get("swipe") if isinstance(spec, dict) else None
                if not geo:
                    continue
                res = self.swipe_do(*geo, duration=(spec or {}).get("duration", 500),
                                    note=f"replay:{rec.get('target')}")
            else:
                res = self.do(spec if spec is not None else rec.get("target", ""),
                              note=f"replay:{rec.get('target')}")
            results.append(res)
            if not res.ok and stop_on_fail:
                break
        return results
