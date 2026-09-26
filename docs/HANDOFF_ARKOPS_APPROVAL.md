# 审批单 → 执行端 交接契约（ArkOps 闸门 ⇄ MAAOrch 执行端）

出单方：`ark-ops`（达芬奇，审批闸门 + fairy 私信）。
执行方：本机 MAAOrch rig（`tools/maaorch_service.py --port 8765`）+ 日常机 `mrfz-0000 (100.79.173.69)` SSH/pwsh。
本文只定义**落盘接口**，不定义调度策略。
执行端的可执行实现：`tools/approval_preflight.py`（只读预检，退出码 0 = 可执行 / 1 = 拒）。

## 0. 红线（执行端会硬校验，违反即 refuse，不是警告）

- 只允许消耗 `sanity`（体力），其余资源/游戏数据一律不碰。
- 只允许官服；只允许 `adb_port` 命中的那一台；其他账号一律不动。
- 未审阅直接 `executed` → 抛错，不执行。

## 1. 目录布局（按 host 分目录，**已建好**）

```
D:\1-2\MAAOrch\queue\              # queue_root，两侧都用这个绝对路径
  local-16416/                     # 本机 #1 模拟器（MuMu index 1 / name=V / 127.0.0.1:16416，官服 751737728）
    pending/<id>.json              # ← 闸门写，执行端读
    done/<id>.json                 # → 执行端写回（含 result / after_state）
    refused/<id>.json              # → 锚点复核失败，附 reason，绝不执行
  mrfz-0000/                       # 日常机（100.79.173.69），走 SSH/pwsh，无 HTTP 口
    pending/ done/ refused/
  _fixtures/local-16416/pending/   # 自测夹具（selftest-good-001 / selftest-broken-002），不是真单
```

## 2. 单子 schema（闸门产出，字段全填，缺字段 = 拒收）

```json
{
  "id": "uuid4",
  "ts": "2026-09-24T20:45:00+08:00",
  "host": "local-16416",
  "action": "maa.task",
  "task_type": "维护/登录 | 刷关 | 剿灭 | 肉鸽",
  "params": { "stage": "CE-6", "mode": "普通", "times": 1 },
  "cost": { "sanity": 36, "sanity_source": "table", "sanity_reconcile": null, "zone": null },
  //   zone：只在 code 有「原版/复刻」多候选时必须给（如 SV-5 = act11sre_zone1(12) / act10d5_zone1(15)）
  "precondition": "campaign_quiesced",
  "anchor": {
    "adb_port": 16416,
    "uid": "751737728",
    "server": "官服",
    "uid_source": "lobby_ocr"          // 必须是切号后只读 OCR 到的大厅 ID
  },
  "evidence": {
    "ocr_uid_png": "runtime/_ce6/lobby.png",     // 相对仓库根或绝对路径，必须真实存在（C14）
    "fg_package": "com.hypergryph.arknights",    // 区服唯一证据：前台包名（胸牌没有区服字段）
    "fg_read_raw": "mCurrentFocus=Window{…. u0 com.hypergryph.arknights/com.u8.sdk.U8UnityContext}",
    "sanity_adjudicated": null,   // 两源真分歧关必须给，形如 "OF-F1=9"（谁按哪个源放的行要留痕）
    "account_switch": "MAA 切换账号 189****9036"
  },
  "approved_by": "user",
  "prev": "",
  "sha256": "…"
}
```

**锚点只用 `adb_port + uid + server`。** `index / emu_index / name / instance_id / emulator_label /
旧表里的 adb` 一律不可作锚点（执行端对这些键直接判 `C6` refuse）—— `~/mw_status.json`
（mtime 2026-08-20，盘上无任何进程/脚本写它）是孤儿快照：它的 `emu_index` 列对齐的是
**船队/日常机实例号**（emu_index 1 = 明日方舟-b-2，与日常机 MuMu 表逐条吻合），而 `adb` 列写的是
本机 MuMu12 的端口方案（16416/16448/…）。两套编号在"1"上正好撞车，这就是"以为在 #1、实际打到别的号"的根因。

### 2.1 sha256 口径（两侧逐字一致，否则必报断链）

```
sha256 = sha256_hex( json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False) )
core   = 本条去掉 "sha256" 键后的全部字段（"prev" 保留在 core 内）
prev   = 同 host 目录内上一条的 sha256（按文件名序）；链首写 ""
```

补签：`python tools/approval_preflight.py --sign <path/to/item.json>`。

### 2.2 代价必须有出处（C16 / C17）

**消费面**：`data/stage_apcost_v4.json`（sha256 `32ab99bc…`）的 `unit_cost` —— key 就是 rig 的 item 串
`"code|mode"`（`unit_cost["CE-6|普通"]=36`、`["10-10|突袭"]=21`、`["LE-EX-3|突袭"]=15`），消费方
**不必再判难度/前缀**：`#f#`(774) 与 `tough_`(85) 两套突袭写法已在表侧归一，主线突袭漏价那个坑不存在了。
语义：`int>0` = 标量价；**`null` = 无价**（游戏表 -1/0 的关、无突袭玩法的关，如 `OF-F1|普通`、`CE-6|突袭`）；
`dict` = **zone 变体**（今天 6 条，全 SV：`SV-1/2/3/5/6/7|普通`，`{"variants":{...}, "_请声明 zone":true}`）——
当数字用会 `TypeError`（fail loud），必须读 `variants` 并声明 zone。

