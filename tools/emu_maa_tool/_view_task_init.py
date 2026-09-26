"""看 app/task/__init__.py 类型扫描 + TaskExecuteBase 基类"""
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


if __name__ == "__main__":
    script = (
        "import os, base64, json\n"
        "BS = chr(92)\n"
        "app = 'E:' + BS + 'AUTO-MAS' + BS + 'app'\n"
        "out = {}\n"
        "# 1. app/task/__init__.py\n"
        "p1 = os.path.join(app, 'task', '__init__.py')\n"
        "if os.path.exists(p1):\n"
        "    out['task/__init__.py'] = open(p1, encoding='utf-8', errors='replace').read().splitlines()[:60]\n"
        "# 2. app/models/task.py 的 TaskExecuteBase 完整\n"
        "p2 = os.path.join(app, 'models', 'task.py')\n"
        "if os.path.exists(p2):\n"
        "    out['models/task.py 行数'] = len(open(p2, encoding='utf-8', errors='replace').read().splitlines())\n"
        "print(base64.b64encode(json.dumps(out, ensure_ascii=False).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 90)
    try:
        print(base64.b64decode(r).decode("utf-8", errors="replace"))
    except Exception:
        print("原始:", r[:1500])
