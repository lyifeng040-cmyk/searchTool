// search_index.rs - 高性能搜索索引（Trie + 倒排索引 + 增量更新）

use radix_trie::{Trie, TrieCommon};
use rustc_hash::FxHashMap;
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use std::path::Path;

/// 搜索索引项
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndexedItem {
    pub name: String,
    #[serde(skip)]  // 可从 name 重建，无需序列化
    pub name_lower: String,
    pub path: String,
    pub file_ref: u64,
    pub parent_ref: u64,
    pub size: u64,
    pub is_dir: bool,
    pub mtime: f64,
}

/// 搜索索引（支持前缀搜索、扩展名过滤、增量更新）
pub struct SearchIndex {
    /// 文件名前缀树（小写）
    name_trie: RwLock<Trie<String, Vec<usize>>>,

    /// 扩展名倒排索引
    ext_index: RwLock<FxHashMap<String, Vec<usize>>>,

    /// 文件引用到索引位置的映射（用于增量更新）
    file_ref_map: RwLock<FxHashMap<u64, usize>>,

    /// 实际的索引项数据
    items: RwLock<Vec<IndexedItem>>,

    /// 脏标记（是否需要持久化）
    dirty: RwLock<bool>,
}

impl SearchIndex {
    pub fn new() -> Self {
        Self {
            name_trie: RwLock::new(Trie::new()),
            ext_index: RwLock::new(FxHashMap::default()),
            file_ref_map: RwLock::new(FxHashMap::default()),
            items: RwLock::new(Vec::new()),
            dirty: RwLock::new(false),
        }
    }

    /// 构建索引
    pub fn build(&self, items: Vec<IndexedItem>) {
        let mut name_trie = self.name_trie.write();
        let mut ext_index = self.ext_index.write();
        let mut file_ref_map = self.file_ref_map.write();
        let mut items_guard = self.items.write();

        // 清空旧索引
        *name_trie = Trie::new();
        ext_index.clear();
        file_ref_map.clear();
        items_guard.clear();

        // 预分配
        items_guard.reserve(items.len());

        for (idx, mut item) in items.into_iter().enumerate() {
            // 预计算小写文件名
            item.name_lower = item.name.to_lowercase();
            let name_lower = &item.name_lower;
            
            // 索引文件名（小写）
            name_trie
                .get_mut(name_lower)
                .map(|indices| indices.push(idx))
                .unwrap_or_else(|| {
                    name_trie.insert(name_lower.clone(), vec![idx]);
                });

            // 索引扩展名
            if let Some(ext_pos) = item.name.rfind('.') {
                let ext = item.name[ext_pos + 1..].to_lowercase();
                ext_index.entry(ext).or_insert_with(Vec::new).push(idx);
            }

            // 索引文件引用
            file_ref_map.insert(item.file_ref, idx);

            items_guard.push(item);
        }

        *self.dirty.write() = true;
    }

    /// 增量更新：添加文件
    pub fn add_file(&self, mut item: IndexedItem) {
        let mut items_guard = self.items.write();
        let idx = items_guard.len();

        // 预计算小写文件名
        item.name_lower = item.name.to_lowercase();
        let name_lower = &item.name_lower;

        // 更新各个索引
        self.name_trie
            .write()
            .get_mut(name_lower)
            .map(|indices| indices.push(idx))
            .unwrap_or_else(|| {
                self.name_trie.write().insert(name_lower.clone(), vec![idx]);
            });

        if let Some(ext_pos) = item.name.rfind('.') {
            let ext = item.name[ext_pos + 1..].to_lowercase();
            self.ext_index
                .write()
                .entry(ext)
                .or_insert_with(Vec::new)
                .push(idx);
        }

        self.file_ref_map.write().insert(item.file_ref, idx);
        items_guard.push(item);

        *self.dirty.write() = true;
    }

    /// 增量更新：删除文件（真正删除）
    pub fn remove_file(&self, file_ref: u64) -> bool {
        let mut file_ref_map = self.file_ref_map.write();
        
        if let Some(&idx) = file_ref_map.get(&file_ref) {
            // 从 file_ref_map 中移除
            file_ref_map.remove(&file_ref);
            drop(file_ref_map);
            
            // 从 items 中移除（通过将其替换为空项）
            let mut items = self.items.write();
            if idx < items.len() {
                // 使用空字符串标记为已删除（避免索引错位）
                items[idx].name = String::new();
                items[idx].path = String::new();
                items[idx].size = 0;
            }
            drop(items);
            
            *self.dirty.write() = true;
            return true;
        }

        false
    }
    
    /// 通过路径删除文件（用于 delete_file 命令）
    pub fn remove_file_by_path(&self, path: &str) -> bool {
        let path_normalized = path.replace('/', "\\").to_lowercase();
        let mut items = self.items.write();
        let mut file_ref_map = self.file_ref_map.write();
        
        let mut found = false;
        for (idx, item) in items.iter_mut().enumerate() {
            if item.path.to_lowercase() == path_normalized {
                // 从 file_ref_map 中移除
                file_ref_map.remove(&item.file_ref);
                
                // 标记为已删除
                item.name = String::new();
                item.path = String::new();
                item.size = 0;
                found = true;
                break;
            }
        }
        
        drop(items);
        drop(file_ref_map);
        
        if found {
            *self.dirty.write() = true;
        }
        
        found
    }

