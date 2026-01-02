// 配置管理模块
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use anyhow::Result;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    pub search_history: Vec<String>,
    pub favorites: Vec<Favorite>,
    pub theme: String,
    pub c_scan_paths: CScanPaths,
    pub enable_global_hotkey: bool,
    pub minimize_to_tray: bool,
    pub auto_start_watcher: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Favorite {
    pub name: String,
    pub path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CScanPaths {
    pub paths: Vec<PathConfig>,
    pub initialized: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PathConfig {
    pub path: String,
    pub enabled: bool,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            search_history: Vec::new(),
            favorites: Vec::new(),
            theme: "light".to_string(),
            c_scan_paths: CScanPaths {
                paths: Self::default_c_paths(),
                initialized: false,
            },
            enable_global_hotkey: true,
            minimize_to_tray: true,
            auto_start_watcher: true,
        }
    }
}

impl Config {
    pub fn load() -> Result<Self> {
        let config_path = Self::config_file_path()?;
        
        if config_path.exists() {
            let content = std::fs::read_to_string(&config_path)?;
            // 尝试加载，如果失败则使用默认配置
            match serde_json::from_str(&content) {
                Ok(config) => Ok(config),
                Err(e) => {
                    log::warn!("配置文件解析失败，使用默认配置: {}", e);
                    // 备份旧配置
                    let backup_path = config_path.with_extension("json.bak");
                    let _ = std::fs::copy(&config_path, backup_path);
                    Ok(Self::default())
                }
            }
        } else {
            Ok(Self::default())
        }
    }

    pub fn save(&self) -> Result<()> {
        let config_path = Self::config_file_path()?;
        
        if let Some(parent) = config_path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let content = serde_json::to_string_pretty(self)?;
        std::fs::write(&config_path, content)?;
        Ok(())
    }

    fn config_file_path() -> Result<PathBuf> {
        let home = dirs::home_dir().ok_or_else(|| anyhow::anyhow!("无法获取用户目录"))?;
        Ok(home.join(".filesearch").join("config.json"))
    }

    fn default_c_paths() -> Vec<PathConfig> {
        let paths = vec![
            std::env::var("TEMP").unwrap_or_default(),
            format!("{}\\AppData\\Roaming\\Microsoft\\Windows\\Recent", 
                std::env::var("USERPROFILE").unwrap_or_default()),
            format!("{}\\Desktop", std::env::var("USERPROFILE").unwrap_or_default()),
            format!("{}\\Documents", std::env::var("USERPROFILE").unwrap_or_default()),
            format!("{}\\Downloads", std::env::var("USERPROFILE").unwrap_or_default()),
        ];

        paths
            .into_iter()
            .filter(|p| !p.is_empty() && std::path::Path::new(p).exists())
            .map(|p| PathConfig {
                path: p,
                enabled: true,
            })
            .collect()
    }

    pub fn add_history(&mut self, keyword: String) {
        if keyword.is_empty() {
            return;
        }

        self.search_history.retain(|k| k != &keyword);
        self.search_history.insert(0, keyword);
        self.search_history.truncate(20);
    }

    pub fn add_favorite(&mut self, name: String, path: String) {
        if path.is_empty() {
            return;
        }

        // 检查是否已存在
        if self.favorites.iter().any(|f| f.path.eq_ignore_ascii_case(&path)) {
            return;
        }

        let name = if name.is_empty() {
            std::path::Path::new(&path)
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or(&path)
                .to_string()
        } else {
            name
        };

        self.favorites.push(Favorite { name, path });
    }

    pub fn remove_favorite(&mut self, path: &str) {
        self.favorites.retain(|f| !f.path.eq_ignore_ascii_case(path));
    }
}

