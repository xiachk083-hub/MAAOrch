#!/usr/bin/env python3
"""作业自动降级重试 —— 一份跑不了就换下一份，直到三星为止。

为什么需要它（2026-09-15 实战教训）:
    作业站的热门作业普遍写着「专三/模组」硬要求，而干员箱里查不到专精与模组等级，
    所以"能不能跑"只有 MAA 编队那一刻才知道：
      · 干员不可用（缺干员 / 精英化不够 / 技能等级不足 / 所需模组未解锁）→ 这份废了
      · 战斗失败或未三星                                                    → 换下一份
    人工一关一关试太慢，这里把「试 → 判定 → 换」自动化。

判定来源（全部取实证，不猜）:
    1. asst.log 新写入的段落：编队失败关键字 / MissionFailed / MissionCompleted
    2. 三星核验：跑完用模拟器截图看关卡详情页头部的三个六边形（蓝=三星通关）
       —— 不依赖 MAA 的措辞，见 services/ai_agent 的既有实现

用法:
    # 按筛选结果自动排序重试（推荐）
    python tools/maa_job_fallback.py --stage act54side_ex02 \
        --screening data/copilot/sr_ex_screening.json --emu 1 --max-tries 4

    # 手动给一串作业号
    python tools/maa_job_fallback.py --codes prts://102331,prts://102346 --emu 1

    # 只打印会依次试哪些作业，不真的跑
    python tools/maa_job_fallback.py --stage act54side_ex02 --screening <json> --dry-run
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
import urllib.request

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from infrastructure.task_constants import detect_emu_adb, find_adb  # noqa: E402

API = "https://prts.maa.plus"
UA = {"User-Agent": "Mozilla/5.0"}

# asst.log 里出现这些 → 这份作业用不了（编队阶段就废了）
KEYS_UNUSABLE = ("OperatorMissing", "not found in formation", "has no feasible solution",
                 "char_allocation", "干员不可用", "干员组内的干员均未拥有")
# 出现这些 → 打输了 / 没三星
KEYS_FAILED = ("MissionFailed", "BattleFailed", "BattleOperRetreatEnd", "战斗出错", "任务出错")
# 出现这些 → 打完了（还要再核三星）
KEYS_DONE = ("MissionCompleted", "StageDrops", "AllTasksCompleted", "CompleteTask")


def fetch_job(jid: int, out_dir: pathlib.Path) -> pathlib.Path:
    """从作业站拉一份作业正文落盘（MaaCore 只吃本地文件）"""
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"job_{jid}.json"
    if dst.exists() and dst.stat().st_size > 200:
        return dst
    with urllib.request.urlopen(urllib.request.Request(f"{API}/copilot/get/{jid}", headers=UA), timeout=25) as r:
        data = json.load(r)
    content = (data.get("data") or {}).get("content")
    if isinstance(content, str):
        content = json.loads(content)
    if not content or not content.get("actions"):
        raise RuntimeError(f"作业 {jid} 正文为空（stage_name={content.get('stage_name') if content else None}）")
    dst.write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding="utf-8")
    return dst


def run_once(args, job_path: pathlib.Path, log_path: pathlib.Path, before_size: int) -> tuple[str, str]:
    """跑一次作业，返回 (verdict, 证据行)；verdict ∈ usable / unusable / failed / done"""
    inst = (PROJ / "services" / "maa" / "instances" / str(args.instance)).resolve()
    adb = find_adb() or "adb"
    addr = detect_emu_adb(args.emu)
    if not addr:
        return "error", "取不到模拟器 adb 端口"
    py_dir = inst / "Python"

    code = "\n".join([
        "import sys, time",
        f"sys.path.insert(0, {str(py_dir)!r})",
        "from asst.asst import Asst, InstanceOptionType",
        f"Asst.load(path={str(inst)!r})",
        "asst = Asst()",
        f"asst.set_instance_option(InstanceOptionType.touch_type, {args.touch!r})",
        f"ok = asst.connect({adb!r}, {addr!r})",
        "if not ok:",
        "    print('CONNECT_FAIL'); sys.exit(1)",
        "print('CONNECTED')",
        "cop = {'filename': %r, 'loop_times': 1, 'formation': True}" % str(job_path),
        ("cop['use_sanity_potion'] = True") if args.potion else "pass",
        ("cop['support_unit_usage'] = 1") if args.support else "pass",
        ("cop['ignore_requirements'] = True") if args.ignore_requirements else "pass",
        "asst.append_task('Copilot', cop)",
        "print('PARAMS', cop)",
        "asst.start()",
        f"dl = time.time() + {args.timeout}",
        "while time.time() < dl and asst.running():",
        "    time.sleep(3)",
        "asst.stop()",
        "print('DONE')",
    ])
    try:
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=args.timeout + 120,
                       encoding="utf-8", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return "failed", "子进程超时"

    # 只看本次新增的日志
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(before_size)
            tail = f.read()
    except Exception as e:
        return "error", f"读日志失败 {e}"
    if any(k in tail for k in KEYS_UNUSABLE):
        ev = next(l for l in tail.splitlines() if any(k in l for k in KEYS_UNUSABLE))
        return "unusable", ev.split("] ", 1)[-1][:150]
    if any(k in tail for k in KEYS_FAILED):
        ev = next(l for l in tail.splitlines() if any(k in l for k in KEYS_FAILED))
        return "failed", ev.split("] ", 1)[-1][:150]
    if any(k in tail for k in KEYS_DONE):
        return "done", "打完，待核三星"
    return "unknown", (tail.strip().splitlines() or ["(日志无关键字)"])[-1][-150:]


def verify_three_star(emu: int) -> bool | None:
    """截图核三星：关卡详情页头部三个六边形，蓝=已通关。返回 None 表示无法判定。"""
    try:
        import cv2  # noqa: F401
        from services.ai_agent.percept import Perception
        p = Perception.for_emu(emu, instance=1, client="Official")
        v = p.state(p.shot(), use_ocr=True)["verdict"]
        if "不可刷" in v or "代理锁定" in v:
            return False
        if "可刷" in v:
            return True
        from services.ai_agent.brain import vision_locate
        d = vision_locate(p.shot(), "关卡详情页里关卡编号右侧的三个六边形是蓝色还是灰色？只回答：蓝 或 灰 或 不确定")
        if d and "蓝" in (d.label or ""):
            return True
        if d and "灰" in (d.label or ""):
            return False
    except Exception:
        pass
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="作业自动降级重试")
    ap.add_argument("--stage", default="", help="stage_name，如 act54side_ex02（配合 --screening）")
    ap.add_argument("--screening", default="", help="prts_job_finder.py 的输出 json")
    ap.add_argument("--codes", default="", help="逗号分隔的作业号或 prts:// 代码（与 --stage 二选一）")
    ap.add_argument("--local", default="",
                    help="逗号分隔的本地作业 json 路径（跑本地改过的作业，不走作业站）")
    ap.add_argument("--emu", default="1")
    ap.add_argument("--instance", default="1")
    ap.add_argument("--touch", default="maatouch")
    ap.add_argument("--timeout", type=float, default=600.0, help="单次作业最长秒数")
    ap.add_argument("--max-tries", type=int, default=5)
    ap.add_argument("--potion", action="store_true", default=True, help="理智不足自动嗑药（默认开）")
    ap.add_argument("--no-support", dest="support", action="store_false", default=True,
                    help="不借助战（默认允许借，用于「只缺一组」的作业）")
    ap.add_argument("--ignore-requirements", action="store_true",
                    help="忽略作业里的练度要求（不推荐：会把低练度干员拉上战场）")
    ap.add_argument("--out", default="data/copilot/fallback_report.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # 组装候选顺序
    cands: list[dict] = []
    if args.local:
        for c in args.local.split(","):
            c = c.strip()
            if not c:
                continue
            p = pathlib.Path(c)
            if not p.exists():
                raise SystemExit(f"本地作业不存在：{p}")
            cands.append({"id": 0, "local": str(p.resolve()), "title": p.name,
                          "missing": [], "requirements": []})
    elif args.codes:
        for c in args.codes.split(","):
            c = c.strip()
            if not c:
                continue
            jid = int(c.replace("prts://", "").replace("s", "") or 0)
            cands.append({"id": jid, "title": "(手动指定)", "missing": [], "requirements": []})
    elif args.stage and args.screening:
        rows = json.loads(pathlib.Path(args.screening).read_text(encoding="utf-8")).get(args.stage) or []
        cands = rows[: args.max_tries]
    else:
        raise SystemExit("需要 --local，或 --codes，或 --stage 与 --screening 同时给")
    if not cands:
        raise SystemExit("候选为空")

    print(f"关卡 {args.stage or '(手动)'}｜将依次尝试 {len(cands)} 份：")
    for i, c in enumerate(cands, 1):
        miss = ("缺" + ",".join(c.get("missing") or [])) if c.get("missing") else "干员齐"
        req = ("｜硬要求 " + "/".join((c.get("requirements") or [])[:2])) if c.get("requirements") else ""
        tag = c["local"] if c.get("local") else f"prts://{c['id']}"
        print(f"  {i}. {tag}  ★{c.get('rating', '-')} 赞{c.get('like', '-')}  {miss}{req}  {c.get('title', '')[:26]}")
    if args.dry_run:
        return

    log_path = (PROJ / "services" / "maa" / "instances" / str(args.instance) / "debug" / "asst.log").resolve()
    work = PROJ / "data" / "copilot" / "fallback_jobs"
    report = {"stage": args.stage, "tries": []}
    verdict_final = "未成功"
    for i, c in enumerate(cands, 1):
        jid = c["id"]
        tag = c["local"] if c.get("local") else f"prts://{jid}"
        print(f"\n=== [{i}/{len(cands)}] {tag} {c.get('title', '')[:30]} ===")
        try:
            job = pathlib.Path(c["local"]) if c.get("local") else fetch_job(jid, work)
        except Exception as e:
            print(f"  拉取失败：{e}")
            report["tries"].append({"id": jid, "verdict": "fetch_error", "evidence": str(e)[:120]})
            continue
        before = log_path.stat().st_size if log_path.exists() else 0
        verdict, ev = run_once(args, job, log_path, before)
        print(f"  判定：{verdict}｜{ev}")
        report["tries"].append({"id": jid, "verdict": verdict, "evidence": ev,
                                "title": c.get("title", ""), "missing": c.get("missing") or [],
                                "requirements": c.get("requirements") or []})
        if verdict in ("done", "unknown"):
            stars = verify_three_star(int(args.emu))
            report["tries"][-1]["three_star"] = stars
            if stars is True:
                print("  三星核验：蓝 ✓ → 这份可用 ✓")
                verdict_final = f"成功 prts://{jid}"
                break
            if stars is False:
                print("  三星核验：灰 ✗ → 未通关，换下一份")
            else:
                print("  三星核验：判不了（页面不在关卡详情页？）→ 不认定为成功，换下一份")
        else:
            print("  换下一份")
        time.sleep(2)

    report["result"] = verdict_final
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果：{verdict_final}\n报告：{out}")


if __name__ == "__main__":
    main()
