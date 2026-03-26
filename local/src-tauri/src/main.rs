#![cfg_attr(
  all(not(debug_assertions), target_os = "windows"),
  windows_subsystem = "windows"
)]

use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use tauri::{AppHandle, Manager, State};

#[derive(Default)]
struct AppState {
  last_status_file: Mutex<Option<String>>,
}

fn resolve_app_py(app: &AppHandle) -> Result<PathBuf, String> {
  let mut candidates: Vec<PathBuf> = Vec::new();

  if let Some(resource_dir) = tauri::api::path::resource_dir(app.package_info(), &app.env()) {
    candidates.push(resource_dir.join("app.py"));
    candidates.push(resource_dir.join("_up_").join("app.py"));
  }

  if let Ok(cwd) = std::env::current_dir() {
    let mut dir = cwd;
    for _ in 0..6 {
      candidates.push(dir.join("app.py"));
      candidates.push(dir.join("resources").join("app.py"));
      candidates.push(dir.join("_up_").join("app.py"));
      candidates.push(dir.join("_up_").join("resources").join("app.py"));
      if !dir.pop() {
        break;
      }
    }
  }

  if let Ok(exe) = std::env::current_exe() {
    if let Some(mut dir) = exe.parent().map(|p| p.to_path_buf()) {
      for _ in 0..8 {
        candidates.push(dir.join("app.py"));
        candidates.push(dir.join("resources").join("app.py"));
        candidates.push(dir.join("_up_").join("app.py"));
        candidates.push(dir.join("_up_").join("resources").join("app.py"));
        if !dir.pop() {
          break;
        }
      }
    }
  }

  for path in &candidates {
    if path.exists() {
      return Ok(path.clone());
    }
  }

  let checked = candidates
    .iter()
    .map(|p| p.to_string_lossy().to_string())
    .collect::<Vec<_>>()
    .join("; ");

  Err(format!("Не найден app.py. Проверено: {}", checked))
}

fn resolve_python_exe(app: &AppHandle) -> Result<PathBuf, String> {
  let mut candidates: Vec<PathBuf> = Vec::new();
  let rel = PathBuf::from("resources").join("python").join("python.exe");
  let rel_nested = PathBuf::from("resources")
    .join("resources")
    .join("python")
    .join("python.exe");

  if let Some(resource_dir) = tauri::api::path::resource_dir(app.package_info(), &app.env()) {
    candidates.push(resource_dir.join("python").join("python.exe"));
    candidates.push(resource_dir.join("resources").join("python").join("python.exe"));
    candidates.push(
      resource_dir
        .join("resources")
        .join("resources")
        .join("python")
        .join("python.exe"),
    );
    candidates.push(
      resource_dir
        .join("_up_")
        .join("resources")
        .join("python")
        .join("python.exe"),
    );
    candidates.push(
      resource_dir
        .join("_up_")
        .join("resources")
        .join("resources")
        .join("python")
        .join("python.exe"),
    );
  }

  if let Ok(cwd) = std::env::current_dir() {
    let mut dir = cwd;
    for _ in 0..6 {
      candidates.push(dir.join(&rel));
      candidates.push(dir.join(&rel_nested));
      candidates.push(dir.join("_up_").join(&rel));
      candidates.push(dir.join("_up_").join(&rel_nested));
      if !dir.pop() {
        break;
      }
    }
  }

  if let Ok(exe) = std::env::current_exe() {
    if let Some(mut dir) = exe.parent().map(|p| p.to_path_buf()) {
      for _ in 0..8 {
        candidates.push(dir.join(&rel));
        candidates.push(dir.join(&rel_nested));
        candidates.push(dir.join("_up_").join(&rel));
        candidates.push(dir.join("_up_").join(&rel_nested));
        if !dir.pop() {
          break;
        }
      }
    }
  }

  candidates.push(PathBuf::from("python"));
  candidates.push(PathBuf::from("py"));

  for path in &candidates {
    if path.exists() {
      return Ok(path.clone());
    }
  }

  let checked = candidates
    .iter()
    .map(|p| p.to_string_lossy().to_string())
    .collect::<Vec<_>>()
    .join("; ");

  Err(format!(
    "Не найден python. Положите python.exe в resources/python/ или установите Python в PATH. Проверено: {}",
    checked
  ))
}

