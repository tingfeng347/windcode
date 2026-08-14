use serde::{Deserialize, Serialize};
use std::env;
use std::process::ExitCode;

const PROTOCOL_VERSION: u32 = 1;
const DENIAL_PREFIX: &str = "windcode-sandbox:";

#[derive(Serialize)]
struct Capabilities {
    filesystem_isolation: bool,
    network_isolation: bool,
    process_isolation: bool,
}

#[derive(Serialize)]
struct Status {
    version: u32,
    ready: bool,
    capabilities: Capabilities,
    warning: Option<String>,
    remediation: Option<String>,
}

#[derive(Debug, Deserialize)]
#[cfg_attr(not(windows), allow(dead_code))]
struct RunRequest {
    version: u32,
    command: Vec<String>,
    cwd: String,
    workspace: String,
    preset: String,
    writable_roots: Vec<String>,
    network_enabled: bool,
    parent_pid: u32,
}

#[cfg(windows)]
mod windows;

#[cfg(not(windows))]
mod platform {
    use super::{Capabilities, RunRequest, Status};

    pub fn status() -> Status {
        Status {
            version: super::PROTOCOL_VERSION,
            ready: false,
            capabilities: Capabilities {
                filesystem_isolation: false,
                network_isolation: false,
                process_isolation: false,
            },
            warning: Some("the Windows sandbox helper was built for a non-Windows target".into()),
            remediation: Some("install the platform-specific Windcode Windows wheel".into()),
        }
    }

    pub fn setup() -> Result<(), String> {
        Err("Windows sandbox setup is only available on Windows".into())
    }

    pub fn run(_request: RunRequest) -> Result<u32, String> {
        Err("Windows sandbox execution is only available on Windows".into())
    }
}

#[cfg(windows)]
use windows as platform;

/// Emit the model-facing denial signature and exit non-zero. The Python backend
/// (`WindowsSandbox.classifies_denial`) matches the `windcode-sandbox:` prefix.
fn fail(code: u8, detail: &str) -> ExitCode {
    eprintln!("{DENIAL_PREFIX} {detail}");
    ExitCode::from(code)
}

fn emit_status() -> ExitCode {
    println!(
        "{}",
        serde_json::to_string(&platform::status()).expect("serialize status")
    );
    ExitCode::SUCCESS
}

fn setup() -> ExitCode {
    match platform::setup() {
        Ok(()) => {
            println!(r#"{{"version":1,"ready":true}}"#);
            ExitCode::SUCCESS
        }
        Err(error) => fail(78, &error),
    }
}

/// Parse the flag-based `run` protocol the Python backend (`WindowsSandbox.prepare`)
/// builds:
///   windcode-sandbox run --workspace <dir> --cwd <dir> --preset <name>
///       --parent-pid <pid> [--writable-root <dir>]... [--network] -- <command>...
fn parse_run_args(args: &[String]) -> Result<RunRequest, String> {
    let mut workspace: Option<String> = None;
    let mut cwd: Option<String> = None;
    let mut preset: Option<String> = None;
    let mut parent_pid: Option<u32> = None;
    let mut writable_roots: Vec<String> = Vec::new();
    let mut network_enabled = false;
    let mut command: Vec<String> = Vec::new();

    let mut index = 0;
    while index < args.len() {
        let token = args[index].clone();
        if token == "--" {
            command = args[index + 1..].to_vec();
            break;
        }
        match token.as_str() {
            "--workspace" | "--cwd" | "--preset" | "--writable-root" | "--parent-pid" => {
                let label = token.clone();
                index += 1;
                let value = args
                    .get(index)
                    .cloned()
                    .ok_or_else(|| format!("missing value after {label}"))?;
                match label.as_str() {
                    "--workspace" => workspace = Some(value),
                    "--cwd" => cwd = Some(value),
                    "--preset" => preset = Some(value),
                    "--writable-root" => writable_roots.push(value),
                    "--parent-pid" => {
                        parent_pid = Some(
                            value
                                .parse::<u32>()
                                .map_err(|_| format!("invalid --parent-pid: {value}"))?,
                        );
                    }
                    _ => unreachable!(),
                }
            }
            "--network" => network_enabled = true,
            other => return Err(format!("unknown argument: {other}")),
        }
        index += 1;
    }

    let workspace = workspace.ok_or("missing --workspace")?;
    let cwd = cwd.ok_or("missing --cwd")?;
    let preset = preset.ok_or("missing --preset")?;
    let parent_pid = parent_pid.ok_or("missing --parent-pid")?;
    if command.is_empty() {
        return Err("missing command after --".into());
    }

    Ok(RunRequest {
        version: PROTOCOL_VERSION,
        command,
        cwd,
        workspace,
        preset,
        writable_roots,
        network_enabled,
        parent_pid,
    })
}

fn run(request: RunRequest) -> ExitCode {
    if request.version != PROTOCOL_VERSION || request.command.is_empty() {
        return fail(77, "invalid request");
    }
    match platform::run(request) {
        Ok(code) => std::process::exit(code as i32),
        Err(error) => fail(77, &error),
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("status") => emit_status(),
        Some("setup") => setup(),
        Some("run") => match parse_run_args(&args[2..]) {
            Ok(request) => run(request),
            Err(error) => fail(2, &error),
        },
        _ => fail(
            2,
            "usage: windcode-sandbox status | setup | run --workspace <dir> --cwd <dir> \
             --preset <read_only|workspace_write> --parent-pid <pid> \
             [--writable-root <dir>]... [--network] -- <command>...",
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_flag_based_run_request() {
        let request = parse_run_args(&[
            "--workspace".into(),
            "C:\\ws".into(),
            "--cwd".into(),
            "C:\\ws\\sub".into(),
            "--preset".into(),
            "workspace_write".into(),
            "--parent-pid".into(),
            "12345".into(),
            "--writable-root".into(),
            "C:\\tmp".into(),
            "--network".into(),
            "--".into(),
            "cmd.exe".into(),
            "/c".into(),
            "echo".into(),
            "ok".into(),
        ])
        .expect("parse");
        assert_eq!(request.workspace, "C:\\ws");
        assert_eq!(request.cwd, "C:\\ws\\sub");
        assert_eq!(request.preset, "workspace_write");
        assert_eq!(request.parent_pid, 12345);
        assert_eq!(request.writable_roots, vec!["C:\\tmp".to_string()]);
        assert!(request.network_enabled);
        assert_eq!(request.command, vec!["cmd.exe", "/c", "echo", "ok"]);
    }

    #[test]
    fn run_request_requires_command_and_parent() {
        assert!(parse_run_args(&[
            "--workspace".into(),
            "C:\\ws".into(),
            "--cwd".into(),
            "C:\\ws".into(),
            "--preset".into(),
            "read_only".into(),
            "--parent-pid".into(),
            "1".into(),
            "--".into(),
        ])
        .is_err());
        assert!(parse_run_args(&[
            "--workspace".into(),
            "C:\\ws".into(),
            "--cwd".into(),
            "C:\\ws".into(),
            "--preset".into(),
            "read_only".into(),
            "--".into(),
            "cmd.exe".into(),
        ])
        .is_err());
    }
}
