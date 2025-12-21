//! 文件扫描引擎 - Rust 实现

mod database;
mod filter;
mod mft;

use database::{Database, FileEntry};
use rayon::prelude::*;
use std::ffi::{c_char, CStr, CString};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

/// 扫描结果
#[repr(C)]
pub struct ScanResult {
    pub success: bool,
    pub file_count: u64,
    pub error_msg: *mut c_char,
}

impl Default for ScanResult {
    fn default() -> Self {
        Self {
            success: false,
            file_count: 0,
            error_msg: std::ptr::null_mut(),
        }
    }
}

/// 进度回调函数类型
pub type ProgressCallback = extern "C" fn(current: u64, total: u64, message: *const c_char);

/// 扫描所有驱动器并建立索引
#[no_mangle]
pub extern "C" fn scan_and_index(
    db_path: *const c_char,
    drives: *const c_char,
    c_allowed_paths: *const c_char,
    progress_callback: Option<ProgressCallback>,
) -> ScanResult {
    let result = std::panic::catch_unwind(|| {
        let db_path = unsafe { CStr::from_ptr(db_path).to_string_lossy().into_owned() };
        let drives = unsafe { CStr::from_ptr(drives).to_string_lossy().into_owned() };
        let c_paths = unsafe { CStr::from_ptr(c_allowed_paths).to_string_lossy().into_owned() };
        
        scan_and_index_impl(&db_path, &drives, &c_paths, progress_callback)
    });
    
    match result {
        Ok(Ok(count)) => ScanResult {
            success: true,
            file_count: count,
            error_msg: std::ptr::null_mut(),
        },
        Ok(Err(e)) => ScanResult {
            success: false,
            file_count: 0,
            error_msg: CString::new(e.to_string()).unwrap().into_raw(),
        },
        Err(_) => ScanResult {
            success: false,
            file_count: 0,
            error_msg: CString::new("Panic occurred").unwrap().into_raw(),
        },
    }
}

/// 重建单个驱动器索引
#[no_mangle]
pub extern "C" fn rebuild_drive_index(
    db_path: *const c_char,
    drive_letter: c_char,
    c_allowed_paths: *const c_char,
    progress_callback: Option<ProgressCallback>,
) -> ScanResult {
    let result = std::panic::catch_unwind(|| {
        let db_path = unsafe { CStr::from_ptr(db_path).to_string_lossy().into_owned() };
        let drive = drive_letter as u8 as char;
        let c_paths = unsafe { CStr::from_ptr(c_allowed_paths).to_string_lossy().into_owned() };
        
        rebuild_drive_impl(&db_path, drive, &c_paths, progress_callback)
    });
    
    match result {
        Ok(Ok(count)) => ScanResult {
            success: true,
            file_count: count,
            error_msg: std::ptr::null_mut(),
        },
        Ok(Err(e)) => ScanResult {
            success: false,
            file_count: 0,
            error_msg: CString::new(e.to_string()).unwrap().into_raw(),
        },
        Err(_) => ScanResult {
            success: false,
            file_count: 0,
            error_msg: CString::new("Panic occurred").unwrap().into_raw(),
        },
    }
}

/// 释放错误消息内存
#[no_mangle]
pub extern "C" fn free_error_msg(msg: *mut c_char) {
    if !msg.is_null() {
        unsafe {
            drop(CString::from_raw(msg));
        }
    }
}

/// 获取版本号
#[no_mangle]
pub extern "C" fn get_version() -> *const c_char {
    static VERSION: &[u8] = b"2.0.0\0";
    VERSION.as_ptr() as *const c_char
}

// ==================== 内部实现 ====================

fn send_progress(callback: Option<ProgressCallback>, current: u64, total: u64, message: &str) {
    if let Some(cb) = callback {
        if let Ok(msg) = CString::new(message) {
            cb(current, total, msg.as_ptr());
        }
    }
}

fn scan_and_index_impl(
    db_path: &str,
    drives: &str,
    c_allowed_paths: &str,
    progress_callback: Option<ProgressCallback>,
) -> Result<u64, Box<dyn std::error::Error + Send + Sync>> {
    send_progress(progress_callback, 0, 100, "初始化数据库...");
    
    // 1. 初始化数据库
    let mut db = Database::new(db_path)?;
    db.clear_all()?;
    
    // 2. 解析参数
    let drive_list: Vec<char> = drives
        .split(',')
        .filter_map(|s| s.trim().chars().next())
        .collect();
    
    let c_paths: Vec<String> = c_allowed_paths
        .split(',')
        .map(|s| s.trim().to_lowercase().replace('/', "\\").trim_end_matches('\\').to_string())
        .filter(|s| !s.is_empty())
        .collect();
    
    send_progress(progress_callback, 5, 100, &format!("准备扫描 {} 个驱动器...", drive_list.len()));
    
    // 3. 并行扫描所有驱动器
    let total_files = Arc::new(AtomicU64::new(0));
    let total_files_clone = Arc::clone(&total_files);
    
    let all_files: Vec<FileEntry> = drive_list
        .par_iter()
        .flat_map(|&drive| {
            let allowed = if drive == 'C' || drive == 'c' {
                Some(c_paths.as_slice())
            } else {
                None
            };
            
            match mft::scan_drive(drive, allowed) {
                Ok(files) => {
                    total_files_clone.fetch_add(files.len() as u64, Ordering::Relaxed);
                    files
                }
                Err(e) => {
                    eprintln!("扫描驱动器 {} 失败: {}", drive, e);
                    Vec::new()
                }
            }
        })
        .collect();
    
    let file_count = all_files.len() as u64;
    send_progress(progress_callback, 50, 100, &format!("扫描完成: {} 个文件，正在写入数据库...", file_count));
    
    // 4. 批量写入数据库
    let count = db.insert_batch(&all_files)?;
    
    send_progress(progress_callback, 80, 100, "正在构建全文索引...");
    
    // 5. 构建 FTS 索引
    db.build_fts()?;
    
    // 6. 恢复正常模式
    db.restore_normal_mode()?;
    
    send_progress(progress_callback, 100, 100, &format!("完成! 共索引 {} 个文件", count));
    
    Ok(count)
}

