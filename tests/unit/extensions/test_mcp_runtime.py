import asyncio

import pytest

from windcode.domain.errors import ErrorCategory
from windcode.extensions.mcp.client import McpClient, ResolvedHttpServer
from windcode.extensions.mcp.runtime import McpRuntime, McpServerState
from windcode.types import RequiredExtensionStartupError


class FakeClient(McpClient):
    def __init__(self, *, fail: bool = False, close_fail: bool = False) -> None:
        super().__init__(ResolvedHttpServer("http://127.0.0.1/unused"))
        self.fail = fail
        self.close_fail = close_fail
        self.connect_count = 0
        self.close_count = 0
        self.is_connected = False

    @property
    def connected(self) -> bool:
        return self.is_connected

    async def connect(self) -> object:  # type: ignore[override]
        self.connect_count += 1
        if self.fail:
            raise ConnectionError("failed")
        self.is_connected = True
        return object()

    async def aclose(self) -> None:
        self.close_count += 1
        self.is_connected = False
        if self.close_fail:
            raise RuntimeError("close failed")


@pytest.mark.asyncio
async def test_runtime_is_lazy_deduplicates_activation_and_closes() -> None:
    client = FakeClient()
    runtime = McpRuntime({"server": (lambda: client, False)})
    assert runtime.state("server") is McpServerState.DISCOVERED

    first, second = await asyncio.gather(runtime.activate("server"), runtime.activate("server"))
    assert first is second is client
    assert client.connect_count == 1

    await runtime.aclose()
    assert runtime.state("server") is McpServerState.CLOSED
    assert client.close_count == 1


@pytest.mark.asyncio
async def test_required_failure_attempts_required_only_and_blocks() -> None:
    failed = FakeClient(fail=True)
    required = FakeClient()
    optional = FakeClient()
    runtime = McpRuntime(
        {
            "bad": (lambda: failed, True),
            "required": (lambda: required, True),
            "optional": (lambda: optional, False),
        }
    )

    with pytest.raises(RequiredExtensionStartupError) as error:
        await runtime.activate_required()

    assert error.value.failed_sources == ("bad",)
    assert error.value.category is ErrorCategory.EXTENSION
    assert "Check configuration, credentials, network policy" in str(error.value)
    assert runtime.state("bad") is McpServerState.FAILED
    assert runtime.state("required") is McpServerState.READY
    assert required.connect_count == 1
    assert runtime.state("optional") is McpServerState.DISCOVERED
    assert optional.connect_count == 0
    await runtime.aclose()


@pytest.mark.asyncio
async def test_concurrent_observers_are_task_local() -> None:
    first = FakeClient()
    second = FakeClient()
    runtime = McpRuntime({"first": (lambda: first, False), "second": (lambda: second, False)})
    observed: dict[str, list[tuple[str, str]]] = {"first": [], "second": []}

    async def activate(server_id: str) -> None:
        async def observer(action: str, observed_server_id: str, _status: str) -> None:
            observed[server_id].append((action, observed_server_id))

        token = runtime.bind_observer(observer)
        try:
            await runtime.activate(server_id)
        finally:
            runtime.reset_observer(token)

    await asyncio.gather(activate("first"), activate("second"))

    assert {server_id for _, server_id in observed["first"]} == {"first"}
    assert {server_id for _, server_id in observed["second"]} == {"second"}
    await runtime.aclose()


@pytest.mark.asyncio
async def test_required_servers_preactivate_without_starting_optional_servers() -> None:
    required = FakeClient()
    optional = FakeClient()
    runtime = McpRuntime(
        {"required": (lambda: required, True), "optional": (lambda: optional, False)}
    )

    await runtime.activate_required()

    assert runtime.required_server_ids == ("required",)
    assert runtime.state("required") is McpServerState.READY
    assert required.connect_count == 1
    assert runtime.state("optional") is McpServerState.DISCOVERED
    assert optional.connect_count == 0
    await runtime.aclose()


@pytest.mark.asyncio
async def test_close_failure_does_not_skip_other_servers() -> None:
    failed = FakeClient(close_fail=True)
    good = FakeClient()
    runtime = McpRuntime({"failed": (lambda: failed, False), "good": (lambda: good, False)})
    await runtime.activate("failed")
    await runtime.activate("good")

    await runtime.aclose()

    assert runtime.state("failed") is McpServerState.CLOSED
    assert runtime.state("good") is McpServerState.CLOSED
    assert failed.close_count == good.close_count == 1


@pytest.mark.asyncio
async def test_cancelled_call_retires_client_and_next_call_reconnects() -> None:
    clients: list[FakeClient] = []

    def factory() -> FakeClient:
        client = FakeClient()
        clients.append(client)
        return client

    runtime = McpRuntime({"server": (factory, False)})
    started = asyncio.Event()

    async def blocked(_client: McpClient) -> object:
        started.set()
        await asyncio.Future()

    task = asyncio.create_task(runtime.call("server", blocked))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime.state("server") is McpServerState.DISCOVERED
    result = await runtime.call("server", lambda _client: asyncio.sleep(0, result="ok"))

    assert result == "ok"
    assert len(clients) == 2
    await runtime.aclose()
    assert clients[0].close_count == clients[1].close_count == 1


@pytest.mark.asyncio
async def test_ready_slot_with_disconnected_client_reconnects() -> None:
    clients: list[FakeClient] = []

    def factory() -> FakeClient:
        client = FakeClient()
        clients.append(client)
        return client

    runtime = McpRuntime({"server": (factory, False)})
    first = await runtime.activate("server")
    clients[0].is_connected = False

    second = await runtime.activate("server")

    assert second is not first
    assert len(clients) == 2
    await runtime.aclose()


@pytest.mark.asyncio
async def test_each_call_gets_one_connection_retry() -> None:
    clients: list[FakeClient] = []

    def factory() -> FakeClient:
        client = FakeClient()
        clients.append(client)
        return client

    runtime = McpRuntime({"server": (factory, False)})

    async def flaky(client: McpClient) -> object:
        if client in clients[::2]:
            raise ConnectionError("connection dropped")
        return "ok"

    assert await runtime.call("server", flaky) == "ok"
    clients[-1].is_connected = False
    assert await runtime.call("server", flaky) == "ok"

    assert len(clients) == 4
    await runtime.aclose()
