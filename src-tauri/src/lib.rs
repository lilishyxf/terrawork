use std::sync::Mutex;
use tauri::Manager;
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

/// 持有后端子进程句柄,供退出时清理。
struct Backend(Mutex<Option<CommandChild>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .plugin(tauri_plugin_shell::init())
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }
      // 自启 Python 后端(PyInstaller sidecar);前端连 127.0.0.1:8000
      let sidecar = app
        .shell()
        .sidecar("terraworks-backend")
        .expect("缺少 terraworks-backend sidecar");
      let (mut rx, child) = sidecar.spawn().expect("无法启动后端 sidecar");
      // 排空后端 stdout/stderr,防管道缓冲写满阻塞
      tauri::async_runtime::spawn(async move {
        while rx.recv().await.is_some() {}
      });
      app.manage(Backend(Mutex::new(Some(child))));
      Ok(())
    })
    .build(tauri::generate_context!())
    .expect("error while building tauri application")
    .run(|app_handle, event| {
      // 应用退出时杀掉后端,避免遗留进程占 8000
      if let tauri::RunEvent::ExitRequested { .. } = event {
        if let Some(state) = app_handle.try_state::<Backend>() {
          if let Some(child) = state.0.lock().unwrap().take() {
            let _ = child.kill();
          }
        }
      }
    });
}
