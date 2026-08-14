
<h1 align="center">Windcode</h1>

<p align="center">
  <em>A safe, extensible terminal coding agent that understands real repositories, edits code, runs commands, and verifies its work.</em>
</p>

<p align="center">
  <a href="https://pypi.org/project/windcode/"><img src="https://img.shields.io/pypi/v/windcode?logo=pypi&label=PyPI" alt="PyPI version"></a>
  <a href="https://pypi.org/project/windcode/"><img src="https://img.shields.io/pypi/pyversions/windcode?logo=python" alt="Python versions"></a>
  <a href="https://github.com/tingfeng347/windcode/actions/workflows/ci.yml"><img src="https://github.com/tingfeng347/windcode/actions/workflows/ci.yml/badge.svg" alt="Cross-platform CI"></a>
  <a href="https://github.com/tingfeng347/windcode/stargazers"><img src="https://img.shields.io/github/stars/tingfeng347/windcode?logo=github&label=Stars" alt="GitHub stars"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-65a30d" alt="Apache-2.0 license"></a>
  <br>
  <img src="https://img.shields.io/badge/Textual-TUI-111827" alt="Textual TUI">
  <img src="https://img.shields.io/badge/MCP-enabled-0ea5e9" alt="MCP enabled">
  <img src="https://img.shields.io/badge/Multi--agent-ready-7c3aed" alt="Multi-agent ready">
  <img src="https://img.shields.io/badge/Python-SDK-3776AB?logo=python&logoColor=white" alt="Python SDK">
</p>

<p align="center">
  <b>English</b> · <a href="README.zh-CN.md">中文</a>
</p>

---

## Overview

Windcode is a terminal coding agent for real software repositories. It can inspect a project, edit
files, run commands and tests, and ask for approval before high-risk operations. It combines a
Textual-based TUI with multi-provider model access, subagent collaboration, MCP, Skills, Plugins,
recoverable sessions, long-term memory, and an asynchronous Python SDK.

Windcode is designed for auditable development work rather than isolated code-generation demos.
Permission decisions, process isolation, run budgets, session persistence, and trace events are
built into the runtime so a task can move from discussion to implementation and verification in a
single workspace.

- **For:** developers and teams bringing AI into daily engineering workflows.
- **Interfaces:** an interactive terminal UI and an asynchronous Python SDK.
- **Principles:** safe by default, observable, recoverable, and open to extension.

## Demo

<p align="center">
  <img src="https://pic1.imgdb.cn/i/033rgL8ytDrAySvBniqhgs.png" alt="Windcode TUI" width="920">
</p>

