// 数据库模块 - 使用rusqlite + FTS5全文搜索
use rusqlite::{Connection, params, Result as SqlResult, OptionalExtension};
use rusqlite::types::Value;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::Mutex;
use anyhow::Result;
use crate::utils;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileRecord {
    pub id: Option<i64>,
    pub filename: String,
    pub filename_lower: String,
    pub full_path: String,
    pub parent_dir: String,
    pub extension: String,
    pub size: u64,
    pub mtime: f64,
    pub is_dir: bool,
}

pub struct Database {
    conn: Mutex<Connection>,
}

impl Database {
    pub fn new() -> Result<Self> {
        let db_path = Self::db_file_path()?;
        
        if let Some(parent) = db_path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let conn = Connection::open(&db_path)?;
        
        // 优化设置
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA synchronous=NORMAL;
             PRAGMA cache_size=-64000;
             PRAGMA temp_store=MEMORY;
             PRAGMA mmap_size=30000000000;"
        )?;

        let db = Self {
            conn: Mutex::new(conn),
        };

        db.init_schema()?;
        Ok(db)
    }

    fn db_file_path() -> Result<PathBuf> {
        let home = dirs::home_dir().ok_or_else(|| anyhow::anyhow!("无法获取用户目录"))?;
        Ok(home.join(".filesearch").join("index.db"))
    }

    fn init_schema(&self) -> Result<()> {
        let conn = self.conn.lock().unwrap();

        // 创建主表
        conn.execute(
            "CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                filename_lower TEXT NOT NULL,
                full_path TEXT UNIQUE NOT NULL,
                parent_dir TEXT NOT NULL,
                extension TEXT,
                size INTEGER DEFAULT 0,
                mtime REAL DEFAULT 0,
                is_dir INTEGER DEFAULT 0
            )",
            [],
        )?;

        // 创建FTS5全文搜索表
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS files_fts 
             USING fts5(filename, content=files, content_rowid=id)",
            [],
        )?;

        // 创建触发器：插入时同步到FTS
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                INSERT INTO files_fts(rowid, filename) VALUES (new.id, new.filename);
             END",
            [],
        )?;

        // 创建触发器：删除时同步到FTS
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, filename) VALUES('delete', old.id, old.filename);
             END",
            [],
        )?;

        // 创建索引
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fn ON files(filename_lower)",
            [],
        )?;
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_parent ON files(parent_dir)",
            [],
        )?;
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ext ON files(extension)",
            [],
        )?;

        // 元数据表
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )",
            [],
        )?;

        log::info!("✅ 数据库初始化完成");
        Ok(())
    }

    // 批量插入文件记录
    pub fn batch_insert(&self, records: &[FileRecord]) -> Result<usize> {
        let conn = self.conn.lock().unwrap();
        let mut inserted = 0;

        let tx = conn.unchecked_transaction()?;

        {
            let mut stmt = tx.prepare_cached(
                "INSERT OR REPLACE INTO files 
                 (filename, filename_lower, full_path, parent_dir, extension, size, mtime, is_dir)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)"
            )?;

            for record in records {
                match stmt.execute(params![
                    record.filename,
                    record.filename_lower,
                    record.full_path,
                    record.parent_dir,
                    record.extension,
                    record.size as i64,
                    record.mtime,
                    if record.is_dir { 1 } else { 0 },
                ]) {
                    Ok(_) => inserted += 1,
                    Err(e) => log::warn!("插入失败: {} - {}", record.full_path, e),
                }
            }
        }

        tx.commit()?;
        Ok(inserted)
    }

    // 搜索文件（支持FTS5全文搜索）
    pub fn search(
        &self,
        keyword: &str,
        use_fts: bool,
        use_regex: bool,
        limit: usize,
    ) -> Result<Vec<FileRecord>> {
        let conn = self.conn.lock().unwrap();
        let mut results = Vec::new();

        if use_fts && !keyword.is_empty() {
            // 使用FTS5全文搜索
            let query = format!("{}*", keyword);
            let mut stmt = conn.prepare(
                "SELECT f.id, f.filename, f.filename_lower, f.full_path, f.parent_dir, 
                        f.extension, f.size, f.mtime, f.is_dir
                 FROM files_fts
                 JOIN files f ON files_fts.rowid = f.id
                 WHERE files_fts MATCH ?1
                 LIMIT ?2"
            )?;

            let rows = stmt.query_map(params![query, limit], |row| {
                Ok(FileRecord {
                    id: Some(row.get(0)?),
                    filename: row.get(1)?,
                    filename_lower: row.get(2)?,
                    full_path: row.get(3)?,
                    parent_dir: row.get(4)?,
                    extension: row.get(5)?,
                    size: row.get::<_, i64>(6)? as u64,
                    mtime: row.get(7)?,
                    is_dir: row.get::<_, i32>(8)? != 0,
                })
            })?;

            for row in rows {
                if let Ok(record) = row {
                    results.push(record);
                }
            }
        } else {
            // 使用LIKE搜索
            let pattern = format!("%{}%", keyword);
            let mut stmt = conn.prepare(
                "SELECT id, filename, filename_lower, full_path, parent_dir, 
                        extension, size, mtime, is_dir
                 FROM files
                 WHERE filename_lower LIKE ?1
                 LIMIT ?2"
            )?;

            let rows = stmt.query_map(params![pattern, limit], |row| {
                Ok(FileRecord {
                    id: Some(row.get(0)?),
                    filename: row.get(1)?,
                    filename_lower: row.get(2)?,
                    full_path: row.get(3)?,
                    parent_dir: row.get(4)?,
                    extension: row.get(5)?,
                    size: row.get::<_, i64>(6)? as u64,
                    mtime: row.get(7)?,
                    is_dir: row.get::<_, i32>(8)? != 0,
                })
            })?;

            for row in rows {
                if let Ok(record) = row {
                    results.push(record);
                }
            }
        }

        Ok(results)
    }

    /// LIKE 多关键字搜索（对齐 Python：AND 组合），避免包含空格时 `LIKE "%a b%"` 的漏结果问题。
    pub fn search_like_tokens(&self, tokens: &[String], limit: usize) -> Result<Vec<FileRecord>> {
        let conn = self.conn.lock().unwrap();
        let mut results = Vec::new();

        let tokens: Vec<String> = tokens
            .iter()
            .map(|s| s.trim().to_lowercase())
            .filter(|s| !s.is_empty())
            .collect();
        if tokens.is_empty() {
            return Ok(results);
        }

        let mut sql = String::from(
            "SELECT id, filename, filename_lower, full_path, parent_dir,
                    extension, size, mtime, is_dir
             FROM files
             WHERE 1=1",
        );
        for _ in &tokens {
            sql.push_str(" AND filename_lower LIKE ?");
        }
        sql.push_str(" LIMIT ?");

        let mut vals: Vec<Value> = Vec::with_capacity(tokens.len() + 1);
        for t in &tokens {
            vals.push(Value::Text(format!("%{}%", t)));
        }
        vals.push(Value::Integer(limit as i64));

        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(vals.iter()), |row| {
            Ok(FileRecord {
                id: Some(row.get(0)?),
                filename: row.get(1)?,
                filename_lower: row.get(2)?,
                full_path: row.get(3)?,
                parent_dir: row.get(4)?,
                extension: row.get(5)?,
                size: row.get::<_, i64>(6)? as u64,
                mtime: row.get(7)?,
                is_dir: row.get::<_, i32>(8)? != 0,
            })
        })?;
        for row in rows {
            if let Ok(record) = row {
                results.push(record);
            }
        }

        Ok(results)
    }

    /// LIKE 多关键字（按“字符”拆分）搜索：用于中文模糊扩大候选集
    /// - token "华润" => filename_lower LIKE "%华%" AND "%润%"
    pub fn search_like_char_tokens(&self, tokens: &[String], limit: usize) -> Result<Vec<FileRecord>> {
        let conn = self.conn.lock().unwrap();
        let mut results = Vec::new();

        let mut chars: Vec<String> = Vec::new();
        for t in tokens {
            let t = t.trim().to_lowercase();
            if t.is_empty() {
                continue;
            }
            for ch in t.chars() {
                // 跳过空白
                if ch.is_whitespace() {
                    continue;
                }
                chars.push(ch.to_string());
            }
        }
        if chars.is_empty() {
            return Ok(results);
        }

        let mut sql = String::from(
            "SELECT id, filename, filename_lower, full_path, parent_dir,
                    extension, size, mtime, is_dir
             FROM files
             WHERE 1=1",
        );
        for _ in &chars {
            sql.push_str(" AND filename_lower LIKE ?");
        }
        sql.push_str(" LIMIT ?");

        let mut vals: Vec<Value> = Vec::with_capacity(chars.len() + 1);
        for c in &chars {
            vals.push(Value::Text(format!("%{}%", c)));
        }
        vals.push(Value::Integer(limit as i64));

        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(vals.iter()), |row| {
            Ok(FileRecord {
                id: Some(row.get(0)?),
                filename: row.get(1)?,
                filename_lower: row.get(2)?,
                full_path: row.get(3)?,
                parent_dir: row.get(4)?,
                extension: row.get(5)?,
                size: row.get::<_, i64>(6)? as u64,
                mtime: row.get(7)?,
                is_dir: row.get::<_, i32>(8)? != 0,
            })
        })?;
        for row in rows {
            if let Ok(record) = row {
                results.push(record);
            }
        }

        Ok(results)
    }

    /// 带范围前缀过滤的搜索（性能优化：缩小扫描范围）
    pub fn search_scoped(
        &self,
        keyword: &str,
        full_path_prefix: &str,
        use_fts: bool,
        use_regex: bool,
        limit: usize,
    ) -> Result<Vec<FileRecord>> {
        let conn = self.conn.lock().unwrap();
        let mut results = Vec::new();

        // 统一前缀：Windows 盘符/目录
        let mut prefix = full_path_prefix.replace('/', "\\");
        if prefix.len() == 2 && prefix.ends_with(':') {
            prefix.push('\\');
        }
        let prefix_like = format!("{}%", prefix);

        if use_fts && !keyword.is_empty() {
            // FTS + 前缀过滤（用 JOIN 的 files.full_path 过滤）
            let query = format!("{}*", keyword);
            let mut stmt = conn.prepare(
                "SELECT f.id, f.filename, f.filename_lower, f.full_path, f.parent_dir,
                        f.extension, f.size, f.mtime, f.is_dir
                 FROM files_fts
                 JOIN files f ON files_fts.rowid = f.id
                 WHERE files_fts MATCH ?1
                   AND f.full_path LIKE ?2
                 LIMIT ?3",
            )?;

            let rows = stmt.query_map(params![query, prefix_like, limit as i64], |row| {
                Ok(FileRecord {
                    id: Some(row.get(0)?),
                    filename: row.get(1)?,
                    filename_lower: row.get(2)?,
                    full_path: row.get(3)?,
                    parent_dir: row.get(4)?,
                    extension: row.get(5)?,
                    size: row.get::<_, i64>(6)? as u64,
                    mtime: row.get(7)?,
                    is_dir: row.get::<_, i64>(8)? != 0,
                })
            })?;

            for row in rows {
                results.push(row?);
            }
        } else {
            // LIKE + 前缀过滤
            let pattern = format!("%{}%", keyword);
            let mut stmt = conn.prepare(
                "SELECT id, filename, filename_lower, full_path, parent_dir,
                        extension, size, mtime, is_dir
                 FROM files
                 WHERE filename_lower LIKE ?1
                   AND full_path LIKE ?2
                 LIMIT ?3",
            )?;

            let rows = stmt.query_map(params![pattern, prefix_like, limit as i64], |row| {
                Ok(FileRecord {
                    id: Some(row.get(0)?),
                    filename: row.get(1)?,
                    filename_lower: row.get(2)?,
                    full_path: row.get(3)?,
                    parent_dir: row.get(4)?,
                    extension: row.get(5)?,
                    size: row.get::<_, i64>(6)? as u64,
                    mtime: row.get(7)?,
                    is_dir: row.get::<_, i64>(8)? != 0,
                })
            })?;

            for row in rows {
                results.push(row?);
            }
        }

        // use_regex 当前实现仍在上层处理，这里保留参数以兼容调用签名
        let _ = use_regex;
        Ok(results)
    }

    /// scoped + LIKE 多关键字搜索（AND 组合）
    pub fn search_scoped_like_tokens(
        &self,
        tokens: &[String],
        full_path_prefix: &str,
        limit: usize,
    ) -> Result<Vec<FileRecord>> {
        let conn = self.conn.lock().unwrap();
        let mut results = Vec::new();

        let tokens: Vec<String> = tokens
            .iter()
            .map(|s| s.trim().to_lowercase())
            .filter(|s| !s.is_empty())
            .collect();
        if tokens.is_empty() {
            return Ok(results);
        }

        let mut prefix = full_path_prefix.replace('/', "\\");
        if prefix.len() == 2 && prefix.ends_with(':') {
            prefix.push('\\');
        }
        let prefix_like = format!("{}%", prefix);

        let mut sql = String::from(
            "SELECT id, filename, filename_lower, full_path, parent_dir,
                    extension, size, mtime, is_dir
             FROM files
             WHERE full_path LIKE ?",
        );
        for _ in &tokens {
            sql.push_str(" AND filename_lower LIKE ?");
        }
        sql.push_str(" LIMIT ?");

        let mut vals: Vec<Value> = Vec::with_capacity(tokens.len() + 2);
        vals.push(Value::Text(prefix_like));
        for t in &tokens {
            vals.push(Value::Text(format!("%{}%", t)));
        }
        vals.push(Value::Integer(limit as i64));

        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(vals.iter()), |row| {
            Ok(FileRecord {
                id: Some(row.get(0)?),
                filename: row.get(1)?,
                filename_lower: row.get(2)?,
                full_path: row.get(3)?,
                parent_dir: row.get(4)?,
                extension: row.get(5)?,
                size: row.get::<_, i64>(6)? as u64,
                mtime: row.get(7)?,
                is_dir: row.get::<_, i32>(8)? != 0,
            })
        })?;
        for row in rows {
            results.push(row?);
        }

        Ok(results)
    }

    /// scoped + LIKE 多关键字（按“字符”拆分）搜索：用于中文模糊扩大候选集
    pub fn search_scoped_like_char_tokens(
        &self,
        tokens: &[String],
        full_path_prefix: &str,
        limit: usize,
    ) -> Result<Vec<FileRecord>> {
        let conn = self.conn.lock().unwrap();
        let mut results = Vec::new();

        let mut chars: Vec<String> = Vec::new();
        for t in tokens {
            let t = t.trim().to_lowercase();
            if t.is_empty() {
                continue;
            }
            for ch in t.chars() {
                if ch.is_whitespace() {
                    continue;
                }
                chars.push(ch.to_string());
            }
        }
        if chars.is_empty() {
            return Ok(results);
        }

        let mut prefix = full_path_prefix.replace('/', "\\");
        if prefix.len() == 2 && prefix.ends_with(':') {
            prefix.push('\\');
        }
        let prefix_like = format!("{}%", prefix);

        let mut sql = String::from(
            "SELECT id, filename, filename_lower, full_path, parent_dir,
                    extension, size, mtime, is_dir
             FROM files
             WHERE full_path LIKE ?",
        );
        for _ in &chars {
            sql.push_str(" AND filename_lower LIKE ?");
        }
        sql.push_str(" LIMIT ?");

        let mut vals: Vec<Value> = Vec::with_capacity(chars.len() + 2);
        vals.push(Value::Text(prefix_like));
        for c in &chars {
            vals.push(Value::Text(format!("%{}%", c)));
        }
        vals.push(Value::Integer(limit as i64));

        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(vals.iter()), |row| {
            Ok(FileRecord {
                id: Some(row.get(0)?),
                filename: row.get(1)?,
                filename_lower: row.get(2)?,
                full_path: row.get(3)?,
                parent_dir: row.get(4)?,
                extension: row.get(5)?,
                size: row.get::<_, i64>(6)? as u64,
                mtime: row.get(7)?,
                is_dir: row.get::<_, i32>(8)? != 0,
            })
        })?;
        for row in rows {
            results.push(row?);
        }

        Ok(results)
    }

    // 获取统计信息
    pub fn get_stats(&self) -> Result<(usize, usize)> {
        let conn = self.conn.lock().unwrap();

        let total: i64 = conn.query_row(
            "SELECT COUNT(*) FROM files",
            [],
            |row| row.get(0)
        )?;

        let files: i64 = conn.query_row(
            "SELECT COUNT(*) FROM files WHERE is_dir = 0",
            [],
            |row| row.get(0)
        )?;

        Ok((total as usize, files as usize))
    }

    // 清空数据库
    pub fn clear(&self) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute("DELETE FROM files", [])?;
        conn.execute("DELETE FROM files_fts", [])?;
        log::info!("数据库已清空");
        Ok(())
    }

    // 删除文件记录（大小写不敏感，避免 USN 返回路径大小写与索引不一致导致删不到）
    pub fn delete_file(&self, path: &str) -> Result<usize> {
        let conn = self.conn.lock().unwrap();
        let deleted = conn.execute(
            "DELETE FROM files WHERE LOWER(full_path) = LOWER(?1)",
            params![path],
        )?;
        Ok(deleted)
    }

    // 批量删除（支持前缀匹配，用于删除目录）
    pub fn delete_by_prefix(&self, prefix: &str) -> Result<usize> {
        let conn = self.conn.lock().unwrap();
        let pattern = format!("{}%", prefix);
        let deleted = conn.execute(
            "DELETE FROM files WHERE LOWER(full_path) LIKE LOWER(?1)",
            params![pattern]
        )?;
        Ok(deleted)
    }

    // 更新文件信息
    pub fn update_file(&self, record: &FileRecord) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE files SET size = ?1, mtime = ?2 WHERE full_path = ?3",
            params![record.size as i64, record.mtime, record.full_path]
        )?;
        Ok(())
    }

    /// 补全缺失的 size/mtime（主要用于 mtime=0 导致 dm: 语法无效）。
    ///
    /// 设计目标：
    /// - 只处理 mtime=0 的记录，避免每次重建都重复扫全库
    /// - 用 `GetFileAttributesExW` 获取信息（通过 `crate::usn_engine::get_file_info_fast_native`），
    ///   不打开文件句柄，开销相对低
    /// - 用事务批量 UPDATE，避免频繁 fsync
    pub fn hydrate_missing_meta(&self, max_count: usize) -> Result<usize> {
        use rayon::prelude::*;

        // 1) 先把需要补全的路径取出来（不要持锁做并行 I/O）
        let targets: Vec<String> = {
            let conn = self.conn.lock().unwrap();
            if max_count > 0 {
                let mut stmt = conn.prepare(
                    "SELECT full_path FROM files WHERE mtime <= 0 LIMIT ?",
                )?;
                let rows = stmt.query_map(params![max_count as i64], |row| {
                    row.get::<_, String>(0)
                })?;
                let mut out = Vec::new();
                for r in rows {
                    if let Ok(p) = r {
                        out.push(p);
                    }
                }
                out
            } else {
                let mut stmt = conn.prepare(
                    "SELECT full_path FROM files WHERE mtime <= 0",
                )?;
                let rows = stmt.query_map([], |row| row.get::<_, String>(0))?;
                let mut out = Vec::new();
                for r in rows {
                    if let Ok(p) = r {
                        out.push(p);
                    }
                }
                out
            }
        };

        if targets.is_empty() {
            return Ok(0);
        }

        // 2) 并行获取属性（Windows fast path；非 Windows 退化到 std::fs::metadata）
        let metas: Vec<(String, u64, f64)> = targets
            .par_iter()
            .filter_map(|path| {
                #[cfg(windows)]
                {
                    let (size, mtime) = crate::usn_engine::get_file_info_fast_native(path)?;
                    if mtime > 0.0 {
                        Some((path.clone(), size, mtime))
                    } else {
                        None
                    }
                }
                #[cfg(not(windows))]
                {
                    use std::fs;
                    use std::time::UNIX_EPOCH;
                    let md = fs::metadata(path).ok()?;
                    let mtime = md
                        .modified()
                        .ok()
                        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                        .map(|d| d.as_secs_f64())
                        .unwrap_or(0.0);
                    if mtime > 0.0 {
                        Some((path.clone(), md.len(), mtime))
                    } else {
                        None
                    }
                }
            })
            .collect();

        if metas.is_empty() {
            return Ok(0);
        }

        // 3) 事务批量 UPDATE
        let updated = {
            let conn = self.conn.lock().unwrap();
            let tx = conn.unchecked_transaction()?;
            let mut cnt = 0usize;
            {
                let mut stmt = tx.prepare_cached(
                    "UPDATE files SET size = ?1, mtime = ?2 WHERE full_path = ?3",
                )?;
                for (path, size, mtime) in metas {
                    if stmt.execute(params![size as i64, mtime, path]).is_ok() {
                        cnt += 1;
                    }
                }
            }
            tx.commit()?;
            cnt
        };

        Ok(updated)
    }

    // 保存元数据
    pub fn set_meta(&self, key: &str, value: &str) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?1, ?2)",
            params![key, value]
        )?;
        Ok(())
    }

    // 获取元数据
    pub fn get_meta(&self, key: &str) -> Result<Option<String>> {
        let conn = self.conn.lock().unwrap();
        let result = conn.query_row(
            "SELECT value FROM meta WHERE key = ?1",
            params![key],
            |row| row.get(0)
        ).optional()?;
        Ok(result)
    }

    // 优化数据库
    pub fn optimize(&self) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute("VACUUM", [])?;
        conn.execute("ANALYZE", [])?;
        log::info!("✅ 数据库优化完成");
        Ok(())
    }

    /// 仅按过滤条件搜索（用于 “ext:pdf / dm:7d” 这类语法-only 查询）
    pub fn search_filters(
        &self,
        exts: &[String],
        min_mtime: Option<f64>,
        limit: usize,
    ) -> Result<Vec<FileRecord>> {
        let conn = self.conn.lock().unwrap();
        let mut results = Vec::new();

        let mut sql = String::from(
            "SELECT id, filename, filename_lower, full_path, parent_dir,
                    extension, size, mtime, is_dir
             FROM files
             WHERE 1=1",
        );

        let mut vals: Vec<Value> = Vec::new();

        if !exts.is_empty() {
            sql.push_str(" AND extension IN (");
            for i in 0..exts.len() {
                if i > 0 {
                    sql.push(',');
                }
                sql.push('?');
            }
            sql.push(')');
            for e in exts {
                vals.push(Value::Text(e.to_string()));
            }
        }

        if let Some(mm) = min_mtime {
            sql.push_str(" AND mtime >= ?");
            vals.push(Value::Real(mm));
        }

        sql.push_str(" ORDER BY mtime DESC LIMIT ?");
        vals.push(Value::Integer(limit as i64));

        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(vals.iter()), |row| {
            Ok(FileRecord {
                id: Some(row.get(0)?),
                filename: row.get(1)?,
                filename_lower: row.get(2)?,
                full_path: row.get(3)?,
                parent_dir: row.get(4)?,
                extension: row.get(5)?,
                size: row.get::<_, i64>(6)? as u64,
                mtime: row.get(7)?,
                is_dir: row.get::<_, i64>(8)? != 0,
            })
        })?;
        for row in rows {
            results.push(row?);
        }

        Ok(results)
    }

    /// scoped + 仅过滤条件搜索（用于 “scope + ext/dm” 语法-only）
    pub fn search_scoped_filters(
        &self,
        full_path_prefix: &str,
        exts: &[String],
        min_mtime: Option<f64>,
        limit: usize,
    ) -> Result<Vec<FileRecord>> {
        let conn = self.conn.lock().unwrap();
        let mut results = Vec::new();

        let mut prefix = full_path_prefix.replace('/', "\\");
        if prefix.len() == 2 && prefix.ends_with(':') {
            prefix.push('\\');
        }
        let prefix_like = format!("{}%", prefix);

        let mut sql = String::from(
            "SELECT id, filename, filename_lower, full_path, parent_dir,
                    extension, size, mtime, is_dir
             FROM files
             WHERE full_path LIKE ?",
        );

        let mut vals: Vec<Value> = Vec::new();
        vals.push(Value::Text(prefix_like));

        if !exts.is_empty() {
            sql.push_str(" AND extension IN (");
            for i in 0..exts.len() {
                if i > 0 {
                    sql.push(',');
                }
                sql.push('?');
            }
            sql.push(')');
            for e in exts {
                vals.push(Value::Text(e.to_string()));
            }
        }

        if let Some(mm) = min_mtime {
            sql.push_str(" AND mtime >= ?");
            vals.push(Value::Real(mm));
        }

        sql.push_str(" ORDER BY mtime DESC LIMIT ?");
        vals.push(Value::Integer(limit as i64));

        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(vals.iter()), |row| {
            Ok(FileRecord {
                id: Some(row.get(0)?),
                filename: row.get(1)?,
                filename_lower: row.get(2)?,
                full_path: row.get(3)?,
                parent_dir: row.get(4)?,
                extension: row.get(5)?,
                size: row.get::<_, i64>(6)? as u64,
                mtime: row.get(7)?,
                is_dir: row.get::<_, i64>(8)? != 0,
            })
        })?;
        for row in rows {
            results.push(row?);
        }

        Ok(results)
    }

    /// 仅按过滤条件搜索（Tier2：ext/dm/size/is）
    pub fn search_filters_v2(
        &self,
        exts: &[String],
        min_mtime: Option<f64>,
        min_size: Option<u64>,
        max_size: Option<u64>,
        is_dir: bool,
        is_file: bool,
        limit: usize,
    ) -> Result<Vec<FileRecord>> {
        let conn = self.conn.lock().unwrap();
        let mut results = Vec::new();

        let mut sql = String::from(
            "SELECT id, filename, filename_lower, full_path, parent_dir,
                    extension, size, mtime, is_dir
             FROM files
             WHERE 1=1",
        );
        let mut vals: Vec<Value> = Vec::new();

        if is_dir {
            sql.push_str(" AND is_dir = 1");
        } else if is_file {
            sql.push_str(" AND is_dir = 0");
        }

        if !exts.is_empty() {
            sql.push_str(" AND extension IN (");
            for i in 0..exts.len() {
                if i > 0 {
                    sql.push(',');
                }
                sql.push('?');
            }
            sql.push(')');
            for e in exts {
                vals.push(Value::Text(e.to_string()));
            }
        }

        if let Some(mm) = min_mtime {
            sql.push_str(" AND mtime >= ?");
            vals.push(Value::Real(mm));
        }

        if let Some(ms) = min_size {
            sql.push_str(" AND size >= ?");
            vals.push(Value::Integer(ms.min(i64::MAX as u64) as i64));
        }
        if let Some(mx) = max_size {
            sql.push_str(" AND size <= ?");
            vals.push(Value::Integer(mx.min(i64::MAX as u64) as i64));
        }

        // 排序：dm 优先看最新；否则 size 场景更直观；再否则按 id
        if min_mtime.is_some() {
            sql.push_str(" ORDER BY mtime DESC");
        } else if min_size.is_some() || max_size.is_some() {
            sql.push_str(" ORDER BY size DESC");
        } else {
            sql.push_str(" ORDER BY id DESC");
        }
        sql.push_str(" LIMIT ?");
        vals.push(Value::Integer(limit as i64));

        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(vals.iter()), |row| {
            Ok(FileRecord {
                id: Some(row.get(0)?),
                filename: row.get(1)?,
                filename_lower: row.get(2)?,
                full_path: row.get(3)?,
                parent_dir: row.get(4)?,
                extension: row.get(5)?,
                size: row.get::<_, i64>(6)? as u64,
                mtime: row.get(7)?,
                is_dir: row.get::<_, i64>(8)? != 0,
            })
        })?;
        for row in rows {
            results.push(row?);
        }
        Ok(results)
    }

    /// scoped + 仅过滤条件搜索（Tier2：ext/dm/size/is）
    pub fn search_scoped_filters_v2(
        &self,
        full_path_prefix: &str,
        exts: &[String],
        min_mtime: Option<f64>,
        min_size: Option<u64>,
        max_size: Option<u64>,
        is_dir: bool,
        is_file: bool,
        limit: usize,
    ) -> Result<Vec<FileRecord>> {
        let conn = self.conn.lock().unwrap();
        let mut results = Vec::new();

        let mut prefix = full_path_prefix.replace('/', "\\");
        if prefix.len() == 2 && prefix.ends_with(':') {
            prefix.push('\\');
        }
        let prefix_like = format!("{}%", prefix);

        let mut sql = String::from(
            "SELECT id, filename, filename_lower, full_path, parent_dir,
                    extension, size, mtime, is_dir
             FROM files
             WHERE full_path LIKE ?",
        );
        let mut vals: Vec<Value> = Vec::new();
        vals.push(Value::Text(prefix_like));

        if is_dir {
            sql.push_str(" AND is_dir = 1");
        } else if is_file {
            sql.push_str(" AND is_dir = 0");
        }

        if !exts.is_empty() {
            sql.push_str(" AND extension IN (");
            for i in 0..exts.len() {
                if i > 0 {
                    sql.push(',');
                }
                sql.push('?');
            }
            sql.push(')');
            for e in exts {
                vals.push(Value::Text(e.to_string()));
            }
        }

        if let Some(mm) = min_mtime {
            sql.push_str(" AND mtime >= ?");
            vals.push(Value::Real(mm));
        }

        if let Some(ms) = min_size {
            sql.push_str(" AND size >= ?");
            vals.push(Value::Integer(ms.min(i64::MAX as u64) as i64));
        }
        if let Some(mx) = max_size {
            sql.push_str(" AND size <= ?");
            vals.push(Value::Integer(mx.min(i64::MAX as u64) as i64));
        }

        if min_mtime.is_some() {
            sql.push_str(" ORDER BY mtime DESC");
        } else if min_size.is_some() || max_size.is_some() {
            sql.push_str(" ORDER BY size DESC");
        } else {
            sql.push_str(" ORDER BY id DESC");
        }
        sql.push_str(" LIMIT ?");
        vals.push(Value::Integer(limit as i64));

        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(vals.iter()), |row| {
            Ok(FileRecord {
                id: Some(row.get(0)?),
                filename: row.get(1)?,
                filename_lower: row.get(2)?,
                full_path: row.get(3)?,
                parent_dir: row.get(4)?,
                extension: row.get(5)?,
                size: row.get::<_, i64>(6)? as u64,
                mtime: row.get(7)?,
                is_dir: row.get::<_, i64>(8)? != 0,
            })
        })?;
        for row in rows {
            results.push(row?);
        }
        Ok(results)
    }

    /// path: 作用域（仅路径 LIKE，多 token AND）
    pub fn search_path_like_tokens(&self, tokens: &[String], limit: usize) -> Result<Vec<FileRecord>> {
        let conn = self.conn.lock().unwrap();
        let mut results = Vec::new();

        let tokens: Vec<String> = tokens
            .iter()
            .map(|s| s.trim().to_lowercase())
            .filter(|s| !s.is_empty())
            .collect();
        if tokens.is_empty() {
            return Ok(results);
        }

        let mut sql = String::from(
            "SELECT id, filename, filename_lower, full_path, parent_dir,
                    extension, size, mtime, is_dir
             FROM files
             WHERE 1=1",
        );
        for _ in &tokens {
            // 使用 LOWER(full_path) 确保大小写稳定（避免 case_sensitive_like 导致 path: 失效）
            sql.push_str(" AND LOWER(full_path) LIKE ?");
        }
        sql.push_str(" LIMIT ?");

        let mut vals: Vec<Value> = Vec::with_capacity(tokens.len() + 1);
        for t in &tokens {
            vals.push(Value::Text(format!("%{}%", t)));
        }
        vals.push(Value::Integer(limit as i64));

        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(vals.iter()), |row| {
            Ok(FileRecord {
                id: Some(row.get(0)?),
                filename: row.get(1)?,
                filename_lower: row.get(2)?,
                full_path: row.get(3)?,
                parent_dir: row.get(4)?,
                extension: row.get(5)?,
                size: row.get::<_, i64>(6)? as u64,
                mtime: row.get(7)?,
                is_dir: row.get::<_, i32>(8)? != 0,
            })
        })?;
        for row in rows {
            results.push(row?);
        }
        Ok(results)
    }

    /// scoped + path: 作用域（仅路径 LIKE，多 token AND）
    pub fn search_scoped_path_like_tokens(
        &self,
        tokens: &[String],
        full_path_prefix: &str,
        limit: usize,
    ) -> Result<Vec<FileRecord>> {
        let conn = self.conn.lock().unwrap();
        let mut results = Vec::new();

        let tokens: Vec<String> = tokens
            .iter()
            .map(|s| s.trim().to_lowercase())
            .filter(|s| !s.is_empty())
            .collect();
        if tokens.is_empty() {
            return Ok(results);
        }

        let mut prefix = full_path_prefix.replace('/', "\\");
        if prefix.len() == 2 && prefix.ends_with(':') {
            prefix.push('\\');
        }
        let prefix_like = format!("{}%", prefix);

        let mut sql = String::from(
            "SELECT id, filename, filename_lower, full_path, parent_dir,
                    extension, size, mtime, is_dir
             FROM files
             WHERE full_path LIKE ?",
        );
        for _ in &tokens {
            // 使用 LOWER(full_path) 确保大小写稳定（避免 case_sensitive_like 导致 path: 失效）
            sql.push_str(" AND LOWER(full_path) LIKE ?");
        }
        sql.push_str(" LIMIT ?");

        let mut vals: Vec<Value> = Vec::with_capacity(tokens.len() + 2);
        vals.push(Value::Text(prefix_like));
        for t in &tokens {
            vals.push(Value::Text(format!("%{}%", t)));
        }
        vals.push(Value::Integer(limit as i64));

        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(vals.iter()), |row| {
            Ok(FileRecord {
                id: Some(row.get(0)?),
                filename: row.get(1)?,
                filename_lower: row.get(2)?,
                full_path: row.get(3)?,
                parent_dir: row.get(4)?,
                extension: row.get(5)?,
                size: row.get::<_, i64>(6)? as u64,
                mtime: row.get(7)?,
                is_dir: row.get::<_, i32>(8)? != 0,
            })
        })?;
        for row in rows {
            results.push(row?);
        }
        Ok(results)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_database_creation() {
        let db = Database::new().unwrap();
        let stats = db.get_stats().unwrap();
        assert_eq!(stats.0, 0);
    }

    #[test]
    fn test_insert_and_search() {
        let db = Database::new().unwrap();
        db.clear().unwrap();

        let record = FileRecord {
            id: None,
            filename: "test.txt".to_string(),
            filename_lower: "test.txt".to_string(),
            full_path: "C:\\test.txt".to_string(),
            parent_dir: "C:\\".to_string(),
            extension: ".txt".to_string(),
            size: 1024,
            mtime: 0.0,
            is_dir: false,
        };

        db.batch_insert(&[record]).unwrap();

        let results = db.search("test", false, false, 100).unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].filename, "test.txt");
    }
}

