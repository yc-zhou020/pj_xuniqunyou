# DeepSeek 抖音虚拟群友：当前开发交接

最后核对：2026-11-03，用户时区 America/New_York。

## 最新：持续等待的输入栏移位修复及后台窗口管理

- 用户确认 19:26 单次真实回复成功，日志 visible_sent/confirmed。该单次模型→自动发送链路已验收，持续运行尚未验收。
- 19:27 持续模式一直 waiting_for_visible_private_target。只读诊断发现窗口实际可见、Nefariousa 私聊仍选中，但首次发送后招呼面板消失，输入栏由 main/3/1/0 移到 main/2/1/0。已同步修复 Python 和原生识别，并保留严格私聊标题/结构校验；不是窗口实际不可见。等待日志现附 reason。
- 用户新增要求解决不能最小化的缺陷，提出隐藏桌面。已核对 Windows 隐藏桌面只有活动桌面接收系统输入；当前 SendInput 路径不能直接当成无头后台。系统为 Windows 11 家庭版，无现成 Hyper-V 模块/服务。
- 曾按后台控制目标重启一次抖音，尝试标准 loopback Electron 调试端口及后台 renderer 参数；客户端没有开放端口，未改客户端内部代码/资料，也未退出账号或自动登录。Playwright 实验依赖已用 uv remove 移除，正常依赖恢复为空。失败实验脚本归档在忽略的 diagnostics，不是正常启动入口。
- 已恢复 Nefariousa 私聊，备份旧绑定到 `state/douyin-nefariousa-test.before-background-probe.<旧session>.json`，更新当前测试绑定。新窗口快照 pid 25760、handle 395508、进程启动 2026-11-04T00:38:04.7823504Z（用户当地仍 11月3日19:38），仅作快照，实际从绑定读取。群绑定和长期记忆未改。当前读到 8 条文本，7 incoming/1 outgoing，已有自身回复方向可识别。
- 最终实现 `group_friend/window_manager.py`，默认 window_policy=restore_background（本机配置已显式写入）：在采集前核对窗口 PID、FILETIME 精确启动时间和标题，将最小化/隐藏窗口用 SW_SHOWNOACTIVATE 恢复并置底，不抢焦点；发送后在未被用户主动切走的情况下归还原窗口焦点。焦点归还失败不会把已确认发送误记为 unknown。
- 这不是完全隐藏桌面或无头执行：内部维持抖音未最小化，发送可能短暂置前。不能锁屏或移到其他虚拟桌面，程序不会自动切换桌面；目标私聊需保持选中。passive 可关闭自动窗口管理。
- 新增 `--inspect`：禁用模型及发送，只验证窗口管理和基线，不创建/修改任务状态。真实结果 inspection_ready，8条/7来信/1发信。实际最小化→后台恢复验证成功：was_minimized=true、restored_background=true、foreground_unchanged=true，8条消息和签名均不变。当前私聊原生 CheckOnly 为 ready。
- **121 项测试通过**，uv 锁文件匹配。没有启动自动发送或后台常驻任务，没有进行新的模型调用或发送。下一步用户自行用 `uv run --locked python run_friend.py --config autonomous.local.json --reply-latest` 验证新窗口管理模式下持续收发；无 --once。不要把最小化恢复/只读检查称为新模式完整收发已验收。

## 最新：19:22 history_or_ambiguous_change 的滚动容器修复

- 用户持续运行在 baseline=5 后停止，未进入生成/发送。只读诊断发现消息区新增了一层滚动容器，旧解析器把 6 条实际文字读成 0 条，触发保守差分停止。新来信已经可读，不需要再要求对方重复发测试消息。
- 已添加 private_message_base：只在唯一分组子节点与外层边界一致、其下存在直接时间分隔行及消息行时展开已测量的容器。Python 文字提取/方向推断和原生 Get-Context 同步适配。单条消息不会被任意展开，历史/编辑歧义仍拒绝。
- 历史真实快照回放：旧快照 5 条 → 当前容器快照 6 条，得到 1 条 incoming 候选。当前真实 CheckOnly 为 ready。此验证没有调用 API、聚焦、输入或点击发送。
- 新增差分失败诊断 observation_changed，记录前后条数和方向分布，不记录原文。新增容器切换、同时追加来信、单气泡不误解包及真实历史编辑拒绝等测试；**105 项测试通过**，锁文件匹配。
- 最新快照 diagnostics/private-tracker-stop.json。未修改任何任务回执，也未启动真实发送。用户可直接自行执行单次 --once --reply-latest --retry-unsent，处理当前已收到的新来信；完整真实发送仍待验收。

## 最新：19:12 私聊发送 unknown、用户确认未发出

