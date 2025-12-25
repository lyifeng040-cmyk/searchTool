// file_scanner_engine/src/lib.rs
// 极速文件搜索 V53 - Arc + 搜索索引 + 增量更新
// 2025-12-24

pub mod search_index;
pub mod commands;
pub mod config;
pub mod shortcuts;
pub mod index_engine;
pub mod search_syntax;

use parking_lot::RwLock;
use rayon::prelude::*;
use rustc_hash::{FxHashMap, FxHashSet};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::ffi::{CStr, OsStr};
use std::fs;
use std::os::raw::c_char;
use std::os::windows::ffi::OsStrExt;
use std::path::Path;
use std::sync::{Arc, LazyLock};

use search_index::{IndexedItem, SearchIndex};

// ============== 全局目录缓存 ==============

struct DirCache {
    paths: Arc<FxHashMap<u64, Arc<String>>>,
    journal_id: u64,
    last_usn: i64,
}

static DIR_CACHE: LazyLock<RwLock<FxHashMap<char, DirCache>>> =
    LazyLock::new(|| RwLock::new(FxHashMap::default()));

#[derive(Serialize, Deserialize)]
struct PersistDirCacheV1 {
    version: u32,
    drive: u8,
    journal_id: u64,
    paths: Vec<(u64, String)>,
}

// ============== FFI 结构 ==============

#[repr(C)]
pub struct ScanResult {
    pub data: *mut u8,
    pub data_len: usize,
    pub count: usize,
}

#[repr(C)]
pub struct FileChange {
    pub action: u8,
    pub is_dir: u8,
    pub path_ptr: *mut u8,
    pub path_len: usize,
}

#[repr(C)]
pub struct ChangeList {
    pub changes: *mut FileChange,
    pub count: usize,
}

#[repr(C)]
pub struct UsnChangeResult {
    pub data: *mut u8,
    pub data_len: usize,
    pub count: usize,
    pub next_usn: i64,
}

#[repr(C)]
pub struct FileInfo {
    pub size: u64,
    pub mtime: f64,
    pub exists: u8,
}

// ============== 搜索索引 FFI 结构 ==============

#[repr(C)]
pub struct SearchResultFFI {
    pub items: *mut SearchItemFFI,
    pub count: usize,
}

#[repr(C)]
pub struct SearchItemFFI {
    pub name_ptr: *mut u8,
    pub name_len: usize,
    pub path_ptr: *mut u8,
    pub path_len: usize,
    pub size: u64,
    pub is_dir: u8,
    pub mtime: f64,
}

// ============== 全局搜索索引缓存 ==============

pub static SEARCH_INDICES: LazyLock<RwLock<FxHashMap<char, Arc<SearchIndex>>>> =
    LazyLock::new(|| RwLock::new(FxHashMap::default()));

// ============== 过滤规则 ==============

const SKIP_DIRS: &[&str] = &[
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "system volume information",
    "appdata",
    "boot",
    "node_modules",
    ".git",
    "__pycache__",
    "site-packages",
    "sys",
    "recovery",
    "config.msi",
    "$windows.~bt",
    "$windows.~ws",
    "cache",
    "caches",
    "temp",
    "tmp",
    "logs",
    "log",
    ".vscode",
    ".idea",
    ".vs",
    "obj",
    "bin",
    "debug",
    "release",
    "packages",
    ".nuget",
    "bower_components",
];

const SKIP_EXTS: &[&str] = &[
    ".lsp", ".fas", ".lnk", ".html", ".htm", ".xml", ".ini", ".lsp_bak", ".cuix", ".arx", ".crx",
    ".fx", ".dbx", ".kid", ".ico", ".rz", ".dll", ".sys", ".tmp", ".log", ".dat", ".db", ".pdb",
    ".obj", ".pyc", ".class", ".cache", ".lock",
];

#[inline]
fn build_skip_dirs_set() -> FxHashSet<&'static str> {
    SKIP_DIRS.iter().copied().collect()
}

#[inline]
fn build_skip_exts_set() -> FxHashSet<&'static str> {
    SKIP_EXTS.iter().copied().collect()
}

#[inline]
fn is_cad_path(s: &str) -> bool {
    let bytes = s.as_bytes();
    bytes
        .windows(6)
        .any(|w| w.eq_ignore_ascii_case(b"cad201") || w.eq_ignore_ascii_case(b"cad202"))
        || bytes.windows(11).any(|w| {
            w.eq_ignore_ascii_case(b"autocad_201") || w.eq_ignore_ascii_case(b"autocad_202")
        })
        || bytes.windows(7).any(|w| w.eq_ignore_ascii_case(b"tangent"))
}

#[inline]
fn should_skip_dir(name_lower: &str, skip_dirs: &FxHashSet<&str>) -> bool {
    skip_dirs.contains(name_lower) || is_cad_path(name_lower)
}

#[inline]
fn should_skip_ext_fast(filename: &str, skip_exts: &FxHashSet<&str>) -> bool {
    if let Some(pos) = filename.rfind('.') {
        let ext = &filename[pos..];
        if ext.len() <= 10 {
            let mut buf = [0u8; 10];
            let ext_bytes = ext.as_bytes();
            for (i, &b) in ext_bytes.iter().enumerate() {
                buf[i] = b.to_ascii_lowercase();
            }
            let ext_lower = unsafe { std::str::from_utf8_unchecked(&buf[..ext_bytes.len()]) };
            return skip_exts.contains(ext_lower);
        }
    }
    false
}

#[inline]
fn get_ext_lower(filename: &str) -> String {
    if let Some(pos) = filename.rfind('.') {
        filename[pos..].to_ascii_lowercase()
    } else {
        String::new()
    }
}

#[inline]
fn is_recycle_bin_path(path: &str) -> bool {
    let bytes = path.as_bytes();
    bytes.len() >= 12 && bytes.windows(12).any(|w| w.eq_ignore_ascii_case(b"$recycle.bin"))
}

// ============== Windows API ==============

#[repr(C)]
struct WIN32_FILE_ATTRIBUTE_DATA {
    file_attributes: u32,
    creation_time_low: u32,
    creation_time_high: u32,
    last_access_time_low: u32,
    last_access_time_high: u32,
    last_write_time_low: u32,
    last_write_time_high: u32,
    file_size_high: u32,
    file_size_low: u32,
}

extern "system" {
    fn GetFileAttributesExW(
        lpFileName: *const u16,
        fInfoLevelId: i32,
        lpFileInformation: *mut WIN32_FILE_ATTRIBUTE_DATA,
    ) -> i32;

    fn GetFinalPathNameByHandleW(
        hFile: isize,
        lpszFilePath: *mut u16,
        cchFilePath: u32,
        dwFlags: u32,
    ) -> u32;
}

const EPOCH_DIFF: u64 = 116444736000000000;

