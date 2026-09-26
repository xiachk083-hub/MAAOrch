"""决策层 — 把"看懂的画面"变成"下一步做什么"。

双通道（快/慢）:
  · 快思考（本地，<300ms）: reflex() 里的硬规则 —— 界面状态直接映射到一个
    已知动作。这类情况占绝大多数，且必须快到能进战斗循环（战斗内留给
    "看+想"只有约 0.5s，见 docs/AI_AGENT.md）。
  · 慢思考（LLM）: 规则覆盖不到时，把**结构化画面描述**交给文本模型决策。
    注意不是喂图片 —— 坐标一律由本地 CV 落定（ocr 框/模板命中/格子投影），
    模型只负责"做什么"，不负责"点哪里"。

两条纪律:
  1. **不懂就停**。规则不命中 + LLM 不可用/答不出 → 返回 give_up + 当时的画面
     描述。绝不瞎点 —— 瞎点会把自己的游戏状态点坏，而且没有可复盘的信息。
  2. **动作必须可验证**。每个动作都带 verify（点了之后要看到什么），
     actuator 会照它判定成败并留证；验证不到就重试或升级给人看。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from services.ai_agent.llm import LLM
from services.ai_agent.percept import PROJ, Hit, Perception, log

# ── 动作模型 ───────────────────────────────────────────────────────────
ALLOWED_KINDS = ("tap", "swipe", "back", "wait", "give_up")


@dataclass
class Action:
    kind: str                       # tap | swipe | back | wait | give_up
    target: object | None = None    # 给 Perception.locate 的目标规格
    verify: object | None = None    # 给 Actuator.check 的验证规格
    why: str = ""
    confidence: float = 0.0
    source: str = "reflex"          # reflex | llm | none
    resolved: Hit | None = None     # 本地 grounding 的结果（坐标由它决定）
    extras: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.kind in ALLOWED_KINDS and (self.kind != "tap" or self.target is not None)


# 动作锚点表 = agent 的"UI 地图"。
# 每条 (人类可读名, 定位规格)：规格用 MAA 任务定义（roi+文案/模板）或模板名。
# 这张表就是"教"的落点 —— 遇到不认识的界面时，把新锚点加进来即可，
# 不必改代码逻辑（P2 会把它写成可配置的数据）。
# 教训（2026-09-14 真机）: 手工猜的锚点会把 AI 带沟里 —— 我按字面把任务
# `Fight` 当成"主界面作战按钮"，实际它的模板是 SwitchTheme@ToggleSettingsMenu.png
# （装扮齿轮），LLM 信了这个锚点就点进了首页装扮模式，整屏按钮全不响应。
# 所以: 锚点要么来自**验证过的发现**（tools/ai_agent_discover.py 用 MAA 自己的
# 模板↔元素映射扫出来），要么来自人工实测标定；不要凭名字假设语义。
ANCHORS: list[tuple[str, dict]] = [
    # 关卡详情页 / 战斗（这几个都在离线回归里验证过命中率）
    ("跳过剧情", {"task": "SkipThePreBattlePlot"}),
    ("开始行动", {"task": "StartButton1"}),
    ("全权委托", {"task": "UsePrts"}),
    ("战斗开始确认", {"template": "BattleStartNormal"}),
    ("确认弹窗", {"template": "PopupConfirm", "roi": (630, 400, 650, 160)}),
    ("掉线弹窗", {"template": "OfflineConfirm"}),
    ("战斗中标记", {"template": "BattleOfficiallyBegin"}),
    ("二倍速", {"task": "BattleSpeedUp"}),
    ("暂停", {"task": "BattlePause"}),
    ("肉鸽-开始行动", {"template": "Roguelike@StartAction"}),
    ("肉鸽-快速编队", {"template": "Roguelike@QuickFormation"}),
    ("肉鸽-放弃战斗", {"template": "RoguelikeBattleAbandon"}),
    ("肉鸽-结算掉落", {"template": "Roguelike@DropsFlag"}),
]


ANCHOR_BY_LABEL: dict[str, dict] = {label: spec for label, spec in ANCHORS}

LEARNED_STORE = PROJ / "data" / "agent_anchors.json"


def learned_anchors() -> list[tuple[str, dict]]:
    """已学锚点（tools/ai_agent_discover.py 从 MAA 任务库刷出来的）。

    这是"教"的沉淀：每遇到一个新界面就扫一次、存下来，下次直接可用。
    返回 (标签, 规格) 列表；文件不存在就返回空（不报错）。
    """
    import json as _json
    try:
        d = _json.loads(LEARNED_STORE.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[tuple[str, dict]] = []
    for state, items in d.items():
        for it in items or []:
            spec = it.get("spec")
            if isinstance(spec, dict) and spec:
                out.append((f"{it.get('label', spec.get('task', '?'))}", spec))
    return out


def _describe_target(target) -> str:
    """把目标规格翻译成一句"人话"，交给视觉模型去找。"""
    import json as _json
    if isinstance(target, dict):
        if "vision" in target:
            return str(target["vision"])
        if "ocr" in target:
            return f"写着「{target['ocr']}」的按钮/文字"
        if "task" in target:
            return f"MAA 任务 {target['task']} 对应的按钮"
        if "template" in target:
            return f"界面元素（模板 {target['template']}）"
    if isinstance(target, str):
        return target
    return _json.dumps(target, ensure_ascii=False)


def normalize_target(target):
    """把"锚点名称"或 {"template": "锚点名称"} 归一化成真正的定位规格。

    LLM 很容易把人类可读名当模板名填（实测过），这里兜一层，不然每次都要
    重问一轮 —— 那是纯浪费 token 和游戏时间。
    """
    if isinstance(target, str) and target in ANCHOR_BY_LABEL:
        return ANCHOR_BY_LABEL[target]
    if isinstance(target, dict):
        for key in ("template", "ocr"):
            v = target.get(key)
            if isinstance(v, str) and v in ANCHOR_BY_LABEL:
                spec = dict(ANCHOR_BY_LABEL[v])
                for extra in ("roi", "threshold", "min_score"):
                    if extra in target:
                        spec[extra] = target[extra]
                return spec
    return target


@dataclass
class Observation:
    """一帧的完整感知结果（也是喂给 LLM 的描述来源）。"""

    state: str = ""
    state_detail: str = ""
    texts: list[tuple[str, tuple[int, int]]] = field(default_factory=list)
    anchors: dict[str, dict] = field(default_factory=dict)
    battle: dict = field(default_factory=dict)

    def describe(self) -> str:
        lines = [f"【当前界面】{self.state}（{self.state_detail[:80]}）"]
        if self.texts:
            lines.append("【画面文字】" + " | ".join(
                f"{t}@{c}" for t, c in self.texts[:14]))
        if self.anchors:
            # 必须带上规格（target 要能原样复制）—— 只给名字时模型会把名字当
            # 模板名填进去，本地 grounding 必然失败（2026-09-14 实测）
            import json as _json
            lines.append("【可定位的元素】（点它就把右侧 JSON 原样填进 target）")
            for k, v in self.anchors.items():
                lines.append(f"  - {k} @{v['center']} → target={_json.dumps(v['spec'], ensure_ascii=False)}")
        if self.battle:
            b = self.battle
            parts = []
            if b.get("cost") is not None:
                parts.append(f"费用={b['cost']}")
            if b.get("kills") is not None:
                parts.append(f"击杀={b['kills']}")
            if b.get("operators") is not None:
                parts.append(f"已部署干员={b['operators']}个")
            if b.get("skill_ready"):
                parts.append("技能就绪: " + ", ".join(
                    f"@{c}" for c in b["skill_ready"]))
            if parts:
                lines.append("【战斗信号】" + " | ".join(parts))
        return "\n".join(lines)


def observe(p: Perception, img: np.ndarray | None = None, use_ocr: bool = True,
            battle: bool = False) -> Observation:
    """看一眼：状态 + 文字 + 可定位锚点（+ 战斗信号）。"""
    from services.ai_agent.battle_vision import BattleVision
    img = p.shot() if img is None else img
    obs = Observation()
    st = p.state(img, use_ocr=use_ocr)
    obs.state, obs.state_detail = st["verdict"], st.get("detail", "")
    if use_ocr:
        hits = sorted(p.texts(img), key=lambda h: -h.score)
        obs.texts = [(h.label, h.center) for h in hits[:14]]
    candidates = list(ANCHORS) + [a for a in learned_anchors()
                                  if a[0] not in ANCHOR_BY_LABEL]
    for label, spec in candidates:
        try:
            hit = p.locate(spec, img=img)
        except Exception as e:
            log(f"锚点 {label} 定位异常: {e}")
            hit = None
        if hit:
            obs.anchors[label] = {"center": list(hit.center), "kind": hit.kind,
                                  "score": round(hit.score, 3), "spec": spec}
    # 已经在战斗中就必须读战斗信号 —— 否则 reflex 会基于"空数据"说
    # "暂无技能就绪"，那是假信息（第一次实测就踩到）
    if battle or "战斗中" in obs.state:
        battle = True
    if battle:
        bv = BattleVision(p.paths)
        roi_cost = _task_num(p, img, "BattleCostData")
        roi_kills = _task_num(p, img, "BattleKills")
        obs.battle = {
            "cost": roi_cost,
            "kills": roi_kills,
            "operators": len(bv.operator.detect(img)),
            "skill_ready": [tuple(m["center"]) for m in bv.skill_ready_markers(img)
                            if m.get("ready")],
        }
    return obs


def _task_num(p: Perception, img: np.ndarray, task: str):
    """读一个数字型 ROI（费用/击杀）。读不到返回 None，不猜。"""
    from services.ai_agent.percept import task_roi
    roi = task_roi(task, p.paths.client)
    if not roi:
        return None
    hit = p.ocr.read_region(img, roi, upscale=3, min_score=0.2)
    if not hit:
        return None
    digits = "".join(ch for ch in hit.label if ch.isdigit())
    return digits or None


# ── 视觉落点（多模态模型） ──────────────────────────────────────────────
# 2026-09-14 实测: deepseek-flash 能看图且读得准（读出"终端"/"15-11"/八个按钮/
# 左上齿轮）。这解锁了"未知界面不用手工锚点"—— 文本脑说"我要点 <描述>"，
# 视觉模型把它落到像素，最后由执行器的结果验证。
# 纪律不变: 坐标允许来自视觉（它是看图给的，比文本脑编坐标可靠），但**必须有
# 验证**，且越界/低置信一律拒绝。
_SYS_LOCATE = """你在看明日方舟游戏截图（宽 1280、高 720，左上角为原点）。
用户会告诉你想要点击的界面元素。只输出 JSON，不要多余文字：
{"found": true/false, "x": 整数, "y": 整数, "what": "你在该位置看到的东西", "confidence": 0~1}
x/y 是**截图像素坐标**。找不到、或不确定，就 found=false —— 不要瞎给坐标。"""


def vision_locate(img, what: str, llm: "LLM | None" = None) -> Hit | None:
    """让视觉模型把"文字描述"落成像素坐标。"""
    client = llm or LLM()
    if not client.vision_ready:
        log("视觉落点不可用（未配置 ai_vision_model）")
        return None
    r = client.ask_json(_SYS_LOCATE,
                        f"我想要点击：{what}\n给出它的中心像素坐标。",
                        image=img, vision=True, max_tokens=8000)
    if not r.get("ok"):
        log(f"视觉落点失败: {r.get('error', '')[:120]}")
        return None
    d = r["json"] or {}
    if not d.get("found"):
        log(f"视觉未找到「{what}」（{str(d.get('what'))[:40]}）")
        return None
    try:
        x, y = int(d.get("x")), int(d.get("y"))
    except Exception:
        return None
    if not (0 <= x < 1280 and 0 <= y < 720):
        log(f"视觉坐标越界被拒: {x},{y}")
        return None
    conf = float(d.get("confidence") or 0.5)
    return Hit(kind="vision", label=str(d.get("what") or what)[:40],
               box=(x - 8, y - 8, 16, 16), score=conf)


def _forbidden(act: Action, forbid: tuple) -> bool:
    """目标是否命中禁止关键字 —— 只看**结构化字段**与落点标签。

    不要看自然语言描述: 实测视觉模型写"编队页底部的演习按钮（**不是**开始行动
    按钮）"，描述里带"开始行动"就被禁词误伤了（2026-09-14）。所以只判:
    task / ocr / template 三个结构化字段，以及视觉落点后的 resolved.label。
    """
    if not forbid:
        return False
    parts: list[str] = []
    tgt = act.target
    if isinstance(tgt, dict):
        for k in ("task", "ocr", "template"):
            if tgt.get(k):
                parts.append(str(tgt[k]))
    elif isinstance(tgt, str):
        parts.append(tgt)
    if act.resolved is not None and act.resolved.kind == "vision":
        # 视觉标签常拿邻近元素做位置参照（"开始行动按钮左侧的演习"），
        # 用 `in` 会误伤；只认"标签开头就是禁词"（模型命名目标通常把目标放最前）
        lab = str(act.resolved.label).strip()
        if any(lab == str(f) or lab.startswith(str(f)) for f in forbid):
            return True
    elif act.resolved is not None:
        parts.append(str(act.resolved.label))
    return any(str(f) in p for f in forbid for p in parts)


def _goal_with_forbid(goal: str, forbid: tuple) -> str:
    if not forbid:
        return goal
    return (f"{goal}\n【硬约束】绝对不要点: {', '.join(map(str, forbid))}")


# ── 快思考: 硬规则 ─────────────────────────────────────────────────────
def reflex(obs: Observation, goal: str = "") -> Action | None:
    """已知界面的直接映射。返回 None 表示"这个界面我没被教过"。

    规则刻意保持"少而硬"：只在有确定把握时出手。宁可停下来让人教，
    也不要在不确定的界面上试 —— 试错的代价是游戏状态被点乱。
    """
    anchors = obs.anchors
    st = obs.state
    # 登录失效：立刻停手并把原因说清楚（继续点只会把状态弄更乱）
    if "登录失效" in st:
        return Action("give_up", None,
                      why="登录认证已失效，游戏不接收操作 —— 需要人重新登录"
                          "（常见原因: 同一账号在别处登录把会话顶掉）",
                      confidence=1.0)
    # 弹窗优先（挡住一切）
    if "掉线弹窗" in anchors:
        return Action("tap", {"template": "OfflineConfirm"}, verify={"absent": {"template": "OfflineConfirm"}},
                      why="掉线弹窗，点掉继续", confidence=0.9)
    if "确认弹窗" in anchors and "关卡详情页" not in st:
        return Action("tap", {"template": "PopupConfirm", "roi": (630, 400, 650, 160)},
                      verify=None, why="确认弹窗", confidence=0.6)
    # 剧情（战前剧情会挡住开战）→ 跳过
    if "剧情" in st and "跳过剧情" in anchors:
        return Action("tap", {"task": "SkipThePreBattlePlot"},
                      verify={"absent": {"task": "SkipThePreBattlePlot"}},
                      why="战前剧情，点跳过", confidence=0.85)
    # 战斗中但被暂停 → 先恢复（暂停时无法部署、费用不涨）
    if "已暂停" in st:
        return Action("tap", {"task": "BattlePauseCancel"},
                      verify={"state": "战斗中"}, why="战斗被暂停，点恢复", confidence=0.9)
    # 战斗内（技能就绪就开技能；否则不动，避免乱放干员）
    if "战斗中" in st:
        ready = obs.battle.get("skill_ready") or []
        if ready:
            x, y = ready[0]
            return Action("tap", {"coord": [x, y]}, verify=None,
                          why=f"技能就绪 {ready[0]}，点开技能", confidence=0.75)
        return Action("wait", None, verify=None,
                      why="战斗进行中，暂无技能就绪，等待", confidence=0.5)
    # 关卡详情页 → 开打（这是"自己打"的入口，也是 P1 导航的终点）
    if "关卡详情页" in st and "开始行动" in anchors:
        return Action("tap", {"task": "StartButton1"},
                      verify=None, why="在关卡详情页，点开始行动", confidence=0.85)
    return None


# ── 慢思考: 交给 LLM ───────────────────────────────────────────────────
_SYS = """你在操作明日方舟游戏（一个自动化 agent 的决策模块）。
你只能看到结构化的画面描述，不能看图；坐标不要自己编 —— 你只需从给定候选里挑一个目标。
严格输出 JSON，不要任何额外文字，格式:
{"action":"tap|swipe|back|wait|give_up",
 "target": {"task":"任务名"} 或 {"ocr":"文字"} 或 {"template":"模板名"} 或 {"coord":[x,y]} 或 {"swipe":[x1,y1,x2,y2]} 或 {"vision":"用一句中文描述要点的东西"},
  · 候选里没有合适目标时，可以用 {"vision":"..."} 让视觉模型去找（例如 {"vision":"右下角开始行动按钮"}）,
 "verify": {"state":"界面状态词"} 或 {"task":"任务名"} 或 null,
  界面状态词只能用这些（中文，必须原文照抄）: 主界面 / 终端页 / 关卡列表页 / 关卡详情页 / 关卡详情页 → 可刷 / 关卡详情页 → 不可刷（代理锁定）/ 战斗中 / 结算页 / 失败页 / 确认弹窗 / 掉线弹窗 / 加载中 / 启动/登录页 / 未知界面
 "why":"一句话说明你在做什么",
 "confidence":0.0~1.0}
