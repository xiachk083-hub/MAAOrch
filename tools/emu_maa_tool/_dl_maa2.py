"""目标机用 python 直接下载 MAA v6.16.8"""
import base64
import json
import urllib.request

CRED = json.load(open(r"C:\Users\xiach\.maorch_deploy.json", encoding="utf-8"))
MANAGER = CRED.get("manager", "http://100.79.173.69:19998")
TOKEN = CRED.get("manager_token", "")


def ex(cmd, t=900):
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
            return "HTTPERR " + e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            return "ERR " + str(e)[:200]


if __name__ == "__main__":
    # 后台用 python 下载（写到日志文件）
    script = (
        "import urllib.request, os, time\n"
        "url='https://github.com/MaaAssistantArknights/MaaAssistantArknights/releases/download/v6.16.8/MAA-v6.16.8-win-x64.zip'\n"
        "out=r'E:\\\\MAAdl\\\\MAA-v6.16.8-win-x64.zip'\n"
        "log=r'E:\\\\MAAdl\\\\dl.log'\n"
        "open(log,'w').write('START\\n')\n"
        "try:\n"
        "    urllib.request.urlretrieve(url, out)\n"
        "    sz=os.path.getsize(out)\n"
        "    open(log,'a').write('DONE '+str(sz)+'\\n')\n"
        "except Exception as e:\n"
        "    open(log,'a').write('FAIL '+str(e)+'\\n')\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    # 写脚本文件，后台执行
    r = ex("[IO.File]::WriteAllText('E:\\MAAdl\\_dl.py', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + b64 + "')))", 30)
    print("写脚本:", r[:50])
    r = ex("Start-Process python -ArgumentList 'E:\\MAAdl\\_dl.py' -WorkingDirectory E:\\MAAdl -WindowStyle Hidden", 30)
    print("启动下载:", r[:50])
