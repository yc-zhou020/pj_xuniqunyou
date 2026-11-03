# DeepSeek 抖音虚拟群友

通过 DeepSeek 在真实抖音群中生成回复的本地项目，当前提供 Windows 只读观察、手动历史回放、回复审核队列和长期记忆。

**持续自动回复尚未完成。** 已验证客户端读取、原账号下的一次固定文字发送、真实 DeepSeek 调用及模型引用本地记忆；新审核队列的实际发送仍待验收。接续开发先读 [CODEX_HANDOFF.md](CODEX_HANDOFF.md)，最新实测见 [docs/VERIFICATION.md](docs/VERIFICATION.md)。

## 当前会话

截至 2026-11-03：用户已手动登录专属账号，目标群为“高雅抖音”，实际标题 `高雅抖音(9)`。已重新绑定并关联原长期记忆空间，最后采集到 9 条文字。没有后台持续观察任务，state/PAUSE 不存在，默认 dry_run=true。本地旧队列样例属于旧绑定，需重新生成和审核。

**登录、切换账号和验证码均由用户手动完成。自动登录已取消并禁用。**

## 环境和启动

在项目目录的 Windows PowerShell 中运行：

```powershell
uv sync --locked
uv run --locked python -m group_friend --mock
uv run --locked python -m unittest discover -s tests -v
```

