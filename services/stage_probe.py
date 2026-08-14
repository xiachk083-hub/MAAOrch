"""Stage probe — detect whether an account can farm a stage.

Lab-validated logic (docs/STAGE_PROBE.md): MAA navigates to the stage detail
page, we screenshot and judge three states:
  CAN_FARM / CANNOT / DONE(无法前往)

Primary signal: template match of the highlighted PRTS (全权委托) icon inside
the PRTS ROI — score >= 0.4 means the sweep button is active.
Auxiliary: OCR text features (NEW / 剿灭战模拟) on the annihilation list page.

This is the productized version of the lab scripts (anni_batch.py / batch_v3.py).
"""
from __future__ import annotations
import json
import time
import pathlib
import subprocess
import threading
import sys

import numpy as np

# ── Constants (validated 2026-08-09) ──
PRTS_ROI = (876, 532, 183, 123)          # 全权委托按钮区域
START_BTN_ROI = (1090, 620, 190, 60)     # "开始行动"按钮区域（详情页标志）
SUCCESS_THRESHOLD = 0.4                  # UsePrts-AnnihilationSuccess 匹配阈值

STAGES_ANNIHILATION = [
    "Chernobog@Annihilation",
    "LungmenOutskirts@Annihilation",
    "LungmenDowntown@Annihilation",
]

VERDICT_CAN_FARM = "CAN_FARM"
VERDICT_CANNOT = "CANNOT"
VERDICT_DONE = "DONE"

# ── 普通关卡判定（2026-08-12 用户: 检测活动关 TO-9/TO-8/TO-7/TO-5）──
# 详情页**关卡名**区（720x1280 竖屏截图，左上角大字如 "TO-9"。
# 实测: 关卡名在 (236,245) 附近; 右侧 (969,233) 是关卡简介（非标题）。
STAGE_NAME_ROI = (80, 200, 450, 130)
# 活动关卡列表网格（实测 720x1280: "直到大地变成一颗酸橙" 列表布局。
# 12 个位置 = 横向滑动前后两批，OCR 框定位失败时的 fallback。
# MAA OCR 识别不了列表小字关卡名（9→2 / 8→6 误读 → SwipeToStage 滑
# 50 次找不到），改为按网格 tap 进详情页 → 关卡名 OCR 验证（大字可靠）。
GRID_POSITIONS = [
    (168, 349), (886, 341), (1208, 341), (302, 489), (654, 488), (1128, 488),
    (237, 245), (534, 341), (856, 341), (301, 488), (772, 489), (1154, 493),
]
GRID_SWIPE = (1000, 640, 200, 640)  # 列表横向滑动（批次 1 → 批次 2）


