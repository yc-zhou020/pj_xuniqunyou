# 从抖音群聊初始化成员记忆

入口为 `scripts/init_group_memory.py`，实现分为只读采集器 `group_friend/group_memory_reader.py` 和记忆初始化模块 `group_friend/group_memory_initializer.py`。

它复用本地 Firefox 登录资料，直接在群聊页面读取平台群 ID 和成员绑定，并将成员卡片、聊天区域截图发送给 DeepSeek 原生视觉接口。截图不先转换为 OCR 文本。API 格式按 [DeepSeek 官方视觉文档](https://api-docs.deepseek.com/guides/vision/) 实现：`deepseek-flash`、`/chat/completions`、用户消息中的 PNG base64 `image_url` 内容块。

## 运行

先停止正在占用 Firefox 登录资料的主程序，再执行：

```bash
uv run scripts/init_group_memory.py --group-title "高雅抖音" --group-id 7516934184514961957
```

群 ID 可省略，此时要求群名唯一。登录和验证码仍由用户手动完成。仅需采集、暂不调用模型时：

```bash
uv run scripts/init_group_memory.py --group-title "高雅抖音" --capture-only
```

默认采集 6 个聊天视窗，`--pages` 可设为 1–10；每批最多 10 位成员卡片，最多 20 张图片。使用 `.env` 中的 DeepSeek Key，视觉请求固定到官方 API 和 `deepseek-flash`，不会因为普通回复配置使用其他模型而改用不支持视觉的模型。图片有正常 API 用量。

## 本地文件

结果位于 `state/memory/groups/<群 ID 指纹>/`：

```text
README.md                      每位成员的笔记索引
INITIALIZATION.md              覆盖数量及不确定内容
<成员 sec_uid 指纹>/profile.md  长期笔记和视觉初始化区块
sources/<采集指纹>/
  capture.md                   平台成员列表与采集范围
  member-*.png                 与平台成员绑定的头像/昵称卡片
  chat-*.png                   聊天视窗截图
  vision-*.md                  已验证格式的原生视觉输出缓存
```

顶层 `state/memory/README.md` 也会链接群记忆索引。群记忆与现有私聊记忆分开保存，不按昵称合并；同一个人在不同群也使用独立目录。初始化器不修改白名单、私聊任务、发送回执或群设置，没有群消息发送步骤。初始化器本身只提供记忆初始化；主程序现支持经 `group_whitelist` 显式授权的群自动回复，配置见 [WHITELIST.md](WHITELIST.md)。

## 完整性与信息来源

- 群会话、头部和成员卡片须绑定同一平台群 ID；成员以原生用户 ID、sec_uid 标识。成员卡片异步重排时按 ID 重新定位，截图前后再次核对。
- 页面卡片数、已加载平台成员数和群成员总数必须一致。如果群较大、成员尚未全部加载或前端结构变更，初始化会报“不完整”，不会声称全群已覆盖。目前不自动分页拉取隐藏成员。
- 每位成员都会获得笔记；缺少可归属的聊天信息时明确保留空白。截图只覆盖所采集的近期视窗，不能代表全部群历史，也不能保证每个人都在其中发言。
- 成员卡片按平台 ID 绑定给模型；群聊气泡只有昵称唯一且视觉归属明确时才接受，转发、引用、同名或看不清的内容列为不确定项。归属检查不能保证模型转录完全准确，仍应查看报告和原截图。
- 不从头像推断现实身份、敏感属性、人格或长期喜好；视频缩略图不代表已观看视频。视觉结果在笔记中明确标为“待核对”，不是已验证事实。
- 初始化只替换 `group-vision:start/end` 之间的自动区块，保留其他人工笔记。不要手动破坏这两个标记；标记损坏时模块拒绝覆盖。相同采集内容会复用已校验的模型输出；截图、成员或提示词变化则重新分析。
- 原始截图会留在本地用于核对，没有自动过期清理；按需在停机后清理旧 `sources/` 目录。它们不会改变私聊事件的保留策略。

中断后可以重新运行同一命令。已完成的模型批次有缓存，笔记使用原子替换；不涉及重新发送群消息。
