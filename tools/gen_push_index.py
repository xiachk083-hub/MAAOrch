"""多账号总览页：三个号一眼看全（进度/理智/最近事件 + 跳各自详情）。

用法: python tools/gen_push_index.py        # 生成 ui/index.html

为什么单独成页（用户 2026-09-17）: 三个号各有一张 push_agent_<uid>.html，逐个开太慢；
这页只做汇总，数据全部来自各号的 push_state_<uid>.json 与各自日志（**只读，不花理智**）。
"""
from __future__ import annotations

import datetime as dt
import html
import json
import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "tools"))
import gen_push_ui as G  # noqa: E402  —— 复用它的"读日志/读状态"逻辑，别再写一份

SR54 = G.LOG_DIRS[0]
ACCOUNTS = PROJ / "data" / "accounts.json"
SANITY_FILE = PROJ / "data" / "sanity.json"
OUT = PROJ / "ui" / "index.html"


def load_json(p: pathlib.Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def counts(stages: dict) -> tuple[int, int]:
    """→（SR 已通关数, EX 已通关数）；二星/通关（星级未核）都算通关（有没有三星看各自页面）"""
    sr = sum(1 for i in range(1, 9)
             if (stages.get(f"SR-{i}") or {}).get("status") in ("三星", "二星", "通关"))
    ex = sum(1 for i in range(1, 9)
             if (stages.get(f"EX-{i}") or {}).get("status") in ("三星", "二星", "通关"))
    return sr, ex


def main() -> None:
    accs = load_json(ACCOUNTS, {}).get("accounts", [])
    sanity = load_json(SANITY_FILE, {})
    rows, events = [], []

    for acc in accs:
        uid = acc["uid"]
        state = load_json(SR54 / acc.get("state", ""), {}).get("stages", {})
        sr, ex = counts(state)
        rows.append({
            "name": acc["name"], "client": acc["client"], "uid": uid,
            "sr": sr, "ex": ex,
            "sanity": sanity.get(uid, "?"),
            "url": f"http://127.0.0.1:{acc['channel_port']}/",
            "port": acc["channel_port"],
        })
        G.LOG_KEY = uid                       # 先按 uid 找这个号的日志
        ev = G.read_events(limit=8)
        if not ev:                            # 9036 的日志还是老名字（不带键）
            G.LOG_KEY = ""
            ev = G.read_events(limit=8)
        for t, s in ev:
            events.append((t or "", f"{acc['name']}", html.escape(s)))
    G.LOG_KEY = ""

    events.sort(key=lambda x: x[0], reverse=True)
    events = events[:12]

    trs = "\n".join(
        f'<tr><td><b>{r["name"]}</b> <span class="mut">{r["client"]} · {r["uid"]}</span></td>'
        f'<td>{r["sr"]}/8</td><td>{r["ex"]}/8</td>'
        f'<td>{r["sanity"]}</td>'
        f'<td><a href="{r["url"]}">详情 :{r["port"]}</a></td></tr>' for r in rows)
    evs = "\n".join(f'<div class="ev"><span class="t">{t or "—"}</span> '
                    f'<span class="who">{w}</span> {s}</div>' for t, w, s in events)

    page = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>多账号推图总览</title>
<style>
body{{background:linear-gradient(160deg,#1a1a1a,#2d2d2d 70%,#111);color:#eee;
 font:14px/1.7 "Microsoft YaHei",system-ui,sans-serif;padding:24px}}
h1{{font-size:22px;letter-spacing:.1em}} h1 small{{color:#FF6B00;letter-spacing:.3em;
 display:block;font-size:11px;font-weight:600}}
table{{width:100%;border-collapse:collapse;margin-top:14px;max-width:900px}}
td,th{{padding:10px 12px;border-bottom:1px solid #333;text-align:left}}
th{{color:#7a7a7a;font-size:11px;letter-spacing:.1em}}
a{{color:#00D9FF;text-decoration:none}} a:hover{{text-decoration:underline}}
.mut{{color:#7a7a7a;font-size:12px}}
.ev{{border-left:2px solid #FF6B00;padding:2px 10px;margin:6px 0;background:#242424}}
.ev .t{{color:#7a7a7a;font-size:12px;margin-right:8px}}
.ev .who{{color:#FF6B00;margin-right:6px}}
.box{{max-width:900px;background:#242424;padding:14px 18px;margin-top:18px;
 clip-path:polygon(0 0,calc(100% - 14px) 0,100% 14px,100% 100%,14px 100%,0 calc(100% - 14px))}}
.gen{{color:#7a7a7a;font-size:12px;margin-top:14px}}
</style></head><body>
<h1><small>MULTI-ACCOUNT</small>多账号推图总览</h1>
<table>
 <tr><th>账号</th><th>SR</th><th>EX</th><th>理智</th><th></th></tr>
 {trs}
</table>
<div class="box"><h3>最近事件</h3>{evs or '<div class="mut">（暂无）</div>'}</div>
<div class="gen">生成时间 {dt.datetime.now():%Y-%m-%d %H:%M} ｜ 活动截止 2026-09-18 03:59
 ｜ 三号共用一个推图 Agent，理智回复慢，**建议错开跑**</div>
</body></html>"""
    OUT.write_text(page, encoding="utf-8")
    print(f"已生成 {OUT}")
    for r in rows:
        print(f"  {r['name']:6s} {r['client']:4s} SR {r['sr']}/8 EX {r['ex']}/8 理智 {r['sanity']}")


if __name__ == "__main__":
    main()
