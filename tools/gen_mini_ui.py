"""极简 UI —— 只干"看候选 + 点执行"，别的都不要。

用法:
    python tools/gen_mini_ui.py --account 751737728 --emu 2 --out ui/mini_751737728.html

规格（用户 2026-09-17《极简执行器》）:
    界面上**只有**：账号名、box 人数、每关的候选列表、每份一个 ▶ 按钮。
    **不要**：进度条、三星/失败标记、状态显示、自动刷新。
    点 ▶ → 起 MAA 跑那份 → 完（脚本不碰导航，人自己把游戏停在详情页）。

判据同 `tools/list_jobs.py`：✅ 缺 0 ｜ ⚠️ 缺 1-2（可能要借）｜ ❌ 缺 3+ 或空壳。
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "tools"))

import job_fit as JF                                           # noqa: E402

STAGES = [f"act54side_{i:02d}_" for i in range(1, 9)] + \
         [f"act54side_ex{i:02d}_" for i in range(1, 9)]


def candidates(pat: str, box: set[str]) -> list[tuple[int, pathlib.Path, str]]:
    files = sorted(set(glob.glob(str(PROJ / "data" / "copilot" / "*" / f"{pat}*.json")))
                   | set(glob.glob(str(PROJ / "data" / "copilot" / pat / "*.json"))))
    rows = []
    for p in files:
        try:
            d = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
        except Exception:                                       # noqa: BLE001
            continue
        if not isinstance(d, dict):
            continue
        if not (d.get("actions") or []):
            rows.append((99, pathlib.Path(p), "空壳"))
            continue
        miss, dead, _t = JF.fit(p, box)
        miss = list(miss) + list(dead)
        rows.append((len(miss), pathlib.Path(p), "缺 " + "、".join(miss[:2]) if miss else "能编满"))
    rows.sort(key=lambda r: r[0])
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="极简 UI 生成器")
    ap.add_argument("--account", required=True)
    ap.add_argument("--emu", required=True)
    ap.add_argument("--box", default="", help="默认 data/boxes/box_<account>.json")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    box_path = pathlib.Path(a.box) if a.box else PROJ / "data" / "boxes" / f"box_{a.account}.json"
    box: set[str] = set()
    if box_path.is_file():
        d = json.loads(box_path.read_text(encoding="utf-8"))
        own = d.get("own") if isinstance(d, dict) else d
        box = {str(o["name"]) for o in (own or []) if isinstance(o, dict) and o.get("name")}

    blocks = []
    for pat in STAGES:
        rows = candidates(pat, box)
        rows = [r for r in rows if r[0] < 99]                    # 空壳不显示（免得点了白跑）
        if not rows:
            continue
        stage_name = ("EX-" + pat.split("_ex")[1].rstrip("_").lstrip("0")) if "_ex" in pat \
            else ("SR-" + pat.rsplit("_", 2)[1].lstrip("0"))
        items = []
        for n, p, why in rows:
            rel = p.relative_to(PROJ / "data" / "copilot")
            cls = "ok" if n == 0 else ("warn" if n <= 2 else "bad")
            items.append(f'<button class="jb {cls}" onclick="go(\'{rel.as_posix()}\')" '
                         f'title="{html.escape(str(p))}">{p.stem.split("_")[-1]}'
                         f'<span class="why">{html.escape(why)}</span></button>')
        blocks.append(f'<section><h2>{stage_name}</h2><div class="row">{"".join(items)}</div></section>')

    page = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>执行器 · {a.account}</title><style>
body{{background:#141414;color:#ddd;font:14px/1.6 "Microsoft YaHei",system-ui;padding:20px}}
h1{{font-size:18px;letter-spacing:.08em}} h1 small{{color:#FF6B00;letter-spacing:.3em;font-size:11px;display:block}}
h2{{font-size:13px;color:#888;letter-spacing:.12em;margin:18px 0 6px}}
.row{{display:flex;flex-wrap:wrap;gap:8px}}
.jb{{background:#222;color:#ddd;border:1px solid #3a3a3a;padding:6px 10px;cursor:pointer;
 font:600 13px/1.3 inherit;text-align:left}}
.jb:hover{{background:#2e2e2e}}
.jb .why{{display:block;font-weight:400;font-size:11px;color:#7a7a7a}}
.jb.ok{{border-color:#4a7a34}} .jb.ok .why{{color:#8fbf6a}}
.jb.warn{{border-color:#8a6a2f}} .jb.warn .why{{color:#ddb066}}
#st{{margin-left:12px;font-size:12px;color:#8fbf6a}} #st.err{{color:#ff8f8f}}
button.scan{{background:#2b2b2b;color:#ffbe6b;border:1px solid #5a4a2a;padding:5px 12px;cursor:pointer}}
</style></head><body>
<h1><small>MINI EXECUTOR</small>账号 {a.account} ｜ box {len(box)} 人 ｜ 模拟器 {a.emu}
  <button class="scan" onclick="scan()">重扫 box</button><span id="st"></span></h1>
{''.join(blocks) or '<p style="color:#888">没有候选：先「重扫 box」，或池子里确实没有这些关的作业。</p>'}
<p style="color:#666;margin-top:26px">人先把游戏停在某关详情页（能看到「开始行动」），再点那一行的 ▶。</p>
<script>
async function go(rel){{
  document.getElementById('st').className='';
  const st=document.getElementById('st');
  st.textContent='已发起：'+rel+' …';
  const r=await fetch('/runjob?job='+encodeURIComponent(rel),{{method:'POST'}});
  const j=await r.json().catch(()=>({{}}));
  if(r.ok){{st.textContent='MAA 跑完：'+rel+'（用时 '+(j.elapsed||'?')+'s，退出码 '+j.rc+'）';}}
  else{{st.className='err';st.textContent='起不来：'+(j.error||r.status);alert(st.textContent);}}
}}
async function scan(){{
  const st=document.getElementById('st'); st.className=''; st.textContent='正在扫 box（要几十秒）…';
  const r=await fetch('/scan',{{method:'POST'}});
  const j=await r.json().catch(()=>({{}}));
  if(r.ok){{st.textContent='扫完，刷新页面看人数'; setTimeout(()=>location.reload(),1500);}}
  else{{st.className='err';st.textContent='扫 box 失败：'+(j.error||r.status);alert(st.textContent);}}
}}
</script></body></html>"""
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    n_jobs = sum(len(candidates(p, box)) for p in STAGES)
    print(f"已生成 {out}（{len(box)} 人 ｜ 候选 {n_jobs} 条）")


if __name__ == "__main__":
    main()
