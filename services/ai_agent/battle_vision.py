"""战斗视觉 — MAA 自带的三个 ONNX 模型（Python 侧首次接入）。

MaaCore 一直在用它们，Python 侧从来没人加载过（全仓 grep `.onnx` 只有
ocr_tool / stage_probe 的 PaddleOCR）。这里把它们接进感知层，战斗内
（<300ms 预算）就不需要任何外呼。

模型规格（onnxruntime 实读，非推断）:
    operators_det.onnx        输入 [1,3,640,640]  → 输出 [1,5,8400]
                              = 单类 yolov8 检测（4 bbox + 1 类分，无 objectness）
    skill_ready_cls.onnx      输入 [N,3,64,64]    → 输出 [N,3] 原始 logits
                              = 三类 (c, n, y)，见下面 skill_ready 的说明
    deploy_direction_cls.onnx 输入 [1,3,96,96]    → 输出 [1,4]
                              = 四分类（方向顺序**未标定**，见 deploy_direction）

预处理是这类模型最容易错的地方（通道顺序/归一化/尺寸），而 MaaCore 的
调试产物恰好给了答案 —— services/maa/instances/1/debug/skill_ready/{y,n}/
有 66 张带标签的 64x64 真实样本，文件名里还嵌了 MaaCore 当时的三个软值
（c/n/y）。SkillReadyModel.calibrate() 用这批样本反推正确的预处理组合，
校准结果落 data/agent_vision_calib.json，避免以后靠猜。

坐标换算用的是 MAA 自己的几何常量（来自 tasks/tasks.json）:
    BattleSkillReady          rectMove [-28,-140,64,64]   干员卡 → 技能就绪窗
    BattleDeployDirectionRectMove rectMove [-48,-48,96,96] 目标格 → 朝向窗
"""
from __future__ import annotations

import json
import pathlib

import numpy as np

from services.ai_agent.percept import PROJ, ResourcePaths, Hit, log

# MAA 的几何常量（tasks/tasks.json:4016 / :3991）
SKILL_READY_RECT_MOVE = (-28, -140, 64, 64)
DEPLOY_DIRECTION_RECT_MOVE = (-48, -48, 96, 96)

# 技能就绪标记（金色菱形 + 黑色爆发图标）的模板定位参数。
# 2026-09-14 实机校准（日服界园战斗帧）: 真标记在亮色屋顶背景上只有 **0.685** 分，
# MAA 默认 templThreshold 0.8 会漏掉；0.65 起有效。标记中心 ± 32 = 模型要的
# 64x64 窗口 —— 用该窗口喂模型得 ready 0.9952，与画面（干员头顶确有标记）一致。
# 所以判定顺序是: 模板先定位（便宜、给坐标）→ 模型确认（抗背景误裁）。
SKILL_MARKER_TEMPLATE = "BattleSkillReady"
SKILL_MARKER_THRESHOLD = 0.65
SKILL_MARKER_WINDOW = 64

# c/n/y 的顺序来自 MaaCore 落盘文件名 `(c{x})(n{y})(y{z})` 与文件夹标签
SKILL_READY_LABELS = ("cooling", "not_ready", "ready")

CALIB_FILE = PROJ / "data" / "agent_vision_calib.json"

# 待校准的预处理候选：(通道顺序, 缩放) —— 用带标签样本选最准的那个
_PREP_CANDIDATES = (
    ("bgr", "unit"),    # BGR/255
    ("rgb", "unit"),    # RGB/255
    ("bgr", "raw"),     # BGR 0-255
    ("rgb", "raw"),
    ("bgr", "imagenet"),
    ("rgb", "imagenet"),
)


def _prep(crop: np.ndarray, order: str, norm: str) -> np.ndarray:
    """BGR(HWC uint8) → 模型输入 (1,3,H,W) float32。"""
    import cv2
    x = crop
    if order == "rgb":
        x = cv2.cvtColor(x, cv2.COLOR_BGR2RGB)
    x = x.astype(np.float32)
    if norm == "unit":
        x /= 255.0
    elif norm == "imagenet":
        x /= 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        x = (x - mean) / std
    return x.transpose(2, 0, 1)[np.newaxis, :, :, :]


