#!/usr/bin/env python3
"""推图 UI 的「真一键重试」控制通道 —— 页面点一下，Agent 自己去打。

为什么需要（用户 2026-09-17 定）:
    `ui/push_agent.html` 是纯静态页（file:// 打开，或把日志拖进来），**页面自己起不了进程**
    —— 浏览器不给本地文件写权限，点按钮最多只能复制命令。可手机上"复制到剪贴板再粘进
    终端"基本不可行 ⇒ 补一条极小的本地通道：页面 POST 一下，这里把 auto_push.py 起起来。
    这不是新后端，是**命令转发器**：只转发一条写死的命令。

三条边界（用户定的原话，别放松）:
    · 只监听 127.0.0.1；要手机走局域网才加 --lan（= 0.0.0.0），**局域网必须有 token**
    · stage 白名单：只认 SR-1..SR-8 / EX-1..EX-8，别的一律 400
    · 命令写死 auto_push.py，stage 只当参数传（不进 shell ⇒ 没有任意命令的口子）

用法:
    python ui/control_server.py                # 只听本机（页面 file:// 打开也够用）
    python ui/control_server.py --lan          # 局域网：首次自动生成 ui/control_token.txt
    python ui/control_server.py --sanity 75    # 页面顶上的理智数（刷新时重生成页面用）

    不点页面也能验通道（本机）:
      curl -X POST http://127.0.0.1:8898/retry/SR-8
      curl http://127.0.0.1:8898/status
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import json
from pathlib import Path

from flask import Flask, jsonify, request, send_file

PROJ = Path(__file__).resolve().parent.parent
PAGE = PROJ / "ui" / "push_agent.html"
TOKEN_FILE = PROJ / "ui" / "control_token.txt"
GENERATOR = PROJ / "tools" / "gen_push_ui.py"
PREFS = PROJ / "ui" / "prefs.json"
LAST_RUN_FILE = PROJ / "ui" / "lastrun.json"   # 横幅状态落盘（重启通道也不丢）       # 页面开关的落盘位置（跨页面/跨账号都记得）
STAGE_RE = re.compile(r"^(SR|EX)-[1-8]$")            # ← 白名单（唯一允许的"输入"）

_token = ""                                          # 空 = 不校验（只听本机时）
EMU = "2"                                             # 目标模拟器实例（端口 = 16384 + 32*N）
ACCOUNT = ""                                          # 账号键（多开：emu0/emu1/…；空 = 9036 默认）
POOL = ""                                             # 作业池目录名（默认 event54_c）
_lock = threading.Lock()
_state: dict = {"proc": None, "stage": "", "started": 0.0, "log": None, "fh": None}
_pick_count: dict[str, int] = {}
_lastrun: dict = {}          # 最后一次执行（页面刷新/切回来后横幅还能显示它）      # 「换作业」按钮：每关被点了几次（→ --pick N 轮换候选）


def find_push_script() -> Path | None:
    """找 auto_push.py：它是"推图 Agent"本体。

    顺序：环境变量 → **仓库 tools/push/** → work/ → 沙箱（历史遗留）。找不到就明确报错，别瞎跑。
    """
    env = os.environ.get("MAA_AUTO_PUSH", "")
    if env and Path(env).is_file():
        return Path(env)
    # **仓库内那份优先**（2026-09-18 收编：推图本体从沙箱搬进 tools/push/）——
    # 以前只找 work/ 与 tools/，沙箱那份永远赢 ⇒ "收进仓库"只是多了一份副本 ✗。
    cands = [PROJ / "tools" / "push" / "auto_push.py",
             PROJ / "work" / "auto_push.py", PROJ / "tools" / "auto_push.py"]
    # 可视化临时目录的层级是 <年>/<月>/<日>/<会话 id>/sr54/auto_push.py（4 层，别少写一层）
    viz = Path.home() / ".codex" / "visualizations"
    if viz.is_dir():
        try:
            cands += sorted(viz.glob("*/*/*/*/sr54/auto_push.py"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            pass
    for p in cands:
        if p.is_file():
            return p
    return None


