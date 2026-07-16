from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from windcode.domain.tools import ToolContext, ToolEffect, ToolResult
from windcode.policy.commands import CommandAnalysis, analyze_bash, analyze_powershell
from windcode.runtime.process import ProcessRunner, terminate_process_tree
from windcode.sandbox import (
    LaunchSpec,
    SandboxBackend,
    SandboxPermissions,
    SandboxPolicy,
)
from windcode.tools.filesystem import require_workspace_path


class ShellInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)
    cwd: str = "."
    timeout_seconds: float | None = Field(default=None, gt=0, le=3600)
    network: bool = False
    sandbox_permissions: SandboxPermissions = SandboxPermissions.USE_DEFAULT
    justification: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_escalation_justification(self) -> ShellInput:
        if (
            self.sandbox_permissions is SandboxPermissions.REQUIRE_ESCALATED
            and self.justification is None
        ):
            raise ValueError("justification is required when sandbox_permissions=require_escalated")
        return self


def _powershell_command(executable: str, command: str) -> tuple[str, ...]:
    utf8_command = (
        "$OutputEncoding = [Console]::OutputEncoding = "
        "[System.Text.UTF8Encoding]::new($false); "
        f"{command}"
    )
    return (
        executable,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        utf8_command,
    )


_terminate_process_group = terminate_process_tree


class ShellTool:
    name = "shell"
    input_model = ShellInput
    effects = frozenset({ToolEffect.PROCESS})

    def __init__(
        self,
        *,
        sandbox: SandboxBackend | None = None,
        sandbox_policy: SandboxPolicy | None = None,
        default_timeout: float = 120.0,
        output_limit: int = 1_000_000,
        platform: str | None = None,
        shell_executable: str | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.sandbox_policy = sandbox_policy or SandboxPolicy()
        self.default_timeout = default_timeout
        self.output_limit = output_limit
        self.platform = platform or os.name
        if self.platform in {"nt", "win32"}:
            self.shell_executable = shell_executable or self._detect_powershell()
            self.description = (
                "Run a bounded PowerShell command with timeout, cancellation, optional network, "
                "and explicit sandbox escalation. Use PowerShell syntax."
            )
        else:
            self.shell_executable = shell_executable or "bash"
            self.description = (
                "Run a bounded Bash command with timeout, cancellation, optional network, "
                "and explicit sandbox escalation."
            )
        self.runner = ProcessRunner(default_timeout=default_timeout, output_limit=output_limit)

    @staticmethod
    def _detect_powershell() -> str:
        return (
            shutil.which("pwsh")
            or shutil.which("powershell.exe")
            or shutil.which("powershell")
            or "powershell.exe"
        )

    def _command(self, command: str) -> tuple[str, ...]:
        if self.platform in {"nt", "win32"}:
            return _powershell_command(self.shell_executable, command)
        return (self.shell_executable, "-lc", command)

    def analyze(self, arguments: Mapping[str, Any]) -> CommandAnalysis:
        command = arguments.get("command")
        if not isinstance(command, str):
            return analyze_bash("")
        if self.platform in {"nt", "win32"}:
            return analyze_powershell(command, executable=self.shell_executable)
        return analyze_bash(command)

    def effects_for(self, arguments: Mapping[str, Any]) -> frozenset[ToolEffect]:
        effects = set(self.effects)
        if arguments.get("network") is True:
            effects.add(ToolEffect.NETWORK)
        requested = arguments.get("sandbox_permissions")
        unavailable = self.sandbox is None or not self.sandbox.status.available
        if (
            requested == SandboxPermissions.REQUIRE_ESCALATED.value
            or requested is SandboxPermissions.REQUIRE_ESCALATED
            or unavailable
        ):
            effects.add(ToolEffect.OUTSIDE_WORKSPACE)
        return frozenset(effects)

    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        parsed = cast(ShellInput, arguments)
        cwd = require_workspace_path(context.workspace, parsed.cwd)
        command = self._command(parsed.command)
        escalated = (
            parsed.sandbox_permissions is SandboxPermissions.REQUIRE_ESCALATED
            or ToolEffect.OUTSIDE_WORKSPACE in context.granted_effects
        )
        policy = SandboxPolicy(
            self.sandbox_policy.preset,
            self.sandbox_policy.writable_roots,
            parsed.network,
        )
        if escalated or self.sandbox is None:
            spec = LaunchSpec(command, Path(cwd), backend="none", sandboxed=False)
        else:
            if not self.sandbox.status.available:
                return ToolResult(
                    output=self.sandbox.status.warning or "sandbox unavailable",
                    is_error=True,
                    data={
                        "error": "sandbox_unavailable",
                        "remediation": self.sandbox.status.remediation,
                    },
                )
            spec = self.sandbox.prepare(command, cwd=Path(cwd), policy=policy)
        return await self.runner.run(
            spec,
            context,
            timeout_seconds=parsed.timeout_seconds,
            backend=self.sandbox,
            original_command=parsed.command,
        )
