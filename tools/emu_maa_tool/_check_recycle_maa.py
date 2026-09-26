"""检查回收站里的 MAA 多开套件是否完整可用"""
import base64
import json
import urllib.request

CRED = json.load(open(r"C:\Users\xiach\.maorch_deploy.json", encoding="utf-8"))
MANAGER = CRED.get("manager", "http://100.79.173.69:19998")
TOKEN = CRED.get("manager_token", "")


def ex(cmd, t=60):
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
        "import os, json, base64\n"
        "base = r'E:\\$RECYCLE.BIN\\S-1-5-21-3985176414-2312377112-82738781-500\\$RWARR92\\script\\MAA'\n"
        "out = {'dirs': [], 'MAA_1': {}, 'base_exists': os.path.exists(base)}\n"
        "if os.path.exists(base):\n"
        "    for d in sorted(os.listdir(base))[:3]:\n"
        "        p = os.path.join(base, d)\n"
        "        if os.path.isdir(p):\n"
        "            has_exe = os.path.exists(os.path.join(p, 'MAA.exe'))\n"
        "            has_gui = os.path.exists(os.path.join(p, 'config', 'gui.json'))\n"
        "            has_gui_new = os.path.exists(os.path.join(p, 'config', 'gui.new.json'))\n"
        "            out['dirs'].append(d)\n"
        "            if d == 'MAA_1':\n"
        "                out['MAA_1'] = {'MAA.exe': has_exe, 'gui.json': has_gui, 'gui.new.json': has_gui_new,\n"
        "                                'config内容': os.listdir(os.path.join(p, 'config')) if os.path.isdir(os.path.join(p, 'config')) else []}\n"
        "print(base64.b64encode(json.dumps(out, ensure_ascii=False).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 60)
    try:
        print(json.dumps(json.loads(base64.b64decode(r).decode("utf-8")), ensure_ascii=False, indent=2))
    except Exception:
        print("原始:", r[:400])
