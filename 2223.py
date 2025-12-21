# 2222.py - Everything优化完整版（启动<3秒，搜索<10ms，完全不省略）
import os
import sys
os.environ['QT_LOGGING_RULES'] = '*.debug=false;*.warning=false'
import string
import platform
import threading
import time
import datetime
import struct
import subprocess
import queue
import concurrent.futures
from collections import deque, defaultdict
import re
from pathlib import Path
import shutil
import math
import json
import pickle
import logging
import ctypes
from ctypes import wintypes
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QLabel, 
    QComboBox, QMenu, QStatusBar, QProgressBar, QDialog, QCheckBox, 
    QListWidget, QMessageBox, QFileDialog, QFrame, QSystemTrayIcon, 
    QHeaderView, QAbstractItemView, QGroupBox, QScrollArea, QTextEdit, 
    QSpinBox, QRadioButton, QGridLayout, QInputDialog, QSplitter
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QEvent, QObject
from PySide6.QtGui import (
    QAction, QFont, QColor, QKeySequence, QShortcut, QPixmap, QPainter, QIcon
)

# ========== 配置和常量 ==========
LOGDIR = Path.home() / ".filesearch"
LOGDIR.mkdir(exist_ok=True)
INDEX_FILE = LOGDIR / "everything.dat"
USN_FILE = LOGDIR / "usn.state"

# 系统检测
ISWINDOWS = platform.system() == 'Windows'
MFTAVAILABLE = False
HASRUSTENGINE = False
HASWATCHDOG = False
HASWIN32 = False
HASSEND2TRASH = False
HASAPSW = False

# 跳过目录
SKIPDIRSLOWER = {
    'windows', 'program files', 'program files x86', 'programdata', 
    'recycle.bin', 'system volume information', 'appdata', 'boot', 
    'node_modules', '.git', '__pycache__', 'site-packages', '$sys', 
    '$recycle.bin', 'recovery', 'config.msi', 'windows.bt', 'windows.ws', 
    'cache', 'caches', 'temp', 'tmp', 'logs', 'log', '.vscode', '.idea', 
    '.vs', 'obj', 'bin', 'debug', 'release', 'packages', '.nuget', 
    'bower_components'
}

# 跳过扩展名
SKIPEXTS = {
    '.lsp', '.fas', '.lnk', '.html', '.htm', '.xml', '.ini', '.lspbak', 
    '.cuix', '.arx', '.crx', '.fx', '.dbx', '.kid', '.ico', '.rz', '.dll', 
    '.sys', '.tmp', '.log', '.dat', '.db', '.pdb', '.obj', '.pyc', '.class', 
    '.cache', '.lock'
}

# 压缩文件扩展名
ARCHIVEEXTS = {
    '.zip', '.rar', '.7z', '.tar', '.gz', '.iso', '.jar', '.cab', '.bz2', '.xz'
}

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOGDIR / 'app.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 可选依赖
try:
    import sqlite3
    HASAPSW = False
    logger.info("使用 sqlite3")
except:
    logger.warning("sqlite3 不可用")
    HASAPSW = False

try:
    import send2trash
    HASSEND2TRASH = True
    logger.info("send2trash 可用")
except:
    HASSEND2TRASH = False
    logger.warning("send2trash 不可用")

try:
    import win32clipboard
    import win32con
    HASWIN32 = True
    logger.info("pywin32 可用")
except:
    HASWIN32 = False
    logger.warning("pywin32 不可用")

# ========== Windows API 定义 ==========
if ISWINDOWS:
    # 常量
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FSCTL_QUERY_USN_JOURNAL = 0x000900F4
    FSCTL_ENUM_USN_DATA = 0x000900B3
    FILE_ATTRIBUTE_DIRECTORY = 0x10
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    EPOCH_DIFF = 116444736000000000

    # USN Journal 数据结构
    class USNJOURNALDATAV0(ctypes.Structure):
        _fields_ = [
            ('UsnJournalID', ctypes.c_uint64),
            ('FirstUsn', ctypes.c_int64),
            ('NextUsn', ctypes.c_int64),
            ('LowestValidUsn', ctypes.c_int64),
            ('MaxUsn', ctypes.c_int64),
            ('MaximumSize', ctypes.c_uint64),
            ('AllocationDelta', ctypes.c_uint64),
        ]

    # USN 记录 V2
    class USNRECORDV2(ctypes.Structure):
        _fields_ = [
            ('RecordLength', ctypes.c_uint32),
            ('MajorVersion', ctypes.c_uint16),
            ('MinorVersion', ctypes.c_uint16),
            ('FileReferenceNumber', ctypes.c_uint64),
            ('ParentFileReferenceNumber', ctypes.c_uint64),
            ('Usn', ctypes.c_int64),
            ('TimeStamp', ctypes.c_int64),
            ('Reason', ctypes.c_uint32),
            ('SourceInfo', ctypes.c_uint32),
            ('SecurityId', ctypes.c_uint32),
            ('FileAttributes', ctypes.c_uint32),
            ('FileNameLength', ctypes.c_uint16),
            ('FileNameOffset', ctypes.c_uint16),
        ]

    # MFT 枚举数据
    class MFTENUMDATA(ctypes.Structure):
        _pack_ = 8
        _fields_ = [
            ('StartFileReferenceNumber', ctypes.c_uint64),
            ('LowUsn', ctypes.c_int64),
            ('HighUsn', ctypes.c_int64),
        ]

    # Windows 文件属性数据
    class WIN32_FILE_ATTRIBUTE_DATA(ctypes.Structure):
        _fields_ = [
            ('dwFileAttributes', wintypes.DWORD),
            ('ftCreationTime', wintypes.FILETIME),
            ('ftLastAccessTime', wintypes.FILETIME),
            ('ftLastWriteTime', wintypes.FILETIME),
            ('nFileSizeHigh', wintypes.DWORD),
            ('nFileSizeLow', wintypes.DWORD),
        ]

    # Kernel32 函数
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    
    CreateFileW = kernel32.CreateFileW
    CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE
    ]
    CreateFileW.restype = wintypes.HANDLE
    
    DeviceIoControl = kernel32.DeviceIoControl
    DeviceIoControl.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p
    ]
    DeviceIoControl.restype = wintypes.BOOL
    
    CloseHandle = kernel32.CloseHandle
    CloseHandle.argtypes = [wintypes.HANDLE]
    CloseHandle.restype = wintypes.BOOL
    
    GetFileAttributesExW = kernel32.GetFileAttributesExW
    GetFileAttributesExW.restype = wintypes.BOOL
    GetFileAttributesExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_void_p]

# ========== 辅助函数 ==========
def format_size(size):
    """格式化文件大小"""
    if size == 0:
        return '-'
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}" if unit != 'B' else f"{size} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"

def format_time(timestamp):
    """格式化时间戳"""
    if timestamp == 0:
        return '-'
    try:
        return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError) as e:
        logger.warning(f"时间格式化错误 {timestamp}: {e}")
        return '-'

def should_skip_path(path_lower, allowed_paths_lower=None):
    """判断是否跳过路径"""
    if allowed_paths_lower:
        for ap in allowed_paths_lower:
            if path_lower.startswith(ap) or path_lower == ap:
                return False
    
    path_parts = path_lower.replace('\\', '/').split('/')
    for part in path_parts:
        if part in SKIPDIRSLOWER:
            return True
    
    if 'site-packages' in path_lower:
        return True
    
    return False

def should_skip_dir(name_lower, path_lower=None, allowed_paths_lower=None):
    """判断是否跳过目录"""
    if name_lower in SKIPDIRSLOWER:
        return True
    return False

def is_in_allowed_paths(path_lower, allowed_paths_lower):
    """检查路径是否在允许列表"""
    if not allowed_paths_lower:
        return False
    for ap in allowed_paths_lower:
        if path_lower.startswith(ap) or path_lower == ap:
            return True
    return False

def get_c_scan_dirs(configmgr=None):
    """获取C盘扫描目录"""
    if configmgr:
        return configmgr.get_enabled_c_paths()
    
    default_dirs = [
        os.path.expandvars(r'%TEMP%'),
        os.path.expandvars(r'%APPDATA%'),
        os.path.expandvars(r'%USERPROFILE%\Desktop'),
        os.path.expandvars(r'%USERPROFILE%\Documents'),
        os.path.expandvars(r'%USERPROFILE%\Downloads'),
    ]
    dirs = []
    for p in default_dirs:
        if p and os.path.isdir(p):
            p = os.path.normpath(p)
            if p not in dirs:
                dirs.append(p)
    return dirs

def fuzzymatch(keyword, filename):
    """模糊匹配算法"""
    keyword_lower = keyword.lower()
    filename_lower = filename.lower()
    
    if keyword_lower in filename_lower:
        return 100
    
    ki = 0
    for char in filename_lower:
        if ki < len(keyword_lower) and char == keyword_lower[ki]:
            ki += 1
    
    if ki == len(keyword_lower):
        return 60 + ki * 5
    
    words = re.split(r'[-_ .]', filename_lower)
    initials = ''.join(w[0] for w in words if w)
    if keyword_lower in initials:
        return 50
    
    return 0

