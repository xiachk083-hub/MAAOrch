"""把诊断脚本写到目标机执行（读取 AUTO-MAS 调度源码的 add_task 逻辑）"""
import base64
import json
import urllib.request

CRED = json.load(open(r"C:\Users\xiach\.maorch_deploy.json", encoding="utf-8"))
MANAGER = CRED.get("manager", "http://100.79.173.69:19998")
TOKEN = CRED.get("manager_token", "")


def ex(cmd, t=90):
    req = urllib.request.Request(
        MANAGER + "/api/exec",
        data=json.dumps({"command": cmd}).encode(),
        headers={"x-manager-token": TOKEN, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=t).read())
        return (r.get("stdout", "") + r.get("stderr", "")).strip() or "(空)"
    except Exception as e:
        try:
            return "HTTPERR " + e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            return "ERR " + str(e)[:150]


def deploy_script(script):
    """把 python 脚本写到目标机 E:\\emu_maa_tool\\_diag.py 并执行。"""
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    ex("[IO.File]::WriteAllText('E:\\emu_maa_tool\\_diag.py', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + b64 + "')))", 30)
    return ex("python E:\\emu_maa_tool\\_diag.py", 60)


if __name__ == "__main__":
    script = r'''
import pathlib, base64, json
found = []
for base in [r"E:\AUTO-MAS\app", r"E:\AUTO-MAS\repo\app"]:
    p = pathlib.Path(base) / "core" / "task_manager.py"
    if p.exists():
        raw = p.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()
        for i, ln in enumerate(lines):
            if "def add_task" in ln or "async def add_task" in ln:
                found.append((str(p), lines[max(0,i-5):i+80]))
                break
# 输出 base64（分小段）
out = json.dumps([(p, seg) for p, seg in found], ensure_ascii=False)
b = base64.b64encode(out.encode("utf-8")).decode()
pdir = pathlib.Path(r"E:\emu_maa_tool\_diag_parts")
pdir.mkdir(exist_ok=True)
for f in pdir.glob("*.txt"): f.unlink()
for i in range(0, len(b), 2000):
    (pdir / ("d%03d.txt" % (i // 2000))).write_text(b[i:i+2000], encoding="ascii")
print("DIAG_PARTS", len(b) // 2000 + 1)
'''
    r = deploy_script(script)
    print("远程:", r[:80])
    n = int(r.strip().splitlines()[-1].split()[-1])
    b64_all = ""
    for i in range(n):
        piece = ex("python -c \"import pathlib;print(pathlib.Path(r'E:\\\\emu_maa_tool\\\\_diag_parts\\\\d%03d.txt').read_text(encoding='ascii'))\"" % i, 30)
        b64_all += piece.strip()
    data = json.loads(base64.b64decode(b64_all).decode("utf-8"))
    for p, seg in data:
        print("=" * 20, p)
        print("\n".join(seg))
