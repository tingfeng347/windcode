from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol, runtime_checkable

from windcode.domain.models import ModelEvent, ModelRequest


@runtime_checkable
class ModelTransport(Protocol):
    name: str

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...

    async def aclose(self) -> None: ...


class BaseTransport(ABC):
    name: str

    def __init__(self) -> None:
        self._close_callbacks: list[Callable[[], Awaitable[None]]] = []
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def add_close_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._close_callbacks.append(callback)

    @abstractmethod
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        raise NotImplementedError

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        for callback in reversed(self._close_callbacks):
            await callback()

    def ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"transport {self.name} is closed")
