import asyncio
from pathlib import Path

import pytest

from windcode.application import ConfigurationApplication, ExtensionApplication
from windcode.auth import FileCredentialStore
from windcode.config import AppConfig
from windcode.extensions.mcp import McpRuntime
from windcode.extensions.runtime import RunExtensions
from windcode.extensions.service import ExtensionService


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


@pytest.mark.asyncio
async def test_open_failure_does_not_publish_half_initialized_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_reload(service: ExtensionService) -> None:
        del service
        raise OSError("reload failed")

    monkeypatch.setattr(ExtensionService, "reload", fail_reload)
    application = extension_application(tmp_path)

    with pytest.raises(OSError, match="reload failed"):
        await application.open()
    with pytest.raises(RuntimeError, match="inside the Windcode async context"):
        await application.list_capabilities()

    await application.aclose()


@pytest.mark.asyncio
async def test_repeated_open_does_not_replace_current_generation(tmp_path: Path) -> None:
    application = extension_application(tmp_path)
    await application.open()
    lease = application.acquire_run()
    current_state = lease.state

    with pytest.raises(RuntimeError, match="already initialized"):
        await application.open()

    after = application.acquire_run()
    assert after.state is current_state
    lease.release()
    after.release()
    await application.aclose()


@pytest.mark.asyncio
async def test_concurrent_open_waits_then_rejects_second_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reload_entered = asyncio.Event()
    finish_reload = asyncio.Event()
    original_reload = ExtensionService.reload

    async def delayed_reload(service: ExtensionService) -> object:
        reload_entered.set()
        await finish_reload.wait()
        return await original_reload(service)

    monkeypatch.setattr(ExtensionService, "reload", delayed_reload)
    application = extension_application(tmp_path)
    first_open = asyncio.create_task(application.open())
    await reload_entered.wait()
    second_open = asyncio.create_task(application.open())
    await asyncio.sleep(0)
    assert not second_open.done()

    finish_reload.set()
    await first_open
    with pytest.raises(RuntimeError, match="already initialized"):
        await second_open

    await application.aclose()


@pytest.mark.asyncio
async def test_close_waits_for_reload_and_closes_both_generations(
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
    previous_generation = application.snapshot.generation
    service = application.service
    assert service is not None
    original_reload = service.reload
    reload_entered = asyncio.Event()
    finish_reload = asyncio.Event()

    async def delayed_reload() -> object:
        reload_entered.set()
        await finish_reload.wait()
        return await original_reload()

    monkeypatch.setattr(service, "reload", delayed_reload)
    reload_task = asyncio.create_task(application.reload())
    await reload_entered.wait()
    close_task = asyncio.create_task(application.aclose())
    await asyncio.sleep(0)
    assert not close_task.done()

    finish_reload.set()
    await asyncio.gather(reload_task, close_task)
    current_generation = service.snapshot.generation

    assert set(closed) == {previous_generation, current_generation}
    with pytest.raises(RuntimeError, match="extension runtime is not initialized"):
        application.acquire_run()


@pytest.mark.asyncio
async def test_close_rejects_new_leases_before_closing_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    close_entered = asyncio.Event()
    finish_close = asyncio.Event()
    original_close = RunExtensions.aclose

    async def delayed_close(extensions: RunExtensions) -> None:
        close_entered.set()
        await finish_close.wait()
        await original_close(extensions)

    monkeypatch.setattr(RunExtensions, "aclose", delayed_close)
    application = extension_application(tmp_path)
    await application.open()

    close_task = asyncio.create_task(application.aclose())
    await close_entered.wait()

    with pytest.raises(RuntimeError, match="extension runtime is not initialized"):
        application.acquire_run()

    finish_close.set()
    await close_task


@pytest.mark.asyncio
async def test_cancelled_generation_close_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    close_entered = asyncio.Event()
    finish_close = asyncio.Event()
    original_close = RunExtensions.aclose
    close_count = 0
    close_targets: list[RunExtensions] = []

    async def delayed_close(extensions: RunExtensions) -> None:
        nonlocal close_count
        close_count += 1
        close_targets.append(extensions)
        close_entered.set()
        await finish_close.wait()
        await original_close(extensions)

    monkeypatch.setattr(RunExtensions, "aclose", delayed_close)
    application = extension_application(tmp_path)
    await application.open()

    close_task = asyncio.create_task(application.aclose())
    await close_entered.wait()
    close_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close_task

    with pytest.raises(RuntimeError, match="extension runtime is not initialized"):
        application.acquire_run()
    with pytest.raises(RuntimeError, match="already initialized"):
        await application.open()

    finish_close.set()
    await application.aclose()
    assert close_count == 2
    assert close_targets[0] is close_targets[1]
