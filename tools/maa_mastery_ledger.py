#!/usr/bin/env python3
"""练度台账 —— 把 MAA 抄作业编队时报的「干员不可用」攒成证据库。

为什么需要（2026-09-15 用户）:
    MAA 的「干员识别」(OperBox) 只回 **精英化 / 等级 / 潜能 / 星级**，
    读不到 **技能专精** 与 **模组** —— 而作业站的要求恰恰按 skill_level
    (7=技能满, 8/9/10=专一/二/三) 和 module 来写。于是筛选出来的首选作业
    经常"看着能跑、一打就废"（2026-09-15 的 102533 就是要求 5 个干员全专三）。

    MAA 唯一能看到这两项的地方是 **抄作业编队那一刻**，日志里会写：
        gui.log:  干员不可用: <名>, 原因: 技能等级不足 / 所需模组未解锁 / 精英化不足 / 等级不足
        asst.log: SubTaskError {"details":{"opers":{"<名>":[{"reason":"Missing"}]}}}
    把这些攒起来 = "这号的哪些干员专精/模组不达标" 的实证，作业筛选可以直接避开。

号归属: gui.log 每段运行前会打 `Already connected to ...127.0.0.1:<端口>` 与
    `"client_type":"<服>"`，据此把证据归到 (端口, 服区)。同一端口换号的时段靠时间戳区分。

产出: data/mastery_ledger.json
用法:
    python tools/maa_mastery_ledger.py              # 扫描默认日志位置并落盘
    python tools/maa_mastery_ledger.py --show       # 顺便打印汇总
    python tools/maa_mastery_ledger.py --logs <路径> [<路径> ...]
"""
from __future__ import annotations

import argparse
import glob
import json
import pathlib
import re
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
OUT = PROJ / "data" / "mastery_ledger.json"

REASON_MAP = {
    "技能等级不足": "技能等级不足",
    "所需模组未解锁": "所需模组未解锁",
    "精英化不足": "精英化不足",
    "等级不足": "等级不足",
    "潜能不足": "潜能不足",
}
CORE_REASON = {
    "Missing": "未拥有",
    "SkillLevel": "技能等级不足",
    "SkillLevelNotEnough": "技能等级不足",
    "Module": "所需模组未解锁",
    "ModuleNotUnlocked": "所需模组未解锁",
    "Elite": "精英化不足",
    "Level": "等级不足",
}

DEFAULT_GLOBS = [
    "D:/Xiach/M-A/MAA/*/debug/gui.log",
    "D:/Xiach/M-A/MAA/*/debug/asst.log",
    "C:/Users/*/M-A/MAA/*/debug/gui.log",
    str(PROJ / "services" / "maa" / "instances" / "*" / "debug" / "asst.log"),
]

GUARD = "以下干员组内的干员均未拥有（每组只需其一）或未达到作业要求"


def _iter_lines(path: pathlib.Path):
    try:
        yield from path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        print(f"  ! 读不了 {path}: {e}")


