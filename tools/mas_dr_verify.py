#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""mas_dr_verify.py —— 灾难恢复（DR）产物校验器：对「从快照恢复出的目录树」做交付级校验。

用途（配合 tools/mas_config_snapshot.py）：
  恢复后不要直接发车 —— 先回答三个问题：
    ① 树是不是完整的（对照快照 manifest：文件在不在、哈希对不对）；
    ② 配置是不是可用的（data\**\gui*.json 全部可解析、注入字段（账号切换）没有违规组合）；
    ③ 关键补丁是不是对的（app\task\MAA\AutoProxy.py 的 v2 指纹 = 3 处 AccountSwitchEnabled）。

用法：
  python tools/mas_dr_verify.py --snapshot runtime/snapshots/<ts> --root <恢复出的目录> [--json] [--report <文件>]
  python tools/mas_dr_verify.py --root <目录>                     # 只做 ②③（无 manifest 参照时）
  python tools/mas_dr_verify.py --selftest                        # 离线自检（合成夹具，不连网）

判读（末行机检）：
  DR_VERIFY=OK          哈希全对 + JSON 全可解析 + 注入组合无违规 + 补丁 v2 指纹
  DR_VERIFY=WARN[:原因] 有可解释的异常（解析失败/指纹异常/组合超预期），需人工看一眼
  DR_VERIFY=FAIL[:原因] 缺文件或哈希不符 —— 恢复不可信，禁止拿去发车（fail-closed）

退出码：0=OK；1=WARN；2=FAIL；3=参数错误。

边界（诚实口径）：
  * 敏感文件（config\ScriptConfig.json，DPAPI 密文）**本地快照无全保副本**：本地恢复时按设计跳过，
    本工具把它计入「跳过」而非「缺失」；从主机 store 恢复（--remote-root）时才有全保。
  * JSON 解析失败**不一定是恢复引入**：生产上也可能有坏文件（快照如实收录）。工具只如实报告，
    是否阻断由使用者结合 manifest 判断（默认 WARN）。
  * 注入规则出处：docs/MAS_EMERGENCY_RUNBOOK.md §0.4/§2（v2 口径：官服 掩码+true；非官服 ""+false）
    与 tools/mas_switch_gate.py 的判定集合；模板（Default\ConfigFile）存兜底值，运行时注入由
    AutoProxy.py 补丁执行 —— 故模板里的「非掩码+false」是正常态，不算违规。
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):                          # Windows 控制台中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

