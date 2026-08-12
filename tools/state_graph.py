#!/usr/bin/env python3
"""MAA 隐式状态机 → 显式游戏语义状态图。

MAA 是"死"的: 它不知道自己在哪个界面, 只知道"匹配到模板 X 就点 Y"。
但它的识别条件就是状态定义 —— 我们把它的隐式认知显式化:

    识别到 UsePrts-Annihilation 模板 → 界面处于「关卡详情页」
    识别到 BattleOfficiallyBegin   → 界面处于「战斗中」
    识别到 EndOfAction             → 界面处于「结算」

用法:
    python tools/state_graph.py                   # 全部状态清单（markdown）
    python tools/state_graph.py --state 关卡详情页  # 单状态详情
    python tools/state_graph.py --task UsePrts-Annihilation  # 任务属于哪个状态
    python tools/state_graph.py --mermaid          # mermaid stateDiagram
    python tools/state_graph.py --out docs/MAA_STATE_MAP.md
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "tools"))

from task_graph import load_tasks, resolve_task  # noqa: E402

# ── 语义状态清单（2026-08-12 从 14 域解剖报告提炼，任务名经 task_graph 验证）──
# signals: [(类型, 内容, 位置)]  类型: 模板/OCR/颜色/结构
STATES = [
    {"name": "启动/登录页", "desc": "游戏启动到进入主界面前的所有界面（开始游戏按钮/开始唤醒/B服登录/连接中）",
     "signals": [("模板", "GameStart 区域 550,600", "游戏标题区"),
                 ("OCR", "开始唤醒/登录", "中上部"),
                 ("结构", "StartUpThemes 分诊台命中任一", "全屏")],
     "tasks": ["StartUpBegin", "StartUpThemes", "GameStart", "StartToWakeUp",
               "StartToWakeUpOCR", "StartLoginBServer", "StartUpConnectingFlag"],
     "next": ["加载中", "主界面"]},
    {"name": "加载中", "desc": "黑屏/转场加载（黑色正三角图标或'正在提交'文字）",
     "signals": [("模板", "LoadingIcon（480,210,320,280）", "中央"),
                 ("OCR", "正在提交/反馈至神经", "下方 700,600")],
     "tasks": ["LoadingIcon", "LoadingText", "LoadingTextPostDelay", "StartButton2WaitTime"],
     "next": ["任意目标界面"]},
    {"name": "公告/弹窗清理", "desc": "启动/切页后的公告、今日配给、语音下载等弹窗",
     "signals": [("结构", "CloseAnnos 分诊台命中", "全屏"),
                 ("模板", "CloseAnno 系列 ROI（1100,0 等）", "右上")],
     "tasks": ["CloseAnnos", "CloseAnno", "CloseAnnoTexas", "CloseAnnoSpecialAccess",
               "TodaysSupplies", "NotDownloadVoice"],
     "next": ["主界面", "调用方界面"]},
    {"name": "主界面", "desc": "游戏大厅——所有玩法的共同入口锚点",
     "signals": [("模板", "SwitchTheme@ToggleSettingsMenu.png（227,327,159,152）", "左中"),
                 ("结构", "9 个入口页共享此锚点（Fight/Recruit/Infrast/Award…）", "全屏")],
     "tasks": ["Fight", "StartUp", "Recruit", "Infrast", "Award", "Mall",
               "Gacha", "OperBox", "Visit"],
     "next": ["终端页", "基建页", "公招页", "任务页", "商店页", "抽卡页"]},
    {"name": "终端页", "desc": "终端主界面（16 套主题，主题无关锚点触发后转发）",
     "signals": [("模板", "Terminal-Entry 16 主题之一命中", "右上 844,58")],
     "tasks": ["Terminal-Entry", "TerminalDefault", "TerminalDark",
               "GoLastBattle", "GoTerminalStopflag"],
     "next": ["关卡选择页", "剿灭列表页", "肉鸽内部", "生息内部"]},
    {"name": "关卡选择页", "desc": "主线/活动关卡列表（OCR 关卡名导航）",
     "signals": [("OCR", "关卡名（ClickStageName/SideStoryStage 全屏 roi）", "全屏 0,60"),
                 ("结构", "FullStageNavigation 滑动找关", "全屏")],
     "tasks": ["ClickStageName", "SideStoryStage", "SwipeToStage",
               "ClickedCorrectStage", "FullStageNavigation", "ChapterSlowlySwipeToTheLeft"],
     "next": ["关卡详情页"]},
    {"name": "关卡详情页", "desc": "点关卡后的确认页（代理/全权委托/开始行动按钮区）",
     "signals": [("模板", "UsePrts-Annihilation（877,532,183,123）", "右中"),
                 ("OCR", "开始行动/开始作战/开始推演（1010,625）", "右下"),
                 ("颜色", "开始行动按钮金色占比 > 0.10", "右下 1090,620")],
     "tasks": ["UsePrts-Annihilation", "UsePrts", "UsePrts-StageSN",
               "NotUsePrts", "StartButton1", "UseMedicine", "UseStone",
               "AnnihilationConfirm"],
     "next": ["战斗中", "关卡选择页"]},
    {"name": "战斗中", "desc": "战斗进行中（顶角标记出现，5s 轮询）",
     "signals": [("模板", "BattleOfficiallyBegin（1165,20,75,65, maskRange 滤浅色）", "右上顶角")],
     "tasks": ["PRTS", "PRTS1", "PRTS2", "PRTS3", "WaitAfterPRTS",
               "BattleOfficiallyBegin", "BattleStart", "BattlePauseCancelCheck"],
     "next": ["结算页", "失败页"]},
    {"name": "结算页", "desc": "战斗结束结算（掉落解析插件挂载点）",
     "signals": [("模板", "EndOfAction（0,290,70,60）", "左上"),
                 ("结构", "ClickCorner 盲区点击再来一局", "右上")],
     "tasks": ["EndOfAction", "EndOfActionAnnihilation", "ClickCorner",
               "ClickCornerAfterPRTS", "CheckLevelUp"],
     "next": ["关卡详情页", "主界面"]},
    {"name": "失败页", "desc": "任务失败（3 连败停止/降级换关）",
     "signals": [("OCR", "任务失败（36,226,482,269）", "左中")],
     "tasks": ["FightMissionFailed", "FightMissionFailedAndStop", "PrtsErrorConfirm",
               "AbandonAction"],
     "next": ["结算页", "关卡详情页"]},
    {"name": "掉线弹窗", "desc": "网络断线重连确认",
     "signals": [("模板", "OfflineConfirm（PopupConfirm 630,400,650,160）", "中央")],
     "tasks": ["OfflineConfirm", "OfflineConfirmImpl", "OfflineConfirmAfterBattle"],
     "next": ["主界面", "调用方界面"]},
    {"name": "确认弹窗", "desc": "通用二次确认（返回确认/刷新确认/公招加速确认共享同一模板）",
     "signals": [("模板", "PopupConfirm（630,400,650,160）", "中央")],
     "tasks": ["ReturnConfirm", "RecruitRefreshConfirm", "RecruitNowConfirm",
               "MedicineConfirm", "StoneConfirm", "GachaConfirm"],
     "next": ["调用方界面"]},
    {"name": "剿灭列表页", "desc": "剿灭关卡选择（切关菜单 + OCR 三关名）",
     "signals": [("模板", "StageAnnihilation（900,70,380,400, 0.9）", "右上 ToDoList"),
                 ("OCR", "切换（966,577）/切尔诺伯格/龙门外环/龙门市区", "右下/中部")],
     "tasks": ["Annihilation", "Chernobog@Annihilation", "AnnihilationSwitchButton",
               "Chernobog@AnnihilationStage", "AnnihilationReturn"],
     "next": ["关卡详情页"]},
    {"name": "基建页", "desc": "基建总览/设施内页",
     "signals": [("模板", "InfrastOverview（0,100,300,150, 0.95）", "左上"),
                 ("模板", "设施卡片 Mini 模板（Mfg/Trade/Dorm…）", "中部")],
     "tasks": ["InfrastBegin", "InfrastEnteredFlag", "InfrastMfg", "InfrastMfgMini",
               "InfrastEnterOperList", "InfrastConfirmButton", "InfrastDormConfirmButton"],
     "next": ["主界面"]},
    {"name": "公招页", "desc": "公开招募界面（标签 OCR + 招募按钮）",
     "signals": [("OCR", "公开招募（50,100,230,100）", "左上"),
                 ("OCR", "开始招（0,300,1280,420）", "中下")],
     "tasks": ["Recruit", "RecruitBegin", "RecruitFlag", "RecruitTags",
               "StartRecruit", "RecruitNow", "RecruitConfirm"],
     "next": ["主界面"]},
    {"name": "任务/领取页", "desc": "每日/每周任务奖励领取",
     "signals": [("模板", "ReceiveAward（950,50,330,150, 金 0.79）", "右上"),
                 ("颜色", "AwardFinished 顶栏红点消失（HSVCount）", "顶部")],
     "tasks": ["AwardBegin", "ReceiveAward", "CloseTaskAward", "DailyTask",
               "WeeklyTask", "AwardFinished", "MailBegin", "ReceiveMail"],
     "next": ["主界面"]},
    {"name": "商店页", "desc": "信用/绿票/黄票商店",
     "signals": [("OCR", "信用交易所/资质凭证区/高级凭证区", "顶部"),
                 ("结构", "Store@Begin 分诊", "全屏")],
     "tasks": ["Store@Begin", "CreditStoreOcr", "CollectCredit", "CreditShop-BuyIt",
               "ChooseMaxAmount", "Purchase", "UnderfundedOCR"],
     "next": ["主界面"]},
    {"name": "抽卡页", "desc": "寻访界面",
     "signals": [("OCR", "寻访一次/十次（779,576,248,141）", "右下"),
                 ("模板", "Gacha-Entry 15 主题之一", "入口")],
     "tasks": ["Gacha", "Gacha-Entry", "DoGacha", "GachaConfirm",
               "GachaSkip", "GachaSwipeToOpenBag"],
     "next": ["主界面"]},
    {"name": "肉鸽内部", "desc": "集成战略整局（主题选择/节点/战斗/结算，状态中枢分诊）",
     "signals": [("结构", "Roguelike@Begin 分诊命中任一", "全屏")],
     "tasks": ["Roguelike@Begin", "Roguelike@SelectTheme", "Roguelike@StartExplore",
               "Roguelike@Stages", "Roguelike@GamePass"],
     "next": ["主界面"]},
    {"name": "生息内部", "desc": "生息演算整局（建设/采集/跳天，C++ 入口）",
     "signals": [("结构", "Reclamation2 分诊命中任一", "全屏")],
     "tasks": ["Reclamation2", "RA1-Prepare", "Tales@RA@PIS-Commence"],
     "next": ["主界面"]},
    {"name": "未知界面", "desc": "没有任何已知信号命中——MAA 会死磕兜底链，我们应该停下记录",
     "signals": [("结构", "全部状态信号不命中", "全屏")],
     "tasks": [],
     "next": []},
]

# 转移边（语义层, 与任务 next 链对应）
_EDGES = [
    ("启动/登录页", "加载中"), ("启动/登录页", "主界面"),
    ("加载中", "主界面"), ("加载中", "终端页"), ("加载中", "关卡详情页"),
    ("公告/弹窗清理", "主界面"),
    ("主界面", "终端页"), ("主界面", "基建页"), ("主界面", "公招页"),
    ("主界面", "任务/领取页"), ("主界面", "商店页"), ("主界面", "抽卡页"),
    ("终端页", "关卡选择页"), ("终端页", "剿灭列表页"),
    ("终端页", "肉鸽内部"), ("终端页", "生息内部"),
    ("关卡选择页", "关卡详情页"), ("剿灭列表页", "关卡详情页"),
    ("关卡详情页", "战斗中"), ("关卡详情页", "关卡选择页"),
    ("战斗中", "结算页"), ("战斗中", "失败页"),
    ("结算页", "关卡详情页"), ("结算页", "主界面"),
    ("失败页", "结算页"), ("失败页", "关卡详情页"),
    ("掉线弹窗", "主界面"), ("确认弹窗", "调用方界面"),
    ("肉鸽内部", "主界面"), ("生息内部", "主界面"),
    ("任意状态", "加载中"), ("任意状态", "掉线弹窗"), ("任意状态", "确认弹窗"),
]


def _verify() -> list[str]:
    """验证状态清单里的任务真实存在（宽松匹配：允许 @ 前缀变体）。"""
    tasks = load_tasks(None)
    all_names = set(tasks.keys())

    def exists(t: str) -> bool:
        return t in all_names or any(n.endswith("@" + t) for n in all_names)

    problems = []
    for st in STATES:
        for t in st["tasks"]:
            if not exists(t):
                problems.append(f"[{st['name']}] 任务不存在: {t}")
    return problems


def render_markdown() -> str:
    lines = ["# MAA 游戏语义状态图（MAA 隐式状态机的显式化）", "",
             "> 2026-08-12 从 14 域任务解剖提炼 — MAA 不知道自己在哪个界面，",
             "> 但它的每个识别条件就是一条状态定义。这张图把它不自知的认知显式化。", "",
             "## 状态清单", ""]
    for st in STATES:
        lines.append(f"### {st['name']}")
        lines.append(f"{st['desc']}")
        lines.append("")
        lines.append("| 信号类型 | 内容 | 位置 |")
        lines.append("|----------|------|------|")
        for stype, content, pos in st["signals"]:
            lines.append(f"| {stype} | `{content}` | {pos} |")
        if st["tasks"]:
            lines.append("")
            lines.append(f"关联任务（{len(st['tasks'])}）: `{'`, `'.join(st['tasks'])}`")
        if st["next"]:
            lines.append(f"转移: {' → '.join(st['next'])}")
        lines.append("")
    lines.append("## 转移关系")
    lines.append("")
    lines.append("```mermaid")
    lines.append("stateDiagram-v2")
    lines.append("    [*] --> 启动/登录页")
    for a, b in _EDGES:
        if a == "任意状态":
            continue
        lines.append(f"    {a} --> {b}")
    lines.append("    [*] --> 未知界面")
    lines.append("```")
    lines.append("")
    lines.append("## 关键认识")
    lines.append("")
    lines.append("1. **MAA 的认知 = 一组识别信号**：命中什么模板/OCR = 在什么界面，仅此而已")
    lines.append("2. **状态转移 = next 链**：识别信号 → 动作 → 下一组信号，MAA 不自知地在状态机里走")
    lines.append("3. **未知界面是 MAA 的盲区**：它只会死磕兜底链（重开游戏）；我们应该停下记录报警")
    lines.append("4. **画面理解工具基于此表**：截图 → 逐状态验证信号 → 输出状态+证据")
    return "\n".join(lines)


def render_mermaid() -> str:
    lines = ["stateDiagram-v2", "    [*] --> 启动/登录页"]
    for a, b in _EDGES:
        if a == "任意状态":
            continue
        lines.append(f"    {a} --> {b}")
    lines.append("    [*] --> 未知界面")
    return "\n".join(lines)


def find_state_of_task(task: str) -> list[str]:
    return [st["name"] for st in STATES if task in st["tasks"]]


def main() -> None:
    ap = argparse.ArgumentParser(description="MAA 语义状态图")
    ap.add_argument("--state", help="单状态详情")
    ap.add_argument("--task", help="任务属于哪个状态")
    ap.add_argument("--mermaid", action="store_true")
    ap.add_argument("--out", help="输出 markdown 到文件")
    ap.add_argument("--verify", action="store_true", help="验证状态清单任务真实性")
    args = ap.parse_args()

    if args.verify:
        problems = _verify()
        if problems:
            print("发现问题:")
            for p in problems:
                print(" ", p)
        else:
            print("状态清单全部任务验证通过")
        return
    if args.state:
        for st in STATES:
            if args.state in st["name"]:
                print(f"# {st['name']}\n{st['desc']}\n")
                for stype, content, pos in st["signals"]:
                    print(f"- [{stype}] {content}（{pos}）")
                if st["tasks"]:
                    print(f"\n关联任务: {', '.join(st['tasks'])}")
                if st["next"]:
                    print(f"转移: {' → '.join(st['next'])}")
                return
        print(f"无状态 {args.state}")
        return
    if args.task:
        found = find_state_of_task(args.task)
        print(f"{args.task} → 状态: {', '.join(found) if found else '未收录（可能属于子流程内部）'}")
        return
    if args.mermaid:
        print(render_mermaid())
        return
    text = render_markdown()
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
