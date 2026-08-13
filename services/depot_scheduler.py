"""Depot scheduler — 仓库扫描执行器 + 每日定时（材料规划产品化 2026-08-13）。

DepotScanner: 串行扫账号仓库（独立 Asst 子进程路线，不进 queue 体系 —
用户拍板），扫前互斥检查（挂起/无模拟器/队列活跃/MAA 心跳/runner 进程），
借用空闲 MAA 实例，扫完写快照 + 计划到 models/accounts/<id>/。

DepotScheduler: daemon 线程，每天 daily_scan_time（默认 04:30，可配）对
全部非挂起账号串行扫描，跳过正忙的。配置在 config/material_water_levels.json
的 scan_enabled / daily_scan_time。
"""
from __future__ import annotations
import json
import pathlib
import threading
import time

ROOT = pathlib.Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "material_water_levels.json"

# 扫描状态（API 轮询读）: {account_id: {state, ts, error, reason}}
_SCAN_STATUS: dict = {}
_SCAN_LOCK = threading.Lock()


def _set_status(aid: str, **kw) -> None:
    with _SCAN_LOCK:
        _SCAN_STATUS.setdefault(aid, {}).update({"ts": time.time(), **kw})


def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
    except Exception:
        return False


class DepotScanner:
    """Scan accounts' warehouses → depot.json + plan.json per account."""

    def __init__(self, mw) -> None:
        self.mw = mw

    def _log(self, msg: str) -> None:
        fn = getattr(self.mw, "_log", None)
        if fn:
            fn(f"[材料规划] {msg}")
        else:
            print(f"[{time.strftime('%H:%M:%S')}][材料规划] {msg}")

    # ── 互斥检查 ──
    def busy_reason(self, ac) -> str | None:
        """返回忙因字符串；None=可扫。"""
        aid = ac.get("id", "")
        if ac.get("suspended"):
            return "账号挂起"
        emu = str(ac.get("emu_instance_index", "") or "")
        if not emu:
            return "无模拟器绑定"
        lq = getattr(self.mw, "launch_queue", None)
        if lq is not None:
            if getattr(lq, "is_running", lambda _a: False)(aid):
                return "队列运行中"
            occupant = getattr(lq, "_active_emus", {}).get(emu, "")
            if occupant and occupant != aid:
                return f"模拟器被 {occupant} 占用"
        hb = getattr(self.mw, "_maa_heartbeat", None) or {}
        if time.time() - float(hb.get(aid, 0)) < 10:
            return "MAA 心跳存活(任务中)"
        runner = getattr(self.mw, "runner", None)
        if runner is not None and aid in getattr(runner, "_procs", {}):
            return "MAA 进程运行中"
        return None

    # ── 实例/模拟器准备 ──
    def _pick_free_instance(self) -> int | None:
        """找 .pid 已死且未被 runner 预留的空闲实例号。"""
        base = ROOT / "services" / "maa" / "instances"
        if not base.exists():
            return None
        runner = getattr(self.mw, "runner", None)
        reserved = set()
        for v in getattr(runner, "_inst_reserved", {}).values():
            try:
                reserved.add(str(pathlib.Path(v).resolve()))
            except Exception:
                pass
        try:
            idxs = sorted(int(p.name) for p in base.iterdir()
                          if p.is_dir() and p.name.isdigit())
        except Exception:
            return None
        for i in idxs:
            if str((base / str(i)).resolve()) in reserved:
                continue
            pid_f = base / str(i) / ".pid"
            if pid_f.exists():
                try:
                    if _pid_alive(int(pid_f.read_text().strip())):
                        continue
                except Exception:
                    pass
            return i
        return None

    def _ensure_emu(self, emu: str) -> bool:
        """模拟器没起则拉起（复刻 stage_probe.probe 的启动段）。
        返回 True=本扫描器拉起的（扫描完应关闭），False=原本就在跑（不碰）。"""
        from infrastructure.task_constants import find_mumu_cli
        cli = find_mumu_cli()
        if not cli:
            return False
        idx_flag = "-v" if "MuMuManager" in cli else "--vmindex"
        import subprocess
        try:
            r = subprocess.run([cli, "info", idx_flag, emu],
                               capture_output=True, text=True, timeout=8,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                               encoding="utf-8", errors="replace")
            d = json.loads(r.stdout.lstrip("\ufeff").strip())
            if d.get("is_android_started") and d.get("adb_port"):
                return False
        except Exception:
            pass
        self._log(f"启动模拟器 #{emu}...")
        try:
            subprocess.run([cli, "control", idx_flag, emu, "launch"],
                           capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
        deadline = time.time() + 240
        while time.time() < deadline:
            time.sleep(5)
            try:
                r2 = subprocess.run([cli, "info", idx_flag, emu],
                                    capture_output=True, text=True, timeout=8,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                    encoding="utf-8", errors="replace")
                d2 = json.loads(r2.stdout.lstrip("\ufeff").strip())
                if d2.get("is_android_started") and d2.get("adb_port"):
                    return True
            except Exception:
                pass
        return True

    def _close_emu_if_launched(self, ac, emu: str, launched: bool) -> None:
        """扫描完关闭本扫描器拉起的模拟器（2026-08-13 用户: 测完的模拟器
        不应等 30 分钟回收期；用户手动开的绝不碰）。走项目统一优雅关闭。"""
        if not launched:
            return
        try:
            from infrastructure.task_constants import find_mumu_cli
            cli = find_mumu_cli()
            if not cli:
                return
            from services.launch_queue import graceful_emu_shutdown
            graceful_emu_shutdown(cli, emu, ac.get("adb_path", ""),
                                  ac.get("adb_address", ""))
            # 清掉手动保护标记（模拟器已关，不再需要保护期）
            mts = getattr(self.mw, "manual_emu_started", None)
            if mts is not None:
                mts.pop(str(emu), None)
            self._log(f"关闭扫描拉起模拟器 #{emu}")
        except Exception as e:
            self._log(f"关闭模拟器 #{emu} 失败: {e}")

    def _adb_address(self, ac, emu: str) -> str:
        """端口以模拟器实测为准（禁止公式 — 重启漂移）。"""
        from infrastructure.task_constants import detect_emu_instances
        try:
            for e in detect_emu_instances():
                if str(e.get("index", "")) == emu and e.get("adb_port"):
                    return f"127.0.0.1:{e['adb_port']}"
        except Exception:
            pass
        return ac.get("adb_address", "")

    def _protect_emu(self, emu: str) -> None:
        """扫描直接起模拟器没走队列/状态机 — 防回收器当闲置关掉
        （复刻 api /api/probe/stage 的保护段，2026-08-12 #19 教训）。"""
        try:
            v2 = getattr(self.mw, "emu_service_v2", None)
            if v2 is not None:
                v2.mark_external(str(emu))
            mts = getattr(self.mw, "manual_emu_started", None)
            if mts is not None:
                mts[str(emu)] = time.time()
        except Exception:
            pass

    # ── 代理预检（全权委托实测 — 2026-08-13）──
    # MAA 对代理锁定的关是「静默跳过」：导航识别 UnableToAgent → 不刷
    # 直接走完任务链 → 退出码正常被判"完成" → 计划白给。
    # 主防线 = 计划生成时对要刷的关逐个 stage_probe 实测。
    def _probe_cache_path(self, aid: str) -> pathlib.Path:
        return ROOT / "models" / "accounts" / aid / "farm_probe.json"

    def _load_probe_cache(self, aid: str) -> dict:
        try:
            return json.loads(self._probe_cache_path(aid).read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _make_probe_fn(self, ac, emu: str, client: str):
        """probe_fn(stage) -> {ok, verdict, detail}，带每账号缓存。

        ok=True 可刷 / ok=False 实测锁定 / ok=None 未测到（不因此锁关）。
        缓存策略: CAN_FARM 长期复用（代理权不会丢）；
        CANNOT/DONE 7 天重测（账号推主线后可能解锁）；
        S- 资源本跳过（进度到了必可刷，避免误锁）。
        """
        aid = ac.get("id", "")
        cache = self._load_probe_cache(aid)

        def probe_fn(stage: str) -> dict:
            from services.material_planner import stage_chapter
            if stage.startswith("S"):
                return {"ok": None, "verdict": "SKIP_SUPPLY", "detail": "资源本不预检"}
            old = cache.get(stage)
            if old and old.get("verdict") == "CAN_FARM":
                return old
            if old and old.get("ts") and old.get("verdict") in ("CANNOT", "DONE"):
                if time.time() - float(old["ts"]) < 7 * 86400:
                    old["ok"] = False
                    return old
            inst = self._pick_free_instance()
            if inst is None:
                return {"ok": None, "verdict": "NO_INSTANCE", "detail": "无空闲实例未预检"}
            self._protect_emu(emu)
            from services.stage_probe import StageProbe
            sp = StageProbe(emu, client_type=client, maa_instance=inst, timeout=240)
            try:
                res = sp.probe(stage)
                ok = res.get("verdict") == "CAN_FARM"
                r = {"ok": ok, "verdict": res.get("verdict", ""),
                     "score": res.get("score", 0.0),
                     "detail": res.get("detail", ""), "ts": time.time()}
                cache[stage] = r
                self._log(f"预检 {ac.get('name', aid)} {stage}: "
                          f"{'可刷' if ok else ('锁定' if ok is False else '未定')} "
                          f"({r['verdict']})")
            except Exception as e:
                r = {"ok": None, "verdict": "PROBE_ERROR", "detail": str(e)[:150],
                     "ts": time.time()}
                cache[stage] = r
            try:
                self._probe_cache_path(aid).write_text(
                    json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            return r

        return probe_fn

    def _probe_activity_stages(self, ac, probe_fn) -> None:
        """当前活动的材料/搓玉/资源关逐个预检（复用 farm_probe.json 缓存）。"""
        try:
            act_p = ROOT / "config" / "activity_stages.json"
            act = json.loads(act_p.read_text(encoding="utf-8"))
        except Exception:
            return
        if not act.get("active"):
            return
        stages = [m.get("stage", "") for m in act.get("material") or []]
        o = act.get("orundum") or {}
        if o.get("stage"):
            stages.append(o["stage"])
        for m in act.get("resource") or []:
            if m.get("stage"):
                stages.append(m["stage"])
        for st in stages:
            if st:
                try:
                    probe_fn(st)
                except Exception as e:
                    self._log(f"活动关预检异常 {st}: {e}")

    # ── 扫描主流程 ──
    def scan_account(self, ac) -> bool:
        """扫一个账号；返回是否成功产出快照+计划。"""
        from services.depot_reader import DepotReader
        from services.material_planner import plan, load_config
        aid = ac.get("id", "")
        reason = self.busy_reason(ac)
        if reason:
            _set_status(aid, state="skipped", reason=reason)
            self._log(f"跳过 {ac.get('name', aid)}: {reason}")
            return False
        inst = self._pick_free_instance()
        if inst is None:
            _set_status(aid, state="skipped", reason="无空闲 MAA 实例")
            self._log(f"跳过 {ac.get('name', aid)}: 无空闲 MAA 实例")
            return False
        emu = str(ac.get("emu_instance_index", ""))
        addr = self._adb_address(ac, emu)
        if not addr:
            _set_status(aid, state="error", error="模拟器不可达（无 ADB 地址）")
            return False
        client = {"Bilibili": "Bilibili", "YoStarJP": "JP", "YoStarEN": "EN"}.get(
            ac.get("game_client", ""), "Official")
        self._protect_emu(emu)
        launched = self._ensure_emu(emu)
        _set_status(aid, state="running")
        self._log(f"扫描 {ac.get('name', aid)} (#{emu} {addr}, inst {inst})...")
        try:
            reader = DepotReader(addr, account=ac.get("name", aid),
                                 account_id=aid, client_type=client,
                                 maa_instance=inst)
            snap = reader.read()
            if snap.get("error"):
                _set_status(aid, state="error", error=snap["error"])
                self._log(f"扫描失败 {ac.get('name', aid)}: {snap['error']}")
                return False
            chapter_raw = ac.get("mainline_chapter", 0)
            chapter = int(chapter_raw) if chapter_raw else None
            cfg = load_config()
            # 自适应预算（2026-08-13 用户: 近 24h 实测消耗预测还需几天）
            from services.material_planner import observed_daily_ap
            budget = observed_daily_ap(aid)
            budget_source = "近24h实测" if budget else "默认"
            if budget is None:
                budget = int(cfg.get("ap_budget_per_day", 280))
            probe_fn = self._make_probe_fn(ac, emu, client)
            result = plan(snap, cfg, budget=budget, chapter=chapter, probe_fn=probe_fn)
            # 剩余体力 + 预计还需几天
            cur_sanity = 0
            try:
                from services.sanity_tracker import get as _sg
                _live = _sg(aid)
                if _live and _live.get("current") is not None:
                    cur_sanity = int(_live["current"])
            except Exception:
                pass
            result["budget_source"] = budget_source
            result["cur_sanity"] = cur_sanity
            if budget and result.get("total_ap"):
                import math as _m
                result["remaining_days"] = int(_m.ceil(
                    max(0, result["total_ap"] - cur_sanity) / budget))
            else:
                result["remaining_days"] = len(result.get("days") or [])
            # 活动关顺带预检（2026-08-13）: 分派时锁了要能回退
            self._probe_activity_stages(ac, probe_fn)
            out = ROOT / "models" / "accounts" / aid / "plan.json"
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            probed = result.get("probe") or {}
            _set_status(aid, state="done",
                        summary=f"{len(snap['named'])} 种物品, "
                                f"{len(result['gap_fills'])} 缺口, "
                                f"{len(result['days'])} 天排程, "
                                f"{len(probed)} 关预检, {len(result['locked'])} 锁定")
            self._log(f"计划完成 {ac.get('name', aid)}: "
                      f"{len(result['gap_fills'])} 缺口 / {len(result['days'])} 天 / "
                      f"{len(probed)} 关预检 / {len(result['locked'])} 锁定")
            return True
        except Exception as e:
            _set_status(aid, state="error", error=str(e)[:300])
            self._log(f"扫描异常 {ac.get('name', aid)}: {e}")
            return False
        finally:
            # 扫描收尾：本扫描器拉起的模拟器扫完即关（不等回收期）
            self._close_emu_if_launched(ac, emu, launched)

    def scan_accounts(self, account_ids: list[str]) -> dict:
        """串行扫描指定账号（全局锁，一次只扫一台）。"""
        results = {}
        acs = [a for a in self.mw.accounts if a.get("id") in account_ids]
        for ac in acs:
            ok = self.scan_account(ac)
            results[ac.get("id")] = ok
        return results

    def scan_all(self) -> int:
        """扫全部非挂起且有模拟器的账号；返回成功数。"""
        acs = [a for a in self.mw.accounts
               if not a.get("suspended") and a.get("emu_instance_index")]
        ok = 0
        for ac in acs:
            if self.scan_account(ac):
                ok += 1
        self._log(f"批量扫描完成: {ok}/{len(acs)} 成功")
        return ok


class DepotScheduler(threading.Thread):
    """每日定时扫描线程（main_web.pyw 启动一次）。"""

    def __init__(self, mw) -> None:
        super().__init__(daemon=True, name="depot-scheduler")
        self.mw = mw
        self._last_run_date = ""

    def _load_cfg(self) -> dict:
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _tick(self) -> None:
        cfg = self._load_cfg()
        if not cfg.get("scan_enabled", True):
            return
        hhmm = cfg.get("daily_scan_time", "04:30")
        today = time.strftime("%Y-%m-%d")
        if time.strftime("%H:%M") == hhmm and self._last_run_date != today:
            self._last_run_date = today
            try:
                DepotScanner(self.mw).scan_all()
            except Exception as e:
                fn = getattr(self.mw, "_log", None)
                if fn:
                    fn(f"[材料规划] 定时扫描异常: {e}")

    def run(self) -> None:
        while True:
            try:
                self._tick()
            except Exception:
                pass
            time.sleep(60)
