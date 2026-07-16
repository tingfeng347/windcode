from __future__ import annotations

import hashlib
import os
import threading
import time
import tomllib
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from uuid import uuid4

import tomli_w

from windcode.policy.commands import CommandAnalysis, CommandRule


def project_identifier(workspace: Path) -> str:
    return hashlib.sha256(str(workspace.expanduser().resolve()).encode()).hexdigest()[:24]


class CommandRuleStore:
    def __init__(self, state_root: Path, workspace: Path) -> None:
        self.path = (
            state_root.expanduser().resolve()
            / "permissions"
            / "projects"
            / f"{project_identifier(workspace)}.toml"
        )
        self._lock = threading.Lock()

    @contextmanager
    def _file_lock(self) -> Generator[None, None, None]:
        lock_path = self.path.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor: int | None = None
        for _ in range(100):
            try:
                descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                break
            except FileExistsError:
                try:
                    stale = time.time() - lock_path.stat().st_mtime > 30
                except OSError:
                    stale = False
                if stale:
                    lock_path.unlink(missing_ok=True)
                    continue
                time.sleep(0.02)
        if descriptor is None:
            raise TimeoutError(f"timed out acquiring permission-rule lock: {lock_path}")
        try:
            yield
        finally:
            os.close(descriptor)
            lock_path.unlink(missing_ok=True)

    def load(self) -> tuple[CommandRule, ...]:
        if not self.path.is_file():
            return ()
        try:
            with self.path.open("rb") as stream:
                raw = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError):
            return ()
        values = cast(object, raw.get("rules", []))
        if not isinstance(values, list):
            return ()
        rules: list[CommandRule] = []
        for value in cast(list[object], values):
            if not isinstance(value, dict):
                continue
            try:
                rules.append(CommandRule.model_validate(value))
            except ValueError:
                continue
        return tuple(rules)

    def append(self, rule: CommandRule) -> None:
        with self._lock:
            with self._file_lock():
                rules = (*self.load(), rule)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(f".tmp-{uuid4().hex}")
                payload = {"rules": [item.model_dump(mode="json") for item in rules]}
                try:
                    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "wb") as stream:
                        tomli_w.dump(payload, stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    temporary.replace(self.path)
                finally:
                    temporary.unlink(missing_ok=True)

    def allows(self, analysis: CommandAnalysis, *, network: bool, escalated: bool = False) -> bool:
        return bool(analysis.actions) and all(
            any(
                rule.matches(
                    action,
                    dialect=analysis.dialect,
                    network=network,
                    escalated=escalated,
                )
                for rule in self.load()
            )
            for action in analysis.actions
        )
