"""批量录入账号到 AUTO-MAS：对每个账号 user/add + user/update。

数据源：本地 _accounts_ready.json（从 MAAOrch config.json 提取 + 映射好 auto_id）。
目标：目标机 AUTO-MAS 后端 http://127.0.0.1:36163，MAA 脚本已建。
"""
import base64
import json
import pathlib
import urllib.request

CRED = json.load(open(r"C:\Users\xiach\.maorch_deploy.json", encoding="utf-8"))
MANAGER = CRED.get("manager", "http://100.79.173.69:19998")
TOKEN = CRED.get("manager_token", "")
SCRIPT_ID = "7d84fda0-80d1-4bea-84fd-89ee4015ac70"

# 账号数据（本机准备）
ACCTS = json.loads(
    pathlib.Path(r"D:\1-2\MAAOrch\tools\emu_maa_tool\_accounts_ready.json")
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


def build_remote_script(accounts, script_id):
    """生成目标机执行的批量录入 python 脚本（一次性处理全部账号）。"""
    lines = []
    lines.append("import urllib.request, json, sys")
    lines.append("BASE='http://127.0.0.1:36163'")
    lines.append("sid=%r" % script_id)
    lines.append(
        "def post(path, body):\n"
        "    req = urllib.request.Request(BASE+path, data=json.dumps(body).encode(), method='POST',\n"
        "        headers={'Content-Type':'application/json'})\n"
        "    try:\n"
        "        r = urllib.request.urlopen(req, timeout=30)\n"
        "        return json.loads(r.read())\n"
        "    except Exception as e:\n"
        "        try: return {'error': e.read().decode('utf-8','replace')[:200]}\n"
        "        except: return {'error': str(e)[:150]}\n"
    )
    lines.append("results=[]")
    lines.append("for i, acc in enumerate(%r):" % accounts)
    lines.append("    name = acc['name']")
    lines.append("    server = acc['game_client']")
    lines.append("    aid = acc.get('auto_id','')")
    lines.append("    note = acc.get('note','')")
    lines.append("    r = post('/api/scripts/user/add', {'scriptId': sid})")
    lines.append("    if r.get('code') != 200:")
    lines.append("        results.append((name, 'ADD_FAIL', r.get('message',''))); continue")
    lines.append("    uid = r.get('userId','')")
    lines.append(
        "    u = post('/api/scripts/user/update', {'scriptId': sid, 'userId': uid,\n"
        "        'data': {'Info': {'Name': name, 'Server': server, 'Id': aid,\n"
        "                           'Notes': note[:100], 'Status': True}}})"
    )
    lines.append("    if u.get('code') != 200:")
    lines.append("        results.append((name, 'UPDATE_FAIL', u.get('message',''))); continue")
    lines.append("    results.append((name, 'OK'))")
    lines.append(
        "    if i % 5 == 0: sys.stderr.write('progress ' + str(i+1) + '/' + str(" + str(len(accounts)) + ") + chr(10))"
    )
    # 结果写文件（避免 GBK print 崩溃）
    lines.append(
        "import pathlib\n"
        "pathlib.Path(r'E:\\emu_maa_tool\\_batch_result.json').write_text(\n"
        "    json.dumps(results, ensure_ascii=False), encoding='utf-8')\n"
        "print('BATCH_DONE')\n"
    )
    return "\n".join(lines)


def read_result():
    code = (
        "import pathlib, base64\n"
        "raw = pathlib.Path(r'E:\\emu_maa_tool\\_batch_result.json').read_text(encoding='utf-8')\n"
        "b = base64.b64encode(raw.encode('utf-8')).decode()\n"
        "for i in range(0, len(b), 3000):\n"
        "    print('CHUNK', i // 3000, b[i:i+3000])\n"
        "print('END')\n"
    )
    out = run_remote_py(code, 120)
    b64_all = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("CHUNK"):
            parts = line.split(" ", 2)
            if len(parts) == 3:
                b64_all += parts[2]
        elif line == "END":
            break
    try:
        return json.loads(base64.b64decode(b64_all).decode("utf-8"))
    except Exception as e:
        return {"_error": str(e), "_raw": out[:200]}


if __name__ == "__main__":
    print(f"批量录入 {len(ACCTS)} 个账号到 AUTO-MAS ...")
    script = build_remote_script(ACCTS, SCRIPT_ID)
    r = run_remote_py(script, 600)
    print("远程执行:", r[:120])
    results = read_result()
    if isinstance(results, dict) and results.get("_error"):
        print("读取结果失败:", results["_error"])
    else:
        ok = [x for x in results if x[1] == "OK"]
        fail = [x for x in results if x[1] != "OK"]
        print(f"\n成功: {len(ok)} / 失败: {len(fail)}")
        for f in fail[:10]:
            print("  ✗", f)
