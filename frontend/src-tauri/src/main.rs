use std::path::PathBuf;
use tauri::{Manager, State};
use tauri_plugin_shell::ShellExt;

struct CredentialStore;

#[tauri::command]
fn save_credential(_state: State<'_, CredentialStore>, reference: String, secret: String) -> Result<(), String> {
    keyring::Entry::new("pm-pal", &reference).map_err(|e| e.to_string())?.set_password(&secret).map_err(|e| e.to_string())
}

#[tauri::command]
fn delete_credential(_state: State<'_, CredentialStore>, reference: String) -> Result<(), String> {
    keyring::Entry::new("pm-pal", &reference).map_err(|e| e.to_string())?.delete_credential().map_err(|e| e.to_string())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(CredentialStore)
        .setup(|app| {
            let data_dir: PathBuf = app.path().app_local_data_dir()?;
            std::fs::create_dir_all(&data_dir)?;
            let sidecar = app.shell().sidecar("pm-pal-api")
                .map_err(|e| std::io::Error::other(e.to_string()))?
                .env("PM_PAL_DATA_DIR", data_dir)
                .env("PM_PAL_HOST", "127.0.0.1")
                .env("PM_PAL_PORT", "8765")
                .spawn().map_err(|e| std::io::Error::other(e.to_string()))?;
            app.manage(sidecar);
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("failed to launch PM Pal");
}
