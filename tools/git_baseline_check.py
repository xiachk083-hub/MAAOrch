#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""入库漂移闸（只读）—— 限定路径内「未跟踪数=0 且 未提交改动数=0 」才算基线干净。

为什么：Hermes 期工具链/文档曾长期游离在 git 之外（600+ 文件无基线），
丢失/被覆盖无法定位与回退；本工具给出可巡检的机器判据。

判据（默认 --line，末行机器可读）：
    GIT_BASELINE=OK                —— 限定路径内无漂移
    GIT_BASELINE=DRIFT:<n>         —— 漂移 n 项，随后最多列前 10 条路径
限定路径：tools/ services/ ui/ docs/ infra/ network/ app/

用法:
    <Python312>\\python.exe tools/git_baseline_check.py --line        # 默认
    <Python312>\\python.exe tools/git_baseline_check.py --json
    <Python312>\\python.exe tools/git_baseline_check.py --selftest    # 临时仓库正反用例（离线）
    [--root D:/1-2/MAAOrch]
只读：仅跑 git 子进程（stdin=DEVNULL + 显式 timeout），不写任何文件。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

PATHS = ["tools", "services", "ui", "docs", "infra", "network", "app"]
GIT_TIMEOUT = 60


def _git(root, *args, timeout=GIT_TIMEOUT):
    return subprocess.run(
        ["git", "-C", root] + list(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=timeout,
    )


def repo_root(start):
    r = _git(start, "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        raise RuntimeError("not a git repo: %s (%s)" % (start, r.stderr.strip()))
    return r.stdout.strip()


def in_scope(path):
    top = path.split("/", 1)[0]
    if path.endswith("/") and "/" not in path.rstrip("/"):
        top = path.rstrip("/")
    return top in PATHS


def collect(root):
    """返回 (untracked, changed, other)；条目 = (kind, path)，其余路径忽略。"""
    r = _git(root, "-c", "core.quotepath=false", "status", "--porcelain", "-uall", "-z")
    if r.returncode != 0:
        raise RuntimeError("git status failed: %s" % r.stderr.strip())
    recs = [x for x in r.stdout.split("\0") if x]
    untracked, changed, other = [], [], []
    i = 0
    while i < len(recs):
        e = recs[i]
        xy, path = e[:2], e[3:]
        if xy[0] in ("R", "C"):  # rename/copy: 下一条是源路径，跳过
            i += 1
        if in_scope(path):
            if xy == "??":
                untracked.append(path)
            else:
                changed.append(path)
        else:
            other.append(path)
        i += 1
    return untracked, changed, other


def run_check(root):
    untracked, changed, other = collect(root)
    items = [{"status": "untracked", "path": p} for p in sorted(untracked)]
    items += [{"status": "uncommitted", "path": p} for p in sorted(changed)]
    n = len(items)
    return {
        "root": root,
        "paths": PATHS,
        "untracked_count": len(untracked),
        "changed_count": len(changed),
        "drift_count": n,
        "status": "OK" if n == 0 else "DRIFT",
        "items": items,
        "out_of_scope_count": len(other),
    }


def print_line(res):
    if res["status"] == "OK":
        print("GIT_BASELINE=OK")
        return
    print("GIT_BASELINE=DRIFT:%d" % res["drift_count"])
    for it in res["items"][:10]:
        print("  %-11s %s" % (it["status"], it["path"]))


def selftest():
    """临时仓库正反用例：OK -> untracked DRIFT -> commit -> OK -> modified DRIFT。"""
    tmp = tempfile.mkdtemp(prefix="gitbaseline_")
    try:
        def g(*a):
            r = _git(tmp, *a)
            assert r.returncode == 0, "git %s failed: %s" % (a, r.stderr)
            return r

        g("init", "-q")
        g("config", "user.email", "selftest@local")
        g("config", "user.name", "selftest")
        os.makedirs(os.path.join(tmp, "tools"))
        os.makedirs(os.path.join(tmp, "tmp"))
        with open(os.path.join(tmp, "tools", "a.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        g("add", "tools/a.py")
        g("commit", "-q", "-m", "init")

        checks = []

        def ck(name, expect_status, expect_n):
            res = run_check(tmp)
            ok = res["status"] == expect_status and res["drift_count"] == expect_n
            checks.append((name, ok, res["status"], res["drift_count"]))
            return ok

        ck("clean", "OK", 0)
        with open(os.path.join(tmp, "tools", "b.py"), "w", encoding="utf-8") as fh:
            fh.write("y = 2\n")
        with open(os.path.join(tmp, "tmp", "junk.txt"), "w", encoding="utf-8") as fh:
            fh.write("ignore me\n")
        ck("untracked-in-scope", "DRIFT", 1)  # tmp/ 不在限定路径
        g("add", "tools/b.py")
        g("commit", "-q", "-m", "add b")
        ck("clean-again", "OK", 0)
        with open(os.path.join(tmp, "tools", "a.py"), "a", encoding="utf-8") as fh:
            fh.write("x = 2\n")
        ck("modified", "DRIFT", 1)
        # 场景化双计数：未跟踪 + 未提交
        with open(os.path.join(tmp, "docs_c.py"), "w") as fh:  # 不在 scope 的根文件
            fh.write("z\n")
        os.makedirs(os.path.join(tmp, "docs"))
        with open(os.path.join(tmp, "docs", "d.md"), "w", encoding="utf-8") as fh:
            fh.write("doc\n")
        ck("two-kinds", "DRIFT", 2)

        ok_all = all(c[1] for c in checks)
        for name, ok, st, n in checks:
            print("  %-4s %-20s -> %s:%d" % ("PASS" if ok else "FAIL", name, st, n))
        print("GIT_BASELINE_SELFTEST=%s" % ("PASS" if ok_all else "FAIL"))
        return 0 if ok_all else 3
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="入库漂移闸（只读）")
    ap.add_argument("--root", default=None, help="仓库根（默认取当前目录的 git 顶层）")
    ap.add_argument("--line", action="store_true", help="单行机器可读（默认）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 对象")
    ap.add_argument("--selftest", action="store_true", help="离线自测（临时仓库正反用例）")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    root = os.path.abspath(args.root) if args.root else repo_root(os.getcwd())
    try:
        res = run_check(root)
    except Exception as exc:  # noqa: BLE001 —— 判不出来时必须显式失败，不得静默 OK
        print("GIT_BASELINE=ERROR:%s" % exc)
        return 2

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print_line(res)
    return 0 if res["status"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
