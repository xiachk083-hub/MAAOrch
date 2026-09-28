#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""client_state_check.py — 只读客户端状态检测（截图 + OCR 关键词判别）

把「客户端停在哪、有没有挡路弹窗」变成一行可判定的输出，供发车前预检 / 故障定位。

只读：仅做 adb screencap（或读本地图片）+ OCR；不点击、不按键、不改配置。

用法：
  # 1) 对一台在线实例现场判定（在装有 adb 的机器上运行；本仓库场景=远端主机）
  python client_state_check.py --serial 127.0.0.1:7555
  python client_state_check.py --serial emulator-5554 --adb "E:/Platform-tools/adb.exe"

  # 2) 离线判定已有截图（不碰设备）
  python client_state_check.py --image "D:/.../guan1_verify/state_modal.png"

  # 3) 批量判定（每行一个图片路径）
  python client_state_check.py --list pool.txt --jsonl

输出：
  CLIENT_STATE=<正常|正常:大厅|正常:标题页|模态:登录被取消|模态:更新提示|模态:公告|模态:活动页|
                模态:退出确认|模态:账号管理残留|模态:世界观光卡|桌面|未登录|未知>
  随后附 ACTION=<安全处置建议>（点击坐标按归一化百分比给出，各分辨率通用）
  --json / --jsonl 输出 {state, action, evidence, size} 结构。

判定顺序与关键词（依据 2026-09-27 实拍帧，样本见 docs/audits/2026-09-27_client_state_detection.md）：
  登录被取消 > 退出/删除确认 > 更新提示 > 公告页 > 活动页 > 签到页 > 账号管理残留 >
  世界观光卡(冷启动) > 桌面 > 大厅 > 加载页 > 连接页 > 关卡页 > 招募页 > 基建页 >
  标题页 > 未登录(兜底) > 未知
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# 处置建议表：坐标一律用归一化百分比（x%, y%），来源=实拍帧量取（1600x900 与 1280x720 帧一致）。
ACTIONS: dict[str, str] = {
    "模态:登录被取消": "点弹窗按钮（对话框下半部 ≈(50%,52%)；按钮字迹小，或直接按 BACK 关闭）后重试",
    "模态:更新提示": "点「确认/更新」，等客户端自动更新完成；强更需重启客户端，更新期间 MAA 无法跑",
    "模态:公告": "点右上角 X ≈(94%,10%) 关闭公告页（或 BACK）",
    "模态:活动页": "点右上角 X ≈(91%,13%) 关闭活动页（或 BACK）",
    "模态:签到页": "点右上角 X ≈(95%,10%) 关闭签到页（或领取后关闭）",
    "模态:退出确认": "点「取消」（对话框下半部左侧按钮；勿点「确认」，会退出游戏）",
    "模态:账号管理残留": "按 BACK（或点面板左侧空白 ≈(12%,50%)）关闭账号管理面板；勿在面板内乱点",
    "模态:世界观光卡": "点 START ≈(50%,95%)（或屏幕中央）推进到标题页；冷启动卡，MAA 亦能点",
    "模态:加载页": "过渡态：正常数秒~数十秒通过；若 >60s 滞留=加载卡住，重开客户端再发车",
    "模态:连接页": "等连接完成（正常 5-30s）；>2min 滞留=网络/服务器问题，重开客户端",
    "正常:关卡页": "正常（选关/作战页在游戏内），无需处理",
    "正常:招募页": "正常（公开招募界面在游戏内），无需处理",
    "正常:基建页": "正常（基建/干员界面在游戏内），无需处理",
    "桌面": "正常收尾留机；如要跑任务由发车方启动游戏，或点桌面「明日方舟」图标",
    "正常:大厅": "正常，无需处理",
    "正常:标题页": "正常（MAA 会点「开始唤醒」），无需处理",
    "未登录": "客户端未处于已登录标题页：检查登录态/走一次账号切换；勿直接发车",
    "未知": "未匹配到已知状态：人工看图确认",
}

MASKED_PHONE = re.compile(r"\d{3}\*{4}\d{4}")

