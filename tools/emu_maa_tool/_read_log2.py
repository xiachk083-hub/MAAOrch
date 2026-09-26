"""可靠读回目标机 AUTO-MAS 日志（每次一个小文件，严格解析）"""
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


def run_remote_py(code):
    b64 = base64.b64encode(code.encode("utf-8")).decode()
    return ex('python -c "import base64;exec(base64.b64decode(\'' + b64 + '\').decode())"')


if __name__ == "__main__":
    # 1. 目标机把日志末尾 100 行写到单文件（UTF-8）
    r = run_remote_py(
        "import pathlib\n"
        "p = pathlib.Path(r'E:\\AUTO-MAS\\debug\\app.log')\n"
        "raw = p.read_bytes()\n"
        "txt = raw.decode('utf-8', errors='replace')\n"
        "lines = txt.splitlines()[-100:]\n"
        "pathlib.Path(r'E:\\emu_maa_tool\\_tail.txt').write_text('\\n'.join(lines), encoding='utf-8')\n"
        "print('TAIL_OK')\n"
    )
    print("步骤1:", r[:50])
    # 2. 逐段读回（每段 1400 字符 base64，独立文件）
    r2 = run_remote_py(
        "import pathlib, base64\n"
        "raw = pathlib.Path(r'E:\\emu_maa_tool\\_tail.txt').read_text(encoding='utf-8')\n"
        "b = base64.b64encode(raw.encode('utf-8')).decode()\n"
        "p = pathlib.Path(r'E:\\emu_maa_tool\\_tail_parts')\n"
        "p.mkdir(exist_ok=True)\n"
        "for f in p.glob('*.txt'): f.unlink()\n"
        "n = 0\n"
        "for i in range(0, len(b), 1400):\n"
        "    (p / ('t%03d.txt' % (i//1400))).write_text(b[i:i+1400], encoding='ascii')\n"
        "    n += 1\n"
        "print('PARTS', n)\n"
    )
    n = int(r2.strip().splitlines()[-1].split()[-1])
    print("步骤2: 分片数", n)
    b64_all = ""
    for i in range(n):
        piece = run_remote_py(
            "import pathlib\n"
            "print(pathlib.Path(r'E:\\emu_maa_tool\\_tail_parts\\t%03d.txt').read_text(encoding='ascii'))\n" % i
        )
        b64_all += piece.strip()
    try:
        txt = base64.b64decode(b64_all).decode("utf-8", errors="replace")
        with open(r"D:\1-2\MAAOrch\tools\emu_maa_tool\_tail_decoded.txt", "w", encoding="utf-8") as f:
            f.write(txt)
        print("=== 日志尾部 ===")
        print(txt)
    except Exception as e:
        print("解析失败:", e, "长度:", len(b64_all))
