# 定因：闸门首夜两例官方槽违规（MAA_10 / MAA_12 masked+false = 轮末还原残留）

- 任务：t_06801d45（mas）· 2026-09-27 22:20–23:00 · **全程只读**（未写任何生产文件、未改 AUTO-MAS/MAA 配置、未发车、未 kill）
- 证据源：①MAA v6.18.0 源码（GitHub tag v6.18.0，clone 校验 commit `23feeddc`，行号逐条实读）；②主机只读读取（`E:\MAA-Multi\MAA_N\config\gui.new.json` 字段/mtime/md5、app.log、gui(.bak).log、asst(.bak).log、`E:\AUTO-MAS\app\task\MAA\manager.py`）；③`tools/mas_switch_gate.py` 实跑（22:25:35 / 22:39:13 两次一致）。
- 约束遵守：结论逐条带出处；未执行任何清理写（写方案见 §6，留给 lead 拍板）。

---

## 0. 结论摘要（按卡内四问）

**Q1｜`AccountSwitchEnabled=false + 非空 AccountName(掩码)` 对「该槽下次启动」的真实行为：不会切号。**
掩码会被 MAA 当哑值丢弃：GUI 序列化判定 `AccountSwitchEnabled is true` 不成立 ⇒ 下发给 core 的 `account_name=""`（`StartUpSettingsUserControlModel.cs` L94-99）⇒ core `set_enable(!account_name.empty())` = false（`StartUpTask.cpp` L96）⇒ AccountSwitchTask 直接 `task disabled, pass`（`AbstractTask.cpp` L31-34）、**不做任何账号动作，停在客户端当前已登录的号**上照常跑完（不报错、无账号相关日志）。
- 对 **AUTO-MAS 驱动的运行无实际影响**：每号 spawn 前注入先重写该文件（v2 后恒写合规值）——今晚 in-flight 实测见 §2.3。
- 对**出带启动**（人工开 MAA 等不经注入的启动）才会吃这个静默态：操作者「以为会切到掩码号、实际不切」。

**Q2｜还原窗口的 masked+false 是否属可容忍残留：在带无害，但长期不可放任 ⇒ 判「一次性清理项」。**
它是**还原不动点**：轮末 `final_task` 用轮起快照整份覆盖回槽位 ⇒ 每次轮末都把同一个旧值写回，下一轮再快照同一个值 ⇒ 不清就**每晚复现违例、永久 false**（MAA_12 今日 7 个任务全部写回 09-26 旧态，实证 §3.2）。清一次即永久合规（合规同样是不动点）。出带启动下它还是「静默不切号」面。⇒ 不属于「可容忍、只存档」。

**Q3｜口径 v3 建议：判定规则保持不动（不加「轮末还原窗口」例外），只增不改。**
理由与增补（违规条目补 `mtime/age` 注记 + 巡检分诊 fresh/stale）见 §5。核心论证：该例外类可被清理到零，且清理后本应永不复现——为不会再出现的类加例外，等于给未来异常（手改/新遗留/注入回归）开后门。

**Q4｜清理：最小写方案 = 受控播种**（MAA_10 / MAA_12 各 1 处布尔翻转 `false→true`），完整前置检查/命令/读回/回滚/验证与**预期 `SWITCH_GATE` 变化：`VIOLATION:MAA_10,MAA_12` → `OK`** 见 §6。
- **「次日队列应用自然覆盖」不成立**（清不掉）——机制论证 + 实证 §3.2。
- 本卡未执行（只读约束）。

---

## 1. 症状复核（本次实读，与 parent 22:14 一致）

gate 22:39:13：`REST=ok 槽位=12 脚本=54`，12 槽表：

