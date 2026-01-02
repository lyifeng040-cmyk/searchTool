// 工具函数模块
use std::path::{Path, PathBuf};
use chrono::{DateTime, Local};

#[derive(Debug, Clone, Default)]
pub struct ParsedQuery {
    /// 剥离语法后的“纯关键词”（用于 FTS/模糊/正则）
    pub keyword: String,
    /// 扩展名过滤（统一小写，带前导点，如 ".pdf"）
    pub exts: Vec<String>,
    /// 仅保留最近修改：mtime >= min_mtime（unix seconds）
    pub min_mtime: Option<f64>,
    /// 文件大小过滤（bytes）
    /// - min_size: size >= min_size
    /// - max_size: size <= max_size
    pub min_size: Option<u64>,
    pub max_size: Option<u64>,
    /// name: 作用域 token（只匹配文件名）
    pub name_terms: Vec<String>,
    /// path: 作用域 token（只匹配完整路径）
    pub path_terms: Vec<String>,
    /// is:dir / is:file
    pub is_dir: bool,
    pub is_file: bool,
}

/// 解析增强语法（主窗口 + mini 窗口通用）
/// - ext:pdf / ext:.pdf / ext:pdf,docx  （可多次出现）
/// - dm:7d / dm:24h / dm:30m          （最近修改）
/// - size:>100mb / size:<=1gb / size:=1024
/// - name:foo / path:bar
/// - is:dir / is:file
/// 其他 token 组合成 keyword
pub fn parse_advanced_query(input: &str) -> ParsedQuery {
    let mut out = ParsedQuery::default();
    let s = input.replace(';', " ");
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);

    let mut kw_parts: Vec<String> = Vec::new();

    fn parse_bytes(s: &str) -> Option<u64> {
        let t = s.trim().to_lowercase();
        if t.is_empty() {
            return None;
        }
        // 拆出数值 + 单位（允许 100, 100b, 10kb, 10k, 1mb, 1m, 2gb, 2g, 1tb, 1t）
        let mut num = String::new();
        let mut unit = String::new();
        for ch in t.chars() {
            if ch.is_ascii_digit() || ch == '.' {
                if unit.is_empty() {
                    num.push(ch);
                } else {
                    // 单位后出现数字：不支持
                    return None;
                }
            } else if !ch.is_whitespace() {
                unit.push(ch);
            }
        }
        if num.is_empty() {
            return None;
        }
        let n = num.parse::<f64>().ok()?;
        if !n.is_finite() || n < 0.0 {
            return None;
        }
        let mul = match unit.as_str() {
            "" | "b" => 1f64,
            "k" | "kb" => 1024f64,
            "m" | "mb" => 1024f64 * 1024f64,
            "g" | "gb" => 1024f64 * 1024f64 * 1024f64,
            "t" | "tb" => 1024f64 * 1024f64 * 1024f64 * 1024f64,
            _ => return None,
        };
        let v = n * mul;
        if v > (u64::MAX as f64) {
            return Some(u64::MAX);
        }
        Some(v.round() as u64)
    }

    fn apply_size_filter(out: &mut ParsedQuery, op: &str, value: u64) {
        match op {
            ">" => {
                let min = value.saturating_add(1);
                out.min_size = Some(out.min_size.map(|v| v.max(min)).unwrap_or(min));
            }
            ">=" => {
                out.min_size = Some(out.min_size.map(|v| v.max(value)).unwrap_or(value));
            }
            "<" => {
                let max = value.saturating_sub(1);
                out.max_size = Some(out.max_size.map(|v| v.min(max)).unwrap_or(max));
            }
            "<=" => {
                out.max_size = Some(out.max_size.map(|v| v.min(value)).unwrap_or(value));
            }
            "=" | "==" => {
                out.min_size = Some(out.min_size.map(|v| v.max(value)).unwrap_or(value));
                out.max_size = Some(out.max_size.map(|v| v.min(value)).unwrap_or(value));
            }
            _ => {}
        }
    }

    for raw in s.split_whitespace() {
        let token = raw.trim().trim_matches(|c: char| c == ',' || c == ';');
        if token.is_empty() {
            continue;
        }
        let lower = token.to_lowercase();

        if let Some(rest) = lower.strip_prefix("ext:") {
            // 支持 ext:pdf,docx
            for e in rest.split(|c| c == ',' || c == '|' || c == ';') {
                let e = e.trim().trim_matches('.');
                if e.is_empty() {
                    continue;
                }
                out.exts.push(format!(".{}", e.to_lowercase()));
            }
            continue;
        }

        if let Some(rest) = lower.strip_prefix("dm:") {
            // dm:7d / dm:24h / dm:30m
            let r = rest.trim().trim_matches(|c: char| c == ',' || c == ';');
            if r.len() >= 2 {
                let (num_part, unit_part) = r.split_at(r.len() - 1);
                if let Ok(n) = num_part.parse::<f64>() {
                    let secs = match unit_part {
                        "d" => n * 86400.0,
                        "h" => n * 3600.0,
                        "m" => n * 60.0,
                        _ => 0.0,
                    };
                    if secs > 0.0 {
                        let min_mtime = (now - secs).max(0.0);
                        out.min_mtime = Some(out.min_mtime.map(|v| v.max(min_mtime)).unwrap_or(min_mtime));
                    }
                }
            }
            continue;
        }

        if let Some(rest) = lower.strip_prefix("is:") {
            let r = rest.trim().trim_matches(|c: char| c == ',' || c == ';');
            if r == "dir" || r == "folder" {
                out.is_dir = true;
                out.is_file = false;
            } else if r == "file" {
                out.is_file = true;
                out.is_dir = false;
            }
            continue;
        }

        if let Some(rest) = lower.strip_prefix("name:") {
            let r = rest.trim().trim_matches(|c: char| c == ',' || c == ';');
            if !r.is_empty() {
                out.name_terms.push(r.to_string());
            }
            continue;
        }

        if let Some(rest) = lower.strip_prefix("path:") {
            let r = rest.trim().trim_matches(|c: char| c == ',' || c == ';');
            if !r.is_empty() {
                out.path_terms.push(r.to_string());
            }
            continue;
        }

        if let Some(rest) = lower.strip_prefix("size:") {
            let r = rest.trim().trim_matches(|c: char| c == ',' || c == ';');
            // 支持：> >= < <= = 以及省略符号（视为 =）
            let (op, val_str) = if let Some(x) = r.strip_prefix(">=") {
                (">=", x)
            } else if let Some(x) = r.strip_prefix("<=") {
                ("<=", x)
            } else if let Some(x) = r.strip_prefix("==") {
                ("==", x)
            } else if let Some(x) = r.strip_prefix('>') {
                (">", x)
            } else if let Some(x) = r.strip_prefix('<') {
                ("<", x)
            } else if let Some(x) = r.strip_prefix('=') {
                ("=", x)
            } else {
                ("=", r)
            };
            if let Some(b) = parse_bytes(val_str) {
                apply_size_filter(&mut out, op, b);
            }
            continue;
        }

        kw_parts.push(token.to_string());
    }

    out.keyword = kw_parts.join(" ").trim().to_string();
    // 去重 ext
    out.exts.sort();
    out.exts.dedup();
    out.name_terms = out
        .name_terms
        .into_iter()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    out.path_terms = out
        .path_terms
        .into_iter()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    out
}

