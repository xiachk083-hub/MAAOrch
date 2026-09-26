#!/usr/bin/env python3
"""CE-6 一局完整尝试：导航 → 验「代理指挥」关 → 录屏 → 跑我们的作业 → 出证据报告。

为什么要有它（2026-09-20 用户："一直打到通关为止而且必须是三星"）:
    手点一局要十几次往返 + 每次都在不同的界面状态上重新判断 ✗。
    把「一局」封成一条命令，才能一局一局地迭代，且**每局都留下证据**（录像 + 事件流 + 泄漏时刻）。

四条已踩实的规矩（都写进代码里了）：
  1. 作业 `stage_name` 必须是内部 stageId（wk_melee_6）—— 生成器已保证 ✓
  2. 游戏必须停在**关卡详情页**：MAA 的 copilot 不做关卡导航 ✓（从大厅：终端卡 → 资源收集 → 货物运送 → CE-6 节点）
  3. **代理指挥必须手动关**：开着 ⇒ 编队页出现「本次行动配置不可更改」⇒ MAA 自动编队卡死 60 秒 ✗
     （判据用**与参考图的相似度**，不信灰度阈值 ✗ —— 我在这上面读反过两次）
  4. 取帧方式必须是 MaaFwAdb（adb screencap ~300ms/帧 ⇒ 第一次部署落到开打 +23 秒 ⇒ 开局必漏）
  5. **导航走不出来时不要重启游戏**（2026-09-21 用户要求：「其实不需要每次都重启游戏这样」）：
     先「点画面中央消结算/提示层 + 返回」，反复几次出不来就连按返回回大厅重走 —— 绝不 `am force-stop` ✓

用法:
    python tools/ce6_attempt.py --job data/copilot/CE-6_ours_v4.json --tag v5
    python tools/ce6_attempt.py --template-check v30      # 先体检模板有没有过期（不碰游戏）
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import time

import cv2
import numpy as np

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
DEV = "127.0.0.1:16416"   # #1（官服）
MC = pathlib.Path.home() / "AppData/Local/Microsoft/WinGet/Links/maa-cli.exe"
OUT = PROJ / "runtime" / "_ce6"

# 界面锚点（device 坐标）
TAP_TERMINAL_CARD = (890, 120)    # 大厅右侧「终端」卡（2026-09-20 17:5x 实测：文字中心 (898,120)，
#                                  旧值 (1110,170) 实际落在「逐影集趣」活动卡上 ✗ —— 那才是"被弹窗挡住"的真相）
TAP_BACK = (50, 40)               # 返回
TAP_DISMISS = (640, 400)          # 画面中央：结算页/提示弹层都靠点画面消掉（用户要求不重启游戏后加）
TAP_LAST_BATTLE = (1110, 575)     # 作战页右下「前往上一次作战」（不可靠，保留备用）
TAP_RESOURCE = (531, 670)         # 战斗页底部导航「资源收集」（来自 MAA 自己的日志 StageResource ✓）
TAP_CARGO = (430, 400)            # 资源收集页的「货物运送」= CE 系列（龙门币）
TAP_CE6_NODE = (1020, 170)        # CE 关卡地图上的 CE-6 节点
TAP_PRTS_TOGGLE = (1067, 591)     # 代理指挥开关（白方块中心）
BOX_TOGGLE = (1057, 570, 1090, 615)   # 代理开关区域
_NAV_OCR = None                        # 导航时判「这屏是不是提示层」用的 OCR 引擎（懒加载）


def sh(*args, timeout=180):
    env = {"MSYS_NO_PATHCONV": "1"}
    return subprocess.run([ADB, "-s", DEV, *args], capture_output=True, text=True,
                          timeout=timeout, env={**__import__("os").environ, **env},
                          errors="replace")


def shot(name: str) -> pathlib.Path:
    p = OUT / f"{name}.png"
    with open(p, "wb") as f:
        subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"], stdout=f, timeout=60)
    return p


def tap(x, y, wait=5.0):
    sh("shell", "input", "tap", str(x), str(y))
    time.sleep(wait)


def maa(*args, timeout=600):
    """跑一次 maa-cli（环境用隔离的那套目录 ✓）。"""
    import os
    env = {**os.environ, "MAA_DATA_DIR": str(PROJ / "runtime/maa_cli/data"),
           "MAA_CONFIG_DIR": str(PROJ / "runtime/maa_cli/config"),
           "MAA_STATE_DIR": str(PROJ / "runtime/maa_cli/state"),
           "MAA_CACHE_DIR": str(PROJ / "runtime/maa_cli/cache")}
    return subprocess.run([str(MC), *args], capture_output=True, text=True, env=env,
                          timeout=timeout, errors="replace")


def tag_job(tag, job):
    return "%s（作业 %s）" % (tag, pathlib.Path(job).name)


def stage_score(img) -> float:
    """关卡详情页判定 = 与参考模板的匹配分数（2026-09-20 实测：关卡页 1.00 / 其它页 0.22~0.34 ✓）。

    ⚠️ 别用灰度阈值判界面 —— 我在这个文件的第一版里就是这么写的 ✗ 结果 3 轮都判错、
    整局 44 秒就 Error ✗（而"别信阈值、要用参照物"是我自己记过的纪律）。
    """
    tpl = cv2.imread(str(OUT / "tpl_stage.png"))
    if tpl is None:
        raise SystemExit("✗ 缺模板 runtime/_ce6/tpl_stage.png")
    r = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
    return float(r.max())


def prts_is_on(img) -> bool:
    """代理指挥开着吗 —— 拿 ON/OFF 两张参考图比相似度，取更近的那个（不猜灰度 ✓）。"""
    x0, y0, x1, y1 = BOX_TOGGLE
    cur = img[y0:y1, x0:x1]
    best, ans = -1.0, None
    for label, name in ((True, "tpl_prts_on.png"), (False, "tpl_prts_off.png")):
        ref = cv2.imread(str(OUT / name))
        if ref is None or ref.shape != cur.shape:
            continue
        s = float(cv2.matchTemplate(cur, ref, cv2.TM_CCOEFF_NORMED).max())
        if s > best:
            best, ans = s, label
    return bool(ans) if ans is not None else False


SCREENS = [
    ("stage",    "tpl_stage.png",    None),
    ("lobby",    "tpl_lobby.png",    "TAP_TERMINAL_CARD"),
    ("hub",      "tpl_hub.png",      "TAP_RESOURCE"),
    ("resource", "tpl_resource.png", "TAP_CARGO"),
    ("cemap",    "tpl_cemap.png",    "TAP_CE6_NODE"),
]


def which_screen(img) -> str:
    """认出当前在哪个界面（模板匹配取最高分，>0.7 才算认出来 ✓）。

    2026-09-20 的教训：固定连点导航在"当前屏幕不确定"时必挂 ✗（两次把我们带错地方）。
    各界面模板实测分数：本界面 1.00 / 其它界面 0.25~0.51 ✓ 界限清楚。
    """
    best, name = 0.0, "unknown"
    for nm, tpl, _ in SCREENS:
        t = cv2.imread(str(OUT / tpl))
        if t is None or t.shape[0] > img.shape[0] or t.shape[1] > img.shape[1]:
            continue
        s = float(cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED).max())
        if s > best:
            best, name = s, nm
    return name if best > 0.7 else "unknown"


def template_check(tag: str) -> int:
    """体检认屏模板：拿最近一局的 *_nav*.png 当参考帧，看每张模板还认不认得出自己的界面。

    为什么需要它（2026-09-20 实测）：`tpl_lobby.png` 是 04:54 截的，当天游戏大厅多了活动条 ⇒
    到 17:40 再跑时它对大厅只打 **0.640**（阈值 0.7）⇒ 12 步全判 unknown ⇒ 认屏导航整段空转、
    最后误报成"被活动弹窗挡住"✗。**模板会随游戏内容过期，而失败长相和"这屏我不认识"一模一样** ——
    所以每局开始时先体检，把"模板过期"和"真的到了新界面"分开。
    """
    import glob
    frames = sorted(glob.glob(str(OUT / f"{tag}_nav*.png")))
    if not frames:
        print(f"✗ 找不到参考帧 {OUT / (tag + '_nav*.png')}（先跑一局，再用它当 tag）")
        return 4
    imgs = [(pathlib.Path(f).name, cv2.imread(f)) for f in frames]
    imgs = [(n, i) for n, i in imgs if i is not None]
    print(f"参考帧 {len(imgs)} 张（tag={tag}）")

    # ① 先给每帧定身份：**直接调用 which_screen**（同一个判定、同一个 0.7 门槛 ✓）——
    #    我自己在这里再写一遍 argmax 就会和真代码分叉 ✗（第一版就分叉了：把 0.548 的帧认成 hub）
    labels: dict[str, list[tuple[float, str]]] = {}
    bestscore: dict[str, tuple[float, str]] = {}
    for fn, img in imgs:
        labels.setdefault(which_screen(img), []).append((0.0, fn))
        for nm, tpl, _ in SCREENS:
            t = cv2.imread(str(OUT / tpl))
            if t is None or t.shape[0] > img.shape[0] or t.shape[1] > img.shape[1]:
                continue
            v = float(cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED).max())
            if nm not in bestscore or v > bestscore[nm][0]:
                bestscore[nm] = (v, fn)
    for nm in sorted(labels):
        print(f"  身份：{nm:8s} {len(labels[nm])} 帧（which_screen 判定，门槛 0.7）")

    # ② 逐模板判：认得 / 确实过期 / 本局没到过（无法判定，不许诬告 ✓）
    bad = []
    for nm, tpl, _ in SCREENS:
        if cv2.imread(str(OUT / tpl)) is None:
            print(f"  ✗ {tpl} 缺失（界面 {nm} 永远认不出来）")
            bad.append(nm)
            continue
        top = bestscore.get(nm, (0.0, "-"))
        if nm in labels:
            print(f"  ✓ {nm:8s} 本局认出 {len(labels[nm])} 帧（最高 {top[0]:.3f} @ {top[1]}）⇒ 模板可用")
        elif "unknown" not in labels:
            print(f"  ✗ {nm:8s} 参考帧都已认出别的界面、却认不出它（最高仅 {top[0]:.3f} @ {top[1]}）"
                  f"⇒ 模板过期/失效，请重截 {tpl}")
            bad.append(nm)
        else:
            print(f"  ○ {nm:8s} 本局没到过该界面 / 帧身份不明（最高 {top[0]:.3f}）⇒ 判不了，别急着重截")

    # ③ 导航卡死的信号：帧身份要么清一色同一个界面（= 点不动）、要么全 unknown（= 认不出）
    if len(labels) == 1 and "unknown" not in labels and len(imgs) >= 3:
        nm = next(iter(labels))
        print(f"⚠ 全部 {len(labels[nm])} 帧都是「{nm}」⇒ 导航一步都没走出去（点没生效/落点错），不是'到不了'")
    elif labels.get("unknown") and len(labels["unknown"]) == len(imgs) and len(imgs) >= 3:
        print("⚠ 所有帧都判 unknown ⇒ 认屏全挂（先看是不是某个模板过期）")
    return 2 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job")
    ap.add_argument("--template-check", metavar="TAG",
                    help="体检认屏模板（用最近一局的 TAG 的 *_nav*.png 当参考帧）后退出")
    ap.add_argument("--tag")
    ap.add_argument("--record", type=int, default=180)
    # 硬超时：2026-09-21 实测游戏会卡在 Loading 上，而 MAA 在 BattleStartAll 上能空转 15 分钟不报错 ✗
    # ⇒ 必须由我们在外层掐掉，否则整个"跑到三星为止"的循环会被一局拖死。
    ap.add_argument("--job-timeout", type=int, default=420, help="单局作业硬超时（秒）")
    ap.add_argument("--nav-only", action="store_true", help="只导航到关卡页就退出（配合别的小工具用）")
    ap.add_argument("--no-formation", action="store_true",
                    help="不传 --formation（用于「编队已手动摆好、作业直写干员名」的绕法）")
    args = ap.parse_args()

    if args.template_check:
        return template_check(args.template_check)
    if not args.job or not args.tag:
        ap.error("--job 和 --tag 必填（除非用 --template-check）")

    log = OUT / f"{args.tag}.attempt.log"
    def say(m):
        print(m, flush=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(m + "\n")

    say("=== 尝试 %s ===" % tag_job(args.tag, args.job))

    # ① 到达关卡详情页：认屏 → 点该屏该点的东西 → 循环（不认识的界面靠"消弹层+返回"自己走出来，不重启游戏）
    def here():
        return stage_score(cv2.imread(str(shot(f"{args.tag}_probe"))))

    taps = {"lobby": TAP_TERMINAL_CARD, "hub": TAP_RESOURCE, "resource": TAP_CARGO, "cemap": TAP_CE6_NODE}
    unknown = 0
    # 2026-09-21 用户要求：「其实不需要每次都重启游戏这样」⇒ **取消 am force-stop + 重拉游戏**那套兜底。
    # 实测那套又慢（多花 30~40 秒）又打断用户，而且大多数"不认识"只是**结算页/提示弹层**没点掉、
    # 或还停在上一次的编队页 —— 这两种都能靠「点画面中央 + 返回」自己走出来 ✓。
    # 因此改成：先消弹层、再返回，反复几次；真出不来就回大厅重走，绝不重启游戏。
    for step in range(24):
        img = cv2.imread(str(shot(f"{args.tag}_nav{step}")))
        scr = which_screen(img)
        say("  第%d步：认出界面 = %s（关卡页匹配 %.3f）" % (step + 1, scr, stage_score(img)))
        if scr == "stage":
            break
        if scr in taps:
            tap(*taps[scr], wait=6)
            unknown = 0
            continue
        unknown += 1
        if unknown <= 6:
            # ⚠️ 2026-09-21 血的教训：**不能盲目点画面中央** ✗ —— 在游戏主界面按返回会弹出
            #    「确认退出游戏」，那一下中央点击正好落在确认上，把游戏关掉了（之后 24 步全在对牛弹琴）。
            #    所以先用 OCR 看这屏是不是"点击画面以继续"这类提示层：是才点中央，否则只按返回。
            hint = ""
            try:
                from rapidocr_onnxruntime import RapidOCR as _R
                global _NAV_OCR
                if _NAV_OCR is None:
                    _NAV_OCR = _R()
                res, _ = _NAV_OCR(img)
                hint = "".join(t[1] for t in res) if res else ""
            except Exception:
                hint = ""
            if ("点击画面" in hint) or ("以继续" in hint):
                say("    不认识 ⇒ 这是提示层（读到「点击画面」）⇒ 点画面中央，再返回（第 %d 次）" % unknown)
                tap(*TAP_DISMISS, wait=2)
                tap(*TAP_BACK, wait=3)
            else:
                say("    不认识 ⇒ 只按返回（第 %d 次，不点中央以免误触退出）" % unknown)
                tap(*TAP_BACK, wait=3)
        else:
            say("    连续 %d 次仍不认识 ⇒ 连按返回回大厅重走（不重启游戏 ✓）" % unknown)
            for _ in range(4):
                tap(*TAP_BACK, wait=2)
            unknown = 0
    else:
        say("  走了 24 步还没到关卡页 ✗ 停手（游戏没重启过，可人工看一眼屏幕状态）"); return 3
    sc = here()
    if sc <= 0.7:
        say("  收尾复核没到关卡页（%.3f）✗ 停手" % sc)
        return 3
    say("  到关卡详情页 ✓")

    # ② 代理指挥必须关（红线：开着 = 作业不成立，且 MAA 自动编队会被锁死的编队页卡住）
    img = cv2.imread(str(shot(f"{args.tag}_prts")))
    if prts_is_on(img):
        say("  ⚠ 代理指挥开着 ⇒ 点掉它")
        tap(*TAP_PRTS_TOGGLE, wait=4)
        img = cv2.imread(str(shot(f"{args.tag}_prts2")))
        if prts_is_on(img):
            say("  点完还是开着 ✗ 停手（不拿代理局冒充我们的轴）")
            return 2
    say("  代理指挥关着 ✓")
    if args.nav_only:
        say("  --nav-only ⇒ 到此为止（关卡页 ✓ 代理关 ✓）")
        return 0

    # ③ 录屏（后台）+ 跑作业
    # ⚠️ Android 的 screenrecord **只接受 --time-limit 1~180**：传 200/220 会立刻退出并打印
    #    `Time limit 200s outside acceptable range [1,180]` —— 而我把它 stderr 丢了 ⇒
    #    整整 4 局（v19a/v20a/v21a/v22a）"录屏已开 ✓"却一个文件都没落盘 ✗（2026-09-20 实测）。
    #    所以这里 fail-closed 夹到 180，并且把 stderr 存下来。
    rec_secs = max(1, min(int(args.record), 180))
    if rec_secs != args.record:
        say("  ⚠ --record %d 超出 screenrecord 上限 180 ⇒ 按 %d 秒录" % (args.record, rec_secs))
    mp4 = f"/sdcard/{args.tag}.mp4"
    sh("shell", "rm", "-f", mp4)                      # 先清掉同名残留，避免拿旧文件当新证据
    rec = subprocess.Popen([ADB, "-s", DEV, "shell", "screenrecord", "--time-limit", str(rec_secs),
                            "--bit-rate", "4000000", "--size", "1280x720", mp4],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    time.sleep(3)
    if rec.poll() is not None:                        # 立刻退出 = 参数被拒 ⇒ 别装成开好了
        say("  ✗ 录屏进程启动即退出（rc=%s）：%s" %
            (rec.returncode, (rec.stderr.read() or b"").decode("utf-8", "replace").strip()[:200]))
        say("  ⇒ 这一局没有录像可用，但继续跑作业")
    else:
        say("  录屏已开 ✓（%d 秒）" % rec_secs)
    env = {**__import__("os").environ}
    env.update({"MAA_DATA_DIR": str(PROJ / "runtime/maa_cli/data"),
                "MAA_CONFIG_DIR": str(PROJ / "runtime/maa_cli/config"),
                "MAA_STATE_DIR": str(PROJ / "runtime/maa_cli/state"),
                "MAA_CACHE_DIR": str(PROJ / "runtime/maa_cli/cache")})
    runlog = OUT / f"{args.tag}.run.log"
    t0 = time.time()
    # ⚠️ 作业路径必须给**绝对路径**（2026-09-20 实测）：maa-cli 会把相对路径解析到自己的
    #    MAA_CACHE_DIR/copilot/ 下 ⇒ `Failed to parse local copilot file ...cache\copilot\data\...`
    #    然后 1 秒就退出、日志全空 ⇒ 看起来像"跑了一局"，其实一手没动 ✗
    job = str(pathlib.Path(args.job).resolve())
    argv = [str(MC), "copilot", job, "-p", "ce6", "--batch"]
    if not args.no_formation:
        argv.append("--formation")     # 默认自动编队；手动摆过队时用 --no-formation ✓
    # ⚠️ 不要加 --ignore-requirements：2026-09-20 加了它之后**每一局都在编队阶段挂** ✗
    # （v13/v14/v15 连挂三局；撤掉它、其余不变，v2/v8/v11 那种能编成的状态就回来了）
    argv.append(f"--log-file={runlog}")
    try:
        r = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=args.job_timeout,
                           errors="replace")
    except subprocess.TimeoutExpired:
        say("  ✗ 作业超过 %ds 未结束（游戏多半卡在 Loading / MAA 空转）⇒ 掐掉本局" % args.job_timeout)
        try:
            rec.kill()
        except Exception:
            pass
        sh("shell", "pkill", "-f", "screenrecord")
        return 4
    dur = time.time() - t0
    tail = [l for l in (r.stdout or "").splitlines() if "Copilot" in l or "Error" in l]
    say("  作业结束：%.0fs ／ %s" % (dur, " ／ ".join(tail[-2:]) or "（无摘要）"))

    # ④ 收录像：⚠️ 必须**在设备侧优雅收尾**再拉（2026-09-20 实测的坑）：
    #    只 terminate 本地 adb 进程 ⇒ 设备上的 screenrecord 被杀得太急 ⇒ **文件根本没落盘** ✗，
    #    而 `adb pull` 失败又被我忽略 ⇒ 日志照样打印"录像：…" ⇒ 白录一整局（v19a~v22a 全中）。
    #    正解：给设备侧 screenrecord 发 SIGINT（它才会写 moov box），然后**回读文件大小**再判定。
    sh("shell", "pkill", "-SIGINT", "screenrecord")
    time.sleep(4)
    q = sh("shell", "ls", "-la", mp4)
    say("  设备录像：%s" % (q.stdout or "").strip())
    if "No such file" in (q.stdout + q.stderr):
        say("  ✗ 录像没落盘（screenrecord 未生成文件）—— 这一局没有录像可用")
    else:
        pull = subprocess.run([ADB, "-s", DEV, "pull", mp4, str(OUT / f"{args.tag}.mp4")],
                              capture_output=True, text=True, timeout=300,
                              env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"}, errors="replace")
        dst = OUT / f"{args.tag}.mp4"
        ok = dst.exists() and dst.stat().st_size > 100_000
        # ⚠️ 不要只看 pull 的返回码：拉空了它也常返回 0；一律以**本地文件大小**为准
        say("  录像：%s（%s）" % (dst, ("%.1f MB ✓" % (dst.stat().st_size / 1048576)) if ok
                                else "拉取失败/空文件 ✗ " + (pull.stderr or "")[-120:]))


    # ⑤ 事件流（谁在几点上场、编队选了谁）
    if runlog.exists():
        ev = [l.strip()[-150:] for l in open(runlog, encoding="utf-8", errors="replace")
              if "CopilotAction" in l or "BattleFormationSelected" in l]
        for l in ev[-8:]:
            t = re.search(r"\[(\d\d:\d\d:\d\d)", l)
            say("    事件 %s %s" % (t.group(1) if t else "", l[-90:]))
    say("=== 完毕 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
