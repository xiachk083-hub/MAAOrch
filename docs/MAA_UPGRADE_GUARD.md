# MAA 升级护栏（MAA_UPGRADE_GUARD）

> 定位：MAA 会自己更新（程序本体走 `MAA.Updater.exe`，`resource\` 资源随版本走），而它的 **GUI 配置 schema 会随大版本迁移**——6.x 把「账号切换」从全局键搬进 `StartUpTask.AccountName/AccountSwitchEnabled`，AUTO-MAS 注入层没跟上 ⇒ **切号静默失效数月**（任务照样跑完，跑的是别人的号）。
> 所以升级不是「装完就行」：**升级前**把「现在是什么样」变成可比对的事实，**升级后、下一班次发车前**校验注入面，**出问题**按本文回滚。
> 工具：`tools/maa_upgrade_check.py`（默认只读；唯一外部写动作 = `live` 子命令的官方 start/stop）。配套：`mas_switch_gate`（注入合规闸门）、`mas_patch_guard`（补丁面）、`mas_config_snapshot`（配置面）、`mas_preflight`（发车前 preflight）。

## 何时用

- MAA 弹更新、有人点了「安装更新」前后；
- 怀疑「切号怎么又不对了 / 注入到底生不生效」（先 `verify`，再按 §3 处置）；
- 要做整机 MAA 升级的排期（§2 流程 + §4 回滚卡）；
- 新槽位、新版本基线整理（`snapshot --all` 把退役目录也收进基线）。

## §1 判什么：三层判据（别凭记忆改）

| 层 | 判据 | 落在哪 |
|---|---|---|
| ① 程序身份 | `MAA.dll` ProductVersion（带 commit，形如 `v6.18.0+23feeddc538de4e10646b6`）+ `MaaCore.dll` sha256（MaaCore 无版本号，只能哈希认） | 每个槽目录 `E:\MAA-Multi\MAA_N\` |
| ② 资源身份 | `resource\tasks\tasks.json` sha256（MaaCore 任务图；6.18.0 = 1090 节点）+ `resource\version.json`（游戏活动/卡池面）+ `cache\version\summary.json`（上游版本面：`stable=v…/alpha=v…`） | 同上 |
| ③ 注入面 | `config\gui.new.json` 的 TaskQueue 字段与取值（`WATCH_TASK_TYPES` × `WATCH_FIELDS`），**这一层才是 AUTO-MAS 真正写、MAA 真正读的东西，也是历史上被打穿的那一层** | 同上 |

- 「升级≠故障」：程序/资源变化只记 `changed`（报告点名），**判定失败只看「我们依赖的字段/取值还在不在」**。
- 账号注入合规不另立口径：一律复用 `mas_switch_gate`（官方 masked+`true` / 非官 empty+`false`，fail-closed）。
- 两个已知噪声，先记住再读报告（详见 §3 D2）：
  - AUTO-MAS 每次跑完/开跑都会重写 `tasks.json`（`agree_bilibili`）⇒ **B 服槽位的 tasks_sha 会振荡**（实机样：`38e897ce…` ↔ `7b172da9…`，差一个节点）；
  - `tasks.json` / `gui.new.json` 的 **mtime 不是判据**（每轮任务收尾都会写），只认 sha 与字段值。

## §2 标准流程

### 2.1 升级前（任何版本变更开始前，5 分钟）

```bash
# ① 建基线（12 槽；含退役目录加 --all）——记下 stamp
python tools/maa_upgrade_check.py snapshot

# ② 配置面快照（X3 工具，带标签；主机侧也留一份）
python tools/mas_config_snapshot.py --label pre-maa-upgrade

# ③ 补丁面闸门（记录注入补丁在位）
python tools/mas_patch_guard.py --check        # 末行 PATCH_GUARD=OK

# ④ 回滚凭据（若确定要升级：备份程序+资源，见 §4.1/§4.2）
```

### 2.2 升级（人工点装）

- 实测本队策略（`gui.new.json`）：`VersionType=Stable`、`CheckOnStartup=true`、`CheckOnSchedule=false`、`AutoDownloadUpdatePackage=true`、`AutoInstallUpdatePackage=false` ⇒ **自动下载、安装需人工点**——好在「升级前可快照」的窗口天然存在。
- 升级逐槽发生（每槽一份 `MAA.exe/MAA.dll/MaaCore.dll`）。挑**班次窗口外**做（05:00–08:00 / 19:30–23:00 别动），别在有 MAA 在跑时点安装。
- 安装动作只动 MAA 自身目录，**不动 AUTO-MAS**（侧车不重启；窗口内重启侧车会吞在飞任务）。

### 2.3 升级后（下一班次发车前，必做）

```bash
# ① 差异报告：基线 vs 现状（只读；新 stamp_verify）
python tools/maa_upgrade_check.py verify

