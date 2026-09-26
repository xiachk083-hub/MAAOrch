# -*- coding: utf-8 -*-
"""从 MRFZ-0000 拉取文件（ssh + base64，不依赖 sftp）。用法: python _pull.py <远程路径> <本地路径>"""
import base64
import os
import subprocess
import sys

KEY = os.path.join(os.path.expanduser("~"), ".ssh", "maaorch_target")
HOST = "Administrator@100.79.173.69"


def main():
    remote, local = sys.argv[1], sys.argv[2]
    cmd = "powershell -NoProfile -Command \"[Convert]::ToBase64String([IO.File]::ReadAllBytes('%s'))\"" % remote
    p = subprocess.run(
        ["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", HOST, cmd],
        capture_output=True, timeout=180,
    )
    if p.returncode != 0:
        print("SSH-FAIL", p.returncode, p.stderr.decode("utf-8", "replace")[:300])
        return 2
    b64 = b"".join(p.stdout.split()).decode("ascii")
    data = base64.b64decode(b64)
    open(local, "wb").write(data)
    print("OK %s -> %s (%d bytes)" % (remote, local, len(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
