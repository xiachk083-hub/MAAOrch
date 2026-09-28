#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""mas_scenario_dispatch.py —— 按场景发车（肉鸽一局 / 活动一轮 / 剿灭）：**骨架 + dry-run 默认**。

写给未来的 Hermes：把「按场景发一局肉鸽/一轮活动刷取」变成一条受控命令。
真发车需要用户批准 —— 草案阶段**双闸默认关闭**：`--apply` ＋ 环境变量
`MAAORCH_SCENARIO_APPLY=1` 同时给足才会触达任何写请求；缺一即拒绝并打印完整计划。

## 用法（全部默认 dry-run）

    python tools/mas_scenario_dispatch.py --list-scripts                 # 只读：脚本/实例/用户表
    python tools/mas_scenario_dispatch.py --script "明日方舟-官-1-1-7" \
        --scenario rogue --times 1 --window "21:30-22:30"                # dry-run：打印计划 + 闸门
    python tools/mas_scenario_dispatch.py ... --apply                    # 真发车（草案阶段会被双闸拦下）
    python tools/mas_scenario_dispatch.py --reconcile                    # 对账：到期该还原谁（dry-run）
    python tools/mas_scenario_dispatch.py --reconcile --apply            # 真还原（同样双闸）
    python tools/mas_scenario_dispatch.py --restore-run <run_id>         # 手动还原某次运行
    python tools/mas_scenario_dispatch.py --status                       # 只读：open/restored 一览
    python tools/mas_scenario_dispatch.py --selftest                     # 纯本地自检（不连网）

## 写面（apply 时才会触达；逐条有出处）

三个写面 + 一个发车调用，全部先备份后写、写后回读校验：

1. **Task 开关**（AUTO-MAS 用户配置，`POST /api/scripts/user/update`，`data={"Task": {...}}`）
   —— 10 个 `If*` 键**显式全写**（不依赖默认值）；键集 = `tools/mas_startup_verify.py:126-128`
   实证。AutoProxy 用它们决定 Routine 轮各任务开不开（`app/task/MAA/AutoProxy.py:416-423`）。
2. **配置文件 TaskQueue 参数**（主机 `E:\AUTO-MAS\data\<sid>\<uid>\ConfigFile\gui.new.json`
   —— 标注「详细」的用户；标「简洁」的用户读 `data\<sid>\Default\ConfigFile\gui.new.json`）
   —— AutoProxy 在跑前把该目录整包拷进 MAA 配置目录，并以「同类型第一条任务」为底
   （`AutoProxy.py:555-565 / 613-627`）⇒ 在这里改肉鸽/活动参数才有效。
3. **Task 扩展字段**（如 `ActivityStageIndex`/`ActivityMedicineNumb`，同写面 1 的 API）
   —— 模板声明才写。
4. **发车**：`POST /api/dispatch/start {"taskId": <scriptId>, "mode": "AutoProxy"}`
   —— scriptId 口径 = 该脚本**全部启用用户**逐个跑（`docs/audits/2026-09-27_mas_task_modes.md` §1）；
   返回 `{taskId: <task_uid>}` 记入审计；中止用 `/api/dispatch/stop {taskId: <task_uid>}`（别 kill）。

## 闸门（真发车/真还原前全过，任一不过即拒绝且零上游写请求）

- **G1 输入**：脚本可解析（`--script` 或 `--instance`）、场景合法、窗口合法（可选 `--duration`）。
- **G2 模板**：`runtime/templates/maa/<场景>.json`（t_c60939cd 产物）存在且可解析；或 `--template` 指定。
- **G3 双闸**：`--apply` ＋ `MAAORCH_SCENARIO_APPLY=1`；另 `MAAORCH_SCENARIO_NO_GO=1` 一票否决。
- **G4 让位区**：时间窗与 **04:00–05:33 / 18:30–20:30** 不得相交（`tools/lib/rogue_plan.mjs`
  `inYieldZone()` 同口径；依据见 `docs/audits/20260924_1830_rogue_yieldzone_symmetry.md`）。
- **G5 preflight**：`tools/mas_preflight.py --script <名>`（t_cd11ca49 产物）须 `PREFLIGHT=GO`；
  产物未落地时 apply 默认拒绝（`--allow-no-preflight` 可显式放行，审计留痕）。
- **G6 现场只读**：AUTO-MAS 可达；目标脚本的 MAA 目录无 MAA.exe 在跑；无 open 运行。
- **G7 单飞**：同一脚本同日同场景只跑一次（重复须 `--force`；state 记录）。

## 自动还原（按时间窗）

- 每次运行把 `窗口结束` 记为还原时刻；`--reconcile --apply` 对「已到期」的 open 运行执行还原
  （Task 与 ConfigFile 回滚 + 回读校验；配置被锁时重试，等解锁）。
- 建议接线（待用户批准后做）：Hermes cron 每 15 分钟跑 `--reconcile --apply`（同环境变量）；
  本文档不含 cron 改动。

## 状态 / 审计 / 备份

    runtime/scenario_dispatch_state.json        运行台账（open / restored / failed）
    runtime/scenario_dispatch_log.jsonl         每次 plan/apply/dispatch/restore 一行
    runtime/scenario_dispatch/backups/<run_id>/ 运行前快照（Task + ConfigFile + meta）

**凭据红线**：只快照 `Task` 与白名单 Info 字段（Name/Status/Server/Mode/Id），
含 password/token/secret/cookie/credential 的键读入即弃、绝不落盘。

## 诚实清单（未验证项，见 docs/MAS_SCENARIO_DISPATCH.md §未验证）

- 发车链路**未实测**（草案只到 dry-run；`--apply` 双闸默认关闭）。
- `tools/mas_preflight.py`（t_cd11ca49）与模板库（t_c60939cd）落地前，apply 会被 G2/G5 拦。
- AutoProxy 会先跑「剿灭轮」（若该号本周未完成、`Info.Annihilation≠Close`）：这是上游行为，
  本工具不改变它，窗口与成本要把这轮算进去（`AutoProxy.py:299-318 / 410-418 / 736-756`）。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):          # Windows 控制台中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

ROOT = Path(__file__).resolve().parent.parent                     # D:\1-2\MAAOrch
RUNTIME = ROOT / "runtime"
STATE = RUNTIME / "scenario_dispatch_state.json"
LOG = RUNTIME / "scenario_dispatch_log.jsonl"
BACKUPS = RUNTIME / "scenario_dispatch" / "backups"
TEMPLATE_DIR = RUNTIME / "templates" / "maa"
PREFLIGHT = ROOT / "tools" / "mas_preflight.py"

API = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
SSH_KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")
AUTO_MAS_DATA = "E:\\AUTO-MAS\\data"

# ── 双闸 / 一票否决 ─────────────────────────────────────────────────────────
APPLY_ENV = "MAAORCH_SCENARIO_APPLY"       # =1/true/yes/on 才允真写（草案默认关）
NO_GO_ENV = "MAAORCH_SCENARIO_NO_GO"       # =1/true/yes/on 一票否决一切写

# ── 让位区（与 tools/lib/rogue_plan.mjs inYieldZone 同口径）─────────────────
YIELD_ZONES = [("04:00", "05:33"), ("18:30", "20:30")]

# ── Task 键（tools/mas_startup_verify.py:126-128 实证的 10 键；显式全写）─────
TASK_KEYS = ["IfStartUp", "IfDepotMaintain", "IfFight", "IfInfrast", "IfRecruit",
             "IfMall", "IfAward", "IfRoguelike", "IfReclamation", "IfActivityFirst"]

# ── 不注入字段（切号语义归 AUTO-MAS AutoProxy 注入策略 + tools/mas_switch_gate.py 闸门：
#   官 masked+true / 非官 empty+false。模板里的同名值一律跳过并记审计，避免静默改切号）──
ACCOUNT_FIELDS = ("AccountName", "AccountSwitchEnabled")