// 格式化文件大小
pub fn format_size(size: u64) -> String {
    const KB: u64 = 1024;
    const MB: u64 = KB * 1024;
    const GB: u64 = MB * 1024;
    const TB: u64 = GB * 1024;

    if size >= TB {
        format!("{:.2} TB", size as f64 / TB as f64)
    } else if size >= GB {
        format!("{:.2} GB", size as f64 / GB as f64)
    } else if size >= MB {
        format!("{:.2} MB", size as f64 / MB as f64)
    } else if size >= KB {
        format!("{:.2} KB", size as f64 / KB as f64)
    } else {
        format!("{} B", size)
    }
}

// 格式化时间戳
pub fn format_time(timestamp: f64) -> String {
    if timestamp <= 0.0 {
        return String::new();
    }

    let dt = DateTime::from_timestamp(timestamp as i64, 0)
        .unwrap_or_else(|| DateTime::from_timestamp(0, 0).unwrap());
    let local: DateTime<Local> = dt.into();
    local.format("%Y-%m-%d %H:%M:%S").to_string()
}

// 获取文件扩展名（小写）
pub fn get_extension(path: &str) -> String {
    Path::new(path)
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| format!(".{}", e.to_lowercase()))
        .unwrap_or_default()
}

// 模糊匹配
/// Python 端 fuzzy_match 的 Rust 等价：返回匹配分数
/// - 完整包含：100
/// - 子序列匹配：60 + len(keyword)*5
/// - 首字母匹配：50
/// - 否则：0
pub fn fuzzy_score(keyword: &str, filename: &str) -> u32 {
    let keyword = keyword.to_lowercase();
    let filename_lower = filename.to_lowercase();

    if keyword.is_empty() {
        return 0;
    }

    if filename_lower.contains(&keyword) {
        return 100;
    }

    // 子序列匹配：对齐 Python 的逐字符推进逻辑（避免 keyword.len() 的字节长度陷阱，也避免 nth 的 O(n^2)）
    let kw_chars: Vec<char> = keyword.chars().collect();
    let mut ki = 0usize;
    for ch in filename_lower.chars() {
        if ki < kw_chars.len() && ch == kw_chars[ki] {
            ki += 1;
            if ki == kw_chars.len() {
                break;
            }
        }
    }
    if ki == kw_chars.len() {
        return 60 + (ki as u32) * 5;
    }

    // initials: split on whitespace, -, _, .
    let mut initials = String::new();
    let mut new_word = true;
    for ch in filename_lower.chars() {
        if ch == ' ' || ch == '-' || ch == '_' || ch == '.' {
            new_word = true;
            continue;
        }
        if new_word {
            initials.push(ch);
            new_word = false;
        }
    }
    if initials.contains(&keyword) {
        return 50;
    }

    0
}