项目固定已验证的 Python 3.13.7，使用 .venv 和 uv.lock，当前无第三方运行依赖；首次同步需下载构建工具 setuptools。源码以可编辑方式安装，修改代码无需重装。新增运行依赖用 `uv add 包名`，开发依赖用 `uv add --dev 包名`，同时保存 pyproject.toml 与 uv.lock。参见 [uv 官方项目文档](https://docs.astral.sh/uv/guides/projects/)。

uv.toml 将缓存置于项目内的 .uv-cache，VS Code 默认解释器指向 .venv/Scripts/python.exe。无需手动激活环境。`uv run --locked group-friend ...` 也可使用同一入口。Windows 采集依赖仓库根目录的 scripts，当前按源码目录运行。

## 群窗口绑定和只读观察

先手动登录并打开目标群，再查看当前绑定：

```powershell
uv run --locked python -m group_friend douyin inspect
uv run --locked python -m group_friend douyin watch
```

watch 默认每 5 秒采集一次，首轮建立历史基线，Ctrl+C 停止；`--captures 2` 可只检查两轮。默认输出统计，`--show-text` 才显示可见文字和昵称。原始采集 JSON 在内存处理，不默认写盘。

窗口变化时，先诊断再 bind。以下是最后实测参数，不能作为永久句柄使用：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Inspect-DouyinMsaa.ps1
uv run --locked python -m group_friend douyin bind --window-handle 460304 --window-title '抖音' --group-heading '高雅抖音(9)'
uv run --locked python -m group_friend memory link --space 2d3c34df-6e84-48ac-ac34-eb035fc447dd --binding state/douyin-session.json
```

每次 bind 会创建新会话，旧审批不沿用。记忆空间需要显式关联，不能只凭同名群自动共享。观察器不会自动生成或发送回复；遇到目标变化、历史加载、重复文字歧义、无重叠或读取不完整时停止。

UIA 在本机只暴露容器，MSAA 可读取正文。昵称继承仍属于推断，平台账号/消息/群 ID、精确时间与 is_self 保持未知；本地观察编号不等于原生消息 ID。

## 快照回放和审核队列

需要手动生成回复时，先保存当前快照并检查文字索引：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Inspect-DouyinMsaa.ps1 -IncludeText -OutputPath .\diagnostics\current-group.json
uv run --locked python -m group_friend douyin inspect --snapshot .\diagnostics\current-group.json --window-handle 460304 --window-title '抖音' --group-heading '高雅抖音(9)' --show-text
```

快照会包含可见消息和昵称，存于已忽略的 diagnostics。替换示例中的窗口参数，并根据 inspect 选择索引；索引从 0 开始。固定快照可以避免界面变化使索引指向其他消息。

```powershell
$messageIndex = 0
uv run --locked python -m group_friend review generate --snapshot .\diagnostics\current-group.json --index $messageIndex --mock
uv run --locked python -m group_friend review list
```

去掉 --mock 会调用本地配置的 DeepSeek，提交所选的一条文字及相关记忆，产生 API 费用；结果只加入 pending 队列。新建条目不会批准或发送。

将输出的新编号填入 `$replyId`，检查、修改或丢弃：

```powershell
$replyId = '填入新生成的编号'
uv run --locked python -m group_friend review show $replyId
uv run --locked python -m group_friend review edit $replyId --revision 1 --text '[AI] 这是修改后的回复。'
```

编辑会增加版本并撤销批准；支持 --text-file 读取 UTF-8 文件。`discard 编号 --revision 版本` 逻辑清除该项原文和草稿。回复须单行、最长 300 字符，自动保留 [AI] 标识。

以下是**真正发送前的人工操作步骤**，不属于默认启动：

1. 用 show 核对目标和完整文字，批准确切版本：`review approve 编号 --revision 版本 --target '高雅抖音(9)'`。批准不发送，10 分钟过期。
2. `review check 编号` 只读检查目标、源文字和空输入框。源文字当前不可读或不唯一时拒绝，请手动定位到源消息。
3. 在自用 config.local.json 中设置 dry_run=false，然后执行 `uv run --locked python -m group_friend review --config config.local.json send 编号 --revision 版本`。示例配置保持 dry_run=true。

发送会原子占用队列项，再次验证窗口与进程启动时间、群标题、源文字、焦点和完整草稿，最多按一次 Enter。只有输入框清空且消息区对应文字数量增加一条，才标记 visible_sent；这不是服务端回执。

出现 sending/unknown 时，全队列停止继续发送，重启或重绑不能绕过。人工检查群消息和草稿后，使用 `review resolve 编号 --outcome visible_sent|not_sent --note '核对说明'` 记录结果，不重试同一项。疑似崩溃的 sending 至少等 90 秒再处理。不要清空数据库或回执。

审核库会保存选中原文、草稿和绑定；`review purge --days 7` 逻辑删除超过七天的终态项，保留待审核、已批准和未确认记录。冷却/小时/每日额度按整个队列统计。

## 长期记忆

库为 state/long_term_memory.sqlite3，重启后可检索。默认主文件上限 **256 MiB**，按需占用，一半作为文字和元数据预算，另一半留给索引；事务日志、压缩临时文件、审核库不计入此上限。

```powershell
uv run --locked python -m group_friend memory stats
uv run --locked python -m group_friend memory spaces
uv run --locked python -m group_friend memory resize --capacity-mb 512
uv run --locked python -m group_friend memory retention --days 180
```

--capacity-mb 按 MiB 计算。已有库的容量与期限通过命令调整，配置字段仅初始化新库。缩容时先压缩；有效内容仍放不下则拒绝，不自动删数据来强行缩容。

当前群空间为 `2d3c34df-6e84-48ac-ac34-eb035fc447dd`，所有空间共享总容量、按空间隔离检索：

```powershell
uv run --locked python -m group_friend memory search --space 2d3c34df-6e84-48ac-ac34-eb035fc447dd --query '抖音' --show-text
uv run --locked python -m group_friend memory add --space 2d3c34df-6e84-48ac-ac34-eb035fc447dd --text '群话题记录：大家聊过抖音冷门推荐。' --pin
```

到期记录自动清理，容量满时淘汰最旧未置顶项；置顶仍遵守期限，置顶填满时拒绝新增。保留期限从写入时间计算，默认 180 天。prune 清过期记录，compact 回收空闲页。

`list --space` 默认只显示元数据；--show-text 显示正文。`show/forget/pin/unpin --space 空间 --id 记录` 管理单条，`clear --space 空间` 清空指定空间。删除会清理索引，属于逻辑删除，不承诺磁盘安全擦除。`memory_enabled=false` 可关闭自动记忆，已有库保留。

只有过滤后的输入、手动选择的源文字和确认发送的回复会被记住，草稿不存成已发送发言。当前使用中英文关键词检索，非向量语义检索；每次最多 6 条/2000 字符、最多占总上下文的一半，总预算仍为 context_chars（默认 6000）。相关片段随请求发给模型，并作为 user 角色的不可信历史数据处理。昵称只是标签，旧说法不是已核实事实。

## 登录与凭据

用户已完成专属账号登录。登录、切换账号、验证码均手动处理；程序不再自动读取凭据填表或提交登录。旧自动登录入口只返回 manual_login_required。

抖音账号密码已保存在当前 Windows 用户的凭据管理器，目标 `pj_DeepSeek/douyin/bot`，不写入项目文件或记忆。不要重复索取或在聊天中提供密码。确需维护时：

```powershell
uv run --locked python -m group_friend credentials status
uv run --locked python -m group_friend credentials set --replace --phone
```

第二条仅在用户主动更新凭据时使用，隐藏录入手机号和密码；不会登录。没有密码命令参数或显示密码的 CLI，非交互/无法隐藏输入时停止。credentials delete 只删除本项目凭据，不影响客户端登录状态。

## 配置与暂停

.env 中已配置 DeepSeek Key；不要覆盖、输出或分享。模型客户端默认 api.deepseek.com / deepseek-flash，模型名可配置；后续变更时再核实服务文档。环境变量优先于 .env。

config.example.json 中 local-test/bot-local 是控制台标识，不是抖音群/账号 ID。默认 /ai 或 /ai 空格内容触发，未实现 @ 识别。控制台仅接受 dry_run=true，/pause、/resume、/quit 控制本地循环。

创建 state/PAUSE 可暂停生成关键阶段及发送、停止观察；恢复前确认目标和队列状态。切勿删除整个 state 来解除暂停或发送不确定性。头像/视频/账号卡片的文字不能当作群发言者或正文。

`.env`、state/、diagnostics/、config.local.json、.venv/、.uv-cache/ 均已忽略。历史说明保存在 [docs/archive](docs/archive/)，当前以本文件与 CODEX_HANDOFF 为准。
