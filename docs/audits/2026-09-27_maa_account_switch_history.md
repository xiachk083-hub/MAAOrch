# MAA 账号切换「非官服支持」溯源 —— 从无到有的编年史（只读研究）

> 2026-09-27 ｜ 执行：mas（kanban t_3f5e4681）｜ **只读**：未改配置、未发车、未动实例。
> 口径：上游仓库 MaaAssistantArknights/MaaAssistantArknights（commit/tag/release/PR）+ 资源实证：
> 主机 `E:\MAA-Multi\MAA_6`（v6.18.0，MRFZ-0000）与 本机 `D:\Xiach\M-A\MAA\MAA-v6.12.2-win-x64`。

## 0. 结论（TL;DR）

1. **不存在「MAA 从某版本取消了非官服账号切换」这一事件**。事实相反，客户端支持是**递增**的：
   - 官服 + B 服：2023-09-09 功能上线即有，**至今从未取消**（2026-06 仍有 B 服切号 issue 报告在用：#17064）；
   - 台服（txwy）：**v6.11.0（2026-06-01）新增**；
   - 韩服（YoStarKR）：**v6.16.8（2026-08-11）新增**（PR #17661）；
   - 日服（YoStarJP）/ 国际服（YoStarEN）：**自始至终未支持**（源码注释占位、GUI 无入口、文档无条目，三处一致）。
2. 用户记忆中「非官服不能切号」的真实对应物 = 两条 **UI 收窄**（对齐 core 已有能力，并非功能移除）：
   - **v5.14.0（2025-03-12）**：① 未选择客户端时不再显示切号输入框（此前可见）；② 非「官服/B 服」客户端在序列化时把 `account_name` 清空（「改了也没用」的状态终结）。
   - v5.21.0（2025-07-27）：core StartUp 的 `client_type` 不再接受空值（#13318，切号必须挂明确客户端）。
3. 「找回来」评估：**B 服无需找回；台/韩服升级即得；日/国际服从未存在过**。若要做，按 KR 先例 4 步补丁（§4），MAA 侧无制度性障碍，缺的只是真机 UI 适配。

## 1. 编年史（逐条出处）

