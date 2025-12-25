// Tauri 命令处理 - 调用lib.rs中的搜索索引功能
use serde::{Deserialize, Serialize};
use std::sync::Arc;

// 导入同一crate中的全局索引和内部函数
use crate::{SEARCH_INDICES, init_search_index_internal};
use crate::search_syntax::{SearchSyntaxParser, SearchFilters};

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

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct SearchResult {
    pub filename: String,
    pub fullpath: String,
    pub size: u64,
    pub mtime: u64,
    pub is_dir: bool,
}

/// 获取所有可用驱动器
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

/// 搜索文件（使用lib.rs中的搜索索引，支持流式输出）
#[tauri::command]
pub async fn search_files(
    window: tauri::Window,
    query: String,
    scope: Option<String>,
) -> Result<Vec<SearchResult>, String> {
    log::info!("🔍 搜索: query='{}', scope={:?}", query, scope);
    
    // 解析增强语法
    let (pure_keyword, filters) = SearchSyntaxParser::parse(&query);
    log::info!("📝 解析结果: 关键词='{}', 过滤器={:?}", pure_keyword, filters);
    
    // 确定要搜索的驱动器
    let drives = if let Some(scope_str) = scope {
        if scope_str == "all" || scope_str.is_empty() {
            get_all_drives().await?
        } else {
            vec![scope_str]
        }
    } else {
        get_all_drives().await?
    };

    log::info!("📂 将搜索 {} 个驱动器: {:?}", drives.len(), drives);

    // 为每个驱动器初始化索引
    for drive in &drives {
        let drive_char = drive.chars().next().ok_or("Invalid drive")?.to_ascii_uppercase();

        // 检查索引是否已存在
        let need_init = {
            let indices = SEARCH_INDICES.read();
            !indices.contains_key(&drive_char)
        };

        if need_init {
            log::info!("📊 正在为 {} 盘构建索引（首次使用，约需10-60秒）...", drive_char);
            
            // 调用lib.rs中的内部函数
            if init_search_index_internal(drive_char) {
                log::info!("✅ 驱动器 {} 索引构建完成", drive_char);
            } else {
                log::warn!("⚠️ 驱动器 {} 索引构建失败", drive_char);
                continue;
            }
        } else {
            log::info!("✓ 驱动器 {} 索引已就绪", drive_char);
        }
    }

    // 执行搜索 - 所有驱动器并行搜索，边搜边发送（最快速度）
    let keyword = pure_keyword.to_lowercase();
    let window_for_stream = window.clone();
    let drives_clone = drives.clone();
    let filters_clone = filters.clone();
    
    // 为所有驱动器并行搜索（每个盘一个独立任务）
    tokio::spawn(async move {
        use tokio::sync::mpsc;
        use std::sync::atomic::{AtomicUsize, Ordering};
        
        let (tx, mut rx) = mpsc::unbounded_channel::<Vec<SearchResult>>();
        let total_count = Arc::new(AtomicUsize::new(0));
        let active_tasks = Arc::new(AtomicUsize::new(0));
        
        // 为每个驱动器启动独立的搜索任务
        for drive in &drives_clone {
            let drive_char = match drive.chars().next() {
                Some(c) => c.to_ascii_uppercase(),
                None => continue,
            };
            
            // 获取索引
            let index = {
                let indices = SEARCH_INDICES.read();
                match indices.get(&drive_char) {
                    Some(idx) => Arc::clone(idx),
                    None => {
                        log::warn!("驱动器 {} 索引未就绪，跳过", drive_char);
                        continue;
                    }
                }
            };

            let keyword_clone = keyword.clone();
            let filters_clone = filters_clone.clone();
            let tx_clone = tx.clone();
            let total_count_clone = Arc::clone(&total_count);
            let active_tasks_clone = Arc::clone(&active_tasks);
            
            active_tasks.fetch_add(1, Ordering::SeqCst);
            
            // 每个驱动器独立并行搜索
            tokio::spawn(async move {
                log::info!("🔎 并行搜索 {} 盘: '{}'", drive_char, keyword_clone);
                
                // 搜索该驱动器
                let items = if keyword_clone.is_empty() {
                    index.search_contains("", 50000)
                } else {
                    index.search_contains(&keyword_clone, 10000)
                };
                log::info!("✅ {} 盘找到 {} 个匹配项", drive_char, items.len());
                
                // 转换并过滤
                let mut drive_results: Vec<SearchResult> = items.into_iter().map(|item| {
                    SearchResult {
                        filename: item.name,
                        fullpath: item.path,
                        size: item.size,
                        mtime: item.mtime as u64,
                        is_dir: item.is_dir,
                    }
                }).collect();
                
                drive_results = SearchSyntaxParser::apply_filters(drive_results, &filters_clone);
                log::info!("过滤后 {} 盘: {} 个结果", drive_char, drive_results.len());
                
                // 立即分批发送（通过 channel）
                const BATCH_SIZE: usize = 100;
                for chunk in drive_results.chunks(BATCH_SIZE) {
                    let batch: Vec<SearchResult> = chunk.to_vec();
                    total_count_clone.fetch_add(batch.len(), Ordering::SeqCst);
                    let _ = tx_clone.send(batch);
                }
                
                // 任务完成
                active_tasks_clone.fetch_sub(1, Ordering::SeqCst);
            });
        }
        
        drop(tx); // 关闭发送端
        
        // 接收并立即转发结果
        while let Some(batch) = rx.recv().await {
            log::info!("[PARALLEL] 收到批次: {} 个结果", batch.len());
            let _ = window_for_stream.emit("search-batch", &batch);
        }
        
        // 等待所有任务完成
        while active_tasks.load(Ordering::SeqCst) > 0 {
            tokio::time::sleep(tokio::time::Duration::from_millis(10)).await;
        }
        
        let final_count = total_count.load(Ordering::SeqCst);
        log::info!("🎯 所有驱动器并行搜索完成: 共 {} 个结果", final_count);
        let _ = window_for_stream.emit("search-complete", final_count);
    });

    // 立即返回，不等待搜索完成
    log::info!("✅ 搜索命令立即返回，所有盘并行边搜边发送中...");
    Ok(Vec::new())
}

