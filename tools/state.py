"""账号状态层：每个账号「现在在哪一步、跑过什么、结果如何」。

设计要点（2026-09-16 用户定）：
  · 六阶段固定枚举：未开始 / 匹配中 / 执行中 / 已完成 / 失败 / 异常
  · **原子写**：先写 .tmp 再 os.replace —— 写到一半崩溃不损坏文件（今晚已吃过非原子写的亏）
  · 中断可续跑：get_pending_accounts() 跳过「已完成」，失败且 attempts<3 的才重试
"""
import json, os, pathlib, datetime

PROJ = pathlib.Path(__file__).resolve().parent.parent
STATE = PROJ / "data" / "accounts_state.json"
STAGES = ("未开始", "匹配中", "执行中", "已完成", "失败", "异常")
MAX_ATTEMPTS = 3


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}                      # 文件损坏 → 当空处理，不静默丢已完成的判断由调用方记录


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)             # 原子：要么旧文件完整，要么新文件完整


def update_state(account_id: str, **kw) -> dict:
    if "阶段" in kw and kw["阶段"] not in STAGES:
        raise ValueError(f"未知阶段 {kw['阶段']}，允许: {STAGES}")
    s = load_state()
    cur = s.get(account_id, {})
    if kw.get("阶段") == "失败":
        kw.setdefault("attempts", int(cur.get("attempts", 0)) + 1)
    s[account_id] = {**cur, **kw, "updated": _now()}
    save_state(s)
    return s[account_id]


def get_state(account_id: str) -> dict:
    return load_state().get(account_id, {})


def get_pending_accounts(account_ids: list[str]) -> list[str]:
    """需要跑的账号：未开始 / 异常 / 失败且未超重试上限。已完成的一律跳过（中断可续跑）。"""
    s = load_state()
    out = []
    for a in account_ids:
        st = s.get(a, {}).get("阶段", "未开始")
        if st in ("未开始", "异常"):
            out.append(a)
        elif st == "失败" and int(s[a].get("attempts", 0)) < MAX_ATTEMPTS:
            out.append(a)
    return out


def get_summary() -> dict:
    s = load_state()
    cnt = {k: 0 for k in STAGES}
    for v in s.values():
        st = v.get("阶段", "未开始")
        cnt[st] = cnt.get(st, 0) + 1
    fails = [k for k, v in s.items() if v.get("阶段") == "失败"]
    return {"总数": len(s), **cnt, "失败列表": fails}


if __name__ == "__main__":
    # smoke 用临时状态文件：不碰真实 data/accounts_state.json，且**可重复运行**（幂等）
    import tempfile
    STATE = pathlib.Path(tempfile.mkdtemp()) / "accounts_state.json"
    update_state("test_acc", 阶段="匹配中")
    assert get_state("test_acc")["阶段"] == "匹配中", "回读失败"
    update_state("test_acc", 阶段="失败", 结果="fail", missing=["煌"], failed_at="T+152", location="right")
    assert get_state("test_acc")["attempts"] == 1, "失败应自动累加 attempts"
    update_state("done_acc", 阶段="已完成", 结果="三星")
    # 未见过的账号 → 阶段默认「未开始」→ **应该待跑**（修正断言：原来误期望它跳过）
    assert get_pending_accounts(["test_acc", "new_acc"]) == ["test_acc", "new_acc"]
    assert get_pending_accounts(["test_acc", "done_acc"]) == ["test_acc"], "失败未超限应待跑、已完成应跳过"
    update_state("test_acc", 阶段="已完成", 结果="三星")
    assert get_pending_accounts(["test_acc"]) == [], "已完成必须跳过（中断可续跑）"
    # 原子写：文件必须是完整 JSON
    json.loads(STATE.read_text(encoding="utf-8"))
    print("smoke ✓ | summary:", get_summary())
