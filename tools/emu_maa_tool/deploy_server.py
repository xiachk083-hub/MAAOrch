"""一键部署工具服务端到目标机（经 manager /api/exec 传文件，deploy.py 同款通道）。

用法:
    python tools/emu_maa_tool/deploy_server.py              # 全量部署
    python tools/emu_maa_tool/deploy_server.py --no-takeover # 部署但不停用 MAAOrch

流程:
  1. 本地打包 server/ + web/ + start_server.bat → base64 分块
  2. 经 manager /api/exec（19998）写目标机 E:\\emu_maa_tool\\
  3. 大小校验
  4. 远程启动服务端（后台 pythonw）+ 注册启动文件夹自启
  5. --takeover（默认）：停用 MAAOrch 队列/回收（工具完全接管）

凭据: ~/.maorch_deploy.json  {"manager":..., "manager_token":..., "api_token":...}
"""
from __future__ import annotations
import base64
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).parent.parent.parent  # D:\1-2\MAAOrch
TOOL = ROOT / "tools" / "emu_maa_tool"
CRED = pathlib.Path.home() / ".maorch_deploy.json"

REMOTE_DIR = r"E:\emu_maa_tool"
# 分块大小（base64 字符数）。目标机 manager exec 经 PowerShell 执行，
# 命令行长度限制严格（实测 35KB 报 WinError 206，12KB 安全）→ 用小块。
CHUNK = 12_000

# 需要部署的路径（server/ web/ start_server.bat / data 骨架）
DEPLOY_PATHS = [
    "server",
    "web",
    "start_server.bat",
]

# 目标机路径（与本地不同）
REMOTE_SOURCE = r"E:\MAAOrch\services\maa\source"
REMOTE_PROJECT = r"E:\MAAOrch"


def _load_cred() -> dict:
    if CRED.exists():
        return json.loads(CRED.read_text(encoding="utf-8"))
    raise SystemExit("缺少凭据文件 ~/.maorch_deploy.json")