| 槽 | 服务器 | 账号注入 | 开关 | 判定 | mtime（字段=文件 mtime） |
|---|---|---|---|---|---|
| MAA_1 | Official | 185****3616 | true | OK | 2026-09-27 19:54:51 |
| MAA_2 | Official | 192****7973 | true | OK | 2026-09-27 19:55:33 |
| MAA_3 | Official | 158****4013 | true | OK | 2026-09-27 19:59:21 |
| MAA_4 | Official | 171****6354 | true | OK | 2026-09-27 20:03:37 |
| MAA_5 | Official | 171****6036 | true | OK | 2026-09-27 20:07:44 |
| MAA_6 | Official | 134****5276 | true | OK | 2026-09-27 20:02:12 |
| MAA_7 | Official | 182****2399 | true | OK | 2026-09-27 19:49:40 |
| MAA_8 | Official | 151****9793 | true | OK | 2026-09-27 19:57:58 |
| MAA_9 | Bilibili | （空） | false | OK | 2026-09-27 05:51:52 |
| **MAA_10** | Official | **186****6119** | **false** | **VIOLATION** | **2026-09-27 05:56:42** |
| MAA_11 | Bilibili | （空） | false | OK | 2026-09-27 05:55:38 |
| **MAA_12** | Official | **177****2151** | **false** | **VIOLATION** | **2026-09-26 06:13:09** |

- 两例 issue 均单条：`official-switch-not-true`；掩码均在各自槽 `expected_masks` 内（186****6119∈MAA_10；177****2151∈MAA_12）。
- 文件字节级复核（22:33）：MAA_10 `md5=086F1E0CF7A3D47AD41BF6D040F67250 size=16063`；MAA_12 `md5=1C8D758D84FA72A0531DC23887A824E8 size=16062` —— 与 22:14 parent 读取**完全一致**（期间无净变化）。
- 首 3 字节 = `123,13,10`（`{\r\n`）⇒ UTF-8 **无 BOM**（写方案注意事项，§6.2）。

---

## 2. Q1 定因：组合行为（MAA v6.18.0 源码逐行 + 实测锚点）

### 2.1 链路（开关=false 时掩码被丢弃）

| 步 | 位置 | 行为 |
|---|---|---|
| ① GUI 序列化 | `src/MaaWpfGui/ViewModels/UserControl/TaskQueue/StartUpSettingsUserControlModel.cs` **L94-99** | `accountName = 非PC && client∈{Official,Bilibili,Txwy,KR} && startUp.AccountSwitchEnabled is true ? startUp.AccountName : string.Empty` ⇒ **开关非 true：掩码不下发** |
| ② 下发体 | `src/MaaWpfGui/Models/AsstTasks/AsstStartUpTask.cs` **L39-44** | `account_name` 注释「留空以禁用」；空值 `DefaultValueHandling.Ignore`（序列化时直接省略该键） |
| ③ core 参数 | `src/MaaCore/Task/Interface/StartUpTask.cpp` **L87-98** | `m_account_switch_task_ptr->set_enable(!account_name.empty())`；`set_account(account_name)` |
| ④ core 执行 | `src/MaaCore/Task/AbstractTask.cpp` **L31-34** | `if (!m_enable) { Log.info("task disabled, pass", ...); return true; }` ⇒ **整段跳过、返回 true（不算失败）** |
| ⑤ 对照（开关=true） | `src/MaaCore/Task/Miscellaneous/AccountSwitchTask.cpp` L21-24/26-29/32-34/36-48/50-58 | 空名报错；客户端白名单；导航到账号页 retry30；**已在目标号 ⇒ 静默直登（不发事件）**；否则滑扫找号（≤21×2），选中才发 `AccountSwitch` 事件 → gui `账号切换 -->> <掩码>` |
| ⑥ 迁移边界 | `src/MaaWpfGui/Configuration/Single/MaaTask/StartUpTask.cs` **L28-33** | `AccountSwitchEnabled ??= !empty(AccountName)` 只对**缺键**生效；现网显式 `false` 不会迁移 ⇒ false 一直是 false |

### 2.2 行为矩阵

