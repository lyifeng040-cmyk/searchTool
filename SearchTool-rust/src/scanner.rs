// 文件扫描模块 - 集成USN Journal扫描引擎
use crate::database::{Database, FileRecord};
use crate::utils;
use anyhow::Result;
use std::sync::Arc;
use rayon::prelude::*;

// 从rust_project/src/lib.rs复制USN扫描代码
mod usn_engine {
    use parking_lot::RwLock;
    use rustc_hash::FxHashMap;
    use std::collections::VecDeque;
    use std::sync::{Arc, LazyLock};

    #[cfg(windows)]
    use windows::Win32::{
        Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE},
        Storage::FileSystem::{
            CreateFileW, FILE_FLAG_BACKUP_SEMANTICS, FILE_SHARE_DELETE,
            FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING,
        },
        System::IO::DeviceIoControl,
    };

    const GENERIC_READ: u32 = 0x80000000;

    // 目录缓存
    struct DirCache {
        paths: Arc<FxHashMap<u64, String>>,
        journal_id: u64,
        last_usn: i64,
    }

    static DIR_CACHE: LazyLock<RwLock<FxHashMap<char, DirCache>>> =
        LazyLock::new(|| RwLock::new(FxHashMap::default()));

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

    #[repr(C, packed)]
    struct MFT_ENUM_DATA_V0 {
        start_file_reference_number: u64,
        low_usn: i64,
        high_usn: i64,
    }

    #[repr(C)]
    struct USN_RECORD_V2 {
        record_length: u32,
        major_version: u16,
        minor_version: u16,
        file_reference_number: u64,
        parent_file_reference_number: u64,
        usn: i64,
        time_stamp: i64,
        reason: u32,
        source_info: u32,
        security_id: u32,
        file_attributes: u32,
        file_name_length: u16,
        file_name_offset: u16,
    }

    const FSCTL_QUERY_USN_JOURNAL: u32 = 0x000900f4;
    const FSCTL_ENUM_USN_DATA: u32 = 0x000900b3;
    const FILE_ATTRIBUTE_DIRECTORY: u32 = 0x10;
    const MFT_ENUM_BUFFER_SIZE: usize = 16 * 1024 * 1024;

    pub struct MftRecord {
        pub filename: String,
        pub parent_ref: u64,
        pub is_dir: bool,
        pub file_ref: u64,
    }

    #[cfg(windows)]
    pub fn scan_usn_journal_all(drive: char) -> Result<Vec<MftRecord>, Box<dyn std::error::Error>> {
        use std::ffi::OsStr;
        use std::os::windows::ffi::OsStrExt;

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
                FILE_FLAG_BACKUP_SEMANTICS,
                None,
            )?;

            if h == INVALID_HANDLE_VALUE {
                return Err("打开卷失败".into());
            }

            let mut jd: USN_JOURNAL_DATA_V0 = std::mem::zeroed();
            let mut br: u32 = 0;

            if DeviceIoControl(
                h,
                FSCTL_QUERY_USN_JOURNAL,
                None,
                0,
                Some(&mut jd as *mut _ as _),
                std::mem::size_of::<USN_JOURNAL_DATA_V0>() as u32,
                Some(&mut br),
                None,
            ).is_err()
            {
                CloseHandle(h);
                return Err("查询USN失败".into());
            }

            let mut records = Vec::with_capacity(800_000);
            let mut med = MFT_ENUM_DATA_V0 {
                start_file_reference_number: 0,
                low_usn: 0,
                high_usn: jd.next_usn,
            };
            let mut buf = vec![0u8; MFT_ENUM_BUFFER_SIZE];

            loop {
                if DeviceIoControl(
                    h,
                    FSCTL_ENUM_USN_DATA,
                    Some(&med as *const _ as _),
                    std::mem::size_of::<MFT_ENUM_DATA_V0>() as u32,
                    Some(buf.as_mut_ptr() as _),
                    buf.len() as u32,
                    Some(&mut br),
                    None,
                ).is_err() || br <= 8
                {
                    break;
                }

                med.start_file_reference_number = *(buf.as_ptr() as *const u64);
                let mut off = 8usize;

                while off < br as usize {
                    let rec = &*(buf.as_ptr().add(off) as *const USN_RECORD_V2);
                    if rec.record_length == 0 {
                        break;
                    }

                    let noff = off + rec.file_name_offset as usize;
                    let nlen = rec.file_name_length as usize;

                    if noff + nlen <= br as usize && nlen > 0 {
                        let slice = std::slice::from_raw_parts(
                            buf.as_ptr().add(noff) as *const u16,
                            nlen / 2,
                        );
                        if let Ok(name) = String::from_utf16(slice) {
                            let fc = name.as_bytes().first().copied().unwrap_or(b'$');
                            if fc != b'$' && fc != b'.' {
                                records.push(MftRecord {
                                    filename: name,
                                    parent_ref: rec.parent_file_reference_number & 0xFFFFFFFFFFFF,
                                    is_dir: (rec.file_attributes & FILE_ATTRIBUTE_DIRECTORY) != 0,
                                    file_ref: rec.file_reference_number & 0xFFFFFFFFFFFF,
                                });
                            }
                        }
                    }
                    off += rec.record_length as usize;
                }
            }

            CloseHandle(h);
            Ok(records)
        }
    }

    #[cfg(not(windows))]
    pub fn scan_usn_journal_all(_drive: char) -> Result<Vec<MftRecord>, Box<dyn std::error::Error>> {
        Err("USN Journal仅支持Windows系统".into())
    }

    pub fn build_path_map(records: &[MftRecord], drive: char) -> FxHashMap<u64, String> {
        let root = format!("{}:\\", drive);

        let mut p2c: FxHashMap<u64, Vec<usize>> = FxHashMap::default();
        p2c.reserve(records.len());
        for (i, r) in records.iter().enumerate() {
            if r.is_dir {
                p2c.entry(r.parent_ref).or_default().push(i);
            }
        }

        let mut paths: FxHashMap<u64, String> = FxHashMap::default();
        paths.reserve(records.len());
        paths.insert(5, root);

        let mut queue = VecDeque::with_capacity(2000);
        queue.push_back(5u64);

        let mut path_buf = String::with_capacity(512);

        while let Some(pid) = queue.pop_front() {
            let parent_path_owned = match paths.get(&pid) {
                Some(p) => p.clone(),
                None => continue,
            };
            let parent_trimmed = parent_path_owned.trim_end_matches('\\');

            if let Some(children) = p2c.get(&pid) {
                for &i in children {
                    let r = &records[i];

                    path_buf.clear();
                    path_buf.push_str(parent_trimmed);
                    path_buf.push('\\');
                    path_buf.push_str(&r.filename);

                    paths.insert(r.file_ref, path_buf.clone());
                    queue.push_back(r.file_ref);
                }
            }
        }

        paths
    }
}