def _exec(cmd: str, timeout: int = 300) -> dict:
    cred = _load_cred()
    req = urllib.request.Request(
        _load_cred().get("manager", "http://100.79.173.69:19998") + "/api/exec",
        data=json.dumps({"command": cmd}).encode(),
        headers={"x-manager-token": cred.get("manager_token", ""),
                 "Content-Type": "application/json"}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception as e:
        return {"error": str(e)[:200]}


def _run_remote_py(script: str, timeout: int = 300) -> dict:
    b64 = base64.b64encode(script.encode()).decode()
    return _exec(f'python -c "import base64;exec(base64.b64decode(\'{b64}\').decode())"', timeout)


def _collect() -> dict[str, bytes]:
    """收集要部署的文件（相对路径 → 内容）。"""
    files = {}
    for rel in DEPLOY_PATHS:
        src = TOOL / rel
        if src.is_dir():
            for f in sorted(src.rglob("*")):
                if f.is_file() and not any(part.startswith("__pycache__") for part in f.parts):
                    files[str(f.relative_to(TOOL)).replace("\\", "/")] = f.read_bytes()
        elif src.is_file():
            files[rel.replace("\\", "/")] = src.read_bytes()
    return files


def _deploy_files(files: dict[str, bytes]) -> None:
    """分块经 manager /api/exec 写目标机。

    deploy.py 同款可靠方案：base64 分块用 Add-Content 追加到目标机临时
    文件（每条 PowerShell 命令短，不触发 Windows 32767 命令行限制），
    全部写完再统一 base64 解码到目标位置。
    """
    total = sum(len(v) for v in files.values())
    print(f"📦 部署 {len(files)} 个文件，共 {total/1024:.0f} KB")

    # 目标机解码器脚本（一次性写入）
    decoder = ("import sys, base64, pathlib\n"
               "raw = open(r'E:\\emu_maa_tool\\_dep.b64', encoding='ascii').read()\n"
               "data = base64.b64decode(raw)\n"
               "target = pathlib.Path(sys.argv[1])\n"
               "target.parent.mkdir(parents=True, exist_ok=True)\n"
               "open(target, 'wb').write(data)\n"
               "print('WROTE', len(data))\n")
    d_b64 = base64.b64encode(decoder.encode("utf-8")).decode("ascii")
    r = _exec("[IO.File]::WriteAllText('E:\\emu_maa_tool\\_dep2.py', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + d_b64 + "')))", 30)
    if r.get("error"):
        raise SystemExit(f"写解码器失败: {r['error']}")

    for rel, content in files.items():
        b64 = base64.b64encode(content).decode("ascii")
        tmp = r"E:\emu_maa_tool\_dep.b64"
        r = _exec("[IO.File]::WriteAllText('" + tmp + "', '')", 30)
        if r.get("error"):
            raise SystemExit(f"清空临时文件失败: {r['error']}")
        parts = [b64[i:i + CHUNK] for i in range(0, len(b64), CHUNK)]
        for part in parts:
            r = _exec("Add-Content -Path '" + tmp + "' -Value '" + part + "' -NoNewline", 120)
            if r.get("error"):
                raise SystemExit(f"部署 {rel} 分块失败: {r['error']}")
        r = _exec("python 'E:\\emu_maa_tool\\_dep2.py' '" + REMOTE_DIR.replace("\\", "\\\\") + "\\\\" + rel.replace("/", "\\") + "'", 60)
        out = r.get("stdout", "")
        if "WROTE" not in out:
            print(f"  ✗ {rel} 写入失败: {r.get('stderr', r.get('error', ''))[:200]}")
            raise SystemExit(f"部署 {rel} 失败")
        sent = len(content)
        print(f"  ✓ {rel} ({sent}B)")
    # 清理临时文件
    try:
        _exec("Remove-Item 'E:\\emu_maa_tool\\_dep.b64','E:\\emu_maa_tool\\_dep2.py' -ErrorAction SilentlyContinue", 30)
    except Exception:
        pass
    print("✅ 全部写入完成")


def _verify() -> None:
    script = f'''
import pathlib
root = pathlib.Path(r"{REMOTE_DIR}")
ok = (root / "server" / "main.py").exists() and (root / "web" / "index.html").exists()
print("VERIFY", ok, root)
'''
    r = _run_remote_py(script, 60)
    print(r.get("stdout", r.get("error", "")))
    if "VERIFY True" not in r.get("stdout", ""):
        raise SystemExit("校验失败")


def _write_remote_config(token: str) -> None:
    """目标机生成 data/server.json（token + 目标机路径）。"""
    cfg = {
        "port": 18889,
        "token": token,
        "maa_source_path": REMOTE_SOURCE,
        "project_root": REMOTE_PROJECT,
        "pool_size": 4,
        "config_sync_interval": 30,
    }
    payload = json.dumps(cfg, ensure_ascii=False, indent=2)
    b64 = base64.b64encode(payload.encode()).decode()
    script = f'''
import base64, pathlib, json
root = pathlib.Path(r"{REMOTE_DIR}")
(root / "data").mkdir(parents=True, exist_ok=True)
cfg = json.loads(base64.b64decode("{b64}").decode())
p = root / "data" / "server.json"
old = {{}}
if p.exists():
    try: old = json.loads(p.read_text(encoding="utf-8"))
    except Exception: pass
if old.get("token"):
    cfg["token"] = old["token"]  # 保留已有 token，避免反复部署后前端 token 失效
p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
print("CONFIG_WRITTEN", cfg["token"][:6] + "…")
'''
    r = _run_remote_py(script, 60)
    print(r.get("stdout", r.get("error", "")))
    if "CONFIG_WRITTEN" not in r.get("stdout", ""):
        raise SystemExit("写 server.json 失败")


def _gen_token() -> str:
    import secrets
    return secrets.token_hex(16)


def _start_remote() -> None:
    """远程启动服务端 + 注册启动文件夹自启。"""
    script = (
        "import subprocess, os\n"
        "root = r\"" + REMOTE_DIR + "\"\n"
        "bat = os.path.join(root, 'start_server.bat')\n"
        "r = subprocess.run(['netstat', '-ano'], capture_output=True, text=True, timeout=15)\n"
        "if ':18889' in r.stdout:\n"
        "    print('ALREADY_RUNNING')\n"
        "else:\n"
        "    startup = os.path.join(os.environ['APPDATA'], r'Microsoft\\Windows\\Start Menu\\Programs\\Startup')\n"
        "    vbs = os.path.join(startup, 'MAAOrchTool-Server.vbs')\n"
        "    with open(vbs, 'w', encoding='utf-8') as f:\n"
        "        f.write('Set s = CreateObject(\"WScript.Shell\")\\n')\n"
        "        f.write('s.Run chr(34) & \"' + bat + '\" & chr(34), 0, False\\n')\n"
        "    subprocess.Popen(['pythonw', os.path.join(root, 'server', 'main.py'), '0.0.0.0'], cwd=root)\n"
        "    print('STARTED')\n"
    )
    r = _run_remote_py(script, 120)
    print(r.get("stdout", r.get("error", "")))


def _takeover_maorch() -> None:
    """停用 MAAOrch 队列/回收（工具完全接管）。只停 scheduler，不动主进程。"""
    script = r'''
import json, pathlib
cfg = pathlib.Path(r"E:\MAAOrch\models\config.json")
if cfg.exists():
    d = json.loads(cfg.read_text(encoding="utf-8"))
    changed = False
    # 禁用队列 tick 与批量入队调度
    for k in ("daily_batch_time", "queue_tick_enabled"):
        if d.get(k) not in (None, "", False):
            d[k] = "" if k == "daily_batch_time" else False
            changed = True
    # 暂停队列
    q = pathlib.Path(r"E:\MAAOrch\services\queue.json")
    if q.exists():
        try:
            qd = json.loads(q.read_text(encoding="utf-8"))
            if isinstance(qd, dict):
                qd["paused"] = True
                q.write_text(json.dumps(qd, ensure_ascii=False, indent=2), encoding="utf-8")
                changed = True
        except Exception:
            pass
    if changed:
        cfg.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        print("TAKEOVER_DONE")
    else:
        print("ALREADY_TAKEOVER")
else:
    print("NO_MAORCH_CONFIG")
'''
    r = _run_remote_py(script, 60)
    print(r.get("stdout", r.get("error", "")))
    if "TAKEOVER_DONE" in r.get("stdout", ""):
        print("⚠ MAAOrch 队列/回收已停用（config.json 直写，未重启 manager）")


def main() -> None:
    takeover = "--no-takeover" not in sys.argv
    token = _gen_token()
    files = _collect()
    _deploy_files(files)
    _verify()
    _write_remote_config(token)
    _start_remote()
    if takeover:
        _takeover_maorch()
    print("\n✅ 部署完成。")
    print("   目标机服务端: http://100.79.173.69:18889")
    print(f"   访问 token: {token}")
    print("   本机启动: python tools/emu_maa_tool/client/app.py http://100.79.173.69:18889")
    print("   （前端顶栏「token」输入框填入上述 token）")


if __name__ == "__main__":
    main()
