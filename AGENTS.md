# Repository Guidelines

## Command Routing

- Only invoke the `mew-spec` workflow when the user's first non-whitespace token is exactly
  `/spec`, or when the user explicitly names `$mew-spec`.
- Do not infer spec mode from ordinary requests such as “功能开发” or “先澄清需求”.

## Project Structure

Windcode is a Python 3.12 terminal coding agent. Production code lives under `src/windcode/`:

- `domain/`: core messages, events, models, errors, and tool contracts.
- `runtime/`: agent loop, scheduling, budgets, subagents, prompts, and orchestration.
- `providers/`: model protocol adapters and transport error normalization.
- `tools/`: built-in callable tools and the tool registry.
- `extensions/`: MCP, skills, hooks, plugins, discovery, and extension state.
- `memory/`: long-term memory extraction, activation, indexing, and recall.
- `tui/`: Textual application, commands, screens, and widgets.
- `config/`, `sessions/`, `context/`, `policy/`, `sandbox/`, and `observability/` own their
  corresponding infrastructure concerns.

Local tests mirror behavior in `tests/unit/`, `tests/contract/`, `tests/integration/`, `tests/e2e/`,
and `tests/smoke/`. Product requirements and checklists live under `spec/`. Both directories are
intentionally Git-ignored and must not be force-added unless the user explicitly requests it.

## Development Commands

- `uv sync --frozen --all-groups`: install locked development dependencies.
- `uv run windcode /path/to/project`: launch the TUI.
- `uv run ruff format --check .`: verify formatting.
- `uv run ruff check .`: run lint and import-order checks.
- `uv run pyright`: run strict type checking.
- `uv run pytest -q`: run the local test suite.
- `uv build`: build the source and wheel distributions.

## Engineering Conventions

Use four-space indentation, Python 3.12 syntax, and type annotations for public APIs and non-trivial
internals. Ruff enforces a 100-character line length and the `E`, `F`, `I`, `UP`, `B`, `ASYNC`, and
`RUF` rule sets. Use `snake_case` for modules and functions, `PascalCase` for classes, and
`UPPER_SNAKE_CASE` for constants.

Keep domain logic independent of Textual and provider-specific payloads. Preserve explicit async
boundaries. Prefer existing registries, stores, event types, and configuration models over parallel
abstractions. Add focused tests for local rules and integration coverage for runtime, provider, MCP,
memory, session, or TUI behavior.

## State And Configuration

All runtime state uses one selected root:

```text
explicit SDK state_root
-> configured project_state_root
-> user_storage_root
```

The root contains `skill/`, `memory/`, `sessions/`, `traces/`, `extensions/`, and `worktrees/`.
`.windcode/config.toml`, runtime directories under `.windcode/`, `tests/`, and `spec/` are local-only
ignored paths.
Never remove, rewrite, force-add, or commit them without an explicit user request.

For MCP servers, `enable` controls discovery and runtime visibility; `required` only controls startup
requirements for an enabled server. Disabled servers must not connect, appear in default server
lists, participate in tool search, or enter the system prompt. Explicit extension reload invalidates
MCP catalog and selected-tool caches.

## Security

Never commit API keys, tokens, credential-bearing URLs, local state, or trace data. Project TOML may
contain environment-variable names or credential IDs, not secret values. Use
`.windcode/config.toml.example` as the public configuration reference.

## Commits And Reviews

Use short imperative conventional subjects, for example `fix(tui): 允许取消空会话选择` or
`feat(memory): 增加分层上下文召回`. Keep unrelated user changes intact. Reviews should lead with
behavioral bugs and risks, include file references, and state remaining test gaps.
