# DeepSeek 抖音私聊助手

独立 Python 程序，使用 Playwright/Firefox 操作抖音网页版，将结构化聊天快照直接交给 DeepSeek。只回复白名单私聊文字，运行时不需要 Codex。

## 安装与登录

Ubuntu 迁移步骤见 [docs/UBUNTU.md](docs/UBUNTU.md)，包含系统依赖、手动登录和状态迁移。以下命令在 Ubuntu 项目目录的终端中执行：

```bash
uv sync --locked
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers"
uv run --locked python -m playwright install --with-deps firefox
cp -n browser.example.json browser.local.json
cp -n config.example.json config.local.json
cp -n .env.example .env
uv run --locked python run_friend.py --login
```

已有配置和登录资料不要覆盖。将 DeepSeek Key 填入本地 `.env`。登录、验证码和安全验证由用户手动完成；打开目标私聊并完成抖音号及 sec_uid 绑定后，常规运行使用无界面 Firefox。

新环境默认 `dry_run=true`。核对配置后，在 `browser.local.json` 指向的回复配置中设置 `dry_run=false`，启动后即可自动发送。已有本地 `reply_config` 路径继续有效，无需重命名。

## 运行

```bash
uv run --locked python run_friend.py
```

默认读取 `browser.local.json`，也可用 `--config` 指定。固定每 5 秒采样，发送至少间隔 5 秒，每小时 30 条、每天 100 条（示例配置）。模型在线程中运行；限额会延后任务，生成结果持久化，不重复付费生成。

首次启动建立历史基线，之后处理新来信；已有检查点则恢复增量和未完成任务。`--once --reply-latest` 可处理首次基线中的最新来信，已有检查点时不会重放已处理消息。

```bash
uv run --locked python run_friend.py --inspect
uv run --locked python run_friend.py --inspect --duration 60
uv run --locked python run_friend.py --status
tail -n 30 -f state/browser.log
```

`--inspect` 不调用模型、不发送、不创建回复任务，快照保存在 `state/browser-snapshots/`。`--dry-run` 调用模型但不发送；`--mock` 使用本地固定回复且不发送。Ctrl+C 停止，创建 `state/PAUSE` 也会停止工作；恢复前核对状态并移除暂停文件。

发送前核验平台身份、源消息和完整草稿，不覆盖人工草稿。只点击一次按钮，结果不明则标记 `unknown` 并阻止继续发送，重启不会重发。停止程序、人工核对并至少等待 90 秒后记录结论：

```bash
uv run --locked python run_friend.py --resolve '任务编号' --outcome not_sent --note '人工核对未发送'
```

确认已发送时用 `--outcome visible_sent`。该操作只记录结论，不重试任务。旧数据库中的未决发送仍会阻断运行；不要删除数据库或回执来绕过。

## 维护

```bash
uv run --locked python -m unittest discover -s tests -v
uv lock --check --offline
```

只保留 Firefox 网页路线。`python -m group_friend` 和 `group-friend` 也进入同一运行器。旧 Windows 原生收发、人工审核、长期记忆和凭据管理 CLI 已移除。`.env`、本地配置、登录资料、数据库和回执不属于清理对象。

Windows 仍可运行；安装浏览器前在 PowerShell 设置 `$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD/.playwright-browsers"`，复制配置用 `Copy-Item`，日志用 `Get-Content state/browser.log -Tail 30 -Wait`。

架构取舍见 [docs/EXAMPLE_ALIGNMENT.md](docs/EXAMPLE_ALIGNMENT.md)，交接见 [CODEX_HANDOFF.md](CODEX_HANDOFF.md)，验证见 [docs/VERIFICATION.md](docs/VERIFICATION.md)。参考代码及许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
