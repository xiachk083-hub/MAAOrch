"""找 AUTO-MAS 配置存储并清掉脚本锁"""
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
        "import os, pathlib\n"
        "for p in [r'E:\\AUTO-MAS\\data', r'E:\\AUTO-MAS\\config', r'E:\\AUTO-MAS\\repo\\data']:\n"
        "    if os.path.isdir(p):\n"
        "        print('===', p, '===')\n"
        "        for f in sorted(os.listdir(p))[:25]:\n"
        "            fp = os.path.join(p, f)\n"
        "            sz = os.path.getsize(fp) if os.path.isfile(fp) else '(dir)'\n"
        "            print(' ', f, sz)\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 60)
    print(r[:1000])
