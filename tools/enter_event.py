#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""进活动 —— 从大厅一路点到「活动的关卡地图」，可顺手切到 EX 区。

为什么要有它（2026-09-23 实测）：
    `event_sweep.mjs` / `mt_nav.py` **都不会从大厅进活动**（mt_nav 不在活动图里就 rc=7 收手）。
    而活动地图的 **EX 区不在主区那一屏**：地图靠**横向拖动**翻页，EX 区必须点
    **右下角的区域卡**（LE 实测「夕照大厅」≈(1020,660) / 「小巷深处」≈(1180,660)）才进得去
    —— 我为此白花了半小时（横扫连续 rc=4「5 轮内没能进到详情页」）。
    所以把「大厅 → 曲谱 → 选乐章 → 进入活动 → （切 EX 区）」固定成一条命令。

用法:
    python tools/enter_event.py --name 尘影余音 --prefix LE --zone-ex
    python tools/enter_event.py --name 出苍白海 --prefix EP

输出（stdout 最后一行）: JSON {"ok":bool,"on":"曲谱|地图","codes":[...],"detail":..}
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mt_nav import ADB, DEV, hits_of, shot, tap  # noqa: E402

PROJ = pathlib.Path(__file__).resolve().parent.parent      # 仓库根目录（存归零记录用）

LOBBY_CARD = (873, 200)       # 大厅「终端」大卡
NAV_BOOK = (315, 665)         # 底部导航第 2 个 = 曲谱
ENTER_BTN = (1132, 616)       # 乐章页「进入活动」
BOOK_COLLECT = (1174, 49)      # 曲谱页右上「乐章收录」
TAB_PROGRESS = (959, 42)       # 乐章收录页第三个页签「完成进度」
ZONE_Y = (640, 695)           # 区域卡所在的那一条
ZONE_X_MIN = 850


def txt_of(hits) -> str:
    return " ".join(t for _, _, t in hits)


def back():
    subprocess.run([ADB, "-s", DEV, "shell", "input", "keyevent", "4"], timeout=30)
    time.sleep(2.5)


def find(hits, needle, ymin=0, ymax=720, xmin=0, xmax=1280, exact=False):
    """按文字找 token —— **先去掉空格**再比（OCR 常把「Story Line · LE」拆成带空格的）。"""
    nd = needle.replace(" ", "")
    for x, y, t in hits:
        tt = t.strip()
        if not (ymin <= y <= ymax and xmin <= x <= xmax):
            continue
        flat = tt.replace(" ", "")
        if (flat == nd) if exact else (nd in flat):
            return (x, y, tt)
    return None


def find_retry(needle, ymin=0, ymax=720, tries=3, gap=2.5):
    """连拍几张再认 —— 预览面板有淡入动画，只拍一次会漏（2026-09-23 实测）。"""
    for i in range(tries):
        hits = hits_of(shot(f"fr{ymin}{i}"))
        hit = find(hits, needle, ymin=ymin, ymax=ymax)
        if hit:
            return hit, hits
        time.sleep(gap)
    return None, hits


def zone_cards(img):
    """右下角的区域卡（**带框**）。

    实测坑（2026-09-23）：RapidOCR 会把两张区域卡**并成一个 token**
    「夕照大厅小巷深处」@x≈1123 —— 拿中心点去点会落在两张卡中间 ⇒ 切不过去。
    所以这里保留 x0/x1：**并框（宽>170）就点右边那张的右半边**。
    """
    global _eng
    if _eng is None:
        from rapidocr_onnxruntime import RapidOCR
        _eng = RapidOCR()
    res, _ = _eng(img)
    out = []
    for box, text, *_ in (res or []):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x0, x1 = min(xs), max(xs)
        yc = (min(ys) + max(ys)) / 2
        tt = text.strip()
        if x0 >= ZONE_X_MIN and ZONE_Y[0] <= yc <= ZONE_Y[1] and len(tt) >= 2:
            tx = (x1 - 60) if (x1 - x0) > 170 else (x0 + x1) / 2
            out.append({"x": int(tx), "y": int(yc), "text": tt, "wide": (x1 - x0) > 170})
    return sorted(out, key=lambda d: -d["x"])


_eng = None


def to_book(prefix: str, log) -> bool:
    """把游戏弄到「曲谱」页（沿用底部导航第 2 个）。"""
    for i in range(6):
        hits = hits_of(shot(f"book{i}"))
        txt = txt_of(hits)
        if "乐章收录" in txt and find(hits, "曲谱", ymin=620):
            log(f"  已在曲谱页（第 {i} 步）")
            return True
        if find(hits, "采购中心") or find(hits, "公开招募"):
            log("  在大厅 → 点终端大卡")
            tap(*LOBBY_CARD, wait=5.0)
            continue
        if find(hits, "MAIN THEME") or find(hits, "终端", ymin=620):
            log("  在终端页 → 点底部「曲谱」")
            tap(*NAV_BOOK, wait=5.0)
            continue
        log("  认不出页面 → 按返回")
        back()
    return False


