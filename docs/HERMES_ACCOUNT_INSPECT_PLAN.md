# 上号 → 看干员/仓库：三条路径勘察与最小可行方案

> 勘察日期 2026-09-28 · **只读勘察**：未登录任何游戏账号、未改任何配置、未发车
> 实测探测=只截图不点击（约束允许级别）
> 证据口径：`文件:行` 为源码引用；`实测` 为本文档记录的一次真实执行输出
> 代码口径：AUTO-MAS 本地 `D:\MAS`（v5.4.0，与线上 `E:\AUTO-MAS` 逐文件 SHA1 一致，见 §8）；MAA 源码 `D:\1-2\_maa_src`（dev-v2 @4d26dd5，2026-09-19，与主机 MAA **6.18.0** 同源）

---

## 0. 结论先行

1. **能做，且主机上不用装任何东西。** 主机已有 MAA 自带的 Python `asst` 绑定（`E:\MAA-Multi\MAA_6\Python\asst`、`MAA_13\Python\asst`、`E:\MAA-CLI\core\Python\asst`）+ Python **3.12.9**（`C:\Program Files\Python312\python.exe`）。本机也有一套现成实装（`tools/maa_operbox.py`、`services/depot_reader.py`）。
2. **"无人值守触发 MAA 原生识别"不能走 AUTO-MAS 发车。** AutoProxy 每次发车**重建** TaskQueue（`MAS:app/task/MAA/AutoProxy.py:838-844`，只写 8 项白名单 `MAA_TASKS`）；ManualReview 把 TaskQueue 换成只有 `StartUp`（`MAS:app/task/MAA/ManualReview.py:396`）；ScriptConfig 是编辑模式（`RunDirectly=False`、不自动跑链）。⇒ **"往 MAA 配置里塞一个『更新用户数据』任务"这条路，在 AUTO-MAS 三模式下必然被丢弃。**
3. **两条可行的无人值守读法：**
   - **B′：maa-cli 自定义任务文件**（`type = "OperBox" / "Depot"`，主机已部署 `E:\MAA-CLI` 0.7.5）——不依赖 GUI，**但从未实测**（待验证 V1）。
   - **C：asst 子进程直连 MaaCore**（本机已实装并跑通；主机只需搬一个等价 runner，**唯一需要写的新代码**）——**推荐作为主链路**。
4. **产出是结构化 JSON，自动落盘。** MAA GUI 收到识别回调后自动写 `data/OperBoxData.json` / `data/DepotData.json`（`JsonDataHelper.Set` → `data/{key}.json`）；剪贴板/导出按钮只是附加面。**主机实测：`E:\MAA-Multi\MAA_6\data\DepotData.json` 存在（450B，mtime 2026-09-28 06:32）；但全主机（`E:\MAA-Multi` + `E:\MAA-CLI` 递归搜 `*OperBoxData*.json`）没有 OperBoxData.json** ⇒ 干员数据目前**完全是空白**。
5. **并发限制 = 同一脚本（同 MAA 目录 / 同模拟器实例）同一时刻只能有一个任务**；**不同脚本但同 MAA 目录也互杀**（`docs/HERMES_MAS_PLAYBOOKS.md:29`）。ManualReview 收尾会**等客户端应答**，没人应答会一直挂着、脚本锁不释放（`MAAOrch:tools/mas_ondemand.py:1024-1025` 原文警告）。
6. **OCR 路径（A）不做主链路。** 它给的是"截图 + 模板/文字点击"，**不是识别**；要读 391 个干员得自己进页/翻页/滚动/识字，且依赖窗口可见 + 独占鼠标。它最适合做**取证截图**与**失败兜底**。

---

## 1. 三条路径对照表

