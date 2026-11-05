# DeepSeek 抖音私聊助手

独立 Python 程序，使用 Playwright/Firefox 操作抖音网页版，将结构化聊天快照直接交给 DeepSeek。只回复已授权的白名单私聊和群成员文字，运行时不需要 Codex。

## 安装与登录

Ubuntu 迁移步骤见 [docs/UBUNTU.md](docs/UBUNTU.md)，包含系统依赖、手动登录和状态迁移。以下命令在 Ubuntu 项目目录的终端中执行：

```bash
uv sync --locked
uv run scripts/test_live_reply.py --prepare
uv run scripts/test_live_reply.py --install-browser
uv run run_friend.py --login
```

已有配置和登录资料不要覆盖。将 DeepSeek Key 填入本地 `.env`。登录、验证码和安全验证由用户手动完成；打开目标私聊并完成抖音号及 sec_uid 绑定后，即可启动主程序。Firefox 是否显示窗口由 `browser.local.json` 的 `headless` 控制：`false` 显示窗口，`true` 无界面运行。

新环境默认 `dry_run=true`。日常真实回复使用 `--live`，只对本次运行启用发送，不改写本地配置；也可在回复配置中设置 `dry_run=false`。已有本地 `reply_config` 路径继续有效，无需重命名。

## 运行

主程序为 `run_friend.py`，持续采集并自动回复白名单私聊和已授权群聊：

```bash
uv run run_friend.py --live
```

当前本地配置为 `"headless": false`，运行主程序时会显示 Firefox 窗口，可在窗口中手动完成安全验证。显示窗口需要图形桌面环境；无桌面的服务器可将其改为 `true`。修改后需重新启动主程序。

回复正文最多 300 字符，不添加 `[AI]` 或其他自动身份前缀；直接询问身份时仍按 persona 如实回答。

不加 `--live` 时按回复配置的 `dry_run` 执行；启动日志会显示运行模式和白名单。`--live`、`--dry-run`、`--mock` 互斥。首次成功采样会记录 `ready` 和“采样就绪”，这时可以发送新消息验证。

Ctrl+C 停止后，重新执行同一命令即可恢复。主程序与实网测试共用运行器、持久化队列和历史对齐逻辑；网页重启后向前补载的旧历史不会作为新消息处理，已确认发送的消息不会重放。中断会记录 `stopped/interrupted` 及采样、确认发送数；若中断发生在点击发送附近，结果不明仍按未知发送处理，需要人工核对。

默认读取 `browser.local.json`，也可用 `--config` 指定。固定每 5 秒采样，发送至少间隔 5 秒，每小时 30 条、每天 100 条（示例配置）。采集、模型生成和发送由三个独立异步任务运行，通过 SQLite 队列衔接。模型在线程中运行；限额会延后任务，生成结果持久化，不重复付费生成。

也可分阶段运行，默认 `--stage all`：

```bash
uv run run_friend.py --stage receive
uv run run_friend.py --stage generate
uv run run_friend.py --stage send
```

- `receive`：只读取抖音并将新消息快照持久化为 `pending`，不需要模型 Key。
- `generate`：只消费 `pending`，调用 DeepSeek 并保存为 `generated`；不启动浏览器，也不读取网页。模型失败独立退避重试。
- `send`：只消费 `generated`，重新核验网页身份、源消息、人工回复和草稿后发送，不需要模型 Key、persona 文件或模型客户端。

各阶段支持 `--once` 和 `--duration`；`--once` 处理当前可执行任务后退出，不等待重试或发送限额。`--reply-latest` 只影响采集阶段。快照采用采集时上下文，发送前再次核验是否已有人工回复。同一联系人的回复按入队顺序发送。

生成阶段可与采集或发送阶段并行运行。采集与发送共用一个 Firefox 登录资料，单独启动时互斥；需要三步持续协作时直接使用默认启动命令。每类工作只允许一个实例，恢复状态时不会改写其他仍在运行的阶段。生成结果和发送回执均持久化，重启后继续；`--dry-run`、`--mock` 的生成结果会终结为 `dry_run`，以后启用真实发送也不会补发。

开发和维护优先使用 Python，脚本统一 `uv run 文件名.py`。保留 Playwright 网页 DOM/React 读取需要的少量 JavaScript，不引入 Node 或 shell 运行桥接。

首次启动建立历史基线，之后处理新来信；已有检查点则恢复增量和未完成任务。`--once --reply-latest` 可处理首次基线中的最新来信，已有检查点时不会重放已处理消息。

```bash
uv run run_friend.py --inspect
uv run run_friend.py --inspect --duration 60
uv run run_friend.py --status
tail -n 30 -f state/browser.log
```

`--inspect` 不调用模型、不发送、不创建回复任务，快照保存在 `state/browser-snapshots/`。`--dry-run` 调用模型但不发送；`--mock` 使用本地固定回复且不发送。Ctrl+C 停止，创建 `state/PAUSE` 也会停止工作；恢复前核对状态并移除暂停文件。

发送前核验平台身份、源消息和完整草稿，不覆盖人工草稿。只点击一次按钮，结果不明则标记 `unknown` 并阻止继续发送，重启不会重发。停止程序、人工核对并至少等待 90 秒后记录结论：

