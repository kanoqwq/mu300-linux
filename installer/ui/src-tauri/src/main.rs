#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::{collections::HashMap, io::{BufRead, BufReader, Write}, path::PathBuf,
    process::{ChildStdin, Command, Stdio}, sync::{Arc, Mutex, atomic::{AtomicBool, AtomicU64, Ordering}}};
use tauri::{Emitter, Manager, State};
use tokio::sync::oneshot;

const COMMANDS: &[&str] = &["hello","snapshot","list","package","devices","adb_connect","adb_disconnect","probe","probe_update","plan","start","resume","retry","cancel","can_close","export","import","diagnostics","verify_openwrt","ota_analyze","ota_export","ota_package","ota_probe","ota_apply","ota_status","ota_reboot"];
struct Bridge {
    input: Mutex<Option<ChildStdin>>,
    pending: Mutex<HashMap<u64, oneshot::Sender<Result<Value, String>>>>,
    sequence: AtomicU64,
    busy: AtomicBool,
    directory: PathBuf,
    export_log: Mutex<Option<PathBuf>>,
}

#[tauri::command]
async fn backend_request(state: State<'_, Arc<Bridge>>, command: String, params: Value) -> Result<Value, String> {
    if !COMMANDS.contains(&command.as_str()) || !params.is_object() {return Err("Unsupported installer command".into());}
    let id = state.sequence.fetch_add(1, Ordering::SeqCst);
    let (send, receive) = oneshot::channel();
    state.pending.lock().unwrap().insert(id, send);
    let line = json!({"protocol":1,"id":id,"command":command,"params":params}).to_string()+"\n";
    let written = {
        let mut input = state.input.lock().unwrap();
        match input.as_mut() {
            Some(stream) => stream.write_all(line.as_bytes()).map_err(|e|e.to_string()),
            None => Err("Backend is unavailable".to_string()),
        }
    };
    if let Err(error) = written {state.pending.lock().unwrap().remove(&id); return Err(error);}
    // Verification and large archives can take minutes. No artificial device wait deadline.
    receive.await.map_err(|_|"Backend closed the response channel".to_string())?
}

#[tauri::command]
async fn pick_package() -> Option<String> {
    tauri::async_runtime::spawn_blocking(|| rfd::FileDialog::new().set_title("OpenWrt package / 安装包目录").pick_folder().map(|p|p.to_string_lossy().to_string())).await.ok().flatten()
}
#[tauri::command]
async fn pick_ota_input() -> Option<String> {
    tauri::async_runtime::spawn_blocking(|| rfd::FileDialog::new().set_title("Select release ZIP or OTA / 选择发行包或差异包").add_filter("OpenWrt packages", &["zip","ota"]).pick_file().map(|p|p.to_string_lossy().to_string())).await.ok().flatten()
}
#[tauri::command]
async fn save_ota(suggested_name: Option<String>, release_path: Option<String>) -> Option<String> {
    let name = suggested_patch_name(suggested_name.as_deref(), release_path.as_deref());
    tauri::async_runtime::spawn_blocking(move || rfd::FileDialog::new().set_file_name(name).add_filter("Desktop + Web", &["zip"]).add_filter("Web OTA only", &["ota"]).save_file().map(|p|p.to_string_lossy().to_string())).await.ok().flatten()
}

