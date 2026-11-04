# DeepSeek 抖音虚拟群友

通过 DeepSeek 在真实抖音群中生成回复的本地项目，当前提供 Windows 只读观察、手动历史回放、回复审核队列和长期记忆。

已实现独立 Python 白名单私聊运行器，用户已确认一次真实模型生成及自动发送成功。持续模式已修复输入框移位识别，并加入最小化后的后台恢复及发送后的焦点归还；新窗口管理模式的持续收发仍需用户终端验证。接续开发先读 [CODEX_HANDOFF.md](CODEX_HANDOFF.md)，最新实测见 [docs/VERIFICATION.md](docs/VERIFICATION.md)。

## 独立白名单私聊运行

入口是 `run_friend.py`，运行期间不调用 Codex，也不依赖本聊天。Python 直接采集 Windows MSAA、调用 DeepSeek，并通过本地 PowerShell 原生助手发送；不再逐条创建人工审核项。登录和账号切换仍由用户手动完成。

本机已准备 `autonomous.local.json` 和 `config.autonomous.local.json`：白名单只有 Nefariousa，使用独立私聊绑定，关闭长期记忆，目标 API 为 api.deepseek.com。本地回复配置 dry_run=false，代表启动后可以真实自动发送。用户选择自行从终端启动；Codex 此后只修改代码和执行只读验证，不替用户启动真实发送。

用户前台运行命令（在项目目录中执行）：

```powershell
uv run --locked python run_friend.py --config autonomous.local.json
```

默认先建立历史基线，之后只处理观测到的新增私聊文字。不会回复启动时已有历史；显式 `--reply-latest --once` 才会尝试对最新可见来信做一次完整测试。处理记录同时包含可见消息序列的指纹，以区分相同文字在不同位置的出现；发送前由 Python 和原生后端分别校验已观测序列仍为当前序列前缀，以及所选位置仍是来信。启动测试时若消息序列未变，已处理的任务不会重做。原生平台消息 ID 尚未取得，历史翻页、编辑或无法确定的变化仍会停止，不声称完全可靠的消息去重。

重复测试文字可以正常处理：只要此前观测到的完整消息序列保持为前缀，追加同文被视为新的本地观测；这不等于获得平台消息 ID。对进入发送前就失败的 error，可显式重试：

```powershell
uv run --locked python run_friend.py --config autonomous.local.json --once --reply-latest --retry-unsent
```

此选项只允许重新处理发送前失败的任务，不改变已发送、sending 或 unknown 状态，也不会清空任何回执。

已适配实际观察到的两种私聊列表：直接包含消息行，或外面多一层滚动容器。识别容器时核对其唯一子节点、边界和直接时间分隔行，不会随意把单条消息气泡展开成列表；Python 读取及原生发送复核使用相同规则。容器出现或消失而消息未变时不触发回复，容器变化同时新增文字时仍可识别新增来信。其他差分歧义会在 observation_changed 日志记录前后条数及方向分布后停止，不输出原始消息。

需要保持目标私聊处于选中状态。默认 `window_policy="restore_background"`：你可以最小化抖音，脚本在下次采集前验证窗口进程及精确启动时间，将其恢复到其他窗口后方，使用不激活窗口的方式，不要求手动恢复；发送时可能短暂置前，发送后若仍在抖音前台则归还原应用的焦点。如果用户在发送期间自行切换应用，程序不会强行切回旧应用。此模式内部仍保持抖音未最小化，**不是隐藏桌面、无头或全程不影响前台的执行**。锁屏或把窗口移到另一个 Windows 虚拟桌面不在支持范围，脚本不会自动切换桌面。

设置 `window_policy="passive"` 可保留旧的手动窗口管理方式。当前版本不自动打开其他联系人，也不支持多个未打开会话的后台收信；切到群或其他联系人时只等待。等待日志新增 reason，区分 window_not_visible、not_whitelisted_private_pane、window_on_other_desktop。仅因最小化而自动恢复时保留原差分基线；真正离开目标私聊或切到其他桌面后重新回到目标时，会建立新基线，不补回离开期间的消息。

白名单依据本地绑定和当前私聊标题栏，**不是平台用户 ID 白名单，无法识别同名账号或同进程内切换账号**；切账号前应停止程序，重新核对绑定。首次发送后“打个招呼”面板消失会使输入栏位置改变，已适配两种实际观测布局，读取和原生发送前复核采用一致规则。

可先运行一次不调用模型、不发送且不修改任务回执的窗口检查：

```powershell
uv run --locked python run_friend.py --config autonomous.local.json --inspect
```

检查通过会输出 inspection_ready。持续运行若也要处理启动时当前最后一条来信，可使用 `--reply-latest`（不要加 --once）：

```powershell
uv run --locked python run_friend.py --config autonomous.local.json --reply-latest
```

消息的来信/发信方向根据头像在消息区左右位置推断；未知方向不回复，不向核心 Engine 填造 sender_id/is_self/timestamp。自身输出统一带 [AI]，即使视觉方向推断失败也不会再次处理带此标识的消息。只提交当前选择的来信和本进程内该会话的短期对话，不使用原群长期记忆。限频时跳过该条观测并记录 rate_limited，不延迟补发。