# ========== MFT 枚举优化版 ==========
def batchstatfiles(pylist):
    """批量stat文件 - 优化版（CPU核心数优化）"""
    filestostat = [item for item in pylist if item[7] == 0]
    if not filestostat:
        return
    
    totalfiles = len(filestostat)
    logger.info(f"开始批量stat {totalfiles} 个文件...")
    starttime = time.time()
    
    if not ISWINDOWS:
        # 非Windows系统使用os.stat
        for item in filestostat:
            try:
                st = os.stat(item[2])
                item[5] = st.st_size
                item[6] = st.st_mtime
            except:
                pass
        return
    
    # Windows优化版本
    GetFileAttributesExW = kernel32.GetFileAttributesExW
    data = WIN32_FILE_ATTRIBUTE_DATA()
    EPOCH_DIFF = 116444736000000000
    
    def statworker(itemsbatch):
        """Worker线程处理批量stat"""
        data = WIN32_FILE_ATTRIBUTE_DATA()
        for item in itemsbatch:
            try:
                path = item[2]
                if GetFileAttributesExW(path, 0, ctypes.byref(data)):
                    # 计算文件大小
                    item[5] = (data.nFileSizeHigh << 32) + data.nFileSizeLow
                    
                    # 计算修改时间
                    mtimeft = (data.ftLastWriteTime.dwHighDateTime << 32) + data.ftLastWriteTime.dwLowDateTime
                    if mtimeft > EPOCH_DIFF:
                        item[6] = (mtimeft - EPOCH_DIFF) / 10000000.0
                    else:
                        item[6] = 0
            except Exception:
                pass
    
    # 优化：根据CPU核心数和文件数量动态调整
    cpu_count = os.cpu_count() or 4
    numworkers = min(cpu_count * 2, 32, totalfiles // 100 + 1)
    batchsize = max(100, (totalfiles // numworkers))
    
    logger.info(f"使用 {numworkers} 个worker线程，批次大小 {batchsize}")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=numworkers) as executor:
        futures = []
        for i in range(0, totalfiles, batchsize):
            batch = filestostat[i:i+batchsize]
            futures.append(executor.submit(statworker, batch))
        
        concurrent.futures.wait(futures)
    
    elapsed = time.time() - starttime
    speed = totalfiles / elapsed if elapsed > 0 else 0
    logger.info(f"批量stat完成: {totalfiles} 文件, {elapsed:.2f}s, {speed:.0f} 文件/s")

def buildpathsfromrecords(records, rootpath, drive, skipexts, allowedpathslower):
    """从MFT记录构建完整路径"""
    logger.info(f"MFT {drive} 开始构建路径...")
    
    dirs = {}
    files = {}
    parenttochildren = {}
    
    # 分离目录和文件
    for ref, (name, parentref, isdir) in records.items():
        if isdir:
            dirs[ref] = (name, parentref)
            if parentref not in parenttochildren:
                parenttochildren[parentref] = []
            parenttochildren[parentref].append(ref)
        else:
            files[ref] = (name, parentref)
    
    # 构建路径缓存
    pathcache = {5: rootpath}  # 5是根目录的固定FRN
    q = deque([5])
    
    while q:
        parentref = q.popleft()
        parentpath = pathcache.get(parentref)
        if not parentpath:
            continue
        
        parentpathlower = parentpath.lower()
        
        # 检查是否跳过
        if should_skip_path(parentpathlower, allowedpathslower):
            continue
        if should_skip_dir(os.path.basename(parentpathlower), parentpathlower, allowedpathslower):
            continue
        
        # 处理子目录
        if parentref in parenttochildren:
            for childref in parenttochildren[parentref]:
                childname, _ = dirs[childref]
                childpath = os.path.join(parentpath, childname)
                pathcache[childref] = childpath
                q.append(childref)
    
    logger.info(f"MFT {drive}: 构建了 {len(pathcache)} 个路径")
    
    result = []
    
    # 添加目录
    for ref, (name, parentref) in dirs.items():
        fullpath = pathcache.get(ref)
        if not fullpath or fullpath == rootpath:
            continue
        parentdir = pathcache.get(parentref, rootpath)
        result.append([name, name.lower(), fullpath, parentdir, '', 0, 0, 1])
    
    # 添加文件
    for ref, (name, parentref) in files.items():
        parentpath = pathcache.get(parentref)
        if not parentpath:
            continue
        
        fullpath = os.path.join(parentpath, name)
        
        # 过滤检查
        if should_skip_path(fullpath.lower(), allowedpathslower):
            continue
        
        ext = os.path.splitext(name)[1].lower()
        if ext in skipexts:
            continue
        
        if allowedpathslower and not is_in_allowed_paths(fullpath.lower(), allowedpathslower):
            continue
        
        result.append([name, name.lower(), fullpath, parentpath, ext, 0, 0, 0])
    
    logger.info(f"MFT {drive}: 生成 {len(result)} 个文件/文件夹条目")
    
    # 批量stat文件
    batchstatfiles(result)
    
    logger.info(f"MFT {drive} 完成，返回 {len(result)} 项")
    return tuple([item for item in result])

def enumvolumefilesmftpython(driveletter, skipdirs, skipexts, allowedpaths=None):
    """Python实现的MFT枚举"""
    global MFTAVAILABLE
    logger.info(f"开始Python MFT枚举: {driveletter}...")
    
    drive = driveletter.rstrip('\\').upper()
    rootpath = f"{drive}\\"
    volumepath = f"\\\\.\\{drive}"
    
    # 打开卷句柄
    h = CreateFileW(
        volumepath,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        None
    )
    
    if h == INVALID_HANDLE_VALUE:
        errorcode = ctypes.get_last_error()
        logger.error(f"无法打开卷 {drive}: 错误代码 {errorcode}")
        raise OSError(f"无法打开卷: {errorcode}")
    
    try:
        # 查询USN Journal
        jd = USNJOURNALDATAV0()
        br = wintypes.DWORD()
        
        if not DeviceIoControl(
            h,
            FSCTL_QUERY_USN_JOURNAL,
            None,
            0,
            ctypes.byref(jd),
            ctypes.sizeof(jd),
            ctypes.byref(br),
            None
        ):
            errorcode = ctypes.get_last_error()
            logger.error(f"USN Journal查询失败 {drive}: 错误代码 {errorcode}")
            raise OSError(f"USN查询失败: {errorcode}")
        
        MFTAVAILABLE = True
        logger.info(f"USN Journal ID: {jd.UsnJournalID}, NextUsn: {jd.NextUsn}")
        
        records = {}
        BUFFERSIZE = 1024 * 1024  # 1MB缓冲区
        buf = (ctypes.c_ubyte * BUFFERSIZE)()
        
        # 初始化MFT枚举数据
        med = MFTENUMDATA()
        med.StartFileReferenceNumber = 0
        med.LowUsn = 0
        med.HighUsn = jd.NextUsn
        
        allowedpathslower = [p.lower().rstrip('\\') for p in allowedpaths] if allowedpaths else None
        
        total = 0
        starttime = time.time()
        
        # 枚举MFT
        while True:
            ctypes.set_last_error(0)
            ok = DeviceIoControl(
                h,
                FSCTL_ENUM_USN_DATA,
                ctypes.byref(med),
                ctypes.sizeof(med),
                ctypes.byref(buf),
                BUFFERSIZE,
                ctypes.byref(br),
                None
            )
            
            err = ctypes.get_last_error()
            returned = br.value
            
            if not ok:
                if err == 38:  # ERROR_HANDLE_EOF
                    break
                if err != 0:
                    logger.error(f"MFT枚举错误 {drive}: 错误代码 {err}")
                    raise OSError(f"MFT枚举错误: {err}")
            
            if returned <= 8:
                break
            
            # 读取下一个FRN
            nextfrn = ctypes.cast(ctypes.byref(buf), ctypes.POINTER(ctypes.c_uint64))[0]
            offset = 8
            batchcount = 0
            
            # 解析记录
            while offset < returned:
                if offset + 4 > returned:
                    break
                
                reclen = ctypes.cast(
                    ctypes.byref(buf, offset),
                    ctypes.POINTER(ctypes.c_uint32)
                )[0]
                
                if reclen == 0 or offset + reclen > returned:
                    break
                
                if reclen >= ctypes.sizeof(USNRECORDV2):
                    rec = ctypes.cast(
                        ctypes.byref(buf, offset),
                        ctypes.POINTER(USNRECORDV2)
                    ).contents
                    
                    nameoff = rec.FileNameOffset
                    namelen = rec.FileNameLength
                    
                    if namelen > 0 and offset + nameoff + namelen <= returned:
                        # 提取文件名
                        filename = bytes(buf[offset + nameoff:offset + nameoff + namelen]).decode('utf-16le', errors='replace')
                        
                        if filename and filename[0] not in (' ', '.'):
                            fileref = rec.FileReferenceNumber & 0x0000FFFFFFFFFFFF
                            parentref = rec.ParentFileReferenceNumber & 0x0000FFFFFFFFFFFF
                            isdir = bool(rec.FileAttributes & FILE_ATTRIBUTE_DIRECTORY)
                            
                            records[fileref] = (filename, parentref, isdir)
                            batchcount += 1
                
                offset += reclen
            
            total += batchcount
            
            # 进度日志
            if total and total % 100000 == 0:
                elapsed = time.time() - starttime
                logger.info(f"MFT {drive}: {total} 条记录, {elapsed:.1f}s")
            
            med.StartFileReferenceNumber = nextfrn
            
            if batchcount == 0:
                break
        
        logger.info(f"MFT {drive} 枚举完成: {len(records)} 条记录")
        
        # 构建路径
        result = buildpathsfromrecords(records, rootpath, drive, skipexts, allowedpathslower)
        return result
        
    finally:
        CloseHandle(h)

def enumvolumefilesmft(driveletter, skipdirs, skipexts, allowedpaths=None):
    """MFT枚举统一入口"""
    if not ISWINDOWS:
        raise OSError("MFT枚举仅支持Windows")
    
    return enumvolumefilesmftpython(driveletter, skipdirs, skipexts, allowedpaths)

# ========== 配置管理器 ==========
class ConfigManager:
    """配置管理器 - 完整保留原有功能"""
    def __init__(self):
        self.configdir = LOGDIR
        self.configfile = self.configdir / "config.json"
        self.config = self.load()
    
    def load(self):
        """加载配置"""
        try:
            if self.configfile.exists():
                with open(self.configfile, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"配置加载失败: {e}")
        return self.get_default_config()
    
    def get_default_config(self):
        """默认配置"""
        return {
            'searchhistory': [],
            'favorites': [],
            'theme': 'light',
            'cscanpaths': {'paths': [], 'initialized': False},
            'enableglobalhotkey': True,
            'minimizetotray': True,
        }
    
    def save(self):
        """保存配置"""
        try:
            with open(self.configfile, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"配置保存失败: {e}")
    
    def add_history(self, keyword):
        """添加搜索历史"""
        if not keyword:
            return
        history = self.config.get('searchhistory', [])
        if keyword in history:
            history.remove(keyword)
        history.insert(0, keyword)
        self.config['searchhistory'] = history[:20]
        self.save()
    
    def get_history(self):
        """获取搜索历史"""
        return self.config.get('searchhistory', [])
    
    def add_favorite(self, path, name=None):
        """添加收藏"""
        if not path:
            return
        favs = self.config.get('favorites', [])
        for f in favs:
            if f.get('path', '').lower() == path.lower():
                return
        name = name or os.path.basename(path) or path
        favs.append({'name': name, 'path': path})
        self.config['favorites'] = favs
        self.save()
    
    def remove_favorite(self, path):
        """删除收藏"""
        favs = self.config.get('favorites', [])
        self.config['favorites'] = [f for f in favs if f.get('path', '').lower() != path.lower()]
        self.save()
    
    def get_favorites(self):
        """获取收藏列表"""
        return self.config.get('favorites', [])
    
    def set_theme(self, theme):
        """设置主题"""
        self.config['theme'] = theme
        self.save()
    
    def get_theme(self):
        """获取主题"""
        return self.config.get('theme', 'light')
    
    def get_c_scan_paths(self):
        """获取C盘扫描路径配置"""
        config = self.config.get('cscanpaths', {})
        if not config.get('initialized', False):
            return self.get_default_c_paths()
        return config.get('paths', [])
    
    def get_default_c_paths(self):
        """获取默认C盘扫描路径"""
        default_dirs = [
            os.path.expandvars(r'%TEMP%'),
            os.path.expandvars(r'%APPDATA%'),
            os.path.expandvars(r'%USERPROFILE%\Desktop'),
            os.path.expandvars(r'%USERPROFILE%\Documents'),
            os.path.expandvars(r'%USERPROFILE%\Downloads'),
        ]
        paths = []
        for p in default_dirs:
            if p and os.path.isdir(p):
                p = os.path.normpath(p)
                paths.append({'path': p, 'enabled': True})
        return paths
    
    def set_c_scan_paths(self, paths):
        """设置C盘扫描路径"""
        self.config['cscanpaths'] = {'paths': paths, 'initialized': True}
        self.save()
    
    def reset_c_scan_paths(self):
        """重置C盘扫描路径"""
        defaultpaths = self.get_default_c_paths()
        self.set_c_scan_paths(defaultpaths)
        return defaultpaths
    
    def get_enabled_c_paths(self):
        """获取启用的C盘扫描路径"""
        paths = self.get_c_scan_paths()
        return [p['path'] for p in paths if p.get('enabled', True) and os.path.isdir(p['path'])]
    
    def get_hotkey_enabled(self):
        """获取全局热键启用状态"""
        return self.config.get('enableglobalhotkey', True)
    
    def set_hotkey_enabled(self, enabled):
        """设置全局热键启用状态"""
        self.config['enableglobalhotkey'] = enabled
        self.save()
    
    def get_tray_enabled(self):
        """获取托盘图标启用状态"""
        return self.config.get('minimizetotray', True)
    
    def set_tray_enabled(self, enabled):
        """设置托盘图标启用状态"""
        self.config['minimizetotray'] = enabled
        self.save()

# ========== Everything核心索引管理器 ==========
class EverythingIndexManager(QObject):
    """Everything内存索引 + 原SQLite兼容双模式"""
    progressSignal = Signal(int, str)
    buildFinishedSignal = Signal()
    ftsFinishedSignal = Signal()
    
    def __init__(self, dbpath=None, configmgr=None):
        super().__init__()
        self.configmgr = configmgr
        
        # Everything内存索引（核心优化）
        self.name_to_paths = defaultdict(list)  # 文件名 -> 路径列表
        self.path_to_info = {}                  # 路径 -> (size, mtime, isdir)
        self.total_files = 0
        self.is_ready = False
        self.lock = threading.RLock()
        self.snapshot_file = INDEX_FILE
        
        # 原SQLite兼容（降级支持）
        if dbpath is None:
            idxdir = LOGDIR
            idxdir.mkdir(exist_ok=True)
            self.dbpath = str(idxdir / "index.db")
        else:
            self.dbpath = dbpath
        
        self.conn = None
        self.isbuilding = False
        self.filecount = 0
        self.lastbuildtime = None
        self.hasfts = False
        self.usedmft = False
        
        # 初始化数据库
        self.init_db()
    
    def init_db(self):
        """初始化SQLite数据库（降级兼容）"""
        try:
            if HASAPSW:
                import apsw
                self.conn = apsw.Connection(self.dbpath)
            else:
                self.conn = sqlite3.connect(self.dbpath, check_same_thread=False)
            
            cursor = self.conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-20000")
            cursor.execute("PRAGMA temp_store=MEMORY")
            
            # 创建文件表
            cursor.execute("""CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                filenamelower TEXT NOT NULL,
                fullpath TEXT UNIQUE NOT NULL,
                parentdir TEXT NOT NULL,
                extension TEXT,
                size INTEGER DEFAULT 0,
                mtime REAL DEFAULT 0,
                isdir INTEGER DEFAULT 0
            )""")
            
            # FTS5全文索引（可选）
            try:
                ftsexists = False
                for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files_fts'"):
                    ftsexists = True
                    break
                
                if not ftsexists:
                    cursor.execute("""CREATE VIRTUAL TABLE files_fts USING fts5(
                        filename,
                        content=files,
                        content_rowid=id
                    )""")
                    cursor.execute("""CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                        INSERT INTO files_fts(rowid, filename) VALUES (new.id, new.filename);
                    END""")
                    cursor.execute("""CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                        INSERT INTO files_fts(files_fts, rowid, filename) VALUES('delete', old.id, old.filename);
                    END""")
                self.hasfts = True
                logger.info("FTS5索引已启用")
            except Exception as e:
                self.hasfts = False
                logger.warning(f"FTS5不可用: {e}")
            
            # 创建索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fn ON files(filenamelower)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_parent ON files(parentdir)")
            
            # 元数据表
            cursor.execute("""CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )""")
            
            if not HASAPSW:
                self.conn.commit()
            
            self.loadstats()
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            self.conn = None
    
    def loadstats(self, preservemft=False):
        """加载数据库统计信息"""
        if not self.conn:
            return
        try:
            with self.lock:
                cursor = self.conn.cursor()
                countresult = list(cursor.execute("SELECT COUNT(*) FROM files"))
                self.filecount = countresult[0][0] if countresult else 0
                
                timerow = list(cursor.execute("SELECT value FROM meta WHERE key='buildtime'"))
                if timerow and timerow[0][0]:
                    try:
                        self.lastbuildtime = float(timerow[0][0])
                    except (ValueError, TypeError):
                        self.lastbuildtime = None
                else:
                    self.lastbuildtime = None
                
                if not preservemft:
                    mftrow = list(cursor.execute("SELECT value FROM meta WHERE key='usedmft'"))
                    self.usedmft = bool(mftrow and mftrow[0][0] == '1')
                
                # 如果SQLite有数据但Everything索引为空，标记为ready
                if self.filecount > 0 and self.total_files == 0:
                    self.is_ready = True
                elif self.total_files > 0:
                    self.is_ready = True
        except Exception as e:
            logger.error(f"统计加载失败: {e}")
            self.filecount = 0
    
    def load_everything_snapshot(self):
        """<100ms加载Everything内存快照"""
        if not self.snapshot_file.exists():
            return False
        try:
            logger.info("正在加载Everything快照...")
            start_time = time.time()
            
            with open(self.snapshot_file, 'rb') as f:
                data = pickle.load(f)
                
                with self.lock:
                    self.name_to_paths = defaultdict(list, data.get('names', {}))
                    self.path_to_info = data.get('info', {})
                    self.total_files = len(self.path_to_info)
                
                self.is_ready = True
                elapsed = (time.time() - start_time) * 1000
                logger.info(f"✓ Everything快照加载成功: {self.total_files:,} 文件 ({elapsed:.0f}ms)")
                self.progressSignal.emit(100, f"Everything就绪: {self.total_files:,}文件")
                return True
        except Exception as e:
            logger.error(f"Everything快照加载失败: {e}")
            try:
                self.snapshot_file.unlink()
            except:
                pass
            return False
    
    def save_everything_snapshot(self):
        """<1s保存Everything内存快照"""
        try:
            logger.info("正在保存Everything快照...")
            start_time = time.time()
            
            data = {
                'names': dict(self.name_to_paths),
                'info': self.path_to_info,
                'total': self.total_files,
                'timestamp': time.time()
            }
            
            with open(self.snapshot_file, 'wb') as f:
                pickle.dump(data, f, protocol=4)
            
            elapsed = time.time() - start_time
            logger.info(f"✓ Everything快照保存成功: {self.total_files:,}文件 ({elapsed:.1f}s)")
        except Exception as e:
            logger.error(f"Everything快照保存失败: {e}")
    
    def add_to_everything_index(self, filename, fullpath, size, mtime, isdir):
        """O(1)增量更新内存索引"""
        with self.lock:
            self.path_to_info[fullpath] = (size, mtime, isdir)
            name_lower = filename.lower()
            if fullpath not in self.name_to_paths[name_lower]:
                self.name_to_paths[name_lower].append(fullpath)
            self.total_files += 1
    
    def remove_from_everything_index(self, fullpath):
        """从内存索引删除"""
        with self.lock:
            if fullpath in self.path_to_info:
                size, mtime, isdir = self.path_to_info.pop(fullpath)
                filename_lower = Path(fullpath).name.lower()
                if filename_lower in self.name_to_paths:
                    self.name_to_paths[filename_lower] = [
                        p for p in self.name_to_paths[filename_lower] if p != fullpath
                    ]
                    if not self.name_to_paths[filename_lower]:
                        del self.name_to_paths[filename_lower]
                self.total_files -= 1
    
    def everything_search(self, keywords, scopetargets=None, limit=50000):
        """<10ms Everything内存搜索"""
        if not self.is_ready or self.total_files == 0:
            return []
        
        start_time = time.time()
        results = []
        
        # 处理关键词
        if isinstance(keywords, str):
            kw_lower = keywords.lower().split()
        elif isinstance(keywords, list):
            kw_lower = [kw.lower() for kw in keywords]
        else:
            kw_lower = []
        
        if not kw_lower:
            return []
        
        with self.lock:
            # 遍历name_to_paths索引
            for name_lower in self.name_to_paths:
                # 多关键词AND匹配
                if all(kw in name_lower for kw in kw_lower):
                    for fullpath in self.name_to_paths[name_lower]:
                        if fullpath in self.path_to_info:
                            size, mtime, isdir = self.path_to_info[fullpath]
                            filename = Path(fullpath).name
                            results.append((filename, fullpath, size, mtime, isdir))
                            
                            if len(results) >= limit:
                                break
                    
                    if len(results) >= limit:
                        break
        
        elapsed = (time.time() - start_time) * 1000
        logger.info(f"Everything搜索: {len(results)}结果 ({elapsed:.1f}ms)")
        return results[:limit]
    
    def search(self, keywords, scopetargets, limit=50000):
        """统一搜索接口：Everything优先，SQLite降级"""
        # Everything内存搜索（优先）
        if self.is_ready and self.total_files > 0:
            return self.everything_search(keywords, scopetargets, limit)
        
        # SQLite降级搜索
        return self.sqlite_search(keywords, scopetargets, limit)
    
    def sqlite_search(self, keywords, scopetargets, limit=50000):
        """原SQLite搜索（降级模式）"""
        if not self.conn or self.filecount == 0:
            return []
        
        try:
            with self.lock:
                cursor = self.conn.cursor()
                
                # 处理关键词
                if isinstance(keywords, str):
                    kw_list = keywords.split()
                elif isinstance(keywords, list):
                    kw_list = keywords
                else:
                    return []
                
                # 构建WHERE子句
                wheres = " AND ".join(["filenamelower LIKE ?"] * len(kw_list))
                sql = f"SELECT filename, fullpath, size, mtime, isdir FROM files WHERE {wheres} LIMIT ?"
                params = tuple([f'%{kw}%' for kw in kw_list] + [limit])
                
                rawresults = list(cursor.execute(sql, params))
                
                # 过滤scope
                filtered = []
                scopetargets_lower = [os.path.normpath(t.lower()) for t in scopetargets] if scopetargets else None
                
                for row in rawresults:
                    pathlower = os.path.normpath(row[1].lower())
                    
                    if scopetargets_lower:
                        pathdrive = Path(pathlower).drive.lower().rstrip(':')
                        if pathdrive in [Path(s).drive.lower().rstrip(':') for s in scopetargets_lower]:
                            pass
                        elif any(pathlower.startswith(allowed) for allowed in scopetargets_lower):
                            pass
                        else:
                            continue
                    
                    if should_skip_path(pathlower):
                        continue
                    
                    filtered.append(row)
                
                return filtered
        except Exception as e:
            logger.error(f"SQLite搜索失败: {e}")
            return []
    
    def getstats(self):
        """获取索引统计"""
        self.loadstats(preservemft=True)
        return {
            'count': self.total_files if self.total_files > 0 else self.filecount,
            'ready': self.is_ready,
            'building': self.isbuilding,
            'time': self.lastbuildtime,
            'path': self.dbpath,
            'hasfts': self.hasfts,
            'usedmft': self.usedmft,
            'everything': self.is_ready and self.total_files > 0,
        }
    
    def close(self):
        """关闭数据库连接"""
        with self.lock:
            if self.conn:
                try:
                    self.conn.close()
                    logger.info("数据库已关闭")
                except Exception as e:
                    logger.warning(f"数据库关闭失败: {e}")
                finally:
                    self.conn = None
    def buildindex(self, drives, stopfn=None):
        """索引构建：Everything内存索引 + SQLite后台双写"""
        global MFTAVAILABLE
        
        if not self.conn or self.isbuilding:
            return
        
        self.isbuilding = True
        self.is_ready = False
        self.usedmft = False
        MFTAVAILABLE = False
        buildstart = time.time()
        
        try:
            logger.info("=" * 60)
            logger.info("开始索引构建...")
            logger.info("=" * 60)
            
            # 阶段1：清空旧索引
            self.progressSignal.emit(0, "阶段1: 清空旧索引...")
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute("DROP TRIGGER IF EXISTS files_ai")
                cursor.execute("DROP TRIGGER IF EXISTS files_ad")
                cursor.execute("DROP TABLE IF EXISTS files_fts")
                cursor.execute("DROP TABLE IF EXISTS files")
                cursor.execute("""CREATE TABLE files (
                    id INTEGER PRIMARY KEY,
                    filename TEXT NOT NULL,
                    filenamelower TEXT NOT NULL,
                    fullpath TEXT UNIQUE NOT NULL,
                    parentdir TEXT NOT NULL,
                    extension TEXT,
                    size INTEGER DEFAULT 0,
                    mtime REAL DEFAULT 0,
                    isdir INTEGER DEFAULT 0
                )""")
                if not HASAPSW:
                    self.conn.commit()
                
                # 清空Everything内存索引
                self.name_to_paths.clear()
                self.path_to_info.clear()
                self.total_files = 0
                
                self.hasfts = False
                self.filecount = 0
            
            logger.info(f"阶段1完成: {time.time() - buildstart:.2f}s")
            
            # 阶段2：MFT枚举
            self.progressSignal.emit(0, "阶段2: MFT扫描中...")
            alldrives = [d.upper().rstrip('\\') for d in drives if os.path.exists(d)]
            c_allowed_paths = get_c_scan_dirs(self.configmgr)
            alldata = []
            faileddrives = []
            
            if alldrives and ISWINDOWS:
                datalock = threading.Lock()
                
                def scanone(drv):
                    try:
                        # C盘使用限定路径，其他盘全盘扫描
                        allowed = c_allowed_paths if drv == 'C:' else None
                        data = enumvolumefilesmft(drv, SKIPDIRSLOWER, SKIPEXTS, allowedpaths=allowed)
                        with datalock:
                            alldata.extend(data)
                        return (drv, len(data))
                    except Exception as e:
                        logger.error(f"{drv} 枚举失败: {e}")
                        return (drv, -1)
                
                # 并行扫描多个驱动器
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(alldrives), 4)) as ex:
                    futures = [ex.submit(scanone, d) for d in alldrives]
                    for future in concurrent.futures.as_completed(futures):
                        if stopfn and stopfn():
                            break
                        drv, result = future.result()
                        if result < 0:
                            faileddrives.append(drv)
                        self.progressSignal.emit(
                            len(alldata),
                            f"MFT {drv}: {result if result >= 0 else '失败'}"
                        )
                
                if alldata:
                    self.usedmft = True
                    logger.info(f"阶段2完成: {time.time() - buildstart:.2f}s, 共{len(alldata)}项")
            
            # 阶段2B：失败驱动器降级os.walk扫描
            if faileddrives:
                logger.info(f"失败驱动器使用os.walk降级扫描: {faileddrives}")
                for drv in faileddrives:
                    if stopfn and stopfn():
                        break
                    pathstoscan = c_allowed_paths if drv == 'C:' else [f"{drv}\\"]
                    for path in pathstoscan:
                        logger.info(f"os.walk扫描: {path}")
                        self.scandir_fallback(path, c_allowed_paths if drv == 'C:' else None, stopfn)
            
            # 阶段3：填充Everything内存索引
            if alldata:
                self.progressSignal.emit(len(alldata), "阶段3: 构建Everything内存索引...")
                logger.info("开始填充Everything内存索引...")
                
                for item in alldata:
                    name, name_lower, fullpath, parentdir, ext, size, mtime, isdir = item
                    self.add_to_everything_index(name, fullpath, size, mtime, bool(isdir))
                
                self.is_ready = True
                logger.info(f"Everything索引完成: {self.total_files:,}文件")
                
                # 保存快照
                self.save_everything_snapshot()
            
            # 阶段4：写入SQLite（后台，不阻塞）
            if alldata:
                self.progressSignal.emit(len(alldata), "阶段4: 写入SQLite数据库...")
                writestart = time.time()
                
                with self.lock:
                    cursor = self.conn.cursor()
                    
                    # SQLite性能优化
                    cursor.execute("PRAGMA synchronous=OFF")
                    cursor.execute("PRAGMA journal_mode=MEMORY")
                    cursor.execute("PRAGMA locking_mode=EXCLUSIVE")
                    cursor.execute("PRAGMA temp_store=MEMORY")
                    cursor.execute("PRAGMA cache_size=-50000")
                    cursor.execute("PRAGMA mmap_size=268435456")
                    
                    # 批量插入
                    if HASAPSW:
                        cursor.executemany(
                            "INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)",
                            alldata
                        )
                    else:
                        cursor.executemany(
                            "INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)",
                            alldata
                        )
                        self.conn.commit()
                    
                    self.filecount = len(alldata)
                    writetime = time.time() - writestart
                    logger.info(f"阶段4完成: {writetime:.2f}s, 写入{len(alldata)}项")
            
            # 阶段5：创建索引
            self.progressSignal.emit(self.filecount, "阶段5: 创建数据库索引...")
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_fn ON files(filenamelower)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_parent ON files(parentdir)")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA locking_mode=NORMAL")
            
            logger.info(f"阶段5完成: {time.time() - buildstart:.2f}s")
            
            # 阶段6：保存元数据
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('buildtime', ?)",
                    (str(time.time()),)
                )
                cursor.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('usedmft', ?)",
                    ('1' if self.usedmft else '0',)
                )
                if not HASAPSW:
                    self.conn.commit()
            
            logger.info(f"阶段6完成: {time.time() - buildstart:.2f}s")
            
            # 更新统计
            self.loadstats(preservemft=True)
            self.is_ready = self.filecount > 0 or self.total_files > 0
            
            logger.info("=" * 60)
            logger.info(f"✓ 索引构建完成: {self.total_files or self.filecount:,}文件, 总耗时{time.time() - buildstart:.2f}s")
            logger.info("=" * 60)
            
            self.buildFinishedSignal.emit()
            
        except Exception as e:
            import traceback
            logger.error(f"索引构建失败: {e}")
            traceback.print_exc()
        finally:
            self.isbuilding = False
    
    def scandir_fallback(self, target, allowedpaths=None, stopfn=None):
        """os.walk降级扫描（用于MFT失败的情况）"""
        try:
            if not os.path.exists(target):
                return
        except (OSError, PermissionError):
            logger.warning(f"无法访问: {target}")
            return
        
        allowedpaths_lower = [p.lower().rstrip('\\') for p in allowedpaths] if allowedpaths else None
        batch = []
        stack = deque([target])
        
        while stack:
            if stopfn and stopfn():
                break
            
            cur = stack.pop()
            
            if should_skip_path(cur.lower(), allowedpaths_lower):
                continue
            
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        if stopfn and stopfn():
                            break
                        
                        if not e.name or e.name.startswith(('.', '$')):
                            continue
                        
                        try:
                            isdir = e.is_dir()
                            st = e.stat(follow_symlinks=False)
                        except (OSError, PermissionError):
                            continue
                        
                        pathlower = e.path.lower()
                        
                        if isdir:
                            if should_skip_dir(e.name.lower(), pathlower, allowedpaths_lower):
                                continue
                            stack.append(e.path)
                            batch.append([
                                e.name, e.name.lower(), e.path, cur,
                                '', 0, 0, 1
                            ])
                        else:
                            ext = os.path.splitext(e.name)[1].lower()
                            if ext in SKIPEXTS:
                                continue
                            batch.append([
                                e.name, e.name.lower(), e.path, cur,
                                ext, st.st_size, st.st_mtime, 0
                            ])
                        
                        # 批量写入
                        if len(batch) >= 1000:
                            self.write_batch_to_db(batch)
                            # 同时更新Everything索引
                            for item in batch:
                                name, _, fullpath, _, _, size, mtime, isdir = item
                                self.add_to_everything_index(name, fullpath, size, mtime, bool(isdir))
                            batch.clear()
            
            except (PermissionError, OSError):
                continue
        
        # 写入剩余批次
        if batch:
            self.write_batch_to_db(batch)
            for item in batch:
                name, _, fullpath, _, _, size, mtime, isdir = item
                self.add_to_everything_index(name, fullpath, size, mtime, bool(isdir))
    
    def write_batch_to_db(self, batch):
        """批量写入数据库"""
        with self.lock:
            cursor = self.conn.cursor()
            cursor.executemany(
                "INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)",
                batch
            )
            if not HASAPSW:
                self.conn.commit()
            self.filecount += len(batch)
            self.progressSignal.emit(self.filecount, f"扫描中: {self.filecount}文件")

