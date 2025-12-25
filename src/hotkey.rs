// 全局热键管理
use tauri::{App, GlobalShortcutManager, Manager};

pub fn register_hotkeys(app: &App) -> Result<(), String> {
    let mut shortcut_mgr = app.global_shortcut_manager();
    let app_handle = app.handle();

    // 迷你搜索窗口热键候选（依次尝试，取第一个可用）
    let mini_candidates = [
        "alt+space",      // 常见但易被系统占用
        "ctrl+alt+f",     // 推荐优先
        "alt+f",          // 备用
        "ctrl+alt+s",     // 备用
        "ctrl+shift+q",   // 备用
    ];
    for &comb in &mini_candidates {
        let handle = app_handle.clone();
        match shortcut_mgr.register(comb, move || {
            if let Some(window) = handle.get_window("mini") {
                let is_visible = window.is_visible().unwrap_or(false);
                if is_visible {
                    let _ = window.hide();
                    log::info!("🔁 迷你窗口隐藏 (hotkey: {})", comb);
                } else {
                    let _ = window.show();
                    let _ = window.set_focus();
                    let _ = window.emit("focus-search", ());
                    log::info!("🔁 迷你窗口显示 (hotkey: {})", comb);
                }
            }
        }) {
            Ok(()) => {
                log::info!("✅ 迷你窗口热键已注册: {}", comb);
                break;
            }
            Err(e) => {
                log::warn!("热键 {} 不可用: {}", comb, e);
            }
        }
    }

    // 主窗口热键 Ctrl+Shift+Tab
    let main_candidates = ["ctrl+shift+tab", "ctrl+alt+s", "ctrl+shift+s"];
    for &comb in &main_candidates {
        let handle = app_handle.clone();
        match shortcut_mgr.register(comb, move || {
            if let Some(window) = handle.get_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }) {
            Ok(()) => {
                log::info!("✅ 主窗口热键已注册: {}", comb);
                break;
            }
            Err(e) => {
                log::warn!("热键 {} 不可用: {}", comb, e);
            }
        }
    }

    Ok(())
}
