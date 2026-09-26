# -*- coding: utf-8 -*-
"""maa-channel MCP server — 目标机远程控制（stdio 模式，经 SSH 使用）。

客户端连接方式（任何 MCP 客户端）:
    ssh Administrator@100.79.173.69 python E:\\maaorch-channel\\mcp_server.py

工具: exec_command / copy_dir / read_file / write_file / list_dir / file_exists
安全: 鉴权由 SSH 承担（持有目标机 SSH 密钥者可用），服务器本身不另设 token。
"""
import base64
import os
import subprocess
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("maa-channel")


def _run(cmd: str) -> str:
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, timeout=900,
        )
        out = (p.stdout or b"").decode("utf-8", "replace")
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        return "rc=%d\n%s%s" % (p.returncode, out[-50000:], ("\n[ERR] " + err[-5000:]) if err else "")
    except Exception as e:
        return "error: %s: %s" % (type(e).__name__, str(e))


@mcp.tool()
def exec_command(cmd: str) -> str:
    """在目标机执行一条 PowerShell 命令，返回 stdout/stderr/退出码。用于管理进程、查状态、跑脚本。"""
    return _run(cmd)


@mcp.tool()
def copy_dir(src: str, dst: str) -> str:
    """用 robocopy 整目录复制（如复制 MAA 副本 MAA_1 -> MAA_10）。src/dst 为 Windows 路径。"""
    return _run("robocopy '%s' '%s' /E /R:1 /W:1 /NFL /NDL /NP" % (src.replace("'", "''"), dst.replace("'", "''")))


@mcp.tool()
def read_file(path: str, max_len: int = 200000) -> str:
    """读取目标机文件（先按 UTF-8，失败按 GBK，二进制则返回 base64）。max_len 限制读取字节数。"""
    try:
        with open(path, "rb") as f:
            data = f.read(max_len)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return data.decode("gbk")
            except Exception:
                return "[binary] base64: " + base64.b64encode(data).decode("ascii")
    except Exception as e:
        return "error: %s: %s" % (type(e).__name__, str(e))


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """写入目标机文件（UTF-8），自动创建父目录。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return "ok"
    except Exception as e:
        return "error: %s: %s" % (type(e).__name__, str(e))


@mcp.tool()
def list_dir(path: str) -> str:
    """列出目标机目录内容（每行一项）。"""
    try:
        return "\n".join(sorted(os.listdir(path)))
    except Exception as e:
        return "error: %s: %s" % (type(e).__name__, str(e))


@mcp.tool()
def file_exists(path: str) -> bool:
    """检查目标机路径是否存在。"""
    return os.path.exists(path)


if __name__ == "__main__":
    mcp.run()
