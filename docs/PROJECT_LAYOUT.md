# 项目目录

```text
run_friend.py                  日常主程序入口
group_friend/                  程序实现
  browser_reference/           网页读取代码、选择器和许可证
  live_test.py                 真实收发测试实现
  markdown_memory.py           Markdown 长期记忆实现
  memory_recall.py             关键词模糊匹配与概率召回
  targets.py                   私聊/群目标及授权检查
  group_message_identity.py    原生群消息发送者绑定
  group_memory_reader.py       群成员核验与只读截图
  group_memory_initializer.py  DeepSeek 视觉初始化群成员记忆
scripts/
  run_tests.py                 本地回归测试入口
  test_live_reply.py           真实收发测试入口
  init_group_memory.py         群成员记忆初始化入口
tests/                         自动化回归测试
personas/
  NICKY_PERSONA.md              当前使用的人格
  default.txt                  默认人格
config/examples/
  browser.json                 浏览器配置模板
  reply.json                   回复配置模板
docs/                          安装、架构、目录和验证记录
zip/pack_project.py            迁移打包脚本
zip/*.zip                      生成的迁移压缩包，Git 忽略
CODEX_HANDOFF.md                开发交接
README.md                      使用说明
THIRD_PARTY_NOTICES.md          第三方代码及许可证说明
```

本地 `.env`、`browser.local.json`、`config.local.json` 继续留在根目录，便于维护且不改变已有配置入口。其他已有回复配置路径也继续保留。当前人格路径为 `personas/NICKY_PERSONA.md`。

`state/memory/` 保存按用户隔离的 Markdown 长期记忆，入口为 `state/memory/README.md`。详见 [MEMORY.md](MEMORY.md)。

`state/` 保存登录资料、数据库、回执、快照和日志；不要为整理目录而清空。`.venv/`、`.uv-cache/`、`.playwright-browsers/` 和 `*.egg-info/` 为依赖工具生成的目录，已由 Git 忽略。`__pycache__/` 是可再生成的 Python 字节码缓存。

所有入口都使用 Python：

```bash
uv run run_friend.py --live
uv run scripts/run_tests.py
uv run scripts/test_live_reply.py
```

脚本会以项目根目录为运行目录。真实测试会调用模型并发送白名单私聊回复；本地回归测试使用临时数据库、假模型和本地 HTML。
