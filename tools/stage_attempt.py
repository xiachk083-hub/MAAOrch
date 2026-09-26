#!/usr/bin/env python3
"""通用跑局：**假定游戏已经停在目标关卡的详情页**（不做关卡导航），只负责：
验代理指挥 → 录屏 → 跑 MAA copilot → 拉录像 → 读星级/结算 → 留证据。

为什么单独一份（2026-09-21）：`ce6_attempt.py` 里的认屏导航是 CE-6 专用的（模板也都是 CE-6 的截图 ✗），
换关（BI-EX-1）时导航必然判不出来。与其为每关造导航，不如：**人/用户把关卡页点好**，这一份只跑局。

用法:
    python tools/stage_attempt.py --job data/copilot/BI-EX-1_v1.json --tag bi1 [--record 200]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import time

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
DEV = "127.0.0.1:16416"   # #1（官服）
MC = pathlib.Path.home() / "AppData/Local/Microsoft/WinGet/Links/maa-cli.exe"
OUT = PROJ / "runtime" / "_ce6"
sys.path.insert(0, str(PROJ / "tools"))


# ══ 干员拥有性闸（2026-09-24 夜；审计 `runtime/_audit_axis_opers_20260924.md` + 用户/审计两轮复核）══
# **为什么闸放在这里**（而不是选轴层）：`runtime/plans/cvex_jobs/*.json` 那批（含**专门为本号**
#   把「煌」改写成「乌尔比安」的产物）**不经过选轴层、直接喂 stage_attempt.py** ⇒ 闸放选轴层会
#   整批漏掉它们。这里是**唯一**的 choke point：真正 spawn MAA（`spawn_maa()`）**之前**。
# **为什么必须形状感知**：轴库两种形状并存 ——
#   · 包装型 `{status_code, data:{content:"<JSON 字符串>"}}`：内层要**二次 parse**
#     （事件路径 `*/jobs` 88 份里 80 份是它，实测 91% 在只读顶层的老读取器下静默返回空表）；
#   · 扁平型：`opers` 在顶层（主线两目录 951 份全是它）。
#   老读取器把「数据缺失」和「干员齐全」压成同一个 `[]` ⇒ fail-open 的闸**恰好且只在事件路径**
#   上瞎掉，而事件路径正是现在在挖的那条。这里把 `readable` **显式返回**出去。
# **读取范围（硬约束）**：只认 `opers` / `groups` / `actions` 里**被引用的名字**。
#   **绝不把整份 JSON 当字符串找干员名、绝不读 `doc`** —— 实测 `cvex_jobs/CV-EX-8_27879_patched.json`
#   这批轴上「煌」**只出现在 `doc` 说明里**（`opers` 里是改写过后的 乌尔比安/棘刺…）；扫全文或读
#   `doc` 会把**最该放行的**那批轴判成「缺煌」拦掉，正好挡在最不该挡的地方。
# **需求集口径**：`opers` 里的干员必须**全部**拥有；`actions` 引用到的**组**只要**任一成员拥有**
#   即满足（`story_jobs_big` 862 个组、成员常 13~14、最大 16，语义就是「任一」，与 MAA 编队一致）；
#   `actions` 引用到的**非组名**里，只有确定是**真干员**（`data/_character_table_full.json` 的
#   profession **不是** TRAP/TOKEN）才计入需求 —— 「障碍物/干扰地雷/魂灵之影」这类召唤物与装置
#   不需要拥有（不排除的话 47 份轴会被误拦，那正是 fail-closed 误伤）。
#
# **盒子取值（2026-09-24 修正）**：权威源是**本号工作区盒子** `data/workspaces/<uid>/box.json`
#   （`{"own":[{name,own:true,elite,level,potential,rarity}]}`，本号 751737728 实测 **391** 名）。
#   MAA 自更新的 `runtime/maa_cli/data/lib/data/OperBoxData.json`（`{"own_opers":[…]}`）**会陈旧**：
#   实测只有 **255** 名、`syncTime=2026-09-15`，把 塞雷娅/山/泥岩/伊芙利特/星熊/夜莺/浊心斯卡蒂
#   等**本号确实拥有**的干员判成「没有」——**用它会误拦 744/1039（72%）的轴**。
#   反证（硬）：`AD-EX-2_59767.json` 需要「山」，而这份轴在 13:15 **真打赢了 AD-EX-2|突袭并记账**，
#   即本号一定有「山」⇒ MAA 那份快照不可信。
#   ⇒ 口径：**所有可读源的并集**（单源可能陈旧）。**但并集不是无风险的**：见下。
#   **2026-09-24 审计裁决（达芬奇，我采纳）**：旧注释写"并集只会更全，不会凭空多出没拥有的干员"——
#   **这句是错的**。实测：本号 `box.json` own=**391**；MAA 快照 **255**、`syncTime=2026-09-15`（陈旧 9 天）；
#   **快照有而本号盒没有的 = 12 名**（圣聆初雪 埃癸斯 岳羽由加莉 嵯峨 怒潮凛冬 折桠 提丰 斥罪 火神 矩 罗德岛隐秘队 虎狼丸）。
#   而 `OperBoxData.json` 的键只有 `done/own_opers/source/syncTime` ⇒ **没有 uid 可绑**；
#   旁证：这 12 名在本号盒命中 **0**、在其余 5 个 workspace（别的号）各命中 **7–9** ⇒ **快照可能属于别的号**。
#   ⇒ 风险方向 **fail-open**（放行偏多 ⇒ 烧真打次数，不丢矿），但**必须可证伪**，不能靠注释保证：
#     · 出处审计写 `runtime/reports/oper_gate_box_sources.json`（谁贡献了哪几名）；
#     · `uid` 确认 == 751737728 ⇒ 正常并集；
#     · 取不到/不匹配 ⇒ **告警**（默认仍并集 = 人裁决的"告警不硬失败"），
#       但 `runtime/config/oper_gate.json` 的 `trust_snapshot_without_uid=false` 可以让它**不并入**那些名。
#     · 落 uid 的办法：`python tools/box_uid_stamp.py`（用大厅顶栏读数给快照盖 sidecar）。
OPER_UID = "751737728"
SNAPSHOT_UID_SIDECAR = PROJ / "runtime" / "maa_cli" / "data" / "lib" / "data" / "OperBoxData.uid.json"
BOX_AUDIT = PROJ / "runtime" / "reports" / "oper_gate_box_sources.json"
GATE_CFG = PROJ / "runtime" / "config" / "oper_gate.json"
OPER_BOX = PROJ / "data" / "workspaces" / OPER_UID / "box.json"
BOX_SOURCES = (OPER_BOX,
               PROJ / "runtime" / "maa_cli" / "data" / "lib" / "data" / "OperBoxData.json")
BOX_USED: list = []                           # 本次判定实际用了哪些源（进审计行，供事后追责）
CHAR_TABLE = PROJ / "data" / "_character_table_full.json"
OPER_GATE_AUDIT = PROJ / "runtime" / "_oper_gate.jsonl"
_NON_OPER_PROF = ("TRAP", "TOKEN")          # 召唤物/装置：不需要拥有，也不是编队里选的人
GATED_VERDICTS = ("axis-incompatible", "axis-group-unsat", "axis-opers-unreadable")
MAA_SPAWN = {"n": 0}                        # 闸的回归靠它计数：拦下时它必须是 0


def spawn_maa(argv, env=None):
    """**唯一** spawn MAA 的入口（`MAA_SPAWN` 计数；闸拦下时一次都不会被调到）。"""
    MAA_SPAWN["n"] += 1
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env, errors="replace")


def axis_shape(job_path):
    """(轴正文 dict|None, 形状)。形状 ∈ `flat` / `wrap` / `wrap-bad` / `bad`。

    **包装型要二次 parse**（`data.content` 是 JSON 字符串）—— 老读取器 91% 静默返回空表就在这里。
    """
    try:
        j = json.loads(pathlib.Path(job_path).read_text(encoding="utf-8"))
    except Exception:                                             # noqa: BLE001
        return None, "bad"
    if not isinstance(j, dict):
        return None, "bad"
    d = j.get("data")
    if isinstance(d, dict) and isinstance(d.get("content"), str):
        try:
            c = json.loads(d["content"])
        except Exception:                                         # noqa: BLE001
            return None, "wrap-bad"
        return (c if isinstance(c, dict) else None), "wrap"
    return j, "flat"


def axis_opers(job_path):
    """返回 `(opers, readable)`；`readable=False` = 读不出名单，**不许当空表**。

    ⚠️ 这里的 `readable` 只看 `opers` **非空**（给账本/兼容用的粗口径）。**闸的判决必须用
    `axis_requirements()` 的 `readable`**（= `opers`+`groups`+`actions` **三者全空**才算读不出）：
    `opers: []` + 3 个组（`story_jobs_big/13-12_71775.json`）是**合法的纯组轴**，把这类组编码轴
    判成「不可读」是 fail-closed 误伤（上一个 bug 的镜像面）。
    """
    c, _sh = axis_shape(job_path)
    if not isinstance(c, dict):
        return [], False
    ops = [str(o["name"]) for o in (c.get("opers") or [])
           if isinstance(o, dict) and o.get("name")]
    return ops, bool(ops)


def _axis_groups(c: dict) -> dict:
    """`{组名: [成员名…]}`（`groups` 是**备选**语义：任一成员拥有即可）。"""
    out: dict = {}
    for g in (c.get("groups") or []):
        if isinstance(g, dict) and g.get("name"):
            out[str(g["name"])] = [str(o["name"]) for o in (g.get("opers") or [])
                                   if isinstance(o, dict) and o.get("name")]
    return out


def _axis_actions(c: dict) -> list:
    """`actions` 里**被引用到的名字**（去重保序）：可能是干员名、也可能是组名。"""
    out: list = []
    for a in (c.get("actions") or []):
        if isinstance(a, dict) and a.get("name"):
            n = str(a["name"])
            if n not in out:
                out.append(n)
    return out


_CHAR_PROF = None


def is_player_operator(name: str):
    """这个名字是不是**需要拥有**的真干员：`True`/`False`；表读不到 ⇒ `None`（未知，一律不当需求）。

    判据：`data/_character_table_full.json` 里该名对应的 `profession` 不是 `TRAP`/`TOKEN`。
    """
    global _CHAR_PROF
    if _CHAR_PROF is None:
        try:
            d = json.loads(CHAR_TABLE.read_text(encoding="utf-8"))
            _CHAR_PROF = {str(v.get("name")): str(v.get("profession") or "")
                          for v in d.values() if isinstance(v, dict) and v.get("name")}
        except Exception:                                         # noqa: BLE001
            _CHAR_PROF = {}          # 表缺失 ⇒ 恒 None ⇒ 非组名不进需求（宁可放过，绝不误拦）
    if not _CHAR_PROF:
        return None
    prof = _CHAR_PROF.get(str(name))
    if prof is None:
        return None                  # 表里没有这个名字（新干员/别名）⇒ 不当需求（保守不误拦）
    return prof not in _NON_OPER_PROF


def _read_box_names(p) -> "tuple[set, bool]":
    """读**一个**盒子快照 → `(拥有名单, 可读)`；支持两种形状：

      · 工作区盒子 `{"own": [{name, own: true, …}]}`（本号权威源，带精英/等级/潜能）
      · MAA 快照   `{"own_opers": [{name, …}]}`（可能陈旧，见 `BOX_SOURCES` 上方说明）
    """
    try:
        d = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:                                             # noqa: BLE001
        return set(), False
    if not isinstance(d, dict):
        return set(), False
    lst = d.get("own_opers")
    if not isinstance(lst, list):
        lst = d.get("own")
    if not isinstance(lst, list):
        return set(), False
    names = set()
    for o in lst:
        if isinstance(o, dict):
            if o.get("name") and o.get("own", True) is not False:   # `own:false` 的条目不算
                names.add(str(o["name"]))
        elif isinstance(o, str) and o.strip():
            names.add(o.strip())
    return names, bool(names)


def _snapshot_provenance(p) -> dict:
    """MAA 干员盒快照的**出处**：`{"uid", "from", "declared", "syncTime"}`。

    `from` 表示**证据强度**（不是浏览顺序）：
      · `file`     = **快照文件自己**写了 uid（唯一算"证明"的） ⇒ `declared=False`
      · `sidecar`  = `OperBoxData.uid.json` 里的**声明**（"本号盒子的 uid 是这个"，**不是**快照自身元数据）
                     ⇒ `declared=True` ⇒ **调用方必须仍然告警**
      · `none`     = 两处都没有

    **我第一版把 sidecar 当成了证据 ⇒ 会让告警消失**（那等于用"我们的号是谁"冒充"这份快照出自谁"）。
    2026-09-24 审计的要求正是"取不到就告警"，不是"补一个字段让它闭嘴"——所以 sidecar 只记录**声明**。
    """
    out = {"uid": None, "from": "none", "declared": False, "syncTime": None}
    try:
        d = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:                                              # noqa: BLE001
        d = None
    if isinstance(d, dict):
        out["syncTime"] = d.get("syncTime")
        uid = d.get("uid") or d.get("hoder") or d.get("hoderId")
        if uid:
            out.update(uid=str(uid), **{"from": "file"})
            return out
    try:
        s = json.loads(SNAPSHOT_UID_SIDECAR.read_text(encoding="utf-8"))
    except Exception:                                              # noqa: BLE001
        return out
    if isinstance(s, dict):
        if out["syncTime"] is None:
            out["syncTime"] = s.get("syncTime")
        uid = s.get("uid") or s.get("declared_uid")
        if uid:
            out.update(uid=str(uid), **{"from": "sidecar"}, declared=True)
    return out


def _gate_trust_snapshot() -> bool:
    """`runtime/config/oper_gate.json` 的 `trust_snapshot_without_uid`（默认 **True**）。

    默认 True = 人裁决（达芬奇 2026-09-24）：**告警不硬失败**（fail-open 只烧真打次数，不丢矿）；
    置 False = 保守模式，第二源里那些"本号盒子没有"的名字**不并入**（等于是宁缺勿滥）。
    """
    try:
        d = json.loads(GATE_CFG.read_text(encoding="utf-8"))
        return bool(d.get("trust_snapshot_without_uid", True))
    except Exception:                                              # noqa: BLE001
        return True


def owned_opers(box_path=None):
    """拥有名单 + 可读性：`(set, bool)` —— 可读源取并集，**并做第二源出处审计**。

    为什么并集：单源可能陈旧（MAA 快照实测 255 名 vs 工作区 391 名，会把本号真有的干员判成没有）。
    **但并集不是零风险**（2026-09-24 审计裁决）：快照没有 uid、且比本号盒多出 12 名，
    可能是别的号 ⇒ 出处审计 + 告警 + 可选保守模式，见文件上方注释。
    读不到任何源 ⇒ `(set(), False)` —— **绝不当成「谁都没有」**（那会把每份轴都判成缺干员）。
    """
    paths = [box_path] if box_path is not None else list(BOX_SOURCES)
    names, ok, used = set(), False, []
    ws_names, snap_names, snap_path = set(), set(), None
    for p in paths:
        n, good = _read_box_names(p)
        if good:
            names |= n
            ok = True
            used.append({"src": pathlib.Path(p).name, "n": len(n)})
            if pathlib.Path(p) == OPER_BOX:
                ws_names |= n
            elif pathlib.Path(p).name.startswith("OperBoxData"):
                snap_names |= n
                snap_path = pathlib.Path(p)

    # ② 第二源出处审计（只在**真用到**第二源时做；`box_path` 指定单源的自测路径不触发）
    if snap_path is not None and snap_names:
        prov = _snapshot_provenance(snap_path)
        only = sorted(snap_names - ws_names)
        # **只有快照文件自己写了 uid 才算"证明"**；sidecar 只是声明 ⇒ 仍然告警。
        uid_ok = (prov["uid"] == OPER_UID) and not prov["declared"]
        trust = _gate_trust_snapshot()
        if only and not uid_ok:
            how = ("快照文件自带 uid" if prov["from"] == "file" else
                   f"仅有 sidecar 声明（{prov['uid']}，非快照自身元数据）" if prov["from"] == "sidecar" else "无 uid")
            print(f"⚠ 干员盒第二源出处**未证实**：{snap_path.name} {how}"
                  f"，却比本号盒多 {len(only)} 名：{'、'.join(only[:6])}{'…' if len(only) > 6 else ''}"
                  f" ⇒ 这 12 名可能根本不属于本号；trust_snapshot_without_uid={trust}",
                  file=sys.stderr)
            if not trust:
                names -= set(only)
                print(f"  ⇒ 保守模式：已剔除这 {len(only)} 名（以本号工作区盒为权威）", file=sys.stderr)
        try:
            BOX_AUDIT.parent.mkdir(parents=True, exist_ok=True)
            BOX_AUDIT.write_text(json.dumps({
                "at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "oper_uid": OPER_UID,
                "workspace_n": len(ws_names), "snapshot_n": len(snap_names),
                "snapshot_syncTime": prov["syncTime"], "uid_from": prov["from"],
                "snapshot_uid": prov["uid"], "uid_is_only_declared": prov["declared"],
                "uid_confirmed": uid_ok,
                "only_snapshot_n": len(only), "only_snapshot": only,
                "trust_snapshot_without_uid": trust, "merged_n": len(names),
                "why": "并集是 fail-open（放行偏多 ⇒ 烧真打次数，不丢矿）；本文件用于事后追责哪几名来自第二源。"
                       "sidecar 里的 uid 是**声明**不是证据（快照无 uid 字段），故 uid_confirmed 只在快照自带 uid 时为真",
            }, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:                                          # noqa: BLE001
            pass
    BOX_USED[:] = used
    return names, ok


def axis_requirements(job_path) -> dict:
    """这份轴的**需求集**（形状感知；只从 `opers`/`groups`/`actions` 取名字，**不读 doc**）。

    `readable=False` ⇔ `opers`+`groups`+`actions` **三者全空**（生产 1039 份里应为 **0**；
    非 0 ⇒ **告警**，不是常规路径）。
    `advisory` = `actions` 里既不是组名、也不在 `opers`、又无法确认为真干员的名字（召唤物/装置）：
    **不进需求**，只进审计行。
    """
    c, sh = axis_shape(job_path)
    out = {"readable": False, "shape": sh, "opers": [], "groups": {}, "actions": [],
           "required": [], "ref_groups": [], "action_only": [], "advisory": []}
    if not isinstance(c, dict):
        return out
    op = [str(o["name"]) for o in (c.get("opers") or [])
          if isinstance(o, dict) and o.get("name")]
    gs, ac = _axis_groups(c), _axis_actions(c)
    out.update(opers=op, groups=gs, actions=ac)
    if not op and not gs and not ac:
        return out                              # 三者全空 ⇒ 真·数据缺口（readable=False）
    out["readable"] = True
    out["ref_groups"] = list(dict.fromkeys([n for n in [*ac, *op] if n in gs]))
    req = [n for n in op if n not in gs]         # `opers` 里的干员（用组名占位的归组）
    act_only = [n for n in ac if n not in gs and n not in op]
    out["action_only"] = act_only
    real, adv = [], []
    for n in act_only:
        (real if is_player_operator(n) is True else adv).append(n)
    out["advisory"] = adv
    out["required"] = list(dict.fromkeys([*req, *real]))
    return out


def oper_gate_verdict(req: dict, owned) -> dict:
    """**判决表**（纯函数，`--selftest` 覆盖）：

      | readable | 缺失 / 未满足 | 判决 |
      |---|---|---|
      | True  | 无（`required` 全拥有 且 每个被引用组**至少 1 个成员**拥有） | **放行**（照旧） |
      | True  | `required` 里有缺 | **不 spawn**；`axis-incompatible-<干员>`（**环境不兼容**，**不降权**） |
      | True  | 某被引用组**一个成员都没有** | **不 spawn**；`axis-group-unsat-<组名>`（环境不兼容，**不降权**） |
      | False | — | **不 spawn**；`axis-opers-unreadable`（**数据缺口**桶，**不降权**，日志**显式告警**） |

    **`readable=False` 时绝不放行** —— 放行就是把那批轴重新交回给 MAA 去撞 `OperatorMissing`。
    """
    own = set(owned or ())
    gs = req.get("groups") or {}
    if not req.get("readable"):
        return {"allow": False, "verdict": "axis-opers-unreadable",
                "verdict_tag": "axis-opers-unreadable", "bucket": "data-gap",
                "missing": [], "unsat_groups": []}
    missing = [n for n in (req.get("required") or []) if n not in own]
    unsat = [g for g in (req.get("ref_groups") or []) if not (set(gs.get(g) or ()) & own)]
    if missing:
        return {"allow": False, "verdict": "axis-incompatible",
                "verdict_tag": "axis-incompatible-%s" % missing[0], "bucket": "env-incompatible",
                "missing": missing, "unsat_groups": unsat}
    if unsat:
        return {"allow": False, "verdict": "axis-group-unsat",
                "verdict_tag": "axis-group-unsat-%s" % unsat[0], "bucket": "env-incompatible",
                "missing": [], "unsat_groups": unsat}
    return {"allow": True, "verdict": "allow", "verdict_tag": "allow", "bucket": "allow",
            "missing": [], "unsat_groups": []}


def oper_gate_log(rec: dict, path=OPER_GATE_AUDIT) -> None:
    """**每次判定写一行**审计日志（append-only jsonl；写失败不许影响跑局）。

    为什么要这行：`stage_attempt.py` **不是全仓唯一的 MaaCore 入口**（`infrastructure/maa_core.py`、
    `manager/manager.py`、`network/api_fastapi.py`、`server/copilot_node.mjs`、`lab_anni_test.py`、
    `runtime/_mas_pre12.py` 都另起炉灶）⇒ 将来靠这行**证明**有没有战斗绕过闸：
    **有跑批、却没有对应的 `allow` 行 = 绕过了闸。**
    """
    try:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec},
                               ensure_ascii=False) + "\n")
    except Exception:                                             # noqa: BLE001
        pass


def oper_gate_check(job_path, *, box_path=None, audit_path=None,
                    tag: str = "", mode: str = "", gate_on: bool = True) -> dict:
    """闸的**完整一步**：读需求 → 读盒子 → 判决 → 写审计行。**不碰设备**（在 spawn MAA 之前调用）。

    `gate_on=False`（`--no-oper-gate`）⇒ **照旧放行**，但**仍然写审计行**（`verdict=gate-off`）：
    每一次跑批都必须在 `_oper_gate.jsonl` 里留下判决，将来才能用「有跑批却没有 allow 行」证明绕过。
    盒子快照读不出来 ⇒ 拥有性**未知**：不判「缺」也不判「齐」（那都是编的），改记
    `oper-box-unreadable`（数据缺口桶）**并显式告警**后**放行** —— 快照缺失时把整批打关全拦下是
    fail-closed 误伤（审计复核明确警告的镜像面）；要临时严格核，用 `--no-oper-gate` 反查再修快照。
    """
    req = axis_requirements(job_path)
    box_path = pathlib.Path(box_path or OPER_BOX)          # 默认**运行时**取（改成 None 才能被测试重定向）
    audit_path = pathlib.Path(audit_path or OPER_GATE_AUDIT)
    own, box_ok = owned_opers(box_path)
    v = oper_gate_verdict(req, own) if gate_on else {
        "allow": True, "verdict": "gate-off", "verdict_tag": "gate-off", "bucket": "gate-off",
        "missing": [], "unsat_groups": []}
    box_unreadable = False
    if gate_on and not box_ok:
        box_unreadable = True
        v = {"allow": True, "verdict": "oper-box-unreadable",
             "verdict_tag": "oper-box-unreadable", "bucket": "data-gap",
             "missing": [], "unsat_groups": []}
    opers, readable = axis_opers(job_path)
    rec = {"tag": tag, "mode": mode, "job": pathlib.Path(job_path).name,
           "job_path": str(job_path), "job_dir": pathlib.Path(job_path).parent.name,
           "gate_on": bool(gate_on),
           "shape": req.get("shape"), "readable": bool(req.get("readable")),
           "box": pathlib.Path(box_path).name, "box_readable": bool(box_ok),
           "box_used": list(BOX_USED),
           "n_box": len(own), "n_opers_top": len(opers),
           "opers_readable_strict": bool(readable),
           "n_opers": len(req.get("opers") or []), "n_groups": len(req.get("groups") or {}),
           "n_required": len(req.get("required") or []),
           "ref_groups": req.get("ref_groups") or [],
           "action_only": req.get("action_only") or [], "advisory": req.get("advisory") or [],
           "verdict": v["verdict"], "verdict_tag": v["verdict_tag"], "bucket": v["bucket"],
           "missing": v["missing"], "unsat_groups": v["unsat_groups"],
           "allow": bool(v["allow"]), "spawn": bool(v["allow"]),
           "box_unreadable": box_unreadable}
    oper_gate_log(rec, audit_path)
    return {**v, "req": req, "audit": rec, "box_readable": bool(box_ok),
            "box_unreadable": box_unreadable}


def sh(*args, timeout=180):
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    return subprocess.run([ADB, "-s", DEV, *args], capture_output=True, text=True,
                          timeout=timeout, env=env, errors="replace")


def shot(name: str) -> pathlib.Path:
    p = OUT / f"{name}.png"
    with open(p, "wb") as f:
        subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"], stdout=f, timeout=60)
    return p


_ABANDON_OCR = None

# 「放弃行动」这一类的按钮文案 —— 演习局里菜单可能换成「退出演习」之类的说法（未逐字核过），
# 所以按**一组**关键词找；找到哪个点哪个（点 OCR 给的真实坐标，不点死坐标）。
_ABANDON_LABELS = ("放弃行动", "退出演习", "结束演习", "放弃演习", "退出战斗")


def battle_hud_text(text: str) -> bool:
    """**文本层**的「战斗态 / 战斗暂停态」判据（纯函数，`--selftest` 覆盖）。

    为什么单列一条：`in_battle()` 只看 ENEMY 计数 / 费用框两个 ROI，而**暂停覆盖层会把那两块盖住**
    ⇒ 只看 ROI 会把「演习中 + PAUSE」误判成"不在战斗"。下面两句是 2026-09-24 14:58/14:59 留证帧
    （`runtime/_blocked_20260924_1458_CV-EX-8.png`、`..._1459_CV-1.png`）上真实读到的文字——
    正是那把游戏留住、让之后 4 次导航全废的一屏：
        `27/37 2X X>演习中<> 剩余可放置角色：2`
        `33/37 X>演习中<X> PAUSE 暂停中--- 剩余可放置角色：2`
    ⚠️ 「剩余可放置角色」**不能单独当判据**（编队页也有这四个字，CE-6 踩过）⇒ 必须与「演习中」同现。
    """
    s = (text or "").replace(" ", "").upper()
    if "PAUSE" in s or "暂停中" in s:
        return True
    # 战斗暂停菜单**独有的按钮文案**（点齿轮/PAUSE 后的覆盖层）—— 出现它就是在战斗里
    if "继续战斗" in s or "放弃行动" in s:
        return True
    return ("演习中" in s) and ("剩余可放置角色" in s)


def _find_abandon(img):
    """在整屏 OCR 里找「放弃行动/退出演习…」按钮，返回其中心坐标；没找到返回 None。

    2026-09-24 修：放弃确认弹窗里顶部说明文字（"放弃行动将会恢复0理智…"）也含关键词，
    老写法按 OCR 顺序取第一个 ⇒ 点中说明文本（非按钮），4 次命中全打空、rc=6 卡死。
    真按钮是短文案（"放弃行动" 4 字）且在屏幕底部 ⇒ 短文案优先、取最靠下者。
    """
    cands = []
    for cx, cy, t in _ocr_hits(img):
        tt = (t or "").replace(" ", "")
        if any(k in tt for k in _ABANDON_LABELS):
            cands.append((cx, cy, tt))
    if not cands:
        return None
    short = [c for c in cands if len(c[2]) <= 8]
    pool = short or cands
    pool.sort(key=lambda c: c[1], reverse=True)
    return (pool[0][0], pool[0][1])


def leave_battle(tag: str, tries: int = 4) -> dict:
    """**把游戏从战斗/暂停态带出去**（收尾逻辑，2026-09-24 加）。

    为什么必须有它（真机教训，CV-EX-8 突袭 14:52~14:57）：两份演习轴跑完时 MAA 报 Completed、
    页面却是 unknown、结果帧**仍是战斗 HUD**，脚本只打印一句警告就 exit 5 走人 ⇒ 游戏停在
    「演习中 + PAUSE 暂停中」那一屏（帧：`runtime/_blocked_20260924_1459_CV-1.png`）⇒ 之后 4 次
    导航全废（`nav_map`/`mt_nav` 只会从地图/详情页出发），CV-EX-8/CV-1/CV-2/CV-3 被队列误标 noreach。
    更早的旁证：`runtime/_settle_20260924_1440_CV-EX-8_raid.png` 这一帧**不是结算页**，
    是「演习中」的战斗画面（ENERGY 22/37、右上 ⏸ 暂停、右下"剩余可放置角色 2"）—— 也就是说
    14:57 那次被记成"真打 failed"的局**根本没开始真打**，人还在上一轮演习没退出来的战斗里。

    ⇒ 规则：**任何出口之前都要确认"不在战斗里"**；在就 齿轮(80,53) → OCR 找「放弃行动」→ 点它，
    最多 `tries` 轮；最后一轮还没找到按钮才用确认框的兜底坐标 (960,574)。

    返回 `{"left": bool, "hits": int, "text": str}` —— `left=True` 才算真的出来了。
    """
    left, hits, last = False, 0, ""
    try:
        import cv2
    except Exception:                     # 没 opencv ⇒ 别让收尾变成崩溃（宁可如实报"没确认"）
        return {"left": False, "hits": 0, "text": "no-cv2"}
    try:
        for k in range(tries):
            img = cv2.imread(str(shot(f"{tag}_l{k}")))
            if img is None:
                continue
            last = _ocr_text(img)
            if not in_battle_or_paused(img):
                left = True
                break
            # 已经停在暂停菜单上（屏上就有「放弃行动」）⇒ 直接点它，别先点齿轮（那会把菜单关掉）
            hit = _find_abandon(img)
            if hit is None:
                sh("shell", "input", "tap", "80", "53")       # 左上齿轮 → 暂停菜单
                time.sleep(2.0)
                img = cv2.imread(str(shot(f"{tag}_m{k}")))
                hit = _find_abandon(img) if img is not None else None
            if hit:
                sh("shell", "input", "tap", str(hit[0]), str(hit[1]))
                hits += 1
                time.sleep(3.0)
                continue
            if k >= tries - 1:
                # 兜底：确认框的按钮（2026-09-21 实测坐标）。**只在最后一轮**才盲点一下，
                #   免得在战斗里乱点把画面点得更糟。
                sh("shell", "input", "tap", "960", "574")
                time.sleep(3.0)
            else:
                time.sleep(1.0)
        if not left:
            img = cv2.imread(str(shot(f"{tag}_after")))
            left = img is not None and not in_battle_or_paused(img)
    except Exception as e:                # noqa: BLE001 —— 收尾失败不该把跑局结果吞掉
        last = "%s｜err:%s" % (last, str(e)[:80])
    print("  ⇒ 战斗收尾：%s（放弃/退出命中 %d 次）"
          % ("已离开战斗" if left else "**仍可能在战斗里**", hits))
    return {"left": bool(left), "hits": hits, "text": last[:200]}


def _cleanup_if_battle(tag: str, img) -> dict:
    """出口收尾：**像战斗态（或这一帧读不到、判不了）才动作**，免得在正常画面上乱点。
    返回可直接 `**` 进 res/finish 的字段。"""
    try:
        if img is not None and not in_battle_or_paused(img):
            return {"battle_after": False}
    except Exception:                     # noqa: BLE001
        pass
    r = leave_battle(tag)
    return {"left_battle": r["left"], "battle_after": not r["left"], "abandon_hits": r["hits"]}


# 战斗 HUD 生命数的**精确位置**（2026-09-21 由视觉核证 E7 逐像素定标）：
#   顶栏中部「白底亮蓝描边棋子(车)图标 x=678..714, y=13..46」+ 右侧**红色数字** x=756..761, y=23..38。
# 旧的 crop=(y16:48, x712:790) 把**棋子图标**当成了数字，OCR 永远读不出 ⇒ read_life() 恒 None
# ⇒ 漏怪检测静默失效（mt1raid* 五局里一次都没报过漏怪）。现在：先窄框 OCR，再用红色像素兜底。
LIFE_RECT = (750, 770, 15, 46)     # x0,x1,y0,y1（红色数字所在带）
LIFE_RECT_WIDE = (700, 800, 8, 52)  # 兜底：连棋子图标一起看
_LIFE_ENG = None


def _ocr_hits(img):
    """OCR 返回 [(中心x, 中心y, 文本), ...]（用于按坐标点击，比如点关卡节点）。"""
    global _LIFE_ENG
    import cv2
    if _LIFE_ENG is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception:
            return []
        _LIFE_ENG = RapidOCR()
    res, _ = _LIFE_ENG(img)
    out = []
    for box, text, *_ in (res or []):
        if not text or not text.strip():
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append((int(sum(xs) / len(xs)), int(sum(ys) / len(ys)), text))
    return out


def _ocr_text(img, rect=None):
    """整屏（或指定矩形）的 OCR 文本。"""
    global _LIFE_ENG
    import cv2
    sub = img if rect is None else img[rect[2]:rect[3], rect[0]:rect[1]]
    if sub is None or sub.size == 0:
        return ""
    big = cv2.resize(sub, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    if _LIFE_ENG is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception:
            return ""
        _LIFE_ENG = RapidOCR()
    res, _ = _LIFE_ENG(big)
    return "".join(t for _b, t, _s in (res or []) if t)


# 战斗 HUD 特征（E7 逐像素定标）：ENEMY 计数在顶栏 x=484-607,y=10-49；费用框在右下 x=1158-1280,y=490-545。
BATTLE_ENEMY_RECT = (480, 615, 8, 52)
BATTLE_COST_RECT = (1150, 1285, 486, 550)


# ⚠️ 2026-09-24 18:3x 修「闸2 假通过」（审计 `docs/audits/20260924_1845_jt83_gate2_falsepass.md`）：
#   原来两个 ROI 都用 `re.search(r"\d", t)` ⇒ **ROI 里出现任意一个数字就算"进战斗"**。
#   JT8-3 36786 那局全程停在**编队页**，费用框 ROI 却 OCR 出噪声串 `类2O` —— 那个 `2` 让闸2 报
#   「t≈48s 起已在战斗界面」，`battle:true / battle_s:47.5` 因此是假读数（该字段的用途恰恰是
#   「进过战斗 ⇒ 这局到不了 3★ 的证据」）。25 帧里 1 帧假真，而闸2 是"任意一帧通过即通过"
#   ⇒ 一段 90s 的跑局误通过概率 ≈62%，全靠闸3 的 `valid:false` 在后面兜。
# 真机留证帧逐帧 OCR 实测的战斗 HUD 文本（`runtime/_ce6/*_seq`）：
#   ENEMY 框：`0/53ENEMY` / `車22/53EHEHY` / `15/37ENEMY`    ← 计数 `N/M`
#   费用框  ：`20C` / `7C` / `330C二` / `99CMAX` / `74C`      ← **数字开头 + 费用图标 `C`**
# ⇒ 只认这两种形态；`类2O`（中文打头、数字后面跟 `O`）两种都不像 ⇒ 判否。
_ENEMY_TOKEN_RE = re.compile(r"\d{1,3}\s*/\s*\d{1,3}")   # 顶栏 ENEMY 计数 `N/M`
_COST_TOKEN_RE = re.compile(r"^\d{1,3}\s*[Cc]|^[Cc]\s*\d{1,3}")  # 费用框：数字+图标 `C`（真机两种读法：`20C` / `C6`）


def hud_roi_hit(rect_text: str) -> bool:
    """单个 ROI 的 OCR 文本是不是**干净的战斗 HUD token**（纯函数，`--selftest` 覆盖）。

    为什么这么严：见上面那段注释 —— 宽判据（"ROI 里有任意数字"）会被编队页的噪声数字骗过。
    口径：**宁可漏判**（漏了由闸3 判「无效-未进战斗」，是"这局不算数"），
    **绝不假真**（假 `battle` 会把"根本没跑成"记成"打了、打不过"）。
    """
    t = (rect_text or "").replace(" ", "")
    return bool(_ENEMY_TOKEN_RE.search(t) or _COST_TOKEN_RE.search(t))


def in_battle(img) -> bool:
    """这一帧是不是「战斗进行中」——判据：顶栏 ENEMY 计数 `N/M`，或右下费用框「数字 + 费用图标 `C`」。"""
    return any(hud_roi_hit(_ocr_text(img, rect)) for rect in (BATTLE_ENEMY_RECT, BATTLE_COST_RECT))


def in_battle_or_paused(img) -> bool:
    """**战斗进行中 或 战斗暂停态** —— 收尾闸的判据。

    为什么不能只用 `in_battle()`：那两个 ROI（ENEMY 计数/费用框）会被**暂停覆盖层**盖住，
    而被留在「演习中 + PAUSE」那一屏时（真机 2026-09-24 14:57~14:59）恰恰就是这种画面。
    ⇒ ROI 判据 + `battle_hud_text()` 的整屏文本判据，两条取或。"""
    if in_battle(img):
        return True
    return battle_hud_text(_ocr_text(img))


# ── 「演习券」读数（2026-09-24 加；用户口径：**演习只消耗演习券，不消耗理智、也不产出收益**）──
# ROI 选在**「演习」按钮那一条**（底部右半条 x860..1290 / y596..706）：stage_attempt 就是点
# (1003,659) 进演习的（见下面 `_DRILL` 那段），而「演习券」的剩余数按 UI 惯例就在这个按钮上/紧邻处。
# ⚠️ 实测留证帧 `runtime/_ce6/20260924_1440_CV-EX-8_drill_41415_pre.png` 上，这一条**只有
#    「普通难度 / 演习 / 开始突袭 -20」三段文字，没有演习券数字** ⇒ 这时**如实返回 None**，
#    绝不拿别的数字（比如那儿的 `-20` 理智消耗）冒充 —— 上层据此"照常演习但日志记 null"。
TICKET_RECT = (860, 1290, 596, 706)
TICKET_KEYWORDS = ("演习券", "演习卷", "演习票")
# 「券不够」的文字证据（读到它才算 low，别拿"没读到"当"没券"）
TICKET_LOW_HINTS = ("演习券不足", "演习卷不足", "演习票不足", "演习券为0", "没有演习券")


def parse_drill_tickets(text: str):
    """从 OCR 文本里取「演习券」剩余数；取不到返回 `None`（纯函数，`--selftest` 覆盖）。

    **只认关键词锚定的数字**（`演习券`/`演习卷`/`演习票` 前后那几个字符里的数）——那一带还有
    `-20`（理智消耗）这种裸数字，裸数字一律不认：宁可 null，也不要一个看着合理的假读数。"""
    s = (text or "").replace(" ", "").replace("：", ":")
    if not s or not any(k in s for k in TICKET_KEYWORDS):
        return None
    for kw in TICKET_KEYWORDS:
        for i in [j for j in range(len(s)) if s.startswith(kw, j)]:
            after = s[i + len(kw): i + len(kw) + 14]
            before = s[max(0, i - 14): i]
            for seg in (after, before):
                m = re.search(r"(\d+)\s*/\s*(\d+)", seg)        # 演习券 12/30
                if m:
                    return int(m.group(1))
                m = re.search(r"剩余[^0-9]{0,3}(\d+)", seg)      # 演习券 剩余 12 张 / 剩余:12
                if m:
                    return int(m.group(1))
            m = re.match(r"[^0-9]{0,4}(\d+)", after)             # 演习券x12 / 演习券 12
            if m:
                return int(m.group(1))
            m = re.search(r"(\d+)[^0-9]{0,2}$", before)          # 12 演习券
            if m:
                return int(m.group(1))
    return None


def read_drill_tickets(img=None, tag: str = "tickets") -> dict:
    """**尽力**读「演习券」剩余数 → `{"tickets": int|None, "text": str, "low": bool}`。

    先看 `TICKET_RECT`（演习按钮那一条），读不出再整屏找一次（仍然**只认关键词锚定的数字**）。
    读不到就 `tickets=None`（不是 0）—— 上层据此"照常演习、日志记 null"。"""
    out = {"tickets": None, "text": "", "low": False}
    try:
        import cv2
        if img is None:
            img = cv2.imread(str(shot(tag)))
        if img is None:
            return out
        x0, x1, y0, y1 = TICKET_RECT
        txt = _ocr_text(img, (x0, x1, y0, y1))
        n = parse_drill_tickets(txt)
        if n is None:
            txt2 = _ocr_text(img)
            n = parse_drill_tickets(txt2)
            out["text"] = (txt + "｜" + txt2)[:240]
        else:
            out["text"] = txt[:240]
        out["tickets"] = n
        flat = out["text"].replace(" ", "")
        out["low"] = bool(n is not None and n <= 0) or any(h in flat for h in TICKET_LOW_HINTS)
    except Exception as e:                # noqa: BLE001 —— 读数失败不许影响跑局
        out["text"] = "err:%s" % str(e)[:80]
    return out


def is_detail_page(img) -> bool:
    """是不是「关卡详情页」（可以起跑的画面）——判据：有关卡机制/按钮文案。"""
    t = _ocr_text(img).replace(" ", "")
    keys = ("萨科塔生态园", "开始行动", "开始突袭", "演习", "推荐平均等级", "附加条件")
    return sum(1 for k in keys if k in t) >= 2


def is_raid_page(img) -> bool:
    """详情页上是不是「突袭」——判据：出现「突袭模式」「开始突袭」或「附加条件」关键词。"""
    t = _ocr_text(img).replace(" ", "")
    return ("突袭模式" in t) or ("开始突袭" in t) or ("附加条件" in t)


def read_life(img):
    """读战斗 HUD 的生命数（1~3 或突袭的 1；漏怪后减少）。

    两级判据：① 窄框 OCR 出纯数字；② OCR 不行就数**红色像素连通块**（红色数字是纯红、白底/蓝图标不是）。
    读不到返回 None（调用方必须把 None 当"没读到"，不要当"没漏怪"）。
    """
    import cv2
    import numpy as np

    global _LIFE_ENG

    def _ocr(rect):
        global _LIFE_ENG
        x0, x1, y0, y1 = rect
        crop = img[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        big = cv2.resize(crop, None, fx=8, fy=8, interpolation=cv2.INTER_CUBIC)
        if _LIFE_ENG is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except Exception:
                return None
            _LIFE_ENG = RapidOCR()
        res, _ = _LIFE_ENG(big)
        for _, t, *_ in (res or []):
            t = t.strip()
            if t.isdigit():
                return int(t)
        return None

    for rect in (LIFE_RECT, LIFE_RECT_WIDE):
        v = _ocr(rect)
        if v is not None:
            return v

    # 兜底：红色数字 → 数连通块（红色数字只有 1~2 个字符）
    x0, x1, y0, y1 = LIFE_RECT_WIDE
    crop = img[y0:y1, x0:x1]
    b, g, r = crop[:, :, 0].astype(int), crop[:, :, 1].astype(int), crop[:, :, 2].astype(int)
    mask = ((r > 130) & (r - g > 60) & (r - b > 60)).astype(np.uint8)
    if mask.sum() < 8:
        return None
    n, lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    blobs = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 4]
    return len(blobs) if blobs else None


def _gate_selftest() -> tuple:
    """**干员闸的离线回归**（不连 adb、不 spawn MAA、不碰模拟器）：形状感知读取器 + 判决表 + 审计行。

    夹具全部是**真轴文件**（不是编的）：
      · `cvex/jobs/job_41427.json`           —— 包装型（`data.content` 要二次 parse）·7 干员
      · `cvex/jobs/act28side_ex01_41496.json`—— 扁平型 ·1 干员（维什戴尔）
      · `story_jobs_big/13-12_71775.json`    —— **纯组轴**（`opers: []` + 3 个组）⇒ 必须 readable
      · `story_jobs_big/7-5_23982.json`      —— 「煌」只出现在 `actions` 里 ⇒ axis-incompatible
      · `cvex_jobs/CV-EX-8_27879_patched.json` —— **doc 陷阱**：「煌」只在 `doc` 说明里，**不许进需求**
    返回 `(通过数, 总数)`。"""
    import contextlib
    import io
    import tempfile

    rows: list = []

    def chk(name, cond, detail: object = ""):
        rows.append((str(name), bool(cond), str(detail)[:150]))

    PL = PROJ / "runtime" / "plans"
    fixA = PL / "cvex" / "jobs" / "job_41427.json"
    fixB = PL / "cvex" / "jobs" / "act28side_ex01_41496.json"
    fixC = PL / "story_jobs_big" / "13-12_71775.json"
    fixH = PL / "story_jobs_big" / "7-5_23982.json"
    fixD = PL / "cvex_jobs" / "CV-EX-8_27879_patched.json"
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="opergate_selftest_"))
    globals()["OPER_GATE_AUDIT"] = tmp / "audit.jsonl"
    audit = tmp / "audit.jsonl"

    def _run_main(job, tag):
        """把 `main()` 当**同进程子过程**跑（这样 `MAA_SPAWN` 计数才看得见）；
        重定向 `OUT` 到临时目录、`stdout` 收进字符串，返回 `(退出码, JSON, 控制台文本)`。"""
        jp = tmp / ("%s.json" % tag)
        old_argv, old_out = sys.argv, OUT
        sys.argv = ["stage_attempt.py", "--job", str(job), "--tag", tag,
                    "--json-out", str(jp)]
        globals()["OUT"] = tmp
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = main()
        finally:
            sys.argv = old_argv
            globals()["OUT"] = old_out
        j = json.loads(jp.read_text(encoding="utf-8")) if jp.exists() else {}
        return rc, j, buf.getvalue()

    # ── ① 形状感知读取器（两种形状 + 纯组轴）──
    _c_a, sh_a = axis_shape(fixA)
    ops_a, rd_a = axis_opers(fixA)
    chk("【夹具A｜包装型】cvex/jobs/job_41427.json 形状=wrap", sh_a == "wrap", sh_a)
    chk("【夹具A】readable=True 且从 `data.content` **二次 parse** 出 7 名干员",
        rd_a and len(ops_a) == 7, ops_a)
    _c_b, sh_b = axis_shape(fixB)
    ops_b, rd_b = axis_opers(fixB)
    chk("【夹具B｜扁平型】act28side_ex01_41496.json 形状=flat", sh_b == "flat", sh_b)
    chk("【夹具B】readable=True 且读出 1 名干员（维什戴尔）", rd_b and ops_b == ["维什戴尔"], ops_b)
    req_c = axis_requirements(fixC)
    chk("【夹具C｜纯组轴】story_jobs_big/13-12_71775.json readable=True（**不是** unreadable）",
        req_c["readable"], req_c["shape"])
    chk("【夹具C】`opers: []` + 读出 **3 个组**（组语义，不是空轴）",
        len(req_c["groups"]) == 3 and not req_c["opers"], list(req_c["groups"]))
    g_c = oper_gate_verdict(req_c, owned_opers()[0])
    chk("【夹具C】3 个被引用组各有成员拥有 ⇒ **放行**（组=任一成员拥有）", g_c["allow"], g_c["verdict_tag"])

    # ── ② 判决表（合成用例；组 10 人里拥有 1 人 vs 拥有 0 人）──
    grp = {"某组": ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]}
    req_g = {"readable": True, "groups": grp, "ref_groups": ["某组"], "required": ["桃金娘"]}
    v1 = oper_gate_verdict(req_g, {"甲", "桃金娘"})
    chk("【判决表】某组 10 人只拥有 1 人 + 需求全拥有 ⇒ **放行**", v1["allow"], v1["verdict_tag"])
    v2 = oper_gate_verdict(req_g, {"桃金娘"})
    chk("【判决表】某组 10 人**一个都没有** ⇒ `axis-group-unsat`（不 spawn）",
        (not v2["allow"]) and v2["verdict"] == "axis-group-unsat" and v2["unsat_groups"] == ["某组"],
        v2["verdict_tag"])
    v3 = oper_gate_verdict({"readable": True, "groups": {}, "ref_groups": [], "required": ["煌"]},
                           {"桃金娘"})
    chk("【判决表】需求里有缺 ⇒ `axis-incompatible-煌`（不 spawn）",
        (not v3["allow"]) and v3["verdict_tag"] == "axis-incompatible-煌" and v3["missing"] == ["煌"],
        v3)
    v4 = oper_gate_verdict({"readable": False, "groups": {}, "ref_groups": [], "required": []}, set())
    chk("【判决表】**readable=False ⇒ 绝不放行**（axis-opers-unreadable／数据缺口桶）",
        (not v4["allow"]) and v4["verdict"] == "axis-opers-unreadable" and v4["bucket"] == "data-gap",
        v4["verdict_tag"])

    # ── ③ 真轴「煌」+ doc 陷阱 ──
    g_h = oper_gate_check(fixH, tag="selftest_huang", audit_path=audit)
    chk("【真轴·煌】story_jobs_big/7-5_23982.json（煌只在 actions 里）⇒ axis-incompatible-煌",
        (not g_h["allow"]) and g_h["verdict_tag"] == "axis-incompatible-煌" and g_h["missing"] == ["煌"],
        (g_h["verdict_tag"], g_h["missing"]))
    if fixD.exists():
        raw_d = fixD.read_text(encoding="utf-8")
        req_d = axis_requirements(fixD)
        g_d = oper_gate_verdict(req_d, owned_opers()[0])
        chk("【doc 陷阱】该轴 `doc` 里**确实写着**「煌」（夹具有效）", "煌" in raw_d, "")
        chk("【doc 陷阱】「煌」只在 `doc` ⇒ **不进需求、不进缺失**（灰名单台账里那批改写给本号的轴）",
            ("煌" not in req_d["required"]) and ("煌" not in g_d["missing"])
            and ("煌" not in req_d["opers"]), (req_d["required"], g_d["missing"]))
    else:
        chk("【doc 陷阱】夹具缺失（cvex_jobs/CV-EX-8_27879_patched.json）", False, fixD)

    # ── ④ 读不出名单的轴（合成临时文件；三种坏法）──
    bads = {"空对象": "{}",
            "包装壳内无内容": '{"status_code":1,"data":{}}',
            "包装壳内层 content 也空": '{"status_code":1,"data":{"content":"{}"}}',
            "内层 content 不是 JSON": '{"status_code":1,"data":{"content":"{oops"}}',
            "整个文件不是 JSON": "not json at all"}
    bad_paths = {}
    for k, txt in bads.items():
        p = tmp / ("bad_%s.json" % abs(hash(k)))
        p.write_text(txt, encoding="utf-8")
        bad_paths[k] = p
        g = oper_gate_check(p, tag="selftest_bad_%s" % abs(hash(k)), audit_path=audit)
        chk("【读不出】%s ⇒ axis-opers-unreadable 且不 spawn" % k,
            (not g["allow"]) and g["verdict"] == "axis-opers-unreadable", g["verdict_tag"])

    # ── ⑤ 端到端：闸拦下 ⇒ **一个 MAA 都没起**（`spawn_maa` 计数）──
    MAA_SPAWN["n"] = 0
    rc_h, j_h, txt_h = _run_main(fixH, "_selftest_opergate_huang")
    chk("【端到端·煌】`main()` 退出码=7（拦下，不是跑局）", rc_h == 7, rc_h)
    chk("【端到端·煌】JSON `verdict=axis-incompatible` + `missing=['煌']`",
        j_h.get("verdict") == "axis-incompatible" and j_h.get("missing") == ["煌"], j_h.get("verdict"))
    chk("【端到端·煌】**日志里出现干员名「煌」**（一眼看得出为什么跳过）", "煌" in txt_h, "")
    chk("【端到端·煌】**没有调用 MAA**（spawn_maa 计数=0）", MAA_SPAWN["n"] == 0, MAA_SPAWN["n"])
    rc_u, j_u, txt_u = _run_main(bad_paths["包装壳内无内容"], "_selftest_opergate_unreadable")
    chk("【端到端·读不出】退出码=7 且 `verdict=axis-opers-unreadable`（数据缺口桶）",
        rc_u == 7 and j_u.get("verdict") == "axis-opers-unreadable"
        and j_u.get("bucket") == "data-gap", [rc_u, j_u.get("verdict")])
    chk("【端到端·读不出】日志**显式告警**（“读不出/三者全空”）", "三者全空" in txt_u, "")
    chk("【端到端·读不出】**也没有调用 MAA**", MAA_SPAWN["n"] == 0, MAA_SPAWN["n"])
    _p = spawn_maa([sys.executable, "-c", "pass"])
    _p.communicate()
    chk("【计数自证】`spawn_maa()` 是真入口（调一次计数就 +1，所以上面的 0 是有意义的）",
        MAA_SPAWN["n"] == 1, MAA_SPAWN["n"])

    # ── ⑥ 审计行（判完每次都写一行）──
    lines = []
    try:
        lines = [json.loads(x) for x in audit.read_text(encoding="utf-8").splitlines() if x.strip()]
    except Exception:                                             # noqa: BLE001
        lines = []
    chk("【审计】`_oper_gate.jsonl` 每次判定一行（本自测共 %d 次判定 / %d 行）"
        % (1 + 4 + len(bads), len(lines)), len(lines) >= len(bads) + 2, len(lines))
    last = lines[-1] if lines else {}
    chk("【审计】行里带 ts/job/verdict/verdict_tag/bucket/missing/spawn（可事后审计）",
        all(k in last for k in ("ts", "job", "verdict", "verdict_tag", "bucket", "missing", "spawn")),
        sorted(last)[:8])
    chk("【审计】拦下的行 `spawn=False`（能证明「没起 MAA」）",
        any(x.get("spawn") is False and x.get("job") == fixH.name for x in lines), "")

    # ── ⑦ 盒子快照读不出（拥有性未知）与 ⑧ --no-oper-gate ──
    g_nb = oper_gate_check(fixH, box_path=tmp / "nope_OperBoxData.json", tag="selftest_nobox",
                           audit_path=audit)
    chk("【盒子缺失】记 `oper-box-unreadable` 且**放行**（拥有性未知 ⇒ 不编「缺」也不编「齐」）",
        g_nb["allow"] and g_nb["verdict"] == "oper-box-unreadable" and not g_nb["box_readable"],
        g_nb["verdict_tag"])
    g_off = oper_gate_check(fixH, tag="selftest_gate_off", audit_path=audit, gate_on=False)
    chk("【--no-oper-gate】关闸 ⇒ 放行（verdict=gate-off），**仍然写审计行**",
        g_off["allow"] and g_off["verdict"] == "gate-off"
        and any(x.get("verdict") == "gate-off" for x in
                [json.loads(y) for y in audit.read_text(encoding="utf-8").splitlines() if y.strip()]),
        g_off["verdict_tag"])

    bad = 0
    for name, ok, detail in rows:
        bad += 0 if ok else 1
        print("  %s %s" % ("✓" if ok else "✗", name))
        if not ok:
            print("      got %s" % detail)
    return len(rows) - bad, len(rows)


def selftest() -> int:
    """**离线自测**（不连 adb/模拟器/MAA）：验「战斗态/暂停态」判据 + 「演习券」解析两段纯逻辑。

    用例里的画面文字**全部来自真机留证帧的 OCR**（不是编的）：
      · `27/37 2X X>演习中<> 剩余可放置角色：2`  ← `runtime/_prime_queue3.log:7458`（14:58 那帧）
      · `33/37 X>演习中<X> PAUSE 暂停中--- 剩余可放置角色：2` ← 同上 :7477（14:59 那帧）
    """
    cases = [
        # ── 战斗态/暂停态（收尾闸的判据；2026-09-24 真机两帧当回归夹具）──
        ("【回归】演习留下的战斗画面（演习中/无 PAUSE）⇒ 判战斗态",
         battle_hud_text("27/37 2X X>演习中<> 剩余可放置角色：2"), True),
        ("【回归】演习留下的战斗画面（演习中 + PAUSE 暂停中）⇒ 判战斗态",
         battle_hud_text("33/37 X>演习中<X> PAUSE 暂停中--- 剩余可放置角色：2"), True),
        ("战斗暂停菜单（覆盖层只剩按钮文案）⇒ 判战斗态",
         battle_hud_text("继续战斗 放弃行动"), True),
        ("**运行中的普通战斗在文本层判不出来**（那两块 ROI 被暂停层挡住才是文本层的活，"
         "运行帧交给 in_battle() 的 ROI 判据）⇒ False",
         battle_hud_text("ENEMY 12/34 COST 32 剩余可放置角色：4"), False),
        ("关卡详情页 ⇒ **不是**战斗态（收尾不许乱点）",
         battle_hud_text("CV-EX-8 附加条件 推荐平均等级 精英2 开始突袭 演习"), False),
        ("编队页（也有「剩余可放置角色」，但没有演习中/PAUSE）⇒ 不是战斗态",
         battle_hud_text("剩余可放置角色：2 开始行动"), False),
        ("空/读不到 ⇒ 不是战斗态（宁可不动手）", battle_hud_text(""), False),
        # ── 闸2 ROI token 判据（2026-09-24 18:3x 加；文本逐字来自真机留证帧的逐帧 OCR）──
        ("【回归】真战斗帧 ENEMY 框 `0/53ENEMY` ⇒ 战斗 HUD", hud_roi_hit("0/53ENEMY"), True),
        ("【回归】真战斗帧 ENEMY 框（棋子图标被读成「車」）`車22/53EHEHY` ⇒ 战斗 HUD", hud_roi_hit("車22/53EHEHY"), True),
        ("【回归】真战斗帧费用框 `20C` ⇒ 战斗 HUD", hud_roi_hit("20C"), True),
        ("【回归】真战斗帧费用框 `330C二` ⇒ 战斗 HUD", hud_roi_hit("330C二"), True),
        ("【回归】真战斗帧费用框 `99CMAX` ⇒ 战斗 HUD", hud_roi_hit("99CMAX"), True),
        ("【回归】真战斗帧费用框（图标被读在数字前）`C6` ⇒ 战斗 HUD", hud_roi_hit("C6"), True),
        ("【回归｜JT8-3 36786 frame011】编队页费用框噪声 `类2O` ⇒ **不是**战斗 HUD（旧判据在这里假真）",
         hud_roi_hit("类2O"), False),
        ("编队页「剩余可放置角色:2」⇒ **不是**战斗 HUD（那个 `2` 就是假真的来源）",
         hud_roi_hit("剩余可放置角色:2"), False),
        ("裸数字 `2` ⇒ **不是**战斗 HUD（没有费用图标 `C`，不当战斗证据）", hud_roi_hit("2"), False),
        ("空/读不到 ⇒ 不是战斗 HUD（宁可漏判，交给闸3）", hud_roi_hit(""), False),
        # ── 「演习券」读数（**只认关键词锚定的数字**，裸数字一律 null）──
        ("演习券 12/30 ⇒ 12", parse_drill_tickets("演习券 12/30"), 12),
        ("「剩余演习券：30」⇒ 30", parse_drill_tickets("演习券剩余:30"), 30),
        ("「演习券x5」⇒ 5", parse_drill_tickets("演习券x5"), 5),
        ("「演习券 0」⇒ 0（真的没券了）", parse_drill_tickets("演习券0"), 0),
        ("**只有 `-20`（理智消耗）⇒ null**（不拿别的数字冒充）",
         parse_drill_tickets("普通难度 演习 开始突袭 -20"), None),
        ("详情页整条文字里没有「演习券」⇒ null",
         parse_drill_tickets("CV-EX-8 附加条件 推荐平均等级 开始突袭 演习"), None),
        ("空文本 ⇒ null", parse_drill_tickets(""), None),
    ]
    bad = 0
    for name, got, want in cases:
        ok = got == want
        bad += 0 if ok else 1
        print("  %s %s" % ("✓" if ok else "✗", name))
        if not ok:
            print("      got  %r\n      want %r" % (got, want))
    print("\n--selftest（战斗态判据 + 演习券解析，不连 adb）：%d/%d 通过" % (len(cases) - bad, len(cases)))
    print("\n--selftest（**干员拥有性闸**：形状感知读取器 + 判决表 + 不 spawn MAA，不连 adb）：")
    g_ok, g_all = _gate_selftest()
    print("--selftest（干员闸）：%d/%d 通过" % (g_ok, g_all))
    return 1 if (bad or g_ok < g_all) else 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", default="", help="copilot 作业 json（`--leave-battle`/`--read-tickets` 时可不给）")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--record", type=int, default=200)
    ap.add_argument("--record-after", type=int, default=0,
                    help="延迟这么多秒再开录屏（用于录长局的后半段，screenrecord 上限 180s）")
    ap.add_argument("--job-timeout", type=int, default=420)
    ap.add_argument("--raid", action="store_true",
                    help="突袭模式：MAA 需要 --raid 才认「开始突袭」按钮")
    ap.add_argument("--drill", action="store_true",
                    help="演习模式：先点关卡页的「演习」，不消耗理智也不计入通关（用于自由迭代）")
    ap.add_argument("--abort-on-leak", dest="abort", action="store_true", default=True,
                    help="漏怪（生命值减少）就掐掉这局——三星目标下继续打没意义（默认开）")
    ap.add_argument("--no-abort", dest="abort", action="store_false")
    # ── 给 sweep 的"演习优先"用的两个开关（2026-09-24，task-15）────────────────
    ap.add_argument("--no-record", action="store_true",
                    help="不录屏：演习试轴每关要跑好几份，省掉 180s 录屏 + ~50MB 拉取")
    ap.add_argument("--json-out", default="",
                    help="把这一局的结果写成 JSON（机读：mode/kind/stars/valid/exit），给 event_sweep 用")
    # ── 两个「只做一件事」的模式（2026-09-24 加，给 event_sweep 用）─────────────────
    ap.add_argument("--leave-battle", dest="leave_battle", action="store_true",
                    help="**只做收尾**：把游戏从战斗/暂停态带出去（齿轮→找「放弃行动」→确认），不跑作业。"
                         "被留在「演习中+PAUSE」时所有导航都会失败，这是唯一的出口收尾")
    ap.add_argument("--read-tickets", dest="read_tickets", action="store_true",
                    help="**只读「演习券」**：OCR 演习按钮那一条（TICKET_RECT）→ JSON/日志，不碰游戏")
    # ── 干员拥有性闸的开关（2026-09-24 夜）────────────────────────────────────
    ap.add_argument("--no-oper-gate", dest="no_oper_gate", action="store_true",
                    help="**关掉干员拥有性闸**（默认开）：不开闸就不会按盒子快照 own_opers 检查"
                         "这份轴需要的干员。只在取证/回归对比时用 —— 平时开着才拦得住"
                         "「缺干员的轴跑到 MAA 编队才炸」")
    args = ap.parse_args()
    if not args.job and not (args.leave_battle or args.read_tickets):
        ap.error("--job 必填（除非用 --leave-battle / --read-tickets）")

    log = OUT / f"{args.tag}.attempt.log"
    res: dict = {"mode": "drill" if args.drill else "real", "raid": bool(args.raid)}

    def finish(code: int, **extra) -> int:
        """**统一出口**：先落 JSON（若 --json-out），再返回退出码。
        为什么要有它：sweep 要机读"这局是演习还是真打、结算几星、有效没有"，
        解析中文日志太脆（换一个空格就崩），所以给一个 JSON 契约。"""
        if args.json_out:
            try:
                p = pathlib.Path(args.json_out)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps({"tag": args.tag, "job": pathlib.Path(args.job).name,
                                         "exit": code, **res, **extra},
                                        ensure_ascii=False), encoding="utf-8")
            except Exception:                      # noqa: BLE001 —— 落盘失败不该影响跑局
                pass
        return code


    def say(m):
        print(m, flush=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(m + "\n")

    if args.leave_battle:
        # **只做收尾**：给 sweep 兜底用（sweep 侧发现这一局把游戏留在战斗里时会调它）。
        res["mode"] = "leave-battle"
        say("=== 收尾：把游戏带出战斗/暂停态（%s）===" % args.tag)
        _lb = leave_battle(args.tag)
        say("  结果：%s（命中 %d 次；屏上文字 %s）"
            % ("已离开战斗" if _lb["left"] else "**仍可能在战斗里**", _lb["hits"], (_lb["text"] or "")[:120]))
        return finish(0 if _lb["left"] else 6, valid=bool(_lb["left"]),
                      reason="leave-battle" if _lb["left"] else "leave-battle-failed",
                      left_battle=_lb["left"], battle_after=not _lb["left"],
                      abandon_hits=_lb["hits"], page_text=_lb["text"])

    if args.read_tickets:
        # **只读「演习券」**（读不到就是 null，不瞎猜）——sweep 在"要不要演习"之前调它。
        res["mode"] = "read-tickets"
        say("=== 读「演习券」剩余数（%s）===" % args.tag)
        _tk = read_drill_tickets(tag=f"{args.tag}_tickets")
        say("  drill_tickets=%s（ROI x%d..%d/y%d..%d；屏上文字：%s）"
            % (_tk["tickets"], TICKET_RECT[0], TICKET_RECT[1], TICKET_RECT[2], TICKET_RECT[3],
               (_tk["text"] or "")[:160]))
        return finish(0, valid=True, reason="read-tickets",
                      drill_tickets=_tk["tickets"], drill_tickets_low=bool(_tk["low"]),
                      tickets_text=_tk["text"])

    # ── ⛔ **干员拥有性闸**（唯一 choke point：真正 spawn MAA 之前；2026-09-24 夜）────────
    #   只挂 `--job` 这一支 —— `--leave-battle` / `--read-tickets` 是收尾与读数（上面已各自返回），
    #   **不该被拦**：拦了只会把「退不出战斗」这类工装问题拖成打不了关。
    #   放在这里 = **连一张截图都不拍**：闸判不许跑就一个字节都不发给设备，更不会 spawn MAA。
    if args.job:
        _g = oper_gate_check(args.job, tag=args.tag,
                            mode=("drill" if getattr(args, "drill", False) else "real"),
                            gate_on=not getattr(args, "no_oper_gate", False))
        res.update({"oper_gate": _g["verdict"], "oper_gate_tag": _g["verdict_tag"],
                    "oper_gate_bucket": _g["bucket"], "axis_shape": _g["req"].get("shape"),
                    "oper_gate_missing": _g["missing"], "oper_gate_unsat_groups": _g["unsat_groups"],
                    "oper_box_readable": _g["box_readable"],
                    "oper_gate_audit": str(OPER_GATE_AUDIT)})
        if _g["verdict"] == "gate-off":
            say("  ✓ 干员闸：**被 --no-oper-gate 关掉**（本次不做拥有性检查，照旧跑；"
                "审计行记 `gate-off` —— 取证/对比专用，别常开）")
        elif _g["box_unreadable"]:
            say("  ⚠️ 干员闸：**盒子快照读不出 own_opers**（%s）⇒ 拥有性未知："
                "不判「缺」也不判「齐」，记 oper-box-unreadable 后**照常跑**（不是「齐全」！）"
                "—— 快照缺失时把整批打关全拦下是 fail-closed 误伤；请尽快修快照" % OPER_BOX)
        if _g["allow"] and _g["verdict"] == "allow":
            say("  ✓ 干员闸放行（形状=%s｜需求 %d 名全拥有｜被引用组 %d 个都可满足）"
                % (_g["req"].get("shape"), len(_g["req"].get("required") or []),
                   len(_g["req"].get("ref_groups") or [])))
        if not _g["allow"]:
            if _g["verdict"] == "axis-incompatible":
                say("  ⛔ 干员闸拦下：这份轴需要**未拥有**的干员【%s】⇒ 不 spawn MAA"
                    "（判定 `%s`／**环境不兼容**：**不判失败、不写 noreach、不降权**）"
                    % ("、".join(_g["missing"]), _g["verdict_tag"]))
            elif _g["verdict"] == "axis-group-unsat":
                say("  ⛔ 干员闸拦下：被引用组【%s】**一个成员都没有** ⇒ 不 spawn MAA"
                    "（判定 `%s`／**环境不兼容**：不判失败、不写 noreach、不降权）"
                    % ("、".join(_g["unsat_groups"]), _g["verdict_tag"]))
            else:
                say("  ⚠️⚠️ 干员闸拦下：这份轴**读不出干员名单**（`opers`+`groups`+`actions` "
                    "三者全空）⇒ 不 spawn MAA（判定 `axis-opers-unreadable`／**数据缺口**："
                    "不判失败、不写 noreach、不降权）。生产 1039 份轴里应为 **0 份** —— "
                    "**出现即告警**，请查这份轴文件是不是没下全")
            say("  审计：%s（每次判定都写一行；tag=%s mode=%s shape=%s）"
                % (OPER_GATE_AUDIT, args.tag, res.get("mode"), _g["req"].get("shape")))
            say("=== 完毕（干员闸拦下：不跑这局、不算失败） ===")
            return finish(7, valid=False, reason=_g["verdict"], verdict=_g["verdict"],
                          verdict_tag=_g["verdict_tag"], bucket=_g["bucket"],
                          missing=_g["missing"], unsat_groups=_g["unsat_groups"],
                          axis_shape=_g["req"].get("shape"),
                          oper_gate_audit=str(OPER_GATE_AUDIT))

    say("=== 通用跑局 %s（作业 %s）===" % (args.tag, pathlib.Path(args.job).name))
    # ⚠️ **顺序修复（2026-09-24 实测）**：原来"先点演习、再自检页面"，可点了演习人就已经离开
    #    关卡详情页（进编队/战斗）⇒ 闸1 判"不像详情页"⇒ 整局作废、MAA 都没起（EA-1 演习就死在这）。
    #    改成：**先自检 + 由闸0 收尾到关卡详情页，最后才点演习**（drill 顺延到闸之后）。
    _DRILL = bool(getattr(args, "drill", False))
    pre = shot(f"{args.tag}_pre")
    try:
        from ce6_star_count import page_kind
        import cv2
        say("  开局前画面类型（自检）：%s" % page_kind(cv2.imread(str(pre))))
    except Exception as e:
        say("  （界面自检跳过：%s）" % e)

    def wait_screen_change(prev: pathlib.Path, timeout: float = 8.0, step: float = 0.4) -> bool:
        """**等画面真的变了**再走，而不是死等 N 秒（task-17 issue#5：闸0 原来每轮盲等 4s×6=24s）。
        判据：与上一帧的平均像素差 > 6（分辨率变化也算变了）。返回是否变过。"""
        import cv2 as _cv2
        import numpy as _np
        try:
            a = _cv2.imread(str(prev))
        except Exception:
            a = None
        if a is None:
            time.sleep(min(1.0, timeout))
            return True
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(step)
            try:
                b = _cv2.imread(str(shot(f"{args.tag}_wait")))
            except Exception:
                continue
            if b is None:
                continue
            if b.shape != a.shape:
                return True
            try:
                if float(_np.abs(b.astype("int16") - a.astype("int16")).mean()) > 6.0:
                    return True
            except Exception:
                return True
        return False

    # ── 闸 0：自动收尾（2026-09-21 加）───────────────────────────────────────
    # 上一局打完会停在结算页（clear/fail），脚本以前直接开跑 ⇒ 闸1 拦下、这局白等。
    # 收尾链：结算页→点中央；地图页→点关卡节点；其它→点中央推进。最多 6 次，绝不点左上返回。
    _stage_code = pathlib.Path(args.job).stem.split("_")[0]   # 如 MT-EX-2_raid_v1 → MT-EX-2
    for _k in range(6):
        try:
            import cv2 as _cv2
            _img = _cv2.imread(str(pre))
            _kind = page_kind(_img)
            # **战斗态 ⇒ 先退出来**（2026-09-24 加）：闸0 原来只会"点中央推进"，而「演习中 + PAUSE」
            #   那一屏点中央根本没用 —— 真机 51786 的第一局就是这么连点 5 次、次次仍判"不在详情页"，
            #   最后闸1 拦下、整局作废。收尾逻辑必须认识"战斗态"（含 PAUSE）。
            if _img is not None and in_battle_or_paused(_img):
                say("  ⟳ 闸0：这一屏是战斗/暂停态 ⇒ 先放弃行动退出来，再谈进详情页")
                _lb0 = leave_battle(f"{args.tag}_gate0_{_k}")
                if not _lb0["left"]:
                    say("  ⚠️ 闸0 没能把游戏带出战斗 ⇒ 不再盲点，交给闸1/上层如实记账")
                    break
                pre = shot(f"{args.tag}_pre")
                continue
            if _kind in ("clear", "fail", "maybe-clear"):
                say("  ⟳ 上一局的结算页（%s）⇒ 点中央收尾" % _kind)
                sh("shell", "input", "tap", "640", "640")
                wait_screen_change(pre, timeout=8.0)      # 条件轮询（原来盲等 4s）
                pre = shot(f"{args.tag}_pre")
                continue
            if is_detail_page(_img):
                break
            # 关卡地图上找目标关卡节点，点它进详情页（用 OCR 给的真实坐标，别点画面中央）
            _hits = _ocr_hits(_img)
            _node = next(((x, y) for x, y, t in _hits if _stage_code in t and y > 140), None)
            if _node:
                say("  ⟳ 关卡地图 ⇒ 点 %s 节点 (%d,%d)" % (_stage_code, _node[0], _node[1] - 20))
                sh("shell", "input", "tap", str(_node[0]), str(_node[1] - 20))
                wait_screen_change(pre, timeout=8.0)      # 条件轮询（原来盲等 4s）
                pre = shot(f"{args.tag}_pre")
                continue
            say("  ⟳ 当前不在关卡详情页 ⇒ 点中央推进一次，重看")
            sh("shell", "input", "tap", "640", "400")
            wait_screen_change(pre, timeout=5.0)          # 条件轮询（原来盲等 3s）
            pre = shot(f"{args.tag}_pre")
        except Exception as e:
            say("  （自动收尾跳过：%s）" % e)
            break

    # ── 闸 1：开局前闸（2026-09-21 加）───────────────────────────────────────
    # 旧版只把 page_kind 打印出来、unknown 也照跑 ⇒ "没在关卡详情页/难度没切过去" 静默放过，
    # 那种局跑完拿到的数据不可用（MT-EX-1 突袭 5 连败里就有这种）。现在：判不出详情页就不起 MAA。
    _pre_img = None
    try:
        import cv2 as _cv2
        _pre_img = _cv2.imread(str(pre))
        _detail, _raid_page = is_detail_page(_pre_img), is_raid_page(_pre_img)
        _btn = "开始突袭" if args.raid else "开始行动"
        _btn_ok = _btn in _ocr_text(_pre_img).replace(" ", "")
        if not (_detail and _btn_ok):
            say("  ✗ 闸1 拦下：这帧不像关卡详情页（detail=%s / 按钮「%s」=%s）⇒ 不起 MAA、不耗理智"
                % (_detail, _btn, _btn_ok))
            say("  ⇒ 请先 tools/bi_goto.py --stage <关> [--want 突袭] 把页面点到位；本次标「无效-未在详情页」")
            # **闸1 失败也要收尾**（2026-09-24 加）：闸1 最常见的触发原因就是"上一局把游戏留在了
            #   战斗/暂停态"——CV-EX-8 突袭 51786 的第一次正是如此（detail=False，而屏上正在演习战斗）。
            #   不带出来，紧接着的重导航/下一局还会照样失败（真机连废了 4 次导航）。
            _lb1 = _cleanup_if_battle(f"{args.tag}_gate1", _pre_img)
            if _lb1.get("left_battle") is not None:
                say("  ⟳ 收尾：%s" % ("已离开战斗" if _lb1["left_battle"] else "⚠ 仍可能在战斗里（后面会失败）"))
            say("=== 完毕（无效局） ===")
            return finish(3, valid=False, reason="gate1-not-detail", **_lb1)
        if args.raid and not _raid_page:
            say("  ✗ 闸1 拦下：--raid 但页面上看不到「突袭模式/开始突袭/附加条件」⇒ 难度可能没切过去")
            say("=== 完毕（无效局） ===")
            return finish(3, valid=False, reason="gate1-raid-page",
                          **_cleanup_if_battle(f"{args.tag}_gate1b", _pre_img))
        say("  ✓ 闸1 通过：详情页 + 按钮「%s」%s" % (_btn, "（突袭页）" if _raid_page else ""))
    except Exception as e:
        say("  （闸1 自检异常，按放行处理：%s）" % e)

    # **演习模式：确认站在关卡详情页之后才点「演习」**（顺序修复，见文件开头的说明）。
    #   点完演习人就离开详情页了，所以必须在闸1 通过之后做这一步。
    if _DRILL:
        if args.raid:
            say("  ⚠️ 同时给了 `--drill` 与 `--raid`：突袭演习实测（2026-09-24 CV-EX-8）两份轴都跑不出"
                "结算页、还会把游戏留在战斗里 ⇒ **别这么用**（event_sweep 对突袭已默认不跑演习）")
        # **点演习之前先尽力读「演习券」**（2026-09-24 加；用户口径：演习只烧演习券、不烧理智、不产收益）：
        #   读数进日志与 --json-out，让"这局演习用了券没有/券够不够"可审计。读不到就是 null。
        _tk = read_drill_tickets(tag=f"{args.tag}_tk")
        res["drill_tickets"] = _tk["tickets"]
        say("  演习券读数：%s（%s）"
            % (_tk["tickets"] if _tk["tickets"] is not None else "null（读不到 ⇒ 照常演习，不瞎猜）",
               (_tk["text"] or "")[:100]))
        sh("shell", "input", "tap", "1003", "659")
        say("  已点「演习」(1003,659)（不耗理智、不计通关的演练模式）")
        # **确认真的进了演习流程**（task-17 issue#1：不能盲等 3s 就当成了 —— 点空了的话这一局
        #   跑出来的东西不可信、还会让上层误判"演习通道坏"）。判据：详情页特征消失（「开始行动/
        #   推荐平均等级」不在了）或已经出现战斗 HUD。最多等 12s，失败只警告（闸2/闸3 还会兜）。
        _t_d = time.time()
        _confirmed = False
        while time.time() - _t_d < 12:
            time.sleep(0.6)
            try:
                import cv2 as _cv2
                _im = _cv2.imread(str(shot(f"{args.tag}_drillchk")))
                if _im is None:
                    continue
                if in_battle(_im) or not is_detail_page(_im):
                    _confirmed = True
                    break
            except Exception as _e:                       # noqa: BLE001
                say("  （演习确认轮询异常：%s）" % str(_e)[:60])
                break
        say(("  ✓ 演习已确认进入（%.1fs）" % (time.time() - _t_d)) if _confirmed
            else ("  ⚠️ 点了「演习」但 %.0fs 内没确认离开详情页 ⇒ 这局可疑（闸2/闸3 会兜）"
                  % (time.time() - _t_d)))
        # **「演习券不足」的证据**（2026-09-24 加）：点完演习再读一次 —— 若读数为 0 或屏上写着
        #   「演习券不足」，那就是**没券**，不是"演习通道坏了"。记进 JSON 让上层走老路径（真打），
        #   而不是被误记成 `drill_broken`（那会白烧一次真打）。
        _tk2 = read_drill_tickets(tag=f"{args.tag}_tk2")
        if _tk2["tickets"] is not None:
            res["drill_tickets"] = _tk2["tickets"]
        if _tk["low"] or _tk2["low"]:
            res["drill_tickets_low"] = True
            say("  ⚠️ 演习券不足（读数=%s / 屏上有「不足」字样）⇒ **这不是演习通道坏，是没券**"
                % (_tk2["tickets"] if _tk2["tickets"] is not None else "null"))

    rec_secs = max(1, min(int(args.record), 180))
    mp4 = f"/sdcard/{args.tag}.mp4"
    sh("shell", "rm", "-f", mp4)
    rec = None
    if args.record_after > 0:
        say("  延迟 %ds 再开录屏…" % args.record_after)
    env0 = None

    def start_rec():
        nonlocal rec
        rec = subprocess.Popen([ADB, "-s", DEV, "shell", "screenrecord", "--time-limit", str(rec_secs),
                                "--bit-rate", "4000000", "--size", "1280x720", mp4],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    if args.record_after <= 0 and not args.no_record:
        start_rec()
        time.sleep(2)
        say("  录屏已开 ✓（%ds）" % rec_secs)
    if args.no_record:
        say("  （--no-record：不录屏 —— 演习试轴不需要录像，省 180s + 一次 50MB 拉取）")

    env = {**os.environ,
           "MAA_DATA_DIR": str(PROJ / "runtime/maa_cli/data"),
           "MAA_CONFIG_DIR": str(PROJ / "runtime/maa_cli/config"),
           "MAA_STATE_DIR": str(PROJ / "runtime/maa_cli/state"),
           "MAA_CACHE_DIR": str(PROJ / "runtime/maa_cli/cache")}
    job = str(pathlib.Path(args.job).resolve())
    runlog = OUT / f"{args.tag}.run.log"
    argv = [str(MC), "copilot", job, "-p", "ce6", "--batch", "--formation", f"--log-file={runlog}"]
    if getattr(args, "raid", False):
        argv += ["--raid", "raid"]
    rec_timer = None
    if args.record_after > 0:
        import threading

        def _late():
            start_rec()
            say("  录屏已开（延迟 %ds 后开始，%ds）✓" % (args.record_after, rec_secs))

        rec_timer = threading.Timer(args.record_after, _late)
        rec_timer.daemon = True
        rec_timer.start()
    t0 = time.time()
    # **唯一 spawn MAA 的入口**（`spawn_maa()` 计数；闸拦下时走不到这里 —— 见 main 开头的干员闸）
    proc = spawn_maa(argv, env=env)
    leak_seen = {"t": None}
    battle_seen = {"t": None}      # 闸2：首次看到战斗 HUD 的时刻（相对 t0）

    if args.abort:
        import threading

        def _watch():
            import cv2 as _cv2
            import numpy as _np
            seq = OUT / f"{args.tag}_seq"
            seq.mkdir(exist_ok=True)
            last = None
            n = 0
            while proc.poll() is None:
                time.sleep(2)
                tmp = OUT / f"{args.tag}_watch.png"
                with open(tmp, "wb") as fh:
                    subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"],
                                   stdout=fh, timeout=60)
                img = _cv2.imread(str(tmp))
                if img is None:
                    continue
                # 每 2 秒留一帧，漏怪前后可直接回看
                n += 1
                try:
                    _cv2.imwrite(str(seq / ("%03d.png" % n)), img)
                except Exception:
                    pass
                v = read_life(img)
                # 闸2（进战斗闸，2026-09-21）：只认战斗 HUD 独有的信号 —— ENEMY 计数 "N/M" 或费用框数字。 判据 2026-09-24 18:3x 收紧成「干净 token」（`hud_roi_hit()`）——「任意数字」会在编队页被 `类2O` 骗过。
                # ⚠️ 不能用「剩余可放置角色」当判据：编队页也有这四个字（CE-6 踩过）。
                if battle_seen["t"] is None and in_battle(img):
                    battle_seen["t"] = time.time() - t0
                    say("  ✓ 闸2 通过：t≈%.0fs 起已在战斗界面" % battle_seen["t"])
                if v is None:
                    continue
                if last is not None and v < last:
                    leak_seen["t"] = time.time() - t0
                    # **给上层一个"这局确定到不了 3★"的信号**（2026-09-24，task-15/17 实测需要）：
                    #   演习试轴时"进战斗后漏怪被掐"= **这份轴不行**（零理智、且是有效结论），
                    #   跟"通道坏了/没跑成"完全不是一回事 —— 上层据此换轴，而不是退回"盲目真打"。
                    res["leak"] = True
                    res["leak_life"] = [int(last), int(v)]
                    res["leak_s"] = round(leak_seen["t"], 1)
                    say("  ⚠ 检测到漏怪（生命 %d → %d，t≈%.0fs）⇒ 按三星目标直接掐局" % (last, v, time.time() - t0))
                    # 连拍 3 张：漏怪瞬间的画面，便于事后看是哪只、从哪个口进来
                    for k in range(3):
                        shot(f"{args.tag}_leak{k}")
                        time.sleep(0.4)
                    say("  ⇒ 已连拍 3 张漏怪瞬间画面（%s_leak0..2.png）" % args.tag)
                    # 退出战斗：点左上齿轮 → 弹「是否放弃行动？」 → 点「放弃行动」
                    # （2026-09-21 实测：齿轮 (80,53)、放弃行动 (960,574)。**不能只点死坐标** ——
                    #  v6/v8 两局都出现「日志说已放弃、实际游戏还停在暂停态」，因为那一下点到别的
                    #  东西上（弹了单位详情/只打开了暂停）。改成 OCR 找「放弃行动」按钮并重试。）
                    # 2026-09-24：改走 `leave_battle()`（同一份实现，且**会验证真的出来了**）。
                    _lb = leave_battle(f"{args.tag}_abort")
                    res.update({"left_battle": _lb["left"], "battle_after": not _lb["left"],
                                "abandon_hits": _lb["hits"]})
                    say("  ⇒ 已点「放弃行动」退出关卡（返还理智；命中 %d 次，已离开战斗=%s）"
                        % (_lb["hits"], _lb["left"]))
                    proc.kill()
                    return
                last = v

        th = threading.Thread(target=_watch, daemon=True)
        th.start()

    try:
        out, _ = proc.communicate(timeout=args.job_timeout)
        tail = [l for l in (out or "").splitlines() if "Copilot" in l or "Error" in l]
        say("  作业结束：%.0fs ／ %s" % (time.time() - t0, " ／ ".join(tail[-2:]) or "（无摘要）"))
    except subprocess.TimeoutExpired:
        proc.kill()
        say("  ✗ 作业超过 %ds 未结束 ⇒ 掐掉" % args.job_timeout)
        if rec:
            rec.kill()
        sh("shell", "pkill", "-f", "screenrecord")
        # **超时也要收尾**（2026-09-24 加）：MAA 被掐时游戏多半还在战斗/暂停里 —— 不把它带出来，
        #   下一次导航就会像 CV-EX-8 那串一样连着失败、把关卡误标 noreach。
        _lbt = leave_battle(f"{args.tag}_timeout")
        # 超时局也要把"漏没漏怪 / 进没进战斗"带上（漏怪被掐 = 这是"轴不行"的有效结论）
        return finish(4, valid=False, reason="job-timeout",
                      leak=bool(res.get("leak")), battle=battle_seen["t"] is not None,
                      battle_s=(round(battle_seen["t"], 1) if battle_seen["t"] is not None else None),
                      left_battle=_lbt["left"], battle_after=not _lbt["left"],
                      abandon_hits=_lbt["hits"])

    if rec_timer is not None:
        rec_timer.cancel()
    if not args.no_record:
        sh("shell", "pkill", "-SIGINT", "screenrecord")
        time.sleep(4)
        q = sh("shell", "ls", "-la", mp4)
        if "No such file" in (q.stdout + q.stderr):
            say("  ✗ 录像没落盘")
        else:
            subprocess.run([ADB, "-s", DEV, "pull", mp4, str(OUT / f"{args.tag}.mp4")],
                           capture_output=True, timeout=300,
                           env={**os.environ, "MSYS_NO_PATHCONV": "1"}, errors="replace")
            dst = OUT / f"{args.tag}.mp4"
            say("  录像：%s（%.1f MB）" % (dst, dst.stat().st_size / 1048576 if dst.exists() else 0))

    p = shot(f"{args.tag}_result")
    dur = time.time() - t0
    valid = None      # 闸3 结论：True=有效（可以拿去做打法分析）/ False=无效（别分析）
    try:
        import cv2
        from ce6_star_count import page_kind, star_blobs
        img = cv2.imread(str(p))
        kind = page_kind(img)
        stars = len(star_blobs(img)) if kind in ("clear", "maybe-clear") else 0
        # **战斗态判据用 `in_battle_or_paused()`**（2026-09-24 修）：只认两个 ROI 的话，
        #   「演习中 + PAUSE」那一屏会被判成"不在战斗"，收尾就漏了（真机 51786 的 result 帧就是它）。
        still_battle = in_battle_or_paused(img)
        err_in_log = ("Error" in " ".join(tail)) or ("Network error" in " ".join(tail))
        say("  结算：页面类型=%s  星级=%s%s" % (kind, stars if kind in ("clear", "maybe-clear") else 0,
                                             "  ⚠结果帧仍是战斗 HUD" if still_battle else ""))
        res.update({"kind": kind, "stars": stars if kind in ("clear", "maybe-clear") else 0,
                    "dur_s": round(dur, 1), "still_battle": bool(still_battle),
                    # 「这局确定到不了 3★」的证据：漏怪被掐（leak）/ 进过战斗（battle）
                    "leak": bool(res.get("leak")), "battle": battle_seen["t"] is not None,
                    "battle_s": (round(battle_seen["t"], 1) if battle_seen["t"] is not None else None)})
        # ── 闸3：时长/结算闸（2026-09-21 加）────────────────────────────────
        # 由来：MAA 报 TaskChainCompleted 只是"它脚本里的动作做完了"，**不等于这局打完了**。
        # MT-EX-1 突袭那 5 局就是 106~146s 报 Completed、结算页 unknown、结果帧还在战斗
        # （随后才真正以「任务失败」收场）——这种局绝不能进"打法失败"统计。
        if battle_seen["t"] is None:
            valid = False
            say("  ✗ 闸3 判「无效-未进战斗」：整局没看到战斗 HUD（MAA 可能没进关卡/没切难度）")
        elif kind not in ("clear", "maybe-clear", "fail"):
            valid = False
            say("  ✗ 闸3 判「无效-未结算」：MAA 结束（%.0fs）时游戏还没出结算页（页面类型=%s）" % (dur, kind))
            say("     ⇒ MAA 收工 ≠ 这局打完；这局的数据不可用于打法分析，请重跑或稍后再读结算")
        elif err_in_log:
            valid = False
            say("  ✗ 闸3 判「无效-运行报错」：MAA 输出含 Error ⇒ 这局不算数")
        else:
            valid = True
            say("  ✓ 闸3 判「有效」：已在结算页（%s，%.0fs）" % (kind, dur))
    except Exception as e:
        say("  （结算判读失败：%s）" % e)
        # 连判读都失败了 ⇒ 更要确认没把游戏留在战斗里（这一帧读不到就整屏 OCR 自查一次，
        # 仍判不了才会真去动作；见 `_cleanup_if_battle`）。
        res.update(_cleanup_if_battle(f"{args.tag}_err", None))
    # ── **出口收尾：绝不把游戏留在战斗/暂停里**（2026-09-24 加）─────────────────
    #   起因见 `leave_battle()` 的注释：51786 那份演习的 result 帧**仍是战斗 HUD**（演习中+PAUSE），
    #   旧代码只打印一句警告就 exit 5 ⇒ 之后 4 次导航全废、CV-1/2/3 被误标 noreach。
    if res.get("still_battle"):
        say("  ⟳ 收尾：结果帧还在战斗/暂停态 ⇒ 把游戏带出战斗（不带出来，后面的导航全会失败）")
        res.update(_cleanup_if_battle(f"{args.tag}_exit", None))
        if res.get("left_battle"):
            say("  ⇒ 收尾后确认已离开战斗")
    if valid is False:
        say("=== 完毕（无效局，请勿用于打法结论） ===")
        return finish(5, valid=False, reason="gate3-invalid")
    say("=== 完毕 ===")
    return finish(0, valid=True, reason="ok")


if __name__ == "__main__":
    raise SystemExit(main())
