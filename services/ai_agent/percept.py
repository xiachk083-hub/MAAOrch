"""感知层 — 本地零外呼的"看懂画面 + 定位元素"能力。

为什么是本地 CV 而不是视觉大模型（2026-09-14 取证）:
  · 一次完整部署手势的墙钟不可压缩部分是 1.0-1.5s（BattleUseOper.preDelay 150
    + BattleSwipeOper 400 系数/300 下限 + postDelay 200 + 注册后 200），留给
    "看+想"只有约 0.5s；VLM 关 thinking 单次仍约 4s（开 thinking 100s+）。
  · 战斗内可用的信号全是"MAA 标注的固定 ROI + 模板/OCR/ONNX"，不缺语义理解。

关于坐标（本文件相对仓库现有实现的修正点）:
  仓库里已有的模板匹配实现 —— services/stage_probe.py:339 与
  tools/screen_understand.py:125/158 —— 都把 cv2.minMaxLoc 的 max_loc 丢弃成
  `_`，只留下分数。分数能判"在不在"，判不了"点哪里"。本文件的 TemplateMatcher
  保留命中位置并换算成绝对中心坐标，这是"AI 能点中东西"的前提。
  另外 stage_probe._ocr_texts(roi=...) 返回的是**裁剪后**坐标（没加回 roi 偏移），
  本文件的 OcrEngine 统一返回绝对坐标 —— 这是个潜伏的坐标 bug，别再踩。

复用（不重写）:
  · 状态判别表与信号函数 → tools/screen_understand.py（18 状态 + 证据链）
  · OCR → ocr_tool.py（MAA 自带 PaddleOCR ONNX det+rec，零网络）
  · adb 端口真值 → infrastructure/task_constants.py（MuMuManager，禁公式端口）
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Sequence

import numpy as np

PROJ = pathlib.Path(__file__).resolve().parent.parent.parent
if str(PROJ) not in sys.path:  # 让 `import ocr_tool`（仓库根）可用
    sys.path.insert(0, str(PROJ))

WINDOW_W, WINDOW_H = 1280, 720          # MAA roi 坐标系基准，非此分辨率一律缩放归一
BLACK_FRAME_MEAN = 12.0                 # 平均亮度低于此值判为黑帧（见 AdbScreen.capture）
BLACK_FRAME_RETRIES = 2                 # 黑帧重试次数
LOG_FILE = PROJ / "logs" / "agent.log"

_log_lock = threading.Lock()


def log(msg: str) -> None:
    """落盘 + stdout —— pythonw 下没有控制台，只 print 会丢（stage_probe 的教训）。"""
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    with _log_lock:
        print(line, flush=True)
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ── MAA 任务定义（全仓共用的注册表缓存）────────────────────────────────
# 有什么用: MAA 的每个任务都带 roi（开发者手工标注的准确搜索窗口）和识别条件
# （text / template）。把这份人工标注当"地图"用，比自己全图猜位置可靠得多
# —— 实测（2026-09-14）: 小模板全图扫必然误命中；「开始行动」这种红底白字按钮
# 连本地 OCR 的检测阶段都会给出错误的框，只有"任务 roi + 文字直读"才稳。
_task_defs_cache: dict[str, dict] = {}


def _tools_path() -> None:
    """把仓库 tools/ 加入模块搜索路径。

    必须在 `import task_graph` **之前**调用 —— 踩过: 先 import 后插路径时
    ImportError 被宽 except 吞掉，resolve_task 静默返回 None，表现为"按钮定位
    全部未命中"，查半天（2026-09-14）。
    """
    tools_dir = str(PROJ / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)


def task_defs(client: str = "Official") -> dict:
    """任务注册表（国服基线 + 外服字段级覆盖）。

    MAA 的外服语义是**字段级覆盖**（外服 json 只写要改的字段，例如按钮文案
    从「开始行动」改成日文），所以不能用整体替换 —— 整体替换会把国服 roi
    一起丢掉，定位就全废了。
    """
    key = client if client in CLIENTS else "Official"
    if key not in _task_defs_cache:
        _tools_path()
        import contextlib
        import io
        import task_graph
        # load_tasks 每合并一个 json 都往 stderr 打一行（实测 ~50 行），没必要刷屏
        with contextlib.redirect_stderr(io.StringIO()):
            merged = task_graph.load_tasks(None)
        g = ResourcePaths(1, key).global_dir
        if g and (g / "tasks").exists():
            n_over = 0
            for jf in sorted((g / "tasks").rglob("*.json")):
                try:
                    data = json.loads(jf.read_text(encoding="utf-8"))
                except Exception as e:
                    log(f"外服任务覆盖解析失败 {jf.name}: {e}")
                    continue
                for name, overlay in data.items():
                    base = merged.get(name)
                    if isinstance(base, dict) and isinstance(overlay, dict):
                        merged[name] = {**base, **overlay}
                    else:
                        merged[name] = overlay
                    n_over += 1
            log(f"任务注册表: {key} 覆盖 {n_over} 项（共 {len(merged)}）")
        _task_defs_cache[key] = merged
    return _task_defs_cache[key]


def resolve_task(name: str, client: str = "Official") -> dict | None:
    """按名解析 MAA 任务（展开 baseTask / @ 继承）；不存在返回 None。"""
    _tools_path()
    try:
        import task_graph
        resolved = task_graph.resolve_task(task_defs(client), name)
    except Exception as e:
        log(f"任务定义解析失败 {name}: {e}")     # 不静默
        return None
    return resolved[0] if resolved else None


def task_roi(name: str, client: str = "Official") -> tuple | None:
    d = resolve_task(name, client)
    roi = (d or {}).get("roi")
    if isinstance(roi, list) and len(roi) == 4:
        return tuple(int(v) for v in roi)
    return None


# ── 资源路径 ───────────────────────────────────────────────────────────
CLIENTS = ("Official", "YoStarJP", "YoStarEN", "YoStarKR", "txwy")


class ResourcePaths:
    """MAA 实例的资源目录解析（分服）。

    分服是硬需求，不是可选优化: 外服资源在 resource/global/<Client>/resource/
    下**字段级覆盖**国服任务、**文件级**覆盖模板，识别模型(rec)也是分服的
    （2026-09-14 实测: 手头这台模拟器跑的是日文客户端 UI，用国服任务表会
    找不到按钮文案、用国服 rec 模型 OCR 日文全乱）。
    注意 det 模型不分服（global 下只有 rec），所以 det 一律取国服。

    另外 services/maa/instances/{2,3}/resource 是指向 source 的符号链接，
    所以"实例目录"和"源目录"实际是同一棵树；实例目录优先只是为了让实例
    自带的覆盖生效。
    """

    def __init__(self, instance: int | str = 1, client: str = "Official") -> None:
        self.instance = str(instance)
        self.client = client if client in CLIENTS else "Official"

    @property
    def maa_root(self) -> pathlib.Path:
        return PROJ / "services" / "maa"

    @property
    def resource_dir(self) -> pathlib.Path:
        inst = self.maa_root / "instances" / self.instance / "resource"
        if inst.exists():
            return inst
        return self.maa_root / "source" / "resource"

    @property
    def instance_dir(self) -> pathlib.Path:
        return self.maa_root / "instances" / self.instance

    @property
    def global_dir(self) -> pathlib.Path | None:
        """外服覆盖层目录；国服没有这一层（返回 None）。"""
        if self.client == "Official":
            return None
        d = self.resource_dir / "global" / self.client / "resource"
        return d if d.exists() else None

    @property
    def template_dir(self) -> pathlib.Path:
        return self.resource_dir / "template"

    @property
    def template_dirs(self) -> list[pathlib.Path]:
        """模板查找顺序：外服覆盖在前，国服在后（MAA 的文件级覆盖语义）。"""
        dirs = []
        g = self.global_dir
        if g and (g / "template").exists():
            dirs.append(g / "template")
        dirs.append(self.template_dir)
        return dirs

    @property
    def paddleocr_det_dir(self) -> pathlib.Path:
        return self.resource_dir / "PaddleOCR" / "det"

    @property
    def paddleocr_rec_dir(self) -> pathlib.Path:
        """识别模型分服 —— 外服有就用外服的，否则回落国服。"""
        g = self.global_dir
        if g:
            rec = g / "PaddleOCR" / "rec"
            if (rec / "inference.onnx").exists():
                return rec
        return self.resource_dir / "PaddleOCR" / "rec"

    @property
    def paddlechar_det_dir(self) -> pathlib.Path:
        """**字符模型**（数字/字母专用）的 det。MAA 自己读数字时切的就是这套
        （`tasks.json` 里 `BattleKills.isAscii: true` ⇒ `set_use_char_model(true)`），
        我们以前一直只用通用模型 ⇒ 汉字读得像、数字全崩（"7"→Z、"0"→盛，2026-09-19 实测）。"""
        return self.resource_dir / "PaddleCharOCR" / "det"

    @property
    def paddlechar_rec_dir(self) -> pathlib.Path:
        return self.resource_dir / "PaddleCharOCR" / "rec"

    @property
    def onnx_dir(self) -> pathlib.Path:
        return self.resource_dir / "onnx"

    @property
    def tile_pos_dir(self) -> pathlib.Path:
        return self.resource_dir / "Arknights-Tile-Pos"

    @property
    def battle_data(self) -> pathlib.Path:
        return self.resource_dir / "battle_data.json"


# ── 命中结果 ───────────────────────────────────────────────────────────
@dataclass
class Hit:
    """一次定位结果。坐标一律是 1280x720 基准下的**绝对**像素。"""

    kind: str                            # ocr | template | coord
    label: str
    box: tuple[int, int, int, int]       # x, y, w, h
    score: float
    image: np.ndarray | None = None      # 命中区域裁剪（可选，便于调试/送模型）

    @property
    def center(self) -> tuple[int, int]:
        x, y, w, h = self.box
        return (x + w // 2, y + h // 2)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label,
                "box": list(self.box), "score": round(self.score, 3),
                "center": list(self.center)}


# ── 截图源 ─────────────────────────────────────────────────────────────
class AdbScreen:
    """adb exec-out screencap 取图（MAA 生态里最稳的取图方式，无外部依赖）。

    并发保护: screencap 会抢 adb 通道拖慢同时运行的 MAA，多个 agent 同时
    截图时必须串行（api_fastapi.py 里已有同样的全局锁先例）。
    """

    _lock = threading.Lock()

    def __init__(self, emu_idx, adb_path: str | None = None,
                 adb_address: str | None = None, timeout: float = 15.0) -> None:
        self.emu_idx = str(emu_idx)
        self.timeout = timeout
        self._adb_path = adb_path
        self._adb_address = adb_address
        self._connected_at = 0.0

    def ensure_connected(self, force: bool = False) -> bool:
        """确保这台设备已连到 adb server。

        必须做：模拟器刚启动时 adb server 里没有它，`adb -s <addr> exec-out
        screencap` 直接失败（2026-09-14 实测：刚启动 #1 时截图报错，手动
        `adb connect` 立刻恢复；MAA 自己也是先 connect 再操作）。
        60s 内不重复探测，避免每帧都跑一次 `adb devices`。
        """
        now = time.time()
        if not force and self._connected_at and now - self._connected_at < 60:
            return True
        if not self.adb_address:
            return False
        try:
            out = self._run(["devices"]).stdout.decode("utf-8", "replace")
        except Exception as e:
            log(f"adb devices 失败: {e}")
            return False
        if self.adb_address in out:
            self._connected_at = now
            return True
        log(f"设备 {self.adb_address} 未连上 adb server，执行 adb connect")
        try:
            r = self._run(["connect", self.adb_address])
            log("  adb connect → " + r.stdout.decode("utf-8", "replace").strip()[:80])
        except Exception as e:
            log(f"  adb connect 失败: {e}")
            return False
        self._connected_at = now
        return True

    @property
    def adb_path(self) -> str:
        if self._adb_path is None:
            from infrastructure.task_constants import find_adb
            self._adb_path = find_adb() or "adb"
        return self._adb_path

    @property
    def adb_address(self) -> str:
        if self._adb_address is None:
            from infrastructure.task_constants import detect_emu_adb
            self._adb_address = detect_emu_adb(self.emu_idx) or ""
        return self._adb_address

    def _run(self, args: Sequence[str]) -> subprocess.CompletedProcess:
        return subprocess.run([self.adb_path, "-s", self.adb_address, *args],
                              capture_output=True, timeout=self.timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def run_shell(self, args: Sequence[str]) -> subprocess.CompletedProcess:
        """执行 adb shell 子命令（tap/swipe/keyevent 等输入动作）。

        与截图共用同一把锁：截屏和输入抢同一条 adb 通道，串行化避免
        "点了但截到旧帧"这类时序错乱。
        """
        with self._lock:
            return self._run(["shell", *args])

    def capture(self) -> np.ndarray | None:
        import cv2
        if not self.adb_address:
            log(f"截图失败: 模拟器 #{self.emu_idx} 的 adb 端口未取到（MuMuManager 真值）")
            return None
        self.ensure_connected()
        with self._lock:
            try:
                r = self._run(["exec-out", "screencap", "-p"])
            except Exception as e:
                log(f"截图异常: {e}")
                return None
        if r.returncode != 0 or len(r.stdout) < 1000:
            # 设备掉线（重启模拟器/换机）时强制重连一次再试
            if self.ensure_connected(force=True):
                with self._lock:
                    try:
                        r = self._run(["exec-out", "screencap", "-p"])
                    except Exception:
                        return None
                if r.returncode == 0 and len(r.stdout) >= 1000:
                    img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
                    if img is not None and float(img.mean()) > BLACK_FRAME_MEAN:
                        return normalize(img)
            return None
        img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
        # 黑帧检测 + 重试：screencap 会间歇性返回全黑图（实测约 2/10，且集中在刚发完
        # input 之后）。黑帧是**合法 PNG**（4KB+），过得了上面那条长度判据，于是被当
        # 好帧喂给识别层 —— YOLO 会数出 0 个敌人、干员检测也会漏，属于静默错误
        # （判据看着都对，结论却错）。这里的实测数据来自 Arknight-Agent ADB 改造。
        for attempt in range(BLACK_FRAME_RETRIES):
            if img is not None and float(img.mean()) > BLACK_FRAME_MEAN:
                break
            log(f"截图返回黑帧（第 {attempt + 1} 次重试）")
            time.sleep(0.25)
            with self._lock:
                try:
                    r = self._run(["exec-out", "screencap", "-p"])
                except Exception:
                    break
            if r.returncode != 0 or len(r.stdout) < 1000:
                continue
            img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
        if img is None or float(img.mean()) <= BLACK_FRAME_MEAN:
            # 重试仍是黑帧 → 返回 None（**不要**把黑帧当结果返回：调用侧对 None 有
            # 失败分支，而黑帧会被继续喂进识别层，产出"0 个敌人"这类静默错结论）
            log(f"截图连续 {BLACK_FRAME_RETRIES + 1} 次拿到黑帧，放弃本次抓帧")
            return None
        return normalize(img)


class ImageScreen:
    """离线截图源 —— 固定返回同一张图，供离线回归/单测（不打模拟器）。"""

    def __init__(self, source) -> None:
        if isinstance(source, np.ndarray):
            self._img = normalize(source)
            self.source = "<ndarray>"
        else:
            self._img = normalize(imread(source))
            if self._img is None:
                raise FileNotFoundError(f"读不到截图: {source}")
            self.source = str(source)

    def capture(self) -> np.ndarray | None:
        return self._img


def imread(path) -> np.ndarray | None:
    """读图（Windows 中文路径安全）。

    cv2.imread 在 Windows 上遇到非 ASCII 路径会静默返回 None —— 仓库里
    大量 `_r_代理.png` 这类中文文件名，离线回归因此丢样本。
    np.fromfile + imdecode 绕开路径编码问题。
    """
    import cv2
    try:
        buf = np.fromfile(str(path), dtype=np.uint8)
        if buf.size == 0:
            return None
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception:
        return None


def normalize(img: np.ndarray | None) -> np.ndarray | None:
    """统一到 1280x720 —— MAA 的所有 roi 都以此为基准。"""
    if img is None:
        return None
    import cv2
    h, w = img.shape[:2]
    if (w, h) != (WINDOW_W, WINDOW_H):
        img = cv2.resize(img, (WINDOW_W, WINDOW_H))
    return img


# ── OCR ───────────────────────────────────────────────────────────────
class OcrEngine:
    """MAA 自带 PaddleOCR ONNX 的薄封装。惰性加载会话（两个模型 ~13MB）。

    返回坐标是**绝对**坐标：给了 roi 也会加回偏移。det 输入要求 32 对齐
    （8 对齐会在 fastdeploy 导出的模型里 p2o.Add 广播崩溃 —— ocr_tool 的注释）。
    """

    def __init__(self, paths: ResourcePaths | None = None,
                 templates: "TemplateMatcher | None" = None) -> None:
        self.paths = paths or ResourcePaths()
        self._templates = templates or TemplateMatcher(self.paths)   # 模板型任务要用
        self._det = None
        self._rec = None
        self._keys: list[str] | None = None
        self._char = None            # 字符模型（数字/字母）的三件套，用时才加载

    def _sessions(self, char: bool = False):
        """char=True ⇒ 用 **PaddleCharOCR**（数字/字母专用），MAA 读数字走的就是它。"""
        import onnxruntime as ort
        if char:
            if self._char is None:
                from ocr_tool import load_keys
                cdet = self.paths.paddlechar_det_dir / "inference.onnx"
                crec = self.paths.paddlechar_rec_dir / "inference.onnx"
                self._char = (
                    ort.InferenceSession(str(cdet), providers=["CPUExecutionProvider"]),
                    ort.InferenceSession(str(crec), providers=["CPUExecutionProvider"]),
                    load_keys(str(self.paths.paddlechar_rec_dir / "keys.txt")))
            return self._char
        if self._det is None:
            # det 不分服（global 下只有 rec），rec 分服 —— 用错 rec 模型会让
            # 日文/英文界面 OCR 全乱（2026-09-14 实测）
            self._det = ort.InferenceSession(
                str(self.paths.paddleocr_det_dir / "inference.onnx"),
                providers=["CPUExecutionProvider"])
            self._rec = ort.InferenceSession(
                str(self.paths.paddleocr_rec_dir / "inference.onnx"),
                providers=["CPUExecutionProvider"])
            from ocr_tool import load_keys
            self._keys = load_keys(str(self.paths.paddleocr_rec_dir / "keys.txt"))
        return self._det, self._rec, self._keys

    def read_region(self, img: np.ndarray, box: tuple, upscale: float = 2.0,
                    min_score: float = 0.3, char: bool = False) -> Hit | None:
        """直读一个**已知框**里的文字（跳过检测阶段）。

        为什么必须有它（2026-09-14 实测）: 本地 PaddleOCR 的检测阶段在"红底白字"
        这类游戏按钮上给出的框是错的 —— 「开始行动」只被框到下半条 86x11，识别成
        "后丁"；把框对准后同图能读出「开始行动」0.98~1.00。试过 6 种预处理
        （raw/gray/三档二值化/RGB）× 3 档放大，全都没救回检测框。
        MAA 自己的按钮识别走的就是"任务 roi 定框 + OCR"这条路，不靠全图检测。
        """
        import cv2
        x, y, w, h = (int(v) for v in box)
        crop = img[max(0, y):y + h, max(0, x):x + w]
        if crop.size == 0:
            return None
        if upscale and upscale != 1.0:
            crop = cv2.resize(crop, None, fx=upscale, fy=upscale,
                              interpolation=cv2.INTER_CUBIC)
        from ocr_tool import rec_text
        _, rec, keys = self._sessions(char)
        text, score = rec_text(crop, (0, 0, crop.shape[1], crop.shape[0]), rec, keys)
        if not text or score < min_score:
            return None
        return Hit(kind="ocr", label=text, box=(x, y, w, h), score=float(score))

    def texts(self, img: np.ndarray, roi: tuple | None = None,
              min_score: float = 0.3, char: bool = False) -> list[Hit]:
        """识别文字并返回带绝对坐标的命中列表（走检测阶段）。char=True 用字符模型。"""
        from ocr_tool import det_text_regions, rec_text
        det, rec, keys = self._sessions(char)
        ox = oy = 0
        crop = img
        if roi:
            ox, oy, rw, rh = roi
            crop = img[oy:oy + rh, ox:ox + rw]
        out: list[Hit] = []
        for reg in det_text_regions(crop, det):
            text, score = rec_text(crop, reg, rec, keys)
            if not text or score < min_score:
                continue
            x, y, w, h = reg
            box = (int(x + ox), int(y + oy), int(w), int(h))
            out.append(Hit(kind="ocr", label=text, box=box, score=float(score)))
        return out

    def find_text(self, img: np.ndarray, needle: str, roi: tuple | None = None,
                  contains: bool = True, min_score: float = 0.3,
                  direct: bool = True) -> Hit | None:
        """按文字定位。给了 roi 就先"直读整块 roi"，再退回检测。

        直读优先是因为按钮类文字的检测框不可靠（见 read_region）。返回的 center
        是 roi 中心 —— 点按钮时这正是想要的位置。
        """
        def _match(hit: Hit | None) -> Hit | None:
            if hit is None:
                return None
            ok = (needle in hit.label) if contains else (needle == hit.label)
            return hit if ok else None

        if roi and direct:
            hit = _match(self.read_region(img, roi, min_score=min_score))
            if hit:
                return hit
        best: Hit | None = None
        for hit in self.texts(img, roi=roi, min_score=min_score):
            if _match(hit) and (best is None or hit.score > best.score):
                best = hit
        return best

    def find_task_text(self, img: np.ndarray, task_name: str,
                       min_score: float = 0.3) -> Hit | None:
        """用 MAA 任务定义定位按钮：取任务的 roi + text 列表，直读 roi 里的文字。

        这是"人工标注当定位器"的落点 —— 例如 find_task_text(img, "StartButton1")
        就是"点开始行动"，不依赖我们自己去猜按钮在哪。

        roi 比文字大 2-4 倍（MAA 的 roi 是搜索窗口），整块直读会被背景干扰，
        所以按"整块 → 居中 0.7 → 居中 0.45"依次尝试，命中任一期望文案就返回。
        """
        d = resolve_task(task_name, self.paths.client)
        if not d:
            return None
        roi = d.get("roi")
        want = d.get("text") or []
        if not (isinstance(roi, list) and len(roi) == 4):
            return None
        # 模板型任务（没有 text 列表，例如 UsePrts / BattleOfficiallyBegin）必须
        # 走模板匹配 —— 否则对空 want 会把 roi 里读到的任何文字当成命中，
        # 实测在战斗画面上把日文误认成「全权委托」（2026-09-14）。
        if not want:
            # MAA 缺省规则: 未写 template 就用 "<任务名>.png"
            # （SkipThePreBattlePlot 这类任务就是这样，漏了这条会定位不到）
            tpls = d.get("template") or (task_name + ".png")
            if tpls:
                if isinstance(tpls, str):
                    tpls = [tpls]
                for t in tpls:
                    if t and t != "empty.png":
                        hit = self._templates.find(img, t, roi=tuple(int(v) for v in roi))
                        if hit:
                            return hit
            return None
        x, y, w, h = (int(v) for v in roi)
        cands = [(x, y, w, h)]
        for f in (0.7, 0.45):
            cw, ch = int(w * f), int(h * f)
            cands.append((x + (w - cw) // 2, y + (h - ch) // 2, cw, ch))
        for box in cands:
            hit = self.read_region(img, box, min_score=min_score)
            if hit and any(t in hit.label or hit.label in t for t in want):
                return hit
        return None


# ── 模板匹配（带坐标） ──────────────────────────────────────────────────
class TemplateMatcher:
    """MAA 模板库匹配，**保留命中位置**。

    模板名规则与 MAA 一致：给"任务名"就去 template/ 下递归找 <任务名>.png；
    也接受相对路径（如 "Battle/UsePrts/UsePrts.png"）或绝对路径。
    maskRange 的语义与 MAA 对齐：只让落在灰度区间内的像素参与匹配。
    """

    def __init__(self, paths: ResourcePaths | None = None) -> None:
        self.paths = paths or ResourcePaths()
        self._warned: set[str] = set()
        self._tpl_cache: dict[str, "np.ndarray | None"] = {}   # 模板图缓存（扫描任务库时必需）

    def resolve(self, name: str) -> pathlib.Path | None:
        """模板查找：外服覆盖目录优先，再国服（MAA 的文件级覆盖语义）。

        两种输入都要支持:
          · 模板文件名（"BattleSkillReady" → BattleSkillReady.png）
          · **任务名**（"Fight" → 任务定义里的 template）
        第二种必须走任务定义 —— 例如 task Fight 的模板是
        `SwitchTheme@ToggleSettingsMenu.png`（MAA 的主题模板机制），根本没有
        Fight.png；只看文件名会得到"模板不存在"，而状态判别却能命中 1.00，
        看起来像随机故障（2026-09-14 实测）。
        """
        hit = self._find_file(name)
        if hit:
            return hit
        d = resolve_task(name, self.paths.client)
        tpls = (d or {}).get("template")
        if isinstance(tpls, str):
            tpls = [tpls]
        for t in (tpls or []):
            if t and t != "empty.png":
                hit = self._find_file(t)
                if hit:
                    return hit
        return None

    def _find_file(self, name: str) -> pathlib.Path | None:
        import pathlib as _pl
        p = _pl.Path(name)
        if p.is_absolute() and p.exists():
            return p
        fname = name if name.endswith(".png") else name + ".png"
        for base in self.paths.template_dirs:
            cand = base / name
            if cand.exists():
                return cand
            cand = base / fname
            if cand.exists():
                return cand
            found = sorted(base.rglob(fname))
            if found:
                return found[0]
        return None

    def find(self, img: np.ndarray, name: str, roi: tuple | None = None,
             threshold: float | None = None, mask_range: tuple | None = None,
             scales: Sequence[float] = (1.0,), use_task_roi: bool = True) -> Hit | None:
        """返回得分最高的一个命中（含绝对中心坐标）；未命中返回 None。"""
        hits = self.find_all(img, name, roi=roi, threshold=threshold,
                             mask_range=mask_range, scales=scales, max_hits=1,
                             use_task_roi=use_task_roi)
        return hits[0] if hits else None

    def find_all(self, img: np.ndarray, name: str, roi: tuple | None = None,
                 threshold: float | None = None, mask_range: tuple | None = None,
                 scales: Sequence[float] = (1.0,), max_hits: int = 20,
                 nms_iou: float = 0.3, use_task_roi: bool = True) -> list[Hit]:
        """多命中 + NMS —— 关卡列表那种"一排同型按钮"靠它。"""
        import cv2
        tpl_path = self.resolve(name)
        if tpl_path is None:
            if name not in self._warned:
                self._warned.add(name)
                log(f"模板不存在，跳过: {name}")
            return []
        key = str(tpl_path)
        if key not in self._tpl_cache:
            self._tpl_cache[key] = cv2.imread(str(tpl_path), cv2.IMREAD_GRAYSCALE)
        tpl0 = self._tpl_cache[key]
        if tpl0 is None:
            return []
        if roi is None and use_task_roi:
            roi = task_roi(name, self.paths.client)   # MAA 自己就是这么限定搜索窗口的
        thr = 0.8 if threshold is None else float(threshold)   # MAA templThreshold 默认 0.8
        ox = oy = 0
        region = img
        if roi:
            ox, oy, rw, rh = roi
            ih, iw = img.shape[:2]
            region = img[oy:min(oy + rh, ih), ox:min(ox + rw, iw)]
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        if mask_range:
            lo, hi = int(mask_range[0]), int(mask_range[1])
            gray = np.where((gray >= lo) & (gray <= hi), gray, 0).astype(np.uint8)
        cands: list[tuple[float, tuple[int, int, int, int]]] = []
        for s in scales:
            tpl = tpl0 if s == 1.0 else cv2.resize(
                tpl0, (max(1, int(tpl0.shape[1] * s)), max(1, int(tpl0.shape[0] * s))))
            th, tw = tpl.shape
            if th > gray.shape[0] or tw > gray.shape[1]:
                continue
            res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res >= thr)
            for y, x in zip(ys, xs):
                cands.append((float(res[y, x]), (int(x + ox), int(y + oy), int(tw), int(th))))
        cands.sort(key=lambda c: -c[0])
        kept: list[tuple[float, tuple[int, int, int, int]]] = []
        for score, box in cands:
            if all(_iou(box, kb) <= nms_iou for _s, kb in kept):
                kept.append((score, box))
            if len(kept) >= max_hits:
                break
        return [Hit(kind="template", label=name, box=b, score=s,
                    image=_crop(region, b, (ox, oy))) for s, b in kept]


def _iou(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _crop(region: np.ndarray, box: tuple, offset: tuple) -> np.ndarray | None:
    x, y, w, h = box
    ox, oy = offset
    try:
        sub = region[max(0, y - oy):y - oy + h, max(0, x - ox):x - ox + w]
        return sub if sub.size else None
    except Exception:
        return None


# ── 状态判别 ───────────────────────────────────────────────────────────
class StateClassifier:
    """画面 → 语义状态。复用 tools/screen_understand.py 的状态表与信号函数。

    只复用**信号函数**（_template_check / _theme_check / _gold_check /
    _gold_block_check / STATE_CHECKS），判定循环在本类里重写：原版
    understand() 只收图片路径、且自己做一次 OCR，不适合按帧调用的热路径。

    tools/ 会被插进 sys.path（screen_understand 的 import 副作用），所以
    这里改成惰性导入 —— 不用状态判别时不影响进程的模块搜索路径。
    """

    def __init__(self, client: str = "Official") -> None:
        self.client = client if client in CLIENTS else "Official"
        self._mod = None
        self._ocr_sess = None

    def _ocr_session(self):
        """借一个 OCR 会话（暂停检测用；本类平时不持 OCR）。"""
        if getattr(self, "_ocr_sess", None) is None:
            import onnxruntime as ort
            from ocr_tool import load_keys
            paths = ResourcePaths()
            self._ocr_sess = (
                ort.InferenceSession(str(paths.paddleocr_det_dir / "inference.onnx"),
                                     providers=["CPUExecutionProvider"]),
                ort.InferenceSession(str(paths.paddleocr_rec_dir / "inference.onnx"),
                                     providers=["CPUExecutionProvider"]),
                load_keys(str(paths.paddleocr_rec_dir / "keys.txt")),
            )
        return self._ocr_sess

    def _su(self):
        if self._mod is None:
            tools_dir = str(PROJ / "tools")
            if tools_dir not in sys.path:
                sys.path.insert(0, tools_dir)
            import screen_understand as su
            # 共享任务注册表缓存：否则它自己会再 load 一次，而且 load_tasks 每合并
            # 一个 json 就往 stderr 打一行（实测刷屏 ~50 行）
            # 客户端相关的 roi/阈值要通过注册表传进去（外服字段级覆盖）
            su._tasks_cache = task_defs(self.client)
            self._mod = su
        return self._mod

    def classify(self, img: np.ndarray, ocr_texts: list[str] | None = None) -> dict:
        """默认不用 OCR（0.35s/张）；给了 ocr_texts 就会把 OCR 信号也算进去。

        返回 {verdict, score, detail, candidates:[{state,score,evidence}]}
        """
        import cv2
        su = self._su()
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        texts = ocr_texts or []
        results = []
        for state, checks in su.STATE_CHECKS.items():
            score = 0.0
            evidence = []
            for check in checks:
                kind = check[0]
                if kind == "模板":
                    hit, val = su._template_check(gray, check[1], check[2])
                    if hit:
                        score += 1.0
                    evidence.append(f"{'✓' if hit else '✗'} 模板 {check[1]} {val:.2f}")
                elif kind == "主题":
                    hit, val = su._theme_check(gray, check[1], check[2], check[3])
                    if hit:
                        score += 1.0
                    evidence.append(f"{'✓' if hit else '✗'} 主题 {check[1]} {val:.2f}")
                elif kind == "颜色金":
                    hit, val = su._gold_check(img, check[1], check[2])
                    if hit:
                        score += 0.8
                    evidence.append(f"{'✓' if hit else '✗'} 金色 {val:.2f}")
                elif kind == "颜色金任务":
                    hit, val = su._gold_task_check(img, check[1], check[2])
                    if hit:
                        score += 0.8
                    evidence.append(f"{'✓' if hit else '✗'} 金色(任务roi) {val:.2f}")
                elif kind == "金色块":
                    hit, val = su._gold_block_check(img, check[1])
                    if hit:
                        score += 0.8
                    evidence.append(f"{'✓' if hit else '✗'} 金色块 {val:.2f}")
                elif kind == "OCR":
                    if texts:
                        hit = any(any(t in t2 for t in check[1]) for t2 in texts)
                        if hit:
                            score += 1.0
                        evidence.append(f"{'✓' if hit else '✗'} OCR {check[1]}")
                    else:
                        evidence.append("· OCR 跳过")
            if score > 0:
                results.append({"state": state, "score": round(score, 2),
                                "evidence": evidence})
        results.sort(key=lambda r: -r["score"])
        if not results:
            return {"verdict": "未知界面", "score": 0.0,
                    "detail": "没有任何已知状态信号命中——人工确认，不要死磕",
                    "candidates": []}
        # 18 状态表里没有"编队页"（MAA 用 BattleQuickFormation 模板判它），
        # 而它正是"开战前最后一屏"—— 缺了它，agent 在编队页会以为自己
        # 在关卡列表页（2026-09-14 实测），后续决策全错。
        _su = self._su()
        # 暂停检测: 战斗中按到暂停（例如脚本误按返回键）会显示 PAUSE/暂停中 遮罩，
        # 此时既不能部署、费用也不涨 —— 实测因此把四次部署手势全测成了"无效"
        # （2026-09-14）。中央那行大字 OCR 能读出来，用它判。
        import cv2 as _cv2
        _c = gray[280:400, 380:900]
        _t = []
        try:
            from ocr_tool import det_text_regions, rec_text
            _det, _rec, _keys = self._ocr_session()
            for _r in det_text_regions(_cv2.cvtColor(_c, _cv2.COLOR_GRAY2BGR), _det):
                _x, _sc = rec_text(_cv2.cvtColor(_c, _cv2.COLOR_GRAY2BGR), _r, _rec, _keys)
                if _x:
                    _t.append((_x, _sc))
        except Exception as _e:
            log(f"暂停检测 OCR 失败: {_e}")
        if any("暂停" in x for x, _ in _t) or any("PAUSE" in x.upper() for x, _ in _t):
            return {"verdict": "战斗中（已暂停）", "score": 1.0,
                    "detail": f"中央出现暂停遮罩 {_t[:2]} —— 必须恢复再操作",
                    "candidates": results[:4]}
        # 暗色遮罩检测: 模态弹窗（登录失效/掉线/未知确认框）会把整屏压暗，
        # 但**模糊掉的战斗背景仍能让 PRTS1 模板命中** —— 实测登录失效弹窗被
        # 判成"战斗中"，硬规则于是"安心地"瞎点了一整轮（2026-09-14）。
        # 阈值实测：弹窗帧 偏暗像素占比 0.744，正常战斗 0.54-0.56。
        import numpy as _np
        g = _np.asarray(gray)
        dim_ratio = float((g < 70).mean())
        # 只靠"偏暗"会误伤本来就暗的界面（关卡列表页偏暗占比 0.694 —— 实测
        # 因此被误判成弹窗，"恢复"逻辑把导航带到了 14 章列表）。
        # 真弹窗的判据是**中央面板比边缘暗**（弹窗盖住中间、边缘露出被压暗的场景）：
        #   登录失效弹窗 比值 0.75 ；战斗 1.05 / 关卡列表 1.75 / 主界面 1.36 / 终端页 0.88
        center = float(g[150:550, 300:980].mean())
        edge = float(_np.concatenate([g[:120].ravel(), g[600:].ravel(),
                                      g[:, :260].ravel(), g[:, 1020:].ravel()]).mean())
        ratio = center / max(edge, 1.0)
        base_state = results[0]["state"] if results else ""
        # 只对这两种误判最危险的状态生效：失败页/结算页本身也可能偏暗，
        # 被遮罩检测抢先判成"弹窗"会让 agent 连"打完了"都不知道（2026-09-14）
        if dim_ratio > 0.60 and ratio < 0.85 and (
                "战斗" in base_state or "详情页" in base_state):
            top_state = results[0]["state"] if results else ""
            return {"verdict": f"疑似弹窗遮罩（原本判为「{top_state}」）",
                    "score": 1.0,
                    "detail": f"偏暗 {dim_ratio:.2f} 且中央比边缘暗（比值 {ratio:.2f}）"
                              f"—— 极可能是模态弹窗，不要按原状态行动；交给视觉确认弹窗内容与按钮",
                    "candidates": results[:4]}
        # 登录失效必须在最前面判 —— 这一屏长得像"未知界面"，今晚因此瞎点了
        # 一整轮（2026-09-14）；真相是账号会话被别处顶掉了，游戏已不接收操作。
        for needle in ("请重新登录", "登录认证已失效", "重新登录"):
            if any(needle in t for t in (texts or [])):
                return {"verdict": "登录失效（需重新登录）", "score": 1.0,
                        "detail": f"OCR 命中「{needle}」", "candidates": results[:4]}
        # 注意: 不要用 TerminalDefault 主题模板判"终端页" —— 实测它是
        # **主界面上的终端卡片**的模板（主界面会被误标成终端页）。
        # 终端页（两张 MAIN THEME 卡那一屏）没有专属主题锚点，导航时用
        # 内容判定（OCR 文案）比状态名可靠。
        plot_hit, plot_val = _su._template_check(gray, "SkipThePreBattlePlot", 0.8)
        if plot_hit:
            return {"verdict": "剧情页（可跳过）", "score": 1.0,
                    "detail": f"SkipThePreBattlePlot 模板 {plot_val:.2f}",
                    "candidates": results[:4]}
        qf_hit, qf_val = _su._template_check(gray, "BattleQuickFormation", 0.8)
        if qf_hit:
            return {"verdict": "编队页", "score": 1.0,
                    "detail": f"BattleQuickFormation 模板 {qf_val:.2f}",
                    "candidates": results[:4]}
        top = results[0]
        verdict = top["state"]
        detail = "；".join(top["evidence"][:4])
        if verdict == "关卡详情页":
            verdict, extra = self._refine_detail(img, gray, texts)
            detail += "；" + extra
        return {"verdict": verdict, "score": top["score"], "detail": detail,
                "candidates": results[:4]}

    def _refine_detail(self, img: np.ndarray, gray: np.ndarray,
                       texts: list[str] | None = None) -> tuple[str, str]:
        """详情页细分 —— 只认硬信号。

        硬信号只有两个（MAA 自己也是这么判的）: 全权委托图标（UsePrtsSuccessCheck
        已激活 / UnableToAgent2 锁定）与「开始行动/开始作战/开始推演」文案。
        **金色块不是硬信号**：离线回归实测（2026-09-14，331 张）主界面与肉鸽局内
        都有大片金色，靠金色块兜底会把 45 张非详情页判成"详情页 → 可刷"，
        而 AI 一旦信了就会去点一个不存在的"开始行动"。
        """
        su = self._su()
        zones = [(877, 532, 183, 123), (1010, 625, 260, 61), (960, 360, 160, 140)]
        zone_vals = [su._gold_check(img, z, 0.05)[1] for z in zones]
        prts_hit, prts_val = su._template_check(gray, "UsePrtsSuccessCheck", 0.9)
        locked_hit, locked_val = su._template_check(gray, "UnableToAgent2", 0.6)
        action = any(("开始行动" in t) or ("开始作战" in t) or ("开始推演" in t)
                     for t in (texts or []))
        if prts_hit:
            return "关卡详情页 → 可刷", f"全权委托已激活 {prts_val:.2f}"
        if locked_hit:
            return "关卡详情页 → 不可刷（代理锁定）", f"锁图标 {locked_val:.2f}"
        if action:
            return "关卡详情页 → 可刷（OCR 到开始行动）", "开始行动文案"
        if max(zone_vals) >= 0.05:
            return ("疑似关卡详情页",
                    f"标志区金色 {[round(v, 3) for v in zone_vals]}，无全权委托图标/开始行动文案")
        return "关卡列表页", f"标志区无金色按钮 {[round(v, 3) for v in zone_vals]}"


# ── 统一目标解析（AI 说的"点这个" → 实际像素） ──────────────────────────
class Perception:
    """一次捕获、多处复用：状态 + OCR + 模板 + 目标解析。"""

    def __init__(self, screen, paths: ResourcePaths | None = None,
                 ocr: OcrEngine | None = None, templates: TemplateMatcher | None = None,
                 states: StateClassifier | None = None) -> None:
        self.paths = paths or ResourcePaths()
        self.screen = screen
        self.templates = templates or TemplateMatcher(self.paths)
        self.ocr = ocr or OcrEngine(self.paths, self.templates)
        self.states = states or StateClassifier()

    @classmethod
    def for_emu(cls, emu_idx, instance: int | str = 1, client: str = "Official",
                **kw) -> "Perception":
        paths = ResourcePaths(instance, client)
        return cls(AdbScreen(emu_idx), paths=paths, states=StateClassifier(client), **kw)

    @classmethod
    def offline(cls, source, instance: int | str = 1, client: str = "Official",
                **kw) -> "Perception":
        """离线：从一张图/一个文件建感知（离线回归用，不连模拟器）。"""
        paths = ResourcePaths(instance, client)
        return cls(ImageScreen(source), paths=paths, states=StateClassifier(client), **kw)

    # 采集
    def shot(self) -> np.ndarray:
        img = self.screen.capture()
        if img is None:
            raise RuntimeError("截图失败（见 logs/agent.log）")
        return img

    # 感知
    def is_frozen(self, samples: int = 3, interval: float = 1.2,
                  threshold: float = 0.0015) -> bool:
        """画面是否完全静止 —— 判断"战斗被暂停"的最可靠信号。

        为什么不用 OCR: 暂停遮罩上那行大字（PAUSE/暂停中）本地 OCR 完全读不出
        （1x/2x 放大都试过），而**暂停时整个画面没有任何动画**，帧差恒为 0；
        正常战斗里敌人走动、费用跳动，帧差不可能为 0。
        """
        import cv2
        import numpy as np
        prev = None
        diffs = []
        for _ in range(max(2, samples)):
            img = self.shot()
            g = cv2.cvtColor(cv2.resize(img, (320, 180)), cv2.COLOR_BGR2GRAY).astype(np.float32)
            if prev is not None:
                diffs.append(float(np.abs(g - prev).mean() / 255.0))
            prev = g
            if len(diffs) >= samples - 1:
                break
            import time as _t
            _t.sleep(interval)
        if not diffs:
            return False
        return max(diffs) < threshold

    def is_in_battle(self) -> bool:
        """**是否真的在战斗里** —— 交给视觉裁决。

        为什么不能信本地状态: 关卡选择地图/失败页/登录弹窗这些暗色界面都会让
        战斗模板误命中，本地判别在实测里把它们全判成了"战斗中"，导致测试器
        在"第 12 章关卡地图"上煞有介事地找干员卡（2026-09-14）。
        判据: 是否能看到部署费用数字 + 底部干员卡 + 战场。
        """
        from services.ai_agent.llm import LLM
        client = LLM()
        if not client.vision_ready:
            return "战斗中" in self.state(self.shot(), use_ocr=True)["verdict"]
        r = client.ask_json(
            '你在看明日方舟截图。只输出 JSON: {"in_battle": true/false, "what": "画面是什么"}。'
            'in_battle=true 只有当画面是**战斗进行中**（能看到右下角部署费用数字、'
            '底部一排干员卡、中间战场地图）时才成立；关卡选择地图、结算/失败页、'
            '主界面、加载页都算 false。',
            "现在是战斗进行中吗？", image=self.shot(), vision=True, max_tokens=8000)
        if not r.get("ok"):
            return False
        ok = bool((r.get("json") or {}).get("in_battle"))
        log(f"是否在战斗（视觉）: {ok} | {str((r.get('json') or {}).get('what'))[:60]}")
        return ok

    def battle_over(self) -> bool:
        """战斗是否已结束（失败/结算）。**必须先判这个再判暂停** ——
        结束画面同样完全静止，用 is_frozen 会把它当成暂停去点恢复（2026-09-14）。

        本地状态判不出来：失败页背景是**模糊掉的战场**，PRTS1 模板照样命中，
        被判成"战斗中"（和登录失效弹窗同一个坑）。所以交给视觉裁决 ——
        这一步只在画面静止时调用，成本可接受。
        """
        st = self.state(self.shot(), use_ocr=True)["verdict"]
        if any(k in st for k in ("失败页", "结算页", "任务失败")):
            return True
        if "战斗" not in st:
            return False
        from services.ai_agent.llm import LLM
        client = LLM()
        if not client.vision_ready:
            return False
        import json as _json
        r = client.ask_json(
            '你在看明日方舟截图。只输出 JSON: {"over": true/false, "what": "画面是什么"}。'
            'over=true 表示战斗已结束（任务失败 MISSION FAILED / 行动结束 MISSION RESULTS / 结算画面）；'
            '战斗进行中（还能看到费用、干员卡、战场）则 over=false。',
            "这是战斗进行中，还是已经结束了？", image=self.shot(), vision=True, max_tokens=8000)
        if not r.get("ok"):
            return False
        over = bool((r.get("json") or {}).get("over"))
        log(f"战斗是否结束（视觉）: {over} | {_json.dumps(r.get('json'), ensure_ascii=False)[:80]}")
        return over

    def resume_if_paused(self, client: str | None = None) -> bool:
        """战斗中若卡住（暂停），点右上角的暂停键恢复。返回是否做过恢复。"""
        from services.ai_agent.percept import task_roi as _roi
        if not self.is_frozen():
            return False
        if self.battle_over():
            log("画面静止但战斗已结束（失败/结算），不当作暂停处理")
            return False
        roi = _roi("BattlePauseCancel", self.paths.client) or (1170, 20, 60, 60)
        x, y = roi[0] + roi[2] // 2, roi[1] + roi[3] // 2
        log(f"检测到画面静止（疑似暂停），点 {x},{y} 恢复")
        self.screen.run_shell(["input", "tap", str(x), str(y)])
        return True

    def state(self, img: np.ndarray | None = None, use_ocr: bool = False) -> dict:
        img = self.shot() if img is None else img
        texts = [h.label for h in self.ocr.texts(img)] if use_ocr else []
        return self.states.classify(img, ocr_texts=texts)

    def texts(self, img: np.ndarray | None = None, roi: tuple | None = None) -> list[Hit]:
        img = self.shot() if img is None else img
        return self.ocr.texts(img, roi=roi)

    def find(self, name: str, img: np.ndarray | None = None, roi: tuple | None = None,
             threshold: float | None = None, mask_range: tuple | None = None) -> Hit | None:
        img = self.shot() if img is None else img
        return self.templates.find(img, name, roi=roi, threshold=threshold,
                                   mask_range=mask_range)

    def find_task_text(self, task_name: str, img: np.ndarray | None = None,
                       min_score: float = 0.3) -> Hit | None:
        """按 MAA 任务定义定位按钮（任务 roi + 任务 text）。"""
        img = self.shot() if img is None else img
        return self.ocr.find_task_text(img, task_name, min_score=min_score)

    def locate(self, target, img: np.ndarray | None = None) -> Hit | None:
        """把"点这个"的语义解析成像素命中。

        target 形态:
            "开始行动"                    → 先按 OCR 文字找，找不到再当模板名
            {"ocr": "开始行动", "roi": …}  → 只 OCR（给 roi 会先直读整块 roi）
            {"task": "StartButton1"}      → 用 MAA 任务的 roi+text 定位（按钮首选）
            {"template": "PRTS1", …}      → 只模板（可带 roi/threshold/mask_range）
            {"coord": [640, 360]}        → 直接坐标（盲点/已标定位置）
            Hit 实例                      → 原样返回
        """
        img = self.shot() if img is None else img
        if isinstance(target, Hit):
            return target
        if isinstance(target, (list, tuple)) and len(target) == 2:
            x, y = int(target[0]), int(target[1])
            return Hit(kind="coord", label=f"{x},{y}", box=(x, y, 1, 1), score=1.0)
        if isinstance(target, str):
            hit = self.ocr.find_text(img, target)
            if hit:
                return hit
            return self.templates.find(img, target)
        if isinstance(target, dict):
            if "coord" in target:
                x, y = target["coord"]
                return Hit(kind="coord", label=f"{x},{y}", box=(int(x), int(y), 1, 1), score=1.0)
            if "task" in target:
                return self.find_task_text(target["task"], img=img,
                                           min_score=target.get("min_score", 0.3))
            if "ocr" in target:
                return self.ocr.find_text(img, target["ocr"], roi=target.get("roi"),
                                          min_score=target.get("min_score", 0.3))
            if "template" in target:
                return self.templates.find(img, target["template"], roi=target.get("roi"),
                                           threshold=target.get("threshold"),
                                           mask_range=target.get("mask_range"))
        raise ValueError(f"无法解析的目标: {target!r}")


# ── 从 MAA 任务库"学"当前画面有哪些元素 ────────────────────────────────
# 这就是"不会的就找 MAA 的定义去学"的落点：MAA 的 tasks.json 是人工标注过的
# UI 地图（每个可点元素都有 roi + 模板/文案），拿它来刷一屏，就知道这一屏
# 有哪些已知元素、各自在哪。
_SCAN_ACTIONS = {"ClickSelf", "ClickRect", "DoNothing", "Swipe"}


def discover_tasks(img: np.ndarray, paths: ResourcePaths | None = None,
                   client: str = "Official", min_score: float = 0.9,
                   limit: int = 20, budget_s: float = 25.0, name_filter=None) -> list[dict]:
    """扫描任务库，返回当前画面上匹配到的元素（按分数降序）。

    只扫"有 roi + 有模板 + 是点击类动作"的任务（其余要么是全屏 OCR 类，
    要么没有可点语义）。模板图会缓存，但首轮仍要读上千张 png —— 所以有
    budget_s 预算，超时就停并如实报告"没扫完"。
    """
    import time as _t
    paths = paths or ResourcePaths()
    matcher = TemplateMatcher(paths)
    tasks = task_defs(client)
    t0 = _t.time()
    hits: list[dict] = []
    scanned = skipped = 0
    for name, d in tasks.items():
        if _t.time() - t0 > budget_s:
            log(f"任务扫描超预算 {budget_s}s（已扫 {scanned} 项）")
            break
        if not isinstance(d, dict):
            continue
        if name_filter and name_filter not in name:
            continue
        if d.get("action") not in _SCAN_ACTIONS:
            continue
        roi = d.get("roi")
        if not (isinstance(roi, list) and len(roi) == 4):
            continue
        tpls = d.get("template")
        if isinstance(tpls, str):
            tpls = [tpls]
        tpls = [t for t in (tpls or []) if t and t != "empty.png"]
        if not tpls:
            continue
        thr = d.get("templThreshold")
        thr = float(thr if isinstance(thr, (int, float)) else
                    (thr[0] if isinstance(thr, list) and thr else 0.8))
        thr = max(thr, min_score)
        scanned += 1
        for t in tpls:
            hit = matcher.find(img, t, roi=tuple(int(v) for v in roi), threshold=thr,
                               mask_range=d.get("maskRange"))
            if hit:
                # 规格必须**自包含**（template + roi）—— 用 {"task": name} 会在
                # 主题类任务上失效: UiTheme 机制的任务定义里没有 template 字段
                # （模板由主题运行时决定），locate() 解析不出来（2026-09-14 实测）。
                hits.append({"task": name, "template": t, "center": list(hit.center),
                             "box": list(hit.box), "score": round(hit.score, 3),
                             "spec": {"template": t, "roi": [int(v) for v in roi]}})
                break
        else:
            skipped += 1
    # 去重：同一模板命中同一位置的任务（例如 Return.png 被 5 个任务共用）
    # 只留一个 —— 否则锚点列表全是重复项，把 LLM 的注意力挤掉
    seen: set[tuple] = set()
    uniq: list[dict] = []
    for h in sorted(hits, key=lambda x: -x["score"]):
        key = (h["template"], tuple(h["center"]))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(h)
    log(f"任务扫描: 命中 {len(uniq)}（去重前 {len(hits)}）/ 扫 {scanned} 项"
        f"（{_t.time() - t0:.1f}s）")
    return uniq[:limit]
