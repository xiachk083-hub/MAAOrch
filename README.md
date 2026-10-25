# MAAOrch（已退役）

2026-10-26 起退役。原 Web UI、Python 调度器、模拟器编排和实验模块已删除，git 历史仍保留。

## 还在维护的项目

| 项目 | 位置 | 作用 |
|---|---|---|
| `maa-cli-fleet` | https://github.com/xiachk083-hub/maa-cli-fleet | 日常 / 剿灭 / 肉鸽统一循环、设备自愈 |
| `maa-event-push` | https://github.com/MAAOrch/maa-event-push | 活动推关（二进制名 `masgo.exe`） |
| lane 工作区 | `D:\1-2\maa-event-push\lanes` | masgo 的账号配置、模板、作业和证据 |

## 本仓保留的内容

- `docs/MAA_*`：MAA 任务、日志、关卡、UI 参考手册；
- `tools/mas_*`：MAS 账号 / 巡检 / 健康检查小工具（按需使用）；
- `config/`：活动关卡、掉落、刷取和材料水位表；
- `LICENSE`。

完整的旧代码在 git 历史中，可用 `pre-slim-2026-10-26` 这个 tag 找回。

## 不再提供

- `main_web.pyw` / FastAPI Web UI；
- `services/` 下的 Python 调度器、模拟器服务、仓库/基建/统计模块；
- `manager/` 远程部署、`ui/` 旧前端、`plans/` / `strategy/` 实验；
- 运行时数据（`runtime/`、`data/samples`、`logs/`、`debug/`）。

License: AGPL-3.0，与原项目一致。
