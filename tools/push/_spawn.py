"""**完全脱离地**启动后台进程（给 auto_push / screen_watch 这类长跑任务用）。

为什么不用 `nohup ... &`（2026-09-17）: 那样起的进程挂在调用它的 shell 下，shell 被
取消/回收时**会连带把它们一起收走** —— 三个号跑一半集体消失，日志戛然而止，
看上去像"自己崩了"。这里用 Windows 的 DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP，
父进程退出不影响它，stdout/stderr 重定向到自己那个日志文件。

用法:
    python _spawn.py "日志绝对路径::要跑的命令行" ["日志2::命令2" ...]
    例: python _spawn.py "…/ap_V.log::cd /d D:\\1-2\\MAAOrch && python …"
"""
from __future__ import annotations

import subprocess
import sys

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for spec in sys.argv[1:]:
        log, _, cmd = spec.partition("::")
        if not cmd:
            raise SystemExit(f"参数要写成 '日志::命令'，收到的是 {spec!r}")
        # **在 cmd 里做重定向**，不要把文件句柄传给子进程（2026-09-17 实测：传句柄那版
        # 日志永远是 0 字节 —— 子进程拿到的是无效句柄，而 cmd 自己重定向这条路今晚一直好用）。
        fh = None
        p = subprocess.Popen(["cmd", "/c", f'{cmd} >> "{log}" 2>&1'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, close_fds=True,
                             creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
        print(f"已脱离启动 pid={p.pid} → {log}")
        print(f"    {cmd}")


if __name__ == "__main__":
    main()
