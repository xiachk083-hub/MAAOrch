import pathlib
import shutil

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
# 把 EA 地图的真实截图登记成模板（"学一张图"的泛化做法：新活动地图第一见时存一帧）
src = ROOT / "runtime" / "_map_now.png"
dst = ROOT / "data" / "templates" / "pages" / "map_EA.png"
shutil.copy2(src, dst)
print("已学入地图模板:", dst.name, dst.stat().st_size, "bytes")

p = ROOT / "tools" / "maaorch_service.py"
s = p.read_text(encoding="utf-8")
old = """             ("活动地图", "map_PV.png", (0, 0, 1280, 719))]"""
new = """             ("活动地图", "map_PV.png", (0, 0, 1280, 719)),
             ("活动地图", "map_EA.png", (0, 0, 1280, 719))]"""
assert old in s, "anchor not found"
p.write_text(s.replace(old, new), encoding="utf-8")
print("specs 已登记 map_EA.png")
