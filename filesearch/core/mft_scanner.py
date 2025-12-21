"""
MFT / USN scanning utilities (extracted from legacy implementation)
"""

import os
import ctypes
import struct
import time
import concurrent.futures
from collections import deque
from pathlib import Path
import logging

from ..constants import (
    IS_WINDOWS,
    SKIP_DIRS_LOWER,
    SKIP_EXTS,
)
from ..utils import should_skip_path, should_skip_dir, is_in_allowed_paths
from .rust_engine import HAS_RUST_ENGINE, RUST_ENGINE, FileInfo, ScanResult
from .dependencies import HAS_APSW

logger = logging.getLogger(__name__)

# Shared flag with legacy
MFT_AVAILABLE = False

if IS_WINDOWS:
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
            ("UsnJournalID", ctypes.c_uint64),
            ("FirstUsn", ctypes.c_int64),
            ("NextUsn", ctypes.c_int64),
            ("LowestValidUsn", ctypes.c_int64),
            ("MaxUsn", ctypes.c_int64),
            ("MaximumSize", ctypes.c_uint64),
            ("AllocationDelta", ctypes.c_uint64),
        ]

    class USN_RECORD_V2(ctypes.Structure):
        _fields_ = [
            ("RecordLength", ctypes.c_uint32),
            ("MajorVersion", ctypes.c_uint16),
            ("MinorVersion", ctypes.c_uint16),
            ("FileReferenceNumber", ctypes.c_uint64),
            ("ParentFileReferenceNumber", ctypes.c_uint64),
            ("Usn", ctypes.c_int64),
            ("TimeStamp", ctypes.c_int64),
            ("Reason", ctypes.c_uint32),
            ("SourceInfo", ctypes.c_uint32),
            ("SecurityId", ctypes.c_uint32),
            ("FileAttributes", ctypes.c_uint32),
            ("FileNameLength", ctypes.c_uint16),
            ("FileNameOffset", ctypes.c_uint16),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    CreateFileW = kernel32.CreateFileW
    CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    CreateFileW.restype = wintypes.HANDLE

    DeviceIoControl = kernel32.DeviceIoControl
    DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    DeviceIoControl.restype = wintypes.BOOL

    CloseHandle = kernel32.CloseHandle
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def enum_volume_files_mft(drive_letter, skip_dirs, skip_exts, allowed_paths=None):
    """MFT 枚举文件（含 Rust 快速路径）"""
    global MFT_AVAILABLE

    if IS_WINDOWS and HAS_RUST_ENGINE:
        logger.info(f"🚀 使用 Rust 核心引擎扫描驱动器 {drive_letter}...")
        result = None
        try:
            result = RUST_ENGINE.scan_drive_packed(ord(drive_letter.upper()[0]))

            if not result.data or result.count == 0:
                raise Exception("空数据")

            raw_data = ctypes.string_at(result.data, result.data_len)
            py_list = []
            off = 0
            n = len(raw_data)

            allowed_paths_lower = [p.lower().rstrip("\\") for p in allowed_paths] if allowed_paths else None
            skipped_count = 0

            while off < n:
                if off + 24 > n:
                    break

                is_dir = raw_data[off]
                name_len = int.from_bytes(raw_data[off + 1:off + 3], "little")
                path_len = int.from_bytes(raw_data[off + 3:off + 5], "little")
                parent_len = int.from_bytes(raw_data[off + 5:off + 7], "little")
                ext_len = raw_data[off + 7]
                size = int.from_bytes(raw_data[off + 8:off + 16], "little")
                mtime = struct.unpack("<d", raw_data[off + 16:off + 24])[0]
                off += 24

                total_len = name_len + path_len + parent_len + ext_len
                if off + total_len > n:
                    break

                name = raw_data[off:off + name_len].decode("utf-8", "replace")
                off += name_len

                path = raw_data[off:off + path_len].decode("utf-8", "replace")
                off += path_len

                parent = raw_data[off:off + parent_len].decode("utf-8", "replace")
                off += parent_len

                ext = raw_data[off:off + ext_len].decode("utf-8", "replace") if ext_len else ""
                off += ext_len

                name_lower = name.lower()
                path_lower = path.lower()

                if allowed_paths_lower:
                    in_allowed = any(path_lower.startswith(ap + "\\") or path_lower == ap for ap in allowed_paths_lower)
                    if not in_allowed:
                        skipped_count += 1
                        continue
                else:
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

                py_list.append((name, name_lower, path, parent, ext, size, mtime, is_dir))

            logger.info(f"✅ Rust返回={result.count}, 跳过={skipped_count}, 保留={len(py_list)}")

            MFT_AVAILABLE = True
            return py_list

        except Exception as e:
            logger.error(f"Rust 引擎错误: {e}，回退到 Python")
        finally:
            if result and result.data:
                try:
                    RUST_ENGINE.free_scan_result(result)
                except Exception:
                    pass

    if not IS_WINDOWS:
        raise OSError("MFT仅Windows可用")

    return _enum_volume_files_mft_python(drive_letter, skip_dirs, skip_exts, allowed_paths)


def _batch_stat_files(py_list, only_missing=True, write_back_db=False, db_conn=None, db_lock=None):
    """批量获取文件大小和修改时间（增强版）"""
    if not IS_WINDOWS:
        return
    if not py_list:
        return

    files_to_stat = []
    for item in py_list:
        try:
            if item[7] != 0:
                continue
            if only_missing and (item[5] != 0 or item[6] != 0):
                continue
            files_to_stat.append(item)
        except Exception:
            continue

    if not files_to_stat:
        return

    import ctypes.wintypes as wintypes  # local import to keep parity

    total_files = len(files_to_stat)
    start_time = time.time()

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

    EPOCH_DIFF = 116444736000000000

    def stat_worker(batch):
        data = WIN32_FILE_ATTRIBUTE_DATA()
        updates = []
        for item in batch:
            try:
                path = item[2]
                if GetFileAttributesExW(path, 0, ctypes.byref(data)):
                    size = (data.nFileSizeHigh << 32) + data.nFileSizeLow
                    mtime_ft = (data.ftLastWriteTime.dwHighDateTime << 32) + data.ftLastWriteTime.dwLowDateTime
                    if mtime_ft > EPOCH_DIFF:
                        mtime = (mtime_ft - EPOCH_DIFF) / 10000000.0
                    else:
                        mtime = 0.0
                    item[5] = int(size)
                    item[6] = float(mtime)
                    if write_back_db:
                        updates.append((int(size), float(mtime), path))
            except Exception:
                pass
        return updates

    if total_files < 200:
        num_workers = 4
    elif total_files < 2000:
        num_workers = 8
    else:
        num_workers = 16

    batch_size = max(50, (total_files + num_workers - 1) // num_workers)
    batches = [files_to_stat[i:i + batch_size] for i in range(0, total_files, batch_size)]

    all_updates = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as ex:
        for ups in ex.map(stat_worker, batches):
            if ups:
                all_updates.extend(ups)

    if write_back_db and all_updates and db_conn is not None:
        try:
            if db_lock is not None:
                with db_lock:
                    cur = db_conn.cursor()
                    cur.executemany(
                        "UPDATE files SET size=?, mtime=? WHERE full_path=?",
                        all_updates,
                    )
                    if not HAS_APSW:
                        db_conn.commit()
            else:
                cur = db_conn.cursor()
                cur.executemany(
                    "UPDATE files SET size=?, mtime=? WHERE full_path=?",
                    all_updates,
                )
                if not HAS_APSW:
                    db_conn.commit()
        except Exception as e:
            logger.debug(f"[stat回写] 写回数据库失败: {e}")

    elapsed = time.time() - start_time
    speed = total_files / elapsed if elapsed > 0 else 0
    logger.info(f"补齐完成: {total_files} 个文件, 耗时 {elapsed:.2f}s, 速度 {speed:.0f}/s")


def _enum_volume_files_mft_python(drive_letter, skip_dirs, skip_exts, allowed_paths=None):
    """Python MFT 实现"""
    global MFT_AVAILABLE
    if not IS_WINDOWS:
        raise OSError("MFT仅Windows可用")

    logger.info(f"使用 Python MFT 实现扫描驱动器 {drive_letter}...")
    drive = drive_letter.rstrip(":").upper()
    root_path = f"{drive}:\\"

    volume_path = f"\\\\.\\{drive}:"
    h = CreateFileW(
        volume_path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if h == INVALID_HANDLE_VALUE:
        error_code = ctypes.get_last_error()
        logger.error(f"打开卷失败 {drive}: 错误代码 {error_code}")
        raise OSError(f"打开卷失败: {error_code}")

    try:
        jd = USN_JOURNAL_DATA_V0()
        br = wintypes.DWORD()
        if not DeviceIoControl(
            h,
            FSCTL_QUERY_USN_JOURNAL,
            None,
            0,
            ctypes.byref(jd),
            ctypes.sizeof(jd),
            ctypes.byref(br),
            None,
        ):
            error_code = ctypes.get_last_error()
            logger.error(f"查询USN失败 {drive}: 错误代码 {error_code}")
            raise OSError(f"查询USN失败: {error_code}")

        MFT_AVAILABLE = True
        records = {}
        BUFFER_SIZE = 1024 * 1024
        buf = (ctypes.c_ubyte * BUFFER_SIZE)()

        class MFT_ENUM_DATA(ctypes.Structure):
            _pack_ = 8
            _fields_ = [
                ("StartFileReferenceNumber", ctypes.c_uint64),
                ("LowUsn", ctypes.c_int64),
                ("HighUsn", ctypes.c_int64),
            ]

        med = MFT_ENUM_DATA()
        med.StartFileReferenceNumber = 0
        med.LowUsn = 0
        med.HighUsn = jd.NextUsn

        allowed_paths_lower = (
            [p.lower().rstrip("\\") for p in allowed_paths]
            if allowed_paths
            else None
        )

        total = 0
        start_time = time.time()

        while True:
            ctypes.set_last_error(0)
            ok = DeviceIoControl(
                h,
                FSCTL_ENUM_USN_DATA,
                ctypes.byref(med),
                ctypes.sizeof(med),
                ctypes.byref(buf),
                BUFFER_SIZE,
                ctypes.byref(br),
                None,
            )
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

            next_frn = ctypes.cast(
                ctypes.byref(buf), ctypes.POINTER(ctypes.c_uint64)
            )[0]
            offset = 8
            batch_count = 0

            while offset < returned:
                if offset + 4 > returned:
                    break
                rec_len = ctypes.cast(
                    ctypes.byref(buf, offset), ctypes.POINTER(ctypes.c_uint32)
                )[0]
                if rec_len == 0 or offset + rec_len > returned:
                    break

                if rec_len >= ctypes.sizeof(USN_RECORD_V2):
                    rec = ctypes.cast(
                        ctypes.byref(buf, offset), ctypes.POINTER(USN_RECORD_V2)
                    ).contents
                    name_off, name_len = rec.FileNameOffset, rec.FileNameLength
                    if name_len > 0 and offset + name_off + name_len <= returned:
                        filename = bytes(
                            buf[offset + name_off : offset + name_off + name_len]
                        ).decode("utf-16le", errors="replace")
                        if filename and filename[0] not in ("$", "."):
                            file_ref = rec.FileReferenceNumber & 0x0000FFFFFFFFFFFF
                            parent_ref = (
                                rec.ParentFileReferenceNumber & 0x0000FFFFFFFFFFFF
                            )
                            is_dir = bool(
                                rec.FileAttributes & FILE_ATTRIBUTE_DIRECTORY
                            )
                            records[file_ref] = (filename, parent_ref, is_dir)
                            batch_count += 1
                offset += rec_len

            total += batch_count
            if total and total % 100000 < batch_count:
                logger.info(
                    f"[MFT] {drive}: 已枚举 {total:,} 条, 用时 {time.time()-start_time:.1f}s"
                )

            med.StartFileReferenceNumber = next_frn
            if batch_count == 0:
                break

        logger.info(f"[MFT] {drive}: 枚举完成 {len(records):,} 条")

        result = _build_paths_from_records(
            records, root_path, drive, skip_exts, allowed_paths_lower
        )

        return result
    finally:
        CloseHandle(h)


def _build_paths_from_records(records, root_path, drive, skip_exts, allowed_paths_lower):
    """从MFT记录构建完整路径"""
    logger.info(f"[MFT] {drive}: 开始构建路径...")

    dirs = {}
    files = {}
    parent_to_children = {}

    for ref, (name, parent_ref, is_dir) in records.items():
        if is_dir:
            dirs[ref] = (name, parent_ref)
            parent_to_children.setdefault(parent_ref, []).append(ref)
        else:
            files[ref] = (name, parent_ref)

    path_cache = {5: root_path}
    q = deque([5])

    while q:
        parent_ref = q.popleft()
        parent_path = path_cache.get(parent_ref)
        if not parent_path:
            continue

        parent_path_lower = parent_path.lower()
        if should_skip_path(parent_path_lower, allowed_paths_lower) or should_skip_dir(
            os.path.basename(parent_path_lower), parent_path_lower, allowed_paths_lower
        ):
            continue

        for child_ref in parent_to_children.get(parent_ref, []):
            child_name, _ = dirs[child_ref]
            child_path = os.path.join(parent_path, child_name)
            path_cache[child_ref] = child_path
            q.append(child_ref)

    logger.info(
        f"[MFT] {drive}: 目录路径构建完成，缓存了 {len(path_cache):,} 个有效目录。"
    )

    result = []

    for ref, (name, parent_ref) in dirs.items():
        full_path = path_cache.get(ref)
        if not full_path or full_path == root_path:
            continue
        parent_dir = path_cache.get(parent_ref, root_path)
        result.append([name, name.lower(), full_path, parent_dir, "", 0, 0, 1])

    for ref, (name, parent_ref) in files.items():
        parent_path = path_cache.get(parent_ref)
        if not parent_path:
            continue

        full_path = os.path.join(parent_path, name)

        if should_skip_path(full_path.lower(), allowed_paths_lower):
            continue

        ext = os.path.splitext(name)[1].lower()
        if ext in skip_exts:
            continue

        if allowed_paths_lower and not is_in_allowed_paths(
            full_path.lower(), allowed_paths_lower
        ):
            continue

        result.append([name, name.lower(), full_path, parent_path, ext, 0, 0, 0])

    logger.info(f"[MFT] {drive}: 路径拼接与过滤完成，总计 {len(result):,} 条。")

    _batch_stat_files(result)

    logger.info(f"[MFT] {drive}: 过滤后 {len(result):,} 条")
    return [tuple(item) for item in result]

__all__ = [
    "enum_volume_files_mft",
    "_batch_stat_files",
    "_enum_volume_files_mft_python",
    "_build_paths_from_records",
    "MFT_AVAILABLE",
]
