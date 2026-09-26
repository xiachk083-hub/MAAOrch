"""看一下某关官方 routes 里被过滤的路线（尤其无人机路线）到底起止在哪。"""
import json
import pathlib
import sys

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
sys.path.insert(0, str(PROJ / "tools"))
import stage_cut as sc  # noqa: E402

stage = sys.argv[1] if len(sys.argv) > 1 else "act14side_ex02"
tp, T, H, W, walk, build, lvl, paths, starts, ends, flip, skipped, air = sc.load_paths(stage)
print("关卡", stage, "H=%d W=%d flip=%s" % (H, W, flip))
if air:
    print("飞行路线：", sorted(air))
print("被过滤：")
for i, why in skipped:
    r = lvl["routes"][i]
    sp, ep = r["startPosition"], r["endPosition"]
    print("  r%-2d %s   raw start=(row%d,col%d) end=(row%d,col%d) checkpoints=%d"
          % (i, why, sp["row"], sp["col"], ep["row"], ep["col"], len(r.get("checkpoints") or [])))

# 波次里每条路线用的是谁
per, first, _ = __import__("stage_plan").enemy_weights(stage)
print("\n各路线敌人（含被过滤的）：")
for ri in sorted(per):
    inpaths = "✓在paths" if ri in paths else "✗被过滤"
    print("  r%-2d %-8s %s" % (ri, inpaths, dict(per[ri])))
