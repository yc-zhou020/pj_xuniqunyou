# 验证记录与验收边界

## 2026-11-04：Ubuntu 迁移适配

- 移除 VS Code 的 Windows 专用解释器路径和 WSL 禁用设置，使用虚拟环境目录。
- Firefox 集成测试改用 Playwright 对当前系统提供的可执行文件路径，支持指定浏览器安装目录；环境缺失时的清理也得到保留。`REQUIRE_BROWSER_TESTS=1` 将浏览器缺失视为失败。
- Windows 回归 35 项通过；Ubuntu 24.04 WSL、Python 3.13.7、Playwright 1.64.1 回归也为 **35 项通过，无跳过**，包括 3 项真实 Linux 无界面 Firefox 本地 HTML 测试及 `fcntl.flock` 互斥测试。
- Ubuntu `uv lock --check --offline` 和 `run_friend.py --help` 通过。新增 Ubuntu 24.04 GitHub Actions 工作流，尚未触发远程执行。
- 新增 Ubuntu 安装、登录、数据库及回执迁移说明。用户恢复的 `example/` 参考项目保留。
- 没有真实抖音访问或模型调用。Ubuntu 真实账号登录及收发仍需迁移后验收。

## 2026-11-04：精简项目

- 移除 Windows 原生路线、审核队列、长期记忆、凭据管理及其专用测试。网页所需的锁、日志和回复校验独立提取。
- `uv run --locked python -m unittest discover -s tests -v`：**35 项全部通过，无跳过**，包含 3 项真实无界面 Firefox 本地 HTML 集成测试。
- 覆盖消息读取、重复文字、身份不匹配、人工草稿保护、composition 重复输入回归、单次按钮点击及确认；不访问真实抖音，不使用登录资料。
- 网页核心测试覆盖历史基线、重启恢复、人工接管、未知发送不重试、旧数据库未知回执阻断、配额延后、暂停、白名单移除和上下文预算。24 小时虚拟时间耐久测试包含 17,280 次采样、2,880 条模拟来信和临时模型失败；不代表实网耐久验收。
- 提取后的互斥锁排他与释放、回复校验测试通过，模型客户端请求和有限重试测试通过。
- `run_friend.py --help`、`python -m group_friend --help`、`group-friend --help` 均通过，三个入口统一进入网页运行器。
- `uv lock --check --offline` 通过。依赖版本未变。测试用环境、浏览器和缓存均为此次临时安装，验证后清理。
- 本次没有真实模型调用或对外发送。

## 历史实网证据

- 2026-11-04 11:02：真实 DeepSeek → 无界面 Firefox → Nefariousa 单次发送成功，日志 `visible_sent/confirmed`，任务 5087a2…。单次点击后看到新己方气泡且草稿为空，没有群消息。
- 旧 unknown 经用户确认未发送，通过正式 resolve 标记 not_sent，没有重试或删除回执。
- 后续 180 秒运行正常结束：37 次采样、0 次额外发送、无历史重放，期间没有新增来信。
- 历史全套 149 项测试包含本次已移除的旧路线测试，不作为当前测试数量。持续多轮真实收发、长期网络抖动与登录过期恢复仍待验收。
