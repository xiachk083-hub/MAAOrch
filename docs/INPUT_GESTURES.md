# 明日方舟里"拖/滑"这类操作的可靠手法（实测）

> 2026-09-24 Lead 实测记录。**凡是"要拖、要滑、要按住"的操作，一律优先 `input motionevent`，不要用 `input swipe`。**

## 结论

| 手法 | 结果 |
|---|---|
| `adb shell input swipe x1 y1 x2 y2 <ms>` | **经常不生效**（游戏不认作拖拽/滚动） |
| `adb shell input motionevent DOWN/MOVE/UP`（分步、每步 200~300ms） | **可靠**（实测在战斗里成功触发"拖拽中"状态；滚动同理） |

## 证据（两次失败 + 一次成功）

1. **失败**：滑「完成进度」页 3 次（`input swipe 640 660 640 150 900`）→ planner-1 用**逐像素比对**证明三张截图差异只有 **0.13%~0.27%**，每张卡位置与匹配分数完全一致 ⇒ **完全没滚动**（我原以为是"页面滚不动"）。
2. **失败**：在 AD-TR-1 战斗里用 `input swipe` 拖干员 3 次 → 干员卡被"拖起来"但**放不下去**（游戏不认落位）。
3. **成功**：改用
   ```powershell
   adb shell input motionevent DOWN 1164 662
   adb shell input motionevent MOVE 1000 600
   adb shell input motionevent MOVE  850 540
   adb shell input motionevent MOVE  700 480
   adb shell input motionevent UP    700 480
   ```
   → 游戏立刻进入"拖拽中/可放置"状态（说明它认这套触摸序列）。

## 用法建议

- 滚动列表/地图：`DOWN (x, y_start)` → 3~5 次 `MOVE`（等距插值到 `y_end`）→ `UP`；**起手点要避开底部导航栏**（别在 y≥650 起手，会被导航吃掉）。
- 拖干员/滑条：同上，`UP` 落在目标格上；若游戏进入"选方向"状态，再对方向箭头做一次 `DOWN→MOVE→UP`。
- 每步之间 `Start-Sleep -Milliseconds 200~300`；`DOWN` 后**不要立刻 MOVE**（给游戏识别"长按"的时间）。
