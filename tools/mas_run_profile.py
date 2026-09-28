#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""mas_run_profile.py —— 按 profile 一次发车（AUTO-MAS）：计划表 / Series / 吃药 / 活动优先 + 读回校验 + 自动还原。

定位：把「用指定配置替某个号跑一趟」做成一条受控命令，八步时序按
`docs/MAS_RUN_PROFILE.md` §4 执行（该文档是机制规范；行号口径=宿主 E:\AUTO-MAS 09-28 现状）：

    ① 备份 → ② 写配置 → ③ preflight → ④ 发车 → ⑤ 读回校验 → ⑥ 等完/超时 → ⑦ 还原 → ⑧ 审计

核心口径（都有出处，别凭记忆改）：
  * 改哪一层看 `Info.StageMode`：`Fixed` ⇒ 读账号自己的 `Info.<7键>`；uuid ⇒ 读那张计划表
    （AutoProxy.py:782-794 @宿主 1153 行版）。**默认路线 = 新建「临时-按需-<yyyymmdd_HHMM>」计划表**
    并把账号 `StageMode` 指过去 —— 不动账号自己的值、也不动存量计划表（多号可能共用）。
    `--write-mode fixed` 是备选（只对 Fixed 账号；写账号自己的 7 键，快照还原）。
  * 临时计划表必须「**全量复制该号当日生效值 + 本次差分**」：未请求的键（Stage_Remain/
    SeriesNumb/MedicineNumb/其余候选位）若不带过去，会被新表默认值（"-"/"0"/0）静默改写
    （docs/MAS_RUN_PROFILE.md §2 注记 1、§4-②）。
  * 多号脚本：一个计划表只服务一个号 ⇒ **每个参与账号各建一张临时计划表**（各带自己的当前值），
    否则会把别的号的关卡计划一起改掉。发车粒度只到「脚本」（task_manager.py:305-316），
    单号=①单号脚本 ②`--disable-others` 临时停用同脚本其它启用号（快照/还原）③`--all-accounts` 接受整脚本跑。
  * `Series` = 每号一张表里的**连战倍数（代理倍率）**，不是总次数上限；"只刷 N 次"没有配置路径
    （简洁模式 EnableTimesLimit 被硬关 AutoProxy:835）——profile 里出现 total_times/times 一律拒绝，
    不静默忽略（docs/MAS_RUN_PROFILE.md §2 行 4、§7）。
  * `UseExpiringMedicine` / `EnableTargetDrop` 不在 AUTO-MAS 两层（MAA 侧字段）⇒ 请求了也不写，
    读回时只记录实际值并告警（§2 行 6/8）。`target_drop.enabled=true` 直接拒绝。
  * 读回校验（L1）= `<MAA目录>/config/gui.new.json` 的 `Configurations.Default.TaskQueue`：
    断言主战斗「理智作战」条目的 `StagePlan/Series/MedicineCount/UseMedicine/IsEnable`
    ＋「剩余理智」条目存在性；多号按 `StartUp.AccountName`（官服掩码 Id）归属窗口。
    L2 = `debug/asst.log` 的 `append_task Fight`（参考级，默认不阻断；`--strict-l2` 才阻断）。

闸门（apply 时逐条；dry-run 只打印）：
  G0 `MAAORCH_DISPATCH_NO_GO` / `MAAORCH_RUN_PROFILE_NO_GO` 一票否决
  G1 REST 可达（绕系统代理；内网走 7890 会间歇 502，见 mas_stage_fix.py 头注）
  G2 profile/目标合法（账号必须启用；多号脚本必须显式 --all-accounts 或 --account）
  G3 互斥锁 `runtime/mas_run_profile.lock`（同一时刻只允许一个 profile 任务；长跑心跳续期）
  G3L 实例租约 `runtime/instance_leases.json`（跨任务/跨工具的**实例层**占位，P1-2 接入）：
      取到租约才动配置；被别人占 ⇒ rc=2 拒发（**零写**：不备份、不写配置、不发车），
      文案带持有者卡号与到期时间（「拿不到就停」与 mas_farm_orchestrator.py 步骤 ⑤ 同口径）。
      **dry-run 与 apply 同口径**：计划期只读探针被占即拒（rc=2）——计划不可执行就不输出「可发」的假象
      （与 mas_ondemand.py 的 G3L 一致）；apply 另在本进程锁内**再取一次**为准（兜住探针→取租的竞态）。
      释放点 = 还原之后（成功/失败/异常都放）；长跑在 touch() 心跳处顺带 renew（节流 120s）；
      进程被 kill 由 TTL 兜底（默认 1800s，`MAAORCH_RUN_PROFILE_LEASE_TTL_S` 可调）。
      `--lease-owner` 覆盖持有者（缺省 `MAAORCH_LEASE_OWNER` → `HERMES_KANBAN_TASK` → `cli:run_profile`）；
      上游已持有同主租约时按「同主=续租」继续、收尾一并释放（mas_farm_orchestrator 链如此，属预期）。
  G4 preflight `tools/mas_preflight.py --script <名> --json` 必须 GO（exit 0），否则回滚退出
  G5 读回不一致 ⇒ 立即 `/api/dispatch/stop` 止损（走 stop 才触发 unlock + Temp 还原）

用法（**默认 dry-run；真执行要 --apply**）：
    python tools/mas_run_profile.py --profile runtime/templates/run_profile.example.json
    python tools/mas_run_profile.py --profile P.json --apply
    python tools/mas_run_profile.py --profile P.json --apply --lease-owner t_abc   # 显式声明租约持有者（缺省 HERMES_KANBAN_TASK → cli:run_profile）
    python tools/mas_run_profile.py --profile P.json --dry-run --preflight     # --dry-run=显式声明缺省行为（无操作开关）；--preflight 让 dry-run 也算一次 G4（只读）
    python tools/mas_run_profile.py --profile P.json --account 烤箱 --apply   # 单号（多号脚本要显式 --disable-others 或 --all-accounts）
    python tools/mas_run_profile.py --status                                   # 运行台账（有 open 记录=可能有残留）
    python tools/mas_run_profile.py --restore-run <run_id> --apply             # 手动还原（崩溃后补）
    python tools/mas_run_profile.py --reconcile --apply                        # 对账：还原「已确认结束」的 open 记录
    python tools/mas_run_profile.py --list-plans                               # 只读：计划表 + 残留「临时-按需-*」
    python tools/mas_run_profile.py --selftest                                 # 离线自测（假传输，不碰网络/runtime 真目录）
    # `--dry-run` 只是显式声明缺省行为（纯无操作开关，rc=0 与不给等价）；与 `--apply` 同给 = 参数冲突，
    # 直接 rc=2 拒绝（不猜意图、不做任何 IO）——见 selftest dry_run_flag_explicit_and_conflict。
    # 注意：缺省 dry-run 在**实例租约被占**时会拒发 rc=2（G3L 计划期只读探针；见上「闸门」段）。

产出/审计：
    runtime/run_profile_state.json         运行台账（open/restored；还原凭它，不凭记忆）
    runtime/run_profile/backups/<run_id>/  备份（meta/账号/计划表快照 + sha256；凭据键读入即弃）
    runtime/mas_run_profile_log.jsonl      每次 plan/apply/readback/restore 一行
    runtime/mas_ledger.jsonl               全局台账一行摘要（kind=run_profile）

退出码：0 正常（含 dry-run 计划成立）；2 拒绝/发车前失败（已回滚，配置干净；含 --apply 与 --dry-run 互斥的参数冲突）；
        3 发车后失败（读回不符/超时，已按策略止损+还原）；4 还原失败（有残留，需要人肉介入）。

约束（写入方不变式）：
  * 绝不重启侧车/模拟器、绝不 kill 进程；要停任务只用 `/api/dispatch/stop`。
  * 只写本次涉及的字段：临时计划表 7 键、账号 `StageMode`、（请求时）`Task.IfActivityFirst/
    ActivityMedicineNumb`、（--disable-others 时）其它号的 `Status`；其余一律不碰。
  * 失败也必须还原：`try/finally` + 落盘 state 先于写入（崩溃可用 --restore-run 补）。
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):                      # Windows 控制台中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
RUNTIME = os.environ.get("MAAORCH_RUN_PROFILE_RUNTIME") or os.path.join(ROOT, "runtime")
STATE = os.path.join(RUNTIME, "run_profile_state.json")
LOG = os.path.join(RUNTIME, "mas_run_profile_log.jsonl")
LEDGER = os.path.join(RUNTIME, "mas_ledger.jsonl")
LEDGER_LOCK = os.path.join(RUNTIME, "mas_ledger.lock")
BACKUPS = os.path.join(RUNTIME, "run_profile", "backups")
LOCK = os.path.join(RUNTIME, "mas_run_profile.lock")
PREFLIGHT = os.path.join(TOOLS, "mas_preflight.py")

API = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
SSH_KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")
PY312 = os.environ.get("MAAORCH_RUN_PROFILE_PYTHON",
                       r"C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe")
APP_LOG = r"E:\AUTO-MAS\debug\app.log"

TEMP_PLAN_PREFIX = "临时-按需"
STAGE_KEYS = ("MedicineNumb", "SeriesNumb", "Stage", "Stage_1", "Stage_2", "Stage_3", "Stage_Remain")
SERIES_OK = ("0", "6", "5", "4", "3", "2", "1", "-1")
STAGE_SLOT_KEYS = ("Stage", "Stage_1", "Stage_2", "Stage_3")
TASK_WRITE_KEYS = ("IfActivityFirst", "ActivityMedicineNumb")
TS_FMT = "%Y-%m-%d %H:%M:%S"
SLEEP = time.sleep                                          # selftest 里替换为快进
NO_GO = (str(os.environ.get("MAAORCH_DISPATCH_NO_GO", "")).strip().lower() in {"1", "true", "yes", "on"}
         or str(os.environ.get("MAAORCH_RUN_PROFILE_NO_GO", "")).strip().lower() in {"1", "true", "yes", "on"})
LOCK_STALE_S = 6 * 3600
LEASE_TTL_S = 1800                                  # 实例租约 TTL（长跑在 touch() 心跳处 renew；被 kill 由它兜底）
LEASE_BEAT_MIN_S = 120.0                            # renew 节流（秒）——别每秒都写账本
DEFAULT_LEASE_OWNER = "cli:run_profile"             # 没有 --lease-owner / 环境变量时的持有者口径
PROFILE_TARGET_KEYS = ("script", "account")
PROFILE_TOP_KEYS = ("target", "stage_plan", "series", "medicine", "activity_priority", "target_drop",
                    "keep_remain_sanity_task", "restore_after", "notes")
FORBIDDEN_TOP_KEYS = ("total_times", "times", "times_limit", "total", "count", "max_times")

MUU_DIR_RE = re.compile(r"^[A-Za-z]:[\\/]MAA-Multi[\\/]MAA_\d+$", re.I)
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
TIMEOUT_RE = re.compile(r"^\s*timeout\s*:\s*(\d+)\s*s?\s*$", re.I)

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class UsageError(Exception):
    """用法/输入问题（拒绝执行）。"""


class TransportError(RuntimeError):
    """REST/SSH 传输失败。"""


class VerifyError(RuntimeError):
    """读回校验失败。"""


class RestoreError(RuntimeError):
    """还原失败（有残留）。"""


# ---------------------------------------------------------------- 基础工具
def _now() -> str:
    return dt.datetime.now().strftime(TS_FMT)


def log(msg: str) -> None:
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def scrub(obj):
    """递归丢弃凭据类键（凭据红线）。"""
    banned = ("password", "token", "secret", "cookie", "credential", "skland")
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if not any(b in str(k).lower() for b in banned)}
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    return obj


def sha256_json(obj) -> str:
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 统一审计流（双写期）
# 接线卡 t_a190ca4b：旧流照写（各 audit/ledger 函数的写出行为不动），旁路再补一条
# tools/mas_audit.py 的统一事件（schema maaorch.audit.v1）。审计失败绝不打断主流程。
# 台账（runtime/mas_ledger.jsonl）是**多工具共用写者**：probe_farm / farm_orchestrator /
# activity_switch 均经本文件的 ledger_append 落账 ⇒ 在此按 `kind` 分派 actor/action
# （与 tools/mas_audit.py::_ad_ledger 同口径；各自不要再加一遍，避免重复计数）。
_LEDGER_ACTOR_ACTION = {
    "run_profile": ("tool:mas_run_profile", "profile.apply"),
    "run_profile_restore": ("tool:mas_run_profile", "profile.restore"),
    "probe_farm": ("tool:mas_probe_farm", "farm.probe"),
    "probe_farm_refused": ("tool:mas_probe_farm", "farm.refuse"),
    "farm_orchestrator": ("tool:mas_farm_orchestrator", "farm.run"),
    "activity_switch": ("tool:activity_switch", "account.activity_switch"),
    "activity_switch_restore": ("tool:activity_switch", "account.activity_switch"),
    "stage_fix": ("tool:mas_stage_fix", "stage.fix"),
    "watch": ("tool:mas_watch", "run.watch"),
    "watch_report": ("tool:mas_watch", "run.watch"),
    "probe_stage": ("tool:mas_probe_farm", "stage.probe"),
    "run_profile_refused": ("tool:mas_run_profile", "profile.refuse"),   # P1-2 租约闸门的拒绝行
}
# 家族前缀兜底：新 kind（如 P1-2 后续加的 *_refused/*_done）仍归到对的工具与域，
# 不至于落到 tool:ledger。域按家族固定，动词从 kind 尾部取（refused→refuse / failed→fail）。
_LEDGER_FAMILY = (
    ("run_profile", "tool:mas_run_profile", "profile"),
    ("probe_farm", "tool:mas_probe_farm", "farm"),
    ("farm_orchestrator", "tool:mas_farm_orchestrator", "farm"),
    ("activity_switch", "tool:activity_switch", "account"),
    ("stage_fix", "tool:mas_stage_fix", "stage"),
)
_VERB_ALIAS = {"refused": "refuse", "failed": "fail", "finished": "done"}


def _ledger_family_of(kind: str):
    """按家族前缀解析 (actor, action)；解析不出返回 (None, None)。"""
    for prefix, actor, domain in _LEDGER_FAMILY:
        if kind.startswith(prefix):
            verb = kind[len(prefix):].strip("_") or "apply"
            verb = _VERB_ALIAS.get(verb, re.sub(r"[^a-z0-9_]+", "_", verb.lower()))
            return actor, "%s.%s" % (domain, verb)
    return None, None


def _unified_guard(fn, rec: dict) -> None:
    """统一审计写入的兜底：任何失败都不打断主流程（同 tools/stage_capability.py::_audit_write）。"""
    try:
        fn(rec)
    except Exception:  # noqa: BLE001
        pass


def _unified_from_audit(rec: dict) -> None:
    """事件行（mas_run_profile_log）→ 统一流：profile.<event>（映射同 mas_audit._ad_profile）。"""
    ev = str(rec.get("event") or "").strip()
    if not ev:
        return
    amap = {"plan_dry_run": "profile.plan", "plan_refused": "profile.refuse",
            "apply_start": "profile.apply", "apply_done": "profile.apply",
            "readback": "profile.readback", "restore": "profile.restore", "done": "profile.done"}
    action = amap.get(ev, "profile." + re.sub(r"[^a-z0-9_]+", "_", ev.lower()))
    res = "refused" if "refus" in ev else ("info" if "dry" in ev else None)
    if res is None:
        ok, rc = rec.get("ok"), rec.get("rc")
        res = "fail" if (ok is False or (isinstance(rc, int) and rc != 0)) else "ok"
    run_id = str(rec.get("run_id") or "")
    script = str(rec.get("script") or "")
    params = {k: rec.get(k) for k in ("profile", "script", "mode", "apply", "refusals", "rc",
                                      "detail", "attempts", "verdict", "failing", "reasons",
                                      "taskId", "dir", "hashes", "failure", "summary")
              if rec.get(k) not in (None, "")}
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import mas_audit  # noqa: PLC0415
    mas_audit.safe_append(
        actor="tool:mas_run_profile", action=action,
        target=("script:" + script) if script else (("run:" + run_id) if run_id else "-"),
        result=res, reason=str(rec.get("reason") or ""), params=params, evidence=[_rel(LOG)],
        card=os.environ.get("HERMES_KANBAN_TASK", ""), run_id=run_id,
        path=mas_audit.stream_for_runtime(RUNTIME))


def _unified_from_ledger(rec: dict) -> None:
    """台账行（mas_ledger）→ 统一流：按 kind 分派 actor/action；未知 kind 落 ledger.<kind>。"""
    kind = str(rec.get("kind") or "").strip()
    if not kind:
        return
    actor, action = _LEDGER_ACTOR_ACTION.get(kind, (None, None))
    if actor is None:
        actor, action = _ledger_family_of(kind)      # 家族前缀兜底（新 kind 不丢归属）
    if actor is None:
        actor, action = "tool:ledger", "ledger." + re.sub(r"[^a-z0-9_]+", "_", kind.lower())
    ok = rec.get("ok")
    res = "ok" if ok is True else ("fail" if ok is False else "info")
    detail = rec.get("detail")
    if isinstance(detail, str):
        with contextlib.suppress(ValueError):
            detail = json.loads(detail)
    reason = ""
    if isinstance(detail, dict):
        verdict = detail.get("verdict")
        reason = str(detail.get("conclusion") or detail.get("reason")
                     or (verdict.get("kind") if isinstance(verdict, dict) else verdict) or "")
    run_id = str(rec.get("run_id") or "")
    params = {k: rec.get(k) for k in ("kind", "scope", "detail", "refusals")
              if rec.get(k) not in (None, "")}
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import mas_audit  # noqa: PLC0415
    mas_audit.safe_append(
        actor=actor, action=action, target=("run:" + run_id) if run_id else "-", result=res,
        reason=reason, params=params, evidence=[_rel(LEDGER)],
        card=os.environ.get("HERMES_KANBAN_TASK", ""), run_id=run_id,
        path=mas_audit.stream_for_runtime(RUNTIME))


def audit(rec: dict) -> None:
    """追加一行到本工具审计流（脱敏）。"""
    os.makedirs(RUNTIME, exist_ok=True)
    rec = dict(rec)
    rec.setdefault("ts", _now())
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(scrub(rec), ensure_ascii=False) + "\n")
    _unified_guard(_unified_from_audit, rec)


def ledger_append(rec: dict) -> None:
    """追加一行到全局台账 `runtime/mas_ledger.jsonl`（与 mas_stage_fix 同一条流）。"""
    os.makedirs(RUNTIME, exist_ok=True)
    rec = dict(rec)
    rec.setdefault("ts", _now())
    for _ in range(3):
        try:
            fd = open(LEDGER_LOCK, "x")
            fd.close()
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - os.path.getmtime(LEDGER_LOCK) > 120:
                    os.remove(LEDGER_LOCK)
                    continue
            time.sleep(2)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(scrub(rec), ensure_ascii=False) + "\n")
    with contextlib.suppress(OSError):
        os.remove(LEDGER_LOCK)
    _unified_guard(_unified_from_ledger, rec)


def norm_dir(p) -> str:
    """/ → \\、折叠重复分隔符、去尾部（比对用）。"""
    s = str(p or "").strip().replace("/", "\\")
    s = re.sub(r"\\+", "\\\\", s)
    return s.rstrip("\\")


def dkey(p) -> str:
    return norm_dir(p).lower()


def slug(s: str, n: int = 14) -> str:
    out = re.sub(r"[^0-9A-Za-z_\-]+", "", str(s or ""))
    return out[:n] or "run"


