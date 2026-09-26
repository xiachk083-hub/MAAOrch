# 卫戍协议（SPA / 堅守協定：盟約）进场导航 —— MaaFramework / MXU 版

**导航**：`D:\Xiach\M-A\MAAW\maa-arknights-fw\resource\global\YoStarJP\resource\pipeline\maaorch_spa.json`
**日服模板**：同 Bundle 的 `image\`（6 张，全部从实机截图裁）
**入口任务**：`MAAOrch_SPA_Enter`（interface.json 里叫「卫戍协议进场」，只挂在**日服**资源下）
**设计文档**：本文件；改动前先读「验证现状」与「已知坑」

---

## 1. 资源分层：为什么放在 `global/YoStarJP`

MAA（MaaCore）的做法是 **base 资源 + 服务器覆盖**：`resource/global/YoStarJP/resource/{tasks,template,PaddleOCR}`（实测：55 个 task 文件 + 493 个模板）。MaaFramework 对应做法是**多 Bundle 依次加载，后加载的覆盖先加载的**（同名字点/模板被覆盖）。

所以本项目的布局是：

```
resource/                              ← Bundle 1（主资源，跨服通用）
│   ├─ pipeline/  (bridge.json, maa_all.json, diagnostic.json)
│   ├─ image/     ← 是指向 MAAW\MAA\resource\template 的 Junction（公共模板，别往里写自研图）
│   └─ model/
└─ global/YoStarJP/resource/           ← Bundle 2（日服覆盖，可放心的自研内容）
    ├─ pipeline/maaorch_spa.json
    └─ image/MAAOrch_SPA_*.png
```

`interface.json`：

```jsonc
"resource": [
  { "name": "主资源", "path": ["resource"] },
  { "name": "日服",   "path": ["resource", "resource/global/YoStarJP/resource"] }  // 后加载覆盖前者
],
"task": [ { "name": "卫戍协议进场", "entry": "MAAOrch_SPA_Enter", "resource": ["日服"] } ]
```

三份 interface.json 已同步：`FW\mxu\interface.json`（源）、`MXU\interface.json`（start.bat 会拷过去）、`MAAOrch\runtime\pi_cli\interface.json`（我用 MaaPiCli 跑的无头副本，路径写绝对）。

## 2. 节点链（带自愈）

```
MAAOrch_SPA_Enter (DirectHit)
├─ [JumpBack] PopupOffline   ← 掉线/离线确认（OfflineConfirm.png）
├─ [JumpBack] PopupConfirm   ← 通用红确认条（PopupConfirm.png）
├─ HubSolo   已在卫戍主页 → 点「単独演算」 ─┐ max_hit 3，失败/未跳转自旋重试
├─ StartSim  演算类型页 → 点「演算開始」   ─┤ max_hit 2
├─ Ready     情報確認页 → 点「準備完了」   ─┘ max_hit 4（等 8s 给它出现）
├─ LobbyTerminal  大厅 → 点「端末」 → TerminalCard →（点活动卡片）→ HubSolo
└─ Escape    BACK(key 4)，最多 3 次，terminal
```

**自愈三件套**（MaaFramework v5.x 的正确姿势）：
- `[JumpBack]`：取代 **v5.1 已废弃的 `interrupt`**。把"随时会冒出来的弹窗"放 next 最前面，处理完自动跳回原列表继续 —— 这就是"中途出问题还能接着走"。
- `max_hit`：节点最多命中几次，用来做**有界重试**（自旋 `next: [..., 自己]` 时不会死循环）。
- `on_error`：识别超时或动作失败后的兜底（HubSolo 上挂了 `Escape`）。
- 另外 `pre/post_wait_freezes`（等画面静止）专治"页面自己跳走"的竞态 —— 我一开始就是在 `情報確認`（18s 倒计时）上踩了这个坑：探测时还在、动手时已翻页。

## 3. 验证现状（2026-09-22）

| 项 | 证据 |
|---|---|
| 模板辨识度 | 6 张模板在各自页面 **1.0000**，跨页全 **0**（`tools\mpelb_vision.mjs` 离线固定图识别） |
| 真机点击链 | ✅ **端末 → 活动卡片 → 単独演算 → 演算開始 → 準備完了 → 2/2 戦術選択**（MaaPiCli 实跑，`runtime\after_run*.png` 逐张留档） |
| 掉线/弹窗自愈 | ⏳ 机制已挂上，尚未构造真实掉线场景验证 |
| 戦術選択 → 开局 | ⏳ 该页有 28s 倒计时自动推进；若要卡点确认需再补一个模板 |

## 4. 怎么跑

**A. MXU GUI**：`FW\start.bat`（拷 interface.json → 启动 mxu.exe）→ 资源选「日服」→ 任务勾「卫戍协议进场」→ 运行。

**B. 无头（MaaPiCli）**：
```powershell
$d='D:\1-2\MAAOrch\runtime\pi_cli'      # MaaPiCli + DLL + interface.json（绝对路径）
Set-Location $d
"6`n" | & "$d\MaaPiCli.exe"             # 6 = Run tasks；设备/资源已存在 config\maa_pi_config.json
```
改设备：`config\maa_pi_config.json` 的 `adb.{adb_path,address}`；改资源：`resource` 字段（`主资源` / `日服`）。
⚠️ MaaPiCli **按 exe 同级找 interface.json**，且 adb 需要完整 config（MuMu 要 `extras.mumu`）——用菜单里的「Auto detect」选的设备才带全配置。

**C. 只识别不点击（安全）**：
```powershell
node tools\mpelb_debug.mjs --node MAAOrch_SPA_Enter --mode recognition-only
```

## 5. 待办

- [ ] 戦術選択（2/2）之后：需要的话补「決定/確認」模板，或直接交给 MaaCore 的 `MiniGame@SPA@*` 玩局
- [ ] 构造一次掉线/断连，验证 `[JumpBack]` 弹窗自愈真的接得住
- [ ] 日服覆盖里要不要也放 OCR 配置（对标 MAA 的 `global/YoStarJP/resource/ocr_config.json`）
