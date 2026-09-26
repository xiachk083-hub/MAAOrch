"""清场回详情页 → 起指定方案。用法: python tools/_launch.py <plan.json>"""
import sys, time, pathlib, subprocess
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from services.ai_agent.percept import AdbScreen
import screen_state as SS
PROJ=pathlib.Path('.').resolve(); plan=sys.argv[1]

def kill_stray_maa() -> int:
    """只按脚本名精确匹配清 MAA（绝不 taskkill /IM python.exe —— 我们自己也是 python）。"""
    import subprocess as _sp
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -match 'maa_copilot_run' } | "
          "ForEach-Object { Write-Output $_.ProcessId; Stop-Process -Id $_.ProcessId -Force }")
    try:
        r = _sp.run(["powershell","-NoProfile","-Command",ps], capture_output=True, text=True, timeout=40, encoding="gbk", errors="replace")
        return len([x for x in (r.stdout or "").split() if x.strip().isdigit()])
    except Exception:
        return -1


def no_maa_running() -> bool:
    import subprocess as _sp
    ps = ("(Get-CimInstance Win32_Process -Filter \"Name='python.exe\" | "
          "Where-Object { $_.CommandLine -match 'maa_copilot_run' }).Count")
    try:
        r = _sp.run(["powershell","-NoProfile","-Command",ps], capture_output=True, text=True, timeout=30, encoding="gbk", errors="replace")
        return (r.stdout or "0").strip() in ("0", "")
    except Exception:
        return False


def arm_observer():
    """起跑前武装观察者，返回本局 mark（精确锚点，日志增量切片用）。"""
    import subprocess as _sp
    log = pathlib.Path("data/agent_run/_obs_preflight.log")
    proc = _sp.Popen([sys.executable, "tools/trace_observer.py"],
                     stdout=open(log, "w", encoding="utf-8"), stderr=_sp.STDOUT, encoding="utf-8", errors="replace",
                     env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    for _ in range(20):
        time.sleep(1)
        if log.exists():
            txt = log.read_text(encoding="utf-8", errors="ignore")
            if "[武装]" in txt:
                return txt.strip().splitlines()[-1]
    return "(未取到 mark)"


def preflight():
    """起跑前三件：① 清 MAA ② 武装观察者 ③ 状态必须是 form/detail，否则先归位。"""
    print("[preflight] ① 清 MAA：", kill_stray_maa(), "个已清")
    if not no_maa_running():
        raise SystemExit("[preflight] MAA 未清零 → 停")
    mark = arm_observer()
    print("[preflight] ② 观察者已武装：", mark)
    from services.ai_agent.percept import AdbScreen
    import screen_state as SS
    sc = AdbScreen("2"); sc.ensure_connected()
    st, conf = SS.state_of_stable(frames=2, gap=0.8)
    print(f"[preflight] ③ 状态 {st} ({conf})")
    if st not in ("form", "detail"):
        print("[preflight] 需归位 → ai_navigate(三层匹配)")
        from tools.ai_navigate import main as nav
        nav(6)
        st, conf = SS.state_of_stable(frames=2, gap=0.8)
        print(f"[preflight] 归位后 {st} ({conf})")
        if st not in ("form", "detail"):
            raise SystemExit(f"[preflight] 归位失败（{st}）→ 按纪律不盲起 MAA")
    # ③b 起跑前自动体检：给本局作业出图并打印冲突（不必我手动画）
    try:
        import viz_plan, glob as _g
        stage = "act16side_ex02"
        cands = _g.glob(f"data/copilot/{stage}*/*.json")
        html, conflicts = viz_plan.render_for(stage, [plan] if plan.endswith(".json") else cands)
        print(f"[preflight] ③b 可视化: {html}")
        for title, warns in conflicts:
            print(f"           {title}: " + (("；".join(warns)) if warns else "无冲突"))
    except Exception as e:
        print(f"[preflight] ③b 可视化失败（不阻塞）: {type(e).__name__}: {e}")
    return mark

mark = preflight()   # 起跑前三件（def 段之后调用，符合"定义先于调用"）
sc=AdbScreen("2"); sc.ensure_connected()
def st():
    f=sc.capture(); return SS.state_of(f) if f is not None else ("other",0)
s,_=st(); print("起始:",s)
if s not in ("detail","form"):
    sc.run_shell(["input","tap","640","660"]); time.sleep(1.6)
    for j in range(8):
        s,c=st(); print(f"  {j}) {s}")
        if s in ("detail","form"): break
        if s=="exmap": sc.run_shell(["input","tap","370","275"]); time.sleep(2.5); continue
        if s in ("win","fail"):
            sc.run_shell(["input","tap","80","45"]); time.sleep(1.5)
            sc.run_shell(["input","tap","960","574"]); time.sleep(2.0); continue
        sc.run_shell(["input","tap","640","660"]); time.sleep(1.6)
s,_=st(); print("最终:",s)
if s in ("detail","form"):
    tag=pathlib.Path(plan).stem
    subprocess.Popen([sys.executable,str(PROJ/"tools"/"clear_runner.py"),"--emu","2",
        "--stage","act16side_ex02","--node","370,275","--plans",plan,"--max-tries","1"],
        stdout=open(f"data/agent_run/_{tag}.log","w",encoding="utf-8"),stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                     env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    print("→ 已起跑:", tag)
else: print("没回到关卡页，不盲起")


