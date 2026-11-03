# DeepSeek 抖音虚拟群友

目标是在真实抖音群里收发文字。用户已确认 **Windows 抖音客户端可以正常收发群消息**。

当前会话：用户已手动登录专属账号，目标群“高雅抖音”已重新绑定，实际标题为 `高雅抖音(9)`，当前显示在主窗口内。长期记忆空间已重新关联，两轮只读采集正常（9 条文字、无新增候选），本次账号切换暂停已解除，默认 `dry_run=true`。持续观察使用 `uv run --locked python -m group_friend douyin watch`；两轮验证进程已结束，尚未启动后台常驻任务。原账号的审核队列项不可直接用于新绑定；下面带 `(8)` 或旧窗口句柄的快照命令用于历史样例，实际操作以当前绑定与新快照为准。

## 环境管理：uv

项目使用 uv 管理本地虚拟环境和依赖。先在项目目录同步环境，再通过 uv 运行，无需手动激活：

```powershell
uv sync --locked
uv run --locked python -m group_friend --mock
uv run --locked python -m unittest discover -s tests -v
```

`.python-version` 固定本项目已验证的 Python 3.13.7；`uv.lock` 记录项目依赖解析结果，应与 pyproject.toml 一起保留。当前没有第三方运行依赖，首次同步会下载构建工具 setuptools 并以可编辑模式安装本项目。源码修改无需重新安装；新增运行依赖用 `uv add 包名`，开发依赖用 `uv add --dev 包名`，依赖变更后同时保留更新的锁文件。`--locked` 会检查锁文件是否匹配配置，详见 [uv 官方项目文档](https://docs.astral.sh/uv/guides/projects/)和[锁定与同步说明](https://docs.astral.sh/uv/concepts/projects/sync/)。

`.venv/` 为本机环境，`.uv-cache/` 为项目缓存，两者均已忽略。uv.toml 将缓存放在项目内，避免本机受限执行环境无法访问默认用户缓存目录。VS Code 默认解释器已指向 `.venv/Scripts/python.exe`。环境重建不需要修改 `.env`、数据库、记忆空间或审核回执。

从项目目录运行 `uv run --locked group-friend ...` 也可使用已安装的命令入口，例如 `uv run --locked group-friend memory stats`。Windows 的独立 PowerShell 脚本命令保持原样。后续更换 Python 版本可先 `uv python pin 版本`，再同步并运行测试。

已在 uv 0.11.7、Python 3.13.7 环境通过 52 项测试，并验证实际 Windows MSAA 采集可用。

## 专属抖音账号：Windows 凭据管理器

登录环节由用户在抖音客户端手动完成。自动登录验证已停止，原自动登录入口仅返回 `manual_login_required`；共享输入助手中的凭据读取、填充和登录提交代码已移除。当前项目保留发送暂停，旧群窗口绑定已归档；手动登录并打开目标群后，先重新绑定并确认目标，再恢复观察或审核发送。

本项目的专属抖音账号和密码存入当前 Windows 用户的凭据管理器，目标名称为 `pj_DeepSeek/douyin/bot`。使用 Windows Generic Credential，保留在本机后续登录会话中，详见 [Microsoft CREDENTIAL 文档](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)。不在 `.env`、配置、长期记忆、审核库或 Git 中保存账号密码；DeepSeek Key 的现有配置保持原样。

在本地交互终端录入，账号、密码及确认密码均隐藏输入：

```powershell
uv run --locked python -m group_friend credentials set
```

也可运行 `scripts/Set-DouyinAccount.ps1` 的独立交互窗口。输入不会成为 shell 命令参数或命令历史；命令不接受 `--password`，非交互终端或无法隐藏输入时拒绝录入。已有凭据不会被默认覆盖，需要更新时用 `credentials set --replace`。

查看状态或删除本项目凭据：

```powershell
uv run --locked python -m group_friend credentials status
uv run --locked python -m group_friend credentials delete
```

状态只显示是否配置及账号的脱敏提示，没有打印密码的 CLI。程序以后可通过明确的 Windows API 调用读取，模型和群消息没有读取此存储的工具。凭据关联当前 Windows 用户及本机，不随仓库复制；换电脑或 Windows 用户后需重新录入。

此入口仅管理凭据存储。账号登录、账号切换、验证码和二次验证均由用户在客户端完成。此前按用户授权进行的一次自动登录测试已退出原账号并设置 +86，提交后客户端返回“账号或密码错误”，未成功登录；用户随后决定取消自动登录，程序不会继续尝试。

已通过 58 项测试，并使用独立随机测试项完成 Windows 原生写入、读取、Unicode 密码及删除验证，测试项已移除。

当前实现了独立核心、DeepSeek 客户端、本地 dry-run 控制台、Windows MSAA 只读观察适配器、历史回放、回复审核队列及长期记忆库。**真实群读取、一次固定文字发送、独立模型调用和模型引用本地记忆均已实测；新审核队列的实际发送与持续自动回复尚未验收。**

## 新增：长期记忆库与容量预算

长期记忆使用本地 `state/long_term_memory.sqlite3`，重启后仍可检索。已初始化 **256 MiB 主数据库容量上限、180 天保留期限**，当前群已关联独立记忆空间。容量按需增长，不预先占满磁盘；事务日志、压缩时的临时文件及其他数据库不计入此主文件上限。

查看当前用量，或调整为 512 MiB：

```powershell
uv run --locked python -m group_friend memory stats
uv run --locked python -m group_friend memory resize --capacity-mb 512
```

`--capacity-mb` 按 MiB 计算（1 MiB = 1,048,576 字节）。主数据库用 SQLite 页数上限控制；一半容量作为文字及记录元数据预算，其余为检索索引和数据库结构留空间。达到预算时淘汰最旧的未置顶记录，索引较大时可能更早淘汰。置顶记录不参与容量淘汰，但仍遵守保留期限；全部被置顶占满时拒绝新写入。缩小容量会先压缩空闲页，如果现有有效内容仍放不下，会拒绝缩容并提示先删除内容。容量上限控制此记忆库，不控制审核队列或消息状态库。

数据库保存容量与保留设置，重启不重置。配置中的 `memory_capacity_mb` 和 `memory_retention_days` 用于新库初始化；已有库请通过 memory 命令调整。关闭自动记忆可在自己的配置中设置 `memory_enabled=false`，已有数据保留供管理命令查看或删除。

每个会话使用独立记忆空间，只有当前空间参与检索；各空间共享总容量预算。本机当前群空间为 `2d3c34df-6e84-48ac-ac34-eb035fc447dd`，可列出空间、检索或加入重要笔记：

```powershell
uv run --locked python -m group_friend memory spaces
uv run --locked python -m group_friend memory search --space 2d3c34df-6e84-48ac-ac34-eb035fc447dd --query '抖音' --show-text
uv run --locked python -m group_friend memory add --space 2d3c34df-6e84-48ac-ac34-eb035fc447dd --text '群话题记录：大家曾聊到抖音冷门推荐。' --pin
```

笔记也可用 `--text-file` 读取 UTF-8 文件。置顶用于保护存储，检索仍按相关性选择；全局人设仍在 persona.txt 中设置。`list --space <空间>` 默认只显示记录元数据；`--show-text` 显示正文。`show --space <空间> --id <记录>` 查看一条记录；`pin` / `unpin` 管理置顶；`forget --space <空间> --id <记录>` 删除一条；`clear --space <空间>` 清空指定空间。记录删除同时清理检索索引，属于逻辑删除，不承诺磁盘安全擦除。

可调整保留期限并清理、压缩：

```powershell
uv run --locked python -m group_friend memory retention --days 90
uv run --locked python -m group_friend memory prune
uv run --locked python -m group_friend memory compact
```

保留期限从写入时间计算，调整会应用到已有记录；过期记录在下次读写或 prune 时移除。被删除的旧记录不会因延长保留期限而恢复。删除后的数据库空闲页会供未来写入复用，compact 可回收文件占用。

抖音没有可靠原生群 ID 时，群名仅用作标签。记忆空间通过显式关联绑定到用户选定的客户端会话，不修改已有审核队列的窗口绑定。客户端重绑后，要复用旧空间请执行：

```powershell
uv run --locked python -m group_friend memory link --space 2d3c34df-6e84-48ac-ac34-eb035fc447dd --binding state/douyin-session.json
```

新群可先 `create-space --label '新群的本地标签'`，再关联新空间。不会因为群名相同而自动共享记忆；未关联空间的抖音回放不读取其他群的记忆。

写入与使用规则：

- 核心程序只保存通过身份、会话、历史和去重过滤的输入，普通非触发文字也可进入记忆；确认发送的回复以 assistant 类别保存。控制台按 console 会话独立存储。
- 抖音审核生成及手动回放只记住所选的一条源文字；模型草稿不会保存成已发生的发言。观察器本身仍只读，不会自动将不确定来源的全部快照或旧历史写入记忆。审核发送被确认可见后才记住相应回复。
- 当前使用中英文关键词匹配（含中文单字和双字片段）检索相关记录，不是向量语义检索。每次最多取 6 条、2000 字符，并最多使用总上下文预算的一半；总字符预算仍受 `context_chars` 控制，为当前消息保留空间。不会把整个记忆库塞入请求。
- 被取回的片段会随当前请求发送给配置的模型服务。记忆以用户消息角色中的历史数据呈现，并标注不可信；不会提升为系统指令，也不能调用工具或读取本地配置。记录中的旧说法不等于已核实事实，当前信息优先。
- 控制台会显示记忆状态与本次取回的记录编号。存储异常时核心仍可继续回复，并提示记忆不可用；手动管理和回放入口会报告存储错误。

本轮已通过 **52 项自动化测试**，覆盖重启检索、来源去重、同文不同来源、空间隔离、容量淘汰、置顶保护、过期清理、缩容拒绝、JSON/长度预算、昵称标签和发送确认后写入。已在本机保存一条此前选中的真实历史文字，并在新进程中检索后交给 DeepSeek；模型准确引用了存储的原句。本轮没有向抖音发送消息。

## 新增：回复审核队列

使用 `uv run --locked python -m group_friend review` 管理本地待审核回复。生成、修改、批准都不会输入或发送抖音消息。没有后台自动发送任务；实际发送由独立的 `send` 命令触发，并要求本地配置 `dry_run=false`。

本机已用真实 DeepSeek 生成一条待审核回复，可查看：

```powershell
uv run --locked python -m group_friend review list
uv run --locked python -m group_friend review show 58784f9d-414d-4ae4-9abb-53a7dd2ed709
```

从已检查的固定快照生成其他待审核回复，索引沿用前面的 inspect 输出：

```powershell
uv run --locked python -m group_friend review generate --snapshot .\diagnostics\douyin-history-older.json --index 1
```

加 `--mock` 则不调用 API。生成时只向模型提交所选的一条文字，结果加入 `pending` 队列；段落换行为单行。该入口是手动历史回放，不会把观察器的未知来源候选自动转成回复。

以下示例的 `$replyId` 替换为本机实际队列编号；每步根据 show 显示的版本操作：

```powershell
$replyId = '58784f9d-414d-4ae4-9abb-53a7dd2ed709'
uv run --locked python -m group_friend review show $replyId
uv run --locked python -m group_friend review edit $replyId --revision 1 --text '[AI] 哈哈，推荐流确实越来越冷门了。'
```

编辑后版本变为 2，旧批准失效；也支持 `--text-file .\reply.txt` 读取 UTF-8 文本。文字自动保留 `[AI]` 标识，最长 300 字符，禁止控制字符和多行内容。不想发送时使用 `discard <编号> --revision <版本>`，逻辑清除原文和回复。

确认目标群及完整文字后，可批准当前版本（不会发送）：

```powershell
uv run --locked python -m group_friend review approve $replyId --revision 2 --target '高雅抖音(8)'
uv run --locked python -m group_friend review check $replyId
```

批准与具体文字、版本和窗口绑定关联，10 分钟过期，可再次查看并显式批准续期。`check` 只读检查客户端进程启动时间、窗口、群标题、源消息唯一性及输入区是否为空，不聚焦或输入。历史源文字当前不可读时会拒绝；请手动将目标群滚动到对应源消息再检查。重新绑定客户端后必须重新生成队列项，旧批准不可沿用。

真正执行发送前，将自用 `config.local.json` 的 `dry_run` 改为 `false`（保留示例文件默认值），再对已批准的版本执行：

```powershell
uv run --locked python -m group_friend review --config config.local.json send $replyId --revision 2
```

上述为操作示例，本轮没有执行批准或发送。默认配置会拒绝发送。输入前再校验目标和源文字、保留已有输入框草稿、校验焦点、输入后核对完整文字；最多按一次 Enter。只有输入框清空且消息区对应文字的数量恰好增加一条，才标为 `visible_sent`，这不是服务端送达回执。消息未滚动到可读取区域、窗口改变等情况可能导致结果无法确认，此时不会重试。

队列用 SQLite 原子占用防止并发发送。`sending` 或 `unknown` 会阻止整个审核队列继续发言，重启或重绑也不能绕过。`state/PAUSE` 在生成及输入关键阶段都会检查；输入后暂停可能留下未发送草稿，需人工核对。审核发送沿用配置中的冷却、小时和每日限额，当前按整个队列统计，不使用昵称伪造用户限额。

若发送结果不确定，请先检查群内消息及输入区，再用 `resolve <编号> --outcome visible_sent|not_sent --note '核对说明'` 记录人工结果；不会再次发送该条。疑似崩溃的 `sending` 至少等待 90 秒，避免把仍在运行的发送提前解除。不要清空数据库或回执来解决不确定发送。

审核队列会有意在已忽略的 `state/replies.sqlite3` 保存选中的原文、生成回复及绑定信息，方便重启后继续审核；其他群聊不写入该库。`review purge --days 7` 逻辑删除超过七天的终态项目，保留待审核、已批准和未确认发送记录。逻辑删除不承诺磁盘安全擦除。

本轮验证：38 项自动化测试通过；真实模型生成并持久化一条 `pending` 回复；新后端在真实群窗口的 `CheckOnly` 只读预检查返回 `ready`，历史源文字不可见的拦截也生效。没有修改默认发送配置，没有批准队列样例，没有新的真实群发送；新发送完整路径仍待按具体回复批准后验收。

## 新增：只读观察与手动回放

用户要求先跳过实时新消息验证，继续开发。本轮新增可运行的只读适配器，不向群里发送，也不会自动调用模型。请从项目源码目录运行；Windows 采集依赖仓库中的 PowerShell 脚本。

本机已创建当前群窗口的绑定 `state/douyin-session.json`，可直接启动：

```powershell
uv run --locked python -m group_friend douyin watch
```

每次启动会把首轮读取到的文字作为历史基线，不输出为新增候选。默认每 5 秒采集一次，Ctrl+C 停止；创建 `state/PAUSE` 也会停止观察，恢复后重新运行命令并建立新基线。默认只显示统计及本地候选编号；加 `--show-text` 才会在本地终端显示内容和昵称。原始快照通过 UTF-8 管道保存在内存，不默认落盘。只运行两次检查可加 `--captures 2`。

窗口关闭、客户端重启或群标题变化后须重新绑定。先用已有诊断脚本确认独立群窗口句柄、窗口标题及完整群标题，再执行（下列值是格式示例）：

```powershell
uv run --locked python -m group_friend douyin bind --window-handle 123456 --window-title '目标群' --group-heading '目标群(8)'
uv run --locked python -m group_friend douyin inspect --show-text
```

绑定核对进程启动时间、句柄、窗口标题和群标题。它仅绑定用户手动选中的本次窗口，不是抖音原生群 ID，也不会自动选择或打开群。

观察器跳过视频、图片、账号卡片及已识别的不可查看提示，拼接文字节点，并将连续消息的昵称继承标为推断。昵称始终不作为账号 ID；`sender_id`、`message_id`、`timestamp` 和 `is_self` 保留未知。本地候选编号也不是平台消息 ID。窗口重复暴露的主页面不会被同时采集，避免同一会话重复计数。

快照差分仅在具有唯一连续重叠、尾部出现未见文字时输出候选；加载旧历史、编辑、重复文字歧义、无重叠或采集不完整时停止。相同画面可能隐藏被替换的同文消息，因此不能把该策略视为可靠的实时检测或持久化去重。新消息实测按用户要求暂时跳过，观察器不会把候选传给自动发送核心。

用保存的历史快照检查文字索引和手动生成回复：

```powershell
uv run --locked python -m group_friend douyin inspect --snapshot .\diagnostics\douyin-history-older.json --window-handle 460288 --window-title '高雅抖音' --group-heading '高雅抖音(8)' --show-text
uv run --locked python -m group_friend douyin replay --snapshot .\diagnostics\douyin-history-older.json --window-handle 460288 --window-title '高雅抖音' --group-heading '高雅抖音(8)' --index 1 --mock
```

以上快照仅存在于本机已忽略的诊断目录；其他机器需自行生成快照并替换参数。索引从 0 开始；回放必须使用与 inspect 相同的快照，防止界面变化使索引指向其他消息。去掉 `--mock` 会把选中的一条文字交给本地配置的模型服务，产生 API 费用，并只在终端显示回复。不会上传其他消息或昵称，不会绕过核心的历史过滤去自动发言。暂停文件在模型调用前后都会检查；模型执行期间暂停会抑制回复输出。

已验证：25 项自动化测试通过；真实客户端会话绑定和两轮只读采集成功（均解析出 2 条文字，未输出候选）；本地历史快照解析出 2 条文字，手动 mock 回放成功。另用包含本人新消息的已有快照验证了显式昵称与文字的提取。头像不会被误当成消息图片而过滤正文。未执行新的真实群发送或真实新消息测试。

## 1. 先诊断 Windows 客户端

请在 Windows 打开抖音，登录并手动打开目标群，保持窗口可见。在项目目录打开 **Windows PowerShell**（不是 WSL 终端），执行：

```powershell
cd C:\Users\14180\pj_DeepSeek
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Inspect-Douyin.ps1
```

`-ExecutionPolicy Bypass` 仅作用于这个脚本进程，不修改系统策略。脚本只读取 UI Automation 控件树，不点击、不输入、不发送、不读取输入框 Value，也不截图。输出到 `diagnostics\douyin-uia.json`；默认隐藏窗口标题和控件名称，但保留控件类型、类名、AutomationId 和支持的模式。发送给别人之前仍应检查是否含敏感标识。

如果没有找到窗口，请从任务管理器“详细信息”确认抖音进程 PID 后指定，例如：

```powershell
.\scripts\Inspect-Douyin.ps1 -ProcessId 12345
```

若控件树已能看到聊天区域，但需要文字来定位控件，可在本地执行下面这条。**该选项可能把可见群消息和昵称写入文件，请先脱敏再分享。**

```powershell
.\scripts\Inspect-Douyin.ps1 -IncludeText -OutputPath .\diagnostics\douyin-uia-with-text.json
```

2026-11-03 已在本机 Windows 实测：Python 3.13.7 可运行，15 项核心测试通过。用户打开目标群后，检测到抖音 8.7.0 窗口，但 Control 与 Raw 两种 UIA 控件树均只有窗口和 `Intermediate D3D Window` 两个节点，没有消息、发送者或输入控件。按 Chromium 文档发送 `WM_GETOBJECT` 可访问性探测后仍只有两个节点。尚不能据此实现可靠的收发适配器。

诊断报告现在记录桌面窗口总数和会话编号；若桌面窗口总数为零，只能说明当前执行环境不可见桌面，不能断定抖音未打开。可用 `-TreeView Raw` 检查原始树，并通过 `-OutputPath` 保留不同诊断结果。Codex 受限执行环境起初看不到桌面，在沙箱外重试且用户打开客户端后成功识别窗口。

用户批准重启后，已确认抖音带 `--force-renderer-accessibility` 成功启动。该参数来自 [Chromium 官方文档](https://www.chromium.org/developers/design-documents/accessibility/)。注意 Codex 环境的 `ELECTRON_RUN_AS_NODE=1` 会令客户端进入 Node 模式，带参数时立即退出（实测退出码 9）；只需在启动子进程时移除此变量，无须修改系统配置。新增启动脚本会临时移除该变量，并在启动后恢复当前 PowerShell 环境；不结束已有进程。完全退出抖音后执行：

```powershell
.\scripts\Start-DouyinAccessible.ps1
```

进入目标群后，UIA 仍只有 3 个容器节点，但 **MSAA 可读取完整控件树**，已在未截断、无读取错误的报告中读到群标题、部分昵称、消息时间及“发送消息”输入区域。已对比测试前后快照：先前不存在 `/ai 接收测试1103`，用户安排发送后，聊天内容和会话摘要中均读到该文字。多个窗口会重复暴露同一会话，不能据控件出现次数当作新消息数。此次结果仅验证界面读取；不能确认可访问性参数是否为 MSAA 可读的必要条件。

可复现 MSAA 检查（默认隐藏名称，`-IncludeText` 会将可见消息和昵称写入本地文件）：

```powershell
.\scripts\Inspect-DouyinMsaa.ps1
.\scripts\Inspect-DouyinMsaa.ps1 -IncludeText -OutputPath .\diagnostics\douyin-msaa-with-text.json
```

用户确认接收测试消息是本人发送；这次只验证了自己发送的新消息能被读取，其他群成员的新消息检测仍待单独验证。用户随后明确批准发送一次 `[AI] 接入测试：消息收发验证。`，已在手动确认的独立群窗口中完成：核对进程、窗口句柄、群标题和接收测试标记 → 确认输入区为空 → MSAA 激活输入区并校验焦点 → Unicode 输入 → 核对完整草稿 → 按一次 Enter。发送后输入区清空，会话摘要更新；本地截图中确认出现对应的本人消息气泡。没有获取服务端回执，不据此声称对方已收到或已读。

用户提出用其他成员的历史消息代替新发测试。已向上翻阅当前群历史，读取实际文字“这应该是抖音最深处了吧”，并用 `diagnostics/douyin-history-sender.png` 人工核对对应群成员与左侧消息气泡，排除本人消息及卡片内作者名。发现连续消息可能省略重复昵称，控件树顺序也不能简单当作屏幕上的正序；不能靠最近一个昵称就判定发送者。仅将这一句文字交给现有 DeepSeek 客户端，成功生成本地回复，没有上传整段历史或发送者昵称，没有调用抖音发送。回放结果见已忽略的 `diagnostics/history-replay-result.json`；平台 ID 和精确时间保持未知，未绕过核心过滤器将历史消息作为实时消息处理。此项验证了他人历史文字读取、人工核对昵称及模型生成，未验证实时新增检测、自动发送者归属或可靠去重。

`scripts/Test-DouyinSend.ps1` 是针对当前手动确认窗口的一次性诊断工具，不是自动收发适配器。它使用本地 `state/send-probe-request.json` 和回执文件；默认只写草稿，发送前再次检查会话、草稿及焦点，发送动作前写入 `sending_unknown`，禁止自动重试。已实测输入后占位标签会消失、工具栏结构会变化，因此不能固定按旧标签定位或假设存在可点击的发送按钮；脚本支持只读 `VerifyDraft` 核对已有草稿。此次回执已在人工检查截图后标记为 `visible_sent`，保留重复操作阻止记录。请求文件包含本次进程和窗口句柄，窗口重建后不可复用；不要清空回执来重发。

目前仍未取得稳定的群 ID、账号 ID 或消息 ID，控件路径随界面变化。昵称不能视作账号 ID，群标题不能单独保证会话唯一，快照差分也不构成可靠的长期去重方案。尚未实现轮询监听、其他群成员与自身消息的可靠区分或生产适配器。单次发送授权不扩展为持续自动发言授权。

持续自动接入前仍须确认：目标会话如何可靠识别、发送者如何识别、是否有稳定消息 ID/时间、怎样区分自己的消息，以及登录失效如何检测。不能只凭可重名群名自动发送。当前单次验证依靠用户手动确认会话及指定测试文字，不代表以上问题已解决。

## 2. 运行核心程序

核心程序仅使用标准库，无第三方运行依赖。项目由 uv 管理，默认使用已验证的 Python 3.13.7；先执行 `uv sync --locked`，再在项目目录运行：

```powershell
uv run --locked python -m group_friend --mock
```

离线控制台也可在其他系统使用 uv 运行；抖音控件采集和发送需要 Windows PowerShell。输入 `/ai 你好` 会显示固定离线回复，**不会调用 DeepSeek 或发送抖音消息**。默认限频生效，连续输入会显示 `rate_limited`。

真实模型测试：复制 `.env.example` 为 `.env`，在本地填写 `DEEPSEEK_API_KEY`，然后运行：

```powershell
uv run --locked python -m group_friend
```

不要把 Key 发到聊天里。真实模型模式会将你在控制台输入的最近少量消息发给配置的 API 服务，并产生 API 费用；输出仍只显示在控制台。环境变量优先于 `.env`。默认使用 `https://api.deepseek.com`、`deepseek-flash`，可通过 `.env` 修改。接口与模型名依据 2026-11-03 查看的 [DeepSeek 官方文档](https://api-docs.deepseek.com/zh-cn/)。用户已在本地配置 Key，2026-11-03 已通过实际模型客户端完成固定文字 API 测试，中文请求精确返回“连接成功。”。测试未向模型上传群聊，也未将模型结果发送至抖音。

从 Windows PowerShell 通过管道向 Python 传递代码时，要保证管道使用 UTF-8，或在测试代码中使用 Python Unicode 转义；默认管道编码可能将中文提示损坏。本次首次请求连通但提示未正确保留中文，改用 Unicode 转义后再次测试，精确匹配预期回复。共两次短请求，无自动重试。

## 3. 配置、暂停与状态

复制 `config.example.json` 为 `config.local.json`，用 `--config config.local.json` 加载。`local-test` 和 `bot-local` 只是控制台标识，不是抖音群 ID 或账号 ID。当前仅支持 `/ai` 或 `/ai 空格 内容` 触发，暂未实现 @ 识别。

- `/pause`：暂停；`/resume`：恢复；`/quit` 或 Ctrl+C：停止。
- 外部暂停：创建 `state/PAUSE` 文件。删除该文件，并在必要时输入 `/resume` 后恢复。模型请求不会立刻取消，但完成后会再次检查暂停，阻止发送。
- 控制台只接受 `dry_run=true`。目前没有开启真实发送的命令。
- 默认群冷却 30 秒，用户冷却 60 秒，每滚动小时最多 10 条，每滚动 24 小时最多 30 条；dry-run 也计入额度。不是自然日计数。
- 近期滑动上下文保存在内存，默认最多 12 条；开启长期记忆后会从独立本地记忆库补充相关片段，总计最多 6000 字符。这是字符预算，不是精确 token 计算。
- 消息状态 SQLite 仅记录消息标识、发送者标识、状态和时间；长期记忆库另行保存通过过滤或手动选中的文字。不同平台分别去重，发送者 ID 必须可靠，不能拿昵称冒充。
- 状态以 `(platform, conversation_id, message_id)` 唯一约束，重启不会重发已处理消息。生成失败也不自动重处理同一消息。
- 发送超时或异常记为 `unknown` 并暂停；重启仍阻止该会话继续发送。崩溃时处于 `sending` 的消息也转为 `unknown`。将来接入后必须人工核对实际发送结果再处理此状态，当前不提供盲目清除命令。UI 自动化不承诺严格只发送一次。
- 更改 `persona.txt` 后重启生效。群消息只进入用户消息角色，核心没有 shell、文件访问或工具执行能力；这不保证模型不会受到文字诱导，人设一致性仍需真实模型评估。

清理：停止程序后可删除 `state/` 清除本地状态，但**也会丢失去重与未确认发送记录**；真实接入后不能用清空数据库解决发送不确定问题。诊断文件可单独删除。`.env`、状态和诊断文件均已加入 `.gitignore`。`config.local.json` 也被忽略。

## 4. 验证

```powershell
uv run --locked python -m unittest discover -s tests -v
```

覆盖自身/历史/非目标消息过滤、重启去重、相同文字不同 ID、崩溃恢复、发送不确定性、生成期间暂停、目标切换、限频、API 超时与限流的有界重试，以及群文字与系统规则分离。测试使用假模型和假发送器，不能替代真实客户端验收。

真正验收仍需：目标群收到其他成员新消息 → 正确判断触发 → DeepSeek 回复 → 发到正确群 → 忽略自己的回复。Windows MSAA 已读到本人发出的新测试消息，并在用户批准后完成一次固定文字发送，截图确认出现消息气泡；DeepSeek API 的独立中文请求也已验证。可靠身份标识、其他群成员消息检测和完整自动回复循环仍待验证。用户的 `.env` 已配置，禁止输出或分享该文件。

## 5. 文件

- `group_friend/config.py`：配置和环境变量。
- `group_friend/core.py`：标准消息、持久化状态、过滤、限频和发送接口。
- `group_friend/llm.py`：HTTPS API、有界重试、不转发重定向中的密钥。
- `group_friend/douyin.py`：MSAA 只读采集、会话绑定、文字解析和保守快照差分。
- `group_friend/douyin_cli.py`：bind / inspect / watch / replay 命令，无发送接口。
- `group_friend/review.py`：审核队列、版本批准、并发占用与结果记录。
- `group_friend/review_cli.py`：生成、查看、编辑、丢弃、批准及独立发送命令。
- `group_friend/review_transport.py`：审核发送的 Windows 校验与子进程桥接。
- `group_friend/memory.py`：长期记忆数据库、容量控制、会话空间和相关上下文检索。
- `group_friend/memory_cli.py`：记忆容量、期限、笔记、关联和清理命令。
- `group_friend/credentials.py`：Windows 凭据 API，敏感字段不进入对象 repr。
- `group_friend/credentials_cli.py`：本地隐藏录入、脱敏状态和删除。
- `group_friend/__main__.py`：本地 dry-run 控制台。
- `scripts/Inspect-Douyin.ps1`：Windows UIA 只读诊断。
- `scripts/Inspect-DouyinMsaa.ps1`：Windows MSAA 只读诊断，默认隐藏名称。
- `scripts/Start-DouyinAccessible.ps1`：用可访问性参数启动客户端，避免继承 Electron Node 模式。
- `scripts/Test-DouyinSend.ps1`：对手动确认窗口分阶段执行一次草稿/发送验证，保留回执阻止重复操作。
- `scripts/DouyinInput.ps1`：共享原生输入助手，加载不会执行输入。
- `scripts/Send-DouyinReviewedReply.ps1`：审核发送后端，支持 `-CheckOnly` 只读验证。
- `tests/`：关键失效场景测试。

Windows 控件检查路线参考 [pywinauto 官方文档对 UIA 的说明](https://pywinauto.readthedocs.io/en/latest/getting_started.html)，此项目的诊断脚本直接使用 Windows UIAutomation，不依赖 pywinauto。官方群聊能力也做过复核，其 [公开仓库](https://github.com/bytedance/douyin-im-group-open-capabilities) 不能作为当前个人账号已获普通群消息收发权限的证据。