- 用户自行终端测试到 generated 后返回 unknown/auto_send_unknown，随后明确回复“未发出”。只读采集及截图确认：私聊消息区没有生成回复，输入框仍留有完整 AI 草稿；草稿哈希与该任务 reply_hash 一致。
- 旧版本吞掉了原生异常的具体阶段，因此无法追溯断言是 Enter 行为、焦点变化或哪一次复核失败。没有重试或点击发送，没有清空草稿。
- 已修改私聊发送为单次点击实际测量的输入栏右侧发送按钮，不使用 Enter/点击回退。原生助手再次校验可点击角色、默认动作、可用状态、前台窗口、控件几何与点下窗口归属，拒绝遮挡；仍须先校验完整草稿及源消息。群审核发送路径保留原单次 Enter。
- 新增只读 InspectOnly，允许在有草稿时验证 draft_matches/composer_focused/send_button_available，不进行聚焦/输入/发送。当前实测 inspected、draft_matches=true、send_button_available=true。新辅助 C# 已编译，实际点击发送尚未验收。
- 新增固定 stage/reason 诊断，经 Python 白名单筛选写入日志，不暴露 UI 原文或异常正文。**100 项测试通过**，uv 锁文件匹配。
- 已依据用户明确“未发出”及只读证据，通过正式 --resolve 将该 unknown 记录为人工 not_sent，说明写入日志；未删除状态、未自动重试。该任务保持终态。旧草稿仍保留，需要用户手动清空，不能按 Enter 发出。
- 下一次由用户自行启动持续运行模式，Nefariousa 发新文字后测试，避免复用这条已经人工解决的任务。没有常驻进程由 Codex 启动，没有实际发送。
- 当前证据：diagnostics/private-after-unknown.json、diagnostics/private-test-window.png；日志 state/autonomous.log 含 manually_resolved 记录。不要输出整段聊天或秘密。

## 最新修复：用户终端测试的 incoming_source_changed

- 用户明确选择自行从终端启动测试；Codex 负责修复和只读检查，不替用户启动真实发送。
- 用户 19:08:35（America/New_York）的单次测试日志显示 baseline=5、generated，随后 incoming_source_changed。数据库该任务为 error，未进入 sending。当前只读采集确认最新源文字在私聊中出现两次且方向都为 incoming；旧代码按全文唯一性拒绝了重复来信，不是 API 生成失败。
- 已改为按完整可见消息序列及所选位置校验重复文字。Python 和原生后端均复核原序列仍是当前序列前缀，且所选位置方向为来信；不把本地位置当作平台消息 ID。当前真实私聊的两条同文经带顺序锚点的 CheckOnly 检查为 ready。没有执行输入或发送。
- 独立运行器允许在完整旧序列不变时追加同文；其他历史/编辑歧义仍停止。单次测试去重键包含整个可见序列指纹，区分不同的同文出现。旧 error 记录原样保留。
- 新选项 `--retry-unsent` 仅与 `--once --reply-latest` 配合，显式重新处理进入发送前的 error；绝不重试 visible_sent、sending 或 unknown。
- **96 项测试通过**，uv 锁文件匹配。最新只读快照在 diagnostics/private-source-change.json；完整真实发送仍待用户终端复测，不能宣称闭环完成。

## 最新目标：独立白名单私聊脚本（优先于后文旧授权描述）

- 用户最新要求推进到“启动一个 Python 脚本，完成对白名单用户的回复，运行时不需要 Codex 介入”，允许记录 log。新目标是独立自动私聊运行，不再设计为逐条让 Codex 生成/审核/发送。当前白名单仅使用此前指定的 Nefariousa；禁止向群回复仍有效。
- 已实现 `run_friend.py` 和 `group_friend/autonomous.py`：白名单绑定核对、可见私聊观测、头像左右位置方向推断、历史基线、保守差分、短期会话上下文、DeepSeek 调用、自动原生发送、暂停、单实例锁、轮转日志、持久回执、未知发送阻断及人工 resolve。没有编造平台账号/消息 ID。
- 程序自身不调用 Codex。当前只能处理已展开且未最小化的白名单私聊，不会自动打开联系人。恢复可见目标重新建立基线，不补发不可见期间的消息。昵称白名单不等于平台 ID 白名单，不能鉴别同名账号或同进程账号切换。发送自身文本带 [AI] 并过滤此标识。
- 本机配置已准备：`autonomous.local.json`（Nefariousa、api.deepseek.com、5 秒轮询、正文日志关闭）和 `config.autonomous.local.json`（dry_run=false、memory_enabled=false、5 秒冷却、每小时30/每天100）。均已忽略。原群绑定、原群记忆及旧回执没有改写。源码示例保持 dry_run=true。
- auto_jobs 默认写入 `state/replies.sqlite3`，只存文字哈希与状态；与人工审核发送共享未决阻断和额度计数。CLI 两个发送入口共享 `state/autonomous.lock`。日志在 `state/autonomous.log`，轮转约 2 MB、3 个备份。不要删除状态绕过 unknown。
- **92 项测试通过**，`uv lock --check --offline` 通过。真实独立启动 `--mock --once` 成功建立 4 条私聊文字基线；私聊方向观测为 incoming，新增原生 require_incoming CheckOnly 实测 ready。
- 真实测试命令 `uv run --locked python run_friend.py --config autonomous.local.json --once --reply-latest` 被自动审批拒绝，命令没有执行，未调用真实模型也未发送。理由：需要明确批准私聊正文提交到 api.deepseek.com 及随后无人工确认的自动发送。**目前没有常驻自动回复进程**。不能改用其他方式来绕过此拒绝。
- 所有实现及只读/离线检查已经完成。下一步仅需用户对具体范围作一次明确授权：允许脚本把白名单 Nefariousa 的私聊文字提交 api.deepseek.com，并自动向该私聊发送生成的 AI 回复，无需逐条确认，不发群。获准后可重新进行真实单次验收并启动持续运行；仍不得把未实际验证的链路称为完成。
- 使用、日志、停止和未知回执处理见 README“独立白名单私聊运行”。`--status` 不修改回执；`--resolve` 要求停止运行、至少90秒、明确结果及核对说明，不会重发。

