"""打印各 BI-EX 关卡的 options（人数/初始费/回费速度/生命点）。"""
import json
import pathlib

P = pathlib.Path(r"D:\1-2\MAAOrch")
for n in range(1, 9):
    s = "act14side_ex%02d" % n
    f = P / "data" / ("_level_%s.json" % s)
    if not f.exists():
        print(s, "（没有关卡文件）")
        continue
    d = json.loads(f.read_text(encoding="utf-8"))
    o = d["options"]
    r = d.get("characterLimit")
    print("%s: 人数上限 %s  初始费 %s  %s 秒/费  生命 %s  moveMul %s  最大费 %s"
          % (s, o.get("characterLimit"), o.get("initialCost"), o.get("costIncreaseTime"),
             o.get("maxLifePoint"), o.get("moveMultiplier"), o.get("maxCost")))