# 判定规则：(内部状态, 命中条件)。条件函数接收 OCR 全文。
RULES: list[tuple[str, object]] = []


def _rule(state: str):
    def deco(fn):
        RULES.append((state, fn))
        return fn

    return deco


@_rule("模态:登录被取消")
def _login_cancelled(t: str) -> bool:
    return "登录被取消" in t


@_rule("模态:退出确认")
def _exit_confirm(t: str) -> bool:
    return "是否确认退出游戏" in t or "是否确认删除该账号的登录记录" in t


@_rule("模态:更新提示")
def _update_notice(t: str) -> bool:
    for k in ("资源更新", "更新提示", "发现新版本", "下载新版本", "需要更新", "强制更新"):
        if k in t:
            return True
    return ("下载" in t) and ("更新" in t)


@_rule("模态:公告")
def _news(t: str) -> bool:
    hits = sum(1 for k in ("活动公告", "系统公告", "资讯速报") if k in t)
    return hits >= 2


@_rule("模态:活动页")
def _activity(t: str) -> bool:
    return "月饼烘焙小教程" in t or ("锦枫映月" in t and "月饼" in t)


@_rule("模态:签到页")
def _signin(t: str) -> bool:
    return ("九月签到" in t) or (("常规配给" in t) and ("高级凭证" in t))


@_rule("模态:账号管理残留")
def _acct_panel(t: str) -> bool:
    if "登录其他账号" in t:
        return True
    return ("鹰角网络" in t) and ("上次登录" in t) and bool(MASKED_PHONE.search(t))


@_rule("模态:世界观光卡")
def _coldstart(t: str) -> bool:
    if ("清除缓存" in t) and ("网络检测" in t):
        return True
    if ("适龄提示" in t) and any(
        k in t for k in ("矿石病", "维多利亚帝国", "罗德岛制药", "泰拉")
    ):
        return True
    return ("START" in t) and any(k in t for k in ("Ver:", "适龄提示", "清除缓存"))


@_rule("桌面")
def _desktop(t: str) -> bool:
    if "搜索游戏和应用" in t:
        return True
    if ("游戏中心" in t) and ("明日方舟" in t):
        return True
    return ("TapTap" in t) and ("浏览器" in t)


@_rule("正常:大厅")
def _lobby(t: str) -> bool:
    if ("终端" in t) and ("签到活动" in t or "理智" in t):
        return True
    if ("编队" in t) and ("干员" in t):
        return True
    if any(k in t for k in ("作战进度", "TO-DO LIST", "长期剿灭委托", "攻略完了",
                            "SQUADS", "OPERATOR", "HEADHUNT")):
        return True
    return bool(re.search(r"TERM[Il1iİ]N", t))


@_rule("模态:加载页")
def _loading(t: str) -> bool:
    # 游戏加载 tip 页：RHODESISLAND 水印 + 干员/作战提示；世界观光卡的同类水印已被前面规则截获
    return ("RHODESISLAND" in t) and any(
        k in t for k in ("即可获得", "干员", "作战", "优先", "选择", "部署")
    )


@_rule("模态:连接页")
def _connecting(t: str) -> bool:
    return ("正在尝试与" in t) or ("神经连接" in t)


@_rule("正常:关卡页")
def _stage(t: str) -> bool:
    return "推荐平均等级" in t


@_rule("正常:招募页")
def _recruit(t: str) -> bool:
    return any(k in t for k in ("連络", "連絡", "联络", "募集"))


@_rule("正常:基建页")
def _infra(t: str) -> bool:
    return ("进驻状态" in t) or (("工作状态" in t) and ("心情" in t))


@_rule("正常:标题页")
def _title(t: str) -> bool:
    return "开始唤醒" in t or ("账号管理" in t and "查看公告" in t)


