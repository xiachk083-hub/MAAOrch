"""页面纪要：第一次读图识别→验证→存；第二次查表→直接用（0 延迟、0 token）。

2026-09-16 用户定：**只存验证过的页面**（防错误坐标被反复使用）；
现网资产是纪要雏形（refs/main_fp.npy = pages.json 里的一条，screen_state = 第②层）。
指纹 = 64×64 展平 + L2 归一化；原子写（tmp + replace）。
"""
import json, time, pathlib
import numpy as np
import cv2

PAGES_FILE = pathlib.Path(__file__).resolve().parent.parent / "knowledge" / "pages.json"
SIM_THRESHOLD = 0.85


def fingerprint(frame) -> np.ndarray:
    fp = cv2.resize(frame, (64, 64)).flatten().astype(float)
    return fp / (np.linalg.norm(fp) + 1e-6)


def load_pages() -> dict:
    if not PAGES_FILE.exists():
        return {"pages": []}
    try:
        return json.loads(PAGES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"pages": []}


def save_pages(data: dict) -> None:
    PAGES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PAGES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PAGES_FILE)                     # 原子写


def lookup_page(frame):
    """① 指纹匹配：命中 verified 页面则返回它（0 延迟）。"""
    fp = fingerprint(frame)
    best, best_sim = None, 0.0
    for p in load_pages()["pages"]:
        if not p.get("verified"):
            continue
        sim = float(np.dot(fp, np.array(p["fingerprint"])))
        if sim > best_sim:
            best, best_sim = p, sim
    return best if best_sim > SIM_THRESHOLD else None


def save_page(frame, exit_btn, name: str = "unknown") -> dict:
    """验证后存入纪要；同指纹只累加 seen_count（不重复存）。"""
    data = load_pages()
    fp = fingerprint(frame)
    for p in data["pages"]:
        if float(np.dot(fp, np.array(p["fingerprint"]))) > SIM_THRESHOLD:
            p["seen_count"] = int(p.get("seen_count", 0)) + 1
            p["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_pages(data)
            return p
    entry = {"id": f"page_{int(time.time())}", "fingerprint": fp.tolist(), "name": name,
             "first_seen": time.strftime("%Y-%m-%d %H:%M:%S"), "seen_count": 1,
             "exit": exit_btn, "verified": True}
    data["pages"].append(entry)
    save_pages(data)
    return entry


def is_same(a, b) -> bool:
    if a is None or b is None:
        return True
    return float(np.abs(b.astype(int) - a.astype(int)).mean()) <= 3


def verify_and_save(frame_before, exit_btn, tap_fn, capture_fn) -> bool:
    """点一次 → 双重验证（画面变了 + 新页可识别）→ 通过才存；不通过不存、交人工。"""
    tap_fn(int(exit_btn["x"] * frame_before.shape[1]), int(exit_btn["y"] * frame_before.shape[0]))
    time.sleep(0.8)
    after = capture_fn()
    if is_same(frame_before, after):
        return False                            # 没变 → 不存
    from tools.screen_state import state_of      # noqa: E402
    st, conf = state_of(after)
    if st == "other" and conf < 0.5:
        return False                            # 新页也认不出 → 不确定，不存
    save_page(frame_before, exit_btn)
    return True


if __name__ == "__main__":
    import tempfile
    PAGES_FILE = pathlib.Path(tempfile.mkdtemp()) / "pages.json"      # smoke 用临时文件
    f = np.zeros((720, 1280, 3), dtype=np.uint8)
    f[300:400, 500:700] = 200
    save_page(f, {"name": "X关闭", "x": 0.95, "y": 0.06}, name="test_popup")
    hit = lookup_page(f)
    assert hit and hit["name"] == "test_popup" and hit["verified"], hit
    save_page(f, {"name": "X关闭", "x": 0.95, "y": 0.06}, name="test_popup")   # 同指纹 → 只累加
    assert load_pages()["pages"][0]["seen_count"] == 2, "同指纹应累加 seen_count"
    g = np.zeros((720, 1280, 3), dtype=np.uint8); g[:, :400] = 255            # 不同页
    assert lookup_page(g) is None, "不同页不应命中"
    json.loads(PAGES_FILE.read_text(encoding="utf-8"))
    print("smoke ✓ | 页数:", len(load_pages()["pages"]), "| 原子写+指纹匹配+去重都过")
