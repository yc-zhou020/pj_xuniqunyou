# DeepSeek 抖音私聊助手：开发交接

## Firefox 窗口显示

- 当前 `browser.local.json` 设置 `headless=false`，主程序正常运行时显示 Firefox 窗口；现有 BrowserSession 已支持该配置，无需额外启动参数。设为 `true` 可恢复无界面运行；修改配置后重新启动生效。
- 显示窗口需要图形桌面环境。用户要求本次不做测试，未启动主程序、浏览器或任何测试。

## 当前路线

- Python 直接调用 DeepSeek，Playwright/Firefox 操作抖音网页版，结构化文本快照；固定 5 秒采样、最小发送间隔 5 秒，不使用 Node 桥接。
- 用户最新明确授权“高雅抖音”群内自动回复及其全部成员私聊。当前 9 位成员中 test 为登录账号本人，私聊停用，其余 8 位及群会话启用；不自动授权未来新成员。
- 登录、切换账号、验证码和二次验证由用户手动完成，不恢复自动登录或读取密码填表。
- 使用 uv 管理依赖，脚本运行命令统一 `uv run 文件名.py`，依赖安装使用 `uv sync --locked`。日常真实回复启动：`uv run run_friend.py --live`，不加 `--live` 时遵循回复配置的 dry_run，默认配置 `browser.local.json`。
- `browser.local.json` 的 `reply_config` 可继续指向原来的 `config.autonomous.local.json`；新安装使用 `config.local.json`。不覆盖已有配置。

## 回复不再添加身份前缀

- 用户要求移除 `[AI]` 表示头，保留正文 300 字符上限。提示词、模拟模型和校验器均不再添加前缀；模型输出若残留旧前缀则先剥离，再按完整 300 字符截断。
- 待发送 generated 草稿在发送前剥离旧前缀，不重新调用模型；sending/unknown、历史回执和历史聊天不改写。
- 去掉按来信 `[AI]` 前缀过滤，仍以消息方向、原生发送者和白名单防止自身消息触发。直接询问身份仍如实回答。
- 字符预算不再预留标记的 5 字符；新增 300 字正文、旧草稿、未决文本不改写以及普通来信含标记的回归。

## 群消息未回复的采样阻断修复

- 日志证据：高雅抖音及土嗨天王游标停在 22:36:38，之后私聊仍正常生成发送，群没有任务。反复历史异常发生在排在群之前的土嗨天王。
- 只读页面核验：土嗨天王同一条原生消息从 unsupported 加载为 sticker，文字均为空，但旧指纹改变；异常原先会终止整个采样循环，导致后面的群一直未采集。
- 改为逐目标捕获 ContinuityError，日志带 target，其他会话继续；单次运行同样处理。
- 非文字消息按原生 ID+方向建立观察指纹，原消息 key 不变。新增 browser_observation_cursors 只存哈希及一致性摘要，与原游标同一事务更新，不存聊天正文。旧游标仅允许通过完全匹配的已接受快照升级。
- 源消息定位也容忍同一原生媒体的加载变化，但待回复文字内容变化仍拒绝，缺少原生 ID 的媒体变化仍拒绝。
- 真实页面数据在临时库验证：土嗨天王 0 个新任务；群两条新文字形成 1 个 pending 任务，baseline 未重建。生产库只读，未调用模型或发送。119 项回归通过。

## 当前关键词召回与群回复

- `memory_recall.py` 使用最近 6 条文字上下文匹配当前用户的离页长期事件/人工笔记，模糊下限 0.8；命中后一次随机值 `<0.33` 才注入整个记忆块。未命中不抽样、不兜底无关事件；拒绝时仍正常生成回复。日志只含统计，不含聊天正文。当前本地配置已写入参数，1 GB 容量保留。
- 用户明确要求同时启用群自动回复。`browser.local.json` 新增 `group_whitelist`，高雅抖音 ID `7516934184514961957`、9 位原生成员；原私聊白名单扩展为 9 人，其中本人 test 标为 is_self 且 enabled=false；有效私聊 8 人。
- `owner_user_id=2785538148432986` 从当前页面 SDK `option.userId` 核验；登录账号变化时核验阻断，不凭昵称识别本人。
- 私聊可用抖音号+sec_uid 或原生 user_id+sec_uid 核验；尚未出现的私聊跳过，不阻断其他采样。群按原生群 ID 绑定，消息逐气泡读取原生发送者，缺失身份拒绝采集。
- 群任务按连续发言人分批，模型保留发送者；来源成员必须在群 members 名单内。群成员记忆使用初始化时的群/成员目录，与私聊隔离，召回概率规则共用。
- 新增 sender 字段时 message.key 明确排除它们，保持旧私聊游标与回执指纹不变。群任务 target 使用 group: 前缀，不伪装成用户 sec_uid。
- 保留原有唯一按钮点击、精确草稿、人工接管取消、配额和全局未知发送阻断。当前开发仅真实只读核验了 3 个已有私聊与群的 11 条原生消息身份，没有实网群发送；发送由本地 Firefox fixture 与状态回归验证，仍待用户实测。
- 详见 `docs/MEMORY.md` 和 `docs/WHITELIST.md`。

