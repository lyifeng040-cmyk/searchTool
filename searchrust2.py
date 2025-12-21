import os
os.environ['QT_LOGGING_RULES'] = '*.debug=false;*.warning=false'
import string
import platform
import threading
import time
import datetime
import tkinter as tk
from tkinter import messagebox, filedialog
import struct
import subprocess
import queue
import concurrent.futures
from collections import deque
import re
from pathlib import Path
import shutil
import math
import json
import logging
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import apsw
import ctypes

# ==================== 日志配置 ====================
LOG_DIR = Path.home() / ".filesearch"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "app.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== Rust 核心引擎加载 ====================
HAS_RUST_ENGINE = False
RUST_ENGINE = None

if platform.system() == "Windows":
    try:
        import ctypes
        
        class ScanResult(ctypes.Structure):
            _fields_ = [
                ("data", ctypes.POINTER(ctypes.c_uint8)),
                ("data_len", ctypes.c_size_t),
                ("count", ctypes.c_size_t),
            ]
        
        possible_paths = [
            Path(__file__).parent / "file_scanner_engine.dll",
            Path.cwd() / "file_scanner_engine.dll",
        ]
        
        dll_path = None
        for p in possible_paths:
            if p.exists():
                dll_path = p
                break
        
        if dll_path:
            if hasattr(os, 'add_dll_directory'):
                os.add_dll_directory(str(dll_path.parent.resolve()))
            
            RUST_ENGINE = ctypes.CDLL(str(dll_path))
            
            # 新接口
            RUST_ENGINE.scan_drive_packed.argtypes = [ctypes.c_uint16]
            RUST_ENGINE.scan_drive_packed.restype = ScanResult
            
            RUST_ENGINE.free_scan_result.argtypes = [ScanResult]
            RUST_ENGINE.free_scan_result.restype = None
            
            HAS_RUST_ENGINE = True
            logger.info(f"✅ Rust 核心引擎加载成功: {dll_path}")
        else:
            logger.warning("⚠️ 未找到 file_scanner_engine.dll")
            
    except Exception as e:
        logger.warning(f"⚠️ Rust 引擎加载失败: {e}")
        HAS_RUST_ENGINE = False


# ==================== 依赖检查 ====================
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    logger.warning("watchdog 未安装，文件监控功能不可用")

try:
    import win32clipboard
    import win32con
    import win32api
    import win32gui
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
    logger.warning("pywin32 未安装，部分功能不可用")

try:
    import send2trash
    HAS_SEND2TRASH = True
except ImportError:
    HAS_SEND2TRASH = False
    logger.warning("send2trash 未安装，删除将直接删除而非进入回收站")

# ==================== 托盘依赖检查 ====================
try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False
    logger.warning("pystray 或 PIL 未安装，托盘功能不可用")

# ==================== 有效主题列表 ====================
VALID_THEMES = ['flatly', 'darkly', 'solar', 'superhero', 'cyborg', 'vapor',
                'cosmo', 'litera', 'lumen', 'minty', 'pulse', 'sandstone',
                'united', 'yeti', 'morph', 'journal', 'simplex']


# ==================== 默认C盘扫描目录 ====================
def get_c_scan_dirs(config_mgr=None):
    """获取C盘扫描目录列表"""
    if config_mgr:
        return config_mgr.get_enabled_c_paths()
    
    # 无配置管理器时返回默认目录
    default_dirs = [
        os.path.expandvars(r"%TEMP%"),
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Recent"),
        os.path.expandvars(r"%USERPROFILE%\Desktop"),
        os.path.expandvars(r"%USERPROFILE%\Documents"),
        os.path.expandvars(r"%USERPROFILE%\Downloads"),
    ]
    dirs = []
    for p in default_dirs:
        if p and os.path.isdir(p):
            p = os.path.normpath(p)
            if p not in dirs:
                dirs.append(p)
    return dirs

def is_in_allowed_paths(path_lower, allowed_paths_lower):
    """检查路径是否在允许路径列表内"""
    if not allowed_paths_lower:
        return False
    for ap in allowed_paths_lower:
        if path_lower.startswith(ap + '\\') or path_lower == ap:
            return True
    return False

# ==================== 过滤规则 ====================
CAD_PATTERN = re.compile(r'cad20(1[0-9]|2[0-4])', re.IGNORECASE)
AUTOCAD_PATTERN = re.compile(r'autocad_20(1[0-9]|2[0-5])', re.IGNORECASE)

SKIP_DIRS_LOWER = {
    'windows', 'program files', 'program files (x86)', 'programdata',
    '$recycle.bin', 'system volume information', 'appdata',
    'boot', 'node_modules', '.git', '__pycache__', 'site-packages', 'sys',
    'recovery', 'config.msi', '$windows.~bt', '$windows.~ws',
    'cache', 'caches', 'temp', 'tmp', 'logs', 'log',
    '.vscode', '.idea', '.vs', 'obj', 'bin', 'debug', 'release',
    'packages', '.nuget', 'bower_components',
}

SKIP_EXTS = {
    '.lsp', '.fas', '.lnk', '.html', '.htm',
    '.xml', '.ini', '.lsp_bak', '.cuix', '.arx', '.crx',
    '.fx', '.dbx', '.kid', '.ico', '.rz', '.dll',
    '.sys', '.tmp', '.log', '.dat', '.db', '.pdb',
    '.obj', '.pyc', '.class', '.cache', '.lock',
}

ARCHIVE_EXTS = {'.zip', '.rar', '.7z', '.tar', '.gz', '.iso', '.jar', '.cab', '.bz2', '.xz'}


def is_in_allowed_paths(path_lower, allowed_paths_lower):
    """检查路径是否在允许的路径列表中"""
    if not allowed_paths_lower:
        return False
    for allowed in allowed_paths_lower:
        # 确保是路径前缀匹配
        if path_lower == allowed or path_lower.startswith(allowed + '\\'):
            return True
    return False


def should_skip_path(path_lower, allowed_paths_lower=None):
    """检查路径是否应该跳过"""
    # 如果在允许路径中，不跳过
    if allowed_paths_lower and is_in_allowed_paths(path_lower, allowed_paths_lower):
        return False
    
    # 检查路径中是否包含应跳过的目录名
    path_parts = path_lower.replace('/', '\\').split('\\')
    for part in path_parts:
        if part in SKIP_DIRS_LOWER:
            return True
    
    # 检查 site-packages
    if 'site-packages' in path_lower:
        return True
    
    # 检查 CAD 相关
    if CAD_PATTERN.search(path_lower):
        return True
    if AUTOCAD_PATTERN.search(path_lower):
        return True
    
    # 检查 tangent
    if 'tangent' in path_lower:
        return True
    
    return False


def should_skip_dir(name_lower, path_lower=None, allowed_paths_lower=None):
    """检查目录是否应该跳过"""
    # 检查 CAD 相关（优先级最高）
    if CAD_PATTERN.search(name_lower):
        return True
    if AUTOCAD_PATTERN.search(name_lower):
        return True
    if 'tangent' in name_lower:
        return True
    
    # 如果在允许路径中，不跳过
    if path_lower and allowed_paths_lower:
        if is_in_allowed_paths(path_lower, allowed_paths_lower):
            return False
    
    # 检查目录名是否在跳过列表中
    if name_lower in SKIP_DIRS_LOWER:
        return True
    
    return False

def format_size(size):
    if size <= 0:
        return "-"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == 'B' else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

def format_time(timestamp):
    if timestamp <= 0:
        return "-"
    try:
        return datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')
    except (OSError, ValueError) as e:
        logger.warning(f"时间戳格式化失败: {timestamp}, {e}")
        return "-"

def parse_search_scope(scope_str, get_drives_fn, config_mgr=None):
    """统一解析搜索范围"""
    targets = []
    if "所有磁盘" in scope_str:
        for d in get_drives_fn():
            if d.upper().startswith('C:'):
                targets.extend(get_c_scan_dirs(config_mgr))
            else:
                # 规范化其他盘符根路径
                norm = os.path.normpath(d).rstrip("\\/ ")
                targets.append(norm)
    else:
        # 对单一路径做更强的规范化
        s = scope_str.strip()
        # 去掉“（全盘）”等描述（如果你范围下拉里有类似文字）
        # 例如： "G:\\"、"G:\"、"G:" 都统一成 "G:\"
        if os.path.isdir(s):
            norm = os.path.normpath(s).rstrip("\\/ ")
            targets.append(norm)
        else:
            targets.append(s)
    return targets

# ==================== 模糊匹配 ====================
def fuzzy_match(keyword, filename):
    """模糊匹配 - 返回匹配分数"""
    keyword = keyword.lower()
    filename_lower = filename.lower()
    
    if keyword in filename_lower:
        return 100
    
    ki = 0
    for char in filename_lower:
        if ki < len(keyword) and char == keyword[ki]:
            ki += 1
    if ki == len(keyword):
        return 60 + ki * 5
    
    words = re.split(r'[\s\-_.]', filename_lower)
    initials = ''.join(w[0] for w in words if w)
    if keyword in initials:
        return 50
    
    return 0
# ==================== 配置管理 ====================
class ConfigManager:
    def __init__(self):
        self.config_dir = LOG_DIR
        self.config_file = self.config_dir / "config.json"
        self.config = self._load()
    
    def _load(self):
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"配置加载失败: {e}")
        return {
            "search_history": [], 
            "favorites": [], 
            "theme": "flatly",
            "c_scan_paths": {
                "custom": [],
                "use_default": True,
                "disabled_defaults": []
            },
            "enable_global_hotkey": True,
            "minimize_to_tray": True
        }
    
    def save(self):
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"配置保存失败: {e}")
    
    def add_history(self, keyword):
        if not keyword:
            return
        history = self.config.get("search_history", [])
        if keyword in history:
            history.remove(keyword)
        history.insert(0, keyword)
        self.config["search_history"] = history[:20]
        self.save()
    
    def get_history(self):
        return self.config.get("search_history", [])
    
    def add_favorite(self, path, name=None):
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
        favs = self.config.get("favorites", [])
        self.config["favorites"] = [f for f in favs if f.get("path", "").lower() != path.lower()]
        self.save()
    
    def get_favorites(self):
        return self.config.get("favorites", [])
    
    def set_theme(self, theme):
        if theme in VALID_THEMES:
            self.config["theme"] = theme
            self.save()
    
    def get_theme(self):
        theme = self.config.get("theme", "flatly")
        if theme not in VALID_THEMES:
            theme = "flatly"
            self.set_theme(theme)
        return theme
        # ==================== C盘路径配置（新版） ====================
    def get_c_scan_paths(self):
        """获取C盘扫描路径列表"""
        config = self.config.get("c_scan_paths", {})
        
        # 如果未初始化，返回默认路径
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
        self.config["c_scan_paths"] = {
            "paths": paths,
            "initialized": True
        }
        self.save()
    
    def reset_c_scan_paths(self):
        """重置为默认C盘路径"""
        default_paths = self._get_default_c_paths()
        self.set_c_scan_paths(default_paths)
        return default_paths
    
    def get_enabled_c_paths(self):
        """获取启用的C盘路径列表（供扫描使用）"""
        paths = self.get_c_scan_paths()
        return [p["path"] for p in paths if p.get("enabled", True) and os.path.isdir(p["path"])]
    
    # ==================== 全局热键配置 ====================
    def get_hotkey_enabled(self):
        """获取全局热键启用状态"""
        return self.config.get("enable_global_hotkey", True)
    
    def set_hotkey_enabled(self, enabled):
        """设置全局热键启用状态"""
        self.config["enable_global_hotkey"] = enabled
        self.save()
    
    # ==================== 托盘配置 ====================
    def get_tray_enabled(self):
        """获取托盘功能启用状态"""
        return self.config.get("minimize_to_tray", True)
    
    def set_tray_enabled(self, enabled):
        """设置托盘功能启用状态"""
        self.config["minimize_to_tray"] = enabled
        self.save()
    
# ==================== Rust 核心引擎加载 ====================
HAS_RUST_ENGINE = False
RUST_ENGINE = None

if platform.system() == "Windows":
    try:
        import ctypes
        
        class ScanResult(ctypes.Structure):
            _fields_ = [
                ("data", ctypes.POINTER(ctypes.c_uint8)),
                ("data_len", ctypes.c_size_t),
                ("count", ctypes.c_size_t),
            ]
        
        possible_paths = [
            Path(__file__).parent / "file_scanner_engine.dll",
            Path.cwd() / "file_scanner_engine.dll",
        ]
        
        dll_path = None
        for p in possible_paths:
            if p.exists():
                dll_path = p
                break
        
        if dll_path:
            if hasattr(os, 'add_dll_directory'):
                os.add_dll_directory(str(dll_path.parent.resolve()))
            
            RUST_ENGINE = ctypes.CDLL(str(dll_path))
            
            # 新接口
            RUST_ENGINE.scan_drive_packed.argtypes = [ctypes.c_uint16]
            RUST_ENGINE.scan_drive_packed.restype = ScanResult
            
            RUST_ENGINE.free_scan_result.argtypes = [ScanResult]
            RUST_ENGINE.free_scan_result.restype = None
            
            HAS_RUST_ENGINE = True
            logger.info(f"✅ Rust 核心引擎加载成功: {dll_path}")
            
    except Exception as e:
        logger.warning(f"⚠️ Rust 引擎加载失败: {e}")
        HAS_RUST_ENGINE = False
            
    except Exception as e:
        logger.warning(f"⚠️ 未找到或加载 Rust 核心引擎失败: {e}。将使用较慢的 Python 实现。")
        HAS_RUST_ENGINE = False


# ==================== MFT/USN 模块 ====================
IS_WINDOWS = platform.system() == "Windows"
MFT_AVAILABLE = False

