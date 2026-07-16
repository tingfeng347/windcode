use crate::{Capabilities, RunRequest, Status, PROTOCOL_VERSION};
use serde::{Deserialize, Serialize};
use std::ffi::{c_void, OsStr};
use std::fs;
use std::os::windows::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::ptr::{null, null_mut};
use windows_sys::Win32::Foundation::{
    CloseHandle, GetLastError, LocalFree, HANDLE, HLOCAL, WAIT_OBJECT_0,
};
use windows_sys::Win32::Security::Authorization::ConvertSidToStringSidW;
use windows_sys::Win32::Security::Isolation::{
    CreateAppContainerProfile, DeriveAppContainerSidFromAppContainerName, GetAppContainerFolderPath,
};
use windows_sys::Win32::Security::{FreeSid, SECURITY_CAPABILITIES, SID_AND_ATTRIBUTES};
use windows_sys::Win32::System::Com::CoTaskMemFree;
use windows_sys::Win32::System::Console::{
    GetStdHandle, STD_ERROR_HANDLE, STD_INPUT_HANDLE, STD_OUTPUT_HANDLE,
};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Threading::{
    CreateProcessW, DeleteProcThreadAttributeList, GetExitCodeProcess,
    InitializeProcThreadAttributeList, OpenProcess, ResumeThread, UpdateProcThreadAttribute,
    WaitForMultipleObjects, CREATE_SUSPENDED, CREATE_UNICODE_ENVIRONMENT,
    EXTENDED_STARTUPINFO_PRESENT, INFINITE, PROCESS_INFORMATION,
    PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES, STARTF_USESTDHANDLES, STARTUPINFOEXW,
};

const PROFILE_PREFIX: &str = "Windcode.Sandbox.v1";
const FIREWALL_GROUP: &str = "Windcode Sandbox";
const INTERNET_CLIENT_SID: &str = "S-1-15-3-1";
const PRIVATE_NETWORK_CLIENT_SERVER_SID: &str = "S-1-15-3-3";
const SYNCHRONIZE_ACCESS: u32 = 0x0010_0000;

#[derive(Serialize, Deserialize)]
struct SetupMarker {
    version: u32,
    offline_profiles: Vec<String>,
}

struct OwnedHandle(HANDLE);

impl Drop for OwnedHandle {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { CloseHandle(self.0) };
        }
    }
}

struct OwnedSid(*mut c_void);

impl Drop for OwnedSid {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { FreeSid(self.0) };
        }
    }
}

struct LocalSid(*mut c_void);

impl Drop for LocalSid {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { LocalFree(self.0 as HLOCAL) };
        }
    }
}

fn wide(value: impl AsRef<OsStr>) -> Vec<u16> {
    value.as_ref().encode_wide().chain(Some(0)).collect()
}

fn state_dir() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    base.join("Windcode").join("sandbox")
}

fn marker_path() -> PathBuf {
    state_dir().join("setup-v1.json")
}

fn profile_name(preset: &str, network_enabled: bool) -> Result<String, String> {
    let access = match preset {
        "read_only" => "ReadOnly",
        "workspace_write" => "WorkspaceWrite",
        other => return Err(format!("unsupported Windows sandbox preset: {other}")),
    };
    let network = if network_enabled { "Online" } else { "Offline" };
    Ok(format!("{PROFILE_PREFIX}.{access}.{network}"))
}

fn all_profiles() -> [String; 4] {
    [
        format!("{PROFILE_PREFIX}.ReadOnly.Offline"),
        format!("{PROFILE_PREFIX}.ReadOnly.Online"),
        format!("{PROFILE_PREFIX}.WorkspaceWrite.Offline"),
        format!("{PROFILE_PREFIX}.WorkspaceWrite.Online"),
    ]
}

fn derive_profile_sid(name: &str) -> Result<OwnedSid, String> {
    let name_w = wide(name);
    let mut sid = null_mut();
    let result = unsafe { DeriveAppContainerSidFromAppContainerName(name_w.as_ptr(), &mut sid) };
    if result < 0 || sid.is_null() {
        return Err(format!(
            "DeriveAppContainerSidFromAppContainerName({name}) failed: 0x{result:08x}"
        ));
    }
    Ok(OwnedSid(sid))
}