fn suggested_patch_name(suggested_name: Option<&str>, release_path: Option<&str>) -> String {
    // Analyze once using verified release metadata; native dialog only sanitizes.
    let candidate = if let Some(name) = suggested_name.filter(|name| !name.trim().is_empty()) {
        name.to_owned()
    } else {
        let leaf = release_path.unwrap_or("").rsplit(['/', '\\']).next().unwrap_or("");
        let stem = if leaf.to_ascii_lowercase().ends_with(".zip") { &leaf[..leaf.len()-4] } else { leaf };
        if stem.is_empty() { "OpenWrt-patch.zip".into() } else { format!("OpenWrt-patch-{stem}.zip") }
    };
    let safe: String = candidate.chars().map(|c| if c.is_control() || "<>:\"/\\|?*".contains(c) { '_' } else { c }).collect();
    let safe = safe.trim().trim_end_matches(['.', ' ']);
    if safe.is_empty() { "OpenWrt-patch.zip".into() } else { safe.into() }
}
#[tauri::command]
async fn pick_recovery() -> Option<String> {
    tauri::async_runtime::spawn_blocking(|| rfd::FileDialog::new().set_title("Recovery folder / 完整事务与备份目录").pick_folder().map(|p|p.to_string_lossy().to_string())).await.ok().flatten()
}
#[tauri::command]
async fn save_backup() -> Option<String> {
    tauri::async_runtime::spawn_blocking(|| rfd::FileDialog::new().set_file_name("OpenWrt-Backup.zip").save_file().map(|p|p.to_string_lossy().to_string())).await.ok().flatten()
}
#[tauri::command]
async fn save_log(state: State<'_, Arc<Bridge>>) -> Result<Option<String>, String> {
    let result = tauri::async_runtime::spawn_blocking(|| rfd::FileDialog::new().set_file_name("OpenWrt-Installer.log").save_file()).await.ok().flatten();
    *state.export_log.lock().unwrap() = result.clone();
    Ok(result.map(|p|p.to_string_lossy().to_string()))
}
#[tauri::command]
fn write_log(state: State<'_, Arc<Bridge>>, path: String, text: String) -> Result<(), String> {
    let selected = state.export_log.lock().unwrap().take().ok_or("Select export destination first")?;
    if selected != PathBuf::from(path) || text.len() > 2_000_000 {return Err("Invalid log export".into());}
    std::fs::write(selected, text).map_err(|e|e.to_string())
}
#[tauri::command]
fn open_data(state: State<'_, Arc<Bridge>>) -> Result<(), String> {
    // Fixed directory, never a shell string supplied by the webview.
    open_native(&state.directory)?;
    Ok(())
}

#[tauri::command]
fn open_repository(repository: String) -> Result<(), String> {
    let url = match repository.as_str() {
        "e5" => "https://github.com/sprd-linux-devs/e5-linux",
        "mu300" => "https://github.com/dikeckaan/mu300-linux",
        _ => return Err("Unknown project repository".into()),
    };
    open_native(url)?;
    Ok(())
}

fn open_native(target: impl AsRef<std::ffi::OsStr>) -> Result<(), String> {
    #[cfg(target_os = "windows")] let opener = "explorer.exe";
    #[cfg(target_os = "macos")] let opener = "open";
    #[cfg(not(any(target_os = "windows", target_os = "macos")))] let opener = "xdg-open";
    Command::new(opener).arg(target).spawn().map_err(|e|e.to_string())?;
    Ok(())
}

fn data_directory() -> PathBuf {
    #[cfg(target_os = "macos")]
    return std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(std::env::temp_dir)
        .join("Library/Application Support/OpenWrt-Installer");
    #[cfg(not(target_os = "macos"))]
    return std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(std::env::temp_dir).join("OpenWrt-Installer");
}

fn distribution_root(executable: &std::path::Path) -> Result<PathBuf, String> {
    #[cfg(target_os = "macos")]
    for ancestor in executable.ancestors() {
        if ancestor.extension().is_some_and(|ext| ext == "app") {
            let root = ancestor.parent().ok_or("Application has no parent directory")?;
            if root.join("OpenWrt-Installer.py").is_file()
                && (root.join("desktop/manifest.json").is_file() || root.join("files/device-profile.json").is_file())
                || root.join("OpenWrt-Diff-Studio.py").is_file() || root.join("update.ota").is_file() {
                return Ok(root.to_path_buf());
            }
            // Finder may translocate quarantined applications. Ask for the
            // extracted package rather than guessing its original location.
            return rfd::FileDialog::new().set_title("Select extracted OpenWrt package / 选择完整解压的安装包")
                .pick_folder().ok_or_else(|| "No package selected".into());
        }
    }
    executable.parent().map(|p|p.to_path_buf()).ok_or_else(|| "Executable has no directory".into())
}

