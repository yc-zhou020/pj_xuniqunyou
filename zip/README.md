# 迁移打包

在项目目录运行：

```bash
uv run zip/pack_project.py
```

脚本从自身位置确定项目根目录，生成 `zip/pj_xuniqunyou_年月日_时分秒.zip`，完成后打印绝对路径、文件数量和大小，并校验 ZIP CRC。已有压缩包不会覆盖。

内容与此前 Bash 命令的明确文件清单一致：程序、脚本、测试、人格、示例配置、文档、根目录 JSON 配置、`.env`、依赖声明和锁、以及整个 `state/`。`state/` 中包含记忆、登录资料、数据库、WAL/SHM 文件、回执和来源截图。打包脚本及本说明也随包保留。

不打包 `.venv/`、`.uv-cache/`、`.playwright-browsers/`、`.git/`、旧 ZIP、字节码缓存或 `state/browser.log*`。不跟随符号链接。

运行前停止主程序、独立模型进程和群记忆初始化程序；脚本会检查并持有它们的进程锁，以便在程序停止写入时打包。Ctrl+C 或写入异常会清理当前未完成的 ZIP，不删除旧压缩包。

服务器解压后按 `docs/UBUNTU.md` 安装系统依赖，执行：

```bash
uv sync --locked
uv run scripts/test_live_reply.py --install-browser
```

登录过期或迁移后需要安全验证时，用 `uv run run_friend.py --login` 手动完成。
