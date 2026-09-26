# MAA-ALL 总管理器

目标机上的独立 MAA 团队管理器（与 `E:\MAA-Multi` 的 AUTO-MAS 体系互不干扰）。

## 目录
```
E:\MAA-ALL\
├─ manage_maa.py     # 管理器（Web GUI + CLI）
├─ favorites.json    # 收藏的模拟器索引（★标记）
├─ logs\manager.log  # 操作日志
├─ MAA\              # 原样本（干净母版；update 只更新这里）
└─ MAAS\<ID>-<备注名>\  # 子 MA（ID 自动生成，与一台模拟器绑定；.bind.json + backup\）
```

## 启动（已注册为计划任务 MAA-ALL-GUI，开机自启）
- 手动启动：`python E:\MAA-ALL\manage_maa.py serve`
- 局域网无鉴权，直接访问：
  - 目标机本机：`http://127.0.0.1:18999/`
  - 开发机（同一 Tailscale）：`http://100.79.173.69:18999/`

## 页面功能
1. **新建子 MA**：填备注名 → 下拉选模拟器 → 点“复制+填ADB+绑定”（自动：复制原样本 → 模拟器没开自动拉起 → 查实时 ADB 端口 → 写入连接 → 验证 ✓/✗ → 写绑定；重复绑定会拒绝）
2. **启动**：勾选一行或多行 → “启动勾选的(模拟器+MAA)”——模拟器没开会自动启动并等就绪，然后拉起对应 MAA.exe；端口漂移会自动重填
3. **关闭**：勾选 → 关闭（可连同模拟器）
4. **模拟器面板**：MuMu 全实例 + 启动/停止；行首 **★/☆ 点击收藏**，勾选「只看收藏」后列表和“新建子 MA”下拉只显示收藏的模拟器（收藏存 favorites.json，多台电脑一致）
5. **更新原样本**：下载 MaaRelease 最新版到 `E:\MAA-ALL\MAA`（含 3 个镜像回退）
6. **批量同步全部子 MA**：程序/资源换成原样本版本，子 MA 的 config/data/绑定**全部保留**（更新前自动备份 config 到副本内 `backup\`）；列表里每行的“更新”= 单副本，“换绑”= 换模拟器并重新填 ADB

## 命令行（能力相同）
```
python manage_maa.py list                 # 副本 + 模拟器一览
python manage_maa.py new 欧哈呦学妹 --emu 54
python manage_maa.py open 欧哈呦学妹      # 模拟器+MAA 一起启动
python manage_maa.py close 欧哈呦学妹 --kill-emu
python manage_maa.py bind a3f2d1c0 --emu 明日方舟-日-5
python manage_maa.py sync-copies          # 全部；或 sync-copies <ID...>
python manage_maa.py adb 欧哈呦学妹       # 打印 adb connect 串并测连通
python manage_maa.py update               # 更新原样本
```

## 约定与坑（重要）
- **ADB 端口从不按公式算**（模拟器重启会漂移）：一律 `MuMuManager info -v all` 实时取；启动前若发现端口变了会自动重填并提示。
- `--emu` 接受数字索引（如 54）或实例名（如 明日方舟-日-5，支持前缀）。
- 一台模拟器只能绑定一个子 MA（拒绝重复绑定，防 ADB 端口互抢）。
- 每个子 MA 启动时用独立 ADB server 端口（`5037+索引%100`），多个副本同时跑不互踩。
- 任务（作战/基建等）由用户在 MAA 界面里点——管理器只管“复制+连接+启停”。
- 服务以计划任务运行：若改过 manage_maa.py，需 任务计划程序里重启 MAA-ALL-GUI（或 schtasks /end + /run）。