| 维度 | A. AUTO-MAS OCR 界面自动化 | B. MAA 原生识别 | C. asst 子进程绑定（推荐） |
|---|---|---|---|
| 入口/代码 | `MAS:app/api/ocr.py`（7 端点，REST 36163，**无鉴权**） | MAA GUI 内置任务 + maa-cli 任务文件 | `services/maa/instances*/Python/asst` + 自写 runner |
| 触发方式 | 直接 HTTP 调用 | ① 工具箱按钮（手动）② 任务队列「更新用户数据」（自动，但被 AUTO-MAS 重写）③ maa-cli 任务文件（自动，待验证 V1）④ 一图流 OpenAPI（不用模拟器，需 token） | 子进程 `append_task("OperBox"/"Depot")` |
| 可行性 | 端点实测可用；作为"读干员"主链路=不推荐（成本/脆弱） | 能力确认存在（6.18）；③待验证 | **本机已跑通**；主机=搬脚本（唯一新代码） |
| 无人值守程度 | 中低：需窗口可见 + 独占鼠标 + 自备模板；多实例机队=物理串行 | ③=高；②经 AUTO-MAS=失效；①=人工 | **高**：无 GUI/窗口/鼠标，纯 ADB |
| 产出格式 | `image_base64` PNG；check/click 返回 bool（**无结构化数据**） | `data/OperBoxData.json`、`data/DepotData.json`（结构化、自动落盘） | 回调 JSON（同 MAA 格式），可自定义落盘 |
| 主要风险 | 窗口模糊匹配（实测命中 464×261 非设备窗）；DPI zoom；鼠标独占；17 套 UI 主题；无鉴权 | 需 MAA 侧驱动；队列被重写；一图流需 token | 前置=游戏在大厅；子进程隔离；抢实例 |

---

## 2. 路径 A：AUTO-MAS OCR 界面自动化（`app/api/ocr.py`）

### 2.1 端点清单（行号=本地 `D:\MAS`，与线上 SHA1 一致）

| 端点 | 行 | 用途 |
|---|---|---|
| `POST /api/ocr/screenshot` | `ocr.py:149`（handler `:156`） | 按**窗口标题**截图 |
| `POST /api/ocr/screenshot/adb` | `:223`（`:230`） | 按 **adb 路径 + serial** 截图 |
| `POST /api/ocr/check/image` | `:311`（`:318`） | 模板匹配"是否存在" |
| `POST /api/ocr/check/any` / `check/all` | `:367` / `:423` | 多模板任一 / 全部 |
| `POST /api/ocr/click/image` | `:480`（`:487`） | 匹配到就点 |
| `POST /api/ocr/click/text` | `:536`（`:543`） | OCR 找到文字就点 |

> ⚠️ 卡片里写的 `/api/ocr/screenshot/window` **不存在**（实测 404）。实测活路径（`GET /openapi.json`）就是上表这些。

### 2.2 入参/出参（模型定义 `ocr.py:50-148`）

- **截图怎么指定设备**：
  - 窗口路 = `window_title: str`（必填，`ocr.py:51`）→ `win32gui.EnumWindows` 取**第一个**标题包含该子串的可见窗口（`OCRtool.py:325-355`）。
  - ADB 路 = `adb_path` + `serial`（如 `127.0.0.1:16800`）+ `use_screencap`（`ocr.py:72-81`）。
- **返回**：`image_base64`（PNG，**内存直返，不落文件**）+ `region` + `image_width/height`（`ocr.py:63-69`）；ADB 路额外带 `serial`（`:83-87`）。
- **模板从哪来**：`image_path` / `image_paths` 由**调用方给绝对路径**（`OCRtool.py:708` `cv2.matchTemplate`，`threshold` 默认 0.8）。AUTO-MAS 侧**没有模板资源库** ⇒ 需自备；**建议直接复用 MAA 的 UI 模板 PNG**（见 §5.4）。
- **点击坐标语义**：截图坐标 → 屏幕绝对坐标（`OCRtool.py:692` `_location_calculator`：`x * (1/screenshot_proportion) + area_left`）→ `pyautogui.click(screen_x, screen_y)`（`OCRtool.py:944` / `:1017`）⇒ **真鼠标点击桌面像素**，不是设备坐标、不是 adb tap。
- **截图的三个隐式变换**（同一文件）：① 窗口会被强制激活（`_force_activate_window:251`；最小化会先 `SW_RESTORE`，`:164-167`）；② 预处理默认开=扣边框后对齐到 16:9（`top_offset=30*zoom`、`left_offset=8*zoom`，`:197-212`），`zoom=DPI 缩放`（默认 1.5，`:51/:76`）；③ 若宽高不是 16:9 的整数倍，会**缩放到宽 1920**（`_image_resize:670`）——点击换算吃的就是这套比例。

