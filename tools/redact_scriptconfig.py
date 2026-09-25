# -*- coding: utf-8 -*-
"""raw ScriptConfig.json → 脱敏版（任何 agent 一键可用）。

为什么要它：AUTO-MAS 的脚本配置 `ScriptConfig.json` 里 `Password` 是 DPAPI 密文口令。
密文绑**主机作用域**，副本落到别的机器既解不开（做不了"恢复"），又只剩**误提交/外发**的风险。
而批跑真正需要的字段（账号登录名 / 服 / 设备 index / 开关）**一个都不在敏感字段里**，
所以口径统一为：**raw 不留，留脱敏版**。数据要现取用 `tools/mas_accounts.py`。

它做什么：
  1. 找到目标文件（`--path` 指定，或 `--scan` 扫整个仓库里的 `*ScriptConfig*.json`，排除 `*.redacted.json`）；
  2. 把敏感键的值替成 `[已脱敏]`（空值不动）—— 敏感键 = `Password` / `SklandToken` / `ServerChanKey` /
     `Notify_CustomWebhooks`，外加任何名字含 pass|token|secret|cookie 的键；
  3. 校验：全文件再不允许出现 ≥60 字符的 base64 长串（有就**拒绝写盘**并让人工看，不猜）；
  4. 写 `<原名>.redacted.json`，校验通过后**默认删掉 raw 原件**（要留加 `--keep-raw`）。

用法：
  python tools/redact_scriptconfig.py --scan                    # 扫全仓库（先 --dry-run 看清单）
  python tools/redact_scriptconfig.py --scan --dry-run
  python tools/redact_scriptconfig.py --path runtime/x/ScriptConfig.json
  python tools/redact_scriptconfig.py --path <f> --keep-raw      # 只脱敏不删原件
  python tools/redact_scriptconfig.py --selftest                # 离线自测（含密文夹具）

退出码：0 正常（含"没找到目标"）｜3 校验拒绝（见上）｜4 自测失败。
**输出里永远不打印凭据值**（失败时只报长度与条数）。
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SENS_EXACT = {"Password", "SklandToken", "ServerChanKey", "Notify_CustomWebhooks"}
SENS_RE = re.compile(r"(pass|token|secret|cookie)", re.I)
LONG_B64 = re.compile(r'"[A-Za-z0-9+/=]{60,}"')
MARK = "[已脱敏]"


def log(msg: str) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def sensitive(key: str) -> bool:
    return key in SENS_EXACT or bool(SENS_RE.search(key))


def scrub(obj, counter) -> None:
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if sensitive(k):
                if isinstance(v, str) and v:
                    obj[k] = MARK
                    counter[0] += 1
                elif isinstance(v, list) and v:
                    obj[k] = []
                    counter[0] += 1
            else:
                scrub(v, counter)
    elif isinstance(obj, list):
        for x in obj:
            scrub(x, counter)


def has_secret(path: Path) -> bool:
    """只看值，不看键名：文件里有没有非空的敏感值（快速判定，不做修改）。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    hit = [0]

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if sensitive(k) and ((isinstance(v, str) and v) or (isinstance(v, list) and v)):
                    hit[0] += 1
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(d)
    return hit[0] > 0


def redact_file(path: Path, keep_raw: bool, dry_run: bool) -> int:
    d = json.loads(path.read_text(encoding="utf-8"))
    counter = [0]
    scrub(d, counter)
    txt = json.dumps(d, ensure_ascii=False, indent=1)
    residual = LONG_B64.findall(txt)
    log("%s：脱敏 %d 处；残留长串 %d 个" % (path, counter[0], len(residual)))
    if residual:
        log("  ✗ 拒绝写盘（残留 ≥60 字符 base64 串 %d 个，长度 %s）—— 人工看一眼再定"
            % (len(residual), [len(r) for r in residual[:5]]))
        return 3
    out = path.with_name(path.stem + ".redacted.json")
    if dry_run:
        log("  [dry-run] 会写 %s" % out)
        if not keep_raw:
            log("  [dry-run] 会删 %s" % path)
        return 0
    io.open(out, "w", encoding="utf-8", newline="\r\n").write(txt)
    log("  已写 %s（%d 字节）" % (out, out.stat().st_size))
    if keep_raw:
        log("  --keep-raw：原件保留 %s（注意它仍含密文）" % path)
    else:
        os.remove(path)
        log("  已删原件 %s" % path)
    return 0


