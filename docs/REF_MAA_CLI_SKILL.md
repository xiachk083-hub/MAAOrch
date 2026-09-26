# maa-cli 执行底座（2026-09-20 首次真机跑通 CE-6）

> 目标：给"自主推关"补**执行器那一层** —— 不用 GUI、可脚本化、拿得到退出码与日志。
> 本次真机结果：**CE-6 通关一次**（2m31s，掉落 龙门币 ×10000，理智 55→19 = 正好 −36 ✓）。

## 一、环境（工作区隔离，**不碰现有 MAA GUI**）

```bash
# 1) 装 maa-cli（winget 装出来叫 maa-cli，不是 maa）
winget install maa-cli

# 2) 环境变量指到工作区内（见 runtime/maa_cli/env.sh）
export MAA_DATA_DIR=...\runtime\maa_cli\data      # lib/ + resource/ 用 junction 指向已装 MAA ⇒ 免下载 MaaCore
export MAA_CONFIG_DIR=...\runtime\maa_cli\config
export MAA_STATE_DIR=...\runtime\maa_cli\state    # MaaCore 的日志/调试图在这里
export MAA_CACHE_DIR=...\runtime\maa_cli\cache
# 3) junction（目标必须是 MAA 根目录：MaaCore.dll 与依赖同目录）
New-Item -ItemType Junction -Path <data>\lib      -Target D:\Xiach\M-A\MAA\<MAA 根>
New-Item -ItemType Junction -Path <data>\resource -Target D:\Xiach\M-A\MAA\<MAA 根>\resource
```

实测：`maa-cli v0.7.5` + 复用本机 `MaaCore v6.18.0-beta.2` ✓（`maa-cli version` 直接读到）。

## 二、跑任务的命令形状

```bash
maa-cli startup Official  -p <profile> --batch          # client 是**位置参数** ✓（不是 --client-type ✗）
maa-cli fight CE-6        -p <profile> --batch --times 1 --client-type=Official --log-file=<绝对路径>
```

`profiles/<name>.toml`（关键项）：

```toml
[connection]
adb_path = 'D:\Xiach\MuMuPlayer\nx_main\adb.exe'   # ⚠️ TOML 里 Windows 路径用**单引号**（基本字符串会把 \X 当转义 ✗）
address  = "127.0.0.1:16448"
[instance_options]
touch_mode = "MaaTouch"
kill_adb_on_exit = false
```

## 三、五个真坑（2026-09-20 全部踩过并定案）

1. **客户端类型错了 ⇒ 零点击直接报错** ✗✓：实例 #2 是**官服（中文界面）**，我按日服配（`global_resource="YoStarJP"` + `--client-type=YoStarJP`）
   ⇒ `StageBegin` 重试 5 次后 `SubTaskError`，**全程 0 次点击**（日志里没有任何 `minitouch click`）✓ ——
   **"识别全不中 + 不点任何东西"就是客户端类型/资源不对的典型表征** ✓。
2. **`emulator-5554` 与 `127.0.0.1:16384` 是同一台实例** ✗（两者 `getprop`/`uptime` 完全一致）⇒ 不要按"两个 adb 设备"当成两台机器 ✓（老坑：端口/实例名易混）。
3. **`startup` 的 client 是位置参数** ✓（`maa-cli startup YoStarJP`）；写成 `--client-type=...` 会直接 usage 报错 ✓；而 `fight` 用 `--client-type=` ✓ —— 两者不同形 ✓。
4. **`--log-file` 必须给绝对路径** ✓：不带路径时默认名含 `HH:MM:SS` 的冒号 ⇒ Windows 非法文件名 ⇒ 启动即退 ✓（技能文档里也写了 ✓）。
5. **`maa-cli` 不校验参数名** ✓：写错的参数会**静默无效** ⇒ 参数名一律对 MAA 集成文档/技能自带 `references/` 核 ✓。

## 四、真机坐标（本机，2026-09-20 实测）

| 实例 | 名字/端口 | 客户端 | 状态 |
|---|---|---|---|
| #0 | `MuMuPlayer-12.0-0` → adb `127.0.0.1:16384`（= `emulator-5554` 同一台 ✓） | **日服**（画面日文） | **MAA GUI 正在刷肉鸽**（别抢 ✓） |
| #2 | `MuMuPlayer-12.0-2` → adb `127.0.0.1:16448` | **官服**（画面中文） | 本次执行底座的目标 ✓ 分辨率 1280×720 ✓ |

> 起实例：`D:\Xiach\MuMuPlayer\nx_main\MuMuManager.exe control -v 2 launch` ✓（返回 `errcode: 0` ✓）；
> 查端口：`MuMuManager.exe info -v 2` ✓（**manager 在 `nx_main\` 下**，不在 `MuMuPlayer\` 根 ✗ —— 我因此白查了两次 ✓）。

## 五、产物

- 掉帧/识别原始帧：`runtime/maa_cli/state/debug/interface/*.png` ✓（MaaCore 自己存的 ✓ ⇒ 正好可当**真实关卡素材** ✓）
- 运行日志：`runtime/maa_cli/state/debug/asst.log` ✓ + `--log-file` 那份 ✓
- 本次截图：`data/_emu2_first.png`（CE-6 详情页，理智 55）✓、`data/_emu2_after_ce6.png`（理智 19 = −36 ✓）
