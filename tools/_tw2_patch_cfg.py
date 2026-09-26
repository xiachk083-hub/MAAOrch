# -*- coding: utf-8 -*-
"""把 台-2 用户从复制的 Raiz 改成 史蒂芬芬（直改 ScriptConfig.json，补 门2 只改内存不落盘）。

用法:
  python tools/_tw2_patch_cfg.py <拉取的ScriptConfig.json> <输出.json>
先做「原样重序列化字节一致」校验，再改字段并输出 leaf diff。
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

NEW_SID = "b88585f3-d258-42f9-9175-583bbf8164bf"
NEW_UID = "5e8e4684-b697-440c-ad84-fc6484a04de7"
EMAIL = "momoiscoming321@gmail.com"

WANT = {
    "Name": "史蒂芬芬",
    "Id": EMAIL,
    "Notes": f"台服 史蒂芬芬 {EMAIL}",
    "Server": "txwy",
    "Status": True,
    "StageMode": "Fixed",
    "Stage": "1-7",
    "Stage_1": "-",
    "Stage_2": "-",
    "Stage_3": "-",
    "Stage_Remain": "1-7",
    "Annihilation": "Close",
    "Tag": "",
}


def dump(o) -> bytes:
    # 对齐 AUTO-MAS 落盘格式：ensure_ascii=False + indent=2 + LF + 无尾换行
    return json.dumps(o, ensure_ascii=False, indent=2).encode("utf-8")


def leaves(o, p=""):
    """展开成 {路径: 值} 便于逐叶子 diff。"""
    out = {}
    if isinstance(o, dict):
        for k, v in o.items():
            out.update(leaves(v, f"{p}/{k}"))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out.update(leaves(v, f"{p}[{i}]"))
    else:
        out[p] = o
    return out


def main() -> int:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    raw = src.read_bytes()
    data = json.loads(raw.decode("utf-8"))

    # —— 校验 1：原样重序列化必须字节一致 ——
    if dump(data) != raw:
        print("✗ 重序列化字节不一致，不能安全回写")
        a, b = dump(data), raw
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                print("  首个差异 @", i, a[max(0, i - 60):i + 60], "|VS|", b[max(0, i - 60):i + 60])
                break
        return 2
    print("✓ 序列化格式与 AUTO-MAS 落盘格式字节一致（可安全回写）")

    # —— 校验 2：目标对象存在 ——
    node = data[NEW_SID]["SubConfigsInfo"]["UserData"][NEW_UID]["Info"]
    before = leaves(node)
    print("改前:", {k.split("/")[-1]: before.get(k) for k in
                   ("/Name", "/Id", "/Stage", "/Stage_1", "/Stage_Remain", "/Annihilation", "/Server")})

    node.update(WANT)
    after = leaves(node)
    print("\n— 变更的叶子（仅限目标用户）—")
    for k in sorted(set(before) | set(after)):
        if before.get(k) != after.get(k):
            print(f"  {k}\n      {before.get(k)!r} → {after.get(k)!r}")

    # —— 校验 3：全局只应有这些差异 ——
    ua, ub = dump(data), raw
    la, lb = leaves(json.loads(ua.decode("utf-8"))), leaves(json.loads(ub.decode("utf-8")))
    diff = {k for k in set(la) | set(lb) if la.get(k) != lb.get(k)}
    print("\n全局差异叶子数:", len(diff))
    ok = all(k.startswith(f"/{NEW_SID}/SubConfigsInfo/UserData/{NEW_UID}/Info/") for k in diff)
    print("是否全部落在目标用户 Info 内:", ok)

    dst.write_bytes(ua)
    print(f"\n写出 {len(ua)} 字节 → {dst}（原 {len(raw)} 字节）")
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