最新私聊测试（优先于下方群会话快照）：

- 用户要求测试改为 **Nefariousa 私聊，禁止在群里回复**，并确认对方已发测试消息。已在截图和 MSAA 中读到三条新文字，最新一条为“消息测试”。
- 单独创建 `state/douyin-nefariousa-test.json`，没有改写原群绑定；快照 `diagnostics/nefariousa-review-test-01.json`，最新消息 index=3。测试配置 `diagnostics/config-nefariousa-test.json` 关闭记忆，保留 dry_run=true，避免使用群记忆。未创建新草稿或批准项，未输入或发送消息。
- 已支持实际观测到的内嵌私聊布局：标题只在当前私聊标题栏匹配，不把联系人列表中的同名标签当作当前目标；可处理未读数字使标题位置变化；私聊 sender_id/is_self 等继续未知。Python 与原生 CheckOnly 均验证真实窗口通过，返回 ready。
- **71 项单元测试通过**，uv 锁文件匹配。新私聊发送路径仅验证到 CheckOnly，没有实际发送验收。
- 窗口最小化时曾读到旧的可访问性内容；恢复窗口后才读到新消息。不应把这次采集称为稳定的后台实时接收。
- 真实 DeepSeek 草稿调用被自动审批拒绝，理由为没有明确批准将这段私聊正文发送至 DeepSeek API。该请求未执行；不能通过其他执行方式绕过。已核对目的地 `https://api.deepseek.com`、模型 deepseek-flash；待用户批准仅提交“消息测试”后才能继续真实模型调用。回复完整文字生成后，真实抖音发送仍需按原约束批准。
- 只读预检成功前后均未发送内容；群观察/回复没有启动，不能将开发授权扩大成持续自动发送授权。

本轮续开发更新（优先于下方旧状态快照）：

- 已实现 `review snapshot --output diagnostics/新文件.json [--show-text]`，按当前绑定采集并显示可选索引，不调用模型、不发送、不覆盖已有文件，只保存所选窗口。
- `review generate` 现在只接受此入口生成的审核快照，校验完整本地绑定及快照中的进程/启动时间；原始 MSAA 诊断快照仍供 douyin inspect/replay 使用。生成途中若重绑则拒绝入队。未提供可靠原生账号/群 ID，快照也不是实时新消息证明。
- 最新检查为 **66 项测试通过**，`uv lock --check --offline` 通过；本轮没有调用真实模型或发送消息。
- 原绑定已失效：沙箱内外 inspect 均返回 window_not_unique。沙箱外只读诊断发现新进程 pid 9952、窗口 133650、启动时间 2026-11-03T23:29:06.7512829Z，标题“抖音”；按原绑定群标题解析返回 group_heading_changed。未自动重绑、未切换界面、未启动观察任务。以上新句柄同样仅为临时快照。
- 记忆仍为 1 条/348 字节计费，旧队列样例仍 pending。恢复真实窗口验收时，先由用户打开目标群，再只读确认、重新 bind 并显式关联记忆；不要把新主窗口直接当作目标群。

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

交接原检查为 58 项；续开发后为 **66 项单元测试全部通过**，uv 锁文件匹配。详见 [验证记录](docs/VERIFICATION.md)。

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

1. “当前会话快照 → 选择源消息 → 审核草稿”的 CLI 入口已实现并完成离线测试，使用方式见 README。目标群恢复可读后，验证新入口的真实窗口采集及待审核生成；不能将离线测试称为真实窗口验收。
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
