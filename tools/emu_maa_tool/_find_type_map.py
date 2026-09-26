"""找脚本类型注册/分发逻辑（Config 类 ↔ type 字符串）"""
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
    # 搜 app/core 和 app/api 里 type 字符串 → Config 类
    script = (
        "import os, base64, json\n"
        "BS = chr(92)\n"
        "app = 'E:' + BS + 'AUTO-MAS' + BS + 'app'\n"
        "out = []\n"
        "for root, dirs, files in os.walk(app):\n"
        "    if '__pycache__' in root:\n"
        "        continue\n"
        "    for fn in files:\n"
        "        if not fn.endswith('.py'): continue\n"
        "        p = os.path.join(root, fn)\n"
        "        try:\n"
        "            raw = open(p, encoding='utf-8', errors='replace').read()\n"
        "        except Exception:\n"
        "            continue\n"
        "        if 'MaaConfig' in raw and ('type' in raw or 'Type' in raw):\n"
        "            # 找 type 映射到 MaaConfig 的行\n"
        "            for ln in raw.splitlines():\n"
        "                if 'MaaConfig' in ln and ('MAA' in ln or 'type' in ln.lower() or '\"MAA\"' in ln):\n"
        "                    out.append(os.path.relpath(p, app) + ' :: ' + ln.strip()[:100])\n"
        "print(base64.b64encode(('\\n'.join(out[:20])).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 90)
    try:
        print(base64.b64decode(r).decode("utf-8", errors="replace"))
    except Exception:
        print("原始:", r[:600])