# ========== 搜索Worker线程 ==========
class IndexSearchWorker(QThread):
    """索引搜索Worker - 后台线程"""
    batchready = Signal(list)
    finished = Signal(float)
    error = Signal(str)
    
    def __init__(self, indexmgr, keyword, scopetargets, regexmode, fuzzymode):
        super().__init__()
        self.indexmgr = indexmgr
        self.keywordstr = keyword
        self.keywords = keyword.lower().split()
        self.scopetargets = scopetargets
        self.regexmode = regexmode
        self.fuzzymode = fuzzymode
        self.stopped = False
    
    def stop(self):
        self.stopped = True
    
    def match(self, filename):
        """匹配文件名"""
        if self.regexmode:
            try:
                return re.search(self.keywordstr, filename, re.IGNORECASE)
            except re.error:
                return False
        
        if self.fuzzymode:
            return all(fuzzymatch(kw, filename) >= 50 for kw in self.keywords)
        
        return all(kw in filename.lower() for kw in self.keywords)
    
    def run(self):
        """执行搜索"""
        starttime = time.time()
        try:
            results = self.indexmgr.search(self.keywords, self.scopetargets)
            if results is None:
                self.error.emit("搜索失败")
                return
            
            batch = []
            for fn, fp, sz, mt, isdir in results:
                if self.stopped:
                    return
                
                if not self.match(fn):
                    continue
                
                ext = os.path.splitext(fn)[1].lower()
                tc = 0 if isdir else (1 if ext in ARCHIVEEXTS else 2)
                
                batch.append({
                    'filename': fn,
                    'fullpath': fp,
                    'dirpath': os.path.dirname(fp),
                    'size': sz,
                    'mtime': mt,
                    'typecode': tc,
                    'sizestr': format_size(sz) if tc != 0 else '<文件夹>',
                    'mtimestr': format_time(mt),
                })
                
                if len(batch) >= 200:
                    self.batchready.emit(list(batch))
                    batch.clear()
            
            if batch:
                self.batchready.emit(batch)
            
            self.finished.emit(time.time() - starttime)
        
        except Exception as e:
            logger.error(f"搜索错误: {e}")
            self.error.emit(str(e))