/// 实时搜索（不需要索引，直接遍历文件系统，支持流式更新）
#[tauri::command]
pub async fn realtime_search(
    window: tauri::Window,
    query: String,
    scope: Option<String>,
) -> Result<Vec<SearchResult>, String> {
    use walkdir::WalkDir;
    use std::time::SystemTime;

    log::info!("🔍 实时搜索: query='{}', scope={:?}", query, scope);
    
    // 解析增强语法
    let (pure_keyword, filters) = SearchSyntaxParser::parse(&query);
    log::info!("📝 解析结果: 关键词='{}', 过滤器={:?}", pure_keyword, filters);
    
    let keyword = pure_keyword.to_lowercase();
    let mut all_results = Vec::new();
    let mut batch = Vec::new();
    const BATCH_SIZE: usize = 50;
    
    let search_paths = if let Some(scope_str) = scope {
        if scope_str == "all" || scope_str.is_empty() {
            get_all_drives().await?
        } else {
            vec![scope_str]
        }
    } else {
        get_all_drives().await?
    };

    let skip_dirs = [
        "windows", "program files", "program files (x86)", "programdata",
        "$recycle.bin", "system volume information", "appdata", "boot",
        "node_modules", ".git", "__pycache__", "site-packages", "sys",
        "recovery", "config.msi", "$windows.~bt", "$windows.~ws",
        "cache", "caches", "temp", "tmp", "logs", "log",
        ".vscode", ".idea", ".vs", "obj", "bin", "debug", "release",
        "packages", ".nuget", "bower_components",
    ];
    
    let skip_exts = [
        ".lsp", ".fas", ".lnk", ".html", ".htm", ".xml", ".ini", ".lsp_bak",
        ".cuix", ".arx", ".crx", ".fx", ".dbx", ".kid", ".ico", ".rz",
        ".dll", ".sys", ".tmp", ".log", ".dat", ".db", ".pdb", ".obj",
        ".pyc", ".class", ".cache", ".lock",
    ];

    for path in search_paths {
        log::info!("📂 实时扫描: {}", path);
        
        for entry in WalkDir::new(&path)
            .follow_links(false)
            .max_depth(20)
            .into_iter()
            .filter_entry(|e| {
                let name = e.file_name().to_string_lossy().to_lowercase();
                !skip_dirs.iter().any(|&d| name == d) && !name.starts_with('$')
            })
            .filter_map(|e| e.ok())
        {
            let file_name = entry.file_name().to_string_lossy().to_string();
            let path_lower = entry.path().to_string_lossy().to_lowercase();
            
            if skip_exts.iter().any(|&ext| path_lower.ends_with(ext)) {
                continue;
            }
            
            // 关键词匹配（如果有）
            if !keyword.is_empty() && !file_name.to_lowercase().contains(&keyword) {
                continue;
            }
            
            let metadata = match entry.metadata() {
                Ok(m) => m,
                Err(_) => continue,
            };
            
            let mtime = metadata.modified()
                .ok()
                .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
                .map(|d| d.as_secs())
                .unwrap_or(0);
                
            let result = SearchResult {
                filename: file_name,
                fullpath: entry.path().display().to_string(),
                size: metadata.len(),
                mtime,
                is_dir: metadata.is_dir(),
            };
            
            // 应用过滤器
            if !match_filters(&result, &filters) {
                continue;
            }
            
            all_results.push(result.clone());
            batch.push(result);
            
            // 流式发送批次
            if batch.len() >= BATCH_SIZE {
                log::info!("[STREAM] 发送批次: {} 个结果", batch.len());
                let _ = window.emit("search-batch", &batch);
                batch.clear();
            }
            
            if all_results.len() >= 10000 {
                log::info!("⚠️ 已达到结果上限 10000，停止搜索");
                break;
            }
        }
        
        // 发送剩余批次
        if !batch.is_empty() {
            log::info!("[STREAM] 发送最后批次: {} 个结果", batch.len());
            let _ = window.emit("search-batch", &batch);
            batch.clear();
        }
    }

    log::info!("🎯 实时搜索完成: 找到 {} 个结果", all_results.len());
    // 通知前端搜索完成
    let _ = window.emit("search-complete", all_results.len());
    Ok(all_results)
}