#[inline]
fn get_file_info_fast(path: &str) -> Option<(u64, f64)> {
    let wide: Vec<u16> = OsStr::new(path)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();

    let mut data = std::mem::MaybeUninit::<WIN32_FILE_ATTRIBUTE_DATA>::uninit();

    unsafe {
        if GetFileAttributesExW(wide.as_ptr(), 0, data.as_mut_ptr()) != 0 {
            let data = data.assume_init();
            let size = ((data.file_size_high as u64) << 32) | (data.file_size_low as u64);
            let mtime_ft =
                ((data.last_write_time_high as u64) << 32) | (data.last_write_time_low as u64);
            let mtime = if mtime_ft > EPOCH_DIFF {
                (mtime_ft - EPOCH_DIFF) as f64 / 10_000_000.0
            } else {
                0.0
            };
            Some((size, mtime))
        } else {
            None
        }
    }
}

fn get_path_by_file_ref_with_handle(
    volume_handle: windows_sys::Win32::Foundation::HANDLE,
    file_ref: u64,
) -> Option<String> {
    use windows_sys::Win32::Foundation::*;
    use windows_sys::Win32::Storage::FileSystem::*;

    unsafe {
        #[repr(C)]
        struct FILE_ID_DESCRIPTOR {
            dw_size: u32,
            id_type: u32,
            file_id: u64,
        }

        let desc = FILE_ID_DESCRIPTOR {
            dw_size: std::mem::size_of::<FILE_ID_DESCRIPTOR>() as u32,
            id_type: 0,
            file_id: file_ref,
        };

        let file_h = OpenFileById(
            volume_handle,
            &desc as *const _ as *const _,
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            std::ptr::null(),
            FILE_FLAG_BACKUP_SEMANTICS,
        );

        if file_h == INVALID_HANDLE_VALUE {
            return None;
        }

        let mut path_buf = [0u16; 520];
        let len = GetFinalPathNameByHandleW(file_h as isize, path_buf.as_mut_ptr(), 520, 0);

        CloseHandle(file_h);

        if len > 0 && len < 520 {
            let path = String::from_utf16_lossy(&path_buf[..len as usize]);
            Some(path.trim_start_matches("\\\\?\\").to_string())
        } else {
            None
        }
    }
}

// ============== USN 结构 ==============

struct MftRecord {
    filename: String,
    parent_ref: u64,
    is_dir: bool,
    file_ref: u64,
}

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

