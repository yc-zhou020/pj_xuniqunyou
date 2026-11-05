# 从 Windows 迁移到 Ubuntu

目标环境为 Ubuntu 24.04 LTS。项目通过 uv 使用 `.python-version` 指定的 Python，系统自带 Python 不需要替换。Windows 和 Ubuntu 共用源码、`uv.lock` 和相对路径配置；`example/` 是开发参考项目，继续保留。

## 安装

先将项目放到 Ubuntu 本地目录，例如 `~/projects/douyin-friend`。在项目根目录操作：

```bash
sudo apt-get update
sudo apt-get install -y curl ca-certificates
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
uv sync --locked
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers"
uv run --locked python -m playwright install --with-deps firefox
```

最后一条安装 Firefox 和 Ubuntu 系统依赖，安装系统依赖时可能要求 sudo 密码。使用日常用户运行 uv 和项目，不要用 sudo 启动聊天程序。该导出变量只供安装命令使用；运行器会根据 `browser_path` 自行设置路径。

首次配置时复制示例，已有文件不覆盖：

```bash
cp -n browser.example.json browser.local.json
cp -n config.example.json config.local.json
cp -n .env.example .env
chmod 600 .env
```

在 `.env` 填入 DeepSeek Key。如果是迁移旧配置，继续使用原来的 `reply_config`，不必改名。配置路径使用 `/` 和相对路径，移除 `C:\...` 等 Windows 绝对路径。

## 迁移已有状态

1. 停止 Windows 上的程序并关闭专用 Firefox，确认不再写数据库，再备份项目的本地配置、`.env` 和整个 `state/`。复制 SQLite 时连同现存的 `-wal`、`-shm` 文件一起保留。
2. 将这些文件复制到 Ubuntu 的对应位置。不要复制 Windows 的 `.venv/`、`.playwright-browsers/`、`.uv-cache/`；在 Ubuntu 按上面的命令重新安装。保留 `uv.lock`。
3. 保留 `state/replies.sqlite3` 等数据库和回执，不新建数据库来绕过 unknown。配置中的白名单抖音号及 sec_uid 也继续保留。
4. Firefox 登录资料不保证能跨系统复用。保留原资料备份，在 Ubuntu 使用新的资料目录（例如将 `profile_dir` 设为 `state/firefox-profile-ubuntu`），手动重新登录；不要删除数据库来重置登录。

迁移完成后两台机器不要同时运行同一个聊天助手。本地文件锁只约束本机进程，不能防止两台独立机器重复回复。

## 手动登录与运行

在 Ubuntu 图形桌面的终端中运行：

```bash
uv run --locked python run_friend.py --login
```

登录、验证码和身份绑定由用户操作。`--login` 总会打开可见浏览器；纯 SSH、无桌面或仅使用 Xvfb 不提供用户可直接操作的登录窗口，需要先通过实际桌面或远程桌面完成登录。登录后 `headless=true` 的日常运行不需要图形桌面或 Xvfb。

先检查，再启动：

```bash
uv run --locked python run_friend.py --status
uv run --locked python run_friend.py --inspect
uv run --locked python run_friend.py
```

`--inspect` 会读取真实白名单私聊但不调用模型、不发送。最后一条在回复配置 `dry_run=false` 时会自动回复，只在准备好正式运行后执行。

日志：`tail -n 30 -f state/browser.log`。Ctrl+C 停止，或 `mkdir -p state && touch state/PAUSE`；核对状态后用 `rm state/PAUSE` 解除暂停再启动。退出终端会影响前台进程，如需后台运行可使用自己已有的终端会话管理工具。

## 验证

```bash
REQUIRE_BROWSER_TESTS=1 uv run --locked python -m unittest discover -s tests -v
uv lock --check --offline
```

测试使用本地 HTML 和模拟模型，不需要 `.env` 或抖音登录。`REQUIRE_BROWSER_TESTS=1` 防止浏览器缺失时静默跳过集成测试。GitHub Actions 的 Ubuntu 24.04 任务也执行这些检查。

安装说明参考 [Playwright 系统要求](https://playwright.dev/python/docs/intro)、[浏览器与系统依赖安装](https://playwright.dev/python/docs/browsers) 和 [uv 安装](https://docs.astral.sh/uv/getting-started/installation/)。
