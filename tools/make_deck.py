"""生成《明日方舟 · 肉鸽战斗画面标注》交接 PPT。

只用 python-pptx，图片全部用我们这次的真实产物（不拉伸、按原比例缩放 ✓）。
"""
from __future__ import annotations

import pathlib

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt

ROOT = pathlib.Path(__file__).resolve().parents[1]      # 仓库根（data/ 在根上）
DATA = ROOT / "data"
OUT = ROOT / "roguelike_annotation_deck.pptx"

W, H, M = 13.333, 7.5, 0.6
FONT = "微软雅黑"

DARK = RGBColor(0x12, 0x16, 0x1C)
BG = RGBColor(0xF4, 0xF6, 0xF8)
CARD = RGBColor(0xE8, 0xEC, 0xF0)
PRIMARY = RGBColor(0x36, 0x45, 0x55)
ACCENT = RGBColor(0xE0, 0x7B, 0x1E)
TEXT = RGBColor(0x1B, 0x20, 0x27)
MUTED = RGBColor(0x6B, 0x76, 0x84)
OK = RGBColor(0x2E, 0x7D, 0x32)
BAD = RGBColor(0xB0, 0x30, 0x2A)


def tb(slide, x, y, w, h, runs, size=18, color=TEXT, bold=False, align=PP_ALIGN.LEFT,
       line=1.25, space_after=6):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, item in enumerate(runs if isinstance(runs, list) else [runs]):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line
        p.space_after = Pt(space_after)
        if isinstance(item, str):
            text, opt = item, {}
        elif isinstance(item, dict):
            text, opt = item.get("text", ""), item
        else:
            text, opt = item
        r = p.add_run()
        r.text = text
        r.font.name = FONT
        r.font.size = Pt(opt.get("size", size))
        r.font.bold = opt.get("bold", bold)
        r.font.color.rgb = opt.get("color", color)
    return box


def bg(slide, color):
    sh = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(W), Inches(H))
    sh.fill.solid()
    sh.fill.fore_color.rgb = color
    sh.line.fill.background()
    sh.shadow.inherit = False
    return sh


def head(slide, kicker, title, sub=""):
    tb(slide, M, 0.42, W - 2 * M, 0.36, [{"text": kicker, "size": 13, "color": ACCENT, "bold": True}])
    tb(slide, M, 0.78, W - 2 * M, 0.7, [{"text": title, "size": 32, "bold": True}])
    if sub:
        tb(slide, M, 1.52, W - 2 * M, 0.4, [{"text": sub, "size": 15, "color": MUTED}])


def pic(slide, name, x, y, w=None, h=None):
    p = DATA / name
    iw, ih = Image.open(p).size
    if w and not h:
        h = w * ih / iw
    elif h and not w:
        w = h * iw / ih
    elif w and h:
        if w / h > iw / ih:
            w = h * iw / ih
        else:
            h = w * ih / iw
    slide.shapes.add_picture(str(p), Inches(x), Inches(y), Inches(w), Inches(h))
    return w, h


def card(slide, x, y, w, h, fill=CARD):
    sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    sh.adjustments[0] = 0.06
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    sh.line.fill.background()
    sh.shadow.inherit = False
    return sh


def chip(slide, x, y, text, color=OK, w=1.5):
    sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(0.34))
    sh.adjustments[0] = 0.3
    sh.fill.solid()
    sh.fill.fore_color.rgb = color
    sh.line.fill.background()
    sh.shadow.inherit = False
    tf = sh.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = text
    r.font.name = FONT
    r.font.size = Pt(12)
    r.font.bold = True
    r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)


prs = Presentation()
prs.slide_width = Inches(W)
prs.slide_height = Inches(H)
blank = prs.slide_layouts[6]

# ── 1 封面 ───────────────────────────────────────────────────────────
s = prs.slides.add_slide(blank)
bg(s, DARK)
tb(s, M, 2.05, 9.5, 0.4, [{"text": "明日方舟 · 肉鸽（统合战略）战斗画面标注", "size": 14, "color": ACCENT, "bold": True}])
tb(s, M, 2.5, 11.6, 1.6, [{"text": "能力现状 · 证据 · 交接", "size": 54, "bold": True,
                           "color": RGBColor(0xFF, 0xFF, 0xFF)}])