#[repr(C, packed)]
struct READ_USN_JOURNAL_DATA_V0 {
    start_usn: i64,
    reason_mask: u32,
    return_only_on_close: u32,
    timeout: u64,
    bytes_to_wait_for: u64,
    usn_journal_id: u64,
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
const FSCTL_READ_USN_JOURNAL: u32 = 0x000900bb;
const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x02000000;
const FILE_FLAG_SEQUENTIAL_SCAN: u32 = 0x08000000;

const USN_REASON_FILE_CREATE: u32 = 0x00000100;
const USN_REASON_FILE_DELETE: u32 = 0x00000200;
const USN_REASON_DATA_EXTEND: u32 = 0x00000002;
const USN_REASON_DATA_OVERWRITE: u32 = 0x00000001;
const USN_REASON_RENAME_OLD_NAME: u32 = 0x00001000;
const USN_REASON_RENAME_NEW_NAME: u32 = 0x00002000;
const USN_REASON_CLOSE: u32 = 0x80000000;

const FILE_ATTRIBUTE_DIRECTORY: u32 = 0x10;

// Buffer 大小（优化点2：增大 USN buffer）
const MFT_ENUM_BUFFER_SIZE: usize = 16 * 1024 * 1024;
const USN_READ_BUFFER_SIZE: usize = 256 * 1024;
const USN_QUICK_BUFFER_SIZE: usize = 4 * 1024 * 1024;

// ============== FFI 导出：扫描 ==============

#[no_mangle]
pub extern "C" fn scan_drive_packed(drive_letter: u16) -> ScanResult {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();
    match scan_and_pack(drive) {
        Ok((data, count)) => {
            let len = data.len();
            let ptr = Box::into_raw(data.into_boxed_slice()) as *mut u8;
            ScanResult {
                data: ptr,
                data_len: len,
                count,
            }
        }
        Err(_) => ScanResult {
            data: std::ptr::null_mut(),
            data_len: 0,
            count: 0,
        },
    }
}

// 优化点1：对称释放 Box<[u8]>
#[no_mangle]
pub extern "C" fn free_scan_result(result: ScanResult) {
    if !result.data.is_null() && result.data_len > 0 {
        unsafe {
            let slice = std::slice::from_raw_parts_mut(result.data, result.data_len);
            let _ = Box::<[u8]>::from_raw(slice);
        }
    }
}

// ============== FFI 导出：懒加载文件信息 ==============

#[no_mangle]
pub extern "C" fn get_file_info(path_ptr: *const u8, path_len: usize) -> FileInfo {
    if path_ptr.is_null() || path_len == 0 {
        return FileInfo {
            size: 0,
            mtime: 0.0,
            exists: 0,
        };
    }

    let path = unsafe {
        let slice = std::slice::from_raw_parts(path_ptr, path_len);
        match std::str::from_utf8(slice) {
            Ok(s) => s,
            Err(_) => {
                return FileInfo {
                    size: 0,
                    mtime: 0.0,
                    exists: 0,
                }
            }
        }
    };

    match get_file_info_fast(path) {
        Some((size, mtime)) => FileInfo {
            size,
            mtime,
            exists: 1,
        },
        None => FileInfo {
            size: 0,
            mtime: 0.0,
            exists: 0,
        },
    }
}

#[no_mangle]
pub extern "C" fn get_file_info_batch(
    paths_ptr: *const u8,
    paths_len: usize,
    results_ptr: *mut FileInfo,
    max_count: usize,
) -> usize {
    if paths_ptr.is_null() || paths_len == 0 || results_ptr.is_null() || max_count == 0 {
        return 0;
    }

    let paths_data = unsafe { std::slice::from_raw_parts(paths_ptr, paths_len) };
    let paths_str = match std::str::from_utf8(paths_data) {
        Ok(s) => s,
        Err(_) => return 0,
    };

    let paths: Vec<&str> = paths_str.split('\0').filter(|p| !p.is_empty()).collect();
    let count = paths.len().min(max_count);

    let results: Vec<FileInfo> = paths[..count]
        .par_iter()
        .map(|path| match get_file_info_fast(path) {
            Some((size, mtime)) => FileInfo {
                size,
                mtime,
                exists: 1,
            },
            None => FileInfo {
                size: 0,
                mtime: 0.0,
                exists: 0,
            },
        })
        .collect();

    unsafe {
        for (i, info) in results.into_iter().enumerate() {
            *results_ptr.add(i) = info;
        }
    }

    count
}

// ============== FFI 导出：USN 相关 ==============

#[no_mangle]
pub extern "C" fn get_current_usn(drive_letter: u16) -> i64 {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();
    get_next_usn(drive).unwrap_or(-1)
}

#[no_mangle]
pub extern "C" fn get_usn_changes(drive_letter: u16, last_usn: i64) -> ChangeList {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();
    match get_changes_since(drive, last_usn) {
        Ok(changes) => {
            let count = changes.len();
            if count == 0 {
                return ChangeList {
                    changes: std::ptr::null_mut(),
                    count: 0,
                };
            }
            let ptr = Box::into_raw(changes.into_boxed_slice()) as *mut FileChange;
            ChangeList { changes: ptr, count }
        }
        Err(_) => ChangeList {
            changes: std::ptr::null_mut(),
            count: 0,
        },
    }
}

// 优化点1：对称释放 Box<[FileChange]> + 每个 path_ptr
#[no_mangle]
pub extern "C" fn free_change_list(list: ChangeList) {
    if list.changes.is_null() || list.count == 0 {
        return;
    }

    unsafe {
        let slice = std::slice::from_raw_parts_mut(list.changes, list.count);

        for c in slice.iter_mut() {
            if !c.path_ptr.is_null() && c.path_len > 0 {
                let ps = std::slice::from_raw_parts_mut(c.path_ptr, c.path_len);
                let _ = Box::<[u8]>::from_raw(ps);
                c.path_ptr = std::ptr::null_mut();
                c.path_len = 0;
            }
        }

        let _ = Box::<[FileChange]>::from_raw(slice);
    }
}

// 优化点1：对称释放 Box<[u8]>
#[no_mangle]
pub extern "C" fn free_usn_change_result(result: UsnChangeResult) {
    if !result.data.is_null() && result.data_len > 0 {
        unsafe {
            let slice = std::slice::from_raw_parts_mut(result.data, result.data_len);
            let _ = Box::<[u8]>::from_raw(slice);
        }
    }
}

#[no_mangle]
pub extern "C" fn get_usn_journal_id(drive_letter: u16) -> u64 {
    use windows_sys::Win32::Foundation::*;
    use windows_sys::Win32::Storage::FileSystem::*;
    use windows_sys::Win32::System::IO::DeviceIoControl;

    let drive = (drive_letter as u8 as char).to_ascii_uppercase();
    let volume: Vec<u16> = format!("\\\\.\\{}:", drive)
        .encode_utf16()
        .chain(Some(0))
        .collect();

    unsafe {
        let h = CreateFileW(
            volume.as_ptr(),
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            std::ptr::null(),
            OPEN_EXISTING,
            0,
            0,
        );
        if h == INVALID_HANDLE_VALUE {
            return 0;
        }

        let mut jd: USN_JOURNAL_DATA_V0 = std::mem::zeroed();
        let mut br: u32 = 0;

        let ret = if DeviceIoControl(
            h,
            FSCTL_QUERY_USN_JOURNAL,
            std::ptr::null(),
            0,
            &mut jd as *mut _ as _,
            std::mem::size_of::<USN_JOURNAL_DATA_V0>() as u32,
            &mut br,
            std::ptr::null_mut(),
        ) != 0
        {
            jd.usn_journal_id
        } else {
            0
        };

        CloseHandle(h);
        ret
    }
}

// ============== FFI 导出：USN 监控器 ==============

pub struct UsnMonitor {
    drive: char,
    last_usn: i64,
}

#[no_mangle]
pub extern "C" fn create_usn_monitor(drive_letter: c_char) -> *mut UsnMonitor {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();
    match get_next_usn(drive) {
        Ok(usn) => Box::into_raw(Box::new(UsnMonitor { drive, last_usn: usn })),
        Err(_) => std::ptr::null_mut(),
    }
}

#[no_mangle]
pub extern "C" fn destroy_usn_monitor(monitor: *mut UsnMonitor) {
    if !monitor.is_null() {
        unsafe {
            let _ = Box::from_raw(monitor);
        }
    }
}

#[no_mangle]
pub extern "C" fn get_changes(monitor: *mut UsnMonitor) -> ChangeList {
    if monitor.is_null() {
        return ChangeList {
            changes: std::ptr::null_mut(),
            count: 0,
        };
    }

    unsafe {
        let mon = &mut *monitor;
        let result = match get_changes_since(mon.drive, mon.last_usn) {
            Ok(changes) => changes,
            Err(_) => {
                return ChangeList {
                    changes: std::ptr::null_mut(),
                    count: 0,
                }
            }
        };

        if let Ok(new_usn) = get_next_usn(mon.drive) {
            mon.last_usn = new_usn;
        }

        let count = result.len();
        if count == 0 {
            return ChangeList {
                changes: std::ptr::null_mut(),
                count: 0,
            };
        }

        let ptr = Box::into_raw(result.into_boxed_slice()) as *mut FileChange;
        ChangeList { changes: ptr, count }
    }
}

// ============== FFI 导出：缓存管理 ==============

#[no_mangle]
pub extern "C" fn clear_dir_cache(drive_letter: u16) {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();
    let mut cache = DIR_CACHE.write();
    cache.remove(&drive);
}

#[no_mangle]
pub extern "C" fn clear_all_dir_cache() {
    let mut cache = DIR_CACHE.write();
    cache.clear();
}

#[no_mangle]
pub extern "C" fn warmup_dir_cache(drive_letter: u16) -> i32 {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();

    {
        let cache = DIR_CACHE.read();
        if let Some(dc) = cache.get(&drive) {
            if !dc.paths.is_empty() {
                return 1;
            }
        }
    }

    match warmup_cache_internal(drive) {
        Ok(_) => 1,
        Err(_) => 0,
    }
}

#[no_mangle]
pub extern "C" fn save_dir_cache(
    drive_letter: u16,
    file_path_ptr: *const u8,
    file_path_len: usize,
) -> i32 {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();

    if file_path_ptr.is_null() || file_path_len == 0 {
        return 0;
    }

    let file_path = unsafe {
        let slice = std::slice::from_raw_parts(file_path_ptr, file_path_len);
        match std::str::from_utf8(slice) {
            Ok(s) => s,
            Err(_) => return 0,
        }
    };

    let journal_id_now = get_usn_journal_id(drive as u16);
    if journal_id_now == 0 {
        return 0;
    }

    let persisted = {
        let cache = DIR_CACHE.read();
        let dc = match cache.get(&drive) {
            Some(v) => v,
            None => return 0,
        };

        if dc.journal_id != journal_id_now {
            return 0;
        }

        let mut paths_vec: Vec<(u64, String)> = Vec::with_capacity(dc.paths.len());
        for (k, v) in dc.paths.iter() {
            paths_vec.push((*k, v.as_ref().clone()));
        }

        PersistDirCacheV1 {
            version: 1,
            drive: drive as u8,
            journal_id: dc.journal_id,
            paths: paths_vec,
        }
    };

    let bytes = match bincode::serialize(&persisted) {
        Ok(b) => b,
        Err(_) => return 0,
    };

    if let Some(parent) = Path::new(file_path).parent() {
        let _ = fs::create_dir_all(parent);
    }

    match fs::write(file_path, bytes) {
        Ok(_) => 1,
        Err(_) => 0,
    }
}

#[no_mangle]
pub extern "C" fn load_dir_cache(
    drive_letter: u16,
    file_path_ptr: *const u8,
    file_path_len: usize,
) -> i32 {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();

    if file_path_ptr.is_null() || file_path_len == 0 {
        return 0;
    }

    let file_path = unsafe {
        let slice = std::slice::from_raw_parts(file_path_ptr, file_path_len);
        match std::str::from_utf8(slice) {
            Ok(s) => s,
            Err(_) => return 0,
        }
    };

    let journal_id_now = get_usn_journal_id(drive as u16);
    if journal_id_now == 0 {
        return 0;
    }

    let bytes = match fs::read(file_path) {
        Ok(b) => b,
        Err(_) => return 0,
    };

    let persisted: PersistDirCacheV1 = match bincode::deserialize(&bytes) {
        Ok(v) => v,
        Err(_) => return 0,
    };

    if persisted.version != 1 {
        return 0;
    }
    if persisted.drive != drive as u8 {
        return 0;
    }
    if persisted.journal_id != journal_id_now {
        return 0;
    }

    let mut map: FxHashMap<u64, Arc<String>> = FxHashMap::default();
    map.reserve(persisted.paths.len());
    for (k, v) in persisted.paths {
        map.insert(k, Arc::new(v));
    }

    let last_usn = get_current_usn(drive as u16);

    let mut cache = DIR_CACHE.write();
    cache.insert(
        drive,
        DirCache {
            paths: Arc::new(map),
            journal_id: journal_id_now,
            last_usn,
        },
    );

    1
}

#[no_mangle]
pub extern "C" fn get_engine_version() -> u32 {
    52
}

// ============== 内部函数 ==============

fn get_next_usn(drive: char) -> Result<i64, Box<dyn std::error::Error>> {
    use windows_sys::Win32::Foundation::*;
    use windows_sys::Win32::Storage::FileSystem::*;
    use windows_sys::Win32::System::IO::DeviceIoControl;

    let volume: Vec<u16> = format!("\\\\.\\{}:", drive)
        .encode_utf16()
        .chain(Some(0))
        .collect();

    unsafe {
        let h = CreateFileW(
            volume.as_ptr(),
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            std::ptr::null(),
            OPEN_EXISTING,
            0,
            0,
        );
        if h == INVALID_HANDLE_VALUE {
            return Err("打开卷失败".into());
        }

        let mut jd: USN_JOURNAL_DATA_V0 = std::mem::zeroed();
        let mut br: u32 = 0;

        let result = if DeviceIoControl(
            h,
            FSCTL_QUERY_USN_JOURNAL,
            std::ptr::null(),
            0,
            &mut jd as *mut _ as _,
            std::mem::size_of::<USN_JOURNAL_DATA_V0>() as u32,
            &mut br,
            std::ptr::null_mut(),
        ) != 0
        {
            Ok(jd.next_usn)
        } else {
            Err("查询USN失败".into())
        };

        CloseHandle(h);
        result
    }
}

fn warmup_cache_internal(drive: char) -> Result<(), Box<dyn std::error::Error>> {
    use windows_sys::Win32::Foundation::*;
    use windows_sys::Win32::Storage::FileSystem::*;
    use windows_sys::Win32::System::IO::DeviceIoControl;

    {
        let cache = DIR_CACHE.read();
        if let Some(dc) = cache.get(&drive) {
            if !dc.paths.is_empty() {
                return Ok(());
            }
        }
    }

    let volume: Vec<u16> = format!("\\\\.\\{}:", drive)
        .encode_utf16()
        .chain(Some(0))
        .collect();

    unsafe {
        let h = CreateFileW(
            volume.as_ptr(),
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            std::ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS,
            0,
        );
        if h == INVALID_HANDLE_VALUE {
            return Err("打开卷失败".into());
        }

        let mut jd: USN_JOURNAL_DATA_V0 = std::mem::zeroed();
        let mut br: u32 = 0;

        if DeviceIoControl(
            h,
            FSCTL_QUERY_USN_JOURNAL,
            std::ptr::null(),
            0,
            &mut jd as *mut _ as _,
            std::mem::size_of::<USN_JOURNAL_DATA_V0>() as u32,
            &mut br,
            std::ptr::null_mut(),
        ) == 0
        {
            CloseHandle(h);
            return Err("查询USN失败".into());
        }

        let records = scan_usn_journal_quick(h, &jd)?;
        let paths = build_path_map(&records, drive);

        CloseHandle(h);

        let mut cache = DIR_CACHE.write();
        cache.insert(
            drive,
            DirCache {
                paths: Arc::new(paths),
                journal_id: jd.usn_journal_id,
                last_usn: jd.next_usn,
            },
        );

        Ok(())
    }
}

fn get_or_build_cache(
    drive: char,
    h: windows_sys::Win32::Foundation::HANDLE,
    jd: &USN_JOURNAL_DATA_V0,
) -> Result<Arc<FxHashMap<u64, Arc<String>>>, Box<dyn std::error::Error>> {
    {
        let cache = DIR_CACHE.read();
        if let Some(dc) = cache.get(&drive) {
            if dc.journal_id == jd.usn_journal_id && !dc.paths.is_empty() {
                return Ok(Arc::clone(&dc.paths));
            }
        }
    }

    let records = unsafe { scan_usn_journal_quick(h, jd)? };
    let paths = build_path_map(&records, drive);
    let paths_arc = Arc::new(paths);

    {
        let mut cache = DIR_CACHE.write();
        cache.insert(
            drive,
            DirCache {
                paths: Arc::clone(&paths_arc),
                journal_id: jd.usn_journal_id,
                last_usn: jd.next_usn,
            },
        );
    }

    Ok(paths_arc)
}

// 优化点3：每个父目录只 clone 一次，避免借用冲突
fn build_path_map(records: &[MftRecord], drive: char) -> FxHashMap<u64, Arc<String>> {
    let root = format!("{}:\\", drive);

    let mut p2c: FxHashMap<u64, Vec<usize>> = FxHashMap::default();
    p2c.reserve(records.len());
    for (i, r) in records.iter().enumerate() {
        if r.is_dir {
            p2c.entry(r.parent_ref).or_default().push(i);
        }
    }

    let mut paths: FxHashMap<u64, Arc<String>> = FxHashMap::default();
    paths.reserve(records.len());
    paths.insert(5, Arc::new(root));

    let mut queue = VecDeque::with_capacity(2000);
    queue.push_back(5u64);

    let mut path_buf = String::with_capacity(512);

    while let Some(pid) = queue.pop_front() {
        // 每个父目录 clone 一次（现在是 Arc 克隆），结束借用后才能 insert
        let parent_path_owned = match paths.get(&pid) {
            Some(p) => Arc::clone(p),
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

                paths.insert(r.file_ref, Arc::new(path_buf.clone()));
                queue.push_back(r.file_ref);
            }
        }
    }

