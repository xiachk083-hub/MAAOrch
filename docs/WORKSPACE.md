# 排轴工作区 —— 一个账号 × 一个活动，一次生成

> 由来（2026-09-16 用户）：**从"一关一关生成"改成"一个账号一次生成整个活动"**。
> 编辑器加账号+活动导航，选完进活动主页，点关卡进编辑；每关带候选作业、最终通关、备注。
> 生成器：`tools/workspace.py`（入口 `tools/viz_plan.py --workspace …`）。
> 单关编辑器本身见 [`PLAN_EDITOR.md`](PLAN_EDITOR.md) —— 本文只讲"工作区"这一层；
> **修改树 / 规则入库 / 反哺 / 关卡逻辑 / 账号分组见 [`JOB_SYSTEM.md`](JOB_SYSTEM.md)**（闭环那一层）。

---

## 一、它解决什么

以前：`--stage act54side_05` 一关一关来，换个账号要重来一遍，谁改过哪关、改到哪一步、哪关过了，
全靠人记。现在：

- **一次生成整个活动**（SR 系列 16 关一条命令），每关都有自己的编辑器页；
- **按账号组织**（`data/workspaces/<账号>/…`），候选作业是按**这个号的 box** 匹配出来的；
- 每关留三样东西：候选作业、人改完的 `cleared.json`、备注；
- 同关不同账号的 `cleared.json` 放一起 = **差异数据**（AI 给的对不上人改的，差在哪就是标注）。

---

## 二、目录结构

```
data/workspaces/                       ← .gitignore 里（有账号 box，绝不入库）
  index.html                           工作区根主页：账号列表
  activities.json                      （可选）活动中文名 { "act54side": "月行水上" }
  <account>/
    box.json                           生成时那份 box 的快照（结论只对这份 box 成立）
    index.html                         账号主页：活动列表（关卡数 / 已通关数）
    <activity>/
      index.html                       活动主页：关卡 × [N 份作业] × [已通关/未通关]
      <CODE>/                          目录名 = **关卡代号**（SR-1），不是 stageId
        stage.json                     {stage_id, code, name, group, has_hard, level_status…}
        index.html                     这一关的编辑器页（viz_plan 的 HTML + 工作区横条）
        jobs/*.json                    候选作业（原文件**字节复制**，不含任何改动）
        manifest.json                  全部候选的匹配结论（挑中的与淘汰的都留档）
        versions/                      修改树：v1_ai → v2_human → …（每节点带 changes/note）
        cleared.json                   人改完保存的最终通关（MAA 作业格式，可直接跑）
        cleared_meta.json              什么时候、从哪份版本存下来的（溯源，含 version id）
        notes.md                       人工备注：为什么改、改了哪里
```

**目录名用代号、stageId 存在 `stage.json` 里**：人看的是 `SR-1`，机器要的是 `act54side_01`，
两个都留、谁也别猜谁。`find_stage_dir()` 两种键都认（`--stage SR-1` 与 `--stage act54side_01` 等价）。

---

## 三、三条口径（是定的，不是没做完）

1. **候选排除自产件**：`ga_derived/`、`*_adapted`、`doc.title` 含「编排」的一律不当候选
   （拿自己的半成品推荐给自己是错的，口径与 `tools/batch_index.py` 一致）。要看自产件加 `--include-derived`。
2. **缺关卡数据就去拉**：`data/_level_<stage>.json` 不存在时从镜像取
   （`levels/<levelId>.json`，**levelId 要整串小写** —— `Activities/…` 大写一律 404，实测）。
   拉不到就生成一个**兜底页**：候选清单照旧可看、可跑 `viz_plan` 手工画，**整活动不会因为一关没数据整体失败**。
3. **机器拥有 vs 人拥有**：`jobs/` 是机器拥有的（重生成时清掉上一轮遗留的），
   `cleared.json` / `notes.md` 是人的产出（**任何重生成都不覆盖**）。

---

## 四、怎么用