tb(s, M, 4.2, 10.8, 1.2, [
    {"text": "把画面变成可复用数据：只读采样 → 测量式标注 → 格网 / 防守点 / 路径", "size": 21,
     "color": RGBColor(0xC9, 0xD1, 0xD9)},
    {"text": "2026-09-19 · 全部结论都带可复现的命令与产物路径", "size": 14, "color": MUTED},
])

# ── 1b 验收标准（本 deck 自身是样板：标准就是“能在截图上指出什么”）─────
s = prs.slides.add_slide(blank)
bg(s, BG)
head(s, "00 · 验 收 标 准", "标准不是“掌握了哪些数据”，而是“能在截图上指出什么”",
     "本 deck 里的每一页成品，都按这条标准验收")
card(s, M, 2.2, 6.0, 3.55)
tb(s, M + 0.3, 2.42, 5.4, 0.4, [{"text": "能指出来（才算做到）", "size": 19, "bold": True, "color": OK}])
for i, d in enumerate(["UI / 手牌框（贴元素、可复现）",
                       "格心点 + 格心之间的连线（按可部署性上色）",
                       "防守点：数据 tile_end 落在画面正确位置（已与画面蓝 ⚠ 块核对）",
                       "tile_road 格：平铺标出，人一眼看出哪些格属于路径"]):
    tb(s, M + 0.3, 2.92 + i * 0.72, 5.5, 0.66, [{"text": d, "size": 13, "color": TEXT, "line": 1.3}])
card(s, 6.9, 2.2, 5.85, 3.55, fill=RGBColor(0xF0, 0xE4, 0xE4))
tb(s, 7.2, 2.42, 5.3, 0.4, [{"text": "还不能声称（说了就是过头）", "size": 19, "bold": True, "color": BAD}])
for i, d in enumerate(["这些格已恢复真实路径顺序 / 可靠距离",
                       "已推出敌人行进时序",
                       "纯视觉防守点识别已达标（8 张里 5 张 20~70px，仍有挑错/漏检）",
                       "格网已贴合格心（官方投影约有 10% 缩放偏差）"]):
    tb(s, 7.2, 2.92 + i * 0.72, 5.35, 0.66, [{"text": d, "size": 13, "color": TEXT, "line": 1.3}])
tb(s, M, 6.0, 12.2, 0.8, [{"text": "一句话能力边界：能把肉鸽战斗画面转换成可回放、可核对的视觉标注（UI/手牌、格心与连线、防守点、tile_road 格），"
                                 "并与官方 Tile-Pos 数据及采样帧对应；但部分几何仍有约 10% 缩放偏差，路径顺序/时序与纯视觉防守点识别尚未达到可下结论的标准。",
                           "size": 13.5, "color": PRIMARY, "line": 1.35}])

# ── 2 目标 ───────────────────────────────────────────────────────────
s = prs.slides.add_slide(blank)
bg(s, BG)
head(s, "01 · 目 标", "为什么要做标注", "AI 打关的瓶颈不在“能不能点”，而在“看不看得懂画面”")
steps = [("采样", "只读抓帧\n不干扰 MAA"),
         ("标注", "框贴元素\n值可复现"),
         ("几何", "格网 / 可部署\n防守点 / 路径"),
         ("决策", "打关策略\n可被验证")]
for i, (t, d) in enumerate(steps):
    x = M + i * 3.02
    card(s, x, 2.35, 2.78, 2.6)
    tb(s, x + 0.28, 2.62, 2.2, 0.5, [{"text": f"0{i+1}", "size": 26, "bold": True, "color": ACCENT}])
    tb(s, x + 0.28, 3.22, 2.2, 0.45, [{"text": t, "size": 21, "bold": True}])
    tb(s, x + 0.28, 3.75, 2.25, 1.0, [{"text": d, "size": 13, "color": MUTED, "line": 1.35}])
    if i < 3:
        tb(s, x + 2.8, 3.4, 0.25, 0.4, [{"text": "→", "size": 20, "color": MUTED}])
