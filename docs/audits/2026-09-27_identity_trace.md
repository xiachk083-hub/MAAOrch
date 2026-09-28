# 每轮身份留痕（mas_identity_trace）— 落地与 09-27 首跑

- 日期: 2026-09-27（夜班 t_6f159f59）
- 起因: 09-27 凌晨手动多号任务出「串号」——官-1-1-7 的 R1/R3 实际跑在 139 号（潮响）上，且 MAA_2 出同号重登（无
  `账号切换` 行）导致「日志看起来正常」。结论：**切换行只是佐证，实机 UID 才是真值**，需要机器可回归的每轮身份留痕。
- 性质: 全程只读（读 gui.log/app.log + 截图 + OCR），不写远端、不发车、不改配置。

## 1. 交付物

| 文件 | 说明 |
|---|---|
| `tools/mas_identity_trace.py` | 新工具（1078 行，纯 stdlib + 可选 rapidocr），子命令 `watch`/`ocr`/`report`/`selftest` |
| `runtime/identity_trace/t_6f159f59/report_20260927.md` `.json` | 09-27 全天窗口对照报告（下见 §3） |
| `runtime/identity_trace/uid_map.json` | 掩码→实机 UID 基线（官-1-1-7 四号已取证；官-2-4-47 仅 192 号已取证，其余 null 待补） |
| `runtime/identity_trace/user_map.json` | 脚本→[name/mask/uuid]（**无原始号码**，仅掩码） |
| `runtime/identity_trace/t_6f159f59/frames/`+`frames_manifest.jsonl` | 6 张证据帧（今晚 3 张：MAA_6×2 + MAA_2×1；凌晨负控制 3 张）及 dir/ts 索引 |
| `runtime/identity_trace/smoke_20260927*/` | watch 冒烟输出（12min/21 轮询，抓到 21:20–21:22 真实事件 6 条，干净退出） |

## 2. 工具用法（全部只读）

```bat
:: ① 实时留痕（发车前起、任务后停）：盯 gui.log 抓帧 + 记 app.log 轮边界
python tools\mas_identity_trace.py watch --duration-min 90 --shot-delays "12,45" --post-delay 8
::   --deadline HH:MM / --quiet-min N / --no-shots / --outdir ... 可选；默认目标 官-1-1-7=MAA_6:13;官-2-4-47=MAA_2:14

:: ② 帧 OCR（必须 Python312：带 rapidocr+cv2）
C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe tools\mas_identity_trace.py ocr --outdir runtime\identity_trace\<run>

:: ③ 离线对照报告（本地拉好日志时用 --logs/--applog；否则自动 ssh type 拉取）
python tools\mas_identity_trace.py report --since 2026-09-27 --until 2026-09-28 ^
  --outdir runtime\identity_trace\<run> --frames-manifest ... --uid-map ... --user-map ...

:: ④ 解析器自检（无需联网）
python tools\mas_identity_trace.py selftest
```

判定语义（身份优先）：帧级 ID 核对（绿）> 日志级切换行（黄绿）> 「无切换行且无帧证」（黄，需补帧）> 轮失败/未完成（灰）；
**实测≠期望 → 🔴 标红**（帧或切换行任一错配都红）。

## 3. 首跑结果（09-27 全天窗口，RED=1）

官-1-1-7（MAA_6/实例13，序列 134→139→130→187）：

| 轮 | 用户@期望 | 切换行 | 帧证 | 判定 |
|---|---|---|---|---|
| 04:49 | 汐影月k@134 | — | — | ⚠️ 无切换行且无帧证（凌晨被串号那轮；当时无留痕工具） |
| 05:14 | 潮响@139 | — | ID:551601505→139 | PASS（帧级）(尝试3次失败3) |
| 05:19 | tbNick@130 | — | ID:551601505→139 | **🔴 错配：实测139≠期望130（负控制验证成功）** |
| 10:54 | 汐影月k@134 | 134 | — | PASS（日志级） |
| 11:15 | 潮响@139 | 139 | — | PASS（日志级） |
| 11:31 | tbNick@130 | 130 | — | PASS（日志级）(尝试2失败1) |
| 12:03 | him@187 | 187 | — | PASS（日志级）(尝试2失败1) |
| 19:45 | 汐影月k@134 | 134 | ID:568685565→134 | **PASS（帧级）** ← 今晚修复后首轮，双证吻合 |