```bash
uv run run_friend.py --resolve '任务编号' --outcome not_sent --note '人工核对未发送'
```

确认已发送时用 `--outcome visible_sent`。该操作只记录结论，不重试任务。旧数据库中的未决发送仍会阻断运行；不要删除数据库或回执来绕过。

## 当前白名单

`browser.local.json` 中 `whitelist` 配置私聊，`group_whitelist` 单独配置群及允许回复的成员。当前已授权“高雅抖音”与该群 9 位成员，其中登录账号本人 `test` 不接收自身私信，其余 8 人可作为私聊对象。尚未出现在最近列表的私聊会等待，不阻断群和其他联系人。

群消息核验原生群 ID、成员 ID 和消息 ID，首次建立历史基线后才处理新文字来信；只回复 `members` 中的成员，不自动授权以后入群的用户。群内同样执行全局发送间隔、按群配额、人工接管取消和未知发送阻断。详见 [docs/WHITELIST.md](docs/WHITELIST.md)。

## 长期记忆

当前本地配置已启用长期记忆，使用 Markdown 保存。打开 [记忆索引](state/memory/README.md) 可找到每位白名单用户的 `profile.md` 长期笔记和 `events/` 对话记录；`profile.md` 可以手动编辑。

生成阶段保存新来信，发送阶段只保存网页确认发送成功的回复；重启会去重恢复。模型请求前使用最近 6 条文字消息做关键词模糊匹配；有匹配且一次随机数小于 0.33 时，才带入同一用户的长期笔记和相关历史，默认最多 6 条事件、合计 2000 字符。未匹配或未通过抽样时照常回复，不注入长期记忆。记录是带来源的原话，不将陈的角色编造自动当作事实。`--dry-run`、`--mock` 不写记忆，旧历史基线不会自动导入。

`memory_path` 默认为目录 `state/memory`，自动事件默认保留 180 天，默认总容量上限 256 MB（当前本地配置为 1024 MB），人工笔记不会被自动清理。设置 `memory_enabled=false` 可关闭记忆而不删除文件。配置、维护与限制详见 [docs/MEMORY.md](docs/MEMORY.md)。

## 群成员记忆初始化

使用已登录的抖音群聊页面及 DeepSeek 原生视觉，为每位平台成员初始化独立 Markdown 笔记：

```bash
uv run scripts/init_group_memory.py --group-title "高雅抖音" --group-id 7516934184514961957
```

运行前停止占用 Firefox 的主程序。模块只采集成员和聊天截图，不发送群消息；群记忆位于 `state/memory/groups/`，与私聊分开。采集范围、待核对信息、缓存和使用限制见 [docs/GROUP_MEMORY.md](docs/GROUP_MEMORY.md)。

## 真实收发测试

这是独立的人工触发测试，不包含在本地回归测试中。它会真实调用 DeepSeek，并启用真实发送（不受回复配置 `dry_run` 控制），仅处理白名单私聊。

```bash
uv run scripts/test_live_reply.py --prepare
uv run scripts/test_live_reply.py --install-browser
uv run run_friend.py --login
uv run scripts/test_live_reply.py --check
uv run scripts/test_live_reply.py --duration 180
```

准备步骤只创建缺失的本地配置，不覆盖已有文件。请在 `.env` 填写真实模型 Key；登录、验证码和白名单账号绑定由用户手动完成。浏览器系统依赖的安装见 Ubuntu 文档。

测试打印“采样就绪”后，请让白名单用户发一条新文字消息。180 秒内至少有一次页面确认发送且没有未知发送时输出 PASS、退出码 0；无确认输出 FAIL、退出码 2。缺少运行条件时退出码 1；手动中断为 130，不判通过。测试期间可能回复多条新消息，仍遵守配置中的配额和发送间隔。

已有历史检查点继续增量处理，不使用 `--reply-latest`。启动前若存在待处理任务或未知发送，测试会要求先核对，不清除状态、不发送旧队列。`--check` 只检查本地条件，不能证明真实网页仍然处于登录状态。

## 维护

迁移打包使用 `uv run zip/pack_project.py`，压缩包保存在项目 `zip/` 目录；清单和使用说明见 [zip/README.md](zip/README.md)。打包前停止主程序及独立工作进程。

```bash
uv run scripts/run_tests.py
uv lock --check --offline
```

只保留 Firefox 网页路线。`python -m group_friend` 和 `group-friend` 也进入同一运行器。旧 Windows 原生收发、人工审核和凭据管理 CLI 已移除；长期记忆已重新实现为 Markdown 模块。`.env`、本地配置、登录资料、数据库和回执不属于清理对象。

Windows 使用相同的 Python 入口和准备脚本；Ubuntu 系统依赖安装见迁移文档。

目录说明见 [docs/PROJECT_LAYOUT.md](docs/PROJECT_LAYOUT.md)。当前人格为 `personas/NICKY_PERSONA.md`，示例配置位于 `config/examples/`，本地配置和运行状态保持原路径。

架构取舍见 [docs/EXAMPLE_ALIGNMENT.md](docs/EXAMPLE_ALIGNMENT.md)，交接见 [CODEX_HANDOFF.md](CODEX_HANDOFF.md)，验证见 [docs/VERIFICATION.md](docs/VERIFICATION.md)。参考代码及许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
