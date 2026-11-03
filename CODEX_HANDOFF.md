# DeepSeek 抖音虚拟群友：当前开发交接

最后核对：2026-11-03，用户时区 America/New_York。

新 Codex 先读本文，再按需要查 [README](README.md) 和 [验证记录](docs/VERIFICATION.md)。历史材料已归档，存在已失效的状态和授权，不能作为当前操作指令。

## 1. 用户目标、决定和授权

- 最终目标：DeepSeek 虚拟群友参与真实抖音群“高雅抖音”的聊天。用户不是群主。
- 用户已创建专属抖音账号，现已手动登录并打开目标群。当前读取路径是 Windows 抖音 8.7.0 的 MSAA 可访问性接口。
- **登录、切换账号、验证码和二次验证全部由用户完成。用户明确取消了自动登录，不要恢复登录脚本、读取密码填表或自行退出账号。**
- 使用 uv 管理环境和依赖。后续执行统一用 `uv run --locked`，新增依赖用 uv add。
- 用户要求先跳过实时新消息验证，继续开发；他人历史文字读取与模型回放已验证。不要反复要求群友新发测试文字。
- 用户已批准开发回复审核队列和长期记忆。默认不发送；新真实发送必须有具体目标及完整文字的批准。以前仅批准过一条固定测试文字，且已发送，不能重发或扩展成持续自动发言授权。
- 普通实现细节可采用可逆、可配置默认值，缺少关键信息时再问。不要把离线/单项验证称作自动回复闭环完成。

## 2. 当前本机状态（快照，不是永久标识）

| 项目 | 最后核对结果 |
| --- | --- |
| 项目目录 | `C:\Users\14180\pj_DeepSeek` |
| 环境 | Windows PowerShell；uv 0.11.7；Python 3.13.7；SQLite 3.50.4 |
| Python 环境 | `.venv`，可编辑安装；`.python-version` 固定 3.13.7；uv.lock 已生成并校验 |
| 缓存 | uv.toml 使用 `.uv-cache`，避免沙箱访问默认用户缓存被拒绝 |
| 当前群绑定 | `state/douyin-session.json`，窗口标题“抖音”，群标题“高雅抖音(9)” |
| 临时窗口信息 | pid 12112；handle 460304；当前会话在主窗口内，不再是早期独立群窗口 |
| 最后只读检查 | 成功解析 9 条文字；最后两轮观察均 0 个新增候选 |
| 常驻任务 | 已确认没有匹配本项目 douyin watch 的 Python/uv 进程 |
| 暂停 | `state/PAUSE` 不存在；只解除过本次账号切换创建的暂停 |
| 发送配置 | `config.example.json` 中 dry_run=true；本机没有 config.local.json |
| DeepSeek | `.env` 已存在并配置 Key，实际 API 调用已成功；不要输出或覆盖该文件 |
| 抖音凭据 | Windows 凭据管理器 `pj_DeepSeek/douyin/bot`；已本地录入。不得用于自动登录 |
| 记忆空间 | `2d3c34df-6e84-48ac-ac34-eb035fc447dd`，标签“高雅抖音”，已关联当前绑定 |
| 记忆库 | `state/long_term_memory.sqlite3`，主文件上限 256 MiB，内容预算 128 MiB，保留 180 天 |
| 当前记忆数据 | 1 条手动选中的历史文字；主文件 57,344 字节，内容计费 348 字节 |
| 审核样例 | `58784f9d-414d-4ae4-9abb-53a7dd2ed709`，pending，revision=1；绑定属于旧账号会话，不能直接发送 |
| 旧发送验证 | `state/send-probe-receipt.json` 为 visible_sent，禁止重发、清空回执或复用旧句柄 |
| 自动登录记录 | `state/login-attempt.json` 为 manual_login_confirmed，automatic_login_enabled=false |

进程、句柄、群人数和控件路径都会变化。恢复时先读绑定文件并只读校验；不可照抄表中数值操作。主窗口标题“抖音”和同名群标题本身不构成可靠平台群 ID。

## 3. 已实现与实际验证的范围

