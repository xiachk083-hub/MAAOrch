"""读 ScriptConfig.py 的 ConfigFile 生成逻辑"""
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
        "import pathlib, base64\n"
        "f = pathlib.Path(r'E:\\AUTO-MAS\\app\\task\\MAA\\ScriptConfig.py')\n"
        "raw = f.read_text(encoding='utf-8', errors='replace')\n"
        "lines = raw.splitlines()\n"
        "out = []\n"
        "for i, ln in enumerate(lines):\n"
        "    if 'ConfigFile' in ln or 'copytree' in ln or 'maa_set_path' in ln or 'def save' in ln or 'def main' in ln:\n"
        "        out.append(str(i) + ': ' + ln.strip()[:100])\n"
        "b = base64.b64encode(('\\n'.join(out[:30])).encode('utf-8')).decode()\n"
        "print(b)\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 60)
    try:
        print(base64.b64decode(r).decode("utf-8", errors="replace"))
    except Exception:
        print("原始:", r[:500])
