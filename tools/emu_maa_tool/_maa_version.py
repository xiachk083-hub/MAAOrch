"""锁定回收站 MAA 的确切版本号"""
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
        "# 找 version 相关文件\n"
        "for root, dirs, files in os.walk(p):\n"
        "    depth = root[len(p):].count(os.sep)\n"
        "    if depth > 2: continue\n"
        "    for fn in files:\n"
        "        if 'version' in fn.lower() or 'deps' in fn.lower():\n"
        "            fp = os.path.join(root, fn)\n"
        "            try:\n"
        "                content = open(fp, encoding='utf-8', errors='replace').read()\n"
        "                out[os.path.relpath(fp, p)] = content[:200]\n"
        "            except Exception:\n"
        "                pass\n"
        "# MAA.exe 属性版本（用 powershell）\n"
        "print(base64.b64encode(json.dumps(out, ensure_ascii=False).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 60)
    try:
        print(json.dumps(json.loads(base64.b64decode(r).decode("utf-8")), ensure_ascii=False, indent=2))
    except Exception:
        print("原始:", r[:500])
