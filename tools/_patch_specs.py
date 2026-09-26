import pathlib

p = pathlib.Path(r"D:\1-2\MAAOrch\tools\maaorch_service.py")
s = p.read_text(encoding="utf-8")
old = """             ("结算页", "result.png", (0, 120, 700, 380))]"""
new = """             ("结算页", "result.png", (0, 120, 700, 380)),
             # **活动地图模板**（2026-09-24 修，auditor-1 发现）：这四张原来叫
             #   `鍦板浘_EP.png`（"地图_EP" 用错编码写出来的乱码名），而且**没列进 specs**
             #   ⇒ page_of 永远认不出任何活动地图（EA 地图 0.391「未知」）⇒ "进图守卫"永远看到「未知」、
             #   把成功判成失败。文件名已改成 ASCII 并在此登记（整屏匹配，不带 ROI）。
             ("活动地图", "map_EP.png", (0, 0, 1280, 719)),
             ("活动地图", "map_LE.png", (0, 0, 1280, 719)),
             ("活动地图", "map_MT.png", (0, 0, 1280, 719)),
             ("活动地图", "map_PV.png", (0, 0, 1280, 719))]"""
assert old in s, "anchor not found"
p.write_text(s.replace(old, new), encoding="utf-8")
print("specs 已加入 4 张活动地图模板")