| 日期 | 版本 | 事件 | 出处 |
|---|---|---|---|
| 2023-09-09 | v4.24.0 | 切号功能上线，**仅官服+B服**（模板仅 AccountManagerOfficial/Bili；文档只写官/B；XAML 输入仅 Official/Bilibili/空 可见） | commit `f5d49e6eb`（首发 v4.24.0-beta.3，稳定版 v4.24.0 2023-09-26） |
| 2023-12-23 | — | perf：**取消 B 服客户端检测流程**（步骤简化，非取消支持）；同期 `.h` 已写死 {Official,Bilibili} + 注释 {YoStarEN,YoStarJP,YoStarKR,txwy} | `de4b2f237` (#7657) |
| 2024-10-17 | — | B 服多分辨率多 dpi 登录 | `9211b67c9` (#10817) |
| 2025-03-05 | v5.14.0 | **GUI 收窄**：非官/B 不下发 account_name；未选服不再显示输入（「取消」感知来源） | `c2dfe33d7` |
| 2025-07-21 | v5.21.0 | core：client_type 必填、移除空值支持（协议文档同步 5 种客户端枚举） | `e3c0925d0` (#13318) |
| 2026-06-01 | **v6.11.0** | **台服支持**：core 把 txwy 与 Official 同类化；GUI 放行 Txwy；文档补繁中样例 | `9237dddda` + `c20c10317`（release 2026-06-01T19:27Z） |
| 2026-07-06 | v6.14.0 | 新增「账号切换是否启用」勾选框（`AccountSwitchEnabled` / 立即切换按钮） | `7e61c2eab` (#17280) |
| 2026-08-11 | **v6.16.8** | **韩服支持**（PR #17661，导航/OCR 分组合并 + KR 全局资源 + GUI + 文档，by HX3N） | `e52c95ebb`（release 2026-08-11T12:29Z） |
| 2026-08-11→ | v6.18.0 | 支持集定格：`{ "Official", "Bilibili", "txwy", "YoStarKR" /*, "YoStarEN", "YoStarJP" */ }` | `AccountSwitchTask.h:38-41` @v6.18.0 |

版本对照（同一文件随 tag 抓取实读）：
- `.h` 支持集：v5.0.0 / v5.28.5 / v6.0.0 = 官/B（4 客户端注释）→ v6.12.2 = +txwy → v6.16.8 / v6.17.5 / v6.18.0 = +KR。
- GUI XAML：…v5.13.1 = `Official or Bilibili or ""` → v5.14.0~v6.10.7 = `Official or Bilibili` → v6.11.0 = +txwy → v6.16.8+ = +KR。
- 界面文案（zh-cn.xaml `AccountSwitchTip`）：v6.0.0「仅支持官服、B服」→ v6.11.0「官服、B服及繁中服」→ v6.16.8「仅支持官服、B服、繁中服及韩服，不支持第三方登录方式。」（现值）。
- 官方手册 docs/zh-cn/manual/introduction/startup.md @v6.18.0：账号切换例子只列 官服/B服/繁中服/韩服（日/国际服无条目）。

## 2. 资源实证（对照卡片问题 1/2）

- **基础 tasks.json 无任何 ClientType 条件**：v6.18.0 `resource\tasks\tasks.json`（13958 行）中 `client_type|ClientType` 计数 = **0**（主机实测）；本机 v6.12.2 同构。
  - v6.18.0 关键行号：`SwitchAccount@StartUpBegin`:1652 ｜ `SwitchAccount`:1661 ｜ `SwitchAccount@StartToWakeUp`:1781 ｜ `LoginOther`:1801（next=`[AccountManagerOfficial:1805, AccountManagerBili:1806]`）｜ `AccountManagerOfficial`:1809 ｜ `AccountManagerBili`:1817（OCR「登录记录」，注释：B 服标题随 dpi 变、只能文字识别）｜ `AccountCurrentOCR(Bili)`:1830/1841 ｜ `LoginButton(Bili)`:1851/1865 ｜ `ListAccount(Bili)`:1874/1883 ｜ `TrashButton(Bili)`:1896/1904 ｜ `Swipe`:1912/1931。
  - 本机 v6.12.2 同块：`:736/:745/:796/:807/:812/:815/:821/:832/:843/:853/:859`（结构逐条一致）。
  - **不存在「SwitchAccount@StartToWakeUp 只在 Official 下可用」这种 JSON 级分服**；该子任务（DoNothing→AccountManager）对官/繁中/韩通用。
- **分服发生在两处**：① C++ `AccountSwitchTask` 按 `m_client_type` 选导航/OCR 分支（Official/txwy 一组、Bilibili 一组、Yostar/KR 一组）；② `resource\global\<client>\resource\tasks\tasks.json` **按服覆盖**（主机实测）：
  - txwy：`AccountManagerTxwy`:2；`LoginOther.next = [Official, Bili, Txwy]`:905；模板 `template\AccountManagerTxwy.png` + `WakeUp\AccountManager\AccountManagerListAccount.png`。
  - YoStarKR：`AccountManagerYostar`:2；`LoginOther.next=[AccountManagerYostar]`:1216；覆盖 `SwitchAccount@StartUpBegin`:1188、`AccountCurrentOCR`:1219、`LoginButton`:1222、`ListAccount`:1226（另有 `SwitchAccount@StartUpNotification`:1210）；模板 3 张（AccountManagerListAccount/AccountManagerYostar/YostarUserCenter）。
  - YoStarEN / YoStarJP：AccountManager 模板 **0 张**、无覆盖条目（= 未适配）。
- **cache\resource（OTA 增量）**：`cache\resource\tasks\tasks.json` 仅 362 行（SR-* 等增量），不含 SwitchAccount/AccountManager —— 切号定义不被 OTA 覆盖，权威 = 基础 resource + global 覆盖。
- 机队版本：MAA_1..12 = 6.18.0；MAA_13/14 = v6.16.8+build；MAA_25/28 = 6.17.5。（台/韩切号在 6.16.8+ 全队可用；日/国际任何版本都不可用。）

## 3. 为什么（原因判定）

- **技术/维护成本**，非合规：各家客户端账号管理 UI 不同，需专属模板+OCR+导航分支（B 服连标题都随 dpi 变、只能 OCR；KR 需 3 张专属模板+全局任务重写）。txwy 恰与官服同 UI，可「视为同类」低成本支持（提交自述），所以先被补上；日/国际服的 UI 适配**从未有人做**，注释自 2023 保留至今。
- 无「封禁/下架/合规」记录：相关提交均为增补与差错修复（#7657/#10817/#13318/#17280/#17661）；未检索到任何声称「取消非官服切号」的 release note 或 issue。
- 用户侧先例：台/韩服均由社区 PR 推动 —— 「有适配才有支持」，等不来自动恢复。

## 4. 可恢复性评估（怎么「找回」）

- **B 服**：所有版本均可（自 v4.24.0 功能上线至今未取消；GUI 有输入框）。填 B 站用户名子串；注意唯一性/易错（用户口径同此）。
- **台/韩服**：≥v6.11.0 / ≥v6.16.8 已可用；升级即得，无需补丁。
- **日服/国际服**：从未支持过，「找回」= 新做适配；路径（以 PR #17661 为模板）：
  1. core：`AccountSwitchTask` 增加 Yostar JP/EN 分组（导航账号管理页 + 选行/点登录）；
  2. 资源：`global\YoStarJP(YoStarEN)` 补 `template\WakeUp\AccountManager\*` + tasks 覆盖（LoginOther.next、AccountCurrentOCR、LoginButton、ListAccount…）；
  3. GUI：StartUpTaskUserControl 可见性 + Serialize 白名单 + 本地化文案 + docs/startup.md；
  4. 验证：日/国际服真机样本（机队若跑对应客户端可产出）。
- 阻断点：需要日/国际服真机 UI 素材（KR 从提交到合并仅 9 小时，上游无制度性障碍）。
- AUTO-MAS 侧提醒：切号注入现只对 Official 做掩码、B 服注入用户名（AutoProxy.py:767-777）；若未来要用台/韩切号，注入格式需按官方文档（Email 明文片段）另行适配。

## 5. 复核命令（只读）

- 上游历史：`curl -s "https://api.github.com/repos/MaaAssistantArknights/MaaAssistantArknights/commits?path=src/MaaCore/Task/Miscellaneous/AccountSwitchTask.h"`（同理 `.cpp`、`StartUpSettingsUserControlModel.cs`、`StartUpTaskUserControl.xaml`）。
- 版本对照：`for t in v6.10.7 v6.11.0 v6.16.7 v6.16.8; do curl -s "https://raw.githubusercontent.com/MaaAssistantArknights/MaaAssistantArknights/$t/src/MaaCore/Task/Miscellaneous/AccountSwitchTask.h" | grep -A5 SupportedClientType; done`
- 主机：`Select-String 'E:\MAA-Multi\MAA_6\resource\global\*\resource\tasks\tasks.json' -Pattern 'AccountManager'`
- 本机：`grep -n 'SwitchAccount' D:\Xiach\M-A\MAA\MAA-v6.12.2-win-x64\resource\tasks\tasks.json`
