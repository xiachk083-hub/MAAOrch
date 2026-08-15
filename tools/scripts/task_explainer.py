"""Task explainer — 用 DeepSeek 分析 MAA 任务链/图片转译，解释逻辑并辅助设计自定义任务。

用法:
    python services/task_explainer.py --task Fight        # 解释任务链逻辑
    python services/task_explainer.py --image <png>       # 分析图片（转译后问 DeepSeek）
    python services/task_explainer.py --custom "晚上自动清体力"  # 生成自定义任务建议

配置: 复用 ai_assistant 的设置（config.json 的 ai_provider/ai_endpoint/ai_model/ai_api_key），
默认 provider=deepseek。DeepSeek 无视觉 — 图片走 tools/vision_translate.py 转译成文本。
无 key 时给出配置提示，不影响其他工具。
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "tools"))

from ai_assistant import _provider_config  # noqa: E402
from infrastructure.utils import safe_urlopen  # noqa: E402

try:
    from models.config_manager import load_config
except Exception:
    def load_config() -> dict:
        return {}


def _ask_deepseek(question: str, config: dict, max_tokens: int = 1200) -> str:
    """调用配置的 LLM（默认 deepseek）返回文本回答。"""
    pconf = _provider_config(config)
    if not pconf["api_key"]:
        return ("未配置 AI API key。请到 设置页 → AI 助手 配置 ai_api_key "
                "（或 config.json 的 ai_api_key 字段），provider 默认 deepseek。")
    body = json.dumps({
        "model": pconf["model"],
        "messages": [
            {"role": "system", "content":
             "你是明日方舟 MAA 自动化任务专家，精通 MAA 任务 JSON 机制"
             "（识别条件/动作/状态转移/兜底）。回答简洁、分步骤、用中文。"},
            {"role": "user", "content": question},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = __import__("urllib.request", fromlist=["Request"]).Request(
        pconf["endpoint"],
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {pconf['api_key']}",
        },
        method="POST",
    )
    try:
        resp = safe_urlopen(req, timeout=60)
        data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[LLM 调用失败: {e}] 请检查网络与 API 配置"


def explain_task_chain(task_name: str, config: dict, depth: int = 10) -> str:
    """任务链 → DeepSeek 解释。"""
    from task_graph import load_tasks, render_chain

    tasks = load_tasks(None)
    try:
        chain = "\n".join(render_chain(tasks, task_name, depth=depth))
    except SystemExit as e:
        return str(e)
    question = (
        "以下是 MAA 的任务链展开（每个任务 = 识别条件 | 动作 | 跳转）：\n```\n"
        f"{chain}\n```\n"
        "请解释这条任务链的逻辑：每一步【识别什么画面 → 做什么操作 → 进入什么状态】。"
        "最后用一句话总结这个流程在干什么。"
    )
    return _ask_deepseek(question, config)


def explain_image(image_path: str, config: dict, no_ocr: bool = False) -> str:
    """图片转译 → DeepSeek 判断界面元素与用途。"""
    from vision_translate import to_markdown, translate
    from task_graph import load_tasks

    tasks = load_tasks(None)
    result = translate(Path(image_path), tasks, do_ocr=not no_ocr)
    md = to_markdown(result)
    question = (
        "这是一张 MAA 游戏截图/模板图的转译描述（OCR 文字 + 像素特征 + 被哪些任务引用）：\n"
        f"{md}\n"
        "请判断：1) 这是什么界面元素/状态？2) 在明日方舟游戏里出现在哪里？"
        "3) MAA 拿它识别什么、用于什么判断？"
    )
    return _ask_deepseek(question, config)


def suggest_custom_task(requirement: str, config: dict) -> str:
    """自定义任务需求 → DeepSeek 生成 MAA 任务 JSON 定义。"""
    question = (
        f"我要为明日方舟 MAA 自定义一个自动化流程：{requirement}\n"
        "请输出一个 MAA 任务 JSON 定义（tasks.json 格式），包含："
        "识别条件（template 或 OcrDetect text + roi）、动作、next 跳转、"
        "兜底（maxTimes/exceededNext）。"
        "并简要说明每一步的作用。注意识别条件要窄（限定 roi），每步都要有兜底。"
    )
    return _ask_deepseek(question, config, max_tokens=2000)


def main() -> None:
    ap = argparse.ArgumentParser(description="MAA 任务逻辑问答（DeepSeek）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--task", help="解释任务链（从该任务展开）")
    g.add_argument("--image", help="分析图片（自动转译）")
    g.add_argument("--custom", help="生成自定义任务定义建议")
    ap.add_argument("--depth", type=int, default=10, help="任务链展开深度")
    ap.add_argument("--no-ocr", action="store_true", help="图片转译跳过 OCR")
    args = ap.parse_args()

    config = load_config()
    if args.task:
        print(explain_task_chain(args.task, config, args.depth))
    elif args.image:
        print(explain_image(args.image, config, args.no_ocr))
    elif args.custom:
        print(suggest_custom_task(args.custom, config))


if __name__ == "__main__":
    main()
