"""文本 LLM 客户端 — 给 agent 的"慢思考"用（OpenAI 兼容协议）。

为什么是文本而不是多模态（2026-09-14 实测）:
  手头这把 key 指向的模型是**纯文本**的（deepseek-v4-pro 被喂图后明确回
  "看不到图片"）。但这不影响设计 —— 我们本来就不想让模型输出像素坐标
  （不可靠），坐标一律由本地 CV 落定（OCR 框/模板命中/格子投影）。
  所以正确的形态是: **本地把画面转成结构化文字**（brain.describe_frame）
  → 交给 LLM 决策"下一步做什么" → 由 actuator 执行并验证。
  仓库里的 tools/vision_translate.py 早就在用这个思路（四通道转译给无视觉模型）。

配置字段（models/config.json）:
  ai_endpoint / ai_model / ai_api_key / ai_protocol(openai|anthropic)

踩过的坑:
  · 推理型模型会把 token 花在 reasoning 上 —— max_tokens 给小了会返回**空
    content**（deepseek-flash 用 200 tokens 时就是这样）。默认给 2000，并且
    空 content 不算成功，要把 reasoning 摘出来当失败证据。
  · 不静默降级: 未配置 key 就明确返回 ok=False + 原因（vision.py 的既有原则）。
"""
from __future__ import annotations
import os

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from services.ai_agent.percept import log   # 统一日志（percept 不反向依赖本模块）

PROJ = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = PROJ / "models" / "config.json"


def load_ai_config() -> dict:
    cfg: dict = {}
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    return {
        "endpoint": (cfg.get("ai_endpoint") or "").strip(),
        "model": (cfg.get("ai_model") or "").strip(),
        # 视觉模型单独一项：多模态与推理模型的选型不同（2026-09-14 实测:
        # deepseek-v4-pro 看不见图、deepseek-flash 看得见）。
        # 没配就退回主模型，行为与以前一致。
        "vision_model": (cfg.get("ai_vision_model") or cfg.get("ai_model") or "").strip(),
        "api_key": (cfg.get("ai_api_key") or "").strip(),
        "protocol": (cfg.get("ai_protocol") or "openai").strip().lower(),
    }


# ── 视觉调用预算（2026-09-16 实测：读一帧 ≈14k token，reasoning 占 90%）──
# 主循环只在决策点/定期校准调视觉，其余走规则引擎；超预算直接拒绝，不静默烧 token。
VISION_MAX_CALLS = int(os.environ.get("VISION_MAX_CALLS", "10"))
_vision_used = 0


def vision_budget_left() -> int:
    return max(0, VISION_MAX_CALLS - _vision_used)


def vision_reset() -> None:
    """每局开始调一次，重置预算。"""
    global _vision_used
    _vision_used = 0


def _vision_take() -> bool:
    global _vision_used
    if _vision_used >= VISION_MAX_CALLS:
        return False
    _vision_used += 1
    return True


def _encode_image(image) -> str | None:
    """把 ndarray / 路径 / bytes 编成 data URL（JPEG，长边限到 1280 省 token）。"""
    import base64
    import numpy as np
    try:
        if isinstance(image, (str, Path)):
            from services.ai_agent.percept import imread
            img = imread(image)
        elif isinstance(image, bytes):
            import cv2
            img = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        else:
            img = image
        if img is None:
            return None
        import cv2
        h, w = img.shape[:2]
        if max(h, w) > 1280:
            r = 1280.0 / max(h, w)
            img = cv2.resize(img, (int(w * r), int(h * r)))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
    except Exception as e:
        log(f"图片编码失败: {e}")
        return None


