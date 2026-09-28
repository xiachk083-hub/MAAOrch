#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""审计巡检接线（卡 t_537fbff2）的离线演练：假统一流 / 假归档 ⇒ 断言判据真的触发。

覆盖两件交付物（**全程 tempfile 假目录，零真实 runtime 读写、零网络、零写仓库**）：
  A `tools/audit_waterline.py`（巡检收尾判据 AUDIT_STATUS=OK|WARN|CRIT）
     A1 OK   ：小 live + 归档在        ⇒ rc=0 / AUDIT_STATUS=OK
     A2 WARN ：live 11 MiB > 10 MiB    ⇒ rc=2 / AUDIT_STATUS=WARN
     A3 WARN ：覆盖 26h 且该轮转未轮转（live 8 天前 ⇒ age_hit）⇒ rc=2
     A4 CRIT ：归档缺失且 live 9 MiB > 轮转 8 MiB ⇒ rc=3
  B `tools/storage_waterline.py --runtime <假 runtime>`（本地水位扩面到 runtime/audit/）
     B1 输出含 runtime/audit 段（体积 / 最老文件天数 / 趋势）※ rc 可能被宿主机既有盘位 WARN 抬到 2，故只断言段内容 + rc∈{0,2}
     B2 假 11 MiB 统一流 ⇒ 出 WARN finding（阈值口径与 audit_waterline 同款）

用法：
  C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe tests\audit_patrol_drill.py
  ... --keep   # 保留假目录（默认跑完删）
退出码：0 = 全绿；2 = 有 FAIL。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY312 = r"C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe"
PY = PY312 if os.path.exists(PY312) else sys.executable
CLI_AUDIT_WL = os.path.join(ROOT, "tools", "audit_waterline.py")
CLI_STORAGE_WL = os.path.join(ROOT, "tools", "storage_waterline.py")
MI = 1024 * 1024
RESULTS: list = []