def find_targets(root: Path) -> list:
    out = []
    for p in root.rglob("*"):
        if ".git" in p.parts or not p.is_file():
            continue
        n = p.name.lower()
        if "scriptconfig" in n and n.endswith(".json") and not n.endswith(".redacted.json"):
            out.append(p)
    return sorted(out)


def selftest() -> int:
    fails = 0

    def ck(cond, what):
        nonlocal fails
        print(("  ok   " if cond else "  FAIL ") + what)
        fails += 0 if cond else 1

    fixture = {
        "instances": [{"uid": "a", "type": "MaaConfig"}],
        "sid-a": {"Info": {"Name": "明日方舟-官-1", "Path": r"E:\MAA-Multi\MAA_1"}, "Emulator": {"Index": "13"},
                  "SubConfigsInfo": {"UserData": {"instances": [{"uid": "u1"}], "u1": {"Info": {
                      "Name": "汐影月k", "Server": "Official", "Status": True,
                      "Password": "AQAAANCMnd8BFdERjHoAwE/Cl+sBAAAAokAYgYvIzEm1BxHPIXnXtgAAAAACAAAAAAAQZgAAAAEAACAAAAAqTGKFfmwiie/bIw6E0xZNrn7o84Kd7n3XO3ssJv8xaw",
                      "SklandToken": "", "ServerChanKey": "", "Notes": "MikuKong"}}}}},
    }
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ScriptConfig.json"
        p.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
        print("自测 1：识别出含密文的目标")
        ck(has_secret(p) is True, "has_secret 认出 Password 非空")
        print("自测 2：脱敏 + 删原件")
        rc = redact_file(p, keep_raw=False, dry_run=False)
        out = p.with_name("ScriptConfig.redacted.json")
        ck(rc == 0, "返回 0")
        ck(not p.exists(), "raw 原件已删")
        ck(out.exists(), "脱敏件已写")
        txt = out.read_text(encoding="utf-8")
        ck(MARK in txt and "AQAAANCM" not in txt, "密文被替换、明文不再出现")
        d = json.loads(txt)
        u = d["sid-a"]["SubConfigsInfo"]["UserData"]["u1"]["Info"]
        ck(u["Name"] == "汐影月k" and u["Server"] == "Official" and u["Notes"] == "MikuKong" and u["Status"] is True,
           "业务字段（登录名/服/备注/开关）原样保留")
        ck(u["SklandToken"] == "" and u["ServerChanKey"] == "", "空值字段不动")
        print("自测 3：干净文件不该被当成目标")
        q = Path(td) / "clean_ScriptConfig.json"
        q.write_text(json.dumps({"a": {"Info": {"Password": ""}}}, ensure_ascii=False), encoding="utf-8")
        ck(has_secret(q) is False, "全空口令 ⇒ has_secret=False")
        print("自测 4：残留长串会被拒")
        bad = {"x": {"Password": MARK, "Note": "A" * 80}}
        ck(bool(LONG_B64.findall(json.dumps(bad, ensure_ascii=False))) is True, "≥60 字符长串能被检出（拒绝写盘那步靠它）")
    print("自测：%s（失败 %d）" % ("全绿" if fails == 0 else "有失败", fails))
    return 0 if fails == 0 else 4


def main() -> int:
    ap = argparse.ArgumentParser(description="raw ScriptConfig.json → 脱敏版（raw 不留）")
    ap.add_argument("--path", help="指定文件")
    ap.add_argument("--scan", action="store_true", help="扫仓库里的 *ScriptConfig*.json")
    ap.add_argument("--root", default=str(ROOT), help="扫描根（默认仓库根）")
    ap.add_argument("--keep-raw", action="store_true", help="只脱敏，不删原件")
    ap.add_argument("--dry-run", action="store_true", help="只看清单，不动文件")
    ap.add_argument("--selftest", action="store_true", help="离线自测")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    targets = []
    if a.path:
        targets = [Path(a.path)]
    elif a.scan:
        targets = find_targets(Path(a.root))
    else:
        log("给 --path 或 --scan 之一")
        return 2

    if not targets:
        log("没找到目标（仓库里没有 raw ScriptConfig 副本）")
        return 0
    dirty = [p for p in targets if has_secret(p)]
    clean = [p for p in targets if p not in dirty]
    for p in clean:
        log("跳过（无任何非空敏感值，不是 raw）：%s" % p)
    if not dirty:
        log("没有含密文的目标，无需处置")
        return 0
    rc = 0
    for p in dirty:
        rc = redact_file(p, a.keep_raw, a.dry_run) or rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
