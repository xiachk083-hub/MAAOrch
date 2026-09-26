"""找 AUTO-MAS 的 maa_remote/RemoteControl 相关代码"""
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
        "base = 'E:' + BS + 'AUTO-MAS' + BS + 'app'\n"
        "found = []\n"
        "for root, dirs, files in os.walk(base):\n"
        "    if '__pycache__' in root:\n"
        "        continue\n"
        "    for fn in files:\n"
        "        if not fn.endswith('.py'):\n"
        "            continue\n"
        "        p = os.path.join(root, fn)\n"
        "        try:\n"
        "            raw = open(p, encoding='utf-8', errors='replace').read()\n"
        "        except Exception:\n"
        "            continue\n"
        "        if 'maa_remote' in raw or 'RemoteControl' in raw or 'GetTaskEndpoint' in raw:\n"
        "            found.append(os.path.relpath(p, base))\n"
        "print(base64.b64encode(('\\n'.join(found[:15])).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 90)
    try:
        print(base64.b64decode(r).decode("utf-8", errors="replace"))
    except Exception:
        print("原始:", r[:600])