# ② 注入→读回实测（窗口外、挑空闲槽的脚本；默认 dry-run，--apply 才真发车）
python tools/maa_upgrade_check.py live --script 明日方舟-官-23            # 先看 gates
python tools/maa_upgrade_check.py live --script 明日方舟-官-23 --apply    # 发车→读回→官方 stop

# ③ 补丁面 + 闸门复核
python tools/mas_patch_guard.py --check        # PATCH_GUARD=OK
python tools/mas_switch_gate.py --check        # SWITCH_GATE=OK

# ④ 回滚凭据（若 ② 失败且决定回滚：照 §4.3 走）
```

- **放行口径**：`verify` 无 `DRIFT`/无新增 `VIOLATION`（既有事件面违规除外）＋ `live` ∈ {OK, PARTIAL} ＋ ③ 两项 OK ⇒ 可发车。
- `live` 判词：`OK` = 读回对 + 见「账号切换」新行；`PARTIAL` = 读回对、未见切换行（同号重登不产生切换行 / stop 早于 MAA 启动，均常见）；`FAIL` = 读回与期望不符 ⇒ 按 §3 D7 处置；`STOP-FAILED` = 车没停干净，立刻人工确认（工具已按 stop 应答 `code=200` 判定，不会再现 2026-09-28 那种假阴性）。
- 升级后**首班**跑完再看一眼 `mas_switch_gate --check` 与班次复盘（首班真实数据是最终验收）。

## §3 漂移处置表

| 编号 | 报告现象 | 含义 | 处置 |
|---|---|---|---|
| **D1** | 程序身份变化（`程序变化槽位` 非空，`v6.18.0+…` → 新号） | 真升级（或降级） | 正常：走 §2.3 三步补齐验收。若 ② 失败 ⇒ 转 D7 |
| **D2** | 资源 `tasks.json` 变化（`资源变化槽位` 非空 / 「二、节点级差异」有增删） | 节点进出 | ① 差异**仅** `BilibiliAgreement_AUTO` ±1（报告会标「ℹ️ …非升级漂移」）⇒ AUTO-MAS `agree_bilibili` per-run 注入，**忽略**；② 涉及 `WATCH_NODES`（切号/战斗/基建/公招/商店/领奖/掉线）增删改 ⇒ 按影响链评估，涉及切号/战斗视同 D7；③ 纯新增（如新活动节点）⇒ 通常安全 |
| **D3** | `DRIFT`：注入面 schema 漂移（`WATCH_FIELDS` 键缺失 / 任务类型消失） | **就是要防的那种**：升级打穿了注入层 | **禁止发车**（当晚先不发）；二选一：按 §4 回滚，或改 AUTO-MAS 注入层（`AutoProxy.py` 补丁流程 → `mas_patch_guard --reapply --apply` → 重启侧车；窗口外） |
| **D4** | `VIOLATION:MAA_xx`（闸门） | 注入合规问题（官方未 masked+`true` 等） | 未必升级引起（多数是事件面）；按 `mas_switch_gate` 口径逐槽处置；**升级后首次出现**才先怀疑 D3 |
| **D5** | `UNKNOWN`（读不到/判不了） | fail-closed，**不算通过** | 查 SSH/REST 可达性、目录权限；修好后重跑。没跑通不放过 |
| **D6** | `live` = `PARTIAL` | 注入读回 OK、未见切号行 | 多数无害（见 §2.3 判词）；连续多次且实机帧证（`mas_identity_trace`）不符 ⇒ 转 D7 |
| **D7** | `live` = `FAIL`（读回与期望不符） | 注入层打穿（等同 D3 实锤） | 禁止发车 + §4 回滚（或补丁修复后重验） |

补充：`STOP-FAILED` 不进表——它不是漂移判定，是事故信号：先人工确认任务真的停了（`mas_ondemand.py --list-running` / 主机侧 MAA.exe），必要时再 stop 一次。

## §4 回滚操作卡（MAA 程序/资源侧）

> 本卡补的正是「带外回滚演练」（`docs/audits/2026-09-28_rollback_drill.txt`）**没覆盖**的那块：演练覆盖了配置面/补丁面/定时面，唯独没有 MAA 程序与资源。
> 回滚三项铁律：**窗口外**做、**没有在飞任务**、回滚后**验证再发车**。

### 4.1 备份什么（体积为 2026-09-28 实测）

| 对象 | 内容 | 体积 |
|---|---|---|
| 官方 12 槽 | `E:\MAA-Multi\MAA_{1..12}` 全目录（退役 13/14/25/28 可选） | 单槽 **610–854 MB**，20 个目录合计 **11,369 MB** |
| 其中最小集（省时省空间） | 程序五件套 `MAA.exe` 359 KB、`MAA.dll` 4.6 MB、`MaaCore.dll` 5.9 MB、`MAA.Updater.exe` 340 KB、`filelist.txt` 756 KB ＋ `resource\` ≈252 MB | ≈**265 MB/槽** ⇒ 12 槽 ≈ 3.2 GB |
| 母版（新槽由此生成） | `E:\MAA-Multi\` 根下 `MAA.*`/`Maa*.dll`/`onnxruntime_maa.dll`/`opencv_world4_maa.dll`/`DirectML.dll`/`fastdeploy_ppocr_maa.dll`/`hostfxr.dll`/`hostpolicy.dll`/`filelist.txt` ＋ `resource\`(246 MB)、`externals\`(202 MB)、`Res\`、`Python\` | ≈450 MB |

- 目标目录：`E:\MAA-Multi\backups\upg_<stamp>__pre-<ver>\`（**现无此目录**，先建；`E:` 盘 2026-09-28 剩 **150,832 MB ≈ 147 GiB**，全量 20 槽放得下）。
- 时机：**就在点「安装更新」前几分钟**（这样全量镜像回滚 = 回到升级瞬间，最干净）。

### 4.2 备份命令（主机侧 cmd；robocopy 退出码 0–7 都算成功）

```bat
:: 全量（推荐；对每个 MAA_1..12 循环。交互 cmd 用 %D，.bat 文件用 %%D）
set STAMP=20260928_1100
set VER=pre6180
mkdir "E:\MAA-Multi\backups" 2>nul
for %D in (1 2 3 4 5 6 7 8 9 10 11 12) do robocopy "E:\MAA-Multi\MAA_%D" "E:\MAA-Multi\backups\upg_%STAMP%__%VER%\MAA_%D" /E /R:1 /W:1 /NFL /NDL /NP