| 配置态 | GUI 下发 account_name | core | 结果 |
|---|---|---|---|
| **masked+false（本两例）** | `""` | AccountSwitchTask disabled | **不切号**，留在当前号，静默 |
| empty+false（非官服口径） | `""` | disabled | 同上（非官服预期路径） |
| mask+true（官方口径） | 掩码 | enabled | 已在目标号→静默直登；否则找号切（成功有 `账号切换 -->>`）；找不到→游戏重启循环 ≤5（`StartUpTask.h` L27 `MaxRestartAttempts=5`）→「任务出错: 开始唤醒」 |

### 2.3 实测锚点（只读，今晚 09-27）

- **v2 注入在跑（官方=mask+true 已下发给 core）** —— asst.log `append_task StartUp` 原文：
  - MAA_10 `19:58:56` `{"start_game_enabled":true,"account_name":"186****6119","client_type":"Official"}`（官-33 轮，19:57:41→20:20:29 完成）
  - MAA_12 `20:04:56` `…"account_name":"177****2151"…`（官-24 轮，20:03:41→20:27:26 完成）
  - MAA_12 `20:51:38` `…"account_name":"138****0245"…`（官-12 轮，20:50:23→21:15:30 完成）
  - 三例均 `AccountSwitchTask::_run enter` → `leave, ~27–30s`（如 19:58:56.777→19:59:26.692,29915ms），**全程无 `SubTaskExtraInfo AccountSwitch`、gui.log 无 `账号切换 -->>`** ⇒ 走「已在目标号」静默直登支路、任务照常完成（单实例单号场景的预期行为）。
- **非官方=空+false 也在跑**（同批 append_task 均无 account_name 键）：MAA_10 20:33/20:37/21:06/21:11（Bilibili）、21:42（txwy）；MAA_12 20:28（Bilibili）。
- **disabled 即静默**（实采 `task disabled, pass {"class":"asst::AccountSwitchTask",…}`，两槽合计 **39 条**）：MAA_10 共 26 条 = asst.bak.log 21 条（09-24 21:09 → 09-27 05:38，末 4 条含 09-27 05:29:08.601，即官-33 晨轮/v2 前）+ asst.log 5 条（09-27 20:33:05.538 / 20:37:34.305 / 21:06:26.254 / 21:11:10.116 / 21:42:49.547，全为非官服用户：B服×4 + txwy×1）；MAA_12 共 13 条 = asst.bak.log 12 条（09-25 06:34 → 09-27 06:25，末 4 条含 09-27 06:11:06.579）+ asst.log 1 条（20:28:47.675，B服）。逐条与当时口径的 disabled 预期吻合（非官服用户 / v2 前官方用户）；**官方 + v2 后（≥19:54）的运行 0 条 disabled**（对应 PID/时间核对见附录 A.3）。
- **历史锚点**：09-24..26 官方用户带掩码+false 配置运行时，MAA_6 asst.bak.log 25 条 `task disabled, pass`（t_c5a92e8f §5）⇒ 同一组合的真实运行行为 = 静默跳过。
- **两槽 gui 日志全历史均无 `账号切换 -->>` 成功行**（覆盖：MAA_10 gui.bak.log 08-31→09-27 20:19 + gui.log 09-27 20:32→22:06；MAA_12 gui.bak.log 08-29→09-12 + gui.log 09-21→21:15）⇒ 从未发生过“实际选择动作”。

**⇒ 判定**：masked+false 对「该槽下次启动」的净效果 = 掩码被忽略、不切号、作业停在当前已登录号。对自动化管线，该值在 spawn 前被注入覆盖；对出带启动，是「以为会切、实际不切」的静默面。

---

## 3. 机制复核：为什么这两个 false「赖着不走」（残留 = 还原不动点）

### 3.1 机制（manager.py 实读）

`E:\AUTO-MAS\app\task\MAA\manager.py`：
- `prepare()` **L121-142**：`temp_path = data/<脚本id>/Temp`（L132）→ `copytree(maa_set_path → temp_path, dirs_exist_ok=True)`（L142）＝**轮起快照**（copy2 语义、保 mtime）。
- `final_task()` **L257-260**：`rmtree(maa_set_path)` + `copytree(temp_path → maa_set_path)` ＝**轮末用轮起快照整份覆盖回来**，随后删 Temp。
⇒ 任务轮结束后的槽位文件 = **该任务轮开始前的状态**（连 mtime 一起）。

