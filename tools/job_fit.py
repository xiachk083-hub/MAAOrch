"""把「作业要求」和「这个号的 box」对一遍 —— 挑作业不再靠试。

为什么（2026-09-17 用户："读完缓存，以后挑作业就不靠试"）:
今晚查清了失败的绝对主因是 `no oper found`（364 次 vs 16 次"点不到开始"）——
**MAA 在编队列表里找不到作业要的干员**。以前只能一份一份试（几分钟 + 可能白花理智），
现在有了 box，就能在开跑前把"这份作业这个号能不能编满"算出来。

口径（与 MAA 的实际行为对齐）:
  · `opers[]`  = **硬需求**：每个都必须拥有（MAA 会按其 requirements 校验）
  · `groups[]` = **软需求**：组里**任意一个**拥有即可（真正一个人都没有才算过不去）
  · 突袭版单独标注：没通关普通版时进突袭会被游戏强制当演习（不计通关）

用法:
    python tools/job_fit.py --uid 394162955 --stage EX-5
    python tools/job_fit.py --uid 751737728 --stage EX-5,EX-6,EX-7,EX-8
"""
from __future__ import annotations

import argparse
import glob
import json
import pathlib

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")


def aliases(uid: str) -> list[str]:
    """这个 uid 可能出现在哪些 box 文件名里（历史命名不统一：uid / 昵称"9036" / emu2）。"""
    out = [uid]
    try:
        accs = json.loads((PROJ / "data" / "accounts.json").read_text(encoding="utf-8"))["accounts"]
        for a in accs:
            if a["uid"] == uid:
                out += [str(a.get("name") or ""), f"emu{a.get('emu_index')}"]
    except Exception:
        pass
    return [x for x in out if x]


def load_box(uid: str) -> tuple[set[str], dict[str, dict]]:
    """找这个号最新的一份 box。**也接受直接给文件路径**（`data/boxes/box_<uid>.json`）。

    （2026-09-17 用户验收时就是按路径写的 `load_box(f"data/boxes/box_{uid}.json")` ——
    原来只吃 uid 键 ⇒ 那种写法会 glob 出 0 个文件、静默返回空，看着像"这个号没 box" ✗。）
    """
    _pp = pathlib.Path(str(uid))
    if _pp.suffix == ".json" and _pp.is_file():
        try:
            d = json.loads(_pp.read_text(encoding="utf-8"))
            own = (d.get("own") or d.get("own_opers") or []) if isinstance(d, dict) else []
            m = {o["name"]: o for o in own if isinstance(o, dict) and o.get("name")}
            return set(m), m
        except Exception:                                      # noqa: BLE001
            return set(), {}

    # ⚠️ **优先精确按 uid 找**（`operbox_<uid>_<日期>_<时刻>.json` 是新版命名）——别名（昵称/emuN）
    # 只在找不到 uid 文件时才用，而且**别名必须够长**：曾经用昵称"1"去通配，结果匹配到了
    # 别人的 box 文件、把 9036 的干员当成"1"的（差点又出一个假结论）。
    def pick(files: list[str], prefer_latest: bool) -> tuple[str, list]:
        got = []
        for f in sorted(set(files)):
            try:
                d = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(d, dict):          # 老格式有顶层是数组的，跳过
                continue
            own = d.get("own") or d.get("own_opers") or []
            if own:
                got.append((f, own))
        if not got:
            return "", []
        return (got[-1] if prefer_latest else max(got, key=lambda x: len(x[1])))

    f, own = pick(glob.glob(str(PROJ / "data" / f"operbox_{uid}_*.json")), prefer_latest=True)
    # **按 box 文件里的 uid 校验**（2026-09-17 用户："必须解决这个储存的问题"）: 文件名可以被改、
    # 也可以被别名匹配到别人的文件（实测：V 名下有 396 人的 = SquidP 的 ✗）—— 而文件里的 uid
    # 是**扫描时从游戏画面读到的**（RHODES ID）⇒ 请求的是数字 uid 且对不上就别用，宁可没有。
    if f and str(uid).isdigit():
        try:
            _d = json.loads(pathlib.Path(str(f)).read_text(encoding="utf-8"))
            _fu = str((_d or {}).get("uid") or "")
            if _fu and _fu != str(uid):
                print(f"[闸] {pathlib.Path(str(f)).name} 里的 uid={_fu} ≠ 请求的 {uid} ⇒ 不用这个 box",
                      flush=True)
                return set(), {}
        except Exception:                                      # noqa: BLE001
            pass
    m = {o["name"]: o for o in own if isinstance(o, dict) and o.get("name")}
    return set(m), m


