"""修正版：用 AUTO-MAS python 手动执行调度，捕获 MaaManager 真实异常。"""
import base64
import json
import urllib.request

CRED = json.load(open(r"C:\Users\xiach\.maorch_deploy.json", encoding="utf-8"))
MANAGER = CRED.get("manager", "http://100.79.173.69:19998")
TOKEN = CRED.get("manager_token", "")


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
            return "HTTPERR " + e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            return "ERR " + str(e)[:200]


if __name__ == "__main__":
    script = r'''
import sys, asyncio, traceback
sys.path.insert(0, r"E:\AUTO-MAS")
sys.path.insert(0, r"E:\AUTO-MAS\app")
import os
os.chdir(r"E:\AUTO-MAS")  # 关键：cwd 必须是 AUTO-MAS 安装目录

async def main():
    from app.core import Config
    from app.core.task_manager import TaskManager
    await Config.init_config()
    sid = "bfd6d8e0-8771-4bfe-9a82-ef1931371f2a"
    import uuid
    suid = uuid.UUID(sid)
    sc = await Config.ScriptConfig.get(suid)
    print("脚本类型:", type(sc).__name__)
    print("Path:", sc.get("Info", "Path") if hasattr(sc, "get") else "?")
    tm = TaskManager()
    try:
        tid = await tm.add_task("AutoProxy", sid)
        print("add_task OK:", tid)
        # 等任务真正跑（accomplish 会等 main_task 完成）
        task = tm.task_handler.get(tid)
        if task:
            await asyncio.wait_for(task.accomplish.wait(), timeout=30)
            print("任务已完成")
    except Exception as e:
        print("EXCEPTION:", type(e).__name__, str(e))
        traceback.print_exc()

asyncio.run(main())
'''
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    ex("[IO.File]::WriteAllText('E:\\emu_maa_tool\\_diag_exec.py', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + b64 + "')))", 30)
    r = ex(r"E:\AUTO-MAS\environment\python\python.exe E:\emu_maa_tool\_diag_exec.py", 120)
    print(r[:3500])
