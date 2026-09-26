# 求助：如何从 C API 给 MaaCore 挂上 Win32 控制单元（FramePool 取帧）？

> 用途：贴到 MAA 社区 / issue / 群里问。里面全是实测证据 + 最小复现，照着能重现。
> 目标读者：懂 MaaCore / MaaFramework 控制单元的人。

## 我们想干什么

我们在做一个"AI 打关"的旁路工具（不改 MAA 本身）：需要**实时看战斗画面**并叠标注。
现在唯一卡住的是**取帧速度**：

| 取帧方式 | 帧率 | 分辨率 | 备注 |
|---|---|---|---|
| MAA GUI（跑任务时） | **13 ms / 张 ≈ 20 fps** ✓ | — | 我们想要的 |
| 我们自己 `adb exec-out screencap` | 330 ms / 张 | 1920×1080 | 现在用的，能满足"看清"，但做不了流畅标注 |
| 我们另起 MaaCore 实例（adb 通道） | 280~302 ms | 1280×720 | 更慢，还更糊 |

**我们不需要输入，只要帧**（点按、滑动都由我们自己的 adb 通道做）。

## 已查到的证据

1. **MAA 自己确实在用 20fps 的取帧**（它自己的日志 `debug/asst.log`）：
   ```
   AsyncCallInfo {"what":"Screencap","cost":14,...}      ← 每张 13~21 ms
   ConnectionInfo {"details":{"avg":13,"max":20,"min":11},"what":"ScreencapCost"}
   时间戳：17:40:17.330 → .383 → .436 → .480 → .533 → .588 → .632 → …   ← 每 ~50ms 一张
   ```
2. **它用的方式不是 adb 截图**：`config/gui.new.json` 里
   ```json
   "ScreencapMethod": "FramePool"
   ```
3. **源码依据**（`dev` 分支）：
   - `src/MaaCore/Common/AsstTypes.h:118`
     ```cpp
     constexpr Win32ScreencapMethod FramePool = 1ULL << 1;   // = 2
     ```
     （同处：GDI=1, DXGI_DesktopDup=4, DXGI_DesktopDup_Window=8, PrintWindow=16, ScreenDC=32;
     input 侧 `Win32Input::{Seize=1, SendMessage=2, PostMessage=4, ...}`）
   - `src/MaaCore/Controller/Controller.cpp:344`
     ```cpp
     auto win32_controller = std::make_shared<Win32Controller>(m_callback, m_inst);
     if (!win32_controller->attach(hwnd, screencap_method, mouse_method, keyboard_method)) { ... }
     ```
   - 所以 C API 里的入口应该是 `AsstAttachWindow` / `AsstAsyncAttachWindow`
     （这两个符号在 `MaaCore.dll` 的导出表里确实存在）

## 我们试了什么（全部失败）

用官方 Python 绑定（`asst/asst.py`，`D:\...\MAA-v6.12.2-win-x64\Python\asst`）起实例、
`Asst.load(...)` 之后直接 ctypes 调 `MaaCore.dll` 的 `AsstAttachWindow`：

```python
lib.AsstAttachWindow.argtypes = (ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64)
lib.AsstAttachWindow.restype = ctypes.c_bool
ok = lib.AsstAttachWindow(handle, ctypes.c_void_p(hwnd),
                          ctypes.c_uint64(2),   # FramePool
                          ctypes.c_uint64(0),   # 想要"不接管输入"
                          ctypes.c_uint64(0))
# → ok 恒为 False
```

试过的组合（MuMu 12，三个窗口都试了）：

| 窗口（客户区） | screencap | input | 结果 |
|---|---|---|---|
| `MuMuNxDevice` 标题 `1`（1346×796） | FramePool(2) / GDI(1) / PrintWindow(16) | None(0) | attach=False |
| 同上 | 同上 | PostMessage(4) | attach=False |
| `MuMuNxDevice` 标题 `MuMuNxDevice`（1346×756） | 同上 | 同上 | attach=False |
| `MuMuNxMain` 标题 `MuMu模拟器` | 同上 | 同上 | attach=False |

MaaCore 自己打的错误（节选）：