    /// 前缀搜索
    pub fn search_prefix(&self, prefix: &str, max_results: usize) -> Vec<IndexedItem> {
        let prefix_lower = prefix.to_lowercase();
        let name_trie = self.name_trie.read();
        let items = self.items.read();

        let mut results = Vec::new();

        // 使用前缀树查找
        if let Some(subtrie) = name_trie.get_raw_descendant(&prefix_lower) {
            for indices in subtrie.values() {
                for &idx in indices {
                    if let Some(item) = items.get(idx) {
                        // 过滤已删除的项（name为空）
                        if !item.name.is_empty() {
                            results.push(item.clone());
                            if results.len() >= max_results {
                                return results;
                            }
                        }
                    }
                }
            }
        }

        results
    }

    /// 模糊搜索（包含匹配）- 优化版本
    pub fn search_contains(&self, pattern: &str, max_results: usize) -> Vec<IndexedItem> {
        use rayon::prelude::*;

        log::info!("search_contains 开始: pattern='{}', max_results={}", pattern, max_results);
        let pattern_lower = pattern.to_lowercase();
        let items = self.items.read();
        log::info!("获取到 items 读锁，总项数: {}", items.len());

        // 零拷贝：直接使用预计算的 name_lower 字段
        log::info!("开始并行过滤...");
        let filtered: Vec<_> = items
            .par_iter()
            .filter(|item| !item.name.is_empty() && item.name_lower.contains(&pattern_lower))
            .cloned()
            .collect();
        
        log::info!("过滤完成，匹配 {} 项，取前 {} 项", filtered.len(), max_results);
        let result = filtered.into_iter().take(max_results).collect();
        log::info!("search_contains 完成");
        result
    }

    /// 按扩展名搜索
    pub fn search_by_extension(&self, ext: &str, max_results: usize) -> Vec<IndexedItem> {
        let ext_lower = ext.to_lowercase();
        let ext_index = self.ext_index.read();
        let items = self.items.read();

        if let Some(indices) = ext_index.get(&ext_lower) {
            indices
                .iter()
                .take(max_results)
                .filter_map(|&idx| items.get(idx).cloned())
                .collect()
        } else {
            Vec::new()
        }
    }

    /// 持久化到文件
    pub fn save_to_file(&self, path: &Path) -> std::io::Result<()> {
        use std::io::Write;

        let items = self.items.read();
        let serialized = bincode::serialize(&*items)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;

        let mut file = std::fs::File::create(path)?;
        file.write_all(&serialized)?;

        *self.dirty.write() = false;
        Ok(())
    }

    /// 从文件加载
    pub fn load_from_file(&self, path: &Path) -> std::io::Result<()> {
        let file = std::fs::File::open(path)?;
        let mmap = unsafe { memmap2::Mmap::map(&file)? };

        let items: Vec<IndexedItem> = bincode::deserialize(&mmap)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;

        self.build(items);
        *self.dirty.write() = false;

        Ok(())
    }

    pub fn is_dirty(&self) -> bool {
        *self.dirty.read()
    }

    pub fn item_count(&self) -> usize {
        // 只统计未删除的项（name非空）
        self.items.read().iter().filter(|item| !item.name.is_empty()).count()
    }

    /// 修改时间范围搜索（返回修改时间在 [min_mtime, max_mtime] 之间的项，max_results 上限）
    pub fn search_by_mtime_range(
        &self,
        min_mtime: f64,
        max_mtime: f64,
        max_results: usize,
    ) -> Vec<IndexedItem> {
        let items = self.items.read();
        let mut out = Vec::with_capacity(max_results.min(1024));
        for it in items.iter() {
            // 过滤范围
            if it.mtime >= min_mtime && it.mtime <= max_mtime {
                out.push(it.clone());
                if out.len() >= max_results {
                    break;
                }
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_prefix_search() {
        let index = SearchIndex::new();

        let items = vec![
            IndexedItem {
                name: "test.txt".to_string(),
                name_lower: "test.txt".to_string(),
                path: "C:\\test.txt".to_string(),
                file_ref: 1,
                parent_ref: 0,
                size: 100,
                is_dir: false,
                mtime: 0.0,
            },
            IndexedItem {
                name: "testing.doc".to_string(),
                name_lower: "testing.doc".to_string(),
                path: "C:\\testing.doc".to_string(),
                file_ref: 2,
                parent_ref: 0,
                size: 200,
                is_dir: false,
                mtime: 0.0,
            },
        ];

        index.build(items);

        let results = index.search_prefix("test", 10);
        assert_eq!(results.len(), 2);
    }

    #[test]
    fn test_contains_search() {
        let index = SearchIndex::new();

        let items = vec![IndexedItem {
            name: "my_test_file.txt".to_string(),
            name_lower: "my_test_file.txt".to_string(),
            path: "C:\\my_test_file.txt".to_string(),
            file_ref: 1,
            parent_ref: 0,
            size: 100,
            is_dir: false,
            mtime: 0.0,
        }];

        index.build(items);

        let results = index.search_contains("test", 10);
        assert_eq!(results.len(), 1);
    }

    #[test]
    fn test_extension_search() {
        let index = SearchIndex::new();

        let items = vec![
            IndexedItem {
                name: "file1.txt".to_string(),
                name_lower: "file1.txt".to_string(),
                path: "C:\\file1.txt".to_string(),
                file_ref: 1,
                parent_ref: 0,
                size: 100,
                is_dir: false,
                mtime: 0.0,
            },
            IndexedItem {
                name: "file2.doc".to_string(),
                name_lower: "file2.doc".to_string(),
                path: "C:\\file2.doc".to_string(),
                file_ref: 2,
                parent_ref: 0,
                size: 200,
                is_dir: false,
                mtime: 0.0,
            },
        ];

        index.build(items);

        let results = index.search_by_extension("txt", 10);
        assert_eq!(results.len(), 1);
    }
}