class RealtimeSearchWorker(QThread):
    """实时搜索Worker - os.walk扫描"""
    batchready = Signal(list)
    progress = Signal(int, float)
    finished = Signal(float)
    error = Signal(str)
    
    def __init__(self, keyword, scopetargets, regexmode, fuzzymode):
        super().__init__()
        self.keywordstr = keyword
        self.keywords = keyword.lower().split()
        self.scopetargets = scopetargets
        self.regexmode = regexmode
        self.fuzzymode = fuzzymode
        self.stopped = False
        self.ispaused = False
    
    def stop(self):
        self.stopped = True
    
    def togglepause(self, paused):
        self.ispaused = paused
    
    def match(self, filename):
        """匹配文件名"""
        if self.regexmode:
            try:
                return re.search(self.keywordstr, filename, re.IGNORECASE)
            except re.error:
                return False
        
        if self.fuzzymode:
            return all(fuzzymatch(kw, filename) >= 50 for kw in self.keywords)
        
        return all(kw in filename.lower() for kw in self.keywords)
    
    def run(self):
        """执行实时搜索"""
        starttime = time.time()
        try:
            taskqueue = queue.Queue()
            
            # 初始化任务队列
            for t in self.scopetargets:
                if os.path.isdir(t):
                    taskqueue.put(t)
            
            activethreads = [0]
            lock = threading.Lock()
            scanneddirs = [0]
            
            def worker():
                localbatch = []
                while not self.stopped:
                    # 暂停控制
                    while self.ispaused:
                        if self.stopped:
                            return
                        time.sleep(0.1)
                    
                    try:
                        cur = taskqueue.get(timeout=0.1)
                    except queue.Empty:
                        with lock:
                            if taskqueue.empty() and activethreads[0] == 1:
                                break
                        continue
                    
                    with lock:
                        activethreads[0] += 1
                        scanneddirs[0] += 1
                    
                    if should_skip_path(cur.lower()):
                        with lock:
                            activethreads[0] -= 1
                        continue
                    
                    try:
                        with os.scandir(cur) as it:
                            for e in it:
                                if self.stopped:
                                    return
                                
                                if not e.name or e.name.startswith(('.', '$')):
                                    continue
                                
                                try:
                                    isdir = e.is_dir()
                                    st = e.stat(follow_symlinks=False)
                                except (OSError, PermissionError):
                                    continue
                                
                                if self.match(e.name):
                                    ext = os.path.splitext(e.name)[1].lower()
                                    tc = 0 if isdir else (1 if ext in ARCHIVEEXTS else 2)
                                    
                                    localbatch.append({
                                        'filename': e.name,
                                        'fullpath': e.path,
                                        'dirpath': cur,
                                        'size': st.st_size,
                                        'mtime': st.st_mtime,
                                        'typecode': tc,
                                        'sizestr': format_size(st.st_size) if tc != 0 else '<文件夹>',
                                        'mtimestr': format_time(st.st_mtime),
                                    })
                                
                                if isdir and not should_skip_dir(e.name.lower()):
                                    taskqueue.put(e.path)
                                
                                if len(localbatch) >= 50:
                                    self.batchready.emit(list(localbatch))
                                    localbatch.clear()
                                    elapsed = time.time() - starttime
                                    speed = scanneddirs[0] / elapsed if elapsed > 0 else 0
                                    self.progress.emit(scanneddirs[0], speed)
                    
                    except (PermissionError, OSError):
                        pass
                    
                    with lock:
                        activethreads[0] -= 1
                
                if localbatch:
                    self.batchready.emit(localbatch)
            
            # 启动多个worker线程
            threads = [threading.Thread(target=worker, daemon=True) for _ in range(16)]
            for t in threads:
                t.start()
            
            for t in threads:
                t.join()
            
            if not self.stopped:
                self.finished.emit(time.time() - starttime)
        
        except Exception as e:
            logger.error(f"实时搜索错误: {e}")
            self.error.emit(str(e))

            # ========== 主搜索应用 ==========
