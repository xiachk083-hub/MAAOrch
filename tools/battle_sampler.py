#!/usr/bin/env python3
"""肉鸽战斗画面 · **只读**采样器（把战斗帧按帧存下来喂识别/标注）。

抓帧（零输入）→ 判"是不是战斗画面" → 去重后落盘。**与 MAA 并行**：只读、不发点击。

## 判据怎么定下来的（2026-09-19；三段弯路全部实测过）
1. **"手牌 ≥4 张"不行**（`annotate_view` 的旧口径）：肉鸽战斗帧上恒不成立 —— 实测 60 秒 26 帧
   + 连抓 15 帧，`read_deck` 一次都没认到。不是模板坏（同一份代码在 `_live_pa5_bright.png`
   上认到 12 张 ✓），是**手牌行当时不在屏上**；右下角那两个"卡片"其实是**再配置倒计时的
   干员头像**（30.4 秒环 + ⟪24 花费 ✗）—— 我认错过一次。
2. **"战斗 HUD 节点"也不行**：`layout_boxes` 返回的节点是**由仿射推导**出来的（锚点够解方程
   就 22 个全在）⇒ 拿它当闸等于"锚点够不够解方程"，**招募页照样判成战斗** ✗
   （实测把招募页存成了"第 2 局"）。锚点也不可靠：招募页误报 `pause`，真战斗里 `pause` 又时无。
3. **现在用"敌人数 x/y"**（`read_number(..., "ratio")`）：这个读数只有战斗界面才有 ⇒
   招募/事件/掉落/结算页一律 None ✓（同一批帧上验过：战斗帧读到 `0/3`、招募页 None）。
   代价是每轮一次小 ROI OCR（约 1s）。
手牌数照读，只作为元数据附注（读不到记 0，不当闸 ✗）。

## 抓帧通道
  · `framepool`：MaaFramework FramePool，18ms/帧，**但 MAA 自己配置里也是 FramePool**
    （`ScreencapMethod: "FramePool"`）⇒ 同一窗口会被抢，实测 20:36 起持续 `No opaque pixels found` ✗
  · `adb`：`exec-out screencap` 原始流（复用 `battle_loop.shot_fast`，含 16 字节头处理），
    约 0.3~1.7s/帧、偶发短读，但**通道独立**、分辨率就是原生 1920×1080 ✓
  · `auto`（默认）：先试 framepool，不行就 adb，**运行中掉了也会自动切** ✓

用法：
  python tools/battle_sampler.py --minutes 30     # 采 30 分钟（0=一直采）
  python tools/battle_sampler.py --source adb     # 强制走 adb
  python tools/battle_sampler.py --verbose        # 打印跳过原因
  python tools/battle_sampler.py --require-cards  # 只存手牌认得出来的帧（更少更精）

输出（默认 `data/samples/rogue/`，已被 .gitignore 覆盖）：
  <日期>/<HHMMSS>_b<局号>_<序号>.jpg   帧
  index.jsonl                          每帧一行元数据（含 HUD 框坐标，方便标注直接用）
  _status.json / _run.log              实时状态 / 逐行日志
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import pathlib
import shutil
import sys
import time

import cv2
import numpy as np

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

import annotate_live as AL                                       # noqa: E402
import tilepos_audit as TA                                       # noqa: E402  (只要 _mask：绿高亮检测)
import battle_loop as BL                                         # noqa: E402
from maafw_capture import MaafwCapture                           # noqa: E402
from services.ai_agent.percept import Perception                 # noqa: E402

READ_DECK = BL.Loop.read_deck      # 复用现成实现（阈值阶梯 + 等距 + 右锚校验）
HAND_MIN = 4                       # 手牌"算数"的下限（只用于元数据/可选闸）
SMALL = (64, 36)                   # 去重缩略图
HUD_NODES = ("5 暂停/继续", "6 费用(菱形C+数字)", "8 还可部署数")   # 记进元数据的关键框


class _DeckShell:
    """给 `Loop.read_deck` 用的最小外壳（它只读 `.p` 与 `.stage_px`）。

    肉鸽的关卡没有 level 文件，`Loop.__init__` 会 RuntimeError ⇒ 不能为了读数手牌去建整个 Loop。
    """

    def __init__(self, instance: int = 1, client: str = "Official") -> None:
        self.p = Perception.offline("data/test_battle_pa3.png", instance=instance, client=client)
        self.stage_px = 1.5


def _quiet(fn, *a, **kw):
    """压掉被复用函数的 print（它们每帧都会说话），返回值照收。"""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _small(frame: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, SMALL, interpolation=cv2.INTER_AREA).astype(np.int16)


def _alive(pid: int) -> bool:
    """进程还在不在（**只读查询，绝不能用 os.kill(pid, 0)**：Windows 上那等于终止它 ✗）。"""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = wintypes.DWORD()
    ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return bool(ok) and code.value == 259            # STILL_ACTIVE


class Sampler:
    def __init__(self, args) -> None:
        self.a = args
        self.out = pathlib.Path(args.out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.cap = None
        self.source = args.source
        self.shell = _DeckShell()
        self.t0 = time.time()
        self.bytes = sum(p.stat().st_size for p in self.out.rglob("*") if p.is_file())
        self.battles = 0
        self.saved = 0
        self.skip = {"not_battle": 0, "dup": 0, "cap": 0, "no_cards": 0, "grab_fail": 0,
                       "black": 0}
        self.err = ""
        self.in_battle = False
        self.battle_id = 0
        self.n_this_battle = 0
        self.last_small: np.ndarray | None = None
        self.last_save_t = 0.0
        self.last_file = ""
        self.next_status = 0.0
        self.frames = 0
        self.last_battle_t = 0.0     # 最近一次"像战斗"的时刻（时间滞回用）

    # ---------- 抓帧通道 ----------
    def open_source(self) -> None:
        if self.a.source in ("auto", "framepool"):
            try:
                self.cap = MaafwCapture.open()
                self.source = "framepool"
                self.log("抓帧通道：FramePool（18ms/帧，目标帧率 %.1f/s）" % (1 / self.a.period))
                return
            except Exception as exc:                             # noqa: BLE001
                if self.a.source == "framepool":
                    raise
                self.log("FramePool 用不了（%s）⇒ 回退 adb screencap" % str(exc)[:80])
        self.cap = None
        self.source = "adb"
        self.log("抓帧通道：adb（exec-out screencap，原生 1080p，约 0.3~1.7s/帧）")

    def _grab(self) -> np.ndarray | None:
        if self.source == "framepool":
            try:
                f = self.cap.grab()
                if f is not None:
                    if self._usable(f):
                        return f
                    self.skip["black"] += 1
                    return None
            except Exception as exc:                             # noqa: BLE001
                self.err = "FramePool 掉了：%s" % str(exc)[:60]
            if self.err:
                self.log("FramePool 抓不到（%s）⇒ 切 adb" % self.err)
            self.source = "adb"
        f = BL.shot_fast()
        if f is None:
            return None
        if self._usable(f):
            return f
        self.skip["black"] += 1
        return None

    @staticmethod
    def _usable(f: np.ndarray) -> bool:
        """黑帧闸 —— screencap 会**间歇返回黑帧**（这项目记过的坑：~2/10，集中在刚发完输入之后）。
        不拦就会把黑图当"战斗帧"存下来（2026-09-19 实测：拼图里出现一整格纯黑 ✗）。"""
        return float(f.mean()) > 4.0 and float(f.std()) > 8.0

    # ---------- 输出 ----------
    def dir_mb(self) -> float:
        return self.bytes / 1e6

    def status(self, running: bool) -> None:
        (self.out / "_status.json").write_text(json.dumps({
            "running": running, "pid": os.getpid(), "source": self.source,
            "since": time.strftime("%H:%M:%S", time.localtime(self.t0)),
            "now": time.strftime("%H:%M:%S"),
            "battles": self.battles, "saved": self.saved, "in_battle": self.in_battle,
            "frames": self.frames, "skip": dict(self.skip), "dir_mb": round(self.dir_mb(), 1),
            "last_file": self.last_file, "err": self.err,
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    def log(self, msg: str) -> None:
        line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        with (self.out / "_run.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # ---------- 单轮 ----------
    def step(self) -> None:
        frame = self._grab()
        if frame is None:
            self.skip["grab_fail"] += 1
            time.sleep(0.3)
            return
        self.frames += 1
        ui, _tr, _why, _ui_content = _quiet(AL.layout_boxes, frame)
        # **战斗判据 = 敌人数读数（"x/y"）** —— 这一条是**真识别**，不是"仿射解得开"的推导节点：
        # 实测 `layout_boxes` 返回的 22 个节点是由仿射**推导**出来的 ⇒ 拿它当闸等于"锚点够解方程"，
        # 在**招募页**也会判成战斗 ✗（2026-09-19 实测把招募页存成"战斗第 2 局"）。
        # 锚点也不可靠：招募页会误报 `pause`，真战斗里 `pause` 又时无 ✗。
        # 敌人数 x/y 只有战斗界面才有 ⇒ 招募/事件/掉落页一律 None ✓（同一份帧上验过）。
        _roi = (ui or {}).get("2 敌人数 x/y")
        battle_now = bool(_roi) and _quiet(AL.read_number, frame, _roi, "ratio",
                                           loop=self.shell, upscale=2.0) is not None
        # **时间滞回**（不是帧计数）：一局里常有一两帧解不出锚点（拖拽中/闪白/转场遮罩），
        # 立刻判"这局结束"会把一局切成好几段 —— 实测 20:48 那次真实战斗（2.5 分钟）被切成 5 局 ✗。
        # 现在：只要最近 leave_gap 秒内出现过战斗帧，就仍算"在同一局里" ✓。
        if battle_now:
            self.last_battle_t = time.time()
        if (time.time() - self.last_battle_t) >= self.a.leave_gap:
            if self.in_battle:
                self.log("■ 这一局结束（存了 %d 张）" % self.n_this_battle)
            self.in_battle = False
            self.n_this_battle = 0
            self.last_small = None
            if not battle_now:
                self.skip["not_battle"] += 1
            return
        small = _small(frame)
        chg = 0.0 if self.last_small is None else float(np.abs(small - self.last_small).mean())
        # **绿高亮**（选牌后地面/高台亮起，`tilepos_audit` 的同一套掩码）：这是几何体检的唯一素材，
        # 平时抓到它的概率不高（4/142 帧）⇒ 见到就必存（不受 --every/去重限制）✓
        hl = 0
        if self.a.keep_highlight:
            with contextlib.redirect_stdout(io.StringIO()):
                hl = int(TA._mask(frame).sum() // 255)
        fresh = not self.in_battle                    # 本局第一帧（刚从别的界面切进来）
        now = time.time()
        hot_hl = hl >= self.a.hl_min and (now - self.last_save_t) >= self.a.min_gap
        due = (now - self.last_save_t) >= self.a.every
        novel = chg >= self.a.change and (now - self.last_save_t) >= self.a.min_gap
        if self.n_this_battle >= self.a.max_per_battle and not (due or hot_hl):
            self.skip["cap"] += 1
            self.last_small = small
            return
        if not (fresh or due or novel or hot_hl):
            self.skip["dup"] += 1
            self.last_small = small
            return
        deck = _quiet(READ_DECK, self.shell, frame)   # 1~2s；肉鸽里多半是 0（见文件头）
        if self.a.require_cards and len(deck) < HAND_MIN:
            self.skip["no_cards"] += 1
            self.last_small = small
            if self.a.verbose:
                self.log("  跳过：像战斗但手牌只认出 %d 张" % len(deck))
            return
        if fresh:
            self.in_battle = True
            self.battles += 1
            self.battle_id = self.battles
            self.n_this_battle = 0
            self.log("▶ 第 %d 局开始（手牌 %d 张）" % (self.battle_id, len(deck)))
        self.n_this_battle += 1
        self.saved += 1
        self.last_save_t = now
        self.last_small = small
        self.last_file = str(self._save(frame, ui or {}, deck, chg, fresh, hl))

    def _save(self, frame: np.ndarray, ui: dict, deck: list, chg: float, fresh: bool,
              hl: int = 0) -> pathlib.Path:
        d = self.out / time.strftime("%Y-%m-%d")
        d.mkdir(exist_ok=True)
        stem = "%s_b%02d_%02d" % (time.strftime("%H%M%S"), self.battle_id, self.n_this_battle)
        if self.a.format == "png":
            p = d / (stem + ".png")
            cv2.imwrite(str(p), frame)
        else:
            p = d / (stem + ".jpg")
            cv2.imwrite(str(p), frame, [cv2.IMWRITE_JPEG_QUALITY, self.a.quality])
        size = p.stat().st_size
        self.bytes += size
        rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "file": p.as_posix(),
               "battle": self.battle_id, "idx": self.n_this_battle, "first": bool(fresh),
               "src": self.source, "cards": len(deck), "has_hand": len(deck) >= HAND_MIN,
               "roles": [c["role"] for c in deck], "costs": [c.get("cost") for c in deck],
               "hud": {k: ui.get(k) for k in HUD_NODES if k in ui},
               "chg": round(chg, 2), "hl": hl, "note": self.a.note,
               "size_kb": round(size / 1024, 1)}
        with (self.out / "index.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.log("  ✎ %s  %2d 张牌%s  Δ%-5.1f %5.0fKB  [第%d局第%d张]"
                 % (p.name, len(deck), "(有手牌 ✓)" if len(deck) >= HAND_MIN else "",
                    chg, size / 1024, self.battle_id, self.n_this_battle))
        return p


def verify_store(out: pathlib.Path) -> int:
    """按**当前**判据复核已存的帧，不合格的移到 `<out>/_bad/`（不删，留证）。

    为什么需要：判据改过好几轮（手牌数 → HUD 节点 → 敌人数 x/y），**旧帧是按旧判据存进来的**
    ⇒ 存量里混着招募页/黑帧（2026-09-19 实测：拼图里两格都不合格 ✗）。
    只读图、不抓屏、不发输入 ✓。
    """
    idx = out / "index.jsonl"
    if not idx.is_file():
        print("没有 %s" % idx)
        return 2
    shell = _DeckShell()
    rows = [json.loads(l) for l in idx.read_text(encoding="utf-8").splitlines() if l.strip()]
    trash = out / "_bad"
    trash.mkdir(exist_ok=True)
    keep, bad = [], []
    for i, r in enumerate(rows, 1):
        f = pathlib.Path(r["file"])
        im = cv2.imread(str(f)) if f.is_file() else None
        ok = False
        if im is not None and float(im.mean()) > 4 and float(im.std()) > 8:
            ui, _t, _w, _c = _quiet(AL.layout_boxes, im)
            roi = (ui or {}).get("2 敌人数 x/y")
            ok = bool(roi) and _quiet(AL.read_number, im, roi, "ratio",
                                      loop=shell, upscale=2.0) is not None
        if ok:
            keep.append(r)
        else:
            if f.is_file():
                shutil.move(str(f), str(trash / f.name))
            bad.append(r)
        if i % 25 == 0:
            print("   …%d/%d" % (i, len(rows)), flush=True)
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in keep)
    idx.write_text(body + "\n", encoding="utf-8")
    print("复核完成：留 %d 帧 / 移走 %d 帧 → %s（带手牌 %d）"
          % (len(keep), len(bad), trash, sum(1 for r in keep if r["has_hand"])))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="肉鸽战斗画面只读采样器（喂识别/标注）")
    ap.add_argument("--out", default="data/samples/rogue", help="输出目录")
    ap.add_argument("--source", choices=("auto", "framepool", "adb"), default="auto",
                    help="抓帧通道（auto=先 FramePool，不行自动退 adb）")
    ap.add_argument("--period", type=float, default=1.0, help="每轮间隔秒")
    ap.add_argument("--every", type=float, default=6.0, help="同一局内每隔几秒必存一张")
    ap.add_argument("--min-gap", type=float, default=2.0, help="两次保存的最小间隔秒")
    ap.add_argument("--leave-gap", type=float, default=25.0,
                    help="多久没有战斗帧才算这局结束（时间滞回：局中面板打开/拖拽时读不到敌人数）")
    ap.add_argument("--change", type=float, default=5.0, help="画面变化阈值（太像就不存）")
    ap.add_argument("--max-per-battle", type=int, default=24, help="单局上限（超过只按 --every 存）")
    ap.add_argument("--max-gb", type=float, default=4.0, help="输出目录上限，超了自动收尾")
    ap.add_argument("--minutes", type=float, default=0.0, help="0 = 一直采")
    ap.add_argument("--format", choices=("jpg", "png"), default="jpg")
    ap.add_argument("--quality", type=int, default=95)
    ap.add_argument("--note", default="rogue-phantom", help="写进 index.jsonl 的备注")
    ap.add_argument("--require-cards", action="store_true",
                    help="只存手牌认得出来的帧（肉鸽里手牌行时有时无 ⇒ 会很少）")
    ap.add_argument("--verbose", action="store_true", help="打印跳过原因")
    ap.add_argument("--force", action="store_true", help="忽略单实例锁（同目录强行再起一个）")
    ap.add_argument("--keep-highlight", action="store_true", default=True,
                    help="见到绿高亮（选牌态）就必存 —— 几何体检的唯一素材")
    ap.add_argument("--hl-min", type=int, default=20000, help="算作高亮的绿色像素下限")
    ap.add_argument("--verify", action="store_true",
                    help="不开采：按当前判据复核存量帧，不合格的移到 _bad/")
    args = ap.parse_args()

    if args.verify:
        return verify_store(pathlib.Path(args.out))

    s = Sampler(args)
    # **单实例锁**（按输出目录）：两个采样器同时写同一个目录会让局号/计数互相污染 ✗
    # （2026-09-19 实测踩到：`TaskStop` 杀的是 shell，python 子进程成了孤儿继续写 ⇒
    #  index 里出现 b1/b2/b4/b5 交错、状态文件只报 2 局 ✗）
    lk = s.out / "_lock.json"
    if lk.is_file() and not args.force:
        try:
            other = json.loads(lk.read_text(encoding="utf-8"))
        except Exception:                                    # noqa: BLE001
            other = {}
        opid = int(other.get("pid") or 0)
        if opid and opid != os.getpid() and _alive(opid):
            print("✗ %s 上已经有一个采样器在跑（pid=%s，起于 %s）\n"
                  "  停它： taskkill /PID %s /F      强行再起一个： --force"
                  % (s.out, opid, other.get("since"), opid))
            return 3
    lk.write_text(json.dumps({"pid": os.getpid(), "since": time.strftime("%H:%M:%S")}),
                  encoding="utf-8")
    s.log("采样开始：输出 %s；上限 %s 分钟 / %.1fGB；格式 %s"
          % (s.out, args.minutes or "∞", args.max_gb, args.format))
    try:
        s.open_source()
    except Exception as exc:                                     # noqa: BLE001
        s.err = "开抓帧失败：%s" % exc
        s.log(s.err)
        s.status(False)
        return 2
    s.status(True)
    try:
        while True:
            t = time.time()
            s.step()
            if args.minutes and (t - s.t0) >= args.minutes * 60:
                s.log("到时间上限，收尾")
                break
            if s.dir_mb() >= args.max_gb * 1024:
                s.log("到磁盘上限（%.1fGB），收尾" % args.max_gb)
                break
            if time.time() >= s.next_status:
                s.next_status = time.time() + 15
                s.status(True)
            time.sleep(max(0.0, args.period - (time.time() - t)))
    except KeyboardInterrupt:
        s.log("收到中断，收尾")
    finally:
        try:
            if s.cap is not None:
                s.cap.close()
        except Exception:                                        # noqa: BLE001
            pass
        s.status(False)
        s.log("结束：%d 局 / 存 %d 张 / 抓 %d 帧 / 跳过 %s / 目录 %.1fMB / 用时 %.1f 分钟"
              % (s.battles, s.saved, s.frames, s.skip, s.dir_mb(), (time.time() - s.t0) / 60))
        try:
            if lk.is_file() and json.loads(lk.read_text(encoding="utf-8")).get("pid") == os.getpid():
                lk.unlink()                                     # 只删自己那把锁 ✓
        except Exception:                                       # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
