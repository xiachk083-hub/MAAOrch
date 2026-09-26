"""FastAPI-based API server for MAAOrch."""
from __future__ import annotations
import asyncio, json, time, re, os, sys, hmac, subprocess, io, zipfile, shutil, urllib.request, uuid, mimetypes, threading, tempfile
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn


# ── Shared state ──

_OPLOG: list[dict] = []
# 根日志收口（2026-08-14 Step 5）：oplog 写者=本文件 → 直接迁 logs/；
# 读兼容旧根目录文件（历史审计）
_OPLOG_FILE = Path(__file__).parent.parent / "logs" / "oplog.json"
_OPLOG_LEGACY = Path(__file__).parent.parent / "oplog.json"

def _load_oplog() -> list[dict]:
    try:
        if _OPLOG_FILE.exists():
            return json.loads(_OPLOG_FILE.read_text(encoding="utf-8"))[-200:]
    except Exception:
        pass
    try:
        if _OPLOG_LEGACY.exists():  # 旧路径兜底（历史审计）
            return json.loads(_OPLOG_LEGACY.read_text(encoding="utf-8"))[-200:]
    except Exception:
        pass
    return []

def _save_oplog() -> None:
    try:
        from infrastructure.utils import atomic_write
        try:
            _OPLOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        atomic_write(_OPLOG_FILE, json.dumps(_OPLOG[-200:], ensure_ascii=False))
    except Exception:
        pass

_OPLOG = _load_oplog()

def _log_op(action: str, detail: str = "") -> None:
    global _OPLOG
    _OPLOG.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action, "detail": detail})
    if len(_OPLOG) > 200:
        _OPLOG = _OPLOG[-200:]
    _save_oplog()


def _restart_hot(root: Path, mw: Any = None) -> None:
    """TRUE hot reload: re-import changed modules in-place, no restart.
    Running tasks keep their process; next task launch uses the new code."""
    import importlib
    for mod in ("services.config_injector", "services.runner",
                "services.launch_queue",
                "services.dispatch_pool", "services.instance_pool",
                "services.maa_download",
                "infrastructure.task_constants", "models.config_manager",
                "models.account"):
        try:
            importlib.reload(sys.modules[mod])
        except KeyError:
            try:
                importlib.import_module(mod)
            except Exception:
                pass
        except Exception:
            pass


def _hot_reload_zip(root: Path, mw: Any) -> None:
    """Zip-mode hot reload: download main.zip, extract ONLY code files
    (skip services/maa, models/config.json, logs — user data preserved),
    then importlib.reload the changed modules — no process restart."""
    try:
        import tempfile
        from services.maa_download import _get_download_url, _download_zip
        tmp = Path(tempfile.gettempdir()) / f"maorch_hot_{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True, exist_ok=True)
        url, _tag = _get_download_url()
        if not url:
            return
        zpath = tmp / "main.zip"
        if not _download_zip(url, zpath, lambda m: None):
            return
        with zipfile.ZipFile(zpath) as z:
            # Integrity check — a corrupted zip would overwrite code and break
            # the project. Only proceed if the archive passes CRC validation.
            if z.testzip() is not None:
                return
            names = z.namelist()
            # Strip top-level dir (main-<branch>)
            top = names[0].split("/")[0] if names else ""
            skip_dirs = ("services/maa", "models/config.json", "logs/")
            for n in names:
                rel = n[len(top) + 1:] if top and n.startswith(top + "/") else n
                if not rel or rel.endswith("/"):
                    continue
                if any(rel == s or rel.startswith(s) for s in skip_dirs):
                    continue
                # Only update code files (.py/.html/.js/.css/.bat) — never
                # binary data; a truncated download can't corrupt the project.
                if not rel.endswith((".py", ".html", ".js", ".css", ".bat", ".md")):
                    continue
                dst = root / rel
                try:
                    if n.endswith("/"):
                        dst.mkdir(parents=True, exist_ok=True)
                    else:
                        data = z.read(n)
                        if not data:  # empty file in zip = corrupt
                            continue
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        dst.write_bytes(data)
                except Exception:
                    pass
    except Exception:
        pass
    _restart_hot(root, mw)


def _get_web_schedule_tasks(mw: Any, include_anni: bool = True, only_anni: bool = False) -> list[str]:
    mode = mw.config.get("schedule_mode", "daily") if hasattr(mw, 'config') else "daily"
    if mode == "roguelike":
        tasks = ["StartUp", "Roguelike"]
        if only_anni or include_anni:
            tasks.append("Annihilation")
        return tasks
    if mode == "reclamation":
        tasks = ["StartUp", "Reclamation"]
        if only_anni or include_anni:
            tasks.append("Annihilation")
        return tasks
    sg = mw.config.get("smart_global", {})
    tasks = ["StartUp"]
    if only_anni:
        tasks.append("Annihilation")
        return tasks
    if sg.get("annihilation_enabled", True) and include_anni:
        tasks.append("Annihilation")
    tasks.append("Fight")
    if sg.get("recruit_enabled", True):
        tasks.append("Recruit")
    if sg.get("infrast_enabled", True):
        tasks.append("Infrast")
    if sg.get("mall_enabled", True):
        tasks.append("Mall")
    tasks.append("Award")
    return tasks


# ── Rate limiter ──

_rate_buckets: dict[str, list[float]] = {}

def _check_rate(ip: str, limit: int = 200) -> bool:
    if ip in ("127.0.0.1", "::1"):
        return True
    now = time.time()
    bucket = _rate_buckets.get(ip, [])
    bucket = [t for t in bucket if t > now - 60]
    if len(bucket) >= limit:
        _rate_buckets[ip] = bucket
        return False
    bucket.append(now)
    _rate_buckets[ip] = bucket
    return True


# ── Screenshot cache (connect wall) ──
# 2.5s per-emulator cache + global serialization of adb screencap:
# N emulators polling every 3-5s would otherwise saturate the adb channel
# and slow down MAA tasks running on the same adb server.
_shot_cache: dict[str, tuple[float, bytes]] = {}
_shot_lock = threading.Lock()


# ── App factory ──

