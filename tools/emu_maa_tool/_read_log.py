"""读 AUTO-MAS app.log 最近的调度相关日志"""
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


def run_remote_py(code):
    b64 = base64.b64encode(code.encode("utf-8")).decode()
    return ex('python -c "import base64;exec(base64.b64decode(\'' + b64 + '\').decode())"')


if __name__ == "__main__":
    # 在目标机读日志，按行过滤调度相关，base64 分小段
    script = r'''
import pathlib, base64
p = pathlib.Path(r"E:\AUTO-MAS\debug\app.log")
raw = p.read_bytes()
try:
    txt = raw.decode("utf-8", errors="replace")
except Exception:
    txt = raw.decode("gbk", errors="replace")
lines = txt.splitlines()
# 过滤关键行
keys = ["任务", "task", "Task", "AutoProxy", "error", "Error", "脚本", "模拟器", "失败", "exception"]
out = []
for ln in lines[-300:]:
    if any(k in ln for k in keys):
        out.append(ln[-200:])
sel = out[-80:]
b = base64.b64encode(("\n".join(sel)).encode("utf-8")).decode()
pdir = pathlib.Path(r"E:\emu_maa_tool\_log_parts")
pdir.mkdir(exist_ok=True)
for f in pdir.glob("*.txt"): f.unlink()
for i in range(0, len(b), 2000):
    (pdir / ("l%03d.txt" % (i // 2000))).write_text(b[i:i+2000], encoding="ascii")
print("LOG_PARTS", len(b) // 2000 + 1)
'''
    r = run_remote_py(script)
    print("远程:", r[:60])
    n = int(r.strip().splitlines()[-1].split()[-1])
    b64_all = ""
    for i in range(n):
        piece = run_remote_py(
            "import pathlib\n"
            "print(pathlib.Path(r'E:\\emu_maa_tool\\_log_parts\\l%03d.txt').read_text(encoding='ascii'))\n" % i
        )
        b64_all += piece.strip()
    try:
        txt = base64.b64decode(b64_all).decode("utf-8", errors="replace")
        print(txt)
    except Exception as e:
        print("解析失败:", e, "| 长度:", len(b64_all))