    paths
}

fn get_changes_since(
    drive: char,
    last_usn: i64,
) -> Result<Vec<FileChange>, Box<dyn std::error::Error>> {
    use windows_sys::Win32::Foundation::*;
    use windows_sys::Win32::Storage::FileSystem::*;
    use windows_sys::Win32::System::IO::DeviceIoControl;

    let volume: Vec<u16> = format!("\\\\.\\{}:", drive)
        .encode_utf16()
        .chain(Some(0))
        .collect();
    let root = format!("{}:\\", drive);

    unsafe {
        let h = CreateFileW(
            volume.as_ptr(),
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            std::ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS,
            0,
        );
        if h == INVALID_HANDLE_VALUE {
            return Err("打开卷失败".into());
        }

        let mut jd: USN_JOURNAL_DATA_V0 = std::mem::zeroed();
        let mut br: u32 = 0;

        if DeviceIoControl(
            h,
            FSCTL_QUERY_USN_JOURNAL,
            std::ptr::null(),
            0,
            &mut jd as *mut _ as _,
            std::mem::size_of::<USN_JOURNAL_DATA_V0>() as u32,
            &mut br,
            std::ptr::null_mut(),
        ) == 0
        {
            CloseHandle(h);
            return Err("查询USN失败".into());
        }

        if last_usn >= jd.next_usn {
            CloseHandle(h);
            return Ok(Vec::new());
        }

        let paths = get_or_build_cache(drive, h, &jd)?;

        let reason_mask = USN_REASON_FILE_CREATE
            | USN_REASON_FILE_DELETE
            | USN_REASON_DATA_EXTEND
            | USN_REASON_DATA_OVERWRITE
            | USN_REASON_RENAME_OLD_NAME
            | USN_REASON_RENAME_NEW_NAME
            | USN_REASON_CLOSE;

        let mut read_data = READ_USN_JOURNAL_DATA_V0 {
            start_usn: last_usn,
            reason_mask,
            return_only_on_close: 0,
            timeout: 0,
            bytes_to_wait_for: 0,
            usn_journal_id: jd.usn_journal_id,
        };

        let mut buf = vec![0u8; USN_READ_BUFFER_SIZE];
        let mut changes: Vec<FileChange> = Vec::with_capacity(64);

        // 优化点4：seen 改成 u64，减少 hash/tuple 开销
        let mut seen: FxHashSet<u64> = FxHashSet::default();
        seen.reserve(64);

        let mut path_buf = String::with_capacity(512);

        loop {
            if DeviceIoControl(
                h,
                FSCTL_READ_USN_JOURNAL,
                &read_data as *const _ as _,
                std::mem::size_of::<READ_USN_JOURNAL_DATA_V0>() as u32,
                buf.as_mut_ptr() as _,
                buf.len() as u32,
                &mut br,
                std::ptr::null_mut(),
            ) == 0
                || br <= 8
            {
                break;
            }

            let next_usn = *(buf.as_ptr() as *const i64);
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
                        if fc != b'.' && fc != b'$' {
                            let reason = rec.reason;

                            let is_delete = (reason & USN_REASON_FILE_DELETE) != 0;
                            let is_rename_old = (reason & USN_REASON_RENAME_OLD_NAME) != 0;
                            let is_rename_new = (reason & USN_REASON_RENAME_NEW_NAME) != 0;
                            let is_create = (reason & USN_REASON_FILE_CREATE) != 0;

                            if !is_delete
                                && !is_rename_old
                                && !is_rename_new
                                && (reason & USN_REASON_CLOSE) == 0
                            {
                                off += rec.record_length as usize;
                                continue;
                            }

                            let parent_ref = rec.parent_file_reference_number & 0xFFFFFFFFFFFF;
                            let file_ref = rec.file_reference_number & 0xFFFFFFFFFFFF;

                            path_buf.clear();
                            if let Some(parent_path) = paths.get(&parent_ref) {
                                path_buf.push_str(parent_path.trim_end_matches('\\'));
                            } else if is_rename_new || is_create {
                                if let Some(p) = get_path_by_file_ref_with_handle(h, file_ref) {
                                    if let Some(pos) = p.rfind('\\') {
                                        path_buf.push_str(&p[..pos]);
                                    } else {
                                        path_buf.push_str(root.trim_end_matches('\\'));
                                    }
                                } else {
                                    path_buf.push_str(root.trim_end_matches('\\'));
                                }
                            } else {
                                path_buf.push_str(root.trim_end_matches('\\'));
                            }
                            path_buf.push('\\');
                            path_buf.push_str(&name);

                            if is_recycle_bin_path(&path_buf) {
                                off += rec.record_length as usize;
                                continue;
                            }

                            let is_dir = (rec.file_attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;

                            let action: u8 = if is_delete || is_rename_old {
                                0
                            } else if is_rename_new || is_create {
                                1
                            } else if (reason
                                & (USN_REASON_DATA_EXTEND | USN_REASON_DATA_OVERWRITE))
                                != 0
                            {
                                2
                            } else {
                                off += rec.record_length as usize;
                                continue;
                            };

                            // 优化点4：组合 key：action(8bit) + file_ref(48bit)
                            let key = ((action as u64) << 56) | file_ref;
                            if !seen.insert(key) {
                                off += rec.record_length as usize;
                                continue;
                            }

                            let path_bytes = path_buf.as_bytes().to_vec();
                            let path_len = path_bytes.len();
                            let path_ptr =
                                Box::into_raw(path_bytes.into_boxed_slice()) as *mut u8;

                            changes.push(FileChange {
                                action,
                                is_dir: if is_dir { 1 } else { 0 },
                                path_ptr,
                                path_len,
                            });
                        }
                    }
                }

                off += rec.record_length as usize;
            }

            if next_usn >= jd.next_usn {
                break;
            }
            read_data.start_usn = next_usn;
        }

        CloseHandle(h);
        Ok(changes)
    }
}