class SearchApp(QMainWindow):
    """主搜索应用窗口 - 完整UI实现"""
    def __init__(self, dbpath=None):
        super().__init__()
        
        # 配置和索引管理器
        self.configmgr = ConfigManager()
        self.indexmgr = EverythingIndexManager(dbpath, self.configmgr)
        
        # 搜索状态
        self.resultslock = threading.Lock()
        self.issearching = False
        self.ispaused = False
        self.stopevent = False
        self.totalfound = 0
        self.allresults = []
        self.filteredresults = []
        self.shownpaths = set()
        self.worker = None
        
        # 分页
        self.pagesize = 1000
        self.currentpage = 1
        self.totalpages = 1
        
        # UI元素引用
        self.itemmeta = {}
        self.lastsearchparams = None
        
        # 窗口设置
        self.setWindowTitle("V42 Everything - 文件搜索器")
        self.resize(1400, 900)
        
        # 初始化UI
        self.init_ui()
        
        # 检查索引状态
        QTimer.singleShot(500, self.checkindex)
    
    def init_ui(self):
        """初始化UI界面"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(5)
        
        # === 搜索栏 ===
        search_frame = QFrame()
        search_layout = QHBoxLayout(search_frame)
        search_layout.setContentsMargins(0, 0, 0, 0)
        
        # 搜索输入框
        self.entrykw = QLineEdit()
        self.entrykw.setPlaceholderText("输入关键字搜索... (支持空格分隔多个关键词)")
        self.entrykw.setFont(QFont("Consolas", 11))
        self.entrykw.returnPressed.connect(self.startsearch)
        search_layout.addWidget(self.entrykw, 1)
        
        # 搜索按钮
        self.btnsearch = QPushButton("🔍 搜索")
        self.btnsearch.setFont(QFont("", 10))
        self.btnsearch.clicked.connect(self.startsearch)
        search_layout.addWidget(self.btnsearch)
        
        # 暂停按钮
        self.btnpause = QPushButton("⏸ 暂停")
        self.btnpause.setEnabled(False)
        self.btnpause.clicked.connect(self.togglepause)
        search_layout.addWidget(self.btnpause)
        
        # 停止按钮
        self.btnstop = QPushButton("⏹ 停止")
        self.btnstop.setEnabled(False)
        self.btnstop.clicked.connect(self.stopsearch)
        search_layout.addWidget(self.btnstop)
        
        # 刷新按钮
        self.btnrefresh = QPushButton("🔄 重建索引")
        self.btnrefresh.clicked.connect(self.rebuild_drives)
        search_layout.addWidget(self.btnrefresh)
        
        main_layout.addWidget(search_frame)
        
        # === 过滤器栏 ===
        filter_frame = QFrame()
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(0, 5, 0, 5)
        
        filter_layout.addWidget(QLabel("类型:"))
        self.extvar = QComboBox()
        self.extvar.addItem("全部")
        self.extvar.currentTextChanged.connect(self.applyfilter)
        filter_layout.addWidget(self.extvar)
        
        filter_layout.addWidget(QLabel("大小:"))
        self.sizevar = QComboBox()
        self.sizevar.addItems(["全部", "0", ">1MB", ">10MB", ">100MB", ">500MB", ">1GB"])
        self.sizevar.currentTextChanged.connect(self.applyfilter)
        filter_layout.addWidget(self.sizevar)
        
        filter_layout.addWidget(QLabel("日期:"))
        self.datevar = QComboBox()
        self.datevar.addItems(["全部", "今天", "最近3天", "最近7天", "最近30天", "今年"])
        self.datevar.currentTextChanged.connect(self.applyfilter)
        filter_layout.addWidget(self.datevar)
        
        self.btnfilter = QPushButton("清除过滤")
        self.btnfilter.clicked.connect(self.clearfilter)
        filter_layout.addWidget(self.btnfilter)
        
        self.lblfilter = QLabel("")
        self.lblfilter.setStyleSheet("color: #666;")
        filter_layout.addWidget(self.lblfilter, 1)
        
        main_layout.addWidget(filter_frame)
        
        # === 结果树 ===
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["文件名", "路径", "大小", "修改时间", "类型"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setSortingEnabled(True)
        self.tree.itemDoubleClicked.connect(self.ondblclick)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.showmenu)
        
        # 设置列宽
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.tree.setColumnWidth(0, 300)
        
        main_layout.addWidget(self.tree)
        
        # === 分页控制 ===
        page_frame = QFrame()
        page_layout = QHBoxLayout(page_frame)
        page_layout.setContentsMargins(0, 5, 0, 0)
        
        self.btnprevpage = QPushButton("◀ 上一页")
        self.btnprevpage.clicked.connect(self.prevpage)
        page_layout.addWidget(self.btnprevpage)
        
        self.pagelabel = QLabel("第 1 页 / 共 1 页")
        self.pagelabel.setAlignment(Qt.AlignCenter)
        page_layout.addWidget(self.pagelabel, 1)
        
        self.btnnextpage = QPushButton("下一页 ▶")
        self.btnnextpage.clicked.connect(self.nextpage)
        page_layout.addWidget(self.btnnextpage)
        
        main_layout.addWidget(page_frame)
        
        # === 状态栏 ===
        self.status = QLabel("准备就绪")
        self.statusBar().addWidget(self.status, 1)
        
        self.statuspath = QLabel("")
        self.statusBar().addWidget(self.statuspath)
        
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setMaximumWidth(200)
        self.statusBar().addPermanentWidget(self.progress)
        
        # === 菜单栏 ===
        self.buildmenubar()
    
    def buildmenubar(self):
        """构建菜单栏"""
        menubar = self.menuBar()
        
        # 文件菜单
        filemenu = menubar.addMenu("文件(&F)")
        
        action_rebuild = QAction("重建索引", self)
        action_rebuild.setShortcut(QKeySequence("Ctrl+R"))
        action_rebuild.triggered.connect(self.rebuild_drives)
        filemenu.addAction(action_rebuild)
        
        filemenu.addSeparator()
        
        action_exit = QAction("退出", self)
        action_exit.setShortcut(QKeySequence("Ctrl+Q"))
        action_exit.triggered.connect(self.close)
        filemenu.addAction(action_exit)
        
        # 工具菜单
        toolmenu = menubar.addMenu("工具(&T)")
        
        action_stats = QAction("索引统计", self)
        action_stats.triggered.connect(self.showstats)
        toolmenu.addAction(action_stats)
        
        # 帮助菜单
        helpmenu = menubar.addMenu("帮助(&H)")
        
        action_about = QAction("关于", self)
        action_about.triggered.connect(self.showabout)
        helpmenu.addAction(action_about)
    
    def checkindex(self):
        """检查索引状态"""
        # 尝试加载Everything快照
        if self.indexmgr.load_everything_snapshot():
            self.status.setText(f"✓ Everything就绪: {self.indexmgr.total_files:,} 文件")
            self.entrykw.setFocus()
        else:
            # 检查SQLite索引
            stats = self.indexmgr.getstats()
            if stats['count'] > 0:
                self.status.setText(f"索引: {stats['count']:,} 文件 (建议重建以启用Everything模式)")
            else:
                self.status.setText("首次启动，请点击「重建索引」")
                QMessageBox.information(
                    self,
                    "欢迎",
                    "首次使用需要构建索引，请点击「重建索引」按钮\n\n"
                    "Everything模式特性：\n"
                    "• 启动时间 <3秒\n"
                    "• 搜索延迟 <10ms\n"
                    "• 内存占用 50-100MB\n"
                    "• 支持实时更新"
                )
    
    def startsearch(self):
        """开始搜索"""
        kw = self.entrykw.text().strip()
        if not kw:
            return
        
        # 保存搜索历史
        self.configmgr.add_history(kw)
        
        # 清空之前的结果
        self.tree.clear()
        self.itemmeta.clear()
        self.totalfound = 0
        self.currentpage = 1
        
        with self.resultslock:
            self.allresults.clear()
            self.filteredresults.clear()
            self.shownpaths.clear()
        
        self.issearching = True
        self.stopevent = False
        
        # 禁用按钮
        self.btnsearch.setEnabled(False)
        self.btnpause.setEnabled(True)
        self.btnstop.setEnabled(True)
        self.btnrefresh.setEnabled(False)
        
        # 显示进度条
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        
        self.status.setText("搜索中...")
        
        # 获取搜索范围
        scopetargets = self.get_search_scope_targets()
        
        # 使用索引搜索（Everything模式）
        useidx = self.indexmgr.is_ready and not self.indexmgr.isbuilding
        
        if useidx:
            self.status.setText("Everything搜索中...")
            self.worker = IndexSearchWorker(
                self.indexmgr, kw, scopetargets,
                False, False  # regexmode, fuzzymode
            )
        else:
            self.status.setText("实时搜索中...")
            self.worker = RealtimeSearchWorker(
                kw, scopetargets,
                False, False  # regexmode, fuzzymode
            )
            self.worker.progress.connect(self.onrtprogress)
        
        self.worker.batchready.connect(self.onbatchready)
        self.worker.finished.connect(self.onsearchfinished)
        self.worker.error.connect(self.onsearcherror)
        self.worker.start()
        
        self.lastsearchparams = {'kw': kw}
    
    def get_search_scope_targets(self):
        """获取搜索范围"""
        # 默认搜索所有驱动器
        drives = []
        for letter in string.ascii_uppercase:
            drive = f"{letter}:"
            if os.path.exists(drive):
                drives.append(drive)
        return drives
    
    def onbatchready(self, batch):
        """处理搜索结果批次"""
        with self.resultslock:
            for itemdata in batch:
                if itemdata['fullpath'] not in self.shownpaths:
                    self.shownpaths.add(itemdata['fullpath'])
                    self.allresults.append(itemdata)
            
            self.totalfound = len(self.allresults)
            self.filteredresults = list(self.allresults)
        
        # 更新显示
        if self.totalfound <= 200 or time.time() - getattr(self, 'lastrendertime', 0) > 0.15:
            self.renderpage()
            self.lastrendertime = time.time()
        
        self.status.setText(f"找到 {self.totalfound} 个结果...")
    
    def onrtprogress(self, scanneddirs, speed):
        """实时搜索进度"""
        self.status.setText(f"扫描中... {scanneddirs} 个目录, {speed:.0f} 目录/s")
    
    def onsearchfinished(self, totaltime):
        """搜索完成"""
        self.resetui()
        self.finalize()
        self.status.setText(f"找到 {self.totalfound} 个结果 ({totaltime:.2f}s)")
    
    def onsearcherror(self, errormsg):
        """搜索错误"""
        self.resetui()
        QMessageBox.warning(self, "搜索错误", errormsg)
    
    def finalize(self):
        """完成搜索后的处理"""
        self.updateextcombo()
        with self.resultslock:
            self.filteredresults = list(self.allresults)
        self.renderpage()
    
    def updateextcombo(self):
        """更新扩展名下拉框"""
        counts = {}
        with self.resultslock:
            for item in self.allresults:
                if item['typecode'] == 0:
                    ext = "<文件夹>"
                elif item['typecode'] == 1:
                    ext = "<压缩包>"
                else:
                    ext = os.path.splitext(item['filename'])[1].lower() or "<无扩展名>"
                counts[ext] = counts.get(ext, 0) + 1
        
        values = [f"{ext} ({cnt})" for ext, cnt in sorted(counts.items(), key=lambda x: -x[1])[:30]]
        
        self.extvar.clear()
        self.extvar.addItem("全部")
        self.extvar.addItems(values)
    
    def renderpage(self):
        """渲染当前页"""
        self.tree.clear()
        self.itemmeta.clear()
        
        with self.resultslock:
            total = len(self.filteredresults)
            self.totalpages = max(1, (total + self.pagesize - 1) // self.pagesize)
            self.currentpage = min(self.currentpage, self.totalpages)
            
            start = (self.currentpage - 1) * self.pagesize
            end = min(start + self.pagesize, total)
            
            page_items = self.filteredresults[start:end]
        
        for idx, itemdata in enumerate(page_items):
            tc = itemdata['typecode']
            icon = "📁" if tc == 0 else ("📦" if tc == 1 else "📄")
            
            item = QTreeWidgetItem([
                f"{icon} {itemdata['filename']}",
                itemdata['fullpath'],
                itemdata['sizestr'],
                itemdata['mtimestr'],
                "文件夹" if tc == 0 else ("压缩包" if tc == 1 else "文件")
            ])
            
            self.tree.addTopLevelItem(item)
            self.itemmeta[id(item)] = start + idx
        
        self.pagelabel.setText(f"第 {self.currentpage} 页 / 共 {self.totalpages} 页")
        self.btnprevpage.setEnabled(self.currentpage > 1)
        self.btnnextpage.setEnabled(self.currentpage < self.totalpages)
    
    def prevpage(self):
        """上一页"""
        if self.currentpage > 1:
            self.currentpage -= 1
            self.renderpage()
    
    def nextpage(self):
        """下一页"""
        if self.currentpage < self.totalpages:
            self.currentpage += 1
            self.renderpage()
    
    def togglepause(self):
        """切换暂停状态"""
        if not self.issearching or not hasattr(self, 'worker'):
            return
        
        self.ispaused = not self.ispaused
        if hasattr(self.worker, 'togglepause'):
            self.worker.togglepause(self.ispaused)
        
        if self.ispaused:
            self.btnpause.setText("▶ 继续")
            self.progress.setRange(0, 100)
        else:
            self.btnpause.setText("⏸ 暂停")
            self.progress.setRange(0, 0)
    
    def stopsearch(self):
        """停止搜索"""
        if hasattr(self, 'worker') and self.worker:
            self.worker.stop()
        self.resetui()
        self.status.setText(f"已停止：找到 {self.totalfound} 个结果")
    
    def resetui(self):
        """重置UI状态"""
        self.issearching = False
        self.ispaused = False
        self.btnsearch.setEnabled(True)
        self.btnpause.setEnabled(False)
        self.btnpause.setText("⏸ 暂停")
        self.btnstop.setEnabled(False)
        self.btnrefresh.setEnabled(True)
        self.progress.setVisible(False)
    
    def applyfilter(self):
        """应用过滤器"""
        extsel = self.extvar.currentText()
        sizemin = self.getsizemin()
        datemin = self.getdatemin()
        
        targetext = extsel.split(' ')[0] if extsel != "全部" else None
        
        with self.resultslock:
            self.filteredresults.clear()
            for item in self.allresults:
                # 大小过滤
                if sizemin > 0 and item['typecode'] != 0 and item['size'] < sizemin:
                    continue
                
                # 日期过滤
                if datemin > 0 and item['mtime'] < datemin:
                    continue
                
                # 类型过滤
                if targetext:
                    if item['typecode'] == 0:
                        itemext = "<文件夹>"
                    elif item['typecode'] == 1:
                        itemext = "<压缩包>"
                    else:
                        itemext = os.path.splitext(item['filename'])[1].lower() or "<无扩展名>"
                    
                    if itemext != targetext:
                        continue
                
                self.filteredresults.append(item)
        
        self.currentpage = 1
        self.renderpage()
        
        with self.resultslock:
            allcount = len(self.allresults)
            filteredcount = len(self.filteredresults)
        
        if extsel != "全部" or sizemin > 0 or datemin > 0:
            self.lblfilter.setText(f"过滤后: {filteredcount}/{allcount}")
        else:
            self.lblfilter.setText("")
    
    def clearfilter(self):
        """清除过滤"""
        self.extvar.setCurrentText("全部")
        self.sizevar.setCurrentText("全部")
        self.datevar.setCurrentText("全部")
        with self.resultslock:
            self.filteredresults = list(self.allresults)
        self.currentpage = 1
        self.renderpage()
        self.lblfilter.setText("")
    
    def getsizemin(self):
        """获取最小大小"""
        mapping = {
            "全部": 0,
            ">1MB": 1 * 1024 * 1024,
            ">10MB": 10 * 1024 * 1024,
            ">100MB": 100 * 1024 * 1024,
            ">500MB": 500 * 1024 * 1024,
            ">1GB": 1 * 1024 * 1024 * 1024,
        }
        return mapping.get(self.sizevar.currentText(), 0)
    
    def getdatemin(self):
        """获取最早日期"""
        now = time.time()
        day = 86400
        mapping = {
            "全部": 0,
            "今天": now - day,
            "最近3天": now - 3 * day,
            "最近7天": now - 7 * day,
            "最近30天": now - 30 * day,
            "今年": time.mktime(datetime.datetime(datetime.datetime.now().year, 1, 1).timetuple()),
        }
        return mapping.get(self.datevar.currentText(), 0)
    
    def ondblclick(self, item, column):
        """双击打开文件/文件夹"""
        if not item:
            return
        
        idx = self.itemmeta.get(id(item))
        if idx is None:
            return
        
        with self.resultslock:
            if idx < 0 or idx >= len(self.filteredresults):
                return
            data = self.filteredresults[idx]
        
        fullpath = data['fullpath']
        
        if data['typecode'] == 0:  # 文件夹
            try:
                subprocess.Popen(['explorer', fullpath])
            except Exception as e:
                logger.error(f"打开文件夹失败: {e}")
                QMessageBox.warning(self, "错误", f"打开失败: {e}")
        else:  # 文件
            try:
                os.startfile(fullpath)
            except Exception as e:
                logger.error(f"打开文件失败: {e}")
                QMessageBox.warning(self, "错误", f"打开失败: {e}")
    
    def showmenu(self, pos):
        """显示右键菜单"""
        item = self.tree.itemAt(pos)
        if not item:
            return
        
        self.tree.setCurrentItem(item)
        
        idx = self.itemmeta.get(id(item))
        if idx is None:
            return
        
        with self.resultslock:
            if idx < 0 or idx >= len(self.filteredresults):
                return
            data = self.filteredresults[idx]
        
        fullpath = data['fullpath']
        
        ctxmenu = QMenu(self)
        ctxmenu.addAction("打开", lambda: self.openfile(fullpath))
        ctxmenu.addAction("打开文件夹", lambda: self.openfolder(fullpath))
        ctxmenu.addSeparator()
        ctxmenu.addAction("复制路径", lambda: self.copypath(fullpath))
        
        if HASSEND2TRASH:
            ctxmenu.addSeparator()
            ctxmenu.addAction("删除", lambda: self.deletefile(fullpath))
        
        ctxmenu.exec(self.tree.viewport().mapToGlobal(pos))
    
    def openfile(self, fullpath):
        """打开文件"""
        try:
            os.startfile(fullpath)
        except Exception as e:
            logger.error(f"打开失败: {e}")
            QMessageBox.warning(self, "错误", f"打开失败: {e}")
    
    def openfolder(self, fullpath):
        """打开文件所在文件夹"""
        try:
            subprocess.Popen(['explorer', '/select,', fullpath])
        except Exception as e:
            logger.error(f"打开文件夹失败: {e}")
            QMessageBox.warning(self, "错误", f"打开失败: {e}")
    
    def copypath(self, fullpath):
        """复制路径"""
        QApplication.clipboard().setText(fullpath)
        self.status.setText("路径已复制")
    
    def deletefile(self, fullpath):
        """删除文件"""
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除吗?\n\n{fullpath}",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            try:
                if HASSEND2TRASH:
                    send2trash.send2trash(fullpath)
                else:
                    if os.path.isdir(fullpath):
                        shutil.rmtree(fullpath)
                    else:
                        os.remove(fullpath)
                self.status.setText("删除成功")
            except Exception as e:
                logger.error(f"删除失败: {e}")
                QMessageBox.warning(self, "错误", f"删除失败: {e}")
    
    def rebuild_drives(self):
        """重建索引"""
        reply = QMessageBox.question(
            self, "确认重建",
            "重建索引将清空现有数据并重新扫描所有驱动器\n\n"
            "这可能需要5-10秒时间，是否继续？",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.status.setText("开始重建索引...")
            self.progress.setVisible(True)
            self.progress.setRange(0, 0)
            self.btnrefresh.setEnabled(False)
            
            self.indexmgr.progressSignal.connect(self.onbuildprogress)
            self.indexmgr.buildFinishedSignal.connect(self.onbuildfinished)
            
            # 获取所有驱动器
            drives = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:"
                if os.path.exists(drive):
                    drives.append(drive)
            
            # 后台线程构建
            threading.Thread(target=self.indexmgr.buildindex, args=(drives,), daemon=True).start()
    
    def onbuildprogress(self, count, message):
        """构建进度"""
        self.status.setText(f"构建中: {message} ({count})")
        self.statuspath.setText(message)
    
    def onbuildfinished(self):
        """构建完成"""
        self.indexmgr.forcereloadstats()
        self.checkindex()
        self.progress.setVisible(False)
        self.btnrefresh.setEnabled(True)
        
        stats = self.indexmgr.getstats()
        self.status.setText(f"✓ 索引构建完成: {stats['count']:,} 文件")
        
        QMessageBox.information(
            self, "完成",
            f"索引构建完成！\n\n"
            f"文件总数: {stats['count']:,}\n"
            f"Everything模式: {'是' if stats['everything'] else '否'}\n"
            f"使用MFT: {'是' if stats['usedmft'] else '否'}"
        )
    
    def showstats(self):
        """显示索引统计"""
        stats = self.indexmgr.getstats()
        
        msg = f"""索引统计信息