def start_retry(stage: str, raid: bool = False, pick: int = 0, here: bool = False) -> dict:
    """起一次重试（独立进程 + 日志追加到 auto_push.log，页面刷新就能看到）。

    `raid=True`（页面上的「打突袭」按钮）= 让 auto_push 走 `--raid`：只挑「仅突袭」的作业，
    结果记在 `raid_*` 字段（**不碰普通记录**）。前提是那一关的普通已通关，否则游戏会把突袭
    当演习跑 —— auto_push 自己会拦下来并说明。
    """
    script = find_push_script()
    if script is None:
        raise RuntimeError("找不到 auto_push.py（可用环境变量 MAA_AUTO_PUSH 指定）")
    with _lock:
        proc = _state.get("proc")
        if proc and proc.poll() is None:
            # 手机上双击了不该起两个进程去抢同一个模拟器
            raise RuntimeError(f"已有一次运行在进行中：{_state.get('stage')}"
                               f"（等它跑完，或先把它停掉再点）")
        old_fh = _state.get("fh")
        if old_fh:
            try:
                old_fh.close()
            except OSError:
                pass
        # 每次重试**单独一个日志文件**（页面的 find_logs 认 auto_push*.log）：
        # auto_push.log 是 PowerShell 重定向写的 UTF-16，往里追加 UTF-8 会让日志面板变乱码
        # （2026-09-17 实测），所以另起一份纯 UTF-8 的；旧文件不动。
        # 多账号：文件名里带账号键（形如 auto_push_retry_emu0_0917_0400.log），页面据此过滤
        key = f"_{ACCOUNT}" if ACCOUNT else ""
        log = script.parent / f"auto_push_retry{key}_{time.strftime('%m%d_%H%M%S')}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        fh = open(log, "a", encoding="utf-8")
        fh.write(f"\n{'=' * 18} [retry {time.strftime('%Y-%m-%d %H:%M:%S')}] "
                 f"{stage} {'=' * 18}\n")
        fh.flush()
        cmd = [sys.executable, str(script), "--stages", stage, "--emu", EMU]
        if raid:
            cmd.append("--raid")
        if pick:
            cmd += ["--pick", str(pick)]
        if here:
            cmd.append("--here")
        if ACCOUNT:
            cmd += ["--account", ACCOUNT]
        if POOL:
            cmd += ["--pool", POOL]
        proc = subprocess.Popen(cmd,
                                cwd=str(script.parent), stdout=fh, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _state.update({"proc": proc, "stage": stage, "started": time.time(),
                       "log": log, "fh": fh})
        return {"stage": stage, "pid": proc.pid, "script": str(script), "log": str(log)}


def start_calibrate() -> dict:
    """按**游戏画面上的黄点**校准这个账号的状态（跑 calibrate.py，纠正"假通关"）。

    为什么需要（2026-09-17 用户："已经通关的关卡没办法让他再打吗，有些其实是误判"）:
    状态文件会被"没看到结算页 → 记三星"污染，那关就再也点不动、后面全卡住。
    黄点是游戏原话，以它为准回写：有黄点→三星；无黄点而记录是通关档→未确认。

    **与重试共用同一把锁 / 同一个"当前进程"槽**：校准也要开着游戏画面点，和推图同时跑
    会互相抢同一台模拟器（一台上面只能有一个控制者）。
    """
    script = find_push_script()
    if script is None:
        raise RuntimeError("找不到 auto_push.py（可用环境变量 MAA_AUTO_PUSH 指定）")
    calib = script.parent / "calibrate.py"
    if not calib.is_file():
        raise RuntimeError(f"同目录下找不到 calibrate.py：{calib}")
    with _lock:
        proc = _state.get("proc")
        if proc and proc.poll() is None:
            raise RuntimeError(f"已有一次运行在进行中：{_state.get('stage')}")
        old_fh = _state.get("fh")
        if old_fh:
            try:
                old_fh.close()
            except OSError:
                pass
        key = f"_{ACCOUNT}" if ACCOUNT else ""
        log = script.parent / f"calibrate{key}_{time.strftime('%m%d_%H%M%S')}.log"
        fh = open(log, "a", encoding="utf-8")
        fh.write(f"\n{'=' * 18} [calibrate {time.strftime('%Y-%m-%d %H:%M:%S')}] "
                 f"{ACCOUNT or '9036'} {'=' * 18}\n")
        fh.flush()
        cmd = [sys.executable, str(calib), "--emu", EMU]
        if ACCOUNT:
            cmd += ["--account", ACCOUNT]
        if POOL:
            cmd += ["--pool", POOL]
        proc = subprocess.Popen(cmd, cwd=str(script.parent), stdout=fh,
                                stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _state.update({"proc": proc, "stage": "校准", "started": time.time(),
                       "log": log, "fh": fh})
        return {"stage": "校准", "pid": proc.pid, "script": str(calib), "log": str(log)}


def start_boxread() -> dict:
    """读这个账号的**干员 box**（`tools/box_read_when_free.py`）——等模拟器空闲再读、读完归位。

    为什么要有这个按钮（2026-09-17 用户："这个网站没有扫描干员的按钮"）: 挑作业靠 box
    （"用算不用试"）。box 缺了或过期，preflight 就只能"照旧全部试"，等于退回瞎试、白花理智
    —— 今晚从"试一次几分钟"变成"算完再挑"就是靠这个。
    """
    script = find_push_script()
    if script is None:
        raise RuntimeError("找不到 auto_push.py（可用环境变量 MAA_AUTO_PUSH 指定）")
    box = PROJ / "tools" / "box_read_when_free.py"
    if not box.is_file():
        raise RuntimeError(f"找不到 {box}")
    if not ACCOUNT:
        raise RuntimeError("这条通道没带账号（--account），读出来的 box 没法归到哪个号")
    with _lock:
        proc = _state.get("proc")
        if proc and proc.poll() is None:
            raise RuntimeError(f"已有一次运行在进行中：{_state.get('stage')}"
                               f"（等它跑完，或先把它停掉再点）")
        old_fh = _state.get("fh")
        if old_fh:
            try:
                old_fh.close()
            except OSError:
                pass
        log = script.parent / f"boxread_{ACCOUNT}_{time.strftime('%m%d_%H%M%S')}.log"
        fh = open(log, "a", encoding="utf-8")
        fh.write(f"\n{'=' * 18} [boxread {time.strftime('%Y-%m-%d %H:%M:%S')}] "
                 f"{ACCOUNT} {'=' * 18}\n")
        fh.flush()
        cmd = [sys.executable, str(box), "--emu", EMU, "--uid", ACCOUNT]
        proc = subprocess.Popen(cmd, cwd=str(script.parent), stdout=fh,
                                stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _state.update({"proc": proc, "stage": "读 box", "started": time.time(),
                       "log": log, "fh": fh})
        return {"stage": "读 box", "pid": proc.pid, "script": str(box), "log": str(log)}


def stop_all() -> dict:
    """**一键停止**：把这条通道管辖的那台模拟器上正在跑的东西全停掉，并释放锁。

    为什么（2026-09-17 用户："我现在点什么东西它会跟我说有东西在运行，我就运行不了了"）:
    在跑的进程**不一定是这条通道起的**（可能是命令行起的、或上次重启通道之前起的），
    通道只记得自己那一份 ⇒ 光看 _state 会"点了停不掉、还是说在运行"。所以按**模拟器**清场：
      ① 通道自己起的那份  ② 其它 `auto_push.py --emu N`  ③ 连在这台模拟器上的 MAA 运行器
      ④ 残留的 `emu_<N>.lock`（**PID 已死的才删**，活着的说明真有控制者）
    """
    killed: list[str] = []
    with _lock:
        proc = _state.get("proc")
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                killed.append(f"通道起的那份 pid={proc.pid}")
            except Exception as e:                            # noqa: BLE001
                killed.append(f"通道那份杀不掉（{type(e).__name__}）")
        old_fh = _state.get("fh")
        if old_fh:
            try:
                old_fh.close()
            except OSError:
                pass
        _state.update({"proc": None, "stage": "", "started": 0.0, "log": None, "fh": None})

    # ② 其它 auto_push.py --emu N（命令行起的也一起停）
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -match 'auto_push|run_job|scan_box' -and "
          f"$_.CommandLine -match '--emu\\s+{EMU}(\\s|$)' }} | "
          "ForEach-Object { $_.ProcessId }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=30).stdout
        for tok in out.split():
            if tok.strip().isdigit():
                subprocess.run(["taskkill", "/F", "/PID", tok.strip()],
                               capture_output=True, timeout=15)
                killed.append(f"auto_push pid={tok.strip()}")
    except Exception as e:                                    # noqa: BLE001
        killed.append(f"扫 auto_push 失败（{type(e).__name__}）")

    # ③ 连在这台模拟器端口上的 MAA 运行器（孤儿的典型形态）
    port = str(16384 + 32 * int(EMU))
    ps2 = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
           "Where-Object { $_.CommandLine -match 'asst\\.asst' -and "
           f"$_.CommandLine -match ':{port}' }} | ForEach-Object {{ $_.ProcessId }}")
    try:
        out2 = subprocess.run(["powershell", "-NoProfile", "-Command", ps2],
                              capture_output=True, text=True, timeout=30).stdout
        for tok in out2.split():
            if tok.strip().isdigit():
                subprocess.run(["taskkill", "/F", "/PID", tok.strip()],
                               capture_output=True, timeout=15)
                killed.append(f"MAA 运行器 pid={tok.strip()}")
    except Exception as e:                                    # noqa: BLE001
        killed.append(f"扫运行器失败（{type(e).__name__}）")

    # ④ 残留锁（只删 PID 已死的）
    script = find_push_script()
    lock = (script.parent if script else PAGE.parent) / f"emu_{EMU}.lock"
    if lock.is_file():
        try:
            pid = int(lock.read_text(encoding="utf-8").strip().split()[0])
            alive = subprocess.run(["powershell", "-NoProfile", "-Command",
                                    f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue)"
                                    " { 'yes' } else { 'no' }"],
                                   capture_output=True, text=True, timeout=15).stdout.strip()
            if alive == "no":
                lock.unlink(missing_ok=True)
                killed.append(f"清掉残留锁（PID {pid} 已死）")
            else:
                killed.append(f"锁还被 pid={pid} 持有（**没动它**）")
        except Exception:                                     # noqa: BLE001
            pass
    if not killed:
        killed.append("没有发现要停的东西")
    return {"stopped": killed}


def start_sync(kind: str, cmd: list, label: str, timeout: float) -> dict:
    """**同步**跑一条命令，把结果直接带回页面（极简执行器"点 ▶ 就等结果"的语义）。

    为什么不做进程台账（2026-09-17 用户："我现在点什么东西它会跟我说有东西在运行"）:
    旧版把"当前进程"记在内存里，进程被外部杀掉/通道重启之后那条记录还在 ⇒ 页面永远说
    "已有一次运行在进行中"。这里只做**一次忙判定**（真在跑就拒绝），跑完即返回，不留状态。
    """
    with _lock:
        proc = _state.get("proc")
        if proc is not None and proc.poll() is None:
            raise RuntimeError(f"已有一次运行在进行中：{_state.get('stage')}"
                               f"（等它跑完，或点「一键停止」）")
    t0 = time.time()
    # **`creationflags` 不能漏**（2026-09-17 实测）：不传时子进程在"没有控制台"的会话里
    # 起不来，返回 `0xC0000142`（STATUS_DLL_INIT_FAILED）、**零输出** ⇒ 页面上就是
    # "没跑起来：（空）"、0 秒。其它启动器（start_retry 等）一直带着它，就这里漏了。
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, cwd=str(PROJ),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    tail = [x for x in (r.stdout or "").strip().splitlines() if x.strip()][-10:]
    out = {"kind": kind, "label": label, "rc": r.returncode,
           "elapsed": round(time.time() - t0, 1), "tail": tail}
    _lastrun.update(out, running=False)
    try:
        LAST_RUN_FILE.write_text(json.dumps(_lastrun, ensure_ascii=False), encoding="utf-8")
    except Exception:                        # noqa: BLE001
        pass
    return out


def status() -> dict:
    with _lock:
        proc = _state.get("proc")
        running = bool(proc and proc.poll() is None)
        return {"running": running, "stage": _state.get("stage") or "",
                "elapsed": round(time.time() - _state["started"], 1) if _state.get("started") else 0,
                "pid": proc.pid if running else 0,
                "log": str(_state.get("log") or ""),
                "script": str(find_push_script() or "")}


def regenerate_page(sanity: str, account_label: str = "") -> None:
    """刷新页面 = 重跑一次生成器（沿用原来的约定）。失败就算了，旧页面照常给。

    **账号键必须传对**（2026-09-17 修）: 以前这里是 `account_label or "9036"`，而 `/` 路由
    调用时没传 label ⇒ 每次打开页面都把标题重写成 "9036"（我手动生成时明明传的是 UID）。
    现在优先用服务端自己的 ACCOUNT；确实不知道时**干脆不传**，让生成器从 --state 文件名认。
    """
    acct = account_label or ACCOUNT
    cmd = [sys.executable, str(GENERATOR), "--sanity", sanity]
    if acct:
        cmd += ["--account", acct]
    if ACCOUNT:
        script = find_push_script()
        state = (script.parent if script else PAGE.parent) / f"push_state_{ACCOUNT}.json"
        cmd += ["--state", str(state),
                "--out", str(page_path()), "--log-key", ACCOUNT]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:                                    # noqa: BLE001 —— 页面生成失败不该拖垮通道
        print(f"[warn] 重新生成页面失败（用旧页面继续）：{type(e).__name__}: {e}", flush=True)


def page_path() -> Path:
    """这个账号的页面文件（9036 用原来的 ui/push_agent.html）。"""
    return PAGE if not ACCOUNT else PAGE.with_name(f"push_agent_{ACCOUNT}.html")


def build_app(sanity: str) -> Flask:
    app = Flask(__name__)

    @app.after_request
    def _cors(resp):
        # 页面用 file:// 打开时 origin 是 "null"，带 X-Token 的 fetch 还会先发 OPTIONS —— 都放行
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "X-Token, Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp

    @app.post("/retry/<stage>")
    def _retry(stage: str):
        if not STAGE_RE.match(stage):
            return jsonify({"ok": False, "error": f"stage 不在白名单：{stage}"}), 400
        if _token and request.headers.get("X-Token") != _token:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        try:
            return jsonify({"ok": True, **start_retry(stage)})
        except Exception as e:                                # noqa: BLE001 —— 一律当"起不来"报给页面
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 409

    @app.post("/raid/<stage>")
    def _raid(stage: str):
        """打这一关的**突袭**（2026-09-17 用户："我想让他打一下这个突袭"）。

        与 /retry 共用同一把锁 / 同一个"当前进程"槽 —— 一台模拟器上只能有一个控制者。
        """
        if not STAGE_RE.match(stage):
            return jsonify({"ok": False, "error": f"stage 不在白名单：{stage}"}), 400
        if _token and request.headers.get("X-Token") != _token:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        try:
            return jsonify({"ok": True, **start_retry(stage, raid=True)})
        except Exception as e:                                # noqa: BLE001
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 409

    @app.post("/boxread")
    def _boxread():
        """读干员 box（页面上的「扫描干员」按钮）。"""
        if _token and request.headers.get("X-Token") != _token:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        try:
            return jsonify({"ok": True, **start_boxread()})
        except Exception as e:                                # noqa: BLE001
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 409

    @app.post("/next/<stage>")
    def _next(stage: str):
        """**换一份作业**重打这一关（页面上的「换作业」按钮）。

        做法 = `--pick N`：环形轮换候选列表、从第 N 份开始试。N 按"这个关被点了几次"递增，
        所以连点就沿候选列表往下走 —— 今晚 9036 的 EX-7 就是这么换到能过的作业的
        （用户："不行就换一份吧，这一个作业他都抄了好几次了"）。
        """
        if not STAGE_RE.match(stage):
            return jsonify({"ok": False, "error": f"stage 不在白名单：{stage}"}), 400
        if _token and request.headers.get("X-Token") != _token:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        with _lock:
            n = int(_pick_count.get(stage, 0))
            _pick_count[stage] = n + 1
        try:
            return jsonify({"ok": True, "pick": n, **start_retry(stage, pick=n)})
        except Exception as e:                                # noqa: BLE001
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 409

    @app.post("/run/<stage>")
    def _run(stage: str):
        """**按开关打**：`?mode=normal|raid&here=0|1&pick=N`。

        为什么把"四个按钮"改成"开关 + 一个动作"（2026-09-17 用户："这个是否直接打，其实是个
        总开关 —— 你直接打，是直接打普通还是突袭呢？你用的哪份作业呢"）:
        **路径**（自己导航 / 就在这一屏）、**目标**（普通 / 突袭）、**作业**（默认 / 换一份）
        是三个独立的轴；做成互斥按钮就组合不出来（"直接打 + 突袭"、"直接打 + 换作业"都点不到）。
        """
        if not STAGE_RE.match(stage):
            return jsonify({"ok": False, "error": f"stage 不在白名单：{stage}"}), 400
        if _token and request.headers.get("X-Token") != _token:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        raid = (request.args.get("mode") or "normal").lower() == "raid"
        here = (request.args.get("here") or "0").lower() not in ("0", "", "false", "no")
        try:
            pick = int(request.args.get("pick") or 0)
        except ValueError:
            pick = 0
        try:
            return jsonify({"ok": True, "mode": "突袭" if raid else "普通",
                            "path": "就在这一屏" if here else "自动导航",
                            "pick": pick,
                            **start_retry(stage, raid=raid, here=here, pick=pick)})
        except Exception as e:                                # noqa: BLE001
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 409

    @app.get("/mini")
    def _mini():
        """**极简执行器页**（用户 2026-09-17 规格）：只有账号/box 人数/每关候选/每份一个 ▶。

        页面上没有进度条、没有三星标记、没有状态显示、没有自动刷新 —— 那四样用户明确不要。
        """
        page = PROJ / "ui" / f"mini_{ACCOUNT or 'default'}.html"
        gen = PROJ / "tools" / "gen_mini_ui.py"
        need = (not page.is_file()) or (gen.is_file() and gen.stat().st_mtime > page.stat().st_mtime)
        if need and gen.is_file():
            try:
                subprocess.run([sys.executable, str(gen), "--account", ACCOUNT or "",
                                "--emu", EMU, "--out", str(page)],
                               capture_output=True, timeout=240,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception as e:                            # noqa: BLE001
                print(f"[warn] 生成极简页失败：{type(e).__name__}: {e}", flush=True)
            # 生成脚本自己会 print；失败时页面可能还是旧的 —— 下面照旧发文件
        if not page.is_file():
            return "还没有极简页（生成失败，看通道日志）", 404
        resp = send_file(page)
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        return resp

    @app.post("/scan")
    def _scan():
        """重扫 box（极简执行器第 ① 步）——同步等它跑完（几十秒）。"""
        if _token and request.headers.get("X-Token") != _token:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        if not ACCOUNT:
            return jsonify({"ok": False, "error": "这条通道没带账号（--account），扫出来没法归档"}), 400
        try:
            return jsonify({"ok": True, **start_sync(
                "scan", [sys.executable, str(PROJ / "tools" / "scan_box.py"),
                         "--emu", EMU, "--uid", ACCOUNT],
                f"扫 box {ACCOUNT}", 900)})
        except Exception as e:                                # noqa: BLE001
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 409

    @app.post("/runjob")
    def _runjob():
        """⚡ **快速入口**：调**现有的** `maa_copilot_run.py` 跑指定作业。

        （用户 2026-09-17："点 ▶ = 调现有的 maa_copilot_run.py，传作业路径" ——
        不新写流程、不做检测：人负责把游戏停到那一关，剩下的 MAA 自己认屏。）
        """
        rel = (request.args.get("job") or "").strip()
        root = (PROJ / "data" / "copilot").resolve()
        # **码直跑**（用户 2026-09-17《现场搜 + 码直跑》）: `maa://12345` 或纯数字直接放行，
        # run_job.py 会去作业站拉正文落临时文件、跑完删（不留本地）。
        if rel.startswith("maa://") or rel.isdigit():
            job = rel
        else:
            job = (root / rel).resolve()
            if job.suffix != ".json" or root not in job.parents or not job.is_file():
                return jsonify({"ok": False, "error": f"作业路径不合法或不存在：{rel!r}"}), 400
        cmd = [sys.executable, str(PROJ / "tools" / "run_job.py"),
               "--emu", EMU, "--job", str(job)]
        # MAA 面板里那两个勾（2026-09-17 用户："这些按钮我在网站上好像不能操作嘛"）:
        #   忽略干员属性要求 —— 默认开（今晚 33 关是开着过的）；?ignore=0 关掉
        #   借助战         —— 默认开（缺 1 人靠它借核）；?support=0 关掉
        if (request.args.get("ignore") or "1") == "0":
            pass                                   # run_job 默认就不忽略，只有显式要才加
        else:
            cmd.append("--ignore-requirements")
        if (request.args.get("support") or "1") == "0":
            cmd.append("--no-support")
        try:
            # 码是**字符串**、没有 .name（2026-09-17 实测：点码直跑 409 AttributeError）
            _label = job if isinstance(job, str) else job.name
            return jsonify({"ok": True, **start_sync("runjob", cmd,
                                                     f"执行 {_label}", 1300)})
        except Exception as e:                                # noqa: BLE001
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 409

    @app.get("/lastrun")
    def _lastrun_get():
        """最后一次执行的状态（页面刷新/切回来时，横幅照着它重建）。"""
        return jsonify(dict(_lastrun))

    @app.get("/prefs")
    def _prefs_get():
        """读页面开关（目标/路径）——**存在磁盘上**，所以换页面、换账号回来都还在。

        为什么（2026-09-17 用户："我一换其他页面再回来…返回默认了"）: 开关原来是纯 HTML
        默认值，一刷新就回默认；而 localStorage 按端口隔离 ⇒ 切到别的账号照样丢。
        """
        try:
            return jsonify(json.loads(PREFS.read_text(encoding="utf-8")))
        except Exception:                                     # noqa: BLE001
            return jsonify({})

    @app.post("/prefs")
    def _prefs_set():
        body = request.get_json(silent=True) or {}
        keep = {k: str(v) for k, v in body.items() if k in ("mode", "path", "support", "ignore")}
        try:
            PREFS.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
        except Exception as e:                                # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify({"ok": True, **keep})

    @app.post("/search/<stage>")
    def _search(stage: str):
        """**现场搜这一关的作业**（用户 2026-09-17《现场搜 + 码直跑》）: 调
        `tools/prts_job_finder.py`（连作业站 + 按本号 box 评估）→ 回候选列表（带 maa:// 码）。

        只在点「搜作业」时才搜（用户明确要求：不预加载、不每次开页都搜）。
        """
        if not STAGE_RE.match(stage):
            return jsonify({"ok": False, "error": f"stage 不在白名单：{stage}"}), 400
        box = sorted((PROJ / "data").glob(f"operbox_{ACCOUNT}_*.json"),
                     key=lambda x: x.stat().st_mtime) if ACCOUNT else []
        if not box:
            return jsonify({"ok": False, "error": f"没有 {ACCOUNT} 的 box ⇒ 先点「扫描干员」"}), 409
        disp = f"SR-EX-{stage.split('-')[1]}" if stage.startswith("EX-") else stage
        dur = {"SR-EX-1..SR-EX-8": None}
        try:
            subprocess.run([sys.executable, str(PROJ / "tools" / "prts_job_finder.py"),
                            "--display", f"{disp}..{disp}", "--operbox", str(box[-1]),
                            "--out", str(PROJ / "data" / "copilot" / "screening.json")],
                           capture_output=True, timeout=300,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:                                # noqa: BLE001
            return jsonify({"ok": False, "error": f"搜索失败：{type(e).__name__}: {e}"}), 500
        out = []
        try:
            raw = json.loads((PROJ / "data" / "copilot" / "screening.json").read_text(encoding="utf-8"))
            # **真实形状**：`{stage_name: [候选, ...]}`（2026-09-17 实测：我原来按 list/{"stages":…}
            # 解析 ⇒ 恒返回 0 条，页面就一直说"站上没搜到" ✗）。这里把两种都摊平。
            if isinstance(raw, dict):
                flat = []
                for v in raw.values():
                    flat += v if isinstance(v, list) else [v]
            else:
                flat = raw if isinstance(raw, list) else []
            for c in flat[:12]:
                if not isinstance(c, dict):
                    continue
                jid = c.get("id") or c.get("job_id")
                if not jid:
                    continue
                miss = c.get("missing") or c.get("gaps") or c.get("lack") or []
                if isinstance(miss, str):
                    miss = [x for x in miss.replace("、", ",").split(",") if x.strip()]
                title = c.get("title") or (c.get("doc") or {}).get("title") if isinstance(c.get("doc"), dict) else c.get("title")
                out.append({"code": f"maa://{jid}",
                            "title": str(title or "")[:40],
                            "miss": len(miss) if isinstance(miss, list) else int(miss or 0),
                            "acts": c.get("actions") or c.get("act_count") or 0})
        except Exception as e:                                # noqa: BLE001
            return jsonify({"ok": False, "error": f"读结果失败：{type(e).__name__}: {e}"}), 500
        # **把清单存下来**（2026-09-17 用户："为什么刷新一下，找到了的作业就没了"）:
        # 搜到的码原来只活在页面 DOM 里，一刷新就被重新生成的页面盖掉 ✗。存清单（不是作业正文
        # ——正文照旧"码直跑、跑完删"），页面生成时会把它一起画出来。
        try:
            f = PROJ / "ui" / f"search_{ACCOUNT or 'default'}.json"
            cur = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
            cur[stage] = out[:8]
            f.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:                                # noqa: BLE001
            print(f"[warn] 存搜索清单失败：{type(e).__name__}: {e}", flush=True)
        return jsonify({"ok": True, "stage": stage, "candidates": out[:8]})

    @app.post("/stop")
    def _stop():
        """一键停止（页面上的「■ 停止」按钮）。"""
        if _token and request.headers.get("X-Token") != _token:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        try:
            return jsonify({"ok": True, **stop_all()})
        except Exception as e:                                # noqa: BLE001
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

    @app.post("/here/<stage>")
    def _here(stage: str):
        """**直接打**：人已经把游戏点到这一关的详情页 ⇒ 不导航、不按返回键，就地编队开打。

        为什么（2026-09-17 用户："他为什么每次都要回到桌面，然后再去啊 这怎么慢成这个样子
        能不能做一个不导航直接打的"）: 导航里的返回键在大厅会弹「退出游戏」确认框，点错就把
        游戏退回桌面 ⇒ 下次又得重新唤醒/进活动。人只要把画面停在该关详情页，这条路最快。
        """
        if not STAGE_RE.match(stage):
            return jsonify({"ok": False, "error": f"stage 不在白名单：{stage}"}), 400
        if _token and request.headers.get("X-Token") != _token:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        try:
            return jsonify({"ok": True, **start_retry(stage, here=True)})
        except Exception as e:                                # noqa: BLE001
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 409

    @app.get("/live.png")
    def _live():
        """这个账号的**最新画面**（tools/screen_watch.py 定时抓的那一帧）。

        为什么要有（2026-09-17 用户："你需要有画面监控"）: 这一晚每次真定位问题都靠看一眼
        画面（SR 图是青色六边形、MAA 点到"干员培养"、EX 图有黄点），看日志只能猜。
        """
        p = PROJ / "tools" / "watch" / (ACCOUNT or "9036") / "latest.png"
        if p.is_file():
            resp = send_file(p, mimetype="image/png")
            resp.headers["Cache-Control"] = "no-store"          # 页面靠刷新时间戳取新帧
            return resp
        return ("还没有画面（先跑 python tools/screen_watch.py）", 404)

    @app.post("/calibrate")
    def _calibrate():
        if _token and request.headers.get("X-Token") != _token:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        try:
            return jsonify({"ok": True, **start_calibrate()})
        except Exception as e:                                # noqa: BLE001
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 409

    @app.get("/status")
    def _status():
        return jsonify(status())

    @app.get("/index")
    def _index():
        """多账号总览页（任何一条通道上都能开 /index）——现生成，保证"最近事件"是新的"""
        try:
            subprocess.run([sys.executable, str(PROJ / "tools" / "gen_push_index.py")],
                           capture_output=True, timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:                                # noqa: BLE001
            print(f"[warn] 生成总览页失败：{type(e).__name__}: {e}", flush=True)
        idx = PROJ / "ui" / "index.html"
        return send_file(idx) if idx.is_file() else ("还没有 ui/index.html", 404)

    @app.get("/")
    def _page():
        # **不再每次请求都 spawn 生成器**（2026-09-17 性能项 b）: 以前每开一次页面都起一个
        # python 子进程重跑 gen_push_ui.py（每次都重新载入 OCR/模板）。现在只看时间戳：
        # 状态文件比页面新（= 推图刚写了状态）才重生成；否则直接给现有页面。
        #
        # **生成器自己的改动也要算**（2026-09-17 补）: 只比状态文件的话，改了 gen_push_ui.py
        # 页面会一直给旧的（实测：新加的「打突袭／换作业／扫描干员」三个按钮看不见）。
        page = page_path()
        stale = True
        if page.is_file():
            gen = PROJ / "tools" / "gen_push_ui.py"
            newer = [gen] if gen.is_file() and gen.stat().st_mtime > page.stat().st_mtime else []
            # **搜索清单也要算进"该不该重画"**（2026-09-17 用户："搜完作业，点个刷新，它就又
            # 消失了。它只能用一次"）: 只比状态文件时，搜完清单变了而页面被判"够新" ⇒ 直接发旧页
            # ⇒ 码就没了（第一次是页面 JS 当场画的，所以看着"只能用一次" ✗）。
            _srch = PROJ / "ui" / f"search_{ACCOUNT or 'default'}.json"
            if _srch.is_file() and _srch.stat().st_mtime > page.stat().st_mtime:
                newer.append(_srch)
            state = (find_push_script().parent if find_push_script() else PAGE.parent) \
                / f"push_state_{ACCOUNT}.json" if ACCOUNT else None
            if state is not None and state.is_file() and state.stat().st_mtime > page.stat().st_mtime:
                newer.append(state)
            stale = bool(newer)                          # 页面不比"状态/生成器"旧 ⇒ 够新，直接给
            if stale:
                print(f"[page] 重画（{len(newer)} 个源比页面新："
                      f"{', '.join(p.name for p in newer)}）", flush=True)
        if stale:
            regenerate_page(sanity)
        if not page.is_file():
            return f"还没有 {page}，先跑一次 tools/gen_push_ui.py", 404
        resp = send_file(page)
        # **绝不让浏览器缓存页面**（2026-09-17 用户反复遇到"点了没反应"）: 旧页面里的按钮
        # 指向的接口/函数已经变了 ⇒ 点了静默无事发生，看起来就是"没反应"。宁可每次重传。
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp

    return app


def main() -> None:
    # noqa: PLW0603 —— 一个进程一份，够了
    global _token, EMU, ACCOUNT, POOL
    ap = argparse.ArgumentParser(description="推图 UI 的重试控制通道（只转发 auto_push.py）")
    ap.add_argument("--host", default="127.0.0.1", help="默认只听本机")
    ap.add_argument("--lan", action="store_true", help="= --host 0.0.0.0（手机局域网访问，必须有 token）")
    ap.add_argument("--port", type=int, default=8898)
    ap.add_argument("--token", default="", help="手工指定 token（不给就读 ui/control_token.txt）")
    # 多账号（2026-09-17 用户又开了两个模拟器）：每开一个通道 = 一个模拟器 + 一个账号键
    ap.add_argument("--emu", default="2", help="MuMu 实例号（端口 = 16384 + 32*N）")
    ap.add_argument("--account", default="", help="账号键（如 emu0）：状态/日志/页面都按它分开")
    ap.add_argument("--pool", default="", help="作业池目录名（data/copilot/<pool>，默认 event54_c）")
    # 理智数只是页面上的一个数字，生成器默认是 "—"（会让那个格子空掉），所以沿用上次生成的值
    ap.add_argument("--sanity", default="75")
    a = ap.parse_args()
    EMU, ACCOUNT, POOL = a.emu, a.account, a.pool
    host = "0.0.0.0" if a.lan else a.host

    if TOKEN_FILE.is_file():
        _token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if a.token:
        _token = a.token
    if host not in ("127.0.0.1", "localhost"):
        if not _token:                                           # 局域网必须带 token —— 自动生成一份
            _token = secrets.token_urlsafe(18)
            TOKEN_FILE.write_text(_token, encoding="utf-8")
            print(f"[token] 已生成 {TOKEN_FILE}（局域网必须带它，页面会自动读）", flush=True)

    script = find_push_script()
    print(f"控制通道 http://{host}:{a.port}/ ｜ 推送脚本 {script or '❌ 没找到'}", flush=True)
    print(f"目标：模拟器实例 {EMU}（127.0.0.1:{16384 + 32 * int(EMU)}）"
          f" ｜ 账号键 {ACCOUNT or '（默认 9036）'} ｜ 作业池 {POOL or 'event54_c'}"
          f" ｜ 页面 {page_path().name}", flush=True)
    print(f"token：{'已启用（' + str(TOKEN_FILE) + '）' if _token else '未启用（只听本机 127.0.0.1）'}",
          flush=True)
    if script is None:
        print("[warn] 没找到 auto_push.py ⇒ 点重试会 409（可用 MAA_AUTO_PUSH=路径 指定）", flush=True)
    if PAGE.is_file():
        regenerate_page(a.sanity, ACCOUNT or "9036")             # 让页面里带上最新的 token/状态
    app = build_app(a.sanity)
    app.run(host=host, port=a.port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