### 3.2 实证（今晚）

- **MAA_10**：21:46 读到中途态 `Txwy / mtime 21:42:33` → 22:15 读到 `Official/186****6119 / mtime 05:56:42`（文件被整体换回今晨旧态、mtime 回退）。对应 app.log：台-1 任务 `已解锁脚本配置 834dc5cf` **22:07:20**、下一任务 22:07:21 开始 —— 还原就发生在这两行之间。
- **MAA_12**：今日 **7 个任务全部以 `已解锁脚本配置 <sid>` 收尾（=执行了还原）**：官-24 05:36:41（解锁 06:09:49）、官-12 05:37:21（解锁 05:38:53）与重试 06:37:31（解锁 07:05:56）、b-5 06:09:49（解锁 06:37:30）、官-24 20:03:41（解锁 20:27:26）、b-5 20:27:27（解锁 20:50:23）、官-12 20:50:23（解锁 21:15:30）。期间注入写过的内容（如 20:51:38 的 `138****0245`+true）全部被覆盖，文件**全天停在 `177****2151/false/mtime 09-26 06:13:09` 字节未变**。
- **结论：「次日队列应用自然覆盖」不收敛。** 每轮 prepare 快照的就是当前残留值 ⇒ 轮末还原把同一个值写回 ⇒ **false 是不动点**；注入只改运行中值，轮末必被还原覆盖。（MAA_10 的 05:56:42 态与 MAA_12 的 09-26 态，在今日多轮之后字节未变。）
- **同理：清理一次即永久（合规也是不动点）**——播种后的下一次 prepare 快照=播种值 ⇒ 轮末还原=播种值 ⇒ 常绿。（唯二打破：人为/外部写入、或「外部抢锁打断收尾」类异常。）

### 3.3 注入侧闭合性（为什么在带运行不受影响）

- 每号 spawn 前 `AutoProxy.set_maa()` 必写（`E:\AUTO-MAS\app\task\MAA\AutoProxy.py` L766-779，v2：官=掩码+`AccountSwitchEnabled=True`；非官=空+`false`；19:53 落盘＋侧车 19:54:28 重启生效）⇒ MAA 读到的永远是刚注入的值（§2.3 的 append_task 行即实证）。
- ⇒ 运行期不碰残留；残留只影响「不经注入的启动」。

---

## 4. 两例成因判定（逐条定因）

| 槽 | 当前态来源 | 判定 |
|---|---|---|
| **MAA_10** | 今晨残留：官-33 晨轮 05:27:41 起跑（app.log `任务开始 5173bc02`＋`5173bc02…已锁定, MAA配置提取完成` 05:27:41.431=prepare 快照已做），**05:34:37 后端重启**（app.log `启动时任务开始运行` 05:34:37，此前 04:01:47）使其孤儿化（此后无完成/解锁事件）⇒ 该轮**未走 final_task 还原**，文件停在晨轮运行窗内的 v2 前官方注入态（mask+false，mtime 05:56:42）；晚间各轮还原又把它当快照写回 | **旧态被还原**（非当晚注错） |
| **MAA_12** | 09-26 06:13:09 的旧态（v2 前产物），今日 7 个任务的还原全部写回它 | 同上 |

（两例均与 parent 证据包结论一致；本卡补充了「不动点」机制与逐轮回写链。）

---

## 5. Q2/Q3：可容忍性判定 与 口径 v3 建议

### 5.1 可容忍性

| 面 | 判定 |
|---|---|
| 在带运行（AUTO-MAS 驱动） | **无影响**（注入先于 spawn；v2 后恒写合规值，§2.3） |
| 出带启动（人工开 MAA 等） | **静默风险**（mask 是哑值、不切号） |
| 闸门/巡检 | **每晚复现违例**（不动点）⇒ 消耗巡检注意力，会淹没真信号 |

