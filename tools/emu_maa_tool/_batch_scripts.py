"""每个账号建独立 MAA 脚本（绑定对应模拟器），账号从共用脚本迁移。

AUTO-MAS 架构：一个脚本 = 一台模拟器 + 多个用户。
48 账号一对一分布在 48 台模拟器 → 每个账号一个脚本（脚本绑模拟器 + 挂账号）。
"""
import base64
import json
import pathlib
import urllib.request

CRED = json.load(open(r"C:\Users\xiach\.maorch_deploy.json", encoding="utf-8"))
MANAGER = CRED.get("manager", "http://100.79.173.69:19998")
TOKEN = CRED.get("manager_token", "")
SHARED_SCRIPT = "7d84fda0-80d1-4bea-84fd-89ee4015ac70"  # 共用脚本（迁移源）
EMULATOR_ID = "6e09a6bb-3126-412e-8412-9c90fb0c4499"  # MuMu 多开器
MAA_PATH = r"E:\MAAOrch\services\maa\source"

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


def build(accounts, shared_sid, emu_id, maa_path):
    L = []
    L.append("import urllib.request, json, pathlib, sys")
    L.append("BASE='http://127.0.0.1:36163'")
    L.append("shared=%r" % shared_sid)
    L.append("emu_id=%r" % emu_id)
    L.append("maa=%r" % maa_path)
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
    L.append("for i, acc in enumerate(%r):" % accounts)
    L.append("    name = acc['name']")
    L.append("    idx = str(acc.get('emu_instance_index',''))")
    L.append("    server = acc['game_client']")
    L.append("    aid = acc.get('auto_id','')")
    L.append("    note = acc.get('note','')")
    # 1. 建脚本（type=MAA）
    L.append("    r1 = post('/api/scripts/add', {'type':'MAA'})")
    L.append("    if r1.get('code') != 200:")
    L.append("        results.append((name, 'SCRIPT_ADD_FAIL', r1.get('error') or r1.get('message',''))); continue")
    L.append("    sid = r1.get('scriptId','')")
    # 2. 设脚本：名称 + MAA 路径 + 模拟器
    L.append(
        "    r2 = post('/api/scripts/update', {'scriptId': sid, 'data': {\n"
        "        'Info': {'Name': name, 'Path': maa},\n"
        "        'Emulator': {'Id': emu_id, 'Index': idx}}})"
    )
    L.append("    if r2.get('code') != 200:")
    L.append("        results.append((name, 'SCRIPT_UPDATE_FAIL', r2.get('error') or r2.get('message',''))); continue")
    # 3. 建用户（挂到新脚本下）
    L.append("    r3 = post('/api/scripts/user/add', {'scriptId': sid})")
    L.append("    if r3.get('code') != 200:")
    L.append("        results.append((name, 'USER_ADD_FAIL', r3.get('error') or r3.get('message',''))); continue")
    L.append("    uid = r3.get('userId','')")
    # 4. 设用户配置
    L.append(
        "    r4 = post('/api/scripts/user/update', {'scriptId': sid, 'userId': uid,\n"
        "        'data': {'Info': {'Name': name, 'Server': server, 'Id': aid,\n"
        "                           'Notes': note[:100], 'Status': True}}})"
    )
    L.append("    if r4.get('code') != 200:")
    L.append("        results.append((name, 'USER_UPDATE_FAIL', r4.get('error') or r4.get('message',''))); continue")
    L.append("    results.append((name, 'OK', sid))")
    L.append("    if i % 5 == 0: sys.stderr.write('progress ' + str(i+1) + '/' + str(" + str(len(accounts)) + ") + chr(10))")
    L.append(
        "pathlib.Path(r'E:\\emu_maa_tool\\_script_result.json').write_text(\n"
        "    json.dumps(results, ensure_ascii=False), encoding='utf-8')\n"
        "print('SCRIPTS_DONE')\n"
    )
    return "\n".join(L)


def read_result():
    code = (
        "import pathlib, base64\n"
        "raw = pathlib.Path(r'E:\\emu_maa_tool\\_script_result.json').read_text(encoding='utf-8')\n"
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
    print(f"为 {len(ACCTS)} 个账号建独立脚本（每脚本绑一台模拟器）...")
    script = build(ACCTS, SHARED_SCRIPT, EMULATOR_ID, MAA_PATH)
    r = run_remote_py(script, 600)
    print("远程执行:", r[:100])
    results = read_result()
    if isinstance(results, dict) and results.get("_error"):
        print("读取结果失败:", results["_error"], results.get("_raw",""))
    else:
        ok = [x for x in results if x[1] == "OK"]
        fail = [x for x in results if x[1] != "OK"]
        print(f"\n成功: {len(ok)} / 失败: {len(fail)}")
        for f in fail[:15]:
            print("  ✗", f)
        if ok:
            pathlib.Path(r"D:\1-2\MAAOrch\tools\emu_maa_tool\_scripts_map.json").write_text(
                json.dumps(ok, ensure_ascii=False, indent=2), encoding="utf-8")
            print("脚本映射已存 _scripts_map.json")
