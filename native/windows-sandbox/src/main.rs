use serde::{Deserialize, Serialize};
use std::env;
use std::process::ExitCode;

const PROTOCOL_VERSION: u32 = 1;

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
        Err(error) => {
            eprintln!("WINDCODE_SANDBOX_SETUP_FAILED {error}");
            ExitCode::from(78)
        }
    }
}

fn run(request: RunRequest) -> ExitCode {
    if request.version != PROTOCOL_VERSION || request.command.is_empty() {
        eprintln!("WINDCODE_SANDBOX_DENIAL invalid request");
        return ExitCode::from(77);
    }
    match platform::run(request) {
        Ok(code) => std::process::exit(code as i32),
        Err(error) => {
            eprintln!("WINDCODE_SANDBOX_DENIAL {error}");
            ExitCode::from(77)
        }
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("status") => emit_status(),
        Some("setup") => setup(),
        Some("run") => {
            let Some(index) = args.iter().position(|item| item == "--request") else {
                eprintln!("missing --request");
                return ExitCode::from(2);
            };
            let Some(raw) = args.get(index + 1) else {
                eprintln!("missing request payload");
                return ExitCode::from(2);
            };
            match serde_json::from_str::<RunRequest>(raw) {
                Ok(request) => run(request),
                Err(error) => {
                    eprintln!("invalid request: {error}");
                    ExitCode::from(2)
                }
            }
        }
        _ => {
            eprintln!("usage: windcode-sandbox status --json | setup --json | run --request JSON");
            ExitCode::from(2)
        }
    }
}
