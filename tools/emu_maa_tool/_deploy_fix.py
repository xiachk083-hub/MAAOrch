"""临时部署脚本：部署最新代码到目标机 E:\emu_maa_tool"""
import base64
import json
import pathlib
import urllib.request

CRED = json.load(open(r"C:\Users\xiach\.maorch_deploy.json", encoding="utf-8"))
MANAGER = CRED.get("manager", "http://100.79.173.69:19998")
TOKEN = CRED.get("manager_token", "")

REMOTE = r"E:\emu_maa_tool"


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
            return "ERR " + str(e)[:200]


def deploy(rel, local_path):
    ex("[IO.File]::WriteAllText('E:\\emu_maa_tool\\_dep.b64', '')")
    content = pathlib.Path(local_path).read_bytes()
    b64 = base64.b64encode(content).decode()
    for i in range(0, len(b64), 12000):
        r = ex("Add-Content -Path 'E:\\emu_maa_tool\\_dep.b64' -Value '" + b64[i:i + 12000] + "' -NoNewline", 120)
        if "HTTPERR" in r or "ERR" in r:
            return "chunk fail: " + r[:150]
    remote_file = REMOTE + "\\" + rel
    decoder = (
        "import base64\n"
        "raw = open(r'E:\\emu_maa_tool\\_dep.b64', encoding='ascii').read()\n"
        "open(r'" + remote_file + "', 'wb').write(base64.b64decode(raw))\n"
        "print('WROTE')\n"
    )
    db = base64.b64encode(decoder.encode()).decode()
    ex("[IO.File]::WriteAllText('E:\\emu_maa_tool\\_dep2.py', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + db + "')))")
    r = ex("python 'E:\\emu_maa_tool\\_dep2.py'", 60)
    ex("Remove-Item 'E:\\emu_maa_tool\\_dep.b64','E:\\emu_maa_tool\\_dep2.py' -ErrorAction SilentlyContinue")
    return r


if __name__ == "__main__":
    base = pathlib.Path(r"D:\1-2\MAAOrch\tools\emu_maa_tool")
    print("maa_api.py:", deploy("server\\maa_api.py", base / "server" / "maa_api.py"))
    print("main.py:", deploy("server\\main.py", base / "server" / "main.py"))
    print("index.html:", deploy("web\\index.html", base / "web" / "index.html"))