### 2.3 实测（2026-09-28 06:26–06:28，只截图不点击）

**A1（窗口路）** `POST /api/ocr/screenshot` body `{"window_title":"明日方舟-官-1"}`：

```
code=200 status=success message=截图成功
region=[968,365,464,261]  w=464 h=261  b64_len=8000（PNG 6000B）
```

落地件：`docs/audits/shots/2026-09-28_ocr_window_probe_mumu_thumb.png`。本地 rapidocr 读出画面文字：`空格 / 速度暂停 / 撤退` ⇒ 命中的是**显示着游戏战斗画面、但尺寸远小于设备窗（1600×900）的小窗**——**模糊标题匹配不可靠的实证**。

**A2（ADB 路）** `POST /api/ocr/screenshot/adb` body `{"adb_path":"E:\\MuMu Player 12\\shell\\adb.exe","serial":"127.0.0.1:16800"}`：

```
code=200 status=success message=ADB 截图成功
1600×900  b64_len=1370804（PNG 1,028,103B）
```

落地件：`docs/audits/shots/2026-09-28_ocr_adb_probe_idx13_guan1.png`（画面=「正在提交反馈至神经…」载入中，即当时 idx13 正在跑批）。**ADB 路稳定、分辨率正确、可脚本化；窗口路需要额外的窗口身份校验才敢用。**

### 2.4 结论

- 适合：**取证截图**（发车后"人头确认"）、失败兜底、离线核对。
- 不适合：当"读干员/仓库"的主链路（要自己实现进页、翻页、滚动、识字，且窗口/鼠标/主题/DPI 全是脆弱点）。
- Hermes 侧已有对应 MCP 工具面（`auto-mas-scrubbed`：`get_screenshot` / `get_screenshot_adb` / `check_image(_any/_all)` / `click_image` / `click_text`），可直接调用；也可直接 curl（实测无鉴权）。

---

## 3. 路径 B：MAA 原生识别（OperBox / Depot）

### 3.1 能力确认（MAA 6.18.0）

- **干员识别 OperBox**：`MaaTask/BaseTask.cs:77` `class OperBoxTask`；工具箱按钮 `ToolboxViewModel.StartOperBoxRecognitionTask`（`ToolboxViewModel.cs:1743`）。
- **仓库识别 Depot**：`BaseTask.cs:73` `class DepotTask`；按钮 `StartDepotRecognitionTask`（`:1141`）；任务链资源 `resource/tasks/tasks.json` 的 `Depot`（`:5028-5033`，`sub:["Depot-Entry"]`）+ `UiTheme/Depot.json` 的 `Depot-Entry`（入口 ROI `[1153,606,77,79]`，模板 `Default/DepotEnter.png`）。**6.18 保留该功能**（主机 MAA_6 FileVersion 实测 = 6.18.0，且 `data\DepotData.json` 在盘）。
- **顺带识别**：启用「库存保持」(DepotMaintain) 且 `UpdateDepot=true`（默认 true，`DepotMaintainTask.cs`）时，MAA 会**先追加一次仓库识别**再刷材料（`DepotMaintainTaskUserControlModel.ISerialize`：`AsstAppendTaskWithEncoding(TaskType.Depot, ...)`）。主机 `MAA_6\debug\gui.log` 实测有 DepotMaintain 活动（05:25–06:37 多轮 `UpdateStageList`）。

