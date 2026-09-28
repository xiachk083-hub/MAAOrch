# -*- coding: utf-8 -*-
"""AUTO-MAS 账号启用/停用工具（锁感知）。

背景（2026-09-15 实测）：
  AUTO-MAS 的脚本配置在任务 prepare() 时被整体上锁（app/models/ConfigBase.py
  `is_locked`），任务结束才解锁 —— 期间任何 `scripts/user/update` 都会被拒：
  `ValueError: 配置已锁定, 无法修改`（HTTP 200 + body code=500）。
  一个脚本 = 一台模拟器 + N 个用户（MaaUserConfig）串行跑，一次任务可能 3 小时
  （官-1-1-7 = 柠檬汽水/汐影月k/潮响/tbNick_2l3vf/him 五个号轮着登）。
  所以「停用某账号」不能立刻生效，必须等锁释放。

本工具做的事：
  1. 按名字（或 Id / uid）定位用户，打印它属于哪个脚本、当前 Status；
  2. 等待写窗口：E:\\MAA-Multi\\MAA_*.exe 不在运行，且 API 写入被接受；
  3. 写 `Info.Status`（整对象回写，避免键级合并语义差异）；
  4. 三重校验：API 复读、ScriptConfig.json 落盘值、36163 仍存活；
  5. 追加审计到 logs/auto_mas_toggle.jsonl。

用法：
  python tools/auto_mas_toggle_user.py --name 柠檬汽水 --dry-run
  python tools/auto_mas_toggle_user.py --name 柠檬汽水            # 停用
  python tools/auto_mas_toggle_user.py --name 柠檬汽水 --wait-min 240
  python tools/auto_mas_toggle_user.py --name 柠檬汽水 --enable    # 反向启用

写窗口两级：默认等「所有 MAA 副本空闲」（保守，避免在批次运行中改配置）；
等满 --relax-after-min 仍没有空闲窗口时降级为「该脚本锁一放开就写」。
"""
import argparse
import base64
import datetime as dt
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

API = "http://100.79.173.69:36163"
SSH_KEY = str(Path.home() / ".ssh" / "maaorch_target")
SSH_HOST = "Administrator@100.79.173.69"
SCRIPT_CONFIG = r"E:\AUTO-MAS\config\ScriptConfig.json"
AUDIT = Path(__file__).resolve().parent.parent / "logs" / "auto_mas_toggle.jsonl"


def log(msg: str) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def api(path: str, body: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ssh_ps(script: str, timeout: int = 60) -> str:
    """在目标机跑 PowerShell（UTF-16LE base64，规避中文被 GBK 破坏）。"""
    b64 = base64.b64encode(script.encode("utf-16-le")).decode()
    p = subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
         SSH_HOST, f"chcp 65001 >nul & powershell -NoProfile -EncodedCommand {b64}"],
        capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
    )
    return p.stdout.decode("utf-8", "replace")


def maa_running() -> bool:
    """AUTO-MAS 的 MAA 副本是否在跑（= 有任务在中途，避免此刻改配置）。"""
    out = ssh_ps(
        "Get-CimInstance Win32_Process -Filter \"Name='MAA.exe'\" | "
        "Where-Object { $_.CommandLine -like '*MAA-Multi*' } | "
        "ForEach-Object { $_.ProcessId }"
    )
    return bool([l for l in out.splitlines() if l.strip().isdigit()])


def find_users(needle: str) -> list[dict]:
    """按名字/Id/uid 精确匹配，跨全部脚本找用户。"""
    scripts = api("/api/scripts/get", {"scriptId": None})["data"]
    hits = []
    for sid, sv in scripts.items():
        d = (api("/api/scripts/user/get", {"scriptId": sid, "userId": None}).get("data") or {})
        for uid, u in d.items():
            info = u.get("Info", {})
            if needle in (info.get("Name"), info.get("Id"), uid):
                hits.append({
                    "sid": sid, "script": sv["Info"].get("Name"), "emu": sv["Emulator"].get("Index"),
                    "uid": uid, "info": info,
                })
    return hits


def read_disk_status(sid: str, uid: str) -> dict:
    """读目标机 ScriptConfig.json 里该用户的 Status（唯一真相源）。"""
    out = ssh_ps(
        f"$c = Get-Content '{SCRIPT_CONFIG}' -Raw -Encoding UTF8 | ConvertFrom-Json; "
        f"$u = $c.'{sid}'.SubConfigsInfo.UserData.'{uid}'; "
        "if ($u) { $u.Info.Status } else { 'MISSING' }"
    )
    for line in out.splitlines():
        s = line.strip()
        if s in ("True", "False", "MISSING"):
            return {"status": {"True": True, "False": False}.get(s), "raw": s}
    return {"status": None, "raw": out.strip()[:120]}