class LLM:
    """OpenAI /chat/completions 的薄封装（含 JSON 契约）。"""

    def __init__(self, config: dict | None = None, timeout: float = 180.0,
                 max_tokens: int = 4000) -> None:
        self.cfg = config or load_ai_config()
        self.timeout = timeout
        self.max_tokens = max_tokens

    @property
    def ready(self) -> bool:
        return bool(self.cfg.get("api_key") and self.cfg.get("endpoint") and self.cfg.get("model"))

    @property
    def vision_ready(self) -> bool:
        return bool(self.cfg.get("api_key") and self.cfg.get("endpoint")
                    and self.cfg.get("vision_model"))

    def chat(self, system: str, user: str, temperature: float = 0.2,
             max_tokens: int | None = None, image=None, vision: bool = False) -> dict:
        """返回 {ok, text, error, raw, usage}；失败不抛异常。

        推理型模型（deepseek-v4-pro 这类）常把 token 全花在 reasoning 上，
        导致 content 为空 —— 实测每十几次就会碰一次。这里对"空内容"自动
        加倍预算重试一次，而不是把空回答当成"模型没想法"。
        """
        budget = max_tokens or self.max_tokens
        # 视觉调用给足预算：带 reasoning 的模型会先烧 1~2k token 思考（2026-09-16 实测 2000/4000 均被吃光返回空）
        if image is not None and not _vision_take():
            return {"ok": False, "error": f"视觉调用超预算（上限 {VISION_MAX_CALLS}/局，已用 {_vision_used}）——改用规则引擎或 vision_reset()"}
        r = self._chat_once(system, user, temperature, (16000 if vision else budget), image=image, vision=vision)
        if not r.get("ok") and "空内容" in (r.get("error") or ""):
            log(f"LLM 空内容（reasoning 吃光 {budget} token），用 {budget * 2} 重试")
            r = self._chat_once(system, user, temperature, max(budget * 2, 32000), image=image, vision=vision)
        return r

    def _chat_once(self, system: str, user: str, temperature: float,
                   max_tokens: int, image=None, vision: bool = False) -> dict:
        if not self.ready:
            return {"ok": False, "error": "未配置 AI 通道（models/config.json 的 ai_endpoint/ai_model/ai_api_key）"}
        model = self.cfg["vision_model"] if vision else self.cfg["model"]
        if image is None:
            content = user
        else:
            url = _encode_image(image)
            if url is None:
                return {"ok": False, "error": "图片编码失败"}
            content = [{"type": "text", "text": user},
                       {"type": "image_url", "image_url": {"url": url}}]
        body = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": content}],
        }
        req = urllib.request.Request(
            self.cfg["endpoint"], data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.cfg['api_key']}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            return {"ok": False, "error": f"HTTP {e.code}: {detail}"}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        try:
            msg = d["choices"][0]["message"]
            text = (msg.get("content") or "").strip()
            reasoning = (msg.get("reasoning_content") or "").strip()
        except Exception as e:
            return {"ok": False, "error": f"响应结构异常: {e}", "raw": d}
        if not text:
            # 推理型模型的空 content: 把 reasoning 当失败证据返回（别当成"想通了"）
            return {"ok": False, "error": "模型返回空内容（可能是 max_tokens 不够/全花在 reasoning）",
                    "raw": d, "reasoning": reasoning[:800]}
        return {"ok": True, "text": text, "usage": d.get("usage"), "raw": d}

    def ask_json(self, system: str, user: str, temperature: float = 0.2,
                 max_tokens: int | None = None, image=None, vision: bool = False) -> dict:
        """要求模型回 JSON，返回 {"ok": bool, "json": dict|None, "text": …, "error": …}。

        解析策略: 先剥 ``` 围栏，再取第一个平衡的 {...}（模型常带前后说明文字）。
        """
        r = self.chat(system, user, temperature=temperature, max_tokens=max_tokens,
                      image=image, vision=vision)
        if not r.get("ok"):
            return {"ok": False, "json": None, "error": r.get("error", ""), "raw": r.get("raw")}
        text = r["text"]
        data = _extract_json(text)
        if data is None:
            return {"ok": False, "json": None, "text": text,
                    "error": "返回内容不是合法 JSON"}
        return {"ok": True, "json": data, "text": text, "usage": r.get("usage")}


def _extract_json(text: str) -> dict | None:
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(t[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except Exception:
                    return None
    return None
