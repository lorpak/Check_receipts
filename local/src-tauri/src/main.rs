#![cfg_attr(
  all(not(debug_assertions), target_os = "windows"),
  windows_subsystem = "windows"
)]

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::thread;
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
  let rel_pythonw = PathBuf::from("resources").join("python").join("pythonw.exe");
  let rel_python = PathBuf::from("resources").join("python").join("python.exe");
  let rel_nested_pythonw = PathBuf::from("resources")
    .join("resources")
    .join("python")
    .join("pythonw.exe");
  let rel_nested_python = PathBuf::from("resources")
    .join("resources")
    .join("python")
    .join("python.exe");

  if let Some(resource_dir) = tauri::api::path::resource_dir(app.package_info(), &app.env()) {
    candidates.push(resource_dir.join("python").join("pythonw.exe"));
    candidates.push(resource_dir.join("resources").join("python").join("pythonw.exe"));
    candidates.push(
      resource_dir
        .join("resources")
        .join("resources")
        .join("python")
        .join("pythonw.exe"),
    );
    candidates.push(
      resource_dir
        .join("_up_")
        .join("resources")
        .join("python")
        .join("pythonw.exe"),
    );
    candidates.push(
      resource_dir
        .join("_up_")
        .join("resources")
        .join("resources")
        .join("python")
        .join("pythonw.exe"),
    );
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
      candidates.push(dir.join(&rel_pythonw));
      candidates.push(dir.join(&rel_nested_pythonw));
      candidates.push(dir.join("_up_").join(&rel_pythonw));
      candidates.push(dir.join("_up_").join(&rel_nested_pythonw));
      candidates.push(dir.join(&rel_python));
      candidates.push(dir.join(&rel_nested_python));
      candidates.push(dir.join("_up_").join(&rel_python));
      candidates.push(dir.join("_up_").join(&rel_nested_python));
      if !dir.pop() {
        break;
      }
    }
  }

  if let Ok(exe) = std::env::current_exe() {
    if let Some(mut dir) = exe.parent().map(|p| p.to_path_buf()) {
      for _ in 0..8 {
        candidates.push(dir.join(&rel_pythonw));
        candidates.push(dir.join(&rel_nested_pythonw));
        candidates.push(dir.join("_up_").join(&rel_pythonw));
        candidates.push(dir.join("_up_").join(&rel_nested_pythonw));
        candidates.push(dir.join(&rel_python));
        candidates.push(dir.join(&rel_nested_python));
        candidates.push(dir.join("_up_").join(&rel_python));
        candidates.push(dir.join("_up_").join(&rel_nested_python));
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

fn write_backend_error_status(
  status_path: &PathBuf,
  message: String,
  stdout_text: String,
  stderr_text: String,
  exit_code: Option<i32>,
) {
  let mut diagnostics = serde_json::Map::new();
  if let Some(code) = exit_code {
    diagnostics.insert("exit_code".into(), serde_json::json!(code));
  }
  if !stdout_text.trim().is_empty() {
    diagnostics.insert("backend_stdout".into(), serde_json::json!(stdout_text));
  }
  if !stderr_text.trim().is_empty() {
    diagnostics.insert("backend_stderr".into(), serde_json::json!(stderr_text));
  }

  let status = serde_json::json!({
    "is_processing": false,
    "message": message,
    "result_files": [],
    "result_full_paths": [],
    "result_file_paths": {},
    "diagnostics": diagnostics,
    "progress": 0,
    "has_error": true,
  });

  if let Ok(text) = serde_json::to_string(&status) {
    let _ = fs::write(status_path, text);
  }
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
    .current_dir(&app_dir)
    .stdin(Stdio::null())
    .stdout(Stdio::piped())
    .stderr(Stdio::piped());

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

  let child = cmd.spawn().map_err(|e| e.to_string())?;

  let status_path_for_thread = status_path.clone();
  thread::spawn(move || {
    match child.wait_with_output() {
      Ok(output) if !output.status.success() => {
        let stdout_text = String::from_utf8_lossy(&output.stdout).trim().to_string();
        let stderr_text = String::from_utf8_lossy(&output.stderr).trim().to_string();
        let fallback_message = if !stderr_text.is_empty() {
          format!("Ошибка обработки: {}", stderr_text.lines().next().unwrap_or("backend process failed"))
        } else if !stdout_text.is_empty() {
          format!("Ошибка обработки: {}", stdout_text.lines().next().unwrap_or("backend process failed"))
        } else {
          match output.status.code() {
            Some(code) => format!("Ошибка обработки: процесс завершился с кодом {}", code),
            None => "Ошибка обработки: backend process terminated".to_string(),
          }
        };
        write_backend_error_status(
          &status_path_for_thread,
          fallback_message,
          stdout_text,
          stderr_text,
          output.status.code(),
        );
      }
      Err(err) => {
        write_backend_error_status(
          &status_path_for_thread,
          format!("Ошибка запуска backend: {}", err),
          String::new(),
          String::new(),
          None,
        );
      }
      _ => {}
    }
  });

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
