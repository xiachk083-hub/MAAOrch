"""战斗画面标注 · 可视化界面（本地网页）

用户要求：把"量出来的标注"做成能看的界面 —— 实时截帧、叠上四层标注、可逐层开关、旁边列出实测数值。

    python tools/annotate_view.py                 # 默认 8930，从模拟器实时截
    python tools/annotate_view.py --port 8931 --img data/_live_now4.png   # 拿固定图演示（不连模拟器）

页面：
    GET /                界面（每 3 秒自动刷，可关）
    GET /frame.png?v=&layers=ui,cards,grid,ops   标注后的帧（PNG）
    GET /measure.json    这一帧的全部量测值

设计取舍
  · 只用标准库 + cv2（不引前端框架）—— 这是个"看"的工具，不是产品页面
  · 量测全走 tools/annotate_live.py（**同一份代码**），界面上标的就是刚量的，不存在"两套数"
  · 截图失败/不是战斗界面时**如实说**（把状态写在页面上），不拿上一帧冒充
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "tools"))

import annotate_live as AL  # noqa: E402
import battle_loop as BL  # noqa: E402

# RLock（可重入）：render_layers 持锁后会再调 snapshot()，普通 Lock 会自锁死（踩过 ✗）
_lock = threading.RLock()
# png 按 (layers,panel) 分键缓存 —— 否则 /measure.json 先渲染一次(panel=True)会把
# 带图例栏的图塞进缓存，随后图片请求直接吃缓存 ⇒ 图层开关失灵（踩过）
_cache: dict = {"t": 0.0, "png": {}, "meas": None, "err": ""}
ARGS: argparse.Namespace
_FAST: dict = {"cap": None, "src": "adb", "err": ""}   # 帧源：FramePool 优先、adb 兜底


def grab() -> tuple[np.ndarray | None, str]:
    """取一帧：--img 固定图 / 否则从模拟器实时截（黑帧重试在 battle_loop.shot 里）。"""
    if ARGS.img:
        im = cv2.imdecode(np.fromfile(ARGS.img, np.uint8), cv2.IMREAD_COLOR)
        return (im, "") if im is not None else (None, f"读不到图 {ARGS.img}")
    # ① 首选 **MaaFramework FramePool**（实测 19ms/帧 ≈ 52fps，**不走 adb、不发输入** ⇒ 不干扰 MAA）
    try:
        if _FAST["cap"] is None:
            sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
            from maafw_capture import MaafwCapture      # noqa: PLC0415
            _FAST["cap"] = MaafwCapture.open()
        im = _FAST["cap"].grab()
        if im is not None:
            _FAST["src"], _FAST["err"] = "FramePool", ""
            return im, ""
        _FAST["err"] = "FramePool 抓到空帧"
    except Exception as e:                              # noqa: BLE001
        _FAST["err"] = "FramePool 不可用：" + str(e)[:60]
        _FAST["cap"] = None
    # ② 兜底：adb raw（330ms/帧、1080p）
    try:
        im = BL.shot_fast()          # 0.33s/帧（png 那条要 1.1s）；黑帧重试与安全通道都不变
    except Exception as e:                      # noqa: BLE001
        return None, f"截图异常：{e}"
    return (im, "") if im is not None else (None, "截图失败（黑帧重试也没过）")


# ── 视角跟踪：格网跟着镜头走（用户："我正在往后推，地图也在变化，标注并不会变"） ──
# 关卡投影算出的格心是**某个固定取景**下的坐标；玩家平移/缩放镜头后就不对了。
# 做法：锚一帧"基准视角"（默认第一帧；也可用页面按钮重锚），之后每帧用 ORB+RANSAC
# 求 (基准 → 当前) 的单应，把格心搬过去。配准失败就沿用上一次的变换并**明说**，不装准。
_REF: dict = {"gray": None, "H": None, "info": "", "inl": 0}
ROI = (130, 820, 300, 1780)


def set_reference(a):
    """锚基准：格网 = 关卡投影原样；同时记住这一帧，之后靠**逐帧小步**累积跟踪。"""
    _REF.update(gray=cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), prev=None, H=np.eye(3),
                info="基准=当前帧（格网为关卡投影原样）", inl=0)


def _plausible(H):
    """合理性闸：一次镜头变化不会太离谱（fail-closed —— 不像镜头动的就别采纳）。"""
    try:
        J = H[:2, :2] - np.outer(H[2, :2], H[:2, 2]) / H[2, 2]
    except Exception:                                   # noqa: BLE001
        return False, "矩阵退化"
    sc = float(np.sqrt(abs(np.linalg.det(J))))
    rot = abs(float(np.degrees(np.arctan2(J[1, 0], J[0, 0]))))
    tr = float(np.hypot(H[0, 2], H[1, 2]))
    if not (0.80 <= sc <= 1.30):
        return False, f"缩放 ×{sc:.2f} 不像镜头动"
    if rot > 15:
        return False, f"旋转 {rot:.1f}° 不像镜头动"
    if tr > 320:
        return False, f"平移 {tr:.0f}px 太大"
    return True, f"×{sc:.3f} 转{rot:+.1f}° 移({H[0, 2]:+.0f},{H[1, 2]:+.0f}) 内点{int(_REF.get('inl', 0))}"


def _flow_step(prev_g: np.ndarray, g: np.ndarray):
    """相邻两帧的单应一步：**光流**（在预测点附近局部搜索）+ 位移上限。

    为什么不用 ORB 全局匹配：地图是重复铺装（周期 ~90px），全局匹配会锁到
    "差一个周期、错误但自洽"的那一族上（合成测试里偏 48px ✗ 且逐帧累积也一样偏）。
    光流是局部搜索 ⇒ 天然不歧义；再把单点位移压到 < 60px（小于一个纹理周期）双保险。
    """
    pts = cv2.goodFeaturesToTrack(prev_g, 1500, 0.01, 12)
    if pts is None or len(pts) < 40:
        return None, "光流：角点太少"
    nxt, st, _err = cv2.calcOpticalFlowPyrLK(prev_g, g, pts, None, winSize=(21, 21), maxLevel=3)
    if nxt is None:
        return None, "光流：跟踪失败"
    d = np.abs(nxt - pts).max(axis=2).ravel()
    ok = (st.ravel() == 1) & (d < 60)
    if int(ok.sum()) < 40:
        return None, f"光流：可用点只有 {int(ok.sum())}"
    a1 = pts[ok].reshape(-1, 1, 2)
    a2 = nxt[ok].reshape(-1, 1, 2)
    H, mask = cv2.findHomography(a1, a2, cv2.RANSAC, 2.0)
    inl = int(mask.sum()) if mask is not None else 0
    if H is None or inl < 30:
        return None, f"光流：内点只有 {inl}"
    _REF["inl"] = inl
    return H, f"光流内点 {inl}/{int(ok.sum())}"


def view_transform(a):
    """(当前 → 基准) 的单应 —— **光流逐帧累积**（见 _flow_step 的说明）。"""
    g = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    if _REF["gray"] is None:
        set_reference(a)
        return np.eye(3)
    prev = _REF["prev"] if _REF["prev"] is not None else _REF["gray"]
    step, info = _flow_step(prev, g)              # 上一帧 → 本帧
    if step is None:
        _REF["info"] = info + " ⇒ 沿用上次变换"
        return None
    ok, why = _plausible(step)
    if not ok:
        _REF["info"] = f"这一步被闸挡下（{why}）⇒ 沿用上次变换"
        return None
    H = _REF["H"] @ np.linalg.inv(step)           # 累积：本帧 →（inv step）上一帧 → 基准
    _REF.update(H=H, prev=g, info=f"(当前→基准) {why} 累积 内点{_REF['inl']}")
    return H


_LOOP = {}


def _loop():
    """复用 Loop（建一次 ~0.1s；每帧重建纯浪费）。"""
    if "L" not in _LOOP:
        _LOOP["L"] = BL.Loop(ARGS.stage, None)
    return _LOOP["L"]



# ── ③ 自动体检：进入战斗 + 手牌被选中（= 有高亮）+ 本关没审过 ⇒ 跑一次并登记 ──
_AUDITED: set[str] = set()


def maybe_audit(stage: str, frame, tr) -> str:
    """有高亮就体检一次（幂等 ✓）。返回说明文字（空 = 没做）。

    触发时机（外援 §触发点 ✓）：**选牌时游戏会把可放置格高亮** ⇒ 那正是"有高亮"的时刻；
    我们靠"任务面板的蓝 HP 条在不在"判它（和 ops 状态闸同一个信号 ✓）。
    */
    """
    if not stage or stage in _AUDITED:
        return ""
    try:
        import ui_layout as _UL
        import tilepos_audit as _TA
        nodes = _UL.nodes_in_frame(tr)
        hp = (nodes.get("ops.hp") or {}).get("container")
        if not hp:
            return ""
        x0, y0, x1, y1 = hp
        reg = frame[max(0, y0):y1, max(0, x0):x1].astype(int)
        if not reg.size:
            return ""
        blue = (reg[:, :, 0] - reg[:, :, 2] > 40) & (reg[:, :, 0] > 120)
        if float(blue.mean()) < 0.02:
            return ""                      # 没选牌 ⇒ 没高亮 ⇒ 不审（UNKNOWN ✓）
        out = pathlib.Path(f"data/_audit_{stage}.png")
        cv2.imwrite(str(out), frame)
        r = _TA.audit_stage(stage, [str(out)], frame_size=(frame.shape[0], frame.shape[1]))
        wrote = _TA.record(stage, r)
        _AUDITED.add(stage)
        return (f"体检 {stage} = {r['status']}" + ("（已登记 ✓）" if wrote else "（同版本已登记过，跳过 ✓）")
                + f"  角度 {r['observation']['axis_angle_deg']}")
    except Exception as e:                                   # noqa: BLE001
        _AUDITED.add(stage)
        return f"体检异常：{str(e)[:60]}"

def snapshot(ttl: float = 0.9):
    """抓帧 + 量测（TTL 内复用）。**抓帧是最贵的一步**，所以两端点（图 / json）共用这一份缓存。

    ⚠️ 早先每个请求各抓一次 ⇒ 页面每轮同时要图和 json、两次抓帧互相排队 ⇒ 图永远加载不完。
    顺序要紧（2026-09-19 踩过：把某段插错位置 ⇒ `a`/`cards`/`_val` 未赋值就崩 ✗）：
        抓帧 → 认关（格网/敌人按关算 ⇒ 先认 ✓，认不出就不画 ✓）→ 格网 → UI(layout+锚点+仿射)
        → 手牌（动态 N）→ 精修 + 几何→图标校验 → 战斗闸 → 读数 → 写缓存
    """
    _val = None                      # 手牌几何校验通过率（可能提前 return ⇒ 先初始化 ✓）
    with _lock:
        if time.time() - _cache["t"] < ttl and _cache["frame"] is not None:
            return True, _cache["err"]
        _t0 = time.time()
        a, err = grab()
        if a is None:
            _cache.update(t=time.time(), frame=None, err=err, png={})
            return False, err
        # ① 认关：UI 布局跨关通用 ✓，但**格网/敌人是按某一关算的** ⇒ 认不出就不画（fail-closed ✓）
        _st, _err, _cands = AL.detect_stage(a)
        if _st and _st != ARGS.stage:
            ARGS.stage = _st
            _LOOP.pop("L", None)
        _cache["stage"] = _st or ("未知（最接近 %s 误差 %.0f，注册表 %s）"
                                  % (min(_cands, key=_cands.get) if _cands else "-",
                                     _err if _err is not None else -1, list(_cands) or "空"))
        L = _loop()
        # ② 格网（只在该关成立 ⇒ 认不出关就不画 ✓）
        grid = AL.measure_grid(a, L) if _st else []
        if not ARGS.ref:                      # 逐帧跟踪镜头（--ref 给了固定参照就不跟）
            H = view_transform(a)
            if H is not None and not np.allclose(H, np.eye(3)):
                grid = AL.transform_cells(grid, np.linalg.inv(H))
        elif ARGS.img:
            H = AL.estimate_view(ARGS.img, ARGS.ref)
            if H is not None:
                grid = AL.transform_cells(grid, np.linalg.inv(H))
        # ③ UI：静态表 + 锚点 + 仿射；解不出就整套不画（fail-closed ✓）
        ui, tr, ui_why, _ui_content = AL.layout_boxes(a)
        _cache["ui_why"] = "" if ui else ui_why
        ui = ui or {}
        # ③ 自动体检：有高亮（= 选牌中）且本关没审过 ⇒ 跑一次并登记 ✓
        _audit_note = maybe_audit(_st, a, tr) if _st else ""
        if _audit_note:
            _cache["audit_note"] = _audit_note
        # ④ 手牌（**动态 N**：右锚 + 按时数取间距；图标只计数/校验 ✓）
        fresh = _cache.get("cards") and (time.time() - _cache.get("cards_t", 0) < 10.0)
        cards = _cache["cards"] if fresh else AL.measure_cards(a, L, tr)
        _cache["cards_t"] = _cache.get("cards_t", 0) if fresh else time.time()
        # ⑤ 边缘精修 + 几何→图标校验（与命令行工具同一套 ✓）
        if ui:
            import ui_layout as _UL                                   # noqa: PLC0415
            _gray = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
            for _k, _b in list(ui.items()):
                ui[_k] = _UL.refine_box(_gray, _b)[0]
            if cards:
                _sc = a.shape[1] / _UL.LW
                _g1280 = cv2.resize(a, (_UL.LW, _UL.LH), interpolation=cv2.INTER_AREA)
                _b1280 = [(round(c["box"][0] / _sc), round(c["box"][1] / _sc),
                           round(c["box"][2] / _sc), round(c["box"][3] / _sc)) for c in cards]
                _val = round(_UL.validate_cards(L, _g1280, _b1280)[0] * 100)
        # ⑥ 战斗闸：手牌行只在战斗界面出现 ⇒ 认不到牌就不画那些框
        battle = len(cards) >= 4
        why = "" if battle else f"这一帧不像战斗画面（手牌只认出 {len(cards)} 张）—— 战斗 HUD 的框不画"
        vals = AL.read_values(a, L, ui, cards, upscale=2.0) if (battle and ui) else {}
        _cache.update(t=time.time(), frame=a, err=err, png={}, battle=battle, why=why, vals=vals,
                      took=round(time.time() - _t0, 2), ui=ui, cards=cards,
                      grid=grid, fo=AL.measure_fieldops(a), cards_val=_val)
        return True, err


def render_layers(layers: tuple[str, ...], panel: bool):
    """用缓存里的量测渲染（不抓帧，快）。"""
    key = (tuple(layers), bool(panel))
    with _lock:
        if key in _cache["png"]:
            return _cache["png"][key]
        ok, err = snapshot()
        if not ok:
            return None
        lay = layers if _cache.get("battle") else ()
        canvas = AL.render(_cache["frame"], _cache["ui"], _cache["cards"], _cache["grid"],
                           _cache["fo"], layers=lay, panel=panel)
        ok, buf = cv2.imencode(".png", canvas)
        png = buf.tobytes() if ok else None
        _cache["png"][key] = png
        return png


def measurement() -> dict | None:
    ok, err = snapshot()
    if not ok:
        return {"frame": {}, "ui": {}, "fieldops": {}, "cards": [], "grid": {},
                "err": err, "battle": False, "why": err}
    a = _cache["frame"]
    return {
        "frame": {"w": int(a.shape[1]), "h": int(a.shape[0]),
                  "mean": round(float(a.mean()), 1), "at": time.strftime("%H:%M:%S")},
        "ui": dict(_cache["ui"]),
        "fieldops": dict(_cache["fo"]),
        "cards": [{"n": c["n"], "role": c["role"], "icon_x": c["icon_x"], "box": c["box"]}
                  for c in _cache["cards"]],
        "grid": {"total": len(_cache["grid"]),
                 "gnd": sum(1 for g in _cache["grid"] if g["kind"] == "gnd"),
                 "high": sum(1 for g in _cache["grid"] if g["kind"] == "high"),
                 "blk": sum(1 for g in _cache["grid"] if g["kind"] == "blk")},
        "err": _cache["err"],
        "battle": bool(_cache.get("battle")),
        "why": _cache.get("why", ""),
        "ui_why": _cache.get("ui_why", ""),
        "cards_val": _cache.get("cards_val"),
        "stage": _cache.get("stage", ""),
        "view": {"info": _REF["info"], "inliers": _REF["inl"]},
        "source": {"kind": _FAST["src"], "err": _FAST["err"]},
        "values": _cache.get("vals", {}),
        "perf": {"frame_cost": _cache.get("took", 0),
                 "refresh_s": 1.2, "fps_cap": round(1.0 / max(1.2, 1.2), 2)},
    }


PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><link rel="icon" href="data:,">
<title>战斗画面标注 · 实时</title>
<style>
 :root{--bg:#14161a;--fg:#e8e8ea;--dim:#8b93a1;--line:#2a2f38;--ui:#ffa500;--card:#00a2ff;--ops:#00dcff;--gnd:#00dc00;}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 "Segoe UI",system-ui,"Microsoft YaHei",sans-serif}
 header{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;gap:18px;align-items:center;flex-wrap:wrap}
 h1{font-size:16px;margin:0;font-weight:600}
 .bar{display:flex;gap:14px;align-items:center;flex-wrap:wrap;color:var(--dim)}
 label{display:flex;gap:6px;align-items:center;cursor:pointer;user-select:none}
 label.c-ui{color:var(--ui)} label.c-card{color:var(--card)} label.c-ops{color:var(--ops)} label.c-gnd{color:var(--gnd)}
 button{background:#222833;color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 12px;cursor:pointer}
 button:hover{background:#2c3442}
 main{padding:14px 18px;display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:16px}
 @media(max-width:1100px){main{grid-template-columns:1fr}}
 .shot{background:#0c0e12;border:1px solid var(--line);border-radius:8px;padding:8px;position:relative}
 .shot img{width:100%;display:block;border-radius:4px}
 .stat{position:absolute;left:16px;bottom:16px;background:#000a;padding:4px 10px;border-radius:6px;color:var(--dim);font-size:12px}
 .warn{color:#ff6b6b}
 .banner{background:#4a1d1d;border:1px solid #7a2b2b;color:#ffd9d9;padding:8px 12px;border-radius:8px;margin-bottom:10px;font-size:13px}
 table{width:100%;border-collapse:collapse;font-size:12.5px}
 th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
 th{color:var(--dim);font-weight:500}
 code{color:#b9c6d6;font-family:Consolas,monospace;font-size:12px}
 .sec{margin:0 0 14px;background:#191d24;border:1px solid var(--line);border-radius:8px;padding:10px 12px}
 .sec h2{font-size:13px;margin:2px 0 8px;color:var(--dim);font-weight:600;letter-spacing:.3px}
 .k{color:var(--dim)}
</style></head><body>
<header>
  <h1>战斗画面标注 · 实时</h1>
  <div class="bar">
    图层
    <label class="c-ui"><input type="checkbox" id="l-ui" checked>战斗 HUD</label>
    <label class="c-card"><input type="checkbox" id="l-cards" checked>手牌</label>
    <label class="c-gnd"><input type="checkbox" id="l-grid" checked>地图格</label>
    <label class="c-ops"><input type="checkbox" id="l-ops" checked>战场干员</label>
    <label><input type="checkbox" id="l-panel">图例栏(在图上)</label>
  </div>
  <div class="bar">
    <label><input type="checkbox" id="auto" checked>自动刷新</label>
    <button id="now">立即刷新</button>
    <button id="anchor" title="❗在镜头处于默认视角时按（例如刚进战斗）：格网 = 关卡投影原样；之后你推图/缩放，格网会靠光流逐帧跟着搬">① 镜头归位后点这里锚基准</button>
    <span id="clock" class="k"></span>
    <span id="view" class="k"></span>
    <span id="perf" class="k"></span>
  </div>
</header>
<main>
  <div>
    <div id="banner" class="banner" style="display:none"></div>
    <div class="shot"><img id="img" alt="">
      <div class="stat" id="stat"></div>
    </div>
  </div>
  <aside>
    <div class="sec"><h2>识别读数（OCR：字符模型 + 抽令牌）</h2><table id="t-vals"></table></div>
    <div class="sec"><h2>这一帧</h2><table id="t-frame"></table></div>
    <div class="sec"><h2>UI 元素（量出来的外框）</h2><table id="t-ui"></table></div>
    <div class="sec"><h2>战场干员 UI</h2><table id="t-ops"></table></div>
    <div class="sec"><h2>手牌</h2><table id="t-cards"></table></div>
    <div class="sec"><h2>地图格</h2><table id="t-grid"></table></div>
  </aside>
</main>
<script>
const $ = id => document.getElementById(id);
function layers(){ return ["ui","cards","grid","ops"].filter(k => $("l-"+k).checked).join(","); }
let n = 0, timer = null;
function tick(){
  const t = Date.now();
  const url = "/frame.png?layers=" + layers() + "&panel=" + ($("l-panel").checked?1:0) + "&v=" + t;
  $("img").src = url;
  fetch("/measure.json?v=" + t).then(r => r.json()).then(show).catch(()=>{});
  $("clock").textContent = "刷新 #" + (++n) + " · " + new Date(t).toLocaleTimeString();
  fetch("/measure.json?view=1&v=" + t).then(r => r.json()).then(m => {
    const v = (m && m.view) || {};
    $("view").textContent = v.info ? ("视角 " + v.info + (v.inliers < 20 ? " ⚠️" : "")) : "";
  }).catch(()=>{});
}
function tbl(el, rows){
  el.innerHTML = rows.map(([a,b]) => `<tr><td class="k">${a}</td><td><code>${b}</code></td></tr>`).join("");
}
function show(m){
  if(!m) return;
  const b = $("banner");
  const uiw = m.ui_why || "";
  if(m.battle === false || uiw){
    b.style.display = "block";
    b.textContent = "⚠️ " + [ (m.battle === false ? (m.why || "这一帧不像战斗画面") : ""), uiw ]
                        .filter(Boolean).join(" ｜ ");
  } else { b.style.display = "none"; }
  // 不是战斗画面时把右侧表格压暗（那些框是"量到了"但不是战斗 HUD，别被读数骗了）
  const pf = m.perf || {};
  const S = (m.source || {});
  $("perf").textContent = (pf.frame_cost ? ("抓帧 " + pf.frame_cost + "s") : "")
      + (S.kind ? "  ·  帧源 " + S.kind + (S.err ? "（" + S.err + "）" : "") : "");
  document.querySelector("aside").style.opacity = (m.battle === false) ? 0.4 : 1;
  document.querySelector("aside").title = (m.battle === false) ? "未识别为战斗画面，以下数值不可信" : "";
  const f = m.frame || {};
  $("stat").innerHTML = `${f.w}×${f.h} · 均值 ${f.mean} · ${f.at}` + (m.err ? ` <span class="warn">${m.err}</span>` : "");
  tbl($("t-frame"), [["分辨率", f.w+"×"+f.h], ["画面均值(压暗程度)", f.mean], ["截图时刻", f.at]]);
  const V = m.values || {};
  tbl($("t-vals"), [["敌人数 x/y", V["敌人数"] ? V["敌人数"].join(" / ") : "读不到"],
                    ["我方生命", V["我方生命"] ?? "读不到"],
                    ["费用", V["费用"] ?? "读不到"],
                    ["还可部署", V["可部署"] ?? "读不到"],
                    ["手牌费用", (V["手牌费用"]||[]).map(x => x===null?"?":x).join(" ") || "读不到"]]);
  tbl($("t-ui"), Object.entries(m.ui).map(([k,v]) => [k, v ? "("+v.join(", ")+")" : "量不到(None)"]));
  tbl($("t-ops"), Object.entries(m.fieldops).map(([k,v]) => [k, v ? "("+v.join(", ")+")" : "量不到(None)"]));
  tbl($("t-cards"), m.cards.map(c => ["#"+c.n+" "+c.role, "icon_x="+c.icon_x+" x "+c.box[0]+".."+c.box[2]]));
  const g = m.grid;
  tbl($("t-grid"), [["总格数", g.total], ["地面可放(绿点)", g.gnd], ["高台可放(青点)", g.high], ["不可放(白点)", g.blk]]);
}
function setupAuto(){ if(timer){clearInterval(timer);timer=null;} if($("auto").checked) timer=setInterval(tick, 1200);   /* 取帧变快后（0.33s）刷新提到 1.2s */; }
["l-ui","l-cards","l-grid","l-ops","l-panel"].forEach(k => $(k).addEventListener("change", tick));
$("auto").addEventListener("change", () => { setupAuto(); tick(); });
$("now").addEventListener("click", tick);
$("anchor").addEventListener("click", () => {
  fetch("/anchor").then(r => r.json()).then(j => { $("view").textContent = "视角 " + (j.view||""); tick(); })
                  .catch(()=>{});
});
tick(); setupAuto();
</script></body></html>
"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):                  # 安静点
        pass

    def _send(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                            # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            return self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
        if u.path == "/frame.png":
            layers = tuple(x for x in (q.get("layers", [",".join(AL.LAYERS)])[0]).split(",")
                           if x in AL.LAYERS) or AL.LAYERS
            panel = q.get("panel", ["1"])[0] == "1"
            png = render_layers(layers, panel)
            if png is None:
                return self._send(503, "text/plain; charset=utf-8",
                                  ("截图失败：" + _cache["err"]).encode("utf-8"))
            return self._send(200, "image/png", png)
        if u.path == "/anchor":               # 把当前帧锚成基准（页面按钮）
            ok, err = snapshot(ttl=0.0)
            if ok:
                set_reference(_cache["frame"])
                _cache["png"] = {}
                return self._send(200, "application/json; charset=utf-8",
                                  json.dumps({"ok": True, "view": _REF["info"]},
                                             ensure_ascii=False).encode("utf-8"))
            return self._send(503, "text/plain; charset=utf-8", err.encode("utf-8"))
        if u.path == "/measure.json":
            m = measurement()
            return self._send(200, "application/json; charset=utf-8",
                              json.dumps(m, ensure_ascii=False).encode("utf-8"))
        self._send(404, "text/plain; charset=utf-8", b"404")


def main() -> None:
    global ARGS
    ap = argparse.ArgumentParser(description="战斗画面标注 · 可视化界面")
    ap.add_argument("--port", type=int, default=8930)
    ap.add_argument("--stage", default="act51side_01")
    ap.add_argument("--img", default="", help="用固定图（不连模拟器）")
    ap.add_argument("--ref", default="", help="参照帧（普通视角），给出则把格心搬到当前视角")
    ARGS = ap.parse_args()
    print(f"→ http://127.0.0.1:{ARGS.port}/   （来源：{'固定图 ' + ARGS.img if ARGS.img else '模拟器实时截帧'}）")
    ThreadingHTTPServer(("127.0.0.1", ARGS.port), H).serve_forever()


if __name__ == "__main__":
    main()