def _rel(p: str) -> str:
    """相对仓库根的显示路径（跨盘符时退回绝对路径）。"""
    try:
        return os.path.relpath(p, ROOT)
    except ValueError:
        return p


def local_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


# ---------------------------------------------------------------- 互斥锁（长跑心跳续期）
class LockBusy(RuntimeError):
    pass


@contextlib.contextmanager
def _lock(enabled: bool = True):
    if not enabled:
        yield
        return
    local_mkdir(RUNTIME)
    fd = None
    for attempt in (1, 2):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, ("pid=%d ts=%s\n" % (os.getpid(), _now())).encode("utf-8"))
            break
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(LOCK)
            except OSError:
                age = 0.0
            if attempt == 1 and age > LOCK_STALE_S:
                log("发现陈旧锁（%.0f 分钟前）——抢占" % (age / 60))
                with contextlib.suppress(OSError):
                    os.remove(LOCK)
                continue
            raise LockBusy("锁被占（%.0f 秒前创建，%s）——同一时刻只允许一个 profile 任务"
                           % (age, LOCK))
    def touch():
        with contextlib.suppress(OSError):
            os.utime(LOCK, None)
    try:
        yield touch
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                os.remove(LOCK)


# ---------------------------------------------------------------- 实例租约（G3L：跨任务/跨工具的实例层占位）
def _lease_glue():
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    import mas_lease_glue  # noqa: PLC0415
    return mas_lease_glue


def lease_owner(args=None) -> str:
    """租约持有者：--lease-owner > MAAORCH_LEASE_OWNER > HERMES_KANBAN_TASK > cli:run_profile。"""
    return _lease_glue().owner(getattr(args, "lease_owner", None) or DEFAULT_LEASE_OWNER)


def lease_idxs(sc) -> list:
    """目标脚本的实例号（Emulator.Index）；没有 ⇒ 没有可协调的实例（跳过，不当闸门）。"""
    return _lease_glue().idxs_of({"emus": [(sc or {}).get("emu")]})


def _lease_ttl() -> int:
    try:
        v = int(str(os.environ.get("MAAORCH_RUN_PROFILE_LEASE_TTL_S", "")).strip() or LEASE_TTL_S)
    except ValueError:
        v = LEASE_TTL_S
    return max(60, min(86400, v))


def lease_probe(sc, owner_id: str) -> dict:
    """计划期/dry-run 的**只读**租约检查（不取租；apply 的裁决在锁内 acquire）。"""
    LG = _lease_glue()
    idxs = lease_idxs(sc)
    r = LG.check_all(idxs, owner_id=owner_id)
    r["idxs"], r["owner"] = idxs, owner_id
    return r


def lease_acquire(plan: dict, owner_id: str) -> dict:
    """apply 起点取租（全有或全无；同主=续租）。`ok=False` ⇒ 调用方必须**零写**退出。"""
    LG = _lease_glue()
    sc = plan.get("script") or {}
    idxs = lease_idxs(sc)
    purpose = "profile 发车 %s / %s" % (sc.get("name") or "?",
                                        "、".join(a.get("name") or "?" for a in plan.get("accounts") or []) or "?")
    r = LG.acquire_all(idxs, owner_id, purpose, ttl_s=_lease_ttl(), src="run_profile")
    r["idxs"], r["owner"] = idxs, owner_id
    return r


def lease_release(plan: dict, owner_id: str) -> dict:
    """收尾释放（只放自己的：万一被 --force 抢走，不去动别人的）。"""
    r = _lease_glue().release_all(lease_idxs(plan.get("script")), owner_id=owner_id, note="run_profile 收尾")
    r["owner"] = owner_id
    return r


def lease_beat(lease: dict, beat: dict) -> dict:
    """心跳处续租（节流 LEASE_BEAT_MIN_S）。失败只告警**不中断**——把这一趟跑完/还原比守租约重要。"""
    if not (lease or {}).get("ok") or not lease.get("idxs"):
        return {"ok": True, "status": "skipped", "detail": "没有在租的实例"}
    now = time.time()
    if now - float(beat.get("last") or 0) < LEASE_BEAT_MIN_S:
        return {"ok": True, "status": "throttled", "detail": "节流中"}
    beat["last"] = now
    r = _lease_glue().renew_all(lease["idxs"], lease.get("owner") or "", ttl_s=_lease_ttl())
    if not r.get("ok"):
        log("⚠ 实例租约续租失败：%s（继续跑，收尾照常释放）" % (r.get("detail") or r.get("status")))
    return r


def lease_brief(lease) -> dict:
    """审计/摘要用的短视图（别把整条租约塞进每行日志）。"""
    lease = lease or {}
    return {k: lease.get(k) for k in ("ok", "status", "idxs", "owner", "detail", "host") if k in lease}


def refuse_lease_records(plan: dict, args=None) -> str:
    """租约被占的拒发留痕（dry-run 与 apply 同口径）：审计流 + 全局台账各一条；返回拒绝文案。

    文案**原样沿用**租约工具输出（含持有者卡号与到期时间），不另编。
    """
    lz = plan.get("lease") or {}
    detail = _lease_glue().refuse("实例租约（G3L）", lz)
    scope = "profile 发车 %s" % ((plan.get("script") or {}).get("name") or "?")
    audit({"event": "plan_refused", "apply": bool(getattr(args, "apply", False)), "reason": "lease_busy",
           "detail": detail[:300], "profile": getattr(args, "profile", None),
           "script": (plan.get("script") or {}).get("name"), "lease": lease_brief(lz),
           "refusals": plan.get("refusals")})
    ledger_append({"kind": "run_profile_refused", "ok": False, "reason": "lease_busy",
                   "scope": scope, "detail": detail[:300], "lease": lease_brief(lz)})
    return detail


# ---------------------------------------------------------------- REST（绕代理 + 退避重试）
def post(path: str, body: dict | None = None, timeout: int = 60, tries: int = 4) -> dict:
    last = None
    for i in range(tries):
        req = urllib.request.Request(API + path, data=json.dumps(body or {}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with _opener.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = "HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:160])
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, e)
        if i < tries - 1:
            SLEEP(2 + 2 * i)
    raise TransportError("%s 失败：%s" % (path, last))


def reachable() -> tuple:
    try:
        r = post("/api/scripts/get", {}, timeout=15, tries=1)
        return (r.get("code") == 200), "code=%s" % r.get("code")
    except Exception as e:  # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------- 主机侧（复用 mas_ondemand 的 ssh 基建）
def _ond():
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    import mas_ondemand  # noqa: PLC0415
    return mas_ondemand


def ssh_ps(script: str, timeout: int = 180) -> str:
    return _ond().ssh_ps(script, timeout=timeout)


def read_host_file(path: str, timeout: int = 120) -> dict:
    """读主机文件（base64 回传，免代码页/引号坑）。返回 {ok,mtime,size,text} 或 {ok:False,error}。"""
    ps = ("$ErrorActionPreference='Stop'; $p=%s; "
          "if (-not (Test-Path -LiteralPath $p)) { Write-Output 'MISSING'; exit 0 }; "
          "$f = Get-Item -LiteralPath $p; "
          "Write-Output ('META|' + $f.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') + '|' + $f.Length); "
          "Write-Output ([Convert]::ToBase64String([IO.File]::ReadAllBytes($p)))" % json.dumps(path))
    try:
        out = ssh_ps(ps, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "ssh: %s: %s" % (type(e).__name__, e)}
    lines = [x.rstrip("\r") for x in out.splitlines() if x.strip()]
    if not lines:
        return {"ok": False, "error": "ssh 无输出（脚本失败/不通）"}
    if any(x == "MISSING" for x in lines):
        return {"ok": False, "error": "MISSING", "missing": True}
    meta, blob = None, None
    for i, ln in enumerate(lines):
        if ln.startswith("META|"):
            meta = ln.split("|", 2)[1:]
            blob = "".join(lines[i + 1:])
            break
    if meta is None or not blob:
        return {"ok": False, "error": "响应无法解析：%s" % out[-200:]}
    try:
        text = base64.b64decode(blob).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "base64 解码失败：%s" % e}
    return {"ok": True, "mtime": meta[0], "size": meta[1], "text": text}


def read_host_tail(path: str, n: int = 300, contains: str | None = None, timeout: int = 120) -> dict:
    """读主机文件尾部 N 行（可选在远端先按关键词过滤）。"""
    filt = ""
    if contains:
        filt = ("| Where-Object { $_ -match %s }" % json.dumps(contains))
    ps = ("$ErrorActionPreference='Continue'; "
          "$p=%s; if (-not (Test-Path -LiteralPath $p)) { Write-Output 'MISSING'; exit 0 }; "
          "$lines = Get-Content -LiteralPath $p -Tail %d -Encoding UTF8 %s; "
          "foreach ($l in @($lines)) { Write-Output ('L|' + $l) }"
          % (json.dumps(path), int(n), filt))
    try:
        out = ssh_ps(ps, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "ssh: %s: %s" % (type(e).__name__, e), "lines": []}
    if "MISSING" in out.split("\n")[0]:
        return {"ok": False, "error": "MISSING", "lines": []}
    lines = [x[len("L|"):].rstrip("\r") for x in out.splitlines() if x.startswith("L|")]
    return {"ok": True, "lines": lines}


def host_probe(task_uid: str | None, dirs: list, event_lines: int = 4000) -> dict:
    """一次 ssh 拿：在跑的 MAA.exe 目录 + app.log 里与任务相关的调度行。

    ended = app.log 出现「任务结束:/中止任务: <task_uid>」；maa_in_dir = 目标目录还有 MAA.exe。
    """
    want = ";".join(dkey(d) for d in dirs if d)
    ps = r"""
$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'
$OutputEncoding=[System.Text.Encoding]::UTF8; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$dirs = Get-CimInstance Win32_Process -Filter "Name='MAA.exe'" -ErrorAction SilentlyContinue |
  ForEach-Object { $_.ExecutablePath }
Write-Output ('MAADIRS|' + ($dirs -join ';'))
$lines = Get-Content -LiteralPath '__APPLOG__' -Tail __EVENTLINES__ -Encoding UTF8 -ErrorAction SilentlyContinue |
  Where-Object { $_ -match '创建任务:|开始运行任务:|任务结束:|中止任务:' }
foreach ($l in @($lines)) { Write-Output ('T|' + $l) }
Write-Output 'PROBE-END'
""".replace("__APPLOG__", APP_LOG).replace("__EVENTLINES__", str(int(event_lines)))
    try:
        out = ssh_ps(ps, timeout=180)
    except Exception as e:  # noqa: BLE001
        raise TransportError("host_probe ssh 失败：%s: %s" % (type(e).__name__, e))
    if "PROBE-END" not in out:
        raise TransportError("host_probe 无输出：%s" % out[-200:])
    maa_dirs, tlines = [], []
    for ln in out.splitlines():
        ln = ln.rstrip("\r")
        if ln.startswith("MAADIRS|"):
            maa_dirs = [d for d in ln[len("MAADIRS|"):].split(";") if d.strip()]
        elif ln.startswith("T|"):
            tlines.append(ln[2:])
    ended, end_line, started_seen = False, "", False
    if task_uid:
        for ln in tlines:
            if task_uid not in ln:
                continue
            if "任务结束:" in ln or "中止任务:" in ln:
                ended, end_line = True, ln
            elif "创建任务:" in ln or "开始运行任务:" in ln:
                started_seen = True
    want_set = {dkey(d) for d in (want.split(";") if want else []) if d}
    maa_in_dir = False
    for d in maa_dirs:
        try:
            dd = dkey(os.path.dirname(str(d)))        # 进程路径 → 所在目录（去掉 \MAA.exe）
        except Exception:  # noqa: BLE001
            continue
        if dd in want_set:
            maa_in_dir = True
            break
    return {"ended": ended, "end_line": end_line, "started_seen": started_seen,
            "maa_in_dir": maa_in_dir, "maa_dirs": maa_dirs, "tail_lines": len(tlines)}


def pick_python() -> str:
    """preflight 要 OCR（rapidocr）⇒ 优先 Python312；探测失败退 sys.executable。"""
    for c in (PY312, sys.executable):
        if not c or not os.path.exists(c):
            continue
        if c == sys.executable:
            return c
        try:
            r = subprocess.run([c, "-c", "import rapidocr_onnxruntime"], capture_output=True,
                               stdin=subprocess.DEVNULL, timeout=60)
            if r.returncode == 0:
                return c
        except Exception:  # noqa: BLE001
            continue
    return sys.executable


def run_preflight_check(script_name: str, timeout: int = 900, lease_owner: str | None = None) -> dict:
    """跑 tools/mas_preflight.py --script <名> --json；返回 {verdict,exit,failing,reasons,raw}。

    `lease_owner`：把本 run 的租约持有者透给 preflight 第 3L 项（`MAAORCH_LEASE_OWNER`）——
    自己取了的租约不算「被占」，否则 G3L 取租后自己的 G4 会自锁（docs/MAS_INSTANCE_LEASE.md §5.1）。
    """
    if not os.path.exists(PREFLIGHT):
        return {"verdict": "MISSING", "exit": 2, "failing": [], "reasons": ["mas_preflight.py 不在位"], "raw": ""}
    py = pick_python()
    env = dict(os.environ)
    if lease_owner:
        env["MAAORCH_LEASE_OWNER"] = str(lease_owner)
    try:
        p = subprocess.run([py, PREFLIGHT, "--script", script_name, "--json"],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
                           cwd=ROOT, env=env)
    except subprocess.TimeoutExpired:
        return {"verdict": "TIMEOUT", "exit": 2, "failing": [], "reasons": ["preflight 超时"], "raw": ""}
    except Exception as e:  # noqa: BLE001
        return {"verdict": "ERROR", "exit": 2, "failing": [], "reasons": ["%s: %s" % (type(e).__name__, e)],
                "raw": ""}
    blob = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    data, verdict, failing, reasons = None, "", [], []
    try:
        data = json.loads(blob)
    except ValueError:
        m = re.search(r'PREFLIGHT\s*=\s*(GO|NO-GO)', blob) or re.search(r'"preflight"\s*:\s*"(GO|NO-GO)"', blob)
        if m:
            verdict = m.group(1)
    if isinstance(data, dict):
        verdict = str(data.get("preflight") or verdict or "")
        failing = data.get("failing_checks") or []
        reasons = data.get("reasons") or []
        for k, v in (data.get("checks") or {}).items():
            if isinstance(v, dict) and v.get("status") == "FAIL" and k not in failing:
                failing.append(k)
    if not verdict:
        verdict = "UNKNOWN"
    return {"verdict": "GO" if verdict == "GO" else verdict, "exit": p.returncode,
            "failing": failing, "reasons": [str(r) for r in reasons[:6]], "raw": blob[:1500],
            "interpreter": py}


# ---------------------------------------------------------------- 上游读取（只读，白名单）
def fetch_scripts() -> dict:
    """{sid: {sid,name,dir,emu}}（只收 MAA 目录形态的脚本，与 preflight 同口径）。"""
    data = post("/api/scripts/get", {}).get("data") or {}
    out = {}
    for sid, s in data.items():
        info = (s or {}).get("Info") or {}
        p = norm_dir(info.get("Path"))
        if not MUU_DIR_RE.match(p):
            continue
        emu = ((s or {}).get("Emulator") or {}).get("Index")
        out[str(sid)] = {"sid": str(sid), "name": str(info.get("Name") or ""), "dir": p,
                         "emu": str(emu) if emu is not None else ""}
    return out


USER_INFO_KEYS = ("Name", "Id", "Mode", "StageMode", "Server", "Status", "RemainedDay", "Annihilation") + STAGE_KEYS


def fetch_user(sid: str, uid: str) -> dict | None:
    d = post("/api/scripts/user/get", {"scriptId": sid, "userId": uid}).get("data") or {}
    uv = d.get(uid)
    if not isinstance(uv, dict) or not uv:
        return None
    return {"uid": str(uid), "Info": scrub({k: (uv.get("Info") or {}).get(k) for k in USER_INFO_KEYS}),
            "Task": scrub({k: (uv.get("Task") or {}).get(k) for k in TASK_WRITE_KEYS})}


def fetch_users(sid: str) -> list:
    d = post("/api/scripts/user/get", {"scriptId": sid, "userId": None}).get("data") or {}
    out = []
    for uid, uv in d.items():
        if uid == "instances" or not isinstance(uv, dict):
            continue
        info = (uv.get("Info") or {})
        out.append({"uid": str(uid), "name": str(info.get("Name") or ""),
                    "status": bool(info.get("Status")), "mode": info.get("Mode"),
                    "server": info.get("Server"), "remained_day": info.get("RemainedDay"),
                    "id": info.get("Id"), "stage_mode": info.get("StageMode"),
                    "stage": {k: info.get(k) for k in STAGE_KEYS},
                    "task": scrub({k: (uv.get("Task") or {}).get(k) for k in TASK_WRITE_KEYS})})
    return out


