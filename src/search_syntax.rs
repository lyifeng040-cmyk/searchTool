// 搜索语法解析器 - 支持 Everything 风格的增强语法
use regex::Regex;
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Default)]
pub struct SearchFilters {
    pub ext: Vec<String>,
    pub size_min: u64,
    pub size_max: u64,
    pub date_after: Option<u64>,
    pub path: String,
    pub name_pattern: String,
}

pub struct SearchSyntaxParser;

impl SearchSyntaxParser {
    pub fn parse(query: &str) -> (String, SearchFilters) {
        let mut filters = SearchFilters::default();
        let mut text = query.to_string();

        // 提取各种语法
        text = Self::extract_ext(&text, &mut filters);
        text = Self::extract_size(&text, &mut filters);
        text = Self::extract_date(&text, &mut filters);
        text = Self::extract_path(&text, &mut filters);
        text = Self::extract_name(&text, &mut filters);

        // 清理多余空格
        let pure_keyword = text.split_whitespace().collect::<Vec<_>>().join(" ");

        (pure_keyword, filters)
    }

    fn extract_ext(text: &str, filters: &mut SearchFilters) -> String {
        let re = Regex::new(r"(?i)ext:([a-zA-Z0-9,]+)").unwrap();
        let result = re.replace_all(text, "");
        
        for cap in re.captures_iter(text) {
            if let Some(m) = cap.get(1) {
                let exts: Vec<String> = m.as_str()
                    .split(',')
                    .filter_map(|e| {
                        let e = e.trim().to_lowercase();
                        if !e.is_empty() { Some(e) } else { None }
                    })
                    .collect();
                filters.ext.extend(exts);
            }
        }
        
        result.to_string()
    }

    fn extract_size(text: &str, filters: &mut SearchFilters) -> String {
        let mut result = text.to_string();

        // size:>100mb
        let re_min = Regex::new(r"(?i)size:>(\d+(?:\.\d+)?)(kb|mb|gb)").unwrap();
        if let Some(cap) = re_min.captures(&result) {
            if let (Some(num), Some(unit)) = (cap.get(1), cap.get(2)) {
                let n: f64 = num.as_str().parse().unwrap_or(0.0);
                filters.size_min = Self::parse_size(n, unit.as_str());
            }
        }
        result = re_min.replace_all(&result, "").to_string();

        // size:<1gb
        let re_max = Regex::new(r"(?i)size:<(\d+(?:\.\d+)?)(kb|mb|gb)").unwrap();
        if let Some(cap) = re_max.captures(&result) {
            if let (Some(num), Some(unit)) = (cap.get(1), cap.get(2)) {
                let n: f64 = num.as_str().parse().unwrap_or(0.0);
                filters.size_max = Self::parse_size(n, unit.as_str());
            }
        }
        result = re_max.replace_all(&result, "").to_string();

        result
    }

    fn parse_size(num: f64, unit: &str) -> u64 {
        let multiplier = match unit.to_lowercase().as_str() {
            "kb" => 1024,
            "mb" => 1024 * 1024,
            "gb" => 1024 * 1024 * 1024,
            _ => 1,
        };
        (num * multiplier as f64) as u64
    }

    fn extract_date(text: &str, filters: &mut SearchFilters) -> String {
        let re = Regex::new(r"(?i)dm:(\S+)").unwrap();
        let result = re.replace_all(text, "");

        if let Some(cap) = re.captures(text) {
            if let Some(date_str) = cap.get(1) {
                let ds = date_str.as_str().to_lowercase();
                let now = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs();

                let secs_in_day = 86400u64;

                filters.date_after = match ds.as_str() {
                    "today" => Some(now - now % secs_in_day),
                    "yesterday" => Some(now - secs_in_day - now % secs_in_day),
                    "week" => Some(now - 7 * secs_in_day),
                    "month" => Some(now - 30 * secs_in_day),
                    "year" => {
                        // 今年1月1日
                        let year_start = now - (now % (365 * secs_in_day));
                        Some(year_start)
                    }
                    _ => {
                        // 解析相对时间：7d, 12h, 30m
                        let rel_re = Regex::new(r"^(\d+)([dhm])$").unwrap();
                        if let Some(rel_cap) = rel_re.captures(&ds) {
                            if let (Some(num), Some(unit)) = (rel_cap.get(1), rel_cap.get(2)) {
                                let n: u64 = num.as_str().parse().unwrap_or(0);
                                match unit.as_str() {
                                    "d" => Some(now - n * secs_in_day),
                                    "h" => Some(now - n * 3600),
                                    "m" => Some(now - n * 60),
                                    _ => None,
                                }
                            } else {
                                None
                            }
                        } else {
                            None
                        }
                    }
                };
            }
        }

        result.to_string()
    }

    fn extract_path(text: &str, filters: &mut SearchFilters) -> String {
        // path:"C:\Program Files" 或 path:D:\
        let re_quoted = Regex::new(r#"(?i)path:"([^"]+)""#).unwrap();
        if let Some(cap) = re_quoted.captures(text) {
            if let Some(p) = cap.get(1) {
                filters.path = p.as_str().to_string();
            }
            return re_quoted.replace_all(text, "").to_string();
        }

        let re = Regex::new(r"(?i)path:(\S+)").unwrap();
        if let Some(cap) = re.captures(text) {
            if let Some(p) = cap.get(1) {
                filters.path = p.as_str().to_string();
            }
        }
        re.replace_all(text, "").to_string()
    }

    fn extract_name(text: &str, filters: &mut SearchFilters) -> String {
        let re = Regex::new(r"(?i)name:(\S+)").unwrap();
        if let Some(cap) = re.captures(text) {
            if let Some(n) = cap.get(1) {
                filters.name_pattern = n.as_str().to_string();
            }
        }
        re.replace_all(text, "").to_string()
    }

    pub fn apply_filters(results: Vec<crate::commands::SearchResult>, filters: &SearchFilters) -> Vec<crate::commands::SearchResult> {
        results
            .into_iter()
            .filter(|item| Self::match_item(item, filters))
            .collect()
    }

    fn match_item(item: &crate::commands::SearchResult, filters: &SearchFilters) -> bool {
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
}