### 3.2 无人值守触发（4 条，含有效性判定）

| # | 方式 | 是否无人值守 | 判定 |
|---|---|---|---|
| ① | MAA 工具箱按钮（干员识别/仓库识别） | ❌ 人工点 | 只能人工 |
| ② | 任务队列「更新用户数据」`UserDataUpdateTask`（`UpdateOperBox/UpdateDepot/TriggerInterval=EveryTime\|Daily\|Weekly` + `Last*SyncTime` 门控，`UserDataUpdateSettingsUserControlModel.cs:102-140`） | ✅ MAA 侧自动 | **但经 AUTO-MAS 发车会被重写队列丢掉**（AutoProxy 重建 / ManualReview 只留 StartUp / ScriptConfig 不自动跑） |
| ③ | **maa-cli 自定义任务文件**（`type="OperBox"/"Depot"`；`MAAOrch:docs/MAA_CLI_SURVEY.md:133-135` 列了 16 种 type 含二者；`:277` 任务文件还支持 `account_name`） | ✅ | **未实测** → V1 |
| ④ | 一图流 OpenAPI（`OperBoxUseYituliuApi=true` + token → `GET https://backend.yituliu.cn/open-api/operator/info`，`MaaUrls.cs:38`；GUI 后台拉取不进 core，`ToolboxViewModel.cs:1798/1810`） | ✅ 不用模拟器 | 需 token；当前生产未启用（MAA_6 实测 `OperBoxUseYituliuApi=false`） |

> 结论：想无人值守拿干员/仓库，**别指望 AUTO-MAS 发车顺带**；要么走 ③（maa-cli），要么走路径 C。

### 3.3 产出落点与格式

- 干员：`<MAA根>/data/OperBoxData.json`
  ```json
  { "done": true,
    "own_opers": [{"id":"char_147_shining","name":"闪灵","elite":0,"level":50,"own":true,"potential":1,"rarity":6}, ...],
    "source": "local", "syncTime": "…" }
  ```
  （落盘代码 `ToolboxViewModel.cs:1513-1526` `SaveOperBoxDetails` → `JsonDataHelper.Set(JsonDataKey.OperBoxData)`；键名 `JsonDataKey.cs`；路径规则 `JsonDataHelper.cs`：`Path.Combine(PathsHelper.DataDir, "{key}.json")`）
- 仓库：`<MAA根>/data/DepotData.json`
  ```json
  { "done": true, "data": {"4003": 51465, "4001": 2297278, "3003": 445, ...} }
  ```
  （`ToolboxViewModel.cs:601-615` `SaveDepotDetails`；物品ID→数量）
