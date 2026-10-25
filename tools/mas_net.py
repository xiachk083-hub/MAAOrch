#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""统一 HTTP 客户端：显式绕系统代理 + 统一异常契约（G2 根因修复，t_f79db125）。

背景（docs/MAS_SHIFT_GAPS.md §G2）：
  本机系统代理（`getproxies()` → `http://127.0.0.1:7890`）会把发往内网
  （100.79.173.69 / 127.0.0.1）的 urllib 请求也劫持走代理，表现为间歇
  `502 Bad Gateway`（空 body、每次卡 5s）或 WinError `10054` 连接重置；
  curl 只认环境变量代理、不读系统代理，故此前「curl 好、urllib 坏」即此。
  修复口径：**所有直连内网的 urllib 请求改走本模块的 opener**
  （`build_opener(ProxyHandler({}))`，显式禁代理）。

用法（库）：

    from mas_net import post_json, get_json, get_text, request
    data = post_json("http://100.79.173.69:36163/api/scripts/get",
                     {"scriptId": None}, timeout=30)

用法（自检 / 现场取证）：

    python tools/mas_net.py --selftest    # 离线：假 transport，不碰网络
    python tools/mas_net.py --ab-check    # 现场 A/B：默认路径 vs 绕代理（只读 POST /api/scripts/get；
                                          #   含「死代理确定性差分」第三相，机制可重复自证）

契约：
  * `request()` 返回 `NetResponse(status, headers, body)`；HTTP >= 400（含假 transport 直回的 4xx/5xx）
    抛 `NetError(kind="http_error")`，带 `status` / `reason` / `body`（原始字节，供取证）。
  * 连接层失败抛 `NetError(kind="timeout"|"transport")`；应答不是合法 JSON 抛 `NetError(kind="bad_json")`。
  * `transport` 参数可注入（测试用）：`transport(req, timeout) -> 类 HTTPResponse 对象`
    （只需 `.read() -> bytes`；可选 `.status`/`.code`/`.headers`/`.close()`）。
  * 调用方原有的 `--timeout` 语义原样保留（本模块只做透传，不改各工具 CLI 口径）。

退出码：--selftest 0=PASS / 3=失败；--ab-check 0=绕代理全通 / 2=绕代理有失败（fail-closed）。
末行判据：NET_SELFTEST=PASS|FAIL ・ NET_AB=PASS|FAIL。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 30.0
API_BASE = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")

# 唯一 opener：显式空 ProxyHandler —— 任何直连内网的请求都必须经过它（G2）。
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class NetError(Exception):
    """统一网络异常。

    kind:
      * ``http_error`` —— HTTP 状态 >= 400（status/reason/body 一并带上 ）
      * ``timeout``    —— 连接/读超时
      * ``transport``  —— 连接重置/不可达等其它传输层错误
      * ``bad_json``   —— 应答不是合法 JSON
    """

    def __init__(self, kind: str, message: str, *, status: int | None = None,
                 reason: str | None = None, body: bytes = b"", url: str = ""):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.reason = reason
        self.body = body or b""
        self.url = url

    def detail(self) -> str:
        """给审计/日志用的一行摘要（不含 body 全文）。"""
        return "%s: %s" % (self.kind, self)


class NetResponse:
    """一次请求的最小应答面（status / headers / body）。"""

    __slots__ = ("status", "headers", "body", "url")

    def __init__(self, status, headers, body: bytes, url: str = ""):
        self.status = status
        self.headers = headers
        self.body = body
        self.url = url

    @property
    def text(self) -> str:
        return (self.body or b"").decode("utf-8", "replace")

    def json(self):
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as e:
            raise NetError("bad_json", "应答不是合法 JSON: %s" % e,
                           status=self.status, url=self.url) from e


def _open(req: urllib.request.Request, timeout: float, transport=None):
    """打开请求：默认走模块 opener（绕代理）；测试可注入 transport。"""
    if transport is not None:
        return transport(req, timeout)
    return opener.open(req, timeout=timeout)