# ── 场景定义（内置兜底；模板可覆盖 switch / task_fields / times_path）────────
# 证据：MAA_TASKS 清单见 docs/MAA_ROGUELIKE_PLAYBOOK.md §3.3 引 utils/constants.py；
#       活动优先见 D:\MAS app/task/MAA/AutoProxy.py:595-604 / 199-228（IfActivityFirst +
#       ActivityStageIndex + ActivityMedicineNumb ⇒ 追加「活动关优先」任务）。
SCENARIOS = {
    "rogue": {
        "aliases": ["rogue", "肉鸽", "rl"],
        "title": "发一局肉鸽",
        "templates": ["roguelike.json"],
        "switch": {"IfStartUp": True, "IfRoguelike": True},
        "times_path": "TaskQueue.Roguelike.StartCount",   # 发一局=1（PLAYBOOK §2.3）
        "notes": "MAA_1..12 为 6.18.0 ⇒ 开局干员用 StartingOpers（PLAYBOOK §1.3）",
    },
    "event": {
        "aliases": ["event", "活动", "activity"],
        "title": "一轮活动刷取",
        "templates": ["activity_priority.json"],
        "switch": {"IfStartUp": True, "IfFight": True, "IfActivityFirst": True},
        "times_path": None,                                # 待模板声明（ActivityMedicineNumb 等）
        "notes": "活动关由 AUTO-MAS 的 stage 下拉解析（Task.ActivityStageIndex，默认 1）",
    },
    "annihilation": {
        "aliases": ["annihilation", "剿灭", "anni"],
        "title": "剿灭",
        "templates": ["annihilation.json"],
        "switch": {"IfStartUp": True, "IfFight": True},
        "times_path": None,                                # 剿灭轮由 AUTO-MAS 自行编排，本工具不控次数
        "notes": "AutoProxy 剿灭轮还会受 Info.Annihilation / AnnihilationCompletedWeek 影响（上游行为）",
    },
}

# ── API 白名单（写端点只允许这两个；annotation 给审计用）────────────────────
READ_ENDPOINTS = {"/api/scripts/get", "/api/scripts/user/get", "/api/queue/get",
                  "/api/info/version", "/api/info/combox/stage"}
WRITE_ENDPOINTS = {"/api/scripts/user/update", "/api/dispatch/start"}


# ============================================================ 纯函数（可自测）
def hhmm_to_min(s: str) -> int:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(s or ""))
    if not m:
        raise ValueError("时间格式应为 HH:MM：%r" % s)
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError("时间越界：%r" % s)
    return h * 60 + mi


def parse_window(spec: str) -> dict:
    """'HH:MM-HH:MM' → {start, end, cross_midnight, start_min, end_min, span_min}。

    end <= start ⇒ 跨零点（如 22:30-01:30）。end == start ⇒ 拒绝（含义不明）。
    """
    m = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*", str(spec or ""))
    if not m:
        raise ValueError("窗口格式应为 HH:MM-HH:MM：%r" % spec)
    start, end = m.group(1), m.group(2)
    s_min, e_min = hhmm_to_min(start), hhmm_to_min(end)
    if s_min == e_min:
        raise ValueError("窗口起止相同，含义不明：%r" % spec)
    cross = e_min < s_min
    span = (e_min - s_min) % 1440
    return {"start": "%02d:%02d" % divmod(s_min, 60), "end": "%02d:%02d" % divmod(e_min, 60),
            "start_min": s_min, "end_min": e_min, "cross_midnight": cross, "span_min": span}


def yield_conflicts(start_min: int, span_min: int) -> list:
    """[start_min, start_min+span) 与让位区的交集（跨零点按天重复展开）。返回 ['04:00-05:33', ...]。

    取「整分钟闭区间」：与 rogue_plan.mjs inYieldZone（≤05.55 / ≤20.5）同口径，
    ⇒ 05:33 / 20:30 这两分钟**仍在区内**（20:31 起出区）。
    """
    hits = []
    for zs, ze in YIELD_ZONES:
        z0, z1 = hhmm_to_min(zs), hhmm_to_min(ze) + 1
        for day in range(-1, 3):
            lo, hi = z0 + day * 1440, z1 + day * 1440
            if start_min < hi and lo < start_min + span_min:
                hits.append("%s-%s" % (zs, ze))
                break
    return hits


def now_minutes(now: dt.datetime | None = None) -> int:
    n = now or dt.datetime.now()
    return n.hour * 60 + n.minute


def window_covers_now(win: dict, now: dt.datetime | None = None) -> bool:
    n = now_minutes(now)
    if win["cross_midnight"]:
        return n >= win["start_min"] or n < win["end_min"]
    return win["start_min"] <= n < win["end_min"]


def resolve_scenario(name: str) -> tuple:
    """返回 (规范名, 定义)；不认识抛 ValueError 并列出别名。"""
    key = str(name or "").strip().lower()
    for canon, d in SCENARIOS.items():
        if key == canon or key in [a.lower() for a in d["aliases"]]:
            return canon, d
    raise ValueError("未知场景 %r，可选：%s" % (name, "/".join(SCENARIOS)))


def build_task_patch(scenario: str, template: dict, base_task: dict) -> dict:
    """在 base（现状 Task）之上生成**显式全量**的新 Task：场景开关 1，其余 0；模板 task_fields 覆盖。"""
    out = {k: False for k in TASK_KEYS}
    for k, v in (base_task or {}).items():        # 保留未知键（前向兼容）但以 TASK_KEYS 为准
        if k not in TASK_KEYS:
            out[k] = v
    sw = (template or {}).get("switch") or SCENARIOS[scenario]["switch"]
    for k, v in sw.items():
        if k in TASK_KEYS:
            out[k] = bool(v)
    for k, v in ((template or {}).get("task_fields") or {}).items():
        out[k] = v
    return out


def task_diff(before: dict, after: dict) -> dict:
    keys = sorted(set(before) | set(after))
    return {k: {"from": before.get(k), "to": after.get(k)}
            for k in keys if before.get(k) != after.get(k)}