// 辅助函数：检查单个结果是否匹配过滤器
fn match_filters(item: &SearchResult, filters: &SearchFilters) -> bool {
    // 扩展名过滤
    if !filters.ext.is_empty() {
        let ext = std::path::Path::new(&item.filename)
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or("")
            .to_lowercase();
        if !filters.ext.contains(&ext) {
            return false;
        }
    }

    // 大小过滤
    if filters.size_min > 0 && item.size < filters.size_min {
        return false;
    }
    if filters.size_max > 0 && item.size > filters.size_max {
        return false;
    }

    // 日期过滤
    if let Some(date_after) = filters.date_after {
        if item.mtime < date_after {
            return false;
        }
    }

    // 路径过滤
    if !filters.path.is_empty() {
        let path_lower = item.fullpath.to_lowercase();
        let filter_lower = filters.path.to_lowercase();
        if !path_lower.contains(&filter_lower) {
            return false;
        }
    }

    // 文件名模式过滤
    if !filters.name_pattern.is_empty() {
        let pattern = filters.name_pattern.to_lowercase();
        let filename_lower = item.filename.to_lowercase();
        if !filename_lower.contains(&pattern) {
            return false;
        }
    }

    true
}

/// 强制构建索引（异步后台执行）
#[tauri::command]
pub async fn build_index(
    window: tauri::Window,
    scope: Option<String>,
) -> Result<String, String> {
    log::info!("🔨 开始强制构建索引, scope={:?}", scope);
    
    let drives = if let Some(scope_str) = scope {
        if scope_str == "all" || scope_str.is_empty() {
            get_all_drives().await?
        } else {
            vec![scope_str]
        }
    } else {
        get_all_drives().await?
    };

    // 使用 std::thread 在独立线程中执行重建，避免阻塞 tokio runtime
    std::thread::spawn(move || {
        let mut built_count = 0;
        let mut failed_count = 0;
        
        for drive in &drives {
            let drive_char = match drive.chars().next() {
                Some(c) => c.to_ascii_uppercase(),
                None => continue,
            };
            
            log::info!("📊 正在为 {} 盘强制重建索引（将删除旧文件）...", drive_char);
            
            // 发送进度事件到前端
            let _ = window.emit("index-building", serde_json::json!({
                "drive": drive_char.to_string(),
                "status": "building"
            }));
            
            // 使用强制重建函数（删除旧索引文件并重新构建）
            if crate::force_rebuild_search_index_internal(drive_char) {
                built_count += 1;
                log::info!("✅ 驱动器 {} 强制重建完成", drive_char);
                
                // 发送完成事件
                let _ = window.emit("index-building", serde_json::json!({
                    "drive": drive_char.to_string(),
                    "status": "completed"
                }));
            } else {
                failed_count += 1;
                log::warn!("⚠️ 驱动器 {} 强制重建失败", drive_char);
                
                // 发送失败事件
                let _ = window.emit("index-building", serde_json::json!({
                    "drive": drive_char.to_string(),
                    "status": "failed"
                }));
            }
        }

        // 发送总完成事件
        let message = if failed_count > 0 {
            format!("索引强制重建完成：成功 {}，失败 {}（详细信息请查看日志）", built_count, failed_count)
        } else {
            format!("索引强制重建完成：成功 {} 个驱动器", built_count)
        };
        
        let _ = window.emit("index-rebuild-finished", serde_json::json!({
            "success": built_count,
            "failed": failed_count,
            "message": message
        }));
    });

    Ok("索引重建已在后台启动，请稍候...".to_string())
}

