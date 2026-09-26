import pathlib

p = pathlib.Path(r"D:\1-2\MAAOrch\tools\maaorch_service.py")
s = p.read_text(encoding="utf-8")

old = """    import cv2
    scores = {}
    for name, t, search in pages_load():
        roi = img[search[1]:search[3], search[0]:min(search[2], img.shape[1])] if search else img
        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        if t.shape[0] >= g.shape[0] or t.shape[1] >= g.shape[1]:
            continue
        scores[name] = round(float(cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED).max()), 3)"""

new = """    import cv2
    scores = {}
    for name, t, search in pages_load():
        roi = img[search[1]:search[3], search[0]:min(search[2], img.shape[1])] if search else img
        if roi.size == 0:
            continue
        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # **2026-09-24 修的自身 bug**：原来写的是 `t.shape >= g.shape ⇒ continue`，
        #   而"整屏地图模板"（720x1280）恰好等于整屏 ROI（720x1280）⇒ **永远被跳过**
        #   ⇒ 活动地图永远认不出（EA 地图 0.391「未知」，而模板自匹配其实是 1.000）。
        #   现在只跳过"严格大于 ROI"的模板；等大时 matchTemplate 返回 1x1，正好是自匹配。
        if t.shape[0] > g.shape[0] or t.shape[1] > g.shape[1]:
            continue
        sc = round(float(cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED).max()), 3)
        # 同名多模板（EP/LE/MT/PV/EA 都叫「活动地图」）要取**最大值**，不能互相覆盖。
        if sc > scores.get(name, -1):
            scores[name] = sc"""

assert old in s, "anchor not found"
p.write_text(s.replace(old, new), encoding="utf-8")
print("page_of 的模板跳过 bug 已修（等大模板 + 同名取最大）")