- **剪贴板不可用时的替代**：就是这两个 JSON（自动写、无需 GUI）。GUI 导出（干员 Clipboard/Json/Markdown、仓库 Arkplanner/Lolicon/Markdown/Csv，`ToolboxViewModel.cs:864/838/1905-1909`）都是**人工动作**，无人值守用不上。
- 主机实测：`MAA_6\data\` 只有 `Achievement.json / CHANGELOG.md / DepotData.json`；**全主机无 OperBoxData.json** ⇒ 干员侧空白待补。

### 3.4 与"上号"的衔接

识别要求游戏停在**可接管界面（大厅）**——与 ManualReview 的产物天然衔接（它把号切到大厅后驻留）。注意：**点击游戏界面并不会触发 MAA 识别**；触发面只有 §3.2 那四个。

---

## 4. 路径 C：asst 子进程绑定直驱 MaaCore（推荐主链路）

### 4.1 现成资产

- **本机（已实装、已跑通）**：
  - `MAAOrch:tools/maa_operbox.py`：子进程 `asst` → `append_task(StartUp)+append_task(OperBox)` → 收 `SubTaskExtraInfo(20003)` `what` 前缀 `OperBox` → 落 `data/operbox_<tag>.json`（own_opers：name/rarity/elite/level/potential）。
  - `MAAOrch:services/depot_reader.py`：同法 `append_task('Depot')` → `DepotInfo` → `{物品ID:数量}` → `models/accounts/<id>/depot.json`。
  - `MAAOrch:tools/box_read_when_free.py`：等实例空闲 + 先回大厅的自动版。
  - 依赖目录 `services/maa/instances/{1..3}`（MAA 6.11.1，`docs/POSITIONING.md:13`）。
- **主机（具备条件，缺 runner）**：`MAA_6\Python\asst`、`MAA_13\Python\asst`、`E:\MAA-CLI\core\Python\asst` 都在；Python 3.12.9 在 PATH ⇒ **不需要安装 Python 绑定**。缺的只是一个把上面两个脚本改造/搬运过去的 runner（路径映射：`--addr` 直接用主机实例的 adb serial，`--instance` 指向 `MAA_6` 目录）。

### 4.2 协议要点（已有文档锚）

- 回调 `SubTaskExtraInfo = 20003`，`what` 前缀 `OperBox` / `DepotInfo`（实测，文档写 `Depot`，前缀匹配兼容），`details.done=true` 时 `data` 是 JSON 字符串。
- **前置条件（坑，已有实测）**：游戏**必须在大厅**——停在活动地图里直接读会返回 `own: []`（看着像"一个干员都没有"，其实是没读到；同一号回大厅再读 338/391 人）。
- **子进程隔离**：MaaCore in-process 会崩项目进程 ⇒ 一律子进程跑。

### 4.3 无人值守程度与风险

- 最高：无 GUI、无窗口、无焦点、无鼠标；纯 ADB；产出结构化 JSON。
- 风险：① 必须等目标实例空闲（AUTO-MAS 跑批中同一目录=互杀）；② 需自建主机 runner（唯一新代码）；③ 超时/断连要兜底（现有脚本已有 timeout + asst.log 取证）。

---

## 5. 「上号」动作（ManualReview）语义与并发限制

### 5.1 行为链（`MAS:app/task/MAA/ManualReview.py`）

1. 前置 `check()`：**详细模式**用户必须有 `data/<script>/<user>/ConfigFile`，否则该用户直接「异常」（`:78-95`）。
2. 注入 MAA 配置：`PostActions=8 (ExitSelf)`（`:332-336`）、`StartUp` 任务带 `AccountName`（官方=掩码，`:385-393`）、**`TaskQueue=[StartUp]`（`:396`）** ⇒ 只唤醒、不跑链。
3. 成功判据：`gui.log` 出现 **`完成任务: StartUp` 或 `完成任务: 开始唤醒`**（`:414`；对应 MAA 侧英文行 `Completed Task Chain: StartUp`，`AsstProxy.cs:1463`）⇒ 账号已在大厅。
4. 收尾/驻留：`setVisible(True)` 显示模拟器（`:232-238`）→ 发 Question「请检查用户代理情况…」（`:240-252`）→ **等客户端应答**；无人应答=一直挂着、**脚本锁不释放**（`MAAOrch:tools/mas_ondemand.py:1024-1025` 原文警告）⇒ 程序化用法必须带 `--force` 明示，并在读数后**主动 stop**。

### 5.2 并发限制（三层）

- **脚本锁**：同一脚本同时只允许一个任务；再来直接 `RuntimeError("任务 … 已在运行")`（`MAS:app/core/task_manager.py:316-319`）；锁在 `prepare()` 获取、`final_task()` 释放（`manager.py:121-127`）。
- **逐用户串行**：一个脚本的多用户按顺序跑（`manager.py:182-189`）。
- **目录互杀**：不同脚本共用同一 MAA 目录（如 MAA_6=官-1-1-7+官-38）时，同目录并发=互杀（`MAAOrch:docs/HERMES_MAS_PLAYBOOKS.md:29`、`docs/ACCOUNT_HEALTH_PROFILES.md:161`）。
- 收尾（stop 后）：`final_task` 对 AutoProxy/ManualReview 会**关模拟器** + 还原 MAA 配置（`manager.py:191-262`，`:202-206`）⇒ **读数要在 stop 之前完成**。

### 5.3 现成入口

`MAAOrch:tools/mas_ondemand.py --wake <脚本名|uid> --apply --force`（约 `:1008` 起），等价于 `POST /api/dispatch/start {"taskId":<脚本uid>,"mode":"ManualReview"}`。**实测口径**：REST `http://100.79.173.69:36163` 直连无鉴权。

