#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""P1-3 冒烟③：产物字节级 / 接口语义核对（写入口径没变；读路径输出样本）。

1) state 字节一致：真实 state 文件（旧直写法产物）→ 新代码 load_state + save_state(临时路径)
   → sha256 必须与原地文件一致（写入的序列化口径没变）；
2) 报告 JSON/MD 字节一致：读入真实报告 → render_json/render_md 重渲染 → 与文件字节一致（渲染口径没变）；
3) import 接口语义：can_farm（已知成功号 / 台账外未知号）；
4) 追加审计存在性：runtime/audit/mas_audit.jsonl 里有本工具的 stage.rebuild 行（最近若干条）。
输出末行：`VERIFY=PASS` / `VERIFY=FAIL ...`
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import stage_capability as sc  # noqa: E402

TMP = os.path.join(os.environ.get("TEMP") or ".", "p13_state_roundtrip.json")


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ok = True

    def chk(cond, msg):
        nonlocal ok
        ok &= bool(cond)
        print(("OK  " if cond else "FAIL") + " " + msg)

    # 1) state 字节一致（序列化口径不变：同数据 round-trip 必须字节相等，含 Windows CRLF 口径）
    st_path = sc.STATE_PATH
    mt = os.path.getmtime(st_path)
    st = sc.load_state()
    sc.save_state(st, TMP)
    same = sha256_file(st_path) == sha256_file(TMP)
    chk(same, "state 写入口径：读入 → 新代码 save_state 重写 → 字节一致（attempts=%d，原文件 mtime %s）"
        % (len(st.get("attempts") or {}), __import__("datetime").datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M:%S")))
    os.remove(TMP)

    # 2) 报告 JSON / MD 重渲染一致
    #    （注意：Windows 文本模式写入 ⇒ 盘上是 CRLF（旧写法同样如此）；文本读回即 LF，故按文本比）
    rep = sc._load_json(sc.JSON_PATH)
    chk(sc.render_json(rep) == Path(sc.JSON_PATH).read_text(encoding="utf-8"),
        "报告 JSON 渲染口径一致（render_json 重渲染 == 文件文本；盘上 CRLF 与旧写法一致）")
    with open(sc.MD_PATH, "r", encoding="utf-8") as f:
        md_file = f.read()
    chk(sc.render_md(rep) == md_file, "报告 MD 渲染口径一致（render_md 重渲染 == 文件文本）")

    # 3) import 接口语义样本
    sample = None
    for acct in (rep.get("accounts") or [])[:60]:
        for r in rep.get("rows") or []:
            if r["account"] == acct:
                sample = (acct, r["stage"], r["status"])
        if sample:
            break
    got = sc.can_farm(sample[0], sample[1]) if sample else None
    chk(sample and got == sample[2], "can_farm 样本：%s × %s -> %s（台账 row=%s）"
        % (sample[0] if sample else "?", sample[1] if sample else "?", got, sample[2] if sample else "?"))
    chk(sc.can_farm("不存在的号-测试", "1-7") == "unknown", "台账外账号 -> unknown（不猜）")

    # 4) 审计行（本工具）
    alog = os.path.join(ROOT, "runtime", "audit", "mas_audit.jsonl")
    hits = []
    if os.path.exists(alog):
        with open(alog, "r", encoding="utf-8") as f:
            for line in f:
                if '"tool:stage_capability"' in line and '"stage.rebuild"' in line:
                    try:
                        hits.append(json.loads(line))
                    except ValueError:
                        pass
    chk(bool(hits), "审计流有 stage.rebuild 行 %d 条（最近：%s）"
        % (len(hits), json.dumps(hits[-1].get("params"), ensure_ascii=False) if hits else "-"))

    print("VERIFY=%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
