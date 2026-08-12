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
# 详情页标题区（720x1280 竖屏截图，右侧标题文字）
TITLE_ROI = (860, 170, 340, 150)
# 活动关卡列表网格（实测 720x1280: "直到大地变成一颗酸橙" 列表布局。
# 12 个位置 = 横向滑动前后两批。MAA OCR 识别不了列表小字关卡名
# （9→2 / 8→6 误读 → SwipeToStage 滑 50 次找不到），改为按网格 tap 进
# 详情页 → 标题 OCR 验证（详情页大字识别可靠）。
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

    # ── logging ──
    def _log(self, msg: str) -> None:
        self._log_lines.append(msg)
        print(msg, flush=True)

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
                out.append((text, sum(confs) / len(confs) if confs else 0.0))
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

    def _judge_normal(self, img: np.ndarray, stage: str) -> dict:
        """普通关卡判定: 详情页标题 OCR 匹配目标关卡（能进详情页 = 可刷）。

        导航链: MAA Fight 导航（可能停在列表/详情页）→ 标题匹配 →
        不匹配则网格遍历（tap 列表位置 → 详情页标题验证）。
        """
        import cv2
        # 详情页标志: "开始行动"按钮区域金色占比
        x0, y0, w, h = START_BTN_ROI
        reg = img[y0:y0 + h, x0:x0 + w]
        b = reg[:, :, 2].astype(int); r = reg[:, :, 0].astype(int); g = reg[:, :, 1].astype(int)
        gold = ((b < 110) & (r > 150) & (g > 90)).mean()
        titles = self._load_stage_titles()
        expected = titles.get(stage, "")
        self._log(f"普通关卡判定 {stage} (标题: {expected or '无'}, 详情页gold={gold:.2f})")

        def _check() -> dict | None:
            shot = self._capture()
            if shot is None:
                return {"verdict": "SHOTFAIL", "score": 0.0, "detail": "截图失败"}
            texts = self._ocr_texts(shot, TITLE_ROI)
            joined = "|".join(t for t, _ in texts)
            self._log(f"  标题区 OCR: {joined[:80]}")
            if expected and self._title_match(texts, expected):
                return {"verdict": VERDICT_CAN_FARM, "score": round(max(s for _, s in texts) if texts else 0, 3),
                        "detail": f"详情页标题匹配 {expected}"}
            return None

        # 1) 当前截图判定（MAA 可能已停在目标详情页）
        r = _check()
        if r:
            return r
        # 2) 若在详情页但标题不匹配 → 返回列表
        if gold >= 0.10:
            self._back()
            time.sleep(1.5)
        # 3) 网格遍历（列表 tap → 详情页标题验证）
        for i, (px, py) in enumerate(GRID_POSITIONS):
            if i == len(GRID_POSITIONS) // 2:
                self._swipe(*GRID_SWIPE)  # 批次 2 前横向滑动
                time.sleep(1.5)
            self._tap(px, py)
            time.sleep(2.0)
            r = _check()
            if r:
                return r
            self._back()
            time.sleep(1.2)
        return {"verdict": VERDICT_DONE, "score": 0.0,
                "detail": f"网格遍历未找到 {stage}（列表不可见/关卡未开放）"}

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
