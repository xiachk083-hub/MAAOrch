#!/usr/bin/env python3
"""探「MAA 自动编队能不能把某人编进队」+ A/B 组名形式 —— 编队失败在开打前 ⇒ **不花理智** ✓。

由来（2026-09-20 晚）：v18 的 6 个组，MAA 只编进 4 个（桃金娘 / 仇白 报 Missing，号上确实有）。
原报告把原因归给"短组名（P0/P1）会被整片拒"，但 v18 用的是「位置+人名」却仍然部分失败 ⇒ 这个结论要复核。

本工具做三件事：
  1. 用**真实干员名**当组名（`探-<人名>`）批量测"谁编得进去"；
  2. `--ab-name` 时额外加一组**短组名**（`P<n>`）对照，同时验证"短组名是否真的必挂"；
  3. 一直读到 MAA 进编队确认阶段（`BattleQuickFormationConfirm`）就**杀掉**它 —— 那一刻还没点开始行动 ✓ 不花理智 ✓。

判据只用 MAA 自己的事件流（`asst.log`，从启动前的文件长度往后读，**不读旧行** ✗）：
  `BattleFormationSelected {group_name, selected}`            = 编成了 ✓
  `SubTaskError {details.opers: {<组名>: [{name, reason}]}}`  = 编不进去 ✗（reason=Missing 等）

用法:
    python tools/maa_form_probe2.py --opers 极境 讯使 德克萨斯 红豆 清道夫 凛冬 棘刺 烛煌 --ab-name
    python tools/maa_form_probe2.py --opers-file data/copilot/_probe_list.txt --tag p2

⚠️ 跑之前游戏必须停在 **CE-6 关卡详情页**（用 `python tools/ce6_attempt.py --job <任意作业> --tag nav --nav-only` 到达）。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import time

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
MC = pathlib.Path.home() / "AppData/Local/Microsoft/WinGet/Links/maa-cli.exe"
CORE = PROJ / "runtime" / "maa_cli" / "state" / "debug" / "asst.log"
OUT = PROJ / "runtime" / "_ce6"
CELLS = [(2, 1), (2, 2), (2, 3), (3, 1), (3, 2), (3, 3), (4, 1), (4, 2)]


def build_job(entries: list[tuple[str, str]], title: str) -> dict:
    """entries = [(组名, 干员名)]，每组一人。"""
    return {
        "stage_name": "wk_melee_6", "minimum_required": "v4.0.0",
        "doc": {"title": title, "details": "探测 MAA 自动编队：谁编得进去、短组名是否真的必挂"},
        "opers": [],
        "groups": [{"name": g, "opers": [{"name": n, "skill": 3, "skill_usage": 1}]} for g, n in entries],
        "actions": [{"type": "SpeedUp"}] + [
            {"type": "Deploy", "name": g, "location": list(CELLS[i]), "direction": "Right"}
            for i, (g, _) in enumerate(entries)] + [{"type": "SkillDaemon"}],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--opers", nargs="*", default=[])
    ap.add_argument("--opers-file")
    ap.add_argument("--ab-name", action="store_true", help="同时用 P0/P1… 短组名对照")
    ap.add_argument("--tag", default="p2")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    opers = list(args.opers)
    if args.opers_file:
        opers += [l.strip() for l in open(args.opers_file, encoding="utf-8") if l.strip() and not l.startswith("#")]
    if not opers:
        ap.error("--opers 或 --opers-file 至少给一个")

    # characterLimit=8 ⇒ 一次最多 8 个坑；--ab-name 时 6 真 + 2 短名
    limit = 6 if args.ab_name else 8
    opers = opers[:limit]
    entries = [("探-" + n, n) for n in opers]
    if args.ab_name:
        entries += [("P%d" % i, n) for i, n in enumerate(opers[:2], start=len(entries))]

    jp = OUT / f"{args.tag}.json"
    jp.write_text(json.dumps(build_job(entries, "编队探针 %s" % args.tag), ensure_ascii=False, indent=1), encoding="utf-8")
    print("探针作业 →", jp)
    print("组 →", ", ".join("%s=%s" % e for e in entries))

    pos0 = CORE.stat().st_size if CORE.exists() else 0
    env = {**os.environ,
           "MAA_DATA_DIR": str(PROJ / "runtime/maa_cli/data"),
           "MAA_CONFIG_DIR": str(PROJ / "runtime/maa_cli/config"),
           "MAA_STATE_DIR": str(PROJ / "runtime/maa_cli/state"),
           "MAA_CACHE_DIR": str(PROJ / "runtime/maa_cli/cache")}
    # ⚠️ 作业路径必须绝对（相对路径会被 maa-cli 解析到自己 cache 里 ⇒ 秒退、日志空）
    p = subprocess.Popen([str(MC), "copilot", str(jp), "-p", "ce6", "--batch", "--formation",
                          f"--log-file={OUT / (args.tag + '.log')}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)

    ok: dict[str, str] = {}
    miss: dict[str, str] = {}
    done, raw = False, []
    t0 = time.time()
    while time.time() - t0 < args.timeout:
        if p.poll() is not None:
            break
        if CORE.exists():
            # 用二进制读、自己推进字节偏移：文本模式的换行翻译会让 seek 偏移错位 ✗
            with open(CORE, "rb") as f:
                f.seek(pos0)
                b = f.read()
            pos0 += len(b)
            chunk = b.decode("utf-8", "replace")
            raw.append(chunk)
            for line in chunk.splitlines():
                mm = re.search(r"\| (\w+) (\{.*\})\s*$", line.strip())
                if mm:
                    try:
                        d = json.loads(mm.group(2))
                    except Exception:
                        continue
                    det = d.get("details") or {}
                    if d.get("what") == "BattleFormationSelected":
                        ok[det.get("group_name")] = det.get("selected")
                    if isinstance(det.get("opers"), dict):
                        for g, lst in det["opers"].items():
                            for o in lst or []:
                                miss[g] = "%s(%s)" % (o.get("name"), o.get("reason"))
                    if "BattleQuickFormationConfirm" in line:
                        done = True
        if done:
            break
        time.sleep(2)
    p.kill()
    try:
        p.wait(timeout=15)
    except Exception:
        pass

    (OUT / f"{args.tag}.probe.json").write_text(
        json.dumps({"entries": entries, "selected": ok, "missing": miss, "reached_confirm": done},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== 结果（%s）===" % ("编队完成、已拦在开打前 ✓ 未花理智" if done else "未到确认阶段（超时/被提前退出）"))
    for g, n in entries:
        if g in ok:
            print("  ✓ %-16s %-8s 编进去了（MAA 选中 %s）" % (g, n, ok[g]))
        elif g in miss:
            print("  ✗ %-16s %-8s MAA 报 %s" % (g, n, miss[g]))
        else:
            print("  ? %-16s %-8s 没有明确记录" % (g, n))
    good = [n for g, n in entries if g in ok]
    print("\n可用（%d/%d）: %s" % (len(good), len(entries), " ".join(good)))
    if args.ab_name:
        short = [e for e in entries if e[0].startswith("P")]
        short_ok = [g for g, _ in short if g in ok]
        print("短组名对照：%d/%d 编成（%s）⇒ %s" % (
            len(short_ok), len(short), " ".join(short_ok) or "全挂",
            "**短组名并非必挂**，原报告这条结论错 ✗" if short_ok else "短组名确实全挂（与原报告一致）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