⇒ **结论：不是「可容忍、只存档」；是「一次性清理项」**。清理后由不动点自维持。

### 5.2 口径 v3 建议：判定**不变**，只增不改

1. **不加「轮末还原窗口」例外**（三种备选均否决）：
   - 「只判当日被应用写入过的槽」：班前/空档会把全队判成“无数据”，且正好放过残留类；且实现上等于间歇性失明。
   - 「对 mtime 设时效放行」：把本卡这类信号直接吞掉；mtime 灰区大（手改的新鲜态 / 任务在飞态 vs 残留）。
   - 「区分任务在飞态」：需要跨 app.log 状态推理，复杂度高、错判面大，且不改收益（残留仍只剩 2 个槽位级别的一次性清理）。
2. **准核心理由**：v2 后运行期写入恒合规、还原=轮起态 ⇒ 合规是不动点。**一次播种后「陈旧残留违例」不应再出现**；为不会再出现的类加例外，等于给未来异常（手改、新遗留、注入回归）开后门。判定核心价值=「上一笔写配置的动作是否合规」，fresh/stale 都该先被看见再分诊。
3. **增补（建议随 v3 落地，不改判定结果）**：
   - `mas_switch_gate.py`：违规/未知条目在 `--check` 输出附带 `mtime=… age=…`（JSON 已含 mtime，仅补打印）。
   - 巡检提示词（job 52b88b0ffca0）加分诊一句：
     - `age` 小（≈班次内/与最近 spawn 同刻）⇒ **活态违规**：立即查注入/补丁/侧车；
     - `age` 大（老于本班次、且该槽当日无应用写入）⇒ **残留负债**：登记清理（播种），不当作当晚运行风险。

---

## 6. Q4 最小写方案（受控播种；本卡未执行）

**对 MAA_10 / MAA_12 各做 1 处布尔翻转：`"AccountSwitchEnabled": false` → `true`，其余字节不动。**
目标值口径：保留 StartUp 任务的 `AccountName`（186****6119 / 177****2151，均属所在槽合法掩码）与其余所有字段 ⇒ 安上「官方槽 = masked+true」的静止标准态（与 MAA_1–8 现状一致）。
被否方案：「次日自然覆盖」（§3 已证不收敛）；「改 `data/<sid>` 源配置」（治错地方）；「final_task 后校验/重施」（代码层正解，需改侧车+重启，另卡）。

### 6.1 前置检查（只读；任一不过就不写）

1. 该槽 5/4 个脚本**无在飞任务**：app.log 尾部逐 sid 看最后一笔事件应为 `已解锁脚本配置 <sid>`（或该 sid 当日无 `任务开始`）。
2. **无 MAA 进程占用该槽**：`Get-Process MAA*` 结果为空，且该槽 `debug\gui.log` mtime 陈旧（>10 分钟无写）。
3. **时间窗**：非班次窗（建议 23:30–04:30 或 08:00–19:00），避开 05:00–08:00 / 19:30–23:00。
4. **执行时先复读文件**：若已是 true / 内容有变 ⇒ 重新定因，不盲写。

### 6.2 写命令（PowerShell；保编码、保换行、判独一性）

```powershell
$p = 'E:\MAA-Multi\MAA_10\config\gui.new.json'          # MAA_12 同法换路径
$n = (Select-String -Path $p -Pattern 'AccountSwitchEnabled').Count
if ($n -ne 1) { "ABORT: 命中 $n 处"; return }            # 独一性检查（实读两文件各 1 处，行 8）
Copy-Item $p "$p.bak-resting-seed-$(Get-Date -Format yyyyMMdd-HHmmss)"   # 备份（不覆盖）
$t  = [IO.File]::ReadAllText($p)
$t2 = $t.Replace('"AccountSwitchEnabled": false', '"AccountSwitchEnabled": true')
if ($t2 -eq $t) { "ABORT: 未命中替换"; return }
[IO.File]::WriteAllText($p, $t2, (New-Object System.Text.UTF8Encoding($false)))   # UTF-8 无 BOM，与原文一致
```

