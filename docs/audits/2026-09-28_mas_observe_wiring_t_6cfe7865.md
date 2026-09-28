# 落地审计：mas-observe / mas-triage(error_daily) 接入网关工具面 + MCP-stdio env 自愈

- 任务：t_6cfe7865（板 maa-parallel-benchmark-20260926）· 2026-09-27 23:00 – 09-28 01:40
- 范围：host 侧工具代码 + `profiles/mas/config.yaml`（**未**动远端生产配置、未发车、未杀进程）
- 目标：① 巡检工具面出现 `mas-observe` 5 工具与 `mas-triage.error_daily`；② 经 MCP-stdio 派生的 ssh 子进程 env 残缺（rc=255 / WinError 2）根因修复并实跑验证

## 0. 终态（2026-09-28 01:34 巡检实跑，全为工具返回真值）

```
MCP_API_TOOL_COUNT=27 | ISOLATION=PASS          # monitor10/daily6/dispatch4/observe5/triage2
CLIENT_STATE=未知×2、桌面×2（idx3=未知；idx11=桌面；idx12=未知；idx13=桌面）
ERR_SUMMARY=103 条，Top=未知（fatal=64/warn=10/benign=29；login=33；escalated=true）
IDENTITY=一致（red_rounds=0；官-2-4-47=2轮、官-1-1-7=0轮）
POWER=0 建议=无（running14/in_use6/needed8；参考在飞 MAA=6）
SWITCH_GATE=VIOLATION:MAA_10,MAA_12（已知项，见 §4）
```

## 1. 三个根因与修复

### 1.1 env 大小写双键（裸名 `ssh` 秒败的真凶）

- 现象：`mas_idle_emulators` 经 MCP 0.2s `FileNotFoundError: [WinError 2]`；`mas_identity_trail` 拉日志失败；POWER 行 `SSH 上传失败 WinError 2`。
- 根因：`mas_observe_mcp._env()` 把 registry 值**按原大小写**合并（HKCU `Path`）进已含 `PATH` 的字典；Windows 进程环境块大小写不敏感 → 子进程实际生效「后写键」= 用户版 `Path`（无 System32/OpenSSH）→ 裸名 `ssh` 不可解析。
- 修复（两处同款）：
  - `tools/mas_observe_mcp.py::_env()` — 键名统一大写；`PATH` 两段（继承 + registry）显式合并去重；`System32\OpenSSH` 前置。
  - `tools/maa_error_daily.py::_child_env()` — 同上（该脚本原本就用绝对 ssh 路径，修复消除双键隐患）。
- 证据：`confirm_path_case.py`（唯一 `PATH`、首位 OpenSSH、子进程 `which ssh`=System32\OpenSSH\ssh.EXE）；残 env 对照：修前 4.4s fail → 修后 `rc=0 / SSH=ok`；idle 复现 `rc=0` 出真值 JSON。
- 哈希：`mas_observe_mcp.py=92404b40…`、`maa_error_daily.py=44b7c13b…`；备份 `tools/*.bak-t6cfe7865-*.{envfix,casefix}`。

### 1.2 工具面热加载机制（reconciler 只增删，不刷新已连接 server）

- 现象：config 写入后工具面迟迟不见新 server；`error_daily` 已写成但面上仍是旧 schema。
- 机制：网关 `mcp_config_reconciler` 仅处理「新增/移除/禁用」；已连接 server 的工具 schema 不会自动刷新。
- 操作：`hermes -p mas config set mcp_servers.<X>.enabled false → true` 强制重连。实测：01:03:03 `removed=['mas-observe','mas-triage']` → 01:04:05 `added=[…]` + `registered 5 tool(s)` + `registered 2 tool(s)`。
- 同时补 `timeout: 900`（两 server，config diff 仅 +2 行，注释保留）。

### 1.3 identity 420s 超时（全量拉取 20MB + 日志占用共享冲突）

- 现象：修复 1.1 后 `mas_identity_trail` 不再早死，但 420s 超时（预算上限）。
- 根因 A：`report` 全量 `cmd type` 拉 gui×2 + app.log ≈ **20MB**；远机负载下有效带宽 ~100KB/s，仅传输就 >200s，峰值期更差。
- 根因 B（实现坑）：远端 PS 预过滤初版用 `[IO.File]::ReadLines`，日志被 MAA/AUTO-MAS 写入占用 → 共享冲突异常被 `$ErrorActionPreference='Continue'` **静默吞成空输出**（gui2/app 曾全空）。必须 `[IO.File]::Open(..., FileShare::ReadWrite)` + StreamReader 共享读。
- 修复：`tools/mas_identity_trace.py`
  - `pull_remote_text(path, filters=…)` — 远端 PS 预过滤（UTF8 共享读 + 字面量并集正则，`-EncodedCommand` 免引号地狱）；
  - `GUI_PULL_FILTERS=["Task Chain","->>"]`、`APP_PULL_FILTERS=`8 个解析锚点；
  - 仅 `report` 的两处调用启用（其余路径不变）。
- 证据：
  - 体量/时延：20MB → 0.64MB，**420s+ → 27.7s**；
  - **等价性**：filtered 版 JSON rounds 与全量版逐字段一致（00:44 ⚠️ + 01:07 PASS，RED=0）；
  - MCP 全链路：`mas_identity_trail` 30.2s `identity="一致"`。
  - 哈希 `mas_identity_trace.py=a9cce432…`；备份 `.bak-t6cfe7865-*.pullfilter`。

## 2. 验证矩阵（全部实跑）

| 检查 | 结果 |
|---|---|
| `mas_observe_mcp.py --check` | OK；5 工具；5 CLI selftest 全 OK；readonly_assert=PASS |
| `py_compile` 三文件 | COMPILE_OK |
| reduced-env 对照（errdaily） | 修前 4.4s fail → 修后 rc=0/SSH=ok |
| MCP 探针（stdio 全链路） | identity 30.2s「一致」；client_state 86.3s 真值；report 84.5s 真值 |
| 网关重连日志 | 01:03:03 removed / 01:04:05 added + 5/2 tool(s) |
| 巡检实跑（01:34） | 27 工具 ISOLATION=PASS；五真值行全落地（见 §0） |

## 3. 回滚

- 强制重连失败时：`config set enabled false` → 观察 removed → 恢复 `true`。
- 回滚到修复前行为：用 §1 标注的 `.bak-t6cfe7865-*` 恢复对应文件；`timeout: 900` 行删除即可。
- 全案回滚：从 `platform_toolsets.cron` 摘除 mas-observe 行 + 删 `mcp_servers.mas-observe` 段（roadmap §4.2 原方案仍有效）。

## 4. 遗留（非本卡范围）

- `WIKI_GOV=UNKNOWN:工具面未提供（mas-wiki 未接线）` —— 待 mas-wiki 接入（其卡）。
- `SWITCH_GATE=VIOLATION:MAA_10,MAA_12` —— 已由 t_06801d45 定因（轮末还原残留；`2026-09-27_switch_gate_two_violations_rootcause.md` §6 清理方案待 lead 拍板），**非新问题**。
- `auto-mas-monitor` 上游间歇 `CancelledError`（flappy）——出现时计数退为 17；上游恢复即 27（01:17:27 已实达 27）。
- 巡检提示词预算仍写「上限 15 次/18 次」（历史版本），本轮 13 次余量正常；如需按新五检收紧预算另开卡。
