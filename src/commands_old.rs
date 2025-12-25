// Tauri 命令处理 - 使用lib.rs中的搜索索引
use serde::{Deserialize, Serialize};
use parking_lot::RwLock;
use std::sync::LazyLock;
use rustc_hash::FxHashMap;

// 直接调用lib.rs中的搜索索引函数（内部调用，不需要FFI）
use crate::search_index::{SearchIndex, IndexedItem, SEARCH_INDICES};

#[derive(Serialize, Deserialize, Debug)]
pub struct SearchRequest {
    pub keywords: Vec<String>,
    pub mode: SearchMode,
}

#[derive(Serialize, Deserialize, Debug)]
pub enum SearchMode {
    #[serde(rename = "index")]
    Index,
    #[serde(rename = "realtime")]
    Realtime,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct SearchResult {
    pub filename: String,
    pub fullpath: String,
    pub size: u64,
    pub mtime: u64,
    pub is_dir: bool,
}

// 获取所有可用磁盘
#[tauri::command]
pub async fn get_all_drives() -> Result<Vec<String>, String> {
    #[cfg(target_os = "windows")]
    {
        use std::path::Path;
        let drives = ('A'..='Z')
            .filter_map(|letter| {
                let drive = format!("{}:\\", letter);
                if Path::new(&drive).exists() {
                    Some(drive)
                } else {
                    None
                }
            })
            .collect();
        Ok(drives)
    }
    
    #[cfg(not(target_os = "windows"))]
    {
        Ok(vec![String::from("/")])
    }
}

#[tauri::command]
pub async fn search_files(
    query: String,
    scope: Option<String>,
) -> Result<Vec<SearchResult>, String> {
    let index = get_index();
    
    // 初始化索引（只在第一次调用时）
    let need_init = {
        let idx = index.lock().unwrap();
        idx.get_stats().total_files == 0
    };
    
    if need_init {
        log::info!("Building search index for all drives...");
        let drives = get_all_drives().await?;
        
        {
            let mut idx = index.lock().unwrap();
            for drive in &drives {
                log::info!("Scanning drive: {}", drive);
                if let Err(e) = idx.build_index(drive) {
                    log::warn!("Failed to scan {}: {}", drive, e);
                }
            }
            
            let stats = idx.get_stats();
            log::info!(
                "Index built: {} files, {} dirs, {:.2} GB total",
                stats.total_files,
                stats.total_dirs,
                stats.total_size as f64 / (1024.0 * 1024.0 * 1024.0)
            );
        }
    }

    // 执行搜索
    let idx = index.lock().unwrap();
    
    // 简单搜索：按关键词匹配
    let keywords = vec![query.clone()];
    let filters = SearchFilters {
        ext: None,
        size_min: None,
        size_max: None,
        only_dir: None,
    };
    let indexed_results = idx.search(&keywords, &filters);

    // 转换为 SearchResult
    let mut results: Vec<SearchResult> = indexed_results
        .into_iter()
        .map(|file| SearchResult {
            filename: file.name.clone(),
            fullpath: file.path.clone(),
            size: file.size,
            mtime: file.mtime,
            is_dir: file.is_dir,
        })
        .collect();
    
    // 如果指定了范围，过滤结果
    if let Some(scope_drive) = scope {
        if scope_drive != "all" {
            results.retain(|r| r.fullpath.starts_with(&scope_drive));
        }
    }

    Ok(results)
}

#[tauri::command]
pub async fn open_file(path: String) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("cmd")
            .args(&["/C", "start", "", &path])
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(&path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    Ok(())
}

#[tauri::command]
pub async fn locate_file(path: String) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer")
            .args(&["/select,", &path])
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .args(&["-R", &path])
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("nautilus")
            .arg(&path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    Ok(())
}

#[tauri::command]
pub async fn delete_file(path: String) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        // 尝试用 send2trash，否则直接删除
        let result = std::process::Command::new("powershell")
            .args(&["-Command", &format!("Remove-Item -Path '{}' -Force", path)])
            .output();

        if result.is_err() {
            std::fs::remove_file(&path).map_err(|e| e.to_string())?;
        }
    }

    #[cfg(not(target_os = "windows"))]
    {
        std::fs::remove_file(&path).map_err(|e| e.to_string())?;
    }

    Ok(())
}

#[tauri::command]
pub async fn get_config(_key: String) -> Result<String, String> {
    // TODO: 从配置文件读取
    Ok(String::new())
}

#[tauri::command]
pub async fn set_config(_key: String, _value: String) -> Result<(), String> {
    // TODO: 保存到配置文件
    Ok(())
}

#[tauri::command]
pub async fn copy_to_clipboard(text: String) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        use std::process::Command;
        let script = format!("Set-Clipboard -Value '{}'" , text.replace("'", "''"));
        Command::new("powershell")
            .args(&["-Command", &script])
            .output()
            .map_err(|e| e.to_string())?;
    }
    
    #[cfg(not(target_os = "windows"))]
    {
        // TODO: 支持 Linux/macOS 剪贴板
        return Err("Clipboard not supported on this platform".to_string());
    }
    
    Ok(())
}

#[tauri::command]
pub async fn export_csv(results: Vec<SearchResult>) -> Result<(), String> {
    use std::fs::File;
    use std::io::Write;
    
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    
    let filename = format!("search_results_{}.csv", timestamp);
    let desktop = dirs::desktop_dir().ok_or("Cannot find desktop directory")?;
    let filepath = desktop.join(&filename);
    
    let mut file = File::create(&filepath).map_err(|e| e.to_string())?;
    
    // 写入 UTF-8 BOM
    file.write_all(&[0xEF, 0xBB, 0xBF]).map_err(|e| e.to_string())?;
    
    // 写入表头
    writeln!(file, "文件名,完整路径,大小(字节),修改时间").map_err(|e| e.to_string())?;
    
    // 写入数据
    let count = results.len();
    for result in &results {
        writeln!(
            file,
            "\"{}\",\"{}\",{},{}",
            result.filename.replace("\"", "\"\""),
            result.fullpath.replace("\"", "\"\""),
            result.size,
            result.mtime
        ).map_err(|e| e.to_string())?;
    }
    
    log::info!("Exported {} results to {}", count, filepath.display());
    
    // 打开文件所在文件夹
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer")
            .args(&["/select,", &filepath.to_string_lossy()])
            .spawn()
            .ok();
    }
    
    Ok(())
}