fn ensure_profile(name: &str) -> Result<OwnedSid, String> {
    let name_w = wide(name);
    let display_w = wide("Windcode command sandbox");
    let description_w = wide("Restricted identity used for Windcode tool execution");
    let mut sid = null_mut();
    let result = unsafe {
        CreateAppContainerProfile(
            name_w.as_ptr(),
            display_w.as_ptr(),
            description_w.as_ptr(),
            null_mut(),
            0,
            &mut sid,
        )
    };
    if result == 0x8007_00b7u32 as i32 {
        return derive_profile_sid(name);
    }
    if result < 0 || sid.is_null() {
        return Err(format!(
            "CreateAppContainerProfile({name}) failed: 0x{result:08x}"
        ));
    }
    Ok(OwnedSid(sid))
}

fn verify_profile(name: &str) -> Result<(), String> {
    let sid = derive_profile_sid(name)?;
    let sid_w = wide(sid_string(sid.0)?);
    let mut folder = null_mut();
    let result = unsafe { GetAppContainerFolderPath(sid_w.as_ptr(), &mut folder) };
    if result < 0 || folder.is_null() {
        return Err(format!("AppContainer profile {name} is not registered"));
    }
    unsafe { CoTaskMemFree(folder.cast()) };
    Ok(())
}

fn sid_string(sid: *mut c_void) -> Result<String, String> {
    let mut raw = null_mut();
    if unsafe { ConvertSidToStringSidW(sid, &mut raw) } == 0 {
        return Err(format!("ConvertSidToStringSidW failed: {}", unsafe {
            GetLastError()
        }));
    }
    let mut len = 0;
    unsafe {
        while *raw.add(len) != 0 {
            len += 1;
        }
    }
    let value = String::from_utf16_lossy(unsafe { std::slice::from_raw_parts(raw, len) });
    unsafe { LocalFree(raw as HLOCAL) };
    Ok(value)
}

fn powershell(script: &str) -> Result<(), String> {
    let status = Command::new("powershell.exe")
        .args([
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ])
        .stdin(Stdio::null())
        .status()
        .map_err(|error| format!("failed to start PowerShell: {error}"))?;
    if !status.success() {
        return Err(format!("PowerShell exited with {status}"));
    }
    Ok(())
}

fn ps_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "''"))
}

fn install_firewall_rule(name: &str, sid: &str, direction: &str) -> Result<(), String> {
    let rule_name = format!("Windcode Sandbox {name} {direction}");
    let principal = format!("D:(A;;CC;;;{sid})");
    let script = "$ErrorActionPreference='Stop'; ".to_string()
        + "Get-NetFirewallRule -Name "
        + &ps_quote(&rule_name)
        + " -ErrorAction SilentlyContinue | Remove-NetFirewallRule; "
        + "New-NetFirewallRule -Name "
        + &ps_quote(&rule_name)
        + " -DisplayName "
        + &ps_quote(&rule_name)
        + " -Group "
        + &ps_quote(FIREWALL_GROUP)
        + " -Direction "
        + direction
        + " -Action Block -Enabled True -Profile Any -LocalUser "
        + &ps_quote(&principal)
        + " | Out-Null";
    powershell(&script)
}

