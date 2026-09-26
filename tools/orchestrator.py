"""编排器（20 行量级）：串起 状态层 + 匹配层 + 执行层。
先跑单账号；多账号并行等真跑过一轮再加（2026-09-16 用户定：再写是猜，跑完是数据）。
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools.state import get_pending_accounts, update_state
from tools.match_candidates import build_box_index, rank_candidates
from tools.batch_index import load_box
from tools.match_candidates import load_jobs

STAGE = "act16side_ex02"
ACCOUNTS = [{"id": "emu2", "box_path": "data/operbox_batch/emu2.json"}]


def match(acc):
    box = build_box_index(load_box(acc["box_path"]))
    return rank_candidates(box, load_jobs())


def ensure_detail_page() -> bool:
    """前置：确认在关卡详情页（不在就交给 _launch.py 的导航链处理）。"""
    import tools.screen_state as SS
    from services.ai_agent.percept import AdbScreen
    st = SS.state_of(AdbScreen("2").capture())[0]
    return st in ("detail", "form")


def main():
    for acc in ACCOUNTS:
        if acc["id"] not in get_pending_accounts([a["id"] for a in ACCOUNTS]):
            continue
        update_state(acc["id"], 阶段="匹配中")
        cands = [c for c in match(acc) if c["match"] in ("exact", "partial")]
        if not cands:
            update_state(acc["id"], 阶段="失败", 原因="无候选"); continue
        job = cands[0]
        update_state(acc["id"], 阶段="执行中", job_id=job["job_id"], 缺员=job["missing"])
        ok = ensure_detail_page()
        update_state(acc["id"], 详情页=ok, 说明="执行层待接 MAA（先手动跑一局拿数据）")
        print(f"{acc['id']}: 命中 {job['job_id']}（缺{len(job['missing'])}）| 在详情页={ok}")


if __name__ == "__main__":
    main()