def classify(text: str) -> tuple[str, list[str]]:
    """Return (state, evidence-tokens)."""
    if not text.strip():
        return "未知", ["空白帧"]
    tokens: list[str] = []
    for state, cond in RULES:
        try:
            if cond(text):  # type: ignore[operator]
                tokens = [k for k in ("登录被取消", "是否确认", "活动公告", "月饼烘焙小教程",
                                      "登录其他账号", "上次登录", "清除缓存", "适龄提示",
                                      "搜索游戏和应用", "游戏中心", "终端", "签到活动",
                                      "开始唤醒", "账号管理") if k in text]
                return state, tokens
        except Exception:  # noqa: BLE001 - rule must never break the run
            continue
    # 兜底：像客户端但没到已登录标题页 → 未登录；完全无客户端痕迹 → 未知
    if any(k in text for k in ("鹰角网络", "ARKNIGHTS", "HYPERGRYPH")):
        return "未登录", ["客户端元素未见已登录标题页"]
    if "明日方舟" in text:
        return "未登录", ["仅见应用名"]
    return "未知", []


# ---------------- 图像来源 ----------------

def ocr_image(path: Path) -> tuple[str, tuple[int, int]]:
    import cv2  # 延迟导入，确保 --help 无需重依赖
    from rapidocr_onnxruntime import RapidOCR

    img = cv2.imread(str(path))
    if img is None:
        raise SystemExit(f"无法读取图片: {path}")
    h, w = img.shape[:2]
    engine = RapidOCR()
    res, _ = engine(img)
    text = "\n".join(line[1] for line in res) if res else ""
    return text, (w, h)


def screencap(serial: str, adb: str) -> Path:
    """adb exec-out screencap -p → 临时 png（只读操作）。"""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        out = Path(f.name)
    cmd = [adb, "-s", serial, "exec-out", "screencap", "-p"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0 or not proc.stdout.startswith(b"\x89PNG"):
        # 部分 Windows adb 会把 \n 翻译成 \r\n；修复后再试一次
        data = proc.stdout.replace(b"\r\n", b"\n")
        if not data.startswith(b"\x89PNG"):
            raise SystemExit(
                f"screencap 失败 (rc={proc.returncode}): {proc.stderr.decode('utf-8', 'replace')[:200]}"
            )
    else:
        data = proc.stdout
    out.write_bytes(data)
    return out


# ---------------- CLI ----------------

def main() -> int:
    ap = argparse.ArgumentParser(description="只读客户端状态检测")
    ap.add_argument("--serial", help="adb 设备序列号（如 127.0.0.1:7555）")
    ap.add_argument("--adb", default="adb", help="adb 可执行文件路径（默认 PATH 中的 adb）")
    ap.add_argument("--image", help="离线判定单张截图")
    ap.add_argument("--list", dest="list_file", help="批量判定：每行一个图片路径")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--jsonl", action="store_true", help="批量模式逐行 JSON")
    args = ap.parse_args()

    if args.list_file:
        total = 0
        counts: dict[str, int] = {}
        for line in Path(args.list_file).read_text(encoding="utf-8").splitlines():
            p = line.strip()
            if not p:
                continue
            text, size = ocr_image(Path(p))
            state, tokens = classify(text)
            total += 1
            counts[state] = counts.get(state, 0) + 1
            if args.jsonl:
                print(json.dumps({"file": p, "state": state, "evidence": tokens,
                                  "size": size}, ensure_ascii=False))
            else:
                print(f"{state}\t{p}")
        print(f"\n# total={total} counts={json.dumps(counts, ensure_ascii=False)}")
        return 0

    if args.image:
        text, size = ocr_image(Path(args.image))
        src = str(args.image)
    elif args.serial:
        shot = screencap(args.serial, args.adb)
        text, size = ocr_image(shot)
        src = f"adb:{args.serial}"
    else:
        ap.error("需要 --serial 或 --image（或 --list）")

    state, tokens = classify(text)
    action = ACTIONS.get(state) or ACTIONS["未知"]
    if args.json:
        print(json.dumps({"state": state, "action": action, "evidence": tokens,
                          "source": src, "size": size}, ensure_ascii=False, indent=2))
    else:
        print(f"CLIENT_STATE={state}")
        print(f"ACTION={action}")
        if tokens:
            print(f"EVIDENCE={','.join(tokens)}")
        print(f"SOURCE={src} {size[0]}x{size[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