```
[ERR][Win32ControlUnitMgr.cpp][L184] make_input] Unknown input method:  0
[ERR][Win32ControlUnitMgr.cpp][L466] touch_move] mouse_ is null
[ERR][DesktopDupWindowScreencap.cpp][L57] Client rect out of bounds
      [crop_x=-31996] [crop_y=-32000] [client_width=191] [client_height=30] ...
[ERR][Win32ControlUnitMgr.cpp][L222] speed_test] cannot find any method to screencap!
[ERR][Win32ControlUnitMgr.cpp][L132] init_screencap] failed to select screencap method
```

另外我们还试过走 adb 控制器的路子（能连上、能取帧，就是慢）：

```python
asst.set_instance_option(InstanceOptionType.kill_on_adb_exit, "0")   # 必须先设，别让它退出去杀 adb
asst.connect(adb_path, "127.0.0.1:16384", json.dumps({"screencap": "FramePool"}))
# 以及 Asst.set_connection_extras("MuMuExtras"/"AVMumuExtras",
#        {"path": r"D:\Xiach\MuMuPlayer", "index": 0, "display_id": 0})
# → 都还是 274~302 ms / 张，且帧是 1280×720
```

（`MuMu12ExtrasEnabled` / `MuMu12EmulatorPath` / `MuMu12Index` / `MuMu12Display` /
`MuMuBridgeConnection` / `MuMuEmulator12KeepAliveOn` 这些键是从 `MAA.dll` 的字符串里找到的，
但 extras 的确切 JSON 结构、以及它跟 FramePool 的关系我们没查到。）

顺带说明：Python 绑定里**没有** `attach_window` 的封装，也没有任何"导出帧/推帧"的接口
（`MaaCore.dll` 导出共 30 个：`AsstAsyncScreencap` / `AsstGetImage` / `AsstGetImageBgr` … 没有 dump/callback 图像的）。

## 具体想问的

1. **从 C API 给 MaaCore 挂 Win32 控制单元的正确姿势是什么？** `AsstAttachWindow` 的
   完整参数语义（尤其 `hwnd` 应该是哪个窗口：设备窗？主窗？）、以及它返回 False 时的判断依据。
2. **FramePool 依赖什么？** 它读的是 MuMu 的共享帧池 —— 这个句柄是控制单元自己找的，
   还是要通过 `AsstSetConnectionExtras("MuMuExtras", {...})` 传进去？如果是后者，**JSON 的确切字段**是什么？
3. **`input` 能不能传"不接管输入"的值？** 我们只要帧，不想抢鼠标键盘。上面日志里
   `Unknown input method: 0` 说明 0 不合法 —— 想确认哪个值最安全（`PostMessage`？还是别的）。
4. **有没有官方推荐的"只取帧"做法？** 比如单独开一个不带任务的实例、只调
   `AsstAsyncScreencap` + `AsstGetImageBgr` 应该能到多少 fps？我们实测 3 fps（720p），
   比我们自己的 adb 截图（330ms / 1080p）还差，感觉姿势不对。
5. 如果 FramePool 这条路在外部不可用，**有没有别的≥10fps 的取帧建议**（比如 minicap 流、
   MuMu 自己的接口、或者让 MAA 以某种"只截图"的模式跑）？

## 环境

- Windows 10.0.26200（x64）
- MAA：`v6.18.0-beta.2#0917`（GUI），安装目录 `D:\Xiach\M-A\MAA\MAA-v6.12.2-win-x64`
  （里面 `MaaCore.dll` / `MaaWin32ControlUnit.dll` / `MaaAdbControlUnit.dll` 都在）
- 模拟器：MuMu Player 12（`MuMuNxDevice.exe` / `MuMuNxMain.exe`），设备 `emulator-5554`，`wm size` = 1080×1920
- 语言：Python 3.12 + 官方 `asst` 绑定（该目录下 `Python/asst`）
- 我们自己的取帧（对照）：`adb -s emulator-5554 exec-out screencap`（**不带 `-p`**，
  原始流带 16 字节头，去掉后 0.33s/张、像素级准确）

## 一句总结