def ck(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": bool(cond), "detail": str(detail)[:400]})
    print("  %-52s %s%s" % (name, "PASS" if cond else "FAIL", ("  " + str(detail)[:260]) if not cond else ""))


def fake_live(path: str, mb: float, span_hours: float, age_days: float | None = None) -> int:
    """造一个假统一流（合法 v1 记录，体积≈mb MiB，首末 ts 跨 span_hours，可选回拨 mtime）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    target = int(mb * MI)
    now = dt.datetime.now()
    pad = "x" * 380
    n = max(2, int(target / 500))
    lines = []
    for i in range(n):
        ts = now - dt.timedelta(hours=span_hours * (1 - i / max(1, n - 1)))
        lines.append(json.dumps({"v": 1, "ts": ts.strftime("%Y-%m-%d %H:%M:%S"),
                                 "actor": "tool:audit_patrol_drill", "action": "audit.drill",
                                 "target": "-", "result": "info", "reason": "",
                                 "params": {"pad": pad, "i": i}}, ensure_ascii=False))
    blob = "\n".join(lines) + "\n"
    if len(blob.encode()) < target:
        blob += (lines[-1] + "\n") * ((target - len(blob.encode())) // (len(lines[-1]) + 1) + 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(blob)
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))
    return len(blob.encode("utf-8"))


def fake_archive(audit_dir: str, kb: int = 40) -> None:
    import gzip
    os.makedirs(os.path.join(audit_dir, "archive"), exist_ok=True)
    rec = json.dumps({"v": 1, "ts": "2026-09-20 00:00:00", "actor": "tool:audit_patrol_drill",
                      "action": "audit.drill", "target": "-", "result": "info", "reason": ""},
                     ensure_ascii=False) + "\n"
    with gzip.open(os.path.join(audit_dir, "archive", "mas_audit-20260920-000000.jsonl.gz"), "wb") as f:
        f.write((rec * 40).encode("utf-8") * kb)


def run(argv, env_extra=None, timeout=180):
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    env.update(env_extra or {})
    p = subprocess.run([PY] + argv, capture_output=True, stdin=subprocess.DEVNULL,
                       timeout=timeout, cwd=ROOT, env=env)
    return p.returncode, (p.stdout or b"").decode("utf-8", "replace"), (p.stderr or b"").decode("utf-8", "replace")


def last_status_line(out: str) -> str:
    for ln in reversed((out or "").splitlines()):
        if ln.strip().startswith("AUDIT_STATUS="):
            return ln.strip()
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留假目录")
    a = ap.parse_args()
    print("审计巡检接线离线演练 @%s（假统一流/假归档，全程 tempfile）" % dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    tmp = tempfile.mkdtemp(prefix="audit_patrol_drill_")
    made = []
    try:
        # ---------------- A. audit_waterline（巡检收尾判据）
        print("\nA. tools/audit_waterline.py（MAAORCH_AUDIT_DIR=假目录）")
        cases = [
            ("A1 OK（live 0.2MiB / 覆盖 2h / 归档在）", "OK", 0, 0.2, 2.0, True, None),
            ("A2 WARN（live 11MiB > 10MiB）", "WARN", 2, 11.0, 2.0, True, None),
            ("A3 WARN（覆盖 26h 且该转未轮转：mtime 回拨 8 天）", "WARN", 2, 0.2, 26.0, True, 8.0),
            ("A4 CRIT（归档缺失且 live 9MiB > 轮转 8MiB）", "CRIT", 3, 9.0, 2.0, False, None),
        ]
        for name, want, want_rc, mb, hours, arch, age_days in cases:
            d = os.path.join(tmp, "a_" + want + str(len(made)))
            made.append(d)
            n = fake_live(os.path.join(d, "mas_audit.jsonl"), mb, hours, age_days)
            if arch:
                fake_archive(d)
            t0 = time.time()
            rc, out, err = run([CLI_AUDIT_WL, "--audit-dir", d], env_extra={"MAAORCH_AUDIT_DIR": d})
            dt_s = round(time.time() - t0, 2)
            line = last_status_line(out)
            got = line.split("=", 1)[1].split()[0] if line else "(无判据行)"
            ck(name, rc == want_rc and got == want,
               "rc=%s(期望%s) 判据=%r bytes=%d elapsed=%ss stderr=%s" % (rc, want_rc, got, n, dt_s, err[:120]))
            print("        %s   （%.2fs，AUDIT_STATUS 判据行）" % (line or "(空)", dt_s))
            print("        %s" % [l for l in out.splitlines() if l.startswith("原因=") or "原因=" in l][-1:])

        # ---------------- B. storage_waterline（本地水位扩面 runtime/audit/）
        print("\nB. tools/storage_waterline.py --local-only --runtime <假 runtime>")
        rt = os.path.join(tmp, "runtime_ok")
        made.append(rt)
        fake_live(os.path.join(rt, "audit", "mas_audit.jsonl"), 0.2, 3.0)
        fake_archive(os.path.join(rt, "audit"))
        rc, out, err = run([CLI_STORAGE_WL, "--local-only", "--runtime", rt])
        # 注意：--local-only 仍会读**宿主机盘位**（C:/D: 剩余空间），rc 可能被既有盘位 WARN 抬到 2
        # （本机 D: 长期 <60GB）。所以 B1 只断言「审计段出现且字段齐」+ rc ∈ {0,2}，
        # 审计段自身的等级交给 B2/B3（完全由假目录决定）。
        sec_ok = ("本机 runtime/audit 审计流:" in out and "MB /" in out and "最老" in out and "趋势" in out)
        ck("B1 输出含 runtime/audit 段（体积/最老天数/趋势）",
           sec_ok and rc in (0, 2),
           "rc=%s sec_ok=%s out=%s" % (rc, sec_ok, out[-300:]))
        for ln in out.splitlines():
            if "runtime/audit 审计流" in ln:
                print("        " + ln.strip())

        rt2 = os.path.join(tmp, "runtime_warn")
        made.append(rt2)
        fake_live(os.path.join(rt2, "audit", "mas_audit.jsonl"), 11.0, 3.0)
        fake_archive(os.path.join(rt2, "audit"))
        rc, out, err = run([CLI_STORAGE_WL, "--local-only", "--runtime", rt2])
        # 断言的是**审计段自己的** finding（不是全局 rc：宿主机盘位 WARN 也会给 2）
        ck("B2 假 11MiB 统一流 ⇒ WARN finding（rc≥2）",
           rc >= 2 and "WARN  本机 runtime/audit 统一流" in out,
           "rc=%s out=%s" % (rc, out[-300:]))
        for ln in out.splitlines():
            if "runtime/audit 统一流" in ln:
                print("        " + ln.strip())

        rt3 = os.path.join(tmp, "runtime_crit")
        made.append(rt3)
        fake_live(os.path.join(rt3, "audit", "mas_audit.jsonl"), 9.0, 3.0)   # 无归档
        rc, out, err = run([CLI_STORAGE_WL, "--local-only", "--runtime", rt3])
        ck("B3 假 9MiB 无归档 ⇒ CRIT finding（rc=3）", rc == 3 and "归档缺失" in out,
           "rc=%s out=%s" % (rc, out[-300:]))
    finally:
        if not a.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print("\n假目录保留在: %s" % tmp)

    bad = [r for r in RESULTS if not r["ok"]]
    print("\nDRILL=%s (%d/%d)" % ("PASS" if not bad else "FAIL", len(RESULTS) - len(bad), len(RESULTS)))
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main())
