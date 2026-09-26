"""批量执行器（按活动）：**一次跑完一个活动的所有关卡** —— 队列 + 结果检测 + 失败处理 + 实时流。

由来（2026-09-16 用户《批量执行器 · 总指导》）。但勘察后发现：**你表里列的四个"缺失"，三个已经存在**，
所以本文件是**组合层**，不是重写一套 —— 这是本项目的既有原则（单点收敛，别长出第二套并行实现）：

    批量队列     → 复用 `tools/state.py`（六阶段/原子写/pending 跳过=中断可续跑）
    三星检测     → 复用 `tools/clear_runner.py` 的 `_verify_stars()`（→ `maa_job_fallback.verify_three_star`）
    关卡间导航   → 复用 `clear_runner.go_terminal/safe_tap/wait_state`、`ga_ex_drive` 的"失败立刻换下一份"
    失败处理     → 本文件只定**策略**（跳过 + 记录 + 每关最多 1 次重试 + 绝不阻塞）
    实时流       → 本文件新增（JSONL 落盘 + 带时间戳的 stdout；SSE 可后接 `/api/events_bus`）

🔴 **两道闸**（默认不越线，必须显式开）：
  1. **默认 dry-run**：不给 `--live` 就只读工作区、只打印计划，**不碰任何机器**。
  2. **没有节点坐标就不跑**：每关要有"活动地图上的节点坐标"（`--node 390,165` 那种）。
     实测**这个坐标没有落盘的表**（`tools/_read_node.py` 只是探索脚本，不是 API），
     所以 `build_queue()` 会把 `node=None` 标出来，`--live` 时**拒绝执行缺坐标的关并说明原因**，
     而不是猜一个坐标然后乱点（乱点会替客户浪费理智、甚至点进别的关）。

用法:
    python tools/batch_run.py 9036 act54side                 # 计划（dry-run，离线）
    python tools/batch_run.py 9036 act54side --json           # 计划（机器可读）
    python tools/batch_run.py 9036 act54side --set-node SR-1=390,165   # 登记节点坐标（落盘复用）
    python tools/batch_run.py 9036 act54side --emu 2 --live   # 真跑（需已登记节点坐标）
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
PROJ = _HERE.parent
sys.path.insert(0, str(_HERE))
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

import versions as VSN          # noqa: E402
import workspace as WSP         # noqa: E402

NODES_FILE = PROJ / "data" / "stage_nodes.json"
# `clear_runner.run()` 的失败返回值 → 人话（失败要记"为什么"，这是纪律④）
WHY = {"nav_failed": "导航失败（没走到关卡详情页）", "exhausted": "作业都试完仍没通关",
       "stuck": "卡在某个界面（看门狗判死）", "timeout": "超时未结束",
       "no_deploy": "一个干员都没部署上"}
RUN_DIR = PROJ / "data" / "batch_runs"


# ── 节点坐标表（当前唯一的手工输入）──

def load_nodes() -> dict:
    """`{活动: {代号: "x,y"}}`。没有就返回空 —— **不猜坐标**。"""
    if not NODES_FILE.is_file():
        return {}
    try:
        return json.loads(NODES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_node(activity: str, code: str, xy: str) -> pathlib.Path:
    """登记一关在地图上的节点坐标（`--set-node SR-1=390,165`）。落盘后所有关卡复用。"""
    x, y = (int(v) for v in str(xy).split(","))
    d = load_nodes()
    d.setdefault(activity, {})[code] = "%d,%d" % (x, y)
    NODES_FILE.parent.mkdir(parents=True, exist_ok=True)
    NODES_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return NODES_FILE


# ── 队列 ──

def pick_job(sd: pathlib.Path) -> tuple[dict | None, str, str]:
    """这一关该跑哪份作业：`cleared.json` → 最新的人改版 → AI 根版本 → 候选第一份。

    返回 `(job, 来源标签, 绝对路径)`。**人改过的版本优先级高于 AI 的**（闭环的产物要真被跑起来，
    否则"改完存下来"这件事没有出口）。
    """
    cl = sd / "cleared.json"
    if cl.is_file():
        return json.loads(cl.read_text(encoding="utf-8")), "cleared", str(cl)
    tree = VSN.load_tree(sd)
    for m in reversed([x for x in tree if x.get("author") == "human"]):
        p = sd / VSN.V_DIR / m["file"]
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8")), m["id"], str(p)
    roots = [x for x in tree if not x.get("parent")]
    if roots:
        p = sd / VSN.V_DIR / roots[-1]["file"]
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8")), roots[-1]["id"], str(p)
    jobs = sorted((sd / "jobs").glob("*.json"))
    if jobs:
        return json.loads(jobs[0].read_text(encoding="utf-8")), "jobs/%s" % jobs[0].name, str(jobs[0])
    return None, "", ""


def build_queue(account: str, activity: str, only: list[str] | None = None,
                include_cleared: bool = True) -> list[dict]:
    """活动 → 逐关队列。每项：代号 / stageId / 用哪份作业 / **节点坐标** / 是否已通关。

    `pending` 的语义与 `tools/state.py` 一致：中断后重跑，已通关的关默认跳过（`--rerun-cleared` 才重跑）。
    """
    base = WSP.WS / WSP._safe_name(account) / WSP._safe_name(activity)
    if not base.is_dir():
        raise SystemExit("工作区里没有这个活动：%s / %s" % (account, activity))
    nodes = load_nodes().get(activity) or {}
    out = []
    for d in sorted(base.glob("*/")):
        if not (d / "stage.json").is_file():
            continue
        st = json.loads((d / "stage.json").read_text(encoding="utf-8"))
        code = st.get("code") or d.name
        if only and code not in only and st.get("stage_id") not in only:
            continue
        job, src, path = pick_job(d)
        cleared = (d / "cleared.json").is_file()
        item = {"code": code, "stage_id": st.get("stage_id"), "name": st.get("name"),
                "group": st.get("group"), "dir": str(d), "job_source": src,
                "job_path": path, "n_actions": len((job or {}).get("actions") or []),
                "cleared": cleared, "node": nodes.get(code),
                "skip": "已通关（要重跑加 --rerun-cleared）" if (cleared and include_cleared) else None,
                "block": None if job else "没有可用作业", "events": []}
        if not item["node"] and not item["block"]:
            item["block"] = "缺地图节点坐标（用 --set-node %s=x,y 登记）" % code
        out.append(item)
    if not out:
        raise SystemExit("这个活动下没有关卡目录（%s）" % base.relative_to(PROJ))
    return out


# ── 事件流 ──

def emit(run: dict, stage: str | None, event: str, **kv) -> dict:
    """推一条事件：带时间戳 → stdout + JSONL 落盘。

    **实时性**：逐条 flush（跑起来时能立刻看到，不是等跑完再吐）。落盘是为了事后归因
    （哪关失败、为什么），也是接 SSE/UI 的数据源。
    """
    rec = {"time": datetime.datetime.now().strftime("%H:%M:%S"), "stage": stage, "event": event}
    rec.update(kv)
    run["events"].append(rec)
    line = "[%s] %s %s%s" % (rec["time"], stage or "-", event,
                             ("  " + " ".join("%s=%s" % (k, v) for k, v in kv.items())) if kv else "")
    print(line, flush=True)
    if run.get("log"):
        with open(run["log"], "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# ── 执行 ──

def execute_stage(item: dict, run: dict, emu: str, max_tries: int = 1) -> dict:
    """跑一关：**委托给既有的 `clear_runner.Runner`**（导航/点按/三星核验都在那里）。

    本函数只负责：起跑 → 结果归类 → 失败策略（跳过、不阻塞）。真跑必须已有节点坐标。
    """
    if item.get("skip"):
        return {"result": "skipped", "why": item["skip"]}
    if item.get("block"):
        return {"result": "blocked", "why": item["block"]}
    nx, ny = (int(v) for v in str(item["node"]).split(","))
    emit(run, item["code"], "navigate_start", node=item["node"], job=item["job_source"])
    try:
        import clear_runner as CR          # 懒导入：dry-run 时不碰任何依赖
    except Exception as e:                 # 诚实失败，不静默降级
        emit(run, item["code"], "runner_unavailable", error="%s: %s" % (type(e).__name__, e))
        return {"result": "error", "why": "clear_runner 不可用：%s" % e}
    for attempt in range(1, max_tries + 1):
        try:
            r = CR.Runner(emu=str(emu), node=(nx, ny), plans=[item["job_path"]],
                          max_tries=1, out_dir=RUN_DIR)
            verdict = r.run()              # 既有实现：导航→起 MAA→等结算→三星核验
            # ⚠️ 真实返回值只有这六种（`clear_runner.run()` 的 docstring）：cleared / exhausted /
            # timeout / nav_failed / no_deploy / stuck。**`cleared` 只表示"看到通关结算页"**，
            # 三星要看 `last_stars`（核验值原来只被 log 掉，见上面 clear_runner 的改动）。
            stars = getattr(r, "last_stars", None)
            emit(run, item["code"], "result", verdict=verdict, stars=stars, attempt=attempt)
            if verdict == "cleared":
                if stars is True:
                    return {"result": "win", "stars": 3}
                # 通关了但没三星 / 判不出 —— **都不算通过**，记下来交人工（判不出 ≠ 通过）
                return {"result": "cleared_no_star" if stars is False else "cleared_unknown",
                        "why": "通关但三星核验=%s" % stars}
            return {"result": "fail", "why": WHY.get(verdict, verdict)}
        except Exception as e:
            emit(run, item["code"], "stage_error", attempt=attempt,
                 error="%s: %s" % (type(e).__name__, e))
    return {"result": "fail", "why": "重试 %d 次仍失败" % max_tries}


def batch_run(account: str, activity: str, emu: str = "", live: bool = False,
              only: list[str] | None = None, rerun_cleared: bool = False,
              max_tries: int = 1, quiet: bool = False) -> dict:
    """一个活动跑一遍。**live=False 时只出计划，不碰机器。**"""
    q = build_queue(account, activity, only, include_cleared=not rerun_cleared)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    run = {"account": account, "activity": activity, "started": ts, "live": live,
           "log": str(RUN_DIR / ("%s_%s_%s.jsonl" % (ts, account, activity))), "events": []}
    emit(run, None, "batch_start", n=len(q), live=live, emu=emu or "-")
    todo = [x for x in q if not x["skip"]]
    n_block = sum(1 for x in todo if x["block"])
    if live and n_block:
        emit(run, None, "refuse", why="%d 关缺节点坐标/无作业 —— 拒绝真跑（不猜坐标乱点）" % n_block,
             stages=[x["code"] for x in todo if x["block"]])
        return dict(run, results={}, aborted=True)
    if not live:
        for it in q:
            emit(run, it["code"], "plan", job=it["job_source"] or "-", actions=it["n_actions"],
                 node=it["node"] or "缺", state=it["skip"] or it["block"] or "待执行")
        emit(run, None, "dry_run_done", todo=len(todo), blocked=n_block,
             note="这是计划。真跑加 --emu N --live（且先补齐节点坐标）")
        return dict(run, results={}, aborted=False)

    results, ok = {}, 0
    for it in q:
        results[it["code"]] = execute_stage(it, run, emu, max_tries)
        ok += 1 if results[it["code"]].get("result") == "win" else 0
        # 每关跑完就落一次盘：中途崩了也知道跑到哪、结果如何（state.py 的同一套纪律）
        (pathlib.Path(it["dir"]) / "batch_state.json").write_text(
            json.dumps({"at": datetime.datetime.now().isoformat(timespec="seconds"),
                        "result": results[it["code"]], "job": it["job_source"]},
                       ensure_ascii=False, indent=1), encoding="utf-8")
    emit(run, None, "batch_done", win=ok, fail=len(results) - ok, total=len(q))
    return dict(run, results=results, aborted=False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="批量执行器（按活动）：默认 dry-run，只出计划")
    ap.add_argument("account")
    ap.add_argument("activity")
    ap.add_argument("--live", action="store_true", help="真跑（需 --emu 且每关已有节点坐标）")
    ap.add_argument("--emu", default="", help="模拟器索引（--live 必填）")
    ap.add_argument("--stage", action="append", default=None, help="只跑这些关（代号/stageId，可重复）")
    ap.add_argument("--rerun-cleared", action="store_true", help="已通关的也重跑")
    ap.add_argument("--max-tries", type=int, default=1, help="每关最多重试次数（默认 1，失败就跳）")
    ap.add_argument("--set-node", action="append", default=None,
                    metavar="代号=x,y", help="登记这一关在地图上的节点坐标（落盘，后续复用）")
    ap.add_argument("--json", action="store_true", help="输出机器可读的队列")
    a = ap.parse_args(argv)

    for kv in a.set_node or []:
        if "=" not in kv:
            raise SystemExit("--set-node 要写成 代号=x,y，例如 SR-1=390,165")
        code, xy = kv.split("=", 1)
        set_node(a.activity, code, xy)
        print("已登记节点坐标：%s/%s = %s → %s" % (a.activity, code, xy,
                                                NODES_FILE.relative_to(PROJ)), flush=True)

    if a.json:
        print(json.dumps(build_queue(a.account, a.activity, a.stage,
                                     include_cleared=not a.rerun_cleared),
                         ensure_ascii=False, indent=1))
        return 0
    if a.live and not a.emu:
        raise SystemExit("--live 需要 --emu N（跑哪个模拟器）")
    r = batch_run(a.account, a.activity, a.emu, live=a.live, only=a.stage,
                  rerun_cleared=a.rerun_cleared, max_tries=a.max_tries)
    if r.get("aborted"):
        print("\n已拒绝真跑。补齐上面的节点坐标后重试：")
        print("  python tools/batch_run.py %s %s --set-node <代号>=x,y" % (a.account, a.activity))
    print("\n事件流：%s（%d 条）" % (r["log"], len(r["events"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
