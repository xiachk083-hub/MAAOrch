"""本地 OCR 封装（rapidocr_onnxruntime）。

**为什么有这个文件**：`tools/read_nodes.py` 的 `extract_nodes()` 一直是
    `from infrastructure.ocr import ocr_boxes`
—— 而这个文件**此前不存在** ⇒ 它 except 掉、`ocr_boxes = None`、直接 `return []`，
于是 `--read` 永远报"没读到节点"，**看起来像识别不出来，实际是接口缺了**。

约定：`ocr_boxes(img) -> [{"text", "cx", "cy", "conf", "box"}, …]`，坐标是像素中心。
中文识别一般，**ASCII 的关卡代号（SR-1 / SR-EX-3 / GA-EX-2）是它的舒适区** —— 这正是
读活动地图节点需要的。
"""
from __future__ import annotations

import atexit
import os

_engine = None

# ---------------- 帧指纹记忆（同画面不重复 OCR）----------------
# 为什么：一局要 OCR 15~30 次（每张 2.4 秒、吃 CPU），其中不少是**同一画面重复读**
# （弹窗清理、tap_text 前后、状态复核）。同一像素 ⇒ 同一结果，缓存**逐字相同**，
# 不改任何行为；省下的是纯 CPU 时间。键 = 32×18 灰度字节（同画面必同键）。
# 体积：每条只存指纹 + 结果列表，上限 8 条 ⇒ 可忽略。
# 关掉：`MAA_OCR_CACHE=0`。看统计：`MAA_OCR_STATS=1`（退出时打印）。
_CACHE_SIZE = int(os.environ.get("MAA_OCR_CACHE", "8") or "0")
_CACHE: dict[bytes, list] = {}
_ORDER: list[bytes] = []
_STATS = {"call": 0, "hit": 0}


def _finger(img):
    """帧指纹；拿不到图就返回 None（退化成不缓存）。"""
    try:
        from PIL import Image
        if isinstance(img, (str, bytes, os.PathLike)):
            return None                     # 路径输入：不缓存（读盘成本本就不在 OCR 里）
        im = img if hasattr(img, "convert") else Image.fromarray(img)
        return im.convert("L").resize((32, 18)).tobytes()
    except Exception:                                       # noqa: BLE001
        return None


def cache_stats() -> dict:
    return dict(_STATS, size=_CACHE_SIZE, entries=len(_CACHE))


def _report() -> None:
    if os.environ.get("MAA_OCR_STATS") == "1" and _STATS["call"]:
        print("[ocr] 调用 %d 次，命中缓存 %d 次（省下约 %.1f 秒 CPU）"
              % (_STATS["call"], _STATS["hit"], _STATS["hit"] * 2.4))


atexit.register(_report)


def _get():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        # **省 CPU 的两个开关**（2026-09-17 用户："这个电脑 CPU 占用这么高不行的呀，
        # 还会影响模拟器它自己游戏的运行"）—— 这台机器同时跑着 3 个模拟器 + 2 个推图，
        # OCR 一抢核，模拟器渲染就卡，MAA 的识图跟着不准：
        #   use_cls=False —— 关掉**文字方向分类器**（默认开启的第三个模型）。它的作用是判断
        #     每框文字是否旋转 180°，而我们的截图永远正立 ⇒ 纯浪费。实测关掉后
        #     「SR-EX-3…SR-EX-7」5 个节点名一个不少。
        #   intra_op_num_threads=3 —— onnxruntime 默认用满**物理核**（这台 18 个），
        #     多个进程同时全屏 OCR 会互相抢核。实测同一帧 CPU **4.25s → 2.39s**（-44%），
        #     识别结果逐字相同；代价是墙钟 0.67s → 1.7s，可接受。
        _engine = RapidOCR(use_cls=False, intra_op_num_threads=3)
    return _engine


def ocr_boxes(img) -> list[dict]:
    """一张图 → 文字框列表。`img` 可以是 cv2 的 ndarray（BGR）或文件路径。

    开缓存时：**同一帧第二次调用直接返回上次结果**（逐字相同，不改变判断）。
    """
    key = _finger(img) if _CACHE_SIZE > 0 else None
    if key is not None and key in _CACHE:
        _STATS["call"] += 1
        _STATS["hit"] += 1
        return _CACHE[key]

    res, _elapse = _get()(img)
    out = []
    for item in (res or []):
        try:
            box, text, score = item[0], item[1], item[2]
        except Exception:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append({"text": str(text), "conf": float(score),
                    "cx": int(sum(xs) / len(xs)), "cy": int(sum(ys) / len(ys)),
                    "box": [[int(p[0]), int(p[1])] for p in box]})

    if key is not None:
        _CACHE[key] = out
        _ORDER.append(key)
        while len(_ORDER) > _CACHE_SIZE:
            _CACHE.pop(_ORDER.pop(0), None)
    _STATS["call"] += 1
    return out
