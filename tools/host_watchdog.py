# -*- coding: utf-8 -*-
"""主机看门狗 —— 生产主机失联就发通知。

判据（**连续 N 次**失败才算，避免网络抖动误报）：
  ① ICMP ping 不通 且 ② AUTO-MAS API 端口连不上 ⇒ 主机或 AUTO-MAS 挂了
  只有 ② 不通 ⇒ 机器活着但 AUTO-MAS 没起来（另一类告警）

用法:
  python tools/host_watchdog.py                 # 常驻，每 interval_min 分钟探一次
  python tools/host_watchdog.py --once          # 探一次（计划任务用这个）
  python tools/host_watchdog.py --test-notify   # 只发一条测试通知
  python tools/host_watchdog.py --simulate 3    # 假装连失 N 次，验证状态机（不真探）

配置 runtime/watchdog.ini（不入库；值取自生产主机 AUTO-MAS 的通知设置）
状态 runtime/watchdog_state.json ｜ 日志 runtime/watchdog.log
"""
from __future__ import annotations

import argparse
import configparser
import datetime as dt
import json
import pathlib
import smtplib
import socket
import subprocess
import sys
import time
import urllib.request
from email.header import Header
from email.mime.text import MIMEText

# Windows：不带这个标志，子进程（adb/ssh/ping）会**闪一个 cmd 黑窗** ✗
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PROJ = pathlib.Path(__file__).resolve().parents[1]
CONF = PROJ / "runtime" / "watchdog.ini"
STATE = PROJ / "runtime" / "watchdog_state.json"
LOG = PROJ / "runtime" / "watchdog.log"
RENOTIFY_MIN = 30          # 持续失联时，每隔这么久再提醒一次
LOG_MAX_LINES = 2000