```bash
# ① 一次生成整个活动（缺的关卡数据会自动拉）
python tools/viz_plan.py --workspace 9036 --activity act54side

# ② 只重生成某一关（与整活动同一条代码路径，行为不会分叉）
python tools/viz_plan.py --workspace 9036 --activity act54side --stage SR-5

# ③ 看有哪些账号/活动/关卡（也告诉你作业池里最近的活动 id）
python tools/workspace.py --list

# ④ 起可写服务（「保存为 cleared」需要它）
python tools/ws_server.py                 # http://127.0.0.1:8897/
python tools/ws_server.py --open          # 起来后自动开浏览器
python tools/ws_server.py --host 0.0.0.0  # 手机同 WiFi 也能开（无鉴权，只写 data/workspaces）

# ⑤ 静态托管时（没有写接口）的收编回路：页面导出 → 命令行落盘
python tools/workspace.py --save-cleared 9036 act54side SR-5
python tools/workspace.py --save-cleared 9036 act54side SR-5 "C:\Users\me\Downloads\act54side_05__xxx__20260916.json"

# ⑥ 只重建索引页（保存后活动主页的「已通关」没刷新时）
python tools/workspace.py --rebuild-index
```

可选参数：`--limit N`（每关挑几份候选，默认 6）、`--include-derived`、`--no-fetch`（不联网拉关卡数据）。

---

## 五、编辑器里多了什么

工作区生成的页面比单关页面多一条**横条**（由 `D.ws` 驱动，单关 `--stage` 生成的页面完全没有它）：

```
← 活动主页 | ⌂ 工作区 | SR-2 月涌 | 已通关 ✓ | 按 data/operbox_batch/emu2_9036.json 的练度算 ｜ 下拉 5 份
[账号 9036 ▾] [活动 月行水上 ▾] [保存为 cleared] [保存备注]   ← 消息
[ 备注 textarea ]（写 notes.md；跟着关卡走，不跟着作业下拉走）
```

| 元素 | 行为 |
|---|---|
| **账号 / 活动下拉** | 直接跳到那个主页（静态托管没有后端可查，链接**在生成时就写死**进页面） |
| **作业下拉** | `cleared.json` **排第一位**（"下次直接用"），后面才是候选；已存的通关版不会被原始候选盖住 |
| **保存为 cleared** | POST 给 `ws_server.py` → 写 `cleared.json` + `cleared_meta.json` + `notes.md`，并把活动主页刷成「已通关 ✓」 |
| **保存备注** | 只写 `notes.md` |
| **编辑仍存在 localStorage** | 键按**作业 src** 分槽（`cleared.json` 与各候选各占一槽，互不串位） |

**保存有两条路，不是一条半**：

- 走 `ws_server.py`：点一下写盘（POST `/api/ws/save`）；
- 走静态 `python -m http.server`：没有写接口（POST 501）→ 页面**自动改下载**并告诉你跑哪条命令收编
  （`--save-cleared`）。这与既有的 `viz_plan --ingest` 是同一个道理：浏览器写不进仓库目录，
  **导出→导回是一条命令、不是零命令，这是设计前提**。

服务还提供 `GET /api/ws/list`（账号→活动→关卡+是否通关的 JSON），页面和脚本都能用。

---

## 六、差异对比 = 标注

```bash
# 两份文件
python tools/job_diff.py A.json B.json
python tools/job_diff.py A.json B.json --json          # 结构化（喂训练/落库）
python tools/job_diff.py A.json B.json -o diff.json

# 各账号工作区里同一关的 cleared.json 两两对比
python tools/job_diff.py --stage act54side_01
python tools/job_diff.py --stage act54side_01 --a 9036 --b emu2 --json -o pair.json
```

输出是**结构化条目**（`step / type / from / to / name / note`），不是给人看的文本 diff：

```json
{"step": 2, "type": "location", "name": "圣聆初雪", "from": [5,2], "to": [5,4],
 "note": "圣聆初雪 的位置 [5, 2] → [5, 4]"}
```

类型有 `operator`（换人/换操作）、`location`、`direction`、`costs`/`kills`/`pre_delay`（条件字段）、
`action_added`/`action_removed`、`opers_added`/`opers_removed`/`opers_skill`（**跨账号时这类最多**：
两个号有什么人根本不同）、`groups`。

**为什么不按下标 zip**（规格里的写法）：作业改一行（多加一次撤退）就会让后面全部错位，
报出来的"第 3 步换了人"其实是"第 3 步前面插了一条"。这里先用 `difflib.SequenceMatcher`
按 `(动作类型, 干员名)` 对齐，再逐步比字段 —— 插入/删除/替换都能落到正确的位置上。

---

## 七、实现要点（改代码前先读这几条）