文件总数: {stats['count']:,}
索引状态: {'就绪' if stats['ready'] else '未就绪'}
构建中: {'是' if stats['building'] else '否'}
Everything模式: {'是' if stats['everything'] else '否'}
使用MFT: {'是' if stats['usedmft'] else '否'}
FTS5支持: {'是' if stats['hasfts'] else '否'}
数据库路径: {stats['path']}
"""
        
        if stats['time']:
            buildtime = datetime.datetime.fromtimestamp(stats['time']).strftime("%Y-%m-%d %H:%M:%S")
            msg += f"最后构建: {buildtime}\n"
        
        QMessageBox.information(self, "索引统计", msg)
    
    def showabout(self):
        """显示关于信息"""
        QMessageBox.about(
            self, "关于",
            "V42 Everything - 文件搜索器\n\n"
            "Everything级性能优化版\n"
            "• 启动时间 <3秒\n"
            "• 搜索延迟 <10ms\n"
            "• 内存占用 50-100MB\n"
            "• 支持MFT快速枚举\n"
            "• 内存索引 + SQLite双模式\n\n"
            "版本: 42.0\n"
            "基于PySide6 + ctypes + pickle"
        )
    
    def closeEvent(self, event):
        """关闭事件"""
        # 停止搜索
        if hasattr(self, 'worker') and self.worker:
            self.worker.stop()
        
        # 保存Everything快照
        if self.indexmgr.is_ready:
            self.indexmgr.save_everything_snapshot()
        
        # 关闭数据库
        self.indexmgr.close()
        
        event.accept()

        # ========== 主程序入口 ==========
def main():
    """主程序入口"""
    app = QApplication(sys.argv)
    
    # 设置应用信息
    app.setApplicationName("V42 Everything")
    app.setOrganizationName("FileSearch")
    app.setApplicationVersion("42.0")
    
    # 设置样式
    app.setStyle('Fusion')
    
    # 应用主题
    apply_theme(app, 'light')
    
    # 创建主窗口
    window = SearchApp()
    window.show()
    
    # 运行应用
    sys.exit(app.exec())

def apply_theme(app, themename):
    """应用主题"""
    if themename == 'dark':
        # 深色主题
        stylesheet = """
        QMainWindow, QDialog {
            background-color: #2d2d2d;
            color: #ffffff;
        }
        QTreeWidget {
            background-color: #3d3d3d;
            color: #ffffff;
            alternate-background-color: #454545;
        }
        QTreeWidget::item:selected {
            background-color: #0078d4;
        }
        QLineEdit, QComboBox, QSpinBox {
            background-color: #3d3d3d;
            color: #ffffff;
            border: 1px solid #555;
            padding: 4px;
        }
        QPushButton {
            background-color: #4d4d4d;
            color: #ffffff;
            border: 1px solid #666;
            padding: 5px 10px;
        }
        QPushButton:hover {
            background-color: #5d5d5d;
        }
        QPushButton:disabled {
            background-color: #3d3d3d;
            color: #888;
        }
        QLabel {
            color: #ffffff;
        }
        QGroupBox {
            color: #ffffff;
            border: 1px solid #555;
        }
        QCheckBox, QRadioButton {
            color: #ffffff;
        }
        QMenu {
            background-color: #3d3d3d;
            color: #ffffff;
        }
        QMenu::item:selected {
            background-color: #0078d4;
        }
        QStatusBar {
            background-color: #2d2d2d;
            color: #aaaaaa;
        }
        QHeaderView::section {
            background-color: #3d3d3d;
            color: #ffffff;
            padding: 4px;
            border: 1px solid #555;
        }
        QScrollBar {
            background-color: #2d2d2d;
        }
        QProgressBar {
            background-color: #3d3d3d;
            border: 1px solid #555;
            color: #ffffff;
        }
        QProgressBar::chunk {
            background-color: #0078d4;
        }
        """
        app.setStyleSheet(stylesheet)
    else:
        # 浅色主题（默认）
        stylesheet = """
        QMainWindow, QDialog {
            background-color: #ffffff;
        }
        QTreeWidget {
            alternate-background-color: #f8f9fa;
            border: 1px solid #dcdcdc;
        }
        QTreeWidget::item:selected {
            background-color: #0078d4;
            color: white;
        }
        QHeaderView::section {
            background-color: #f0f0f0;
            padding: 4px;
            border: 1px solid #dcdcdc;
            font-weight: bold;
        }
        QPushButton {
            padding: 5px 10px;
            border: 1px solid #ccc;
            background-color: #f0f0f0;
        }
        QPushButton:hover {
            background-color: #e0e0e0;
        }
        QPushButton:disabled {
            background-color: #f5f5f5;
            color: #888;
        }
        QLineEdit, QComboBox {
            padding: 4px;
            border: 1px solid #ccc;
        }
        QProgressBar {
            border: 1px solid #ccc;
            background-color: #f0f0f0;
        }
        QProgressBar::chunk {
            background-color: #0078d4;
        }
        """
        app.setStyleSheet(stylesheet)

# ========== 工具函数 ==========
def get_drives():
    """获取所有可用驱动器"""
    drives = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:"
        if os.path.exists(drive):
            drives.append(drive)
    return drives

def parse_search_scope(scopestr, getdrivesfn, configmgr=None):
    """解析搜索范围"""
    targets = []
    
    if '*' in scopestr:
        # 全部驱动器
        for d in getdrivesfn():
            if d.upper().startswith('C'):
                # C盘使用限定路径
                targets.extend(get_c_scan_dirs(configmgr))
            else:
                # 其他盘全盘
                norm = os.path.normpath(d.rstrip('\\'))
                targets.append(norm)
    else:
        s = scopestr.strip()
        if os.path.isdir(s):
            norm = os.path.normpath(s.rstrip('\\'))
            targets.append(norm)
        else:
            targets.append(s)
    
    return targets

# ========== 附加UI组件（可选） ==========
class CDriveSettingsDialog(QDialog):
    """C盘扫描路径设置对话框"""
    def __init__(self, parent, configmgr):
        super().__init__(parent)
        self.configmgr = configmgr
        self.setWindowTitle("C盘扫描设置")
        self.setMinimumSize(650, 500)
        self.setModal(True)
        
        self.originalpaths = [p.copy() for p in self.configmgr.get_c_scan_paths()]
        self.pathvars = {}
        
        self.buildui()
    
    def buildui(self):
        """构建UI"""
        mainlayout = QVBoxLayout(self)
        mainlayout.setContentsMargins(15, 15, 15, 15)
        mainlayout.setSpacing(10)
        
        # 说明
        desclabel = QLabel("配置C盘扫描路径（避免扫描系统目录）:")
        desclabel.setFont(QFont("", 9))
        desclabel.setStyleSheet("color: #666;")
        mainlayout.addWidget(desclabel)
        
        # 操作按钮
        btnrow = QHBoxLayout()
        titlelabel = QLabel("扫描路径:")
        titlelabel.setFont(QFont("", 10, QFont.Bold))
        btnrow.addWidget(titlelabel)
        btnrow.addStretch()
        
        browsebtn = QPushButton("浏览添加")
        browsebtn.clicked.connect(self.browseadd)
        btnrow.addWidget(browsebtn)
        
        manualbtn = QPushButton("手动添加")
        manualbtn.clicked.connect(self.manualadd)
        btnrow.addWidget(manualbtn)
        
        mainlayout.addLayout(btnrow)
        
        # 快捷操作
        quickrow = QHBoxLayout()
        
        selectallbtn = QPushButton("全选")
        selectallbtn.clicked.connect(self.selectall)
        quickrow.addWidget(selectallbtn)
        
        selectnonebtn = QPushButton("全不选")
        selectnonebtn.clicked.connect(self.selectnone)
        quickrow.addWidget(selectnonebtn)
        
        quickrow.addStretch()
        
        self.statlabel = QLabel()
        self.statlabel.setFont(QFont("", 9))
        self.statlabel.setStyleSheet("color: #666;")
        quickrow.addWidget(self.statlabel)
        
        mainlayout.addLayout(quickrow)
        
        # 路径列表（滚动区域）
        self.scrollarea = QScrollArea()
        self.scrollarea.setWidgetResizable(True)
        self.scrollarea.setStyleSheet("QScrollArea { background-color: #fafafa; border: 1px solid #ddd; }")
        
        self.pathsframe = QWidget()
        self.pathslayout = QVBoxLayout(self.pathsframe)
        self.pathslayout.setContentsMargins(5, 5, 5, 5)
        self.pathslayout.setSpacing(2)
        self.pathslayout.addStretch()
        
        self.scrollarea.setWidget(self.pathsframe)
        mainlayout.addWidget(self.scrollarea, 1)
        
        self.refreshpathslist()
        
        # 底部按钮
        bottomlayout = QHBoxLayout()
        
        resetbtn = QPushButton("恢复默认")
        resetbtn.clicked.connect(self.resetdefault)
        bottomlayout.addWidget(resetbtn)
        
        bottomlayout.addStretch()
        
        cancelbtn = QPushButton("取消")
        cancelbtn.clicked.connect(self.reject)
        bottomlayout.addWidget(cancelbtn)
        
        savebtn = QPushButton("保存")
        savebtn.clicked.connect(self.save)
        bottomlayout.addWidget(savebtn)
        
        mainlayout.addLayout(bottomlayout)
    
    def refreshpathslist(self):
        """刷新路径列表"""
        # 清空现有项
        while self.pathslayout.count() > 1:
            item = self.pathslayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        self.pathvars.clear()
        
        paths = self.configmgr.get_c_scan_paths()
        
        if not paths:
            emptylabel = QLabel("（空）点击上方按钮添加路径")
            emptylabel.setFont(QFont("", 9))
            emptylabel.setStyleSheet("color: gray;")
            self.pathslayout.insertWidget(0, emptylabel)
            self.updatestats()
            return
        
        for i, item in enumerate(paths):
            path = item.get('path', '')
            enabled = item.get('enabled', True)
            
            rowwidget = QWidget()
            rowlayout = QHBoxLayout(rowwidget)
            rowlayout.setContentsMargins(5, 2, 5, 2)
            rowlayout.setSpacing(8)
            
            # 复选框
            cb = QCheckBox()
            cb.setChecked(enabled)
            cb.stateChanged.connect(self.updatestats)
            self.pathvars[path] = cb
            rowlayout.addWidget(cb)
            
            # 路径标签
            pathexists = os.path.isdir(path)
            maxlen = 55
            if len(path) > maxlen:
                displaypath = path[:20] + '...' + path[-(maxlen - 23):]
            else:
                displaypath = path
            
            if not pathexists:
                displaypath = f"{displaypath} (不存在)"
            
            pathlabel = QLabel(displaypath)
            pathlabel.setFont(QFont("Consolas", 9))
            pathlabel.setStyleSheet(f"color: {'#333' if pathexists else 'red'};")
            pathlabel.setToolTip(path)
            rowlayout.addWidget(pathlabel, 1)
            
            # 删除按钮
            delbtn = QPushButton("×")
            delbtn.setFixedWidth(30)
            delbtn.setStyleSheet("color: red; font-weight: bold;")
            delbtn.clicked.connect(lambda checked, p=path: self.deletepath(p))
            rowlayout.addWidget(delbtn)
            
            self.pathslayout.insertWidget(i, rowwidget)
        
        self.updatestats()
    
    def selectall(self):
        """全选"""
        for cb in self.pathvars.values():
            cb.setChecked(True)
        self.updatestats()
    
    def selectnone(self):
        """全不选"""
        for cb in self.pathvars.values():
            cb.setChecked(False)
        self.updatestats()
    
    def updatestats(self):
        """更新统计"""
        total = len(self.pathvars)
        enabled = sum(1 for cb in self.pathvars.values() if cb.isChecked())
        self.statlabel.setText(f"共 {total} 条，已启用 {enabled} 条")
    
    def browseadd(self):
        """浏览添加"""
        path = QFileDialog.getExistingDirectory(self, "选择C盘扫描目录", "C:\\")
        if path:
            self.addpath(path)
    
    def manualadd(self):
        """手动添加"""
        text, ok = QInputDialog.getText(self, "手动添加", "输入路径:", QLineEdit.Normal, "")
        if ok and text:
            self.addpath(text.strip())
    
    def addpath(self, path):
        """添加路径"""
        path = os.path.normpath(path)
        
        if not path.upper().startswith('C'):
            QMessageBox.warning(self, "错误", "只能添加C盘路径")
            return False
        
        if not os.path.isdir(path):
            QMessageBox.warning(self, "错误", "路径不存在")
            return False
        
        paths = self.configmgr.get_c_scan_paths()
        for p in paths:
            if os.path.normpath(p['path']).lower() == path.lower():
                QMessageBox.warning(self, "提示", "路径已存在")
                return False
        
        paths.append({'path': path, 'enabled': True})
        self.configmgr.set_c_scan_paths(paths)
        self.refreshpathslist()
        return True
    
    def deletepath(self, path):
        """删除路径"""
        if QMessageBox.question(self, "确认", f"删除路径?\n{path}") == QMessageBox.Yes:
            paths = self.configmgr.get_c_scan_paths()
            paths = [p for p in paths if os.path.normpath(p['path']).lower() != os.path.normpath(path).lower()]
            self.configmgr.set_c_scan_paths(paths)
            self.refreshpathslist()
    
    def resetdefault(self):
        """恢复默认"""
        if QMessageBox.question(self, "确认", "恢复到默认配置?") == QMessageBox.Yes:
            self.configmgr.reset_c_scan_paths()
            self.refreshpathslist()
    
    def save(self):
        """保存"""
        paths = self.configmgr.get_c_scan_paths()
        for p in paths:
            path = p['path']
            if path in self.pathvars:
                p['enabled'] = self.pathvars[path].isChecked()
        
        self.configmgr.set_c_scan_paths(paths)
        
        # 检测是否有变化
        currentpaths = self.configmgr.get_c_scan_paths()
        haschanges = self.detectchanges(currentpaths)
        
        if haschanges:
            result = QMessageBox.question(
                self,
                "重建索引",
                "C盘扫描配置已更改，需要重建索引才能生效\n\n是否现在重建?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )
            
            if result == QMessageBox.Yes:
                self.accept()
                # 触发重建（由父窗口处理）
            elif result == QMessageBox.No:
                QMessageBox.information(self, "提示", "配置已保存，下次重建索引时生效")
                self.accept()
        else:
            QMessageBox.information(self, "提示", "配置已保存")
            self.accept()
    
    def detectchanges(self, currentpaths):
        """检测配置是否有变化"""
        if len(currentpaths) != len(self.originalpaths):
            return True
        
        for curr, orig in zip(currentpaths, self.originalpaths):
            if curr.get('path') != orig.get('path'):
                return True
            if curr.get('enabled') != orig.get('enabled'):
                return True
        
        return False

# ========== 程序入口 ==========
if __name__ == "__main__":
    # 设置高DPI支持
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    # 启动主程序
    try:
        main()
    except Exception as e:
        logger.error(f"程序崩溃: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)




    