def fit(job_path: str, box: set[str]) -> tuple[list[str], list[str], str]:
    """返回 (缺的硬需求, 组里一个人都没有的组, 标题)。

    注意 `opers`/`groups[].opers` 里可能混进**列表**等非 dict 项（老格式/手改过），
    一律跳过 —— 之前没防，直接 `o.get` 崩在半路。
    """
    d = json.loads(pathlib.Path(job_path).read_text(encoding="utf-8"))
    if not isinstance(d, dict):
        return [], [], ""
    title = ((d.get("doc") or {}).get("title") or "")
    # **空壳作业必须拦住**（2026-09-17 用户："你这个作业是对的吗？…他怎么什么操作都没有"）:
    # 池子里有 6 份"只有标题+视频链接"的占位文件（`actions: []`、`opers: []`），MAA 拿到
    # 零动作作业会**什么都不做**；而我们的代码随后读到**屏幕上残留的上一场结算页**，
    # 把它记成"赢/输" ⇒ 假状态。实测受害两处：V 的 EX-4 突袭（"失败"）、「1」的 SR-8。
    if not (d.get("actions") or []):
        return ["〈空壳作业：0 动作，MAA 会空转〉"], [], title
    # **MAA 兼容性预检**（2026-09-17 实测：`CopilotConfig parse failed` / `invalid params` ⇒
    # 秒退、退出码 0 ⇒ 看着像"跑完了"其实什么都没干）。已知原因 = 动作里带了 **非 MAA 字段**
    # （网页标注器/别的编辑器导出的，如 BulletTime 上的 `cost_changes`）⇒ 直接标出来别让人点。
    BAD_FIELDS = {"cost_changes"}
    for a in (d.get("actions") or []):
        if isinstance(a, dict) and (set(a) & BAD_FIELDS):
            return [f"〈MAA 读不了：动作里有非标准字段 {sorted(set(a) & BAD_FIELDS)[0]}〉"], [], title

    def names_of(items) -> list[str]:
        return [o["name"] for o in (items or [])
                if isinstance(o, dict) and o.get("name")]

    miss = [n for n in names_of(d.get("opers")) if n not in box]
    dead_groups = []
    for g in (d.get("groups") or []):
        if not isinstance(g, dict):
            continue
        names = names_of(g.get("opers"))
        if names and not any(n in box for n in names):
            dead_groups.append(f"{g.get('name')}({len(names)}选0中)")
    return miss, dead_groups, title


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", required=True)
    ap.add_argument("--stage", required=True, help="如 EX-5 或 SR-3,SR-4")
    ap.add_argument("--all-pools", action="store_true", default=True)
    a = ap.parse_args()

    box, meta = load_box(a.uid)
    print(f"账号 {a.uid} ｜ box {len(box)} 人")
    if not box:
        print("  ⚠️ 没有 box：先 python tools/box_read_when_free.py --emu N --uid " + a.uid)
        return

    for stage in [s.strip() for s in a.stage.split(",") if s.strip()]:
        kind, num = stage.split("-")
        prefix = (f"act54side_ex{int(num):02d}_" if kind.upper() == "EX"
                  else f"act54side_{int(num):02d}_")
        rows = []
        for f in sorted(glob.glob(str(PROJ / "data" / "copilot" / "*" / (prefix + "*.json")))):
            pool = pathlib.Path(f).parent.name
            miss, dead, title = fit(f, box)
            rows.append((len(miss), len(dead), "突袭" in title, pool, pathlib.Path(f).name, miss, dead))
        rows.sort(key=lambda r: (r[0], r[1], r[2]))
        print(f"\n=== {stage} ｜ 候选 {len(rows)} 份（按「缺人少→多」排序）===")
        for miss_n, dead_n, raid, pool, fn, miss, dead in rows[:8]:
            tag = "⚠️突袭" if raid else "普通"
            verdict = "✅ 能编" if (miss_n == 0 and dead_n == 0) else \
                      f"❌ 缺硬需求{miss_n}" + (f"/组空{dead_n}" if dead_n else "")
            print(f"  {tag} {pool:20s} {fn[:34]:34s} {verdict}"
                  + (f"  缺={miss[:5]}" if miss else "")
                  + (f"  {dead[:2]}" if dead else ""))


if __name__ == "__main__":
    main()