if IS_WINDOWS:
    import ctypes
    import ctypes.wintypes as wintypes

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FSCTL_ENUM_USN_DATA = 0x000900B3
    FSCTL_QUERY_USN_JOURNAL = 0x000900F4
    FILE_ATTRIBUTE_DIRECTORY = 0x10

    class USN_JOURNAL_DATA_V0(ctypes.Structure):
        _fields_ = [
            ("UsnJournalID", ctypes.c_uint64), ("FirstUsn", ctypes.c_int64),
            ("NextUsn", ctypes.c_int64), ("LowestValidUsn", ctypes.c_int64),
            ("MaxUsn", ctypes.c_int64), ("MaximumSize", ctypes.c_uint64),
            ("AllocationDelta", ctypes.c_uint64),
        ]

    class USN_RECORD_V2(ctypes.Structure):
        _fields_ = [
            ("RecordLength", ctypes.c_uint32), ("MajorVersion", ctypes.c_uint16),
            ("MinorVersion", ctypes.c_uint16), ("FileReferenceNumber", ctypes.c_uint64),
            ("ParentFileReferenceNumber", ctypes.c_uint64), ("Usn", ctypes.c_int64),
            ("TimeStamp", ctypes.c_int64), ("Reason", ctypes.c_uint32),
            ("SourceInfo", ctypes.c_uint32), ("SecurityId", ctypes.c_uint32),
            ("FileAttributes", ctypes.c_uint32), ("FileNameLength", ctypes.c_uint16),
            ("FileNameOffset", ctypes.c_uint16),
        ]

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    CreateFileW = kernel32.CreateFileW
    CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    CreateFileW.restype = wintypes.HANDLE
    DeviceIoControl = kernel32.DeviceIoControl
    DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    DeviceIoControl.restype = wintypes.BOOL
    CloseHandle = kernel32.CloseHandle
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def enum_volume_files_mft(drive_letter, skip_dirs, skip_exts, allowed_paths=None):
        
        """MFT枚举文件 - 使用优化的 Rust 引擎"""
       
        if HAS_RUST_ENGINE:
            logger.info(f"🚀 使用 Rust 核心引擎扫描驱动器 {drive_letter}: ...")
        
            result = None
            try:
                result = RUST_ENGINE.scan_drive_packed(ord(drive_letter.upper()[0]))
                
                if not result.data or result.count == 0:
                    raise Exception("空数据")

                # 一次性读取所有数据
                raw_data = ctypes.string_at(result.data, result.data_len)
                py_list = []
                off = 0
                n = len(raw_data)

                # C盘路径过滤准备
                allowed_paths_lower = None
                if allowed_paths:
                    allowed_paths_lower = [p.lower().rstrip('\\') for p in allowed_paths] 


                skipped_count = 0  # 调试：记录跳过数量
         
                while off < n:
                    is_dir = raw_data[off]
                    name_len = int.from_bytes(raw_data[off+1:off+3], 'little')
                    name_lower_len = int.from_bytes(raw_data[off+3:off+5], 'little')
                    path_len = int.from_bytes(raw_data[off+5:off+7], 'little')
                    parent_len = int.from_bytes(raw_data[off+7:off+9], 'little')
                    ext_len = raw_data[off+9]
                    off += 10
                    # 边界检查
                    total_len = name_len + name_lower_len + path_len + parent_len + ext_len
                    if off + total_len > n:
                        break

                    name = raw_data[off:off+name_len].decode('utf-8', 'replace')
                    off += name_len
                    name_lower = raw_data[off:off+name_lower_len].decode('utf-8', 'replace') 
                    off += name_lower_len
                    path = raw_data[off:off+path_len].decode('utf-8', 'replace')
                    off += path_len
                    parent = raw_data[off:off+parent_len].decode('utf-8', 'replace')
                    off += parent_len
                    ext = raw_data[off:off+ext_len].decode('utf-8', 'replace') if ext_len else ''
                    off += ext_len
                
                    path_lower = path.lower()


                    # C盘：白名单模式
                    if allowed_paths_lower:
                       in_allowed = False
                       for ap in allowed_paths_lower:
                           if path_lower.startswith(ap + '\\') or path_lower == ap:
                               in_allowed = True
                               break
                       if not in_allowed:
                           skipped_count += 1
                           continue  # 跳过不在允许路径中的文件
                       
                    else:
                        # 其他盘：黑名单模式（和 MFT Python 版本一样）
                        if should_skip_path(path_lower, None):
                            skipped_count += 1
                            continue
                        if is_dir:
                           if should_skip_dir(name_lower, path_lower, None):
                               skipped_count += 1
                               continue
                        else:
                            if ext in skip_exts:
                                skipped_count += 1
                                continue
                  
                    # 先用 0 占位，后面批量获取
                    py_list.append([name, name_lower, path, parent, ext, 0, 0, is_dir])

                if allowed_paths_lower:
                    logger.info(f"C盘过滤: Rust返回={result.count}, 跳过={skipped_count}, 保留={len(py_list)}")
                else:
                    logger.info(f"过滤: Rust返回={result.count}, 跳过={skipped_count}, 保留={len(py_list)}")
            
                logger.info(f"Rust 返回 {len(py_list)} 条")
            
                # ========== 新增：批量获取文件大小和时间 ==========
                files_to_stat = [item for item in py_list if item[7] == 0]  # 只处理文件，不处理目录
                if files_to_stat:
                    logger.info(f"获取 {len(files_to_stat)} 个文件的大小...")
                
                    GetFileAttributesExW = kernel32.GetFileAttributesExW
                    GetFileAttributesExW.restype = wintypes.BOOL
                    GetFileAttributesExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_void_p]
                
                    class WIN32_FILE_ATTRIBUTE_DATA(ctypes.Structure):
                        _fields_ = [
                            ("dwFileAttributes", wintypes.DWORD),
                            ("ftCreationTime", wintypes.FILETIME),
                            ("ftLastAccessTime", wintypes.FILETIME),
                            ("ftLastWriteTime", wintypes.FILETIME),
                            ("nFileSizeHigh", wintypes.DWORD),
                            ("nFileSizeLow", wintypes.DWORD),
                        ]
                
                    def filetime_to_unix(ft):
                        return (ft - 116444736000000000) / 10000000

                    def stat_batch(items):
                        for item in items:
                            try:
                                data = WIN32_FILE_ATTRIBUTE_DATA()
                                if GetFileAttributesExW(item[2], 0, ctypes.byref(data)):
                                    item[5] = (data.nFileSizeHigh << 32) + data.nFileSizeLow
                                    mtime_ft = (data.ftLastWriteTime.dwHighDateTime << 32) + data.ftLastWriteTime.dwLowDateTime
                                    item[6] = filetime_to_unix(mtime_ft)
                            except:
                                pass
                
                    # 多线程获取
                    batch_size = max(1, len(files_to_stat) // 16)
                    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
                        futures = []
                        for i in range(0, len(files_to_stat), batch_size):
                            futures.append(executor.submit(stat_batch, files_to_stat[i:i+batch_size]))
                        concurrent.futures.wait(futures)
                
                    logger.info("文件大小获取完成")
            
                # 转换为元组
                return [tuple(item) for item in py_list]
            
            except Exception as e:
                logger.error(f"Rust 引擎错误: {e}，回退到 Python")

            finally:
                if result and result.data:
                    try:
                        RUST_ENGINE.free_scan_result(result)
                    except:
                        pass 


        # ========== Python MFT 实现（回退方案）==========
        logger.info(f"使用 Python MFT 实现扫描驱动器 {drive_letter}...")
        global MFT_AVAILABLE
        drive = drive_letter.rstrip(':').upper()
        root_path = f"{drive}:\\"
        
        volume_path = f"\\\\.\\{drive}:"
        h = CreateFileW(volume_path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
        if h == INVALID_HANDLE_VALUE:
            error_code = ctypes.get_last_error()
            logger.error(f"打开卷失败 {drive}: 错误代码 {error_code}")
            raise OSError(f"打开卷失败: {error_code}")
        
        try:
            jd = USN_JOURNAL_DATA_V0()
            br = wintypes.DWORD()
            if not DeviceIoControl(h, FSCTL_QUERY_USN_JOURNAL, None, 0, ctypes.byref(jd), ctypes.sizeof(jd), ctypes.byref(br), None):
                error_code = ctypes.get_last_error()
                logger.error(f"查询USN失败 {drive}: 错误代码 {error_code}")
                raise OSError(f"查询USN失败: {error_code}")
            
            MFT_AVAILABLE = True
            records = {}
            BUFFER_SIZE = 1024 * 1024 
            buf = (ctypes.c_ubyte * BUFFER_SIZE)()
            
            class MFT_ENUM_DATA(ctypes.Structure):
                _pack_ = 8
                _fields_ = [("StartFileReferenceNumber", ctypes.c_uint64), ("LowUsn", ctypes.c_int64), ("HighUsn", ctypes.c_int64)]
            
            med = MFT_ENUM_DATA()
            med.StartFileReferenceNumber = 0
            med.LowUsn = 0
            med.HighUsn = jd.NextUsn
            
            if allowed_paths:
                allowed_paths_lower = [p.lower().rstrip('\\') for p in allowed_paths]
            else:
                allowed_paths_lower = None
            
            total = 0
            start_time = time.time()
            
            while True:
                ctypes.set_last_error(0)
                ok = DeviceIoControl(h, FSCTL_ENUM_USN_DATA, ctypes.byref(med), ctypes.sizeof(med), ctypes.byref(buf), BUFFER_SIZE, ctypes.byref(br), None)
                err = ctypes.get_last_error()
                returned = br.value
                
                if not ok:
                    if err == 38:
                        break
                    if err != 0:
                        logger.error(f"MFT枚举失败 {drive}: 错误代码 {err}")
                        raise OSError(f"枚举失败: {err}")
                    if returned <= 8:
                        break
                if returned <= 8:
                    break
                
                next_frn = ctypes.cast(ctypes.byref(buf), ctypes.POINTER(ctypes.c_uint64))[0]
                offset = 8
                batch_count = 0
                
                while offset < returned:
                    if offset + 4 > returned:
                        break
                    rec_len = ctypes.cast(ctypes.byref(buf, offset), ctypes.POINTER(ctypes.c_uint32))[0]
                    if rec_len == 0 or offset + rec_len > returned:
                        break
                    
                    if rec_len >= ctypes.sizeof(USN_RECORD_V2):
                        rec = ctypes.cast(ctypes.byref(buf, offset), ctypes.POINTER(USN_RECORD_V2)).contents
                        name_off, name_len = rec.FileNameOffset, rec.FileNameLength
                        if name_len > 0 and offset + name_off + name_len <= returned:
                            filename = bytes(buf[offset + name_off:offset + name_off + name_len]).decode('utf-16le', errors='replace')
                            if filename and filename[0] not in ('$', '.'):
                                file_ref = rec.FileReferenceNumber & 0x0000FFFFFFFFFFFF
                                parent_ref = rec.ParentFileReferenceNumber & 0x0000FFFFFFFFFFFF
                                is_dir = bool(rec.FileAttributes & FILE_ATTRIBUTE_DIRECTORY)
                                records[file_ref] = (filename, parent_ref, is_dir)
                                batch_count += 1
                    offset += rec_len
                
                total += batch_count
                if total and total % 100000 < batch_count:
                    logger.info(f"[MFT] {drive}: 已枚举 {total:,} 条, 用时 {time.time()-start_time:.1f}s")
                
                med.StartFileReferenceNumber = next_frn
                if batch_count == 0:
                    break
            
            logger.info(f"[MFT] {drive}: 枚举完成 {len(records):,} 条")
            
            logger.info(f"[MFT] {drive}: 开始构建路径...")
            
            # 1. 分离目录和文件，并创建父子关系映射
            dirs = {}
            files = {}
            parent_to_children = {}

            for ref, (name, parent_ref, is_dir) in records.items():
                if is_dir:
                    dirs[ref] = (name, parent_ref)
                    if parent_ref not in parent_to_children:
                        parent_to_children[parent_ref] = []
                    parent_to_children[parent_ref].append(ref)
                else:
                    files[ref] = (name, parent_ref)

            # 2. 层序遍历构建目录路径
            path_cache = {5: root_path}
            q = deque([5])
            
            while q:
                parent_ref = q.popleft()
                parent_path = path_cache.get(parent_ref)
                if not parent_path: continue
                
                parent_path_lower = parent_path.lower()
                if should_skip_path(parent_path_lower, allowed_paths_lower) or \
                   should_skip_dir(os.path.basename(parent_path_lower), parent_path_lower, allowed_paths_lower):
                    continue

                if parent_ref in parent_to_children:
                    for child_ref in parent_to_children[parent_ref]:
                        child_name, _ = dirs[child_ref]
                        child_path = os.path.join(parent_path, child_name)
                        path_cache[child_ref] = child_path
                        q.append(child_ref)
            
            logger.info(f"[MFT] {drive}: 目录路径构建完成，缓存了 {len(path_cache):,} 个有效目录。")

            # 3. 生成最终结果列表
            result = []
            
            for ref, (name, parent_ref) in dirs.items():
                full_path = path_cache.get(ref)
                if not full_path or full_path == root_path: continue
                parent_dir = path_cache.get(parent_ref, root_path)
                result.append([name, name.lower(), full_path, parent_dir, '', 0, 0, 1])

            for ref, (name, parent_ref) in files.items():
                parent_path = path_cache.get(parent_ref)
                if not parent_path: continue

                full_path = os.path.join(parent_path, name)
                
                if should_skip_path(full_path.lower(), allowed_paths_lower):
                    continue
                
                ext = os.path.splitext(name)[1].lower()
                if ext in skip_exts:
                    continue
                
                if allowed_paths_lower and not is_in_allowed_paths(full_path.lower(), allowed_paths_lower):
                    continue

                result.append([name, name.lower(), full_path, parent_path, ext, 0, 0, 0])

            logger.info(f"[MFT] {drive}: 路径拼接与过滤完成，总计 {len(result):,} 条。")

            # 4. 批量获取文件大小和修改时间
            files_to_stat = [item for item in result if item[7] == 0]
            if files_to_stat:
                logger.info(f"[MFT] {drive}: 开始获取 {len(files_to_stat):,} 个文件的大小和时间...")

                GetFileAttributesExW = kernel32.GetFileAttributesExW
                GetFileAttributesExW.restype = wintypes.BOOL
                GetFileAttributesExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_void_p]
                
                class WIN32_FILE_ATTRIBUTE_DATA(ctypes.Structure):
                    _fields_ = [
                        ("dwFileAttributes", wintypes.DWORD),
                        ("ftCreationTime", wintypes.FILETIME),
                        ("ftLastAccessTime", wintypes.FILETIME),
                        ("ftLastWriteTime", wintypes.FILETIME),
                        ("nFileSizeHigh", wintypes.DWORD),
                        ("nFileSizeLow", wintypes.DWORD),
                    ]
                
                GET_FILEEX_INFO_LEVELS = 0

                def filetime_to_unix(ft):
                    return (ft - 116444736000000000) / 10000000

                def stat_worker_win32(items_batch):
                    for item in items_batch:
                        try:
                            data = WIN32_FILE_ATTRIBUTE_DATA()
                            if GetFileAttributesExW(item[2], GET_FILEEX_INFO_LEVELS, ctypes.byref(data)):
                                size = (data.nFileSizeHigh << 32) + data.nFileSizeLow
                                mtime_ft = (data.ftLastWriteTime.dwHighDateTime << 32) + data.ftLastWriteTime.dwLowDateTime
                                item[5] = size
                                item[6] = filetime_to_unix(mtime_ft)
                        except Exception:
                            pass
                
                batch_size = math.ceil(len(files_to_stat) / 32)
                with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
                    futures = []
                    for i in range(0, len(files_to_stat), batch_size):
                        batch = files_to_stat[i:i + batch_size]
                        futures.append(executor.submit(stat_worker_win32, batch))
                    concurrent.futures.wait(futures)

                logger.info(f"[MFT] {drive}: 文件信息获取完成。")
            
            logger.info(f"[MFT] {drive}: 过滤后 {len(result):,} 条")
            return [tuple(item) for item in result]
        finally:
            CloseHandle(h)
else:
    def enum_volume_files_mft(drive_letter, skip_dirs, skip_exts, allowed_paths=None):
        raise OSError("MFT仅Windows可用")

# ==================== 索引管理器 (最终版 - 已包含 get_stats) ====================
class IndexManager:
    def __init__(self, db_path=None, config_mgr=None):
        self.config_mgr = config_mgr
        if db_path is None:
            idx_dir = LOG_DIR
            idx_dir.mkdir(exist_ok=True)
            self.db_path = str(idx_dir / "index.db")
        else:
            self.db_path = db_path
        self.conn = None
        self.lock = threading.RLock()
        self.is_ready = False
        self.is_building = False
        self.file_count = 0
        self.last_build_time = None
        self.has_fts = False
        self.used_mft = False
        self._init_db()

    def _init_db(self):
        try:
            self.conn = apsw.Connection(self.db_path)
            cursor = self.conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-2000000") # 2GB cache
            cursor.execute("PRAGMA temp_store=MEMORY")
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY, filename TEXT NOT NULL, filename_lower TEXT NOT NULL,
                    full_path TEXT UNIQUE NOT NULL, parent_dir TEXT NOT NULL, extension TEXT,
                    size INTEGER DEFAULT 0, mtime REAL DEFAULT 0, is_dir INTEGER DEFAULT 0
                )
            """)
            
            try:
                fts_exists = False
                for _ in cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files_fts'"):
                    fts_exists = True
                    break
                
                if not fts_exists:
                    cursor.execute("CREATE VIRTUAL TABLE files_fts USING fts5(filename, content=files, content_rowid=id)")
                    cursor.execute("CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN INSERT INTO files_fts(rowid, filename) VALUES (new.id, new.filename); END")
                    cursor.execute("CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN INSERT INTO files_fts(files_fts, rowid, filename) VALUES('delete', old.id, old.filename); END")
                self.has_fts = True
                logger.info("✅ FTS5 已启用")
            except apsw.Error as e:
                self.has_fts = False
                logger.warning(f"⚠️ FTS5 不可用: {e}")
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fn ON files(filename_lower)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_parent ON files(parent_dir)")
            cursor.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            
            self._load_stats()
        except apsw.Error as e:
            logger.error(f"❌ 数据库初始化错误: {e}")
            self.conn = None

    def _load_stats(self, preserve_mft=False):
        if not self.conn:
            return
        try:
            with self.lock:
                cursor = self.conn.cursor()
                
                count_result = cursor.execute("SELECT COUNT(*) FROM files").fetchone()
                self.file_count = count_result[0] if count_result else 0
                
                time_row = cursor.execute("SELECT value FROM meta WHERE key='build_time'").fetchone()
                if time_row and time_row[0]:
                    try: self.last_build_time = float(time_row[0])
                    except (ValueError, TypeError): self.last_build_time = None
                else:
                    self.last_build_time = None

                if not preserve_mft:
                    mft_row = cursor.execute("SELECT value FROM meta WHERE key='used_mft'").fetchone()
                    self.used_mft = bool(mft_row and mft_row[0] == '1')

            self.is_ready = self.file_count > 0
        except apsw.Error as e:
            logger.error(f"加载统计信息失败: {e}")
            self.file_count = 0
            self.is_ready = False

    def reload_stats(self):
        if not self.is_building:
            self._load_stats(preserve_mft=True)

    def change_db_path(self, new_path):
        if not new_path:
            return False, "路径不能为空"
        new_path = os.path.abspath(new_path)
        try:
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
        except OSError as e:
            logger.error(f"创建目录失败: {e}")
        self.close()
        if os.path.exists(self.db_path):
            for ext in ['', '-wal', '-shm']:
                src, dst = self.db_path + ext, new_path + ext
                if os.path.exists(src):
                    try: shutil.copy2(src, dst)
                    except (IOError, OSError) as e: logger.error(f"复制数据库文件失败 {src}: {e}")
        self.db_path = new_path
        self.conn = None
        self._init_db()
        return True, "已更改"

    def search(self, keywords, scope_targets, limit=50000):
        if not self.conn or not self.is_ready:
            return None
        try:
            with self.lock:
                cursor = self.conn.cursor()
                if self.has_fts and keywords:
                    fts_query = ' AND '.join(f'"{kw}"' for kw in keywords)
                    sql = "SELECT f.filename, f.full_path, f.size, f.mtime, f.is_dir FROM files f INNER JOIN files_fts fts ON f.id = fts.rowid WHERE fts MATCH ? LIMIT ?"
                    params = (fts_query, limit)
                else:
                    wheres = " AND ".join(["filename_lower LIKE ?"] * len(keywords))
                    sql = f"SELECT filename, full_path, size, mtime, is_dir FROM files WHERE {wheres} LIMIT ?"
                    params = [f"%{kw}%" for kw in keywords] + [limit]
                
                try:
                    raw_results = list(cursor.execute(sql, params))
                except apsw.Error as e:
                    logger.warning(f"FTS5查询失败，降级为LIKE: {e}")
                    wheres = " AND ".join(["filename_lower LIKE ?"] * len(keywords))
                    sql = f"SELECT filename, full_path, size, mtime, is_dir FROM files WHERE {wheres} LIMIT ?"
                    params = [f"%{kw}%" for kw in keywords] + [limit]
                    raw_results = list(cursor.execute(sql, params))
                
                filtered = []
                scope_targets_lower = [t.lower().rstrip('\\') for t in scope_targets] if scope_targets else None
                
                for row in raw_results:
                    path_lower = row[1].lower()
                    if scope_targets_lower and not is_in_allowed_paths(path_lower, scope_targets_lower): continue
                    if should_skip_path(path_lower, scope_targets_lower): continue
                    
                    name_lower = row[0].lower()
                    if row[4]: # is_dir
                        if should_skip_dir(name_lower, path_lower, scope_targets_lower): continue
                    else:
                        if os.path.splitext(name_lower)[1] in SKIP_EXTS: continue
                    
                    filtered.append(row)
                
                return filtered
        except apsw.Error as e:
            logger.error(f"搜索错误: {e}")
            return None

    def get_stats(self):
        """获取格式化的统计信息字典"""
        self._load_stats(preserve_mft=True)
        return {
            "count": self.file_count,
            "ready": self.is_ready,
            "building": self.is_building,
            "time": self.last_build_time,
            "path": self.db_path,
            "has_fts": self.has_fts,
            "used_mft": self.used_mft
        }

    def build_index(self, drives, progress_cb=None, stop_fn=None):
        global MFT_AVAILABLE
        if not self.conn: return
        self.is_building = True
        self.is_ready = False
        self.used_mft = False
        MFT_AVAILABLE = False
        build_start = time.time()
        
        try:
            # ========== 新的删除逻辑：DROP + CREATE ==========
            with self.lock:
                cursor = self.conn.cursor()
                # 删除旧表（比 DELETE 快很多）
                cursor.execute("DROP TABLE IF EXISTS files_fts")
                cursor.execute("DROP TABLE IF EXISTS files")
            
                # 重建主表
                cursor.execute("""
                    CREATE TABLE files (
                        id INTEGER PRIMARY KEY, filename TEXT NOT NULL, filename_lower TEXT NOT NULL,
                        full_path TEXT UNIQUE NOT NULL, parent_dir TEXT NOT NULL, extension TEXT,
                        size INTEGER DEFAULT 0, mtime REAL DEFAULT 0, is_dir INTEGER DEFAULT 0
                    )
               """) 
                cursor.execute("CREATE INDEX idx_fn ON files(filename_lower)")
                cursor.execute("CREATE INDEX idx_parent ON files(parent_dir)")
            
                # 重建 FTS5
                if self.has_fts:
                    try:
                        cursor.execute("CREATE VIRTUAL TABLE files_fts USING fts5(filename, content=files, content_rowid=id)")
                        cursor.execute("""CREATE TRIGGER files_ai AFTER INSERT ON files BEGIN 
                            INSERT INTO files_fts(rowid, filename) VALUES (new.id, new.filename); END""")
                        cursor.execute("""CREATE TRIGGER files_ad AFTER DELETE ON files BEGIN 
                            INSERT INTO files_fts(files_fts, rowid, filename) VALUES('delete', old.id, old.filename); END""")
                    except:
                        self.has_fts = False
        
                    self.file_count = 0

            all_drives = [d.upper().rstrip(':\\') for d in drives if os.path.exists(d)]
            c_allowed_paths = get_c_scan_dirs(self.config_mgr) if 'C' in all_drives else None
            
            logger.info(f"🔧 构建索引: 盘符 {all_drives}")
            if c_allowed_paths: logger.info(f"   C盘限制目录: {[os.path.basename(p) for p in c_allowed_paths]}")

            all_data, mft_scanned_drives, failed_drives = [], [], []

            if all_drives and IS_WINDOWS:
                data_lock = threading.Lock()
                def scan_one(drv):
                    try:
                        allowed = c_allowed_paths if drv == 'C' else None
                        logger.info(f"[MFT] 准备使用引擎扫描 {drv}: (Rust可用: {HAS_RUST_ENGINE})")
                        data = enum_volume_files_mft(drv, SKIP_DIRS_LOWER, SKIP_EXTS, allowed_paths=allowed)
                        with data_lock: all_data.extend(data)
                        return drv, len(data)
                    except Exception as e:
                        logger.error(f"[MFT] {drv}: 失败 - {e}")
                        return drv, -1
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(all_drives), 8)) as ex:
                    futures = [ex.submit(scan_one, d) for d in all_drives]
                    for future in concurrent.futures.as_completed(futures):
                        if stop_fn and stop_fn(): break
                        drv, result = future.result()
                        if result < 0: failed_drives.append(drv)
                        else: mft_scanned_drives.append(drv)
                        if progress_cb: progress_cb(len(all_data), f"MFT {drv}:")
                
                if all_data:
                    self.used_mft = True
                    logger.info(f"[MFT] 写入数据库: {len(all_data):,} 条")
    
                    with self.lock:
                        cursor = self.conn.cursor()
                        cursor.execute("PRAGMA synchronous=OFF")
                        cursor.execute("PRAGMA journal_mode=OFF")
        
                        # 临时禁用 FTS5 触发器
                        if self.has_fts:
                            cursor.execute("DROP TRIGGER IF EXISTS files_ai")
                            cursor.execute("DROP TRIGGER IF EXISTS files_ad")
    
                    # 写入主表
                    with self.lock, self.conn:
                        self.conn.cursor().executemany(
                            "INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)", 
                            all_data
                        )
    
                    # 重建 FTS5 索引
                    if self.has_fts:
                        logger.info("[MFT] 重建 FTS5 索引...")
                        with self.lock:
                            cursor = self.conn.cursor()
                            cursor.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
                            # 重建触发器
                            cursor.execute("""CREATE TRIGGER files_ai AFTER INSERT ON files BEGIN 
                                INSERT INTO files_fts(rowid, filename) VALUES (new.id, new.filename); END""")
                            cursor.execute("""CREATE TRIGGER files_ad AFTER DELETE ON files BEGIN 
                                INSERT INTO files_fts(files_fts, rowid, filename) VALUES('delete', old.id, old.filename);
              END""")
    
                    with self.lock:
                        cursor = self.conn.cursor()
                        cursor.execute("PRAGMA synchronous=NORMAL")
                        cursor.execute("PRAGMA journal_mode=WAL")
    
                    self.file_count += len(all_data)
                    logger.info(f"[MFT] 写入完成")
            
            for drv in failed_drives:
                if stop_fn and stop_fn(): break
                paths_to_scan = c_allowed_paths if drv == 'C' else [f"{drv}:\\"]
                for path in paths_to_scan:
                    logger.info(f"[传统扫描] {path}")
                    self._scan_dir(path, c_allowed_paths if drv == 'C' else None, progress_cb, stop_fn)

            elapsed = time.time() - build_start
            logger.info(f"✅ 索引完成: {self.file_count:,} 条 (MFT✅), 耗时 {elapsed:.2f}s")
            
            def final_tasks():
                try:
                    logger.info("📊 正在后台执行收尾任务...")
                    with apsw.Connection(self.db_path) as bg_conn:
                        cursor = bg_conn.cursor()
                        cursor.execute("INSERT OR REPLACE INTO meta VALUES('build_time', ?)", (str(time.time()),))
                        cursor.execute("INSERT OR REPLACE INTO meta VALUES('used_mft', ?)", ('1' if self.used_mft else '0',))
                        cursor.execute("ANALYZE")
                    self.reload_stats()
                    logger.info(f"✅ 后台收尾任务完成。最终精确文件数: {self.file_count:,}")
                except apsw.Error as e:
                    logger.error(f"后台收尾任务失败: {e}")

            threading.Thread(target=final_tasks, daemon=True).start()
            
        except Exception as e:
            import traceback
            logger.error(f"❌ 构建错误: {e}")
            traceback.print_exc()
        finally:
            self.is_building = False

    def _scan_dir(self, target, allowed_paths=None, progress_cb=None, stop_fn=None):
        try:
            if not os.path.exists(target): return
        except (OSError, PermissionError):
            logger.warning(f"无法访问目录: {target}")
            return
        
        allowed_paths_lower = [p.lower().rstrip('\\') for p in allowed_paths] if allowed_paths else None
        batch, stack = [], deque([target])
        
        while stack:
            if stop_fn and stop_fn(): break
            cur = stack.pop()
            
            if should_skip_path(cur.lower(), allowed_paths_lower): continue
            
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        if stop_fn and stop_fn(): break
                        if not e.name or e.name.startswith(('.', '$')): continue
                        
                        try:
                            is_dir = e.is_dir()
                            st = e.stat(follow_symlinks=False)
                        except (OSError, PermissionError): continue
                        
                        path_lower = e.path.lower()
                        
                        if is_dir:
                            if should_skip_dir(e.name.lower(), path_lower, allowed_paths_lower): continue
                            stack.append(e.path)
                            batch.append((e.name, e.name.lower(), e.path, cur, '', 0, 0, 1))
                        else:
                            ext = os.path.splitext(e.name)[1].lower()
                            if ext in SKIP_EXTS: continue
                            batch.append((e.name, e.name.lower(), e.path, cur, ext, st.st_size, st.st_mtime, 0))
                        
                        if len(batch) >= 20000:
                            with self.lock, self.conn:
                                self.conn.cursor().executemany("INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)", batch)
                            self.file_count += len(batch)
                            if progress_cb: progress_cb(self.file_count, cur)
                            batch = []
            except (PermissionError, OSError): continue
        
        if batch:
            with self.lock, self.conn:
                self.conn.cursor().executemany("INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)", batch)
            self.file_count += len(batch)

    def rebuild_drive(self, drive_letter, progress_cb=None, stop_fn=None):
        if not self.conn: 
            return
        
        drive = drive_letter.upper().rstrip(':\\')
        drive_pattern = f"{drive}:%"
        self.is_building = True
        build_start = time.time()
        
        try:
            # 删除该盘的旧数据
            with self.lock:
                cursor = self.conn.cursor()
                logger.info(f"🗑️ 删除 {drive}: 盘索引记录...")
            
                # 临时禁用 FTS5 触发器
                if self.has_fts:
                    cursor.execute("DROP TRIGGER IF EXISTS files_ai")
                    cursor.execute("DROP TRIGGER IF EXISTS files_ad")

                cursor.execute("DELETE FROM files WHERE full_path LIKE ?", (drive_pattern,))

            scan_paths = get_c_scan_dirs(self.config_mgr) if drive == 'C' else [f"{drive}:\\"]
            logger.info(f"🔧 重建 {drive}: 盘索引，目录: {scan_paths}")
            
            data_to_write = []
            if IS_WINDOWS:
                try:
                    logger.info(f"[MFT] 开始扫描 {drive}: ...")
                    data = enum_volume_files_mft(drive, SKIP_DIRS_LOWER, SKIP_EXTS,
                        allowed_paths=(scan_paths if drive == 'C' else None))
                    data_to_write.extend(data)
                    if progress_cb:
                        progress_cb(len(data), f"MFT {drive}:")
                except Exception as e:
                    logger.warning(f"[MFT] {drive}: 失败，使用传统扫描 - {e}")
                    for path in scan_paths:
                        self._scan_dir(path, (scan_paths if drive == 'C' else None), progress_cb, stop_fn)
            else:
                for path in scan_paths:
                    if stop_fn and stop_fn():
                        break
                    self._scan_dir(path, None, progress_cb, stop_fn)

            if data_to_write:
                logger.info(f"[MFT] {drive}: 写入 {len(data_to_write):,} 条记录")

                with self.lock:
                    cursor = self.conn.cursor()
                    cursor.execute("PRAGMA synchronous=OFF")

                with self.lock, self.conn:
                    self.conn.cursor().executemany(
                        "INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)", 
                        data_to_write
                    )
            
                with self.lock:
                    cursor = self.conn.cursor()
                    cursor.execute("PRAGMA synchronous=NORMAL")

            # 重建 FTS5 索引
            if self.has_fts:
                logger.info(f"[MFT] {drive}: 重建 FTS5 索引...")
                with self.lock:
                    cursor = self.conn.cursor()
                try:
                    cursor.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
                    # 重新创建触发器
                    cursor.execute("""CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN 
                        INSERT INTO files_fts(rowid, filename) VALUES (new.id, new.filename); END""")
                    cursor.execute("""CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN 
                        INSERT INTO files_fts(files_fts, rowid, filename) VALUES('delete', old.id, old.filename); END""")
                except Exception as e:
                    logger.warning(f"FTS5 重建失败: {e}")

                with self.lock, self.conn:
                    self.conn.cursor().execute("INSERT OR REPLACE INTO meta VALUES('build_time', ?)", (str(time.time()),))
        
                self.reload_stats()
                elapsed = time.time() - build_start
                logger.info(f"✅ {drive}: 盘索引重建完成，耗时 {elapsed:.2f}s")
        
        except apsw.Error as e:
            import traceback
            logger.error(f"❌ 重建 {drive}: 盘索引错误: {e}")
            traceback.print_exc()
        finally:
            self.is_building = False
      # ==================== 文件监控 ====================
if HAS_WATCHDOG:
    class _Handler(FileSystemEventHandler):
        def __init__(self, mgr, eq, config_mgr=None):
            self.mgr = mgr
            self.eq = eq
            self.config_mgr = config_mgr
        
        def _ignore(self, p):
            n = os.path.basename(p)
            if not n or n.startswith(('.', '$')):
                return True
            if os.path.splitext(n)[1].lower() in SKIP_EXTS:
                return True
            return any(part.lower() in SKIP_DIRS_LOWER for part in Path(p).parts)
        
        def on_created(self, e):
            if not self._ignore(e.src_path):
                self.eq.put(('c', e.src_path, e.is_directory))
        
        def on_deleted(self, e):
            if not self._ignore(e.src_path):
                self.eq.put(('d', e.src_path))
        
        def on_moved(self, e):
            self.eq.put(('m', e.src_path, e.dest_path))
else:
    class _Handler:
        pass


class FileWatcher:
    def __init__(self, mgr, config_mgr=None):
        self.mgr = mgr
        self.db_path = mgr.db_path
        self.config_mgr = config_mgr
        self.observer = None
        self.running = False
        self.eq = queue.Queue()
        self.thread = None
        self.stop_flag = False
        
    def start(self, paths):
        if not HAS_WATCHDOG or self.running:
            return
        try:
            self.observer = Observer()
            handler = _Handler(self.mgr, self.eq, self.config_mgr)
            for p in paths:
                if p.upper().startswith('C:'):
                    for cp in get_c_scan_dirs(self.config_mgr):
                        if os.path.exists(cp):
                            try:
                                self.observer.schedule(handler, cp, recursive=True)
                                logger.info(f"[监控] 添加: {cp}")
                            except Exception as e:
                                logger.error(f"[监控] 失败: {cp} - {e}")
                elif os.path.exists(p):
                    try:
                        self.observer.schedule(handler, p, recursive=True)
                        logger.info(f"[监控] 添加: {p}")
                    except Exception as e:
                        logger.error(f"[监控] 失败: {p} - {e}")
            self.observer.start()
            self.running = True
            self.stop_flag = False
            self.thread = threading.Thread(target=self._process, daemon=True)
            self.thread.start()
        except Exception as e:
            logger.error(f"[监控] 启动失败: {e}")
            
    def _process(self):
        batch, last = [], time.time()
        while not self.stop_flag:
            try:
                batch.append(self.eq.get(timeout=2.0))
            except queue.Empty:
                pass
            if batch and (len(batch) >= 100 or time.time() - last >= 2.0):
                self._apply(batch)
                batch.clear()
                last = time.time()
                
    def _apply(self, events):
        if not events:
            return

        ins, dels = [], []
        for ev in events:
            if ev[0] == 'c':
                p = ev[1]
                try:
                    # 只处理文件，目录的创建会在扫描时加入
                    if os.path.isfile(p):
                        n = os.path.basename(p)
                        st = os.stat(p)
                        ins.append((n, n.lower(), p, os.path.dirname(p), os.path.splitext(n)[1].lower(), st.st_size, st.st_mtime, 0))
                        logger.info(f"[监控] 新增文件: {p}")
                except (FileNotFoundError, PermissionError, OSError) as e:
                    logger.debug(f"监控新增失败 {p}: {e}")
            elif ev[0] == 'd':
                dels.append(ev[1])
                logger.info(f"[监控] 删除文件: {ev[1]}")
            elif ev[0] == 'm':
                # 移动操作简化为：删除旧路径，添加新路径
                dels.append(ev[1]) # ev[1] is src_path
                p = ev[2] # ev[2] is dest_path
                try:
                    if os.path.isfile(p):
                        n = os.path.basename(p)
                        st = os.stat(p)
                        ins.append((n, n.lower(), p, os.path.dirname(p), os.path.splitext(n)[1].lower(), st.st_size, st.st_mtime, 0))
                        logger.info(f"[监控] 移动(新增): {p}")
                except (FileNotFoundError, PermissionError, OSError) as e:
                    logger.debug(f"监控移动失败 {p}: {e}")

        if not ins and not dels:
            return

        # 使用独立的连接来执行数据库操作，避免与主线程冲突
        try:
            # 使用 with 语句确保连接和事务被正确处理
            with apsw.Connection(self.db_path) as conn:
                cursor = conn.cursor()
                if dels:
                    logger.info(f"[监控] 执行DELETE: {len(dels)}条")
                    # 对于删除，我们需要迭代执行
                    for d in dels:
                        # 删除文件本身或以该目录开头的所有文件
                        cursor.execute(
                            "DELETE FROM files WHERE full_path = ? OR full_path LIKE ?",
                            (d, d + os.path.sep + '%')
                        )
                if ins:
                    logger.info(f"[监控] 执行INSERT: {len(ins)}条")
                    cursor.executemany("INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)", ins)
            
            # 事务在 with 块结束时自动提交
            logger.info(f"[监控] 数据库已更新")
            # 更新主界面的统计信息（通过队列或事件）
            # 这里可以加一个回调，但为了简单，暂时让用户手动刷新
            
        except apsw.Error as e:
            logger.error(f"监控数据库更新失败: {e}")

                
    def stop(self):
        self.stop_flag = True
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        if self.observer and self.running:
            try:
                self.observer.stop()
                self.observer.join(timeout=2)
            except Exception as e:
                logger.error(f"停止监控失败: {e}")
            self.running = False


# ==================== 系统托盘管理 ====================
class TrayManager:
    """系统托盘管理器"""
    
    def __init__(self, app):
        self.app = app
        self.icon = None
        self.running = False
        self.thread = None
    
    def _create_icon_image(self):
        """创建托盘图标图像"""
        if not HAS_TRAY:
            return None
        
        # 创建一个简单的图标
        size = 64
        image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # 绘制一个搜索图标样式（圆形+手柄）
        # 圆形部分
        draw.ellipse([8, 8, 40, 40], outline='#4CAF50', width=4)
        # 手柄部分
        draw.line([36, 36, 54, 54], fill='#4CAF50', width=4)
        
        return image
    
    def _create_menu(self):
        """创建托盘菜单"""
        if not HAS_TRAY:
            return None
        
        return pystray.Menu(
            pystray.MenuItem("显示主窗口", self._show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("重建索引", self._rebuild_index),
            pystray.MenuItem("刷新状态", self._refresh_status),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit)
        )
    
    def _show_window(self, icon=None, item=None):
        """显示主窗口"""
        self.app.root.after(0, self._do_show_window)
    
    def _do_show_window(self):
        """在主线程中显示窗口"""
        self.app.root.deiconify()
        self.app.root.lift()
        self.app.root.focus_force()
        self.app.entry_kw.focus()
    
    def _rebuild_index(self, icon=None, item=None):
        """重建索引"""
        self.app.root.after(0, self.app._build_index)
    
    def _refresh_status(self, icon=None, item=None):
        """刷新状态"""
        self.app.root.after(0, self.app.refresh_index_status)
    
    def _quit(self, icon=None, item=None):
        """退出程序"""
        self.stop()
        self.app.root.after(0, self.app._do_quit)
    
    def start(self):
        """启动托盘"""
        if not HAS_TRAY or self.running:
            return False
        
        try:
            image = self._create_icon_image()
            if image is None:
                return False
            
            menu = self._create_menu()
            self.icon = pystray.Icon(
                "FileSearch",
                image,
                "极速文件搜索",
                menu
            )
            
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            self.running = True
            logger.info("🔔 托盘已启动")
            return True
        except Exception as e:
            logger.error(f"启动托盘失败: {e}")
            return False
    
    def _run(self):
        """运行托盘图标"""
        try:
            self.icon.run()
        except Exception as e:
            logger.error(f"托盘运行错误: {e}")
    
    def stop(self):
        """停止托盘"""
        if self.icon and self.running:
            try:
                self.icon.stop()
                self.running = False
                logger.info("🔔 托盘已停止")
            except Exception as e:
                logger.error(f"停止托盘失败: {e}")
    
    def show_notification(self, title, message):
        """显示托盘通知"""
        if self.icon and self.running and HAS_TRAY:
            try:
                self.icon.notify(message, title)
            except Exception as e:
                logger.debug(f"显示通知失败: {e}")


# ==================== 全局热键管理 ====================
class HotkeyManager:
    """全局热键管理器"""
    
    HOTKEY_MINI = 1      # 迷你窗口热键ID
    HOTKEY_MAIN = 2      # 主窗口热键ID
    
    def __init__(self, app):
        self.app = app
        self.registered = False
        self.thread = None
        self.stop_flag = False
    
    def start(self):
        """启动全局热键监听"""
        if not IS_WINDOWS:
            logger.warning("全局热键仅支持Windows系统")
            return False
        
        if self.registered:
            return True
        
        self.stop_flag = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True
    
    def _run(self):
        """热键监听线程"""
        try:
            import ctypes
            from ctypes import wintypes
            
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            
            # 定义函数
            RegisterHotKey = user32.RegisterHotKey
            RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            RegisterHotKey.restype = wintypes.BOOL
            
            UnregisterHotKey = user32.UnregisterHotKey
            UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            UnregisterHotKey.restype = wintypes.BOOL
            
            PeekMessageW = user32.PeekMessageW
            PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
            PeekMessageW.restype = wintypes.BOOL
            
            # 常量
            MOD_CONTROL = 0x0002
            MOD_SHIFT = 0x0004
            VK_SPACE = 0x20
            VK_TAB = 0x09
            WM_HOTKEY = 0x0312
            PM_REMOVE = 0x0001
            
            # 注册热键1: Ctrl+Shift+Space → 迷你窗口
            if not RegisterHotKey(None, self.HOTKEY_MINI, MOD_CONTROL | MOD_SHIFT, VK_SPACE):
                error = ctypes.get_last_error()
                logger.error(f"注册迷你窗口热键失败，错误代码: {error}")
            else:
                logger.info("⌨️ 全局热键已注册: Ctrl+Shift+Space → 迷你窗口")
            
            # 注册热键2: Ctrl+Shift+Tab → 主窗口
            if not RegisterHotKey(None, self.HOTKEY_MAIN, MOD_CONTROL | MOD_SHIFT, VK_TAB):
                error = ctypes.get_last_error()
                logger.error(f"注册主窗口热键失败，错误代码: {error}")
            else:
                logger.info("⌨️ 全局热键已注册: Ctrl+Shift+Tab → 主窗口")
            
            self.registered = True
            
            # 消息循环
            msg = wintypes.MSG()
            while not self.stop_flag:
                if PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    if msg.message == WM_HOTKEY:
                        if msg.wParam == self.HOTKEY_MINI:
                            self._on_hotkey_mini()
                        elif msg.wParam == self.HOTKEY_MAIN:
                            self._on_hotkey_main()
                else:
                    time.sleep(0.1)
            
            # 注销热键
            UnregisterHotKey(None, self.HOTKEY_MINI)
            UnregisterHotKey(None, self.HOTKEY_MAIN)
            self.registered = False
            logger.info("⌨️ 全局热键已注销")
            
        except Exception as e:
            logger.error(f"热键监听错误: {e}")
            import traceback
            traceback.print_exc()
            self.registered = False
    
    def _on_hotkey_mini(self):
        """迷你窗口热键触发"""
        logger.info("⌨️ 热键触发: 迷你窗口")
        self.app.root.after(0, self._show_mini_window)
    
    def _on_hotkey_main(self):
        """主窗口热键触发"""
        logger.info("⌨️ 热键触发: 主窗口")
        self.app.root.after(0, self._show_main_window)
    
    def _show_mini_window(self):
        """显示迷你窗口"""
        if hasattr(self.app, 'mini_search') and self.app.mini_search:
            self.app.mini_search.show()
    
    def _show_main_window(self):
        """显示主窗口"""
        try:
            if not self.app.root.winfo_viewable():
                self.app.root.deiconify()
            
            self.app.root.state('normal')
            self.app.root.lift()
            self.app.root.attributes('-topmost', True)
            self.app.root.after(100, lambda: self.app.root.attributes('-topmost', False))
            self.app.root.focus_force()
            self.app.entry_kw.focus()
            self.app.entry_kw.select_range(0, tk.END)
        except Exception as e:
            logger.error(f"显示主窗口失败: {e}")
    
    def stop(self):
        """停止热键监听"""
        self.stop_flag = True
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self.registered = False
        
# ==================== 迷你搜索窗口 ====================
class MiniSearchWindow:
    """迷你搜索窗口"""
    
    def __init__(self, app):
        self.app = app
        self.window = None
        self.search_mode = "index"  # "index" 或 "realtime"
        self.results = []
        self.result_listbox = None
        self.mode_label = None
        self.search_entry = None
        self.search_var = None
        self.tip_label = None
        self.result_frame = None
        self.tip_frame = None
        self.button_frame = None
        self.ctx_menu = None  # 右键菜单
    
    def show(self):
        """显示迷你窗口"""
        if self.window and self.window.winfo_exists():
            self.window.focus_force()
            self.search_entry.focus_force()
            self.search_entry.select_range(0, tk.END)
            return
        
        self._create_window()
    
    def _create_window(self):
        """创建窗口"""
        # 创建独立窗口
        self.window = tk.Toplevel()
        self.window.overrideredirect(True)
        self.window.attributes('-topmost', True)
        self.window.configure(bg='#b8e0f0')
        
        # 窗口大小和位置
        width, height = 720, 70
        screen_w = self.window.winfo_screenwidth()
        screen_h = self.window.winfo_screenheight()
        x = (screen_w - width) // 2
        y = int(screen_h * 0.20)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        
        # 边框
        self.border = tk.Frame(self.window, bg='#006699', padx=3, pady=3)
        self.border.pack(fill='both', expand=True)
        
        self.inner = tk.Frame(self.border, bg='#b8e0f0')
        self.inner.pack(fill='both', expand=True)
        
        # 主框架
        self.main_frame = tk.Frame(self.inner, bg='#b8e0f0', padx=10, pady=8)
        self.main_frame.pack(fill='both', expand=True)
        
        # 搜索栏
        self.search_frame = tk.Frame(self.main_frame, bg='#b8e0f0')
        self.search_frame.pack(fill='x')
        
        # 放大镜（可点击搜索）
        self.search_icon = tk.Label(
            self.search_frame,
            text="🔍",
            font=("Segoe UI Emoji", 18),
            bg='#b8e0f0',
            fg='#004466',
            cursor='hand2'
        )
        self.search_icon.pack(side='left', padx=(5, 12))
        self.search_icon.bind('<Button-1>', self._on_search)
        self.search_icon.bind('<Enter>', lambda e: self.search_icon.configure(fg='#0088cc'))
        self.search_icon.bind('<Leave>', lambda e: self.search_icon.configure(fg='#004466'))
        
        # 搜索输入框
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            self.search_frame,
            textvariable=self.search_var,
            font=("微软雅黑", 14),
            width=38,
            bg='#ffffff',
            fg='#333333',
            insertbackground='#006699',
            relief='flat',
            highlightthickness=2,
            highlightcolor='#006699',
            highlightbackground='#88c0d8'
        )
        self.search_entry.pack(side='left', fill='x', expand=True, ipady=6)
        
        # 关闭按钮
        self.close_btn = tk.Label(
            self.search_frame,
            text="✕",
            font=("Arial", 14, "bold"),
            bg='#b8e0f0',
            fg='#666666',
            cursor='hand2'
        )
        self.close_btn.pack(side='right', padx=(10, 5))
        self.close_btn.bind('<Button-1>', self._on_close)
        self.close_btn.bind('<Enter>', lambda e: self.close_btn.configure(fg='#cc0000'))
        self.close_btn.bind('<Leave>', lambda e: self.close_btn.configure(fg='#666666'))
        
        # 搜索模式区域
        self.mode_frame = tk.Frame(self.search_frame, bg='#b8e0f0')
        self.mode_frame.pack(side='right', padx=(15, 8))
        
        self.left_arrow = tk.Label(
            self.mode_frame, 
            text="◀", 
            font=("Arial", 12, "bold"), 
            bg='#b8e0f0', 
            fg='#004466',
            cursor='hand2'
        )
        self.left_arrow.pack(side='left', padx=(0, 3))
        self.left_arrow.bind('<Button-1>', self._on_mode_switch)
        self.left_arrow.bind('<Enter>', lambda e: self.left_arrow.configure(fg='#0088cc'))
        self.left_arrow.bind('<Leave>', lambda e: self.left_arrow.configure(fg='#004466'))
        
        self.mode_label = tk.Label(
            self.mode_frame,
            text="索引搜索",
            font=("微软雅黑", 10, "bold"),
            width=8,
            bg='#b8e0f0',
            fg='#004466'
        )
        self.mode_label.pack(side='left', padx=3)
        
        self.right_arrow = tk.Label(
            self.mode_frame, 
            text="▶", 
            font=("Arial", 12, "bold"), 
            bg='#b8e0f0', 
            fg='#004466',
            cursor='hand2'
        )
        self.right_arrow.pack(side='left', padx=(3, 0))
        self.right_arrow.bind('<Button-1>', self._on_mode_switch)
        self.right_arrow.bind('<Enter>', lambda e: self.right_arrow.configure(fg='#0088cc'))
        self.right_arrow.bind('<Leave>', lambda e: self.right_arrow.configure(fg='#004466'))
        
        # 结果区域（初始隐藏）
        self.result_frame = tk.Frame(self.main_frame, bg='#b8e0f0')
        
        self.result_listbox = tk.Listbox(
            self.result_frame,
            font=("微软雅黑", 11),
            height=12,
            bg='#ffffff',
            fg='#333333',
            selectbackground='#006699',
            selectforeground='#ffffff',
            borderwidth=1,
            highlightthickness=0,
            activestyle='none',
            relief='solid'
        )
        
        scrollbar = tk.Scrollbar(self.result_frame, orient="vertical", command=self.result_listbox.yview)
        self.result_listbox.configure(yscrollcommand=scrollbar.set)
        
        self.result_listbox.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        # 按钮区
        self.button_frame = tk.Frame(self.main_frame, bg='#b8e0f0')
        self.button_frame.pack(fill='x', pady=(6, 0))
        
        self.btn_open = tk.Button(
            self.button_frame, text="打开",
            font=("微软雅黑", 9),
            width=8,
            command=self._btn_open,
            bg='#ffffff', fg='#004466',
            relief='groove'
        )
        self.btn_open.pack(side='left', padx=(0, 4))
        
        self.btn_locate = tk.Button(
            self.button_frame, text="定位",
            font=("微软雅黑", 9),
            width=8,
            command=self._btn_locate,
            bg='#ffffff', fg='#004466',
            relief='groove'
        )
        self.btn_locate.pack(side='left', padx=4)
        
        self.btn_copy = tk.Button(
            self.button_frame, text="复制",
            font=("微软雅黑", 9),
            width=8,
            command=self._btn_copy,
            bg='#ffffff', fg='#004466',
            relief='groove'
        )
        self.btn_copy.pack(side='left', padx=4)
        
        self.btn_delete = tk.Button(
            self.button_frame, text="删除",
            font=("微软雅黑", 9),
            width=8,
            command=self._btn_delete,
            bg='#ffffff', fg='#aa0000',
            relief='groove'
        )
        self.btn_delete.pack(side='left', padx=4)
        
        self.btn_to_main = tk.Button(
            self.button_frame, text="主页面查看",
            font=("微软雅黑", 9),
            width=10,
            command=self._btn_to_main,
            bg='#ffffff', fg='#004466',
            relief='groove'
        )
        self.btn_to_main.pack(side='left', padx=4)
        
        # 提示栏
        self.tip_frame = tk.Frame(self.main_frame, bg='#b8e0f0')
        self.tip_label = tk.Label(
            self.tip_frame,
            text="Enter=打开  Ctrl+Enter=定位  Ctrl+C=复制  Delete=删除  Tab=主页面  Esc=关闭",
            font=("微软雅黑", 9),
            bg='#b8e0f0',
            fg='#004466'
        )
        self.tip_label.pack(pady=5)
        
        # 创建右键菜单
        self._create_context_menu()
        
        # 绑定事件
        self._bind_events()
        
        # 强制聚焦
        self.window.after(50, self._force_focus)
    
    def _force_focus(self):
        """强制聚焦到搜索框"""
        try:
            bg_color = '#b8e0f0'
            self.window.configure(bg=bg_color)
            self.border.configure(bg='#006699')
            self.inner.configure(bg=bg_color)
            self.main_frame.configure(bg=bg_color)
            self.search_frame.configure(bg=bg_color)
            self.mode_frame.configure(bg=bg_color)
            self.result_frame.configure(bg=bg_color)
            self.button_frame.configure(bg=bg_color)
            self.tip_frame.configure(bg=bg_color)
            self.close_btn.configure(bg=bg_color)
            self.search_icon.configure(bg=bg_color)
            self.left_arrow.configure(bg=bg_color)
            self.right_arrow.configure(bg=bg_color)
            self.mode_label.configure(bg=bg_color)
            self.tip_label.configure(bg=bg_color)
            self.window.update()
        except:
            pass
        
        self.window.focus_force()
        self.search_entry.focus_force()
    
    def _create_context_menu(self):
        """创建右键菜单"""
        self.ctx_menu = tk.Menu(self.window, tearoff=0)
        self.ctx_menu.add_command(label="打开", command=self._btn_open)
        self.ctx_menu.add_command(label="定位", command=self._btn_locate)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="复制", command=self._btn_copy)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="删除", command=self._btn_delete)
        self.ctx_menu.add_command(label="主页面查看", command=self._btn_to_main)
    
    def _bind_events(self):
        """绑定事件"""
        self.search_entry.bind('<Return>', self._on_search)
        self.search_entry.bind('<Escape>', self._on_close)
        self.search_entry.bind('<Up>', self._on_up)
        self.search_entry.bind('<Down>', self._on_down)
        self.search_entry.bind('<Left>', self._on_mode_switch)
        self.search_entry.bind('<Right>', self._on_mode_switch)
        self.search_entry.bind('<Tab>', self._on_switch_to_main)
        self.search_entry.bind('<Control-Return>', self._on_locate)
        self.search_entry.bind('<Control-c>', self._on_copy_shortcut)
        self.search_entry.bind('<Delete>', self._on_delete_shortcut)
        
        self.result_listbox.bind('<Return>', self._on_open)
        self.result_listbox.bind('<Double-Button-1>', self._on_open)
        self.result_listbox.bind('<Escape>', self._on_close)
        self.result_listbox.bind('<Tab>', self._on_switch_to_main)
        self.result_listbox.bind('<Control-Return>', self._on_locate)
        self.result_listbox.bind('<Control-c>', self._on_copy_shortcut)
        self.result_listbox.bind('<Delete>', self._on_delete_shortcut)
        self.result_listbox.bind('<Button-3>', self._on_right_click)
        
        self.window.bind('<Escape>', self._on_close)
    
    def _on_mode_switch(self, event=None):
        """切换搜索模式（键盘或鼠标点击）"""
        if event and hasattr(event, 'keysym'):
            text = self.search_var.get()
            cursor = self.search_entry.index('insert')
            if event.keysym == 'Left' and cursor > 0:
                return
            if event.keysym == 'Right' and cursor < len(text):
                return
        
        if self.search_mode == "index":
            self.search_mode = "realtime"
            self.mode_label.config(text="实时搜索")
        else:
            self.search_mode = "index"
            self.mode_label.config(text="索引搜索")
        return "break"
    
    def _on_search(self, event=None):
        """搜索"""
        keyword = self.search_var.get().strip()
        if not keyword:
            return
        
        self.results.clear()
        self.result_listbox.delete(0, tk.END)
        self._show_results_area()
        
        if self.search_mode == "index":
            self._search_index(keyword)
        else:
            self._search_realtime(keyword)
    
    def _search_index(self, keyword):
        """索引搜索"""
        if not self.app.index_mgr.is_ready:
            self.result_listbox.insert(tk.END, "   ⚠️ 索引未就绪，请先构建索引")
            return
        
        keywords = keyword.lower().split()
        scope_targets = self.app._get_search_scope_targets()
        results = self.app.index_mgr.search(keywords, scope_targets, limit=200)
        
        if results is None:
            self.result_listbox.insert(tk.END, "   ⚠️ 搜索失败")
            return
        
        self._display_results(results)
    
    def _search_realtime(self, keyword):
        """实时搜索"""
        self.result_listbox.insert(tk.END, "   🔍 正在搜索...")
        self.window.update()
        
        keywords = keyword.lower().split()
        scope_targets = self.app._get_search_scope_targets()
        results = []
        count = 0
        
        for target in scope_targets:
            if count >= 200 or not os.path.isdir(target):
                continue
            try:
                for root, dirs, files in os.walk(target):
                    dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS_LOWER and not d.startswith('.')]
                    for name in files + dirs:
                        if count >= 200:
                            break
                        if all(kw in name.lower() for kw in keywords):
                            fp = os.path.join(root, name)
                            is_dir = os.path.isdir(fp)
                            try:
                                st = os.stat(fp)
                                sz, mt = (0, st.st_mtime) if is_dir else (st.st_size, st.st_mtime)
                            except:
                                sz, mt = 0, 0
                            results.append((name, fp, sz, mt, 1 if is_dir else 0))
                            count += 1
            except:
                continue
        
        self.result_listbox.delete(0, tk.END)
        self._display_results(results)
    
    def _display_results(self, results):
        """显示结果"""
        if not results:
            self.result_listbox.insert(tk.END, "   😔 未找到匹配的文件")
            return
        
        self.results = []
        for i, (fn, fp, sz, mt, is_dir) in enumerate(results):
            ext = os.path.splitext(fn)[1].lower()
            if is_dir:
                icon = "📁"
            elif ext in ARCHIVE_EXTS:
                icon = "📦"
            else:
                icon = "📄"
            
            self.result_listbox.insert(tk.END, f"   {icon}  {fn}")
            
            # 奇偶行不同背景增加层次感
            if i % 2 == 0:
                self.result_listbox.itemconfig(i, bg='#ffffff')
            else:
                self.result_listbox.itemconfig(i, bg='#e8f4f8')
            
            self.results.append({
                'filename': fn,
                'fullpath': fp,
                'size': sz,
                'mtime': mt,
                'is_dir': is_dir
            })
        
        if self.results:
            self.result_listbox.selection_set(0)
        
        self.tip_label.config(text=f"找到 {len(self.results)} 个  │  Enter=打开  Ctrl+Enter=定位  Delete=删除  Tab=主页面  Esc=关闭")
    
    def _show_results_area(self):
        """显示结果区域并加大窗口"""
        self.result_frame.pack(fill='both', expand=True, pady=(10, 0))
        self.button_frame.pack(fill='x', pady=(6, 0))
        self.tip_frame.pack(fill='x')
        
        screen_w = self.window.winfo_screenwidth()
        screen_h = self.window.winfo_screenheight()
        x = (screen_w - 720) // 2
        y = int(screen_h * 0.15)
        self.window.geometry(f"720x480+{x}+{y}")
    
    def _get_current_item(self):
        """获取当前选中的结果项，没选中返回None"""
        if not self.results:
            return None
        sel = self.result_listbox.curselection()
        if not sel or sel[0] >= len(self.results):
            return None
        return self.results[sel[0]]
    
    # 按钮封装
    def _btn_open(self):
        self._on_open()
    
    def _btn_locate(self):
        self._on_locate()
    
    def _btn_copy(self):
        self._on_copy_shortcut()
    
    def _btn_delete(self):
        self._on_delete_shortcut()
    
    def _btn_to_main(self):
        self._on_switch_to_main()
    
    # 键盘快捷封装
    def _on_copy_shortcut(self, event=None):
        item = self._get_current_item()
        if not item:
            return "break"
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(item['fullpath'])
        except Exception as e:
            logger.error(f"复制路径失败: {e}")
        return "break"
    
    def _on_delete_shortcut(self, event=None):
        item = self._get_current_item()
        if not item:
            return "break"
        path = item['fullpath']
        name = item['filename']
        
        if HAS_SEND2TRASH:
            msg = f"确定删除？\n{name}\n\n将移动到回收站。"
        else:
            msg = f"确定永久删除？\n{name}\n\n⚠ 此操作不可恢复。"
        
        if not messagebox.askyesno("确认删除", msg, parent=self.window):
            return "break"
        
        try:
            if HAS_SEND2TRASH:
                send2trash.send2trash(path)
            else:
                if item['is_dir']:
                    shutil.rmtree(path)
                else:
                    os.remove(path)
        except Exception as e:
            logger.error(f"删除失败: {path} - {e}")
            messagebox.showerror("删除失败", f"无法删除：\n{path}\n\n{e}", parent=self.window)
            return "break"
        
        idx = self.result_listbox.curselection()[0]
        self.result_listbox.delete(idx)
        del self.results[idx]
        
        if self.results:
            new_idx = min(idx, len(self.results) - 1)
            self.result_listbox.selection_set(new_idx)
            self.result_listbox.see(new_idx)
        return "break"
    
    def _on_open(self, event=None):
        item = self._get_current_item()
        if not item:
            return
        try:
            if item['is_dir']:
                subprocess.Popen(f'explorer "{item["fullpath"]}"')
            else:
                os.startfile(item['fullpath'])
            self.close()
        except Exception as e:
            logger.error(f"打开失败: {e}")
    
    def _on_locate(self, event=None):
        item = self._get_current_item()
        if not item:
            return "break"
        try:
            subprocess.Popen(f'explorer /select,"{item["fullpath"]}"')
            self.close()
        except Exception as e:
            logger.error(f"定位失败: {e}")
        return "break"
    
    def _on_switch_to_main(self, event=None):
        """切换到主窗口并联动结果"""
        keyword = self.search_var.get().strip()
        results_copy = list(self.results)
        
        self.close()
        
        self.app.root.deiconify()
        self.app.root.lift()
        self.app.root.focus_force()
        
        if keyword:
            self.app.kw_var.set(keyword)
            
            if results_copy:
                with self.app.results_lock:
                    self.app.all_results.clear()
                    self.app.filtered_results.clear()
                    self.app.shown_paths.clear()
                    
                    for item in results_copy:
                        ext = os.path.splitext(item['filename'])[1].lower()
                        if item['is_dir']:
                            tc, ss = 0, "📂 文件夹"
                        elif ext in ARCHIVE_EXTS:
                            tc, ss = 1, "📦 压缩包"
                        else:
                            tc, ss = 2, format_size(item['size'])
                        
                        self.app.all_results.append({
                            'filename': item['filename'],
                            'fullpath': item['fullpath'],
                            'dir_path': os.path.dirname(item['fullpath']),
                            'size': item['size'],
                            'mtime': item['mtime'],
                            'type_code': tc,
                            'size_str': ss,
                            'mtime_str': format_time(item['mtime'])
                        })
                        self.app.shown_paths.add(item['fullpath'])
                    
                    self.app.filtered_results = list(self.app.all_results)
                    self.app.total_found = len(self.app.all_results)
                
                self.app.current_page = 1
                self.app._update_ext_combo()
                self.app._render_page()
                self.app.status.set(f"✅ 从迷你窗口导入 {len(results_copy)} 个结果")
                self.app.btn_refresh.config(state="normal")
        
        self.app.entry_kw.focus()
        return "break"
    
    def _on_up(self, event=None):
        if not self.results:
            return "break"
        cur = self.result_listbox.curselection()
        if cur and cur[0] > 0:
            self.result_listbox.selection_clear(0, tk.END)
            self.result_listbox.selection_set(cur[0] - 1)
            self.result_listbox.see(cur[0] - 1)
        return "break"
    
    def _on_down(self, event=None):
        if not self.results:
            return "break"
        cur = self.result_listbox.curselection()
        if cur:
            if cur[0] < len(self.results) - 1:
                self.result_listbox.selection_clear(0, tk.END)
                self.result_listbox.selection_set(cur[0] + 1)
                self.result_listbox.see(cur[0] + 1)
        else:
            self.result_listbox.selection_set(0)
        return "break"
    
    def _on_right_click(self, event):
        """右键菜单"""
        if not self.results:
            return
        try:
            idx = self.result_listbox.nearest(event.y)
            self.result_listbox.selection_clear(0, tk.END)
            self.result_listbox.selection_set(idx)
            self.result_listbox.activate(idx)
            self.ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.ctx_menu.grab_release()
    
    def _on_close(self, event=None):
        """关闭"""
        self.close()
        return "break"
    
    def close(self):
        """关闭窗口"""
        if self.window:
            try:
                self.window.destroy()
            except:
                pass
            self.window = None
        self.results.clear()

# ==================== C盘目录设置对话框（新版） ====================
class CDriveSettingsDialog:
    """C盘目录设置对话框"""
    
    def __init__(self, parent, config_mgr, index_mgr=None, on_rebuild_callback=None):
        self.parent = parent
        self.config_mgr = config_mgr
        self.index_mgr = index_mgr
        self.on_rebuild_callback = on_rebuild_callback
        self.dialog = None
        self.path_vars = {}  # {path: BooleanVar}
        self.paths_frame = None
        self.canvas = None
        self.stat_label = None
        self.original_paths = []  # 保存原始配置，用于检测变化
    
    def show(self):
        """显示对话框"""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("⚙️ C盘扫描目录设置")
        self.dialog.geometry("650x500")
        self.dialog.transient(self.parent)
        self.dialog.grab_set()
        self.dialog.resizable(True, True)
        
        # 居中显示
        self.dialog.geometry(f"+{self.parent.winfo_x() + 100}+{self.parent.winfo_y() + 50}")
        
        # 保存原始配置
        self.original_paths = [p.copy() for p in self.config_mgr.get_c_scan_paths()]
        
        self._build_ui()
    
    def _build_ui(self):
        """构建对话框UI"""
        main_frame = ttk.Frame(self.dialog, padding=15)
        main_frame.pack(fill=BOTH, expand=True)
        
        # 说明文字
        ttk.Label(
            main_frame, 
            text="设置C盘索引扫描的目录范围，勾选启用，取消勾选禁用，点击 ✕ 删除",
            font=("微软雅黑", 9),
            foreground="#666"
        ).pack(anchor=W, pady=(0, 10))
        
        # 按钮栏
        btn_row = ttk.Frame(main_frame)
        btn_row.pack(fill=X, pady=(0, 8))
        
        ttk.Label(btn_row, text="扫描目录列表:", font=("微软雅黑", 10, "bold")).pack(side=LEFT)
        
        # 右侧添加按钮
        ttk.Button(
            btn_row, text="+ 手动输入", 
            command=self._manual_add, 
            bootstyle="info-outline",
            width=10
        ).pack(side=RIGHT, padx=(5, 0))
        
        ttk.Button(
            btn_row, text="+ 浏览添加", 
            command=self._browse_add, 
            bootstyle="success-outline",
            width=10
        ).pack(side=RIGHT)
        
        # 快捷操作栏
        quick_row = ttk.Frame(main_frame)
        quick_row.pack(fill=X, pady=(0, 8))
        
        ttk.Button(
            quick_row, text="✓ 全选", 
            command=self._select_all, 
            bootstyle="secondary-outline",
            width=8
        ).pack(side=LEFT, padx=(0, 3))
        
        ttk.Button(
            quick_row, text="✗ 全不选", 
            command=self._select_none, 
            bootstyle="secondary-outline",
            width=8
        ).pack(side=LEFT, padx=(0, 3))
        
        ttk.Button(
            quick_row, text="↻ 反选", 
            command=self._select_invert, 
            bootstyle="secondary-outline",
            width=8
        ).pack(side=LEFT)
        
        # 统计标签
        self.stat_label = ttk.Label(quick_row, text="", font=("微软雅黑", 9), foreground="#666")
        self.stat_label.pack(side=RIGHT)
        
        # 路径列表区域（带滚动条）
        list_container = ttk.Frame(main_frame)
        list_container.pack(fill=BOTH, expand=True, pady=(0, 10))
        
        # 创建Canvas和Scrollbar
        self.canvas = tk.Canvas(list_container, highlightthickness=0, bg="#fafafa")
        scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=self.canvas.yview)
        
        self.paths_frame = ttk.Frame(self.canvas)
        self.paths_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        self.canvas.create_window((0, 0), window=self.paths_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        
        # 鼠标滚轮支持
        def on_mousewheel(event):
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.canvas.bind_all("<MouseWheel>", on_mousewheel)
        
        scrollbar.pack(side=RIGHT, fill=Y)
        self.canvas.pack(side=LEFT, fill=BOTH, expand=True)
        
        # 加载路径列表
        self._refresh_paths_list()
        
        # 分隔线
        ttk.Separator(main_frame).pack(fill=X, pady=5)
        
        # 底部按钮
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill=X, pady=(10, 0))
        
        ttk.Button(
            bottom_frame, text="恢复系统默认", 
            command=self._reset_default, 
            bootstyle="warning-outline",
            width=12
        ).pack(side=LEFT)
        
        ttk.Button(
            bottom_frame, text="保存", 
            command=self._save, 
            bootstyle="success",
            width=10
        ).pack(side=RIGHT, padx=(5, 0))
        
        ttk.Button(
            bottom_frame, text="取消", 
            command=self.dialog.destroy, 
            bootstyle="secondary",
            width=10
        ).pack(side=RIGHT)
        
        ttk.Button(
            bottom_frame, text="🔄 立即重建C盘", 
            command=self._rebuild_c_drive, 
            bootstyle="primary-outline",
            width=14
        ).pack(side=RIGHT, padx=(0, 20))
    
    def _refresh_paths_list(self):
        """刷新路径列表显示"""
        # 清空现有内容
        for widget in self.paths_frame.winfo_children():
            widget.destroy()
        self.path_vars.clear()
        
        paths = self.config_mgr.get_c_scan_paths()
        
        if not paths:
            ttk.Label(
                self.paths_frame, 
                text="（暂无目录，请点击上方按钮添加）",
                font=("微软雅黑", 9),
                foreground="gray"
            ).pack(anchor=W, pady=20, padx=10)
            self._update_stats()
            return
        
        for i, item in enumerate(paths):
            path = item.get("path", "")
            enabled = item.get("enabled", True)
            
            row = ttk.Frame(self.paths_frame)
            row.pack(fill=X, pady=2, padx=5)
            
            # 复选框
            var = tk.BooleanVar(value=enabled)
            self.path_vars[path] = var
            
            cb = ttk.Checkbutton(
                row, 
                variable=var, 
                bootstyle="round-toggle",
                command=self._update_stats
            )
            cb.pack(side=LEFT)
            
            # 路径显示
            path_exists = os.path.isdir(path)
            
            # 路径过长时截断显示
            max_len = 55
            if len(path) > max_len:
                # 显示前面一部分 + ... + 后面一部分
                display_path = path[:20] + "..." + path[-(max_len-23):]
            else:
                display_path = path
            
            if not path_exists:
                display_path = f"{display_path}  (不存在)"
            
            lbl = ttk.Label(
                row, 
                text=display_path, 
                font=("Consolas", 9),
                foreground="#333" if path_exists else "red",
                cursor="hand2"
            )
            lbl.pack(side=LEFT, fill=X, expand=True, padx=(8, 5))
            
            # 鼠标悬停显示完整路径
            def show_tooltip(event, full_path=path):
                tooltip = tk.Toplevel(self.dialog)
                tooltip.wm_overrideredirect(True)
                tooltip.wm_geometry(f"+{event.x_root + 10}+{event.y_root + 10}")
                
                tip_label = ttk.Label(
                    tooltip, 
                    text=full_path, 
                    font=("Consolas", 9),
                    background="#ffffe0",
                    relief="solid",
                    borderwidth=1,
                    padding=(5, 2)
                )
                tip_label.pack()
                
                # 保存tooltip引用，用于销毁
                lbl._tooltip = tooltip
            
            def hide_tooltip(event):
                if hasattr(lbl, '_tooltip') and lbl._tooltip:
                    lbl._tooltip.destroy()
                    lbl._tooltip = None
            
            lbl.bind("<Enter>", show_tooltip)
            lbl.bind("<Leave>", hide_tooltip)
            
            # 删除按钮
            del_btn = ttk.Button(
                row, 
                text="✕", 
                command=lambda p=path: self._delete_path(p),
                bootstyle="danger-link",
                width=3
            )
            del_btn.pack(side=RIGHT, padx=(10, 8))
        
        self._update_stats()
    
    def _select_all(self):
        """全选"""
        for var in self.path_vars.values():
            var.set(True)
        self._update_stats()
    
    def _select_none(self):
        """全不选"""
        for var in self.path_vars.values():
            var.set(False)
        self._update_stats()
    
    def _select_invert(self):
        """反选"""
        for var in self.path_vars.values():
            var.set(not var.get())
        self._update_stats()
    
    def _update_stats(self):
        """更新统计信息"""
        total = len(self.path_vars)
        enabled = sum(1 for var in self.path_vars.values() if var.get())
        self.stat_label.config(text=f"共 {total} 个目录，已启用 {enabled} 个")
    
    def _browse_add(self):
        """浏览添加目录"""
        path = filedialog.askdirectory(
            title="选择C盘目录",
            initialdir="C:\\"
        )
        if path:
            self._add_path(path)
    
    def _manual_add(self):
        """手动输入添加目录"""
        dialog = tk.Toplevel(self.dialog)
        dialog.title("手动输入C盘目录路径")
        dialog.geometry("450x150")
        dialog.transient(self.dialog)
        dialog.grab_set()
        dialog.geometry(f"+{self.dialog.winfo_x() + 50}+{self.dialog.winfo_y() + 100}")
        
        frame = ttk.Frame(dialog, padding=15)
        frame.pack(fill=BOTH, expand=True)
        
        ttk.Label(frame, text="路径:", font=("微软雅黑", 9)).pack(anchor=W)
        
        input_frame = ttk.Frame(frame)
        input_frame.pack(fill=X, pady=(5, 10))
        
        path_var = tk.StringVar()
        entry = ttk.Entry(input_frame, textvariable=path_var, font=("Consolas", 10))
        entry.pack(side=LEFT, fill=X, expand=True, padx=(0, 5))
        entry.focus()
        
        # 输入框右键菜单
        entry_menu = tk.Menu(dialog, tearoff=0)
        entry_menu.add_command(label="粘贴", command=lambda: entry.event_generate("<<Paste>>"))
        entry_menu.add_command(label="清空", command=lambda: path_var.set(""))
        entry.bind("<Button-3>", lambda e: entry_menu.post(e.x_root, e.y_root))
        
        ttk.Button(
            input_frame, text="📁", width=3,
            command=lambda: path_var.set(filedialog.askdirectory(initialdir="C:\\") or path_var.get())
        ).pack(side=LEFT)
        
        ttk.Label(frame, text="⚠️ 请输入C盘下的有效目录路径", font=("微软雅黑", 8), foreground="#888").pack(anchor=W)
        
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=X, pady=(15, 0))
        
        def do_add():
            path = path_var.get().strip()
            if path:
                if self._add_path(path):
                    dialog.destroy()
        
        ttk.Button(btn_frame, text="添加", command=do_add, bootstyle="success", width=10).pack(side=RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text="取消", command=dialog.destroy, bootstyle="secondary", width=10).pack(side=RIGHT)
        
        entry.bind("<Return>", lambda e: do_add())
    
    def _add_path(self, path):
        """添加路径"""
        path = os.path.normpath(path)
        
        # 验证
        if not path.upper().startswith("C:"):
            messagebox.showerror("错误", "只能添加C盘路径", parent=self.dialog)
            return False
        
        if not os.path.isdir(path):
            messagebox.showerror("错误", "路径不存在", parent=self.dialog)
            return False
        
        # 检查是否已存在
        paths = self.config_mgr.get_c_scan_paths()
        for p in paths:
            if os.path.normpath(p["path"]).lower() == path.lower():
                messagebox.showwarning("提示", "路径已存在", parent=self.dialog)
                return False
        
        # 添加到配置
        paths.append({"path": path, "enabled": True})
        self.config_mgr.set_c_scan_paths(paths)
        
        # 刷新列表
        self._refresh_paths_list()
        
        return True
    
    def _delete_path(self, path):
        """删除路径"""
        if not messagebox.askyesno("确认", f"确定删除此目录？\n{path}", parent=self.dialog):
            return
        
        paths = self.config_mgr.get_c_scan_paths()
        paths = [p for p in paths if os.path.normpath(p["path"]).lower() != os.path.normpath(path).lower()]
        self.config_mgr.set_c_scan_paths(paths)
        
        self._refresh_paths_list()
    
    def _reset_default(self):
        """恢复默认设置"""
        if messagebox.askyesno("确认", "确定恢复系统默认目录？\n这将清空当前列表。", parent=self.dialog):
            self.config_mgr.reset_c_scan_paths()
            self._refresh_paths_list()
    
    def _save(self):
        """保存设置"""
        # 更新启用状态
        paths = self.config_mgr.get_c_scan_paths()
        for p in paths:
            path = p["path"]
            if path in self.path_vars:
                p["enabled"] = self.path_vars[path].get()
        
        self.config_mgr.set_c_scan_paths(paths)
        
        # 检测是否有变化
        current_paths = self.config_mgr.get_c_scan_paths()
        has_changes = self._detect_changes(current_paths)
        
        if has_changes:
            # 询问是否立即重建
            result = messagebox.askyesnocancel(
                "设置已保存",
                "C盘目录配置已更改。\n\n是否立即重建C盘索引？\n（只更新C盘，其他磁盘保持不变）",
                parent=self.dialog
            )
            
            if result is True:  # 是
                self.dialog.destroy()
                self._do_rebuild_c_drive()
            elif result is False:  # 否
                messagebox.showinfo("提示", "设置已保存，稍后可手动重建C盘索引", parent=self.dialog)
                self.dialog.destroy()
            # result is None (取消) - 不关闭对话框
        else:
            messagebox.showinfo("成功", "设置已保存", parent=self.dialog)
            self.dialog.destroy()
    
    def _detect_changes(self, current_paths):
        """检测配置是否有变化"""
        if len(current_paths) != len(self.original_paths):
            return True
        
        for curr, orig in zip(current_paths, self.original_paths):
            if curr.get("path") != orig.get("path"):
                return True
            if curr.get("enabled") != orig.get("enabled"):
                return True
        
        return False
    
    def _rebuild_c_drive(self):
        """立即重建C盘索引按钮"""
        # 先保存当前设置
        paths = self.config_mgr.get_c_scan_paths()
        for p in paths:
            path = p["path"]
            if path in self.path_vars:
                p["enabled"] = self.path_vars[path].get()
        self.config_mgr.set_c_scan_paths(paths)
        
        if messagebox.askyesno("确认", "确定立即重建C盘索引？", parent=self.dialog):
            self.dialog.destroy()
            self._do_rebuild_c_drive()
    
    def _do_rebuild_c_drive(self):
        """执行C盘重建"""
        if self.on_rebuild_callback:
            self.on_rebuild_callback("C")
# ==================== 批量重命名对话框 ====================
class BatchRenameDialog:
    """批量重命名对话框（带预览与实际重命名）"""
    
    def __init__(self, parent, targets, app):
        self.parent = parent
        self.targets = targets  # list of item dicts: {'filename', 'fullpath', ...}
        self.app = app
        self.dialog = None
        
        # 规则相关变量
        self.mode_var = tk.StringVar(value="prefix")  # prefix / replace
        self.prefix_var = tk.StringVar(value="")
        self.start_num_var = tk.IntVar(value=1)
        self.width_var = tk.IntVar(value=3)
        self.find_var = tk.StringVar(value="")
        self.replace_var = tk.StringVar(value="")
        
        self.preview_lines = []  # [(old_full, new_full), ...]
    
    def show(self, scope_text=""):
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("✏ 批量重命名")
        self.dialog.geometry("780x650")
        self.dialog.transient(self.parent)
        self.dialog.grab_set()
        self.dialog.resizable(True, True)
        self.dialog.geometry(f"+{self.parent.winfo_x()+80}+{self.parent.winfo_y()+40}")
        
        main = ttk.Frame(self.dialog, padding=15)
        main.pack(fill=BOTH, expand=True)
        
        # 顶部说明
        ttk.Label(main, text="批量重命名", font=("微软雅黑", 12, "bold")).pack(anchor=W)
        ttk.Label(
            main,
            text=scope_text,
            font=("微软雅黑", 9),
            foreground="#555"
        ).pack(anchor=W, pady=(0, 5))
        
        ttk.Separator(main).pack(fill=X, pady=5)
        
        # 规则区域
        rule_frame = ttk.Labelframe(main, text="重命名规则", padding=10)
        rule_frame.pack(fill=X, pady=(5, 10))
        
        # 模式选择
        mode_row = ttk.Frame(rule_frame)
        mode_row.pack(fill=X, pady=3)
        
        ttk.Radiobutton(
            mode_row, text="前缀 + 序号", variable=self.mode_var, value="prefix"
        ).pack(side=LEFT, padx=(0, 15))
        ttk.Radiobutton(
            mode_row, text="替换文本", variable=self.mode_var, value="replace"
        ).pack(side=LEFT)
        
        # 前缀 + 序号设置
        prefix_row = ttk.Frame(rule_frame)
        prefix_row.pack(fill=X, pady=3)
        
        ttk.Label(prefix_row, text="新前缀:").pack(side=LEFT)
        ttk.Entry(prefix_row, textvariable=self.prefix_var, width=20).pack(side=LEFT, padx=(3, 15))
        
        ttk.Label(prefix_row, text="起始序号:").pack(side=LEFT)
        ttk.Entry(prefix_row, textvariable=self.start_num_var, width=6).pack(side=LEFT, padx=(3, 15))
        
        ttk.Label(prefix_row, text="序号位数:").pack(side=LEFT)
        ttk.Entry(prefix_row, textvariable=self.width_var, width=4).pack(side=LEFT, padx=(3, 0))
        
        # 替换文本设置
        replace_row = ttk.Frame(rule_frame)
        replace_row.pack(fill=X, pady=3)
        
        ttk.Label(replace_row, text="查找文本:").pack(side=LEFT)
        ttk.Entry(replace_row, textvariable=self.find_var, width=18).pack(side=LEFT, padx=(3, 15))
        
        ttk.Label(replace_row, text="替换为:").pack(side=LEFT)
        ttk.Entry(replace_row, textvariable=self.replace_var, width=18).pack(side=LEFT, padx=(3, 0))
        
        # 预览区域
        preview_frame = ttk.Labelframe(main, text="预览", padding=10)
        preview_frame.pack(fill=BOTH, expand=True, pady=(5, 10))
        
        self.preview_text = tk.Text(
            preview_frame,
            font=("Consolas", 9),
            height=12,
            wrap="none"
        )
        self.preview_text.pack(fill=BOTH, expand=True)
        
        # 按钮区域
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=X)
        
        ttk.Button(
            btn_frame, text="预览效果", command=self._update_preview,
            bootstyle="info"
        ).pack(side=LEFT)
        
        ttk.Button(
            btn_frame, text="执行重命名", command=self._do_rename,
            bootstyle="success"
        ).pack(side=LEFT, padx=8)
        
        ttk.Button(
            btn_frame, text="关闭", command=self.dialog.destroy,
            bootstyle="secondary"
        ).pack(side=RIGHT)
        
        # 初次预览
        self._update_preview()
    
    def _update_preview(self):
        """根据当前规则生成预览"""
        self.preview_text.delete("1.0", tk.END)
        self.preview_lines = []
        
        if not self.targets:
            self.preview_text.insert(tk.END, "（没有可重命名的项目）")
            return
        
        mode = self.mode_var.get()
        
        # 生成新名字
        if mode == "prefix":
            prefix = self.prefix_var.get()
            start = self.start_num_var.get()
            width = self.width_var.get()
            
            num = start
            for item in self.targets:
                old_full = item['fullpath']
                old_name = item['filename']
                name, ext = os.path.splitext(old_name)
                new_name = f"{prefix}{str(num).zfill(width)}{ext}"
                num += 1
                new_full = os.path.join(os.path.dirname(old_full), new_name)
                self.preview_lines.append((old_full, new_full))
        else:  # replace
            find = self.find_var.get()
            replace = self.replace_var.get()
            for item in self.targets:
                old_full = item['fullpath']
                old_name = item['filename']
                name, ext = os.path.splitext(old_name)
                if find:
                    new_name = name.replace(find, replace) + ext
                else:
                    new_name = old_name
                new_full = os.path.join(os.path.dirname(old_full), new_name)
                self.preview_lines.append((old_full, new_full))
        
        # 显示预览文本 + 标记潜在冲突
        lines = []
        for old_full, new_full in self.preview_lines:
            old_name = os.path.basename(old_full)
            new_name = os.path.basename(new_full)
            mark = ""
            if old_full == new_full:
                mark = "  (未变化)"
            else:
                # 简单判断：新路径已存在且不是自己
                if os.path.exists(new_full) and os.path.normpath(old_full).lower() != os.path.normpath(new_full).lower():
                    mark = "  (⚠ 目标已存在)"
            lines.append(f"{old_name}  →  {new_name}{mark}")
        
        self.preview_text.insert(tk.END, "\n".join(lines))
    
    def _do_rename(self):
        """执行实际重命名，并尝试同步更新主窗口结果（含调试日志）"""
        if not self.preview_lines:
            messagebox.showwarning("提示", "没有可执行的重命名记录", parent=self.dialog)
            return

        if not messagebox.askyesno("确认", "确定执行重命名？\n请先确认预览无误。", parent=self.dialog):
            return

        success = 0
        skipped = 0
        failed = 0
        renamed_pairs = []

        # 1) 先改磁盘
        for old_full, new_full in self.preview_lines:
            if old_full == new_full:
                skipped += 1
                continue
            try:
                if os.path.exists(new_full) and os.path.normpath(old_full).lower() != os.path.normpath(new_full).lower():
                    skipped += 1
                    logger.warning(f"[重命名跳过] 目标已存在: {new_full}")
                    continue
                os.rename(old_full, new_full)
                success += 1
                renamed_pairs.append((old_full, new_full))
                logger.info(f"[重命名成功] {old_full} -> {new_full}")
            except Exception as e:
                failed += 1
                logger.error(f"[重命名失败] {old_full} -> {new_full} - {e}")

        # 2) 同步更新主窗口内存
        if renamed_pairs:
            with self.app.results_lock:
                for old_full, new_full in renamed_pairs:
                    old_norm = os.path.normpath(old_full)
                    new_norm = os.path.normpath(new_full)
                    new_name = os.path.basename(new_norm)
                    new_dir = os.path.dirname(new_norm)

                    logger.info(f"[同步] 查找内存条目: {old_norm}")

                    # 更新 all_results
                    hit_all = False
                    for item in self.app.all_results:
                        if os.path.normpath(item.get("fullpath", "")) == old_norm:
                            item["fullpath"] = new_norm
                            item["filename"] = new_name
                            item["dir_path"] = new_dir
                            hit_all = True
                            logger.info(f"[同步] all_results 命中并更新: {old_norm} -> {new_name}")
                            break
                    if not hit_all:
                        logger.warning(f"[同步] all_results 未找到: {old_norm}")

                    # 更新 filtered_results
                    hit_filtered = False
                    for item in self.app.filtered_results:
                        if os.path.normpath(item.get("fullpath", "")) == old_norm:
                            item["fullpath"] = new_norm
                            item["filename"] = new_name
                            item["dir_path"] = new_dir
                            hit_filtered = True
                            logger.info(f"[同步] filtered_results 命中并更新: {old_norm} -> {new_name}")
                            break
                    if not hit_filtered:
                        logger.warning(f"[同步] filtered_results 未找到: {old_norm}")

                    # 更新 shown_paths
                    if hasattr(self.app, "shown_paths"):
                        self.app.shown_paths.discard(old_norm)
                        self.app.shown_paths.add(new_norm)

                # 重置当前页到第一页（避免页码越界）
                self.app.current_page = 1

        # 3) 刷新主窗口
        try:
            self.app._render_page()
            logger.info("[同步] 已调用 _render_page() 刷新界面")
        except Exception as e:
            logger.error(f"[同步] 刷新界面失败: {e}")

        # 4) 提示结果
        self.app.status.set(f"批量重命名完成：成功 {success}，跳过 {skipped}，失败 {failed}")
        messagebox.showinfo("完成", f"重命名完成：成功 {success}，跳过 {skipped}，失败 {failed}", parent=self.dialog)
        self.dialog.destroy()
        # ==================== 主程序 ====================
class SearchApp:
    def __init__(self, root, db_path=None):
        self.root = root
        self.config_mgr = ConfigManager()
        
        # 应用保存的主题
        saved_theme = self.config_mgr.get_theme()
        self.style = ttk.Style(saved_theme)
        self.style.configure("Treeview.Heading", font=("微软雅黑", 10, "bold"), 
                            background='#4CAF50', foreground='white', borderwidth=2, relief="groove")
        self.style.map("Treeview.Heading", background=[('active', '#45a049')], relief=[('active', 'groove')])
        self.style.configure("Treeview", font=("微软雅黑", 9), rowheight=26)
        
        self.root.title("🚀 极速文件搜索 V42 增强版")
        self.root.geometry("1400x900")
        
        # 初始化变量
        self.result_queue = queue.Queue()
        self.results_lock = threading.Lock()
        self.is_searching = False
        self.is_paused = False
        self.stop_event = False
        self.total_found = 0
        self.current_search_id = 0
        self.all_results = []
        self.filtered_results = []
        self.page_size = 1000
        self.current_page = 1
        self.total_pages = 1
        self.item_meta = {}
        self.start_time = 0.0
        self.last_search_params = None
        self.force_realtime = tk.BooleanVar(value=False)
        self.fuzzy_var = tk.BooleanVar(value=True)
        self.regex_var = tk.BooleanVar(value=False)
        self.shown_paths = set()
        self.last_render_time = 0
        self.render_interval = 0.15
        
        # 搜索进度统计
        self.search_stats = {'scanned_dirs': 0, 'start_time': 0}
        
        # 磁盘筛选联动相关
        self.last_search_scope = None  # 记录上次搜索的范围
        self.full_search_results = []  # 全盘搜索结果缓存
        
        # 索引管理器（传入config_mgr）
        self.index_mgr = IndexManager(db_path=db_path, config_mgr=self.config_mgr)
        self.file_watcher = FileWatcher(self.index_mgr, config_mgr=self.config_mgr)
        self.index_build_stop = False
        
        # 托盘和热键管理器
        self.tray_mgr = TrayManager(self)
        self.hotkey_mgr = HotkeyManager(self)
        self.mini_search = MiniSearchWindow(self) 
        
        self._build_menubar()
        self._build_ui()
        self._bind_shortcuts()
        
        # 启动托盘和热键
        self._init_tray_and_hotkey()
        
        self.root.after(100, self.process_queue)
        self.root.after(500, self._check_index)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _init_tray_and_hotkey(self):
        """初始化托盘和全局热键"""
        # 启动托盘
        if self.config_mgr.get_tray_enabled() and HAS_TRAY:
            self.tray_mgr.start()
        
        # 启动全局热键
        if self.config_mgr.get_hotkey_enabled() and HAS_WIN32:
            self.hotkey_mgr.start()

    def _build_menubar(self):
        """构建菜单栏"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # 文件菜单
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件(F)", menu=file_menu, underline=3)
        file_menu.add_command(label="📤 导出结果", command=self.export_results, accelerator="Ctrl+E")
        file_menu.add_separator()
        file_menu.add_command(label="📂 打开文件", command=self.open_file, accelerator="Enter")
        file_menu.add_command(label="🎯 定位文件", command=self.open_folder, accelerator="Ctrl+L")
        file_menu.add_separator()
        file_menu.add_command(label="🚪 退出", command=self._do_quit, accelerator="Alt+F4")
        
        # 编辑菜单
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="编辑(E)", menu=edit_menu, underline=3)
        edit_menu.add_command(label="✅ 全选", command=self.select_all, accelerator="Ctrl+A")
        edit_menu.add_separator()
        edit_menu.add_command(label="📋 复制路径", command=self.copy_path, accelerator="Ctrl+C")
        edit_menu.add_command(label="📄 复制文件", command=self.copy_file, accelerator="Ctrl+Shift+C")
        edit_menu.add_separator()
        edit_menu.add_command(label="🗑️ 删除", command=self.delete_file, accelerator="Delete")
        
        # 搜索菜单
        search_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="搜索(S)", menu=search_menu, underline=3)
        search_menu.add_command(label="🔍 开始搜索", command=self.start_search, accelerator="Enter")
        search_menu.add_command(label="🔄 刷新搜索", command=self.refresh_search, accelerator="F5")
        search_menu.add_command(label="⏹ 停止搜索", command=self.stop_search, accelerator="Escape")
        search_menu.add_separator()
        search_menu.add_checkbutton(label="模糊搜索", variable=self.fuzzy_var)
        search_menu.add_checkbutton(label="正则表达式", variable=self.regex_var)
        
        # 工具菜单
        tool_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="工具(T)", menu=tool_menu, underline=3)
        tool_menu.add_command(label="📊 大文件扫描", command=self.scan_large_files, accelerator="Ctrl+G")
        tool_menu.add_command(label="✏ 批量重命名", command=self._show_batch_rename)
        tool_menu.add_command(label="🔍 查找重复文件", command=self.find_duplicates)
        tool_menu.add_command(label="📁 查找空文件夹", command=self.find_empty_folders)
        tool_menu.add_separator()
        tool_menu.add_command(label="🔧 索引管理", command=self._show_index_mgr)
        tool_menu.add_command(label="🔄 重建索引", command=self._build_index)
        tool_menu.add_separator()
        tool_menu.add_command(label="⚙️ 设置", command=self._show_settings)
        
        # 收藏菜单
        self.fav_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="收藏(B)", menu=self.fav_menu, underline=3)
        self._update_favorites_menu()
        
        # 帮助菜单
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助(H)", menu=help_menu, underline=3)
        help_menu.add_command(label="⌨️ 快捷键列表", command=self._show_shortcuts)
        help_menu.add_separator()
        help_menu.add_command(label="ℹ️ 关于", command=self._show_about)

    def _update_favorites_menu(self):
        """更新收藏夹菜单"""
        self.fav_menu.delete(0, tk.END)
        self.fav_menu.add_command(label="⭐ 收藏当前目录", command=self._add_current_to_favorites)
        self.fav_menu.add_command(label="📂 管理收藏夹", command=self._manage_favorites)
        self.fav_menu.add_separator()
        
        favorites = self.config_mgr.get_favorites()
        if favorites:
            for fav in favorites:
                self.fav_menu.add_command(
                    label=f"📁 {fav['name']}", 
                    command=lambda p=fav['path']: self._goto_favorite(p)
                )
        else:
            self.fav_menu.add_command(label="(无收藏)", state="disabled")

    def _build_ui(self):
        # ==================== 头部区域 ====================
        header = ttk.Frame(self.root, padding=15)
        header.pack(fill=X, padx=10, pady=10)
        
        # Row0: 标题、状态、工具按钮
        row0 = ttk.Frame(header)
        row0.pack(fill=X, pady=(0, 10))
        
        ttk.Label(row0, text="⚡ 极速搜 V42", font=("微软雅黑", 18, "bold"), foreground='#4CAF50').pack(side=LEFT)
        ttk.Label(row0, text="🎯 增强版", font=("微软雅黑", 10), foreground='#FF9800').pack(side=LEFT, padx=10)
        self.idx_lbl = ttk.Label(row0, text="检查中...", font=("微软雅黑", 9))
        self.idx_lbl.pack(side=LEFT, padx=20)
        
        # 右侧工具栏（从右到左排列）
        ttk.Button(row0, text="🔧 索引管理", command=self._show_index_mgr, bootstyle="info-outline", width=12).pack(side=RIGHT, padx=2)
        ttk.Button(row0, text="📤 导出", command=self.export_results, bootstyle="info-outline", width=8).pack(side=RIGHT, padx=2)
        ttk.Button(row0, text="📊 大文件", command=self.scan_large_files, bootstyle="info-outline", width=9).pack(side=RIGHT, padx=2)
        
        # 主题下拉框
        self.theme_var = tk.StringVar(value=self.config_mgr.get_theme())
        self.combo_theme = ttk.Combobox(row0, textvariable=self.theme_var, state="readonly", width=10,
                                         values=["flatly", "darkly", "solar", "superhero", "cyborg", "vapor"])
        self.combo_theme.pack(side=RIGHT, padx=2)
        self.combo_theme.bind('<<ComboboxSelected>>', self._on_theme_change)
        ttk.Label(row0, text="主题:", font=("微软雅黑", 9)).pack(side=RIGHT, padx=(10, 2))
        
        # C盘目录设置按钮（新增）
        ttk.Button(row0, text="📂 C盘目录", command=self._show_c_drive_settings, bootstyle="warning-outline", width=10).pack(side=RIGHT, padx=2)
        ttk.Button(
            row0,
            text="✏ 批量重命名",
            command=self._show_batch_rename,
            bootstyle="secondary-outline",
            width=12
        ).pack(side=RIGHT, padx=2)
        
        ttk.Button(row0, text="🔄 刷新状态", command=self.refresh_index_status, bootstyle="info-outline", width=10).pack(side=RIGHT, padx=2)
        
        # Row1: 搜索栏
        row1 = ttk.Frame(header)
        row1.pack(fill=X, pady=(0, 8))
        
        # 收藏夹快捷下拉
        self.fav_combo_var = tk.StringVar(value="⭐ 收藏夹")
        self.combo_fav = ttk.Combobox(row1, textvariable=self.fav_combo_var, state="readonly", width=10)
        self._update_fav_combo()
        self.combo_fav.pack(side=LEFT, padx=(0, 5))
        self.combo_fav.bind('<<ComboboxSelected>>', self._on_fav_combo_select)
        self.combo_fav.bind('<Button-1>', lambda e: self._update_fav_combo())
        
        self.scope_var = tk.StringVar(value="所有磁盘 (全盘)")
        self.combo_scope = ttk.Combobox(row1, textvariable=self.scope_var, state="readonly", width=18, font=("微软雅黑", 9))
        self._update_drives()
        self.combo_scope.pack(side=LEFT, padx=(0, 5))
        # 绑定磁盘选择事件（新增）
        self.combo_scope.bind('<<ComboboxSelected>>', self._on_scope_change)
        
        ttk.Button(row1, text="📂 选择目录", command=self._browse, bootstyle="secondary", width=10).pack(side=LEFT, padx=(0, 15))
        
        self.kw_var = tk.StringVar()
        self.entry_kw = ttk.Entry(row1, textvariable=self.kw_var, font=("微软雅黑", 12), width=40)
        self.entry_kw.pack(side=LEFT, fill=X, expand=True, padx=(0, 10))
        self.entry_kw.bind('<Return>', lambda e: self.start_search())
        self.entry_kw.bind('<Button-3>', self._show_entry_menu)
        self.entry_kw.focus()
        
        # 搜索选项
        ttk.Checkbutton(row1, text="模糊", variable=self.fuzzy_var, bootstyle="round-toggle").pack(side=LEFT, padx=(0, 3))
        ttk.Checkbutton(row1, text="正则", variable=self.regex_var, bootstyle="round-toggle").pack(side=LEFT, padx=(0, 5))
        ttk.Checkbutton(row1, text="实时", variable=self.force_realtime, bootstyle="round-toggle").pack(side=LEFT, padx=(0, 10))
        
        self.btn_search = ttk.Button(row1, text="🚀 搜索", command=self.start_search, bootstyle="primary", width=10)
        self.btn_search.pack(side=LEFT, padx=2)
        self.btn_refresh = ttk.Button(row1, text="🔄 刷新", command=self.refresh_search, bootstyle="info", width=8, state="disabled")
        self.btn_refresh.pack(side=LEFT, padx=2)
        self.btn_pause = ttk.Button(row1, text="⏸ 暂停", command=self.toggle_pause, bootstyle="warning", width=8, state="disabled")
        self.btn_pause.pack(side=LEFT, padx=2)
        self.btn_stop = ttk.Button(row1, text="⏹ 停止", command=self.stop_search, bootstyle="danger", width=8, state="disabled")
        self.btn_stop.pack(side=LEFT, padx=2)
        
        # Row2: 筛选栏
        row2 = ttk.Frame(header)
        row2.pack(fill=X)
        
        ttk.Label(row2, text="筛选:", font=("微软雅黑", 9)).pack(side=LEFT)
        ttk.Label(row2, text="格式", font=("微软雅黑", 9)).pack(side=LEFT, padx=(10, 2))
        self.ext_var = tk.StringVar(value="全部")
        self.combo_ext = ttk.Combobox(row2, textvariable=self.ext_var, state="readonly", width=15, values=["全部"])
        self.combo_ext.pack(side=LEFT, padx=(0, 15))
        self.combo_ext.bind('<<ComboboxSelected>>', lambda e: self._apply_filter())
        
        ttk.Label(row2, text="大小", font=("微软雅黑", 9)).pack(side=LEFT, padx=(0, 2))
        self.size_var = tk.StringVar(value="不限")
        self.combo_size = ttk.Combobox(row2, textvariable=self.size_var, state="readonly", width=10, 
                                        values=["不限", ">1MB", ">10MB", ">100MB", ">500MB", ">1GB"])
        self.combo_size.pack(side=LEFT, padx=(0, 15))
        self.combo_size.bind('<<ComboboxSelected>>', lambda e: self._apply_filter())
        
        ttk.Label(row2, text="时间", font=("微软雅黑", 9)).pack(side=LEFT, padx=(0, 2))
        self.date_var = tk.StringVar(value="不限")
        self.combo_date = ttk.Combobox(row2, textvariable=self.date_var, state="readonly", width=10, 
                                        values=["不限", "今天", "3天内", "7天内", "30天内", "今年"])
        self.combo_date.pack(side=LEFT, padx=(0, 15))
        self.combo_date.bind('<<ComboboxSelected>>', lambda e: self._apply_filter())
        
        ttk.Button(row2, text="清除", bootstyle="secondary-outline", width=6, command=self._clear_filter).pack(side=LEFT)
        
        self.lbl_filter = ttk.Label(row2, text="", font=("微软雅黑", 9), foreground="#666")
        self.lbl_filter.pack(side=RIGHT, padx=10)
        
        # ==================== 结果区域 ====================
        body = ttk.Frame(self.root, padding=(10, 0))
        body.pack(fill=BOTH, expand=True)
        
        columns = ("filename", "path", "size", "mtime")
        self.tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="extended")
        for col, text, w in zip(columns, ["📄 文件名", "📂 所在目录", "📊 类型/大小", "🕒 修改时间"], [400, 400, 130, 150]):
            self.tree.heading(col, text=text, command=lambda c=col: self.sort_column(c, False))
            self.tree.column(col, width=w, anchor="w" if col in ("filename", "path") else "center")
        
        sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=sb.set)
        sb.pack(side=RIGHT, fill=Y)
        self.tree.pack(fill=BOTH, expand=True)
        self.tree.tag_configure('odd', background='white')
        self.tree.tag_configure('even', background='#f8f9fa')
        self.tree.bind("<Double-1>", self.on_dblclick)
        self.tree.bind("<Button-3>", self.show_menu)
        self.tree.bind("<Return>", lambda e: self.open_file())
        self.tree.bind("<space>", lambda e: self.preview_file())
        
        # ==================== 分页栏 ====================
        pg = ttk.Frame(body, padding=5)
        pg.pack(fill=X, side=BOTTOM)
        pg_ctr = ttk.Frame(pg)
        pg_ctr.pack(anchor=CENTER)
        self.btn_first = ttk.Button(pg_ctr, text="⏮", command=lambda: self.go_page('first'), bootstyle="link", state="disabled")
        self.btn_first.pack(side=LEFT)
        self.btn_prev = ttk.Button(pg_ctr, text="◀", command=lambda: self.go_page('prev'), bootstyle="link", state="disabled")
        self.btn_prev.pack(side=LEFT)
        self.lbl_page = ttk.Label(pg_ctr, text="第 1/1 页 (0项)", font=("微软雅黑", 9))
        self.lbl_page.pack(side=LEFT, padx=15)
        self.btn_next = ttk.Button(pg_ctr, text="▶", command=lambda: self.go_page('next'), bootstyle="link", state="disabled")
        self.btn_next.pack(side=LEFT)
        self.btn_last = ttk.Button(pg_ctr, text="⏭", command=lambda: self.go_page('last'), bootstyle="link", state="disabled")
        self.btn_last.pack(side=LEFT)
        
        # ==================== 状态栏 ====================
        btm = ttk.Frame(self.root, padding=5)
        btm.pack(side=BOTTOM, fill=X)
        self.status = tk.StringVar(value="就绪")
        ttk.Label(btm, textvariable=self.status, font=("微软雅黑", 9)).pack(side=LEFT, padx=10)
        self.status_path = tk.StringVar()
        ttk.Label(btm, textvariable=self.status_path, font=("Consolas", 8), foreground="#718096").pack(side=LEFT, fill=X, expand=True)
        self.progress = ttk.Progressbar(btm, mode='indeterminate', bootstyle="success", length=200)
        
        # ==================== 右键菜单 ====================
        self.ctx_menu = tk.Menu(self.root, tearoff=0)
        self.ctx_menu.add_command(label="📂 打开文件", command=self.open_file)
        self.ctx_menu.add_command(label="🎯 定位文件", command=self.open_folder)
        self.ctx_menu.add_command(label="👁️ 预览文件", command=self.preview_file)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="📄 复制文件", command=self.copy_file)
        self.ctx_menu.add_command(label="📝 复制路径", command=self.copy_path)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="🗑️ 删除", command=self.delete_file)
        
        # 搜索框右键菜单
        self.entry_menu = tk.Menu(self.root, tearoff=0)
        self.entry_menu.add_command(label="剪切(X)", command=self._entry_cut)
        self.entry_menu.add_command(label="复制(C)", command=self._entry_copy)
        self.entry_menu.add_command(label="粘贴(V)", command=self._entry_paste)
        self.entry_menu.add_command(label="全选(A)", command=self._entry_select_all)
        self.entry_menu.add_separator()
        self.entry_menu.add_command(label="清空", command=lambda: self.kw_var.set(""))
        self.entry_menu.add_separator()
        self.history_menu = tk.Menu(self.entry_menu, tearoff=0)
        self.entry_menu.add_cascade(label="📜 搜索历史", menu=self.history_menu)

    def _bind_shortcuts(self):
        """绑定快捷键"""
        self.root.bind('<Control-f>', lambda e: self.entry_kw.focus())
        self.root.bind('<Control-F>', lambda e: self.entry_kw.focus())
        self.root.bind('<Escape>', lambda e: self.stop_search() if self.is_searching else self.kw_var.set(""))
        self.root.bind('<Delete>', lambda e: self.delete_file())
        self.root.bind('<F5>', lambda e: self.refresh_search())
        self.root.bind('<Control-a>', lambda e: self.select_all())
        self.root.bind('<Control-A>', lambda e: self.select_all())
        self.root.bind('<Control-c>', lambda e: self.copy_path())
        self.root.bind('<Control-C>', lambda e: self.copy_path())
        self.root.bind('<Control-Shift-c>', lambda e: self.copy_file())
        self.root.bind('<Control-Shift-C>', lambda e: self.copy_file())
        self.root.bind('<Control-e>', lambda e: self.export_results())
        self.root.bind('<Control-E>', lambda e: self.export_results())
        self.root.bind('<Control-g>', lambda e: self.scan_large_files())
        self.root.bind('<Control-G>', lambda e: self.scan_large_files())
        self.root.bind('<Control-l>', lambda e: self.open_folder())
        self.root.bind('<Control-L>', lambda e: self.open_folder())
        # 搜索框按下键跳到结果区
        self.entry_kw.bind('<Down>', self._focus_to_tree)

    def _focus_to_tree(self, event=None):
        """从搜索框跳到结果列表"""
        children = self.tree.get_children()
        if children:
            self.tree.focus(children[0])
            self.tree.selection_set(children[0])
            self.tree.focus_set()
        return "break"

    # ==================== C盘目录设置 ====================
    def _show_c_drive_settings(self):
        """显示C盘目录设置对话框"""
        dialog = CDriveSettingsDialog(
            self.root, 
            self.config_mgr, 
            self.index_mgr,
            self._rebuild_c_drive
        )
        dialog.show()

    def _rebuild_c_drive(self, drive_letter="C"):
        """重建指定盘符的索引"""
        if self.index_mgr.is_building:
            messagebox.showwarning("提示", "索引正在构建中，请稍后")
            return
        
        self.index_build_stop = False
        
        def run():
            self.index_mgr.rebuild_drive(
                drive_letter,
                lambda c, p: self.result_queue.put(("IDX_PROG", (c, p))),
                lambda: self.index_build_stop
            )
            self.result_queue.put(("IDX_DONE", None))
        
        threading.Thread(target=run, daemon=True).start()
        self._check_index()
        self.status.set(f"🔄 正在重建 {drive_letter}: 盘索引...")
    # ==================== 磁盘筛选联动 ====================
    def _on_scope_change(self, event=None):
        """磁盘选择变化时的处理"""
        if not self.kw_var.get().strip():
            # 没有关键词，不做任何操作
            return
        
        if self.is_searching:
            # 正在搜索中，不处理
            return
        
        current_scope = self.scope_var.get()
        
        # 如果上次是全盘搜索，且有缓存结果
        if self.last_search_scope == "所有磁盘 (全盘)" and self.full_search_results:
            if "所有磁盘" in current_scope:
                # 切换回全盘，恢复完整结果
                with self.results_lock:
                    self.all_results = list(self.full_search_results)
                    self.filtered_results = list(self.all_results)
                self._apply_filter()
                self.status.set(f"✅ 显示全部结果: {len(self.filtered_results)}项")
            else:
                # 切换到具体磁盘，从缓存中筛选
                self._filter_by_drive(current_scope)
        else:
            # 上次不是全盘搜索，或没有缓存，需要重新搜索
            self.start_search()
    
    def _filter_by_drive(self, drive_path):
        """从已有结果中筛选指定磁盘的文件"""
        if not self.full_search_results:
            return
        
        drive_letter = drive_path.rstrip('\\').upper()
        
        with self.results_lock:
            self.all_results = []
            for item in self.full_search_results:
                item_drive = item['fullpath'][:2].upper()
                if item_drive == drive_letter[:2]:
                    self.all_results.append(item)
            self.filtered_results = list(self.all_results)
        
        self._apply_filter()
        self.status.set(f"✅ 筛选 {drive_letter}: {len(self.filtered_results)}项")
        self.lbl_filter.config(text=f"磁盘筛选: {len(self.filtered_results)}/{len(self.full_search_results)}")

    # ==================== 主题切换 ====================
    def _on_theme_change(self, event=None):
        """主题即时切换"""
        theme = self.theme_var.get()
        try:
            self.style.theme_use(theme)
            self.config_mgr.set_theme(theme)
            
            # 重新配置 Treeview 样式
            self.style.configure("Treeview.Heading", font=("微软雅黑", 10, "bold"), 
                                background='#4CAF50', foreground='white', borderwidth=2, relief="groove")
            self.style.map("Treeview.Heading", background=[('active', '#45a049')], relief=[('active', 'groove')])
            self.style.configure("Treeview", font=("微软雅黑", 9), rowheight=26)
            
            self.status.set(f"主题已切换: {theme}")
        except Exception as e:
            logger.error(f"主题切换失败: {e}")
            messagebox.showerror("主题错误", str(e))
            self.theme_var.set("flatly")

    # ==================== 设置对话框 ====================
    def _show_settings(self):
        """显示设置对话框"""
        dlg = tk.Toplevel(self.root)
        dlg.title("⚙️ 设置")
        dlg.geometry("400x300")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.geometry(f"+{self.root.winfo_x() + 150}+{self.root.winfo_y() + 100}")
        
        frame = ttk.Frame(dlg, padding=20)
        frame.pack(fill=BOTH, expand=True)
        
        ttk.Label(frame, text="常规设置", font=("微软雅黑", 12, "bold")).pack(anchor=W, pady=(0, 15))
        
        # 全局热键设置
        hotkey_frame = ttk.Frame(frame)
        hotkey_frame.pack(fill=X, pady=5)
        
        hotkey_var = tk.BooleanVar(value=self.config_mgr.get_hotkey_enabled())
        ttk.Checkbutton(
            hotkey_frame, 
            text="启用全局热键 (Ctrl+Shift+Space)", 
            variable=hotkey_var,
            bootstyle="round-toggle"
        ).pack(side=LEFT)
        
        if not HAS_WIN32:
            ttk.Label(hotkey_frame, text="(需要pywin32)", foreground="gray").pack(side=LEFT, padx=10)
        
        # 托盘设置
        tray_frame = ttk.Frame(frame)
        tray_frame.pack(fill=X, pady=5)
        
        tray_var = tk.BooleanVar(value=self.config_mgr.get_tray_enabled())
        ttk.Checkbutton(
            tray_frame, 
            text="关闭时最小化到托盘", 
            variable=tray_var,
            bootstyle="round-toggle"
        ).pack(side=LEFT)
        
        if not HAS_TRAY:
            ttk.Label(tray_frame, text="(需要pystray和PIL)", foreground="gray").pack(side=LEFT, padx=10)
        
        ttk.Separator(frame).pack(fill=X, pady=15)
        
        # 提示信息
        ttk.Label(
            frame, 
            text="💡 提示：修改设置后需要重启程序才能完全生效",
            font=("微软雅黑", 9),
            foreground="#888"
        ).pack(anchor=W)
        
        # 按钮
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=X, pady=(20, 0))
        
        def save_settings():
            self.config_mgr.set_hotkey_enabled(hotkey_var.get())
            self.config_mgr.set_tray_enabled(tray_var.get())
            
            # 动态启用/禁用热键
            if hotkey_var.get() and not self.hotkey_mgr.registered and HAS_WIN32:
                self.hotkey_mgr.start()
            elif not hotkey_var.get() and self.hotkey_mgr.registered:
                self.hotkey_mgr.stop()
            
            messagebox.showinfo("成功", "设置已保存", parent=dlg)
            dlg.destroy()
        
        ttk.Button(btn_frame, text="保存", command=save_settings, bootstyle="success", width=10).pack(side=RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text="取消", command=dlg.destroy, bootstyle="secondary", width=10).pack(side=RIGHT)

    # ==================== 收藏夹功能 ====================
    def _update_fav_combo(self):
        """更新收藏夹下拉框内容"""
        favorites = self.config_mgr.get_favorites()
        if favorites:
            values = ["⭐ 收藏夹"] + [f"📁 {fav['name']}" for fav in favorites]
        else:
            values = ["⭐ 收藏夹", "(无收藏)"]
        self.combo_fav['values'] = values
        self.fav_combo_var.set("⭐ 收藏夹")

    def _on_fav_combo_select(self, event):
        """收藏夹下拉选择"""
        sel = self.fav_combo_var.get()
        if sel == "⭐ 收藏夹" or sel == "(无收藏)":
            self.fav_combo_var.set("⭐ 收藏夹")
            return
        
        name = sel.replace("📁 ", "")
        favorites = self.config_mgr.get_favorites()
        for fav in favorites:
            if fav['name'] == name:
                if os.path.exists(fav['path']):
                    self.scope_var.set(fav['path'])
                else:
                    messagebox.showwarning("警告", f"目录不存在: {fav['path']}")
                break
        
        self.root.after(100, lambda: self.fav_combo_var.set("⭐ 收藏夹"))

    def _add_current_to_favorites(self):
        """添加当前目录到收藏夹"""
        scope = self.scope_var.get()
        if "所有磁盘" in scope:
            messagebox.showinfo("提示", "请先选择一个具体目录")
            return
        self.config_mgr.add_favorite(scope)
        self._update_favorites_menu()
        self._update_fav_combo()
        messagebox.showinfo("成功", f"已收藏: {scope}")

    def _goto_favorite(self, path):
        """跳转到收藏目录"""
        if os.path.exists(path):
            self.scope_var.set(path)
        else:
            messagebox.showwarning("警告", f"目录不存在: {path}")

    def _manage_favorites(self):
        """管理收藏夹对话框"""
        dlg = tk.Toplevel(self.root)
        dlg.title("📂 管理收藏夹")
        dlg.geometry("500x400")
        dlg.transient(self.root)
        dlg.grab_set()
        
        frame = ttk.Frame(dlg, padding=15)
        frame.pack(fill=BOTH, expand=True)
        
        ttk.Label(frame, text="收藏夹列表", font=("微软雅黑", 11, "bold")).pack(anchor=W)
        
        listbox_frame = ttk.Frame(frame)
        listbox_frame.pack(fill=BOTH, expand=True, pady=10)
        
        listbox = tk.Listbox(listbox_frame, font=("微软雅黑", 10), selectmode=tk.SINGLE)
        scrollbar = ttk.Scrollbar(listbox_frame, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=RIGHT, fill=Y)
        listbox.pack(fill=BOTH, expand=True)
        
        def refresh_list():
            listbox.delete(0, tk.END)
            for fav in self.config_mgr.get_favorites():
                listbox.insert(tk.END, f"{fav['name']} - {fav['path']}")
        
        refresh_list()
        
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=X)
        
        def remove_selected():
            sel = listbox.curselection()
            if sel:
                favs = self.config_mgr.get_favorites()
                if sel[0] < len(favs):
                    self.config_mgr.remove_favorite(favs[sel[0]]['path'])
                    refresh_list()
                    self._update_favorites_menu()
                    self._update_fav_combo()
        
        ttk.Button(btn_frame, text="删除选中", command=remove_selected, bootstyle="danger-outline").pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="关闭", command=dlg.destroy, bootstyle="secondary").pack(side=RIGHT, padx=5)
            # ==================== 辅助方法 ====================
    def _show_entry_menu(self, event):
        self.history_menu.delete(0, tk.END)
        history = self.config_mgr.get_history()
        if history:
            for kw in history[:15]:
                self.history_menu.add_command(label=kw, command=lambda k=kw: (self.kw_var.set(k), self.start_search()))
            self.history_menu.add_separator()
            self.history_menu.add_command(label="清除历史", command=self._clear_history)
        else:
            self.history_menu.add_command(label="(无历史记录)", state="disabled")
        self.entry_menu.post(event.x_root, event.y_root)

    def _entry_cut(self):
        try:
            self.entry_kw.event_generate("<<Cut>>")
        except tk.TclError as e:
            logger.debug(f"剪切失败: {e}")

    def _entry_copy(self):
        try:
            self.entry_kw.event_generate("<<Copy>>")
        except tk.TclError as e:
            logger.debug(f"复制失败: {e}")

    def _entry_paste(self):
        try:
            self.entry_kw.event_generate("<<Paste>>")
        except tk.TclError as e:
            logger.debug(f"粘贴失败: {e}")

    def _entry_select_all(self):
        self.entry_kw.select_range(0, tk.END)
        self.entry_kw.icursor(tk.END)

    def _clear_history(self):
        self.config_mgr.config["search_history"] = []
        self.config_mgr.save()

    def _update_drives(self):
        if platform.system() == 'Windows':
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            self.combo_scope['values'] = ["所有磁盘 (全盘)"] + drives
        else:
            self.combo_scope['values'] = ["所有磁盘 (全盘)", "/"]
        self.combo_scope.current(0)

    def _browse(self):
        d = filedialog.askdirectory()
        if d:
            self.combo_scope.set(d)

    def _get_drives(self):
        if platform.system() == 'Windows':
            return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        return ["/"]

    def _get_search_scope_targets(self):
        return parse_search_scope(self.scope_var.get(), self._get_drives, self.config_mgr)

    def _show_shortcuts(self):
        """显示快捷键列表"""
        shortcuts = """
快捷键列表:

搜索操作:
  Ctrl+F      聚焦搜索框
  Enter       开始搜索
  F5          刷新搜索
  Escape      停止搜索/清空

文件操作:
  Enter       打开选中文件
  Ctrl+O      打开文件
  Ctrl+L      定位文件
  Delete      删除文件
  Space       预览文件

编辑操作:
  Ctrl+A      全选
  Ctrl+C      复制路径
  Ctrl+Shift+C  复制文件

工具:
  Ctrl+E      导出结果
  Ctrl+G      大文件扫描

全局热键:
  Ctrl+Shift+Space  迷你搜索窗口
  Ctrl+Shift+Tab    主窗口
        """
        
        dlg = tk.Toplevel(self.root)
        dlg.title("⌨️ 快捷键列表")
        dlg.geometry("350x480")
        dlg.transient(self.root)
        
        text = tk.Text(dlg, font=("Consolas", 10), wrap=tk.WORD, padx=15, pady=15)
        text.pack(fill=BOTH, expand=True)
        text.insert("1.0", shortcuts)
        text.config(state="disabled")

    def _show_about(self):
        """显示关于对话框"""
        messagebox.showinfo("关于", 
            "🚀 极速文件搜索 V42 增强版\n\n"
            "功能特性:\n"
            "• MFT极速索引\n"
            "• FTS5全文搜索\n"
            "• 模糊/正则搜索\n"
            "• 实时文件监控\n"
            "• 收藏夹管理\n"
            "• 多主题支持\n"
            "• 全局热键呼出\n"
            "• 系统托盘常驻\n"
            "• C盘目录自定义\n\n"
            "© 2024"
        )

    # ==================== 筛选功能 ====================
    def _update_ext_combo(self):
        counts = {}
        with self.results_lock:
            for item in self.all_results:
                if item['type_code'] == 0:
                    ext = "📂文件夹"
                elif item['type_code'] == 1:
                    ext = "📦压缩包"
                else:
                    ext = os.path.splitext(item['filename'])[1].lower() or "(无)"
                counts[ext] = counts.get(ext, 0) + 1
        values = ["全部"] + [f"{ext} ({cnt})" for ext, cnt in sorted(counts.items(), key=lambda x: -x[1])[:30]]
        self.combo_ext['values'] = values

    def _get_size_min(self):
        return {"不限": 0, ">1MB": 1<<20, ">10MB": 10<<20, ">100MB": 100<<20, ">500MB": 500<<20, ">1GB": 1<<30}.get(self.size_var.get(), 0)

    def _get_date_min(self):
        """获取日期筛选的最小时间戳"""
        now = time.time()
        day = 86400
        mapping = {
            "不限": 0,
            "今天": now - day,
            "3天内": now - 3 * day,
            "7天内": now - 7 * day,
            "30天内": now - 30 * day,
            "今年": time.mktime(datetime.datetime(datetime.datetime.now().year, 1, 1).timetuple())
        }
        return mapping.get(self.date_var.get(), 0)

    def _apply_filter(self):
        ext_sel = self.ext_var.get()
        size_min = self._get_size_min()
        date_min = self._get_date_min()
        target_ext = ext_sel.split(" (")[0] if ext_sel != "全部" else None
        
        with self.results_lock:
            self.filtered_results = []
            for item in self.all_results:
                if size_min > 0 and item['type_code'] == 2 and item['size'] < size_min:
                    continue
                if date_min > 0 and item['mtime'] < date_min:
                    continue
                if target_ext:
                    if item['type_code'] == 0:
                        item_ext = "📂文件夹"
                    elif item['type_code'] == 1:
                        item_ext = "📦压缩包"
                    else:
                        item_ext = os.path.splitext(item['filename'])[1].lower() or "(无)"
                    if item_ext != target_ext:
                        continue
                self.filtered_results.append(item)
        
        self.current_page = 1
        self._render_page()
        with self.results_lock:
            all_count = len(self.all_results)
            filtered_count = len(self.filtered_results)
        
        # 更新筛选提示（保留磁盘筛选信息）
        current_filter_text = self.lbl_filter.cget("text")
        if "磁盘筛选" in current_filter_text:
            base_text = current_filter_text.split(" | ")[0]
            if ext_sel != "全部" or size_min > 0 or date_min > 0:
                self.lbl_filter.config(text=f"{base_text} | 筛选: {filtered_count}/{all_count}")
            else:
                self.lbl_filter.config(text=base_text)
        else:
            if ext_sel != "全部" or size_min > 0 or date_min > 0:
                self.lbl_filter.config(text=f"筛选: {filtered_count}/{all_count}")
            else:
                self.lbl_filter.config(text="")

    def _clear_filter(self):
        self.ext_var.set("全部")
        self.size_var.set("不限")
        self.date_var.set("不限")
        with self.results_lock:
            self.filtered_results = list(self.all_results)
        self.current_page = 1
        self._render_page()
        
        # 保留磁盘筛选信息
        current_filter_text = self.lbl_filter.cget("text")
        if "磁盘筛选" in current_filter_text:
            base_text = current_filter_text.split(" | ")[0]
            self.lbl_filter.config(text=base_text)
        else:
            self.lbl_filter.config(text="")

    # ==================== 分页功能 ====================
    def _update_page_info(self):
        total = len(self.filtered_results)
        self.total_pages = max(1, math.ceil(total / self.page_size))
        self.lbl_page.config(text=f"第 {self.current_page}/{self.total_pages} 页 ({total}项)")
        self.btn_first.config(state="normal" if self.current_page > 1 else "disabled")
        self.btn_prev.config(state="normal" if self.current_page > 1 else "disabled")
        self.btn_next.config(state="normal" if self.current_page < self.total_pages else "disabled")
        self.btn_last.config(state="normal" if self.current_page < self.total_pages else "disabled")

    def go_page(self, action):
        if action == 'first':
            self.current_page = 1
        elif action == 'prev' and self.current_page > 1:
            self.current_page -= 1
        elif action == 'next' and self.current_page < self.total_pages:
            self.current_page += 1
        elif action == 'last':
            self.current_page = self.total_pages
        self._render_page()

    def _render_page(self):
        self.tree.delete(*self.tree.get_children())
        self.item_meta.clear()
        self._update_page_info()
        start = (self.current_page - 1) * self.page_size
        for i, item in enumerate(self.filtered_results[start:start + self.page_size]):
            iid = self.tree.insert("", "end", values=(item['filename'], item['dir_path'], item['size_str'], item['mtime_str']), tags=('even' if i % 2 else 'odd',))
            self.item_meta[iid] = start + i

    def select_all(self):
        """全选"""
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children)

    # ==================== 文件操作 ====================
    def on_dblclick(self, e):
        sel = self.tree.selection()
        if not sel or sel[0] not in self.item_meta:
            return
        item = self.filtered_results[self.item_meta[sel[0]]]
        if item['type_code'] == 0:
            try:
                subprocess.Popen(f'explorer "{item["fullpath"]}"')
            except (OSError, FileNotFoundError) as e:
                logger.error(f"打开文件夹失败: {e}")
                messagebox.showerror("错误", f"无法打开文件夹: {e}")
        else:
            try:
                os.startfile(item['fullpath'])
            except (OSError, FileNotFoundError) as e:
                logger.error(f"打开文件失败: {e}")
                messagebox.showerror("错误", f"无法打开文件: {e}")

    def show_menu(self, e):
        item = self.tree.identify_row(e.y)
        if item:
            self.tree.selection_set(item)
            self.ctx_menu.post(e.x_root, e.y_root)

    def _get_sel(self):
        sel = self.tree.selection()
        if not sel or sel[0] not in self.item_meta:
            return None
        return self.filtered_results[self.item_meta[sel[0]]]

    def _get_selected_items(self):
        """获取所有选中项"""
        items = []
        for sel in self.tree.selection():
            if sel in self.item_meta:
                items.append(self.filtered_results[self.item_meta[sel]])
        return items

    def open_file(self):
        item = self._get_sel()
        if item:
            try:
                os.startfile(item['fullpath'])
            except (OSError, FileNotFoundError) as e:
                logger.error(f"打开文件失败: {e}")
                messagebox.showerror("错误", f"无法打开文件: {e}")

    def open_folder(self):
        item = self._get_sel()
        if item:
            try:
                subprocess.Popen(f'explorer /select,"{item["fullpath"]}"')
            except (OSError, FileNotFoundError) as e:
                logger.error(f"定位文件失败: {e}")
                messagebox.showerror("错误", f"无法定位文件: {e}")

    def copy_path(self):
        items = self._get_selected_items()
        if items:
            paths = '\n'.join(item['fullpath'] for item in items)
            self.root.clipboard_clear()
            self.root.clipboard_append(paths)
            self.status.set(f"已复制 {len(items)} 个路径")

    def copy_file(self):
        if not HAS_WIN32:
            messagebox.showwarning("提示", "需要安装 pywin32: pip install pywin32")
            return
        items = self._get_selected_items()
        if not items:
            return
        try:
            files = [os.path.abspath(item['fullpath']) for item in items if os.path.exists(item['fullpath'])]
            if not files:
                return
            
            file_str = '\0'.join(files) + '\0\0'
            data = struct.pack('IIIII', 20, 0, 0, 0, 1) + file_str.encode('utf-16le')
            
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
            win32clipboard.CloseClipboard()
            self.status.set(f"已复制 {len(files)} 个文件")
        except Exception as e:
            logger.error(f"复制文件失败: {e}")
            messagebox.showerror("错误", f"复制文件失败: {e}")

    def delete_file(self):
        """删除文件 - 优化版（使用回收站）"""
        items = self._get_selected_items()
        if not items:
            return
        
        if len(items) == 1:
            msg = f"确定删除?\n{items[0]['filename']}"
        else:
            msg = f"确定删除 {len(items)} 个文件/文件夹?"
        
        if HAS_SEND2TRASH:
            msg += "\n\n(将移至回收站)"
        else:
            msg += "\n\n⚠️ 警告：将永久删除！"
        
        if not messagebox.askyesno("确认", msg, icon='warning'):
            return
        
        deleted = 0
        failed = []
        
        for item in items:
            try:
                if HAS_SEND2TRASH:
                    # 使用回收站
                    send2trash.send2trash(item['fullpath'])
                else:
                    # 直接删除
                    if item['type_code'] == 0:
                        shutil.rmtree(item['fullpath'])
                    else:
                        os.remove(item['fullpath'])
                
                with self.results_lock:
                    self.shown_paths.discard(item['fullpath'])
                deleted += 1
            except Exception as e:
                logger.error(f"删除失败: {item['fullpath']} - {e}")
                failed.append(item['filename'])
        
        # 从界面移除已删除的项
        for sel in self.tree.selection():
            self.tree.delete(sel)
        
        if failed:
            self.status.set(f"✅ 已删除 {deleted} 个，失败 {len(failed)} 个")
            messagebox.showwarning("部分失败", f"以下文件删除失败:\n" + "\n".join(failed[:5]))
        else:
            self.status.set(f"✅ 已删除 {deleted} 个文件")

    def preview_file(self):
        """预览文件"""
        item = self._get_sel()
        if not item:
            return
        
        ext = os.path.splitext(item['filename'])[1].lower()
        text_exts = {'.txt', '.log', '.py', '.json', '.xml', '.md', '.csv', '.ini', '.cfg', '.yaml', '.yml', '.js', '.css', '.sql', '.sh', '.bat', '.cmd'}
        
        if ext in text_exts:
            self._preview_text(item['fullpath'])
        elif item['type_code'] == 0:
            try:
                subprocess.Popen(f'explorer "{item["fullpath"]}"')
            except (OSError, FileNotFoundError) as e:
                logger.error(f"打开文件夹失败: {e}")
                messagebox.showerror("错误", f"无法打开文件夹: {e}")
        else:
            try:
                os.startfile(item['fullpath'])
            except (OSError, FileNotFoundError) as e:
                logger.error(f"打开文件失败: {e}")
                messagebox.showerror("错误", f"无法打开文件: {e}")

    def _preview_text(self, path):
        """文本预览窗口"""
        dlg = tk.Toplevel(self.root)
        dlg.title(f"预览: {os.path.basename(path)}")
        dlg.geometry("800x600")
        dlg.transient(self.root)
        
        frame = ttk.Frame(dlg)
        frame.pack(fill=BOTH, expand=True, padx=5, pady=5)
        
        text = tk.Text(frame, wrap=tk.WORD, font=("Consolas", 10))
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=RIGHT, fill=Y)
        text.pack(fill=BOTH, expand=True)
        
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(200000)
            if len(content) >= 200000:
                content += "\n\n... [文件过大，仅显示前200KB] ..."
            text.insert('1.0', content)
            text.config(state='disabled')
        except Exception as e:
            logger.error(f"读取文件失败 {path}: {e}")
            text.insert('1.0', f"无法读取文件: {e}")
            text.config(state='disabled')

    # ==================== 搜索功能 ====================
    def start_search(self):
        if self.is_searching:
            return
        kw = self.kw_var.get().strip()
        if not kw:
            messagebox.showwarning("提示", "请输入关键词")
            return
        
        self.config_mgr.add_history(kw)
        self.tree.delete(*self.tree.get_children())
        
        with self.results_lock:
            self.all_results.clear()
            self.filtered_results.clear()
            self.shown_paths.clear()
        self.item_meta.clear()
        self.total_found = 0
        self.current_search_id += 1
        self.start_time = time.time()
        self.current_page = 1
        self.ext_var.set("全部")
        self.size_var.set("不限")
        self.date_var.set("不限")
        self.combo_ext['values'] = ["全部"]
        self.lbl_filter.config(text="")
        
        # 重置搜索统计
        self.search_stats = {'scanned_dirs': 0, 'start_time': time.time()}
        
        # 记录本次搜索范围
        current_scope = self.scope_var.get()
        self.last_search_scope = current_scope
        
        # 如果不是全盘搜索，清空全盘缓存
        if "所有磁盘" not in current_scope:
            self.full_search_results = []
        
        if self.regex_var.get():
            try:
                re.compile(kw)
                keywords = [kw]
            except re.error as e:
                messagebox.showerror("正则错误", f"正则表达式无效: {e}")
                return
        else:
            keywords = kw.lower().split()
        
        scope_targets = self._get_search_scope_targets()
        self.last_search_params = {
            'keywords': keywords, 
            'scope_targets': scope_targets, 
            'kw': kw,
            'regex': self.regex_var.get(),
            'fuzzy': self.fuzzy_var.get()
        }
        
        use_idx = not self.force_realtime.get() and self.index_mgr.is_ready and not self.index_mgr.is_building
        if use_idx:
            self.status.set("⚡ 索引搜索...")
            self.btn_refresh.config(state="normal")
            threading.Thread(target=self._search_idx, args=(self.current_search_id, keywords, scope_targets), daemon=True).start()
        else:
            self.status.set("🔍 实时扫描...")
            self.is_searching = True
            self.stop_event = False
            self.btn_search.config(state="disabled")
            self.btn_pause.config(state="normal")
            self.btn_stop.config(state="normal")
            self.progress.pack(side=RIGHT, padx=10)
            self.progress.start(10)
            threading.Thread(target=self._search_rt, args=(self.current_search_id, kw, scope_targets), daemon=True).start()

    def refresh_search(self):
        if self.last_search_params and not self.is_searching:
            self.kw_var.set(self.last_search_params['kw'])
            self.start_search()

    def toggle_pause(self):
        if not self.is_searching:
            return
        self.is_paused = not self.is_paused
        self.btn_pause.config(text="▶ 继续" if self.is_paused else "⏸ 暂停", bootstyle="success" if self.is_paused else "warning")
        (self.progress.stop if self.is_paused else lambda: self.progress.start(10))()

    def stop_search(self):
        if not self.is_searching:
            return
        self.stop_event = True
        self.current_search_id += 1
        self._reset_ui()
        self._finalize()
        with self.results_lock:
            count = len(self.all_results)
        self.status.set(f"🛑 已停止 ({count}项)")

    def _reset_ui(self):
        self.is_searching = False
        self.is_paused = False
        self.btn_search.config(state="normal")
        self.btn_pause.config(state="disabled", text="⏸ 暂停", bootstyle="warning")
        self.btn_stop.config(state="disabled")
        self.progress.stop()
        self.progress.pack_forget()

    def _finalize(self):
        self._update_ext_combo()
        with self.results_lock:
            self.filtered_results = list(self.all_results)
            
            # 如果是全盘搜索，缓存结果
            if self.last_search_scope == "所有磁盘 (全盘)":
                self.full_search_results = list(self.all_results)
        
        self._render_page()

    def _match_keyword(self, filename, keywords):
        """匹配关键词（支持模糊和正则）"""
        if self.last_search_params and self.last_search_params.get('regex'):
            try:
                pattern = keywords[0] if keywords else ''
                return re.search(pattern, filename, re.IGNORECASE) is not None
            except re.error:
                return False
        elif self.last_search_params and self.last_search_params.get('fuzzy'):
            filename_lower = filename.lower()
            for kw in keywords:
                if kw in filename_lower:
                    continue
                if fuzzy_match(kw, filename) >= 50:
                    continue
                return False
            return True
        else:
            filename_lower = filename.lower()
            return all(kw in filename_lower for kw in keywords)

    def _search_idx(self, sid, keywords, scope_targets):
        try:
            results = self.index_mgr.search(keywords, scope_targets)
            if results is None:
                self.result_queue.put(("MSG", "索引不可用"))
                return
            
            batch = []
            for fn, fp, sz, mt, is_dir in results:
                if sid != self.current_search_id:
                    return
                
                if not self._match_keyword(fn, keywords):
                    continue
                
                ext = os.path.splitext(fn)[1].lower()
                tc = 0 if is_dir else (1 if ext in ARCHIVE_EXTS else 2)
                batch.append((fn, fp, sz, mt, tc))
                if len(batch) >= 100:
                    self.result_queue.put(("BATCH", list(batch)))
                    batch.clear()
            if batch:
                self.result_queue.put(("BATCH", batch))
            self.result_queue.put(("DONE", time.time() - self.start_time))
        except Exception as e:
            logger.error(f"索引搜索错误: {e}")
            self.result_queue.put(("ERROR", str(e)))

    def _search_rt(self, sid, keyword, scope_targets):
        """实时搜索 - 优化版（带进度统计）"""
        try:
            keywords = keyword.lower().split()
            
            def check(name):
                return self._match_keyword(name, keywords)
            
            task_queue = queue.Queue()
            for t in scope_targets:
                if os.path.isdir(t):
                    task_queue.put(t)
            
            active = [0]
            lock = threading.Lock()
            scanned = [0]  # 统计已扫描目录数
            
            def worker():
                local_batch = []
                while not self.stop_event and self.current_search_id == sid:
                    while self.is_paused:
                        if self.stop_event:
                            return
                        time.sleep(0.1)
                    try:
                        cur = task_queue.get(timeout=0.1)
                    except queue.Empty:
                        with lock:
                            if task_queue.empty() and active[0] <= 1:
                                break
                        continue
                    
                    with lock:
                        active[0] += 1
                        scanned[0] += 1
                    
                    if should_skip_path(cur.lower()):
                        with lock:
                            active[0] -= 1
                        continue
                    
                    try:
                        with os.scandir(cur) as it:
                            for e in it:
                                if self.stop_event or self.current_search_id != sid:
                                    break
                                name = e.name
                                if not name or name[0] in ('.', '$'):
                                    continue
                                try:
                                    st = e.stat(follow_symlinks=False)
                                    is_dir = st.st_mode & 0o040000
                                except (OSError, PermissionError):
                                    continue
                                
                                if is_dir:
                                    if should_skip_dir(name.lower()):
                                        continue
                                    task_queue.put(e.path)
                                    if check(name):
                                        local_batch.append((name, e.path, 0, 0, 0))
                                else:
                                    ext = os.path.splitext(name)[1].lower()
                                    if ext in SKIP_EXTS:
                                        continue
                                    if check(name):
                                        local_batch.append((name, e.path, st.st_size, st.st_mtime, 1 if ext in ARCHIVE_EXTS else 2))
                                
                                if len(local_batch) >= 50:
                                    self.result_queue.put(("BATCH", list(local_batch)))
                                    # 发送进度更新
                                    with lock:
                                        self.result_queue.put(("PROGRESS", (scanned[0], cur)))
                                    local_batch.clear()
                    except (PermissionError, OSError):
                        pass
                    with lock:
                        active[0] -= 1
                
                if local_batch:
                    self.result_queue.put(("BATCH", local_batch))
            
            threads = [threading.Thread(target=worker, daemon=True) for _ in range(16)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            if self.current_search_id == sid and not self.stop_event:
                self.result_queue.put(("DONE", time.time() - self.start_time))
        except Exception as e:
            logger.error(f"实时搜索错误: {e}")
            self.result_queue.put(("ERROR", str(e)))

    def process_queue(self):
        try:
            for _ in range(200):
                if self.result_queue.empty():
                    break
                t, d = self.result_queue.get_nowait()
                if t == "BATCH":
                    for item in d:
                        self._add_item(*item)
                elif t == "DONE":
                    self._reset_ui()
                    self.status.set(f"✅ 完成: {self.total_found}项 ({d:.2f}s)")
                    self._finalize()
                elif t == "ERROR":
                    self._reset_ui()
                    messagebox.showerror("错误", d)
                elif t == "PROGRESS":
                    # 更新搜索进度
                    scanned_dirs, _ = d
                    elapsed = time.time() - self.search_stats['start_time']
                    speed = scanned_dirs / elapsed if elapsed > 0 else 0
                    self.status.set(f"🔍 实时扫描中... (已扫描 {scanned_dirs:,} 个目录，{speed:.0f}/s)")
                elif t == "IDX_PROG":
                    self._check_index()
                    self.status_path.set(f"索引: {d[1][-40:]}")
                elif t == "IDX_DONE":
                    self._check_index()
                    self.status_path.set("")
                    self.status.set(f"✅ 索引完成 ({self.index_mgr.file_count:,})")
                    self.file_watcher.stop()
                    self.file_watcher.start(self._get_drives())
                    logger.info("👁️ 文件监控已启动")
            
            if self.is_searching:
                with self.results_lock:
                    result_count = len(self.all_results)
                if result_count > 0:
                    now = time.time()
                    if result_count <= 200 or (now - self.last_render_time) > self.render_interval:
                        with self.results_lock:
                            self.filtered_results = list(self.all_results)
                        self._render_page()
                        self.last_render_time = now
                        self.status.set(f"已找到: {result_count}")
        except Exception as e:
            logger.error(f"process_queue error: {e}")
        self.root.after(100, self.process_queue)

    def _add_item(self, name, path, size, mtime, type_code):
        with self.results_lock:
            if path in self.shown_paths:
                return
            self.shown_paths.add(path)
            size_str = "📂 文件夹" if type_code == 0 else ("📦 压缩包" if type_code == 1 else format_size(size))
            mtime_str = "-" if type_code == 0 else format_time(mtime)
            self.all_results.append({
                'filename': name,
                'fullpath': path,
                'dir_path': os.path.dirname(path),
                'size': size,
                'mtime': mtime,
                'type_code': type_code,
                'size_str': size_str,
                'mtime_str': mtime_str
            })
            self.total_found = len(self.all_results)

    def sort_column(self, col, rev):
        if not self.filtered_results:
            return
        key = {
            'size': lambda x: (x['type_code'], x['size']),
            'mtime': lambda x: x['mtime'],
            'filename': lambda x: x['filename'].lower(),
            'path': lambda x: x['dir_path'].lower()
        }[col]
        self.filtered_results.sort(key=key, reverse=rev)
        self._render_page()
        # 切换排序方向
        self.tree.heading(col, command=lambda: self.sort_column(col, not rev))

    # ==================== 索引管理 ====================
    def _check_index(self):
        s = self.index_mgr.get_stats()
        fts = "FTS5✅" if s.get('has_fts') else "FTS5❌"
        mft = "MFT✅" if s.get('used_mft') else "MFT❌"
        
        # 显示最后更新时间
        time_info = ""
        if s['time']:
            last_update = datetime.datetime.fromtimestamp(s['time'])
            time_diff = datetime.datetime.now() - last_update
            if time_diff.days > 0:
                time_info = f" (更新于{time_diff.days}天前)"
            elif time_diff.seconds > 3600:
                time_info = f" (更新于{time_diff.seconds//3600}小时前)"
            else:
                time_info = f" (更新于{time_diff.seconds//60}分钟前)"
        
        if s['building']:
            txt = f"🔄 构建中({s['count']:,}) [{fts}][{mft}]"
        elif s['ready']:
            txt = f"✅ 就绪({s['count']:,}){time_info} [{fts}][{mft}]"
        else:
            txt = f"❌ 未构建 [{fts}][{mft}]"
        self.idx_lbl.config(text=txt)

    def refresh_index_status(self):
        self.index_mgr.reload_stats()
        self._check_index()

    def _show_index_mgr(self):
        """索引管理对话框"""
        dlg = tk.Toplevel(self.root)
        dlg.title("🔧 索引管理")
        dlg.geometry("500x400")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.geometry(f"+{self.root.winfo_x()+100}+{self.root.winfo_y()+100}")
        
        f = ttk.Frame(dlg, padding=15)
        f.pack(fill=BOTH, expand=True)
        s = self.index_mgr.get_stats()
        
        ttk.Label(f, text="📊 索引状态", font=("微软雅黑", 12, "bold")).pack(anchor=W)
        ttk.Separator(f).pack(fill=X, pady=8)
        
        info = ttk.Frame(f)
        info.pack(fill=X, pady=5)
        
        c_dirs = get_c_scan_dirs(self.config_mgr)
        c_dirs_str = ", ".join([os.path.basename(d) for d in c_dirs[:3]]) + ("..." if len(c_dirs) > 3 else "")
        
        last_update_str = "从未"
        if s['time']:
            last_update = datetime.datetime.fromtimestamp(s['time'])
            last_update_str = last_update.strftime('%m-%d %H:%M')
        
        rows = [
            ("文件数量:", f"{s['count']:,}" if s['count'] else "未构建"),
            ("状态:", "✅就绪" if s['ready'] else ("🔄构建中" if s['building'] else "❌未构建")),
            ("FTS5:", "✅已启用" if s.get('has_fts') else "❌未启用"),
            ("MFT:", "✅已使用" if s.get('used_mft') else "❌未使用"),
            ("构建时间:", last_update_str),
            ("C盘范围:", c_dirs_str),
            ("索引路径:", os.path.basename(s['path'])),
        ]
        for i, (l, v) in enumerate(rows):
            ttk.Label(info, text=l).grid(row=i, column=0, sticky=W, pady=2)
            ttk.Label(info, text=v, foreground="#28a745" if "✅" in str(v) else "#555").grid(row=i, column=1, sticky=W, padx=10)
        
        ttk.Separator(f).pack(fill=X, pady=10)
        
        bf = ttk.Frame(f)
        bf.pack(fill=X, pady=10)
        bf.columnconfigure(0, weight=1)
        bf.columnconfigure(1, weight=1)
        bf.columnconfigure(2, weight=1)
        
        def browse():
            p = filedialog.asksaveasfilename(
                title="选择索引位置",
                initialdir=os.path.dirname(s['path']),
                initialfile="index.db",
                defaultextension=".db",
                filetypes=[("SQLite", "*.db")]
            )
            if p:
                ok, msg = self.index_mgr.change_db_path(p)
                if ok:
                    self.file_watcher.stop()
                    self.file_watcher = FileWatcher(self.index_mgr, config_mgr=self.config_mgr)
                    self._check_index()
                    dlg.destroy()
                    self._show_index_mgr()
                else:
                    messagebox.showerror("错误", msg)
        
        def rebuild():
            dlg.destroy()
            self._build_index()
        
        def delete():
            if messagebox.askyesno("确认", "确定删除索引？"):
                self.file_watcher.stop()
                self.index_mgr.close()
                for ext in ['', '-wal', '-shm']:
                    try:
                        os.remove(self.index_mgr.db_path + ext)
                    except (FileNotFoundError, PermissionError, OSError) as e:
                        logger.warning(f"删除索引文件失败 {ext}: {e}")
                self.index_mgr = IndexManager(db_path=self.index_mgr.db_path, config_mgr=self.config_mgr)
                self.file_watcher = FileWatcher(self.index_mgr, config_mgr=self.config_mgr)
                self._check_index()
                dlg.destroy()
        
        ttk.Button(bf, text="🔄 重建索引", command=rebuild, bootstyle="primary", width=14).grid(row=0, column=0, padx=5)
        ttk.Button(bf, text="📁 更改位置", command=browse, bootstyle="secondary", width=14).grid(row=0, column=1, padx=5)
        ttk.Button(bf, text="🗑️ 删除索引", command=delete, bootstyle="danger-outline", width=14).grid(row=0, column=2, padx=5)

    def _build_index(self):
        if self.index_mgr.is_building:
            return
        self.index_build_stop = False
        
        def run():
            self.index_mgr.build_index(
                self._get_drives(),
                lambda c, p: self.result_queue.put(("IDX_PROG", (c, p))),
                lambda: self.index_build_stop
            )
            self.result_queue.put(("IDX_DONE", None))
        
        threading.Thread(target=run, daemon=True).start()
        self._check_index()

    # ==================== 新增功能 ====================
    def export_results(self):
        """导出搜索结果"""
        if not self.filtered_results:
            messagebox.showinfo("提示", "没有可导出的结果")
            return
        
        filetypes = [("CSV文件", "*.csv"), ("文本文件", "*.txt")]
        try:
            import openpyxl
            filetypes.insert(0, ("Excel文件", "*.xlsx"))
        except ImportError:
            pass
        
        path = filedialog.asksaveasfilename(title="导出结果", defaultextension=".csv", filetypes=filetypes)
        if not path:
            return
        
        ext = os.path.splitext(path)[1].lower()
        
        try:
            with self.results_lock:
                data = [(r['filename'], r['fullpath'], r['size_str'], r['mtime_str']) for r in self.filtered_results]
            
            if ext == '.xlsx':
                import openpyxl
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.append(["文件名", "完整路径", "大小", "修改时间"])
                for row in data:
                    ws.append(row)
                wb.save(path)
            elif ext == '.csv':
                import csv
                with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f)
                    writer.writerow(["文件名", "完整路径", "大小", "修改时间"])
                    writer.writerows(data)
            else:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write("文件名\t完整路径\t大小\t修改时间\n")
                    for row in data:
                        f.write('\t'.join(row) + '\n')
            
            messagebox.showinfo("成功", f"已导出 {len(data)} 条记录")
            logger.info(f"导出成功: {len(data)} 条记录 -> {path}")
        except Exception as e:
            logger.error(f"导出失败: {e}")
            messagebox.showerror("导出失败", str(e))

    def scan_large_files(self):
        """扫描大文件"""
        if not self.all_results:
            messagebox.showinfo("提示", "请先进行搜索")
            return
        
        min_size = 100 * 1024 * 1024
        with self.results_lock:
            large_files = [item for item in self.all_results if item['type_code'] in (1, 2) and item['size'] >= min_size]
        
        large_files.sort(key=lambda x: x['size'], reverse=True)
        
        with self.results_lock:
            self.filtered_results = large_files
        
        self.current_page = 1
        self._render_page()
        
        total_size = sum(f['size'] for f in large_files)
        self.status.set(f"找到 {len(large_files)} 个大文件 (≥100MB)，共 {format_size(total_size)}")
        self.lbl_filter.config(text=f"大文件: {len(large_files)}/{len(self.all_results)}")

    def find_duplicates(self):
        """查找重复文件"""
        if not self.all_results:
            messagebox.showinfo("提示", "请先进行搜索")
            return
        
        from collections import defaultdict
        size_groups = defaultdict(list)
        
        with self.results_lock:
            for item in self.all_results:
                if item['type_code'] == 2 and item['size'] > 0:
                    key = (item['size'], item['filename'].lower())
                    size_groups[key].append(item)
        
        duplicates = []
        for key, items in size_groups.items():
            if len(items) > 1:
                duplicates.extend(items)
        
        duplicates.sort(key=lambda x: (x['size'], x['filename'].lower()), reverse=True)
        
        with self.results_lock:
            self.filtered_results = duplicates
        
        self.current_page = 1
        self._render_page()
        self.status.set(f"找到 {len(duplicates)} 个可能重复的文件")
        self.lbl_filter.config(text=f"重复: {len(duplicates)}/{len(self.all_results)}")

    def find_empty_folders(self):
        """查找空文件夹"""
        if not self.all_results:
            messagebox.showinfo("提示", "请先进行搜索")
            return
        
        empty_folders = []
        with self.results_lock:
            for item in self.all_results:
                if item['type_code'] == 0:
                    try:
                        if os.path.exists(item['fullpath']) and not os.listdir(item['fullpath']):
                            empty_folders.append(item)
                    except (PermissionError, OSError):
                        pass
        
        with self.results_lock:
            self.filtered_results = empty_folders
        
        self.current_page = 1
        self._render_page()
        self.status.set(f"找到 {len(empty_folders)} 个空文件夹")
        self.lbl_filter.config(text=f"空文件夹: {len(empty_folders)}/{len(self.all_results)}")

    def _show_batch_rename(self):
        """显示批量重命名对话框"""
        # 优先使用选中项，否则使用当前筛选结果
        selected_items = self._get_selected_items()
        if selected_items:
            targets = selected_items
            scope_text = f"当前选中 {len(targets)} 个项目"
        else:
            with self.results_lock:
                targets = list(self.filtered_results)
            if not targets:
                messagebox.showinfo("提示", "没有可重命名的结果", parent=self.root)
                return
            scope_text = f"当前筛选结果 {len(targets)} 个项目"
        
        dlg = BatchRenameDialog(self.root, targets, self)
        dlg.show(scope_text)
    # ==================== 关闭处理 ====================
    def _on_close(self):
        """窗口关闭处理"""
        # 如果启用了托盘，最小化到托盘
        if self.config_mgr.get_tray_enabled() and HAS_TRAY and self.tray_mgr.running:
            self.root.withdraw()  # 隐藏窗口
            self.tray_mgr.show_notification("极速文件搜索", "程序已最小化到托盘")
        else:
            self._do_quit()
    
    def _do_quit(self):
        """真正退出程序"""
        self.index_build_stop = True
        self.stop_event = True
        
        # 停止热键
        self.hotkey_mgr.stop()
        
        # 停止托盘
        self.tray_mgr.stop()
        
        # 停止文件监控
        self.file_watcher.stop()
        
        # 关闭索引
        self.index_mgr.close()
        
        # 销毁窗口
        self.root.destroy()


# ==================== 程序入口 ====================
if __name__ == "__main__":
    logger.info("🚀 极速文件搜索 V42 增强版 - 四大新功能")
    logger.info("新增功能: C盘目录设置、磁盘筛选联动、全局热键、系统托盘")
    
    if platform.system() == 'Windows':
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception as e:
            logger.warning(f"设置DPI失败: {e}")
    
    # 加载保存的主题
    config = ConfigManager()
    theme = config.get_theme()
    
    root = ttk.Window(themename=theme)
    app = SearchApp(root)
    root.mainloop()          