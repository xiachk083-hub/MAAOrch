"""第二层：纯统计聚合（不需要 AI）——失败模式 / 成功规律 / 替代效果。"""
import json, pathlib, sys, collections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools.runs import load_runs

PROJ = pathlib.Path(__file__).resolve().parent.parent
OUT = PROJ / "data" / "patterns.json"


def _bucket(failed_at) -> int:
    try:
        return int(str(failed_at).replace("T+", "")) // 30 * 30
    except Exception:
        return -1


def aggregate_failures(runs: list[dict]) -> list[dict]:
    pats = {}
    for r in runs:
        if r.get("result") != "fail":
            continue
        b = _bucket(r.get("failed_at"))
        key = f"{r.get('location')}_T{b}"
        p = pats.setdefault(key, {"location": r.get("location"),
                                  "time_window": f"T+{b}~T+{b+30}" if b >= 0 else "unknown",
                                  "count": 0, "accounts": [], "jobs": []})
        p["count"] += 1
        p["accounts"].append(r.get("account"))
        p["jobs"].append(r.get("job_id"))
    return sorted(pats.values(), key=lambda x: -x["count"])


def aggregate_success(runs: list[dict]) -> list[tuple]:
    first = collections.Counter()
    for r in runs:
        if r.get("result") != "win" or not r.get("trace_file"):
            continue
        p = PROJ / r["trace_file"]
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                a = (json.loads(ln)["decision"] or {}).get("action", {}) or {}
            except Exception:
                continue
            if a.get("category") == "deploy":
                first[a.get("operator", "?")] += 1
                break
    return first.most_common()


def aggregate_replacements(runs: list[dict]) -> dict:
    stats = {}
    for r in runs:
        for orig, repl in (r.get("replacements") or {}).items():
            s = stats.setdefault(f"{orig}→{repl}", {"attempts": 0, "wins": 0})
            s["attempts"] += 1
            if r.get("result") == "win":
                s["wins"] += 1
    for s in stats.values():
        s["rate"] = round(s["wins"] / max(s["attempts"], 1), 2)
    return stats


if __name__ == "__main__":
    runs = load_runs()
    rep = {"failures": aggregate_failures(runs), "success_first_deploy": aggregate_success(runs),
           "replacements": aggregate_replacements(runs)}
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    hot = [p for p in rep["failures"] if p["count"] > 5]
    print(f"runs={len(runs)} | 失败模式 {len(rep['failures'])} 类，其中 count>5 的高频 {len(hot)} 类")
    for p in rep["failures"][:3]:
        print(f"  {p['location']} {p['time_window']} × {p['count']}")
    print("首条部署TOP:", rep["success_first_deploy"][:3])
    print("替代效果:", rep["replacements"])
    print("→", OUT)
