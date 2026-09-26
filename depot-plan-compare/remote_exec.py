# -*- coding: utf-8 -*-
"""生产机执行命令（manager /api/exec 封装，避免命令行转义地狱）。"""
import sys
import json
import urllib.request

cfg = json.load(open(r"C:\Users\xiach\.maorch_deploy.json", encoding="utf-8"))
mg = cfg["manager"].rstrip("/")


def exec_ps(script: str, timeout: int = 600) -> dict:
    body = json.dumps({"command": script, "timeout": timeout}).encode("utf-8")
    req = urllib.request.Request(
        mg + "/api/exec", data=body,
        headers={"x-manager-token": cfg["manager_token"],
                 "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout + 10)
                      .read().decode("utf-8", "replace"))


if __name__ == "__main__":
    script = sys.stdin.read()
    timeout = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    d = exec_ps(script, timeout)
    print("=== stdout ===")
    print(d.get("stdout", ""))
    print("=== stderr ===")
    print(d.get("stderr", ""))
    if d.get("exit_code"):
        print("exit_code:", d["exit_code"])
