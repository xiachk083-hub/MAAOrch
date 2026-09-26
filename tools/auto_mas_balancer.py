#!/usr/bin/env python3
"""AUTO-MAS 自动均分工具：把启用账号（Status=true）均匀分配到 N 个队列并行。

账号筛选口径（用户 2026-08-18 确认）:
    只用 Status —— 用户配置 Status=true 纳入均分，false 自动排除（官-25/l-4/l-6 即如此）。
    Status 以目标机 ScriptConfig.json 文件为准（AUTO-MAS 内存与文件有不同步的情况）。
    新增账号（Status 默认 true）自动纳入；停用/删除自动排除。

用法:
    python tools/auto_mas_balancer.py                      # 均分到 9 队列，只排不启动
    python tools/auto_mas_balancer.py --queues 8           # 均分到 8 队列
    python tools/auto_mas_balancer.py --queues 9 --start   # 均分 + 启动全部队列
    python tools/auto_mas_balancer.py --dry-run            # 只打印分配方案，不改任何配置
    python tools/auto_mas_balancer.py --host <addr>        # 指定 AUTO-MAS API 地址
"""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import sys
import time
import urllib.request

CRED = pathlib.Path.home() / ".maorch_deploy.json"
MANAGER = "http://100.79.173.69:19998"


