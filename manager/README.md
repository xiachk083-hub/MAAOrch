# MAAOrch-Manager

独立常驻管理服务，用于远程管理 MAAOrch 项目（下载/启动/关闭/删除），不依赖 MAAOrch 自身运行。

## 部署

```
E:\MAAOrch-Manager\        ← 管理器目录（常驻）
├─ manager.py              自包含 HTTP 服务（仅标准库）
├─ install.bat             首次安装
├─ manager.bat             启动入口
└─ config.json             自动生成 { project_dir, port, token }
```

## 首次安装（一次性）

1. 从 https://github.com/xiachk083-hub/MAAOrch/archive/refs/heads/main.zip 下载
2. 解压出 `manager/` 目录，放到 `E:\MAAOrch-Manager\`
3. 双击 `install.bat`
   - 自动注册开机自启
   - 自动生成 token（存于 `config.json`）
   - 启动管理器（后台运行，端口 19998）

## API（端口 19998，需 `x-manager-token` header）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 项目存在/运行/PID + 管理器版本 + 进度 |
| GET | `/api/progress` | 下载/解压/替换进度 |
| GET | `/api/log` | 管理器日志尾部 |
| POST | `/api/download` | 拉 main.zip → 替换项目（保留 config.json + services/maa/）→ 自动重启 |
| POST | `/api/start` | 启动 MAAOrch |
| POST | `/api/stop` | 优雅关闭（WM_CLOSE + 兜底 taskkill） |
| POST | `/api/delete` | 删除项目（`{"confirm": true}`；先备份 config.json 到 backups/） |
| POST | `/api/update_manager` | 从 GitHub 拉最新 manager.py 自替换重启 |

## 示例

```bash
# 查看状态
curl -H "x-manager-token: <token>" http://127.0.0.1:19998/api/status

# 下载并更新项目
curl -X POST -H "x-manager-token: <token>" http://127.0.0.1:19998/api/download

# 删除项目（危险，先备份）
curl -X POST -H "x-manager-token: <token>" -d '{"confirm": true}' http://127.0.0.1:19998/api/delete
```

## 安全说明

- 监听 `0.0.0.0`，任何请求需 `x-manager-token` header（token 在 config.json）
- `delete` 必须显式 `confirm: true`，且先备份 config.json 到 `backups\`
- 下载替换自动保留 `models\config.json`（账号配置）和 `services\maa\`（MAA 二进制）
