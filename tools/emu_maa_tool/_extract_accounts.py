"""从目标机分批读回账号清单（每次读文件一段，规避 manager stdout 限制）"""
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
        return (r.get("stdout", "") + r.get("stderr", "")).strip()
    except Exception as e:
        return "ERR " + str(e)[:150]


def run_py(code):
    b64 = base64.b64encode(code.encode("utf-8")).decode()
    return ex('python -c "import base64;exec(base64.b64decode(\'' + b64 + '\').decode())"')


if __name__ == "__main__":
    # 目标机把账号 JSON 切成若干 base64 片段文件（每片 2KB 内，含序号）
    run_py(
        "import json, pathlib, base64\n"
        "d = json.load(open(r'E:\\MAAOrch\\models\\config.json', encoding='utf-8'))\n"
        "out = []\n"
        "for a in d.get('accounts', []):\n"
        "    out.append({'name': a.get('name',''), 'game_client': a.get('game_client',''),\n"
        "               'note': a.get('note',''), 'account_switch': a.get('account_switch',''),\n"
        "               'emu_instance_index': a.get('emu_instance_index','')})\n"
        "raw = json.dumps(out, ensure_ascii=False)\n"
        "b = base64.b64encode(raw.encode('utf-8')).decode()\n"
        "p = pathlib.Path(r'E:\\emu_maa_tool\\_acct_parts')\n"
        "p.mkdir(exist_ok=True)\n"
        "for f in p.glob('*.txt'): f.unlink()\n"
        "for i in range(0, len(b), 2000):\n"
        "    (p / ('p%03d.txt' % (i//2000))).write_text(b[i:i+2000], encoding='ascii')\n"
        "print('PARTS', len(b)//2000 + 1)\n"
    )
    # 列出片段数
    n_parts = run_py(
        "import pathlib\n"
        "p = pathlib.Path(r'E:\\emu_maa_tool\\_acct_parts')\n"
        "print(len(list(p.glob('*.txt'))))\n"
    )
    n = int(n_parts.strip().splitlines()[-1])
    print("片段数:", n)
    # 逐片读回
    b64_all = ""
    for i in range(n):
        fname = "p%03d.txt" % i
        piece = run_py(
            "import pathlib\n"
            "print(pathlib.Path(r'E:\\emu_maa_tool\\_acct_parts\\%s').read_text(encoding='ascii'))\n" % fname
        )
        b64_all += piece.strip()
    raw = base64.b64decode(b64_all).decode("utf-8")
    accts = json.loads(raw)
    out = r"D:\1-2\MAAOrch\tools\emu_maa_tool\_accounts_from_maorch.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(accts, f, ensure_ascii=False, indent=2)
    print("提取账号数:", len(accts))
    for a in accts[:12]:
        print(
            " ",
            a["name"],
            "|",
            a["game_client"],
            "| emu:",
            a["emu_instance_index"],
            "| switch:",
            repr(a["account_switch"][:14]),
            "| note:",
            repr(a["note"][:14]),
        )
