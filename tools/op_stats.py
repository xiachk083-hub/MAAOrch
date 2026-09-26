"""打印候选干员的阻挡数 / 生命 / 费用 —— 选堵口人的硬指标（表里 blockCnt 对部分职业是虚的，需配合实战看）。"""
import json
import pathlib
import sys

P = pathlib.Path(r"D:\1-2\MAAOrch")
ct = json.loads((P / "data/_character_table_full.json").read_text(encoding="utf-8"))
names = sys.argv[1:] or ["凛御银灰", "伊内丝", "风笛", "琴柳", "山", "银灰", "星熊", "塞雷娅", "涤火杰西卡",
                         "年", "泥岩", "幽灵鲨", "艾丝黛尔", "重岳", "玛恩纳", "棘刺", "乌尔比安", "蕾缪安",
                         "艾雅法拉", "灵知", "流明", "予愿安洁莉娜", "缄默德克萨斯", "麒麟R夜刀", "魔王"]
rows = []
for k, v in ct.items():
    if not isinstance(v, dict) or v.get("name") not in names:
        continue
    blk = hp = df = cost = None
    for f in ((v.get("phases") or [{}])[-1].get("attributesKeyFrames") or []):
        d = f.get("data") or {}
        blk = d.get("blockCnt", blk)
        hp = d.get("maxHp", hp)
        df = d.get("def", df)
        cost = d.get("cost", cost)
    rows.append((v["name"], v.get("profession"), v.get("subProfessionId"), blk, hp, df, cost))
for n, p, sp, blk, hp, df, c in rows:
    print("%-10s %-9s %-12s 阻挡%-3s HP%-6s 防%-5s 费%s" % (n, p, sp, blk, hp, df, c))
