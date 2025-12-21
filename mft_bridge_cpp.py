# mft_bridge_cpp.py
"""
桥接 C++ MFT 扫描结果，用于喂给原来的 Python MFT 过滤逻辑。
"""
import ctypes
import os
import logging

logger = logging.getLogger(__name__)


# ===== DLL 中的 FileRecord 结构，与 scanner_api.h 一致 =====
class CppFileRecord(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("ref", ctypes.c_uint64),
        ("parent_ref", ctypes.c_uint64),
        ("filename", ctypes.c_wchar * 260),
        ("attributes", ctypes.c_uint32),
    ]


_DLL = None


def _load_dll(dll_path=None):
    """加载 C++ 扫描 DLL"""
    if dll_path is None:
        # TODO: 按你的实际 DLL 路径修改
        dll_path = r"G:\新建文件夹\Scanner\x64\Release\Scanner.dll"
        # 或者：
        # dll_path = os.path.join(os.path.dirname(__file__), "Scanner.dll")

    if not os.path.exists(dll_path):
        raise FileNotFoundError(f"找不到 DLL: {dll_path}")

    dll = ctypes.CDLL(dll_path)

    # mft_clear
    dll.mft_clear.restype = None

    # mft_scan_fast
    dll.mft_scan_fast.argtypes = [ctypes.c_wchar_p]
    dll.mft_scan_fast.restype = ctypes.c_int64

    # mft_get_scan_time
    dll.mft_get_scan_time.restype = ctypes.c_double

    # mft_get_count
    dll.mft_get_count.restype = ctypes.c_int64

    # mft_get_records
    dll.mft_get_records.argtypes = [
        ctypes.POINTER(CppFileRecord),
        ctypes.c_int64,
        ctypes.c_int32
    ]
    dll.mft_get_records.restype = ctypes.c_int32

    logger.info(f"[MFT_CPP] 已加载 DLL: {dll_path}")
    return dll


def get_dll():
    """单例获取 DLL 句柄"""
    global _DLL
    if _DLL is None:
        _DLL = _load_dll()
    return _DLL


def mft_read_records_cpp(drive_letter: str):
    """
    用 C++ DLL 读取指定盘符的 MFT 记录，
    返回：records = { ref: (name, parent_ref, is_dir) }
    这里只做“快速读取”，不做复杂过滤。
    """
    dll = get_dll()
    dll.mft_clear()

    if not drive_letter:
        return {}

    drv = drive_letter[0].upper()
    count = dll.mft_scan_fast(drv)
    if count <= 0:
        logger.error(f"[MFT_CPP] {drv}: mft_scan_fast 返回 {count}")
        return {}

    total = dll.mft_get_count()
    logger.info(f"[MFT_CPP] {drv}: C++ 扫描记录总数 {total:,} 条, 用时 {dll.mft_get_scan_time():.2f} ms")

    records = {}
    offset = 0
    batch_size = 50000

    buf = (CppFileRecord * batch_size)()

    while offset < total:
        n = min(batch_size, total - offset)
        got = dll.mft_get_records(buf, offset, n)
        if got <= 0:
            break

        for i in range(got):
            rec = buf[i]
            name = rec.filename
            if not name or name[0] in ('$', '.'):
                continue

            parent = rec.parent_ref & 0x0000FFFFFFFFFFFF
            is_dir = bool(rec.attributes & 0x10)
            ref = rec.ref & 0x0000FFFFFFFFFFFF

            # 与你原来 enum_volume_files_mft 的 records 结构一致
            records[ref] = (name, parent, is_dir)

        offset += got

    logger.info(f"[MFT_CPP] {drv}: 读取记录并转换完成 {len(records):,} 条")
    return records