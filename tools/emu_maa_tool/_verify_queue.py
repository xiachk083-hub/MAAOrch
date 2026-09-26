"""验证队列内容和脚本路径"""
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
        "out = []\n"
        "# 查队列1 的项（脚本名）\n"
        "q = post('/api/queue/get')\n"
        "for qi in q.get('index', []):\n"
        "    qid = qi['uid']\n"
        "    qd = post('/api/queue/get', {'queueId': qid})\n"
        "    name = qd.get('data', {}).get(qid, {}).get('Info', {}).get('Name', '?')\n"
        "    if name != '队列1': continue\n"
        "    items = post('/api/queue/item/get', {'queueId': qid})\n"
        "    for it in items.get('index', []):\n"
        "        itd = post('/api/queue/item/get', {'queueId': qid, 'queueItemId': it['uid']})\n"
        "        sid = itd.get('data', {}).get(it['uid'], {}).get('Info', {}).get('ScriptId', '?')\n"
        "        sc = post('/api/scripts/get', {'scriptId': sid})\n"
        "        sname = sc.get('data', {}).get(sid, {}).get('Info', {}).get('Name', '?')\n"
        "        spath = sc.get('data', {}).get(sid, {}).get('Info', {}).get('Path', '?')\n"
        "        out.append(sname + ' -> ' + spath.split(chr(92))[-1])\n"
        "print(base64.b64encode(('\\n'.join(out)).encode('utf-8')).decode())\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    r = ex("python -c \"import base64;exec(base64.b64decode('" + b64 + "').decode())\"", 60)
    try:
        print(base64.b64decode(r).decode("utf-8", errors="replace"))
    except Exception:
        print("原始:", r[:500])
