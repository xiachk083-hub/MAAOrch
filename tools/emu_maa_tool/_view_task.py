"""看 AUTO-MAS 的 app/task 结构（脚本类型适配器如何组织）"""
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
        "app = 'E:' + BS + 'AUTO-MAS' + BS + 'app'\n"
        "out = {}\n"
        "# app 顶层\n"
        "out['app顶层'] = sorted(os.listdir(app))\n"
        "# app/task 结构（脚本类型适配器）\n"
        "task = os.path.join(app, 'task')\n"
        "if os.path.isdir(task):\n"
        "    out['app/task'] = {}\n"
        "    for d in sorted(os.listdir(task)):\n"
        "        dp = os.path.join(task, d)\n"
        "        if os.path.isdir(dp):\n"
        "            out['app/task'][d] = sorted(os.listdir(dp))[:15]\n"
        "print(base64.b64encode(json.dumps(out, ensure_ascii=False).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 90)
    try:
        print(base64.b64decode(r).decode("utf-8", errors="replace"))
    except Exception:
        print("原始:", r[:800])