fn resolve_site_packages_dirs(app: &AppHandle) -> Vec<PathBuf> {
  let mut dirs: Vec<PathBuf> = Vec::new();
  let rel_site = PathBuf::from("resources")
    .join("python")
    .join("Lib")
    .join("site-packages");
  let rel_site_nested = PathBuf::from("resources")
    .join("resources")
    .join("python")
    .join("Lib")
    .join("site-packages");

  if let Some(resource_dir) = tauri::api::path::resource_dir(app.package_info(), &app.env()) {
    dirs.push(resource_dir.join("python").join("Lib").join("site-packages"));
    dirs.push(resource_dir.join("resources").join("python").join("Lib").join("site-packages"));
    dirs.push(
      resource_dir
        .join("resources")
        .join("resources")
        .join("python")
        .join("Lib")
        .join("site-packages"),
    );
    dirs.push(
      resource_dir
        .join("_up_")
        .join("resources")
        .join("python")
        .join("Lib")
        .join("site-packages"),
    );
    dirs.push(
      resource_dir
        .join("_up_")
        .join("resources")
        .join("resources")
        .join("python")
        .join("Lib")
        .join("site-packages"),
    );
  }

  if let Ok(cwd) = std::env::current_dir() {
    let mut dir = cwd;
    for _ in 0..6 {
      dirs.push(dir.join(&rel_site));
      dirs.push(dir.join(&rel_site_nested));
      dirs.push(dir.join("_up_").join(&rel_site));
      dirs.push(dir.join("_up_").join(&rel_site_nested));
      if !dir.pop() {
        break;
      }
    }
  }

  if let Ok(exe) = std::env::current_exe() {
    if let Some(mut dir) = exe.parent().map(|p| p.to_path_buf()) {
      for _ in 0..8 {
        dirs.push(dir.join(&rel_site));
        dirs.push(dir.join(&rel_site_nested));
        dirs.push(dir.join("_up_").join(&rel_site));
        dirs.push(dir.join("_up_").join(&rel_site_nested));
        if !dir.pop() {
          break;
        }
      }
    }
  }

  dirs
}

#[tauri::command]
fn start_processing(payload: String, app: AppHandle, state: State<AppState>) -> Result<String, String> {
  let app_py = resolve_app_py(&app)?;
  let temp_dir = std::env::temp_dir();
  let ts = SystemTime::now()
    .duration_since(UNIX_EPOCH)
    .map_err(|e| e.to_string())?
    .as_millis();

  let payload_path = temp_dir.join(format!("check_receipts_payload_{ts}.json"));
  let status_path = temp_dir.join(format!("check_receipts_status_{ts}.json"));

  fs::write(&payload_path, payload).map_err(|e| e.to_string())?;

  let python_exe = resolve_python_exe(&app)?;
  let app_dir = app_py.parent().map(|p| p.to_path_buf()).unwrap_or_else(|| PathBuf::from("."));

  let mut cmd = Command::new(&python_exe);
  cmd.arg(app_py)
    .arg("--payload-file")
    .arg(&payload_path)
    .arg("--status-file")
    .arg(&status_path)
    .current_dir(&app_dir);

  let mut pythonpath_parts: Vec<String> = Vec::new();
  pythonpath_parts.push(app_dir.to_string_lossy().to_string());
  for dir in resolve_site_packages_dirs(&app) {
    pythonpath_parts.push(dir.to_string_lossy().to_string());
  }
  let pythonpath = pythonpath_parts.join(";");
  cmd.env("PYTHONPATH", pythonpath);

  if let Some(parent) = python_exe.parent() {
    if parent.ends_with("python") {
      cmd.env("PYTHONHOME", parent);
    }
  }

  cmd.spawn().map_err(|e| e.to_string())?;

  let status_path_str = status_path.to_string_lossy().to_string();
  let mut guard = state.last_status_file.lock().map_err(|_| "State lock error".to_string())?;
  *guard = Some(status_path_str.clone());
  Ok(status_path_str)
}

#[tauri::command]
fn get_status(status_file: String) -> Result<serde_json::Value, String> {
  match fs::read_to_string(&status_file) {
    Ok(contents) => serde_json::from_str(&contents).map_err(|e| e.to_string()),
    Err(_) => Ok(serde_json::json!({
      "is_processing": true,
      "message": "Ожидание запуска...",
      "result_files": [],
      "result_full_paths": [],
      "result_file_paths": {},
      "diagnostics": {},
      "progress": 0,
      "has_error": false
    })),
  }
}

fn main() {
  tauri::Builder::default()
    .manage(AppState::default())
    .invoke_handler(tauri::generate_handler![start_processing, get_status])
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
