import asyncio
from pathlib import Path

import pytest

from windcode.application import ConfigurationApplication, ExtensionApplication
from windcode.auth import FileCredentialStore
from windcode.config import AppConfig
from windcode.extensions.mcp import McpRuntime
from windcode.extensions.runtime import RunExtensions


def extension_application(tmp_path: Path) -> ExtensionApplication:
    return ExtensionApplication(
        ConfigurationApplication(AppConfig()),
        FileCredentialStore(tmp_path / "credentials.json"),
        workspace=tmp_path,
        state_root=tmp_path / "state",
        user_skill_root=tmp_path / "user-skills",
    )


@pytest.mark.asyncio
async def test_reload_retires_generation_only_after_last_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[int] = []
    original_close = RunExtensions.aclose

    async def record_close(extensions: RunExtensions) -> None:
        closed.append(extensions.snapshot.generation)
        await original_close(extensions)

    monkeypatch.setattr(RunExtensions, "aclose", record_close)
    application = extension_application(tmp_path)
    await application.open()
    lease = application.acquire_run()
    old_state = lease.state

    await application.reload()
    current = application.acquire_run()

    assert current.state.snapshot.generation > old_state.snapshot.generation
    assert current.state.tool_catalogs is not old_state.tool_catalogs
    assert current.state.selected_tools is not old_state.selected_tools
    await asyncio.sleep(0)
    assert old_state.snapshot.generation not in closed

    lease.release()
    for _ in range(10):
        if old_state.snapshot.generation in closed:
            break
        await asyncio.sleep(0)
    assert old_state.snapshot.generation in closed

    current.release()
    await application.aclose()


@pytest.mark.asyncio
async def test_reload_failure_keeps_current_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = extension_application(tmp_path)
    await application.open()
    before = application.acquire_run()
    before_state = before.state
    before.release()
    service = application.service
    assert service is not None

    async def fail_reload() -> None:
        raise OSError("reload failed")

    monkeypatch.setattr(service, "reload", fail_reload)

    with pytest.raises(OSError, match="reload failed"):
        await application.reload()

    after = application.acquire_run()
    assert after.state is before_state
    after.release()
    await application.aclose()


@pytest.mark.asyncio
async def test_close_cancels_current_startup_before_waiting_for_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = extension_application(tmp_path)
    await application.open()
    retiring_lease = application.acquire_run()
    startup_gate = asyncio.Event()

    async def delayed_startup(runtime: McpRuntime, *, concurrency: int = 4) -> tuple[str, ...]:
        del concurrency
        await startup_gate.wait()
        return runtime.required_server_ids

    monkeypatch.setattr(McpRuntime, "activate_required", delayed_startup)
    await application.reload()
    close_task = asyncio.create_task(application.aclose())
    for _ in range(10):
        if not application.required_loading:
            break
        await asyncio.sleep(0)

    assert not application.required_loading
    assert not close_task.done()

    retiring_lease.release()
    await asyncio.wait_for(close_task, timeout=1)
