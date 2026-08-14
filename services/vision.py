"""Vision service — 截图发给视觉大模型（OpenCode Zen qwen3.8-max），返回理解。

2026-08-13 用户: 接入 ZCode 配置的 opencode-go provider（qwen3.8-max，
支持 image 输入）— 直接看图回答问题，替代本地四通道转译。

调用协议: Anthropic Messages API（OpenCode Zen 的 /zen/go 通道，cost=0）
端点: https://opencode.ai/zen/go/v1/messages
模型: qwen3.8-max（thinking 模型 — 响应含 thinking 块，取 text 块为答案）

用法:
    from services.vision import understand_image
    r = understand_image(img_bytes_or_path, "这是什么界面？")
"""
from __future__ import annotations
import base64
import json
import pathlib
import urllib.request
import urllib.error

DEFAULT_ENDPOINT = "https://opencode.ai/zen/go/v1/messages"
DEFAULT_MODEL = "qwen3.8-max"
# 注意: 不内置任何 API key（2026-08-14 审查 #2a — 硬编码 key 入公开仓库/
# 部署清单即泄露面）。key 只从 config.json ai_api_key（UI 可配）或环境变量
# DSH_AI_API_KEY 读取；两者都缺 → 明确报错，不静默降级。
UA = {"User-Agent": "Mozilla/5.0 (MAAOrch vision)"}


def _config() -> dict:
    """config.json 优先，其次环境变量；key 缺省为空（调用时明确报错）。"""
    try:
        from models.config_manager import load_config
        cfg = load_config()
    except Exception:
        cfg = {}
    import os
    return {
        "endpoint": (cfg.get("ai_endpoint", "") or "").strip() or DEFAULT_ENDPOINT,
        "model": (cfg.get("ai_model", "") or "").strip() or DEFAULT_MODEL,
        "api_key": ((cfg.get("ai_api_key", "") or "").strip()
                    or os.environ.get("DSH_AI_API_KEY", "").strip()),
    }


def _read_image(src) -> bytes:
    if isinstance(src, (str, pathlib.Path)):
        p = pathlib.Path(src)
        if not p.exists():
            raise FileNotFoundError(str(p))
        return p.read_bytes()
    if isinstance(src, bytes):
        return src
    raise TypeError("src must be path or bytes")


def _extract_answer(content: list[dict]) -> str:
    """取 text 块（跳过 thinking 块）。"""
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    return "\n".join(t for t in texts if t).strip()


def understand_image(src, prompt: str, model: str | None = None,
                     api_key: str | None = None, endpoint: str | None = None,
                     max_tokens: int = 8000) -> dict:
    """Send an image + question to the vision model.

    Returns {ok, answer, model, cost, error}.
    """
    cfg = _config()
    ep = endpoint or cfg["endpoint"]
    md = model or cfg["model"]
    key = api_key or cfg["api_key"]
    if not key:
        return {"ok": False,
                "error": "未配置 AI API key（config.json 的 ai_api_key 或环境变量 DSH_AI_API_KEY）",
                "model": md}
    img_bytes = _read_image(src)
    b64 = base64.b64encode(img_bytes).decode("ascii")

    body = json.dumps({
        "model": md,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},  # thinking 模型思考极慢（图片 100s+），禁用后 4s
        "messages": [{"role": "user", "content": [
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": prompt},
        ]}],
    }).encode("utf-8")
    req = urllib.request.Request(ep, data=body, headers={
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
        **UA,
    })
    try:
        r = urllib.request.urlopen(req, timeout=120)
        d = json.loads(r.read().decode("utf-8", "replace"))
        answer = _extract_answer(d.get("content", []))
        if not answer:
            return {"ok": False, "error": "empty answer (stop=" + str(d.get("stop_reason")) + ")",
                    "model": md}
        return {"ok": True, "answer": answer, "model": md,
                "cost": d.get("cost", "")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read()[:200]}", "model": md}
    except Exception as e:
        return {"ok": False, "error": str(e), "model": md}


_SCREEN_PROMPT = """这是明日方舟游戏的横屏截图（1280x720）。
判断当前画面，简洁回答：
1. 界面类型（主界面/关卡选择/战斗/结算/弹窗/肉鸽相关/其他）
2. 关键文字内容（如有）
3. 是否存在异常（卡住/报错/未知弹窗）
用中文回答，60 字以内。"""


def understand_screen(src, prompt: str = "") -> dict:
    """游戏画面理解（未知界面兜底用）。"""
    return understand_image(src, prompt or _SCREEN_PROMPT)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python services/vision.py <图片路径> [自定义问题]")
        sys.exit(1)
    img = sys.argv[1]
    q = sys.argv[2] if len(sys.argv) > 2 else _SCREEN_PROMPT
    print(json.dumps(understand_image(img, q), ensure_ascii=False, indent=2))
