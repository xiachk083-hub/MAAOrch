import pathlib

p = pathlib.Path(r"D:\1-2\MAAOrch\tools\scan_map_panels.py")
s = p.read_text(encoding="utf-8")
old = """            if cur.get("page") != "活动地图":
                rec = {"page": cur.get("page"), "skipped": True,
                       "detail": f"不在活动地图（{cur.get('page')}）⇒ 跳过，不点不读"}"""
new = """            # **主线地图也要能扫**（2026-09-24 修）：主线章节地图的页面指纹是「未知」
            #   （它不在活动地图模板里），原判据直接跳过 ⇒ 主线全变 unknown。
            #   放宽：只要屏上能看到"关卡代号"（白名条 OCR）就当成地图，继续点节点。
            if cur.get("page") != "活动地图" and not _looks_like_map():
                rec = {"page": cur.get("page"), "skipped": True,
                       "detail": f"不在活动地图（{cur.get('page')}）⇒ 跳过，不点不读"}"""
assert old in s, "锚点未找到"
s = s.replace(old, new)

# 插入 _looks_like_map 辅助（复用服务 /look 的 OCR）
helper = '''

def _looks_like_map() -> bool:
    """屏上能否看到关卡代号（XX-n / XX-EX-n）——活动地图与主线章节地图都适用。"""
    import re as _re
    try:
        txt = " ".join(svc("/look").get("texts") or []).upper().replace(" ", "")
        return bool(_re.search(r"[A-Z0-9]{1,3}-(EX-|S-)?\\d{1,2}", txt))
    except Exception:
        return False

'''
anchor = "\ndef main("
if "_looks_like_map" not in s.split("def main(")[0]:
    s = s.replace(anchor, helper + "\ndef main(", 1)

p.write_text(s, encoding="utf-8")
print("scan_map_panels.py 已放宽：主线地图（页面=未知但有代号）也允许扫描")