| 功能 | 实现与证据 | 当前限制 |
| --- | --- | --- |
| 核心 Engine | 过滤自身/历史/非目标/重复消息，触发、限频、短期上下文、暂停、状态持久化 | 要求可靠 sender_id/is_self/timestamp；尚未与不确定的桌面观察结果接成自动回复循环 |
| DeepSeek | HTTPS、有限重试、禁止重定向携带密钥；实际中文请求成功 | 不提供工具执行能力，不向模型暴露本地配置 |
| Windows 观察器 | bind/inspect/watch，真实客户端读取与历史基线验证通过 | 只读；差分仅是候选观察，不是真实平台消息去重保证 |
| 手动历史回放 | 从固定快照选择一条文字，真实模型回放成功 | 不是实时触发；不会自动发送 |
| 审核队列 | 生成/查看/修改/丢弃/批准/检查/独立发送/人工解决/清理 | 新后端 CheckOnly 在真实窗口成功；新队列实际发送完整流程尚未验收 |
| 一次文字发送 | 原账号下按用户具体批准发送过一次，截图确认消息气泡 | 与新账号、新审核队列的发送验收必须区分 |
| 长期记忆 | 本地容量/期限/隔离/置顶/检索/清理；真实 DeepSeek 精确引用已存原句 | 关键词检索，非向量语义检索；原始历史不批量导入 |
| 凭据管理 | Windows 原生存取删除测试通过，用户已本地保存 | 只维护存储；登录自动化已禁用 |

最终检查：**58 项单元测试全部通过**，uv 锁文件匹配。详见 [验证记录](docs/VERIFICATION.md)。

## 4. 新对话接手的第一轮操作

在项目目录执行这些命令即可检查，不会发送群消息：

```powershell
uv sync --locked
uv run --locked python -m group_friend douyin inspect
uv run --locked python -m group_friend memory stats
uv run --locked python -m group_friend review list
```

当前没有持续观察进程。需要前台观察时：

```powershell
uv run --locked python -m group_friend douyin watch
```

首轮只建立历史基线。Ctrl+C 停止；创建 state/PAUSE 也会停止观察。默认不打印聊天全文，不自动调用模型。

需要改动代码时运行相应测试；交接时刚通过的全套命令：

```powershell
uv run --locked python -m unittest discover -s tests -v
uv lock --check --offline
```

若读取失败，先区分沙箱桌面不可见、窗口重建、群标题变化或用户未打开群。此工具环境通常需要在沙箱外运行只读桌面采集；不应把 UIA 返回 0 个窗口当作抖音未运行。保持用户手动登录，不通过重启或退出账号来试错。

## 5. 绑定与记忆空间的恢复

窗口变化时，先用 MSAA 诊断确认当前窗口，再调用 bind；下面仅示例最后一次实测参数：

```powershell
uv run --locked python -m group_friend douyin bind --window-handle 460304 --window-title '抖音' --group-heading '高雅抖音(9)'
uv run --locked python -m group_friend memory link --space 2d3c34df-6e84-48ac-ac34-eb035fc447dd --binding state/douyin-session.json
```

每次 bind 创建新 session_id。旧队列的批准不能迁移到新绑定；必须重新生成并审核。原绑定保存在 `state/douyin-session.before-account-switch.d223e42d.json`，仅供追溯，不要还原来绕过校验。

记忆空间通过绑定指纹显式关联，不能按群名自动串用。所有记忆空间共享总容量。主文件容量上限不包含事务日志、压缩临时文件、审核库或状态库；没有预先占用 256 MiB 空文件。容量和保留设置在库中持久保存，配置只初始化新库；用 memory resize/retention 调整已有库。

只有已通过核心过滤的输入、手动选择的源文字及确认发送的回复会被记住；草稿不会当作已发送发言。相关记忆以 user 角色的不可信历史数据交给模型，最多 6 条/2000 字符，最多占总字符预算的一半。原文作者昵称只是标签。

## 6. 关键实现及运行陷阱