pub struct Scanner {
    // 扫描器不需要状态
}

impl Scanner {
    pub fn new() -> Self {
        Self {}
    }

    /// 扫描整个驱动器并返回文件记录
    pub fn scan_drive(&self, drive: char, _progress_callback: Option<Box<dyn Fn(usize, &str) + Send>>) -> Result<Vec<FileRecord>> {
        log::info!("开始扫描驱动器: {} (使用原lib.rs引擎)", drive);

        // 直接调用原lib.rs的scan_and_pack
        let (packed_data, count) = crate::usn_engine::scan_and_pack(drive)
            .map_err(|e| anyhow::anyhow!("扫描失败: {}", e))?;
        
        log::info!("USN扫描完成，共 {} 条记录，数据大小 {} bytes", count, packed_data.len());

        // 按 Python 端的协议解包（不是 msgpack！）
        // [is_dir:1][name_len:2][path_len:2][parent_len:2][ext_len:1][size:8][mtime:8][data...]
        let mut off: usize = 0;
        let n = packed_data.len();
        let mut file_records: Vec<FileRecord> = Vec::with_capacity(count);

        while off < n {
            if off + 24 > n {
                break;
            }

            let is_dir = packed_data[off] != 0;
            let name_len = u16::from_le_bytes([packed_data[off + 1], packed_data[off + 2]]) as usize;
            let path_len = u16::from_le_bytes([packed_data[off + 3], packed_data[off + 4]]) as usize;
            let parent_len = u16::from_le_bytes([packed_data[off + 5], packed_data[off + 6]]) as usize;
            let ext_len = packed_data[off + 7] as usize;
            let size = u64::from_le_bytes([
                packed_data[off + 8],
                packed_data[off + 9],
                packed_data[off + 10],
                packed_data[off + 11],
                packed_data[off + 12],
                packed_data[off + 13],
                packed_data[off + 14],
                packed_data[off + 15],
            ]);
            let mtime = f64::from_le_bytes([
                packed_data[off + 16],
                packed_data[off + 17],
                packed_data[off + 18],
                packed_data[off + 19],
                packed_data[off + 20],
                packed_data[off + 21],
                packed_data[off + 22],
                packed_data[off + 23],
            ]);
            off += 24;

            let total_len = name_len + path_len + parent_len + ext_len;
            if off + total_len > n {
                break;
            }

            let name_bytes = &packed_data[off..off + name_len];
            off += name_len;
            let path_bytes = &packed_data[off..off + path_len];
            off += path_len;
            let parent_bytes = &packed_data[off..off + parent_len];
            off += parent_len;
            let ext_bytes = &packed_data[off..off + ext_len];
            off += ext_len;

            let filename = String::from_utf8_lossy(name_bytes).to_string();
            let full_path = String::from_utf8_lossy(path_bytes).to_string();
            let parent_dir = String::from_utf8_lossy(parent_bytes).to_string();
            let extension = if ext_len == 0 { String::new() } else { String::from_utf8_lossy(ext_bytes).to_string() };

            file_records.push(FileRecord {
                id: None,
                filename_lower: filename.to_lowercase(),
                filename,
                full_path,
                parent_dir,
                extension,
                size,
                mtime,
                is_dir,
            });
        }

        log::info!("转换完成，共 {} 条有效记录", file_records.len());

        Ok(file_records)
    }

