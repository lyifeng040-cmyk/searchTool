"""ConfigManager：从原版提取，逻辑不改。"""
from __future__ import annotations
from ..utils.constants import *

class ConfigManager:
    """配置管理器 - 处理应用程序配置的保存和加载"""

    def __init__(self):
        self.config_dir = LOG_DIR
        self.config_file = self.config_dir / "config.json"
        self.config = self._load()

    def _load(self):
        """加载配置文件"""
        try:
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"配置加载失败: {e}")
        return self._get_default_config()

    def _get_default_config(self):
        """获取默认配置"""
        return {
            "search_history": [],
            "favorites": [],
            "theme": "light",
            "c_scan_paths": {"paths": [], "initialized": False},
            "enable_global_hotkey": True,
            "minimize_to_tray": True,
        }

    def save(self):
        """保存配置到文件"""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"配置保存失败: {e}")

    def add_history(self, keyword):
        """添加搜索历史"""
        if not keyword:
            return
        history = self.config.get("search_history", [])
        if keyword in history:
            history.remove(keyword)
        history.insert(0, keyword)
        self.config["search_history"] = history[:20]
        self.save()

    def get_history(self):
        """获取搜索历史"""
        return self.config.get("search_history", [])

    def add_favorite(self, path, name=None):
        """添加收藏"""
        if not path:
            return
        favs = self.config.get("favorites", [])
        for f in favs:
            if f.get("path", "").lower() == path.lower():
                return
        name = name or os.path.basename(path) or path
        favs.append({"name": name, "path": path})
        self.config["favorites"] = favs
        self.save()

    def remove_favorite(self, path):
        """移除收藏"""
        favs = self.config.get("favorites", [])
        self.config["favorites"] = [
            f for f in favs if f.get("path", "").lower() != path.lower()
        ]
        self.save()

    def get_favorites(self):
        """获取收藏列表"""
        return self.config.get("favorites", [])

    def set_theme(self, theme):
        """设置主题"""
        self.config["theme"] = theme
        self.save()

    def get_theme(self):
        """获取主题"""
        return self.config.get("theme", "light")

    def get_c_scan_paths(self):
        """获取C盘扫描路径列表"""
        config = self.config.get("c_scan_paths", {})
        if not config.get("initialized", False):
            return self._get_default_c_paths()
        return config.get("paths", [])

    def _get_default_c_paths(self):
        """获取默认的C盘路径配置"""
        default_dirs = [
            os.path.expandvars(r"%TEMP%"),
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Recent"),
            os.path.expandvars(r"%USERPROFILE%\Desktop"),
            os.path.expandvars(r"%USERPROFILE%\Documents"),
            os.path.expandvars(r"%USERPROFILE%\Downloads"),
        ]
        paths = []
        for p in default_dirs:
            if p and os.path.isdir(p):
                p = os.path.normpath(p)
                paths.append({"path": p, "enabled": True})
        return paths

    def set_c_scan_paths(self, paths):
        """设置C盘扫描路径列表"""
        self.config["c_scan_paths"] = {"paths": paths, "initialized": True}
        self.save()

    def reset_c_scan_paths(self):
        """重置为默认C盘路径"""
        default_paths = self._get_default_c_paths()
        self.set_c_scan_paths(default_paths)
        return default_paths

    def get_enabled_c_paths(self):
        """获取启用的C盘路径列表"""
        paths = self.get_c_scan_paths()
        return [
            p["path"]
            for p in paths
            if p.get("enabled", True) and os.path.isdir(p["path"])
        ]

    def get_hotkey_enabled(self):
        """获取热键启用状态"""
        return self.config.get("enable_global_hotkey", True)

    def set_hotkey_enabled(self, enabled):
        """设置热键启用状态"""
        self.config["enable_global_hotkey"] = enabled
        self.save()

    def get_tray_enabled(self):
        """获取托盘启用状态"""
        return self.config.get("minimize_to_tray", True)

    def set_tray_enabled(self, enabled):
        """设置托盘启用状态"""
        self.config["minimize_to_tray"] = enabled
        self.save()