tb(s, M, 5.3, 11.9, 0.9, [
    {"text": "一条原则贯穿全程：结论必须能在图上被指出来，指不出来的一律不写进结论。", "size": 17},
])

# ── 3 能力现状 ───────────────────────────────────────────────────────
s = prs.slides.add_slide(blank)
bg(s, BG)
head(s, "02 · 现 状", "现在能做到什么", "肉鸽图：格心点 + 点间连线（用户认可的样式）、UI/手牌框、防守点")
pic(s, "_rogue_mesh_9.png", M, 2.0, w=8.1)
card(s, 9.0, 2.0, 3.75, 4.6)
rows = [("格网", "格心点 + 点间连线，按可部署性上色", OK),
        ("防守点", "蓝 ⚠ 块 = tile_end（与画面核对 52/65/67 px）", OK),
        ("手牌", "动态数量（实测 4~8 张），只当元数据", OK),
        ("路径格", "tile_road 平铺标出；距离不下结论", ACCENT)]
y = 2.3
for t, d, c in rows:
    tb(s, 9.3, y, 3.2, 0.34, [{"text": t, "size": 16, "bold": True, "color": c}])
    tb(s, 9.3, y + 0.36, 3.15, 0.6, [{"text": d, "size": 12, "color": MUTED, "line": 1.3}])
    y += 1.15
tb(s, M, 6.75, 12, 0.35, [{"text": "证据图：data/_rogue_mesh_9.png（巡逻队）", "size": 11, "color": MUTED}])

# ── 4 数据链 ─────────────────────────────────────────────────────────
s = prs.slides.add_slide(blank)
bg(s, BG)
head(s, "03 · 数 据 链", "从一帧画面到一个结论", "每一环都有产物落盘、可回放")
chain = [("采集", "tools/battle_sampler.py", "MAA 刷肉鸽时并行\n只读、不发输入"),
         ("判定", "敌人数 x/y 读数", "唯一可靠的战斗判据\n（前两版判据已推翻）"),
         ("标注", "tools/annotate_live.py", "静态表 + 锚点 + 仿射\n解不出就一个框都不画"),
         ("几何", "Arknights-Tile-Pos", "官方格点表 + 路径/终点\n现约 10% 缩放偏差"),
         ("产物", "data/samples/rogue/", "帧 + index.jsonl\n+ _status.json")]
for i, (t, tool, d) in enumerate(chain):
    x = M + i * 2.46
    card(s, x, 2.15, 2.24, 3.5)
    tb(s, x + 0.22, 2.4, 1.9, 0.35, [{"text": t, "size": 19, "bold": True}])
    tb(s, x + 0.22, 2.86, 1.9, 0.7, [{"text": tool, "size": 11, "color": ACCENT, "line": 1.25}])
    tb(s, x + 0.22, 3.66, 1.95, 1.7, [{"text": d, "size": 11.5, "color": MUTED, "line": 1.35}])
    if i < 4:
        tb(s, x + 2.24, 3.6, 0.22, 0.4, [{"text": "→", "size": 18, "color": MUTED}])
tb(s, M, 6.0, 12, 0.6, [
    {"text": "护栏：pit_check.py 共 16 条断言全 PASS（含“采样器只读”“地图只画格心连线不许画方块”），"
             "每条都故意改坏验证过能拦住。", "size": 15}])

# ── 5 已核实的事实 ───────────────────────────────────────────────────
s = prs.slides.add_slide(blank)
bg(s, BG)
head(s, "04 · 已 核 实", "这些数字可以直接引用", "每一条都能在下面的命令/产物里复现")
facts = [("471 帧", "已采样肉鸽战斗帧，其中 140 帧带手牌（手牌 4~8 张/局）"),
         ("133 vs 147 px", "画面实测格距 vs 官方投影 ⇒ 缩放偏差约 10%"),
         ("52 / 65 / 67 px", "防守点：画面蓝块 ↔ 数据 tile_end 的距离（三张图）"),
         ("13~28 格", "每张图的路径格（tile_road）数量"),
         ("16 / 16", "pit_check 断言通过率（含两条已验红）")]
