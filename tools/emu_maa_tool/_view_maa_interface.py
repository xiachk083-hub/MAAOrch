"""看 MAA 脚本类型的接口规范（插件开发参考）"""
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
    # 读 MAA 类型的关键文件（__init__ 注册 + manager 头 + task_manager 里怎么分发）
    script = (
        "import os, base64, json\n"
        "BS = chr(92)\n"
        "base = 'E:' + BS + 'AUTO-MAS' + BS + 'app' + BS + 'task' + BS + 'MAA'\n"
        "out = {}\n"
        "for fn in ['__init__.py', 'manager.py']:\n"
        "    p = os.path.join(base, fn)\n"
        "    if os.path.exists(p):\n"
        "        raw = open(p, encoding='utf-8', errors='replace').read()\n"
        "        lines = raw.splitlines()\n"
        "        out[fn] = lines[:60]\n"
        "print(base64.b64encode(json.dumps(out, ensure_ascii=False).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 90)
    try:
        print(base64.b64decode(r).decode("utf-8", errors="replace"))
    except Exception:
        print("原始:", r[:1500])
