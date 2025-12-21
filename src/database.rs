//! SQLite 数据库操作

use rusqlite::{Connection, params};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

/// 文件条目
#[derive(Debug, Clone)]
pub struct FileEntry {
    pub name: String,
    pub name_lower: String,
    pub full_path: String,
    pub parent_dir: String,
    pub extension: String,
    pub size: u64,
    pub mtime: f64,
    pub is_dir: bool,
}

/// 数据库管理器
pub struct Database {
    conn: Connection,
}

impl Database {
    /// 创建或打开数据库
    pub fn new<P: AsRef<Path>>(path: P) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let conn = Connection::open(path)?;
        
        // 极限优化配置
        conn.execute_batch("
            PRAGMA synchronous = OFF;
            PRAGMA journal_mode = MEMORY;
            PRAGMA locking_mode = EXCLUSIVE;
            PRAGMA temp_store = MEMORY;
            PRAGMA cache_size = -500000;
            PRAGMA mmap_size = 268435456;
            PRAGMA page_size = 4096;
        ")?;
        
        // 创建表
        conn.execute_batch("
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                filename_lower TEXT NOT NULL,
                full_path TEXT UNIQUE NOT NULL,
                parent_dir TEXT NOT NULL,
                extension TEXT,
                size INTEGER DEFAULT 0,
                mtime REAL DEFAULT 0,
                is_dir INTEGER DEFAULT 0
            );
            
            CREATE INDEX IF NOT EXISTS idx_filename_lower ON files(filename_lower);
            CREATE INDEX IF NOT EXISTS idx_parent_dir ON files(parent_dir);
            
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        ")?;
        
        Ok(Self { conn })
    }
    
    /// 清空所有文件记录
    pub fn clear_all(&self) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        self.conn.execute("DELETE FROM files", [])?;
        Ok(())
    }
    
    /// 删除指定驱动器的记录
    pub fn delete_drive(&self, drive: char) -> Result<u64, Box<dyn std::error::Error + Send + Sync>> {
        let pattern = format!("{}:%", drive.to_ascii_uppercase());
        let count = self.conn.execute(
            "DELETE FROM files WHERE full_path LIKE ?1 || '%'",
            [&pattern],
        )?;
        Ok(count as u64)
    }
    
    /// 批量插入文件记录
    pub fn insert_batch(&mut self, entries: &[FileEntry]) -> Result<u64, Box<dyn std::error::Error + Send + Sync>> {
        let tx = self.conn.transaction()?;
        
        {
            let mut stmt = tx.prepare_cached(
                "INSERT OR IGNORE INTO files (filename, filename_lower, full_path, parent_dir, extension, size, mtime, is_dir)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)"
            )?;
            
            for entry in entries {
                stmt.execute(params![
                    &entry.name,
                    &entry.name_lower,
                    &entry.full_path,
                    &entry.parent_dir,
                    &entry.extension,
                    entry.size as i64,
                    entry.mtime,
                    if entry.is_dir { 1 } else { 0 },
                ])?;
            }
        }
        
        // 更新元数据
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs_f64();
        
        tx.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('build_time', ?1)",
            [now.to_string()],
        )?;
        
        tx.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('used_mft', '1')",
            [],
        )?;
        
        tx.commit()?;
        
        Ok(entries.len() as u64)
    }
    
    /// 构建 FTS5 全文索引
    pub fn build_fts(&self) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        // 先删除旧的 FTS 表和触发器
        self.conn.execute_batch("
            DROP TRIGGER IF EXISTS files_ai;
            DROP TRIGGER IF EXISTS files_ad;
            DROP TABLE IF EXISTS files_fts;
        ")?;
        
        // 创建新的 FTS 表
        self.conn.execute_batch("
            CREATE VIRTUAL TABLE files_fts USING fts5(
                filename,
                content = files,
                content_rowid = id
            );
            
            INSERT INTO files_fts(files_fts) VALUES('rebuild');
            
            CREATE TRIGGER files_ai AFTER INSERT ON files BEGIN
                INSERT INTO files_fts(rowid, filename) VALUES (new.id, new.filename);
            END;
            
            CREATE TRIGGER files_ad AFTER DELETE ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, filename) VALUES('delete', old.id, old.filename);
            END;
        ")?;
        
        Ok(())
    }
    
    /// 恢复正常的数据库模式
    pub fn restore_normal_mode(&self) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        self.conn.execute_batch("
            PRAGMA synchronous = NORMAL;
            PRAGMA journal_mode = WAL;
            PRAGMA locking_mode = NORMAL;
        ")?;
        Ok(())
    }
    
    /// 获取文件计数
    #[allow(dead_code)]
    pub fn get_file_count(&self) -> Result<u64, Box<dyn std::error::Error + Send + Sync>> {
        let count: i64 = self.conn.query_row(
            "SELECT COUNT(*) FROM files",
            [],
            |row| row.get(0),
        )?;
        Ok(count as u64)
    }
}