    /// 扫描指定目录（实时扫描）
    pub fn scan_directory(&self, path: &str, recursive: bool) -> Result<Vec<FileRecord>> {
        use std::fs;
        use std::path::Path;

        let mut records = Vec::new();
        let path_obj = Path::new(path);

        if !path_obj.exists() {
            return Ok(records);
        }

        self.scan_dir_recursive(path_obj, recursive, &mut records)?;
        Ok(records)
    }

    fn scan_dir_recursive(&self, path: &std::path::Path, recursive: bool, records: &mut Vec<FileRecord>) -> Result<()> {
        use std::fs;

        if utils::should_skip_path_str(path.to_str().unwrap_or("")) {
            return Ok(());
        }

        let entries = match fs::read_dir(path) {
            Ok(e) => e,
            Err(_) => return Ok(()),
        };

        for entry in entries.flatten() {
            let path = entry.path();
            let metadata = match entry.metadata() {
                Ok(m) => m,
                Err(_) => continue,
            };

            let filename = path.file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("")
                .to_string();

            if filename.is_empty() || filename.starts_with('.') || filename.starts_with('$') {
                continue;
            }

            let full_path = path.to_str().unwrap_or("").to_string();
            let parent_dir = path.parent()
                .and_then(|p| p.to_str())
                .unwrap_or("")
                .to_string();

            let is_dir = metadata.is_dir();
            let extension = if is_dir {
                String::new()
            } else {
                utils::get_extension(&filename)
            };

            if !is_dir && utils::should_skip_ext(&extension) {
                continue;
            }

            let size = if is_dir { 0 } else { metadata.len() };
            let mtime = metadata.modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs_f64())
                .unwrap_or(0.0);

            records.push(FileRecord {
                id: None,
                filename: filename.clone(),
                filename_lower: filename.to_lowercase(),
                full_path: full_path.clone(),
                parent_dir,
                extension,
                size,
                mtime,
                is_dir,
            });

            if is_dir && recursive {
                let _ = self.scan_dir_recursive(&path, true, records);
            }
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_scanner_creation() {
        let scanner = Scanner::new();
        // 扫描器创建成功
    }
}

