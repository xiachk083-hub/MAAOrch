"""从目标机提取每个账号的任务配置（分小文件读回，规避 stdout 长度限制）"""
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


def run_remote_py(code):
    b64 = base64.b64encode(code.encode("utf-8")).decode()
    return ex('python -c "import base64;exec(base64.b64decode(\'' + b64 + '\').decode())"')


if __name__ == "__main__":
    # 1. 提取任务配置 → 目标机文件
    code1 = (
        "import json, pathlib\n"
        "d = json.load(open(r'E:\\MAAOrch\\models\\config.json', encoding='utf-8'))\n"
        "out = []\n"
        "for a in d.get('accounts', []):\n"
        "    out.append({\n"
        "        'name': a.get('name',''),\n"
        "        'game_client': a.get('game_client',''),\n"
        "        'emu_instance_index': a.get('emu_instance_index',''),\n"
        "        'farm_tag': a.get('farm_tag',''),\n"
        "        'fight_default': a.get('fight_default',''),\n"
        "        'fight_stage': a.get('fight_stage',''),\n"
        "        'task_pipeline': a.get('task_pipeline',''),\n"
        "        'fight_priority': a.get('fight_priority', {}),\n"
        "        'stages': a.get('stages', []),\n"
        "        'suspended': a.get('suspended', False),\n"
        "    })\n"
        "pathlib.Path(r'E:\\emu_maa_tool\\_task_extract.json').write_text(\n"
        "    json.dumps(out, ensure_ascii=False), encoding='utf-8')\n"
        "print('EXTRACT_DONE', len(out))\n"
    )
    print("远程提取:", run_remote_py(code1)[:80])

    # 2. 切分成小文件（每文件 ~2KB base64）
    code2 = (
        "import pathlib, base64\n"
        "raw = pathlib.Path(r'E:\\emu_maa_tool\\_task_extract.json').read_text(encoding='utf-8')\n"
        "b = base64.b64encode(raw.encode('utf-8')).decode()\n"
        "p = pathlib.Path(r'E:\\emu_maa_tool\\_task_parts')\n"
        "p.mkdir(exist_ok=True)\n"
        "for f in p.glob('*.txt'): f.unlink()\n"
        "n = 0\n"
        "for i in range(0, len(b), 2000):\n"
        "    (p / ('p%03d.txt' % (i//2000))).write_text(b[i:i+2000], encoding='ascii')\n"
        "    n += 1\n"
        "print('PARTS', n)\n"
    )
    r2 = run_remote_py(code2)
    print("切分:", r2[:60])
    n_parts = int(r2.strip().splitlines()[-1].split()[-1])

    # 3. 逐文件读回
    b64_all = ""
    for i in range(n_parts):
        fname = "p%03d.txt" % i
        piece = run_remote_py(
            "import pathlib\n"
            "print(pathlib.Path(r'E:\\emu_maa_tool\\_task_parts\\%s').read_text(encoding='ascii'))\n" % fname
        )
        b64_all += piece.strip()

    accts = json.loads(base64.b64decode(b64_all).decode("utf-8"))
    out = r"D:\1-2\MAAOrch\tools\emu_maa_tool\_tasks_from_maorch.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(accts, f, ensure_ascii=False, indent=2)

    from collections import Counter

    tags = Counter(a["farm_tag"] or "(无)" for a in accts)
    print("账号数:", len(accts))
    print("farm_tag 分布:", dict(tags))
    for a in accts[:18]:
        print(
            f"  {a['name']:<22} tag={a['farm_tag'] or '-':<10} "
            f"def={a['fight_default'] or '-':<8} suspend={a['suspended']}"
        )