/// 检查索引状态
#[tauri::command]
pub async fn check_index_status(scope: Option<String>) -> Result<serde_json::Value, String> {
    let drives = if let Some(scope_str) = scope {
        if scope_str == "all" || scope_str.is_empty() {
            get_all_drives().await?
        } else {
            vec![scope_str]
        }
    } else {
        get_all_drives().await?
    };

    let mut ready_count = 0;
    let mut total_files = 0u64;
    let mut loading_count = 0;
    let indices = SEARCH_INDICES.read();
    
    for drive in &drives {
        let drive_char = drive.chars().next().ok_or("Invalid drive")?.to_ascii_uppercase();
        
        // 检查内存中的索引
        if let Some(index) = indices.get(&drive_char) {
            ready_count += 1;
            total_files += index.item_count() as u64;
        } else {
            // 检查磁盘上是否有索引文件（说明正在加载中）
            let index_path = format!("{}:\\.search_index.bin", drive_char);
            if std::path::Path::new(&index_path).exists() {
                loading_count += 1;
            }
        }
    }
    
    Ok(serde_json::json!({
        "is_ready": ready_count > 0,
        "ready_count": ready_count,
        "total_drives": drives.len(),
        "total_files": total_files,
        "loading_count": loading_count,
        "status_text": if loading_count > 0 {
            format!("正在加载索引... ({}/{})", ready_count, drives.len())
        } else if ready_count > 0 {
            format!("索引就绪 ({} 个驱动器)", ready_count)
        } else {
            "索引未初始化".to_string()
        }
    }))
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

    Ok(())
}

