#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 MAA 干员盒快照盖上**本号 uid**（sidecar），让"并集"这件事可证伪。

为什么需要它（2026-09-24 审计裁决，达芬奇查出、我采纳）：
  · `runtime/maa_cli/data/lib/data/OperBoxData.json` 的键只有 `done/own_opers/source/syncTime`
    —— **没有任何 uid/账号字段**；
  · 而它比本号工作区盒子（`data/workspaces/751737728/box.json`，own=391）**多出 12 个名字**，
    这 12 名在本号盒命中 0、在其余 5 个 workspace（别的号）各命中 7–9 ⇒ **快照可能是别的号**；
  · 干员闸把它们并进"可用集" ⇒ 方向是 **fail-open**（放行偏多 ⇒ 烧真打次数，不丢矿）。
  ⇒ 所以：**落一个 uid，让闸能断言同源**；取不到时闸会告警（默认仍并集，见 `tools/stage_attempt.py`
     的 `trust_snapshot_without_uid`）。

uid 从哪来：**大厅顶栏读到的玩家 ID**（唯一权威、且不依赖任何配置文件）。
优先读 `runtime/stone_ledger.json` 里最近一次核对记录里的 ID/玩家字段；读不到就退回参数 `--uid`。

用法：
    python tools/box_uid_stamp.py                 # 自动从账本找
    python tools/box_uid_stamp.py --uid 751737728 # 显式指定
    python tools/box_uid_stamp.py --show          # 只看现状，不写
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
SNAPSHOT = PROJ / "runtime" / "maa_cli" / "data" / "lib" / "data" / "OperBoxData.json"
SIDECAR = SNAPSHOT.with_name("OperBoxData.uid.json")
LEDGER = PROJ / "runtime" / "stone_ledger.json"

ID_RE = re.compile(r"(?:HODER|ID|玩家\s*ID|记录\s*ID)\D{0,4}(\d{6,12})", re.I)
ANY_ID_RE = re.compile(r"^\d{6,12}$")


def find_uid_in_ledger() -> tuple[str | None, str]:
    """从账本里找大厅读到的玩家 ID ⇒ `(uid, 出处说明)`。读不到返回 `(None, 原因)`。"""
    try:
        d = json.loads(LEDGER.read_text(encoding="utf-8"))
    except Exception as e:                                          # noqa: BLE001
        return None, f"账本读不到（{type(e).__name__}）"

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from walk(v, f"{path}[{i}]")
        else:
            yield path, node

    # ① 先找形状像 ID 的数字，且路径名暗示是 ID/玩家
    for path, val in walk(d):
        low = path.lower()
        if any(t in low for t in ("id", "uid", "玩家")) and isinstance(val, (int, str)):
            s = str(val).strip()
            if ANY_ID_RE.match(s):
                return s, path
        if isinstance(val, str):
            m = ID_RE.search(val)
            if m:
                return m.group(1), path
    return None, "账本里没找到玩家 ID（下次跑批核对大厅后会写进去）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", help="显式指定 uid（不给就从账本找）")
    ap.add_argument("--show", action="store_true", help="只看现状，不写 sidecar")
    a = ap.parse_args()

    uid, why = (a.uid, "--uid 参数") if a.uid else find_uid_in_ledger()
    sync = None
    n = 0
    if SNAPSHOT.exists():
        try:
            d = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
            sync = d.get("syncTime")
            n = len(d.get("own_opers") or [])
        except Exception as e:                                      # noqa: BLE001
            print(f"⚠ 快照读不到：{type(e).__name__}", file=sys.stderr)
    print(f"快照 {SNAPSHOT.name}：{n} 名｜syncTime={sync}")
    if SIDECAR.exists():
        try:
            print(f"现有 sidecar：{json.loads(SIDECAR.read_text(encoding='utf-8'))}")
        except Exception:                                           # noqa: BLE001
            print("现有 sidecar：读不动")
    else:
        print("现有 sidecar：无")
    if a.show:
        return 0
    if not uid:
        print(f"✗ 拿不到 uid（{why}）⇒ 不写 sidecar（闸会按「未确认」告警，默认仍并集）", file=sys.stderr)
        return 1
    SIDECAR.write_text(json.dumps({
        "uid": str(uid), "declared_uid": str(uid), "syncTime": sync,
        "stamped_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "evidence_kind": "declared-not-snapshot-metadata",
        "source": f"本号工作区盒 uid / 大厅顶栏读数（{why}）",
        "why": ("**这是声明，不是证据**：快照文件自己没有 uid 字段，本文件只记录"
                "『我们正在玩的号是谁』。干员闸据此仍然告警（uid_confirmed 只在快照自带 uid 时为真），"
                "以免用『我们的号是谁』冒充『这份快照出自谁』。"),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ 已盖 sidecar：{SIDECAR}（uid={uid}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
