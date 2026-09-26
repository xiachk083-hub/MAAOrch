from __future__ import annotations
import time, json, urllib.request, threading
from pathlib import Path
from datetime import datetime
from typing import Any
from infrastructure.utils import parse_maa_version, get_platform_key, _version_tuple, make_id, atomic_write
from infrastructure.task_constants import find_mumu_cli

# from ui.widgets.config_card import refresh_config_cards  # lazy import in method


_INSTANCE_LOCK = threading.Lock()
_inst_init_task = None  # prevent GC of BackgroundTask


def _create_instance(inst: Path, source: Path) -> bool:
    """Create a single MAA instance using junctions for large dirs (fast, no full copy)."""
    import shutil, subprocess as _sp
    if (inst / "MAA.exe").exists():
        return True
    if inst.exists():
        try:
            shutil.rmtree(str(inst))
        except PermissionError:
            # Directory locked — try to work with it as-is
            pass
    inst.mkdir(parents=True, exist_ok=True)
    try:
        for item in source.iterdir():
            if item.is_file():
                # 运行时标记不复制 — 实例 1 可能正在被使用（.pid/.meta 是活的
                # MAA 的），复制会把一个 PID 污染到所有实例 → "无空闲 MAA 实例"
                # （2026-08-10 实例池 9 个 .pid 全 = 21900 事故根因）
                if item.name in (".pid", ".meta"):
                    continue
                shutil.copy2(str(item), str(inst / item.name))
        for sub in ("externals", "Python"):
            src_sub = source / sub
            dst_sub = inst / sub
            if src_sub.exists() and not dst_sub.exists():
                r = _sp.run(["cmd", "/c", "mklink", "/J", str(dst_sub), str(src_sub)],
                            capture_output=True, timeout=5, creationflags=_sp.CREATE_NO_WINDOW)
                # junction 必须真建成：mklink rc=0 且 dst 是目录。失败时实例是半成品
                # （MAA.exe 已复制、externals/Python 缺失），会被 (inst/"MAA.exe").exists()
                # 误判为可用实例 → 抛错走下方统一清理，绝不返回成功。
                if r.returncode != 0 or not dst_sub.is_dir():
                    raise RuntimeError(
                        f"mklink /J {sub} 失败 (rc={r.returncode}, dir={dst_sub.is_dir()})")
        # resource 物理复制（不用 junction — 2026-08-14 凌晨根因）：
        # MAA 6.16 启动时把 cache 里 ETag 下载的新资源「移动」到 resource/
        # （Moved cache\resource\tasks.json → resource\tasks.json）。junction
        # 共享 source/resource 时多个实例并发启动互踩同一批文件 → 半移动
        # 状态 → ErrorCongratulations 间歇崩（日服清日常风暴）。每实例独立
        # 复制后 MAA 只搬自己的目录，零竞态。resource 244MB×实例数 ≈ 2.7GB
        # 磁盘代价可接受（externals/Python 不变动，保持 junction 省空间）。
        src_res = source / "resource"
        dst_res = inst / "resource"
        if src_res.exists() and not dst_res.exists():
            shutil.copytree(str(src_res), str(dst_res))
        # Copy config from source (MAA must have been opened at least once to generate defaults)
        src_config = source / "config"
        dst_config = inst / "config"
        if src_config.exists():
            shutil.copytree(str(src_config), str(dst_config), dirs_exist_ok=True)
        else:
            dst_config.mkdir(exist_ok=True)
        for sub in ("cache", "data", "debug"):
            (inst / sub).mkdir(exist_ok=True)
        # Auto-write EmulatorPath so users can open MAA GUI without "path empty" errors
        if cli := find_mumu_cli():
            gj_path = inst / "config" / "gui.json"
            if gj_path.exists():
                import json
                try:
                    d = json.loads(gj_path.read_text(encoding="utf-8"))
                    c = d["Configurations"]["Default"]
                    c["Start.EmulatorPath"] = str(cli)
                    c["Start.EmulatorAddCommand"] = f"control --vmindex {inst.name} launch"
                    atomic_write(gj_path, json.dumps(d, ensure_ascii=False, indent=2))
                except Exception:
                    pass
            gnj_path = inst / "config" / "gui.new.json"
            if gnj_path.exists():
                import json
                try:
                    d2 = json.loads(gnj_path.read_text(encoding="utf-8"))
                    c2 = d2["Configurations"]["Default"]
                    c2.setdefault("connection", {})["EmulatorPath"] = str(cli)
                    atomic_write(gnj_path, json.dumps(d2, ensure_ascii=False, indent=2))
                except Exception:
                    pass
        return True
    except Exception:
        # 创建中途失败 → 清掉这个半成品实例（只删本实例，不碰 source / 其他实例池），
        # 否则重试时 MAA.exe 还在、junction 已缺失，被 (inst/"MAA.exe").exists()
        # 误判为「已就绪」。rmtree 被占用挡住时，退一步拿掉 MAA.exe 这个就绪标志。
        try:
            shutil.rmtree(str(inst))
        except Exception:
            try:
                (inst / "MAA.exe").unlink(missing_ok=True)
            except Exception:
                pass
        return False