class _Session:
    """惰性 ONNX 会话（模型 ~41MB 合计，进程内只加载一次）。"""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self._sess = None

    def get(self):
        if self._sess is None:
            if not self.path.exists():
                raise FileNotFoundError(f"模型缺失: {self.path}")
            import onnxruntime as ort
            self._sess = ort.InferenceSession(str(self.path),
                                             providers=["CPUExecutionProvider"])
        return self._sess

    def run(self, batch: np.ndarray) -> np.ndarray:
        sess = self.get()
        return sess.run(None, {sess.get_inputs()[0].name: batch})[0]


class SkillReadyModel:
    """干员卡技能是否可点（三分类）。

    MAA 的调用位置: BattleSkillReady 的 rectMove [-28,-140,64,64] —— 以干员卡
    矩形为基准向左上偏移取 64x64 窗口，32x32 的"技能就绪"光圈落在其中。
    """

    def __init__(self, paths: ResourcePaths | None = None) -> None:
        self.paths = paths or ResourcePaths()
        self._model = _Session(self.paths.onnx_dir / "skill_ready_cls.onnx")
        self._prep = self._load_calib() or ("bgr", "unit")

    # ── 校准 ──
    def _load_calib(self) -> tuple[str, str] | None:
        try:
            d = json.loads(CALIB_FILE.read_text(encoding="utf-8"))
            p = d.get("skill_ready_prep")
            if isinstance(p, list) and len(p) == 2:
                return (str(p[0]), str(p[1]))
        except Exception:
            pass
        return None

    @staticmethod
    def sample_dir(instance: int | str = 1) -> pathlib.Path:
        return (PROJ / "services" / "maa" / "instances" / str(instance)
                / "debug" / "skill_ready")

    def calibrate(self, instance: int | str = 1, verbose: bool = False) -> dict:
        """用 MaaCore 自己落盘的带标签样本反推预处理组合。

        样本目录 debug/skill_ready/{y,n}/*.png：文件夹名 = 真值，文件名里
        括号内的 (c,n,y) 是 MaaCore 当时的软值（可交叉验证）。
        没有样本时返回 {}，调用方沿用默认预处理。
        """
        import cv2
        base = self.sample_dir(instance)
        samples: list[tuple[np.ndarray, int]] = []
        for folder, idx in (("y", 2), ("n", 1)):
            for f in sorted((base / folder).glob("*.png")):
                img = cv2.imread(str(f))
                if img is not None and img.shape[:2] == (64, 64):
                    samples.append((img, idx))
        if len(samples) < 10:
            log(f"技能就绪校准样本不足（{len(samples)} 张），跳过")
            return {}
        best = None
        results = {}
        for order, norm in _PREP_CANDIDATES:
            ok = 0
            for img, idx in samples:
                logits = self._model.run(_prep(img, order, norm))[0]
                if int(np.argmax(logits)) == idx:
                    ok += 1
            acc = ok / len(samples)
            results[f"{order}/{norm}"] = round(acc, 4)
            if best is None or acc > best[0]:
                best = (acc, (order, norm))
            if verbose:
                log(f"  预处理 {order}/{norm}: {acc:.3f}")
        self._prep = best[1]
        out = {"skill_ready_prep": list(best[1]), "skill_ready_acc": round(best[0], 4),
               "skill_ready_samples": len(samples), "skill_ready_all": results}
        try:
            CALIB_FILE.parent.mkdir(parents=True, exist_ok=True)
            CALIB_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        except Exception as e:
            log(f"校准结果落盘失败: {e}")
        log(f"技能就绪校准: {best[1]} 准确率 {best[0]:.3f}（{len(samples)} 样本）")
        return out

    # ── 推理 ──
    def predict(self, crops: list[np.ndarray]) -> list[dict]:
        """批量判定。返回 [{label, ready, probs}]，probs 为 softmax。"""
        if not crops:
            return []
        batch = np.concatenate([_prep(c, *self._prep) for c in crops], axis=0)
        logits = self._model.run(batch)
        e = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs = e / e.sum(axis=1, keepdims=True)
        out = []
        for p in probs:
            idx = int(np.argmax(p))
            out.append({"label": SKILL_READY_LABELS[idx], "ready": idx == 2,
                        "probs": {SKILL_READY_LABELS[i]: round(float(p[i]), 4)
                                  for i in range(len(SKILL_READY_LABELS))}})
        return out

    def window_from_card(self, card_box: tuple) -> tuple[int, int, int, int]:
        """干员卡矩形 → 技能就绪窗（MAA rectMove 约定）。"""
        dx, dy, w, h = SKILL_READY_RECT_MOVE
        return (int(card_box[0] + dx), int(card_box[1] + dy), w, h)