pub fn setup() -> Result<(), String> {
    fs::create_dir_all(state_dir()).map_err(|error| format!("create state directory: {error}"))?;
    let mut offline_profiles = Vec::new();
    for name in all_profiles() {
        let sid = ensure_profile(&name)?;
        if name.ends_with("Offline") {
            let sid_text = sid_string(sid.0)?;
            install_firewall_rule(&name, &sid_text, "Outbound").map_err(|error| {
                format!(
                    concat!(
                        "administrator WFP/firewall initialization failed for {}: {}. ",
                        "Run this helper's `setup --json` command from an elevated terminal"
                    ),
                    name, error
                )
            })?;
            install_firewall_rule(&name, &sid_text, "Inbound").map_err(|error| {
                format!(
                    concat!(
                        "administrator WFP/firewall initialization failed for {}: {}. ",
                        "Run this helper's `setup --json` command from an elevated terminal"
                    ),
                    name, error
                )
            })?;
            offline_profiles.push(name);
        }
    }
    let marker = SetupMarker {
        version: PROTOCOL_VERSION,
        offline_profiles,
    };
    let encoded = serde_json::to_vec_pretty(&marker).map_err(|error| error.to_string())?;
    let temporary = marker_path().with_extension("tmp");
    fs::write(&temporary, encoded).map_err(|error| format!("write setup marker: {error}"))?;
    if marker_path().exists() {
        fs::remove_file(marker_path()).map_err(|error| format!("replace setup marker: {error}"))?;
    }
    fs::rename(&temporary, marker_path())
        .map_err(|error| format!("commit setup marker: {error}"))?;
    Ok(())
}

fn readiness() -> Result<(), String> {
    let raw = fs::read(marker_path())
        .map_err(|_| "administrator initialization is required".to_string())?;
    let marker: SetupMarker =
        serde_json::from_slice(&raw).map_err(|_| "setup marker is corrupt".to_string())?;
    if marker.version != PROTOCOL_VERSION {
        return Err("Windows sandbox setup version does not match the helper".into());
    }
    for name in all_profiles() {
        verify_profile(&name)?;
    }
    let expected_offline = all_profiles()
        .into_iter()
        .filter(|name| name.ends_with("Offline"))
        .collect::<std::collections::HashSet<_>>();
    let recorded_offline: std::collections::HashSet<String> =
        marker.offline_profiles.iter().cloned().collect();
    if recorded_offline != expected_offline {
        return Err("setup marker does not contain every offline identity".into());
    }
    let mut rule_names = Vec::new();
    for name in marker.offline_profiles {
        rule_names.push(format!("Windcode Sandbox {name} Inbound"));
        rule_names.push(format!("Windcode Sandbox {name} Outbound"));
    }
    let list = rule_names
        .iter()
        .map(|name| ps_quote(name))
        .collect::<Vec<_>>()
        .join(",");
    let script = "$ErrorActionPreference='Stop'; $missing=@(".to_string()
        + &list
        + ") | Where-Object { $rule=Get-NetFirewallRule -Name $_ -PolicyStore ActiveStore "
        + "-ErrorAction SilentlyContinue; $null -eq $rule -or $rule.Enabled -ne 'True' }; "
        + "if ($missing.Count -ne 0) { exit 1 }";
    powershell(&script)
        .map_err(|_| "required WFP/firewall rules are missing or disabled".to_string())?;
    Ok(())
}

pub fn status() -> Status {
    let problem = readiness().err();
    Status {
        version: PROTOCOL_VERSION,
        ready: problem.is_none(),
        capabilities: Capabilities {
            filesystem_isolation: problem.is_none(),
            network_isolation: problem.is_none(),
            process_isolation: true,
        },
        warning: problem.clone(),
        remediation: problem
            .map(|_| "Open an elevated terminal and run: windcode-sandbox.exe setup --json".into()),
    }
}