私聊发送现使用已测量的输入栏右侧发送按钮：重新检查目标、源消息、完整草稿和按钮位置后，单次点击，不再依赖 Enter；按钮被遮挡或窗口焦点变化时拒绝。群审核后端仍保留原单次 Enter 路径。失败日志增加 stage/reason 固定代码，以区分 typing、validating_draft、invoking_send、confirming_send 等阶段，不输出原始异常文本。

日志默认在 `state/autonomous.log`，单文件约 2 MB、保留 3 个备份；默认只记录目标、状态、条数和错误类别。`autonomous.local.json` 中 `log_text=true` 可选择记录选中正文与生成回复。数据库 auto_jobs 默认与人工审核项共享 `state/replies.sqlite3`，保存状态和文字哈希，不保存正文。两个发送入口共享互斥锁、未决发送阻断和额度计数；不要为规避未知回执更换数据库。

停止：前台 Ctrl+C，或另一个 PowerShell 窗口执行 `New-Item state/PAUSE -ItemType File -Force`。后者会让程序停止，恢复前核对状态并移除该暂停文件，再重新运行。正常运行只需维护 Python 进程和抖音客户端，无需保持 Codex 打开。

查看任务状态：

```powershell
uv run --locked python run_friend.py --status
Get-Content state/autonomous.log -Tail 30
```

发送结果为 unknown 或进程在 sending 阶段中断时，全队列停止；程序不会自动重试。停止程序并人工核对私聊后，至少等 90 秒再记录处理结果：

```powershell
uv run --locked python run_friend.py --resolve '任务编号' --outcome not_sent --note '已核对私聊，未发送且输入框已清空'
```

确认已发送则 outcome 选 visible_sent。这是记录人工结论，不会重发该任务。保留数据库和回执。

用户本机 19:12 的测试遗留完整草稿，用户确认未发出，已记录为 not_sent，原任务不会重试。旧草稿随后由用户处理；19:26 单次测试返回 visible_sent/confirmed，用户明确确认回复成功。19:27 的持续等待则定位为首次发送后输入栏移位导致的识别遗漏，已修复。此前失败日志没有记录阶段，不能据此断言是 Enter 行为或焦点检查的哪一步失败。

为评估真正最小化或隐藏桌面执行，曾进行一次标准 Electron 本机调试端口启动实验；本客户端未开放该端口，没有修改客户端内部代码或登录资料。实验后已恢复 Nefariousa 私聊、备份并更新测试绑定，调试依赖已移除，正常运行仍无第三方 Python 依赖。最终采用上述后台自动恢复方案，不宣称完全无前台交互。

新环境先用 `autonomous.example.json` 和 `config.autonomous.example.json`（dry_run=true）检查。白名单的每个条目都需独立绑定文件，且 name 必须与绑定标题完全一致；示例中的本机文件路径需按实际创建。可用以下命令只读建立基线，不调用 API、不发送：

```powershell
uv run --locked python run_friend.py --config autonomous.example.json --mock --once
```

## 当前会话

截至 2026-11-03：用户已手动登录专属账号，目标群为“高雅抖音”，实际标题 `高雅抖音(9)`。已重新绑定并关联原长期记忆空间，最后采集到 9 条文字。没有后台持续观察任务，state/PAUSE 不存在，默认 dry_run=true。本地旧队列样例属于旧绑定，需重新生成和审核。

续开发复核：上述绑定已因客户端进程/窗口变化失效，新窗口中也未找到原群标题。尚未重绑；使用下方采集流程前，需手动打开目标群，再只读诊断并绑定当前窗口。新审核快照入口已通过离线测试，真实窗口验收仍待进行。

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

需要手动生成回复时，使用当前绑定保存审核快照并检查文字索引，无需复制窗口句柄和群人数：

```powershell
uv run --locked python -m group_friend review snapshot --output .\diagnostics\current-review.json --show-text
```

快照包含当前绑定窗口的可见消息和昵称，存于已忽略的 diagnostics；不会覆盖已有文件，请为下一次采集换一个文件名。默认只打印索引和统计，--show-text 才打印文字和昵称。索引从 0 开始，生成时使用同一文件可避免界面变化使索引指向其他消息。采集不调用模型。

```powershell
$messageIndex = 0
uv run --locked python -m group_friend review generate --snapshot .\diagnostics\current-review.json --index $messageIndex --mock
uv run --locked python -m group_friend review list
```

去掉 --mock 会调用本地配置的 DeepSeek，提交所选的一条文字及相关记忆，产生 API 费用；结果只加入 pending 队列。新建条目不会批准或发送。

审核快照记录本地 session_id、进程及启动时间、窗口和群标题。generate 校验这些字段与当前绑定一致；旧会话快照及原始 MSAA 诊断快照不能用于新建审核草稿，应重新运行 review snapshot。生成期间绑定变化也会拒绝入队。该校验不提供原生账号或群 ID，也不证明快照仍是最新画面；发送前仍需独立检查源文字和目标。原始诊断快照仍可用于 douyin inspect/replay 的历史调试。

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
