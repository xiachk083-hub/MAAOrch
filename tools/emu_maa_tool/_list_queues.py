"""列出 AUTO-MAS 所有队列及其项数，删掉旧测试队列"""
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
        "import json, base64, urllib.request\n"
        "def post(path, body=None):\n"
        "    data = json.dumps(body).encode() if body is not None else b'{}'\n"
        "    req = urllib.request.Request('http://127.0.0.1:36163' + path, data=data, method='POST',\n"
        "        headers={'Content-Type': 'application/json'})\n"
        "    try:\n"
        "        r = urllib.request.urlopen(req, timeout=20)\n"
        "        return json.loads(r.read())\n"
        "    except Exception as e:\n"
        "        try: return {'error': e.read().decode('utf-8','replace')[:150]}\n"
        "        except: return {'error': str(e)[:120]}\n"
        "q = post('/api/queue/get')\n"
        "idx = q.get('index', [])\n"
        "out = []\n"
        "for qi in idx:\n"
        "    qid = qi['uid']\n"
        "    qd = post('/api/queue/get', {'queueId': qid})\n"
        "    data = qd.get('data', {}).get(qid, {})\n"
        "    name = data.get('Info', {}).get('Name', '?')\n"
        "    items = post('/api/queue/item/get', {'queueId': qid})\n"
        "    n = len(items.get('index', []))\n"
        "    out.append(qid[:8] + ' | ' + name + ' | ' + str(n) + '项')\n"
        "print(base64.b64encode(('\\n'.join(out)).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 60)
    try:
        print(base64.b64decode(r).decode("utf-8", errors="replace"))
    except Exception:
        print("原始:", r[:500])