MAA GUI 能 13ms 一张（FramePool），我们按源码里的 `AsstAttachWindow + Win32Screencap::FramePool`
怎么都挂不上（attach 恒 False）。**求正确的调用姿势 / 依赖条件**；我们只要帧、不要输入，
也愿意接受"另起一个专门取帧的实例"这种方案。

---

# 补充：按外援意见逐条实验后的结果（2026-09-19 晚）

> 上面是原始报告。下面是拿到一份专家意见后**逐条实测**的结果 —— 结论更新在这里，**优先看这一节**。

## 已采纳并排除的几条

**① `input=0` 不是充分原因**（这条是专家指出的最硬线索，实测**不成立**）✗
`MaaWin32InputMethod_None = 0` 确实会让 `make_input()` 报 `Unknown input method: 0`，
但换成合法值后 **attach 依然 False**。试过的输入组合（都配 FramePool=2）：

| input（mouse / keyboard） | 结果 |
|---|---|
| 0 / 0 | attach=False（日志 `Unknown input method: 0`）|
| 4 / 4（PostMessage）| attach=False |
| 2 / 2（SendMessage）| attach=False |
| **32 / 2（SendMessageWithCursorPos / SendMessage）** | attach=False |

最后那一行**是 MAA 自己配置里用的值** —— 从它自己的 `config/gui.new.json` 里读出来的：

```json
"ScreencapMethod": "FramePool",
"MouseMethod": "SendMessageWithCursorPos",
"KeyboardMethod": "SendMessage"
```

（注意：**不是** `Seize`。专家意见里写的 `Seize/Seize` 与本机配置不符。）

## ② 新查明的一条：日志里的 `-32000` 是**最小化窗口**，不是"找不到帧池"

我们把 DPI 感知也补上了（`SetProcessDpiAwareness(2)`，因为 MAA 的 GUI 是 WPF、按显示器 DPI 感知，
而我们的 Python 进程默认不是）。窗口几何在 DPI 感知前后差很多：

```
DPI 感知前：hwnd=1773102 GetWindowRect=(246,207,1592,964)   客户区=1346×756
DPI 感知后：hwnd=1773102 GetWindowRect=(308,259,1991,1205)  客户区=1683×946
主窗(最小化)：GetWindowRect=(-32000,-32000,-31801,-31966)   客户区=191×30   ← 正是错误日志里那两个数
```

⇒ 之前那句 `Client rect out of bounds [crop_x=-31996] [client_width=191] [client_height=30]`
来自**最小化的主窗口**（Windows 对最小化窗口返回 −32000）—— **和 FramePool 无关**，是红鲱鱼。
（同时也说明：**要挂的是真正渲染游戏的那个窗口**，别用主窗口。）

## ③ 现在的三个窗口（MuMu 12）

| hwnd | 类/进程 | 标题 | 客户区（DPI 感知后） |
|---|---|---|---|
| 721842 | MuMuNxDevice.exe | `1` | 1683×996 |
| 1773102 | MuMuNxDevice.exe | `MuMuNxDevice` | 1683×946 |
| 2230458 | MuMuNxMain.exe | `MuMu模拟器` | 191×30（**最小化**）|

上面所有 (窗口 × 截图法 × 输入法) 组合都试过，**`AsstAttachWindow` 恒返回 False** ✗。

## ④ 一处要澄清的版本问题

专家提到"v6.18 GUI + v6.12.2 MaaCore 混用"。实测澄清：
目录名 `MAA-v6.12.2-win-x64` 是**历史遗留的文件夹名**，里面跑的 GUI 报 `v6.18.0-beta.2#0917`，
而我们的 Python 绑定 `Asst.load(path=该目录)` 加载的正是**同一个目录里的 `MaaCore.dll`**
（GUI 也用这一份）⇒ **DLL 与 GUI 同源**。不确定的只剩 Python `asst.py` 包装器自身的版本。

## ⑤ 更新后的提问（比原来的更具体）

**A.** `AsstAttachWindow` 的 `hwnd` 到底该给哪个？我们试了 `MuMuNxDevice` 的两个窗口都与主窗，
全 False；是否必须给**带 D3D swapchain 的那个子窗口**（需要 `EnumChildWindows` 一层层找）？

