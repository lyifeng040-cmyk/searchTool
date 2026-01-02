// 文件监控模块 - USN Journal实时监控
use crate::database::Database;
use anyhow::Result;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};
use crossbeam_channel::{unbounded, Sender, Receiver};
use tauri::Manager;

#[derive(Debug, Clone, serde::Serialize)]
pub enum FileChangeType {
    Created,
    Modified,
    Deleted,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct FileChange {
    pub path: String,
    pub change_type: FileChangeType,
    pub is_dir: bool,
}

pub struct FileWatcher {
    database: Arc<Database>,
    running: bool,
    stop_flag: Arc<AtomicBool>,
    tx: Option<Sender<FileChange>>,
    rx: Option<Receiver<FileChange>>,
    thread_handle: Option<thread::JoinHandle<()>>,
}

impl FileWatcher {
    pub fn new(database: Arc<Database>) -> Self {
        Self {
            database,
            running: false,
            stop_flag: Arc::new(AtomicBool::new(false)),
            tx: None,
            rx: None,
            thread_handle: None,
        }
    }

    /// 启动文件监控
    pub fn start(&mut self, drives: Vec<String>, app: Option<tauri::AppHandle>) -> Result<()> {
        if self.running {
            log::warn!("文件监控已在运行");
            return Ok(());
        }

        let (tx, rx) = unbounded();
        self.tx = Some(tx.clone());
        self.rx = Some(rx);

        let database = Arc::clone(&self.database);
        let stop_flag = Arc::clone(&self.stop_flag);
        stop_flag.store(false, Ordering::SeqCst);
        
        #[cfg(windows)]
        {
            self.thread_handle = Some(thread::spawn(move || {
                Self::watch_loop_windows(drives, tx, database, stop_flag, app);
            }));
        }

        #[cfg(not(windows))]
        {
            log::warn!("USN监控仅支持Windows系统");
        }

        self.running = true;
        log::info!("✅ 文件监控已启动");
        Ok(())
    }

    /// 停止文件监控
    pub fn stop(&mut self) {
        if !self.running {
            return;
        }

        self.running = false;
        self.stop_flag.store(true, Ordering::SeqCst);
        
        if let Some(handle) = self.thread_handle.take() {
            let _ = handle.join();
        }

        self.tx = None;
        self.rx = None;

        log::info!("⏹ 文件监控已停止");
    }

    /// 获取文件变更
    pub fn get_changes(&self) -> Vec<FileChange> {
        let mut changes = Vec::new();
        
        if let Some(rx) = &self.rx {
            while let Ok(change) = rx.try_recv() {
                changes.push(change);
            }
        }

        changes
    }

