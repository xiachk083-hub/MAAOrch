"""AI assistant — analyze MAA task failures via LLM API."""
from __future__ import annotations
import json, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime

_PROVIDER_HINTS = {
    "openai": {"endpoint": "https://api.openai.com/v1/chat/completions", "model": "gpt-4o-mini"},
    "deepseek": {"endpoint": "https://api.deepseek.com/v1/chat/completions", "model": "deepseek-chat"},
    "qwen": {"endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "model": "qwen-plus"},
    "siliconflow": {"endpoint": "https://api.siliconflow.cn/v1/chat/completions", "model": "Qwen/Qwen2.5-7B-Instruct"},
}


def _provider_config(cfg: dict) -> dict:
    provider = cfg.get("ai_provider", "openai")
    hint = _PROVIDER_HINTS.get(provider, {})
    return {
        "endpoint": cfg.get("ai_endpoint", "").strip() or hint.get("endpoint", "https://api.openai.com/v1/chat/completions"),
        "model": cfg.get("ai_model", "").strip() or hint.get("model", "gpt-4o-mini"),
        "api_key": cfg.get("ai_api_key", "").strip(),
    }


def _read_asst_tail(warehouse: list[dict], account_id: str, lines: int = 80) -> str:
    for w in warehouse:
        if w.get("account_ref") == account_id:
            lp = Path(w.get("path", "")).parent / "debug" / "asst.log"
            if lp.exists():
                try:
                    return "\n".join(lp.read_text(encoding="utf-8", errors="replace").split("\n")[-lines:])
                except Exception:
                    return ""
    return ""


def _build_prompt(context: dict) -> str:
    return f"""你是一个明日方舟 MAA 自动化工具的故障诊断专家。
分析以下任务执行记录，给出失败原因和修复建议。

账号: {context.get('name', '?')}
退出码: {context.get('exit_code', '?')}
失败任务: {', '.join(context.get('failed_tasks', ['?']))}
连续失败: {context.get('consecutive_failures', 0)} 次
游戏客户端: {context.get('game_client', '?')}

MAA 日志 (末尾):
```
{context.get('log_tail', '(无)')}
```

请用 JSON 格式回答，不要包含其他内容:
{{"reason": "简短中文原因（一句话）", "suggestion": "修复建议（一句话）", "confidence": "high|medium|low"}}"""


def analyze_failure(
    aid: str,
    name: str,
    exit_code: int,
    failed_tasks: list[str],
    consecutive_failures: int,
    game_client: str,
    warehouse: list[dict],
    config: dict,
) -> dict | None:
    pconf = _provider_config(config)
    if not pconf["api_key"]:
        return None

    log_tail = _read_asst_tail(warehouse, aid)
    context = {
        "name": name,
        "exit_code": exit_code,
        "failed_tasks": failed_tasks,
        "consecutive_failures": consecutive_failures,
        "game_client": game_client,
        "log_tail": log_tail,
    }

    prompt = _build_prompt(context)
    body = json.dumps({
        "model": pconf["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 300,
    }).encode("utf-8")

    req = urllib.request.Request(
        pconf["endpoint"],
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {pconf['api_key']}",
        },
        method="POST",
    )

    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        result = json.loads(content)
        result.setdefault("reason", "未知错误")
        result.setdefault("suggestion", "")
        result.setdefault("confidence", "low")
        return result
    except Exception as e:
        return {"reason": f"AI 分析失败: {e}", "suggestion": "请检查 API 配置和网络连接", "confidence": "low"}
