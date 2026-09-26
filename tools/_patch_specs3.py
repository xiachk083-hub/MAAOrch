import pathlib

p = pathlib.Path(r"D:\1-2\MAAOrch\tools\maaorch_service.py")
s = p.read_text(encoding="utf-8")
# ROI 高度写成了 719，而地图模板是整屏 720 高 ⇒ 模板比 ROI 还高，matchTemplate 直接不匹配。
s = s.replace('("活动地图", "map_EP.png", (0, 0, 1280, 719))', '("活动地图", "map_EP.png", (0, 0, 1280, 720))')
s = s.replace('("活动地图", "map_LE.png", (0, 0, 1280, 719))', '("活动地图", "map_LE.png", (0, 0, 1280, 720))')
s = s.replace('("活动地图", "map_MT.png", (0, 0, 1280, 719))', '("活动地图", "map_MT.png", (0, 0, 1280, 720))')
s = s.replace('("活动地图", "map_PV.png", (0, 0, 1280, 719))', '("活动地图", "map_PV.png", (0, 0, 1280, 720))')
s = s.replace('("活动地图", "map_EA.png", (0, 0, 1280, 719))', '("活动地图", "map_EA.png", (0, 0, 1280, 720))')
p.write_text(s, encoding="utf-8")
print("ROI 高度已修为 720")