def fetch_plans() -> dict:
    r = post("/api/plan/get", {"planId": None})
    data = r.get("data") or {}
    out = {}
    for pid, pv in data.items():
        if not isinstance(pv, dict):
            continue
        out[str(pid)] = {"Info": dict(pv.get("Info") or {}),
                         "ALL": dict(pv.get("ALL") or {}),
                         **{g: dict(pv.get(g) or {}) for g in
                            ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")}}
    return out


def fetch_plan(pid: str) -> dict | None:
    r = post("/api/plan/get", {"planId": pid})
    data = r.get("data") or {}
    pv = data.get(pid)
    return pv if isinstance(pv, dict) else None


# ---------------------------------------------------------------- profile 校验
def _as_bool(v, name: str) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in {"true", "false"}:
        return v.strip().lower() == "true"
    raise UsageError("%s 应为 bool，实得 %r" % (name, v))


def _as_int(v, name: str) -> int:
    if isinstance(v, bool):
        raise UsageError("%s 应为 int，实得 %r" % (name, v))
    if isinstance(v, int):
        return v
    if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
        return int(v.strip())
    raise UsageError("%s 应为 int，实得 %r" % (name, v))


def normalize_stage(v: str) -> str:
    s = str(v).strip()
    return "-" if s.lower() in {"", "-", "none", "null", "空"} else s


def validate_profile(prof: dict) -> tuple:
    """返回 (norm, warnings, errors)。不触网、纯静态。"""
    warns, errs = [], []
    if not isinstance(prof, dict):
        return None, warns, ["profile 必须是 JSON 对象"]
    for k in prof:
        if str(k).startswith("_"):
            continue                              # 下划线开头 = 注释（模板自用）
        if k in FORBIDDEN_TOP_KEYS:
            errs.append("profile 含 %r：AUTO-MAS 两层**没有**「总次数」字段（简洁模式 EnableTimesLimit "
                        "被硬关 AutoProxy:835；详细模式沿用该号 ConfigFile）⇒ 拒绝执行，不静默忽略。"
                        "要限次请用 series（连战倍数，语义不同）或改该号的 MAA ConfigFile。"
                        "（docs/MAS_RUN_PROFILE.md §2 行 4 / §7）" % k)
        elif k not in PROFILE_TOP_KEYS:
            errs.append("profile 顶层键 %r 不认识（白名单：%s）" % (k, "、".join(PROFILE_TOP_KEYS)))
    target = prof.get("target")
    if not isinstance(target, dict):
        errs.append("缺 target（{script, account}）")
        target = {}
    for k in target:
        if k not in PROFILE_TARGET_KEYS:
            errs.append("target.%s 不认识（白名单：script / account）" % k)
    script = str(target.get("script") or "").strip()
    account = str(target.get("account") or "").strip() or None
    if not script:
        errs.append("target.script 必填（脚本名或 uid，如 明日方舟-官-12）")

    stage_plan = prof.get("stage_plan")
    norm_plan = None
    if stage_plan is not None:
        if not isinstance(stage_plan, list) or not (1 <= len(stage_plan) <= 4):
            errs.append("stage_plan 应为 1–4 个关卡码的列表（如 [\"1-7\"] 或 [\"SR-7\",\"1-7\"]）")
        else:
            norm_plan = [normalize_stage(x) for x in stage_plan]
            if all(v == "-" for v in norm_plan):
                errs.append("stage_plan 全是空位（\"-\"）——没有要刷的关卡，拒绝")
    series = prof.get("series")
    norm_series = None
    if series is not None:
        s = str(series).strip()
        if s not in SERIES_OK:
            errs.append("series 只能是 %s 之一（连战倍数/代理倍率；0=自动，-1=不调整）实得 %r"
                        % ("/".join(SERIES_OK), series))
        else:
            norm_series = s
    med = prof.get("medicine")
    norm_med = None
    if med is not None:
        if not isinstance(med, dict):
            errs.append("medicine 应为 {use, count, use_expiring}")
        else:
            for k in med:
                if k not in ("use", "count", "use_expiring"):
                    errs.append("medicine.%s 不认识（白名单：use / count / use_expiring）" % k)
            try:
                use = _as_bool(med.get("use", False), "medicine.use")
                cnt = _as_int(med.get("count", 0), "medicine.count")
            except UsageError as e:
                errs.append(str(e))
                use, cnt = False, 0
            if not (0 <= cnt <= 9999):
                errs.append("medicine.count 取值域 0–9999，实得 %r" % cnt)
            if use and cnt <= 0:
                errs.append("medicine.use=true 但 count<=0 —— 要吃几瓶？")
            exp = med.get("use_expiring")
            if exp is not None:
                exp = _as_bool(exp, "medicine.use_expiring")
                warns.append("medicine.use_expiring=%s：**AUTO-MAS 两层没有这个键**（MAA 侧 UseExpiringMedicine，"
                             "主战斗值随模板/该号 ConfigFile）⇒ 本次不会写它，只读回记录实际值。"
                             "（docs/MAS_RUN_PROFILE.md §2 行 6）" % exp)
            norm_med = {"use": use, "count": cnt, "use_expiring": exp}
    act = prof.get("activity_priority")
    norm_act = None
    if act is not None:
        if not isinstance(act, dict):
            errs.append("activity_priority 应为 {enabled, medicine}")
        else:
            for k in act:
                if k not in ("enabled", "medicine"):
                    errs.append("activity_priority.%s 不认识（白名单：enabled / medicine）" % k)
            try:
                en = _as_bool(act.get("enabled", False), "activity_priority.enabled")
                am = _as_int(act.get("medicine", 0), "activity_priority.medicine")
            except UsageError as e:
                errs.append(str(e))
                en, am = False, 0
            if not (0 <= am <= 9999):
                errs.append("activity_priority.medicine 取值域 0–9999，实得 %r" % am)
            if en:
                warns.append("activity_priority: 将写 Task.IfActivityFirst=True / ActivityMedicineNumb=%d"
                             "（活动列表为空时 AutoProxy 不插该任务，:646-659）" % am)
            norm_act = {"enabled": en, "medicine": am}
    drop = prof.get("target_drop")
    if drop is not None:
        if not isinstance(drop, dict):
            errs.append("target_drop 应为 {enabled, drop_id, count}")
        else:
            try:
                en = _as_bool(drop.get("enabled", False), "target_drop.enabled")
            except UsageError as e:
                errs.append(str(e))
                en = False
            if en:
                errs.append("target_drop.enabled=true：**AUTO-MAS 两层没有目标掉落字段**，简洁模式还被硬关"
                            "（AutoProxy:836）⇒ 拒绝执行。要用只能详细模式改该号 MAA ConfigFile 或打补丁。"
                            "（docs/MAS_RUN_PROFILE.md §2 行 8）")
            else:
                warns.append("target_drop.enabled=false：本次不写该字段（简洁模式 AutoProxy 每轮自会硬关）")
    keep_remain = prof.get("keep_remain_sanity_task")
    norm_keep = True
    if keep_remain is not None:
        try:
            norm_keep = _as_bool(keep_remain, "keep_remain_sanity_task")
        except UsageError as e:
            errs.append(str(e))
    restore_after = str(prof.get("restore_after") or "run_end").strip()
    if restore_after != "run_end" and not TIMEOUT_RE.match(restore_after):
        errs.append("restore_after 应为 \"run_end\" 或 \"timeout:Ns\"，实得 %r" % restore_after)
    norm = {"target": {"script": script, "account": account}, "stage_plan": norm_plan,
            "series": norm_series, "medicine": norm_med, "activity_priority": norm_act,
            "target_drop": None, "keep_remain_sanity_task": norm_keep, "restore_after": restore_after,
            "notes": prof.get("notes")}
    return norm, warns, errs


# ---------------------------------------------------------------- 目标解析 & 生效值
def resolve_script(token: str) -> dict:
    scripts = fetch_scripts()
    if token in scripts:
        return dict(scripts[token])
    hits = [s for s in scripts.values() if s["name"] == token]
    if not hits:
        names = sorted(s["name"] for s in scripts.values())
        raise UsageError("找不到脚本 %r（共 %d 个）；样例：%s"
                         % (token, len(names), "、".join(names[:8])))
    if len(hits) > 1:
        raise UsageError("脚本名 %r 不唯一（%d 个），请用 uid" % (token, len(hits)))
    return dict(hits[0])


def weekday_utc4(now: dt.datetime | None = None) -> str:
    n = now or dt.datetime.now(dt.timezone(dt.timedelta(hours=4)))
    return n.strftime("%A")


def effective_from_plan(plan: dict, now: dt.datetime | None = None) -> tuple:
    """按计划表 Info.Mode 取当日生效 7 键（Weekly 用 UTC+4 当日；组缺项回落 ALL）。"""
    info = plan.get("Info") or {}
    mode = info.get("Mode")
    grp = "ALL"
    if mode == "Weekly":
        day = weekday_utc4(now)
        if isinstance(plan.get(day), dict) and plan.get(day):
            grp = day
    vals = {}
    for i, k in enumerate(STAGE_KEYS):
        v = (plan.get(grp) or {}).get(k)
        if v is None:
            v = (plan.get("ALL") or {}).get(k)
        if v is None:
            v = 0 if k == "MedicineNumb" else ("0" if k == "SeriesNumb" else "-")
        vals[k] = v
    return vals, grp


def account_effective(user: dict, plans: dict, now: dt.datetime | None = None) -> tuple:
    """返回 (7 键生效值, 来源描述)。Fixed ⇒ Info.<7键>；uuid ⇒ 该计划表当日值。"""
    sm = str(user.get("stage_mode") or "Fixed")
    if sm == "Fixed":
        vals = {k: (user.get("stage") or {}).get(k) for k in STAGE_KEYS}
        for k, v in vals.items():
            if v is None:
                vals[k] = 0 if k == "MedicineNumb" else ("0" if k == "SeriesNumb" else "-")
        return vals, "Fixed"
    plan = plans.get(sm)
    if plan is None:
        raise UsageError("账号 %s 的 StageMode=%s 指向的计划表不存在（取不到当日生效值，拒绝执行）"
                         % (user.get("name"), sm))
    vals, grp = effective_from_plan(plan, now)
    return vals, "计划表 %r(%s)" % ((plan.get("Info") or {}).get("Name"), grp)


def build_stage_payload(base_vals: dict, norm: dict) -> dict:
    """全量复制 + 差分 ⇒ 目标 7 键（写临时计划表/账号 Fixed 都用它）。"""
    out = dict(base_vals)
    if norm.get("stage_plan"):
        for i, k in enumerate(STAGE_SLOT_KEYS):
            out[k] = norm["stage_plan"][i] if i < len(norm["stage_plan"]) else "-"
    if norm.get("series") is not None:
        out["SeriesNumb"] = str(norm["series"])
    if norm.get("medicine") is not None:
        m = norm["medicine"]
        out["MedicineNumb"] = int(m["count"]) if m["use"] else 0
    if not norm.get("keep_remain_sanity_task", True):
        out["Stage_Remain"] = "-"
    # 类型归一（写面是"按键写"：MedicineNumb=int、SeriesNumb=str、Stage*=str）
    return {"MedicineNumb": int(out.get("MedicineNumb") or 0),
            "SeriesNumb": str(out.get("SeriesNumb") if out.get("SeriesNumb") is not None else "0"),
            "Stage": str(out.get("Stage") or "-"), "Stage_1": str(out.get("Stage_1") or "-"),
            "Stage_2": str(out.get("Stage_2") or "-"), "Stage_3": str(out.get("Stage_3") or "-"),
            "Stage_Remain": str(out.get("Stage_Remain") or "-")}


def stage_plan_effective(vals: dict) -> list:
    return [("" if vals.get(k, "-") == "*" else vals.get(k, "-"))
            for k in STAGE_SLOT_KEYS if vals.get(k, "-") != "-"]


def account_mask(user: dict) -> str | None:
    """官服 = Id 掩码（AutoProxy 口径：len==11 ⇒ 前3+****+后4）；B 服 = Id 原样；其它 None。"""
    uid = user.get("id")
    if not uid:
        return None
    srv = str(user.get("server") or "")
    if srv == "Official":
        s = str(uid)
        return (s[:3] + "****" + s[7:]) if len(s) == 11 else s
    if srv == "Bilibili":
        return str(uid)
    return None


def readback_expectation(vals: dict, task: dict, mask: str | None, simple: bool = True) -> dict:
    return {"stage_plan": stage_plan_effective(vals), "series": int(str(vals.get("SeriesNumb") or "0")),
            "medicine": int(vals.get("MedicineNumb") or 0),
            "use_medicine": bool(int(vals.get("MedicineNumb") or 0) != 0),
            "remain": (str(vals.get("Stage_Remain") or "-") != "-"),
            "remain_plan": ([("" if vals.get("Stage_Remain") == "*" else vals.get("Stage_Remain"))]
                            if str(vals.get("Stage_Remain") or "-") != "-" else None),
            "if_fight": bool(task.get("IfFight", True)), "mask": mask,
            "strict_simple": bool(simple), "times_limit_note": None}


# ---------------------------------------------------------------- 计划构建（只读）
def build_plan(args) -> dict:
    plan = {"tool": "mas_run_profile", "action": "run_profile", "generated_at": _now(),
            "apply": bool(args.apply), "refusals": [], "warnings": [], "steps": [],
            "profile_path": getattr(args, "profile", None), "script": None, "accounts": [],
            "temp_plans": [], "expected": {}, "preflight": None, "write_mode": args.write_mode,
            "lock": None, "lease": None}
    try:
        prof_raw = load_json(args.profile, None)
    except Exception as e:  # noqa: BLE001
        plan["refusals"].append({"gate": "G2", "reason": "profile_unreadable",
                                 "detail": "%s: %s" % (type(e).__name__, e)})
        return plan
    if prof_raw is None:
        plan["refusals"].append({"gate": "G2", "reason": "profile_unreadable",
                                 "detail": "读不到 profile：%s" % args.profile})
        return plan
    norm, warns, errs = validate_profile(prof_raw)
    plan["profile"] = scrub(prof_raw)
    plan["normalized"] = norm
    plan["warnings"].extend(warns)
    for e in errs:
        plan["refusals"].append({"gate": "G2", "reason": "profile_invalid", "detail": e})
    if errs or norm is None:
        return plan

    ok, why = reachable()
    plan["reachable"] = {"ok": ok, "detail": why}
    if not ok:
        plan["refusals"].append({"gate": "G1", "reason": "rest_unreachable", "detail": why})
        return plan

    try:
        sc = resolve_script(norm["target"]["script"])
    except UsageError as e:
        plan["refusals"].append({"gate": "G2", "reason": "script_not_found", "detail": str(e)})
        return plan
    plan["script"] = sc

    users = fetch_users(sc["sid"])
    enabled = [u for u in users if u["status"] and str(u.get("remained_day")) != "0"]
    acct = norm["target"]["account"] or (args.account if args.account else None)
    plan["script_users"] = [{"name": u["name"], "status": u["status"], "remained_day": u["remained_day"],
                             "stage_mode": u["stage_mode"]} for u in users]

    picks, others = [], []
    if acct:
        hit = [u for u in users if u["name"] == acct]
        if not hit:
            plan["refusals"].append({"gate": "G2", "reason": "account_not_found",
                                     "detail": "脚本 %s 里找不到账号 %r（现有：%s）"
                                               % (sc["name"], acct, "、".join(u["name"] for u in users))})
            return plan
        u = hit[0]
        if not (u["status"] and str(u.get("remained_day")) != "0"):
            plan["refusals"].append({"gate": "G2", "reason": "account_not_enabled",
                                     "detail": "账号 %s 未启用或 RemainedDay=0 —— AutoProxy 会跳过它，"
                                               "本工具不改这些开关" % acct})
            return plan
        picks = [u]
        others = [x for x in enabled if x["uid"] != u["uid"]]
    else:
        picks = list(enabled)
        if len(enabled) > 1 and not args.all_accounts:
            plan["refusals"].append({
                "gate": "G2", "reason": "multi_account_needs_explicit",
                "detail": "脚本 %s 有 %d 个启用号（%s）而你没有指定单号。发车粒度只到脚本"
                          "（task_manager.py:305-316）⇒ 必须显式选择：① --account <名>（只跑它；若还有其它"
                          "启用号需再给 --disable-others 临时停用它们）② --all-accounts（接受整脚本跑）"
                          % (sc["name"], len(enabled), "、".join(u["name"] for u in enabled))})
            return plan
    if not picks:
        plan["refusals"].append({"gate": "G2", "reason": "no_enabled_account",
                                 "detail": "脚本 %s 没有启用中的账号（Status ∧ RemainedDay≠0）——发车会空跑" % sc["name"]})
        return plan

    if others and acct and not args.disable_others:
        plan["refusals"].append({
            "gate": "G2", "reason": "multi_account_needs_explicit",
            "detail": "账号 %s 所在脚本还有 %d 个启用号（%s）会一起跑。要只跑 %s：加 --disable-others"
                      "（临时写 Status=False，工具会快照并在结束/失败时还原）；要整脚本跑：加 --all-accounts。"
                      "（docs/MAS_RUN_PROFILE.md §7-4）"
                      % (acct, len(others), "、".join(u["name"] for u in others), acct)})
        return plan

    plans = fetch_plans()
    for u in picks:
        try:
            base, src = account_effective(u, plans)
        except UsageError as e:
            plan["refusals"].append({"gate": "G2", "reason": "stage_mode_unresolved", "detail": str(e)})
            return plan
        payload = build_stage_payload(base, norm)
        if args.write_mode == "fixed" and str(u.get("stage_mode") or "Fixed") != "Fixed":
            plan["refusals"].append({"gate": "G2", "reason": "fixed_mode_needs_fixed_account",
                                     "detail": "账号 %s 的 StageMode=%s 不是 Fixed —— --write-mode fixed 只对 "
                                               "Fixed 账号可用（否则要改那张计划表，本工具不改存量表）"
                                               % (u["name"], u.get("stage_mode"))})
            return plan
        task = dict(u.get("task") or {})
        simple = (str(u.get("mode") or "") != "详细")
        exp = readback_expectation(payload, task, account_mask(u), simple=simple)
        if not task.get("IfFight", True):
            plan["warnings"].append("账号 %s 的 Task.IfFight=False ⇒ 主战斗条目会被注入成 IsEnable=false，"
                                    "本趟不会真的打（工具不改这个开关）" % u["name"])
        exp["base_source"] = src
        exp["base_before"] = {k: base.get(k) for k in STAGE_KEYS}
        exp["payload"] = payload
        plan["accounts"].append({"uid": u["uid"], "name": u["name"], "server": u["server"],
                                 "mode": u["mode"], "stage_mode": u["stage_mode"],
                                 "id_tail": (str(u.get("id"))[-4:] if u.get("id") else None),
                                 "will_disable": False})
        plan["expected"][u["uid"]] = exp
        plan["temp_plans"].append({"uid": u["uid"], "name": None, "payload": payload,
                                   "base": base, "base_source": src,
                                   "mode": "plan" if args.write_mode == "plan" else "fixed"})
    for u in others:
        plan["accounts"].append({"uid": u["uid"], "name": u["name"], "server": u["server"],
                                 "mode": u["mode"], "stage_mode": u["stage_mode"],
                                 "id_tail": (str(u.get("id"))[-4:] if u.get("id") else None),
                                 "will_disable": True})

    # 步骤与预告
    do_act = norm.get("activity_priority") or {}
    plan["steps"] = [
        {"op": "lease(acquire)", "idx": lease_idxs(sc), "owner": None,
         "note": "G3L：拿不到就停（零写）；dry-run 计划期只读探针被占即拒（rc=2），apply 锁内再取一次为准"},
        {"op": "backup", "to": os.path.join(BACKUPS, "<run_id>")},
        {"op": "plan/add+plan/update", "per_account": len(plan["temp_plans"]),
         "payload_keys": list(STAGE_KEYS)} if args.write_mode == "plan" else
        {"op": "scripts/user/update", "keys": list(STAGE_KEYS), "scope": "账号自己的 Fixed 值（备选路线）"},
        {"op": "scripts/user/update", "StageMode": "<planId>" if args.write_mode == "plan" else "Fixed"},
    ]
    if do_act.get("enabled"):
        plan["steps"].append({"op": "scripts/user/update",
                              "Task": {"IfActivityFirst": True, "ActivityMedicineNumb": do_act.get("medicine", 0)}})
    if others:
        plan["steps"].append({"op": "scripts/user/update", "Status": False, "accounts": [u["name"] for u in others]})
    plan["steps"].append({"op": "preflight", "cmd": "mas_preflight.py --script %s --json" % sc["name"]})
    plan["steps"].append({"op": "dispatch/start", "taskId": sc["sid"], "mode": "AutoProxy"})
    plan["steps"].append({"op": "readback(L1)", "file": sc["dir"] + r"\config\gui.new.json",
                          "assert": ["StagePlan", "Series", "MedicineCount", "UseMedicine", "IsEnable",
                                     "剩余理智条目"]})
    plan["steps"].append({"op": "readback(L2, 参考)", "file": sc["dir"] + r"\debug\asst.log"})
    plan["steps"].append({"op": "wait", "policy": norm["restore_after"]})
    plan["steps"].append({"op": "restore", "what": ["StageMode", "Task 键", "删临时计划表", "Status"]})
    plan["steps"].append({"op": "audit", "files": [_rel(LOG), _rel(LEDGER)]})

    # 告警
    sp = norm.get("stage_plan")
    if sp and "1-7" not in sp:
        plan["warnings"].append("stage_plan 里没有恒开放的 1-7：候选全部「当日不开放」时 MAA 会记"
                                "「关卡计划内没有当日开放的关卡」并跳过理智作战（docs §6-11）")
    for u in picks:
        if str(u.get("mode")) == "详细":
            plan["warnings"].append("账号 %s 是「详细」模式：EnableTimesLimit/EnableTargetDrop 等沿用该号"
                                    "ConfigFile，读回只记录不断言" % u["name"])
    # 宿主计划重启窗口（04:00 / 19:00，事件 1074；§6-15）——只告警不拦
    now = dt.datetime.now()
    for hh in (4, 19):
        edge = now.replace(hour=hh, minute=0, second=0, microsecond=0)
        if edge <= now < edge + dt.timedelta(minutes=25):
            plan["warnings"].append("现在处于宿主计划重启窗口（%02d:00 前后）——在跑任务会被中断，建议避开" % hh)
        elif now < edge <= now + dt.timedelta(seconds=float(args.max_wait_seconds)):
            plan["warnings"].append("等待窗口会跨过 %02d:00 宿主计划重启（事件 1074）——跨窗会中断任务；"
                                    "要么缩短 --max-wait-seconds，要么错峰再发" % hh)

    # G3L 实例租约（计划期只读探针：被占 ⇒ 与 apply 同口径**拒发**（fail-closed；dry-run 也不装「可发」）；
    # apply 的最终裁决仍在本进程锁内 acquire —— 探针与取租之间的竞态由它兜底）
    plan["lease"] = lease_probe(sc, lease_owner(args))
    if plan["steps"]:
        plan["steps"][0]["owner"] = plan["lease"].get("owner")
    if not plan["lease"].get("ok"):
        plan["refusals"].append({"gate": "G3L", "reason": "lease_busy",
                                 "detail": _lease_glue().refuse("实例租约（G3L）", plan["lease"])})
    return plan


def render_plan(plan: dict, as_json: bool) -> str:
    if as_json:
        return json.dumps(plan, ensure_ascii=False, default=str)
    L = ["=== mas_run_profile · %s ===" % ("APPLY" if plan.get("apply") else "DRY-RUN")]
    if plan.get("script"):
        sc = plan["script"]
        L.append("脚本: %s（sid=%s, emu=%s, 目录=%s）" % (sc["name"], sc["sid"][:8], sc["emu"], sc["dir"]))
    norm = plan.get("normalized") or {}
    if norm.get("stage_plan") is not None:
        L.append("stage_plan: %s（写 Stage/Stage_1..3：长度为 4，未给的位清成 '-'）" % norm["stage_plan"])
    if norm.get("series") is not None:
        L.append("series: %s（连战倍数/代理倍率，**不是**总次数上限）" % norm["series"])
    if norm.get("medicine") is not None:
        m = norm["medicine"]
        L.append("medicine: use=%s count=%s（use=false ⇒ MedicineNumb=0）" % (m["use"], m["count"]))
    if norm.get("activity_priority"):
        a = norm["activity_priority"]
        L.append("activity_priority: enabled=%s medicine=%s" % (a["enabled"], a["medicine"]))
    L.append("keep_remain_sanity_task: %s（False ⇒ Stage_Remain=\"-\"，不挂剩余理智）"
             % norm.get("keep_remain_sanity_task"))
    L.append("restore_after: %s" % norm.get("restore_after"))
    L.append("写路线: %s" % ("临时计划表（默认，账号自己的值不动）" if plan.get("write_mode") == "plan"
                             else "Fixed 直写（备选）"))
    for a in plan.get("accounts") or []:
        exp = (plan.get("expected") or {}).get(a["uid"]) or {}
        tag = "（临时停用）" if a.get("will_disable") else ""
        L.append("  账号: %-16s%s server=%s mode=%s StageMode=%s" % (
            a["name"], tag, a["server"], a["mode"], str(a["stage_mode"])[:38]))
        if exp:
            L.append("      目标 7 键: %s" % json.dumps(exp.get("payload"), ensure_ascii=False))
            L.append("      读回期望: StagePlan=%s Series=%s MedicineCount=%s UseMedicine=%s 剩余理智=%s" % (
                exp["stage_plan"], exp["series"], exp["medicine"], exp["use_medicine"], exp["remain"]))
    L.append("  预期写入步骤:")
    for s in plan["steps"]:
        L.append("    - %s" % json.dumps(s, ensure_ascii=False))
    if plan.get("reachable") is not None:
        L.append("  REST: %s (%s)" % ("OK" if plan["reachable"]["ok"] else "UNREACHABLE", plan["reachable"]["detail"]))
    lz = plan.get("lease")
    if lz:
        L.append("  实例租约(G3L): %s idx=%s owner=%s — %s" % (
            "空着/自己的" if lz.get("ok") else "**被占**",
            ",".join(lz.get("idxs") or []) or "—", lz.get("owner") or "—", lz.get("detail") or ""))
        for i, r in (lz.get("rows") or {}).items():
            L.append("      idx%-4s %-4s %s" % (i, r.get("status"), r.get("detail") or ""))
    if plan.get("preflight"):
        pf = plan["preflight"]
        L.append("  preflight: %s%s" % (pf.get("verdict"),
                                        ("（不过：%s）" % ",".join(map(str, pf.get("failing") or []))) if pf.get("failing") else ""))
    for w in plan.get("warnings") or []:
        L.append("  WARN: %s" % w)
    for r in plan.get("refusals") or []:
        L.append("  拒绝[%s]: %s — %s" % (r.get("gate"), r.get("reason"), r.get("detail")))
    if not plan.get("apply"):
        L.append("\n[dry-run] 未触达任何写请求。加 --apply 真执行。")
    return "\n".join(L)


# ---------------------------------------------------------------- 读回解析
def parse_gui(doc: dict) -> dict:
    """取 TaskQueue 里的 Fight 条目 + StartUp（按 Name 分类）。"""
    tq = (((doc.get("Configurations") or {}).get("Default") or {}).get("TaskQueue")) or []
    fights, startup = {}, None
    for it in tq:
        if not isinstance(it, dict):
            continue
        tt = str(it.get("TaskType") or "")
        if tt == "StartUp":
            startup = it
        elif tt == "Fight":
            fights[str(it.get("Name") or "-")] = it
    main = fights.get("理智作战")
    if main is None:
        for name, it in fights.items():
            if name not in ("剩余理智", "剿灭作战", "活动关优先"):
                main = it
                break
    return {"main": main, "remain": fights.get("剩余理智"), "annihilation": fights.get("剿灭作战"),
            "activity": fights.get("活动关优先"), "startup": startup, "fights": fights}


def match_main(obs: dict, exp: dict) -> tuple:
    """主战斗条目 vs 期望：返回 (ok, diffs)。"""
    diffs = []
    if (obs.get("StagePlan") or []) != exp["stage_plan"]:
        diffs.append("StagePlan %r≠%r" % (obs.get("StagePlan"), exp["stage_plan"]))
    if int(obs.get("Series") or 0) != exp["series"]:
        diffs.append("Series %r≠%r" % (obs.get("Series"), exp["series"]))
    if int(obs.get("MedicineCount") or 0) != exp["medicine"]:
        diffs.append("MedicineCount %r≠%r" % (obs.get("MedicineCount"), exp["medicine"]))
    if bool(obs.get("UseMedicine")) != exp["use_medicine"]:
        diffs.append("UseMedicine %r≠%r" % (obs.get("UseMedicine"), exp["use_medicine"]))
    if bool(obs.get("IsEnable")) != exp["if_fight"]:
        diffs.append("IsEnable %r≠%r" % (obs.get("IsEnable"), exp["if_fight"]))
    if exp.get("strict_simple"):
        # §5 期望值表（简洁口径）：简洁分支硬关这两键（AutoProxy:834-836）
        if bool(obs.get("EnableTimesLimit")):
            diffs.append("EnableTimesLimit=%r≠False（简洁应硬关）" % obs.get("EnableTimesLimit"))
        if bool(obs.get("EnableTargetDrop")):
            diffs.append("EnableTargetDrop=%r≠False（简洁应硬关）" % obs.get("EnableTargetDrop"))
    return (not diffs), diffs


def match_readback(parsed: dict, expected: dict, pending: list, pre_mtimes: dict) -> tuple:
    """把一次 gui.new.json 快照归属到某个账号。

    返回 (hit_uid|None, info)。归属规则：主战斗 5 键全等 + 剩余理智条目符合 + 掩码一致（有则校验）。
    时效门：文件 mtime 必须晚于发车前快照（否则读到的可能是上一轮的旧文件，§6-17 回读时序）。
    """
    main = parsed.get("main")
    info = {"observed": None, "mask": None, "annihilation_seen": bool(parsed.get("annihilation") and not main)}
    pre = (pre_mtimes or {}).get("__pre__")
    if pre and parsed.get("mtime") and str(parsed.get("mtime")) <= str(pre):
        info["stale"] = "文件 mtime=%s ≤ 发车前=%s（旧快照，继续等）" % (parsed.get("mtime"), pre)
        return None, info
    if not isinstance(main, dict):
        return None, info
    obs = {k: main.get(k) for k in ("StagePlan", "Series", "MedicineCount", "UseMedicine", "IsEnable",
                                    "EnableTimesLimit", "EnableTargetDrop", "UseExpiringMedicine")}
    mask = ((parsed.get("startup") or {}).get("AccountName")
            if isinstance(parsed.get("startup"), dict) else None)
    info["observed"] = {"main": obs,
                        "remain": (parsed.get("remain") or {}).get("StagePlan") if parsed.get("remain") else None,
                        "remain_series": (parsed.get("remain") or {}).get("Series") if parsed.get("remain") else None,
                        "use_expiring": main.get("UseExpiringMedicine"),
                        "EnableTimesLimit": main.get("EnableTimesLimit"),
                        "EnableTargetDrop": main.get("EnableTargetDrop"),
                        "mask": mask}
    cands = [uid for uid in pending if match_main(obs, expected[uid])[0]]
    if not cands:
        return None, info
    if len(cands) > 1 and mask:
        mask_hits = [uid for uid in cands if expected[uid].get("mask") == mask]
        if mask_hits:
            cands = mask_hits
    if len(cands) > 1:
        info["ambiguous"] = cands
        return None, info
    uid = cands[0]
    # 剩余理智条目核对
    exp = expected[uid]
    rem_ok = True
    if exp["remain"]:
        rem = parsed.get("remain")
        rem_ok = bool(rem) and (rem.get("StagePlan") or []) == exp["remain_plan"] \
            and int(rem.get("Series") or 0) == exp["series"]
    else:
        rem_ok = parsed.get("remain") is None
    if exp.get("mask") and mask and mask != exp["mask"]:
        info["mask_mismatch"] = "%r≠%r" % (mask, exp["mask"])
        return None, info
    if not rem_ok:
        info["remain_mismatch"] = "剩余理智条目不符（期望存在=%s）" % exp["remain"]
        return None, info
    return uid, info


# ---------------------------------------------------------------- 执行（apply）
def new_run_id(sc: dict) -> str:
    return "rp_%s_%s" % (dt.datetime.now().strftime("%Y%m%d_%H%M%S"), slug(sc["name"]))


def temp_plan_name(existing: set, stamp: str, n: int) -> tuple:
    while True:
        name = "%s-%s" % (TEMP_PLAN_PREFIX, stamp) if n == 1 else "%s-%s-%d" % (TEMP_PLAN_PREFIX, stamp, n)
        if name not in existing:
            return name, n
        n += 1


def plan_add(kind: str = "MaaPlan") -> str:
    r = post("/api/plan/add", {"type": kind})
    pid = r.get("planId") or (r.get("data") or {}).get("uid")
    if r.get("code") != 200 or not pid:
        raise TransportError("plan/add 失败：%s" % json.dumps(r, ensure_ascii=False)[:220])
    return str(pid)


def plan_update(pid: str, data: dict) -> None:
    r = post("/api/plan/update", {"planId": pid, "data": data})
    if r.get("code") != 200:
        raise TransportError("plan/update 失败：%s" % json.dumps(r, ensure_ascii=False)[:220])


def plan_delete(pid: str) -> tuple:
    r = post("/api/plan/delete", {"planId": pid})
    return r.get("code") == 200, str(r.get("message") or "")[:160]


def user_update(sid: str, uid: str, data: dict) -> tuple:
    r = post("/api/scripts/user/update", {"scriptId": sid, "userId": uid, "data": data})
    ok = r.get("code") == 200
    return ok, str(r.get("message") or "")[:200]


def dispatch_start(sid: str) -> tuple:
    r = post("/api/dispatch/start", {"taskId": sid, "mode": "AutoProxy"})
    tid = r.get("taskId")
    if r.get("code") == 200 and tid:
        return True, str(tid), r
    return False, None, r


def dispatch_stop(tid: str) -> tuple:
    r = post("/api/dispatch/stop", {"taskId": tid})
    return r.get("code") == 200, r


def save_state(st: dict) -> None:
    st["updated"] = _now()
    runs = st.setdefault("runs", {})
    keep = {k: v for k, v in runs.items() if v.get("status") != "restored"}
    restored = sorted((k for k, v in runs.items() if v.get("status") == "restored"), reverse=True)[:50]
    st["runs"] = {**{k: runs[k] for k in restored}, **keep}
    save_json(STATE, st)


def load_state() -> dict:
    st = load_json(STATE, {"runs": {}})
    if not isinstance(st, dict) or not isinstance(st.get("runs"), dict):
        return {"runs": {}}
    return st


def backup_snapshot(run: dict, plan: dict) -> dict:
    run_dir = run["backup_dir"]
    local_mkdir(run_dir)
    plans_before = fetch_plans()
    accounts_before = {}
    for a in plan["accounts"]:
        u = fetch_user(plan["script"]["sid"], a["uid"])
        if u is None:
            raise TransportError("备份读取账号 %s 失败" % a["name"])
        accounts_before[a["uid"]] = {"name": a["name"], "Info": u["Info"], "Task": u["Task"]}
    files = {
        "meta.json": scrub({"run_id": run["run_id"], "ts": _now(), "api": API,
                            "script": plan["script"], "write_mode": plan["write_mode"],
                            "profile": plan.get("profile"), "apply": True,
                            "accounts": [{"uid": a["uid"], "name": a["name"], "will_disable": a["will_disable"]}
                                         for a in plan["accounts"]]}),
        "plans_before.json": scrub(plans_before),
        "accounts_before.json": scrub(accounts_before),
        "expected.json": scrub(plan["expected"]),
    }
    hashes = {}
    for name, obj in files.items():
        p = os.path.join(run_dir, name)
        save_json(p, obj)
        hashes[name] = sha256_json(obj)
    hashes["accounts_before_sha256"] = hashes["accounts_before.json"]
    hashes["plans_before_sha256"] = hashes["plans_before.json"]
    save_json(os.path.join(run_dir, "hashes.json"), hashes)
    return {"dir": run_dir, "hashes": hashes, "accounts_before": accounts_before}


def poll_readback(run: dict, plan: dict, args) -> dict:
    """轮询 gui.new.json，断言注入值（L1）。返回 {ok, satisfied, evidence, last, polls}。"""
    sc = plan["script"]
    expected = {uid: e for uid, e in plan["expected"].items()
                if uid in {a["uid"] for a in plan["accounts"] if not a.get("will_disable")}}
    pending = list(expected)
    ev = {"polls": [], "satisfied": {}, "other_windows": [], "annihilation_seen": False,
          "last": None, "started": _now()}
    path = sc["dir"] + r"\config\gui.new.json"
    deadline = time.time() + float(args.readback_timeout)
    pre_mtimes = {"__pre__": run.get("pre_mtime")}
    n = 0
    while pending and time.time() < deadline:
        n += 1
        rf = read_host_file(path)
        if not rf.get("ok"):
            ev["polls"].append({"n": n, "t": _now(), "error": rf.get("error")})
            SLEEP(float(args.poll_seconds))
            continue
        try:
            doc = json.loads(rf["text"])
        except ValueError as e:
            ev["polls"].append({"n": n, "t": _now(), "error": "JSON 解析失败：%s" % e})
            SLEEP(float(args.poll_seconds))
            continue
        parsed = parse_gui(doc)
        parsed["mtime"] = rf.get("mtime")
        hit, info = match_readback(parsed, expected, pending, pre_mtimes)
        rec = {"n": n, "t": _now(), "mtime": rf.get("mtime"), "match": hit,
               "observed": info.get("observed")}
        if info.get("stale"):
            rec["stale"] = info["stale"]
        if info.get("annihilation_seen"):
            ev["annihilation_seen"] = True
            rec["note"] = "当前是剿灭段（先剿灭后日常 :464）"
        if hit:
            ev["satisfied"][hit] = rec
            pending.remove(hit)
        elif info.get("observed"):
            ev["last"] = info["observed"]
            if info.get("ambiguous"):
                rec["note"] = "多号同值，无法归属（%s）" % info["ambiguous"]
        ev["polls"].append(rec)
        if pending:
            SLEEP(float(args.poll_seconds))
    ev["ok"] = not pending
    ev["pending"] = pending
    ev["polls_n"] = n
    return ev


def wait_task_end(run: dict, plan: dict, args, touch) -> dict:
    """等任务链结束（app.log 任务结束/中止）或 MAA 进程消失；超时按策略停止。"""
    sc = plan["script"]
    policy = (plan.get("normalized") or {}).get("restore_after") or "run_end"
    m = TIMEOUT_RE.match(policy)
    limit = float(m.group(1)) if m else float(args.max_wait_seconds)
    tid = run.get("task_uid")
    deadline = time.time() + limit
    out = {"policy": policy, "limit_s": limit, "tid": tid, "ended": False, "reason": "",
           "stopped": False, "polls": 0, "start": _now()}
    gone = 0
    while True:
        out["polls"] += 1
        touch()
        try:
            pr = host_probe(tid, [sc["dir"]])
        except TransportError as e:
            out.setdefault("errors", []).append(str(e)[:200])
            pr = {"ended": False, "maa_in_dir": True, "started_seen": True}
        if pr.get("ended"):
            out.update({"ended": True, "reason": "task_end", "end_line": pr.get("end_line", "")[:200]})
            break
        if not pr.get("maa_in_dir"):
            gone += 1
            if gone >= 2:
                out.update({"ended": True, "reason": "maa_exit",
                            "note": "目标目录已无 MAA.exe（app.log 尾部未见结束行，按进程消失判定）"})
                break
        else:
            gone = 0
        if time.time() >= deadline:
            out["timeout"] = True
            break
        SLEEP(float(args.wait_poll_seconds))
    out["duration_s"] = int(time.time() - (dt.datetime.strptime(out["start"], TS_FMT).timestamp()))
    if not out["ended"] and out.get("timeout"):
        if args.stop_on_timeout and tid:
            log("等待超时 ⇒ dispatch/stop %s（走 stop 才触发 unlock + Temp 还原）" % str(tid)[:8])
            try:
                ok, resp = dispatch_stop(tid)
                out["stopped"] = bool(ok)
                out["stop_resp"] = str(resp.get("message"))[:200]
            except Exception as e:  # noqa: BLE001
                out["stopped"] = False
                out["stop_resp"] = "%s: %s" % (type(e).__name__, e)
            # 等解锁（配置被锁时还原会被拒）
            for _ in range(int(args.restore_wait_seconds // 10)):
                touch()
                try:
                    pr = host_probe(tid, [sc["dir"]])
                    if pr.get("ended") or not pr.get("maa_in_dir"):
                        break
                except TransportError:
                    pass
                SLEEP(10)
        else:
            out["note"] = "未停止任务（--no-stop-on-timeout）：还原可能撞配置锁，会重试"
    return out


def l2_check(plan: dict, expected: dict, satisfied: dict) -> dict:
    """L2 参考：debug/asst.log 最后一条 append_task Fight 的 stage/series/medicine。"""
    sc = plan["script"]
    out = {"ok": None, "lines": 0}
    rf = read_host_tail(sc["dir"] + r"\debug\asst.log", n=6000, contains="append_task Fight")
    if not rf.get("ok"):
        out["error"] = rf.get("error")
        return out
    lines = rf.get("lines") or []
    out["lines"] = len(lines)
    if not lines:
        out["error"] = "尾部 6000 行没有 append_task Fight（asst.log 约只留 2 天）"
        return out
    last = lines[-1]
    m = re.search(r"append_task Fight (\{.*\})\s*$", last)
    if not m:
        out["error"] = "行无法解析：%s" % last[-160:]
        return out
    try:
        params = json.loads(m.group(1))
    except ValueError as e:
        out["error"] = "JSON 解析失败：%s" % e
        return out
    out["params"] = {k: params.get(k) for k in ("stage", "series", "medicine", "times")}
    out["line_ts"] = last.strip().lstrip("[").split("]")[0][:19]
    uid = next(iter(satisfied), None) if len(satisfied) == 1 else None
    if uid:
        exp = expected[uid]
        diffs = []
        if exp["stage_plan"]:
            if str(params.get("stage")) not in [str(x) for x in exp["stage_plan"]]:
                diffs.append("stage %r ∉ %r" % (params.get("stage"), exp["stage_plan"]))
        if int(params.get("series") or 0) != exp["series"]:
            diffs.append("series %r≠%r" % (params.get("series"), exp["series"]))
        if int(params.get("medicine") or 0) != exp["medicine"]:
            diffs.append("medicine %r≠%r" % (params.get("medicine"), exp["medicine"]))
        out["diffs"] = diffs
        out["ok"] = not diffs
    return out


def do_writes(run: dict, plan: dict, touch) -> None:
    """② 写配置：临时计划表 + StageMode + Task；写后立即 API 读回。失败抛异常（外层回滚）。"""
    sc = plan["script"]
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
    existing = set()
    with contextlib.suppress(Exception):
        existing = {str((v.get("Info") or {}).get("Name") or "") for v in fetch_plans().values()}
    n = 1
    if plan["write_mode"] == "plan":
        for tp in plan["temp_plans"]:
            name, n = temp_plan_name(existing, stamp, n)
            n += 1
            existing.add(name)
            pid = plan_add("MaaPlan")
            data = {"Info": {"Name": name, "Mode": "ALL"}, "ALL": dict(tp["payload"])}
            plan_update(pid, data)
            got = fetch_plan(pid) or {}
            got_all = got.get("ALL") or {}
            diffs = {k: (got_all.get(k), tp["payload"].get(k)) for k in STAGE_KEYS
                     if str(got_all.get(k)) != str(tp["payload"].get(k))}
            if diffs:
                raise VerifyError("plan/update 后读回不符：%s" % json.dumps(diffs, ensure_ascii=False))
            tp.update({"plan_id": pid, "name": name, "readback": "ok"})
            run["actions"].append({"op": "plan/add+update", "plan_id": pid, "name": name, "payload": tp["payload"]})
            audit({"run_id": run["run_id"], "event": "plan_created", "plan_id": pid, "name": name,
                   "payload": tp["payload"], "account": next(a["name"] for a in plan["accounts"]
                                                              if a["uid"] == tp["uid"])})
    # 其它号临时停用
    for a in plan["accounts"]:
        if a.get("will_disable"):
            ok, msg = user_update(sc["sid"], a["uid"], {"Info": {"Status": False}})
            run["actions"].append({"op": "user/update Status=False", "uid": a["uid"], "name": a["name"],
                                   "ok": ok, "message": msg})
            if not ok:
                raise VerifyError("停用 %s 失败：%s" % (a["name"], msg))
    # 目标号：StageMode / Fixed 7 键 / Task
    act = (plan.get("normalized") or {}).get("activity_priority") or {}
    for tp in plan["temp_plans"]:
        uid = tp["uid"]
        name = next(a["name"] for a in plan["accounts"] if a["uid"] == uid)
        if plan["write_mode"] == "plan":
            ok, msg = user_update(sc["sid"], uid, {"Info": {"StageMode": tp["plan_id"]}})
            run["actions"].append({"op": "user/update StageMode", "uid": uid, "name": name,
                                   "to": tp["plan_id"], "ok": ok, "message": msg})
            if not ok:
                raise VerifyError("写 StageMode(%s) 失败：%s" % (name, msg))
            u = fetch_user(sc["sid"], uid) or {}
            got = str((u.get("Info") or {}).get("StageMode"))
            if got != tp["plan_id"]:
                raise VerifyError("StageMode 读回不符：%s ⇒ %s（期望 %s）" % (name, got, tp["plan_id"]))
        else:
            ok, msg = user_update(sc["sid"], uid, {"Info": {k: tp["payload"][k] for k in STAGE_KEYS}})
            run["actions"].append({"op": "user/update Info.7键", "uid": uid, "name": name,
                                   "to": tp["payload"], "ok": ok, "message": msg})
            if not ok:
                raise VerifyError("写 Fixed 7 键(%s) 失败：%s" % (name, msg))
            u = fetch_user(sc["sid"], uid) or {}
            got = {k: (u.get("Info") or {}).get(k) for k in STAGE_KEYS}
            diffs = {k: (got.get(k), tp["payload"][k]) for k in STAGE_KEYS
                     if str(got.get(k)) != str(tp["payload"][k])}
            if diffs:
                raise VerifyError("Fixed 7 键读回不符：%s" % json.dumps(diffs, ensure_ascii=False))
        if act.get("enabled"):
            ok, msg = user_update(sc["sid"], uid, {"Task": {"IfActivityFirst": True,
                                                           "ActivityMedicineNumb": int(act.get("medicine") or 0)}})
            run["actions"].append({"op": "user/update Task", "uid": uid, "name": name,
                                   "Task": {"IfActivityFirst": True,
                                            "ActivityMedicineNumb": int(act.get("medicine") or 0)},
                                   "ok": ok, "message": msg})
            if not ok:
                raise VerifyError("写 Task 活动优先(%s) 失败：%s" % (name, msg))
    touch()


def restore_run(run_id: str, args, reason: str = "") -> tuple:
    """⑦ 还原（幂等；可手动调用）：StageMode/Task → 删临时计划表 → Status → 回读。
    返回 (ok, detail)。"""
    st = load_state()
    run = (st.get("runs") or {}).get(run_id)
    if not run:
        return False, "未知 run_id：%s" % run_id
    if run.get("status") == "restored":
        return True, "已还原过（%s）" % run.get("restored_at")
    plan_like = {"script": run["script"], "write_mode": run.get("write_mode") or "plan",
                 "accounts": run.get("accounts") or [], "expected": run.get("expected") or {}}
    sid = run["script"]["sid"]
    pending_fixed = {a["uid"]: a.get("prev_stage_keys") for a in plan_like["accounts"]
                     if plan_like["write_mode"] == "fixed" and a.get("prev_stage_keys")}
    attempts = max(1, int(args.restore_attempts))
    gap = max(1, float(args.restore_gap_seconds))
    last = {}
    for i in range(1, attempts + 1):
        pending, ok_ops = [], []
        # ① StageMode / 7 键 / Task 回写
        for a in plan_like["accounts"]:
            uid, name = a["uid"], a["name"]
            cur = fetch_user(sid, uid)
            if cur is None:
                pending.append("读回 %s 失败" % name)
                continue
            info = cur.get("Info") or {}
            task = cur.get("Task") or {}
            if plan_like["write_mode"] == "fixed" and pending_fixed.get(uid):
                want = pending_fixed[uid]
                diffs = {k: (info.get(k), want.get(k)) for k in STAGE_KEYS
                         if str(info.get(k)) != str(want.get(k))}
                if diffs:
                    ok, msg = user_update(sid, uid, {"Info": dict(want)})
                    if not ok:
                        pending.append("回写 7 键 %s：%s" % (name, msg))
            if a.get("prev_stage_mode") is not None and plan_like["write_mode"] == "plan":
                if str(info.get("StageMode")) != str(a["prev_stage_mode"]):
                    ok, msg = user_update(sid, uid, {"Info": {"StageMode": a["prev_stage_mode"]}})
                    if not ok:
                        pending.append("回写 StageMode %s：%s" % (name, msg))
            for k, v in (a.get("prev_task") or {}).items():
                if str(task.get(k)) != str(v):
                    ok, msg = user_update(sid, uid, {"Task": {k: v}})
                    if not ok:
                        pending.append("回写 Task.%s %s：%s" % (k, name, msg))
            if a.get("disabled_by_us"):
                if bool(info.get("Status")) is not True:
                    ok, msg = user_update(sid, uid, {"Info": {"Status": True}})
                    if not ok:
                        pending.append("恢复 Status %s：%s" % (name, msg))
            ok_ops.append(name)
        # ② 删临时计划表（StageMode 已还原，删除会自动把仍指向它的账号改回 Fixed）
        for tp in run.get("temp_plans") or []:
            if run.get("write_mode") != "plan" or not tp.get("plan_id"):
                continue
            exists = fetch_plan(tp["plan_id"]) is not None
            if not exists:
                continue
            ok, msg = plan_delete(tp["plan_id"])
            if not ok and ("不存在" not in msg and "not found" not in msg.lower()):
                pending.append("删计划表 %s：%s" % (tp.get("name"), msg))
        if not pending:
            # ③ 回读校验
            bad = []
            for a in plan_like["accounts"]:
                cur = fetch_user(sid, a["uid"])
                if cur is None:
                    bad.append("%s 读回失败" % a["name"])
                    continue
                info = cur.get("Info") or {}
                if (plan_like["write_mode"] == "plan" and a.get("prev_stage_mode") is not None
                        and str(info.get("StageMode")) != str(a["prev_stage_mode"])):
                    bad.append("%s StageMode=%s≠%s" % (a["name"], info.get("StageMode"), a["prev_stage_mode"]))
                if plan_like["write_mode"] == "fixed" and pending_fixed.get(a["uid"]):
                    for k, v in pending_fixed[a["uid"]].items():
                        if str((info or {}).get(k)) != str(v):
                            bad.append("%s %s=%r≠%r" % (a["name"], k, (info or {}).get(k), v))
                if a.get("disabled_by_us") and bool(info.get("Status")) is not True:
                    bad.append("%s Status=%s≠True" % (a["name"], info.get("Status")))
            for tp in run.get("temp_plans") or []:
                if run.get("write_mode") == "plan" and tp.get("plan_id") and fetch_plan(tp["plan_id"]) is not None:
                    bad.append("计划表仍在：%s" % tp.get("name"))
            if not bad:
                run["status"] = "restored"
                run["restored_at"] = _now()
                run["restore_reason"] = reason
                save_state(st)
                audit({"run_id": run_id, "event": "restore", "ok": True, "reason": reason,
                       "attempts": i, "detail": "；".join(ok_ops)})
                ledger_append({"kind": "run_profile_restore", "ok": True, "run_id": run_id,
                               "reason": reason})
                return True, "还原完成（第 %d 次尝试）：%s" % (i, "；".join(ok_ops) or "-")
            pending = bad
        last = {"attempt": i, "pending": pending}
        if i < attempts:
            log("还原第 %d 次未完成：%s；%ds 后重试（跑中被锁要排队）" % (i, "；".join(map(str, pending))[:300], gap))
            SLEEP(gap)
    run["status"] = "restore_failed"
    run["restore_error"] = json.dumps(last, ensure_ascii=False)[:600]
    save_state(st)
    audit({"run_id": run_id, "event": "restore", "ok": False, "reason": reason, "detail": last})
    ledger_append({"kind": "run_profile_restore", "ok": False, "run_id": run_id, "reason": reason,
                   "detail": str(last)[:300]})
    return False, "还原失败（残留！）：%s" % json.dumps(last, ensure_ascii=False)[:400]


def cmd_run(args) -> int:
    plan = build_plan(args)
    if args.preflight and not plan["refusals"]:
        plan["preflight"] = run_preflight_check(plan["script"]["name"], lease_owner=lease_owner(args))
    if plan["refusals"]:
        if any(r.get("reason") == "lease_busy" for r in plan["refusals"]):
            refuse_lease_records(plan, args)        # 租约拒发：文案/留痕沿用租约工具输出（含持有者+到期）
        else:
            audit({"event": "plan_refused", "apply": bool(args.apply), "profile": args.profile,
                   "refusals": plan["refusals"], "script": (plan.get("script") or {}).get("name")})
        if args.json:
            print(json.dumps({"tool": "mas_run_profile", "action": "run_profile",
                              "refused": plan["refusals"][0]["reason"], "refusals": plan["refusals"]},
                             ensure_ascii=False))
        else:
            print(render_plan(plan, False))
        return 2
    if not args.apply:
        audit({"event": "plan_dry_run", "profile": args.profile,
               "script": plan["script"]["name"],
               "accounts": [a["name"] for a in plan["accounts"]],
               "expected": {k: v["payload"] for k, v in plan["expected"].items()},
               "steps": plan["steps"]})
        print(render_plan(plan, args.json))
        return 0

    if NO_GO:
        audit({"event": "plan_refused", "apply": True, "reason": "dispatch_disabled",
               "detail": "MAAORCH_DISPATCH_NO_GO / MAAORCH_RUN_PROFILE_NO_GO 置位"})
        print("拒绝：MAAORCH_DISPATCH_NO_GO / MAAORCH_RUN_PROFILE_NO_GO 置位（一票否决）", file=sys.stderr)
        return 2

    try:
        with _lock(True) as touch:
            return _apply_locked(args, plan, touch)
    except LockBusy as e:
        audit({"event": "plan_refused", "apply": True, "reason": "concurrent_write", "detail": str(e)})
        print("拒绝：%s" % e, file=sys.stderr)
        return 2


def _apply_locked(args, plan: dict, touch) -> int:
    sc = plan["script"]
    # G3L 实例租约（P1-2）：拿不到就停，零写（不备份、不写配置、不发车）——与 mas_farm_orchestrator 步骤 ⑤ 同口径
    owner_id = lease_owner(args)
    lease = lease_acquire(plan, owner_id)
    plan["lease"] = lease
    if not lease.get("ok"):
        # 计划期探针通常已拒（build_plan 的 G3L）；走到这儿 = 探针→取租之间被人抢走（真竞态）
        detail = refuse_lease_records(plan, args)
        log("✗ %s" % detail)
        if args.json:
            print(json.dumps({"tool": "mas_run_profile", "action": "apply", "apply": True, "ok": False, "rc": 2,
                              "refused": "lease_busy", "detail": detail, "script": sc["name"],
                              "lease": lease_brief(lease)}, ensure_ascii=False))
        else:
            print(render_plan(plan, False))
            print("\n⇒ rc=2 失败：%s" % detail)
        return 2
    log("实例租约：%s（owner=%s，TTL=%ss）" % (lease.get("detail") or lease.get("status"), owner_id, _lease_ttl()))
    beat = {"last": 0.0}

    def touch_lease():
        """本地单飞锁心跳 + 租约续租（节流）；长跑期间别人看得到「这台还在被用」。"""
        touch()
        lease_beat(lease, beat)

    run_id = new_run_id(sc)
    run_dir = os.path.join(BACKUPS, run_id)
    st = load_state()
    run = {"run_id": run_id, "ts": _now(), "status": "prepared", "profile_path": args.profile,
           "script": sc, "write_mode": plan["write_mode"], "backup_dir": run_dir,
           "accounts": [], "temp_plans": [], "expected": plan["expected"], "actions": [],
           "task_uid": None, "readback": None, "wait": None, "l2": None,
           "lease": lease_brief(lease)}
    for a in plan["accounts"]:
        exp = plan["expected"].get(a["uid"]) or {}
        acc = {"uid": a["uid"], "name": a["name"], "server": a["server"],
               "disabled_by_us": bool(a.get("will_disable"))}
        if not a.get("will_disable"):
            u = fetch_user(sc["sid"], a["uid"]) or {}
            acc.update({"prev_stage_mode": (u.get("Info") or {}).get("StageMode"),
                        "prev_task": {k: (u.get("Task") or {}).get(k) for k in TASK_WRITE_KEYS},
                        "prev_stage_keys": {k: (u.get("Info") or {}).get(k) for k in STAGE_KEYS}})
        run["accounts"].append(acc)
    run["temp_plans"] = [{"uid": tp["uid"], "name": tp["name"], "payload": tp["payload"]}
                         for tp in plan["temp_plans"]]
    st.setdefault("runs", {})[run_id] = run
    save_state(st)                                   # 落盘先于任何写入（崩溃可 --restore-run 补）
    log("run_id=%s 备份目录=%s" % (run_id, run_dir))

    rc, failure = 0, None
    try:
        # ① 备份
        bk = backup_snapshot(run, plan)
        run["backup"] = {"dir": bk["dir"], "hashes": bk["hashes"]}
        pre = read_host_file(sc["dir"] + r"\config\gui.new.json")
        run["pre_mtime"] = pre.get("mtime") if pre.get("ok") else None
        save_state(st)
        audit({"run_id": run_id, "event": "backup", "dir": bk["dir"], "hashes": bk["hashes"]})

        # ② 写配置
        do_writes(run, plan, touch_lease)
        run["status"] = "written"
        run["temp_plans"] = [{"uid": tp["uid"], "name": tp["name"], "plan_id": tp.get("plan_id"),
                              "payload": tp["payload"]} for tp in plan["temp_plans"]]
        save_state(st)

        # ③ preflight
        pf = run_preflight_check(sc["name"], lease_owner=owner_id)
        plan["preflight"] = pf
        audit({"run_id": run_id, "event": "preflight", "verdict": pf["verdict"],
               "failing": pf.get("failing"), "reasons": pf.get("reasons")})
        if pf["verdict"] != "GO":
            slug = "nogo" if str(pf["verdict"]).upper().startswith("NO") else str(pf["verdict"]).lower().replace("-", "_")
            failure = ("preflight_%s" % slug,
                       "preflight=%s 不过%s%s" % (pf["verdict"],
                                                  ("（不过项：%s）" % ",".join(map(str, pf["failing"]))) if pf.get("failing") else "",
                                                  ("；" + "；".join(map(str, pf.get("reasons") or []))) if pf.get("reasons") else ""))
            raise VerifyError(failure[1])

        # ④ 发车
        ok, tid, resp = dispatch_start(sc["sid"])
        run["dispatch_resp"] = str(resp)[:300]
        if not ok:
            reason = "dispatch_refused"
            msg = str((resp or {}).get("message") or resp)[:200].lower()
            if "已在运行" in msg or "running" in msg:
                reason = "target_running"
            failure = (reason, "dispatch/start 被拒：%s" % str(resp)[:220])
            raise VerifyError(failure[1])
        run["task_uid"] = tid
        run["status"] = "dispatched"
        save_state(st)
        audit({"run_id": run_id, "event": "dispatch", "taskId": tid, "script": sc["name"]})
        log("已发车：task_uid=%s（脚本 %s）" % (str(tid)[:8], sc["name"]))

        # ⑤ 读回校验
        rb = poll_readback(run, plan, args)
        run["readback"] = rb
        save_state(st)
        for uid, rec in rb["satisfied"].items():
            log("读回通过：%s（mtime=%s observed=%s）" % (
                next(a["name"] for a in run["accounts"] if a["uid"] == uid), rec.get("mtime"),
                json.dumps(rec.get("observed"), ensure_ascii=False)[:200]))
        if not rb["ok"]:
            failure = ("readback_mismatch",
                       "读回校验未通过：%d 个账号未确认（%s）；最后观测=%s" % (
                           len(rb["pending"]), [next(a["name"] for a in run["accounts"] if a["uid"] == u)
                                                for u in rb["pending"]],
                           json.dumps(rb.get("last"), ensure_ascii=False)[:300]))
            # 止损：走 stop 才触发 unlock + Temp 还原
            if run.get("task_uid"):
                ok_, resp_ = dispatch_stop(run["task_uid"])
                audit({"run_id": run_id, "event": "stop_on_mismatch", "ok": bool(ok_),
                       "message": str(resp_.get("message"))[:200]})
                log("读回不符 ⇒ 已止损 stop（ok=%s）" % ok_)
            raise VerifyError(failure[1])

        # L2 参考
        run["l2"] = l2_check(plan, plan["expected"], rb["satisfied"])
        if run["l2"].get("ok") is False:
            audit({"run_id": run_id, "event": "l2_mismatch", "detail": run["l2"]})
            log("L2 参考不符（%s）——core 实收参数与 L1 有落差" % run["l2"].get("diffs"))
            if args.strict_l2:
                failure = ("l2_mismatch", "L2 实收参数与期望不符：%s" % run["l2"].get("diffs"))
                if run.get("task_uid"):
                    dispatch_stop(run["task_uid"])
                raise VerifyError(failure[1])
        save_state(st)

        # ⑥ 等完/超时
        wait = wait_task_end(run, plan, args, touch_lease)
        run["wait"] = wait
        save_state(st)
        if not wait.get("ended"):
            failure = ("wait_timeout",
                       "等待超时（%.0fs）：任务未确认结束%s" % (wait.get("limit_s") or 0,
                                                              "，已 dispatch/stop" if wait.get("stopped") else ""))
            raise VerifyError(failure[1])
        log("任务结束：reason=%s（%ss）" % (wait.get("reason"), wait.get("duration_s")))
    except VerifyError as e:
        rc = 2 if (failure and failure[0] in ("preflight_nogo", "preflight_missing", "preflight_error",
                                             "preflight_timeout", "dispatch_refused",
                                             "target_running")) else 3
        if failure is None:
            failure = ("verify_error", str(e))
        log("✗ %s：%s" % (failure[0], failure[1]))
    except (TransportError, UsageError) as e:
        rc, failure = 2, ("transport_or_input", "%s: %s" % (type(e).__name__, e))
        log("✗ %s" % failure[1])
    except Exception as e:  # noqa: BLE001
        rc, failure = 2, ("execute_error", "%s: %s" % (type(e).__name__, e))
        log("✗ 未预期异常：%s" % failure[1])
    finally:
        # ⑦ 还原（失败也必须还原）；⑦b 之后一定释放实例租约（成功/失败/异常都放；被 kill 由 TTL 兜底）
        try:
            ok, detail = restore_run(run_id, args, reason=(failure[0] if failure else "run_end"))
        finally:
            lease_rel = lease_release(plan, owner_id)
            plan["lease_release"] = lease_rel
            run["lease_release"] = lease_brief(lease_rel)
            if not lease_rel.get("ok"):
                log("⚠ 租约释放有失败项：%s（TTL 到期会自动失效，别慌）" % (lease_rel.get("detail") or lease_rel.get("status")))
            else:
                log("实例租约已释放：%s" % (lease_rel.get("detail") or lease_rel.get("status")))
            try:                                   # 把释放结果补记进运行台账（restore_run 刚写完盘）
                st2 = load_state()
                rec2 = (st2.get("runs") or {}).get(run_id)
                if isinstance(rec2, dict):
                    rec2["lease_release"] = lease_brief(lease_rel)
                    save_state(st2)
            except Exception as e:  # noqa: BLE001 —— 台账补记失败不该影响还原结论，只告警
                log("⚠ 租约释放未写回台账：%s: %s" % (type(e).__name__, e))
        if not ok:
            rc = 4
        elif failure is None:
            rc = 0
    # ⑧ 审计
    st = load_state()
    run = (st.get("runs") or {}).get(run_id) or run
    summary = {
        "tool": "mas_run_profile", "action": "apply", "run_id": run_id, "ok": (rc == 0),
        "rc": rc, "failure": failure, "script": sc["name"], "dir": sc["dir"],
        "accounts": [a["name"] for a in run.get("accounts") or []],
        "payload": {k: v["payload"] for k, v in (plan["expected"] or {}).items()},
        "task_uid": run.get("task_uid"),
        "readback": {"ok": (run.get("readback") or {}).get("ok"),
                     "satisfied": list(((run.get("readback") or {}).get("satisfied") or {}).keys()),
                     "polls": (run.get("readback") or {}).get("polls_n")},
        "wait": {k: (run.get("wait") or {}).get(k) for k in ("ended", "reason", "duration_s", "stopped")},
        "l2": run.get("l2"), "restore": {"status": run.get("status"), "reason": run.get("restore_reason")},
        "backup": run_dir,
        "lease": {"acquire": lease_brief(lease), "release": lease_brief(plan.get("lease_release"))},
    }
    audit({"run_id": run_id, "event": "done", "rc": rc, "failure": failure, "summary": summary})
    ledger_append({"kind": "run_profile", "ok": bool(rc == 0), "run_id": run_id,
                   "scope": "profile 发车：%s / %s" % (sc["name"], "、".join(summary["accounts"])),
                   "detail": json.dumps({k: summary[k] for k in ("failure", "task_uid", "readback",
                                                                 "wait", "restore")}, ensure_ascii=False)[:400]})
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(render_plan(plan, False))
        print("\n⇒ rc=%d %s" % (rc, ("完成" if rc == 0 else "失败：%s" % (failure[1] if failure else detail))))
        print("运行台账：kind=run_profile run_id=%s（runtime/mas_ledger.jsonl）" % run_id)
    return rc


# ---------------------------------------------------------------- 其它命令
def cmd_status(args) -> int:
    st = load_state()
    runs = st.get("runs") or {}
    if args.json:
        print(json.dumps(runs, ensure_ascii=False, indent=2, default=str))
        return 0
    if not runs:
        print("（无运行记录）state=%s" % STATE)
        return 0
    print("%-32s %-15s %-12s %-22s %s" % ("run_id", "status", "task_uid", "script", "ts"))
    for rid, r in sorted(runs.items()):
        print("%-32s %-15s %-12s %-22s %s" % (
            rid, r.get("status"), str(r.get("task_uid"))[:8], str((r.get("script") or {}).get("name"))[:22],
            r.get("ts")))
    open_runs = [r for r in runs.values() if r.get("status") not in ("restored",)]
    if open_runs:
        print("\n⚠ 有 %d 条未还原记录（可能有残留配置）：--reconcile 或 --restore-run 处理" % len(open_runs))
    return 0


def cmd_list_plans(args) -> int:
    plans = fetch_plans()
    leftovers = []
    for pid, p in sorted(plans.items(), key=lambda kv: str((kv[1].get("Info") or {}).get("Name"))):
        name = str((p.get("Info") or {}).get("Name") or "")
        mark = ""
        if name.startswith(TEMP_PLAN_PREFIX):
            mark = "  ← 本工具遗留？"
            leftovers.append((pid, name))
        print("%s  name=%r mode=%s ALL=%s%s" % (pid, name, (p.get("Info") or {}).get("Mode"),
                                                json.dumps(p.get("ALL") or {}, ensure_ascii=False), mark))
    if leftovers:
        print("\n⚠ %d 张「%s-*」计划表 —— 若对应 run 已 restored，可用 --restore-run 之外的人工删除"
              "（AUTO-MAS 界面/plan/delete）确认无残留" % (len(leftovers), TEMP_PLAN_PREFIX))
    return 0


def cmd_reconcile(args) -> int:
    """对账：把「任务已确认结束」或「从未发车」的 open 记录还原。"""
    st = load_state()
    open_runs = {k: v for k, v in (st.get("runs") or {}).items()
                 if v.get("status") not in ("restored", "restore_failed")}
    if not open_runs:
        print("对账：没有未还原记录")
        return 0
    rc = 0
    for rid, run in open_runs.items():
        tid = run.get("task_uid")
        due, why = True, "从未发车（无 task_uid）"
        if tid:
            try:
                pr = host_probe(tid, [(run.get("script") or {}).get("dir")])
                due = bool(pr.get("ended"))
                why = "任务已结束（%s）" % pr.get("end_line", "")[:120] if due else "app.log 未见结束行：任务可能仍在跑"
            except TransportError as e:
                due, why = False, "探测失败（fail-closed，不动）：%s" % e
        print("%s：%s（status=%s）" % (rid, why, run.get("status")))
        if not due:
            rc = max(rc, 1)
            continue
        if not args.apply:
            print("  [dry-run] would restore（加 --apply 真还原）")
            continue
        ok, detail = restore_run(rid, args, reason="reconcile")
        print("  %s %s" % ("✓" if ok else "✗", detail))
        if not ok:
            rc = max(rc, 3)
    return rc


def cmd_restore_run(args) -> int:
    st = load_state()
    rid = args.restore_run
    if rid not in (st.get("runs") or {}):
        print("未知 run_id：%s（--status 看台账）" % rid, file=sys.stderr)
        return 2
    if not args.apply:
        print("[dry-run] would restore %s；加 --apply 真还原" % rid)
        return 0
    if NO_GO:
        print("拒绝：NO_GO 置位", file=sys.stderr)
        return 2
    try:
        ok, detail = restore_run(rid, args, reason="manual")
    except LockBusy as e:
        print("拒绝：%s" % e, file=sys.stderr)
        return 2
    print(("✓ " if ok else "✗ ") + detail)
    return 0 if ok else 4


# ---------------------------------------------------------------- 离线自测（假传输）
class _FakeMAS:
    """AUTO-MAS 传输桩：覆盖本工具用到的全部端点 + 调用流水。"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.calls = []
        self.scripts = {"sid-1": {"Info": {"Name": "脚本单号", "Path": r"E:\MAA-Multi\MAA_1"},
                                  "Emulator": {"Index": 13}},
                        "sid-2": {"Info": {"Name": "脚本多号", "Path": r"E:\MAA-Multi\MAA_2"},
                                  "Emulator": {"Index": 14}}}
        self.users = {
            "sid-1": {"u1": {"Info": {"Name": "单号甲", "Id": "12345678901", "Mode": "简洁",
                                      "StageMode": "Fixed", "Server": "Official", "Status": True,
                                      "RemainedDay": -1, "MedicineNumb": 0, "SeriesNumb": "0",
                                      "Stage": "SR-7", "Stage_1": "1-7", "Stage_2": "-", "Stage_3": "-",
                                      "Stage_Remain": "1-7"},
                            "Task": {"IfFight": True, "IfActivityFirst": False, "ActivityMedicineNumb": 0}}},
            "sid-2": {"u2": {"Info": {"Name": "多号乙", "Id": "22345678902", "Mode": "简洁",
                                      "StageMode": "pl-old", "Server": "Official", "Status": True,
                                      "RemainedDay": -1, "MedicineNumb": 0, "SeriesNumb": "0",
                                      "Stage": "-", "Stage_1": "-", "Stage_2": "-", "Stage_3": "-",
                                      "Stage_Remain": "1-7"},
                            "Task": {"IfFight": True, "IfActivityFirst": False, "ActivityMedicineNumb": 0}},
                      "u3": {"Info": {"Name": "多号丙", "Id": "32345678903", "Mode": "简洁",
                                      "StageMode": "Fixed", "Server": "Official", "Status": True,
                                      "RemainedDay": -1, "MedicineNumb": 0, "SeriesNumb": "0",
                                      "Stage": "1-7", "Stage_1": "-", "Stage_2": "-", "Stage_3": "-",
                                      "Stage_Remain": "-"},
                            "Task": {"IfFight": True, "IfActivityFirst": False, "ActivityMedicineNumb": 0}},
                      "u4": {"Info": {"Name": "停用丁", "Id": "42345678904", "Mode": "简洁",
                                      "StageMode": "Fixed", "Server": "Official", "Status": False,
                                      "RemainedDay": -1, "MedicineNumb": 0, "SeriesNumb": "0",
                                      "Stage": "1-7", "Stage_1": "-", "Stage_2": "-", "Stage_3": "-",
                                      "Stage_Remain": "-"},
                            "Task": {"IfFight": True, "IfActivityFirst": False, "ActivityMedicineNumb": 0}}},
        }
        self.plans = {"pl-old": {"Info": {"Name": "旧计划", "Mode": "Weekly"},
                                 "ALL": {"MedicineNumb": 0, "SeriesNumb": "0", "Stage": "CE-6",
                                         "Stage_1": "-", "Stage_2": "-", "Stage_3": "-", "Stage_Remain": "1-7"}}}
        self.n_plan, self.n_task = 0, 0
        self.fail_start = None

    def post(self, path, body=None, timeout=60, tries=4):
        body = body if isinstance(body, dict) else {}
        self.calls.append((path, body))
        if path == "/api/scripts/get":
            return {"code": 200, "data": self.scripts}
        if path == "/api/scripts/user/get":
            sid = str(body.get("scriptId") or "")
            uid = body.get("userId")
            if uid:
                u = self.users.get(sid, {}).get(str(uid))
                return {"code": 200, "data": ({str(uid): u} if u else {})}
            return {"code": 200, "data": dict(self.users.get(sid, {}))}
        if path == "/api/scripts/user/update":
            sid, uid = str(body.get("scriptId")), str(body.get("userId"))
            data = body.get("data") or {}
            u = self.users.setdefault(sid, {}).setdefault(uid, {"Info": {}, "Task": {}})
            for grp, kv in data.items():
                u.setdefault(grp, {}).update(kv)
            return {"code": 200, "status": "success", "message": "操作成功"}
        if path == "/api/plan/get":
            pid = body.get("planId")
            if pid:
                return {"code": 200, "index": [], "data": ({str(pid): self.plans[str(pid)]}
                                                           if str(pid) in self.plans else {})}
            return {"code": 200, "index": [{"uid": k, "type": "MaaPlanConfig"} for k in self.plans],
                    "data": dict(self.plans)}
        if path == "/api/plan/add":
            self.n_plan += 1
            pid = "pl-new-%d" % self.n_plan
            self.plans[pid] = {"Info": {"Name": "新 MAA 计划表", "Mode": "ALL"},
                               "ALL": {k: (0 if k == "MedicineNumb" else ("0" if k == "SeriesNumb" else "-"))
                                       for k in STAGE_KEYS}}
            return {"code": 200, "status": "success", "message": "操作成功", "planId": pid,
                    "data": dict(self.plans[pid])}
        if path == "/api/plan/update":
            pid = str(body.get("planId"))
            if pid not in self.plans:
                return {"code": 500, "status": "error", "message": "计划表不存在"}
            for grp, kv in (body.get("data") or {}).items():
                self.plans[pid].setdefault(grp, {}).update(kv)
            return {"code": 200, "status": "success", "message": "操作成功"}
        if path == "/api/plan/delete":
            self.plans.pop(str(body.get("planId")), None)
            return {"code": 200, "status": "success", "message": "操作成功"}
        if path == "/api/dispatch/start":
            if self.fail_start:
                r, self.fail_start = self.fail_start, None
                return r
            self.n_task += 1
            return {"code": 200, "status": "success", "message": "操作成功", "taskId": "task-%d" % self.n_task}
        if path == "/api/dispatch/stop":
            return {"code": 200, "status": "success", "message": "操作成功"}
        return {"code": 404, "message": "unknown endpoint %s" % path}


_FAKE_FORCE = {}


def _fake_gui_doc(fake: _FakeMAS, uid: str) -> str:
    """按账号当前 StageMode 模拟 AutoProxy 注入后的 gui.new.json（L1 断言的真实验证对象）。"""
    u = None
    for sid, us in fake.users.items():
        if uid in us:
            u = us[uid]
            break
    info = u["Info"]
    sm = str(info.get("StageMode"))
    if sm == "Fixed":
        vals = {k: info.get(k) for k in STAGE_KEYS}
    else:
        vals = dict(fake.plans.get(sm, {}).get("ALL") or {})
    sp = [("" if vals.get(k) == "*" else vals.get(k)) for k in STAGE_SLOT_KEYS if vals.get(k) not in ("-", None)]
    main = {"TaskType": "Fight", "Name": "理智作战", "IsEnable": bool(u["Task"].get("IfFight", True)),
            "StagePlan": sp, "Series": int(str(vals.get("SeriesNumb") or "0")),
            "MedicineCount": int(vals.get("MedicineNumb") or 0),
            "UseMedicine": bool(int(vals.get("MedicineNumb") or 0) != 0),
            "UseExpiringMedicine": True, "MedicineExpireDays": 2,
            "EnableTimesLimit": bool(_FAKE_FORCE.get("times_limit", False)), "TimesLimit": 2147483647,
            "EnableTargetDrop": bool(_FAKE_FORCE.get("target_drop", False)),
            "IsStageManually": True, "UseOptionalStage": True}
    tq = [{"TaskType": "StartUp", "Name": "开始唤醒", "IsEnable": True,
           "AccountName": (info["Id"][:3] + "****" + info["Id"][7:]) if len(str(info["Id"])) == 11 else info["Id"],
           "AccountSwitchEnabled": True}, main]
    if str(vals.get("Stage_Remain") or "-") != "-":
        tq.append({**main, "Name": "剩余理智",
                   "StagePlan": [("" if vals.get("Stage_Remain") == "*" else vals.get("Stage_Remain"))],
                   "Series": int(str(vals.get("SeriesNumb") or "0")), "UseOptionalStage": False})
    doc = {"Configurations": {"Default": {"TaskQueue": tq, "Gui": {}}}, "ConfigGui": {}, "ConfigVersion": "x"}
    return json.dumps(doc, ensure_ascii=False)


def selftest() -> dict:
    """离线回归：全链路（假传输）。覆盖 dry-run 零写、临时计划表全量复制、preflight 回滚、
    读回不符止损、超时停止、还原、多号拒绝/停用还原、profile 校验红灯。"""
    import io
    import shutil
    import tempfile

    cases = []

    def add(name, fn):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                fn()
            cases.append({"name": name, "ok": True, "detail": ""})
        except Exception as e:  # noqa: BLE001
            cases.append({"name": name, "ok": False, "detail": "%s: %s" % (type(e).__name__, e)})

    def ck(cond, msg):
        if not cond:
            raise AssertionError(msg)

    tmp = tempfile.mkdtemp(prefix="mas_run_profile_selftest_")
    saved = {k: globals()[k] for k in ("RUNTIME", "STATE", "LOG", "LEDGER", "LOCK", "BACKUPS",
                                       "NO_GO", "SLEEP")}
    saved_fns = {k: globals()[k] for k in ("post", "reachable", "run_preflight_check", "host_probe",
                                           "read_host_file", "read_host_tail", "ssh_ps")}
    F = _FakeMAS()
    globals()["RUNTIME"] = tmp
    globals()["STATE"] = os.path.join(tmp, "state.json")
    globals()["LOG"] = os.path.join(tmp, "run_profile_log.jsonl")
    globals()["LEDGER"] = os.path.join(tmp, "ledger.jsonl")
    globals()["LOCK"] = os.path.join(tmp, "lock")
    globals()["BACKUPS"] = os.path.join(tmp, "backups")
    globals()["SLEEP"] = lambda s: None
    LEASE_RT = os.path.join(tmp, "lease")            # 租约账本也隔离（P1-2）：绝不动 runtime/ 真账本
    lease_env_saved = os.environ.get("MAAORCH_LEASE_RUNTIME")
    os.environ["MAAORCH_LEASE_RUNTIME"] = LEASE_RT
    _lease_glue().set_runtime(LEASE_RT)
    window = {"uid": None, "delay": 0}
    probes = {"ended": False, "maa_in_dir": True}
    mt = {"n": 0}
    pf_seen = {}

    def pf_go(name, timeout=900, lease_owner=None):
        pf_seen["lease_owner"] = lease_owner
        return {"verdict": "GO", "exit": 0, "failing": [], "reasons": [], "raw": ""}

    def pf_nogo(name, timeout=900, lease_owner=None):
        pf_seen["lease_owner"] = lease_owner
        return {"verdict": "NO-GO", "exit": 1, "failing": ["slot_idle"], "reasons": ["槽位忙"], "raw": ""}

    def lease_state() -> dict:
        m = _lease_glue().mod()
        assert m is not None, "租约机制应可用（tools/mas_instance_lease.py）"
        return m.load()

    def fake_mtime():
        mt["n"] += 1
        return (dt.datetime.now() + dt.timedelta(seconds=60 * mt["n"])).strftime(TS_FMT)

    def fake_read(path, timeout=120):
        if path.endswith("gui.new.json"):
            uid = window["uid"]
            if not uid:
                return {"ok": False, "error": "MISSING", "missing": True}
            mt_v = window.get("frozen_mtime") or fake_mtime()
            return {"ok": True, "mtime": mt_v, "size": "1", "text": _fake_gui_doc(F, uid)}
        if path.endswith("asst.log"):
            u = F.users["sid-1"]["u1"]["Info"]
            stage = (u.get("Stage") if u.get("StageMode") == "Fixed"
                     else (F.plans.get(u["StageMode"], {}).get("ALL") or {}).get("Stage"))
            if stage in (None, "-"):
                return {"ok": True, "lines": []}
            line = ('2026-09-28 06:03:52.000 | DEBUG | asst::Assistant::append_task Fight '
                    '{"stage":"%s","medicine":0,"medicine_expire_days":2,"stone":0,'
                    '"times":2147483647,"series":0,"DrGrandet":false,"server":"CN",'
                    '"client_type":"Official"}' % stage)
            return {"ok": True, "lines": [line]}
        return {"ok": False, "error": "unexpected path %s" % path}

    def fake_tail(path, n=300, contains=None, timeout=120):
        return fake_read(path)

    def fake_probe(task_uid, dirs, event_lines=4000):
        return {"ended": probes["ended"], "end_line": ("任务结束: %s" % task_uid) if probes["ended"] else "",
                "started_seen": True, "maa_in_dir": probes["maa_in_dir"], "maa_dirs": [], "tail_lines": 1}

    def reset(profile=None, **kw):
        F.reset()
        window.update({"uid": None, "delay": 0, "frozen_mtime": None})
        probes.update({"ended": False, "maa_in_dir": True})
        mt["n"] = 0
        pf_seen.clear()
        _FAKE_FORCE.clear()
        globals()["post"] = F.post
        globals()["reachable"] = lambda: (True, "code=200")
        globals()["run_preflight_check"] = pf_go
        globals()["host_probe"] = fake_probe
        globals()["read_host_file"] = fake_read
        globals()["read_host_tail"] = fake_tail
        globals()["NO_GO"] = False
        for f in ("state.json", "run_profile_log.jsonl", "ledger.jsonl", "lock",
                  os.path.join("lease", "instance_leases.json"), os.path.join("lease", "instance_leases.lock")):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(tmp, f))
        path = os.path.join(tmp, "profile.json")
        if profile is not None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(profile, f, ensure_ascii=False)
        return path

    def NS(**kw):
        d = {"profile": os.path.join(tmp, "profile.json"), "apply": False, "dry_run": False, "json": True,
             "account": None, "all_accounts": False, "disable_others": False, "write_mode": "plan",
             "preflight": False, "readback_timeout": 1, "poll_seconds": 0, "max_wait_seconds": 1,
             "wait_poll_seconds": 0, "stop_on_timeout": True, "restore_wait_seconds": 0,
             "restore_attempts": 2, "restore_gap_seconds": 0, "strict_l2": False,
             "lease_owner": None,
             "status": False, "list_plans": False, "reconcile": False, "restore_run": None,
             "selftest": False}
        d.update(kw)
        return argparse.Namespace(**d)

    P1 = {"target": {"script": "脚本单号", "account": "单号甲"}, "stage_plan": ["1-7"], "series": 6,
          "medicine": {"use": True, "count": 2, "use_expiring": True},
          "activity_priority": {"enabled": False, "medicine": 0},
          "target_drop": {"enabled": False, "drop_id": "", "count": 0},
          "keep_remain_sanity_task": True, "restore_after": "run_end"}

    def writes_of(kind):
        return [b for p, b in F.calls if p == kind]

    # ---- 校验红灯 ----------------------------------------------------------
    def c_validate_total_times():
        _, w, e = validate_profile({"target": {"script": "x"}, "total_times": 5})
        ck(any("总次数" in x for x in e), "total_times 应被拒：%r" % e)

    def c_validate_drop():
        _, w, e = validate_profile({"target": {"script": "x"},
                                    "target_drop": {"enabled": True, "drop_id": "30012", "count": 5}})
        ck(any("目标掉落" in x for x in e), "target_drop.enabled 应被拒：%r" % e)

    def c_validate_ok():
        norm, w, e = validate_profile(P1)
        ck(not e, "P1 应通过：%r" % e)
        ck(any("use_expiring" in x for x in w), "应告警 use_expiring 不可写：%r" % w)

    def c_validate_series_bad():
        _, _, e = validate_profile({"target": {"script": "x"}, "series": 9})
        ck(any("series" in x for x in e), "series=9 应被拒：%r" % e)

    # ---- dry-run 零写 ------------------------------------------------------
    def c_dry_run():
        p = reset(P1)
        rc = cmd_run(NS(profile=p))
        ck(rc == 0, "dry-run 应 0，实得 %s" % rc)
        ck(not F.calls or all(c[0] in ("/api/scripts/get", "/api/scripts/user/get", "/api/plan/get")
                              for c in F.calls), "dry-run 只能读：%r" % F.calls)

    def c_multi_account_needs_explicit():
        p = reset({"target": {"script": "脚本多号"}})
        rc = cmd_run(NS(profile=p))
        ck(rc == 2, "多号脚本无显式选择应拒：%s" % rc)

    def c_multi_account_explicit_disabled_account():
        p = reset({"target": {"script": "脚本多号", "account": "停用丁"}})
        rc = cmd_run(NS(profile=p))
        ck(rc == 2, "未启用账号应拒：%s" % rc)

    def c_multi_account_disable_others_apply():
        p = reset({"target": {"script": "脚本多号", "account": "多号乙"}, "stage_plan": ["1-7"]})
        window["uid"] = "u2"
        probes["ended"] = True
        rc = cmd_run(NS(profile=p, apply=True, account="多号乙", disable_others=True))
        ck(rc == 0, "单号+停用其它号应成功：%s" % rc)
        # 多号丙被临时停用又还原
        ck(F.users["sid-2"]["u3"]["Info"]["Status"] is True, "多号丙 Status 应还原为 True")
        # 临时计划表已删
        ck(not [x for x in F.plans if str((x or "")).startswith("pl-new-")],
           "临时计划表应已删除：%r" % list(F.plans))
        # StageMode 还原
        ck(str(F.users["sid-2"]["u2"]["Info"]["StageMode"]) == "pl-old", "StageMode 应还原为 pl-old")
        st = load_state()
        ck(list(st["runs"].values())[0]["status"] == "restored", "state 应 restored：%r" % st)

    # ---- 计划表全量复制 + 差分 --------------------------------------------
    def c_plan_full_copy_diff():
        p = reset(P1)
        plan = build_plan(NS(profile=p))
        ck(not plan["refusals"], "计划应成立：%r" % plan["refusals"])
        exp = plan["expected"]["u1"]
        ck(exp["payload"]["Stage"] == "1-7", "Stage 应 1-7：%r" % exp["payload"])
        ck(exp["payload"]["Stage_1"] == "-", "未请求候选位应清成 -（stage_plan 全量语义）：%r" % exp["payload"])
        ck(exp["payload"]["SeriesNumb"] == "6", "SeriesNumb 应 6：%r" % exp["payload"])
        ck(exp["payload"]["MedicineNumb"] == 2, "MedicineNumb 应 2：%r" % exp["payload"])
        ck(exp["payload"]["Stage_Remain"] == "1-7", "未请求键 Stage_Remain 必须**复制**过来：%r" % exp["payload"])
        ck(exp["stage_plan"] == ["1-7"] and exp["series"] == 6 and exp["medicine"] == 2
           and exp["remain"] is True, "L1 期望应与请求一致：%r" % exp)

    def c_plan_weekly_account():
        p = reset({"target": {"script": "脚本多号", "account": "多号乙"}, "stage_plan": ["CE-6"]})
        plan = build_plan(NS(profile=p, disable_others=True))
        exp = plan["expected"]["u2"]
        ck(exp["payload"]["Stage_Remain"] == "1-7", "Weekly 计划表基础值应被复制：%r" % exp["payload"])
        ck("计划表" in str(exp["base_source"]), "来源应是计划表：%r" % exp["base_source"])

    def c_keep_remain_false():
        p = reset({"target": {"script": "脚本单号"}, "keep_remain_sanity_task": False})
        plan = build_plan(NS(profile=p))
        exp = plan["expected"]["u1"]
        ck(exp["payload"]["Stage_Remain"] == "-" and exp["remain"] is False,
           "keep_remain=false ⇒ Stage_Remain='-' 且不期望剩余理智条目：%r" % exp)

    # ---- apply 全链路 ------------------------------------------------------
    def c_apply_success():
        p = reset(P1)
        window["uid"] = "u1"
        probes["ended"] = True
        rc = cmd_run(NS(profile=p, apply=True))
        ck(rc == 0, "apply 应成功：%s" % rc)
        ck(len(writes_of("/api/plan/add")) == 1, "应建 1 张临时计划表：%r" % F.calls)
        ck(writes_of("/api/dispatch/start"), "应发车")
        lg = [json.loads(x) for x in open(globals()["LOG"], encoding="utf-8") if x.strip()]
        ck(any(x["event"] == "readback" or x.get("event") == "restore" for x in lg), "审计缺事件：%r" % lg)
        led = [json.loads(x) for x in open(globals()["LEDGER"], encoding="utf-8") if x.strip()]
        ck(any(x["kind"] == "run_profile" and x["ok"] for x in led), "台账应记 run_profile ok：%r" % led)
        # 备份文件
        run = list(load_state()["runs"].values())[0]
        for f in ("meta.json", "plans_before.json", "accounts_before.json", "expected.json", "hashes.json"):
            ck(os.path.exists(os.path.join(run["backup_dir"], f)), "备份缺 %s" % f)
        blob = open(os.path.join(run["backup_dir"], "accounts_before.json"), encoding="utf-8").read()
        ck("Password" not in blob and "SklandToken" not in blob, "备份不得带凭据键")

    def c_apply_readback_mismatch_stops():
        p = reset(P1)
        window["uid"] = None                       # gui.new.json 永远读不到 ⇒ 读回超时
        rc = cmd_run(NS(profile=p, apply=True))
        ck(rc == 3, "读回不符应 3，实得 %s" % rc)
        ck(writes_of("/api/dispatch/stop"), "读回不符必须 stop 止损：%r" % F.calls)
        run = list(load_state()["runs"].values())[0]
        ck(run["status"] == "restored", "应已还原：%r" % run["status"])

    def c_apply_readback_wrong_values():
        p = reset(P1)
        # 窗口里给的是"另一个号"的配置（同脚本但值不同）⇒ 不匹配 ⇒ 止损
        F.users["sid-1"]["u1"]["Info"]["Stage"] = "SR-7"
        rc = cmd_run(NS(profile=p, apply=True, account="单号甲", disable_others=False))
        ck(rc in (3, 2), "值不符应失败：%s" % rc)
        ck(writes_of("/api/dispatch/stop"), "应止损：%r" % F.calls)

    def c_apply_preflight_nogo_rollback():
        p = reset(P1)
        globals()["run_preflight_check"] = pf_nogo
        rc = cmd_run(NS(profile=p, apply=True))
        ck(rc == 2, "preflight NO-GO 应 2：%s" % rc)
        ck(not writes_of("/api/dispatch/start"), "preflight 不过不得发车：%r" % F.calls)
        ck(str(F.users["sid-1"]["u1"]["Info"]["StageMode"]) == "Fixed", "StageMode 应还原")
        ck(not [x for x in F.plans if x.startswith("pl-new-")], "临时计划表应删：%r" % list(F.plans))
        run = list(load_state()["runs"].values())[0]
        ck(run["status"] == "restored", "应 restored：%r" % run["status"])

    def c_apply_timeout_stop_and_restore():
        p = reset(P1)
        window["uid"] = "u1"
        probes["ended"] = False
        probes["maa_in_dir"] = True
        rc = cmd_run(NS(profile=p, apply=True))
        ck(rc == 3, "超时应 3：%s" % rc)
        ck(writes_of("/api/dispatch/stop"), "超时应 stop：%r" % F.calls)
        run = list(load_state()["runs"].values())[0]
        ck(run["wait"]["timeout"] is True and run["status"] == "restored", "应超时并还原：%r" % run["wait"])

    def c_apply_run_end_ok():
        p = reset(P1)
        window["uid"] = "u1"
        probes["ended"] = True
        rc = cmd_run(NS(profile=p, apply=True))
        ck(rc == 0, "任务自然结束应成功：%s" % rc)
        ck(not writes_of("/api/dispatch/stop"), "自然结束不应 stop：%r" % F.calls)

    def c_strict_l2_mismatch():
        p = reset(P1)
        window["uid"] = "u1"
        probes["ended"] = True
        F.users["sid-1"]["u1"]["Info"]["Stage"] = "SR-8"      # L2 会报 stage 不符
        rc = cmd_run(NS(profile=p, apply=True, strict_l2=True))
        ck(rc == 3, "strict-l2 不符应 3：%s" % rc)
        ck(writes_of("/api/dispatch/stop"), "strict-l2 不符应 stop：%r" % F.calls)

    def c_write_mode_fixed():
        p = reset(P1)
        window["uid"] = "u1"
        probes["ended"] = True
        rc = cmd_run(NS(profile=p, apply=True, write_mode="fixed"))
        ck(rc == 0, "fixed 路线应成功：%s" % rc)
        ck(not writes_of("/api/plan/add"), "fixed 路线不应建计划表：%r" % F.calls)
        ck(str(F.users["sid-1"]["u1"]["Info"]["Stage"]) == "SR-7", "7 键应还原")
        ck(str(F.users["sid-1"]["u1"]["Info"]["StageMode"]) == "Fixed", "StageMode 不动")

    def c_write_mode_fixed_needs_fixed():
        p = reset({"target": {"script": "脚本多号", "account": "多号乙"}})
        rc = cmd_run(NS(profile=p, apply=False, write_mode="fixed"))
        ck(rc == 2, "计划表账号 + fixed 路线应拒：%s" % rc)

    def c_lock_busy():
        p = reset(P1)
        open(globals()["LOCK"], "w").close()
        rc = cmd_run(NS(profile=p, apply=True))
        ck(rc == 2, "锁被占应拒：%s" % rc)
        with contextlib.suppress(OSError):
            os.remove(globals()["LOCK"])

    def c_no_go():
        p = reset(P1)
        globals()["NO_GO"] = True
        rc = cmd_run(NS(profile=p, apply=True))
        ck(rc == 2, "NO_GO 应拒：%s" % rc)
        ck(not writes_of("/api/plan/add"), "NO_GO 不得写：%r" % F.calls)

    def c_dispatch_refused():
        p = reset(P1)
        F.fail_start = {"code": 500, "status": "error", "message": "RuntimeError: 任务 脚本单号 已在运行",
                        "taskId": ""}
        rc = cmd_run(NS(profile=p, apply=True))
        ck(rc == 2, "发车被拒应 2：%s" % rc)
        ck(str(F.users["sid-1"]["u1"]["Info"]["StageMode"]) == "Fixed", "应还原")

    def c_reconcile_and_restore_run():
        p = reset(P1)
        window["uid"] = "u1"
        probes["ended"] = True
        ck(cmd_run(NS(profile=p, apply=True)) == 0, "先跑一车")
        # 人为把状态改回 open（模拟崩溃后补）
        st = load_state()
        rid = list(st["runs"])[0]
        st["runs"][rid]["status"] = "dispatched"
        st["runs"][rid]["restored_at"] = None
        save_state(st)
        F.users["sid-1"]["u1"]["Info"]["StageMode"] = "pl-new-1"       # 再造一点漂移
        rc = cmd_reconcile(NS(reconcile=True, apply=True))
        ck(rc == 0, "对账应还原成功：%s" % rc)
        ck(str(F.users["sid-1"]["u1"]["Info"]["StageMode"]) == "Fixed", "StageMode 应还原")

    def c_status_and_list_plans():
        reset(P1)
        ck(cmd_status(NS(status=True)) == 0, "status 应 0")
        ck(cmd_list_plans(NS(list_plans=True)) == 0, "list-plans 应 0")

    def c_readback_stale_file_rejected():
        """时效门：文件 mtime 早于发车前快照 ⇒ 旧快照不得算通过（§6-17）。"""
        p = reset(P1)
        window["uid"] = "u1"
        window["frozen_mtime"] = "2000-01-01 00:00:00.000"
        probes["ended"] = True
        rc = cmd_run(NS(profile=p, apply=True))
        ck(rc == 3, "旧文件应判读回不符：%s" % rc)
        ck(writes_of("/api/dispatch/stop"), "应止损 stop：%r" % F.calls)

    def c_simple_flags_asserted():
        """简洁号：EnableTimesLimit/EnableTargetDrop 非 False ⇒ 判不符（§5 期望值表）。"""
        p = reset(P1)
        window["uid"] = "u1"
        _FAKE_FORCE["times_limit"] = True
        probes["ended"] = True
        rc = cmd_run(NS(profile=p, apply=True))
        run = list(load_state()["runs"].values())[0]
        ck(rc == 3, "简洁号 EnableTimesLimit=True 应判不符：%s / strict_simple=%r / mode=%r / readback=%r"
           % (rc, (run.get("expected") or {}).get("u1", {}).get("strict_simple"),
              F.users["sid-1"]["u1"]["Info"].get("Mode"),
              json.dumps(run.get("readback"), ensure_ascii=False)[:300]))
        ck(writes_of("/api/dispatch/stop"), "应止损：%r" % F.calls)

    def c_detailed_mode_tolerates_flags():
        """详细号：同样的 EnableTimesLimit=True 只是信息（详细沿用 ConfigFile）⇒ 仍应成功。"""
        p = reset(P1)
        window["uid"] = "u1"
        F.users["sid-1"]["u1"]["Info"]["Mode"] = "详细"
        _FAKE_FORCE["times_limit"] = True
        probes["ended"] = True
        rc = cmd_run(NS(profile=p, apply=True))
        ck(rc == 0, "详细号不应因该键判错：%s" % rc)

    def c_real_mask_attribution():
        """多号同值域时靠 StartUp.AccountName 掩码归属（§6-7 按当前号读）。"""
        p = reset({"target": {"script": "脚本多号", "account": "多号丙"}, "stage_plan": ["1-7"]})
        window["uid"] = "u3"
        probes["ended"] = True
        rc = cmd_run(NS(profile=p, apply=True, account="多号丙", disable_others=True))
        ck(rc == 0, "掩码归属应成功：%s" % rc)
        run = list(load_state()["runs"].values())[0]
        ck("u3" in (run.get("readback") or {}).get("satisfied", {}), "u3 应有读回证据：%r" % run.get("readback"))

    def c_dry_run_flag_explicit_and_conflict():
        """P1-1 契约：`--dry-run` 是显式无操作开关（rc=0 且输出与缺省 dry-run 一致）；
        `--apply --dry-run` 组合 = 参数冲突（rc=2，拒于任何 IO 之前，不猜意图）。"""
        p = reset(P1)

        def run_cli(argv):
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                rc = main(argv)
            return rc, buf.getvalue(), err.getvalue()

        def norm(text):      # 抹掉每次必然不同的字：generated_at 与计划表名里的分钟戳
            text = re.sub(r'"generated_at": "[^"]*"', '"generated_at": "<ts>"', text)
            return re.sub(r"\d{8}_\d{4}", "<stamp>", text)

        rc0, out0, _ = run_cli(["--profile", p, "--json"])
        rc1, out1, _ = run_cli(["--profile", p, "--dry-run", "--json"])
        ck(rc0 == 0 and rc1 == 0, "缺省与显式 --dry-run 都应 rc=0：%s / %s" % (rc0, rc1))
        ck(norm(out0) == norm(out1), "显式 --dry-run 的输出必须与缺省 dry-run 完全一致")
        n_before = len(F.calls)
        rc2, _, err2 = run_cli(["--profile", p, "--apply", "--dry-run"])
        ck(rc2 == 2, "--apply --dry-run 应判参数冲突 rc=2：%s" % rc2)
        ck("参数冲突" in err2, "冲突拒绝文案应点名「参数冲突」：%r" % err2)
        ck(len(F.calls) == n_before, "冲突组合必须拒于任何 IO 之前（新增 REST：%r）" % (F.calls[n_before:],))

    # ---- 实例租约（G3L，P1-2 跨族单飞）----------------------------------------
    def c_lease_busy_refused_zero_write():
        """别人占着实例：dry-run / apply **同口径拒发** rc=2 且零写（不备份/不写配置/不发车）；
        拒绝文案带持有者；释放后同一实例立刻回到原路径。"""
        LG = _lease_glue()
        p = reset(P1)
        pre = LG.acquire_all(["13"], "t_lease_holder", "别人的活（selftest）", ttl_s=300, src="selftest")
        ck(pre.get("ok"), "前置：替身应先拿到租约：%r" % pre)
        try:
            n_calls_dry = len(F.calls)
            bdir = os.path.join(tmp, "backups")
            b_before = sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []
            rc_dry = cmd_run(NS(profile=p))
            ck(rc_dry == 2, "dry-run 在租约被占时也必须 NO-GO（rc=2）：%s" % rc_dry)
            plan = build_plan(NS(profile=p))
            ck(plan["lease"]["ok"] is False, "dry-run 计划应看到租约被占：%r" % plan.get("lease"))
            ck(any(r.get("reason") == "lease_busy" for r in plan["refusals"]),
               "dry-run 计划应是拒绝态（lease_busy）：%r" % plan.get("refusals"))
            ck(any("t_lease_holder" in (r.get("detail") or "") for r in plan["refusals"]),
               "拒绝文案应带持有者：%r" % plan.get("refusals"))
            ck(any("到期" in (r.get("detail") or "") for r in plan["refusals"]),
               "拒绝文案应带到期时间：%r" % plan.get("refusals"))
            writes_dry = [c for c in F.calls[n_calls_dry:]
                          if c[0] in ("/api/plan/add", "/api/plan/update", "/api/plan/delete", "/api/queue/add",
                                      "/api/scripts/user/update", "/api/dispatch/start", "/api/dispatch/stop")]
            ck(not writes_dry, "dry-run 拒绝路径必须零写（配置/发车）：%r" % writes_dry)
            b_after_dry = sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []
            ck(b_after_dry == b_before, "dry-run 拒绝路径不该落备份：%r → %r" % (b_before, b_after_dry))
            n_calls = len(F.calls)
            rc = cmd_run(NS(profile=p, apply=True, lease_owner="t_rp_selftest"))
            ck(rc == 2, "租约被占 ⇒ apply 必须拒发 rc=2：%s" % rc)
            writes = [c for c in F.calls[n_calls:]
                      if c[0] in ("/api/plan/add", "/api/plan/update", "/api/plan/delete", "/api/queue/add",
                                  "/api/scripts/user/update", "/api/dispatch/start", "/api/dispatch/stop")]
            ck(not writes, "拒绝路径必须零写（配置/发车）：%r" % writes)
            b_after = sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []
            ck(b_after == b_before, "拒绝路径不该落备份：%r → %r" % (b_before, b_after))
            ck(not (load_state().get("runs") or {}), "拒绝路径不该写运行台账（零残留）")
            lg = [json.loads(x) for x in open(globals()["LOG"], encoding="utf-8") if x.strip()]
            ck(any(x.get("reason") == "lease_busy" for x in lg), "审计应记 lease_busy：%r" % lg[-3:])
            ck(any(x.get("reason") == "lease_busy" and x.get("apply") is False for x in lg),
               "dry-run 拒绝也该留审计（apply=False）：%r" % lg[-4:])
            led = [json.loads(x) for x in open(globals()["LEDGER"], encoding="utf-8") if x.strip()]
            ck(any(x.get("reason") == "lease_busy" for x in led), "全局台账应记拒绝：%r" % led)
            st_now = lease_state()
            ck("13" in (st_now.get("leases") or {}) or any(
                str((v or {}).get("idx")) == "13" for v in (st_now.get("leases") or {}).values()),
               "拒发不该动持有者的租约：%r" % st_now.get("leases"))
        finally:
            LG.release_all(["13"], owner_id="t_lease_holder")
        # 释放后同一实例立刻能走原路径（空闲时正常走原路径）
        ck(cmd_run(NS(profile=p)) == 0, "释放后 dry-run 应回到原路径 rc=0")
        window["uid"] = "u1"
        probes["ended"] = True
        ck(cmd_run(NS(profile=p, apply=True, lease_owner="t_rp_selftest")) == 0, "空窗后应能正常发车")

    def c_lease_apply_lifecycle():
        """apply 全链路：acquire → 心跳 renew → 还原后 release；持有者透给 G4；运行台账留痕。"""
        LG = _lease_glue()
        p = reset(P1)
        window["uid"] = "u1"
        probes["ended"] = True
        rc = cmd_run(NS(profile=p, apply=True, lease_owner="t_rp_selftest"))
        ck(rc == 0, "apply 应成功：%s" % rc)
        ck(pf_seen.get("lease_owner") == "t_rp_selftest",
           "G4 preflight 应带上自己的持有者（否则第 3L 项自锁）：%r" % pf_seen)
        st = lease_state()
        ck(not st["leases"], "跑完必须释放租约：%r" % st["leases"])
        ck(any(h.get("ended_reason") == "released" for h in st["history"]), "历史应有 released：%r" % st["history"])
        run = list(load_state()["runs"].values())[0]
        ck(((run.get("lease") or {}).get("owner")) == "t_rp_selftest", "运行台账应记租约：%r" % run.get("lease"))
        ck(((run.get("lease") or {}).get("idxs")) == ["13"], "租约应认到脚本实例号：%r" % run.get("lease"))
        ck((run.get("lease_release") or {}).get("ok"), "运行台账应记释放：%r" % run.get("lease_release"))

    def c_lease_released_on_failure():
        """失败路径（preflight NO-GO 回滚）也必须释放租约——别把实例锁死一整个 TTL。"""
        LG = _lease_glue()
        p = reset(P1)
        window["uid"] = "u1"
        globals()["run_preflight_check"] = pf_nogo
        rc = cmd_run(NS(profile=p, apply=True, lease_owner="t_rp_fail"))
        ck(rc == 2, "preflight NO-GO 应 rc=2：%s" % rc)
        ck(pf_seen.get("lease_owner") == "t_rp_fail", "失败路径也该带持有者：%r" % pf_seen)
        st = lease_state()
        ck(not st["leases"], "失败路径也必须释放：%r" % st["leases"])
        ck(any(h.get("owner") == "t_rp_fail" and h.get("ended_reason") == "released" for h in st["history"]),
           "历史应留 released 痕迹：%r" % st["history"])

    def c_lease_beat_semantics():
        """心跳续租语义：首次 renew / 节流 / 过节点再续；非持有者续租失败不抛；没租约跳过。"""
        LG = _lease_glue()
        reset(P1)
        acq = LG.acquire_all(["13"], "t_beat", "beat 单测", ttl_s=120, src="selftest")
        ck(acq.get("ok"), "前置取租失败：%r" % acq)
        try:
            lease = {"ok": True, "idxs": ["13"], "owner": "t_beat"}
            beat = {"last": 0.0}
            r1 = lease_beat(lease, beat)
            ck(r1.get("status") == "renewed", "首次心跳应续租：%r" % r1)
            ck((r1.get("renewed") or [{}])[0].get("expires_at"), "续租应带新到期时间：%r" % r1)
            ck(lease_beat(lease, beat).get("status") == "throttled", "紧接着应节流")
            beat["last"] = time.time() - LEASE_BEAT_MIN_S - 1
            ck(lease_beat(lease, beat).get("status") == "renewed", "过节点后应再续")
            bad = lease_beat({"ok": True, "idxs": ["13"], "owner": "t_not_owner"}, {"last": 0.0})
            ck(bad.get("ok") is False, "非持有者续租应失败（不抛）：%r" % bad)
            ck(lease_beat({"ok": False}, {"last": 0.0}).get("status") == "skipped", "没有租约应跳过")
        finally:
            LG.release_all(["13"], owner_id="t_beat")

    def c_lease_no_emu_skips():
        """脚本没有 Emulator.Index：没有可协调的实例 ⇒ 跳过租约（不当闸门），照原路径走。"""
        LG = _lease_glue()
        p = reset(P1)
        window["uid"] = "u1"
        probes["ended"] = True
        F.scripts["sid-1"]["Emulator"] = {}          # 目标实例号缺失
        plan = build_plan(NS(profile=p))
        ck(plan["lease"]["status"] == "no_target", "应判定为无可协调实例：%r" % plan.get("lease"))
        ck(plan["lease"]["ok"] is True, "no_target 不算被占：%r" % plan["lease"])
        ck(cmd_run(NS(profile=p, apply=True, lease_owner="t_rp_selftest")) == 0, "无实例号应照跑（跳过租约）")
        st = lease_state()
        ck(not st["leases"], "不该留下租约：%r" % st["leases"])

    def c_lease_preflight_env_passthrough():
        """`run_preflight_check` 把持有者透给子进程 env（MAAORCH_LEASE_OWNER）——G4 自锁的另一半保险。"""
        saved_owner = os.environ.pop("MAAORCH_LEASE_OWNER", None)
        real = subprocess
        seen = []

        class _R:
            returncode = 0
            stdout = b'{"preflight": "GO", "failing_checks": [], "reasons": []}'
            stderr = b""

        class _S:
            DEVNULL = subprocess.DEVNULL
            TimeoutExpired = subprocess.TimeoutExpired

            @staticmethod
            def run(argv, **kw):
                seen.append({"argv": list(argv), "env": dict(kw.get("env") or {})})
                return _R()

        globals()["subprocess"] = _S
        try:
            r1 = saved_fns["run_preflight_check"]("脚本单号", lease_owner="t_rp_x")
            saved_fns["run_preflight_check"]("脚本单号")
        finally:
            globals()["subprocess"] = real
            if saved_owner is not None:
                os.environ["MAAORCH_LEASE_OWNER"] = saved_owner
        ck(r1["verdict"] == "GO", "应解析出 GO：%r" % r1)
        ck("--script" in seen[0]["argv"] and str(PREFLIGHT) in seen[0]["argv"], "argv 应是 preflight 调用：%r" % seen[0]["argv"])
        ck(seen[0]["env"].get("MAAORCH_LEASE_OWNER") == "t_rp_x", "应注入持有者 env：%r" % seen[0]["env"])
        ck("MAAORCH_LEASE_OWNER" not in seen[1]["env"], "没给持有者就不该注入：%r" % seen[1]["env"])

    add("validate_total_times_refused", c_validate_total_times)
    add("validate_target_drop_refused", c_validate_drop)
    add("validate_ok_and_warning", c_validate_ok)
    add("validate_series_bad", c_validate_series_bad)
    add("dry_run_zero_write", c_dry_run)
    add("multi_account_needs_explicit", c_multi_account_needs_explicit)
    add("disabled_account_refused", c_multi_account_explicit_disabled_account)
    add("plan_full_copy_diff", c_plan_full_copy_diff)
    add("plan_weekly_base_copy", c_plan_weekly_account)
    add("keep_remain_false_semantics", c_keep_remain_false)
    add("apply_success_full_chain", c_apply_success)
    add("readback_mismatch_stops", c_apply_readback_mismatch_stops)
    add("readback_wrong_values_stops", c_apply_readback_wrong_values)
    add("preflight_nogo_rollback", c_apply_preflight_nogo_rollback)
    add("timeout_stop_and_restore", c_apply_timeout_stop_and_restore)
    add("run_end_success", c_apply_run_end_ok)
    add("strict_l2_mismatch", c_strict_l2_mismatch)
    add("write_mode_fixed_route", c_write_mode_fixed)
    add("write_mode_fixed_needs_fixed_account", c_write_mode_fixed_needs_fixed)
    add("multi_account_disable_others", c_multi_account_disable_others_apply)
    add("lock_busy_refused", c_lock_busy)
    add("no_go_refused", c_no_go)
    add("dispatch_refused_rollback", c_dispatch_refused)
    add("reconcile_restores_run", c_reconcile_and_restore_run)
    add("status_and_list_plans", c_status_and_list_plans)
    add("readback_stale_file_rejected", c_readback_stale_file_rejected)
    add("simple_flags_asserted", c_simple_flags_asserted)
    add("detailed_mode_tolerates_flags", c_detailed_mode_tolerates_flags)
    add("mask_attribution", c_real_mask_attribution)
    add("dry_run_flag_explicit_and_conflict", c_dry_run_flag_explicit_and_conflict)
    add("lease_busy_refused_zero_write", c_lease_busy_refused_zero_write)
    add("lease_apply_lifecycle", c_lease_apply_lifecycle)
    add("lease_released_on_failure", c_lease_released_on_failure)
    add("lease_beat_semantics", c_lease_beat_semantics)
    add("lease_no_emu_skips", c_lease_no_emu_skips)
    add("lease_preflight_env_passthrough", c_lease_preflight_env_passthrough)

    if lease_env_saved is None:
        os.environ.pop("MAAORCH_LEASE_RUNTIME", None)
    else:
        os.environ["MAAORCH_LEASE_RUNTIME"] = lease_env_saved
    _lease_glue().set_runtime(None)                  # 收回隔离：进程内恢复默认账本路径
    globals().update(saved)
    globals().update(saved_fns)
    shutil.rmtree(tmp, ignore_errors=True)
    failed = [c for c in cases if not c["ok"]]
    return {"ok": not failed, "passed": len(cases) - len(failed), "failed": len(failed), "cases": cases}


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="按 profile 一次发车（AUTO-MAS）：计划表/Series/吃药/活动优先 + 读回校验 + 自动还原")
    ap.add_argument("--profile", help="profile JSON 路径（见 runtime/templates/run_profile.example.json）")
    ap.add_argument("--apply", action="store_true", help="真执行（缺省 dry-run：只打印将做的改动与接口）")
    ap.add_argument("--dry-run", action="store_true",
                    help="显式声明 dry-run（工具缺省即 dry-run，本开关为无操作；与 --apply 同给 ⇒ rc=2 参数冲突）")
    ap.add_argument("--account", help="只跑这个号（覆盖 profile.target.account；多号脚本还需 --disable-others）")
    ap.add_argument("--all-accounts", action="store_true", help="接受整脚本跑（多号脚本的显式确认）")
    ap.add_argument("--disable-others", action="store_true",
                    help="单号发车时临时停用同脚本其它启用号（Status=False，工具快照/还原）")
    ap.add_argument("--write-mode", choices=["plan", "fixed"], default="plan",
                    help="plan=新建临时计划表（默认，账号自己的值不动）；fixed=只对 Fixed 账号直写自己的 7 键")
    ap.add_argument("--preflight", action="store_true", help="dry-run 时也跑一次 preflight（只读，较慢）")
    ap.add_argument("--readback-timeout", type=float, default=300, help="读回校验超时秒（默认 300）")
    ap.add_argument("--poll-seconds", type=float, default=15, help="读回轮询间隔（默认 15s）")
    ap.add_argument("--max-wait-seconds", type=float, default=10800,
                    help="restore_after=run_end 时的等待上限（默认 10800s=3h）")
    ap.add_argument("--wait-poll-seconds", type=float, default=20, help="等任务轮询间隔（默认 20s）")
    ap.add_argument("--no-stop-on-timeout", dest="stop_on_timeout", action="store_false",
                    help="超时不 stop（默认超时用 /api/dispatch/stop 止损再还原）")
    ap.add_argument("--restore-wait-seconds", type=float, default=180, help="超时 stop 后等解锁上限（默认 180s）")
    ap.add_argument("--restore-attempts", type=int, default=12, help="还原重试次数（默认 12）")
    ap.add_argument("--restore-gap-seconds", type=float, default=30, help="还原重试间隔（默认 30s）")
    ap.add_argument("--strict-l2", action="store_true", help="L2（asst.log append_task）不符也止损（默认仅告警）")
    ap.add_argument("--lease-owner", help="实例租约持有者（G3L；缺省 MAAORCH_LEASE_OWNER → HERMES_KANBAN_TASK → cli:run_profile）")
    ap.add_argument("--status", action="store_true", help="列出运行台账")
    ap.add_argument("--reconcile", action="store_true", help="对账：还原已确认结束的 open 记录")
    ap.add_argument("--restore-run", metavar="RUN_ID", help="手动还原某次运行")
    ap.add_argument("--list-plans", action="store_true", help="只读：计划表 + 遗留「临时-按需-*」")
    ap.add_argument("--selftest", action="store_true", help="离线自测（假传输，不碰网络/runtime）")
    ap.add_argument("--json", action="store_true", help="单行/结构化 JSON 输出")
    a = ap.parse_args(argv)
    if a.apply and a.dry_run:                       # 组合语义显式定义（P1-1）：不猜意图，直接参数冲突
        print("拒绝：参数冲突 —— --apply 与 --dry-run 互斥（本工具缺省即 dry-run；预演只给 --dry-run，"
              "真执行只给 --apply）", file=sys.stderr)
        return 2

    if a.selftest:
        rep = selftest()
        if a.json:
            print(json.dumps(rep, ensure_ascii=False))
        else:
            print("SELFTEST=%s (%d/%d)" % ("OK" if rep["ok"] else "FAIL", rep["passed"],
                                           rep["passed"] + rep["failed"]))
            for c in rep["cases"]:
                print("  %s %s%s" % ("PASS" if c["ok"] else "FAIL", c["name"],
                                     ("" if c["ok"] else "  ← " + c["detail"])))
        return 0 if rep["ok"] else 3
    if a.status:
        return cmd_status(a)
    if a.list_plans:
        return cmd_list_plans(a)
    if a.reconcile:
        return cmd_reconcile(a)
    if a.restore_run:
        return cmd_restore_run(a)
    if not a.profile:
        ap.print_help()
        return 2
    try:
        return cmd_run(a)
    except urllib.error.URLError as e:
        print("上游不可达：%s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