fn rebuild_drive_impl(
    db_path: &str,
    drive: char,
    c_allowed_paths: &str,
    progress_callback: Option<ProgressCallback>,
) -> Result<u64, Box<dyn std::error::Error + Send + Sync>> {
    send_progress(progress_callback, 0, 100, &format!("准备重建驱动器 {}...", drive));
    
    let mut db = Database::new(db_path)?;
    
    // 删除该驱动器的记录
    send_progress(progress_callback, 10, 100, "删除旧记录...");
    db.delete_drive(drive)?;
    
    // 解析 C 盘允许路径
    let c_paths: Vec<String> = c_allowed_paths
        .split(',')
        .map(|s| s.trim().to_lowercase().replace('/', "\\").trim_end_matches('\\').to_string())
        .filter(|s| !s.is_empty())
        .collect();
    
    let allowed = if drive == 'C' || drive == 'c' {
        Some(c_paths.as_slice())
    } else {
        None
    };
    
    // 扫描
    send_progress(progress_callback, 20, 100, "扫描文件系统...");
    let files = mft::scan_drive(drive, allowed)?;
    
    let file_count = files.len() as u64;
    send_progress(progress_callback, 60, 100, &format!("扫描完成: {} 个文件，正在写入...", file_count));
    
    // 写入
    let count = db.insert_batch(&files)?;
    
    // 恢复正常模式
    db.restore_normal_mode()?;
    
    send_progress(progress_callback, 100, 100, &format!("完成! 写入 {} 个文件", count));
    
    Ok(count)
}

// ==================== 兼容旧版 API ====================

/// 打包的扫描结果
#[repr(C)]
pub struct ScanResultPacked {
    pub data: *const u8,
    pub data_len: usize,
    pub count: usize,
}

/// 旧版扫描接口（保持兼容）
#[no_mangle]
pub extern "C" fn scan_drive_packed(drive_letter: u16) -> ScanResultPacked {
    let drive = char::from_u32(drive_letter as u32).unwrap_or('C');
    
    match mft::scan_drive(drive, None) {
        Ok(files) => {
            let packed = pack_files(&files);
            let len = packed.len();
            let count = files.len();
            let ptr = Box::into_raw(packed.into_boxed_slice()) as *const u8;
            
            ScanResultPacked {
                data: ptr,
                data_len: len,
                count,
            }
        }
        Err(_) => ScanResultPacked {
            data: std::ptr::null(),
            data_len: 0,
            count: 0,
        },
    }
}

fn pack_files(files: &[FileEntry]) -> Vec<u8> {
    let mut data = Vec::with_capacity(files.len() * 200);
    
    for file in files {
        // is_dir (1 byte)
        data.push(if file.is_dir { 1 } else { 0 });
        
        // name_len (2 bytes)
        let name_bytes = file.name.as_bytes();
        data.extend_from_slice(&(name_bytes.len() as u16).to_le_bytes());
        
        // name_lower_len (2 bytes)
        let name_lower_bytes = file.name_lower.as_bytes();
        data.extend_from_slice(&(name_lower_bytes.len() as u16).to_le_bytes());
        
        // path_len (2 bytes)
        let path_bytes = file.full_path.as_bytes();
        data.extend_from_slice(&(path_bytes.len() as u16).to_le_bytes());
        
        // parent_len (2 bytes)
        let parent_bytes = file.parent_dir.as_bytes();
        data.extend_from_slice(&(parent_bytes.len() as u16).to_le_bytes());
        
        // ext_len (1 byte)
        let ext_bytes = file.extension.as_bytes();
        data.push(ext_bytes.len() as u8);
        
        // 字符串数据
        data.extend_from_slice(name_bytes);
        data.extend_from_slice(name_lower_bytes);
        data.extend_from_slice(path_bytes);
        data.extend_from_slice(parent_bytes);
        data.extend_from_slice(ext_bytes);
    }
    
    data
}

#[no_mangle]
pub extern "C" fn free_scan_result(result: ScanResultPacked) {
    if !result.data.is_null() && result.data_len > 0 {
        unsafe {
            let _ = Box::from_raw(std::slice::from_raw_parts_mut(
                result.data as *mut u8,
                result.data_len,
            ));
        }
    }
}