unsafe fn scan_usn_journal_quick(
    h: windows_sys::Win32::Foundation::HANDLE,
    jd: &USN_JOURNAL_DATA_V0,
) -> Result<Vec<MftRecord>, Box<dyn std::error::Error>> {
    use windows_sys::Win32::System::IO::DeviceIoControl;

    let mut records = Vec::with_capacity(80_000);
    let mut med = MFT_ENUM_DATA_V0 {
        start_file_reference_number: 0,
        low_usn: 0,
        high_usn: jd.next_usn,
    };
    let mut buf = vec![0u8; USN_QUICK_BUFFER_SIZE];
    let mut br: u32 = 0;

    loop {
        if DeviceIoControl(
            h,
            FSCTL_ENUM_USN_DATA,
            &med as *const _ as _,
            std::mem::size_of::<MFT_ENUM_DATA_V0>() as u32,
            buf.as_mut_ptr() as _,
            buf.len() as u32,
            &mut br,
            std::ptr::null_mut(),
        ) == 0
            || br <= 8
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
                let is_dir = (rec.file_attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
                if is_dir {
                    let slice =
                        std::slice::from_raw_parts(buf.as_ptr().add(noff) as *const u16, nlen / 2);
                    if let Ok(name) = String::from_utf16(slice) {
                        let fc = name.as_bytes().first().copied().unwrap_or(b'.');
                        if fc != b'.' && fc != b'$' {
                            records.push(MftRecord {
                                filename: name,
                                parent_ref: rec.parent_file_reference_number & 0xFFFFFFFFFFFF,
                                is_dir: true,
                                file_ref: rec.file_reference_number & 0xFFFFFFFFFFFF,
                            });
                        }
                    }
                }
            }
            off += rec.record_length as usize;
        }
    }

    Ok(records)
}