def enter_via_progress(name: str, prefix: str, zone_ex: bool, log) -> dict:
    """**推荐路线**（2026-09-23 晚实测）：曲谱 → 右上「乐章收录」→「完成进度」页
    → 在一格格卡片里找乐章名（这页卡片是网格、位置随滚动漂，但**名字 OCR 得出来**，
    而且卡片点下去就直接进该乐章的详情页）→ 「进入活动」。"""
    res = {"ok": False, "on": "", "codes": [], "detail": ""}
    if not to_book(prefix, log):
        res["detail"] = "没能进到曲谱页"
        return res
    log("  打开「乐章收录」→「完成进度」")
    tap(*BOOK_COLLECT, wait=5.0)
    tap(*TAB_PROGRESS, wait=4.0)

    card = None
    # ① **归零**（用户 2026-09-24 提醒：这页竖直很长，必须先量长度）：
    #    先往下滑到"画面不再变"（= 到底了，滑了几次就是**页面长度**），
    #    再往上滑回"画面不再变"（= 到顶，确定起点）。之后找卡才是确定性的，不是碰运气。
    #    ⚠️ 滑动手势**必须慢**（duration ≈1100ms）：500ms 的快滑在这页上完全不滚动。
    def _grid_zero() -> dict:
        import fastnav
        import numpy as np
        import cv2
        sh = fastnav.Shell.get()

        def _frame_small():
            """服务端取帧 → 只取**卡片所在的中段**（y 90~660）缩到 80x36 灰度。
            取中段是为了甩掉上下边缘的飘动装饰；判"动没动"用它做平均像素差。"""
            import urllib.request
            p = PROJ / "runtime" / "_zero.png"
            try:
                urllib.request.urlopen(f"http://127.0.0.1:8765/shot?path={p}", timeout=15).read()
                im = cv2.imread(str(p))
                if im is None:
                    return None
                band = im[90:660, :]
                return cv2.resize(cv2.cvtColor(band, cv2.COLOR_BGR2GRAY), (80, 36),
                                  interpolation=cv2.INTER_AREA)
            except Exception:
                return None

        def slide(down: bool) -> int:
            """往一个方向滑，直到"画面不再动"为止。

            用户 2026-09-24 的观察一针见血：**明明到底了还在一直滑** ⇒ 我原来的判停是坏的。
            坏在哪：`wait_settle()` 用 0.12s 间隔的两帧相等就返回，而这页的滚动有缓动动画，
            动画刚开始时相邻两帧也可能一模一样 ⇒ 我在"还在滚"的时候取帧，前后帧当然都不同
            ⇒ diff 永远超标，一路滑到上限。改法：
              ① 滑完**固定等 1.5s** 让缓动跑完再取帧；
              ② 用"这一次的静止帧"和"上一次的静止帧"比，diff < 2.0 连续两次才算真的到头；
              ③ 把每次的 diff 打进日志，肉眼能看出到底了没有。
            """
            import numpy as np
            n, still = 0, 0
            prev = _frame_small()
            for _ in range(10):
                if down:                      # 手指向下拖 = 内容向下 = 往**顶部**走
                    sh.swipe(640, 240, 640, 560, 900)
                else:                         # 手指向上拖 = 往**底部**走
                    sh.swipe(640, 560, 640, 240, 900)
                time.sleep(1.5)               # 等缓动结束（这是判停正确的前提）
                cur = _frame_small()
                if prev is None or cur is None:
                    log("    取帧失败，停")
                    break
                diff = float(np.abs(cur.astype(np.int16) - prev.astype(np.int16)).mean())
                prev = cur
                n += 1
                log(f"    第{n}滑 diff={diff:.1f}")
                if diff < 2.0:
                    still += 1
                    if still >= 2:
                        log("    ⇒ 连续两次几乎没动，判定到头")
                        break
                else:
                    still = 0
            return n

        n_down = slide(True)             # 到底：得到页面长度（屏数）
        n_up = slide(False)              # 回顶：得到确定性起点
        try:
            (PROJ / "runtime" / "_grid_len.json").write_text(
                json.dumps({"screens": n_down, "back": n_up, "ts": time.strftime("%H:%M:%S")}),
                encoding="utf-8")
        except Exception:
            pass
        log(f"  归零：向下 {n_down} 屏到底，向上 {n_up} 屏回顶（页面长度 ≈ {n_down} 屏）")
        return {"down": n_down, "up": n_up}

    try:
        _grid_zero()
    except Exception as e:                                    # noqa: BLE001
        log(f"  归零失败（继续按老办法找卡）：{type(e).__name__}: {e}")
        for _ in range(4):
            try:
                import fastnav
                fastnav.Shell.get().swipe(640, 220, 640, 580, 1100)
                fastnav.wait_settle(max_wait=1.6)
            except Exception:
                subprocess.run([ADB, "-s", DEV, "shell", "input", "swipe",
                                "640", "220", "640", "580", "1100"], timeout=30)
                time.sleep(1.2)
    def needles(nm: str) -> list[str]:
        """乐章名的匹配候选：全名 → 前 3 字 → 后 3 字 → 3 字滑窗 → 2 字滑窗（长优先）。
        为什么要滑窗（2026-09-23 晚）：`为戏剧，为永恒` 这种带中文顿号的短名，
        OCR 常见把标点吃成别的字符（半角逗号/顿号/省略），整名匹配必失败。"""
        out = [nm, nm[:3], nm[-3:]]
        for w in (3, 2):
            out += [nm[i:i + w] for i in range(0, max(1, len(nm) - w + 1))]
        seen, uniq = set(), []
        for x in out:
            if len(x) >= 2 and x not in seen:
                seen.add(x)
                uniq.append(x)
        return uniq

    for i in range(14):
        # y>60：跳过顶部页签（默认进度/奖励进度/完成进度/乐章），短词很容易撞上它们
        hits = [h for h in hits_of(shot(f"pg{i}")) if h[1] > 60]
        for needle in needles(name):
            card = find(hits, needle)
            if card:
                break
        if card:
            break
        subprocess.run([ADB, "-s", DEV, "shell", "input", "swipe",
                        "640", "560", "640", "200", "1100"], timeout=30)
        time.sleep(2.0)
    if not card:
        res["detail"] = f"完成进度页里翻不到乐章「{name}」"
        return res
    log(f"  点乐章卡「{card[2]}」@({card[0]},{card[1]})")
    tap(card[0], card[1], wait=5.0)
    hits = hits_of(shot("pgcard"))
    if not (find(hits, "本章回想") or find(hits, "进入活动")):
        # 可能点歪了（点到隔壁卡）⇒ 退回来重来一次
        log("  没进到乐章页（点歪？）⇒ 返回重试")
        back()
        hits = hits_of(shot("pgretry"))
        for needle in (name, name[:3]):
            card = find(hits, needle)
            if card:
                tap(card[0], card[1], wait=5.0)
                break
    return after_enter(zone_ex, prefix, res, log)


