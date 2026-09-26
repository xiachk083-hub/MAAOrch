# 结论抽查裁决：docs/audits 已提交结论 vs 当前代码

> 2026-09-26 · 只读抽查 · **未执行任何 git 命令** · 只写本文件。
> 事实源 = 工作区当前代码；每条给「判定 / 证据 文件:行 / 复现命令」。

## 0. 总判定

| # | 抽查项 | 判定 |
|---|---|---|
| 1 | G3 health 杀进程 6 处被 _kill_gate 拦住 | ✅ 一致（就是 6 处） |
| 2 | G5 emit_finished / remove_dispatch / .pyw 注册 | ✅ 一致 |
| 3 | bind 缺省 127.0.0.1 + 显式 0.0.0.0 仍生效 | ✅ 一致 |
| 4 | api_fastapi.py 业务路由 49 条 | ✅ 一致 |
| 5 | docs/http-api.md 无未标注失效端点 | ❌ **不符：:497 GET /api/logs 未标注** |

## 1. G3（services/runtime_health.py）
- 总闸定义 `_kill_gate` = :94-108；`check_health` 顶部唯一解析 `_kill_gate_on` = **:129**（注释 :128「六处共用」）。
- 并列守卫 `if not _kill_gate_on:` 共 **6 处**：
  1. :172 僵尸 MAA（杀 :178 taskkill）  2. :198 幽灵 MAA（:204 taskkill）
  3. :247 无主 MAA 系统扫描（:253 taskkill）  4. :288 卡死 adb 命令进程（:297-301 PowerShell Stop-Process）
  5. :326 MuMu 崩溃弹窗（:332 taskkill /IM）  6. :353 僵尸 VMM（:389 taskkill）
- 结论：**6 处**（此前报 4/5 均错），与 docs/audits/2026-09-26_glue_verify_p2.md:82-93 一致。
- 复现：`(Select-String -Path services/runtime_health.py -Pattern 'if not _kill_gate_on').LineNumber` → 172,198,247,288,326,353

## 2. G5（services/runner.py + main_web.pyw）
- `emit_finished` = runner.py:285；**逐订阅者 try/except**（with warn 留痕）= :295-304。
- `remove_dispatch` 受保护：主路径 :3175-3179（try/except+warn）；connect-only 路径 :3151-3160 亦然。
- **main_web.pyw 真的注册了**（此前只扫 .py 漏 .pyw 误判过）：:183 `runner._finished_callbacks.append(launch_queue.on_account_finished)`；另有 :195 gantt 订阅者（共 2 个订阅者）。
- 结论：与 docs/audits/2026-09-26_glue_verify_p2.md:13-14,34-41,63-72 一致。
- 复现：`Select-String -Path main_web.pyw -Pattern '_finished_callbacks.append'` → 183,195

## 3. bind（缺省 127.0.0.1；显式 0.0.0.0 仍生效）
- `DEFAULT_CONFIG["bind_address"]` = **"127.0.0.1"**：models/config_manager.py:18。
- `load_config` 仅缺键时 setdefault：config_manager.py:93（不覆盖已存的 "0.0.0.0"）。
- `main_web.pyw` 的 `_bind` 缺省 = **"127.0.0.1"**：main_web.pyw:336；传入 uvicorn `host=_bind`：:347。
- 显式 `0.0.0.0`：`config.get("bind_address", "127.0.0.1")` 原样返回配置值 → **仍生效**；无「非回环即拒启」硬闸（bind_and_exposure.md §5 的加固只是建议，未实现）。
- 注：models/config.json 当前**无** `bind_address` 键 → 运行期靠 :93 补成 127.0.0.1。未起服务，以上为静态判定。
- 复现：`Select-String -Path models/config_manager.py,main_web.pyw,network/api_fastapi.py -Pattern 'bind_address'`

## 4. 前端退役：49 条路由
- `network/api_fastapi.py` 内 `@app.` 共 **50** 处；其中 :200 是 `@app.middleware("http")`（非路由）→ 业务路由 = **49**。
- 结论：与 docs/audits/2026-09-26_frontend_retire_cleanup.md:4、2026-09-26_doc_drift_cleanup.md:11,18 一致。
- 复现：`(Select-String -Path network/api_fastapi.py -Pattern '^\s*@app\.').Count` → 50；另看 :200 为 middleware。

## 5. docs/http-api.md 残留失效端点 —— ❌ 发现 1 处
- **docs/http-api.md:497 `GET /api/logs?lines=100` 无失效横幅**（:499 说明、:502 响应示例均为现在时）。
- 紧邻的 docs/http-api.md:514 `GET /api/maa/log` 就有横幅（:516）。
- `/api/logs` **不在** 49 条 Python 路由内 → 按 task-27 自称的事实源（49 路由）判定为「已删」。
- 故此条与 docs/audits/2026-09-26_doc_drift_cleanup.md:13,102「未标注的残留漂移 = 0」**矛盾**；也与 docs/audits/2026-09-26_frontend_retire_cleanup.md:126 把 `/api/logs` 列为已删端点互相矛盾（同批两份文档对同一行给出相反结论）。
- 唯一可辩护点：Node 门面有原生 `/api/logs`（server/maaorch-web.mjs:934），在 19999 上仍可用；但本档 :12 明确以 Python API(19997) 为对象，Python 侧确无此路由。
- 复现：`Select-String -Path docs/http-api.md -Pattern 'api/logs|api/maa/log'`；`(Select-String -Path network/api_fastapi.py -Pattern '"/api/logs').Count` → 0

## 6. 矛盾清单（文档 vs 代码）
1. **主矛盾（本次新发现）**：http-api.md:497 `/api/logs` 未标注 vs doc_drift_cleanup.md:13/102「残留=0」；两份 dated 审计互相打架（frontend_retire_cleanup.md:126 曾正确列为已删）。→ **文档错了**：要么给 :497 补横幅，要么承认「49 路由」不是唯一事实源。
2. docs/audits/2026-09-26_glue_verify.md:152 称「本仓无任何代码给 _finished_callbacks 注册回调」「on_account_finished 是死路径」——**错**，反例 main_web.pyw:183（+195）。其后 p2（glue_verify_p2.md:34-41）已自纠，但 p1 正文未改。
3. docs/audits/2026-09-26_bind_and_exposure.md:206 的 H1「api_fastapi.py:1751 handle_get_config 回显 bind_address」在当前代码已不存在（GET /api/config 随退役删除），全文件仅剩 start_server 缺省在 **api_fastapi.py:2240**；行号已漂移（该报告 §9 已预告需复检）。
4. 轻微：doc_drift_cleanup.md:61 称历史默认 19999「已不再指代本 API」，但代码缺省仍是 19999（config_manager.py:18 `api_port`；main_web.pyw:325）。实际运行值取 config.json=19997，属缺省值与文档表述的张力，非硬冲突。
5. 陈旧快照（非缺陷）：minimal_ops_gate_recheck.md:92 称「140 条路由」系退役前快照，现为 49。

## 7. 边界与未做
- 未启动任何服务：bind 的 0.0.0.0 与 /status 类结论均为静态判定。
- 未跑 git；未打印任何凭据值（仅读 config.json 的 api_port 与 bind_address 是否存在，未读 token）。
