"""执行 —— 极简执行器第 ③ 步：人已把游戏停在**该关详情页**，这里只负责起 MAA 跑指定作业。

用法:
    python tools/run_job.py --emu 2 --job data/copilot/event54_picked/act54side_ex07_102418.json
    python tools/run_job.py --emu 2 --job <path> --ignore-requirements   # 需要绕过作业里的练度门槛时

**只做三件事**（用户 2026-09-17《极简执行器》规格）:
    ① 清孤儿 MAA（按端口清场 —— 推图被杀时它起的运行器不会跟着死，会跟新的抢同一台模拟器）
    ② 确认当前在**关卡详情页**（不是就报错退出，**绝不盲跑**：MAA 认不出屏时会对着一屏乱点）
    ③ 起 MAA 跑作业 → 等它结束 → 打印退出码与用时 → 退出

**不做**：不导航（你已经放好了）、不判结算（你自己看）、不写状态（不需要）、不归位（你自己弄）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import pathlib
import subprocess
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from infrastructure.ocr import ocr_boxes                       # noqa: E402
from services.ai_agent.fastshot import find_adb                # noqa: E402


def serial_of(emu: str) -> str:
    return f"127.0.0.1:{16384 + 32 * int(emu)}"


def clear_stray_maa(emu: str) -> int:
    """按端口清掉连在这台模拟器上的 MAA 运行器（孤儿）。"""
    port = str(16384 + 32 * int(emu))
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -match 'asst\\.asst' -and "
          f"$_.CommandLine -match ':{port}' }} | ForEach-Object {{ $_.ProcessId }}")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:                                          # noqa: BLE001
        return 0
    n = 0
    for tok in out.split():
        if tok.strip().isdigit():
            subprocess.run(["taskkill", "/F", "/PID", tok.strip()],
                           capture_output=True, timeout=15)
            print(f"  [清场] 干掉旧 MAA 运行器 pid={tok.strip()}")
            n += 1
    return n


def on_detail_page(adb: str, serial: str) -> tuple[bool, str]:
    """当前画面有没有「开始行动」/「代理指挥」—— 这是"停在能开打的地方"的判据。

    （MAA 自己也认屏，但**盲跑很贵**：认不出时它会对着随便一屏点 40 多秒还留下错状态。）
    """
    try:
        p = subprocess.Popen([adb, "-s", serial, "exec-out", "screencap", "-p"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out = p.communicate(timeout=25.0)[0]
    except Exception:                                          # noqa: BLE001
        return False, "(抓帧失败)"
    if not out or len(out) < 10000:
        return False, "(抓帧太小)"
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    ts = [b["text"] for b in (ocr_boxes(img) or [])]
    blob = " ".join(ts)
    # **比之前先把空白去掉**（2026-09-17 实测）：OCR 会把「开始行动」读成「开始 行动」，
    # 带空格的串会让 `in` 判据**假阴性** ⇒ 人明明摆好了却被拦下（"点了没反应"的一种）。
    tight = "".join(blob.split())
    ok = ("开始行动" in tight) or ("代理指挥" in tight)
    # **顺便读回"画面上的是哪一关"**（2026-09-17 用户："脚本只判是不是详情页，不判是不是对的详情页"
    # —— 在 EX-4 的详情页点 EX-8 的码，MAA 会拿 EX-8 的作业去打 EX-4 ✗）。详情页大字标题就印着它。
    # 先认 SR-EX-N（它同时含 "SR-" 子串 ⇒ 必须排在前面），再认 SR-N。
    m = re.search(r"SR-EX-(\d+)", tight) or re.search(r"SR-(\d+)", tight)
    seen_stage = ("EX-" + m.group(1)) if (m and m.group(0).startswith("SR-EX")) else         ("SR-" + m.group(1)) if m else ""
    return ok, blob[:80], seen_stage




def fetch_by_code(code: str) -> pathlib.Path:
    """`maa://12345`（或纯数字）→ 从作业站拉正文 → **临时文件**。

    为什么（用户 2026-09-17《现场搜 + 码直跑》）: MAA 的 `filename` 只吃**本地路径**
    ⇒ 码要落成临时文件；而"**跑完删、不留本地**"是明确要求 ⇒ 用系统临时目录 + finally 删。
    接口实测：`GET https://prts.maa.plus/copilot/get/<id>` → `data.content`（JSON 字符串）。
    """
    import json as _json
    import tempfile
    import urllib.request
    job_id = re.sub(r"\D", "", code)
    if not job_id:
        raise SystemExit(f"[闸] 认不出这个码：{code!r}（要 maa://12345 或纯数字）")
    try:
        req = urllib.request.Request(f"https://prts.maa.plus/copilot/get/{job_id}",
                                     headers={"User-Agent": "MAAOrch/1.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            d = _json.loads(r.read().decode("utf-8"))
    except Exception as e:                                    # noqa: BLE001
        raise SystemExit(f"[闸] 拉作业 {job_id} 失败：{type(e).__name__}: {e}")
    body = (d.get("data") or {}).get("content")
    if isinstance(body, str):
        body = _json.loads(body)
    if not isinstance(body, dict) or not (body.get("actions") or []):
        raise SystemExit(f"[闸] 作业站没给正文（id={job_id}）——换一个码")
    tmp = pathlib.Path(tempfile.gettempdir()) / f"copilot_{job_id}.json"
    tmp.write_text(_json.dumps(body, ensure_ascii=False), encoding="utf-8")
    print(f"   [码直跑] maa://{job_id} → 临时文件 {tmp.name}（{tmp.stat().st_size} 字节，跑完删）")
    return tmp


def stage_of(job: pathlib.Path) -> str:
    """作业文件 → 关卡代号（act54side_ex03_* → EX-3；act54side_03_* → SR-3）。"""
    try:
        sn = str(json.loads(job.read_text(encoding="utf-8")).get("stage_name") or "")
    except Exception:                                          # noqa: BLE001
        return ""
    m = re.search(r"act54side_(ex)?(\d+)$", sn)
    return ("EX-" if m.group(1) else "SR-") + str(int(m.group(2))) if m else ""


def push_dir() -> pathlib.Path | None:
    """状态文件所在目录（auto_push 的沙盒目录）—— 由 MAA_PUSH_DIR 指定，或从 control_server 的常量猜。"""
    import os
    d = os.environ.get("MAA_PUSH_DIR", "") or globals().get("_PUSH_DIR", "")
    if d and pathlib.Path(d).is_dir():
        return pathlib.Path(d)
    for c in pathlib.Path.home().glob(".codex/visualizations/*/*/*/*/sr54"):
        if (c / "auto_push.py").is_file():
            return c
    return None


def write_state(uid: str, stage: str, verdict: str, job: pathlib.Path) -> None:
    """把这一次的结果写进 `push_state_<uid>.json`（**人点 ▶ 打的关也要反映到页面上**）。

    2026-09-17 用户："我说的是这个网站的状态是错的啊 我早就把他给通关了，他还显示未通关"
    —— 根因是这条路原来按规格"不写状态"，用它打的关永远不更新。判据与 auto_push 同一套：
    `行动结束`（且无失败信号）= 三星 ｜ `任务失败`/`理智返还` = 失败 ｜ `演习` = 未确认。
    """
    d = push_dir()
    if d is None or not stage:
        return
    f = d / f"push_state_{uid}.json"
    try:
        st = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {"stages": {}}
    except Exception:                                          # noqa: BLE001
        st = {"stages": {}}
    rec = (st.setdefault("stages", {})).setdefault(stage, {})
    if verdict == "won":
        rec.update({"status": "三星", "job_used": job.name,
                    "verified_by": "点▶执行后读结算页（run_job）",
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")})
        rec.pop("reason", None)
    elif verdict == "lost":
        rec.update({"status": "失败", "reason": "战斗失败（点▶执行）", "job_used": job.name,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")})
    elif verdict == "drill":
        rec.update({"status": "未确认", "reason": "被当演习跑（不计通关）", "job_used": job.name,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")})
    else:
        return
    try:
        f.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"   [state] {stage} → {rec.get('status')}（点▶执行后读结算页回写）")
    except Exception as e:                                     # noqa: BLE001
        print(f"   [state] 回写失败：{type(e).__name__}: {e}")


def read_settlement(adb: str, serial: str) -> str:
    """跑完读一眼**结算页**（MAA 打完不会替我们点掉它）—— 判词照 auto_push 那套已验证的。"""
    try:
        p = subprocess.Popen([adb, "-s", serial, "exec-out", "screencap", "-p"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out = p.communicate(timeout=25.0)[0]
    except Exception:                                          # noqa: BLE001
        return ""
    if not out or len(out) < 10000:
        return ""
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    tight = "".join(" ".join(b["text"] for b in (ocr_boxes(img) or [])).split())
    if "行动结束" not in tight and "任务失败" not in tight and "MISSIONFAILED" not in tight.upper():
        return ""
    if "任务失败" in tight or "MISSIONFAILED" in tight.upper() or "理智返还" in tight:
        return "lost"
    if "演习" in tight or "PRACTICE" in tight.upper():
        return "drill"
    return "won"


def main() -> None:
    ap = argparse.ArgumentParser(description="执行（极简执行器第 ③ 步）")
    ap.add_argument("--emu", required=True)
    ap.add_argument("--job", required=True, help="作业文件路径")
    ap.add_argument("--timeout", type=float, default=900.0, help="MAA 最长跑多少秒")
    ap.add_argument("--push-dir", default="", help="auto_push 所在目录（状态文件在那儿）")
    ap.add_argument("--uid", default="", help="账号键（回写 push_state_<uid>.json 用）")
    ap.add_argument("--no-support", action="store_true",
                    help="关掉「借助战」（缺核心时不再借，编不满就停）")
    ap.add_argument("--ignore-requirements", action="store_true",
                    help="绕过作业里的精英/等级/技能门槛（默认**不绕**：作者常把门槛写成"
                         "『干员组最低需求精1后期，请勿忽略』）")
    a = ap.parse_args()

    globals()["_PUSH_DIR"] = a.push_dir
    # **码直跑**（用户 2026-09-17《现场搜 + 码直跑》）：--job 可以是 maa://12345 或纯数字
    tmp_job = None
    if a.job.startswith("maa://") or a.job.strip().isdigit():
        tmp_job = fetch_by_code(a.job)
        job = tmp_job
    else:
        job = pathlib.Path(a.job)
        if not job.is_absolute():
            job = PROJ / job
    # ① 清场
    clear_stray_maa(a.emu)
    # ② 作业存在
    if not job.is_file():
        print(f"❌ 作业不存在: {job}")
        raise SystemExit(1)
    # ③ 在详情页
    adb, serial = find_adb(), serial_of(a.emu)
    ok, blob, seen_stage = on_detail_page(adb, serial)
    if not ok:
        print(f"❌ 现在不在关卡详情页（画面：{blob!r}）⇒ 停手，没动游戏。"
              f"先把游戏点到那一关再跑本脚本。")
        raise SystemExit(1)
    want = stage_of(job)                       # 作业声明的关卡
    if want and seen_stage and seen_stage != want:
        print(f"❌ 当前在 {seen_stage} 的详情页，你要跑 {want} ⇒ 停手（点错关了，没动游戏）")
        raise SystemExit(1)
    if want and not seen_stage:
        print(f"⚠️ 读不出画面上的关卡名（照 {want} 跑；若不对请自己确认）")
    jobname = job if isinstance(job, str) else job.name
    print(f"✅ 在 {seen_stage or '?'} 详情页（目标 {want or '?'}）⇒ 起 MAA：{jobname}")

    cmd = [sys.executable, str(PROJ / "tools" / "maa_copilot_run.py"),
           "--emu", a.emu, "--copilot", str(job),
           # 每台模拟器一个 MAA 实例（2026-09-17 用户："我运行另外一个，它就断掉了"）
           "--instance", str(int(a.emu) + 1),
           "--touch", "maatouch", "--no-startup", "--timeout", str(int(a.timeout))]
    if a.ignore_requirements:
        cmd.append("--ignore-requirements")
    if a.no_support:
        cmd.append("--no-support")
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=a.timeout + 180)
    el = int(time.time() - t0)
    out = (r.stdout or "") + (r.stderr or "")
    deploys = out.count('"action":"Deploy"')
    print(f"MAA 退出码: {r.returncode} ｜ 用时: {el}s ｜ 部署动作: {deploys} 次")
    for line in out.strip().splitlines()[-6:]:
        print("  ", line[:150])
    # **判词**（2026-09-17 用户："我给你发这个，就是告诉你这个是不正常的"）:
    # 退出码 0 **不代表打了**（打输/被当演习/对错屏跑完，退出码都是 0）。人看的是这一句：
    if "CopilotConfig parse failed" in out or "invalid params" in out:
        # **这份作业 MAA 根本读不了**（2026-09-17 实测：exit 0、秒退，日志原文
        # `CopilotConfig parse failed` / `append_task | invalid params`）⇒ 换一份作业。
        print(f"⚠️ 这份作业 MAA 解析不了（版本/格式不兼容）⇒ 换一份；用时 {el}s、部署 {deploys} 次")
    elif deploys == 0 or el < 30:
        print(f"⚠️ 没打起来：部署 0 次 / 只用 {el}s ⇒ 关卡多半没解锁，或画面不在那一关（看游戏画面确认）")
    else:
        print(f"✅ 真打了一局：{deploys} 次部署、用时 {el}s（输赢请去游戏里看结算）")
    # **回写状态**（看结算页；没打起来就不写）—— 人点 ▶ 打的关也要反映到页面上
    if deploys > 0 and el >= 30:
        v = read_settlement(adb, serial)
        write_state(a.uid, stage_of(job), v, job)
    if tmp_job is not None:
        tmp_job.unlink(missing_ok=True)          # **跑完删，不留本地**
        print("   [码直跑] 临时文件已删")
    raise SystemExit(0 if r.returncode == 0 else 1)


if __name__ == "__main__":
    main()
