"""看 MAA AutoProxy 如何触发任务（兼容问题的根源）"""
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
        "p = 'E:' + BS + 'AUTO-MAS' + BS + 'app' + BS + 'task' + BS + 'MAA' + BS + 'AutoProxy.py'\n"
        "raw = open(p, encoding='utf-8', errors='replace').read()\n"
        "lines = raw.splitlines()\n"
        "out = []\n"
        "# 找任务触发相关：RemoteControl/GetTask/start/maa_tasks/启动任务\n"
        "for i, ln in enumerate(lines):\n"
        "    if any(k in ln for k in ['RemoteControl', 'GetTask', 'maa_tasks', 'open_process', 'wait_event', 'start_monitor', 'def main_task', 'RunDirectly', 'StartUpTask', 'set_maa']):\n"
        "        out.append(str(i) + ': ' + ln.strip()[:110])\n"
        "print(base64.b64encode(('\\n'.join(out[:35])).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 90)
    try:
        print(base64.b64decode(r).decode("utf-8", errors="replace"))
    except Exception:
        print("原始:", r[:1200])
