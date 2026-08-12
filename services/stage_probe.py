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
        code = """ + repr(NL) + """.join(lines)
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