MASK = re.compile(r"^\d{3}\*{4}\d{4}$")
PHONE = re.compile(r"^\d{11}$")
PATCH = os.path.join("app", "task", "MAA", "AutoProxy.py")
OK_COMBOS = {("掩码", True), ("空", False), ("用户_N", False), ("非字符串", False)}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_startup(obj, out: list) -> None:
    if isinstance(obj, dict):
        if obj.get("$type") == "StartUpTask":
            out.append(obj)
        for v in obj.values():
            find_startup(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_startup(v, out)


def name_class(v) -> str:
    if not isinstance(v, str):
        return "非字符串"
    if v == "":
        return "空"
    if MASK.fullmatch(v):
        return "掩码"
    if PHONE.fullmatch(v):
        return "明文手机号"
    if v.startswith("用户_"):
        return "用户_N"
    return "其它:" + v[:24]


def verify_hashes(snap_dir: str, root: str) -> dict:
    man = json.load(open(os.path.join(snap_dir, "manifest.json"), encoding="utf-8"))
    ok = miss = mism = skip = 0
    bad: list[str] = []
    skipped: list[str] = []
    for e in man.get("files") or []:
        rel = e["path"]
        if not e.get("local"):
            skip += 1
            skipped.append(rel)
            continue
        fp = os.path.join(root, *rel.split("\\"))
        if not os.path.exists(fp):
            miss += 1
            bad.append("缺失: " + rel)
        elif sha256_file(fp) != e["sha256"]:
            mism += 1
            bad.append("哈希不符: " + rel)
        else:
            ok += 1
    total_local = ok + miss + mism
    return {"ok": ok, "missing": miss, "mismatch": mism, "skipped_sensitive": skip,
            "bad": bad[:20], "skipped": skipped[:20], "total_local": total_local,
            "snapshot": man.get("snapshot", os.path.basename(snap_dir))}


def verify_tree(root: str) -> dict:
    """②③：JSON 可解析性 + 注入字段普查 + 补丁指纹。"""
    data = os.path.join(root, "data")
    parse_bad: list[str] = []
    combos = collections.Counter()
    n_files = 0
    if os.path.isdir(data):
        for uid in sorted(os.listdir(data)):
            d = os.path.join(data, uid)
            if not os.path.isdir(d):
                continue
            for sub in ("Default/ConfigFile", "Temp"):
                sd = os.path.join(d, *sub.split("/"))
                if not os.path.isdir(sd):
                    continue
                for fn in sorted(os.listdir(sd)):
                    if not (fn.startswith("gui") and fn.endswith(".json")):
                        continue
                    n_files += 1
                    fp = os.path.join(sd, fn)
                    try:
                        with open(fp, encoding="utf-8-sig") as f:
                            obj = json.load(f)
                    except Exception as e:                       # noqa: BLE001 - 逐文件报告
                        parse_bad.append("%s/%s/%s: %s" % (uid[:8], sub, fn, str(e)[:80]))
                        continue
                    st: list = []
                    find_startup(obj, st)
                    for s in st:
                        combos[(name_class(s.get("AccountName")),
                                bool(s.get("AccountSwitchEnabled"))) ] += 1
    patch_fp = None
    patch_path = os.path.join(root, PATCH)
    if os.path.exists(patch_path):
        with open(patch_path, encoding="utf-8", errors="replace") as f:
            txt = f.read()
        n = txt.count("AccountSwitchEnabled")
        patch_fp = {"markers": n,
                    "verdict": {0: "原版(0)", 1: "v1(1)", 3: "v2(3)"}.get(n, "异常(%d)" % n)}
    violations = []
    for (nc, b), cnt in sorted(combos.items(), key=lambda x: -x[1]):
        if (nc, b) not in OK_COMBOS:
            violations.append("%s + AccountSwitchEnabled=%s ×%d" % (nc, b, cnt))
    return {"json_files": n_files, "parse_bad": parse_bad[:20], "parse_bad_n": len(parse_bad),
            "combos": {"%s|%s" % (nc, b): c for (nc, b), c in combos.items()},
            "violations": violations, "patch": patch_fp}


def run(snapshot: str | None, root: str, out) -> int:
    problems: list[str] = []
    warns: list[str] = []

    def P(s: str = ""):
        out.append(s)

    P("== DR 校验：%s ==" % root)
    if snapshot:
        hv = verify_hashes(snapshot, root)
        P("[哈希] ok=%d 缺失=%d 不符=%d 本地跳过(敏感/设计)=%d（期望本地可恢复 %d）"
          % (hv["ok"], hv["missing"], hv["mismatch"], hv["skipped_sensitive"], hv["total_local"]))
        if hv["bad"]:
            problems.extend(hv["bad"])
        if hv["skipped"]:
            P("  跳过（无本地全保副本）: %s" % ", ".join(hv["skipped"][:3]))
    else:
        hv = None
        P("[哈希] 未提供 --snapshot，跳过")

    tv = verify_tree(root)
    P("[JSON] data 下 gui*.json 共 %d 个，解析失败 %d 个" % (tv["json_files"], tv["parse_bad_n"]))
    for b in tv["parse_bad"][:5]:
        P("  ! %s" % b)
        warns.append("parse:" + b)
    P("[注入] 组合分布（AccountName 类别 + AccountSwitchEnabled）:")
    for k, c in sorted(tv["combos"].items(), key=lambda x: -x[1]):
        P("  %-24s ×%d" % (k.replace("|", " + asw="), c))
    for v in tv["violations"]:
        P("  ! 违规组合: %s" % v)
        warns.append("combo:" + v)
    if tv["patch"] is None:
        P("[补丁] %s 不存在" % PATCH.replace("\\", "/"))
        problems.append("补丁缺失: " + PATCH)
    else:
        fp = tv["patch"]
        P("[补丁] AutoProxy.py 指纹=%s" % fp["verdict"])
        if fp["verdict"] != "v2(3)":
            warns.append("patch-fp:" + fp["verdict"])

    if problems:
        P("DR_VERIFY=FAIL 原因: %s" % "; ".join(problems[:5]))
        return 2
    if warns:
        P("DR_VERIFY=WARN 事项: %s" % "; ".join(warns[:5]))
        return 1
    P("DR_VERIFY=OK")
    return 0


def selftest() -> int:
    """合成夹具：一个正常树（OK）+ 一个缺文件树（FAIL）+ 一个坏 json（WARN）。"""
    import shutil
    bad = 0
    td = tempfile.mkdtemp(prefix="mas_dr_verify_")
    try:
        snap = os.path.join(td, "snap")
        root = os.path.join(td, "root")
        uid = "00000000-0000-0000-0000-000000000001"
        gui = {"TaskQueue": [{"$type": "StartUpTask", "AccountName": "", "AccountSwitchEnabled": False,
                              "Name": "开始唤醒"}]}
        rels = {
            "config\\Config.json": b'{"a":1}',
            "data\\%s\\Default\\ConfigFile\\gui.new.json" % uid: json.dumps(gui).encode("utf-8"),
            PATCH: b"x = 1  # AccountSwitchEnabled AccountSwitchEnabled AccountSwitchEnabled\n",
        }
        os.makedirs(os.path.join(snap, "files"), exist_ok=True)
        files = []
        for rel, blob in rels.items():
            lp = os.path.join(snap, "files", *rel.split("\\"))
            os.makedirs(os.path.dirname(lp), exist_ok=True)
            with open(lp, "wb") as f:
                f.write(blob)
            files.append({"path": rel, "local": "files/" + rel.replace("\\", "/"),
                          "sha256": hashlib.sha256(blob).hexdigest()})
        man = {"snapshot": "fixture", "files": files}
        with open(os.path.join(snap, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(man, f)
        for rel, blob in rels.items():
            fp = os.path.join(root, *rel.split("\\"))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "wb") as f:
                f.write(blob)

        buf: list = []
        rc = run(snap, root, buf)
        if rc != 0:
            print("SELFTEST FAIL: 正常树应 OK，得 rc=%d %s" % (rc, buf[-1])); bad += 1
        # 缺文件 → FAIL
        os.remove(os.path.join(root, "config", "Config.json"))
        buf = []
        rc = run(snap, root, buf)
        if rc != 2:
            print("SELFTEST FAIL: 缺文件应 FAIL(2)，得 rc=%d %s" % (rc, buf[-1])); bad += 1
        # 补回 + 坏 json（未跟踪的额外文件）→ WARN
        with open(os.path.join(root, "config", "Config.json"), "wb") as f:
            f.write(b'{"a":1}')
        xtra = os.path.join(root, "data", "00000000-0000-0000-0000-000000000002", "Temp")
        os.makedirs(xtra, exist_ok=True)
        with open(os.path.join(xtra, "gui.new.json"), "wb") as f:
            f.write(b"\x00\x00\x00")
        buf = []
        rc = run(snap, root, buf)
        if rc != 1:
            print("SELFTEST FAIL: 未跟踪坏 json 应 WARN(1)，得 rc=%d %s" % (rc, buf[-1])); bad += 1
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("SELFTEST=%s" % ("OK" if not bad else "FAIL(%d)" % bad))
    return 0 if not bad else 4


def main() -> int:
    ap = argparse.ArgumentParser(description="DR 恢复产物校验器（配合 mas_config_snapshot.py）")
    ap.add_argument("--snapshot", help="快照目录（含 manifest.json；不给则跳过哈希校验）")
    ap.add_argument("--root", help="恢复出的目录树")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--report", help="把人类可读报告写到该文件")
    ap.add_argument("--selftest", action="store_true", help="离线自检")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.root or not os.path.isdir(args.root):
        print("参数错误：--root 必须是存在的目录", file=sys.stderr)
        return 3
    if args.snapshot and not os.path.exists(os.path.join(args.snapshot, "manifest.json")):
        print("参数错误：--snapshot 目录下没有 manifest.json", file=sys.stderr)
        return 3
    buf: list = []
    rc = run(args.snapshot, os.path.abspath(args.root), buf)
    text = "\n".join(buf)
    if args.json:
        print(json.dumps({"rc": rc, "lines": buf}, ensure_ascii=False, indent=1))
    else:
        print(text)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