fn grant_acl(path: &Path, sid: &str, writable: bool) -> Result<(), String> {
    let permission = if writable { "(OI)(CI)M" } else { "(OI)(CI)RX" };
    let grant = format!("*{sid}:{permission}");
    let output = Command::new("icacls.exe")
        .arg(path)
        .args(["/grant", &grant, "/T", "/C", "/Q"])
        .output()
        .map_err(|error| format!("failed to start icacls for {}: {error}", path.display()))?;
    if !output.status.success() {
        return Err(format!(
            "workspace ACL grant failed for {}: {}",
            path.display(),
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(())
}

fn command_line(argv: &[String]) -> Vec<u16> {
    fn quote(arg: &str) -> String {
        if !arg.is_empty() && !arg.chars().any(|c| c == ' ' || c == '\t' || c == '"') {
            return arg.to_string();
        }
        let mut result = String::from("\"");
        let mut slashes = 0;
        for ch in arg.chars() {
            if ch == '\\' {
                slashes += 1;
            } else if ch == '"' {
                result.push_str(&"\\".repeat(slashes * 2 + 1));
                result.push('"');
                slashes = 0;
            } else {
                result.push_str(&"\\".repeat(slashes));
                slashes = 0;
                result.push(ch);
            }
        }
        result.push_str(&"\\".repeat(slashes * 2));
        result.push('"');
        result
    }
    wide(
        argv.iter()
            .map(|item| quote(item))
            .collect::<Vec<_>>()
            .join(" "),
    )
}

fn capability(value: &str) -> Result<LocalSid, String> {
    #[link(name = "advapi32")]
    unsafe extern "system" {
        fn ConvertStringSidToSidW(value: *const u16, sid: *mut *mut c_void) -> i32;
    }
    let raw = wide(value);
    let mut sid = null_mut();
    if unsafe { ConvertStringSidToSidW(raw.as_ptr(), &mut sid) } == 0 {
        return Err(format!(
            "failed to create network capability SID: {}",
            unsafe { GetLastError() }
        ));
    }
    Ok(LocalSid(sid))
}

unsafe fn launch(request: &RunRequest, app_sid: *mut c_void) -> Result<u32, String> {
    let capability_sids = if request.network_enabled {
        vec![
            capability(INTERNET_CLIENT_SID)?,
            capability(PRIVATE_NETWORK_CLIENT_SERVER_SID)?,
        ]
    } else {
        Vec::new()
    };
    let mut attributes = capability_sids
        .iter()
        .map(|sid| SID_AND_ATTRIBUTES {
            Sid: sid.0,
            Attributes: 0x0000_0004, // SE_GROUP_ENABLED
        })
        .collect::<Vec<_>>();
    let mut security = SECURITY_CAPABILITIES {
        AppContainerSid: app_sid,
        Capabilities: if attributes.is_empty() {
            null_mut()
        } else {
            attributes.as_mut_ptr()
        },
        CapabilityCount: attributes.len() as u32,
        Reserved: 0,
    };

    let mut attribute_size = 0usize;
    InitializeProcThreadAttributeList(null_mut(), 1, 0, &mut attribute_size);
    if attribute_size == 0 {
        return Err(format!(
            "size process attribute list failed: {}",
            GetLastError()
        ));
    }
    let mut attribute_storage = vec![0u8; attribute_size];
    let attribute_list = attribute_storage.as_mut_ptr().cast();
    if InitializeProcThreadAttributeList(attribute_list, 1, 0, &mut attribute_size) == 0 {
        return Err(format!(
            "initialize process attribute list failed: {}",
            GetLastError()
        ));
    }
    struct AttributeGuard(windows_sys::Win32::System::Threading::LPPROC_THREAD_ATTRIBUTE_LIST);
    impl Drop for AttributeGuard {
        fn drop(&mut self) {
            unsafe { DeleteProcThreadAttributeList(self.0) };
        }
    }
    let _attribute_guard = AttributeGuard(attribute_list);
    if UpdateProcThreadAttribute(
        attribute_list,
        0,
        PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES as usize,
        (&mut security as *mut SECURITY_CAPABILITIES).cast(),
        std::mem::size_of::<SECURITY_CAPABILITIES>(),
        null_mut(),
        null_mut(),
    ) == 0
    {
        return Err(format!(
            "set AppContainer process attribute failed: {}",
            GetLastError()
        ));
    }

    let job = OwnedHandle(CreateJobObjectW(null_mut(), null()));
    if job.0.is_null() {
        return Err(format!("CreateJobObjectW failed: {}", GetLastError()));
    }
    let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if SetInformationJobObject(
        job.0,
        JobObjectExtendedLimitInformation,
        (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
        std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
    ) == 0
    {
        return Err(format!(
            "enable Job Object kill-on-close failed: {}",
            GetLastError()
        ));
    }

    let mut startup: STARTUPINFOEXW = std::mem::zeroed();
    startup.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.StartupInfo.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    startup.StartupInfo.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    startup.lpAttributeList = attribute_list;
    let mut process: PROCESS_INFORMATION = std::mem::zeroed();
    let mut cmdline = command_line(&request.command);
    let cwd = wide(&request.cwd);
    let flags = CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT;
    if CreateProcessW(
        null(),
        cmdline.as_mut_ptr(),
        null(),
        null(),
        1,
        flags,
        null(),
        cwd.as_ptr(),
        &startup.StartupInfo,
        &mut process,
    ) == 0
    {
        return Err(format!(
            "CreateProcessW in AppContainer failed: {}",
            GetLastError()
        ));
    }
    let process_handle = OwnedHandle(process.hProcess);
    let thread_handle = OwnedHandle(process.hThread);
    if AssignProcessToJobObject(job.0, process_handle.0) == 0 {
        return Err(format!(
            "AssignProcessToJobObject failed: {}",
            GetLastError()
        ));
    }
    if ResumeThread(thread_handle.0) == u32::MAX {
        return Err(format!("ResumeThread failed: {}", GetLastError()));
    }
    let parent = OwnedHandle(OpenProcess(SYNCHRONIZE_ACCESS, 0, request.parent_pid));
    if parent.0.is_null() {
        return Err(format!("open parent process failed: {}", GetLastError()));
    }
    let wait_handles = [process_handle.0, parent.0];
    let wait_result = WaitForMultipleObjects(
        wait_handles.len() as u32,
        wait_handles.as_ptr(),
        0,
        INFINITE,
    );
    if wait_result != WAIT_OBJECT_0 {
        return Err("parent process exited; terminated the sandbox process tree".into());
    }
    let mut exit_code = 1;
    if GetExitCodeProcess(process_handle.0, &mut exit_code) == 0 {
        return Err(format!("GetExitCodeProcess failed: {}", GetLastError()));
    }
    Ok(exit_code)
}

pub fn run(request: RunRequest) -> Result<u32, String> {
    readiness()?;
    let cwd = PathBuf::from(&request.cwd)
        .canonicalize()
        .map_err(|error| format!("invalid cwd: {error}"))?;
    let workspace = PathBuf::from(&request.workspace)
        .canonicalize()
        .map_err(|error| format!("invalid workspace: {error}"))?;
    if !cwd.starts_with(&workspace) {
        return Err("cwd is outside the configured workspace".into());
    }
    if request.parent_pid == 0 {
        return Err("invalid parent process identifier".into());
    }
    let name = profile_name(&request.preset, request.network_enabled)?;
    let sid = derive_profile_sid(&name)?;
    let sid_text = sid_string(sid.0)?;
    grant_acl(&workspace, &sid_text, request.preset == "workspace_write")?;
    for root in &request.writable_roots {
        let root = PathBuf::from(root)
            .canonicalize()
            .map_err(|error| format!("invalid writable root {root}: {error}"))?;
        grant_acl(&root, &sid_text, true)?;
    }
    let temp = state_dir().join("tmp").join(name.replace('.', "-"));
    fs::create_dir_all(&temp).map_err(|error| format!("create sandbox temp: {error}"))?;
    grant_acl(&temp, &sid_text, true)?;
    std::env::set_var("TEMP", &temp);
    std::env::set_var("TMP", &temp);
    unsafe { launch(&request, sid.0) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profiles_separate_access_and_network_modes() {
        let values = all_profiles();
        let unique = values.iter().collect::<std::collections::HashSet<_>>();
        assert_eq!(unique.len(), 4);
        assert_eq!(
            profile_name("read_only", false).unwrap(),
            "Windcode.Sandbox.v1.ReadOnly.Offline"
        );
        assert_eq!(
            profile_name("workspace_write", true).unwrap(),
            "Windcode.Sandbox.v1.WorkspaceWrite.Online"
        );
    }

    #[test]
    fn quotes_windows_command_line_arguments() {
        let encoded = command_line(&[
            "tool.exe".into(),
            "plain".into(),
            "with space".into(),
            r#"a\"b"#.into(),
        ]);
        let value = String::from_utf16_lossy(&encoded[..encoded.len() - 1]);
        assert_eq!(value, r#"tool.exe plain "with space" "a\\\"b""#);
    }
}
