"""看类型注册表完整上下文（插件注册点）"""
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
        "out = {}\n"
        "# 1. config.py 里注册表上下文\n"
        "p1 = 'E:' + BS + 'AUTO-MAS' + BS + 'app' + BS + 'models' + BS + 'config.py'\n"
        "lines = open(p1, encoding='utf-8', errors='replace').read().splitlines()\n"
        "for i, ln in enumerate(lines):\n"
        "    if '\"MAA\": MaaConfig' in ln:\n"
        "        out['config.py 注册表'] = lines[max(0,i-3):i+12]\n"
        "        break\n"
        "# 2. constants.py 映射上下文\n"
        "p2 = 'E:' + BS + 'AUTO-MAS' + BS + 'app' + BS + 'utils' + BS + 'constants.py'\n"
        "lines2 = open(p2, encoding='utf-8', errors='replace').read().splitlines()\n"
        "for i, ln in enumerate(lines2):\n"
        "    if '\"MaaConfig\": \"MAA\"' in ln:\n"
        "        out['constants.py 映射'] = lines2[max(0,i-3):i+10]\n"
        "        break\n"
        "print(base64.b64encode(json.dumps(out, ensure_ascii=False).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 90)
    try:
        print(base64.b64decode(r).decode("utf-8", errors="replace"))
    except Exception:
        print("原始:", r[:1500])
