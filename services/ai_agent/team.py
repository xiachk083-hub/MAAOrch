"""小组作业模型·最小形态（机制三）：need_help 协议 + 编排器路由 + 预定义角色池。

设计边界（2026-09-16 用户指出的两条，已编码）：
  · 边界一【LLM 不是"不会"而是"看不到"】：求助分两类——
      capability（能力缺口：需要别的角色来做）→ 可路由给池中其他角色
      information（信息缺口：同一帧里看不到/被遮挡）→ **禁止**转发给另一个视觉角色
        （它看的是同一帧，转发等于空转），只能升级给编排器/人（换帧、关面板）
  · 边界二【动态招人 = token 爆炸】：预定义池 + 按需启用，且**每局 Agent 数上限**、
    每 Agent 调用上限；超限降级到规则引擎（不新增 Agent）。

这是"预定义角色池 + 按需启用"的版本；"运行时新建角色"（机制四）留到下一步。
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent.parent
STRAT = PROJ / "strategy"

# 预定义角色池（enabled 按需打开；prompt 从 strategy/*.md 读，人可维护）
AGENT_POOL: dict[str, dict] = {
    "vision":    {"prompt": "vision_prompt.md",   "can": ["read_frame"],            "enabled": False, "calls": 0},
    "decision":  {"prompt": "decision_prompt.md", "can": ["choose_action"],         "enabled": False, "calls": 0},
    "challenge": {"prompt": None,                 "can": ["challenge_decision"],    "enabled": False, "calls": 0},
    "review":    {"prompt": "review_prompt.md",   "can": ["analyze_trace"],         "enabled": False, "calls": 0},
}

MAX_AGENTS_PER_GAME = 4     # 边界二：不无节制招人
MAX_CALLS_PER_AGENT = 10


@dataclass
class Msg:
    frm: str
    to: str
    type: str                       # query | reply | need_help
    content: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class Bus:
    """极简消息总线（内存 + 落盘邮箱，便于赛后复盘）。"""

    def __init__(self, dump: Path | None = None):
        self.log: list[Msg] = []
        self.dump = dump

    def send(self, m: Msg) -> None:
        self.log.append(m)
        if self.dump:
            with self.dump.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"frm": m.frm, "to": m.to, "type": m.type,
                                    "content": m.content, "ts": m.ts}, ensure_ascii=False) + "\n")

    def pending(self, kind: str = "need_help") -> list[Msg]:
        return [m for m in self.log if m.type == kind]


class Orchestrator:
    def __init__(self, bus: Bus | None = None):
        self.bus = bus or Bus()
        self.enabled: dict[str, str] = {}      # role -> system_prompt

    # ---- 机制三：增援请求处理 ----
    def on_need_help(self, m: Msg) -> Msg:
        kind = (m.content or {}).get("kind", "capability")
        need = (m.content or {}).get("need", "")
        reason = (m.content or {}).get("reason", "")

        # 边界一：信息缺口不转发（同一帧，转发=空转）
        if kind == "information":
            self.bus.send(Msg("orchestrator", m.frm, "reply",
                              {"action": "escalate_human",
                               "why": f"信息缺口（{reason}）不能靠别的 Agent 解决——它们看的是同一帧；"
                                      f"应由编排器/人换帧（关面板、换采样时刻）后重拍"}))
            return self.bus.log[-1]

        # 能力缺口：先查池子里现成的
        for role, spec in AGENT_POOL.items():
            if need in spec["can"] and role != m.frm:
                if len(self.enabled) >= MAX_AGENTS_PER_GAME and role not in self.enabled:
                    self.bus.send(Msg("orchestrator", m.frm, "reply",
                                      {"action": "degrade_rule_engine",
                                       "why": f"已达每局 Agent 上限 {MAX_AGENTS_PER_GAME}，不新增角色，"
                                              f"降级到规则引擎"}))
                    return self.bus.log[-1]
                self.enable(role)
                self.bus.send(Msg("orchestrator", role, "query",
                                  {"task": (m.content or {}).get("task", ""),
                                   "from": m.frm, "reason": reason}))
                return self.bus.log[-1]

        self.bus.send(Msg("orchestrator", m.frm, "reply",
                          {"action": "no_capable_agent",
                           "why": f"池中无角色具备 {need}；机制四（运行时新建角色）未启用 → 降级规则引擎"}))
        return self.bus.log[-1]

    def enable(self, role: str) -> None:
        spec = AGENT_POOL.get(role)
        if not spec:
            return
        spec["enabled"] = True
        p = STRAT / spec["prompt"] if spec["prompt"] else None
        self.enabled[role] = p.read_text(encoding="utf-8") if (p and p.exists()) else f"(无提示词文件) 角色={role} can={spec['can']}"
        self.bus.send(Msg("orchestrator", role, "query", {"welcome": f"你被启用，职责={spec['can']}"}))

    def note_call(self, role: str) -> bool:
        """Agent 实际调用 LLM 前记账；超限返回 False（应降级规则引擎）。"""
        spec = AGENT_POOL.get(role)
        if not spec:
            return False
        if spec["calls"] >= MAX_CALLS_PER_AGENT:
            return False
        spec["calls"] += 1
        return True


if __name__ == "__main__":
    # smoke test：决策 Agent 请求复核 → 编排器启用质疑 Agent；信息缺口 → 升级给人而非转发
    b = Bus(PROJ / "data" / "agent_run" / "team_bus.jsonl")
    o = Orchestrator(b)
    o.enable("decision")
    r1 = o.on_need_help(Msg("decision", "orchestrator", "need_help",
                            {"kind": "capability", "need": "challenge_decision",
                             "task": "复核 T+35 是否该撤凛御银灰", "reason": "两种方案收益接近"}))
    assert r1.to == "challenge", r1
    print("✅ 能力缺口 → 已启用并转发给 challenge:", r1.content)
    r2 = o.on_need_help(Msg("vision", "orchestrator", "need_help",
                            {"kind": "information", "need": "read_frame",
                             "task": "确认 [9,5] 干员身份", "reason": "面板遮挡+箭头淡出"}))
    assert r2.content.get("action") == "escalate_human", r2
    print("✅ 信息缺口 → 不转发，升级给人:", r2.content["action"])
    for _ in range(MAX_CALLS_PER_AGENT):
        assert o.note_call("vision")
    assert o.note_call("vision") is False
    print(f"✅ 单 Agent 调用上限 {MAX_CALLS_PER_AGENT} 生效（第 {MAX_CALLS_PER_AGENT+1} 次被拒）")
    print("bus 消息数:", len(b.log))
