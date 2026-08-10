"""实验室剿灭测试 — 手动起模拟器 → 注入指定剿灭关卡 → 启动 MAA → 看理智变化。
用法: python lab_anni_test.py <emu_idx> <stage> [timeout_s]
  stage: Chernobog@Annihilation | LungmenOutskirts@Annihilation | LungmenDowntown@Annihilation
        （也支持 "Annihilation" = MAA 自动选）
不经过调度队列/账号配置 — 直接操作指定实例目录，测完不留痕（恢复原配置）。
"""
import sys, os, time, json, re, io, subprocess, shutil
from pathlib import Path

ROOT = Path(__file__).parent
EMU_IDX = sys.argv[1] if len(sys.argv) > 1 else "19"
STAGE = sys.argv[2] if len(sys.argv) > 2 else "Chernobog@Annihilation"
TIMEOUT = int(sys.argv[3]) if len(sys.argv) > 3 else 420
INST_NO = int(sys.argv[4]) if len(sys.argv) > 4 else 1  # 用哪个实例目录测

ADB = r"E:\MuMu Player 12\nx_main\adb.exe"
MM = r"E:\MuMu Player 12\nx_main\MuMuManager.exe"
INST_DIR = ROOT / "services" / "maa" / "instances" / str(INST_NO)
CONFIG_DIR = INST_DIR / "config"
ASST_LOG = INST_DIR / "debug" / "asst.log"

def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

def run(cmd, timeout=20, capture=True):
    try:
        r = subprocess.run(cmd, capture_output=capture, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                           encoding="utf-8", errors="replace")
        return r
    except Exception as e:
        log(f"run 异常 {cmd}: {e}")
        return None

def wait_adb_port(deadline):
    """轮询 MuMuManager info 拿 ADB 端口（is_android_started 后才准）。"""
    while time.time() < deadline:
        r = run([MM, "info", "-v", EMU_IDX], timeout=10)
        if r and r.returncode == 0:
            try:
                d = json.loads(r.stdout)
                if d.get("is_android_started"):
                    # MuMuManager info 输出里有 adb_port 或从进程推
                    port = d.get("adb_port") or d.get("port")
                    if port:
                        return f"127.0.0.1:{port}"
            except Exception:
                pass
        # 备用：adb devices 扫 16384+idx*32 附近端口
        r2 = run([ADB, "devices"], timeout=10)
        if r2 and r2.returncode == 0:
            for line in r2.stdout.splitlines():
                m = re.match(r"127\.0\.0\.1:(\d+)\s+device", line.strip())
                if m:
                    p = int(m.group(1))
                    if abs(p - (16384 + int(EMU_IDX) * 32)) < 64:
                        return f"127.0.0.1:{p}"
        time.sleep(5)
    return None

