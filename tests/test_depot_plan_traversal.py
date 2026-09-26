# -*- coding: utf-8 -*-
"""回归测试：/api/depot/plan 的 account_id 路径穿越（2026-09-26）。

背景
----
独立验证（docs/audits/2026-09-26_verdict_traversal.md）发现：前端退役只删掉了那条
GET /api/screenshots/file/.../{fname:path} 的 P0 路由，**同族的路径拼接仍然活着**——
本路由把 query 参数 account_id 直接拼进 Path 且只做 exists()：

    p = ... / "models" / "accounts" / account_id / "plan.json"   # 修复前
    account_id="../../.."  ->  D:/1-2/plan.json                  # 实测可读到仓库外

修复：拒绝分隔符/NUL/相对段，并 resolve() 后断言仍落在 accounts 目录内
（resolve 会解掉符号链接，故同时挡住软链逃逸）。

运行：python -m pytest tests/test_depot_plan_traversal.py -q
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _StubMW:
    """create_app 只需要 .config / ._log；MCP 挂载关掉以避免冷启动。api_token 留空便于直接调。"""

    def __init__(self) -> None:
        self.config = {"api_token": "", "mcp_mount_enabled": False}

    def _log(self, *a, **k) -> None:
        pass


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from network.api_fastapi import create_app

    with TestClient(create_app(_StubMW())) as c:
        yield c


# 覆盖：纯相对段、夹带分隔符、反斜杠、NUL、以及嵌套的 ../
TRAVERSAL_INPUTS = [
    "..",
    "../..",
    "../../..",
    "../../../../etc",
    "a/../..",
    "models/../..",
    "..\\..",
    "....//",
    "\x00",
]


@pytest.mark.parametrize("bad", TRAVERSAL_INPUTS)
def test_traversal_rejected(client, bad):
    r = client.get("/api/depot/plan", params={"account_id": bad})
    assert r.status_code == 400, (bad, r.status_code)
    # 绝不能返回文件内容
    assert "SENTINEL" not in r.text
    assert "plan.json" not in r.text or "非法" in r.text


def test_missing_account_id_is_400(client):
    assert client.get("/api/depot/plan").status_code == 400


def test_unknown_account_is_404_not_500(client):
    r = client.get("/api/depot/plan", params={"account_id": "no_such_account_zzz"})
    assert r.status_code == 404


def test_handler_keeps_containment_guard():
    """源码级断言：防止有人把修复退回成朴素的路径拼接。"""
    src = (ROOT / "network" / "api_fastapi.py").read_text(encoding="utf-8")
    start = src.index('@app.get("/api/depot/plan")')
    block = src[start:start + 1800]
    assert "resolve()" in block, "缺少 resolve() 规范化"
    assert "accounts_dir" in block, "缺少落点目录断言"
    assert "account_id 非法" in block, "缺少非法入参拒绝"


def test_lexical_join_would_have_escaped():
    """把当年的漏洞向量固定成事实：朴素拼接会解析到 accounts 目录之外。"""
    accounts = (ROOT / "models" / "accounts").resolve()
    escaped = (accounts / "../../.." / "plan.json").resolve()
    assert accounts not in escaped.parents, "向量本身应指向目录外（否则本测试失去意义）"