### 5.4 附：可复用的 UI 导航资源（给 OCR/模板点击用）

MAA 自带 17 套主题的入口模板（`resource/template/UiTheme/<Theme>/{OperBoxEnter,DepotEnter}.png`，含 `Default/Dark/Sami/MistCity/...`）：

- 干员入口：`OperBoxDefault` = `ClickSelf`，ROI `[935,300,265,110]`（1280×720 基准），模板 `Default/OperBoxEnter.png`（`UiTheme/OperBox.json`；`OperBox-Entry` 搜索域 `[945,301,85,78]`）。
- 仓库入口：`DepotDefault` = `ClickSelf`，ROI `[1109,560,171,160]`，模板 `Default/DepotEnter.png`（`UiTheme/Depot.json`）。
- 机队是 1600×900 ⇒ ROI 需 ×1.25；主题未知时要先判定当前 UI 主题（或做多模板 any 匹配）。

---

## 6. 推荐最小可行方案（两段式）

**目标闭环（推荐）= 上号（ManualReview）+ 读数（asst 子进程）+ 落 JSON**

| 步 | 动作 | 判据/兜底 |
|---|---|---|
| 0 | preflight：目标脚本无任务在跑；目标实例 adb 可连；无残留弹窗（adb 截图判据） | 有占用 ⇒ 等/换号 |
| 1 | `--wake <脚本> --apply --force`（或直调 `/api/dispatch/start` mode=ManualReview） | 轮询 `gui.log` 至 `完成任务: StartUp`/`Completed Task Chain: StartUp`；否则看 gui.log 错误行（ADB 异常/未检测到模拟器/超时） |
| 2 | **主机侧 asst runner**：对目标实例 append `OperBox`（+ `Depot`）→ 收 20003 → 拉回 JSON | 游戏必须在大厅（回大厅再读）；超时 420s；子进程隔离 |
| 3 | 落盘 `runtime/reports/accounts/<账号>/operators.json`、`depot.json`（含 source/fetched_at/account/uid若可得/evidence 截图路径） | 截图证据=大厅 UID/掩码 + 干员页/仓库页 |
| 4 | `POST /api/dispatch/stop {taskId:<task_uid>}` 收尾 | 确认配置解锁；注意 stop 会关模拟器（先读后停） |

**降级/替代路径**：
- **零新代码（今天就能验证格式）**：本机实例用 `tools/maa_operbox.py --emu N` / `services/depot_reader.py --adb … --account …` 跑一个本地号，先验证 JSON 口径与流程。
- **待验证的第三条**：maa-cli 自定义任务（V1）。若通过，可完全绕开 GUI 与 AUTO-MAS。
- **OCR 只做取证**：发车后 ADB 截图（`/api/ocr/screenshot/adb`）留"确实是那个号"的证据。

**明确不要做**：① 不要把 OCR 点击链当"识别"主链路；② 不要在 AUTO-MAS 跑批窗口内抢同一 MAA 目录/实例；③ 不要指望改 MAA 配置里的任务队列被 AUTO-MAS 保留。

---

## 7. 待验证清单