def read_sanity_history():
    """读 asst.log 里所有 Current Sanity 记录（启动前历史，判断本轮有没有新增）。"""
    if not ASST_LOG.exists():
        return []
    out = []
    try:
        with io.open(ASST_LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.search(r"Current Sanity:\s*(\d+)\s*,\s*Max Sanity:\s*(\d+)", line)
                if m:
                    out.append((line[1:20], int(m.group(1))))
    except Exception:
        pass
    return out

def main():
    log(f"== 剿灭测试 emu={EMU_IDX} stage={STAGE} inst={INST_NO} ==")
    if not INST_DIR.exists():
        log(f"实例目录不存在: {INST_DIR}")
        sys.exit(1)

    # 0. 备份实例配置（测试后恢复）
    bak = CONFIG_DIR / "gui.new.json.lab_bak"
    if CONFIG_DIR.joinpath("gui.new.json").exists():
        shutil.copy2(CONFIG_DIR / "gui.new.json", bak)

    # 1. 重启模拟器（保证干净状态 — 上次测试残留的界面会污染 StartUp）
    log(f"重启模拟器 #{EMU_IDX}...")
    run([MM, "control", "-v", EMU_IDX, "shutdown"], timeout=20)
    time.sleep(6)
    run([MM, "control", "-v", EMU_IDX, "launch"], timeout=15)
    time.sleep(5)

    # 2. 等 ADB 端口
    log("等待 ADB 端口...")
    addr = wait_adb_port(time.time() + 120)
    if not addr:
        log("✗ ADB 端口探测超时，无法测试")
        sys.exit(2)
    log(f"ADB: {addr}")

    # 3. 注入配置：StartUp + Fight(剿灭) 仅测试这两个任务
    ac = {
        "name": f"测试emu{EMU_IDX}",
        "id": f"lab_{EMU_IDX}",
        "game_client": "Official",
        "emu_instance_index": EMU_IDX,
        "adb_address": addr,
        "adb_path": ADB,
        "smart_annihilation": STAGE,
        "fight_mode": "priority",
        "fight_default": "1-7",
        "stages": [],
        "fight_priority": {},
        "post_action": "ExitSelf",
    }
    sys.path.insert(0, str(ROOT))
    from app.service_context import ServiceContext
    ctx = ServiceContext(
        log=log, save=lambda: None, set_status=lambda m: None,
        set_theme=lambda m: None, show_dashboard=lambda a: None,
        inject_config=lambda w, a: None, launch_program=lambda w, a: None,
        start_pipeline=lambda: None, restart_api_server=lambda: None,
        accounts=[], warehouse=[], config={"maa_instances": 9, "parallel_max": 4}, groups=[],
    )
    from services.config_injector import ConfigService
    cfg = ConfigService(ctx)
    log("注入配置...")
    cfg.inject_smart(["StartUp", "Fight", "Annihilation"], ac, str(CONFIG_DIR))
    log("注入完成")

    # 4. 清空 asst.log 基线（记录起始理智用旧历史，检测新增长度）
    pre_len = len(read_sanity_history())
    try:
        with io.open(ASST_LOG, "w", encoding="utf-8") as f:
            f.write("")
    except Exception:
        pass

    # 5. 启动 MAA
    log("启动 MAA.exe...")
    real_dir = str(INST_DIR.resolve())
    p = subprocess.Popen([str(INST_DIR / "MAA.exe")], cwd=real_dir)
    log(f"MAA PID={p.pid}")

    # 6. 监控 asst.log：理智变化 / AllTasksCompleted / TaskChainError
    start = time.time()
    sanity_seen = set()
    result = "超时"
    while time.time() - start < TIMEOUT:
        if p.poll() is not None:
            log(f"MAA 进程已退出 rc={p.poll()}")
            result = "MAA退出"
            break
        try:
            with io.open(ASST_LOG, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            content = ""
        if "AllTasksCompleted" in content:
            result = "任务完成"
        if "TaskChainError" in content:
            result = "任务链错误"
            break
        # 理智记录
        for m in re.finditer(r"Current Sanity:\s*(\d+)\s*,\s*Max Sanity:\s*(\d+)", content):
            sanity_seen.add(int(m.group(1)))
        if len(sanity_seen) >= 2:
            # 出现 ≥2 个不同理智值 → 战斗扣理智了
            result = "理智变化"
            break
        time.sleep(10)

    # 7. 停 MAA
    try:
        p.terminate()
        p.wait(5)
    except Exception:
        try: p.kill()
        except Exception: pass
    time.sleep(2)
    run(["taskkill", "/F", "/IM", "MAA.exe"], timeout=10)

    # 8. 结论
    sanity_vals = sorted(sanity_seen)
    log(f"== 结果: {result} ==")
    log(f"本轮理智记录: {sanity_vals}")
    if result == "理智变化":
        log(f"✅ {STAGE} 能刷（理智 {sanity_vals[0]} → {sanity_vals[-1]}）")
    elif result == "任务完成":
        log(f"✅ {STAGE} 任务链完成（需人工确认理智变化）")
    else:
        log(f"❌ {STAGE} 刷不了（{result}）")

    # 9. 恢复原配置
    if bak.exists():
        shutil.copy2(bak, CONFIG_DIR / "gui.new.json")
        bak.unlink()
        log("已恢复实例原配置")
    log("== 测试结束 ==")

if __name__ == "__main__":
    main()
