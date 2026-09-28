#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""mas_patch_guard 实机自测（沙箱）—— 零生产风险。

做什么：在主机 `E:\AUTO-MAS\_patchguard_selftest\` 建一个沙箱（只放补丁文件的**副本**），
故意把副本改坏一个字节，然后：
  1) `--check --override-map` ⇒ 期望 PATCH_GUARD=MISSING:<n>（坏的那个被检出）；
  2) `--reapply <name>`（dry-run）⇒ 期望 REAPPLY=DRY-RUN 且沙箱文件**零改动**；
  3) `--reapply <name> --apply` ⇒ 期望 REAPPLY=OK 且沙箱文件哈希回到补丁版；
  4) 再 `--check --override-map` ⇒ 期望 PATCH_GUARD=OK；
  5) 清理沙箱（生产文件全程只读、零接触）。

用法：python tools/mas_patch_guard_selftest.py
退出码：0=全过；1=有失败步骤。日志落 runtime/patches/selftest_last.txt。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mas_patch_guard as pg  # noqa: E402

SANDBOX = r"E:\AUTO-MAS\_patchguard_selftest"
LOCAL_TEST_DIR = os.path.join(ROOT, "runtime", "patches", "selftest")
OVERRIDE_MAP = os.path.join(LOCAL_TEST_DIR, "override_map.json")
LOG = os.path.join(ROOT, "runtime", "patches", "selftest_last.txt")
GUARD = os.path.join(ROOT, "tools", "mas_patch_guard.py")

FAILS = []
LOG_LINES = []


def log(msg: str) -> None:
    line = str(msg).rstrip()
    print(line)
    LOG_LINES.append(line)


def step_ok(name: str, cond: bool, detail: str = "") -> bool:
    log("%s %-46s %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        FAILS.append(name)
    return cond


def ps(script: str, timeout: int = 180) -> str:
    return pg.ssh_ps(script, timeout=timeout)


def guard_run(args: list, timeout: int = 300) -> tuple:
    cmd = [sys.executable, GUARD] + args
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    return r.returncode, out


def parse(out: str, key: str) -> str:
    """取末行 `KEY=...` 的第一个 token（行内可能有附加说明，如 `REAPPLY=OK  backup=...`）。"""
    m = re.findall(r"^%s=(.*)$" % re.escape(key), out, re.M)
    return m[-1].strip().split()[0] if m and m[-1].strip() else ""


def sha_remote(path: str) -> str:
    return (pg.remote_fetch([path]).get(path) or {}).get("sha256") or ""


def main() -> int:
    manifest = pg.load_manifest()
    p_by_name = {p["name"]: p for p in manifest["patches"]}
    prod_ap = p_by_name["accswitch_v2"]["file"]
    prod_co = p_by_name["medicine_constants"]["file"]
    sand_ap = SANDBOX + r"\AutoProxy.py"
    sand_co = SANDBOX + r"\constants.py"

    log("== mas_patch_guard 沙箱自测 %s ==" % pg.dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    prod_ap_sha0, prod_co_sha0 = sha_remote(prod_ap), sha_remote(prod_co)

    # 0) 建沙箱（副本）
    out = ps(r"""
New-Item -ItemType Directory -Force -Path '__SB__' | Out-Null
Copy-Item -LiteralPath '__AP__' -Destination '__SAP__' -Force
Copy-Item -LiteralPath '__CO__' -Destination '__SCO__' -Force
Write-Output ('SANDBOX=' + (Test-Path '__SAP__') + '/' + (Test-Path '__SCO__'))
""".replace("__SB__", SANDBOX).replace("__AP__", prod_ap).replace("__SAP__", sand_ap)
     .replace("__CO__", prod_co).replace("__SCO__", sand_co))
    step_ok("0. 沙箱建立（副本）", "SANDBOX=True/True" in out, pg.short(out, 80))

    # 1) 单字节改坏：AutoProxy 标记里的 v→V；constants 的第一个 True→true
    out = ps(r"""
$p = '__SAP__'
$b = [IO.File]::ReadAllBytes($p)
$s = [Text.Encoding]::GetEncoding(28591).GetString($b)
$i = $s.IndexOf('# 2026-09-27 fix(v2)')
if ($i -lt 0) { Write-Output 'ANCHOR_MISSING'; exit 1 }
$j = $i + 17
$old = $b[$j]; $b[$j] = $b[$j] -bxor 0x20
[IO.File]::WriteAllBytes($p, $b)
Write-Output ('FLIP1 byte=' + $j + ' ' + [char]$old + '->' + [char]$b[$j])
$p2 = '__SCO__'
$b2 = [IO.File]::ReadAllBytes($p2)
$s2 = [Text.Encoding]::GetEncoding(28591).GetString($b2)
$k = $s2.IndexOf('"UseExpiringMedicine": True,')
if ($k -lt 0) { Write-Output 'ANCHOR2_MISSING'; exit 1 }
$kj = $k + 23
$old2 = $b2[$kj]; $b2[$kj] = $b2[$kj] -bxor 0x20
[IO.File]::WriteAllBytes($p2, $b2)
Write-Output ('FLIP2 byte=' + $kj + ' ' + [char]$old2 + '->' + [char]$b2[$kj])
""".replace("__SAP__", sand_ap).replace("__SCO__", sand_co))
    step_ok("1. 沙箱副本各改坏 1 字节", "FLIP1 byte=" in out and "FLIP2 byte=" in out, pg.short(out, 120))

    # 2) override map（本地）
    os.makedirs(LOCAL_TEST_DIR, exist_ok=True)
    with open(OVERRIDE_MAP, "w", encoding="utf-8") as fh:
        json.dump({prod_ap: sand_ap, prod_co: sand_co}, fh, ensure_ascii=False, indent=1)
    args = ["--check", "--override-map", OVERRIDE_MAP]

    # 3) 检出：期望 MISSING:2（accswitch_v2 + medicine_constants）；medicine_autoproxy 应为哈希漂移 WARN
    rc, out = guard_run(args)
    gate = parse(out, "PATCH_GUARD")
    step_ok("2. 坏字节被检出（期望 MISSING:2）", gate == "MISSING:2", "PATCH_GUARD=%s rc=%s" % (gate, rc))
    step_ok("2b. 同文件另一补丁报漂移不误杀", "medicine_autoproxy: hash-drift" in out, "")
    step_ok("2c. 退出码=1", rc == 1, "rc=%s" % rc)

    # 4) dry-run 重放：计划打印、零写入
    before = sha_remote(sand_ap)
    rc, out = guard_run(["--reapply", "accswitch_v2", "--override-map", OVERRIDE_MAP])
    after = sha_remote(sand_ap)
    step_ok("3. dry-run 重放（REAPPLY=DRY-RUN）", parse(out, "REAPPLY") == "DRY-RUN" and rc == 0,
            "rc=%s" % rc)
    step_ok("3b. dry-run 零写入（沙箱哈希不变）", before == after and before != "", "sha=%s" % (before or "?")[:16])
    step_ok("3c. 计划含 diff 与备份/重启提醒", "diff 摘要" in out and "重启侧车" in out, "")

    # 5) 真重放（只写沙箱）：期望回读哈希 == payload
    want_ap = pg.sha256_file(pg._payload_path(p_by_name["accswitch_v2"]))
    rc, out = guard_run(["--reapply", "accswitch_v2", "--apply", "--override-map", OVERRIDE_MAP], timeout=420)
    got = sha_remote(sand_ap)
    step_ok("4. --apply 重放成功（REAPPLY=OK）", parse(out, "REAPPLY") == "OK" and rc == 0, "rc=%s" % rc)
    step_ok("4b. 回读哈希 == 补丁版", got == want_ap, "got=%s want=%s" % (got[:16], want_ap[:16]))
    step_ok("4c. 备份文件已生成（.bak-patchguard-*）", "backup=" in out and ".bak-patchguard-" in out, "")

    # 6) constants 同一套流程（dry-run 略，直接 apply + 校验）
    want_co = pg.sha256_file(pg._payload_path(p_by_name["medicine_constants"]))
    rc, out = guard_run(["--reapply", "medicine_constants", "--apply", "--override-map", OVERRIDE_MAP], timeout=420)
    got_co = sha_remote(sand_co)
    step_ok("5. constants 重放成功", parse(out, "REAPPLY") == "OK" and rc == 0, "rc=%s" % rc)
    step_ok("5b. 回读哈希 == 补丁版", got_co == want_co, "got=%s want=%s" % (got_co[:16], want_co[:16]))

    # 7) 复检：沙箱全绿（生产文件按同一断言）
    rc, out = guard_run(args)
    gate = parse(out, "PATCH_GUARD")
    step_ok("6. 重放后复检全绿（PATCH_GUARD=OK）", gate == "OK" and rc == 0, "PATCH_GUARD=%s rc=%s" % (gate, rc))

    # 8) 生产文件零接触 + 清理沙箱
    prod_now = (sha_remote(prod_ap), sha_remote(prod_co))
    step_ok("7. 生产文件全程只读（哈希未变）", prod_now == (prod_ap_sha0, prod_co_sha0), "")
    out = ps(r"""
Remove-Item -LiteralPath '__SB__' -Recurse -Force -ErrorAction SilentlyContinue
Write-Output ('CLEANED=' + (-not (Test-Path '__SB__')))
""".replace("__SB__", SANDBOX))
    step_ok("8. 沙箱已清理", "CLEANED=True" in out, pg.short(out, 60))
    shutil.rmtree(LOCAL_TEST_DIR, ignore_errors=True)

    log("")
    log("selftest: %s（%d 步失败：%s）" % ("OK" if not FAILS else "FAIL", len(FAILS), ", ".join(FAILS) or "无"))
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(LOG_LINES) + "\n")
    log("日志: %s" % LOG)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