def load_template(path: Path) -> dict:
    """模板容错解析：{switch/task_fields/tasks|TaskQueue/[...]} 三种形态都认。

    单任务对象形态（annihilation.json / activity_priority.json 实形：裸 FightTask 对象）
    ⇒ 包成 [对象]。`tasks` 为单对象亦然。
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(raw, list):
        raw = {"tasks": raw}
    if not isinstance(raw, dict):
        raise ValueError("模板不是对象/数组：%s" % path)
    if str(raw.get("TaskType") or "").strip():                    # 裸任务对象
        raw = {"tasks": [raw]}
    out = {"path": str(path), "switch": raw.get("switch") or raw.get("Switch"),
           "task_fields": raw.get("task_fields") or raw.get("TaskFields") or {},
           "times_path": raw.get("times_path") or raw.get("TimesPath"),
           "notes": raw.get("notes") or ""}
    tasks = raw.get("tasks") or raw.get("Tasks") or raw.get("TaskQueue") or []
    if isinstance(tasks, dict) and str(tasks.get("TaskType") or "").strip():
        tasks = [tasks]
    out["tasks"] = tasks if isinstance(tasks, list) else []
    return out


def template_form(template: dict) -> str:
    r"""模板任务项的 MAA 版本形态：6.17.5（CoreChar）/ 6.18.0（StartingOpers）/ ""。

    依据 t_c60939cd roguelike.README §3：6.17.5 用 CoreChar，6.18.0 用 StartingOpers；
    注错版本会静默丢核心干员/助战设置。
    """
    fields = set()
    for t in (template or {}).get("tasks") or []:
        if isinstance(t, dict):
            fields |= set(t.keys())
    if "StartingOpers" in fields:
        return "6.18.0"
    if "CoreChar" in fields:
        return "6.17.5"
    return ""


def resolve_template(scenario: str, override: str | None) -> dict:
    if override:
        p = Path(override)
        return load_template(p) if p.exists() else {"missing": str(p)}
    for name in SCENARIOS[scenario]["templates"]:
        p = TEMPLATE_DIR / name
        if p.exists():
            t = load_template(p)
            t["missing"] = None
            return t
    return {"missing": str(TEMPLATE_DIR / SCENARIOS[scenario]["templates"][0]),
            "tasks": [], "switch": None, "task_fields": {}, "times_path": None}


_SET_PATH_RE = re.compile(r"^(Task|TaskQueue)\.([A-Za-z0-9_]+)(?:\.([A-Za-z0-9_]+))?$")


def set_path(target: dict, path: str, value) -> tuple:
    """把 --set / --times 应用到 (task_patch, config_patch) 上。

    支持：`Task.<key>`、`TaskQueue.<TaskType>.<key>`。
    返回 (ok, 说明)；目标不存在 ⇒ (False, 原因)（**不造字段**）。
    """
    task_patch, config_patch, times_path = target["task"], target["config"], target["times_path"]
    m = _SET_PATH_RE.match(str(path))
    if not m:
        return False, "路径须为 Task.<键> 或 TaskQueue.<任务型>.<键>：%r" % path
    p1, p2, p3 = m.groups()
    if p1 == "Task":
        if p3 is not None:
            return False, "Task 只支持两层：%r" % path
        task_patch[p2] = value
        return True, "Task.%s=%s" % (p2, json.dumps(value, ensure_ascii=False))
    # TaskQueue.<Type>.<key>
    if p3 is None:
        return False, "TaskQueue 需要三段：%r" % path
    items = [t for t in config_patch if str(t.get("TaskType", "")).lower() == p2.lower()]
    if not items:
        return False, "模板/计划里没有 TaskType=%s 的任务项，不造字段" % p2
    items[0][p3] = value
    return True, "TaskQueue.%s.%s=%s" % (p2, p3, json.dumps(value, ensure_ascii=False))


def merge_taskqueue(doc: dict, template_tasks: list) -> tuple:
    """把模板任务项合并进用户的 gui.new.json 文档。

    命中规则（t_c60939cd README 红线③「同类型多条靠 Name 定位」）：
      1) 模板项 Name 非空 ⇒ 先按 (TaskType, Name) 精确命中；
      2) 未命中 ⇒ 退回首条同 TaskType（摘要里记 how，便于审计）；
      3) 都没有 ⇒ 记 skip（**不新增**，避免臆造字段）。
    返回 (新文档, 变更摘要)。
    """
    tq = (((doc.get("Configurations") or {}).get("Default") or {}).get("TaskQueue")) \
        if isinstance(doc, dict) else None
    if tq is None:
        return doc, [{"task": None, "error": "文档里没有 Configurations.Default.TaskQueue（结构不符，拒改）"}]
    summary = []
    for patch in template_tasks or []:
        tt = str((patch or {}).get("TaskType") or "").strip()
        if not tt:
            summary.append({"task": None, "error": "模板项缺 TaskType，跳过"})
            continue
        name = str(patch.get("Name") or "").strip()
        hit, how = None, "TaskType(首条)"
        if name:
            hit = next((it for it in tq
                        if str(it.get("TaskType", "")).lower() == tt.lower()
                        and str(it.get("Name") or "") == name), None)
            how = "TaskType+Name" if hit is not None else "TaskType(首条; Name %r 未命中)" % name
        if hit is None:
            hit = next((it for it in tq if str(it.get("TaskType", "")).lower() == tt.lower()), None)
        if hit is None:
            summary.append({"task": tt, "skip": "TaskQueue 里没有同类型任务项（不新增）"})
            continue
        diff, skipped_policy = {}, {}
        for k, v in patch.items():
            if k in ("$type", "TaskType"):
                continue
            if k in ACCOUNT_FIELDS:                              # 切号字段不注入（见常量注释）
                if hit.get(k) != v:
                    skipped_policy[k] = {"from": hit.get(k), "to": v}
                continue
            if hit.get(k) != v:
                diff[k] = {"from": hit.get(k), "to": v}
                hit[k] = v
        if hit.get("IsEnable") is not True:
            diff["IsEnable"] = {"from": hit.get("IsEnable"), "to": True}
            hit["IsEnable"] = True
        entry = {"task": tt, "how": how, "hit_name": str(hit.get("Name") or ""), "diff": diff}
        if skipped_policy:
            entry["skipped_policy"] = skipped_policy
        summary.append(entry)
    return doc, summary


def scrub(obj):
    """递归丢弃凭据类键（凭据红线）。"""
    banned = ("password", "token", "secret", "cookie", "credential", "skland")
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if not any(b in k.lower() for b in banned)}
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    return obj


def gen_run_id(script_name: str, script_id: str, now: dt.datetime | None = None) -> str:
    n = now or dt.datetime.now()
    slug = re.sub(r"[^A-Za-z0-9_]+", "", str(script_name or ""))[:12] or str(script_id)[:8]
    return "run_%s_%s" % (n.strftime("%Y%m%d_%H%M%S"), slug)


def window_dates(win: dict, base: dt.datetime) -> tuple:
    """窗口 → (start_dt, end_dt)（跨零点时 end 推一天）。"""
    s = base.replace(hour=win["start_min"] // 60, minute=win["start_min"] % 60, second=0, microsecond=0)
    e = base.replace(hour=win["end_min"] // 60, minute=win["end_min"] % 60, second=0, microsecond=0)
    if win["cross_midnight"]:
        e += dt.timedelta(days=1)
    return s, e


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        return {"runs": {}}


def save_state(st: dict) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


def audit(rec: dict) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(scrub(rec), ensure_ascii=False) + "\n")


def env_flag(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


# ============================================================ 网络 / SSH（只读为主）
def api_post(path: str, body: dict | None = None, writes: bool = False, timeout: int = 60) -> dict:
    if writes:
        if path not in WRITE_ENDPOINTS:
            raise RuntimeError("写端点未在白名单：%s" % path)
    elif path not in READ_ENDPOINTS:
        raise RuntimeError("只读端点未在白名单：%s" % path)
    req = urllib.request.Request(API + path, data=json.dumps(body or {}).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def reachable() -> tuple:
    try:
        r = api_post("/api/info/version", {}, timeout=15)
        return True, "code=%s" % r.get("code")
    except Exception as e:                                       # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, e)


def ssh_ps(script: str, timeout: int = 90) -> str:
    """主机 PowerShell（UTF-16LE base64，规避中文被 GBK 破坏）。"""
    b64 = base64.b64encode(script.encode("utf-16-le")).decode()
    p = subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15",
         SSH_HOST, "chcp 65001 >nul & powershell -NoProfile -EncodedCommand " + b64],
        capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    return (p.stdout + p.stderr).decode("utf-8", "replace")


def maa_busy_dirs() -> set:
    """正在跑的 MAA 目录（小写集合）；SSH 失败抛异常（调用方 fail-closed）。"""
    out = ssh_ps("Get-CimInstance Win32_Process -Filter \"Name='MAA.exe'\" | "
                 "ForEach-Object { $_.CommandLine }")
    res = set()
    for ln in out.splitlines():
        for m in re.finditer(r"([A-Za-z]:\\MAA-Multi\\MAA_\d+)", ln):
            res.add(m.group(1).lower())
    return res


def scp_down(remote: str, local: Path) -> tuple:
    local.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(["scp", "-O", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
                        SSH_HOST + ":" + remote.replace("\\", "/"), str(local)],
                       capture_output=True, stdin=subprocess.DEVNULL, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return (local.exists() and local.stat().st_size > 0), (p.stderr or p.stdout or "").strip()[:200]


def scp_up(local: Path, remote: str) -> tuple:
    p = subprocess.run(["scp", "-O", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
                        str(local), SSH_HOST + ":" + remote.replace("\\", "/")],
                       capture_output=True, stdin=subprocess.DEVNULL, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return p.returncode == 0, (p.stderr or p.stdout or "").strip()[:200]


# ============================================================ 上游读取（只读）
def load_scripts() -> dict:
    data = api_post("/api/scripts/get", {"scriptId": None}).get("data") or {}
    out = {}
    for sid, sv in data.items():
        info = (sv or {}).get("Info") or {}
        out[str(sid)] = {"name": info.get("Name") or "?",
                         "path": str(info.get("Path") or "").replace("/", "\\").replace("\\\\", "\\"),
                         "emu": str(((sv or {}).get("Emulator") or {}).get("Index", ""))}
    return out


def resolve_script(scripts: dict, name: str | None, instance: str | None) -> dict:
    hits = []
    for sid, sv in scripts.items():
        if name and sv["name"] == name:
            hits.append((sid, sv))
        elif instance and sv["emu"] == str(instance):
            hits.append((sid, sv))
    if len(hits) == 1:
        sid, sv = hits[0]
        return {"script_id": sid, **sv}
    if not hits:
        raise ValueError("找不到脚本：%s" % (name or ("实例 %s" % instance)))
    raise ValueError("命中多个脚本，请用 --script 精确指定：%s"
                     % ", ".join("%s(emu %s)" % (s["name"], s["emu"]) for _, s in hits))


def load_users(script_id: str) -> dict:
    """{uid: {name,status,server,mode,id,task}}；Info 只取白名单键（凭据读入即弃）。"""
    data = api_post("/api/scripts/user/get", {"scriptId": script_id}).get("data") or {}
    out = {}
    for uid, uv in data.items():
        info = (uv or {}).get("Info") or {}
        out[str(uid)] = {
            "name": info.get("Name") or "?",
            "status": bool(info.get("Status")),
            "server": info.get("Server"),
            "mode": info.get("Mode"),
            "id": info.get("Id"),
            "remained": info.get("RemainedDay"),
            "task": (uv or {}).get("Task") or {},
        }
    return out


def configfile_paths(script_id: str, users: dict) -> dict:
    r"""{用户显示名: (布局, 主机路径)}；布局 ∈ {详细, 简洁}。

    详细 ⇒ data\<sid>\<uid>\ConfigFile\gui.new.json；简洁 ⇒ data\<sid>\Default\ConfigFile\gui.new.json
    （依据 D:\MAS app/task/MAA/AutoProxy.py:555-565）。Mode 缺失时现场探测（谁存在用谁）。
    """
    per_user_missing = []
    out = {}
    for uid, u in users.items():
        p = "%s\\%s\\%s\\ConfigFile\\gui.new.json" % (AUTO_MAS_DATA, script_id, uid)
        if u["mode"] == "详细":
            out[u["name"]] = ("详细", p)
        elif u["mode"] == "简洁":
            out[u["name"]] = ("简洁", "%s\\%s\\Default\\ConfigFile\\gui.new.json" % (AUTO_MAS_DATA, script_id))
        else:
            per_user_missing.append((uid, u))
    if per_user_missing:      # Mode 缺失的：现场探测一次（只读）
        ps = "; ".join(
            "$p='%s'; if (Test-Path $p) { 'U:'+'%s' } else { 'D:'+'%s' }"
            % (p, u["name"], u["name"])
            for uid, u in per_user_missing
            for p in ["%s\\%s\\%s\\ConfigFile\\gui.new.json" % (AUTO_MAS_DATA, script_id, uid)])
        try:
            for ln in ssh_ps(ps).splitlines():
                ln = ln.strip()
                if ln.startswith("U:"):
                    nm = ln[2:]
                    uid = next(u for uid, u in per_user_missing if u["name"] == nm)
                    out[nm] = ("详细?", "%s\\%s\\%s\\ConfigFile\\gui.new.json" % (AUTO_MAS_DATA, script_id, uid))
                elif ln.startswith("D:"):
                    nm = ln[2:]
                    out[nm] = ("简洁?", "%s\\%s\\Default\\ConfigFile\\gui.new.json" % (AUTO_MAS_DATA, script_id))
        except Exception:                                        # noqa: BLE001
            for _, u in per_user_missing:
                out[u["name"]] = ("?", "未探测到（SSH 不可达）")
    return out


# ============================================================ 闸门
def gate_preflight(script_name: str, allow_missing: bool) -> tuple:
    """(verdict, detail)；verdict ∈ GO / NO-GO / MISSING / ERROR。

    MISSING（产物未落地）默认算**不过**；--allow-no-preflight 显式放行（审计留痕）。
    """
    if not PREFLIGHT.exists():
        return ("GO" if allow_missing else "MISSING",
                "tools/mas_preflight.py 不存在（t_cd11ca49 未落地）%s"
                % ("；--allow-no-preflight 放行（记审计）" if allow_missing else ""))
    try:
        p = subprocess.run([sys.executable, str(PREFLIGHT), "--script", script_name, "--json"],
                           capture_output=True, stdin=subprocess.DEVNULL, text=True,
                           encoding="utf-8", errors="replace", timeout=420, cwd=str(ROOT))
        blob = (p.stdout or "") + (p.stderr or "")
        m = re.search(r"PREFLIGHT\s*=\s*(GO|NO-GO)", blob) \
            or re.search(r'"preflight"\s*:\s*"(GO|NO-GO)"', blob)
        if p.returncode == 2:                                    # 2=无法判定/参数错误 ⇒ fail-closed
            verdict = "ERROR"
        elif m:
            verdict = m.group(1)
        else:
            verdict = "GO" if p.returncode == 0 else "NO-GO"
        return (verdict, ("exit=%d %s" % (p.returncode, blob.replace("\n", " ")[-300:])).strip())
    except Exception as e:                                       # noqa: BLE001
        return ("ERROR", "preflight 执行异常：%s: %s" % (type(e).__name__, e))


# ============================================================ 计划构建
def build_plan(args) -> dict:
    """只读地构建完整计划（含闸门评估）。任何写请求不在此发生。"""
    plan: dict = {"refused": [], "warnings": [], "gates": []}

    def gate(name, status, detail="", hard=False):
        plan["gates"].append({"gate": name, "status": status, "detail": detail, "hard": hard})
        if hard and status not in ("GO", "SKIP"):
            plan["refused"].append("%s: %s" % (name, detail or status))

    try:
        canon, sdef = resolve_scenario(args.scenario)
    except ValueError as e:
        gate("G1 输入", "FAIL", str(e), hard=True)
        return plan
    plan["scenario"] = canon
    plan["scenario_title"] = sdef["title"]

    # 窗口
    win = None
    now = dt.datetime.now()
    if args.window:
        try:
            win = parse_window(args.window)
        except ValueError as e:
            gate("G1 输入", "FAIL", str(e), hard=True)
    elif args.duration:
        s = now_minutes(now) + 5                       # 默认 5 分钟后开始
        e = s + int(args.duration)
        win = {"start": "%02d:%02d" % divmod(s % 1440, 60), "end": "%02d:%02d" % divmod(e % 1440, 60),
               "start_min": s % 1440, "end_min": e % 1440, "cross_midnight": (e % 1440) < (s % 1440),
               "span_min": int(args.duration)}
        plan["warnings"].append("未给 --window：按 --duration 推窗（%s–%s，含 5 分钟缓冲）"
                                % (win["start"], win["end"]))
    else:
        gate("G1 输入", "FAIL", "需要 --window HH:MM-HH:MM 或 --duration 分钟", hard=True)
    if win:
        plan["window"] = win
        zc = yield_conflicts(win["start_min"], win["span_min"])
        gate("G4 让位区", "FAIL" if zc else "GO",
             ("窗口与让位区相交：%s（早班 04:00–05:33 / 晚班 18:30–20:30，肉鸽×产线不许并行）" % "、".join(zc))
             if zc else "与 04:00–05:33 / 18:30–20:30 无交集", hard=True)
        if not window_covers_now(win, now):
            gate("G1 输入", "WARN", "当前时刻不在窗口内（apply 会在窗口外被拒；如系预排，可先看计划）")

    # 模板
    tpl = resolve_template(canon, args.template)
    if tpl.get("missing"):
        gate("G2 模板", "FAIL", "模板缺失：%s（t_c60939cd 产物未落地；或 --template 指定）"
             % tpl["missing"], hard=True)
    else:
        gate("G2 模板", "GO", "模板：%s（任务项 %d 条）" % (tpl.get("path"), len(tpl.get("tasks") or [])))
        form = template_form(tpl)
        if form == "6.17.5" and canon == "rogue":
            gate("G2 模板", "WARN",
                 "肉鸽模板为 6.17.5（CoreChar）形态；AUTO-MAS 的 MAA_1..12 为 6.18.0（StartingOpers）"
                 "——直接注入会丢核心干员/助战（t_c60939cd roguelike.README §3），注入前按目标版本选形态")

    # --times/--set 落点（模板/场景派生，与是否解析到用户无关——离线也生效）
    plan["times_path"] = tpl.get("times_path") or sdef["times_path"]
    plan["times"] = args.times
    plan["sets"] = args.set or []
    if args.times is not None and not plan["times_path"]:
        plan["warnings"].append(
            "--times 对场景 %s 未定义映射（模板未声明 times_path）已忽略；"
            "请用 --set 精确指定（例：--set Task.ActivityMedicineNumb=N）" % canon)

    # 双闸 / 一票否决
    if env_flag(NO_GO_ENV):
        gate("G3 双闸", "FAIL", "%s 已置位（一票否决）" % NO_GO_ENV, hard=True)
    if not args.apply:
        gate("G3 双闸", "SKIP", "dry-run（未给 --apply）")
    elif not env_flag(APPLY_ENV):
        gate("G3 双闸", "FAIL", "草案阶段：真发车需 --apply ＋ %s=1（用户批准后设置）" % APPLY_ENV, hard=True)

    # 只读现场（离线/上游不可达时降级为 WARN；apply 时由 main 提高为硬拒）
    plan["live"] = {"offline": bool(args.offline)}
    if args.offline:
        gate("G6 现场", "SKIP", "--offline：跳过只读探测；--apply 与 --offline 互斥")
        if args.apply:
            plan["refused"].append("G6 现场: --apply 不能与 --offline 同用（apply 必须做现场验证）")
    else:
        ok, why = reachable()
        gate("G6 现场", "GO" if ok else "WARN", "AUTO-MAS %s（%s）" % ("可达" if ok else "不可达", why))
        plan["live"]["mas"] = ok
        if ok:
            try:
                scripts = load_scripts()
                sc = resolve_script(scripts, args.script, args.instance)
                plan["script"] = sc
                users = load_users(sc["script_id"])
                act = {u: v for u, v in users.items()
                       if v["status"] and (v["remained"] is None or str(v["remained"]) != "0")}
                plan["users"] = act
                gate("G1 输入", "GO", "脚本 %s（sid=%s，emu=%s）：启用用户 %d 个（%s）"
                     % (sc["name"], sc["script_id"][:8], sc["emu"], len(act),
                        "、".join(v["name"] for v in act.values()) or "无"))
                if not act:
                    gate("G1 输入", "FAIL", "该脚本没有启用中的用户", hard=True)
                cfp = configfile_paths(sc["script_id"], act)
                plan["configfile_paths"] = cfp
                plan["configfile_shared_default"] = sum(1 for l, _ in cfp.values() if l.startswith("简洁"))
                try:
                    busy = maa_busy_dirs()
                    if str(sc["path"]).lower() in busy:
                        gate("G6 现场", "FAIL", "目标目录有 MAA 在跑：%s（改配置先停 MAA：改前它会把旧配置写回盘）"
                             % sc["path"], hard=True)
                    else:
                        gate("G6 现场", "GO", "目标目录空闲：%s（当前跑动目录：%s）"
                             % (sc["path"], "、".join(sorted(x.split("\\")[-1] for x in busy)) or "无"))
                except Exception as e:                           # noqa: BLE001
                    gate("G6 现场", "WARN", "SSH 探测 MAA 进程失败：%s: %s（apply 时 fail-closed）"
                         % (type(e).__name__, str(e)[:120]), hard=bool(args.apply))
            except Exception as e:                               # noqa: BLE001
                gate("G1 输入", "FAIL", "脚本解析失败：%s: %s" % (type(e).__name__, e), hard=True)

    # 单飞
    st = load_state()
    today = now.date().isoformat()
    if "script" in plan:
        sid = plan["script"]["script_id"]
        open_same = [r for r in st["runs"].values()
                     if r.get("status") == "open" and r.get("script_id") == sid]
        if open_same:
            gate("G7 单飞", "FAIL", "该脚本已有 open 运行：%s（先 --restore-run 或等对账还原）"
                 % ", ".join(r["run_id"] for r in open_same), hard=True)
        else:
            same_day = [r for r in st["runs"].values()
                        if r.get("script_id") == sid and r.get("scenario") == canon
                        and str(r.get("dispatched_at", "")).startswith(today)
                        and r.get("status") != "failed"]
            gate("G7 单飞", "FAIL" if (same_day and not args.force) else "GO",
                 ("今天已跑过同场景：%s（重复须 --force）" % ", ".join(r["run_id"] for r in same_day))
                 if same_day and not args.force else "今日无同脚本同场景记录", hard=bool(same_day))

    # preflight（apply 时是真闸；dry-run 展示；产物未落地默认不过）
    if args.offline:
        gate("G5 preflight", "SKIP", "--offline：跳过 preflight（它要联远端）")
    else:
        pf, pf_detail = gate_preflight(plan.get("script", {}).get("name", args.script or "?"),
                                       allow_missing=bool(args.allow_no_preflight))
        if pf == "MISSING" and not args.apply:
            gate("G5 preflight", "SKIP", pf_detail + "（dry-run 展示；apply 需先落地或 --allow-no-preflight）")
        else:
            gate("G5 preflight", pf, pf_detail, hard=bool(args.apply) and pf != "GO")
            if args.allow_no_preflight and pf == "GO":
                plan["warnings"].append("--allow-no-preflight 放行：preflight 未以 GO 通过（记审计）")

    # 计划明细（逐用户写面；模板缺失也展示，写面参数以模板为准、缺失处标注）
    if "users" in plan:
        sw = (tpl.get("switch") or sdef["switch"])
        details = []
        for uid, u in plan["users"].items():
            after = build_task_patch(canon, tpl, u["task"])
            details.append({"uid": uid, "name": u["name"], "server": u["server"],
                            "task_diff": task_diff({k: bool(u["task"].get(k)) for k in TASK_KEYS},
                                                   {k: after[k] for k in TASK_KEYS}),
                            "task_after": {k: after[k] for k in TASK_KEYS},
                            "configfile": (plan.get("configfile_paths") or {}).get(u["name"], ("?", "?"))})
        plan["per_user"] = details
    return plan


def print_plan(plan: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))
        return
    print("=== mas_scenario_dispatch · %s ===" % ("APPLY" if plan.get("applying") else "DRY-RUN"))
    if "scenario" in plan:
        print("场景: %s（%s）  窗口: %s" % (
            plan["scenario"], plan.get("scenario_title", ""),
            (plan.get("window") or {}).get("start", "?") + "–" + (plan.get("window") or {}).get("end", "?")))
    if "script" in plan:
        sc = plan["script"]
        print("脚本: %s（sid=%s, emu=%s）" % (sc["name"], sc["script_id"][:8], sc["emu"]))
    if plan.get("users"):
        print("目标用户（启用）: %s" % "、".join(v["name"] for v in plan["users"].values()))
    print("\n-- 计划（按闸门全过后执行；apply 前不触达任何写请求）--")
    print("  1) 备份: Task(10键) + ConfigFile（含共享 Default 去重）+ meta → runtime/scenario_dispatch/backups/<run_id>/")
    if plan.get("per_user"):
        tps = plan.get("times_path")
        print("  2) 写入 Task: 场景开关置 1、其余显式置 0%s" % ("；--times %s → %s" % (plan["times"], tps) if plan.get("times") and tps else ""))
        for d in plan["per_user"][:12]:
            lay, path = d["configfile"]
            print("     - %-14s [%s] %s" % (d["name"], lay, path))
        if len(plan["per_user"]) > 12:
            print("     - … 共 %d 个用户" % len(plan["per_user"]))
    print("  3) 写入 ConfigFile.TaskQueue: 按模板合并（同类型按 Name 定位，缺则首条；切号字段不注入；不改的不写）")
    for m in plan.get("preview_writes") or []:
        print("     · %s" % m)
    print("  4) 发车: POST /api/dispatch/start {\"taskId\": <scriptId>, \"mode\": \"AutoProxy\"}")
    print("  5) 审计: runtime/scenario_dispatch_log.jsonl")
    w = plan.get("window") or {}
    print("  6) 还原: 到期时刻 %s %s（--reconcile --apply）" % (w.get("date", dt.date.today().isoformat()), w.get("end", "?")))
    print("\n-- 闸门 --")
    for g in plan["gates"]:
        mark = {"GO": "GO  ", "SKIP": "SKIP", "WARN": "WARN", "FAIL": "FAIL", "MISSING": "FAIL",
                "NO-GO": "FAIL", "ERROR": "FAIL"}.get(g["status"], g["status"])
        print("  [%s] %-14s %s" % (mark, g["gate"], g["detail"]))
    if plan.get("warnings"):
        print("\n-- 告警 --")
        for w_ in plan["warnings"]:
            print("  ! " + w_)
    if plan["refused"]:
        print("\n拒绝原因（apply 前必须清零）：")
        for r in plan["refused"]:
            print("  ✗ " + r)
    print("\n[dry-run] 没有触达任何写请求。%s" % (
        "（--apply 可尝试真跑，但草案双闸默认关闭）" if not plan.get("applying") else ""))


# ============================================================ apply 路径（双闸内）
def do_apply(args) -> int:
    """真发车。仅当 G3 双闸放行后才会被调用；任何一步失败 → 尽力回滚 + 留痕。"""
    plan = build_plan(args)
    plan["applying"] = True
    if plan["refused"]:
        print_plan(plan, args.json)
        print("\n[refused] 闸门未过，零上游写请求。", file=sys.stderr)
        return 1
    if not args.apply or not env_flag(APPLY_ENV):
        print_plan(plan, args.json)
        print("\n[refused] 草案阶段：真发车需 --apply ＋ %s=1（用户批准后设置）。" % APPLY_ENV,
              file=sys.stderr)
        return 1
    sc, users, tpl = plan["script"], plan["users"], resolve_template(plan["scenario"], args.template)
    now = dt.datetime.now()
    run_id = gen_run_id(sc["name"], sc["script_id"], now)
    win = plan["window"]
    run_dir = BACKUPS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {"run_id": run_id, "ts": now.isoformat(timespec="seconds"), "scenario": plan["scenario"],
            "script_id": sc["script_id"], "script_name": sc["name"], "emu": sc["emu"],
            "users": [u["name"] for u in users.values()], "window": win,
            "times": args.times, "sets": args.set or [], "backup_dir": str(run_dir)}
    (run_dir / "meta.json").write_text(json.dumps(scrub(meta), ensure_ascii=False, indent=1), encoding="utf-8")
    # 1) 快照
    (run_dir / "task.json").write_text(json.dumps(
        scrub({uid: {"name": u["name"], "task": u["task"]} for uid, u in users.items()}),
        ensure_ascii=False, indent=1), encoding="utf-8")
    cf_backup, seen, resolved = {}, set(), set()
    for uid, u in users.items():
        lay, rpath = (plan.get("configfile_paths") or {}).get(u["name"], ("?", "?"))
        if rpath in ("?", "未探测到（SSH 不可达）"):
            continue
        resolved.add(u["name"])
        if rpath in seen:
            continue
        seen.add(rpath)
        local = run_dir / "configfiles" / ("%s.gui.new.json" % (uid if lay.startswith("详细") else "Default"))
        ok, why = scp_down(rpath, local)
        if not ok:
            audit({"ts": now.isoformat(timespec="seconds"), "run_id": run_id, "event": "backup_fail",
                   "detail": {"user": u["name"], "path": rpath, "why": why}})
            print("[ABORT] ConfigFile 备份失败：%s（%s）" % (rpath, why), file=sys.stderr)
            return 3
        cf_backup[u["name"]] = {"layout": lay, "path": rpath, "local": str(local)}
        ssh_ps("Copy-Item '%s' '%s.bak-mas-scenario-%s' -Force" % (rpath, rpath, now.strftime("%Y%m%d-%H%M%S")))
    (run_dir / "configmap.json").write_text(json.dumps(cf_backup, ensure_ascii=False, indent=1), encoding="utf-8")
    missing_users = [u["name"] for u in users.values() if u["name"] not in resolved]
    if missing_users and (tpl.get("tasks") or []):
        print("[ABORT] 以下用户的 ConfigFile 目标未解析（场景参数无法保证落地）：%s"
              % "、".join(missing_users), file=sys.stderr)
        return 3
    audit({"ts": now.isoformat(timespec="seconds"), "run_id": run_id, "event": "backup",
           "detail": {"dir": str(run_dir), "users": len(users), "files": len(seen)}})

    # 2/3) 写 Task 与 ConfigFile
    written_tasks, patched_files = [], []
    try:
        for uid, u in users.items():
            after = build_task_patch(plan["scenario"], tpl, u["task"])
            r = api_post("/api/scripts/user/update",
                         {"scriptId": sc["script_id"], "userId": uid,
                          "data": {"Task": {k: after[k] for k in after}}}, writes=True)
            if r.get("code") != 200:
                raise RuntimeError("Task 写入被拒（%s）：%s" % (u["name"], str(r.get("message"))[:120]))
            written_tasks.append(uid)
        for name, info_ in cf_backup.items():
            doc = json.loads(Path(info_["local"]).read_text(encoding="utf-8-sig"))
            doc2, summary = merge_taskqueue(doc, tpl.get("tasks") or [])
            if any("error" in s for s in summary):
                raise RuntimeError("ConfigFile 合并失败：%s" % summary)
            tmp = Path(info_["local"] + ".patched")
            tmp.write_text(json.dumps(doc2, ensure_ascii=False, indent=4), encoding="utf-8")
            ok, why = scp_up(tmp, info_["path"])
            if not ok:
                raise RuntimeError("ConfigFile 回传失败：%s（%s）" % (info_["path"], why))
            patched_files.append(info_["path"])
            audit({"ts": dt.datetime.now().isoformat(timespec="seconds"), "run_id": run_id,
                   "event": "configfile_write", "detail": {"path": info_["path"], "summary": summary}})
    except Exception as e:                                       # noqa: BLE001
        print("[ABORT] 写入失败：%s: %s —— 尽力回滚" % (type(e).__name__, e), file=sys.stderr)
        restore_run(run_id, dry=False, note="apply 失败回滚")
        return 3

    # 4) 发车
    try:
        r = api_post("/api/dispatch/start", {"taskId": sc["script_id"], "mode": "AutoProxy"}, writes=True)
    except Exception as e:                                       # noqa: BLE001
        print("[ABORT] 发车异常：%s: %s —— 回滚配置" % (type(e).__name__, e), file=sys.stderr)
        restore_run(run_id, dry=False, note="dispatch 异常回滚")
        return 3
    task_uid = r.get("taskId")
    good = r.get("code") == 200 and bool(task_uid)
    st = load_state()
    st["runs"][run_id] = {**meta, "dispatched_at": dt.datetime.now().isoformat(timespec="seconds"),
                          "task_uid": task_uid, "status": "open" if good else "failed",
                          "restored_at": None, "configfiles": {n: v["path"] for n, v in cf_backup.items()}}
    save_state(st)
    audit({"ts": dt.datetime.now().isoformat(timespec="seconds"), "run_id": run_id, "event": "dispatch",
           "detail": {"ok": good, "task_uid": task_uid, "resp": r}})
    if not good:
        print("[FAIL] 发车返回异常：%s —— 回滚配置" % json.dumps(r, ensure_ascii=False)[:200], file=sys.stderr)
        restore_run(run_id, dry=False, note="dispatch 返回异常回滚")
        return 3
    print("[SENT] run_id=%s task_uid=%s 用户=%d 窗口=%s–%s 到期还原：--reconcile --apply"
          % (run_id, task_uid, len(users), win["start"], win["end"]))
    return 0


# ============================================================ 还原
def restore_run(run_id: str, dry: bool, note: str = "", attempts: int = 12, gap: int = 30) -> int:
    """还原一次运行：Task 回写（锁重试）+ ConfigFile 回滚（scp）+ 回读校验。返回 0/2/3。"""
    st = load_state()
    run = st["runs"].get(run_id)
    if not run:
        print("未知 run_id：%s" % run_id, file=sys.stderr)
        return 2
    if run.get("status") == "restored":
        print("run %s 已还原过（%s）" % (run_id, run.get("restored_at")))
        return 0
    sid, run_dir = run["script_id"], Path(run.get("backup_dir", ""))
    tasks = json.loads((run_dir / "task.json").read_text(encoding="utf-8")) if (run_dir / "task.json").exists() else {}
    cfmap = json.loads((run_dir / "configmap.json").read_text(encoding="utf-8")) if (run_dir / "configmap.json").exists() else {}
    if dry:
        print("[dry-run] would restore %s：Task %d 个用户 + ConfigFile %d 个文件（%s）"
              % (run_id, len(tasks), len(cfmap), note or "到期"))
        return 0
    if not env_flag(APPLY_ENV) or env_flag(NO_GO_ENV):
        print("还原被闸拦下：需 %s=1 且 %s 未置位" % (APPLY_ENV, NO_GO_ENV), file=sys.stderr)
        return 1
    # ConfigFile 回滚（文件面无锁；直接回传）
    cf_ok, cf_fail = 0, []
    for name, info_ in cfmap.items():
        local = Path(info_.get("local", ""))
        if not local.exists():
            cf_fail.append("%s(备份缺失)" % name)
            continue
        ok, why = scp_up(local, info_["path"])
        (cf_ok := cf_ok + 1) if ok else cf_fail.append("%s(%s)" % (name, why))
    # Task 回滚（锁重试）
    still = []
    for i in range(1, attempts + 1):
        still = []
        for uid, rec in tasks.items():
            cur = None
            try:
                d = api_post("/api/scripts/user/get", {"scriptId": sid, "userId": uid}).get("data") or {}
                cur = (d.get(uid) or {}).get("Task") or {}
            except Exception:                                    # noqa: BLE001
                still.append("%s(读回失败)" % rec["name"])
                continue
            want = {k: rec["task"].get(k) for k in TASK_KEYS if k in rec["task"]}
            if {k: cur.get(k) for k in want} != want:
                try:
                    r = api_post("/api/scripts/user/update",
                                 {"scriptId": sid, "userId": uid, "data": {"Task": rec["task"]}}, writes=True)
                    if r.get("code") != 200:
                        still.append("%s(%s)" % (rec["name"], str(r.get("message"))[:40]))
                except Exception as e:                           # noqa: BLE001
                    still.append("%s(%s)" % (rec["name"], type(e).__name__))
        if not still:
            break
        print("restore 第 %d 次未完成：%s；%ds 后重试（配置运行中会被锁）" % (i, "、".join(still), gap))
        time.sleep(gap)
    ok_all = not still and not cf_fail
    st = load_state()
    if run_id in st["runs"]:
        st["runs"][run_id]["status"] = "restored" if ok_all else "open"
        st["runs"][run_id]["restored_at"] = dt.datetime.now().isoformat(timespec="seconds") if ok_all else None
        st["runs"][run_id]["restore_note"] = note
        save_state(st)
    audit({"ts": dt.datetime.now().isoformat(timespec="seconds"), "run_id": run_id, "event": "restore",
           "detail": {"ok": ok_all, "cf_ok": cf_ok, "cf_fail": cf_fail, "task_still": still, "note": note}})
    print("恢复：ConfigFile %d 个成功%s；Task %s" % (
        cf_ok, ("，失败 %s" % cf_fail) if cf_fail else "",
        "全部一致 ✓" if not still else "仍未一致：%s" % "、".join(still)))
    return 0 if ok_all else 3


def cmd_reconcile(args) -> int:
    st = load_state()
    now = dt.datetime.now()
    due = []
    for rid, run in (st.get("runs") or {}).items():
        if run.get("status") != "open":
            continue
        win = run.get("window") or {}
        base = dt.datetime.fromisoformat(run["dispatched_at"]) if run.get("dispatched_at") else now
        _, end_dt = window_dates(win, base)
        if now >= end_dt:
            due.append((rid, end_dt))
    if not due:
        print("对账：没有到期该还原的运行（open=%d）"
              % sum(1 for r in (st.get("runs") or {}).values() if r.get("status") == "open"))
        return 0
    rc = 0
    for rid, end_dt in due:
        print("到期运行 %s（窗口止于 %s）" % (rid, end_dt.strftime("%m-%d %H:%M")))
        if args.apply:
            rc = max(rc, restore_run(rid, dry=False, note="reconcile 到期还原"))
        else:
            restore_run(rid, dry=True)
    return rc


# ============================================================ 其余命令
def cmd_status(args) -> int:
    st = load_state()
    runs = st.get("runs") or {}
    if args.json:
        print(json.dumps(runs, ensure_ascii=False, indent=2, default=str))
        return 0
    if not runs:
        print("（无运行记录）state=%s" % STATE)
        return 0
    print("%-34s %-8s %-12s %-18s %-9s %s" % ("run_id", "status", "scenario", "script", "task_uid", "窗口"))
    for rid, r in sorted(runs.items()):
        w = r.get("window") or {}
        print("%-34s %-8s %-12s %-18s %-9s %s–%s" % (
            rid, r.get("status"), r.get("scenario"), str(r.get("script_name"))[:18],
            str(r.get("task_uid"))[:8], w.get("start"), w.get("end")))
    return 0


def cmd_list_scripts(args) -> int:
    scripts = load_scripts()
    if args.json:
        print(json.dumps(scripts, ensure_ascii=False, indent=2))
        return 0
    print("%-44s %-6s %s" % ("脚本名", "实例", "MAA 目录"))
    for sid, s in sorted(scripts.items(), key=lambda kv: kv[1]["name"]):
        print("%-44s %-6s %s" % (s["name"][:44], s["emu"], s["path"]))
        if args.with_users:
            try:
                us = load_users(sid)
                print("    用户: %s" % "、".join(
                    "%s%s" % (u["name"], "" if u["status"] else "(停用)") for u in us.values()))
            except Exception as e:                               # noqa: BLE001
                print("    用户读取失败: %s" % e)
    return 0


# ============================================================ 自检（纯本地）
def selftest() -> int:
    cases = []

    def T(name, got, want):
        cases.append((name, got, want))

    # 窗口解析
    w = parse_window("22:30-23:30")
    T("win-basic", (w["start"], w["end"], w["cross_midnight"], w["span_min"]), ("22:30", "23:30", False, 60))
    w2 = parse_window("22:30-01:30")
    T("win-cross", (w2["cross_midnight"], w2["span_min"]), (True, 180))
    for bad in ("22:30", "25:00-26:00", "22:30-22:30", ""):
        try:
            parse_window(bad)
            T("win-bad:%r" % bad, "accepted", "rejected")
        except ValueError:
            T("win-bad:%r" % bad, "rejected", "rejected")
    # 窗口 → 到期时刻（跨零点推一天；对账还原依赖它）
    base = dt.datetime(2026, 9, 27, 22, 30)
    w_same = parse_window("21:30-22:30")
    T("win-same-flags", (w_same["cross_midnight"], w_same["span_min"]), (False, 60))
    T("win-same-covers", window_covers_now(w_same, dt.datetime(2026, 9, 27, 22, 0)), True)
    T("win-same-due", [x.isoformat() for x in window_dates(w_same, base)],
      ["2026-09-27T21:30:00", "2026-09-27T22:30:00"])
    w_cross = parse_window("22:30-03:30")
    T("win-cross-flags", (w_cross["cross_midnight"], w_cross["span_min"]), (True, 300))
    T("win-cross-covers-late", window_covers_now(w_cross, dt.datetime(2026, 9, 28, 1, 0)), True)
    T("win-cross-due", [x.isoformat() for x in window_dates(w_cross, base)],
      ["2026-09-27T22:30:00", "2026-09-28T03:30:00"])
    # 让位区（整分钟闭区间：05:33/20:30 仍在区内，20:31 起出区）
    T("yield-in-morning", yield_conflicts(hhmm_to_min("05:00"), 30), ["04:00-05:33"])
    T("yield-in-evening", yield_conflicts(hhmm_to_min("19:30"), 60), ["18:30-20:30"])
    T("yield-clear-2130", yield_conflicts(hhmm_to_min("21:30"), 60), [])
    T("yield-edge-2030-in", yield_conflicts(hhmm_to_min("20:30"), 30), ["18:30-20:30"])
    T("yield-edge-2031-out", yield_conflicts(hhmm_to_min("20:31"), 30), [])
    T("yield-cross-midnight-ok", yield_conflicts(hhmm_to_min("22:30"), 300), [])  # 22:30–03:30 不碰让位
    T("yield-cross-midnight-hit", yield_conflicts(hhmm_to_min("22:30"), 420), ["04:00-05:33"])
    # 场景别名
    T("scenario-alias-rouge", resolve_scenario("肉鸽")[0], "rogue")
    T("scenario-alias-anni", resolve_scenario("剿灭")[0], "annihilation")
    try:
        resolve_scenario("迷雾")
        T("scenario-bad", "accepted", "rejected")
    except ValueError:
        T("scenario-bad", "rejected", "rejected")
    # Task 补丁：显式全 10 键
    after = build_task_patch("rogue", {}, {"IfStartUp": True, "IfRoguelike": False, "Custom": 7})
    T("patch-keys", sorted(after), sorted(TASK_KEYS + ["Custom"]))
    T("patch-rogue-on", (after["IfRoguelike"], after["IfFight"], after["Custom"]), (True, False, 7))
    T("patch-diff", task_diff({"IfFight": True}, after)["IfFight"], {"from": True, "to": False})
    # 模板解析（三种形态）
    import tempfile
    for i, payload in enumerate([
        {"switch": {"IfRoguelike": True}, "task_fields": {"IfActivityFirst": False},
         "tasks": [{"TaskType": "Roguelike", "StartCount": 1}], "times_path": "TaskQueue.Roguelike.StartCount"},
        {"TaskQueue": [{"TaskType": "Roguelike", "StartCount": 1}]},
        [{"TaskType": "Roguelike", "StartCount": 1}],
    ]):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            p = f.name
        t = load_template(Path(p))
        T("tpl-shape-%d" % i, len(t["tasks"]), 1)
        os.unlink(p)
    # 单任务对象形态（annihilation.json / activity_priority.json 实形）
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"TaskType": "Fight", "Name": "剿灭作战", "StagePlan": ["Annihilation"]},
                  f, ensure_ascii=False)
        p = f.name
    t = load_template(Path(p))
    T("tpl-single-object", (len(t["tasks"]), t["tasks"][0].get("Name")), (1, "剿灭作战"))
    os.unlink(p)
    # --set / --times 路径
    tgt = {"task": {}, "config": [{"TaskType": "Roguelike", "StartCount": 99999}], "times_path": None}
    ok1, _ = set_path(tgt, "TaskQueue.Roguelike.StartCount", 1)
    ok2, msg2 = set_path(tgt, "TaskQueue.NotExist.X", 1)
    ok3, _ = set_path(tgt, "Task.ActivityStageIndex", 3)
    T("set-path", (ok1, ok2, tgt["config"][0]["StartCount"], tgt["task"], ok3), (True, False, 1, {"ActivityStageIndex": 3}, True))
    # 合并（同类型第一条；缺类型 → skip 不新增）
    doc = {"Configurations": {"Default": {"TaskQueue": [
        {"TaskType": "StartUp", "IsEnable": True},
        {"TaskType": "Roguelike", "IsEnable": False, "StartCount": 99999, "Theme": "Mizuki"}]}}}
    doc2, summ = merge_taskqueue(doc, [{"TaskType": "Roguelike", "StartCount": 1},
                                       {"TaskType": "Fight", "StagePlan": ["x"]}])
    T("merge-count", doc2["Configurations"]["Default"]["TaskQueue"][1]["StartCount"], 1)
    T("merge-enable", doc2["Configurations"]["Default"]["TaskQueue"][1]["IsEnable"], True)
    T("merge-skip-missing", "skip" in summ[1], True)
    T("merge-no-new-item", len(doc2["Configurations"]["Default"]["TaskQueue"]), 2)
    # Name 命中合并（Fight 型多条：理智作战 vs 剿灭作战）
    doc3, sum3 = merge_taskqueue({"Configurations": {"Default": {"TaskQueue": [
        {"TaskType": "Fight", "Name": "理智作战", "IsEnable": True, "StagePlan": ["1-7"]},
        {"TaskType": "Fight", "Name": "剿灭作战", "IsEnable": False, "StagePlan": []}]}}},
        [{"TaskType": "Fight", "Name": "剿灭作战", "StagePlan": ["Annihilation"]}])
    q3 = doc3["Configurations"]["Default"]["TaskQueue"]
    T("merge-by-name", (q3[1]["StagePlan"], q3[0]["StagePlan"], sum3[0].get("how")),
      (["Annihilation"], ["1-7"], "TaskType+Name"))
    T("merge-name-miss-fallback",
      merge_taskqueue({"Configurations": {"Default": {"TaskQueue": [
          {"TaskType": "Fight", "Name": "理智作战", "IsEnable": True}]}}},
          [{"TaskType": "Fight", "Name": "剿灭作战", "IsEnable": True}])[1][0].get("how"),
      "TaskType(首条; Name '剿灭作战' 未命中)")
    # 切号字段不注入（模板 StartUp 带 AccountName/AccountSwitchEnabled ⇒ 跳过并记 skipped_policy）
    doc4, sum4 = merge_taskqueue({"Configurations": {"Default": {"TaskQueue": [
        {"TaskType": "StartUp", "Name": "开始唤醒", "IsEnable": True,
         "AccountName": "Masked_AB12", "AccountSwitchEnabled": True}]}}},
        [{"TaskType": "StartUp", "Name": "开始唤醒", "AccountName": "", "AccountSwitchEnabled": False}])
    q4 = doc4["Configurations"]["Default"]["TaskQueue"][0]
    T("merge-account-skip",
      (q4["AccountName"], q4["AccountSwitchEnabled"], sorted(sum4[0].get("skipped_policy") or {})),
      ("Masked_AB12", True, ["AccountName", "AccountSwitchEnabled"]))
    # 版本形态（t_c60939cd roguelike.README §3）
    T("form-6175", template_form({"tasks": [{"TaskType": "Roguelike", "CoreChar": "维什戴尔"}]}), "6.17.5")
    T("form-6180", template_form({"tasks": [{"TaskType": "Roguelike", "StartingOpers": []}]}), "6.18.0")
    T("form-none", template_form({"tasks": [{"TaskType": "Fight"}]}), "")
    # 凭据红线
    T("scrub", scrub({"a": 1, "Password": "x", "b": {"Token": "y", "c": 2}}), {"a": 1, "b": {"c": 2}})
    # 写端点白名单
    try:
        api_post("/api/scripts/delete", {}, writes=True, timeout=1)
        T("write-allowlist", "accepted", "rejected")
    except RuntimeError:
        T("write-allowlist", "rejected", "rejected")
    except Exception:
        T("write-allowlist", "net", "rejected")                  # 不该发生（白名单先抛）
    # 双闸（不触网）
    T("apply-env-name", APPLY_ENV, "MAAORCH_SCENARIO_APPLY")
    T("yield-zones", YIELD_ZONES, [("04:00", "05:33"), ("18:30", "20:30")])

    fails = [c for c in cases if c[1] != c[2]]
    for name, got, want in cases:
        print("%s %-26s got=%r want=%r" % ("PASS" if got == want else "FAIL", name, got, want))
    print("selftest: %s (%d/%d)" % ("OK" if not fails else "FAILED", len(cases) - len(fails), len(cases)))
    return 0 if not fails else 2


# ============================================================ main
def main() -> int:
    ap = argparse.ArgumentParser(description="按场景发车（肉鸽/活动/剿灭）—— dry-run 默认，真发车需批准")
    ap.add_argument("--script", help="脚本名（精确匹配）")
    ap.add_argument("--instance", help="或：MuMu 实例号（按 Emulator.Index 反查）")
    ap.add_argument("--scenario", help="场景：rogue/event/annihilation（或 肉鸽/活动/剿灭）")
    ap.add_argument("--times", type=int, help="次数（映射字段见模板 times_path；肉鸽=StartCount）")
    ap.add_argument("--window", help="时间窗 HH:MM-HH:MM（发车须在窗内；窗止=自动还原时刻）")
    ap.add_argument("--duration", type=int, help="替代窗尾：现在起 N 分钟")
    ap.add_argument("--template", help="模板路径（默认 runtime/templates/maa/<场景>.json）")
    ap.add_argument("--set", action="append", metavar="PATH=JSON",
                    help="点点路径覆盖，如 Task.ActivityStageIndex=3 / TaskQueue.Roguelike.StartCount=1")
    ap.add_argument("--apply", action="store_true", help="真发车（草案双闸默认关闭）")
    ap.add_argument("--force", action="store_true", help="忽略同日单飞记录（审计留痕）")
    ap.add_argument("--allow-no-preflight", action="store_true", help="preflight 未落地时放行（审计留痕）")
    ap.add_argument("--offline", action="store_true", help="跳过一切只读探测（纯本地计划/自检）")
    ap.add_argument("--reconcile", action="store_true", help="对账：还原到期运行")
    ap.add_argument("--restore-run", metavar="RUN_ID", help="手动还原某次运行")
    ap.add_argument("--status", action="store_true", help="列出运行台账")
    ap.add_argument("--list-scripts", action="store_true", help="只读：脚本/实例表")
    ap.add_argument("--with-users", action="store_true", help="--list-scripts 附带用户")
    ap.add_argument("--selftest", action="store_true", help="纯本地自检")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.status:
        return cmd_status(args)
    if args.list_scripts:
        return cmd_list_scripts(args)
    if args.restore_run and args.apply:
        return restore_run(args.restore_run, dry=False, note="手动还原")
    if args.restore_run:
        return restore_run(args.restore_run, dry=True)
    if args.reconcile and args.apply:
        return cmd_reconcile(args)
    if args.reconcile:
        return cmd_reconcile(args)
    if not args.scenario:
        ap.print_help()
        return 2
    if not (args.script or args.instance):
        print("需要 --script 或 --instance", file=sys.stderr)
        return 2

    # --set / --times 的落点要在 plan 里体现：这里做一次纯本地预演（不触写）
    if args.apply:
        return do_apply(args)
    plan = build_plan(args)
    plan["applying"] = False
    # 预演 --times / --set 的解析（不触写）
    if not plan["refused"]:
        prev = {"task": {}, "config": [dict(t) for t in (resolve_template(plan["scenario"], args.template).get("tasks") or [])],
                "times_path": plan.get("times_path")}
        msgs = []
        if args.times is not None and prev["times_path"]:
            ok, m = set_path(prev, prev["times_path"], args.times)
            msgs.append(m if ok else "times 未落地：" + m)
        for s in (args.set or []):
            if "=" in s:
                k, v = s.split("=", 1)
                try:
                    v = json.loads(v)
                except Exception:                                # noqa: BLE001
                    pass
                ok, m = set_path(prev, k.strip(), v)
                msgs.append(m)
        plan["preview_writes"] = msgs
    print_plan(plan, args.json)
    return 0 if not plan["refused"] else 1


if __name__ == "__main__":
    sys.exit(main())
