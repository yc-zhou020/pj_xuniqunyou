# DeepSeek 抖音私聊助手：开发交接

## 当前路线

- Python 直接调用 DeepSeek，Playwright/Firefox 操作抖音网页版，结构化文本快照；固定 5 秒采样、最小发送间隔 5 秒，不使用 Node 桥接。
- 白名单仅 Nefariousa 私聊，禁止向群聊发送。历史会话中用户已允许测试时点击发送；本次清理没有调用模型或发送消息。
- 登录、切换账号、验证码和二次验证由用户手动完成，不恢复自动登录或读取密码填表。
- 使用 uv 管理依赖，运行命令统一 `uv run --locked`。启动：`uv run --locked python run_friend.py`，默认配置 `browser.local.json`。
- `browser.local.json` 的 `reply_config` 可继续指向原来的 `config.autonomous.local.json`；新安装使用 `config.local.json`。不覆盖已有配置。

## 本次精简

用户要求删除不需要的文件。已移除 Windows MSAA/原生发送路线、PowerShell 助手、人工审核队列、长期记忆和凭据管理模块、对应测试及旧示例配置，删除过期文档归档并精简 README 和交接。

网页仍使用的进程锁、轮转日志和回复校验提取至 `runtime_support.py`。三个启动入口统一进入 `browser_runtime.py`。保留 `config.py` 旧配置字段以兼容已有本地配置，保留数据库旧表的未知发送阻断。

`group_friend/browser_reference/` 是现用网页读取和身份核验实现，必须保留 JS、选择器及 AGPL-3.0 LICENSE。用户已恢复 `example/`，它是供开发者参考的项目，必须保留；不参与运行不等于可以删除。

## Ubuntu 迁移

目标 Ubuntu 24.04 LTS，步骤见 [docs/UBUNTU.md](docs/UBUNTU.md)。使用 uv 重建 Python 环境、安装 Linux Firefox 及系统依赖；不复制 Windows `.venv` 和浏览器二进制。迁移时停止旧实例，保留本地配置、数据库和回执，在 Ubuntu 图形桌面手动重新登录。登录后无界面运行不需要桌面。

VS Code 解释器配置已使用跨平台虚拟环境目录。Firefox 本地测试使用 Playwright 提供的系统对应可执行文件路径，设置 `REQUIRE_BROWSER_TESTS=1` 时禁止因缺少浏览器而跳过。新增 GitHub Actions Ubuntu 24.04 回归任务，未触发远程运行。`runtime_support.py` 已包含 Linux `fcntl.flock` 锁实现。

本次 Windows 与 Ubuntu 24.04 WSL 均通过 35 项测试（无跳过），包括真实无界面 Firefox 本地 HTML 测试、互斥锁、状态队列和模型客户端测试。Ubuntu 依赖锁及 `run_friend.py --help` 检查通过。该结果不等于 Ubuntu 真实抖音登录或收发已验收；没有访问抖音或调用真实模型。

## 实际验证边界

- 历史记录：2026-11-04 11:02，真实 DeepSeek → 无界面 Firefox → Nefariousa 单次发送成功，日志 `visible_sent/confirmed`，任务 5087a2…。
- 随后 180 秒运行正常退出：37 次采样、0 次额外发送、无历史重放。期间没有新来信，持续多轮收发尚未验收。
- 历史完整测试共 149 项，其中包含本次已删除的旧路线测试。当前保留网页核心、Firefox 本地集成和模型客户端测试，具体本次结果见 `docs/VERIFICATION.md`。
- 本次保留的 35 项测试全部通过，包括 3 项真实 Firefox 本地 HTML 集成测试；三个启动入口和依赖锁检查通过。测试临时下载的环境与缓存已清理，没有访问抖音或调用真实模型。
- 本工作区是项目副本，不能根据历史记录断言本地配置、`state/` 或登录资料当前存在；使用前检查实际文件。

## 发送状态与数据保护

- prepare 按键输入并核对完整草稿、平台身份和源消息；失败保留 generated。Firefox 实际编辑器的 `fill()` 曾重复文字，必须保留 `press_sequentially` 和草稿校验。
- 紧邻唯一按钮点击才记录 sending；点击异常为 unknown，绝不自动重试。重启将 sending 转为 unknown，未决发送阻断队列。
- 人工草稿不覆盖，人工回复取消对应任务；移出白名单的任务不执行。平台 ID 缺失保持空值，本地指纹不是平台 ID。
- 不删除或改写旧回执来排除阻断。不输出 `.env`、Cookie、密码或整库聊天。保留已有登录资料、配置、数据库和回执。
- `--inspect` 不调用模型、不发送、不写回复状态；`--status` 只读。`--resolve` 需停止运行、至少等 90 秒、明确结果和人工核对说明，不重发。

详细使用方式见 [README.md](README.md)。