#[tauri::command]
pub async fn delete_file(path: String) -> Result<(), String> {
    // 先从索引中移除（使用路径查找）
    if let Some(drive_char) = path.chars().next() {
        let drive = drive_char.to_ascii_uppercase();
        let indices = SEARCH_INDICES.read();
        if let Some(index) = indices.get(&drive) {
            if index.remove_file_by_path(&path) {
                log::info!("🗑️ 从索引中删除: {}", path);
                
                // 保存索引到磁盘
                let index_path = format!("{}:\\.search_index.bin", drive);
                let _ = index.save_to_file(std::path::Path::new(&index_path));
            } else {
                log::warn!("⚠️ 索引中未找到文件: {}", path);
            }
        }
    }
    
    // 再删除文件系统中的文件
    #[cfg(target_os = "windows")]
    {
        let ps_script = format!("Remove-Item -Path '{}' -Force -Recurse", path.replace("'", "''"));
        std::process::Command::new("powershell")
            .args(&["-NoProfile", "-Command", &ps_script])
            .output()
            .map_err(|e| e.to_string())?;
    }

    #[cfg(not(target_os = "windows"))]
    {
        std::fs::remove_file(&path).map_err(|e| e.to_string())?;
    }

    Ok(())
}

#[tauri::command]
pub async fn copy_to_clipboard(text: String) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        let ps_script = format!("Set-Clipboard -Value '{}'", text.replace("'", "''"));
        std::process::Command::new("powershell")
            .args(&["-NoProfile", "-Command", &ps_script])
            .output()
            .map_err(|e| e.to_string())?;
    }

    Ok(())
}

#[tauri::command]
pub async fn export_csv(results: Vec<SearchResult>) -> Result<(), String> {
    use std::fs::File;
    use std::io::Write;

    let desktop = dirs::desktop_dir().ok_or("无法获取桌面路径")?;
    let timestamp = chrono::Local::now().format("%Y%m%d_%H%M%S");
    let filename = format!("search_results_{}.csv", timestamp);
    let filepath = desktop.join(&filename);

    let mut file = File::create(&filepath).map_err(|e| format!("创建文件失败: {}", e))?;

    // 写入 UTF-8 BOM（Excel 识别UTF-8）
    file.write_all(&[0xEF, 0xBB, 0xBF]).map_err(|e| e.to_string())?;

    // 写入表头
    file.write_all(b"Filename,Size,Modified Time,Full Path\n").map_err(|e| e.to_string())?;

    let count = results.len();
    // 写入数据
    for result in &results {
        let line = format!(
            "\"{}\",{},{},\"{}\"\n",
            result.filename.replace("\"", "\"\""),
            result.size,
            result.mtime,
            result.fullpath.replace("\"", "\"\"")
        );
        file.write_all(line.as_bytes()).map_err(|e| e.to_string())?;
    }

    file.flush().map_err(|e| e.to_string())?;

    log::info!("Exported {} results to {}", count, filepath.display());

    // 打开文件所在文件夹
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer")
            .args(&["/select,", filepath.to_str().unwrap()])
            .spawn()
            .ok();
    }

    Ok(())
}

#[tauri::command]
pub async fn get_config(_key: String) -> Result<String, String> {
    // TODO: Implement config retrieval
    Ok(String::new())
}

#[tauri::command]
pub async fn set_config(_key: String, _value: String) -> Result<(), String> {
    // TODO: Implement config setting
    Ok(())
}

