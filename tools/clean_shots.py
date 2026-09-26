"""清理截图：保留 `tpl_*.png`（脚本输入，imread 它们）+ 最近 50 张运行截图，其余删。

约定：**默认 dry-run**，加 `--apply` 才真删（和 tools/batch_run.py 一致）。

为什么要保 tpl_*（2026-09-17 核对）: click_story.py / find_game_box.py /
video_to_recipe.py / sr4_takeover.py 都 `cv2.imread(HERE/'tpl_*.png')` —— 删了这些脚本
直接崩。而 story_hit_*/choice_hit_*/grid_overlay/crop_* 是它们**写出**的调试帧，可以删。
"""
from __future__ import annotations

import argparse
import pathlib

SR54 = pathlib.Path(r"C:\Users\xiach\.codex\visualizations\2026\09\16"
                    r"\01a0aaf9-9a07-7931-b068-f38e4064ea0f\sr54")
KEEP_RECENT = 50
KEEP_PREFIXES = ("tpl_",)          # 脚本输入，永不删


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真删（默认只列）")
    a = ap.parse_args()

    pngs = sorted(SR54.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    keep = [p for p in pngs if p.name.startswith(KEEP_PREFIXES)][:KEEP_RECENT]
    keep_names = {p.name for p in keep}
    rest = [p for p in pngs if p.name not in keep_names]
    recent = rest[:KEEP_RECENT]
    keep_names |= {p.name for p in recent}
    drop = [p for p in rest if p.name not in keep_names]

    total = sum(p.stat().st_size for p in drop)
    print(f"PNG 共 {len(pngs)} 张")
    print(f"  保留（tpl_* 脚本输入）: {len(keep)} 张")
    print(f"  保留（最近 {KEEP_RECENT} 张）: {len(recent)} 张")
    print(f"  待删: {len(drop)} 张，{total / 1048576:.1f} MB")
    if drop:
        from collections import Counter
        c = Counter(p.name.split("_")[0] + "_" for p in drop)
        print("  待删构成:", dict(c.most_common(8)))

    if not a.apply:
        print("\n（dry-run：没删任何东西。要真删加 --apply）")
        return
    n = 0
    for p in drop:
        try:
            p.unlink()
            n += 1
        except Exception as e:
            print("  删失败", p.name, e)
    left = len(list(SR54.glob("*.png")))
    print(f"\n已删 {n} 张，释放 {total / 1048576:.1f} MB ｜ 剩余 PNG {left} 张")


if __name__ == "__main__":
    main()