def _check_source_ready(source: Path) -> bool:
    """Check if source MAA has been initialized (config with $type fields)."""
    gj = source / "config" / "gui.new.json"
    if not gj.exists():
        return False
    try:
        data = json.loads(gj.read_text(encoding="utf-8"))
        tq = data.get("Configurations", {}).get("Default", {}).get("TaskQueue", [])
        return any("$type" in item for item in tq)
    except Exception:
        return False


def _init_maa_source(source: Path) -> bool:
    """Silently launch MAA to generate default config ($type etc.), then kill it.
    Writes minimize-to-tray config before launch to prevent window flashing."""
    import subprocess, time as _time, json
    exe = source / "MAA.exe"
    if not exe.exists():
        return False
    # Write minimize config before launching to prevent window flash.
    # MAA 6.16 reads gui.new.json top-level Gui.* (nested bools); the old
    # flat "GUI.*"/"Start.*" keys under Global/GUI are 6.11-only and are
    # ignored here (instance gui.new.json has no flat-key reader).
    gj = source / "config" / "gui.new.json"
    try:
        if gj.exists():
            d = json.loads(gj.read_text(encoding="utf-8"))
        else:
            d = {}
        gj.parent.mkdir(parents=True, exist_ok=True)
        g = d.setdefault("Gui", {})
        g["MinimizeToTray"] = True
        g["MinimizeOnStartup"] = True
        g["UseTray"] = True
        # Disable MAA self-update: MAAOrch manages MAA versions itself. If left on,
        # MAA.exe starts downloading OTA components and gets killed mid-update by
        # our $type wait loop → source files end up missing.
        d.setdefault("Resource", {})["AutoUpdate"] = False
        gj.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    try:
        proc = subprocess.Popen([str(exe)], creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = _time.time() + 60
        while _time.time() < deadline:
            if gj.exists():
                try:
                    data = json.loads(gj.read_text(encoding="utf-8"))
                    tq = data.get("Configurations", {}).get("Default", {}).get("TaskQueue", [])
                    # $type OR any TaskQueue entries — MAA may serialize without
                    # $type on some versions; a non-empty TaskQueue means the
                    # config was generated (initialized).
                    if any("$type" in item for item in tq) or len(tq) > 0:
                        proc.terminate()
                        proc.wait(5)
                        return True
                except Exception:
                    pass
            _time.sleep(1)
        proc.kill()
        proc.wait(3)
        return _check_source_ready(source)
    except Exception:
        return False


def _delete_instances(pool: Path) -> None:
    """Delete all instance directories, skipping running ones."""
    import subprocess as _sp
    for inst_dir in pool.glob("*"):
        if not inst_dir.is_dir():
            continue
        pid_file = inst_dir / ".pid"
        running = False
        if pid_file.exists():
            # An existing marker is inconclusive until tasklist confirms the PID is gone.
            running = True
            try:
                pid = int(pid_file.read_text().strip())
                r = _sp.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                            capture_output=True, text=True, timeout=2,
                            creationflags=_sp.CREATE_NO_WINDOW)
                if r.returncode == 0 and isinstance(r.stdout, str) and r.stdout.strip():
                    running = str(pid) in r.stdout
            except Exception:
                pass
        if running:
            continue
        try:
            import shutil as _su
            _su.rmtree(str(inst_dir))
        except Exception:
            pass


