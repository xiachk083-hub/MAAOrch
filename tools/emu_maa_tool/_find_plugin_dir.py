"""找 AUTO-MAS 插件目录与加载逻辑"""
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
        "# 1. 顶层目录\n"
        "base = 'E:' + BS + 'AUTO-MAS'\n"
        "out = {'顶层目录': sorted([d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))])}\n"
        "# 2. 找 plugin/plugins 目录（不限深度，但限制）\n"
        "plugins = []\n"
        "for root, dirs, files in os.walk(base):\n"
        "    if 'node_modules' in root or '.git' in root:\n"
        "        dirs[:] = []\n"
        "        continue\n"
        "    for d in dirs:\n"
        "        if 'plugin' in d.lower():\n"
        "            plugins.append(os.path.relpath(os.path.join(root, d), base))\n"
        "out['plugin目录'] = plugins[:20]\n"
        "print(base64.b64encode(json.dumps(out, ensure_ascii=False).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 90)
    try:
        print(base64.b64decode(r).decode("utf-8", errors="replace"))
    except Exception:
        print("原始:", r[:500])