官-2-4-47（MAA_2/实例14，序列 192→189→139→150）：

| 轮 | 用户@期望 | 切换行 | 帧证 | 判定 |
|---|---|---|---|---|
| 05:03 | y9Flipped@192 | — | — | ⚠️ 需帧证（同号重登无切换行）(尝试2失败1) |
| 05:31 | 古茶@189 | — | — | ⚠️ 无切换行且无帧证 |
| 19:33 | y9Flipped@192 | — | ID:64323902→192 | **PASS（帧级）**（无切换行=同号重登，帧证判定正确） |

关键发现：
1. **红色检测在真实数据上有效**：凌晨 tbNick 轮的帧（05:21:24）直接读出 `ID:551601505`（=139 号）≠ 期望 130 —— 这正是「理智上限 210 异常」推断的实锤。
2. **同号重登**（192 两轮均无切换行）由帧证兜底 —— 单纯看日志会误判「无切换=可疑」，帧证后是 PASS。
3. 官-1-1-7 白天/晚班各轮「切换行=期望掩码」全对，与工单方向一致。
4. **活体验证齐**：`selftest` 通过；21:16–21:28 冒烟 watch（21 轮询）+ 静默正确、21:20–21:22 真实捕获 6 条 app.log 事件（当时另一脚本在 MAA_11 跑动）后干净退出；截图探针 13/14 号实例各 1 张（1.27MB / 883KB）成功；PII 扫描确认全部产物只含掩码、无裸号。

## 4. 建议接线（最小改动）

1. **巡检 job 52b88b0ffca0**：现有 `SWITCH_GATE`（只看切换行）之后，追加一次
   `report` 干跑（日志直拉，无需帧）→ 输出 `IDENTITY_TRACE=OK|RED:n`；有 RED 时附明细行。0 成本、纯只读。
2. **发车/首轮验收（t_38b8ef3c 一类）**：发车前 `watch` 随任务起，`--deadline` 到任务尾；产出 events+shots 供 report 二跳核对。
3. **uid_map.json 维护**：每新增号，做一次「抓连接页帧 → OCR 抄 ID」入表（一次性 30 秒）；帧 OCR 仅需 Python312。
4. 报告并入晚班复盘（docs/audits/ 或看板评论），RED 即人工介入。

## 5. 边界与局限

- 帧证要求游戏在前台显示 `ID:xxxxxxxx`（官服连接页/大厅）；桌面/加载页拍不到属正常（如 21:16 探针只拍到 MuMu 桌面）。
- 非官/B 服等无 ID 显示面的场景只能日志级；跨服不适用。
- 「同号重登」无切换行，只有帧证能判；watch 抓帧是唯一可靠来源。
- app.log 因后端崩溃缺「已完成」行（僵尸任务）不影响身份结论（身份优先判定）。
- UID 基线未入表的号，帧证列报 UID 原文、判定降为 ⚠️。

## 6. 文件路径速查

```
D:\1-2\MAAOrch\tools\mas_identity_trace.py
D:\1-2\MAAOrch\runtime\identity_trace\uid_map.json
D:\1-2\MAAOrch\runtime\identity_trace\user_map.json
D:\1-2\MAAOrch\runtime\identity_trace\t_6f159f59\{report_20260927.md,report_20260927.json,frames\,frames_manifest.jsonl,ocr\,ocr_index.json,probe\}
D:\1-2\MAAOrch\runtime\identity_trace\smoke_20260927\{watch.log,status.json,account_map.json}
```
