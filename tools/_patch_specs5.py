import pathlib
import shutil

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
src = ROOT / "runtime" / "_cv1.png"          # CV(不义之财) EX 区地图真截图
dst = ROOT / "data" / "templates" / "pages" / "map_CV.png"
shutil.copy2(src, dst)
print("已学入地图模板:", dst.name, dst.stat().st_size, "bytes")

p = ROOT / "tools" / "maaorch_service.py"
s = p.read_text(encoding="utf-8")
old = '             ("活动地图", "map_EA.png", (0, 0, 1280, 720))]'
new = ('             ("活动地图", "map_EA.png", (0, 0, 1280, 720)),\n'
       '             ("活动地图", "map_CV.png", (0, 0, 1280, 720))]')
if old in s and 'map_CV.png' not in s:
    p.write_text(s.replace(old, new), encoding="utf-8")
    print("specs 已登记 map_CV.png")
else:
    print("specs 无需改动（已存在或锚点不匹配）")