    #[cfg(windows)]
    fn watch_loop_windows(
        drives: Vec<String>,
        tx: Sender<FileChange>,
        database: Arc<Database>,
        stop_flag: Arc<AtomicBool>,
        app: Option<tauri::AppHandle>,
    ) {
        use std::os::raw::c_char;

        // 用 usn_engine 内置的 UsnMonitor（已经实现了 FSCTL_READ_USN_JOURNAL）
        let mut monitors: Vec<(String, *mut crate::usn_engine::UsnMonitor)> = Vec::new();
        for d in drives {
            let drive_letter = d.chars().next().unwrap_or('C').to_ascii_uppercase();
            // 先探测该盘是否支持 USN（非 NTFS/网络盘/移动盘通常为 0）
            let jid = crate::usn_engine::get_usn_journal_id(drive_letter as u16);
            if jid == 0 {
                log::warn!("USN 不可用/不支持：{}（跳过增量监控，索引数不会自动变化）", d);
                continue;
            }
            let mon = unsafe { crate::usn_engine::create_usn_monitor(drive_letter as u8 as c_char) };
            if mon.is_null() {
                log::warn!("USN monitor 创建失败: {}", d);
                continue;
            }
            monitors.push((d, mon));
        }

        if monitors.is_empty() {
            log::warn!("USN 监控未启动：没有可用的 monitor（可能权限/卷不可用），自动降级为空操作");
            return;
        }

        let mut last_emit = Instant::now() - Duration::from_secs(60);
        while !stop_flag.load(Ordering::SeqCst) {
            thread::sleep(Duration::from_millis(600));

            let mut any_db_changed = false;
            for (_d, mon) in &monitors {
                let list = unsafe { crate::usn_engine::get_changes(*mon) };
                if list.changes.is_null() || list.count == 0 {
                    continue;
                }
                let list_count = list.count;

                // 先把变更转换出来（因为 free_change_list 会释放底层 path_ptr）
                let mut out_changes: Vec<FileChange> = Vec::with_capacity(list.count);
                let mut upserts: Vec<crate::database::FileRecord> = Vec::new();
                let mut did_change = false;

                unsafe {
                    let slice = std::slice::from_raw_parts(list.changes, list.count);
                    for c in slice {
                        let path = if !c.path_ptr.is_null() && c.path_len > 0 {
                            let ps = std::slice::from_raw_parts(c.path_ptr, c.path_len);
                            std::str::from_utf8(ps).unwrap_or("").to_string()
                        } else {
                            String::new()
                        };
                        if path.is_empty() {
                            continue;
                        }

                        let is_dir = c.is_dir != 0;
                        let change_type = match c.action {
                            1 => FileChangeType::Created,
                            2 => FileChangeType::Modified,
                            _ => FileChangeType::Deleted,
                        };

                        // 增量写库：delete / upsert
                        match change_type {
                            FileChangeType::Deleted => {
                                if is_dir {
                                    // 目录删除：删除前缀（含自身与子项）
                                    // 注意：prefix 只会命中子项，不一定命中“目录本身那条记录”，所以两步都做
                                    if let Ok(n0) = database.delete_file(&path) {
                                        if n0 > 0 {
                                            did_change = true;
                                        }
                                    }
                                    let prefix = format!("{}\\", path.trim_end_matches('\\'));
                                    if let Ok(n1) = database.delete_by_prefix(&prefix) {
                                        if n1 > 0 {
                                            did_change = true;
                                        }
                                    }
                                } else {
                                    if let Ok(n) = database.delete_file(&path) {
                                        if n > 0 {
                                            did_change = true;
                                        } else {
                                            // 常见问题：路径大小写/格式不一致导致删不到
                                            log::debug!("USN 删除未命中（可能路径大小写不同）: {}", path);
                                        }
                                    }
                                }
                            }
                            FileChangeType::Created | FileChangeType::Modified => {
                                // 用 fast native 获取 size/mtime（失败则保守写 0）
                                let (size, mtime) = if is_dir {
                                    (0u64, 0f64)
                                } else {
                                    crate::usn_engine::get_file_info_fast_native(&path).unwrap_or((0, 0.0))
                                };
                                let filename = std::path::Path::new(&path)
                                    .file_name()
                                    .and_then(|n| n.to_str())
                                    .unwrap_or("")
                                    .to_string();
                                if filename.is_empty() {
                                    continue;
                                }
                                let parent_dir = std::path::Path::new(&path)
                                    .parent()
                                    .and_then(|p| p.to_str())
                                    .unwrap_or("")
                                    .to_string();
                                let ext = if is_dir { String::new() } else { crate::utils::get_extension(&filename) };

                                upserts.push(crate::database::FileRecord {
                                    id: None,
                                    filename: filename.clone(),
                                    filename_lower: filename.to_lowercase(),
                                    full_path: path.clone(),
                                    parent_dir,
                                    extension: ext,
                                    size,
                                    mtime,
                                    is_dir,
                                });
                                did_change = true;
                            }
                        }

                        out_changes.push(FileChange { path, change_type, is_dir });
                    }
                }

                // 释放底层 USN 引擎的分配
                unsafe { crate::usn_engine::free_change_list(list) };

                // 批量 upsert（用 batch_insert 达到 INSERT OR REPLACE）
                if !upserts.is_empty() {
                    if let Ok(n) = database.batch_insert(&upserts) {
                        if n > 0 {
                            did_change = true;
                        }
                    }
                }

                for ch in out_changes {
                    let _ = tx.send(ch);
                }

                any_db_changed = any_db_changed || did_change;
                if did_change {
                    log::info!("USN 增量: changes={}, upserts={}, db_changed=1", list_count, upserts.len());
                }
            }

            // 方案 A：增量落库后主动通知 UI 刷新索引状态（节流，避免频繁刷新）
            if any_db_changed && last_emit.elapsed() >= Duration::from_millis(500) {
                if let Some(app) = &app {
                    let _ = app.emit_all("refresh-status", serde_json::json!({}));
                }
                log::info!("USN: 已通知 UI 刷新索引状态（refresh-status）");
                last_emit = Instant::now();
            }
        }

        // 清理 monitor
        for (_d, mon) in monitors {
            unsafe { crate::usn_engine::destroy_usn_monitor(mon) };
        }
    }

    #[cfg(windows)]
    fn get_current_usn_windows(drive: char) -> Result<i64> {
        use std::ffi::OsStr;
        use std::os::windows::ffi::OsStrExt;
        use windows::Win32::{
            Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE},
            Storage::FileSystem::{
                CreateFileW, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING,
            },
            System::IO::DeviceIoControl,
        };

        const GENERIC_READ: u32 = 0x80000000;
        const FSCTL_QUERY_USN_JOURNAL: u32 = 0x000900f4;

        #[repr(C, packed)]
        struct USN_JOURNAL_DATA_V0 {
            usn_journal_id: u64,
            first_usn: i64,
            next_usn: i64,
            lowest_valid_usn: i64,
            max_usn: i64,
            maximum_size: u64,
            allocation_delta: u64,
        }

        let volume: Vec<u16> = format!("\\\\.\\{}:", drive)
            .encode_utf16()
            .chain(Some(0))
            .collect();

        unsafe {
            let h = CreateFileW(
                windows::core::PCWSTR(volume.as_ptr()),
                GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                Default::default(),
                None,
            )?;

            if h == INVALID_HANDLE_VALUE {
                return Err(anyhow::anyhow!("打开卷失败"));
            }

            let mut jd: USN_JOURNAL_DATA_V0 = std::mem::zeroed();
            let mut br: u32 = 0;

            let result = if DeviceIoControl(
                h,
                FSCTL_QUERY_USN_JOURNAL,
                None,
                0,
                Some(&mut jd as *mut _ as _),
                std::mem::size_of::<USN_JOURNAL_DATA_V0>() as u32,
                Some(&mut br),
                None,
            ).is_ok()
            {
                Ok(jd.next_usn)
            } else {
                Err(anyhow::anyhow!("查询USN失败"))
            };

            CloseHandle(h);
            result
        }
    }

    #[cfg(windows)]
    fn get_usn_changes_windows(drive: char, last_usn: i64) -> Result<Vec<FileChange>> {
        // TODO: 实现USN变更读取
        // 这里需要实现完整的USN_READ逻辑，类似lib.rs中的get_changes_since
        Ok(Vec::new())
    }
}

impl Drop for FileWatcher {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_watcher_creation() {
        use crate::database::Database;
        let db = Arc::new(Database::new().unwrap());
        let watcher = FileWatcher::new(db);
        assert!(!watcher.running);
    }
}