class StageProbe:
    """Probe one account's ability on a stage (MAA nav + screenshot judgement).

    Usage (standalone):
        probe = StageProbe(emu_idx, client_type="Official")
        verdict = probe.probe("Chernobog@Annihilation")
        print(verdict)
    """

    def __init__(self, emu_idx, client_type: str = "Official",
                 maa_instance: int = 1, timeout: float = 240.0) -> None:
        self.emu_idx = str(emu_idx)
        self.client_type = client_type
        self.maa_instance = maa_instance
        self.timeout = timeout
        self._log_lines: list[str] = []
        # 视觉模型兜底开关（2026-08-13 用户: 弱信号/异常画面裁决）。
        # 2026-08-14 审查 #2b: 恒真 = 每步截图外呼 LLM（120s 超时 × 遍历
        # 12 位置）→ 默认关，仅「详情页但 OCR 未确认关卡名」或「锁模板
        # 弱匹配」时触发，且每关最多 2 次（_vision_used 计数）。
        self._vision_fallback = False
        self._vision_used = 0

    # ── logging ──
    def _log(self, msg: str) -> None:
        self._log_lines.append(msg)
        print(msg, flush=True)
        try:  # 落盘（pythonw 无控制台，print 丢失 — 2026-08-12 排查用）
            with open(pathlib.Path(__file__).parent.parent / "logs" / "probe.log",
                      "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        except Exception:
            pass

    @property
    def logs(self) -> list[str]:
        return list(self._log_lines)

    # ── helpers ──
    def _inst_dir(self) -> pathlib.Path:
        return (pathlib.Path(__file__).parent.parent
                / "services" / "maa" / "instances" / str(self.maa_instance))

    def _clear_log(self) -> None:
        p = self._inst_dir() / "debug" / "asst.log"
        for _ in range(5):
            try:
                p.write_text("", encoding="utf-8")
                return
            except PermissionError:
                time.sleep(2)

    def _run_asst(self, stage: str, start_game: bool) -> bool:
        """Run MAA StartUp or Fight-nav via SUBPROCESS.

        in-process Asst (MaaCore ctypes) 会崩溃项目进程 — 2026-08-12
        probe 检测时项目进程直接死（crash.log 空，看门狗拉起）。MaaCore
        in-process 与项目环境冲突（项目设计本就禁用 ctypes 直连）。
        改独立 python 子进程执行 — 崩了只影响自己，项目安全。"""
        import sys as _sys
        inst_dir = str(self._inst_dir())
        py_dir = str(self._inst_dir() / "Python")
        lines = [
            "import sys",
            "sys.path.insert(0, %r)" % py_dir,
            "from asst.asst import Asst",
            "Asst.load(path=%r)" % inst_dir,
            "asst = Asst()",
            "ok = asst.connect(%r, %r)" % (self._adb_path(), self._adb_address()),
            "if not ok:",
            "    print('CONNECT_FAIL')",
            "    sys.exit(1)",
            "if %r:" % start_game,
            "    asst.append_task('StartUp', {'client_type': %r, 'start_game_enabled': True})" % self.client_type,
            "else:",
            "    asst.append_task('Fight', {'stage': %r, 'times': 0, 'medicine': 0, 'stone': 0})" % stage,
            "asst.start()",
            "import time",
            "dl = time.time() + %r" % self.timeout,
            "while time.time() < dl and asst.running():",
            "    time.sleep(2)",
            "asst.stop()",
            "print('DONE')",
        ]
        code = chr(10).join(lines)
        try:
            r = subprocess.run([_sys.executable, "-c", code],
                               capture_output=True, text=True,
                               timeout=self.timeout + 60, encoding="utf-8",
                               errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if "DONE" in (r.stdout or ""):
                return True
            self._log(f"asst 子进程异常: {(r.stdout or '').strip()[-100:]} "
                      f"{(r.stderr or '').strip()[-100:]}")
            return False
        except Exception as e:
            self._log(f"asst 子进程失败: {e}")
            return False

    def _adb_path(self) -> str:
        from infrastructure.task_constants import find_mumu_cli, find_adb
        cli = find_mumu_cli()
        if cli:
            cand = str(pathlib.Path(cli).parent / "adb.exe")
            if pathlib.Path(cand).exists():
                return cand
        return find_adb() or "adb"

    def _adb_address(self) -> str:
        """Port straight from MuMuManager — the emulator is the source of truth."""
        from infrastructure.task_constants import detect_emu_instances
        for e in detect_emu_instances():
            if str(e.get("index", "")) == self.emu_idx and e.get("adb_port"):
                return f"127.0.0.1:{e['adb_port']}"
        return ""

    def _capture(self) -> np.ndarray | None:
        """Screenshot via adb exec-out screencap."""
        import cv2
        addr = self._adb_address()
        if not addr:
            return None
        r = subprocess.run([self._adb_path(), "-s", addr, "exec-out", "screencap", "-p"],
                           capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0 or len(r.stdout) < 1000:
            return None
        return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

    # ── 普通关卡判定（活动关/主线，非剿灭 — 2026-08-12）──
    @staticmethod
    def _load_stage_titles() -> dict:
        """读 MAA 资源的活动关卡标题表（act53side_XX → {code, name}）。"""
        titles = {}
        import glob
        base = (pathlib.Path(__file__).parent.parent
                / "services" / "maa" / "instances" / "1"
                / "resource" / "Arknights-Tile-Pos")
        for f in glob.glob(str(base / "act53side_*.json")):
            try:
                d = json.load(open(f, encoding="utf-8"))
                if d.get("code") and d.get("name"):
                    titles[d["code"]] = d["name"]
            except Exception:
                pass
        return titles

    def _ocr_texts(self, img: np.ndarray, roi=None) -> list[tuple[str, float]]:
        """OCR 截图（内联复用 MAA PaddleOCR ONNX — ocr_tool.py 核心）。"""
        import cv2
        import onnxruntime as ort
        inst = self._inst_dir()
        try:
            det = ort.InferenceSession(str(inst / "resource" / "PaddleOCR" / "det" / "inference.onnx"))
            rec = ort.InferenceSession(str(inst / "resource" / "PaddleOCR" / "rec" / "inference.onnx"))
            keys = [l.strip() for l in open(inst / "resource" / "PaddleOCR" / "rec" / "keys.txt",
                                            encoding="utf-8")]
        except Exception as e:
            self._log(f"OCR 初始化失败: {e}")
            return []
        if roi:
            x, y, w, h = roi
            img = img[y:y + h, x:x + w]
        ih, iw = img.shape[:2]
        scale = 960.0 / iw if iw > 960 else 1.0
        # det 模型要求 32 对齐（8 对齐在 ROI 小图报 Add 广播错误 21by22）
        tw = max(32, int(round(iw * scale / 32) * 32))
        th = max(32, int(round(ih * scale / 32) * 32))
        im = img if (tw, th) == (iw, ih) else cv2.resize(img, (tw, th))
        p = im.astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)
        p = (p.transpose(2, 0, 1) - mean) / std
        p = p[np.newaxis, :, :, :].astype(np.float32)
        prob = det.run(None, {det.get_inputs()[0].name: p})[0][0, 0]
        if prob.shape != (th, tw):
            prob = cv2.resize(prob, (tw, th))
        thr = (prob > 0.3).astype(np.uint8) * 255
        k = max(2, int(3 * scale)) if scale != 1.0 else 2
        thr = cv2.dilate(thr, np.ones((k, k), np.uint8))
        contours, _ = cv2.findContours(thr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out = []
        for c in contours:
            x, y, bw, bh = cv2.boundingRect(c)
            if bw < 8 or bh < 8:
                continue
            x, y, bw, bh = int(x / scale), int(y / scale), int(bw / scale), int(bh / scale)
            crop = img[y:y + bh, x:x + bw]
            if crop.size == 0:
                continue
            rh = 48.0
            rw = max(8, min(int(round(crop.shape[1] * rh / crop.shape[0] / 8) * 8), 480))
            crop = cv2.resize(crop, (rw, 48)).astype(np.float32) / 255.0
            crop = (crop - 0.5) / 0.5
            crop = crop.transpose(2, 0, 1)[np.newaxis, :, :, :].astype(np.float32)
            pred = rec.run(None, {rec.get_inputs()[0].name: crop})[0][0]
            text, confs, prev = "", [], -1
            for t, idx in enumerate(np.argmax(pred, axis=1)):
                if idx != prev and idx != 0:
                    text += keys[idx - 1]
                    confs.append(float(pred[t, idx]))
                prev = idx
            if text:
                out.append((text, (x, y, bw, bh), sum(confs) / len(confs) if confs else 0.0))
        return out

    @staticmethod
    def _title_match(ocr_texts: list[tuple[str, float]], expected: str) -> bool:
        """标题匹配（容错 OCR 误读）: 任一 OCR 文本含标题前 3+ 字符。"""
        if not expected:
            return False
        head = expected[:3]
        for text, _score in ocr_texts:
            if head in text or text in expected or expected[:2] in text and len(text) >= 2:
                return True
        return False

    def _tap(self, x: int, y: int) -> None:
        addr = self._adb_address()
        if addr:
            subprocess.run([self._adb_path(), "-s", addr, "shell", "input", "tap", str(x), str(y)],
                           capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def _swipe(self, x1: int, y1: int, x2: int, y2: int) -> None:
        addr = self._adb_address()
        if addr:
            subprocess.run([self._adb_path(), "-s", addr, "shell", "input", "swipe",
                            str(x1), str(y1), str(x2), str(y2), "500"],
                           capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def _back(self) -> None:
        addr = self._adb_address()
        if addr:
            subprocess.run([self._adb_path(), "-s", addr, "shell", "input", "keyevent", "4"],
                           capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    @staticmethod
    def _split_difficulty(stage: str) -> tuple[str, str | None]:
        """剥离 MAA 难度后缀: "10-6-HARD" → ("10-6", "HARD")。

        MAA 原生支持磨难（官方格式 = 关卡名末尾加 -HARD/-NORMAL，
        10-14 章标准/磨难，15 章后常规/险地），导航全交给 MAA，
        这里只负责把后缀剥掉做关卡名匹配。
        """
        for suffix in ("-HARD", "-NORMAL"):
            if stage.endswith(suffix):
                return stage[: -len(suffix)], suffix[1:]
        return stage, None

    def _judge_normal(self, img: np.ndarray, stage: str) -> dict:
        """普通关卡判定（含磨难）: 详情页判定。

        能刷 = UsePrtsSuccess 图标命中（全权委托已激活，实测 0.998）
        锁   = UnableToAgent2 图标命中（代理锁定）
        兜底 = 关卡名/开始行动按钮 OCR（进入详情页 = 可刷）

        2026-08-12 修正: 普通/磨难关卡详情页按钮是暗色（非金色），
        金色判定只适用于剿灭（金色高亮图标）。MATERIAL_FARMING 的
        "10 章锁"结论来自金色误判 — 实测 10-6-HARD 能刷（UsePrtsSuccess 0.998）。
        """
        import cv2
        base, diff = self._split_difficulty(stage)
        titles = self._load_stage_titles()
        expected_title = titles.get(base, "")
        num = base.rsplit("-", 1)[-1] if "-" in base else base  # "6"
        self._vision_used = 0  # 本轮探测视觉外呼计数（上限 2 — 120s 超时兜底）

        def _tpl(name: str, thresh: float) -> tuple[bool, float]:
            """全图模板匹配（模板小、位置随关卡布局变化 → 全图扫）。"""
            inst = self._inst_dir()
            tpls = list((inst / "resource" / "template" / "Battle" / "UsePrts").glob(name + ".png"))
            if not tpls:
                return False, 0.0
            t = cv2.imread(str(tpls[0]), 0)
            if t is None:
                return False, 0.0
            t = t.astype(np.float32)
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
            res = cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)
            _mn, mx, _a, _b = cv2.minMaxLoc(res)
            return bool(mx >= thresh), float(mx)

        def _check(tag: str = "") -> dict | None:
            shot = self._capture()
            if shot is None:
                return {"verdict": "SHOTFAIL", "score": 0.0, "detail": "截图失败"}
            # 保存现场截图（排查用 — 遍历每步画面落盘 logs/probe_shots/）
            try:
                import cv2
                d = pathlib.Path(__file__).parent.parent / "logs" / "probe_shots"
                d.mkdir(exist_ok=True)
                cv2.imwrite(str(d / f"{tag}_{int(time.time())}.png"), shot)
            except Exception:
                pass
            # 1) 全权委托已激活图标（能刷的权威信号 — 2026-08-12 实测 0.998）
            hit, val = _tpl("UsePrtsSuccess", 0.9)
            if hit:
                return {"verdict": VERDICT_CAN_FARM, "score": round(val, 3),
                        "detail": f"全权委托已激活（UsePrtsSuccess {val:.3f}）"}
            # 2) 锁定图标（代理锁定 → 不能刷）
            lhit, lval = _tpl("UnableToAgent2", 0.6)
            if lhit:
                return {"verdict": VERDICT_CANNOT, "score": round(lval, 3),
                        "detail": f"代理锁定（UnableToAgent2 {lval:.3f}）"}
            # 3) 详情页标志: 开始行动按钮 OCR（防列表页关卡名误匹配 —
            #    列表节点 '40-6' 也含 '-'，必须先确认在详情页）
            texts = self._ocr_texts(shot)
            joined = "|".join(t for t, _b, _s in texts)
            self._log(f"  OCR: {joined[:100]}")
            on_detail = any("开始行动" in t or "开始作战" in t or "开始推演" in t
                            for t, _b, _s in texts)
            for text, _box, score in texts:
                # 关卡名数字匹配: 含 "-" 且数字 == 目标（"TO-9"→"9"）
                if on_detail and "-" in text and num in text and len(text) <= 12:
                    return {"verdict": VERDICT_CAN_FARM, "score": round(score, 3),
                            "detail": f"详情页关卡名匹配 {base}（OCR: {text}）"}
                # 标题匹配（详情页若显示标题）: 目标标题前 3 字
                if on_detail and expected_title and expected_title[:3] in text:
                    return {"verdict": VERDICT_CAN_FARM, "score": round(score, 3),
                            "detail": f"详情页标题匹配 {expected_title}（OCR: {text}）"}
            # 4) 视觉裁决（2026-08-13 用户: 模板/OCR 信号弱或画面异常时，
            #    直接问视觉模型 — 详情页确认 + 代理可用性一眼看出。
            #    2026-08-14 审查 #2b: 恒真=每步外呼 LLM（120s 超时×12
            #    位置）→ 收紧: 仅详情页且（本轮首次 / 锁模板弱匹配 /
            #    显式强制兜底）才触发，每关最多 2 次。走到这里说明 OCR
            #    关卡名/标题未匹配 — 视觉负责确认「是不是目标关的详情页」）
            if (on_detail and (self._vision_used == 0
                               or 0.35 < lval < 0.6 or self._vision_fallback)
                    and self._vision_used < 2):
                self._vision_used += 1
                vr = _judge_vision(shot, base, diff)
                if vr:
                    return vr
            if on_detail:
                return {"verdict": VERDICT_CANNOT, "score": 0.0,
                        "detail": f"在详情页但关卡名不匹配 {base}（可能点了别的关）"}
            return None

        def _judge_vision(self, img: np.ndarray, base: str, diff: str | None) -> dict | None:
            """视觉模型裁决: 详情页确认 + 代理可用性（弱信号兜底）。

            2026-08-14 审查 #2b 修正: 必须核验「界面=详情页 + 关卡名对上
            目标」才采信 — 否则遍历时点在别的关的详情页，也会被
            "代理=可用" 误判 CAN_FARM。关卡名对不上 → 返回 None 继续遍历。
            """
            try:
                import cv2
                from services.vision import understand_image
                ok, buf = cv2.imencode(".png", img)
                if not ok:
                    return None
                q = (f"这是明日方舟游戏横屏截图。目标关卡: {base}"
                     f"{'（磨难模式）' if diff else ''}。\n"
                     f"1. 当前是什么界面？\n"
                     f"2. 若是关卡详情页：关卡名是什么？右下角「开始行动」按钮上方"
                     f"的「全权委托/代理作战」是金色可用、还是灰色带锁不可用？\n"
                     f"按格式回答: 界面=...；关卡名=...；代理=可用|不可用|不适用")
                r = understand_image(buf.tobytes(), q)
                if not r.get("ok"):
                    self._log(f"  视觉裁决失败: {r.get('error', '')[:80]}")
                    return None
                ans = r["answer"]
                self._log(f"  视觉: {ans[:100]}")
                # 核验关卡名（"TO-9" 或数字 "9" 出现在回答里）— 防点在
                # 别的关的详情页被"代理=可用"误判可刷
                _in_detail = ("界面=关卡详情页" in ans) or ("详情页" in ans)
                _name_ok = (base in ans) or (num in ans)
                if "代理=可用" in ans and _in_detail and _name_ok:
                    return {"verdict": VERDICT_CAN_FARM, "score": 0.99,
                            "detail": f"视觉判定可刷（{ans[:60]}）"}
                if ("代理=不可用" in ans or "代理=不适用" in ans) and _in_detail and _name_ok:
                    return {"verdict": VERDICT_CANNOT, "score": 0.99,
                            "detail": f"视觉判定锁定（{ans[:60]}）"}
                # 详情页 + 关卡名对上，但代理状态不明 → 详情页即可刷（兜底）
                if _in_detail and _name_ok:
                    return {"verdict": VERDICT_CAN_FARM, "score": 0.8,
                            "detail": f"视觉确认详情页（{ans[:60]}）"}
                return None  # 界面不对/关卡名不对 → 继续遍历
            except Exception as e:
                self._log(f"  视觉裁决异常: {e}")
                return None

        def _detail_gold(shot) -> float:
            x0, y0, w, h = START_BTN_ROI
            reg = shot[y0:y0 + h, x0:x0 + w]
            b = reg[:, :, 2].astype(int); r = reg[:, :, 0].astype(int); g = reg[:, :, 1].astype(int)
            return ((b < 110) & (r > 150) & (g > 90)).mean()

        # 1) 当前截图判定（MAA 可能已停在目标详情页）
        r = _check(f"{base}_init")
        if r:
            return r
        # 2) 若在详情页但关卡名不匹配 → 返回列表
        if _detail_gold(img) >= 0.10:
            self._log("  详情页但关卡名不匹配，返回列表")
            self._back()
            time.sleep(1.5)
        # 3) 列表遍历: 动态 OCR 框优先，固定网格 fallback
        shot = self._capture()
        positions = []
        if shot is not None:
            texts = self._ocr_texts(shot)
            for text, (x, y, w, h), score in texts:
                if any(k in text for k in ("TO-", "O-", "IO-", "T0-")) and 280 < y < 580:
                    positions.append((int(x + w / 2), int(y + h / 2)))
            if positions:
                self._log(f"  列表 OCR 定位 {len(positions)} 个关卡框: {positions}")
        positions = positions or GRID_POSITIONS
        self._log(f"  遍历 {len(positions)} 个位置")
        for i, (px, py) in enumerate(positions):
            if i == len(GRID_POSITIONS) // 2:
                self._swipe(*GRID_SWIPE)  # 批次 2 前横向滑动
                time.sleep(1.5)
            self._tap(px, py)
            time.sleep(2.0)
            r = _check(f"{base}_{i}")
            if r:
                return r
            self._back()
            time.sleep(1.2)
        return {"verdict": VERDICT_DONE, "score": 0.0,
                "detail": f"遍历未找到 {base}（列表不可见/关卡未开放）"}

    # ── judgement ──
    @staticmethod
    def _template_score(img: np.ndarray, tpl_path: str, roi: tuple) -> float:
        import cv2
        t = cv2.imread(tpl_path, 0)
        if t is None:
            return 0.0
        t = t.astype(np.float32)
        th, tw = t.shape
        x0, y0, w, h = roi
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        best = 0.0
        for y in range(y0, min(y0 + h, g.shape[0]) - th, 2):
            for x in range(x0, min(x0 + w, g.shape[1]) - tw, 2):
                s = g[y:y + th, x:x + tw]
                n = np.sum((s - s.mean()) * (t - t.mean()))
                d = np.sqrt(np.sum((s - s.mean()) ** 2) * np.sum((t - t.mean()) ** 2))
                c = n / max(1e-6, d)
                if c > best:
                    best = c
        return float(best)

    def judge(self, img: np.ndarray) -> dict:
        """Judge a detail-page screenshot. Returns {verdict, score, detail}."""
        tpl = (self._inst_dir() / "resource" / "template" / "Battle" / "UsePrts"
               / "UsePrts-AnnihilationSuccess.png")
        # 详情页检测: 开始行动按钮金色占比
        x0, y0, w, h = START_BTN_ROI
        reg = img[y0:y0 + h, x0:x0 + w]
        b = reg[:, :, 2].astype(int)
        r = reg[:, :, 0].astype(int)
        g = reg[:, :, 1].astype(int)
        gold = ((b < 110) & (r > 150) & (g > 90)).mean()
        if gold < 0.10:
            return {"verdict": VERDICT_DONE, "score": 0.0,
                    "detail": "非详情页（导航失败/无法前往）"}
        score = self._template_score(img, str(tpl), PRTS_ROI)
        if score >= SUCCESS_THRESHOLD:
            return {"verdict": VERDICT_CAN_FARM, "score": round(score, 3),
                    "detail": "全权委托可用（高亮图标）"}
        return {"verdict": VERDICT_CANNOT, "score": round(score, 3),
                "detail": "全权委托不可用（需先代理/未解锁）"}

    # ── main flow ──
    def probe(self, stage: str) -> dict:
        """Full probe: StartUp (game) → Fight nav → screenshot → judge."""
        from infrastructure.task_constants import find_mumu_cli
        cli = find_mumu_cli()
        idx_flag = "-v" if "MuMuManager" in cli else "--vmindex"
        try:
            r = subprocess.run([cli, "info", idx_flag, self.emu_idx],
                               capture_output=True, text=True, timeout=8,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                               encoding="utf-8", errors="replace")
            d = json.loads(r.stdout.lstrip("\ufeff").strip())
            if not (d.get("is_android_started") or d.get("is_process_started")):
                self._log(f"启动模拟器 #{self.emu_idx}...")
                subprocess.run([cli, "control", idx_flag, self.emu_idx, "launch"],
                               capture_output=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                # wait for android
                deadline = time.time() + 240
                while time.time() < deadline:
                    time.sleep(5)
                    try:
                        r2 = subprocess.run([cli, "info", idx_flag, self.emu_idx],
                                            capture_output=True, text=True, timeout=8,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                            encoding="utf-8", errors="replace")
                        d2 = json.loads(r2.stdout.lstrip("\ufeff").strip())
                        if d2.get("is_android_started") and d2.get("adb_port"):
                            break
                    except Exception:
                        pass
        except Exception as e:
            self._log(f"模拟器检查失败: {e}")

        self._log(f"StartUp (client={self.client_type})...")
        self._run_asst(stage, start_game=True)
        self._log(f"导航 {stage}...")
        self._run_asst(stage, start_game=False)
        time.sleep(2)

        img = self._capture()
        if img is None:
            return {"verdict": "SHOTFAIL", "score": 0.0, "detail": "截图失败"}
        # 普通关卡（活动关/主线）→ 标题 OCR 判定 + 网格遍历兜底
        if stage not in STAGES_ANNIHILATION:
            return self._judge_normal(img, stage)
        result = self.judge(img)
        self._log(f"判定: {result['verdict']} ({result['score']}) {result['detail']}")
        return result

    def probe_all(self, stages: list[str] | None = None) -> dict:
        """Probe multiple stages; returns {stage: verdict-dict}."""
        stages = stages or STAGES_ANNIHILATION
        out = {}
        for s in stages:
            self._log(f"--- {s} ---")
            out[s] = self.probe(s)
        return out


def run_cli() -> None:
    """Standalone CLI: python stage_probe.py <emu_idx> [client_type] [stage...]"""
    emu = sys.argv[1]
    client = sys.argv[2] if len(sys.argv) > 2 else "Official"
    stages = sys.argv[3:] or STAGES_ANNIHILATION
    probe = StageProbe(emu, client_type=client)
    results = probe.probe_all(stages)
    print("=== RESULT ===")
    for s, r in results.items():
        print(f"{s}: {r['verdict']}({r['score']})")


if __name__ == "__main__":
    run_cli()
