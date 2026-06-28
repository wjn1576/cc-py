# cc-py

一个用 Python 实现的 Claude Code 架构学习项目。

项目目标不是做一个简单的 API wrapper，而是拆解并复现 Claude Code 这类 coding agent 的核心思想：Agent Loop、工具调用、权限控制、Hook、上下文压缩、记忆、Skill、Session、MCP 以及多 Agent 协作。

## 这是什么

Claude Code 的本质可以理解为一个 **tool-use agent runtime**：

1. 接收用户输入并维护 transcript。
2. 把当前上下文、系统提示词、工具定义发送给模型。
3. 模型决定直接回答，或发起 tool_use。
4. Runtime 执行工具，并把 tool_result 写回上下文。
5. 继续调用模型，直到任务完成。

这个项目用 Python 把这套运行时机制拆开实现，方便学习 Claude Code 背后的工程设计。

## 已实现能力

| 模块 | 说明 |
| --- | --- |
| Agent Loop | 多轮 tool-use 循环、流式响应、错误恢复、自动重试 |
| Streaming Tool Executor | 工具可在模型流式输出过程中提前执行 |
| Tools | Bash、Read、Edit、Write、Glob、Grep、Agent、WebFetch、WebSearch、TodoWrite、Skill、Team 等工具 |
| Permission | 支持权限模式、规则引擎、工具调用门控 |
| Hooks | 支持 PreToolUse / PostToolUse，在工具执行前后插入自定义逻辑 |
| Prompt | System Prompt 分段拼装，支持 Coordinator / Teammate 等角色提示词 |
| Memory | 支持项目记忆、用户记忆、自动记忆提取 |
| Compact | 监控 token 预算，接近上下文上限时自动压缩 |
| Session | 会话保存、恢复、历史校验和恢复 |
| Skills | 按需加载能力说明，支持 slash command 注入 |
| MCP | 支持 MCP server 工具桥接 |
| Agent Teams | 支持 Leader / Teammate、Mailbox 通信、团队状态管理 |

## 目录结构

```text
cc/
├── main.py             CLI 入口、REPL、模块组装、session 保存
├── core/               QueryEngine、query_loop、agent runtime 状态机
├── api/                Anthropic 兼容 API 客户端、流式调用、token 统计
├── models/             Message、ContentBlock、ToolUse、ToolResult 等数据模型
├── prompts/            System Prompt 构建、角色提示词
├── tools/              工具定义、工具注册表、流式工具执行器
├── permissions/        权限模式、规则匹配、执行门控
├── hooks/              PreToolUse / PostToolUse hook 加载与执行
├── compact/            上下文压缩、摘要生成、token 预算管理
├── memory/             记忆加载、保存、提取和索引
├── session/            会话持久化、恢复、transcript recovery
├── skills/             Skill 定义加载和命令注册
├── commands/           /clear /compact /model /help /cost 等 slash 命令
├── mcp/                MCP 客户端和工具桥接
├── swarm/              Agent Team、Mailbox、Coordinator、Teammate
└── ui/                 Rich 终端渲染
```

推荐阅读顺序：

```text
cc/main.py
  -> cc/core/query_engine.py
  -> cc/core/query_loop.py
  -> cc/models/messages.py
  -> cc/tools/base.py
  -> cc/tools/streaming_executor.py
```

## 核心流程

```text
用户输入
  -> main.py 写入 messages
  -> QueryEngine.run_turn()
  -> query_loop 调用模型
  -> 模型返回 text 或 tool_use
  -> StreamingToolExecutor 执行工具
  -> tool_result 写回 messages
  -> 继续调用模型
  -> end_turn 后保存 session / 提取 memory
```

这也是它和普通 chatbot 的区别：一次用户输入可能触发多轮模型调用、多次工具执行和多次状态更新。

## 快速开始

### 使用 conda

```powershell
conda create -n cc-python-claude python=3.12 -y
conda activate cc-python-claude
pip install -e .
```

`pip install -e .` 只需要在这个 conda 环境里执行一次。安装后会注册 `cc` 命令，因此可以在任意目录启动：

```powershell
cd E:\your-project
cc
```

此时 `cc-py` 的工作目录就是 `E:\your-project`，会优先读取该目录下的 `.env`、`CLAUDE.md`、`.mcp.json` 等项目配置。

### 使用 uv

```powershell
pip install uv
uv sync
```

## 配置模型

默认模型是 `qwen3-max`，走阿里云百炼的 Anthropic 兼容接口。也就是说，直接运行 `python -m cc` 时优先读取 `DASHSCOPE_API_KEY`。

### 阿里云百炼

项目支持通过百炼的 Anthropic 兼容接口调用模型，例如 `qwen3-max`、`glm-5`、`kimi-k2.5`、`deepseek-v4-flash`。

```env
DASHSCOPE_API_KEY=your-dashscope-api-key
```

启动：

```powershell
python -m cc
python -m cc --model qwen3-max
python -m cc --model deepseek-v4-flash
```

### Claude

如果要切换到 Claude 模型，再配置 Anthropic key：

```env
ANTHROPIC_API_KEY=sk-ant-...
```

启动：

```powershell
python -m cc --model claude-sonnet-4-20250514
```

也可以在 REPL 里用 `/model` 切换模型：

```text
> /model
> /model 4
```

## CLI 用法

```powershell
# 交互模式
python -m cc

# 安装后可在任意目录直接启动
cc

# 默认使用百炼 qwen3-max，也可以指定其他百炼模型
python -m cc --model qwen3-max
python -m cc --model deepseek-v4-flash

# 从其他位置指定工作目录启动
cc --cwd E:\your-project

# 单次输入模式
echo "解释这个项目的架构" | python -m cc -p

# 恢复会话
python -m cc -c <session-id>

# 查看帮助
python -m cc --help
```

## 常用命令

```text
/help       查看帮助
/model      查看或切换模型
/compact    手动压缩上下文
/clear      清空当前上下文
/cost       查看 token / cost 信息
```

## 学习重点

这个项目适合重点观察这些问题：

1. Agent Loop 如何组织多轮模型调用和工具结果。
2. ToolUse / ToolResult 如何进入 transcript。
3. PreToolUse / PostToolUse Hook 如何做安全拦截和行为扩展。
4. Permission Check 如何降低危险工具调用风险。
5. Compact 如何在上下文快满时保留关键信息。
6. Memory 和 Session 的边界如何划分。
7. Sub Agent / Agent Team 如何通过隔离上下文和 mailbox 协作。

## 测试

```powershell
pytest tests/unit/ -v
pytest tests/integration/ tests/e2e/ -v
ruff check cc/ tests/
mypy cc/
```

集成测试需要可用的 API key 和网络环境。
