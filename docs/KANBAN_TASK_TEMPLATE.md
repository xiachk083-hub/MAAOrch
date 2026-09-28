# 看板任务模板（复制即用）

- 配套规范：`docs/KANBAN_DISPATCH_POLICY.md`（v1，2026-09-27）。
- 用法：建卡时**整段复制**下面的模板改内容；工具建卡用 `kanban_create`，人建卡用 `hermes kanban create --body-file <文件>`（正文含换行/特殊字符时用文件，别用 `--body` 内联）。

---

## 一、完整模板（默认用这个）

```markdown
## 目标
<动词> <对象> → <交付物路径/形态>（一句话、可验收）

## 背景与已确认事实（别重推）
- 已有结论/审计: docs/audits/<文件>.md（读它，别重做）
- 权威数据源: <API/文件/日志的准确路径（本机或远端要分清）>
- 已确立判据: <上一波验证过的判定规则，防止重复踩坑>
- 环境红线（单发模式）: 禁 python -c / here-doc / powershell -Command / execute_code；
  Python 一律写 .py 文件执行（C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe）

## 前置与环境
- 独占资源: <adb/实例/侧车/班次窗口>；是否需要串行、要不要避开 05:00–05:40 / 19:30–20:30
- 现成工具: <tools/xxx.sh 等（用现成的，别自造样板）>
- 允许访问: <目录/端点最小集>；禁止对 D:/、E:/ 根做无范围 find/grep

## 步骤
1. <可直接执行的命令/动作，含期望输出>
2. <分支处理: 若 X ⇒ 做 Y；若发现冲突 ⇒ 停手并在注释里报告>
3. <收尾动作（清理/恢复现场/回滚路径）>

## 产出
- 文件: <绝对路径 + 命名规则>（禁止只留在 scratch 工作区——完结即删）
- 必含字段: <表格列 / 结论格式 / 证据引用格式>
- 同时在任务注释里贴: <摘要/结论>

## 验收
- [ ] <一条能复跑的检查> 应输出 <期望形态>
- [ ] <PASS/FAIL 判定条件>
- [ ] artifacts 已声明（kanban_complete(artifacts=[...])）

## 约束与红线
- <只读/可写边界>
- 绝不: <明确禁止的动作清单>
- 冲突处理: <与在跑任务/班次窗口冲突时怎么办>

## 预算
- 工具调用 ≤ <150>；工具时间 ≤ <10min>；超预算先 comment 汇报再继续
- 长卡兜底: max_runtime_seconds = <秒数>
```

---

## 二、精简版（小卡/巡检用）

```markdown
## 目标
<动词> <对象> → <产出路径>

## 已确认事实（别重推）
- <引用审计/权威源路径>
- 环境红线: 禁 python -c/here-doc/powershell -Command/execute_code；Python 用 .py 文件跑

## 步骤
1. …

## 产出与验收
- 文件 <绝对路径>；判据 <一条可复跑检查>

## 约束
只读 / 不碰实例 / 与班次冲突即停手报告
```

---

## 三、填好的例子 A：只读调查类

```markdown
## 目标
查清"<现象>"的逐台原因，产出 实例清单+判因+处置建议（不执行）→ 任务注释 + docs/audits/<date>_<slug>.md

## 背景与已确认事实（别重推）
- 判据: MuMuManager info -v 的状态面不可信；可靠判据 = MuMuVMMHeadless 进程命令行里的 --comment MuMuPlayer-12.0-<idx>
- adb 串口会漂: 用 `MuMuManager.exe adb --vmindex <idx> connect`，再从 `adb devices` 取增量 serial，别写死端口
- 已归档: docs/audits/2026-09-27_<相关审计>.md（先读）
- 环境红线: 禁 python -c/here-doc/powershell -Command/execute_code

## 步骤
1. 枚举在跑实例（进程法）；对每台取 serial 判前台包（dumpsys window）；
2. 前台是游戏包 ⇒ 正常；是桌面 ⇒ 进第 3 步；
3. 对异常实例读 <日志绝对路径> 末尾 40 行，按判据归类（a/b/c/d）；
4. 每台 1 张截图（可选）存 tmp/hermes_daily_check/<目录>/。

## 产出
表: 实例 | 脚本/账号 | 前台包 | 判定 | 最后活动时间 | 原因分类 | 建议
一句话结论 + 是否影响后续班次。

## 验收
- [ ] 每台在跑实例都有判定（无遗漏），异常项有日志证据行
- [ ] 结论明确"正常收尾 vs 异常"

## 约束与红线
只读: 不启动/停止/kill 任何实例，不发车不改配置；与在跑任务冲突 ⇒ 跳过并注明。
与班次窗口（05:00–05:40 / 19:30–20:30）重叠时只做只读查询。
```