| 点 | 说明 |
|---|---|
| **按账号 box 算** | `viz_plan.build_payload(stage, lv, jobs, box_path=…)` 会把 box_path 写进模块全局 `BOX_PATH`：射程（`ranges_payload→box_ops→load_box`）、部署费用（`CM.load_elites`）、逐秒决斗（`duel_model.duel` 内部走 `load_box()`）**全都**吃这个全局。不给就退回默认 box（`emu2.json`）→ 结论会按别人的练度算。 |
| **作业池索引按 `stage_name`** | 不用 `viz_plan.scan_jobs()` 的文件名前缀扫描（那要求文件名以 `<stage>_` 开头）。全量索引只有 319 个 json、glob 一次 0.01s，一次生成建一次。 |
| **数组文件不是作业** | `*_jobs_dump.json` 这类顶层是数组的是**容器**，不进候选（取首元素等于拿"20 份里的第 1 份"冒充这一份）。`viz_plan.load_jobs()` 也改成明确跳过而非崩在 `AttributeError`。 |
| **同一份作业被下到多目录** | 只按 prts id/文件名去重会漏（同一 id 在不同目录下文件名前缀不同）。按 `(actions, opers, groups)` 的哈希去重，重复的记进 `duplicates`。 |
| **`rank_candidates(allow_low_squad=True)`** | 引擎默认把需求 <6 的作业直接丢掉，而 SR 这类活动关本来就少人（首选只有 2 个干员）——丢掉的往往正是最该看的。这里开着并把 `low_squad` 只当排序降权项。排序 = (缺人, 练度缺, 软需求, 自产件, 低配, -动作数)。 |
| **`stage_name` 会被纠正** | 保存时以 `stage.json` 里的 stage_id 为准（存错关卡会让下次跨账号比对整片错位）。 |
| **端口 8897** | `8898` 是文档里 ad-hoc 静态服务占的位置（`cd Downloads && python -m http.server 8898`），`8899` 是可视化页的静态服务。三个别撞。服务用 `allow_reuse_address=False`：端口被占就报错，**不和别的 http.server 悄悄共用**（Windows 上默认会共用一个 0.0.0.0、一个 127.0.0.1，保存行为会因机器而异）。 |

---

## 八、边界与注意

1. **写范围只在 `data/workspaces/`**：账号/活动/关卡三个名字都过 `_safe_name`（只允许字母数字._-，拒 `..`），
   路径再 resolve 校验一次。监听局域网时仍**没有鉴权**（`--host 0.0.0.0` 前想清楚：里面的 box 是账号数据）。
2. **`data/workspaces/` 不进公开仓库**（`.gitignore` 的 `data/` 已覆盖）：里面有账号干员箱与作业，
   属客户数据（见记忆「公开仓库隐私原则」「绝不读设备里的应用数据」）。
3. **关卡数据来源**：`levels/<levelId>.json` @ Kengxxiao/ArknightsGameData（与 `tools/stage_value.py` 同源的三个镜像）。
   镜像限流/挂掉时该关走兜底页，不影响其它关。
4. **装置冲突按关登记**：`viz_plan.DEVICE` 里没有的关卡既不画装置也不报"无人站"（避免拿 ex02 的 [5,3] 在别的图上胡说）。
5. **单关模式没变**：`python tools/viz_plan.py --stage act16side_ex02` / `--ingest` 的行为与产物与以前一致，
   工作区只是多了一条生成路径（共用 `build_payload`，字段集合一致）。

---

## 九、验收（2026-09-16 实测）

| 项 | 结果 |
|---|---|
| 一次生成整个活动 | `--workspace 9036 --activity act54side` → 16 关 / 16 个编辑器页（15 关关卡数据为首次联网拉取，之后 cached） |
| 按账号组织 | `9036`（391 人）与 `emu2`（252 人）各生成一份，**同一关的最优候选不同**（SR-1：9036→101746 / emu2→101906） |
| 导航 | 浏览器实测：横条面包屑、账号下拉（9036/emu2）、活动下拉、作业下拉（cleared 排首位）均正常，无 JS 报错 |
| cleared 能保存 | 浏览器点「保存为 cleared」→ 写盘；改过的坐标 `[7,2]` 落在 `cleared.json` 里；活动主页变「已通关 ✓」 |
| 失败兜底 | 写接口不可达时自动改为下载并给出 `--save-cleared` 命令；该命令实测收编成功（12 动作） |
| 差异可对比 | `job_diff --stage act54side_01` 报出 5 处差异（含 2 处跨账号换人与编队差异） |
