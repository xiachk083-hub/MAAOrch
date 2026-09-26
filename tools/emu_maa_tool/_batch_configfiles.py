"""批量给所有脚本生成 ConfigFile 基线（从 MAA_1 config 模板复制）"""
import base64
import json
import pathlib
import urllib.request

CRED = json.load(open(r"C:\Users\xiach\.maorch_deploy.json", encoding="utf-8"))
MANAGER = CRED.get("manager", "http://100.79.173.69:19998")
TOKEN = CRED.get("manager_token", "")

# 脚本列表: [name, 'OK', scriptId]
SCRIPTS = json.loads(
    pathlib.Path(r"D:\1-2\MAAOrch\tools\emu_maa_tool\_scripts_map.json")
    .read_text(encoding="utf-8")
)


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


if __name__ == "__main__":
    # 生成脚本：把 MAA_1 config 复制到每个脚本的 ConfigFile
    script_ids = [s[2] for s in SCRIPTS]
    code = (
        "import pathlib, shutil, json\n"
        "BS = chr(92)\n"
        "src_cfg = pathlib.Path('E:' + BS + 'MAA-Multi' + BS + 'MAA_1' + BS + 'config')\n"
        "sids = " + repr(script_ids) + "\n"
        "ok = 0; fail = []\n"
        "for sid in sids:\n"
        "    dst = pathlib.Path('E:' + BS + 'AUTO-MAS' + BS + 'data' + BS + sid + BS + 'Default' + BS + 'ConfigFile')\n"
        "    try:\n"
        "        dst.mkdir(parents=True, exist_ok=True)\n"
        "        for f in src_cfg.glob('*.json'):\n"
        "            shutil.copy2(str(f), str(dst / f.name))\n"
        "        for b in dst.glob('*.bak*'):\n"
        "            b.unlink(missing_ok=True)\n"
        "        ok += 1\n"
        "    except Exception as e:\n"
        "        fail.append(sid[:8] + ':' + str(e)[:50])\n"
        "print('DONE ok=' + str(ok) + ' fail=' + str(len(fail)))\n"
        "if fail:\n"
        "    print(';'.join(fail[:10]))\n"
    )
    r = run_remote_py(code, 300)
    print(r[:300])
