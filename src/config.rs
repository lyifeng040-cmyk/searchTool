// 配置管理模块
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct Config {
    pub window_position: (i32, i32),
    pub window_size: (u32, u32),
    pub page_size: u32,
    pub search_mode: String,
    pub excluded_dirs: Vec<String>,
    pub indexed_drives: Vec<String>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            window_position: (100, 100),
            window_size: (720, 70),
            page_size: 200,
            search_mode: "index".to_string(),
            excluded_dirs: vec![
                "System Volume Information".to_string(),
                "$Recycle.Bin".to_string(),
                ".git".to_string(),
                "node_modules".to_string(),
            ],
            indexed_drives: vec!["C:".to_string()],
        }
    }
}

pub struct ConfigManager {
    config_path: PathBuf,
    config: Config,
}

impl ConfigManager {
    pub fn new() -> anyhow::Result<Self> {
        let config_dir = dirs::config_dir()
            .ok_or_else(|| anyhow::anyhow!("Cannot find config directory"))?
            .join("filesearch");

        std::fs::create_dir_all(&config_dir)?;

        let config_path = config_dir.join("config.json");
        let config = if config_path.exists() {
            let content = std::fs::read_to_string(&config_path)?;
            serde_json::from_str(&content).unwrap_or_default()
        } else {
            Config::default()
        };

        Ok(Self {
            config_path,
            config,
        })
    }

    pub fn get(&self) -> &Config {
        &self.config
    }

    pub fn set(&mut self, config: Config) -> anyhow::Result<()> {
        self.config = config;
        let content = serde_json::to_string_pretty(&self.config)?;
        std::fs::write(&self.config_path, content)?;
        Ok(())
    }
}
