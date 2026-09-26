"""桌面客户端：pywebview 窗口，加载服务端页面。

用法:
    python client/app.py                    # 本机（默认 127.0.0.1:18889）
    python client/app.py http://100.79.173.69:18889            # 远程
    python client/app.py http://100.79.173.69:18889 --token xxx # 远程+token

token 优先顺序: --token 参数 > client/token.txt > 空（本机不鉴权）。
"""
from __future__ import annotations
import sys
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOL_ROOT))
TOKEN_FILE = Path(__file__).resolve().parent / "token.txt"


def _load_token() -> str:
    # --token 参数
    if "--token" in sys.argv:
        i = sys.argv.index("--token")
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1].strip()
    # client/token.txt 文件
    try:
        if TOKEN_FILE.exists():
            t = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if t:
                return t
    except Exception:
        pass
    return ""


def main() -> None:
    try:
        import webview
    except ImportError:
        print("缺少 pywebview，请先安装: pip install pywebview")
        sys.exit(1)

    # 提取连接地址（第一个非 --token 开头的参数）
    url = "http://127.0.0.1:18889"
    for a in sys.argv[1:]:
        if a.startswith("http://") or a.startswith("https://"):
            url = a
            break

    token = _load_token()
    sep = "&" if "?" in url else "?"
    if token:
        url = f"{url}{sep}token={token}"
    print(f"[MAAOrch tool] opening: {url}")
    print(f"[MAAOrch tool] token: {'已加载' if token else '无（本机模式）'}")

    webview.create_window("MAAOrch 小工具", url, width=1280, height=800,
                          min_size=(960, 640), resizable=True)
    webview.start()


if __name__ == "__main__":
    main()
