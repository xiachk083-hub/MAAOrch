"""批量更新 AUTO-MAS 用户配置：Stage（刷关关卡）+ Status（挂起状态）。

数据源：_scripts_with_tasks.json（脚本ID + 关卡 + 挂起）。
对每个脚本：查其唯一用户 → 更新 Info.Stage/Status。
"""
import base64
import json
import pathlib
import urllib.request

CRED = json.load(open(r"C:\Users\xiach\.maorch_deploy.json", encoding="utf-8"))
MANAGER = CRED.get("manager", "http://100.79.173.69:19998")
TOKEN = CRED.get("manager_token", "")

MERGED = json.loads(
    pathlib.Path(r"D:\1-2\MAAOrch\tools\emu_maa_tool\_scripts_with_tasks.json")
    .read_text(encoding="utf-8")
)


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


def run_remote_py(code, t=300):
    b64 = base64.b64encode(code.encode("utf-8")).decode()
    return ex('python -c "import base64;exec(base64.b64decode(\'' + b64 + '\').decode())"', t)


def build(merged):
    L = []
    L.append("import urllib.request, json, pathlib, sys")
    L.append("BASE='http://127.0.0.1:36163'")
    L.append(
        "def post(path, body):\n"
        "    req = urllib.request.Request(BASE+path, data=json.dumps(body).encode(), method='POST',\n"
        "        headers={'Content-Type':'application/json'})\n"
        "    try:\n"
        "        r = urllib.request.urlopen(req, timeout=30)\n"
        "        return json.loads(r.read())\n"
        "    except Exception as e:\n"
        "        try: return {'error': e.read().decode('utf-8','replace')[:150]}\n"
        "        except: return {'error': str(e)[:120]}\n"
    )
    L.append("results=[]")
    L.append("for i, m in enumerate(%r):" % merged)
    L.append("    sid = m['script_id']")
    L.append("    stage = m.get('stage','')")
    L.append("    suspended = m.get('suspended', False)")
    L.append("    name = m['name']")
    # 查脚本用户
    L.append("    us = post('/api/scripts/user/get', {'scriptId': sid})")
    L.append("    idx = us.get('index', [])")
    L.append("    if not idx:")
    L.append("        results.append((name, 'NO_USER')); continue")
    L.append("    uid = idx[0]['uid']")
    # 更新用户配置
    L.append(
        "    data = {'Info': {'Stage': stage, 'Status': not suspended}}\n"
        "    r = post('/api/scripts/user/update', {'scriptId': sid, 'userId': uid, 'data': data})"
    )
    L.append("    if r.get('code') != 200:")
    L.append("        results.append((name, 'UPDATE_FAIL', r.get('error') or r.get('message',''))); continue")
    L.append("    results.append((name, 'OK', stage, '停' if suspended else '启'))")
    L.append("    if i % 5 == 0: sys.stderr.write('p ' + str(i+1) + '/' + str(" + str(len(merged)) + ") + chr(10))")
    L.append(
        "pathlib.Path(r'E:\\emu_maa_tool\\_task_update_result.json').write_text(\n"
        "    json.dumps(results, ensure_ascii=False), encoding='utf-8')\n"
        "print('UPDATE_DONE')\n"
    )
    return "\n".join(L)


def read_result():
    code = (
        "import pathlib, base64\n"
        "raw = pathlib.Path(r'E:\\emu_maa_tool\\_task_update_result.json').read_text(encoding='utf-8')\n"
        "b = base64.b64encode(raw.encode('utf-8')).decode()\n"
        "p = pathlib.Path(r'E:\\emu_maa_tool\\_task_res_parts')\n"
        "p.mkdir(exist_ok=True)\n"
        "for f in p.glob('*.txt'): f.unlink()\n"
        "for i in range(0, len(b), 2000):\n"
        "    (p / ('r%03d.txt' % (i//2000))).write_text(b[i:i+2000], encoding='ascii')\n"
        "print('PARTS', len(b)//2000 + 1)\n"
    )
    r = run_remote_py(code, 120)
    n = int(r.strip().splitlines()[-1].split()[-1])
    b64_all = ""
    for i in range(n):
        piece = run_remote_py(
            "import pathlib\n"
            "print(pathlib.Path(r'E:\\emu_maa_tool\\_task_res_parts\\r%03d.txt').read_text(encoding='ascii'))\n" % i
        )
        b64_all += piece.strip()
    return json.loads(base64.b64decode(b64_all).decode("utf-8"))


if __name__ == "__main__":
    print(f"更新 {len(MERGED)} 个账号的关卡/挂起配置 ...")
    script = build(MERGED)
    r = run_remote_py(script, 600)
    print("远程:", r[:80])
    results = read_result()
    ok = [x for x in results if x[1] == "OK"]
    fail = [x for x in results if x[1] != "OK"]
    print(f"成功: {len(ok)} / 失败: {len(fail)}")
    for f in fail[:10]:
        print("  ✗", f)
    for x in ok[:10]:
        print("  ✓", x[0], "→", x[2], x[3])