def log(msg: str) -> None:
    line = "%s  %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if LOG.stat().st_size > 512_000:                    # 太大就留尾部
            lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
            LOG.write_text("\n".join(lines[-LOG_MAX_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def load_conf() -> configparser.ConfigParser:
    c = configparser.ConfigParser()
    if not CONF.exists():
        sys.exit("[看门狗] 缺少配置 %s" % CONF)
    c.read(CONF, encoding="utf-8")
    return c


# ---------------------------------------------------------------- 通知通道

def _send_email(c: configparser.ConfigParser, subject: str, body: str) -> str:
    e = c["email"]
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = e["from_addr"]
    msg["To"] = e["to_addr"]
    host, port = e["smtp_host"], int(e.get("smtp_port", "465"))
    if e.getboolean("smtp_ssl", fallback=True):
        srv = smtplib.SMTP_SSL(host, port, timeout=25)
    else:
        srv = smtplib.SMTP(host, port, timeout=25)
        srv.starttls()
    try:
        srv.login(e["from_addr"], e["auth_code"])
        srv.sendmail(e["from_addr"], [x.strip() for x in e["to_addr"].split(",")], msg.as_string())
    finally:
        try:
            srv.quit()
        except Exception:                                   # noqa: BLE001
            pass
    return "邮件 -> %s" % e["to_addr"]


def _post(url: str, payload: dict, timeout: int = 20) -> None:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read(200)


def notify(c: configparser.ConfigParser, subject: str, body: str, send: bool = True) -> None:
    n = c["notify"]
    if not send:
        log("[通知-已抑制] %s" % subject)
        return
    sent: list[str] = []
    ch = (n.get("channel") or "email").strip()
    if ch == "serverchan" and n.get("serverchan_key"):
        _post("https://sctapi.ftqq.com/%s.send" % n["serverchan_key"].strip(),
              {"title": subject, "desp": body})
        sent.append("Server酱")
    elif ch == "telegram" and n.get("telegram_token"):
        _post("https://api.telegram.org/bot%s/sendMessage" % n["telegram_token"].strip(),
              {"chat_id": n.get("telegram_chat", "").strip(), "text": subject + "\n" + body})
        sent.append("Telegram")
    elif ch == "dingtalk" and n.get("dingtalk_webhook"):
        _post(n["dingtalk_webhook"].strip(),
              {"msgtype": "text", "text": {"content": subject + "\n" + body}})
        sent.append("钉钉")
    else:
        sent.append(_send_email(c, subject, body))
    log("[通知] 已发送（%s）: %s" % ("、".join(sent), subject))


# ---------------------------------------------------------------- 探测

def ping_ok(host: str) -> bool:
    try:
        r = subprocess.run(["ping", "-n", "1", "-w", "3000", host],
                           capture_output=True, timeout=8, creationflags=NOWIN)
        return r.returncode == 0
    except Exception:                                       # noqa: BLE001
        return False


def tcp_ok(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=6):
            return True
    except OSError:
        return False


def probe(host: str, port: int) -> tuple[bool, str]:
    """返回 (是否健康, 说明)"""
    t = tcp_ok(host, port)
    p = ping_ok(host)
    if t:
        return True, "ping=%s API口=tcp通" % ("通" if p else "不通")
    if p:
        return False, "ping=通 但 API口 %d 连不上 ⇒ 机器活着、AUTO-MAS 没起来" % port
    return False, "ping=不通 API口 %d 连不上 ⇒ 主机整个失联" % port


# ---------------------------------------------------------------- 状态机

def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return {"fails": 0, "outage": False, "last_notify": 0.0, "last_ok": 0.0}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def tick(c: configparser.ConfigParser, healthy: bool, why: str, send: bool = True) -> None:
    host = c["host"]["host"]
    thr = c["host"].getint("fail_threshold", fallback=3)
    s = load_state()
    now = time.time()
    if healthy:
        if s.get("outage"):
            mins = int((now - s.get("last_ok", now)) / 60)
            notify(c, "[恢复] 生产主机已回来",
                   "主机 %s 已恢复（断线约 %d 分钟）。\n探测：%s" % (host, mins, why), send=send)
            s["outage"] = False
        s["fails"] = 0
        s["last_ok"] = now
        save_state(s)
        log("[正常] %s" % why)
        return
    s["fails"] = int(s.get("fails", 0)) + 1
    log("[异常 %d/%d] %s" % (s["fails"], thr, why))
    if s["fails"] >= thr:
        if not s.get("outage") or now - s.get("last_notify", 0) > RENOTIFY_MIN * 60:
            notify(c, "[告警] 生产主机失联",
                   "主机 %s 已连续 %d 次探测失败（约 %d 分钟）。\n探测：%s\n\n时间：%s"
                   % (host, s["fails"],
                      s["fails"] * c["host"].getint("interval_min", fallback=5),
                      why, dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")), send=send)
            s["last_notify"] = now
        s["outage"] = True
    save_state(s)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只探一次（计划任务用）")
    ap.add_argument("--test-notify", action="store_true", help="只发一条测试通知")
    ap.add_argument("--simulate", type=int, default=0, help="假装连失 N 次（验证状态机）")
    ap.add_argument("--no-send", action="store_true", help="测试用：只记日志，不发通知")
    a = ap.parse_args()
    send = not a.no_send

    c = load_conf()
    host = c["host"]["host"]
    port = c["host"].getint("port", fallback=36163)

    if a.test_notify:
        notify(c, "[测试] MAAOrch 看门狗", "这是一条测试通知；收到即表示告警通道可用。", send=send)
        return

    if a.simulate:
        for i in range(a.simulate):
            tick(c, False, "（模拟）第 %d 次失联" % (i + 1), send=send)
        return

    if a.once:
        ok, why = probe(host, port)
        tick(c, ok, why, send=send)
        return

    interval = c["host"].getint("interval_min", fallback=5)
    log("[启动] 看门狗常驻，每 %d 分钟探一次 %s (API 口 %d)" % (interval, host, port))
    while True:
        ok, why = probe(host, port)
        tick(c, ok, why, send=send)
        time.sleep(interval * 60)


if __name__ == "__main__":
    main()
