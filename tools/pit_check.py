# -*- coding: utf-8 -*-
"""坑的回归哨兵 —— 把"踩过的坑"变成能重跑的断言。

为什么不是文档/规则库：
  · `rules_db.py` 是**作业修改规则库**（(关卡,类型,干员,from,to) 计数 → 反哺生成轴），
    装不下"工程纪律"这类条目；
  · 写在注释里的纪律会被改没：`load_box` 的返回值就被改坏过一次，
    整条链路静默失效（页面候选全空、预检全跳过），离线看不出来。
  ⇒ 所以每条坑 = 一条**可执行断言**，改坏了这里就红。

用法:
    python tools/pit_check.py          # 6 条全跑，有 FAIL 则 exit 1
    python tools/pit_check.py -v       # 打印证据

**加坑流程（每个新坑都走这条）**：
    1. 发现坑 → 写修复；
    2. 在下面加一条断言（默认按**行为断言**写：真调用一次看返回值/副作用，
       而不是 grep 源码 —— 源码断言拦不住"逻辑改错"）；
    3. 断言跑红过一次才算有效（故意改坏一次验证它真能拦住）。
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import re
import json
import shutil
import subprocess
import sys
import tempfile

PROJ = pathlib.Path(__file__).resolve().parents[1]
PUSH = PROJ / "tools" / "push" / "auto_push.py"
FIT = PROJ / "tools" / "job_fit.py"
SAMPLER = PROJ / "tools" / "battle_sampler.py"

fail: list[str] = []
checked: list[str] = []


def ok(name: str, cond: bool, why: str, evidence: str = "") -> None:
    print("%s  %s%s" % ("PASS" if cond else "FAIL", name, ("   ← " + why) if why and not cond else ""))
    if evidence and VERBOSE:
        print("      " + evidence[:300])
    checked.append(name)
    if not cond:
        fail.append(name)


def sampler_readonly_guard(path=None) -> tuple[bool, str]:
    """坑⑩a 采样器**只读** + 战斗判据是"敌人数 x/y"（真识别），不是推导节点/手牌数。

    "不发输入"只能从源码上拦（没法用行为断言证明"某件事没发生"）；
    判据这一条也是有理由的：2026-09-19 连着踩了两次 —— ① `annotate_view` 的"手牌 ≥4 张"
    在肉鸽战斗帧上恒不成立；② `layout_boxes` 的节点是**仿射推导**出来的，招募页照样判成战斗 ✗。
    """
    p = pathlib.Path(path or SAMPLER)
    if not p.is_file():
        return False, "找不到 %s" % p
    t = p.read_text(encoding="utf-8", errors="replace")
    bad = [k for k in ("minitouch", "PostMessage", "post_message", "click(", "input tap",
                       "shell input") if k in t]
    if bad:
        return False, "采样器里出现输入通道关键字（只读性质没了）：%s" % bad
    miss = [k for k in ('"ratio"', "read_number", "2 敌人数 x/y", "layout_boxes", "_grab",
                        "黑帧") if k not in t]
    if miss:
        return False, "判据/口径常量缺失：%s" % miss
    return True, "源码 %d 字节；无输入通道；判据=敌人数 x/y（read_number ratio）+ 黑帧闸" % len(t)


def sampler_behavior_guard(out, script=None, minutes: float = 0.05, timeout: int = 150) -> tuple[bool, str]:
    """坑⑩b 真跑一次采样器（走 adb、落盘到临时目录）：起得来 + 状态文件字段齐全。

    为什么必须真跑：它的失效模式**是静默的** —— 起不来或闸不亮就一张都不存，
    退出码照样是 0 ⇒ 只看"有没有报错"会假通过。
    """
    py = pathlib.Path(script or SAMPLER)
    r = subprocess.run([sys.executable, str(py), "--source", "adb", "--minutes", str(minutes),
                        "--period", "0.2", "--out", str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, cwd=str(PROJ),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        return False, "退出码 %s：%s" % (r.returncode, (r.stderr or r.stdout)[-160:])
    st = pathlib.Path(out) / "_status.json"
    if not st.is_file():
        return False, "没写出 _status.json（跑没跑都看不出来）"
    d = json.loads(st.read_text(encoding="utf-8"))
    miss = sorted({"running", "source", "battles", "saved", "skip", "dir_mb"} - set(d))
    if miss:
        return False, "状态文件缺字段：%s" % miss
    if "not_battle" not in (d.get("skip") or {}):
        return False, "skip 里没有 not_battle（跳过原因分类丢了）"
    return True, "真跑 %.2f 分钟：通道=%s 抓帧=%s 跳过=%s" % (minutes, d.get("source"),
                                                          d.get("frames"), d.get("skip"))


def load_job_fit():
    spec = importlib.util.spec_from_file_location("job_fit_mod", FIT)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(PROJ / "tools"))
    spec.loader.exec_module(mod)
    return mod


def map_style_guard(path=None) -> tuple[bool, str]:
    """坑⑪ 地图标注样式 = **格心点 + 点间连线**，**不许画方格**。

    用户 2026-09-19 两次点名：早先「竖线横线 或者说标注每个格子的中心点」，
    后又明确「点会在中央 然后点与点之间用线段连接 **不会框出方块**」——
    我中间自己造过一版"分色方块"（`_tileoutline_*`）被否 ✗，所以把这条钉成断言，
    免得第三次画错。
    """
    p = pathlib.Path(path or (PROJ / "tools" / "annotate_live.py"))
    if not p.is_file():
        return False, "找不到 %s" % p
    t = p.read_text(encoding="utf-8", errors="replace")
    if 'if "grid" in layers:' not in t:
        return False, "grid 层不见了"
    seg = t.split('if "grid" in layers:')[1][:800]
    if 'cv2.line(canvas, g["center"]' not in seg or "CEN[nb]" not in seg:
        return False, "grid 层没按「格心 → 相邻格心」连线"
    bad = [k for k in ("polylines", "fillPoly", "cv2.rectangle") if k in seg]
    if bad:
        return False, "grid 层里出现「框方块」画法：%s" % bad
    return True, "grid 层 = 格心点 + 点间线段，无方块 ✓"


def chokepoint_guard(script=None) -> tuple[bool, str]:
    """坑⑫ 堵点排名**只能含可部署格**（放不了人的格不进榜）。

    2026-09-19 用户当场抓到的坑：第一版把 `tile_floor`（可走但**不可部署** ✗）排到第 2 名。

    ⚠️ **第一版哨兵是"假通过"**：它拿真实地图（礼炮小队）跑，而那张图前 8 名本来就是可部署格 ⇒
    把过滤器删掉它也报 PASS ✗。所以改成**确定性构造**：造一个"不可部署格分最高"的假关卡，
    真调 `chokepoints()`，看它有没有被拦住 —— 这样删掉过滤器必红 ✓。
    """
    import importlib.util

    class _Tile:
        def __init__(self, buildable, height=0):
            self.buildable, self.height_type, self.key = buildable, height, "tile_road"

    class _Lv:
        width = height = 3

        def __init__(self):
            # (0,1) 是"可走但不可部署"的格、且分数最高 ⇒ 必须被过滤掉 ✓；(1,1) 可部署 ⇒ 应入选 ✓
            self.grid = {(0, 1): _Tile(0), (1, 1): _Tile(1)}

        def at(self, c, r):
            return self.grid.get((c, r))

    class _L:
        lv = _Lv()

    py = pathlib.Path(script or (PROJ / "tools" / "rogue_routes.py"))
    if not py.is_file():
        return False, "找不到 %s" % py
    spec = importlib.util.spec_from_file_location("rogue_routes_probe", py)
    m = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(PROJ / "tools"))
    sys.path.insert(0, str(PROJ))
    try:
        spec.loader.exec_module(m)
        # passages 元素 = (t, n, route_idx, i, route_len)：这里 i=3 表示"离刷新点 3 步"⇒ 不会被门口规则误杀 ✓
        passages = {(0, 1): [(1.0, 99, 0, 3, 6)], (1, 1): [(1.0, 5, 1, 3, 6)]}
        top = m.chokepoints(_L(), passages, top=8)
        cells = [z[1] for z in top]
        if (0, 1) in cells:
            return False, "不可部署的格 (0,1) 上了榜 ⇒ 过滤器失效"
        if (1, 1) not in cells:
            return False, "可部署的格 (1,1) 反而没上榜 ⇒ 筛选过度"
        return True, "不可部署格被拦住 ✓（%d 项，最高 %s）" % (len(top), cells)
    except Exception as e:                                    # noqa: BLE001
        return False, "检查本身报错: %s" % str(e)[:120]


def main() -> None:
    boxes = sorted((PROJ / "data" / "boxes").glob("box_*.json"))
    jf = load_job_fit()

    # 坑① `load_box` 的返回契约：必须是 (set, dict)，且能按**文件路径**调用
    r = jf.load_box(str(boxes[-1])) if boxes else None
    ok("坑① load_box 返回 (set, dict) 且按路径可用",
       bool(boxes) and isinstance(r, tuple) and len(r) == 2
       and isinstance(r[0], set) and isinstance(r[1], dict) and len(r[1]) > 0,
       "返回值/形状不对（曾经被切片替换吞掉 → 全线静默失效）",
       "boxes=%d, 返回类型=%s, 人数=%s" % (len(boxes), type(r).__name__,
                                       len(r[1]) if isinstance(r, tuple) else "-"))

    src = PUSH.read_text(encoding="utf-8")

    # 坑② 截图绝不能走 `nc_capture`（它的 timeout 是假的，会冻住整条推图）
    calls = [m for m in re.finditer(r"\bnc_capture\s*\(", src)
             if not src[max(0, m.start() - 30):m.start()].rstrip().endswith("#")]
    ok("坑② 截图路径不用 nc_capture（只用 _cap_png）",
       ("def _cap_png" in src) and len(calls) == 0,
       "发现 nc_capture 调用或 _cap_png 消失",
       "nc_capture 调用点=%d" % len(calls))

    # 坑③ 黄点判据只对 EX 区有效（SR 区标记是别的形状）
    ok("坑③ 黄点判据标注为「只对 EX 区」",
       "只对 EX 区" in src and "def has_yellow_dot" in src,
       "EX 限定标注或函数不存在了")

    # 坑④ 「仅突袭」作业必须先确认普通已通关（否则游戏按演习跑 → 白跑还被误记三星）
    ok("坑④ RAID 模式带「普通已通关」前置",
       "RAID_MODE" in src and "普通已通关" in src,
       "突袭守卫被拿掉")

    # 坑⑤ 判赢要同时看三个失败信号（少一个就把败仗记成三星）
    ok("坑⑤ 判赢同时检查 任务失败 / MISSION FAILED / 理智返还",
       all(k in src for k in ("任务失败", "MISSION FAILED", "理智返还")),
       "失败信号漏了")

    # 坑⑥ 地图节点读取要排除终端页右下角的「前往上一次作战」卡片（假节点）
    ok("坑⑥ node 读取带终端卡片过滤",
       ("def read_nodes" in src) and re.search(r"0\.88|right_guard|终端卡片|上一次作战", src) is not None,
       "终端卡片过滤被拿掉（会造成导航空滚 + 假「未解锁」）")

    # 坑⑦ 帧指纹缓存**不得改变 OCR 输出**（同帧两次必须逐字相同；缓存是加速不是改写）
    try:
        sys.path.insert(0, str(PROJ))          # 让 `infrastructure` 可导入（脚本从 tools/ 起跑）
        from PIL import Image

        from infrastructure import ocr as _ocr
        frame = next((p for p in (PROJ / "tools" / "watch").rglob("latest.png")), None)
        if frame is None:
            ok("坑⑦ 帧指纹缓存不改变 OCR 输出", False, "没有可用的帧夹具（tools/watch/**/latest.png）")
        else:
            img = Image.open(frame).convert("RGB")
            keep = _ocr._CACHE_SIZE
            _ocr._CACHE_SIZE = 0
            truth = _ocr.ocr_boxes(img)            # 不走缓存
            _ocr._CACHE_SIZE = keep or 8
            cached = _ocr.ocr_boxes(img)           # 走缓存
            ok("坑⑦ 帧指纹缓存不改变 OCR 输出",
               bool(truth) and truth == cached,
               "缓存改写了结果（同一帧两次输出不一致）",
               "框数=%d" % len(truth))
    except Exception as e:                                  # noqa: BLE001
        ok("坑⑦ 帧指纹缓存不改变 OCR 输出", False, "检查本身报错: %s" % e)

    # 坑⑧ 采样行有两种形态（带/不带时间戳前缀）—— 一套下标解析两种 ⇒ 字段错位
    #      （踩过：把"实例数 13"当"内存 13MB"，直接发出一封"内存跌破 8192"的假告警）
    try:
        spec = importlib.util.spec_from_file_location("hp_mod", PROJ / "tools" / "host_perf.py")
        hp = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(PROJ / "tools"))
        spec.loader.exec_module(hp)
        raw = hp._parse("80|16072|10|13|13|11|MuMuVMMHeadless")
        log = hp._parse("2026-09-18 06:25:33 | 80|16072|10|13|13|11|MuMuVMMHeadless")
        old = hp._parse("76|14476|13|13|12|MuMuVMMHeadless")      # 老格式（无 adb 列）
        bad = hp._parse("ERR|ssh: connect timed out")
        # 最强形态：**真实日志的最后一条**必须能解析（本次两个 bug 都会被这条抓到：
        #   行过滤器只认 5 个竖线 ⇒ 7 字段行被丢；老格式分支把进程名当数字解析）
        last = None
        lf = PROJ / "runtime" / "host_perf.log"
        if lf.exists():
            for ln in reversed(lf.read_text(encoding="utf-8", errors="replace").splitlines()):
                stamp, _, rest = ln.partition("|")
                v = hp._parse(stamp + "|" + rest)
                if v:
                    last = v
                    break
        ok("坑⑧ 采样行解析：新格式两种形态一致 + 老格式兼容 + 真实日志行可解析",
           raw == log and raw is not None and raw[1] == 16072
           and old is not None and old[1] == 14476 and bad is None and last is not None,
           "解析字段错位或行过滤器过严（会退化成 ERR / 发假告警）",
           "raw=%s old=%s last=%s" % (raw, old, last))
    except Exception as e:                                  # noqa: BLE001
        ok("坑⑧ 采样行两种形态解析一致、内存在下标 1", False, "检查本身报错: %s" % e)

    # 坑⑨ **关键路径整段消失** + 三态几何闸必须在位（2026-09-19 真实事故）
    #   事故：我用切片改 tilepos_audit.py 时把 main() 与 __main__ 之间的**五个函数全删了** ✗，
    #   文件 ast 照样过、看起来"完整"，实际关键路径没了 ⇒ 只有"真跑一次"才炸出来。
    #   ⇒ 这条哨兵就是那次"真跑一次"的脚本化：断言关键函数在、登记表可读、三态答得出。
    try:
        import tilepos_audit as TA
        need = ("audit_stage", "record", "trusted", "action_of", "chamfer_audit")
        miss = [n for n in need if not callable(getattr(TA, n, None))]
        ok("坑⑨a 体检器关键函数在位（audit_stage/record/trusted/action_of/chamfer_audit）",
           not miss, "切片/覆盖把关键函数删掉了 ⇒ 只有真跑一次才暴露", "缺: %s" % (miss or "无"))
        import annotate_live as AL
        ok("坑⑨b 标注层走**委托**（stage_trusted → tilepos_audit.trusted，单一真相点）",
           callable(getattr(AL, "stage_trusted", None)),
           "标注层自带一份判定就会与执行闸分叉", "annotate_live.stage_trusted 缺失")
        import battle_loop as BL
        ok("坑⑨c 执行层有部署闸（FALLBACK/UNKNOWN ⇒ 拒部署，fail-closed）",
           callable(getattr(BL.Loop, "deploy_allowed", None)),
           "没有部署闸 ⇒ 会按错坐标拖干员", "Loop.deploy_allowed 缺失")
        reg = pathlib.Path("data/tilepos_audit.json")
        d = json.loads(reg.read_text(encoding="utf-8")) if reg.is_file() else {}
        ok("坑⑨d 登记表 schema 带 action + audit_version（只存证据、无修正值）",
           bool(d) and all(("action" in v and "audit_version" in v) for v in d.values())
           and not any(k for v in d.values() for k in v if "view" in k.lower() or "correct" in k.lower()),
           "登记表格式退化 / 混进修正值（会反污染官方几何）", "条目: %d" % len(d))
        a1, _ = TA.trusted("act51side_01")
        a5, _ = TA.trusted("act51side_05")
        a2, _ = TA.trusted("act51side_02")
        ok("坑⑨e 三态闸答得出（01 TRUSTED / 05 FALLBACK / 未登记 UNKNOWN）",
           a1 and not a5 and not a2, "三态判定退化", "01=%s 05=%s 02=%s" % (a1, a5, a2))
    except Exception as e:                                  # noqa: BLE001
        ok("坑⑨ 几何三态闸关键路径", False, "检查本身报错: %s" % e)

    # 坑⑩ 肉鸽战斗帧采样器（2026-09-19 落地）—— 失效是**静默**的（起不来/闸不亮 ⇒ 一张都不存却仍退 0）
    try:
        g, why10 = sampler_readonly_guard()
        ok("坑⑩a 采样器只读（无输入通道）+ 战斗判据=敌人数 x/y（真识别）", g, why10, why10)
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="pitsampler_"))
        try:
            g2, why10b = sampler_behavior_guard(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        ok("坑⑩b 采样器真跑一次能落盘（静默失效会被这条抓住）", g2, why10b, why10b)
    except Exception as e:                                  # noqa: BLE001
        ok("坑⑩ 肉鸽采样器关键路径", False, "检查本身报错: %s" % e)

    # 坑⑪ 地图标注样式（用户两次点名）：格心点 + 点间线段，不许框方块
    try:
        g11, why11 = map_style_guard()
        ok("坑⑪ 地图标注 = 格心点+点间线段（不许画方格）", g11, why11, why11)
    except Exception as e:                                  # noqa: BLE001
        ok("坑⑪ 地图标注样式", False, "检查本身报错: %s" % e)

    # 坑⑫ 堵点排名只能含可部署格（用户 2026-09-19 当场抓到）
    try:
        g12, why12 = chokepoint_guard()
        ok("坑⑫ 堵点排名只含可部署格（放不了人的格不进榜）", g12, why12, why12)
    except Exception as e:                                  # noqa: BLE001
        ok("坑⑫ 堵点排名可部署性", False, "检查本身报错: %s" % e)

    print()
    if fail:
        print("❌ %d 条坑的守卫失效：%s" % (len(fail), "、".join(fail)))
        sys.exit(1)
    print("✅ %d 条坑的守卫都在" % len(checked))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    VERBOSE = ap.parse_args().verbose
    main()