**表身**：`stage_apcost_v3.json`（sha256 `0b026b63…`；主键 `stageId`、`code` 只作索引；
`ap ≤ 0` 落 `no_cost` 并区分语义 —— `-1` = 特殊/不消耗理智、`0` = 教学/剧情/TR，**0 不是免费**）。
执行端每次仍用 stageId 口径独立复算一遍与 `unit_cost` 交叉校验，不符 ⇒ `C16 表内不一致` 拒（不静默信一边）。
`stage_apcost_v2.json` / `stage_apcost.json` 只留作复算，不参与判定。

`cost.sanity_source ∈ {table, manual}`；`manual` 必须同时写 `sanity_reconcile: "required"`：

- **唯一价**（绝大多数）⇒ 执行端复算，不等即拒（`CE-6` 报 21 属这类）。
- **zone 多候选**（同一 code 原版/复刻不同价；今天 3 条 SV-5/6/7 = 12 vs 15）⇒ 必须 `cost.zone`
  显式声明打哪个区，**禁止静默挑值**；声明了才按该 zone 的价复算。SV-5/6/7 不是两源分歧，是 zone 变体。
- **无正值价**（`no_cost`：`PV-SP-1/2/3`、`OF-F1..F4` 等）⇒ 只能 manual。
- **两源真分歧**（`_divergences_genuine`，今天 7 条：CW-6/7、MN-6、OF-F1..F4）⇒ 必须先裁定，且
  **裁定值必须写进 evidence**：`evidence.sanity_adjudicated: "OF-F1=9"`，同时 `cost.sanity` 必须等于裁定值、
  `sanity_source` 必须是 `manual`；未裁定 ⇒ `C17` 拒（不许静默取一个）。
- 禁手写价表；`0`/负值一律降级 manual（纵深防御 —— 即便表侧已不生产 0 价）。

## 3. 执行端回写

```json
{ "id": "…", "executed_at": "…", "result": "ok|fail",
  "after_state": { "uid": "751737728", "sanity": 0, "task": "刷关 CE-6" },
  "sanity_spent_measured": 36,          // 放行前/后大厅理智读数差（manual 单必须给，供闸门事后对账）
  "executor": "maaorch@local-16416", "err": "" }
```

## 4. 执行端保证（编号即执行顺序）

0. **先清场（quiesce）**：设备必须在**大厅**。判据 = `GET /page` → `page == "大厅"`（rig 模板判据，
   右上角 ROI `(1000,0,1280,90)`，阈值 0.80 + 余量 0.06）。不在大厅 ⇒ 单子一律 refuse，原因写
   `not_quiesced` 并附实测 `page` / `page_score`，**不是**"锚点不符"。实测（2026-09-24 20:4x，扫矿进行中）：
   `/page` → `page='未知' (score=0.534)` ⇒ 此刻任何单子都拒。清场动作 = 等当前关打完 → 退回大厅 → 再预检；
   **绝不为了读 UID 去打断战斗。**
1. 复算 `sha256`（§2.1）并校验 `prev` 链；断链/不符 → `refused/`，不执行。
2. 读大厅 ID 与 `anchor.uid` 比对：`GET /look`（整屏 OCR）→ 正则 `ID\s*[:：]?\s*(\d{6,12})`，
   命中集合必须包含 `anchor.uid`，否则 `refused/`（附新旧 uid）。口径已实测：`_lobby_5554_uid.png`
   经 rig 同款引擎（rapidocr_onnxruntime）读出 `ID: 32822410` —— 大厅左下的胸牌就是「名字 + 服务器名 + `ID:<9位>`」；
   读不到就报 `uid_unreadable`，**不许猜**。
3. 只 `adb -s 127.0.0.1:16416`；**禁止**用 `emulator-*` 或"第一台设备"选择目标 —— 本机
   `adb devices` 同时挂着 `127.0.0.1:16416` 和 `emulator-5556`，已实测两者是同一台机器（/sdcard 内容逐字相同），
   而 `emulator-5554` 是另一个号（B 服 32822410），选错就是碰别的账号。
4. 执行结果落 `done/<id>.json`（含 `sanity_spent_measured`），然后回一条私信（借闸门的 `notify.send`，
   执行端不自建通道）。

静态断言（与清场无关，随时可算；`tools/approval_preflight.py --no-live` 里 C14/C15/C16）：

5. `C14` 证据胸牌与锚点自洽：`evidence.ocr_uid_png` 必须真实存在，且**离线 OCR** 出的 `ID:<数字>`
   必须包含 `anchor.uid`。实测：`runtime/_ce6/lobby.png` → `HODESID:751737728` ✅；
   `_lobby_5554_uid.png` → `32822410` ⇒ 拿它当 751737728 的凭证会被 C14 拦下。
6. `C15` 区服只认前台包名：`evidence.fg_package` 必须是 `com.hypergryph.arknights`；执行时还会**现场复读**
   `adb -s <serial> shell dumpsys window | grep -i mCurrentFocus` 再核一次。`pm list packages`
   不算证据（16416 上官方包与 `.bilibili` 同时装着）。解析必须要求包名带点（串里包名前蹲着无点的 `u0`）。
7. `C16` 代价与关卡价表自洽（见 §2.2）。
