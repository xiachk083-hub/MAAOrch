"""看 MaaConfig 等配置类定义（脚本类型核心）"""
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
    # 看 config.py 里 MaaConfig 定义 + ScriptType/脚本类型注册
    script = (
        "import os, base64, json\n"
        "BS = chr(92)\n"
        "p = 'E:' + BS + 'AUTO-MAS' + BS + 'app' + BS + 'models' + BS + 'config.py'\n"
        "raw = open(p, encoding='utf-8', errors='replace').read()\n"
        "lines = raw.splitlines()\n"
        "out = []\n"
        "# 找 class 定义 + 类型注册\n"
        "for i, ln in enumerate(lines):\n"
        "    if ln.startswith('class ') and ('Config' in ln or 'Manager' in ln):\n"
        "        out.append(str(i) + ': ' + ln.strip())\n"
        "    if 'ScriptType' in ln or 'type' in ln and 'enum' in ln.lower():\n"
        "        out.append(str(i) + ': ' + ln.strip()[:80])\n"
        "print(base64.b64encode(('\\n'.join(out[:40])).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 90)
    try:
        print(base64.b64decode(r).decode("utf-8", errors="replace"))
    except Exception:
        print("原始:", r[:800])
