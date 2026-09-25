# AUTO-MAS 账号清单：现取现盘（tools/mas_accounts.py）

> 2026-09-25 立。起因：活动「月行水上 → 校园商店」03:59 关窗，跨 agent 需要一份
> "哪些号要换商店、在哪台、怎么切"的准确清单。手工挖了一次（主机 `ScriptConfig.json`
> + MuMu 实例表），结论是**这种清单必须现取**——配置随时改，快照第二天就是错的。

## 1. 它做什么

一条命令，从主机现取两份易变状态，导出一张"配置 → 账号 → 模拟器 → ADB 口"的表：

| 数据源 | 内容 |
|---|---|
| `E:\AUTO-MAS\config\ScriptConfig.json` | 每个脚本配置 = 一台模拟器 + N 个用户（账号） |
| `MuMuManager.exe info -v all` | 实例 index → 实例名 / 是否在跑 |
| `E:\AUTO-MAS\config\QueueConfig.json`（`--schedule`） | 班次定时表（日常 05:00 / 晚班 19:30 起，+3 分钟错峰） |

## 2. 怎么用

```bash
python tools/mas_accounts.py                      # 现取 + 打印候选（默认口径：官服、排除 l-*）
python tools/mas_accounts.py --all-servers         # 不按服过滤（看全量）
python tools/mas_accounts.py --include-l           # 不排除实例名 l-* 的机器
python tools/mas_accounts.py --dump --schedule     # 写 runtime/reports/mas_accounts_<ts>.{json,md}
python tools/mas_accounts.py --offline --script-config X.json --mumu Y.json   # 用本地副本（不联网）
python tools/mas_accounts.py --selftest            # 离线自测：解析 / 过滤 / 端口公式 / 凭据剔除
```

退出码：`0` 正常（含"候选 0 个"）、`2` 取数失败、`3` 自测失败。
默认口径之外的号**不是被静默丢掉**：输出行首会给全量服分布（如 `Bilibili 9、Official 38、YoStarJP 11、txwy 2`）。

## 3. 字段口径（踩过的都写在这）

- **配置名 = 模拟器实例名**：本机这套命名是一一对应的（`明日方舟-官-1` ↔ MuMu 实例 `明日方舟-官-1`）。
- **切换关键字 = 账号的登录名**（`Info.Name`，如 `汐影月k`）：MAA 的 account_switch 用的就是它；
  **同一个配置挂多个号** = 一个脚本内串行切号（实测 `官-1` 4 个号、`官-2` 4 个号）。
- **服 = `Info.Server`**，实测枚举四种：`Official`（官服）/ `Bilibili`（B 服）/ `YoStarJP`（日服）/ `txwy`（台服）。
- **ADB 口 = 16384 + 32 × index**：实测 idx1→16416、idx28→17280 吻合。
  ⚠️ **重启模拟器后端口会漂**（实测 5554→5556 那种），真正执行前必须 `adb devices` 现查，
  别把这张表里的端口当保证。
- **是否挂起 = `Info.Status`**（`false` 就是停用）：注意停用的号照样会出现在清单里，要显式过滤。
- **模拟器名以 `l-` 开头** = 肉鸽机队那几台（`l-1/l-2/l-4/l-5/l-7`）。批跑避开它们，
  理由不是服，而是**那几台归肉鸽线用**（见 `docs/ROGUE_WATCH.md` 的让位区）。

## 4. 凭据卫生（硬规矩）

- 原文件 `ScriptConfig.json` **含 DPAPI 密文 `Password` 字段** ⇒ 按敏感件对待：不入库、不外发、不截图。
- 本工具**只取白名单字段**（cfg / sid / maa / emu_idx / emu_name / emu_running / adb / server / login / enabled / notes），
  读入后即丢弃任何 `pass|token|secret|key|cred` 键，序列化前再断言一次 ⇒ 导出的 JSON 里不可能有凭据。
- 自测第 3 组就是拿"带密文的夹具"验这条；`--dump` 时还会 `assert "password" not in payload`。
- **兜底**：`.gitignore` 里已加 `ScriptConfig.json`（任意层级）—— raw 副本即使被误拷进仓库也进不了提交。
  备份要留就留**脱敏版**（把 `Password` / `SklandToken` / `ServerChanKey` / `Notify_CustomWebhooks` 的值替成
  `[已脱敏]`；实测 212KB 的 raw 里 Password 有 44 处非空），其余字段（账号/服/设备）一个不少，足够离线审计。
- **一键脱敏**：`python tools/redact_scriptconfig.py --scan` —— 扫全仓库 `*ScriptConfig*.json`（`--dry-run` 先看清单），
  把敏感键值替成 `[已脱敏]`，校验无残留长串后写 `<原名>.redacted.json` 并**默认删掉 raw 原件**（`--keep-raw` 可保留）。
  只管含非空密文的文件；空口令的老 dump 会被列出但不改（要删自己删）。`--selftest` 10 项含密文夹具。

## 5. 与"让位/班次"的关系

批跑（换商店、扫关、学习）占用的是**模拟器 + 人的注意力**，和日常/肉鸽线抢机器：

- 主机 12 槽班次：**日常定时 1–12 = 05:00/05:03/…/05:33**、**晚班定时 1–12 = 19:30/19:33/…/20:03**。
- 肉鸽线的让位区是 `04:00–05:33`、`18:30–20:30`（见 `docs/ROGUE_WATCH.md` §5.25）——批跑要落在**别的时间窗**里，
  或者提前跟肉鸽线确认"此刻哪台真空着"。
- 清单里带 `emu_running`，但那是取数那一刻的快照；**执行前现查**才是准的。

## 6. 已知缺口 / 下一步

- **实例表可能输出不全**：`MuMuManager info -v all` 偶尔少几个 index（实测漏过 idx51），
  此时名称/在跑状态为空、只留 ADB 口，并在 stdout 打一行 `注意：实例表里缺 N 个 index` —— 重跑一次通常就好。
- 班次时间只做"名字 + HH:MM"抽取（QueueConfig.json 结构会变），不解读"哪个配置绑哪个定时"。
- 想按"哪台此刻真空"排程，还是要配合 `adb devices` + MuMu `is_process_started` 现查（本工具给了接口，没做成自动排程）。
