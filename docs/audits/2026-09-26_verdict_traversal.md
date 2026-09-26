# P0 路径穿越随前端退役的消失性验证

- 日期：2026-09-26 ｜ 范围：`network/api_fastapi.py`（2254 行，只读） ｜ 铁律：未执行 git、未启动服务、未打印任何凭据
- 结论一句话：**旧 P0（`GET /api/screenshots/file/{aid}/{run_dir}/{fname:path}`）确已随 91 条退役路由删除，代码中不存在；但新发现 1 处未规范化的路径拼接残留（`/api/depot/plan`），定级 P1，非原 P0。**

## 1) 旧 P0 路由确认不存在

`rg -n "fname:path|screenshots/file|\{fname\}" D:/1-2/MAAOrch --glob '!node_modules'` 结果：
- 代码文件命中 **0**。`network/` 目录下 `fname`、`screenshots/file`、`web_dir` 均 0 命中。
- 全部命中只在文档：`docs/audits/_deleted_routes.txt:31`（被删清单第 31 行）、`docs/audits/2026-09-26_api_trim_plan_p2.md:125,228`、`docs/audits/2026-09-26_api_trim_plan.md:150`。
- 代码里唯一相关行是注释：`network/api_fastapi.py:2196`（说明旧静态 catch-all 已于 task-14 删除）。
- 与既有文档一致（`_deleted_routes.txt` 已列为已删），**没有文档与实测冲突**。

## 2) 现有 49 条路由的文件读取/路径拼接面

路由计数：`rg -c "@app\.(get|post|put|delete|patch|websocket)" network/api_fastapi.py` = **49**（与题干一致，无 catch-all、无 `{x:path}` 活路由）。

| 面 | 命中（文件:行） | 入参可达用户？ | 规范化/白名单 | 判定 |
|---|---|---|---|---|
| `FileResponse` | `network/api_fastapi.py:9` 仅 import；:2197 仅注释 | — | — | 死 import，无文件下发路由 |
| `StaticFiles` | `:10` 仅 import，无 `app.mount` | — | — | 死 import，无静态目录暴露 |
| `HTMLResponse`/`StreamingResponse`/`mimetypes` | `:9`、`:3` 仅 import | — | — | 无文件读面 |
| `open(` | `:1913`（tmp_zip "wb"）、`:1937`（zip 解包） | 否（更新流内部临时路径） | `:1927 is_safe_zip_path(member, update_dir.parent)` 拒绝 zip-slip | 安全 |
| `read_text` 固定路径 | `:26,:31`（oplog）、`:760`（activity_tracker.CONFIG_PATH）、`:1820/:1871/:1880/:2017/:2024/:2034/:2140/:2149/:2160`（instances 目录，`:1814/:1861/:2011/:2134` isdigit 过滤）、`:406/:416`（固定 glob `services/maa/instances/*/.pid`） | 否 | 常量路径 + 目录名白名单 | 安全 |
| **`account_id` 拼路径** | **`:725 Path(...) / "models" / "accounts" / account_id / "plan.json"` → `:729 read_text`** | **是**：`GET /api/depot/plan?account_id=`（`:721` query 参数，`:723` 只校验非空） | **无**：无 `resolve()`、无 `..` 拒绝、不比对 `mw.accounts` 白名单 | **残留（P1）** |
| 路径参数 `{name}` | `:2082 /api/inspect/account/{name}` → `:2097-2099` 仅与内存 `mw.accounts` 名字相等比较；文件访问走固定 `instances` 目录 + `:2011` isdigit | 是 | 白名单比较，未入路径 | 安全 |

## 3) 结论

- **原 P0 已消除**：截图文件路由（及配套静态 catch-all）在代码中 0 残留，`models/config.json` 不能经该路径读取。退役有效。
- **残留 P1（1 处）**：`network/api_fastapi.py:725,729` 的 `/api/depot/plan`。`account_id` 直接进 `Path` 拼接，`exists()` 后 `read_text`；可读白名单外**任意名为 `plan.json` 的文件**（后缀固定，读不到 config.json，故非原 P0 级）。
  - 鉴权前提：`:218` 仅当 `api_token` 非空才校验；空 token = 开放（与 `docs/audits/2026-09-26_bind_and_exposure.md:10` 一致）。
  - 最小复现思路（**勿打生产**，仅本地代码路径推理）：pathlib 纯词法拼接，`account_id="../.."` → `<repo>/plan.json`；`account_id="../../.."` → `D:/1-2/plan.json`。本地起服务后可用不存在 vs 存在同名文件的返回码差异确认；根治：`p.resolve()` 后断言 `p.is_relative_to(<repo>/models/accounts)`。
- 另注无害残留：`:208` 的限流豁免仍判断 `path.endswith("/screenshot")`，但该文件已无 `/screenshot` 路由，属退役后的死分支。

## 4) web_dir 残留

`rg -n "web_dir" network/api_fastapi.py` = **0**；全仓仅 `docs/audits/_deleted_refs_scan.py.txt:101`（扫描脚本的 pattern 字符串）。**该文件内 web_dir 已无残留。**

## 复现命令（全部只读）

```powershell
rg -n "fname:path|screenshots/file" D:/1-2/MAAOrch
rg -c "@app\.(get|post|put|delete|patch|websocket)" D:/1-2/MAAOrch/network/api_fastapi.py
rg -n "FileResponse|StaticFiles|open\(|read_text|send_file|\{[a-z_]+:path\}" D:/1-2/MAAOrch/network/api_fastapi.py
rg -n "account_id|Path\(__file__\)\.parent\.parent" D:/1-2/MAAOrch/network/api_fastapi.py
rg -n "web_dir" D:/1-2/MAAOrch/network/api_fastapi.py
```
