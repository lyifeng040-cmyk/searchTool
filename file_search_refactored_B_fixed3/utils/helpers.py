"""顶层工具函数：从原版提取，逻辑不改。"""
from __future__ import annotations
from .constants import *

def get_c_scan_dirs(config_mgr=None):
    """获取C盘扫描目录列表"""
    if config_mgr:
        return config_mgr.get_enabled_c_paths()

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
        if path_lower.startswith(ap + "\\") or path_lower == ap:
            return True
    return False

def should_skip_path(path_lower, allowed_paths_lower=None):
    """检查路径是否应该跳过"""
    if allowed_paths_lower and is_in_allowed_paths(path_lower, allowed_paths_lower):
        return False

    path_parts = path_lower.replace("/", "\\").split("\\")
    for part in path_parts:
        if part in SKIP_DIRS_LOWER:
            return True

    if "site-packages" in path_lower:
        return True
    if CAD_PATTERN.search(path_lower):
        return True
    if AUTOCAD_PATTERN.search(path_lower):
        return True
    if "tangent" in path_lower:
        return True

    return False

def should_skip_dir(name_lower, path_lower=None, allowed_paths_lower=None):
    """检查目录是否应该跳过"""
    if CAD_PATTERN.search(name_lower):
        return True
    if AUTOCAD_PATTERN.search(name_lower):
        return True
    if "tangent" in name_lower:
        return True

    if path_lower and allowed_paths_lower:
        if is_in_allowed_paths(path_lower, allowed_paths_lower):
            return False

    if name_lower in SKIP_DIRS_LOWER:
        return True

    return False

def format_size(size):
    """格式化文件大小"""
    if size <= 0:
        return "-"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

def format_time(timestamp):
    """格式化时间戳"""
    if timestamp <= 0:
        return "-"
    try:
        return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError) as e:
        logger.warning(f"时间戳格式化失败: {timestamp}, {e}")
        return "-"

def parse_search_scope(scope_str, get_drives_fn, config_mgr=None):
    """统一解析搜索范围"""
    targets = []
    if "所有磁盘" in scope_str:
        for d in get_drives_fn():
            if d.upper().startswith("C:"):
                targets.extend(get_c_scan_dirs(config_mgr))
            else:
                norm = os.path.normpath(d).rstrip("\\/ ")
                targets.append(norm)
    else:
        s = scope_str.strip()
        if os.path.isdir(s):
            norm = os.path.normpath(s).rstrip("\\/ ")
            targets.append(norm)
        else:
            targets.append(s)
    return targets

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

    words = re.split(r"[\s\-_.]", filename_lower)
    initials = "".join(w[0] for w in words if w)
    if keyword in initials:
        return 50

    return 0

def _norm_path(p: str) -> str:
    """规范化路径，尽量保证和数据库 full_path 的格式一致"""
    p = os.path.normpath(p)
    # 去掉末尾反斜杠（根目录如 C:\ 不处理）
    if len(p) > 3 and p.endswith(os.sep):
        p = p.rstrip(os.sep)
    return p

def _dir_cache_file(drive_letter: str) -> str:
    """DIR_CACHE 持久化文件路径（按盘）"""
    base = Path(os.getenv("LOCALAPPDATA", ".")) / "SearchTool" / "dir_cache"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / f"dir_cache_{drive_letter.upper()}.bin")