def api(host: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    """调 AUTO-MAS API（36163，无鉴权）。"""
    req = urllib.request.Request(
        host + path,
        data=json.dumps(body if body is not None else {}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def exec_remote(cmd: str, timeout: int = 180) -> dict:
    """manager /api/exec 通道（19998，PowerShell）。"""
    cred = json.loads(CRED.read_text(encoding="utf-8"))
    req = urllib.request.Request(
        MANAGER + "/api/exec",
        data=json.dumps({"command": cmd}).encode(),
        headers={"x-manager-token": cred.get("manager_token", ""),
                 "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"error": str(e)[:200]}


def run_target_py(script: str, timeout: int = 120) -> str:
    """目标机跑 python（base64 内嵌），返回 stdout。"""
    b64 = base64.b64encode(script.encode()).decode()
    r = exec_remote(f'python -c "import base64;exec(base64.b64decode(\'{b64}\').decode())"', timeout)
    return r.get("stdout") or r.get("stderr") or r.get("error") or ""


# ── 账号读取与筛选 ──────────────────────────────────────────────

def get_scripts(host: str) -> dict:
    """AUTO-MAS 全部脚本: {uid: {Info, Emulator, Run}}（API 内存态）。"""
    sc = api(host, "/api/scripts/get", {"scriptId": None})
    return {it["uid"]: sc["data"][it["uid"]]
            for it in sc.get("index", []) if it.get("type") == "MaaConfig"}


def get_enabled_accounts() -> list[tuple[str, str]]:
    """读目标机 ScriptConfig.json，返回启用账号 [(名称, 脚本uid)]（按名称排序）。

    Status 以文件为准（AUTO-MAS 内存与文件可能不同步）。
    目标机只输出精简结果（避免 exec stdout 8000 字符截断大文件）。
    """
    s = (
        "import sys,json\n"
        "sys.stdout.reconfigure(encoding='utf-8',errors='replace')\n"
        "sc=json.load(open('E:/AUTO-MAS/config/ScriptConfig.json',encoding='utf-8'))\n"
        "out=[]\n"
        "for uid,v in sc.items():\n"
        "    if uid=='instances' or not isinstance(v,dict): continue\n"
        "    ud=v.get('SubConfigsInfo',{}).get('UserData',{})\n"
        "    st=None\n"
        "    for u_uid,u in ud.items():\n"
        "        if u_uid=='instances': continue\n"
        "        st=u.get('Info',{}).get('Status')\n"
        "    if st is not False:\n"
        "        out.append([v.get('Info',{}).get('Name','?'),uid])\n"
        "print(json.dumps(out))"
    )
    out = run_target_py(s)
    try:
        pairs = json.loads(out)
    except Exception:
        sys.exit(f"[错误] 解析启用账号失败: {out[:200]}")
    enabled = [(nm, sid) for nm, sid in pairs]
    enabled.sort()
    return enabled


def snake_assign(items: list[tuple[str, str]], n: int) -> dict[int, list[str]]:
    """蛇形均分：账号按序轮流放入队列 1..n, n..1, 1..n…（慢号天然分散）。"""
    groups: dict[int, list[str]] = {i + 1: [] for i in range(n)}
    for i, (nm, sid) in enumerate(items):
        row, col = i // n, i % n
        q = col + 1 if row % 2 == 0 else n - col
        groups[q].append(sid)
    return groups


# ── 队列管理 ────────────────────────────────────────────────────

def get_queue_ids(host: str) -> list[str]:
    """现有队列 uid 列表（index 顺序 = 创建顺序，对应 MAA_1..N）。"""
    q = api(host, "/api/queue/get", {"queueId": None})
    return [it["uid"] for it in q.get("index", []) if it.get("type") == "QueueConfig"]


def ensure_queues(host: str, n: int) -> list[str]:
    """确保至少有 n 个队列，不足则 queue/add 补建。返回前 n 个 uid。"""
    qids = get_queue_ids(host)
    while len(qids) < n:
        r = api(host, "/api/queue/add", {})
        if r.get("code") != 200:
            sys.exit(f"[错误] 补建队列失败: {r.get('message', r)}")
        qids.append(r["queueId"])
        print(f"[队列] 补建队列 {len(qids)} 成功")
    return qids[:n]


def queue_item_uids() -> dict[str, list[str]]:
    """读目标机 QueueConfig.json 拿每队列的项 uid（queue/get API 不返回项）。"""
    s = (
        "import sys,json\n"
        "sys.stdout.reconfigure(encoding='utf-8',errors='replace')\n"
        "q=json.load(open('E:/AUTO-MAS/config/QueueConfig.json',encoding='utf-8'))\n"
        "out={}\n"
        "for qid,qv in q.items():\n"
        "    if qid=='instances' or not isinstance(qv,dict): continue\n"
        "    qi=qv.get('SubConfigsInfo',{}).get('QueueItem',{})\n"
        "    if isinstance(qi,dict):\n"
        "        out[qid]=[it['uid'] for it in qi.get('instances',[])]\n"
        "print(json.dumps(out))"
    )
    out = run_target_py(s)
    try:
        return json.loads(out)
    except Exception:
        sys.exit(f"[错误] 解析队列项失败: {out[:200]}")


# ── 配置动作 ────────────────────────────────────────────────────

def stop_all_and_wait(host: str) -> None:
    """stop ALL 停所有任务 + 轮询 MAA/模拟器归零。"""
    print("[停止] 停所有任务…")
    try:
        req = urllib.request.Request(
            host + "/api/dispatch/stop",
            data=json.dumps({"taskId": "ALL"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=150) as r:
            print("  ", r.read().decode("utf-8", "replace")[:120])
    except Exception as e:
        print("  stop 超时（动作继续）:", str(e)[:60])
    for i in range(10):
        time.sleep(20)
        r1 = exec_remote('powershell -NoProfile -Command "(Get-Process MAA -ErrorAction SilentlyContinue | Measure-Object).Count"')
        r2 = exec_remote('powershell -NoProfile -Command "(Get-Process MuMuVMMHeadless -ErrorAction SilentlyContinue | Measure-Object).Count"')
        m = (r1.get("stdout", "") or "?").strip()
        v = (r2.get("stdout", "") or "?").strip()
        print(f"  等{20 * (i + 1)}s: MAA={m} VMM={v}")
        if m == "0" and v == "0":
            print("[停止] 全部停止")
            return
    print("[警告] 停止超时，仍有 MAA/模拟器进程，继续配置（可能不完整）")


def set_script_path(host: str, sid: str, maa_dir: str) -> bool:
    """改脚本 Info.Path（scripts/update，传完整对象）。"""
    d = api(host, "/api/scripts/get", {"scriptId": sid})
    data = d.get("data", {}).get(sid)
    if not data:
        return False
    data["Info"]["Path"] = maa_dir
    r = api(host, "/api/scripts/update", {"scriptId": sid, "data": data})
    return r.get("code") == 200


def clear_queue(host: str, qid: str) -> int:
    """清空队列项（读文件拿 uid → item/delete）。"""
    items = queue_item_uids()
    uids = items.get(qid, [])
    n = 0
    for iuid in uids:
        r = api(host, "/api/queue/item/delete", {"queueId": qid, "queueItemId": iuid})
        if r.get("code") == 200:
            n += 1
        time.sleep(0.05)
    return n


def add_queue_items(host: str, qid: str, sids: list[str]) -> bool:
    """队列加项（item/add + item/update 设置 ScriptId）。"""
    for sid in sids:
        r1 = api(host, "/api/queue/item/add", {"queueId": qid})
        iid = r1.get("queueItemId")
        if not iid:
            return False
        r2 = api(host, "/api/queue/item/update",
                 {"queueId": qid, "queueItemId": iid, "data": {"Info": {"ScriptId": sid}}})
        if r2.get("code") != 200:
            return False
        time.sleep(0.05)
    return True


# ── 主流程 ──────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="AUTO-MAS 自动均分队列（启用账号 → N 队列）")
    ap.add_argument("--host", default="http://100.79.173.69:36163", help="AUTO-MAS API 地址")
    ap.add_argument("--queues", type=int, default=9, help="队列数（默认 9）")
    ap.add_argument("--start", action="store_true", help="配置完成后启动全部队列")
    ap.add_argument("--dry-run", action="store_true", help="只打印分配方案，不写任何配置")
    args = ap.parse_args()
    host = args.host.rstrip("/")
    n = args.queues

    # 1) 读取与筛选
    print("== 读取启用账号 ==")
    accounts = get_enabled_accounts()
    if not accounts:
        sys.exit("[错误] 未读取到任何启用账号")
    print(f"启用账号 {len(accounts)} 个（Status=true，停用自动排除）")

    # 2) 蛇形均分
    groups = snake_assign(accounts, n)
    print(f"\n== 均分方案（{n} 队列）==")
    name_by_sid = {sid: nm.replace("明日方舟-", "") for nm, sid in accounts}
    for q in range(1, n + 1):
        print(f"  队列{q} ({len(groups[q])}): {[name_by_sid.get(s, '?') for s in groups[q]]}")

    if args.dry_run:
        print("\n[dry-run] 仅预览，未做任何修改")
        return

    # 3) 停旧任务
    stop_all_and_wait(host)

    # 4) 改脚本 Path
    print("\n== 改脚本 MAA 副本指向 ==")
    fails = []
    for q, sids in groups.items():
        maa_dir = f"E:\\MAA-Multi\\MAA_{q}"
        for sid in sids:
            if not set_script_path(host, sid, maa_dir):
                fails.append(name_by_sid.get(sid, sid[:8]))
            time.sleep(0.05)
    if fails:
        sys.exit(f"[错误] 改 Path 失败: {fails}")
    print(f"  全部 {len(accounts)} 个脚本 Path 已指向 MAA_1~{n}")

    # 5) 重建队列（清旧项 + 加新项；队列数不足自动补建）
    print("\n== 重建队列 ==")
    qids = ensure_queues(host, n)
    for q, qid in enumerate(qids, 1):
        cleared = clear_queue(host, qid)
        if not add_queue_items(host, qid, groups[q]):
            sys.exit(f"[错误] 队列{q} 加项失败")
        print(f"  队列{q}: 清 {cleared} 项，加 {len(groups[q])} 项")
        time.sleep(0.1)

    # 6) 启动（可选）
    if args.start:
        print("\n== 启动全部队列 ==")
        for q, qid in enumerate(qids, 1):
            r = api(host, "/api/dispatch/start", {"taskId": qid, "mode": "AutoProxy"})
            ok = r.get("code") == 200
            print(f"  队列{q}: {'已启动' if ok else '失败 ' + str(r.get('message'))[:80]}")
            time.sleep(0.5)

    print("\n完成。分配表见上方；未传 --start 时请到 AUTO-MAS 调度中心自行启动。")


if __name__ == "__main__":
    main()
