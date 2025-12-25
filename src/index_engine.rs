// 高性能搜索索引引擎
// 支持全文搜索、过滤、排序

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;
use walkdir::WalkDir;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndexedFile {
    pub name: String,
    pub path: String,
    pub size: u64,
    pub mtime: u64,
    pub is_dir: bool,
    pub extension: String,
}

pub struct SearchIndex {
    // 内存索引: 文件名 → 文件列表
    name_index: HashMap<String, Vec<IndexedFile>>,
    // 扩展名索引
    ext_index: HashMap<String, Vec<IndexedFile>>,
    // 全文索引（三元组）
    trigram_index: HashMap<String, Vec<usize>>,
    // 所有文件
    all_files: Vec<IndexedFile>,
}

impl SearchIndex {
    pub fn new() -> Self {
        SearchIndex {
            name_index: HashMap::new(),
            ext_index: HashMap::new(),
            trigram_index: HashMap::new(),
            all_files: Vec::new(),
        }
    }

    /// 构建完整索引
    pub fn build_index(&mut self, root_path: &str) -> Result<usize, String> {
        let mut count = 0;

        // 使用 walkdir 遍历所有文件
        for entry in WalkDir::new(root_path)
            .into_iter()
            .filter_entry(|e| {
                // 跳过隐藏目录和系统目录
                let name = e.file_name().to_string_lossy().to_lowercase();
                !name.starts_with('.') 
                    && name != "system volume information"
                    && name != "$recycle.bin"
                    && name != "pagefile.sys"
                    && name != "hiberfil.sys"
            })
            .filter_map(|e| e.ok())
            .take(500000) // 限制索引文件数
        {
            let path = entry.path();
            let name = path
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("unknown")
                .to_string();
            let name_lower = name.to_lowercase();

            if let Ok(metadata) = path.metadata() {
                let extension = path
                    .extension()
                    .and_then(|e| e.to_str())
                    .unwrap_or("")
                    .to_lowercase();

                let file = IndexedFile {
                    name: name.clone(),
                    path: path.display().to_string(),
                    size: metadata.len(),
                    mtime: 0, // 可扩展
                    is_dir: metadata.is_dir(),
                    extension: extension.clone(),
                };

                // 添加到名字索引
                self.name_index
                    .entry(name_lower.clone())
                    .or_insert_with(Vec::new)
                    .push(file.clone());

                // 添加到扩展名索引
                if !extension.is_empty() {
                    self.ext_index
                        .entry(extension)
                        .or_insert_with(Vec::new)
                        .push(file.clone());
                }

                // 添加三元组索引用于快速搜索
                self.add_trigrams(&name_lower, self.all_files.len());

                self.all_files.push(file);
                count += 1;
            }
        }

        Ok(count)
    }

    /// 添加三元组索引
    fn add_trigrams(&mut self, text: &str, file_idx: usize) {
        if text.len() < 3 {
            return;
        }

        let chars: Vec<char> = text.chars().collect();
        for i in 0..=chars.len().saturating_sub(3) {
            let trigram: String = chars[i..i + 3].iter().collect();
            self.trigram_index
                .entry(trigram)
                .or_insert_with(Vec::new)
                .push(file_idx);
        }
    }

    /// 快速搜索（支持多关键词）
    pub fn search(&self, keywords: &[String], filters: &SearchFilters) -> Vec<IndexedFile> {
        if keywords.is_empty() {
            return vec![];
        }

        let mut results = Vec::new();
        let keywords_lower: Vec<String> = keywords.iter().map(|k| k.to_lowercase()).collect();

        // 使用三元组索引快速过滤候选
        let candidates = if keywords_lower.len() == 1 && keywords_lower[0].len() >= 3 {
            // 单个关键词且 >= 3 字符：使用三元组索引
            self.get_candidates_by_trigram(&keywords_lower[0])
        } else {
            // 多关键词或短关键词：扫描所有文件
            (0..self.all_files.len()).collect()
        };

        // 精确匹配
        for idx in candidates {
            if idx >= self.all_files.len() {
                continue;
            }

            let file = &self.all_files[idx];
            let file_name_lower = file.name.to_lowercase();

            // 所有关键词都要匹配
            let mut matched = true;
            for keyword in &keywords_lower {
                if !file_name_lower.contains(keyword) {
                    matched = false;
                    break;
                }
            }

            if !matched {
                continue;
            }

            // 应用过滤器
            if !self.apply_filters(file, filters) {
                continue;
            }

            results.push(file.clone());
        }

        // 限制结果数量
        results.truncate(1000);
        results
    }

    /// 通过三元组索引获取候选文件
    fn get_candidates_by_trigram(&self, keyword: &str) -> Vec<usize> {
        if keyword.len() < 3 {
            return (0..self.all_files.len()).collect();
        }

        let chars: Vec<char> = keyword.chars().collect();
        let mut candidates = None;

        for i in 0..=chars.len().saturating_sub(3) {
            let trigram: String = chars[i..i + 3].iter().collect();
            if let Some(indices) = self.trigram_index.get(&trigram) {
                candidates = match candidates {
                    None => Some(indices.clone()),
                    Some(mut prev) => {
                        prev.retain(|idx| indices.contains(idx));
                        Some(prev)
                    }
                };
            }
        }

        candidates.unwrap_or_default()
    }

    /// 应用过滤器
    fn apply_filters(&self, file: &IndexedFile, filters: &SearchFilters) -> bool {
        // 扩展名过滤
        if let Some(ref exts) = filters.ext {
            let matched = exts.iter().any(|e| {
                file.extension.eq_ignore_ascii_case(e) 
                    || file.extension.eq_ignore_ascii_case(&format!(".{}", e))
            });
            if !matched {
                return false;
            }
        }

        // 大小过滤
        if let Some(min) = filters.size_min {
            if file.size < min {
                return false;
            }
        }
        if let Some(max) = filters.size_max {
            if file.size > max {
                return false;
            }
        }

        // 目录/文件过滤
        if let Some(only_dir) = filters.only_dir {
            if only_dir && !file.is_dir {
                return false;
            }
        }

        true
    }

    /// 获取索引统计
    pub fn get_stats(&self) -> IndexStats {
        IndexStats {
            total_files: self.all_files.len(),
            total_dirs: self.all_files.iter().filter(|f| f.is_dir).count(),
            total_size: self.all_files.iter().map(|f| f.size).sum(),
        }
    }
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct SearchFilters {
    pub ext: Option<Vec<String>>,
    pub size_min: Option<u64>,
    pub size_max: Option<u64>,
    pub only_dir: Option<bool>,
}

#[derive(Debug, Serialize)]
pub struct IndexStats {
    pub total_files: usize,
    pub total_dirs: usize,
    pub total_size: u64,
}