/// 启动 USN 文件监控
#[tauri::command]
pub async fn start_file_monitoring(window: tauri::Window, drives: Vec<String>) -> Result<(), String> {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::time::Duration;
    use std::fs;
    use std::path::Path;
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    
    log::info!("👁️ 启动文件监控: {:?}", drives);
    
    // 创建停止标志
    let stop_flag = Arc::new(AtomicBool::new(false));
    
    // 为每个驱动器启动监控
    for drive_str in drives {
        let drive_char = drive_str.chars().next().ok_or("Invalid drive")?.to_ascii_uppercase();
        let window_clone = window.clone();
        let stop_flag_clone = stop_flag.clone();
        
        // 在后台线程中监控
        tokio::spawn(async move {
            let mut last_usn = crate::get_current_usn(drive_char as u16);
            
            log::info!("📊 {} 盘初始 USN: {}", drive_char, last_usn);
            
            while !stop_flag_clone.load(Ordering::Relaxed) {
                tokio::time::sleep(Duration::from_secs(2)).await;
                
                // 获取当前 USN 并检查变化
                let current_usn = crate::get_current_usn(drive_char as u16);
                
                if current_usn > last_usn {
                    // 获取变化详情
                    let changes = crate::get_usn_changes(drive_char as u16, last_usn);
                    
                    let change_count = changes.count as i32;
                    if change_count > 0 {
                        log::info!("📁 {} 盘检测到 {} 个文件变化", drive_char, change_count);
                        
                        // 更新索引
                        let mut added_count = 0;
                        let mut deleted_count = 0;
                        
                        // 解析变化列表并更新索引
                        if changes.count > 0 {
                            // 访问FFI数据
                            let changes_vec = unsafe {
                                std::slice::from_raw_parts(changes.changes, changes.count)
                            };
                            
                            let indices = crate::SEARCH_INDICES.read();
                            if let Some(index) = indices.get(&drive_char) {
                                for change in changes_vec {
                                    // 获取路径
                                    let path = if change.path_ptr.is_null() {
                                        String::new()
                                    } else {
                                        let path_bytes = unsafe {
                                            std::slice::from_raw_parts(change.path_ptr, change.path_len)
                                        };
                                        String::from_utf8_lossy(path_bytes).to_string()
                                    };
                                    
                                    if path.is_empty() {
                                        continue;
                                    }
                                    
                                    // 0, 4 = 删除，1, 2, 3 = 添加/修改
                                    if change.action == 0 || change.action == 4 {
                                        // 文件被删除 - 使用路径删除
                                        if index.remove_file_by_path(&path) {
                                            deleted_count += 1;
                                            log::debug!("🗑️ 从索引删除: {}", path);
                                        }
                                    } else if change.action == 1 || change.action == 2 || change.action == 3 {
                                        // 文件被添加或修改
                                        if Path::new(&path).exists() {
                                            if let Ok(metadata) = fs::metadata(&path) {
                                                let filename = Path::new(&path)
                                                    .file_name()
                                                    .and_then(|n| n.to_str())
                                                    .unwrap_or("")
                                                    .to_string();
                                                
                                                // 使用路径哈希作为file_ref（与构建索引时不同，但用于增量添加）
                                                let mut hasher = DefaultHasher::new();
                                                path.hash(&mut hasher);
                                                let file_ref = hasher.finish();
                                                
                                                let name_lower = filename.to_lowercase();
                                                let parent_path = Path::new(&path).parent().map(|p| p.to_string_lossy().to_string()).unwrap_or_default();
                                                let mut parent_hasher = DefaultHasher::new();
                                                parent_path.hash(&mut parent_hasher);
                                                let parent_ref = parent_hasher.finish();
                                                
                                                let item = crate::search_index::IndexedItem {
                                                    name: filename,
                                                    name_lower,
                                                    path: path.clone(),
                                                    file_ref,
                                                    parent_ref,
                                                    size: metadata.len(),
                                                    mtime: metadata.modified()
                                                        .ok()
                                                        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                                                        .map(|d| d.as_secs_f64())
                                                        .unwrap_or(0.0),
                                                    is_dir: metadata.is_dir(),
                                                };
                                                
                                                index.add_file(item);
                                                added_count += 1;
                                                log::debug!("📝 添加到索引: {}", path);
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        
                        log::info!("📑 索引更新: +{} -{}", added_count, deleted_count);
                        
                        // NOTE: 前端已取消直接显示USN增量变化，此处不再发送file-changes事件
                        // 后端仍然继续监控USN并更新索引（无声模式）
                        // let _ = window_clone.emit("file-changes", serde_json::json!({
                        //     "drive": drive_char.to_string(),
                        //     "added": added_count,
                        //     "deleted": deleted_count,
                        //     "total": change_count
                        // }));
                        
                        // 释放内存
                        crate::free_change_list(changes);
                    }
                    
                    last_usn = current_usn;
                }
            }
            
            log::info!("🛑 {} 盘监控已停止", drive_char);
        });
    }
    
    Ok(())
}
