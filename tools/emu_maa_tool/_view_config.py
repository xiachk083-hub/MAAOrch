"""看 AUTO-MAS ScriptConfig 和 QueueConfig 结构"""
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
            return "HTTPERR " + e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            return "ERR " + str(e)[:200]


if __name__ == "__main__":
    # 读 ScriptConfig.json 结构（前几个脚本的字段）+ QueueConfig.json
    script = (
        "import json, base64\n"
        "sc = json.load(open(r'E:\\AUTO-MAS\\config\\ScriptConfig.json', encoding='utf-8'))\n"
        "qc = json.load(open(r'E:\\AUTO-MAS\\config\\QueueConfig.json', encoding='utf-8'))\n"
        "out = {}\n"
        "# ScriptConfig 结构\n"
        "if isinstance(sc, dict):\n"
        "    keys = list(sc.keys())[:5]\n"
        "    out['ScriptConfig keys'] = keys\n"
        "    for k in keys[:2]:\n"
        "        v = sc[k]\n"
        "        out['script ' + k[:8]] = {kk: (str(vv)[:60]) for kk, vv in list(v.items())[:8]} if isinstance(v, dict) else str(v)[:100]\n"
        "else:\n"
        "    out['ScriptConfig type'] = type(sc).__name__\n"
        "out['QueueConfig'] = json.dumps(qc, ensure_ascii=False)[:500] if isinstance(qc, dict) else str(qc)[:200]\n"
        "print(base64.b64encode(json.dumps(out, ensure_ascii=False).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 60)
    try:
        print(json.dumps(json.loads(base64.b64decode(r).decode("utf-8")), ensure_ascii=False, indent=2))
    except Exception:
        print("原始:", r[:600])