规则:
- target 必须从"【可定位的元素】"里挑，并把该行右侧的 JSON **原样**填进 target；
  不要发明模板名、不要写中文名称、不要自己编坐标。
- 不确定就 give_up，并在 why 里说清缺什么信息。
- 不要点 destructive 的东西（放弃战斗/退出/结束进程），除非目标明确要求。"""


def slow_think(obs: Observation, goal: str, llm: LLM | None = None,
               allowed_kinds: tuple = ("tap", "swipe", "back", "wait", "give_up")) -> Action:
    client = llm or LLM()
    if not client.ready:
        return Action("give_up", None, why="LLM 通道未配置（models/config.json 缺 ai_*）",
                      source="none")
    user = (f"【目标】{goal or '推进当前流程'}\n"
            f"{obs.describe()}\n\n"
            f"允许的动作: {', '.join(allowed_kinds)}\n"
            f"下一步该做什么？只回 JSON。")
    r = client.ask_json(_SYS, user)
    if not r.get("ok"):
        return Action("give_up", None, why=f"LLM 失败: {r.get('error', '')[:120]}", source="none")
    d = r["json"]
    kind = str(d.get("action", "")).strip().lower()
    if kind not in allowed_kinds:
        return Action("give_up", None, why=f"LLM 回了不允许的动作 {kind!r}", source="none",
                      extras={"raw": d})
    return Action(kind, d.get("target"), d.get("verify"),
                  why=str(d.get("why", ""))[:200],
                  confidence=float(d.get("confidence") or 0.0), source="llm",
                  extras={"raw": d, "usage": r.get("usage")})


# ── 决策入口 ───────────────────────────────────────────────────────────
class Brain:
    """决策门面：先快后慢，都不行就停下来（give_up），并把现场描述交出来。"""

    def __init__(self, llm: LLM | None = None, use_llm: bool = True,
                 use_vision: bool = True) -> None:
        self.llm = llm or LLM()
        self.use_llm = use_llm
        self.use_vision = use_vision and self.llm.vision_ready

    def decide(self, p: Perception, goal: str = "", img: np.ndarray | None = None,
               battle: bool | None = None, allowed_kinds: tuple = ALLOWED_KINDS,
               forbid: tuple = ()) -> Action:
        """forbid: 本轮**禁止**的目标关键字（例如 ("StartButton1",)）。

        为什么需要它: 硬规则表不看目标就出手 —— 实测目标写着"用演习、别点开始
        行动"，reflex 照样点了开始行动（2026-09-14）。显式约束是唯一可靠的拦法，
        靠提示词里"请不要"是不可靠的。"""
        img = p.shot() if img is None else img
        if battle is None:
            battle = self._looks_like_battle(p, img)
        obs = observe(p, img, battle=battle)
        act = reflex(obs, goal)
        if act is not None and _forbidden(act, forbid):
            log(f"reflex 想执行 {act.target!r}，被 forbid={forbid} 拦下")
            act = None
        if act is not None and self._grounded(p, act, img):
            return act
        if not self.use_llm:
            return Action("give_up", None, why="规则未覆盖且已禁用 LLM", source="none",
                          extras={"observation": obs.describe()})
        act = slow_think(obs, _goal_with_forbid(goal, forbid), self.llm, allowed_kinds)
        if act.kind != "give_up" and _forbidden(act, forbid):
            return Action("give_up", None,
                          why=f"模型选择的目标 {act.target!r} 落在禁止清单 {forbid} 内，拒绝执行",
                          source="none", extras={"observation": obs.describe()})
        if act.kind == "give_up":
            act.extras.setdefault("observation", obs.describe())
            return act
        if not self._grounded(p, act, img):
            # 本地定位失败 → 交给视觉模型把"这是什么"落成像素（它看图，比让
            # 文本脑编坐标可靠得多），并降置信度、靠执行结果验证
            if self.use_vision:
                what = _describe_target(act.target)
                vhit = vision_locate(img, what, self.llm)
                if vhit:
                    act.target, act.resolved = {"coord": list(vhit.center)}, vhit
                    if _forbidden(act, forbid):
                        return Action("give_up", None,
                                      why=f"视觉落点命中禁止清单（{vhit.label[:40]} ∈ {forbid}），拒绝执行",
                                      source="none",
                                      extras={"observation": obs.describe()})
                    act.confidence = min(act.confidence or 0.5, 0.6)
                    act.extras["vision"] = vhit.as_dict()
                    act.why = (act.why + f"；经视觉落点 {vhit.center}"
                               f"（{vhit.label}，置信 {vhit.score:.2f}）")
                    return act
            return Action("give_up", None,
                          why=f"LLM 指定了 {act.target!r}，本地定位不到且视觉也未找到",
                          source="none", extras={"observation": obs.describe(), "raw": act.extras})
        return act

    @staticmethod
    def _looks_like_battle(p: Perception, img: np.ndarray) -> bool:
        try:
            return p.find("BattleOfficiallyBegin", img=img) is not None
        except Exception:
            return False

    @staticmethod
    def _grounded(p: Perception, act: Action, img: np.ndarray) -> bool:
        """动作必须能在当前画面上落地（坐标由本地 CV 给，不由模型编）。"""
        if act.kind != "tap":
            return True
        act.target = normalize_target(act.target)
        try:
            hit = p.locate(act.target, img=img)
        except Exception as e:
            log(f"动作落地失败 {act.target!r}: {e}")
            return False
        act.resolved = hit
        return hit is not None