- `group_friend/core.py`：标准消息、Engine、去重状态库；MemoryStore 为可注入依赖。
- `douyin.py` / `douyin_cli.py`：MSAA 子进程采集、会话绑定、文字解析、保守快照差分、手动回放。
- `review.py` / `review_cli.py` / `review_transport.py`：SQLite 审核队列与原生发送桥接。
- `memory.py` / `memory_cli.py`：容量受控数据库、会话空间和关键词检索。
- `credentials.py` / `credentials_cli.py`：凭据存储、隐藏录入、脱敏 status。不要调用 load 取密码来登录。
- `scripts/Inspect-DouyinMsaa.ps1`：默认名称脱敏；-IncludeText 会保存可见内容；-Stdout 供程序在内存接收 UTF-8 JSON。
- `scripts/DouyinInput.ps1`：原生输入助手，加载不会操作界面，已移除自动登录/读取密码代码。
- `scripts/Send-DouyinReviewedReply.ps1`：真实发送后端；-CheckOnly 在聚焦/输入前返回。
- `scripts/Invoke-DouyinPasswordLogin.ps1`：已禁用的兼容入口，仅返回 manual_login_required，不要恢复。
- 测试文件：test_core/test_llm/test_douyin/test_review/test_memory/test_credentials。

注意事项：

1. UIA 只有窗口容器，MSAA 才读取到实际聊天树。不要重新猜测控件选择器、私有 API 或群 ID。
2. 本机抖音曾用 `--force-renderer-accessibility` 启动；其是否为 MSAA 必要条件尚未做对照验证。
3. Codex 环境有 ELECTRON_RUN_AS_NODE=1，直接启动 Electron 客户端会变成 Node 模式并退出。仅在确实需要、用户授权的客户端启动中移除此变量；当前不需要重启客户端。
4. PowerShell 向 Python 管道输入中文代码可能变成问号；优先用 -X utf8、文件或 ASCII Unicode 转义。原生子进程桥接已显式使用 UTF-8。
5. 动态脚本可能遇到执行策略，使用 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ...`，只作用于本次进程。
6. 视频卡片内作者名不是发言者；头像不能被误判为正文图片。连续消息省略昵称时只能标记为继承推断，系统事件会打断推断。
7. 原生 sender_id/message_id/群 ID 和精确消息时间尚未取得，is_self 仍未知。不要用昵称、消息文字或本地观察序号冒充这些字段。
8. 新审核发送校验绑定、源文字唯一可见、空输入框、焦点及完整草稿，最多按一次 Enter。不能确认结果则 unknown，全队列停止；sending/unknown 不能通过重绑、删除状态或自动重试绕过。
9. 项目目前从源码目录、uv 可编辑环境运行；独立 wheel 分发尚未打包根目录的 PowerShell 资源。

## 7. 接下来适合推进的工作

1. 完善“当前会话快照 → 选择源消息 → 审核草稿”的易用入口，避免使用旧账号/旧人数/旧窗口的历史快照生成新会话回复。
2. 将审核队列的真实发送做一次具体、可复核的验收。先准备完整文字和目标，最后获得用户对该次发送的批准；不要把本会话开发授权视为发送批准。
3. 改进消息归属和可靠会话/自身标识，保持未知字段为未知。不能为了让 Engine 接受桌面观察而填造 sender_id、timestamp 或 is_self。
4. 实时新增验证由用户此前要求暂缓；未撤销该偏好前，可继续做本地开发、历史回放、只读观察和测试。
5. 原本还讨论过人设/触发配置、控制面板、启动恢复工具；它们是可选方向，不是已确认的新任务。先向用户简要说明当前进展再决定下一步。

## 8. 本地数据与历史证据

不要输出 `.env` 或凭据，不要整库导出聊天。不要删除 state 来“修复”发送不确定性。

- state/messages.sqlite3：核心处理状态；replies.sqlite3：审核原文/草稿/批准与发送状态。
- state/long_term_memory.sqlite3：当前 1 条历史输入记忆；可通过 memory 命令检索/清理。
- state/send-probe-receipt.json：旧的 visible_sent 回执，保留并阻止重发。
- diagnostics/douyin-after-send.png / .json：旧账号单次发送证据。
- diagnostics/douyin-history-sender.png / history-replay-result.json：历史发言者人工核对及模型回放证据。
- diagnostics/login-validation.json：自动登录失败的脱敏状态，仅供追溯。
- `.env`、state/、diagnostics/、.venv/、.uv-cache/、config.local.json 均已忽略。

旧文档原样保存在 [docs/archive](docs/archive/)，存在“没有代码”“API Key 未配置”“自动登录待处理”等已失效内容，仅供追溯。当前以本文、代码和本地状态核对结果为准。
