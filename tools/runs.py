"""第一层：每局一条结构化记录（runs.jsonl）。没有它，后面的聚合/提炼全是空的。"""
import json, os, pathlib, datetime, hashlib

PROJ = pathlib.Path(__file__).resolve().parent.parent
RUNS = PROJ / "data" / "runs.jsonl"


def _now_id() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def box_hash(box_path: str) -> str:
    try:
        return hashlib.md5(pathlib.Path(box_path).read_bytes()).hexdigest()[:8]
    except Exception:
        return ""


def record(acc: dict | str, stage: str, job: dict | None, result: dict) -> dict:
    """写一条 run 记录（追加；字段缺一不可，见用户 schema）。"""
    acc_id = acc["id"] if isinstance(acc, dict) else str(acc)
    r = {
        "run_id": _now_id(),
        "account": acc_id,
        "stage": stage,
        "job_id": (job or {}).get("job_id"),
        "box_hash": box_hash((acc or {}).get("box_path", "")) if isinstance(acc, dict) else "",
        "result": "win" if result.get("三星") else "fail",
        "survived": result.get("survived_seconds"),
        "failed_at": result.get("failed_at"),
        "location": result.get("location"),
        "missing": (job or {}).get("missing", []) if job else [],
        "replacements": result.get("replacements", {}),
        "strategy_version": result.get("strategy_version", ""),
        "trace_file": result.get("trace_file"),
        "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    RUNS.parent.mkdir(parents=True, exist_ok=True)
    with RUNS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return r


def load_runs(path: str | None = None) -> list[dict]:
    p = pathlib.Path(path) if path else RUNS
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out
