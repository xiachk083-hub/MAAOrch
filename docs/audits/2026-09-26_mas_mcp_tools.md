# AUTO-MAS 自带 MCP 工具面（2026-09-26 实测存档）

来源：官方文档 https://doc.auto-mas.top/docs/advanced-features/mcp.html ，文档给的地址是 http://localhost:36163/mcp

实测：对 http://100.79.173.69:36163/mcp 做 MCP initialize，返回 serverInfo = AUTO-MAS MCP；本文件由 tools/list 直接导出。

**工具总数：118**

> 注意：该端点无鉴权。谁能访问该地址，谁就能调用全部 118 个工具（含关闭后端、脚本配置增删改、导入导出与配置分享）。

| # | tool | 说明（截断 110 字符） |
|---|---|---|
| 1 | `get_health_api_core_health_get` | 获取后端就绪状态  返回核心 API 与后台初始化状态。  ### Responses:  **200**: Successful Response (Success Response) Content-Type: ap |
| 2 | `close_api_core_close_post` | 关闭后端程序  关闭后端程序  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json |
| 3 | `get_git_version_api_info_version_post` | 获取后端git版本信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  * |
| 4 | `get_stage_combox_api_info_combox_stage_post` | 获取关卡号下拉框信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  ** |
| 5 | `get_script_combox_api_info_combox_script_post` | 获取脚本下拉框信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **E |
| 6 | `get_task_combox_api_info_combox_task_post` | 获取可选任务下拉框信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  * |
| 7 | `get_plan_combox_api_info_combox_plan_post` | 获取可选计划下拉框信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  * |
| 8 | `get_emulator_combox_api_info_combox_emulator_post` | 获取可选模拟器下拉框信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json   |
| 9 | `get_emulator_devices_combox_api_info_combox_emulator_devices_post` | 获取可选模拟器多开实例下拉框信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/js |
| 10 | `get_notice_info_api_info_notice_get_post` | 获取通知信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exam |
| 11 | `confirm_notice_api_info_notice_confirm_post` | 确认通知  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exampl |
| 12 | `get_web_config_api_info_webconfig_post` | 获取配置分享中心的配置信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  |
| 13 | `get_overview_api_info_get_overview_post` | 信息总览  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exampl |
| 14 | `add_script_api_scripts_add_post` | 添加脚本  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exampl |
| 15 | `get_script_api_scripts_get_post` | 查询脚本配置信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Ex |
| 16 | `update_script_api_scripts_update_post` | 更新脚本配置信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Ex |
| 17 | `reorder_script_api_scripts_order_post` | 重新排序脚本  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exam |
| 18 | `import_script_from_file_api_scripts_import_file_post` | 从文件加载脚本配置  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **E |
| 19 | `export_script_to_file_api_scripts_export_file_post` | 导出脚本配置到文件  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **E |
| 20 | `import_script_from_web_api_scripts_import_web_post` | 从网络加载脚本配置  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **E |
| 21 | `upload_script_to_web_api_scripts_Upload_web_post` | 上传脚本配置到网络  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **E |
| 22 | `import_script_config_file_api_scripts_config_import_post` | 从脚本目录导入配置文件  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  * |
| 23 | `get_maaend_options_api_scripts_maaend_options_post` | 获取 MaaEnd 动态选项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json |
| 24 | `get_user_api_scripts_user_get_post` | 查询用户  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exampl |
| 25 | `add_user_api_scripts_user_add_post` | 添加用户  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exampl |
| 26 | `update_user_api_scripts_user_update_post` | 更新用户配置信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Ex |
| 27 | `reorder_user_api_scripts_user_order_post` | 重新排序用户  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exam |
| 28 | `import_infrastructure_api_scripts_user_infrastructure_post` | 导入基建配置文件  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Ex |
| 29 | `get_user_combox_infrastructure_api_scripts_user_combox_infrastructure_post` | 用户自定义基建排班可选项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json   |
| 30 | `get_maa_depot_items_api_scripts_maa_depot_items_post` | MAA 库存保持物品可选项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  |
| 31 | `get_webhook_api_scripts_webhook_get_post` | 查询 webhook 配置  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  |
| 32 | `add_webhook_api_scripts_webhook_add_post` | 添加webhook项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  ** |
| 33 | `update_webhook_api_scripts_webhook_update_post` | 更新webhook项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  ** |
| 34 | `reorder_webhook_api_scripts_webhook_order_post` | 重新排序webhook项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json   |
| 35 | `get_m9a_available_tasks_api_scripts_m9a_tasks_available_post` | 获取 M9A 可用任务列表（排除 standalone 任务）  获取 M9A 可用任务列表（排除 standalone 任务）  前端调用此接口获取可选择的任务列表， 用于展示在用户编辑界面的任务选择区域。  Args |
| 36 | `get_hsr_stage_options_api_api_scripts_hsr_stage_options_get` | 获取 HSR 体力副本动态选项  返回 M7A/SRA 原生副本字段。  ``userId`` 仅用于校验用户归属；``slot`` 是兼容参数，动态选项当前 按引擎统一返回，不按 slot 生成不同结果。  ### R |
| 37 | `get_hsr_capabilities_api_api_scripts_hsr_capabilities_get` | 获取内置 HSR 能力快照  返回内置 HSR 的能力快照，不暴露原生编辑器会话。  ### Responses:  **200**: Successful Response (Success Response) Con |
| 38 | `get_hsr_managed_config_api_api_scripts_hsr_managed_config_get` | 获取 HSR 托管配置字段  返回原生动态托管字段；用户 ID 只负责归属校验。  ### Responses:  **200**: Successful Response (Success Response) Cont |
| 39 | `import_hsr_direct_config_api_api_scripts_hsr_direct_config_import_post` | 导入 HSR 原生配置快照  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  |
| 40 | `get_oknte_configs_list_api_scripts_oknte_configs_list_post` | 获取 OK-NTE 配置文件列表及 schema  获取 OK-NTE 配置文件列表及 schema 定义。 读写用户配置目录（data/{script_id}/{user_id}/ConfigFile/）， 若为空则自 |
| 41 | `update_oknte_config_api_scripts_oknte_configs_update_post` | 更新 OK-NTE 配置文件  更新 OK-NTE 配置文件  Args:     script_id: OK-NTE 脚本 ID     user_id: 用户 ID     filename: 配置文件名（如 Dai |
| 42 | `batch_update_oknte_configs_api_scripts_oknte_configs_batch_update_post` | 批量更新 OK-NTE 配置文件  批量更新 OK-NTE 配置文件  Args:     script_id: OK-NTE 脚本 ID     user_id: 用户 ID     configs: { filena |
| 43 | `add_plan_api_plan_add_post` | 添加计划表  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Examp |
| 44 | `get_plan_api_plan_get_post` | 查询计划表  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Examp |
| 45 | `update_plan_api_plan_update_post` | 更新计划表配置信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **E |
| 46 | `reorder_plan_api_plan_order_post` | 重新排序计划表  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exa |
| 47 | `get_emulator_api_emulator_get_post` | 查询模拟器配置  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exa |
| 48 | `add_emulator_api_emulator_add_post` | 添加模拟器项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exam |
| 49 | `update_emulator_api_emulator_update_post` | 更新模拟器项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exam |
| 50 | `reorder_emulator_api_emulator_order_post` | 重新排序模拟器项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Ex |
| 51 | `operation_emulator_api_emulator_operate_post` | 操作模拟器  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Examp |
| 52 | `get_status_api_emulator_status_post` | 查询模拟器状态  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exa |
| 53 | `search_emulators_api_emulator_emulator_search_post` | 搜索已安装的模拟器  枚举卸载表并解析主管理器路径（不依赖 ADB 设备枚举）。  ### Responses:  **200**: Successful Response (Success Response) Cont |
| 54 | `add_queue_api_queue_add_post` | 添加调度队列  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exam |
| 55 | `get_queues_api_queue_get_post` | 查询调度队列配置信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  ** |
| 56 | `update_queue_api_queue_update_post` | 更新调度队列配置信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  ** |
| 57 | `reorder_queue_api_queue_order_post` | 重新排序  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exampl |
| 58 | `get_time_set_api_queue_time_get_post` | 查询定时项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Examp |
| 59 | `add_time_set_api_queue_time_add_post` | 添加定时项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Examp |
| 60 | `update_time_set_api_queue_time_update_post` | 更新定时项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Examp |
| 61 | `reorder_time_set_api_queue_time_order_post` | 重新排序定时项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exa |
| 62 | `get_item_api_queue_item_get_post` | 查询队列项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Examp |
| 63 | `add_item_api_queue_item_add_post` | 添加队列项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Examp |
| 64 | `update_item_api_queue_item_update_post` | 更新队列项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Examp |
| 65 | `reorder_item_api_queue_item_order_post` | 重新排序队列项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exa |
| 66 | `add_task_api_dispatch_start_post` | 添加任务  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exampl |
| 67 | `stop_task_api_dispatch_stop_post` | 中止任务  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exampl |
| 68 | `get_power_api_dispatch_get_power_post` | 获取电源标志  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exam |
| 69 | `set_power_api_dispatch_set_power_post` | 设置电源标志  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exam |
| 70 | `cancel_power_task_api_dispatch_cancel_power_post` | 取消电源任务  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exam |
| 71 | `search_history_api_history_search_post` | 搜索历史记录总览信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  ** |
| 72 | `get_history_data_api_history_data_post` | 从指定文件内获取历史记录数据  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json |
| 73 | `get_tools_api_tools_get_post` | 查询工具配置  获取工具设置  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json |
| 74 | `update_tools_api_tools_update_post` | 更新工具配置  更新工具配置  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json |
| 75 | `manual_game_sign_api_tools_sign_post` | 手动触发游戏社区签到  手动触发游戏社区签到  ### Responses:  **200**: Successful Response (Success Response) Content-Type: applicat |
| 76 | `list_game_sign_accounts_api_tools_sign_account_list_post` | 获取所有游戏签到账号组  获取所有游戏签到账号组  ### Responses:  **200**: Successful Response (Success Response) Content-Type: applic |
| 77 | `add_game_sign_account_api_tools_sign_account_add_post` | 添加游戏签到账号组  添加游戏签到账号组  ### Responses:  **200**: Successful Response (Success Response) Content-Type: applicatio |
| 78 | `get_game_sign_account_api_tools_sign_account_get_post` | 获取游戏签到账号组详情  获取游戏签到账号组详情  ### Responses:  **200**: Successful Response (Success Response) Content-Type: applic |
| 79 | `update_game_sign_account_api_tools_sign_account_update_post` | 更新游戏签到账号组配置  更新游戏签到账号组配置  ### Responses:  **200**: Successful Response (Success Response) Content-Type: applic |
| 80 | `delete_game_sign_account_api_tools_sign_account_delete_post` | 删除游戏签到账号组  删除游戏签到账号组  ### Responses:  **200**: Successful Response (Success Response) Content-Type: applicatio |
| 81 | `reorder_game_sign_accounts_api_tools_sign_account_reorder_post` | 调整游戏签到账号组顺序  调整游戏签到账号组顺序  ### Responses:  **200**: Successful Response (Success Response) Content-Type: applic |
| 82 | `login_taygedo_api_tools_sign_account_taygedo_login_post` | 塔吉多账号密码登录  一次性使用账号密码换取并保存塔吉多 Token，不保存密码。  ### Responses:  **200**: Successful Response (Success Response) Con |
| 83 | `login_skland_api_tools_sign_account_skland_login_post` | 森空岛手机号密码登录  一次性使用手机号和密码换取并保存森空岛凭据，不保存密码。  ### Responses:  **200**: Successful Response (Success Response) Cont |
| 84 | `get_scripts_api_setting_get_post` | 查询配置  查询配置  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  ** |
| 85 | `update_script_api_setting_update_post` | 更新配置  更新配置  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  ** |
| 86 | `test_notify_api_setting_test_notify_post` | 测试通知  测试通知  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  ** |
| 87 | `get_webhook_api_setting_webhook_get_post` | 查询 webhook 配置  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  |
| 88 | `add_webhook_api_setting_webhook_add_post` | 添加webhook项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  ** |
| 89 | `update_webhook_api_setting_webhook_update_post` | 更新webhook项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  ** |
| 90 | `reorder_webhook_api_setting_webhook_order_post` | 重新排序webhook项  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json   |
| 91 | `test_webhook_api_setting_webhook_test_post` | 测试Webhook配置  测试自定义Webhook  ### Responses:  **200**: Successful Response (Success Response) Content-Type: appli |
| 92 | `check_update_api_update_check_post` | 检查更新  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exampl |
| 93 | `download_update_api_update_download_post` | 下载更新  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exampl |
| 94 | `cancel_update_download_api_update_cancel_download_post` | 取消下载更新  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exam |
| 95 | `switch_update_download_to_cnb_api_update_switch_to_cnb_post` | 切换下载源到 CNB  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  ** |
| 96 | `install_update_api_update_install_post` | 安装更新  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Exampl |
| 97 | `get_screenshot_api_ocr_screenshot_post` | 获取窗口截图  根据窗口标题获取截图，返回Base64编码的图像数据  Args:     params: 截图参数         - window_title: 窗口标题关键字         - should_pr |
| 98 | `get_screenshot_adb_api_ocr_screenshot_adb_post` | 通过ADB获取设备截图  通过 ADB 端口获取 Android 设备/模拟器截图，返回Base64编码的图像数据  支持两种截图方法： 1. screencap PNG 方法（推荐）：速度快，直接获取 PNG 图像 2 |
| 99 | `check_image_api_ocr_check_image_post` | 检查是否存在指定图像  截图并查找是否存在图片内的内容  Args:     params: 检查图像参数         - window_title: 窗口标题关键字         - image_path: 要查 |
| 100 | `check_image_any_api_ocr_check_image_any_post` | 检查是否存在任意一个指定图像  截图并查找是否存在列表中任意一张图片的内容  Args:     params: 检查图像参数         - window_title: 窗口标题关键字         - imag |
| 101 | `check_image_all_api_ocr_check_image_all_post` | 检查是否存在所有指定图像  截图并查找是否存在列表中所有图片的内容  Args:     params: 检查图像参数         - window_title: 窗口标题关键字         - image_pa |
| 102 | `click_image_api_ocr_click_image_post` | 点击指定图像位置  截图、查找并点击与图像一致的位置  Args:     params: 点击图像参数         - window_title: 窗口标题关键字         - image_path: 要查找 |
| 103 | `click_text_api_ocr_click_text_post` | 点击指定文字位置  截图、OCR识别并点击与文字一致的位置  Args:     params: 点击文字参数         - window_title: 窗口标题关键字         - text: 要查找并点击 |
| 104 | `create_client_api_ws_debug_client_create_post` | 创建 WebSocket 客户端  创建一个新的 WebSocket 客户端实例  - **name**: 客户端唯一名称 - **url**: WebSocket 服务器地址 - **ping_interval**:  |
| 105 | `connect_client_api_ws_debug_client_connect_post` | 连接 WebSocket 客户端  启动指定客户端的连接（非阻塞）  ### Responses:  **200**: Successful Response (Success Response) Content-Typ |
| 106 | `disconnect_client_api_ws_debug_client_disconnect_post` | 断开 WebSocket 客户端  断开指定客户端的连接  ### Responses:  **200**: Successful Response (Success Response) Content-Type: ap |
| 107 | `remove_client_api_ws_debug_client_remove_post` | 删除 WebSocket 客户端  删除指定客户端（会自动断开连接）  注意：系统客户端（如 Koishi）不可删除  ### Responses:  **200**: Successful Response (Succ |
| 108 | `get_client_status_api_ws_debug_client_status_post` | 获取客户端状态  获取指定客户端的状态信息  ### Responses:  **200**: Successful Response (Success Response) Content-Type: applicati |
| 109 | `list_clients_api_ws_debug_client_list_get` | 列出所有客户端  获取所有已创建的 WebSocket 客户端列表及状态  ### Responses:  **200**: Successful Response (Success Response) Content- |
| 110 | `send_message_api_ws_debug_message_send_post` | 发送原始消息  发送原始 JSON 消息到指定客户端连接的服务器  ### Responses:  **200**: Successful Response (Success Response) Content-Type |
| 111 | `send_json_message_api_ws_debug_message_send_json_post` | 发送格式化消息  发送格式化的 JSON 消息（自动组装 id、type、data 结构）  ### Responses:  **200**: Successful Response (Success Response) |
| 112 | `send_auth_api_ws_debug_message_auth_post` | 发送认证消息  发送认证消息到服务器  - **name**: 客户端名称 - **token**: 认证 Token - **auth_type**: 认证消息类型，默认 "auth" - **extra_data** |
| 113 | `get_history_api_ws_debug_history_get` | 获取消息历史  获取消息历史记录  - **name**: 客户端名称，为空则获取所有客户端的历史  ### Responses:  **200**: Successful Response (Success Respo |
| 114 | `clear_history_api_ws_debug_history_clear_post` | 清空消息历史  清空消息历史记录  - **name**: 客户端名称，为空则清空所有  ### Responses:  **200**: Successful Response (Success Response) C |
| 115 | `get_commands_api_ws_debug_commands_get` | 获取可用 WS 命令  获取所有已注册的 WebSocket 命令端点  ### Responses:  **200**: Successful Response (Success Response) Content-T |
| 116 | `qr_create_api_tools_sign_miyoushe_qr_create_post` | 创建二维码  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/json  **Examp |
| 117 | `qr_check_api_tools_sign_miyoushe_qr_check_post` | 轮询扫码状态  轮询状态，确认后返回从 Passport 响应提取的 cookies。  ### Responses:  **200**: Successful Response (Success Response) C |
| 118 | `qr_save_api_tools_sign_miyoushe_qr_save_post` | 保存 cookie 到账号配置  ### Responses:  **200**: Successful Response (Success Response) Content-Type: application/jso |
