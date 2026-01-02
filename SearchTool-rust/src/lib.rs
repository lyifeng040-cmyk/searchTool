// 极速文件搜索 V2.0 - Rust完全重写版
// 基于Tauri + Rust + USN Journal

mod config;
mod database;
mod scanner;
mod watcher;
mod commands;
mod hotkey;
mod utils;
mod usn_engine;
mod clipboard;

use tauri::{CustomMenuItem, Manager, SystemTray, SystemTrayEvent, SystemTrayMenu, SystemTrayMenuItem, WindowEvent};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use parking_lot::RwLock;
use parking_lot::Mutex;

pub use config::Config;
pub use database::Database;
pub use scanner::Scanner;
pub use watcher::FileWatcher;

static ALLOW_EXIT: AtomicBool = AtomicBool::new(false);

// 全局应用状态
pub struct AppState {
    pub config: Arc<RwLock<Config>>,
    pub database: Arc<Database>,
    pub scanner: Arc<Scanner>,
    pub watcher: Arc<RwLock<FileWatcher>>,
    pub mini_transfer: Arc<Mutex<Option<commands::MiniTransfer>>>,
}

impl AppState {
    pub fn new() -> anyhow::Result<Self> {
        let config = Arc::new(RwLock::new(Config::load()?));
        let database = Arc::new(Database::new()?);
        let scanner = Arc::new(Scanner::new());
        let watcher = Arc::new(RwLock::new(FileWatcher::new(Arc::clone(&database))));
        let mini_transfer = Arc::new(Mutex::new(None));

        Ok(Self {
            config,
            database,
            scanner,
            watcher,
            mini_transfer,
        })
    }
}

fn create_system_tray() -> SystemTray {
    let show = CustomMenuItem::new("show".to_string(), "显示/隐藏");
    let quit = CustomMenuItem::new("quit".to_string(), "退出");

    let tray_menu = SystemTrayMenu::new()
        .add_item(show)
        .add_native_item(SystemTrayMenuItem::Separator)
        .add_item(quit);

    SystemTray::new().with_menu(tray_menu)
}

fn show_and_focus_main(app: &tauri::AppHandle) {
    if let Some(w) = app.get_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn hide_main(app: &tauri::AppHandle) {
    if let Some(w) = app.get_window("main") {
        let _ = w.hide();
    }
}

pub fn run() {
    // 设置panic hook
    std::panic::set_hook(Box::new(|panic_info| {
        eprintln!("❌ 应用崩溃: {:?}", panic_info);
        if let Some(location) = panic_info.location() {
            eprintln!("位置: {}:{}:{}", location.file(), location.line(), location.column());
        }
    }));

    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    log::info!("🚀 初始化应用状态...");

    let app_state = match AppState::new() {
        Ok(state) => {
            log::info!("✅ 应用状态初始化成功");
            state
        }
        Err(e) => {
            eprintln!("❌ 应用状态初始化失败: {}", e);
            panic!("Failed to initialize app state: {}", e);
        }
    };

    tauri::Builder::default()
        .manage(app_state)
        .system_tray(create_system_tray())
        .on_system_tray_event(|app, event| match event {
            SystemTrayEvent::LeftClick { .. } => {
                show_and_focus_main(app);
            }
            SystemTrayEvent::MenuItemClick { id, .. } => match id.as_str() {
                "show" => {
                    if let Some(w) = app.get_window("main") {
                        if w.is_visible().unwrap_or(true) {
                            hide_main(app);
                        } else {
                            show_and_focus_main(app);
                        }
                    } else {
                        show_and_focus_main(app);
                    }
                }
                "quit" => {
                    ALLOW_EXIT.store(true, Ordering::SeqCst);
                    app.exit(0);
                }
                _ => {}
            },
            _ => {}
        })
        .on_window_event(|event: tauri::GlobalWindowEvent| {
            // 关闭按钮：默认隐藏到托盘（不退出）
            if let WindowEvent::CloseRequested { api, .. } = event.event() {
                if !ALLOW_EXIT.load(Ordering::SeqCst) {
                    api.prevent_close();
                    let _ = event.window().hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            commands::search_files,
            commands::search_realtime,
            commands::get_file_info,
            commands::get_file_info_batch,
            commands::scan_drive,
            commands::rebuild_index,
            commands::get_index_stats,
            commands::get_config,
            commands::save_config,
            commands::add_favorite,
            commands::remove_favorite,
            commands::get_favorites,
            commands::add_history,
            commands::get_history,
            commands::export_results,
            commands::scan_large_files,
            commands::batch_rename,
            commands::delete_files,
            commands::copy_files,
            commands::open_file,
            commands::open_folder,
            commands::get_drives,
            commands::start_watcher,
            commands::stop_watcher,
            commands::get_usn_changes,
            commands::show_main_and_search,
            commands::show_main_with_results,
            commands::mini_prepare_transfer,
            commands::mini_take_transfer,
            commands::show_main_only,
            commands::hide_mini,
            commands::set_mini_expanded,
            commands::start_drag_mini,
            commands::clipboard_copy_files,
            commands::clipboard_cut_files,
            commands::clipboard_set_text,
            commands::reveal_in_folder,
        ])
        .setup(|app: &mut tauri::App| {
            log::info!("🚀 Tauri setup开始...");

            // 全局热键（Windows：Ctrl+Shift+Alt+Space 呼出 mini 窗口）
            let cfg = app.state::<AppState>().config.read().clone();
            if cfg.enable_global_hotkey {
                let app_handle = app.handle();
                std::thread::spawn(move || {
                    let mut hk = crate::hotkey::windows_hotkey::HotkeyManager::new();
                    if let Err(e) = hk.start() {
                        log::warn!("全局热键启动失败: {}", e);
                        return;
                    }
                    loop {
                        if ALLOW_EXIT.load(Ordering::SeqCst) {
                            break;
                        }
                        for ev in hk.get_events() {
                            match ev {
                                crate::hotkey::windows_hotkey::HotkeyEvent::ShowMain => {
                                    // 切换 mini 窗口显示/隐藏并聚焦
                                    if let Some(mini) = app_handle.get_window("mini") {
                                        if mini.is_visible().unwrap_or(false) {
                                            let _ = mini.hide();
                                        } else {
                                            // 打开前先收起到默认高度，避免布局残留
                                            let _ = mini.emit("mini-opened", serde_json::json!({}));
                                            let _ = mini.show();
                                            let _ = mini.set_focus();
                                            let _ = mini.center();
                                        }
                                    }
                                }
                            }
                        }
                        std::thread::sleep(std::time::Duration::from_millis(50));
                    }
                });
            }

            // 增量索引：USN watcher（失败自动降级，不影响主功能）
            if cfg.auto_start_watcher {
                let drives = crate::utils::get_drives();
                if !drives.is_empty() {
                    let st = app.state::<AppState>();
                    let mut w = st.watcher.write();
                    if let Err(e) = w.start(drives, Some(app.handle())) {
                        log::warn!("USN watcher 启动失败（已降级）：{}", e);
                    }
                }
            }

            log::info!("✅ Tauri setup完成");
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("❌ Tauri构建失败")
        .run(|_app_handle, event| match event {
            tauri::RunEvent::ExitRequested { api, .. } => {
                if !ALLOW_EXIT.load(Ordering::SeqCst) {
                    log::info!("退出请求（托盘托底）");
                    api.prevent_exit();
                }
            }
            tauri::RunEvent::Exit => {
                log::info!("应用退出");
            }
            _ => {}
        });
    
    log::info!("应用已关闭");
}