![Windcode conversation and tool execution](https://pic1.imgdb.cn/i/033rhoraACOSdTMUADV8IH.png)

---

![Windcode provider and runtime interface](https://pic1.imgdb.cn/i/033rhryNejzD7nUXIJxqqT.png)

---

![Windcode terminal workflow](https://pic1.imgdb.cn/i/033ri07lrFD4Pt6SIu3Maz.png)

## Features

### Coding workspace

- Discuss a task, inspect and search files, apply patches, run shell commands, test, and build from
  one TUI.
- See tool calls, reasoning state, elapsed time, token use, approvals, and subagent progress as they
  happen.
- Queue tasks, cancel active runs, retry model streams, and recover from idle network streams.
- Manage providers, extensions, long-term memory, sessions, and history from built-in screens.
- Enter the TUI even when a provider is missing or invalid, then repair the connection through
  `/model` without restarting the application.
- Use the asynchronous SDK to subscribe to structured events, answer approvals, cancel runs,
  compact context, and coordinate subagents.

### Models and reliable execution

- Native adapters for Anthropic Messages, OpenAI Responses, and OpenAI-compatible APIs.
- Presets for OpenAI, DeepSeek, Moonshot AI, SiliconFlow, OpenRouter, Zhipu AI, Alibaba Cloud,
  Groq, Mistral, xAI, and Google Gemini, plus custom compatible endpoints.
- Primary providers, explicit fallback chains, streaming text/reasoning/tool calls, retries, and
  model fallback.
- Add, edit, disconnect, select, and query models from `/model`; store API keys in a dedicated
  credential store or provide them through environment variables.
- Automatic retry when a model stream stays idle beyond `model_stream_idle_timeout_seconds`.
- Automatic context compaction at the configured threshold, with `/compact` for manual compaction.

### Multi-agent collaboration

- `explicit` and `proactive` delegation modes with researcher, worker, and verifier roles.
- Parallel independent tasks and structured `division`, `negotiation`, or `hybrid` collaboration.
- Controlled messaging, synchronized rounds, cancellation, timeouts, and aggregate budgets.
- Isolated Git worktrees for write tasks, followed by commit, changed-file, and verification checks.
- Role-filtered tools, MCP servers, Skills, permissions, and sandbox boundaries for every child;
  recursive subagent creation is disabled.

### MCP, Skills, Hooks, and Plugins

- MCP over stdio and Streamable HTTP, including Tools, Resources, Resource Templates, and Prompts.
- Direct injection for small tool catalogs and on-demand `search_mcp_tools` discovery for large
  catalogs.
- Project Skills in `.windcode/skills/<skill-name>/SKILL.md` and user Skills in
  `~/.windcode/skills/<skill-name>/SKILL.md`, with `$skill-name` activation.
- Local plugins declared through `.windcode-plugin/plugin.toml`, combining Skills, MCP servers,
  Hooks, and custom commands.
- Hooks across session, run, tool policy, approval, compaction, and subagent lifecycle events.

### Sessions, memory, and observability

- Incrementally persisted sessions and events, resumable conversations, and history rewind.
- Long-term user profiles, project knowledge, engineering experiences, SOPs, and references with
  review, activation, search, rejection, and forgetting workflows.
- Trace events for models, tools, approvals, extensions, and subagents with retention controls.
- Session artifacts for large tool results, keeping context compact without losing provenance.

### Permissions, sandboxing, and platforms

- `plan`, `default`, `accept_edits`, and `full_access` permission modes, switchable during a run.
- Risk decisions based on side effects, parsed commands, working directories, network access, and
  sandbox state, including one-time approvals and project command-prefix rules.
- Bubblewrap on Linux and Seatbelt on macOS with `read_only`, `workspace_write`, and
  `danger_full_access` presets.
- PowerShell without an OS sandbox on Windows. Sandbox presets deterministically fall back to
  `danger_full_access`, while permission modes and dangerous-command checks remain active.

## Quick Start

Requirements: Linux, macOS, or Windows; Python 3.11+; and
[`uv`](https://docs.astral.sh/uv/).

Install the command from PyPI:

```bash
uv tool install windcode
windcode /path/to/project
```

Or install it into the current Python environment:

```bash
uv pip install windcode
```

Run from source:

```bash
uv sync --frozen --all-groups
uv run windcode /path/to/project
```

### Container image

Run a published image from GitHub Container Registry with an interactive TTY and a mounted project:

```bash
docker run --rm -it -v "$PWD:/workspace" ghcr.io/tingfeng347/windcode:0.4.2
```

See the [GHCR guide](docs/ghcr.md) for image login, persistence, and runtime details.

### Connect a model

The first launch does not require a configured model. Enter `/model` in the TUI to connect a
provider. For file-based configuration, start from `.windcode/config.toml.example`.

```toml
primary_provider = "primary"

[providers.primary]
protocol = "openai_compatible"
model = "your-model"
base_url = "https://example.com/v1"
api_key_env = "MODEL_API_KEY"
```

Provide secrets through an environment variable or the Windcode credential store, never through
project configuration:

```bash
export MODEL_API_KEY="..."
uv run windcode .
```

If a provider is absent, invalid, or has unreadable credentials, Windcode keeps the TUI and
extension system available and explains how to reconnect. Only invalid TOML or unrelated base
configuration errors prevent startup.

Common startup options:

```text
--config FILE
--model PROVIDER_OR_MODEL
--resume SESSION_ID
--permission-mode plan|default|accept_edits|full_access
--sandbox / --no-sandbox
```

## Commands and Shortcuts

```text
/new                         Start a new session
/resume [SESSION_ID]         Resume a session
/rewind                      Rewind to an earlier user message
/model [PROVIDER_ALIAS]      Manage or switch models and providers
/memory [ACTION]             Manage long-term memory
/extensions [ACTION] [ID]    Manage extensions, plugins, and trust
/compact                     Compact the current context
/clear                       Clear the visible message history
/agents                      View subagents
/status                      View runtime status
/help                        List built-in and plugin commands
/quit                        Exit Windcode

Shift+Tab                    Cycle the permission mode
Esc twice                    Interrupt the active run
```

## MCP Server

Streamable HTTP example:

```toml
[extensions]
enabled = true

[extensions.mcp_servers.example]
transport = "streamable_http"
url = "https://example.com/mcp"
enable = true
required = false
```

stdio example:

```toml
[extensions.mcp_servers.local-example]
transport = "stdio"
command = "uvx"
args = ["example-mcp-server"]
enable = true
required = false
```

Disabled servers do not connect, enter tool search, or appear in model context. `required` only
controls eager startup for an enabled server; a failed server reports degraded status without
blocking ordinary conversation. No MCP server is enabled by default.

## Subagent Configuration

```toml
[subagents]
mode = "explicit" # explicit | proactive
max_tasks = 8
max_concurrent = 4
max_model_steps = 20
max_tool_calls = 50
max_runtime_seconds = 900
max_total_model_steps = 80
max_total_tool_calls = 200
```

`explicit` exposes delegation only when the user asks for subagents or parallel work. `proactive`
allows the model to split complex work when useful. Per-task, concurrency, and aggregate budgets
apply together.

## Run Budgets and Stream Timeouts

```toml
[budgets]
max_model_steps = 40
max_tool_calls = 100
max_runtime_seconds = 1800
model_stream_idle_timeout_seconds = 60
shell_timeout_seconds = 120
```

A model stream that produces no event before the idle deadline enters the network retry and
fallback path. Manual interruption is recorded as cancellation, not as a provider failure.

## Local State

Windcode stores memory, sessions, traces, extension state, and worktrees under one selected root:

```toml
[storage]
project_state_root = ".windcode"
user_storage_root = "~/.windcode"
```

User configuration is read from `~/.windcode/config.toml`; project configuration in
`.windcode/config.toml` has higher precedence. Project configuration and runtime state under
`.windcode/` should not be committed.

API keys are stored in `auth.json` under the user storage root rather than in TOML. Windcode does
not echo credential values in project configuration or error messages.

## Troubleshooting

### No model provider is configured

This is recoverable. Extensions, MCP, Skills, sessions, and memory remain available. Run `/model`,
select a preset or custom endpoint, enter the model ID and API key, and save.

### Provider configuration or credentials are invalid

Windcode temporarily disables the unavailable model connection and continues to the welcome
screen. Repair the provider through `/model`; the updated connection takes effect without a
restart. Invalid TOML must be corrected in the file reported by the terminal.

## License

[Apache-2.0](LICENSE)