def write_status(sid: str, uid: str, want: bool) -> tuple[bool, str]:
    """回写完整 Info + 改写 Status。返回 (是否成功, 说明)。"""
    cur = api("/api/scripts/user/get", {"scriptId": sid, "userId": uid})["data"][uid]
    info = dict(cur["Info"])
    if info.get("Status") is want:
        return True, "already"
    info["Status"] = want
    r = api("/api/scripts/user/update", {"scriptId": sid, "userId": uid, "data": {"Info": info}})
    if r.get("code") == 200:
        return True, "ok"
    return False, str(r.get("message") or r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="用户名 / 脱敏Id / uid（精确匹配）")
    ap.add_argument("--enable", action="store_true", help="反向：启用（默认停用）")
    ap.add_argument("--wait-min", type=int, default=0, help="最多等多少分钟（0=只试一次）")
    ap.add_argument("--poll-sec", type=int, default=60)
    ap.add_argument("--relax-after-min", type=int, default=60,
                    help="等这么久仍没有全空闲窗口时，降级为「锁一放开就写」（默认 60）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-mma-check", action="store_true", help="不等 MAA 空闲，锁一放就写")
    args = ap.parse_args()

    want = bool(args.enable)
    log(f"目标 {args.name!r} → Status={want}（{'启用' if want else '停用'}）")

    hits = find_users(args.name)
    if not hits:
        log("✗ 没找到匹配用户")
        return 1
    for h in hits:
        log(f"  命中: 脚本 {h['script']} (emu #{h['emu']}) 用户 {h['uid']} "
            f"name={h['info'].get('Name')} Id={h['info'].get('Id')} "
            f"当前 Status={h['info'].get('Status')}")
    if len(hits) > 1:
        log("✗ 命中多个，请用 uid 精确定位")
        return 1

    h = hits[0]
    sid, uid = h["sid"], h["uid"]
    if h["info"].get("Status") is want:
        log(f"= 已经是 {want}，无需改动")
        return 0
    if args.dry_run:
        log("dry-run：未写入")
        return 0

    deadline = time.time() + args.wait_min * 60
    relax_at = time.time() + args.relax_after_min * 60
    attempt = 0
    relaxed = False
    while True:
        attempt += 1
        if not relaxed and time.time() >= relax_at and not args.skip_mma_check:
            relaxed = True
            log(f"已等满 {args.relax_after_min} 分钟仍未全空闲 → 降级：锁一放开就写")
        busy = (not args.skip_mma_check) and not relaxed and maa_running()
        if busy:
            log(f"#{attempt} MAA 副本在跑（任务进行中），等待…")
        else:
            ok, why = write_status(sid, uid, want)
            if ok:
                log(f"#{attempt} 写入成功（{why}）")
                break
            log(f"#{attempt} 写入被拒：{why}" + ("（该脚本任务仍在跑，锁未释放）" if "锁定" in why else ""))

        if time.time() >= deadline:
            log("✗ 超过等待上限，未写入")
            return 2
        time.sleep(args.poll_sec)

    # —— 校验 ——
    live = api("/api/scripts/user/get", {"scriptId": sid, "userId": uid})["data"][uid]["Info"]
    api_ok = live.get("Status") is want
    disk = {"status": None, "raw": "?"}
    for i in range(3):  # 落盘可能有异步延迟，重试确认
        time.sleep(10)
        disk = read_disk_status(sid, uid)
        if disk["status"] is want:
            break
        log(f"落盘复读第 {i + 1} 次仍为 {disk['raw']}，重试…")
    disk_ok = disk["status"] is want
    try:
        alive = api("/api/info/version", {}).get("code") == 200
    except Exception as e:  # noqa: BLE001
        alive = f"ERR {e}"

    log(f"校验：API 复读 Status={live.get('Status')} ({'✓' if api_ok else '✗'}) | "
        f"落盘 Status={disk['raw']} ({'✓' if disk_ok else '✗'}) | 36163={'✓' if alive is True else alive}")
    log(f"保留字段：Name={live.get('Name')} Stage={live.get('Stage')} "
        f"Annihilation={live.get('Annihilation')} Notes={live.get('Notes')}")

    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "script": h["script"], "sid": sid, "uid": uid, "name": live.get("Name"),
            "id": live.get("Id"), "set_status": want,
            "api_ok": api_ok, "disk_ok": disk_ok, "disk_raw": disk["raw"], "36163_alive": alive,
        }, ensure_ascii=False) + "\n")

    if api_ok and disk_ok:
        log("✓ 完成")
        return 0
    log("⚠ 已写入但校验不一致 —— 可能被运行中的任务回写覆盖；稍后复查")
    return 3


if __name__ == "__main__":
    sys.exit(main())
