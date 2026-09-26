"""战斗 HUD 读数 — 用多模态模型读，而不是本地 OCR。

为什么换掉 OCR（2026-09-14 实测，全部真机踩坑）:
  · 费用「12」被读成「72」、「34」被读成「78」—— 艺术字体把 1 认成 7；
    而"费用下降"正是我们判断部署是否成功的**唯一硬判据**，读数不稳等于判据废了。
  · 暂停遮罩上的「PAUSE / 暂停中」本地 OCR **完全读不出**（1x/2x 放大都试过）。
  · 干员名「玛露西尔」被读成「马拓示」（0.39 分）。
  多模态模型这几项都读得准（实测准确读出「终端」「15-11 一段长梦将醒」、
  「剩余可放置角色：8」、「开始行动（消耗 -21 理智）」等）。

设计: 一次调用读完整屏 HUD（而不是一个字段一次调用），把延迟摊薄；
结果带 `source` 字段，便于和本地 OCR 交叉验证、也便于事后审计。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.ai_agent.percept import Perception, log

_SYS = """你在看明日方舟**战斗**截图（1280x720，左上角为原点）。只输出 JSON，不要多余文字。
{
 "paused": true/false,             // 是否处于暂停（画面中央有 PAUSE/暂停中 遮罩）
 "cost": 整数或null,                // 右下角的部署费用数字（剑形图标旁）
 "cost_xy": [x,y] 或 null,          // 费用数字的中心像素
 "deploy_left": 整数或null,          // 「剩余可放置角色」后面的数字
 "enemies": "x/y" 或 null,          // 左上角敌人计数（如 "0/22"）
 "life": 整数或null,                // 顶部生命/目标点数字
 "deployed": 整数,                  // 战场上**我方**已部署干员的个数（不要数敌人）
 "deployed_xy": [[x,y],...],        // 每个我方干员的中心像素（最多 8 个）
 "note": "一句话说明画面状态"
}
规则: 认不出的字段给 null，**不要猜**。敌我区分: 我方干员站在发亮的格子上、通常带蓝色/我方配色；敌人从红门方向来。"""


@dataclass
class Hud:
    paused: bool = False
    cost: int | None = None
    cost_xy: tuple[int, int] | None = None
    deploy_left: int | None = None
    enemies: str | None = None
    life: int | None = None
    deployed: int = 0
    deployed_xy: list = field(default_factory=list)
    note: str = ""
    ok: bool = False
    error: str = ""

    def as_dict(self) -> dict:
        return {"paused": self.paused, "cost": self.cost, "deploy_left": self.deploy_left,
                "enemies": self.enemies, "life": self.life, "deployed": self.deployed,
                "note": self.note, "ok": self.ok, "error": self.error}


def read_hud(p: Perception, img=None, llm=None) -> Hud:
    """读一屏战斗 HUD（一次调用）。失败返回 ok=False 的 Hud，不抛异常。"""
    from services.ai_agent.llm import LLM
    client = llm or LLM()
    img = p.shot() if img is None else img
    if not client.vision_ready:
        return Hud(error="未配置 ai_vision_model")
    r = client.ask_json(_SYS, "读这屏战斗 HUD。", image=img, vision=True, max_tokens=8000)
    if not r.get("ok"):
        return Hud(error=str(r.get("error"))[:150])
    d = r["json"] or {}

    def _int(v):
        try:
            return int(v)
        except Exception:
            return None

    h = Hud(
        paused=bool(d.get("paused")),
        cost=_int(d.get("cost")),
        deploy_left=_int(d.get("deploy_left")),
        enemies=d.get("enemies"),
        life=_int(d.get("life")),
        deployed=_int(d.get("deployed")) or 0,
        note=str(d.get("note") or "")[:120],
        ok=True,
    )
    xy = d.get("cost_xy")
    if isinstance(xy, (list, tuple)) and len(xy) == 2:
        try:
            h.cost_xy = (int(xy[0]), int(xy[1]))
        except Exception:
            h.cost_xy = None
    for pt in (d.get("deployed_xy") or [])[:8]:
        if isinstance(pt, (list, tuple)) and len(pt) == 2:
            try:
                h.deployed_xy.append((int(pt[0]), int(pt[1])))
            except Exception:
                pass
    log(f"HUD(视觉): 暂停={h.paused} 费用={h.cost} 剩余={h.deploy_left} "
        f"敌人={h.enemies} 我方={h.deployed} | {h.note[:50]}")
    return h
