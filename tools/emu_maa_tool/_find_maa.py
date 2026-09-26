"""找目标机独立的 MAA 安装（排除 MAAOrch 屎山）"""
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
        "found = []\n"
        "for base in ['E:\\\\', 'D:\\\\']:\n"
        "    for dirpath, dirs, files in os.walk(base):\n"
        "        if 'MAAOrch' in dirpath or 'AUTO-MAS' in dirpath or 'emu_maa_tool' in dirpath:\n"
        "            dirs[:] = []\n"
        "            continue\n"
        "        if 'MAA.exe' in files:\n"
        "            found.append(dirpath)\n"
        "            dirs[:] = []\n"
        "out = json.dumps(found[:20], ensure_ascii=False)\n"
        "print(base64.b64encode(out.encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 120)
    try:
        print("独立 MAA 位置:", json.loads(base64.b64decode(r).decode("utf-8")))
    except Exception:
        print("原始:", r[:300])
