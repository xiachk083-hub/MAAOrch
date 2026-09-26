"""人工抽查用：给定账号（本地 box 或目标机导出），输出候选作业的缺员/软需求明细与建议。

用法: python tools/spot_check.py [账号名]     # 省略则对全部可用 box 打印摘要
"""
import json, glob, pathlib, sys
from collections import Counter

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
from tools.match_candidates import build_box_index, rank_candidates, load_jobs


def load_box(name: str | None):
    cand = []
    if name:
        cand += glob.glob(str(PROJ / "data" / "operbox_batch" / f"{name}.json"))
        cand += glob.glob(str(PROJ / "data" / f"{name}*.json"))
    if not cand:
        cand = sorted(glob.glob(str(PROJ / "data" / "operbox_batch" / "*.json"))) or \
               sorted(glob.glob(str(PROJ / "data" / "operbox_pasted_0915*.json")))
    return [(pathlib.Path(f).stem, json.loads(pathlib.Path(f).read_text(encoding="utf-8"))) for f in cand]


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else None
    jobs = load_jobs()
    for acc, raw in load_box(name):
        box = build_box_index(raw)
        cands = rank_candidates(box, jobs)
        print(f"\n=== {acc}（{len(box['owned_names'])} 人）===")
        for c in cands:
            print(f"  {c['job_id']:<8} [{c['match']:<8}{c['partial_type'] or ''}] "
                  f"需求{c['n_reqs']} 动作{c.get('n_actions','?')} 缺{len(c['missing'])} 软{len(c['soft'])} → {c['action']}")
            if c["missing"]:
                print(f"       硬缺: {c['missing']}")
            if c["soft"]:
                cnt = Counter(c["soft"])
                disp = ", ".join(f"{k}×{v}" if v > 1 else k for k, v in cnt.items())
                print(f"       软需求(待判定): {disp}")
        pick = [c for c in cands if c["match"] == "exact"] or \
               [c for c in cands if c["match"] == "partial"]
        if pick:
            best = pick[0]
            print(f"  ★ 建议: 用 {best['job_id']}（{best['action']}，缺 {len(best['missing'])}，"
                  f"软需求 {best['soft'] or '无'}）")
        else:
            print("  ★ 建议: 无可用候选 → 进队列人工处理")


if __name__ == "__main__":
    main()