## 群成员记忆初始化

- 用户指定从抖音页面直接采集“高雅抖音”群，已核验平台群 ID `7516934184514961957`、成员总数 9。
- 入口：`uv run scripts/init_group_memory.py --group-title "高雅抖音" --group-id 7516934184514961957`。使用原 Firefox 登录资料及浏览器锁，只读，不启用群回复或修改私聊白名单/回执。
- 原生 `deepseek-flash` 接收成员卡片和 6 个聊天视窗 PNG，未经过 OCR。已真实完成一次初始化：9 份成员 profile，6 人有视觉观测；16 条未能确定的信息留在报告中，观测全部标记待核对。
- 路径：`state/memory/groups/d790cd572545949d13d53066/README.md`；报告 `INITIALIZATION.md`；来源截图和模型缓存位于 `sources/`。顶层记忆索引也已链接。
- 前端实测经验：群标题带 `(9)`；群成员卡片会异步重排，必须按平台 ID 固定定位而非 nth；名单首次加载未完成时需要等待；列表使用反向滚动，允许负 scrollTop；头部菜单不能单靠再次点击关闭，应恢复头部焦点后 Escape，并确认成员面板隐藏。
- 完整成员数无法确认时拒绝初始化。大群隐藏成员分页尚未实现；聊天只覆盖所采集的有限视窗。群记忆与私聊及其他群隔离，不声称模型观测是已验证事实。
- 相同采集复用已校验视觉缓存，更新自动区块而保留人工笔记。详见 `docs/GROUP_MEMORY.md`。

## 当前 Markdown 长期记忆

- 用户要求启用长期记忆并使用 `.md` 保存；已实现 `group_friend/markdown_memory.py` 并移除运行器的禁用阻断。当前本地 `memory_enabled=true`、`memory_path=state/memory`，索引和 Nefariousa 笔记已初始化，未回填旧聊天。
- persona 日常称呼统一为“陈”，保留陈昕晴/Nicky 身世；本地 self_identity 同步为陈。
- 生成阶段去重保存选中的来信、检索同一用户的 Markdown 记录与可编辑 profile；发送阶段只在网页确认且数据库回执完成后保存回复。dry-run/mock 不写长期记忆。
- 输入按用户数据注入，不能改变 persona；不把陈的角色自述自动归纳为事实。默认 6 条事件/2000 字符，180 天保留，256 MB 容量，人工笔记不自动删除。
- 记忆使用独立文件锁和原子写入；失败记录类型而非正文，不改变发送状态、不触发重发。旧 SQLite 记忆不会自动迁移或删除。
- 维护与检索限制见 `docs/MEMORY.md`。当前模块已完成离线集成验证，尚未执行本轮真实模型和发送验收。

## 项目目录整理

- 根目录保留主入口 `run_friend.py`、本地配置、`.env` 和交接说明。
- 人格文件移动到 `personas/`，当前回复配置已指向 `personas/NICKY_PERSONA.md`；默认人格为 `personas/default.txt`。文件内容未改写。
- 示例配置位于 `config/examples/browser.json` 与 `config/examples/reply.json`。准备脚本只创建缺失配置，不覆盖已有文件。
- 本地回归入口为 `uv run scripts/run_tests.py`，真实收发入口为 `uv run scripts/test_live_reply.py`，后者实现位于 `group_friend/live_test.py`。
- 浏览器、环境、缓存、登录资料及 state 目录保留。详细目录见 `docs/PROJECT_LAYOUT.md`。

## 本次阶段解耦

