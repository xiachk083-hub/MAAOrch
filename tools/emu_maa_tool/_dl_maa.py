"""目标机下载官方最新 MAA v6.16.8 到 E:\MAA-Multi-download"""
import base64
import json
import urllib.request

CRED = json.load(open(r"C:\Users\xiach\.maorch_deploy.json", encoding="utf-8"))
MANAGER = CRED.get("manager", "http://100.79.173.69:19998")
TOKEN = CRED.get("manager_token", "")


def ex(cmd, t=600):
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
    # 写下载脚本到目标机再执行（避免转义）
    script = (
        "$ErrorActionPreference='Continue'\n"
        "$url='https://github.com/MaaAssistantArknights/MaaAssistantArknights/releases/download/v6.16.8/MAA-v6.16.8-win-x64.zip'\n"
        "$dir='E:\\MAA-Multi-download'\n"
        "New-Item -ItemType Directory -Force -Path $dir | Out-Null\n"
        "$out=Join-Path $dir 'MAA-v6.16.8-win-x64.zip'\n"
        "Write-Output 'DL_START'\n"
        "Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing\n"
        "if (Test-Path $out) { $len=(Get-Item $out).Length; Write-Output ('DL_DONE ' + $len) } else { Write-Output 'DL_FAIL' }\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    ex("[IO.File]::WriteAllText('E:\\MAA-Multi-download\\dl.ps1', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + b64 + "')))", 30)
    print("启动下载...")
    r = ex("Start-Process powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File E:\\MAA-Multi-download\\dl.ps1' -WindowStyle Hidden -RedirectStandardOutput E:\\MAA-Multi-download\\dl.log")
    print(r)