def create_app(mw: Any) -> FastAPI:
    app = FastAPI(title="MAAOrch", docs_url=None, redoc_url=None)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    token = mw.config.get("api_token", "") if hasattr(mw, 'config') else ""

    @app.middleware("http")
    async def auth_rate_middleware(request: Request, call_next):
        ip = request.client.host if request.client else "127.0.0.1"
        path = request.url.path
        # Screenshot polling is a required high-frequency read-only resource
        # (N emulators × every 2s easily exceeds the 200/min limit and gets
        # 429'd → the connect wall shows broken images). Exempt it from the
        # rate limiter; all other APIs keep the limit.
        if not path.endswith("/screenshot") and not _check_rate(ip):
            return JSONResponse({"error": "rate_limited"}, 429)
        # Static files don't need auth. api_token 配置后强制校验（含 SSE —
        # 2026-08-14 安全加固：SSE 是唯一开放端点，EventSource 无法带
        # header → token 走 query 参数）：不带 token 或带错 token 一律
        # 401（弱鉴权"仅拦错误 token"会放行所有无 token 请求 —
        # 2026-08-10 安全加固）。留空=不鉴权（兼容本地）。
        # MAA 远程控制轮询（本地进程每秒 POST — 无 token，127.0.0.1 豁免）
        if path.startswith("/api/maa_remote") and ip in ("127.0.0.1", "::1", "localhost"):
            return await call_next(request)
        if path.startswith("/api/") and token:
            h = request.headers.get("x-agent-token", "")
            if not h:
                h = request.query_params.get("token", "")  # SSE: EventSource 无 header
            if not h or not hmac.compare_digest(h, token):
                # 安全审计：未授权访问来源留痕（IP + 路径 + token 前缀）
                try:
                    mw._log(f"⚠️ 未授权访问 {path} from {ip} (token={h[:8]!r}...)")
                except Exception:
                    pass
                return JSONResponse({"error": "unauthorized"}, 401)
        return await call_next(request)

    # ── Helpers ──

    _CF = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0

    def _proc_pid(a):
        progs = [w for w in mw.warehouse if w.get("account_ref") == a["id"]]
        return progs[0]["id"] if progs else ""

    def _is_running(pid):
        return pid in mw._proc_status

    def _account_state(aid):
        try:
            from models.account_state import AccountState
            return AccountState(aid).data
        except Exception:
            return {}

    def _account_by_idx(idx):
        if idx < 0 or idx >= len(mw.accounts):
            raise HTTPException(404)
        return mw.accounts[idx]

    # ── Queue helpers ──

    def _lq():
        lq = getattr(mw, "launch_queue", None)
        if not lq:
            raise HTTPException(503, "queue not available")
        return lq

    def _runner():
        r = getattr(mw, 'runner', None)
        if not r:
            raise HTTPException(503, "runner not available")
        return r

    # ═══════════════════════════════════════════
    # GET endpoints
    # ═══════════════════════════════════════════
    # ── 域分区导航（2026-08-15 整理）──
    #   status/node   L276 系统状态/节点
    #   account       L479 账号状态/截图/详情
    #   stats/export  L564 统计/导出
    #   probe         L701 关卡检测
    #   depot         L806 仓库/企鹅/活动
    #   health/events L1079 健康/事件/公告
    #   queue/shots   L1223 队列/截图视图
    #   maa_log       L1342 MAA 日志
    #   accounts      L1452 账号管理
    #   sse/node      L1673 事件流/节点注册
    #   queue_action  L1782 队列操作
    #   account_edit  L1848 账号编辑
    #   kill_audit    L2033 守护审计
    #   maa_remote    L2071 MAA 远程控制
    #   emulator      L2194 模拟器操作
    #   system        L2309 系统管理
    #   smart_action  L2484 智能调度
    #   connect       L2642 连接页
    #   maa_manage    L2948 MAA 版本/实例管理
    #   orch          L3184 项目自更新
    #   config        L3408 配置导入导出

    @app.get("/api/status")
    def handle_status():
        accts = []
        alive = set()
        try:
            r = subprocess.run(["tasklist", "/NH", "/FI", "IMAGENAME eq MAA.exe"],
                               capture_output=True, text=True, timeout=5, creationflags=_CF)
            for line in r.stdout.splitlines():
                if "MAA.exe" in line:
                    alive.add(line.split()[1])
        except:
            pass
        for i, a in enumerate(mw.accounts):
            pid = _proc_pid(a)
            running = pid in mw._proc_status and pid in alive if pid else False
            elapsed = 0
            if running and pid in mw._proc_start_times:
                elapsed = int(time.time() - mw._proc_start_times[pid])
            accts.append({"name": a.get("name", ""), "index": i, "running": running,
                          "elapsed": elapsed, "adb": a.get("adb_address", ""),
                          "emu_index": a.get("emu_instance_index", "")})
        rc = getattr(mw, 'runner', None)
        running_count = len(rc._active) if rc else sum(1 for a in accts if a["running"])
        return {"accounts": accts, "pipeline_running": False, "running": running_count}


    @app.get("/api/node/dashboard")
    def handle_node_dashboard():
        import psutil as _ps
        try:
            mem = _ps.virtual_memory()
            cpu_pct = _ps.cpu_percent(interval=0)
            cpu_count = _ps.cpu_count()
        except:
            mem = type('m', (), {'total': 1, 'available': 1, 'percent': 0})()
            cpu_pct = 0
            cpu_count = 1
        processes = []
        runner = getattr(mw, 'runner', None)
        accounts_list = getattr(mw, 'accounts', []) or []
        proc_status = getattr(mw, "_proc_status", set())
        seen_pids = set()
        seen_aids = set()
        actual_pids = set()
        try:
            r2 = subprocess.run(["tasklist", "/NH", "/FI", "IMAGENAME eq MAA.exe"],
                                capture_output=True, text=True, timeout=5, creationflags=_CF)
            for _line in r2.stdout.splitlines():
                if "MAA.exe" not in _line:
                    continue
                _parts = _line.split()
                if len(_parts) >= 2:
                    try:
                        actual_pids.add(int(_parts[1]))
                    except:
                        pass
        except:
            pass
        if runner and hasattr(runner, '_proc_info'):
            for aid, info in list(runner._proc_info.items()):
                try:
                    ac = next((a for a in accounts_list if a.get("id") == aid), None)
                    if not ac:
                        continue
                    maa = info.get("maa", {}) or {}
                    maa_pid = maa.get("pid")
                    if aid in seen_aids:
                        continue
                    is_running = aid in proc_status
                    if not maa_pid and not is_running:
                        continue
                    if maa_pid and (maa_pid in seen_pids or maa_pid not in actual_pids):
                        continue
                    seen_aids.add(aid)
                    if maa_pid:
                        seen_pids.add(maa_pid)
                    emu = info.get("emu", {}) or {}
                    processes.append({
                        "aid": aid, "name": ac.get("name", aid),
                        "running": is_running if not maa_pid else maa_pid in actual_pids,
                        "last_task": ac.get("_last_task", ""),
                        "maa_mem_mb": maa.get("mem_mb", 0),
                        "maa_cpu_pct": maa.get("cpu_pct", 0),
                        "maa_pid": maa_pid,
                        "emu_mem_mb": emu.get("mem_mb", 0),
                        "emu_cpu_pct": emu.get("cpu_pct", 0),
                        "emu_pid": emu.get("pid"),
                        "emu_name": emu.get("name", ""),
                    })
                except:
                    continue
        lq = getattr(mw, "launch_queue", None)
        if lq and hasattr(lq, '_active_emus'):
            active_emus = getattr(lq, '_active_emus', {})
            for ac in accounts_list:
                aid = ac.get("id", "")
                if aid not in seen_aids and aid in active_emus.values():
                    processes.append({
                        "aid": aid, "name": ac.get("name", aid),
                        "running": True, "last_task": "", "maa_mem_mb": 0, "maa_cpu_pct": 0,
                        "maa_pid": None, "emu_mem_mb": 0, "emu_cpu_pct": 0,
                        "emu_pid": None, "emu_name": "",
                    })
                    seen_aids.add(aid)
        try:
            r2 = subprocess.run(["tasklist", "/NH", "/FI", "IMAGENAME eq MAA.exe"],
                                capture_output=True, text=True, timeout=5, creationflags=_CF)
            maa_count = r2.stdout.count("MAA.exe")
            if maa_count > 0 and len(processes) < maa_count:
                seen_pids = {p.get("maa_pid") for p in processes if p.get("maa_pid")}
                for pf in Path(__file__).parent.parent.glob("services/maa/instances/*/.pid"):
                    try:
                        pid_str = pf.read_text().strip()
                        if not pid_str:
                            continue
                        pid = int(pid_str)
                        if pid in seen_pids or pid not in actual_pids:
                            continue
                        meta_file = pf.parent / ".meta"
                        name = "MAA-" + pid_str[-4:]
                        meta_aid = ""
                        if meta_file.exists():
                            meta = meta_file.read_text().strip()
                            if "|" in meta:
                                meta_aid, name = meta.split("|", 1)
                                if meta_aid in seen_aids:
                                    continue
                        processes.append({
                            "aid": meta_aid, "name": name, "running": True,
                            "last_task": "", "maa_mem_mb": 0, "maa_cpu_pct": 0,
                            "maa_pid": pid, "emu_mem_mb": 0, "emu_cpu_pct": 0,
                            "emu_pid": None, "emu_name": "",
                        })
                        seen_pids.add(pid)
                        if meta_aid:
                            seen_aids.add(meta_aid)
                    except:
                        pass
        except:
            pass
        gpu = {"name": "", "usage": 0, "mem_used_mb": 0, "mem_total_mb": 0}
        try:
            o = subprocess.check_output(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                                         "--format=csv,noheader,nounits"], timeout=2, encoding="utf-8",
                                        errors="replace", creationflags=_CF)
            parts = o.strip().split(", ")
            if len(parts) >= 4:
                gpu = {"name": parts[0], "usage": float(parts[1]),
                       "mem_used_mb": int(float(parts[2])), "mem_total_mb": int(float(parts[3]))}
        except:
            pass
        seen = set()
        deduped = []
        for p in processes:
            n = p.get("name", "")
            if n and n not in seen:
                seen.add(n)
                deduped.append(p)
        processes = deduped
        total_proc_mem = sum((p.get("maa_mem_mb", 0) or 0) + (p.get("emu_mem_mb", 0) or 0) for p in processes) if processes else 0
        running = sum(1 for p in processes if p["running"])
        parallel_max = mw.config.get("parallel_max", 3)
        by_parallel = max(0, parallel_max - running)
        est_per = max(1, (total_proc_mem / max(running, 1))) if running > 0 else 1500
        by_mem = max(0, int((mem.available / 1048576) / est_per))
        gpu_free = max(0, gpu["mem_total_mb"] - gpu["mem_used_mb"])
        by_gpu = max(0, int(gpu_free / est_per)) if gpu["mem_total_mb"] > 0 else 99
        capacity = min(by_parallel, by_mem, by_gpu)
        limit_by = "并行上限" if capacity == by_parallel else ("内存" if capacity == by_mem else "显存")
        samples = getattr(mw, "_res_samples", [])
        gantt = getattr(mw, "_gantt_events", [])
        return {
            "ok": True,
            "system": {"cpu_pct": cpu_pct, "cpu_count": cpu_count,
                       "memory_total_mb": mem.total // 1048576,
                       "memory_available_mb": mem.available // 1048576,
                       "memory_pct": mem.percent},
            "gpu": gpu, "processes": processes,
            "capacity": {"parallel_max": parallel_max, "running": running,
                         "by_parallel": by_parallel, "by_memory": by_mem, "by_gpu": by_gpu,
                         "max": capacity, "limit_by": limit_by,
                         "est_per_instance_mb": int(est_per),
                         "deficit": mw.config.get("deficit", 0),
                         "stuck_timeout": mw.config.get("stuck_timeout", 10)},
            "samples": samples[-360:] if samples else [],
            "gantt": gantt[-100:] if gantt else [],
        }

    @app.get("/api/account/{idx}/status")
    def handle_account_status(idx: int):
        a = _account_by_idx(idx)
        pid = _proc_pid(a)
        running = _is_running(pid)
        elapsed = 0
        if running and pid in mw._proc_start_times:
            elapsed = int(time.time() - mw._proc_start_times[pid])
        return {"name": a.get("name", ""), "running": running, "elapsed": elapsed}








    @app.get("/api/stats/dashboard")
    def handle_stats_dashboard():
        from models.stats import RunStats
        today = datetime.now()
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        heatmap = [[0] * 24 for _ in range(7)]
        daily_runs = {}
        daily_drops = {}
        all_drops = {}
        total_runs = 0
        for a in mw.accounts:
            st = RunStats(a["id"])
            for run in st._data.get("runs", []):
                total_runs += 1
                try:
                    dt = datetime.strptime(run["ts"], "%Y-%m-%d %H:%M:%S")
                except:
                    continue
                dow = dt.weekday()
                hr = dt.hour
                heatmap[dow][hr] += 1
                date_key = dt.strftime("%Y-%m-%d")
                daily_runs[date_key] = daily_runs.get(date_key, 0) + 1
                for item, qty in run.get("drops", {}).items():
                    all_drops[item] = all_drops.get(item, 0) + qty
                    if date_key not in daily_drops:
                        daily_drops[date_key] = {}
                    daily_drops[date_key][item] = daily_drops[date_key].get(item, 0) + qty
        top_mats = sorted(all_drops, key=all_drops.get, reverse=True)[:10]
        today_key = today.strftime("%Y-%m-%d")
        return {
            "ok": True,
            "summary": {"total_runs": total_runs, "today_runs": daily_runs.get(today_key, 0),
                        "accounts": len(mw.accounts), "total_drops": sum(all_drops.values())},
            "heatmap": heatmap, "weekdays": weekdays,
            "daily_runs": daily_runs, "daily_drops": daily_drops,
            "top_materials": top_mats,
        }








    # ── L3 产线工作台（/api/mas/*）────────────────────────────────────
    # 数据源见 services/mas_l3.py：主机实时快照（SSH）+ AUTO-MAS 历史聚合 + 本地留痕。
    # 两个动作都复用已有 CLI 工具，不在这里重写发车逻辑。
    @app.get("/api/mas/status")
    def handle_mas_status(days: int = 7, refresh: int = 0):
        from services.mas_l3 import collect_status
        return collect_status(days=max(1, min(int(days or 7), 30)), refresh=bool(refresh))

    @app.post("/api/mas/rerun")
    def handle_mas_rerun(body: dict = None):
        from services.mas_l3 import rerun_async
        return rerun_async((body or {}).get("accounts") or [])

    @app.post("/api/mas/cleanup")
    def handle_mas_cleanup(body: dict = None):
        from services.mas_l3 import cleanup
        return cleanup()

    # ── Stage probe（关卡检测 — docs/STAGE_PROBE.md）──
    _probe_results: dict = {}
    _probe_lock = threading.Lock()

    @app.post("/api/probe/stage")
    def handle_probe_stage(body: dict):
        """Probe an account's ability on a stage (async).
        Body: {account_id, stage} — result lands in _probe_results + account.stage_ability."""
        from services.stage_probe import StageProbe
        aid = body.get("account_id", "")
        stage = body.get("stage", "")
        if not aid or not stage:
            raise HTTPException(400, "account_id and stage required")
        ac = next((a for a in mw.accounts if a.get("id") == aid), None)
        if not ac:
            raise HTTPException(404, "account not found")
        emu = ac.get("emu_instance_index", "")
        if not emu:
            raise HTTPException(400, "account has no emulator")
        client = {"Bilibili": "Bilibili", "YoStarJP": "YoStarJP", "YoStarEN": "YoStarEN"}.get(
            ac.get("game_client", ""), "Official")

        def _run():
            # 保护标记: probe 直接 MuMuManager launch 启动模拟器，没走队列/
            # v2 状态机 — 回收器看 state_since 旧值 + 无账号映射会当闲置
            # 关掉（2026-08-12 #19 启动 5s 后被回收 shutdown，probe 卡死）。
            # mark_external 重置外部闲置计时（30min 重新算）+ manual_emu_started
            # 10 分钟保护期 — 足够覆盖一轮检测（5 关 × 2-4min）。
            try:
                v2 = getattr(mw, "emu_service_v2", None)
                if v2 is not None:
                    v2.mark_external(str(emu))
                mts = getattr(mw, "manual_emu_started", None)
                if mts is not None:
                    mts[str(emu)] = time.time()
            except Exception:
                pass
            try:
                probe = StageProbe(emu, client_type=client)
                result = probe.probe(stage)
                result["logs"] = probe.logs[-30:]
                with _probe_lock:
                    _probe_results[aid] = result
                # 持久化到账号 stage_ability
                ability = dict(ac.get("stage_ability") or {})
                ability[stage] = result["verdict"]
                ac["stage_ability"] = ability
                try:
                    from models.config_manager import save_config
                    save_config(mw.config)
                except Exception:
                    pass
                mw._log(f"[检测] {ac.get('name','')} {stage}: {result['verdict']}")
            except Exception as e:
                with _probe_lock:
                    _probe_results[aid] = {"verdict": "ERROR", "score": 0.0,
                                           "detail": str(e)[:300]}

        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "account_id": aid, "stage": stage, "status": "running"}

    @app.post("/api/probe/anni_weekly")
    def handle_probe_anni_weekly(body: dict):
        """剿灭周常检测（2026-08-17）: 终端页有无剿灭卡片判定本周可刷。

        Body: {account_id} → 结果 CAN_FARM(本周未刷)/DONE(本周已刷)/SHOTFAIL，
        存 _probe_results["<aid>@anni_weekly"]，前端轮询 /api/probe/status。
        """
        from services.stage_probe import StageProbe
        aid = body.get("account_id", "")
        if not aid:
            raise HTTPException(400, "account_id required")
        ac = next((a for a in mw.accounts if a.get("id") == aid), None)
        if not ac:
            raise HTTPException(404, "account not found")
        emu = ac.get("emu_instance_index", "")
        if not emu:
            raise HTTPException(400, "account has no emulator")
        client = {"Bilibili": "Bilibili", "YoStarJP": "YoStarJP", "YoStarEN": "YoStarEN"}.get(
            ac.get("game_client", ""), "Official")

        def _run():
            try:
                v2 = getattr(mw, "emu_service_v2", None)
                if v2 is not None:
                    v2.mark_external(str(emu))
                mts = getattr(mw, "manual_emu_started", None)
                if mts is not None:
                    mts[str(emu)] = time.time()
            except Exception:
                pass
            key = f"{aid}@anni_weekly"
            try:
                probe = StageProbe(emu, client_type=client)
                result = probe.probe_annihilation_weekly()
                result["logs"] = getattr(probe, "logs", [])[-30:]
                with _probe_lock:
                    _probe_results[key] = result
                mw._log(f"[剿灭周常] {ac.get('name','')}: {result['verdict']}")
            except Exception as e:
                with _probe_lock:
                    _probe_results[key] = {"verdict": "ERROR", "score": 0.0,
                                           "detail": str(e)[:300]}

        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "account_id": aid, "status": "running"}

    @app.get("/api/probe/results")
    def handle_probe_results():
        with _probe_lock:
            return {"ok": True, "results": dict(_probe_results)}

    @app.post("/api/probe/apply_plan")
    def handle_probe_apply_plan():
        """产线自动规划: stage_ability → smart_annihilation 自动映射。
        市中心能刷 → LungmenDowntown; 仅外环能刷 → LungmenOutskirts;
        都不能/DONE → 清空剿灭配置。"""
        changes = []
        for a in mw.accounts:
            ability = a.get("stage_ability") or {}
            if not ability:
                continue
            downtown = ability.get("LungmenDowntown@Annihilation")
            outskirts = ability.get("LungmenOutskirts@Annihilation")
            new_anni = ""
            if downtown == "CAN_FARM":
                new_anni = "LungmenDowntown@Annihilation"
            elif outskirts == "CAN_FARM":
                new_anni = "LungmenOutskirts@Annihilation"
            old = a.get("smart_annihilation", "")
            if new_anni != old:
                a["smart_annihilation"] = new_anni
                changes.append({"name": a.get("name", ""),
                                "from": old or "—", "to": new_anni or "—"})
        try:
            from models.config_manager import save_config
            save_config(mw.config)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        mw._log(f"[产线规划] 更新 {len(changes)} 个账号的剿灭配置")
        return {"ok": True, "updated": len(changes), "changes": changes}

    @app.get("/api/probe/status")
    def handle_probe_status():
        with _probe_lock:
            latest = {}
            for aid, r in _probe_results.items():
                latest[aid] = {k: r.get(k) for k in ("verdict", "score", "detail")}
            return {"ok": True, "results": latest}

    # ═══════════════════════════════════════════
    # 材料规划（仓库识别 + 水位计划 — 2026-08-13 产品化）
    # 模型: 扫描器独立 Asst 子进程（不进 queue，用户拍板），互斥跳过忙的账号
    # ═══════════════════════════════════════════



    @app.get("/api/depot/plan")
    def handle_depot_plan(account_id: str = ""):
        """该账号最近一次扫描生成的规划（models/accounts/<id>/plan.json）。"""
        if not account_id:
            raise HTTPException(400, "account_id required")
        # 2026-09-26 安全修复（P1 路径穿越）：原先直接把 account_id 拼进路径、且只做 exists()，
        # account_id="../../.." 即可读到仓库外的 <任意路径>/plan.json（后缀固定，故非原 P0 级）。
        # 双重防护：① 拒绝一切分隔符/NUL/相对段；② resolve() 后断言仍落在 accounts 目录内
        # （resolve 会解掉符号链接，因此也挡住软链逃逸）。
        if ("/" in account_id or "\\" in account_id or "\x00" in account_id
                or account_id in (".", "..")):
            raise HTTPException(400, "account_id 非法")
        accounts_dir = (Path(__file__).parent.parent / "models" / "accounts").resolve()
        try:
            p = (accounts_dir / account_id / "plan.json").resolve()
        except Exception:
            raise HTTPException(400, "account_id 非法")
        if p.parent.parent != accounts_dir:
            raise HTTPException(400, "account_id 非法")
        if not p.exists():
            raise HTTPException(404, "该账号还没有规划，先扫描仓库")
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            raise HTTPException(500, f"计划文件损坏: {e}")




    # ═══════════════════════════════════════════
    # 企鹅物流数据采集（2026-08-13: 权威掉率数据定期入库）
    # ═══════════════════════════════════════════



    # ═══════════════════════════════════════════
    # 活动关识别（2026-08-13: 每小时自动 + 手动修正）
    # ═══════════════════════════════════════════

    @app.get("/api/activity/stages")
    def handle_activity_get():
        from services.activity_tracker import status
        return status()


    @app.post("/api/activity/stages")
    def handle_activity_save(body: dict):
        """人工修正活动关配置。Body: {material: [{stage, item}], orundum: {stage, item},
        resource: [{stage, item}], until, active} — 写 manual=true 防自动覆盖。"""
        from services.activity_tracker import CONFIG_PATH
        p = CONFIG_PATH
        cfg = {}
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        for k in ("material", "orundum", "resource", "until", "active"):
            if k in body:
                cfg[k] = body[k]
        if body.get("manual") is False:
            cfg.pop("manual", None)  # 清空人工修正，恢复自动识别
        else:
            cfg["manual"] = True
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        mw._log(f"[活动] 人工修正活动配置: {cfg.get('zone_name', '')}")
        return {"ok": True}

    @app.get("/api/account_status")
    def handle_account_status():
        """账号状态总览：体力排行 + 今日运行 + 完成情况（图表/排行榜数据源）。"""
        from models.account_state import AccountState
        lq = getattr(mw, 'launch_queue', None)
        rows = []
        for i, a in enumerate(mw.accounts):
            if a.get("_connect_only"):
                continue
            st = AccountState(a["id"]).data
            san = st.get("sanity") or {}
            # 实时快照优先（最后进关卡前体力 — 2026-08-12 用户: 以最后
            # 实时的结果为准），快照缺失回退 AccountState 死值。
            try:
                from services.sanity_tracker import get as _sn_get
                _snap = _sn_get(a["id"])
                if _snap and _snap.get("current") is not None:
                    san = _snap
            except Exception:
                pass
            cur = san.get("current")
            mx = san.get("max")
            # 体力记录的时间戳（判断"刷完"是否今天）— report_time 可能为空
            # （旧数据/写入缺时间戳）→ 退化用 captured_at / last_complete
            san_ts = san.get("report_time", "") or san.get("captured_at", "") or ""
            done_ts = san_ts or st.get("last_complete", "")
            # 最近一次清体：时间 + 清完剩余（2026-08-13 用户: 上次什么时候清、
            # 清完剩多少 — stats.json 最近一条完成 run 的 sanity_after）
            last_clear_ts = ""
            last_sanity_after = None
            try:
                from models.stats import RunStats
                for run in reversed(RunStats(a["id"]).runs):
                    if run.get("status") == "完成" and run.get("end_ts"):
                        last_clear_ts = run.get("end_ts", "")
                        _sa = run.get("sanity_after") or run.get("sanity")
                        if isinstance(_sa, dict) and _sa.get("current") is not None:
                            last_sanity_after = int(_sa["current"])
                        break
            except Exception:
                pass
            running = lq.is_running(a["id"]) if lq else False
            queued = lq.is_queued(a["id"]) if lq else False
            rows.append({
                "index": i, "id": a["id"],
                "name": a.get("name", "").replace("明日方舟-", ""),
                "sanity_current": cur, "sanity_max": mx,
                "sanity_pct": round(cur / mx * 100, 1) if (cur is not None and mx) else None,
                "sanity_time": san_ts,
                "done_ts": done_ts,
                "last_complete": st.get("last_complete", ""),
                "last_clear_ts": last_clear_ts,
                "last_sanity_after": last_sanity_after,
                "today_runs": int(st.get("today_runs", 0)),
                "last_status": st.get("last_status", ""),
                "last_login": st.get("last_login", ""),
                "running": running, "queued": queued,
            })
        # 排序: 有体力数据的在前（体力低=刷完），无数据的按今日次数
        rows.sort(key=lambda r: (r["sanity_pct"] is None, -(r["sanity_pct"] if r["sanity_pct"] is not None else 99),
                                 -r["today_runs"], r["name"]))
        # "刷完"判定必须含"今天"条件 — 体力记录是上次刷取结果，
        # 旧数据（凌晨残留）不算今天刷完（2026-08-10 用户质疑）。
        _today = datetime.now().strftime("%Y-%m-%d")
        done = sum(1 for r in rows
                   if r["sanity_pct"] is not None and r["sanity_pct"] <= 20
                   and r["done_ts"].startswith(_today))
        low = sum(1 for r in rows if r["sanity_pct"] is not None and 20 < r["sanity_pct"] <= 60)
        full = sum(1 for r in rows if r["sanity_pct"] is not None and r["sanity_pct"] > 60)
        unknown = sum(1 for r in rows if r["sanity_pct"] is None)
        return {"ok": True, "accounts": rows,
                "stats": {"total": len(rows), "done": done, "low": low, "full": full,
                          "unknown": unknown, "running": sum(1 for r in rows if r["running"])}}

    @app.get("/api/health")
    def handle_health():
        """Runtime health: zombie MAA / stale emulators / port drift / queue stuck."""
        from services.runtime_health import check_health
        return check_health(mw)


    @app.get("/api/events/list")
    def handle_events_list(year: int = 2026):
        """活动明细列表（开始/结束/时长/间隔 — 2026-08-11 用户）。"""
        from services.event_analyzer import list_events_detail
        try:
            return {"ok": True, **list_events_detail(year)}
        except Exception as e:
            return {"ok": False, "error": str(e)}








    @app.get("/api/debug/state")
    def handle_debug_state():
        """内部状态快照（排障用）— 全部运行状态一眼可见，aid 脱敏前缀。
        2026-08-11: 问题定位从 grep 日志猜状态 → 直接看状态。"""
        def _m(aid):
            return aid[:8] if isinstance(aid, str) else aid
        out = {}
        try:
            lq = _lq()
            runner = _runner()
            out["queue"] = {
                "paused": lq._paused,
                "pending": [{"aid": _m(e.account_id), "prio": e.sort_key[0], "at": str(e.not_before)[11:19]}
                            for e in list(lq._pending)[:20]],
                "pending_count": len(lq._pending),
                "active_emus": {k: _m(v) for k, v in list(lq._active_emus.items())[:20]},
                "active_emus_ts": {k: round(v, 0) for k, v in list(lq._active_emus_ts.items())[:20]},
                "system_started": {k: time.strftime("%H:%M:%S", time.localtime(v))
                                   for k, v in list(__import__("services.emu_service", fromlist=["_system_started"])._system_started.items())[:20]},
            }
        except Exception as e:
            out["queue"] = f"err {e}"
        try:
            out["runner"] = {
                "active": {_m(k): v.get("name", "?") for k, v in list(runner._active.items())[:20]},
                "task_running": {_m(k): runner.is_task_running(k) for k in list(runner._active.keys())[:20]},
                "maa_scan": runner.scan_maa_instances()[:20],
                "procs": {_m(k): (getattr(p, "pid", None) if not isinstance(p, str) else "占位")
                          for k, p in list(runner._procs.items())[:20]},
                "inst_reserved": {_m(k): v for k, v in list(runner._inst_reserved.items())[:20]},
                "done_flags": {_m(k): v for k, v in list(runner._done_flags.items())[:10]},
                "adb_fail": {_m(k): v for k, v in list(runner._adb_fail_count.items())[:10]},
                "stopping": [_m(x) for x in list(runner._stopping)[:10]],
            }
        except Exception as e:
            out["runner"] = f"err {e}"
        try:
            from services.emu_service import _recently_closed, _locks
            out["closing"] = {
                "recently_closed": {k: time.strftime("%H:%M:%S", time.localtime(v))
                                    for k, v in list(_recently_closed.items())[:20]},
                "graceful_locked": [k for k, v in list(_locks.items()) if v.locked()][:10],
            }
        except Exception as e:
            out["closing"] = f"err {e}"
        return out

    @app.get("/api/queue")
    def handle_queue_status():
        lq = _lq()
        src_map = {"manual": "手动", "schedule": "定时", "sanity": "理智"}
        # ── 预排队: 排队位置 + 预期启动时间（ETA）──
        # 槽位 = 并行上限; 每槽平均运行时长来自历史 stats（默认 25 分钟）。
        # ETA = now + 前方槽位数 × 平均时长。位置是"预排顺序"——账号按序
        # 进入空闲槽位，不会挤着同时启动（另有 20s 启动间隔兜底）。
        from models.stats import RunStats
        from datetime import timedelta
        parallel = max(1, int(mw.config.get("parallel_max", 3) or 3))
        # 平均运行时长（今天）
        avg_sec = 25 * 60
        try:
            elaps = []
            for a in mw.accounts:
                st = RunStats(a["id"])
                for r in st.runs[-10:]:
                    if r.get("elapsed"):
                        elaps.append(r["elapsed"])
            if len(elaps) >= 3:
                avg_sec = int(sum(elaps) / len(elaps))
        except Exception:
            pass
        now = datetime.now()
        entries = sorted(lq._pending, key=lambda x: x.sort_key)
        # 任务内容（调度台可见 — 2026-08-13）: dispatch 带参任务 → 「刷关×次数」
        from services.dispatch_pool import get_template_info
        def _task_desc(a, slot: str) -> str:
            if not a:
                return ""
            key = f"_dispatch_{slot}" if slot else "dispatch_id"
            did = a.get(key, "") or a.get("dispatch_id", "")
            if not did:
                return ""
            tasks, params = get_template_info(did)
            f = params.get("fight") or {}
            if f.get("stage"):
                t = f.get("times") or 0
                return f"刷 {f['stage']}" + (f" ×{t}" if t else " ×不限")
            return "、".join(tasks) or ""
        pending = []
        for pos, e in enumerate(entries, start=1):
            a = next((x for x in mw.accounts if x["id"] == e.account_id), None)
            # 前方还有多少个"满槽批次"
            ahead_slots = (pos - 1) // parallel
            eta_dt = now + timedelta(seconds=ahead_slots * avg_sec)
            pending.append({
                "account_id": e.account_id,
                "account_name": a.get("name", "") if a else "",
                "source": src_map.get(e.source, e.source),
                "priority": e.sort_key[0],
                "not_before": e.not_before.strftime("%Y-%m-%d %H:%M:%S"),
                "position": pos,
                "eta": eta_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "eta_min": ahead_slots * avg_sec // 60,
                "suspended": a.get("suspended", False) if a else False,
                "slot": e.slot or "",
                "task": _task_desc(a, e.slot or ""),
            })
        active = []
        for aid in list(lq._active_emus.values()):
            a = next((x for x in mw.accounts if x["id"] == aid), None)
            active.append({"account_id": aid,
                           "account_name": a.get("name", "") if a else "",
                           "task": _task_desc(a, a.get("_slot", "") if a else "")})
        return {"pending": pending, "active": active,
                "pending_count": len(pending), "active_count": len(active),
                "paused": lq._paused, "avg_duration_min": avg_sec // 60}

    # ── 截图视图（2026-08-15 用户: 收集每一次截图，单独视图事后查看）──
    # 来源: 完成截图 logs/shots/*.png + 收集器拷贝的 MAA 界面/掉落图
    # logs/shots/<aid>/*.png







    def _account_sanity(aid: str) -> dict:
        """账号体力快照（实时优先，回退 AccountState）。"""
        try:
            from services.sanity_tracker import get as _sg
            _snap = _sg(aid)
            if _snap and _snap.get("current") is not None:
                return {"current": int(_snap["current"]),
                        "max": int(_snap.get("max", 0) or 0),
                        "time": _snap.get("report_time", "") or _snap.get("captured_at", "")}
        except Exception:
            pass
        return {}

    def _account_last_clear(aid: str) -> dict:
        """最近一次清体: 时间 + 清完剩余（stats.json 完成 run 的 sanity_after）。

        完成 run 后面常跟 0s 完成记录（完成检测路径不记体力）→ 回退扫描到
        最近一条带 sanity 的完成 run，时间以它为准。
        """
        try:
            from models.stats import RunStats
            latest = None
            for run in reversed(RunStats(aid).runs):
                if run.get("status") != "完成" or not run.get("end_ts"):
                    continue
                if latest is None:
                    latest = run
                _sa = run.get("sanity_after") or run.get("sanity")
                if isinstance(_sa, dict) and _sa.get("current") is not None:
                    return {"ts": run.get("end_ts", ""), "sanity": int(_sa["current"])}
            if latest:
                return {"ts": latest.get("end_ts", ""), "sanity": None}
        except Exception:
            pass
        return {}

    @app.get("/api/accounts")
    def handle_accounts():
        data = []
        for a in mw.accounts:
            if a.get("_connect_only"):
                continue
            aid = a.get("id", "")
            lq = getattr(mw, 'launch_queue', None)
            running = lq.is_running(aid) if lq else False
            queued = lq.is_queued(aid) if lq else False
            data.append({
                "id": aid, "name": a.get("name", ""),
                "game_client": a.get("game_client", ""),
                "emu_instance_index": a.get("emu_instance_index", ""),
                "account_switch": a.get("account_switch", ""),
                "uid": a.get("uid", ""),
                "running": running, "queued": queued,
                "failures": a.get("consecutive_failures", 0),
                "suspended": a.get("suspended", False),
                "stages": a.get("stages", []),
                "smart_annihilation": a.get("smart_annihilation", ""),
                "fight_mode": a.get("fight_mode", "schedule"),
                "fight_default": a.get("fight_default", "1-7"),
                "schedule_weekly": a.get("schedule_weekly", {}),
                "schedule_monthly": a.get("schedule_monthly", {}),
                "fight_priority": a.get("fight_priority", {}),
                "fight_materials": a.get("fight_materials", []),
                "stage_ability": a.get("stage_ability", {}),
                "farm_tag": a.get("farm_tag", "material"),
                "mainline_chapter": a.get("mainline_chapter", 0),
                "sanity": _account_sanity(aid),
                "last_clear": _account_last_clear(aid),
                "state": _account_state(aid),
            })
        return {"ok": True, "accounts": data}







    @app.get("/api/emulators")
    def handle_emulators():
        from infrastructure.task_constants import detect_emu_instances
        return {"ok": True, "emulators": detect_emu_instances()}

    @app.get("/api/emulator/{idx}")
    def handle_emulator_status(idx: str):
        from infrastructure.task_constants import detect_emu_instances
        for e in detect_emu_instances():
            if str(e.get("index")) == idx:
                return {"ok": True, "emulator": e}
        raise HTTPException(404)



    # ═══════════════════════════════════════════
    # SSE endpoint (streaming)
    # ═══════════════════════════════════════════


    def _gather_sse_state() -> dict:
        try:
            accts = []
            for a in mw.accounts:
                aid = a.get("id", "")
                lq = getattr(mw, 'launch_queue', None)
                running = lq.is_running(aid) if lq else False
                queued = lq.is_queued(aid) if lq else False
                accts.append({
                    "id": aid, "name": a.get("name", ""),
                    "running": running, "queued": queued,
                    "emu": a.get("emu_instance_index", ""),
                    "client": a.get("game_client", ""),
                    "failures": a.get("consecutive_failures", 0),
                    "suspended": a.get("suspended", False),
                    "stages": a.get("stages", []),
                    "smart_annihilation": a.get("smart_annihilation", ""),
                })
            q = {"count": mw.launch_queue.pending_count if hasattr(mw, 'launch_queue') and mw.launch_queue else 0}
            notifs = getattr(mw, '_notifications', [])[-5:]
            insights = getattr(mw, '_ai_insights', [])[-3:]
            return {"ok": True, "accounts": accts, "queue": q, "notifications": notifs, "ai_insights": insights}
        except:
            return {"ok": False}

    # ═══════════════════════════════════════════
    # POST endpoints
    # ═══════════════════════════════════════════




    @app.post("/api/account/{idx}/launch")
    def handle_account_launch(idx: int):
        a = _account_by_idx(idx)
        runner = _runner()
        ok = runner.launch(idx)
        if ok:
            _log_op("启动", a.get("name", ""))
        return {"ok": ok}

    @app.post("/api/account/{idx}/stop")
    def handle_account_stop(idx: int):
        a = _account_by_idx(idx)
        runner = _runner()
        runner.stop(a["id"])
        _log_op("停止", a.get("name", ""))
        return {"ok": True}


    @app.post("/api/queue/enqueue")
    def handle_queue_enqueue(body: dict):
        lq = _lq()
        idx = body.get("account_index", -1)
        if isinstance(idx, str):
            try:
                idx = int(idx)
            except:
                idx = -1
        a = _account_by_idx(idx)
        source = body.get("source", "manual")
        priority = body.get("priority", 0)
        not_before_str = body.get("not_before", "")
        not_before = None
        if not_before_str:
            try:
                not_before = datetime.strptime(not_before_str, "%Y-%m-%d %H:%M:%S")
            except:
                pass
        lq.enqueue(a["id"], source, priority, not_before)
        _log_op("入队", a.get("name", ""))
        return {"ok": True, "account_id": a["id"], "pending_count": lq.pending_count}

    @app.post("/api/queue/dequeue")
    def handle_queue_dequeue(body: dict):
        lq = _lq()
        aid = body.get("account_id", "")
        if not aid:
            idx = body.get("account_index", -1)
            if isinstance(idx, str):
                try:
                    idx = int(idx)
                except:
                    idx = -1
            if 0 <= idx < len(mw.accounts):
                aid = mw.accounts[idx]["id"]
        if not aid:
            raise HTTPException(400, "invalid account")
        lq.dequeue(aid)
        ac = next((x for x in mw.accounts if x["id"] == aid), None)
        _log_op("出队", ac.get("name", "") if ac else aid)
        return {"ok": True, "account_id": aid, "pending_count": lq.pending_count}

    @app.post("/api/queue/clear")
    def handle_queue_clear():
        lq = _lq()
        lq._pending.clear()
        lq._save_queue()
        return {"ok": True}

    @app.post("/api/queue/pause")
    def handle_queue_pause():
        lq = _lq()
        lq.pause()
        _log_op("队列暂停", "")
        mw._log("⏸ 队列已暂停")
        return {"ok": True, "paused": True}

    @app.post("/api/queue/resume")
    def handle_queue_resume():
        lq = _lq()
        lq.resume()
        _log_op("队列恢复", "")
        mw._log("▶️ 队列已恢复")
        return {"ok": True, "paused": False}













    @app.post("/api/action/stop_all")
    def handle_stop_all():
        lq = _lq()
        count = lq.stop_all()
        return {"ok": True, "stopped": count}

    # ── 守护审计（Kill Audit）— 2026-08-14 用户: 杀/关动作先确认再执行，
    # 全程留痕，页面展示原因与解决方式。判据以实际状态为准（铁律）。──
    @app.get("/api/kill_audit")
    def handle_kill_audit(limit: int = 100):
        try:
            from models.kill_audit import history, summary
            return {"ok": True, "summary": summary(), "entries": history(limit)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.get("/api/kill_audit/pending")
    def handle_kill_audit_pending():
        try:
            from models.kill_audit import expire_all, pending
            expire_all()  # 先清算超时项
            return {"ok": True, "pending": pending()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/api/kill_audit/decide")
    def handle_kill_audit_decide(body: dict = {}):
        # body: {id, approve: bool, by?, note?, fix?}
        try:
            from models.kill_audit import decide
            eid = (body or {}).get("id", "")
            approve = bool((body or {}).get("approve", False))
            if not eid:
                return {"ok": False, "error": "missing id"}
            entry = decide(eid, approve,
                           by=(body or {}).get("by", "manual"),
                           note=(body or {}).get("note", ""),
                           fix=(body or {}).get("fix", ""))
            if not entry:
                return {"ok": False, "error": "entry not found"}
            return {"ok": True, "entry": entry}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── MAA 远程控制（6.16 — MAA 每秒轮询 GetTask = 心跳；ReportStatus =
    # 任务结果上报。检测 MAA 是否在运行的权威信号 — 2026-08-12 用户）──
    @app.post("/api/maa_remote/task")
    def handle_maa_remote_task(body: dict = {}):
        # MAA 每秒轮询: 心跳记录（user=账号 id — MAA 活着就每秒报到）。
        # 必须返回含 tasks 字段（空数组）— 否则 MAA 判连接无效。
        try:
            _user = body.get("user", "") if isinstance(body, dict) else ""
            if _user:
                _hb = getattr(mw, "_maa_heartbeat", None)
                if _hb is None:
                    _hb = mw._maa_heartbeat = {}
                _hb[_user] = time.time()
        except Exception:
            pass
        return {"tasks": []}

    @app.get("/api/maa_remote")
    def handle_maa_remote_view():
        """心跳快照（浏览器可看 — 2026-08-12 用户: 你给的本地我看不见 —
        给 MAA 的端点是 POST，浏览器 GET 看不到 → 加 GET 视图）。"""
        try:
            _hb = getattr(mw, "_maa_heartbeat", {}) or {}
            now = time.time()
            out = {}
            for _aid, _ts in _hb.items():
                _a = next((a for a in (mw.accounts or []) if a.get("id") == _aid), None)
                _nm = _a.get("name", "") if _a else str(_aid)[:8]
                # 昵称（note 首位 — 真实名字，2026-08-12 用户填入）显示
                _nick = ""
                if _a and _a.get("note"):
                    _nick = str(_a["note"]).split()[0]
                _disp = f"{_nm}({_nick})" if _nick else _nm
                out[_disp] = {"last_seen": time.strftime("%H:%M:%S", time.localtime(_ts)),
                              "age_s": int(now - _ts),
                              "alive": (now - _ts) < 10}
            return {"ok": True, "heartbeats": out,
                    "hint": "MAA 每秒 POST /api/maa_remote/task 报到；alive=10s 内有报到"}
        except Exception:
            return {"ok": False}

    @app.post("/api/maa_remote/status")
    def handle_maa_remote_status(body: dict = {}):
        # MAA 任务结果上报（SUCCESS/FAILED — MAA 自己报错）。必须 200 —
        # 否则 MAA 弹"上传失败"通知。
        try:
            _st = body.get("status", "") if isinstance(body, dict) else ""
            _task = body.get("task", "") if isinstance(body, dict) else ""
            _user = body.get("user", "") if isinstance(body, dict) else ""
            if _st == "FAILED":
                mw._log(f"[MAA远程] {str(_user)[:8]} 任务失败: {str(_task)[:60]}")
        except Exception:
            pass
        return {"ok": True}

    def _stop_maa_for_emu(emu_idx) -> None:
        """Stop any MAA connected to this emulator before shutting it down.
        Otherwise MAA's ADB keepalive (RetryOnDisconnected) keeps reconnecting
        during shutdown, corrupting the emulator's state."""
        runner = _runner()
        if not runner:
            return
        for aid, ac in list(runner._active.items()):
            if str(ac.get("emu_instance_index", "")) == str(emu_idx):
                runner.stop(aid)

    def _emu_adb(emu_idx) -> tuple:
        """该模拟器的 (adb_path, adb_address) — 优先正式账号配置，其次连接模式临时账号。"""
        from infrastructure.task_constants import find_adb
        for ac in (getattr(mw, "accounts", []) or []) + (getattr(mw, "connect_accounts", []) or []):
            if str(ac.get("emu_instance_index", "")) == str(emu_idx):
                if ac.get("adb_path") and ac.get("adb_address"):
                    return ac["adb_path"], ac["adb_address"]
        adb = ""
        try:
            adb = find_adb()
        except Exception:
            pass
        return adb, ""

    def _save_manual_started() -> None:
        """手动启动保护标记落盘（重启不丢 — 否则重启后手动开的模拟器被回收）。"""
        try:
            import json as _j2
            _p = Path(__file__).parent.parent / "models" / "manual_emu_started.json"
            _p.parent.mkdir(parents=True, exist_ok=True)
            _p.write_text(_j2.dumps(mw.manual_emu_started), encoding="utf-8")
        except Exception:
            pass

    def _wait_emu_stopped(cli: str, idx: str, timeout: int = 30) -> bool:
        """Poll MuMuManager info until the emulator fully exits (both process and android off)."""
        from infrastructure.task_constants import cli_flag
        for _ in range(timeout):
            try:
                r = subprocess.run([cli, "info", cli_flag(cli), str(idx)],
                                   capture_output=True, text=True, timeout=5,
                                   creationflags=_CF, encoding="utf-8", errors="replace")
                if r.returncode == 0:
                    d = json.loads(r.stdout.lstrip("\ufeff").strip())
                    if not d.get("is_process_started") and not d.get("is_android_started"):
                        return True
            except Exception:
                pass
            time.sleep(1)
        return False

    def _wait_emu_started(cli: str, idx: str, timeout: int = 40) -> bool:
        """轮询 MuMuManager 等模拟器 Android 就绪 — 启动结果留痕（起不来要有日志）。"""
        from infrastructure.task_constants import cli_flag
        dl = time.time() + timeout
        while time.time() < dl:
            try:
                r = subprocess.run([cli, "info", cli_flag(cli), str(idx)],
                                   capture_output=True, text=True, timeout=5,
                                   creationflags=_CF, encoding="utf-8", errors="replace")
                if r.returncode == 0:
                    d = json.loads(r.stdout.lstrip("\ufeff").strip())
                    if d.get("is_android_started") and d.get("is_process_started"):
                        return True
            except Exception:
                pass
            time.sleep(5)
        return False

    @app.post("/api/emulator/batch/{action}")
    def handle_emulator_batch(action: str, body: dict = {}):
        """Batch emulator control. body: {indexes: [..]} — default:
        start → all not-running, stop/restart → all running.
        Serial with small gaps to avoid concurrent mumu-cli conflicts.
        NOTE: registered BEFORE /api/emulator/{idx}/{action} — otherwise
        'batch' would match as an emulator index."""
        from infrastructure.task_constants import detect_emu_instances, find_mumu_cli, cli_flag
        if action not in ("start", "stop", "restart"):
            raise HTTPException(400, "bad action")
        cli = find_mumu_cli()
        if not cli:
            return {"ok": False, "error": "mumu-cli not found"}
        idxs = [str(x) for x in (body.get("indexes") or [])]
        try:
            emus = detect_emu_instances()
        except Exception:
            emus = []
        targets = []
        for e in emus:
            idx = str(e.get("index", ""))
            if idxs and idx not in idxs:
                continue
            running = bool(e.get("running"))
            if action == "start" and running:
                continue
            if action in ("stop", "restart") and not running:
                continue
            targets.append(idx)
        done = 0
        for idx in targets:
            try:
                if action == "start":
                    mw._log(f"⏯ 批量启动模拟器 #{idx}")
                    subprocess.run([cli, "control", cli_flag(cli), idx, "launch"], timeout=30, creationflags=_CF)
                    if not _wait_emu_started(cli, idx, 20):
                        mw._log(f"⚠️ 模拟器 #{idx} 批量启动 20s 未就绪")
                    try:
                        mw.manual_emu_started[idx] = time.time()
                        _save_manual_started()
                    except Exception:
                        pass
                else:
                    mw._log(f"⏹ 批量{action}模拟器 #{idx}")
                    _stop_maa_for_emu(idx)
                    time.sleep(2)
                    # 统一优雅关闭（adb reboot -p → 等退出 → 兜底）— 直接
                    # shutdown 留 VMM 残留 + 弹"运行异常"（2026-08-10）
                    from services.launch_queue import graceful_emu_shutdown
                    graceful_emu_shutdown(cli, idx, *_emu_adb(idx), wait=60)
                    if action == "restart":
                        time.sleep(2)
                        subprocess.run([cli, "control", cli_flag(cli), idx, "launch"], timeout=30, creationflags=_CF)
                        if not _wait_emu_started(cli, idx, 20):
                            mw._log(f"⚠️ 模拟器 #{idx} 批量重启 20s 未就绪")
                done += 1
            except Exception as ex:
                mw._log(f"[批量{action}] #{idx} 失败: {ex}")
            time.sleep(1.5)
        _log_op(f"批量{action}模拟器", f"{done} 个")
        return {"ok": True, action: done, "total": len(targets)}

    @app.post("/api/emulator/{idx}/{action}")
    def handle_emulator_control(idx: str, action: str):
        from infrastructure.task_constants import find_mumu_cli, cli_flag
        cli = find_mumu_cli()
        if not cli:
            raise HTTPException(500, "mumu-cli not found")
        try:
            if action == "start":
                mw._log(f"⏯ 启动模拟器 #{idx}")
                subprocess.run([cli, "control", cli_flag(cli), idx, "launch"], timeout=30, creationflags=_CF)
                # 启动结果留痕（起不来要有日志可查）
                if _wait_emu_started(cli, idx, 40):
                    mw._log(f"✅ 模拟器 #{idx} Android 就绪")
                else:
                    mw._log(f"⚠️ 模拟器 #{idx} 启动超时（40s 未就绪）")
                # 手动启动保护期 — 回收不立刻关（用户手动操作中）
                try:
                    mw.manual_emu_started[idx] = time.time()
                    _save_manual_started()
                except Exception:
                    pass
                _log_op("启动模拟器", f"#{idx}")
                return {"ok": True, "action": "started"}
            elif action == "stop":
                mw._log(f"⏹ 关闭模拟器 #{idx}")
                _stop_maa_for_emu(idx)
                time.sleep(2)  # let MAA exit and release ADB before shutting down
                # 统一优雅关闭（adb reboot -p → 等退出 → shutdown 兜底）—
                # 直接 shutdown 留 VMM 残留 + 弹"运行异常"（2026-08-10）
                from services.launch_queue import graceful_emu_shutdown
                graceful_emu_shutdown(cli, idx, *_emu_adb(idx))
                _log_op("关闭模拟器", f"#{idx}")
                return {"ok": True, "action": "stopped"}
            elif action == "restart":
                mw._log(f"🔄 重启模拟器 #{idx}")
                _stop_maa_for_emu(idx)
                time.sleep(2)
                from services.launch_queue import graceful_emu_shutdown
                graceful_emu_shutdown(cli, idx, *_emu_adb(idx))  # 内置等待完全退出
                time.sleep(2)
                subprocess.run([cli, "control", cli_flag(cli), idx, "launch"], timeout=30, creationflags=_CF)
                if _wait_emu_started(cli, idx, 40):
                    mw._log(f"✅ 模拟器 #{idx} 重启后 Android 就绪")
                else:
                    mw._log(f"⚠️ 模拟器 #{idx} 重启后 40s 未就绪")
                _log_op("重启模拟器", f"#{idx}")
                return {"ok": True, "action": "restarted"}
        except Exception as ex:
            mw._log(f"[模拟器{action}] #{idx} 失败: {ex}")
            _log_op(f"模拟器{action}失败", f"#{idx}: {ex}")
            raise HTTPException(500, str(ex))
        raise HTTPException(400, "bad action")





    def _dispatch_slot(ac, tasks, slot: str, params: dict | None = None) -> None:
        """Create a dispatch for the given slot and store on the account."""
        from services.dispatch_pool import create_dispatch
        key = f"_dispatch_{slot}" if slot else "dispatch_id"
        ac[key] = create_dispatch(tasks, params)

    def _fight_task_pack(ac) -> tuple[list, dict]:
        """Fight 槽位任务打包（2026-08-13 任务参数化）: 标签分派 → 带参任务。
        分派无结果 → 纯任务名（注入时现算，存量行为）。"""
        try:
            from services.farm_dispatch import build_farm_task
            t = build_farm_task(ac)
            if t and t.get("fight", {}).get("stage"):
                return t["tasks"], {"fight": t["fight"]}
        except Exception:
            pass
        return ["StartUp", "Fight"], {}

    _MATERIAL_STAGES = {
        "固源岩": "1-7", "装置": "S3-4", "聚酸酯": "S3-3", "酯": "S3-1",
        "异铁": "S3-2", "酮凝集": "S3-5", "凝胶": "S3-5", "龙门币": "CE-6",
        "作战记录": "LS-6",
    }

    def _is_stage_usable(stage_id: str) -> bool:
        """Check if a stage is within its time window (available_from/available_until)."""
        lib = mw.config.get("stage_library", [])
        entry = next((s for s in lib if s.get("id") == stage_id), None)
        if not entry:
            return True
        now = datetime.now()
        af = entry.get("available_from", "")
        au = entry.get("available_until", "")
        if af:
            try:
                h, m = map(int, af.split(":"))
                if now.hour < h or (now.hour == h and now.minute < m):
                    return False
            except:
                pass
        if au:
            try:
                h, m = map(int, au.split(":"))
                if now.hour > h or (now.hour == h and now.minute > m):
                    return False
            except:
                pass
        return True

    def _pick_fight_stage(a) -> str:
        """Pick a stage based on the account's fight strategy."""
        mode = a.get("fight_mode", "schedule")
        default = a.get("fight_default", "1-7")
        stages = a.get("stages", []) or []

        if mode == "schedule":
            # Check weekly schedule
            weekdays = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
            wd = weekdays[datetime.now().weekday()]
            weekly = a.get("schedule_weekly", {}) or {}
            if wd in weekly and weekly[wd]:
                stage = weekly[wd]
                if stage in stages or stage == "Annihilation":
                    if _is_stage_usable(stage):
                        return stage
            # Check monthly schedule
            day = str(datetime.now().day)
            monthly = a.get("schedule_monthly", {}) or {}
            if day in monthly and monthly[day]:
                stage = monthly[day]
                if stage in stages or stage == "Annihilation":
                    if _is_stage_usable(stage):
                        return stage
            return default

        elif mode == "priority":
            priority = a.get("fight_priority", {}) or {}
            if stages:
                sorted_stages = sorted(
                    [s for s in stages if s in priority and _is_stage_usable(s)],
                    key=lambda s: priority.get(s, 0), reverse=True
                )
                if sorted_stages:
                    return sorted_stages[0]
            return default

        elif mode == "material":
            materials = a.get("fight_materials", []) or []
            unfinished = [m for m in materials if m.get("achieved", 0) < m.get("target", 0)]
            if unfinished:
                for m in unfinished:
                    item = m.get("item", "")
                    if item in _MATERIAL_STAGES:
                        stage = _MATERIAL_STAGES[item]
                        if _is_stage_usable(stage):
                            return stage
            return default

        return default

    def _account_usable(a, lq) -> bool:
        """Check if an account can be dispatched (has ADB/emu, not running)."""
        if a.get("_connect_only"):
            return False
        if not a.get("adb_address", "").strip() and not a.get("emu_instance_index", ""):
            return False
        if a.get("suspended", False):
            return False
        # 手动专用账号（2026-08-15 用户）: skip_daily 的模拟器归用户手动
        # 用（手动开模拟器自己玩），不参与自动调度。手动启动不受影响
        # （连接页/账号详情页的启动走的是独立路径）。
        if a.get("skip_daily", False):
            return False
        if lq.is_running(a.get("id", "")):
            return False
        return True





    def _connect_account(conn: list, idx, port: str, mode: str = "connect",
                         game_client: str = "") -> dict:
        """Create (or reuse) a connect-only temp account and dispatch tasks for it."""
        aid = f"emu{idx}"
        ac = next((x for x in conn if x.get("id") == aid), None)
        if ac is None:
            ac = {
                "id": aid,
                "name": f"模拟器#{idx}",
                "game_client": game_client,
                "emu_instance_index": idx,
                "adb_address": f"127.0.0.1:{port}" if port else "",
                "adb_path": "",
                "account_switch": "", "uid": "", "note": "仅连接(手动操作)",
                "consecutive_failures": 0,
                "_connect_only": True,
            }
            conn.append(ac)
        elif game_client:
            ac["game_client"] = game_client
        if mode == "daily":
            tasks = ["StartUp", "Infrast", "Recruit", "Mall", "Award"]
            ac["note"] = "通用日常"
        else:
            tasks = []
            ac["note"] = "仅连接(手动操作)"
        _dispatch_slot(ac, tasks, "connect")
        return ac

    @app.get("/api/connect/status")
    def handle_connect_status():
        from infrastructure.task_constants import detect_emu_instances
        lq = _lq()
        conn = getattr(mw, 'connect_accounts', None) or []
        emus = [e for e in detect_emu_instances() if e.get("running")]
        data = []
        for e in emus:
            idx = e.get("index", "")
            aid = f"emu{idx}"
            ac = next((x for x in conn if x.get("id") == aid), None)
            runner = _runner()
            real = runner._has_real_process(aid) if runner else False
            queued = lq.is_queued(aid) if lq else False
            data.append({
                "index": idx,
                "name": e.get("name", ""),
                "adb_port": e.get("adb_port", ""),
                "running": True,
                "maa_running": real or queued,
                "maa_queued": queued,
                "mode": (ac or {}).get("note", ""),
            })
        return {"ok": True, "emulators": data}


    @app.post("/api/connect/{aid}/stop")
    def handle_connect_stop(aid: str):
        runner = _runner()
        runner.stop(aid)
        return {"ok": True}

    @app.post("/api/connect/{aid}/restart")
    def handle_connect_restart(aid: str, body: dict = {}):
        runner = _runner()
        runner.stop(aid)
        idx = aid.replace("emu", "", 1)
        mode = body.get("mode", "connect")
        from infrastructure.task_constants import detect_emu_instances
        lq = _lq()
        emu = next((e for e in detect_emu_instances()
                    if e.get("index") == idx and e.get("running")), None)
        if not emu:
            return {"ok": False, "error": f"模拟器 #{idx} 未运行"}
        conn = getattr(mw, 'connect_accounts', None)
        if conn is None:
            mw.connect_accounts = []
            conn = mw.connect_accounts
        ac = _connect_account(conn, idx, emu.get("adb_port", ""), mode=mode,
                              game_client=str(body.get("game_client", "") or ""))
        lq.enqueue(aid, "force", priority=0, slot="connect")
        lq.tick()
        return {"ok": True}







    @app.post("/api/action/smart_selected")
    def handle_smart_selected(body: dict):
        from services.dispatch_pool import create_dispatch
        ids = body.get("account_ids", [])
        include_anni = body.get("include_anni", True)
        only_anni = body.get("only_anni", False)
        probe_mode = body.get("mode") == "probe"  # 仅检测（关卡检测调度）
        probe_stages = body.get("probe_stages") or []  # 指定检测关卡（2026-08-14
        # 活动关预检走队列：队列启动模拟器有回收保护，手动拉起会被回收）
        lq = _lq()
        if lq._paused:
            lq.resume()
        smart = mw.config.get("smart_global", {}).get("enabled", False)
        if probe_mode:
            base_tasks = ["StartUp", "Probe"]
        elif only_anni:
            # 2026-08-16 修复: only_anni 之前仅在 smart_global.enabled=True 时
            # 生效（_get_web_schedule_tasks 内部），生产未开智能调度时被静默
            # 忽略、注入成日常全套（StartUp/Fight/Infrast/Recruit/Mall/Award）。
            # 用户要求「只刷剿灭」→ only_anni 无条件优先，其余一律不注入。
            base_tasks = ["StartUp", "Annihilation"]
        else:
            base_tasks = _get_web_schedule_tasks(mw, include_anni, only_anni) if smart else [
                "StartUp", "Fight", "Infrast", "Recruit", "Mall", "Award"]
        count = 0
        for a in mw.accounts:
            aid = a.get("id", "")
            if aid not in ids:
                continue
            if not a.get("adb_address", "").strip() and not a.get("emu_instance_index", ""):
                continue
            if a.get("suspended", False):
                continue
            if lq.is_queued(aid) or lq.is_running(aid):
                continue
            # Per-account annihilation
            tasks = list(base_tasks)
            if not probe_mode and not only_anni:
                # only_anni 时任务已固定为 StartUp+Annihilation，不做 per-account
                # 移除（2026-08-16: 账号 smart_annihilation 为空时曾被移除，
                # 导致"只刷剿灭"注入失败）
                anni = a.get("smart_annihilation", "")
                has_anni = "Annihilation" in tasks
                if has_anni and not anni:
                    tasks.remove("Annihilation")
                elif not has_anni and anni:
                    tasks.append("Annihilation")
            # 账号级肉鸽（l-4/l-6 业务机）— 只跑肉鸽不清日常
            if a.get("roguelike_only"):
                tasks = ["StartUp", "Roguelike"]
            if probe_mode and probe_stages:
                a["probe_stages"] = [str(s) for s in probe_stages]  # 瞬态不落盘
            # dispatch 键按 slot 化（2026-08-14 凌晨修：probe 分支曾写旧键
            # dispatch_id → runner 按 slot 找 _dispatch_probe 找不回 → 回退
            # 默认清日常任务，检测变清日常被用户画面抓到）
            _slot = ""
            if "Roguelike" in tasks or a.get("roguelike_only"):
                _slot = "roguelike"
            elif probe_mode:
                _slot = "probe"
            _dispatch_slot(a, tasks, _slot)
            lq.enqueue(aid, "force", priority=0, slot=_slot)
            count += 1
        if count:
            mw._log(f"▶ 调度选中: {count} 个账号已入队" + ("（仅检测）" if probe_mode else ""))
            _log_op("调度选中", f"{count} 个账号" + ("（仅检测）" if probe_mode else ""))
            lq.tick()
        return {"ok": True, "count": count}





    @app.get("/api/maa/status")
    def handle_maa_status():
        from services.maa_download import get_download_status, _is_source_ready, _detect_version
        source_dir = Path(__file__).parent.parent / "services" / "maa" / "source"
        ready = _is_source_ready(source_dir)
        version = mw.config.get("maa_version", "")
        source_exe = source_dir / "MAA.exe"
        source_running = False
        if source_exe.exists():
            r = subprocess.run(["tasklist", "/NH", "/FI", "IMAGENAME eq MAA.exe"],
                               capture_output=True, text=True, timeout=3, creationflags=_CF)
            source_running = "MAA.exe" in r.stdout
        pool = Path(__file__).parent.parent / "services" / "maa" / "instances"
        total = 0
        running = 0
        if pool.exists():
            for d in sorted(pool.glob("*"), key=lambda x: x.name):
                if not d.is_dir() or not d.name.isdigit():
                    continue
                total += 1
                pid_file = d / ".pid"
                if pid_file.exists():
                    try:
                        pid = int(pid_file.read_text().strip())
                        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                           capture_output=True, text=True, timeout=2,
                                           creationflags=_CF)
                        if str(pid) in r.stdout:
                            running += 1
                    except Exception:
                        pass
        dl = get_download_status()
        return {"ok": True, "ready": ready, "version": version,
                "source_ready": ready, "source_version": _detect_version(source_dir),
                "source_running": source_running,
                "instances": total, "instances_running": running,
                "download": dl}

    @app.post("/api/maa/launch_source")
    def handle_maa_launch_source():
        """Launch the source MAA.exe in a normal window so the user can complete
        first-run initialization (protocol/hot-update) manually, then close it."""
        source_dir = Path(__file__).parent.parent / "services" / "maa" / "source"
        exe = source_dir / "MAA.exe"
        if not exe.exists():
            return {"ok": False, "error": "源 MAA 未安装，请先下载"}
        r = subprocess.run(["tasklist", "/NH", "/FI", "IMAGENAME eq MAA.exe"],
                           capture_output=True, text=True, timeout=3, creationflags=_CF)
        if "MAA.exe" in r.stdout:
            return {"ok": False, "error": "MAA 已在运行"}
        try:
            subprocess.Popen([str(exe)])  # normal window — user needs to see/operate it
            mw._log("🚀 启动源 MAA（请完成初始化后关闭窗口）")
            return {"ok": True, "message": "源 MAA 已启动"}
        except Exception as e:
            return {"ok": False, "error": f"启动失败: {e}"}


    @app.get("/api/maa/instances")
    def handle_maa_instances_list():
        pool = Path(__file__).parent.parent / "services" / "maa" / "instances"
        items = []
        if pool.exists():
            for d in sorted(pool.glob("*"), key=lambda x: x.name):
                if not d.is_dir() or not d.name.isdigit():
                    continue
                exe = d / "MAA.exe"
                pid_file = d / ".pid"
                meta_file = d / ".meta"
                pid = ""
                running = False
                meta = ""
                if pid_file.exists():
                    try:
                        pid = pid_file.read_text().strip()
                        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                           capture_output=True, text=True, timeout=2,
                                           creationflags=_CF)
                        running = str(pid) in r.stdout
                    except Exception:
                        pass
                if meta_file.exists():
                    try:
                        meta = meta_file.read_text(encoding="utf-8", errors="replace").strip()
                    except Exception:
                        pass
                items.append({"index": d.name, "exe": exe.exists(), "pid": pid,
                              "running": running, "meta": meta})
        return {"ok": True, "instances": items}



    def _orch_update_zip(root: Path, result_file: Path, mw_ref) -> None:
        """Zip-deployment update: download main.zip, extract to _update/, then
        replace via replace.bat (taskkill → xcopy → restart). Runs in background."""
        import shutil as _sh
        from infrastructure.utils import is_safe_zip_path
        _log = mw_ref._log
        _log("[更新] git 不可用，使用 zip 模式更新（main 分支最新）")
        zip_url = "https://github.com/xiachk083-hub/MAAOrch/archive/refs/heads/main.zip"
        try:
            # Graceful shutdown: stop running MAA first
            runner = getattr(mw_ref, 'runner', None)
            if runner:
                for aid in list(runner._active.keys()):
                    try:
                        runner.stop(aid)
                    except Exception:
                        pass
            time.sleep(2)
            # Download main.zip (small: gitignored files excluded)
            tmp_dir = Path(tempfile.mkdtemp(prefix="maorch_upd_"))
            tmp_zip = tmp_dir / "main.zip"
            _log(f"[更新] 下载 {zip_url.split('/')[-1]}")
            req = urllib.request.Request(validate_http_url(zip_url), headers={"User-Agent": "MAAOrch-Updater"})
            with safe_urlopen(req, timeout=120) as resp:
                with open(tmp_zip, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
            _log(f"[更新] 下载完成 {tmp_zip.stat().st_size // 1024}KB")
            # Extract to _update/ (strip top-level folder, zip-slip safe)
            update_dir = root / "_update"
            if update_dir.exists():
                _sh.rmtree(str(update_dir), ignore_errors=True)
            update_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(str(tmp_zip)) as zf:
                for member in zf.namelist():
                    if not is_safe_zip_path(member, update_dir.parent):
                        raise ValueError(f"zip slip: {member}")
                    parts = member.split("/", 1)
                    if len(parts) < 2 or not parts[1]:
                        continue  # top-level folder itself
                    target = update_dir / parts[1]
                    if member.endswith("/"):
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(str(target), "wb") as dst:
                            dst.write(src.read())
            _sh.rmtree(str(tmp_dir), ignore_errors=True)
            _log(f"[更新] 解压完成 {update_dir}")
            # Generate replace.bat
            bat = root / "replace.bat"
            bat.write_text(
                '@echo off\r\n'
                'chcp 65001 >nul\r\n'
                f'taskkill /f /fi "WINDOWTITLE eq MAAOrch" 2>nul\r\n'
                'timeout /t 3 /nobreak >nul\r\n'
                f'cd /d "{root}"\r\n'
                f'xcopy /E /Y "{root}\\_update\\*" "{root}" >"{result_file}" 2>&1\r\n'
                'if errorlevel 1 (\r\n'
                f'  echo [RESULT] FAILED >>"{result_file}"\r\n'
                ') else (\r\n'
                f'  echo [RESULT] OK >>"{result_file}"\r\n'
                ')\r\n'
                f'rmdir /S /Q "{root}\\_update"\r\n'
                f'start /min "" "{sys.executable}" "{root}\\main_web.pyw"\r\n'
                'del "%~f0"\r\n', encoding="utf-8")
            subprocess.Popen(["cmd", "/c", str(bat)], creationflags=subprocess.CREATE_NO_WINDOW)
            time.sleep(1)
            os._exit(0)
        except Exception as e:
            try:
                result_file.write_text(f"[RESULT] FAILED\n{e}", encoding="utf-8")
            except Exception:
                pass
            _log(f"[更新] zip 更新失败: {e}")
            os._exit(0)







    # ── 勘察端点（2026-08-17：MCP 勘察工具后端 — 现场对应/连接关系）──
    # 项目在目标机运行，可直接 subprocess 调 MuMuManager / 读实例文件，
    # MCP 工具薄封装调这些端点（见 network/mcp_server.py 的 inspect_*）。

    @app.get("/api/inspect/link_map")
    def inspect_link_map():
        """模拟器 ↔ MAA ↔ ADB 端口对应关系 + 一致性 + 任务链。

        返回 {emulators: {name: {idx, on, adb_real}}, maas: {name: {inst, addr_cfg, alive, tasks}},
              accounts: [{name, emu, real_port, cfg_port, match, queue, tasks}]}"""
        import re as _re
        from infrastructure.task_constants import find_mumu_cli, cli_flag
        cli = find_mumu_cli()

        # 模拟器真实端口
        emus: dict = {}
        if cli:
            for i in range(0, 61):
                try:
                    r = subprocess.run([cli, "info", cli_flag(cli), str(i)],
                                       capture_output=True, text=True, timeout=6,
                                       encoding="utf-8", errors="replace")
                    d = json.loads(r.stdout.lstrip("\ufeff").strip())
                    nm = d.get("name", "")
                    if nm:
                        emus[nm.replace("明日方舟-", "")] = {
                            "idx": i, "on": bool(d.get("is_process_started")),
                            "adb_real": d.get("adb_port")}
                except Exception:
                    pass

        # MAA 实例：配置端口 + 归属 + 活否 + 任务
        inst_dir = pathlib.Path(__file__).parent.parent / "services" / "maa" / "instances"
        maas: dict = {}
        if inst_dir.exists():
            for inst in inst_dir.iterdir():
                if not inst.name.isdigit():
                    continue
                mp = inst / ".meta"
                if not mp.exists():
                    continue
                try:
                    nm = mp.read_text(encoding="utf-8", errors="replace").split("|")[-1].strip()
                except Exception:
                    continue
                alive = False
                pidp = inst / ".pid"
                if pidp.exists():
                    try:
                        pid = pidp.read_text().strip()
                        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                                           capture_output=True, text=True, timeout=15, errors="replace")
                        alive = "MAA.exe" in r.stdout
                    except Exception:
                        pass
                addr_cfg, tasks = "", []
                gnew = inst / "config" / "gui.new.json"
                if gnew.exists():
                    try:
                        d = json.loads(gnew.read_text(encoding="utf-8"))
                        cfg = d.get("Configurations", {}).get("Default", {})
                        addr_cfg = cfg.get("Connect.Address") or cfg.get("Connect", {}).get("Address", "")
                        tq = cfg.get("TaskQueue")
                        if isinstance(tq, str):
                            tq = json.loads(tq)
                        for it in (tq or []):
                            if isinstance(it, str):
                                try:
                                    it = json.loads(it)
                                except Exception:
                                    continue
                            if it.get("IsEnable"):
                                tasks.append(it.get("TaskType", "?"))
                    except Exception:
                        pass
                maas[nm] = {"inst": inst.name, "addr_cfg": addr_cfg,
                            "alive": alive, "tasks": tasks}

        # 账号对照
        qd = _lq()
        active_ids = set(qd._active_emus.values())
        pending_ids = {e.account_id for e in qd._pending}
        accounts = []
        for ac in mw.accounts:
            if not ac.get("name", "").startswith("官-"):
                continue
            nm = ac["name"].replace("明日方舟-", "")
            aid = ac.get("id", "")
            e = emus.get(nm, {})
            m = maas.get(nm, {})
            real = e.get("adb_real")
            cfg = m.get("addr_cfg", "")
            cfg_num = ""
            if cfg:
                mm = _re.search(r":(\d+)", str(cfg))
                cfg_num = mm.group(1) if mm else str(cfg)
            match = bool(real and cfg_num and str(real) == str(cfg_num))
            accounts.append({
                "name": nm, "emu": ac.get("emu_instance_index", ""),
                "real_port": real, "cfg_port": cfg_num, "match": match,
                "emu_on": e.get("on", None), "maa_alive": m.get("alive", None),
                "queue": "active" if aid in active_ids else ("pending" if aid in pending_ids else "-"),
                "tasks": m.get("tasks", [])})
        mism = [a["name"] for a in accounts if a["real_port"] and a["cfg_port"] and not a["match"]]
        return {"ok": True, "emulator_count": len(emus), "maa_count": len(maas),
                "accounts": accounts, "mismatches": mism}

    @app.get("/api/inspect/account/{name}")
    def inspect_account(name: str):
        """单个账号深挖：配置 + 模拟器 + MAA 实例 + 当前任务 + asst.log 最近错误。"""
        import re as _re
        try:
            return _inspect_account_impl(mw, name)
        except Exception as _e:
            import traceback as _tb
            _tb.print_exc()
            try:
                mw._log(f"[inspect] {name} 异常: {_e}")
            except Exception:
                pass
            raise HTTPException(500, f"inspect error: {_e}")

    def _inspect_account_impl(mw: Any, name: str) -> dict:
        ac = next((x for x in mw.accounts
                   if x.get("name", "").replace("明日方舟-", "") == name), None)
        if not ac:
            raise HTTPException(404, f"account {name} not found")
        emu_idx = ac.get("emu_instance_index", "")
        out = {"name": name,
               "config": {k: ac.get(k) for k in ("emu_instance_index", "game_client",
                          "stages", "fight_default", "farm_tag", "suspended", "smart_annihilation")},
               "queue": "-"}
        qd = _lq()
        _aid = ac.get("id", "")
        if _aid in set(qd._active_emus.values()):
            out["queue"] = "active"
        elif _aid in {e.account_id for e in qd._pending}:
            out["queue"] = "pending"

        # 模拟器
        from infrastructure.task_constants import find_mumu_cli, cli_flag
        cli = find_mumu_cli()
        out["emu"] = {}
        if cli:
            try:
                r = subprocess.run([cli, "info", cli_flag(cli), str(emu_idx)],
                                   capture_output=True, text=True, timeout=8,
                                   encoding="utf-8", errors="replace")
                d = json.loads(r.stdout.lstrip("\ufeff").strip())
                out["emu"] = {"idx": d.get("index"), "on": bool(d.get("is_process_started")),
                              "adb": d.get("adb_port"), "state": d.get("player_state")}
            except Exception:
                pass

        # MAA 实例（.meta 匹配）
        inst_dir = pathlib.Path(__file__).parent.parent / "services" / "maa" / "instances"
        out["maa"] = {}
        if inst_dir.exists():
            for inst in inst_dir.iterdir():
                if not inst.name.isdigit():
                    continue
                mp = inst / ".meta"
                if not mp.exists():
                    continue
                try:
                    nm = mp.read_text(encoding="utf-8", errors="replace").split("|")[-1].strip()
                except Exception:
                    continue
                if nm != name:
                    continue
                alive = False
                pidp = inst / ".pid"
                if pidp.exists():
                    try:
                        pid = pidp.read_text().strip()
                        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                                           capture_output=True, text=True, timeout=15, errors="replace")
                        alive = "MAA.exe" in r.stdout
                    except Exception:
                        pass
                al = inst / "debug" / "asst.log"
                cur, recent, age = "", [], -1
                if al.exists():
                    import time as _t
                    age = _t.time() - al.stat().st_mtime
                    lines = al.read_text(encoding="utf-8", errors="replace").strip().splitlines()
                    for l in reversed(lines):
                        m = _re.search(r'"cur_task":"([^"]*)"', l)
                        if m and not cur:
                            cur = m.group(1)[:60]
                    recent = [l[:140] for l in lines[-6:]]
                out["maa"] = {"inst": inst.name, "pid": pidp.read_text().strip() if pidp.exists() else "",
                              "alive": alive, "log_age": age, "cur_task": cur, "recent": recent}
                break
        return out

    @app.get("/api/inspect/processes")
    def inspect_processes():
        """枚举可疑进程（MAA/python/adb/MuMu）含父子关系，查挂着的子代。"""
        import time as _t
        procs = []
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'MAA|python|adb|MuMu|mumu' } "
                "| Select-Object ProcessId, ParentProcessId, CreationDate, Name, CommandLine "
                "| ConvertTo-Json -Compress -Depth 2"],
                capture_output=True, text=True, timeout=40, encoding="utf-8", errors="replace")
            data = json.loads(r.stdout or "[]")
            if isinstance(data, dict):
                data = [data]
            for p in data:
                cmd = p.get("CommandLine") or ""
                if len(cmd) > 200:
                    cmd = cmd[:200] + "..."
                procs.append({"pid": p.get("ProcessId"), "parent": p.get("ParentProcessId"),
                              "name": p.get("Name"), "cmd": cmd})
        except Exception:
            pass
        return {"ok": True, "processes": procs}

    # ── MCP 挂载（2026-08-14：DSH Agent 工具插件）──
    # 注：旧静态 catch-all（GET /{full_path:path}）已随前端退役于 2026-09-26 task-14 删除，
    #     未知路径由 FastAPI 返回干净 404（不再是 FileResponse 指向不存在文件的 500）。
    # streamable-http，挂载点自身校验 x-agent-token（api_token 配置时）。
    # FastAPI mount 子 app 不传播 lifespan → 显式组合 MCP 的 lifespan。
    # ★ 2026-09-26 G6：MCP 挂载开关。fastmcp 导入使 create_app 慢约 5-6s
    #   （冷启动更久），不需要 MCP 的部署可用 mcp_mount_enabled=False 跳过。
    #   缺省 True = 现有行为不变。
    try:
        _mcp_on = bool(mw.config.get("mcp_mount_enabled", True))
    except Exception:
        _mcp_on = True  # 读不到配置 = 维持现状（挂载）
    if not _mcp_on:
        try:
            mw._log("⏭ 已跳过 MCP 挂载（mcp_mount_enabled=False）")
        except Exception:
            pass
    else:
        try:
            from network.mcp_server import build_mcp_app
            _mcp_mounted = build_mcp_app(mw)
            app.mount("/mcp", _mcp_mounted)
            if getattr(_mcp_mounted, "lifespan", None):
                from contextlib import asynccontextmanager as _actx

                @_actx
                async def _combined_lifespan(_app):
                    async with _mcp_mounted.lifespan(_app):
                        yield

                app.router.lifespan_context = _combined_lifespan
        except Exception as _mcp_err:
            try:
                mw._log(f"⚠️ MCP 挂载失败: {_mcp_err}")
            except Exception:
                pass


    return app


def start_server(mw: Any, port: int = 19999) -> None:
    # ★ 2026-09-26 安全收紧：缺省只听本机（同 main_web.pyw:332）。注意本函数
    #   目前无调用方（实际绑定在 main_web.pyw:343 自建 uvicorn.Config），
    #   保留一致缺省以防将来回归/误用。
    bind = mw.config.get("bind_address", "127.0.0.1") if hasattr(mw, 'config') else "127.0.0.1"
    cert = mw.config.get("ssl_cert", "") if hasattr(mw, 'config') else ""
    key = mw.config.get("ssl_key", "") if hasattr(mw, 'config') else ""
    app = create_app(mw)
    # 作业实验室（prts 作业筛选 + 跑不了就自动换下一份）—— 独立模块，挂不上不影响主服务
    try:
        from network import copilot_api as _copilot_api
        _copilot_api.register(app)
    except Exception as _e:
        print("[copilot_api] register failed:", _e)
    config = uvicorn.Config(app, host=bind, port=port, log_level="info",
                            ssl_certfile=cert if cert and key else None,
                            ssl_keyfile=key if cert and key else None)
    server = uvicorn.Server(config)
    server.run()
