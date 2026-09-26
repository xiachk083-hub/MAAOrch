"""拉取 GA-EX-2 候选详情并落盘（2026-09-16）。

关键：prts 的作业内容在 `data.content` 里，可能是 JSON 字符串 / 字符串列表 / dict 三种形态。
落盘前校验必备字段（stage_name + actions 非空），按普通/突袭分目录。
"""
import json, pathlib, sys, time, urllib.request, collections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools.batch_index import parse_requirements

CAND = pathlib.Path("data/copilot/ga16_user_pick.json")
OUT_N = pathlib.Path("data/copilot/act16side_ex02")
OUT_H = pathlib.Path("data/copilot/act16side_ex02_hard")
BASE = "https://prts.maa.plus/copilot/get"


def extract_job(d):
    c = d.get("content")
    if isinstance(c, str):
        return json.loads(c)
    if isinstance(c, list) and c:
        return json.loads(c[0]) if isinstance(c[0], str) else c[0]
    if isinstance(c, dict):
        return c
    return None


def main():
    data = json.loads(CAND.read_text(encoding="utf-8"))
    def walk(o, out):
        if isinstance(o, dict):
            if o.get("id") and (o.get("stage") == "act16side_ex02"
                                or "ex02" in str(o.get("stage_name", ""))):
                out.append(o["id"])
            for v in o.values():
                walk(v, out)
        elif isinstance(o, list):
            for v in o:
                walk(v, out)
        return out
    ids = sorted(set(walk(data, [])))
    print(f"候选 {len(ids)} 份: {ids}")
    rows = []
    for cid in ids:
        try:
            with urllib.request.urlopen(f"{BASE}/{cid}", timeout=20) as r:
                d = json.loads(r.read()).get("data") or {}
            job = extract_job(d)
            if job is None:
                rows.append((cid, f"content 解析失败 type={type(d.get('content')).__name__}", 0, 0))
                continue
            if not job.get("actions") or not job.get("stage_name"):
                rows.append((cid, "缺 actions/stage_name", 0, 0))
                continue
            hard = ("突袭" in str(job.get("stage_name"))) or bool(d.get("hard")) or bool(d.get("is_raid"))
            out = OUT_H if hard else OUT_N
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{cid}.json").write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
            reqs = parse_requirements(job)
            rows.append((cid, "突袭" if hard else "普通", len(reqs), len(job["actions"])))
            time.sleep(0.4)
        except Exception as e:
            rows.append((cid, f"{type(e).__name__}: {str(e)[:40]}", 0, 0))
    for cid, st, nreq, nact in rows:
        print(f"  {cid} {st:<12} 需求={nreq:<3} 动作={nact}")
    oks = [r for r in rows if r[1] in ("普通", "突袭")]
    print(f"\n验收① actions 非空: {len(oks)}/{len(ids)}（目标 ≥8）")
    print("验收② 需求分布:", dict(sorted(collections.Counter(r[2] for r in oks).items())))
    low = [r[0] for r in oks if r[2] < 6]
    print("验收③ <6 的份:", low or "无")


if __name__ == "__main__":
    main()