:: 母版程序文件
robocopy "E:\MAA-Multi" "E:\MAA-Multi\backups\upg_%STAMP%__%VER%\_master" MAA.exe MAA.dll MaaCore.dll MaaAdbControlUnit.dll MaaUtils.dll MaaWin32ControlUnit.dll onnxruntime_maa.dll opencv_world4_maa.dll DirectML.dll fastdeploy_ppocr_maa.dll hostfxr.dll hostpolicy.dll MAA.deps.json MAA.runtimeconfig.json MAA.Updater.exe filelist.txt /R:1 /W:1 /NFL /NDL /NP
```

最小集（空间/时间紧时）：把 `/E` 换成对五件套 `/IF` 的 copy，另加一条 `resource\` 的 `/E` 镜像。

### 4.3 回滚步骤

1. **确认窗口与空闲**：班次窗口外；`python tools/mas_ondemand.py --list-running` 无在飞任务；目标槽无 `MAA.exe`。有在飞任务先走官方 stop（不要 kill）。
2. **反向镜像**（推荐全量 `/MIR`，前提 = 备份取自升级前几小时内）：
   ```bat
   robocopy "E:\MAA-Multi\backups\upg_<stamp>__<ver>\MAA_1" "E:\MAA-Multi\MAA_1" /MIR /R:1 /W:1 /NFL /NDL /NP
   :: 母版：同命令把备份 _master 拷回 E:\MAA-Multi 根（不加 /MIR，避免误删根下其它目录）
   ```
   - `/MIR` 会把升级引入的**新文件也删掉**——这正是要的；但**更早的备份**（隔半天以上）别用 `/MIR`：先只拷回旧文件、人工确认后再清理新增文件。
   - **不需要重启 AUTO-MAS 侧车**（MAA 文件与侧车无关）；**别**借机在窗口内重启侧车。
3. **验证回滚成功**（照 §2.3 的 ①②③ 跑一遍）：
   - `snapshot` 新 stamp 的程序 sha / 核 sha 应回到升级前 baseline 的值（对比 `runtime/upgrade_guard/<stamp>/baseline.json`）；
   - `mas_patch_guard.py --check` = `PATCH_GUARD=OK`；`mas_switch_gate.py --check` = `SWITCH_GATE=OK`；
   - 想更稳：窗口外跑一次 `live --apply`（读回对即闭环）。
4. **恢复发车**：下一班次自然发车（被跳过的班次按 `mas_dispatch` 口径人工补发）。
5. **留档**：把回滚原因/时间/涉及槽位/验证输出写进 `docs/audits/`（编号 + 一句话结论）。

### 4.4 与配置面/补丁面/定时面回滚的衔接

- 配置面：`python tools/mas_config_snapshot.py --list` 找 `pre-maa-upgrade` 快照 → `--restore <ts>`（dry-run）→ `--apply`；演练已验（drill E 段）。
- 补丁面：`python tools/mas_patch_guard.py --reapply <补丁名> --apply` + 侧车重启（窗口外）；演练已验（drill A/B 段）。
- 定时面：`python tools/mas_timers.py --enable` + Hermes cron pause（两条一起做，见 auto-mas-host-ops「发车调度」）。
- MAA 升级**不动**上面三项，正常升级不需要动它们；回滚时若连带改过 AutoProxy 代码/模板，才走 4.4 的补丁/配置路径。

## §5 差异报告怎么读

报告落 `runtime/upgrade_guard/<stamp>_verify/report.md`（+ `diff.json`）。结构：判定 → 一、升级是否发生 → 二、tasks.json 节点级差异 → 三、逐槽差异 → 五、注入闸门 → 七、处置建议。

### 5.1 实样一：正常的一天（无升级）

`20260928_102347_verify/report.md`：`程序变化槽位：无`、`资源变化槽位：无`、判定 **VIOLATION**——但唯一来源是「五、注入闸门」的 `MAA_10/MAA_12: official-switch-not-true`（已知事件面，与升级无关）。
读法：**先看一/二节有无变化，再看五节违规归属**；「没有程序/资源变化」时判定基本都是事件面。

### 5.2 实样二：B 服 per-run 注入（benign，学到的那一课）

今日基线 `20260928_101942` 里 MAA_9 的 tasks_sha = `7b172da9…`，其余 11 槽 = `38e897ce…`：当时 MAA_9 正跑 B 服脚本（b-3，10:09 发车），AUTO-MAS `agree_bilibili` 往它的 `tasks.json` 插了 `BilibiliAgreement_AUTO` 节点（`StartUpThemes.next` 多一行）；跑完会删回去。
节点级 diff = `+1 节点：BilibiliAgreement_AUTO`，**工具标 benign**。⇒ 见到「B 服槽位 tasks_sha 漂」不要当故障（§3 D2-①）。

### 5.3 实样三：跨大版本（6.16.8 → 6.18.0）

完整样例：`docs/audits/2026-09-28_maa_6168_to_6180_tasks_diff.md`（由 fixtures 直接产出）。
要点：节点 869 → 1090（**+224 / −3**）；`WATCH_NODES` 全部在位（`SwitchAccount@StartUpBegin`、`StartUp`、`Fight/…/Recruit/Infrast/Mall/Award/OfflineConfirm`）；新增大头 `OperProgress`（96，干员养成）、`InfrastAssistant*`（87，基建助手）——**「新能力/新关卡」型**，不改变我们链的走法。
读法：
- 关注节点**全在** + 注入面字段无漂移 ⇒ 升级对链的中性影响，放行；
- 切号相关节点（`SwitchAccount@StartUpBegin`/`StartUpBegin`/`OfflineConfirm`）**消失或改名** ⇒ 按 D7/§4 走（切号链可能已打穿）；
- `Fight@StartButton`/`Recruit`/`Mall`/`Award`/`Infrast` 消失 ⇒ 对应 AutoProxy 子任务链抽查先停，别带病发车。

## §6 工具面（tools/maa_upgrade_check.py）

| 子命令 | 作用 | 写面 |
|---|---|---|
| `snapshot` | 建基线：12 槽（REST 推导；`--all` 含退役 13/14/25/28）身份三元组 + 注入面 + tasks 原件 + 闸门 | 只读远端 |
| `verify` | 基线 vs 现状 → `report.md` + `diff.json`（`--baseline <stamp>` 指定） | 只读 |
| `live` | 注入→读回实测（默认 dry-run；`--apply` 真发车；班次窗口/占用槽/有 live 锁时拒绝或 BLOCKED） | 唯一外部写 = `POST /api/dispatch/start|stop` |
| `show` | 最近基线摘要 | 只读 |
| `--selftest` | 离线自测（35 项，假传输不连网） | 不连网 |

- 常用参数：`--dirs`（自定义槽）、`--script`（名/前缀/sid）、`--timeout/--poll`（读回轮询）、`--force-window`（窗口内跑，危险）、`--no-tasks-pull`（省时间）、`--json`。
- `live` gates：`NO_GO`（`MAAORCH_UPGRADE_NO_GO=1`/`MAAORCH_DISPATCH_NO_GO=1`）→ `REST` → `TARGET` → `WINDOW`（班次窗口拒绝）→ `PROBE`（槽位配置读不到）→ `IDLE`（有 MAA.exe 换槽）→ `LOCK`（已有 live 在跑）→ `DISPATCH`（发车应答）。
- 判词：`verify` ∈ {`OK`, `DRIFT`, `VIOLATION`, `UNKNOWN`}；`live` ∈ {`DRY-RUN`, `OK`, `PARTIAL`, `FAIL`, `STOP-FAILED`, `BLOCKED`}。
- 退出码：`0` 无漂移；`1` 需处置（`PARTIAL`/`FAIL`/`DRIFT`/`VIOLATION`）；`2` 读不到/判不了（`UNKNOWN`/`BLOCKED`/`STOP-FAILED`，不当通过）。
- 产物：
  - `runtime/upgrade_guard/<stamp>/baseline.json`、`<stamp>/tasks/<sha16>.json`、`<stamp>/gui/<槽>.gui.new.json`
  - `runtime/upgrade_guard/<stamp>_verify/report.md`（+ `diff.json`）
  - `runtime/upgrade_guard/current.json`（最近基线指针）、`log.jsonl`（每次一行）、`runtime/maa_upgrade_check.lock`（live 单飞锁）
- 凭据红线：**基线不含凭据**——掩码手机号是身份不是秘密；账号明文 Id/密码不落盘。
- 环境变量：`MAAORCH_UPGRADE_TIMEOUT`（默认 240）、`MAAORCH_UPGRADE_POLL`（默认 15）、`MAAORCH_UPGRADE_NO_GO` / `MAAORCH_DISPATCH_NO_GO`（硬闸）。
- 停 live 车：只走官方 stop（工具自己做）；**绝不** kill MAA 进程/重启侧车。

## §7 与其它护栏/流程的关系

```
升级前           升级             升级后（下一班次发车前）
snapshot ──→ [装新版本] ──→ verify ──→ live ──→ patch_guard/switch_gate ──→ 发车
   │                         │            │
   └── mas_config_snapshot   └─ 见 §3 D1..D7 ──→ 异常 ⇒ §4 回滚（回滚也要验证）
