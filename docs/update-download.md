# MAA/maa-cli 下载更新与代�?

## 更新检�?

`UpdateCheckThread`（`services/update_service.py`）通过 GitHub Release API 查询最新版本：

```
GET https://api.github.com/repos/MaaAssistantArknights/MaaAssistantArknights/releases/latest
Headers: User-Agent: MAA-Launcher
```

返回数据解析�?

- `tag_name` �?版本号（�?`v6.11.1`�?
- `assets[]` �?按平台过�?`win-x64` / `win-arm64` �?`.zip` �?
- 排除 debug symbol �?component �?

## 版本切换

`LogService.switch_maa_version()` 支持�?Stable / Beta / Alpha 之间切换 MAA 版本�?

1. 确认对话�?
2. 调用 `UpdateCheckThread` 查询
3. 弹出 `UpdateDialog` 下载目标版本
4. 下载完成后覆�?`{MAA目录}/` 下的文件
5. 更新仓库条目�?`maa_version` �?`update_channel`
6. 重新注入 gui.json 配置

## 下载线程

`DownloadThread` 处理 MAA 压缩包的下载和解压：

```
下载进度 �?progress(downloaded, total)
解压 �?临时目录 �?覆盖目标目录 �?清理临时文件
```

### 文件覆盖策略

- **目录**：先删除目标目录 �?`shutil.copytree`
- **文件**：`shutil.copy2`，若受权限问题影响则先复制到 `.new` 后缀再替�?
- **取消**：设�?`cancel_flag`，下载循环中检测并退�?

## UpdateDialog

下载进度对话框：

- 显示版本号、文件大小（MB�?
- 进度条实时显示下�?解压状�?
- 完成后自动关�?

## 批量检查更�?

`MaintService.check_updates()`�?

1. 收集所�?`maa_type != "general"` 的仓库条�?
2. 查询最新版�?
3. 列出所有版本低于最新的程序
4. 批量或者逐个确认下载

## 自动下载 MAA

`MaintService.dl_maa()` 为选中账号下载 MAA�?

1. 调用 `UpdateCheckThread` 查询最新版
2. 弹出 `UpdateDialog` 下载�?`accounts/{账号ID}/MAA/`
3. 搜索解压后的 `MAA.exe`
4. 自动创建仓库条目（`guard_enabled=True`�?
5. 注入配置，更新账号仪表盘

## 手动绑定

`MaintService.pk_maa()` 通过文件选择对话框手动绑定已有的 MAA 程序�?

- 自动解析版本号（从父目录名提�?`vX.X.X`�?
- 创建仓库条目（`guard_enabled=False`�?
- 注入配置

## maa-cli 安装

`MaacliInstallThread` �?GitHub 下载并安�?maa-cli�?

```
GET https://api.github.com/repos/MaaAssistantArknights/maa-cli/releases/latest
�?过滤 windows x86_64 .zip �?下载 �?解压�?maa-cli/ 目录
```

`MaacliInstallDialog` 显示安装进度，完成后自动关闭�?

## 代理自动检�?

`utils.setup_proxy()` �?`main.pyw` 启动时调用，�?`urllib.request` 配置代理�?

### 检测顺�?

1. **环境变量**：检�?`HTTP_PROXY` / `HTTPS_PROXY` / `http_proxy` / `https_proxy`，任一存在则直接使�?
2. **端口探测**：依次尝�?TCP 连接以下本地端口�?
   - 7890, 7891（Clash�?
   - 1080, 10809（v2ray�?
   - 8080（通用 HTTP 代理�?
3. **超时**：每个端�?0.3 �?
4. **命中**：设�?`http://127.0.0.1:{port}` 代理

### 作用范围

配置后，所有通过 `urllib.request` 发出的请求（GitHub API、下载）均走代理�?

## MAAOrch 自更�?

### OrchUpdateCheckThread

`services/update_service.py` 中的 `OrchUpdateCheckThread` 查询 MAAOrch �?GitHub Release�?

```
GET https://api.github.com/repos/xiachk083-hub/MAAOrch/releases/latest
```

返回 `tag_name`（如 `v1.2.0`）和下载链接�?

### 流程

1. **菜单触发**：工�?�?检�?MAAOrch 更新
2. **版本比较**：`_version_tuple()` �?`MainWindow.VERSION` 比较
3. **下载**：从 GitHub 下载 ZIP
4. **解压**：解压到项目目录下的 `_update/` 临时文件�?
5. **生成替换脚本**：生�?`replace.bat`
6. **退�?+ 替换**：关�?MAAOrch �?批处理杀�?python �?复制文件 �?重启

### replace.bat 逻辑

```bat
taskkill /f /im python.exe
timeout /t 3
xcopy /E /Y "%~dp0_update\*" "%~dp0"
rmdir /S /Q "%~dp0_update"
start "" python "%~dp0main.pyw"
del "%~dp0replace.bat"
```

### 注意事项

- 不会覆盖 `accounts/`、`config.json`、`backups/`（xcopy �?`/exclude` 或手动排除）
- 需要在其他设备上先 `pip install PySide6`