y = 2.15
for big, d in facts:
    card(s, M, y, 12.1, 0.85)
    tb(s, M + 0.3, y + 0.14, 3.4, 0.6, [{"text": big, "size": 26, "bold": True, "color": ACCENT}])
    tb(s, M + 3.9, y + 0.26, 8.0, 0.5, [{"text": d, "size": 15}])
    y += 1.0
tb(s, M, 7.12, 12, 0.3, [{"text": "数据源：本机实测 2026-09-19；产物目录 data/samples/rogue/、data/_rogue_mesh_*.png",
                        "size": 11, "color": MUTED}])

# ── 6 判死的三条路 ───────────────────────────────────────────────────
s = prs.slides.add_slide(blank)
bg(s, BG)
head(s, "05 · 否 掉 的 路", "三条“看起来能用”的判据，实测都不成立", "记录在此，避免下一位重复踩")
dead = [("“手牌 ≥4 张”判战斗", "肉鸽战斗帧上恒不成立：60 秒 26 帧一次都没认到；右下角像卡片的是再配置倒计时头像", BAD),
        ("“战斗 HUD 节点”判战斗", "节点是仿射推导出来的 ⇒ 招募页照样判成战斗（MAA 2 分钟打 1 场，我们报了 5 局）", BAD),
        ("“从画面自拟格网”", "FFT 锁到装饰纹理（格距只有真值一半）；角点法位移直方图无稳定峰 ⇒ 两次都 fail-closed 拒画", BAD)]
y = 2.2
for t, d, c in dead:
    card(s, M, y, 12.1, 1.32)
    chip(s, M + 0.28, y + 0.2, "已否", c, w=0.95)
    tb(s, M + 1.45, y + 0.18, 10.2, 0.4, [{"text": t, "size": 19, "bold": True}])
    tb(s, M + 1.45, y + 0.66, 10.4, 0.6, [{"text": d, "size": 13, "color": MUTED, "line": 1.3}])
    y += 1.45
tb(s, M, 6.7, 12.2, 0.4, [{"text": "共同点：判据不是“有道理”，而是“在目标画面上量过”。", "size": 15}])

# ── 7 未验证 / 待办 ─────────────────────────────────────────────────
s = prs.slides.add_slide(blank)
bg(s, BG)
head(s, "06 · 未 验 证 / 待 办", "现在还不能说死的事", "按“可验证性”排序，标注当前状态")
todo = [("肉鸽图 routes 解析", "报 route_missing ⇒ 拿不到真正的出怪路径与时刻", "待修", BAD),
        ("纯图像识别防守点", "8 张里 5 张落在 20~70 px、1 张 136 px；另 1 张挑错、1 张没找到 ⇒ 需更严的 ⚠ 形状校验", "部分可行", ACCENT),
        ("每图一次标定", "画面实测格距 133 px vs 官方 147 px；标定后可让格网贴合格心", "未启动", MUTED),
        ("路径“距离/顺序”", "路径格网络不连通（步数只有 0~1）⇒ 距离结论已撤回", "已撤回", BAD)]
y = 2.15
for t, d, st, c in todo:
    card(s, M, y, 12.1, 1.05)
    tb(s, M + 0.3, y + 0.16, 4.2, 0.45, [{"text": t, "size": 17, "bold": True}])
    tb(s, M + 4.6, y + 0.22, 6.2, 0.6, [{"text": d, "size": 12.5, "color": MUTED, "line": 1.3}])
    chip(s, M + 11.0, y + 0.32, st, c, w=1.0)
    y += 1.15

