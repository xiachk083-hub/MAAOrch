#!/usr/bin/env python3
"""打关 MVP 循环（单一控制者 + 状态驱动 + 暂停中观察）。

对应 docs/AGENT_ACTION_MAP.md §4 的 MVP：
    动作：取图 / 判状态 / 点 / 返回 / 等待状态 / 暂停·恢复 / 跳过剧情 / 点掉结算 / 三星核验
    流程：goto_stage → 起 MAA 打这一关 → 盯状态
          失败 → 点掉 → 换下一份方案 → 重开
          通关 → 三星核验 → 点掉 → 退地图 → （有下一节点就进下一关）
铁律（今晚的教训，全部写进代码）：
    1) 只在状态"跳变"时动作一次；同一状态不重复点
    2) 点节点只在「地图 → 关卡页」这一次跳变上做
    3) 不 sleep 盲等，用 wait_state(超时)
    4) 卡住 >8s 才做一次单向自愈（确认→开始行动→返回），且先确认没有 MAA 在跑
    5) 退出时带走自己的子进程

用法:
  python tools/clear_runner.py --emu 2 --stage act16side_ex02 --node 390,258 \
      --plans data/copilot/ex2_jobs/ex2_84728.json,data/copilot/ga_derived/act16side_ex02_derived.json \
      --max-tries 3
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "tools"))
import screen_state as SS  # noqa: E402

PAUSE = (1200, 50)      # MAA BattlePause rect [1170,20,60,60]
SPEED = (1100, 50)      # MAA BattleSpeedUp rect [1070,20,60,60]


class Runner:
    def __init__(self, emu: str, node: tuple[int, int], plans: list[str], max_tries: int, out_dir: pathlib.Path):
        from services.ai_agent.percept import AdbScreen
        self.sc = AdbScreen(emu)
        self.sc.ensure_connected()
        self.node = node
        self.plans = plans
        self.max_tries = max_tries
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)
        self.steps = (self.out / "steps.jsonl").open("a", encoding="utf-8")
        self.proc: subprocess.Popen | None = None
        self.try_idx = 0
        self.heals = 0            # 自愈次数上限（今晚第三次同类教训：任何自动动作都必须有界）
        self.last_heal = 0.0
        self.battle_since = 0.0   # 战斗计时：超时必须有兜底（今天卡在暂停就是缺这条）
        self.last_stars: bool | None = None   # 最近一次通关的三星核验（None=判不出）；批量执行器读它
        self.maa_gone_since = 0.0  # MAA 退出时刻：结算页要 1~2s 才渲染，不能拿"退出"当"卡死"
        self.empty_ref = PROJ / "data" / "agent_run" / "_c120.png"   # 空场参照帧（MAA 未部署时拍到的）
        self._empty_warned = False


    def log(self, msg: str, **kv) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        self.steps.write(json.dumps({"ts": time.time(), "msg": msg, **kv}, ensure_ascii=False) + "\n")
        self.steps.flush()

    # ---------- 原子动作 ----------
    def tap(self, x: int, y: int, w: float = 0.7) -> None:
        self.sc._run(["shell", "input", "tap", str(x), str(y)])
        time.sleep(w)

    def back(self, n: int = 1) -> None:
        for _ in range(n):
            self.sc._run(["shell", "input", "back"] if False else ["shell", "input", "keyevent", "4"])
            time.sleep(0.7)

    def safe_tap(self, x: int, y: int, expect: set[str], w: float = 0.8) -> bool:
        """只在当前状态属于 expect 时才点，且**点前重新识别一次**。

        2026-09-16 教训（同类 bug 第三例）：失败结算被点掉后游戏退回详情页，
        收尾代码不验证状态接着点 (640,660)×2，正好落在详情页的「开始行动」上
        → 误开一局无人控制的野战斗。点击必须验证状态：收尾/自愈/导航都适用。
        """
        f = self.sc.capture()
        st = SS.state_of(f)[0] if f is not None else "other"
        if st not in expect:
            self.log(f"跳过点击({x},{y})：当前 {st} 不在预期 {sorted(expect)}（防误触）")
            return False
        self.tap(x, y, w)
        return True

    def wait_state(self, want: set[str], timeout: float = 25.0) -> str:
        t0 = time.time()
        while time.time() - t0 < timeout:
            f = self.sc.capture()
            if f is not None:
                st, _ = SS.state_of(f)
                if st in want:
                    return st
            time.sleep(0.7)
        return "timeout"

    # ---------- 导航（2026-09-16 定稿：主题无关的 QuickSwitch 链） ----------
    # 新版大厅里没有可见的「终端」按钮，MAA 的 Terminal 模板也不匹配本主题（最高 0.63）；
    # 但任务页有 QuickSwitch 小房子，它的下拉第一个入口就是终端。实测全链 1.0 命中。
    MISSION_BTN = (820, 645)     # 大厅「任务」按钮
    LAST_BATTLE = (1100, 578)    # 终端右下「前往上一次作战」（=本次目标关的入口）

    def _find_full(self, name: str, thr: float = 0.9):
        """全屏找 MAA 模板，返回中心坐标或 None。"""
        from services.ai_agent.percept import TemplateMatcher
        f = self.sc.capture()
        if f is None:
            return None
        if not hasattr(self, "_tm"):
            self._tm = TemplateMatcher()
        hit = self._tm.find(f, name, threshold=thr)
        return hit.center if hit else None

    CLOSE_FALLBACK = (1216, 43)     # 已验证的公告弹窗 X（相对 0.95/0.06；模型给不合理坐标时才用）

    def close_announcement_if_present(self) -> bool:
        """导航链第 0 步：关掉公告/资讯弹窗。

        坐标**不写死**：由视觉模型读图给出；只在模型坐标不合理时用已验证的 fallback。
        双重验证：画面必须变化 + 再读图确认不再是公告页；**失败就停**（不盲按）。
        （2026-09-16 实测：回大厅时活动公告自动弹出，会把任务按钮挡住 → 导航链 8 次超时）
        """
        try:
            from services.ai_agent import llm as _LLM
            L = _LLM.LLM()
        except Exception:
            return False
        if not L.vision_ready:
            self.log("视觉不可用 → 跳过弹窗检查")
            return False
        q = ("这是《明日方舟》截图。画面中是否有公告/资讯弹窗？若有，给出右上角关闭按钮(X)的"
             "中心点相对坐标 x,y（0~1）。只输出 JSON: {\"is_announcement\": true/false, "
             "\"close_x\": 0.xx, \"close_y\": 0.yy}")
        f0 = self.sc.capture()
        r = L.chat("你是方舟UI识别器，只描述看到的。", q, image=f0, vision=True, max_tokens=16000)
        txt = (r.get("text") or "") if isinstance(r, dict) and r.get("ok") else "{}"
        import json as _json, re as _re
        m = _re.search(r"\{[\s\S]*\}", txt)
        d = _json.loads(m.group(0)) if m else {}
        if not d.get("is_announcement"):
            return False
        try:
            rx, ry = float(d.get("close_x")), float(d.get("close_y"))
            assert 0 <= rx <= 1 and 0 <= ry <= 1
            x, y = int(rx * f0.shape[1]), int(ry * f0.shape[0])
        except Exception:
            x, y = self.CLOSE_FALLBACK
        self.log(f"检测到公告弹窗 → 点 X ({x},{y})")
        self.tap(x, y, 1.0)
        f1 = self.sc.capture()
        import numpy as _np
        changed = f0 is not None and f1 is not None and float(_np.abs(f1.astype(int) - f0.astype(int)).mean()) > 3
        if not changed:
            raise RuntimeError("关弹窗后画面未变 → 停下交人工（不盲按）")
        r2 = L.chat("你是方舟UI识别器，只描述看到的。", q, image=f1, vision=True, max_tokens=16000)
        t2 = (r2.get("text") or "") if isinstance(r2, dict) and r2.get("ok") else "{}"
        m2 = _re.search(r"\{[\s\S]*\}", t2)
        if m2 and _json.loads(m2.group(0)).get("is_announcement"):
            raise RuntimeError("弹窗未关掉 → 停下交人工")
        self.log("弹窗已关 ✓")
        return True

    def go_terminal(self) -> bool:
        """任意页面 → 终端页。返回是否到达。"""
        # 第 0 步**懒触发**：只在「非 main/detail」的未知页才读图，
        # 否则每次尝试都吃 5~15s 模型延迟 → 导航链会因超时收工（2026-09-16 实测踩过）
        try:
            f0 = self.sc.capture()
            st0 = SS.state_of(f0)[0] if f0 is not None else "other"
            if st0 in ("main", "detail", "form"):
                self.log(f"第0步跳过（当前 {st0}，无需清弹窗）")
            else:
                self.close_announcement_if_present()
        except RuntimeError as e:
            self.log(f"弹窗处理失败：{e}")
            return False
        for attempt in range(2):
            c = self._find_full("QuickSwitch/Menu.png")
            if c is None:
                self.log("找不到 QuickSwitch 小房子 → 先点大厅「任务」进去")
                # ⚠️ 白名单必须含 `main`（2026-09-16 实测踩到）：主界面上状态判别就是 `main`，
                # 原来只写 {"other"} ⇒ safe_tap 直接拒点，导航链在第一步就死（日志：
                # 「跳过点击(269,37)：当前 main 不在预期 [...]（防误触）」）。
                self.safe_tap(*self.MISSION_BTN, {"other", "main"}, 2.0)
                c = self._find_full("QuickSwitch/Menu.png")
            if c is None:
                self.log(f"第 {attempt + 1} 次仍找不到小房子")
                continue
            self.log(f"小房子 {c} → 开快捷切换")
            self.safe_tap(*c, {"other", "main", "exmap", "detail", "form"}, 1.8)
            tc = self._find_full("QuickSwitch/Terminal.png")
            if tc is None:
                self.log("下拉里没找到终端入口 → 退回")
                self.back(1)
                continue
            self.log(f"终端入口 {tc} → 点入")
            self.safe_tap(*tc, {"other", "main", "form", "detail", "exmap"}, 3.0)
            if self._find_full("QuickSwitch/Menu.png") is not None:
                return True          # 终端页也有小房子
        return False

    MAP_REF = PROJ / "data" / "agent_run" / "_rn_3_map.png"   # 活动地图参照帧（实测指纹 1.000）

    def _looks_like_map(self, frame, thr: float = 0.95) -> bool:
        """帧指纹闸门：与参照地图帧高相关即认为「在活动地图上」。

        2026-09-16 实测：状态判别器偶发漏判 exmap（同一帧有时读 exmap、有时读 other），
        一漏判就跳到大厅链、找不到小房子直接停。指纹比判别器稳（相关系数 1.000）。
        """
        cv2 = getattr(SS, "cv2", None)
        if cv2 is None or frame is None or not self.MAP_REF.exists():
            return False
        try:
            ref = cv2.imread(str(self.MAP_REF))
            if ref is None:
                return False
            a = cv2.cvtColor(cv2.resize(frame, (64, 36)), cv2.COLOR_BGR2GRAY).astype("float32")
            b = cv2.cvtColor(cv2.resize(ref, (64, 36)), cv2.COLOR_BGR2GRAY).astype("float32")
            a = (a - a.mean()) / (a.std() + 1e-6)
            b = (b - b.mean()) / (b.std() + 1e-6)
            return float((a * b).mean()) >= thr
        except Exception:
            return False

    def goto_stage(self) -> str:
        """已在活动地图 → 直接点节点；否则走 QuickSwitch 链到终端→前往上一次作战。

        点节点是**状态化**的（仅 exmap 下执行），不是以前的盲点兜底。
        """
        f = self.sc.capture()
        st_now = SS.state_of(f)[0] if f is not None else "other"
        if st_now == "exmap" or self._looks_like_map(f):
            self.log(f"已在活动地图 → 点节点 {self.node}")
            self.safe_tap(*self.node, {"exmap"}, 1.6)
            st = self.wait_state({"detail", "form", "battle"}, timeout=10)
            if st != "timeout":
                return st
        if self.go_terminal():
            self.log(f"点「前往上一次作战」{self.LAST_BATTLE}")
            # 终端页的状态判别实测是 `main`（不是 other）⇒ 这里也必须收 `main`
            self.safe_tap(*self.LAST_BATTLE, {"other", "main"}, 3.5)
            st = self.wait_state({"detail", "form", "battle"}, timeout=10)
            if st != "timeout":
                return st
        self.log("导航失败：没走到详情页 → 停下（无兜底盲点，交人工）")
        return "timeout"

    def _looks_empty_battle(self, frame, thr: float = 0.97) -> bool:
        """画面与「空场参照」高相关 = 一个干员都没下（MAA 未执行作业）。"""
        cv2 = getattr(SS, "cv2", None)
        if cv2 is None or frame is None or not self.empty_ref.exists():
            return False
        try:
            ref = cv2.imread(str(self.empty_ref))
            if ref is None:
                return False
            a = cv2.cvtColor(cv2.resize(frame, (64, 36)), cv2.COLOR_BGR2GRAY).astype("float32")
            b = cv2.cvtColor(cv2.resize(ref, (64, 36)), cv2.COLOR_BGR2GRAY).astype("float32")
            a = (a - a.mean()) / (a.std() + 1e-6); b = (b - b.mean()) / (b.std() + 1e-6)
            return float((a * b).mean()) >= thr
        except Exception:
            return False

    def mma_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start_plan(self, plan: str) -> None:
        """起一把 MAA（编队+开打由 MAA 负责；agent 负责状态与判定）"""
        self.log(f"起 MAA：{pathlib.Path(plan).name}", plan=plan)
        self.proc = subprocess.Popen(
            [sys.executable, str(PROJ / "tools" / "maa_copilot_run.py"),
             "--emu", str(self.sc.emu_idx), "--copilot", plan, "--timeout", "420",
             "--no-startup"],      # 关卡页由 agent 打开，MAA 的 StartUp 会把它关掉（实测）
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def kill_own(self) -> None:
        if self.mma_running():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
        self.steps.close()

    # ---------- 流程 ----------
    def run(self, next_node: tuple[int, int] | None = None) -> str:
        """返回 结果字符串：cleared / exhausted / timeout"""
        # 0) 清场：失败/通关结算、弹窗先点掉，否则导航动作点在它们身上无效（今晚实测）
        for _ in range(4):
            f = self.sc.capture()
            st, _c = SS.state_of(f) if f is not None else ("other", 0)
            if st in ("fail", "win"):
                self.log(f"清场：{st} → 点掉结算（640,660 ×2，点前验证状态）")
                self.safe_tap(640, 660, {"win", "fail"}, 0.8)
                self.safe_tap(640, 660, {"win", "fail"}, 0.8)
                continue
            if st == "dialog":
                self.log("清场：弹窗 → 确认（800,465）")
                self.safe_tap(800, 465, {"dialog"}, 0.8)
                continue
            break

        # 1) 导航：**先看状态**，已在关卡页/编队页/战斗中就不点节点（点在详情页上会误触）
        f = self.sc.capture()
        st0, _ = SS.state_of(f) if f is not None else ("other", 0)
        self.log(f"起始状态：{st0}")
        if st0 not in ("detail", "form", "battle"):
            st = self.goto_stage()
            self.log(f"导航结果状态：{st}")
            if st == "timeout":
                self.log("两次都没进关卡页 → 收工（不盲目起 MAA）")
                return "nav_failed"
        # 2) 起第一把
        self.start_plan(self.plans[self.try_idx])

        state, handled, stuck = "start", None, time.time()
        t_end = time.time() + 3600
        while time.time() < t_end:
            f = self.sc.capture()
            if f is None:
                time.sleep(1.0)
                continue
            st, conf = SS.state_of(f)
            if st != state:
                self.log(f"状态 → {st}（{conf}）")
                state, handled, stuck = st, None, time.time()

            if handled == state:
                # 战斗超时兜底：>8 分钟还在"战斗中" → 解除暂停（游戏可能被 MAA 停住）并退回
                if (st == "battle" and self.battle_since and not self._empty_warned
                        and time.time() - self.battle_since > 30):
                    f2 = self.sc.capture()
                    if self._looks_empty_battle(f2):
                        self._empty_warned = True
                        self.log("!! 战斗 30s 后画面仍与「空场」参照一致 → MAA 未执行作业，止损：放弃行动（返理智）")
                        self.safe_tap(80, 45, {"battle", "other"}, 1.5)
                        self.safe_tap(960, 574, {"other", "battle"}, 2.0)
                        return "no_deploy"
                if st == "battle" and self.battle_since and time.time() - self.battle_since > 480:
                    self.log("战斗 >480s 未结束 → 解除暂停(1220,52) 并退回地图")
                    self.safe_tap(1220, 52, {"battle"}, 1.0)
                    if not self.mma_running():
                        self.back(2)
                    self.battle_since = time.time()
                # 同一状态不重复动作；只有"卡太久"才做一次单向自愈
                # 阈值按"这个状态正常该花多久"：编队页要给 MAA 45s（选人本来就慢）
                limit = 45 if st == "form" else 20
                # MAA 退出 ≠ 卡死：结算页（行动结束）要 1~2s 才渲染出来。
                # 2026-09-16 实测：自愈在 MAA 退出的同一秒就点了确认+开始行动，
                # 把刚打完的结算页当卡死重开 → 每局结果都被自己吃掉。所以：
                #   ① 记下 MAA 何时退出；② 给 6s 宽限；③ 动手前重新识一次状态。
                if self.mma_running():
                    self.maa_gone_since = 0.0
                elif not self.maa_gone_since:
                    self.maa_gone_since = time.time()
                    self.log("MAA 已退出 → 宽限 6s（等结算页/主界面渲染，防自愈抢结果）")
                ready = bool(self.maa_gone_since) and time.time() - self.maa_gone_since >= 6.0
                if ready:
                    f2 = self.sc.capture()
                    fresh = SS.state_of(f2)[0] if f2 is not None else st
                    if fresh in ("win", "fail"):
                        self.log(f"宽限后重新识别到结算页：{fresh} → 走结算分支（不自愈）")
                        state, handled, stuck = fresh, None, time.time()
                        self.maa_gone_since = 0.0
                        time.sleep(0.8)
                        continue
                if (st in ("other", "detail", "form") and not self.mma_running() and ready
                        and time.time() - stuck > limit
                        and self.heals < 2 and time.time() - self.last_heal > 30):
                    self.heals += 1; self.last_heal = time.time()
                    self.log(f"{st} 卡住 >{limit}s 且 MAA 已不在跑 → 自愈第 {self.heals}/2 次"
                             f"（点确认(1000,678) → 开始行动(1000,660) → 重开这一把）")
                    self.safe_tap(1000, 678, {"other", "form", "detail"}, 0.8)
                    self.safe_tap(1000, 660, {"form", "other", "detail"}, 0.8)
                    if not self.mma_running():
                        self.back(2); self.safe_tap(*self.node, {"exmap"}, 1.4)
                        self.start_plan(self.plans[min(self.try_idx, len(self.plans) - 1)])
                    stuck = time.time()
                elif self.heals >= 2 and time.time() - stuck > 20:
                    self.log("自愈已用尽且仍卡住 → 收工，交回人工")
                    return "stuck"
                time.sleep(0.8)
                continue

            if st == "battle":
                handled = st                      # 战斗中：只观察，不动手
                self.battle_since = time.time()
                self.log("战斗中（观察模式）")
            elif st == "win":
                handled = st
                time.sleep(3.0)          # 结算页星星逐个亮起，立刻读会读空
                stars = self._verify_stars()
                # 把核验值**留成属性**：`run()` 的返回值三个分支都是 "cleared"（三星/没三星/判不出），
                # 只 log 不返回的话，批量执行器根本分不清"三星通关"和"通关了没三星"。
                # 不改返回字符串是因为 ga_ex_drive/live_drive/plan_loop 都按 "cleared" 比。
                self.last_stars = stars
                self.log(f"通关结算（三星核验={stars}）")
                self.safe_tap(640, 660, {"win"}, 0.8)
                self.safe_tap(640, 660, {"win"}, 0.8)
                self.safe_tap(640, 400, {"win"}, 0.8)
                if stars is True:
                    self.log("✓ 三星通关")
                    if next_node:
                        self.back(2); self.safe_tap(*next_node, {"exmap"}, 1.4)
                        self.log(f"→ 进入下一关节点 {next_node}")
                        return "cleared"
                    return "cleared"
                return "cleared"
            elif st == "fail":
                handled = st
                self.log("任务失败 → 点掉结算(640,660 ×2) → 退回地图 → 换下一份方案")
                self.safe_tap(640, 660, {"fail", "win"}, 0.8)
                self.safe_tap(640, 660, {"fail", "win"}, 0.8)
                self.try_idx += 1
                if self.try_idx >= self.max_tries or self.try_idx >= len(self.plans):
                    self.log("方案用尽，退出")
                    return "exhausted"
                self.back(2); self.safe_tap(*self.node, {"exmap"}, 1.4)
                self.start_plan(self.plans[self.try_idx])
            else:
                handled = st                      # other/detail/form/exmap：观察
                time.sleep(0.8)
        return "timeout"

    def _verify_stars(self) -> bool | None:
        try:
            from maa_job_fallback import verify_three_star
            return verify_three_star(int(self.sc.emu_idx))
        except Exception:
            return None


def kill_stray_maa() -> str:
    """清遗留的 maa_copilot_run（按脚本名精确匹配，绝不 taskkill /IM python.exe —— 我们自己也是 python）。"""
    import subprocess as _sp
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -match 'maa_copilot_run' } | "
          "ForEach-Object { Write-Output ('  孤儿 MAA PID ' + $_.ProcessId + ' 启动于 ' + $_.CreationDate); "
          "Stop-Process -Id $_.ProcessId -Force }")
    try:
        r = _sp.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=40)
        return (r.stdout or "").strip()
    except Exception as e:
        return f"  清理异常: {e}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", default="2")
    ap.add_argument("--stage", required=True)
    ap.add_argument("--node", required=True)
    ap.add_argument("--plans", required=True, help="逗号分隔的本地作业 json（按优先级）")
    ap.add_argument("--next-node", default="")
    ap.add_argument("--max-tries", type=int, default=3)
    args = ap.parse_args()
    node = tuple(int(v) for v in args.node.split(","))
    nxt = tuple(int(v) for v in args.next_node.split(",")) if args.next_node else None
    plans = [x.strip() for x in args.plans.split(",") if x.strip()]
    out = PROJ / "data" / "agent_run" / f"{args.stage}_{time.strftime('%m%d_%H%M')}"
    print("[取证] 清理遗留 MAA 进程：")
    print(kill_stray_maa() or "  （无遗留）")
    r = Runner(args.emu, node, plans, args.max_tries, out)
    try:
        res = r.run(nxt)
    finally:
        r.kill_own()
    print(f"结果：{res}｜留证：{out}")


if __name__ == "__main__":
    main()
