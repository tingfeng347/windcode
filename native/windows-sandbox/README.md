# Windcode Windows sandbox helper

The helper implements protocol version 1 and is bundled in Windows wheels. It uses four distinct
AppContainer profiles (`read_only`/`workspace_write` crossed with offline/online), so concurrent
commands cannot inherit a more permissive ACL or network capability from another invocation.

Before the backend can become ready, run the helper once from an elevated terminal:

```powershell
windcode-sandbox.exe setup --json
```

Setup creates the AppContainer profiles and persistent Windows Firewall (WFP-backed) inbound and
outbound block rules scoped to each offline profile SID. A versioned marker is written under
`%LOCALAPPDATA%\Windcode\sandbox` only after every rule succeeds. Missing or stale setup fails
closed and `status --json` returns the remediation command.

At execution time the helper:

- grants the selected profile SID read-only or modify ACLs on the workspace and explicit writable
  roots;
- grants `internetClient` only to an online profile (offline profiles also have the WFP rules);
- creates the child suspended inside the AppContainer;
- assigns it to a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` before resuming it; and
- waits for and returns the child exit code. Killing the helper closes the Job Object and terminates
  the complete descendant process tree.

Build a platform wheel by setting both build variables to a release helper binary:

```powershell
$env:WINDCODE_WINDOWS_HELPER = "native/windows-sandbox/target/release/windcode-sandbox.exe"
$env:WINDCODE_WINDOWS_WHEEL_TAG = "py3-none-win_amd64"
uv build --wheel --no-sources
```
