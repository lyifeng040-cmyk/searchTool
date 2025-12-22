"""
Shared constants extracted from legacy implementation.
"""

import platform
import re
from pathlib import Path

LOG_DIR = Path.home() / ".filesearch"
LOG_DIR.mkdir(exist_ok=True)

IS_WINDOWS = platform.system() == "Windows"

CAD_PATTERN = re.compile(r"cad20(1[0-9]|2[0-4])", re.IGNORECASE)
AUTOCAD_PATTERN = re.compile(r"autocad_20(1[0-9]|2[0-5])", re.IGNORECASE)

SKIP_DIRS_LOWER = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "$recycle.bin",
    "system volume information",
    "appdata",
    "boot",
    "node_modules",
    ".git",
    "__pycache__",
    "site-packages",
    "sys",
    "recovery",
    "config.msi",
    "$windows.~bt",
    "$windows.~ws",
    "cache",
    "caches",
    "temp",
    "tmp",
    "logs",
    "log",
    ".vscode",
    ".idea",
    ".vs",
    "obj",
    "bin",
    "debug",
    "release",
    "packages",
    ".nuget",
    "bower_components",
}

SKIP_EXTS = {
    ".lsp",
    ".fas",
    ".lnk",
    ".html",
    ".htm",
    ".xml",
    ".ini",
    ".lsp_bak",
    ".cuix",
    ".arx",
    ".crx",
    ".fx",
    ".dbx",
    ".kid",
    ".ico",
    ".rz",
    ".dll",
    ".sys",
    ".tmp",
    ".log",
    ".dat",
    ".db",
    ".pdb",
    ".obj",
    ".pyc",
    ".class",
    ".cache",
    ".lock",
}

ARCHIVE_EXTS = {
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".iso",
    ".jar",
    ".cab",
    ".bz2",
    ".xz",
}

__all__ = [
    "LOG_DIR",
    "IS_WINDOWS",
    "CAD_PATTERN",
    "AUTOCAD_PATTERN",
    "SKIP_DIRS_LOWER",
    "SKIP_EXTS",
    "ARCHIVE_EXTS",
]
