//! MFT (Master File Table) 扫描模块

use crate::database::FileEntry;
use crate::filter;
use rayon::prelude::*;
use std::collections::HashMap;
use std::ffi::OsString;
use std::os::windows::ffi::OsStringExt;
use std::path::Path;
use std::time::UNIX_EPOCH;
use windows::Win32::Foundation::*;
use windows::Win32::Storage::FileSystem::*;
use windows::Win32::System::Ioctl::*;
use windows::Win32::System::IO::*;

/// USN_JOURNAL_DATA 结构
#[repr(C)]
#[derive(Default)]
struct UsnJournalData {
    usn_journal_id: u64,
    first_usn: i64,
    next_usn: i64,
    lowest_valid_usn: i64,
    max_usn: i64,
    maximum_size: u64,
    allocation_delta: u64,
}

/// MFT_ENUM_DATA 结构
#[repr(C, packed)]
struct MftEnumData {
    start_file_reference_number: u64,
    low_usn: i64,
    high_usn: i64,
}

const FSCTL_QUERY_USN_JOURNAL: u32 = 0x000900F4;
const FSCTL_ENUM_USN_DATA: u32 = 0x000900B3;
const FILE_ATTRIBUTE_DIRECTORY: u32 = 0x10;

/// 扫描驱动器
pub fn scan_drive(
    drive: char,
    allowed_paths: Option<&[String]>,
) -> Result<Vec<FileEntry>, Box<dyn std::error::Error + Send + Sync>> {
    let drive_upper = drive.to_ascii_uppercase();
    let volume_path: Vec<u16> = format!("\\\\.\\{}:\0", drive_upper)
        .encode_utf16()
        .collect();
    
    // 打开卷
    let handle = unsafe {
        CreateFileW(
            windows::core::PCWSTR(volume_path.as_ptr()),
            GENERIC_READ.0 | GENERIC_WRITE.0,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )?
    };
    
    if handle.is_invalid() {
        return Err(format!("无法打开驱动器 {}", drive).into());
    }
    
    let result = scan_mft(handle, drive_upper, allowed_paths);
    
    unsafe { let _ = CloseHandle(handle); }
    
    result
}

fn scan_mft(
    handle: HANDLE,
    drive: char,
    allowed_paths: Option<&[String]>,
) -> Result<Vec<FileEntry>, Box<dyn std::error::Error + Send + Sync>> {
    // 查询 USN Journal
    let mut journal_data = UsnJournalData::default();
    let mut bytes_returned: u32 = 0;
    
    let success = unsafe {
        DeviceIoControl(
            handle,
            FSCTL_QUERY_USN_JOURNAL,
            None,
            0,
            Some(&mut journal_data as *mut _ as *mut _),
            std::mem::size_of::<UsnJournalData>() as u32,
            Some(&mut bytes_returned),
            None,
        )
    };
    
    // 修复：检查 Result 类型
    if success.is_err() {
        return Err("查询 USN Journal 失败".into());
    }
    
    // 枚举 MFT 记录
    let mut records: HashMap<u64, (String, u64, bool)> = HashMap::with_capacity(500000);
    let mut buffer = vec![0u8; 1024 * 1024]; // 1MB 缓冲区
    
    let mut enum_data = MftEnumData {
        start_file_reference_number: 0,
        low_usn: 0,
        high_usn: journal_data.next_usn,
    };
    
    loop {
        bytes_returned = 0;
        
        let success = unsafe {
            DeviceIoControl(
                handle,
                FSCTL_ENUM_USN_DATA,
                Some(&enum_data as *const _ as *const _),
                std::mem::size_of::<MftEnumData>() as u32,
                Some(buffer.as_mut_ptr() as *mut _),
                buffer.len() as u32,
                Some(&mut bytes_returned),
                None,
            )
        };
        
        // 修复：检查 Result 类型
        if success.is_err() || bytes_returned <= 8 {
            break;
        }
        
        // 获取下一个起始位置
        let next_frn = u64::from_le_bytes(buffer[0..8].try_into().unwrap());
        
        // 解析 USN 记录
        let mut offset = 8usize;
        while offset + 64 <= bytes_returned as usize {
            let record_len = u32::from_le_bytes(
                buffer[offset..offset + 4].try_into().unwrap()
            ) as usize;
            
            if record_len == 0 || offset + record_len > bytes_returned as usize {
                break;
            }
            
            // 解析记录（USN_RECORD_V2 结构）
            if record_len >= 60 {
                let file_ref = u64::from_le_bytes(
                    buffer[offset + 8..offset + 16].try_into().unwrap()
                ) & 0x0000FFFFFFFFFFFF;
                
                let parent_ref = u64::from_le_bytes(
                    buffer[offset + 16..offset + 24].try_into().unwrap()
                ) & 0x0000FFFFFFFFFFFF;
                
                let attrs = u32::from_le_bytes(
                    buffer[offset + 52..offset + 56].try_into().unwrap()
                );
                
                let name_len = u16::from_le_bytes(
                    buffer[offset + 56..offset + 58].try_into().unwrap()
                ) as usize;
                
                let name_offset = u16::from_le_bytes(
                    buffer[offset + 58..offset + 60].try_into().unwrap()
                ) as usize;
                
                if name_len > 0 && offset + name_offset + name_len <= bytes_returned as usize {
                    let name_bytes = &buffer[offset + name_offset..offset + name_offset + name_len];
                    
                    // UTF-16LE 解码
                    let name_u16: Vec<u16> = name_bytes
                        .chunks_exact(2)
                        .map(|c| u16::from_le_bytes([c[0], c[1]]))
                        .collect();
                    
                    let name = OsString::from_wide(&name_u16)
                        .to_string_lossy()
                        .into_owned();
                    
                    // 过滤系统文件
                    if !name.is_empty() && !name.starts_with('$') && !name.starts_with('.') {
                        let is_dir = (attrs & FILE_ATTRIBUTE_DIRECTORY) != 0;
                        records.insert(file_ref, (name, parent_ref, is_dir));
                    }
                }
            }
            
            offset += record_len;
        }
        
        enum_data.start_file_reference_number = next_frn;
    }
    
    // 构建完整路径
    let root_path = format!("{}:\\", drive);
    let entries = build_paths(&records, &root_path, drive, allowed_paths);
    
    Ok(entries)
}

