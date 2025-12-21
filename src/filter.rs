//! 文件过滤规则

use std::collections::HashSet;
use std::sync::LazyLock;

/// 需要跳过的目录（小写）
pub static SKIP_DIRS: LazyLock<HashSet<&'static str>> = LazyLock::new(|| {
    [
        "windows", "program files", "program files (x86)", "programdata",
        "$recycle.bin", "system volume information", "appdata", "boot",
        "node_modules", ".git", "__pycache__", "site-packages", "sys",
        "recovery", "config.msi", "$windows.~bt", "$windows.~ws",
        "cache", "caches", "temp", "tmp", "logs", "log",
        ".vscode", ".idea", ".vs", "obj", "bin", "debug", "release",
        "packages", ".nuget", "bower_components",
    ].into_iter().collect()
});

/// 需要跳过的扩展名（小写，带点）
pub static SKIP_EXTS: LazyLock<HashSet<&'static str>> = LazyLock::new(|| {
    [
        ".lsp", ".fas", ".lnk", ".html", ".htm", ".xml", ".ini", ".lsp_bak",
        ".cuix", ".arx", ".crx", ".fx", ".dbx", ".kid", ".ico", ".rz",
        ".dll", ".sys", ".tmp", ".log", ".dat", ".db", ".pdb", ".obj",
        ".pyc", ".class", ".cache", ".lock",
    ].into_iter().collect()
});

/// 检查目录是否应该跳过
pub fn should_skip_dir(name_lower: &str) -> bool {
    if SKIP_DIRS.contains(name_lower) {
        return true;
    }
    
    // CAD 相关目录
    if name_lower.contains("cad20") || name_lower.contains("autocad_20") {
        return true;
    }
    
    if name_lower.contains("tangent") {
        return true;
    }
    
    false
}

/// 检查路径是否应该跳过
pub fn should_skip_path(path_lower: &str, allowed_paths: Option<&[String]>) -> bool {
    // 如果在允许列表中，不跳过
    if let Some(allowed) = allowed_paths {
        for ap in allowed {
            if path_lower.starts_with(ap) {
                return false;
            }
        }
    }
    
    // 检查路径中的每个部分
    for part in path_lower.split('\\') {
        if SKIP_DIRS.contains(part) {
            return true;
        }
    }
    
    if path_lower.contains("site-packages") {
        return true;
    }
    
    if path_lower.contains("cad20") || path_lower.contains("autocad_20") {
        return true;
    }
    
    if path_lower.contains("tangent") {
        return true;
    }
    
    false
}

/// 检查扩展名是否应该跳过
pub fn should_skip_ext(ext_lower: &str) -> bool {
    SKIP_EXTS.contains(ext_lower)
}

/// 检查路径是否在允许列表中
pub fn is_in_allowed_paths(path_lower: &str, allowed_paths: &[String]) -> bool {
    for ap in allowed_paths {
        if path_lower.starts_with(ap) || path_lower == ap.as_str() {
            return true;
        }
    }
    false
}