pub fn fuzzy_match(keyword: &str, filename: &str) -> bool {
    fuzzy_score(keyword, filename) >= 50
}

/// 从正则模式中提取一个“尽量靠谱的普通文本片段”，用于先做候选集过滤（再用真正正则二次过滤）。
/// 目标是避免 `LIKE %a.*b%` 这种把元字符当普通字符导致候选集极小/为 0 的问题。
///
/// 规则（偏保守）：
/// - 把未转义的正则元字符当作分隔符
/// - `\x` 视为字面量 `x`
/// - 取最长的连续片段
pub fn extract_regex_seed(pattern: &str) -> String {
    let mut best = String::new();
    let mut cur = String::new();
    let mut escaped = false;

    // 常见正则元字符（未转义时）
    fn is_meta(c: char) -> bool {
        matches!(
            c,
            '.' | '*' | '+' | '?' | '|' | '(' | ')' | '[' | ']' | '{' | '}' | '^' | '$'
        )
    }

    for ch in pattern.chars() {
        if escaped {
            // 把转义后的字符当字面量
            cur.push(ch);
            escaped = false;
            continue;
        }
        if ch == '\\' {
            escaped = true;
            continue;
        }
        if is_meta(ch) {
            if cur.chars().count() > best.chars().count() {
                best = cur.clone();
            }
            cur.clear();
            continue;
        }
        cur.push(ch);
    }
    if cur.chars().count() > best.chars().count() {
        best = cur;
    }

    best.trim().to_string()
}

// 解析搜索范围
pub fn parse_search_scope(scope: &str) -> Vec<String> {
    if scope.is_empty() || scope == "all" {
        return get_drives()
            .into_iter()
            .map(|s| normalize_scope_path(&s))
            .collect();
    }
    
    // 支持多个路径，用 | 分隔
    scope.split('|')
        .map(|s| normalize_scope_path(s.trim()))
        .filter(|s| !s.is_empty() && Path::new(s).exists())
        .collect()
}

