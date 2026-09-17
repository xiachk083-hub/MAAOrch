"""这份作业"算过没有"——把排轴可视化的三条判据写成可计算检查。

判据（用户口径）：
  ① 射程盖住敌人路径（右路 T+10 / 左路 T+16）
  ② 逻各斯是否覆盖第 6 列中路（撕裂者走那里）
  ③ 左路高台有没有**远程**能打御4（飞行，走 (6,6)->(0,1) 直线；近战打不了空中）

射程形状/朝向旋转逻辑**照抄页面**（tools/viz_plan.py 的 HTML 模板），
保证"这里算出来的"与"你在页面上看到的绿格"是同一件事。

重要口径：prts 作业里有**占位符名**（"先锋"/"奶"/"奶盾"）——`data/_char_table.json` 查不到，
既无职业也无射程。这类部署判不了，必须计入"不可判"而不是"没覆盖"。
"""
import sys, pathlib, glob, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import viz_plan as V

RANGED = ("CASTER", "MEDIC", "SNIPER", "SUPPORT")
# 能打空中：狙击/术师/辅助可以，**医疗不能攻击**（只治疗）⇒ 判「打御4」必须排掉 MEDIC
ANTI_AIR = ("CASTER", "SNIPER", "SUPPORT")
HEAL = ("MEDIC",)


def rot(dx, dy, direction):
    """与页面 drawRange() 逐字一致（73 个射程全关于朝向轴对称，四种摆法等价）。"""
    if direction == "Left":
        return -dx, dy
    if direction == "Up":
        return dy, dx
    if direction == "Down":
        return -dy, -dx
    return dx, dy


def line_cells(a, b):
    """Bresenham：飞行单位两点间直线经过的格。"""
    x0, y0 = a
    x1, y1 = b
    cells, dx, dy = [], abs(x1 - x0), abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx - dy
    while True:
        cells.append((x0, y0))
        if (x0, y0) == (x1, y1):
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy; x0 += sx
        if e2 < dx:
            err += dx; y0 += sy
    return cells


def cover_of(act, n2t, rng, ops, W, H):
    """该部署覆盖的格集合 + 射程 id。

    几何计算**走 viz_plan.range_cells（Python 侧唯一实现）** —— 这里只薄包装一层，
    额外回传 rid 供明细打印。别再在本文件里重写旋转/阶段逻辑。
    ops 必须是 `ranges_payload()` 的输出（键 `e`）；**不要**传 `load_box()` 的原始条目
    （那里键是 `elite`）—— 我曾因此让所有人都按 E0 射程算，低估了 E2 干员的覆盖。
    """
    cells = V.range_cells(act, n2t, rng, ops, W, H)
    tri = n2t.get(act["name"])
    rid = None
    if tri and act.get("location"):
        op = ops.get(act["name"]) or {}
        e = op.get("e")
        ph = max(0, min(len(tri) - 1, 0 if e is None else e))
        rid = tri[ph] or tri[0]
    return cells, rid


def analyse(stage, files, label):
    W, H, key, bt, routes, waves = V.level(stage)
    right = next(p for a, b, p in routes if a == (10, 1))
    mid = next(p for a, b, p in routes if a == (4, 6))
    left = next(p for a, b, p in routes if a == (2, 6))
    fly = line_cells((6, 6), (0, 1))          # BFS 跨不过第 3 列 ⇒ 按直线
    col6 = [c for c in mid if c[0] == 6]
    SR, SM, SL, SF = set(right), set(mid), set(left), set(fly)

    prof = V.professions()
    jobs, skipped = V.load_jobs(files, prof, stage)
    n2t, rng, ops = V.ranges_payload(jobs)

    print("\n" + "=" * 118)
    print("【%s】%d 份   右路%d格 中路%d格(6列%d格) 左路%d格 飞行直线%d格"
          % (label, len(jobs), len(right), len(mid), len(col6), len(left), len(fly)))
    print("=" * 118)
    print("%-30s %-3s %-3s | %-6s %-6s %-5s %-5s %-5s | %s"
          % ("作业", "部署", "占位", "右/9", "左/8", "6列/5", "机[5,3]", "御4/7", "判定"))
    print("-" * 118)

    out = []
    for jb in jobs:
        deps = [a for a in jb["actions"] if a["type"] == "Deploy" and a.get("location")]
        ph = [a for a in deps if not a.get("profession")]           # 名字查不到 = 占位符/组名
        covs = [(i + 1, a, cover_of(a, n2t, rng, ops, W, H)[0]) for i, a in enumerate(deps)]

        def best(target, exclude=()):
            """覆盖该目标格数最多的部署：返回 (格数, 排名, 干员)。"""
            hits = [(len(c & target), i, a) for i, a, c in covs
                    if c & target and a.get("profession") not in exclude]
            return max(hits) if hits else (0, None, None)

        # 医疗只治疗（char_table.description 实证：凯尔希"恢复友方单位生命"、
        # 白面鸮"同时恢复三个友方单位的生命"）⇒ 它的射程框住路径/装置不等于打得到，
        # 五条判据全部要排掉 MEDIC，否则凯尔希站在右路会被算成"覆盖右路"。
        nR, _, aR = best(SR, exclude=HEAL)
        nL, _, aL = best(SL, exclude=HEAL)
        n6, _, a6 = best(set(col6), exclude=HEAL)
        nD, _, aD = best({DEV}, exclude=HEAL)
        air = [(len(c & SF), i, a) for i, a, c in covs
               if a.get("profession") in ANTI_AIR and c & SF]
        nF, iF, aF = max(air) if air else (0, None, None)

        ok = [nR > 0, nL > 0, n6 > 0, nD > 0, nF > 0]
        n_ok = sum(ok)
        trust = "可信" if n_ok == 5 else ("不可信(占位%d)" % len(ph) if ph else "缺%d" % (5 - n_ok))
        cells = lambda n, a: ("%d%s" % (n, a["name"][:4])) if a else "-"
        print("%-30s %-3d %-3d | %-6s %-6s %-5s %-5s %-5s | %d/5 %s"
              % (jb["title"][:30], len(deps), len(ph),
                 cells(nR, aR), cells(nL, aL), cells(n6, a6), cells(nD, aD), cells(nF, aF),
                 n_ok, trust))
        out.append((jb, deps, ph, covs, ok, trust,
                    dict(R=(nR, aR), L=(nL, aL), mid=(n6, a6), dev=(nD, aD), fly=(nF, aF))))
    return out, (right, mid, left, fly, col6)