def after_enter(zone_ex: bool, prefix: str, res: dict, log) -> dict:
    """点「进入活动」→（可选）切 EX 区 → 报屏上的关卡代号。"""
    hits = hits_of(shot("enter"))
    btn = find(hits, "进入活动") or find(hits, "开始行动")
    tap(*(btn[:2] if btn else ENTER_BTN), wait=9.0)
    hits = hits_of(shot("map"))
    txt = txt_of(hits)
    pre = (prefix + "-").upper() if prefix else ""
    for i in range(4):
        if any(t.strip().upper().startswith(pre) for _, _, t in hits if pre) or "OPERATION" in txt.upper():
            break
        time.sleep(3.0)
        hits = hits_of(shot(f"map{i}"))
        txt = txt_of(hits)
    codes = sorted({t.strip().upper() for _, _, t in hits
                    if pre and t.strip().upper().startswith(pre)})
    res["codes"] = codes
    if not codes and "OPERATION" not in txt.upper():
        res["detail"] = f"没进到活动地图（屏上：{txt[:80]}）"
        return res
    log(f"  已进地图，屏上关卡：{codes}")
    if zone_ex:
        zs = zone_cards(shot("zonefind"))
        if zs:
            # 优先挑**名字里带 EX 的区域卡**（EP 实测就叫「EX」、PV 是「EX 彩车游行」）；
            # 没有 EX 字样的才按"右到左"依次试（LE 实测：夕照大厅 / 小巷深处，EX 区在右边）。
            # **点完必须验货**：2026-09-24 00:30 实测 PV 点错到 S 区（屏上只有 PV-S-1）
            #   ⇒ 横扫连着报「5 轮内没能进到 PV-EX-3 的详情页」。所以现在点一张、看代号，
            #   不是 `-EX-` 就换下一张。
            zs.sort(key=lambda c: (0 if "EX" in c["text"].upper() else 1, -c["x"]))
            for z in zs[:4]:
                log(f"  切 EX 区：点区域卡「{z['text']}」@({z['x']},{z['y']})"
                    + ("（并框→点右边那张）" if z["wide"] else ""))
                tap(z["x"], z["y"], wait=4.5)
                hits = hits_of(shot("zone"))
                codes = sorted({t.strip().upper() for _, _, t in hits
                                if pre and t.strip().upper().startswith(pre)})
                res["codes"] = codes
                log(f"  切区后屏上关卡：{codes}")
                if any("-EX-" in c for c in codes):
                    break
                log("  ✗ 这张不是 EX 区 ⇒ 换下一张区域卡")
        else:
            log("  屏幕上没认出区域卡（可能本来就在 EX 区）")
    res.update(ok=True, on="地图", detail="已在地图")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="乐章名，如 尘影余音 / 出苍白海")
    ap.add_argument("--prefix", default="", help="关卡前缀，用于在曲谱页选分组行（如 LE / EP）")
    ap.add_argument("--zone-ex", action="store_true", help="进图后再切到 EX 区（点最右的区域卡）")
    ap.add_argument("--via-book", action="store_true", help="用旧的曲谱页分组行路线（默认走完成进度页）")
    a = ap.parse_args()
    logs: list[str] = []

    def log(m):
        logs.append(m)
        print(m, flush=True)

    if a.via_book:
        res = enter_via_book(a.name, a.prefix, a.zone_ex, log)
    else:
        res = enter_via_progress(a.name, a.prefix, a.zone_ex, log)
    res["detail"] = res.get("detail") or f"已在 {a.name} 地图"
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else (7 if "曲谱" in res["detail"] else 4)


