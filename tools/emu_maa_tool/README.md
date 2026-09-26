# 模拟器 + MAA 小工具（emu_maa_tool）

独立小工具：本机/远程同构（服务端跑哪台管哪台），桌面端 pywebview 窗口，
账号为中心（账号→模拟器→MAA配置→MAA连接），配置集中存储，副本池复用。

## 快速开始（本机）

```bat
start.bat     :: 双击：自动拉起服务端(127.0.0.1:18889) + 打开桌面窗口
```

或分开：
```bat
start_server.bat            :: 只起服务端
python client/app.py        :: 只开桌面窗口（连 127.0.0.1:18889）
python client/app.py http://100.79.173.69:18889   :: 连远程
```

## 功能

| 功能 | 说明 |
|---|---|
| 账号管理 | 新建/绑定模拟器/备注/删除/移动分组（data/accounts.json） |
| 账号分组 | 新建/重命名/删除组、移组、按组折叠、按组批量圈选 |
| 模拟器 | 单个/批量开启/关闭（优雅关闭链，绝不碰 ADB） |
| MAA 打开 | 实时检测端口 → 空闲副本写配置 → 拉起 → 默认小窗 |
| MAA 关闭 | 优雅关 → 配置存回存档 → 日志剪切归档 → 副本释放 |
| MAA 小窗 | 卡片按钮 小窗↔还原 toggle |
| 配置存档 | data/configs/<账号>.json，跟账号走，30s 同步 + 关闭最终同步 |
| 日志 | 操作日志 op.log + 每次运行归档 data/logs/<账号>/<时间戳>/ |
| 状态推送 | SSE 实时更新，不轮询 |

## 架构

```
桌面客户端(pywebview) ── fetch/SSE ──▶ API 服务端(FastAPI :18889) ──▶ MuMuManager / MAA
  只在本机                          跑哪台管哪台                    (本机 or 目标机)
```

- 工具自带完整 API，不依赖 MAAOrch 服务运行。
- 只复用 MAAOrch 4 处函数：task_constants(检测) / instance_pool._create_instance(副本)
  / emu_service.graceful_shutdown(关闭链) / config_injector(字段结构)。

## 关键机制

- **ADB 端口永不保存**：每次打开/刷新实时 detect（公式/缓存禁用）。
- **配置跟账号走**：data/configs/<账号>.json 存完整 Configurations+Gui（连接不入库）。
- **副本池复用**：maa_instances/{1..N}（默认 4），停车位模式，关了释放回池，永不删除。
- **MAA 启动会清空 asst.log** → 关闭时剪切到账号日志目录，副本保持干净。

## 部署到目标机（等你拍板后执行）

```bat
python tools/emu_maa_tool/deploy_server.py                :: 部署 + 停用 MAAOrch 队列/回收
python tools/emu_maa_tool/deploy_server.py --no-takeover  :: 只部署不停用
```

- 落位 `E:\emu_maa_tool\`，注册启动文件夹自启。
- 客户端连接：`python client/app.py http://100.79.173.69:18889`

## 配置（data/server.json）

| 字段 | 默认 | 说明 |
|---|---|---|
| port | 18889 | 服务端口 |
| token | "" | 空=本机不鉴权；远程部署必须设置 |
| maa_source_path | 自动探测 | MAA 模板目录 |
| project_root | 自动探测 | MAAOrch 项目根（复用函数） |
| pool_size | 4 | 副本池并发上限 |
| config_sync_interval | 30 | 运行期配置同步秒数 |

## 日志

- 操作日志：data/logs/op.log（追加式）
- MAA 日志归档：data/logs/<账号>/<时间戳>/asst.log + gui.log（每次运行一个文件夹）
