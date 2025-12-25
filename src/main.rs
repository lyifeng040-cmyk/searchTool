// 极速文件搜索 - Rust 全栈版本
// 使用 Tauri 作为 UI 框架

// 导入library crate中的commands模块
use file_scanner_engine::commands;

// mod tray;  // 暂时禁用托盘功能
mod hotkey;

fn main() {
    env_logger::init();

    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            commands::search_files,
            commands::realtime_search,
            commands::build_index,
            commands::check_index_status,
            commands::get_all_drives,
            commands::open_file,
            commands::locate_file,
            commands::delete_file,
            commands::copy_to_clipboard,
            commands::export_csv,
            commands::get_config,
            commands::set_config,
            commands::start_file_monitoring,
        ])
        // .system_tray(tray::create_system_tray())  // 暂时禁用以避免 TaskDialogIndirect 依赖
        // .on_system_tray_event(tray::handle_tray_event)
        .setup(|app| {
            // 注册全局快捷键
            hotkey::register_hotkeys(app)?;
            
            // 🚀 启动时预加载所有驱动器索引（常驻内存）
            std::thread::spawn(|| {
                log::info!("🚀 启动索引预加载...");
                
                // 获取所有驱动器
                let drives: Vec<char> = ('C'..='Z')
                    .filter(|&letter| {
                        let drive = format!("{}:\\", letter);
                        std::path::Path::new(&drive).exists()
                    })
                    .collect();
                
                log::info!("📂 检测到 {} 个驱动器: {:?}", drives.len(), drives);
                
                // 为每个驱动器加载索引
                for drive in drives {
                    match file_scanner_engine::init_search_index_internal(drive) {
                        true => log::info!("✅ {} 盘索引已加载到内存", drive),
                        false => log::warn!("⚠️ {} 盘索引加载失败", drive),
                    }
                }
                
                log::info!("🎉 所有索引加载完成，已常驻内存");
            });
            
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