def enter_via_book(name: str, prefix: str, zone_ex: bool, log) -> dict:
    """旧路线：曲谱页左栏点分组行 → 点预览面板里的乐章卡。左栏滚动位置会让它失手。"""
    res = {"ok": False, "on": "", "codes": [], "detail": ""}
    if not to_book(prefix, log):
        res["detail"] = "没能进到曲谱页"
        return res

    # ①② 找乐章卡：直接找 → 找不到就把左栏的分组行一个个点过去（预览面板跟着换）
    #     → 还找不到就把左栏滚一滚再试。
    #     为什么（2026-09-23 晚踩到）：左栏列表**会保持上次的滚动位置**，某次进来时顶上
    #     顶着 `Story Line ·RL`、`·LE` 那行在屏幕外 ⇒ 只找一次必然「找不到乐章」，
    #     队列白跑一整轮（8 个单位全 rc=7）。
    def card_now(hs):
        return find(hs, name) or find(hs, name[:3], ymin=300)

    hits = hits_of(shot("card0"))
    card = card_now(hits)
    if not card:
        # 左栏「Story Line ·XX」的行 —— **OCR 会把它拆成两个 token**（`Story Line ·` + `LE`，
        # 实测见 `Story Line ·` @(203,417) 与 `SU` @(258,416) 同一行）⇒ 三个来源都当候选：
        # 带 `·XX` 的、单独的两位大写字母、含 `Story` 的。
        def rows_of(hs):
            out = []
            for x, y, t in hs:
                if y >= 645 or x >= 480:
                    continue
                tt = t.strip()
                if (re.search(r"·\s*[A-Z]{2}", tt) or re.fullmatch(r"[A-Z]{2}", tt)
                        or "Story" in tt):
                    out.append((x, y, tt))
            return out

        tried: set[tuple[int, int]] = set()
        for rnd in range(6):
            rs = [r for r in rows_of(hits) if (r[0], r[1]) not in tried]
            if not rs:
                step = -1 if rnd % 2 else 1          # 来回滚，覆盖整个列表
                subprocess.run([ADB, "-s", DEV, "shell", "input", "swipe",
                                "250", str(560 if step > 0 else 180),
                                "250", str(180 if step > 0 else 560), "500"], timeout=30)
                time.sleep(2.0)
                hits = hits_of(shot(f"cardscroll{rnd}"))
                continue
            for r in rs[:6]:
                tried.add((r[0], r[1]))
                log(f"  点分组行 {r[2]} @({r[0]},{r[1]})")
                tap(r[0], r[1], wait=3.5)
                hits = hits_of(shot(f"cardr{rnd}_{r[0]}"))
                card = card_now(hits)
                if card:
                    break
            if card:
                break
    if not card:
        card, hits = find_retry(name[:3], ymin=300, tries=4)
    if not card:
        card, hits = find_retry(name[:2], ymin=300, tries=2)
    if not card:
        res["detail"] = f"曲谱页上找不到乐章「{name}」"
        print(json.dumps(res, ensure_ascii=False))
        return 4
    log(f"  点乐章卡「{card[2]}」@({card[0]},{card[1]})")
    tap(card[0], card[1], wait=5.0)
    return after_enter(zone_ex, prefix, res, log)


if __name__ == "__main__":
    raise SystemExit(main())