---

## 四、填好的例子 B：实机操作类

```markdown
## 目标
以只读方式在 <实例> 上核验 <对象>，产出 对照表 + 截图 → 任务注释 + tmp/hermes_daily_check/<目录>/

## 背景与已确认事实（别重推）
- 链路: 脚本 <名> → MAA 目录 <路径> → MuMu 实例 index=<N>（标题 <...>）→ 包名 <...>
- 已知锚点: <已确证的 UID/掩码对照，来源审计>
- 环境红线: 禁 python -c/here-doc/powershell -Command/execute_code

## 前置检查（必须先做）
1. 主机上没有 MAA 在跑本实例（进程列表核对）；在跑 ⇒ 停手并注释报告；
2. 实例 adb serial 现查（MuMuManager adb --vmindex <N> connect → adb devices 增量）；
3. 必要时 launch 并轮询 sys.boot_completed=1。

## 步骤
1. …
（每步含精确命令/坐标/等待秒数；每步失败的处理分支）

## 产出
- 表格: <列定义>
- 截图路径 + 字节数
- 异常记录条目

## 验收
- [ ] 对照表完整（每项有值或明确"不在列表"）
- [ ] 截图可复核（路径已声明）

## 约束与红线
- 只点 <允许的控件>；绝不点 <禁止的控件清单>
- 不跑任务、不发车、不 kill、不改配置、不动其它实例
- 冲突（有人/有任务在用）⇒ 立即停手报告
- 让车: 若临近 19:30/05:00 班次窗口 ⇒ 停手（或按卡内指定时间让位）
```

---

## 五、建卡机读字段速查（`kanban_create` / CLI 等价）

| 字段 | 怎么定 |
|---|---|
| `assignee` | 必须是**已存在**的 profile 名（未知 assignee 的卡会永远躺在 ready） |
| `priority` | 3=抢修止损（同波 ≤2）/ **2=默认** / 1=可延后 / 0=待定；判据见规范 §3.2 |
| `parents` | 产物依赖才挂（全 done 才解锁）；仅共享资源 → 用时间窗，别挂 |
| `idempotency_key` | 周期卡/巡检卡/高危重发必带，如 `maa-bench-<slug>-<yyyymmdd>` |
| `max_runtime_seconds` | 长卡兜底（45m=2700）；超时计失败，2 次自动止损 |
| `skills` | 需要专门技能时挂（本板一般不用） |
| `workspace_kind` | 默认 scratch（完结即删）→ 产物放 durable 路径并声明 artifacts |
| 建卡前 | `hermes kanban list --status running` 查重（规范 §6.2） |

### 建卡命令样例（bash，等价 CLI）

```bash
# 1) 常规只读卡：默认优先级 2，允许一次自动重试
hermes kanban create "标题：<对象>（只读）" --assignee mas --priority 2 \
  --body-file task.md --idempotency-key maa-bench-<slug>-<yyyymmdd> --max-retries 2

# 2) 有副作用/高风险卡：失败即停 + 长卡兜底
hermes kanban create "…" --assignee mas --priority 3 --body-file task.md \
  --max-retries 1 --max-runtime 45m

# 3) 依赖卡：等上游全部 done 才 ready
hermes kanban create "…总纲…" --assignee mas --priority 1 --body-file task.md \
  --parent <上游id1> --parent <上游id2>

# 4) 评审卡：实现完由 reviewer 复核
hermes kanban create "复核：<上游卡> 的产出" --assignee default --body-file review.md --parent <上游id>
```

一行查重（worker 上下文必须带 `env -u HERMES_DELEGATED_CHILD_CONTEXT`，原因见规范 §9.3）：

```bash
env -u HERMES_DELEGATED_CHILD_CONTEXT hermes kanban list --json \
  | grep -o '"title": *"[^"]*"' | sort | uniq -d   # 有输出 = 有重复标题组
```


## 六、建卡人自检（30 秒）

- [ ] 目标句能验收？产出有绝对路径？
- [ ] "已确认事实"引用了现有审计/权威源？（防重推）
- [ ] 本地/远端路径分清？搜索范围写死？
- [ ] 资源类任务已避开其它卡与班次窗口？
- [ ] 查重做过了？周期卡带 key？
- [ ] 预算与 max_runtime 写了？
