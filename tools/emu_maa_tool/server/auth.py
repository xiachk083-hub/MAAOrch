"""x-agent-token 校验（FastAPI 依赖）。token 为空 = 本机模式不鉴权。"""
from __future__ import annotations
import hmac
from fastapi import Header, HTTPException

from .config import get_config


def verify_token(x_agent_token: str | None = Header(default=None)) -> None:
    cfg = get_config()
    token = cfg.get("token") or ""
    if not token:
        return  # 本机模式，不鉴权
    if not x_agent_token or not hmac.compare_digest(x_agent_token, token):
        raise HTTPException(status_code=401, detail="无效的 token")