class DirectionModel:
    """部署朝向识别（四分类）。

    用途是"看懂当前朝向"（MAA 的 BattlefieldClassifier::deploy_direction_analyze
    与 CombatRecordRecognitionTask::classify_direction），不是点方向按钮 ——
    部署时设朝向靠的是从格子中心往该方向再滑一段（BattleSwipeOper）。

    两处未标定（诚实标注，别当成能用的能力）:
      · 预处理用 rgb/imagenet —— 与同类分类器 skill_ready_cls 一致（那个用
        MaaCore 的 66 张标签样本标定到 1.000），且在本场战斗的实测里它给出
        最高的置信度（0.97/0.91 vs unit 的 0.83/0.73）。
      · 四个类别的顺序**未知**（本地无带标签样本），所以只暴露 index+概率。
        P3 打战斗时补样本：按已知朝向部署一次、截 96x96 窗口，即可定序。
    """

    PREP = ("rgb", "imagenet")

    def __init__(self, paths: ResourcePaths | None = None) -> None:
        self.paths = paths or ResourcePaths()
        self._model = _Session(self.paths.onnx_dir / "deploy_direction_cls.onnx")

    def predict(self, crop: np.ndarray) -> dict:
        logits = self._model.run(_prep(crop, *self.PREP))[0]
        e = np.exp(logits - logits.max())
        probs = e / e.sum()
        idx = int(np.argmax(probs))
        return {"index": idx, "probs": [round(float(v), 4) for v in probs],
                "label": None, "verified": False}

    def window_from_point(self, point: tuple) -> tuple[int, int, int, int]:
        """目标格屏幕坐标 → 朝向窗（MAA rectMove [-48,-48,96,96]）。"""
        dx, dy, w, h = DEPLOY_DIRECTION_RECT_MOVE
        return (int(point[0] + dx), int(point[1] + dy), w, h)


def _letterbox(img: np.ndarray, size: int = 640) -> tuple[np.ndarray, float, int, int]:
    """等比缩放 + 灰边填充到 size×size（ultralytics YOLO 的标准预处理）。

    别用直接拉伸：1280x720 → 640x640 会把宽压扁成一半，检测直接崩
    （2026-09-14 实测: 拉伸时全场只命中一块地砖；letterbox 后同一场战斗的
    连续截图上稳定检出 2-3 个干员，中心坐标一致、得分 0.80-0.83）。
    返回 (画布, 缩放比, 左填充, 上填充) —— 反算坐标时必须用它们。
    """
    import cv2
    r = min(size / img.shape[0], size / img.shape[1])
    nw, nh = int(round(img.shape[1] * r)), int(round(img.shape[0] * r))
    canvas = np.full((size, size, 3), 114, np.uint8)
    dw, dh = (size - nw) // 2, (size - nh) // 2
    canvas[dh:dh + nh, dw:dw + nw] = cv2.resize(img, (nw, nh))
    return canvas, r, dw, dh