- 编码必须 `UTF8Encoding($false)`：原文件无 BOM（首 3 字节 `123,13,10`）；PS 5.1 的 `Set-Content -Encoding UTF8` 会写 BOM，**禁用**。
- 预期 size 恰好 −1：MAA_10 `16063→16062`；MAA_12 `16062→16061`。

### 6.3 读回与验证（写后立即，只读）

```powershell
# 字段读回：sw=true、AccountName 不变、size=旧值-1、mtime=写入时刻
$i=Get-Item $p; $j=Get-Content -Raw -Encoding UTF8 $p | ConvertFrom-Json
$tk=@($j.Configurations.Default.TaskQueue) | ? { $_.TaskType -eq 'StartUp' }
"mtime=$($i.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')) size=$($i.Length) sw=$($tk[0].AccountSwitchEnabled) an=$($tk[0].AccountName)"

# 闸门（本地）：
python D:/1-2/MAAOrch/tools/mas_switch_gate.py --check
```

- **预期 SWITCH_GATE 变化：`VIOLATION:MAA_10,MAA_12` → `OK`（全 12 槽）**。
- **持久性复核（下一自然班次后，只读）**：两槽文件内容仍 true；**mtime 保持=播种时刻**（还原保 mtime ⇒ 可区分「还原写回」与「新注入写」）；闸门仍 `OK`。

### 6.4 风险与回滚

| 风险 | 级别 | 说明 / 缓解 |
|---|---|---|
| 写时与在飞任务竞态（该任务轮末还原覆盖播种） | 低 | 前置检查 1/2 排除；万一发生 → 文件回旧值 → 复跑闸门即知 → 重播 |
| 出带人工启动行为变化：MAA 会「尝试切到该掩码号」 | 低-中 | 与 MAA_1–8 现有静止态一致；单号实例上通常“已在目标号”直登；若该号不在客户端登录列表 → 开始唤醒失败循环（有界 ≤5）——人工启动前本来就要看画面（已有 preflight/冷启流程） |
| 备份 `.bak` 文件进入共享面 | 极低 | 留在 `config\` 会被轮末快照携带（16KB，无害）；介意可改放 `C:\Users\Administrator\` |
| 误伤其他字段 | 极低 | 单串替换 + 独一性检查 + size/md5 复读 |

**回滚**：`Copy-Item "$p.bak-resting-seed-<ts>" $p -Force`（同样只在空闲窗做）。

---

## 7. 只读复核命令合集（谁都能跑）

```bash
# 闸门（全量 / 紧凑 / 自检）
python D:/1-2/MAAOrch/tools/mas_switch_gate.py
python D:/1-2/MAAOrch/tools/mas_switch_gate.py --check
python D:/1-2/MAAOrch/tools/mas_switch_gate.py --selftest

# 槽位文件（远端，只读；PowerShell）
#   $p='E:\MAA-Multi\MAA_10\config\gui.new.json'; $i=Get-Item $p; $j=Get-Content -Raw -Encoding UTF8 $p|ConvertFrom-Json
#   $tk=@($j.Configurations.Default.TaskQueue)|?{$_.TaskType -eq 'StartUp'}
#   "mtime=$($i.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')) md5=$((Get-FileHash $p -Algorithm MD5).Hash) ct=$($j.Configurations.Default.Gui.RuntimeSettings.ClientType) an=$($tk[0].AccountName) sw=$($tk[0].AccountSwitchEnabled)"

# 机制证据（远端）
findstr /n "Temp copytree rmtree" E:\AUTO-MAS\app\task\MAA\manager.py
findstr /n "AccountSwitchEnabled" E:\AUTO-MAS\app\task\MAA\AutoProxy.py
findstr /n "append_task StartUp" E:\MAA-Multi\MAA_10\debug\asst.log
findstr /n "task disabled, pass" E:\MAA-Multi\MAA_12\debug\asst.log

