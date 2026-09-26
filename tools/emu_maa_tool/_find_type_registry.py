"""看 AUTO-MAS 如何注册脚本类型（type → Manager 类映射）"""
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
    # 搜 app/core 或 app/models 里 type → Manager 的注册表
    script = (
        "import os, base64, json\n"
        "BS = chr(92)\n"
        "app = 'E:' + BS + 'AUTO-MAS' + BS + 'app'\n"
        "out = []\n"
        "for root, dirs, files in os.walk(app):\n"
        "    if 'node_modules' in root or '__pycache__' in root:\n"
        "        continue\n"
        "    for fn in files:\n"
        "        if not fn.endswith('.py'): continue\n"
        "        p = os.path.join(root, fn)\n"
        "        try:\n"
        "            raw = open(p, encoding='utf-8', errors='replace').read()\n"
        "        except Exception:\n"
        "            continue\n"
        "        # 找类型注册：MaaManager/SrcManager/OkwwManager 等映射\n"
        "        if 'Manager' in raw and ('TYPE' in raw.upper() or 'type_map' in raw or 'MaaManager' in raw):\n"
        "            if 'ScriptType' in raw or 'Manager' in raw and 'register' in raw.lower():\n"
        "                out.append(os.path.relpath(p, app))\n"
        "print(base64.b64encode(json.dumps(out[:20], ensure_ascii=False).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 90)
    try:
        print(base64.b64decode(r).decode("utf-8", errors="replace"))
    except Exception:
        print("原始:", r[:500])