def scan_file(path: pathlib.Path, src_tag: str) -> list[dict]:
    """扫描单个日志，返回证据列表"""
    out: list[dict] = []
    port = client = job = ""
    pending_guard = False          # 上一条是"干员组未满足"，接下来几行是干员名/组候选
    for ln in _iter_lines(path):
        m = re.search(r"Already connected to .*127\.0\.0\.1:(\d+)", ln)
        if m:
            port = m.group(1)
        m = re.search(r'"client_type":"(\w+)"', ln)
        if m:
            client = m.group(1)
        m = re.search(r"copilot/get/(\d+)", ln)
        if m:
            job = m.group(1)
        ts = re.match(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", ln)
        ts = ts.group(1) if ts else ""

        # 形态 1（GUI）：干员不可用: X, 原因: Y
        m = re.search(r"干员不可用[:：]\s*([^,，]+)[,，]\s*原因[:：]\s*(\S+)", ln)
        if m:
            out.append({"op": m.group(1).strip(), "reason": REASON_MAP.get(m.group(2), m.group(2)),
                        "ts": ts, "port": port, "client": client, "job": job, "src": src_tag})
            pending_guard = False
            continue

        # 形态 2（Core JSON）：{"opers":{"X":[{"name":"X","reason":"..."}]}}
        for om in re.finditer(r'\{"name":"([^"]+)","reason":"([^"]+)"\}', ln):
            out.append({"op": om.group(1), "reason": CORE_REASON.get(om.group(2), om.group(2)),
                        "ts": ts, "port": port, "client": client, "job": job, "src": src_tag})
        if '"opers"' in ln:
            continue

        # 形态 3（GUI 整组失败）：标题行之后，逐行是「干员名」或「组名=> 候选 / 候选」
        if GUARD in ln:
            pending_guard = True
            continue
        if pending_guard:
            name = ln.strip()
            if name.startswith("["):
                pending_guard = False
                continue
            if name:
                if "特别关注" in name or "可能影响干员识别" in name:   # 紧跟其后的界面提示，不是干员名
                    pending_guard = False
                    continue
                if "=>" in name:
                    gname, cands = name.split("=>", 1)
                    out.append({"group": gname.strip(), "candidates": [c.strip() for c in cands.split("/") if c.strip()],
                                "reason": "组内候选均不达标", "ts": ts, "port": port,
                                "client": client, "job": job, "src": src_tag})
                else:
                    out.append({"op": name, "reason": "未达到要求（组）", "ts": ts,
                                "port": port, "client": client, "job": job, "src": src_tag})
                continue
    return out


def build(entries: list[dict]) -> dict:
    by_op: dict = {}
    by_account: dict = {}
    by_group: dict = {}
    for e in entries:
        if "op" not in e:                      # 组级失败单独归档，不污染干员信号
            g = by_group.setdefault(e["group"], {"count": 0, "candidates": e.get("candidates") or [],
                                                 "last_ts": "", "jobs": []})
            g["count"] += 1
            g["last_ts"] = max(g["last_ts"], e["ts"])
            if e.get("job") and e["job"] not in g["jobs"]:
                g["jobs"].append(e["job"])
            continue
        acct = f"{e['port'] or '?'}/{e['client'] or '?'}"
        d = by_op.setdefault(e["op"], {})
        r = d.setdefault(e["reason"], {"count": 0, "last_ts": "", "ports": [], "jobs": []})
        r["count"] += 1
        r["last_ts"] = max(r["last_ts"], e["ts"])
        if e["port"] and e["port"] not in r["ports"]:
            r["ports"].append(e["port"])
        if e.get("job") and e["job"] not in r["jobs"]:
            r["jobs"].append(e["job"])
        acct_op = by_account.setdefault(acct, {}).setdefault(e["op"], {})
        acct_op[e["reason"]] = acct_op.get(e["reason"], 0) + 1
    return {"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "total": len(entries),
            "by_op": by_op, "by_account": by_account, "by_group": by_group,
            "entries": [e for e in entries if "op" in e][-400:]}


def known_gaps(op: str, port: str = "") -> set[str]:
    """给筛选器用：某干员已知的练度短板（不传端口则跨全部端口合并）"""
    try:
        led = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if port:
        return set(led.get("by_account", {}).get(port, {}).get(op, {}))
    return set(led.get("by_op", {}).get(op, {}))


def main() -> None:
    ap = argparse.ArgumentParser(description="MAA 练度台账")
    ap.add_argument("--logs", nargs="*", default=None, help="日志路径（默认自动发现）")
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    paths: list[pathlib.Path] = []
    for p in (args.logs if args.logs else DEFAULT_GLOBS):
        if any(ch in p for ch in "*?"):
            paths += [pathlib.Path(x) for x in glob.glob(p)]
        else:
            paths.append(pathlib.Path(p))

    entries: list[dict] = []
    for p in sorted(set(paths)):
        if not p.exists():
            continue
        got = scan_file(p, p.parent.parent.name + "/" + p.name)
        if got:
            print(f"  {p}：{len(got)} 条")
        entries += got

    data = build(entries)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n共 {data['total']} 条证据 → {args.out}")

    if args.show or True:
        print("\n按干员（只列出现过 ≥2 次的）：")
        flat = []
        for op, rs in data["by_op"].items():
            for reason, info in rs.items():
                flat.append((info["count"], op, reason, ",".join(info["ports"]), info["last_ts"]))
        for cnt, op, reason, ports, last in sorted(flat, reverse=True):
            if cnt < 2:
                continue
            print(f"  {op:<12}{reason:<12}×{cnt:<4} 端口{ports:<10} 最近 {last}")


if __name__ == "__main__":
    main()