# 日志判据字典：D:\1-2\MAAOrch\docs\MAA_LOG_DICTIONARY.md
```

---

## 8. 附录（原始证据摘录）

### A.1 文件态（22:33 实读）
```
E:\MAA-Multi\MAA_10\config\gui.new.json  mtime=2026-09-27 05:56:42 size=16063 md5=086F1E0CF7A3D47AD41BF6D040F67250 ct=Official an=186****6119 sw=False
  L7: "AccountName": "186****6119",     L8: "AccountSwitchEnabled": false,     L11: "TaskType": "StartUp"
E:\MAA-Multi\MAA_12\config\gui.new.json  mtime=2026-09-26 06:13:09 size=16062 md5=1C8D758D84FA72A0531DC23887A824E8 ct=Official an=177****2151 sw=False
  L7: "AccountName": "177****2151",     L8: "AccountSwitchEnabled": false,
（两文件首 3 字节=123,13,10 → UTF-8 无 BOM；上方 sw 值在 JSON 中为 false，行 8 即写方案靶点）
```
对照：`MAA_6` 20:02:12 `size=16062 md5=1D48B71248F3B0F9B17CDD2636989A1F ct=Official an=134****5276 sw=True`（在队、合规；其轮 19:45–20:02 为孤儿轮、无还原）。

### A.2 app.log 任务时序（09-27，两槽；含解锁=还原时点）
```
MAA_10：
05:27:41 任务开始 5173bc02(官-33) + 05:27:41.431 已锁定/MAA配置提取完成 …（此后无完成/解锁=被 05:34:37 重启吞掉）
19:54:21 任务开始 5173bc02 → 19:55:46 未完成 →（19:54:28 后端重启）
19:57:41 任务开始 5173bc02 → 20:19:33 MAA进程完成代理任务 → 20:20:29 已完成 + 20:20:29 已解锁 5173bc02
20:20:29 任务开始 57b32336(b-7) →（20:31:43 进程超时/20:36:12 未能正确登录重试）→ 21:04:16 完成 → 21:05:07 已解锁
21:05:08 任务开始 616d13e7(b-12) → 21:09:38 未能正确登录重试 → 21:40:43 完成 → 21:41:18 已解锁
21:41:19 任务开始 834dc5cf(台-1) → 22:06:45 完成 → 22:07:20 已解锁   ← 还原时点（文件→05:56:42 旧态）
22:07:21 任务开始 a9525747(l-4)（未见后续事件）
MAA_12：
05:36:41 任务开始 c6444432(官-24) → 06:09:34 完成 → 06:09:49 已解锁
05:37:21 任务开始 8ed62276(官-12) → 05:38:53 未完成(互杀) → 重试 06:37:31 → 07:05:35 完成 → 07:05:56 已解锁
06:09:49 任务开始 c7e0389d(b-5) → 06:37:15 完成 → 06:37:30 已解锁
07:05:57 任务开始 710c22de(l-7)（未见后续事件）
20:03:41 任务开始 c6444432 → 20:26:54 完成 → 20:27:26 已解锁
20:27:27 任务开始 c7e0389d → 20:49:54 完成 → 20:50:23 已解锁
20:50:23 任务开始 8ed62276 → 21:14:59 完成 → 21:15:30 已解锁
21:15:31 任务开始 710c22de(l-7)（未见后续事件）
（所有“已解锁”= final_task 执行=整份还原回轮起态；期间注入内容均被覆盖，§3.2）
```

### A.3 asst.log 摘录（判定锚点）
```
# v2 注入在跑（官方=mask 已下发）
[2026-09-27 19:58:56.577][INF][Px30800] asst::Assistant::append_task StartUp {"start_game_enabled":true,"account_name":"186****6119","client_type":"Official"}
[2026-09-27 20:04:56.896][INF][Px39188] asst::Assistant::append_task StartUp {"start_game_enabled":true,"account_name":"177****2151","client_type":"Official"}
[2026-09-27 20:51:38.447][INF][Px35660] asst::Assistant::append_task StartUp {"start_game_enabled":true,"account_name":"138****0245","client_type":"Official"}
# 非官方=空（无 account_name 键）
[2026-09-27 20:33:05.359][INF][Px27800] asst::Assistant::append_task StartUp {"start_game_enabled":true,"client_type":"Bilibili"}
[2026-09-27 21:42:49.368][INF][Px38852] asst::Assistant::append_task StartUp {"start_game_enabled":true,"client_type":"txwy"}
# disabled 态（静默跳过）样本（两槽合计 39 条；全为非官服用户 / v2 前官方用户，官方+v2 后 0 条）
[2026-09-27 20:33:05.538][INF][Px27800] task disabled, pass {"class":"asst::AccountSwitchTask","details":{},"subtask":"AccountSwitchTask","taskchain":"StartUp","taskid":0}   # MAA_10 / Bilibili 轮
[2026-09-27 21:42:49.547][INF][Px38852] task disabled, pass {"class":"asst::AccountSwitchTask","details":{},"subtask":"AccountSwitchTask","taskchain":"StartUp","taskid":0}   # MAA_10 / txwy 轮
[2026-09-27 20:28:47.675][INF][Px9332] task disabled, pass {"class":"asst::AccountSwitchTask","details":{},"subtask":"AccountSwitchTask","taskchain":"StartUp","taskid":0}   # MAA_12 / Bilibili 轮
# 启用态路径（进入→导航→静默直登返回；无切换事件）
[2026-09-27 20:51:38.628][TRC][Px35660] asst::AccountSwitchTask::_run | enter
[2026-09-27 20:52:05.500][TRC][Px35660] asst::AccountSwitchTask::_run | leave, 26872 ms
```

### A.4 manager.py（远端实读）
```
121: async def prepare(self):
132: self.temp_path = Path.cwd() / f"data/{self.script_info.script_id}/Temp"
140: self.temp_path.mkdir(parents=True, exist_ok=True)
142: shutil.copytree(self.maa_set_path, self.temp_path, dirs_exist_ok=True)
191: async def final_task(self):
257: if (self.temp_path).exists():
258: shutil.rmtree(self.maa_set_path, ignore_errors=True)
259: shutil.copytree(self.temp_path, self.maa_set_path, dirs_exist_ok=True)
260: shutil.rmtree(self.temp_path, ignore_errors=True)
```

### A.5 后端重启标记（09-27，app.log `启动时任务开始运行`）
```
04:01:47.805 / 05:34:37.610 / 19:01:49.339 / 19:54:28.218
```

### A.6 gui 日志覆盖（“无账号切换成功行”的范围声明）
```
MAA_10 gui.bak.log 08-31 21:40 → 09-27 20:19（4.25MB）；gui.log 09-27 20:32 → 22:06（0.22MB）
MAA_12 gui.bak.log 08-29 21:46 → 09-12 19:00（4.40MB）；gui.log 09-21 20:04 → 21:15（2.85MB）
两槽全区间 grep「账号切换 -->>」= 0 行
```

---

## 9. 边界与后续建议（不在本卡执行）

1. **播种执行**（§6）：需 lead 拍板；执行后按 §6.3 复读 + 闸门复核（预期 OK），下一班次后复核持久性。
2. **代码层根治（另卡）**：建议在 `final_task` 还原后按策略复核/重施（或 `prepare` 快照与策略不符时 warning）——改 `AutoProxy/manager` 属侧车代码，需备份链 + 班次窗外重启；同时覆盖「MAA_6 已做过的 Temp 手术」那种临时手段，避免复发。
3. **出带启动规范**：任何“手启动 MAA”前走 preflight（画面 + 注入态检查），避免吃到静止态（本次两例静止态若不清理，就是这类操作的静默陷阱）。
