#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task-12 自证（离线、确定性）：主线接入的每一条都能被判据证明。

跑法：python tools/story_nav_selftest.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  —— {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)                                      # type: ignore[union-attr]
    return m


print("== ① 入口坐标：学到的值优先于代码默认值 ==")
learned = json.loads((ROOT / "runtime/learned/story_nav.json").read_text(encoding="utf-8"))
check("story_nav.json 有 entry.terminal/theme_card",
      bool(learned.get("entry", {}).get("terminal")) and bool(learned.get("entry", {}).get("theme_card")),
      json.dumps(learned.get("entry"), ensure_ascii=False))
mn = load(ROOT / "tools/main_nav.py", "main_nav")
check("main_nav 读到 learned 的 terminal",
      tuple(learned["entry"]["terminal"]) == tuple(mn.TERMINAL_BTN), str(mn.TERMINAL_BTN))
check("main_nav 读到 learned 的 theme_card",
      tuple(learned["entry"]["theme_card"]) == tuple(mn.THEME_CARD), str(mn.THEME_CARD))

print("== ② 章节映射：story_mines 里每个关卡的代号 → main_nav 的章号 ==")
sm = json.loads((ROOT / "runtime/plans/story_mines.json").read_text(encoding="utf-8"))
bad = []
n = 0
for ch in sm["chapters"]:
    for u in ch["units"]:
        n += 1
        got = mn.chapter_of_code(str(u["code"]))
        if got != ch["ep"]:
            bad.append((u["code"], ch["ep"], got))
check(f"全部 {n} 个关卡代号都能映射到正确章号", not bad, str(bad[:6]))

print("== ③ 主线单位展开（队列视角） ==")
q = load(ROOT / "runtime/_prime_queue3.py", "prime_queue3")
su = q.story_units()
check("story_units() 非空", len(su) > 0, f"{len(su)} 个单位")
check("章覆盖 7..14", sorted({int(u['prefix']) for u in su}) == list(range(7, 15)),
      str(sorted({u['prefix'] for u in su})))
check("都带 navigator=main_nav", all(u.get("navigator") == "main_nav" for u in su))
check("want ∈ {普通,突袭}", {u["want"] for u in su} <= {"普通", "突袭"}, str({u["want"] for u in su}))
check("都有 stage_id（真打时要用）", all(u.get("stage_id") for u in su))
check("普通单位含 H 关/S 关（它们是独立代号）",
      any(u["code"].startswith("H") for u in su) and any(u["code"].startswith("S") for u in su))

print("== ④ 队列判决（纯函数，用合成数据） ==")
cases = [
    ({"ok": False, "detail": "进不去章节地图"}, "7-2", "nav_fail"),
    ({"ok": True, "on_page": "7-3", "stars": 3, "reward_primes": True}, "7-2", "other_page"),
    ({"ok": True, "on_page": "7-2", "stars": 2, "reward_primes": False}, "7-2", "done"),
    ({"ok": True, "on_page": "7-2", "stars": 2, "reward_primes": True}, "7-2", "mine"),
    ({"ok": True, "on_page": "7-2", "stars": 0, "reward_primes": True}, "7-2", "mine"),
    ({"ok": True, "on_page": "", "stars": None, "reward_primes": True}, "7-2", "mine"),
]
for nav, code, want in cases:
    got = q.story_verdict(nav, code)
    check(f"nav={ {k: nav[k] for k in nav if k != 'detail'} } → {want}", got == want, got)

print("== ⑤ build_units：活动行为不回归 + 主线可并入 ==")
without = q.build_units(with_story=False)
with_story = q.build_units(with_story=True)
check("不传 --with-story 时与改动前一致（195 个活动单位）", len(without) == 195, str(len(without)))
check("--with-story 时 = 195 + story_units",
      len(with_story) == 195 + len(su), f"{len(with_story)} = 195 + {len(su)}")
check("把主线单位筛掉后，活动顺序与改动前**逐个一致**（无回归）",
      [u for u in with_story if u["ev"] != "story"] == without,
      f"活动 {len(without)} 个")
check("主线单位确实进了排产（前 3 个是主线 —— 章 EV 19 压过活动 11~12，符合 task-12 预期）",
      all(u["ev"] == "story" for u in with_story[:3]),
      str([(u["ev"], u["code"]) for u in with_story[:3]]))

print("== ⑥ 战斗内判据（窗口实测：BACK 在战斗里无效，白跑 40s×2）==")
for t in ["0/10 52 剩余可放置角色：8 21", "剩余可放置角色：8", "1 倍速 暂停 0/10"]:
    check(f"战斗 HUD「{t[:16]}」⇒ 判为战斗", mn.is_battle([], t))
for t in ["黑暗时代·上 46/46 完成进度", "EPISODE 00 当前进度 EPISODE 01", "行动结束 继续"]:
    check(f"普通页「{t[:16]}」⇒ 不判为战斗", not mn.is_battle([], t))

print("== ⑦ MAIN THEME 入口 + 详情页判据（Lead 真机情报，2026-09-24）==")
_term = [(742, 519, "MAIN THEME"), (742, 545, "进度"), (820, 570, "怒号光明"), (967, 160, "仓库")]
mtc = mn.main_theme_card(_term)
check("终端页 OCR 出 MAIN THEME ⇒ 给出可点位置（文字下方）", mtc == (742, 545), str(mtc))
check("没有 MAIN THEME 时返回 None（走老路）", mn.main_theme_card([(100, 200, "仓库")]) is None)
_det = [(931, 98, "oPenATion07-2"), (200, 300, "推荐平均等级"), (640, 660, "开始行动"), (900, 500, "报酬")]
ok1, code1 = mn.detail_confirmed(_det, "推荐平均等级 07-2 开始行动 报酬 演习", "7-2")
check("主线详情页（代号 07-2 vs 目标 7-2、不依赖 page_of）⇒ 确认", ok1 and code1 == "07-2", f"{ok1},{code1}")
_det_left = [(120, 300, "07-2"), (640, 660, "开始行动")]
ok1b, code1b = mn.detail_confirmed(_det_left, "07-2 开始行动", "7-2")
check("代号落在左半屏也能确认（panel_code 只看 x>600 的兜底）", ok1b and code1b == "07-2", f"{ok1b},{code1b}")
_map_hits = [(200, 200, "R8-1"), (300, 200, "R8-2"), (984, 685, "EPISODE")]
ok2, _c2 = mn.detail_confirmed(_map_hits, "R8-1 R8-2 EPISODE 07 08 09", "7-2")
check("地图页不会被误判成详情页", not ok2)
_bad = [(931, 98, "oPenATion07-3"), (200, 300, "推荐平均等级"), (640, 660, "开始行动")]
ok3, code3 = mn.detail_confirmed(_bad, "推荐平均等级 07-3 开始行动", "7-2")
check("站在别的关面板上 ⇒ 不确认（返回读到的代号，供上层继续切章）", not ok3 and code3 == "07-3", f"{ok3},{code3}")

print()
if fails:
    print(f"❌ {len(fails)} 项未通过：" + "、".join(fails))
    raise SystemExit(1)
print("✅ 全部通过（task-12 主线接入自证，真机验收另做）")