/// 从 MFT 记录构建完整路径
fn build_paths(
    records: &HashMap<u64, (String, u64, bool)>,
    root_path: &str,
    drive: char,
    allowed_paths: Option<&[String]>,
) -> Vec<FileEntry> {
    // 分离目录和文件
    let mut dirs: HashMap<u64, (String, u64)> = HashMap::new();
    let mut files: HashMap<u64, (String, u64)> = HashMap::new();
    let mut parent_to_children: HashMap<u64, Vec<u64>> = HashMap::new();
    
    for (&ref_num, (name, parent_ref, is_dir)) in records {
        if *is_dir {
            dirs.insert(ref_num, (name.clone(), *parent_ref));
            parent_to_children
                .entry(*parent_ref)
                .or_default()
                .push(ref_num);
        } else {
            files.insert(ref_num, (name.clone(), *parent_ref));
        }
    }
    
    // BFS 构建目录路径
    let mut path_cache: HashMap<u64, String> = HashMap::new();
    path_cache.insert(5, root_path.to_string()); // 根目录的 MFT 引用号是 5
    
    let mut queue = std::collections::VecDeque::new();
    queue.push_back(5u64);
    
    let allowed_lower: Option<Vec<String>> = allowed_paths.map(|paths| {
        paths.iter().map(|p| p.to_lowercase()).collect()
    });
    
    while let Some(parent_ref) = queue.pop_front() {
        let parent_path = match path_cache.get(&parent_ref) {
            Some(p) => p.clone(),
            None => continue,
        };
        
        let parent_path_lower = parent_path.to_lowercase();
        
        // 检查是否应该跳过
        if filter::should_skip_path(&parent_path_lower, allowed_lower.as_deref()) {
            continue;
        }
        
        let parent_name_lower = Path::new(&parent_path)
            .file_name()
            .map(|n| n.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        
        if filter::should_skip_dir(&parent_name_lower) {
            continue;
        }
        
        if let Some(children) = parent_to_children.get(&parent_ref) {
            for &child_ref in children {
                if let Some((child_name, _)) = dirs.get(&child_ref) {
                    let child_path = format!("{}\\{}", parent_path.trim_end_matches('\\'), child_name);
                    path_cache.insert(child_ref, child_path);
                    queue.push_back(child_ref);
                }
            }
        }
    }
    
    // 构建结果列表
    let mut entries = Vec::with_capacity(records.len());
    
    // 添加目录
    for (&ref_num, (name, parent_ref)) in &dirs {
        if let Some(full_path) = path_cache.get(&ref_num) {
            if full_path == root_path {
                continue;
            }
            
            let parent_dir = path_cache.get(parent_ref).cloned().unwrap_or_default();
            let name_lower = name.to_lowercase();
            
            entries.push(FileEntry {
                name: name.clone(),
                name_lower,
                full_path: full_path.clone(),
                parent_dir,
                extension: String::new(),
                size: 0,
                mtime: 0.0,
                is_dir: true,
            });
        }
    }
    
    // 添加文件（并行获取大小）
    let file_entries: Vec<FileEntry> = files
        .par_iter()
        .filter_map(|(_, (name, parent_ref))| {
            let parent_path = path_cache.get(parent_ref)?;
            let full_path = format!("{}\\{}", parent_path.trim_end_matches('\\'), name);
            let full_path_lower = full_path.to_lowercase();
            
            // 检查是否应该跳过
            if filter::should_skip_path(&full_path_lower, allowed_lower.as_deref()) {
                return None;
            }
            
            // 检查 C 盘的允许路径
            if drive == 'C' || drive == 'c' {
                if let Some(ref allowed) = allowed_lower {
                    if !filter::is_in_allowed_paths(&full_path_lower, allowed) {
                        return None;
                    }
                }
            }
            
            let name_lower = name.to_lowercase();
            let extension = Path::new(name)
                .extension()
                .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
                .unwrap_or_default();
            
            // 检查扩展名
            if filter::should_skip_ext(&extension) {
                return None;
            }
            
            // 获取文件大小和修改时间
            let (size, mtime) = get_file_info(&full_path);
            
            Some(FileEntry {
                name: name.clone(),
                name_lower,
                full_path,
                parent_dir: parent_path.clone(),
                extension,
                size,
                mtime,
                is_dir: false,
            })
        })
        .collect();
    
    entries.extend(file_entries);
    entries
}

/// 获取文件大小和修改时间
fn get_file_info(path: &str) -> (u64, f64) {
    match std::fs::metadata(path) {
        Ok(meta) => {
            let size = meta.len();
            let mtime = meta
                .modified()
                .ok()
                .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                .map(|d| d.as_secs_f64())
                .unwrap_or(0.0);
            (size, mtime)
        }
        Err(_) => (0, 0.0),
    }
}