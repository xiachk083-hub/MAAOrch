"""闭环体检：**一圈到底转了没有** —— 一条命令看三件事（修改树 / 规则库 / 反哺能不能套上）。

由来（2026-09-16 用户）："闭环的零件全做好了，但从未真正转过一圈……你改完，闭环才算启动"。
人改完一版之后要看的三件事，不该靠人记着挨个翻文件 —— 落成脚本：

    python tools/loop_check.py 9036 act54side            # 整个活动
    python tools/loop_check.py 9036 act54side SR-1       # 某一关
    python tools/loop_check.py 9036 act54side SR-1 --build   # 顺带重建规则库

输出四段，每段都直接给结论 + **下一步该敲什么**：
  ① 修改树：每关的节点（谁改的、改了几处、备注）—— 一个 `human` 节点都没有 = **闭环还没启动**
  ② 规则库：`knowledge/edits.json` 里这一关的规则条数与 `count`（谁贡献的看完整档）
  ③ 反哺预演：`--dry-run` 那套逻辑跑一遍，报"能套上几条"（**只读，不写任何文件**）
  ④ 结论一句话 + 下一步命令

**只读**：除了 `--build` 之外不写任何东西（`--build` 也只写规则库那两份 json）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
PROJ = _HERE.parent
sys.path.insert(0, str(_HERE))
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

import rules_db as RDB          # noqa: E402
import versions as VSN          # noqa: E402
import workspace as WSP         # noqa: E402


def check_stage(account: str, activity: str, key: str, min_count: int = 2,
                quiet: bool = False) -> dict:
    """一关的闭环状态（只读）。返回 dict 供上层汇总。"""
    sd = WSP.find_stage_dir(account, activity, key)
    if not sd:
        return {"ok": False, "key": key, "error": "工作区里没有这一关"}
    st = json.loads((sd / "stage.json").read_text(encoding="utf-8"))
    stage = st.get("stage_id")
    tree = VSN.load_tree(sd)
    humans = [m for m in tree if m.get("author") == "human"]
    n_changes = sum(m.get("n_changes", 0) for m in humans)
    db = RDB.load(full=True)
    rules = (db.get("rules") or {}).get(stage) or []
    usable = RDB.rules_for(stage, min_count, db)

    # 反哺预演（不落盘）—— 复用 feedback 的真实现，别在这里另写一套判断
    pre = {"n_rules": len(usable), "n_applied": 0, "reason": None}
    if usable:
        try:
            import feedback as FB
            base, base_id = FB.pick_base(sd)
            _job, log = FB.apply_rules(base, usable, None)
            pre = {"n_rules": len(usable), "n_applied": sum(1 for x in log if x["applied"]),
                   "base": base_id,
                   "reason": None if any(x["applied"] for x in log)
                             else "规则都匹配不上当前基版（作业状态与规则的 from 不符）"}
        except SystemExit as e:
            pre = {"n_rules": len(usable), "n_applied": 0, "reason": str(e)}
        except Exception as e:
            pre = {"n_rules": len(usable), "n_applied": 0,
                   "reason": "%s: %s" % (type(e).__name__, e)}

    if not quiet:
        print("  %s（%s %s）" % (st.get("code") or key, stage, st.get("name") or ""))
        if not tree:
            print("    ① 修改树：**空的**（连 AI 根节点都没有 —— 先重生成这一关）")
        else:
            print("    ① 修改树：%d 个节点 ｜ 人改 %d 个、共 %d 处%s"
                  % (len(tree), len(humans), n_changes,
                     ("（" + "；".join("%s: %s" % (h["id"], (h.get("note") or "无备注")[:24])
                                      for h in humans[-3:]) + "）") if humans else ""))
        print("    ② 规则库：%d 条（≥%d 次的 %d 条）%s"
              % (len(rules), min_count, len(usable),
                 ("　" + "；".join("×%d %s %s→%s" % (r["count"], r["type"],
                                                    json.dumps(r["from"], ensure_ascii=False),
                                                    json.dumps(r["to"], ensure_ascii=False))
                                   for r in usable[:3])) if usable else ""))
        print("    ③ 反哺预演：能套上 %d/%d 条（基版 %s）%s"
              % (pre["n_applied"], pre["n_rules"], pre.get("base") or "?",
                 ("　← " + pre["reason"]) if pre.get("reason") else ""))
        state = ("闭环已转（有人改 + 有规则 + 反哺能套上）" if (humans and usable and pre["n_applied"])
                 else "闭环**还没转起来**" if not humans
                 else "有人改但规则还不够" if not usable
                 else "有规则但套不上当前基版")
        print("    ④ %s" % state)
    return {"ok": True, "key": key, "stage_id": stage, "code": st.get("code"),
            "n_nodes": len(tree), "n_human": len(humans), "n_human_changes": n_changes,
            "n_rules": len(rules), "n_usable": len(usable), "preview": pre,
            "started": bool(humans and usable and pre["n_applied"]),
            "humans": [{"id": h["id"], "note": h.get("note") or "",
                        "n_changes": h.get("n_changes", 0)} for h in humans]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="闭环体检：修改树 / 规则库 / 反哺预演")
    ap.add_argument("account")
    ap.add_argument("activity")
    ap.add_argument("stage", nargs="?", default=None, help="代号或 stageId；不给=整个活动")
    ap.add_argument("--min-count", type=int, default=2)
    ap.add_argument("--build", action="store_true", help="先重建规则库（写 knowledge/edits.json）")
    a = ap.parse_args(argv)

    if a.build:
        db = RDB.collect([a.account])
        out = RDB.save(db)
        print("规则库已重建：%d 个节点（人改 %d）→ 变化 %d 处"
              % (db["stats"]["nodes"], db["stats"]["human_nodes"], db["stats"]["changes"]))
        print("  → %s（无标识）｜ %s（含 sources）"
              % (pathlib.Path(out["public"]).relative_to(PROJ),
                 pathlib.Path(out["full"]).relative_to(PROJ)))

    base = WSP.WS / a.account / a.activity
    if not base.is_dir():
        raise SystemExit("工作区里没有这个活动：%s / %s" % (a.account, a.activity))
    keys = ([a.stage] if a.stage else
            [d.name for d in sorted(base.glob("*/")) if (d / "stage.json").is_file()])
    if not keys:
        raise SystemExit("%s 下没有关卡目录" % base.relative_to(PROJ))
    print("闭环体检：%s / %s（%d 关，判据 min-count=%d）" % (a.account, a.activity, len(keys),
                                                          a.min_count))
    res = [check_stage(a.account, a.activity, k, a.min_count) for k in keys]
    ok = [r for r in res if r.get("ok")]
    started = [r for r in ok if r.get("started")]
    print("\n合计：%d 关可查 ｜ 节点 %d（其中人改 %d、共 %d 处）｜ 规则 %d 条 ｜ **闭环已转的关 %d/%d**"
          % (len(ok), sum(r["n_nodes"] for r in ok), sum(r["n_human"] for r in ok),
             sum(r["n_human_changes"] for r in ok), sum(r["n_rules"] for r in ok),
             len(started), len(ok)))
    if not any(r["n_human"] for r in ok):
        print("\n下一步（只有人能做的这一步）：")
        print("  1) 起服务：python tools/ws_server.py --open")
        print("  2) 打开 %s/%s/<关卡>/index.html，看 AI 的 v1_ai，挪位/换朝向/换人"
              % (a.account, a.activity))
        print("  3) 写一句版本备注 → 点「保存为新版本」")
        print("  4) 回来跑：python tools/loop_check.py %s %s --build" % (a.account, a.activity))
    elif not started:
        print("\n有改动但闭环还没合上：看上面每关的 ③（为什么规则套不上）。")
        print("  规则要能套上，得让**同一个位置/朝向的修正**出现 ≥%d 次（同一个人改两版、" % a.min_count
              + "或两个账号都这么改）。")
    else:
        print("\n闭环已转：可以直接跑反哺生成下一版 ——")
        print("  python tools/feedback.py %s %s <关卡> --min-count %d" % (a.account, a.activity,
                                                                        a.min_count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