pub fn normalize_scope_path(s: &str) -> String {
    let s = s.trim();
    if s.is_empty() {
        return String::new();
    }

    // Windows: "C:" -> "C:\"
    #[cfg(windows)]
    {
        if s.len() == 2 && s.as_bytes()[1] == b':' {
            return format!("{}\\", s);
        }
        if s.len() == 3 && s.as_bytes()[1] == b':' && (s.as_bytes()[2] == b'\\' || s.as_bytes()[2] == b'/') {
            return format!("{}\\", &s[..2]);
        }
    }

    // 统一分隔符
    let mut out = s.replace('/', "\\");
    // 目录去掉尾部空格，保留末尾反斜杠由调用者决定
    while out.ends_with(' ') {
        out.pop();
    }
    out
}

// 检查路径是否应该跳过
pub fn should_skip_path(path: &Path) -> bool {
    let path_str = path.to_string_lossy();
    should_skip_path_str(&path_str)
}

pub fn should_skip_path_str(path: &str) -> bool {
    let path_lower = path.to_lowercase();

    const SKIP_DIRS: &[&str] = &[
        "windows", "program files", "program files (x86)", "programdata",
        "$recycle.bin", "system volume information", "appdata", "boot",
        "node_modules", ".git", "__pycache__", "site-packages", "sys",
        "recovery", "config.msi", "$windows.~bt", "$windows.~ws",
        "cache", "caches", "temp", "tmp", "logs", "log",
        ".vscode", ".idea", ".vs", "obj", "bin", "debug", "release",
        "packages", ".nuget", "bower_components",
    ];

    for skip_dir in SKIP_DIRS {
        if path_lower.contains(skip_dir) {
            return true;
        }
    }

    // 检查CAD路径
    if path_lower.contains("cad20") || path_lower.contains("autocad_20") || path_lower.contains("tangent") {
        return true;
    }

    false
}

// 检查扩展名是否应该跳过
pub fn should_skip_ext(ext: &str) -> bool {
    const SKIP_EXTS: &[&str] = &[
        ".lsp", ".fas", ".lnk", ".html", ".htm", ".xml", ".ini",
        ".lsp_bak", ".cuix", ".arx", ".crx", ".fx", ".dbx", ".kid",
        ".ico", ".rz", ".dll", ".sys", ".tmp", ".log", ".dat",
        ".db", ".pdb", ".obj", ".pyc", ".class", ".cache", ".lock",
    ];

    SKIP_EXTS.contains(&ext.to_lowercase().as_str())
}

// （忽略规则配置化已移除：实时/索引回到同一套硬编码过滤原则）

// 获取所有可用驱动器
#[cfg(windows)]
pub fn get_drives() -> Vec<String> {
    use std::ffi::OsString;
    use std::os::windows::ffi::OsStringExt;

    let mut drives = Vec::new();
    
    unsafe {
        let mask = windows::Win32::Storage::FileSystem::GetLogicalDrives();
        for i in 0..26 {
            if (mask & (1 << i)) != 0 {
                let drive = format!("{}:", (b'A' + i) as char);
                drives.push(drive);
            }
        }
    }

    drives
}

#[cfg(not(windows))]
pub fn get_drives() -> Vec<String> {
    vec!["/".to_string()]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_format_size() {
        assert_eq!(format_size(0), "0 B");
        assert_eq!(format_size(1023), "1023 B");
        assert_eq!(format_size(1024), "1.00 KB");
        assert_eq!(format_size(1024 * 1024), "1.00 MB");
    }

    #[test]
    fn test_fuzzy_match() {
        assert!(fuzzy_match("abc", "aabbcc"));
        assert!(fuzzy_match("test", "test.txt"));
        assert!(!fuzzy_match("xyz", "abc"));
    }

    #[test]
    fn test_should_skip_path() {
        assert!(should_skip_path_str("C:\\Windows\\System32"));
        assert!(should_skip_path_str("C:\\Program Files\\test"));
        assert!(!should_skip_path_str("C:\\Users\\Documents"));
    }
    
    #[test]
    fn test_parse_search_scope() {
        let scope = parse_search_scope("C:\\Users|D:\\Data");
        assert!(scope.len() >= 1);
    }
}

