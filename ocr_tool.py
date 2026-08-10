"""本地 OCR 工具 — 复用 MAA 的 PaddleOCR ONNX 模型（det + rec），零网络依赖。
用法: python ocr_tool.py <图片路径>
输出: 每行 "文字|x,y,w,h|score"
"""
import sys, os
import numpy as np
import cv2
import onnxruntime as ort

OCR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "services", "maa", "instances", "1", "resource", "PaddleOCR")
DET_ONNX = os.path.join(OCR_DIR, "det", "inference.onnx")
REC_ONNX = os.path.join(OCR_DIR, "rec", "inference.onnx")
KEYS_TXT = os.path.join(OCR_DIR, "rec", "keys.txt")


def load_keys(path):
    with open(path, encoding="utf-8") as f:
        keys = [line.strip() for line in f]
    return keys


def det_text_regions(img_bgr, det_session):
    """DB 文本检测: 返回 [x,y,w,h] 列表（原图坐标）"""
    h, w = img_bgr.shape[:2]
    # 缩放至 960 宽（PaddleOCR 标准预处理），且宽高 8 对齐（ONNX 输入要求）
    scale = 960.0 / w if w > 960 else 1.0
    tw = int(round(w * scale / 8) * 8)
    th = int(round(h * scale / 8) * 8)
    if (tw, th) != (w, h):
        img = cv2.resize(img_bgr, (tw, th))
    else:
        img = img_bgr
    ih, iw = img.shape[:2]
    # 归一化 + 转 CHW
    im = img.astype(np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)
    im = (im.transpose(2, 0, 1) - mean) / std
    im = im[np.newaxis, :, :, :].astype(np.float32)
    input_name = det_session.get_inputs()[0].name
    out = det_session.run(None, {input_name: im})[0]
    # out: [1,1,H,W] 概率图
    prob = out[0, 0]
    if prob.shape != (ih, iw):
        prob = cv2.resize(prob, (iw, ih))
    # PaddleOCR DB 后处理: 阈值 → 膨胀 → 轮廓 → 外接矩形
    thr = (prob > 0.3).astype(np.uint8) * 255
    # 膨胀补偿 DB 的收缩（unclip 近似）
    k = max(2, int(3 * (scale if scale != 1.0 else 1)))
    kernel = np.ones((k, k), np.uint8)
    thr = cv2.dilate(thr, kernel)
    contours, _ = cv2.findContours(thr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < 8 or bh < 8:
            continue
        regions.append((int(x / scale), int(y / scale), int(bw / scale), int(bh / scale)))
    # 按 y 排序（行优先）
    regions.sort(key=lambda r: (r[1] // 30, r[0]))
    return regions


def rec_text(img_bgr, region, rec_session, keys):
    """识别单个文本区域"""
    x, y, w, h = region
    x = max(0, x); y = max(0, y)
    crop = img_bgr[y:y + h, x:x + w]
    if crop.size == 0:
        return "", 0.0
    # 保持宽高比缩放到高度 48（MAA rec 模型输入高度）
    rh = 48.0
    rw = crop.shape[1] * rh / crop.shape[0]
    rw = int(round(rw / 8) * 8)  # 8 对齐
    rw = max(8, min(rw, 480))
    crop = cv2.resize(crop, (rw, 48))
    im = crop.astype(np.float32) / 255.0
    im = (im - 0.5) / 0.5  # 归一化到 [-1,1]
    im = im.transpose(2, 0, 1)[np.newaxis, :, :, :].astype(np.float32)
    input_name = rec_session.get_inputs()[0].name
    out = rec_session.run(None, {input_name: im})[0]  # [1, T, C]
    pred = out[0]  # [T, C]
    # CTC 解码（贪心）
    indices = np.argmax(pred, axis=1)
    text = ""
    confs = []
    prev = -1
    for t, idx in enumerate(indices):
        if idx != prev and idx != 0:  # 0 = blank
            text += keys[idx - 1]
            confs.append(float(pred[t, idx]))
        prev = idx
    score = sum(confs) / len(confs) if confs else 0.0
    return text, score


def visualize(img_bgr, results, out_path, darken=0.35):
    """MAA 风格调试可视化：识别区域彩色高亮框，未识别区域压暗。
    每个识别框: 半透明彩色填充 + 边框 + 左上角标签(颜色块)。
    不同行用不同颜色，方便辨别。"""
    overlay = img_bgr.copy()
    h, w = img_bgr.shape[:2]
    # 整图压暗
    dark = (img_bgr.astype(np.float32) * darken).astype(np.uint8)
    colors = [
        (66, 133, 244),   # 蓝
        (219, 68, 55),    # 红
        (244, 180, 0),    # 黄
        (15, 157, 88),    # 绿
        (152, 0, 136),    # 紫
        (0, 172, 172),    # 青
        (255, 112, 67),   # 橙
    ]
    # 按行分组分配颜色
    rows = []
    for text, (x, y, bw, bh), score in results:
        placed = False
        for r in rows:
            if abs(r["y"] - y) < 20:
                r["items"].append((text, x, y, bw, bh, score))
                placed = True
                break
        if not placed:
            rows.append({"y": y, "items": [(text, x, y, bw, bh, score)]})
    for ri, row in enumerate(rows):
        color = colors[ri % len(colors)]
        for text, x, y, bw, bh, score in row["items"]:
            x = max(0, x); y = max(0, y)
            x2 = min(w, x + bw); y2 = min(h, y + bh)
            if x2 <= x or y2 <= y:
                continue
            # 半透明填充
            overlay[y:y2, x:x2] = (
                overlay[y:y2, x:x2].astype(np.float32) * 0.55 +
                np.array(color, dtype=np.float32) * 0.45
            ).astype(np.uint8)
            # 边框
            cv2.rectangle(dark, (x, y), (x2, y2), color, 2)
            # 左上角小色块（行标记）
            cv2.rectangle(dark, (x, y - 12), (x + 12, y), color, -1)
    # 高亮区域恢复亮度
    mask = np.zeros((h, w), dtype=np.uint8)
    for text, (x, y, bw, bh), score in results:
        x = max(0, x); y = max(0, y)
        mask[y:min(h, y + bh), x:min(w, x + bw)] = 255
    bright = cv2.bitwise_and(overlay, overlay, mask=mask)
    bg = cv2.bitwise_and(dark, dark, mask=cv2.bitwise_not(mask))
    result = cv2.add(bright, bg)
    cv2.imwrite(out_path, result)
    return out_path


def main():
    if len(sys.argv) < 2:
        print("用法: python ocr_tool.py <图片路径> [--visual 输出路径]")
        sys.exit(1)
    img_path = sys.argv[1]
    visual_out = None
    if "--visual" in sys.argv:
        idx = sys.argv.index("--visual")
        if idx + 1 < len(sys.argv):
            visual_out = sys.argv[idx + 1]
    img = cv2.imread(img_path)
    if img is None:
        print(f"无法读取图片: {img_path}")
        sys.exit(2)
    # 加载模型
    det_session = ort.InferenceSession(DET_ONNX, providers=["CPUExecutionProvider"])
    rec_session = ort.InferenceSession(REC_ONNX, providers=["CPUExecutionProvider"])
    keys = load_keys(KEYS_TXT)
    regions = det_text_regions(img, det_session)
    results = []
    for reg in regions:
        text, score = rec_text(img, reg, rec_session, keys)
        if text and score > 0.3:
            results.append((text, reg, score))
    for text, (x, y, w, h), score in results:
        print(f"{text}|{x},{y},{w},{h}|{score:.3f}")
    if visual_out:
        visualize(img, results, visual_out)
        print(f"VISUAL_SAVED:{visual_out}")


if __name__ == "__main__":
    main()