fn entry_page(executable: &std::path::Path, has_patch: bool) -> (&'static str, &'static str) {
    // Exported macOS updaters retain the studio's internal executable name.
    // Their enclosing app bundle is the public entry point instead.
    let entry = executable.ancestors().find(|p| p.extension().is_some_and(|ext| ext == "app"))
        .unwrap_or(executable).file_stem().unwrap_or_default().to_string_lossy().to_lowercase();
    if entry.contains("updater") {
        ("ota.html", "OpenWrt Patch Updater")
    } else if entry.contains("diff") {
        ("diff.html", "OpenWrt Diff Studio")
    } else if has_patch {
        ("ota.html", "OpenWrt Patch Updater")
    } else {
        ("index.html", "OpenWrt Installer")
    }
}

fn configure_entry(config: &mut tauri::Config, executable: &std::path::Path, has_patch: bool) {
    let (page, title) = entry_page(executable, has_patch);
    if let Some(window) = config.app.windows.iter_mut().find(|window| window.label == "main") {
        // Choose the asset before webview creation. During setup the webview
        // can still be about:blank, which cannot resolve relative URLs.
        window.url = tauri::WebviewUrl::App(PathBuf::from(page));
        window.title = title.into();
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let executable = std::env::current_exe()?;
    let root = distribution_root(&executable).map_err(std::io::Error::other)?;
    let mut context = tauri::generate_context!();
    configure_entry(context.config_mut(), &executable, root.join("update.ota").is_file());
    let directory = data_directory();
    let bridge = Arc::new(Bridge {input:Mutex::new(None),pending:Mutex::new(HashMap::new()),sequence:AtomicU64::new(1),busy:AtomicBool::new(false),directory,export_log:Mutex::new(None)});
    let setup_state = bridge.clone();
    let app = tauri::Builder::default().manage(bridge.clone())
        .invoke_handler(tauri::generate_handler![backend_request,pick_package,pick_ota_input,save_ota,pick_recovery,save_backup,save_log,write_log,open_data,open_repository])
        .setup(move |app| {
            #[cfg(target_os = "macos")]
            let backend = "tools/macos/backend/openwrt-backend";
            #[cfg(not(target_os = "macos"))]
            let backend = "tools/backend/openwrt-backend.exe";
            let mut process = Command::new(root.join(backend));
            process.args(["--package-root", &root.to_string_lossy(), "--data-dir", &setup_state.directory.to_string_lossy()]);
            process.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
            #[cfg(windows)] { use std::os::windows::process::CommandExt; process.creation_flags(0x08000000); }
            match process.spawn() {
                Ok(mut child) => {
                    *setup_state.input.lock().unwrap() = child.stdin.take();
                    let output = child.stdout.take().unwrap();
                    let state = setup_state.clone();
                    let handle = app.handle().clone();
                    std::thread::spawn(move || {
                        for line in BufReader::new(output).lines() {
                            let Ok(line) = line else {break};
                            let Ok(event) = serde_json::from_str::<Value>(&line) else {continue};
                            if event["type"] == "response" {
                                if let Some(id) = event["id"].as_u64() {
                                    if let Some(sender) = state.pending.lock().unwrap().remove(&id) {
                                        let result = if event["data"]["ok"] == true {Ok(event["data"]["result"].clone())} else {Err(event["data"]["error"].as_str().unwrap_or("Backend error").to_string())};
                                        let _ = sender.send(result);
                                    }
                                }
                            } else {
                                if event["type"] == "ota_activity" {
                                    state.busy.store(event["data"]["busy"].as_bool().unwrap_or(false), Ordering::SeqCst);
                                }
                                if event["type"] == "transaction" {
                                    let status = event["data"]["status"].as_str().unwrap_or("");
                                    state.busy.store(matches!(status,"running"|"waiting_user"|"waiting_device"),Ordering::SeqCst);
                                }
                                let _ = handle.emit("backend", event);
                            }
                        }
                        state.busy.store(false,Ordering::SeqCst);
                        state.pending.lock().unwrap().clear();
                        let _ = handle.emit("backend",json!({"type":"backend_exit","data":{}}));
                        let _ = child.wait();
                    });
                }
                Err(_) => {
                    rfd::MessageDialog::new().set_title("OpenWrt Installer").set_description("Backend is missing. Extract the complete package, including tools/backend.\n后端缺失，请完整解压安装包（含 tools/backend 目录）。").set_level(rfd::MessageLevel::Error).show();
                }
            }
            Ok(())
        })
        .on_window_event(|window,event| {
            if let tauri::WindowEvent::CloseRequested {api,..} = event {
                let state = window.state::<Arc<Bridge>>();
                if state.busy.load(Ordering::SeqCst) {
                    api.prevent_close();
                    let _ = window.emit("backend",json!({"type":"close_blocked","data":{}}));
                }
            }
        }).build(context)?;
    app.run(move |_, event| {
        if matches!(event,tauri::RunEvent::Exit) {bridge.input.lock().unwrap().take();}
    });
    Ok(())
}

fn main() {
    if let Err(error) = run() {
            #[cfg(target_os = "macos")]
            let message = format!("Cannot start OpenWrt Installer. Keep the app beside tools and files in the extracted package.\n无法启动安装器，请将 app 与 tools、files 一起保留在完整解压目录。\n\n{error}");
            #[cfg(not(target_os = "macos"))]
            let message = format!("Cannot start OpenWrt Installer. Keep the complete extracted package together and check that Microsoft Edge WebView2 Runtime is installed.\n无法启动程序。请保留完整解压目录，并检查是否已安装 Microsoft Edge WebView2 Runtime。\n\n{error}");
            rfd::MessageDialog::new().set_title("OpenWrt Installer").set_description(message).set_level(rfd::MessageLevel::Error).show();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn patch_name_uses_analyzed_identity_without_reparsing_the_zip_name() {
        let name = "OpenWrt-patch-T551M-25.12.5-k6.18.54-20261020.215049-gb8d4f222-to-20261021.075202-g99570375.zip";
        assert_eq!(suggested_patch_name(Some(name), Some("/release/renamed.zip")), name);
        assert_eq!(suggested_patch_name(Some("bad/name?.zip"), None), "bad_name_.zip");
    }

    #[test]
    fn patch_name_falls_back_without_errors_on_both_platforms() {
        for (path, name) in [
            (Some(r"D:\Releases\t551m-openwrt-r123-k6.18.54.zip"), "OpenWrt-patch-t551m-openwrt-r123-k6.18.54.zip"),
            (Some("/Users/test/MU300-发行包.ZIP"), "OpenWrt-patch-MU300-发行包.zip"),
            (Some("/release/new:bad?.zip"), "OpenWrt-patch-new_bad_.zip"),
            (Some(".zip"), "OpenWrt-patch.zip"),
            (None, "OpenWrt-patch.zip"),
        ] {
            assert_eq!(suggested_patch_name(None, path), name);
            assert_eq!(suggested_patch_name(Some("   "), path), name);
        }
    }

    #[test]
    fn updater_actions_are_exposed_by_native_bridge() {
        for command in ["ota_package", "ota_probe", "ota_apply", "ota_status", "ota_reboot"] {
            assert!(COMMANDS.contains(&command), "{command} missing from the native bridge");
        }
    }

    #[test]
    fn public_entry_selects_page() {
        for (path, patch, expected) in [
            ("/tools/OpenWrt-Installer.exe", false, "index.html"),
            ("/tools/OpenWrt-Installer.exe", true, "ota.html"),
            ("/tools/OpenWrt-Diff-Studio.exe", false, "diff.html"),
            ("/tools/OpenWrt-Diff-Studio.exe", true, "diff.html"),
            ("/tools/OpenWrt-Updater.exe", false, "ota.html"),
            ("/tools/OpenWrt-Updater.app/Contents/MacOS/openwrt-diff-studio", false, "ota.html"),
            ("/tools/OpenWrt-Diff-Studio.app/Contents/MacOS/openwrt-installer", false, "diff.html"),
        ] {
            assert_eq!(entry_page(Path::new(path), patch).0, expected, "{path}");
        }
    }

    #[test]
    fn initial_window_uses_app_asset_without_navigation() {
        let mut config = tauri::Config::default();
        let mut window = tauri::utils::config::WindowConfig::default();
        window.label = "main".into();
        config.app.windows.push(window);
        configure_entry(&mut config, Path::new("OpenWrt-Diff-Studio.exe"), false);
        assert!(matches!(&config.app.windows[0].url,
            tauri::WebviewUrl::App(path) if path == Path::new("diff.html")));
        assert_eq!(config.app.windows[0].title, "OpenWrt Diff Studio");
    }
}