```

- `mas_switch_gate`：注入合规的**单一口径**（本工具直接复用，不另立标准）。
- `mas_patch_guard`：AUTO-MAS 补丁面的漂移检测；升级 MAA 不动它，但回滚/补丁修复后都要复核。
- `mas_config_snapshot`：配置面快照/回滚（X3 工具）；升级前的 ② 步。
- `mas_preflight`：发车前综合体检，可与 `verify` 串用（升级后首班建议手动跑一遍 §2.3）。
- 建议接线（未接线，待拍板）：30 分钟巡检加一个只读位 `python tools/maa_upgrade_check.py verify`，把「程序/资源变化 + DRIFT/VIOLATION」带进巡检上报。

## 附：今日实测记录（2026-09-28）

- 自测：`--selftest` **35/35**（含实机回归项：stop 应答口径 / 陈旧切号行防伪 / B 服 benign）。
- 基线：`20260928_101942`（12 槽均 `v6.18.0+23feeddc538de4e10646b6`）。
- verify：`20260928_102347_verify` ⇒ 无程序/资源变化；`VIOLATION` 仅事件面 `MAA_10,MAA_12`。
- live 实测（`runtime/upgrade_guard/live_guan23_run2.json`）：`明日方舟-官-23` @ `MAA_1`，窗口外、空闲槽；**读回成功**：`StartUp=185****3616/true`、`Fight=[SR-7, 1-7]`（吃药 off/0）；`stop` 应答 `code=200` 判 `OK`；判定 `PARTIAL`（MAA 未及启动即停，无切换行）——注入面按预期工作。
- 跨版本样例：`docs/audits/2026-09-28_maa_6168_to_6180_tasks_diff.md`。
- 回滚数据：见 §4.1（体积/路径/命令均实测）。
