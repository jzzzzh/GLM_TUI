# GLM 中文 TUI 助手

一个运行在终端里的中文 GLM 助手，基于 Textual 构建。它支持普通中文问答、项目文件上下文、本地长期记忆、对话日志检索、个性化名字/风格设置，以及带审批流程的代码修改代理。

## 功能

- 中文对话：直接输入问题即可和 GLM 对话。
- 项目上下文：用 `@文件名`、`/add`、`/read`、`/grep` 读取和检索本地项目文件。
- 本地记忆：保存会话、长期记忆、对话日志和自动摘要；询问“上一轮”“我问过什么”“聊天历史”等会自动检索历史。
- 个性化：支持修改助手名字、用户名字和回答风格。
- 代码代理：自动生成代码修改方案，默认先展示 diff，需 `/approve` 后才写入文件。
- 安全回滚：应用 AI 代码变更前会创建 checkpoint，可用 `/undo` 回滚。

## 环境要求

- Python 3.10+
- 智谱 AI API Key
- macOS/Linux 终端环境

依赖见 [requirements.txt](requirements.txt)：

```txt
textual
rich
httpx
```

## 快速开始

1. 准备 API Key。

   注册或登录智谱开放平台并创建 API Key：

   ```text
   https://open.bigmodel.cn/
   ```

2. 在项目根目录创建 `.apikey`。

   支持纯 key，或写成环境变量格式：

   ```bash
   ZHIPUAI_API_KEY=your_api_key_here
   ```

3. 启动。

   ```bash
   ./run.sh
   ```

`run.sh` 会自动创建 `.venv` 并安装依赖。如果没有读到 API Key，会提示注册和配置方式。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `/help` | 查看完整命令和快捷键 |
| `/model [模型名]` | 查看或切换模型 |
| `/mode ask\|code\|auto` | 切换问答、代码代理或自动识别模式 |
| `/rag on\|off` | 开启或关闭本地记忆 RAG |
| `/memory` | 查看长期记忆 |
| `/memory sync` | 立即整理未总结对话并更新记忆 |
| `/remember 键=值` | 手动写入长期记忆 |
| `/name 名字` | 修改助手名字 |
| `/user 名字` | 记住用户名字 |
| `/persona 描述` | 修改回答风格 |
| `/add 文件路径` | 加入只读上下文 |
| `/read 文件路径` | 预览文件内容 |
| `/grep 关键词` | 在项目内搜索文本 |
| `/approve [remember]` | 应用待审批代码变更 |
| `/reject` | 丢弃待审批代码变更 |
| `/undo` | 回滚最近一次已应用 AI 代码变更 |
| `/logs [关键词]` | 查看或搜索本地对话日志 |
| `/sessions` | 查看已保存会话 |
| `/quit` | 保存记忆并退出 |

## 个性化示例

可以用命令：

```text
/name GLM助手
/user 小明
/persona 简洁、直接、少废话
```

也可以直接用自然语言：

```text
把你的名字改成GLM助手
我的名字是小明
以后用简洁直接的风格回答
```

这些设置会保存到本地记忆中，后续对话会自动生效。

## 本地数据

运行后会在项目根目录生成 `.glm_tui/`，用于保存本地状态：

```text
.glm_tui/
  memory.json              # 手动记忆、个性化设置、摘要
  memory/
    summary.json           # 自动滚动记忆总结
    segments.jsonl         # 可检索记忆片段
  logs/
    turns.jsonl            # 原始对话日志
    state.json             # 记忆同步状态
  sessions/                # 会话快照
  checkpoints/             # 代码变更回滚点
```

`.apikey` 和 `.glm_tui/` 通常不应该提交到 Git，因为里面可能包含个人配置、会话内容或敏感信息。

## 代码修改流程

默认审批策略是 `strict`：

1. 你提出修改需求。
2. 助手生成待审批 diff。
3. 输入 `/approve` 后才会写入文件。
4. 写入前创建 checkpoint。
5. 如需回滚，输入 `/undo`。

低/中风险的相似任务可以用：

```text
/approve remember
```

让系统记住相似路径和操作类型的授权。

## 退出与保存

- 会话会定期自动保存。
- `/quit` 会保存会话、整理记忆并退出。
- `Ctrl+C` 会取消当前生成并保存当前状态。
- 强制结束进程或断电时，仍可能丢失最近尚未保存的内容。
