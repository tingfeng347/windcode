from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def request(
    helper: Path,
    workspace: Path,
    command: list[str],
    *,
    preset: str = "workspace_write",
    network: bool = False,
) -> subprocess.CompletedProcess[str]:
    argv = [
        str(helper),
        "run",
        "--workspace",
        str(workspace),
        "--cwd",
        str(workspace),
        "--preset",
        preset,
        "--parent-pid",
        str(os.getpid()),
    ]
    if network:
        argv.append("--network")
    argv.extend(("--", *command))
    return subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)


def assert_process_stopped(pid: int) -> None:
    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for _ in range(50):
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return
        exit_code = ctypes.c_ulong()
        try:
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                if exit_code.value != still_active:
                    return
        finally:
            kernel32.CloseHandle(handle)
        time.sleep(0.1)
    raise AssertionError(f"Job Object descendant {pid} survived helper termination")


def main() -> None:
    helper = Path(sys.argv[1]).resolve()

    def read_status() -> dict[str, object]:
        return json.loads(
            subprocess.run(
                [helper, "status", "--json"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            ).stdout
        )

    status = read_status()
    if status.get("ready") is not True:
        subprocess.run([helper, "setup", "--json"], check=True, timeout=120)
        status = read_status()
    assert status["ready"] is True, status
    assert all(status["capabilities"].values()), status

    with tempfile.TemporaryDirectory(prefix="windcode-sandbox-") as raw:
        workspace = Path(raw, "workspace")
        workspace.mkdir()
        outside = Path(raw, "outside.txt")

        writable = request(helper, workspace, ["cmd.exe", "/d", "/c", "echo ok>inside.txt"])
        assert writable.returncode == 0, writable
        assert workspace.joinpath("inside.txt").is_file()

        readonly = request(
            helper,
            workspace,
            ["cmd.exe", "/d", "/c", "echo denied>readonly.txt"],
            preset="read_only",
        )
        assert readonly.returncode != 0, readonly
        assert not workspace.joinpath("readonly.txt").exists()

        escaped = request(
            helper,
            workspace,
            ["cmd.exe", "/d", "/c", f"echo denied>{outside}"],
        )
        assert escaped.returncode != 0, escaped
        assert not outside.exists()

        other_workspace = Path(raw, "other-workspace")
        other_workspace.mkdir()
        cross_workspace = request(
            helper,
            other_workspace,
            ["cmd.exe", "/d", "/c", f"type {workspace / 'inside.txt'}"],
        )
        assert cross_workspace.returncode != 0, cross_workspace

        cancelled = subprocess.Popen(
            [
                str(helper),
                "run",
                "--workspace",
                str(workspace),
                "--cwd",
                str(workspace),
                "--preset",
                "workspace_write",
                "--parent-pid",
                str(os.getpid()),
                "--",
                "ping.exe",
                "-t",
                "127.0.0.1",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)
        assert cancelled.poll() is None, "long-running sandbox command exited early"
        cancelled.terminate()
        cancelled.wait(timeout=10)
        after_cancellation = request(
            helper,
            other_workspace,
            ["cmd.exe", "/d", "/c", f"type {workspace / 'inside.txt'}"],
        )
        assert after_cancellation.returncode != 0, after_cancellation

        child_pid_file = workspace / "child.pid"
        script = (
            "$p=Start-Process cmd.exe -ArgumentList '/d','/c','ping -t 127.0.0.1' "
            "-PassThru; Set-Content -Path child.pid -Value $p.Id; Wait-Process $p.Id"
        )
        argv = [
            str(helper),
            "run",
            "--workspace",
            str(workspace),
            "--cwd",
            str(workspace),
            "--preset",
            "workspace_write",
            "--parent-pid",
            str(os.getpid()),
            "--",
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ]
        wrapper = subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            if child_pid_file.exists():
                break
            if wrapper.poll() is not None:
                raise AssertionError(
                    f"helper exited before spawning descendant: {wrapper.returncode}"
                )
            time.sleep(0.1)
        else:
            wrapper.kill()
            raise AssertionError("timed out waiting for descendant PID")
        child_pid = int(child_pid_file.read_text().strip())
        wrapper.terminate()
        wrapper.wait(timeout=10)
        assert_process_stopped(child_pid)

        offline = request(
            helper,
            workspace,
            ["curl.exe", "-fsS", "--connect-timeout", "3", "https://example.com/"],
        )
        assert offline.returncode != 0, offline
        online = request(
            helper,
            workspace,
            ["curl.exe", "-fsS", "--connect-timeout", "5", "https://example.com/"],
            network=True,
        )
        assert online.returncode == 0, online


if __name__ == "__main__":
    main()
