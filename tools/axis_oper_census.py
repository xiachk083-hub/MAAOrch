#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**轴库干员名单普查**（离线，不连设备；2026-09-24 夜）——给「干员拥有性闸」当分母与回归尺子。

为什么要有它（审计 `runtime/_audit_axis_opers_20260924.md`）：
  · 轴库**两种形状并存**：包装型 `{status_code, data:{content:"<JSON 字符串>"}}`（内层要**二次
    parse**）与扁平型（`opers` 在顶层），**且按路径严格分裂** —— 主线两目录全扁平，包装型 80 份
    全在事件路径 `*/jobs`；⇒ 只读顶层 `opers` 的老读取器**恰好且只在事件路径上瞎掉**（91% 空表）。
  · 数量口径按**代码真正读的目录**算（生产分母 **1039** = `story_jobs_big` 785 + `story_jobs` 166
    + 事件 `*/jobs` 88）；`*_cache*` 是缓存不是轴，`ad_jobs` 没有任何代码按轴目录读它 ⇒ **不入选**。

用法:
    python tools/axis_oper_census.py                 # 生产口径（1039 份）
    python tools/axis_oper_census.py --dirs a b c    # 指定目录
    python tools/axis_oper_census.py --json-out x.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                                 # noqa: BLE001
    pass
ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
SA = ROOT / "tools" / "stage_attempt.py"


def _load_shared():
    """复用 `tools/stage_attempt.py` 里的**形状感知读取器**（唯一实现，别在这里再写一份）。"""
    spec = importlib.util.spec_from_file_location("_maaorch_stage_attempt", SA)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 %s" % SA)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def default_dirs() -> list:
    """生产口径的轴目录：主线两目录 + 事件 `*/jobs`（按名字排序，**不含** `*_cache*`）。"""
    out = [ROOT / "runtime" / "plans" / "story_jobs_big", ROOT / "runtime" / "plans" / "story_jobs"]
    out += sorted(p for p in (ROOT / "runtime" / "plans").glob("*/jobs") if p.is_dir())
    return out


def old_reader_opers(path: pathlib.Path) -> list:
    """**老读取器**（只读顶层 `opers`，就是审计里那个 fail-open 版本）—— 只为对比着报。"""
    try:
        j = json.loads(path.read_text(encoding="utf-8"))
        return [str(o.get("name")) for o in (j.get("opers") or [])
                if isinstance(o, dict) and o.get("name")]
    except Exception:                                             # noqa: BLE001
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", nargs="*", default=[], help="要普查的目录（默认＝生产口径三处）")
    ap.add_argument("--json-out", default="", help="把逐目录统计写成 JSON")
    a = ap.parse_args()
    sa = _load_shared()
    own, box_ok = sa.owned_opers()

    dirs = [pathlib.Path(p) for p in a.dirs] or default_dirs()
    rows, tot, totsum = [], {}, {}
    print("轴库干员名单普查（形状感知读取器；只从 opers/groups/actions 取名，**不读 doc**）")
    print("盒子快照：%s（own_opers=%d 名，readable=%s）" % (sa.OPER_BOX, len(own), box_ok))
    print("─" * 118)
    print("%-42s %5s %5s %5s %6s %8s %8s %6s %6s %6s %6s" %
          ("目录", "总数", "扁平", "包装", "读出数", "老读取器", "三方全空", "放行", "环境不兼", "组unsat", "数据缺口"))
    for d in dirs:
        if not d.is_dir():
            continue
        c = {"total": 0, "wrap": 0, "flat": 0, "bad": 0, "readable": 0, "old": 0,
             "unreadable": 0, "allow": 0, "incompat": 0, "group_unsat": 0, "data_gap": 0,
             "huang_raw": 0, "huang_blocked": 0}
        for p in sorted(d.glob("*.json")):
            c["total"] += 1
            txt = ""
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except Exception:                                     # noqa: BLE001
                pass
            req = sa.axis_requirements(p)
            sh = req.get("shape")
            c["wrap" if sh == "wrap" else ("flat" if sh == "flat" else "bad")] += 1
            if req.get("readable"):
                c["readable"] += 1
            if old_reader_opers(p):
                c["old"] += 1
            g = sa.oper_gate_verdict(req, own)
            if not req.get("readable"):
                c["unreadable"] += 1
                c["data_gap"] += 1
            elif g["verdict"] == "allow":
                c["allow"] += 1
            elif g["verdict"] == "axis-group-unsat":
                c["incompat"] += 1
                c["group_unsat"] += 1
            else:
                c["incompat"] += 1
            if "煌" in txt:
                c["huang_raw"] += 1
                if not g["allow"]:
                    c["huang_blocked"] += 1
        rows.append({"dir": str(d.relative_to(ROOT)), **c})
        for k, val in c.items():
            totsum[k] = totsum.get(k, 0) + val
        print("%-42s %5d %5d %5d %6d %8d %8d %6d %6d %6d %6d" %
              (str(d.relative_to(ROOT)), c["total"], c["flat"], c["wrap"], c["readable"], c["old"],
               c["unreadable"], c["allow"], c["incompat"], c["group_unsat"], c["data_gap"]))
    print("─" * 118)
    print("%-42s %5d %5d %5d %6d %8d %8d %6d %6d %6d %6d" %
          ("合计（生产口径）", totsum.get("total", 0), totsum.get("flat", 0), totsum.get("wrap", 0),
           totsum.get("readable", 0), totsum.get("old", 0), totsum.get("unreadable", 0),
           totsum.get("allow", 0), totsum.get("incompat", 0), totsum.get("group_unsat", 0),
           totsum.get("data_gap", 0)))
    print()
    print("· **三方全空（真数据缺口）= %d** —— 生产口径应为 **0**；非 0 就是告警（那份轴文件没下全）"
          % totsum.get("unreadable", 0))
    print("· 老读取器（只读顶层 opers）只能读出 %d/%d 份 —— 差值 %d 份全是**形状盲区**，"
          "包装型全部集中在事件 `*/jobs`（这正是「闸会恰好在事件路径上瞎掉」的证据）"
          % (totsum.get("old", 0), totsum.get("total", 0),
             totsum.get("readable", 0) - totsum.get("old", 0)))
    print("· 闸的判决：放行 %d ／ 环境不兼容 %d（其中某组一个成员都没有 %d）／ 数据缺口 %d"
          " —— 被拦下的**不写 noreach、不计未成、不降权**"
          % (totsum.get("allow", 0), totsum.get("incompat", 0), totsum.get("group_unsat", 0),
             totsum.get("data_gap", 0)))
    print("· 全文含「煌」的轴 %d 份（**其中 %d 份会被闸拦下**；只在 `doc` 说明里提到「煌」的那些轴"
          "**不在需求里**，闸不拦 —— 见 `cvex_jobs/CV-EX-8_27879_patched.json`）"
          % (totsum.get("huang_raw", 0), totsum.get("huang_blocked", 0)))
    if a.json_out:
        p = pathlib.Path(a.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"box": str(sa.OPER_BOX), "n_box": len(own), "box_readable": box_ok,
                                 "rows": rows, "total": totsum}, ensure_ascii=False, indent=1),
                     encoding="utf-8")
        print("· 已写 %s" % p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
