"""查 MAA source 的 asst.log（看 MAA 为什么没跑任务）"""
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


def run_py(code):
    b64 = base64.b64encode(code.encode("utf-8")).decode()
    return ex('python -c "import base64;exec(base64.b64decode(\'' + b64 + '\').decode())"')


if __name__ == "__main__":
    # 读 source/debug/asst.log 尾部
    r = run_py(
        "import pathlib\n"
        "p = pathlib.Path(r'E:\\MAAOrch\\services\\maa\\source\\debug\\asst.log')\n"
        "if p.exists():\n"
        "    raw = p.read_bytes()\n"
        "    txt = raw.decode('utf-8', errors='replace')\n"
        "    lines = txt.splitlines()[-40:]\n"
        "    pathlib.Path(r'E:\\emu_maa_tool\\_asst_tail.txt').write_text('\\n'.join(lines), encoding='utf-8')\n"
        "    print('ASST_OK', p.stat().st_size)\n"
        "else:\n"
        "    print('NO_ASST')\n"
    )
    print("步骤1:", r[:60])
    r2 = run_py(
        "import pathlib, base64\n"
        "raw = pathlib.Path(r'E:\\emu_maa_tool\\_asst_tail.txt').read_text(encoding='utf-8')\n"
        "b = base64.b64encode(raw.encode('utf-8')).decode()\n"
        "p = pathlib.Path(r'E:\\emu_maa_tool\\_asst_parts')\n"
        "p.mkdir(exist_ok=True)\n"
        "for f in p.glob('*.txt'): f.unlink()\n"
        "n=0\n"
        "for i in range(0, len(b), 1400):\n"
        "    (p / ('a%03d.txt' % (i//1400))).write_text(b[i:i+1400], encoding='ascii')\n"
        "    n+=1\n"
        "print('PARTS', n)\n"
    )
    n = int(r2.strip().splitlines()[-1].split()[-1])
    b64_all = ""
    for i in range(n):
        piece = run_py(
            "import pathlib\n"
            "print(pathlib.Path(r'E:\\emu_maa_tool\\_asst_parts\\a%03d.txt').read_text(encoding='ascii'))\n" % i
        )
        b64_all += piece.strip()
    try:
        txt = base64.b64decode(b64_all).decode("utf-8", errors="replace")
        print("=== asst.log 尾部 ===")
        print(txt)
    except Exception as e:
        print("解析失败:", e)
