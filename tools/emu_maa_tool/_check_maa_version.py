"""查回收站 MAA 套件的版本（判断 AUTO-MAS 适配的 MAA 版本）"""
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
        "p = r'E:\\$RECYCLE.BIN\\S-1-5-21-3985176414-2312377112-82738781-500\\$RWARR92\\script\\MAA\\MAA_1'\n"
        "out = {}\n"
        "# 读 gui.json 结构\n"
        "gui_p = os.path.join(p, 'config', 'gui.json')\n"
        "if os.path.exists(gui_p):\n"
        "    d = json.load(open(gui_p, encoding='utf-8'))\n"
        "    out['gui.json keys'] = list(d.keys())[:10]\n"
        "    out['has_Global'] = 'Global' in d\n"
        "    out['has_Configurations'] = 'Configurations' in d\n"
        "    out['has_Current'] = 'Current' in d\n"
        "# 版本来源：MAA.deps.json 或 version 文件\n"
        "for vf in ['MAA.deps.json', 'version.json', 'res/version.json']:\n"
        "    vp = os.path.join(p, vf)\n"
        "    if os.path.exists(vp):\n"
        "        out[vf] = '存在'\n"
        "# MAA.deps.json 里的版本\n"
        "deps = os.path.join(p, 'MAA.deps.json')\n"
        "if os.path.exists(deps):\n"
        "    d = json.load(open(deps, encoding='utf-8'))\n"
        "    out['MAA.deps runtime'] = str(d.get('runtimeTarget', {}).get('name', ''))[:80]\n"
        "# 目录结构\n"
        "out['顶层文件数'] = len([f for f in os.listdir(p) if os.path.isfile(os.path.join(p, f))])\n"
        "out['顶层目录'] = [d for d in os.listdir(p) if os.path.isdir(os.path.join(p, d))][:10]\n"
        "print(base64.b64encode(json.dumps(out, ensure_ascii=False).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 60)
    try:
        print(json.dumps(json.loads(base64.b64decode(r).decode("utf-8")), ensure_ascii=False, indent=2))
    except Exception:
        print("原始:", r[:400])
