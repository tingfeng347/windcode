# Repository Guidelines

## Project Structure & Module Organization

Windcode is a Python 3.12 terminal application. Production code lives under `src/windcode/`. Keep core types and rules in `domain/`, orchestration in `runtime/`, model adapters in `providers/`, callable capabilities in `tools/`, and terminal UI code in `tui/`. Configuration, session, context, policy, and sandbox concerns each have dedicated packages. Tests mirror behavior by scope in `tests/unit/`, `tests/contract/`, `tests/integration/`, `tests/e2e/`, and `tests/smoke/`; TUI integration tests belong in `tests/integration/tui/`. Product requirements and implementation checklists live in `spec/`, including `spec/tui-refresh/`.

## Build, Test, and Development Commands

- `uv sync --frozen --all-groups`: install the locked development environment.
- `uv run windcode /path/to/project`: launch the CLI against a workspace.
- `uv run ruff format --check .`: verify formatting without rewriting files.
- `uv run ruff check .`: run lint and import-order checks.
- `uv run pyright`: run strict static type checking.
- `uv run pytest -q`: execute the complete test suite.
- `uv build`: create source and wheel distributions.

## Coding Style & Naming Conventions

Use four-space indentation, Python 3.12 syntax, and type annotations for public APIs and non-trivial internals. Ruff enforces a 100-character line length and the `E`, `F`, `I`, `UP`, `B`, `ASYNC`, and `RUF` rule sets. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants. Keep async boundaries explicit and avoid coupling domain logic to Textual widgets or provider-specific payloads.

## Testing Guidelines

Pytest uses asyncio auto mode. Name files `test_*.py` and test functions `test_*`. Add focused unit tests for local logic, contract tests for stable interfaces, and integration tests for provider, runtime, or TUI flows. Keep real-provider smoke tests opt-in and never require credentials for the default suite.

## Commit & Pull Request Guidelines

The history does not yet establish a formal convention. Use short imperative subjects, optionally scoped, such as `tui: add slash command completion` or `runtime: prevent historical event replay`. Pull requests should summarize behavioral changes, list verification commands and results, link relevant issues or specs, and include screenshots for visible TUI changes.

## Security & Configuration

Never commit API keys. In `.windcode/config.toml`, set `api_key_env` to an environment variable name, not the secret itself. Use `.windcode/config.toml.example` as the configuration reference.