def ensure_maa_instances_async(ctx, force=False, progress_cb=None, sync=False) -> None:
    """Pre-create all MAA instances up to parallel_max + 1.
    
    Args:
        force: If True, delete and recreate all instances even if up-to-date.
        progress_cb: Optional callback(current, total) for progress UI.
        sync: If True, run init synchronously in current thread (caller must handle threading).
    """
    global _inst_init_task
    src = _find_maa_source(ctx.config if hasattr(ctx, 'config') else None)
    if not src:
        ctx.log("[实例] 未找到 MAA.exe — 请将 MAA 完整目录复制到 maa/source/（含 MAA.exe 及 resource/ 等）")
        return
    desired = ctx.config.get("parallel_max", 1) + 1
    pool = Path(__file__).parent / "maa" / "instances"

    # Version check — rebuild if MAA version changed
    current_ver = ctx.config.get("maa_version", "")
    built_ver = ctx.config.get("maa_instances_version", "")
    ver_changed = current_ver and built_ver and current_ver != built_ver

    if force or ver_changed:
        ctx.log("[实例] 源 MAA 已变化，删除旧实例...")
        _delete_instances(pool)
        with _INSTANCE_LOCK:
            ctx.config["maa_instances"] = 0

    # Check if source MAA.exe differs from instance 1 (user updated source/)
    src_exe = src / "MAA.exe"
    inst1_exe = pool / "1" / "MAA.exe"
    if not force and not ver_changed and inst1_exe.exists():
        try:
            s_stat = src_exe.stat()
            i_stat = inst1_exe.stat()
            # Compare size only — mtime is unreliable (MAA.exe running rewrites
            # timestamps, causing infinite rebuild loops).
            if s_stat.st_size != i_stat.st_size:
                ctx.log("[实例] maa/source/ 已更新，重建实例池...")
                _delete_instances(pool)
                with _INSTANCE_LOCK:
                    ctx.config["maa_instances"] = 0
        except Exception:
            pass

    # Ensure source MAA config is initialized
    if not _check_source_ready(src):
        ctx.log("[MAA] 正在初始化 MAA 默认配置...")
        if not _init_maa_source(src):
            ctx.log("[MAA] MAA 初始化失败，部分功能可能不可用")
            return

    with _INSTANCE_LOCK:
        existing_ok = sum(1 for i in range(1, desired + 1) if (pool / str(i) / "MAA.exe").exists())
        if existing_ok >= desired and not ver_changed and not force:
            ctx.config["maa_instances"] = desired
            ctx.save()
            return

    def _run_init():
        pool = Path(__file__).parent / "maa" / "instances"
        pool.mkdir(parents=True, exist_ok=True)
        created = 0
        for i in range(1, desired + 1):
            inst = pool / str(i)
            ctx.log(f"[实例] 创建 #{i}...")
            source = src if i == 1 else (pool / "1")
            if _create_instance(inst, source):
                created = i
                ctx.log(f"[实例] #{i} 完成（软链接 resource/externals/Python）")
            else:
                ctx.log(f"[实例] #{i} 创建失败")
                break
            if progress_cb:
                progress_cb(created, desired)
        with _INSTANCE_LOCK:
            ctx.config["maa_instances"] = max(created, ctx.config.get("maa_instances", 0))
            if created >= desired:
                ctx.config["maa_instances_version"] = ctx.config.get("maa_version", "")
            ctx.save()

    if sync:
        _run_init()
        return

    from infrastructure.background_thread import BackgroundTask

    with _INSTANCE_LOCK:
        t = BackgroundTask(_run_init)
        _inst_init_task = t
        t.start()


def _ensure_instance_n(ctx, n: int) -> bool:
    """Lazily create instance #n (n >= 2) if it doesn't exist yet.
    Called when parallel_max is increased at runtime beyond existing instances."""
    pool = Path(__file__).parent / "maa" / "instances"
    inst = pool / str(n)
    if (inst / "MAA.exe").exists():
        return True
    src = pool / "1"
    if not (src / "MAA.exe").exists():
        return False
    return _create_instance(inst, src)


def _find_maa_source(config: dict | None = None) -> Path | None:
    """Find MAA executable source for instance pool creation.
    Priority: config maa_source_path → maa/source/ (user-managed) → maa/v*/ (versioned) → accounts/*/ (legacy)."""
    root = Path(__file__).parent
    ver = root / "maa"
    src = ver / "source"
    # 0. User-configured path from settings
    if config:
        user_path = config.get("maa_source_path", "")
        if user_path and Path(user_path).joinpath("MAA.exe").exists():
            return Path(user_path)
    # Ensure directory exists even on fresh clone (maa/ is not tracked by git)
    src.mkdir(parents=True, exist_ok=True)
    # 1. User-managed source
    if (src / "MAA.exe").exists():
        return src
    # 2. Versioned download directories
    for d in sorted(ver.iterdir()) if ver.exists() else []:
        if d.name == "source":
            continue
        exe = d / "MAA.exe"
        if exe.exists():
            return exe.parent
    # 3. Legacy account directories
    import glob
    maas = list(root.glob("accounts/*/MAA/MAA.exe"))
    if maas:
        return maas[0].parent
    return None
