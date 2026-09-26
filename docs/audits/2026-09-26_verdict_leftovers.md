# 遗留缺陷清单核实（第 6 路独立验证）

日期：2026-09-26 ｜ **说明**：本路原分派给一个独立子代理，该子代理中途失败、零产出（与那批坏队友同样的失败形态），
故由 Lead 直接完成。**结论均为本次实测**，非引用旧报告。

## 1. tools/rank_service.mjs 悬空引用 —— **确认存在**

- `tools/rank_service.mjs:130` 注释：`// 预留：tools/train_ranker.py 导出的系数 JSON`
- `tools/rank_service.mjs:132`（原文）：`if (!coef) throw new Error('ranker_coef.json 不存在（先跑 tools/train_ranker.py）')`
- `tools/train_ranker.py` 实测 **MISSING**（不存在）
- **处置：已修文案**。`tools/rank_service.mjs:132` 现在明确写出『训练脚本 tools/train_ranker.py 已不在本仓库
  （2026-09-26 复核确认），需另行找回或改用已有系数文件』—— 避免指引用户去跑一个不存在的脚本。
  （仅改错误文案，零行为改动；`kind === 'gbm'` 分支本身仍是『系数文件不存在就报错』。）

## 2. 非原子写 —— **确认存在，已修**

- `models/stats.py:34-39` `_save()` 原文：`self._path.write_text(json.dumps(...), encoding="utf-8")`
- `models/account_state.py:44-49` `_save()` 同形。
- 影响：写到一半崩溃/断电 → 状态文件被截断。两个读取方都做了容错（stats 返回默认、account_state 返回 `{}`），
  所以后果是**静默状态丢失**而不是崩溃。
- **处置：已修**为同目录临时文件 + `Path.replace()`（同文件系统上原子）。
  两处均加注释说明来由。

## 3. infrastructure/utils.py 的 UTF-8 BOM —— **确认存在，已修**

- 实测前 3 字节：`EF BB BF`（UTF-8 BOM）。
- 影响：Python 3 能容忍，但按原始字节处理的工具（拼接、比较、某些 loader）会踩坑。
- **处置：已移除**。移除后前 3 字节 `69 6D 70`（即 `imp`…），`compileall` exit 0。

## 4. network/mcp_server.py 的 docstring 日期 —— **确认是错的，已修**

- 原文 `network/mcp_server.py:14`：`例外与写闸门（2026-08-16 前端退役，人工入口整体迁到 MCP）：`
- **前端退役实际发生于 2026-09-26**（本次会话 task-14），08-16 是错的。
- **处置：已改为 2026-09-26**。

## 5. docs/audits 下 6 个 `_*.py.txt` scratch 文件 —— **不是垃圾，保留**

| 文件 | 被仓库引用次数 |
|---|---|
| `_deleted_routes.txt` | 5 |
| `_deleted_refs_scan.py.txt` | 2 |
| `_farm_probe.py.txt` | 1 |
| `_frontend_500_probe.py.txt` | 1 |
| `_frontend_ast_verify.py.txt` | 1 |
| `_frontend_mutant_test.py.txt` | 1 |

- 它们**已被纳入版本控制**（在 `c71eaf1` 随 docs/audits 一并提交），且被审计文档按名引用。
- **处置：保留**。它们是可复现证据件，不是残留。
  （唯一需要说明的是它们由某次失败任务的探测脚本产生 —— 但内容已被引用，删掉会造成引用悬空。）

## 6. 编译与测试 —— **通过**

- `python -m compileall -q .` → exit 0
- `python -m pytest tests -q` → 全绿（见提交时的实测数字）

## 小结

| # | 项 | 判定 | 处置 |
|---|---|---|---|
| 1 | rank_service 悬空引用 | 存在 | 已修文案 |
| 2 | stats/account_state 非原子写 | 存在 | 已修（tmp + replace） |
| 3 | utils.py BOM | 存在 | 已移除 |
| 4 | mcp_server.py 日期 | 错误 | 已修 |
| 5 | 6 个 scratch 文件 | 非缺陷 | 保留（被引用） |
| 6 | 编译/测试 | — | 通过 |