- 用户要求：操作优先 Python，脚本使用 `uv run 文件名.py`；仅网页 DOM/React 读取保留必要 JavaScript。
- 默认运行器分别启动采集、模型生成、发送三个异步任务。`--stage receive|generate|send` 可单独执行，均支持 `--once`、`--duration`。
- 采集写入 SQLite `pending` 快照；`reply_worker.py` 不访问浏览器，只生成并持久化 `generated`；发送只处理已生成回复，不调用模型、不读取 persona 或模型 Key。
- 生成进程使用 `state/model.lock`，可与单独采集或发送并行；采集和发送仍共享 Firefox profile，使用原有浏览器锁互斥。默认 all 持有两个锁。
- 各阶段只恢复自己负责的中断状态；同一目标按入队顺序发送，人工接管、源消息核验、配额、未知发送阻断保留。dry-run/mock 结果终结，防止后续真实发送补发。
- 测试入口 `uv run scripts/run_tests.py`；本次验证结果见 `docs/VERIFICATION.md`。

## 本地真实收发测试与 CI 清理

- 已按用户要求删除 `.github/workflows` 及对应文档说明；保留本地 Python 回归测试。
- `uv run scripts/test_live_reply.py` 为显式实网测试：真实模型、真实发送、只处理白名单新消息，默认 180 秒；至少一次可见发送且无 unknown 才通过。
- 支持 `--prepare`（仅创建缺失配置）、`--install-browser`、`--check`。不覆盖配置或清除回执；有待处理任务时拒绝启动，避免测试发送旧队列。
- 本地白名单 Nefariousa 已绑定平台身份。用户完成真实测试后，本地数据库确认 4 条 visible_sent；真实中断后重启出现历史补载对齐问题，修复见下。

## 中断后重启的历史补载修复

- 用户报告第一次 Ctrl+C 后第二次不回复。日志显示第二次启动正常、无未知发送阻断，但持续 `observation_waiting/history_or_ambiguous_change`。
- 只读网页核验发现当前列表在保存的 19 条消息前补载了 1 条旧历史，后面有 2 条未采集来信；19 条消息的 native ID 和完整指纹均匹配。
- `history_alignment` 保留末条已知消息作为锚点，支持在前面补载历史。新消息从锚定窗口末端之后入队；源消息位置同步映射，缺失锚点和重复歧义仍阻断。
- 不删除数据库、不重置游标、不改写回执。新增历史补载、重启增量、源消息定位及歧义保护测试。用户已确认修复后 Ctrl+C 中断再启动能够正常运行。

## 实测经验落实到主程序

- 主程序已存在：`run_friend.py`；它、包入口和实网测试共享 `browser_runtime.py`，历史补载修复已经覆盖日常运行。
- 新增 `--live`，本次运行显式启用真实发送，不改本地 dry_run 配置；与 `--dry-run`、`--mock` 互斥。
- 启动显示运行模式与白名单；每个目标首次采样记录 `ready` 和恢复标识，实网测试复用同一就绪事件。
- 运行器在正常结束、取消和异常时统一记录停止统计；Ctrl+C 取消记录 `stopped/interrupted`，释放浏览器和数据库。
- 新增主运行器取消后重启集成测试：保留检查点、补载历史、处理新来信，再次重启无重复生成和发送；全部使用临时库、假页面和模型，不对外发送。

## 本次精简

用户要求删除不需要的文件。已移除 Windows MSAA/原生发送路线、PowerShell 助手、人工审核队列、长期记忆和凭据管理模块、对应测试及旧示例配置，删除过期文档归档并精简 README 和交接。

网页仍使用的进程锁、轮转日志和回复校验提取至 `runtime_support.py`。三个启动入口统一进入 `browser_runtime.py`。保留 `config.py` 旧配置字段以兼容已有本地配置，保留数据库旧表的未知发送阻断。

`group_friend/browser_reference/` 是现用网页读取和身份核验实现，必须保留 JS、选择器及 AGPL-3.0 LICENSE。用户已恢复 `example/`，它是供开发者参考的项目，必须保留；不参与运行不等于可以删除。

## Ubuntu 迁移

目标 Ubuntu 24.04 LTS，步骤见 [docs/UBUNTU.md](docs/UBUNTU.md)。使用 uv 重建 Python 环境、安装 Linux Firefox 及系统依赖；不复制 Windows `.venv` 和浏览器二进制。迁移时停止旧实例，保留本地配置、数据库和回执，在 Ubuntu 图形桌面手动重新登录。登录后无界面运行不需要桌面。

VS Code 解释器配置已使用跨平台虚拟环境目录。Firefox 本地测试使用 Playwright 提供的系统对应可执行文件路径，设置 `REQUIRE_BROWSER_TESTS=1` 时禁止因缺少浏览器而跳过。`runtime_support.py` 已包含 Linux `fcntl.flock` 锁实现。

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