def main():
    stage = "act16side_ex02"
    groups = [
        ("A · prts 池（下拉现有）", sorted(glob.glob("data/copilot/act16side_ex02/*.json"))),
        ("B · ga_derived 推导版（下拉漏掉）", sorted(glob.glob(f"data/copilot/ga_derived/{stage}_derived*.json"))),
        ("C · ex2_jobs / ga16_picked", sorted(glob.glob("data/copilot/ex2_jobs/*.json"))
         + sorted(glob.glob(f"data/copilot/ga16_picked/{stage}*.json"))),
    ]
    allrows = []
    for label, files in groups:
        if not files:
            print("\n【%s】无文件" % label)
            continue
        rows, _ = analyse(stage, files, label)
        allrows += rows

    allrows.sort(key=lambda r: -sum(r[4]))
    print("\n" + "=" * 118)
    print("【总排名】5 条判据：右路 / 左路 / 中路6列 / 装置[5,3]可打 / 御4")
    print("=" * 118)
    for jb, deps, ph, covs, ok, trust, d in allrows:
        print("  %d/5  %-34s %2d部署 占位%-2d  右%-8s 左%-8s 6列%-8s 装置%-8s 御4%-8s %s"
              % (sum(ok), jb["title"][:34], len(deps), len(ph),
                 "%d%s" % (d["R"][0], (d["R"][1]["name"][:4] if d["R"][1] else "")),
                 "%d%s" % (d["L"][0], (d["L"][1]["name"][:4] if d["L"][1] else "")),
                 "%d%s" % (d["mid"][0], (d["mid"][1]["name"][:4] if d["mid"][1] else "")),
                 "%d%s" % (d["dev"][0], (d["dev"][1]["name"][:4] if d["dev"][1] else "")),
                 "%d%s" % (d["fly"][0], (d["fly"][1]["name"][:4] if d["fly"][1] else "")),
                 trust))

    print("\n" + "=" * 118)
    print("【5/5 满分明细】")
    print("=" * 118)
    best = [r for r in allrows if sum(r[4]) == 5]
    if not best:
        print("  （没有任何一份满足全部判据）")
    for jb, deps, ph, covs, ok, trust, d in best:
        print("\n■ %s\n   %s" % (jb["title"], jb["src"]))
        for i, a, c in covs:
            o = ops_of.get(a["name"]) or {}
            tags = []
            if c & set(TH[0]): tags.append("右路%d格" % len(c & set(TH[0])))
            if c & set(TH[2]): tags.append("左路%d格" % len(c & set(TH[2])))
            if c & set(TH[4]): tags.append("中路6列%d格" % len(c & set(TH[4])))
            if c & {DEV}: tags.append("★打冰淇淋机[5,3]")
            if c & set(TH[3]): tags.append("飞行线%d格" % len(c & set(TH[3])))
            print("   %d. %-10s %-9s [%d,%d] %-5s 射程%-5s %2d格  %s"
                  % (i, a["name"], a.get("profession") or "占位", a["location"][0],
                     a["location"][1], a.get("direction") or "?", o.get("e", "-"),
                     len(c), " ".join(tags) or "—"))
    print("\n【飞行直线经过的格】", TH[3])
    print("【装置格】", DEV, " 覆盖它的格数=0 表示没人打得到冰淇淋机")


ops_of = {}
TH = None
DEV = (5, 3)
if __name__ == "__main__":
    _W, _H, _k, _b, _r, _w = V.level("act16side_ex02")
    DEV = tuple(V.device_info("act16side_ex02")[0][0][:2])
    TH = (next(p for a, b, p in _r if a == (10, 1)),
          next(p for a, b, p in _r if a == (4, 6)),
          next(p for a, b, p in _r if a == (2, 6)),
          line_cells((6, 6), (0, 1)),
          [c for c in next(p for a, b, p in _r if a == (4, 6)) if c[0] == 6])
    _jobs, _ = V.load_jobs(sorted(glob.glob("data/copilot/ga_derived/act16side_ex02_derived*.json"))
                           + sorted(glob.glob("data/copilot/act16side_ex02/*.json")),
                           V.professions(), "act16side_ex02")
    ops_of = V.ranges_payload(_jobs)[2]
    main()