def request(method: str, url: str, data=None, headers: dict | None = None,
            timeout: float = DEFAULT_TIMEOUT, transport=None) -> NetResponse:
    """单次 HTTP 请求（走绕代理 opener）。

    data: bytes / str / None（GET 用 None；JSON 体请用 post_json）。
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=dict(headers or {}),
                                 method=method.upper())
    try:
        resp = _open(req, float(timeout), transport)
    except urllib.error.HTTPError as e:
        detail = b""
        try:
            detail = e.read() or b""
        except Exception:  # noqa: BLE001
            pass
        raise NetError("http_error", "HTTP %s %s" % (e.code, e.reason), status=int(e.code),
                       reason=str(e.reason), body=detail, url=url) from e
    except urllib.error.URLError as e:
        kind = "timeout" if isinstance(e.reason, (socket.timeout, TimeoutError)) else "transport"
        raise NetError(kind, "URLError: %s" % (e.reason,), url=url) from e
    except (socket.timeout, TimeoutError) as e:
        raise NetError("timeout", "%s: %s" % (type(e).__name__, e), url=url) from e
    except OSError as e:
        raise NetError("transport", "%s: %s" % (type(e).__name__, e), url=url) from e
    try:
        try:
            body = resp.read() or b""
        except (socket.timeout, TimeoutError) as e:
            raise NetError("timeout", "读应答超时: %s: %s" % (type(e).__name__, e), url=url) from e
        except OSError as e:
            raise NetError("transport", "读应答失败: %s: %s" % (type(e).__name__, e), url=url) from e
    finally:
        closer = getattr(resp, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass
    status = getattr(resp, "status", None)
    if status is None:
        status = getattr(resp, "code", None)
    if isinstance(status, int) and status >= 400:  # 假 transport 直回错误码时与真 urlopen 同口径
        raise NetError("http_error", "HTTP %s" % status, status=status,
                       body=body, url=url)
    return NetResponse(status, getattr(resp, "headers", None), body, url)


def post_json(url: str, body=None, timeout: float = DEFAULT_TIMEOUT,
              headers: dict | None = None, transport=None) -> dict:
    """POST JSON 并解析 JSON 应答（body=None ⇒ ``{}``）。"""
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    data = json.dumps(body if body is not None else {}).encode("utf-8")
    resp = request("POST", url, data=data, headers=h, timeout=timeout, transport=transport)
    return resp.json()


def get_json(url: str, timeout: float = DEFAULT_TIMEOUT,
             headers: dict | None = None, transport=None) -> dict:
    """GET 并解析 JSON 应答。"""
    return request("GET", url, headers=headers, timeout=timeout, transport=transport).json()


def get_text(url: str, timeout: float = DEFAULT_TIMEOUT,
             headers: dict | None = None, transport=None) -> str:
    """GET 并返回文本（utf-8, replace）。"""
    return request("GET", url, headers=headers, timeout=timeout, transport=transport).text


# ---------------------------------------------------------------- 离线自检

class _FakeResp:
    """假应答：只需 .read()/.status/.headers/.close()。"""

    def __init__(self, body: bytes = b"", status: int = 200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers if headers is not None else {}
        self.closed = False

    def read(self, *_a):
        return self.body

    def close(self):
        self.closed = True


def run_selftest() -> int:
    """离线自检（假 transport / 假 opener，零网络、零写入）。"""
    global opener
    checks: list = []

    def check(cond, msg):
        checks.append(bool(cond))
        print(("  PASS " if cond else "  FAIL ") + msg)

    print("mas_net selftest —— 绕代理契约")

    # 1) 绕代理不变量：本 opener 不带任何「真代理」handler。
    #    机制说明：build_opener(ProxyHandler({})) 不是「挂一个空 handler」，而是让默认
    #    ProxyHandler（读 getproxies()）**不被构造**；空 dict 的 ProxyHandler 连
    #    <proto>_open 方法都不会注册（CPython 按 proxies 逐项 setattr），故 opener.handlers
    #    里也不会有它 —— 不变量要按「有没有非空代理 handler」来断言。
    from urllib.request import ProxyHandler  # noqa: PLC0415

    def _proxy_capable(op):
        out = []
        for h in getattr(op, "handlers", []):
            if isinstance(h, ProxyHandler) and getattr(h, "proxies", None):
                out.append(h.proxies)
        for chain in (getattr(op, "handle_open", {}) or {}).values():
            for h in chain:
                if isinstance(h, ProxyHandler) and getattr(h, "proxies", None):
                    out.append(h.proxies)
        return out

    check(not _proxy_capable(opener), "绕代理 opener 未挂非空代理 handler：%r" % _proxy_capable(opener))
    sys_proxies = urllib.request.getproxies()
    dhd = _proxy_capable(urllib.request.build_opener())
    if sys_proxies:
        check(bool(dhd), "对照组：本机存在系统代理 %r，默认 opener 确实挂上了它（差异真实）" % sys_proxies)
    else:
        check(True, "本机无系统代理（对照组差异不可测，跳过）")

    # 2) post_json：方法 / Content-Type / JSON body / 显式 timeout 透传
    seen: dict = {}

    def t_ok(req, timeout):
        seen["method"] = req.get_method()
        seen["url"] = req.full_url
        seen["body"] = req.data
        seen["ctype"] = req.get_header("Content-type")
        seen["timeout"] = timeout
        return _FakeResp(b'{"code": 200, "data": {"x": 1}}', 200)

    r = post_json("http://example.invalid/x", {"a": 1}, timeout=7, transport=t_ok)
    check(r == {"code": 200, "data": {"x": 1}}, "post_json 解析 JSON 应答：%r" % r)
    check(seen.get("method") == "POST" and json.loads(seen.get("body") or b"{}") == {"a": 1},
          "POST 方法与 JSON body：%r %r" % (seen.get("method"), seen.get("body")))
    check(seen.get("ctype") == "application/json" and abs((seen.get("timeout") or 0) - 7) < 1e-9,
          "Content-Type 与显式 timeout 透传：%r %r" % (seen.get("ctype"), seen.get("timeout")))
    check(seen.get("url") == "http://example.invalid/x", "URL 未变：%r" % seen.get("url"))

    # 3) HTTPError（真 urlopen 形态）⇒ NetError(http_error, status, body)
    def t_httperr(req, timeout):
        import io  # noqa: PLC0415
        raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", None, io.BytesIO(b"proxy says no"))

    try:
        post_json("http://example.invalid/x", {}, transport=t_httperr)
        check(False, "HTTPError 应映射为 NetError")
    except NetError as e:
        check(e.kind == "http_error" and e.status == 502 and b"proxy says no" in e.body,
              "HTTPError→NetError(http_error,502,body)：%r" % e.detail())

    # 4) 假 transport 直回 502（不抛）⇒ 同样 http_error（与真 urlopen 同口径）
    try:
        post_json("http://example.invalid/x", {}, transport=lambda req, to: _FakeResp(b"oops", 502))
        check(False, "直回 502 应映射为 NetError")
    except NetError as e:
        check(e.kind == "http_error" and e.status == 502 and e.body == b"oops",
              "直回 502→NetError(http_error,502)：%r" % e.detail())

    # 5) timeout / 连接重置 / 坏 JSON 三态映射
    def t_timeout(req, timeout):
        raise socket.timeout("timed out")

    def t_reset(req, timeout):
        raise ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接")

    for fn, want_kind, label in ((t_timeout, "timeout", "socket.timeout"),
                                 (t_reset, "transport", "ConnectionResetError(10054)")):
        try:
            get_text("http://example.invalid/x", timeout=1, transport=fn)
            check(False, "%s 应映射为 NetError" % label)
        except NetError as e:
            check(e.kind == want_kind, "%s→NetError(%s)：%r" % (label, want_kind, e.detail()))

    try:
        post_json("http://example.invalid/x", {}, transport=lambda req, to: _FakeResp(b"<html>", 200))
        check(False, "坏 JSON 应映射为 NetError")
    except NetError as e:
        check(e.kind == "bad_json", "坏 JSON→NetError(bad_json)：%r" % e.detail())

    # 6) 不注入 transport 时走模块 opener（可替换、调用参数正确）+ 应答被关闭
    holder = {"resp": None}

    def t_close(req, timeout):
        holder["resp"] = _FakeResp(b"hello", 200)
        return holder["resp"]

    txt = get_text("http://example.invalid/y", timeout=3, transport=t_close)
    check(txt == "hello" and holder["resp"].closed, "应答读后关闭：%r closed=%r" % (txt, holder["resp"].closed))

    class _FakeOpener:
        def __init__(self):
            self.calls = []

        def open(self, req, timeout=None):
            self.calls.append((req.get_method(), req.full_url, timeout))
            return _FakeResp(b"via-opener", 200)

    old = opener
    try:
        opener = _FakeOpener()
        txt2 = get_text("http://example.invalid/z", timeout=2)
        check(txt2 == "via-opener" and opener.calls and opener.calls[0][2] == 2.0,
              "默认路径经模块 opener（timeout 透传）：%r" % (opener.calls,))
    finally:
        opener = old

    ok = sum(1 for c in checks if c)
    total = len(checks)
    if ok == total:
        print("NET_SELFTEST=PASS %d/%d" % (ok, total))
        return 0
    print("NET_SELFTEST=FAIL %d/%d" % (ok, total))
    return 3


# ---------------------------------------------------------------- 现场 A/B

def _default_post(url: str, timeout: float) -> dict:
    """默认路径（复现根因）：裸 urllib.urlopen —— 会被系统代理劫持。"""
    data = json.dumps({"scriptId": None}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 —— 刻意用默认路径取证
        return json.loads(r.read().decode("utf-8", "replace"))


def _tally(errs: list) -> str:
    if not errs:
        return "[]"
    from collections import Counter  # noqa: PLC0415
    c = Counter(errs)
    return "[" + ", ".join("%s x%d" % (k, v) for k, v in c.most_common(6)) + "]"


def run_ab_check(n: int = 20, url: str | None = None, timeout: float = 6.0,
                 out: str | None = None) -> int:
    """现场 A/B：默认路径 vs 绕代理，各 n 次只读 POST /api/scripts/get。

    第三相为「死代理确定性差分」：注入 http_proxy=127.0.0.1:9（无监听）后，
    默认路径必然全挂、mas_net 必然全通 —— 把「代理劫持」这一根因从间歇复现
    变成可重复复现（间歇 502 窗口之外也能自证机制）。
    """
    url = url or (API_BASE + "/api/scripts/get")
    lines: list = []

    def emit(s: str = ""):
        print(s, flush=True)
        lines.append(s)

    emit("NET_AB url=%s n=%d timeout=%.1fs" % (url, n, timeout))
    try:
        prox = urllib.request.getproxies()
    except Exception as e:  # noqa: BLE001
        prox = {"<getproxies failed>": str(e)}
    emit("proxies=%s" % (prox or {}))

    # 第一相：默认路径（复现根因）
    d_ok, d_err = 0, []
    for _ in range(n):
        try:
            body = _default_post(url, timeout)
            if int((body or {}).get("code") or 0) == 200:
                d_ok += 1
            else:
                d_err.append("code=%s" % (body or {}).get("code"))
        except urllib.error.HTTPError as e:
            d_err.append("HTTP %s %s" % (e.code, e.reason))
        except Exception as e:  # noqa: BLE001
            d_err.append("%s: %s" % (type(e).__name__, e))
    emit("default: ok=%d/%d fail=%d %s" % (d_ok, n, n - d_ok, _tally(d_err)))

    # 第二相：绕代理（修复路径）
    b_ok, b_err, durs = 0, [], []
    for _ in range(n):
        t0 = time.monotonic()
        try:
            r = post_json(url, {"scriptId": None}, timeout=timeout)
            if int((r or {}).get("code") or 0) == 200:
                b_ok += 1
            else:
                b_err.append("code=%s" % (r or {}).get("code"))
        except NetError as e:
            b_err.append("%s: %s" % (e.kind, e))
        except Exception as e:  # noqa: BLE001
            b_err.append("%s: %s" % (type(e).__name__, e))
        durs.append(time.monotonic() - t0)
    emit("bypass:  ok=%d/%d fail=%d %s avg_ms=%.1f max_ms=%.1f"
         % (b_ok, n, n - b_ok, _tally(b_err),
            (sum(durs) / len(durs) * 1000) if durs else -1.0,
            (max(durs) * 1000) if durs else -1.0))

    # 第三相：死代理确定性差分（k=3；只看机制，不掺间歇性）
    k = 3
    old_env = os.environ.get("http_proxy")
    dd_ok, dd_err, db_ok, db_err = 0, [], 0, []
    os.environ["http_proxy"] = "http://127.0.0.1:9"
    try:
        dead_opener = urllib.request.build_opener()   # 此刻才读 getproxies() ⇒ 死代理
        req = urllib.request.Request(url, data=b'{"scriptId": null}', method="POST",
                                     headers={"Content-Type": "application/json"})
        for _ in range(k):
            try:
                with dead_opener.open(req, timeout=timeout) as r:
                    r.read()
                dd_ok += 1
            except Exception as e:  # noqa: BLE001
                dd_err.append("%s: %s" % (type(e).__name__, e))
        for _ in range(k):
            try:
                r = post_json(url, {"scriptId": None}, timeout=timeout)
                if int((r or {}).get("code") or 0) == 200:
                    db_ok += 1
                else:
                    db_err.append("code=%s" % (r or {}).get("code"))
            except Exception as e:  # noqa: BLE001
                db_err.append("%s: %s" % (type(e).__name__, e))
    finally:
        if old_env is None:
            os.environ.pop("http_proxy", None)
        else:
            os.environ["http_proxy"] = old_env
    emit("deadproxy: env http_proxy=http://127.0.0.1:9（确定性差分）")
    emit("  default: ok=%d/%d fail=%d %s" % (dd_ok, k, k - dd_ok, _tally(dd_err)))
    emit("  bypass:  ok=%d/%d fail=%d %s" % (db_ok, k, k - db_ok, _tally(db_err)))
    emit("NET_AB_DEADPROXY=%s" % ("PASS" if (dd_ok == 0 and db_ok == k) else "ANOMALY"))

    verdict = b_ok == n and db_ok == k
    emit("NET_AB_DEFAULT_REPRO=%s" % ("yes" if d_ok < n else "no"))
    emit("NET_AB=%s" % ("PASS" if verdict else "FAIL"))

    if out:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            print("（输出已写 %s）" % out, flush=True)
        except OSError as e:
            print("（写 %s 失败：%s）" % (out, e), file=sys.stderr, flush=True)
    return 0 if verdict else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="mas_net：绕代理统一 HTTP 客户端（G2 修复）")
    ap.add_argument("--selftest", action="store_true", help="离线自检（假 transport，不碰网络）")
    ap.add_argument("--ab-check", action="store_true", help="现场 A/B：默认路径 vs 绕代理（只读 POST /api/scripts/get）")
    ap.add_argument("--url", default=None, help="A/B 目标（默认 MAAORCH_MAS_API + /api/scripts/get）")
    ap.add_argument("--n", type=int, default=20, help="A/B 每侧次数（默认 20）")
    ap.add_argument("--timeout", type=float, default=6.0, help="A/B 单次超时秒（默认 6.0；各工具 CLI 的 --timeout 语义不在此列）")
    ap.add_argument("--out", default=None, help="把输出同时写到该文件（取证用）")
    args = ap.parse_args(argv)
    if args.selftest:
        return run_selftest()
    if args.ab_check:
        return run_ab_check(n=max(1, args.n), url=args.url, timeout=args.timeout, out=args.out)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