| # | 待验证 | 方法 | 通过判据 |
|---|---|---|---|
| V1 | maa-cli 自定义任务 `type="OperBox"/"Depot"` 端到端 | `E:\MAA-CLI\bin\maa.exe run <taskfile> -a 127.0.0.1:<port>`（先 `--dry-run`） | 无报错 + `MAA-CLI` 侧 core 目录 `data/*.json` 更新 |
| V2 | 主机 Python 3.12.9 + `MAA_6\Python\asst` 直连读盒 | 写 `host_operbox.py`（照搬 `tools/maa_operbox.py`，`path=E:\MAA-Multi\MAA_6`），目标实例空闲时跑 | 收到 20003 `own_opers` 非空（≥300 人） |
| V3 | 主机侧运行期安全 | 同上，但选 ManualReview 停驻窗口内（MAA 已退出、模拟器在） | 不与 AUTO-MAS 互杀；adb 会话无冲突 |
| V4 | OCR 窗口匹配唯一性 | 对目标标题先查 `region` 尺寸是否≈设备窗（1600×900×16:9），否则换标题/用 ADB 路 | region 宽高比≈16:9 且宽度 >1200 |
| V5 | OCR 点击落点校准（1600×900 实例） | 用 MAA 模板 `OperBoxEnter.png` 做一次 `click_image` 干跑，截图核对落点 | 点击进入干员页；无副作用 |
| V6 | ManualReview 问答的程序化收尾 | 是否有 API 应答 Question；否则只能 `stop` | stop 后配置解锁（`data/<script>/…/ConfigFile` 恢复） |
| V7 | DepotData.json 刷新来源 | 区分 "Depot updated from stage drops"（掉落记账）vs 真识别链 | 触发一次 DepotMaintain 后查识别是否跑 |
| V8 | 一图流 OpenAPI 可用性 | 填 token（需用户提供）后 `OperBoxUseYituliuApi=true` | GUI 拉到 operator/info 并落 `source=yituliu` |

---

## 8. 证据索引

**代码引用**
- AUTO-MAS：`D:\MAS`（v5.4.0）——`app/api/ocr.py`、`app/utils/OCR/OCRtool.py`、`app/task/MAA/{AutoProxy,ManualReview,ScriptConfig,manager}.py`、`app/core/task_manager.py`、`app/utils/constants.py`。**与线上 `E:\AUTO-MAS` 一致性实测**：3 个关键文件 SHA1 逐字节相同（ManualReview.py `1ba8611a305…`、ocr.py `550ef9f4…`、OCRtool.py `755f1267…`）。
- MAA：`D:\1-2\_maa_src`（dev-v2 @`4d26dd5`，2026-09-19）——`MaaTask/BaseTask.cs`、`UserDataUpdateTask.cs`、`DepotMaintainTask*.cs`、`ToolboxViewModel.cs`、`AsstProxy.cs`、`JsonDataKey/JsonDataHelper.cs`、`MaaUrls.cs`、`resource/tasks/tasks.json`、`resource/tasks/UiTheme/{OperBox,Depot}.json`。
- MAAOrch：`tools/maa_operbox.py`、`services/depot_reader.py`、`tools/mas_ondemand.py`、`docs/MAA_CLI_SURVEY.md`、`docs/HERMES_MAS_PLAYBOOKS.md`。

**实测输出（2026-09-28）**
- OCR 窗口路：`region=[968,365,464,261]`（464×261，非设备窗）→ `docs/audits/shots/2026-09-28_ocr_window_probe_mumu_thumb.png`
- OCR ADB 路：`1600×900`（"ADB 截图成功"）→ `docs/audits/shots/2026-09-28_ocr_adb_probe_idx13_guan1.png`
- 主机：`MAA_6` = MAA 6.18.0；`MAA_6\data\DepotData.json` 450B（mtime 09-28 06:32）；全主机无 OperBoxData.json；`gui.new.json` 实测 `OperBoxExportFormat="Clipboard"`、`OperBoxUseYituliuApi=false`、无 UserDataUpdateTask；`python --version`=3.12.9；`E:\MAA-CLI\bin\maa.exe` 在盘。
