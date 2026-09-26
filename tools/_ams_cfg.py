# -*- coding: utf-8 -*-
"""拉取/回写目标机 ScriptConfig.json（base64 走 SSH，规避编码与二进制破坏）。

  python tools/_ams_cfg.py pull  <本地路径>
  python tools/_ams_cfg.py push  <本地路径>     # 先自动备份为 .bak_<ts>
  python tools/_ams_cfg.py info  <本地路径>     # 只看指定 sid/uid 的关键字段
"""
import base64
import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

spec = importlib.util.spec_from_file_location(
    "tg", Path(__file__).resolve().parent / "auto_mas_toggle_user.py")
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)

REMOTE = "E:/AUTO-MAS/config/ScriptConfig.json"
NEW_SID = "b88585f3-d258-42f9-9175-583bbf8164bf"
NEW_UID = "5e8e4684-b697-440c-ad84-fc6484a04de7"


def ps(script: str, timeout: int = 300) -> str:
    b64 = base64.b64encode(script.encode("utf-16-le")).decode()
    p = subprocess.run(
        ["ssh", "-i", tg.SSH_KEY, "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=10", tg.SSH_HOST,
         f"chcp 65001 >nul & powershell -NoProfile -EncodedCommand {b64}"],
        capture_output=True, timeout=timeout,
    )
    return p.stdout.decode("utf-8", "replace")


def pull(local: Path) -> None:
    out = ps("[Convert]::ToBase64String([IO.File]::ReadAllBytes('%s'))" % REMOTE)
    b64 = "".join(c for c in out if c.isalnum() or c in "+/=")
    local.write_bytes(base64.b64decode(b64))
    print(f"拉取 {len(local.read_bytes())} 字节 → {local}")
    print("mtime:", ps(f"(Get-Item '{REMOTE}').LastWriteTime").strip())


def push(local: Path) -> None:
    """回写：base64 走 stdin（命令行上限 32KB，绝不能内联进 argv）。"""
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    print("备份:", ps(f"Copy-Item '{REMOTE}' '{REMOTE}.bak_tw2_{stamp}' -Force; "
                     f"(Get-Item '{REMOTE}.bak_tw2_{stamp}').Length").strip())
    b64 = base64.b64encode(local.read_bytes()).decode()
    inner = (
        "$s = [Console]::In.ReadToEnd(); "
        f"[IO.File]::WriteAllBytes('{REMOTE}', [Convert]::FromBase64String($s)); "
        f"(Get-Item '{REMOTE}').Length"
    )
    enc = base64.b64encode(inner.encode("utf-16-le")).decode()
    p = subprocess.run(
        ["ssh", "-i", tg.SSH_KEY, "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=10", tg.SSH_HOST,
         f"chcp 65001 >nul & powershell -NoProfile -EncodedCommand {enc}"],
        input=b64.encode(), capture_output=True, timeout=300,
    )
    print("回写后长度:", p.stdout.decode("utf-8", "replace").strip())
    if p.stderr:
        err = p.stderr.decode("utf-8", "replace")
        if "Error" in err:
            print("stderr:", err[:300])


def info(local: Path) -> None:
    d = json.loads(local.read_text(encoding="utf-8"))
    sv = d.get(NEW_SID) or {}
    print("新脚本 Info:", json.dumps(sv.get("Info"), ensure_ascii=False))
    print("新脚本 Emulator:", json.dumps(sv.get("Emulator"), ensure_ascii=False))
    u = ((sv.get("SubConfigsInfo") or {}).get("UserData") or {})
    print("新脚本用户数:", len(u))
    for uid, v in u.items():
        if uid == "instances" or not isinstance(v, dict):
            continue
        i = v.get("Info") or {}
        print(f"  uid={uid}  Name={i.get('Name')!r} Id={i.get('Id')!r} "
              f"Server={i.get('Server')} Status={i.get('Status')} "
              f"Stage={i.get('Stage')} S1={i.get('Stage_1')} Anni={i.get('Annihilation')} "
              f"Notes={i.get('Notes')!r}")
        print("   Task:", json.dumps(v.get("Task"), ensure_ascii=False))
    print("文件尾部是否换行:", local.read_bytes()[-1:] in (b"\n", b"\r"))
    print("总脚本数:", len([k for k in d if k != "instances"]))


if __name__ == "__main__":
    cmd, path = sys.argv[1], Path(sys.argv[2])
    {"pull": pull, "push": push, "info": info}[cmd](path)
