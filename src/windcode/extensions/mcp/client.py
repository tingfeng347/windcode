from __future__ import annotations

import asyncio
import os
import tempfile
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import TracebackType
from typing import Self, TextIO, cast

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    InitializeResult,
    ListPromptsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ListToolsResult,
    PaginatedRequestParams,
    ReadResourceResult,
)


@dataclass(frozen=True, slots=True)
class ResolvedStdioServer:
    command: str
    args: tuple[str, ...] = ()
    cwd: Path | None = None
    env: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class ResolvedHttpServer:
    url: str
    headers: dict[str, str] | None = None


class McpClient:
    def __init__(
        self,
        definition: ResolvedStdioServer | ResolvedHttpServer,
        *,
        connect_timeout: float = 10.0,
        call_timeout: float = 60.0,
        stderr_limit: int = 16_384,
    ) -> None:
        self.definition = definition
        self.connect_timeout = connect_timeout
        self.call_timeout = call_timeout
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._stderr_limit = stderr_limit
        self._stderr_file: TextIO | None = None
        self._stderr_value = ""
        self.initialize_result: InitializeResult | None = None
        self._owner_task: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[InitializeResult] | None = None
        self._close_requested: asyncio.Event | None = None
        self._close_error: BaseException | None = None

    @property
    def stderr(self) -> str:
        stream = self._stderr_file
        if stream is None:
            return self._stderr_value
        stream.flush()
        position = stream.tell()
        stream.seek(0)
        value = stream.read(self._stderr_limit)
        stream.seek(position)
        return value

    @property
    def connected(self) -> bool:
        return self._session is not None

    @property
    def close_error(self) -> BaseException | None:
        return self._close_error

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        await self.aclose()

    async def connect(self) -> InitializeResult:
        if self.initialize_result is not None:
            return self.initialize_result
        if self._owner_task is None:
            loop = asyncio.get_running_loop()
            self._ready = loop.create_future()
            self._close_requested = asyncio.Event()
            self._owner_task = asyncio.create_task(self._own_connection())
        ready = self._ready
        if ready is None:
            raise RuntimeError("MCP client connection owner was not initialized")
        async with asyncio.timeout(self.connect_timeout):
            return await asyncio.shield(ready)

    async def _own_connection(self) -> None:
        stack = AsyncExitStack()
        try:
            async with asyncio.timeout(self.connect_timeout):
                if isinstance(self.definition, ResolvedStdioServer):
                    stderr_file = cast(
                        TextIO,
                        tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace"),
                    )
                    self._stderr_file = stderr_file
                    minimum_env = {
                        key: value
                        for key in ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR")
                        if (value := os.environ.get(key)) is not None
                    }
                    minimum_env.update(self.definition.env or {})
                    streams = await stack.enter_async_context(
                        stdio_client(
                            StdioServerParameters(
                                command=self.definition.command,
                                args=list(self.definition.args),
                                cwd=self.definition.cwd,
                                env=minimum_env,
                            ),
                            errlog=stderr_file,
                        )
                    )
                    read_stream, write_stream = streams
                else:
                    http_client = await stack.enter_async_context(
                        httpx.AsyncClient(
                            headers=self.definition.headers,
                            timeout=httpx.Timeout(self.call_timeout, connect=self.connect_timeout),
                        )
                    )
                    streams = await stack.enter_async_context(
                        streamable_http_client(
                            self.definition.url,
                            http_client=http_client,
                            terminate_on_close=False,
                        )
                    )
                    read_stream, write_stream, _ = streams
                session = await stack.enter_async_context(
                    ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=self.call_timeout),
                    )
                )
                result = await session.initialize()
            self._stack = stack
            self._session = session
            self.initialize_result = result
            ready = self._ready
            if ready is not None and not ready.done():
                ready.set_result(result)
            close_requested = self._close_requested
            if close_requested is not None:
                await close_requested.wait()
        except BaseException as exc:
            ready = self._ready
            if ready is not None and not ready.done():
                if isinstance(exc, asyncio.CancelledError):
                    ready.cancel()
                else:
                    ready.set_exception(exc)
            elif not isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            self._session = None
            self.initialize_result = None
            await stack.aclose()
            self._stack = None
            self._close_stderr()

    def _close_stderr(self) -> None:
        if self._stderr_file is None:
            return
        self._stderr_value = self.stderr
        self._stderr_file.close()
        self._stderr_file = None

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("MCP client is not connected")
        return self._session

    async def list_tools(self, cursor: str | None = None) -> ListToolsResult:
        async with asyncio.timeout(self.call_timeout):
            return await self._require_session().list_tools(
                params=PaginatedRequestParams(cursor=cursor)
            )

    async def list_resources(self, cursor: str | None = None) -> ListResourcesResult:
        async with asyncio.timeout(self.call_timeout):
            return await self._require_session().list_resources(
                params=PaginatedRequestParams(cursor=cursor)
            )

    async def list_resource_templates(
        self, cursor: str | None = None
    ) -> ListResourceTemplatesResult:
        async with asyncio.timeout(self.call_timeout):
            return await self._require_session().list_resource_templates(
                params=PaginatedRequestParams(cursor=cursor)
            )

    async def list_prompts(self, cursor: str | None = None) -> ListPromptsResult:
        async with asyncio.timeout(self.call_timeout):
            return await self._require_session().list_prompts(
                params=PaginatedRequestParams(cursor=cursor)
            )

    async def call_tool(self, name: str, arguments: dict[str, object]) -> CallToolResult:
        async with asyncio.timeout(self.call_timeout):
            return await self._require_session().call_tool(name, arguments)

    async def read_resource(self, uri: str) -> ReadResourceResult:
        async with asyncio.timeout(self.call_timeout):
            return await self._require_session().read_resource(uri)  # pyright: ignore[reportArgumentType]

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> GetPromptResult:
        async with asyncio.timeout(self.call_timeout):
            return await self._require_session().get_prompt(name, arguments)

    async def aclose(self) -> None:
        task = self._owner_task
        if task is None:
            self._settle_ready()
            self._close_stderr()
            return
        close_requested = self._close_requested
        if close_requested is not None:
            close_requested.set()
        try:
            async with asyncio.timeout(self.connect_timeout):
                result = await asyncio.gather(task, return_exceptions=True)
                if result and isinstance(result[0], BaseException):
                    self._close_error = result[0]
        except TimeoutError:
            task.cancel()
            result = await asyncio.gather(task, return_exceptions=True)
            self._close_error = TimeoutError("MCP connection close timed out")
            if result and isinstance(result[0], BaseException):
                self._close_error = result[0]
        finally:
            self._settle_ready()
            self._owner_task = None
            self._ready = None
            self._close_requested = None

    def _settle_ready(self) -> None:
        ready = self._ready
        if ready is None:
            return
        if not ready.done():
            ready.cancel()
        elif not ready.cancelled():
            ready.exception()