# ── 8 交接清单 ───────────────────────────────────────────────────────
s = prs.slides.add_slide(blank)
bg(s, BG)
head(s, "07 · 交 接", "拿到机器后先跑这三条", "只读、可回放；都不会干扰正在运行的 MAA")
cmd = [("采样（一直采，磁盘到 4GB 自停）", "python tools/battle_sampler.py"),
       ("看实时状态 / 为什么跳过", "data/samples/rogue/_status.json"),
       ("出一张带格网与防守点的标注图", "python tools/annotate_live.py --img <帧> --stage <图名>"),
       ("回归断言（16 条，含已验红的）", "python tools/pit_check.py"),
       ("本地看图（只读静态服务）", "python -m http.server 8931 --directory data --bind 127.0.0.1")]
y = 2.1
for t, c in cmd:
    card(s, M, y, 12.1, 0.78, fill=RGBColor(0xE2, 0xE7, 0xEC))
    tb(s, M + 0.3, y + 0.1, 5.4, 0.4, [{"text": t, "size": 14, "bold": True}])
    tb(s, M + 5.9, y + 0.12, 6.0, 0.5, [{"text": c, "size": 12.5, "color": PRIMARY}])
    y += 0.9

# ── 9 纪律 ───────────────────────────────────────────────────────────
s = prs.slides.add_slide(blank)
bg(s, DARK)
tb(s, M, 1.5, 11.5, 0.4, [{"text": "08 · 纪 律", "size": 14, "color": ACCENT, "bold": True}])
tb(s, M, 1.95, 11.5, 0.9, [{"text": "这套东西能信，靠的是三条纪律", "size": 34, "bold": True,
                            "color": RGBColor(0xFF, 0xFF, 0xFF)}])
rules = [("fail-closed", "取不到证据就拒绝：解不出仿射不画框、认不到格子不画格网、没有高亮就不判几何"),
         ("断言可跑红", "每条坑都写成可执行断言，并且故意改坏验证过它真能拦住（假通过比没有更危险）"),
         ("结论带边界", "每个数字都写清“在什么帧、什么状态下量出来的”，以及它不覆盖什么")]
y = 3.15
for t, d in rules:
    tb(s, M, y, 2.6, 0.4, [{"text": t, "size": 19, "bold": True, "color": ACCENT}])
    tb(s, M + 2.9, y + 0.03, 8.9, 0.8, [{"text": d, "size": 14, "color": RGBColor(0xC9, 0xD1, 0xD9), "line": 1.35}])
    y += 1.2

# ── 10 收尾 ──────────────────────────────────────────────────────────
s = prs.slides.add_slide(blank)
bg(s, DARK)
tb(s, M, 1.7, 11.5, 0.9, [{"text": "下一步，三选一", "size": 40, "bold": True, "color": RGBColor(0xFF, 0xFF, 0xFF)}])
opts = [("A", "修 routes 解析", "拿到真正的出怪路径与时刻（回答“敌人从哪进、走哪条”）"),
        ("B", "纯图像防守点", "把 ⚠ 形状校验做严，做到不依赖官方数据"),
        ("C", "每图一次标定", "消除 10% 缩放，让格网真正贴合格心")]
y = 2.95
for k, t, d in opts:
    tb(s, M, y, 0.7, 0.5, [{"text": k, "size": 26, "bold": True, "color": ACCENT}])
    tb(s, M + 0.9, y + 0.02, 3.4, 0.5, [{"text": t, "size": 20, "bold": True, "color": RGBColor(0xFF, 0xFF, 0xFF)}])
    tb(s, M + 4.4, y + 0.06, 7.4, 0.6, [{"text": d, "size": 13.5, "color": RGBColor(0xC9, 0xD1, 0xD9)}])
    y += 1.05
tb(s, M, 6.5, 11.5, 0.4, [{"text": "所有产物与命令都在仓库里；本页之后的任何结论，先在图上指给我看。",
                           "size": 13, "color": MUTED}])

prs.save(str(OUT))
print("✓", OUT, OUT.stat().st_size // 1024, "KB,", len(prs.slides.__iter__.__self__._sldIdLst), "张")
