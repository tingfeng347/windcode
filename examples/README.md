# Windcode Python SDK 中文示例

本目录提供一组可直接运行的 Windcode SDK 示例。示例按学习顺序编号，每个文件都是独立
程序，默认以执行命令时的当前目录作为 Agent 工作区。

## 示例目录

| 文件 | 内容 |
| --- | --- |
| `01_stateful_chat.py` | 复用 `session_id` 完成多轮有状态对话 |
| `02_custom_tool.py` | 使用 Pydantic 参数模型注册自定义只读工具 |
| `03_multi_agent.py` | 并行运行两个只读子智能体并汇总结果 |
| `04_mcp_tools.py` | 等待 MCP 启动、查看能力并调用 MCP 工具 |
| `05_skill.py` | 发现并激活本目录中的 `release-notes` Skill |
| `06_stream_and_cancel.py` | 消费事件流，并在超时后取消运行 |
| `07_bash_approval.py` | 接收并处理 shell 工具审批请求 |
| `skills/release_notes/SKILL.md` | `05_skill.py` 使用的示例 Skill |

## 准备环境

在 Windcode 仓库根目录安装依赖并创建本地配置：

```bash
uv sync --frozen --all-groups
cp .windcode/config.toml.example .windcode/config.toml
```

至少配置一个模型 Provider。下面以 OpenAI Responses 协议为例：

```toml
primary_provider = "primary"

[providers.primary]
protocol = "openai_responses"
model = "gpt-5.2-codex"
api_key_env = "OPENAI_API_KEY"
```

密钥只放在环境变量中，不要写入 TOML：

```bash
export OPENAI_API_KEY="你的密钥"
```

## 运行示例

请在仓库根目录运行，这样 `Path.cwd()` 会指向 Windcode 工作区：

```bash
uv run python examples/01_stateful_chat.py
uv run python examples/02_custom_tool.py
uv run python examples/03_multi_agent.py
uv run python examples/04_mcp_tools.py
uv run python examples/05_skill.py
uv run python examples/06_stream_and_cancel.py
uv run python examples/07_bash_approval.py
```

运行状态默认写入 Windcode 配置所选择的状态根。若只是试验，可以在
`.windcode/config.toml` 中使用项目状态目录：

```toml
[storage]
project_state_root = ".windcode/state"
```

## MCP 示例配置

`04_mcp_tools.py` 不内置具体服务地址。请先在 `.windcode/config.toml` 配置至少一个 MCP：

```toml
[extensions]
enabled = true

[extensions.mcp_servers.example]
transport = "streamable_http"
url = "https://example.com/mcp"
enable = true
required = false
```

工作区来源的 MCP 在首次使用前还需要完成信任和 reload。可以先在 TUI 中执行：

```text
/extensions trust
/extensions reload
```

## 权限与安全

- `default` 模式下，shell、写文件和其他高风险工具可能产生 `ApprovalRequested` 事件。
- `07_bash_approval.py` 演示了 `allow_once`，但真实应用应检查工具名、参数和风险后再决定。
- 不要为了省略审批而在无人值守程序中默认使用 `full_access`。
- 自定义工具必须准确声明 `effects`；它们决定权限策略和审批行为。
- Skill 是提示与流程上下文，不会绕过工具权限、沙箱或 MCP 信任规则。

## SDK 基本模式

所有示例都遵循相同的生命周期：

```python
async with Windcode.open(config, workspace=workspace) as client:
    handle = client.start_run(RunRequest("任务", workspace))
    async for event in handle:
        ...  # 流式处理文本、工具、审批和子智能体事件
    result = await handle.result()
```

必须在 `async with` 内注册工具和启动运行。退出上下文时，SDK 会关闭模型传输、MCP 连接和
仍在运行的任务。
