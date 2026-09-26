"""批量配置：48 脚本 MAA 路径均分到 MAA_1~MAA_8 + 建 8 个串行队列。

架构：8 个 MAA 多开副本，每个 MAA 一个队列，队列内账号串行（一个跑完下一个）。
48 账号 / 8 = 每队列 6 个。
"""
import base64
import json
import pathlib
import urllib.request

CRED = json.load(open(r"C:\Users\xiach\.maorch_deploy.json", encoding="utf-8"))
MANAGER = CRED.get("manager", "http://100.79.173.69:19998")
TOKEN = CRED.get("manager_token", "")
NUM_MAAS = 8

# 脚本列表: [name, 'OK', scriptId]
SCRIPTS = json.loads(
    pathlib.Path(r"D:\1-2\MAAOrch\tools\emu_maa_tool\_scripts_map.json")
    .read_text(encoding="utf-8")
)
# 每个脚本名 → scriptId
script_by_name = {s[0]: s[2] for s in SCRIPTS}
# 账号顺序（按录入顺序均分）
names = [s[0] for s in SCRIPTS]


def ex(cmd, t=120):
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


def build_script():
    """生成目标机执行的批量配置脚本。"""
    # 分组: 每 MAA 6 个账号
    groups = []
    for i in range(NUM_MAAS):
        chunk = names[i * 6:(i + 1) * 6]
        groups.append({
            "maa": f"E:\\MAA-Multi\\MAA_{i+1}",
            "queue_name": f"队列{i+1}",
            "scripts": [{"name": n, "sid": script_by_name[n]} for n in chunk],
        })
    L = []
    L.append("import urllib.request, json, sys")
    L.append("BASE='http://127.0.0.1:36163'")
    L.append(
        "def post(path, body=None):\n"
        "    data = json.dumps(body).encode() if body is not None else b'{}'\n"
        "    req = urllib.request.Request(BASE+path, data=data, method='POST',\n"
        "        headers={'Content-Type':'application/json'})\n"
        "    try:\n"
        "        r = urllib.request.urlopen(req, timeout=30)\n"
        "        return json.loads(r.read())\n"
        "    except Exception as e:\n"
        "        try: return {'error': e.read().decode('utf-8','replace')[:150]}\n"
        "        except: return {'error': str(e)[:120]}\n"
    )
    L.append("results=[]")
    # 1. 更新所有脚本 MAA 路径
    L.append("groups = %r" % groups)
    L.append("for gi, g in enumerate(groups):")
    L.append("    for sc in g['scripts']:")
    L.append("        r = post('/api/scripts/update', {'scriptId': sc['sid'], 'data': {'Info': {'Name': sc['name'], 'Path': g['maa']}}})")
    L.append("        if r.get('code') != 200:")
    L.append("            results.append(('SCRIPT_PATH', sc['name'], r.get('error') or r.get('message','')))")
    L.append("        else:")
    L.append("            results.append(('SCRIPT_PATH', sc['name'], 'OK'))")
    # 2. 每个 MAA 建一个队列
    L.append("    q = post('/api/queue/add')")
    L.append("    qid = q.get('queueId','')")
    L.append("    if not qid:")
    L.append("        results.append(('QUEUE_ADD', g['queue_name'], 'FAIL')); continue")
    L.append("    # 设队列名")
    L.append("    r = post('/api/queue/update', {'queueId': qid, 'data': {'Info': {'Name': g['queue_name'], 'TimeEnabled': False, 'StartUpEnabled': False, 'AfterAccomplish': 'NoAction'}}})")
    L.append("    # 逐个加脚本到队列")
    L.append("    for sc in g['scripts']:")
    L.append("        qi = post('/api/queue/item/add', {'queueId': qid})")
    L.append("        qiid = qi.get('queueItemId','')")
    L.append("        if qiid:")
    L.append("            post('/api/queue/item/update', {'queueId': qid, 'queueItemId': qiid, 'data': {'Info': {'ScriptId': sc['sid']}}})")
    L.append("    results.append(('QUEUE', g['queue_name'], qid, len(g['scripts'])))")
    L.append("    sys.stderr.write('queue ' + g['queue_name'] + ' done\\n')")
    # 写结果
    L.append(
        "import pathlib\n"
        "pathlib.Path(r'E:\\emu_maa_tool\\_batch_config_result.json').write_text(\n"
        "    json.dumps(results, ensure_ascii=False), encoding='utf-8')\n"
        "print('CONFIG_DONE')\n"
    )
    return "\n".join(L)


def read_result():
    code = (
        "import pathlib, base64\n"
        "raw = pathlib.Path(r'E:\\emu_maa_tool\\_batch_config_result.json').read_text(encoding='utf-8')\n"
        "b = base64.b64encode(raw.encode('utf-8')).decode()\n"
        "p = pathlib.Path(r'E:\\emu_maa_tool\\_cfg_parts')\n"
        "p.mkdir(exist_ok=True)\n"
        "for f in p.glob('*.txt'): f.unlink()\n"
        "for i in range(0, len(b), 1400):\n"
        "    (p / ('c%03d.txt' % (i//1400))).write_text(b[i:i+1400], encoding='ascii')\n"
        "print('PARTS', len(b)//1400+1)\n"
    )
    r = run_remote_py(code, 120)
    n = int(r.strip().splitlines()[-1].split()[-1])
    b64_all = ""
    for i in range(n):
        piece = run_remote_py(
            "import pathlib\n"
            "print(pathlib.Path(r'E:\\emu_maa_tool\\_cfg_parts\\c%03d.txt').read_text(encoding='ascii'))\n" % i
        )
        b64_all += piece.strip()
    return json.loads(base64.b64decode(b64_all).decode("utf-8"))


if __name__ == "__main__":
    print(f"批量配置 {len(names)} 个脚本 → {NUM_MAAS} 个 MAA/队列 ...")
    script = build_script()
    r = run_remote_py(script, 600)
    print("远程:", r[:80])
    results = read_result()
    path_ok = [x for x in results if x[1] == "SCRIPT_PATH" and x[2] == "OK"]
    path_fail = [x for x in results if x[1] == "SCRIPT_PATH" and x[2] != "OK"]
    queues = [x for x in results if x[1] == "QUEUE"]
    print(f"\n脚本路径: 成功 {len(path_ok)} / 失败 {len(path_fail)}")
    for f in path_fail[:8]:
        print("  ✗", f)
    print(f"\n队列: {len(queues)} 个")
    for q in queues:
        print("  ✓", q[2], "|", q[3], "个脚本")
    # 存队列映射
    queue_map = {q[2]: {"name": q[2], "scripts": q[3]} for q in queues}
    pathlib.Path(r"D:\1-2\MAAOrch\tools\emu_maa_tool\_queues_map.json").write_text(
        json.dumps(queue_map, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n队列映射已存 _queues_map.json")