class OperatorDetector:
    """战场上干员检测（单类 yolov8）。

    "干员还在不在"在 MAA 侧只有这条 ONNX 路径 + 头像重匹配（没有血条识别，
    DLL 里 hp_analyze / LifePoint 计数为 0），所以这是快思考判断存活的主信号。

    预处理 = letterbox + RGB/255（实测标定，见 _letterbox 注释）。
    """

    CONF = 0.35          # 实测: 真干员 0.80+，地砖/背景误检 <0.35，0.35 是干净的分界
    IOU = 0.45
    PREP = ("rgb", "unit")

    def __init__(self, paths: ResourcePaths | None = None, conf: float | None = None,
                 iou: float | None = None) -> None:
        self.paths = paths or ResourcePaths()
        self.conf = self.CONF if conf is None else conf
        self.iou = self.IOU if iou is None else iou
        self._model = _Session(self.paths.onnx_dir / "operators_det.onnx")

    def detect(self, img: np.ndarray) -> list[Hit]:
        """返回图内检测到的干员框（1280x720 绝对坐标，按置信度降序）。"""
        h, w = img.shape[:2]
        canvas, r, dw, dh = _letterbox(img, 640)
        out = self._model.run(_prep(canvas, *self.PREP))   # [1,5,8400]
        pred = out[0].T                                    # [8400,5] → cx,cy,bw,bh,score
        scores = pred[:, 4]
        boxes = []
        for cx, cy, bw, bh, sc in pred[scores >= self.conf]:
            x1 = ((cx - bw / 2) - dw) / r
            y1 = ((cy - bh / 2) - dh) / r
            boxes.append((float(x1), float(y1), float(bw / r), float(bh / r), float(sc)))
        boxes.sort(key=lambda b: -b[4])
        kept: list[tuple[float, tuple[int, int, int, int]]] = []
        for bx, by, bw_, bh_, sc in boxes:
            box = (int(bx), int(by), int(bw_), int(bh_))
            if all(_iou(box, kb) <= self.iou for _s, kb in kept):
                kept.append((sc, box))
        return [Hit(kind="detector", label="operator", box=b, score=s) for s, b in kept]


def _iou(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


class BattleVision:
    """战斗视觉三件套的门面（惰性加载，用到才付加载成本）。"""

    def __init__(self, paths: ResourcePaths | None = None) -> None:
        self.paths = paths or ResourcePaths()
        self._skill: SkillReadyModel | None = None
        self._direction: DirectionModel | None = None
        self._oper: OperatorDetector | None = None

    @property
    def skill(self) -> SkillReadyModel:
        if self._skill is None:
            self._skill = SkillReadyModel(self.paths)
        return self._skill

    @property
    def direction(self) -> DirectionModel:
        if self._direction is None:
            self._direction = DirectionModel(self.paths)
        return self._direction

    @property
    def operator(self) -> OperatorDetector:
        if self._oper is None:
            self._oper = OperatorDetector(self.paths)
        return self._oper

    def skill_ready_markers(self, img: np.ndarray, matcher=None,
                            threshold: float = SKILL_MARKER_THRESHOLD) -> list[dict]:
        """找出"技能就绪"的干员：模板定位标记 + ONNX 确认。

        返回 [{center, box, marker_score, ready, probs}]，center 就是可以点的位置
        （标记浮在干员上方，点它即选中该干员）。这是战斗快思考里"该开谁的技能"
        的唯一信号源 —— 别去猜干员卡偏移（MAA 的 rectMove 是相对它自己跟踪的
        卡矩形，我们拿不到那个矩形）。
        """
        from services.ai_agent.percept import TemplateMatcher
        m = matcher or TemplateMatcher(self.paths)
        img = np.ascontiguousarray(img)
        out: list[dict] = []
        for hit in m.find_all(img, SKILL_MARKER_TEMPLATE, threshold=threshold,
                              max_hits=8, nms_iou=0.3):
            cx, cy = hit.center
            half = SKILL_MARKER_WINDOW // 2
            x0, y0 = max(0, cx - half), max(0, cy - half)
            win = img[y0:y0 + SKILL_MARKER_WINDOW, x0:x0 + SKILL_MARKER_WINDOW]
            if win.shape[:2] != (SKILL_MARKER_WINDOW, SKILL_MARKER_WINDOW):
                continue
            pred = self.skill.predict([win])[0]
            out.append({"center": hit.center, "box": list(hit.box),
                        "marker_score": round(hit.score, 3),
                        "ready": pred["ready"], "probs": pred["probs"]})
        return out