**B.** `AsstAttachWindow`（同步版）与 `AsstAsyncAttachWindow`（异步版）语义差别是什么？
同步版返回 False 是否可能是"请求已发出但还没完成"（即应当用异步版 + 回调/等待）？
异步版的完整签名是什么（我们试了 5 参和 6 参）？

**C.** `AsstAttachWindow` 之前是否需要先 `AsstConnect`（或相反：必须先不连接）？
我们现在是**只 `Asst.load` + `AsstCreate`，不 connect** 就直接 attach。

**D.** 对外部调用者，是否应当**绕开 MaaCore 的兼容层**，直接用 MaaFramework 的
`MaaWin32ControllerCreate(hwnd, FramePool, mouse, keyboard)` →
`MaaControllerPostConnection` → `MaaControllerPostScreencap` → `MaaControllerCachedImage`？
（我们本机也有 MaaFramework 的发行版与头文件，可以试这条。）

**E.** 如果只想**取帧、不接管输入**，官方推荐姿势是什么？Win32 控制单元是否允许"只初始化 screencap"？

## ⑥ 我们下一步自己会试的（不占外援时间）

1. 用 `MaaFramework` 的原生 C API（上面 D 那条）做最小实验；
2. `EnumChildWindows` 找到真正的渲染子窗口再挂；
3. 试 `AsstAsyncAttachWindow` 的正确签名 + 等回调。

---

# ✅ 自己解决了（2026-09-19，按外援方向试出来）

**结论：用 `maa`（MaaFramework 官方 Python 绑定，PyPI 包名 `maafw`，import 名 `maa`）的
`Win32Controller` + `FramePool`，实测 18 ms/帧 ≈ 55 fps，抓到的就是真游戏画面。**

```python
import maa.controller as C, maa.define as D
c = C.Win32Controller(
        hWnd=721842,                                          # MuMu 的设备窗口（标题 "1"）
        screencap_method=D.MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=D.MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=D.MaaWin32InputMethodEnum.PostMessage)
c.post_connection().wait()
img = c.post_screencap().wait().get()          # numpy，18 ms/帧
```

关键点（都是踩出来的）：
- **不需要 adb** ✓、**不发任何输入**（PostMessage ✓ 且我们从不调输入接口 ✓）——
  和 MAA 自己的 adb 通道完全隔离 ✓；
- 参数名是 **`hWnd`**（不是 `hwnd` ✗）；默认截图法就是 `Background = FramePool|PrintWindow` ✓；
- **窗口选 `MuMuNxDevice.exe` 标题为实例名（"1"）的那个** ✓；主窗 `MuMu模拟器` **最小化时会失败** ✓
  （这就是我们之前所有失败的根源之一 ✓）；
- 抓出来的是**窗口**内容 ⇒ 带标题栏/标签页（要裁掉 ✓），长宽比是窗口的（不是设备的）⇒ 用的时候要映射到 1920×1080 ✓；
- 之前用 `MaaCore` 的 `AsstAttachWindow` 怎么都挂不上（3 窗口 × 6 组合全 False ✗）——
  **改用 MaaFramework 的原生控制器一次成功** ✓（外援的判断正确 ✓）。

## 收尾：接进标注界面（2026-09-19）

- `tools/maafw_capture.py`：`MaafwCapture.open()` **用"能否抓出合法帧"选窗口**（尺寸/宽高比漂亮的
  那个 `MuMuNxDevice` 实测抓出来是块状垃圾 ✗）；`grab()` 裁掉顶部标签栏（`CHROME_1080 = 50`，
  它是**窗口内容**、Win32 非客户区量不到 ✗）再映射成 **1920×1080**。
- `tools/annotate_view.py`：帧源**优先 FramePool（19ms/帧 ≈52fps）**，失败回落 adb raw（330ms）。
  界面右上会显示当前帧源（`帧源 FramePool`）✓。
- **验证方式（可靠的，值得复用）**：同一秒内**两条路各抓一帧**，跑同一套读数比对 ——
  实测三轮四个字段（敌人数/生命/费用/可部署）**完全一致** ✓✓。⚠️ 别拿"记忆里的常量"当答案：
  我第一次就是这么验的，结果值早变了（费用 84→99、可部署 6→7）——**差点把对的当成错的**。