fn scan_usn_journal_all(drive: char) -> Result<Vec<MftRecord>, Box<dyn std::error::Error>> {
    use windows_sys::Win32::Foundation::*;
    use windows_sys::Win32::Storage::FileSystem::*;
    use windows_sys::Win32::System::IO::DeviceIoControl;

    let volume: Vec<u16> = format!("\\\\.\\{}:", drive)
        .encode_utf16()
        .chain(Some(0))
        .collect();

    unsafe {
        let h = CreateFileW(
            volume.as_ptr(),
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            std::ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_SEQUENTIAL_SCAN,
            0,
        );
        if h == INVALID_HANDLE_VALUE {
            return Err("打开卷失败".into());
        }

        let mut jd: USN_JOURNAL_DATA_V0 = std::mem::zeroed();
        let mut br: u32 = 0;

        if DeviceIoControl(
            h,
            FSCTL_QUERY_USN_JOURNAL,
            std::ptr::null(),
            0,
            &mut jd as *mut _ as _,
            std::mem::size_of::<USN_JOURNAL_DATA_V0>() as u32,
            &mut br,
            std::ptr::null_mut(),
        ) == 0
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
                &med as *const _ as _,
                std::mem::size_of::<MFT_ENUM_DATA_V0>() as u32,
                buf.as_mut_ptr() as _,
                buf.len() as u32,
                &mut br,
                std::ptr::null_mut(),
            ) == 0
                || br <= 8
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
                    let slice =
                        std::slice::from_raw_parts(buf.as_ptr().add(noff) as *const u16, nlen / 2);
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

fn scan_and_pack(drive: char) -> Result<(Vec<u8>, usize), Box<dyn std::error::Error>> {
    let records = scan_usn_journal_all(drive)?;
    let root = format!("{}:\\", drive);

    let skip_dirs = build_skip_dirs_set();
    let skip_exts = build_skip_exts_set();

    let mut p2c: FxHashMap<u64, Vec<usize>> = FxHashMap::default();
    p2c.reserve(records.len() / 8);
    for (i, r) in records.iter().enumerate() {
        p2c.entry(r.parent_ref).or_default().push(i);
    }

    let mut paths: FxHashMap<u64, Arc<String>> = FxHashMap::default();
    paths.reserve(records.len() / 4);
    let mut skip: FxHashSet<u64> = FxHashSet::default();
    skip.reserve(records.len() / 16);

    paths.insert(5, Arc::new(root));
    let mut queue = VecDeque::with_capacity(8000);
    queue.push_back(5u64);

    let mut path_buf = String::with_capacity(512);

    while let Some(pid) = queue.pop_front() {
        if skip.contains(&pid) {
            if let Some(cs) = p2c.get(&pid) {
                for &i in cs {
                    skip.insert(records[i].file_ref);
                    queue.push_back(records[i].file_ref);
                }
            }
            continue;
        }

        // 优化点3：每个父目录 clone 一次，结束借用后才能 insert
        let parent_path_owned = match paths.get(&pid) {
            Some(p) => Arc::clone(p),
            None => continue,
        };
        let parent_trimmed = parent_path_owned.trim_end_matches('\\');

        if let Some(cs) = p2c.get(&pid) {
            for &i in cs {
                let r = &records[i];
                if r.is_dir {
                    let name_lower = r.filename.to_ascii_lowercase();
                    if should_skip_dir(&name_lower, &skip_dirs) {
                        skip.insert(r.file_ref);
                    } else {
                        path_buf.clear();
                        path_buf.push_str(parent_trimmed);
                        path_buf.push('\\');
                        path_buf.push_str(&r.filename);
                        paths.insert(r.file_ref, Arc::new(path_buf.clone()));
                    }
                    queue.push_back(r.file_ref);
                }
            }
        }
    }

    let items: Vec<_> = records
        .par_iter()
        .filter_map(|r| {
            if skip.contains(&r.file_ref) {
                return None;
            }
            let parent = paths.get(&r.parent_ref)?;
            if !r.is_dir && should_skip_ext_fast(&r.filename, &skip_exts) {
                return None;
            }

            let mut path = String::with_capacity(parent.len() + 1 + r.filename.len());
            path.push_str(parent.trim_end_matches('\\'));
            path.push('\\');
            path.push_str(&r.filename);

            let ext = if r.is_dir {
                String::new()
            } else {
                get_ext_lower(&r.filename)
            };

            Some((r.filename.clone(), path, Arc::clone(parent), ext, r.is_dir))
        })
        .collect();

    let count = items.len();

    let total_size: usize = items
        .iter()
        .map(|(name, path, parent, ext, _)| 24 + name.len() + path.len() + parent.len() + ext.len())
        .sum();

    let mut data = Vec::with_capacity(total_size);

    for (filename, path, parent, ext, is_dir) in items {
        data.push(if is_dir { 1 } else { 0 });
        data.extend(&(filename.len() as u16).to_le_bytes());
        data.extend(&(path.len() as u16).to_le_bytes());
        data.extend(&(parent.len() as u16).to_le_bytes());
        data.push(ext.len() as u8);
        data.extend(&0u64.to_le_bytes());
        data.extend(&0f64.to_le_bytes());
        data.extend(filename.as_bytes());
        data.extend(path.as_bytes());
        data.extend(parent.as_bytes());
        data.extend(ext.as_bytes());
    }

    Ok((data, count))
}

// ============== 搜索索引 FFI 导出 ==============

/// 内部函数：初始化搜索索引（供内部调用）
pub fn init_search_index_internal(drive: char) -> bool {
    let drive = drive.to_ascii_uppercase();

    // 检查是否已初始化
    {
        let indices = SEARCH_INDICES.read();
        if indices.contains_key(&drive) {
            return true;
        }
    }

    // 先尝试从磁盘加载已持久化的索引，避免每次启动都重建
    let index_path = format!("{}:\\.search_index.bin", drive);
    if Path::new(&index_path).exists() {
        let index = Arc::new(SearchIndex::new());
        match index.load_from_file(Path::new(&index_path)) {
            Ok(_) => {
                log::info!("✅ 成功从磁盘加载索引: {}", index_path);
                SEARCH_INDICES.write().insert(drive, index);
                return true;
            }
            Err(e) => {
                log::warn!("⚠️ 加载磁盘索引失败，将执行全盘重建: {} - {}", index_path, e);
            }
        }
    } else {
        log::info!("ℹ️ 索引文件不存在，将执行首次构建: {}", index_path);
    }

    // 扫描并构建索引
    log::info!("📊 {} 盘开始扫描 USN Journal...", drive);
    let start_time = std::time::Instant::now();
    
    let records = match scan_usn_journal_all(drive) {
        Ok(r) => {
            log::info!("✅ {} 盘扫描完成：{} 条记录，耗时 {:.2}秒", drive, r.len(), start_time.elapsed().as_secs_f64());
            r
        },
        Err(e) => {
            log::error!("❌ {} 盘扫描失败: {:?}", drive, e);
            return false;
        }
    };

    let root = format!("{}:\\", drive);
    let skip_dirs = build_skip_dirs_set();
    let skip_exts = build_skip_exts_set();
    
    log::info!("🔧 {} 盘开始构建路径映射...", drive);

    // 构建路径映射
    let mut p2c: FxHashMap<u64, Vec<usize>> = FxHashMap::default();
    p2c.reserve(records.len() / 8);
    for (i, r) in records.iter().enumerate() {
        p2c.entry(r.parent_ref).or_default().push(i);
    }

    let mut paths: FxHashMap<u64, Arc<String>> = FxHashMap::default();
    paths.reserve(records.len() / 4);
    let mut skip: FxHashSet<u64> = FxHashSet::default();
    skip.reserve(records.len() / 16);

    paths.insert(5, Arc::new(root));
    let mut queue = VecDeque::with_capacity(8000);
    queue.push_back(5u64);

    let mut path_buf = String::with_capacity(512);

    while let Some(pid) = queue.pop_front() {
        if skip.contains(&pid) {
            if let Some(cs) = p2c.get(&pid) {
                for &i in cs {
                    skip.insert(records[i].file_ref);
                    queue.push_back(records[i].file_ref);
                }
            }
            continue;
        }

        let parent_path_owned = match paths.get(&pid) {
            Some(p) => Arc::clone(p),
            None => continue,
        };
        let parent_trimmed = parent_path_owned.trim_end_matches('\\');

        if let Some(cs) = p2c.get(&pid) {
            for &i in cs {
                let r = &records[i];
                if r.is_dir {
                    let name_lower = r.filename.to_ascii_lowercase();
                    if should_skip_dir(&name_lower, &skip_dirs) {
                        skip.insert(r.file_ref);
                    } else {
                        path_buf.clear();
                        path_buf.push_str(parent_trimmed);
                        path_buf.push('\\');
                        path_buf.push_str(&r.filename);
                        paths.insert(r.file_ref, Arc::new(path_buf.clone()));
                    }
                    queue.push_back(r.file_ref);
                }
            }
        }
    }

    // 构建索引项（并行获取文件元数据）
    log::info!("📝 {} 盘开始构建索引项...", drive);
    let indexed_items: Vec<IndexedItem> = records
        .par_iter()
        .filter_map(|r| {
            if skip.contains(&r.file_ref) {
                return None;
            }
            let parent = paths.get(&r.parent_ref)?;
            if !r.is_dir && should_skip_ext_fast(&r.filename, &skip_exts) {
                return None;
            }

            let mut path = String::with_capacity(parent.len() + 1 + r.filename.len());
            path.push_str(parent.trim_end_matches('\\'));
            path.push('\\');
            path.push_str(&r.filename);

            // 获取真实的文件元数据
            let (size, mtime) = if r.is_dir {
                (0, 0.0)
            } else {
                get_file_info_fast(&path).unwrap_or((0, 0.0))
            };

            Some(IndexedItem {
                name: r.filename.clone(),
                name_lower: String::new(),  // 将在 build 中填充
                path,
                file_ref: r.file_ref,
                parent_ref: r.parent_ref,
                size,
                is_dir: r.is_dir,
                mtime,
            })
        })
        .collect();

    // 创建索引
    log::info!("🏗️ {} 盘开始创建搜索索引：{} 个项目", drive, indexed_items.len());
    let index = Arc::new(SearchIndex::new());
    index.build(indexed_items);

    // 尝试持久化（写到驱动器根目录）
    log::info!("💾 {} 盘保存索引到磁盘...", drive);
    let _ = index.save_to_file(Path::new(&index_path));

    SEARCH_INDICES.write().insert(drive, index);
    log::info!("✅ {} 盘索引构建完成！", drive);
    true
}

/// 强制重建搜索索引（删除旧文件并重新构建）
pub fn force_rebuild_search_index_internal(drive: char) -> bool {
    let drive = drive.to_ascii_uppercase();

    // 删除旧索引文件
    let index_path = format!("{}:\\.search_index.bin", drive);
    if Path::new(&index_path).exists() {
        if let Err(e) = std::fs::remove_file(&index_path) {
            log::warn!("删除旧索引文件失败: {} ({})", index_path, e);
        } else {
            log::info!("✅ 已删除旧索引文件: {}", index_path);
        }
    }

    // 清空内存索引缓存
    SEARCH_INDICES.write().remove(&drive);

    // 执行新的构建
    init_search_index_internal(drive)
}

/// FFI: 初始化搜索索引
#[no_mangle]
pub extern "C" fn init_search_index(drive_letter: u16) -> i32 {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();
    if init_search_index_internal(drive) { 1 } else { 0 }
}

/// FFI: 前缀搜索
#[no_mangle]
pub extern "C" fn search_prefix(
    drive_letter: u16,
    prefix_ptr: *const c_char,
    max_results: usize,
) -> *mut SearchResultFFI {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();

    let prefix = unsafe {
        if prefix_ptr.is_null() {
            return std::ptr::null_mut();
        }
        match CStr::from_ptr(prefix_ptr).to_str() {
            Ok(s) => s,
            Err(_) => return std::ptr::null_mut(),
        }
    };

    let indices = SEARCH_INDICES.read();
    let index = match indices.get(&drive) {
        Some(idx) => idx,
        None => return std::ptr::null_mut(),
    };

    let results = index.search_prefix(prefix, max_results);
    pack_search_results(results)
}

/// FFI: 模糊搜索（包含匹配）
#[no_mangle]
pub extern "C" fn search_contains(
    drive_letter: u16,
    pattern_ptr: *const c_char,
    max_results: usize,
) -> *mut SearchResultFFI {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();

    let pattern = unsafe {
        if pattern_ptr.is_null() {
            return std::ptr::null_mut();
        }
        match CStr::from_ptr(pattern_ptr).to_str() {
            Ok(s) => s,
            Err(_) => return std::ptr::null_mut(),
        }
    };

    let indices = SEARCH_INDICES.read();
    let index = match indices.get(&drive) {
        Some(idx) => idx,
        None => return std::ptr::null_mut(),
    };

    let results = index.search_contains(pattern, max_results);
    pack_search_results(results)
}

/// FFI: 按扩展名搜索
#[no_mangle]
pub extern "C" fn search_by_ext(
    drive_letter: u16,
    ext_ptr: *const c_char,
    max_results: usize,
) -> *mut SearchResultFFI {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();

    let ext = unsafe {
        if ext_ptr.is_null() {
            return std::ptr::null_mut();
        }
        match CStr::from_ptr(ext_ptr).to_str() {
            Ok(s) => s,
            Err(_) => return std::ptr::null_mut(),
        }
    };

    let indices = SEARCH_INDICES.read();
    let index = match indices.get(&drive) {
        Some(idx) => idx,
        None => return std::ptr::null_mut(),
    };

    let results = index.search_by_extension(ext, max_results);
    pack_search_results(results)
}

/// FFI: 按修改时间范围搜索
#[no_mangle]
pub extern "C" fn search_by_mtime_range(
    drive_letter: u16,
    min_mtime: f64,
    max_mtime: f64,
    max_results: usize,
) -> *mut SearchResultFFI {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();

    let indices = SEARCH_INDICES.read();
    let index = match indices.get(&drive) {
        Some(idx) => idx,
        None => return std::ptr::null_mut(),
    };

    let results = index.search_by_mtime_range(min_mtime, max_mtime, max_results);
    pack_search_results(results)
}

/// FFI: 增量添加文件
#[no_mangle]
pub extern "C" fn index_add_file(
    drive_letter: u16,
    name_ptr: *const c_char,
    path_ptr: *const c_char,
    file_ref: u64,
    parent_ref: u64,
    size: u64,
    is_dir: u8,
) -> i32 {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();

    let (name, path) = unsafe {
        if name_ptr.is_null() || path_ptr.is_null() {
            return 0;
        }
        match (
            CStr::from_ptr(name_ptr).to_str(),
            CStr::from_ptr(path_ptr).to_str(),
        ) {
            (Ok(n), Ok(p)) => (n, p),
            _ => return 0,
        }
    };

    let indices = SEARCH_INDICES.read();
    if let Some(index) = indices.get(&drive) {
        index.add_file(IndexedItem {
            name: name.to_string(),
            name_lower: String::new(),  // 将在 add_file 中填充
            path: path.to_string(),
            file_ref,
            parent_ref,
            size,
            is_dir: is_dir != 0,
            mtime: 0.0,
        });
        1
    } else {
        0
    }
}

/// FFI: 增量删除文件
#[no_mangle]
pub extern "C" fn index_remove_file(drive_letter: u16, file_ref: u64) -> i32 {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();

    let indices = SEARCH_INDICES.read();
    if let Some(index) = indices.get(&drive) {
        if index.remove_file(file_ref) {
            1
        } else {
            0
        }
    } else {
        0
    }
}

/// FFI: 持久化索引
#[no_mangle]
pub extern "C" fn save_search_index(drive_letter: u16) -> i32 {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();

    let indices = SEARCH_INDICES.read();
    if let Some(index) = indices.get(&drive) {
        let index_path = format!("{}:\\.search_index.bin", drive);
        if index.save_to_file(Path::new(&index_path)).is_ok() {
            1
        } else {
            0
        }
    } else {
        0
    }
}

/// FFI: 从磁盘加载索引
#[no_mangle]
pub extern "C" fn load_search_index(drive_letter: u16) -> i32 {
    let drive = (drive_letter as u8 as char).to_ascii_uppercase();
    let index_path = format!("{}:\\.search_index.bin", drive);

    if !Path::new(&index_path).exists() {
        return 0;
    }

    let index = Arc::new(SearchIndex::new());
    if index.load_from_file(Path::new(&index_path)).is_ok() {
        let mut indices = SEARCH_INDICES.write();
        indices.insert(drive, index);
        1
    } else {
        0
    }
}

/// FFI: 释放搜索结果
#[no_mangle]
pub extern "C" fn free_search_result(result: *mut SearchResultFFI) {
    if result.is_null() {
        return;
    }

    unsafe {
        let result_box = Box::from_raw(result);

        if !result_box.items.is_null() && result_box.count > 0 {
            let items_slice =
                std::slice::from_raw_parts_mut(result_box.items, result_box.count);

            for item in &mut *items_slice {
                if !item.name_ptr.is_null() && item.name_len > 0 {
                    let name_slice =
                        std::slice::from_raw_parts_mut(item.name_ptr, item.name_len);
                    let _ = Box::<[u8]>::from_raw(name_slice);
                }
                if !item.path_ptr.is_null() && item.path_len > 0 {
                    let path_slice =
                        std::slice::from_raw_parts_mut(item.path_ptr, item.path_len);
                    let _ = Box::<[u8]>::from_raw(path_slice);
                }
            }

            let _ = Box::<[SearchItemFFI]>::from_raw(items_slice);
        }
    }
}

// 辅助函数：打包搜索结果
fn pack_search_results(results: Vec<IndexedItem>) -> *mut SearchResultFFI {
    let count = results.len();
    if count == 0 {
        return Box::into_raw(Box::new(SearchResultFFI {
            items: std::ptr::null_mut(),
            count: 0,
        }));
    }

    let mut items = Vec::with_capacity(count);

    for item in results {
        let name_bytes = item.name.into_bytes().into_boxed_slice();
        let name_len = name_bytes.len();
        let name_ptr = Box::into_raw(name_bytes) as *mut u8;

        let path_bytes = item.path.into_bytes().into_boxed_slice();
        let path_len = path_bytes.len();
        let path_ptr = Box::into_raw(path_bytes) as *mut u8;

        items.push(SearchItemFFI {
            name_ptr,
            name_len,
            path_ptr,
            path_len,
            size: item.size,
            is_dir: if item.is_dir { 1 } else { 0 },
            mtime: item.mtime,
        });
    }

    let items_box = items.into_boxed_slice();
    let items_ptr = Box::into_raw(items_box) as *mut SearchItemFFI;

    Box::into_raw(Box::new(SearchResultFFI {
        items: items_ptr,
        count,
    }))
}

// ============== 测试 ==============

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_skip_ext_fast() {
        let skip_exts = build_skip_exts_set();
        assert!(should_skip_ext_fast("test.dll", &skip_exts));
        assert!(should_skip_ext_fast("test.DLL", &skip_exts));
        assert!(should_skip_ext_fast("test.tmp", &skip_exts));
        assert!(!should_skip_ext_fast("test.pdf", &skip_exts));
        assert!(!should_skip_ext_fast("noext", &skip_exts));
    }

    #[test]
    fn test_is_recycle_bin() {
        assert!(is_recycle_bin_path("C:\\$Recycle.Bin\\test.txt"));
        assert!(is_recycle_bin_path("D:\\$RECYCLE.BIN\\file"));
        assert!(!is_recycle_bin_path("C:\\Users\\test.txt"));
    }

    #[test]
    fn test_is_cad_path() {
        assert!(is_cad_path("autocad_2021"));
        assert!(is_cad_path("cad2020"));
        assert!(is_cad_path("tangent"));
        assert!(!is_cad_path("documents"));
    }
}